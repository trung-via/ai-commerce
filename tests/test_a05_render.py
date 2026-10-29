"""C06 control-flow tests are simulations; test_real_* never substitutes tools.

Real acceptance cases FAIL with the exact environment dependency if unavailable.
A passing simulator case establishes no codec/playback evidence.
"""

from __future__ import annotations

import copy
import json
import os
import struct
import subprocess
import sys
import time
import zlib
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path

import pytest

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.media import MediaRequest
from ai_commerce.media.files import hash_file
from ai_commerce.operations import ArtifactCollisionError, ArtifactRegistry, JobCollisionError, LocalMediaRunner
from ai_commerce.render import (
    Canvas, EncoderConfig, FFmpegRenderer, RenderError, RenderItem,
    RenderManifest, RenderPlan, SourceMediaRef,
)
from ai_commerce.render import renderer as renderer_module
from ai_commerce.render.manifest import checksum
from ai_commerce.render.process import MAX_LOG, ToolRunner


def png(rgb=(255, 0, 0), size=16):
    def chunk(name, body):
        return struct.pack(">I", len(body)) + name + body + struct.pack(">I", zlib.crc32(name + body) & 0xFFFFFFFF)
    pixels = (b"\0" + bytes(rgb) * size) * size
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


@pytest.fixture
def registry(tmp_path):
    return ArtifactRegistry(tmp_path / "registry")


def import_source(registry, tmp_path, *, rgb=(255, 0, 0), name="still.png", mime="image/png"):
    path = tmp_path / name
    path.write_bytes(png(rgb))
    runner = LocalMediaRunner(registry)
    saved = runner.import_file(MediaRequest(source_path=path, mime_type=mime), job_id=name.replace(".", "-"))
    assert saved.state == "SUCCEEDED", saved.to_dict()
    return path, saved, SourceMediaRef.from_manifest(saved, manifest_ref=runner.manifest_ref(saved.job_id))


@pytest.fixture
def source(registry, tmp_path):
    return import_source(registry, tmp_path)[2]


def plan_for(source, *, frames=12, silence=False):
    return RenderPlan(Canvas(96, 64, 24), (RenderItem(source, 0, frames),), silence)


class Simulator:
    """Deliberately non-media bytes for storage/failure unit tests ONLY."""
    def __init__(self, monkeypatch, plan):
        self.plan, self.calls, self.failure = plan, [], None
        self.encoders = {"libx264", "png", "aac"}
        self.decoders = {"h264", "png", "mjpeg", "webp", "aac"}
        self.probe_change = lambda x: x
        self.decode_progress = None
        self.movie_bytes = 128
        monkeypatch.setattr(renderer_module.shutil, "which", lambda name: name)
        monkeypatch.setattr(subprocess, "run", self.run)

    def run(self, argv, **kwargs):
        assert isinstance(argv, list) and all(isinstance(x, str) for x in argv)
        assert kwargs["shell"] is False and kwargs["check"] is False
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] > 0
        assert kwargs["stdout"] != subprocess.PIPE and kwargs["stderr"] != subprocess.PIPE
        self.calls.append(list(argv))
        stage = ("CAPABILITIES" if "-version" in argv or "-encoders" in argv or "-decoders" in argv else
                 "FFPROBE" if "-show_format" in argv else
                 "FULL_DECODE" if "-progress" in argv else
                 "ENCODE" if "-filter_complex" in argv else "THUMBNAIL")
        if self.failure and self.failure[0] == stage:
            reason = self.failure[1]
            if stage == "ENCODE":
                Path(argv[-1]).write_bytes(b"partial non-media")
            if reason == "timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if reason == "interrupt":
                raise KeyboardInterrupt("interrupted local subprocess")
            if reason == "denied":
                raise PermissionError("fixture execution denied")
            kwargs["stderr"].write(b"simulated diagnostic")
            return subprocess.CompletedProcess(argv, -1073741502 if reason == "dll" else (0 if reason == "stderr" else 1))
        if "-version" in argv:
            kwargs["stdout"].write((argv[0] + " version SIMULATED-CONTROL-FLOW-ONLY\n").encode())
        elif "-encoders" in argv or "-decoders" in argv:
            kwargs["stdout"].write("\n".join(
                (" A..... " if name == "aac" else " V....D ") + name + " fixture"
                for name in sorted(self.encoders if "-encoders" in argv else self.decoders)).encode())
        elif stage == "ENCODE":
            with Path(argv[-1]).open("wb") as stream:
                remaining = self.movie_bytes
                while remaining:
                    count = min(remaining, 1024 * 1024)
                    stream.write(b"X" * count)
                    remaining -= count
        elif stage == "THUMBNAIL":
            Path(argv[-1]).write_bytes(png())
        elif stage == "FFPROBE":
            c, frames = self.plan.canvas, self.plan.duration_frames
            duration = str(float(Fraction(frames, c.fps)))
            video = {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
                     "width": c.width, "height": c.height, "avg_frame_rate": f"{c.fps}/1",
                     "r_frame_rate": f"{c.fps}/1", "time_base": f"1/{c.fps}",
                     "duration_ts": frames, "duration": duration, "nb_read_frames": str(frames),
                     "start_time": "0"}
            streams = [video]
            if self.plan.silence:
                streams.append({"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                                "channels": 2, "duration": duration})
            evidence = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                                  "duration": duration, "size": str(Path(argv[-1]).stat().st_size)},
                        "streams": streams}
            value = self.probe_change(evidence)
            kwargs["stdout"].write(value if isinstance(value, bytes) else canonical_bytes(value))
        else:
            kwargs["stdout"].write(self.decode_progress or f"frame={self.plan.duration_frames}\nprogress=end\n".encode())
        return subprocess.CompletedProcess(argv, 0)

    @property
    def encodes(self):
        return [x for x in self.calls if "-filter_complex" in x]


