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
