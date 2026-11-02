"""Pure deterministic L05 admission; all commercial observations are supplied."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from hashlib import sha256

from jsonschema import Draft202012Validator

from ai_commerce.affiliate_attribution import AffiliateAttributionEvidence, AttributionError
from ai_commerce.affiliate_attribution.evidence import (
    _FORMATS, _time, canonical_bytes as _canonical, parse_json as _parse,
)
from ai_commerce.affiliate_commerce.service import request_value, CommerceError
from .schemas import KINDS, settlement_source_schema, settlement_evidence_schema

MAX_SOURCE_BYTES = 262144
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
_SOURCE = Draft202012Validator(settlement_source_schema(), format_checker=_FORMATS)
_EVIDENCE = Draft202012Validator(settlement_evidence_schema(), format_checker=_FORMATS)


class SettlementError(ValueError):
    """Bounded codes only; no raw identifiers, money, credentials or PII."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(ok, code):
    if not ok:
        raise SettlementError(code)


def canonical_bytes(value):
    try:
        return _canonical(value)
    except AttributionError as exc:
        raise SettlementError("INVALID_JSON_VALUE") from exc


def parse_json(raw, limit=MAX_EVIDENCE_BYTES):
    try:
        return _parse(raw, limit)
    except AttributionError as exc:
        raise SettlementError(exc.code) from exc


def digest(value):
    return sha256(canonical_bytes(value)).hexdigest()


def validate(validator, value, code):
    try:
        require(validator.is_valid(value), code)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        if isinstance(exc, SettlementError):
            raise
        raise SettlementError(code) from exc


def blob(raw):
    return {"raw_utf8": raw.decode("utf-8"), "raw_sha256": sha256(raw).hexdigest()}


def open_blob(envelope, limit):
    require(type(envelope) is dict and set(envelope) == {"raw_utf8", "raw_sha256"}
            and type(envelope["raw_utf8"]) is str, "INVALID_SOURCE_ENVELOPE")
    try:
        raw = envelope["raw_utf8"].encode("utf-8")
    except UnicodeError as exc:
        raise SettlementError("INVALID_JSON") from exc
    require(0 < len(raw) <= limit, "INPUT_SIZE_OR_TYPE_INVALID")
    require(blob(raw) == envelope, "SOURCE_HASH_MISMATCH")
    return raw


def decimal_sum(amounts):
    # Receipt/order limits and decimal contracts keep exact sums within 64 digits.
    with localcontext() as context:
        context.prec = 64
        return format(sum((Decimal(value) for value in amounts), Decimal("0")), "f")


def commerce_scope(scope):
    return {key: value for key, value in scope.items() if key not in {"order_ref", "currency"}}


def active_receipts(source):
    """Receipt corrections replace one current leaf, preserving all prior bytes."""
    active, seen_ids, seen_hashes = {}, set(), set()
    scope = source["scope"]
    start, end = map(_time, scope["measurement_window"])
    for receipt in source["receipts"]:
        require(receipt["scope"] == scope, "RECEIPT_SCOPE_MISMATCH")
        require(receipt["source_kind"] == source["source_kind"], "RECEIPT_PROVENANCE_MISMATCH")
        observed = _time(receipt["occurred_at"])
        require(observed <= _time(source["recorded_at"]), "RECEIPT_FROM_FUTURE")
        if receipt["kind"] == "ORDER":
            require(start <= observed < end, "ORDER_OUTSIDE_WINDOW")
        require(receipt["receipt_ref"] not in seen_ids, "DUPLICATE_RECEIPT_ID")
        require(receipt["raw_sha256"] not in seen_hashes, "DUPLICATE_RECEIPT_CONTENT")
        parent_sha = receipt["correction_of"]
        if parent_sha is not None:
            require(parent_sha in active, "STALE_RECEIPT_CORRECTION")
            parent = active.pop(parent_sha)
            require(parent["kind"] == receipt["kind"], "RECEIPT_CORRECTION_KIND_MISMATCH")
            require(_time(parent["occurred_at"]) <= observed, "STALE_RECEIPT_TIME")
        active[receipt["raw_sha256"]] = receipt
        seen_ids.add(receipt["receipt_ref"])
        seen_hashes.add(receipt["raw_sha256"])
    require(sum(r["kind"] == "ORDER" for r in active.values()) <= 1, "DUPLICATE_ORDER_RECEIPT")
    orders = [r for r in active.values() if r["kind"] == "ORDER"]
    if orders:
        require(all(_time(r["occurred_at"]) >= _time(orders[0]["occurred_at"])
                    for r in active.values()), "RECEIPT_PRECEDES_ORDER")
    return list(active.values())


