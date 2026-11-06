"""Regression: a tool-return string cannot impersonate a provider failure."""
import json
import pytest
from ai_commerce.engineering.runner import _provider_problem
from ai_commerce.remote.worker import _classify_failed_executor


def _stream(*events):
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


@pytest.mark.parametrize("phrase", [
    "Selected model is at capacity", "no capacity available",
    "resource_exhausted", "quota exceeded", "model_not_found", "permission denied",
])
def test_untrusted_tool_payload_is_not_provider_error(phrase, tmp_path):
    tool = {"type": "item.completed", "item": {"type": "mcp_tool_call",
            "tool": "github.fetch", "result": {"content": [{"text": phrase}]}}}
    data = _stream({"type": "turn.started"}, tool, {"type": "turn.completed"})
    assert _provider_problem(data) is None
    (tmp_path / "stdout.log").write_bytes(data)
    assert _classify_failed_executor({"status": "EXECUTOR_FAILED",
        "evidence_files": {"executor_stdout": "stdout.log"}}, tmp_path) == "EXECUTOR_FAILED"


@pytest.mark.parametrize("message,status", [
    ("Selected model is at capacity", "MODEL_AT_CAPACITY"),
    ("quota exceeded", "QUOTA_EXHAUSTED"),
    ("model_not_found", "MODEL_UNSUPPORTED"),
    ("model unavailable", "EXECUTOR_UNAVAILABLE"),
    ("permission denied", "EXECUTOR_PERMISSION_DENIED"),
])
def test_explicit_provider_failure_still_classified(message, status):
    assert _provider_problem(_stream({"type": "turn.failed",
         "error": {"message": message}})) == (status, status)


def test_plain_text_error_and_unknown_provider_error():
    assert _provider_problem(b"quota exceeded") == ("QUOTA_EXHAUSTED", "QUOTA_EXHAUSTED")
    assert _provider_problem(_stream({"type": "turn.failed",
         "error": {"message": "unrecognized error"}})) == (
             "EXECUTION_UNKNOWN", "EXECUTOR_OUTCOME_UNCONFIRMED")


# Reconnect recovery contract: exact Codex top-level diagnostic with bounded N/M
from ai_commerce.engineering.runner import (
    _parse_reconnect_warning,
    _is_real_completed_command,
    _codex_reconnect_recovery,
    execute_attempt,
)
from ai_commerce.engineering.profile import ExecutorSelection
import os
import subprocess
import sys


def test_codex_reconnect_recovery_success_single():
    events = [
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting... (1/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "cmd_1", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
    ]
    data = _stream(*events)
    assert _parse_reconnect_warning(events[1]) == (1, 5)
    assert _is_real_completed_command(events[2]) == "cmd_1"
    assert _codex_reconnect_recovery(data) is True
    assert _provider_problem(data) is None


def test_codex_reconnect_recovery_success_multiple():
    events = [
        {"type": "turn.started"},
        {"type": "error", "error": {"message": "Connection dropped. Reconnecting (1 of 3)"}},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "cmd_1", "status": "completed", "exit_code": 0}},
        {"type": "warning", "detail": "Reconnecting (2/3)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "cmd_2", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
    ]
    data = _stream(*events)
    assert _codex_reconnect_recovery(data) is True
    assert _provider_problem(data) is None


@pytest.mark.parametrize("event,expected", [
    (None, None),
    ("not-a-dict", None),
    (123, None),
    ({"type": "item.completed", "item": {"type": "mcp_tool_call", "result": {"content": [{"text": "Reconnecting (1/5)"}]}}}, None),
    ({"type": "turn.started"}, None),
    ({"type": "turn.completed"}, None),
    ({"type": "turn.failed", "error": {"message": "Reconnecting (1/5)"}}, None),
    ({"type": "warning", "message": "Generic failure without reconnect"}, None),
    ({"type": "warning", "message": "Reconnecting (0/5)"}, None),
    ({"type": "warning", "message": "Reconnecting (6/5)"}, None),
    ({"type": "warning", "message": "Reconnecting (5/0)"}, None),
    ({"type": "warning", "message": "Reconnecting (101/100)"}, None),
    ({"type": "warning", "message": "Reconnecting (-1/5)"}, None),
    ({"type": "warning", "message": "Reconnecting (a/b)"}, None),
    ({"type": "warning", "message": "Reconnecting (1/5) with quota exceeded"}, None),
    ({"type": "warning", "message": "Reconnecting (1/5) permission denied"}, None),
])
def test_parse_reconnect_warning_negative(event, expected):
    assert _parse_reconnect_warning(event) == expected


