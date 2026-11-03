"""A17.D05.L07.offline-reconciliation-dashboard: real native source projections."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.affiliate_attribution import build_attribution_evidence
from ai_commerce.affiliate_attribution.ledger import AttributionLedger
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative_scaling import propose_next_creative, render_variant
from ai_commerce.distribution_intelligence import (AnalyticsSelection, AttributionSelection, CostSelection,
    CreativeSelection, DecisionDashboardSnapshot, FeedbackSelection, IntelligenceError, LearningSelection,
    MetricSelection, PublicationSelection, SettlementSelection, build_dashboard, inspect_sources, schema, verify_dashboard)
from ai_commerce.distribution_intelligence.schemas import bounded, digest
from ai_commerce.learning import SnapshotPin
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.cost_ledger import CostEntry, CostLedger
from ai_commerce.render import FFmpegRenderer
from ai_commerce.settlement import SettlementLedger, admit_settlement

from test_a17_reconciliation import append, make_case, observation, offline, scope_value, tree_bytes
from test_a17_variants import create_case
from test_a17_feedback import learning_case, proposal_args
from test_a16_settlement import source as settlement_source, attribution as settlement_attribution

ROOT = Path(__file__).resolve().parents[1]


def analytics_case(tmp_path, scope, *, post="fixture-post", source_kind="SYNTHETIC_FIXTURE"):
    definitions = [{"schema_version": "MetricDefinition.v1", "metric_key": key, "provider_name": "Fixture " + key,
        "definition_version": "fixture-v1", "platform": scope["platform"], "unit": "count", "definition": "Supplied " + key,
        "denominator": None, "cohort": scope["cohort"], "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE",
        "source_status": VERIFICATION} for key in ("reach", "engagement")]
    plan = {"schema_version": "MeasurementPlan.v1", "plan_id": "dashboard-plan", "campaign_ref": "dashboard-campaign",
        "primary_metric": "reach", "denominator": None, "metric_definition_version": "fixture-v1",
        **{k: scope[k] for k in ("platform", "surface", "account_ref", "measurement_window", "timezone", "cohort")},
        "source_mode": "MANUAL_IMPORT", "attribution_tier": "NOT_AVAILABLE", "missingness_rule": "Missing remains null",
        "source_status": VERIFICATION, "metric_definitions": definitions}
    rows = [{"provider_post_id": post, "public_url": None, "metric_key": d["metric_key"],
        "provider_name": d["provider_name"], "definition_version": "fixture-v1",
        "status": status, "value": value} for d, status, value in zip(definitions, ("AVAILABLE", "NOT_AVAILABLE"), (0, None))]
    path = tmp_path / "audience.json"
    path.write_bytes(canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": rows}))
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    result = ledger.import_file(path, plan=plan, profile="tiktok.manual.v1", file_format="json", source_kind=source_kind)
    record = result["import"]
    pin = SnapshotPin(record["import_id"], record["context_sha256"], record["raw_sha256"], tuple(o["observation_id"] for o in result["observations"]))
    return AnalyticsSelection(ledger.path, pin, tuple(MetricSelection(digest(d), stage) for d, stage in zip(definitions, ("REACH", "ENGAGEMENT"))))


def cost_case(tmp_path, *, duplicate=False, secret=False):
    registry = ArtifactRegistry(tmp_path / "costs")
    ledger = CostLedger(registry)
    entries = []
    specs = [("ESTIMATED", "0", "USD", "USER_SUPPLIED"), ("CHARGED", "2.30", "USD", "USER_SUPPLIED"),
             ("CHARGED", "1000", "VND", "SYNTHETIC_FIXTURE"), ("UNKNOWN", None, "USD", "UNVERIFIED")]
    for index, (status, amount, currency, origin) in enumerate(specs):
        proof_ref, proof_sha = None, None
        if status == "CHARGED":
            raw = b"rekeyed receipt" if duplicate else ("charge line " + str(index)).encode()
            proof_ref = f"receipts/line-{index}.json"
            proof_sha = sha256_bytes(raw)
            registry.put_immutable(proof_ref, raw)
        entry = CostEntry("cost-" + str(index), "job-" + str(index), 1, digest({"job": index}),
            "Bearer fixture-secret" if secret else "fixture-provider", None, status, amount, currency,
            proof_ref, proof_sha, origin, "2026-11-03T01:00:00Z")
        ledger.record(entry)
        entries.append({key: getattr(entry, key) for key in ("entry_id", "job_id", "attempt", "intent_digest", "provider")})
    return CostSelection(ledger, tuple(entries))


def commerce_case(tmp_path, monkeypatch, *, status="SETTLED", clicks_only=False):
    doc = settlement_source(status)
    doc["scope"]["measurement_window"] = ["2026-11-01T00:00:00Z", "2026-11-04T00:00:00Z"]
    # Native A16 requires a completed measurement window at capture time.
    doc["recorded_at"] = "2026-11-04T01:00:00Z"
    for receipt in doc["receipts"]:
        receipt["scope"] = deepcopy(doc["scope"])
    attr = settlement_attribution(doc, clicks_only=clicks_only)
    attr_ledger = AttributionLedger()
    attr_ledger.append(attr)
    native = doc["scope"]
    changes = {"provider_ref": native["provider_ref"], "channel": native["channel"], "listing_ref": native["listing_id"],
        "variant_ref": native["variant_id"], "link_ref": native["link_ref"], "link_url_sha256": native["link_url_sha256"],
        "creative_ref": native["creative_ref"], "creative_revision": 1, "creative_sha256": native["creative_sha256"]}
    args, store, _ = make_case(tmp_path, monkeypatch, receipt=True, product_ref=native["product_id"], scope_changes=changes)
    first = append(store, args)
    selection = PublicationSelection(**{k: args[k] for k in ("package_dir", "journal", "guard", "publication_id", "intent_id", "attempt")},
        source_sha256=inspect_sources(**args)["source_sha256"], reconciliation_ledger=store, reconciliation_id=first.record_id)
    attr_selection = AttributionSelection(attr_ledger, sha256_bytes(attr_ledger.to_bytes()), attr.evidence_id)
    settlement = None
    if not clicks_only:
        evidence = admit_settlement(canonical_bytes(doc), attribution=attr)
        ledger = SettlementLedger(ArtifactRegistry(tmp_path / "settlement"))
        ledger.append(evidence)
        settlement = SettlementSelection(ledger, sha256_bytes(ledger.to_bytes()))
    return args, selection, attr_selection, settlement


def test_missing_sources_are_unknown_null_and_product_null_is_legal(tmp_path):
    scope = scope_value(platform=None, surface=None, account_ref=None, market=None)
    before = tree_bytes(tmp_path)
    snapshot = build_dashboard(scope=scope)
    value = snapshot.to_dict()
    assert value["scope"]["product_ref"] is None
    assert value["creative"]["created"] == value["creative"]["mp4_generated"] == "UNKNOWN"
    assert value["publication"]["reported_state"] is None and value["publication"]["public_visibility"] == "UNKNOWN"
    assert value["audience"] == {"status": "NOT_AVAILABLE", "rows": []}
    assert value["costs"]["by_currency"] == [] and value["realized_profit"] is None
    assert value["affiliate"]["orders"] == [] and value["settlement"]["actual_settlement"] == "UNKNOWN"
    assert value["provider_live"] == "UNKNOWN" and value["read_only"]
    assert tree_bytes(tmp_path) == before
    assert build_dashboard(scope=scope).to_bytes() == snapshot.to_bytes()
    assert DecisionDashboardSnapshot.from_bytes(snapshot.to_bytes(), expected_id=snapshot.snapshot_id) == snapshot
    with pytest.raises(FrozenInstanceError):
        snapshot._raw = b"changed"


def test_native_a08_a09_a14_a15_a16_stages_are_separate_and_source_grounded(tmp_path, monkeypatch):
    args, publication, attribution, settlement = commerce_case(tmp_path, monkeypatch)
    analytics = analytics_case(tmp_path, args["scope"])
    costs = cost_case(tmp_path)
    inputs = {"scope": args["scope"], "publication": publication, "analytics": analytics,
              "attribution": attribution, "settlement": settlement, "costs": costs}
    before = tree_bytes(tmp_path)
    def forbidden(*a, **k):
        pytest.fail("read-only L07 attempted an immutable write, lock or provider render")
    monkeypatch.setattr(ArtifactRegistry, "put_immutable", forbidden)
    monkeypatch.setattr(ArtifactRegistry, "worker", forbidden)
    monkeypatch.setattr(FFmpegRenderer, "render", forbidden)
    monkeypatch.setattr(FFmpegRenderer, "load", forbidden)
    snapshot = build_dashboard(**inputs)
    value = snapshot.to_dict()
    assert value["publication"]["reported_state"] == "PUBLISHED_CONFIRMED"
    assert value["publication"]["public_visibility"] == "UNKNOWN"
    assert value["publication"]["reconciliation_status"] == "RECONCILE_REQUIRED"
    assert [(r["stage"], r["status"], r["value"], r["denominator_value"]) for r in value["audience"]["rows"]] == [
        ("REACH", "AVAILABLE", 0, None), ("ENGAGEMENT", "NOT_AVAILABLE", None, None)]
    assert len(value["affiliate"]["clicks"]) == 1 and value["affiliate"]["orders"][0]["attribution_tier"] == "A"
    assert value["affiliate"]["orders"][0]["reported_order_status"] == "PENDING"
    assert value["settlement"]["rows"][0]["reported_status"] == "SETTLED"
    assert value["settlement"]["rows"][0]["settled_commission"] == {"status": "REPORTED", "amount": "10.00", "currency": "USD"}
    assert not value["settlement"]["settlement_verified"] and value["settlement"]["actual_settlement"] == "UNKNOWN"
    assert value["costs"]["by_currency"] == [
        {"currency": "USD", "estimated": "0", "supplied_charged": "2.30", "synthetic_charged": None, "completeness": "SELECTED_ENTRIES_ONLY"},
        {"currency": "VND", "estimated": None, "supplied_charged": None, "synthetic_charged": "1000", "completeness": "SELECTED_ENTRIES_ONLY"}]
    assert value["realized_profit"] is value["causal_uplift"] is None
    assert {p["component"] for p in value["source_pins"]} == {"A08_A15", "A09", "A14", "A15", "A16"}
    assert "A09_MARKET_PRODUCT_CREATIVE_NOT_RECORDED" in value["limitations"]
    assert "A15_COST_ACCOUNT_CREATIVE_CHANNEL_NOT_RECORDED" in value["limitations"]
    assert verify_dashboard(snapshot, **inputs) is snapshot
    assert tree_bytes(tmp_path) == before


def test_click_only_never_creates_an_order_or_commission(tmp_path, monkeypatch):
    args, publication, attribution, _ = commerce_case(tmp_path, monkeypatch, clicks_only=True)
    value = build_dashboard(scope=args["scope"], publication=publication, attribution=attribution).to_dict()
    assert len(value["affiliate"]["clicks"]) == 1 and value["affiliate"]["orders"] == []
    assert value["settlement"]["rows"] == [] and value["settlement"]["actual_settlement"] == "UNKNOWN"


@pytest.mark.parametrize("status", ["CREATED", "APPROVED", "PENDING", "PARTIALLY_SETTLED", "REFUNDED", "REVOKED"])
def test_native_order_state_does_not_imply_settled_commission(tmp_path, monkeypatch, status):
    args, publication, attribution, settlement = commerce_case(tmp_path, monkeypatch, status=status)
    value = build_dashboard(scope=args["scope"], publication=publication, attribution=attribution, settlement=settlement).to_dict()
    row = value["settlement"]["rows"][0]
    assert row["reported_status"] == status
    assert value["settlement"]["actual_settlement"] == "UNKNOWN"
    if status == "PARTIALLY_SETTLED":
        assert row["settled_commission"]["amount"] == "4.00"
    else:
        assert row["settled_commission"]["amount"] is None


def test_real_a17_a11_history_and_e06_are_read_only_advisory_not_uplift(tmp_path, monkeypatch):
    advisory, native, learning_registry, a09_path = learning_case(tmp_path)
    batch, registry = native["batch"], native["registry"]
    source = next(v for v in batch["variants"] if v["variant_id"] == native["source_id"])
    scope = scope_value(creative_ref=source["variant_id"], creative_revision=1, creative_sha256=source["composition"]["sha256"],
        measurement_window=native["scope"]["measurement_window"], cohort=native["scope"]["cohort"])
    creative = CreativeSelection(registry, batch["batch_id"], batch["record_sha256"], source["variant_id"])
    learning = LearningSelection(advisory, advisory.export_id, native["learning_key"], native["expected_revision"])
    proposal = propose_next_creative(batch, **proposal_args(advisory, native))
    feedback = FeedbackSelection(proposal["proposal_id"], 1, proposal["record_sha256"])
    before = tree_bytes(tmp_path)
    monkeypatch.setattr(ArtifactRegistry, "put_immutable", lambda *a, **k: pytest.fail("L07 wrote A17 custody"))
    snapshot = build_dashboard(scope=scope, creative=creative, learning=learning, feedback=feedback)
    value = snapshot.to_dict()
    assert value["creative"]["created"] == "RECORDED" and value["creative"]["mp4_generated"] == "NOT_AVAILABLE"
    assert value["creative"]["feedback"]["adoption_decision"] == "PENDING"
    assert value["learning"]["status"] == "ADVISORY_ONLY" and len(value["learning"]["history"]) == 3
    latest = [o for o in value["learning"]["observations"] if o["revision"] == 3]
    assert sorted(o["value"] for o in latest if o["status"] == "AVAILABLE") == [-3, 0, 12]
    assert [o["value"] for o in latest if o["status"] == "UNKNOWN"] == [None]
    assert len([o for o in latest if o["role"] == "COUNTEREVIDENCE"]) == 2
    assert value["learning"]["history"][-1]["confounders"]
    assert value["learning"]["history"][-1]["uncertainty"]
    assert value["learning"]["causal_lift"] is None and value["creative"]["feedback"]["after_status"] == "UNKNOWN"
    assert {p["component"] for p in value["source_pins"]} == {"A17_1", "A11", "E06"}
    assert tree_bytes(tmp_path) == before
    assert learning_registry.path and a09_path.is_file()


def test_real_native_render_receipt_and_mp4_hash_without_decoder_writes(tmp_path, monkeypatch):
    registry, _, _, batch = create_case(tmp_path)
    source = batch["variants"][0]
    rendered = render_variant(batch, source["variant_id"], registry=registry, human_authorized=True)
    assert rendered["state"] == "SUCCEEDED" and rendered["full_decode_verified"]
    scope = scope_value(creative_ref=source["variant_id"], creative_revision=1,
        creative_sha256=source["composition"]["sha256"], content_sha256=rendered["output"]["sha256"],
        measurement_window=batch["scope"]["measurement_window"], cohort=batch["scope"]["cohort"])
    selection = CreativeSelection(registry, batch["batch_id"], batch["record_sha256"], source["variant_id"], rendered["record_sha256"])
    before = tree_bytes(tmp_path)
    monkeypatch.setattr(ArtifactRegistry, "put_immutable", lambda *a, **k: pytest.fail("projection wrote files"))
    monkeypatch.setattr(FFmpegRenderer, "load", lambda *a, **k: pytest.fail("projection invoked scratch-file decoder"))
    monkeypatch.setattr(FFmpegRenderer, "render", lambda *a, **k: pytest.fail("projection rendered"))
    value = build_dashboard(scope=scope, creative=selection).to_dict()
    assert value["creative"]["mp4_generated"] == "RECORDED_LOCAL"
    assert value["creative"]["content_sha256"] == rendered["output"]["sha256"]
    assert value["publication"]["reported_state"] is None
    assert "MP4_HASH_REOPENED_DECODE_NOT_RERUN" in value["limitations"]
    assert tree_bytes(tmp_path) == before
    registry.safe_path(rendered["output"]["ref"]).write_bytes(b"tampered MP4")
    with pytest.raises(IntelligenceError, match="RENDER_ARTIFACT_HASH_MISMATCH"):
        build_dashboard(scope=scope, creative=selection)


@pytest.mark.parametrize("field,value", [("account_ref", {"namespace": "internal_alias", "id": "wrong"}),
    ("platform", "YOUTUBE"), ("surface", "PROFILE_VIDEO"), ("market", "US"), ("channel", "other"),
    ("product_ref", "other"), ("listing_ref", None), ("variant_ref", "red"), ("link_ref", "other"),
    ("creative_sha256", "f" * 64), ("content_sha256", "f" * 64),
    ("measurement_window", ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"])])
def test_cross_account_platform_market_window_channel_content_joins_fail(tmp_path, monkeypatch, field, value):
    args, publication, attribution, settlement = commerce_case(tmp_path, monkeypatch)
    scope = {**args["scope"], field: value}
    before = tree_bytes(tmp_path)
    with pytest.raises(ValueError):
        build_dashboard(scope=scope, publication=publication, attribution=attribution, settlement=settlement)
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize("mutation", ["raw-pin", "observation-pin", "definition", "role", "post", "window", "cohort"])
def test_a09_source_metric_and_context_pins_fail_closed(tmp_path, monkeypatch, mutation):
    args, publication, _, _ = commerce_case(tmp_path, monkeypatch)
    scope = args["scope"]
    selected = analytics_case(tmp_path, scope, post="wrong-post" if mutation == "post" else "fixture-post")
    if mutation == "raw-pin": selected = replace(selected, pin=replace(selected.pin, raw_sha256="f" * 64))
    elif mutation == "observation-pin": selected = replace(selected, pin=replace(selected.pin, observation_ids=selected.pin.observation_ids[::-1]))
    elif mutation == "definition": selected = replace(selected, metrics=(MetricSelection("f" * 64, "REACH"), selected.metrics[1]))
    elif mutation == "role": selected = replace(selected, metrics=(MetricSelection(selected.metrics[0].definition_sha256, "ORDERS"), selected.metrics[1]))
    elif mutation == "window": scope = {**scope, "measurement_window": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"]}
    elif mutation == "cohort": scope = {**scope, "cohort": "another-cohort"}
    with pytest.raises(ValueError):
        build_dashboard(scope=scope, publication=publication, analytics=selected)


@pytest.mark.parametrize("mutation", ["duplicate", "receipt-corrupt", "identity", "stale", "secret", "duplicate-id", "float-version"])
def test_a15_costs_reopened_without_rekey_double_counting_or_trust_promotion(tmp_path, mutation):
    selected = cost_case(tmp_path, duplicate=mutation == "duplicate", secret=mutation == "secret")
    if mutation == "receipt-corrupt": selected.ledger.registry.safe_path("receipts/line-1.json").write_bytes(b"corrupt")
    elif mutation == "identity": selected = replace(selected, selections=({**selected.selections[0], "job_id": "swapped"}, *selected.selections[1:]))
    elif mutation == "duplicate-id": selected = replace(selected, selections=(*selected.selections, selected.selections[0]))
    elif mutation == "float-version":
        ref = selected.ledger.ref(selected.selections[0]["entry_id"])
        record = json.loads(selected.ledger.registry.read(ref))
        record["version"] = 1.0
        record["record_sha256"] = digest({k: v for k, v in record.items() if k != "record_sha256"})
        selected.ledger.registry.safe_path(ref).write_bytes(bounded(record))
    scope = scope_value()
    if mutation == "stale": scope["measurement_window"] = ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"]
    before = tree_bytes(tmp_path)
    with pytest.raises(ValueError):
        build_dashboard(scope=scope, costs=selected)
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize("source", ["publication", "attribution", "settlement"])
def test_stale_native_source_pins_never_silently_refresh(tmp_path, monkeypatch, source):
    args, publication, attribution, settlement = commerce_case(tmp_path, monkeypatch)
    if source == "publication": publication = replace(publication, source_sha256="f" * 64)
    elif source == "attribution": attribution = replace(attribution, history_sha256="f" * 64)
    else: settlement = replace(settlement, history_sha256="f" * 64)
    with pytest.raises(ValueError):
        build_dashboard(scope=args["scope"], publication=publication, attribution=attribution, settlement=settlement)


def test_a11_stale_revision_fake_export_and_feedback_wrong_source_rejected(tmp_path):
    advisory, native, _, _ = learning_case(tmp_path)
    batch = native["batch"]
    source = batch["variants"][0]
    scope = scope_value(creative_ref=source["variant_id"], creative_revision=1, creative_sha256=source["composition"]["sha256"],
        measurement_window=batch["scope"]["measurement_window"], cohort=batch["scope"]["cohort"])
    creative = CreativeSelection(native["registry"], batch["batch_id"], batch["record_sha256"], source["variant_id"])
    for learning in (LearningSelection(advisory, advisory.export_id, native["learning_key"], 2),
                     LearningSelection(advisory, "a11-advisory-" + "f" * 64, native["learning_key"], 3)):
        with pytest.raises(ValueError):
            build_dashboard(scope=scope, creative=creative, learning=learning)
    proposal = propose_next_creative(batch, **proposal_args(advisory, native))
    with pytest.raises(ValueError):
        build_dashboard(scope=scope, creative=creative, feedback=FeedbackSelection(proposal["proposal_id"], 1, "f" * 64))


@pytest.mark.parametrize("field,value", [("provider_live", "VERIFIED"), ("network_calls_performed", True), ("read_only", False),
    ("realized_profit", "100"), ("causal_uplift", 1), ("extra_post_permission", True)])
def test_contract_cannot_claim_offline_effects_attestation_profit_or_lift(field, value):
    original = build_dashboard(scope=scope_value()).to_dict()
    body = {k: v for k, v in original.items() if k not in {"record_sha256", "snapshot_id"}}
    body[field] = value
    sha = digest(body)
    with pytest.raises(ValueError):
        DecisionDashboardSnapshot.from_bytes(bounded({**body, "record_sha256": sha, "snapshot_id": "a17-dashboard-" + sha}))


def test_schema_mirror_bounded_exact_json_and_no_input_alias_mutation():
    assert json.loads((ROOT / "contracts/DecisionDashboardSnapshot.v1.schema.json").read_bytes()) == schema("DecisionDashboardSnapshot.v1")
    Draft202012Validator.check_schema(schema("DecisionDashboardSnapshot.v1"))
    scope = scope_value()
    original = deepcopy(scope)
    result = build_dashboard(scope=scope)
    result.to_dict()["scope"]["market"] = "US"
    assert scope == original and result.to_dict()["scope"]["market"] == "VN"
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{}' * (3 * 1024 * 1024)):
        with pytest.raises(ValueError):
            DecisionDashboardSnapshot.from_bytes(raw)
    for changed in ({**scope, "account_ref": {"namespace": "internal_alias", "id": "person@example.test"}},
                    {**scope, "cohort": "Bearer fixture-token"}, {**scope, "api_key": "secret"}):
        with pytest.raises(ValueError):
            build_dashboard(scope=changed)
