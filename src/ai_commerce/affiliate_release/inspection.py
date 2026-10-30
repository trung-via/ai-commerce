"""C08: deterministic technical evidence for explicitly requested release bytes.

This inspects media, not creative quality, rights, eligibility or publication.
The A05 render-plan validator remains authoritative for A05 render conformance.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.e2e import inspect as inspect_e01
from ai_commerce.media.base import MediaValidationError
from ai_commerce.media import files as media_files
from ai_commerce.operations.errors import ManifestValidationError
from ai_commerce.operations.manifest import strict_json

from .process import BoundedToolRunner, ToolFailure
from .schemas import media_inspection_schema

_SHA = re.compile(r"[0-9a-f]{64}")
_VALIDATOR = Draft202012Validator(media_inspection_schema())
_MANIFEST_BYTES = 1024 * 1024
_BLACK = re.compile(r"black_start:([^\s]+)\s+black_end:([^\s]+)\s+black_duration:([^\s]+)")
_SILENCE = re.compile(r"silence_(start|end):\s*([-+0-9.eE]+)")


class MediaInspectionError(ValueError):
    def __init__(self, code: str, status: str = "FAIL"):
        super().__init__(code)
        self.code, self.status = code, status


def _require(condition: bool, code: str):
    if not condition:
        raise MediaInspectionError(code)


@dataclass(frozen=True)
class InspectionLimits:
    """Resource ceilings. Exceeding them yields UNKNOWN, never a creative veto."""
    max_asset_bytes: int = 256 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024
    max_assets: int = 16
    tool_timeout_seconds: float = 30
    max_tool_output_bytes: int = 1024 * 1024

    def __post_init__(self):
        for name, maximum in (("max_asset_bytes", 1024 ** 3), ("max_total_bytes", 4 * 1024 ** 3),
                              ("max_assets", 64), ("max_tool_output_bytes", 1024 ** 2)):
            value = getattr(self, name)
            _require(type(value) is int and 1 <= value <= maximum, "INVALID_INSPECTION_LIMITS")
        value = self.tool_timeout_seconds
        _require(not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value) and 0 < value <= 60
                 and self.max_tool_output_bytes >= 1024, "INVALID_INSPECTION_LIMITS")


@dataclass(frozen=True)
class DiagnosticOptions:
    black: bool = False
    silence: bool = False

    def __post_init__(self):
        _require(type(self.black) is bool and type(self.silence) is bool, "INVALID_DIAGNOSTIC_OPTIONS")


@dataclass(frozen=True)
class ReleaseAsset:
    """An explicit local asset pinned to the release request's expected SHA-256."""
    asset_ref: str
    expected_sha256: str
    mime_type: str = "video/mp4"
    e01_manifest_ref: str | None = None
    e01_manifest_sha256: str | None = None

    def __post_init__(self):
        _require(isinstance(self.asset_ref, str) and 0 < len(self.asset_ref) <= 512,
                 "INVALID_RELEASE_ASSET_REF")
        try:
            self.asset_ref.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise MediaInspectionError("INVALID_RELEASE_ASSET_REF") from exc
        _require(isinstance(self.expected_sha256, str) and bool(_SHA.fullmatch(self.expected_sha256)),
                 "INVALID_RELEASE_SHA256")
        _require(isinstance(self.mime_type, str) and len(self.mime_type) <= 128
                 and bool(re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", self.mime_type)),
                 "INVALID_RELEASE_MIME_TYPE")
        _require((self.e01_manifest_ref is None) == (self.e01_manifest_sha256 is None),
                 "E01_MANIFEST_BINDING_REQUIRED")
        if self.e01_manifest_ref is not None:
            _relative(self.e01_manifest_ref)
            _require(isinstance(self.e01_manifest_sha256, str)
                     and bool(_SHA.fullmatch(self.e01_manifest_sha256)), "INVALID_MANIFEST_SHA256")


def _relative(ref: str) -> PurePosixPath:
    _require(isinstance(ref, str) and 0 < len(ref) <= 512, "MEDIA_PATH_INVALID")
    path = PurePosixPath(ref)
    _require(not path.is_absolute()
             and path.as_posix() == ref and bool(path.parts), "MEDIA_PATH_INVALID")
    for part in path.parts:
        device = part.split(".", 1)[0].upper()
        _require(part not in {".", ".."} and not part.endswith((".", " "))
                 and not re.search(r'[\x00-\x1f\x7f\\:<>"|?*]', part)
                 and device not in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                 and not re.fullmatch(r"(?:COM|LPT)[0-9¹²³]", device), "MEDIA_PATH_INVALID")
    return path


def _local(root: Path, ref: str) -> Path:
    relative = _relative(ref)
    _require(not str(root).startswith(("\\\\", "//")) and ".." not in root.parts
             and not re.search(r"[\x00-\x1f\x7f]", str(root)),
             "MEDIA_PATH_INVALID")
    path = root.joinpath(*relative.parts)
    _require(len(str(path)) <= 4096 and path.is_relative_to(root), "MEDIA_PATH_INVALID")
    # Do not resolve links away: open_regular checks every ancestor and handle.
    return path


def _copy_bounded(stream, target, limit: int) -> tuple[str, int]:
    _require(limit > 0, "MEDIA_BYTE_LIMIT")
    if os.fstat(stream.fileno()).st_size > limit:
        raise MediaInspectionError("MEDIA_BYTE_LIMIT", "UNKNOWN")
    digest, size = hashlib.sha256(), 0
    while chunk := stream.read(min(media_files.CHUNK_BYTES, limit - size + 1)):
        size += len(chunk)
        if size > limit:
            raise MediaInspectionError("MEDIA_BYTE_LIMIT", "UNKNOWN")
        digest.update(chunk)
        if target is not None and target.write(chunk) != len(chunk):
            raise MediaInspectionError("MEDIA_CAPTURE_FAILED", "UNKNOWN")
    return digest.hexdigest(), size


def _read_e01(root: Path, ref: str, expected_sha256: str) -> dict:
    _require(isinstance(expected_sha256, str) and bool(_SHA.fullmatch(expected_sha256)),
             "INVALID_MANIFEST_SHA256")
    path = _local(root, ref)
    try:
        with media_files.open_regular(path) as stream:
            _require(0 < os.fstat(stream.fileno()).st_size <= _MANIFEST_BYTES, "E01_MANIFEST_BYTE_LIMIT")
            raw = stream.read(_MANIFEST_BYTES + 1)
            _require(len(raw) <= _MANIFEST_BYTES, "E01_MANIFEST_BYTE_LIMIT")
        _require(sha256_bytes(raw) == expected_sha256, "E01_MANIFEST_SHA256_MISMATCH")
        # The real A06 public reader sees only this bounded, captured manifest.
        # Full A06 verify would inspect unrequested source assets/render custody.
        with tempfile.TemporaryDirectory(prefix="a13-e01-") as directory:
            captured = Path(directory)
            (captured / "e01-manifest.json").write_bytes(raw)
            record = inspect_e01(captured)
        _require(bool(_SHA.fullmatch(record["output_sha256"]))
                 and bool(_SHA.fullmatch(record["request_sha256"]))
                 and record["run_id"] == "e01-" + record["request_sha256"][:32],
                 "E01_MANIFEST_BINDING_INVALID")
        return record
    except MediaInspectionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise MediaInspectionError("E01_MANIFEST_INVALID") from exc


def asset_from_e01(root: str | Path, manifest_ref: str = "e01-manifest.json", *,
                   expected_manifest_sha256: str) -> ReleaseAsset:
    """Pin the A06 delivered video; never infer rights or full custody verification."""
    root = Path(root).absolute()
    record = _read_e01(root, manifest_ref, expected_manifest_sha256)
    video_ref = str(PurePosixPath(manifest_ref).parent / "video.mp4")
    return ReleaseAsset(video_ref, record["output_sha256"], e01_manifest_ref=manifest_ref,
                        e01_manifest_sha256=expected_manifest_sha256)


def _source(root: Path, asset: ReleaseAsset) -> dict:
    if asset.e01_manifest_ref is None:
        return {"kind": "LOCAL_EXPECTED_HASH", "custody_verification": "NOT_PERFORMED"}
    record = _read_e01(root, asset.e01_manifest_ref, asset.e01_manifest_sha256)
    _require(asset.asset_ref == str(PurePosixPath(asset.e01_manifest_ref).parent / "video.mp4")
             and asset.expected_sha256 == record["output_sha256"] and asset.mime_type == "video/mp4",
             "E01_RELEASE_ASSET_MISMATCH")
    return {"kind": "A06_E01_MANIFEST_HASH_BOUND", "manifest_ref": asset.e01_manifest_ref,
            "manifest_sha256": asset.e01_manifest_sha256, "record_sha256": record["record_sha256"],
            "run_id": record["run_id"], "custody_verification": "NOT_PERFORMED"}


def _diagnostic(kind: str, requested: bool) -> dict:
    parameters = {"minimum_duration_seconds": "1/10"}
    parameters.update({"pixel_threshold": "1/10", "picture_black_ratio": "49/50"}
                      if kind == "black" else {"noise_db": -50})
    return {"status": "UNKNOWN" if requested else "NOT_REQUESTED", "parameters": parameters,
            "segments": [], "reason_codes": ["NOT_INSPECTED"] if requested else []}


def _fraction(value, *, positive=False) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Missing/bounded rational value required")
    # Avoid enormous Fraction allocations from malicious exponents in evidence.
    if not (re.fullmatch(r"[+-]?[0-9]{1,20}/[0-9]{1,20}", value)
            or re.fullmatch(r"[+-]?[0-9]{1,20}(?:\.[0-9]{1,12})?(?:[eE][+-]?0?[0-9])?", value)):
        raise ValueError("Bounded numeric representation required")
    number = Fraction(value)
    if positive and number <= 0:
        raise ValueError("Positive rational value required")
    return str(number)


def _integer(value) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        value = int(value)
    if type(value) is not int or value <= 0:
        raise ValueError("Positive integer required")
    return value


def _observe(raw: bytes, size: int) -> dict:
    try:
        data = strict_json(raw)
        fmt, streams = data["format"], data["streams"]
        _require(_integer(fmt["size"]) == size, "MEDIA_SIZE_MISMATCH")
        _require(isinstance(streams, list) and 1 <= len(streams) <= 16, "MEDIA_STREAMS_INVALID")
        formats = fmt["format_name"].split(",")
        _require("mp4" in formats and len(formats) <= 16, "MEDIA_CONTAINER_MISMATCH")
        observed = []
        for stream in streams:
            kind = stream["codec_type"]
            if kind not in {"video", "audio"}:
                raise MediaInspectionError("MEDIA_STREAM_UNSUPPORTED", "UNKNOWN")
            _require(type(stream["index"]) is int and stream["index"] >= 0
                     and isinstance(stream["codec_name"], str)
                     and 0 < len(stream["codec_name"]) <= 80, "MEDIA_STREAMS_INVALID")
            item = {"index": stream["index"], "kind": kind, "codec": stream["codec_name"],
                    "width": None, "height": None, "fps": None, "duration_seconds": None,
                    "decoded_frames": None, "sample_rate": None, "channels": None}
            if stream.get("duration") not in (None, "N/A"):
                item["duration_seconds"] = _fraction(stream["duration"], positive=True)
            if kind == "video":
                item.update(width=_integer(stream["width"]), height=_integer(stream["height"]),
                            decoded_frames=_integer(stream["nb_read_frames"]))
                if stream.get("avg_frame_rate") not in (None, "N/A", "0/0", "0/1"):
                    item["fps"] = _fraction(stream["avg_frame_rate"], positive=True)
            else:
                item.update(sample_rate=_integer(stream["sample_rate"]), channels=_integer(stream["channels"]))
            observed.append(item)
        _require(len({s["index"] for s in observed}) == len(observed), "MEDIA_STREAMS_INVALID")
        return {"container": "mp4", "format_names": sorted(set(formats)),
                "duration_seconds": _fraction(fmt["duration"], positive=True),
                "streams": sorted(observed, key=lambda s: s["index"])}
    except MediaInspectionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError, ManifestValidationError, RecursionError) as exc:
        raise MediaInspectionError("MEDIA_FACTS_INCOMPLETE", "UNKNOWN") from exc


