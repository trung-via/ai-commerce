"""Git Byte Consistency regressions: EF01/EF02 raw blobs and Windows checkout."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_commerce.engineering.git_byte_consistency import (
    GitByteConsistencyError, assert_candidate_git_filter_consistency,
    checkout_diagnostics,
)

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=data, capture_output=True, check=True,
    )
    return result.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "EF02 Test")
    _git(repo, "config", "user.email", "ef02-test@example.invalid")
    _git(repo, "config", "core.autocrlf", "true")
    (repo / ".gitattributes").write_text(
        "* text=auto\n*.json -text -eol\n*.md text eol=lf\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-m", "initialize rules")
    return repo


def test_all_json_files_are_byte_stable_independent_of_directory() -> None:
    names = [
        "src/ai_commerce/product/a12_02_evidence/final-source.json",
        "src/ai_commerce/product/repair_evidence/round1.failures.json",
        "examples/a11/evidence/project-tests-result.json",
        "contracts/ProductOfferObservation.v1.schema.json",
        "new/nested/directory/results.json",
    ]
    output = _git(ROOT, "check-attr", "-z", "text", "eol", "--", *names)
    parts = output.split(b"\0")
    assert parts[-1] == b""
    triples = list(zip(parts[::3], parts[1::3], parts[2::3]))
    attrs = {(p.decode(), a.decode()): v.decode() for p, a, v in triples}
    for name in names:
        assert attrs[(name, "text")] == "unset"
        assert attrs[(name, "eol")] == "unset"
    md = _git(ROOT, "check-attr", "eol", "--", "README.md")
    assert b"lf" in md


def test_crlf_json_published_as_raw_blob_checks_out_clean(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "nested" / "new_evidence" / "result.json"
    path.parent.mkdir(parents=True)
    raw = b'{\r\n  "status": "PASS"\r\n}\r\n'
    path.write_bytes(raw)
    assert_candidate_git_filter_consistency(repo, [
        ("nested/new_evidence/result.json", raw, "100644"),
    ])
    _git(repo, "add", "nested/new_evidence/result.json")
    _git(repo, "commit", "-m", "raw evidence")
    worktree = tmp_path / "next-action"
    _git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
    assert checkout_diagnostics(worktree, refresh_index=True)["clean"]
    assert (worktree / "nested" / "new_evidence" / "result.json").read_bytes() == raw


def test_git_filter_mismatch_fails_before_remote_publish(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    raw_crlf = b"# Evidence\r\nnot compatible\r\n"
    with pytest.raises(GitByteConsistencyError, match="CANDIDATE_GIT_FILTER_MISMATCH"):
        assert_candidate_git_filter_consistency(repo, [
            ("README.md", raw_crlf, "100644")
        ])
    # Normal LF text remains publishable.
    assert_candidate_git_filter_consistency(repo, [
        ("README.md", b"# Evidence\ncompatible\n", "100644")
    ])


def test_crlf_autotext_candidate_rejected_even_for_renamed_txt(tmp_path: Path) -> None:
    """Auto-text must still reject CRLF raw blobs; JSON exception is not global."""
    repo = _repo(tmp_path)
    original = b"original\r\n"
    with pytest.raises(GitByteConsistencyError, match=r"CANDIDATE_GIT_FILTER_MISMATCH:renamed\.txt"):
        assert_candidate_git_filter_consistency(repo, [
            ("renamed.txt", original, "100644"),
        ])
    assert_candidate_git_filter_consistency(repo, [
        ("renamed.txt", b"original\n", "100644"),
    ])


def test_real_dirty_worktree_stays_blocked_after_metadata_refresh(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "keep.json").write_bytes(b'{"keep":true}\r\n')
    _git(repo, "add", "keep.json")
    _git(repo, "commit", "-m", "track evidence")
    (repo / "keep.json").write_bytes(b'{"keep":false}\r\n')
    observed = checkout_diagnostics(repo, refresh_index=True)
    assert observed["clean"] is False
    assert any("keep.json" in e for e in observed["dirty_entries"])
    assert (repo / "keep.json").read_bytes() == b'{"keep":false}\r\n'


def test_candidate_filters_skip_deletions_and_reject_unsafe_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert_candidate_git_filter_consistency(repo, [("gone.json", None, None)])
    with pytest.raises(GitByteConsistencyError, match="CANDIDATE_PATH_INVALID"):
        assert_candidate_git_filter_consistency(repo, [("../escape.json", b"{}", "100644")])


def test_transport_rejects_filter_mismatch_before_any_remote_api_write(tmp_path: Path) -> None:
    from hashlib import sha256
    from ai_commerce.remote.transport import GitHubTransport

    repo = _repo(tmp_path)
    (repo / "README.md").write_bytes(bytes([35, 32, 67, 82, 76, 70, 13, 10]))
    original = (repo / "README.md").read_bytes()

    class NoRemoteWrites(GitHubTransport):
        def __init__(self) -> None:
            super().__init__("trung-via/ai-commerce", "fake-token")
            self.calls = 0

        def api(self, method: str, path: str, data: dict | None = None) -> dict:
            self.calls += 1
            raise AssertionError("Remote Git API must not be called")

    remote = NoRemoteWrites()
    with pytest.raises(GitByteConsistencyError, match="CANDIDATE_GIT_FILTER_MISMATCH"):
        remote.candidate(
            source_sha="a" * 40,
            attempt_id="byte-v1-0001",
            workspace=repo,
            paths=["README.md"],
            expected_hashes={"README.md": sha256(original).hexdigest()},
        )
    assert remote.calls == 0
    assert (repo / "README.md").read_bytes() == original


def test_new_crlf_txt_is_stabilized_before_verification(tmp_path: Path) -> None:
    from hashlib import sha256
    from ai_commerce.engineering.git_byte_consistency import (
        stabilize_candidate_newlines_before_verify,
    )

    repo = _repo(tmp_path)
    relative = "affiliate/verification/consumer-tests.txt"
    path = repo / relative
    path.parent.mkdir(parents=True)
    original = b"consumer tests\r\nstatus=PASS\r\n"
    path.write_bytes(original)
    changed = stabilize_candidate_newlines_before_verify(
        repo, [relative], allowed_paths=["affiliate"],
    )
    assert changed == [{
        "path": relative, "transformation": "CRLF_TO_LF_PRE_VERIFY",
        "before_sha256": sha256(original).hexdigest(),
        "after_sha256": sha256(original.replace(b"\r\n", b"\n")).hexdigest(),
    }]
    assert path.read_bytes() == b"consumer tests\nstatus=PASS\n"
    assert_candidate_git_filter_consistency(repo, [
        (relative, path.read_bytes(), "100644")
    ])
    _git(repo, "add", relative)
    _git(repo, "commit", "-m", "candidate published")
    next_worktree = tmp_path / "next"
    _git(repo, "worktree", "add", "--detach", str(next_worktree), "HEAD")
    assert checkout_diagnostics(next_worktree, refresh_index=True)["clean"]


def test_tracked_crlf_cannot_be_silently_normalized(tmp_path: Path) -> None:
    from ai_commerce.engineering.git_byte_consistency import (
        stabilize_candidate_newlines_before_verify,
    )

    repo = _repo(tmp_path)
    path = repo / "consumer-tests.txt"
    path.write_bytes(b"original\n")
    _git(repo, "add", "consumer-tests.txt")
    _git(repo, "commit", "-m", "tracked")
    path.write_bytes(b"changed\r\n")
    with pytest.raises(GitByteConsistencyError, match="PREVERIFY_TRACKED_FILTER_MISMATCH"):
        stabilize_candidate_newlines_before_verify(
            repo, ["consumer-tests.txt"], allowed_paths=["consumer-tests.txt"])
    assert path.read_bytes() == b"changed\r\n"


def test_out_of_scope_filter_is_not_auto_rewritten(tmp_path: Path) -> None:
    from ai_commerce.engineering.git_byte_consistency import (
        stabilize_candidate_newlines_before_verify,
    )

    repo = _repo(tmp_path)
    path = repo / "unowned.txt"
    raw = b"not owned\r\n"
    path.write_bytes(raw)
    with pytest.raises(GitByteConsistencyError, match="PREVERIFY_OUT_OF_SCOPE_FILTER"):
        stabilize_candidate_newlines_before_verify(
            repo, ["unowned.txt"], allowed_paths=["owned"])
    assert path.read_bytes() == raw


def test_existing_json_evidence_crlf_stays_raw(tmp_path: Path) -> None:
    from ai_commerce.engineering.git_byte_consistency import (
        stabilize_candidate_newlines_before_verify,
    )

    repo = _repo(tmp_path)
    path = repo / "evidence.json"
    raw = b'{"stage":"verified"}\r\n'
    path.write_bytes(raw)
    assert stabilize_candidate_newlines_before_verify(
        repo, ["evidence.json"], allowed_paths=["evidence.json"]) == []
    assert path.read_bytes() == raw


def test_new_staged_crlf_txt_stabilized_without_rejecting_index(tmp_path: Path) -> None:
    """Regression: #204 Windows Git Write Guard stages a newly created .txt."""
    from ai_commerce.engineering.git_byte_consistency import (
        stabilize_candidate_newlines_before_verify,
    )

    repo = _repo(tmp_path)
    relative = "consumer-tests.txt"
    path = repo / relative
    original = b"consumer\r\nstatus=PASS\r\n"
    path.write_bytes(original)
    _git(repo, "add", relative)
    staged = _git(repo, "rev-parse", ":" + relative).strip()
    assert staged == _git(repo, "hash-object", "--stdin", data=original.replace(b"\r\n", b"\n")).strip()
    edits = stabilize_candidate_newlines_before_verify(
        repo, [relative], allowed_paths=[relative]
    )
    assert len(edits) == 1 and edits[0]["path"] == relative
    assert path.read_bytes() == b"consumer\nstatus=PASS\n"
    assert _git(repo, "rev-parse", ":" + relative).strip() == staged
    assert_candidate_git_filter_consistency(repo, [(relative, path.read_bytes(), "100644")])
    _git(repo, "commit", "-m", "source from staged candidate")
    checkout = tmp_path / "next"
    _git(repo, "worktree", "add", "--detach", str(checkout), "HEAD")
    assert checkout_diagnostics(checkout, refresh_index=True)["clean"]


def test_new_staged_index_divergence_is_not_hidden(tmp_path: Path) -> None:
    from ai_commerce.engineering.git_byte_consistency import (
        stabilize_candidate_newlines_before_verify,
    )

    repo = _repo(tmp_path)
    relative = "changed.txt"
    path = repo / relative
    path.write_bytes(b"old staged\r\n")
    _git(repo, "add", relative)
    raw = b"latest worktree\r\n"
    path.write_bytes(raw)
    with pytest.raises(GitByteConsistencyError, match="PREVERIFY_STAGED_BYTES_DIVERGED"):
        stabilize_candidate_newlines_before_verify(repo, [relative], allowed_paths=[relative])
    assert path.read_bytes() == raw


def test_preverify_empty_allowed_paths_follow_ef01_unrestricted_default(tmp_path: Path) -> None:
    from ai_commerce.engineering.git_byte_consistency import (
        stabilize_candidate_newlines_before_verify,
    )

    repo = _repo(tmp_path)
    relative = "generated.txt"
    path = repo / relative
    path.write_bytes(b"generated\r\n")
    changed = stabilize_candidate_newlines_before_verify(
        repo, [relative], allowed_paths=(),
    )
    assert len(changed) == 1
    assert path.read_bytes() == b"generated\n"
