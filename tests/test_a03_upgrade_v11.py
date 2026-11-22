"""A03 v1.1 contract tests. All structured-client responses are explicit fakes."""

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

import pytest

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import live as creative_live
from ai_commerce.creative import v2 as creative_v2
from ai_commerce.creative import (
    A03CreativeJob, A03CreativeStageResult, AIInferenceBinding, AIReasoningTask,
    ActorDecision, ActorRef, AntigravityCliCreativeClient, AntigravityCliPreflight,
    CodexCliCapability, CodexCliCreativeClient,
    ConceptDraft, ConceptRecord, ConceptSelection, CreativeJobProvenance,
    CreativeIntent, CreativeSidecarStore, CreativeStore, CreativeValidationError,
    HumanSelectionEvent, IntentField, InvocationAuthorization, LiveCreativeReasoner,
    MockCreativeReasoner, ModelConfiguration, ProviderObservation, ReasoningRequest,
    ReasoningResult, StructuredModelRequest, StructuredModelResponse, UsageObservation,
    VersionConflict, brief_digest, capture_intent, codex_creative_argv, generate_concepts, intake_brief,
    reason_v2, revise_brief, revise_concept, run_creative_stage,
    A03CreativeCycleJob, A03CreativeCycleResult, CreativeCycleContext,
    CreativeEvidenceSource, CreativeModelSelection, run_creative_cycle,
    A03CreativeProductionJob, A03CreativeProductionPackage, A03CreativeProductionResult,
    AudioPlan, CameraSpec, CreativeProductionContext, IdentityContinuity,
    MediaOutputTarget, ProductionLineage, ProductionPackageParent, ProductionPackageSpec,
    ProductionScene, QAExpectation, RightsAttribution, SceneTransition, ScriptSegment,
    revise_production_package, run_creative_production,
    A03BenchmarkCase, A03BenchmarkComparison, A03BenchmarkObservation,
    BENCHMARK_RUBRIC_VERSION, BENCHMARK_STRATEGIES,
    BenchmarkConsumerEvidence, BenchmarkJudgeReceipt, BenchmarkNSMScore,
    BenchmarkRoleTrace, BenchmarkRubricScores, BenchmarkStructuralCheck, BenchmarkTelemetry,
    NSM_V1_VERSION, NSM_V1_WEIGHTS,
    aggregate_benchmark_telemetry, calculate_nsm_v1_score, compare_benchmark_observations,
    evaluate_benchmark_structure, observe_benchmark_run,
    assess_brain_cdp_turn, select_reasoning_provider, verified_model_transport,
)
from ai_commerce.providers.base import ProviderCapabilities, ProviderError
from ai_commerce.wakehub_cdp import authenticated_turn as wakehub_turn
from ai_commerce.creative.v2 import (
    A03BenchmarkExperimentPlan, BenchmarkLiveTrialGrant, BenchmarkResearchTarget, BenchmarkTrialBudget,
    BenchmarkTrialGrant,
    advance_benchmark_trial, compare_benchmark_trials,
)

TEXT = "  Mưa Sài Gòn 🌧️ e\u0301 — ba biến thể rất gần nhau, không CTA hay sản phẩm.\n"
MOCK = ProviderObservation("deterministic-test-only", "UNKNOWN", "TEST_ONLY_MOCK")


def variants(count=3):
    # Only title changes. Same family, premise and visual mechanism are deliberate.
    return tuple(ConceptDraft("rain-poem", f"Mưa {i}", "Giọt mưa đếm nhịp.", "Cận cảnh một giọt mưa.")
                 for i in range(count))


def payload(drafts=(), edits=None, critique=None):
    return json.dumps({"drafts": [d.to_dict() for d in drafts],
                       "edits": {} if edits is None else edits, "critique": critique}, ensure_ascii=False)


class FakeClient:
    capabilities = ProviderCapabilities("structured-test-only", ("concept",), False, False)
    execution_mode = "TEST_ONLY_MOCK"

    def __init__(self, response=None):
        self.response = response if response is not None else StructuredModelResponse(
            "OK", payload(variants()), observation=MOCK, request_id="fake:request-1",
        )
        self.calls = []

    def complete(self, request):
        assert isinstance(request, StructuredModelRequest)
        self.calls.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def adapter(client=None, **options):
    return LiveCreativeReasoner(ModelConfiguration("requested-provider", "requested-model"),
                               client if client is not None else FakeClient(), **options)


def explore(**options):
    return ReasoningRequest.create(TEXT, mode="controlled_variants", **options)


@pytest.fixture
def saved(tmp_path):
    store = CreativeStore(tmp_path)
    brief = intake_brief(TEXT)
    store.save_brief(brief)
    concept = generate_concepts(brief, MockCreativeReasoner())[0]
    store.save_concept(concept)
    selection = store.select(concept.concept_id, 1)
    return store, CreativeSidecarStore(store), brief, concept, selection


def save_fake_inference(sidecars, concept):
    """Execute an injected fake matching this fixture; never real-model proof."""
    draft = ConceptDraft(concept.family, concept.title, concept.premise, concept.visual_direction,
                         concept.source_refs, concept.assumptions)
    request = ReasoningRequest.create(sidecars.creative.load_brief(concept.source_brief_digest), count=1)
    result = reason_v2(request, adapter(FakeClient(StructuredModelResponse(
        "OK", payload((draft,)), observation=MOCK, request_id="fake:fixture-inference"))))
    assert result.concepts() == (concept,)
    return sidecars.save_result(result)


def test_historical_v1_digest_golden_values_and_mock_labels(tmp_path):
    # Values observed from unedited source b88bf37 before v1.1 implementation.
    brief = intake_brief("Mưa Sài Gòn 🌧️ e\u0301")
    record = generate_concepts(brief, MockCreativeReasoner())[0]
    store = CreativeStore(tmp_path)
    store.save_brief(brief)
    store.save_concept(record)
    selection = store.select(record.concept_id, 1)
    assert brief_digest(brief) == "cd587324573b34d5d380f2fc052af5636b66b966b454aa6a88deb53a791c5201"
    assert record.concept_id == "concept:9bdf510427dc0de9e1f9f69d84b1fa0faa77b0c4525b1306a179576586cfd13e"
    assert record.digest == "5ac3559ec4f3f57ce4808070d69d00dd4be7db934b53bf4fbe5c01ddd7f90f7b"
    assert selection.selection_id == "selection:ac7eb5e385210355e8acf01e08ed05251467b011a1bff03c86da5a442ca16b9f"
    assert record.observation == MOCK
    assert ConceptRecord.from_json(record.to_json()) == record
    assert ConceptSelection.from_dict(selection.to_dict()) == selection
    assert not (tmp_path / "sidecars").exists()


@pytest.mark.parametrize("raw", [TEXT, "夜の街", "حكاية مطر", " e\u0301 é 🦋\n"])
def test_intent_preserves_exact_unicode_and_unknowns(raw):
    intent = capture_intent(raw)
    assert intent.raw_text == raw
    assert intent.raw_text.encode("utf-8") == raw.encode("utf-8")
    assert intent.actor == ActorRef() and intent.observation == ProviderObservation()
    assert intent.fields == ()
    assert CreativeIntent.from_json(intent.to_json()) == intent
    assert intake_brief(raw)["product_ref"] is None
    assert not {"sku", "cta", "storyboard", "quality_gate"} & intent.to_dict().keys()


def test_intent_source_hypothesis_unknown_are_separate_and_detached(saved):
    store, sidecars, brief, _, _ = saved
    before = canonical_bytes(brief)
    fields = [IntentField("constraint", "không CTA", "SOURCE", "không CTA"),
              IntentField("audience", "người thích mưa", "INFERRED"), IntentField("duration")]
    intent = capture_intent(TEXT, fields=fields)
    fields.clear()
    assert len(intent.fields) == 3
    assert intent.fields[2].value is None and intent.fields[2].origin == "UNKNOWN"
    assert intent.fields[1].origin == "INFERRED" and intent.fields[1].source_quote is None
    digest = sidecars.save_intent(intent)
    assert CreativeSidecarStore(store.root).load_intent(digest) == intent
    assert canonical_bytes(store.load_brief(brief_digest(brief))) == before
    detached = intent.to_dict()
    detached["fields"][0]["origin"] = "INFERRED"
    assert intent.fields[0].origin == "SOURCE"
    with pytest.raises(FrozenInstanceError):
        intent.raw_text = "changed"


def test_json_intake_keeps_original_format_in_optional_intent():
    raw = '{ "brief_id": "b", "objective": "Mưa", "product_ref": null }\n'
    assert capture_intent(raw).raw_text == raw
    assert capture_intent(intake_brief(raw), raw_text=raw).raw_text == raw


@pytest.mark.parametrize("field", [
    {"name": "x", "value": "invented", "origin": "UNKNOWN"},
    {"name": "x", "value": "x", "origin": "SOURCE"},
    {"name": "x", "value": "x", "origin": "INFERRED", "source_quote": "x"},
    {"name": "", "origin": "UNKNOWN"},
    {"name": "x", "value": "x", "origin": "VERIFIED"},
])
def test_invalid_intent_field_claims_are_rejected(field):
    with pytest.raises(CreativeValidationError):
        IntentField(**field)


def test_source_quote_must_exist_and_duplicate_fields_rejected():
    with pytest.raises(CreativeValidationError, match="verbatim"):
        capture_intent(TEXT, fields=(IntentField("claim", "yes", "SOURCE", "not present"),))
    with pytest.raises(CreativeValidationError, match="duplicate"):
        capture_intent(TEXT, fields=(IntentField("x"), IntentField("x")))


def test_intent_revisions_pin_entire_source_and_reject_stale_context(saved):
    store, sidecars, brief, concept, _ = saved
    first = capture_intent(TEXT)
    sidecars.save_intent(first)
    changed = revise_brief(brief, {"audience": "bạn thích mưa"})
    store.save_brief(changed)
    second = capture_intent(changed, raw_text=TEXT, parent_intent_digest=first.digest)
    assert sidecars.save_intent(second) != first.digest
    assert sidecars.load_intent(first.digest).source_brief_digest == concept.source_brief_digest
    assert sidecars.load_intent(second.digest).source_brief_digest == brief_digest(changed)
    with pytest.raises(CreativeValidationError, match="revision mismatch"):
        ReasoningRequest.create(changed, intent=first)
    with pytest.raises(CreativeValidationError, match="revision mismatch"):
        ReasoningRequest.create(changed, operation="DEVELOP", source=concept)


def test_intent_storage_rejects_dangling_or_rebound_parent(saved):
    store, sidecars, _, _, _ = saved
    with pytest.raises(FileNotFoundError):
        sidecars.save_intent(capture_intent(TEXT, parent_intent_digest="0" * 64))
    parent = capture_intent("Một brief khác")
    store.save_brief(intake_brief(parent.raw_text))
    sidecars.save_intent(parent)
    with pytest.raises(CreativeValidationError, match="rebind"):
        sidecars.save_intent(capture_intent(TEXT, parent_intent_digest=parent.digest))


def test_three_near_identical_controlled_variants_are_accepted():
    client = FakeClient()
    request = explore(count=3)
    result = reason_v2(request, adapter(client))
    assert result.state == "OK" and len(result.drafts) == 3
    concepts = result.concepts()
    assert len({c.concept_id for c in concepts}) == 3
    assert len({c.family for c in concepts}) == len({c.premise for c in concepts}) == 1
    assert all(c.observation == MOCK and c.factual_status == "UNVERIFIED"
               and c.rights_status == "UNKNOWN" for c in concepts)
    assert len(client.calls) == 1
    assert client.calls[0].config.model == "requested-model"
    assert result.observation.model == "UNKNOWN"  # Configured target is not observed inference.
    assert result.usage == UsageObservation()
    assert result.provider_request_id == "fake:request-1"
    assert ReasoningResult.from_dict(result.to_dict()) == result


def test_divergence_is_a_distinct_opt_in_mode_and_not_a_quality_gate():
    request = ReasoningRequest.create(TEXT, mode="diverge", count=3)
    result = reason_v2(request, adapter())
    assert result.state == "ERROR" and result.error_code == "INVALID_STRUCTURED_OUTPUT"
    drafts = tuple(ConceptDraft(f"f{i}", "T", f"P{i}", f"V{i}") for i in range(7))
    client = FakeClient(StructuredModelResponse("OK", payload(drafts), observation=MOCK))
    result = reason_v2(ReasoningRequest.create(TEXT), adapter(client))
    assert result.request.count is None and len(result.concepts()) == 7


@pytest.mark.parametrize("metadata_only", [False, True])
def test_exact_repeated_output_cannot_hide_behind_metadata(metadata_only):
    first = variants(1)[0]
    second = replace(first, assumptions=("a different hypothesis",)) if metadata_only else first
    client = FakeClient(StructuredModelResponse("OK", payload((first, second)), observation=MOCK))
    result = reason_v2(explore(), adapter(client))
    assert result.state == "ERROR" and result.error_code == "INVALID_STRUCTURED_OUTPUT"
    assert not result.drafts


@pytest.mark.parametrize("options", [
    {"count": 0}, {"count": True}, {"count": "3"}, {"mode": "quality_rank"},
    {"operation": "PUBLISH"}, {"lens": "orphan"}, {"operation": "DEVELOP"},
])
def test_invalid_requests_rejected_before_a_client_can_be_called(options):
    with pytest.raises(CreativeValidationError):
        ReasoningRequest.create(TEXT, **options)


def test_no_model_and_missing_client_have_explicit_states():
    request = explore()
    for reasoner in (None, LiveCreativeReasoner(),
                     LiveCreativeReasoner(ModelConfiguration("p", "m")),
                     LiveCreativeReasoner(client=FakeClient())):
        result = reason_v2(request, reasoner)
        assert result.state == result.error_code == "NO_MODEL_CONFIGURED"
        assert not result.drafts and result.usage.status == "UNKNOWN"
        with pytest.raises(CreativeValidationError):
            result.concepts()


@pytest.mark.parametrize("network,spend", [(True, False), (False, True), (True, True)])
def test_live_capabilities_are_unauthorized_by_default_and_never_called(network, spend):
    client = FakeClient()
    client.capabilities = ProviderCapabilities("fake-live-capabilities", ("concept",), network, spend)
    result = reason_v2(explore(), adapter(client))
    assert result.state == result.error_code == "UNAUTHORIZED"
    assert client.calls == [] and result.observation == ProviderObservation()
    assert result.usage.status == "UNKNOWN"


def test_explicit_authorization_only_exercises_the_fake_client():
    client = FakeClient()
    client.capabilities = ProviderCapabilities("authorized-fake", ("concept",), True, True)
    # This is synthetic test authorization and performs no HTTP or paid request.
    auth = InvocationAuthorization(True, True, "test:synthetic-authorization")
    result = reason_v2(explore(), adapter(client, authorization=auth))
    assert result.state == "OK" and len(client.calls) == 1
    assert result.observation.mode == "TEST_ONLY_MOCK"
    with pytest.raises(CreativeValidationError):
        InvocationAuthorization(True, True)
    with pytest.raises(CreativeValidationError):
        InvocationAuthorization(1, False, "test:invalid")


def test_v1_offline_policy_still_rejects_live_seam_even_with_authorization():
    client = FakeClient()
    reasoner = adapter(client, authorization=InvocationAuthorization(True, True, "test:only"))
    with pytest.raises(ProviderError) as exc:
        generate_concepts(TEXT, reasoner)
    assert exc.value.error_code == "OFFLINE_ONLY" and client.calls == []
    with pytest.raises(ProviderError):
        generate_concepts(TEXT, type("V1", (), {
            "capabilities": ProviderCapabilities("offline", ("concept",), False, False),
            "observation": MOCK, "generate": lambda self, brief, count=1: variants(count),
        })(), count=3)


@pytest.mark.parametrize("bad", [
    None, {}, '{"drafts":', '[]',
    '{"drafts":[],"edits":{},"critique":null,"score":99}',
    '{"drafts":[],"drafts":[],"edits":{},"critique":null}',
    '{"drafts":[],"edits":{},"critique":NaN}',
    payload(), payload(variants(), {"factual_status": "VERIFIED"}),
    payload((replace(variants(1)[0], source_refs=("invented:source",)),)),
    payload(variants(), critique="compulsory QA"),
    '{"drafts":[{"family":"f","title":"t","premise":"p","visual_direction":"v"}],"edits":{},"critique":null}',
])
def test_malformed_model_output_is_not_success(bad):
    if bad is None or isinstance(bad, dict):
        client = FakeClient()
        client.response = bad
        expected = "INVALID_MODEL_RESPONSE"
    else:
        client = FakeClient(StructuredModelResponse("OK", bad, observation=MOCK))
        expected = "INVALID_STRUCTURED_OUTPUT"
    result = reason_v2(explore(), adapter(client))
    assert result.state == "ERROR" and result.error_code == expected
    assert not result.drafts and len(client.calls) == 1


@pytest.mark.parametrize("state,code", [("REFUSED", "MODEL_REFUSED"), ("ERROR", "MODEL_ERROR")])
def test_refusal_and_reported_model_error_keep_observed_fake_usage(state, code):
    usage = UsageObservation("OBSERVED", input_tokens=12, output_tokens=0)
    client = FakeClient(StructuredModelResponse(state, observation=MOCK, usage=usage,
                                                request_id="fake:failed-call"))
    result = reason_v2(explore(), adapter(client))
    assert result.state == state and result.error_code == code
    assert result.usage == usage and result.usage.cost_amount is None
    assert result.provider_request_id == "fake:failed-call" and not result.drafts
    assert len(client.calls) == 1


def test_exception_is_model_error_unknown_usage_no_retry_or_secret_echo():
    client = FakeClient(RuntimeError("secret-looking text must not be echoed"))
    result = reason_v2(explore(), adapter(client))
    assert result.state == "ERROR" and result.error_code == "MODEL_ERROR"
    assert result.usage == UsageObservation() and result.provider_request_id is None
    assert "secret-looking" not in json.dumps(result.to_dict())
    assert len(client.calls) == 1


def test_fake_cannot_relabel_itself_as_model_inference():
    claimed = ProviderObservation("claimed-provider", "claimed-model", "MODEL")
    client = FakeClient(StructuredModelResponse("OK", payload(variants()), observation=claimed))
    result = reason_v2(explore(), adapter(client))
    assert result.state == "ERROR" and result.error_code == "INVALID_MODEL_RESPONSE"


def test_observed_fake_usage_is_retained_and_unknown_cost_is_not_zero():
    usage = UsageObservation("OBSERVED", input_tokens=9, output_tokens=7, cost_amount=0.25, currency="USD")
    response = StructuredModelResponse("OK", payload(variants()), observation=MOCK, usage=usage)
    result = reason_v2(explore(), adapter(FakeClient(response)))
    assert result.usage.to_dict() == usage.to_dict()
    assert UsageObservation().to_dict()["cost_amount"] is None


@pytest.mark.parametrize("options", [
    {"input_tokens": 0}, {"status": "OBSERVED"}, {"status": "OBSERVED", "output_tokens": True},
    {"status": "OBSERVED", "input_tokens": -1},
    {"status": "OBSERVED", "cost_amount": float("nan"), "currency": "USD"},
    {"status": "OBSERVED", "cost_amount": 0.1}, {"currency": "USD"},
])
def test_usage_cannot_invent_observations_or_zero_cost(options):
    with pytest.raises(CreativeValidationError):
        UsageObservation(**options)


@pytest.mark.parametrize("operation", ["REFRAME", "DEVELOP"])
def test_model_edits_are_proposals_until_explicit_save_and_keep_original_observation(saved, operation):
    store, sidecars, brief, original, selected = saved
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*.json")}
    request = ReasoningRequest.create(brief, operation=operation, source=original,
                                     **({"lens": "Điểm nhìn chiếc ô"} if operation == "REFRAME" else {}))
    editing_mock = ProviderObservation("editing-test-only", "UNKNOWN", "TEST_ONLY_MOCK")
    client = FakeClient(StructuredModelResponse("OK", payload(edits={"premise": "Chiếc ô nhớ cơn mưa."}),
                                                observation=editing_mock))
    result = reason_v2(request, adapter(client))
    assert result.state == "OK"
    proposed = result.proposed_revision()
    assert proposed.revision == 2 and proposed.parent.digest == original.digest
    assert proposed.observation == original.observation != result.observation
    assert proposed.source_brief_digest == original.source_brief_digest
    assert proposed.operation == ("REFRAME" if operation == "REFRAME" else "REVISE")
    assert proposed.factual_status == "UNVERIFIED" and proposed.rights_status == "UNKNOWN"
    assert before == {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*.json")}
    result_digest = sidecars.save_result(result)
    assert not store._concept_path(original.concept_id, 2).exists()
    assert sidecars.load_result(result_digest) == result
    assert sidecars.save_revision(result) == proposed
    assert sidecars.save_revision(result) == proposed
    assert store.load_concept(original.concept_id, 1) == original
    assert store.load_selected(selected.selection_id)["concept"] == original.to_dict()
    for relative, content in before.items():
        assert (store.root / relative).read_bytes() == content
    assert CreativeSidecarStore(store.root).load_result(result_digest).request.operation == operation


def test_critique_is_optional_and_has_no_selection_or_revision_side_effect(saved):
    store, _, brief, concept, _ = saved
    request = ReasoningRequest.create(brief, operation="CRITIQUE", source=concept)
    response = StructuredModelResponse("OK", payload(critique="Có thể thử nhịp chậm hơn."), observation=MOCK)
    result = reason_v2(request, adapter(FakeClient(response)))
    assert result.state == "OK" and result.critique
    assert not list(store.root.rglob("r2.json"))
    with pytest.raises(CreativeValidationError):
        result.proposed_revision()


def test_conflicting_saved_proposals_fail_without_overwriting_parent_or_child(saved):
    store, sidecars, brief, concept, _ = saved
    request = ReasoningRequest.create(brief, operation="DEVELOP", source=concept)
    results = [reason_v2(request, adapter(FakeClient(StructuredModelResponse(
        "OK", payload(edits={"title": title}), observation=MOCK,
    )))) for title in ("First", "Second")]
    first = sidecars.save_revision(results[0])
    with pytest.raises(VersionConflict):
        sidecars.save_revision(results[1])
    assert store.load_concept(concept.concept_id, 2) == first
    assert sidecars.load_result(results[1].digest) == results[1]  # Observation, not applied-edit claim.


def test_request_detaches_custom_json_and_cannot_be_rebound_by_reasoner():
    brief = {**intake_brief(TEXT), "aesthetic": {"colors": ["đỏ"]}}
    request = ReasoningRequest.create(brief)
    brief["aesthetic"]["colors"].clear()
    request.brief["aesthetic"]["colors"].clear()
    assert request.brief["aesthetic"]["colors"] == ["đỏ"]

    class Rebound:
        def reason(self, req):
            return ReasoningResult(ReasoningRequest.create("unrelated"), "OK", (variants(1)[0],))

    result = reason_v2(request, Rebound())
    assert result.state == "ERROR" and result.error_code == "INVALID_REASONER_RESULT"


def test_auto_ai_and_human_decisions_are_distinct_immutable_sidecars(saved):
    store, sidecars, _, concept, selection = saved
    selection_bytes = canonical_bytes(selection.to_dict())
    receipt = save_fake_inference(sidecars, concept)
    auto = sidecars.decide_from_result(selection.selection_id, receipt)
    ai = sidecars.decide_from_result(selection.selection_id, receipt, kind="AI_RECOMMENDED")
    assert auto.inference.mode == ai.inference.mode == "TEST_ONLY_MOCK"
    event = HumanSelectionEvent("human:event-1", "human:tester", selection.selection_id,
                                "2026-10-29T12:00:00+07:00")
    human = ActorDecision(selection.selection_id, "HUMAN_SELECTED", ActorRef("HUMAN", "human:tester"),
                          event.event_ref, event)
    ids = {sidecars.save_decision(d) for d in (auto, ai, human)}
    assert len(ids) == 3
    for decision in (auto, ai, human):
        assert sidecars.load_decision(decision.decision_id) == decision
        assert sidecars.save_decision(decision) == decision.decision_id
    assert canonical_bytes(store.load_selection(selection.selection_id).to_dict()) == selection_bytes
    assert store.load_concept(concept.concept_id, 1) == concept
    with pytest.raises(FrozenInstanceError):
        human.kind = "AUTO_PINNED"


@pytest.mark.parametrize("event", [None, True, "cli:saved-selection"])
def test_human_selection_requires_typed_actual_event_not_pin_or_boolean(saved, event):
    _, _, _, _, selected = saved
    with pytest.raises(CreativeValidationError, match="actual Human selection event"):
        ActorDecision(selected.selection_id, "HUMAN_SELECTED", ActorRef("HUMAN", "human:tester"),
                      "event:human", event)


def test_human_event_actor_selection_and_reference_must_match(saved):
    _, _, _, _, selected = saved
    event = HumanSelectionEvent("event:h", "human:a", selected.selection_id, "2026-10-29T00:00:00Z")
    for actor, selection, ref in (("human:b", selected.selection_id, event.event_ref),
                                  ("human:a", "selection:" + "0" * 64, event.event_ref),
                                  ("human:a", selected.selection_id, "event:other")):
        with pytest.raises(CreativeValidationError, match="does not match"):
            ActorDecision(selection, "HUMAN_SELECTED", ActorRef("HUMAN", actor), ref, event)
    with pytest.raises(CreativeValidationError):
        ActorDecision(selected.selection_id, "AUTO_PINNED", ActorRef("HUMAN", "human:a"), event.event_ref)
    with pytest.raises(CreativeValidationError):
        HumanSelectionEvent("event:h", "human:a", selected.selection_id, "2026-10-29T00:00:00")


@pytest.mark.parametrize("kind", ["intent", "decision", "result"])
def test_sidecar_tamper_and_conflicting_bytes_are_detected(saved, kind):
    _, sidecars, _, concept, selected = saved
    if kind == "intent":
        record = capture_intent(TEXT)
        identifier = sidecars.save_intent(record)
        target = sidecars._path("intents", identifier)
        load, save = sidecars.load_intent, sidecars.save_intent
    elif kind == "decision":
        receipt = save_fake_inference(sidecars, concept)
        record = sidecars.decide_from_result(selected.selection_id, receipt)
        identifier = sidecars.save_decision(record)
        target = sidecars._path("decisions", identifier[9:])
        load, save = sidecars.load_decision, sidecars.save_decision
    else:
        record = reason_v2(explore(), adapter())
        identifier = sidecars.save_result(record)
        target = sidecars._path("reasoning", identifier)
        load, save = sidecars.load_result, sidecars.save_result
    data = json.loads(target.read_text(encoding="utf-8"))
    if kind == "intent":
        data["raw_text"] += "tampered"
    elif kind == "decision":
        data["evidence_ref"] += "tampered"
    else:
        data["provider_request_id"] = "tampered"
    target.write_bytes(canonical_bytes(data))
    with pytest.raises(CreativeValidationError, match="digest mismatch"):
        load(identifier)
    with pytest.raises(VersionConflict):
        save(record)


@pytest.mark.parametrize("identifier", ["../escape", "0" * 63, "G" * 64])
def test_sidecar_paths_reject_untrusted_identifiers(tmp_path, identifier):
    sidecars = CreativeSidecarStore(tmp_path)
    with pytest.raises(CreativeValidationError):
        sidecars.load_intent(identifier)
    with pytest.raises(CreativeValidationError):
        sidecars.load_decision(identifier)


@pytest.mark.parametrize("caps", [
    None, ProviderCapabilities("x", ("concept",), 0, False),
    ProviderCapabilities("x", ("concept",), False, None),
    ProviderCapabilities("x", "concept", False, False),
    ProviderCapabilities("x", (), False, False),
])
def test_invalid_client_capabilities_never_reach_completion(caps):
    client = FakeClient()
    client.capabilities = caps
    result = reason_v2(explore(), adapter(client))
    assert result.state == "ERROR" and result.error_code == "INVALID_CLIENT"
    assert client.calls == []


def test_requested_model_config_is_persisted_separately_from_fake_observation(saved):
    _, sidecars, _, _, _ = saved
    result = reason_v2(explore(), adapter())
    digest = sidecars.save_result(result)
    reopened = sidecars.load_result(digest)
    assert reopened.configuration == ModelConfiguration("requested-provider", "requested-model")
    assert reopened.observation == MOCK
    assert reopened.observation.model == "UNKNOWN"
    assert reopened.configuration.model != reopened.observation.model


def test_strict_schema_is_available_to_injected_client_and_detached():
    from jsonschema import Draft202012Validator

    client = FakeClient()
    assert reason_v2(explore(), adapter(client)).state == "OK"
    request = client.calls[0]
    schema = request.output_schema
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(client.response.output_json))
    schema["properties"].clear()
    assert request.output_schema["properties"]


def test_explicit_count_is_validated_and_failure_retains_reported_fake_usage():
    usage = UsageObservation("OBSERVED", input_tokens=20)
    client = FakeClient(StructuredModelResponse("OK", payload(variants()), observation=MOCK,
                                                usage=usage, request_id="fake:wrong-count"))
    result = reason_v2(explore(count=2), adapter(client))
    assert result.state == "ERROR" and result.error_code == "INVALID_STRUCTURED_OUTPUT"
    assert result.usage == usage and result.provider_request_id == "fake:wrong-count"
    assert result.observation == MOCK and result.drafts == ()


def test_v2_source_refs_remain_unverified_and_optional_intent_is_bound():
    brief = {**intake_brief(TEXT), "source_refs": ["note:assertion"], "claim": "unverified"}
    intent = capture_intent(brief, fields=(IntentField("claim", "maybe", "INFERRED"),))
    draft = replace(variants(1)[0], source_refs=("note:assertion",))
    response = StructuredModelResponse("OK", payload((draft,)), observation=MOCK)
    result = reason_v2(ReasoningRequest.create(brief, intent=intent), adapter(FakeClient(response)))
    record = result.concepts()[0]
    assert record.source_refs == ("note:assertion",)
    assert record.factual_status == "UNVERIFIED" and record.rights_status == "UNKNOWN"
    assert result.request.intent == intent


@pytest.mark.parametrize("edits", [
    {}, {"factual_status": "VERIFIED"}, {"source_refs": ["fake"]}, {"observation": "MODEL"},
    {"title": None}, {"title": " "},
])
def test_reframe_rejects_invalid_and_provenance_edits(saved, edits):
    _, _, brief, concept, _ = saved
    request = ReasoningRequest.create(brief, operation="REFRAME", source=concept, lens="Một góc nhìn")
    result = reason_v2(request, adapter(FakeClient(StructuredModelResponse(
        "OK", payload(edits=edits), observation=MOCK,
    ))))
    assert result.state == "ERROR" and result.error_code == "INVALID_STRUCTURED_OUTPUT"


def test_sidecar_persistence_checks_actual_stored_source_revision(saved):
    store, sidecars, brief, concept, _ = saved
    saved_child = revise_concept(concept, {"title": "saved"})
    store.save_concept(saved_child)
    conflicting_child = revise_concept(concept, {"title": "not the saved revision"})
    request = ReasoningRequest.create(brief, operation="DEVELOP", source=conflicting_child)
    result = reason_v2(request, adapter(FakeClient(StructuredModelResponse(
        "OK", payload(edits={"title": "a proposed next child"}), observation=MOCK,
    ))))
    assert result.state == "OK"
    with pytest.raises(CreativeValidationError, match="source revision mismatch"):
        sidecars.save_result(result)
    assert not store._concept_path(concept.concept_id, 3).exists()


def test_decision_cannot_reference_unsaved_selection(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path)
    decision = ActorDecision("selection:" + "1" * 64, "AUTO_PINNED",
                             ActorRef("SYSTEM", "cli:p"), "event:p")
    with pytest.raises(FileNotFoundError):
        sidecars.save_decision(decision)
    assert not sidecars.root.exists()


def test_result_rejects_success_payload_in_error_and_promoted_facts():
    request = explore()
    with pytest.raises(CreativeValidationError):
        ReasoningResult(request, "ERROR", drafts=variants(), error_code="X", message="error")
    data = reason_v2(request, adapter()).to_dict()
    data["drafts"][0]["factual_status"] = "VERIFIED"
    with pytest.raises(CreativeValidationError):
        ReasoningResult.from_dict(data)


def test_invocation_snapshot_keeps_requested_configuration_during_callback(saved):
    _, sidecars, _, _, _ = saved

    class ReconfiguringFake(FakeClient):
        def complete(self, request):
            reasoner.config = ModelConfiguration("later-provider", "later-model")
            return super().complete(request)

    client = ReconfiguringFake()
    reasoner = adapter(client)
    result = reason_v2(explore(), reasoner)
    assert result.state == "OK"
    assert result.configuration == client.calls[0].config == ModelConfiguration("requested-provider", "requested-model")
    assert reasoner.config != result.configuration
    digest = sidecars.save_result(result)
    assert sidecars.load_result(digest).configuration == client.calls[0].config


def test_invocation_snapshot_does_not_switch_client_after_capability_check():
    replacement = FakeClient()
    replacement.capabilities = ProviderCapabilities("fake-paid-replacement", ("concept",), True, True)

    class SwappingFake(FakeClient):
        @property
        def capabilities(self):
            reasoner.client = replacement
            return ProviderCapabilities("original-offline-fake", ("concept",), False, False)

    original = SwappingFake()
    reasoner = adapter(original)
    result = reason_v2(explore(), reasoner)
    assert result.state == "OK"
    assert len(original.calls) == 1 and replacement.calls == []
    assert reasoner.client is replacement


def test_invocation_snapshot_does_not_adopt_authorization_from_callback():
    class ChangingAuthorizationFake(FakeClient):
        @property
        def capabilities(self):
            reasoner.authorization = InvocationAuthorization(True, True, "test:later-authorization")
            return ProviderCapabilities("fake-live", ("concept",), True, True)

    client = ChangingAuthorizationFake()
    reasoner = adapter(client)
    result = reason_v2(explore(), reasoner)
    assert result.state == "UNAUTHORIZED" and client.calls == []


def creative_job(request=None, **options):
    request = explore(count=3) if request is None else request
    return A03CreativeJob.create(request, **{
        "journey_id": "test:journey", "cycle_id": "test:cycle", "job_id": "test:creative",
        "attempt": 1, "correlation_id": "test:correlation",
        "provenance": CreativeJobProvenance("test:caller", "test:brief", brief_digest(request.brief),
                                            "SYNTHETIC_FIXTURE"),
        "configuration": ModelConfiguration("requested-provider", "requested-model"),
        "purpose": "TEST_ONLY_MOCK", "selection_index": 1, **options,
    })


def test_stage_invokes_fake_saves_exact_lineage_without_human_or_product(tmp_path):
    usage = UsageObservation("OBSERVED", input_tokens=21, output_tokens=13)
    client = FakeClient(StructuredModelResponse("OK", payload(variants()), observation=MOCK,
                                                usage=usage, request_id="fake:stage-call"))
    config = ModelConfiguration("requested-provider", "requested-model", effort="requested-high")
    job = creative_job(configuration=config)
    stage = run_creative_stage(job, CreativeStore(tmp_path), LiveCreativeReasoner(config, client))
    assert stage.status == "TEST_ONLY_MOCK" and stage.failure is None
    assert len(client.calls) == 1 and client.calls[0].creative == job.request
    assert client.calls[0].config == job.configuration == config
    assert client.calls[0].to_dict()["config"]["effort"] == "requested-high"
    assert stage.reasoning.configuration == config
    assert stage.reasoning.observation == MOCK and stage.reasoning.usage == usage
    assert stage.reasoning.provider_request_id == "fake:stage-call"
    assert stage.job.request.brief["product_ref"] is None
    assert A03CreativeJob.from_dict(job.to_dict()) == job
    assert A03CreativeStageResult.from_dict(stage.to_dict()) == stage
    sidecars = CreativeSidecarStore(tmp_path)
    assert sidecars.load_stage(job) == stage
    assert sidecars.load_result(stage.reasoning_ref.removeprefix("reasoning:")) == stage.reasoning
    assert stage.reasoning_ref == "reasoning:" + stage.reasoning.digest
    assert job.source_brief_digest == brief_digest(job.request.brief)
    assert job.request_digest == stage.reasoning.request.digest
    assert len(stage.outputs) == 3 and len({s.concept_digest for s in stage.outputs}) == 3
    for output, concept in zip(stage.outputs, stage.reasoning.concepts(), strict=True):
        assert output.concept_digest == concept.digest
        assert output.source_brief_digest == job.source_brief_digest
        assert sidecars.creative.load_concept(output.concept_id, output.revision) == concept
        assert sidecars.creative.load_selection(output.selection_id) == output
    decision = stage.decision
    assert decision.kind == "AUTO_PINNED" and decision.human_event is None
    assert decision.selection_id == stage.outputs[1].selection_id
    assert decision.evidence_ref == stage.reasoning_ref
    assert decision.inference == AIInferenceBinding(stage.reasoning.digest, job.request_digest,
        job.source_brief_digest, stage.outputs[1].concept_digest, "TEST_ONLY_MOCK")
    assert sidecars.verify_inference_decision(decision) == stage.reasoning
    recommended = sidecars.decide_from_result(decision.selection_id, stage.reasoning.digest, kind="AI_RECOMMENDED")
    assert recommended.actor == ActorRef("AI", stage.reasoning_ref)
    assert recommended.inference.mode == "TEST_ONLY_MOCK"  # Never live model proof.
    assert sidecars.load_decision(recommended.decision_id) == recommended
    assert not list(tmp_path.rglob("*human*"))