def test_plan_contract_roundtrip_and_immutable_source_snapshot(source):
    plan = replace(plan_for(source), composition_ref="composition:manual", composition_sha256="a" * 64,
                   composition_revision=2)
    assert RenderPlan.from_dict(json.loads(canonical_bytes(plan.to_dict()))) == plan
    edited = source.artifact
    edited["origin"] = "MODEL_GENERATED"
    assert source.artifact["origin"] == "LOCAL_IMPORT"
    with pytest.raises(FrozenInstanceError):
        plan.canvas.width = 100
    assert plan.duration_frames == 12
    assert len(plan.digest) == 64
    assert "composition" not in RenderPlan.__module__.split(".")


@pytest.mark.parametrize("values", [(95, 64, 24), (96, 65, 24), (0, 64, 24), (96, 64, 0),
                                    (96, 64, 24.0), (True, 64, 24), (8194, 64, 24)])
def test_invalid_canvas(values):
    with pytest.raises(ValueError):
        Canvas(*values)


@pytest.mark.parametrize("start,duration", [(-1, 2), (0, 0), (0, -1), (0.0, 2), (0, True)])
def test_integer_timing_required(source, start, duration):
    with pytest.raises(ValueError):
        RenderItem(source, start, duration)


def test_asset_required_and_contiguous_order(source):
    with pytest.raises(ValueError, match="ASSET_REQUIRED"):
        RenderPlan(Canvas(64, 64, 30), ())
    for items in ((RenderItem(source, 1, 5),),
                  (RenderItem(source, 0, 5), RenderItem(source, 4, 5)),
                  (RenderItem(source, 0, 5), RenderItem(source, 6, 5))):
        with pytest.raises(ValueError, match="contiguous"):
            RenderPlan(Canvas(64, 64, 30), items)
    with pytest.raises(ValueError):
        replace(plan_for(source), composition_ref="unbound")


@pytest.mark.parametrize("config", [{"encoder": "mpeg4"}, {"crf": 52}, {"crf": True}, {"preset": "bad"}])
def test_incompatible_encoder_config(config):
    with pytest.raises(ValueError):
        EncoderConfig(**config)