def _input(ffmpeg: str, path: Path, *, diagnostic=False) -> list[str]:
    return [ffmpeg, "-nostdin", "-hide_banner", "-v", "info" if diagnostic else "error",
            "-xerror", "-nostats", "-max_alloc", "134217728", "-err_detect", "explode",
            "-threads", "1", "-filter_threads", "1",
            "-protocol_whitelist", "file", "-enable_drefs", "0", "-f", "mov", "-i", str(path)]


def _segment(index: int, start: str, end: str) -> dict:
    first, last = Fraction(_fraction(start)), Fraction(_fraction(end))
    _require(first <= last, "MEDIA_DIAGNOSTIC_INVALID")
    return {"stream_index": index, "start_seconds": str(first), "end_seconds": str(last),
            "duration_seconds": str(last - first)}


def _diagnose(kind, facts, path, ffmpeg, runner, output):
    streams = [s for s in facts["streams"] if s["kind"] == ("video" if kind == "black" else "audio")]
    if not streams:
        output.update(status="NOT_APPLICABLE", reason_codes=[])
        return
    try:
        segments = []
        for stream in streams:
            argv = _input(ffmpeg, path, diagnostic=True) + ["-map", f"0:{stream['index']}"]
            argv += (["-vf", "blackdetect=d=0.1:pix_th=0.1:pic_th=0.98"] if kind == "black" else
                     ["-af", "silencedetect=noise=-50dB:d=0.1"])
            argv += ["-threads", "1", "-f", "null", "-"]
            log = runner.run(argv, strict_errors=False).stderr.decode("utf-8", "strict")
            if kind == "black":
                segments.extend(_segment(stream["index"], start, end)
                                for start, end, duration in _BLACK.findall(log))
            else:
                start = None
                for event, timestamp in _SILENCE.findall(log):
                    if event == "start":
                        _require(start is None, "MEDIA_DIAGNOSTIC_INVALID")
                        start = timestamp
                    else:
                        _require(start is not None, "MEDIA_DIAGNOSTIC_INVALID")
                        segments.append(_segment(stream["index"], start, timestamp))
                        start = None
                _require(start is None, "MEDIA_DIAGNOSTIC_INCOMPLETE")
            _require(len(segments) <= 4096, "MEDIA_DIAGNOSTIC_LIMIT")
        output.update(status="OBSERVED", segments=segments, reason_codes=[])
    except (ToolFailure, MediaInspectionError, ValueError, ArithmeticError) as exc:
        output.update(status="UNKNOWN", segments=[],
                      reason_codes=[getattr(exc, "code", "MEDIA_DIAGNOSTIC_INVALID")])