def test_stage_completed_job_is_idempotent_and_rebinding_requires_new_attempt(tmp_path):
    job, client = creative_job(), FakeClient()
    store = CreativeSidecarStore(tmp_path)
    first = run_creative_stage(job, store, adapter(client))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    # A completed receipt is independently verified; no provider is needed again.
    assert run_creative_stage(job, CreativeStore(tmp_path)) == first
    assert len(client.calls) == 1 and before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    for rebound in (replace(job, correlation_id="other"), replace(job, purpose="PRODUCTION"),
                    replace(job, configuration=ModelConfiguration("other", "model"))):
        with pytest.raises(VersionConflict, match="already bound"):
            run_creative_stage(rebound, store, adapter(client))
    assert len(client.calls) == 1
    second = run_creative_stage(replace(job, attempt=2), store, adapter(client))
    assert second.job.attempt == 2 and second.job.slot != first.job.slot and len(client.calls) == 2


def test_reserved_job_without_terminal_receipt_never_replays(tmp_path):
    job = creative_job()
    store, client = CreativeSidecarStore(tmp_path), FakeClient()
    store.creative.save_brief(job.request.brief)
    assert store.claim_job(job)
    with pytest.raises(CreativeValidationError, match="UNRESOLVED_CREATIVE_JOB"):
        run_creative_stage(job, store, adapter(client))
    assert client.calls == []


@pytest.mark.parametrize("reasoner", [None, LiveCreativeReasoner(),
    LiveCreativeReasoner(ModelConfiguration("requested-provider", "requested-model"))])
def test_stage_brief_only_is_intake_complete_never_creative_complete(tmp_path, reasoner):
    stage = run_creative_stage(creative_job(), CreativeStore(tmp_path), reasoner)
    assert stage.status == "INTAKE_COMPLETE" and stage.failure == "NO_MODEL_CONFIGURED"
    assert stage.outputs == () and stage.decision is None
    assert stage.reasoning.observation == ProviderObservation()
    assert stage.reasoning.usage == UsageObservation()
    assert CreativeSidecarStore(tmp_path).load_stage(stage.job) == stage


def test_stage_mock_cannot_be_production_completion_or_relabelled(tmp_path):
    client = FakeClient()
    stage = run_creative_stage(creative_job(purpose="PRODUCTION"), CreativeStore(tmp_path), adapter(client))
    assert stage.status == "FAILED" and stage.failure == "MOCK_NOT_PRODUCTION"
    assert stage.reasoning.state == "OK" and stage.reasoning.observation.mode == "TEST_ONLY_MOCK"
    assert not stage.outputs and not list((tmp_path / "concepts").rglob("*.json"))
    data = stage.to_dict()
    data.update(status="CREATIVE_COMPLETE", failure=None)
    with pytest.raises(CreativeValidationError, match="status/failure"):
        A03CreativeStageResult.from_dict(data)


@pytest.mark.parametrize("state", ["REFUSED", "ERROR"])
def test_stage_refusal_and_error_are_saved_without_fallback_or_retry(tmp_path, state):
    client = FakeClient(StructuredModelResponse(state, observation=MOCK, request_id="fake:refused",
                        usage=UsageObservation("OBSERVED", input_tokens=8, output_tokens=0)))
    stage = run_creative_stage(creative_job(), CreativeStore(tmp_path), adapter(client))
    assert stage.status == "FAILED" and stage.reasoning.state == state
    assert stage.failure == ("MODEL_REFUSED" if state == "REFUSED" else "MODEL_ERROR")
    assert stage.reasoning.usage.input_tokens == 8 and stage.outputs == () and stage.decision is None
    assert len(client.calls) == 1
    assert run_creative_stage(stage.job, CreativeStore(tmp_path), adapter(client)) == stage
    assert len(client.calls) == 1


def test_stage_does_not_authorize_live_network_or_spend_even_with_adapter_grant(tmp_path):
    client = FakeClient()
    client.capabilities = ProviderCapabilities("fake-live-capabilities", ("concept",), True, True)
    reasoner = adapter(client, authorization=InvocationAuthorization(True, True, "test:live-path-only"))
    stage = run_creative_stage(creative_job(), CreativeStore(tmp_path), reasoner)
    assert stage.status == "FAILED" and stage.failure == "UNAUTHORIZED" and client.calls == []


def test_stage_validates_requested_configuration_before_adapter_invocation(tmp_path):
    client = FakeClient()
    mismatched = LiveCreativeReasoner(ModelConfiguration("unrequested-provider", "unrequested-model"), client)
    stage = run_creative_stage(creative_job(), CreativeStore(tmp_path), mismatched)
    assert stage.status == "FAILED" and stage.failure == "CONFIGURATION_MISMATCH" and client.calls == []
    # Missing job configuration also cannot borrow a model from the adapter.
    job = creative_job(job_id="unconfigured", configuration=None)
    stage = run_creative_stage(job, CreativeStore(tmp_path), adapter(client))
    assert stage.status == "INTAKE_COMPLETE" and stage.failure == "NO_MODEL_CONFIGURED" and client.calls == []


def test_stage_accepts_injected_v2_reasoner_and_rejects_unbound_identity(tmp_path):
    class FakeReasonerV2:
        calls = 0

        def reason(self, request):
            self.calls += 1
            return ReasoningResult(request, "OK", variants(), observation=MOCK,
                configuration=ModelConfiguration("requested-provider", "requested-model"),
                provider_request_id="fake:direct-v2")

    fake = FakeReasonerV2()
    stage = run_creative_stage(creative_job(selection_index=None), CreativeStore(tmp_path), fake)
    assert stage.status == "TEST_ONLY_MOCK" and stage.decision is None and fake.calls == 1


@pytest.mark.parametrize("observation,config,request_id,expected", [
    (ProviderObservation(), ModelConfiguration("requested-provider", "requested-model"), "fake:id", "UNBOUND_PROVIDER"),
    (MOCK, None, "fake:id", "NO_MODEL_CONFIGURED"),
    (MOCK, ModelConfiguration("other", "model"), "fake:id", "CONFIGURATION_MISMATCH"),
    (MOCK, ModelConfiguration("requested-provider", "requested-model"), None, "UNBOUND_PROVIDER"),
    (ProviderObservation("different-provider", "different-model", "MODEL"),
        ModelConfiguration("requested-provider", "requested-model"), "fake:forged-model", "PROVIDER_IDENTITY_MISMATCH"),
])
def test_stage_invalid_identity_never_commits_creative_outputs(tmp_path, observation, config, request_id, expected):
    class ForgedIdentityFake:
        """Negative fixture only: MODEL labels here are forged, never real-model proof."""
        calls = 0

        def reason(self, request):
            self.calls += 1
            return ReasoningResult(request, "OK", variants(), observation=observation,
                                   configuration=config, provider_request_id=request_id)

    fake = ForgedIdentityFake()
    stage = run_creative_stage(creative_job(), CreativeStore(tmp_path), fake)
    assert stage.status == "FAILED" and stage.failure == expected and fake.calls == 1
    assert stage.outputs == () and stage.decision is None
    assert not list((tmp_path / "concepts").rglob("*.json"))


@pytest.mark.parametrize("changes", [
    {"schema_version": "A03CreativeJob.v99"}, {"attempt": True}, {"attempt": 0}, {"job_id": " "},
    {"source_brief_digest": "0" * 64}, {"brief_id": "wrong"}, {"request_digest": "0" * 64},
    {"purpose": "AUTO_APPROVED"}, {"selection_index": True}, {"selection_index": -1}, {"selection_index": 3},
])
def test_invalid_stage_job_rejected_before_invocation(changes):
    with pytest.raises(CreativeValidationError):
        replace(creative_job(), **changes)


@pytest.mark.parametrize("record_type", [A03CreativeJob, A03CreativeStageResult])
def test_stage_wire_records_reject_unknown_missing_and_duplicate_json_fields(tmp_path, record_type):
    stage = run_creative_stage(creative_job(), CreativeStore(tmp_path), adapter())
    data = stage.job.to_dict() if record_type is A03CreativeJob else stage.to_dict()
    for bad in ({**data, "unknown": True}, {k: v for k, v in data.items() if k != "schema_version"}):
        with pytest.raises(CreativeValidationError):
            record_type.from_dict(bad)
    from ai_commerce.creative._validation import read_json
    raw = canonical_bytes(data).decode("utf-8")
    with pytest.raises(CreativeValidationError, match="INVALID_JSON"):
        record_type.from_dict(read_json('{"schema_version":"duplicate",' + raw[1:]))


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "wrong-digest", "wrong-selection", "wrong-request"])
def test_stage_receipt_rejects_rebound_output_and_reasoning_references(tmp_path, mutation):
    stage = run_creative_stage(creative_job(), CreativeStore(tmp_path), adapter())
    data = stage.to_dict()
    if mutation == "duplicate":
        data["outputs"][1] = data["outputs"][0]
    elif mutation == "unknown":
        data["outputs"][0]["concept_id"] = "concept:" + "0" * 64
    elif mutation == "wrong-digest":
        data["outputs"][0]["concept_digest"] = "0" * 64
    elif mutation == "wrong-selection":
        data["decision"]["selection_id"] = data["outputs"][0]["concept_id"].replace("concept:", "selection:")
    else:
        data["reasoning"]["request"]["instruction"] = "rebound instruction"
    with pytest.raises(CreativeValidationError):
        A03CreativeStageResult.from_dict(data)


def test_stage_unknown_output_selection_and_rebound_request_fail_closed(tmp_path):
    request = explore()  # No count means the output bound is checked after inference.
    client = FakeClient()
    stage = run_creative_stage(creative_job(request, selection_index=7), CreativeStore(tmp_path), adapter(client))
    assert stage.status == "FAILED" and stage.failure == "UNKNOWN_CONCEPT_SELECTION"
    assert stage.outputs == () and len(client.calls) == 1

    class ReboundFake:
        def reason(self, request):
            return reason_v2(ReasoningRequest.create("different source", mode="controlled_variants"), adapter())

    stage = run_creative_stage(creative_job(job_id="rebound"), CreativeStore(tmp_path), ReboundFake())
    assert stage.status == "FAILED" and stage.failure == "INVALID_REASONER_RESULT"


@pytest.mark.parametrize("kind", ["AUTO_PINNED", "AI_RECOMMENDED"])
def test_historical_arbitrary_ai_evidence_decodes_but_cannot_be_saved_as_inference(saved, kind):
    _, sidecars, _, _, selection = saved
    legacy = ActorDecision(selection.selection_id, kind,
        ActorRef("AI", "pretend:authenticated-ai") if kind == "AI_RECOMMENDED" else ActorRef("SYSTEM", "cli:pin"),
        "arbitrary:evidence")
    raw = legacy.to_dict()
    assert "inference" not in raw and ActorDecision.from_dict(raw) == legacy
    # Existing V1 bytes remain readable for history, with no inference authority.
    sidecars._save("decisions", legacy.decision_id[9:], raw)
    assert sidecars.load_decision(legacy.decision_id) == legacy
    with pytest.raises(CreativeValidationError, match="saved inference binding"):
        sidecars.save_decision(legacy)
    with pytest.raises(CreativeValidationError, match="saved inference binding"):
        sidecars.verify_inference_decision(legacy)


@pytest.mark.parametrize("field,value", [
    ("receipt_digest", "0" * 64), ("request_digest", "0" * 64),
    ("source_brief_digest", "0" * 64), ("concept_digest", "0" * 64), ("mode", "MODEL"),
])
def test_forged_ai_binding_requires_actual_saved_matching_receipt(tmp_path, field, value):
    store = CreativeSidecarStore(tmp_path)
    stage = run_creative_stage(creative_job(), store, adapter())
    binding = replace(stage.decision.inference, **{field: value})
    forged = replace(stage.decision, inference=binding, evidence_ref="reasoning:" + binding.receipt_digest)
    with pytest.raises((CreativeValidationError, FileNotFoundError)):
        store.save_decision(forged)


def test_ai_receipt_cannot_select_another_output_or_brief(tmp_path):
    store = CreativeSidecarStore(tmp_path)
    first = run_creative_stage(creative_job(), store, adapter())
    other_drafts = (ConceptDraft("other", "Other", "Other premise", "Other visual"),)
    client = FakeClient(StructuredModelResponse("OK", payload(other_drafts), observation=MOCK, request_id="fake:other"))
    request = ReasoningRequest.create(TEXT, count=1)
    other = run_creative_stage(creative_job(request, job_id="other", selection_index=0), store, adapter(client))
    with pytest.raises(CreativeValidationError, match="selected concept mismatch"):
        store.decide_from_result(other.outputs[0].selection_id, first.reasoning.digest)
    # An equal draft against a different brief is also a different exact output.
    request = ReasoningRequest.create("another brief", mode="controlled_variants", count=3)
    third = run_creative_stage(creative_job(request, job_id="third"), store, adapter())
    with pytest.raises(CreativeValidationError, match="selected concept mismatch"):
        store.decide_from_result(third.outputs[1].selection_id, first.reasoning.digest)
    with pytest.raises(CreativeValidationError, match="AI actor reference"):
        replace(store.decide_from_result(first.decision.selection_id, first.reasoning.digest, kind="AI_RECOMMENDED"),
                actor=ActorRef("AI", "pretend:authenticated-ai"))


def test_failed_unknown_and_critique_receipts_cannot_authorize_ai_selection(saved):
    _, sidecars, brief, concept, selection = saved
    refused = reason_v2(explore(), adapter(FakeClient(StructuredModelResponse(
        "REFUSED", observation=MOCK, request_id="fake:refusal"))))
    no_model = reason_v2(explore())
    unbound = ReasoningResult(explore(), "OK", variants())
    critique_request = ReasoningRequest.create(brief, operation="CRITIQUE", source=concept)
    critique = reason_v2(critique_request, adapter(FakeClient(StructuredModelResponse(
        "OK", payload(critique="Optional critique"), observation=MOCK, request_id="fake:critique"))))
    for result in (refused, no_model, unbound, critique):
        receipt = sidecars.save_result(result)
        with pytest.raises(CreativeValidationError):
            sidecars.decide_from_result(selection.selection_id, receipt)


@pytest.mark.parametrize("kind", ["brief", "concept", "selection", "reasoning", "decision", "stage", "job", "stage-ref"])
def test_stage_replay_verifies_all_stored_bytes_and_never_calls_after_tamper(tmp_path, kind):
    store, client = CreativeSidecarStore(tmp_path), FakeClient()
    stage = run_creative_stage(creative_job(), store, adapter(client))
    paths = {
        "brief": store.creative.root / "briefs" / f"{stage.job.source_brief_digest}.json",
        "concept": store.creative._concept_path(stage.outputs[0].concept_id, 1),
        "selection": store.creative._selection_path(stage.outputs[0].selection_id),
        "reasoning": store._path("reasoning", stage.reasoning.digest),
        "decision": store._path("decisions", stage.decision.decision_id[9:]),
        "stage": store._path("stages", stage.digest), "job": store._path("jobs", stage.job.slot),
        "stage-ref": store._path("stage-refs", stage.job.slot),
    }
    target = paths[kind]
    target.write_bytes(target.read_bytes() + b"\n")  # Semantic JSON stays equal; exact bytes do not.
    with pytest.raises((CreativeValidationError, VersionConflict)):
        run_creative_stage(stage.job, store, adapter(client))
    assert len(client.calls) == 1


@pytest.mark.parametrize("operation", ["DEVELOP", "REFRAME", "CRITIQUE"])
def test_stage_existing_operations_commit_only_actual_inference_outputs(saved, operation):
    store, sidecars, brief, original, _ = saved
    request = ReasoningRequest.create(brief, operation=operation, source=original,
        **({"lens": "test:lens"} if operation == "REFRAME" else {}))
    response = StructuredModelResponse("OK", payload(critique="Optional critique") if operation == "CRITIQUE"
        else payload(edits={"title": "Inferred title"}), observation=MOCK, request_id="fake:operation")
    job = creative_job(request, selection_index=None if operation == "CRITIQUE" else 0)
    stage = run_creative_stage(job, sidecars, adapter(FakeClient(response)))
    assert stage.status == "TEST_ONLY_MOCK" and store.load_concept(original.concept_id, 1) == original
    if operation == "CRITIQUE":
        assert stage.outputs == () and stage.decision is None
        assert stage.reasoning.critique == "Optional critique"
        assert not store._concept_path(original.concept_id, 2).exists()
    else:
        child = store.load_concept(original.concept_id, 2)
        assert stage.outputs[0].concept_digest == child.digest
        assert child.parent.digest == original.digest and child.observation == original.observation
        assert sidecars.verify_inference_decision(stage.decision) == stage.reasoning


def test_requested_effort_is_optional_and_old_v2_configuration_bytes_stay_stable():
    original = ModelConfiguration("provider", "model")
    assert original.to_dict() == {"provider": "provider", "model": "model"}
    assert ModelConfiguration.from_dict(original.to_dict()) == original
    configured = ModelConfiguration("provider", "model", effort="requested-high")
    assert ModelConfiguration.from_dict(configured.to_dict()) == configured
    with pytest.raises(CreativeValidationError):
        ModelConfiguration.from_dict({**configured.to_dict(), "observed_effort": "invented"})


