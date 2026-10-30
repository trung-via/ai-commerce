"""A12-02 decimal-safe, variant-specific, time-bounded local offer evidence."""
from __future__ import annotations

import json
import socket
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.product import ProductError, content_id, prepare_bytes, schema, validate_contract
from ai_commerce.product.offers import ProductOfferObservation, assess_offers, build_offer, decimal_amount
from ai_commerce.product.schemas import MAX_RAW_BYTES

CAPTURE = "2026-10-30T05:00:00Z"
OBSERVED = "2026-10-30T04:00:00Z"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A12-02 offers must remain offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def snapshot(**changes):
    raw = json.dumps({"schema_version": "ManualProductListings.v1", "rows": [{
        "marketplace": "fixture", "seller_key": "seller", "listing_key": "listing",
        "variant_key": "red", "gtin": "claimed-shared", **changes}]}).encode()
    return prepare_bytes(raw, file_format="json", source_ref="listing-export", captured_at=CAPTURE, synthetic=True)


def offer_raw(**changes):
    return json.dumps({"schema_version": "ProductOfferSource.v1", "observed_at": OBSERVED,
                       **changes}, ensure_ascii=False).encode("utf-8")


def offer(*, source=None, raw=None, source_ref="offer-export", captured_at=CAPTURE, **changes):
    return build_offer(snapshot() if source is None else source, ordinal=1,
                       raw=offer_raw(**changes) if raw is None else raw, source_ref=source_ref,
                       captured_at=captured_at)


def assess(values, *, as_of=CAPTURE, max_age_seconds=7200):
    return assess_offers(values, as_of=as_of, max_age_seconds=max_age_seconds)


def reseal(value):
    value = deepcopy(value)
    value["offer_id"] = content_id("ProductOfferObservation", value)
    return value


def test_golden_zero_null_missing_and_stock_false_are_distinct():
    missing = offer()
    unknown = offer(price_amount=None, stock_available=None, commission_rate=None)
    zero = offer(price_amount="0.00", currency="VND", stock_available=False,
                 commission_rate="0", commission_amount="0.00000000", commission_currency="VND")
    m, u, z = (item.to_dict() for item in (missing, unknown, zero))
    assert zero.offer_id == "a12-offer-4d04225e14587a13a4204611b6ce671b439e2efcb491f4a96dbe31e3f96f28b3"
    assert m["price"] == {"amount": None, "amount_presence": "MISSING", "currency": None, "status": "UNKNOWN"}
    assert u["price"] == {"amount": None, "amount_presence": "NULL", "currency": None, "status": "UNKNOWN"}
    assert z["price"] == {"amount": "0", "amount_presence": "VALUE", "currency": "VND", "status": "SOURCE_ASSERTED_NOT_LIVE"}
    assert z["stock"] == {"available": False, "presence": "VALUE"}
    assert u["stock"] == {"available": None, "presence": "NULL"}
    assert m["stock"] == {"available": None, "presence": "MISSING"}
    assert z["commission"] == {"rate": "0", "rate_presence": "VALUE", "amount": "0",
                                "amount_presence": "VALUE", "currency": "VND", "status": "NOT_ELIGIBILITY_VERIFIED"}
    assert len({missing.offer_id, unknown.offer_id, zero.offer_id}) == 3
    assert "price_amount" not in m["provided_fields"]
    assert "price_amount" in u["provided_fields"]
    assert all(item["live_price_status"] == "NOT_LIVE_VERIFIED" for item in (m, u, z))
    group = assess([missing, unknown, zero])["groups"][0]
    assert group["status"] == "NO_CONFLICT_DETECTED"
    assert group["affiliate_status"] == "NOT_ELIGIBILITY_VERIFIED"


def test_exact_raw_replay_bom_crlf_and_immutable_copy():
    source = snapshot()
    raw = b"\xef\xbb\xbf" + offer_raw(price_amount="12.3400", currency="USD") + b"\r\n"
    value = offer(raw=raw, source=source, source_ref="https://invalid.test/do-not-fetch")
    assert value.raw_bytes() == raw
    assert value.to_dict()["source"]["raw_sha256"] == sha256(raw).hexdigest()
    assert value.to_dict()["price"]["amount"] == "12.34"
    assert value == offer(raw=raw, source=source, source_ref="https://invalid.test/do-not-fetch")
    assert ProductOfferObservation.from_bytes(value.to_bytes(), raw=raw, snapshot=source) == value
    detached = value.to_dict()
    detached["price"]["amount"] = "999"
    assert value.to_dict()["price"]["amount"] == "12.34"
    with pytest.raises(FrozenInstanceError):
        value._raw_evidence = b"changed"


