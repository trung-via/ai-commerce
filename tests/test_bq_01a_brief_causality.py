"""BQ-01a deterministic synthetic contracts; no provider/media/behavior proof.

Real A03 intake/digest/intent/reasoner/sidecars are exercised separately from
the effect-denied new pure interfaces. Existing A03 tests remain unchanged.
"""

import ast
import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

import pytest

from ai_commerce.creative.briefs import brief_digest, intake_brief
from ai_commerce.creative.intent import capture_intent
from ai_commerce.creative.reasoner import MockCreativeReasoner, generate_concepts
from ai_commerce.creative.sidecars import CreativeSidecarStore
from ai_commerce.creative_quality import (
    A03_INTAKE_REF, BehavioralBriefDraft, CapabilityPreviewDraft, CausalShotPlanDraft,
    KNOWLEDGE_MAP, ModelCapabilityProfileDraft, QualityDraftError, REALISM_AXES,
    build_behavioral_brief, build_causal_plan, compile_capability_mock,
)
from ai_commerce.creative_quality.capability import CAPABILITY_KEYS


ROOT = Path(__file__).resolve().parents[1]
BASE = "f640a1dac925269596ac652322d72edc95738e5a"
TEXT = "  Mưa Sài Gòn 🌧️ e\u0301 — chiếc cốc biết bay, hoạt hình hư cấu, không CTA.\n"
MODELS = (BehavioralBriefDraft, CausalShotPlanDraft, ModelCapabilityProfileDraft, CapabilityPreviewDraft)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def hypotheses():
    return [{"id": "h1", "statement": "Giả thuyết: khoảng lặng có thể gợi tò mò.",
             "assumptions": ["Chưa có dữ liệu quan sát về khán giả."],
             "counterexamples": ["Một số người có thể muốn lời kể rõ hơn."],
             "source_refs": [A03_INTAKE_REF], "truth": "HYPOTHESIS"}]


@pytest.fixture(scope="module")
def native():
    brief = intake_brief(TEXT)
    return brief, brief_digest(brief)


def behavioral(native, **kwargs):
    return build_behavioral_brief(native[0], source_brief_digest=native[1], hypotheses=hypotheses(), **kwargs)


def audio(mode="SILENT", **kwargs):
    value = {"mode": mode, "language": "UNKNOWN" if mode in ("SILENT", "UNKNOWN") else "vi",
             "text": None if mode in ("SILENT", "UNKNOWN") else "Mưa qua rồi, mình cùng lắng nghe nhé.",
             "accent_intent": None, "delivery_intent": None, "acoustic_intent": None,
             "lip_sync": "UNKNOWN" if mode == "UNKNOWN" else "NOT_REQUIRED",
             "source_refs": [] if mode in ("SILENT", "UNKNOWN") else [A03_INTAKE_REF]}
    value.update(kwargs)
    return value


def shot(identifier="s1", order=0, start=0, end=1000):
    return {"id": identifier, "order": order, "start_ms": start, "end_ms": end,
            "description": "Chiếc cốc bay theo một giọt mưa — hành động hư cấu do caller viết.",
            "objects": [{"object_id": "cup", "identity": "cốc xanh", "position_in": "trái",
                         "position_out": "trái", "motion_in": "đứng yên", "motion_out": "đứng yên"}],
            "constraints": []}


def plan(native, **kwargs):
    brief = behavioral(native)
    values = {"shots": [shot(), shot("s2", 1, 1000, 2000)],
              "edges": [{"cause": "s1", "effect": "s2", "continuity_objects": ["cup"]}],
              "visual_form": "ANIMATION", "physics_intent": "INTENTIONAL_FANTASY", "audio": audio()}
    values.update(kwargs)
    return build_causal_plan(brief, behavioral_brief_digest=brief.digest, **values)


def profile(changes=None):
    caps = dict.fromkeys(CAPABILITY_KEYS, "SUPPORTED")
    for key, status in (changes or {}).items():
        if status is None:
            del caps[key]
        else:
            caps[key] = status
    content = "Synthetic metadata supplied by test author; no model benchmark or provider endorsement."
    return ModelCapabilityProfileDraft.from_dict({
        "schema_version": "ModelCapabilityProfileDraft.v1", "execution_state": "SIMULATED_ONLY",
        "profile_id": "fixture:capabilities", "provenance": "TEST_ONLY",
        "metadata_source": {"ref": "fixture:metadata", "content": content, "sha256": sha(content.encode())},
        "capabilities": caps,
    })


def preview(plan_value, profile_value=None):
    return compile_capability_mock(plan_value, profile_value, plan_digest=plan_value.digest,
                                   profile_digest=None if profile_value is None else profile_value.digest)