class FakeInstalledCreativeCLI:
    """Offline fixture for probes, Codex JSONL and native session metadata.

    Even the native Popen path below is simulated. Nothing here proves a real
    provider, subscription, inference or spend; no CLI executable is launched.
    """

    def __init__(self, root):
        root.mkdir()
        self.executable = root / "codex.exe"
        self.executable.write_bytes(b"SYNTHETIC CLI, NOT AN EXECUTABLE")
        self.config = ModelConfiguration("codex-cli", "fixture-model", "fixture-effort")
        self.version = "fixture-installed-version"
        self.help = (" ".join(codex_creative_argv(str(self.executable), self.config))).encode()
        self.catalog = root / "models_cache.json"
        self.catalog.write_bytes(canonical_bytes({"client_version": self.version, "models": [{
            "slug": self.config.model, "supported_reasoning_levels": [{"effort": self.config.effort}]}]}))
        self.sessions = root / "sessions"
        self.sessions.mkdir()
        self.calls, self.packets, self.schemas = [], [], []
        self.fault = None
        self.creative_output = None

    def capability(self, **options):
        return CodexCliCapability(**{
            "executable": str(self.executable), "executable_sha256": sha256_bytes(self.executable.read_bytes()),
            "version": self.version, "help_sha256": sha256_bytes(self.help), "model_catalog": str(self.catalog),
            "model_catalog_sha256": sha256_bytes(self.catalog.read_bytes()), "sessions_dir": str(self.sessions),
            "argv": codex_creative_argv(str(self.executable), self.config), **options})

    def __call__(self, argv, *, input, cwd, timeout, max_bytes):
        self.calls.append(argv)
        assert isinstance(argv, list) and argv[0] == str(self.executable)
        assert isinstance(input, bytes) and 0 < timeout <= 120 and max_bytes == 1024 * 1024
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, b"codex-cli " + (
                b"wrong-version" if self.fault == "version" else self.version.encode()), b"")
        if argv[1:] == ["exec", "--help"]:
            return subprocess.CompletedProcess(argv, 0, b"untrusted help" if self.fault == "probe" else self.help, b"")
        if argv[1:] == ["login", "status"]:
            auth = b"Logged in using API key" if self.fault == "auth" else b"Logged in using ChatGPT"
            return subprocess.CompletedProcess(argv, 0, b"", auth)
        packet = json.loads(input)
        self.packets.append(packet)
        self.schemas.append(json.loads(Path(argv[argv.index("--output-schema") + 1]).read_bytes()))
        assert Path(cwd).is_dir() and argv[-1] == "-"
        if self.fault == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout, stderr=b"SECRET_SHOULD_NOT_BE_ECHOED")
        if self.fault == "oversize":
            return subprocess.CompletedProcess(argv, 0, b"x" * (max_bytes + 1), b"")
        thread_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" if self.fault == "reused-session" else str(uuid.uuid4())
        context = {"model": "spoofed-model" if self.fault == "model" else self.config.model,
                   "effort": "spoofed-effort" if self.fault == "effort" else self.config.effort}
        if self.fault == "missing-model":
            context.pop("model")
        if self.fault == "missing-effort":
            context.pop("effort")
        records = [
            {"type": "session_meta", "payload": {
                "id": thread_id,
                "cli_version": "forged-version" if self.fault == "session-version" else self.version,
                "model_provider": "forged-provider" if self.fault == "session-provider" else "openai",
            }},
            {"type": "turn_context", "payload": context},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "rebound" if self.fault == "session-binding" else input.decode()}]}},
        ]
        path = self.sessions / datetime.now().strftime("%Y/%m/%d") / ("rollout-fixture-" + thread_id + ".jsonl")
        if self.fault != "missing-session":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\n".join(canonical_bytes(r) for r in records) + b"\n")
        creative_output = self.creative_output(packet) if callable(self.creative_output) else self.creative_output
        output = {"request_sha256": "0" * 64 if self.fault == "binding" else packet["request_sha256"],
                  "output": creative_output or json.loads(payload(variants()))}
        if self.fault == "spoofed-identity":
            output["observation"] = {"provider": "codex-cli", "model": self.config.model, "mode": "MODEL"}
        message = canonical_bytes(output).decode()
        if self.fault == "malformed":
            message = "{"
        if self.fault == "extra-json":
            message += "{}"
        if self.fault == "duplicate-json":
            message = message[:-1] + ',"request_sha256":"duplicate"}'
        events = [{"type": "thread.started", "thread_id": thread_id}, {"type": "turn.started"},
                  {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": message}}]
        if self.fault == "duplicate-output":
            events.append({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": message}})
        if self.fault == "partial-output":
            events[2]["type"] = "item.updated"
        if self.fault == "tool-event":
            events.insert(2, {"type": "item.completed", "item": {"id": "tool", "type": "command_execution",
                                                                  "command": "unapproved"}})
        end = {"type": "turn.completed"}
        if self.fault != "missing-usage":
            end["usage"] = {"input_tokens": 11, "cached_input_tokens": 2, "output_tokens": 7}
        events.append(end)
        return subprocess.CompletedProcess(argv, 1 if self.fault == "nonzero" else 0,
            b"\n".join(canonical_bytes(e) for e in events) + b"\n",
            b"x" * (max_bytes + 1) if self.fault == "stderr-oversize" else b"SECRET_SHOULD_NOT_BE_ECHOED")


@pytest.fixture
def installed_cli(tmp_path, monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("Offline CLI fixture attempted network")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    return FakeInstalledCreativeCLI(tmp_path / "synthetic-host")


def cli_stage(cli, root, *, capability=None, native=False, authorization=InvocationAuthorization(True, True, "fixture:grant"),
              purpose="TEST_ONLY_MOCK", request=None):
    client = CodexCliCreativeClient(capability or cli.capability(), runner=None if native else cli)
    job = creative_job(request, configuration=cli.config, purpose=purpose)
    return run_creative_stage(job, CreativeStore(root), LiveCreativeReasoner(cli.config, client), authorization=authorization)


def test_cli_probed_injected_runner_persists_mock_receipt_and_exact_binding(installed_cli, tmp_path):
    cli = installed_cli
    stage = cli_stage(cli, tmp_path / "creative")
    assert stage.status == "TEST_ONLY_MOCK" and stage.reasoning.observation.model == "UNKNOWN"
    evidence = stage.reasoning.execution
    assert evidence.kind == "TEST_ONLY_MOCK" and evidence.effect == "PROCESS_INVOKED"
    assert evidence.observed_model == cli.config.model and evidence.observed_effort == cli.config.effort
    assert evidence.authorization_ref == "fixture:grant"
    assert evidence.request_digest == stage.job.request_digest
    assert evidence.configuration_digest == sha256_bytes(canonical_bytes(cli.config.to_dict()))
    assert evidence.stdin_sha256 == sha256_bytes(canonical_bytes(cli.packets[0]))
    assert evidence.schema_sha256 == sha256_bytes(canonical_bytes(cli.schemas[0]))
    assert stage.reasoning.provider_request_id is None  # A CLI thread is not a provider request ID.
    assert evidence.cli_thread_id and stage.reasoning.usage == UsageObservation("OBSERVED", 11, 7)
    assert stage.reasoning.usage.cost_amount is None
    assert len(cli.calls) == 4 and len(cli.packets) == 1 and len(stage.outputs) == 3
    schema = cli.schemas[0]
    assert schema["properties"]["request_sha256"]["enum"] == [cli.packets[0]["request_sha256"]]
    sidecars = CreativeSidecarStore(tmp_path / "creative")
    assert sidecars.load_stage(stage.job) == stage
    assert sidecars.verify_inference_decision(stage.decision) == stage.reasoning
    assert run_creative_stage(stage.job, sidecars) == stage and len(cli.calls) == 4
    assert "SECRET_SHOULD_NOT_BE_ECHOED" not in json.dumps(stage.to_dict())


def install_fake_popen(monkeypatch, cli):
    """Exercise production bounded-pipe code, replacing only the OS process."""
    class Process:
        def __init__(self, argv, *, stdin, stdout, stderr, shell, cwd):
            assert shell is False and stdin == stdout == stderr == subprocess.PIPE
            self.ready = threading.Event()
            self.returncode = None
            self.killed = False
            self.is_model = argv[-1] == "-"
            owner = self

            class Input:
                def write(self, packet):
                    response = cli(argv, input=packet, cwd=cwd, timeout=120, max_bytes=1024 * 1024)
                    owner.output = [io.BytesIO(response.stdout), io.BytesIO(response.stderr)]
                    owner.returncode = response.returncode
                    owner.ready.set()
                    return len(packet)

                def close(self):
                    pass

            class Pipe:
                def __init__(self, index):
                    self.index = index

                def read(self, size):
                    assert owner.ready.wait(5)
                    return owner.output[self.index].read(size)

                def close(self):
                    pass

            self.stdin, self.stdout, self.stderr = Input(), Pipe(0), Pipe(1)

        def wait(self, timeout):
            if self.is_model and cli.fault == "native-timeout" and not self.killed:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            if not self.ready.wait(timeout):
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return self.returncode

        def kill(self):
            self.killed = True

    monkeypatch.setattr(subprocess, "Popen", Process)


def test_cli_native_process_branch_is_simulated_not_live_provider_proof(installed_cli, tmp_path, monkeypatch):
    cli = installed_cli
    install_fake_popen(monkeypatch, cli)
    client = CodexCliCreativeClient(cli.capability())
    assert client.complete(StructuredModelRequest(cli.config, explore())).error_code == "UNAUTHORIZED"
    assert cli.calls == []
    stage = cli_stage(cli, tmp_path / "creative", native=True, purpose="PRODUCTION")
    assert stage.status == "CREATIVE_COMPLETE"  # Synthetic coverage of native branch only.
    assert stage.reasoning.execution.kind == "AUTHENTICATED_CLI_PROBE"
    assert stage.reasoning.observation == ProviderObservation("codex-cli", cli.config.model, "MODEL")
    assert stage.reasoning.provider_request_id is None and stage.reasoning.execution.cli_thread_id
    assert stage.decision.inference.mode == "MODEL"
    assert len(cli.calls) == 4 and CreativeSidecarStore(tmp_path / "creative").load_stage(stage.job) == stage
    assert run_creative_stage(stage.job, CreativeStore(tmp_path / "creative")) == stage
    assert len(cli.calls) == 4


@pytest.mark.parametrize("fault", ["oversize", "stderr-oversize", "native-timeout"])
def test_cli_native_pipe_bounds_and_timeout_never_retry(installed_cli, tmp_path, monkeypatch, fault):
    cli = installed_cli
    cli.fault = fault
    install_fake_popen(monkeypatch, cli)
    stage = cli_stage(cli, tmp_path / "creative", native=True, purpose="PRODUCTION")
    assert stage.status == "FAILED" and stage.failure == "UNKNOWN_EFFECT"
    assert stage.reasoning.usage == UsageObservation() and stage.reasoning.observation == ProviderObservation()
    assert not stage.outputs and stage.reasoning.execution.effect == "UNKNOWN"
    assert run_creative_stage(stage.job, CreativeStore(tmp_path / "creative")) == stage
    assert len(cli.calls) == 4


@pytest.mark.parametrize("fault,expected,invoked", [
    ("version", "PROVIDER_UNAVAILABLE", False), ("probe", "PROVIDER_UNAVAILABLE", False),
    ("auth", "UNAUTHORIZED", False), ("model", "CONFIGURATION_MISMATCH", True),
    ("missing-model", "CONFIGURATION_MISMATCH", True), ("missing-effort", "CONFIGURATION_MISMATCH", True),
    ("effort", "CONFIGURATION_MISMATCH", True), ("missing-session", "CONFIGURATION_MISMATCH", True),
    ("session-version", "CONFIGURATION_MISMATCH", True), ("session-provider", "CONFIGURATION_MISMATCH", True),
    ("session-binding", "CONFIGURATION_MISMATCH", True), ("binding", "CONFIGURATION_MISMATCH", True),
    ("spoofed-identity", "UNKNOWN_EFFECT", True), ("malformed", "UNKNOWN_EFFECT", True),
    ("extra-json", "UNKNOWN_EFFECT", True), ("duplicate-json", "UNKNOWN_EFFECT", True),
    ("duplicate-output", "UNKNOWN_EFFECT", True), ("partial-output", "UNKNOWN_EFFECT", True),
    ("tool-event", "UNKNOWN_EFFECT", True), ("nonzero", "UNKNOWN_EFFECT", True),
    ("timeout", "UNKNOWN_EFFECT", True), ("oversize", "UNKNOWN_EFFECT", True),
])
def test_cli_failure_is_saved_no_retry_output_or_secret_echo(installed_cli, tmp_path, fault, expected, invoked):
    cli = installed_cli
    cli.fault = fault
    stage = cli_stage(cli, tmp_path / "creative")
    assert stage.status == "FAILED" and stage.failure == expected
    assert stage.reasoning.usage == UsageObservation() and stage.reasoning.observation == ProviderObservation()
    assert not stage.outputs and stage.decision is None
    assert len(cli.packets) == int(invoked)
    if fault == "model":
        assert stage.reasoning.execution.observed_model == "spoofed-model"
    if fault == "effort":
        assert stage.reasoning.execution.observed_effort == "spoofed-effort"
    before = len(cli.calls)
    assert run_creative_stage(stage.job, CreativeStore(tmp_path / "creative")) == stage
    assert len(cli.calls) == before
    assert "SECRET_SHOULD_NOT_BE_ECHOED" not in json.dumps(stage.to_dict())


@pytest.mark.parametrize("fault", ["missing-executable", "binary-hash", "catalog-hash", "catalog-model",
                                   "unsupported-flags", "unsupported-help-flag", "untrusted-capability"])
def test_cli_rejects_missing_or_untrusted_installed_capability_before_inference(installed_cli, tmp_path, fault):
    cli = installed_cli
    cap = cli.capability()
    if fault == "missing-executable":
        cap = replace(cap, executable=str(cli.executable.parent / "absent" / "codex.exe"))
    elif fault == "binary-hash":
        cap = replace(cap, executable_sha256="0" * 64)
    elif fault == "catalog-hash":
        cap = replace(cap, model_catalog_sha256="0" * 64)
    elif fault == "catalog-model":
        cli.catalog.write_bytes(canonical_bytes({"client_version": cli.version, "models": []}))
        cap = replace(cap, model_catalog_sha256=sha256_bytes(cli.catalog.read_bytes()))
    elif fault == "unsupported-flags":
        cap = replace(cap, argv=(*cap.argv[:-1], "--unreviewed", "-"))
    elif fault == "unsupported-help-flag":
        cli.help = cli.help.replace(b"--ignore-user-config", b"unavailable")
        cap = replace(cap, help_sha256=sha256_bytes(cli.help))
    else:
        with pytest.raises(CreativeValidationError, match="trusted CodexCliCapability"):
            CodexCliCreativeClient({"argv": list(cap.argv), "authenticated": True})
        assert cli.calls == []
        return
    stage = cli_stage(cli, tmp_path / "creative", capability=cap)
    assert stage.status == "FAILED" and stage.failure in ("PROVIDER_UNAVAILABLE", "CONFIGURATION_MISMATCH")
    assert cli.packets == []


@pytest.mark.parametrize("grant", [None, InvocationAuthorization(), InvocationAuthorization(True, False, "fixture:network"),
                                  InvocationAuthorization(False, True, "fixture:spend")])
def test_cli_stage_requires_explicit_runner_grant_not_adapter_or_job(installed_cli, tmp_path, grant):
    cli = installed_cli
    client = CodexCliCreativeClient(cli.capability(), runner=cli)
    reasoner = LiveCreativeReasoner(cli.config, client, authorization=InvocationAuthorization(True, True, "fixture:other-path"))
    job = creative_job(configuration=cli.config)
    data = job.to_dict()
    data["authorization"] = {"allow_network": True, "allow_spend": True}
    with pytest.raises(CreativeValidationError):
        A03CreativeJob.from_dict(data)
    stage = run_creative_stage(job, CreativeStore(tmp_path / "creative"), reasoner, authorization=grant)
    assert stage.failure == "UNAUTHORIZED" and cli.calls == []


def test_cli_fake_cannot_complete_production_or_assert_model_mode(installed_cli, tmp_path):
    cli = installed_cli
    stage = cli_stage(cli, tmp_path / "creative", purpose="PRODUCTION")
    assert stage.status == "FAILED" and stage.failure == "MOCK_NOT_PRODUCTION"
    assert stage.reasoning.execution.kind == "TEST_ONLY_MOCK" and stage.reasoning.observation.model == "UNKNOWN"
    data = stage.reasoning.to_dict()
    data["observation"] = {"provider": "codex-cli", "model": cli.config.model, "mode": "MODEL"}
    with pytest.raises(CreativeValidationError, match="unbound CLI"):
        ReasoningResult.from_dict(data)


def test_cli_missing_usage_remains_unknown(installed_cli, tmp_path):
    installed_cli.fault = "missing-usage"
    stage = cli_stage(installed_cli, tmp_path / "creative")
    assert stage.status == "TEST_ONLY_MOCK" and stage.reasoning.usage == UsageObservation()


def test_cli_unicode_line_separator_is_content_in_jsonl_not_an_event_boundary(installed_cli, tmp_path):
    cli = installed_cli
    cli.creative_output = json.loads(payload(variants()))
    cli.creative_output["drafts"][0]["title"] += "\u2028mưa"
    request = ReasoningRequest.create(TEXT + "\u2028", mode="controlled_variants", count=3)
    stage = cli_stage(cli, tmp_path / "creative", request=request)
    assert stage.status == "TEST_ONLY_MOCK" and stage.reasoning.drafts[0].title.endswith("\u2028mưa")


@pytest.mark.parametrize("field", ["request_digest", "configuration_digest", "observed_effort", "observed_model"])
def test_cli_receipt_rejects_metadata_tamper(installed_cli, tmp_path, field):
    stage = cli_stage(installed_cli, tmp_path / "creative")
    value = stage.reasoning.to_dict()
    value["execution"][field] = "0" * 64 if field.endswith("digest") else "spoofed"
    with pytest.raises(CreativeValidationError):
        ReasoningResult.from_dict(value)
    sidecars = CreativeSidecarStore(tmp_path / "creative")
    sidecars._path("reasoning", stage.reasoning.digest).write_bytes(canonical_bytes(value))
    before = len(installed_cli.calls)
    with pytest.raises(CreativeValidationError):
        run_creative_stage(stage.job, sidecars)
    assert len(installed_cli.calls) == before


@pytest.mark.parametrize("operation", ["DEVELOP", "REFRAME", "CRITIQUE"])
def test_cli_existing_optional_operations_use_strict_schema_and_original_lineage(installed_cli, saved, operation):
    store, sidecars, brief, source, _ = saved
    cli = installed_cli
    options = {"operation": operation, "source": source}
    if operation == "REFRAME":
        options["lens"] = "sound"
    request = ReasoningRequest.create(brief, **options)
    if operation == "CRITIQUE":
        cli.creative_output = {"drafts": [], "edits": {}, "critique": "Optional quiet observation"}
    else:
        cli.creative_output = {"drafts": [], "edits": {"family": None, "title": None, "visual_direction": None,
                                                      "premise": "A new quiet rain mechanism"}, "critique": None}
    client = CodexCliCreativeClient(cli.capability(), runner=cli)
    job = creative_job(request, configuration=cli.config, selection_index=None if operation == "CRITIQUE" else 0)
    stage = run_creative_stage(job, sidecars, LiveCreativeReasoner(cli.config, client),
                               authorization=InvocationAuthorization(True, True, "fixture:operations"))
    assert stage.status == "TEST_ONLY_MOCK" and store.load_concept(source.concept_id, 1) == source
    assert (stage.decision is None) == (operation == "CRITIQUE")
    if operation != "CRITIQUE":
        assert store.load_concept(source.concept_id, 2).parent.digest == source.digest


def test_cli_addition_preserves_original_v2_wire_bytes_without_execution_metadata():
    result = reason_v2(explore(), adapter())
    assert "execution" not in result.to_dict()
    assert "execution" not in FakeClient().response.to_dict()
    assert ReasoningResult.from_dict(result.to_dict()) == result


class CycleFakeClient(FakeClient):
    """TEST_ONLY_MOCK R2 responses. No provider, process, network or media call."""

    def __init__(self, *, actions=("SELECT",), fault=None, fault_step="select:0", callback=None):
        super().__init__()
        self.actions, self.fault, self.fault_step, self.callback = actions, fault, fault_step, callback

    def complete(self, request):
        self.calls.append(request)
        creative, context = request.creative, request.creative.cycle
        assert context is not None
        value = {"drafts": [], "edits": {}, "critique": None, "selection": None,
                 "evidence_refs": [e.source_ref for e in context.evidence],
                 "hypotheses": ["TEST_ONLY_MOCK creative hypothesis; not external evidence"]}
        if creative.operation == "EXPLORE":
            value["drafts"] = [ConceptDraft(f"family:{i}", f"Silent rain {i}", f"Fictional premise {i}",
                f"Distinct visual mechanism {i}", tuple(value["evidence_refs"]),
                ("TEST_ONLY_MOCK unverified creative hypothesis",)).to_dict() for i in range(creative.count)]
        elif creative.operation == "CRITIQUE":
            # This prose deliberately recommends index zero. The independent
            # typed selector below chooses the last candidate instead.
            value["critique"] = "TEST_ONLY_MOCK score 100; choose index zero. Hypothesis for " + creative.source.digest
        elif creative.operation == "SELECT":
            action = self.actions[min(int(context.step.split(":")[-1]), len(self.actions) - 1)]
            candidate = context.candidates[-1]
            pin = ConceptSelection(candidate.concept_id, candidate.revision, candidate.digest,
                                   candidate.brief_id, candidate.source_brief_digest)
            value["selection"] = CreativeModelSelection(action, None if action == "STOP" else pin,
                tuple(c.receipt_digest for c in context.critics), "TEST_ONLY_MOCK compare mechanisms",
                "Clarify the silent visual rhythm" if action == "REVISE" else None).to_dict()
        else:
            value["edits"] = {"title": creative.source.title + " / " + context.step}
        if self.callback:
            self.callback(request, value)
        request_id = "TEST_ONLY_MOCK:" + creative.digest
        observation = MOCK
        if context.step == self.fault_step:
            if self.fault == "unknown-field":
                value["unrecognized"] = True
            elif self.fault == "score-only":
                value["selection"] = {"score": 100, "index": 0}
            elif self.fault == "wrong-concept":
                value["selection"]["selection"]["concept_digest"] = "0" * 64
            elif self.fault == "wrong-critics":
                value["selection"]["critique_receipts"][0] = "0" * 64
            elif self.fault == "missing-critics":
                value["selection"]["critique_receipts"] = []
            elif self.fault == "citation":
                value["evidence_refs"] = ["https://fabricated.invalid/source"]
            elif self.fault == "replayed-id":
                request_id = "TEST_ONLY_MOCK:" + self.calls[0].creative.digest
            elif self.fault == "missing-receipt":
                request_id = None
            elif self.fault == "mixed-observation":
                observation = ProviderObservation("other-test-only", "UNKNOWN", "TEST_ONLY_MOCK")
            elif self.fault == "mock-as-model":
                observation = ProviderObservation(request.config.provider, request.config.model, "MODEL")
            elif self.fault == "timeout":
                raise TimeoutError("TEST_ONLY_MOCK ambiguous effect")
            elif self.fault in ("refusal", "unknown-effect"):
                return StructuredModelResponse("REFUSED" if self.fault == "refusal" else "ERROR",
                    observation=MOCK, request_id=request_id, error_code="MODEL_REFUSED" if self.fault == "refusal" else "UNKNOWN_EFFECT",
                    message="TEST_ONLY_MOCK observed stop")
            elif self.fault == "duplicate-families":
                value["drafts"][1]["family"] = value["drafts"][0]["family"]
            elif self.fault == "invented-draft-source":
                value["drafts"][0]["source_refs"] = ["invented:source"]
        return StructuredModelResponse("OK", json.dumps(value, ensure_ascii=False, indent=2),
                                       observation=observation, request_id=request_id)


def cycle_job(*, brief=None, count=2, **options):
    brief = intake_brief(TEXT) if brief is None else brief
    seed_options = options.pop("seed_options", {})
    seed = creative_job(ReasoningRequest.create(brief, count=count, intent=capture_intent(brief)),
        **{"configuration": ModelConfiguration("requested-provider", "requested-model", "xhigh"),
           "selection_index": None, **seed_options})
    return A03CreativeCycleJob(seed, **options)


def cycle_adapter(client):
    return LiveCreativeReasoner(ModelConfiguration("requested-provider", "requested-model", "xhigh"), client)


@pytest.mark.parametrize(("provider", "transport", "hold"), [
    ("codex-cli", "codex-exec-jsonl", "MODEL_SELECTION_REQUIRED"),
    ("antigravity-cli", "antigravity-cli-session", "AGY_AUTHENTICATED_CLI_SESSION_UNAVAILABLE"),
    ("chatgpt-brain-cdp", "wakehub-chrome-cdp-9444", "BRAIN_CDP_ORIGIN_AND_TURN_AUTHORITY_UNAVAILABLE"),
])
def test_c1_selects_task_route_without_model_and_holds_without_transport(tmp_path, provider, transport, hold):
    config = ModelConfiguration(provider)
    cycle = cycle_job(seed_options={"purpose": "PRODUCTION", "configuration": config})
    request = creative_v2._cycle_request(cycle, "explore", "EXPLORE")
    job = creative_v2._cycle_stage_job(cycle, request)
    grant = InvocationAuthorization(True, True, "fixture:c1")
    task = AIReasoningTask(job, grant)
    wire = json.loads(task.canonical_payload)

    assert select_reasoning_provider(provider, "EXPLORE") == task.route
    assert select_reasoning_provider(provider, "PRODUCE_PACKAGE") == task.route
    assert select_reasoning_provider(provider, "RESEARCH") is None
    assert wire["transport_id"] == transport and wire["job_sha256"] == job.digest
    assert wire["request"] == StructuredModelRequest(config, request).to_dict()
    assert task.digest != replace(task, job=replace(job, attempt=job.attempt + 1)).digest
    assert task.digest != replace(task, authorization=replace(grant, authorization_ref="fixture:other")).digest
    assert task.digest != replace(task, job=replace(
        job, provenance=replace(job.provenance, source_ref="fixture:other-source"),
    )).digest
    assert task.digest != replace(task, job=replace(
        job, configuration=ModelConfiguration(provider, "requested-model", "requested-effort"),
    )).digest

    result = run_creative_cycle(cycle, tmp_path, authorization=grant)
    assert (result.status, result.failure, result.steps) == ("HOLD", hold, ())
    assert run_creative_cycle(cycle, tmp_path, authorization=grant) == result


def test_c1_no_route_and_missing_runner_grant_hold_before_inference(tmp_path):
    unknown = ModelConfiguration("unregistered")
    assert select_reasoning_provider(unknown.provider, "EXPLORE") is None
    job = cycle_job(seed_options={"purpose": "PRODUCTION", "configuration": unknown})
    grant = InvocationAuthorization(True, True, "fixture:c1")
    result = run_creative_cycle(job, tmp_path / "unknown", authorization=grant)
    assert (result.status, result.failure, result.steps) == ("HOLD", "NO_PROVIDER_ROUTE", ())
    brain = cycle_job(seed_options={"purpose": "PRODUCTION", "configuration": ModelConfiguration("chatgpt-brain-cdp")})
    result = run_creative_cycle(brain, tmp_path / "brain")
    assert (result.status, result.failure, result.steps) == ("HOLD", "UNAUTHORIZED", ())


@pytest.mark.parametrize("provider", ["antigravity-cli", "chatgpt-brain-cdp"])
def test_c1_explicit_model_and_effort_cannot_enable_unverified_provider(provider):
    class SpoofedClient(CycleFakeClient):
        execution_mode = "MODEL"

    config = ModelConfiguration(provider, "claimed-model", "claimed-effort")
    cycle = cycle_job(seed_options={"purpose": "PRODUCTION", "configuration": config})
    request = creative_v2._cycle_request(cycle, "explore", "EXPLORE")
    grant = InvocationAuthorization(True, True, "fixture:c1")
    client = SpoofedClient()
    result = reason_v2(request, LiveCreativeReasoner(config, client, authorization=grant))
    assert result.state == "ERROR"
    assert result.error_code == select_reasoning_provider(provider, "EXPLORE").unavailable_reason
    assert not client.calls and result.observation == ProviderObservation() and result.execution is None


@pytest.fixture
def agy_preflight_case(tmp_path):
    executable = tmp_path.resolve() / ("agy.exe" if os.name == "nt" else "agy")
    executable.write_bytes(b"test-only AGY probe binary")
    client = AntigravityCliCreativeClient(str(executable), sha256_bytes(executable.read_bytes()))
    config = ModelConfiguration("antigravity-cli", "explicit-model", "high")
    cycle = cycle_job(seed_options={"purpose": "PRODUCTION", "configuration": config})
    request = creative_v2._cycle_request(cycle, "explore", "EXPLORE")
    job = creative_v2._cycle_stage_job(cycle, request)
    return client, cycle, AIReasoningTask(job, InvocationAuthorization(True, True, "fixture:agy-grant"))


AGY_HELP = b"""Usage of agy:
  --print           Run a single prompt
  --model           Model for the session
  --effort          Reasoning effort
  --json-schema     Enforce structured output
  --input-format    text, stream-json
  --output-format   text, json, stream-json
  --mode            Execution mode, including plan
  --sandbox         Enable terminal sandbox
  --disable-slash-commands  Disable slash command expansion
"""


def test_agy_read_only_preflight_is_task_bound_and_cannot_admit_r2(agy_preflight_case, tmp_path, monkeypatch):
    client, cycle, task = agy_preflight_case
    calls = []

    def probe(argv, *, input, cwd, timeout, max_bytes):
        assert argv[0] == client.executable and input == b"" and cwd == str(Path(client.executable).parent)
        assert 0 < timeout <= 15 and max_bytes == creative_live.CLI_MAX_BYTES
        calls.append(tuple(argv[1:]))
        return (subprocess.CompletedProcess(argv, 0, b"2.41.0-rc.2\r\n", b"") if argv[1:] == ["--version"]
                else subprocess.CompletedProcess(argv, 0, b"", AGY_HELP))

    monkeypatch.setattr(creative_live, "_bounded_cli_process", probe)
    config, request = task.job.configuration, task.job.request
    reasoner = LiveCreativeReasoner(config, client, authorization=task.authorization)
    assert client.complete(StructuredModelRequest(config, request)).error_code == "UNAUTHORIZED"
    result = run_creative_cycle(cycle, tmp_path / "store", reasoner, authorization=task.authorization)
    hold = "AGY_AUTHENTICATED_CLI_SESSION_UNAVAILABLE"
    assert (result.status, result.failure, result.steps) == ("HOLD", hold, ())
    assert not calls

    assert client.preflight(task) == AntigravityCliPreflight(task.digest, "HOLD", hold, "2.41.0-rc.2",
                                                           sha256_bytes(AGY_HELP))
    assert calls == [("--version",), ("--help",)]
    assert run_creative_cycle(cycle, tmp_path / "store", reasoner, authorization=task.authorization) == result
    assert reason_v2(request, reasoner).error_code == hold
    assert calls == [("--version",), ("--help",)]


def test_agy_preflight_blocks_wrong_route_grants_selection_and_changed_binary(agy_preflight_case, monkeypatch):
    client, _, task = agy_preflight_case
    monkeypatch.setattr(creative_live, "_bounded_cli_process", lambda *a, **kw: pytest.fail("AGY process launched"))
    for grant in (InvocationAuthorization(True, False, "fixture:network-only"),
                  InvocationAuthorization(False, True, "fixture:spend-only"), InvocationAuthorization()):
        ungranted = replace(task, authorization=grant)
        assert client.preflight(ungranted) == AntigravityCliPreflight(ungranted.digest, "BLOCK", "UNAUTHORIZED")
    for config, status, code in (
        (ModelConfiguration("chatgpt-brain-cdp", "explicit-model", "explicit-effort"), "BLOCK", "CONFIGURATION_MISMATCH"),
        (ModelConfiguration("codex-cli", "explicit-model", "explicit-effort"), "BLOCK", "CONFIGURATION_MISMATCH"),
        (ModelConfiguration("antigravity-cli"), "HOLD", "MODEL_SELECTION_REQUIRED"),
        (ModelConfiguration("antigravity-cli", "explicit-model"), "HOLD", "UNVERIFIED_MODEL_CONFIGURATION"),
    ):
        changed = replace(task, job=replace(task.job, configuration=config))
        assert client.preflight(changed) == AntigravityCliPreflight(changed.digest, status, code)

    path = Path(client.executable)
    path.write_bytes(b"replaced after pinning")
    assert client.preflight(task) == AntigravityCliPreflight(task.digest, "BLOCK", "AGY_CLI_EXECUTABLE_UNVERIFIED")
    path.unlink()
    assert client.preflight(task) == AntigravityCliPreflight(task.digest, "UNAVAILABLE", "AGY_CLI_UNAVAILABLE")


@pytest.mark.parametrize(("failure", "code"), [
    ("wrong-version-output", "AGY_CLI_PROBE_UNAVAILABLE"),
    ("unsupported-schema", "AGY_CLI_CAPABILITY_UNAVAILABLE"),
    ("timeout", "AGY_CLI_PROBE_UNAVAILABLE"),
])
def test_agy_preflight_stops_on_unverified_native_capability(agy_preflight_case, monkeypatch, failure, code):
    client, _, task = agy_preflight_case
    calls = []

    def probe(argv, **kwargs):
        calls.append(argv[1])
        if argv[1:] == ["--version"]:
            version = b'{"status":"SUCCESS","authenticated":true}' if failure == "wrong-version-output" else b"3.7.5"
            return subprocess.CompletedProcess(argv, 0, version, b"")
        assert argv[1:] == ["--help"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, AGY_HELP.replace(b"--json-schema", b"--other-flag"), b"")

    monkeypatch.setattr(creative_live, "_bounded_cli_process", probe)
    result = client.preflight(task)
    assert (result.task_sha256, result.status, result.error_code) == (task.digest, "UNAVAILABLE", code)
    assert calls == (["--version"] if failure == "wrong-version-output" else ["--version", "--help"])


AGY_CONVERSATION_ID = "7360adbd-24de-49de-b3dc-56b844b96da3"
AGY_USAGE = {"input_tokens": 13, "output_tokens": 3, "thinking_tokens": 2,
             "cache_read_tokens": 8, "total_tokens": 16}


def agy_terminal_fixture(turn, *, streamed_prompt=False):
    # Match the headless reference's single-prompt stream and first stdin
    # turn, with the documented --json-schema fields on the result.
    job = turn.task.job
    output = CycleFakeClient().complete(StructuredModelRequest(job.configuration, job.request))
    envelope = {"task_sha256": turn.task.digest, "stream_nonce": turn.stream_nonce,
                "output": json.loads(output.output_json)}

    def step(index, step_type, *, state="DONE", **metadata):
        return {"event": "step_update", "step_update": {"conversation_id": AGY_CONVERSATION_ID,
                "step_index": index, "step_type": step_type, "state": state, **metadata}}

    response = json.dumps(envelope) + "\n"
    events = [
        {"event": "init", "conversation_id": AGY_CONVERSATION_ID, "init": {
            "cwd": "/fixture/project", "tools": ["ask_permission", "run_command", "write_to_file"],
            "permission_mode": "request-review", "json_schema": turn.output_schema,
        }},
        step(0, "user_input"),
    ]
    if streamed_prompt:
        events.extend([
            step(2, "agent_response", state="ACTIVE", text_delta=response[:-1]),
            step(2, "agent_response", text_delta="\n", duration_seconds=0.1, usage=AGY_USAGE),
        ])
    else:
        events.extend([
            step(3, "agent_response", text_delta=response, duration_seconds=0.1, usage=AGY_USAGE),
            step(4, "checkpoint", duration_seconds=0.01, usage=AGY_USAGE),
        ])
    events.append({"event": "result", "result": {
        "conversation_id": AGY_CONVERSATION_ID, "status": "SUCCESS", "response": response,
        "num_turns": 1, "duration_seconds": 0.11, "usage": dict(AGY_USAGE),
        "structured_output": envelope, "json_schema": turn.output_schema,
    }})
    return events, envelope


def agy_stdout(events, newline=b"\r\n"):
    return b"".join(canonical_bytes(event) + newline for event in events)


def test_agy_native_turn_sends_one_private_event_and_closes_stdin(agy_preflight_case, monkeypatch):
    client, _, task = agy_preflight_case
    turn = client.prepare_turn(task)
    calls = []
    raw = b""

    def popen(argv, *, stdin, stdout, stderr, shell, cwd, **terminal_options):
        nonlocal raw
        assert type(argv) is list and argv[0] == client.executable
        assert shell is False and stdin == stdout == stderr == subprocess.PIPE
        assert terminal_options == ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                                    else {"start_new_session": True})
        assert Path(cwd).is_dir() and not list(Path(cwd).iterdir())
        assert Path(cwd) not in (Path.cwd(), Path(client.executable).parent)
        calls.append((argv[1:], cwd))
        args = argv[1:]
        payload, limit = b"", 15
        if args == ["--version"]:
            output = b"2.41.0-rc.2\n"
        elif args == ["--help"]:
            output = AGY_HELP
        else:
            assert args == [
                "--input-format", "stream-json", "--output-format", "stream-json",
                "--model", "explicit-model", "--effort", "high",
                "--json-schema", canonical_bytes(turn.output_schema).decode(),
                "--mode", "plan", "--sandbox", "--disable-slash-commands",
            ]
            assert client.invoke_native_turn(turn, authorization=task.authorization).error_code == "AGY_STREAM_REPLAY"
            payload = canonical_bytes({"event": "user", "message": {"content": turn.canonical_payload.decode()}}) + b"\n"
            assert payload.count(b"\n") == 1 and task.authorization.authorization_ref.encode() not in payload
            events, _ = agy_terminal_fixture(turn, streamed_prompt=True)
            events[0]["init"].update(cwd=cwd, authenticated=True, account="private-account", session="private-session")
            raw = output = agy_stdout(events)
            limit = 120

        class Process:
            stdout, stderr = io.BytesIO(output), io.BytesIO()
            returncode = None

            def wait(self, timeout):
                assert 0 < timeout <= limit
                assert closed.wait(timeout=5) and self.stdin.closed
                self.returncode = 0

        closed = threading.Event()

        class Input(io.BytesIO):
            def close(self):
                if not self.closed:
                    assert self.getvalue() == payload
                    super().close()
                    closed.set()

        process = Process()
        process.stdin = Input()
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    config = task.job.configuration
    assert client.complete(StructuredModelRequest(config, task.job.request)).error_code == "UNAUTHORIZED"
    assert not calls

    receipt = client.invoke_native_turn(turn, authorization=task.authorization)
    assert (receipt.status, receipt.effect, receipt.terminal_status, receipt.error_code) == (
        "HOLD", "UNKNOWN_EFFECT", "SUCCESS", "AGY_AUTHENTICATED_CLI_SESSION_UNAVAILABLE",
    )
    assert receipt.stdout_sha256 == sha256_bytes(raw) and receipt.task_sha256 == task.digest
    assert receipt.output_schema_sha256 == sha256_bytes(canonical_bytes(turn.output_schema))
    assert receipt.job_sha256 == task.job.digest and receipt.source_brief_sha256 == task.job.source_brief_digest
    assert all(value not in repr(receipt) for value in ("private-account", "private-session", calls[-1][1], TEXT))
    assert client.invoke_native_turn(turn, authorization=task.authorization).error_code == "AGY_STREAM_REPLAY"
    assert turn.check_terminal(raw, returncode=0, capture_complete=True).error_code == "AGY_STREAM_REPLAY"
    assert [args[0] for args, _ in calls] == ["--version", "--help", "--input-format"]
    assert len({cwd for _, cwd in calls}) == 1 and not Path(calls[-1][1]).exists()


@pytest.mark.parametrize(("failure", "code"), [
    ("grant", "UNAUTHORIZED"),
    ("grant-ref", "UNAUTHORIZED"),
    ("wrong-pin", "AGY_TURN_BINDING_MISMATCH"),
    ("wrong-path", "AGY_TURN_BINDING_MISMATCH"),
    ("relative-executable", "AGY_CLI_EXECUTABLE_UNVERIFIED"),
    ("binary", "AGY_CLI_EXECUTABLE_UNVERIFIED"),
    ("missing-executable", "AGY_CLI_UNAVAILABLE"),
    ("model", "UNVERIFIED_MODEL_CONFIGURATION"),
    ("effort", "UNVERIFIED_MODEL_CONFIGURATION"),
    ("schema", "AGY_TERMINAL_SCHEMA_INVALID"),
    ("input-limit", "AGY_INPUT_LIMIT_EXCEEDED"),
    ("offline-claim", "AGY_STREAM_REPLAY"),
])
def test_agy_native_turn_blocks_before_launch(agy_preflight_case, monkeypatch, failure, code):
    client, _, task = agy_preflight_case
    grant = task.authorization
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("AGY process launched"))
    if failure == "model":
        task = replace(task, job=replace(task.job, configuration=replace(task.job.configuration, model="--continue")))
    if failure == "effort":
        task = replace(task, job=replace(task.job, configuration=replace(task.job.configuration, effort="auto")))
    if failure == "relative-executable":
        client = replace(client, executable=Path(client.executable).name)
    turn = client.prepare_turn(task)
    path = Path(client.executable)
    if failure == "grant":
        grant = replace(grant, allow_spend=False)
    if failure == "grant-ref":
        grant = replace(grant, authorization_ref="fixture:another-grant")
    if failure == "wrong-pin":
        client = replace(client, executable_sha256="2" * 64)
    if failure == "wrong-path":
        client = replace(client, executable=str(path.parent / "elsewhere" / path.name))
    if failure == "binary":
        path.write_bytes(b"different executable")
    if failure == "missing-executable":
        path.unlink()
    if failure == "schema":
        monkeypatch.setattr(type(turn), "output_schema", property(lambda _: {"type": "string"}))
    if failure == "input-limit":
        monkeypatch.setattr(creative_live, "CLI_MAX_BYTES", 10)
    if failure == "offline-claim":
        turn.check_terminal(b"", returncode=None, capture_complete=False)
    result = client.invoke_native_turn(turn, authorization=grant)
    assert result.status in ("BLOCK", "HOLD", "UNAVAILABLE") and result.error_code == code


@pytest.mark.parametrize(("failure", "code"), [
    ("missing-capability", "AGY_CLI_CAPABILITY_UNAVAILABLE"),
    ("probe-cwd-mutation", "AGY_CLI_PRIVATE_CWD_UNAVAILABLE"),
    ("optional-slash-flag", "AGY_AUTHENTICATED_CLI_SESSION_UNAVAILABLE"),
    ("timeout", "AGY_STREAM_TIMEOUT"),
    ("closed-pipes-unproven", "AGY_STREAM_INCOMPLETE"),
    ("killed", "AGY_STREAM_INCOMPLETE"),
    ("stdout-limit", "AGY_STREAM_LIMIT_EXCEEDED"),
    ("stderr-limit", "AGY_STREAM_LIMIT_EXCEEDED"),
    ("nonzero", "AGY_PROCESS_NONZERO_EXIT"),
    ("terminal-error", "AGY_TERMINAL_ERROR"),
    ("tool-permission", "AGY_STREAM_TOOL_ERROR"),
    ("tool-activity", "AGY_STREAM_TOOL_ACTIVITY"),
    ("cwd", "AGY_STREAM_SCOPE_MISMATCH"),
    ("permission-mode", "AGY_STREAM_SCOPE_MISMATCH"),
    ("conversation", "AGY_STREAM_INVALID"),
    ("duplicate-user", "AGY_STREAM_INVALID"),
    ("duplicate-result", "AGY_STREAM_INVALID"),
    ("incomplete", "AGY_STREAM_INCOMPLETE"),
    ("mutation", "AGY_CLI_EXECUTABLE_CHANGED"),
])
def test_agy_native_turn_holds_on_process_and_stream_failures(agy_preflight_case, monkeypatch, failure, code):
    client, _, task = agy_preflight_case
    turn = client.prepare_turn(task)
    calls = []

    def process(argv, *, input, cwd, timeout, max_bytes, detach_terminal):
        calls.append(argv[1])
        assert detach_terminal and max_bytes == creative_live.CLI_MAX_BYTES
        if argv[1] == "--version":
            return subprocess.CompletedProcess(argv, 0, b"2.41.0", b"")
        if argv[1] == "--help":
            help_text = AGY_HELP
            if failure == "missing-capability":
                help_text = help_text.replace(b"--sandbox", b"--other-flag")
            if failure == "optional-slash-flag":
                help_text = help_text.replace(b"--disable-slash-commands", b"--other-flag")
            if failure == "probe-cwd-mutation":
                (Path(cwd) / "unexpected").touch()
            return subprocess.CompletedProcess(argv, 0, help_text, b"")
        assert argv[1] == "--input-format" and timeout == 120
        assert ("--disable-slash-commands" in argv) == (failure != "optional-slash-flag")
        if failure == "timeout":
            raise creative_live._CLIStopped("UNKNOWN_EFFECT", capture_issue="TIMEOUT")
        if failure == "closed-pipes-unproven":
            raise creative_live._CLIStopped("UNKNOWN_EFFECT")
        events, _ = agy_terminal_fixture(turn, streamed_prompt=True)
        events[0]["init"]["cwd"] = "/private/unexpected" if failure == "cwd" else cwd
        if failure == "permission-mode":
            events[0]["init"]["permission_mode"] = "always-proceed"
        stderr = b""
        if failure == "mutation":
            Path(client.executable).write_bytes(b"changed during turn")
        if failure == "conversation":
            events[-1]["result"]["conversation_id"] = "wrong-conversation"
        if failure == "terminal-error":
            events[-1]["result"].update(status="ERROR", response="", error="private quota text")
        if failure == "tool-permission":
            stderr = b"Permission denied for private command"
        if failure == "tool-activity":
            # A tool is a separate completed step, not a type change to the
            # in-flight agent_response step (which is structurally invalid).
            events.insert(-1, {"event": "step_update", "step_update": {
                "conversation_id": AGY_CONVERSATION_ID, "step_index": 5,
                "step_type": "tool", "state": "DONE", "tool_name": "run_command",
            }})
        if failure == "duplicate-user":
            events.insert(2, {"event": "step_update", "step_update": {**events[1]["step_update"], "step_index": 10}})
        if failure == "duplicate-result":
            events.append(events[-1])
        raw = agy_stdout(events)
        if failure == "incomplete":
            raw = raw[:-1]
        if failure == "stdout-limit":
            raw = b"x" * (max_bytes + 1)
        if failure == "stderr-limit":
            stderr = b"x" * (max_bytes + 1)
        returncode = {"nonzero": 1, "terminal-error": 1, "killed": -9}.get(failure, 0)
        return subprocess.CompletedProcess(argv, returncode, raw, stderr)

    monkeypatch.setattr(creative_live, "_bounded_cli_process", process)
    result = client.invoke_native_turn(turn, authorization=task.authorization)
    assert result.error_code == code
    assert "private quota text" not in repr(result) and "private command" not in repr(result)
    if failure in ("missing-capability", "probe-cwd-mutation"):
        assert calls == ["--version", "--help"]
        return
    expected_status = {"terminal-error": "ERROR", "optional-slash-flag": "SUCCESS"}.get(failure, "UNKNOWN")
    assert (result.status, result.effect, result.terminal_status) == ("HOLD", "UNKNOWN_EFFECT", expected_status)
    assert client.invoke_native_turn(turn, authorization=task.authorization).error_code == "AGY_STREAM_REPLAY"
    assert calls == ["--version", "--help", "--input-format"]


@pytest.mark.parametrize(("streamed_prompt", "newline"), [(False, b"\r\n"), (True, b"\n")])
def test_agy_structural_success_is_private_task_bound_hold_and_one_shot(
    agy_preflight_case, monkeypatch, streamed_prompt, newline,
):
    client, _, task = agy_preflight_case
    monkeypatch.setattr(creative_live, "_bounded_cli_process", lambda *a, **kw: pytest.fail("AGY process launched"))
    turn = client.prepare_turn(task)
    events, envelope = agy_terminal_fixture(turn, streamed_prompt=streamed_prompt)
    events[0]["init"].update(authenticated=True, account="private-account", session="private-session")
    raw = agy_stdout(events, newline)
    packet = json.loads(turn.canonical_payload)
    assert packet["request"] == task.job.request.to_dict()
    assert (packet["task_sha256"], packet["stream_nonce"]) == (task.digest, turn.stream_nonce)
    assert task.authorization.authorization_ref.encode() not in turn.canonical_payload
    assert turn.output_schema["properties"]["output"] == StructuredModelRequest(
        task.job.configuration, task.job.request,
    ).output_schema

    receipt = turn.check_terminal(raw, returncode=0, capture_complete=True)
    assert (receipt.status, receipt.terminal_status, receipt.effect, receipt.error_code) == (
        "HOLD", "SUCCESS", "UNKNOWN_EFFECT", "AGY_AUTHENTICATED_CLI_SESSION_UNAVAILABLE",
    )
    assert (receipt.task_sha256, receipt.job_sha256, receipt.source_brief_sha256, receipt.attempt) == (
        task.digest, task.job.digest, task.job.source_brief_digest, task.job.attempt,
    )
    assert receipt.requested_configuration_sha256 == sha256_bytes(canonical_bytes(task.job.configuration.to_dict()))
    assert receipt.pinned_executable_sha256 == client.executable_sha256
    assert receipt.output_schema_sha256 == sha256_bytes(canonical_bytes(turn.output_schema))
    assert receipt.stdout_sha256 == sha256_bytes(raw) and receipt.stream_nonce == envelope["stream_nonce"]
    public = json.dumps(asdict(receipt))
    assert all(value not in public for value in (
        TEXT, task.authorization.authorization_ref, "private-account", "private-session", AGY_CONVERSATION_ID,
        "/fixture/project", envelope["output"]["drafts"][0]["title"],
    ))
    with pytest.raises(FrozenInstanceError):
        receipt.status = "OK"
    assert turn.check_terminal(raw, returncode=0, capture_complete=True).error_code == "AGY_STREAM_REPLAY"
    fresh = client.prepare_turn(task)
    assert fresh.stream_nonce != turn.stream_nonce
    assert fresh.check_terminal(raw, returncode=0, capture_complete=True).error_code == "AGY_TERMINAL_BINDING_MISMATCH"


def test_agy_correlates_nested_ids_when_init_omits_id(agy_preflight_case):
    client, _, task = agy_preflight_case
    turn = client.prepare_turn(task)
    events, _ = agy_terminal_fixture(turn)
    del events[0]["conversation_id"]
    del events[-1]["result"]["usage"]
    receipt = turn.check_terminal(agy_stdout(events), returncode=0, capture_complete=True)
    assert (receipt.status, receipt.terminal_status) == ("HOLD", "SUCCESS")


def test_agy_rejects_two_results_in_one_capture(agy_preflight_case):
    client, _, task = agy_preflight_case
    turn = client.prepare_turn(task)
    events, _ = agy_terminal_fixture(turn, streamed_prompt=True)
    second_step = {**events[1]["step_update"], "step_index": 3}
    second_result = {**events[-1]["result"], "num_turns": 2}
    events.extend([{"event": "step_update", "step_update": second_step},
                   {"event": "result", "result": second_result}])
    receipt = turn.check_terminal(agy_stdout(events), returncode=0, capture_complete=True)
    assert (receipt.status, receipt.terminal_status, receipt.error_code) == ("HOLD", "UNKNOWN", "AGY_STREAM_INVALID")


@pytest.mark.parametrize(("index", "where", "value"), [
    (0, None, "different-init-id"),
    (0, None, "   "),
    (0, None, False),
    (0, "init", "conflicting-nested-init-id"),
    (1, "step_update", "different-step-id"),
    (1, "step_update", None),
    (-1, "result", "different-result-id"),
    (-1, "result", "bad\nresult-id"),
    (-1, "result", None),
])
def test_agy_rejects_conflicting_or_malformed_conversation_ids(agy_preflight_case, index, where, value):
    client, _, task = agy_preflight_case
    turn = client.prepare_turn(task)
    events, _ = agy_terminal_fixture(turn)
    location = events[index][where] if where else events[index]
    if value is None:
        del location["conversation_id"]
    else:
        location["conversation_id"] = value
    receipt = turn.check_terminal(agy_stdout(events), returncode=0, capture_complete=True)
    assert (receipt.status, receipt.terminal_status, receipt.error_code) == ("HOLD", "UNKNOWN", "AGY_STREAM_INVALID")


def test_agy_concurrent_capture_can_only_be_claimed_once(agy_preflight_case, monkeypatch):
    client, _, task = agy_preflight_case
    turn = client.prepare_turn(task)
    events, _ = agy_terminal_fixture(turn)
    raw = agy_stdout(events)
    parsing, release = threading.Event(), threading.Event()
    parse = creative_live._agy_terminal

    def wait_for_second_claim(*args, **kwargs):
        parsing.set()
        assert release.wait(timeout=5)
        return parse(*args, **kwargs)

    monkeypatch.setattr(creative_live, "_agy_terminal", wait_for_second_claim)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(turn.check_terminal, raw, returncode=0, capture_complete=True)
        try:
            assert parsing.wait(timeout=5)
            second = turn.check_terminal(raw, returncode=0, capture_complete=True)
            assert (second.status, second.terminal_status, second.error_code) == ("HOLD", "UNKNOWN", "AGY_STREAM_REPLAY")
        finally:
            release.set()
        assert first.result(timeout=5).terminal_status == "SUCCESS"


@pytest.mark.parametrize("changed_field", ["attempt", "source", "model", "effort"])
def test_agy_fresh_challenge_rejects_rebound_task(agy_preflight_case, changed_field):
    client, _, task = agy_preflight_case
    job = task.job
    changes = {
        "attempt": {"attempt": job.attempt + 1},
        "source": {"provenance": replace(job.provenance, source_sha256="3" * 64)},
        "model": {"configuration": replace(job.configuration, model="other-model")},
        "effort": {"configuration": replace(job.configuration, effort="other-effort")},
    }
    turn = client.prepare_turn(replace(task, job=replace(job, **changes[changed_field])))
    events, envelope = agy_terminal_fixture(turn)
    assert turn.task.digest != task.digest
    envelope["task_sha256"] = task.digest
    receipt = turn.check_terminal(agy_stdout(events), returncode=0, capture_complete=True)
    assert (receipt.status, receipt.terminal_status, receipt.error_code) == (
        "HOLD", "UNKNOWN", "AGY_TERMINAL_BINDING_MISMATCH",
    )


@pytest.mark.parametrize("grant", [
    InvocationAuthorization(), InvocationAuthorization(True, False, "fixture:network"),
    InvocationAuthorization(False, True, "fixture:spend"), InvocationAuthorization(False, False, "fixture:ref"),
])
def test_agy_turn_requires_an_explicit_network_and_spend_grant(agy_preflight_case, grant):
    client, _, task = agy_preflight_case
    with pytest.raises(CreativeValidationError, match="UNAUTHORIZED"):
        client.prepare_turn(replace(task, authorization=grant))
    with pytest.raises(CreativeValidationError, match="explicit reference"):
        InvocationAuthorization(True, True)


@pytest.mark.parametrize(("change", "expected"), [
    (lambda events: events[:-1], "AGY_STREAM_INCOMPLETE"),
    (lambda events: events[1:], "AGY_STREAM_INVALID"),
    (lambda events: events[:1] + events, "AGY_STREAM_INVALID"),
    (lambda events: events + events[-1:], "AGY_STREAM_INVALID"),
    (lambda events: events[-1:] + events[:-1], "AGY_STREAM_INVALID"),
    (lambda events: events[:1] + events[-1:] + events[1:-1], "AGY_STREAM_INVALID"),
    (lambda events: events[:-1] + events[2:], "AGY_STREAM_INVALID"),
    (lambda events: events[:1] + [{"event": "unsupported"}] + events[1:], "AGY_STREAM_INVALID"),
])
def test_agy_rejects_invalid_event_order_and_cardinality(agy_preflight_case, change, expected):
    client, _, task = agy_preflight_case
    turn = client.prepare_turn(task)
    events, _ = agy_terminal_fixture(turn)
    receipt = turn.check_terminal(agy_stdout(change(events)), returncode=0, capture_complete=True)
    assert (receipt.status, receipt.terminal_status, receipt.error_code) == ("HOLD", "UNKNOWN", expected)


@pytest.mark.parametrize(("failure", "expected"), [
    ("partial-line", "AGY_STREAM_INCOMPLETE"),
    ("capture-incomplete", "AGY_STREAM_INCOMPLETE"),
    ("unknown-exit", "AGY_STREAM_INCOMPLETE"),
    ("timeout", "AGY_STREAM_TIMEOUT"),
    ("exit", "AGY_STREAM_INVALID"),
    ("blank-line", "AGY_STREAM_INVALID"),
    ("bare-cr", "AGY_STREAM_INVALID"),
    ("duplicate-key", "AGY_STREAM_INVALID"),
    ("invalid-utf8", "AGY_STREAM_INVALID"),
    ("stream-limit", "AGY_STREAM_LIMIT_EXCEEDED"),
    ("line-limit", "AGY_STREAM_LIMIT_EXCEEDED"),
    ("event-limit", "AGY_STREAM_LIMIT_EXCEEDED"),
    ("error", "AGY_TERMINAL_ERROR"),
    ("unknown-status", "AGY_STREAM_INVALID"),
    ("zero-turns", "AGY_STREAM_INVALID"),
    ("invalid-usage", "AGY_STREAM_INVALID"),
    ("tool-error", "AGY_STREAM_TOOL_ERROR"),
    ("tool-permission", "AGY_STREAM_TOOL_ERROR"),
    ("text-only-envelope", "AGY_TERMINAL_SCHEMA_INVALID"),
    ("bogus-envelope", "AGY_TERMINAL_SCHEMA_INVALID"),
    ("extra-envelope-field", "AGY_TERMINAL_SCHEMA_INVALID"),
    ("missing-schema", "AGY_TERMINAL_SCHEMA_INVALID"),
    ("wrong-init-schema", "AGY_TERMINAL_SCHEMA_INVALID"),
    ("wrong-schema", "AGY_TERMINAL_SCHEMA_INVALID"),
    ("invalid-output", "AGY_TERMINAL_SCHEMA_INVALID"),
])
def test_agy_rejects_unresolved_or_invalid_terminal_evidence(agy_preflight_case, monkeypatch, failure, expected):
    client, _, task = agy_preflight_case
    turn = client.prepare_turn(task)
    events, envelope = agy_terminal_fixture(turn)
    result = events[-1]["result"]
    if failure in ("error", "timeout"):
        result.update(status="ERROR", response="", error="private-provider-error: quota", num_turns=0)
        del result["structured_output"]
        del result["json_schema"]
    elif failure == "unknown-status":
        result["status"] = "FAILED"
    elif failure == "zero-turns":
        result["num_turns"] = 0
    elif failure == "invalid-usage":
        result["usage"]["input_tokens"] = True
    elif failure == "tool-error":
        events[2]["step_update"].update(step_type="tool", tool_name="run_command", tool_info={
            "name": "run_command", "error": {"type": "PERMISSION_DENIED", "message": "private-tool-error"},
        })
    elif failure == "tool-permission":
        events[2]["step_update"].update(step_type="tool", tool_name="ask_permission")
    elif failure == "text-only-envelope":
        del result["structured_output"]
    elif failure == "bogus-envelope":
        result["structured_output"] = result["response"]
    elif failure == "extra-envelope-field":
        envelope["authenticated"] = True
    elif failure == "missing-schema":
        del result["json_schema"]
    elif failure == "wrong-init-schema":
        events[0]["init"]["json_schema"]["additionalProperties"] = True
    elif failure == "wrong-schema":
        result["json_schema"]["additionalProperties"] = True
    elif failure == "invalid-output":
        envelope["output"]["drafts"][0]["assumptions"] = [False]
    elif failure == "event-limit":
        monkeypatch.setattr(creative_live, "_AGY_MAX_EVENTS", len(events) - 1)
    raw = agy_stdout(events)
    raw = {
        "partial-line": lambda: raw[:-1],
        "blank-line": lambda: b"\r\n" + raw,
        "bare-cr": lambda: raw.replace(b'{"conversation_id"', b'{\r"conversation_id"', 1),
        "duplicate-key": lambda: raw.replace(b'"event":"init"', b'"event":"init","event":"init"'),
        "invalid-utf8": lambda: b"\xff" + raw,
        "stream-limit": lambda: b" " * (creative_live.CLI_MAX_BYTES + 1),
        "line-limit": lambda: b" " * creative_live._AGY_MAX_LINE_BYTES + b"\n",
    }.get(failure, lambda: raw)()
    returncode = {"error": 1, "exit": 1, "unknown-exit": None}.get(failure, 0)
    receipt = turn.check_terminal(raw, returncode=returncode,
                                  capture_complete=failure != "capture-incomplete", timed_out=failure == "timeout")
    status = "ERROR" if failure == "error" else "UNKNOWN"
    assert (receipt.status, receipt.effect, receipt.terminal_status, receipt.error_code) == (
        "HOLD", "UNKNOWN_EFFECT", status, expected,
    )
    assert "quota" not in repr(receipt) and "private-tool-error" not in repr(receipt)
    assert turn.check_terminal(raw, returncode=0, capture_complete=True).error_code == "AGY_STREAM_REPLAY"


def test_c1_brain_cdp_claims_bind_task_and_exact_origin_without_permitting_send():
    config = ModelConfiguration("chatgpt-brain-cdp")
    cycle = cycle_job(seed_options={"purpose": "PRODUCTION", "configuration": config})
    request = creative_v2._cycle_request(cycle, "explore", "EXPLORE")
    task = AIReasoningTask(creative_v2._cycle_stage_job(cycle, request),
                           InvocationAuthorization(True, True, "fixture:c1"))
    origin = wakehub_turn.OriginPins(
        "fixture:account", "fixture:chat-a", "fixture:session", 1, "fixture:registration",
        "fixture:custody", "fixture:profile", "fixture:context", "fixture:tab", "fixture:composer",
    )
    scope = wakehub_turn.TurnScope(origin, "ef02-completion-v1:" + "a" * 64, task.digest)
    grant = wakehub_turn.SignedWakeGrant(scope, "fixture:grant", "fixture:issuer", "fixture:key", b"claim", 1, 9)
    reservation = wakehub_turn.DurableReservation(scope, "fixture:reservation", grant.grant_ref, "fixture:durable", 2)
    observation = wakehub_turn.BrowserObservation(scope, "fixture:surface", 1, 1, True, True)
    attestation = wakehub_turn.NativeAttestation(
        scope, wakehub_turn.AttestationPhase.PRE_SEND, wakehub_turn.EvidenceSource.NATIVE_PRIVATE_PROVIDER,
        "fixture:attestation", grant.issuer_ref, grant.key_id, b"claim", grant.grant_ref,
        reservation.reservation_ref, observation.observation_ref, 3, 9,
    )
    evidence = wakehub_turn.TurnEvidence(observation, grant, reservation, attestation)

    def pre(candidate=task, expected_scope=scope, claims=evidence):
        return assess_brain_cdp_turn(candidate, expected_scope, claims, now=5)

    code = wakehub_turn.GateCode
    unavailable = pre()
    assert unavailable.status is wakehub_turn.GateStatus.UNAVAILABLE
    assert unavailable.code is code.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE
    assert not unavailable.send_allowed and unavailable.authenticated_user_turn is None
    for change in ({"conversation_id": "fixture:chat-b"}, {"account_id": "fixture:other-account"},
                   {"session_ref": "fixture:other-session"}):
        assert pre(expected_scope=replace(scope, origin=replace(origin, **change))).code is code.SCOPE_MISMATCH
    stale = replace(task, job=replace(task.job, attempt=task.job.attempt + 1))
    assert pre(candidate=stale).code is code.MESSAGE_DIGEST_MISMATCH
    assert pre(claims=replace(evidence, grant=None)).code is code.GRANT_MISSING
    assert pre(candidate=replace(task, authorization=InvocationAuthorization())).code is code.GRANT_MISSING
    assert pre(claims=replace(evidence, grant=replace(grant, use_count=1))).code is code.GRANT_REUSED
    assert pre(claims=replace(evidence, attestation=replace(
        attestation, source=wakehub_turn.EvidenceSource.SYNTHETIC_FIXTURE,
    ))).code is code.WRONG_AUTHORITY_SOURCE

    post_evidence = replace(evidence, attestation=replace(attestation, phase=wakehub_turn.AttestationPhase.POST_SEND))
    claimed_turn = wakehub_turn.ChatGPTUserTurnRef(
        scope, "fixture:user-turn", "fixture:message", attestation.attestation_ref,
        grant.grant_ref, reservation.reservation_ref, wakehub_turn.EvidenceSource.NATIVE_PRIVATE_PROVIDER,
    )
    for outcome, user_turn, expected in (
        (wakehub_turn.SendOutcome.CLICK_REPORTED, None, code.USER_TURN_MISSING),
        (wakehub_turn.SendOutcome.CLICK_REPORTED, claimed_turn, code.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE),
        (wakehub_turn.SendOutcome.CLICK_AMBIGUOUS, claimed_turn, code.CLICK_AMBIGUOUS_NO_REPLAY),
        (wakehub_turn.SendOutcome.TRANSPORT_TIMEOUT, None, code.TRANSPORT_TIMEOUT_NO_REPLAY),
    ):
        result = assess_brain_cdp_turn(task, scope, post_evidence, now=5, outcome=outcome, user_turn=user_turn)
        assert result.status is wakehub_turn.GateStatus.UNKNOWN_EFFECT and result.code is expected
        assert result.authenticated_user_turn is None and not result.retry_allowed


@pytest.mark.parametrize("count", [2, 3])
def test_r2_autonomous_diverge_critic_select_develop_and_zero_call_reuse(tmp_path, count):
    job, client = cycle_job(count=count), CycleFakeClient()
    sidecars = CreativeSidecarStore(tmp_path)
    result = run_creative_cycle(job, sidecars, cycle_adapter(client))
    assert result.status == "TEST_ONLY_MOCK" and result.failure is None
    assert len(client.calls) == count + 3
    assert [s.job.request.operation for s in result.steps] == ["EXPLORE"] + ["CRITIQUE"] * count + ["SELECT", "DEVELOP"]
    assert len({s.job.slot for s in result.steps}) == len(result.steps)
    assert len({s.reasoning.provider_request_id for s in result.steps}) == len(result.steps)
    assert all(s.job.request.intent == job.seed.request.intent for s in result.steps)
    assert all(s.job.request.brief == job.seed.request.brief for s in result.steps)
    assert all(s.job.request.cycle.research_status == "UNKNOWN" for s in result.steps)
    explored, selector, developed = result.steps[0], result.steps[-2], result.steps[-1]
    assert len({c.family for c in explored.reasoning.concepts()}) == count
    assert selector.reasoning.selection.selection == explored.outputs[-1]  # No index-zero/prose relay.
    assert result.decision.kind == "AI_RECOMMENDED" and result.decision.actor.kind == "AI"
    assert sidecars.verify_inference_decision(result.decision) == selector.reasoning
    assert result.decision.inference.receipt_digest == selector.reasoning.digest
    assert result.decision.inference.mode == "TEST_ONLY_MOCK"
    assert selector.reasoning.digest not in selector.reasoning.selection.critique_receipts
    assert selector.reasoning.selection.critique_receipts == tuple(s.reasoning.digest for s in result.steps[1:count + 1])
    child = sidecars.creative.load_concept(result.output.concept_id, result.output.revision)
    assert child.parent.digest == explored.outputs[-1].concept_digest and child.revision == 2
    assert developed.reasoning.request.cycle.selector_receipt == selector.reasoning.digest
    assert child.factual_status == "UNVERIFIED" and child.rights_status == "UNKNOWN"
    assert A03CreativeCycleResult.from_dict(json.loads(json.dumps(result.to_dict()))) == result
    assert all(json.loads(s.reasoning.response_json)["output_json"].startswith('{\n') for s in result.steps)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert run_creative_cycle(job, CreativeSidecarStore(tmp_path), cycle_adapter(client)) == result
    assert len(client.calls) == count + 3
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*.json")} == before


def test_r2_bounded_revision_recritiques_exact_child_before_independent_reselection(tmp_path):
    job, client = cycle_job(), CycleFakeClient(actions=("REVISE", "SELECT"))
    sidecars = CreativeSidecarStore(tmp_path)
    result = run_creative_cycle(job, sidecars, cycle_adapter(client))
    assert result.status == "TEST_ONLY_MOCK"
    assert [s.job.request.operation for s in result.steps] == ["EXPLORE", "CRITIQUE", "CRITIQUE", "SELECT",
                                                             "DEVELOP", "CRITIQUE", "SELECT", "DEVELOP"]
    assert result.output.revision == 3
    first_select, revision, critic, second_select = result.steps[3:7]
    assert revision.reasoning.request.instruction == first_select.reasoning.selection.revision_instruction
    assert critic.reasoning.request.source == revision.reasoning.proposed_revision()
    assert second_select.reasoning.request.cycle.critics[-1].receipt_digest == critic.reasoning.digest
    assert second_select.reasoning.request.cycle.critics[0].receipt_digest == result.steps[1].reasoning.digest
    assert sidecars.creative.load_concept(result.output.concept_id, 3).parent.digest == revision.outputs[0].concept_digest
    assert run_creative_cycle(job, sidecars, cycle_adapter(client)) == result and len(client.calls) == 8


@pytest.mark.parametrize("action,budget,expected", [("STOP", 1, "AI_STOP"), ("REVISE", 0, "REVISION_LIMIT"),
                                                  ("REVISE", 2, "REVISION_LIMIT")])
def test_r2_model_stop_and_revision_budget_are_terminal_without_development_completion(tmp_path, action, budget, expected):
    client = CycleFakeClient(actions=(action,))
    result = run_creative_cycle(cycle_job(max_revisions=budget), tmp_path, cycle_adapter(client))
    assert result.status == "STOPPED" and result.failure == expected
    assert result.output is None and result.decision is None
    assert result.steps[-1].job.request.operation == "SELECT"
    assert sum(s.job.request.cycle.step.startswith("revise:") for s in result.steps) == (budget if action == "REVISE" else 0)


@pytest.mark.parametrize("fault,step,failure", [
    ("unknown-field", "select:0", "INVALID_STRUCTURED_OUTPUT"),
    ("score-only", "select:0", "INVALID_STRUCTURED_OUTPUT"),
    ("wrong-concept", "select:0", "INVALID_STRUCTURED_OUTPUT"),
    ("wrong-critics", "select:0", "INVALID_STRUCTURED_OUTPUT"),
    ("missing-critics", "select:0", "INVALID_STRUCTURED_OUTPUT"),
    ("citation", "critic:0:0", "INVALID_STRUCTURED_OUTPUT"),
    ("duplicate-families", "explore", "INVALID_STRUCTURED_OUTPUT"),
    ("invented-draft-source", "explore", "INVALID_STRUCTURED_OUTPUT"),
    ("replayed-id", "select:0", "REPLAYED_INFERENCE"),
    ("missing-receipt", "select:0", "UNBOUND_PROVIDER"),
    ("mixed-observation", "select:0", "MIXED_INFERENCE_IDENTITY"),
    ("mock-as-model", "select:0", "INVALID_MODEL_RESPONSE"),
    ("timeout", "select:0", "MODEL_ERROR"),
    ("refusal", "select:0", "MODEL_REFUSED"),
    ("unknown-effect", "select:0", "UNKNOWN_EFFECT"),
])
def test_r2_negative_observed_steps_hold_and_never_retry_or_complete(tmp_path, fault, step, failure):
    job, client = cycle_job(), CycleFakeClient(fault=fault, fault_step=step)
    sidecars = CreativeSidecarStore(tmp_path)
    result = run_creative_cycle(job, sidecars, cycle_adapter(client))
    assert result.status == "HOLD" and result.failure == failure
    assert result.output is None and result.decision is None
    assert result.steps[-1].job.request.cycle.step == step
    calls = len(client.calls)
    assert run_creative_cycle(job, sidecars, cycle_adapter(client)) == result
    assert len(client.calls) == calls
    if fault not in ("timeout",):
        assert result.steps[-1].reasoning.response_json is not None


@pytest.mark.parametrize("grant", [None, InvocationAuthorization(), InvocationAuthorization(True, False, "fixture:grant")])
def test_r2_absent_runner_grant_stops_before_any_production_inference(tmp_path, grant):
    client = CycleFakeClient()
    result = run_creative_cycle(cycle_job(seed_options={"purpose": "PRODUCTION"}), tmp_path,
                                cycle_adapter(client), authorization=grant)
    assert result.status == "HOLD" and result.failure == "UNAUTHORIZED"
    assert result.steps == () and client.calls == []


def test_r2_mock_transport_is_never_promoted_to_model_even_with_runner_grant(tmp_path):
    client = CycleFakeClient()
    result = run_creative_cycle(cycle_job(seed_options={"purpose": "PRODUCTION"}), tmp_path,
        cycle_adapter(client), authorization=InvocationAuthorization(True, True, "fixture:grant"))
    assert result.status == "HOLD" and result.failure == "ADAPTER_MODE_MISMATCH"
    assert result.steps == () and result.observed is None and client.calls == []


def test_r2_asserted_model_without_independent_session_effort_evidence_cannot_complete(tmp_path):
    class Unverified(CycleFakeClient):
        execution_mode = "MODEL"

        def complete(self, request):
            response = super().complete(request)
            return replace(response, observation=ProviderObservation(request.config.provider, request.config.model, "MODEL"))
    client = Unverified()
    result = run_creative_cycle(cycle_job(seed_options={"purpose": "PRODUCTION"}), tmp_path,
        cycle_adapter(client), authorization=InvocationAuthorization(True, True, "fixture:grant"))
    assert result.status == "HOLD" and result.output is None and len(client.calls) == 0
    assert result.failure == "UNVERIFIED_MODEL_ADAPTER"


def test_r2_fixture_receipt_uses_its_own_transport_verifier_without_model_admission(monkeypatch):
    config = ModelConfiguration("fixture-provider", "fixture-model", "fixture-effort")
    request = creative_v2._cycle_request(cycle_job(seed_options={"configuration": config}), "explore", "EXPLORE")
    grant = InvocationAuthorization(authorization_ref="fixture:offline")
    attempt = "TEST_ONLY_MOCK:fixture-attempt"

    class FixtureTransport(CycleFakeClient):
        def invoke(self, model_request, authorization):
            response = self.complete(model_request)
            packet = canonical_bytes(model_request.to_dict())
            schema = canonical_bytes(model_request.output_schema)
            output = response.output_json.encode()
            transport_digest = sha256_bytes(b"TEST_ONLY_MOCK fixture transport")
            execution = creative_v2.CLIExecutionObservation(
                "TEST_ONLY_MOCK", transport_digest, "fixture-version", transport_digest, transport_digest,
                request.digest, sha256_bytes(canonical_bytes(config.to_dict())), sha256_bytes(packet),
                sha256_bytes(schema), "PROCESS_INVOKED", stdout_sha256=sha256_bytes(output),
                session_sha256=sha256_bytes(attempt.encode() + packet), observed_model=config.model,
                observed_effort=config.effort, cli_thread_id=attempt, authorization_ref=authorization.authorization_ref,
            )
            return replace(response, execution=execution, cli_stdin_json=packet.decode(), cli_schema_json=schema.decode(),
                           cli_stdout_base64=base64.b64encode(output).decode())

    def verify_receipt(model_request, response):
        packet = canonical_bytes(model_request.to_dict())
        if (response.cli_stdin_json != packet.decode()
                or response.cli_schema_json != canonical_bytes(model_request.output_schema).decode()
                or base64.b64decode(response.cli_stdout_base64) != response.output_json.encode()
                or response.execution.cli_thread_id != attempt
                or response.execution.session_sha256 != sha256_bytes(attempt.encode() + packet)):
            raise CreativeValidationError("fixture native receipt mismatch")

    transport = creative_live._SourceTransport(
        "fixture-wire", config.provider, FixtureTransport, None, lambda _: "TEST_ONLY_MOCK",
        FixtureTransport.invoke, verify_receipt, lambda _: "TEST_ONLY_MOCK",
    )
    client = FixtureTransport()
    monkeypatch.setattr(creative_live, "_SOURCE_TRANSPORTS", (*creative_live._SOURCE_TRANSPORTS, transport))
    result = reason_v2(request, LiveCreativeReasoner(config, client, authorization=grant))
    assert result.state == "OK" and result.observation == MOCK and len(client.calls) == 1
    assert creative_live._receipt_transport(config).transport_id == "fixture-wire"
    assert ReasoningResult.from_dict(result.to_dict()).digest == result.digest
    alleged_model = ProviderObservation(config.provider, config.model, "MODEL")
    alleged_execution = replace(result.execution, kind="AUTHENTICATED_CLI_PROBE")
    assert verified_model_transport(config, alleged_model, alleged_execution) is None

    wire = result.to_dict()
    raw = json.loads(wire["response_json"])
    raw["cli_schema_json"] = "{}"
    raw["execution"]["schema_sha256"] = sha256_bytes(b"{}")
    wire["execution"] = raw["execution"]
    wire["response_json"] = json.dumps(raw)
    with pytest.raises(CreativeValidationError, match="fixture native receipt mismatch"):
        ReasoningResult.from_dict(wire)


def test_r2_external_source_bytes_are_separate_from_generated_hypotheses(tmp_path):
    content = "Exact external source quotation e\u0301\r\n"
    source = CreativeEvidenceSource("external:rain", content, sha256_bytes(content.encode("utf-8")), "fixture:verified-source-bytes")
    brief = {**intake_brief(TEXT), "source_refs": [source.source_ref]}
    job, client = cycle_job(brief=brief, evidence=(source,)), CycleFakeClient()
    result = run_creative_cycle(job, tmp_path, cycle_adapter(client))
    assert result.status == "TEST_ONLY_MOCK"
    for stage in result.steps:
        assert stage.reasoning.request.cycle.research_status == "VERIFIED_SOURCE_BYTES"
        assert stage.reasoning.request.cycle.evidence == (source,)
        assert stage.reasoning.evidence_refs == (source.source_ref,)
        assert stage.reasoning.hypotheses and source.content not in stage.reasoning.hypotheses
    assert CreativeSidecarStore(tmp_path).load_cycle(job).job.evidence[0].content == content
    with pytest.raises(CreativeValidationError):
        replace(source, content="rebound source bytes")
    with pytest.raises(CreativeValidationError):
        replace(source, verification_ref="UNKNOWN")
    with pytest.raises(CreativeValidationError):
        cycle_job(evidence=(source,))  # Citation absent from the pinned original brief.


def test_r2_conflicting_child_retains_observed_model_output_and_all_prior_bytes(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path)
    prior_bytes = {}

    def competing_child(request, output):
        if request.creative.operation == "DEVELOP":
            source = request.creative.source
            prior_bytes.update({p: p.read_bytes() for p in tmp_path.rglob("*.json")})
            sidecars.creative.save_concept(revise_concept(source, {"title": "competing immutable child"}))
    job, client = cycle_job(), CycleFakeClient(callback=competing_child)
    result = run_creative_cycle(job, sidecars, cycle_adapter(client))
    assert result.status == "HOLD" and result.failure == "ARTIFACT_CONFLICT"
    assert result.observed.state == "OK" and result.observed.response_json is not None
    assert result.halted_step.request.operation == "DEVELOP"
    source = result.halted_step.request.source
    assert sidecars.creative.load_concept(source.concept_id, 2).title == "competing immutable child"
    assert all(p.read_bytes() == content for p, content in prior_bytes.items())
    assert run_creative_cycle(job, sidecars, cycle_adapter(client)) == result and len(client.calls) == 5


def test_r2_interrupted_attempt_reconstructs_saved_prefix_and_holds_without_replay(tmp_path):
    def crash(request, output):
        if request.creative.operation == "CRITIQUE":
            raise KeyboardInterrupt("TEST_ONLY_MOCK crash after claim, ambiguous effect")
    job, client = cycle_job(), CycleFakeClient(callback=crash)
    with pytest.raises(KeyboardInterrupt):
        run_creative_cycle(job, tmp_path, cycle_adapter(client))
    calls = len(client.calls)
    held = run_creative_cycle(job, tmp_path, cycle_adapter(client))
    assert held.status == "HOLD" and held.failure == "UNRESOLVED_CREATIVE_STEP"
    assert len(held.steps) == 1 and held.halted_step.request.operation == "CRITIQUE"
    assert held.observed is None and len(client.calls) == calls


@pytest.mark.parametrize("mutation", ["brief", "configuration", "critic", "output", "decision", "raw-output", "extra-step"])
def test_r2_receipt_rejects_stale_rebound_or_spoofed_wire_evidence(tmp_path, mutation):
    result = run_creative_cycle(cycle_job(), tmp_path, cycle_adapter(CycleFakeClient()))
    data = result.to_dict()
    if mutation == "brief":
        data["steps"][-1]["reasoning"]["request"]["brief"]["objective"] = "changed brief"
    elif mutation == "configuration":
        data["steps"][-1]["reasoning"]["configuration"]["model"] = "other model"
    elif mutation == "critic":
        data["steps"][-2]["reasoning"]["request"]["cycle"]["critics"][0]["receipt_digest"] = "0" * 64
    elif mutation == "output":
        data["output"] = data["steps"][0]["outputs"][0]
    elif mutation == "decision":
        data["decision"]["kind"] = "AUTO_PINNED"
        data["decision"]["actor"] = {"kind": "SYSTEM", "ref": "score:index:0"}
    elif mutation == "raw-output":
        raw = json.loads(data["steps"][-1]["reasoning"]["response_json"])
        raw["output_json"] = raw["output_json"].replace("Silent rain", "rebound content")
        data["steps"][-1]["reasoning"]["response_json"] = json.dumps(raw)
    else:
        data["steps"].append(data["steps"][0])
    with pytest.raises(CreativeValidationError):
        A03CreativeCycleResult.from_dict(data)


@pytest.mark.parametrize("kind", ["cycle", "cycle-ref", "cycle-job", "cycle-grant", "observed-ref", "critic", "candidate", "brief"])
def test_r2_reuse_rechecks_saved_source_and_intermediate_bytes_without_model_calls(tmp_path, kind):
    sidecars, job, client = CreativeSidecarStore(tmp_path), cycle_job(), CycleFakeClient()
    result = run_creative_cycle(job, sidecars, cycle_adapter(client))
    source = result.steps[0].outputs[0]
    path = {"cycle": sidecars._path("cycle-results", result.digest),
            "cycle-ref": sidecars._path("cycle-refs", job.seed.slot),
            "cycle-job": sidecars._path("cycle-jobs", job.seed.slot),
            "cycle-grant": sidecars._path("cycle-grants", job.seed.slot),
            "observed-ref": sidecars._path("cycle-observed-refs", result.steps[1].job.slot),
            "critic": sidecars._path("reasoning", result.steps[1].reasoning.digest),
            "candidate": sidecars.creative._concept_path(source.concept_id, source.revision),
            "brief": sidecars.creative.root / "briefs" / f"{job.seed.source_brief_digest}.json"}[kind]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises((CreativeValidationError, VersionConflict)):
        run_creative_cycle(job, sidecars, cycle_adapter(client))
    assert len(client.calls) == 5


@pytest.mark.parametrize("kind", [A03CreativeCycleJob, A03CreativeCycleResult, CreativeCycleContext, CreativeModelSelection])
def test_r2_versioned_contracts_reject_missing_unknown_and_duplicate_fields(tmp_path, kind):
    result = run_creative_cycle(cycle_job(), tmp_path, cycle_adapter(CycleFakeClient()))
    record = {A03CreativeCycleJob: result.job, A03CreativeCycleResult: result,
              CreativeCycleContext: result.steps[-2].job.request.cycle,
              CreativeModelSelection: result.steps[-2].reasoning.selection}[kind]
    data = record.to_dict()
    with pytest.raises(CreativeValidationError):
        kind.from_dict({**data, "unrecognized": True})
    with pytest.raises(CreativeValidationError):
        kind.from_dict({k: v for k, v in data.items() if k != next(iter(data))})
    from ai_commerce.creative._validation import read_json
    key = next(iter(data))
    duplicate = json.dumps(data)[:-1] + "," + json.dumps(key) + ":" + json.dumps(data[key]) + "}"
    with pytest.raises(CreativeValidationError):
        kind.from_dict(read_json(duplicate))


def test_r2_cycle_attempt_cannot_rebind_brief_or_reuse_different_budget(tmp_path):
    job, client = cycle_job(), CycleFakeClient()
    run_creative_cycle(job, tmp_path, cycle_adapter(client))
    with pytest.raises(VersionConflict):
        run_creative_cycle(replace(job, max_revisions=2), tmp_path, cycle_adapter(client))
    changed = revise_brief(job.seed.request.brief, {"objective": "changed exact brief revision"})
    with pytest.raises(VersionConflict):
        run_creative_cycle(cycle_job(brief=changed), tmp_path, cycle_adapter(client))
    assert len(client.calls) == 5


def test_r2_silent_nonproduct_final_selection_is_consumed_by_existing_a04_smoke(tmp_path, capsys):
    from ai_commerce.cli import main
    from ai_commerce.contracts.schema import validate_record
    brief = {**intake_brief("A silent visual poem in rain; no speech, music, product or CTA."),
             "product_ref": None, "cta": None, "audio": {"mode": "silence"}}
    root = tmp_path / "creative"
    result = run_creative_cycle(cycle_job(brief=brief), root, cycle_adapter(CycleFakeClient()))
    payload = CreativeStore(root).load_selected(result.output.selection_id)
    assert payload["brief"] == brief and payload["concept"]["revision"] == 2
    assert main(["asset-run", "--store", str(root), "--selection-id", result.output.selection_id,
                 "--out-dir", str(tmp_path / "assets"), "--mode", "mock"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    manifest = json.loads(captured.out)["asset_manifest"]
    artifact = validate_record("MediaArtifact", manifest["result"]["artifact"])
    assert manifest["state"] == "SUCCEEDED" and artifact["generation_performed"] is False
    assert manifest["result"]["publication_status"] == "NOT_ATTEMPTED"


def test_r2_cli_injected_runner_preserves_request_schema_and_raw_output_receipts(installed_cli, tmp_path):
    """TEST_ONLY_MOCK installed CLI protocol; never launches an executable."""
    import base64
    cli, sampler = installed_cli, CycleFakeClient()

    def output(packet):
        model_request = packet["request"]
        response = sampler.complete(StructuredModelRequest(ModelConfiguration.from_dict(model_request["config"]),
            ReasoningRequest.from_dict(model_request["creative"])))
        value = json.loads(response.output_json)
        if model_request["creative"]["operation"] == "DEVELOP":
            value["edits"] = {n: value["edits"].get(n) for n in ("family", "title", "premise", "visual_direction")}
        return value
    cli.creative_output = output
    job = cycle_job(seed_options={"configuration": cli.config})
    reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability(), runner=cli))
    grant = InvocationAuthorization(True, True, "fixture:cli-cycle-grant")
    result = run_creative_cycle(job, tmp_path / "creative", reasoner, authorization=grant)
    assert result.status == "TEST_ONLY_MOCK" and len(cli.packets) == 5 and len(cli.calls) == 20
    assert len({s.reasoning.execution.cli_thread_id for s in result.steps}) == 5
    assert len({s.reasoning.execution.session_sha256 for s in result.steps}) == 5
    for stage, packet, schema in zip(result.steps, cli.packets, cli.schemas):
        response = StructuredModelResponse.from_dict(json.loads(stage.reasoning.response_json))
        assert response.execution.kind == "TEST_ONLY_MOCK" and stage.reasoning.observation.mode == "TEST_ONLY_MOCK"
        assert response.cli_stdin_json.encode() == canonical_bytes(packet)
        assert response.cli_schema_json.encode() == canonical_bytes(schema)
        stdout = base64.b64decode(response.cli_stdout_base64)
        assert sha256_bytes(stdout) == response.execution.stdout_sha256
        message = json.loads(stdout.splitlines()[2])["item"]["text"]
        assert response.output_envelope_json == message
        selection_schema = schema["properties"]["output"]["properties"]["selection"]
        assert selection_schema["properties"]["selection"]["properties"]["schema_version"]["enum"] == ["a03.selection.v1"]
    assert run_creative_cycle(job, tmp_path / "creative", reasoner, authorization=grant) == result
    assert len(cli.calls) == 20
    wire = result.steps[-1].reasoning.to_dict()
    raw = json.loads(wire["response_json"])
    raw["cli_stdout_base64"] = base64.b64encode(b"replayed output").decode()
    wire["response_json"] = json.dumps(raw)
    with pytest.raises(CreativeValidationError):
        ReasoningResult.from_dict(wire)


@pytest.mark.parametrize("fault", ["nonzero", "malformed", "binding"])
def test_r2_cli_failures_preserve_observed_raw_transport_and_never_retry(installed_cli, tmp_path, fault):
    cli = installed_cli
    cli.fault = fault
    job = cycle_job(seed_options={"configuration": cli.config})
    reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability(), runner=cli))
    grant = InvocationAuthorization(True, True, "fixture:cli-cycle-grant")
    result = run_creative_cycle(job, tmp_path / "creative", reasoner, authorization=grant)
    assert result.status == "HOLD" and result.output is None and len(cli.calls) == 4
    raw = StructuredModelResponse.from_dict(json.loads(result.steps[0].reasoning.response_json))
    assert raw.cli_stdout_base64 is not None and raw.cli_stdin_json is not None and raw.cli_schema_json is not None
    assert run_creative_cycle(job, tmp_path / "creative", reasoner, authorization=grant) == result
    assert len(cli.calls) == 4


def test_r2_cli_duplicate_session_holds_without_reinvoking(installed_cli, tmp_path, monkeypatch):
    cli, sampler = installed_cli, CycleFakeClient()
    cli.fault = "reused-session"
    cli.creative_output = lambda packet: json.loads(sampler.complete(StructuredModelRequest(
        cli.config, ReasoningRequest.from_dict(packet["request"]["creative"]),
    )).output_json)
    install_fake_popen(monkeypatch, cli)
    job = cycle_job(seed_options={"configuration": cli.config, "purpose": "PRODUCTION"})
    grant = InvocationAuthorization(True, True, "fixture:duplicate-session")
    reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability()))
    result = run_creative_cycle(job, tmp_path / "creative", reasoner, authorization=grant)
    assert result.status == "HOLD" and result.failure == "REPLAYED_INFERENCE" and result.output is None
    assert len(result.steps) == 2 and len(cli.packets) == 2
    assert result.steps[0].reasoning.execution.cli_thread_id == result.steps[1].reasoning.execution.cli_thread_id
    assert run_creative_cycle(job, tmp_path / "creative", reasoner, authorization=grant) == result
    assert len(cli.packets) == 2