@pytest.mark.parametrize("missing", [{"ffmpeg"}, {"ffprobe"}, {"ffmpeg", "ffprobe"}])
def test_tools_absent_return_saved_precise_failure(registry, source, monkeypatch, missing):
    plan = plan_for(source)
    sim = Simulator(monkeypatch, plan)
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: None if name in missing else name)
    runner = FFmpegRenderer(registry)
    result = runner.render(plan, job_id="absent")
    assert result.state == "FAILED" and result.output is None
    assert result.error["code"] == "ENVIRONMENT_TOOL_UNAVAILABLE"
    assert all(x in result.error["detail"] for x in missing)
    assert runner.load("absent").to_dict() == result.to_dict()
    assert not sim.encodes
    assert result.to_dict()["provenance"]["classification"] == "UNKNOWN"


@pytest.mark.parametrize("missing,silence", [("libx264", False), ("png", False), ("aac", True)])
def test_encoder_absent_is_environment_failure(registry, source, monkeypatch, missing, silence):
    plan = plan_for(source, silence=silence)
    sim = Simulator(monkeypatch, plan)
    sim.encoders.remove(missing)
    result = FFmpegRenderer(registry).render(plan, job_id="codec")
    assert result.state == "FAILED"
    assert result.error["code"] == "ENVIRONMENT_ENCODER_UNAVAILABLE"
    assert missing in result.error["detail"]
    assert not sim.encodes
    assert result.to_dict()["tools"]["ffmpeg_version"].startswith("ffmpeg version")


@pytest.mark.parametrize("failure,code", [("dll", "ENVIRONMENT_TOOL_UNAVAILABLE"),
                                         ("denied", "ENVIRONMENT_TOOL_UNAVAILABLE"),
                                         ("timeout", "RENDER_TIMEOUT"),
                                         ("error", "RENDER_PROCESS_FAILED")])
def test_tool_start_failure_detected_and_logged(registry, monkeypatch, source, failure, code):
    plan = plan_for(source)
    sim = Simulator(monkeypatch, plan)
    sim.failure = ("CAPABILITIES", failure)
    result = FFmpegRenderer(registry).render(plan, job_id="start")
    assert result.error["code"] == code and result.output is None
    raw = registry.read(result.to_dict()["diagnostics"]["ref"])
    assert json.loads(raw)["commands"][0]["stage"] == "CAPABILITIES"


@pytest.mark.parametrize("failure,code", [("timeout", "RENDER_TIMEOUT"), ("error", "RENDER_PROCESS_FAILED"),
                                         ("stderr", "RENDER_PROCESS_FAILED")])
def test_partial_encode_never_becomes_success(registry, monkeypatch, source, failure, code):
    plan = plan_for(source)
    sim = Simulator(monkeypatch, plan)
    sim.failure = ("ENCODE", failure)
    runner = FFmpegRenderer(registry)
    result = runner.render(plan, job_id="partial")
    assert result.state == "FAILED" and result.error["code"] == code
    assert result.output is None and result.thumbnail is None
    assert runner.render(plan, job_id="partial").to_dict() == result.to_dict()
    assert len(sim.encodes) == 1
    assert not list(registry.root.glob(".render-work-*"))


def test_interruption_and_marker_only_reopen_remain_unknown(registry, monkeypatch, source):
    plan = plan_for(source)
    sim = Simulator(monkeypatch, plan)
    sim.failure = ("ENCODE", "interrupt")
    runner = FFmpegRenderer(registry)
    with pytest.raises(KeyboardInterrupt):
        runner.render(plan, job_id="interrupted")
    assert runner.load("interrupted").state == "UNKNOWN"
    assert runner.render(plan, job_id="interrupted").state == "UNKNOWN"
    assert len(sim.encodes) == 1
    binding = checksum({"request_version": 1, "job_id": "marker", "created_at": "2026-10-29T00:00:00+00:00",
                        "plan": plan.to_dict(), "encoder": EncoderConfig().to_dict()})
    registry.put_immutable("render-jobs/marker/request.json", canonical_bytes(binding))
    assert runner.load("marker").error["code"] == "RENDER_INTERRUPTED"
    assert runner.render(plan, job_id="marker").state == "UNKNOWN"
    assert len(sim.encodes) == 1