def record(kind, native):
    if kind is BehavioralBriefDraft:
        return behavioral(native)
    if kind is CausalShotPlanDraft:
        return plan(native)
    if kind is ModelCapabilityProfileDraft:
        return profile()
    return preview(plan(native), profile())


def replace_at(value, path, replacement):
    result = deepcopy(value)
    parent = result
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = replacement
    return result


@pytest.mark.parametrize("text", [TEXT, "Không sản phẩm, không giọng nói.", "Một meme vô lý 🐈", "A\u0300 khác À",
                                  "Tôi thích màu xanh.", "  giữ\tnguyên\n", "أمطار", "雨の物語", "Âm / sắc / hỏi / ngã / nặng",
                                  "Fiction: mặt trăng rơi vào tách trà.", "Không cần 24 concepts.", "🙂" * 40])
def test_real_a03_freeform_unicode_and_digest(text):
    source = intake_brief(text)
    before = deepcopy(source)
    digest = brief_digest(source)
    draft = build_behavioral_brief(source, source_brief_digest=digest, hypotheses=hypotheses(), provenance="HUMAN_SUPPLIED")
    value = draft.to_dict()
    assert source == before and brief_digest(source) == digest
    assert value["source_brief"] == source and value["source_brief_digest"] == digest
    assert value["raw_intent"] == text and value["product_ref"] is None
    assert value["sources"][0]["evidence_type"] == "USER_INTENT"
    assert value["sources"][0]["truth"] == "UNPROVEN"
    assert value["hypotheses"][0]["truth"] == "HYPOTHESIS"
    assert text.encode("utf-8") in draft.to_bytes() or json.loads(draft.to_json())["raw_intent"] == text


@pytest.mark.parametrize("provenance", ["HUMAN_SUPPLIED", "SYNTHETIC_ONLY", "UNKNOWN"])
def test_mapping_with_optional_product_and_source_snapshot(native, provenance):
    source = intake_brief({"brief_id": "brief:custom", "objective": "Nội dung tự do", "product_ref": None,
                           "source_refs": ["research:caller"], "caller_extra": {"desired_count": 2}})
    content = "Caller supplies this reference; authenticity and conclusions are unproven."
    supplied = {"ref": "research:caller", "content": content, "sha256": sha(content.encode()),
                "evidence_type": "RESEARCH_REFERENCE", "provenance": provenance, "truth": "UNPROVEN"}
    hs = hypotheses()
    hs[0]["source_refs"].append("research:caller")
    draft = build_behavioral_brief(source, source_brief_digest=brief_digest(source), hypotheses=hs, sources=[supplied], provenance=provenance)
    assert draft.to_dict()["source_brief"]["caller_extra"] == {"desired_count": 2}
    assert draft.to_dict()["sources"][1]["truth"] == "UNPROVEN"
    assert "cta" not in draft.to_dict() and "storyboard" not in draft.to_dict()


def test_json_intake_formatting_is_preserved():
    text = '{ "brief_id": "bq:json", "objective": "Mưa 🌧️", "product_ref": null }\n'
    source = intake_brief(text)
    draft = build_behavioral_brief(source, source_brief_digest=brief_digest(source), raw_intent=text, hypotheses=hypotheses())
    assert draft.to_dict()["raw_intent"] == text
    with pytest.raises(QualityDraftError):
        build_behavioral_brief(source, source_brief_digest=brief_digest(source), raw_intent='{"objective":"other"}', hypotheses=hypotheses())


@pytest.mark.parametrize("product", [None, "product:caller-supplied"])
def test_product_ref_does_not_create_facts_or_cta(product):
    source = intake_brief({"brief_id": "bq:product", "objective": "Không tự tạo product claim", "product_ref": product})
    draft = build_behavioral_brief(source, source_brief_digest=brief_digest(source), hypotheses=hypotheses())
    assert draft.to_dict()["product_ref"] == product and draft.to_dict()["claims"] == []


