"""Verify-first publication retains immutable scope review; no live providers/API."""
from __future__ import annotations

import hashlib
import json
import subprocess
import traceback
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

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
    candidate_get_path = "/git/ref/" + result["candidate_ref"].removeprefix("refs/")
    assert github.calls[-2] == ("POST", "/git/refs")
    assert github.calls[-1] == ("GET", candidate_get_path)


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


# #512's old api() discarded both phase and body. Its GITHUB_HTTP_422 receipt
# therefore proves ROOT_CAUSE_UNPROVEN, not a policy/ref/tree/quota diagnosis.
# No replay can reconstruct that lost observation. No-executor recovery would
# need an independently granted, hash-pinned snapshot release; existing reuse
# requires an already published candidate SHA/ref and cannot authorize it.
_PUBLICATION_CASES = [
    (transport.PublicationStage.BASE_GET, "GET", "/git/commits/" + "a" * 40, "GIT_COMMIT"),
    (transport.PublicationStage.BLOB_CREATE, "POST", "/git/blobs", "GIT_BLOBS"),
    (transport.PublicationStage.TREE_CREATE, "POST", "/git/trees", "GIT_TREES"),
    (transport.PublicationStage.COMMIT_CREATE, "POST", "/git/commits", "GIT_COMMITS"),
    (transport.PublicationStage.CANDIDATE_REF_CREATE, "POST", "/git/refs", "GIT_REFS"),
]
_PRIVATE = "private-token-and-owner-workspace-marker"


