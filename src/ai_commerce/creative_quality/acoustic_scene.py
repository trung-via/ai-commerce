"""Bounded acoustic scene *intent*. No waveform, measurement or asset access.

SILENT in BQ-01a means no speech. Sound requires a separate explicit request
here; FULL_MUTE is valid. Geometry and room tokens are subjective planning
choices. VO_NARRATION and ON_SCREEN_SPEAKER never share an environment scope.
"""

from __future__ import annotations

from typing import Any

from .behavioral import (
    QualityDraftError, _Draft, _array, _enum, _fields, _identifier, _integer, _refs,
)
from .voice_intent import VietnameseVoiceIntentDraft, _interval, _pins


SOUND_MODES = ("FULL_MUTE", "SOUND_REQUESTED", "UNKNOWN")
CUE_KINDS = ("SPEECH", "AMBIENCE", "MUSIC", "SOUND_EFFECT")
ENVIRONMENT_CHOICES = {
    "scope": ("SCENE", "VO_NARRATION", "ON_SCREEN_SPEAKER"),
    "geometry": ("OPEN", "SMALL_ENCLOSED", "LARGE_ENCLOSED", "UNKNOWN"),
    "room": ("BOOTH", "ROOM", "HALL", "OUTDOOR", "UNKNOWN"),
    "reverb": ("DRY", "SHORT", "LONG", "UNKNOWN"),
    "noise": ("QUIET", "STEADY", "INTERMITTENT", "UNKNOWN"),
    "distance": ("CLOSE", "MID", "FAR", "UNKNOWN"),
}


def _frames(value: Any, cue: dict[str, Any], shot: dict[str, Any]) -> None:
    if value is None:
        return
    frames = _fields(value, "rate_num rate_den start end")
    _integer(frames["rate_num"], "frame rate numerator", 1, 120_000)
    _integer(frames["rate_den"], "frame rate denominator", 1, 1001)
    if not frames["rate_den"] <= frames["rate_num"] <= 120 * frames["rate_den"]:
        raise QualityDraftError("intended frame rate outside 1..120")
    _integer(frames["start"], "frame start", 0, 36_000)
    _integer(frames["end"], "frame end", 1, 36_000)
    if frames["end"] <= frames["start"]:
        raise QualityDraftError("frame interval length")
    # Half-open frame boundaries relative to the shot, checked by integer
    # arithmetic. A caller rate is not a measured media frame rate.
    for frame_key, time_key in (("start", "start_ms"), ("end", "end_ms")):
        if frames[frame_key] * 1000 * frames["rate_den"] != (cue[time_key] - shot["start_ms"]) * frames["rate_num"]:
            raise QualityDraftError("frame/time boundary mismatch")


