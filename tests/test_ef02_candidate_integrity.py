"""Verify-first publication retains immutable scope review; no live providers/API."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.engineering import runner
from ai_commerce.engineering import minimum_test as mt
from ai_commerce.engineering.git_byte_consistency import (
    GitByteConsistencyError, assert_candidate_git_filter_consistency,
)
from ai_commerce.remote import transport
from ai_commerce.remote.candidate_policy import (
    EVIDENCE_MARKER, REVIEW_STATUS, canonical, candidate_accepted, commit_evidence,
    evidence_digest, validate_candidate_evidence,
)
from ai_commerce.remote.completion_event import (
    CompletionEventError, build_completion_event, validate_completion_event,
)
from ai_commerce.remote.reuse import approve_reuse
from ai_commerce.remote.scheduler import run_schedule
from ai_commerce.remote.validation import RemoteRequestError
from ai_commerce.remote.wake_adapter import GitHubArtifactProvenance, ProvenanceError, verify_provenance
from test_ef01_repair import _action, _worker_fixture, _files, _snapshot_fake, build_parser, remote
from test_ef01_ef02_integrity import RecordingTransport, candidate_evidence


def test_repository_markdown_clean_filter_requires_lf_before_verification():
    """Reproduce #149 using real repository attributes without Git writes."""
    repo = Path(__file__).resolve().parents[1]
    relative = "docs/engineering/EF02_SCOPE_PUBLISH_IMPLEMENTATION_2026-10-30.md"
    attrs = subprocess.run(
        ["git", "-C", str(repo), "check-attr", "text", "eol", "filter", "working-tree-encoding", "--", relative],
        capture_output=True, check=True, text=True,
    ).stdout
    assert relative + ": text: set" in attrs
    assert relative + ": eol: lf" in attrs
    assert relative + ": filter: unspecified" in attrs
    assert relative + ": working-tree-encoding: unspecified" in attrs
    raw = b"# Verified generated documentation\r\n\r\nPreserve byte integrity.\r\n"
    with pytest.raises(GitByteConsistencyError, match="CANDIDATE_GIT_FILTER_MISMATCH"):
        assert_candidate_git_filter_consistency(repo, [(relative, raw, "100644")])
    canonical_bytes = raw.replace(b"\r\n", b"\n")
    assert_candidate_git_filter_consistency(repo, [(relative, canonical_bytes, "100644")])


def _candidate_repo(tmp_path, monkeypatch):
    """Real candidate bytes with modeled Git observations; no fixture Git writes.

    Real Git snapshot/rename/filter behavior remains covered by the existing
    integration and byte-consistency tests. These cases exercise publication.
    """
    repo = tmp_path / "candidate"
    repo.mkdir()
    (repo / "old.txt").write_bytes(b"original\n")
    baseline = _files(repo)
    snapshot = lambda root, scope, deadline: _snapshot_fake(root, baseline, {"scope": scope})
    monkeypatch.setattr(runner, "_snapshot", snapshot)
    monkeypatch.setattr(transport, "_snapshot", snapshot)
    monkeypatch.setattr(transport, "assert_candidate_git_filter_consistency", lambda *a: None)
    return repo


def _ordinary_candidate(tmp_path, monkeypatch):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, drift="src/ordinary.py")
    result = context.worker.execute_action(_action("a"))
    return context, result