@pytest.mark.parametrize("kind", ["missing", "corrupt", "wrong-mime", "wrong-size", "wrong-hash", "manifest"])
def test_bad_source_and_manifest_are_rejected_before_encode(registry, tmp_path, monkeypatch, kind):
    path, saved, source = import_source(registry, tmp_path)
    if kind == "missing":
        registry.safe_path(source.artifact["path"]).unlink()
    elif kind == "corrupt":
        registry.safe_path(source.artifact["path"]).write_bytes(b"corrupt")
    elif kind == "manifest":
        registry.safe_path(source.manifest_ref).write_bytes(b'{"manifest_version":1}')
    else:
        artifact = source.artifact
        if kind == "wrong-mime":
            artifact["mime_type"] = "image/jpeg"
        elif kind == "wrong-size":
            artifact["size_bytes"] += 1
        else:
            artifact["sha256"] = "0" * 64
        source = SourceMediaRef.from_artifact(artifact)
    plan = plan_for(source)
    sim = Simulator(monkeypatch, plan)
    result = FFmpegRenderer(registry).render(plan, job_id="bad-source")
    assert result.state == "FAILED" and result.error["code"] == "SOURCE_INVALID"
    assert result.output is None and not sim.encodes


@pytest.mark.parametrize("field,value", [
    ("codec_name", "mpeg4"), ("pix_fmt", "yuv444p"), ("width", 100),
    ("avg_frame_rate", "30/1"), ("r_frame_rate", "30/1"), ("nb_read_frames", "11"),
    ("duration_ts", 13), ("duration", "NaN"), ("start_time", "1"), ("time_base", "1/0"),
])
def test_ffprobe_stream_mismatch_never_succeeds(registry, source, monkeypatch, field, value):
    plan = plan_for(source)
    sim = Simulator(monkeypatch, plan)
    def mutate(data):
        data["streams"][0][field] = value
        return data
    sim.probe_change = mutate
    result = FFmpegRenderer(registry).render(plan, job_id="bad-probe")
    assert result.state == "FAILED" and result.error["code"] == "OUTPUT_INVALID"
    assert result.output is None
    assert not any("-progress" in x for x in sim.calls)


@pytest.mark.parametrize("kind", ["json", "size", "duration", "streams", "format", "no-end", "decode-frames", "decode-error"])
def test_probe_and_full_decode_both_required(registry, source, monkeypatch, kind):
    plan = plan_for(source)
    sim = Simulator(monkeypatch, plan)
    def mutate(data):
        if kind == "json":
            return b'{"streams": ['
        if kind == "size":
            data["format"]["size"] = "1"
        if kind == "duration":
            data["format"]["duration"] = "1"
        if kind == "streams":
            data["streams"].append(copy.deepcopy(data["streams"][0]))
        if kind == "format":
            data["format"]["format_name"] = "avi"
        return data
    sim.probe_change = mutate
    if kind == "no-end":
        sim.decode_progress = b"frame=12\nprogress=continue\n"
    if kind == "decode-frames":
        sim.decode_progress = b"frame=11\nprogress=end\n"
    if kind == "decode-error":
        sim.failure = ("FULL_DECODE", "error")
    result = FFmpegRenderer(registry).render(plan, job_id="verify")
    assert result.state == "FAILED" and result.output is None
    assert result.error["code"] in {"OUTPUT_INVALID", "RENDER_PROCESS_FAILED"}


