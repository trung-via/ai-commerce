"""Execute MT-01 groups inside EF01's existing action budget; no provider calls."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import time
from typing import Callable, Mapping

from .minimum_test import Evidence, apply_evidence, canonical, file_hashes, select_plan
from .pytest_profile import require_managed_parallelism


def execute_plan(*, repo: Path, output_dir: Path, base_sha: str, base_files: dict,
                 changed_paths: list[str], required_acs: tuple[str, ...], fallback_argv: list[str],
                 prior: dict[str, Evidence], save: Callable, evidence_files: Mapping[str, str],
                 deadline: float, timeout_seconds: float, registered_only: bool = False,
                 aggregate_sources: tuple[str, ...] = (), groups=None, command_transform=None,
                 registered_profiles=None) -> tuple:
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
    save("mt01_plan", "mt01.plan.json", (canonical(plan) + "\n").encode())
    stdout, stderr = [], []
    code = 0
    new_evidence: dict[str, Evidence] = {}
    # Shared verify budget, not a fresh timeout per test group.
    verify_deadline = min(deadline, time.monotonic() + timeout_seconds)
    for index, entry in enumerate(plan["groups"]):
        if entry["state"] == "REUSE":
            continue
        if not entry["execute"]:
            code = 5
            break
        argv = entry["binding"]["argv"]
        remaining = verify_deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout_seconds)
        started = time.monotonic()
        try:
            result = subprocess.run(argv, cwd=repo, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=remaining, check=False)
            out, err = result.stdout.encode(), result.stderr.encode()
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode()
            err = exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode()
            save(f"mt01_{index}_stdout", f"mt01.{index}.stdout.log", out)
            save(f"mt01_{index}_stderr", f"mt01.{index}.stderr.log", err)
            entry.update(result="UNPROVEN", actual_exit_code=None, reason="VERIFY_TIMEOUT")
            save("mt01_results", "mt01.results.json", (canonical(plan) + "\n").encode())
            raise subprocess.TimeoutExpired(argv, timeout_seconds, b"\n".join(stdout + [out]),
                                          b"\n".join(stderr + [err])) from exc
        stdout.append(out)
        stderr.append(err)
        refs = {}
        for kind, data in (("stdout", out), ("stderr", err)):
            key = f"mt01_{index}_{kind}"
            save(key, f"mt01.{index}.{kind}.log", data)
            refs[kind] = evidence_files[key]
        observed = re.search(rb"\b([0-9]+) workers \[|created:\s*([0-9]+)/[0-9]+ workers", out)
        entry.update(actual_exit_code=result.returncode, wall_seconds=time.monotonic() - started,
                     actual_argv=argv, workers_requested=entry.get("pytest_profile", {}).get("workers_requested"),
                     workers_actual=int(observed[1] or observed[2]) if observed else None, artifacts=refs)
        if "pytest_profile" in entry:
            entry["pytest_profile"].update(
                workers_actual=entry["workers_actual"], observed_workers=entry["workers_actual"],
                execution_status="12_WORKERS_EXECUTED" if entry["workers_actual"] == 12
                else "12_WORKERS_NOT_EXECUTED" if entry["pytest_profile"]["workers"] == 0
                else "WORKER_COUNT_UNPROVEN")
        if file_hashes(repo) != before:
            # Do not preserve any PASS from a verifier that mutated its source.
            prior.clear()
            raise RuntimeError("MT01_SOURCE_DRIFT_DURING_VERIFICATION")
        diagnostics = (out + b"\n" + err).decode("utf-8", "replace").lower()
        infrastructure = any(s in diagnostics for s in (
            "permission denied", "permissionerror", "no module named pytest", "internalerror",
            "worker crashed", "workers crashed", "winerror", "subprocess.timeoutexpired",
            "memoryerror", "cannot allocate memory", "can't start new thread",
            "failed to start worker", "could not start worker", "error while receiving from worker"))
        passed = bool(re.search(rb"\b[1-9][0-9]* passed\b", out))
        if result.returncode == 0 and passed and not infrastructure:
            evidence = Evidence.from_success(entry["binding"], exit_code=0, stdout=out,
                                             artifacts=refs, evidence_root=output_dir)
            new_evidence[entry["group"]] = evidence
            save(f"mt01_{index}_evidence", f"mt01.{index}.evidence.json", evidence.payload.encode())
            entry["evidence_sha256"] = evidence.sha256
        else:
            determinate = result.returncode == 1 and not infrastructure and bool(re.search(
                rb"(?m)^FAILED\s|^=+ FAILURES =+|\b[1-9][0-9]* failed\b", out + b"\n" + err))
            entry["result"] = "FAIL" if determinate else "UNPROVEN"
            # Only a known code failure permits collecting other groups, then repair.
            code = 1 if determinate else (result.returncode if result.returncode not in (0, 1) else 3)
            if not determinate:
                break
    # Publish successes only after this verification has a stable source. They
    # remain immutable even if the next repair invalidates their bindings.
    prior.update(new_evidence)
    save("mt01_results", "mt01.results.json", (canonical(plan) + "\n").encode())
    if not stdout and all(e["state"] == "REUSE" for e in plan["groups"]):
        stdout = [b"MT01: all applicable groups have strict prior successful evidence\n"]
    return subprocess.CompletedProcess(fallback_argv, code, b"\n".join(stdout).decode(),
                                       b"\n".join(stderr).decode()), plan