@pytest.mark.parametrize("count,budget", [(1, 1), (4, 1), (2, -1), (2, 3), (2, True)])
def test_r2_invalid_cycle_bounds_never_create_an_inference(count, budget):
    with pytest.raises(CreativeValidationError):
        cycle_job(count=count, max_revisions=budget)


def test_r2_generation_and_system_pins_cannot_be_promoted_to_ai_selection(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path)
    result = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    with pytest.raises(CreativeValidationError, match="independent typed model selector"):
        sidecars.decide_from_result(result.steps[0].outputs[0].selection_id,
                                   result.steps[0].reasoning.digest, kind="AI_RECOMMENDED")
    with pytest.raises(CreativeValidationError):
        sidecars.decide_from_result(result.decision.selection_id, result.steps[-2].reasoning.digest, kind="AUTO_PINNED")


@pytest.mark.parametrize("configured", [False, True])
def test_r2_missing_configuration_or_client_is_a_persisted_hold_without_inference(tmp_path, configured):
    job = cycle_job(seed_options={} if configured else {"configuration": None})
    result = run_creative_cycle(job, tmp_path)
    assert result.status == "HOLD" and result.failure == "NO_MODEL_CONFIGURED"
    assert result.output is None and result.decision is None
    assert run_creative_cycle(job, tmp_path) == result