@pytest.mark.parametrize("value,expected", [
    ("0", "0"), ("0.00000000", "0"), ("0.10000000", "0.1"),
    ("123456789012345.12345678", "123456789012345.12345678"),
    ("1000000000000000.00000000", "1000000000000000"),
    (Decimal("12.34000000"), "12.34"), (Decimal("1e-8"), "0.00000001"),
])
def test_strict_decimal_exactness_ignores_process_decimal_precision(value, expected):
    with localcontext() as context:
        context.prec = 2
        assert decimal_amount(value) == expected
    observation = offer(price_amount=expected, currency="USD")
    assert observation.to_dict()["price"]["amount"] == expected


@pytest.mark.parametrize("value", [
    0, 1, 0.1, True, False, None, "", " 1", "1 ", "+1", "-1", "-0", "01", "00.1",
    ".1", "1.", "1e2", "1E-2", "NaN", "Infinity", "-Infinity", "1,000", "١٢", "1\n",
    "0.000000001", "1000000000000001", "1000000000000000.00000001",
    Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-0"),
    Decimal("1e999999"), Decimal("1e-999999"),
])
def test_ambiguous_nonfinite_binary_or_oversized_amounts_rejected(value):
    with pytest.raises(ProductError, match="INVALID_DECIMAL_AMOUNT|DECIMAL_AMOUNT_LIMIT"):
        decimal_amount(value)


@pytest.mark.parametrize("changes,code", [
    ({"price_amount": "0"}, "PRICE_CURRENCY_REQUIRED"),
    ({"price_amount": "0", "currency": None}, "PRICE_CURRENCY_REQUIRED"),
    ({"price_amount": 0, "currency": "USD"}, "INVALID_OFFER_SOURCE"),
    ({"price_amount": 0.1, "currency": "USD"}, "INVALID_OFFER_SOURCE"),
    ({"price_amount": True, "currency": "USD"}, "INVALID_OFFER_SOURCE"),
    ({"price_amount": "1.0", "currency": "usd"}, "INVALID_OFFER_SOURCE"),
    ({"price_amount": "0", "currency": "USD\n"}, "INVALID_OFFER_SOURCE"),
    ({"commission_amount": "0", "commission_currency": "USD\n"}, "INVALID_OFFER_SOURCE"),
    ({"commission_amount": "0"}, "COMMISSION_CURRENCY_REQUIRED"),
    ({"commission_rate": "1.00000001"}, "INVALID_COMMISSION_RATE"),
    ({"commission_rate": "10"}, "INVALID_COMMISSION_RATE"),
    ({"commission_rate": "0.1", "eligible": True}, "INVALID_OFFER_SOURCE"),
    ({"affiliate_status": "VERIFIED"}, "INVALID_OFFER_SOURCE"),
    ({"stock_available": 0}, "INVALID_OFFER_SOURCE"),
    ({"stock_available": "in_stock"}, "INVALID_OFFER_SOURCE"),
    ({"observed_at": None}, "INVALID_OFFER_SOURCE"),
    ({"observed_at": "2026-10-30T05:00:00.000001Z"}, "OBSERVED_AFTER_CAPTURE"),
    ({"observed_at": "2026-02-30T00:00:00Z"}, "INVALID_TIMESTAMP"),
    ({"observed_at": "0001-01-01T00:00:00+23:00"}, "INVALID_TIMESTAMP"),
    ({"observed_at": "2026-10-30T04:00:00-00:00"}, "INVALID_OFFER_SOURCE"),
    ({"expires_at": "2026-10-30T04:00:00Z"}, "INVALID_OFFER_EXPIRY"),
    ({"expires_at": "2026-10-30T03:59:59Z"}, "INVALID_OFFER_EXPIRY"),
    ({"expires_at": "2026-02-30T00:00:00Z"}, "INVALID_TIMESTAMP"),
])
def test_invalid_offer_evidence(changes, code):
    with pytest.raises(ProductError, match=code):
        offer(**changes)


def test_offer_requires_an_explicit_exact_variant_and_keeps_merchant_separation():
    with pytest.raises(ProductError, match="EXACT_VARIANT_REQUIRED"):
        offer(source=snapshot(variant_key=None))
    red = offer(price_amount="1", currency="USD")
    blue = offer(source=snapshot(variant_key="blue"), price_amount="2", currency="USD")
    other_seller = offer(source=snapshot(seller_key="another"), price_amount="3", currency="USD")
    other_market = offer(source=snapshot(marketplace="elsewhere"), price_amount="4", currency="USD")
    groups = assess([red, blue, other_seller, other_market])["groups"]
    assert len(groups) == 4
    assert all(item["status"] == "NO_CONFLICT_DETECTED" for item in groups)
    for value in (red, blue, other_seller, other_market):
        assert value.to_dict()["subject"]["variant_key"] in ("red", "blue")


