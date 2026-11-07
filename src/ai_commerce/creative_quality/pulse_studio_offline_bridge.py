"""BQ-05a: read-only native WAIT_BRAIN -> bounded offline Q4/Q5 sidecar.

Hashes are byte identities, never signatures or private Brain/provider proof.
The only ingress is an existing B0.4 session path, independently reopened by
its owners. Temporary Q4 copies live outside all retained journey directories.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative import briefs
from ai_commerce.creative_quality import behavioral, causality, critic
from ai_commerce.creative_quality import inspection_evidence as t1, t2_sampling as t2
from ai_commerce.creative_quality import quality_review as qr, multimodal_review as mm
from ai_commerce.creative_quality import repair_policy as policy, repair_preservation as preservation
from ai_commerce.production_runtime import attended_worker as w, handoff, operator, metrics
from ai_commerce.production_runtime.contracts import canonical_bytes

SCHEMA = "BQ05aStudioQAEvidence.v1"
SOURCE_SHA = "22b4220936cc9eaf1d952ed1e5e2909d20d4e374"
FLAGS = ["NO_PROVIDER_EFFECT", "NOT_AUTHENTICATED_BRAIN", "NO_PUBLISH", "NO_SPEND",
         "QUALITY_NOT_APPROVED", "RIGHTS_UNKNOWN", "NO_LIVE_PULSE_STUDIO"]
LIMITS = {"creative_quality": "UNKNOWN", "semantic_quality": "UNKNOWN", "rights": "UNKNOWN",
          "T3": "NOT_RUN", "human_calibration": "NOT_RUN", "anti_rollback": "NOT_PROVEN"}
TOP = set("schema_version source_sha status binding provenance q4 q5 limits flags reason_codes record_sha256".split())
REASONS = {"OFFLINE_TECHNICAL_OBSERVATION_ONLY", "NATIVE_EVIDENCE_OR_PATH_HOLD",
           "UNSUPPORTED_REQUEST_HOLD", "WINDOWS_REQUIRED_HOLD", "Q4_TECHNICAL_HOLD"}
MAX_FILES, MAX_BYTES = 512, 64 * 1024 * 1024
PHASES = ("SESSION_PATH", "DIRECTORY_LOCK", "FILE_LOCK_AND_IDENTITY", "OPERATOR_REPLAY",
          "HANDOFF", "A06_CURRENT_BYTES", "A13_T1", "T2_CAPTURE", "Q2_MM_Q5",
          "FINAL_LOCK", "SIDECAR_SEAL")
TECHNICAL_CODES = frozenset(("CHECK_REJECTED", "ACCESS_DENIED", "SHARING_VIOLATION",
    "NOT_FOUND", "NATIVE_IO", "NATIVE_API_FAILED", "HANDLE_PATH_MISMATCH",
    "FILE_IDENTITY_MISMATCH", "OWNER_REJECTED", "UNEXPECTED_FAILURE"))
_TRACE = ContextVar("bq05a_internal_technical_trace", default=None)


def _failure_code(exc):
    if (type(exc) is BridgeHold and len(exc.args) == 1 and type(exc.args[0]) is str
            and exc.args[0] in TECHNICAL_CODES):
        return exc.args[0]
    if isinstance(exc, PermissionError):
        return "ACCESS_DENIED"
    if isinstance(exc, FileNotFoundError):
        return "NOT_FOUND"
    if isinstance(exc, OSError):
        return "SHARING_VIOLATION" if getattr(exc, "winerror", None) == 32 else "NATIVE_IO"
    if isinstance(exc, BridgeHold):
        return "CHECK_REJECTED"
    return "OWNER_REJECTED" if isinstance(exc, ValueError) else "UNEXPECTED_FAILURE"


@contextmanager
def _stage(phase):
    """Count completed protected blocks, never infer a whole phase from entry.

    Internal opt-in diagnostics contain constants/counts only. The innermost
    failed block wins; outer lock unwinding cannot replace its proven stage.
    A completed block is not proof that later checks in that phase succeeded.
    """
    assert phase in PHASES
    trace = _TRACE.get()
    if trace is not None and phase not in trace["entered_phases"]:
        trace["entered_phases"].append(phase)
    try:
        yield
    except Exception as exc:
        if trace is not None and trace["failed_phase"] is None:
            trace.update(failed_phase=phase, failure_code=_failure_code(exc))
        raise
    else:
        if trace is not None:
            trace["completed_blocks"][phase] = min(512, trace["completed_blocks"].get(phase, 0) + 1)


def _win32_need(ok):
    if not ok:
        import ctypes
        raise BridgeHold({5: "ACCESS_DENIED", 32: "SHARING_VIOLATION",
                          2: "NOT_FOUND", 3: "NOT_FOUND"}.get(ctypes.get_last_error(), "NATIVE_API_FAILED"))


class _FinalLocks(ExitStack):
    def __exit__(self, *args):
        # Scope FINAL_LOCK to actual context-exit checks. Wrapping the whole
        # observation would falsely label an uninstrumented read as finalization.
        with _stage("FINAL_LOCK"):
            return super().__exit__(*args)


class BridgeHold(ValueError):
    """Finite sanitized errors; never interpolate native exceptions."""


def _need(ok):
    if not ok:
        raise BridgeHold("NATIVE_EVIDENCE_OR_PATH_HOLD")


def _digest(raw):
    return sha256(raw).hexdigest()


def _metadata(path, *, directory=False):
    info = path.lstat()
    _need(not (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
               or getattr(info, "st_reparse_tag", 0)))
    _need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    return info


def _path(value, *, directory=False):
    # Validate the original spelling before Path can normalize dot components.
    path = w._local(value, directory=directory)
    _need(re.match(r"^[A-Za-z]:[\\/]", value) is not None)
    _need("//" not in value.replace("\\", "/"))
    for part in path.parts[1:]:
        _need("~" not in part)  # no Win32 short-name alias ingress
    _metadata(path, directory=directory)
    repo = Path(__file__).resolve().parents[3]
    _need(path != repo and repo not in path.parents)
    return path


def _kernel():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _handle_path(kernel, handle, path):
    import ctypes
    from ctypes import wintypes
    query = kernel.GetFinalPathNameByHandleW
    query.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    query.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(4096)
    length = query(handle, buffer, len(buffer), 0)
    _need(0 < length < len(buffer))
    if os.path.normcase(buffer.value.removeprefix("\\\\?\\")) != os.path.normcase(str(path)):
        raise BridgeHold("HANDLE_PATH_MISMATCH")


@contextmanager
def _directory_lock(path):
    import ctypes
    from ctypes import wintypes
    with _stage("DIRECTORY_LOCK"):
        kernel = _kernel()
        _metadata(path, directory=True)
        handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x02200000, None)
        _win32_need(handle != ctypes.c_void_p(-1).value)
    try:
        with _stage("DIRECTORY_LOCK"):
            class Tag(ctypes.Structure):
                _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]
            query = kernel.GetFileInformationByHandleEx
            query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            query.restype = wintypes.BOOL
            tag = Tag()
            _win32_need(query(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)))
            _need(not (tag.attributes & 0x400 or tag.tag))
            _handle_path(kernel, handle, path)
            before = _metadata(path, directory=True)
        yield
        with _stage("FINAL_LOCK"):
            after = _metadata(path, directory=True)
            _need((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino))
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _owned_scratch(value):
    """Expand only our new temporary directory, with native identity proof.

    Windows may supply an 8.3 TEMP spelling. Caller/session paths still use
    strict _path unchanged. Expansion alone grants nothing: reject reparse
    ancestors, pin the long-name ancestors with native handles, then require
    both spellings to identify the originally created directory under lock.
    Failed expansion, caller access, identity or final checks remain HOLD.
    """
    import ctypes
    from ctypes import wintypes
    original = w._local(value, directory=True)
    before = _metadata(original, directory=True)
    kernel = _kernel()
    query = kernel.GetLongPathNameW
    query.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    query.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(4096)
    length = query(str(original), buffer, len(buffer))
    _win32_need(length != 0)
    _need(length < len(buffer))
    expanded = _path(buffer.value, directory=True)
    identity = (before.st_dev, before.st_ino)
    with _FinalLocks() as stack:
        for parent in reversed((expanded, *expanded.parents)):
            stack.enter_context(_directory_lock(parent))
        for path in (original, expanded):
            info = _metadata(path, directory=True)
            if (info.st_dev, info.st_ino) != identity:
                raise BridgeHold("FILE_IDENTITY_MISMATCH")
        yield expanded
        for path in (original, expanded):
            info = _metadata(path, directory=True)
            if (info.st_dev, info.st_ino) != identity:
                raise BridgeHold("FILE_IDENTITY_MISMATCH")


@contextmanager
def _frozen_session(session_path):
    """Lock roots/parents and ALL retained inputs/evidence before owner reads.

    Directory handles reject reparse/alias races. FILE_SHARE_READ denies any
    preexisting writer and future write/delete opens. Recheck bytes, identities,
    metadata and directory membership before AND during context exit.
    """
    import msvcrt
    with _stage("SESSION_PATH"):
        path = _path(session_path)
    with _FinalLocks() as stack:
        directories, files, memberships = set(), {}, {}
        total = 0

        def directory(item):
            for parent in reversed((item, *item.parents)):
                if parent not in directories:
                    stack.enter_context(_directory_lock(parent))
                    directories.add(parent)

        def file(item):
            nonlocal total
            if item in files:
                return
            with _stage("FILE_LOCK_AND_IDENTITY"):
                _path(str(item))
                directory(item.parent)
                before = _metadata(item)
                _need(len(files) < MAX_FILES and before.st_size <= MAX_BYTES - total)
                stream = stack.enter_context(t2._snapshot_lock(item))
                _handle_path(_kernel(), msvcrt.get_osfhandle(stream.fileno()), item)
                opened = os.fstat(stream.fileno())
                if a13.media_files.fingerprint(before) != a13.media_files.fingerprint(opened):
                    raise BridgeHold("FILE_IDENTITY_MISMATCH")
                observed = a13._copy_bounded(stream, None, MAX_BYTES - total)
                total += observed[1]
                files[item] = (stream, observed)
                _metadata(item)

        def tree(root):
            directory(root)
            children = tuple(sorted(root.iterdir()))
            _need(len(children) + len(files) + len(memberships) <= MAX_FILES)
            memberships[root] = children
            for child in children:
                info = child.lstat()
                if stat.S_ISDIR(info.st_mode):
                    _path(str(child), directory=True)
                    tree(child)
                else:
                    file(child)

        file(path)
        with _stage("SESSION_PATH"):
            session = w._json(path)
            _need(type(session) is dict and set(session) == operator.SESSION_FIELDS)
            inputs = session["inputs"]
            _need(type(inputs) is dict and set(inputs) == operator.INPUT_FIELDS)
            _need(session["schema_version"] == "OperatorSession.v1"
                  and session["operator_baseline_sha"] == operator.BASELINE_SHA
                  and path == Path(session["operator_dir"]) / operator.SESSION_NAME)
            _need(session["session_sha256"] == _digest(canonical_bytes(
                {k: v for k, v in session.items() if k != "session_sha256"})))
            _need(inputs["source_sha"] == w.SOURCE_SHA)
            roots = [session[k] for k in ("operator_dir", "private_dir", "export_dir")]
            roots.append(inputs["asset_root"])
            operator._directories(*roots)
            # Check all declared mapping boundaries before reading any mapped file.
            for key in ("assets", "rights"):
                _need(type(inputs[key]) is list and len(inputs[key]) <= 4)
                for mapping in inputs[key]:
                    _need(type(mapping) is str and "=" in mapping)
                    mapped = _path(mapping.split("=", 1)[1])
                    _need(Path(inputs["asset_root"]) in mapped.parents)
        with _stage("SESSION_PATH"):
            for value in roots:
                tree(_path(value, directory=True))
            file(_path(inputs["directive_path"]))
            for key in ("assets", "rights"):
                _need(type(inputs[key]) is list and len(inputs[key]) <= 4)
                for mapping in inputs[key]:
                    _need(type(mapping) is str and "=" in mapping)
                    file(_path(mapping.split("=", 1)[1]))
        yield
        with _stage("FINAL_LOCK"):
            for item, (stream, observed) in files.items():
                _metadata(item)
                stream.seek(0)
                _need(a13._copy_bounded(stream, None, MAX_BYTES) == observed)
            for root, children in memberships.items():
                _need(tuple(sorted(root.iterdir())) == children)
    # ExitStack has now completed all final A13 handle/fingerprint checks.


def _technical_preflight():
    # This explicit UNKNOWN placeholder is NOT a translation of private intent.
    intake = briefs.intake_brief("Offline technical review; creative intent unassessed.")
    brief = behavioral.build_behavioral_brief(intake, source_brief_digest=briefs.brief_digest(intake),
        hypotheses=[{"id": "unassessed", "statement": "Creative meaning remains unassessed.",
            "assumptions": ["Technical measurements alone cannot assess intent."],
            "counterexamples": ["Black or silent media may be intentional."],
            "source_refs": [behavioral.A03_INTAKE_REF], "truth": "HYPOTHESIS"}], provenance="UNKNOWN")
    # The Q2 schema requires a visual form and one plan interval. This fictional
    # scaffold describes the review harness, never the PULSE asset or its shots.
    plan = causality.build_causal_plan(brief, behavioral_brief_digest=brief.digest,
        shots=[{"id": "unassessed", "order": 0, "start_ms": 0, "end_ms": 1,
                "description": "Review harness placeholder; no asset shot mapping.", "objects": [], "constraints": []}],
        edges=[], visual_form="FICTION", physics_intent="UNKNOWN", audio={"mode": "UNKNOWN", "language": "UNKNOWN",
        "text": None, "accent_intent": None, "delivery_intent": None, "acoustic_intent": None,
        "lip_sync": "UNKNOWN", "source_refs": []})
    return critic.preflight_critique(plan, plan_digest=plan.digest)


def _observe(session_path, sample):
    with _frozen_session(session_path):
        with _stage("OPERATOR_REPLAY"):
            session, op, root, directive, intent, _ = operator.load(session_path)
            rows = metrics.read(op, session["session_sha256"])
            _need(operator._admission(op, session, intent, rows))
            attempts = {r["attempt_id"] for r in rows if r["phase"] == "ATTEMPT"}
            _need(attempts == {r["attempt_id"] for r in rows if r["phase"] == "RESULT"})
            _need(any(r["command"] == "run" and r["phase"] == "RESULT" and r["outcome"] == "PASS" for r in rows))
        with _stage("HANDOFF"):
            receipt, packet, proof = handoff.verified(root, directive, intent)
            handoff.check_existing(session, root, directive, intent)
            record, artifact = receipt.to_dict(), proof["local_artifact"]
            _need(record["state"] == "WAIT_BRAIN" and record["effect"]["status"] == "NONE"
                  and record["brain_decision"] is None and proof["source_origin"] == "LOCAL_IMPORT")
            _need(record["provenance"]["evidence_type"] in {"SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED"})
            for key in ("journey_id", "source_sha", "directive_version", "directive_sha256"):
                _need(record[key] == intent[key] == directive.to_dict()[key])
        with _stage("A06_CURRENT_BYTES"):
            dest = w._render_path(root, intent)
            movie = dest / "video.mp4"
            digest = artifact["artifact_sha256"]
            _need(a13.media_files.hash_file(movie)[0] == digest and artifact["ffprobe_full_decode"] is True)
        with _stage("A13_T1"):
            technical = t1.inspect_local_t1(dest, [a13.ReleaseAsset("video.mp4", digest)],
                                            diagnostics=a13.DiagnosticOptions(black=True))
            data = technical.to_dict()
            _need(data["status"] == "PASS" and data["verified"] is True)
            asset = data["a13_report"]["assets"][0]
            _need(asset["observed_sha256"] == digest and asset["full_decode"] is True)
            duration = Fraction(asset["facts"]["duration_seconds"])
        with _stage("T2_CAPTURE"):
            request = t2.SamplingRequest("video.mp4", digest,
                                        frame_times=("0", str(duration / 2)), audio_times=("0",))
            if sample:
                # T2 creates a scratch snapshot under root; supply a separate,
                # hash-identical copy, never the retained PULSE render directory.
                with tempfile.TemporaryDirectory(prefix="bq05a-q4-") as temporary, _owned_scratch(temporary) as scratch:
                    _need(all(scratch != Path(p) and Path(p) not in scratch.parents for p in
                              (session["operator_dir"], session["private_dir"], session["export_dir"],
                               session["inputs"]["asset_root"])))
                    with movie.open("rb") as source, (scratch / "video.mp4").open("xb") as target:
                        copied = a13._copy_bounded(source, target, w.MAX_TREE_BYTES)
                    _need(copied[0] == digest)
                    sampled = t2.sample_local_t2(technical, request, root=scratch, allow_local_probe=True)
            else:
                sampled = t2.not_sampled_t2(technical, request)
        with _stage("Q2_MM_Q5"):
            pre = _technical_preflight()
            review = qr.adversarial_review(pre, preflight_digest=pre.digest,
                                           technical_evidence=technical, technical_evidence_digest=technical.digest)
            multi = mm.multimodal_review(review, sampled, original_review_digest=review.digest, t2_digest=sampled.digest)
            # Native Q5 has no observed asset when T2 was not run or failed. Retain
            # its HOLD rather than passing a T1 pin as if T2 had observed it.
            q5_asset = multi.to_dict()["observed_asset_sha256"]
            _need(q5_asset is None or q5_asset == digest)
            decision = policy.decide_repair(multi, review_digest=multi.digest, asset_sha256=q5_asset)
            _need(decision.to_dict()["choice"] in {"KEEP_ORIGINAL", "HOLD", "RETURN_TO_BRAIN"})
            plan = preservation.create_preservation_plan(decision, decision_digest=decision.digest, editor="NONE")
        with _stage("FINAL_LOCK"):
            _need(handoff.verified(root, directive, intent)[:2] == (receipt, packet))
            _need(a13.media_files.hash_file(movie)[0] == digest)
        with _stage("SIDECAR_SEAL"):
            sampled_data = sampled.to_dict()
            result = {
                "binding": {**{k: record[k] for k in ("journey_id", "directive_version", "directive_sha256")},
                    "pulse_source_sha": record["source_sha"],
                    **{k: proof[k] for k in ("runtime_sha256", "intent_sha256", "directive_file_sha256",
                                            "receipt_sha256", "receipt_file_sha256", "packet_sha256", "journal_checkpoint")},
                    "packet_file_sha256": _digest(packet), "artifact_ref": "local:video",
                    "artifact_sha256": digest, "manifest_sha256": artifact["manifest_sha256"],
                    "tree_sha256": artifact["tree_sha256"], "a06_verification_ref": artifact["verification_ref"]},
                "provenance": {"evidence_type": record["provenance"]["evidence_type"], "origin": "LOCAL_IMPORT",
                    "confidence": "CURRENT_BYTES_TECHNICAL_ONLY", "provider_attested": False},
                "q4": {"status": "OBSERVED" if not sample or sampled_data["status"] == "OBSERVED" else "UNKNOWN",
                    "source_asset_sha256": digest, "result_asset_sha256": digest,
                    "t1_digest": technical.digest, "a13_record_sha256": data["a13_record_sha256"],
                    "full_decode": True, "size_bytes": asset["size_bytes"],
                    "facts": {"duration_seconds": str(duration),
                        "video_streams": [{k: s[k] for k in ("index", "width", "height", "fps", "decoded_frames")}
                                          for s in asset["facts"]["streams"] if s["kind"] == "video"],
                        "audio_present": any(s["kind"] == "audio" for s in asset["facts"]["streams"])},
                    "black_diagnostic": {k: asset["diagnostics"]["black"][k] for k in ("status", "segments")},
                    "t2_status": sampled_data["status"], "t2_digest": sampled.digest,
                    "requested_frame_times": list(request.frame_times),
                    "frames": [{"requested_seconds": f["time"]["requested_seconds"],
                        **{k: f[k] for k in ("sample_sha256", "minimum", "maximum", "mean_luma")}}
                               for f in sampled_data["frames"]], "audio_status": sampled_data["audio_status"],
                    "multimodal_review_digest": multi.digest, "review_status": multi.to_dict()["status"],
                    "preflight_scope": "UNKNOWN_PLACEHOLDER_NOT_CREATIVE_INTENT",
                    "confidence": "MEASURED_TECHNICAL_SIGNALS_ONLY"},
                "q5": {"choice": decision.to_dict()["choice"], "decision_digest": decision.digest,
                       "preservation_digest": plan.digest, "editor": "NONE", "executed": False},
            }
    return result  # only after every lock's final checks succeeded


def _seal(body):
    return BQ05aStudioQAEvidence(canonical_bytes({**body, "record_sha256": _digest(canonical_bytes(body))}))


@dataclass(frozen=True, slots=True)
class BQ05aStudioQAEvidence:
    """Closed sanitized serialization, NOT an authentication capability.

    Independent receivers must use recheck_sidecar, not trust this constructor.
    """
    raw: bytes

    def __post_init__(self):
        try:
            _need(type(self.raw) is bytes and len(self.raw) <= 64 * 1024)
            value = json.loads(self.raw)
            _need(type(value) is dict and set(value) == TOP and canonical_bytes(value) == self.raw)
            _need(value["schema_version"] == SCHEMA and value["source_sha"] == SOURCE_SHA)
            _need(value["flags"] == FLAGS and value["limits"] == LIMITS)
            _need(value["status"] in {"OFFLINE_TECHNICAL_REVIEW_ONLY", "FAIL_CLOSED_HOLD"})
            _need(value["reason_codes"] in [[code] for code in REASONS])
            body = {k: v for k, v in value.items() if k != "record_sha256"}
            _need(value["record_sha256"] == _digest(canonical_bytes(body)))
            _validate_projection(value)
        except Exception:
            raise BridgeHold("SIDECAR_INVALID") from None

    @property
    def digest(self):
        return _digest(self.raw)

    def to_dict(self):
        return json.loads(self.raw)


def _validate_projection(value):
    """Closed nested fields; native parsers still own the diagnostic subrecords."""
    binding, q4, q5, origin = (value[k] for k in ("binding", "q4", "q5", "provenance"))
    _need(type(q5) is dict and set(q5) == set("choice decision_digest preservation_digest editor executed".split()))
    _need(q5["choice"] in {"HOLD", "KEEP_ORIGINAL", "RETURN_TO_BRAIN"}
          and q5["editor"] == "NONE" and q5["executed"] is False)
    if binding is None:
        _need(value["status"] == "FAIL_CLOSED_HOLD" and q4 is origin is None)
        _need(value["reason_codes"] != ["OFFLINE_TECHNICAL_OBSERVATION_ONLY"])
        _need(q5 == {"choice": "HOLD", "decision_digest": None, "preservation_digest": None,
                    "editor": "NONE", "executed": False})
        return
    _need(type(binding) is dict and set(binding) == set("journey_id directive_version directive_sha256 pulse_source_sha runtime_sha256 intent_sha256 directive_file_sha256 receipt_sha256 receipt_file_sha256 packet_sha256 journal_checkpoint packet_file_sha256 artifact_ref artifact_sha256 manifest_sha256 tree_sha256 a06_verification_ref".split()))
    _need(type(origin) is dict and set(origin) == {"evidence_type", "origin", "confidence", "provider_attested"})
    _need(origin == {"evidence_type": origin["evidence_type"], "origin": "LOCAL_IMPORT",
                    "confidence": "CURRENT_BYTES_TECHNICAL_ONLY", "provider_attested": False}
          and origin["evidence_type"] in {"SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED"})
    _need(type(q4) is dict and set(q4) == set("status source_asset_sha256 result_asset_sha256 t1_digest a13_record_sha256 full_decode size_bytes facts black_diagnostic t2_status t2_digest requested_frame_times frames audio_status multimodal_review_digest review_status preflight_scope confidence".split()))
    _need(q4["source_asset_sha256"] == q4["result_asset_sha256"] == binding["artifact_sha256"])
    _need(q4["full_decode"] is True and q4["status"] in {"OBSERVED", "UNKNOWN"})
    _need(value["status"] == ("OFFLINE_TECHNICAL_REVIEW_ONLY" if q4["status"] == "OBSERVED" else "FAIL_CLOSED_HOLD"))
    _need(value["reason_codes"] == (["OFFLINE_TECHNICAL_OBSERVATION_ONLY"]
          if q4["status"] == "OBSERVED" else ["Q4_TECHNICAL_HOLD"]))
    _need(set(binding["journal_checkpoint"]) == {"journal_id", "source_sha", "position", "sha256", "generation"})
    _need(binding["journal_checkpoint"]["source_sha"] == binding["pulse_source_sha"])
    _need(re.fullmatch(r"journey:[0-9a-f]{32,64}", binding["journey_id"]) is not None)
    for mapping in (binding, q4, q5):
        for key, item in mapping.items():
            if key.endswith(("sha256", "digest")):
                _need(type(item) is str and re.fullmatch(r"[0-9a-f]{64}", item) is not None)
    _need(binding["pulse_source_sha"] == w.SOURCE_SHA and binding["artifact_ref"] == "local:video")
    _need(type(binding["directive_version"]) is int and binding["directive_version"] > 0)
    _need(binding["a06_verification_ref"].startswith("a06:")
          and re.fullmatch(r"a06:[0-9a-f]{64}", binding["a06_verification_ref"]) is not None)
    checkpoint = binding["journal_checkpoint"]
    _need(re.fullmatch(r"journal:[0-9a-f]{32}", checkpoint["journal_id"]) is not None
          and re.fullmatch(r"[0-9a-f]{64}", checkpoint["sha256"]) is not None)
    _need(all(type(checkpoint[k]) is int and checkpoint[k] > 0 for k in ("position", "generation")))
    _need(q4["t2_status"] in {"OBSERVED", "NOT_RUN", "UNKNOWN"}
          and q4["audio_status"] in {"OBSERVED", "NOT_APPLICABLE", "NOT_RUN", "UNKNOWN"}
          and q4["review_status"] in {"HOLD", "REVISE", "UNKNOWN"})
    _need((q4["t2_status"] == "UNKNOWN") == (q4["status"] == "UNKNOWN"))
    _need(q4["preflight_scope"] == "UNKNOWN_PLACEHOLDER_NOT_CREATIVE_INTENT"
          and q4["confidence"] == "MEASURED_TECHNICAL_SIGNALS_ONLY")
    _need(type(q4["size_bytes"]) is int and 0 < q4["size_bytes"] <= w.MAX_TREE_BYTES)
    facts = q4["facts"]
    _need(type(facts) is dict and set(facts) == {"duration_seconds", "video_streams", "audio_present"})
    _need(type(facts["duration_seconds"]) is str and Fraction(facts["duration_seconds"]) > 0
          and type(facts["audio_present"]) is bool)
    _need(type(facts["video_streams"]) is list and 1 <= len(facts["video_streams"]) <= 16)
    for stream in facts["video_streams"]:
        _need(set(stream) == {"index", "width", "height", "fps", "decoded_frames"})
        _need(type(stream["index"]) is int and stream["index"] >= 0)
        _need(all(type(stream[k]) is int and stream[k] > 0 for k in ("width", "height", "decoded_frames")))
        _need(stream["fps"] is None or type(stream["fps"]) is str and Fraction(stream["fps"]) > 0)
    diagnostic = q4["black_diagnostic"]
    _need(type(diagnostic) is dict and set(diagnostic) == {"status", "segments"}
          and diagnostic["status"] == "OBSERVED" and type(diagnostic["segments"]) is list)
    for segment in diagnostic["segments"]:
        _need(set(segment) == {"stream_index", "start_seconds", "end_seconds", "duration_seconds"})
        _need(type(segment["stream_index"]) is int and segment["stream_index"] >= 0)
        _need(all(type(segment[k]) is str for k in ("start_seconds", "end_seconds", "duration_seconds")))
        _need(Fraction(segment["end_seconds"]) - Fraction(segment["start_seconds"])
              == Fraction(segment["duration_seconds"]) >= 0 and Fraction(segment["start_seconds"]) >= 0)
    _need(type(q4["requested_frame_times"]) is list and len(q4["requested_frame_times"]) == 2)
    _need(q4["requested_frame_times"] == ["0", str(Fraction(facts["duration_seconds"]) / 2)])
    _need(type(q4["frames"]) is list and len(q4["frames"]) == (2 if q4["t2_status"] == "OBSERVED" else 0))
    for index, frame in enumerate(q4["frames"]):
        _need(set(frame) == {"requested_seconds", "sample_sha256", "minimum", "maximum", "mean_luma"})
        _need(frame["requested_seconds"] == q4["requested_frame_times"][index]
              and re.fullmatch(r"[0-9a-f]{64}", frame["sample_sha256"]) is not None)
        _need(type(frame["minimum"]) is int and type(frame["maximum"]) is int
              and 0 <= frame["minimum"] <= frame["maximum"] <= 255
              and type(frame["mean_luma"]) is str and 0 <= Fraction(frame["mean_luma"]) <= 255)


def inspect_wait_brain(session_path, *, sample=True, effect="REVIEW_ONLY", context=None):
    """Return deterministic evidence or sanitized HOLD. Never advance a journey.

    No receipt/state/fixture-token ingress. Any caller context or effect other
    than REVIEW_ONLY fails closed, regardless of asserted rights or authority.
    """
    body = {"schema_version": SCHEMA, "source_sha": SOURCE_SHA, "status": "FAIL_CLOSED_HOLD",
            "binding": None, "provenance": None, "q4": None,
            "q5": {"choice": "HOLD", "decision_digest": None, "preservation_digest": None,
                   "editor": "NONE", "executed": False}, "limits": LIMITS, "flags": FLAGS,
            "reason_codes": ["NATIVE_EVIDENCE_OR_PATH_HOLD"]}
    if (type(session_path) is not str or type(sample) is not bool or type(effect) is not str
            or effect != "REVIEW_ONLY" or context is not None):
        body["reason_codes"] = ["UNSUPPORTED_REQUEST_HOLD"]
    elif os.name != "nt":
        body["reason_codes"] = ["WINDOWS_REQUIRED_HOLD"]
    else:
        try:
            with metrics.Boundary():
                observed = _observe(session_path, sample)
            body.update(observed)
            if observed["q4"]["status"] == "OBSERVED":
                body.update(status="OFFLINE_TECHNICAL_REVIEW_ONLY", reason_codes=["OFFLINE_TECHNICAL_OBSERVATION_ONLY"])
            else:
                body["reason_codes"] = ["Q4_TECHNICAL_HOLD"]
        except Exception:
            pass  # no native private error text, paths or partial trust escapes
    with _stage("SIDECAR_SEAL"):
        return _seal(body)


def _diagnose_wait_brain(session_path, *, sample=True):
    """Trusted disposable-fixture triage only; never a sidecar or acceptance.

    No exception text, type names, stack, paths or native data is retained.
    Completed block counts prove only those blocks, not whole-stage success.
    """
    trace = {"entered_phases": [], "completed_blocks": {}, "failed_phase": None,
             "failure_code": None, "observation": "HOLD_OR_UNKNOWN"}
    token = _TRACE.set(trace)
    try:
        result = inspect_wait_brain(session_path, sample=sample)
        if result.to_dict()["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY":
            trace["observation"] = "ALL_BOUNDARIES_COMPLETED"
    except Exception:
        pass  # the stage has already recorded a finite code
    finally:
        _TRACE.reset(token)
    return trace


def recheck_sidecar(raw, session_path, *, sample=True):
    """Full native current-byte replay, not hash-only acceptance of caller JSON."""
    supplied = BQ05aStudioQAEvidence(raw)
    current = inspect_wait_brain(session_path, sample=sample)
    if current.raw != supplied.raw or current.to_dict()["status"] != "OFFLINE_TECHNICAL_REVIEW_ONLY":
        raise BridgeHold("SIDECAR_CURRENT_NATIVE_REPLAY_REQUIRED")
    return current
