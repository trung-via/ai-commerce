"""EF01 bounded, attempt-specific source change, targeted verification and evidence.

Local CLI only. Not a remote wake, provider permission check, or AIOS lifecycle.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .adapters import command_for
from .profile import ExecutorSelection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=False)


def _changes(repo: Path) -> list[str]:
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode:
        raise RuntimeError("GIT_STATUS_FAILED: " + status.stderr.strip())
    # porcelain -z gives unambiguous paths including spaces / quoted filenames.
    raw = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1", "-z",
                          "--untracked-files=all"], capture_output=True, check=False)
    if raw.returncode:
        raise RuntimeError("GIT_STATUS_FAILED")
    entries = raw.stdout.split(b"\x00")
    result: list[str] = []
    index = 0
    while index < len(entries) and entries[index]:
        entry = entries[index].decode("utf-8", "replace")
        kind = entry[:2]
        result.append(entry[3:])
        index += 1
        if "R" in kind or "C" in kind:
            index += 1  # extra source path in a rename/copy record
    return sorted(set(result))


def _safe_scope(paths: Sequence[str], prefixes: Sequence[str]) -> bool:
    if not prefixes:
        return True
    for path in paths:
        norm = path.replace("\\", "/")
        if not any(norm == p.rstrip("/") or norm.startswith(p.rstrip("/") + "/")
                   for p in prefixes):
            return False
    return True


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # One attempt identity per folder; never silently overwrite prior evidence.
    with path.open("xb") as f:
        f.write(data)


def _observe_usage(data: bytes, executor: str) -> dict[str, int] | None:
    """Only provider-reported exact, nonnegative counters; unknown stays unknown."""
    candidates: list[dict[str, Any]] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            if executor == "codex" and obj.get("type") == "turn.completed":
                candidates.append(obj.get("usage", {}))
            if executor == "antigravity":
                candidates.append(obj.get("usage", {}))
    for item in reversed(candidates):
        if not isinstance(item, dict):
            continue
        names = ("input_tokens", "cached_input_tokens", "output_tokens")
        if all(type(item.get(n)) is int and item[n] >= 0 for n in names):
            if item["cached_input_tokens"] <= item["input_tokens"]:
                return {n: item[n] for n in names}
    return None


def execute_attempt(*, repo: Path, output_dir: Path, work_id: str, attempt_id: str,
                    selection: ExecutorSelection, instruction: str,
                    verify_argv: Sequence[str], allowed_prefixes: Sequence[str] = (),
                    timeout_seconds: int = 7200, verify_timeout_seconds: int = 1800,
                    codex_bin: str = "codex", antigravity_bin: str = "agy") -> dict[str, Any]:
    """Execute exactly one selected provider and one targeted verification command.

    Does not create commits, push, dispatch, retry, or switch providers.
    The output directory must be ignored/outside the repo to preserve Git attribution.
    """
    repo = repo.resolve()
    output_dir = output_dir.resolve()
    if not work_id.strip() or not attempt_id.strip():
        raise ValueError("work_id and attempt_id must be nonempty")
    if not instruction.strip() or not verify_argv or not all(isinstance(x, str) and x for x in verify_argv):
        raise ValueError("Nonempty instruction and argv-array verification are required")
    if timeout_seconds < 1 or verify_timeout_seconds < 1:
        raise ValueError("Timeouts must be positive")
    if output_dir == repo or repo in output_dir.parents and ".local" not in output_dir.relative_to(repo).parts:
        raise ValueError("Output dir within repo must be under ignored .local/")
    if output_dir.exists():
        raise FileExistsError("EVIDENCE_EXISTS: choose a new attempt/output folder")
    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode:
        raise ValueError("Not a Git repository with HEAD: " + head.stderr.strip())
    if _changes(repo):
        raise ValueError("WORKTREE_DIRTY: commit/stash existing changes before attempt")

    command, stdin = command_for(selection, repo, instruction,
                                 codex_bin=codex_bin, antigravity_bin=antigravity_bin)
    report: dict[str, Any] = {
        "kind": "AI_COMMERCE_ENGINEERING_RESULT", "version": 1,
        "work_id": work_id, "attempt_id": attempt_id, "started_at": _now(),
        "executor": selection.executor, "model": selection.model, "reasoning_effort": selection.effort,
        "selection_source": "HUMAN_EXPLICIT", "source_base_sha": head.stdout.strip(),
        "instruction_sha256": _sha(instruction.encode("utf-8")),
        "repo": str(repo), "status": "NOT_STARTED", "error_code": None,
        "executor_invoked": False, "executor_exit_code": None,
        "verify_argv": list(verify_argv), "verify_exit_code": None,
        "changed_files": [], "scope_valid": None, "git_head_after": None,
        "usage": None, "usage_status": "UNKNOWN", "cost": None, "cost_status": "UNKNOWN",
        "evidence_files": {}, "git_delivery": "LOCAL_DIFF_ONLY_NO_COMMIT_NO_PUSH",
        "live_provider_confirmed": False,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        report["executor_invoked"] = True
        try:
            p = subprocess.run(command, cwd=repo, input=stdin, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout_seconds, check=False)
            report["executor_exit_code"] = p.returncode
            out, err = p.stdout.encode("utf-8"), p.stderr.encode("utf-8")
        except FileNotFoundError as exc:
            out, err = b"", str(exc).encode("utf-8")
            report.update(status="EXECUTOR_UNAVAILABLE", error_code="EXECUTOR_BINARY_MISSING",
                          executor_invoked=False)
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode("utf-8")
            err = (exc.stderr or b"") if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode("utf-8")
            report.update(status="EXECUTOR_FAILED", error_code="EXECUTOR_TIMEOUT")
        _write(output_dir / "executor.stdout.log", out)
        _write(output_dir / "executor.stderr.log", err)
        report["evidence_files"].update(executor_stdout="executor.stdout.log", executor_stderr="executor.stderr.log")
        usage = _observe_usage(out, selection.executor)
        if usage is not None:
            report.update(usage=usage, usage_status="PROVIDER_REPORTED")
        changed = _changes(repo)
        report["changed_files"] = changed
        report["scope_valid"] = _safe_scope(changed, allowed_prefixes)
        report["git_head_after"] = _git(repo, "rev-parse", "HEAD").stdout.strip()
        if report["status"] in ("EXECUTOR_UNAVAILABLE", "EXECUTOR_FAILED"):
            return report
        if report["executor_exit_code"] != 0:
            report.update(status="EXECUTOR_FAILED", error_code="EXECUTOR_NONZERO")
            return report
        if report["git_head_after"] != report["source_base_sha"]:
            report.update(status="GIT_UNEXPECTED_COMMIT", error_code="GIT_HEAD_CHANGED")
            return report
        if not changed:
            report.update(status="NO_CODE_CHANGE", error_code="NO_CHANGED_FILES")
            return report
        if not report["scope_valid"]:
            report.update(status="SCOPE_VIOLATION", error_code="CHANGES_OUTSIDE_ALLOWED_SCOPE")
            return report
        try:
            check = subprocess.run(list(verify_argv), cwd=repo, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=verify_timeout_seconds, check=False)
            report["verify_exit_code"] = check.returncode
            vout, verr = check.stdout.encode("utf-8"), check.stderr.encode("utf-8")
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            vout = (getattr(exc, "stdout", b"") or b"")
            verr = str(exc).encode("utf-8")
            if isinstance(vout, str):
                vout = vout.encode("utf-8")
        _write(output_dir / "verify.stdout.log", vout)
        _write(output_dir / "verify.stderr.log", verr)
        report["evidence_files"].update(verify_stdout="verify.stdout.log", verify_stderr="verify.stderr.log")
        report.update(status="BUILD_VERIFIED_FOR_SCOPE" if report["verify_exit_code"] == 0 else "VERIFICATION_FAILED",
                      error_code=None if report["verify_exit_code"] == 0 else "TARGETED_VERIFY_NONZERO_OR_UNAVAILABLE")
        return report
    finally:
        report["finished_at"] = _now()
        # Snapshot at end of attempt, no publishing or remote side effects.
        _write(output_dir / "engineering_result.json", json.dumps(report, indent=2, ensure_ascii=False,
                                                                   sort_keys=True).encode("utf-8") + b"\n")
