"""B1.3-b: bounded native SRT clock inspection against current PCM WAV bytes.

SRT timestamps + measured WAV duration are technical facts only. Caption text
does NOT prove spoken words, Vietnamese pronunciation, semantic correctness,
subtitle/audio sync, asset rights or any consent to use a voice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import json
import re

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
