"""EF01 bounded, attempt-specific source change, targeted verification and evidence.

Local CLI only. Not a remote wake, provider permission check, or AIOS lifecycle.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import re
import subprocess
import threading
import time
from subprocess import list2cmdline
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .adapters import command_for
from .git_write_guard import GIT_GUARD_INSTRUCTION, git_write_guard_env
from .profile import ExecutorSelection
from .git import git_argv, resolve_git_executable
from .minimum_test import file_hashes
from .verification import execute_plan
from .pytest_profile import host_capacity, profile_command, worker_decision, xdist_available, require_managed_parallelism


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(repo: Path, *args: str, timeout_seconds: float = 30,
         git_executable: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(git_argv(repo, *args, executable=git_executable), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=False, timeout=timeout_seconds)


def _changes(repo: Path, *, timeout_seconds: float = 30, git_executable: str | None = None) -> list[str]:
    # porcelain -z gives unambiguous paths including spaces / quoted filenames.
    raw = subprocess.run(git_argv(repo, "status", "--porcelain=v1", "-z",
                                 "--untracked-files=all", executable=git_executable), capture_output=True, check=False,
                         timeout=timeout_seconds)
    if raw.returncode:
        raise RuntimeError("GIT_STATUS_FAILED: " + raw.stderr.decode("utf-8", "replace").strip())
    entries = raw.stdout.split(b"\x00")
    result: list[str] = []
    index = 0
    while index < len(entries) and entries[index]:
        entry = entries[index].decode("utf-8", "replace")
        kind = entry[:2]
        result.append(entry[3:])
        index += 1
        if "R" in kind or "C" in kind:
            # Porcelain -z stores destination first, original second. Renames
            # delete the old tree path; copies leave their source unchanged.
            if "R" in kind and index < len(entries) and entries[index]:
                result.append(entries[index].decode("utf-8", "replace"))
            index += 1
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


def _candidate_hashes(repo: Path, paths: Sequence[str]) -> dict[str, str | None]:
    """Snapshot changed file bytes without reading through symlinks outside the repo."""
    hashes: dict[str, str | None] = {}
    for relative in paths:
        candidate = repo / relative
        if candidate.is_symlink():
            hashes[relative] = "SYMLINK:" + os.readlink(candidate)
        elif candidate.is_file():
            hashes[relative] = _sha(candidate.read_bytes())
        else:
            hashes[relative] = None  # deletion or non-regular path
    return hashes


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # One attempt identity per folder; never silently overwrite prior evidence.
    with path.open("xb") as f:
        f.write(data)


def _observe_usage(data: bytes, executor: str) -> dict[str, int] | None:
    """Only provider-reported exact, nonnegative counters; unknown stays unknown."""
    candidates: list[dict[str, Any]] = []
    for obj in _provider_events(data) or []:
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



def _headless_denied_actions(data: bytes, executor: str) -> list[str]:
    """AGY may return SUCCESS/exit 0 when headless tool approval was denied."""
    return sorted({item["tool"] for item in _headless_denial_diagnostics(data, executor)})


def _headless_denial_diagnostics(data: bytes, executor: str, *, repo: Path | None = None,
                                  agy_input_dir: Path | None = None) -> list[dict]:
    """Only structured top-level denials; never infer a target from prose.

    Targets are reduced to a lexical root classification, never copied or opened.
    An unknown/malformed action is still a denial. Raw provider logs stay separate.
    """
    if executor != "antigravity":
        return []
    result: list[dict] = []
    for value in _provider_events(data) or []:
        actions = value.get("denied_actions")
        if not actions:
            continue
        for item in actions if isinstance(actions, list) else [{}]:
            item = item if isinstance(item, dict) else {}
            action = item.get("action")
            tool = action if isinstance(action, str) and action in (
                "read_file", "write_file", "edit_file", "command", "run_command",
                "list_directory", "search") else "UNKNOWN_TOOL"
            target = None
            # These fields may be absent in AGY versions that report only action.
            # Do not parse serialized commands, messages, URLs or prior artifacts.
            for container in (item, item.get("arguments"), item.get("parameters"), item.get("args")):
                if isinstance(container, dict):
                    target = next((container[k] for k in ("path", "file_path", "absolute_path")
                        if isinstance(container.get(k), str) and container[k].strip()), None)
                    if target is not None:
                        break
            scope = "UNKNOWN"
            if target is not None:
                scope = "UNCLASSIFIED"
                if len(target) <= 4096 and not any(ord(c) < 32 for c in target):
                    private_check_path = target
                    windows = bool(re.match(r"^[A-Za-z]:[/\\]|^\\\\", target))
                    paths = ntpath if windows else posixpath
                    if paths.isabs(target):
                        normalized = paths.normcase(paths.normpath(target)).replace("\\", "/")
                        scope = "EXTERNAL"
                        for root, label in ((agy_input_dir, "AGY_INPUT"), (repo, "WORKTREE")):
                            if root is not None:
                                prefix = paths.normcase(paths.normpath(str(root))).replace("\\", "/").rstrip("/")
                                if normalized == prefix or normalized.startswith(prefix + "/"):
                                    scope = label
                                    private_check_path = normalized[len(prefix):]
                                    break
                    else:
                        scope = "RELATIVE"  # no assumption about provider-relative base
                    parts = private_check_path.replace("\\", "/").lower().split("/")
                    if any(p in (".local", ".git", ".aws", ".ssh", ".codex", ".gemini", "credentials", "secrets")
                           or p == ".env" or p.startswith(".env.") for p in parts):
                        scope = "PRIVATE_PATH"
            result.append({"tool": tool, "target_status": (
                "DENIED_TARGET_KNOWN" if target is not None else "DENIED_TARGET_UNKNOWN"),
                "target_scope": scope})
            if len(result) == 32:
                return result
    return result


def _provider_test_observations(data: bytes) -> list[dict]:
    """Bounded provider-reported command results, never official EF01 verification."""
    result = []
    for event in _provider_events(data) or []:
        item = event.get("item")
        if event.get("type") != "item.completed" or not isinstance(item, dict):
            continue
        command, code = item.get("command"), item.get("exit_code")
        if (item.get("type") == "command_execution" and isinstance(command, str)
                and re.match(r"^(?:(?:python(?:[0-9.]+)?|py)(?:\.exe)?\s+-m\s+pytest\b|pytest(?:\.exe)?\b)", command)
                and type(code) is int and -255 <= code <= 255):
            result.append({"phase": "AGENT_INTERNAL_PYTEST", "tool": "pytest",
                "status": "PASSED" if code == 0 else "FAILED", "exit_code": code,
                "authority": "PROVIDER_REPORTED_NOT_EF01_VERIFY"})
            if len(result) == 16:
                break
    return result


def _failure_provenance(inv: dict, *, precondition_instruction_sha256: str,
                         sandbox_backend: str = "executor-default") -> dict | None:
    """Safe classification and stable signature, independent of attempt identity.

    Hash preconditions instead of exposing prompts, argv, file names or log text.
    Registered verify, provider tools and Git publication remain separate origins.
    """
    if inv.get("error_code") is None:
        return None
    phase, tool = "UNKNOWN_EFFECTS", inv["executor"]
    if inv.get("error_code") == "HEADLESS_TOOL_PERMISSION_DENIED":
        phase = "HEADLESS_TOOL_DENIED"
        tools = inv.get("denied_actions", [])
        tool = tools[0] if len(tools) == 1 else "MULTIPLE_OR_UNKNOWN"
    elif inv.get("status") in ("MODEL_AT_CAPACITY", "QUOTA_EXHAUSTED"):
        phase = "PROVIDER_CAPACITY" if inv["status"] == "MODEL_AT_CAPACITY" else "PROVIDER_QUOTA"
    elif inv.get("verify_started_at") or inv.get("verify_completed"):
        phase = "EF01_REGISTERED_VERIFY" if inv.get("verification_origin") == "REGISTERED_EF01" else "EF01_VERIFY"
        tool = "pytest" if _is_pytest(inv.get("verify_argv", [])) else "local-verifier"
        if inv.get("status") in ("EXECUTION_UNKNOWN", "SOURCE_DRIFT", "GIT_UNEXPECTED_COMMIT", "SCOPE_VIOLATION"):
            phase = "UNKNOWN_EFFECTS"
    precondition = {key: inv.get(key) for key in (
        "source_base_sha", "executor", "model", "reasoning_effort", "candidate_file_sha256", "verify_argv")}
    precondition["verify_argv"] = inv.get("requested_verify_argv", inv.get("verify_argv"))
    precondition.update(instruction_sha256=precondition_instruction_sha256,
                        environment={"platform": os.name, "sandbox_backend": sandbox_backend})
    classification = {"phase": phase, "tool": tool, "status": inv["status"],
        "error_code": inv["error_code"], "source_sha": inv["source_base_sha"],
        "executor": inv["executor"], "environment": precondition["environment"],
        "precondition_sha256": _sha(_json_bytes(precondition)),
        "denied_targets": inv.get("denied_action_diagnostics", [])}
    attempt = inv["attempt_id"]
    attempt = attempt if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", attempt) else "UNPROVEN"
    return {**classification, "fingerprint": _sha(_json_bytes(classification)),
            "attempt_id": attempt, "replay_authorized": False}



def _run_codex_with_live_event_mirror(command: list[str], *,
                                      repo: Path, stdin_text: str | None,
                                      timeout_seconds: int,
                                      live_path: Path,
                                       env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Opt-in Codex JSONL mirror, observable BEFORE process completion.

    The default EF01 provider route is unchanged. This opt-in path records the
    original JSONL bytes in a write-through local evidence file; it does not
    interpret them, authorize retries, alter model selection, or publish.
    """
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    capture_errors: list[str] = []

    def drain(stream, parts: list[str], destination: Path | None):
        try:
            if destination is None:
                for line in stream:
                    parts.append(line)
            else:
                with destination.open("w", encoding="utf-8") as handle:
                    for line in stream:
                        parts.append(line)
                        handle.write(line)
                        handle.flush()
        except Exception as exc:
            capture_errors.append(type(exc).__name__)

    proc = subprocess.Popen(
        command, cwd=repo, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
    )
    out_reader = threading.Thread(
        target=drain, args=(proc.stdout, stdout_parts, live_path), daemon=True)
    err_reader = threading.Thread(
        target=drain, args=(proc.stderr, stderr_parts, None), daemon=True)
    out_reader.start()
    err_reader.start()
    def feed_input():
        try:
            if stdin_text is not None:
                proc.stdin.write(stdin_text)
                proc.stdin.flush()
        except (BrokenPipeError, OSError):
            # The original Codex process might reject the input and exit.
            # Its exit/stream still require observation, not a retry.
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
    # Repair feedback can exceed a pipe buffer. Writing it on the supervisor
    # thread would postpone the timeout indefinitely if the CLI stops reading.
    input_writer = threading.Thread(target=feed_input, daemon=True)
    input_writer.start()
    try:
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
            raise subprocess.TimeoutExpired(
                cmd=command, timeout=timeout_seconds,
                output="".join(stdout_parts), stderr="".join(stderr_parts))
    finally:
        input_writer.join(timeout=5)
        out_reader.join(timeout=5)
        err_reader.join(timeout=5)
    if capture_errors or input_writer.is_alive() or out_reader.is_alive() or err_reader.is_alive():
        raise RuntimeError("CODEX_LIVE_EVENT_MIRROR_UNCONFIRMED")
    return subprocess.CompletedProcess(
        args=command, returncode=proc.returncode,
        stdout="".join(stdout_parts), stderr="".join(stderr_parts))

