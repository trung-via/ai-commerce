"""Read-only E05 projection of reopened A14, A15 and L05 native evidence."""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from jsonschema import Draft202012Validator

from ai_commerce.affiliate_attribution.evidence import _FORMATS
from ai_commerce.affiliate_commerce import AffiliateCommerceRun, CommerceError
from ai_commerce.operations.cost_ledger import CostEntry, CostLedger, CostEvidenceError
from ai_commerce.operations.errors import MediaOperationError
from .evidence import (SettlementError, blob, canonical_bytes, commerce_scope, decimal_sum,
                       digest, open_blob, parse_json, require, validate)
from .ledger import SettlementLedger, latest_records, validate_history, MAX_HISTORY_BYTES
from .schemas import KINDS, COST_SELECTION, MONEY, commercial_review_schema, array

MAX_REVIEW_BYTES = 80 * 1024 * 1024
_REVIEW = Draft202012Validator(commercial_review_schema(), format_checker=_FORMATS)
_SOURCES = Draft202012Validator(commercial_review_schema()["properties"]["sources"], format_checker=_FORMATS)
_SELECTIONS = Draft202012Validator(array(COST_SELECTION, 128))
_MONEY = Draft202012Validator(MONEY)
COST_CATEGORIES = ("estimated_cost", "supplied_charged_cost", "synthetic_charged_cost")


def sum_money(observations, *, currency):
    """Exact same-currency subtotal with explicit incompleteness, never implicit FX."""
    require(type(observations) in {list, tuple} and len(observations) <= 128, "PROJECTION_LIMIT")
    known, unknown = [], 0
    for observation in observations:
        validate(_MONEY, observation, "INVALID_MONEY_OBSERVATION")
        require(observation["currency"] == currency, "CROSS_CURRENCY_ADDITION")
        if observation["status"] == "REPORTED":
            known.append(observation["amount"])
        else:
            unknown += 1
    subtotal = decimal_sum(known) if known else None
    return {"status": "PARTIAL" if known and unknown else "REPORTED" if known else "UNKNOWN",
            "amount": subtotal if known and not unknown else None, "reported_subtotal": subtotal,
            "known_count": len(known), "unknown_count": unknown}


class _CostSnapshot:
    """Minimal read-only custody for native A15 replay of retained exact records."""
    def __init__(self, records):
        self.records = {CostLedger.ref(row["entry_id"]): canonical_bytes(row) for row in records}

    def read(self, reference):
        require(reference in self.records, "COST_RECORD_MISSING")
        return self.records[reference]


def _costs(records, selections):
    require(len(records) == len(selections), "COST_SELECTION_MISMATCH")
    ids = [row["entry_id"] for row in records]
    require(len(set(ids)) == len(ids), "DUPLICATE_COST_ENTRY")
    require([row["entry_id"] for row in selections] == ids, "COST_SELECTION_MISMATCH")
    receipt_hashes, receipt_ids, observations = set(), {}, set()
    entries = []
    try:
        for record, selection in zip(records, selections):
            entry = CostEntry.from_record(record)
            require(all(getattr(entry, key) == value for key, value in selection.items()), "COST_SOURCE_IDENTITY_MISMATCH")
            # A duplicate estimate is not independent cost; charge line items need
            # distinct source bytes and aliases, even if IDs/providers differ.
            identity = (entry.job_id, entry.attempt, entry.intent_digest, entry.provider,
                        entry.status, entry.evidence_ref, entry.evidence_sha256)
            require(identity not in observations, "DUPLICATE_COST_OBSERVATION")
            observations.add(identity)
            if entry.status == "CHARGED":
                require(entry.evidence_sha256 not in receipt_hashes, "DUPLICATE_COST_RECEIPT")
                address = (entry.provider, entry.evidence_ref)
                require(address not in receipt_ids, "COST_RECEIPT_IDENTITY_COLLISION")
                receipt_hashes.add(entry.evidence_sha256)
                receipt_ids[address] = entry.evidence_sha256
            entries.append(entry)
        # Use the upstream consumer, including its checksum and duplicate guards.
        # A15's zero-valued empty categories are not promoted to observed zero.
        with localcontext() as context:
            context.prec = 64
            CostLedger(_CostSnapshot(records)).summarize(ids)
    except (CostEvidenceError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, SettlementError):
            raise
        raise SettlementError("INVALID_COST_EVIDENCE") from exc
    return entries