def _status(assets):
    states = {asset["status"] for asset in assets}
    return "FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS"


def _validate_facts(facts):
    _require(Fraction(facts["duration_seconds"]) > 0 and "mp4" in facts["format_names"],
             "INSPECTION_FACTS_INVALID")
    indices = [s["index"] for s in facts["streams"]]
    _require(indices == sorted(set(indices)), "INSPECTION_FACTS_INVALID")
    for stream in facts["streams"]:
        if stream["duration_seconds"] is not None:
            _require(Fraction(stream["duration_seconds"]) > 0, "INSPECTION_FACTS_INVALID")
        if stream["kind"] == "video":
            _require(all(stream[name] is not None for name in ("width", "height", "decoded_frames"))
                     and stream["channels"] is None and stream["sample_rate"] is None,
                     "INSPECTION_FACTS_INVALID")
            if stream["fps"] is not None:
                _require(Fraction(stream["fps"]) > 0, "INSPECTION_FACTS_INVALID")
        else:
            _require(stream["channels"] is not None and stream["sample_rate"] is not None
                     and all(stream[name] is None for name in ("width", "height", "decoded_frames", "fps")),
                     "INSPECTION_FACTS_INVALID")


@dataclass(frozen=True, init=False, slots=True)
class MediaReleaseInspection:
    """An immutable digest-bound receipt, not a statement of external authority."""
    _encoded: bytes = field(repr=False)

    def __init__(self, value: dict):
        try:
            raw = canonical_bytes(value)
            _require(len(raw) <= 8 * 1024 * 1024, "INSPECTION_BYTE_LIMIT")
            _VALIDATOR.validate(value)
            payload = {k: v for k, v in value.items() if k != "record_sha256"}
            _require(value["record_sha256"] == sha256_bytes(canonical_bytes(payload)),
                     "INSPECTION_DIGEST_MISMATCH")
            _require(value["status"] == _status(value["assets"]), "INSPECTION_STATUS_MISMATCH")
            refs = [a["asset_ref"] for a in value["assets"]]
            _require(len(set(refs)) == len(refs) and len(refs) <= value["limits"]["max_assets"],
                     "INSPECTION_ASSET_INDEX_INVALID")
            captured_sizes = [a["size_bytes"] for a in value["assets"] if a["observed_sha256"] is not None]
            _require(all(size is not None and size <= value["limits"]["max_asset_bytes"] for size in captured_sizes)
                     and sum(captured_sizes) <= value["limits"]["max_total_bytes"], "INSPECTION_BYTE_LIMIT")
            for asset in value["assets"]:
                if asset["status"] == "PASS":
                    _relative(asset["asset_ref"])
                    _require(asset["full_decode"] and asset["facts"] is not None
                             and asset["observed_sha256"] == asset["expected_sha256"]
                             and asset["size_bytes"] is not None and asset["size_bytes"] > 0
                             and not asset["reason_codes"] and all(value["tools"].values()),
                             "INSPECTION_PASS_WITHOUT_EVIDENCE")
                    _require(asset["mime_type"] == "video/mp4", "INSPECTION_FACTS_INVALID")
                    if asset["source"]["kind"] == "A06_E01_MANIFEST_HASH_BOUND":
                        _relative(asset["source"]["manifest_ref"])
                        _require(asset["asset_ref"] == str(PurePosixPath(asset["source"]["manifest_ref"]).parent / "video.mp4"),
                                 "INSPECTION_SOURCE_BINDING_INVALID")
                else:
                    _require(not asset["full_decode"] and bool(asset["reason_codes"]),
                             "INSPECTION_INCOMPLETE_WITHOUT_REASON")
                if asset["facts"] is not None:
                    _validate_facts(asset["facts"])
                for kind, diagnostic in asset["diagnostics"].items():
                    requested = value["diagnostics_requested"][kind]
                    _require((diagnostic["status"] != "NOT_REQUESTED") == requested,
                             "INSPECTION_DIAGNOSTIC_REQUEST_MISMATCH")
                    if diagnostic["status"] != "OBSERVED":
                        _require(not diagnostic["segments"], "INSPECTION_UNPROVEN_DIAGNOSTIC")
                    matching = ([] if asset["facts"] is None else
                                [s["index"] for s in asset["facts"]["streams"]
                                 if s["kind"] == ("video" if kind == "black" else "audio")])
                    if diagnostic["status"] == "OBSERVED":
                        _require(bool(matching) and not diagnostic["reason_codes"], "INSPECTION_UNPROVEN_DIAGNOSTIC")
                    elif diagnostic["status"] == "NOT_APPLICABLE":
                        _require(asset["facts"] is not None and not matching and not diagnostic["reason_codes"],
                                 "INSPECTION_UNPROVEN_DIAGNOSTIC")
                    elif diagnostic["status"] == "UNKNOWN":
                        _require(bool(diagnostic["reason_codes"]), "INSPECTION_UNPROVEN_DIAGNOSTIC")
                    else:
                        _require(not diagnostic["reason_codes"], "INSPECTION_UNPROVEN_DIAGNOSTIC")
                    for segment in diagnostic["segments"]:
                        _require(Fraction(segment["end_seconds"]) - Fraction(segment["start_seconds"])
                                 == Fraction(segment["duration_seconds"]) >= 0
                                 and segment["stream_index"] in matching, "INSPECTION_DIAGNOSTIC_INVALID")
            object.__setattr__(self, "_encoded", raw)
        except MediaInspectionError:
            raise
        except (ValidationError, ValueError, TypeError, ArithmeticError, RecursionError) as exc:
            raise MediaInspectionError("INSPECTION_SCHEMA_INVALID") from exc

    @classmethod
    def from_bytes(cls, raw: bytes) -> MediaReleaseInspection:
        _require(type(raw) is bytes and 0 < len(raw) <= 8 * 1024 * 1024, "INSPECTION_BYTE_LIMIT")
        try:
            return cls(strict_json(raw))
        except (ManifestValidationError, RecursionError) as exc:
            raise MediaInspectionError("INSPECTION_JSON_INVALID") from exc

    def to_dict(self) -> dict:
        return strict_json(self._encoded)

    def to_bytes(self) -> bytes:
        return self._encoded

    @property
    def status(self) -> str:
        return self.to_dict()["status"]