BRIEF_BAD = [
    (("execution_state",), "LIVE"), (("schema_version",), "BehavioralBriefDraft.v2"),
    (("provenance",), "PROVIDER_OBSERVED"), (("product_ref",), "fixed:SKU"),
    (("raw_intent",), "changed source"), (("source_brief_digest",), "0" * 64),
    (("source_brief", "objective"), "tampered"), (("hypotheses",), []),
    (("hypotheses", 0, "truth"), "OBSERVED"), (("hypotheses", 0, "truth"), "VERIFIED_RESEARCH"),
    (("hypotheses", 0, "assumptions"), []), (("hypotheses", 0, "counterexamples"), []),
    (("hypotheses", 0, "source_refs"), []), (("hypotheses", 0, "source_refs"), ["unknown:research"]),
    (("hypotheses", 0, "id"), "../../x"), (("hypotheses", 0, "statement"), " "),
    (("sources", 0, "evidence_type"), "RESEARCH_REFERENCE"), (("sources", 0, "evidence_type"), "OBSERVED_BEHAVIOR"),
    (("sources", 0, "truth"), "VERIFIED"), (("sources", 0, "provenance"), "PROVIDER_OBSERVED"),
    (("sources", 0, "provenance"), "HUMAN_SUPPLIED"), (("sources", 0, "ref"), "spoof:source"),
    (("sources", 0, "sha256"), "0" * 64), (("sources",), []),
]


@pytest.mark.parametrize("path,value", BRIEF_BAD)
def test_hypothesis_source_spoof_and_hash_tamper_rejected(native, path, value):
    with pytest.raises(QualityDraftError):
        BehavioralBriefDraft.from_dict(replace_at(behavioral(native).to_dict(), path, value))


@pytest.mark.parametrize("key", ["observed_behavior", "roi", "research_metrics", "copyright_granted", "provider", "concept_count", "landmark_emotion"])
def test_no_unreviewed_brief_fields(native, key):
    value = behavioral(native).to_dict()
    value[key] = "caller attempts to add authority"
    with pytest.raises(QualityDraftError):
        BehavioralBriefDraft.from_dict(value)


@pytest.mark.parametrize("kind", ["BEHAVIORAL_METRIC", "PRODUCT_FACT", "COPYRIGHT_RIGHTS", "TESTIMONIAL", "RESEARCH_CONCLUSION"])
@pytest.mark.parametrize("truth", ["UNPROVEN", "VERIFIED", "GRANTED", "OBSERVED"])
def test_false_metrics_factual_claims_rights_stay_unproven(native, kind, truth):
    claim = {"kind": kind, "text": "Synthetic assertion: 99% lift, all rights granted, endorsed testimonial.",
             "source_refs": [A03_INTAKE_REF], "truth": truth}
    if truth != "UNPROVEN":
        with pytest.raises(QualityDraftError):
            behavioral(native, claims=[claim])
    else:
        draft = behavioral(native, claims=[claim])
        assert draft.to_dict()["claims"] == [claim]
        assert preview(build_causal_plan(draft, behavioral_brief_digest=draft.digest,
                       shots=[shot()], edges=[], visual_form="FICTION", physics_intent="INTENTIONAL_FANTASY", audio=audio()), profile()).to_dict()["rights"] == "UNPROVEN"


@pytest.mark.parametrize("refs", [[], ["spoof:claims"]])
def test_claim_requires_bound_source(native, refs):
    with pytest.raises(QualityDraftError):
        behavioral(native, claims=[{"kind": "PRODUCT_FACT", "text": "fake", "source_refs": refs, "truth": "UNPROVEN"}])


def test_explicit_hypotheses_and_no_duplicate_semantics(native):
    with pytest.raises(TypeError):
        build_behavioral_brief(native[0], source_brief_digest=native[1])
    value = behavioral(native).to_dict()
    for key in ("hypotheses", "sources"):
        bad = deepcopy(value)
        bad[key].append(deepcopy(bad[key][0]))
        with pytest.raises(QualityDraftError):
            BehavioralBriefDraft.from_dict(bad)


@pytest.mark.parametrize("form", ["REALISTIC", "STYLIZED", "FICTION", "ANIMATION"])
@pytest.mark.parametrize("physics", ["REAL_WORLD", "INTENTIONAL_FANTASY"])
def test_style_physics_are_explicit_and_fantasy_has_no_realism_veto(native, form, physics):
    p = plan(native, visual_form=form, physics_intent=physics)
    caps = profile({"PHYSICAL_REALISM": "UNSUPPORTED"}) if physics == "INTENTIONAL_FANTASY" else profile()
    result = preview(p, caps).to_dict()
    assert result["status"] == "PLAN_COMPATIBLE_SIMULATED"
    assert result["execution_state"] == "SIMULATED_ONLY"
    assert p.to_dict()["knowledge_map"] == dict(KNOWLEDGE_MAP)
    assert p.to_dict()["realism_axes"] == list(REALISM_AXES)
    if physics == "INTENTIONAL_FANTASY":
        assert "PHYSICAL_REALISM" not in {c["constraint"] for c in result["checks"]}