def _publication_fixture(tmp_path, monkeypatch, *, fault=None, body=None, status=422, kind="http"):
    repo = _candidate_repo(tmp_path, monkeypatch)
    (repo / "ordinary.py").write_bytes(b"verified\n")
    proof = candidate_evidence(repo, "publication-001", ["old.txt", "ordinary.py"])
    github = transport.GitHubTransport("private-owner/private-repo", _PRIVATE)
    calls, writes, reads = [], [], []

    class ErrorBody(BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    def open_request(request, timeout):
        method = request.get_method()
        path = urlsplit(request.full_url).path.removeprefix("/repos/private-owner/private-repo")
        calls.append((method, path))
        data = json.loads(request.data) if request.data else None
        if method == "POST":
            # Simulate an external effect before its reply is lost. This record
            # must never itself authorize a retry or prove ref publication.
            writes.append((path, data))
        if (method, path) == fault:
            if kind == "timeout":
                raise TimeoutError(_PRIVATE)
            if kind == "url-timeout":
                raise URLError(TimeoutError(_PRIVATE))
            if kind == "reset":
                raise ConnectionResetError(_PRIVATE)
            if kind == "truncated":
                from http.client import IncompleteRead
                raise IncompleteRead(_PRIVATE.encode(), 200)
            if kind == "json":
                return BytesIO(_PRIVATE.encode())
            if kind == "shape":
                return BytesIO(json.dumps([] if path == "/git/refs" else
                    {"sha": _PRIVATE, "tree": {"sha": _PRIVATE}}).encode())
            raise HTTPError(request.full_url + "?token=" + _PRIVATE, status,
                            _PRIVATE, {"X-Private": _PRIVATE}, ErrorBody(body or b"{}"))
        if method == "GET":
            if "/git/ref/" in path:
                ref_subpath = path.removeprefix("/git/ref/").removeprefix("heads/")
                response = {"ref": "refs/heads/" + ref_subpath,
                            "object": {"sha": "d" * 40, "type": "commit"}}
            else:
                response = {"tree": {"sha": "a" * 40}}
        elif path == "/git/commits":
            response = {"sha": "d" * 40}
        elif path == "/git/refs":
            response = {"ref": data["ref"], "object": {"sha": data["sha"], "type": "commit"}}
        else:
            response = {"sha": ("b" if path == "/git/blobs" else "c") * 40}
        return BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(transport, "urlopen", open_request)
    def publish():
        return github.candidate(source_sha=proof["effective_source_sha"], attempt_id=proof["attempt_id"],
                                workspace=repo, paths=proof["changed_files"],
                                expected_hashes=proof["candidate_file_sha256"], evidence=proof)
    return SimpleNamespace(publish=publish, github=github, calls=calls, writes=writes,
                           reads=reads, proof=proof)


@pytest.mark.parametrize("stage,method,path,route", _PUBLICATION_CASES)
def test_candidate_http_422_retains_only_closed_stage_and_safe_identifiers(
        tmp_path, monkeypatch, stage, method, path, route):
    body = json.dumps({"message": _PRIVATE, "code": "unprocessable", "token": _PRIVATE,
        "documentation_url": "https://" + _PRIVATE, "errors": [
            {"code": "invalid", "field": "sha", "value": _PRIVATE, "resource": _PRIVATE},
            {"code": _PRIVATE, "field": "C:/" + _PRIVATE},
            {"code": "custom", "field": "ref", "message": _PRIVATE}]}).encode()
    fixture = _publication_fixture(tmp_path, monkeypatch, fault=(method, path), body=body)
    with pytest.raises(transport.RemoteTransportError) as error:
        fixture.publish()
    exc = error.value
    payload = exc.publication.payload()
    assert payload == {"stage": stage.value, "method": method, "route": route, "http_status": 422,
        "github_codes": ["custom", "invalid", "unprocessable"], "validation_fields": ["ref", "sha"],
        "disposition": "TERMINAL_VALIDATION_OR_POLICY_REJECTION",
        "root_owner": "REMOTE_GITHUB_OWNER", "replay_authorized": False}
    assert str(exc).startswith("GITHUB_HTTP_422;publication=")
    assert len(str(exc)) < 1024 and _PRIVATE not in str(exc) and "private-owner" not in str(exc)
    assert str(tmp_path) not in str(exc) and "ordinary.py" not in str(exc)
    assert _PRIVATE not in "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert exc.__cause__ is None and exc.__suppress_context__
    assert fixture.calls == [(m, p) for _, m, p, _ in _PUBLICATION_CASES[:len(fixture.calls)]]
    assert fixture.calls[-1] == (method, path) and fixture.reads == [8193]


@pytest.mark.parametrize("body", [
    b"not-json", b"\xff", b"[]", b"null", b'{"errors":"private"}',
    b'{"errors":["private",null,42,{"code":{},"field":[]}]}',
    b'{"code":"INVALID","errors":[{"code":"invalid-secret","field":"refs/heads/private"}]}',
    b'{"message":"' + b"x" * 9000 + b'","errors":[{"code":"invalid","field":"sha"}]}',
])
def test_candidate_validation_body_is_bounded_and_fail_closed(tmp_path, monkeypatch, body):
    fixture = _publication_fixture(tmp_path, monkeypatch, fault=("POST", "/git/refs"), body=body)
    with pytest.raises(transport.RemoteTransportError) as error:
        fixture.publish()
    payload = error.value.publication.payload()
    assert payload["http_status"] == 422 and payload["github_codes"] == payload["validation_fields"] == []
    assert payload["stage"] == "CANDIDATE_REF_CREATE" and fixture.reads == [8193]
    assert fixture.calls.count(("POST", "/git/refs")) == 1


@pytest.mark.parametrize("stage,method,path,route", _PUBLICATION_CASES)
@pytest.mark.parametrize("kind", ["timeout", "url-timeout", "reset", "truncated", "json", "shape"])
def test_candidate_ambiguous_reply_stops_at_exact_stage_without_second_write(
        tmp_path, monkeypatch, stage, method, path, route, kind):
    fixture = _publication_fixture(tmp_path, monkeypatch, fault=(method, path), kind=kind)
    with pytest.raises(transport.RemoteTransportError) as error:
        fixture.publish()
    payload = error.value.publication.payload()
    assert payload["stage"] == stage.value and payload["route"] == route
    assert payload["http_status"] is None and payload["replay_authorized"] is False
    assert payload["disposition"] == ("READ_UNCONFIRMED" if method == "GET" else "UNKNOWN_EFFECT_RECONCILE")
    assert fixture.calls == [(m, p) for _, m, p, _ in _PUBLICATION_CASES[:len(fixture.calls)]]
    assert fixture.calls[-1] == (method, path)
    assert len(fixture.writes) == len(fixture.calls) - 1
    assert _PRIVATE not in str(error.value)


def test_candidate_ref_collision_never_infers_matching_commit_or_reuses_ref(tmp_path, monkeypatch):
    fixture = _publication_fixture(tmp_path, monkeypatch, fault=("POST", "/git/refs"),
        body=b'{"message":"Reference already exists","errors":[{"code":"already_exists","field":"ref"}]}')
    with pytest.raises(transport.RemoteTransportError) as error:
        fixture.publish()
    assert error.value.publication.payload()["github_codes"] == ["already_exists"]
    assert error.value.publication.payload()["stage"] == "CANDIDATE_REF_CREATE"
    assert len(fixture.calls) == 5  # No update, fallback, read-based guess or second commit/ref.


def test_candidate_success_preserves_payload_evidence_and_create_only_protocol(tmp_path, monkeypatch):
    fixture = _publication_fixture(tmp_path, monkeypatch)
    result = fixture.publish()
    assert result["candidate_sha"] == "d" * 40 and result["candidate_evidence"] == fixture.proof
    assert result["status"] == "CANDIDATE_READY" and result["review_required"] is False
    assert result["candidate_evidence_sha256"] == evidence_digest(fixture.proof)
    candidate_get_path = "/git/ref/" + result["candidate_ref"].removeprefix("refs/")
    assert fixture.calls == [(m, p) for _, m, p, _ in _PUBLICATION_CASES] + [("GET", candidate_get_path)]
    assert fixture.writes[0][1] == {"content": "dmVyaWZpZWQK", "encoding": "base64"}
    assert fixture.writes[1][1]["base_tree"] == "a" * 40
    assert commit_evidence(fixture.writes[2][1]["message"]) == fixture.proof
    assert fixture.writes[2][1]["parents"] == ["a" * 40]
    assert fixture.writes[-1] == ("/git/refs", {"ref": result["candidate_ref"], "sha": "d" * 40})
    assert len([w for w in fixture.writes if w[0] == "/git/refs"]) == 1


@pytest.mark.parametrize("status,disposition", [(403, "HTTP_REJECTION"), (500, "UNKNOWN_EFFECT_RECONCILE")])
def test_candidate_http_rejection_and_server_ambiguity_have_no_write_retry(tmp_path, monkeypatch, status, disposition):
    fixture = _publication_fixture(tmp_path, monkeypatch, fault=("POST", "/git/refs"), status=status)
    with pytest.raises(transport.RemoteTransportError) as error:
        fixture.publish()
    assert error.value.publication.payload()["disposition"] == disposition
    assert len(fixture.calls) == 5


def test_publication_vocabulary_rejects_arbitrary_stage_status_and_details():
    with pytest.raises(ValueError, match="PUBLICATION_STAGE_INVALID"):
        transport.PublicationDiagnostic(_PRIVATE)
    for status in (True, "422", 999, -1):
        with pytest.raises(ValueError, match="PUBLICATION_HTTP_STATUS_INVALID"):
            transport.PublicationDiagnostic(transport.PublicationStage.BLOB_CREATE, status)
    with pytest.raises(ValueError, match="PUBLICATION_DISPOSITION_INVALID"):
        transport.PublicationDiagnostic(transport.PublicationStage.BLOB_CREATE, disposition="INVALID")
    diagnostic = transport.PublicationDiagnostic(transport.PublicationStage.TREE_CREATE, 422,
        ("invalid", _PRIVATE) * 100, ("sha", _PRIVATE) * 100)
    assert diagnostic.payload()["github_codes"] == ["invalid"]
    assert diagnostic.payload()["validation_fields"] == ["sha"]
    assert set(transport.PublicationRoute) == {route for _, route, _ in transport._PUBLICATION_STEPS.values()}


@pytest.mark.parametrize("state", ["missing-ref", "different-sha", "altered-evidence", "matching"])
def test_reuse_requires_remote_ref_exact_commit_and_evidence_reads(tmp_path, monkeypatch, state):
    fixture = _publication_fixture(tmp_path, monkeypatch)
    github, proof = fixture.github, fixture.proof
    attempt = proof["attempt_id"]
    ref = "refs/heads/ef02/candidate/" + github.attempt_key(attempt)
    approved = {"attempt_id": attempt, "candidate_sha": "d" * 40, "candidate_ref": ref,
                "parent_sha": "a" * 40, "tree_sha": "c" * 40, "source_sha": "a" * 40,
                "candidate_evidence_sha256": evidence_digest(proof)}
    reads = []
    def open_request(request, timeout):
        assert request.get_method() == "GET"  # Reconciliation cannot create anything.
        path = urlsplit(request.full_url).path.removeprefix("/repos/private-owner/private-repo")
        reads.append(path)
        if "/git/ref/heads/ef02/candidate/" in path:
            if state == "missing-ref":
                raise HTTPError(request.full_url, 404, _PRIVATE, {}, BytesIO(b'{"message":"private"}'))
            response = {"ref": ref, "object": {"sha": ("e" if state == "different-sha" else "d") * 40, "type": "commit"}}
        elif path == "/git/commits/" + "d" * 40:
            bound = {**proof, "fingerprint": "0" * 64} if state == "altered-evidence" else proof
            response = {"message": "EF02 candidate attempt " + attempt + EVIDENCE_MARKER + canonical(bound),
                        "tree": {"sha": "c" * 40}, "parents": [{"sha": "a" * 40}]}
        elif "/git/ref/heads/ef02/claims/" in path:
            response = {"ref": "refs/heads/ef02/claims/" + github.attempt_key(attempt), "object": {"sha": "b" * 40, "type": "commit"}}
        elif path == "/git/commits/" + "b" * 40:
            response = {"message": "EF02 CLAIM\n\nfingerprint=" + proof["fingerprint"] + "\nattempt_id=" + attempt,
                        "parents": [{"sha": "a" * 40}]}
        else:
            raise AssertionError("unexpected reconciliation read")
        return BytesIO(json.dumps(response).encode())
    monkeypatch.setattr(transport, "urlopen", open_request)
    if state == "matching":
        assert github.verify_reused_candidate(approved) is None and len(reads) == 4
    else:
        expected = {"missing-ref": "GITHUB_HTTP_404", "different-sha": "REUSE_REF_SHA_CHANGED",
                    "altered-evidence": "REUSE_COMMIT_MISMATCH"}[state]
        with pytest.raises(transport.RemoteTransportError, match=expected) as error:
            github.verify_reused_candidate(approved)
        assert _PRIVATE not in str(error.value)
        assert len(reads) == (2 if state == "altered-evidence" else 1)
    assert not fixture.writes


@pytest.mark.parametrize("post_response,desc", [
    ({}, "empty dict"),
    ({"ref": "refs/heads/other", "object": {"sha": "d" * 40, "type": "commit"}}, "wrong ref"),
    ({"ref": "REFS/HEADS/EF02/CANDIDATE/UPPERCASE", "object": {"sha": "d" * 40, "type": "commit"}}, "case variant"),
    ({"ref": "expected", "object": {}}, "missing object.sha"),
    ({"ref": "expected", "object": {"sha": "0" * 40, "type": "commit"}}, "wrong sha"),
    ({"ref": "expected", "object": {"sha": "d" * 40, "type": "tag"}}, "wrong type tag"),
    ({"ref": "expected", "object": {"sha": "d" * 40, "type": "tree"}}, "wrong type tree"),
    ({"ref": "expected", "object": {"sha": "d" * 40, "type": "blob"}}, "wrong type blob"),
    ({"ref": "expected", "object": "not-a-dict"}, "object not a dict"),
    ([], "response not a dict"),
])
def test_candidate_ref_post_adversarial_identities_fail_closed_unknown_reconcile(
        tmp_path, monkeypatch, post_response, desc):
    fixture = _publication_fixture(tmp_path, monkeypatch)
    key = fixture.github.attempt_key(fixture.proof["attempt_id"])
    expected_ref = "refs/heads/ef02/candidate/" + key

    orig_open = transport.urlopen
    def open_request(request, timeout):
        method = request.get_method()
        path = urlsplit(request.full_url).path.removeprefix("/repos/private-owner/private-repo")
        if method == "POST" and path == "/git/refs":
            resp = dict(post_response) if isinstance(post_response, dict) else post_response
            if isinstance(resp, dict) and resp.get("ref") == "expected":
                resp["ref"] = expected_ref
            fixture.writes.append((path, json.loads(request.data)))
            fixture.calls.append((method, path))
            return BytesIO(json.dumps(resp).encode())
        return orig_open(request, timeout)

    monkeypatch.setattr(transport, "urlopen", open_request)
    with pytest.raises(transport.RemoteTransportError) as error:
        fixture.publish()
    payload = error.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["disposition"] == "UNKNOWN_EFFECT_RECONCILE"
    assert payload["replay_authorized"] is False
    assert payload["http_status"] is None
    ref_writes = [w for w in fixture.writes if w[0] == "/git/refs"]
    assert len(ref_writes) == 1
    assert not any(m == "GET" and "/git/ref/" in p for m, p in fixture.calls)


@pytest.mark.parametrize("case,kind,status,get_body", [
    ("get-404", "http", 404, b'{"message":"Not Found"}'),
    ("get-timeout", "timeout", 0, None),
    ("get-url-timeout", "url-timeout", 0, None),
    ("get-non-json", "non-json", 0, b'not-json'),
    ("get-empty-dict", "json-body", 0, b'{}'),
    ("get-wrong-sha", "json-body", 0, b'{"ref":"expected","object":{"sha":"0000000000000000000000000000000000000000","type":"commit"}}'),
    ("get-wrong-type", "json-body", 0, b'{"ref":"expected","object":{"sha":"dddddddddddddddddddddddddddddddddddddddd","type":"tag"}}'),
    ("get-wrong-name", "json-body", 0, b'{"ref":"refs/heads/other","object":{"sha":"dddddddddddddddddddddddddddddddddddddddd","type":"commit"}}'),
])
def test_candidate_ref_get_adversarial_verification_fails_closed_unknown_reconcile(
        tmp_path, monkeypatch, case, kind, status, get_body):
    fixture = _publication_fixture(tmp_path, monkeypatch)
    key = fixture.github.attempt_key(fixture.proof["attempt_id"])
    expected_ref = "refs/heads/ef02/candidate/" + key

    orig_open = transport.urlopen
    def open_request(request, timeout):
        method = request.get_method()
        path = urlsplit(request.full_url).path.removeprefix("/repos/private-owner/private-repo")
        if method == "GET" and "/git/ref/" in path:
            fixture.calls.append((method, path))
            if kind == "timeout":
                raise TimeoutError(_PRIVATE)
            if kind == "url-timeout":
                raise URLError(TimeoutError(_PRIVATE))
            if kind == "http":
                raise HTTPError(request.full_url, status, "Not Found", {}, BytesIO(get_body))
            if kind == "non-json":
                return BytesIO(get_body)
            if kind == "json-body":
                body = get_body.replace(b'"expected"', json.dumps(expected_ref).encode())
                return BytesIO(body)
        return orig_open(request, timeout)

    monkeypatch.setattr(transport, "urlopen", open_request)
    with pytest.raises(transport.RemoteTransportError) as error:
        fixture.publish()
    payload = error.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["disposition"] == "UNKNOWN_EFFECT_RECONCILE"
    assert payload["replay_authorized"] is False
    if status == 404:
        assert payload["http_status"] == 404
    else:
        assert payload["http_status"] is None
    ref_writes = [w for w in fixture.writes if w[0] == "/git/refs"]
    assert len(ref_writes) == 1
    get_calls = [c for c in fixture.calls if c[0] == "GET" and "/git/ref/" in c[1]]
    assert len(get_calls) == 1
    assert _PRIVATE not in str(error.value)


# Subclass GitHubTransport tests: production must fail closed without
# __init_subclass__ monkeypatching or success fabrication.

class BaseSubclassTransport(transport.GitHubTransport):
    def __init__(self):
        super().__init__("private-owner/private-repo", _PRIVATE)
        self.calls = []
        self.post_refs_count = 0
        self.get_ref_count = 0

    def base_dispatch(self, method, path, data=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/git/commits/"):
            return {"tree": {"sha": "a" * 40}}
        if method == "POST" and path == "/git/blobs":
            return {"sha": "b" * 40}
        if method == "POST" and path == "/git/trees":
            return {"sha": "c" * 40}
        if method == "POST" and path == "/git/commits":
            return {"sha": "d" * 40}
        raise AssertionError("unexpected base dispatch: " + method + " " + path)


def _run_subclass_candidate(tmp_path, monkeypatch, transport_instance, attempt_id="subclass-001"):
    repo = _candidate_repo(tmp_path, monkeypatch)
    (repo / "ordinary.py").write_bytes(b"verified\n")
    proof = candidate_evidence(repo, attempt_id, ["old.txt", "ordinary.py"])
    return transport_instance.candidate(
        source_sha=proof["effective_source_sha"],
        attempt_id=proof["attempt_id"],
        workspace=repo,
        paths=proof["changed_files"],
        expected_hashes=proof["candidate_file_sha256"],
        evidence=proof,
    )


def test_subclass_overriding_api_empty_post_refs_fails_closed(tmp_path, monkeypatch):
    class EmptyPostRefsTransport(BaseSubclassTransport):
        def api(self, method, path, data=None):
            if method == "POST" and path == "/git/refs":
                self.post_refs_count += 1
                return {}
            return self.base_dispatch(method, path, data)

    t = EmptyPostRefsTransport()
    with pytest.raises(transport.RemoteTransportError) as exc:
        _run_subclass_candidate(tmp_path, monkeypatch, t, "empty-post-001")
    payload = exc.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["disposition"] == "UNKNOWN_EFFECT_RECONCILE"
    assert payload["replay_authorized"] is False
    assert t.post_refs_count == 1
    assert t.get_ref_count == 0
    assert not any(m == "GET" and "/git/ref/" in p for m, p in t.calls)
    assert _PRIVATE not in str(exc.value)


@pytest.mark.parametrize("bad_post_resp,case", [
    ({"ref": "refs/heads/wrong", "object": {"sha": "d" * 40, "type": "commit"}}, "wrong ref"),
    ({"object": {"sha": "d" * 40, "type": "commit"}}, "missing ref"),
    ({"ref": "expected", "object": {}}, "missing object sha"),
    ({"ref": "expected", "object": {"sha": "0" * 40, "type": "commit"}}, "wrong sha"),
    ({"ref": "expected", "object": {"sha": "d" * 40, "type": "tag"}}, "wrong type"),
    ({"ref": "expected", "object": "not-a-dict"}, "object not a dict"),
    ({"ref": "expected"}, "missing object"),
])
def test_subclass_overriding_api_bad_post_refs_payload_fails_closed(
        tmp_path, monkeypatch, bad_post_resp, case):
    class BadPostTransport(BaseSubclassTransport):
        def api(self, method, path, data=None):
            if method == "POST" and path == "/git/refs":
                self.post_refs_count += 1
                resp = dict(bad_post_resp)
                if resp.get("ref") == "expected":
                    resp["ref"] = data["ref"]
                return resp
            return self.base_dispatch(method, path, data)

    t = BadPostTransport()
    with pytest.raises(transport.RemoteTransportError) as exc:
        _run_subclass_candidate(tmp_path, monkeypatch, t, "bad-post-001")
    payload = exc.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["disposition"] == "UNKNOWN_EFFECT_RECONCILE"
    assert payload["replay_authorized"] is False
    assert t.post_refs_count == 1
    assert t.get_ref_count == 0
    assert not any(m == "GET" and "/git/ref/" in p for m, p in t.calls)
    assert _PRIVATE not in str(exc.value)


@pytest.mark.parametrize("get_error_type", ["assertion", "http_500", "transport_unknown"])
def test_subclass_overriding_api_error_on_get_after_post_fails_closed(
        tmp_path, monkeypatch, get_error_type):
    class ErrorOnGetTransport(BaseSubclassTransport):
        def api(self, method, path, data=None):
            if method == "POST" and path == "/git/refs":
                self.post_refs_count += 1
                return {"ref": data["ref"], "object": {"sha": data["sha"], "type": "commit"}}
            if method == "GET" and "/git/ref/" in path:
                self.get_ref_count += 1
                if get_error_type == "assertion":
                    raise AssertionError("unrecorded ref asserted: " + _PRIVATE)
                if get_error_type == "http_500":
                    raise transport.RemoteTransportError("GITHUB_HTTP_500", http_status=500)
                raise transport.RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
            return self.base_dispatch(method, path, data)

    t = ErrorOnGetTransport()
    with pytest.raises(transport.RemoteTransportError) as exc:
        _run_subclass_candidate(tmp_path, monkeypatch, t, "error-get-001")
    payload = exc.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["disposition"] == "UNKNOWN_EFFECT_RECONCILE"
    assert payload["replay_authorized"] is False
    assert t.post_refs_count == 1
    assert t.get_ref_count == 1
    assert _PRIVATE not in str(exc.value)


def test_subclass_overriding_api_does_not_record_remote_ref_fails_closed(tmp_path, monkeypatch):
    class NoRecordTransport(BaseSubclassTransport):
        def api(self, method, path, data=None):
            if method == "POST" and path == "/git/refs":
                self.post_refs_count += 1
                # Does NOT save ref anywhere
                return {"ref": data["ref"], "object": {"sha": data["sha"], "type": "commit"}}
            if method == "GET" and "/git/ref/" in path:
                self.get_ref_count += 1
                raise transport.RemoteTransportError("GITHUB_HTTP_404", http_status=404)
            return self.base_dispatch(method, path, data)

    t = NoRecordTransport()
    with pytest.raises(transport.RemoteTransportError) as exc:
        _run_subclass_candidate(tmp_path, monkeypatch, t, "no-record-001")
    payload = exc.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["disposition"] == "UNKNOWN_EFFECT_RECONCILE"
    assert payload["http_status"] == 404
    assert payload["replay_authorized"] is False
    assert t.post_refs_count == 1
    assert t.get_ref_count == 1
    assert _PRIVATE not in str(exc.value)


@pytest.mark.parametrize("bad_get_resp,case", [
    ({"ref": "refs/heads/other", "object": {"sha": "d" * 40, "type": "commit"}}, "wrong ref"),
    ({"ref": "expected", "object": {"sha": "0" * 40, "type": "commit"}}, "wrong sha"),
    ({"ref": "expected", "object": {"sha": "d" * 40, "type": "tag"}}, "wrong type"),
    ({"ref": "expected", "object": {}}, "missing sha"),
    ({"ref": "expected", "object": None}, "object None"),
    ({"ref": "expected"}, "missing object"),
])
def test_subclass_overriding_api_wrong_fields_on_get_fails_closed(
        tmp_path, monkeypatch, bad_get_resp, case):
    class WrongOnGetTransport(BaseSubclassTransport):
        def api(self, method, path, data=None):
            if method == "POST" and path == "/git/refs":
                self.post_refs_count += 1
                return {"ref": data["ref"], "object": {"sha": data["sha"], "type": "commit"}}
            if method == "GET" and "/git/ref/" in path:
                self.get_ref_count += 1
                resp = dict(bad_get_resp)
                if resp.get("ref") == "expected":
                    ref_subpath = path.removeprefix("/git/ref/")
                    resp["ref"] = ref_subpath if ref_subpath.startswith("refs/") else "refs/" + ref_subpath
                return resp
            return self.base_dispatch(method, path, data)

    t = WrongOnGetTransport()
    with pytest.raises(transport.RemoteTransportError) as exc:
        _run_subclass_candidate(tmp_path, monkeypatch, t, "wrong-get-001")
    payload = exc.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["disposition"] == "UNKNOWN_EFFECT_RECONCILE"
    assert payload["replay_authorized"] is False
    assert t.post_refs_count == 1
    assert t.get_ref_count == 1
    assert _PRIVATE not in str(exc.value)


@pytest.mark.parametrize("target_method", ["POST", "GET"])
@pytest.mark.parametrize("malformed", [[], None, "invalid-string", 42])
def test_subclass_overriding_api_malformed_payload_fails_closed(
        tmp_path, monkeypatch, target_method, malformed):
    class MalformedTransport(BaseSubclassTransport):
        def api(self, method, path, data=None):
            if method == "POST" and path == "/git/refs":
                self.post_refs_count += 1
                if target_method == "POST":
                    return malformed
                return {"ref": data["ref"], "object": {"sha": data["sha"], "type": "commit"}}
            if method == "GET" and "/git/ref/" in path:
                self.get_ref_count += 1
                if target_method == "GET":
                    return malformed
                ref_subpath = path.removeprefix("/git/ref/")
                return {"ref": ref_subpath if ref_subpath.startswith("refs/") else "refs/" + ref_subpath,
                        "object": {"sha": "d" * 40, "type": "commit"}}
            return self.base_dispatch(method, path, data)

    t = MalformedTransport()
    with pytest.raises(transport.RemoteTransportError) as exc:
        _run_subclass_candidate(tmp_path, monkeypatch, t, "malformed-001")
    payload = exc.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["disposition"] == "UNKNOWN_EFFECT_RECONCILE"
    assert payload["replay_authorized"] is False
    assert t.post_refs_count == 1
    if target_method == "POST":
        assert t.get_ref_count == 0
    else:
        assert t.get_ref_count == 1
    assert _PRIVATE not in str(exc.value)


def test_source_regression_no_github_transport_init_subclass():
    import inspect
    assert "__init_subclass__" not in transport.GitHubTransport.__dict__
    assert inspect.getattr_static(transport.GitHubTransport, "__init_subclass__") is inspect.getattr_static(object, "__init_subclass__")
    source = inspect.getsource(transport.GitHubTransport)
    assert "__init_subclass__" not in source
    assert "adapted_api" not in source
    assert "orig_api" not in source
    class CustomSubclass(transport.GitHubTransport):
        def api(self, method, path, data=None):
            return {"custom": True}
    assert "adapted_api" not in repr(CustomSubclass.api)
    assert getattr(CustomSubclass.api, "__name__", "") == "api"


def test_positive_stateful_fake_exact_post_and_get_readback_and_duplicate_collision(
        tmp_path, monkeypatch):
    repo = _candidate_repo(tmp_path, monkeypatch)
    (repo / "ordinary.py").write_bytes(b"verified\n")
    proof = candidate_evidence(repo, "stateful-001", ["old.txt", "ordinary.py"])
    t = RecordingTransport()

    # Before publication, ref does not exist
    key = t.attempt_key("stateful-001")
    expected_ref = "refs/heads/ef02/candidate/" + key
    assert expected_ref not in t.refs
    with pytest.raises(transport.RemoteTransportError) as exc_before:
        t.api("GET", "/git/ref/heads/ef02/candidate/" + key)
    assert exc_before.value.http_status == 404

    # Publish succeeds with stateful recording
    result = t.candidate(
        source_sha=proof["effective_source_sha"],
        attempt_id=proof["attempt_id"],
        workspace=repo,
        paths=proof["changed_files"],
        expected_hashes=proof["candidate_file_sha256"],
        evidence=proof,
    )
    assert result["candidate_sha"] == "d" * 40
    assert result["candidate_ref"] == expected_ref
    assert result["candidate_ref"] in t.refs
    assert t.refs[result["candidate_ref"]] == "d" * 40

    # GET readback reads the separately saved SHA from state
    readback = t.api("GET", "/git/ref/heads/ef02/candidate/" + key)
    assert readback["ref"] == expected_ref
    assert readback["object"]["sha"] == "d" * 40
    assert readback["object"]["type"] == "commit"

    # Duplicate POST collision is not success
    with pytest.raises(transport.RemoteTransportError) as collision_exc:
        t.candidate(
            source_sha=proof["effective_source_sha"],
            attempt_id=proof["attempt_id"],
            workspace=repo,
            paths=proof["changed_files"],
            expected_hashes=proof["candidate_file_sha256"],
            evidence=proof,
        )
    payload = collision_exc.value.publication.payload()
    assert payload["stage"] == "CANDIDATE_REF_CREATE"
    assert payload["http_status"] == 422
    assert "already_exists" in payload["github_codes"]
    assert payload["disposition"] == "TERMINAL_VALIDATION_OR_POLICY_REJECTION"
