"""D5 local Wake entrypoint tests. Never access GitHub or actual Chrome."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_commerce.remote import local_wake_entry as d5
from ai_commerce.remote.completion_event import build_completion_event
from ai_commerce.remote.wake_adapter import WakeReceipt

REPO = "trung-via/ai-commerce"
SHA = "a" * 40


def files(tmp_path):
    report = {
        "kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
        "batch_id": "d5-batch", "source_sha": SHA,
        "status": "COMPLETE", "total": 1, "succeeded": 1,
        "actions": [{"action_id": "D5", "attempt_id": "d5-1",
                     "status": "CANDIDATE_READY", "candidate_sha": "b" * 40}],
        "github_workflow_run_id": "120", "github_issue_number": 109,
    }
    event = build_completion_event(report, repository=REPO, run_id=120,
                                   run_attempt=1, issue_number=109)
    report.update(wake_event_status="RECORDED_UNBOUND", wake_event_id=event["event_id"])
    event_path = tmp_path / "completion_event.json"
    report_path = tmp_path / "batch_result.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return event_path, report_path, event, report


def test_precheck_reads_local_event_without_network_or_browser(tmp_path, monkeypatch, capsys):
    ep, rp, ev, _ = files(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("precheck attempted a network or browser operation")

    monkeypatch.setattr(d5, "deliver_exact_chat_wake", forbidden)
    monkeypatch.setattr(d5, "LocalChromeDoorbellAdapter", forbidden)
    code = d5.main(["check", "--event-file", str(ep), "--result-file", str(rp),
                    "--checkout", str(tmp_path)])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "PRECHECK_ONLY", "event_id": ev["event_id"],
                      "delivery_verified": False, "browser_accessed": False}


def test_duplicate_json_key_and_wrong_repo_are_blocked(tmp_path, capsys):
    ep, rp, ev, _ = files(tmp_path)
    ep.write_text('{"kind":"one","kind":"two"}', encoding="utf-8")
    assert d5.main(["check", "--event-file", str(ep), "--result-file", str(rp),
                    "--checkout", str(tmp_path)]) == 2
    ep.write_text(json.dumps({**ev, "repository": "evil/repo"}), encoding="utf-8")
    assert d5.main(["check", "--event-file", str(ep), "--result-file", str(rp),
                    "--checkout", str(tmp_path)]) == 2
    assert "EF02_WAKE_BLOCKED" in capsys.readouterr().err


def test_send_is_disabled_by_default_and_requires_windows(tmp_path, monkeypatch, capsys):
    ep, rp, _, _ = files(tmp_path)
    monkeypatch.delenv("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED", raising=False)
    args = ["send", "--event-file", str(ep), "--result-file", str(rp),
            "--checkout", str(tmp_path)]
    assert d5.main(args) == 2
    assert "EF02_WAKE_BLOCKED" in capsys.readouterr().err


def test_send_one_wires_exact_event_provenance_and_two_grant_authorities(tmp_path, monkeypatch):
    ep, rp, event, report = files(tmp_path)
    grants_path = tmp_path / "private" / "grants.json"
    grants_path.parent.mkdir()
    grants_path.write_text(json.dumps({
        "version": 1,
        "grants": [{"operation": "resolve_claim_route"}, {"operation": "deliver_wake"}],
    }), encoding="utf-8")
    calls = {}
    sentinel = object()
    def fake_origin(**kwargs):
        calls["grants"] = kwargs["grants"]
        calls["principal"] = kwargs["principal_provider"]()
        calls["key"] = kwargs["secret_provider"]()
        return sentinel
    def fake_artifact(**kwargs):
        calls["repository"] = kwargs["repository"]
        calls["workflow_id"] = kwargs["workflow_id"]
        calls["token"] = kwargs["token_provider"]()
        return sentinel
    def fake_deliver(**kwargs):
        calls["deliver"] = kwargs
        p = kwargs["provenance"]
        calls["provenance"] = (
            p.repository, p.run_id, p.run_attempt, p.artifact_name,
        )
        return WakeReceipt(event_id=event["event_id"], route_id="route-a",
                           generation=1, delivery_status="UNKNOWN",
                           error_code="UI_USER_TURN_OBSERVED")
    monkeypatch.setattr(d5, "SignedOriginConsentAuthority", fake_origin)
    monkeypatch.setattr(d5, "GitHubRestArtifactAuthority", fake_artifact)
    monkeypatch.setattr(d5, "deliver_exact_chat_wake", fake_deliver)
    receipt = d5.send_one(
        event=event, report=report, workflow_id=399104102, caller_id="operator",
        grants_path=grants_path, registry_path=tmp_path / "private" / "registry.json",
        idempotency_path=tmp_path / "private" / "idem.json",
        repository_root=tmp_path, token_provider=lambda: "read-token",
        secret_provider=lambda: b"test-key" * 5,
        principal_provider=lambda: "S-1-5-21-999",
        adapter=sentinel,
    )
    assert receipt.delivery_status == "UNKNOWN"
    assert calls["provenance"] == (REPO, 120, 1, "ef02-results-120-1")
    assert calls["repository"] == REPO
    assert calls["workflow_id"] == 399104102
    assert calls["token"] == "read-token"
    assert calls["principal"] == "S-1-5-21-999"
    assert calls["deliver"]["adapter"] is sentinel
    assert calls["deliver"]["artifact_authority"] is sentinel
    assert calls["deliver"]["origin_authority"] is sentinel
    assert [g["operation"] for g in calls["grants"]] == [
        "resolve_claim_route", "deliver_wake",
    ]


def test_grants_must_live_outside_checkout(tmp_path):
    ep, rp, event, report = files(tmp_path)
    grant_file = tmp_path / "inside.json"
    grant_file.write_text(json.dumps({"version": 1, "grants": [{}, {}]}), encoding="utf-8")
    with pytest.raises(d5.LocalWakeConfigError):
        d5._load_grants(grant_file, tmp_path)


def test_send_command_requires_creds_and_no_untrusted_process_flags(tmp_path, monkeypatch, capsys):
    ep, rp, _, _ = files(tmp_path)
    # Distinct module-level OS facade avoids mutating the actual os.name used
    # by pytest/pathlib on non-Windows hosts.
    fake_env = {"AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED": "true"}
    monkeypatch.setattr(d5, "os", SimpleNamespace(name="nt", environ=fake_env))
    def forbidden(*args, **kwargs):
        pytest.fail("send without credentials attempted delivery")
    monkeypatch.setattr(d5, "send_one", forbidden)
    args = ["send", "--event-file", str(ep), "--result-file", str(rp),
            "--checkout", str(tmp_path), "--registry", str(tmp_path / "private" / "reg.json"),
            "--idempotency", str(tmp_path / "private" / "idem.json"),
            "--grants", str(tmp_path / "private" / "grants.json"),
            "--workflow-id", "399104102", "--caller-id", "operator"]
    assert d5.main(args) == 2
    assert "EF02_WAKE_BLOCKED" in capsys.readouterr().err