@pytest.mark.parametrize("mode", ["SILENT", "VOICEOVER", "ON_SCREEN_SPEECH"])
def test_vietnamese_voice_silent_and_optional_lipsync(native, mode):
    p = plan(native, audio=audio(mode))
    result = preview(p, profile({"LIP_SYNC": "UNSUPPORTED"})).to_dict()
    assert result["status"] == "PLAN_COMPATIBLE_SIMULATED"
    assert "LIP_SYNC" not in {item["constraint"] for item in result["checks"]}
    if mode != "SILENT":
        assert "lắng nghe" in p.to_dict()["audio"]["text"]
        assert any(c["constraint"] == "VIETNAMESE_VOICE" for c in result["checks"])


def test_causal_action_motion_and_position_endpoints(native):
    one, two = shot(), shot("s2", 1, 1000, 2000)
    one["objects"][0]["position_out"] = two["objects"][0]["position_in"] = "trên mây"
    one["objects"][0]["motion_out"] = two["objects"][0]["motion_in"] = "bay lên"
    p = plan(native, shots=[one, two])
    assert preview(p, profile()).to_dict()["status"] == "PLAN_COMPATIBLE_SIMULATED"


@pytest.mark.parametrize("middle", ["UNKNOWN", "cốc xanh"])
def test_identity_cannot_change_through_unknown_or_absent_intermediate_shot(native, middle):
    one, two, three = shot(), shot("s2", 1, 1000, 2000), shot("s3", 2, 2000, 3000)
    two["objects"][0]["identity"] = middle
    three["objects"][0]["identity"] = "cốc khác"
    with pytest.raises(QualityDraftError, match="identity"):
        plan(native, shots=[one, two, three], edges=[])
    two["objects"] = []
    with pytest.raises(QualityDraftError, match="identity"):
        plan(native, shots=[one, two, three], edges=[])


@pytest.mark.parametrize("where", ["constraint_intent", "description", "speech_text"])
def test_unknown_text_cannot_become_supported_intent(native, where):
    value = plan(native).to_dict()
    if where == "constraint_intent":
        value["shots"][0]["constraints"] = [{"kind": "CAMERA", "intent": "UNKNOWN", "state": "REQUESTED", "source_refs": [A03_INTAKE_REF]}]
    elif where == "description":
        value["shots"][0]["description"] = "UNKNOWN"
    else:
        value["audio"] = audio("VOICEOVER", text="UNKNOWN")
    p = CausalShotPlanDraft.from_dict(value)
    assert preview(p, profile()).to_dict()["status"] == "HOLD_UNKNOWN"


@pytest.mark.parametrize("language", ["vi", "vi-VN", "vi-vn", "en", "zh-Hant-TW"])
def test_language_tags_preserved(native, language):
    value = plan(native, audio=audio("VOICEOVER", language=language))
    assert value.to_dict()["audio"]["language"] == language
    assert preview(value, profile()).to_dict()["status"] == "PLAN_COMPATIBLE_SIMULATED"


@pytest.mark.parametrize("language", ["unknown", "unspecified language", "vi_XX", "vi-", "x" * 65])
def test_ambiguous_or_unbounded_language_tags_rejected(native, language):
    with pytest.raises(QualityDraftError):
        plan(native, audio=audio("VOICEOVER", language=language))


@pytest.mark.parametrize("attack", ["aggregate_bytes", "value_nodes", "multibyte_text", "escaped_bytes"])
def test_total_json_bounds_before_semantic_validation(native, attack):
    value = behavioral(native).to_dict()
    if attack == "aggregate_bytes":
        value["attack"] = ["x" * 16_384] * 9
    elif attack == "value_nodes":
        value["attack"] = [[0] * 100 for _ in range(50)]
    elif attack == "multibyte_text":
        value["attack"] = "🙂" * 4097
    else:
        value["attack"] = ["\n" * 16_384] * 5
    with pytest.raises(QualityDraftError, match="bound"):
        BehavioralBriefDraft.from_dict(value)


GRAPH_BAD = [
    (("shots",), []), (("shots", 1, "id"), "s1"), (("shots", 1, "order"), 0),
    (("shots", 0, "order"), True), (("shots", 0, "start_ms"), -1),
    (("shots", 0, "start_ms"), 0.5), (("shots", 0, "end_ms"), 0),
    (("shots", 1, "start_ms"), 999), (("shots", 1, "end_ms"), 300_001),
    (("shots", 1, "objects", 0, "identity"), "cốc đỏ"),
    (("shots", 1, "objects", 0, "position_in"), "phải"),
    (("shots", 1, "objects", 0, "motion_in"), "bay lên"),
    (("edges", 0, "cause"), "missing"), (("edges", 0, "effect"), "s1"),
    (("edges", 0, "continuity_objects"), ["missing-object"]),
    (("edges", 0, "continuity_objects"), ["cup", "cup"]),
    (("visual_form",), "REALISM_ONLY"), (("physics_intent",), "ATTESTED"),
    (("knowledge_map", "K6"), "rights granted"), (("realism_axes",), ["R6", "R7"]),
    (("behavioral_brief_digest",), "0" * 64), (("execution_state",), "PASS_LIVE"),
    (("behavioral_brief", "hypotheses", 0, "statement"), "source changed"),
]