def test_simulated_receipt_reopen_preserves_import_lineage_and_immutability(registry, tmp_path, monkeypatch):
    original, saved, source = import_source(registry, tmp_path)
    old_bytes = registry.read(source.manifest_ref)
    plan = replace(plan_for(source), composition_ref="composition:external-v1", composition_sha256="1" * 64,
                   composition_revision=1)
    sim = Simulator(monkeypatch, plan)
    runner = FFmpegRenderer(registry)
    result = runner.render(plan, job_id="custody")
    assert result.state == "SUCCEEDED"  # Control-flow fixture, NOT playable media.
    original.unlink()
    assert FFmpegRenderer(ArtifactRegistry(registry.root)).load("custody").to_dict() == result.to_dict()
    assert registry.read(source.manifest_ref) == old_bytes
    assert result.plan.items[0].source.artifact == saved.result["artifact"]
    assert result.to_dict()["provenance"]["classification"] == "DERIVED_RENDER"
    assert result.to_dict()["provenance"]["generation_performed"] is False
    assert result.to_dict()["generation_performed"] is False
    assert result.to_dict()["semantic_creative_realization"] == "UNKNOWN"
    assert result.to_dict()["plan"]["composition_sha256"] == "1" * 64
    with pytest.raises(ArtifactCollisionError):
        registry.put_immutable(runner.manifest_ref("custody"), b"replacement")
    with pytest.raises(JobCollisionError):
        runner.render(plan_for(source, frames=24), job_id="custody")
    assert len(sim.encodes) == 1
    altered = result.to_dict()
    altered["state"] = "FAILED"
    assert result.state == "SUCCEEDED"
    assert RenderManifest.from_dict(result.to_dict()).to_dict() == result.to_dict()


@pytest.mark.parametrize("target", ["video", "thumbnail", "diagnostics", "manifest", "request", "source-manifest"])
def test_reopen_rejects_hash_corruption(registry, source, monkeypatch, target):
    plan = plan_for(source)
    Simulator(monkeypatch, plan)
    runner = FFmpegRenderer(registry)
    result = runner.render(plan, job_id="corruption")
    assert result.state == "SUCCEEDED"
    record = result.to_dict()
    refs = {"video": result.output["ref"], "thumbnail": result.thumbnail["ref"],
            "diagnostics": record["diagnostics"]["ref"], "manifest": runner.manifest_ref(result.job_id),
            "request": f"render-jobs/{result.job_id}/request.json", "source-manifest": source.manifest_ref}
    registry.safe_path(refs[target]).write_bytes(b"corrupted immutable bytes")
    with pytest.raises(RenderError):
        runner.load(result.job_id)


def test_rehashed_manifest_cannot_promote_generation(registry, source, monkeypatch):
    plan = plan_for(source)
    Simulator(monkeypatch, plan)
    result = FFmpegRenderer(registry).render(plan, job_id="promote")
    record = result.to_dict()
    record.pop("record_sha256")
    record["generation_performed"] = True
    with pytest.raises(RenderError, match="generation"):
        RenderManifest.from_dict(checksum(record))


def test_output_custody_and_reopen_use_bounded_file_reads(registry, source, monkeypatch):
    from ai_commerce.media import files as media_files
    from ai_commerce.operations import registry as registry_module
    plan = plan_for(source)
    sim = Simulator(monkeypatch, plan)
    sim.movie_bytes = 20 * 1024 * 1024
    actual_open = media_files.open_regular
    observed_reads = []
    class Bounded:
        def __init__(self, stream):
            self.stream = stream
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024, "Unbounded large-file read"
            observed_reads.append(size)
            return self.stream.read(size)
    @contextmanager
    def guarded(path):
        with actual_open(path) as stream:
            yield Bounded(stream) if os.fstat(stream.fileno()).st_size >= sim.movie_bytes else stream
    monkeypatch.setattr(media_files, "open_regular", guarded)
    monkeypatch.setattr(registry_module, "open_regular", guarded)
    actual_read = registry.read
    def no_object_read(ref):
        assert not ref.startswith("objects/"), "Registry bytes API used for output"
        return actual_read(ref)
    monkeypatch.setattr(registry, "read", no_object_read)
    runner = FFmpegRenderer(registry)
    result = runner.render(plan, job_id="large")
    assert result.state == "SUCCEEDED" and result.output["size_bytes"] == sim.movie_bytes
    assert runner.load("large").state == "SUCCEEDED"
    assert observed_reads and max(observed_reads) == 1024 * 1024


def test_actual_child_timeout_is_bounded_and_captured(tmp_path):
    logs = []
    started = time.monotonic()
    with pytest.raises(RenderError) as error:
        ToolRunner(tmp_path, 0.2, logs).run([sys.executable, "-c", "import time; time.sleep(10)"], "TIMEOUT")
    assert error.value.code == "RENDER_TIMEOUT"
    assert time.monotonic() - started < 6
    assert logs[0]["timed_out"] is True and logs[0]["returncode"] is None