DEFAULT_MAX_REPAIR_ROUNDS = 2
MAX_REPAIR_ROUNDS = 5
DEFAULT_ACTION_TIMEOUT_SECONDS = 7200
MAX_TIMEOUT_SECONDS = 86400
# Stay below cmd.exe's limit too: AGY may be installed as a Windows .cmd shim.
AGY_COMMANDLINE_MAX_UNITS = 8000
AGY_INPUT_CONTEXT = (
    "Use the current isolated source worktree and this invocation's prepared agy_input only. "
    "External archived artifacts are OPTIONAL, including prior GitHub artifacts, sibling worktrees "
    "and runner-owned .local records. Do not read user profiles, credentials or authoritative runner logs. "
    "Use current source and the disposable instruction/repair_feedback.json copies. "
    "If these inputs are insufficient, report UNPROVEN; if a tool is denied, stop without a bypass. "
)


def validate_max_repair_rounds(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_REPAIR_ROUNDS:
        raise ValueError(f"max_repair_rounds must be an integer from 0 to {MAX_REPAIR_ROUNDS}")
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _remaining(deadline: float, limit: float = 30) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("ACTION_TIME_BUDGET_EXHAUSTED")
    return min(limit, remaining)


def _snapshot(repo: Path, prefixes: Sequence[str], deadline: float,
              git_executable: str | None = None) -> tuple[dict, bytes]:
    head = _git(repo, "rev-parse", "HEAD", timeout_seconds=_remaining(deadline), git_executable=git_executable)
    identity = _git(repo, "rev-parse", "--absolute-git-dir", timeout_seconds=_remaining(deadline), git_executable=git_executable)
    if head.returncode or identity.returncode:
        raise RuntimeError("GIT_SOURCE_UNAVAILABLE")
    changed = _changes(repo, timeout_seconds=_remaining(deadline), git_executable=git_executable)
    diff = subprocess.run(git_argv(repo, "diff", "--binary", head.stdout.strip(), executable=git_executable),
                          capture_output=True, check=False, timeout=_remaining(deadline))
    if diff.returncode:
        raise RuntimeError("GIT_DIFF_FAILED")
    confirmed = _git(repo, "rev-parse", "HEAD", timeout_seconds=_remaining(deadline), git_executable=git_executable)
    if confirmed.returncode or confirmed.stdout != head.stdout:
        raise RuntimeError("GIT_SOURCE_CHANGED_DURING_SNAPSHOT")
    return {
        "git_head_after": head.stdout.strip(), "git_dir": identity.stdout.strip(),
        "changed_files": changed, "candidate_file_sha256": _candidate_hashes(repo, changed),
        "tracked_diff_sha256": _sha(diff.stdout), "scope_valid": _safe_scope(changed, prefixes),
    }, diff.stdout


def _outside_scope(paths: Sequence[str], prefixes: Sequence[str]) -> list[str]:
    """Describe ordinary paths requiring scope reconciliation."""
    return sorted(path for path in paths if not _safe_scope([path], prefixes))


def _protected_change(paths: Sequence[str]) -> bool:
    """Do not execute changed candidate tests after control/credential edits."""
    for path in paths:
        normalized = path.replace("\\", "/").lower()
        parts = normalized.split("/")
        if (not normalized or normalized.startswith("/") or
                any(part in ("", ".", "..") for part in parts) or
                any(part in (".git", ".github", ".local", "secrets", "credentials") or
                    part == ".env" or part.startswith(".env.") for part in parts)):
            return True
    return False


def _source_problem(snapshot: dict, initial: dict, *,
                    verify_first_scope: bool = False) -> tuple[str, str] | None:
    if snapshot["git_head_after"] != initial["git_head_after"]:
        return "GIT_UNEXPECTED_COMMIT", "GIT_HEAD_CHANGED"
    if snapshot["git_dir"] != initial["git_dir"]:
        return "SOURCE_DRIFT", "GIT_WORKTREE_IDENTITY_CHANGED"
    if _protected_change(snapshot["changed_files"]):
        return "SCOPE_VIOLATION", "PROTECTED_CHANGED_PATH"
    if any(str(value).startswith("SYMLINK:") for value in
           snapshot.get("candidate_file_sha256", {}).values()):
        return "SCOPE_VIOLATION", "CHANGED_SYMLINK_REQUIRES_REVIEW"
    if not verify_first_scope and not snapshot["scope_valid"]:
        return "SCOPE_VIOLATION", "CHANGES_OUTSIDE_ALLOWED_SCOPE"
    return None


def _provider_problem(data: bytes) -> tuple[str, str] | None:
    """Classify provider-level errors, not arbitrary GitHub/tool results in JSONL.

    Plain CLI text remains supported. A historical error phrase inside a
    successful tool result must never impersonate a provider failure.
    """
    events = _provider_events(data)
    messages: list[str] = []
    failed_events: list[dict] = []
    if events is None:
        messages.append(data.decode("utf-8", "replace").lower())
    else:
        for event in events:
            failed = (event.get("type") in ("turn.failed", "error") or
                      str(event.get("status", "")).lower() in ("error", "failed", "failure") or
                      bool(event.get("error")))
            if not failed:
                continue
            failed_events.append(event)
            error = event.get("error")
            if isinstance(error, str):
                messages.append(error.lower())
            elif isinstance(error, dict):
                messages.extend(error[key].lower() for key in ("message", "code", "type")
                                if isinstance(error.get(key), str))
            for key in ("message", "detail"):
                if isinstance(event.get(key), str):
                    messages.append(event[key].lower())
    for phrases, status in (
        (("model is at capacity", "no capacity available"), "MODEL_AT_CAPACITY"),
        (("resource_exhausted", "quota exceeded", "quota reached", "429 too many requests"), "QUOTA_EXHAUSTED"),
        (("model_not_found", "model is not supported", "unknown model"), "MODEL_UNSUPPORTED"),
        (("model unavailable", "service unavailable"), "EXECUTOR_UNAVAILABLE"),
        (("permission denied", "permissionerror", "approval denied", "approval required",
          "requires approval", "auto-denied", "operation not permitted"), "EXECUTOR_PERMISSION_DENIED"),
    ):
        if any(phrase in message for message in messages for phrase in phrases):
            return status, status
    if failed_events:
        return "EXECUTION_UNKNOWN", "EXECUTOR_OUTCOME_UNCONFIRMED"
    return None


def _provider_events(data: bytes, *, require_complete: bool = False) -> list[dict] | None:
    """Read JSON/JSONL evidence; completion requires an entirely valid stream."""
    message = data.decode("utf-8", "replace").strip()
    if not message:
        return None
    try:
        event = json.loads(message)
    except ValueError:
        events = []
        for line in message.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                if require_complete:
                    return None
                continue
            if isinstance(event, dict):
                events.append(event)
            elif require_complete:
                return None
    else:
        events = [event]
    return events if events and all(isinstance(event, dict) for event in events) else None


def _provider_completed(data: bytes, executor: str) -> bool:
    events = _provider_events(data, require_complete=True)
    if not events:
        return False
    turns = [event for event in events if event.get("type") in (
        "turn.started", "turn.completed", "turn.failed", "error")]
    if turns and turns[-1].get("type") == "turn.completed":
        return True
    return executor == "antigravity" and events[-1].get("status") == "SUCCESS"


def _verify_infrastructure_failure(stdout: bytes, stderr: bytes) -> bool:
    messages = (stdout + b"\n" + stderr).decode("utf-8", "replace").lower()
    return bool(_provider_problem(stdout + b"\n" + stderr) or any(
        text in messages for text in (
            "no module named pytest", "0xc0000142", "3221225794", "-1073741502",
            "[winerror 1920]", "subprocess.timeoutexpired", "worker crashed", "workers crashed",
            "memoryerror", "cannot allocate memory", "can't start new thread",
            "failed to start worker", "could not start worker", "error while receiving from worker")))


def _windows_command_units(command: Sequence[str]) -> int:
    # Count quoted argv in Windows UTF-16 code units, including the terminating NUL.
    return len(list2cmdline(command).encode("utf-16-le")) // 2 + 1


def _pytest_failure(argv: Sequence[str], code: int | None, stdout: bytes, stderr: bytes) -> bool:
    # Pytest: 1 = test failure; 2/3/4/5 = interruption/internal/usage/no tests.
    # Arbitrary CLI nonzero exits never authorize another provider invocation.
    diagnostics = (stdout + b"\n" + stderr).decode("utf-8", "replace")
    return bool(_is_pytest(argv) and code == 1 and
                re.search(r"(?m)^FAILED\s|^=+ FAILURES =+|\b[1-9][0-9]* failed\b", diagnostics))


def _run_invocation(*, repo: Path, output_dir: Path, invocation: dict,
                    initial: dict, expected: dict, selection: ExecutorSelection,
                    prompt: str, feedback: dict | None, verify_argv: Sequence[str],
                    allowed_prefixes: Sequence[str], deadline: float,
                    timeout_seconds: int, verify_timeout_seconds: int,
                    codex_bin: str, antigravity_bin: str,
                    codex_windows_sandbox: str | None,
                    git_executable: str | None = None, mt_context: dict | None = None,
                    pytest_profile: str = "windows-12", pytest_isolation_approved: bool = False,
                    pytest_known_flaky: bool = False, host_slots: int = 1,
                    verify_first_scope: bool = True) -> None:
    inv = invocation
    directory = output_dir / inv["evidence_dir"]
    directory.mkdir(parents=True, exist_ok=False)
    def snapshot():
        options = {"git_executable": git_executable} if git_executable is not None else {}
        return _snapshot(repo, allowed_prefixes, deadline, **options)

    def save(key: str, name: str, data: bytes) -> None:
        _write(directory / name, data)
        inv["evidence_files"][key] = (directory / name).relative_to(output_dir).as_posix()
        inv["evidence_sha256"][key] = _sha(data)
        # Preserve the pre-loop EF01 boundary/log locations for M0-R consumers.
        # These are immutable copies of the FIRST invocation, never last-round overwrites.
        if inv["repair_round"] == 0:
            _write(output_dir / name, data)

    save("instruction", "instruction.txt", prompt.encode("utf-8"))
    if feedback is not None:
        save("repair_feedback", "repair_feedback.json", _json_bytes(feedback))
    # Grant AGY only a disposable input mirror, never the authoritative logs,
    # hashes, provider boundary, or feedback evidence owned by the runner.
    agy_input_dir = None
    if selection.executor == "antigravity":
        agy_input_dir = directory / "agy_input"
        agy_input_dir.mkdir(parents=True, exist_ok=False)
        _write(agy_input_dir / "instruction.txt", prompt.encode("utf-8"))
        if feedback is not None:
            _write(agy_input_dir / "repair_feedback.json", _json_bytes(feedback))
    before, _ = snapshot()
    inv["input_snapshot"] = before
    save("source_before", "source.before.json", _json_bytes(before))
    problem = _source_problem(before, initial, verify_first_scope=verify_first_scope)
    if problem or before != expected:
        status, error = problem or ("SOURCE_DRIFT", "WORKTREE_CHANGED_BETWEEN_INVOCATIONS")
        inv.update(status=status, error_code=error)
        return
    # Preserve the original instruction evidence; guard only the provider wire prompt.
    context = AGY_INPUT_CONTEXT if selection.executor == "antigravity" else ""
    guarded_prompt = GIT_GUARD_INSTRUCTION + "\n\n" + context + prompt
    command, stdin = command_for(selection, repo, guarded_prompt, codex_bin=codex_bin,
                                 antigravity_bin=antigravity_bin,
                                 codex_windows_sandbox=codex_windows_sandbox,
                                 antigravity_evidence_dir=agy_input_dir)
    if selection.executor == "antigravity" and _windows_command_units(command) > AGY_COMMANDLINE_MAX_UNITS:
        # Preserve the complete original instruction/feedback on disk. Shorten only
        # the transported --print argument; never truncate recorded diagnostics.
        compact = (
            GIT_GUARD_INSTRUCTION + " " + AGY_INPUT_CONTEXT +
            "Read and follow the complete authorized instruction file "
            + str((agy_input_dir or directory) / "instruction.txt") + " (UTF-8; SHA256 "
            + _sha(prompt.encode("utf-8")) + "). "
            "Keep the same worktree, source HEAD, executor/model/effort and allowed scope. "
            "This file contains the full original request and any repair evidence references."
        )
        command = list(command)
        if "--print" not in command:
            raise ValueError("AGY_COMMANDLINE_UNSUPPORTED")
        command[command.index("--print") + 1] = compact
        if _windows_command_units(command) > AGY_COMMANDLINE_MAX_UNITS:
            raise ValueError("AGY_COMMANDLINE_LIMIT_EXCEEDED")
        save("transport_instruction", "transport_instruction.txt", compact.encode("utf-8"))
    # Hook configuration applies only to the executor; EF02 owns publication.
    child_env = git_write_guard_env(directory, repo=repo, git_executable=git_executable or "git")
    budget = _remaining(deadline, timeout_seconds)
    boundary = {
        "kind": "AI_COMMERCE_EF01_PROVIDER_BOUNDARY", "version": 1,
        "work_id": inv["work_id"], "attempt_id": inv["attempt_id"],
        "invocation_id": inv["invocation_id"], "recorded_at": _now(),
        "stage": "BEFORE_EXECUTOR_SUBPROCESS", "status": "EXECUTION_UNKNOWN",
        "executor_process_return_observed": False, "provider_invocation_proven": False,
        "provider_effects_reconciled": False, "replay_authorized": False,
    }
    save("provider_boundary", "provider_boundary.json", _json_bytes(boundary))
    inv.update(status="EXECUTION_UNKNOWN", executor_invoked=True, executor_started_at=_now(),
               executor_timeout_seconds=budget)
    out, err = b"", b""
    live = None
    try:
        if selection.executor == "codex" and os.environ.get("AI_COMMERCE_EF01_CODEX_STREAM_EVENTS") == "1":
            live = (output_dir if inv["repair_round"] == 0 else directory) / "executor.stdout.live.jsonl"
            process = _run_codex_with_live_event_mirror(
                command, repo=repo, stdin_text=stdin, timeout_seconds=budget,
                live_path=live, env=child_env)
        else:
            process = subprocess.run(command, cwd=repo, input=stdin, capture_output=True,
                                     text=True, encoding="utf-8", errors="replace",
                                     timeout=budget, check=False, env=child_env)
        inv.update(status="EXECUTOR_RETURNED_UNVERIFIED", executor_exit_code=process.returncode)
        out, err = process.stdout.encode("utf-8"), process.stderr.encode("utf-8")
        save("provider_return_observed", "provider_return_observed.json", _json_bytes({
            **boundary, "stage": "EXECUTOR_SUBPROCESS_RETURNED", "recorded_at": _now(),
            "executor_process_return_observed": True, "executor_exit_code": process.returncode,
        }))
        if process.returncode:
            inv.update(status="EXECUTOR_FAILED", error_code="EXECUTOR_NONZERO")
            if process.returncode < 0 or process.returncode >= 128:
                inv.update(status="EXECUTION_UNKNOWN", error_code="EXECUTOR_INTERRUPTED")
    except FileNotFoundError as exc:
        err = str(exc).encode("utf-8")
        inv.update(status="EXECUTOR_UNAVAILABLE", error_code="EXECUTOR_BINARY_MISSING",
                   executor_invoked=False)
    except subprocess.TimeoutExpired as exc:
        out, err = _stream_bytes(exc.stdout), _stream_bytes(exc.stderr)
        inv.update(status="EXECUTION_UNKNOWN", error_code="EXECUTOR_TIMEOUT")
        save("provider_timeout", "provider_timeout.json", _json_bytes({
            **boundary, "stage": "EXECUTOR_TIMEOUT_OBSERVED", "recorded_at": _now(),
            "error_code": "EXECUTOR_TIMEOUT",
        }))
    except PermissionError as exc:
        err = str(exc).encode("utf-8")
        inv.update(status="EXECUTOR_PERMISSION_DENIED", error_code="EXECUTOR_PERMISSION_DENIED")
    finally:
        inv["executor_finished_at"] = _now()
        save("executor_stdout", "executor.stdout.log", out)
        save("executor_stderr", "executor.stderr.log", err)
        if live is not None and live.exists():
            data = live.read_bytes()
            if live.parent != directory:
                _write(directory / live.name, data)
            inv["evidence_files"]["executor_stdout_live"] = (directory / live.name).relative_to(output_dir).as_posix()
            inv["evidence_sha256"]["executor_stdout_live"] = _sha(data)
    inv["denied_action_diagnostics"] = (
        _headless_denial_diagnostics(out, selection.executor, repo=repo, agy_input_dir=agy_input_dir)
        + _headless_denial_diagnostics(err, selection.executor, repo=repo, agy_input_dir=agy_input_dir))[:32]
    inv["denied_actions"] = sorted({d["tool"] for d in inv["denied_action_diagnostics"]})
    inv["provider_test_observations"] = _provider_test_observations(out)
    inv["provider_return_determinate"] = _provider_completed(out, selection.executor)
    usage = _observe_usage(out, selection.executor)
    if usage is not None:
        inv.update(usage=usage, usage_status="PROVIDER_REPORTED")
    after, diff = snapshot()
    inv.update(after)
    inv["out_of_scope_files"] = _outside_scope(after["changed_files"], allowed_prefixes)
    inv["final_snapshot"] = after
    save("source_after_executor", "source.after_executor.json", _json_bytes(after))
    save("executor_diff", "git.after_executor.diff.patch", diff)
    # Even a provider failure gets a scoped snapshot and cumulative diff.
    save("tracked_diff", "git.tracked.diff.patch", diff)
    problem = _source_problem(after, initial, verify_first_scope=verify_first_scope)
    if problem is None and inv["denied_actions"]:
        problem = ("EXECUTOR_PERMISSION_DENIED", "HEADLESS_TOOL_PERMISSION_DENIED")
    # Interrupted/abnormal exits are UNKNOWN effects even if a partial provider
    # event or stderr mentions a determinate-looking quota/capacity error.
    # Never overwrite EXECUTOR_INTERRUPTED with a recoverable provider status.
    if (problem is None and inv["executor_exit_code"] is not None
            and inv["status"] != "EXECUTION_UNKNOWN"):
        problem = _provider_problem(out) or _provider_problem(err)
    if problem:
        inv.update(status=problem[0], error_code=problem[1])
        return
    if inv["status"] != "EXECUTOR_RETURNED_UNVERIFIED":
        return
    if not after["changed_files"] or after == before:
        inv.update(status="NO_CODE_CHANGE",
                   error_code="REPAIR_NO_CODE_CHANGE" if inv["repair_round"] else "NO_CHANGED_FILES")
        return

    inv.update(status="EXECUTION_UNKNOWN", verify_started_at=_now())
    vout, verr = b"", b""
    try:
        inv["verify_timeout_seconds"] = _remaining(deadline, verify_timeout_seconds)
        registered = bool(mt_context and mt_context["registered_only"])
        inv["verification_origin"] = "REGISTERED_EF01" if registered else "LOCAL_EF01"
        if _is_pytest(verify_argv):
            require_managed_parallelism(repo, list(verify_argv), registered_only=registered)
        decision = worker_decision(
            profile=pytest_profile, capacity=host_capacity(), host_slots=host_slots,
            xdist_available=xdist_available(), isolation_approved=pytest_isolation_approved,
            flaky=pytest_known_flaky, windows=(os.name == "nt"),
            registered_only=registered, registered_argv=list(verify_argv))
        inv["pytest_profile"] = decision
        def transform(argv, group):
            nonlocal decision
            if not _is_pytest(argv):
                return list(argv), decision
            if registered:
                decision = worker_decision(
                    profile=pytest_profile, capacity=host_capacity(), host_slots=host_slots,
                    xdist_available=xdist_available(), windows=(os.name == "nt"),
                    registered_only=True, registered_argv=argv)
                inv["pytest_profile"] = decision
            return profile_command(argv, decision=decision, temp_dir=output_dir / "pytest-temp" / group), decision
        if mt_context is not None:
            check, plan = execute_plan(
                repo=repo, output_dir=output_dir, base_sha=initial["git_head_after"],
                base_files=mt_context["base_files"], changed_paths=after["changed_files"],
                required_acs=mt_context["required_acs"], fallback_argv=list(verify_argv),
                prior=mt_context["prior"], save=save, evidence_files=inv["evidence_files"],
                deadline=deadline, timeout_seconds=inv["verify_timeout_seconds"],
                registered_only=mt_context["registered_only"], aggregate_sources=mt_context["aggregate_sources"],
                groups=mt_context.get("groups"), command_transform=transform,
                registered_profiles=mt_context.get("registered_profiles"),
                on_plan=lambda current: inv.update(minimum_test=current))
            inv["minimum_test"] = plan
        else:
            actual_argv, _ = transform(list(verify_argv), "scope")
            inv["verify_argv"] = actual_argv
            inv["actual_verify_argv"] = actual_argv
            check = subprocess.run(actual_argv, cwd=repo, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=inv["verify_timeout_seconds"], check=False)
        inv.update(verify_exit_code=check.returncode, verify_completed=True)
        vout, verr = check.stdout.encode("utf-8"), check.stderr.encode("utf-8")
        observed = re.search(rb"\b([0-9]+) workers \[|created:\s*([0-9]+)/[0-9]+ workers", vout)
        inv["observed_pytest_workers"] = int(observed[1] or observed[2]) if observed else None
        if mt_context is None:
            decision.update(workers_actual=inv["observed_pytest_workers"],
                            observed_workers=inv["observed_pytest_workers"],
                            execution_status="12_WORKERS_EXECUTED" if inv["observed_pytest_workers"] == 12
                            else "12_WORKERS_NOT_EXECUTED" if decision["workers"] == 0
                            else "WORKER_COUNT_UNPROVEN")
        else:
            inv["pytest_profiles"] = {entry["group"]: entry.get("pytest_profile") for entry in plan["groups"]}
        inv.update(status="VERIFICATION_FAILED", error_code="TARGETED_VERIFY_NONZERO_OR_UNAVAILABLE")
        if mt_context is not None and check.returncode == 5:
            inv.update(status="UNPROVEN", error_code="MT01_COVERAGE_UNPROVEN")
        if check.returncode < 0 or check.returncode >= 128 or (
                _is_pytest(verify_argv) and check.returncode in (2, 3)):
            inv.update(status="EXECUTION_UNKNOWN", error_code="VERIFY_INTERRUPTED_OR_INTERNAL_ERROR")
    except FileNotFoundError as exc:
        inv.update(status="VERIFICATION_FAILED", error_code="VERIFICATION_TOOL_MISSING",
                   verify_diagnostic=str(exc))
    except subprocess.TimeoutExpired as exc:
        vout, verr = _stream_bytes(exc.stdout), _stream_bytes(exc.stderr)
        inv.update(status="EXECUTION_UNKNOWN", error_code="VERIFY_TIMEOUT",
                   verify_diagnostic=str(exc))
    except PermissionError as exc:
        inv.update(status="VERIFICATION_FAILED", error_code="VERIFY_PERMISSION_DENIED",
                   verify_diagnostic=str(exc))
    finally:
        inv["verify_finished_at"] = _now()
        save("verify_stdout", "verify.stdout.log", vout)
        save("verify_stderr", "verify.stderr.log", verr)
    # Available traceback/summary excerpts are inline; full logs and hashes remain referenced.
    inv["verify_failure_details"] = (vout + b"\n" + verr).decode("utf-8", "replace")[-24000:]
    final, diff = snapshot()
    inv.update(final)
    inv["final_snapshot"] = final
    save("source_after_verify", "source.after_verify.json", _json_bytes(final))
    save("verify_diff", "git.after_verify.diff.patch", diff)
    inv["evidence_files"]["tracked_diff"] = inv["evidence_files"]["verify_diff"]
    inv["evidence_sha256"]["tracked_diff"] = inv["evidence_sha256"]["verify_diff"]
    inv["out_of_scope_files"] = _outside_scope(final["changed_files"], allowed_prefixes)
    problem = _source_problem(final, initial, verify_first_scope=verify_first_scope)
    if problem:
        inv.update(status=problem[0], error_code=problem[1] + "_AFTER_VERIFY")
    elif not final["changed_files"]:
        inv.update(status="NO_CODE_CHANGE", error_code="NO_CHANGED_FILES_AFTER_VERIFY")
    elif final != after:
        inv.update(status="SOURCE_DRIFT", error_code="VERIFY_CHANGED_SOURCE")
    elif inv["verify_completed"] and inv["status"] != "EXECUTION_UNKNOWN":
        messages = (vout + b"\n" + verr).decode("utf-8", "replace").lower()
        infrastructure = _verify_infrastructure_failure(vout, verr)
        if "no module named pytest" in messages:
            inv.update(status="VERIFICATION_FAILED", error_code="VERIFICATION_TOOL_MISSING")
        elif infrastructure:
            inv.update(status="VERIFICATION_FAILED", error_code="VERIFY_INFRASTRUCTURE_ERROR")
        elif not inv["provider_return_determinate"]:
            # A verifier success cannot reconcile an incomplete provider return.
            inv.update(status="EXECUTION_UNKNOWN", error_code="EXECUTOR_OUTCOME_UNCONFIRMED")
        elif inv["verify_exit_code"] == 0:
            if final["scope_valid"]:
                inv.update(status="BUILD_VERIFIED_FOR_SCOPE", error_code=None)
            else:
                inv.update(status="VERIFIED_SCOPE_REVIEW_REQUIRED",
                           error_code="OUT_OF_SCOPE_FILES_REQUIRE_RECONCILIATION",
                           scope_review_required=True)
        else:
            inv["repair_eligible"] = _pytest_failure(
                list(verify_argv), inv["verify_exit_code"], vout, verr)


def _stream_bytes(value: bytes | str | None) -> bytes:
    return value if isinstance(value, bytes) else (value or "").encode("utf-8")


def _is_pytest(argv: Sequence[str]) -> bool:
    executable = Path(argv[0]).name.lower()
    return (list(argv[1:3]) == ["-m", "pytest"] and
            (executable.startswith("python") or executable in ("py", "py.exe"))) or (
                executable in ("pytest", "pytest.exe"))



def _repair_prompt(instruction: str, feedback: dict, feedback_file: str, *,
                    executor: str = "codex") -> str:
    """Keep AGY's Windows --print argument small; full diagnostics stay on disk."""
    previous = feedback["previous_invocations"]
    latest = previous[-1]
    keys = ("verify_stdout", "verify_stderr", "tracked_diff", "source_after_verify")
    inline = {key: feedback[key] for key in (
        "source_base_sha", "repo", "work_id", "attempt_id", "repair_round",
        "max_repair_rounds", "allowed_prefixes", "verify_argv", "evidence_root")}
    inline.update(
        repair_feedback_file=feedback_file,
        repair_feedback_sha256=_sha(_json_bytes(feedback)),
        verify_exit_code=latest["verify_exit_code"],
        failure_excerpt=latest.get("verify_failure_details", "")[-4000:],
        latest_artifacts={key: latest["evidence_files"][key] for key in keys},
        latest_artifact_sha256={key: latest["evidence_sha256"][key] for key in keys},
        previous_rounds=[{
            "invocation_id": item["invocation_id"], "status": item["status"],
            "error_code": item["error_code"], "verify_exit_code": item["verify_exit_code"],
            "result_file": item["result_file"], "result_sha256": _sha(_json_bytes(item)),
        } for item in previous],
    )
    inputs = (
        "Read the disposable agy_input/repair_feedback.json copy and its inline "
        "previous_invocations verify_failure_details and source snapshots. "
        "External archived artifacts are OPTIONAL. Runner-root artifact paths/hashes "
        "are provenance references, not required read targets; do not follow them outside "
        "the worktree or agy_input. If the mirrored inputs cannot establish the fix, stop UNPROVEN.\n"
        if executor == "antigravity" else
        "Read the full feedback, source snapshot (changed paths/hashes), logs and diffs "
        "referenced relative to evidence_root. Earlier round diagnostics remain in those artifacts.\n")
    return instruction + (
        "\n\nRepair the determinate scoped pytest failure on this SAME worktree. "
        "Preserve cumulative changes and the original HEAD. "
        "Unexpected ordinary paths remain scope-review evidence, not publication permission. "
        "This is a fresh CLI invocation; no vendor session continuity is assumed. "
        + inputs + json.dumps(inline, ensure_ascii=False, sort_keys=True))


def execute_attempt(*, repo: Path, output_dir: Path, work_id: str, attempt_id: str,
                    selection: ExecutorSelection, instruction: str,
                    verify_argv: Sequence[str], allowed_prefixes: Sequence[str] = (),
                    timeout_seconds: int = 7200, verify_timeout_seconds: int = 1800,
                    max_repair_rounds: int = DEFAULT_MAX_REPAIR_ROUNDS,
                    action_timeout_seconds: int = DEFAULT_ACTION_TIMEOUT_SECONDS,
                    codex_bin: str = "codex", antigravity_bin: str = "agy",
                    codex_windows_sandbox: str | None = None, git_executable: str | None = None,
                    minimum_test: bool = False, acceptance_ids: Sequence[str] = (),
                    registered_only: bool = False, aggregate_sources: Sequence[str] = (),
                    pytest_profile: str = "windows-12", pytest_isolation_approved: bool = False,
                    pytest_known_flaky: bool = False, host_slots: int = 1,
                    registered_profiles: dict[str, list[str]] | None = None,
                    verify_first_scope: bool = True) -> dict[str, Any]:
    """One attempt on an initially clean tree, with bounded same-profile repairs.

    Only a completed scoped pytest code failure permits repair. Each CLI call is
    independent; no vendor session continuation is assumed. No commits or push.
    """
    validate_max_repair_rounds(max_repair_rounds)
    # Validate bad explicit Git/profile configuration before any provider call.
    configured_git = git_executable if git_executable is not None else os.environ.get("AI_COMMERCE_GIT_EXECUTABLE")
    git_executable = resolve_git_executable(configured_git) if configured_git is not None else None
    worker_decision(profile=pytest_profile, capacity=host_capacity(), host_slots=host_slots)
    if registered_profiles is not None and (not isinstance(registered_profiles, dict) or any(
            not isinstance(name, str) or not name or not isinstance(argv, list) or not argv or
            not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in argv) or
            not _is_pytest(argv)
            for name, argv in registered_profiles.items())):
        raise ValueError("MT01_INVALID_REGISTERED_PROFILES")
    for value in (timeout_seconds, verify_timeout_seconds, action_timeout_seconds):
        if type(value) is not int or not 1 <= value <= MAX_TIMEOUT_SECONDS:
            raise ValueError(f"Timeouts must be integers from 1 to {MAX_TIMEOUT_SECONDS}")
    deadline = time.monotonic() + action_timeout_seconds
    repo, output_dir = repo.resolve(), output_dir.resolve()
    if not work_id.strip() or not attempt_id.strip():
        raise ValueError("work_id and attempt_id must be nonempty")
    if not instruction.strip() or not verify_argv or not all(isinstance(x, str) and x for x in verify_argv):
        raise ValueError("Nonempty instruction and argv-array verification are required")
    if output_dir == repo or repo in output_dir.parents and ".local" not in output_dir.relative_to(repo).parts:
        raise ValueError("Output dir within repo must be under ignored .local/")
    if output_dir.exists():
        raise FileExistsError("EVIDENCE_EXISTS: choose a new attempt/output folder")
    initial, _ = _snapshot(repo, allowed_prefixes, deadline,
                           **({"git_executable": git_executable} if git_executable is not None else {}))
    if initial["changed_files"]:
        # A false dirty worktree is commonly produced by non-round-trippable
        # raw GitHub candidate blobs on Windows. Surface the paths rather than
        # forcing another blind EF02 repair. Never hide real modifications.
        from .git_byte_consistency import checkout_diagnostics, GitByteConsistencyError
        try:
            details = checkout_diagnostics(repo, git_executable=git_executable)
            paths = details["dirty_entries"]
        except GitByteConsistencyError:
            paths = initial["changed_files"][:40]
        raise ValueError("WORKTREE_DIRTY: commit/stash existing changes before attempt; paths=" +
                         repr(paths[:40]))
    if minimum_test and not _is_pytest(verify_argv):
        raise ValueError("MT01_REQUIRES_APPROVED_PYTEST_FALLBACK")
    mt_context = ({"base_files": file_hashes(repo), "required_acs": tuple(acceptance_ids),
                   "prior": {}, "registered_only": registered_only,
                   "aggregate_sources": tuple(aggregate_sources),
                   "registered_profiles": registered_profiles} if minimum_test else None)
    command_for(selection, repo, instruction, codex_bin=codex_bin,
                antigravity_bin=antigravity_bin, codex_windows_sandbox=codex_windows_sandbox)
    report: dict[str, Any] = {
        "kind": "AI_COMMERCE_ENGINEERING_RESULT", "version": 1,
        "work_id": work_id, "attempt_id": attempt_id, "started_at": _now(),
        "executor": selection.executor, "model": selection.model, "reasoning_effort": selection.effort,
        "selection_source": "HUMAN_EXPLICIT",
        "sandbox_backend": codex_windows_sandbox or "executor-default",
        "sandbox_policy": "workspace-write" if selection.executor == "codex" else "accept-edits",
        "source_base_sha": initial["git_head_after"], "repo": str(repo),
        "git_executable": git_executable or "git", "minimum_test_enabled": minimum_test,
        "git_write_guard": "EXECUTOR_SCOPED_HOOKS_PLUS_POST_HEAD_CHECK",
        "instruction_sha256": _sha(instruction.encode("utf-8")),
        "status": "NOT_STARTED", "error_code": None, "executor_invoked": False,
        "executor_exit_code": None, "verify_argv": list(verify_argv), "verify_exit_code": None,
        "changed_files": [], "candidate_file_sha256": {}, "tracked_diff_sha256": None,
        "scope_valid": None, "git_head_after": None, "denied_actions": [],
        "usage": None, "usage_status": "UNKNOWN", "cost": None, "cost_status": "UNKNOWN",
        "evidence_files": {}, "git_delivery": "LOCAL_DIFF_ONLY_NO_COMMIT_NO_PUSH",
        "live_provider_confirmed": False, "replay_authorized": False,
        "max_repair_rounds": max_repair_rounds, "action_timeout_seconds": action_timeout_seconds,
        "timeout_seconds": timeout_seconds, "verify_timeout_seconds": verify_timeout_seconds,
        "allowed_prefixes": list(allowed_prefixes),
        "verify_first_scope": verify_first_scope,
        "out_of_scope_files": [], "scope_review_required": False,
        "invocations": [], "repair_rounds_used": 0, "stop_reason": None,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    expected = initial
    seen_failure_fingerprints: set[str] = set()
    try:
        for round_index in range(max_repair_rounds + 1):
            inv = {
                "work_id": work_id, "attempt_id": attempt_id,
                "invocation_id": f"{attempt_id}:invocation-{round_index:03d}",
                "repair_round": round_index, "evidence_dir": f"invocations/{round_index:03d}",
                "executor": selection.executor, "model": selection.model,
                "reasoning_effort": selection.effort, "source_base_sha": report["source_base_sha"],
                "started_at": _now(), "status": "NOT_STARTED", "error_code": None,
                "executor_invoked": False, "executor_exit_code": None,
                "verify_argv": list(verify_argv), "requested_verify_argv": list(verify_argv),
                "verify_exit_code": None, "verify_completed": False,
                "changed_files": [], "candidate_file_sha256": {}, "tracked_diff_sha256": None,
                "scope_valid": None, "git_head_after": None, "denied_actions": [],
                "denied_action_diagnostics": [], "provider_test_observations": [],
                "out_of_scope_files": [], "scope_review_required": False,
                "usage": None, "usage_status": "UNKNOWN", "cost": None, "cost_status": "UNKNOWN",
                "evidence_files": {}, "evidence_sha256": {}, "repair_eligible": False,
                "replay_authorized": False, "provider_return_determinate": False,
            }
            feedback = None
            prompt = instruction
            if round_index:
                feedback = {
                    "source_base_sha": report["source_base_sha"], "repo": str(repo),
                    "work_id": work_id, "attempt_id": attempt_id, "repair_round": round_index,
                    "max_repair_rounds": max_repair_rounds, "allowed_prefixes": list(allowed_prefixes),
                    "verify_argv": list(verify_argv), "evidence_root": str(output_dir),
                    "previous_invocations": report["invocations"],
                }
                # Freeze prior rounds before appending the current mutable record.
                feedback = json.loads(json.dumps(feedback))
                prompt = _repair_prompt(instruction, feedback, inv["evidence_dir"] + "/repair_feedback.json",
                                        executor=selection.executor)
            inv["instruction_sha256"] = _sha(prompt.encode("utf-8"))
            report["invocations"].append(inv)
            try:
                _run_invocation(
                    repo=repo, output_dir=output_dir, invocation=inv, initial=initial, expected=expected,
                    selection=selection, prompt=prompt, feedback=feedback, verify_argv=verify_argv,
                    allowed_prefixes=allowed_prefixes, deadline=deadline, timeout_seconds=timeout_seconds,
                    verify_timeout_seconds=verify_timeout_seconds, codex_bin=codex_bin,
                    antigravity_bin=antigravity_bin, codex_windows_sandbox=codex_windows_sandbox,
                    git_executable=git_executable, mt_context=mt_context,
                    pytest_profile=pytest_profile, pytest_isolation_approved=pytest_isolation_approved,
                    pytest_known_flaky=pytest_known_flaky, host_slots=host_slots,
                    verify_first_scope=verify_first_scope)
            except Exception as exc:
                inv.update(status="EXECUTION_UNKNOWN",
                           error_code="ACTION_TIMEOUT" if isinstance(exc, TimeoutError) else "RUNNER_ERROR",
                           diagnostic=f"{type(exc).__name__}: {exc}", repair_eligible=False)
            except BaseException:
                inv.update(status="EXECUTION_UNKNOWN", error_code="RUNNER_INTERRUPTED", repair_eligible=False)
                raise
            finally:
                inv["finished_at"] = _now()
                inv["result_file"] = inv["evidence_dir"] + "/invocation_result.json"
                provenance = _failure_provenance(inv,
                    precondition_instruction_sha256=report["instruction_sha256"],
                    sandbox_backend=report["sandbox_backend"])
                inv["failure_provenance"] = provenance
                if provenance:
                    signature = provenance["fingerprint"]
                    if inv["repair_eligible"] and signature in seen_failure_fingerprints:
                        inv.update(repair_eligible=False, repair_stop_reason="UNCHANGED_FAILURE_PRECONDITION")
                    seen_failure_fingerprints.add(signature)
                for key in ("status", "error_code", "executor_exit_code", "verify_exit_code",
                            "changed_files", "candidate_file_sha256", "tracked_diff_sha256",
                            "scope_valid", "git_head_after", "denied_actions", "evidence_files",
                            "out_of_scope_files", "scope_review_required", "denied_action_diagnostics",
                            "provider_test_observations", "failure_provenance"):
                    report[key] = inv[key]
                for key in ("minimum_test", "pytest_profile", "pytest_profiles", "observed_pytest_workers"):
                    if key in inv:
                        report[key] = inv[key]
                _write(output_dir / inv["result_file"], _json_bytes(inv))
            if deadline <= time.monotonic():
                report.update(status="EXECUTION_UNKNOWN", error_code="ACTION_TIMEOUT")
                break
            if not inv["repair_eligible"]:
                report["stop_reason"] = inv.get("repair_stop_reason") or inv["error_code"] or inv["status"]
                break
            if round_index == max_repair_rounds:
                report["stop_reason"] = "REPAIR_ROUNDS_EXHAUSTED" if max_repair_rounds else "REPAIRS_DISABLED"
                break
            expected = inv["final_snapshot"]
    except Exception as exc:
        # Missing/failed evidence publication never authorizes the next round.
        report.update(status="EXECUTION_UNKNOWN", error_code="EVIDENCE_WRITE_FAILED",
                      diagnostic=f"{type(exc).__name__}: {exc}")
    finally:
        invoked = [i for i in report["invocations"] if i["executor_invoked"]]
        report["executor_invoked"] = bool(invoked)
        report["invocation_count"] = len(invoked)
        report["repair_rounds_used"] = sum(i["repair_round"] > 0 for i in invoked)
        if invoked and all(i["usage"] is not None for i in invoked):
            report.update(usage={key: sum(i["usage"][key] for i in invoked) for key in invoked[0]["usage"]},
                          usage_status="PROVIDER_REPORTED")
        report["stop_reason"] = report["stop_reason"] or report["error_code"] or report["status"]
        report["finished_at"] = _now()
        _write(output_dir / "engineering_result.json", _json_bytes(report))
    return report
