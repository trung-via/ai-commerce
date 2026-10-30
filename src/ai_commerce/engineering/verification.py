"""Execute MT-01 groups inside EF01's existing action budget; no provider calls."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import time
from typing import Callable, Mapping

from .minimum_test import Evidence, apply_evidence, canonical, file_hashes, select_plan, pytest_counts
from .pytest_profile import require_managed_parallelism, registered_workers


def execute_plan(*, repo: Path, output_dir: Path, base_sha: str, base_files: dict,
                 changed_paths: list[str], required_acs: tuple[str, ...], fallback_argv: list[str],
                 prior: dict[str, Evidence], save: Callable, evidence_files: Mapping[str, str],
                 deadline: float, timeout_seconds: float, registered_only: bool = False,
                 aggregate_sources: tuple[str, ...] = (), groups=None, command_transform=None,
                 registered_profiles=None, on_plan: Callable | None = None) -> tuple:
    started_plan = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    verify_deadline = min(deadline, started_plan + timeout_seconds)
    require_managed_parallelism(repo, fallback_argv, coverage_required=True, registered_only=registered_only)
    before = file_hashes(repo)
    plan = select_plan(repo=repo, base_sha=base_sha, base_files=base_files, candidate_files=before,
                       changed_paths=changed_paths, required_acs=required_acs, fallback_argv=fallback_argv,
                       groups=groups, registered_only=registered_only, aggregate_sources=aggregate_sources,
                       registered_profiles=registered_profiles)
    if command_transform:
        for entry in plan["groups"]:
            argv, decision = command_transform(entry["binding"]["argv"], entry["group"])
            entry["binding"]["argv"] = argv
            entry["pytest_profile"] = decision
            entry["binding"]["pytest_profile"] = {k: decision[k] for k in ("profile", "workers", "host_slots")}
    apply_evidence(plan, prior, output_dir)
    plan["started_at"] = started_at
    plan["selection_seconds"] = time.monotonic() - started_plan
    for entry in plan["groups"]:
        entry.update(actual_argv=None, tests_executed=pytest_counts(b""),
                     workers_requested=entry.get("pytest_profile", {}).get("workers_requested",
                         registered_workers(entry["binding"]["argv"]) if registered_only else 0),
                     workers_actual=None, wall_seconds=0.0)
    if on_plan:
        on_plan(plan)  # Keep failure diagnostics visible in EF01 and the action result.
    save("mt01_plan", "mt01.plan.json", (canonical(plan) + "\n").encode())
    stdout, stderr = [], []
    code = 0
    def finish():
        for group in plan["groups"]:
            if group["state"] not in ("REUSE", "UNPROVEN") and "result" not in group:
                group.update(state="UNPROVEN", execute=False, result="UNPROVEN",
                             reason="NOT_EXECUTED_AFTER_UNPROVEN_GROUP")
        plan["finished_at"] = datetime.now(timezone.utc).isoformat()
        plan["wall_seconds"] = time.monotonic() - started_plan
        plan["narrow_execution_observed"] = not plan["fallback"] and any(
            g.get("actual_exit_code") == 0 and g.get("result") == "PASS" for g in plan["groups"])
        save("mt01_results", "mt01.results.json", (canonical(plan) + "\n").encode())

    def unproven(entry, reason, started):
        entry.update(state="UNPROVEN", result="UNPROVEN", reason=reason,
                     actual_exit_code=None, wall_seconds=time.monotonic() - started,
                     finished_at=datetime.now(timezone.utc).isoformat())

    def observe_workers(entry, out):
        observed = re.search(rb"\b([0-9]+) workers \[|created:\s*([0-9]+)/[0-9]+ workers", out)
        actual = int(observed[1] or observed[2]) if observed else None
        entry["workers_actual"] = actual
        if "pytest_profile" in entry:
            entry["pytest_profile"].update(
                workers_actual=actual, observed_workers=actual,
                execution_status="12_WORKERS_EXECUTED" if actual == 12
                else "12_WORKERS_NOT_EXECUTED" if entry["pytest_profile"]["workers"] == 0
                else "WORKER_COUNT_UNPROVEN")
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
            result = subprocess.run(argv, cwd=repo, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=remaining, check=False)
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
        diagnostics = (out + b"\n" + err).decode("utf-8", "replace").lower()
        worker_mismatch = entry["workers_requested"] == 12 and entry["workers_actual"] not in (None, 12)
        infrastructure = worker_mismatch or any(s in diagnostics for s in (
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
