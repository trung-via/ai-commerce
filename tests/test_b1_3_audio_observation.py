"""B1.3-a native PCM16 observation: bytes, NOT Vietnamese speech QA."""
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import struct
import subprocess

import pytest

from ai_commerce.creative_quality import audio_observation as obs
from test_bq_01b_voice_acoustics import make_plan, make_voice, make_scene

ROOT = Path(__file__).resolve().parents[1]


def wav(samples=(0, 32767, -32768), *, rate=8000, channels=1,
        format_id=1, bits=16, block_align=None, tail=b""):
    packed = struct.pack("<" + "h" * len(samples), *samples)
    align = channels * (bits // 8) if block_align is None else block_align
    fmt = struct.pack("<HHIIHH", format_id, channels, rate, rate * align, align, bits)
    chunks = (b"fmt " + struct.pack("<I", 16) + fmt
              + b"data" + struct.pack("<I", len(packed)) + packed + tail)
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


def put(root, raw, name="sound.wav"):
    path = root / name
    path.write_bytes(raw)
    return sha256(raw).hexdigest(), path


def inspect(root, raw, *, scene=None):
    digest, path = put(root, raw)
    record = obs.inspect_local_pcm_wav(root, path.name, digest, acoustic_intent=scene)
    return record, digest, path


def test_native_pcm16_signed_range_duration_and_current_replay(tmp_path, record_property):
    raw = wav()
    before = raw
    result, digest, path = inspect(tmp_path, raw)
    d = result.to_dict()
    assert d["asset_sha256"] == digest
    assert d["observed"] == {
        "format": "RIFF_WAVE_PCM16_LE", "channels": 1,
        "sample_rate_hz": 8000, "bits_per_sample": 16,
        "frame_count": 3, "sample_count": 3,
        "duration_fraction_seconds": "3/8000",
        "peak_absolute_pcm": 32768, "clipped_sample_count": 2,
        "all_samples_zero": False,
        "technical_comment": "PCM_LEVELS_NOT_PERCEPTUAL_QUALITY",
    }
    assert obs.recheck_local_pcm_wav(result.raw, tmp_path, "sound.wav", digest).raw == result.raw
    assert path.read_bytes() == before
    assert d["unobserved"]["vietnamese_pronunciation"] == "NOT_MEASURED"
    assert d["unobserved"]["provider_license"] == "UNPROVEN"
    assert d["authority"] == "NO_EXTERNAL_EFFECT_OR_CONSENT"
    record_property("ac", "B1.3-a.pcm-current-byte-metrics")
    record_property("asset_sha256", digest)


def test_intent_is_subjective_even_when_sound_is_silent(tmp_path, record_property):
    plan = make_plan(mode="SILENT")
    scene = make_scene(make_voice(plan))
    result, digest, path = inspect(tmp_path, wav((0, 0)), scene=scene)
    body = result.to_dict()
    assert body["intent"]["sound_mode"] == "FULL_MUTE"
    assert body["intent"]["binding"] == "SUBJECTIVE_INTENT_ONLY"
    assert body["intent"]["intent_sha256"] == scene.digest
    assert body["observed"]["all_samples_zero"] is True
    assert body["observed"]["technical_comment"] == "SILENCE_IS_NOT_A_DEFECT"
    assert body["unobserved"]["transcript"] == "NOT_MEASURED"
    assert body["unobserved"]["subtitle_timing"] == "NOT_MEASURED"
    assert obs.recheck_local_pcm_wav(result.raw, tmp_path, path.name, digest,
                                    acoustic_intent=scene).raw == result.raw
    # Zero frames from a present valid WAV is NOT an absent/uninspected source.
    empty, _, _ = inspect(tmp_path, wav(()), scene=scene)
    assert empty.to_dict()["observed"]["frame_count"] == 0
    assert empty.to_dict()["observed"]["technical_comment"] == "ZERO_FRAME_PCM"
    record_property("ac", "B1.3-a.silent-valid-no-inferred-vn-quality")


@pytest.mark.parametrize("invalid,code", [
    (b"no-file", "AUDIO_WAVE_CONTAINER_INVALID"),
    (wav() + b"TAIL", "AUDIO_WAVE_LENGTH_MISMATCH"),
    (wav()[:-2], "AUDIO_WAVE_LENGTH_MISMATCH"),
    (wav(format_id=3), "AUDIO_WAVE_FORMAT_UNSUPPORTED"),
    (wav(channels=3), "AUDIO_WAVE_FORMAT_UNSUPPORTED"),
    (wav(rate=7999), "AUDIO_WAVE_FORMAT_UNSUPPORTED"),
    (wav(bits=24), "AUDIO_WAVE_FORMAT_UNSUPPORTED"),
    (wav(block_align=3), "AUDIO_WAVE_FORMAT_UNSUPPORTED"),
])
def test_corrupt_or_unsupported_pcm_never_measures_or_infers(tmp_path, invalid, code):
    digest, path = put(tmp_path, invalid)
    with pytest.raises(obs.AudioObservationHold, match=code):
        obs.inspect_local_pcm_wav(tmp_path, path.name, digest)


def test_missing_digest_and_mutated_native_replay_fail_closed(tmp_path):
    record, digest, path = inspect(tmp_path, wav())
    with pytest.raises(obs.AudioObservationHold, match="AUDIO_EXPECTED_DIGEST_MISMATCH"):
        obs.inspect_local_pcm_wav(tmp_path, "sound.wav", "0" * 64)
    path.write_bytes(wav((99, 2)))
    with pytest.raises(obs.AudioObservationHold, match="AUDIO_EXPECTED_DIGEST_MISMATCH"):
        obs.recheck_local_pcm_wav(record.raw, tmp_path, "sound.wav", digest)
    path.unlink()
    with pytest.raises(obs.AudioObservationHold, match="AUDIO_SOURCE_UNAVAILABLE_OR_UNSAFE"):
        obs.inspect_local_pcm_wav(tmp_path, "sound.wav", digest)
    assert record.to_dict()["asset_sha256"] == digest


@pytest.mark.parametrize("ref", ["../outside.wav", "/absolute.wav", "folder\\file.wav",
                                  "NUL.wav", "sub/../../outside.wav", "file.wav:stream"])
def test_bad_or_special_reference_denied_before_media(tmp_path, ref):
    with pytest.raises(obs.AudioObservationHold, match="AUDIO_ASSET_REF_INVALID"):
        obs.inspect_local_pcm_wav(tmp_path, ref, "0" * 64)


def test_hardlinked_input_is_not_trusted_as_private_media(tmp_path):
    raw = wav()
    digest, path = put(tmp_path, raw)
    other = tmp_path / "hardlink.wav"
    try:
        os.link(path, other)
    except OSError:
        pytest.skip("NATIVE_HARDLINK_CREATION_UNPROVEN")
    with pytest.raises(obs.AudioObservationHold, match="AUDIO_SOURCE_UNAVAILABLE_OR_UNSAFE"):
        obs.inspect_local_pcm_wav(tmp_path, "sound.wav", digest)


def test_symlink_is_not_observed_if_host_can_create_it(tmp_path):
    raw = wav()
    digest, path = put(tmp_path, raw)
    link = tmp_path / "linked.wav"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("NATIVE_SYMLINK_CREATION_UNPROVEN")
    with pytest.raises(obs.AudioObservationHold, match="AUDIO_SOURCE_UNAVAILABLE_OR_UNSAFE"):
        obs.inspect_local_pcm_wav(tmp_path, link.name, digest)


def test_no_provider_rights_or_inference_escape(tmp_path, monkeypatch, record_property):
    def no_provider(*args, **kwargs):
        pytest.fail("offline PCM reader invoked provider or process")
    monkeypatch.setattr(socket, "create_connection", no_provider)
    monkeypatch.setattr(subprocess, "run", no_provider)
    monkeypatch.setattr(subprocess, "Popen", no_provider)
    record, digest, _ = inspect(tmp_path, wav())
    assert record.to_dict()["unobserved"]["voice_consent"] == "UNPROVEN"
    assert record.to_dict()["unobserved"]["lip_sync"] == "NOT_MEASURED"
    assert record.to_dict()["unobserved"]["ffprobe_streams"] == "NOT_INSPECTED"
    with pytest.raises(obs.AudioObservationHold, match="AUDIO_INTENT_TYPE_INVALID"):
        obs.inspect_local_pcm_wav(tmp_path, "sound.wav", digest,
                                  acoustic_intent={"voice_consent": "GRANTED"})
    forged = record.to_dict()
    forged["unobserved"]["voice_consent"] = "GRANTED"
    body = {k: v for k, v in forged.items() if k != "record_sha256"}
    forged["record_sha256"] = sha256(obs.canonical_bytes(body)).hexdigest()
    with pytest.raises(obs.AudioObservationHold, match="AUDIO_PACKET_INVALID"):
        obs.PcmAudioObservation(obs.canonical_bytes(forged))
    record_property("ac", "B1.3-a.no-provider-or-consent")


def test_registered_owner_and_actual_consumer_paths():
    registry = json.loads(
        (ROOT / "work_items/mt01-components/b1-3-audio.json").read_text(encoding="utf-8")
    )
    work = json.loads(
        (ROOT / "work_items/B1_3_AUDIO_OBSERVATION_001.json").read_text(encoding="utf-8")
    )
    assert registry["component"] == "b1-3-audio"
    assert registry["sources"] == ["src/ai_commerce/creative_quality/audio_observation.py"]
    assert registry["tests"] == ["tests/test_b1_3_audio_observation.py"]
    assert registry["acs"] == work["acceptance"]
    assert {"src/ai_commerce/creative_quality/acoustic_scene.py",
            "src/ai_commerce/creative_quality/voice_intent.py",
            "src/ai_commerce/creative_quality/voice_compile.py",
            "src/ai_commerce/media/files.py",
            "src/ai_commerce/affiliate_release/inspection.py"} <= set(registry["consumers"])
    assert set(work["allowed_paths"]) == {
        "src/ai_commerce/creative_quality/audio_observation.py",
        "tests/test_b1_3_audio_observation.py",
        "work_items/mt01-components/b1-3-audio.json",
        "work_items/B1_3_AUDIO_OBSERVATION_001.json",
        "docs/creative/B1_3_AUDIO_OBSERVATION_PROTOCOL.md",
        "docs/roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md",
    }