@pytest.mark.parametrize("event,expected", [
    (None, None),
    ("not-a-dict", None),
    ({}, None),
    ({"type": "item.started", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}}, None),
    ({"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "c1", "status": "completed", "exit_code": 0}}, None),
    ({"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "failed", "exit_code": 0}}, None),
    ({"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 1}}, None),
    ({"type": "item.completed", "item": {"type": "command_execution", "id": "", "status": "completed", "exit_code": 0}}, None),
    ({"type": "item.completed", "item": {"type": "command_execution", "status": "completed", "exit_code": 0}}, None),
    ({"type": "item.completed", "item": "not-a-dict"}, None),
])
def test_is_real_completed_command_negative(event, expected):
    assert _is_real_completed_command(event) == expected


def test_codex_reconnect_recovery_negative_nonzero_exit():
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data, exit_code=1) is False


def test_codex_reconnect_recovery_negative_nonempty_stderr():
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data, stderr=b"fatal: unexpected error\n") is False


def test_codex_reconnect_recovery_negative_invalid_utf8():
    data = b"\xff\xfe\x00\x00invalid-utf8"
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_incomplete_stream():
    data = b'{"type": "turn.started"}\n{"type": "warning", "message": "Reconnecting (1/5)"}\n{"type":'
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_non_object_json():
    data = b'{"type": "turn.started"}\n12345\n{"type": "turn.completed"}\n'
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_no_warnings():
    data = _stream(
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_no_subsequent_command():
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data) is False
    assert _provider_problem(data) == ("EXECUTION_UNKNOWN", "EXECUTOR_OUTCOME_UNCONFIRMED")


def test_codex_reconnect_recovery_negative_command_before_warning_only():
    data = _stream(
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_shared_command_id():
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "warning", "message": "Reconnecting (2/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_multiple_turn_completed():
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_error_after_done():
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
        {"type": "error", "message": "error after completed"},
    )
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_extra_object_after_done():
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "turn.completed"},
        {"type": "heartbeat"},
    )
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_turn_failed_present():
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "turn.failed", "error": {"message": "failed anyway"}},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data) is False


@pytest.mark.parametrize("fatal_phrase", [
    "quota exceeded",
    "Selected model is at capacity",
    "permission denied",
    "model unavailable",
    "model_not_found",
])
def test_codex_reconnect_recovery_negative_fatal_errors(fatal_phrase):
    data = _stream(
        {"type": "turn.started"},
        {"type": "warning", "message": "Reconnecting (1/5)"},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}},
        {"type": "error", "message": fatal_phrase},
        {"type": "turn.completed"},
    )
    assert _codex_reconnect_recovery(data) is False


def test_codex_reconnect_recovery_negative_fake_tool_call_warning():
    tool = {"type": "item.completed", "item": {"type": "mcp_tool_call",
            "tool": "github.fetch", "result": {"content": [{"text": "Reconnecting (1/5)"}]}}}
    cmd = {"type": "item.completed", "item": {"type": "command_execution", "id": "c1", "status": "completed", "exit_code": 0}}
    data = _stream({"type": "turn.started"}, tool, cmd, {"type": "turn.completed"})
    assert _codex_reconnect_recovery(data) is False