@pytest.mark.parametrize("path,value", GRAPH_BAD)
def test_graph_time_causality_identity_continuity_and_pins_rejected(native, path, value):
    with pytest.raises(QualityDraftError):
        CausalShotPlanDraft.from_dict(replace_at(plan(native).to_dict(), path, value))


@pytest.mark.parametrize("variant", ["cycle", "reverse_edge", "duplicate_edge", "duplicate_object", "object_absent", "extra_landmarks", "extra_emotion", "attested_constraint", "unbound_constraint"])
def test_graph_adversarial_rejects(native, variant):
    value = plan(native).to_dict()
    if variant == "cycle":
        value["edges"].append({"cause": "s2", "effect": "s1", "continuity_objects": ["cup"]})
    elif variant == "reverse_edge":
        value["edges"] = [{"cause": "s2", "effect": "s1", "continuity_objects": ["cup"]}]
    elif variant == "duplicate_edge":
        value["edges"] *= 2
    elif variant == "duplicate_object":
        value["shots"][0]["objects"] *= 2
    elif variant == "object_absent":
        value["shots"][1]["objects"] = []
    elif variant.startswith("extra_"):
        value["shots"][0][variant] = "infer happiness from facial landmarks"
    else:
        value["shots"][0]["constraints"] = [{"kind": "CAMERA", "intent": "Close shot", "state": "VERIFIED" if variant == "attested_constraint" else "REQUESTED",
                                             "source_refs": ["spoof"] if variant == "unbound_constraint" else [A03_INTAKE_REF]}]
    with pytest.raises(QualityDraftError):
        CausalShotPlanDraft.from_dict(value)


def requesting_feature(native, feature):
    kwargs = {}
    if feature in ("REALISTIC", "STYLIZED", "FICTION", "ANIMATION"):
        kwargs["visual_form"] = feature
    if feature == "PHYSICAL_REALISM":
        kwargs["physics_intent"] = "REAL_WORLD"
    if feature in ("VOICEOVER", "VIETNAMESE_VOICE", "OTHER_LANGUAGE_VOICE", "ACCENT", "DELIVERY", "ACOUSTICS", "LIP_SYNC", "ON_SCREEN_SPEECH"):
        kwargs["audio"] = audio("ON_SCREEN_SPEECH" if feature in ("LIP_SYNC", "ON_SCREEN_SPEECH") else "VOICEOVER")
        if feature == "OTHER_LANGUAGE_VOICE":
            kwargs["audio"]["language"] = "en"
        if feature == "LIP_SYNC":
            kwargs["audio"]["lip_sync"] = "REQUIRED"
        for key, name in (("ACCENT", "accent_intent"), ("DELIVERY", "delivery_intent"), ("ACOUSTICS", "acoustic_intent")):
            if feature == key:
                kwargs["audio"][name] = "Ý định do caller viết; chưa được đo."
    if feature in ("CAMERA", "LIGHTING", "CAUSAL_ACTION"):
        shots = [shot(), shot("s2", 1, 1000, 2000)]
        shots[0]["constraints"] = [{"kind": feature, "intent": "Ý định hư cấu", "state": "REQUESTED", "source_refs": [A03_INTAKE_REF]}]
        kwargs["shots"] = shots
    return plan(native, **kwargs)


@pytest.mark.parametrize("feature", CAPABILITY_KEYS)
@pytest.mark.parametrize("status", ["SUPPORTED", "UNSUPPORTED", "UNKNOWN", None])
def test_each_requested_capability_supported_unsupported_unknown_and_missing(native, feature, status):
    p = requesting_feature(native, feature)
    result = preview(p, profile({feature: status})).to_dict()
    assert feature in {item["constraint"] for item in result["checks"]}
    expected = "PLAN_COMPATIBLE_SIMULATED" if status == "SUPPORTED" else "HOLD_UNSUPPORTED" if status == "UNSUPPORTED" else "HOLD_UNKNOWN"
    assert result["status"] == expected
    assert result["media_inspection"] == "NOT_INSPECTED" and result["execution_authority"] == "NONE"
    assert result["behavioral_outcomes"] == result["perceptual_quality"] == result["rights"] == "UNPROVEN"


