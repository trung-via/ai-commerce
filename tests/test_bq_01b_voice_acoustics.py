"""Deterministic source contracts only: no voices, recordings or media scores."""

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
import unicodedata
import urllib.request

import pytest

from ai_commerce.creative.briefs import brief_digest, intake_brief
from ai_commerce.creative_quality.behavioral import A03_INTAKE_REF, QualityDraftError, build_behavioral_brief
from ai_commerce.creative_quality.causality import CausalShotPlanDraft, build_causal_plan
from ai_commerce.creative_quality.capability import CAPABILITY_KEYS, ModelCapabilityProfileDraft, compile_capability_mock
from ai_commerce.creative_quality.voice_intent import VietnameseVoiceIntentDraft, build_voice_intent
from ai_commerce.creative_quality.acoustic_scene import AcousticSceneDraft, build_acoustic_scene
from ai_commerce.creative_quality.voice_compile import (
    BQ01bVoiceCapabilityPreview, VOICE_CAPABILITY_KEYS, VoiceAcousticCapabilityDraft, compile_voice_capability_mock,
)


ROOT = Path(__file__).resolve().parents[1]
BASE = "381b70f0979258d24a2524c1e8f133338be11ddc"
TEXT = "  Mưa Sài Gòn — má, mà, mả, mã, mạ… ‘lắng nghe nhé!’ 🌧️\n"
REFS = [A03_INTAKE_REF]
KINDS = (VietnameseVoiceIntentDraft, AcousticSceneDraft, VoiceAcousticCapabilityDraft, BQ01bVoiceCapabilityPreview)
ADDITIONS = [
    "src/ai_commerce/creative_quality/voice_intent.py",
    "src/ai_commerce/creative_quality/acoustic_scene.py",
    "src/ai_commerce/creative_quality/voice_compile.py",
    "tests/test_bq_01b_voice_acoustics.py",
    "work_items/mt01-components/bq-01b.json",
    "work_items/BQ-01B_VIETNAMESE_VOICE_ACOUSTICS_001.json",
    "docs/engineering/BQ_01B_OFFLINE_HANDOFF.md",
    "docs/creative/BQ_01B_VIETNAMESE_VOICE_DESIGN_GUIDE.md",
]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_plan(mode="VOICEOVER", text=TEXT, language="vi-VN", sync="NOT_REQUIRED", visual="ANIMATION", product=None):
    source = intake_brief({"brief_id": "fixture:bq01b", "objective": text or "Hoạt hình im lặng do caller viết.",
                           "prompt": text or "Hoạt hình im lặng do caller viết.", "product_ref": product, "source_refs": []})
    brief = build_behavioral_brief(source, source_brief_digest=brief_digest(source), provenance="SYNTHETIC_ONLY",
        hypotheses=[{"id": "h1", "statement": "Giả thuyết: khoảng lặng có thể phù hợp.", "assumptions": ["Chưa quan sát."],
                     "counterexamples": ["Caller có thể muốn lời kể."], "source_refs": REFS, "truth": "HYPOTHESIS"}])
    audio = {"mode": mode, "language": "UNKNOWN" if mode in ("SILENT", "UNKNOWN") else language,
             "text": None if mode in ("SILENT", "UNKNOWN") else text, "accent_intent": None,
             "delivery_intent": None, "acoustic_intent": None,
             "lip_sync": "UNKNOWN" if mode == "UNKNOWN" else sync,
             "source_refs": [] if mode in ("SILENT", "UNKNOWN") else REFS}
    shots = [{"id": "s" + str(i + 1), "order": i, "start_ms": i * 1000, "end_ms": (i + 1) * 1000,
              "description": "Caller: chiếc cốc hư cấu bay.", "objects": [], "constraints": []} for i in range(2)]
    return build_causal_plan(brief, behavioral_brief_digest=brief.digest, shots=shots,
                            edges=[{"cause": "s1", "effect": "s2", "continuity_objects": []}],
                            visual_form=visual, physics_intent="INTENTIONAL_FANTASY", audio=audio)


def direction(text):
    return {"status": "SUBJECTIVE_DIRECTION", "stress": [{"start_char": 0, "end_char": 1}],
            "tone": "PRESERVE_SOURCE_TONES", "pauses": [{"after_char": len(text), "duration_ms": 100}],
            "pacing": "MODERATE", "intonation": "FALLING",
            "accent": {"region": "SOUTHERN", "label": "miền Nam do caller chọn", "status": "SUBJECTIVE_DIRECTION"}}


def make_segment(text, **changes):
    data = {"id": "v1", "shot_id": "s1", "start_ms": 0, "end_ms": 1000,
            "script_start": 0, "script_end": len(text), "text": text, "source_refs": REFS, "direction": direction(text)}
    data.update(changes)
    return data


def make_voice(plan, segments=None):
    audio = plan.to_dict()["audio"]
    if segments is None:
        segments = [] if audio["mode"] in ("SILENT", "UNKNOWN") or audio["text"] in (None, "UNKNOWN") else [make_segment(audio["text"])]
    return build_voice_intent(plan, plan_digest=plan.digest, segments=segments)


def environment(identifier="room1", scope="VO_NARRATION"):
    return {"id": identifier, "scope": scope, "geometry": "SMALL_ENCLOSED", "room": "BOOTH",
            "reverb": "DRY", "noise": "QUIET", "distance": "CLOSE", "evidence": "SUBJECTIVE_DIRECTION", "source_refs": REFS}


