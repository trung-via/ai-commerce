"""BQ-01b deterministic TEST_ONLY compiler; no execution or model selection.

The immutable BQ-01a profile stays closed. A separate digest-bound test fixture
adds finite voice/acoustic features and exact language tags. Neither profile
is a provider observation, benchmark, license or authority to synthesize audio.
"""

from __future__ import annotations

from typing import Any

from .behavioral import (
    QualityDraftError, _Draft, _canonical, _enum, _fields, _hash, _identifier,
    _sha, _text,
)
from .capability import ModelCapabilityProfileDraft, compile_capability_mock
from .causality import CausalShotPlanDraft
from .voice_intent import _language, _plan
from .acoustic_scene import AcousticSceneDraft


VOICE_CAPABILITY_KEYS = (
    "VN_SCRIPT", "VOICE_SEGMENT_TIMING", "STRESS", "TONE_DIRECTION", "PAUSES",
    "PACING", "INTONATION", "REGIONAL_DIRECTION", "SPEECH_SYNC",
    "ROOM_GEOMETRY", "ROOM_REVERB", "NOISE_CUES", "DISTANCE_CUES",
    "VOICEOVER_ENVIRONMENT", "ON_SCREEN_ENVIRONMENT", "SCENE_ENVIRONMENT",
    "AMBIENCE", "MUSIC", "SOUND_EFFECTS", "FRAME_TIMING", "TRIM_TIMING",
)
CAPABILITY_STATES = ("SUPPORTED", "UNSUPPORTED", "UNKNOWN")


class VoiceAcousticCapabilityDraft(_Draft):
    """VoiceAcousticCapabilityDraft.v1: a TEST_ONLY BQ-01a profile extension."""

    __slots__ = ()
    SCHEMA = "VoiceAcousticCapabilityDraft.v1"

    def _validate(self, value: Any) -> None:
        data = _fields(value, "schema_version execution_state provenance profile_id base_profile_digest metadata_source capabilities languages")
        _enum(data["schema_version"], (self.SCHEMA,), "schema_version")
        _enum(data["execution_state"], ("SIMULATED_ONLY",), "execution_state")
        _enum(data["provenance"], ("TEST_ONLY",), "provenance")
        _identifier(data["profile_id"], "profile_id")
        _sha(data["base_profile_digest"], "base_profile_digest")
        source = _fields(data["metadata_source"], "ref content sha256")
        _text(source["ref"], "metadata_source.ref", limit=240)
        _text(source["content"], "metadata_source.content")
        if _hash(source["content"].encode("utf-8")) != _sha(source["sha256"]):
            raise QualityDraftError("voice metadata content hash mismatch")
        if type(data["capabilities"]) is not dict or not set(data["capabilities"]) <= set(VOICE_CAPABILITY_KEYS):
            raise QualityDraftError("unknown voice capability key")
        if type(data["languages"]) is not dict or len(data["languages"]) > 16:
            raise QualityDraftError("language capability collection bound")
        for tag in data["languages"]:
            _language(tag)
        for status in (*data["capabilities"].values(), *data["languages"].values()):
            _enum(status, CAPABILITY_STATES, "TEST_ONLY capability status")