class _Inspection:
    def __init__(self, root, options, limits):
        self.root, self.options, self.limits = root, options, limits
        self.consumed = 0
        self.tools = {"ffprobe": None, "ffmpeg": None}
        self.paths = None
        self.tool_failure = None

    def resolve_tools(self, runner):
        if self.tool_failure:
            raise self.tool_failure
        if self.paths is None:
            paths = {name: shutil.which(name) for name in self.tools}
            try:
                if not all(paths.values()):
                    raise ToolFailure("MEDIA_TOOL_UNAVAILABLE")
                for name, executable in paths.items():
                    line = runner.run([executable, "-version"]).stdout.decode("utf-8", "strict").splitlines()[0]
                    if not line.startswith(name + " version ") or len(line) > 1024:
                        raise ToolFailure("MEDIA_TOOL_IDENTITY_UNKNOWN")
                    self.tools[name] = line
            except (IndexError, UnicodeError) as exc:
                self.tool_failure = ToolFailure("MEDIA_TOOL_IDENTITY_UNKNOWN")
                raise self.tool_failure from exc
            except ToolFailure as exc:
                # Failure of -version is an environment problem, not bad media.
                self.tool_failure = ToolFailure(exc.code)
                raise self.tool_failure from exc
            self.paths = paths
        return self.paths

    def asset(self, asset):
        result = {"asset_ref": asset.asset_ref, "expected_sha256": asset.expected_sha256,
                  "mime_type": asset.mime_type, "observed_sha256": None, "size_bytes": None,
                  "status": "UNKNOWN", "reason_codes": [], "full_decode": False, "facts": None,
                  "diagnostics": {k: _diagnostic(k, getattr(self.options, k)) for k in ("black", "silence")},
                  "source": {"kind": "LOCAL_EXPECTED_HASH", "custody_verification": "NOT_PERFORMED"}}
        try:
            path = _local(self.root, asset.asset_ref)
            source = _source(self.root, asset)
            with tempfile.TemporaryDirectory(prefix="a13-c08-") as directory:
                directory = Path(directory)
                captured = directory / "asset.mp4"
                with media_files.open_regular(path) as stream:
                    limit = min(self.limits.max_asset_bytes, self.limits.max_total_bytes - self.consumed)
                    if limit <= 0:
                        raise MediaInspectionError("MEDIA_BYTE_LIMIT", "UNKNOWN")
                    with captured.open("xb") as target:
                        before = _copy_bounded(stream, target, limit)
                    self.consumed += before[1]
                    result.update(observed_sha256=before[0], size_bytes=before[1])
                    _require(before[0] == asset.expected_sha256, "MEDIA_SHA256_MISMATCH")
                    _require(before[1] > 0, "MEDIA_EMPTY")
                    try:
                        if asset.mime_type != "video/mp4":
                            raise MediaInspectionError("MEDIA_CONTAINER_UNSUPPORTED", "UNKNOWN")
                        # Reuse A04/A05's structural MP4 parser without its
                        # render-plan restrictions or unbounded disk-spooled tools.
                        with captured.open("rb") as content:
                            media_files._mp4_boxes(content, before[1])
                        runner = BoundedToolRunner(directory, timeout_seconds=self.limits.tool_timeout_seconds,
                                                   max_output_bytes=self.limits.max_tool_output_bytes)
                        tools = self.resolve_tools(runner)
                        probe = runner.run([
                            tools["ffprobe"], "-v", "error", "-max_alloc", "134217728",
                            "-threads", "1", "-protocol_whitelist", "file",
                            "-enable_drefs", "0", "-f", "mov", "-count_frames", "-show_entries",
                            "format=format_name,duration,size:stream=index,codec_type,codec_name,width,height,"
                            "avg_frame_rate,duration,nb_read_frames,sample_rate,channels",
                            "-of", "json", "-i", str(captured),
                        ])
                        result["facts"] = _observe(probe.stdout, before[1])
                        decoded = runner.run(_input(tools["ffmpeg"], captured) + [
                            "-progress", "pipe:1", "-map", "0:v?", "-map", "0:a?", "-threads", "1", "-f", "null", "-",
                        ])
                        try:
                            progress = dict(line.split("=", 1) for line in decoded.stdout.decode("utf-8").splitlines()
                                            if "=" in line)
                            frames = int(progress.get("frame", "0"))
                        except (UnicodeError, ValueError) as exc:
                            raise MediaInspectionError("MEDIA_DECODE_EVIDENCE_INVALID", "UNKNOWN") from exc
                        _require(progress.get("progress") == "end", "MEDIA_DECODE_INCOMPLETE")
                        videos = [s for s in result["facts"]["streams"] if s["kind"] == "video"]
                        if videos:
                            _require(frames == videos[0]["decoded_frames"],
                                     "MEDIA_DECODE_FRAME_MISMATCH")
                        for kind in ("black", "silence"):
                            if getattr(self.options, kind):
                                _diagnose(kind, result["facts"], captured, tools["ffmpeg"], runner,
                                          result["diagnostics"][kind])
                    finally:
                        try:
                            stream.seek(0)
                            after_source = _copy_bounded(stream, None, limit)
                            with media_files.open_regular(captured) as content:
                                after_capture = _copy_bounded(content, None, limit)
                            _require(after_source == before and after_capture == before,
                                     "MEDIA_CHANGED_DURING_INSPECTION")
                        except (MediaInspectionError, MediaValidationError) as exc:
                            raise MediaInspectionError("MEDIA_CHANGED_DURING_INSPECTION") from exc
                        _require(_source(self.root, asset) == source, "E01_MANIFEST_CHANGED")
                result.update(status="PASS", full_decode=True, source=source)
        except (MediaInspectionError, ToolFailure) as exc:
            result.update(status=exc.status, reason_codes=[exc.code], full_decode=False)
        except MediaValidationError:
            result.update(status="FAIL", reason_codes=["MEDIA_INVALID_OR_CHANGED"], full_decode=False)
        except OSError:
            result.update(status="UNKNOWN", reason_codes=["MEDIA_LOCAL_IO_FAILED"], full_decode=False)
        return result


