"""Adversarial offline regressions for the pinned #815 read-only forensic owner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_commerce.hostops_v3 import ef02_815_forensic_readonly as f


def _fixture(tmp_path, monkeypatch):
    root = tmp_path / "checkout" / ".local" / "ef02" / f.BATCH / (f.ACTION + "-" + f.ATTEMPT)
    root.mkdir(parents=True)
    monkeypatch.setattr(f, "EVIDENCE", root)
    monkeypatch.setattr(f, "_process", lambda: "PID_ABSENT")
    monkeypatch.setattr(f, "_worktree", lambda: "WORKTREE_ABSENT")
    return root


def _write(root, rel, content):
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def test_closed_schema_and_no_replay_even_with_all_receipts(tmp_path, monkeypatch):
    root = _fixture(tmp_path, monkeypatch)
    for rel, stage in f.RELS:
        row = {"attempt_id": f.ATTEMPT}
        if stage:
            row["stage"] = stage
        else:
            row["work_id"] = f.BATCH + ":" + f.ACTION
        _write(root, rel, row)
    out = f.inspect()
    assert out["status"] == "HOLD_UNKNOWN_EFFECT"
    assert out["replay_authorized"] is False
    assert out["provider_outcome_proven"] is False
    assert out["write_attempted"] is False
    assert out["evidence_root"] == "ACTION_PRESENT"
    assert out["provider_boundary"] == "PRESENT_UNVERIFIED"
    assert out["provider_return"] == "PRESENT_UNVERIFIED"
    assert out["terminal_receipt"] == "PRESENT_UNVERIFIED"
    assert "C:\\" not in json.dumps(out)
    assert set(out) == {
        "schema", "operation", "target_issue", "source_sha", "attempt_id",
        "status", "process", "evidence_root", "provider_boundary", "provider_return",
        "terminal_receipt", "worktree", "replay_authorized",
        "provider_outcome_proven", "write_attempted",
    }


@pytest.mark.parametrize("bad", [
    {"attempt_id": "forged", "stage": "BEFORE_EXECUTOR_SUBPROCESS"},
    {"attempt_id": f.ATTEMPT, "stage": "EXECUTOR_SUBPROCESS_RETURNED"},
    {"attempt_id": f.ATTEMPT, "stage": "BEFORE_EXECUTOR_SUBPROCESS",
     "replay_authorized": True},
])
def test_wrong_or_forged_boundary_does_not_prove_safe_replay(tmp_path, monkeypatch, bad):
    root = _fixture(tmp_path, monkeypatch)
    rel, stage = f.RELS[0]
    _write(root, rel, bad)
    out = f.inspect()
    if bad.get("replay_authorized"):
        assert out["provider_boundary"] == "UNSAFE"
    else:
        assert out["provider_boundary"] == "UNSAFE"
    assert out["replay_authorized"] is False
    assert out["provider_outcome_proven"] is False


def test_missing_root_is_unknown_not_permission(tmp_path, monkeypatch):
    root = _fixture(tmp_path, monkeypatch)
    root.rmdir()
    out = f.inspect()
    assert out["evidence_root"] == "ACTION_ABSENT"
    assert out["terminal_receipt"] == "ROOT_ABSENT"
    assert out["replay_authorized"] is False


def test_symlinked_receipt_denied(tmp_path, monkeypatch):
    root = _fixture(tmp_path, monkeypatch)
    rel, stage = f.RELS[0]
    p = root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"attempt_id": f.ATTEMPT, "stage": stage}), encoding="utf-8")
    try:
        p.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation denied by this Windows environment")
    assert f.inspect()["provider_boundary"] == "UNSAFE"
    assert f.inspect()["replay_authorized"] is False


def test_non_windows_process_is_unverified_not_no_model(monkeypatch):
    monkeypatch.setattr(f, "HOST_IS_WINDOWS", False)
    assert f._process() == "PROCESS_READ_UNKNOWN"
    assert f._worktree() == "WORKTREE_READ_UNKNOWN"


def test_cli_never_accepts_read_target_argument(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        f.main(["--output", str(tmp_path / "receipt.json"), "--read-root", str(tmp_path)])
    assert not (tmp_path / "receipt.json").exists()


def test_cli_writes_only_exact_runner_temp_and_never_alternative(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    wrong = tmp_path / "other-receipt.json"
    with pytest.raises(SystemExit):
        f.main(["--output", str(wrong)])
    assert not wrong.exists()
    expected = tmp_path / "ef02-815-forensic.json"
    assert f.main(["--output", str(expected)]) == 0
    result = json.loads(expected.read_text(encoding="utf-8"))
    assert result["status"] == "HOLD_UNKNOWN_EFFECT"
    with pytest.raises(SystemExit):
        f.main(["--output", str(expected)])
