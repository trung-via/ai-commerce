"""Disposable Windows synthetic Q5 experiment; no production or provider input.

The capability, BEFORE observation and execution witness live only within one
context. Persisted JSON can describe an attempt but cannot authorize or replay
its effects. All tool inputs are fixed and all outputs are bounded local bytes.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import uuid

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative_quality import critic, inspection_evidence as t1, quality_review as qr
from ai_commerce.creative_quality import multimodal_review as mm, t2_sampling as t2
from ai_commerce.creative_quality import repair_policy as policy, repair_preservation as preservation

SCHEMA = "BQ04bOfflineAttemptReceipt.v1"
PROFILE = "bq04b-white-slate-4fps/v1"
OPERATION = "OFFLINE_DETERMINISTIC_FIXTURE_LUMA"
SUPPORTED_OPERATIONS = ("NONE", OPERATION, "OFFLINE_DETERMINISTIC_TRIM",
                        "OFFLINE_DETERMINISTIC_CROP", "OFFLINE_DETERMINISTIC_AUDIO_NORMALIZE")
CONTEXT = {"synthetic_fixture_profile": PROFILE}
TIMES = ("0", "1/4", "1/2", "3/4", "1", "5/4", "3/2", "7/4")
SCOPE = {"shot_id": None, "time_range": [0.5, 1.0],
         "spatial_region": [0.0, 0.0, 1.0, 1.0], "target_type": "SYNTHETIC_LUMA"}
ORACLE = {"oracle_type": "SYNTHETIC_WHITE_SLATE_V1",
          "expected_verification": "Q4_AND_ALL_FRAME_REINSPECTION"}
_SESSIONS = {}


class ParentBindingError(critic.Error):
    """The decision, plan or original media pins disagree."""


class UnsafePathError(critic.Error):
    def __init__(self, code):
        self.code = code
        super().__init__("unsafe sandbox path: " + code)


def _reject_metadata(info, *, directory=False, regular=False):
    """One fail-closed gate for native junctions AND symlink-tagged metadata.

    Reject every reparse tag, including unknown tags and inconsistent metadata
    with a tag but no REPARSE_POINT attribute. Never dereference to classify it.
    """
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or getattr(info, "st_reparse_tag", 0)):
        raise UnsafePathError("LINK_OR_REPARSE")
    if directory and not stat.S_ISDIR(info.st_mode):
        raise UnsafePathError("ANCESTOR_NOT_DIRECTORY")
    if regular:
        if not stat.S_ISREG(info.st_mode):
            raise UnsafePathError("NOT_REGULAR")
        if info.st_nlink != 1:
            raise UnsafePathError("LINK_MULTIPLICITY")


def _validate_safe_path(root, target_path, *, must_exist=False):
    if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts:
        raise UnsafePathError("ABSOLUTE_LOCAL_ROOT_REQUIRED")
    path = Path(target_path)
    if (not path.is_absolute() or str(path).startswith(("\\\\", "//"))
            or str(root).startswith(("\\\\", "//"))):
        raise UnsafePathError("ABSOLUTE_LOCAL_PATH_REQUIRED")
    try:
        a13._relative(path.relative_to(root).as_posix())
        for ancestor in reversed(path.parents):
            _reject_metadata(ancestor.lstat(), directory=True)
        try:
            info = path.lstat()
        except FileNotFoundError:
            if must_exist:
                raise
        else:
            _reject_metadata(info, regular=True)
    except UnsafePathError:
        raise
    except (OSError, ValueError, a13.MediaInspectionError) as exc:
        raise UnsafePathError("LEXICAL_OR_METADATA_INVALID") from exc
    return path


def _kernel():
    if os.name != "nt":
        raise critic.Error("Windows share-mode locks required")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


@contextmanager
def _directory_lock(path):
    import ctypes
    from ctypes import wintypes
    kernel = _kernel()
    # Read access participates in sharing checks. Deny write/delete on each
    # ancestor, and open the reparse object itself rather than following it.
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise critic.Error("sandbox ancestor lock failed")
    try:
        class AttributeTag(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]
        info = AttributeTag()
        query = kernel.GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        if not query(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise UnsafePathError("DIRECTORY_HANDLE_METADATA_UNPROVEN")
        if info.attributes & 0x400 or info.tag:
            raise UnsafePathError("LINK_OR_REPARSE")
        _reject_metadata(path.lstat(), directory=True)
        yield
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _paths_locked(root, *paths):
    # Inspect ALL paths before opening any media, including the original.
    for path in paths:
        _validate_safe_path(root, path, must_exist=True)
    directories = set((root, *root.parents))
    for path in paths:
        directories.update(path.parents)
    with ExitStack() as stack:
        for directory in sorted(directories, key=lambda p: (len(p.parts), str(p))):
            stack.enter_context(_directory_lock(directory))
        for path in paths:
            _validate_safe_path(root, path, must_exist=True)
            stack.enter_context(t2._snapshot_lock(path))
            _validate_safe_path(root, path, must_exist=True)
        yield
        for path in paths:
            _validate_safe_path(root, path, must_exist=True)


@contextmanager
def _exclusive_output(path):
    import ctypes
    import msvcrt
    kernel = _kernel()
    handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 1, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise critic.Error("exclusive output creation failed; no overwrite")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(fd, "w+b") as stream:
        _reject_metadata(os.fstat(stream.fileno()), regular=True)
        yield stream
        stream.flush()
        _reject_metadata(os.fstat(stream.fileno()), regular=True)


def _hash(path):
    with a13.media_files.open_regular(path) as stream:
        return a13._copy_bounded(stream, None, t2.MAX_SNAPSHOT_BYTES)[0]


def _tools():
    result = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    if any(not tool or not Path(tool).is_absolute() or Path(tool).suffix.lower() != ".exe"
           for tool in result.values()):
        raise a13.ToolFailure("MEDIA_TOOL_UNAVAILABLE")
    return result


def _runner(root):
    return a13.BoundedToolRunner(root, timeout_seconds=20, max_output_bytes=262144)


class FixtureSession:
    __slots__ = ()

    def __init__(self):
        raise critic.Error("synthetic_fixture mints sessions; no caller capabilities")

    @property
    def root(self):
        return _session(self)["root"]


def _session(fixture):
    state = _SESSIONS.get(id(fixture))
    if type(fixture) is not FixtureSession or state is None or state["token"] is not fixture:
        raise critic.Error("live native synthetic fixture required")
    return state


@contextmanager
def synthetic_fixture(*, scratch_root):
    scratch_root = Path(scratch_root)
    if (not scratch_root.is_absolute() or ".." in scratch_root.parts
            or str(scratch_root).startswith(("\\\\", "//"))):
        raise critic.Error("test-owned absolute local scratch outside source required")
    # Reject a linked or lexical-alias scratch ancestor before mkdir/tool I/O.
    a13._relative(scratch_root.relative_to(scratch_root.anchor).as_posix())
    for directory in (*reversed(scratch_root.parents), scratch_root):
        _reject_metadata(directory.lstat(), directory=True)
    if (not scratch_root.is_dir()
            or any((p / ".git").exists() for p in (scratch_root, *scratch_root.parents))):
        raise critic.Error("test-owned scratch outside source required")
    tools = _tools()
    with tempfile.TemporaryDirectory(prefix="bq04b-r11-", dir=scratch_root) as temporary:
        root = Path(temporary)
        token = object.__new__(FixtureSession)
        state = dict(token=token, root=root, attempt_id=uuid.uuid4().hex,
            original=None, before=None, receipts={}, operation_state="READY", gate=threading.Lock())
        _SESSIONS[id(token)] = state
        try:
            path = root / "fixture.mp4"
            with _paths_locked(root):
                argv = [tools["ffmpeg"], "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
                    "testsrc2=size=64x64:rate=4:duration=2,drawbox=color=black:t=fill:enable='gte(t,0.5)*lt(t,1)'",
                    "-an", "-c:v", "libx264", "-qp", "0", "-g", "1", "-bf", "0",
                    "-pix_fmt", "yuv420p", "-threads", "1", "-movflags", "frag_keyframe+empty_moov",
                    "-f", "mp4", "pipe:1"]
                raw = _runner(root).run(argv).stdout
                with _exclusive_output(path) as output:
                    if not raw or output.write(raw) != len(raw):
                        raise critic.Error("empty/short fixture")
                state["original"] = _hash(path)
            yield token
        finally:
            _SESSIONS.pop(id(token), None)


def validate_review(review, expected_digest):
    if type(review) is not mm.BQ02bMultimodalReview:
        raise critic.Error("native BQ02bMultimodalReview required")
    review.verify_digest(expected_digest)
    review._validate(review.to_dict())
    return review.to_dict()


def inspect_fixture(fixture, *, preflight, output=False):
    state = _session(fixture)
    if type(output) is not bool or type(preflight) is not critic.BQ02aPreflightCritique:
        raise critic.Error("native preflight and boolean output selector required")
    preflight = critic.BQ02aPreflightCritique.from_dict(preflight.to_dict())
    name = "repaired.mp4" if output else "fixture.mp4"
    path, root = state["root"] / name, state["root"]
    with _paths_locked(root, path):
        sha = _hash(path)
        if not output and sha != state["original"]:
            raise critic.Error("fixture source bytes changed")
        source = t1.inspect_local_t1(root, [a13.ReleaseAsset(name, sha)],
                                    diagnostics=a13.DiagnosticOptions(black=True))
        sampled = t2.sample_local_t2(source, t2.SamplingRequest(name, sha, TIMES),
                                     root=root, allow_local_probe=True)
        if source.to_dict()["status"] != "PASS" or sampled.to_dict()["status"] != "OBSERVED":
            raise critic.Error("genuine current A13/Q4 measurements required")
        original = qr.adversarial_review(preflight, preflight_digest=preflight.digest,
            technical_evidence=source, technical_evidence_digest=source.digest)
        review = mm.multimodal_review(original, sampled,
            original_review_digest=original.digest, t2_digest=sampled.digest)
        if _hash(path) != sha:
            raise critic.Error("fixture changed during inspection")
    if not output:
        state["before"] = review
    return review


def fixture_parents(fixture, before):
    state = _session(fixture)
    if state["before"] is not before:
        raise critic.Error("fixture-bound Q4 BEFORE required")
    decision = policy.decide_repair(before, review_digest=before.digest,
        asset_sha256=state["original"], context=CONTEXT)
    plan = preservation.create_preservation_plan(decision, decision_digest=decision.digest,
        target_scope=SCOPE, test_oracle=ORACLE, editor=OPERATION)
    return decision, plan


def _parents(decision, plan, decision_digest, plan_digest, decision_context=None, preservation_context=None):
    if type(decision) is not policy.BQ04aRepairDecision or type(plan) is not preservation.BQ04aPreservationPlan:
        raise ParentBindingError("native BQ04a decision and preservation plan required")
    decision.verify_digest(decision_digest)
    plan.verify_digest(plan_digest)
    d, p = decision.to_dict(), plan.to_dict()
    if p["decision_digest"] != decision.digest or p["original_asset_sha256"] != d["original_asset_sha256"]:
        raise ParentBindingError("decision/plan parent or original SHA mismatch")
    policy.BQ04aRepairDecision.from_dict(d, review=decision._review,
        study_readiness=decision._readiness, context=decision_context)
    preservation.BQ04aPreservationPlan.from_dict(p, decision=decision, context=preservation_context)
    return d, p


@dataclass(frozen=True, slots=True, init=False)
class BQ04bOfflineAttemptReceipt(critic._Record):
    _sealed: str
    _fixture: object
    SCHEMA = SCHEMA

    def __init__(self, raw):
        raise critic.Error("use assess_offline_attempt or execute_offline_repair")

    def _validate(self, value):
        if critic._canonical(value) != self._sealed:
            raise critic.Error("offline attempt replay mismatch")

    @classmethod
    def from_dict(cls, value, *, witness=None, **sources):
        data = critic._read(critic._canonical(value))
        expected = assess_offline_attempt(**sources)
        if not isinstance(data, dict):
            raise critic.Error("offline attempt replay mismatch")
        if data.get("status") == "EXECUTED":
            if type(witness) is not cls:
                raise critic.Error("offline attempt replay mismatch: native execution witness required")
            state = _session(witness._fixture)
            witness._validate(witness.to_dict())
            if state["receipts"].get(witness.digest) is not witness:
                raise critic.Error("offline attempt replay mismatch: unwitnessed operation")
            held = expected.to_dict()
            if any(data.get(key) != held[key] for key in ("decision_digest", "preservation_plan_digest", "operation")):
                raise critic.Error("offline attempt replay mismatch: parent/operation")
            expected = witness
        if critic._canonical(data) != expected.to_json():
            raise critic.Error("offline attempt replay mismatch: canonical record differs")
        return expected

    @classmethod
    def from_json(cls, raw, **sources):
        return cls.from_dict(critic._read(raw), **sources)


def _bind(data, fixture=None):
    result = object.__new__(BQ04bOfflineAttemptReceipt)
    object.__setattr__(result, "_sealed", critic._canonical(data))
    object.__setattr__(result, "_fixture", fixture)
    critic._Record.__init__(result, result._sealed)
    return result


def assess_offline_attempt(decision, plan, *, decision_digest, plan_digest,
                           operation="NONE", decision_context=None, preservation_context=None):
    d, p = _parents(decision, plan, decision_digest, plan_digest, decision_context, preservation_context)
    if operation not in SUPPORTED_OPERATIONS:
        raise critic.Error("unsupported operation")
    reasons = ["NATIVE_FIXTURE_EXECUTION_NOT_WITNESSED"]
    if d["choice"] != "PROPOSE_LOCAL_DETERMINISTIC_EDIT":
        reasons.append("BQ04A_DECISION_NOT_LOCAL_EDIT")
    if p["status"] != "PRESERVATION_PLAN_ESTABLISHED":
        reasons.append("BQ04A_PRESERVATION_NOT_ACTIONABLE")
    return _bind(dict(schema_version=SCHEMA, status="HOLD", evidence_domain="SYNTHETIC",
        attempt_id=None, decision_digest=decision.digest, preservation_plan_digest=plan.digest,
        before_review_digest=d["multimodal_review_digest"], original_asset_sha256=d["original_asset_sha256"],
        input_identity="UNKNOWN", input_bytes_verified_by_adapter=False, output_asset_sha256=None,
        actual_bounded_location=None, output_created=False, operation=operation, operation_version="1.0.0",
        command_hash=None, supported_operations=list(SUPPORTED_OPERATIONS), reason_codes=sorted(reasons),
        uncertainties=["PRESERVATION_UNPROVEN", "RIGHTS_UNKNOWN", "SEMANTIC_QUALITY_UNKNOWN", "HUMAN_CALIBRATION_NOT_RUN"],
        quality_pass=False, release_authorization="NONE", spend_authorization="NONE", external_effects="NONE",
        authority=dict(policy.AUTHORITY)))


def _frames(root, path, tools):
    raw = _runner(root).run(a13._input(tools["ffmpeg"], path) + ["-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", "format=rgb24", "-pix_fmt", "rgb24", "-threads", "1", "-f", "rawvideo", "pipe:1"]).stdout
    size = 64 * 64 * 3
    if len(raw) != size * 8:
        raise critic.Error("fixed fixture frame count/geometry mismatch")
    return tuple(raw[index:index+size] for index in range(0, len(raw), size))


def execute_offline_repair(decision, plan, *, decision_digest, plan_digest, input_path, output_path,
                           root, operation, fixture=None, decision_context=None, preservation_context=None):
    d, p = _parents(decision, plan, decision_digest, plan_digest, decision_context, preservation_context)
    if d["choice"] != "PROPOSE_LOCAL_DETERMINISTIC_EDIT" or p["status"] != "PRESERVATION_PLAN_ESTABLISHED":
        raise critic.Error("native decision/plan not actionable")
    state = _session(fixture)
    if (root != state["root"] or operation != OPERATION or p["editor"] != OPERATION
            or p["target_scope"] != SCOPE or p["test_oracle"] != ORACLE
            or decision_context != CONTEXT or preservation_context is not None
            or state["before"] is None or state["before"].digest != d["multimodal_review_digest"]):
        raise critic.Error("fixture operation/scope/oracle/parent mismatch")
    input_path, output_path = Path(input_path), Path(output_path)
    if input_path == output_path:
        raise critic.Error("output path cannot alias input path")
    if input_path != root / "fixture.mp4" or output_path != root / "repaired.mp4":
        raise critic.Error("fixed fixture input/output paths required")
    if state["operation_state"] != "READY":
        raise critic.Error("fixture operation already witnessed or started; no retry")
    with _paths_locked(root, input_path):
        _validate_safe_path(root, output_path)
        if output_path.exists():
            raise critic.Error("output already exists; no overwrite or alias")
        original = _hash(input_path)
        if original != state["original"] or original != d["original_asset_sha256"]:
            raise critic.Error("fixture source bytes changed")
        mm.BQ02bMultimodalReview.from_dict(decision._review.to_dict(), root=root)
        tools = _tools()
        before_frames = _frames(root, input_path, tools)
        if any(max(before_frames[i]) != 0 for i in (2, 3)):
            raise critic.Error("localized fixture corruption not observed")
        argv = a13._input(tools["ffmpeg"], input_path) + ["-n", "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", "drawbox=color=white:t=fill:enable='gte(t,0.5)*lt(t,1)'",
            "-c:v", "libx264", "-qp", "0", "-g", "1", "-bf", "0", "-pix_fmt", "yuv420p",
            "-threads", "1", "-movflags", "frag_keyframe+empty_moov", "-f", "mp4", "pipe:1"]
        with state["gate"]:
            if state["operation_state"] != "READY":
                raise critic.Error("fixture operation already started; no retry")
            state["operation_state"] = "STARTED_OUTCOME_UNCONFIRMED"
        with _exclusive_output(output_path) as output:
            raw = _runner(root).run(argv).stdout
            if not raw or output.write(raw) != len(raw):
                raise critic.Error("empty/short edit output")
        with _paths_locked(root, output_path):
            sha = _hash(output_path)
            after_frames = _frames(root, output_path, tools)
            if sha == original or _hash(input_path) != original or sha != hashlib.sha256(raw).hexdigest():
                raise critic.Error("source changed or no-op output")
            if any(min(after_frames[i]) < 254 for i in (2, 3)):
                raise critic.Error("localized correction unproven")
            if any(before_frames[i] != after_frames[i] for i in (0, 1, 4, 5, 6, 7)):
                raise critic.Error("non-target preservation unproven")
        data = assess_offline_attempt(decision, plan, decision_digest=decision_digest,
            plan_digest=plan_digest, operation=operation, decision_context=decision_context).to_dict()
        data.update(status="EXECUTED", attempt_id=state["attempt_id"], input_identity="VERIFIED_HASHED_INPUT",
            input_bytes_verified_by_adapter=True, output_asset_sha256=sha, output_created=True,
            actual_bounded_location=str(root), command_hash=hashlib.sha256(critic._canonical(argv).encode()).hexdigest(),
            reason_codes=["LOCAL_DETERMINISTIC_EDIT_EXECUTED", "ORIGINAL_BYTES_PRESERVED_UNCHANGED", "OUTPUT_BYTES_DISTINCT_AND_HASHED"])
        receipt = _bind(data, fixture)
        state["receipts"][receipt.digest] = receipt
        state["operation_state"] = "EXECUTED"
        return receipt