class AcousticSceneDraft(_Draft):
    """AcousticSceneDraft.v1 with the entire original voice/source chain pinned."""

    __slots__ = ()
    SCHEMA = "AcousticSceneDraft.v1"

    def _validate(self, value: Any) -> None:
        data = _fields(value, "schema_version execution_state voice voice_digest plan_digest behavioral_brief_digest source_brief_digest sound_mode ambient_requested channels environments cues evidence rights")
        _enum(data["schema_version"], (self.SCHEMA,), "schema_version")
        _enum(data["execution_state"], ("SIMULATED_ONLY",), "execution_state")
        _enum(data["evidence"], ("UNVERIFIED_SCENE_INTENT",), "evidence")
        _enum(data["rights"], ("UNPROVEN",), "rights")
        voice = VietnameseVoiceIntentDraft.from_dict(data["voice"])
        voice.verify_digest(data["voice_digest"])
        speech = voice.to_dict()
        plan = speech["plan"]
        _pins(data, plan)
        if data["plan_digest"] != speech["plan_digest"]:
            raise QualityDraftError("acoustic plan digest mismatch")
        _enum(data["sound_mode"], SOUND_MODES, "sound_mode")
        if type(data["ambient_requested"]) is not bool:
            raise QualityDraftError("ambient_requested must be an explicit boolean")
        channels: dict[str, Any] = {}
        for channel in _array(data["channels"], "channels", maximum=16):
            _fields(channel, "id policy")
            identifier = _identifier(channel["id"], "channel.id")
            _enum(channel["policy"], ("SINGLE", "MIX"), "channel.policy")
            if identifier in channels:
                raise QualityDraftError("duplicate channel id")
            channels[identifier] = channel
        environments: dict[str, Any] = {}
        for env in _array(data["environments"], "environments", maximum=16):
            _fields(env, "id scope geometry room reverb noise distance evidence source_refs")
            identifier = _identifier(env["id"], "environment.id")
            if identifier in environments:
                raise QualityDraftError("duplicate environment id")
            environments[identifier] = env
            for key, choices in ENVIRONMENT_CHOICES.items():
                _enum(env[key], choices, "environment." + key)
            _enum(env["evidence"], ("SUBJECTIVE_DIRECTION",), "environment.evidence")
            _refs(env["source_refs"], {s["ref"] for s in plan["behavioral_brief"]["sources"]})
            if env["room"] == "OUTDOOR" and env["geometry"] not in ("OPEN", "UNKNOWN"):
                raise QualityDraftError("outdoor room contradicts enclosed geometry")
            if env["scope"] == "VO_NARRATION" and speech["audio_mode"] != "VOICEOVER":
                raise QualityDraftError("narration environment requires VO intent")
            if env["scope"] == "ON_SCREEN_SPEAKER" and speech["audio_mode"] != "ON_SCREEN_SPEECH":
                raise QualityDraftError("speaker environment requires on-screen intent")
        cues = _array(data["cues"], "cues", maximum=64)
        if data["sound_mode"] in ("FULL_MUTE", "UNKNOWN"):
            if channels or environments or cues or data["ambient_requested"]:
                raise QualityDraftError("mute/unknown sound cannot assert acoustic cues")
            if data["sound_mode"] == "FULL_MUTE" and speech["audio_mode"] != "SILENT":
                raise QualityDraftError("full mute contradicts voiced/unknown plan")
            return
        if speech["audio_mode"] == "UNKNOWN":
            raise QualityDraftError("unknown audio cannot become a known acoustic scene")
        segments = {s["id"]: s for s in speech["segments"]}
        shots = {s["id"]: s for s in plan["shots"]}
        known = {s["ref"] for s in plan["behavioral_brief"]["sources"]}
        ids: set[str] = set()
        used_environments: set[str] = set()
        used_channels: set[str] = set()
        speech_ids: set[str] = set()
        intervals: dict[str, list[tuple[int, int]]] = {}
        non_speech = 0
        for cue in cues:
            _fields(cue, "id shot_id start_ms end_ms frames kind layer channel_id environment_id voice_segment_ids source_refs asset_window rights evidence")
            identifier = _identifier(cue["id"], "cue.id")
            if identifier in ids:
                raise QualityDraftError("duplicate cue id")
            ids.add(identifier)
            shot = _interval(cue, shots)
            _frames(cue["frames"], cue, shot)
            _enum(cue["kind"], CUE_KINDS, "cue.kind")
            _enum(cue["layer"], ("FOREGROUND", "BACKGROUND"), "cue.layer")
            _enum(cue["rights"], ("UNPROVEN",), "cue.rights")
            _enum(cue["evidence"], ("UNVERIFIED_SCENE_INTENT",), "cue.evidence")
            _refs(cue["source_refs"], known)
            channel_id = _identifier(cue["channel_id"], "cue.channel_id")
            env_id = _identifier(cue["environment_id"], "cue.environment_id")
            if channel_id not in channels or env_id not in environments:
                raise QualityDraftError("dangling channel/environment reference")
            used_channels.add(channel_id)
            used_environments.add(env_id)
            env = environments[env_id]
            linked = _array(cue["voice_segment_ids"], "voice_segment_ids", maximum=1)
            if cue["kind"] == "SPEECH":
                if len(linked) != 1 or type(linked[0]) is not str or linked[0] not in segments or linked[0] in speech_ids:
                    raise QualityDraftError("dangling/duplicate speech segment binding")
                segment = segments[linked[0]]
                if any(cue[key] != segment[key] for key in ("shot_id", "start_ms", "end_ms")):
                    raise QualityDraftError("speech cue must match its voice segment interval")
                speech_ids.add(linked[0])
                scope = "VO_NARRATION" if speech["audio_mode"] == "VOICEOVER" else "ON_SCREEN_SPEAKER"
                if env["scope"] != scope or cue["layer"] != "FOREGROUND" or cue["asset_window"] is not None:
                    raise QualityDraftError("speech environment/layer/trim contradiction")
            else:
                non_speech += 1
                if linked or env["scope"] != "SCENE" or not data["ambient_requested"]:
                    raise QualityDraftError("non-speech cues require separately requested scene sound")
            window = cue["asset_window"]
            if window is not None:
                _fields(window, "source_duration_ms trim_start_ms trim_end_ms")
                _integer(window["source_duration_ms"], "source duration", 1, 300_000)
                _integer(window["trim_start_ms"], "trim start", 0, 300_000)
                _integer(window["trim_end_ms"], "trim end", 1, 300_000)
                if (not window["trim_start_ms"] < window["trim_end_ms"] <= window["source_duration_ms"]
                        or window["trim_end_ms"] - window["trim_start_ms"] != cue["end_ms"] - cue["start_ms"]):
                    raise QualityDraftError("impossible intended trim/length")
            intervals.setdefault(channel_id, []).append((cue["start_ms"], cue["end_ms"]))
        for identifier, spans in intervals.items():
            if channels[identifier]["policy"] == "SINGLE":
                ordered = sorted(spans)
                if any(after[0] < before[1] for before, after in zip(ordered, ordered[1:])):
                    raise QualityDraftError("single channel cue overlap/conflict")
        if speech_ids != set(segments):
            raise QualityDraftError("every voice segment needs its own speech environment cue")
        if used_channels != set(channels) or used_environments != set(environments):
            raise QualityDraftError("unbound acoustic fixture")
        if data["ambient_requested"] != bool(non_speech):
            raise QualityDraftError("explicit ambient request needs a non-speech cue")
        if speech["audio_mode"] == "SILENT" and not non_speech:
            raise QualityDraftError("silent sound request requires explicit ambient cues")


