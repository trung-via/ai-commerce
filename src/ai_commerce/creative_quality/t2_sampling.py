"""Explicit offline Q4 T2 probes over a locked, hash-pinned A13 snapshot.

Only full-frame grayscale and mono PCM statistics are measured. No model,
transcription, perceptual verdict, rights ledger or release gate exists here.
Windows share-mode locking is required: unsupported hosts return UNKNOWN.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative_quality import critic, inspection_evidence as t1


PROFILE = "bq02b-t2/gray64-pcm8k-quarter/v1"
DETECTOR = "integer-pixel-pcm-statistics/1.0"
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
TIME_UNCERTAINTY = "SEEK_OUTPUT_MAY_BE_APPROXIMATE; SOURCE_PTS_AND_SOURCE_INDEX_NOT_MEASURED"
COUNTEREXAMPLE = "Intentional black, white, silence or rapid differences can serve fiction or an edit; statistics do not establish a defect."
LIMITS = {"aesthetics": "UNKNOWN", "physics": "UNKNOWN", "object_identity": "UNKNOWN",
          "anatomy": "UNKNOWN", "emotion": "UNKNOWN", "brand": "UNKNOWN", "edit_quality": "UNKNOWN",
          "visible_words": "NOT_RUN", "pronunciation": "NOT_RUN", "language": "NOT_RUN",
          "speaker": "NOT_RUN", "voice_naturalness": "NOT_RUN", "lip_sync": "NOT_RUN", "rights": "UNKNOWN"}


def _times(values, maximum):
    if type(values) is not tuple or not 1 <= len(values) <= maximum:
        raise critic.Error("bounded nonempty tuple of rational timestamps required")
    result = []
    for value in values:
        try:
            number = Fraction(a13._fraction(value))
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise critic.Error("bounded rational timestamp required") from exc
        if not 0 <= number <= 86400:
            raise critic.Error("timestamp outside bounded nonnegative day")
        result.append(number)
    if result != sorted(set(result)):
        raise critic.Error("timestamps must be sorted and unique")
    return tuple(str(value) for value in result)


@dataclass(frozen=True, slots=True)
class SamplingRequest:
    asset_ref: str
    asset_sha256: str
    frame_times: tuple[str, ...] = ("0",)
    audio_times: tuple[str, ...] = ("0",)

    def __post_init__(self):
        try:
            a13.ReleaseAsset(self.asset_ref, self.asset_sha256)
            a13._relative(self.asset_ref)
        except a13.MediaInspectionError as exc:
            raise critic.Error(exc.code) from exc
        object.__setattr__(self, "frame_times", _times(self.frame_times, 8))
        object.__setattr__(self, "audio_times", _times(self.audio_times, 2))

    def to_dict(self):
        return {"asset_ref": self.asset_ref, "asset_sha256": self.asset_sha256,
                "frame_times": list(self.frame_times), "audio_times": list(self.audio_times)}

    @classmethod
    def from_dict(cls, value):
        critic._canonical(value)
        if type(value) is not dict or set(value) != {"asset_ref", "asset_sha256", "frame_times", "audio_times"}:
            raise critic.Error("closed sampling request required")
        if any(type(value[k]) is not list for k in ("frame_times", "audio_times")):
            raise critic.Error("timestamp arrays required")
        return cls(value["asset_ref"], value["asset_sha256"], tuple(value["frame_times"]), tuple(value["audio_times"]))


def _request(value):
    if type(value) is not SamplingRequest:
        raise critic.Error("native SamplingRequest required")
    return SamplingRequest.from_dict(value.to_dict())


def _seek(value):
    # FFmpeg accepts decimal seconds, not Fraction's slash form. Floor to ns;
    # retain both the requested rational and the actual command argument.
    number = Fraction(value)
    nanos = number.numerator * 10**9 // number.denominator
    return f"{nanos // 10**9}.{nanos % 10**9:09d}"


@contextmanager
def _snapshot_lock(path):
    """Deny write/delete/rename for the entire A13 + T2 tool lifetime.

    chmod or an advisory POSIX lock would leave a mutable-path race. Do not
    claim an immutable observation on a platform without this implementation.
    """
    if os.name != "nt":
        raise a13.MediaInspectionError("SNAPSHOT_LOCK_UNAVAILABLE", "UNKNOWN")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    # GENERIC_READ, FILE_SHARE_READ only, OPEN_EXISTING, OPEN_REPARSE_POINT.
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise a13.MediaInspectionError("SNAPSHOT_LOCK_FAILED", "UNKNOWN")
    try:
        with a13.media_files.open_regular(path) as stream:
            yield stream
    finally:
        close(handle)


@contextmanager
def _capture(root, request, limit):
    path = a13._local(root, request.asset_ref)
    with a13.media_files.open_regular(path) as source:
        # An unpredictable user-root scratch directory, never the original
        # mutable filename, is supplied to either inspector or decoder.
        with tempfile.TemporaryDirectory(prefix="bq02b-snapshot-", dir=root) as directory:
            private = Path(directory)
            captured = private.joinpath(*a13._relative(request.asset_ref).parts)
            captured.parent.mkdir(parents=True, exist_ok=True)
            with captured.open("xb") as target:
                before = a13._copy_bounded(source, target, limit)
            if before[0] != request.asset_sha256 or before[1] == 0:
                raise a13.MediaInspectionError("SNAPSHOT_SHA256_MISMATCH", "UNKNOWN")
            with _snapshot_lock(captured) as sealed:
                if a13._copy_bounded(sealed, None, limit) != before:
                    raise a13.MediaInspectionError("SNAPSHOT_CHANGED", "UNKNOWN")
                yield private, captured, before
                sealed.seek(0)
                if a13._copy_bounded(sealed, None, limit) != before:
                    raise a13.MediaInspectionError("SNAPSHOT_CHANGED", "UNKNOWN")
            source.seek(0)
            if a13._copy_bounded(source, None, limit) != before:
                raise a13.MediaInspectionError("SOURCE_CHANGED_DURING_CAPTURE", "UNKNOWN")


def _asset(source, request):
    data = t1.validate_evidence(source).to_dict()
    report = data["a13_report"]
    if report is None:
        raise a13.MediaInspectionError("NATIVE_T1_LOCAL_BYTES_REQUIRED", "UNKNOWN")
    rows = [a for a in report["assets"] if a["asset_ref"] == request.asset_ref]
    if len(rows) != 1:
        raise a13.MediaInspectionError("T1_ASSET_REF_MISMATCH", "UNKNOWN")
    row = rows[0]
    if not (row["status"] == "PASS" and row["full_decode"] is True
            and row["expected_sha256"] == row["observed_sha256"] == request.asset_sha256):
        raise a13.MediaInspectionError("T1_BYTES_NOT_PROVEN", "UNKNOWN")
    return report, row


def _time(value, ordinal):
    return {"requested_seconds": value, "seek_argument_seconds": _seek(value),
            "binding": "REQUESTED_TIME", "sample_output_index": ordinal,
            "observed_source_frame_index": None, "observed_source_pts_seconds": None,
            "uncertainty": TIME_UNCERTAINTY}


def _probes(path, private, request, row, tools):
    facts = row["facts"]
    videos = [s for s in facts["streams"] if s["kind"] == "video"]
    audios = [s for s in facts["streams"] if s["kind"] == "audio"]
    if not videos:
        raise a13.MediaInspectionError("VIDEO_STREAM_MISSING", "UNKNOWN")
    video = videos[0]
    duration = Fraction(video["duration_seconds"] or facts["duration_seconds"])
    if any(Fraction(t) >= duration for t in request.frame_times):
        raise critic.Error("frame timestamp outside A13 observed video duration")
    if audios:
        audio_duration = Fraction(audios[0]["duration_seconds"] or facts["duration_seconds"])
        if any(Fraction(t) + Fraction(1, 4) > audio_duration for t in request.audio_times):
            raise critic.Error("PCM window outside A13 observed audio duration")
    runner = a13.BoundedToolRunner(private, timeout_seconds=10, max_output_bytes=65536)
    frames, pcm, previous = [], [], None
    for ordinal, timestamp in enumerate(request.frame_times):
        argv = a13._input(tools["ffmpeg"], path) + [
            "-ss", _seek(timestamp), "-map", f"0:{video['index']}", "-frames:v", "1",
            "-an", "-sn", "-dn", "-vf", "scale=64:64:flags=area,format=gray", "-pix_fmt", "gray",
            "-threads", "1", "-f", "rawvideo", "pipe:1"]
        raw = runner.run(argv).stdout
        if len(raw) != 4096:
            raise a13.MediaInspectionError("FRAME_SAMPLE_SIZE_INVALID", "UNKNOWN")
        frames.append({"state": "OBSERVED", "stream_index": video["index"], "region": "FULL_FRAME",
                       "time": _time(timestamp, ordinal), "width": 64, "height": 64,
                       "pixel_format": "gray8", "sample_sha256": hashlib.sha256(raw).hexdigest(),
                       "minimum": min(raw), "maximum": max(raw), "mean_luma": str(Fraction(sum(raw), len(raw))),
                       "mean_absolute_difference_previous": None if previous is None else
                       str(Fraction(sum(abs(a-b) for a, b in zip(raw, previous)), len(raw))),
                       "confidence": "MEASURED_SIGNAL_ONLY", "counterexample": COUNTEREXAMPLE})
        previous = raw
    if audios:
        for ordinal, timestamp in enumerate(request.audio_times):
            argv = a13._input(tools["ffmpeg"], path) + [
                "-ss", _seek(timestamp), "-t", "0.25", "-map", f"0:{audios[0]['index']}",
                "-vn", "-sn", "-dn", "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le",
                "-threads", "1", "-f", "s16le", "pipe:1"]
            raw = runner.run(argv).stdout
            if not 0 < len(raw) <= 4000 or len(raw) % 2:
                raise a13.MediaInspectionError("PCM_SAMPLE_SIZE_INVALID", "UNKNOWN")
            samples = [v[0] for v in struct.iter_unpack("<h", raw)]
            squared = sum(v*v for v in samples)
            pcm.append({"state": "OBSERVED", "stream_index": audios[0]["index"], "region": "MONO_MIXDOWN",
                        "time": _time(timestamp, ordinal), "requested_duration_seconds": "1/4",
                        "observed_sample_count": len(samples), "decoded_duration_seconds": str(Fraction(len(samples), 8000)),
                        "sample_rate_hz": 8000, "channels": 1, "sample_format": "s16le",
                        "sample_sha256": hashlib.sha256(raw).hexdigest(), "absolute_peak": max(map(abs, samples)),
                        "mean_square": str(Fraction(squared, len(samples))), "rms_integer_floor": math.isqrt(squared // len(samples)),
                        "confidence": "MEASURED_SIGNAL_ONLY", "counterexample": COUNTEREXAMPLE})
    return frames, pcm, "OBSERVED" if audios else "NOT_APPLICABLE"


def _payload(source, request, status, reasons, *, frames=(), audio=(), audio_status="UNKNOWN", reinspection=None):
    return {"schema_version": "BQ02bT2SelectiveEvidence.v1", "technical_evidence": source.to_dict(),
            "technical_evidence_digest": source.digest, "a13_record_sha256": source.to_dict()["a13_record_sha256"],
            "request": request.to_dict(), "asset_ref": request.asset_ref, "expected_asset_sha256": request.asset_sha256,
            "observed_asset_sha256": request.asset_sha256 if status == "OBSERVED" else None,
            "status": status, "reason_codes": sorted(set(reasons)), "frames": list(frames), "audio": list(audio),
            "audio_status": audio_status, "snapshot_reinspection": reinspection,
            "snapshot_provenance": "LOCKED_SAME_BYTES_A13_AND_T2" if status == "OBSERVED" else "NOT_ESTABLISHED",
            "sampling_profile": PROFILE, "detector_version": DETECTOR,
            "coverage": {"T1": source.to_dict()["coverage"]["T1"], "T2": "SELECTIVE_DETERMINISTIC_ONLY" if status == "OBSERVED" else status,
                         "video_streams": "FIRST_VIDEO_ONLY", "audio_streams": "FIRST_AUDIO_MONO_ONLY",
                         "OCR": "NOT_RUN", "ASR": "NOT_RUN", "T3": "NOT_RUN"},
            "limits_of_evidence": LIMITS, "authority": critic.AUTHORITY}


@dataclass(frozen=True, slots=True, init=False)
class BQ02bT2SelectiveEvidence(critic._Record):
    _source: t1.BQ02aT1TechnicalEvidence
    _authenticated_json: str
    SCHEMA = "BQ02bT2SelectiveEvidence.v1"

    def __init__(self, raw):
        raise critic.Error("use sample_local_t2, not_sampled_t2 or explicit local replay")

    def _validate(self, value):
        t1.validate_evidence(self._source)
        if critic._canonical(value) != self._authenticated_json or value["technical_evidence_digest"] != self._source.digest:
            raise critic.Error("T2 differs from authenticated local snapshot result")

    @classmethod
    def from_dict(cls, value, *, root=None):
        data = critic._read(critic._canonical(value))
        if type(data) is not dict or not {"technical_evidence", "request", "status"} <= set(data):
            raise critic.Error("T2 record required")
        # Even UNKNOWN persisted records can contain a native T1 observation.
        source = t1.BQ02aT1TechnicalEvidence.from_dict(data["technical_evidence"], root=root)
        request = SamplingRequest.from_dict(data["request"])
        if data["status"] == "NOT_RUN":
            observed = not_sampled_t2(source, request)
        else:
            if not isinstance(root, Path):
                raise critic.Error("LOCAL_SNAPSHOT_REINSPECTION_REQUIRED: explicit Path root")
            observed = sample_local_t2(source, request, root=root, allow_local_probe=True)
        if observed.to_dict() != data:
            raise critic.Error("local T2 replay differs from supplied record")
        return observed

    @classmethod
    def from_json(cls, raw, *, root=None):
        return cls.from_dict(critic._read(raw), root=root)


def _bound(data, source):
    # This pure helper is deliberately incapable of minting observations,
    # even with self-rehashed JSON and a fabricated native T1 placeholder.
    # Only the successful locked local sampling block constructs that type.
    if (data["status"] not in ("UNKNOWN", "NOT_RUN") or data["frames"] or data["audio"]
            or data["observed_asset_sha256"] is not None or data["snapshot_reinspection"] is not None):
        raise critic.Error("observed T2 requires actual locked local sampling")
    result = object.__new__(BQ02bT2SelectiveEvidence)
    object.__setattr__(result, "_source", source)
    object.__setattr__(result, "_authenticated_json", critic._canonical(data))
    critic._Record.__init__(result, critic._canonical(data))
    return result


def validate_evidence(value):
    if type(value) is not BQ02bT2SelectiveEvidence:
        raise critic.Error("native T2 snapshot evidence required")
    value._validate(value.to_dict())
    return value


def not_sampled_t2(technical_evidence, request):
    source, request = t1.validate_evidence(technical_evidence), _request(request)
    return _bound(_payload(source, request, "NOT_RUN", ["LOCAL_PROBE_NOT_AUTHORIZED"], audio_status="NOT_RUN"), source)


def sample_local_t2(technical_evidence, request, *, root, allow_local_probe=False):
    """Opt-in I/O only. Missing proof/tools/decoding yields UNKNOWN, retaining T1.

    Invalid caller requests raise Error. No caller runner, argv, executable,
    region, output finding or provider can be injected through this interface.
    """
    source, request = t1.validate_evidence(technical_evidence), _request(request)
    if type(allow_local_probe) is not bool:
        raise critic.Error("explicit boolean local probe opt-in required")
    if not allow_local_probe:
        return not_sampled_t2(source, request)
    if not isinstance(root, Path) or not root.is_absolute():
        raise critic.Error("explicit absolute user-owned temporary Path root required")
    try:
        original, row = _asset(source, request)
        limit = min(MAX_SNAPSHOT_BYTES, original["limits"]["max_asset_bytes"])
        with _capture(root, request, limit) as (private, captured, fingerprint):
            # A13 verifies the very same locked snapshot, not the original.
            report = a13.inspect_media_release(private, [a13.ReleaseAsset(request.asset_ref, request.asset_sha256)],
                diagnostics=a13.DiagnosticOptions(**original["diagnostics_requested"]),
                limits=a13.InspectionLimits(**original["limits"]))
            verified = t1._validated_report(report.to_dict()).to_dict()
            actual = verified["assets"][0]
            # A06 custody stays in the original T1; this reinspection proves
            # bytes/technical facts only and does not duplicate its ledger.
            fields = set(row) - {"source"}
            if (verified["status"] != "PASS" or verified["tools"] != original["tools"]
                    or any(actual[k] != row[k] for k in fields)
                    or actual["observed_sha256"] != fingerprint[0]):
                raise a13.MediaInspectionError("A13_SNAPSHOT_REINSPECTION_DISAGREES", "UNKNOWN")
            tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
            if not all(tools.values()):
                raise a13.ToolFailure("MEDIA_TOOL_UNAVAILABLE")
            frames, audio, audio_status = _probes(captured, private, request, actual, tools)
        # Context exit verifies source/captured handles; only now bind OBSERVED.
        data = _payload(source, request, "OBSERVED", [], frames=frames, audio=audio,
                        audio_status=audio_status, reinspection=verified)
        result = object.__new__(BQ02bT2SelectiveEvidence)
        object.__setattr__(result, "_source", source)
        object.__setattr__(result, "_authenticated_json", critic._canonical(data))
        critic._Record.__init__(result, critic._canonical(data))
        return result
    except (a13.MediaInspectionError, a13.ToolFailure) as exc:
        reason = exc.code
    except (a13.MediaValidationError, OSError):
        reason = "LOCAL_PATH_OR_SNAPSHOT_UNSAFE"
    return _bound(_payload(source, request, "UNKNOWN", [reason]), source)
