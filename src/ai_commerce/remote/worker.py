"""EF02 Windows-ready worker: isolated EF01 subprocesses and durable GitHub results."""
from __future__ import annotations
import base64
import hashlib
import json
import math
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .transport import GitHubTransport, RemoteTransportError, CandidateIntegrityError
from .validation import (fingerprint, check_source, approved_verification_plan, approved_acceptance_plan,
                         reject_retired_wp_identifiers, reject_executor_test_conflict,
                         validate_batch, is_diagnostic_batch, DIAGNOSTIC_MODE, RemoteRequestError)
from .reuse import load_approved_candidates, approve_reuse
from .candidate_policy import (
    REVIEW_STATUS, VERIFIED_STATUSES, candidate_accepted, evidence_digest, validate_candidate_evidence,
)
from ai_commerce.engineering.runner import (
    DEFAULT_MAX_REPAIR_ROUNDS, DEFAULT_ACTION_TIMEOUT_SECONDS, DEFAULT_VERIFY_TIMEOUT_SECONDS,
    validate_max_repair_rounds, _snapshot, _provider_problem, _outside_scope,
)
from ai_commerce.engineering.git import git_argv, resolve_git_executable
from ai_commerce.engineering.git_byte_consistency import (GitByteConsistencyError, checkout_diagnostics)
from ai_commerce.engineering.minimum_test import Evidence, file_hashes
from ai_commerce.engineering.source_safety import checked_path, protected_change, ordinary_scope_drift, is_link


# Human-authorized temporary full-regression budget until ATB-01 is accepted.
# AUTO now gets the Human-approved 90m verification window. EF02 Windows job is 360m.
def _temporary_verification_budget(*, human_full_suite_authorized: bool) -> tuple[int | None, int, int]:
    if human_full_suite_authorized:
        # 180m test budget plus at most 120m EF01 executor time. The external
        # child watchdog exceeds the combined action by only five minutes.
        return (180 * 60, 300 * 60, 305 * 60)
    # Keep a 120m executor window + 90m verification window, then a 5m
    # child cleanup margin. The parent watchdog must not truncate pytest.
    return (DEFAULT_VERIFY_TIMEOUT_SECONDS, DEFAULT_ACTION_TIMEOUT_SECONDS,
            DEFAULT_ACTION_TIMEOUT_SECONDS + 300)


