"""Opt-in, bounded AGY headless stream measurement (no additional model calls).

Only a validated terminal result is converted into the preexisting AGY JSON
envelope. Raw init, tool arguments, output, prompts and subagent data are never
written to an evidence artifact by this module.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Sequence

_MAX_EVENTS = 12000
_MAX_LINE_CHARS = 524288
_MAX_STDERR_CHARS = 262144
_MAX_DURATION_SECONDS = 86400.0
_CATEGORIES = ("agent_response", "tool", "other")
_TOOL_KINDS = {
    "read_file": "read", "list_directory": "search", "search": "search",
    "grep_search": "search", "find_files": "search",
    "write_to_file": "write", "edit_file": "write",
    "run_command": "command", "execute_command": "command",
}
_TOOL_BUCKETS = ("read", "search", "write", "command", "other")
_PYTEST_PREFIX = re.compile(
    r"^\s*(?:(?:python(?:\.exe)?|py(?:\.exe)?)\s+-m\s+pytest\b|pytest(?:\.exe)?\b)",
    re.IGNORECASE,
)


def run_agy_stream_profile(
    command: Sequence[str], *, repo: Path, timeout_seconds: float,
    env: dict[str, str], profile: dict[str, Any],
    progress_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ONE AGY process; consume NDJSON without storing untrusted step data.

    A caller must use --print and --output-format stream-json. The profile dict
    is updated even on timeout. No vendor session continuation or retry occurs.
    """
    argv = list(command)
    if (argv.count("--print") != 1 or "--output-format" not in argv or
            argv[argv.index("--output-format") + 1:argv.index("--output-format") + 2] != ["stream-json"]):
        raise ValueError("AGY_STREAM_PROFILE_COMMAND_INVALID")

    started = time.monotonic()
    first_event: float | None = None
    first_step: float | None = None
    terminal_time: float | None = None
    terminal: dict[str, Any] | None = None
    seen_init = False
    seen_result = False
    invalid = False
    tool_error = False
    permission_gate = False
    event_count = 0
    step_events = 0
    stderr_parts: list[str] = []
    stderr_chars = 0
    capture_errors: list[str] = []
    seen_done: set[int] = set()
    counts = {category: 0 for category in _CATEGORIES}
    durations = {category: 0.0 for category in _CATEGORIES}
    tool_kinds = {kind: 0 for kind in _TOOL_BUCKETS}
    pytest_tool_commands = 0
    checkpoint_error: str | None = None
    last_checkpoint_at = started
    last_event_at: float | None = None
    last_step_kind = "none"
    last_step_state = "none"
    last_tool_kind = "none"

    def persist_checkpoint(stage: str) -> None:
        """Persist only fixed enums, counts and relative times, never NDJSON text."""
        nonlocal checkpoint_error, last_checkpoint_at
        if progress_path is None or checkpoint_error is not None:
            return
        safe = {
            "schema": "AI_COMMERCE_AGY_SAFE_PROGRESS_V1",
            "provisional": True,
            "terminal_proven": False,
            "replay_authorized": False,
            "stage": stage,
            "process_pid_observed": proc.pid,
            "elapsed_seconds": round(time.monotonic() - started, 4),
            "last_event_elapsed_seconds": (
                round(last_event_at - started, 4) if last_event_at is not None else None),
            "events_observed": min(event_count, _MAX_EVENTS),
            "step_events_observed": min(step_events, _MAX_EVENTS),
            "last_step_kind": last_step_kind,
            "last_step_state": last_step_state,
            "last_tool_kind": last_tool_kind,
            "steps_done_by_category": dict(counts),
            "tool_calls_by_safe_kind": dict(tool_kinds),
            "permission_gate_observed": bool(permission_gate),
            "tool_error_observed": bool(tool_error),
        }
        try:
            temporary = progress_path.with_name(progress_path.name + ".tmp")
            temporary.write_text(json.dumps(safe, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, progress_path)
            last_checkpoint_at = time.monotonic()
        except (OSError, ValueError, UnicodeError) as exc:
            checkpoint_error = type(exc).__name__

    def drain_stdout(stream) -> None:
        nonlocal first_event, first_step, terminal_time, terminal
        nonlocal seen_init, seen_result, invalid, tool_error, permission_gate
        nonlocal event_count, step_events, pytest_tool_commands
        nonlocal last_event_at, last_step_kind, last_step_state, last_tool_kind
        try:
            while True:
                raw = stream.readline(_MAX_LINE_CHARS + 1)
                if not raw:
                    break
                # A tool can produce an enormous single NDJSON line. Drain it
                # completely, but never accumulate it or publish any substring.
                if len(raw) > _MAX_LINE_CHARS or not raw.endswith("\n"):
                    invalid = True
                    while raw and not raw.endswith("\n"):
                        raw = stream.readline(_MAX_LINE_CHARS + 1)
                    continue
                event_count += 1
                if event_count > _MAX_EVENTS:
                    invalid = True
                    continue
                now = time.monotonic()
                last_event_at = now
                try:
                    item = json.loads(raw)
                except (ValueError, TypeError):
                    invalid = True
                    continue
                if not isinstance(item, dict):
                    invalid = True
                    continue
                kind = item.get("event")
                if kind == "init":
                    if seen_init or seen_result or event_count != 1 or not isinstance(item.get("init"), dict):
                        invalid = True
                    else:
                        seen_init = True
                        first_event = now
                        persist_checkpoint("INIT")
                elif kind == "step_update":
                    if not seen_init or seen_result:
                        invalid = True
                        continue
                    value = item.get("step_update")
                    if not isinstance(value, dict):
                        invalid = True
                        continue
                    index = value.get("step_index")
                    state, step_type = value.get("state"), value.get("step_type")
                    if (type(index) is not int or index < 0 or index > 1000000 or
                            state not in ("ACTIVE", "DONE") or not isinstance(step_type, str)):
                        invalid = True
                        continue
                    if first_step is None:
                        first_step = now
                    step_events += 1
                    last_step_kind = step_type if step_type in _CATEGORIES else "other"
                    last_step_state = state
                    if step_type == "tool":
                        name = value.get("tool_name")
                        last_tool_kind = _TOOL_KINDS.get(name, "other") if isinstance(name, str) else "other"

                        info = value.get("tool_info")
                        if info is not None and not isinstance(info, dict):
                            invalid = True
                        if isinstance(info, dict) and info.get("error") is not None:
                            tool_error = True
                            error = info["error"]
                            # Only a boolean risk classification is persisted.
                            message = error.get("message", "") if isinstance(error, dict) else error
                            if isinstance(message, str) and any(
                                    word in message.lower() for word in ("permission", "approval", "denied")):
                                permission_gate = True
                        if value.get("tool_name") == "ask_permission":
                            permission_gate = True
                    if state == "DONE":
                        if index in seen_done:
                            invalid = True
                            continue
                        seen_done.add(index)
                        category = step_type if step_type in ("agent_response", "tool") else "other"
                        counts[category] += 1
                        if step_type == "tool":
                            name = value.get("tool_name")
                            bucket = _TOOL_KINDS.get(name, "other") if isinstance(name, str) else "other"
                            tool_kinds[bucket] += 1
                            info = value.get("tool_info")
                            parameters = info.get("parameters") if isinstance(info, dict) else None
                            cmd = parameters.get("CommandLine") if isinstance(parameters, dict) else None
                            # Only the count is retained. Never persist command
                            # text, argv, tool output, paths or account details.
                            if (bucket == "command" and isinstance(cmd, str) and
                                    _PYTEST_PREFIX.match(cmd[:512])):
                                pytest_tool_commands += 1
                        duration = value.get("duration_seconds")
                        if duration is not None:
                            if (type(duration) not in (int, float) or
                                    not math.isfinite(duration) or
                                    not 0 <= duration <= _MAX_DURATION_SECONDS):
                                invalid = True
                            else:
                                durations[category] += float(duration)
                    if step_type == "tool" or now - last_checkpoint_at >= 15 or first_step == now:
                        persist_checkpoint("STEP_ACTIVE" if state == "ACTIVE" else "STEP_DONE")
                elif kind == "result":
                    if not seen_init or seen_result or not isinstance(item.get("result"), dict):
                        invalid = True
                        continue
                    seen_result = True
                    terminal_time = now
                    terminal = item["result"]
                    if not isinstance(terminal.get("status"), str) or not terminal["status"]:
                        invalid = True
                    persist_checkpoint("RESULT_EVENT")
                else:
                    invalid = True
        except Exception as exc:
            # No exception content: it may contain private paths or tool data.
            capture_errors.append(type(exc).__name__)

    def drain_stderr(stream) -> None:
        nonlocal stderr_chars, invalid
        try:
            for chunk in iter(lambda: stream.read(8192), ""):
                if not chunk:
                    break
                remaining = _MAX_STDERR_CHARS - stderr_chars
                if len(chunk) > remaining:
                    invalid = True
                if remaining > 0:
                    stderr_parts.append(chunk[:remaining])
                    stderr_chars += min(len(chunk), remaining)
        except Exception as exc:
            capture_errors.append(type(exc).__name__)

    proc = subprocess.Popen(
        argv, cwd=repo, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
    )
    persist_checkpoint("SPAWNED")
    out_thread = threading.Thread(target=drain_stdout, args=(proc.stdout,), daemon=True)
    err_thread = threading.Thread(target=drain_stderr, args=(proc.stderr,), daemon=True)
    out_thread.start()
    err_thread.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait(timeout=15)
    finally:
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)
        if out_thread.is_alive() or err_thread.is_alive() or capture_errors:
            invalid = True
        elapsed = time.monotonic() - started
        valid = (not invalid and not timed_out and seen_init and seen_result and
                 terminal is not None and event_count <= _MAX_EVENTS and
                 not (proc.returncode != 0 and terminal.get("status") == "SUCCESS"))
        persist_checkpoint("TIMED_OUT" if timed_out else "FINISHED")
        profile.clear()
        profile.update({
            "schema": "AI_COMMERCE_AGY_STREAM_PROFILE_V1",
            "safe_checkpoint_status": ("DISABLED" if progress_path is None else
                                       "RECORDED" if checkpoint_error is None else "UNAVAILABLE"),
            "stream_valid": bool(valid),
            "timed_out": timed_out,
            "wall_seconds": round(elapsed, 4),
            "time_to_first_event_seconds": round(first_event - started, 4) if first_event is not None else None,
            "time_to_first_step_seconds": round(first_step - started, 4) if first_step is not None else None,
            "terminal_event_seconds": round(terminal_time - started, 4) if terminal_time is not None else None,
            "events_observed": min(event_count, _MAX_EVENTS),
            "step_events_observed": min(step_events, _MAX_EVENTS),
            "steps_done_by_category": counts,
            "tool_calls_by_safe_kind": tool_kinds,
            "provider_reported_pytest_tool_commands": min(pytest_tool_commands, _MAX_EVENTS),
            "provider_reported_step_seconds_by_category": {
                key: round(durations[key], 4) for key in _CATEGORIES},
            "tool_error_observed": tool_error,
            "permission_gate_observed": permission_gate,
            "terminal_status": terminal.get("status") if valid and terminal.get("status") in (
                "SUCCESS", "ERROR") else "UNKNOWN",
        })
    if timed_out:
        raise subprocess.TimeoutExpired(argv, timeout_seconds)
    # Strictly preserve a single terminal result in the existing AGY JSON
    # envelope. Invalid streams cannot supply a verified provider outcome.
    stdout = json.dumps(terminal, ensure_ascii=False) if profile["stream_valid"] else ""
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, "".join(stderr_parts))
