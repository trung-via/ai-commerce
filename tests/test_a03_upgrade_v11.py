"""A03 v1.1 contract tests. All structured-client responses are explicit fakes."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import threading
import uuid

import pytest

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import (
    A03CreativeJob, A03CreativeStageResult, AIInferenceBinding,
    ActorDecision, ActorRef, CodexCliCapability, CodexCliCreativeClient,
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
)
from ai_commerce.providers.base import ProviderCapabilities, ProviderError

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
        thread_id = str(uuid.uuid4())
        context = {"model": "spoofed-model" if self.fault == "model" else self.config.model,
                   "effort": "spoofed-effort" if self.fault == "effort" else self.config.effort}
        if self.fault == "missing-model":
            context.pop("model")
        if self.fault == "missing-effort":
            context.pop("effort")
        records = [
            {"type": "session_meta", "payload": {"id": thread_id, "cli_version": self.version,
                                                   "model_provider": "openai"}},
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
