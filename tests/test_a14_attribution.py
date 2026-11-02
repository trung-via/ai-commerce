"""L04 local evidence semantics. Every supplied commerce identifier is synthetic."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import io
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.affiliate_attribution import (
    AffiliateAttributionEvidence, AttributionError, AttributionLedger,
    affiliate_attribution_schema, attribution_source_schema,
    build_attribution_evidence,
)
from ai_commerce.affiliate_attribution.__main__ import main
from ai_commerce.affiliate_attribution.evidence import canonical_bytes, digest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("L04 attempted network access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def source():
    return {
        "schema_version": "AffiliateAttributionSource.v1", "source_kind": "SYNTHETIC_FIXTURE",
        "idempotency_key": "synthetic-import-001",
        "scope": {"provider_ref": "synthetic-provider", "platform": "TIKTOK",
            "account_ref": {"namespace": "internal_alias", "id": "synthetic-account"},
            "app_ref": "synthetic-app", "market": "VN", "surface": "VIDEO",
            "measurement_window": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"],
            "timezone": "UTC", "cohort_ref": "synthetic-cohort"},
        "upstream_refs": [],
        "events": [
            {"event_ref": "map-01", "kind": "LINK_CREATIVE", "observed_at": "2026-11-01T01:00:00Z",
             "link_ref": "synthetic-link", "creative_ref": "synthetic-creative", "creative_sha256": "1"*64,
             "reported_link_status": "UNKNOWN"},
            {"event_ref": "click-01", "kind": "CLICK_LINK", "observed_at": "2026-11-01T02:00:00Z",
             "link_ref": "synthetic-link", "click_ref": "synthetic-click"},
            {"event_ref": "order-01", "kind": "ORDER_CLICK", "observed_at": "2026-11-01T03:00:00Z",
             "click_ref": "synthetic-click", "order_ref": "synthetic-order",
             "reported_order_status": "PENDING", "reported_count": {"status": "AVAILABLE", "value": 1}},
        ],
    }


def provider_event():
    return {"event_ref": "provider-01", "kind": "PROVIDER_REPORTED",
        "observed_at": "2026-11-01T03:00:00Z", "provider_report_ref": "synthetic-report",
        "link_ref": "synthetic-link", "creative_ref": "synthetic-creative", "creative_sha256": "1"*64,
        "order_ref": None, "reported_order_status": "UNKNOWN", "reported_link_status": "UNKNOWN",
        "reported_count": {"status": "AVAILABLE", "value": 0}}


def cohort_event():
    return {"event_ref": "cohort-01", "kind": "COHORT_REPORTED",
        "observed_at": "2026-11-01T03:00:00Z", "cohort_ref": "synthetic-cohort",
        "reported_order_status": "NOT_AVAILABLE", "reported_count": {"status": "NOT_AVAILABLE", "value": None}}


def directional_event():
    return {"event_ref": "direction-01", "kind": "DIRECTIONAL",
        "observed_at": "2026-11-01T03:00:00Z", "link_ref": None, "creative_ref": None,
        "creative_sha256": None, "reported_order_status": "UNKNOWN",
        "reported_count": {"status": "UNKNOWN", "value": None}}


def build(document=None, **kwargs):
    return build_attribution_evidence(canonical_bytes(source() if document is None else document), **kwargs)


def expect(code, action):
    with pytest.raises(AttributionError) as caught:
        action()
    assert caught.value.code == code
    assert str(caught.value) == code


def correction(parent, key="synthetic-correction", state="REFUNDED"):
    document = deepcopy(parent.to_dict()["source"]["document"])
    document["idempotency_key"] = key
    document["events"][-1]["reported_order_status"] = state
    return build(document, algorithm=parent.to_dict()["algorithm"], correction_of=parent)


def test_contract_mirror_and_immutable_bytes():
    contract = Path(__file__).resolve().parents[1] / "contracts/AffiliateAttributionEvidence.v1.schema.json"
    assert json.loads(contract.read_bytes()) == affiliate_attribution_schema()
    Draft202012Validator.check_schema(affiliate_attribution_schema())
    Draft202012Validator.check_schema(attribution_source_schema())
    evidence = build()
    Draft202012Validator(affiliate_attribution_schema()).validate(evidence.to_dict())
    assert AffiliateAttributionEvidence.from_bytes(evidence.to_bytes()) == evidence
    with pytest.raises(FrozenInstanceError):
        evidence._raw = b"changed"
    changed = evidence.to_dict()
    changed["results"][0]["tier"] = "D"
    assert evidence.to_dict()["results"][0]["tier"] == "A"
    schema = affiliate_attribution_schema()
    schema["properties"]["scope"].clear()
    assert affiliate_attribution_schema()["properties"]["scope"]


@pytest.mark.parametrize("algorithm", ["UNSPECIFIED", "FIRST_TOUCH", "LAST_TOUCH", "LINEAR", "PROVIDER_REPORTED"])
def test_all_tiers_are_independent_of_algorithm(algorithm):
    document = source()
    document["events"] += [provider_event(), cohort_event(), directional_event()]
    evidence = build(document, algorithm=algorithm).to_dict()
    results = {r["event_ref"]: r for r in evidence["results"]}
    assert {key: r["tier"] for key, r in results.items()} == {
        "order-01": "A", "provider-01": "B", "cohort-01": "C", "direction-01": "D"}
    assert results["order-01"]["support_event_refs"] == ["click-01", "map-01", "order-01"]
    assert results["cohort-01"]["creative_ref"] is None
    assert results["cohort-01"]["attribution_scope"] == "COHORT"
    assert results["direction-01"]["attribution_scope"] == "UNKNOWN"
    assert evidence["algorithm_execution"] == "DECLARATION_ONLY_NO_TOUCH_MODEL_EXECUTED"
    assert evidence["actual_orders_verified"] is False
    assert all(not r["per_video_profit_verified"] for r in evidence["results"])
    assert evidence["actual_affiliate_entitlement"] == evidence["actual_revenue"] == evidence["actual_payout"] == "UNKNOWN"
    assert evidence["publication_status"] == evidence["visibility_status"] == "NOT_ASSESSED"
    assert evidence["link_activation"] == "UNKNOWN"
    assert evidence["settlement_verification"] == "NOT_PERFORMED"


@pytest.mark.parametrize("state", ["UNKNOWN", "NOT_AVAILABLE", "PENDING", "REFUNDED", "REVOKED", "SETTLED"])
@pytest.mark.parametrize("count", [{"status": "AVAILABLE", "value": 0},
    {"status": "UNKNOWN", "value": None}, {"status": "NOT_AVAILABLE", "value": None}])
def test_reported_states_and_zero_remain_distinct(state, count):
    document = source()
    document["events"][-1].update(reported_order_status=state, reported_count=count)
    document["events"][0]["reported_link_status"] = "REVOKED"
    evidence = build(document).to_dict()
    result = evidence["results"][0]
    assert result["tier"] == "A"
    assert result["reported_order_status"] == state
    assert result["reported_count"] == count
    assert result["reported_link_status"] == "REVOKED"
    assert evidence["actual_payout"] == "UNKNOWN"


@pytest.mark.parametrize("kind", ["USER_AUTHORED_MANUAL", "USER_PROVIDED_EXPORT_PROJECTION", "SYNTHETIC_FIXTURE"])
def test_source_declaration_never_becomes_live_verification(kind):
    document = source()
    document["source_kind"] = kind
    record = build(document).to_dict()
    assert record["source_status"] == "USER_SUPPLIED_NOT_LIVE_VERIFIED"
    assert record["synthetic"] == (kind == "SYNTHETIC_FIXTURE")
    assert record["actual_orders_verified"] is False


def test_exact_bytes_and_context_bound_hashes():
    document = source()
    raw = canonical_bytes(document)
    evidence = build_attribution_evidence(raw, expected_source_sha256=sha256(raw).hexdigest())
    record = evidence.to_dict()
    assert record["source"]["raw_sha256"] == sha256(raw).hexdigest()
    assert record["source"]["raw_utf8"].encode() == raw
    for event_hash in record["event_hashes"]:
        event = next(e for e in document["events"] if e["event_ref"] == event_hash["event_ref"])
        assert event_hash["event_sha256"] == digest({"scope": document["scope"], "event": event})
    payload = {k: v for k, v in record.items() if k not in {"record_sha256", "evidence_id"}}
    assert record["record_sha256"] == digest(payload)
    assert evidence.evidence_id == "a14-attribution-" + digest(payload)
    assert build().to_bytes() == evidence.to_bytes()
    spaced = b"\xef\xbb\xbf" + json.dumps(document, indent=2).replace("\n", "\r\n").encode()
    other = build_attribution_evidence(spaced)
    assert other.to_dict()["source"]["canonical_sha256"] == record["source"]["canonical_sha256"]
    assert other.to_dict()["results"] == record["results"]
    assert other.evidence_id != evidence.evidence_id
    assert other.to_dict()["source"]["raw_utf8"].encode() == spaced
    assert AffiliateAttributionEvidence.from_bytes(other.to_bytes()) == other
    expect("SOURCE_PIN_MISMATCH", lambda: build_attribution_evidence(raw, expected_source_sha256="0"*64))
    expect("EVIDENCE_PIN_MISMATCH", lambda: AffiliateAttributionEvidence.from_bytes(evidence.to_bytes(), expected_id=other.evidence_id))


@pytest.mark.parametrize("field,value", [
    ("provider_ref", "other-provider"), ("account_ref", {"namespace": "internal_alias", "id": "other-account"}),
    ("app_ref", "other-app"), ("market", "US"), ("surface", "PROFILE_VIDEO"),
    ("cohort_ref", "other-cohort"),
    ("measurement_window", ["2026-11-01T00:30:00Z", "2026-11-02T00:00:00Z"]),
])
def test_scope_changes_bind_identity(field, value):
    baseline = build().to_dict()
    document = source()
    document["scope"][field] = value
    other = build(document).to_dict()
    assert other["evidence_id"] != baseline["evidence_id"]
    assert other["event_hashes"] != baseline["event_hashes"]


@pytest.mark.parametrize("removed,reason", [([0], "MISSING_LINK_CREATIVE"),
    ([1], "MISSING_CLICK"), ([0, 1], "MISSING_CLICK")])
def test_missing_chain_is_directional_without_fabricated_ids(removed, reason):
    document = source()
    document["events"] = [e for i, e in enumerate(document["events"]) if i not in removed]
    result = build(document).to_dict()["results"][0]
    assert result["tier"] == "D"
    assert result["attribution_scope"] == "UNKNOWN"
    assert result["reason_code"] == reason
    assert result["creative_ref"] is None
    assert result["reported_count"]["value"] == 1  # Preserve the source, without attributing it.


@pytest.mark.parametrize("position,time", [(0, "2026-11-01T02:30:00Z"), (1, "2026-11-01T04:00:00Z")])
def test_noncausal_timestamp_order_does_not_join(position, time):
    document = source()
    document["events"][position]["observed_at"] = time
    result = build(document).to_dict()["results"][0]
    assert result["tier"] == "D"
    assert result["reason_code"] == "NON_CAUSAL_EVENT_ORDER"


def test_duplicates_and_row_order_do_not_change_derived_results():
    original = build().to_dict()
    document = source()
    document["events"] += deepcopy(document["events"])
    document["events"].reverse()
    record = build(document).to_dict()
    assert record["results"] == original["results"]
    assert record["event_hashes"] == original["event_hashes"]
    assert record["evidence_id"] != original["evidence_id"]  # Raw custody remains exact.


@pytest.mark.parametrize("position,field,value,code", [
    (0, "creative_ref", "other-creative", "LINK_ID_COLLISION"),
    (0, "creative_sha256", "2"*64, "CREATIVE_ID_COLLISION"),
    (1, "link_ref", "other-link", "CLICK_ID_COLLISION"),
    (2, "click_ref", "other-click", "ORDER_ID_COLLISION"),
    (2, "reported_order_status", "SETTLED", "ORDER_ID_COLLISION"),
])
def test_conflicting_semantic_identifiers_fail_closed(position, field, value, code):
    document = source()
    conflict = deepcopy(document["events"][position])
    conflict.update(event_ref="other-event", **{field: value})
    document["events"].append(conflict)
    expect(code, lambda: build(document))


def test_event_and_provider_report_collisions():
    document = source()
    bad = deepcopy(document["events"][0])
    bad["creative_ref"] = "other-creative"
    document["events"].append(bad)
    expect("EVENT_ID_COLLISION", lambda: build(document))
    document = source()
    first, second = provider_event(), provider_event()
    second.update(event_ref="provider-02", reported_count={"status": "AVAILABLE", "value": 1})
    document["events"] = [first, second]
    expect("PROVIDER_REPORT_ID_COLLISION", lambda: build(document))


def test_ambiguous_equivalent_mapping_rows_are_not_arbitrarily_selected():
    for index, code in [(0, "LINK_ID_COLLISION"), (1, "CLICK_ID_COLLISION"), (2, "ORDER_ID_COLLISION")]:
        document = source()
        duplicate = deepcopy(document["events"][index])
        duplicate["event_ref"] = "other-event"
        document["events"].append(duplicate)
        expect(code, lambda: build(document))
    document = source()
    first, second = provider_event(), provider_event()
    second["event_ref"] = "other-report-event"
    document["events"] = [first, second]
    expect("PROVIDER_REPORT_ID_COLLISION", lambda: build(document))


@pytest.mark.parametrize("count", [
    {"status": "UNKNOWN", "value": 0}, {"status": "NOT_AVAILABLE", "value": 0},
    {"status": "AVAILABLE", "value": None}, {"status": "AVAILABLE", "value": True},
    {"status": "AVAILABLE", "value": -1}, {"status": "AVAILABLE", "value": "0"},
    {"status": "AVAILABLE", "value": 10**12+1}, {"status": "PENDING", "value": None},
])
def test_invalid_count_states(count):
    document = source()
    document["events"][-1]["reported_count"] = count
    expect("INVALID_SOURCE_CONTRACT", lambda: build(document))


def test_integral_float_and_large_single_order_are_rejected():
    document = source()
    document["events"][-1]["reported_count"]["value"] = 1.0
    expect("COUNT_INTEGER_REQUIRED", lambda: build(document))
    document["events"][-1]["reported_count"]["value"] = 2
    expect("SINGLE_ORDER_COUNT_INVALID", lambda: build(document))


@pytest.mark.parametrize("private_field", ["cookie", "ip_address", "device_id", "email", "customer_id",
    "crm", "authorization", "receipt", "revenue", "payout", "purchased", "actual_entitlement"])
def test_out_of_scope_and_personal_fields_are_rejected(private_field):
    for place in ["document", "scope", "event"]:
        document = source()
        target = document if place == "document" else document["scope"] if place == "scope" else document["events"][-1]
        target[private_field] = "forbidden"
        expect("INVALID_SOURCE_CONTRACT", lambda: build(document))


@pytest.mark.parametrize("value", ["customer@example.invalid", "127.0.0.1", "https://example.invalid", "alias\nvalue", "", "a"*129])
def test_only_bounded_business_aliases_accepted(value):
    document = source()
    document["scope"]["account_ref"]["id"] = value
    expect("INVALID_SOURCE_CONTRACT", lambda: build(document))


@pytest.mark.parametrize("time", ["2026-11-01T00:00:00", "2026-11-01T00:00:00+00:00",
    "2026-02-30T00:00:00Z", "2026-11-01T25:00:00Z", "2026-11-01T23:59:60Z"])
def test_invalid_dates_fail_before_evaluation(time):
    document = source()
    document["events"][0]["observed_at"] = time
    expect("INVALID_SOURCE_CONTRACT", lambda: build(document))


def test_explicit_window_and_platform_identity_checks():
    document = source()
    document["scope"]["measurement_window"].reverse()
    expect("INVALID_MEASUREMENT_WINDOW", lambda: build(document))
    document = source()
    document["events"][0]["observed_at"] = document["scope"]["measurement_window"][1]
    expect("EVENT_OUTSIDE_WINDOW", lambda: build(document))
    document = source()
    document["scope"]["account_ref"]["namespace"] = "youtube_channel_id"
    expect("ACCOUNT_NAMESPACE_PLATFORM_MISMATCH", lambda: build(document))
    document = source()
    document["scope"]["surface"] = "SHORT"
    expect("SURFACE_PLATFORM_MISMATCH", lambda: build(document))


def test_cohort_and_directional_input_cannot_be_promoted():
    document = source()
    document["events"] = [cohort_event()]
    document["events"][0]["cohort_ref"] = "other-cohort"
    expect("COHORT_SCOPE_MISMATCH", lambda: build(document))
    document["events"] = [directional_event()]
    document["events"][0]["reported_count"] = {"status": "AVAILABLE", "value": 0}
    expect("DIRECTIONAL_COUNT_UNSUPPORTED", lambda: build(document))
    document["events"][0]["reported_count"] = {"status": "UNKNOWN", "value": None}
    document["events"][0]["creative_ref"] = "synthetic-creative"
    expect("INCOMPLETE_CREATIVE_IDENTITY", lambda: build(document))
    document["events"] = [cohort_event()]
    document["events"][0]["creative_ref"] = "synthetic-creative"
    expect("INVALID_SOURCE_CONTRACT", lambda: build(document))


def test_upstream_pins_are_preserved_without_creating_orders_or_entitlement():
    document = source()
    document["events"] = []
    document["upstream_refs"] = [{"component": component, "evidence_ref": "synthetic-evidence", "raw_sha256": "1"*64}
                                 for component in ["A06", "A07", "A08", "A09", "A12", "A13"]]
    record = build(document).to_dict()
    assert record["results"] == []
    assert record["source"]["document"]["upstream_refs"] == document["upstream_refs"]
    assert record["upstream_reference_verification"] == "HASH_PINS_ONLY_NOT_OPENED"
    assert record["actual_affiliate_entitlement"] == "UNKNOWN"
    document["upstream_refs"].append({**document["upstream_refs"][0], "raw_sha256": "2"*64})
    expect("UPSTREAM_REFERENCE_COLLISION", lambda: build(document))


@pytest.mark.parametrize("raw,code", [(b"{}", "INVALID_SOURCE_CONTRACT"),
    (b"[]", "JSON_OBJECT_REQUIRED"), (b'{"a":1,"a":2}', "DUPLICATE_JSON_KEY"),
    (b'{"a":NaN}', "NONFINITE_JSON_NUMBER"), (b'{"a":Infinity}', "NONFINITE_JSON_NUMBER"),
    (b'{"a":1e9999}', "INVALID_JSON_VALUE"), (b'\xff', "INVALID_JSON"),
    (b'{"a":"\\ud800"}', "INVALID_JSON_VALUE"), (b'{', "INVALID_JSON"),
    (b'', "INPUT_SIZE_OR_TYPE_INVALID"), ("text", "INPUT_SIZE_OR_TYPE_INVALID"),
    (b' '*262145, "INPUT_SIZE_OR_TYPE_INVALID")],
    ids=lambda value: f"bytes-{len(value)}" if type(value) is bytes else value)
def test_strict_bounded_json(raw, code):
    expect(code, lambda: build_attribution_evidence(raw))


def reseal(record):
    payload = {k: v for k, v in record.items() if k not in {"record_sha256", "evidence_id"}}
    record["record_sha256"] = digest(payload)
    record["evidence_id"] = "a14-attribution-" + record["record_sha256"]
    return canonical_bytes(record)


@pytest.mark.parametrize("target", ["tier", "scope", "source_document", "raw_hash", "canonical_hash",
    "raw_source", "event_hash", "support", "guardrail", "result_count"])
def test_tampering_and_resealed_derived_results_fail_replay(target):
    record = build().to_dict()
    if target == "tier":
        record["results"][0]["tier"] = "C"
    elif target == "scope":
        record["scope"]["account_ref"]["id"] = "other-account"
    elif target == "source_document":
        record["source"]["document"]["events"][-1]["reported_order_status"] = "SETTLED"
    elif target == "raw_hash":
        record["source"]["raw_sha256"] = "2"*64
    elif target == "canonical_hash":
        record["source"]["canonical_sha256"] = "2"*64
    elif target == "raw_source":
        record["source"]["raw_utf8"] = record["source"]["raw_utf8"].replace("PENDING", "SETTLED")
    elif target == "event_hash":
        record["event_hashes"][0]["event_sha256"] = "2"*64
    elif target == "support":
        record["results"][0]["support_event_refs"] = ["other-event"]
    elif target == "guardrail":
        record["actual_orders_verified"] = True
    else:
        record["results"][0]["reported_count"]["value"] = 0
    expected = "INVALID_EVIDENCE_CONTRACT" if target == "guardrail" else "EVIDENCE_REPLAY_MISMATCH"
    expect(expected, lambda: AffiliateAttributionEvidence.from_bytes(reseal(record)))


def test_integral_float_cannot_alias_hashed_integer_during_evidence_replay():
    for target in ["result", "source_document"]:
        record = build().to_dict()
        if target == "result":
            record["results"][0]["reported_count"]["value"] = 1.0
        else:
            record["source"]["document"]["events"][-1]["reported_count"]["value"] = 1.0
        expect("EVIDENCE_REPLAY_MISMATCH", lambda: AffiliateAttributionEvidence.from_bytes(canonical_bytes(record)))
        expect("EVIDENCE_REPLAY_MISMATCH", lambda: AffiliateAttributionEvidence.from_bytes(reseal(record)))


def test_ledger_retries_and_serialized_replay():
    ledger = AttributionLedger()
    evidence = build()
    assert ledger.append(evidence).duplicate is False
    original = ledger.to_bytes()
    assert ledger.append(evidence).duplicate is True
    assert ledger.to_bytes() == original
    assert ledger.get(evidence.evidence_id) == evidence
    assert ledger.active() == (evidence,)
    restored = AttributionLedger.from_bytes(original)
    assert restored.to_bytes() == original
    assert restored.append(evidence).duplicate
    changed = source()
    changed["events"][-1]["reported_count"]["value"] = 0
    expect("IDEMPOTENCY_KEY_COLLISION", lambda: ledger.append(build(changed)))
    expect("IDEMPOTENCY_KEY_COLLISION", lambda: ledger.append(build(algorithm="LAST_TOUCH")))
    spaced = build_attribution_evidence(json.dumps(source(), indent=2).encode())
    expect("IDEMPOTENCY_KEY_COLLISION", lambda: ledger.append(spaced))
    assert ledger.to_bytes() == original


def test_explicit_corrections_retain_history_and_conflicting_children_fail_atomically():
    original = build()
    first = correction(original)
    ledger = AttributionLedger()
    ledger.append(original)
    assert not ledger.append(first).duplicate
    before = ledger.to_bytes()
    assert ledger.get(original.evidence_id) == original
    assert ledger.active() == (first,)
    assert first.to_dict()["results"][0]["reported_order_status"] == "REFUNDED"
    assert ledger.append(first).duplicate
    expect("CONFLICTING_CORRECTION", lambda: ledger.append(correction(original, "competing-key", "SETTLED")))
    assert ledger.to_bytes() == before
    second = correction(first, "next-correction", "REVOKED")
    ledger.append(second)
    assert ledger.active() == (second,)
    assert AttributionLedger.from_bytes(ledger.to_bytes()).to_bytes() == ledger.to_bytes()
    empty = AttributionLedger()
    expect("CORRECTION_PARENT_MISSING", lambda: empty.append(first))
    assert empty.active() == ()


@pytest.mark.parametrize("change,code", [("scope", "CORRECTION_SCOPE_MISMATCH"),
    ("algorithm", "CORRECTION_ALGORITHM_MISMATCH"), ("source_kind", "CORRECTION_SOURCE_KIND_MISMATCH"),
    ("key", "CORRECTION_REQUIRES_NEW_KEY"), ("events", "CORRECTION_REQUIRES_CHANGED_EVENTS")])
def test_invalid_corrections(change, code):
    parent = build()
    document = source()
    document["idempotency_key"] = "new-key"
    document["events"][-1]["reported_order_status"] = "REFUNDED"
    algorithm = "UNSPECIFIED"
    if change == "scope":
        document["scope"]["account_ref"]["id"] = "other-account"
    elif change == "algorithm":
        algorithm = "LAST_TOUCH"
    elif change == "source_kind":
        document["source_kind"] = "USER_AUTHORED_MANUAL"
    elif change == "key":
        document["idempotency_key"] = source()["idempotency_key"]
    else:
        document["events"] = source()["events"]
    expect(code, lambda: build(document, algorithm=algorithm, correction_of=parent))


def test_serialization_only_changes_do_not_qualify_as_corrections():
    parent = build()
    document = source()
    document["idempotency_key"] = "new-key"
    document["events"].reverse()
    document["events"] += deepcopy(document["events"])
    expect("CORRECTION_REQUIRES_CHANGED_EVENTS", lambda: build(document, correction_of=parent))


def test_input_cardinality_and_history_bounds(monkeypatch):
    document = source()
    document["events"] = [directional_event() for _ in range(257)]
    expect("INVALID_SOURCE_CONTRACT", lambda: build(document))
    document = source()
    document["upstream_refs"] = [{"component": "A06", "evidence_ref": "synthetic", "raw_sha256": "1"*64}] * 33
    expect("INVALID_SOURCE_CONTRACT", lambda: build(document))
    ledger = AttributionLedger()
    ledger.append(build())
    before = ledger.to_bytes()
    monkeypatch.setattr("ai_commerce.affiliate_attribution.ledger.MAX_HISTORY_RECORDS", 1)
    expect("HISTORY_RECORD_LIMIT", lambda: ledger.append(correction(build())))
    assert ledger.to_bytes() == before
    monkeypatch.setattr("ai_commerce.affiliate_attribution.ledger.MAX_HISTORY_RECORDS", 128)
    monkeypatch.setattr("ai_commerce.affiliate_attribution.ledger.MAX_HISTORY_BYTES", len(before))
    expect("HISTORY_SIZE_LIMIT", lambda: ledger.append(correction(build())))
    assert ledger.to_bytes() == before
    expect("INPUT_SIZE_OR_TYPE_INVALID", lambda: AffiliateAttributionEvidence.from_bytes(b" "*(2*1024*1024+1)))


def test_ledger_detects_unlabelled_correction_across_overlapping_windows():
    ledger = AttributionLedger()
    ledger.append(build())
    before = ledger.to_bytes()
    document = source()
    document["idempotency_key"] = "other-key"
    document["scope"]["measurement_window"][0] = "2026-11-01T00:30:00Z"
    document["events"][-1]["reported_order_status"] = "SETTLED"
    expect("EVENT_ID_COLLISION", lambda: ledger.append(build(document)))
    assert ledger.to_bytes() == before
    # A renamed event cannot conceal reuse of the same order ID.
    for event in document["events"]:
        event["event_ref"] += "-other"
    expect("ORDER_ID_COLLISION", lambda: ledger.append(build(document)))
    assert ledger.to_bytes() == before


@pytest.mark.parametrize("field,value", [("provider_ref", "other-provider"),
    ("account_ref", {"namespace": "internal_alias", "id": "other-account"})])
def test_ids_are_not_globally_joined_between_provider_or_account(field, value):
    ledger = AttributionLedger()
    ledger.append(build())
    document = source()
    document["scope"][field] = value
    document["events"][-1]["reported_order_status"] = "SETTLED"
    assert not ledger.append(build(document)).duplicate
    assert len(ledger.active()) == 2
    # Removing that account's click cannot use a previous account's evidence.
    document["events"] = document["events"][2:]
    assert build(document).to_dict()["results"][0]["tier"] == "D"


def test_history_corruption_duplicates_and_reordering_fail():
    ledger = AttributionLedger()
    parent = build()
    ledger.append(parent)
    ledger.append(correction(parent))
    document = json.loads(ledger.to_bytes())
    bad = deepcopy(document)
    bad["records"].reverse()
    expect("CORRECTION_PARENT_MISSING", lambda: AttributionLedger.from_bytes(canonical_bytes(bad)))
    bad = deepcopy(document)
    bad["records"].append(bad["records"][0])
    expect("DUPLICATE_HISTORY_RECORD", lambda: AttributionLedger.from_bytes(canonical_bytes(bad)))
    bad = deepcopy(document)
    bad["records"][0]["source"]["raw_sha256"] = "2"*64
    expect("EVIDENCE_REPLAY_MISMATCH", lambda: AttributionLedger.from_bytes(canonical_bytes(bad)))
    expect("INVALID_HISTORY_CONTRACT", lambda: AttributionLedger.from_bytes(b'{"schema_version":"wrong","records":[]}'))


def test_cli_schema_evaluation_verification_and_bounded_error(monkeypatch):
    def invoke(args, raw=b""):
        stdout, stderr = io.BytesIO(), io.StringIO()
        monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
        monkeypatch.setattr("sys.stdout", SimpleNamespace(buffer=stdout))
        monkeypatch.setattr("sys.stderr", stderr)
        code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()
    code, raw, error = invoke(["schema", "--source"])
    assert code == 0 and not error
    assert json.loads(raw) == attribution_source_schema()
    code, raw, error = invoke(["evaluate", "--algorithm", "LAST_TOUCH"], canonical_bytes(source()))
    assert code == 0 and not error
    evidence = AffiliateAttributionEvidence.from_bytes(raw)
    code, verified, error = invoke(["verify", "--expected-id", evidence.evidence_id], raw)
    assert code == 0 and verified == raw and not error
    code, raw, error = invoke(["evaluate"], b'{"email":"private@example.invalid"}')
    assert code == 40 and raw == b"" and error == "INVALID_SOURCE_CONTRACT\n"
    code, raw, error = invoke(["evaluate"], b" "*262145)
    assert code == 40 and raw == b"" and error == "INPUT_SIZE_OR_TYPE_INVALID\n"
