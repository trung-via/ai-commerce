"""BQ-01b caller-authored Vietnamese direction, never recognized performance.

Only exact spans of the supplied BQ-01a script can become segments. Character
offsets count Python Unicode code points, including combining marks; no Unicode
normalization, pronunciation inference, translation or script generation occurs.
All times are intended absolute plan milliseconds, not media measurements.
"""

from __future__ import annotations

import re
from typing import Any

from .behavioral import (
    QualityDraftError, _Draft, _array, _enum, _fields, _identifier, _integer,
    _refs, _sha, _text,
)
from .causality import CausalShotPlanDraft


AUDIO_MODES = ("SILENT", "VOICEOVER", "ON_SCREEN_SPEECH", "UNKNOWN")
DIRECTION_STATUS = "SUBJECTIVE_DIRECTION"


def _language(value: Any) -> None:
    if type(value) is not str or not re.fullmatch(r"vi(?:-[A-Z]{2})?", value):
        raise QualityDraftError("expected vi or a caller-supplied vi-XX tag")


def _plan(value: Any, digest: Any) -> CausalShotPlanDraft:
    if not isinstance(value, CausalShotPlanDraft):
        raise QualityDraftError("expected CausalShotPlanDraft")
    valid = CausalShotPlanDraft.from_json(value.to_json())
    valid.verify_digest(digest)
    return valid


def _pins(data: dict[str, Any], plan: dict[str, Any]) -> None:
    brief = plan["behavioral_brief"]
    for key, expected in (("behavioral_brief_digest", plan["behavioral_brief_digest"]),
                          ("source_brief_digest", brief["source_brief_digest"])):
        if _sha(data[key], key) != expected:
            raise QualityDraftError(key + ": original source pin mismatch")


def _interval(item: dict[str, Any], shots: dict[str, Any]) -> dict[str, Any]:
    identifier = _identifier(item["shot_id"], "shot_id")
    if identifier not in shots:
        raise QualityDraftError("dangling shot reference")
    shot = shots[identifier]
    _integer(item["start_ms"], "start_ms", 0, 300_000)
    _integer(item["end_ms"], "end_ms", 1, 300_000)
    if not shot["start_ms"] <= item["start_ms"] < item["end_ms"] <= shot["end_ms"]:
        raise QualityDraftError("interval outside original shot")
    return shot


def _direction(value: Any, text: str, duration: int) -> None:
    data = _fields(value, "status stress tone pauses pacing intonation accent")
    _enum(data["status"], (DIRECTION_STATUS,), "direction.status")
    for key, choices in (
        ("tone", ("PRESERVE_SOURCE_TONES", "NEUTRAL", "WARM", "URGENT", "PLAYFUL", "SOMBER", "UNKNOWN")),
        ("pacing", ("SLOW", "MODERATE", "FAST", "UNKNOWN")),
        ("intonation", ("LEVEL", "RISING", "FALLING", "VARIED", "UNKNOWN")),
    ):
        if data[key] is not None:
            _enum(data[key], choices, "direction." + key)
    last_end = 0
    for stress in _array(data["stress"], "stress", maximum=8):
        _fields(stress, "start_char end_char")
        _integer(stress["start_char"], "stress.start_char", 0, len(text))
        _integer(stress["end_char"], "stress.end_char", 1, len(text))
        if not last_end <= stress["start_char"] < stress["end_char"]:
            raise QualityDraftError("stress span order/length")
        last_end = stress["end_char"]
    previous = -1
    pause_total = 0
    for pause in _array(data["pauses"], "pauses", maximum=16):
        _fields(pause, "after_char duration_ms")
        _integer(pause["after_char"], "pause.after_char", 1, len(text))
        _integer(pause["duration_ms"], "pause.duration_ms", 1, 300_000)
        if pause["after_char"] <= previous:
            raise QualityDraftError("pause positions must increase")
        previous = pause["after_char"]
        pause_total += pause["duration_ms"]
    if pause_total >= duration:
        raise QualityDraftError("pauses leave no intended speech interval")
    if data["accent"] is not None:
        accent = _fields(data["accent"], "region label status")
        _enum(accent["region"], ("NORTHERN", "CENTRAL", "SOUTHERN", "OTHER", "UNKNOWN"), "accent.region")
        _enum(accent["status"], (DIRECTION_STATUS,), "accent.status")
        _text(accent["label"], "caller accent label", limit=128)