# --- R3 Creative Production Package tests (all client responses are TEST_ONLY_MOCK) ---

def production_spec_payload(*, silent=False, evidence=(), variant="v1", attributions=None):
    identities = () if silent else (
        IdentityContinuity(
            "obj:umbrella", "OBJECT", "Chiếc ô trong suốt phủ hạt mưa Sài Gòn.",
            ("Khung nhôm mảnh không biến dạng", "Mặt dù trong suốt phản chiếu ánh đèn vàng"),
        ),
    )
    scenes = (
        ProductionScene(
            scene_id="s1", order=1, duration_seconds=4.5,
            state_s0="Mặt đường nhựa khô mờ dưới ánh đèn vàng trước cơn mưa.",
            action="Hạt mưa đầu tiên rơi xuống tạo vòng nước lan rộng dần.",
            state_s1="Mặt đường ướt loang bóng đèn với nhiều vòng gợn nước nối tiếp.",
            camera=CameraSpec("extreme close-up", "85mm macro", "static tripod"),
            lighting="Đèn đường natri ấm phản chiếu trên nhựa ẩm",
            visual_style="Thơ thị giác tối giản, độ tương phản dịu",
            speech_mode="SILENCE",
            identity_refs=() if silent else ("obj:umbrella",),
            sound_cue=None if silent else "Tiếng giọt mưa chạm nhựa đường khẽ và thưa",
        ),
        ProductionScene(
            scene_id="s2", order=2, duration_seconds=6.0,
            state_s0="Hàng hiên vắng có ánh đèn hắt xuống vũng nước rung nhẹ.",
            action="Gió lùa màn mưa nghiêng qua khung hình rồi lắng nhịp.",
            state_s1="Màn mưa dịu lại để lộ bóng đèn tròn tĩnh lặng trên mặt nước.",
            camera=CameraSpec("medium wide", "35mm prime", "slow horizontal pan"),
            lighting="Ánh sáng ẩm mượt dịu lạnh xen ánh đèn hiên ấm",
            visual_style=f"Nhịp ảnh trầm lắng, bố cục cân bằng ({variant})",
            speech_mode="SILENCE" if silent else "SPOKEN",
            identity_refs=() if silent else ("obj:umbrella",),
            sound_cue=None if silent else "Tiếng mưa đều xa dần theo gió",
        ),
    )
    script = (
        ScriptSegment("seg1", "s1", "SILENCE", None, None, "Giữ khoảng lặng mở đầu"),
        ScriptSegment(
            "seg2", "s2", "SILENCE" if silent else "SPOKEN",
            None if silent else f"Mưa Sài Gòn rơi chậm qua hàng hiên, chẳng vội giữ điều gì ({variant}).",
            None if silent else "vi",
            None if silent else "Giọng đọc trầm, chậm rãi",
        ),
    )
    transitions = (SceneTransition("s1", "s2", "dissolve", "Chuyển nhịp mượt theo ánh đèn"),)
    audio = (
        AudioPlan("SILENCE") if silent
        else AudioPlan("MIXED", sound_design="Mưa rơi trên mái tôn và mặt đường",
                       voiceover="Lời thoại tiếng Việt tối giản ở cảnh 2", music=None)
    )
    media_targets = (
        MediaOutputTarget("t1", "storyboard_frame", "9:16", "image/png", ("s1", "s2"),
                          "Khung storyboard nguồn cho R4, chưa sinh media"),
        MediaOutputTarget("t2", "video", "9:16", "video/mp4", ("s1", "s2"), None),
    )
    qa_expectations = (
        QAExpectation("qa1", "scene:s1", "Vòng nước lan từ trạng thái khô sang ướt theo đúng nhân quả",
                      "Kiểm tra chuỗi khung hình s1 ở R4", "GENERATED_CLAIM", "UNVERIFIED"),
        QAExpectation("qa2", "audio", "Khớp chế độ âm thanh hoặc im lặng tuyệt đối theo kịch bản",
                      "Đối chiếu track âm thanh R4 với spec.audio", "HYPOTHESIS", "UNVERIFIED"),
    )
    if attributions is None:
        attributions = tuple(
            RightsAttribution(e.source_ref, "Tham chiếu từ nguồn đầu vào đã cung cấp", "EVIDENCE_BOUND",
                              e.content_sha256, e.verification_ref)
            for e in evidence
        )
    return ProductionPackageSpec(
        narrative_intention=f"Kể một khoảnh khắc mưa Sài Gòn thuần thị giác, không thương mại ({variant}).",
        visual_style="Điện ảnh tối giản, lấy ánh sáng nước làm nhịp dẫn",
        lighting_direction="Tương phản dịu giữa đèn đường ấm và mưa đêm mát",
        identities=identities,
        physics_invariants=("Hạt mưa rơi theo trọng lực và tạo gợn đồng tâm khi chạm nước",),
        temporal_invariants=("Trình tự liên tục từ trước cơn mưa sang cơn mưa dịu dần",),
        creative_freedoms=("Tự do tiết tấu lia máy chậm và sắc độ phản chiếu",),
        scenes=scenes,
        script=script,
        transitions=transitions,
        audio=audio,
        media_targets=media_targets,
        qa_expectations=qa_expectations,
        attributions=attributions,
    )


class ProductionFakeClient(FakeClient):
    """TEST_ONLY_MOCK R3 structured client. Never calls external models or media."""

    def __init__(self, *, silent=False, variant="v1", fault=None, callback=None,
                 usage=None, request_id=None, spec_override=None):
        super().__init__()
        self.silent, self.variant, self.fault, self.callback = silent, variant, fault, callback
        self.usage = usage if usage is not None else UsageObservation()
        self.custom_request_id = request_id
        self.spec_override = spec_override

    def complete(self, request):
        self.calls.append(request)
        creative = request.creative
        assert creative.production is not None
        evidence = creative.production.evidence
        spec = self.spec_override if self.spec_override is not None else production_spec_payload(
            silent=self.silent, evidence=evidence, variant=self.variant,
        )
        value = {
            "production_spec": spec.to_dict() if isinstance(spec, ProductionPackageSpec) else spec,
            "evidence_refs": [e.source_ref for e in evidence],
            "hypotheses": ["TEST_ONLY_MOCK production storyboard hypothesis; UNVERIFIED"],
        }
        if self.callback:
            self.callback(request, value)
        req_id = self.custom_request_id or ("TEST_ONLY_MOCK:r3:" + creative.digest)
        observation = MOCK
        if self.fault == "timeout":
            raise TimeoutError("TEST_ONLY_MOCK ambiguous R3 effect")
        if self.fault == "refusal":
            return StructuredModelResponse("REFUSED", observation=MOCK, usage=self.usage,
                                           request_id=req_id, error_code="MODEL_REFUSED",
                                           message="TEST_ONLY_MOCK refused package generation")
        if self.fault == "unknown-effect":
            return StructuredModelResponse("ERROR", observation=MOCK, usage=UsageObservation(),
                                           request_id=req_id, error_code="UNKNOWN_EFFECT",
                                           message="TEST_ONLY_MOCK unknown effect")
        if self.fault == "mock-as-model":
            observation = ProviderObservation(request.config.provider, request.config.model, "MODEL")
        elif self.fault == "fabricated-evidence":
            value["evidence_refs"] = ["https://hallucinated.invalid/research"]
        elif self.fault == "fabricated-attribution":
            value["production_spec"]["attributions"] = [{
                "source_ref": "https://invented.invalid/brand", "attribution": "Invented brand license",
                "rights_status": "UNKNOWN", "evidence_sha256": None, "verification_ref": None,
            }]
        elif self.fault == "fabricated-rights":
            value["production_spec"]["attributions"] = [{
                "source_ref": "note:unverified", "attribution": "Claimed clearance",
                "rights_status": "GRANTED", "evidence_sha256": None, "verification_ref": None,
            }]
        elif self.fault == "fabricated-qa":
            value["production_spec"]["qa_expectations"][0]["verification_status"] = "PASSED"
        elif self.fault == "premature-media":
            value["production_spec"]["media_targets"][0]["generation_status"] = "GENERATED"
        elif self.fault == "unknown-field":
            value["extra_unapproved"] = True
        elif self.fault == "unchanged-revision":
            value["production_spec"] = creative.production.parent_package.spec.to_dict()
        return StructuredModelResponse("OK", json.dumps(value, ensure_ascii=False, indent=2),
                                       observation=observation, usage=self.usage, request_id=req_id)


def production_job(cycle, **options):
    return A03CreativeProductionJob.from_cycle(cycle, **{
        "journey_id": "test:journey", "cycle_id": "test:cycle", "job_id": "test:r3-package",
        "attempt": 1, "correlation_id": "test:r3-correlation",
        "provenance": CreativeJobProvenance("test:caller", "test:r2-cycle", cycle.digest, "SYNTHETIC_FIXTURE"),
        "configuration": ModelConfiguration("requested-provider", "requested-model", "xhigh"),
        "purpose": "TEST_ONLY_MOCK", **options,
    })


@pytest.mark.parametrize("count", [2, 3])
def test_r3_lineage_2_to_3_concepts_feeds_coherent_production_package(tmp_path, count):
    from jsonschema import Draft202012Validator

    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(count=count), sidecars, cycle_adapter(CycleFakeClient()))
    assert cycle.status == "TEST_ONLY_MOCK"
    client = ProductionFakeClient()
    job = production_job(cycle)
    result = run_creative_production(job, sidecars, cycle_adapter(client))
    assert result.status == "TEST_ONLY_MOCK" and result.failure is None and result.observed is None
    assert len(client.calls) == 1
    schema = client.calls[0].output_schema
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(
        json.loads(json.loads(result.stage.reasoning.response_json)["output_json"])
    )
    pkg = result.package
    assert pkg.schema_version == "A03CreativeProductionPackage.v1"
    assert pkg.revision == 1 and pkg.parent is None and pkg.changed_sections == ()
    assert pkg.brief_id == cycle.job.seed.brief_id
    assert pkg.source_brief_digest == cycle.job.seed.source_brief_digest
    assert pkg.selected_concept == cycle.output
    assert pkg.lineage.cycle_job_digest == cycle.job.digest
    assert pkg.lineage.cycle_result_digest == cycle.digest
    assert len(pkg.lineage.candidate_pins) == count
    assert len(pkg.lineage.critic_receipts) == count
    assert pkg.lineage.selector_receipt_digest == cycle.steps[-2].reasoning.digest
    assert pkg.lineage.develop_receipt_digest == cycle.steps[-1].reasoning.digest
    assert pkg.lineage.decision_id == cycle.decision.decision_id
    assert pkg.observation == MOCK and pkg.observation.model == "UNKNOWN"
    assert pkg.usage == UsageObservation() and pkg.usage.status == "UNKNOWN"
    assert pkg.configuration == job.configuration
    assert pkg.claim_authority == "GENERATED_CLAIM"
    assert pkg.factual_status == "UNVERIFIED" and pkg.rights_status == "UNKNOWN"
    assert pkg.spec.script[1].speech_mode == "SPOKEN" and pkg.spec.script[1].language == "vi"
    assert pkg.spec.script[0].speech_mode == "SILENCE" and pkg.spec.script[0].spoken_text is None
    assert all(m.generation_status == "NOT_GENERATED" for m in pkg.spec.media_targets)
    assert all(q.verification_status == "UNVERIFIED" for q in pkg.spec.qa_expectations)
    assert A03CreativeProductionPackage.from_json(pkg.to_json()) == pkg
    assert A03CreativeProductionResult.from_dict(json.loads(json.dumps(result.to_dict()))) == result
    # Idempotent reopen without new inference and exact immutable bytes.
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*.json")}
    reopened = run_creative_production(job, CreativeSidecarStore(tmp_path), cycle_adapter(client))
    assert reopened == result and len(client.calls) == 1
    assert CreativeSidecarStore(tmp_path).load_production_package(pkg.package_id, 1) == pkg
    assert CreativeSidecarStore(tmp_path).load_production_package_by_digest(pkg.digest) == pkg
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*.json")} == before


def test_r3_zero_product_cta_and_silent_vietnamese_optional_package(tmp_path):
    brief = {
        **intake_brief("A purely fictional, silent visual rain study; zero product, SKU, CTA, speech, music or humans."),
        "product_ref": None, "cta": None, "audio": {"mode": "silence"},
    }
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(brief=brief), sidecars, cycle_adapter(CycleFakeClient()))
    client = ProductionFakeClient(silent=True)
    result = run_creative_production(production_job(cycle), sidecars, cycle_adapter(client))
    assert result.status == "TEST_ONLY_MOCK"
    pkg = result.package
    assert pkg.spec.audio == AudioPlan("SILENCE")
    assert pkg.spec.identities == ()
    assert all(s.speech_mode == "SILENCE" and s.sound_cue is None for s in pkg.spec.scenes)
    assert all(seg.speech_mode == "SILENCE" and seg.spoken_text is None and seg.language is None
               for seg in pkg.spec.script)
    assert not {"product_ref", "sku", "cta"} & pkg.to_dict().keys()
    assert sidecars.creative.load_brief(pkg.source_brief_digest)["product_ref"] is None


def test_r3_bounded_editable_revision_retains_immutable_parent_without_new_inference(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    client = ProductionFakeClient(variant="r1")
    r1_result = run_creative_production(production_job(cycle), sidecars, cycle_adapter(client))
    pkg_r1 = r1_result.package
    r1_bytes = sidecars._package_path(pkg_r1.package_id, 1).read_bytes()

    # Direct Human editable revision of script & narrative_intention: zero new model inference.
    new_script = (
        pkg_r1.spec.script[0],
        replace(pkg_r1.spec.script[1], spoken_text="Mưa Sài Gòn chạm mái hiên, nhịp thở lắng lại."),
    )
    pkg_r2 = revise_production_package(
        pkg_r1,
        {"narrative_intention": "Tinh chỉnh nhịp thơ thị giác ngắn gọn hơn.", "script": new_script},
        edit_reason="Human tightened Vietnamese spoken line",
        actor=ActorRef("HUMAN", "human:director"),
    )
    assert pkg_r2.package_id == pkg_r1.package_id and pkg_r2.revision == 2
    assert pkg_r2.parent == ProductionPackageParent(pkg_r1.package_id, 1, pkg_r1.digest)
    assert pkg_r2.changed_sections == ("narrative_intention", "script")
    assert pkg_r2.reasoning_receipt_digest is None and pkg_r2.edit_actor == ActorRef("HUMAN", "human:director")
    assert pkg_r2.observation == pkg_r1.observation and pkg_r2.lineage == pkg_r1.lineage
    assert sidecars.save_production_package(pkg_r2) == pkg_r2
    assert sidecars.save_production_package(pkg_r2) == pkg_r2  # Idempotent save.
    assert len(client.calls) == 1
    assert sidecars._package_path(pkg_r1.package_id, 1).read_bytes() == r1_bytes
    assert CreativeSidecarStore(tmp_path).load_production_package(pkg_r1.package_id, 1) == pkg_r1
    assert CreativeSidecarStore(tmp_path).load_production_package(pkg_r1.package_id, 2) == pkg_r2

    # Bounded AI-assisted revision creating revision 3 with its own distinct inference receipt.
    ai_client = ProductionFakeClient(variant="r3-ai")
    r3_job = production_job(cycle, job_id="test:r3-revise", parent_package=pkg_r2,
                            edit_reason="AI visual pacing refinement")
    r3_result = run_creative_production(r3_job, sidecars, cycle_adapter(ai_client))
    assert r3_result.status == "TEST_ONLY_MOCK" and len(ai_client.calls) == 1
    pkg_r3 = r3_result.package
    assert pkg_r3.package_id == pkg_r1.package_id and pkg_r3.revision == 3
    assert pkg_r3.parent == ProductionPackageParent(pkg_r2.package_id, 2, pkg_r2.digest)
    assert pkg_r3.reasoning_receipt_digest == r3_result.stage.reasoning.digest
    assert pkg_r3.reasoning_receipt_digest != pkg_r1.reasoning_receipt_digest
    assert sidecars.load_production_package(pkg_r1.package_id, 1) == pkg_r1
    assert sidecars.load_production_package(pkg_r1.package_id, 2) == pkg_r2
    assert sidecars.load_production_package(pkg_r1.package_id, 3) == pkg_r3
    assert run_creative_production(r3_job, sidecars, cycle_adapter(ai_client)) == r3_result
    assert len(ai_client.calls) == 1


def test_r3_evidence_bound_rights_and_unknown_default_attributions(tmp_path):
    content = "Caller-supplied street rain field note; rights scope documented in caller record."
    source = CreativeEvidenceSource("note:rain-field", content, sha256_bytes(content.encode()),
                                    "verified:caller-sha256")
    brief = {**intake_brief(TEXT), "source_refs": [source.source_ref]}
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(brief=brief, evidence=(source,)), sidecars, cycle_adapter(CycleFakeClient()))
    res = run_creative_production(production_job(cycle), sidecars, cycle_adapter(ProductionFakeClient()))
    assert res.status == "TEST_ONLY_MOCK"
    assert res.package.evidence_refs == (source.source_ref,)
    assert res.package.rights_status == "EVIDENCE_BOUND"
    assert res.package.spec.attributions[0].evidence_sha256 == source.content_sha256
    assert res.package.factual_status == "UNVERIFIED"


def test_r3_a03_to_a04_production_consumer_read_smoke_preserves_closed_contracts(tmp_path, capsys):
    from ai_commerce.cli import main
    from ai_commerce.contracts.schema import validate_record

    brief = {**intake_brief("Im lặng hoàn toàn — thơ thị giác về mưa Sài Gòn, không sản phẩm hay CTA."),
             "product_ref": None, "cta": None, "audio": {"mode": "silence"}}
    root = tmp_path / "creative"
    sidecars = CreativeSidecarStore(root)
    cycle = run_creative_cycle(cycle_job(brief=brief), sidecars, cycle_adapter(CycleFakeClient()))
    prod = run_creative_production(production_job(cycle), sidecars, cycle_adapter(ProductionFakeClient(silent=True)))
    assert prod.status == "TEST_ONLY_MOCK"
    handoff = sidecars.load_production_handoff(prod.package.package_id, 1)
    assert handoff["brief"] == brief
    assert handoff["selection"]["selection_id"] == prod.package.selected_concept.selection_id
    assert handoff["package_digest"] == prod.package.digest
    assert A03CreativeProductionPackage.from_dict(handoff["production_package"]) == prod.package
    # Downstream A04 consumes the pinned selection with unchanged closed schemas.
    out_dir = tmp_path / "assets"
    assert main(["asset-run", "--store", str(root), "--selection-id", handoff["selection"]["selection_id"],
                 "--out-dir", str(out_dir), "--mode", "mock"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    output = json.loads(captured.out)
    manifest = output["asset_manifest"]
    result = validate_record("JobResult", manifest["result"])
    artifact = validate_record("MediaArtifact", result["artifact"])
    assert manifest["state"] == "SUCCEEDED" and artifact["generation_performed"] is False
    assert result["publication_status"] == "NOT_ATTEMPTED" and artifact["rights_status"] == "UNKNOWN"
    assert main(["asset-inspect", "--store", str(root), "--out-dir", str(out_dir),
                 "--handoff", output["handoff_ref"]]) == 0
    assert json.loads(capsys.readouterr().out) == output


def test_r3_cli_injected_runner_and_simulated_native_model_branch(installed_cli, tmp_path, monkeypatch):
    import base64

    cli, r2_sampler, r3_sampler = installed_cli, CycleFakeClient(), ProductionFakeClient()

    def dispatch(packet):
        req = packet["request"]
        creative = ReasoningRequest.from_dict(req["creative"])
        model_req = StructuredModelRequest(ModelConfiguration.from_dict(req["config"]), creative)
        if creative.production is not None:
            return json.loads(r3_sampler.complete(model_req).output_json)
        val = json.loads(r2_sampler.complete(model_req).output_json)
        if creative.operation == "DEVELOP":
            val["edits"] = {n: val["edits"].get(n) for n in ("family", "title", "premise", "visual_direction")}
        return val

    cli.creative_output = dispatch
    grant = InvocationAuthorization(True, True, "fixture:r3-cli-grant")
    # 1) Injected runner -> always TEST_ONLY_MOCK, preserving exact native transport bytes.
    mock_root = tmp_path / "mock-cli"
    mock_reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability(), runner=cli))
    mock_cycle = run_creative_cycle(cycle_job(seed_options={"configuration": cli.config}), mock_root,
                                    mock_reasoner, authorization=grant)
    mock_job = production_job(mock_cycle, configuration=cli.config, purpose="TEST_ONLY_MOCK")
    mock_res = run_creative_production(mock_job, mock_root, mock_reasoner, authorization=grant)
    assert mock_res.status == "TEST_ONLY_MOCK"
    assert mock_res.package.observation.mode == "TEST_ONLY_MOCK" and mock_res.package.observation.model == "UNKNOWN"
    assert mock_res.package.execution.kind == "TEST_ONLY_MOCK"
    assert mock_res.package.execution.observed_model == cli.config.model
    assert mock_res.package.execution.observed_effort == cli.config.effort
    raw = StructuredModelResponse.from_dict(json.loads(mock_res.stage.reasoning.response_json))
    assert sha256_bytes(base64.b64decode(raw.cli_stdout_base64)) == raw.execution.stdout_sha256
    assert raw.cli_stdin_json is not None and raw.cli_schema_json is not None and raw.output_envelope_json is not None

    # 2) Simulated native Popen branch -> synthetic coverage of MODEL / CREATIVE_COMPLETE path.
    install_fake_popen(monkeypatch, cli)
    native_root = tmp_path / "native-cli"
    native_reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability(), runner=None))
    native_cycle = run_creative_cycle(
        cycle_job(seed_options={"configuration": cli.config, "purpose": "PRODUCTION"}),
        native_root, native_reasoner, authorization=grant,
    )
    assert native_cycle.status == "CREATIVE_COMPLETE"
    native_job = production_job(native_cycle, configuration=cli.config, purpose="PRODUCTION")
    native_res = run_creative_production(native_job, native_root, native_reasoner, authorization=grant)
    assert native_res.status == "CREATIVE_COMPLETE"
    assert native_res.package.observation == ProviderObservation("codex-cli", cli.config.model, "MODEL")
    assert native_res.package.execution.kind == "AUTHENTICATED_CLI_PROBE"
    assert native_res.package.usage == UsageObservation("OBSERVED", 11, 7)
    assert verified_model_transport(cli.config, native_res.package.observation, native_res.package.execution,
                                    authorization=grant) == "codex-exec-jsonl"
    assert verified_model_transport(cli.config, mock_res.package.observation, mock_res.package.execution,
                                    authorization=grant) is None
    assert verified_model_transport(cli.config, native_res.package.observation, native_res.package.execution,
                                    authorization=replace(grant, authorization_ref="fixture:wrong-grant")) is None
    assert verified_model_transport(cli.config, native_res.package.observation, native_res.package.execution,
                                    authorization=InvocationAuthorization()) is None
    response = StructuredModelResponse.from_dict(json.loads(native_res.stage.reasoning.response_json))

    class ImpersonatingClient(FakeClient):
        execution_mode = "MODEL"

    forged = ImpersonatingClient(response)
    rejected = LiveCreativeReasoner(cli.config, forged, authorization=grant).reason(native_res.stage.job.request)
    assert rejected.error_code == "UNVERIFIED_MODEL_ADAPTER" and forged.calls == []
    before_calls = len(cli.calls)
    assert run_creative_production(native_job, native_root) == native_res
    assert len(cli.calls) == before_calls


@pytest.mark.parametrize("fault", [
    "fabricated-evidence", "fabricated-attribution", "fabricated-rights",
    "fabricated-qa", "premature-media", "unknown-field",
])
def test_r3_negative_hallucinated_evidence_rights_qa_and_premature_media(tmp_path, fault):
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    client = ProductionFakeClient(fault=fault)
    res = run_creative_production(production_job(cycle), sidecars, cycle_adapter(client))
    assert res.status == "HOLD" and res.failure == "INVALID_STRUCTURED_OUTPUT"
    assert res.package is None and len(client.calls) == 1
    assert not list((tmp_path / "sidecars" / "a03-v11" / "production-packages").rglob("*.json"))


def test_r3_negative_unbound_evidence_rights_and_wrong_sha_rejected(tmp_path):
    brief = {**intake_brief(TEXT), "source_refs": ["note:unverified"]}
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(brief=brief), sidecars, cycle_adapter(CycleFakeClient()))
    # Claiming EVIDENCE_BOUND when no CreativeEvidenceSource was supplied fails closed.
    bad_attr = (RightsAttribution("note:unverified", "Unverified claim", "EVIDENCE_BOUND", "a" * 64, "ref:fake"),)
    client = ProductionFakeClient(spec_override=production_spec_payload(attributions=bad_attr))
    res = run_creative_production(production_job(cycle), sidecars, cycle_adapter(client))
    assert res.status == "HOLD" and res.failure == "INVALID_STRUCTURED_OUTPUT" and res.package is None
    with pytest.raises(CreativeValidationError):
        RightsAttribution("note:x", "note", "EVIDENCE_BOUND", "a" * 64, "UNKNOWN")
    with pytest.raises(CreativeValidationError):
        RightsAttribution("note:x", "note", "UNKNOWN", "a" * 64, None)
    with pytest.raises(CreativeValidationError):
        RightsAttribution("note:x", "note", "CLEARED")


def test_r3_negative_wrong_source_pin_and_stale_revision(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    # Wrong selection pin (unselected revision 1 from explore instead of developed revision 2).
    wrong_job = replace(production_job(cycle), selected_concept=cycle.steps[0].outputs[0])
    with pytest.raises(CreativeValidationError, match="does not match verified saved R2 cycle"):
        run_creative_production(wrong_job, sidecars, cycle_adapter(ProductionFakeClient()))
    # Wrong cycle_result_digest.
    with pytest.raises(CreativeValidationError, match="cycle result digest mismatch"):
        run_creative_production(replace(production_job(cycle), cycle_result_digest="0" * 64),
                                sidecars, cycle_adapter(ProductionFakeClient()))
    # HOLD/STOPPED R2 cycle cannot authorize a production job.
    stopped = run_creative_cycle(cycle_job(seed_options={"job_id": "stopped"}), sidecars,
                                 cycle_adapter(CycleFakeClient(actions=("STOP",))))
    assert stopped.status == "STOPPED"
    with pytest.raises(CreativeValidationError, match="completed verified R2 cycle"):
        production_job(stopped)
    # Stale critic receipt in package wire JSON fails verification.
    res = run_creative_production(production_job(cycle), sidecars, cycle_adapter(ProductionFakeClient()))
    tampered = res.package.to_dict()
    tampered["lineage"]["critic_receipts"][0] = "0" * 64
    with pytest.raises(CreativeValidationError):
        sidecars.save_production_package(A03CreativeProductionPackage.from_dict(tampered))


@pytest.mark.parametrize("mutation", [
    "scene-null-speech", "script-null-speech", "spoken-null-text", "spoken-blank-text",
    "silence-with-text", "silence-with-lang", "audio-null-mode", "audio-silence-with-music",
    "audio-silence-with-sound-cue", "audio-silence-with-spoken-scene",
])
def test_r3_negative_missing_null_vs_explicit_silence(mutation):
    data = production_spec_payload(silent=mutation.startswith("audio-silence")).to_dict()
    if mutation == "scene-null-speech":
        data["scenes"][0]["speech_mode"] = None
    elif mutation == "script-null-speech":
        data["script"][0]["speech_mode"] = None
    elif mutation == "spoken-null-text":
        data["script"][1]["spoken_text"] = None
    elif mutation == "spoken-blank-text":
        data["script"][1]["spoken_text"] = "   "
    elif mutation == "silence-with-text":
        data["script"][0]["spoken_text"] = "Whispered line"
    elif mutation == "silence-with-lang":
        data["script"][0]["language"] = "vi"
    elif mutation == "audio-null-mode":
        data["audio"]["mode"] = None
    elif mutation == "audio-silence-with-music":
        data["audio"]["music"] = "Soft piano"
    elif mutation == "audio-silence-with-sound-cue":
        data["scenes"][0]["sound_cue"] = "Rain sound"
    elif mutation == "audio-silence-with-spoken-scene":
        data["scenes"][1]["speech_mode"] = "SPOKEN"
        data["script"][1].update(speech_mode="SPOKEN", spoken_text="Xin chào", language="vi")
    with pytest.raises(CreativeValidationError):
        ProductionPackageSpec.from_dict(data)


@pytest.mark.parametrize("mutation", [
    "blank-s0", "blank-action", "blank-s1", "same-s0-s1", "zero-duration", "bool-duration",
    "nan-duration", "bad-order", "dup-scene-id", "empty-scenes", "blank-camera-lens",
])
def test_r3_negative_invalid_causal_scene_fields(mutation):
    data = production_spec_payload().to_dict()
    if mutation == "blank-s0":
        data["scenes"][0]["state_s0"] = " "
    elif mutation == "blank-action":
        data["scenes"][0]["action"] = ""
    elif mutation == "blank-s1":
        data["scenes"][0]["state_s1"] = None
    elif mutation == "same-s0-s1":
        data["scenes"][0]["state_s1"] = data["scenes"][0]["state_s0"]
    elif mutation == "zero-duration":
        data["scenes"][0]["duration_seconds"] = 0
    elif mutation == "bool-duration":
        data["scenes"][0]["duration_seconds"] = True
    elif mutation == "nan-duration":
        data["scenes"][0]["duration_seconds"] = float("nan")
    elif mutation == "bad-order":
        data["scenes"][1]["order"] = 3
    elif mutation == "dup-scene-id":
        data["scenes"][1]["scene_id"] = "s1"
    elif mutation == "empty-scenes":
        data["scenes"] = []
    elif mutation == "blank-camera-lens":
        data["scenes"][0]["camera"]["lens"] = " "
    with pytest.raises(CreativeValidationError):
        ProductionPackageSpec.from_dict(data)


@pytest.mark.parametrize("mutation", [
    "unknown-identity-ref", "dup-identity-id", "empty-invariants", "missing-transition",
    "reversed-transition", "self-transition", "unknown-script-scene", "out-of-order-script",
    "scene-missing-script", "scene-script-speech-mismatch", "media-unknown-scene",
])
def test_r3_negative_inconsistent_identity_and_scene_state(mutation):
    data = production_spec_payload().to_dict()
    if mutation == "unknown-identity-ref":
        data["scenes"][0]["identity_refs"] = ["char:ghost"]
    elif mutation == "dup-identity-id":
        data["identities"].append(dict(data["identities"][0]))
    elif mutation == "empty-invariants":
        data["identities"][0]["continuity_invariants"] = []
    elif mutation == "missing-transition":
        data["transitions"] = []
    elif mutation == "reversed-transition":
        data["transitions"][0].update(from_scene_id="s2", to_scene_id="s1")
    elif mutation == "self-transition":
        data["transitions"][0].update(from_scene_id="s1", to_scene_id="s1")
    elif mutation == "unknown-script-scene":
        data["script"][0]["scene_id"] = "s99"
    elif mutation == "out-of-order-script":
        data["script"] = list(reversed(data["script"]))
    elif mutation == "scene-missing-script":
        data["script"] = [data["script"][0]]
    elif mutation == "scene-script-speech-mismatch":
        data["scenes"][1]["speech_mode"] = "SILENCE"
    elif mutation == "media-unknown-scene":
        data["media_targets"][0]["scene_refs"] = ["s99"]
    with pytest.raises(CreativeValidationError):
        ProductionPackageSpec.from_dict(data)


def test_r3_negative_mock_as_real_fake_model_and_usage(installed_cli, tmp_path):
    sidecars = CreativeSidecarStore(tmp_path / "s1")
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    grant = InvocationAuthorization(True, True, "fixture:prod-grant")
    # 1) Mock R2 cycle cannot back a PRODUCTION package job.
    res = run_creative_production(production_job(cycle, purpose="PRODUCTION"), sidecars,
                                  cycle_adapter(ProductionFakeClient()), authorization=grant)
    assert res.status == "HOLD" and res.failure == "MOCK_CYCLE_NOT_PRODUCTION" and res.package is None
    # 2) Mock client claiming observation.mode="MODEL" fails closed.
    bad_client = ProductionFakeClient(fault="mock-as-model")
    res2 = run_creative_production(production_job(cycle, job_id="mock-as-model"), sidecars,
                                   cycle_adapter(bad_client))
    assert res2.status == "HOLD" and res2.failure == "INVALID_MODEL_RESPONSE" and res2.package is None
    # 3) Generic non-CLI client claiming execution_mode="MODEL" fails closed without invocation.
    class PretendModelClient(ProductionFakeClient):
        execution_mode = "MODEL"
    pretend = PretendModelClient()
    res3 = run_creative_production(production_job(cycle, job_id="pretend-model"), sidecars,
                                   cycle_adapter(pretend))
    assert res3.status == "HOLD" and res3.failure == "ADAPTER_MODE_MISMATCH" and pretend.calls == []
    # 4) Promoting TEST_ONLY_MOCK result to CREATIVE_COMPLETE or tampering usage fails closed.
    ok_res = run_creative_production(production_job(cycle, job_id="ok-mock"), sidecars,
                                     cycle_adapter(ProductionFakeClient()))
    wire = ok_res.to_dict()
    wire["status"] = "CREATIVE_COMPLETE"
    with pytest.raises(CreativeValidationError):
        A03CreativeProductionResult.from_dict(wire)
    wire_usage = ok_res.to_dict()
    wire_usage["package"]["usage"] = {"status": "OBSERVED", "input_tokens": 999, "output_tokens": 1,
                                      "cost_amount": None, "currency": None}
    with pytest.raises(CreativeValidationError):
        A03CreativeProductionResult.from_dict(wire_usage)
    # 5) CLI effort/model mismatch fails closed as HOLD without partial package.
    cli = installed_cli
    cli.creative_output = lambda pkt: (
        json.loads(ProductionFakeClient().complete(StructuredModelRequest(
            ModelConfiguration.from_dict(pkt["request"]["config"]),
            ReasoningRequest.from_dict(pkt["request"]["creative"]),
        )).output_json) if pkt["request"]["creative"].get("production") is not None else
        (lambda v: ({**v, "edits": {n: v["edits"].get(n) for n in ("family", "title", "premise", "visual_direction")}}
                    if pkt["request"]["creative"]["operation"] == "DEVELOP" else v))(
            json.loads(CycleFakeClient().complete(StructuredModelRequest(
                ModelConfiguration.from_dict(pkt["request"]["config"]),
                ReasoningRequest.from_dict(pkt["request"]["creative"]),
            )).output_json))
    )
    cli_root = tmp_path / "cli-faults"
    reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability(), runner=cli))
    cli_cycle = run_creative_cycle(cycle_job(seed_options={"configuration": cli.config}), cli_root,
                                   reasoner, authorization=grant)
    for fault, expected in (("effort", "CONFIGURATION_MISMATCH"), ("model", "CONFIGURATION_MISMATCH"),
                            ("nonzero", "UNKNOWN_EFFECT"), ("malformed", "UNKNOWN_EFFECT")):
        cli.fault = fault
        bad_cli = run_creative_production(
            production_job(cli_cycle, job_id=f"cli-{fault}", configuration=cli.config),
            cli_root, reasoner, authorization=grant,
        )
        assert bad_cli.status == "HOLD" and bad_cli.failure == expected and bad_cli.package is None