def test_ordinary_drift_pass_publishes_once_with_immutable_review_evidence(tmp_path, monkeypatch):
    context, result = _ordinary_candidate(tmp_path, monkeypatch)
    assert result["status"] == REVIEW_STATUS
    assert result["review_required"] is result["scope_review_required"] is True
    assert result["scope_valid"] is False
    assert result["out_of_scope_files"] == ["src/ordinary.py"]
    assert result["candidate_sha"] and len(context.candidates) == 1
    assert len(context.calls) == 2 and result["repair_rounds_used"] == 1
    proof = result["candidate_evidence"]
    assert proof == context.candidates[0]["evidence"]
    assert proof["allowed_paths"] == _action("a")["allowed_paths"]
    assert proof["verification"]["status"] == "VERIFIED_SCOPE_REVIEW_REQUIRED"
    assert proof["verification"]["exit_code"] == 0
    assert result["candidate_evidence_sha256"] == evidence_digest(proof)
    assert result["tracked_diff_sha256"] == proof["tracked_diff_sha256"]
    assert result["candidate_file_sha256"] == proof["candidate_file_sha256"]
    report = Path(result["evidence_path"]) / "ef01" / "engineering_result.json"
    assert hashlib.sha256(report.read_bytes()).hexdigest() == proof["verification"]["report_sha256"]
    schema = json.loads((Path(__file__).parents[1] / "contracts/RemoteCandidateEvidence.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(proof)
    assert not candidate_accepted(result)
    assert context.worker.execute_action(_action("a"))["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert len(context.candidates) == 1


def test_allowed_pass_preserves_normal_candidate_status(tmp_path, monkeypatch):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    result = context.worker.execute_action(_action("a"))
    assert result["status"] == "CANDIDATE_READY" and candidate_accepted(result)
    assert result["scope_valid"] is True and result["review_required"] is False
    assert result["out_of_scope_files"] == [] and len(context.candidates) == 1
    validate_candidate_evidence(result["candidate_evidence"])


def test_review_requires_fresh_broad_run_even_with_registered_narrow_group(tmp_path, monkeypatch):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, drift="src/ordinary.py")
    monkeypatch.setattr(mt, "approved_groups", lambda: (
        mt.TestGroup("mapped", ("tests/test_scope.py",), (), ("impl.py", "kept.txt", "src/ordinary.py")),))
    context.worker.profiles["mapped-narrow"] = ["python", "-m", "pytest", "tests/test_scope.py", "-q"]
    result = context.worker.execute_action(_action("a"))
    assert result["status"] == REVIEW_STATUS
    groups = result["ef01_invocations"][-1]["minimum_test"]["groups"]
    assert len(groups) == 1 and groups[0]["group"] == "registered-fallback"
    assert groups[0]["state"] == "EXECUTE"
    assert groups[0]["binding"]["argv"] == context.worker.profiles["project-tests"]


@pytest.mark.parametrize("drift", [".github/workflows/blocked.yml", "credentials/token.json", ".env.production", ".gitignore"])
def test_protected_drift_stops_before_verification_and_publication(tmp_path, monkeypatch, drift):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, drift=drift)
    result = context.worker.execute_action(_action("a"))
    assert result["status"] == "SCOPE_VIOLATION"
    assert result["verify_exit_code"] is None and result["candidate_sha"] is None
    assert not context.candidates and len(context.calls) == 1


@pytest.mark.parametrize("kind", ["fail", "unknown", "child-nonzero", "nonordinary"])
def test_failed_unknown_and_nonordinary_drift_never_publish(tmp_path, monkeypatch, kind):
    drift = "payload.exe" if kind == "nonordinary" else "src/ordinary.py"
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, drift=drift,
                              fail=("a",) if kind == "fail" else ())
    if kind in ("unknown", "child-nonzero"):
        child = remote.subprocess.Popen

        class Unknown(child):
            def communicate(self, timeout=None):
                output = super().communicate(timeout)
                if kind == "child-nonzero":
                    self.returncode = 1
                else:
                    args = build_parser().parse_args(self.argv[3:])
                    path = args.out_dir / "engineering_result.json"
                    report = json.loads(path.read_text())
                    report["status"] = "EXECUTION_UNKNOWN"
                    path.write_text(json.dumps(report))
                return output

        monkeypatch.setattr(remote.subprocess, "Popen", Unknown)
    result = context.worker.execute_action(_action("a"))
    assert result["status"] in ("VERIFICATION_FAILED", "EXECUTION_UNKNOWN", "CANDIDATE_INTEGRITY_FAILED")
    assert result["candidate_sha"] is None and not context.candidates