def test_actual_diagnostics_are_bounded(tmp_path):
    logs = []
    with pytest.raises(RenderError) as error:
        ToolRunner(tmp_path, 10, logs).run(
            [sys.executable, "-c", "import sys; sys.stdout.write('x'*2097152); sys.stderr.write('e'*2097152)"], "LOG_LIMIT")
    assert error.value.code == "TOOL_OUTPUT_LIMIT"
    assert logs[0]["stdout_bytes"] == logs[0]["stderr_bytes"] == 2097152
    assert len(logs[0]["stdout"]) <= MAX_LOG and len(logs[0]["stderr"]) <= MAX_LOG
    assert logs[0]["stdout_truncated"] and logs[0]["stderr_truncated"]


def require_real_tools(registry):
    runner = FFmpegRenderer(registry, timeout_seconds=30)
    try:
        tools = runner.detect()
    except RenderError as exc:
        pytest.fail(f"REAL_MP4_UNVERIFIED: {exc.code}: {exc.detail}", pytrace=False)
    return runner, tools


@pytest.mark.parametrize("mime,suffix", [("image/png", ".png"), ("image/jpeg", ".jpg"), ("image/webp", ".webp")])
def test_real_single_import_to_playable_h264(registry, tmp_path, mime, suffix):
    runner, tools = require_real_tools(registry)
    original = tmp_path / ("real" + suffix)
    if mime == "image/png":
        original.write_bytes(png())
    else:
        raw = tmp_path / "fixture.rgb"
        raw.write_bytes(bytes((255, 0, 0)) * 16 * 16)
        ToolRunner(tmp_path, 30, []).run([
            tools["ffmpeg"], "-nostdin", "-v", "error", "-n",
            "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "16x16",
            "-i", str(raw), "-frames:v", "1", "-c:v", "mjpeg" if mime == "image/jpeg" else "libwebp",
            "-threads", "1", str(original),
        ], "TEST_IMAGE_FIXTURE")
    media_runner = LocalMediaRunner(registry)
    saved = media_runner.import_file(MediaRequest(source_path=original, mime_type=mime), job_id="real-import")
    assert saved.state == "SUCCEEDED", f"REAL_MP4_UNVERIFIED: source dependency/validation: {saved.to_dict()}"
    source = SourceMediaRef.from_manifest(saved, manifest_ref=media_runner.manifest_ref(saved.job_id))
    before = registry.read(source.manifest_ref)
    plan = replace(plan_for(source, frames=24), composition_ref="external-composition:1",
                   composition_sha256="2" * 64, composition_revision=1)
    result = runner.render(plan, job_id="real-still")
    assert result.state == "SUCCEEDED", result.to_dict()
    output = result.output
    assert hash_file(registry.safe_path(output["ref"])) == (output["sha256"], output["size_bytes"])
    assert output["verification"]["codec"] == "h264"
    assert output["verification"]["pixel_format"] == "yuv420p"
    assert output["verification"]["frame_count"] == 24
    assert Fraction(output["verification"]["duration"]) == 1
    original.unlink()
    assert FFmpegRenderer(ArtifactRegistry(registry.root), timeout_seconds=30).load(result.job_id).to_dict() == result.to_dict()
    assert registry.read(source.manifest_ref) == before
    commands = json.loads(registry.read(result.to_dict()["diagnostics"]["ref"]))["commands"]
    assert any(c["stage"] == "FFPROBE" and c["returncode"] == 0 for c in commands)
    assert any(c["stage"] == "FULL_DECODE" and c["returncode"] == 0 for c in commands)
    assert "SIMULATED" not in result.to_dict()["tools"]["ffmpeg_version"]
    registry.safe_path(output["ref"]).write_bytes(b"corrupt after successful real render")
    with pytest.raises(RenderError):
        runner.load(result.job_id)


