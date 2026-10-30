"""D5.2 exact GitHub run trigger — mocked transport, never real Chrome."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from ai_commerce.remote import local_wake_trigger as trigger
from ai_commerce.remote.completion_event import build_completion_event
from ai_commerce.remote.wake_adapter import WakeReceipt

SHA = "a" * 40
TARGET = trigger.PinnedRun(
    run_id=120, run_attempt=1, workflow_id=399104102,
    issue_number=109, batch_id="d5-batch", source_sha=SHA,
)


def evidence(target=TARGET):
    report = {
        "kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
        "batch_id": target.batch_id, "source_sha": target.source_sha,
        "status": "PARTIAL_OR_FAILED", "total": 1, "succeeded": 0,
        "actions": [{"action_id": "D5", "attempt_id": "d5-1",
                     "status": "VERIFICATION_FAILED"}],
        "github_workflow_run_id": str(target.run_id), "github_issue_number": target.issue_number,
    }
    event = build_completion_event(report, repository="trung-via/ai-commerce",
                                   run_id=target.run_id, run_attempt=target.run_attempt,
                                   issue_number=target.issue_number)
    report.update(wake_event_status="RECORDED_UNBOUND", wake_event_id=event["event_id"])
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(target.batch_id + "/completion_event.json", json.dumps(event))
        archive.writestr(target.batch_id + "/batch_result.json", json.dumps(report))
    return event, report, bio.getvalue()


class FakeSource:
    def __init__(self, *, status="completed", tamper=None, duplicate=False):
        self.event, self.report, self.archive = evidence()
        self.status = status
        self.tamper = tamper
        self.expected_archive_digest = hashlib.sha256(self.archive).hexdigest()
        self.duplicate = duplicate
        self.calls = []

    def json(self, url, token):
        assert token == "read-only-token"
        self.calls.append(url)
        if "/artifacts?" not in url:
            return {
                "id": 120, "run_attempt": 1, "workflow_id": 399104102,
                "status": self.status, "event": "issues",
                "repository": {"full_name": "trung-via/ai-commerce"},
            } | (self.tamper or {})
        item = {
            "name": "ef02-results-120-1", "id": 4242, "expired": False,
            "digest": "sha256:" + self.expected_archive_digest,
            "workflow_run": {"id": 120},
        }
        return {"total_count": 1, "artifacts": [item, item] if self.duplicate else [item]}

    def zip(self, url, token):
        assert token == "read-only-token"
        self.calls.append(url)
        return self.archive

    def reader(self):
        return trigger.GitHubExactRunReader(
            token_provider=lambda: "read-only-token",
            read_json=self.json, read_archive=self.zip,
        )


def test_exact_completed_run_produces_candidate_not_authority():
    x = FakeSource()
    event, report = x.reader().candidate(TARGET)
    assert event == x.event
    assert report == x.report
    assert any("/actions/runs/120" in call for call in x.calls)
    assert any("/actions/artifacts/4242/zip" in call for call in x.calls)


def test_queued_run_does_not_download_zip_or_post():
    x = FakeSource(status="in_progress")
    assert x.reader().candidate(TARGET) is None
    assert len(x.calls) == 1


@pytest.mark.parametrize("changed", [
    {"id": 121}, {"run_attempt": 2}, {"workflow_id": 999},
    {"event": "workflow_dispatch"}, {"repository": {"full_name": "other/repo"}},
    {"status": "unknown"},
])
def test_wrong_or_unexpected_run_fails_closed(changed):
    with pytest.raises(trigger.TriggerBlocked):
        FakeSource(tamper=changed).reader().candidate(TARGET)


def test_missing_artifact_is_read_only_not_ready():
    s = FakeSource()
    original_json = s.json
    s.json = lambda url, token: (
        {"total_count": 0, "artifacts": []}
        if "/artifacts?" in url else original_json(url, token)
    )
    assert s.reader().candidate(TARGET) is None


def test_duplicate_artifact_fails_closed():
    with pytest.raises(trigger.TriggerBlocked, match="ARTIFACT_INVALID"):
        FakeSource(duplicate=True).reader().candidate(TARGET)


def test_wrong_digest_and_affinity_rejected():
    s = FakeSource()
    s.archive += b"tampered"
    with pytest.raises(trigger.TriggerBlocked, match="ARTIFACT_DIGEST_INVALID"):
        s.reader().candidate(TARGET)
    s = FakeSource()
    with pytest.raises(trigger.TriggerBlocked, match="EVIDENCE_AFFINITY_MISMATCH"):
        s.reader().candidate(trigger.PinnedRun(
            120, 1, 399104102, 109, "d5-batch", "b" * 40,
        ))


@pytest.mark.parametrize("target", [
    (0, 1, 399104102, 109, "d5-batch", SHA),
    (120, 0, 399104102, 109, "d5-batch", SHA),
    (120, 1, 0, 109, "d5-batch", SHA),
    (120, 1, 399104102, 0, "d5-batch", SHA),
    (120, 1, 399104102, 109, "../newest", SHA),
    (120, 1, 399104102, 109, "d5-batch", "invalid"),
])
def test_pinned_target_requires_strict_identity(target):
    with pytest.raises(trigger.TriggerBlocked, match="TARGET_INVALID"):
        trigger.PinnedRun(*target)


def test_bounded_polls_stop_on_first_evidence_and_sleep_between():
    calls = []
    ev, rep, _ = evidence()
    class Reader:
        def candidate(self, target):
            calls.append("read")
            return None if calls.count("read") < 3 else (ev, rep)
    result = trigger.wait_for_exact_evidence(
        Reader(), TARGET, max_polls=5, interval_seconds=10,
        sleep=lambda seconds: calls.append(("sleep", seconds)),
    )
    assert result == (ev, rep)
    assert calls == ["read", ("sleep", 10), "read", ("sleep", 10), "read"]


def test_no_evidence_and_invalid_budget_never_trigger_send():
    class Reader:
        def candidate(self, target):
            return None
    with pytest.raises(trigger.TriggerBlocked, match="EVIDENCE_NOT_READY"):
        trigger.wait_for_exact_evidence(
            Reader(), TARGET, max_polls=2, interval_seconds=1,
            sleep=lambda _: None,
        )
    with pytest.raises(trigger.TriggerBlocked, match="POLL_BUDGET_INVALID"):
        trigger.wait_for_exact_evidence(Reader(), TARGET, max_polls=0)


def cli_args(command="observe"):
    return [command, "--run-id", "120", "--run-attempt", "1",
            "--workflow-id", "399104102", "--issue-number", "109",
            "--batch-id", "d5-batch", "--source-sha", SHA,
            "--max-polls", "1"]


def test_observe_only_never_opens_browser_or_calls_delivery(monkeypatch, capsys):
    ev, rep, _ = evidence()
    monkeypatch.setenv("AI_COMMERCE_GITHUB_READ_TOKEN", "read-only-token")
    monkeypatch.setattr(
        trigger, "GitHubExactRunReader",
        lambda **kwargs: SimpleNamespace(candidate=lambda target: (ev, rep)),
    )
    monkeypatch.setattr(trigger, "send_one", lambda **kwargs: pytest.fail("SEND CALLED"))
    assert trigger.main(cli_args()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "CANDIDATE_READY_NOT_SENT"
    assert output["chat_delivery_proven"] is False
    assert output["browser_accessed"] is False


def test_delivery_command_default_gate_closed(monkeypatch, capsys):
    monkeypatch.delenv("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED", raising=False)
    assert trigger.main(cli_args("deliver")) == 2
    assert "EF02_LOCAL_TRIGGER_BLOCKED" in capsys.readouterr().err


def test_explicit_delivery_still_returns_unproven_status(monkeypatch, tmp_path, capsys):
    ev, rep, _ = evidence()
    private = tmp_path / "private"
    private.mkdir()
    env = {
        "AI_COMMERCE_GITHUB_READ_TOKEN": "read-only-token",
        "AI_COMMERCE_WAKE_HMAC_KEY_HEX": "11" * 32,
        "AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED": "true",
    }
    monkeypatch.setattr(trigger, "os", SimpleNamespace(name="nt", environ=env))
    monkeypatch.setattr(trigger, "_windows_process_sid", lambda: "S-1-5-21-1")
    monkeypatch.setattr(trigger, "GitHubExactRunReader",
                        lambda **kwargs: SimpleNamespace(candidate=lambda t: (ev, rep)))
    calls = []
    def fake_send(**kwargs):
        calls.append(kwargs)
        return WakeReceipt(event_id=ev["event_id"], route_id="original-chat",
                           generation=1, delivery_status="UNKNOWN",
                           error_code="UI_USER_TURN_OBSERVED")
    monkeypatch.setattr(trigger, "send_one", fake_send)
    result = trigger.main(cli_args("deliver") + [
        "--checkout", str(tmp_path), "--registry", str(private / "routes.json"),
        "--idempotency", str(private / "delivery.json"),
        "--grants", str(private / "grants.json"),
        "--caller-id", "trusted-operator",
    ])
    assert result == 3
    assert len(calls) == 1
    assert calls[0]["principal_provider"]() == "S-1-5-21-1"
    assert calls[0]["event"]["event_id"] == ev["event_id"]
    assert calls[0]["report"] == rep
    output = json.loads(capsys.readouterr().out)
    assert output["chat_delivery_proven"] is False
    assert output["brain_continuation_proven"] is False