@pytest.mark.parametrize("kind", ["proof-missing", "proof-tamper", "log-tamper", "flag", "outside", "exit"])
def test_review_publication_requires_intact_registered_proof_and_honest_scope(tmp_path, monkeypatch, kind):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, drift="src/ordinary.py")
    child = remote.subprocess.Popen

    class Tamper(child):
        def communicate(self, timeout=None):
            output = super().communicate(timeout)
            args = build_parser().parse_args(self.argv[3:])
            path = args.out_dir / "engineering_result.json"
            report = json.loads(path.read_text())
            if kind == "proof-missing":
                report.pop("minimum_test")
            elif kind == "proof-tamper":
                report["minimum_test"]["groups"][0]["evidence_sha256"] = "0" * 64
            elif kind == "log-tamper":
                (args.out_dir / report["evidence_files"]["verify_stdout"]).write_text("tampered")
            elif kind == "flag":
                report["scope_valid"] = True
            elif kind == "outside":
                report["out_of_scope_files"] = []
            else:
                report["verify_exit_code"] = 1
            path.write_text(json.dumps(report))
            return output

    monkeypatch.setattr(remote.subprocess, "Popen", Tamper)
    result = context.worker.execute_action(_action("a"))
    assert result["status"] in ("UNPROVEN", "CANDIDATE_INTEGRITY_FAILED", "VERIFICATION_FAILED")
    assert result["candidate_sha"] is None and not context.candidates


def test_transport_commit_binds_review_and_exact_manifest_with_create_only_ref(tmp_path, monkeypatch):
    repo = _candidate_repo(tmp_path, monkeypatch)
    (repo / "ordinary.py").write_bytes(b"verified\n")
    proof = candidate_evidence(repo, "scope-001", ["old.txt"])
    github = RecordingTransport()
    result = github.candidate(source_sha=proof["effective_source_sha"], attempt_id=proof["attempt_id"],
                              workspace=repo, paths=proof["changed_files"],
                              expected_hashes=proof["candidate_file_sha256"], evidence=proof)
    assert result["status"] == REVIEW_STATUS and result["review_required"] is True
    assert commit_evidence(github.commit_data["message"]) == proof
    assert github.commit_data["parents"] == [proof["effective_source_sha"]]
    assert result["candidate_ref"].startswith("refs/heads/ef02/candidate/")
    assert result["candidate_evidence_sha256"] == evidence_digest(proof)
    assert all(method in ("GET", "POST") for method, _ in github.calls)
    assert github.calls[-1] == ("POST", "/git/refs")


@pytest.mark.parametrize("kind", ["missing", "status", "hash", "source", "unexpected-file", "patch"])
def test_transport_rejects_missing_or_altered_proof_before_any_api(tmp_path, monkeypatch, kind):
    repo = _candidate_repo(tmp_path, monkeypatch)
    (repo / "ordinary.py").write_bytes(b"verified\n")
    proof = candidate_evidence(repo, "scope-002", ["old.txt"])
    source, paths, hashes = proof["effective_source_sha"], list(proof["changed_files"]), dict(proof["candidate_file_sha256"])
    if kind == "missing":
        proof = None
    elif kind == "status":
        proof["status"] = "CANDIDATE_READY"
    elif kind == "hash":
        proof["candidate_file_sha256"]["ordinary.py"] = "0" * 64
    elif kind == "source":
        proof["effective_source_sha"] = "b" * 40
    elif kind == "unexpected-file":
        (repo / "unexpected.txt").write_text("unverified")
    else:
        proof["tracked_diff_sha256"] = "0" * 64
        proof["verification"]["evidence_sha256"]["tracked_diff"] = "0" * 64
    github = RecordingTransport()
    with pytest.raises(transport.CandidateIntegrityError):
        github.candidate(source_sha=source, attempt_id="scope-002", workspace=repo,
                         paths=paths, expected_hashes=hashes, evidence=proof)
    assert not github.calls


@pytest.mark.parametrize("path", [".git/config", ".github/unsafe", "secrets/token", "../escape", "C:/escape", "nested/../../escape", "private.pem"])
def test_transport_unsafe_paths_never_read_or_publish(tmp_path, path):
    github = RecordingTransport()
    with pytest.raises(transport.CandidateIntegrityError, match="CANDIDATE_UNSAFE_PATH"):
        github.candidate(source_sha="a" * 40, attempt_id="unsafe", workspace=tmp_path,
                         paths=[path], expected_hashes={path: None})
    assert not github.calls


