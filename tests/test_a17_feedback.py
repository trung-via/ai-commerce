"""C10/E06 real A11 history, negative evidence, comparators and Human adoption."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.creative import brief_digest, intake_brief
from ai_commerce.creative.live import LiveCreativeReasoner
from ai_commerce.creative_scaling import (
    CreativeScalingError, consume_learning_advisory, create_variant_batch, load_proposal,
    propose_next_creative, record_adoption,
)
from ai_commerce.creative_scaling import schemas
from ai_commerce.creative_scaling.feedback import proposal_ref
from ai_commerce.creative_scaling.variants import source_pin
from ai_commerce.distribution import assisted
from ai_commerce.learning import (
    LearningAdvisory, LearningError, LearningRegistry, SnapshotPin, build_performance_report,
    export_advisory, read_snapshot, seal_contract,
)
from ai_commerce.learning.registry import hypothesis_context
from ai_commerce.learning.schemas import CONTRACT_IDS
from ai_commerce.media.fake_generation import FakeAsyncMediaProvider
from ai_commerce.operations import LocalMediaRunner
from ai_commerce.render import FFmpegRenderer

from test_a17_variants import file_bytes, master_case, offline, scope_fixture


def reseal(name, value, **changes):
    own = CONTRACT_IDS[name][0]
    return seal_contract(name, {**{k: v for k, v in value.items() if k != own}, **changes})


def seal_export(value):
    body = {k: v for k, v in value.items() if k != "export_id"}
    return {**body, "export_id": "a11-advisory-" + schemas.digest(body)}


def snapshot_case(tmp_path, *, source_kind="SYNTHETIC_FIXTURE", context_changes=None,
                  definition_version="fixture-1", denominator=None, name="metrics"):
    scope = scope_fixture()
    context = {k: scope[k] for k in ("platform", "surface", "measurement_window", "timezone", "cohort")}
    context["account_ref"] = {"namespace": "internal_alias", "id": "fixture-account"}
    context.update(context_changes or {})
    metric = {"schema_version": "MetricDefinition.v1", "metric_key": "plays", "provider_name": "Fixture plays",
              "definition_version": definition_version, "platform": context["platform"],
              "unit": "count" if denominator is None else "ratio", "definition": "A supplied descriptive count",
              "denominator": denominator, "cohort": context["cohort"],
              "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE", "source_status": VERIFICATION}
    plan = {"schema_version": "MeasurementPlan.v1", "plan_id": name, "campaign_ref": "fixture-campaign",
            "primary_metric": "plays", "denominator": denominator, "metric_definition_version": definition_version,
            **context, "source_mode": "MANUAL_IMPORT", "attribution_tier": "NOT_AVAILABLE",
            "missingness_rule": "Missing stays null", "source_status": VERIFICATION, "metric_definitions": [metric]}
    rows = [{"provider_post_id": f"fixture-post-{i}", "public_url": None, "metric_key": "plays",
             "provider_name": "Fixture plays", "definition_version": definition_version,
             "status": "NOT_AVAILABLE" if v is None else "AVAILABLE", "value": v}
            for i, v in enumerate((12, 0, -3, None))]
    raw = tmp_path / (name + ".json")
    raw.write_bytes(canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": rows}))
    ledger = AnalyticsLedger(tmp_path / (name + "-a09.sqlite"))
    imported = ledger.import_file(raw, plan=plan, profile="tiktok.manual.v1" if context["platform"] == "TIKTOK"
                                  else "youtube.manual.v1", file_format="json", source_kind=source_kind)
    record = imported["import"]
    pin = SnapshotPin(record["import_id"], record["context_sha256"], record["raw_sha256"],
                      tuple(o["observation_id"] for o in imported["observations"]))
    return read_snapshot(ledger.path, pin=pin), Path(ledger.path)


def learning_case(tmp_path, *, source_kind="SYNTHETIC_FIXTURE", target="VARIANT", context_changes=None,
                  denominator=None, denominator_value=None, with_report=False, mixed_metric=False):
    registry, master, edits = master_case(tmp_path, content_only=True)
    scope = scope_fixture()
    batch = create_variant_batch(master, edits, registry=registry, batch_id="learning", scope=scope)
    source_id = batch["master"]["master_id"] if target == "MASTER" else batch["variants"][0]["variant_id"]
    pin, _ = source_pin(batch, source_id)
    snapshot, a09_path = snapshot_case(tmp_path, source_kind=source_kind, context_changes=context_changes,
                                      denominator=denominator)
    hyp = seal_contract("LearningHypothesis", {
        "schema_version": "LearningHypothesis.v1", "source_status": VERIFICATION,
        **hypothesis_context(snapshot), "statement": "The quieter opening may be associated with a different count",
        "creative_ref": {"creative_id": source_id, "version": str(pin["revision"]), "content_sha256": pin["sha256"]},
        "expected_outcome": "Explore a descriptive difference", "uncertainty": "No controlled assignment",
        "confounders": [], "inference": "HYPOTHESIS_ONLY"})
    source_snapshots = [snapshot]
    report = build_performance_report(snapshot, denominator_value=denominator_value) if with_report else None
    observations = snapshot.to_dict()["observations"]
    # Use observed values rather than assuming a ledger-specific sort order.
    observations = [next(o for o in observations if o["value"] == v) for v in (12, 0, -3, None)]
    if mixed_metric:
        extra, _ = snapshot_case(tmp_path, name="other-definition", definition_version="fixture-2")
        source_snapshots.append(extra)
        observations[1] = next(o for o in extra.to_dict()["observations"] if o["value"] == 0)
    evidence = []
    for i, observation in enumerate(observations):
        source = source_snapshots[1] if mixed_metric and i == 1 else snapshot
        state = observation["status"] if report is None or observation["status"] == "NOT_AVAILABLE" else report.measurement["status"]
        role = "INCONCLUSIVE" if state != "AVAILABLE" else ("SUPPORTING" if i == 0 else "COUNTEREVIDENCE")
        evidence.append(seal_contract("LearningEvidence", {
            "schema_version": "LearningEvidence.v1", "source_status": VERIFICATION,
            "hypothesis_ref": {"hypothesis_id": hyp["hypothesis_id"]},
            "observation_ref": source.observation_ref(observation["observation_id"]),
            "report_ref": None if report is None else report.report_ref(), "role": role, "evidence_state": state,
            "observed_outcome": "Supplied observation including negative counterexamples",
            "confounders": [], "limitations": ["No causal attribution or provider verification"],
            "inference": "DESCRIPTIVE_ONLY"}))
    learning = LearningRegistry(tmp_path / "learning-a11.sqlite")
    previous = None
    for number, count in enumerate((1, 2, 4), 1):
        selected = evidence[:count]
        record = seal_contract("LearningRecord", {
            "schema_version": "LearningRecord.v1", "source_status": VERIFICATION, "learning_key": "quiet-opener",
            "hypothesis_ref": {"hypothesis_id": hyp["hypothesis_id"]},
            "evidence_refs": [{"evidence_id": e["evidence_id"]} for e in selected if e["role"] != "COUNTEREVIDENCE"],
            "counterevidence_refs": [{"evidence_id": e["evidence_id"]} for e in selected if e["role"] == "COUNTEREVIDENCE"],
            "status": "INCONCLUSIVE" if number == 3 or any(e["role"] == "INCONCLUSIVE" for e in selected)
                      else "SUPPORTED" if number == 1 else "MIXED",
            "uncertainty": "Sparse supplied data and uncontrolled creative assignment",
            "confounders": [{"code": "UNCONTROLLED", "description": "Uncontrolled audience exposure",
                             "status": "DECLARED", "evidence_refs": []}], "inference": "ADVISORY_ONLY"})
        revision = seal_contract("LearningRevision", {
            "schema_version": "LearningRevision.v1", "source_status": VERIFICATION, "learning_key": "quiet-opener",
            "revision": number, "previous_revision_ref": None if previous is None else {
                "revision_id": previous["revision"]["revision_id"], "revision": number - 1},
            "record_ref": {"record_id": record["record_id"]}, "operation": "CREATE" if number == 1 else "UPDATE",
            "reason": "Human retained positive and negative observations", "actor_ref": "fixture-human",
            "recorded_at": f"2026-10-30T0{number}:00:00Z"})
        previous = learning.append(hypothesis=hyp, evidence=selected, record=record, revision=revision,
                                   snapshots=source_snapshots, reports=[] if report is None else [report]).to_dict()
    advisory = export_advisory(learning)
    kwargs = {"registry": registry, "batch": batch, "source_id": source_id,
              "expected_export_id": advisory.export_id, "learning_key": "quiet-opener",
              "expected_revision": 3, "scope": scope, "opt_in": True}
    return advisory, kwargs, learning, a09_path


def proposal_args(advisory, consumer):
    metric = advisory.to_dict()["entries"][-1]["entry"]["observation_sources"][0]["metric"]
    return {**{k: v for k, v in consumer.items() if k != "batch"}, "advisory": advisory,
            "proposal_id": "next-brief", "suggestion": "Keep the quiet tone and explore a garden opening",
            "hypothesis": "A garden opening may be associated with a different descriptive count",
            "comparison": {"after_window": ["2026-10-03T00:00:00Z", "2026-10-05T00:00:00Z"],
                           "metric": metric, "cohort": "fixture-cohort", "timezone": "UTC"}}


def test_opt_in_consumer_pins_complete_a11_history_and_preserves_originals(tmp_path, monkeypatch):
    advisory, args, learning, a09 = learning_case(tmp_path)
    before = (advisory.to_bytes(), Path(learning.path).read_bytes(), a09.read_bytes(), file_bytes(args["registry"].root))
    monkeypatch.setattr(AnalyticsLedger, "_connect", lambda *a, **k: pytest.fail("consumer reopened A09"))
    result = consume_learning_advisory(advisory, **args)
    context = result["context"]
    assert context["event_count"] == 3 and [h["revision"]["revision"] for h in context["history"]] == [1, 2, 3]
    assert context["export_id"] == advisory.export_id and context["provider_metrics_verified"] is False
    assert context["causal_lift"] is None and context["trend"] == "NOT_ESTABLISHED"
    latest = [o for o in context["observations"] if o["revision"] == 3]
    assert sorted(o["value"] for o in latest if o["status"] == "AVAILABLE") == [-3, 0, 12]
    assert [o["value"] for o in latest if o["status"] == "UNKNOWN"] == [None]
    assert len([o for o in latest if o["evidence"]["role"] == "COUNTEREVIDENCE"]) == 2
    assert all(o["provenance"] == "SYNTHETIC_FIXTURE" for o in latest)
    assert context["history"][-1]["record"]["confounders"]
    assert context["market_basis"] == "USER_SUPPLIED_NOT_RECORDED_BY_A11"
    assert (advisory.to_bytes(), Path(learning.path).read_bytes(), a09.read_bytes(), file_bytes(args["registry"].root)) == before


@pytest.mark.parametrize("change", ["opt-in", "pin", "revision", "key", "variant", "master", "market", "account", "window",
                                   "surface", "cohort", "timezone"])
def test_wrong_scope_source_revision_or_missing_optin_is_rejected(tmp_path, change):
    advisory, args, _, _ = learning_case(tmp_path)
    if change == "opt-in": args["opt_in"] = False
    elif change == "pin": args["expected_export_id"] = "a11-advisory-" + "f" * 64
    elif change == "revision": args["expected_revision"] = 2
    elif change == "key": args["learning_key"] = "missing-key"
    elif change == "variant": args["source_id"] = args["batch"]["variants"][1]["variant_id"]
    elif change == "master": args["source_id"] = args["batch"]["master"]["master_id"]
    else:
        args["scope"] = deepcopy(args["scope"])
        key, value = {"market": ("market", "US"), "account": ("account_ref", {"namespace": "internal_alias", "id": "a11-account-" + "f" * 64}),
                      "window": ("measurement_window", ["2026-10-03T00:00:00Z", "2026-10-05T00:00:00Z"]),
                      "surface": ("surface", "PROFILE_VIDEO"), "cohort": ("cohort", "different-cohort"),
                      "timezone": ("timezone", "UTC+07:00")}[change]
        args["scope"][key] = value
    before = file_bytes(args["registry"].root)
    with pytest.raises(ValueError):
        consume_learning_advisory(advisory, **args)
    assert file_bytes(args["registry"].root) == before


@pytest.mark.parametrize("context", [{"cohort": "different-cohort"}, {"account_ref": {"namespace": "internal_alias", "id": "other-account"}},
    {"measurement_window": ["2026-10-03T00:00:00Z", "2026-10-05T00:00:00Z"]},
    {"platform": "YOUTUBE", "surface": "SHORT"}])
def test_valid_a11_archive_for_another_context_is_not_adopted(tmp_path, context):
    advisory, args, _, _ = learning_case(tmp_path, context_changes=context)
    with pytest.raises(CreativeScalingError, match="CONTEXT_MISMATCH"):
        consume_learning_advisory(advisory, **args)


def test_incompatible_metric_definition_versions_in_real_a11_history_fail_closed(tmp_path):
    advisory, args, _, _ = learning_case(tmp_path, mixed_metric=True)
    with pytest.raises(CreativeScalingError, match="INCOMPATIBLE_METRIC_DEFINITION"):
        consume_learning_advisory(advisory, **args)


@pytest.mark.parametrize("mutation", ["extra", "trust", "causal", "count", "head", "event", "sequence", "role",
                                     "counterevidence", "fact", "revision", "hash", "source-version"])
def test_fake_a11_advisory_rejected_even_with_rehashed_envelope(tmp_path, mutation):
    advisory, args, _, _ = learning_case(tmp_path)
    value = advisory.to_dict()
    event, entry = value["entries"][-1], value["entries"][-1]["entry"]
    if mutation == "extra": value["publish"] = True
    elif mutation == "trust": value["source_status"] = "PROVIDER_ATTESTED"
    elif mutation == "causal": value["inference"] = "CAUSAL_LIFT"
    elif mutation == "count": value["event_count"] = 2
    elif mutation == "head": value["head_event_hash"] = "f" * 64
    elif mutation == "event": event["event_hash"] = "f" * 64
    elif mutation == "sequence": event["sequence"] = True
    elif mutation == "role": next(e for e in entry["evidence"] if e["role"] == "COUNTEREVIDENCE")["role"] = "SUPPORTING"
    elif mutation == "counterevidence": entry["record"] = reseal("LearningRecord", entry["record"], counterevidence_refs=[])
    elif mutation == "fact": entry["observation_sources"][0]["value"] = 999
    elif mutation == "revision": entry["revision"]["revision"] = 7
    elif mutation == "hash": entry["hypothesis"]["creative_ref"]["content_sha256"] = "f" * 64
    elif mutation == "source-version": entry["observation_sources"][0]["reader_version"] = "a11-reader.v999"
    value = seal_export(value)
    args["expected_export_id"] = value["export_id"]
    with pytest.raises(ValueError):
        consume_learning_advisory(value, **args)


def test_retained_export_pin_rejects_valid_prefix_and_fake_typed_object(tmp_path):
    advisory, args, _, _ = learning_case(tmp_path)
    value = advisory.to_dict()
    value["entries"].pop()
    value["event_count"] = 2
    value["head_event_hash"] = value["entries"][-1]["event_hash"]
    prefix = LearningAdvisory(seal_export(value))  # A valid complete shorter archive by itself.
    with pytest.raises(LearningError, match="ADVISORY_PIN_MISMATCH"):
        consume_learning_advisory(prefix, **args)
    class Fake:
        export_id = advisory.export_id
        def to_bytes(self):
            return advisory.to_bytes()
    with pytest.raises(CreativeScalingError, match="A11_ADVISORY_REQUIRED"):
        consume_learning_advisory(Fake(), **args)


@pytest.mark.parametrize("raw", [b'{"export_id":1,"export_id":2}', b'{"value":NaN}', b'[]', b'null', b'not-json'])
def test_malformed_advisory_bytes_cannot_be_consumed(tmp_path, raw):
    _, args, _, _ = learning_case(tmp_path)
    with pytest.raises(ValueError):
        consume_learning_advisory(raw, **args)


def test_next_brief_is_pinned_reproducible_and_preserves_a03_constraints(tmp_path, monkeypatch):
    advisory, consumer, learning, a09 = learning_case(tmp_path)
    args, batch = proposal_args(advisory, consumer), consumer["batch"]
    source_bytes = (advisory.to_bytes(), Path(learning.path).read_bytes(), a09.read_bytes())
    def forbidden(*a, **k):
        pytest.fail("E06 attempted model, media, publish or retry")
    monkeypatch.setattr(FFmpegRenderer, "render", forbidden)
    monkeypatch.setattr(LocalMediaRunner, "import_file", forbidden)
    monkeypatch.setattr(LiveCreativeReasoner, "reason", forbidden)
    monkeypatch.setattr(assisted, "export_package", forbidden)
    for method in ("submit", "observe", "fetch", "reconcile"):
        monkeypatch.setattr(FakeAsyncMediaProvider, method, forbidden)
    proposal = propose_next_creative(batch, **args)
    assert proposal["source"]["id"] == consumer["source_id"] and proposal["master_id"] == batch["master"]["master_id"]
    assert proposal["source_brief"] == batch["master"]["brief"]
    assert proposal["advisory"]["export_id"] == advisory.export_id and proposal["advisory"]["revision"] == 3
    assert len(proposal["evidence_ids"]) == 4 and len(proposal["negative_evidence_ids"]) == 2
    assert proposal["adoption"] == {"decision": "PENDING", "actor": None, "note": None}
    assert proposal["generation_authorization"] == "REQUIRED_SEPARATELY" and proposal["generation_performed"] is False
    assert proposal["comparison"]["after"] == {"status": "UNKNOWN", "value": None, "denominator": None}
    assert proposal["comparison"]["causal_lift"] is None and proposal["comparison"]["trend"] == "NOT_ESTABLISHED"
    assert "BEFORE_EVIDENCE_UNKNOWN" in proposal["missing_data"]
    brief = intake_brief(json.loads(consumer["registry"].read(proposal["next_brief"]["ref"])))
    assert brief_digest(brief) == proposal["next_brief"]["sha256"]
    assert brief["product_ref"] is None and brief["custom_constraint"] == "Keep the quiet tone"
    assert "cta" not in brief and "storyboard" not in brief
    assert brief["brief_id"] != proposal["source_brief"]["id"] and consumer["source_id"] in brief["source_refs"]
    before = file_bytes(consumer["registry"].root)
    assert propose_next_creative(batch, **args) == proposal
    assert file_bytes(consumer["registry"].root) == before
    assert (advisory.to_bytes(), Path(learning.path).read_bytes(), a09.read_bytes()) == source_bytes
    args["suggestion"] = "Explore another quiet opening"
    with pytest.raises(CreativeScalingError, match="PROPOSAL_DIGEST_CONFLICT"):
        propose_next_creative(batch, **args)
    assert file_bytes(consumer["registry"].root) == before


@pytest.mark.parametrize("decision", ["ACCEPTED", "DECLINED"])
def test_human_adoption_is_a_new_immutable_revision_and_never_execution(tmp_path, decision):
    advisory, consumer, _, _ = learning_case(tmp_path)
    proposal = propose_next_creative(consumer["batch"], **proposal_args(advisory, consumer))
    registry = consumer["registry"]
    original_bytes = registry.read(proposal_ref(proposal["proposal_id"]))
    with pytest.raises(CreativeScalingError, match="AUTHORIZATION_REQUIRED"):
        record_adoption(proposal, decision, registry=registry)
    adopted = record_adoption(proposal, decision, registry=registry, human_authorized=True, note="Human reviewed the hypothesis")
    assert adopted["revision"] == 2 and adopted["adoption"]["decision"] == decision
    assert adopted["adoption"]["actor"] == "HUMAN" and adopted["parent"]["sha256"] == proposal["record_sha256"]
    assert adopted["next_brief"] == proposal["next_brief"] and adopted["generation_performed"] is False
    assert adopted["generation_authorization"] == "REQUIRED_SEPARATELY"
    assert registry.read(proposal_ref(proposal["proposal_id"])) == original_bytes
    assert record_adoption(proposal, decision, registry=registry, human_authorized=True,
                           note="Human reviewed the hypothesis") == adopted
    with pytest.raises(CreativeScalingError, match="ADOPTION_DIGEST_CONFLICT"):
        record_adoption(proposal, "DECLINED" if decision == "ACCEPTED" else "ACCEPTED", registry=registry, human_authorized=True)
    with pytest.raises(CreativeScalingError, match="ADOPTION_TERMINAL"):
        record_adoption(adopted, decision, registry=registry, human_authorized=True)


@pytest.mark.parametrize("mutation", ["overlap", "duration", "timezone", "cohort", "definition", "denominator", "extra",
                                     "causal", "provider", "pii", "optin", "cross-variant"])
def test_bad_proposals_fail_without_creating_artifacts(tmp_path, mutation):
    advisory, consumer, _, _ = learning_case(tmp_path)
    args = proposal_args(advisory, consumer)
    if mutation == "overlap": args["comparison"]["after_window"] = ["2026-10-02T00:00:00Z", "2026-10-04T00:00:00Z"]
    elif mutation == "duration": args["comparison"]["after_window"] = ["2026-10-03T00:00:00Z", "2026-10-06T00:00:00Z"]
    elif mutation == "timezone": args["comparison"]["timezone"] = "UTC+07:00"
    elif mutation == "cohort": args["comparison"]["cohort"] = "other-cohort"
    elif mutation == "definition": args["comparison"]["metric"]["definition_version"] = "fixture-2"
    elif mutation == "denominator": args["comparison"]["metric"]["denominator"] = "impressions"
    elif mutation == "extra": args["comparison"]["causal_lift"] = 0.15
    elif mutation == "causal": args["hypothesis"] = "Guaranteed causal lift of fifteen percent"
    elif mutation == "provider": args["hypothesis"] = "Provider-verified metrics prove this winner"
    elif mutation == "pii": args["suggestion"] = "Contact person@example.test"
    elif mutation == "optin": args["opt_in"] = False
    elif mutation == "cross-variant": args["source_id"] = consumer["batch"]["variants"][1]["variant_id"]
    before = file_bytes(consumer["registry"].root)
    with pytest.raises(ValueError):
        propose_next_creative(consumer["batch"], **args)
    assert file_bytes(consumer["registry"].root) == before


@pytest.mark.parametrize("mutation", ["negative", "source", "advisory", "next-brief", "zero", "provenance", "causal", "parent", "legacy"])
def test_resealed_or_legacy_proposal_corruption_is_not_accepted(tmp_path, mutation):
    advisory, consumer, _, _ = learning_case(tmp_path)
    proposal = propose_next_creative(consumer["batch"], **proposal_args(advisory, consumer))
    value = deepcopy(proposal)
    if mutation == "negative": value["negative_evidence_ids"] = []
    elif mutation == "source": value["source"]["id"] = consumer["batch"]["variants"][1]["variant_id"]
    elif mutation == "advisory": value["advisory"]["head_event_hash"] = "f" * 64
    elif mutation == "next-brief": value["next_brief"]["sha256"] = "f" * 64
    elif mutation == "zero": value["comparison"]["after"]["value"] = 0
    elif mutation == "provenance": value["comparison"]["before"][0]["provenance"] = "PROVIDER_ATTESTED"
    elif mutation == "causal": value["comparison"]["causal_lift"] = 0.1
    elif mutation == "parent": value["parent"] = proposal["source_brief"]
    elif mutation == "legacy": value = {"legacy": True}
    value = schemas.seal({k: v for k, v in value.items() if k != "record_sha256"})
    registry = consumer["registry"]
    registry.safe_path(proposal_ref(proposal["proposal_id"])).write_bytes(canonical_bytes(value))
    before = file_bytes(registry.root)
    with pytest.raises(ValueError):
        load_proposal(registry, proposal["proposal_id"])
    assert file_bytes(registry.root) == before


@pytest.mark.parametrize("denominator_value", [None, 0])
def test_declared_or_zero_denominator_stays_unknown_and_source_is_preserved(tmp_path, denominator_value):
    advisory, args, _, _ = learning_case(tmp_path, denominator="impressions", denominator_value=denominator_value, with_report=True)
    result = consume_learning_advisory(advisory, **args)["context"]
    latest = [o for o in result["observations"] if o["revision"] == 3]
    assert all(o["status"] == "UNKNOWN" and o["value"] is None for o in latest)
    assert all(o["denominator"] == denominator_value for o in latest)
    assert any(o["source_fact"]["value"] == 0 for o in latest)
    assert any(o["source_fact"]["value"] is None for o in latest)
    proposal = propose_next_creative(args["batch"], **proposal_args(advisory, args))
    assert proposal["comparison"]["metric"]["denominator"] == "impressions"
    if denominator_value is None:
        assert "DENOMINATOR_UNKNOWN" in proposal["missing_data"]


@pytest.mark.parametrize("kind,expected", [("USER_AUTHORED_MANUAL", "USER_SUPPLIED"), ("SYNTHETIC_FIXTURE", "SYNTHETIC_FIXTURE")])
def test_provenance_is_not_promoted_and_master_source_is_supported(tmp_path, kind, expected):
    advisory, args, _, _ = learning_case(tmp_path, source_kind=kind, target="MASTER")
    context = consume_learning_advisory(advisory, **args)["context"]
    assert context["source"]["kind"] == "MASTER"
    assert {o["provenance"] for o in context["observations"]} == {expected}
    assert {o["source_fact"]["source"]["source_kind"] for o in context["observations"]} == {kind}
    assert context["provider_metrics_verified"] is False


def test_feedback_schema_mirror_is_versioned_strict_and_accepts_actual_proposal(tmp_path):
    advisory, consumer, _, _ = learning_case(tmp_path)
    proposal = propose_next_creative(consumer["batch"], **proposal_args(advisory, consumer))
    path = Path(__file__).resolve().parents[1] / "contracts/CreativeFeedbackProposal.v1.schema.json"
    published = json.loads(path.read_text(encoding="utf-8"))
    assert published == schemas.schema(schemas.FEEDBACK_PROPOSAL_SCHEMA)
    validator = Draft202012Validator(published)
    validator.validate(proposal)
    for bad in ({}, {**proposal, "publish": True}, {**proposal, "schema_version": "CreativeFeedbackProposal.v2"}):
        assert not validator.is_valid(bad)


@pytest.mark.parametrize("mutation", ["accepted-initial", "parent-initial", "missing-actor", "unknown-zero", "promoted", "duplicate-negative"])
def test_standalone_feedback_schema_rejects_inconsistent_states(tmp_path, mutation):
    advisory, consumer, _, _ = learning_case(tmp_path)
    proposal = propose_next_creative(consumer["batch"], **proposal_args(advisory, consumer))
    if mutation == "accepted-initial": proposal["adoption"] = {"decision": "ACCEPTED", "actor": "HUMAN", "note": None}
    elif mutation == "parent-initial": proposal["parent"] = proposal["source_brief"]
    elif mutation == "missing-actor":
        proposal = record_adoption(proposal, "ACCEPTED", registry=consumer["registry"], human_authorized=True)
        proposal["adoption"]["actor"] = None
    elif mutation == "unknown-zero":
        next(v for v in proposal["comparison"]["before"] if v["status"] == "UNKNOWN")["value"] = 0
    elif mutation == "promoted": proposal["comparison"]["before"][0]["provenance"] = "PROVIDER_ATTESTED"
    elif mutation == "duplicate-negative": proposal["negative_evidence_ids"].append(proposal["negative_evidence_ids"][0])
    assert not Draft202012Validator(schemas.schema(schemas.FEEDBACK_PROPOSAL_SCHEMA)).is_valid(proposal)