def _attribution(source, snapshot):
    pin = source["attribution"]
    require((pin is None) == (snapshot is None), "ATTRIBUTION_REFERENCE_REQUIRED")
    if pin is None:
        return "UNKNOWN"
    raw = open_blob(snapshot, 2 * 1024 * 1024)
    try:
        attr = AffiliateAttributionEvidence.from_bytes(raw, expected_id=pin["evidence_id"]).to_dict()
    except (AttributionError, TypeError, ValueError, KeyError) as exc:
        raise SettlementError("INVALID_ATTRIBUTION_EVIDENCE") from exc
    require(snapshot["raw_sha256"] == pin["raw_sha256"], "ATTRIBUTION_PIN_MISMATCH")
    scope = source["scope"]
    require(all(attr["scope"][key] == scope[key] for key in
                ("provider_ref", "platform", "account_ref", "market", "surface", "measurement_window", "timezone")),
            "ATTRIBUTION_SCOPE_MISMATCH")
    require(attr["scope"]["app_ref"] == (scope["app_ref"]["id"] if scope["app_ref"] else None),
            "ATTRIBUTION_SCOPE_MISMATCH")
    require(not attr["synthetic"] or source["source_kind"] == "SYNTHETIC_FIXTURE",
            "SYNTHETIC_PROVENANCE_DOWNGRADE")
    matches = [result for result in attr["results"] if result["event_ref"] == pin["event_ref"]]
    require(len(matches) == 1 and matches[0]["order_ref"] == scope["order_ref"], "ATTRIBUTION_ORDER_REQUIRED")
    result = matches[0]
    require(result["tier"] == pin["tier"], "ATTRIBUTION_TIER_MISMATCH")
    for key in ("link_ref", "creative_ref", "creative_sha256"):
        require(result[key] is None or result[key] == scope[key], "ATTRIBUTION_IDENTITY_MISMATCH")
    require(result["reported_count"] == {"status": "AVAILABLE", "value": 1}, "SINGLE_ORDER_ATTRIBUTION_REQUIRED")
    return result["tier"]