def _derive(sources):
    validate(_SOURCES, sources, "INVALID_REVIEW_SOURCES")
    try:
        run_raw = open_blob(sources["commerce_run"], 20 * 1024 * 1024)
        run = AffiliateCommerceRun.from_bytes(run_raw).to_dict()
    except (CommerceError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, SettlementError):
            raise
        raise SettlementError("INVALID_COMMERCE_RUN") from exc
    records = validate_history(open_blob(sources["settlement_history"], MAX_HISTORY_BYTES))
    orders = latest_records(records)
    entries = _costs(sources["cost_records"], sources["cost_selections"])
    scope = run["request"]["scope"]
    reasons, hold = {"OFFLINE_UNATTESTED"}, run["status"] == "HOLD"
    if hold:
        reasons.add("UPSTREAM_HOLD")
    if not orders:
        reasons.add("SETTLEMENT_EVIDENCE_MISSING")
    if not entries:
        reasons.add("COST_EVIDENCE_MISSING")
    for evidence in orders:
        source = evidence["source"]["document"]
        require(commerce_scope(source["scope"]) == scope, "REVIEW_SCOPE_MISMATCH")
        if evidence["attribution_snapshot"] is not None:
            require(evidence["attribution_snapshot"] == run["evidence"]["attribution"], "COMMERCE_ATTRIBUTION_PIN_MISMATCH")
        if source["source_kind"] == "SYNTHETIC_FIXTURE":
            reasons.add("SYNTHETIC_EVIDENCE_PRESENT")
        if evidence["attribution_tier"] not in {"A", "B"}:
            reasons.add("ATTRIBUTION_AMBIGUOUS")
        if any(amount["status"] == "UNKNOWN" for amount in source["amounts"].values()):
            reasons.add("COMMERCIAL_EVIDENCE_INCOMPLETE")
        paid = source["amounts"]["settled_commission"]["amount"]
        # Keep historical settlement alongside later refund/revocation evidence.
        if paid is not None and Decimal(paid) > 0 and source["reported_status"] in {"REFUNDED", "REVOKED"}:
            reasons.add("REFUND_AFTER_SETTLEMENT" if source["reported_status"] == "REFUNDED" else "REVOCATION_AFTER_SETTLEMENT")
            hold = True
    currencies = {row["source"]["document"]["scope"]["currency"] for row in orders}
    currencies.update(entry.currency for entry in entries if entry.currency is not None)
    require(len(currencies) <= 128, "PROJECTION_LIMIT")
    by_currency = []
    for currency in sorted(currencies):
        bucket = {"currency": currency}
        currency_orders = [row["source"]["document"] for row in orders if row["source"]["document"]["scope"]["currency"] == currency]
        for name in KINDS.values():
            bucket[name] = sum_money([source["amounts"][name] for source in currency_orders], currency=currency)
        for category in COST_CATEGORIES:
            observations = []
            for entry in entries:
                if entry.currency not in {None, currency}:
                    continue
                if entry.status in {"UNKNOWN", "PENDING"}:
                    observations.append({"currency": currency, "status": "UNKNOWN", "amount": None})
                else:
                    actual_category = ("estimated_cost" if entry.status == "ESTIMATED" else
                                       "synthetic_charged_cost" if entry.evidence_origin == "SYNTHETIC_FIXTURE" else "supplied_charged_cost")
                    if entry.status in {"ESTIMATED", "CHARGED"} and category == actual_category:
                        observations.append({"currency": currency, "status": "REPORTED", "amount": entry.amount})
            bucket[category] = sum_money(observations, currency=currency)
        by_currency.append(bucket)
    if any(entry.evidence_origin == "SYNTHETIC_FIXTURE" for entry in entries):
        reasons.add("SYNTHETIC_EVIDENCE_PRESENT")
    if any(entry.currency is None and entry.status in {"PENDING", "UNKNOWN"} for entry in entries):
        reasons.add("UNKNOWN_COST_CURRENCY")
    if any(entry.status in {"UNKNOWN", "PENDING"} for entry in entries):
        reasons.add("COMMERCIAL_EVIDENCE_INCOMPLETE")
    counted = bool(orders) and all(row["source"]["document"]["reported_status"] != "UNKNOWN" for row in orders)
    payload = {"schema_version": "CommercialReview.v1", "evaluator_version": "a16-e05/1.0", "scope": scope,
               "sources": sources, "cost_allocation_basis": "USER_SUPPLIED_CREATIVE_ALLOCATION_NOT_PROVIDER_VERIFIED",
               "status": "HOLD" if hold else "PARTIAL", "reason_codes": sorted(reasons),
               "orders": [{"order_ref": row["source"]["document"]["scope"]["order_ref"], "evidence_id": row["evidence_id"],
                           "reported_status": row["source"]["document"]["reported_status"],
                           "currency": row["source"]["document"]["scope"]["currency"],
                           "source_kind": row["source"]["document"]["source_kind"], "attribution_tier": row["attribution_tier"]}
                          for row in orders],
               "supplied_order_count": {"status": "REPORTED" if counted else "UNKNOWN", "value": len(orders) if counted else None},
               "by_currency": by_currency, "actual_orders_verified": False, "actual_settlement_status": "UNKNOWN",
               "external_provider_charge_verified": False, "realized_profit": {"status": "UNKNOWN", "amount": None, "verified": False},
               "network_calls_performed": False, "advisory_only": True}
    record_sha = digest(payload)
    result = {**payload, "record_sha256": record_sha, "review_id": "a16-review-" + record_sha}
    validate(_REVIEW, result, "INVALID_COMMERCIAL_REVIEW")
    return result