def cue(identifier="c1", kind="SPEECH", **changes):
    data = {"id": identifier, "shot_id": "s1", "start_ms": 0, "end_ms": 1000, "frames": None,
            "kind": kind, "layer": "FOREGROUND" if kind == "SPEECH" else "BACKGROUND",
            "channel_id": "speech" if kind == "SPEECH" else "ambient", "environment_id": "room1" if kind == "SPEECH" else "scene1",
            "voice_segment_ids": ["v1"] if kind == "SPEECH" else [], "source_refs": REFS, "asset_window": None,
            "rights": "UNPROVEN", "evidence": "UNVERIFIED_SCENE_INTENT"}
    data.update(changes)
    return data


def make_scene(voice, ambient=False, kind="AMBIENCE"):
    data = voice.to_dict()
    if data["audio_mode"] == "UNKNOWN":
        return build_acoustic_scene(voice, voice_digest=voice.digest, sound_mode="UNKNOWN")
    channels, envs, cues = [], [], []
    if data["segments"]:
        channels.append({"id": "speech", "policy": "SINGLE"})
        envs.append(environment(scope="VO_NARRATION" if data["audio_mode"] == "VOICEOVER" else "ON_SCREEN_SPEAKER"))
        cues.extend(cue("c:" + s["id"], shot_id=s["shot_id"], start_ms=s["start_ms"], end_ms=s["end_ms"], voice_segment_ids=[s["id"]]) for s in data["segments"])
    if ambient:
        channels.append({"id": "ambient", "policy": "SINGLE"})
        envs.append(environment("scene1", "SCENE"))
        cues.append(cue("ambient1", kind))
    return build_acoustic_scene(voice, voice_digest=voice.digest,
        sound_mode="FULL_MUTE" if data["audio_mode"] == "SILENT" and not ambient else "SOUND_REQUESTED",
        ambient_requested=ambient, channels=channels, environments=envs, cues=cues)


def base_profile(changes=None):
    caps = dict.fromkeys(CAPABILITY_KEYS, "SUPPORTED")
    for key, status in (changes or {}).items():
        if status is None:
            caps.pop(key, None)
        else:
            caps[key] = status
    content = "TEST_ONLY caller fixture; not an observed model or provider benchmark."
    return ModelCapabilityProfileDraft.from_dict({"schema_version": ModelCapabilityProfileDraft.SCHEMA,
        "execution_state": "SIMULATED_ONLY", "provenance": "TEST_ONLY", "profile_id": "fixture:base",
        "metadata_source": {"ref": "fixture:base-source", "content": content, "sha256": sha(content.encode())}, "capabilities": caps})


def extension(profile, changes=None, languages=None):
    caps = dict.fromkeys(VOICE_CAPABILITY_KEYS, "SUPPORTED")
    for key, status in (changes or {}).items():
        if status is None:
            caps.pop(key, None)
        else:
            caps[key] = status
    content = "TEST_ONLY synthetic voice/acoustic metadata; quality, rights and dialect are unproven."
    return VoiceAcousticCapabilityDraft.from_dict({"schema_version": VoiceAcousticCapabilityDraft.SCHEMA,
        "execution_state": "SIMULATED_ONLY", "provenance": "TEST_ONLY", "profile_id": "fixture:voice",
        "base_profile_digest": profile.digest,
        "metadata_source": {"ref": "fixture:voice-source", "content": content, "sha256": sha(content.encode())},
        "capabilities": caps, "languages": {"vi": "SUPPORTED", "vi-VN": "SUPPORTED"} if languages is None else languages})


def compile_preview(plan, scene, base=None, extra=None):
    return compile_voice_capability_mock(plan, scene, base, extra, plan_digest=plan.digest, scene_digest=scene.digest,
        profile_digest=None if base is None else base.digest, voice_profile_digest=None if extra is None else extra.digest)


@pytest.fixture(scope="module")
def bundle():
    plan = make_plan()
    voice = make_voice(plan)
    scene = make_scene(voice, ambient=True)
    base = base_profile()
    extra = extension(base)
    return plan, voice, scene, base, extra, compile_preview(plan, scene, base, extra)


def record(kind, bundle):
    return bundle[{VietnameseVoiceIntentDraft: 1, AcousticSceneDraft: 2, VoiceAcousticCapabilityDraft: 4,
                   BQ01bVoiceCapabilityPreview: 5}[kind]]


def replace(value, path, change):
    data = deepcopy(value)
    parent = data
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = change
    return data


@pytest.mark.parametrize("text", [TEXT, "ma má mà mả mã mạ", "ă â đ ê ô ơ ư", "Dấu hỏi? Dấu ngã!", "‘Ơ… nhé!’",
    "Mưa\u0300 — À", "A\u0300 khác À", "  Giữ\tnguyên\n", "Sắc: á; huyền: à; nặng: ạ.",
    "Giọng Huế do caller chọn.", "Đà Nẵng / Hà Nội / Sài Gòn", "Xin chào 🙂", unicodedata.normalize("NFD", TEXT),
    "Hư cấu: mặt trăng biết nói.", "Cốc không có SKU hay CTA."])