def _new_worktree_path(checkout: Path) -> Path:
    """Allocate a short, durable worktree outside Windows TEMP and the checkout.

    EF02 once put the entire action_id into a TEMP worktree name. Long agent
    sessions could then depend on deep paths and an externally cleaned TEMP
    location. A random, bounded name in the runner-owned sibling directory
    avoids both failure modes without reusing an old worktree or Git claim.
    """
    root = checkout.resolve().parent / "_ef02_worktrees"
    if root.is_symlink():
        raise ValueError("EF02_WORKTREE_ROOT_SYMLINK")
    root.mkdir(mode=0o700, exist_ok=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("EF02_WORKTREE_ROOT_UNAVAILABLE")
    candidate = Path(tempfile.mkdtemp(prefix="wt-", dir=str(root)))
    try:
        if os.name == "nt" and len(str(candidate).encode("utf-16-le")) // 2 > 180:
            raise ValueError("EF02_WORKTREE_PATH_TOO_LONG")
    finally:
        # git worktree add requires an absent target; mkdtemp provides a fresh,
        # unpredictable name without reusing a previous attempt's directory.
        candidate.rmdir()
    return candidate


def _verified_receipt_evidence(report: dict, *, root: Path, workspace: Path,
                               source_sha: str, sources: list[str], profiles: dict,
                               allowed_paths: list[str] | None = None,
                               diagnostics: list[str] | None = None) -> bool:
    """Check actual artifacts and registered PASS bindings, not receipt assertions.

    Emit bounded condition codes, never on-host paths, raw test output, tokens
    or the exception message. A failure is still a hard publication HOLD.
    """
    def rejected(code: str) -> bool:
        if diagnostics is not None:
            diagnostics.append(code)
        return False

    try:
        refs, hashes = report["evidence_files"], report["evidence_sha256"]
        if (report.get("verify_completed") is not True or
                report.get("provider_return_determinate") is not True or
                set(refs) != set(hashes) or
                not {"tracked_diff", "source_after_verify", "verify_stdout", "provider_return_observed"} <= set(refs)):
            return rejected("RECEIPT_PROVENANCE_INCOMPLETE")
        for key, relative in refs.items():
            if hashlib.sha256(checked_path(root, relative).read_bytes()).hexdigest() != hashes[key]:
                return rejected("RECEIPT_ARTIFACT_HASH_MISMATCH")
        if hashes["tracked_diff"] != report["tracked_diff_sha256"]:
            return rejected("RECEIPT_TRACKED_DIFF_HASH_MISMATCH")
        current = file_hashes(workspace)
        covered = set()
        groups = report["minimum_test"]["groups"]
        if not report.get("minimum_test_enabled") or not groups:
            return rejected("MT01_GROUP_EVIDENCE_MISSING")
        for index, group in enumerate(groups):
            binding = group["binding"]
            registered_name = binding.get("registered_profile")
            onboarding_claim = registered_name == "mt01-component-onboarding"
            # The recorded name and exact argv must BOTH be in the trusted
            # registry. An unrelated alias with identical argv is not proof.
            approved_argv = (
                isinstance(registered_name, str)
                and registered_name != "mt01-component-onboarding"
                and profiles.get(registered_name) == binding.get("argv")
            )
            if onboarding_claim:
                from ai_commerce.engineering.impact_v21 import verified_onboarding_binding
                approved_argv = verified_onboarding_binding(
                    workspace=workspace, binding=binding,
                    changed_paths=report.get("changed_files", []),
                    allowed_paths=allowed_paths or [], base_sha=source_sha,
                    registered_profiles=profiles,
                    expected_group=group.get("group"))
            if not approved_argv:
                return rejected(f"MT01_GROUP_{index}_REGISTERED_BINDING_REJECTED")
            if (binding["base_sha"] != source_sha or binding["aggregate_sources"] != sources or
                    binding["workspace"] != str(workspace.resolve()) or
                    binding["candidate_files"] != {p: current.get(p) for p in binding["candidate_files"]}):
                return rejected(f"MT01_GROUP_{index}_SOURCE_OR_HASH_MISMATCH")
            proof = Evidence(checked_path(root, refs[f"mt01_{index}_evidence"]).read_text(encoding="utf-8"),
                             group["evidence_sha256"])
            if not proof.valid_for(binding, root):
                return rejected(f"MT01_GROUP_{index}_EVIDENCE_INVALID")
            if binding.get("argv", [])[-2:] == ["-n", "12"]:
                from ai_commerce.engineering.verification import observed_xdist_workers
                stdout_ref = json.loads(proof.payload)["artifacts"]["stdout"]["path"]
                if observed_xdist_workers(checked_path(root, stdout_ref).read_bytes()) != 12:
                    return rejected(f"MT01_GROUP_{index}_XDIST_UNPROVEN")
            covered.update(binding["candidate_files"])
        if not set(report["changed_files"]) <= covered:
            return rejected("MT01_CHANGED_FILES_UNCOVERED")
        return True
    except (ValueError, KeyError, TypeError, OSError):
        return rejected("RECEIPT_EVIDENCE_UNAVAILABLE")


def _fresh_aggregate_evidence(report: dict, *, root: Path, workspace: Path,
                              source_sha: str, sources: list[str], argv: list[str]) -> bool:
    """Validate immutable executed proof against the actual combined worktree."""
    try:
        plan = report.get("minimum_test")
        if (not report.get("minimum_test_enabled") or report.get("verify_argv") != argv or
                not isinstance(plan, dict) or plan.get("aggregate_sources") != sources or
                not isinstance(plan.get("groups"), list) or not plan["groups"]):
            return False
        current = file_hashes(workspace)
        for index, group in enumerate(plan["groups"]):
            if not isinstance(group, dict) or not isinstance(group.get("binding"), dict):
                return False
            binding = group["binding"]
            if (group.get("actual_exit_code") != 0 or group.get("state") not in ("EXECUTE", "INVALIDATED") or
                    binding["base_sha"] != source_sha or binding["aggregate_sources"] != sources or
                    binding["argv"] != argv or binding["workspace"] != str(workspace.resolve()) or
                    set(current) - set(binding["candidate_files"]) or
                    binding["candidate_files"] != {p: current.get(p) for p in binding["candidate_files"]}):
                return False
            relative = report["evidence_files"][f"mt01_{index}_evidence"]
            path = (root / relative).resolve()
            if root.resolve() not in path.parents:
                return False
            proof = Evidence(path.read_text(encoding="utf-8"), group["evidence_sha256"])
            if not proof.valid_for(binding, root):
                return False
        return True
    except (ValueError, KeyError, TypeError, OSError, AttributeError):
        return False

def _remote_codex_sandbox_args(executor: str, *, windows: bool) -> list[str]:
    """EF02 Windows runner overrides only Codex; Antigravity is untouched."""
    return ["--codex-windows-sandbox", "mxc"] if windows and executor == "codex" else []

def _safe_child_env(checkout: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in tuple(env):
        if name in ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL") or (
                    name.startswith("EF02_") and "TOKEN" in name):
            env.pop(name, None)
    env["PYTHONPATH"] = str(checkout / "src")
    return env

def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(obj, output, indent=2, ensure_ascii=False, sort_keys=True)
        output.write("\n")
    temporary.replace(path)

def _classify_failed_executor(report: dict, evidence: Path) -> str:
    if report.get("status") != "EXECUTOR_FAILED":
        return report.get("status", "EXECUTION_UNKNOWN")
    chunks: list[bytes] = []
    for key, name in (("executor_stderr", "executor.stderr.log"),
                      ("executor_stdout", "executor.stdout.log")):
        path = (evidence / report.get("evidence_files", {}).get(key, name)).resolve()
        if evidence.resolve() not in path.parents:
            continue
        if path.exists():
            with path.open("rb") as stream:
                chunks.append(stream.read(1024 * 1024))
    problem = _provider_problem(b"\n".join(chunks))
    if problem and problem[0] in ("MODEL_AT_CAPACITY", "QUOTA_EXHAUSTED", "MODEL_UNSUPPORTED"):
        return problem[0]
    return "EXECUTOR_FAILED"


# A separate, finite operator. Neither Issue values nor SOURCE_BUILD profiles
# participate in argv, environment controls, selectors or artifact paths.
B03_DIAGNOSTIC_ROOT = Path(".local/ef02/windows-b03-serial-xdist-v1")
B03_DIAGNOSTIC_NODEIDS = (
    "tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[output-foreign]",
    "tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[output-partial]",
    "tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[journal-rollback]",
)
_B03_COMMON = ("-m", "pytest", *B03_DIAGNOSTIC_NODEIDS, "-vv", "-rA",
               "-o", "addopts=", "-p", "no:cacheprovider", "--noconftest",
               "-p", "ai_commerce.remote.worker")
_B03_COMMANDS = (
    (sys.executable, *_B03_COMMON,
     "--junitxml=.local/ef02/windows-b03-serial-xdist-v1/serial.junit.xml"),
    (sys.executable, *_B03_COMMON, "-n", "12",
     "--junitxml=.local/ef02/windows-b03-serial-xdist-v1/xdist.junit.xml"),
)
_B03_TIMEOUT = 600
_B03_MEMORY_POLICY = "NO_JOB_MEMORY_LIMIT"
_B03_OUTPUT_LIMIT = 8 * 1024 ** 2


def _diagnostic_root(checkout: Path) -> Path:
    # This is a source-owned artifact location, not an allowed edit scope.
    # Do not feed .local through (or weaken) source safe_path/checked_path.
    path = checkout.resolve()
    for part in B03_DIAGNOSTIC_ROOT.parts:
        path /= part
        if is_link(path):
            raise RemoteRequestError("DIAGNOSTIC_ARTIFACT_LINK_DENIED")
    return path


def _diagnostic_source(checkout: Path, source_sha: str, git_executable=None) -> None:
    """Read-only pin/clean gates before and after each child; never reconcile."""
    check_source(checkout, source_sha, git_executable=git_executable)
    head = subprocess.run(git_argv(checkout, "rev-parse", "HEAD", executable=git_executable),
                          capture_output=True, text=True, check=False, timeout=30)
    if head.returncode or head.stdout.strip() != source_sha:
        raise RemoteRequestError("DIAGNOSTIC_STALE_SOURCE")
    if not checkout_diagnostics(checkout, git_executable=git_executable)["clean"]:
        raise RemoteRequestError("DIAGNOSTIC_DIRTY_SOURCE")
    if Path(__file__).resolve() != (checkout / "src/ai_commerce/remote/worker.py").resolve():
        raise RemoteRequestError("DIAGNOSTIC_UNPINNED_OPERATOR")


def _diagnostic_environment(checkout: Path) -> dict[str, str]:
    if os.name != "nt" or sys.platform != "win32" or platform.system() != "Windows":
        raise RemoteRequestError("DIAGNOSTIC_WINDOWS_REQUIRED")
    # The existing execute job sets both VIRTUAL_ENV and sys.executable. A
    # machine-wide Python, even with pytest installed, is not that environment.
    venv = os.environ.get("VIRTUAL_ENV", "")
    expected = Path(venv) / "Scripts/python.exe"
    if (not venv or sys.prefix == sys.base_prefix or Path(sys.prefix).resolve() != Path(venv).resolve()
            or Path(sys.executable).resolve() != expected.resolve()):
        raise RemoteRequestError("DIAGNOSTIC_ISOLATED_PYTHON_REQUIRED")
    if any(not shutil.which(name) for name in ("ffmpeg", "ffprobe")):
        raise RemoteRequestError("DIAGNOSTIC_MEDIA_BINARY_MISSING")
    env = _safe_child_env(checkout)
    for name in tuple(env):
        if name.startswith(("PYTEST_", "AI_COMMERCE_B03_", "B03_")):
            env.pop(name)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # xdist is explicit because plugin autoload and inherited pytest options
    # are disabled. Git guard variables are preserved unchanged.
    env["PYTEST_PLUGINS"] = "xdist.plugin"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _windows_diagnostic_job():
    """Contain the pytest/media tree in a kill-on-close Job without a memory cap."""
    import ctypes
    from ctypes import wintypes as wt

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wt.DWORD), ("working_min", ctypes.c_size_t),
                    ("working_max", ctypes.c_size_t), ("active", wt.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wt.DWORD), ("scheduling", wt.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in
                    ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]

    class Limits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IO), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t)]

    class Accounting(ctypes.Structure):
        _fields_ = [(n, ctypes.c_longlong) for n in ("user", "kernel", "period_user", "period_kernel")] + [
            (n, wt.DWORD) for n in ("faults", "total", "active", "terminated")]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    kernel.CreateJobObjectW.restype = wt.HANDLE
    kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
    kernel.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wt.DWORD, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle:
        raise RemoteRequestError("DIAGNOSTIC_JOB_UNAVAILABLE")
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(handle)
        raise RemoteRequestError("DIAGNOSTIC_JOB_CONFIGURATION_FAILED")

    class Job:
        def attach(self, child):
            # Child starts suspended: no worker/media spawn before containment.
            if not kernel.AssignProcessToJobObject(handle, wt.HANDLE(int(child._handle))):
                child.kill()
                child.wait(timeout=10)
                raise RemoteRequestError("DIAGNOSTIC_JOB_ASSIGN_FAILED")
            native = ctypes.WinDLL("ntdll")
            native.NtResumeProcess.argtypes = [wt.HANDLE]
            native.NtResumeProcess.restype = ctypes.c_long
            if native.NtResumeProcess(wt.HANDLE(int(child._handle))) != 0:
                self.stop()
                raise RemoteRequestError("DIAGNOSTIC_CHILD_RESUME_FAILED")

        def stop(self):
            if not kernel.TerminateJobObject(handle, 1):
                raise RemoteRequestError("DIAGNOSTIC_CHILD_TERMINATION_UNPROVEN")

        def close(self):
            try:
                self.stop()
                deadline = time.monotonic() + 10
                while True:
                    accounting = Accounting()
                    if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(accounting),
                                                             ctypes.sizeof(accounting), None):
                        raise RemoteRequestError("DIAGNOSTIC_CHILD_TERMINATION_UNPROVEN")
                    if accounting.active == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise RemoteRequestError("DIAGNOSTIC_CHILD_TERMINATION_UNPROVEN")
                    time.sleep(0.1)
            finally:
                kernel.CloseHandle(handle)

    return Job()


