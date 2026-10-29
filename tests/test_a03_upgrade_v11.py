"""A03 v1.1 contract tests. All structured-client responses are explicit fakes."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.creative import (
    ActorDecision, ActorRef, ConceptDraft, ConceptRecord, ConceptSelection,
    CreativeIntent, CreativeSidecarStore, CreativeStore, CreativeValidationError,
    HumanSelectionEvent, IntentField, InvocationAuthorization, LiveCreativeReasoner,
    MockCreativeReasoner, ModelConfiguration, ProviderObservation, ReasoningRequest,
    ReasoningResult, StructuredModelRequest, StructuredModelResponse, UsageObservation,
    VersionConflict, brief_digest, capture_intent, generate_concepts, intake_brief,
    reason_v2, revise_brief, revise_concept,
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
    auto = ActorDecision(selection.selection_id, "AUTO_PINNED", ActorRef("SYSTEM", "cli:pin"),
                         "event:pin-1")
    ai = ActorDecision(selection.selection_id, "AI_RECOMMENDED", ActorRef("AI", "test:reasoner"),
                       "fake:recommendation-response")
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
    _, sidecars, _, _, selected = saved
    if kind == "intent":
        record = capture_intent(TEXT)
        identifier = sidecars.save_intent(record)
        target = sidecars._path("intents", identifier)
        load, save = sidecars.load_intent, sidecars.save_intent
    elif kind == "decision":
        record = ActorDecision(selected.selection_id, "AUTO_PINNED", ActorRef("SYSTEM", "cli:p"), "event:p")
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