def test_real_a03_bq01a_unicode_source_chain_is_unchanged(text):
    plan = make_plan(text=text)
    before = plan.to_bytes()
    voice = make_voice(plan)
    value = voice.to_dict()
    assert value["segments"][0]["text"] == text
    assert value["plan"]["audio"]["text"] == text
    assert value["source_brief_digest"] == brief_digest(value["plan"]["behavioral_brief"]["source_brief"])
    assert plan.to_bytes() == before and value["plan_digest"] == plan.digest
    assert value["ownership"] == value["rights"] == value["speaker_identity"] == "UNPROVEN"


def test_nfc_and_nfd_are_distinct_exact_pins():
    nfc, nfd = unicodedata.normalize("NFC", TEXT), unicodedata.normalize("NFD", TEXT)
    a, b = make_voice(make_plan(text=nfc)), make_voice(make_plan(text=nfd))
    assert nfc != nfd and a.digest != b.digest
    assert a.to_dict()["segments"][0]["text"].encode() == nfc.encode()
    assert b.to_dict()["segments"][0]["text"].encode() == nfd.encode()


@pytest.mark.parametrize("mode", ["SILENT", "VOICEOVER", "ON_SCREEN_SPEECH", "UNKNOWN"])
@pytest.mark.parametrize("visual", ["ANIMATION", "FICTION", "STYLIZED", "REALISTIC"])
@pytest.mark.parametrize("product", [None, "caller:optional-product"])
def test_audio_forms_and_optional_product(mode, visual, product):
    plan = make_plan(mode, visual=visual, product=product)
    voice = make_voice(plan)
    scene = make_scene(voice)
    base = base_profile()
    result = compile_preview(plan, scene, base, extension(base)).to_dict()
    assert result["status"] == ("HOLD_UNKNOWN" if mode == "UNKNOWN" else "PLAN_COMPATIBLE_SIMULATED")
    assert voice.to_dict()["plan"]["behavioral_brief"]["product_ref"] == product
    if mode == "SILENT":
        assert scene.to_dict()["sound_mode"] == "FULL_MUTE" and not voice.to_dict()["segments"]
        assert not any("VOICE" in c["constraint"] or "LIP_SYNC" in c["constraint"] for c in result["checks"])


@pytest.mark.parametrize("mode", ["SILENT", "VOICEOVER", "ON_SCREEN_SPEECH"])
@pytest.mark.parametrize("kind,feature", [("AMBIENCE", "AMBIENCE"), ("MUSIC", "MUSIC"), ("SOUND_EFFECT", "SOUND_EFFECTS")])
def test_separately_requested_sound_and_unproven_rights(mode, kind, feature):
    plan = make_plan(mode)
    scene = make_scene(make_voice(plan), ambient=True, kind=kind)
    base = base_profile()
    good = compile_preview(plan, scene, base, extension(base)).to_dict()
    missing = compile_preview(plan, scene, base, extension(base, {feature: None})).to_dict()
    assert good["status"] == "PLAN_COMPATIBLE_SIMULATED" and good["rights"] == "UNPROVEN"
    assert missing["status"] == "HOLD_UNKNOWN" and "BQ01b:" + feature in missing["unknown"]
    assert all(c["rights"] == "UNPROVEN" for c in scene.to_dict()["cues"])


def test_silent_full_mute_needs_no_tts_lipsync_or_extension():
    plan = make_plan("SILENT")
    base = base_profile({"VIETNAMESE_VOICE": "UNSUPPORTED", "VOICEOVER": None, "LIP_SYNC": "UNSUPPORTED", "ACOUSTICS": None})
    result = compile_preview(plan, make_scene(make_voice(plan)), base).to_dict()
    assert result["status"] == "PLAN_COMPATIBLE_SIMULATED"
    assert result["voice_profile"] is None


@pytest.mark.parametrize("region,label", [("NORTHERN", "Hà Nội"), ("CENTRAL", "Huế"), ("SOUTHERN", "Sài Gòn"),
    ("OTHER", "Vùng do caller tự mô tả"), ("UNKNOWN", "chưa chọn")])
def test_region_is_subjective_caller_choice(bundle, region, label):
    plan = bundle[0]
    seg = make_segment(TEXT)
    seg["direction"]["accent"] = {"region": region, "label": label, "status": "SUBJECTIVE_DIRECTION"}
    voice = make_voice(plan, [seg])
    result = compile_preview(plan, make_scene(voice), bundle[3], bundle[4]).to_dict()
    assert voice.to_dict()["segments"][0]["direction"]["accent"]["label"] == label
    assert result["status"] == ("HOLD_UNKNOWN" if region == "UNKNOWN" else "PLAN_COMPATIBLE_SIMULATED")
    assert result["dialect_accuracy"] == "UNPROVEN"


@pytest.mark.parametrize("tag", ["vi", "vi-VN", "vi-XX", "vi-US"])
@pytest.mark.parametrize("state", ["SUPPORTED", "UNSUPPORTED", "UNKNOWN", None])
def test_exact_language_requires_explicit_test_metadata(tag, state):
    plan = make_plan(language=tag)
    base = base_profile()
    extra = extension(base, languages={} if state is None else {tag: state})
    result = compile_preview(plan, make_scene(make_voice(plan)), base, extra).to_dict()
    assert result["status"] == ("PLAN_COMPATIBLE_SIMULATED" if state == "SUPPORTED" else "HOLD_UNSUPPORTED" if state == "UNSUPPORTED" else "HOLD_UNKNOWN")
    assert result["dialect_accuracy"] == "UNPROVEN"


