"""Finite TEST_ONLY capability metadata and a deterministic, effect-free compiler.

This checks requested plan constraints against supplied metadata. It does not
choose a model, authenticate metadata, inspect assets or predict quality.
"""

from __future__ import annotations

from typing import Any

from .behavioral import QualityDraftError, _Draft, _enum, _fields, _hash, _identifier, _sha, _text
from .causality import CausalShotPlanDraft, VISUAL_FORMS


CAPABILITY_KEYS = (
    "SHOT_DAG", "OBJECT_CONTINUITY", "IDENTITY_CONTINUITY", "POSITION_CONTINUITY", "MOTION_CONTINUITY",
    *VISUAL_FORMS, "PHYSICAL_REALISM", "INTENTIONAL_FANTASY", "SILENT", "VOICEOVER", "ON_SCREEN_SPEECH",
    "VIETNAMESE_VOICE", "OTHER_LANGUAGE_VOICE", "LIP_SYNC", "ACOUSTICS", "ACCENT", "DELIVERY",
    "CAMERA", "LIGHTING", "CAUSAL_ACTION",
)


class ModelCapabilityProfileDraft(_Draft):
    """ModelCapabilityProfileDraft.v1: explicitly supplied TEST_ONLY fixture data.

    Missing keys mean UNKNOWN, never support. Content hashes bind supplied
    metadata; they do not certify a provider, voice right or benchmark.
    """

    __slots__ = ()
    SCHEMA = "ModelCapabilityProfileDraft.v1"

    def _validate(self, value: Any) -> None:
        data = _fields(value, "schema_version execution_state provenance profile_id metadata_source capabilities")
        _enum(data["schema_version"], (self.SCHEMA,), "schema_version")
        _enum(data["execution_state"], ("SIMULATED_ONLY",), "execution_state")
        _enum(data["provenance"], ("TEST_ONLY",), "provenance")
        _identifier(data["profile_id"], "profile_id")
        source = _fields(data["metadata_source"], "ref content sha256")
        _text(source["ref"], "metadata_source.ref", limit=240)
        _text(source["content"], "metadata_source.content")
        if _hash(source["content"].encode("utf-8")) != _sha(source["sha256"]):
            raise QualityDraftError("capability metadata source hash mismatch")
        if type(data["capabilities"]) is not dict or not set(data["capabilities"]) <= set(CAPABILITY_KEYS):
            raise QualityDraftError("extra/invalid capability keys")
        for status in data["capabilities"].values():
            _enum(status, ("SUPPORTED", "UNSUPPORTED", "UNKNOWN"), "capability status")


def _requirements(plan: dict[str, Any]) -> tuple[set[str], set[str]]:
    required = {"SHOT_DAG", plan["visual_form"]}
    unknown: set[str] = set()
    physics = plan["physics_intent"]
    if physics == "UNKNOWN":
        unknown.add("physics_intent")
    else:
        required.add("PHYSICAL_REALISM" if physics == "REAL_WORLD" else "INTENTIONAL_FANTASY")
    for shot in plan["shots"]:
        if shot["description"] == "UNKNOWN":
            unknown.add("shot:" + shot["id"] + ":description")
        if shot["objects"]:
            required.update(("OBJECT_CONTINUITY", "IDENTITY_CONTINUITY", "POSITION_CONTINUITY", "MOTION_CONTINUITY"))
        for obj in shot["objects"]:
            for key, value in obj.items():
                if value == "UNKNOWN" and key != "object_id":
                    unknown.add("shot:" + shot["id"] + ":" + obj["object_id"] + ":" + key)
        for constraint in shot["constraints"]:
            required.add(constraint["kind"])
            if constraint["state"] == "UNKNOWN" or constraint["intent"] == "UNKNOWN":
                unknown.add("shot:" + shot["id"] + ":" + constraint["kind"])
    audio = plan["audio"]
    if audio["mode"] == "UNKNOWN":
        unknown.add("audio.mode")
    else:
        required.add(audio["mode"])
    if audio["mode"] in ("VOICEOVER", "ON_SCREEN_SPEECH"):
        if audio["language"] == "UNKNOWN":
            unknown.add("audio.language")
        else:
            required.add("VIETNAMESE_VOICE" if audio["language"] == "vi" or audio["language"].startswith("vi-") else "OTHER_LANGUAGE_VOICE")
        if audio["text"] is None or audio["text"] == "UNKNOWN":
            unknown.add("audio.text")
        if audio["lip_sync"] == "UNKNOWN":
            unknown.add("audio.lip_sync")
        elif audio["lip_sync"] == "REQUIRED":
            required.add("LIP_SYNC")
        for key, feature in (("accent_intent", "ACCENT"), ("delivery_intent", "DELIVERY"), ("acoustic_intent", "ACOUSTICS")):
            if audio[key] is not None:
                if audio[key] == "UNKNOWN":
                    unknown.add("audio." + key)
                required.add(feature)
    return required, unknown


