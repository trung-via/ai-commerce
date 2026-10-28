"""A03 product behavior, provider boundary, provenance and immutable versions."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from ai_commerce.contracts.schema import ContractValidationError, validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import (
    ConceptChange, ConceptDraft, ConceptRecord, ConceptSelection, CreativeStore,
    CreativeValidationError, MockCreativeReasoner, NoReasonerConfigured,
    ProviderObservation, VersionConflict, brief_digest, generate_concepts, intake_brief,
    reframe_concept, revise_brief, revise_concept,
)
from ai_commerce.providers.base import ProviderCapabilities, ProviderError

REQUEST = "Làm một ý tưởng về cơn mưa Sài Gòn, vừa hài vừa mơ, không sản phẩm hay CTA. 🌧️"


@pytest.fixture
def brief():
    return intake_brief(REQUEST)


@pytest.fixture
def concept(brief):
    return generate_concepts(brief, MockCreativeReasoner())[0]


class OfflineReasoner:
    """Independent adapter fixture: no inheritance from the supplied mock."""

    capabilities = ProviderCapabilities("offline-fixture", ("concept",), False, False)
    observation = ProviderObservation()

    def generate(self, brief, *, count=1):
        return tuple(ConceptDraft(
            family=f"family-{index}", title=f"Title {index}",
            premise=f"Premise {index} for {brief['objective']}",
            visual_direction=f"Visual grammar {index}",
        ) for index in range(count))


@pytest.mark.parametrize("text", [REQUEST, "夜の街を音だけで描く", "حكاية عن المطر", "Cảm giác 🦋 e\u0301"])
def test_unicode_freeform_intake_needs_no_commerce_or_storyboard(text):
    record = intake_brief(text)
    assert validate_record("ContentBrief", record) is record
    assert record["objective"] == record["prompt"] == text
    assert record["product_ref"] is None
    assert not {"sku", "storyboard", "cta", "concept_count", "kpi"} & record.keys()
    assert intake_brief(text) == record


def test_preexisting_a02_json_and_custom_fields_are_preserved_and_detached():
    record = {
        "brief_id": "custom", "objective": "Thử một ý tưởng", "product_ref": None,
        "source_refs": ["local:note-1"], "audience": None, "cta": None,
        "aesthetic": {"color": ["đỏ", "xanh"]},
    }
    result = intake_brief(record)
    encoded = json.dumps(record, ensure_ascii=False)
    assert intake_brief(encoded) == result == record
    result["aesthetic"]["color"].append("tím")
    assert record["aesthetic"]["color"] == ["đỏ", "xanh"]
    assert intake_brief(record, brief_id="custom") == record
    assert intake_brief(REQUEST, brief_id="human-brief")["brief_id"] == "human-brief"
    with pytest.raises(CreativeValidationError, match="conflicts"):
        intake_brief(record, brief_id="other")


def test_a02_fixture_can_be_consumed_unchanged():
    fixture = Path(__file__).parent / "fixtures" / "creative_only_brief.json"
    record = intake_brief(fixture.read_text(encoding="utf-8"))
    concept = generate_concepts(record, MockCreativeReasoner())[0]
    assert concept.brief_id == record["brief_id"]
    assert record["product_ref"] is None


@pytest.mark.parametrize("bad_input", [
    "", "   ", None, 7, [], {"objective": "missing ID"},
    {"brief_id": "b", "objective": 8}, {"brief_id": " ", "objective": "x"},
    {"brief_id": "b", "objective": " "},
    {"brief_id": "b", "objective": "x", "product_ref": ""},
    {"brief_id": "b", "objective": "x", "source_refs": [" "]},
    '{"brief_id":"b",', '["a brief"]',
    '{"brief_id":"b","objective":"x","objective":"hidden duplicate"}',
    '{"brief_id":"b","objective":"x","score":NaN}',
    {"brief_id": "b", "objective": "x", "custom": float("inf")},
    {"brief_id": "b", "objective": "x", "custom": ("not", "JSON")},
    {"brief_id": "b", "objective": "x", "custom": {1: "non-string-key"}},
    "\ud800",
])
def test_malformed_intake_is_rejected(bad_input):
    with pytest.raises((CreativeValidationError, ContractValidationError)):
        intake_brief(bad_input)


def test_digest_uses_a02_canonical_bytes_and_all_source_fields(brief):
    assert brief_digest(brief) == sha256_bytes(canonical_bytes(brief))
    assert brief_digest(dict(reversed(list(brief.items())))) == brief_digest(brief)
    assert brief_digest({**brief, "extra": "unknown assertion"}) != brief_digest(brief)


def test_brief_edits_create_new_content_versions_without_mutation(brief, tmp_path):
    store = CreativeStore(tmp_path)
    digest1 = store.save_brief(brief)
    revised = revise_brief(brief, {"audience": "Người thích mưa", "product_ref": None})
    digest2 = store.save_brief(revised)
    assert digest1 != digest2
    assert revised["brief_id"] == brief["brief_id"]
    assert "audience" not in brief
    assert store.load_brief(digest1) == brief
    assert store.load_brief(digest2) == revised
    with pytest.raises(CreativeValidationError):
        revise_brief(brief, {"brief_id": "relabel"})
    with pytest.raises(CreativeValidationError):
        revise_brief(brief, {})
    with pytest.raises(CreativeValidationError):
        revise_brief(brief, {"objective": brief["objective"]})


def test_default_is_one_and_mock_is_explicit_test_only(brief):
    one = generate_concepts(brief, MockCreativeReasoner())
    assert len(one) == 1
    concept = one[0]
    assert concept.revision == 1 and concept.parent is None
    assert concept.operation == "GENERATE" and concept.lens is None and not concept.changes
    assert concept.observation.provider == "deterministic-test-only"
    assert concept.observation.model == "UNKNOWN"
    assert concept.observation.mode == "TEST_ONLY_MOCK"
    assert concept.factual_status == "UNVERIFIED" and concept.rights_status == "UNKNOWN"
    assert REQUEST in concept.premise
    assert one == generate_concepts(brief, MockCreativeReasoner())
    assert one == generate_concepts(REQUEST, MockCreativeReasoner())


@pytest.mark.parametrize("count", [2, 3, 4])
def test_multiple_mock_families_have_different_mechanisms_and_visuals(brief, count):
    concepts = generate_concepts(brief, MockCreativeReasoner(), count=count)
    assert len(concepts) == count
    assert len({c.concept_id for c in concepts}) == count
    assert len({c.family for c in concepts}) == count
    assert len({c.premise for c in concepts}) == count
    assert len({c.visual_direction for c in concepts}) == count
    expected = ["sensory_poem", "object_perspective_comedy", "reverse_time_mystery", "impossible_field_guide"]
    assert [c.family for c in concepts] == expected[:count]
    assert concepts[0] == generate_concepts(brief, MockCreativeReasoner())[0]


def test_no_model_configured_does_not_silently_choose_mock(brief):
    with pytest.raises(NoReasonerConfigured, match="NO_MODEL_CONFIGURED"):
        generate_concepts(brief)


def test_provider_neutral_adapter_unknown_identity_and_no_global_count_quota(brief):
    concepts = generate_concepts(brief, OfflineReasoner(), count=7)
    assert len(concepts) == 7
    assert all(c.observation == ProviderObservation() for c in concepts)
    with pytest.raises(ProviderError, match="four idea families") as exc:
        generate_concepts(brief, MockCreativeReasoner(), count=5)
    assert exc.value.error_code == "MOCK_CAPACITY"


@pytest.mark.parametrize("count", [0, -1, True, 1.5, "2", None])
def test_invalid_counts_do_not_call_provider(brief, count):
    class Uncalled(OfflineReasoner):
        def generate(self, brief, *, count=1):
            pytest.fail("invalid request reached provider")
    with pytest.raises(CreativeValidationError, match="positive integer"):
        generate_concepts(brief, Uncalled(), count=count)


@pytest.mark.parametrize("network,spend", [(True, False), (False, True), (True, True), (0, False)])
def test_network_or_paid_providers_rejected_before_execution(brief, network, spend):
    class Disallowed(OfflineReasoner):
        capabilities = ProviderCapabilities("disallowed", ("concept",), network, spend)
        def generate(self, brief, *, count=1):
            pytest.fail("disallowed provider was called")
    with pytest.raises(ProviderError, match="forbids"):
        generate_concepts(brief, Disallowed())


@pytest.mark.parametrize("result", [None, "text", {}, [], [None], [{"title": "untyped"}], [object()]])
def test_malformed_provider_results_fail_explicitly(brief, result):
    class Broken(OfflineReasoner):
        def generate(self, brief, *, count=1):
            return result
    with pytest.raises(ProviderError) as exc:
        generate_concepts(brief, Broken())
    assert exc.value.error_code == "INVALID_CREATIVE_RESULT"


def test_provider_exception_is_an_error_and_not_a_mock_success(brief):
    class Broken(OfflineReasoner):
        def generate(self, brief, *, count=1):
            raise RuntimeError("synthetic failure")
    with pytest.raises(ProviderError, match="adapter failed") as exc:
        generate_concepts(brief, Broken())
    assert exc.value.error_code == "CREATIVE_PROVIDER_FAILED"
    assert isinstance(exc.value.__cause__, RuntimeError)


@pytest.mark.parametrize("value", [None, "MODEL", {"provider": "invented"}])
def test_malformed_provider_metadata_is_rejected(brief, value):
    class Broken(OfflineReasoner):
        observation = value
    with pytest.raises(ProviderError) as exc:
        generate_concepts(brief, Broken())
    assert exc.value.error_code == "INVALID_CREATIVE_RESULT"


@pytest.mark.parametrize("same_family", [True, False])
def test_provider_cannot_rename_duplicate_ideas_as_multiple_families(brief, same_family):
    class Duplicate(OfflineReasoner):
        def generate(self, brief, *, count=1):
            first = ConceptDraft("poem", "First", "Same mechanism", "Same visuals")
            second = replace(first, title="Second", family=" POEM " if same_family else "renamed")
            return [first, second]
    with pytest.raises(ProviderError, match="duplicate"):
        generate_concepts(brief, Duplicate(), count=2)


def test_source_refs_cannot_be_invented_by_provider(brief):
    class Invented(OfflineReasoner):
        def generate(self, brief, *, count=1):
            return [ConceptDraft("f", "t", "p", "v", ("fake:verified-source",))]
    with pytest.raises(ProviderError, match="source_refs"):
        generate_concepts(brief, Invented())


def test_source_refs_are_preserved_as_references_not_verified_facts(brief):
    source = {**brief, "source_refs": ["note:seller-assertion"], "claim": "seller says best in world"}
    concept = generate_concepts(source, MockCreativeReasoner())[0]
    assert concept.source_refs == ("note:seller-assertion",)
    assert concept.factual_status == "UNVERIFIED" and concept.rights_status == "UNKNOWN"
    assert "unverified" in " ".join(concept.assumptions).lower()
    with pytest.raises(CreativeValidationError):
        ConceptDraft.from_dict({**ConceptDraft("f", "t", "p", "v").to_dict(), "factual_status": "VERIFIED"})


def test_provider_cannot_mutate_original_brief_or_rebind_digest(brief):
    class Mutates(OfflineReasoner):
        def generate(self, detached, *, count=1):
            detached["brief_id"] = "tampered"
            detached["source_refs"].append("fake")
            return [ConceptDraft("f", "t", "p", "v")]
    before = canonical_bytes(brief)
    concept = generate_concepts(brief, Mutates())[0]
    assert canonical_bytes(brief) == before
    assert concept.brief_id == brief["brief_id"]
    assert concept.source_brief_digest == brief_digest(brief)


@pytest.mark.parametrize("field,value", [
    ("revision", True), ("revision", 0), ("title", " "),
    ("source_brief_digest", "not-sha"), ("concept_id", "../../escape"),
    ("source_refs", "not-an-array"), ("observation", None),
    ("factual_status", "VERIFIED"), ("rights_status", "GRANTED"),
    ("schema_version", "future-unknown"), ("changes", {}),
])
def test_malformed_concept_records_cannot_roundtrip(concept, field, value):
    data = concept.to_dict()
    data[field] = value
    with pytest.raises(CreativeValidationError):
        ConceptRecord.from_dict(data)


def test_record_json_roundtrip_is_lossless_and_strict(concept):
    assert ConceptRecord.from_json(concept.to_json()) == concept
    assert REQUEST in concept.to_json()
    data = concept.to_dict()
    data["unrecognized"] = True
    with pytest.raises(CreativeValidationError):
        ConceptRecord.from_dict(data)
    with pytest.raises(CreativeValidationError):
        ConceptRecord.from_json(concept.to_json().replace('"revision":1', '"revision":1,"revision":2'))


def test_revision_preserves_identity_and_exact_parent(concept):
    original = concept.to_json()
    revised = revise_concept(concept, {"title": "Một tên khác", "premise": "Mưa có thể nói chuyện trong thế giới hư cấu."})
    assert concept.to_json() == original
    assert revised.concept_id == concept.concept_id and revised.revision == 2
    assert revised.parent.concept_id == concept.concept_id
    assert revised.parent.revision == 1 and revised.parent.digest == concept.digest
    assert revised.observation == concept.observation
    assert revised.source_brief_digest == concept.source_brief_digest
    assert revised.operation == "REVISE" and revised.lens is None
    assert {c.field for c in revised.changes} == {"title", "premise"}
    assert ConceptRecord.from_json(revised.to_json()) == revised


def test_reframe_is_optional_separate_operation_and_retains_original(concept):
    original = concept.to_dict()
    reframed = reframe_concept(
        concept, lens="Điểm nhìn của một chiếc ô",
        changes={"family": "umbrella_observer", "premise": "Chiếc ô sợ trời tạnh, vì nó muốn tiếp tục chuyến đi."},
    )
    assert concept.to_dict() == original
    assert reframed.operation == "REFRAME"
    assert reframed.lens == "Điểm nhìn của một chiếc ô"
    assert reframed.parent.revision == 1 and reframed.parent.digest == concept.digest
    assert reframed.changes[1].before == concept.premise
    assert reframed.observation.mode == "TEST_ONLY_MOCK"
    assert reframed.concept_id == concept.concept_id
    assert ConceptRecord.from_json(reframed.to_json()) == reframed


@pytest.mark.parametrize("changes", [
    {}, {"provider": "real-model"}, {"factual_status": "VERIFIED"},
    {"source_refs": ["new"]}, {"brief_id": "replacement"}, {"title": ""}, {"premise": None},
])
def test_bad_edits_cannot_change_provenance_or_promote_facts(concept, changes):
    with pytest.raises(CreativeValidationError):
        revise_concept(concept, changes)


def test_reframe_requires_lens_and_actual_change(concept):
    with pytest.raises(CreativeValidationError):
        reframe_concept(concept, lens=" ", changes={"title": "new"})
    with pytest.raises(CreativeValidationError):
        reframe_concept(concept, lens="Other view", changes={"title": concept.title})
    with pytest.raises(CreativeValidationError):
        ProviderObservation("deterministic-test-only", "invented", "MODEL")


def test_records_and_nested_lineage_are_immutable(concept):
    revised = revise_concept(concept, {"title": "New"})
    for obj, field, value in [
        (concept, "title", "mutated"), (concept.observation, "mode", "MODEL"),
        (revised.parent, "revision", 99), (revised.changes[0], "after", "mutated"),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)
    copy = revised.to_dict()
    copy["observation"]["mode"] = "MODEL"
    copy["changes"][0]["after"] = "mutated"
    assert revised.observation.mode == "TEST_ONLY_MOCK"
    assert revised.changes[0].after == "New"
    refs = ["ref:one"]
    draft = ConceptDraft("f", "t", "p", "v", refs)
    refs.append("ref:two")
    assert draft.source_refs == ("ref:one",)


def test_save_reload_select_and_a05_consumer_need_no_chat(brief, concept, tmp_path):
    store = CreativeStore(tmp_path)
    assert store.save_brief(brief) == concept.source_brief_digest
    store.save_concept(concept)
    reframed = reframe_concept(concept, lens="Một đồ vật", changes={"premise": "Tự sự hư cấu của một chiếc ô."})
    store.save_concept(reframed)
    selection = store.select(reframed.concept_id, reframed.revision)
    assert ConceptSelection.from_dict(json.loads(json.dumps(selection.to_dict()))) == selection
    reopened = CreativeStore(tmp_path)
    assert reopened.load_concept(concept.concept_id, 1) == concept
    assert reopened.load_concept(concept.concept_id, 2) == reframed
    assert reopened.load_selection(selection.selection_id) == selection
    payload = reopened.load_selected(selection.selection_id)
    assert payload["brief"] == brief
    assert ConceptRecord.from_dict(payload["concept"]) == reframed
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload
    later = revise_concept(reframed, {"title": "Later"})
    reopened.save_concept(later)
    newer = reopened.select(later.concept_id, 3)
    assert newer.selection_id != selection.selection_id
    assert reopened.load_selected(selection.selection_id)["concept"]["revision"] == 2
    payload["brief"]["objective"] = "mutated"
    payload["concept"]["observation"]["mode"] = "MODEL"
    assert reopened.load_selected(selection.selection_id)["brief"] == brief
    assert reopened.load_selected(selection.selection_id)["concept"]["observation"]["mode"] == "TEST_ONLY_MOCK"


def test_store_same_content_is_idempotent_and_old_revisions_unchanged(brief, concept, tmp_path):
    store = CreativeStore(tmp_path)
    store.save_brief(brief)
    store.save_concept(concept)
    selected = store.select(concept.concept_id, 1)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*.json")}
    store.save_brief(brief)
    store.save_concept(concept)
    assert store.select(concept.concept_id, 1) == selected
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert after == before


def test_conflicting_revisions_never_silently_overwrite(brief, concept, tmp_path):
    store = CreativeStore(tmp_path)
    store.save_brief(brief)
    store.save_concept(concept)
    first = revise_concept(concept, {"title": "First"})
    stale = revise_concept(concept, {"title": "Conflicting"})
    store.save_concept(first)
    with pytest.raises(VersionConflict, match="VERSION_CONFLICT"):
        store.save_concept(stale)
    assert store.load_concept(concept.concept_id, 2) == first
    assert store.load_concept(concept.concept_id, 1) == concept


def test_unsaved_brief_or_parent_cannot_form_dangling_lineage(brief, concept, tmp_path):
    store = CreativeStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.save_concept(concept)
    store.save_brief(brief)
    with pytest.raises(FileNotFoundError):
        store.save_concept(revise_concept(concept, {"title": "child"}))
    assert not list((tmp_path / "concepts").glob("**/*.json"))


def test_revision_cannot_rebind_brief_or_mock_provenance(brief, concept, tmp_path):
    store = CreativeStore(tmp_path)
    store.save_brief(brief)
    store.save_concept(concept)
    second = revise_concept(concept, {"title": "new"})
    other_brief = revise_brief(brief, {"objective": "Other"})
    other_digest = store.save_brief(other_brief)
    bad_records = [
        replace(second, source_brief_digest=other_digest),
        replace(second, observation=ProviderObservation("some-model", "unknown-model", "MODEL")),
        replace(second, changes=(ConceptChange("title", "wrong parent title", "new"),)),
    ]
    for bad in bad_records:
        with pytest.raises(CreativeValidationError):
            store.save_concept(bad)


def test_stored_brief_tamper_is_detected_and_save_refuses_overwrite(brief, tmp_path):
    store = CreativeStore(tmp_path)
    digest = store.save_brief(brief)
    path = tmp_path / "briefs" / f"{digest}.json"
    path.write_text(json.dumps({**brief, "objective": "tampered"}), encoding="utf-8")
    with pytest.raises(CreativeValidationError, match="digest"):
        store.load_brief(digest)
    with pytest.raises(VersionConflict):
        store.save_brief(brief)


def test_selection_tamper_and_initial_concept_tamper_are_detected(brief, concept, tmp_path):
    store = CreativeStore(tmp_path)
    store.save_brief(brief)
    store.save_concept(concept)
    selection = store.select(concept.concept_id, 1)
    path = tmp_path / "selections" / f"{selection.selection_id[10:]}.json"
    path.write_text(json.dumps({**selection.to_dict(), "concept_digest": "0" * 64}), encoding="utf-8")
    with pytest.raises(CreativeValidationError, match="digest"):
        store.load_selection(selection.selection_id)
    concept_path = tmp_path / "concepts" / concept.concept_id[8:] / "r1.json"
    data = concept.to_dict()
    data["premise"] = "A changed initial concept"
    concept_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CreativeValidationError, match="initial content"):
        store.load_concept(concept.concept_id, 1)


@pytest.mark.parametrize("unsafe_id", ["../../outside", "concept:../escape", "concept:" + "G" * 64])
def test_store_rejects_invalid_identity_paths(tmp_path, unsafe_id):
    with pytest.raises(CreativeValidationError):
        CreativeStore(tmp_path).load_concept(unsafe_id, 1)

@pytest.mark.parametrize("caps", [
    None,
    ProviderCapabilities("bad-output", "concept", False, False),
    ProviderCapabilities("bad-output", (None,), False, False),
    ProviderCapabilities(" ", ("concept",), False, False),
    ProviderCapabilities("unsupported", ("text",), False, False),
])
def test_malformed_capabilities_fail_before_provider_call(brief, caps):
    class Invalid(OfflineReasoner):
        capabilities = caps
        def generate(self, brief, *, count=1):
            pytest.fail("invalid metadata reached provider")
    with pytest.raises(ProviderError) as exc:
        generate_concepts(brief, Invalid())
    assert exc.value.error_code == "INVALID_CREATIVE_RESULT"


def test_mock_subclass_cannot_silently_claim_live_model_identity(brief):
    class Relabeled(MockCreativeReasoner):
        observation = ProviderObservation("claimed-provider", "claimed-model", "MODEL")
    with pytest.raises(ProviderError, match="TEST_ONLY_MOCK"):
        generate_concepts(brief, Relabeled())


def test_same_brief_id_with_new_content_has_new_source_and_concept_identity(brief, concept):
    edited = revise_brief(brief, {"objective": "Một chủ đề hoàn toàn khác"})
    new_concept = generate_concepts(edited, MockCreativeReasoner())[0]
    assert edited["brief_id"] == brief["brief_id"]
    assert new_concept.source_brief_digest != concept.source_brief_digest
    assert new_concept.concept_id != concept.concept_id


def test_concurrent_revision_writes_cannot_overwrite_each_other(brief, concept, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store = CreativeStore(tmp_path)
    store.save_brief(brief)
    store.save_concept(concept)
    revisions = [
        revise_concept(concept, {"title": "Writer A"}),
        revise_concept(concept, {"title": "Writer B"}),
    ]
    barrier = Barrier(2)
    def save(record):
        barrier.wait(timeout=10)
        try:
            store.save_concept(record)
            return "saved", record
        except VersionConflict:
            return "conflict", record
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, revisions))
    assert sorted(status for status, record in outcomes) == ["conflict", "saved"]
    winner = next(record for status, record in outcomes if status == "saved")
    assert store.load_concept(concept.concept_id, 2) == winner
    assert store.load_concept(concept.concept_id, 1) == concept