@pytest.mark.parametrize("language", ["en", "vi-vn", "vi-VNM", "VI", "vi-VN-x-private", "provider:verified", "", True])
def test_wrong_language_cannot_be_relabelled_vietnamese(bundle, language):
    value = bundle[1].to_dict()
    with pytest.raises(QualityDraftError):
        VietnameseVoiceIntentDraft.from_dict(replace(value, ("language",), language))


@pytest.mark.parametrize("feature", VOICE_CAPABILITY_KEYS)
@pytest.mark.parametrize("state", ["UNSUPPORTED", "UNKNOWN", None])
def test_every_requested_extended_feature_is_checked(bundle, feature, state):
    plan = make_plan("ON_SCREEN_SPEECH", sync="REQUIRED")
    voice = make_voice(plan)
    scene = make_scene(voice, ambient=True, kind="MUSIC").to_dict()
    scene["cues"][1]["frames"] = {"rate_num": 25, "rate_den": 1, "start": 0, "end": 25}
    scene["cues"][1]["asset_window"] = {"source_duration_ms": 2000, "trim_start_ms": 100, "trim_end_ms": 1100}
    # Include all three sound kinds and both voice scopes across concrete
    # fixtures, while preserving separate channels and single speech mode.
    if feature == "VOICEOVER_ENVIRONMENT":
        plan = bundle[0]
        scene = bundle[2].to_dict()
    elif feature in ("AMBIENCE", "SOUND_EFFECTS"):
        scene["cues"][1]["kind"] = "AMBIENCE" if feature == "AMBIENCE" else "SOUND_EFFECT"
    scene = AcousticSceneDraft.from_dict(scene)
    result = compile_preview(plan, scene, bundle[3], extension(bundle[3], {feature: state})).to_dict()
    assert "BQ01b:" + feature in result["unsupported" if state == "UNSUPPORTED" else "unknown"]
    assert result["status"] == ("HOLD_UNSUPPORTED" if state == "UNSUPPORTED" else "HOLD_UNKNOWN")


@pytest.mark.parametrize("feature", ["SHOT_DAG", "ANIMATION", "INTENTIONAL_FANTASY", "VOICEOVER", "VIETNAMESE_VOICE"])
@pytest.mark.parametrize("state", ["UNSUPPORTED", "UNKNOWN", None])
def test_legacy_critical_hold_is_never_overridden(bundle, feature, state):
    base = base_profile({feature: state})
    legacy = compile_capability_mock(bundle[0], base, plan_digest=bundle[0].digest, profile_digest=base.digest)
    result = compile_preview(bundle[0], bundle[2], base, extension(base)).to_dict()
    assert result["bq01a_status"] == legacy.to_dict()["status"]
    assert result["status"] == legacy.to_dict()["status"]
    assert result["bq01a_preview_digest"] == legacy.digest
    assert "BQ01a:" + feature in result["unsupported" if state == "UNSUPPORTED" else "unknown"]


@pytest.mark.parametrize("key", ["accent_intent", "delivery_intent", "acoustic_intent", "language", "text", "lip_sync"])
def test_legacy_unknown_cannot_be_resolved_by_new_metadata(bundle, key):
    plan = make_plan("ON_SCREEN_SPEECH").to_dict()
    plan["audio"][key] = "UNKNOWN"
    plan = CausalShotPlanDraft.from_dict(plan)
    result = compile_preview(plan, make_scene(make_voice(plan)), bundle[3], bundle[4]).to_dict()
    assert result["status"] == result["bq01a_status"] == "HOLD_UNKNOWN"
    assert "BQ01a:audio." + key in result["unknown"]


@pytest.mark.parametrize("field", ["tone", "pacing", "intonation", "accent"])
def test_unknown_direction_remains_unknown(bundle, field):
    seg = make_segment(TEXT)
    if field == "accent":
        seg["direction"][field]["region"] = "UNKNOWN"
    else:
        seg["direction"][field] = "UNKNOWN"
    scene = make_scene(make_voice(bundle[0], [seg]))
    result = compile_preview(bundle[0], scene, bundle[3], bundle[4]).to_dict()
    assert result["status"] == "HOLD_UNKNOWN" and "BQ01b.input:segment:v1:" + field in result["unknown"]


@pytest.mark.parametrize("field", ["geometry", "room", "reverb", "noise", "distance"])
def test_unknown_acoustic_intent_is_not_measured(bundle, field):
    scene = AcousticSceneDraft.from_dict(replace(bundle[2].to_dict(), ("environments", 0, field), "UNKNOWN"))
    result = compile_preview(bundle[0], scene, bundle[3], bundle[4]).to_dict()
    assert result["status"] == "HOLD_UNKNOWN" and "BQ01b.input:environment:room1:" + field in result["unknown"]


def test_missing_profiles_and_mixed_negative_evidence(bundle):
    result = compile_preview(bundle[0], bundle[2]).to_dict()
    assert result["status"] == "HOLD_UNKNOWN" and result["bq01a_status"] == "HOLD_UNKNOWN"
    result = compile_preview(bundle[0], bundle[2], bundle[3]).to_dict()
    assert result["status"] == "HOLD_UNKNOWN" and "BQ01b:VN_SCRIPT" in result["unknown"]
    base = base_profile({"SHOT_DAG": "UNSUPPORTED", "VIETNAMESE_VOICE": None})
    result = compile_preview(bundle[0], bundle[2], base).to_dict()
    assert result["status"] == "HOLD_UNSUPPORTED" and result["unsupported"] and result["unknown"]