@pytest.mark.parametrize("where", ["profile", "audio", "physics", "identity", "position", "motion", "constraint", "language", "speech", "lip_sync", "acoustics"])
def test_missing_optional_audio_or_unknown_constraint_never_passes(native, where):
    p, caps = plan(native), profile()
    value = p.to_dict()
    if where == "profile":
        caps = None
    elif where == "audio":
        b = behavioral(native)
        p = build_causal_plan(b, behavioral_brief_digest=b.digest, shots=[shot()], edges=[], visual_form="FICTION", physics_intent="INTENTIONAL_FANTASY")
    elif where == "physics":
        value["physics_intent"] = "UNKNOWN"
    elif where in ("identity", "position", "motion"):
        key = {"identity": "identity", "position": "position_in", "motion": "motion_in"}[where]
        value["shots"][0]["objects"][0][key] = "UNKNOWN"
    elif where == "constraint":
        value["shots"][0]["constraints"] = [{"kind": "CAMERA", "intent": "UNKNOWN", "state": "UNKNOWN", "source_refs": [A03_INTAKE_REF]}]
    else:
        value["audio"] = audio("ON_SCREEN_SPEECH")
        key = {"language": "language", "speech": "text", "lip_sync": "lip_sync", "acoustics": "acoustic_intent"}[where]
        value["audio"][key] = None if where == "speech" else "UNKNOWN"
    if where not in ("audio",):
        p = CausalShotPlanDraft.from_dict(value)
    result = preview(p, caps).to_dict()
    assert result["status"] == "HOLD_UNKNOWN" and result["unknown"]


@pytest.mark.parametrize("mode,field,value", [
    ("SILENT", "lip_sync", "REQUIRED"), ("SILENT", "text", "lời nói"),
    ("SILENT", "acoustic_intent", "reverb"), ("VOICEOVER", "lip_sync", "REQUIRED"),
    ("UNKNOWN", "text", "giọng tự đoán"), ("VOICEOVER", "source_refs", ["unbound"]),
])
def test_audio_conflicting_or_unbound_intent_rejected(native, mode, field, value):
    with pytest.raises(QualityDraftError):
        plan(native, audio=audio(mode, **{field: value}))


@pytest.mark.parametrize("kind", MODELS)
def test_closed_canonical_roundtrip_immutable_and_detached(native, kind):
    original = record(kind, native)
    assert kind.from_json(original.to_json()) == original
    assert kind.from_dict(original.to_dict()).to_bytes() == original.to_bytes()
    assert original.digest == sha(original.to_bytes())
    original.verify_digest(original.digest)
    value = original.to_dict()
    value["execution_state"] = "FORGED"
    assert original.to_dict()["execution_state"] == "SIMULATED_ONLY"
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        original._json = "{}"
    with pytest.raises(QualityDraftError):
        original.verify_digest("0" * 64)
    with pytest.raises(QualityDraftError):
        kind.from_dict(value)


@pytest.mark.parametrize("kind", MODELS)
@pytest.mark.parametrize("attack", ["duplicate_root", "duplicate_nested", "extra", "nan", "infinity", "overflow", "depth", "bytes", "string", "list", "custom", "cycle", "surrogate", "integer"])
def test_json_duplicates_nonfinite_depth_size_and_plain_types(native, kind, attack):
    value = record(kind, native).to_dict()
    if attack == "duplicate_root":
        text = json.dumps(value, ensure_ascii=False)
        invalid = '{"schema_version":"duplicate",' + text[1:]
    elif attack == "duplicate_nested":
        invalid = json.dumps(value, ensure_ascii=False)[:-1] + ',"attack":{"key":1,"key":2}}'
    elif attack in ("nan", "infinity", "overflow"):
        constant = {"nan": "NaN", "infinity": "Infinity", "overflow": "1e999"}[attack]
        invalid = json.dumps(value)[:-1] + ',"attack":' + constant + '}'
    elif attack == "bytes":
        invalid = " " * 131_073
    else:
        invalid = None
        if attack == "extra":
            value["provider"] = "spoof"
        elif attack == "depth":
            nested = {}
            for _ in range(14):
                nested = {"next": nested}
            value["attack"] = nested
        elif attack == "string":
            value["attack"] = "x" * 16_385
        elif attack == "list":
            value["attack"] = [0] * 129
        elif attack == "custom":
            value["attack"] = ("not", "JSON")
        elif attack == "cycle":
            value["attack"] = value
        elif attack == "surrogate":
            value["attack"] = "\ud800"
        elif attack == "integer":
            value["attack"] = 2**53
    with pytest.raises(QualityDraftError):
        kind.from_json(invalid) if invalid is not None else kind.from_dict(value)


