"""B1.3-b: native SRT timestamps bounded by current B1.3-a PCM source."""
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from ai_commerce.creative_quality import audio_observation as pcm
from ai_commerce.creative_quality import subtitle_clock_observation as srt
from test_b1_3_audio_observation import wav

ROOT = Path(__file__).resolve().parents[1]
SRT_VN = (
    "1\r\n00:00:00,000 --> 00:00:00,450\r\n"
    "Mưa Sài Gòn — má, mà, mả, mã, mạ.\r\n\r\n"
    "2\r\n00:00:00,500 --> 00:00:00,900\r\n"
    "Phụ đề là nội dung do người dùng cung cấp.\r\n"
).encode("utf-8")


def source(root, subtitle=SRT_VN):
    pcm_bytes = wav((0,) * 8000)
    audio = root / "native.wav"
    audio.write_bytes(pcm_bytes)
    audio_hash = sha256(pcm_bytes).hexdigest()
    audio_packet = pcm.inspect_local_pcm_wav(root, audio.name, audio_hash).raw
    if subtitle is None:
        return audio_packet, audio_hash, None, None
    sub = root / "native.srt"
    sub.write_bytes(subtitle)
    subtitle_hash = sha256(subtitle).hexdigest()
    return audio_packet, audio_hash, sub.name, subtitle_hash


def inspect(root, caption=SRT_VN):
    packet, audio_hash, ref, digest = source(root, caption)
    record = srt.inspect_local_srt_clock(
        root, packet, "native.wav", audio_hash,
        srt_ref=ref, srt_sha256=digest
    )
    return record, packet, audio_hash, ref, digest


def test_real_pcm_and_utf8_subtitle_clock_limits(tmp_path, record_property):
    record, packet, audio_hash, ref, digest = inspect(tmp_path)
    data = record.to_dict()
    assert data["pcm_sha256"] == audio_hash
    assert data["pcm_observation_sha256"] == sha256(packet).hexdigest()
    assert data["srt_sha256"] == digest
    assert data["status"] == "LOCAL_CLOCK_BOUNDS_ONLY"
    assert data["timing"]["state"] == "TIMESTAMPS_WITHIN_PCM_DURATION"
    assert data["timing"]["cue_count"] == 2
    assert data["timing"]["audio_duration_fraction_seconds"] == "1"
    assert [(x["start_ms"], x["end_ms"]) for x in data["timing"]["cues"]] == [
        (0, 450), (500, 900)
    ]
    assert data["timing"]["cues"][0]["text_sha256"] == sha256(
        "Mưa Sài Gòn — má, mà, mả, mã, mạ.".encode("utf-8")
    ).hexdigest()
    assert "Mưa Sài Gòn" not in record.raw.decode("utf-8")
    assert data["timing"]["comparison"] == "CLOCK_BOUNDS_NOT_AUDIO_SPEECH_SYNC"
    assert data["unobserved"]["vietnamese_pronunciation"] == "NOT_MEASURED"
    assert data["unobserved"]["voice_consent"] == "UNPROVEN"
    assert data["authority"] == "NO_EXTERNAL_EFFECT_OR_CONSENT"
    assert srt.recheck_local_srt_clock(
        record.raw, tmp_path, packet, "native.wav", audio_hash,
        srt_ref=ref, srt_sha256=digest
    ).raw == record.raw
    record_property("ac", "B1.3-b.current-pcm-clock-srt-bounds")


def test_absent_optional_subtitles_are_not_silent_or_empty(tmp_path, record_property):
    record, packet, audio_hash, _, _ = inspect(tmp_path, caption=None)
    data = record.to_dict()
    assert data["srt_ref"] is None and data["srt_sha256"] is None
    assert data["timing"]["state"] == "NO_SUBTITLE_SUPPLIED"
    assert data["timing"]["cue_count"] == 0
    assert data["timing"]["cues"] == []
    assert data["unobserved"]["spoken_text_transcript"] == "NOT_MEASURED"
    assert srt.recheck_local_srt_clock(
        record.raw, tmp_path, packet, "native.wav", audio_hash
    ).raw == record.raw
    record_property("ac", "B1.3-b.optional-silent-no-inferred-sync")


