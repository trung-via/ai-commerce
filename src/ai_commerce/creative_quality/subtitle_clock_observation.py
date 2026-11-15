"""B1.3-b: bounded native SRT clock inspection against current PCM WAV bytes.

SRT timestamps + measured WAV duration are technical facts only. Caption text
does NOT prove spoken words, Vietnamese pronunciation, semantic correctness,
subtitle/audio sync, asset rights or any consent to use a voice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import json
import re
import struct

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative_quality import audio_observation as pcm
from ai_commerce.media import files as media_files
from ai_commerce.production_runtime.contracts import canonical_bytes

SCHEMA = "B13SrtTimingObservation.v1"
MAX_SRT_BYTES = 64 * 1024
MAX_CUES = 64
MAX_CUE_TEXT = 1200
SHA = re.compile(r"[0-9a-f]{64}\Z")
CLOCK = re.compile(r"(\d{2}):([0-5]\d):([0-5]\d),(\d{3})\Z")
ARROW = re.compile(r"(\d{2}:[0-5]\d:[0-5]\d,\d{3}) --> (\d{2}:[0-5]\d:[0-5]\d,\d{3})\Z")

UNKNOWN = {
    "speech_caption_alignment": "NOT_MEASURED",
    "vietnamese_pronunciation": "NOT_MEASURED",
    "spoken_text_transcript": "NOT_MEASURED",
    "speaker_identity": "NOT_MEASURED",
    "lip_sync": "NOT_MEASURED",
    "subtitle_visual_render": "NOT_MEASURED",
    "source_language": "NOT_VERIFIED",
    "caption_rights": "UNPROVEN",
    "voice_consent": "UNPROVEN",
    "media_provider_license": "UNPROVEN",
    "external_effect_authority": "NONE",
}
PACKET_FIELDS = {
    "schema_version", "status", "pcm_ref", "pcm_sha256",
    "pcm_observation_sha256", "srt_ref", "srt_sha256", "timing",
    "unobserved", "authority", "record_sha256",
}


class SrtObservationHold(ValueError):
    """Finite redacted reason codes, never a file path or raw subtitle text."""


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise SrtObservationHold(code)


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _clock(value: str) -> int:
    matched = CLOCK.fullmatch(value)
    _need(matched is not None, "SRT_TIMESTAMP_INVALID")
    hours, minutes, seconds, milliseconds = map(int, matched.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _parse(raw: bytes, *, frames: int, rate: int) -> list[dict]:
    try:
        text = raw.decode("utf-8-sig", "strict")
    except UnicodeError:
        raise SrtObservationHold("SRT_UTF8_INVALID") from None
    _need(text != "" and not any(
        ord(char) < 32 and char not in "\r\n" or ord(char) == 127
        for char in text
    ), "SRT_TEXT_CONTROL_INVALID")
    text = text.replace("\r\n", "\n")
    _need("\r" not in text, "SRT_LINE_ENDINGS_INVALID")
    blocks = re.split(r"\n{2,}", text.strip("\n"))
    _need(1 <= len(blocks) <= MAX_CUES, "SRT_CUE_COUNT_INVALID")
    cues = []
    previous_end = 0
    for ordinal, block in enumerate(blocks, 1):
        lines = block.split("\n")
        _need(len(lines) >= 3 and lines[0] == str(ordinal),
              "SRT_CUE_SEQUENCE_INVALID")
        m = ARROW.fullmatch(lines[1])
        _need(m is not None, "SRT_CUE_WINDOW_INVALID")
        start, end = _clock(m.group(1)), _clock(m.group(2))
        _need(previous_end <= start < end, "SRT_CUE_OVERLAP_OR_ZERO")
        # Exact integer comparison, not caller-declared video duration.
        _need(end * rate <= frames * 1000, "SRT_EXCEEDS_MEASURED_PCM_DURATION")
        caption = "\n".join(lines[2:])
        _need(0 < len(caption) <= MAX_CUE_TEXT
              and any(line.strip() for line in lines[2:]),
              "SRT_CUE_TEXT_INVALID")
        cues.append({
            "ordinal": ordinal,
            "start_ms": start,
            "end_ms": end,
            "text_codepoints": len(caption),
            "text_sha256": _sha(caption.encode("utf-8")),
        })
        previous_end = end
    return cues


def _current_pcm(audio_packet: bytes, root: Path,
                 wav_ref: str, wav_sha256: str) -> dict:
    try:
        result = pcm.recheck_local_pcm_wav(
            audio_packet, root, wav_ref, wav_sha256
        )
        data = result.to_dict()
        _need(data["asset_ref"] == wav_ref
              and data["asset_sha256"] == wav_sha256,
              "SRT_PCM_BINDING_INVALID")
        return data["observed"]
    except (pcm.AudioObservationHold, ValueError, KeyError, TypeError):
        raise SrtObservationHold("SRT_PCM_CURRENT_RECHECK_HOLD") from None


def _read_srt(root: Path, ref: str, digest: str) -> bytes:
    _need(type(ref) is str and type(digest) is str
          and bool(SHA.fullmatch(digest)),
          "SRT_SOURCE_REQUEST_INVALID")
    try:
        relative = a13._relative(ref)
    except Exception:
        raise SrtObservationHold("SRT_SOURCE_REF_INVALID") from None
    try:
        with media_files.open_regular(root.joinpath(*relative.parts)) as source:
            data = source.read(MAX_SRT_BYTES + 1)
            _need(0 < len(data) <= MAX_SRT_BYTES, "SRT_SOURCE_SIZE_LIMIT")
            _need(_sha(data) == digest, "SRT_SOURCE_SHA_MISMATCH")
            source.seek(0)
            _need(source.read(MAX_SRT_BYTES + 1) == data,
                  "SRT_SOURCE_CHANGED_DURING_READ")
            return data
    except SrtObservationHold:
        raise
    except Exception:
        raise SrtObservationHold("SRT_SOURCE_UNAVAILABLE_OR_UNSAFE") from None


@dataclass(frozen=True, slots=True)
class SrtTimingObservation:
    raw: bytes = field(repr=False)

    def __post_init__(self):
        try:
            _need(type(self.raw) is bytes and 0 < len(self.raw) <= 32768,
                  "SRT_PACKET_BOUNDS")
            data = json.loads(self.raw)
            _need(type(data) is dict and set(data) == PACKET_FIELDS
                  and canonical_bytes(data) == self.raw,
                  "SRT_PACKET_CANONICAL")
            _need(data["schema_version"] == SCHEMA
                  and data["status"] == "LOCAL_CLOCK_BOUNDS_ONLY"
                  and data["authority"] == "NO_EXTERNAL_EFFECT_OR_CONSENT"
                  and data["unobserved"] == UNKNOWN,
                  "SRT_PACKET_SCOPE")
            _need(type(data["timing"]) is dict
                  and set(data["timing"]) == {
                      "state", "audio_duration_fraction_seconds", "cue_count",
                      "cues", "comparison"
                  }, "SRT_PACKET_TIMING")
            _need(type(data["timing"]["cues"]) is list
                  and type(data["timing"]["cue_count"]) is int
                  and data["timing"]["cue_count"] == len(data["timing"]["cues"])
                  and 0 <= len(data["timing"]["cues"]) <= MAX_CUES,
                  "SRT_PACKET_CUES")
            body = {k: v for k, v in data.items() if k != "record_sha256"}
            _need(data["record_sha256"] == _sha(canonical_bytes(body)),
                  "SRT_PACKET_HASH")
        except Exception:
            raise SrtObservationHold("SRT_PACKET_INVALID") from None

    def to_dict(self) -> dict:
        return json.loads(self.raw)


def inspect_local_srt_clock(
    root: Path, audio_packet: bytes, wav_ref: str, wav_sha256: str, *,
    srt_ref: str | None = None, srt_sha256: str | None = None,
) -> SrtTimingObservation:
    """Use independently pinned local WAV and optional subtitle file.

    The result is local technical bounding, NOT caption/spoken-word sync.
    """
    _need(isinstance(root, Path) and root.is_absolute()
          and type(audio_packet) is bytes
          and type(wav_ref) is str and type(wav_sha256) is str
          and bool(SHA.fullmatch(wav_sha256)),
          "SRT_REQUEST_INVALID")
    _need((srt_ref is None) == (srt_sha256 is None),
          "SRT_OPTIONAL_PAIR_REQUIRED")
    observed = _current_pcm(audio_packet, root, wav_ref, wav_sha256)
    if srt_ref is None:
        cues = []
        state = "NO_SUBTITLE_SUPPLIED"
    else:
        cues = _parse(
            _read_srt(root, srt_ref, srt_sha256),
            frames=observed["frame_count"],
            rate=observed["sample_rate_hz"],
        )
        state = "TIMESTAMPS_WITHIN_PCM_DURATION"
    # Re-open after subtitle read to catch ordinary concurrent WAV mutation.
    _need(_current_pcm(audio_packet, root, wav_ref, wav_sha256) == observed,
          "SRT_PCM_CHANGED_DURING_OBSERVATION")
    body = {
        "schema_version": SCHEMA,
        "status": "LOCAL_CLOCK_BOUNDS_ONLY",
        "pcm_ref": wav_ref, "pcm_sha256": wav_sha256,
        "pcm_observation_sha256": _sha(audio_packet),
        "srt_ref": srt_ref, "srt_sha256": srt_sha256,
        "timing": {
            "state": state,
            "audio_duration_fraction_seconds": observed["duration_fraction_seconds"],
            "cue_count": len(cues),
            "cues": cues,
            "comparison": "CLOCK_BOUNDS_NOT_AUDIO_SPEECH_SYNC",
        },
        "unobserved": UNKNOWN,
        "authority": "NO_EXTERNAL_EFFECT_OR_CONSENT",
    }
    return SrtTimingObservation(canonical_bytes({
        **body, "record_sha256": _sha(canonical_bytes(body))
    }))


def recheck_local_srt_clock(
    sealed: bytes, root: Path, audio_packet: bytes,
    wav_ref: str, wav_sha256: str, *,
    srt_ref: str | None = None, srt_sha256: str | None = None,
) -> SrtTimingObservation:
    """Self-sealing a packet cannot replace a fresh native source replay."""
    prior = SrtTimingObservation(sealed)
    current = inspect_local_srt_clock(
        root, audio_packet, wav_ref, wav_sha256,
        srt_ref=srt_ref, srt_sha256=srt_sha256
    )
    _need(prior.raw == current.raw, "SRT_CURRENT_SOURCE_REPLAY_REQUIRED")
    return current


# Separate wire contract: neither B1.3-a nor B1.3-b V1 gains any fields.
ACTIVITY_SCHEMA = "B13SrtPcmActivityObservation.v1"
ACTIVITY_UNKNOWN = {
    **UNKNOWN,
    "speech_activity": "NOT_MEASURED",
    "music_voice_rights": "UNPROVEN",
    "provider_entitlement": "UNPROVEN",
    "chatgpt_authorization": "NONE",
    "publication_authority": "NONE",
}
ACTIVITY_FIELDS = {
    "schema_version", "status", "pcm_ref", "pcm_sha256",
    "pcm_observation_sha256", "srt_ref", "srt_sha256",
    "srt_timing_observation_sha256", "measurement", "unobserved",
    "handoff", "authority", "record_sha256",
}
MEASUREMENT_FIELDS = {
    "state", "pcm_state", "sample_rate_hz", "channels", "frame_count",
    "sample_count", "audio_duration_fraction_seconds", "cue_count", "windows",
    "covered_frame_count", "uncovered_frame_count", "frame_selection",
    "comparison",
}
WINDOW_FIELDS = {
    "ordinal", "start_ms", "end_ms", "start_frame", "end_frame",
    "frame_count", "sample_count", "nonzero_sample_count", "peak_absolute_pcm",
    "sum_squared_pcm", "activity", "boundary_coverage",
}
FRAME_SELECTION = "FRAME_START_IN_HALF_OPEN_CUE_CEIL_BOTH_BOUNDS"
ACTIVITY_COMPARISON = "PCM_ACTIVITY_NOT_SPEECH_OR_CAPTION_ALIGNMENT"


def _activity_int(value, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _validate_activity(data: dict) -> None:
    """Validate the entire new envelope; source authenticity still needs replay."""
    _need(set(data) == ACTIVITY_FIELDS
          and data["schema_version"] == ACTIVITY_SCHEMA
          and data["status"] == "LOCAL_PCM_ACTIVITY_ONLY"
          and data["unobserved"] == ACTIVITY_UNKNOWN
          and data["handoff"] == "B1_5_OFFLINE_WAIT_BRAIN_REVIEW_ONLY"
          and data["authority"] == "NO_EXTERNAL_EFFECT_OR_CONSENT",
          "SRT_PCM_ACTIVITY_SCOPE")
    a13._relative(data["pcm_ref"])
    for key in ("pcm_sha256", "pcm_observation_sha256",
                "srt_timing_observation_sha256"):
        _need(type(data[key]) is str and bool(SHA.fullmatch(data[key])),
              "SRT_PCM_ACTIVITY_BINDING")
    absent = data["srt_ref"] is None
    if absent:
        _need(data["srt_sha256"] is None, "SRT_PCM_ACTIVITY_BINDING")
    else:
        a13._relative(data["srt_ref"])
        _need(type(data["srt_sha256"]) is str
              and bool(SHA.fullmatch(data["srt_sha256"])),
              "SRT_PCM_ACTIVITY_BINDING")
    measured = data["measurement"]
    _need(type(measured) is dict and set(measured) == MEASUREMENT_FIELDS,
          "SRT_PCM_ACTIVITY_MEASUREMENT")
    rate, channels, frames = (measured[k] for k in (
        "sample_rate_hz", "channels", "frame_count"
    ))
    _need(_activity_int(rate, 8000, 96000)
          and _activity_int(channels, 1, 2)
          and _activity_int(frames, 0, min(rate * pcm.MAX_SECONDS,
                                         pcm.MAX_BYTES // (channels * 2)))
          and type(measured["sample_count"]) is int
          and measured["sample_count"] == frames * channels
          and measured["audio_duration_fraction_seconds"] == str(Fraction(frames, rate))
          and measured["frame_selection"] == FRAME_SELECTION
          and measured["comparison"] == ACTIVITY_COMPARISON,
          "SRT_PCM_ACTIVITY_FORMAT")
    _need(measured["pcm_state"] in (
        ("ZERO_FRAME_PCM",) if frames == 0 else
        ("MEASURED_SILENCE", "MEASURED_NONZERO_PCM_NOT_SPEECH")
    ), "SRT_PCM_ACTIVITY_STATE")
    windows = measured["windows"]
    _need(type(windows) is list
          and _activity_int(measured["cue_count"], 0, MAX_CUES)
          and measured["cue_count"] == len(windows)
          and (len(windows) == 0 if absent else 1 <= len(windows) <= MAX_CUES)
          and measured["state"] == ("NO_SUBTITLE_SUPPLIED" if absent else
                                    "MEASURED_PCM_IN_BOUNDED_CUES"),
          "SRT_PCM_ACTIVITY_CUES")
    previous_end, covered = 0, 0
    for ordinal, window in enumerate(windows, 1):
        _need(type(window) is dict and set(window) == WINDOW_FIELDS,
              "SRT_PCM_ACTIVITY_WINDOW_FIELDS")
        start, end = window["start_ms"], window["end_ms"]
        _need(type(window["ordinal"]) is int and window["ordinal"] == ordinal
              and _activity_int(start, previous_end, pcm.MAX_SECONDS * 1000)
              and _activity_int(end, start + 1, pcm.MAX_SECONDS * 1000)
              and end * rate <= frames * 1000,
              "SRT_PCM_ACTIVITY_WINDOW_BOUNDS")
        first, last = (start * rate + 999) // 1000, (end * rate + 999) // 1000
        count = (last - first) * channels
        for key, expected in (("start_frame", first), ("end_frame", last),
                              ("frame_count", last - first), ("sample_count", count)):
            _need(type(window[key]) is int and window[key] == expected,
                  "SRT_PCM_ACTIVITY_WINDOW_COUNTS")
        nonzero, peak, energy = (window[k] for k in (
            "nonzero_sample_count", "peak_absolute_pcm", "sum_squared_pcm"
        ))
        _need(_activity_int(nonzero, 0, count)
              and _activity_int(peak, 0, 32768)
              and _activity_int(energy, 0, count * 32768 ** 2)
              and ((nonzero == 0 and peak == energy == 0) or
                   (nonzero > 0 and peak > 0
                    and peak ** 2 + nonzero - 1 <= energy <= nonzero * peak ** 2))
              and window["activity"] == ("MEASURED_SILENCE" if nonzero == 0 else
                                         "NONZERO_PCM_NOT_SPEECH")
              and window["boundary_coverage"] == "WITHIN_MEASURED_PCM_DURATION",
              "SRT_PCM_ACTIVITY_LEVELS")
        _need(measured["pcm_state"] != "MEASURED_SILENCE" or nonzero == 0,
              "SRT_PCM_ACTIVITY_STATE")
        covered += last - first
        previous_end = end
    _need(type(measured["covered_frame_count"]) is int
          and measured["covered_frame_count"] == covered
          and type(measured["uncovered_frame_count"]) is int
          and measured["uncovered_frame_count"] == frames - covered,
          "SRT_PCM_ACTIVITY_COVERAGE")


@dataclass(frozen=True, slots=True)
class SrtPcmActivityObservation:
    """Local envelope, not a signed attestation. Recheck before QA consumption."""

    raw: bytes = field(repr=False)

    def __post_init__(self):
        try:
            _need(type(self.raw) is bytes and 0 < len(self.raw) <= 32768,
                  "SRT_PCM_ACTIVITY_PACKET_BOUNDS")
            data = json.loads(self.raw)
            _need(type(data) is dict and canonical_bytes(data) == self.raw,
                  "SRT_PCM_ACTIVITY_CANONICAL")
            _validate_activity(data)
            body = {k: v for k, v in data.items() if k != "record_sha256"}
            _need(data["record_sha256"] == _sha(canonical_bytes(body)),
                  "SRT_PCM_ACTIVITY_HASH")
        except Exception:
            raise SrtObservationHold("SRT_PCM_ACTIVITY_PACKET_INVALID") from None

    def to_dict(self) -> dict:
        return json.loads(self.raw)


def inspect_local_srt_pcm_activity(
    root: Path, audio_packet: bytes, wav_ref: str, wav_sha256: str, *,
    srt_ref: str | None = None, srt_sha256: str | None = None,
    clock_packet: bytes | None = None,
) -> SrtPcmActivityObservation:
    """Measure actual interleaved PCM16 samples in the existing valid SRT windows.

    A frame is selected when its start time is in [cue start, cue end), using
    exact integer ceiling at both boundaries. Energy is the integer sum of
    squared sample amplitudes across all channels, not a speech detector.
    No-SRT (including zero-frame PCM) and measured silence remain valid.
    The bounded packet itself is the source-only B1.5 WAIT_BRAIN handoff;
    hashes identify bytes, not an issuer, rights, sync or execution authority.
    """
    if clock_packet is None:
        timing = inspect_local_srt_clock(
            root, audio_packet, wav_ref, wav_sha256,
            srt_ref=srt_ref, srt_sha256=srt_sha256,
        )
    else:
        timing = recheck_local_srt_clock(
            clock_packet, root, audio_packet, wav_ref, wav_sha256,
            srt_ref=srt_ref, srt_sha256=srt_sha256,
        )
    clock = timing.to_dict()
    try:
        observed, samples = pcm._read_local_pcm_wav(root, wav_ref, wav_sha256)
    except pcm.AudioObservationHold:
        raise SrtObservationHold("SRT_PCM_CURRENT_RECHECK_HOLD") from None
    rate, channels = observed["sample_rate_hz"], observed["channels"]
    windows = []
    for cue in clock["timing"]["cues"]:
        first = (cue["start_ms"] * rate + 999) // 1000
        last = (cue["end_ms"] * rate + 999) // 1000
        nonzero, peak, energy = 0, 0, 0
        for (value,) in struct.iter_unpack("<h", samples[first * channels * 2:last * channels * 2]):
            nonzero += value != 0
            peak = max(peak, abs(value))
            energy += value * value
        windows.append({
            "ordinal": cue["ordinal"], "start_ms": cue["start_ms"],
            "end_ms": cue["end_ms"], "start_frame": first, "end_frame": last,
            "frame_count": last - first, "sample_count": (last - first) * channels,
            "nonzero_sample_count": nonzero, "peak_absolute_pcm": peak,
            "sum_squared_pcm": energy,
            "activity": "MEASURED_SILENCE" if nonzero == 0 else "NONZERO_PCM_NOT_SPEECH",
            "boundary_coverage": "WITHIN_MEASURED_PCM_DURATION",
        })
    # Fence both sources again after measurement, using the existing V1 replay.
    recheck_local_srt_clock(
        timing.raw, root, audio_packet, wav_ref, wav_sha256,
        srt_ref=srt_ref, srt_sha256=srt_sha256,
    )
    frames = observed["frame_count"]
    covered = sum(window["frame_count"] for window in windows)
    body = {
        "schema_version": ACTIVITY_SCHEMA, "status": "LOCAL_PCM_ACTIVITY_ONLY",
        "pcm_ref": wav_ref, "pcm_sha256": wav_sha256,
        "pcm_observation_sha256": _sha(audio_packet),
        "srt_ref": srt_ref, "srt_sha256": srt_sha256,
        "srt_timing_observation_sha256": _sha(timing.raw),
        "measurement": {
            "state": "NO_SUBTITLE_SUPPLIED" if srt_ref is None else "MEASURED_PCM_IN_BOUNDED_CUES",
            "pcm_state": "ZERO_FRAME_PCM" if frames == 0 else (
                "MEASURED_SILENCE" if observed["all_samples_zero"] else
                "MEASURED_NONZERO_PCM_NOT_SPEECH"
            ),
            "sample_rate_hz": rate, "channels": channels,
            "frame_count": frames, "sample_count": observed["sample_count"],
            "audio_duration_fraction_seconds": observed["duration_fraction_seconds"],
            "cue_count": len(windows), "windows": windows,
            "covered_frame_count": covered, "uncovered_frame_count": frames - covered,
            "frame_selection": FRAME_SELECTION, "comparison": ACTIVITY_COMPARISON,
        },
        "unobserved": ACTIVITY_UNKNOWN,
        "handoff": "B1_5_OFFLINE_WAIT_BRAIN_REVIEW_ONLY",
        "authority": "NO_EXTERNAL_EFFECT_OR_CONSENT",
    }
    return SrtPcmActivityObservation(canonical_bytes({
        **body, "record_sha256": _sha(canonical_bytes(body)),
    }))


def recheck_local_srt_pcm_activity(
    sealed: bytes, root: Path, audio_packet: bytes,
    wav_ref: str, wav_sha256: str, *,
    srt_ref: str | None = None, srt_sha256: str | None = None,
    clock_packet: bytes | None = None,
) -> SrtPcmActivityObservation:
    """A self-resealed or stale activity claim cannot replace native replay."""
    prior = SrtPcmActivityObservation(sealed)
    current = inspect_local_srt_pcm_activity(
        root, audio_packet, wav_ref, wav_sha256,
        srt_ref=srt_ref, srt_sha256=srt_sha256, clock_packet=clock_packet,
    )
    _need(prior.raw == current.raw, "SRT_PCM_ACTIVITY_CURRENT_SOURCE_REPLAY_REQUIRED")
    return current