@dataclass(frozen=True, slots=True, init=False)
class CommercialReview:
    _raw: bytes

    @classmethod
    def from_bytes(cls, raw, *, expected_id=None):
        value = parse_json(raw, MAX_REVIEW_BYTES)
        validate(_REVIEW, value, "INVALID_COMMERCIAL_REVIEW")
        replay = _derive(value["sources"])
        require(canonical_bytes(value) == canonical_bytes(replay), "COMMERCIAL_REVIEW_REPLAY_MISMATCH")
        require(expected_id is None or value["review_id"] == expected_id, "REVIEW_PIN_MISMATCH")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_raw", canonical_bytes(value))
        return instance

    def to_bytes(self):
        return self._raw

    def to_dict(self):
        return parse_json(self._raw, MAX_REVIEW_BYTES)

    @property
    def review_id(self):
        return self.to_dict()["review_id"]


def commercial_review(*, commerce_run, settlement_ledger, cost_ledger, cost_selections=()):
    """Read explicit local custody only; cost allocation is a supplied assertion.

    Each selection retains the actual A15 job/attempt/intent/provider identity.
    Allocating a job's cost to this creative does not verify a provider charge,
    attribution, settlement, or realized profit.
    """
    require(type(commerce_run) is AffiliateCommerceRun, "NATIVE_COMMERCE_RUN_REQUIRED")
    require(type(settlement_ledger) is SettlementLedger, "SETTLEMENT_LEDGER_REQUIRED")
    require(type(cost_ledger) is CostLedger, "COST_LEDGER_REQUIRED")
    require(type(cost_selections) in {tuple, list}, "INVALID_COST_SELECTIONS")
    selections = parse_json(canonical_bytes({"items": list(cost_selections)}))["items"]
    validate(_SELECTIONS, selections, "INVALID_COST_SELECTIONS")
    try:
        sources = {"commerce_run": blob(commerce_run.to_bytes()), "settlement_history": blob(settlement_ledger.to_bytes()),
                   "cost_records": [cost_ledger.get(row["entry_id"]).to_record() for row in selections],
                   "cost_selections": selections}
    except (CostEvidenceError, MediaOperationError, OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, SettlementError):
            raise
        raise SettlementError("COST_STORAGE_UNPROVEN") from exc
    return CommercialReview.from_bytes(canonical_bytes(_derive(sources)))