def test_symlink_ancestor_rejected_before_hashing_or_transport(tmp_path, monkeypatch):
    # Deterministic Windows regression without requiring symlink privileges.
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p.name == "link" or original(p))
    path = "link/secret.txt"
    assert runner._candidate_hashes(tmp_path, [path])[path].startswith("UNSAFE_PATH:")
    github = RecordingTransport()
    with pytest.raises(transport.CandidateIntegrityError, match="CANDIDATE_UNSAFE_PATH"):
        github.candidate(source_sha="a" * 40, attempt_id="unsafe", workspace=tmp_path,
                         paths=[path], expected_hashes={path: None})
    assert not github.calls


@pytest.mark.parametrize("directory", ["build", "__pycache__", ".local"])
def test_ignored_unsafe_untracked_content_cannot_hide_behind_gitignore(tmp_path, monkeypatch, directory):
    unsafe = tmp_path / directory / "nested" / ".env.production"
    unsafe.parent.mkdir(parents=True)
    unsafe.write_text("fixture-only")
    (tmp_path / directory / "ordinary.txt").write_text("cache/evidence")
    def status(argv, **kwargs):
        assert "--ignored=matching" in argv and "status" in argv
        return subprocess.CompletedProcess(argv, 0, f"!! {directory}/\0".encode(), b"")
    monkeypatch.setattr(runner.subprocess, "run", status)
    changed = runner._changes(tmp_path)
    assert changed == [f"{directory}/nested/.env.production"]
    assert runner._protected_change(changed)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_generated_pytest_and_script_bytecode_caches_are_not_source_drift(tmp_path, monkeypatch, newline):
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / ".gitignore").write_bytes(newline.join([b"# Created by pytest automatically.", b"*", b""]))
    (cache / "README.md").write_text("pytest cache")
    scripts = tmp_path / ".github/scripts"
    (scripts / "__pycache__").mkdir(parents=True)
    (scripts / "probe.py").write_text("# tracked helper fixture")
    (scripts / "__pycache__/probe.cpython-314.pyc").write_bytes(b"bytecode fixture")
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, b"!! .pytest_cache/\0!! .github/scripts/__pycache__/\0", b""))
    assert runner._changes(tmp_path) == []


@pytest.mark.parametrize("path,content", [
    (".pytest_cache/.gitignore", b"altered control file\n"),
    (".pytest_cache/.env.production", b"fixture-only"),
    (".github/scripts/__pycache__/credentials.json", b"fixture-only"),
    (".github/scripts/__pycache__/unknown.cpython-314.pyc", b"unmatched bytecode"),
    (".github/workflows/ignored.yml", b"unsafe control change"),
])
def test_generated_cache_exceptions_do_not_hide_unsafe_ignored_changes(tmp_path, monkeypatch, path, content):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    ignored = (".pytest_cache/" if path.startswith(".pytest_cache/") else
               ".github/scripts/__pycache__/" if path.startswith(".github/scripts/") else path)
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, f"!! {ignored}\0".encode(), b""))
    changed = runner._changes(tmp_path)
    assert changed == [path]
    assert runner._protected_change(changed)


@pytest.mark.parametrize("forged_status", [REVIEW_STATUS, "CANDIDATE_READY"])
def test_scheduler_scope_review_blocks_fanin_even_if_status_is_renamed(forged_status):
    called = []
    batch = {"batch_id": "scope", "source_sha": "a" * 40,
             "actions": [_action("a"), _action("b", ("a",))]}

    def work(action):
        called.append(action["action_id"])
        return {"status": forged_status, "review_required": True, "candidate_sha": "b" * 40}

    report = run_schedule(batch, work)
    assert called == ["a"] and report["succeeded"] == 0
    assert report["status"] == "PARTIAL_OR_FAILED"
    assert report["actions"][0]["review_required"] is True
    assert report["actions"][1]["status"] == "BLOCKED_DEPENDENCY"