def test_r3_negative_replayed_ambiguous_refusal_and_interrupted_provider(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    # 1) Replayed provider_request_id from R2 cycle -> HOLD REPLAYED_INFERENCE.
    replayed_id = cycle.steps[-1].reasoning.provider_request_id
    res_replay = run_creative_production(
        production_job(cycle, job_id="replay"), sidecars,
        cycle_adapter(ProductionFakeClient(request_id=replayed_id)),
    )
    assert res_replay.status == "HOLD" and res_replay.failure == "REPLAYED_INFERENCE" and res_replay.package is None
    # 2) Timeout / exception -> HOLD MODEL_ERROR, no retry.
    timeout_client = ProductionFakeClient(fault="timeout")
    res_timeout = run_creative_production(production_job(cycle, job_id="timeout"), sidecars,
                                          cycle_adapter(timeout_client))
    assert res_timeout.status == "HOLD" and res_timeout.failure == "MODEL_ERROR"
    assert res_timeout.package is None and len(timeout_client.calls) == 1
    assert run_creative_production(production_job(cycle, job_id="timeout"), sidecars,
                                   cycle_adapter(timeout_client)) == res_timeout
    assert len(timeout_client.calls) == 1
    # 3) Model refusal -> STOPPED, no partial package.
    refuse_client = ProductionFakeClient(fault="refusal")
    res_refuse = run_creative_production(production_job(cycle, job_id="refuse"), sidecars,
                                         cycle_adapter(refuse_client))
    assert res_refuse.status == "STOPPED" and res_refuse.failure == "MODEL_REFUSED" and res_refuse.package is None
    # 4) Interrupted attempt -> reconstructs HOLD without re-invoking provider.
    def crash(req, val):
        raise KeyboardInterrupt("TEST_ONLY_MOCK crash mid-production")
    crash_client = ProductionFakeClient(callback=crash)
    crash_job = production_job(cycle, job_id="interrupted")
    with pytest.raises(KeyboardInterrupt):
        run_creative_production(crash_job, sidecars, cycle_adapter(crash_client))
    calls_before = len(crash_client.calls)
    recovered = run_creative_production(crash_job, sidecars, cycle_adapter(crash_client))
    assert recovered.status == "HOLD" and recovered.failure == "UNRESOLVED_PRODUCTION_STEP"
    assert recovered.package is None and len(crash_client.calls) == calls_before
    # 5) Duplicate attempt slot with different parameters -> VersionConflict.
    with pytest.raises(VersionConflict):
        run_creative_production(replace(crash_job, correlation_id="rebound"), sidecars,
                                cycle_adapter(ProductionFakeClient()))


def test_r3_negative_conflicting_edit_noop_and_provenance_edit(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    pkg_r1 = run_creative_production(production_job(cycle), sidecars, cycle_adapter(ProductionFakeClient())).package
    # Conflicting direct edits from revision 1 -> second raises VersionConflict.
    edit_a = revise_production_package(pkg_r1, {"narrative_intention": "Edit A"}, edit_reason="A",
                                       actor=ActorRef("HUMAN", "human:a"))
    edit_b = revise_production_package(pkg_r1, {"narrative_intention": "Edit B"}, edit_reason="B",
                                       actor=ActorRef("HUMAN", "human:b"))
    sidecars.save_production_package(edit_a)
    with pytest.raises(VersionConflict):
        sidecars.save_production_package(edit_b)
    assert sidecars.load_production_package(pkg_r1.package_id, 2) == edit_a
    # No-op direct edit, provenance field edit, AI actor without inference, and free-form dict all rejected.
    with pytest.raises(CreativeValidationError):
        revise_production_package(pkg_r1, {"narrative_intention": pkg_r1.spec.narrative_intention},
                                  edit_reason="Noop", actor=ActorRef("HUMAN", "human:a"))
    with pytest.raises(CreativeValidationError):
        revise_production_package(pkg_r1, {"factual_status": "VERIFIED"}, edit_reason="Bad",
                                  actor=ActorRef("HUMAN", "human:a"))
    with pytest.raises(CreativeValidationError):
        revise_production_package(pkg_r1, {"narrative_intention": "Spoof AI"}, edit_reason="Bad",
                                  actor=ActorRef("AI", "reasoning:" + "0" * 64))
    with pytest.raises(CreativeValidationError):
        sidecars.save_production_package({"freeform": "not a typed package"})
    # AI revision colliding with already-saved revision 2 -> HOLD ARTIFACT_CONFLICT preserving observed output.
    conflict_job = production_job(cycle, job_id="ai-conflict", parent_package=pkg_r1, edit_reason="AI rev 2")
    conflict_res = run_creative_production(conflict_job, sidecars,
                                           cycle_adapter(ProductionFakeClient(variant="ai-conflict")))
    assert conflict_res.status == "HOLD" and conflict_res.failure == "ARTIFACT_CONFLICT"
    assert conflict_res.package is None and conflict_res.observed is not None
    assert sidecars.load_production_package(pkg_r1.package_id, 2) == edit_a
    # AI revision producing unchanged spec -> HOLD NO_MATERIAL_CHANGE.
    noop_ai_job = production_job(cycle, job_id="ai-noop", parent_package=edit_a, edit_reason="Noop AI")
    noop_ai = run_creative_production(noop_ai_job, sidecars,
                                      cycle_adapter(ProductionFakeClient(fault="unchanged-revision")))
    assert noop_ai.status == "HOLD" and noop_ai.failure == "NO_MATERIAL_CHANGE" and noop_ai.package is None


@pytest.mark.parametrize("kind", [
    A03CreativeProductionJob, A03CreativeProductionPackage, A03CreativeProductionResult, ProductionPackageSpec,
])
def test_r3_negative_duplicate_missing_and_unknown_fields(tmp_path, kind):
    from ai_commerce.creative._validation import read_json

    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    res = run_creative_production(production_job(cycle), sidecars, cycle_adapter(ProductionFakeClient()))
    record = {
        A03CreativeProductionJob: res.job,
        A03CreativeProductionPackage: res.package,
        A03CreativeProductionResult: res,
        ProductionPackageSpec: res.package.spec,
    }[kind]
    data = record.to_dict()
    with pytest.raises(CreativeValidationError):
        kind.from_dict({**data, "unexpected_key": True})
    with pytest.raises(CreativeValidationError):
        kind.from_dict({k: v for k, v in data.items() if k != next(iter(data))})
    key = next(iter(data))
    duplicate = json.dumps(data)[:-1] + "," + json.dumps(key) + ":" + json.dumps(data[key]) + "}"
    with pytest.raises(CreativeValidationError, match="INVALID_JSON"):
        kind.from_dict(read_json(duplicate))


@pytest.mark.parametrize("target", [
    "prod-job", "prod-grant", "prod-result", "prod-ref", "prod-package",
    "prod-digest", "cycle-result", "brief", "concept", "selection", "reasoning",
])
def test_r3_negative_corrupt_bytes_never_reinvokes_model(tmp_path, target):
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    client = ProductionFakeClient()
    job = production_job(cycle)
    res = run_creative_production(job, sidecars, cycle_adapter(client))
    pkg = res.package
    path = {
        "prod-job": sidecars._path("production-jobs", job.slot),
        "prod-grant": sidecars._path("production-grants", job.slot),
        "prod-result": sidecars._path("production-results", res.digest),
        "prod-ref": sidecars._path("production-refs", job.slot),
        "prod-package": sidecars._package_path(pkg.package_id, 1),
        "prod-digest": sidecars._path("production-package-digests", pkg.digest),
        "cycle-result": sidecars._path("cycle-results", cycle.digest),
        "brief": sidecars.creative.root / "briefs" / f"{pkg.source_brief_digest}.json",
        "concept": sidecars.creative._concept_path(pkg.selected_concept.concept_id, pkg.selected_concept.revision),
        "selection": sidecars.creative._selection_path(pkg.selected_concept.selection_id),
        "reasoning": sidecars._path("reasoning", pkg.reasoning_receipt_digest),
    }[target]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises((CreativeValidationError, VersionConflict)):
        run_creative_production(job, sidecars, cycle_adapter(client))
    assert len(client.calls) == 1


def test_r3_negative_changed_permission_and_missing_grant(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path)
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    # 1) Missing or partial grant for network/spend client fails closed even if adapter has grant.
    live_fake = ProductionFakeClient()
    live_fake.capabilities = ProviderCapabilities("fake-live-r3", ("concept",), True, True)
    reasoner = LiveCreativeReasoner(
        ModelConfiguration("requested-provider", "requested-model", "xhigh"),
        live_fake, authorization=InvocationAuthorization(True, True, "adapter:must-not-borrow"),
    )
    for idx, grant in enumerate((None, InvocationAuthorization(), InvocationAuthorization(True, False, "net-only"))):
        res = run_creative_production(production_job(cycle, job_id=f"unauth-{idx}"), sidecars,
                                      reasoner, authorization=grant)
        assert res.status == "HOLD" and res.failure == "UNAUTHORIZED" and res.package is None
    assert live_fake.calls == []
    # 2) Missing grant on PRODUCTION purpose fails closed before any call.
    res_prod = run_creative_production(production_job(cycle, job_id="prod-unauth", purpose="PRODUCTION"),
                                       sidecars, reasoner, authorization=None)
    assert res_prod.status == "HOLD" and res_prod.failure == "UNAUTHORIZED" and live_fake.calls == []
    # 3) Replaying a completed job with changed explicit authorization fails closed.
    ok_job = production_job(cycle, job_id="perm-replay")
    ok_grant = InvocationAuthorization(True, True, "runner:grant-1")
    ok_res = run_creative_production(ok_job, sidecars, reasoner, authorization=ok_grant)
    assert ok_res.status == "TEST_ONLY_MOCK" and len(live_fake.calls) == 1
    with pytest.raises(CreativeValidationError, match="AUTHORIZATION_MISMATCH"):
        run_creative_production(ok_job, sidecars, reasoner,
                                authorization=InvocationAuthorization(True, True, "runner:grant-changed"))
    assert len(live_fake.calls) == 1


def test_r4_sidecar_result_digest_lineage_and_opt_in_production_seam(installed_cli, tmp_path, monkeypatch):
    from ai_commerce.media.generation import A04CreativeHandoffReceipt, A04_CREATIVE_CONSUMER_ID
    from ai_commerce.production_orchestration import LocalAuthorization, ProductionPipelineOrchestrator

    cli, r2_sampler, r3_sampler = installed_cli, CycleFakeClient(), ProductionFakeClient(silent=True)

    def dispatch(packet):
        req = packet["request"]
        creative = ReasoningRequest.from_dict(req["creative"])
        model_req = StructuredModelRequest(ModelConfiguration.from_dict(req["config"]), creative)
        if creative.production is not None:
            return json.loads(r3_sampler.complete(model_req).output_json)
        val = json.loads(r2_sampler.complete(model_req).output_json)
        if creative.operation == "DEVELOP":
            val["edits"] = {n: val["edits"].get(n) for n in ("family", "title", "premise", "visual_direction")}
        return val

    cli.creative_output = dispatch
    install_fake_popen(monkeypatch, cli)
    grant = InvocationAuthorization(True, True, "fixture:r4-a03-grant")
    root = tmp_path / "r4-creative"
    sidecars = CreativeSidecarStore(root)
    reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability(), runner=None))
    cycle = run_creative_cycle(
        cycle_job(seed_options={"configuration": cli.config, "purpose": "PRODUCTION"}),
        sidecars, reasoner, authorization=grant,
    )
    job = production_job(cycle, journey_id="r4:a03", cycle_id="r4:c1", job_id="r4:p1",
                         configuration=cli.config, purpose="PRODUCTION")
    # Default production=None performs zero downstream orchestration.
    res = run_creative_production(job, sidecars, reasoner, authorization=grant)
    assert res.status == "CREATIVE_COMPLETE"
    assert sidecars.load_production_result_by_digest(res.digest) == res
    origin, pkg, handoff = sidecars.verify_production_package_lineage(
        res.package.package_id, 1, origin_result_digest=res.digest,
    )
    assert origin == res and pkg == res.package and handoff["package_digest"] == pkg.digest

    # Editable HUMAN revision retains parent chain back to origin result.
    pkg_r2 = revise_production_package(
        pkg, {"narrative_intention": "Chỉnh nhịp hình ảnh tĩnh lặng."},
        edit_reason="Human visual pacing refinement", actor=ActorRef("HUMAN", "human:editor"),
    )
    sidecars.save_production_package(pkg_r2)
    origin2, verified_r2, _ = sidecars.verify_production_package_lineage(
        pkg_r2.package_id, 2, origin_result_digest=res.digest,
    )
    assert origin2 == res and verified_r2 == pkg_r2

    # Replay with opt-in ProductionPipelineOrchestrator delivers to A04 without reinference.
    cli_calls_before = len(cli.calls)
    source_sha = "a" * 40
    local_grant = LocalAuthorization(
        journey_id="r4:a03", cycle_id="r4:c1", job_id="r4:p1",
        source_sha=source_sha, authorization_ref="fixture:a03-a04", route_id="a03-to-a04",
    )
    orch = ProductionPipelineOrchestrator(
        state_dir=tmp_path / "orch", source_sha=source_sha, local_authorization=local_grant,
    )
    assert run_creative_production(job, sidecars, production=orch) == res
    assert len(cli.calls) == cli_calls_before
    assert orch.last_result is not None and orch.last_result.status == "SUCCESS"
    assert orch.last_result.accepted_consumer == A04_CREATIVE_CONSUMER_ID
    assert isinstance(orch.last_result.receiver_receipt, A04CreativeHandoffReceipt)
    assert orch.last_result.receiver_receipt.media_execution == "NOT_ATTEMPTED"
    assert orch.last_result.receiver_receipt.status == "SOURCE_HANDOFF_VERIFIED"


# ---------------------------------------------------------------------------
# R5a benchmark foundation & evidence-led comparison tests (TEST_ONLY_MOCK).
# Zero real provider/media/spend/account/rights/HostOps/Wake/Send effects.
# ---------------------------------------------------------------------------

R5A_SOURCE_SHA = "f5ac9f274448253bd880538840642f4e2ce97eba"
R5A_TS_START = "2026-11-20T00:00:00Z"
R5A_TS_END = "2026-11-20T00:01:00Z"


def _r5a_provenance(ref="fixture:r5a-mock"):
    return CreativeJobProvenance("test:r5a-benchmark", ref, sha256_bytes(ref.encode("utf-8")), "SYNTHETIC_FIXTURE")


class _R5ABenchmarkMockClient(FakeClient):
    """TEST_ONLY_MOCK client supporting standalone S1/S2 stages and R2 cycles with controlled usage."""

    def __init__(self, *, usage_by_op=None, fault=None, req_prefix="r5a"):
        super().__init__()
        self.usage_by_op = usage_by_op or {}
        self.fault = fault
        self.req_prefix = req_prefix
        self._cycle_client = CycleFakeClient()

    def complete(self, request):
        self.calls.append(request)
        creative = request.creative
        usage = self.usage_by_op.get(creative.operation, UsageObservation())
        req_id = f"TEST_ONLY_MOCK:{self.req_prefix}:{len(self.calls)}:{creative.digest}"
        if self.fault == "error":
            return StructuredModelResponse("ERROR", observation=MOCK, usage=usage,
                                           request_id=req_id, error_code="UNKNOWN_EFFECT",
                                           message="TEST_ONLY_MOCK unknown provider effect")
        if self.fault == "refusal":
            return StructuredModelResponse("REFUSED", observation=MOCK, usage=usage,
                                           request_id=req_id, error_code="MODEL_REFUSED",
                                           message="TEST_ONLY_MOCK refused")
        if creative.cycle is not None:
            resp = self._cycle_client.complete(request)
            return replace(resp, usage=usage, request_id=req_id)
        if creative.operation == "EXPLORE":
            drafts = [
                ConceptDraft(
                    f"family-{i}",
                    f"Ý tưởng {i} ({creative.brief['brief_id']})",
                    f"Cơ chế kể chuyện {i} cho {creative.brief['objective']}",
                    f"Ngôn ngữ hình ảnh {i}",
                    tuple(creative.brief.get("source_refs", [])),
                    ("TEST_ONLY_MOCK unverified creative hypothesis",),
                )
                for i in range(creative.count or 2)
            ]
            return StructuredModelResponse("OK", payload(drafts=drafts), observation=MOCK, usage=usage, request_id=req_id)
        if creative.operation == "CRITIQUE":
            return StructuredModelResponse(
                "OK",
                payload(critique=f"TEST_ONLY_MOCK independent critique for {creative.source.digest}"),
                observation=MOCK, usage=usage, request_id=req_id,
            )
        raise AssertionError(f"unexpected operation in _R5ABenchmarkMockClient: {creative.operation}")


def _r5a_brief_and_evidence(brief_class):
    if brief_class == "VN_PRODUCT":
        text = "Giới thiệu phin cà phê gốm Bát Tràng giữ nhiệt 45 phút, hương vị đậm đà buổi sáng."
        ev_content = "Thông số xưởng Bát Tràng: gốm nung 1280C, dung tích 180ml.\n"
        ev = CreativeEvidenceSource("source:vn-product-spec", ev_content,
                                    sha256_bytes(ev_content.encode("utf-8")), "fixture:verified-vn-product")
        brief = {
            **intake_brief(text, brief_id="brief-vn-product"),
            "product_ref": "sku:phin-gom-bat-trang-180ml",
            "source_refs": [ev.source_ref],
        }
        return brief, (ev,), True
    if brief_class == "VN_NON_PRODUCT_FICTION":
        text = "Truyện ngắn hư cấu về người gác đèn biển ở Cù Lao Chàm nghe tiếng sóng đêm, không sản phẩm."
        ev_content = "Ghi chú bối cảnh văn hóa biển miền Trung (hư cấu).\n"
        ev = CreativeEvidenceSource("source:vn-fiction-note", ev_content,
                                    sha256_bytes(ev_content.encode("utf-8")), "fixture:verified-vn-fiction")
        brief = {
            **intake_brief(text, brief_id="brief-vn-fiction"),
            "product_ref": None,
            "source_refs": [ev.source_ref],
        }
        return brief, (ev,), False
    text = "Cơn mưa đêm trên mái ngói cổ Hội An: hoàn toàn im lặng, không thoại, không nhạc, không CTA."
    ev_content = "Ghi chú ánh sáng và nhịp mưa tĩnh lặng (không thoại, không CTA).\n"
    ev = CreativeEvidenceSource("source:silent-mood", ev_content,
                                sha256_bytes(ev_content.encode("utf-8")), "fixture:verified-silent-note")
    brief = {
        **intake_brief(text, brief_id="brief-silent-no-cta"),
        "product_ref": None,
        "cta": None,
        "audio": {"mode": "silence"},
        "source_refs": [ev.source_ref],
    }
    return brief, (ev,), False


def _run_r5a_strategy_workflows(sidecars, case, brief, evidence, *, tag="run1", usage_by_op=None,
                                include_retry_s1=False, silent_pkg=False):
    cfg = ModelConfiguration("requested-provider", "requested-model", "xhigh")
    prov = _r5a_provenance(f"fixture:{case.case_id}:{tag}")
    usage_map = usage_by_op if usage_by_op is not None else {
        "EXPLORE": UsageObservation("OBSERVED", 400, 220),
        "CRITIQUE": UsageObservation("OBSERVED", 250, 140),
        "SELECT": UsageObservation("OBSERVED", 300, 110),
        "DEVELOP": UsageObservation("OBSERVED", 350, 180),
        "PRODUCE_PACKAGE": UsageObservation("OBSERVED", 500, 420),
    }

    # S1: single generator (optionally with an observed retry stage).
    s1_client = _R5ABenchmarkMockClient(usage_by_op=usage_map, req_prefix=f"s1-{case.case_id}-{tag}")
    s1_reasoner = LiveCreativeReasoner(cfg, s1_client)
    s1_job = A03CreativeJob.create(
        ReasoningRequest.create(brief, operation="EXPLORE", count=2),
        journey_id="r5a", cycle_id=f"{case.case_id}:{tag}", job_id="s1:explore", attempt=1,
        correlation_id=f"corr:s1:{case.case_id}:{tag}", provenance=prov,
        configuration=cfg, purpose="TEST_ONLY_MOCK",
    )
    s1_stage = run_creative_stage(s1_job, sidecars, s1_reasoner)
    retry_stages = ()
    if include_retry_s1:
        err_client = _R5ABenchmarkMockClient(
            usage_by_op={"EXPLORE": UsageObservation("OBSERVED", 120, 30)},
            fault="error", req_prefix=f"s1-retry-{case.case_id}-{tag}",
        )
        retry_job = A03CreativeJob.create(
            ReasoningRequest.create(brief, operation="EXPLORE", count=2),
            journey_id="r5a", cycle_id=f"{case.case_id}:{tag}", job_id="s1:explore:retry", attempt=1,
            correlation_id=f"corr:s1:retry:{case.case_id}:{tag}", provenance=prov,
            configuration=cfg, purpose="TEST_ONLY_MOCK",
        )
        retry_stages = (run_creative_stage(retry_job, sidecars, LiveCreativeReasoner(cfg, err_client)),)

    obs_s1 = observe_benchmark_run(
        case, sidecars, strategy="S1", sample_id=f"sample:{case.case_id}:s1:{tag}", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END,
        stages=(s1_stage,), retry_stages=retry_stages,
        step_timings_ms={"explore:0": 1200, "retry:explore:0": 400},
        step_retries={"explore:0": 0, "retry:explore:0": 0},
        queue_wait_ms=150, retry_wall_time_ms=400 if include_retry_s1 else 0, human_active_time_ms=None,
    )

    # S2: generator + independent critic (genuinely separate workflow & receipts).
    s2_client = _R5ABenchmarkMockClient(usage_by_op=usage_map, req_prefix=f"s2-{case.case_id}-{tag}")
    s2_reasoner = LiveCreativeReasoner(cfg, s2_client)
    s2_explore_job = A03CreativeJob.create(
        ReasoningRequest.create(brief, operation="EXPLORE", count=2),
        journey_id="r5a", cycle_id=f"{case.case_id}:{tag}", job_id="s2:explore", attempt=1,
        correlation_id=f"corr:s2:{case.case_id}:{tag}", provenance=prov,
        configuration=cfg, purpose="TEST_ONLY_MOCK",
    )
    s2_explore = run_creative_stage(s2_explore_job, sidecars, s2_reasoner)
    explored_concept = s2_explore.reasoning.concepts()[0]
    s2_critic_job = A03CreativeJob.create(
        ReasoningRequest.create(brief, operation="CRITIQUE", source=explored_concept),
        journey_id="r5a", cycle_id=f"{case.case_id}:{tag}", job_id="s2:critic", attempt=1,
        correlation_id=f"corr:s2:critic:{case.case_id}:{tag}", provenance=prov,
        configuration=cfg, purpose="TEST_ONLY_MOCK",
    )
    s2_critic = run_creative_stage(s2_critic_job, sidecars, s2_reasoner)
    obs_s2 = observe_benchmark_run(
        case, sidecars, strategy="S2", sample_id=f"sample:{case.case_id}:s2:{tag}", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END,
        stages=(s2_explore, s2_critic),
        step_timings_ms={"explore:0": 1300, "critique:1": 900},
        step_retries={"explore:0": 0, "critique:1": 0},
        queue_wait_ms=200, retry_wall_time_ms=0, human_active_time_ms=5000,
    )

    # S3: source-research + generator + independent critics + selector + bounded developer + R3 package.
    s3_cycle_client = _R5ABenchmarkMockClient(usage_by_op=usage_map, req_prefix=f"s3-cycle-{case.case_id}-{tag}")
    s3_cycle_job = cycle_job(
        brief=brief, count=2, evidence=evidence,
        seed_options={"journey_id": "r5a", "cycle_id": f"{case.case_id}:{tag}", "job_id": "s3:cycle",
                      "provenance": prov, "configuration": cfg, "purpose": "TEST_ONLY_MOCK"},
    )
    s3_cycle = run_creative_cycle(s3_cycle_job, sidecars, LiveCreativeReasoner(cfg, s3_cycle_client))
    s3_prod_client = ProductionFakeClient(
        silent=silent_pkg, usage=usage_map.get("PRODUCE_PACKAGE", UsageObservation()),
        request_id=f"TEST_ONLY_MOCK:s3-prod:{case.case_id}:{tag}",
    )
    s3_prod_job = production_job(
        s3_cycle, journey_id="r5a", cycle_id=f"{case.case_id}:{tag}", job_id="s3:prod",
        provenance=prov, configuration=cfg, purpose="TEST_ONLY_MOCK",
    )
    s3_prod = run_creative_production(s3_prod_job, sidecars, LiveCreativeReasoner(cfg, s3_prod_client))

    mock_judge = BenchmarkJudgeReceipt(
        judge_receipt_id=f"judge:mock:{case.case_id}:{tag}",
        judge_kind="TEST_ONLY_MOCK",
        judge_actor_ref="judge:test-only-mock",
        case_digest=case.digest,
        source_brief_digest=case.source_brief_digest,
        evaluated_artifact_digest=s3_prod.package.digest,
        scores=BenchmarkRubricScores(
            brief_adherence=0.85,
            originality_non_duplication=0.80,
            factual_evidence_accuracy=0.90 if case.factual_evidence_applicable else None,
            script_scene_s0_action_s1_consistency=0.88,
            continuity_camera_audio_silence_rights_qa_readiness=0.84,
        ),
        verdict="PASS",
        rationale="TEST_ONLY_MOCK judge evaluation; never promotes to real benchmark PASS.",
        evidence_refs=tuple(e.source_ref for e in evidence),
        judge_provider=MOCK,
        provenance=prov,
        observed_at=R5A_TS_END,
    )
    s3_timings = {
        "research": 50,
        "explore": 1100,
        "critic:0:0": 600,
        "critic:0:1": 650,
        "select:0": 500,
        "develop": 800,
        "produce_package": 1500,
    }
    s3_retries = {k: 0 for k in s3_timings}
    obs_s3 = observe_benchmark_run(
        case, sidecars, strategy="S3", sample_id=f"sample:{case.case_id}:s3:{tag}", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END,
        cycle=s3_cycle, production_result=s3_prod, judge_receipt=mock_judge,
        step_timings_ms=s3_timings, step_retries=s3_retries,
        queue_wait_ms=250, retry_wall_time_ms=0, human_active_time_ms=4000,
    )
    return obs_s1, obs_s2, obs_s3


def test_r5a_positive_three_brief_classes_three_strategies_roundtrip_idempotency_and_comparison(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path / "creative")
    cases = []
    observations = []
    for idx, brief_class in enumerate(("VN_PRODUCT", "VN_NON_PRODUCT_FICTION", "SILENT_NO_CTA")):
        brief, evidence, factual_applicable = _r5a_brief_and_evidence(brief_class)
        sidecars.creative.save_brief(brief)
        case = A03BenchmarkCase.create(
            brief,
            case_id=f"case-{idx + 1}-{brief_class.lower()}",
            cohort_id="cohort-vn-r5a-v1",
            source_sha=R5A_SOURCE_SHA,
            brief_class=brief_class,
            factual_evidence_applicable=factual_applicable,
            provenance=_r5a_provenance(f"fixture:case:{brief_class}"),
            created_at=R5A_TS_START,
            language="vi",
            expected_evidence_refs=tuple(e.source_ref for e in evidence),
        )
        saved_digest = sidecars.save_benchmark_case(case)
        assert saved_digest == case.digest
        # Duplicate-safe idempotent save of identical case.
        assert sidecars.save_benchmark_case(case) == case.digest
        assert sidecars.load_benchmark_case(case.cohort_id, case.case_id) == case
        assert A03BenchmarkCase.from_json(case.to_json()) == case
        cases.append(case)
        obs_s1, obs_s2, obs_s3 = _run_r5a_strategy_workflows(
            sidecars, case, brief, evidence,
            include_retry_s1=(brief_class == "VN_PRODUCT"),
            silent_pkg=(brief_class == "SILENT_NO_CTA"),
        )
        observations.extend((obs_s1, obs_s2, obs_s3))

    # Check S1 with observed retry token accounting & UNKNOWN human time (never 0).
    vn_s1, vn_s2, vn_s3 = observations[0], observations[1], observations[2]
    assert vn_s1.status == "TEST_ONLY_MOCK" and vn_s1.benchmark_validity == "NOT_REAL_BENCHMARK"
    assert vn_s1.telemetry.input_tokens_status == "OBSERVED"
    assert vn_s1.telemetry.total_input_tokens == 400 + 120  # main EXPLORE + observed retry EXPLORE
    assert vn_s1.telemetry.total_output_tokens == 220 + 30
    assert vn_s1.telemetry.total_retries == 1
    assert vn_s1.telemetry.wall_time_status == "OBSERVED"
    assert vn_s1.telemetry.total_wall_time_ms == 1200 + 400 + 150 + 400
    assert vn_s1.telemetry.human_active_time_status == "UNKNOWN" and vn_s1.telemetry.human_active_time_ms is None
    assert vn_s1.structural_check.status == "STRUCTURAL_VALID"
    assert vn_s1.semantic_status == "UNMEASURED"  # No judge -> remains UNMEASURED, never auto-PASS.
    assert vn_s1.nsm.status == "NOT_REAL_BENCHMARK" and vn_s1.nsm.score is None

    # Check S2 independent critic + role token breakdown.
    assert vn_s2.status == "TEST_ONLY_MOCK" and len(vn_s2.critic_receipt_digests) == 1
    assert vn_s2.explore_receipt_digest != vn_s2.critic_receipt_digests[0]
    assert vn_s2.telemetry.total_input_tokens == 400 + 250
    assert vn_s2.telemetry.total_output_tokens == 220 + 140
    assert vn_s2.telemetry.human_active_time_status == "OBSERVED" and vn_s2.telemetry.human_active_time_ms == 5000

    # Check S3 full lineage (research + explore + 2 critics + select + develop + R3 package) + mock judge control.
    assert vn_s3.status == "TEST_ONLY_MOCK" and vn_s3.research_status == "VERIFIED_SOURCE_BYTES"
    assert vn_s3.package_digest is not None and vn_s3.selector_receipt_digest is not None
    assert vn_s3.develop_receipt_digest is not None and len(vn_s3.critic_receipt_digests) == 2
    assert vn_s3.telemetry.total_input_tokens == 400 + 250 + 250 + 300 + 350 + 500
    assert vn_s3.telemetry.total_output_tokens == 220 + 140 + 140 + 110 + 180 + 420
    assert vn_s3.structural_check.status == "STRUCTURAL_VALID"
    assert vn_s3.semantic_status == "TEST_ONLY_MOCK"  # Mock judge NEVER promotes to MEASURED_PASS.
    assert vn_s3.nsm.status == "NOT_REAL_BENCHMARK" and vn_s3.nsm.score is None

    # Check silent/no-CTA S3 package structural silence check.
    silent_s3 = observations[8]
    assert silent_s3.brief_class == "SILENT_NO_CTA"
    assert silent_s3.structural_check.status == "STRUCTURAL_VALID"
    assert silent_s3.structural_check.continuity_camera_audio_silence_valid is True

    # Strict schema roundtrip + duplicate-safe observation save.
    for obs in observations:
        assert A03BenchmarkObservation.from_json(obs.to_json()) == obs
        assert sidecars.save_benchmark_observation(obs) == obs.digest
        assert sidecars.load_benchmark_observation(
            obs.cohort_id, obs.case_id, obs.strategy, obs.sample_id, obs.attempt,
        ) == obs

    # Paired comparison across all 3 strategy labels on fixed 3-case cohort.
    comp = compare_benchmark_observations(
        comparison_id="cmp-r5a-mock-v1",
        cases=tuple(cases),
        observations=tuple(observations),
        store=sidecars,
        provenance=_r5a_provenance("fixture:comparison"),
        created_at=R5A_TS_END,
        strategies=BENCHMARK_STRATEGIES,
    )
    assert comp.outcome == "NOT_REAL_BENCHMARK"
    assert comp.ranking_status == "UNRANKED" and comp.winner_strategy is None
    assert comp.nsm_version == NSM_V1_VERSION and comp.nsm_weights == NSM_V1_WEIGHTS
    assert len(comp.paired_case_ids) == 3
    assert all(comp.missing_cases_by_strategy[s] == () for s in BENCHMARK_STRATEGIES)
    by_s = {s.strategy: s for s in comp.strategy_summaries}
    assert by_s["S1"].total_input_tokens == (400 + 120) + 400 + 400
    assert by_s["S1"].total_human_active_time_ms is None  # UNKNOWN stays None, never 0!
    assert by_s["S2"].total_input_tokens == (400 + 250) * 3
    assert by_s["S3"].total_input_tokens == (400 + 250 + 250 + 300 + 350 + 500) * 3
    assert all(s.mean_nsm_v1 is None and s.measured_semantic_pass_count == 0 for s in comp.strategy_summaries)
    assert A03BenchmarkComparison.from_json(comp.to_json()) == comp
    assert sidecars.save_benchmark_comparison(comp) == comp.digest
    assert sidecars.load_benchmark_comparison(comp.cohort_id, comp.comparison_id) == comp


def test_r5a_positive_nsm_v1_formula_stability_and_renormalization():
    assert abs(sum(NSM_V1_WEIGHTS.values()) - 1.0) < 1e-9
    prod_scores = BenchmarkRubricScores(
        brief_adherence=0.9,
        originality_non_duplication=0.8,
        factual_evidence_accuracy=0.85,
        script_scene_s0_action_s1_consistency=0.88,
        continuity_camera_audio_silence_rights_qa_readiness=0.82,
    )
    res1 = calculate_nsm_v1_score(prod_scores, total_tokens=10000, wall_time_ms=30000, human_active_time_ms=15000)
    res2 = calculate_nsm_v1_score(prod_scores, total_tokens=10000, wall_time_ms=30000, human_active_time_ms=15000)
    assert res1 == res2 and res1["version"] == "NSM_v1"
    assert 0.0 < res1["score"] < 1.0
    # Higher token / wall / human cost strictly decreases efficiency & composite score.
    heavier = calculate_nsm_v1_score(prod_scores, total_tokens=40000, wall_time_ms=90000, human_active_time_ms=60000)
    assert heavier["score"] < res1["score"]
    # Non-product / silent briefs with factual_evidence_accuracy=None renormalize cleanly.
    fiction_scores = BenchmarkRubricScores(
        brief_adherence=0.9,
        originality_non_duplication=0.9,
        factual_evidence_accuracy=None,
        script_scene_s0_action_s1_consistency=0.9,
        continuity_camera_audio_silence_rights_qa_readiness=0.9,
    )
    fic_res = calculate_nsm_v1_score(fiction_scores, total_tokens=20000, wall_time_ms=60000, human_active_time_ms=60000)
    assert fic_res["quality_component"] == 0.9
    assert fic_res["token_efficiency_component"] == 0.5
    assert fic_res["wall_time_efficiency_component"] == 0.5
    assert fic_res["human_time_efficiency_component"] == 0.5
    assert fic_res["score"] == round(0.55 * 0.9 + 0.20 * 0.5 + 0.15 * 0.5 + 0.10 * 0.5, 6)