def inspect_media_release(root: str | Path, assets: list[ReleaseAsset] | tuple[ReleaseAsset, ...], *,
                          diagnostics: DiagnosticOptions | None = None,
                          limits: InspectionLimits | None = None) -> MediaReleaseInspection:
    """Inspect exactly the requested local files; no discovery, platform or network calls."""
    limits, diagnostics = limits or InspectionLimits(), diagnostics or DiagnosticOptions()
    _require(isinstance(limits, InspectionLimits) and isinstance(diagnostics, DiagnosticOptions),
             "INVALID_INSPECTION_OPTIONS")
    _require(isinstance(assets, (list, tuple)) and 0 < len(assets) <= limits.max_assets,
             "RELEASE_ASSETS_REQUIRED_OR_TOO_MANY")
    _require(all(isinstance(asset, ReleaseAsset) for asset in assets), "RELEASE_ASSET_REQUIRED")
    _require(len({a.asset_ref for a in assets}) == len(assets), "DUPLICATE_RELEASE_ASSET")
    context = _Inspection(Path(root).absolute(), diagnostics, limits)
    results = [context.asset(asset) for asset in assets]
    payload = {"schema_version": "MediaReleaseInspection.v1", "inspector_version": "a13-c08/1.0",
               "scope": "COMMERCIAL_PLACEMENT_TECHNICAL_INSPECTION_ONLY", "status": _status(results),
               "limits": asdict(limits), "diagnostics_requested": asdict(diagnostics),
               "tools": context.tools, "assets": results,
               "rights_status": "UNKNOWN", "account_eligibility": "UNKNOWN", "link_activation": "UNKNOWN",
               "publication_status": "NOT_ATTEMPTED"}
    return MediaReleaseInspection({**payload, "record_sha256": sha256_bytes(canonical_bytes(payload))})