def _compile_data(plan: CausalShotPlanDraft, profile: ModelCapabilityProfileDraft | None) -> dict[str, Any]:
    data = plan.to_dict()
    required, unknown = _requirements(data)
    caps = {} if profile is None else profile.to_dict()["capabilities"]
    checks: list[dict[str, str]] = []
    if profile is None:
        unknown.add("capability_profile")
    for key in sorted(required):
        status = caps.get(key, "UNKNOWN")
        checks.append({"constraint": key, "status": status,
                       "rationale": "TEST_ONLY metadata: " + status if key in caps else "No supplied capability metadata; UNKNOWN"})
    checks.extend({"constraint": key, "status": "UNKNOWN", "rationale": "Caller plan constraint remains UNKNOWN"}
                  for key in sorted(unknown))
    unsupported = sorted(item["constraint"] for item in checks if item["status"] == "UNSUPPORTED")
    unknowns = sorted(item["constraint"] for item in checks if item["status"] == "UNKNOWN")
    status = "HOLD_UNSUPPORTED" if unsupported else "HOLD_UNKNOWN" if unknowns else "PLAN_COMPATIBLE_SIMULATED"
    return {
        "schema_version": "BQ01aPreview.v1", "execution_state": "SIMULATED_ONLY",
        "plan": data, "plan_digest": plan.digest,
        "profile": None if profile is None else profile.to_dict(), "profile_digest": None if profile is None else profile.digest,
        "behavioral_brief_digest": data["behavioral_brief_digest"], "status": status,
        "checks": checks, "unsupported": unsupported, "unknown": unknowns,
        "media_inspection": "NOT_INSPECTED", "perceptual_quality": "UNPROVEN", "behavioral_outcomes": "UNPROVEN",
        "rights": "UNPROVEN", "execution_authority": "NONE",
    }


class CapabilityPreviewDraft(_Draft):
    """Replayable simulated compatibility result with full input digest pins."""

    __slots__ = ()
    SCHEMA = "BQ01aPreview.v1"

    def _validate(self, value: Any) -> None:
        data = _fields(value, "schema_version execution_state plan plan_digest profile profile_digest behavioral_brief_digest status checks unsupported unknown media_inspection perceptual_quality behavioral_outcomes rights execution_authority")
        plan = CausalShotPlanDraft.from_dict(data["plan"])
        plan.verify_digest(data["plan_digest"])
        profile = None if data["profile"] is None else ModelCapabilityProfileDraft.from_dict(data["profile"])
        if profile is not None:
            profile.verify_digest(data["profile_digest"])
        if data != _compile_data(plan, profile):
            raise QualityDraftError("mock preview replay/pin mismatch")


def compile_capability_mock(
    plan: CausalShotPlanDraft, profile: ModelCapabilityProfileDraft | None = None, *,
    plan_digest: str, profile_digest: str | None = None,
) -> CapabilityPreviewDraft:
    """Check all declared requirements; absent optional audio is an explicit HOLD.

    Compatibility means only that TEST_ONLY metadata covers requested features.
    It is never PASS for media, rights, model performance or external effects.
    """
    if not isinstance(plan, CausalShotPlanDraft):
        raise QualityDraftError("expected CausalShotPlanDraft")
    valid = CausalShotPlanDraft.from_json(plan.to_json())
    valid.verify_digest(plan_digest)
    if profile is not None:
        if not isinstance(profile, ModelCapabilityProfileDraft):
            raise QualityDraftError("expected ModelCapabilityProfileDraft")
        profile = ModelCapabilityProfileDraft.from_json(profile.to_json())
        profile.verify_digest(profile_digest)
    elif profile_digest is not None:
        raise QualityDraftError("profile digest without capability profile")
    return CapabilityPreviewDraft.from_dict(_compile_data(valid, profile))
