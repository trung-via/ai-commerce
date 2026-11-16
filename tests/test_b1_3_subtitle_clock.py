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


def activity_source(root, *, samples=None, rate=8000, channels=1, subtitle=SRT_VN):
    raw = wav((0,) * rate if samples is None else samples, rate=rate, channels=channels)
    (root / "native.wav").write_bytes(raw)
    digest = sha256(raw).hexdigest()
    inputs = {
        "audio_packet": pcm.inspect_local_pcm_wav(root, "native.wav", digest).raw,
        "wav_ref": "native.wav", "wav_sha256": digest,
        "srt_ref": None, "srt_sha256": None,
    }
    if subtitle is not None:
        (root / "native.srt").write_bytes(subtitle)
        inputs.update(srt_ref="native.srt", srt_sha256=sha256(subtitle).hexdigest())
    inputs["clock_packet"] = srt.inspect_local_srt_clock(root, **inputs).raw
    return inputs


def reseal(data):
    body = {k: v for k, v in data.items() if k != "record_sha256"}
    # Independent encoding also checks the shared canonical serializer's wire form.
    def encode(value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":")).encode("ascii")
    return encode({**body, "record_sha256": sha256(encode(body)).hexdigest()})


def test_activity_uses_actual_samples_in_two_exact_cue_windows(tmp_path, record_property):
    samples = [0] * 8000
    for index, value in {0: 10, 3599: -20, 3600: 30000, 3999: 123,
                         4000: -32768, 7199: 32767, 7200: 30001, 7999: -99}.items():
        samples[index] = value
    inputs = activity_source(tmp_path, samples=samples)
    result = srt.inspect_local_srt_pcm_activity(tmp_path, **inputs)
    data = result.to_dict()
    measured = data["measurement"]
    assert data["schema_version"] == "B13SrtPcmActivityObservation.v1"
    assert data["status"] == "LOCAL_PCM_ACTIVITY_ONLY"
    assert data["pcm_sha256"] == sha256((tmp_path / "native.wav").read_bytes()).hexdigest()
    assert data["srt_sha256"] == sha256(SRT_VN).hexdigest()
    assert data["pcm_observation_sha256"] == sha256(inputs["audio_packet"]).hexdigest()
    assert data["srt_timing_observation_sha256"] == sha256(inputs["clock_packet"]).hexdigest()
    assert measured["state"] == "MEASURED_PCM_IN_BOUNDED_CUES"
    assert measured["pcm_state"] == "MEASURED_NONZERO_PCM_NOT_SPEECH"
    assert measured["frame_count"] == measured["sample_count"] == 8000
    assert measured["covered_frame_count"] == 6800
    assert measured["uncovered_frame_count"] == 1200
    for window, first, last, selected in zip(
        measured["windows"], (0, 4000), (3600, 7200), (samples[:3600], samples[4000:7200])
    ):
        assert (window["start_frame"], window["end_frame"]) == (first, last)
        assert window["frame_count"] == window["sample_count"] == len(selected)
        assert window["nonzero_sample_count"] == sum(value != 0 for value in selected) == 2
        assert window["peak_absolute_pcm"] == max(map(abs, selected))
        assert window["sum_squared_pcm"] == sum(value * value for value in selected)
        assert window["activity"] == "NONZERO_PCM_NOT_SPEECH"
        assert window["boundary_coverage"] == "WITHIN_MEASURED_PCM_DURATION"
    assert measured["windows"][0]["sum_squared_pcm"] == 500
    assert measured["windows"][1]["sum_squared_pcm"] == 2147418113
    assert result.raw == reseal(data)
    assert srt.recheck_local_srt_pcm_activity(result.raw, tmp_path, **inputs).raw == result.raw
    without_clock = {k: v for k, v in inputs.items() if k != "clock_packet"}
    assert srt.inspect_local_srt_pcm_activity(tmp_path, **without_clock).raw == result.raw
    assert pcm.recheck_local_pcm_wav(
        inputs["audio_packet"], tmp_path, inputs["wav_ref"], inputs["wav_sha256"]
    ).raw == inputs["audio_packet"]
    assert srt.recheck_local_srt_clock(
        inputs["clock_packet"], tmp_path, **without_clock
    ).raw == inputs["clock_packet"]
    assert str(tmp_path) not in result.raw.decode("ascii")
    assert "Mưa" not in result.raw.decode("ascii") and b"text_sha256" not in result.raw
    assert data["handoff"] == "B1_5_OFFLINE_WAIT_BRAIN_REVIEW_ONLY"
    assert data["unobserved"]["speech_activity"] == "NOT_MEASURED"
    assert data["unobserved"]["speech_caption_alignment"] == "NOT_MEASURED"
    record_property("ac", "B1.3-b.current-pcm-clock-srt-bounds")


