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


def test_r02_observes_zero_subprocess_return_without_claiming_provider_success(tmp_path, monkeypatch):
    root = _fixture(tmp_path, monkeypatch)
    rel = "ef01/invocations/000/provider_return_observed.json"
    _write(root, rel, {
        "attempt_id": f.ATTEMPT, "work_id": f.BATCH + ":" + f.ACTION,
        "stage": "EXECUTOR_SUBPROCESS_RETURNED",
        "executor_process_return_observed": True,
        "provider_invocation_proven": False,
        "provider_effects_reconciled": False,
        "replay_authorized": False,
        "executor_exit_code": 0,
    })
    assert f._provider_return_exit() == "RETURN_ZERO_OBSERVED"
    assert f.inspect_r02()["replay_authorized"] is False
    assert f.inspect_r02()["provider_outcome_proven"] is False


@pytest.mark.parametrize("value,expected", [
    (123, "RETURN_NONZERO_OBSERVED"),
    (-1, "RETURN_NONZERO_OBSERVED"),
    (True, "RETURN_UNPROVEN"),
    ("0", "RETURN_UNPROVEN"),
])
def test_r02_validates_return_value_types(tmp_path, monkeypatch, value, expected):
    root = _fixture(tmp_path, monkeypatch)
    _write(root, "ef01/invocations/000/provider_return_observed.json", {
        "attempt_id": f.ATTEMPT, "work_id": f.BATCH + ":" + f.ACTION,
        "stage": "EXECUTOR_SUBPROCESS_RETURNED",
        "executor_process_return_observed": True,
        "provider_invocation_proven": False,
        "provider_effects_reconciled": False,
        "replay_authorized": False, "executor_exit_code": value,
    })
    assert f._provider_return_exit() == expected


def test_r02_source_changed_only_with_fixed_source_baseline(tmp_path, monkeypatch):
    root = _fixture(tmp_path, monkeypatch)
    for path in f.SOURCE_PATHS:
        _write(root, path, {"original": True})
    changed_path = f.SOURCE_PATHS[0]
    root.joinpath(*changed_path.split("/")).write_text('{"changed":true}', encoding="utf-8")
    monkeypatch.setattr(f, "_source_worktree", lambda: root)
    baseline = json.dumps({"original": True}).encode("utf-8")
    def fake_git(r, command, relative=None):
        assert r == root
        if command == "head":
            return (f.SOURCE_SHA + "\n").encode()
        if command == "status":
            return (" M " + changed_path + "\0").encode()
        if command == "blob" and relative in f.SOURCE_PATHS:
            return baseline
        raise AssertionError((command, relative))
    monkeypatch.setattr(f, "_read_git", fake_git)
    assert f._source_scope_state() == ("SOURCE_CHANGED_IN_SCOPE", 1)
    monkeypatch.setattr(f, "_read_git", lambda r, cmd, relative=None: (
        b"?? unrelated.txt\0" if cmd == "status" else fake_git(r, cmd, relative)))
    assert f._source_scope_state() == ("OUTSIDE_SCOPE_OR_UNVERIFIED", None)


def test_r02_rejects_baseline_drift(tmp_path, monkeypatch):
    root = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(f, "_source_worktree", lambda: root)
    monkeypatch.setattr(f, "_read_git", lambda *args: b"not-the-authorized-head")
    assert f._source_scope_state() == ("BASELINE_UNVERIFIED", None)


def test_r02_closed_public_schema_and_no_replay(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(f, "_source_scope_state", lambda: ("SOURCE_CHANGED_IN_SCOPE", 1))
    monkeypatch.setattr(f, "_provider_return_exit", lambda: "RETURN_ZERO_OBSERVED")
    report = f.inspect_r02()
    assert report["source_changed_count"] == 1
    assert report["status"] == "HOLD_UNKNOWN_EFFECT"
    assert report["provider_outcome_proven"] is False
    assert report["replay_authorized"] is False
    assert report["write_attempted"] is False
    assert set(report) == {
        "schema", "operation", "target_issue", "source_sha", "attempt_id",
        "status", "process", "terminal_receipt", "provider_return_exit",
        "source_state", "source_changed_count", "replay_authorized",
        "provider_outcome_proven", "write_attempted",
    }


def test_r02_cli_only_pinned_runner_temp_output(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(f, "_source_scope_state", lambda: ("SOURCE_UNCHANGED", 0))
    monkeypatch.setattr(f, "_provider_return_exit", lambda: "RETURN_UNPROVEN")
    wrong = tmp_path / "my-output.json"
    with pytest.raises(SystemExit):
        f.main(["--r02", "--output", str(wrong)])
    assert not wrong.exists()
    correct = tmp_path / "ef02-815-forensic-r02.json"
    assert f.main(["--r02", "--output", str(correct)]) == 0
    result = json.loads(correct.read_text(encoding="utf-8"))
    assert result["replay_authorized"] is False
    with pytest.raises(SystemExit):
        f.main(["--r02", "--output", str(correct)])
