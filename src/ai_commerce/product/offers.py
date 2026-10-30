"""Exact-variant, decimal-safe offer observations; no live/affiliate authority."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from .core import (canonical_bytes, content_id, parse_json, parse_timestamp, require,
                   validate_contract)
from .evidence import (bounded_records, common_body, raw_payload, validate_payload,
                       verify_source)
from .evidence_schemas import DECIMAL_PATTERN, MAX_FRESHNESS_SECONDS, OFFER_INPUT
from .reader import ProductListingSnapshot

_DECIMAL = re.compile(DECIMAL_PATTERN)


def decimal_amount(value: str | Decimal) -> str:
    """Normalize <=1e15 with at most 8 fractional digits, without float/context rounding."""
    if type(value) is Decimal:
        parts = value.as_tuple()
        require(value.is_finite() and not parts.sign and len(parts.digits) <= 24
                and type(parts.exponent) is int and -8 <= parts.exponent <= 15, "INVALID_DECIMAL_AMOUNT")
        value = format(value, "f")
    require(type(value) is str and len(value) <= 25 and _DECIMAL.fullmatch(value) is not None,
            "INVALID_DECIMAL_AMOUNT")
    require(Decimal(value) <= Decimal("1000000000000000"), "DECIMAL_AMOUNT_LIMIT")
    return value.rstrip("0").rstrip(".") if "." in value else value


def _presence(payload: dict, key: str) -> str:
    return "MISSING" if key not in payload else "NULL" if payload[key] is None else "VALUE"


def _optional_amount(payload: dict, key: str) -> str | None:
    return None if payload.get(key) is None else decimal_amount(payload[key])


def _project(payload: dict, captured_at: str) -> dict:
    validate_payload(payload, OFFER_INPUT, "INVALID_OFFER_SOURCE")
    observed = parse_timestamp(payload["observed_at"])
    require(observed <= parse_timestamp(captured_at), "OBSERVED_AFTER_CAPTURE")
    expires_at = payload.get("expires_at")
    if expires_at is not None:
        require(parse_timestamp(expires_at) > observed, "INVALID_OFFER_EXPIRY")
    amount = _optional_amount(payload, "price_amount")
    currency = payload.get("currency")
    require(amount is None or currency is not None, "PRICE_CURRENCY_REQUIRED")
    commission_amount = _optional_amount(payload, "commission_amount")
    commission_currency = payload.get("commission_currency")
    require(commission_amount is None or commission_currency is not None, "COMMISSION_CURRENCY_REQUIRED")
    commission_rate = _optional_amount(payload, "commission_rate")
    require(commission_rate is None or Decimal(commission_rate) <= Decimal("1"), "INVALID_COMMISSION_RATE")
    return {
        "observed_at": payload["observed_at"], "expires_at": expires_at,
        "provided_fields": sorted(payload),
        "price": {"amount": amount, "currency": currency,
                  "amount_presence": _presence(payload, "price_amount"),
                  "status": "UNKNOWN" if amount is None else "SOURCE_ASSERTED_NOT_LIVE"},
        "stock": {"available": payload.get("stock_available"),
                  "presence": _presence(payload, "stock_available")},
        "commission": {"rate": commission_rate, "rate_presence": _presence(payload, "commission_rate"),
                       "amount": commission_amount, "amount_presence": _presence(payload, "commission_amount"),
                       "currency": commission_currency, "status": "NOT_ELIGIBILITY_VERIFIED"},
        "live_price_status": "NOT_LIVE_VERIFIED",
    }


def _verify_offer(value: dict, *, raw: bytes | None, snapshot) -> None:
    payload = verify_source(value, raw, snapshot, exact_variant=True)
    expected = _project(payload, value["source"]["captured_at"])
    require(all(canonical_bytes(value[key]) == canonical_bytes(item) for key, item in expected.items()),
            "OFFER_REPLAY_MISMATCH")


@dataclass(frozen=True, slots=True, init=False)
class ProductOfferObservation:
    _encoded: bytes = field(repr=False)
    _raw_evidence: bytes = field(repr=False)
    _snapshot: ProductListingSnapshot = field(repr=False)

    def __init__(self, value: dict, *, raw: bytes, snapshot: ProductListingSnapshot):
        checked = validate_contract("ProductOfferObservation", value, raw=raw, snapshot=snapshot)
        object.__setattr__(self, "_encoded", canonical_bytes(checked))
        object.__setattr__(self, "_raw_evidence", raw)
        object.__setattr__(self, "_snapshot", snapshot)

    @classmethod
    def from_bytes(cls, encoded: bytes, *, raw: bytes,
                   snapshot: ProductListingSnapshot) -> ProductOfferObservation:
        return cls(parse_json(encoded), raw=raw, snapshot=snapshot)

    def to_dict(self) -> dict:
        return parse_json(self._encoded)

    def to_bytes(self) -> bytes:
        return self._encoded

    def raw_bytes(self) -> bytes:
        return self._raw_evidence

    @property
    def offer_id(self) -> str:
        return self.to_dict()["offer_id"]


def build_offer(snapshot: ProductListingSnapshot, *, ordinal: int, raw: bytes,
                source_ref: str, captured_at: str, source_kind: str = "HUMAN_SUPPLIED",
                pointer: str = "") -> ProductOfferObservation:
    """Read only local supplied bytes; a URL source_ref is inert metadata."""
    body = common_body("ProductOfferObservation", snapshot, ordinal, raw, source_ref=source_ref,
                       captured_at=captured_at, source_kind=source_kind, pointer=pointer,
                       exact_variant=True)
    body.update(_project(raw_payload(raw, pointer), captured_at))
    return ProductOfferObservation({**body, "offer_id": content_id("ProductOfferObservation", body)},
                                   raw=raw, snapshot=snapshot)


def _freshness(value: dict, as_of, max_age_seconds: int) -> tuple[str, list[str]]:
    observed = parse_timestamp(value["observed_at"])
    if as_of < observed:
        return "NOT_YET_OBSERVED", ["OBSERVED_AFTER_AS_OF"]
    reasons = []
    if value["expires_at"] is not None and as_of >= parse_timestamp(value["expires_at"]):
        reasons.append("EXPIRED")
    if as_of - observed > timedelta(seconds=max_age_seconds):
        reasons.append("MAX_AGE_EXCEEDED")
    return ("STALE", reasons) if reasons else ("RECENT_OBSERVATION", [])


def assess_offers(offers: list[ProductOfferObservation] | tuple[ProductOfferObservation, ...], *,
                  as_of: str, max_age_seconds: int) -> dict:
    """Assess overlap at an explicit instant; retain every source and stale record.

    Simultaneously recent, unexpired observations with incompatible known
    values conflict. No latest-wins selection, currency conversion or price
    imputation occurs. Recent still does not mean current or eligible.
    """
    instant = parse_timestamp(as_of)
    require(type(max_age_seconds) is int and 0 <= max_age_seconds <= MAX_FRESHNESS_SECONDS,
            "INVALID_FRESHNESS_LIMIT")
    groups = {}
    for item in bounded_records(offers, ProductOfferObservation):
        value = item.to_dict()
        subject = value["subject"]
        freshness, reasons = _freshness(value, instant, max_age_seconds)
        groups.setdefault((subject["listing_id"], subject["variant_id"]), []).append({
            "offer_id": value["offer_id"], "freshness": freshness, "reason_codes": reasons,
            "observation": value,
        })
    result = []
    for key, values in sorted(groups.items()):
        active = [item["observation"] for item in values if item["freshness"] == "RECENT_OBSERVATION"]
        fields = []
        # Unknowns do not contradict a known value; zero and False are known.
        prices = {(item["price"]["amount"], item["price"]["currency"])
                  for item in active if item["price"]["amount"] is not None}
        stocks = {item["stock"]["available"] for item in active if item["stock"]["available"] is not None}
        rates = {item["commission"]["rate"] for item in active if item["commission"]["rate"] is not None}
        amounts = {(item["commission"]["amount"], item["commission"]["currency"])
                   for item in active if item["commission"]["amount"] is not None}
        for field_name, known in (("PRICE", prices), ("STOCK", stocks),
                                  ("COMMISSION_RATE", rates), ("COMMISSION_AMOUNT", amounts)):
            if len(known) > 1:
                fields.append(field_name)
        result.append({"listing_id": key[0], "variant_id": key[1],
                       "status": "CONFLICT" if fields else "NO_CONFLICT_DETECTED" if active else "NO_RECENT_OBSERVATION",
                       "conflicting_fields": fields, "observations": values,
                       "affiliate_status": "NOT_ELIGIBILITY_VERIFIED", "live_price_status": "NOT_LIVE_VERIFIED"})
    return {"schema_version": "ProductOfferAssessment.v1", "as_of": as_of,
            "max_age_seconds": max_age_seconds, "inference": "ADVISORY_ONLY", "groups": result}