@pytest.mark.parametrize("text", [None, "UNKNOWN"])
def test_no_script_is_fabricated_for_unresolved_voice(bundle, text):
    plan = make_plan(text=text)
    voice = make_voice(plan)
    assert voice.to_dict()["segments"] == []
    result = compile_preview(plan, make_scene(voice), bundle[3], bundle[4]).to_dict()
    assert result["status"] == "HOLD_UNKNOWN" and "BQ01b.input:voice.segments" in result["unknown"]


@pytest.mark.parametrize("path,value", [
    (("id",), "bad id"), (("shot_id",), "absent"), (("start_ms",), -1), (("end_ms",), 1001),
    (("start_ms",), 1000), (("end_ms",), 0), (("start_ms",), True), (("end_ms",), 1000.0),
    (("end_ms",), "1000"), (("script_start",), -1), (("script_start",), True), (("script_end",), len(TEXT)+1),
    (("script_end",), 0), (("text",), "Translated or invented script"), (("text",), unicodedata.normalize("NFD", TEXT)),
    (("source_refs",), ["unbound:source"]), (("source_refs",), []), (("source_refs",), REFS * 2),
    (("direction", "status"), "CERTIFIED"), (("direction", "tone"), "RECOGNIZED_NATIVE"),
    (("direction", "pacing"), "MEASURED"), (("direction", "intonation"), 1),
    (("direction", "stress", 0, "end_char"), 0), (("direction", "stress", 0, "start_char"), True),
    (("direction", "pauses", 0, "duration_ms"), 1000), (("direction", "pauses", 0, "duration_ms"), -1),
    (("direction", "pauses", 0, "after_char"), len(TEXT)+1), (("direction", "accent", "status"), "VERIFIED_DIALECT"),
])
def test_invalid_segment_contracts(bundle, path, value):
    with pytest.raises(QualityDraftError):
        make_voice(bundle[0], [replace(make_segment(TEXT), path, value)])


@pytest.mark.parametrize("defect", ["duplicate_id", "time_overlap", "reversed_time", "script_overlap", "duplicate_pause", "stress_overlap"])
def test_segment_monotonic_constraints(bundle, defect):
    text = TEXT.strip()
    plan = make_plan(text=text)
    middle = len(text)//2
    a = make_segment(text[:middle], script_end=middle)
    b = make_segment(text[middle:], id="v2", shot_id="s2", start_ms=1000, end_ms=2000, script_start=middle, script_end=len(text))
    segments = [a, b]
    if defect == "duplicate_id":
        b["id"] = "v1"
    elif defect == "time_overlap":
        b.update(shot_id="s1", start_ms=500, end_ms=1000)
    elif defect == "reversed_time":
        segments.reverse()
    elif defect == "script_overlap":
        b.update(script_start=0, script_end=middle, text=text[:middle], direction=direction(text[:middle]))
    elif defect == "duplicate_pause":
        a["direction"]["pauses"] *= 2
    else:
        a["direction"]["stress"] *= 2
    with pytest.raises(QualityDraftError):
        make_voice(plan, segments)


def test_adjacent_segments_and_shot_boundaries_are_valid(bundle):
    text = "Xin chào!"
    plan = make_plan(text=text)
    a = make_segment("Xin", script_end=3, end_ms=500)
    b = make_segment(" chào!", id="v2", start_ms=500, end_ms=1000, script_start=3, script_end=len(text))
    voice = make_voice(plan, [a, b])
    result = compile_preview(plan, make_scene(voice), bundle[3], bundle[4]).to_dict()
    assert result["status"] == "PLAN_COMPATIBLE_SIMULATED"


@pytest.mark.parametrize("path,value", [
    (("cues", 0, "id"), "bad id"), (("cues", 0, "shot_id"), "absent"),
    (("cues", 0, "start_ms"), -1), (("cues", 0, "end_ms"), 1001), (("cues", 0, "end_ms"), True),
    (("cues", 0, "end_ms"), 0), (("cues", 0, "end_ms"), 999), (("cues", 0, "voice_segment_ids"), ["absent"]),
    (("cues", 0, "voice_segment_ids"), []), (("cues", 0, "voice_segment_ids"), ["v1", "v1"]),
    (("cues", 0, "channel_id"), "absent"), (("cues", 0, "environment_id"), "absent"),
    (("cues", 0, "layer"), "BACKGROUND"), (("cues", 0, "kind"), "PROVIDER_VERIFIED"),
    (("cues", 0, "rights"), "LICENSED"), (("cues", 0, "evidence"), "PROVIDER_VERIFIED"),
    (("cues", 1, "voice_segment_ids"), ["v1"]), (("cues", 0, "source_refs"), ["unbound"]),
    (("ambient_requested",), False), (("ambient_requested",), 1), (("sound_mode",), "FULL_MUTE"),
    (("sound_mode",), "UNKNOWN"), (("environments", 0, "scope"), "ON_SCREEN_SPEAKER"),
    (("environments", 0, "scope"), "SCENE"), (("environments", 0, "reverb"), "measured 0.3 seconds"),
    (("environments", 0, "noise"), "provider verified quiet"), (("environments", 0, "evidence"), "CERTIFIED"),
    (("environments", 0, "room"), "OUTDOOR"), (("environments", 0, "source_refs"), []),
    (("channels", 0, "policy"), "AUTO"), (("cues", 0, "asset_window"), {"source_duration_ms":1000,"trim_start_ms":0,"trim_end_ms":1000}),
])
def test_invalid_acoustic_contracts(bundle, path, value):
    with pytest.raises(QualityDraftError):
        AcousticSceneDraft.from_dict(replace(bundle[2].to_dict(), path, value))