@pytest.mark.parametrize("silence", [False, True])
def test_real_ordered_scenes_ratio_fps_and_silence(registry, tmp_path, silence):
    runner, tools = require_real_tools(registry)
    red = import_source(registry, tmp_path, rgb=(255, 0, 0), name="red.png")[2]
    blue = import_source(registry, tmp_path, rgb=(0, 0, 255), name="blue.png")[2]
    plan = RenderPlan(Canvas(64, 96, 15), (RenderItem(red, 0, 7), RenderItem(blue, 7, 13)), silence)
    result = runner.render(plan, job_id="real-scenes")
    assert result.state == "SUCCEEDED", result.to_dict()
    assert result.output["verification"]["frame_count"] == 20
    assert Fraction(result.output["verification"]["duration"]) == Fraction(20, 15)
    assert (result.output["verification"]["audio"] is not None) == silence
    sync_args = [["-fps_mode", "passthrough"], ["-vsync", "0"]]
    samples = None
    for sync_arg in sync_args:
        try:
            samples = ToolRunner(tmp_path, 30, []).run([
                tools["ffmpeg"], "-nostdin", "-v", "error", "-xerror",
                "-i", str(registry.safe_path(result.output["ref"])),
                "-vf", r"select=eq(n\,0)+eq(n\,7)", *sync_arg, "-frames:v", "2",
                "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
            ], "TEST_SCENE_ORDER")
            break
        except RenderError:
            continue
    assert samples is not None, "Failed to extract test scene order frames"
    frame_bytes = 64 * 96 * 3
    assert len(samples) == 2 * frame_bytes
    center = (48 * 64 + 32) * 3
    first, second = samples[center:center + 3], samples[frame_bytes + center:frame_bytes + center + 3]
    assert first[0] > 200 and first[2] < 40
    assert second[2] > 200 and second[0] < 40
    assert runner.load(result.job_id).state == "SUCCEEDED"


@pytest.mark.parametrize("missing,silence", [("h264", False), ("png", False), ("aac", True)])
def test_required_decoder_absent_is_environment_failure(registry, source, monkeypatch, missing, silence):
    plan = plan_for(source, silence=silence)
    sim = Simulator(monkeypatch, plan)
    sim.decoders.remove(missing)
    result = FFmpegRenderer(registry).render(plan, job_id="decoder")
    assert result.state == "FAILED"
    assert result.error["code"] == "ENVIRONMENT_DECODER_UNAVAILABLE"
    assert missing in result.error["detail"]
    assert not sim.encodes


def test_simulated_multi_scene_silence_contract(registry, source, tmp_path, monkeypatch):
    blue = import_source(registry, tmp_path, rgb=(0, 0, 255), name="blue.png")[2]
    plan = RenderPlan(Canvas(64, 96, 15), (RenderItem(source, 0, 7), RenderItem(blue, 7, 13)), True)
    sim = Simulator(monkeypatch, plan)
    result = FFmpegRenderer(registry).render(plan, job_id="ordered")
    assert result.state == "SUCCEEDED"  # Stream-contract simulation only.
    command = sim.encodes[0]
    assert command.count("-loop") == 2
    graph = command[command.index("-filter_complex") + 1]
    assert "trim=end_frame=7" in graph and "trim=end_frame=13" in graph
    assert "[s0][s1]concat=n=2" in graph
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in command
    assert "-t" in command and "-frames:v" not in command
    assert result.output["verification"]["audio"]["codec"] == "aac"
    assert FFmpegRenderer(registry).load("ordered").state == "SUCCEEDED"


def test_observed_environment_never_invents_success(registry, source):
    result = FFmpegRenderer(registry).render(plan_for(source), job_id="observed-environment")
    if result.state == "FAILED":
        assert result.error["code"].startswith("ENVIRONMENT_"), result.to_dict()
        assert result.output is None and result.thumbnail is None
        assert result.to_dict()["provenance"]["classification"] == "UNKNOWN"
    else:
        assert result.state == "SUCCEEDED", result.to_dict()
        assert result.output["verification"]["frame_count"] == 12
        assert "SIMULATED" not in result.to_dict()["tools"]["ffmpeg_version"]