def test_activity_stereo_nonintegral_frame_boundaries_are_half_open(tmp_path):
    # 11025 Hz: [1ms, 3ms) selects frame start times 12..33, across both channels.
    caption = b"1\n00:00:00,001 --> 00:00:00,003\nprivate caption"
    samples = [0] * (11025 * 2)
    samples[11 * 2] = 30000  # Outside the start boundary.
    samples[12 * 2 + 1] = -7
    samples[33 * 2] = 9
    samples[34 * 2] = 32000  # Outside the end boundary.
    inputs = activity_source(tmp_path, samples=samples, rate=11025,
                             channels=2, subtitle=caption)
    measured = srt.inspect_local_srt_pcm_activity(tmp_path, **inputs).to_dict()["measurement"]
    window, = measured["windows"]
    assert measured["channels"] == 2
    assert measured["sample_count"] == 22050 and measured["frame_count"] == 11025
    assert (window["start_frame"], window["end_frame"]) == (12, 34)
    assert window["frame_count"] == 22 and window["sample_count"] == 44
    assert window["nonzero_sample_count"] == 2
    assert window["peak_absolute_pcm"] == 9 and window["sum_squared_pcm"] == 130
    assert measured["covered_frame_count"] == 22
    assert measured["uncovered_frame_count"] == 11003
    assert measured["frame_selection"] == "FRAME_START_IN_HALF_OPEN_CUE_CEIL_BOTH_BOUNDS"


@pytest.mark.parametrize("samples,subtitle,pcm_state,cues", [
    (None, SRT_VN, "MEASURED_SILENCE", 2),
    (None, None, "MEASURED_SILENCE", 0),
    ((), None, "ZERO_FRAME_PCM", 0),
    ((100,), None, "MEASURED_NONZERO_PCM_NOT_SPEECH", 0),
])
def test_activity_optional_and_silent_states_are_not_speech_verdicts(
    tmp_path, samples, subtitle, pcm_state, cues, record_property
):
    inputs = activity_source(tmp_path, samples=samples, subtitle=subtitle)
    result = srt.inspect_local_srt_pcm_activity(tmp_path, **inputs)
    measured = result.to_dict()["measurement"]
    assert measured["pcm_state"] == pcm_state
    assert measured["cue_count"] == cues
    assert measured["state"] == ("NO_SUBTITLE_SUPPLIED" if subtitle is None else
                                  "MEASURED_PCM_IN_BOUNDED_CUES")
    for window in measured["windows"]:
        assert window["activity"] == "MEASURED_SILENCE"
        assert window["nonzero_sample_count"] == window["peak_absolute_pcm"] == window["sum_squared_pcm"] == 0
    assert srt.recheck_local_srt_pcm_activity(result.raw, tmp_path, **inputs).raw == result.raw
    record_property("ac", "B1.3-b.optional-silent-no-inferred-sync")