@pytest.mark.parametrize("field,value", [("source_duration_ms", 999), ("source_duration_ms", -1), ("trim_start_ms", -1),
    ("trim_start_ms", 2000), ("trim_end_ms", 2100), ("trim_end_ms", 1000), ("trim_start_ms", True)])
def test_impossible_trim_is_rejected(bundle, field, value):
    data = bundle[2].to_dict()
    window = {"source_duration_ms": 2000, "trim_start_ms": 100, "trim_end_ms": 1100}
    window[field] = value
    data["cues"][1]["asset_window"] = window
    with pytest.raises(QualityDraftError):
        AcousticSceneDraft.from_dict(data)


@pytest.mark.parametrize("field,value", [("rate_num", True), ("rate_den", 0), ("rate_num", 121), ("start", -1),
    ("start", 1), ("end", 24), ("end", 0), ("end", 25.0)])
def test_frame_time_binding_is_exact(bundle, field, value):
    data = bundle[2].to_dict()
    frames = {"rate_num": 25, "rate_den": 1, "start": 0, "end": 25}
    frames[field] = value
    data["cues"][1]["frames"] = frames
    with pytest.raises(QualityDraftError):
        AcousticSceneDraft.from_dict(data)


@pytest.mark.parametrize("duplicate", ["channels", "environments", "cues"])
def test_duplicate_acoustic_ids_rejected(bundle, duplicate):
    value = bundle[2].to_dict()
    value[duplicate].append(deepcopy(value[duplicate][0]))
    with pytest.raises(QualityDraftError):
        AcousticSceneDraft.from_dict(value)


@pytest.mark.parametrize("policy,valid", [("SINGLE", False), ("MIX", True)])
def test_explicit_channel_overlap_policy(bundle, policy, valid):
    value = bundle[2].to_dict()
    value["channels"][1]["policy"] = policy
    value["cues"].append({**value["cues"][1], "id": "ambient2", "start_ms": 500})
    if valid:
        assert AcousticSceneDraft.from_dict(value)
    else:
        with pytest.raises(QualityDraftError, match="overlap"):
            AcousticSceneDraft.from_dict(value)


@pytest.mark.parametrize("mode", ["SILENT", "UNKNOWN"])
def test_cannot_invent_voice_on_unvoiced_plan(mode):
    plan = make_plan(mode)
    with pytest.raises(QualityDraftError):
        make_voice(plan, [make_segment(TEXT)])


def test_full_mute_and_unknown_cannot_contradict_voice(bundle):
    with pytest.raises(QualityDraftError):
        build_acoustic_scene(bundle[1], voice_digest=bundle[1].digest, sound_mode="FULL_MUTE")
    unknown_scene = build_acoustic_scene(bundle[1], voice_digest=bundle[1].digest, sound_mode="UNKNOWN")
    assert compile_preview(bundle[0], unknown_scene, bundle[3], bundle[4]).to_dict()["status"] == "HOLD_UNKNOWN"
    voice = make_voice(make_plan("UNKNOWN"))
    with pytest.raises(QualityDraftError):
        build_acoustic_scene(voice, voice_digest=voice.digest, sound_mode="SOUND_REQUESTED")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("claim", ["provider_verified", "benchmark_score", "native_speaker_trial", "metamodel", "sample_rate_hz",
    "speaker_ethnicity", "speaker_consent", "license", "publish", "cost_usd"])
def test_false_attestations_and_extra_metadata_reject(bundle, kind, claim):
    data = record(kind, bundle).to_dict()
    data[claim] = "VERIFIED"
    with pytest.raises(QualityDraftError):
        kind.from_dict(data)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("bad", ["duplicate", "extra", "missing", "deep", "oversized_text", "oversized_payload", "oversized_array",
    "nonfinite_nan", "nonfinite_inf", "integer_overflow", "invalid_unicode", "cycle", "custom", "non_text_key"])
def test_strict_bounded_json_all_construction_paths(bundle, kind, bad):
    data = record(kind, bundle).to_dict()
    if bad == "duplicate":
        text = json.dumps(data, ensure_ascii=False)
        text = '{"schema_version":"spoof",' + text[1:]
        for constructor in (kind.from_json, kind):
            with pytest.raises(QualityDraftError):
                constructor(text)
        return
    if bad == "missing":
        data.pop("execution_state")
    elif bad == "deep":
        nested = None
        for _ in range(20):
            nested = [nested]
        data["extra"] = nested
    elif bad == "oversized_text":
        data["extra"] = "ệ" * 6000
    elif bad == "oversized_payload":
        with pytest.raises(QualityDraftError):
            kind.from_json(" " * 131073)
        return
    elif bad == "oversized_array":
        data["extra"] = [0] * 129
    elif bad == "nonfinite_nan":
        data["extra"] = float("nan")
    elif bad == "nonfinite_inf":
        data["extra"] = float("inf")
    elif bad == "integer_overflow":
        data["extra"] = 2**53
    elif bad == "invalid_unicode":
        data["extra"] = "\ud800"
    elif bad == "cycle":
        data["extra"] = data
    elif bad == "custom":
        data["extra"] = object()
    elif bad == "non_text_key":
        data[1] = "value"
    else:
        data["extra"] = "value"
    with pytest.raises(QualityDraftError):
        kind.from_dict(data)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("text", ['{"a":NaN}', '{"a":Infinity}', '{"a":1e999}', '{"a":"\\ud800"}', '{', '[]', 'null'])