class VietnameseVoiceIntentDraft(_Draft):
    """VietnameseVoiceIntentDraft.v1; source pins prove integrity, not rights.

    A null language is unresolved, never an inferred locale. An empty voiced
    segment list is an unresolved planning draft and makes the compiler HOLD.
    Region labels are caller choices, not verified dialect or speaker identity.
    """

    __slots__ = ()
    SCHEMA = "VietnameseVoiceIntentDraft.v1"

    def _validate(self, value: Any) -> None:
        data = _fields(value, "schema_version execution_state plan plan_digest behavioral_brief_digest source_brief_digest audio_mode language source_refs segments direction_status ownership rights speaker_identity")
        _enum(data["schema_version"], (self.SCHEMA,), "schema_version")
        _enum(data["execution_state"], ("SIMULATED_ONLY",), "execution_state")
        for key in ("ownership", "rights", "speaker_identity"):
            _enum(data[key], ("UNPROVEN",), key)
        _enum(data["direction_status"], (DIRECTION_STATUS,), "direction_status")
        plan = CausalShotPlanDraft.from_dict(data["plan"])
        plan.verify_digest(data["plan_digest"])
        source = plan.to_dict()
        _pins(data, source)
        audio = source["audio"]
        _enum(data["audio_mode"], AUDIO_MODES, "audio_mode")
        if data["audio_mode"] != audio["mode"]:
            raise QualityDraftError("audio mode must preserve original plan")
        if data["language"] is None:
            if audio["language"] != "UNKNOWN":
                raise QualityDraftError("cannot erase original language")
        else:
            _language(data["language"])
            if data["language"] != audio["language"]:
                raise QualityDraftError("cannot infer or replace original language")
        known = {item["ref"] for item in source["behavioral_brief"]["sources"]}
        _refs(data["source_refs"], known, minimum=0 if audio["mode"] in ("SILENT", "UNKNOWN") else 1)
        if set(data["source_refs"]) != set(audio["source_refs"]):
            raise QualityDraftError("voice refs must preserve original audio refs")
        segments = _array(data["segments"], "segments", maximum=64)
        if audio["mode"] in ("SILENT", "UNKNOWN") and segments:
            raise QualityDraftError("silent/unknown audio cannot invent speech")
        script = audio["text"]
        if segments and (script is None or script == "UNKNOWN"):
            raise QualityDraftError("segments require supplied script text")
        shots = {shot["id"]: shot for shot in source["shots"]}
        ids: set[str] = set()
        last_end = 0
        last_char = 0
        for segment in segments:
            _fields(segment, "id shot_id start_ms end_ms script_start script_end text source_refs direction")
            identifier = _identifier(segment["id"], "segment.id")
            if identifier in ids:
                raise QualityDraftError("duplicate voice segment id")
            ids.add(identifier)
            _interval(segment, shots)
            if segment["start_ms"] < last_end:
                raise QualityDraftError("voice segment time must be monotonic/nonoverlapping")
            last_end = segment["end_ms"]
            _integer(segment["script_start"], "script_start", 0, len(script))
            _integer(segment["script_end"], "script_end", 1, len(script))
            if not last_char <= segment["script_start"] < segment["script_end"]:
                raise QualityDraftError("script spans must increase without overlap")
            last_char = segment["script_end"]
            text = _text(segment["text"], "segment.text")
            if text != script[segment["script_start"]:segment["script_end"]]:
                raise QualityDraftError("script span must retain exact supplied Unicode")
            _refs(segment["source_refs"], set(data["source_refs"]))
            _direction(segment["direction"], text, segment["end_ms"] - segment["start_ms"])


def build_voice_intent(
    plan: CausalShotPlanDraft, *, plan_digest: str, segments: list[dict[str, Any]],
) -> VietnameseVoiceIntentDraft:
    """Bind explicit caller segments; never split, translate or write a script."""
    valid = _plan(plan, plan_digest)
    source = valid.to_dict()
    return VietnameseVoiceIntentDraft.from_dict({
        "schema_version": VietnameseVoiceIntentDraft.SCHEMA, "execution_state": "SIMULATED_ONLY",
        "plan": source, "plan_digest": valid.digest,
        "behavioral_brief_digest": source["behavioral_brief_digest"],
        "source_brief_digest": source["behavioral_brief"]["source_brief_digest"],
        "audio_mode": source["audio"]["mode"],
        "language": None if source["audio"]["language"] == "UNKNOWN" else source["audio"]["language"],
        "source_refs": source["audio"]["source_refs"], "segments": segments,
        "direction_status": DIRECTION_STATUS, "ownership": "UNPROVEN", "rights": "UNPROVEN",
        "speaker_identity": "UNPROVEN",
    })
