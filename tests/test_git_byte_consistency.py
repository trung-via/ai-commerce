"""Git Byte Consistency: GitHub raw blob publication must match Git clean-filter semantics."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from ai_commerce.engineering.git_byte_consistency import (
    GitByteConsistencyError, assert_candidate_blob_portable, inspect_fresh_checkout,
)
from ai_commerce.remote.transport import CandidateIntegrityError, GitHubTransport


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], input=data,
                          capture_output=True, check=True)
    return proc.stdout.strip()


def repository(tmp_path: Path, attrs: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "ef02-fixture@example.invalid")
    git(repo, "config", "user.name", "EF02 fixture")
    (repo / ".gitattributes").write_text(attrs, encoding="ascii")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-qm", "initialize policies")
    return repo


def test_raw_json_candidate_rejected_before_remote_write(tmp_path: Path) -> None:
    repo = repository(tmp_path, "*.json text eol=lf\n")
    raw = b'{\r\n  "value": 1\r\n}\r\n'
    (repo / "future-evidence.json").write_bytes(raw)

    class CountingTransport(GitHubTransport):
        def __init__(self) -> None:
            super().__init__("trung-via/ai-commerce", "fixture")
            self.calls = 0
        def api(self, method, path, data=None):
            self.calls += 1
            raise AssertionError("GitHub API must not be called before portability check")

    transport = CountingTransport()
    with pytest.raises(CandidateIntegrityError, match="CANDIDATE_GIT_FILTER_MISMATCH"):
        transport.candidate(source_sha="a" * 40, attempt_id="fixture-001",
                            workspace=repo, paths=["future-evidence.json"],
                            expected_hashes={"future-evidence.json": hashlib.sha256(raw).hexdigest()})
    assert transport.calls == 0
    assert (repo / "future-evidence.json").read_bytes() == raw


def test_raw_json_byte_stable_regardless_directory_name(tmp_path: Path) -> None:
    repo = repository(tmp_path, "*.json -text -eol\n*.md text eol=lf\n")
    for relative in ("contracts/Arbitrary.v1.schema.json",
                     "unforeseen/round_17_evidence/result.json", "other.json"):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{\r\n  "value": true\r\n}\r\n')
        assert_candidate_blob_portable(repo, relative, path.read_bytes())
    # The generic publisher check also covers non-JSON text files.
    with pytest.raises(GitByteConsistencyError, match="CANDIDATE_GIT_FILTER_MISMATCH"):
        assert_candidate_blob_portable(repo, "notes.md", b"first\r\nsecond\r\n")
    with pytest.raises(GitByteConsistencyError, match="CANDIDATE_INVALID_BLOB_PATH"):
        assert_candidate_blob_portable(repo, "../outside.json", b"{}")


def test_checkout_detects_real_change_without_cleaning(tmp_path: Path) -> None:
    repo = repository(tmp_path, "*.json -text -eol\n")
    payload = b'{\r\n  "payload": "immutable"\r\n}\r\n'
    (repo / "record.json").write_bytes(payload)
    git(repo, "add", "record.json")
    git(repo, "commit", "-qm", "add immutable blob")
    sha = git(repo, "rev-parse", "HEAD").decode()
    clean = inspect_fresh_checkout(repo, sha)
    assert clean["clean"] is True
    assert clean["error_code"] is None
    assert clean["porcelain_entry_count"] == 0

    (repo / "record.json").write_bytes(payload + b"tampered")
    dirty = inspect_fresh_checkout(repo, sha)
    assert dirty["clean"] is False
    assert dirty["error_code"] == "GIT_WORKTREE_DIRTY_AFTER_CHECKOUT"
    assert any("record.json" in path for path in dirty["porcelain_entries"])
    assert (repo / "record.json").read_bytes().endswith(b"tampered")
    assert inspect_fresh_checkout(repo, "f" * 40)["error_code"] == "GIT_CHECKOUT_HEAD_MISMATCH"


def test_raw_crlf_blob_with_lf_policy_is_detected_after_checkout(tmp_path: Path) -> None:
    repo = repository(tmp_path, "*.json text eol=lf\n")
    raw = b'{\r\n  "unexpected": true\r\n}\r\n'
    blob = git(repo, "hash-object", "-w", "--stdin", data=raw).decode()
    # Simulate GitHub REST creating an unfiltered blob, not a normal git-add.
    git(repo, "update-index", "--add", "--cacheinfo", "100644", blob, "raw.json")
    git(repo, "commit", "-qm", "publish raw CRLF evidence")
    sha = git(repo, "rev-parse", "HEAD").decode()
    worktree = tmp_path / "isolated"
    git(repo, "worktree", "add", "--detach", str(worktree), sha)
    result = inspect_fresh_checkout(worktree, sha)
    assert result["clean"] is False
    assert result["error_code"] == "GIT_WORKTREE_DIRTY_AFTER_CHECKOUT"
    assert any("raw.json" in p for p in result["porcelain_entries"])