def test_malformed_json_does_not_become_intent(bundle, kind, text):
    with pytest.raises(QualityDraftError):
        kind.from_json(text)


@pytest.mark.parametrize("kind", KINDS)
def test_canonical_replay_detachment_and_frozen_state(bundle, kind):
    value = record(kind, bundle)
    assert kind.from_json(value.to_json()).to_bytes() == value.to_bytes()
    assert kind.from_dict(dict(reversed(list(value.to_dict().items())))).digest == value.digest
    detached = value.to_dict()
    detached["execution_state"] = "FORGED"
    assert value.to_dict()["execution_state"] == "SIMULATED_ONLY"
    with pytest.raises((FrozenInstanceError, AttributeError)):
        value._json = "{}"
    value.verify_digest(value.digest)
    with pytest.raises(QualityDraftError):
        value.verify_digest("0" * 64)


@pytest.mark.parametrize("kind,path", [
    (VietnameseVoiceIntentDraft, ("plan_digest",)), (VietnameseVoiceIntentDraft, ("behavioral_brief_digest",)),
    (VietnameseVoiceIntentDraft, ("source_brief_digest",)), (VietnameseVoiceIntentDraft, ("plan", "behavioral_brief", "sources", 0, "sha256")),
    (AcousticSceneDraft, ("voice_digest",)), (AcousticSceneDraft, ("plan_digest",)), (AcousticSceneDraft, ("source_brief_digest",)),
    (VoiceAcousticCapabilityDraft, ("metadata_source", "sha256")),
    (BQ01bVoiceCapabilityPreview, ("plan_digest",)), (BQ01bVoiceCapabilityPreview, ("voice_digest",)),
    (BQ01bVoiceCapabilityPreview, ("profile_digest",)), (BQ01bVoiceCapabilityPreview, ("voice_profile_digest",)),
    (BQ01bVoiceCapabilityPreview, ("bq01a_preview_digest",)), (BQ01bVoiceCapabilityPreview, ("source_brief_digest",)),
])
def test_exact_digest_tamper_reject(bundle, kind, path):
    with pytest.raises(QualityDraftError):
        kind.from_dict(replace(record(kind, bundle).to_dict(), path, "0" * 64))


@pytest.mark.parametrize("field,value", [("status", "PLAN_COMPATIBLE_SIMULATED"), ("media_generation", "GENERATED"),
    ("media_inspection", "INSPECTED"), ("voice_naturalness", "NATIVE_CERTIFIED"), ("dialect_accuracy", "VERIFIED"),
    ("speaker_identity", "VERIFIED"), ("rights", "PROVEN"), ("costs", 0), ("execution_authority", "PUBLISH"),
    ("checks", []), ("unknown", []), ("bq01a_status", "PLAN_COMPATIBLE_SIMULATED")])
def test_preview_cannot_promote_held_result(bundle, field, value):
    held = compile_preview(bundle[0], bundle[2]).to_dict()
    with pytest.raises(QualityDraftError):
        BQ01bVoiceCapabilityPreview.from_dict(replace(held, (field,), value))


@pytest.mark.parametrize("field,value", [("provenance", "PROVIDER_VERIFIED"), ("execution_state", "LIVE"),
    ("capabilities", {"BEST_MODEL": "SUPPORTED"}), ("capabilities", {"PACING": True}),
    ("languages", {"vi": "CERTIFIED"}), ("languages", {"en": "SUPPORTED"})])
def test_metadata_cannot_become_benchmark_or_provider_authority(bundle, field, value):
    with pytest.raises(QualityDraftError):
        VoiceAcousticCapabilityDraft.from_dict(replace(bundle[4].to_dict(), (field,), value))


def test_valid_hashes_cannot_mix_different_plan_or_profile(bundle):
    other_plan = make_plan(text="Một kịch bản khác.")
    with pytest.raises(QualityDraftError, match="plan mismatch"):
        compile_preview(other_plan, bundle[2], bundle[3], bundle[4])
    other_base = base_profile({"REALISTIC": "UNSUPPORTED"})
    with pytest.raises(QualityDraftError, match="bind"):
        compile_preview(bundle[0], bundle[2], other_base, bundle[4])


@pytest.mark.parametrize("pin", [None, "A" * 64, "0" * 63, "0" * 64, True])
def test_builder_requires_exact_original_plan_pin(bundle, pin):
    with pytest.raises(QualityDraftError):
        build_voice_intent(bundle[0], plan_digest=pin, segments=[])