@pytest.mark.parametrize("caption,error", [
    (b"0\n00:00:00,000 --> 00:00:00,100\nhello", "SRT_CUE_SEQUENCE_INVALID"),
    (b"1\n00:00:00,000 --> 00:00:01,001\nhello", "SRT_EXCEEDS_MEASURED_PCM_DURATION"),
    (b"1\n00:00:01,000 --> 00:00:00,500\nhello", "SRT_CUE_OVERLAP_OR_ZERO"),
    (b"1\n00:00:00,000 --> 00:00:00,500\nhello\n\n2\n00:00:00,400 --> 00:00:00,600\nother",
     "SRT_CUE_OVERLAP_OR_ZERO"),
    (b"1\n00:00:00,500 --> 00:00:00,500\nhello", "SRT_CUE_OVERLAP_OR_ZERO"),
    (b"1\n00:00:00,000 --> 00:00:00,500\n   ", "SRT_CUE_TEXT_INVALID"),
    (b"1\n00:00:00.000 --> 00:00:00.500\nbad", "SRT_CUE_WINDOW_INVALID"),
    (b"1\n00:00:00,000 --> 00:00:00,500\nfirst\n\n3\n00:00:00,500 --> 00:00:00,700\nbad",
     "SRT_CUE_SEQUENCE_INVALID"),
    (b"1\n00:00:00,000 --> 00:00:00,500\n\xff", "SRT_UTF8_INVALID"),
    (b"1\n00:00:00,000 --> 00:00:00,500\nbad\x00", "SRT_TEXT_CONTROL_INVALID"),
])
def test_bad_srt_never_becomes_a_measured_sync(tmp_path, caption, error):
    packet, audio_hash, ref, digest = source(tmp_path, caption)
    with pytest.raises(srt.SrtObservationHold, match=error):
        srt.inspect_local_srt_clock(
            tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref, srt_sha256=digest
        )


def test_missing_and_incorrect_caption_pin_hold(tmp_path):
    record, packet, audio_hash, ref, digest = inspect(tmp_path)
    with pytest.raises(srt.SrtObservationHold, match="SRT_SOURCE_SHA_MISMATCH"):
        srt.inspect_local_srt_clock(
            tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref, srt_sha256="0" * 64
        )
    (tmp_path / ref).write_bytes(b"changed")
    with pytest.raises(srt.SrtObservationHold, match="SRT_SOURCE_SHA_MISMATCH"):
        srt.recheck_local_srt_clock(
            record.raw, tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref, srt_sha256=digest
        )
    (tmp_path / ref).unlink()
    with pytest.raises(srt.SrtObservationHold, match="SRT_SOURCE_UNAVAILABLE_OR_UNSAFE"):
        srt.inspect_local_srt_clock(
            tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref, srt_sha256=digest
        )


def test_stale_pcm_rejected_even_when_caption_is_current(tmp_path):
    _, packet, audio_hash, ref, digest = inspect(tmp_path)
    (tmp_path / "native.wav").write_bytes(wav((0,) * 4000))
    with pytest.raises(srt.SrtObservationHold, match="SRT_PCM_CURRENT_RECHECK_HOLD"):
        srt.inspect_local_srt_clock(
            tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref, srt_sha256=digest
        )


@pytest.mark.parametrize("ref", [
    "../outside.srt", "/absolute.srt", "C:\\fake.srt", "NUL.srt",
    "sub/../../outside.srt", "native.srt:private",
])
def test_unsafe_caption_path_is_denied(tmp_path, ref):
    packet, audio_hash, _, _ = source(tmp_path, None)
    with pytest.raises(srt.SrtObservationHold, match="SRT_SOURCE_REF_INVALID"):
        srt.inspect_local_srt_clock(
            tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref, srt_sha256="0" * 64
        )