def test_srt_v1_sealed_bytes_remain_identical_with_activity_extension(tmp_path):
    record, audio, digest, ref, sub_sha = inspect(tmp_path)
    captions = ["Mưa Sài Gòn — má, mà, mả, mã, mạ.", "Phụ đề là nội dung do người dùng cung cấp."]
    body = {
        "schema_version": "B13SrtTimingObservation.v1", "status": "LOCAL_CLOCK_BOUNDS_ONLY",
        "pcm_ref": "native.wav", "pcm_sha256": digest,
        "pcm_observation_sha256": sha256(audio).hexdigest(),
        "srt_ref": ref, "srt_sha256": sub_sha,
        "timing": {
            "state": "TIMESTAMPS_WITHIN_PCM_DURATION", "audio_duration_fraction_seconds": "1",
            "cue_count": 2,
            "cues": [{"ordinal": ordinal, "start_ms": start, "end_ms": end,
                      "text_codepoints": len(caption),
                      "text_sha256": sha256(caption.encode("utf-8")).hexdigest()}
                     for ordinal, start, end, caption in
                     zip((1, 2), (0, 500), (450, 900), captions)],
            "comparison": "CLOCK_BOUNDS_NOT_AUDIO_SPEECH_SYNC",
        },
        "unobserved": {
            "speech_caption_alignment": "NOT_MEASURED", "vietnamese_pronunciation": "NOT_MEASURED",
            "spoken_text_transcript": "NOT_MEASURED", "speaker_identity": "NOT_MEASURED",
            "lip_sync": "NOT_MEASURED", "subtitle_visual_render": "NOT_MEASURED",
            "source_language": "NOT_VERIFIED", "caption_rights": "UNPROVEN",
            "voice_consent": "UNPROVEN", "media_provider_license": "UNPROVEN",
            "external_effect_authority": "NONE",
        },
        "authority": "NO_EXTERNAL_EFFECT_OR_CONSENT",
    }
    expected = reseal(body)
    assert record.raw == expected
    srt.inspect_local_srt_pcm_activity(tmp_path, audio, "native.wav", digest,
                                      srt_ref=ref, srt_sha256=sub_sha, clock_packet=expected)
    assert srt.recheck_local_srt_clock(expected, tmp_path, audio, "native.wav", digest,
                                      srt_ref=ref, srt_sha256=sub_sha).raw == expected


def test_activity_fake_metrics_resealed_by_caller_require_native_replay(tmp_path):
    inputs = activity_source(tmp_path, samples=(10,) * 8000)
    record = srt.inspect_local_srt_pcm_activity(tmp_path, **inputs)
    forged = record.to_dict()
    forged["measurement"]["windows"][0].update(peak_absolute_pcm=11, sum_squared_pcm=3600 * 121)
    plausible = srt.SrtPcmActivityObservation(reseal(forged))
    with pytest.raises(srt.SrtObservationHold, match="SRT_PCM_ACTIVITY_CURRENT_SOURCE_REPLAY_REQUIRED"):
        srt.recheck_local_srt_pcm_activity(plausible.raw, tmp_path, **inputs)
    for key in ("audio_packet", "clock_packet"):
        changed = json.loads(inputs[key])
        if key == "audio_packet":
            changed["observed"]["peak_absolute_pcm"] = 11
            code = "SRT_PCM_CURRENT_RECHECK_HOLD"
        else:
            changed["timing"]["cues"][0]["end_ms"] = 449
            code = "SRT_CURRENT_SOURCE_REPLAY_REQUIRED"
        with pytest.raises(srt.SrtObservationHold, match=code):
            srt.inspect_local_srt_pcm_activity(tmp_path, **{**inputs, key: reseal(changed)})


@pytest.mark.parametrize("change", [
    "digest", "noncanonical", "scope", "sync", "extra_top", "extra_window", "bool_count", "bad_ref",
])
def test_activity_strict_contract_rejects_tamper_and_claims(tmp_path, change, record_property):
    inputs = activity_source(tmp_path)
    record = srt.inspect_local_srt_pcm_activity(tmp_path, **inputs)
    data = record.to_dict()
    if change == "digest":
        data["record_sha256"] = "0" * 64
        raw = srt.canonical_bytes(data)
    elif change == "noncanonical":
        raw = b" " + record.raw
    else:
        if change == "scope":
            data["authority"] = "PUBLISH_APPROVED"
        elif change == "sync":
            data["unobserved"]["speech_caption_alignment"] = "VERIFIED"
        elif change == "extra_top":
            data["provider_entitlement"] = "GRANTED"
        elif change == "extra_window":
            data["measurement"]["windows"][0]["transcript"] = "private caption"
        elif change == "bool_count":
            data["measurement"]["windows"][0]["nonzero_sample_count"] = False
        else:
            data["pcm_ref"] = "private\nfile.wav"
        raw = reseal(data)
    with pytest.raises(srt.SrtObservationHold, match="^SRT_PCM_ACTIVITY_PACKET_INVALID$"):
        srt.recheck_local_srt_pcm_activity(raw, tmp_path, **inputs)
    record_property("ac", "B1.3-b.no-provider-rights-or-sync-upgrade")