def test_no_application_effects_and_deterministic_replay(bundle, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("application external effect attempted")
    with monkeypatch.context() as patch:
        for target, name in ((builtins, "open"), (io, "open"), (socket, "socket"), (socket, "create_connection"),
                             (subprocess, "run"), (subprocess, "Popen"), (urllib.request, "urlopen"), (os, "getenv"), (os, "urandom")):
            patch.setattr(target, name, deny)
        voice = make_voice(bundle[0])
        scene = make_scene(voice, ambient=True)
        a = compile_preview(bundle[0], scene, bundle[3], bundle[4])
        b = BQ01bVoiceCapabilityPreview.from_json(a.to_json())
        assert a.digest == b.digest == bundle[5].digest


@pytest.mark.parametrize("module", ["voice_intent", "acoustic_scene", "voice_compile"])
def test_new_source_imports_and_calls_are_effect_free(module):
    tree = ast.parse((ROOT / ("src/ai_commerce/creative_quality/" + module + ".py")).read_text(encoding="utf-8"))
    allowed = {"__future__", "re", "typing", "behavioral", "causality", "capability", "voice_intent", "acoustic_scene"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "eval", "exec", "compile", "__import__"}


def test_import_isolation_has_no_application_effects():
    code = '''
import sys, builtins, io, os, socket, subprocess, urllib.request
sys.path.insert(0, sys.argv[1])
# Preload the permitted standard library dependencies before denying effects.
import dataclasses, hashlib, json, math, re, typing
def deny(*a, **k):
    raise AssertionError("import attempted application effect")
builtins.open = io.open = os.getenv = os.urandom = deny
socket.socket = socket.create_connection = subprocess.run = subprocess.Popen = urllib.request.urlopen = deny
import ai_commerce.creative_quality.voice_intent
import ai_commerce.creative_quality.acoustic_scene
import ai_commerce.creative_quality.voice_compile
print("PURE_IMPORT_OK")
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "src")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PURE_IMPORT_OK"


def test_registered_new_owner_and_actual_readonly_consumer_closure():
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    from ai_commerce.engineering.minimum_test import approved_groups, file_hashes
    work = json.loads((ROOT / ADDITIONS[5]).read_bytes())
    manifest = json.loads((ROOT / ADDITIONS[4]).read_bytes())
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_bytes())
    files = file_hashes(ROOT)
    selected = registered_action_plan(repo=ROOT, base_sha=BASE,
        base_files={p: h for p, h in files.items() if p not in ADDITIONS}, candidate_files=files,
        changed_paths=ADDITIONS, required_acs=work["acceptance"], fallback_argv=profiles["mt01-component-onboarding"],
        groups=approved_groups(), registered_profiles=profiles, allowed_paths=ADDITIONS)
    assert manifest["sources"] == ADDITIONS[:3] and manifest["tests"] == ADDITIONS[3:4]
    assert manifest["consumers"] == ["src/ai_commerce/creative_quality/" + name + ".py" for name in ("causality", "capability", "behavioral")] + ["src/ai_commerce/creative/briefs.py"]
    assert manifest["contracts"] == [] and manifest["acs"] == work["acceptance"]
    assert work["allowed_paths"] == ADDITIONS and work["source_sha"] == BASE
    assert selected["selected_groups"] == ["a03", "bq-01a", "bq-01b"]
    assert not selected["impact_v21"]["missing_mapping"] and not selected["fallback"]
    assert not selected["unknown_acs"] and not selected["unknown_consumers"]
    assert all(g["state"] == "EXECUTE" and g["execute"] for g in selected["groups"])
    owner = next(g for g in selected["groups"] if g["group"] == "bq-01b")
    assert owner["binding"]["argv"] == ["python", "-m", "pytest", ADDITIONS[3], "-q", "-n", "12"]
    assert owner["binding"]["onboarding_data_only"]


@pytest.mark.parametrize("mutate", ["reown", "unregistered_consumer", "outside_scope", "duplicate_source", "extra_argv", "wrong_ac_type"])
def test_owner_admission_rejects_unauthorized_paths_without_editing_control(mutate, tmp_path):
    from ai_commerce.engineering.impact_v21 import validate_component_onboarding_manifest
    from ai_commerce.engineering.minimum_test import TestGroup, approved_groups
    # Shape/ownership errors reject before byte admission; never write a
    # protected or bogus source/manifest to exercise these refusal paths.
    data = json.loads((ROOT / ADDITIONS[4]).read_bytes())
    legacy = json.loads((ROOT / "work_items/mt01-components/bq-01a.json").read_bytes())
    existing = (*approved_groups(), TestGroup(legacy["component"], tuple(legacy["tests"]), tuple(legacy["acs"]),
        tuple(legacy["sources"]), tuple(legacy["consumers"]), tuple(legacy["contracts"])))
    changed = list(ADDITIONS)
    if mutate == "reown":
        data["sources"].append("src/ai_commerce/creative_quality/behavioral.py")
    elif mutate == "unregistered_consumer":
        data["consumers"].append("src/ai_commerce/no_owner.py")
    elif mutate == "outside_scope":
        changed.append("src/ai_commerce/creative/briefs.py")
    elif mutate == "duplicate_source":
        data["sources"].append(data["sources"][0])
    elif mutate == "extra_argv":
        data["argv"] = ["publish"]
    else:
        data["acs"] = [True]
    reasons = {"reown": "ONBOARD_REOWN_OR_NOT_ADDED", "unregistered_consumer": "ONBOARD_CONSUMER_UNPROVEN",
        "outside_scope": "ONBOARD_CHANGED_OUT_OF_SCOPE", "duplicate_source": "ONBOARD_INVALID_SOURCES",
        "extra_argv": "ONBOARD_MANIFEST_SHAPE", "wrong_ac_type": "ONBOARD_INVALID_ACS"}
    with pytest.raises(ValueError, match=reasons[mutate]):
        validate_component_onboarding_manifest(data, manifest_path=ADDITIONS[4], changed_paths=changed,
            base_paths=["src/ai_commerce/creative_quality/behavioral.py"], existing_groups=existing,
            allowed_paths=ADDITIONS, root=tmp_path)