def _build(raw, snapshot, parent_id):
    source = parse_json(raw, MAX_SOURCE_BYTES)
    validate(_SOURCE, source, "INVALID_SETTLEMENT_SOURCE")
    scope = source["scope"]
    try:
        request_value({"schema_version": "AffiliateCommerceRequest.v1", "idempotency_key": source["idempotency_key"],
                       "as_of": source["recorded_at"], "scope": commerce_scope(scope)})
    except (CommerceError, ValueError, TypeError, KeyError) as exc:
        raise SettlementError("INVALID_SETTLEMENT_SCOPE") from exc
    tier = _attribution(source, snapshot)
    receipts = active_receipts(source)
    values = {}
    for kind, name in KINDS.items():
        field = source["amounts"][name]
        require(field["currency"] == scope["currency"], "CURRENCY_MISMATCH")
        observed = [r["amount"] for r in receipts if r["kind"] == kind]
        require(field["status"] == ("REPORTED" if observed else "UNKNOWN"), "AMOUNT_RECEIPT_MISMATCH")
        require(not observed or Decimal(field["amount"]) == Decimal(decimal_sum(observed)), "AMOUNT_RECEIPT_MISMATCH")
        values[name] = Decimal(field["amount"]) if observed else None
    gross, credit, settled, refund, reversed_, _ = (values[name] for name in KINDS.values())
    status = source["reported_status"]
    require((status != "UNKNOWN") == (gross is not None), "ORDER_RECEIPT_REQUIRED")
    if status == "UNKNOWN":
        require(not receipts and source["attribution"] is None, "UNKNOWN_WITH_COMMERCIAL_CLAIMS")
    if status == "ATTRIBUTED":
        require(tier in {"A", "B"}, "EXACT_ORDER_ATTRIBUTION_REQUIRED")
    if status in {"CREATED", "ATTRIBUTED"}:
        require(credit is None and settled is None and refund is None and reversed_ is None,
                "ORDER_STATE_COMMERCIAL_MISMATCH")
    if status in {"APPROVED", "PARTIALLY_SETTLED", "SETTLED"}:
        require(credit is not None and source["commission_basis"] != "UNKNOWN", "COMMISSION_EVIDENCE_REQUIRED")
    if credit is not None:
        require(source["commission_basis"] != "UNKNOWN", "COMMISSION_BASIS_REQUIRED")
    if reversed_ is not None:
        require(credit is not None and reversed_ <= credit, "REVERSAL_EXCEEDS_COMMISSION")
        require(status in {"REFUNDED", "REVOKED", "PARTIALLY_SETTLED", "SETTLED"}, "REVERSAL_STATUS_REQUIRED")
    if refund is not None:
        require(gross is not None and 0 < refund <= gross, "INVALID_REFUND_AMOUNT")
        require(status in {"REFUNDED", "REVOKED"}, "REFUND_STATUS_REQUIRED")
    if status == "REFUNDED":
        require(refund is not None, "REFUND_EVIDENCE_REQUIRED")
    if status == "REVOKED":
        require(reversed_ is not None, "REVERSAL_EVIDENCE_REQUIRED")
    if settled is not None:
        require(credit is not None and settled <= credit, "SETTLEMENT_EXCEEDS_COMMISSION")
        require(status in {"PARTIALLY_SETTLED", "SETTLED", "REFUNDED", "REVOKED"}, "SETTLEMENT_STATUS_REQUIRED")
    if status in {"PARTIALLY_SETTLED", "SETTLED"}:
        require(settled is not None, "SETTLEMENT_EVIDENCE_REQUIRED")
        with localcontext() as context:
            context.prec = 64
            due = credit - (reversed_ if reversed_ is not None else Decimal("0"))
        require((status == "SETTLED" and settled == due) or
                (status == "PARTIALLY_SETTLED" and 0 < settled < due), "SETTLEMENT_STATUS_AMOUNT_MISMATCH")
    payload = {"schema_version": "SettlementEvidence.v1", "evaluator_version": "a16-l05/1.0",
               "correction_of": parent_id,
               "source": {**blob(raw), "canonical_sha256": digest(source), "document": source},
               "attribution_snapshot": snapshot, "attribution_tier": tier,
               "source_status": "SUPPLIED_NOT_PROVIDER_ATTESTED", "actual_orders_verified": False,
               "settlement_verified": False, "realized_profit_verified": False, "network_calls_performed": False}
    record_sha = digest(payload)
    result = {**payload, "record_sha256": record_sha, "evidence_id": "a16-settlement-" + record_sha}
    validate(_EVIDENCE, result, "INVALID_SETTLEMENT_EVIDENCE")
    return result


