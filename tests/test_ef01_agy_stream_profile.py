"""Synthetic AGY NDJSON tests: no real model, account or external provider."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from ai_commerce.engineering.agy_stream_profile import run_agy_stream_profile
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.engineering import runner
from test_ef01 import _repo, _fake_binary


def _script(tmp_path, events, *, make_edit=False, delay_seconds=0):
    code = ("import json, time\n"
            + ("from pathlib import Path\nPath('changed.txt').write_text('ok\\n', encoding='utf-8')\n"
               if make_edit else "")
            + "events = " + repr(events) + "\n"
            + "for event in events:\n"
            + "    print(json.dumps(event), flush=True)\n"
            + (f"time.sleep({delay_seconds})\n" if delay_seconds else ""))
    return _fake_binary(tmp_path, "fake_agy_stream", code)


def _valid_events(*, tool_info=None, status="SUCCESS"):
    steps = [
        {"event": "init", "init": {"cwd": "PRIVATE-PATH", "tools": ["read_file"]}},
        {"event": "step_update", "step_update": {
            "step_index": 1, "state": "ACTIVE", "step_type": "agent_response",
            "text_delta": "PRIVATE-PROMPT"}},
        {"event": "step_update", "step_update": {
            "step_index": 1, "state": "DONE", "step_type": "agent_response",
            "duration_seconds": 1.25}},
        {"event": "step_update", "step_update": {
            "step_index": 2, "state": "DONE", "step_type": "tool",
            "tool_name": "read_file", "duration_seconds": 0.5,
            "tool_info": tool_info if tool_info is not None else {
                "parameters": {"path": "SECRET-PATH"},
                "output": "SECRET-RESULT"}}},
        {"event": "result", "result": {"status": status, "response": "done",
            "usage": {"input_tokens": 8, "output_tokens": 2}}},
    ]
    return steps


def _run(tmp_path, events, *, delay_seconds=0, timeout_seconds=10):
    binary = _script(tmp_path, events, delay_seconds=delay_seconds)
    profile = {}
    cmd = [binary, "--print", "task", "--output-format", "stream-json"]
    result = run_agy_stream_profile(
        cmd, repo=tmp_path, timeout_seconds=timeout_seconds,
        env=os.environ.copy(), profile=profile)
    return result, profile


def test_stream_profile_records_only_sanitized_finite_progress(tmp_path):
    result, profile = _run(tmp_path, _valid_events())
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "SUCCESS"
    assert "SECRET-PATH" not in result.stdout
    assert profile["stream_valid"] is True
    assert profile["steps_done_by_category"] == {
        "agent_response": 1, "tool": 1, "other": 0}
    assert profile["provider_reported_step_seconds_by_category"] == {
        "agent_response": 1.25, "tool": 0.5, "other": 0.0}
    assert profile["step_events_observed"] == 3
    assert profile["time_to_first_event_seconds"] is not None
    assert profile["time_to_first_step_seconds"] is not None
    safe = json.dumps(profile, ensure_ascii=False)
    assert all(secret not in safe for secret in (
        "SECRET-PATH", "SECRET-RESULT", "PRIVATE-PROMPT", "PRIVATE-PATH"))
    assert all(key not in profile for key in ("conversation_id", "tool_info", "response"))


@pytest.mark.parametrize("mutator", [
    lambda events: events[:-1],
    lambda events: events + [events[-1]],
    lambda events: events[1:],
    lambda events: events[:1] + [{"event": "unknown"}] + events[1:],
    lambda events: events[:2] + [{"event": "step_update", "step_update": {
        "step_index": 999, "state": "DONE", "step_type": "tool",
        "duration_seconds": -1}}] + events[2:],
])
def test_missing_duplicate_unknown_or_invalid_events_fail_closed(tmp_path, mutator):
    events = mutator(_valid_events())
    result, profile = _run(tmp_path, events)
    assert profile["stream_valid"] is False
    assert result.stdout == ""


def test_stream_tool_error_and_permission_are_never_promoted_to_pass(tmp_path):
    events = _valid_events(tool_info={"error": {"message": "permission denied"},
                                     "parameters": {"path": "SECRET-CONFIG"}})
    result, profile = _run(tmp_path, events)
    assert profile["stream_valid"] is True
    assert profile["tool_error_observed"] is True
    assert profile["permission_gate_observed"] is True
    assert "SECRET-CONFIG" not in json.dumps(profile)
    assert json.loads(result.stdout)["status"] == "SUCCESS"


def test_timeout_does_not_authorize_success_or_retry(tmp_path):
    binary = _script(tmp_path, _valid_events()[:1], delay_seconds=5)
    profile = {}
    with pytest.raises(subprocess.TimeoutExpired):
        run_agy_stream_profile(
            [binary, "--print", "task", "--output-format", "stream-json"],
            repo=tmp_path, timeout_seconds=0.2, env=os.environ.copy(),
            profile=profile)
    assert profile["timed_out"] is True
    assert profile["stream_valid"] is False


def test_ef01_optin_profile_executes_one_model_stub_and_registered_verify(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    binary = _fake_binary(tmp_path, "single-stream-provider",
        "import json\n"
        "from pathlib import Path\n"
        "Path('changed.txt').write_text('ok\\n', encoding='utf-8')\n"
        "for item in " + repr(_valid_events()) + ":\n"
        "    print(json.dumps(item), flush=True)\n")
    monkeypatch.setenv("AI_COMMERCE_EF01_AGY_STREAM_PROFILE", "1")
    monkeypatch.setattr(runner, "command_for",
        lambda *args, **kwargs: (
            [binary, "--print", "task", "--output-format", "json"], None))
    result = runner.execute_attempt(
        repo=repo, output_dir=tmp_path / "evidence",
        work_id="stream-test", attempt_id="stream-test-a",
        selection=ExecutorSelection("antigravity", "fixture-model", "high"),
        instruction="modify changed.txt", allowed_prefixes=["changed.txt"],
        verify_argv=[sys.executable, "-c",
            "from pathlib import Path; assert Path('changed.txt').read_text() == 'ok\\n'"],
        max_repair_rounds=0)
    inv = result["invocations"][0]
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["executor_invoked"] is True
    assert result["invocation_count"] == 1
    assert inv["agy_stream_profile"]["stream_valid"] is True
    assert (tmp_path / "evidence" / "invocations" / "000" /
            "agy.stream-profile.json").is_file()


def test_ef01_denied_stream_does_not_verify_or_retry(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    events = _valid_events(tool_info={
        "error": {"message": "approval required"},
        "parameters": {"path": "SECRET"}})
    binary = _fake_binary(tmp_path, "denied-stream-provider",
        "import json\n"
        "from pathlib import Path\n"
        "Path('changed.txt').write_text('ok\\n', encoding='utf-8')\n"
        "for item in " + repr(events) + ":\n"
        "    print(json.dumps(item), flush=True)\n")
    monkeypatch.setenv("AI_COMMERCE_EF01_AGY_STREAM_PROFILE", "1")
    monkeypatch.setattr(runner, "command_for",
        lambda *args, **kwargs: (
            [binary, "--print", "task", "--output-format", "json"], None))
    result = runner.execute_attempt(
        repo=repo, output_dir=tmp_path / "evidence",
        work_id="stream-denied", attempt_id="stream-denied-a",
        selection=ExecutorSelection("antigravity", "fixture-model", "high"),
        instruction="modify changed.txt", allowed_prefixes=["changed.txt"],
        verify_argv=[sys.executable, "-c", "raise AssertionError('MUST_NOT_VERIFY')"],
        max_repair_rounds=2)
    assert result["status"] == "EXECUTOR_PERMISSION_DENIED"
    assert result["error_code"] == "HEADLESS_TOOL_PERMISSION_DENIED"
    assert result["verify_exit_code"] is None
    assert result["invocation_count"] == 1
    assert "SECRET" not in json.dumps(result["invocations"][0]["agy_stream_profile"])