@pytest.mark.parametrize("change", ["truth", "model", "feature", "status", "source", "hash"])
def test_capability_spoofing_and_source_binding_rejected(change):
    value = profile().to_dict()
    if change == "truth":
        value["provenance"] = "PROVIDER_OBSERVED"
    elif change == "model":
        value["recommended_vendor"] = "invented winner"
    elif change == "feature":
        value["capabilities"]["GUARANTEED_REVENUE"] = "SUPPORTED"
    elif change == "status":
        value["capabilities"]["SHOT_DAG"] = True
    elif change == "source":
        value["metadata_source"]["content"] = "tampered metadata"
    else:
        value["metadata_source"]["sha256"] = "0" * 64
    with pytest.raises(QualityDraftError):
        ModelCapabilityProfileDraft.from_dict(value)


@pytest.mark.parametrize("path,value", [
    (("plan_digest",), "0" * 64), (("profile_digest",), "0" * 64),
    (("status",), "PASS"), (("rights",), "GRANTED"), (("media_inspection",), "INSPECTED"),
    (("perceptual_quality",), "EXCELLENT"), (("execution_authority",), "GO_PUBLISH"),
    (("behavioral_brief_digest",), "0" * 64), (("checks",), []),
])
def test_preview_replay_tamper_or_promotion_rejected(native, path, value):
    with pytest.raises(QualityDraftError):
        CapabilityPreviewDraft.from_dict(replace_at(preview(plan(native), profile()).to_dict(), path, value))


def test_all_builder_inputs_are_nonmutating_and_pinned(native):
    source, digest = deepcopy(native[0]), native[1]
    hs = hypotheses()
    before = deepcopy((source, hs))
    b = build_behavioral_brief(source, source_brief_digest=digest, hypotheses=hs)
    shots, edges, sound = [shot()], [], audio()
    original = deepcopy((shots, edges, sound))
    p = build_causal_plan(b, behavioral_brief_digest=b.digest, shots=shots, edges=edges, audio=sound, visual_form="FICTION", physics_intent="INTENTIONAL_FANTASY")
    caps = profile()
    one, two = preview(p, caps), preview(p, caps)
    assert one == two and before == (source, hs) and original == (shots, edges, sound)
    hs[0]["statement"] = "caller mutated later"
    shots[0]["description"] = "caller mutated later"
    assert "caller mutated later" not in one.to_json()
    with pytest.raises(QualityDraftError):
        build_causal_plan(b, behavioral_brief_digest="0" * 64, shots=[], edges=[], visual_form="FICTION")
    with pytest.raises(QualityDraftError):
        compile_capability_mock(p, caps, plan_digest="0" * 64, profile_digest=caps.digest)
    with pytest.raises(QualityDraftError):
        compile_capability_mock(p, caps, plan_digest=p.digest, profile_digest="0" * 64)


