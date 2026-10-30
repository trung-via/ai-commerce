"""Regression for EF01 -> EF02 candidate identity. No real provider calls."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_commerce.engineering import runner
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.remote.transport import CandidateIntegrityError, GitHubTransport


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "EF fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "ef@example.invalid"], check=True)
    (repo / "old.txt").write_bytes(b"original\n")
    subprocess.run(["git", "-C", str(repo), "add", "old.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    return repo


def test_staged_git_rename_reports_old_and_new_paths(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "mv", "old.txt", "new.txt"], check=True)
    assert runner._changes(repo) == ["new.txt", "old.txt"]
    assert runner._candidate_hashes(repo, ["old.txt"]) == {"old.txt": None}


def test_verify_undoes_change_cannot_be_ef01_pass(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    command = [sys.executable, "-c", "from pathlib import Path; Path('new.txt').write_text('x')"]
    monkeypatch.setattr(runner, "command_for", lambda *_a, **_kw: (command, None))
    result = runner.execute_attempt(
        repo=repo, output_dir=tmp_path / "ef01", work_id="m0-integrity",
        attempt_id="undo-001", selection=ExecutorSelection("codex", "fixture-model", "high"),
        instruction="test-only local mock", allowed_prefixes=["new.txt"],
        verify_argv=[sys.executable, "-c", "from pathlib import Path; Path('new.txt').unlink()"],
    )
    assert result["verify_exit_code"] == 0
    assert result["changed_files"] == []
    assert result["status"] == "NO_CODE_CHANGE"
    assert result["error_code"] == "NO_CHANGED_FILES_AFTER_VERIFY"


class RecordingTransport(GitHubTransport):
    def __init__(self):
        super().__init__("trung-via/ai-commerce", "fixture-token")
        self.calls = []
        self.tree_items = None
        self.commit_data = None

    def api(self, method, path, data=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/git/commits/"):
            return {"tree": {"sha": "a" * 40}}
        if method == "POST" and path == "/git/blobs":
            return {"sha": "b" * 40}
        if method == "POST" and path == "/git/trees":
            self.tree_items = data["tree"]
            return {"sha": "c" * 40}
        if method == "POST" and path == "/git/commits":
            self.commit_data = data
            return {"sha": "d" * 40}
        if method == "POST" and path == "/git/refs":
            return {}
        raise AssertionError("unexpected GitHub API " + path)


def candidate_evidence(repo, attempt, allowed):
    """Synthetic verified receipt for API boundary tests; no live test claim."""
    snapshot, _ = runner._snapshot(repo, allowed, time.monotonic() + 30)
    outside = runner._outside_scope(snapshot["changed_files"], allowed)
    review = bool(outside)
    return {"version": 1, "status": "CANDIDATE_READY_SCOPE_REVIEW" if review else "CANDIDATE_READY",
            "scope_valid": not review, "review_required": review, "out_of_scope_files": outside,
            "changed_files": snapshot["changed_files"], "allowed_paths": allowed,
            "candidate_file_sha256": snapshot["candidate_file_sha256"],
            "tracked_diff_sha256": snapshot["tracked_diff_sha256"],
            "source_sha": snapshot["git_head_after"], "effective_source_sha": snapshot["git_head_after"],
            "action_id": "a", "attempt_id": attempt, "fingerprint": "f" * 64,
            "verification": {"status": "VERIFIED_SCOPE_REVIEW_REQUIRED" if review else "BUILD_VERIFIED_FOR_SCOPE",
                             "exit_code": 0, "profile": "project-tests",
                             "argv": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
                             "evidence_sha256": {"tracked_diff": snapshot["tracked_diff_sha256"]},
                             "report_sha256": "e" * 64},
            "provenance": {"executor": "codex", "model": "fixture", "effort": "high",
                           "dependency_candidates": [], "run_id": None, "run_attempt": None}}


def test_candidate_bytes_changed_after_verify_rejected_without_github_writes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "change.txt"
    target.write_bytes(b"verified")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_bytes(b"modified after verification")
    t = RecordingTransport()
    with pytest.raises(CandidateIntegrityError, match="CANDIDATE_BYTES_MISMATCH"):
        t.candidate(source_sha="a" * 40, attempt_id="integrity-001",
                    workspace=workspace, paths=["change.txt"],
                    expected_hashes={"change.txt": digest})
    assert t.calls == []


def test_candidate_rename_deletes_original_and_adds_destination(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "mv", "old.txt", "new.txt"], check=True)
    paths = runner._changes(repo)
    hashes = runner._candidate_hashes(repo, paths)
    evidence = candidate_evidence(repo, "rename-001", paths)
    t = RecordingTransport()
    result = t.candidate(source_sha=evidence["effective_source_sha"], attempt_id="rename-001",
                         workspace=repo, paths=paths, expected_hashes=hashes, evidence=evidence)
    assert result["candidate_sha"] == "d" * 40
    tree = {item["path"]: item for item in t.tree_items}
    assert set(tree) == {"old.txt", "new.txt"}
    assert tree["old.txt"]["sha"] is None
    assert tree["new.txt"]["sha"] == "b" * 40


def test_candidate_hash_manifest_missing_path_rejected_before_github(tmp_path):
    t = RecordingTransport()
    with pytest.raises(CandidateIntegrityError, match="CANDIDATE_HASH_MANIFEST_MISMATCH"):
        t.candidate(source_sha="a" * 40, attempt_id="missing-001",
                    workspace=tmp_path, paths=["one.txt"],
                    expected_hashes={})
    assert t.calls == []