@pytest.mark.parametrize("as_of,age,status,reasons", [
    ("2026-10-30T03:59:59Z", 3600, "NOT_YET_OBSERVED", ["OBSERVED_AFTER_AS_OF"]),
    ("2026-10-30T04:00:00Z", 0, "RECENT_OBSERVATION", []),
    ("2026-10-30T05:00:00Z", 3600, "RECENT_OBSERVATION", []),
    ("2026-10-30T05:00:00.000001Z", 3600, "STALE", ["MAX_AGE_EXCEEDED"]),
    ("2026-10-30T06:00:00Z", 10000, "STALE", ["EXPIRED"]),
    ("2026-10-30T07:00:00Z", 3600, "STALE", ["EXPIRED", "MAX_AGE_EXCEEDED"]),
    ("2026-10-30T12:00:00+07:00", 3600, "RECENT_OBSERVATION", []),
])
def test_offer_freshness_expiry_and_age_boundaries(as_of, age, status, reasons):
    value = offer(price_amount="10", currency="USD", expires_at="2026-10-30T06:00:00Z")
    group = assess([value], as_of=as_of, max_age_seconds=age)["groups"][0]
    observation = group["observations"][0]
    assert observation["freshness"] == status
    assert observation["reason_codes"] == reasons
    assert group["affiliate_status"] == "NOT_ELIGIBILITY_VERIFIED"
    assert group["live_price_status"] == "NOT_LIVE_VERIFIED"
    if status != "RECENT_OBSERVATION":
        assert group["status"] == "NO_RECENT_OBSERVATION"


def test_conflicting_overlapping_offers_keep_sources_without_latest_wins():
    a = offer(source_ref="source-a", price_amount="0", currency="USD", stock_available=False,
              commission_rate="0", commission_amount="0", commission_currency="USD")
    b = offer(source_ref="source-b", price_amount="2", currency="USD", stock_available=True,
              commission_rate="0.1", commission_amount="0.2", commission_currency="USD")
    group = assess([a, b, a])["groups"][0]
    assert group["status"] == "CONFLICT"
    assert group["conflicting_fields"] == ["PRICE", "STOCK", "COMMISSION_RATE", "COMMISSION_AMOUNT"]
    assert len(group["observations"]) == 2
    assert {item["observation"]["source"]["source_ref"] for item in group["observations"]} == {"source-a", "source-b"}
    assert assess([a, b]) == assess([b, a])
    currency_conflict = offer(price_amount="0", currency="VND")
    assert assess([a, currency_conflict])["groups"][0]["status"] == "CONFLICT"


def test_expired_offer_is_retained_and_cannot_conflict_as_a_current_offer():
    old = offer(price_amount="0", currency="USD", expires_at="2026-10-30T04:30:00Z")
    recent = offer(price_amount="10", currency="USD", observed_at="2026-10-30T04:30:00Z")
    group = assess([old, recent])["groups"][0]
    assert group["status"] == "NO_CONFLICT_DETECTED"
    assert len(group["observations"]) == 2
    assert {item["freshness"] for item in group["observations"]} == {"STALE", "RECENT_OBSERVATION"}


@pytest.mark.parametrize("limit", [True, False, -1, 1.0, "1", None, 366 * 86400 + 1])
def test_invalid_freshness_policy_is_rejected(limit):
    with pytest.raises(ProductError, match="INVALID_FRESHNESS_LIMIT"):
        assess([], max_age_seconds=limit)


@pytest.mark.parametrize("as_of", ["2026-10-30", "2026-10-30T05:00:00", "2026-02-30T00:00:00Z",
                                      "9999-12-31T23:59:59-23:00", None])
def test_invalid_assessment_timestamp(as_of):
    with pytest.raises(ProductError, match="INVALID_TIMESTAMP"):
        assess([], as_of=as_of)


def test_rehashed_price_time_stock_commission_and_subject_forgery_rejected():
    original = offer(price_amount="10", currency="USD", stock_available=False)
    cases = [("price", {"amount": "99", "amount_presence": "VALUE", "currency": "USD", "status": "SOURCE_ASSERTED_NOT_LIVE"}),
             ("observed_at", "2026-10-30T03:00:00Z"),
             ("expires_at", "2026-11-01T00:00:00Z"),
             ("stock", {"available": True, "presence": "VALUE"})]
    for field, changed in cases:
        value = original.to_dict()
        value[field] = changed
        with pytest.raises(ProductError, match="OFFER_REPLAY_MISMATCH"):
            validate_contract("ProductOfferObservation", reseal(value), raw=original.raw_bytes(), snapshot=original._snapshot)
    value = original.to_dict()
    value["commission"]["status"] = "ELIGIBLE"
    with pytest.raises(ProductError, match="INVALID_PRODUCTOFFEROBSERVATION"):
        validate_contract("ProductOfferObservation", reseal(value), raw=original.raw_bytes(), snapshot=original._snapshot)
    with pytest.raises(ProductError, match="PRODUCT_SUBJECT_MISMATCH"):
        ProductOfferObservation.from_bytes(original.to_bytes(), raw=original.raw_bytes(), snapshot=snapshot(variant_key="blue"))
    with pytest.raises(ProductError, match="RAW_PROVENANCE_MISMATCH"):
        ProductOfferObservation.from_bytes(original.to_bytes(), raw=original.raw_bytes() + b"\n", snapshot=original._snapshot)
    with pytest.raises(ProductError, match="RAW_EVIDENCE_REQUIRED"):
        validate_contract("ProductOfferObservation", original.to_dict(), snapshot=original._snapshot)
    with pytest.raises(ProductError, match="LISTING_SNAPSHOT_REQUIRED"):
        validate_contract("ProductOfferObservation", original.to_dict(), raw=original.raw_bytes())


