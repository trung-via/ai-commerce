"""B1.3-a: bounded technical observation of an actual local PCM16 WAV.

Unlike BQ-01b's subjective Vietnamese voice/acoustic drafts, this reader
measures only file bytes. It does not recognize language, consent, speaker,
naturalness, sync, rights, provider entitlements or an external effect.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import struct

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.media import files as media_files
from ai_commerce.creative_quality.acoustic_scene import AcousticSceneDraft
from ai_commerce.production_runtime.contracts import canonical_bytes

SCHEMA = "B13PcmAudioObservation.v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 300
SHA = re.compile(r"[0-9a-f]{64}\Z")
UNKNOWN = {
    "vietnamese_pronunciation": "NOT_MEASURED",
    "dialect": "NOT_MEASURED",
    "transcript": "NOT_MEASURED",
    "lip_sync": "NOT_MEASURED",
    "speaker_identity": "NOT_MEASURED",
    "voice_naturalness": "NOT_MEASURED",
    "subtitle_timing": "NOT_MEASURED",
    "ffprobe_streams": "NOT_INSPECTED",
    "voice_consent": "UNPROVEN",
    "copyright_rights": "UNPROVEN",
    "provider_license": "UNPROVEN",
    "external_effect_authority": "NONE",
}
TOP = {
    "schema_version", "status", "asset_ref", "asset_sha256",
    "intent", "observed", "unobserved", "authority", "record_sha256",
}
OBSERVED = {
    "format", "channels", "sample_rate_hz", "bits_per_sample",
    "frame_count", "sample_count", "duration_fraction_seconds",
    "peak_absolute_pcm", "clipped_sample_count", "all_samples_zero",
    "technical_comment",
}


class AudioObservationHold(ValueError):
    """Finite failure codes; source filesystem paths and secrets never escape."""


def _need(condition: bool, code: str):
    if not condition:
        raise AudioObservationHold(code)


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _measure_pcm16(raw: bytes) -> dict:
    _need(len(raw) >= 44 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE",
          "AUDIO_WAVE_CONTAINER_INVALID")
    _need(struct.unpack_from("<I", raw, 4)[0] == len(raw) - 8,
          "AUDIO_WAVE_LENGTH_MISMATCH")
    offset, fmt, pcm = 12, None, None
    while offset < len(raw):
        _need(offset + 8 <= len(raw), "AUDIO_WAVE_CHUNK_TRUNCATED")
        tag, size = struct.unpack_from("<4sI", raw, offset)
        offset += 8
        end = offset + size
        _need(end + (size & 1) <= len(raw), "AUDIO_WAVE_CHUNK_TRUNCATED")
        if tag == b"fmt ":
            _need(fmt is None and size == 16, "AUDIO_WAVE_FORMAT_UNSUPPORTED")
            fmt = struct.unpack_from("<HHIIHH", raw, offset)
        elif tag == b"data":
            _need(pcm is None, "AUDIO_WAVE_DUPLICATE_DATA")
            pcm = raw[offset:end]
        offset = end + (size & 1)
    _need(fmt is not None and pcm is not None, "AUDIO_WAVE_MISSING_PCM")
    format_id, channels, sample_rate, byte_rate, align, bits = fmt
    _need(format_id == 1 and channels in (1, 2) and bits == 16
          and 8000 <= sample_rate <= 96000
          and align == channels * 2 and byte_rate == sample_rate * align,
          "AUDIO_WAVE_FORMAT_UNSUPPORTED")
    _need(len(pcm) % align == 0, "AUDIO_WAVE_FRAME_TRUNCATED")
    frames = len(pcm) // align
    _need(frames <= sample_rate * MAX_SECONDS, "AUDIO_DURATION_LIMIT")
    peak = 0
    clipped = 0
    any_nonzero = False
    for (value,) in struct.iter_unpack("<h", pcm):
        if value:
            any_nonzero = True
        peak = max(peak, abs(value))
        if value in (-32768, 32767):
            clipped += 1
    # These are sample statistics, NOT a judgement of a sound's suitability.
    from fractions import Fraction
    duration = Fraction(frames, sample_rate)
    return {
        "format": "RIFF_WAVE_PCM16_LE", "channels": channels,
        "sample_rate_hz": sample_rate, "bits_per_sample": bits,
        "frame_count": frames, "sample_count": frames * channels,
        "duration_fraction_seconds": str(duration),
        "peak_absolute_pcm": peak, "clipped_sample_count": clipped,
        "all_samples_zero": not any_nonzero,
        "technical_comment": "ZERO_FRAME_PCM" if frames == 0 else
            ("SILENCE_IS_NOT_A_DEFECT" if not any_nonzero else
             "PCM_LEVELS_NOT_PERCEPTUAL_QUALITY"),
    }


def _intent(acoustic_intent):
    if acoustic_intent is None:
        return {
            "binding": "NOT_SUPPLIED", "schema_version": None,
            "intent_sha256": None, "sound_mode": None,
            "comparison": "NO_INTENT_COMPARISON",
        }
    _need(type(acoustic_intent) is AcousticSceneDraft,
          "AUDIO_INTENT_TYPE_INVALID")
    try:
        verified = AcousticSceneDraft.from_json(acoustic_intent.to_json())
        verified.verify_digest(acoustic_intent.digest)
        data = verified.to_dict()
    except Exception:
        raise AudioObservationHold("AUDIO_INTENT_INVALID") from None
    return {
        "binding": "SUBJECTIVE_INTENT_ONLY", "schema_version": data["schema_version"],
        "intent_sha256": acoustic_intent.digest, "sound_mode": data["sound_mode"],
        "comparison": "TECHNICAL_MEASUREMENT_NOT_SPEECH_OR_ACOUSTIC_VERDICT",
    }


@dataclass(frozen=True, slots=True)
class PcmAudioObservation:
    raw: bytes = field(repr=False)

    def __post_init__(self):
        try:
            _need(type(self.raw) is bytes and 0 < len(self.raw) <= 8192,
                  "AUDIO_PACKET_BOUNDS")
            data = json.loads(self.raw)
            _need(type(data) is dict and set(data) == TOP,
                  "AUDIO_PACKET_FIELDS")
            _need(canonical_bytes(data) == self.raw
                  and data["schema_version"] == SCHEMA
                  and data["status"] == "LOCAL_TECHNICAL_OBSERVATION_ONLY"
                  and data["unobserved"] == UNKNOWN
                  and data["authority"] == "NO_EXTERNAL_EFFECT_OR_CONSENT",
                  "AUDIO_PACKET_CONTRACT")
            _need(type(data["observed"]) is dict
                  and set(data["observed"]) == OBSERVED
                  and type(data["intent"]) is dict,
                  "AUDIO_PACKET_SUBFIELDS")
            body = {k: v for k, v in data.items() if k != "record_sha256"}
            _need(data["record_sha256"] == _digest(canonical_bytes(body)),
                  "AUDIO_PACKET_HASH")
        except Exception:
            raise AudioObservationHold("AUDIO_PACKET_INVALID") from None

    def to_dict(self) -> dict:
        return json.loads(self.raw)


def inspect_local_pcm_wav(root: Path, asset_ref: str, expected_sha256: str,
                          *, acoustic_intent: AcousticSceneDraft | None = None
                          ) -> PcmAudioObservation:
    """Read the exact local WAV twice within one safe regular-file handle.

    A caller must pin SHA independently. A reread plus stat fence detects
    ordinary races; this is NOT an independently attested Windows share lock.
    """
    _need(isinstance(root, Path) and root.is_absolute()
          and type(asset_ref) is str and type(expected_sha256) is str
          and bool(SHA.fullmatch(expected_sha256)), "AUDIO_REQUEST_INVALID")
    try:
        relative = a13._relative(asset_ref)
    except Exception:
        raise AudioObservationHold("AUDIO_ASSET_REF_INVALID") from None
    intent = _intent(acoustic_intent)
    try:
        path = root.joinpath(*relative.parts)
        with media_files.open_regular(path) as source:
            _need(os.fstat(source.fileno()).st_size <= MAX_BYTES,
                  "AUDIO_SIZE_LIMIT")
            raw = source.read(MAX_BYTES + 1)
            _need(0 < len(raw) <= MAX_BYTES, "AUDIO_SIZE_LIMIT")
            _need(_digest(raw) == expected_sha256,
                  "AUDIO_EXPECTED_DIGEST_MISMATCH")
            measurements = _measure_pcm16(raw)
            source.seek(0)
            _need(source.read(MAX_BYTES + 1) == raw,
                  "AUDIO_SOURCE_CHANGED_DURING_READ")
    except AudioObservationHold:
        raise
    except Exception:
        raise AudioObservationHold("AUDIO_SOURCE_UNAVAILABLE_OR_UNSAFE") from None
    body = {
        "schema_version": SCHEMA,
        "status": "LOCAL_TECHNICAL_OBSERVATION_ONLY",
        "asset_ref": asset_ref,
        "asset_sha256": expected_sha256,
        "intent": intent, "observed": measurements,
        "unobserved": UNKNOWN,
        "authority": "NO_EXTERNAL_EFFECT_OR_CONSENT",
    }
    return PcmAudioObservation(canonical_bytes({
        **body, "record_sha256": _digest(canonical_bytes(body))
    }))


def recheck_local_pcm_wav(raw: bytes, root: Path, asset_ref: str,
                          expected_sha256: str, *,
                          acoustic_intent: AcousticSceneDraft | None = None
                          ) -> PcmAudioObservation:
    """A caller-sealed packet is not sufficient; re-open current native bytes."""
    supplied = PcmAudioObservation(raw)
    current = inspect_local_pcm_wav(
        root, asset_ref, expected_sha256, acoustic_intent=acoustic_intent
    )
    _need(current.raw == supplied.raw, "AUDIO_CURRENT_SOURCE_REPLAY_REQUIRED")
    return current