def build_acoustic_scene(
    voice: VietnameseVoiceIntentDraft, *, voice_digest: str, sound_mode: str,
    ambient_requested: bool = False, channels: list[dict[str, Any]] | None = None,
    environments: list[dict[str, Any]] | None = None, cues: list[dict[str, Any]] | None = None,
) -> AcousticSceneDraft:
    """Record supplied fixtures. No environment, sound or permission is inferred."""
    if not isinstance(voice, VietnameseVoiceIntentDraft):
        raise QualityDraftError("expected VietnameseVoiceIntentDraft")
    valid = VietnameseVoiceIntentDraft.from_json(voice.to_json())
    valid.verify_digest(voice_digest)
    data = valid.to_dict()
    return AcousticSceneDraft.from_dict({
        "schema_version": AcousticSceneDraft.SCHEMA, "execution_state": "SIMULATED_ONLY",
        "voice": data, "voice_digest": valid.digest, "plan_digest": data["plan_digest"],
        "behavioral_brief_digest": data["behavioral_brief_digest"], "source_brief_digest": data["source_brief_digest"],
        "sound_mode": sound_mode, "ambient_requested": ambient_requested,
        "channels": [] if channels is None else channels,
        "environments": [] if environments is None else environments, "cues": [] if cues is None else cues,
        "evidence": "UNVERIFIED_SCENE_INTENT", "rights": "UNPROVEN",
    })
