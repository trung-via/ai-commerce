"""L05 positive/negative AC; all aliases and receipts are fictional fixtures."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
import socket

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.affiliate_attribution import build_attribution_evidence
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.settlement import (SettlementError, SettlementEvidence, SettlementLedger,
                                    admit_settlement, append_order, settlement_evidence_schema,
                                    settlement_source_schema)
from ai_commerce.settlement.evidence import canonical_bytes
from ai_commerce.settlement.ledger import validate_history

FIELDS = {"ORDER": "order_gross", "COMMISSION": "credited_commission", "SETTLEMENT": "settled_commission",
          "REFUND": "refund_gross", "REVERSAL": "reversed_commission", "FEE": "fees"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A16 attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def source(status="CREATED", *, order="fixture-order", currency="USD"):
    scope = {"provider_ref": "fixture-provider", "platform": "TIKTOK",
             "account_ref": {"namespace": "internal_alias", "id": "fixture-account"}, "app_ref": None,
             "market": "VN", "surface": "VIDEO", "channel": "social", "product_id": "fixture-product",
             "listing_id": "fixture-listing", "variant_id": "blue", "link_ref": "fixture-link",
             "link_url_sha256": "1" * 64, "creative_ref": "fixture-creative", "creative_sha256": "2" * 64,
             "measurement_window": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"], "timezone": "UTC",
             "order_ref": order, "currency": currency}
    doc = {"schema_version": "SettlementSource.v1", "source_kind": "SYNTHETIC_FIXTURE",
           "idempotency_key": "import-" + order, "source_ref": "fixture-import", "recorded_at": "2026-11-03T00:00:00Z",
           "scope": scope, "reported_status": status, "commission_basis": "UNKNOWN", "attribution": None,
           "amounts": {name: {"status": "UNKNOWN", "amount": None, "currency": currency} for name in FIELDS.values()}, "receipts": []}
    if status != "UNKNOWN":
        receipt(doc, "ORDER", "100.00")
    if status in {"APPROVED", "PARTIALLY_SETTLED", "SETTLED", "REVOKED"}:
        receipt(doc, "COMMISSION", "10.00")
    if status in {"PARTIALLY_SETTLED", "SETTLED"}:
        receipt(doc, "SETTLEMENT", "4.00" if status == "PARTIALLY_SETTLED" else "10.00")
    if status == "REFUNDED":
        receipt(doc, "REFUND", "100.00")
    if status == "REVOKED":
        receipt(doc, "REVERSAL", "10.00")
    return doc


def receipt(doc, kind, amount, *, ref=None, correction_of=None):
    ref = ref or doc["scope"]["order_ref"] + "-receipt-" + kind.lower()
    row = {"receipt_ref": ref, "raw_sha256": sha256((doc["scope"]["order_ref"] + ":" + ref).encode()).hexdigest(),
           "correction_of": correction_of, "source_kind": doc["source_kind"], "scope": deepcopy(doc["scope"]), "kind": kind,
           "occurred_at": "2026-11-01T01:00:00Z" if kind == "ORDER" else "2026-11-02T01:00:00Z", "amount": amount}
    field = FIELDS[kind]
    with localcontext() as context:
        context.prec = 64
        total = Decimal(doc["amounts"][field]["amount"] or "0") + Decimal(amount)
        if correction_of:
            total -= Decimal(next(old["amount"] for old in doc["receipts"] if old["raw_sha256"] == correction_of))
    doc["amounts"][field] = {"status": "REPORTED", "currency": doc["scope"]["currency"], "amount": format(total, "f")}
    doc["receipts"].append(row)
    if kind == "COMMISSION":
        doc["commission_basis"] = "CREATOR_SHARE"
    return row


def attribution(doc, *, clicks_only=False, missing_link=False, manual=False):
    scope = {key: deepcopy(doc["scope"][key]) for key in
             ("provider_ref", "platform", "account_ref", "market", "surface", "measurement_window", "timezone")}
    scope.update(app_ref=None, cohort_ref=None)
    events = [
        {"kind": "LINK_CREATIVE", "event_ref": "map", "observed_at": "2026-11-01T00:10:00Z",
         "link_ref": "fixture-link", "creative_ref": "fixture-creative", "creative_sha256": "2" * 64, "reported_link_status": "UNKNOWN"},
        {"kind": "CLICK_LINK", "event_ref": "click", "observed_at": "2026-11-01T00:20:00Z", "click_ref": "fixture-click", "link_ref": "fixture-link"}]
    if not clicks_only:
        events.append({"kind": "ORDER_CLICK", "event_ref": "order", "observed_at": "2026-11-01T01:00:00Z",
                       "click_ref": "fixture-click", "order_ref": doc["scope"]["order_ref"], "reported_order_status": "PENDING",
                       "reported_count": {"status": "AVAILABLE", "value": 1}})
    if missing_link:
        events = events[1:]
    attr = build_attribution_evidence(canonical_bytes({"schema_version": "AffiliateAttributionSource.v1",
        "source_kind": "USER_AUTHORED_MANUAL" if manual else "SYNTHETIC_FIXTURE", "idempotency_key": "fixture-attr",
        "scope": scope, "upstream_refs": [], "events": events}))
    doc["attribution"] = {"evidence_id": attr.evidence_id, "raw_sha256": sha256(attr.to_bytes()).hexdigest(),
                          "event_ref": "click" if clicks_only else "order", "tier": "D" if missing_link else "A"}
    return attr


def build(doc=None, **kwargs):
    return admit_settlement(canonical_bytes(source() if doc is None else doc), **kwargs)


def expect(code, action):
    with pytest.raises(SettlementError) as caught:
        action()
    assert str(caught.value) == caught.value.code == code


def revision(parent, status, *, key="revision-1", hour=1):
    doc = parent.to_dict()["source"]["document"]
    doc.update(idempotency_key=key, recorded_at=f"2026-11-03T{hour:02d}:00:00Z", reported_status=status)
    return doc


def test_schema_mirror_immutability_and_portable_replay():
    schema = settlement_evidence_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(settlement_source_schema())
    path = Path(__file__).resolve().parents[1] / "contracts/SettlementEvidence.v1.schema.json"
    assert json.loads(path.read_bytes()) == schema
    item = build()
    Draft202012Validator(schema).validate(item.to_dict())
    assert SettlementEvidence.from_bytes(item.to_bytes(), expected_id=item.evidence_id) == item
    with pytest.raises(FrozenInstanceError):
        item._raw = b"tampered"
    detached = item.to_dict()
    detached["source"]["document"]["reported_status"] = "SETTLED"
    assert item.to_dict()["source"]["document"]["reported_status"] == "CREATED"
    schema["properties"].clear()
    assert settlement_evidence_schema()["properties"]


@pytest.mark.parametrize("status", ["UNKNOWN", "CREATED", "ATTRIBUTED", "APPROVED", "PENDING", "REFUNDED", "REVOKED", "PARTIALLY_SETTLED", "SETTLED"])
def test_states_are_distinct_and_never_provider_verified(status):
    doc = source(status)
    attr = attribution(doc) if status == "ATTRIBUTED" else None
    item = build(doc, attribution=attr).to_dict()
    assert item["source"]["document"]["reported_status"] == status
    assert not item["actual_orders_verified"] and not item["settlement_verified"] and not item["realized_profit_verified"]
    if status in {"CREATED", "ATTRIBUTED", "PENDING", "APPROVED"}:
        assert item["source"]["document"]["amounts"]["settled_commission"] == {"status": "UNKNOWN", "amount": None, "currency": "USD"}


def test_exact_source_bytes_idempotent_reopen_and_changed_digest_collision(tmp_path):
    doc = source()
    raw = json.dumps(doc, indent=2).encode()
    first = admit_settlement(raw, expected_source_sha256=sha256(raw).hexdigest())
    assert first.to_dict()["source"]["raw_utf8"].encode() == raw
    ledger = SettlementLedger(ArtifactRegistry(tmp_path / "store"))
    assert append_order(ledger, first)
    assert ledger.append(first) is False
    assert SettlementLedger(ArtifactRegistry(ledger.registry.root)).append(first) is False
    expect("IDEMPOTENCY_KEY_COLLISION", lambda: ledger.append(build(doc)))
    changed = deepcopy(doc)
    changed["amounts"]["order_gross"]["amount"] = changed["receipts"][0]["amount"] = "200.00"
    expect("IDEMPOTENCY_KEY_COLLISION", lambda: ledger.append(build(changed)))
    expect("SOURCE_PIN_MISMATCH", lambda: admit_settlement(raw, expected_source_sha256="0" * 64))
    assert ledger.get(first.evidence_id) == first


def test_durable_created_pending_approved_partial_paid_refunded_sequence(tmp_path):
    ledger = SettlementLedger(ArtifactRegistry(tmp_path / "store"))
    parent = build()
    history = [parent]
    assert ledger.append(parent)
    for hour, (status, kind, amount) in enumerate([("PENDING", None, None), ("APPROVED", "COMMISSION", "10.00"),
        ("PARTIALLY_SETTLED", "SETTLEMENT", "4.00"), ("SETTLED", "SETTLEMENT", "6.00"), ("REFUNDED", "REFUND", "100.00")], 1):
        doc = revision(parent, status, key=f"r{hour}", hour=hour)
        if kind:
            receipt(doc, kind, amount, ref=f"receipt-step-{hour}")
        parent = build(doc, correction_of=parent)
        history.append(parent)
        assert ledger.append(parent)
    reopened = SettlementLedger(ArtifactRegistry(ledger.registry.root))
    assert reopened.latest() == (parent,)
    assert all(reopened.get(old.evidence_id) == old for old in history)
    assert parent.to_dict()["source"]["document"]["amounts"]["settled_commission"]["amount"] == "10.00"
    assert len(validate_history(reopened.to_bytes())) == 6


def test_receipt_correction_replaces_and_preserves_history_without_double_counting(tmp_path):
    first = build(source("APPROVED"))
    doc = revision(first, "APPROVED")
    old_sha = doc["receipts"][-1]["raw_sha256"]
    receipt(doc, "COMMISSION", "8.00", ref="corrected-commission", correction_of=old_sha)
    corrected = build(doc, correction_of=first)
    ledger = SettlementLedger(ArtifactRegistry(tmp_path / "store"))
    assert ledger.append(first) and ledger.append(corrected)
    assert ledger.latest()[0].to_dict()["source"]["document"]["amounts"]["credited_commission"]["amount"] == "8.00"
    assert len(corrected.to_dict()["source"]["document"]["receipts"]) == 3
    bad = revision(corrected, "APPROVED", key="bad", hour=2)
    receipt(bad, "COMMISSION", "5.00", ref="stale-replacement", correction_of=old_sha)
    expect("STALE_RECEIPT_CORRECTION", lambda: build(bad, correction_of=corrected))


def test_missing_parent_stale_parent_new_key_noop_and_stale_time(tmp_path):
    first = build()
    ledger = SettlementLedger(ArtifactRegistry(tmp_path / "store"))
    second = build(revision(first, "PENDING"), correction_of=first)
    expect("CORRECTION_PARENT_MISSING", lambda: ledger.append(second))
    assert ledger.append(first) and ledger.append(second)
    other = build(revision(first, "PENDING", key="competing", hour=2), correction_of=first)
    expect("STALE_CORRECTION", lambda: ledger.append(other))
    expect("CORRECTION_REQUIRES_NEW_KEY", lambda: build(revision(first, "PENDING", key=source()["idempotency_key"]), correction_of=first))
    expect("EMPTY_CORRECTION", lambda: build(revision(first, "CREATED"), correction_of=first))
    expect("STALE_CORRECTION_TIME", lambda: build(revision(first, "PENDING", hour=0), correction_of=first))


@pytest.mark.parametrize("field,value", [("provider_ref", "other-provider"), ("account_ref", {"namespace": "internal_alias", "id": "other-account"}),
    ("currency", "VND"), ("link_ref", "other-link"), ("order_ref", "other-order"),
    ("measurement_window", ["2026-10-31T00:00:00Z", "2026-11-02T00:00:00Z"]), ("variant_id", "red"), ("market", "US")])
def test_wrong_receipt_scope(field, value):
    doc = source()
    doc["receipts"][0]["scope"][field] = value
    expect("RECEIPT_SCOPE_MISMATCH", lambda: build(doc))


@pytest.mark.parametrize("field,value", [("provider_ref", "other-provider"), ("account_ref", {"namespace": "internal_alias", "id": "other-account"}),
    ("market", "US"), ("measurement_window", ["2026-10-31T00:00:00Z", "2026-11-02T00:00:00Z"])])
def test_native_attribution_scope_is_bound(field, value):
    doc = source("ATTRIBUTED")
    attr = attribution(doc)
    doc["scope"][field] = doc["receipts"][0]["scope"][field] = value
    expect("ATTRIBUTION_SCOPE_MISMATCH", lambda: build(doc, attribution=attr))


@pytest.mark.parametrize("field,value", [("link_ref", "other-link"), ("creative_sha256", "3" * 64)])
def test_attribution_link_and_creative_are_bound(field, value):
    doc = source("ATTRIBUTED")
    attr = attribution(doc)
    doc["scope"][field] = doc["receipts"][0]["scope"][field] = value
    expect("ATTRIBUTION_IDENTITY_MISMATCH", lambda: build(doc, attribution=attr))


def test_click_is_not_an_order_and_ambiguous_attribution_is_not_exact():
    doc = source("ATTRIBUTED")
    click = attribution(doc, clicks_only=True)
    expect("ATTRIBUTION_ORDER_REQUIRED", lambda: build(doc, attribution=click))
    doc = source("ATTRIBUTED")
    ambiguous = attribution(doc, missing_link=True)
    expect("EXACT_ORDER_ATTRIBUTION_REQUIRED", lambda: build(doc, attribution=ambiguous))


@pytest.mark.parametrize("origin", ["PROVIDER_ATTESTED", "PROVIDER_VERIFIED", "UNKNOWN"])
def test_fake_provider_provenance(origin):
    doc = source()
    doc["source_kind"] = origin
    expect("INVALID_SETTLEMENT_SOURCE", lambda: build(doc))


def test_manual_stays_unattested_and_synthetic_cannot_be_laundered():
    doc = source()
    doc["source_kind"] = doc["receipts"][0]["source_kind"] = "USER_SUPPLIED"
    assert build(doc).to_dict()["settlement_verified"] is False
    synthetic = attribution(doc)
    expect("SYNTHETIC_PROVENANCE_DOWNGRADE", lambda: build(doc, attribution=synthetic))
    manual = attribution(doc, manual=True)
    assert build(doc, attribution=manual).to_dict()["actual_orders_verified"] is False


@pytest.mark.parametrize("amount", [1.25, float("nan"), float("inf"), True, "NaN", "Infinity", "-1", "-0", "01", "1e2", ".5", "1\n", "1.0000001", "9" * 25])
def test_invalid_money(amount):
    doc = source()
    doc["amounts"]["order_gross"]["amount"] = amount
    nonfinite = isinstance(amount, float) and (amount != amount or amount == float("inf"))
    expect("INVALID_JSON_VALUE" if nonfinite else "INVALID_SETTLEMENT_SOURCE", lambda: build(doc))


@pytest.mark.parametrize("raw,code", [(b"{}", "INVALID_SETTLEMENT_SOURCE"), (b"[]", "JSON_OBJECT_REQUIRED"), (b"{", "INVALID_JSON"),
    (b'{"a":1,"a":2}', "DUPLICATE_JSON_KEY"), (b'{"amount":NaN}', "NONFINITE_JSON_NUMBER"),
    (b"\xff", "INVALID_JSON"), (b" " * 262145, "INPUT_SIZE_OR_TYPE_INVALID")],
    ids=["empty-object", "array", "broken-json", "duplicate-key", "nonfinite", "invalid-utf8", "oversized"])
def test_malformed_and_bounded_input(raw, code):
    expect(code, lambda: admit_settlement(raw))


def test_missing_settlement_is_unknown_never_zero_or_paid():
    doc = source("APPROVED")
    doc["reported_status"] = "SETTLED"
    expect("SETTLEMENT_EVIDENCE_REQUIRED", lambda: build(doc))
    doc["amounts"]["settled_commission"] = {"status": "REPORTED", "currency": "USD", "amount": "0"}
    expect("AMOUNT_RECEIPT_MISMATCH", lambda: build(doc))


def test_amounts_cannot_exceed_receipts_credit_or_claim_partial_for_full_payment():
    doc = source("SETTLED")
    doc["amounts"]["settled_commission"]["amount"] = "20"
    expect("AMOUNT_RECEIPT_MISMATCH", lambda: build(doc))
    doc["receipts"][-1]["amount"] = "20"
    expect("SETTLEMENT_EXCEEDS_COMMISSION", lambda: build(doc))
    doc = source("SETTLED")
    doc["reported_status"] = "PARTIALLY_SETTLED"
    expect("SETTLEMENT_STATUS_AMOUNT_MISMATCH", lambda: build(doc))


def test_cross_currency_relabelling_and_wrong_expected_variant():
    doc = source()
    doc["amounts"]["order_gross"]["currency"] = "VND"
    expect("CURRENCY_MISMATCH", lambda: build(doc))
    doc = source()
    expected = deepcopy(doc["scope"])
    expected["variant_id"] = "other"
    expect("EXPECTED_SCOPE_MISMATCH", lambda: build(doc, expected_scope=expected))


def test_duplicate_receipt_under_different_ids_and_duplicate_orders(tmp_path):
    doc = source("APPROVED")
    duplicate = deepcopy(doc["receipts"][-1])
    duplicate["receipt_ref"] = "different-id"
    doc["receipts"].append(duplicate)
    expect("DUPLICATE_RECEIPT_CONTENT", lambda: build(doc))
    ledger = SettlementLedger(ArtifactRegistry(tmp_path / "store"))
    first = build()
    assert ledger.append(first)
    other_import = source()
    other_import["idempotency_key"] = "different-import"
    expect("DUPLICATE_ORDER", lambda: ledger.append(build(other_import)))
    other = source(order="order-two")
    other["receipts"][0]["receipt_ref"] = "different-receipt-id"
    other["receipts"][0]["raw_sha256"] = first.to_dict()["source"]["document"]["receipts"][0]["raw_sha256"]
    expect("RECEIPT_IDENTITY_COLLISION", lambda: ledger.append(build(other)))


def test_correction_cannot_rebind_scope_erase_receipts_or_undo_refund():
    first = build()
    doc = revision(first, "PENDING")
    doc["scope"]["variant_id"] = doc["receipts"][0]["scope"]["variant_id"] = "red"
    expect("CORRECTION_SCOPE_MISMATCH", lambda: build(doc, correction_of=first))
    doc = revision(first, "UNKNOWN")
    doc["receipts"] = []
    doc["amounts"]["order_gross"] = {"status": "UNKNOWN", "amount": None, "currency": "USD"}
    expect("IMMUTABLE_RECEIPT_HISTORY_REQUIRED", lambda: build(doc, correction_of=first))
    refunded = build(source("REFUNDED"))
    expect("ORDER_STATE_COMMERCIAL_MISMATCH", lambda: build(revision(refunded, "CREATED"), correction_of=refunded))


@pytest.mark.parametrize("mutation", ["hash", "raw", "derived"])
def test_evidence_corruption_and_forged_promotion(mutation):
    record = build().to_dict()
    if mutation == "hash":
        record["record_sha256"] = "0" * 64
    elif mutation == "raw":
        record["source"]["raw_utf8"] = record["source"]["raw_utf8"].replace("100.00", "999.00")
    else:
        record["attribution_tier"] = "A"
    expect("SETTLEMENT_REPLAY_MISMATCH", lambda: SettlementEvidence.from_bytes(canonical_bytes(record)))
    record["settlement_verified"] = True
    expect("INVALID_SETTLEMENT_EVIDENCE", lambda: SettlementEvidence.from_bytes(canonical_bytes(record)))


def test_journal_corruption_and_sequence_gaps(tmp_path):
    ledger = SettlementLedger(ArtifactRegistry(tmp_path / "store"))
    assert ledger.append(build())
    history = json.loads(ledger.to_bytes())
    history["records"][0]["sequence"] = 2
    expect("JOURNAL_CHAIN_MISMATCH", lambda: validate_history(canonical_bytes(history)))
    path = ledger.registry.safe_path(ledger.ref(1))
    record = json.loads(path.read_bytes())
    record["journal_sha256"] = "0" * 64
    path.write_bytes(canonical_bytes(record))
    expect("JOURNAL_HASH_MISMATCH", ledger.latest)
    path.rename(path.with_name("0002.json"))
    expect("JOURNAL_SEQUENCE_GAP_OR_LIMIT", ledger.latest)


def test_lock_failure_and_record_limit_do_not_retry(tmp_path, monkeypatch):
    import ai_commerce.settlement.ledger as module
    ledger = SettlementLedger(ArtifactRegistry(tmp_path / "store"))
    with ledger.registry.worker():
        expect("LEDGER_STORAGE_UNPROVEN", lambda: ledger.append(build()))
    assert ledger.latest() == ()
    monkeypatch.setattr(module, "MAX_RECORDS", 1)
    assert ledger.append(build())
    expect("LEDGER_RECORD_LIMIT", lambda: ledger.append(build(source(order="second-order"))))


@pytest.mark.parametrize("field", ["email", "phone", "address", "api_key", "access_token", "customer"])
def test_no_pii_credentials_or_raw_input_in_diagnostics(field):
    doc = source()
    doc[field] = "secret@example.invalid"
    expect("INVALID_SETTLEMENT_SOURCE", lambda: build(doc))


def test_explicit_zero_and_extreme_decimal_precision():
    doc = source("SETTLED")
    for kind in ("COMMISSION", "SETTLEMENT"):
        next(row for row in doc["receipts"] if row["kind"] == kind)["amount"] = "0.00"
        doc["amounts"][FIELDS[kind]]["amount"] = "0.00"
    assert build(doc).to_dict()["source"]["document"]["amounts"]["settled_commission"]["amount"] == "0.00"
    doc = source("APPROVED")
    doc["receipts"][-1]["amount"] = doc["amounts"]["credited_commission"]["amount"] = "999999999999999999999999.000001"
    with localcontext() as context:
        context.prec = 3
        assert build(doc).to_dict()["source"]["document"]["amounts"]["credited_commission"]["amount"].endswith("000001")