def test_unsafe_hardlink_caption_never_inspected(tmp_path):
    packet, audio_hash, ref, digest = source(tmp_path)
    try:
        os.link(tmp_path / ref, tmp_path / "alias.srt")
    except OSError:
        pytest.skip("NATIVE_HARDLINK_CREATION_UNPROVEN")
    with pytest.raises(srt.SrtObservationHold, match="SRT_SOURCE_UNAVAILABLE_OR_UNSAFE"):
        srt.inspect_local_srt_clock(
            tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref, srt_sha256=digest
        )


def test_native_symlink_caption_never_inspected_if_supported(tmp_path):
    packet, audio_hash, ref, digest = source(tmp_path)
    linked = tmp_path / "linked.srt"
    try:
        linked.symlink_to(tmp_path / ref)
    except OSError:
        pytest.skip("NATIVE_SYMLINK_CREATION_UNPROVEN")
    with pytest.raises(srt.SrtObservationHold, match="SRT_SOURCE_UNAVAILABLE_OR_UNSAFE"):
        srt.inspect_local_srt_clock(
            tmp_path, packet, "native.wav", audio_hash,
            srt_ref=linked.name, srt_sha256=digest
        )


def test_forged_rights_and_self_resealed_timing_cannot_replay(tmp_path, record_property):
    record, packet, audio_hash, ref, digest = inspect(tmp_path)
    tampered = record.to_dict()
    tampered["unobserved"]["voice_consent"] = "GRANTED"
    body = {k: v for k, v in tampered.items() if k != "record_sha256"}
    tampered["record_sha256"] = sha256(srt.canonical_bytes(body)).hexdigest()
    with pytest.raises(srt.SrtObservationHold, match="SRT_PACKET_INVALID"):
        srt.SrtTimingObservation(srt.canonical_bytes(tampered))
    sealed = record.to_dict()
    sealed["timing"]["cues"][0]["end_ms"] = 449
    body = {k: v for k, v in sealed.items() if k != "record_sha256"}
    sealed["record_sha256"] = sha256(srt.canonical_bytes(body)).hexdigest()
    forged = srt.SrtTimingObservation(srt.canonical_bytes(sealed))
    with pytest.raises(srt.SrtObservationHold, match="SRT_CURRENT_SOURCE_REPLAY_REQUIRED"):
        srt.recheck_local_srt_clock(
            forged.raw, tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref, srt_sha256=digest
        )
    record_property("ac", "B1.3-b.no-provider-rights-or-sync-upgrade")


def test_missing_pair_and_runtime_no_provider(tmp_path, monkeypatch):
    packet, audio_hash, ref, digest = source(tmp_path)
    with pytest.raises(srt.SrtObservationHold, match="SRT_OPTIONAL_PAIR_REQUIRED"):
        srt.inspect_local_srt_clock(
            tmp_path, packet, "native.wav", audio_hash,
            srt_ref=ref
        )
    def deny(*args, **kwargs):
        pytest.fail("B1.3-b source-only boundary attempted remote/process call")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)
    monkeypatch.setattr(subprocess, "run", deny)
    result = srt.inspect_local_srt_clock(
        tmp_path, packet, "native.wav", audio_hash,
        srt_ref=ref, srt_sha256=digest
    )
    assert result.to_dict()["unobserved"]["media_provider_license"] == "UNPROVEN"


def test_b1_3_b_exact_owner_and_real_consumers():
    registry = json.loads(
        (ROOT / "work_items/mt01-components/b1-3-subtitle.json").read_text("utf-8")
    )
    work = json.loads((ROOT / "work_items/B1_3_SUBTITLE_CLOCK_001.json").read_text("utf-8"))
    assert registry["component"] == "b1-3-subtitle"
    assert registry["sources"] == ["src/ai_commerce/creative_quality/subtitle_clock_observation.py"]
    assert registry["tests"] == ["tests/test_b1_3_subtitle_clock.py"]
    assert registry["acs"] == work["acceptance"]
    assert {
        "src/ai_commerce/creative_quality/audio_observation.py",
        "src/ai_commerce/media/files.py",
        "src/ai_commerce/creative_quality/voice_intent.py",
        "src/ai_commerce/creative_quality/acoustic_scene.py",
        "src/ai_commerce/affiliate_release/inspection.py",
    } <= set(registry["consumers"])
    assert len(work["allowed_paths"]) == 6