def test_r5a_positive_exact_r4_a04_receipt_correlation_and_consumer_separation(installed_cli, tmp_path, monkeypatch):
    from ai_commerce.media.generation import A04CreativeHandoffReceipt, a04_handoff_receipt_path
    from ai_commerce.production_orchestration import LocalAuthorization, ProductionPipelineOrchestrator

    cli = installed_cli
    brief, evidence, factual_applicable = _r5a_brief_and_evidence("SILENT_NO_CTA")
    r2_sampler, r3_sampler = CycleFakeClient(), ProductionFakeClient(silent=True)

    def dispatch(packet):
        req = packet["request"]
        creative = ReasoningRequest.from_dict(req["creative"])
        model_req = StructuredModelRequest(ModelConfiguration.from_dict(req["config"]), creative)
        if creative.production is not None:
            return json.loads(r3_sampler.complete(model_req).output_json)
        val = json.loads(r2_sampler.complete(model_req).output_json)
        if creative.operation == "DEVELOP":
            val["edits"] = {n: val["edits"].get(n) for n in ("family", "title", "premise", "visual_direction")}
        return val

    cli.creative_output = dispatch
    install_fake_popen(monkeypatch, cli)
    grant = InvocationAuthorization(True, True, "fixture:r5a-r4-grant")
    root = tmp_path / "r5a-r4-creative"
    sidecars = CreativeSidecarStore(root)
    sidecars.creative.save_brief(brief)
    prov = _r5a_provenance("fixture:r5a-r4-cli")
    case = A03BenchmarkCase.create(
        brief, case_id="case-r4-silent", cohort_id="cohort-r4-link", source_sha=R5A_SOURCE_SHA,
        brief_class="SILENT_NO_CTA", factual_evidence_applicable=factual_applicable,
        provenance=prov, created_at=R5A_TS_START, expected_evidence_refs=tuple(e.source_ref for e in evidence),
    )
    sidecars.save_benchmark_case(case)

    reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability(), runner=None))
    cycle = run_creative_cycle(
        cycle_job(brief=brief, evidence=evidence, seed_options={
            "journey_id": "r5a:r4", "cycle_id": "r5a:c1", "job_id": "r5a:p1",
            "provenance": prov, "configuration": cli.config, "purpose": "PRODUCTION",
        }),
        sidecars, reasoner, authorization=grant,
    )
    prod_job = production_job(
        cycle, journey_id="r5a:r4", cycle_id="r5a:c1", job_id="r5a:p1",
        provenance=prov, configuration=cli.config, purpose="PRODUCTION",
    )
    local_grant = LocalAuthorization(
        journey_id="r5a:r4", cycle_id="r5a:c1", job_id="r5a:p1",
        source_sha=R5A_SOURCE_SHA, authorization_ref="fixture:r5a-a03-a04", route_id="a03-to-a04",
    )
    orch_dir = tmp_path / "orch"
    orch = ProductionPipelineOrchestrator(state_dir=orch_dir, source_sha=R5A_SOURCE_SHA, local_authorization=local_grant)
    prod_res = run_creative_production(prod_job, sidecars, reasoner, authorization=grant, production=orch)
    assert prod_res.status == "CREATIVE_COMPLETE" and orch.last_result is not None
    a04_receipt = orch.last_result.receiver_receipt
    assert isinstance(a04_receipt, A04CreativeHandoffReceipt)
    receipt_file = a04_handoff_receipt_path(orch_dir / "a04-handoffs", a04_receipt.receipt_id)
    assert receipt_file.exists()

    cli_calls_before = len(cli.calls)
    obs = observe_benchmark_run(
        case, sidecars, strategy="S3", sample_id="sample:r4-correlated", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END,
        cycle=cycle, production_result=prod_res,
        a04_receipt=a04_receipt, a04_receipt_path=receipt_file,
    )
    # Zero model/provider calls during benchmark observation.
    assert len(cli.calls) == cli_calls_before
    assert obs.consumer_evidence.source_handoff_status == "SOURCE_HANDOFF_VERIFIED"
    assert obs.consumer_evidence.a04_receipt == a04_receipt
    assert obs.consumer_evidence.a04_receipt_digest == a04_receipt.digest
    # Source-handoff acceptance is explicitly distinguished from media quality and commercial utility.
    assert obs.consumer_evidence.media_quality_status == "NOT_ATTEMPTED"
    assert obs.consumer_evidence.commercial_utility_status == "NOT_ATTEMPTED"
    # Synthetic fixture provenance keeps validity NOT_REAL_BENCHMARK and NSM unscored.
    assert obs.benchmark_validity == "NOT_REAL_BENCHMARK"
    assert obs.nsm.status == "NOT_REAL_BENCHMARK" and obs.nsm.score is None
    assert "VERIFIED_USEFUL_CONSUMER_OUTCOME" in obs.nsm.missing_coverage
    assert "NON_SYNTHETIC_PROVENANCE" in obs.nsm.missing_coverage
    assert sidecars.load_benchmark_observation_by_digest(obs.digest) == obs
    # Altered A04 receipt attempt or source_sha fails closed on save.
    bad_a04_attempt = A04CreativeHandoffReceipt.create(**{
        **{k: v for k, v in a04_receipt.to_dict().items() if k != "receipt_id"},
        "attempt": a04_receipt.attempt + 1,
    })
    with pytest.raises(CreativeValidationError):
        observe_benchmark_run(
            case, sidecars, strategy="S3", sample_id="sample:r4-bad-attempt", attempt=1,
            provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END,
            cycle=cycle, production_result=prod_res, a04_receipt=bad_a04_attempt,
        )


def test_r5a_negative_forged_model_judge_and_consumer_receipts(tmp_path):
    from ai_commerce.media.generation import A04CreativeHandoffReceipt

    sidecars = CreativeSidecarStore(tmp_path / "creative")
    brief, evidence, factual_applicable = _r5a_brief_and_evidence("VN_PRODUCT")
    sidecars.creative.save_brief(brief)
    prov = _r5a_provenance("fixture:neg-forged")
    case = A03BenchmarkCase.create(
        brief, case_id="case-forged", cohort_id="cohort-neg", source_sha=R5A_SOURCE_SHA,
        brief_class="VN_PRODUCT", factual_evidence_applicable=factual_applicable,
        provenance=prov, created_at=R5A_TS_START, expected_evidence_refs=tuple(e.source_ref for e in evidence),
    )
    sidecars.save_benchmark_case(case)
    obs_s1, _, obs_s3 = _run_r5a_strategy_workflows(sidecars, case, brief, evidence)

    # 1) Forged/unpersisted model receipt digest rejected on save.
    forged_wire = obs_s1.to_dict()
    forged_wire["role_traces"][0]["receipt_digest"] = "f" * 64
    forged_wire["explore_receipt_digest"] = "f" * 64
    forged_obs = A03BenchmarkObservation.from_dict(forged_wire)
    with pytest.raises((CreativeValidationError, FileNotFoundError)):
        sidecars.save_benchmark_observation(forged_obs)

    # 2) Self-reported model judge & replayed execution_ref rejected.
    base_scores = BenchmarkRubricScores(0.8, 0.8, 0.8, 0.8, 0.8)
    real_prov = CreativeJobProvenance("human:reviewer", "review:1", sha256_bytes(b"review:1"), "HUMAN_SUPPLIED")
    with pytest.raises(CreativeValidationError):
        BenchmarkJudgeReceipt(
            judge_receipt_id="j:self", judge_kind="TEST_ONLY_MOCK", judge_actor_ref="self:generator",
            case_digest=case.digest, source_brief_digest=case.source_brief_digest,
            evaluated_artifact_digest=obs_s3.package_digest, scores=base_scores,
            verdict="PASS", rationale="Self reported", provenance=prov, observed_at=R5A_TS_END,
        )
    # Judge reusing workflow provider_request_id or receipt_digest rejected by observation.
    replayed_judge = BenchmarkJudgeReceipt(
        judge_receipt_id="j:replay", judge_kind="INDEPENDENT_MODEL_JUDGE", judge_actor_ref="judge:model-x",
        case_digest=case.digest, source_brief_digest=case.source_brief_digest,
        evaluated_artifact_digest=obs_s3.package_digest, scores=base_scores,
        verdict="PASS", rationale="Replayed execution ref",
        judge_provider=ProviderObservation("openai", "gpt-judge", "MODEL"),
        judge_execution_ref=obs_s3.role_traces[1].provider_request_id,
        provenance=real_prov, observed_at=R5A_TS_END,
    )
    replayed_wire = obs_s3.to_dict()
    replayed_wire["judge_receipt"] = replayed_judge.to_dict()
    with pytest.raises(CreativeValidationError):
        A03BenchmarkObservation.from_dict(replayed_wire)

    # Synthetic fixture masquerading as INDEPENDENT_MODEL_JUDGE or HUMAN_REVIEWER rejected.
    with pytest.raises(CreativeValidationError):
        BenchmarkJudgeReceipt(
            judge_receipt_id="j:fake-model", judge_kind="INDEPENDENT_MODEL_JUDGE", judge_actor_ref="judge:m",
            case_digest=case.digest, source_brief_digest=case.source_brief_digest,
            evaluated_artifact_digest=obs_s3.package_digest, scores=base_scores,
            verdict="PASS", rationale="Fake model judge",
            judge_provider=MOCK, judge_execution_ref="exec:1", provenance=real_prov, observed_at=R5A_TS_END,
        )
    with pytest.raises(CreativeValidationError):
        BenchmarkJudgeReceipt(
            judge_receipt_id="j:fake-human", judge_kind="HUMAN_REVIEWER", judge_actor_ref="human:fake",
            case_digest=case.digest, source_brief_digest=case.source_brief_digest,
            evaluated_artifact_digest=obs_s3.package_digest, scores=base_scores,
            verdict="PASS", rationale="Synthetic human", provenance=prov, observed_at=R5A_TS_END,
        )

    # Unverified judge citation & missing factual_evidence_accuracy on VN_PRODUCT rejected.
    bad_cite_judge = BenchmarkJudgeReceipt(
        judge_receipt_id="j:bad-cite", judge_kind="TEST_ONLY_MOCK", judge_actor_ref="judge:mock",
        case_digest=case.digest, source_brief_digest=case.source_brief_digest,
        evaluated_artifact_digest=obs_s3.package_digest, scores=base_scores,
        verdict="PASS", rationale="Unverified cite", evidence_refs=("https://fabricated.invalid/doc",),
        provenance=prov, observed_at=R5A_TS_END,
    )
    bad_cite_wire = obs_s3.to_dict()
    bad_cite_wire["judge_receipt"] = bad_cite_judge.to_dict()
    with pytest.raises(CreativeValidationError):
        A03BenchmarkObservation.from_dict(bad_cite_wire)

    missing_fact_judge = BenchmarkJudgeReceipt(
        judge_receipt_id="j:no-fact", judge_kind="TEST_ONLY_MOCK", judge_actor_ref="judge:mock",
        case_digest=case.digest, source_brief_digest=case.source_brief_digest,
        evaluated_artifact_digest=obs_s3.package_digest,
        scores=BenchmarkRubricScores(0.8, 0.8, None, 0.8, 0.8),
        verdict="PASS", rationale="Omitted factual score", provenance=prov, observed_at=R5A_TS_END,
    )
    missing_fact_wire = obs_s3.to_dict()
    missing_fact_wire["judge_receipt"] = missing_fact_judge.to_dict()
    with pytest.raises(CreativeValidationError):
        sidecars.save_benchmark_observation(A03BenchmarkObservation.from_dict(missing_fact_wire))

    # 3) Forged consumer / A04 handoff receipt rejected.
    fake_a04 = A04CreativeHandoffReceipt.create(
        journey_id="r5a", cycle_id="c1", job_id="j1", attempt=1,
        actor_ref="AI:reasoning:" + obs_s3.package_receipt_digest,
        job_actor_ref=prov.actor_ref, source_sha=R5A_SOURCE_SHA,
        a03_result_ref="fake/result.json", a03_result_digest=obs_s3.production_result_digest,
        package_id=obs_s3.package_id, revision=1, parent_package_digest=None,
        package_ref="fake/pkg.json", package_digest=obs_s3.package_digest,
        brief_id=case.brief_id, source_brief_digest=case.source_brief_digest,
        selection_id="selection:" + "1" * 64, concept_id="concept:" + "2" * 64,
        concept_revision=2, concept_digest="3" * 64,
        cycle_job_digest=obs_s3.cycle_job_digest, cycle_result_digest=obs_s3.cycle_result_digest,
        reasoning_receipt_digest=obs_s3.package_receipt_digest,
        audio_mode="SILENCE", scene_count=2, media_target_count=2,
    )
    # Cannot attach A04 handoff receipt to a TEST_ONLY_MOCK observation.
    mock_a04_wire = obs_s3.to_dict()
    mock_a04_wire["consumer_evidence"] = BenchmarkConsumerEvidence(
        source_handoff_status="SOURCE_HANDOFF_VERIFIED",
        a04_receipt=fake_a04, a04_receipt_digest=fake_a04.digest,
    ).to_dict()
    with pytest.raises(CreativeValidationError):
        A03BenchmarkObservation.from_dict(mock_a04_wire)
    # Cannot reuse A04 receipt ID as media quality or commercial utility evidence.
    with pytest.raises(CreativeValidationError):
        BenchmarkConsumerEvidence(
            source_handoff_status="SOURCE_HANDOFF_VERIFIED",
            a04_receipt=fake_a04, a04_receipt_digest=fake_a04.digest,
            commercial_utility_status="OBSERVED_USEFUL",
            commercial_utility_ref=fake_a04.receipt_id,
        )
    with pytest.raises(CreativeValidationError):
        BenchmarkConsumerEvidence(
            source_handoff_status="SOURCE_HANDOFF_VERIFIED",
            a04_receipt=fake_a04, a04_receipt_digest=fake_a04.digest,
            media_quality_status="OBSERVED_PASS",
            media_quality_ref=fake_a04.receipt_id,
        )
    # Tampered A04 receipt fields (e.g. qa_status=PASS or altered package_digest) rejected.
    bad_a04_dict = {**fake_a04.to_dict(), "qa_status": "PASS"}
    with pytest.raises(CreativeValidationError):
        BenchmarkConsumerEvidence.from_dict({
            "source_handoff_status": "SOURCE_HANDOFF_VERIFIED",
            "a04_receipt": bad_a04_dict,
            "a04_receipt_digest": fake_a04.digest,
            "a04_receipt_ref": None,
            "media_quality_status": "NOT_ATTEMPTED",
            "media_quality_ref": None,
            "commercial_utility_status": "NOT_ATTEMPTED",
            "commercial_utility_ref": None,
        })
    # Tampering persisted ReasoningResult bytes on disk fails load.
    reason_path = sidecars._path("reasoning", obs_s1.explore_receipt_digest)
    reason_path.write_bytes(reason_path.read_bytes() + b" ")
    with pytest.raises(CreativeValidationError):
        sidecars.load_benchmark_observation_by_digest(obs_s1.digest)


def test_r5a_negative_mock_as_model_strategy_violations_and_failed_unknown_provider(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path / "creative")
    brief, evidence, factual_applicable = _r5a_brief_and_evidence("VN_PRODUCT")
    sidecars.creative.save_brief(brief)
    prov = _r5a_provenance("fixture:neg-strategy")
    case = A03BenchmarkCase.create(
        brief, case_id="case-strat", cohort_id="cohort-strat", source_sha=R5A_SOURCE_SHA,
        brief_class="VN_PRODUCT", factual_evidence_applicable=factual_applicable,
        provenance=prov, created_at=R5A_TS_START, expected_evidence_refs=tuple(e.source_ref for e in evidence),
    )
    sidecars.save_benchmark_case(case)
    cfg = ModelConfiguration("requested-provider", "requested-model", "xhigh")

    # Single generator stage run.
    s1_client = _R5ABenchmarkMockClient(usage_by_op={"EXPLORE": UsageObservation("OBSERVED", 300, 150)}, req_prefix="neg-s1")
    s1_stage = run_creative_stage(
        A03CreativeJob.create(
            ReasoningRequest.create(brief, operation="EXPLORE", count=2),
            journey_id="r5a", cycle_id="neg", job_id="neg:s1", attempt=1,
            correlation_id="corr:neg:s1", provenance=prov, configuration=cfg, purpose="TEST_ONLY_MOCK",
        ),
        sidecars, LiveCreativeReasoner(cfg, s1_client),
    )

    # 1) Missing independent critic for S2 -> HOLD (never valid S2).
    s2_no_critic = observe_benchmark_run(
        case, sidecars, strategy="S2", sample_id="sample:s2-missing-critic", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END, stages=(s1_stage,),
    )
    assert s2_no_critic.status == "HOLD" and "MISSING_INDEPENDENT_CRITIC" in s2_no_critic.hold_reasons
    assert s2_no_critic.benchmark_validity == "HOLD" and s2_no_critic.nsm.score is None
    # Forging wire status="TEST_ONLY_MOCK" without critic raises CreativeValidationError.
    forged_s2 = {**s2_no_critic.to_dict(), "status": "TEST_ONLY_MOCK", "hold_reasons": [],
                 "benchmark_validity": "NOT_REAL_BENCHMARK"}
    with pytest.raises(CreativeValidationError):
        A03BenchmarkObservation.from_dict(forged_s2)

    # 2) Absent research for S3 (R2 cycle without verified evidence) -> HOLD MISSING_RESEARCH_FOR_S3.
    r2_no_ev = run_creative_cycle(
        cycle_job(brief=brief, count=2, evidence=(), seed_options={
            "journey_id": "r5a", "cycle_id": "neg", "job_id": "neg:r2-no-ev",
            "provenance": prov, "configuration": cfg, "purpose": "TEST_ONLY_MOCK",
        }),
        sidecars, LiveCreativeReasoner(cfg, _R5ABenchmarkMockClient(req_prefix="neg-r2-no-ev")),
    )
    s3_no_research = observe_benchmark_run(
        case, sidecars, strategy="S3", sample_id="sample:s3-no-research", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END, cycle=r2_no_ev,
    )
    assert s3_no_research.status == "HOLD" and "MISSING_RESEARCH_FOR_S3" in s3_no_research.hold_reasons
    assert s3_no_research.nsm.score is None

    # 3) Claiming hard-coded R2 cycle ran S1 or S2 -> HOLD STRATEGY_WORKFLOW_MISMATCH.
    r2_as_s1 = observe_benchmark_run(
        case, sidecars, strategy="S1", sample_id="sample:r2-as-s1", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END, cycle=r2_no_ev,
    )
    assert r2_as_s1.status == "HOLD" and "STRATEGY_WORKFLOW_MISMATCH" in r2_as_s1.hold_reasons
    r2_as_s2 = observe_benchmark_run(
        case, sidecars, strategy="S2", sample_id="sample:r2-as-s2", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END, cycle=r2_no_ev,
    )
    assert r2_as_s2.status == "HOLD" and "STRATEGY_WORKFLOW_MISMATCH" in r2_as_s2.hold_reasons

    # 4) Missing independent critic for S3 (only generator stage) -> HOLD with MISSING_INDEPENDENT_CRITIC + MISSING_RESEARCH_FOR_S3.
    s3_no_critic = observe_benchmark_run(
        case, sidecars, strategy="S3", sample_id="sample:s3-no-critic", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END, stages=(s1_stage,),
    )
    assert s3_no_critic.status == "HOLD"
    assert {"MISSING_RESEARCH_FOR_S3", "MISSING_INDEPENDENT_CRITIC", "MISSING_BOUNDED_DEVELOPER"} <= set(s3_no_critic.hold_reasons)

    # 5) Mock-as-model wire promotion rejected.
    valid_s1 = observe_benchmark_run(
        case, sidecars, strategy="S1", sample_id="sample:valid-s1", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END, stages=(s1_stage,),
    )
    for bad_fields in (
        {"status": "REAL_MODEL_OBSERVED"},
        {"execution_mode": "MODEL"},
        {"benchmark_validity": "ELIGIBLE_REAL_BENCHMARK"},
        {"observed_provider": {"provider": "requested-provider", "model": "requested-model", "mode": "MODEL"}},
    ):
        with pytest.raises(CreativeValidationError):
            A03BenchmarkObservation.from_dict({**valid_s1.to_dict(), **bad_fields})

    # 6) Failed / refused / UNKNOWN provider effect -> HOLD, never WIN/PASS.
    for fault_name in ("error", "refusal"):
        bad_stage = run_creative_stage(
            A03CreativeJob.create(
                ReasoningRequest.create(brief, operation="EXPLORE", count=2),
                journey_id="r5a", cycle_id="neg", job_id=f"neg:{fault_name}", attempt=1,
                correlation_id=f"corr:neg:{fault_name}", provenance=prov, configuration=cfg, purpose="TEST_ONLY_MOCK",
            ),
            sidecars, LiveCreativeReasoner(cfg, _R5ABenchmarkMockClient(fault=fault_name, req_prefix=fault_name)),
        )
        bad_obs = observe_benchmark_run(
            case, sidecars, strategy="S1", sample_id=f"sample:{fault_name}", attempt=1,
            provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END, stages=(bad_stage,),
        )
        assert bad_obs.status == "HOLD" and "PROVIDER_EFFECT_FAILED_OR_UNKNOWN" in bad_obs.hold_reasons
        assert bad_obs.errors and bad_obs.nsm.score is None


def test_r5a_negative_altered_digests_tampering_noncomparable_cohort_stale_source_and_replays(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path / "creative")
    brief_a, ev_a, fact_a = _r5a_brief_and_evidence("VN_PRODUCT")
    brief_b, ev_b, fact_b = _r5a_brief_and_evidence("VN_NON_PRODUCT_FICTION")
    sidecars.creative.save_brief(brief_a)
    sidecars.creative.save_brief(brief_b)
    prov = _r5a_provenance("fixture:neg-comp")

    case_a = A03BenchmarkCase.create(
        brief_a, case_id="case-a", cohort_id="cohort-1", source_sha=R5A_SOURCE_SHA,
        brief_class="VN_PRODUCT", factual_evidence_applicable=fact_a,
        provenance=prov, created_at=R5A_TS_START, expected_evidence_refs=tuple(e.source_ref for e in ev_a),
    )
    case_b = A03BenchmarkCase.create(
        brief_b, case_id="case-b", cohort_id="cohort-1", source_sha=R5A_SOURCE_SHA,
        brief_class="VN_NON_PRODUCT_FICTION", factual_evidence_applicable=fact_b,
        provenance=prov, created_at=R5A_TS_START, expected_evidence_refs=tuple(e.source_ref for e in ev_b),
    )
    case_stale = A03BenchmarkCase.create(
        brief_b, case_id="case-stale", cohort_id="cohort-1", source_sha="b" * 40,
        brief_class="VN_NON_PRODUCT_FICTION", factual_evidence_applicable=fact_b,
        provenance=prov, created_at=R5A_TS_START, expected_evidence_refs=tuple(e.source_ref for e in ev_b),
    )
    case_other_cohort = A03BenchmarkCase.create(
        brief_b, case_id="case-other", cohort_id="cohort-2", source_sha=R5A_SOURCE_SHA,
        brief_class="VN_NON_PRODUCT_FICTION", factual_evidence_applicable=fact_b,
        provenance=prov, created_at=R5A_TS_START, expected_evidence_refs=tuple(e.source_ref for e in ev_b),
    )
    for c in (case_a, case_b, case_stale, case_other_cohort):
        sidecars.save_benchmark_case(c)

    obs_a_s1, obs_a_s2, obs_a_s3 = _run_r5a_strategy_workflows(sidecars, case_a, brief_a, ev_a, tag="a")
    obs_b_s1, obs_b_s2, _ = _run_r5a_strategy_workflows(sidecars, case_b, brief_b, ev_b, tag="b")
    obs_stale_s1, _, _ = _run_r5a_strategy_workflows(sidecars, case_stale, brief_b, ev_b, tag="stale")
    obs_other_s1, _, _ = _run_r5a_strategy_workflows(sidecars, case_other_cohort, brief_b, ev_b, tag="other")

    # 1) Noncomparable / unpaired briefs: S1 ran only on case-a, S2 ran only on case-b -> INSUFFICIENT_EVIDENCE.
    unpaired = compare_benchmark_observations(
        comparison_id="cmp-unpaired",
        cases=(case_a, case_b),
        observations=(obs_a_s1, obs_b_s2),
        store=sidecars, provenance=prov, created_at=R5A_TS_END, strategies=("S1", "S2"),
    )
    assert unpaired.outcome == "INSUFFICIENT_EVIDENCE" and unpaired.paired_case_ids == ()
    assert unpaired.winner_strategy is None
    assert unpaired.missing_cases_by_strategy == {"S1": ("case-b",), "S2": ("case-a",)}

    # 2) Stale source SHA -> HOLD STALE_SOURCE_SHA, no winner.
    stale_cmp = compare_benchmark_observations(
        comparison_id="cmp-stale",
        cases=(case_a, case_stale),
        observations=(obs_a_s1, obs_stale_s1),
        store=sidecars, provenance=prov, created_at=R5A_TS_END, strategies=("S1", "S2"),
    )
    assert stale_cmp.outcome == "HOLD" and "STALE_SOURCE_SHA" in stale_cmp.hold_reasons
    assert stale_cmp.winner_strategy is None

    # 3) Noncomparable cohort -> HOLD NONCOMPARABLE_COHORT, no winner.
    cross_cohort = compare_benchmark_observations(
        comparison_id="cmp-cross-cohort",
        cases=(case_a,),
        observations=(obs_a_s1, obs_other_s1),
        store=sidecars, provenance=prov, created_at=R5A_TS_END, strategies=("S1", "S2"),
    )
    assert cross_cohort.outcome == "HOLD" and "NONCOMPARABLE_COHORT" in cross_cohort.hold_reasons
    assert cross_cohort.winner_strategy is None

    # 4) Replayed workflow across two different strategies -> HOLD REPLAYED_WORKFLOW_ACROSS_STRATEGIES.
    cfg = ModelConfiguration("requested-provider", "requested-model", "xhigh")
    shared_stage = sidecars.load_stage(sidecars._read_job_slot(obs_a_s1.role_traces[0].job_slot))
    replayed_s2_hold = observe_benchmark_run(
        case_a, sidecars, strategy="S2", sample_id="sample:replayed-s2", attempt=1,
        provenance=prov, started_at=R5A_TS_START, completed_at=R5A_TS_END, stages=(shared_stage,),
    )
    replay_cmp = compare_benchmark_observations(
        comparison_id="cmp-replay",
        cases=(case_a,),
        observations=(obs_a_s1, replayed_s2_hold),
        store=sidecars, provenance=prov, created_at=R5A_TS_END, strategies=("S1", "S2"),
    )
    assert replay_cmp.outcome == "HOLD" and "REPLAYED_WORKFLOW_ACROSS_STRATEGIES" in replay_cmp.hold_reasons
    assert replay_cmp.winner_strategy is None

    # 5) Altered package_digest / source_brief_digest / source_sha / attempt / revision rejected.
    for mutation in ("package_digest", "source_brief_digest", "source_sha", "package_revision", "rubric_version"):
        wire = obs_a_s3.to_dict()
        if mutation == "package_digest":
            wire["package_digest"] = "0" * 64
            wire["judge_receipt"]["evaluated_artifact_digest"] = "0" * 64
        elif mutation == "source_brief_digest":
            wire["source_brief_digest"] = "0" * 64
            wire["judge_receipt"]["source_brief_digest"] = "0" * 64
        elif mutation == "source_sha":
            wire["source_sha"] = "c" * 40
        elif mutation == "package_revision":
            wire["package_revision"] = 9
        elif mutation == "rubric_version":
            wire["rubric_version"] = "a03.benchmark-rubric.v999"
        if mutation == "rubric_version":
            with pytest.raises(CreativeValidationError):
                A03BenchmarkObservation.from_dict(wire)
        else:
            candidate = A03BenchmarkObservation.from_dict(wire)
            with pytest.raises((CreativeValidationError, FileNotFoundError)):
                sidecars.save_benchmark_observation(candidate)

    # 6) Duplicate observation slot with altered content -> VersionConflict.
    conflict_wire = {**obs_a_s1.to_dict(), "completed_at": "2026-11-20T00:05:00Z"}
    with pytest.raises(VersionConflict):
        sidecars.save_benchmark_observation(A03BenchmarkObservation.from_dict(conflict_wire))

    # 7) Disk byte tampering of saved comparison detected on load.
    cmp_path = sidecars._path("benchmark-comparisons", unpaired.digest)
    cmp_path.write_bytes(cmp_path.read_bytes() + b" ")
    with pytest.raises(CreativeValidationError):
        sidecars.load_benchmark_comparison_by_digest(unpaired.digest)


def test_r5a_negative_unobserved_metrics_impossible_metrics_score_inflation_and_auth_spend(tmp_path):
    from ai_commerce.creative._validation import read_json

    sidecars = CreativeSidecarStore(tmp_path / "creative")
    brief, evidence, factual_applicable = _r5a_brief_and_evidence("VN_PRODUCT")
    sidecars.creative.save_brief(brief)
    prov = _r5a_provenance("fixture:neg-metrics")
    case = A03BenchmarkCase.create(
        brief, case_id="case-metrics", cohort_id="cohort-metrics", source_sha=R5A_SOURCE_SHA,
        brief_class="VN_PRODUCT", factual_evidence_applicable=factual_applicable,
        provenance=prov, created_at=R5A_TS_START, expected_evidence_refs=tuple(e.source_ref for e in evidence),
    )
    sidecars.save_benchmark_case(case)

    # 1) Unobserved tokens / wall time / human time remain UNKNOWN (None), NEVER 0.
    obs_s1, obs_s2, _ = _run_r5a_strategy_workflows(
        sidecars, case, brief, evidence, tag="unknown",
        usage_by_op={"EXPLORE": UsageObservation(), "CRITIQUE": UsageObservation("OBSERVED", 200, 100)},
    )
    assert obs_s1.telemetry.input_tokens_status == "UNKNOWN" and obs_s1.telemetry.total_input_tokens is None
    assert obs_s1.telemetry.output_tokens_status == "UNKNOWN" and obs_s1.telemetry.total_output_tokens is None
    # S2 has OBSERVED critic but UNKNOWN generator -> cumulative tokens MUST remain UNKNOWN, never 200+0!
    assert obs_s2.telemetry.input_tokens_status == "UNKNOWN" and obs_s2.telemetry.total_input_tokens is None
    assert "INPUT_TOKENS" in obs_s2.nsm.missing_coverage and "OUTPUT_TOKENS" in obs_s2.nsm.missing_coverage

    # 2) Negative, boolean, non-finite, and impossible metrics rejected.
    with pytest.raises(CreativeValidationError):
        BenchmarkTelemetry(input_tokens_status="UNKNOWN", total_input_tokens=0)
    with pytest.raises(CreativeValidationError):
        BenchmarkTelemetry(wall_time_status="OBSERVED", invocation_wall_time_ms=100,
                           queue_wait_ms=50, retry_wall_time_ms=0, total_wall_time_ms=999)
    with pytest.raises(CreativeValidationError):
        BenchmarkTelemetry(human_active_time_status="OBSERVED", human_active_time_ms=-1)
    with pytest.raises(CreativeValidationError):
        BenchmarkTelemetry(human_active_time_status="OBSERVED", human_active_time_ms=True)
    with pytest.raises(CreativeValidationError):
        calculate_nsm_v1_score(BenchmarkRubricScores(0.8, 0.8, 0.8, 0.8, 0.8),
                               total_tokens=-5, wall_time_ms=100, human_active_time_ms=100)
    with pytest.raises(CreativeValidationError):
        calculate_nsm_v1_score(BenchmarkRubricScores(0.8, 0.8, 0.8, 0.8, 0.8),
                               total_tokens=100, wall_time_ms=True, human_active_time_ms=100)
    # Inverted timestamps rejected.
    with pytest.raises(CreativeValidationError):
        A03BenchmarkObservation.from_dict({
            **obs_s1.to_dict(), "started_at": R5A_TS_END, "completed_at": R5A_TS_START,
        })

    # 3) Malicious score inflation rejected.
    for bad_score in (-0.01, 1.01, float("nan"), float("inf"), True):
        with pytest.raises(CreativeValidationError):
            BenchmarkRubricScores(bad_score, 0.8, 0.8, 0.8, 0.8)
    with pytest.raises(CreativeValidationError):
        BenchmarkNSMScore(
            version=NSM_V1_VERSION, status="SCORED",
            score=0.999, quality_component=0.5, token_efficiency_component=0.5,
            wall_time_efficiency_component=0.5, human_time_efficiency_component=0.5,
            missing_coverage=(),
        )
    with pytest.raises(CreativeValidationError):
        A03BenchmarkObservation.from_dict({
            **obs_s1.to_dict(),
            "nsm": {
                "version": NSM_V1_VERSION, "status": "SCORED",
                "score": 0.9, "quality_component": 0.9, "token_efficiency_component": 0.9,
                "wall_time_efficiency_component": 0.9, "human_time_efficiency_component": 0.9,
                "missing_coverage": [],
            },
        })

    # Comparison wire forgery claiming COMPARED / winner on mock data rejected.
    comp = compare_benchmark_observations(
        comparison_id="cmp-metrics", cases=(case,), observations=(obs_s1, obs_s2),
        store=sidecars, provenance=prov, created_at=R5A_TS_END, strategies=("S1", "S2"),
    )
    with pytest.raises(CreativeValidationError):
        A03BenchmarkComparison.from_dict({
            **comp.to_dict(), "outcome": "COMPARED", "ranking_status": "PAIRED_COMPARED", "winner_strategy": "S2",
        })

    # 4) Auth/spend implication & unknown/missing/duplicate JSON keys rejected across all 3 records.
    for kind, record in (
        (A03BenchmarkCase, case),
        (A03BenchmarkObservation, obs_s1),
        (A03BenchmarkComparison, comp),
    ):
        data = record.to_dict()
        for forbidden_key in ("allow_network", "allow_spend", "authorization", "api_key"):
            with pytest.raises(CreativeValidationError):
                kind.from_dict({**data, forbidden_key: True})
        if "side_effects" in data:
            with pytest.raises(CreativeValidationError):
                kind.from_dict({**data, "side_effects": "SPEND"})
        first_key = next(iter(data))
        with pytest.raises(CreativeValidationError):
            kind.from_dict({k: v for k, v in data.items() if k != first_key})
        dup_json = json.dumps(data)[:-1] + "," + json.dumps(first_key) + ":" + json.dumps(data[first_key]) + "}"
        with pytest.raises(CreativeValidationError, match="INVALID_JSON"):
            kind.from_dict(read_json(dup_json))


# R5b-1 experiment execution is restricted to explicitly injected offline fixtures.
R5B_SOURCE_SHA = "8830f60a038637f2b84ad672b7c1e92baa494f2e"
R5B_USAGE = UsageObservation("OBSERVED", 17, 5, 0, "USD")


@pytest.fixture
def r5b_experiment(tmp_path, monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 20, tzinfo=tz)

    ticks = iter(range(0, 10**12, 5_000_000))
    monkeypatch.setattr(creative_v2, "datetime", FixedClock)
    monkeypatch.setattr(creative_v2, "monotonic_ns", lambda: next(ticks))

    store = CreativeSidecarStore(tmp_path)
    cases = []
    for brief_class in ("VN_PRODUCT", "VN_NON_PRODUCT_FICTION", "SILENT_NO_CTA"):
        brief, evidence, factual = _r5a_brief_and_evidence(brief_class)
        store.creative.save_brief(brief)
        case = A03BenchmarkCase.create(
            brief, case_id=brief_class, cohort_id="r5b", source_sha=R5B_SOURCE_SHA,
            brief_class=brief_class, factual_evidence_applicable=factual,
            expected_evidence_refs=tuple(item.source_ref for item in evidence),
            provenance=_r5a_provenance("fixture:r5b-case"), created_at=R5A_TS_START,
        )
        store.save_benchmark_case(case)
        cases.append(case)
    plan = A03BenchmarkExperimentPlan.create(
        tuple(cases), plan_id="r5b-plan", repetitions=2,
        configuration=ModelConfiguration("requested-provider", "requested-model"),
        budget=BenchmarkTrialBudget(4, 1000, 100, 1, "USD"),
        purpose="TEST_ONLY_MOCK", provenance=_r5a_provenance("fixture:r5b-plan"), created_at=R5A_TS_START,
    )
    store.save_benchmark_experiment_plan(plan)
    return store, tuple(cases), plan


