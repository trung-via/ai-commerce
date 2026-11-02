"""E05 native A14 -> A15 -> A16 offline integration and adversarial projections."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path
import socket
import struct
import zlib

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.affiliate_attribution import build_attribution_evidence
from ai_commerce.affiliate_commerce import build_affiliate_commerce_run
from ai_commerce.e2e import run as creative_run
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.cost_ledger import CostEntry, CostLedger
from ai_commerce.providers.mock_run import run_mock_job
from ai_commerce.settlement import (CommercialReview, SettlementLedger, commercial_review,
                                    commercial_review_schema)
from ai_commerce.settlement.evidence import canonical_bytes, digest
from ai_commerce.settlement.review import sum_money
from test_a16_settlement import source, receipt, build, revision, expect


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("E05 attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def png():
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress((b"\x00" + b"\x30\x50\x90" * 16) * 16)) + chunk(b"IEND", b""))


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    root = tmp_path_factory.mktemp("a16-native")
    still = root / "still.png"
    still.write_bytes(png())
    creative = root / "creative"
    creative_run("Fictional offline product demonstration", [still], creative, frames=2)
    manifest = json.loads((creative / "e01-manifest.json").read_bytes())
    scope = source()["scope"]
    scope = {key: val for key, val in scope.items() if key not in {"order_ref", "currency"}}
    scope.update(creative_ref=manifest["run_id"], creative_sha256=manifest["output_sha256"])
    request = {"schema_version": "AffiliateCommerceRequest.v1", "idempotency_key": "native-review-run",
               "as_of": "2026-11-03T00:00:00Z", "scope": scope}
    attr_scope = {key: scope[key] for key in ("provider_ref", "platform", "account_ref", "market", "surface", "measurement_window", "timezone")}
    attr_scope.update(app_ref=None, cohort_ref=None)
    events = [
        {"kind": "LINK_CREATIVE", "event_ref": "map", "observed_at": "2026-11-01T00:10:00Z", "link_ref": scope["link_ref"],
         "creative_ref": scope["creative_ref"], "creative_sha256": scope["creative_sha256"], "reported_link_status": "UNKNOWN"},
        {"kind": "CLICK_LINK", "event_ref": "click", "observed_at": "2026-11-01T00:20:00Z", "click_ref": "fixture-click", "link_ref": scope["link_ref"]}]
    attr_doc = {"schema_version": "AffiliateAttributionSource.v1", "source_kind": "SYNTHETIC_FIXTURE",
                "idempotency_key": "native-attr", "scope": attr_scope, "upstream_refs": [], "events": events}
    click_attr = build_attribution_evidence(canonical_bytes(attr_doc))
    args = {"request": request, "creative_directory": creative, "attribution": click_attr}
    click_run = build_affiliate_commerce_run(**args)
    attr_doc["events"].append({"kind": "ORDER_CLICK", "event_ref": "order", "observed_at": "2026-11-01T01:00:00Z",
                               "click_ref": "fixture-click", "order_ref": "fixture-order", "reported_order_status": "PENDING",
                               "reported_count": {"status": "AVAILABLE", "value": 1}})
    order_attr = build_attribution_evidence(canonical_bytes(attr_doc))
    order_run = build_affiliate_commerce_run(**{**args, "attribution": order_attr})
    # Also exercise the real A02 consumer: its noncommercial output stays so.
    brief = Path(__file__).resolve().parent / "fixtures/creative_only_brief.json"
    a02 = run_mock_job(brief, root / "a02-output")
    assert a02["publication_status"] == "NOT_ATTEMPTED" and a02["cost_amount"] is None
    return click_run, order_run, order_attr


@pytest.fixture
def stores(tmp_path):
    registry = ArtifactRegistry(tmp_path / "store")
    return SettlementLedger(registry), CostLedger(registry)


def document_for(run, status="CREATED", *, order="fixture-order", currency="USD", attr=None):
    doc = source(status, order=order, currency=currency)
    doc["scope"].update(deepcopy(run.to_dict()["request"]["scope"]))
    for row in doc["receipts"]:
        row["scope"] = deepcopy(doc["scope"])
    if attr:
        doc["attribution"] = {"evidence_id": attr.evidence_id, "raw_sha256": sha256(attr.to_bytes()).hexdigest(), "event_ref": "order", "tier": "A"}
    return doc


def cost(cost_ledger, *, entry_id="cost-estimate", status="ESTIMATED", amount="1.20", currency="USD", origin="UNVERIFIED", **changes):
    fields = {"entry_id": entry_id, "job_id": "fixture-media-job", "attempt": 1, "intent_digest": "a" * 64,
              "provider": "fixture-media-provider", "model": None, "status": status, "amount": amount, "currency": currency,
              "evidence_ref": "fixture:receipt-" + entry_id if status == "CHARGED" else None,
              "evidence_sha256": sha256(entry_id.encode()).hexdigest() if status == "CHARGED" else None,
              "evidence_origin": origin, "observed_at": "2026-11-02T02:00:00Z"}
    fields.update(changes)
    entry = CostEntry(**fields)
    cost_ledger.record(entry)
    return {key: getattr(entry, key) for key in ("entry_id", "job_id", "attempt", "intent_digest", "provider")}


def review(run, stores, selections=()):
    return commercial_review(commerce_run=run, settlement_ledger=stores[0], cost_ledger=stores[1], cost_selections=selections)


def test_native_a14_a15_a16_missing_commercial_evidence_is_partial_unknown(native, stores):
    selection = cost(stores[1])
    result = review(native[0], stores, [selection]).to_dict()
    assert result["status"] == "PARTIAL"
    assert result["orders"] == [] and result["supplied_order_count"] == {"status": "UNKNOWN", "value": None}
    assert result["actual_settlement_status"] == "UNKNOWN" and result["actual_orders_verified"] is False
    assert result["realized_profit"] == {"status": "UNKNOWN", "amount": None, "verified": False}
    assert "SETTLEMENT_EVIDENCE_MISSING" in result["reason_codes"]
    bucket = result["by_currency"][0]
    assert bucket["estimated_cost"]["amount"] == "1.20"
    assert bucket["settled_commission"]["amount"] is None and bucket["order_gross"]["amount"] is None
    assert bucket["supplied_charged_cost"]["amount"] is None
    assert result["network_calls_performed"] is False
    assert result["sources"]["cost_records"][0]["job_id"] == "fixture-media-job"


def test_no_evidence_is_unknown_not_zero(native, stores):
    value = review(native[0], stores).to_dict()
    assert value["by_currency"] == []
    assert value["supplied_order_count"]["value"] is None
    assert {"COST_EVIDENCE_MISSING", "SETTLEMENT_EVIDENCE_MISSING"} <= set(value["reason_codes"])


def test_order_gross_credit_and_paid_commission_remain_separate(native, stores):
    doc = document_for(native[1], "SETTLED", attr=native[2])
    assert stores[0].append(build(doc, attribution=native[2]))
    selection = cost(stores[1], entry_id="manual-charge", status="CHARGED", amount="2.00", origin="USER_SUPPLIED")
    value = review(native[1], stores, [selection]).to_dict()
    bucket = value["by_currency"][0]
    assert bucket["order_gross"]["amount"] == "100.00"
    assert bucket["credited_commission"]["amount"] == bucket["settled_commission"]["amount"] == "10.00"
    assert bucket["supplied_charged_cost"]["amount"] == "2.00"
    assert value["orders"][0]["reported_status"] == "SETTLED" and value["orders"][0]["attribution_tier"] == "A"
    assert value["supplied_order_count"] == {"status": "REPORTED", "value": 1}
    assert value["status"] == "PARTIAL" and value["realized_profit"]["amount"] is None
    assert not value["external_provider_charge_verified"]


def test_synthetic_billed_charge_is_never_business_spend(native, stores):
    selected = cost(stores[1], entry_id="synthetic-bill", status="CHARGED", amount="99.25", origin="SYNTHETIC_FIXTURE")
    bucket = review(native[0], stores, [selected]).to_dict()["by_currency"][0]
    assert bucket["synthetic_charged_cost"]["amount"] == "99.25"
    assert bucket["supplied_charged_cost"]["status"] == "UNKNOWN"
    assert bucket["supplied_charged_cost"]["amount"] is None


def test_unknown_cost_does_not_disappear_or_become_zero(native, stores):
    selections = [cost(stores[1]), cost(stores[1], entry_id="unknown", status="UNKNOWN", amount=None, currency=None)]
    value = review(native[0], stores, selections).to_dict()
    metric = value["by_currency"][0]["estimated_cost"]
    assert metric == {"status": "PARTIAL", "amount": None, "reported_subtotal": "1.20", "known_count": 1, "unknown_count": 1}
    assert "UNKNOWN_COST_CURRENCY" in value["reason_codes"]


@pytest.mark.parametrize("state", ["REFUNDED", "REVOKED"])
def test_refund_or_revocation_after_payment_retains_paid_amount_and_holds(native, stores, state):
    paid = build(document_for(native[1], "SETTLED", attr=native[2]), attribution=native[2])
    assert stores[0].append(paid)
    doc = revision(paid, state)
    receipt(doc, "REFUND" if state == "REFUNDED" else "REVERSAL", "100.00" if state == "REFUNDED" else "10.00")
    assert stores[0].append(build(doc, attribution=native[2], correction_of=paid))
    value = review(native[1], stores).to_dict()
    assert value["status"] == "HOLD" and value["orders"][0]["reported_status"] == state
    assert value["by_currency"][0]["settled_commission"]["amount"] == "10.00"
    assert ("REFUND_AFTER_SETTLEMENT" if state == "REFUNDED" else "REVOCATION_AFTER_SETTLEMENT") in value["reason_codes"]
    assert value["realized_profit"]["verified"] is False


def test_latest_revision_only_is_counted(native, stores):
    first = build(document_for(native[0], "APPROVED"))
    assert stores[0].append(first)
    doc = revision(first, "APPROVED")
    receipt(doc, "COMMISSION", "8.00", ref="commission-correction", correction_of=doc["receipts"][-1]["raw_sha256"])
    assert stores[0].append(build(doc, correction_of=first))
    value = review(native[0], stores).to_dict()
    assert value["supplied_order_count"]["value"] == 1
    assert value["by_currency"][0]["credited_commission"]["amount"] == "8.00"
    assert len(json.loads(value["sources"]["settlement_history"]["raw_utf8"])["records"]) == 2


def test_currencies_are_separate_and_missing_orders_are_not_zero(native, stores):
    for order, currency in [("usd-order", "USD"), ("vnd-order", "VND")]:
        assert stores[0].append(build(document_for(native[0], "APPROVED", order=order, currency=currency)))
    selection = cost(stores[1], currency="EUR")
    value = review(native[0], stores, [selection]).to_dict()
    buckets = {row["currency"]: row for row in value["by_currency"]}
    assert set(buckets) == {"USD", "VND", "EUR"}
    assert buckets["USD"]["order_gross"]["amount"] == buckets["VND"]["order_gross"]["amount"] == "100.00"
    assert buckets["EUR"]["order_gross"]["amount"] is None
    expect("CROSS_CURRENCY_ADDITION", lambda: sum_money([
        {"status": "REPORTED", "amount": "1", "currency": "USD"},
        {"status": "REPORTED", "amount": "100", "currency": "VND"}], currency="USD"))


@pytest.mark.parametrize("field,value", [("provider_ref", "wrong-provider"), ("account_ref", {"namespace": "internal_alias", "id": "wrong-account"}),
    ("link_ref", "wrong-link"), ("variant_id", "red"), ("channel", "other-channel"), ("market", "US"),
    ("measurement_window", ["2026-10-31T00:00:00Z", "2026-11-02T00:00:00Z"])])
def test_review_rejects_wrong_native_commercial_scope(native, stores, field, value):
    doc = document_for(native[0])
    doc["scope"][field] = doc["receipts"][0]["scope"][field] = value
    assert stores[0].append(build(doc))
    expect("REVIEW_SCOPE_MISMATCH", lambda: review(native[0], stores))


def test_native_attribution_must_be_the_one_pinned_by_commerce_run(native, stores):
    assert stores[0].append(build(document_for(native[1], "ATTRIBUTED", attr=native[2]), attribution=native[2]))
    expect("COMMERCE_ATTRIBUTION_PIN_MISMATCH", lambda: review(native[0], stores))


@pytest.mark.parametrize("field,value", [("provider", "wrong-provider"), ("job_id", "wrong-job"), ("attempt", 2), ("intent_digest", "b" * 64)])
def test_cost_job_attempt_intent_provider_identity_is_checked(native, stores, field, value):
    selection = cost(stores[1])
    selection[field] = value
    expect("COST_SOURCE_IDENTITY_MISMATCH", lambda: review(native[0], stores, [selection]))


def test_duplicate_cost_entry_and_receipt_alias_do_not_double_count(native, stores):
    estimate_one = cost(stores[1], entry_id="estimate-one")
    estimate_two = cost(stores[1], entry_id="estimate-two")
    expect("DUPLICATE_COST_OBSERVATION", lambda: review(native[0], stores, [estimate_one, estimate_two]))
    selected = cost(stores[1], entry_id="charge-one", status="CHARGED", origin="USER_SUPPLIED")
    expect("DUPLICATE_COST_ENTRY", lambda: review(native[0], stores, [selected, selected]))
    same_hash = sha256(b"charge-one").hexdigest()
    other = cost(stores[1], entry_id="charge-two", status="CHARGED", origin="USER_SUPPLIED", evidence_sha256=same_hash)
    expect("DUPLICATE_COST_RECEIPT", lambda: review(native[0], stores, [selected, other]))


def test_cost_hash_corruption_and_permission_failure_hold(native, stores, monkeypatch):
    selected = cost(stores[1])
    path = stores[1].registry.safe_path(CostLedger.ref(selected["entry_id"]))
    record = json.loads(path.read_bytes())
    record["amount"] = "10000"
    path.write_bytes(canonical_bytes(record))
    expect("COST_STORAGE_UNPROVEN", lambda: review(native[0], stores, [selected]))
    def denied(*args):
        raise PermissionError("private detail must not be echoed")
    monkeypatch.setattr(stores[1], "get", denied)
    expect("COST_STORAGE_UNPROVEN", lambda: review(native[0], stores, [selected]))


def test_review_is_read_only_schema_mirrored_immutable_and_portable(native, stores):
    selected = cost(stores[1])
    assert stores[0].append(build(document_for(native[0])))
    root = stores[1].registry.root
    before = {str(path): sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
    first = review(native[0], stores, [selected])
    assert review(native[0], stores, [selected]) == first
    after = {str(path): sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
    assert before == after
    schema = commercial_review_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(first.to_dict())
    path = Path(__file__).resolve().parents[1] / "contracts/CommercialReview.v1.schema.json"
    assert json.loads(path.read_bytes()) == schema
    assert CommercialReview.from_bytes(first.to_bytes(), expected_id=first.review_id) == first
    with pytest.raises(FrozenInstanceError):
        first._raw = b"tamper"


@pytest.mark.parametrize("field,value", [("actual_orders_verified", True), ("external_provider_charge_verified", True),
    ("status", "PASS"), ("actual_settlement_status", "SETTLED")])
def test_forged_provider_success_fields_are_rejected(native, stores, field, value):
    record = review(native[0], stores).to_dict()
    record[field] = value
    expect("INVALID_COMMERCIAL_REVIEW", lambda: CommercialReview.from_bytes(canonical_bytes(record)))


@pytest.mark.parametrize("mutation", ["derived", "native_hash", "cost_origin", "cost_float", "cost_checksum", "source_digest"])
def test_resealed_or_corrupted_portable_review_cannot_upgrade_evidence(native, stores, mutation):
    selected = cost(stores[1], entry_id="fake-bill", status="CHARGED", origin="SYNTHETIC_FIXTURE")
    record = review(native[0], stores, [selected]).to_dict()
    code = "COMMERCIAL_REVIEW_REPLAY_MISMATCH"
    if mutation == "derived":
        record["by_currency"][0]["synthetic_charged_cost"]["amount"] = "999"
    elif mutation == "native_hash":
        record["sources"]["commerce_run"]["raw_sha256"] = "0" * 64
        code = "SOURCE_HASH_MISMATCH"
    elif mutation == "source_digest":
        record["sources"]["settlement_history"]["raw_sha256"] = "0" * 64
        code = "SOURCE_HASH_MISMATCH"
    elif mutation == "cost_checksum":
        record["sources"]["cost_records"][0]["amount"] = "99"
        code = "INVALID_COST_EVIDENCE"
    else:
        record["sources"]["cost_records"][0]["evidence_origin" if mutation == "cost_origin" else "amount"] = "PROVIDER_ATTESTED" if mutation == "cost_origin" else 1.2
        code = "INVALID_COMMERCIAL_REVIEW"
    payload = {key: val for key, val in record.items() if key not in {"record_sha256", "review_id"}}
    record["record_sha256"] = digest(payload)
    record["review_id"] = "a16-review-" + record["record_sha256"]
    expect(code, lambda: CommercialReview.from_bytes(canonical_bytes(record)))


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", 1.2, True])
def test_projection_does_not_accept_malformed_or_float_money(amount):
    expect("INVALID_MONEY_OBSERVATION", lambda: sum_money([{"status": "REPORTED", "amount": amount, "currency": "USD"}], currency="USD"))