def test_real_a03_intent_reasoner_and_sidecar_consumer_nonmutation(tmp_path, native):
    # This test explicitly exercises existing A03 storage in pytest scratch.
    # The new BQ pipeline below has no store or provider argument.
    sidecars = CreativeSidecarStore(tmp_path / "a03")
    original = deepcopy(native[0])
    digest = sidecars.creative.save_brief(original)
    intent = capture_intent(original)
    sidecars.save_intent(intent)
    concepts = generate_concepts(original, MockCreativeReasoner(), count=1)
    for concept in concepts:
        sidecars.creative.save_concept(concept)
    files_before = {str(p.relative_to(tmp_path)): sha(p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()}
    draft = behavioral(native)
    result = preview(plan(native), profile())
    files_after = {str(p.relative_to(tmp_path)): sha(p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()}
    assert files_before == files_after
    assert original == native[0] and digest == intent.source_brief_digest == draft.to_dict()["source_brief_digest"]
    assert sidecars.load_intent(intent.digest) == intent
    assert sidecars.creative.load_concept(concepts[0].concept_id, 1) == concepts[0]
    assert result.to_dict()["execution_state"] == "SIMULATED_ONLY"


def test_new_pipeline_has_no_network_provider_filesystem_environment_or_process_effects(native, monkeypatch):
    environment = dict(os.environ)
    captured = []
    def denied(*args, **kwargs):
        captured.append("effect")
        raise AssertionError("external effect attempted")
    with monkeypatch.context() as guard:
        for owner, name in ((builtins, "open"), (io, "open"), (Path, "open"), (Path, "read_bytes"),
                            (Path, "read_text"), (Path, "write_bytes"), (Path, "write_text"), (Path, "mkdir"),
                            (socket, "socket"), (socket, "getaddrinfo"), (subprocess, "Popen"),
                            (urllib.request, "urlopen"), (os, "getenv"), (os, "system"),
                            (os, "urandom"), (os._Environ, "__getitem__"), (os._Environ, "__setitem__"),
                            (os._Environ, "__delitem__"), (MockCreativeReasoner, "generate"),
                            (CreativeSidecarStore, "save_intent")):
            guard.setattr(owner, name, denied)
        b = behavioral(native)
        p = plan(native)
        caps = profile()
        result = preview(p, caps)
        assert BehavioralBriefDraft.from_json(b.to_json()) == b
        assert CapabilityPreviewDraft.from_json(result.to_json()) == result
    assert not captured and dict(os.environ) == environment


def test_fresh_import_isolation_and_inert_explicit_exports():
    code = r'''
import builtins, dataclasses, hashlib, io, json, math, os, pathlib, re, socket, subprocess, sys, typing, urllib.request
sys.path.insert(0, sys.argv[1])
def denied(*a, **k):
    raise AssertionError("import effect")
builtins.open = io.open = socket.socket = socket.getaddrinfo = subprocess.Popen = os.getenv = os.system = denied
os._Environ.__getitem__ = os._Environ.__setitem__ = os._Environ.__delitem__ = denied
import ai_commerce.creative_quality as q
assert set(q.__all__) == {"A03_INTAKE_REF", "BehavioralBriefDraft", "QualityDraftError", "build_behavioral_brief", "KNOWLEDGE_MAP", "REALISM_AXES", "CausalShotPlanDraft", "build_causal_plan", "CapabilityPreviewDraft", "ModelCapabilityProfileDraft", "compile_capability_mock"}
assert not any(n.startswith(("ai_commerce.creative.", "ai_commerce.providers", "ai_commerce.production_runtime", "ai_commerce.hostops")) for n in sys.modules)
print("BQ01A_IMPORT_ISOLATED_NO_EFFECTS")
'''
    completed = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "src")], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "BQ01A_IMPORT_ISOLATED_NO_EFFECTS"


@pytest.mark.parametrize("filename", ["__init__.py", "behavioral.py", "causality.py", "capability.py"])
def test_pure_sources_have_no_external_import_or_effect_seam(filename):
    tree = ast.parse((ROOT / "src/ai_commerce/creative_quality" / filename).read_text(encoding="utf-8"))
    allowed = {"__future__", "dataclasses", "hashlib", "json", "math", "re", "typing", "behavioral", "causality", "capability"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "eval", "exec", "compile", "__import__"}


def test_real_mt01_onboarding_and_a03_registered_closure():
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    from ai_commerce.engineering.minimum_test import approved_groups, file_hashes
    work = json.loads((ROOT / "work_items/BQ-01A_OFFLINE_BEHAVIOR_CRI_001.json").read_bytes())
    manifest = json.loads((ROOT / "work_items/mt01-components/bq-01a.json").read_bytes())
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_bytes())
    files = file_hashes(ROOT)
    selected = registered_action_plan(
        repo=ROOT, base_sha=BASE, base_files={p: h for p, h in files.items() if p not in work["allowed_paths"]},
        candidate_files=files, changed_paths=work["allowed_paths"], required_acs=work["acceptance"],
        fallback_argv=profiles["mt01-component-onboarding"], groups=approved_groups(),
        registered_profiles=profiles, allowed_paths=work["allowed_paths"],
    )
    assert manifest["sources"] == ["src/ai_commerce/creative_quality/" + n + ".py" for n in ("__init__", "behavioral", "causality", "capability")]
    assert manifest["consumers"] == ["src/ai_commerce/creative/" + n + ".py" for n in ("briefs", "intent", "reasoner", "sidecars")]
    assert manifest["contracts"] == [] and manifest["acs"] == work["acceptance"]
    assert selected["selected_groups"] == ["a03", "bq-01a"]
    assert not selected["fallback"] and not selected["unknown_acs"] and not selected["unknown_consumers"]
    assert not selected["impact_v21"]["missing_mapping"]
    assert all(g["execute"] and g["state"] == "EXECUTE" for g in selected["groups"])
    own, old = (next(g for g in selected["groups"] if g["group"] == n) for n in ("bq-01a", "a03"))
    assert own["selected_profile"] == "mt01-component-onboarding" and own["binding"]["onboarding_data_only"]
    assert own["binding"]["argv"] == ["python", "-m", "pytest", "tests/test_bq_01a_brief_causality.py", "-q", "-n", "12"]
    assert old["binding"]["argv"] == profiles["mt01-a03-tests"]