@pytest.mark.parametrize("target", ["native.wav", "native.srt"])
def test_activity_changes_between_source_observations_hold(tmp_path, monkeypatch, target):
    inputs = activity_source(tmp_path)
    read = pcm._read_local_pcm_wav
    reads = 0
    def change_after_sample_capture(*args, **kwargs):
        nonlocal reads
        observed, samples = read(*args, **kwargs)
        reads += 1
        if reads == 3:  # Two V1 clock fences, then the actual cue sample capture.
            (tmp_path / target).write_bytes(b"changed private source")
        return observed, samples
    monkeypatch.setattr(pcm, "_read_local_pcm_wav", change_after_sample_capture)
    with pytest.raises(srt.SrtObservationHold) as failure:
        srt.inspect_local_srt_pcm_activity(tmp_path, **inputs)
    assert str(failure.value) in {"SRT_PCM_CURRENT_RECHECK_HOLD", "SRT_SOURCE_SHA_MISMATCH"}
    assert reads >= 3
    assert str(tmp_path) not in str(failure.value)
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("caption", [
    b"", b"1\n00:00:00,000 --> 00:00:01,001\nprivate",
    b"1\n00:00:00,000 --> 00:00:00,500\nprivate\n\n2\n00:00:00,400 --> 00:00:00,600\nprivate",
    b"1\n00:00:00,000 --> 00:00:00,000\nprivate",
    b"1\n00:00:00,000 --> 00:00:00,500\n  ",
    b"1\n00:00:00,000 --> 00:00:00,500\n\xff",
    b"1\n00:00:00,000 --> 00:00:00,500\nprivate\x00",
    b"x" * (srt.MAX_SRT_BYTES + 1),
], ids=["empty", "over-duration", "overlap", "zero-length", "whitespace",
        "bad-utf8", "control-byte", "oversize"])
def test_activity_reuses_native_srt_negative_boundary(tmp_path, caption):
    packet, digest, ref, sub_sha = source(tmp_path, caption)
    with pytest.raises(srt.SrtObservationHold) as failure:
        srt.inspect_local_srt_pcm_activity(tmp_path, packet, "native.wav", digest,
                                          srt_ref=ref, srt_sha256=sub_sha)
    assert str(failure.value).startswith("SRT_")
    assert "private" not in str(failure.value) and str(tmp_path) not in str(failure.value)


def test_activity_rejects_zero_duration_cues_and_nonmatching_asset_source(tmp_path):
    inputs = activity_source(tmp_path, samples=(), subtitle=None)
    (tmp_path / "native.srt").write_bytes(SRT_VN)
    with pytest.raises(srt.SrtObservationHold, match="SRT_EXCEEDS_MEASURED_PCM_DURATION"):
        srt.inspect_local_srt_pcm_activity(
            tmp_path, **{**inputs, "clock_packet": None,
                         "srt_ref": "native.srt", "srt_sha256": sha256(SRT_VN).hexdigest()}
        )
    (tmp_path / "shadow.wav").write_bytes((tmp_path / "native.wav").read_bytes())
    with pytest.raises(srt.SrtObservationHold, match="SRT_PCM_CURRENT_RECHECK_HOLD"):
        srt.inspect_local_srt_pcm_activity(tmp_path, **{**inputs, "wav_ref": "shadow.wav"})


def test_activity_maximum_cues_are_bounded_and_no_provider_is_called(tmp_path, monkeypatch):
    caption = "\n\n".join(
        f"{i + 1}\n00:00:00,{i:03} --> 00:00:00,{i + 1:03}\nprivate"
        for i in range(srt.MAX_CUES)
    ).encode("utf-8")
    inputs = activity_source(tmp_path, subtitle=caption)
    def deny(*args, **kwargs):
        pytest.fail("activity observation attempted remote/process call")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(subprocess, "run", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)
    result = srt.inspect_local_srt_pcm_activity(tmp_path, **inputs)
    assert len(result.raw) <= 32768
    assert result.to_dict()["measurement"]["cue_count"] == 64
    assert all(value in {"NOT_MEASURED", "UNPROVEN", "NOT_VERIFIED", "NONE"}
               for value in result.to_dict()["unobserved"].values())
    with pytest.raises(srt.SrtObservationHold, match="SRT_PCM_ACTIVITY_PACKET_INVALID"):
        srt.SrtPcmActivityObservation(b"x" * 32769)