def _run_diagnostic_child(argv: tuple[str, ...], *, checkout: Path, env: dict) -> tuple[int, bytes, str]:
    job = _windows_diagnostic_job()
    child = None
    try:
        # Raw output is bounded and ephemeral. Only an allowlisted projection
        # is persisted; assertion tracebacks can contain private fixture paths.
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(argv, cwd=checkout, env=env, stdout=output,
                                     stderr=subprocess.STDOUT, creationflags=0x08000004)
            job.attach(child)
            deadline = time.monotonic() + _B03_TIMEOUT
            problem = ""
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    problem = "DIAGNOSTIC_TIMEOUT"
                elif os.fstat(output.fileno()).st_size > _B03_OUTPUT_LIMIT:
                    problem = "DIAGNOSTIC_OUTPUT_LIMIT"
                if problem:
                    job.stop()
                    break
                time.sleep(0.1)
            child.wait(timeout=10)
            # Closing the Job kills residual children before the next phase.
            output.seek(0)
            raw = output.read(_B03_OUTPUT_LIMIT + 1)
            if len(raw) > _B03_OUTPUT_LIMIT:
                problem = "DIAGNOSTIC_OUTPUT_LIMIT"
            return child.returncode, raw[:_B03_OUTPUT_LIMIT], problem
    finally:
        try:
            job.close()
        finally:
            # An attach exception can leave the suspended child outside the Job.
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=10)


def _diagnostic_json(path: Path) -> dict:
    if is_link(path) or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("receipt")
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError("duplicate")
            obj[k] = v
        return obj
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))


def _diagnostic_junit(raw: bytes) -> list[str]:
    if not raw or len(raw) > 2 * 1024 ** 2 or b"<!" in raw:
        raise ValueError("junit")
    root = ET.fromstring(raw.decode("utf-8"))
    suites = [root] if root.tag == "testsuite" else list(root)
    if root.tag not in ("testsuite", "testsuites") or len(suites) != 1:
        raise ValueError("suite")
    suite = suites[0]
    if (suite.tag != "testsuite" or suite.get("tests") != "3" or
            any(suite.get(k) != "0" for k in ("failures", "errors", "skipped")) or
            len(list(suite)) != 3):
        raise ValueError("counts")
    nodes = []
    for case in suite:
        if (case.tag != "testcase" or list(case) or
                case.get("classname") != "tests.test_b0_3_attended_worker" or
                not math.isfinite(float(case.attrib["time"])) or float(case.attrib["time"]) < 0):
            raise ValueError("case")
        nodes.append("tests/test_b0_3_attended_worker.py::" + case.attrib["name"])
    if sorted(nodes) != sorted(B03_DIAGNOSTIC_NODEIDS):
        raise ValueError("nodes")
    return nodes


def _diagnostic_proof(root: Path, phase: str, nonce: str, raw: bytes) -> dict:
    """Cross-check JUnit against per-process collection and stage receipts.

    Worker counts are observations of distinct processes, never the requested
    -n argument or a printed marker alone. No JUnit-only success path exists.
    """
    import re
    workers = ["serial"] if phase == "serial" else [f"gw{i}" for i in range(12)]
    directory = root / phase
    junit = root / (phase + ".junit.xml")
    if (is_link(root) or is_link(directory) or is_link(junit)
            or not junit.is_file() or junit.stat().st_size > 2 * 1024 ** 2):
        raise ValueError("artifact link")
    nodes = _diagnostic_junit(junit.read_bytes())
    expected_files = {f"{w}.{kind}.json" for w in workers for kind in ("collection", "session")}
    if phase == "xdist":
        expected_files.add("controller.session.json")
    expected_files.update(f"{w}.events.json" for w in workers if (directory / f"{w}.events.json").exists())
    if {p.name for p in directory.iterdir()} != expected_files:
        raise ValueError("receipt set")
    pids, executions, collections, execution_workers = set(), {}, {}, {}
    for worker in workers:
        collection = _diagnostic_json(directory / f"{worker}.collection.json")
        session = _diagnostic_json(directory / f"{worker}.session.json")
        if (set(collection) != {"version", "nonce", "worker", "pid", "nodeids"} or
                set(session) != {"version", "nonce", "worker", "pid", "workers_requested", "start", "end", "exitstatus"}):
            raise ValueError("receipt shape")
        for receipt in (collection, session):
            if (type(receipt["version"]) is not int or receipt["version"] != 1
                    or receipt["nonce"] != nonce or receipt["worker"] != worker):
                raise ValueError("binding")
        pid = collection["pid"]
        if type(pid) is not int or pid <= 0 or pid in pids or session["pid"] != pid:
            raise ValueError("pid")
        pids.add(pid)
        if (sorted(collection["nodeids"]) != sorted(nodes) or type(session["exitstatus"]) is not int
                or session["exitstatus"] != 0 or
                phase == "serial" and session["workers_requested"] != 0):
            raise ValueError("collection")
        if (not all(type(session[k]) in (int, float) and math.isfinite(session[k]) for k in ("start", "end"))
                or not 0 < session["start"] <= session["end"]):
            raise ValueError("clock")
        collections[worker] = collection["nodeids"]
        event_path = directory / f"{worker}.events.json"
        events = _diagnostic_json(event_path) if event_path.exists() else {"events": []}
        if set(events) != {"events"} or not isinstance(events["events"], list):
            raise ValueError("event shape")
        for event in events["events"]:
            if set(event) != {"version", "nonce", "worker", "pid", "nodeid", "when", "outcome", "duration", "at"}:
                raise ValueError("event shape")
            node = event["nodeid"]
            if (type(event["version"]) is not int or event["version"] != 1
                    or event["nonce"] != nonce or event["worker"] != worker or event["pid"] != pid
                    or node not in nodes or event["outcome"] != "passed"
                    or event["when"] not in ("setup", "call", "teardown")
                    or type(event["duration"]) not in (int, float)
                    or not math.isfinite(event["duration"]) or event["duration"] < 0
                    or type(event["at"]) not in (int, float)
                    or not session["start"] <= event["at"] <= session["end"]):
                raise ValueError("execution")
            if node in execution_workers and execution_workers[node] != worker:
                raise ValueError("duplicate worker")
            execution_workers[node] = worker
            executions.setdefault(node, []).append(event["when"])
    if set(executions) != set(nodes) or any(sorted(v) != ["call", "setup", "teardown"] for v in executions.values()):
        raise ValueError("stages")
    if phase == "serial":
        if re.search(rb"\d+ workers|\[gw\d+\]", raw) or not re.search(rb"collected 3 items\b", raw):
            raise ValueError("serial workers")
    else:
        controller = _diagnostic_json(directory / "controller.session.json")
        if (set(controller) != {"version", "nonce", "worker", "pid", "workers_requested", "start", "end", "exitstatus"}
                or controller["nonce"] != nonce or controller["worker"] != "controller"
                or type(controller["version"]) is not int or controller["version"] != 1
                or type(controller["exitstatus"]) is not int or controller["exitstatus"] != 0
                or type(controller["pid"]) is not int or controller["pid"] <= 0
                or controller["pid"] in pids or controller["workers_requested"] != 12
                or not all(type(controller[k]) in (int, float) and math.isfinite(controller[k]) for k in ("start", "end"))
                or not 0 < controller["start"] <= controller["end"]
                or re.findall(rb"created: (\d+)/(\d+) workers\b", raw) != [(b"12", b"12")]
                or re.findall(rb"(\d+) workers \[(\d+) items\]", raw) != [(b"12", b"3")]):
            raise ValueError("xdist workers")
    return {"selected_nodeids": list(B03_DIAGNOSTIC_NODEIDS), "selected_count": 3,
            "collected_count": len(nodes), "executed_count": len(executions),
            "junit_nodeids": nodes, "collection_nodeids": collections,
            "execution_nodeids": sorted(executions), "execution_workers": execution_workers,
            "workers_observed": 0 if phase == "serial" else len(pids)}