@pytest.mark.parametrize("pointer,code", [
    ("missing", "INVALID_EVIDENCE_POINTER"), ("/bad~2", "INVALID_EVIDENCE_POINTER"),
    ("/missing", "EVIDENCE_POINTER_NOT_FOUND"), ("/data/01", "EVIDENCE_POINTER_NOT_FOUND"),
    ("/data/99", "EVIDENCE_POINTER_NOT_FOUND"), ("/data/0/observed_at/child", "EVIDENCE_POINTER_NOT_FOUND"),
    ("/" * 17, "INVALID_EVIDENCE_POINTER"),
])
def test_invalid_local_evidence_pointers(pointer, code):
    raw = json.dumps({"data": [json.loads(offer_raw())]}).encode()
    with pytest.raises(ProductError, match=code):
        build_offer(snapshot(), ordinal=1, raw=raw, source_ref="local", captured_at=CAPTURE, pointer=pointer)


def test_preexported_source_pointer_raw_bounds_and_portable_schema():
    raw = json.dumps({"data": [json.loads(offer_raw(price_amount="1.1", currency="USD"))],
                      "ignored": "Keep exact original API export"}).encode()
    original = build_offer(snapshot(), ordinal=1, raw=raw, source_ref="https://invalid.test/metadata",
                           captured_at=CAPTURE, source_kind="PREEXPORTED_API", pointer="/data/0")
    assert original.to_dict()["price"]["amount"] == "1.1"
    assert original.raw_bytes() == raw
    for bad_raw, code in ((b"", "SOURCE_EMPTY_OR_TOO_LARGE"),
                          (b" " * (MAX_RAW_BYTES + 1), "SOURCE_EMPTY_OR_TOO_LARGE"),
                          (b'{"x":1,"x":2}', "JSON_DUPLICATE_KEY"), (b'{"x":NaN}', "JSON_NONFINITE")):
        with pytest.raises(ProductError, match=code):
            offer(raw=bad_raw)
    definition = schema("ProductOfferObservation")
    assert definition == json.loads((ROOT / "contracts/ProductOfferObservation.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(definition)
    assert Draft202012Validator(definition).is_valid(original.to_dict())
    for records in (iter([original]), [original.to_dict()], [original] * 2001):
        with pytest.raises(ProductError):
            assess(records)


def test_a12_01_fan_in_correction_claim_offer_and_freeform_consumer():
    from ai_commerce.creative.briefs import intake_brief
    from ai_commerce.product.claims import assess_claims, build_claim
    from ai_commerce.product.history import IdentityHistory

    original = snapshot(sku="old")
    correction = snapshot(sku="corrected")
    history = IdentityHistory().append(original).append(correction, correction_of=original.snapshot_id,
                                                       reason="Correct source SKU", actor_ref="human")
    raw = json.dumps({"schema_version": "ProductClaimSource.v1", "claim_key": "material",
                      "category": "COMPOSITION", "statement": "Seller asserts steel", "value": "steel",
                      "unit": None, "origin": "SELLER", "role": "SUPPORTING", "observed_at": OBSERVED}).encode()
    claim = build_claim(correction, ordinal=1, raw=raw, source_ref="seller-statement", captured_at=CAPTURE)
    old_offer = offer(source=original, price_amount="0", currency="USD", expires_at="2026-10-30T04:30:00Z")
    new_offer = offer(source=correction, price_amount="1", currency="USD")
    assert history.snapshots()[0].raw_bytes() == original.raw_bytes()
    assert history.to_dict()["conflicts"][0]["conflicting_fields"] == ["asserted_sku"]
    assert claim.to_dict()["subject"] == new_offer.to_dict()["subject"]
    assert assess_claims([claim])["groups"][0]["evidence"][0]["status"] == "SELLER_ASSERTED"
    assert assess([old_offer, new_offer])["groups"][0]["status"] == "NO_CONFLICT_DETECTED"
    assert intake_brief("Một câu chuyện tự do")["product_ref"] is None