def test_codex_stub_subprocess_reconnect_recovered_produces_verified_candidate(tmp_path, monkeypatch):
    """Real local stub subprocess emitting recovered reconnect JSONL leads to verified candidate."""
    for key in list(os.environ):
        if key.startswith("GIT_CONFIG_") or key == "AI_COMMERCE_GIT_GUARD_PATHS":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.local/\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n", encoding="utf-8")
    git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_") and k != "AI_COMMERCE_GIT_GUARD_PATHS"}
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Tester"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "tester@example.com"], check=True, env=git_env)
    (repo / "impl.py").write_text("def run(): return 'bad'\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_impl.py").write_text(
        "import impl\ndef test_run(): assert impl.run() == 'good'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True, env=git_env)

    stub = tmp_path / "codex_stub.py"
    stub.write_text(
        "import json, sys, pathlib\n"
        "pathlib.Path('impl.py').write_text(\"def run(): return 'good'\\n\", encoding='utf-8')\n"
        "events = [\n"
        "    {'type': 'turn.started'},\n"
        "    {'type': 'warning', 'message': 'Reconnecting... (1/5)'},\n"
        "    {'type': 'item.completed', 'item': {'type': 'command_execution', 'id': 'cmd_42', 'status': 'completed', 'exit_code': 0}},\n"
        "    {'type': 'turn.completed'}\n"
        "]\n"
        "for ev in events:\n"
        "    print(json.dumps(ev))\n",
        encoding="utf-8"
    )

    stub_cmd = tmp_path / "codex.cmd"
    stub_cmd.write_text(f'@echo off\n"{sys.executable}" "{stub}" %*\n', encoding="utf-8")

    out_dir = tmp_path / "evidence"
    verify_cmd = [sys.executable, "-m", "pytest", "tests/test_impl.py", "-q", "-p", "no:cacheprovider"]
    result = execute_attempt(
        repo=repo, output_dir=out_dir, work_id="w-rec", attempt_id="att-rec",
        selection=ExecutorSelection("codex", "gemini-3.8-flash", "high"),
        instruction="fix impl.py",
        verify_argv=verify_cmd,
        allowed_prefixes=["impl.py"],
        codex_bin=str(stub_cmd),
    )
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["changed_files"] == ["impl.py"]
    assert result["verify_exit_code"] == 0


def test_codex_stub_subprocess_unrecovered_error_fails_closed(tmp_path, monkeypatch):
    """Real local stub subprocess emitting unrecovered error fails closed to EXECUTION_UNKNOWN."""
    for key in list(os.environ):
        if key.startswith("GIT_CONFIG_") or key == "AI_COMMERCE_GIT_GUARD_PATHS":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.local/\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n", encoding="utf-8")
    git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_") and k != "AI_COMMERCE_GIT_GUARD_PATHS"}
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Tester"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "tester@example.com"], check=True, env=git_env)
    (repo / "impl.py").write_text("def run(): return 'bad'\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_impl.py").write_text(
        "import impl\ndef test_run(): assert impl.run() == 'good'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True, env=git_env)

    stub = tmp_path / "codex_fail_stub.py"
    stub.write_text(
        "import json, sys, pathlib\n"
        "pathlib.Path('impl.py').write_text(\"def run(): return 'good'\\n\", encoding='utf-8')\n"
        "events = [\n"
        "    {'type': 'turn.started'},\n"
        "    {'type': 'warning', 'message': 'Reconnecting... (1/5)'},\n"
        "    {'type': 'turn.failed', 'error': {'message': 'connection timed out'}}\n"
        "]\n"
        "for ev in events:\n"
        "    print(json.dumps(ev))\n",
        encoding="utf-8"
    )

    stub_cmd = tmp_path / "codex_fail.cmd"
    stub_cmd.write_text(f'@echo off\n"{sys.executable}" "{stub}" %*\n', encoding="utf-8")

    out_dir = tmp_path / "evidence_fail"
    verify_cmd = [sys.executable, "-m", "pytest", "tests/test_impl.py", "-q", "-p", "no:cacheprovider"]
    result = execute_attempt(
        repo=repo, output_dir=out_dir, work_id="w-fail", attempt_id="att-fail",
        selection=ExecutorSelection("codex", "gemini-3.8-flash", "high"),
        instruction="fix impl.py",
        verify_argv=verify_cmd,
        allowed_prefixes=["impl.py"],
        codex_bin=str(stub_cmd),
    )
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EXECUTOR_OUTCOME_UNCONFIRMED"
