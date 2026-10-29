"""Technical acceptance requires both ffprobe JSON and error-strict full decode."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from ai_commerce.media.files import hash_file
from ai_commerce.operations.errors import ManifestValidationError
from ai_commerce.operations.manifest import strict_json

from .models import RenderError, RenderPlan
from .process import ToolRunner


def verify_mp4(path: Path, plan: RenderPlan, tools: dict, runner: ToolRunner) -> dict:
    before = hash_file(path)
    raw = runner.run([
        tools["ffprobe"], "-v", "error", "-protocol_whitelist", "file",
        "-enable_drefs", "0", "-count_frames", "-show_streams", "-show_format",
        "-of", "json", "-i", str(path),
    ], "FFPROBE")
    try:
        data = strict_json(raw)
        fmt, streams = data["format"], data["streams"]
        videos = [s for s in streams if s["codec_type"] == "video"]
        audios = [s for s in streams if s["codec_type"] == "audio"]
        if "mp4" not in fmt["format_name"].split(",") or len(videos) != 1:
            raise ValueError("Expected one MP4 video stream")
        if len(streams) != 1 + int(plan.silence) or len(audios) != int(plan.silence):
            raise ValueError("Unexpected/missing streams")
        video, canvas = videos[0], plan.canvas
        if (video["codec_name"] != "h264" or video["pix_fmt"] != "yuv420p"
                or video["width"] != canvas.width or video["height"] != canvas.height):
            raise ValueError("Codec/pixel format/canvas mismatch")
        if (Fraction(video["avg_frame_rate"]) != canvas.fps
                or Fraction(video["r_frame_rate"]) != canvas.fps
                or int(video["nb_read_frames"]) != plan.duration_frames):
            raise ValueError("FPS or decoded frame count mismatch")
        time_base = Fraction(video["time_base"])
        if time_base <= 0 or time_base > Fraction(1, canvas.fps):
            raise ValueError("Invalid video time base")
        duration = int(video["duration_ts"]) * time_base
        expected = Fraction(plan.duration_frames, canvas.fps)
        if duration != expected or abs(Fraction(video["duration"]) - expected) > Fraction(1, 1000000):
            raise ValueError("Video duration differs from frame plan")
        if abs(Fraction(video.get("start_time", "0"))) > time_base:
            raise ValueError("Video must start at frame zero")
        container_duration = Fraction(fmt["duration"])
        tolerance = Fraction(1, 1000) if not plan.silence else Fraction(1024, 48000) + Fraction(1, 1000)
        if abs(container_duration - expected) > tolerance or int(fmt["size"]) != before[1] or before[1] <= 0:
            raise ValueError("Actual container duration/size mismatch")
        audio = None
        if plan.silence:
            a = audios[0]
            if (a["codec_name"] != "aac" or int(a["sample_rate"]) != 48000 or a["channels"] != 2
                    or abs(Fraction(a["duration"]) - expected) > max(Fraction(1, canvas.fps), tolerance)):
                raise ValueError("Silent AAC stream mismatch")
            audio = {"codec": "aac", "sample_rate": 48000, "channels": 2,
                     "duration": str(Fraction(a["duration"]))}
        observed = {"codec": "h264", "pixel_format": "yuv420p", "width": canvas.width,
                    "height": canvas.height, "fps": canvas.fps,
                    "frame_count": int(video["nb_read_frames"]), "duration": str(duration),
                    "container_duration": str(container_duration), "audio": audio}
    except (KeyError, TypeError, ValueError, ArithmeticError, ManifestValidationError) as exc:
        raise RenderError("OUTPUT_INVALID", f"Invalid ffprobe evidence: {exc}", "FFPROBE") from exc
    progress = runner.run([
        tools["ffmpeg"], "-nostdin", "-hide_banner", "-v", "error", "-xerror",
        "-progress", "pipe:1", "-nostats", "-err_detect", "explode",
        "-protocol_whitelist", "file", "-enable_drefs", "0", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a?", "-threads", "1", "-f", "null", "-",
    ], "FULL_DECODE").decode("utf-8", "replace")
    events = {}
    for line in progress.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            events[key] = value.strip()
    if events.get("progress") != "end" or events.get("frame") != str(plan.duration_frames):
        raise RenderError("OUTPUT_INVALID", "Full decode did not finish with the expected frame count", "FULL_DECODE")
    if hash_file(path) != before:
        raise RenderError("OUTPUT_CORRUPT", "Output changed during validation", "VERIFY")
    return {"ref": "objects/" + before[0], "sha256": before[0], "size_bytes": before[1],
            "mime_type": "video/mp4", "verification": observed}