def check_revision(parent, child):
    """Latest-parent admission is additionally enforced by the durable ledger."""
    before, after = (row["source"]["document"] for row in (parent, child))
    require(child["correction_of"] == parent["evidence_id"], "CORRECTION_PARENT_MISMATCH")
    require(before["scope"] == after["scope"], "CORRECTION_SCOPE_MISMATCH")
    require(before["source_kind"] == after["source_kind"], "CORRECTION_PROVENANCE_MISMATCH")
    require(before["commission_basis"] in {"UNKNOWN", after["commission_basis"]}, "CORRECTION_BASIS_MISMATCH")
    require(before["attribution"] is None or before["attribution"] == after["attribution"], "CORRECTION_ATTRIBUTION_MISMATCH")
    require(before["idempotency_key"] != after["idempotency_key"], "CORRECTION_REQUIRES_NEW_KEY")
    require(_time(before["recorded_at"]) < _time(after["recorded_at"]), "STALE_CORRECTION_TIME")
    require(after["receipts"][:len(before["receipts"])] == before["receipts"], "IMMUTABLE_RECEIPT_HISTORY_REQUIRED")
    require(before["reported_status"] != after["reported_status"] or before["receipts"] != after["receipts"] or
            before["attribution"] != after["attribution"], "EMPTY_CORRECTION")
    allowed = {
        "UNKNOWN": set(["UNKNOWN", "CREATED", "ATTRIBUTED", "APPROVED", "PENDING", "PARTIALLY_SETTLED", "SETTLED", "REFUNDED", "REVOKED"]),
        "CREATED": {"CREATED", "ATTRIBUTED", "APPROVED", "PENDING", "PARTIALLY_SETTLED", "SETTLED", "REFUNDED", "REVOKED"},
        "ATTRIBUTED": {"ATTRIBUTED", "APPROVED", "PENDING", "PARTIALLY_SETTLED", "SETTLED", "REFUNDED", "REVOKED"},
        "APPROVED": {"APPROVED", "PENDING", "PARTIALLY_SETTLED", "SETTLED", "REFUNDED", "REVOKED"},
        "PENDING": {"PENDING", "APPROVED", "PARTIALLY_SETTLED", "SETTLED", "REFUNDED", "REVOKED"},
        "PARTIALLY_SETTLED": {"PARTIALLY_SETTLED", "SETTLED", "REFUNDED", "REVOKED"},
        "SETTLED": {"SETTLED", "PARTIALLY_SETTLED", "REFUNDED", "REVOKED"},
        "REFUNDED": {"REFUNDED", "REVOKED"}, "REVOKED": {"REVOKED", "REFUNDED"},
    }
    require(after["reported_status"] in allowed[before["reported_status"]], "INVALID_ORDER_TRANSITION")
    if before["reported_status"] == "SETTLED" and after["reported_status"] == "PARTIALLY_SETTLED":
        require(any(r["kind"] == "SETTLEMENT" and r["correction_of"] is not None
                    for r in after["receipts"][len(before["receipts"]):]), "SETTLED_DOWNGRADE_REQUIRES_CORRECTION")


@dataclass(frozen=True, slots=True, init=False)
class SettlementEvidence:
    _raw: bytes

    @classmethod
    def from_bytes(cls, raw, *, expected_id=None):
        value = parse_json(raw)
        validate(_EVIDENCE, value, "INVALID_SETTLEMENT_EVIDENCE")
        replay = _build(value["source"]["raw_utf8"].encode("utf-8"), value["attribution_snapshot"], value["correction_of"])
        require(canonical_bytes(value) == canonical_bytes(replay), "SETTLEMENT_REPLAY_MISMATCH")
        require(expected_id is None or value["evidence_id"] == expected_id, "EVIDENCE_PIN_MISMATCH")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_raw", canonical_bytes(value))
        return instance

    def to_bytes(self):
        return self._raw

    def to_dict(self):
        return parse_json(self._raw)

    @property
    def evidence_id(self):
        return self.to_dict()["evidence_id"]


def admit_settlement(raw, *, attribution=None, correction_of=None,
                     expected_scope=None, expected_source_sha256=None):
    """Import an allowlisted business projection, never a raw provider/PII export."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_SOURCE_BYTES, "INPUT_SIZE_OR_TYPE_INVALID")
    require(expected_source_sha256 is None or sha256(raw).hexdigest() == expected_source_sha256, "SOURCE_PIN_MISMATCH")
    require(attribution is None or type(attribution) is AffiliateAttributionEvidence, "NATIVE_ATTRIBUTION_REQUIRED")
    require(correction_of is None or type(correction_of) is SettlementEvidence, "NATIVE_CORRECTION_PARENT_REQUIRED")
    parent = SettlementEvidence.from_bytes(correction_of.to_bytes()).to_dict() if correction_of is not None else None
    value = _build(raw, blob(attribution.to_bytes()) if attribution is not None else None,
                   parent["evidence_id"] if parent else None)
    require(expected_scope is None or value["source"]["document"]["scope"] == expected_scope, "EXPECTED_SCOPE_MISMATCH")
    if parent:
        check_revision(parent, value)
    return SettlementEvidence.from_bytes(canonical_bytes(value))