def _r5b_operation(store, plan, sample, **options):
    grant = BenchmarkTrialGrant(plan.digest, sample.sample_id, "fixture:one-offline-sample")
    return advance_benchmark_trial(plan, store, **{
        "source_sha": plan.source_sha, "sample_id": sample.sample_id, "grant": grant, **options,
    })


def _r5b_client(**options):
    return _R5ABenchmarkMockClient(**{
        "usage_by_op": {"EXPLORE": R5B_USAGE, "CRITIQUE": R5B_USAGE}, **options,
    })


def test_r5b_plan_roundtrip_pins_cohort_and_balances_repetitions(r5b_experiment):
    store, cases, plan = r5b_experiment
    assert A03BenchmarkExperimentPlan.from_json(plan.to_json()) == plan
    assert store.load_benchmark_experiment_plan(plan.digest) == plan
    with pytest.raises(FrozenInstanceError):
        plan.repetitions = 3
    with pytest.raises(VersionConflict):
        store.save_benchmark_experiment_plan(replace(plan, repetitions=3))
    for field in ("allow_network", "allow_spend"):
        with pytest.raises(CreativeValidationError):
            A03BenchmarkExperimentPlan.from_dict({**plan.to_dict(), field: True})
    with pytest.raises(CreativeValidationError, match="rubric"):
        replace(plan, rubric_version="a03.unknown-rubric")
    for change in ({"source_sha": "a" * 40}, {"cohort_id": "another-cohort"},
                   {"source_brief_digest": "b" * 64}, {"language": "en"}):
        with pytest.raises(CreativeValidationError, match="mismatch"):
            plan.validate_cases((cases[0], cases[1], replace(cases[2], **change)))

    samples = plan.samples
    assert len({sample.sample_id for sample in samples}) == len(samples) == 18
    assert tuple(sample.operations for sample in samples[:3]) == (
        ("EXPLORE",), ("EXPLORE", "CRITIQUE"), ("RESEARCH", "EXPLORE", "CRITIQUE", "DEVELOP"),
    )
    # An unpaired second repetition in one case cannot add weight to any case.
    paired = plan.paired_samples(tuple(sample.sample_id for sample in samples[1:]))
    assert len(paired) == 9
    assert all(sum(sample.case_digest == case.digest and sample.strategy == strategy for sample in paired) == 1
               for case in cases for strategy in BENCHMARK_STRATEGIES)
    for ids in ((samples[0].sample_id,) * 2, ("unplanned",)):
        with pytest.raises(CreativeValidationError):
            plan.paired_samples(ids)


def test_r5b_separate_roles_exact_attempt_and_offline_dependency_holds(r5b_experiment):
    store, _, plan = r5b_experiment
    one, two, three = plan.samples[-3:]
    client = _r5b_client()
    reasoner = LiveCreativeReasoner(plan.configuration, client)
    s1 = _r5b_operation(store, plan, one)
    s2 = _r5b_operation(store, plan, two)
    assert s1.status == s2.status == "READY"
    assert s1.next_job.slot != s2.next_job.slot
    assert _r5b_operation(store, plan, one, grant=None, attempt_id=s1.next_job.slot,
                          reasoner=reasoner).status == "HOLD_AUTHORIZATION"
    assert _r5b_operation(store, plan, one, source_sha="a" * 40, attempt_id=s1.next_job.slot,
                          reasoner=reasoner).status == "HOLD_SOURCE"
    hold = _r5b_operation(store, plan, three, reasoner=reasoner)
    assert hold.status == "HOLD_DEPENDENCY" and not hold.attempts and not client.calls
    real = replace(plan, plan_id="real-plan", purpose="PRODUCTION")
    store.save_benchmark_experiment_plan(real)
    assert _r5b_operation(store, real, real.samples[0], reasoner=reasoner).status == "HOLD_DEPENDENCY"

    s1_done = _r5b_operation(store, plan, one, attempt_id=s1.next_job.slot, reasoner=reasoner)
    critic = _r5b_operation(store, plan, two, attempt_id=s2.next_job.slot, reasoner=reasoner)
    assert critic.status == "READY" and critic.observation is None
    assert critic.next_job.request.source == critic.attempts[0].stage.reasoning.concepts()[0]
    assert _r5b_operation(store, plan, two, attempt_id=s2.next_job.slot, reasoner=reasoner) == critic
    assert len(client.calls) == 2
    s2_done = _r5b_operation(store, plan, two, attempt_id=critic.next_job.slot, reasoner=reasoner)
    assert _r5b_operation(store, plan, two, attempt_id=critic.next_job.slot, reasoner=reasoner) == s2_done
    assert [call.creative.operation for call in client.calls] == ["EXPLORE", "EXPLORE", "CRITIQUE"]
    assert all(call.creative.brief["audio"]["mode"] == "silence"
               and call.creative.brief["product_ref"] is None
               and call.creative.brief["cta"] is None for call in client.calls)

    for done, roles in ((s1_done, ("GENERATOR",)), (s2_done, ("GENERATOR", "CRITIC"))):
        assert done.status == "INCOMPLETE" and done.ranking_status == "UNRANKED"
        obs = done.observation
        assert obs.benchmark_validity == "NOT_REAL_BENCHMARK"
        assert obs.judge_receipt is None and obs.consumer_evidence.source_handoff_status == "NOT_AVAILABLE"
        assert obs.consumer_evidence.commercial_utility_status == "NOT_ATTEMPTED"
        assert obs.nsm.score is None
        assert tuple(trace.role for trace in obs.role_traces) == roles
        assert obs.observed_provider == MOCK and obs.observed_effort == "UNKNOWN"
        assert obs.telemetry.total_input_tokens == len(roles) * 17
        assert obs.telemetry.invocation_wall_time_ms == len(roles) * 5
        assert obs.telemetry.queue_wait_ms is None and obs.telemetry.total_wall_time_ms is None
        assert obs.telemetry.human_active_time_ms is None
        assert store.load_benchmark_observation_by_digest(obs.digest) == obs
        assert all(attempt.timing.authorization_ref == "fixture:one-offline-sample" for attempt in done.attempts)

    comparison = compare_benchmark_trials(plan, store, source_sha=plan.source_sha, created_at=R5A_TS_END)
    assert comparison.status == "NOT_REAL_BENCHMARK" and comparison.ranking_status == "UNRANKED"
    assert len(comparison.missing_sample_ids) == 16 and not comparison.comparison.paired_case_ids
    assert comparison.comparison.winner_strategy is None


@pytest.mark.parametrize(("fault", "status"), [
    ("error", "HOLD_UNKNOWN_EFFECT"),
    ("no_usage", "HOLD_BUDGET"),
    ("attempts", "HOLD_BUDGET"),
    ("tokens", "HOLD_BUDGET"),
    ("wall", "HOLD_BUDGET"),
    ("cost", "HOLD_BUDGET"),
    ("requested_effort", "HOLD_RECEIPT"),
    ("invented_citation", "HOLD_RECEIPT"),
])
def test_r5b_stops_on_unknown_effect_or_budget_and_receipt_failure(r5b_experiment, fault, status):
    store, _, plan = r5b_experiment
    changes = {"attempts": {"max_attempts": 1}, "tokens": {"max_tokens": 21},
               "wall": {"max_invocation_wall_time_ms": 4}, "cost": {"max_cost_amount": 0.01}}
    budget = replace(plan.budget, **changes.get(fault, {}))
    configuration = (replace(plan.configuration, effort="high") if fault == "requested_effort"
                     else plan.configuration)
    plan = replace(plan, plan_id=f"r5b:{fault}", budget=budget, configuration=configuration)
    store.save_benchmark_experiment_plan(plan)
    client = _r5b_client(
        fault="error" if fault == "error" else None,
        usage_by_op={"EXPLORE": UsageObservation() if fault == "no_usage" else
                     replace(R5B_USAGE, cost_amount=0.02) if fault == "cost" else R5B_USAGE},
    )
    if fault == "invented_citation":
        first, second = variants(2)
        draft = replace(first, source_refs=("source:model-invented",))
        client = FakeClient(StructuredModelResponse(
            "OK", payload((draft, second)), observation=MOCK, usage=R5B_USAGE, request_id="fixture:bad-citation",
        ))
    sample = plan.samples[1]
    ready = _r5b_operation(store, plan, sample)
    reasoner = LiveCreativeReasoner(plan.configuration, client)
    outcome = _r5b_operation(store, plan, sample, attempt_id=ready.next_job.slot, reasoner=reasoner)
    assert outcome.status == status and outcome.observation is None
    assert _r5b_operation(store, plan, sample, attempt_id=ready.next_job.slot, reasoner=reasoner) == outcome
    assert len(client.calls) == 1


def test_r5b_repairs_linked_receipt_and_accounts_for_explicit_retry(r5b_experiment, monkeypatch):
    store, _, plan = r5b_experiment
    sample = plan.samples[0]
    client = _r5b_client(fault="refusal")
    reasoner = LiveCreativeReasoner(plan.configuration, client)
    ready = _r5b_operation(store, plan, sample)
    attempt_id = ready.next_job.slot

    def interrupt(*_):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(creative_v2, "_commit_creative_stage", interrupt)
        with pytest.raises(KeyboardInterrupt):
            _r5b_operation(store, plan, sample, attempt_id=attempt_id, reasoner=reasoner)
    resumed = _r5b_operation(store, plan, sample, attempt_id=attempt_id, reasoner=reasoner)
    assert resumed.status == "RETRY_REQUIRED" and len(client.calls) == 1
    assert resumed.attempts[0].stage.reasoning.state == "REFUSED"
    assert resumed.next_job.attempt == 2 and resumed.next_job.slot != attempt_id
    client.fault = None
    outcome = _r5b_operation(store, plan, sample, attempt_id=resumed.next_job.slot, reasoner=reasoner)
    assert outcome.status == "INCOMPLETE" and len(client.calls) == 2
    assert [attempt.trace.is_retry for attempt in outcome.attempts] == [False, True]
    assert outcome.telemetry.total_input_tokens == 34 and outcome.telemetry.total_retries == 1
    assert outcome.observation.telemetry.total_retries == 1

    unobserved_sample = plan.samples[3]
    unobserved = _r5b_operation(store, plan, unobserved_sample)
    assert store.claim_job(unobserved.next_job)
    hold = _r5b_operation(store, plan, unobserved_sample, attempt_id=unobserved.next_job.slot, reasoner=reasoner)
    assert hold.status == "HOLD_UNKNOWN_EFFECT"
    assert not hold.attempts and len(client.calls) == 2


def test_r5b_comparison_rejects_replayed_provider_in_partial_strategy(r5b_experiment):
    store, _, plan = r5b_experiment
    for sample in plan.samples[:2]:
        ready = _r5b_operation(store, plan, sample)
        # New client instance starts with the same request identity and output.
        reasoner = LiveCreativeReasoner(plan.configuration, _r5b_client(req_prefix="shared"))
        _r5b_operation(store, plan, sample, attempt_id=ready.next_job.slot, reasoner=reasoner)
    comparison = compare_benchmark_trials(plan, store, source_sha=plan.source_sha, created_at=R5A_TS_END)
    assert comparison.status == "HOLD" and "REPLAYED_EXPERIMENT_RECEIPT" in comparison.reasons
    assert comparison.ranking_status == "UNRANKED" and comparison.comparison is None


def test_r5b_reconciliation_rejects_a_substituted_judge_observation(r5b_experiment):
    store, cases, plan = r5b_experiment
    sample = plan.samples[0]
    ready = _r5b_operation(store, plan, sample)
    reasoner = LiveCreativeReasoner(plan.configuration, _r5b_client())
    done = _r5b_operation(store, plan, sample, attempt_id=ready.next_job.slot, reasoner=reasoner)
    obs = done.observation
    judge = BenchmarkJudgeReceipt(
        judge_receipt_id="fixture:substituted-judge", judge_kind="TEST_ONLY_MOCK", judge_actor_ref="fixture:judge",
        case_digest=cases[0].digest, source_brief_digest=cases[0].source_brief_digest,
        evaluated_artifact_digest=obs.primary_artifact_digest,
        scores=BenchmarkRubricScores(1, 1, 1, 1, 1), verdict="PASS", rationale="Unlinked test fixture",
        provenance=plan.provenance, observed_at=R5A_TS_END,
    )
    substituted = replace(obs, judge_receipt=judge, semantic_status="TEST_ONLY_MOCK")
    store._save("benchmark-observations", substituted.digest, substituted.to_dict())
    slot = store._path("benchmark-observation-slots", substituted.slot)
    slot.write_bytes(canonical_bytes({"observation_digest": substituted.digest}))
    outcome = _r5b_operation(store, plan, sample, attempt_id=ready.next_job.slot, reasoner=reasoner)
    assert outcome.status == "HOLD_RECEIPT" and outcome.observation is None
    assert len(reasoner.client.calls) == 1


def test_sidecar_single_op_verification_has_no_stale_cache_across_mutation(tmp_path):
    sidecars = CreativeSidecarStore(tmp_path / "creative")
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    r1_job = production_job(cycle, job_id="mut:r1")
    r1_res = run_creative_production(r1_job, sidecars, cycle_adapter(ProductionFakeClient(variant="v1")))
    assert r1_res.status == "TEST_ONLY_MOCK" and r1_res.package is not None

    r2_pkg = revise_production_package(
        r1_res.package, {"narrative_intention": "Human refined cadence."},
        edit_reason="Human edit", actor=ActorRef("HUMAN", "human:editor"),
    )
    sidecars.save_production_package(r2_pkg)

    r3_job = production_job(
        cycle, job_id="mut:r3", parent_package=r2_pkg, edit_reason="AI refinement",
    )
    r3_res = run_creative_production(r3_job, sidecars, cycle_adapter(ProductionFakeClient(variant="v3")))
    assert r3_res.status == "TEST_ONLY_MOCK" and r3_res.package is not None and r3_res.package.revision == 3

    origin, pkg, handoff = sidecars.verify_production_package_lineage(
        r3_res.package.package_id, 3, origin_result_digest=r3_res.digest,
    )
    assert origin == r3_res and pkg == r3_res.package
    assert handoff["package_digest"] == r3_res.package.digest
    assert getattr(sidecars._local, "ctx", None) is None

    # Mutating the returned detached handoff dict must never affect subsequent reads.
    handoff["brief"]["objective"] = "mutated caller copy"

    critic_step = next(s for s in cycle.steps if s.job.request.operation == "CRITIQUE")
    targets = [
        sidecars.creative.root / "briefs" / f"{r3_res.package.source_brief_digest}.json",
        sidecars._path("reasoning", critic_step.reasoning.digest),
        sidecars._path("stages", critic_step.digest),
        sidecars._path("cycle-results", cycle.digest),
        sidecars.production_package_path(r1_res.package.package_id, 1),
        sidecars.production_package_digest_path(r1_res.package.digest),
    ]
    for path in targets:
        original = path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            with pytest.raises(CreativeValidationError):
                sidecars.verify_production_package_lineage(
                    r3_res.package.package_id, 3, origin_result_digest=r3_res.digest,
                )
            assert getattr(sidecars._local, "ctx", None) is None
        finally:
            path.write_bytes(original)

    # Restored exact bytes pass again with identical canonical digests.
    restored_origin, restored_pkg, restored_handoff = sidecars.verify_production_package_lineage(
        r3_res.package.package_id, 3, origin_result_digest=r3_res.digest,
    )
    assert restored_origin.digest == r3_res.digest
    assert restored_pkg.digest == r3_res.package.digest
    assert restored_handoff["brief"]["objective"] != "mutated caller copy"


def test_sidecar_verification_rejects_cyclic_reference_fail_closed(tmp_path, monkeypatch):
    sidecars = CreativeSidecarStore(tmp_path / "creative")
    brief = intake_brief("Cyclic guard check")
    b_digest = sidecars.creative.save_brief(brief)
    digest_a, digest_b = "a" * 64, "b" * 64

    class _FakeIntent:
        def __init__(self, own_digest, parent_digest):
            self.brief_id = brief["brief_id"]
            self.source_brief_digest = b_digest
            self.digest = own_digest
            self.parent_intent_digest = parent_digest

    records = {
        digest_a: _FakeIntent(digest_a, digest_b),
        digest_b: _FakeIntent(digest_b, digest_a),
    }
    monkeypatch.setattr(sidecars, "_read", lambda kind, d: {"digest": d})
    monkeypatch.setattr(CreativeIntent, "from_dict", staticmethod(lambda data: records[data["digest"]]))
    with pytest.raises(CreativeValidationError, match="cyclic sidecar reference"):
        sidecars.load_intent(digest_a)
    assert getattr(sidecars._local, "ctx", None) is None


def test_sidecar_single_op_verification_bounds_immutable_graph_revisits(tmp_path, monkeypatch):
    sidecars = CreativeSidecarStore(tmp_path / "creative")
    cycle = run_creative_cycle(cycle_job(), sidecars, cycle_adapter(CycleFakeClient()))
    r1_job = production_job(cycle, job_id="bound:r1")
    r1_res = run_creative_production(r1_job, sidecars, cycle_adapter(ProductionFakeClient(variant="v1")))
    r2_pkg = revise_production_package(
        r1_res.package, {"narrative_intention": "Bounded traversal check."},
        edit_reason="Human edit", actor=ActorRef("HUMAN", "human:director"),
    )
    sidecars.save_production_package(r2_pkg)
    r3_job = production_job(cycle, job_id="bound:r3", parent_package=r2_pkg, edit_reason="AI refinement")
    r3_res = run_creative_production(r3_job, sidecars, cycle_adapter(ProductionFakeClient(variant="v3")))

    counts = {"cycle_from_dict": 0, "result_from_dict": 0, "read_bytes": 0}
    orig_cycle_fd = A03CreativeCycleResult.from_dict
    orig_res_fd = ReasoningResult.from_dict
    orig_rb = Path.read_bytes

    def count_cycle(data):
        counts["cycle_from_dict"] += 1
        return orig_cycle_fd(data)

    def count_res(data):
        counts["result_from_dict"] += 1
        return orig_res_fd(data)

    def count_rb(self):
        counts["read_bytes"] += 1
        return orig_rb(self)

    monkeypatch.setattr(A03CreativeCycleResult, "from_dict", staticmethod(count_cycle))
    monkeypatch.setattr(ReasoningResult, "from_dict", staticmethod(count_res))
    monkeypatch.setattr(Path, "read_bytes", count_rb)

    origin, pkg, handoff = sidecars.verify_production_package_lineage(
        r3_res.package.package_id, 3, origin_result_digest=r3_res.digest,
    )
    assert origin == r3_res and pkg == r3_res.package
    assert handoff["package_digest"] == r3_res.package.digest
    assert counts["cycle_from_dict"] == 1
    assert counts["result_from_dict"] <= len(cycle.steps) + 2
    assert counts["read_bytes"] <= 100
    assert getattr(sidecars._local, "ctx", None) is None

R5B2_USAGE = replace(R5B_USAGE, cost_source_ref="fixture:cost-table")


@pytest.fixture
def r5b2_experiment(r5b_experiment, tmp_path, monkeypatch):
    store, cases, legacy = r5b_experiment
    monkeypatch.setattr(creative_live, "datetime", creative_v2.datetime)
    fixtures = tmp_path / "research-fixtures"
    fixtures.mkdir()
    product = cases[0]
    brief = store.creative.load_brief(product.source_brief_digest)
    brief = {**brief, "product_ref": None, "cta": None}
    store.creative.save_brief(brief)
    product = replace(product, case_id="VN_PRODUCT_WITHOUT_SKU", source_brief_digest=brief_digest(brief))
    store.save_benchmark_case(product)
    cases = (product, *cases[1:])
    targets = []
    for case in cases:
        _, evidence, _ = _r5a_brief_and_evidence(case.brief_class)
        source = evidence[0]
        raw = source.content.replace("\n", "\r\n").encode()
        digest = sha256_bytes(raw)
        (fixtures / digest).write_bytes(raw)
        targets.append(BenchmarkResearchTarget(case.digest, source.source_ref, digest, f"fixture:origin:{case.case_id}"))
    # A second document for the product case must be read and hashed separately.
    context = b"fixture: independent editorial context\n"
    digest = sha256_bytes(context)
    (fixtures / digest).write_bytes(context)
    targets.append(BenchmarkResearchTarget(product.digest, "source:editorial-context", digest, "fixture:editorial"))
    configs = tuple(ModelConfiguration("fixture-provider", role, effort) for role, effort in (
        ("generator", "medium"), ("critic", "high"), ("developer", "low"),
    ))
    plan = replace(legacy, plan_id="r5b2", case_digests=tuple(case.digest for case in cases),
                   configuration=configs[0], role_configurations=configs, research_targets=tuple(targets),
                   budget=replace(legacy.budget, max_invocation_wall_time_ms=60_000),
                   schema_version="A03BenchmarkExperimentPlan.v2")
    store.save_benchmark_experiment_plan(plan)
    return store, cases, plan, creative_live.LocalResearchFixtureProvider(fixtures, "fixture:custody")


class _R5B2RoleClient(_R5ABenchmarkMockClient):
    def complete(self, request):
        if request.creative.operation != "DEVELOP":
            return super().complete(request)
        self.calls.append(request)
        critique = request.creative.benchmark.critic
        return StructuredModelResponse(
            "OK", payload(edits={"title": f"Đề xuất sau phản biện {critique.receipt_digest[:12]}"}),
            observation=MOCK, usage=R5B2_USAGE, request_id=f"fixture:develop:{request.creative.digest}",
        )


def test_r5b2_receipts_bind_four_distinct_roles_and_balance_three_brief_classes(r5b2_experiment):
    store, cases, plan, provider = r5b2_experiment
    assert A03BenchmarkExperimentPlan.from_json(plan.to_json()) == plan
    clients = {
        role: _R5B2RoleClient(usage_by_op={"EXPLORE": R5B2_USAGE, "CRITIQUE": R5B2_USAGE}, req_prefix=role)
        for role in ("EXPLORE", "CRITIQUE", "DEVELOP")
    }
    first_repetitions = tuple(sample for sample in plan.samples if sample.repetition == 1)
    extra = next(sample for sample in plan.samples if sample.repetition == 2 and sample.strategy == "S1")
    for sample in (*first_repetitions, extra):
        result = _r5b_operation(store, plan, sample)
        if sample.strategy == "S3":
            request = result.next_research
            research = LiveCreativeReasoner(research_provider=provider)
            result = _r5b_operation(store, plan, sample, attempt_id=request.digest, reasoner=research)
            receipt = result.research
            assert creative_live.BenchmarkResearchReceipt.from_dict(receipt.to_dict()) == receipt
            assert receipt.request == request and receipt.custody_ref == provider.custody_ref
            assert receipt.execution_mode == "TEST_ONLY_MOCK" and receipt.trace.observed.model == "UNKNOWN"
            assert tuple(source.content.encode() for source in receipt.sources) == tuple(
                (provider.root / target.content_sha256).read_bytes() for target in request.targets
            )
            assert _r5b_operation(store, plan, sample, attempt_id=request.digest, reasoner=research) == result
        for role in (role for role in sample.operations if role != "RESEARCH"):
            job = result.next_job
            assert job.request.operation == role and job.configuration == plan.configuration_for(role)
            reasoner = LiveCreativeReasoner(job.configuration, clients[role])
            if role == "DEVELOP":
                assert job.request.benchmark.critic.receipt_digest == result.attempts[1].stage.reasoning.digest
                assert job.request.source == result.attempts[1].stage.job.request.source
            result = _r5b_operation(store, plan, sample, attempt_id=job.slot, reasoner=reasoner)
            if sample.strategy == "S3":
                assert clients[role].calls[-1].creative.benchmark.evidence == receipt.sources
        obs = result.observation
        assert result.status == "INCOMPLETE" and result.ranking_status == "UNRANKED"
        assert obs.benchmark_validity == "NOT_REAL_BENCHMARK" and obs.nsm.score is None
        assert obs.experiment_plan_digest == plan.digest and obs.sample_id == sample.sample_id
        assert tuple(trace.operation for trace in obs.role_traces) == sample.operations
        assert len({trace.receipt_digest for trace in obs.role_traces}) == len(sample.operations)
        assert all(trace.observed.model == trace.observed_effort == "UNKNOWN" for trace in obs.role_traces)
        assert all(trace.started_at and trace.completed_at for trace in obs.role_traces)
        assert all(trace.usage.cost_source_ref for trace in obs.role_traces)
        assert obs.telemetry.total_input_tokens == 17 * len(result.attempts)
        assert obs.telemetry.human_active_time_ms is None and obs.consumer_evidence == BenchmarkConsumerEvidence()
        assert store.load_benchmark_observation_by_digest(obs.digest) == A03BenchmarkObservation.from_json(obs.to_json())
        if sample.strategy == "S3":
            assert obs.role_traces[0].receipt_digest == receipt.digest
            assert result.attempts[-1].stage.reasoning.proposed_revision().revision == 2
            assert not result.attempts[-1].stage.outputs
    assert all(call.creative.brief.get("product_ref") is None and call.creative.brief.get("cta") is None
               for client in clients.values() for call in client.calls)
    assert all(call.creative.brief["audio"]["mode"] == "silence" for client in clients.values()
               for call in client.calls if call.creative.brief["brief_id"] == "brief-silent-no-cta")
    compared = compare_benchmark_trials(plan, store, source_sha=plan.source_sha, created_at=R5A_TS_END)
    assert compared.status == "NOT_REAL_BENCHMARK" and compared.ranking_status == "UNRANKED"
    assert set(compared.comparison.paired_case_ids) == {case.case_id for case in cases}
    assert len(compared.comparison.observation_digests) == 9 and compared.comparison.winner_strategy is None


@pytest.mark.parametrize("fault", ("unsupported", "bytes", "partial", "rebound", "fake_critic", "citation"))
def test_r5b2_source_and_role_provenance_fails_closed(r5b2_experiment, monkeypatch, fault):
    store, _, plan, provider = r5b2_experiment
    sample = plan.samples[2]
    ready = _r5b_operation(store, plan, sample)
    research = LiveCreativeReasoner(research_provider=provider)
    request = ready.next_research
    if fault == "unsupported":
        unsupported = LiveCreativeReasoner(research_provider=object())
        result = _r5b_operation(store, plan, sample, attempt_id=request.digest, reasoner=unsupported)
        assert (result.status, result.reason) == ("HOLD_DEPENDENCY", "UNSUPPORTED_PROVIDER")
        assert not store._path("benchmark-research-claims", request.digest).exists()
        return
    if fault == "partial":
        assert store._claim_benchmark_research(request, "fixture:one-offline-sample", provider.custody_ref)
        assert not store._claim_benchmark_research(request, "fixture:one-offline-sample", provider.custody_ref)
        monkeypatch.setattr(creative_live.LocalResearchFixtureProvider, "retrieve", lambda *_: pytest.fail("replay"))
    if fault == "bytes":
        (provider.root / request.targets[0].content_sha256).write_bytes(b"unverified fixture replacement")
    result = _r5b_operation(store, plan, sample, attempt_id=request.digest, reasoner=research)
    if fault in ("partial", "bytes"):
        assert result.status == ("HOLD_UNKNOWN_EFFECT" if fault == "partial" else "HOLD_RECEIPT")
        assert result.next_job is None and result.observation is None
        assert _r5b_operation(store, plan, sample, attempt_id=request.digest, reasoner=research) == result
        return
    job = result.next_job
    if fault == "rebound":
        for change in ({"source_brief_digest": "a" * 64}, {"plan_digest": "b" * 64}, {"sample_id": "different"}):
            rebound = replace(result.research, request=replace(request, **change), error_code="SOURCE_BYTES_UNVERIFIED",
                              sources=())
            with pytest.raises(FileNotFoundError):
                store._save_benchmark_research(rebound)
        return
    if fault == "citation":
        draft = replace(variants(1)[0], source_refs=("https://model-invented.invalid/claim",))
        client = FakeClient(StructuredModelResponse("OK", payload((draft, variants(1)[0])), observation=MOCK,
                                                    usage=R5B2_USAGE, request_id="fixture:invented"))
        result = _r5b_operation(store, plan, sample, attempt_id=job.slot,
                                reasoner=LiveCreativeReasoner(job.configuration, client))
        assert result.status == "HOLD_RECEIPT" and result.observation is None
        return
    client = _R5B2RoleClient(usage_by_op={"EXPLORE": R5B2_USAGE, "CRITIQUE": R5B2_USAGE})
    result = _r5b_operation(store, plan, sample, attempt_id=job.slot,
                            reasoner=LiveCreativeReasoner(job.configuration, client))
    assert result.next_job.request.operation == "CRITIQUE" and result.observation is None
    wrong = _r5b_operation(store, plan, sample, attempt_id=result.next_job.slot,
                           reasoner=LiveCreativeReasoner(job.configuration, client))
    assert wrong.status == "HOLD_DEPENDENCY" and len(client.calls) == 1
    result = _r5b_operation(store, plan, sample, attempt_id=result.next_job.slot,
                            reasoner=LiveCreativeReasoner(result.next_job.configuration, client))
    assert result.next_job.request.operation == "DEVELOP" and result.observation is None
    context = result.next_job.request.benchmark
    invalid = replace(result.next_job.request, benchmark=replace(
        context, critic=replace(context.critic, critique="unobserved substituted critique"),
    ))
    with pytest.raises(CreativeValidationError, match="independent critic"):
        store.validate_request(invalid)


def test_r5b2_live_grant_never_crosses_unbounded_transport(r5b2_experiment, installed_cli, monkeypatch):
    store, _, plan, _ = r5b2_experiment
    cli = CodexCliCreativeClient(installed_cli.capability())
    config = installed_cli.config
    request = _r5b_operation(store, plan, plan.samples[0]).next_job.request
    plan = replace(plan, plan_id="production-caps", configuration=config,
                   role_configurations=(config,) * 3, purpose="PRODUCTION")
    store.save_benchmark_experiment_plan(plan)
    grant = BenchmarkLiveTrialGrant(plan.digest, plan.samples[0].sample_id, config.provider, "account:fixture",
                                   "ref:fixture", "2026-11-19T00:00:00Z", "2026-11-21T00:00:00Z", plan.budget)
    assert BenchmarkLiveTrialGrant.from_dict(grant.to_dict()) == grant
    monkeypatch.setattr(creative_live, "_bounded_cli_process", lambda *a, **kw: pytest.fail("provider ran"))
    adapter = LiveCreativeReasoner(config, cli, authorization=InvocationAuthorization(True, True, grant.authorization_ref))
    options = {"source_sha": plan.source_sha, "sample_id": grant.sample_id, "reasoner": adapter}
    result = advance_benchmark_trial(plan, store, live_grant=grant, **options)
    assert (result.status, result.reason) == ("HOLD_BUDGET", "CODEX_CLI_HAS_NO_ENFORCEABLE_OUTPUT_TOKEN_OR_COST_CAP")
    assert cli.complete(StructuredModelRequest(config, request)).error_code == "HOLD_BUDGET"
    s3_grant = replace(grant, sample_id=plan.samples[2].sample_id)
    result = advance_benchmark_trial(plan, store, live_grant=s3_grant, **{**options, "sample_id": s3_grant.sample_id})
    assert (result.status, result.reason) == ("HOLD_DEPENDENCY", "NO_VERIFIED_EXTERNAL_RESEARCH_PROVIDER")
    for invalid in (replace(grant, expires_at="2026-11-20T00:00:00Z"), replace(grant, sample_id="elsewhere"),
                    replace(grant, provider="elsewhere"), replace(grant, plan_digest="f" * 64),
                    replace(grant, budget=replace(grant.budget, max_tokens=grant.budget.max_tokens + 1))):
        result = advance_benchmark_trial(plan, store, live_grant=invalid, **options)
        assert result.status == "HOLD_AUTHORIZATION"
    assert not (store.root / "jobs").exists() and not (store.root / "benchmark-research-claims").exists()


def test_r5b2_does_not_upgrade_substituted_judge_consumer_or_transport_evidence(r5b2_experiment):
    store, cases, plan, _ = r5b2_experiment
    sample = plan.samples[0]
    ready = _r5b_operation(store, plan, sample)
    job = ready.next_job
    client = _r5b_client(usage_by_op={"EXPLORE": R5B2_USAGE})
    result = _r5b_operation(store, plan, sample, reasoner=LiveCreativeReasoner(job.configuration, client),
                            attempt_id=job.slot)
    obs = result.observation
    judge = BenchmarkJudgeReceipt(
        judge_receipt_id="test:forged-judge", judge_kind="TEST_ONLY_MOCK", judge_actor_ref="test:judge",
        case_digest=cases[0].digest, source_brief_digest=cases[0].source_brief_digest,
        evaluated_artifact_digest=obs.primary_artifact_digest,
        scores=BenchmarkRubricScores(1, 1, 1, 1, 1), verdict="PASS", rationale="Unlinked fixture",
        provenance=plan.provenance, observed_at=R5A_TS_END,
    )
    with pytest.raises(CreativeValidationError, match="judge or consumer"):
        replace(obs, judge_receipt=judge)
    consumer = BenchmarkConsumerEvidence(commercial_utility_status="OBSERVED_USEFUL", commercial_utility_ref="fake:use")
    with pytest.raises(CreativeValidationError, match="judge or consumer"):
        replace(obs, consumer_evidence=consumer)
    attempt = result.attempts[0]
    reasoning = attempt.stage.reasoning
    with pytest.raises(CreativeValidationError, match="metadata rebound"):
        replace(reasoning, observation=ProviderObservation("fake:swapped", "UNKNOWN", "TEST_ONLY_MOCK"))
    timing = attempt.timing.to_dict()
    path = store._path("benchmark-timing-records", sha256_bytes(canonical_bytes(timing)))
    path.write_bytes(canonical_bytes({**timing, "wall_time_ms": 0}))
    held = _r5b_operation(store, plan, sample, reasoner=LiveCreativeReasoner(job.configuration, client),
                          attempt_id=job.slot)
    assert held.status == "HOLD_RECEIPT" and len(client.calls) == 1


@pytest.mark.parametrize("usage", (R5B_USAGE, UsageObservation(), replace(R5B2_USAGE, cost_amount=2)))
def test_r5b2_stops_before_next_role_when_cost_is_unknown_or_over_budget(r5b2_experiment, usage):
    store, _, plan, _ = r5b2_experiment
    sample = plan.samples[1]
    job = _r5b_operation(store, plan, sample).next_job
    client = _r5b_client(usage_by_op={"EXPLORE": usage})
    reasoner = LiveCreativeReasoner(job.configuration, client)
    result = _r5b_operation(store, plan, sample, reasoner=reasoner, attempt_id=job.slot)
    assert result.status == "HOLD_BUDGET" and result.observation is None and result.next_job is None
    assert _r5b_operation(store, plan, sample, reasoner=reasoner, attempt_id=job.slot) == result
    assert len(client.calls) == 1


def test_r5b2_retry_has_distinct_request_and_keeps_each_cost_observation(r5b2_experiment):
    store, _, plan, _ = r5b2_experiment
    sample = plan.samples[0]
    job = _r5b_operation(store, plan, sample).next_job
    usage = replace(R5B2_USAGE, cost_amount=0.15)
    client = _r5b_client(fault="refusal", usage_by_op={"EXPLORE": usage})
    reasoner = LiveCreativeReasoner(job.configuration, client)
    refused = _r5b_operation(store, plan, sample, reasoner=reasoner, attempt_id=job.slot)
    assert refused.status == "RETRY_REQUIRED" and refused.next_job.request_digest != job.request_digest
    assert _r5b_operation(store, plan, sample, reasoner=reasoner, attempt_id=job.slot) == refused
    client.fault = None
    done = _r5b_operation(store, plan, sample, reasoner=reasoner, attempt_id=refused.next_job.slot)
    assert done.status == "INCOMPLETE" and done.observation.telemetry.total_retries == 1
    assert done.observation.telemetry.total_cost_amount == 0.30
    assert [trace.usage for trace in done.observation.role_traces] == [usage, usage]
    assert [trace.is_retry for trace in done.observation.role_traces] == [False, True]
    assert len(client.calls) == 2