def _sanitized_diagnostic_output(raw: bytes) -> bytes:
    import re
    allowed = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if re.fullmatch(r"(?:created: 12/12 workers|12 workers \[3 items\]|(?:collecting \.\.\. )?collected 3 items|=+ 3 passed in [0-9.]+s(?: \([0-9:]+\))? =+)", line):
            allowed.append(line)
    return ("\n".join(allowed) + "\n[OTHER_CHILD_OUTPUT_REDACTED]\n").encode("utf-8")


def _diagnostic_artifacts(root: Path, phase: str) -> dict:
    paths = [root / (phase + ".junit.xml"), root / (phase + ".output.log")]
    paths.extend(sorted((root / phase).glob("*.json")))
    artifacts = {}
    for path in paths:
        if is_link(path) or not path.is_file() or path.stat().st_size > 2 * 1024 ** 2:
            raise ValueError("artifact")
        relative = path.relative_to(root).as_posix()
        artifacts[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return artifacts


def _verify_diagnostic_receipt(root: Path, receipt: dict) -> bool:
    try:
        if set(receipt) != {"phase", "status", "error_code", "nonce", "started_at", "ended_at", "elapsed_seconds",
                            "child_status", "child_exit_code", "memory_policy", "memory_limit_bytes",
                            "workers_requested", "workers_observed",
                            "source_sha", "request_fingerprint", "selected_nodeids", "selected_count", "collected_count",
                            "executed_count", "junit_nodeids", "collection_nodeids", "execution_nodeids", "execution_workers", "artifacts"}:
            return False
        phase = receipt["phase"]
        if (phase not in ("serial", "xdist") or receipt["status"] != "DIAGNOSTIC_COMPLETE"
                or receipt["error_code"] is not None or receipt["child_status"] != "EXIT_ZERO"
                or type(receipt["child_exit_code"]) is not int or receipt["child_exit_code"] != 0
                or type(receipt["workers_requested"]) is not int
                or receipt["workers_requested"] != (0 if phase == "serial" else 12)
                or type(receipt["workers_observed"]) is not int
                or not isinstance(receipt["nonce"], str) or len(receipt["nonce"]) != 64
                or any(c not in "0123456789abcdef" for c in receipt["nonce"])
                or receipt["memory_policy"] != _B03_MEMORY_POLICY or receipt["memory_limit_bytes"] is not None
                or any(type(receipt[k]) is not int or receipt[k] != 3
                       for k in ("selected_count", "collected_count", "executed_count"))
                or any(not isinstance(receipt[k], str) or len(receipt[k]) != length
                       or any(c not in "0123456789abcdef" for c in receipt[k])
                       for k, length in (("source_sha", 40), ("request_fingerprint", 64)))
                or not all(type(receipt[k]) in (int, float) and math.isfinite(receipt[k])
                           for k in ("started_at", "ended_at", "elapsed_seconds"))
                or not 0 < receipt["elapsed_seconds"] <= _B03_TIMEOUT + 30
                or not 0 < receipt["started_at"] <= receipt["ended_at"]):
            return False
        proof = _diagnostic_proof(root, phase, receipt["nonce"], (root / (phase + ".output.log")).read_bytes())
        return (all(receipt[k] == v for k, v in proof.items()) and
                receipt["artifacts"] == _diagnostic_artifacts(root, phase))
    except (KeyError, TypeError, ValueError, OSError, ET.ParseError):
        return False


def execute_diagnostic_batch(*, checkout: Path, batch: dict, issuer_login: str,
                             owner_login: str, issue_number: int, git_executable=None) -> dict:
    """Authenticated no-model route. Native proof exists only after real runs.

    Offline fake-subprocess tests establish control logic only: Windows native
    proof for this source change is NOT_YET_RUN.
    """
    checkout = checkout.resolve()
    report = {"kind": "AI_COMMERCE_REMOTE_DIAGNOSTIC_RESULT", "version": 1,
              "delivery": "DIAGNOSTIC_ONLY", "batch_id": batch["batch_id"], "source_sha": batch["source_sha"],
              "diagnostic_mode": DIAGNOSTIC_MODE, "status": "DIAGNOSTIC_FAILED",
              "memory_policy": _B03_MEMORY_POLICY, "memory_limit_bytes": None,
              "executor_invoked": False, "native_proof": "NOT_YET_RUN", "cases": [],
              "error_code": None, "succeeded": 0, "total": 2}
    try:
        validate_batch(batch, schema=checkout / "contracts/RemoteActionBatch.v1.schema.json",
                       registry={}, issuer_login=issuer_login, owner_login=owner_login)
        if not is_diagnostic_batch(batch) or type(issue_number) is not int or issue_number <= 0:
            raise RemoteRequestError("DIAGNOSTIC_AUTHENTICATED_ISSUE_REQUIRED")
        report.update(authorization="AUTHENTICATED_OWNER_HUMAN", github_issue_number=issue_number,
                      request_fingerprint=fingerprint(batch))
        env = _diagnostic_environment(checkout)
        _diagnostic_source(checkout, batch["source_sha"], git_executable)
        root = _diagnostic_root(checkout)
        # No reuse of logs/receipts, including those left by an interrupted run.
        root.mkdir(parents=True, exist_ok=False)
        for phase, argv in zip(("serial", "xdist"), _B03_COMMANDS):
            _diagnostic_source(checkout, batch["source_sha"], git_executable)
            (root / phase).mkdir()
            nonce = secrets.token_hex(32)
            child_env = {**env, "AI_COMMERCE_B03_PHASE": phase, "AI_COMMERCE_B03_NONCE": nonce}
            # Windows monotonic() can tick more coarsely than a fast synthetic
            # child. A genuine sub-tick interval must not become an invented
            # zero-duration receipt that fails the positive-time proof gate.
            started, clock_start_ns = time.time(), time.perf_counter_ns()
            case = {"phase": phase, "status": "DIAGNOSTIC_FAILED", "error_code": None,
                    "nonce": nonce, "started_at": started,
                    "source_sha": batch["source_sha"], "request_fingerprint": report["request_fingerprint"],
                    "child_status": "NOT_STARTED", "memory_policy": _B03_MEMORY_POLICY, "memory_limit_bytes": None,
                    "workers_requested": 0 if phase == "serial" else 12}
            report["cases"].append(case)
            code, raw, problem = _run_diagnostic_child(argv, checkout=checkout, env=child_env)
            case.update(ended_at=time.time(),
                        elapsed_seconds=(time.perf_counter_ns() - clock_start_ns) / 1_000_000_000,
                        child_status="EXIT_ZERO" if code == 0 and not problem else "CHILD_FAILED",
                        child_exit_code=code if type(code) is int and 0 <= code <= 5 else None)
            (root / (phase + ".output.log")).write_bytes(_sanitized_diagnostic_output(raw))
            _diagnostic_source(checkout, batch["source_sha"], git_executable)
            if problem:
                raise RemoteRequestError(problem)
            try:
                case.update(_diagnostic_proof(root, phase, nonce, raw))
                case["artifacts"] = _diagnostic_artifacts(root, phase)
                if code != 0:
                    raise ValueError("exit")
                case["status"] = "DIAGNOSTIC_COMPLETE"
                _write_json(root / (phase + ".result.json"), case)
                persisted = _diagnostic_json(root / (phase + ".result.json"))
                if persisted != case or not _verify_diagnostic_receipt(root, persisted):
                    raise ValueError("receipt")
            except (KeyError, TypeError, ValueError, OSError, ET.ParseError):
                case.update(status="DIAGNOSTIC_FAILED", error_code="DIAGNOSTIC_EVIDENCE_UNPROVEN")
            _write_json(root / (phase + ".result.json"), case)
        # Re-read both actual artifact bindings after the pair finishes. A
        # failed phase never changes a different phase's observed test result.
        proven = True
        for case in report["cases"]:
            result_path = root / (case["phase"] + ".result.json")
            persisted = _diagnostic_json(result_path)
            if persisted != case or not _verify_diagnostic_receipt(root, persisted):
                case.update(status="DIAGNOSTIC_FAILED", error_code="DIAGNOSTIC_EVIDENCE_UNPROVEN")
                _write_json(result_path, case)
                proven = False
        if proven:
            _diagnostic_source(checkout, batch["source_sha"], git_executable)
            report.update(status="DIAGNOSTIC_COMPLETE", native_proof="WINDOWS_NATIVE_OBSERVED")
        else:
            report["error_code"] = "DIAGNOSTIC_EVIDENCE_UNPROVEN"
    except RemoteRequestError as exc:
        report["error_code"] = str(exc)
    except Exception:
        report["error_code"] = "DIAGNOSTIC_EXECUTION_UNPROVEN"
    if (report["error_code"] and report["cases"] and
            report["cases"][-1]["status"] == "DIAGNOSTIC_FAILED" and report["cases"][-1]["error_code"] is None):
        report["cases"][-1].update(status="DIAGNOSTIC_FAILED", error_code=report["error_code"],
                                     ended_at=time.time(),
                                     elapsed_seconds=(time.perf_counter_ns() - clock_start_ns) / 1_000_000_000)
    report["succeeded"] = sum(c["status"] == "DIAGNOSTIC_COMPLETE" for c in report["cases"])
    if report["status"] != "DIAGNOSTIC_COMPLETE" and report["cases"]:
        report["native_proof"] = "WINDOWS_NATIVE_UNPROVEN"
    return report


class _B03DiagnosticPlugin:
    """Loaded only by the fixed operator's -p option, inert in ordinary tests."""
    def __init__(self, config, phase, nonce):
        self.phase, self.nonce = phase, nonce
        self.worker = (getattr(config, "workerinput", {}).get("workerid", "controller")
                       if phase == "xdist" else "serial")
        self.workers_requested = getattr(config.option, "numprocesses", 0) or 0
        self.directory = Path.cwd() / B03_DIAGNOSTIC_ROOT / phase
        self.start = time.monotonic()
        self.events = []

    def _base(self):
        return {"version": 1, "nonce": self.nonce, "worker": self.worker, "pid": os.getpid()}

    def pytest_collection_finish(self, session):
        if self.worker != "controller":
            _write_json(self.directory / (self.worker + ".collection.json"),
                        {**self._base(), "nodeids": [item.nodeid for item in session.items]})

    def pytest_runtest_logreport(self, report):
        if self.worker == "controller":
            return
        self.events.append({**self._base(), "nodeid": report.nodeid, "when": report.when,
                            "outcome": report.outcome, "duration": report.duration, "at": time.monotonic()})
        if len(self.events) > 9:
            raise ValueError("DIAGNOSTIC_EXTRA_EXECUTION")
        _write_json(self.directory / (self.worker + ".events.json"), {"events": self.events})

    def pytest_sessionfinish(self, session, exitstatus):
        _write_json(self.directory / (self.worker + ".session.json"),
                    {**self._base(), "workers_requested": self.workers_requested,
                     "start": self.start, "end": time.monotonic(), "exitstatus": int(exitstatus)})


def pytest_configure(config):
    phase, nonce = os.environ.get("AI_COMMERCE_B03_PHASE"), os.environ.get("AI_COMMERCE_B03_NONCE", "")
    if phase in ("serial", "xdist") and len(nonce) == 64:
        config.pluginmanager.register(_B03DiagnosticPlugin(config, phase, nonce), "b03-diagnostic-proof")


class LocalBatchWorker:
    def __init__(self, *, checkout: Path, batch: dict, profiles: dict[str, list[str]],
                 github: GitHubTransport, root: Path | None = None, git_executable: str | None = None):
        self.checkout = checkout.resolve()
        self.batch = batch
        self.profiles = profiles
        self.github = github
        configured_git = git_executable if git_executable is not None else os.environ.get("AI_COMMERCE_GIT_EXECUTABLE")
        self.git_executable = resolve_git_executable(configured_git) if configured_git is not None else None
        self.root = (root or (self.checkout / ".local" / "ef02" / batch["batch_id"])).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.approved_reuse = load_approved_candidates(
            self.checkout / ".github" / "ef02" / "approved-candidates.v1.json")
        check_source(self.checkout, batch["source_sha"],
                     **({"git_executable": self.git_executable} if self.git_executable is not None else {}))

    def execute_action(self, action: dict) -> dict:
        """One independent action; claim precedes any executor invocation."""
        if is_diagnostic_batch({"actions": [action]}):
            raise RemoteRequestError("DIAGNOSTIC_SEPARATE_ROUTE_REQUIRED")
        attempt_id = action["attempt_id"]
        aid = action["action_id"]
        path = self.root / (aid + "-" + attempt_id)
        path.mkdir(parents=True, exist_ok=True)
        source = self.batch["source_sha"]
        # Stable fingerprint covers batch/source and exact action profile and scope.
        submitted_action = {k: v for k, v in action.items() if k != "resolved_dependencies"}
        fp = fingerprint({"batch_id": self.batch["batch_id"], "source_sha": source,
                          "action": submitted_action})
        outcome = {"action_id": aid, "attempt_id": attempt_id,
                   "executor": action["executor"], "model": action["model"],
                   "effort": action["effort"], "source_sha": source,
                   "fingerprint": fp, "status": "NOT_STARTED",
                   "error_code": None, "candidate_ref": None, "candidate_sha": None,
                   "review_required": False, "scope_review_required": False,
                   "depends_on": action["depends_on"],
                   "dependency_semantics": "PINNED_DEPENDENCY_TREE_COMPOSITION",
                   "evidence_path": str(path), "executor_invoked": False}
        def finish():
            _write_json(path / "action_result.json", outcome)
            return outcome
        try:
            repair_limit = validate_max_repair_rounds(
                action.get("max_repair_rounds", DEFAULT_MAX_REPAIR_ROUNDS))
        except ValueError as exc:
            outcome.update(status="INVALID_ACTION", error_code="INVALID_MAX_REPAIR_ROUNDS",
                           diagnostic=str(exc))
            return finish()
        outcome["max_repair_rounds"] = repair_limit
        # Security retirement gate only: prevents old WP IDs reaching GitHub claim/provider.
        try:
            reject_retired_wp_identifiers(self.batch, submitted_action)
            reject_executor_test_conflict(submitted_action)
        except RemoteRequestError as exc:
            outcome.update(status="INVALID_ACTION", error_code=str(exc),
                           executor_invoked=False)
            return finish()
        if action.get("reuse_candidate") is not None:
            if action["depends_on"]:
                outcome.update(status="UNPROVEN", error_code="AGGREGATE_REQUIRES_FRESH_VERIFICATION",
                               aggregate_verification={"state": "UNPROVEN", "sibling_pass_reusable": False})
                return finish()
            try:
                entry = approve_reuse(self.batch, action, self.approved_reuse)
                actual = {d["action_id"]: d.get("candidate_sha")
                          for d in action.get("resolved_dependencies", [])}
                for predecessor in entry["depends_on"]:
                    approved_parent = next((v for v in self.approved_reuse["candidates"].values()
                                            if v["action_id"] == predecessor), None)
                    if approved_parent is None or actual.get(predecessor) != approved_parent["candidate_sha"]:
                        raise RemoteTransportError("REUSE_DEPENDENCY_SHA_MISMATCH")
                self.github.verify_reused_candidate(entry)
                # v1 approvals bind tree/profile/run, but do not bind the current
                # test definitions, dependency environment or MT-01 artifacts.
                # Keep the request readable; conservatively withhold a new PASS.
                outcome.update(status="UNPROVEN", error_code="LEGACY_REUSE_MT01_EVIDENCE_REQUIRED",
                               candidate_sha=entry["candidate_sha"],
                               candidate_ref=entry["candidate_ref"], effective_source_sha=entry["parent_sha"],
                               verify_exit_code=None, reused=True, reused_verified=False,
                               reuse_original_run_id=entry["verified_run_id"],
                               reuse_original_attempt_id=entry["attempt_id"],
                               claim_status="APPROVED_REUSE_NO_NEW_CLAIM",
                               executor_invoked=False, changed_files=entry["allowed_paths"])
            except Exception as exc:
                outcome.update(status="REUSE_VERIFICATION_FAILED",
                               error_code=str(exc)[:160], executor_invoked=False,
                               reused=True, reused_verified=False)
            return finish()
        try:
            claim = self.github.claim(source_sha=source, attempt_id=attempt_id, fingerprint=fp)
            outcome["claim_status"] = claim
            if claim != "CLAIMED":
                outcome["status"] = claim
                outcome["error_code"] = claim
                return finish()
        except Exception as exc:
            outcome.update(status="CLAIM_UNAVAILABLE", error_code=type(exc).__name__,
                           diagnostic=str(exc))
            return finish()
        effective = source
        try:
            if any(not candidate_accepted(d) for d in action.get("resolved_dependencies", [])):
                raise RemoteTransportError("DEPENDENCY_BRAIN_SCOPE_REVIEW_REQUIRED")
            merged = self.github.integrated_source(
                source_sha=source, attempt_id=attempt_id,
                dependencies=action.get("resolved_dependencies", []))
            effective = merged["effective_sha"]
            outcome["effective_source_sha"] = effective
            outcome["integration_ref"] = merged["integration_ref"]
            if effective != source:
                # GitHub API created the pinned integration commit: fetch it
                # with credentials scoped to this administrative subprocess.
                token = os.environ.get("GITHUB_TOKEN", "")
                auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
                env = dict(os.environ)
                env["GIT_CONFIG_COUNT"] = "1"
                env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
                env["GIT_CONFIG_VALUE_0"] = "AUTHORIZATION: basic " + auth
                fetched = subprocess.run(
                    git_argv(self.checkout, "fetch", "--no-tags", "--no-write-fetch-head", "origin",
                             merged["integration_ref"], executable=getattr(self, "git_executable", None)),
                    env=env, capture_output=True, text=True, check=False)
                if fetched.returncode:
                    raise RemoteTransportError("INTEGRATION_FETCH_FAILED")
        except Exception as exc:
            outcome.update(status="INTEGRATION_FAILED", error_code=type(exc).__name__,
                           diagnostic=str(exc))
            return finish()
        try:
            worktree = _new_worktree_path(self.checkout)
        except (OSError, ValueError) as exc:
            outcome.update(status="SOURCE_WORKTREE_FAILED",
                           error_code="EF02_WORKTREE_ALLOCATION_FAILED",
                           diagnostic=type(exc).__name__, provider_invocation_proven=False)
            return finish()
        outcome["worktree"] = str(worktree)
        try:
            p = subprocess.run(git_argv(self.checkout, "worktree", "add", "--detach", str(worktree), effective,
                                        executable=getattr(self, "git_executable", None)),
                               capture_output=True, text=True, check=False)
            if p.returncode != 0:
                outcome.update(status="SOURCE_WORKTREE_FAILED",
                               error_code="GIT_WORKTREE_ADD_NONZERO", diagnostic=p.stderr)
                return finish()
            # Check immediately after checkout and BEFORE any provider checkpoint.
            # Only refresh Git's stat cache; never modify source files or ignore
            # a genuinely dirty worktree. Preserve exact actionable paths.
            try:
                checkout = checkout_diagnostics(
                    worktree, refresh_index=True,
                    git_executable=getattr(self, "git_executable", None))
            except GitByteConsistencyError as exc:
                outcome.update(status="SOURCE_WORKTREE_FAILED",
                               error_code="GIT_STATUS_FAILED", diagnostic=str(exc),
                               provider_invocation_proven=False)
                return finish()
            outcome["source_checkout"] = checkout
            if not checkout["clean"]:
                outcome.update(
                    status="SOURCE_WORKTREE_FAILED",
                    error_code="SOURCE_CHECKOUT_INCONSISTENT",
                    diagnostic={"reason": "DIRTY_IMMEDIATELY_AFTER_GIT_WORKTREE_ADD",
                                "entries": checkout["dirty_entries"],
                                "entry_count": checkout["dirty_entry_count"],
                                "index_refresh_exit": checkout["index_refresh_exit"]},
                    provider_invocation_proven=False)
                return finish()
            if action.get("verification_intent") == "HUMAN_FULL" and self.batch.get("actor") != "HUMAN":
                outcome.update(status="INVALID_ACTION", error_code="UNAUTHORIZED_HUMAN_FULL")
                return finish()
            plan = approved_verification_plan(action, self.profiles)
            acceptance = approved_acceptance_plan(submitted_action)
            plan["acceptance"] = acceptance
            outcome["mt01_acceptance"] = acceptance
            outcome["verification_plan"] = plan
            dependencies = action.get("resolved_dependencies", [])
            outcome["aggregate_verification"] = {
                "state": "UNPROVEN" if dependencies else "NOT_AGGREGATE",
                "sibling_pass_reusable": False, "effective_source_sha": effective,
                "dependency_candidates": [d.get("candidate_sha") for d in dependencies]}
            _write_json(path / "verification_plan.json", plan)
            if plan["state"] == "UNPROVEN":
                outcome.update(status="UNPROVEN", error_code="APPROVED_BROAD_VERIFICATION_UNAVAILABLE")
                return finish()
            instruction_file = path / "instruction.txt"
            instruction_file.write_text(action["instruction"], encoding="utf-8")
            verify_file = path / "verify.json"
            verify_file.write_text(json.dumps(plan["argv"]),
                                   encoding="utf-8")
            registry_file = path / "verification_registry.json"
            _write_json(registry_file, self.profiles)
            efdir = path / "ef01"
            verify_budget, action_budget, child_wait_timeout = _temporary_verification_budget(
                human_full_suite_authorized=bool(plan.get("human_full_suite_authorized")))
            argv = [sys.executable, "-m", "ai_commerce.engineering",
                    "--repo", str(worktree), "--work-id", self.batch["batch_id"] + ":" + aid,
                    "--attempt-id", attempt_id, "--executor", action["executor"],
                    "--model", action["model"], "--effort", action["effort"],
                    "--instruction-file", str(instruction_file), "--verify-json", str(verify_file),
                    "--out-dir", str(efdir), "--max-repair-rounds", str(repair_limit),
                    "--action-timeout-seconds", str(action_budget)]
            if verify_budget is not None:
                argv.extend(["--verify-timeout-seconds", str(verify_budget)])
            argv.extend(["--minimum-test", "--registered-only", "--host-slots",
                         str(max(int(os.environ.get("EF02_HOST_SLOTS", "2")), self.batch.get("max_parallel", 2)))])
            argv.extend(["--registered-profiles-json", str(registry_file)])
            if plan.get("human_full_suite_authorized"):
                argv.append("--human-full-suite-authorized")
            for acceptance_id in acceptance["ids"]:
                argv.extend(["--acceptance", acceptance_id])
            for dependency in dependencies:
                candidate_sha = dependency.get("candidate_sha")
                if not candidate_sha:
                    outcome.update(status="UNPROVEN", error_code="DEPENDENCY_CANDIDATE_EVIDENCE_MISSING")
                    return finish()
                argv.extend(["--aggregate-source", candidate_sha])
            if getattr(self, "git_executable", None) is not None:
                argv.extend(["--git-bin", self.git_executable])
            for allowed in action["allowed_paths"]:
                argv.extend(["--allow-path", allowed])
            sandbox_argv = _remote_codex_sandbox_args(action["executor"], windows=(os.name == "nt"))
            argv.extend(sandbox_argv)
            outcome["sandbox_backend"] = "mxc" if sandbox_argv else "executor-default"
            # The distributed claim has already been committed. Record an
            # UNKNOWN write-ahead boundary before touching the EF01 child.
            # Parent interruption before Popen and after Popen must NOT become
            # a "model did not run" or retry-safe claim.
            started = time.time()
            progress = {"action_id": aid, "attempt_id": attempt_id,
                        "source_sha": source, "effective_source_sha": effective,
                        "fingerprint": fp, "claim_status": "CLAIMED",
                        "stage": "BEFORE_EF01_CHILD_SPAWN",
                        "status": "EXECUTION_UNKNOWN",
                        "ef01_child_pid_observed": False,
                        "provider_invocation_proven": False,
                        "replay_authorized": False,
                        "recorded_at": started}
            _write_json(path / "invocation_boundary.json", progress)
            # This independent create-only GitHub ref is chained to the exact
            # distributed claim. Remote uncertainty is fail-closed: do NOT
            # spawn EF01 if confirmation was not received, even if the ref
            # may have been created before an API transport timeout.
            try:
                remote_stage = self.github.pre_spawn_checkpoint(
                    source_sha=source, attempt_id=attempt_id, fingerprint=fp)
            except Exception as exc:
                outcome.update(status="PRESPAWN_CHECKPOINT_UNCONFIRMED",
                               error_code="PRESPAWN_CHECKPOINT_UNCONFIRMED",
                               diagnostic=type(exc).__name__,
                               replay_authorized=False)
                return finish()
            outcome["pre_spawn_remote_ref"] = remote_stage
            _write_json(path / "invocation_remote_confirmed.json",
                        {**progress, "stage": "PRESPAWN_REMOTE_REF_CONFIRMED",
                         "pre_spawn_remote_ref": remote_stage,
                         "remote_confirmation_observed_at": time.time()})
            # Never pass GitHub credentials into untrusted model/verification process.
            child = subprocess.Popen(argv, cwd=self.checkout, env=_safe_child_env(self.checkout),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            outcome.update(executor_invoked=True, process_id=child.pid,
                           process_started_at=started)
            # This records an EF01 process PID, not proof that EF01 launched or
            # completed a provider invocation. Keep the immutable pre-spawn
            # boundary alongside it for reconciliation after parent termination.
            _write_json(path / "invocation_spawned.json",
                        {**progress, "stage": "EF01_CHILD_PID_OBSERVED",
                         "ef01_child_pid_observed": True, "process_id": child.pid,
                         "process_observed_at": time.time()})
            # A remotely readable PID observation survives loss of the
            # Windows workspace. This is NOT evidence of provider invocation.
            # If GitHub confirmation fails, keep running/collecting the child
            # result; never interpret absence as permission to replay.
            try:
                spawned_ref = self.github.spawned_checkpoint(
                    source_sha=source, attempt_id=attempt_id,
                    fingerprint=fp, child_pid=child.pid)
                outcome["spawned_remote_ref"] = spawned_ref
                outcome["spawned_remote_checkpoint"] = "CONFIRMED_NOT_PROVIDER_PROOF"
                _write_json(path / "invocation_spawned_remote_confirmed.json",
                            {**progress, "stage": "SPAWNED_REMOTE_REF_CONFIRMED",
                             "ef01_child_pid_observed": True, "process_id": child.pid,
                             "spawned_remote_ref": spawned_ref,
                             "provider_invocation_proven": False,
                             "remote_confirmation_observed_at": time.time()})
            except Exception as exc:
                outcome["spawned_remote_checkpoint"] = "READ_UNKNOWN"
                outcome["spawned_remote_error_type"] = type(exc).__name__
            timed_out = False
            try:
                stdout, stderr = child.communicate(timeout=child_wait_timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, stderr = child.communicate()
                timed_out = True
                outcome.update(status="EXECUTION_UNKNOWN",
                               error_code="EF01_PROCESS_TIMEOUT")
            outcome["process_finished_at"] = time.time()
            outcome["ef01_exit"] = child.returncode
            (path / "ef01-wrapper.stdout").write_text(stdout, encoding="utf-8")
            (path / "ef01-wrapper.stderr").write_text(stderr, encoding="utf-8")
            result_file = efdir / "engineering_result.json"
            if timed_out:
                # A receipt from a killed child can be stale or partially written.
                # Preserve it on disk, but never classify it as verified or publish it.
                outcome["receipt_after_timeout"] = result_file.exists()
                return finish()
            if not result_file.exists():
                outcome.update(status="EXECUTION_UNKNOWN",
                               error_code="EF01_RESULT_MISSING")
                return finish()
            report = json.loads(result_file.read_text(encoding="utf-8"))
            outcome["ef01_status"] = report.get("status")
            outcome["ef01_error_code"] = report.get("error_code")
            outcome["ef01_started_at"] = report.get("started_at")
            outcome["ef01_finished_at"] = report.get("finished_at")
            outcome["changed_files"] = report.get("changed_files", [])
            outcome["out_of_scope_files"] = report.get("out_of_scope_files", [])
            outcome["scope_review_required"] = report.get("scope_review_required", False)
            outcome["review_required"] = report.get("review_required", False)
            for key in ("scope_valid", "tracked_diff_sha256", "candidate_file_sha256"):
                outcome[key] = report.get(key)
            outcome["verify_exit_code"] = report.get("verify_exit_code")
            outcome["ef01_invocations"] = report.get("invocations", [])
            outcome["invocation_count"] = report.get("invocation_count")
            outcome["repair_rounds_used"] = report.get("repair_rounds_used", 0)
            outcome["repair_stop_reason"] = report.get("stop_reason")
            outcome["ef01_usage"] = report.get("usage")
            outcome["ef01_usage_status"] = report.get("usage_status", "UNKNOWN")
            outcome["pytest_profile"] = report.get("pytest_profile")
            outcome["pytest_profiles"] = report.get("pytest_profiles")
            outcome["observed_pytest_workers"] = report.get("observed_pytest_workers")
            outcome["workers_actual"] = report.get("workers_actual")
            outcome["workers_actual_by_group"] = report.get("workers_actual_by_group")
            outcome["worker_count_provenance"] = report.get("worker_count_provenance")
            outcome["minimum_test"] = report.get("minimum_test")
            # Trust neither a success-shaped receipt after a nonzero process
            # exit nor an EF01 PASS receipt contradicted by verification.
            # Both cases can arise when the EF01 process dies after writing
            # its result. Never promote either to a schedulable PASS.
            if child.returncode != 0 and report.get("status") in VERIFIED_STATUSES:
                outcome.update(status="EXECUTION_UNKNOWN",
                               error_code="EF01_EXIT_NONZERO_WITH_SUCCESS_RECEIPT",
                               receipt_status_untrusted=True, replay_authorized=False)
                return finish()
            if (report.get("status") in VERIFIED_STATUSES
                    and report.get("verify_exit_code") != 0):
                outcome.update(status="VERIFICATION_FAILED",
                               error_code="EF01_VERIFY_EXIT_CONTRADICTS_SUCCESS_RECEIPT",
                               receipt_status_untrusted=True)
                return finish()
            if report.get("status") not in VERIFIED_STATUSES or child.returncode:
                classified = _classify_failed_executor(report, efdir)
                if classified in ("CANDIDATE_READY", REVIEW_STATUS, *VERIFIED_STATUSES):
                    # These are not legitimate EF01 failure statuses and
                    # would otherwise be mistaken for a successful action.
                    outcome.update(status="EXECUTION_UNKNOWN",
                                   error_code="EF01_NONPASS_STATUS_INVALID",
                                   receipt_status_untrusted=True, replay_authorized=False)
                    return finish()
                outcome["status"] = classified
                # Surface exact provider reason instead of generic EXECUTOR_NONZERO.
                outcome["error_code"] = (
                    outcome["status"] if outcome["status"] in (
                        "MODEL_AT_CAPACITY", "QUOTA_EXHAUSTED", "MODEL_UNSUPPORTED")
                    else report.get("error_code") or "EF01_NONPASS")
                return finish()
            if report.get("source_base_sha") != effective:
                outcome.update(status="SOURCE_MISMATCH", error_code="EF01_BASE_MISMATCH")
                return finish()
            if dependencies:
                if not _fresh_aggregate_evidence(
                        report, root=efdir, workspace=worktree, source_sha=effective,
                        sources=[d["candidate_sha"] for d in dependencies], argv=plan["argv"]):
                    outcome.update(status="UNPROVEN", error_code="AGGREGATE_FRESH_COVERAGE_UNCONFIRMED")
                    return finish()
                outcome["aggregate_verification"].update(state="VERIFIED_COMBINED_SCOPE",
                                                         profile=plan["profile"], verify_exit_code=0)
            hashes = report.get("candidate_file_sha256")
            changed = report.get("changed_files")
            if (not isinstance(hashes, dict) or not isinstance(changed, list)
                    or not changed or len(set(changed)) != len(changed)
                    or set(hashes) != set(changed)):
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code="EF01_HASH_MANIFEST_INVALID")
                return finish()
            outside = _outside_scope(changed, action["allowed_paths"])
            review = bool(outside)
            if (report.get("git_head_after") != effective or
                    report.get("scope_valid") is not (not review) or
                    report.get("status") != VERIFIED_STATUSES[int(review)] or
                    report.get("out_of_scope_files", []) != outside or
                    report.get("scope_review_required", False) is not review or
                    report.get("review_required", False) is not review or protected_change(changed) or
                    not ordinary_scope_drift(outside)
                    or report.get("executor_exit_code") != 0 or report.get("denied_actions")):
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code="EF01_SOURCE_SCOPE_OR_PERMISSION_UNCONFIRMED")
                return finish()
            # Reconcile the exact final source/tree again after the EF01 process
            # exits. Candidate transport separately checks bytes before any write.
            snapshot, _ = _snapshot(worktree, action["allowed_paths"], time.monotonic() + 30,
                                   **({"git_executable": self.git_executable} if getattr(self, "git_executable", None) is not None else {}))
            if (snapshot.get("git_dir") != report.get("git_dir") or
                    snapshot["scope_valid"] is not (not review) or any(
                    snapshot[key] != report.get(key) for key in (
                        "git_head_after", "changed_files", "candidate_file_sha256",
                        "tracked_diff_sha256"))):
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code="EF01_VERIFIED_TREE_CHANGED")
                return finish()
            # Scope drift requires a fresh registered broad PASS on these bytes.
            # Never convert an old/partial success assertion into publication proof.
            proof_diagnostics: list[str] = []
            proof_confirmed = _verified_receipt_evidence(
                report, root=efdir, workspace=worktree, source_sha=effective,
                sources=[d["candidate_sha"] for d in dependencies], profiles=self.profiles,
                allowed_paths=action["allowed_paths"], diagnostics=proof_diagnostics)
            aggregate_confirmed = (
                not review or (proof_confirmed and _fresh_aggregate_evidence(
                    report, root=efdir, workspace=worktree, source_sha=effective,
                    sources=[d["candidate_sha"] for d in dependencies], argv=plan["argv"])))
            if not proof_confirmed or not aggregate_confirmed:
                outcome.update(
                    status="UNPROVEN", error_code="EF01_VERIFICATION_PROOF_UNCONFIRMED",
                    verification_proof_diagnostics=proof_diagnostics or
                    ["AGGREGATE_FRESH_EVIDENCE_UNCONFIRMED"])
                return finish()
            evidence = validate_candidate_evidence({
                "version": 1, "status": REVIEW_STATUS if review else "CANDIDATE_READY",
                "review_required": review, "scope_valid": not review,
                "out_of_scope_files": outside, "changed_files": changed,
                "allowed_paths": action["allowed_paths"], "candidate_file_sha256": hashes,
                "tracked_diff_sha256": report["tracked_diff_sha256"],
                "source_sha": source, "effective_source_sha": effective,
                "action_id": aid, "attempt_id": attempt_id, "fingerprint": fp,
                "verification": {"status": report["status"], "exit_code": 0,
                                 "profile": plan["profile"], "argv": plan["argv"],
                                 "evidence_sha256": report["evidence_sha256"],
                                 "report_sha256": hashlib.sha256(result_file.read_bytes()).hexdigest()},
                "provenance": {"executor": action["executor"], "model": action["model"],
                               "effort": action["effort"],
                               "dependency_candidates": [d["candidate_sha"] for d in dependencies],
                               "run_id": os.environ.get("GITHUB_RUN_ID"),
                               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT")},
            })
            outcome.update(candidate_evidence=evidence, candidate_evidence_sha256=evidence_digest(evidence))
            try:
                candidate = self.github.candidate(source_sha=effective, attempt_id=attempt_id,
                                                  workspace=worktree, paths=changed,
                                                  expected_hashes=hashes, evidence=evidence)
            except GitByteConsistencyError as exc:
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code=str(exc).split(":", 1)[0], diagnostic=str(exc))
                return finish()
            except CandidateIntegrityError as exc:
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code=str(exc).split(":", 1)[0], diagnostic=str(exc))
                return finish()
            except Exception as exc:
                outcome.update(status="RESULT_PUBLICATION_FAILED",
                               error_code=type(exc).__name__, diagnostic=str(exc))
                return finish()
            outcome.update(status=evidence["status"], candidate_sha=candidate["candidate_sha"],
                           candidate_ref=candidate["candidate_ref"])
            return finish()
        except Exception as exc:
            # A claim now exists. Rerun requires reconciliation, never automatic reinvocation.
            outcome.update(status="EXECUTION_UNKNOWN", error_code=type(exc).__name__,
                           diagnostic=str(exc))
            return finish()
        finally:
            # Keep failing worktrees for evidence; successful are also left for local inspection.
            pass