def test_raw_commit_prevents_transport_and_reuse_from_laundering_review(tmp_path, monkeypatch):
    repo = _candidate_repo(tmp_path, monkeypatch)
    (repo / "ordinary.py").write_text("verified")
    proof = candidate_evidence(repo, "original", ["old.txt"])

    class ImmutableReview(RecordingTransport):
        def api(self, method, path, data=None):
            self.calls.append((method, path))
            assert method == "GET"  # No composition writes before Brain assessment.
            if "/git/ref/" in path:
                return {"object": {"sha": "b" * 40}}
            if path.endswith("b" * 40):
                return {"message": "EF02 candidate attempt original" + EVIDENCE_MARKER + canonical(proof),
                        "tree": {"sha": "c" * 40}, "parents": [{"sha": proof["source_sha"]}]}
            if "/git/trees/" in path:
                return {"tree": [], "truncated": False}
            return {"tree": {"sha": "d" * 40}}

    github = ImmutableReview()
    # All mutable result flags are stripped/rewritten; the pinned commit retains truth.
    with pytest.raises(transport.RemoteTransportError, match="DEPENDENCY_BRAIN_SCOPE_REVIEW_REQUIRED"):
        github.integrated_source(source_sha=proof["source_sha"], attempt_id="consumer", dependencies=[{
            "status": "CANDIDATE_READY", "candidate_sha": "b" * 40,
            "review_required": False, "candidate_evidence_sha256": evidence_digest(proof)}])
    with pytest.raises(transport.RemoteTransportError, match="REUSE_BRAIN_SCOPE_REVIEW_REQUIRED"):
        github.verify_reused_candidate({"attempt_id": "original", "candidate_sha": "b" * 40,
            "candidate_ref": "refs/heads/ef02/candidate/" + github.attempt_key("original")})


@pytest.mark.parametrize("metadata", [{"review_required": True}, {"scope_review_required": True},
    {"out_of_scope_files": ["ordinary.py"]}, {"candidate_evidence": {"status": REVIEW_STATUS}}])
def test_approved_registry_cannot_erase_unreviewed_candidate(metadata):
    entry = {"attempt_id": "original", **metadata}
    with pytest.raises(RemoteRequestError, match="REUSE_BRAIN_SCOPE_REVIEW_REQUIRED"):
        approve_reuse({"source_sha": "a" * 40}, {"attempt_id": "original"}, {"candidates": {"original": entry}})


def test_completion_roundtrip_and_provenance_preserve_review_digest(tmp_path, monkeypatch):
    _, result = _ordinary_candidate(tmp_path, monkeypatch)
    report = {"kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1, "batch_id": "scope",
              "source_sha": result["source_sha"], "status": "PARTIAL_OR_FAILED",
              "actions": [result], "total": 1, "succeeded": 0,
              "github_workflow_run_id": "42", "github_issue_number": 12,
              "wake_event_status": "RECORDED_UNBOUND"}
    event = build_completion_event(report, repository="owner/repo", run_id=42, run_attempt=1, issue_number=12)
    report["wake_event_id"] = event["event_id"]
    assert validate_completion_event(event) == event
    assert event["actions"][0]["review_required"] is True
    assert event["actions"][0]["candidate_evidence_sha256"] == result["candidate_evidence_sha256"]
    provenance = GitHubArtifactProvenance("owner/repo", 42, 1, "ef02-evidence", "completed", attestation_verified=True)
    authority = SimpleNamespace(verify_artifact=lambda *a, **k: True)
    verify_provenance(event, report, provenance, authority=authority)
    report["actions"][0]["candidate_evidence_sha256"] = "0" * 64
    with pytest.raises(ProvenanceError, match="ACTIONS_REVIEW_METADATA_MISMATCH"):
        verify_provenance(event, report, provenance, authority=authority)


def test_completion_cannot_turn_review_candidate_into_success(tmp_path, monkeypatch):
    _, result = _ordinary_candidate(tmp_path, monkeypatch)
    result["status"] = "CANDIDATE_READY"
    report = {"kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1, "batch_id": "scope",
              "source_sha": result["source_sha"], "status": "COMPLETE", "actions": [result], "total": 1, "succeeded": 1}
    with pytest.raises(CompletionEventError, match="CANDIDATE_SCOPE_REVIEW_LAUNDERED"):
        build_completion_event(report, repository="owner/repo", run_id=42, run_attempt=1, issue_number=12)