def _requirements(scene: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    required: set[str] = set()
    base: set[str] = set()
    unknown: set[str] = set()
    voice = scene["voice"]
    plan = voice["plan"]
    if voice["audio_mode"] in ("VOICEOVER", "ON_SCREEN_SPEECH"):
        required.update(("VN_SCRIPT", "VOICE_SEGMENT_TIMING"))
        if voice["language"] is None:
            unknown.add("voice.language")
        if not voice["segments"]:
            unknown.add("voice.segments")
        if plan["audio"]["lip_sync"] == "REQUIRED":
            required.add("SPEECH_SYNC")
            base.add("LIP_SYNC")
        for segment in voice["segments"]:
            direction = segment["direction"]
            for key, feature in (("stress", "STRESS"), ("tone", "TONE_DIRECTION"),
                                 ("pauses", "PAUSES"), ("pacing", "PACING"), ("intonation", "INTONATION")):
                if direction[key]:
                    required.add(feature)
                    base.add("DELIVERY")
                    if direction[key] == "UNKNOWN":
                        unknown.add("segment:" + segment["id"] + ":" + key)
            accent = direction["accent"]
            if accent is not None:
                required.add("REGIONAL_DIRECTION")
                base.add("ACCENT")
                if accent["region"] == "UNKNOWN" or accent["label"] == "UNKNOWN":
                    unknown.add("segment:" + segment["id"] + ":accent")
    if scene["sound_mode"] == "UNKNOWN":
        unknown.add("acoustic.sound_mode")
    elif scene["sound_mode"] == "SOUND_REQUESTED":
        if not scene["cues"]:
            unknown.add("acoustic.cues")
        base.add("ACOUSTICS")
        for env in scene["environments"]:
            required.update(("ROOM_GEOMETRY", "ROOM_REVERB", "NOISE_CUES", "DISTANCE_CUES"))
            required.add({"SCENE": "SCENE_ENVIRONMENT", "VO_NARRATION": "VOICEOVER_ENVIRONMENT",
                          "ON_SCREEN_SPEAKER": "ON_SCREEN_ENVIRONMENT"}[env["scope"]])
            for key in ("geometry", "room", "reverb", "noise", "distance"):
                if env[key] == "UNKNOWN":
                    unknown.add("environment:" + env["id"] + ":" + key)
        for cue in scene["cues"]:
            if cue["kind"] != "SPEECH":
                required.add({"AMBIENCE": "AMBIENCE", "MUSIC": "MUSIC", "SOUND_EFFECT": "SOUND_EFFECTS"}[cue["kind"]])
            if cue["frames"] is not None:
                required.add("FRAME_TIMING")
            if cue["asset_window"] is not None:
                required.add("TRIM_TIMING")
    return required, base, unknown


def _compile_data(
    scene: AcousticSceneDraft, profile: ModelCapabilityProfileDraft | None,
    voice_profile: VoiceAcousticCapabilityDraft | None,
) -> dict[str, Any]:
    acoustic = scene.to_dict()
    voice = acoustic["voice"]
    plan = CausalShotPlanDraft.from_dict(voice["plan"])
    legacy = compile_capability_mock(plan, profile, plan_digest=plan.digest,
                                     profile_digest=None if profile is None else profile.digest)
    previous = legacy.to_dict()
    if voice_profile is not None:
        if profile is None or voice_profile.to_dict()["base_profile_digest"] != profile.digest:
            raise QualityDraftError("voice capability fixture must bind its supplied BQ-01a profile")
    required, base_extra, unknown = _requirements(acoustic)
    base_caps = {} if profile is None else profile.to_dict()["capabilities"]
    extension = None if voice_profile is None else voice_profile.to_dict()
    caps = {} if extension is None else extension["capabilities"]
    checks = [{**item, "constraint": "BQ01a:" + item["constraint"]} for item in previous["checks"]]
    for prefix, features, metadata in (("BQ01b.base:", base_extra, base_caps), ("BQ01b:", required, caps)):
        for feature in sorted(features):
            status = metadata.get(feature, "UNKNOWN")
            checks.append({"constraint": prefix + feature, "status": status,
                           "rationale": "Supplied TEST_ONLY feature: " + status if feature in metadata
                           else "Missing explicit TEST_ONLY feature; UNKNOWN"})
    if voice["audio_mode"] in ("VOICEOVER", "ON_SCREEN_SPEECH") and voice["language"] is not None:
        tag = voice["language"]
        languages = {} if extension is None else extension["languages"]
        checks.append({"constraint": "BQ01b.language:" + tag, "status": languages.get(tag, "UNKNOWN"),
                       "rationale": "Exact caller language tag checked against TEST_ONLY metadata; no locale or dialect certification"})
    checks.extend({"constraint": "BQ01b.input:" + key, "status": "UNKNOWN",
                   "rationale": "Caller intent remains UNKNOWN; no measurement or inference"} for key in sorted(unknown))
    unsupported = sorted(c["constraint"] for c in checks if c["status"] == "UNSUPPORTED")
    unknowns = sorted(c["constraint"] for c in checks if c["status"] == "UNKNOWN")
    status = "HOLD_UNSUPPORTED" if unsupported else "HOLD_UNKNOWN" if unknowns else "PLAN_COMPATIBLE_SIMULATED"
    return {
        "schema_version": BQ01bVoiceCapabilityPreview.SCHEMA, "execution_state": "SIMULATED_ONLY",
        "acoustic_scene": acoustic, "acoustic_scene_digest": scene.digest,
        "voice_digest": acoustic["voice_digest"], "plan_digest": plan.digest,
        "behavioral_brief_digest": voice["behavioral_brief_digest"], "source_brief_digest": voice["source_brief_digest"],
        "profile": None if profile is None else profile.to_dict(), "profile_digest": None if profile is None else profile.digest,
        "voice_profile": extension, "voice_profile_digest": None if voice_profile is None else voice_profile.digest,
        "bq01a_preview_digest": legacy.digest, "bq01a_status": previous["status"],
        "status": status, "checks": checks, "unsupported": unsupported, "unknown": unknowns,
        "media_generation": "NOT_GENERATED", "media_inspection": "NOT_INSPECTED",
        "voice_naturalness": "NOT_MEASURED", "dialect_accuracy": "UNPROVEN", "speaker_identity": "UNPROVEN",
        "rights": "UNPROVEN", "costs": "UNKNOWN", "execution_authority": "NONE",
    }


class BQ01bVoiceCapabilityPreview(_Draft):
    """BQ01bVoiceCapabilityPreview.v1; replay recomputes every result and pin."""

    __slots__ = ()
    SCHEMA = "BQ01bVoiceCapabilityPreview.v1"

    def _validate(self, value: Any) -> None:
        data = _fields(value, "schema_version execution_state acoustic_scene acoustic_scene_digest voice_digest plan_digest behavioral_brief_digest source_brief_digest profile profile_digest voice_profile voice_profile_digest bq01a_preview_digest bq01a_status status checks unsupported unknown media_generation media_inspection voice_naturalness dialect_accuracy speaker_identity rights costs execution_authority")
        scene = AcousticSceneDraft.from_dict(data["acoustic_scene"])
        scene.verify_digest(data["acoustic_scene_digest"])
        profile = None if data["profile"] is None else ModelCapabilityProfileDraft.from_dict(data["profile"])
        if profile is not None:
            profile.verify_digest(data["profile_digest"])
        voice_profile = None if data["voice_profile"] is None else VoiceAcousticCapabilityDraft.from_dict(data["voice_profile"])
        if voice_profile is not None:
            voice_profile.verify_digest(data["voice_profile_digest"])
        if _canonical(data) != _canonical(_compile_data(scene, profile, voice_profile)):
            raise QualityDraftError("voice mock preview replay/pin mismatch")


def compile_voice_capability_mock(
    plan: CausalShotPlanDraft, scene: AcousticSceneDraft,
    profile: ModelCapabilityProfileDraft | None = None, voice_profile: VoiceAcousticCapabilityDraft | None = None, *,
    plan_digest: str, scene_digest: str, profile_digest: str | None = None, voice_profile_digest: str | None = None,
) -> BQ01bVoiceCapabilityPreview:
    """Check supplied metadata only; retain every legacy HOLD and its rationale."""
    valid_plan = _plan(plan, plan_digest)
    if not isinstance(scene, AcousticSceneDraft):
        raise QualityDraftError("expected AcousticSceneDraft")
    valid_scene = AcousticSceneDraft.from_json(scene.to_json())
    valid_scene.verify_digest(scene_digest)
    voice = valid_scene.to_dict()["voice"]
    if voice["plan_digest"] != valid_plan.digest or voice["plan"] != valid_plan.to_dict():
        raise QualityDraftError("voice/acoustic original plan mismatch")
    for supplied, kind, pin in ((profile, ModelCapabilityProfileDraft, profile_digest),
                                (voice_profile, VoiceAcousticCapabilityDraft, voice_profile_digest)):
        if supplied is None:
            if pin is not None:
                raise QualityDraftError("digest supplied without capability fixture")
        else:
            if not isinstance(supplied, kind):
                raise QualityDraftError("unexpected capability fixture type")
            valid = kind.from_json(supplied.to_json())
            valid.verify_digest(pin)
            if kind is ModelCapabilityProfileDraft:
                profile = valid
            else:
                voice_profile = valid
    return BQ01bVoiceCapabilityPreview.from_dict(_compile_data(valid_scene, profile, voice_profile))
