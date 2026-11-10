"""Execute MT-01 groups inside EF01's existing action budget; no provider calls."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import os
import re
import subprocess
import time
import tempfile
import xml.etree.ElementTree as ET
from typing import Callable, Mapping

from .minimum_test import Evidence, apply_evidence, canonical, file_hashes, select_plan, pytest_counts, select_provisional_tests
from .pytest_profile import require_managed_parallelism, registered_workers
from .profile import diagnose_failure


_OBSERVER_PLUGIN = "ai_commerce.engineering.pytest_profile"
_RUNTIME_PLUGIN = "ai_commerce.engineering.profile"


def pytest_observation_env() -> dict[str, str]:
    """Instrument the pytest controller without modifying registered argv."""
    env = os.environ.copy()
    plugins = [item.strip() for item in env.get("PYTEST_PLUGINS", "").split(",") if item.strip()]
    if _OBSERVER_PLUGIN not in plugins:
        plugins.append(_OBSERVER_PLUGIN)
    env["PYTEST_PLUGINS"] = ",".join(plugins)
    return env


@contextmanager
def pytest_execution_env(repo: Path):
    """Own disposable scratch outside the checkout, including nested children.

    Do not clear Git guards, shell/system variables, plugin policy or addopts.
    A poisoned runtime temp location is an environment HOLD, never cleanup of
    arbitrary worktree content. TemporaryDirectory removes only our own tree.
    """
    root = repo.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if temporary_root == root or root in temporary_root.parents:
        raise ValueError("PYTEST_SCRATCH_INSIDE_WORKSPACE")
    # Windows Git may stat <cwd>/<40-char-sha>:<manifest> while resolving
    # revisions. Keep owned names short enough for that ambiguity check too.
    with tempfile.TemporaryDirectory(prefix="p", dir=temporary_root,
                                     ignore_cleanup_errors=True) as scratch:
        env = pytest_observation_env()
        plugins = env["PYTEST_PLUGINS"].split(",")
        if _RUNTIME_PLUGIN not in plugins:
            plugins.append(_RUNTIME_PLUGIN)
        env["PYTEST_PLUGINS"] = ",".join(plugins)
        env.update(TEMP=scratch, TMP=scratch, TMPDIR=scratch,
                   AI_COMMERCE_PYTEST_SCRATCH=scratch,
                   AI_COMMERCE_PYTEST_REPO=str(root))
        yield env


def run_pytest(argv, *, repo: Path, timeout: float, save_junit=None):
    """Synchronous exit and optional JUnit artifact on the unchanged command."""
    with pytest_execution_env(repo) as env:
        result = subprocess.run(argv, cwd=repo, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                check=False, env=env)
        junit = Path(env["AI_COMMERCE_PYTEST_SCRATCH"]) / "junit.xml"
        if save_junit is not None and junit.is_file():
            save_junit(junit.read_bytes())
        return result


def observed_xdist_workers(out: bytes) -> int | None:
    """Accept exactly one complete controller hook receipt, never console guesses."""
    matches = re.findall(rb"(?m)^MT01_XDIST_WORKERS_COLLECTED=([0-9]+)\r?$", out)
    return int(matches[0]) if len(matches) == 1 else None


def observed_console_workers(out: bytes) -> int | None:
    """Diagnostic only; startup/progress text cannot establish workers_actual."""
    match = re.search(rb"\b([0-9]+) workers \[|created:\s*([0-9]+)/[0-9]+ workers", out)
    return int(match[1] or match[2]) if match else None


def git_hook_environment_failure(stdout: bytes, stderr: bytes) -> bool:
    """Native Windows/Git barrier evidence; never authorize hook overrides."""
    text = (stdout + b"\n" + stderr).decode("utf-8", "replace").lower()
    return (("ntcreatedirectoryobject" in text and "0xc0000022" in text)
            or "update aborted by the reference-transaction hook" in text)



# Only this precise optional Windows-symlink test may explain a WinError 1314
# in pytest's SKIPPED summary. Preserve the original stdout and JUnit evidence.
_SYMLINK_SKIP_REASON = "NATIVE_SYMLINK_CREATION_UNPROVEN: WinError 1314"
_SYMLINK_SKIP_CASE = "test_symlink_or_junction_is_never_read_as_source"
_SYMLINK_SKIP_CLASS = (
    "tests.test_hostops_ef02_attempt_reconcile_readonly."
    "EF02Attempt698ReadOnlyTests"
)
_SYMLINK_SKIP_LINE = re.compile(
    rb"SKIPPED \[1\] tests[\\/]+test_hostops_ef02_attempt_reconcile_readonly"
    rb"\.py:[0-9]+: NATIVE_SYMLINK_CREATION_UNPROVEN: WinError 1314"
)


# A second, exact Windows skip occurs in HostOps R02's native reparse
# negative test. Require the independently matching JUnit test identity AND
# summary file/line/reason: never strip other WinError diagnostics.
_SYMLINK_SKIP_CASES = {
    (_SYMLINK_SKIP_CLASS, _SYMLINK_SKIP_CASE): _SYMLINK_SKIP_LINE,
    (
        "tests.test_hostops_ef02_attempt_r02_readonly.EF02ForensicR02Tests",
        "test_symlink_scope_path_is_unsafe",
    ): re.compile(
        rb"SKIPPED \[1\] tests[\\/]+test_hostops_ef02_attempt_r02_readonly"
        rb"\.py:[0-9]+: NATIVE_SYMLINK_CREATION_UNPROVEN: WinError 1314"
    ),
}


def _strip_verified_symlink_skip(
    stdout: bytes, stderr: bytes, exit_code: int, junit: bytes | None,
) -> bytes:
    """Exclude one independently JUnit-backed skip from *classification only*.

    A returned stdout differs only by one verified SKIPPED summary line.
    Anything unrecognized keeps the raw text so real infrastructure faults
    still fail closed. Never promote the skipped security test to a pass.
    """
    if (exit_code != 0 or not junit or len(junit) > 1048576
            or b"winerror" in stderr.lower()
            or b"<!DOCTYPE" in junit.upper() or b"<!ENTITY" in junit.upper()):
        return stdout
    try:
        root = ET.fromstring(junit)
        suites = ([root] if root.tag == "testsuite" else
                  list(root) if root.tag == "testsuites" else [])
        if len(suites) != 1 or suites[0].tag != "testsuite":
            return stdout
        suite = suites[0]
        cases = list(suite.findall("testcase"))
        if (int(suite.get("tests", "-1")) != len(cases)
                or int(suite.get("errors", "-1")) != 0
                or int(suite.get("failures", "-1")) != 0
                or int(suite.get("skipped", "-1")) != 1):
            return stdout
        if any(c.find("failure") is not None or c.find("error") is not None
               for c in cases):
            return stdout
        skipped = [(c, c.find("skipped")) for c in cases
                   if c.find("skipped") is not None]
        if len(skipped) != 1:
            return stdout
        case, skip = skipped[0]
        case_key = (case.get("classname"), case.get("name"))
        if (case_key not in _SYMLINK_SKIP_CASES
                or skip.get("type") != "pytest.skip"
                or (skip.get("message") or "").strip() != _SYMLINK_SKIP_REASON):
            return stdout
    except (ET.ParseError, ValueError, TypeError):
        return stdout

    counts = pytest_counts(stdout)
    if (counts["passed"] != len(cases) - 1
            or counts["passed"] < 1 or counts["skipped"] != 1
            or any(counts[k] != 0 for k in
                   ("failed", "errors", "xfailed", "xpassed"))):
        return stdout

    lines = stdout.splitlines(keepends=True)
    skip_lines = [line for line in lines if
                  line.rstrip(b"\r\n").startswith(b"SKIPPED [")]
    marked = [line for line in lines if b"winerror" in line.lower()]
    if (len(skip_lines) != 1 or marked != skip_lines
            or _SYMLINK_SKIP_CASES[case_key].fullmatch(
                skip_lines[0].rstrip(b"\r\n")) is None):
        return stdout
    # The actual result, count and all original artifacts remain unchanged.
    # Only the exact JUnit-proven diagnostic line is excluded from the
    # substring-based infrastructure classifier.
    return b"".join(line for line in lines if line is not skip_lines[0])


def registered_binding_problems(plan: dict, profiles: Mapping[str, list[str]]) -> list[str]:
    """Early, bounded form of EF02's independent registered-binding check.

    Do not run expensive pytest groups with an ambiguous profile that the
    trusted publisher must reject. This is a *negative* preflight, not a
    replacement for post-execution hash/manifest/evidence validation.
    """
    problems = [] if plan.get("groups") else ["MT01_PLAN_EMPTY"]
    for index, entry in enumerate(plan.get("groups", ())):
        binding = entry.get("binding", {})
        name = binding.get("registered_profile")
        if name == "mt01-component-onboarding":
            if (binding.get("onboarding_data_only") is not True
                    or not binding.get("onboarding_manifest_path")
                    or not binding.get("onboarding_manifest_sha256")):
                problems.append(f"MT01_GROUP_{index}_ONBOARDING_PROOF_MISSING")
        elif (not isinstance(name, str)
                or profiles.get(name) != binding.get("argv")):
            problems.append(f"MT01_GROUP_{index}_REGISTERED_BINDING_REJECTED")
    return problems


def execute_plan(*, repo: Path, output_dir: Path, base_sha: str, base_files: dict,
                 changed_paths: list[str], required_acs: tuple[str, ...], fallback_argv: list[str],
                 prior: dict[str, Evidence], save: Callable, evidence_files: Mapping[str, str],
                 deadline: float, timeout_seconds: float, registered_only: bool = False,
                 aggregate_sources: tuple[str, ...] = (), groups=None, command_transform=None,
                 registered_profiles=None, human_full_suite_authorized: bool = False, on_plan: Callable | None = None,
                 allowed_paths: tuple[str, ...] = ()) -> tuple:
    started_plan = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    verify_deadline = min(deadline, started_plan + timeout_seconds)
    require_managed_parallelism(repo, fallback_argv, coverage_required=True, registered_only=registered_only)
    before = file_hashes(repo)
    plan = select_plan(repo=repo, base_sha=base_sha, base_files=base_files, candidate_files=before,
                       changed_paths=changed_paths, required_acs=required_acs, fallback_argv=fallback_argv,
                       groups=groups, registered_only=registered_only, aggregate_sources=aggregate_sources,
                       registered_profiles=registered_profiles,
                       human_full_suite_authorized=human_full_suite_authorized,
                       allowed_paths=allowed_paths)
    binding_errors = (registered_binding_problems(plan, registered_profiles)
                      if registered_only and registered_profiles is not None else [])
    if command_transform:
        for entry in plan["groups"]:
            argv, decision = command_transform(entry["binding"]["argv"], entry["group"])
            entry["binding"]["argv"] = argv
            entry["pytest_profile"] = decision
            entry["binding"]["pytest_profile"] = {k: decision[k] for k in ("profile", "workers", "host_slots")}
    # Check the command after transformation too. In particular, provisional
    # diagnostics must not execute after a failed registered binding preflight.
    if registered_only and registered_profiles is not None:
        binding_errors += registered_binding_problems(plan, registered_profiles)
    if binding_errors:
        binding_errors = sorted(set(binding_errors))
        plan["reason_codes"] = list(plan.get("reason_codes", ())) + binding_errors
        for entry in plan["groups"]:
            entry.update(state="UNPROVEN", execute=False)
            entry["explanation"] = list(entry.get("explanation", ())) + binding_errors
    preflight_blocked = bool(binding_errors) or any(not entry["execute"] and entry["state"] != "REUSE"
                                                  for entry in plan["groups"])
    if preflight_blocked:
        for entry in plan["groups"]:
            entry.update(state="UNPROVEN", execute=False)
    plan["preflight"] = {"state": "OWNER_ESCALATION" if preflight_blocked else "READY",
                         "source_sha": base_sha, "proves_pass": False,
                         "reason_codes": list(plan.get("reason_codes", ()))[:32]}
    # A provisional test family is only a diagnostic preflight. An actual
    # candidate PASS still requires the final, exact registered full profile.
    provisional = (select_provisional_tests(
        base_files=base_files, candidate_files=before,
        changed_paths=changed_paths, plan=plan, fallback_argv=fallback_argv)
        if registered_only and not human_full_suite_authorized and not preflight_blocked else None)
    if provisional is not None:
        plan["provisional_mapping"] = provisional
    apply_evidence(plan, prior, output_dir)
    plan["started_at"] = started_at
    plan["selection_seconds"] = time.monotonic() - started_plan
    for entry in plan["groups"]:
        entry.update(actual_argv=None, tests_executed=pytest_counts(b""),
                     workers_requested=(registered_workers(entry["binding"]["argv"]) if registered_only
                                        else entry.get("pytest_profile", {}).get("workers", 0)),
                     workers_actual=None, wall_seconds=0.0)
    if on_plan:
        on_plan(plan)  # Keep failure diagnostics visible in EF01 and the action result.
    save("mt01_plan", "mt01.plan.json", (canonical(plan) + "\n").encode())
    stdout, stderr = [], []
    code = 5 if preflight_blocked else 0
    def finish():
        for group in plan["groups"]:
            if group["state"] not in ("REUSE", "UNPROVEN") and "result" not in group:
                group.update(state="UNPROVEN", execute=False, result="UNPROVEN",
                             reason="NOT_EXECUTED_AFTER_UNPROVEN_GROUP")
            # None means the number of unfinished/unobserved tests is unknown;
            # a missing receipt must never be projected as zero unknown tests.
            group["tests_executed"]["unknown"] = 0 if group.get("result") == "PASS" else None
        if provisional is not None and "tests_executed" in provisional:
            provisional["tests_executed"]["unknown"] = (
                0 if provisional.get("result") == "PASS_PREFLIGHT_ONLY" else None)
        plan["finished_at"] = datetime.now(timezone.utc).isoformat()
        plan["wall_seconds"] = time.monotonic() - started_plan
        plan["narrow_execution_observed"] = not plan["fallback"] and any(
            g.get("actual_exit_code") == 0 and g.get("result") == "PASS" for g in plan["groups"])
        reason = next((g.get("reason") for g in plan["groups"] if g.get("result") == "UNPROVEN"), None)
        diagnosis = diagnose_failure({"status": "UNPROVEN", "error_code": (
            "MT01_COVERAGE_UNPROVEN" if preflight_blocked else reason)})
        plan["failure_diagnosis"] = diagnosis.payload() if diagnosis else None
        save("mt01_results", "mt01.results.json", (canonical(plan) + "\n").encode())

    def unproven(entry, reason, started):
        entry.update(state="UNPROVEN", result="UNPROVEN", reason=reason,
                     actual_exit_code=None, wall_seconds=time.monotonic() - started,
                     finished_at=datetime.now(timezone.utc).isoformat())

    def observe_workers(entry, out):
        actual = observed_xdist_workers(out)
        entry["workers_actual"] = actual
        entry["workers_console_reported"] = observed_console_workers(out)
        entry["worker_count_provenance"] = (
            "XDIST_CONTROLLER_COLLECTION_HOOK" if actual is not None else "UNOBSERVED"
        )
        if "pytest_profile" in entry:
            entry["pytest_profile"].update(
                workers_requested=entry["workers_requested"],
                workers_actual=actual, observed_workers=actual,
                worker_count_provenance=entry["worker_count_provenance"],
                execution_status="12_WORKERS_EXECUTED" if actual == 12 and entry["workers_requested"] == 12
                else "SERIAL_PYTEST_EXECUTED" if actual == 0 and entry["workers_requested"] == 0
                else "WORKER_COUNT_UNPROVEN")
    # Run provisional tests BEFORE expensive broad regression. A nonzero
    # determinate result must lead to bounded Verify-Fix without falsely
    # claiming a failed/successful registered verification. Unproven
    # infrastructure or source drift stops both preflight and full suite.
    if provisional is not None:
        argv = provisional["argv"]
        started = time.monotonic()
        try:
            remaining = verify_deadline - started
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout_seconds)
            response = run_pytest(argv, repo=repo, timeout=remaining,
                save_junit=lambda data: save("mt01_provisional_junit", "mt01.provisional.junit.xml", data))
            out, err = response.stdout.encode(), response.stderr.encode()
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode()
            err = exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode()
            save("mt01_provisional_stdout", "mt01.provisional.stdout.log", out)
            save("mt01_provisional_stderr", "mt01.provisional.stderr.log", err)
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_TIMEOUT",
                               actual_exit_code=None)
            finish()
            raise subprocess.TimeoutExpired(argv, timeout_seconds, out, err) from exc
        except OSError:
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_LAUNCH_ERROR",
                               actual_exit_code=None)
            finish()
            raise
        save("mt01_provisional_stdout", "mt01.provisional.stdout.log", out)
        save("mt01_provisional_stderr", "mt01.provisional.stderr.log", err)
        current = file_hashes(repo)
        workers = observed_xdist_workers(out)
        provisional.update(worker_count_provenance=(
                               "XDIST_CONTROLLER_COLLECTION_HOOK" if workers is not None else "UNOBSERVED"),
                           workers_console_reported=observed_console_workers(out),
                           actual_exit_code=response.returncode,
                           wall_seconds=time.monotonic()-started,
                           tests_executed=pytest_counts(out), workers_requested=12,
                           workers_actual=workers)
        if current != before:
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_SOURCE_DRIFT")
            finish()
            raise RuntimeError("MT01_SOURCE_DRIFT_DURING_VERIFICATION")
        diag = (out + b"\n" + err).decode("utf-8", "replace").lower()
        if git_hook_environment_failure(out, err) or workers not in (None, 12) or any(s in diag for s in (
                "internalerror", "worker crashed", "workers crashed",
                "permission denied", "memoryerror", "can't start new thread",
                "failed to start worker", "error while receiving from worker")):
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_INFRASTRUCTURE_ERROR")
            finish()
            return subprocess.CompletedProcess(fallback_argv, 3, out.decode("utf-8","replace"),
                                               err.decode("utf-8","replace")), plan
        if response.returncode == 1 and re.search(
                rb"(?m)^FAILED\s|^=+ FAILURES =+|\b[1-9][0-9]* failed\b", out + b"\n" + err):
            provisional.update(result="FAIL", reason="PROVISIONAL_TEST_FAILED")
            finish()
            return subprocess.CompletedProcess(fallback_argv, 1, out.decode("utf-8","replace"),
                                               err.decode("utf-8","replace")), plan
        if response.returncode != 0 or workers != 12 or not re.search(rb"\b[1-9][0-9]* passed\b", out):
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_NO_VALID_PYTEST_PASS")
            finish()
            return subprocess.CompletedProcess(fallback_argv, 3, out.decode("utf-8","replace"),
                                               err.decode("utf-8","replace")), plan
        provisional["result"] = "PASS_PREFLIGHT_ONLY"
    # Shared verify budget, not a fresh timeout per test group.
    for index, entry in enumerate(plan["groups"]):
        if entry["state"] == "REUSE":
            continue
        if not entry["execute"]:
            entry.update(result="UNPROVEN", reason="NO_APPROVED_SCOPE")
            code = 5
            break
        argv = entry["binding"]["argv"]
        remaining = verify_deadline - time.monotonic()
        started = time.monotonic()
        entry.update(actual_argv=list(argv), started_at=datetime.now(timezone.utc).isoformat())
        try:
            if remaining <= 0:
                entry["actual_argv"] = None
                raise subprocess.TimeoutExpired(argv, timeout_seconds)
            junit_blobs: list[bytes] = []
            def observe_junit(data: bytes) -> None:
                junit_blobs.append(data)
                save(f"mt01_{index}_junit", f"mt01.{index}.junit.xml", data)
            result = run_pytest(argv, repo=repo, timeout=remaining,
                                save_junit=observe_junit)
            out, err = result.stdout.encode(), result.stderr.encode()
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode()
            err = exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode()
            save(f"mt01_{index}_stdout", f"mt01.{index}.stdout.log", out)
            save(f"mt01_{index}_stderr", f"mt01.{index}.stderr.log", err)
            entry["tests_executed"] = pytest_counts(out)
            observe_workers(entry, out)
            unproven(entry, "VERIFY_TIMEOUT", started)
            finish()
            raise subprocess.TimeoutExpired(argv, timeout_seconds, b"\n".join(stdout + [out]),
                                          b"\n".join(stderr + [err])) from exc
        except OSError as exc:
            save(f"mt01_{index}_stderr", f"mt01.{index}.stderr.log", str(exc).encode())
            unproven(entry, "VERIFY_INFRASTRUCTURE_ERROR", started)
            finish()
            raise
        stdout.append(out)
        stderr.append(err)
        refs = {}
        for kind, data in (("stdout", out), ("stderr", err)):
            key = f"mt01_{index}_{kind}"
            save(key, f"mt01.{index}.{kind}.log", data)
            refs[kind] = evidence_files[key]
        entry.update(actual_exit_code=result.returncode, wall_seconds=time.monotonic() - started,
                     finished_at=datetime.now(timezone.utc).isoformat(), tests_executed=pytest_counts(out),
                     artifacts=refs)
        observe_workers(entry, out)
        if file_hashes(repo) != before:
            # Do not preserve any PASS from a verifier that mutated its source.
            prior.clear()
            for previous in plan["groups"]:
                if previous.get("result") == "PASS" or previous["state"] == "REUSE":
                    previous.update(state="UNPROVEN", result="UNPROVEN", reason="MT01_SOURCE_DRIFT_DURING_VERIFICATION")
            unproven(entry, "MT01_SOURCE_DRIFT_DURING_VERIFICATION", started)
            finish()
            raise RuntimeError("MT01_SOURCE_DRIFT_DURING_VERIFICATION")
        diagnostic_out = _strip_verified_symlink_skip(
            out, err, result.returncode,
            junit_blobs[0] if len(junit_blobs) == 1 else None)
        diagnostics = (diagnostic_out + b"\n" + err).decode("utf-8", "replace").lower()
        # A successful -n12 command without a controller receipt is UNPROVEN,
        # even when the quiet console or command line claims twelve workers.
        # A determinate test failure may still enter Verify-Fix when worker
        # startup could not complete, but it cannot become a candidate PASS.
        worker_mismatch = (entry["workers_requested"] == 12 and (
            entry["workers_actual"] not in (None, 12) or
            (result.returncode == 0 and entry["workers_actual"] is None)))
        git_hook_blocked = git_hook_environment_failure(out, err)
        infrastructure = git_hook_blocked or worker_mismatch or any(s in diagnostics for s in (
            "permission denied", "permissionerror", "no module named pytest", "internalerror",
            "worker crashed", "workers crashed", "winerror", "subprocess.timeoutexpired",
            "memoryerror", "cannot allocate memory", "can't start new thread",
            "failed to start worker", "could not start worker", "error while receiving from worker"))
        passed = bool(re.search(rb"\b[1-9][0-9]* passed\b", out))
        if result.returncode == 0 and passed and not infrastructure:
            evidence = Evidence.from_success(entry["binding"], exit_code=0, stdout=out,
                                             artifacts=refs, evidence_root=output_dir)
            save(f"mt01_{index}_evidence", f"mt01.{index}.evidence.json", evidence.payload.encode())
            prior[entry["group"]] = evidence
            entry["evidence_sha256"] = evidence.sha256
            entry["result"] = "PASS"
        else:
            determinate = result.returncode == 1 and not infrastructure and bool(re.search(
                rb"(?m)^FAILED\s|^=+ FAILURES =+|\b[1-9][0-9]* failed\b", out + b"\n" + err))
            entry["result"] = "FAIL" if determinate else "UNPROVEN"
            if not determinate:
                entry.update(state="UNPROVEN", reason="PYTEST_WORKER_COUNT_MISMATCH" if worker_mismatch
                             else "VERIFY_GIT_HOOK_ENVIRONMENT_ERROR" if git_hook_blocked
                             else "VERIFY_INFRASTRUCTURE_ERROR" if infrastructure
                             else "NO_SUCCESSFUL_NONEMPTY_PYTEST_RESULT")
            # Only a known code failure permits collecting other groups, then repair.
            code = 1 if determinate else (result.returncode if result.returncode not in (0, 1) else 3)
            if not determinate:
                break
    finish()
    if not stdout and all(e["state"] == "REUSE" for e in plan["groups"]):
        stdout = [b"MT01: all applicable groups have strict prior successful evidence\n"]
    return subprocess.CompletedProcess(fallback_argv, code, b"\n".join(stdout).decode(),
                                       b"\n".join(stderr).decode()), plan
