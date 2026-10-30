"""Scope review composed with main's AGY preflight and Chrome doorbell gates."""
import json

import pytest

from ai_commerce.engineering import runner
from ai_commerce.remote.candidate_policy import REVIEW_STATUS, candidate_accepted
from ai_commerce.remote.cdp_doorbell import LocalChromeDoorbellAdapter
from ai_commerce.remote.completion_event import build_completion_event
from test_ef01_repair import _action, _worker_fixture
from test_ef02_authoring_preflight import check
from test_ef02 import action, batch
from test_ef02_cdp_doorbell import BIND, FakeInventory, FakePage


def test_agy_scope_review_retains_registered_diagnostics_and_mirrored_repair(tmp_path, monkeypatch):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, drift="src/ordinary.py")
    request = _action("a")
    request["executor"] = "antigravity"
    result = context.worker.execute_action(request)
    assert result["status"] == REVIEW_STATUS and not candidate_accepted(result)
    invocation = result["ef01_invocations"][-1]
    assert invocation["failure_provenance"]["phase"] == "EF01_REGISTERED_VERIFY"
    assert invocation["failure_provenance"]["replay_authorized"] is False
    assert result["review_required"] is True and result["out_of_scope_files"] == ["src/ordinary.py"]
    assert result["ef01_invocations"][-1]["provider_return_determinate"] is True
    prompt = context.calls[1][3]
    assert "External archived artifacts are OPTIONAL" in prompt
    assert "Read the disposable agy_input/repair_feedback.json copy" in prompt
    assert len(context.candidates) == 1


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_agy_denial_with_ordinary_drift_still_stops_before_verify(tmp_path, monkeypatch, channel):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, drift="src/ordinary.py")
    command = runner.command_for

    def denied(*args, **kwargs):
        argv, stdin = command(*args, **kwargs)
        event = json.dumps({"status": "SUCCESS", "denied_actions": [
            {"action": "read_file", "path": str(args[1] / "src" / "private-marker.py")} ]})
        argv[-1] += ";import sys;print(" + repr(event) + (",file=sys.stderr);" if channel == "stderr" else ");")
        return argv, stdin

    monkeypatch.setattr(runner, "command_for", denied)
    request = _action("a")
    request["executor"] = "antigravity"
    result = context.worker.execute_action(request)
    assert result["status"] == "EXECUTOR_PERMISSION_DENIED" and result["verify_exit_code"] is None
    assert not context.candidates and len(context.calls) == 1
    invocation = result["ef01_invocations"][0]
    assert invocation["out_of_scope_files"] == ["src/ordinary.py"]
    assert invocation["failure_provenance"]["phase"] == "HEADLESS_TOOL_DENIED"
    assert invocation["denied_action_diagnostics"] == [
        {"tool": "read_file", "target_status": "DENIED_TARGET_KNOWN", "target_scope": "WORKTREE"}]
    assert "private-marker" not in json.dumps(invocation["failure_provenance"])


@pytest.mark.parametrize("path", [".codex/settings.json", ".aws/config", "src/.gitattributes", "keys/private.pem"])
def test_authoring_preflight_uses_shared_protected_path_policy(path):
    packet = batch([action("a")])
    packet["actions"][0]["allowed_paths"] = [path]
    result = check(packet)
    assert result["status"] == "PREFLIGHT_REJECTED"
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    assert result["failure_provenance"]["phase"] == "REQUEST_VALIDATION"


def test_preflight_rejects_scope_review_hidden_in_reuse_registry():
    packet = batch([action("a")])
    request = packet["actions"][0]
    request["reuse_candidate"] = {"candidate_sha": "b" * 40, "verified_run_id": 123}
    entry = {**request, "source_sha": packet["source_sha"], "candidate_sha": "b" * 40,
             "verified_run_id": 123, "parent_sha": "a" * 40, "tree_sha": "c" * 40,
             "verification_status": "BUILD_VERIFIED_FOR_SCOPE", "verify_exit_code": 0,
             "scope_valid": True, "candidate_evidence": {"status": REVIEW_STATUS}}
    registry = {"version": 1, "approved_source_sha": packet["source_sha"],
                "candidates": {request["attempt_id"]: entry}}
    result = check(packet, approved_reuse=registry)
    assert result["status"] == "PREFLIGHT_REJECTED" and result["error_code"] == "REUSE_UNAPPROVED"
    assert result["dispatch_performed"] is False


def test_scope_review_completion_remains_unaccepted_after_exact_chat_ui_observation(tmp_path, monkeypatch):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, drift="src/ordinary.py")
    result = context.worker.execute_action(_action("a"))
    report = {"kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1, "batch_id": "scope",
              "source_sha": result["source_sha"], "status": "PARTIAL_OR_FAILED",
              "actions": [result], "total": 1, "succeeded": 0}
    event = build_completion_event(report, repository="trung-via/ai-commerce", run_id=42,
                                   run_attempt=1, issue_number=156)
    before = json.dumps(report, sort_keys=True)
    page = FakePage()
    receipt = LocalChromeDoorbellAdapter(allow_submit=True, inventory=FakeInventory(page)).send_wake(
        binding=BIND, message="candidate reviewed and approved", event_id=event["event_id"])
    assert receipt.error_code == "UI_USER_TURN_OBSERVED" and receipt.delivery_status == "UNKNOWN"
    assert receipt.chat_delivery_proven is receipt.brain_continuation_proven is False
    assert "event_id: " + event["event_id"] in page.submitted_text
    assert "candidate reviewed and approved" not in page.submitted_text
    assert json.dumps(report, sort_keys=True) == before and not candidate_accepted(result)
    assert event["actions"][0]["status"] == REVIEW_STATUS
    assert event["actions"][0]["review_required"] is True
