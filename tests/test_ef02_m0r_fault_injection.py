"""M0-R deterministic negative-path fault injections: never call real providers.

These tests model faults at EF02 boundary methods on an isolated worker; they
do not assert that a live Windows runner was taken offline or cancelled.
"""
from __future__ import annotations

import json
import subprocess

import pytest
from pathlib import Path
from types import SimpleNamespace

from ai_commerce.remote.transport import RemoteTransportError
from ai_commerce.remote.worker import LocalBatchWorker


BASE = "a" * 40


def _action():
    return {
        "action_id": "fault-probe",
        "attempt_id": "m0r-fault-probe-001",
        "executor": "codex",
        "model": "gpt-6.1-sol",
        "effort": "max",
        "instruction": "fixture only: must never reach a real model",
        "allowed_paths": ["tests/test_ef02_m0r_fixture.py"],
        "verification_profile": "ef02-tests",
        "depends_on": [],
    }


@pytest.fixture(autouse=True)
def _fake_worktree_checkout_is_clean(monkeypatch):
    """Fault-injection tests mock 'git worktree add' without creating .git.

    Keep checkout-preflight positive in these tests so each test can reach
    its unrelated claim, pre-spawn or untrusted-receipt failure boundary.
    The real worktree and truly dirty source cases are covered separately.
    """
    monkeypatch.setattr(
        "ai_commerce.remote.worker.checkout_diagnostics",
        lambda *_a, **_kw: {
            "clean": True, "dirty_entries": [], "dirty_entry_count": 0,
            "index_refresh_exit": None, "metadata_only_reconciled": False,
        },
    )


def _worker(tmp_path: Path, github):
    worker = object.__new__(LocalBatchWorker)
    worker.checkout = tmp_path
    worker.root = tmp_path / "evidence"
    worker.root.mkdir()
    worker.batch = {"batch_id": "m0r-negative-fi", "source_sha": BASE}
    worker.profiles = {"ef02-tests": ["python", "-m", "pytest", "tests/test_ef02.py", "-q"]}
    worker.profiles["project-tests"] = ["python", "-m", "pytest", "tests", "-q"]
    worker.github = github
    if not hasattr(github, "pre_spawn_checkpoint"):
        # This fixture exercises unrelated faults; provide the fake positive
        # remote checkpoint explicitly rather than touching GitHub.
        github.pre_spawn_checkpoint = lambda **_kw: "refs/heads/ef02/pre-spawn/mock"
    if not hasattr(github, "spawned_checkpoint"):
        github.spawned_checkpoint = lambda **_kw: "refs/heads/ef02/spawned/mock"
    worker.approved_reuse = {"version": 1, "candidates": {}}
    return worker


def _persisted(worker):
    return json.loads(
        (worker.root / "fault-probe-m0r-fault-probe-001" / "action_result.json")
        .read_text(encoding="utf-8")
    )


def _forbid_model(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("PROVIDER_SUBPROCESS_MUST_NOT_START")
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen", fail)


def test_claim_transport_unavailable_prevents_any_model_or_candidate(tmp_path, monkeypatch):
    class NoClaim:
        def claim(self, **_kwargs):
            raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
        def integrated_source(self, **_kwargs):
            raise AssertionError("must not integrate after failed claim")
        def candidate(self, **_kwargs):
            raise AssertionError("must not publish after failed claim")
    _forbid_model(monkeypatch)
    worker = _worker(tmp_path, NoClaim())
    result = worker.execute_action(_action())
    assert result["status"] == "CLAIM_UNAVAILABLE"
    assert result["executor_invoked"] is False
    assert result["candidate_sha"] is None
    assert _persisted(worker)["status"] == "CLAIM_UNAVAILABLE"


def test_post_claim_integration_transport_failure_is_not_blindly_retried(tmp_path, monkeypatch):
    class ClaimedThenFailure:
        calls = 0
        def claim(self, **_kwargs):
            self.calls += 1
            return "CLAIMED" if self.calls == 1 else "DUPLICATE_CLAIM_NO_REEXECUTION"
        def integrated_source(self, **_kwargs):
            raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
        def candidate(self, **_kwargs):
            raise AssertionError("must not publish after failed integration")
    _forbid_model(monkeypatch)
    github = ClaimedThenFailure()
    worker = _worker(tmp_path, github)
    first = worker.execute_action(_action())
    assert first["claim_status"] == "CLAIMED"
    assert first["status"] == "INTEGRATION_FAILED"
    assert first["executor_invoked"] is False
    assert _persisted(worker)["status"] == "INTEGRATION_FAILED"
    second = worker.execute_action(_action())
    assert github.calls == 2
    assert second["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert second["executor_invoked"] is False
    assert second["candidate_sha"] is None


def test_worktree_checkout_failure_stops_before_model(tmp_path, monkeypatch):
    class Claimed:
        def claim(self, **_kwargs):
            return "CLAIMED"
        def integrated_source(self, **_kwargs):
            return {"effective_sha": BASE, "integration_ref": None}
        def candidate(self, **_kwargs):
            raise AssertionError("must not publish after failed checkout")
    def fake_run(argv, **_kwargs):
        assert argv[0] == "git" and "worktree" in argv and "add" in argv
        return SimpleNamespace(returncode=1, stderr="fixture git worktree failure")
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run", fake_run)
    _forbid_model(monkeypatch)
    worker = _worker(tmp_path, Claimed())
    result = worker.execute_action(_action())
    assert result["status"] == "SOURCE_WORKTREE_FAILED"
    assert result["error_code"] == "GIT_WORKTREE_ADD_NONZERO"
    assert result["executor_invoked"] is False
    assert _persisted(worker)["status"] == "SOURCE_WORKTREE_FAILED"


def test_killed_child_with_missing_receipt_is_unknown_and_not_replayed(tmp_path, monkeypatch):
    class FakeGithub:
        claims = 0
        def claim(self, **_kwargs):
            self.claims += 1
            return "CLAIMED" if self.claims == 1 else "DUPLICATE_CLAIM_NO_REEXECUTION"
        def integrated_source(self, **_kwargs):
            return {"effective_sha": BASE, "integration_ref": None}
        def candidate(self, **_kwargs):
            raise AssertionError("no verified child result, no candidate")
    class TimeoutChild:
        pid = 10123
        returncode = 1
        killed = False
        def __init__(self, argv, **_kwargs):
            self.argv = argv
            self.calls = 0
        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="fixture", timeout=timeout)
            return ("no receipt", "simulated child timeout")
        def kill(self):
            self.killed = True
    children = []
    def fake_popen(argv, **kwargs):
        child = TimeoutChild(argv, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen", fake_popen)
    github = FakeGithub()
    worker = _worker(tmp_path, github)
    result = worker.execute_action(_action())
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EF01_PROCESS_TIMEOUT"
    assert result["receipt_after_timeout"] is False
    assert result["executor_invoked"] is True
    assert result["candidate_sha"] is None
    assert len(children) == 1 and children[0].killed
    assert _persisted(worker)["status"] == "EXECUTION_UNKNOWN"
    repeated = worker.execute_action(_action())
    assert repeated["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert len(children) == 1


def test_timed_out_child_with_leftover_verified_receipt_cannot_publish(tmp_path, monkeypatch):
    """Even a success-shaped receipt cannot override observed child timeout."""
    class FakeGithub:
        claim_calls = 0
        candidate_calls = 0
        def claim(self, **_kwargs):
            self.claim_calls += 1
            return ("CLAIMED" if self.claim_calls == 1
                    else "DUPLICATE_CLAIM_NO_REEXECUTION")
        def integrated_source(self, **_kwargs):
            return {"effective_sha": BASE, "integration_ref": None}
        def candidate(self, **_kwargs):
            self.candidate_calls += 1
            raise AssertionError("stale success-shaped receipt must never publish")

    class TimedOutWithReceipt:
        pid = 10555
        # Adversarial status: some test/transport wrappers may report zero even
        # though communicate() timed out. The timeout must take precedence.
        returncode = 0
        def __init__(self, argv, **_kwargs):
            self.calls = 0
            self.out = Path(argv[argv.index("--out-dir") + 1])
            self.killed = False
        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                self.out.mkdir(parents=True, exist_ok=True)
                (self.out / "engineering_result.json").write_text(json.dumps({
                    "status": "BUILD_VERIFIED_FOR_SCOPE",
                    "source_base_sha": BASE,
                    "changed_files": ["tests/test_ef02_m0r_fixture.py"],
                    "verify_exit_code": 0,
                    "error_code": None,
                }), encoding="utf-8")
                raise subprocess.TimeoutExpired(cmd="fixture", timeout=timeout)
            return ("timeout stdout", "timeout stderr")
        def kill(self):
            self.killed = True

    children = []
    def fake_popen(argv, **kwargs):
        child = TimedOutWithReceipt(argv, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen", fake_popen)
    github = FakeGithub()
    worker = _worker(tmp_path, github)
    result = worker.execute_action(_action())
    assert len(children) == 1 and children[0].killed is True
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EF01_PROCESS_TIMEOUT"
    assert result["receipt_after_timeout"] is True
    assert result["executor_invoked"] is True
    assert result["candidate_sha"] is None
    assert github.candidate_calls == 0
    assert _persisted(worker)["status"] == "EXECUTION_UNKNOWN"

    # Existing immutable claim blocks a new provider invocation even though
    # a success-shaped stale receipt exists from the first attempt.
    repeated = worker.execute_action(_action())
    assert repeated["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert len(children) == 1
    assert github.candidate_calls == 0


def test_candidate_publication_failure_preserves_verified_result_but_not_pass(tmp_path, monkeypatch):
    class FakeGithub:
        candidate_calls = 0
        def claim(self, **_kwargs):
            return "CLAIMED"
        def integrated_source(self, **_kwargs):
            return {"effective_sha": BASE, "integration_ref": None}
        def candidate(self, **_kwargs):
            self.candidate_calls += 1
            raise RemoteTransportError("GITHUB_HTTP_403")
    class VerifiedChild:
        pid = 10124
        returncode = 0
        def __init__(self, argv, **_kwargs):
            self.argv = argv
        def communicate(self, timeout=None):
            out = Path(self.argv[self.argv.index("--out-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "engineering_result.json").write_text(json.dumps({
                "status": "BUILD_VERIFIED_FOR_SCOPE",
                "source_base_sha": BASE,
                "git_head_after": BASE, "scope_valid": True, "executor_exit_code": 0,
                "tracked_diff_sha256": "d" * 64,
                "changed_files": ["tests/test_ef02_m0r_fixture.py"],
                "candidate_file_sha256": {"tests/test_ef02_m0r_fixture.py": "0" * 64},
                "verify_exit_code": 0,
                "error_code": None,
            }), encoding="utf-8")
            return ("fixture stdout", "fixture stderr")
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen",
                        lambda argv, **kwargs: VerifiedChild(argv, **kwargs))
    monkeypatch.setattr("ai_commerce.remote.worker._snapshot", lambda *_a: ({
        "git_head_after": BASE, "scope_valid": True,
        "changed_files": ["tests/test_ef02_m0r_fixture.py"],
        "candidate_file_sha256": {"tests/test_ef02_m0r_fixture.py": "0" * 64},
        "tracked_diff_sha256": "d" * 64,
    }, b"mock diff"))
    github = FakeGithub()
    worker = _worker(tmp_path, github)
    result = worker.execute_action(_action())
    assert github.candidate_calls == 1
    assert result["status"] == "RESULT_PUBLICATION_FAILED"
    assert result["ef01_status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["verify_exit_code"] == 0
    assert result["executor_invoked"] is True
    assert result["candidate_sha"] is None
    assert _persisted(worker)["status"] == "RESULT_PUBLICATION_FAILED"


def test_issue_feedback_publication_failure_marks_batch_nonpass(tmp_path, monkeypatch):
    from ai_commerce.remote import __main__ as cli

    action = _action()
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "batch_id": "m0r-comment-failure", "source_sha": BASE,
        "actions": [action]
    }), encoding="utf-8")
    monkeypatch.setattr(cli, "verify_registry", lambda *_a: {"ef02-tests": ["python", "-m", "pytest", "tests/test_ef02.py", "-q"]})
    monkeypatch.setattr(cli, "load_approved_candidates", lambda *_a: {"version": 1, "candidates": {}})
    monkeypatch.setattr(cli, "validate_batch", lambda data, **_kw: data)
    monkeypatch.setattr(cli, "check_source", lambda *_a: None)
    class FakeWorker:
        def __init__(self, **_kwargs):
            self.root = tmp_path / "batch-output"
            self.root.mkdir()
        def execute_action(self, item):
            return {"action_id": item["action_id"], "attempt_id": item["attempt_id"],
                    "status": "CANDIDATE_READY", "executor_invoked": False}
    class FailingComment:
        def __init__(self, *_a, **_kw):
            pass
        def comment(self, *_a, **_kw):
            raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
    monkeypatch.setattr(cli, "LocalBatchWorker", FakeWorker)
    monkeypatch.setattr(cli, "GitHubTransport", FailingComment)
    monkeypatch.setenv("GITHUB_REPOSITORY", "trung-via/ai-commerce")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-fixture-only")
    exit_code = cli.main([
        "run", "--request-file", str(request), "--checkout", str(tmp_path),
        "--issuer-login", "trung-via", "--owner-login", "trung-via",
        "--issue-number", "13",
    ])
    record = json.loads((tmp_path / "batch-output" / "batch_result.json").read_text())
    assert exit_code == 1
    assert record["status"] == "COMPLETE"  # actions finished, delivery failed
    assert record["feedback_status"] == "FEEDBACK_PUBLICATION_FAILED"
    assert record["feedback_error_code"] == "RemoteTransportError"



def _journal(worker, name):
    p = worker.root / "fault-probe-m0r-fault-probe-001" / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_parent_interrupted_at_popen_preserves_pre_spawn_unknown_without_replay(
    tmp_path, monkeypatch,
):
    """A BaseException from the process boundary mimics abrupt parent loss.

    This is deterministic fault injection, not a live runner-service crash.
    """
    class Claimed:
        claim_calls = 0
        def claim(self, **_kwargs):
            self.claim_calls += 1
            return "CLAIMED" if self.claim_calls == 1 else "DUPLICATE_CLAIM_NO_REEXECUTION"
        def integrated_source(self, **_kwargs):
            return {"effective_sha": BASE, "integration_ref": None}
        def candidate(self, **_kwargs):
            raise AssertionError("NO_PROVIDER_RESULT_NO_CANDIDATE")

    def interrupted_popen(*_a, **_kw):
        raise SystemExit("SIMULATED_PARENT_EXIT_AT_SPAWN_BOUNDARY")

    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen", interrupted_popen)
    github = Claimed()
    worker = _worker(tmp_path, github)
    with pytest.raises(SystemExit, match="SIMULATED_PARENT_EXIT_AT_SPAWN_BOUNDARY"):
        worker.execute_action(_action())

    boundary = _journal(worker, "invocation_boundary.json")
    assert boundary["stage"] == "BEFORE_EF01_CHILD_SPAWN"
    assert boundary["status"] == "EXECUTION_UNKNOWN"
    assert boundary["claim_status"] == "CLAIMED"
    assert boundary["ef01_child_pid_observed"] is False
    assert boundary["provider_invocation_proven"] is False
    assert boundary["replay_authorized"] is False
    evidence = worker.root / "fault-probe-m0r-fault-probe-001"
    assert not (evidence / "invocation_spawned.json").exists()
    assert not (evidence / "action_result.json").exists()
    assert worker.execute_action(_action())["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert github.claim_calls == 2


def test_parent_interrupted_after_ef01_child_spawn_preserves_observed_pid_unknown(
    tmp_path, monkeypatch,
):
    """EF01 PID observed does not establish provider invocation or its result."""
    class Claimed:
        claim_calls = 0
        def claim(self, **_kwargs):
            self.claim_calls += 1
            return "CLAIMED" if self.claim_calls == 1 else "DUPLICATE_CLAIM_NO_REEXECUTION"
        def integrated_source(self, **_kwargs):
            return {"effective_sha": BASE, "integration_ref": None}
        def candidate(self, **_kwargs):
            raise AssertionError("INTERRUPTED_CHILD_CANNOT_PUBLISH")

    class InterruptedChild:
        pid = 11223
        returncode = None
        def __init__(self, _argv, **_kwargs):
            pass
        def communicate(self, timeout=None):
            raise SystemExit("SIMULATED_PARENT_EXIT_AFTER_SPAWN")

    spawn_count = 0
    def fake_spawn(argv, **kwargs):
        nonlocal spawn_count
        spawn_count += 1
        return InterruptedChild(argv, **kwargs)

    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen", fake_spawn)
    github = Claimed()
    worker = _worker(tmp_path, github)
    with pytest.raises(SystemExit, match="SIMULATED_PARENT_EXIT_AFTER_SPAWN"):
        worker.execute_action(_action())

    boundary = _journal(worker, "invocation_boundary.json")
    observed = _journal(worker, "invocation_spawned.json")
    assert boundary["status"] == observed["status"] == "EXECUTION_UNKNOWN"
    assert boundary["ef01_child_pid_observed"] is False
    assert observed["stage"] == "EF01_CHILD_PID_OBSERVED"
    assert observed["ef01_child_pid_observed"] is True
    assert observed["process_id"] == 11223
    assert observed["provider_invocation_proven"] is False
    assert observed["replay_authorized"] is False
    assert not (worker.root / "fault-probe-m0r-fault-probe-001" / "action_result.json").exists()
    assert worker.execute_action(_action())["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert spawn_count == 1
    assert github.claim_calls == 2


def test_remote_checkpoint_transport_failure_stops_before_model(tmp_path, monkeypatch):
    class CheckpointUnavailable:
        calls = 0
        def claim(self, **_kw):
            self.calls += 1
            return "CLAIMED" if self.calls == 1 else "DUPLICATE_CLAIM_NO_REEXECUTION"
        def integrated_source(self, **_kw):
            return {"effective_sha": BASE, "integration_ref": None}
        def pre_spawn_checkpoint(self, **_kw):
            raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
        def candidate(self, **_kw):
            raise AssertionError("model never ran")
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    _forbid_model(monkeypatch)
    github = CheckpointUnavailable()
    worker = _worker(tmp_path, github)
    first = worker.execute_action(_action())
    assert first["status"] == "PRESPAWN_CHECKPOINT_UNCONFIRMED"
    assert first["error_code"] == "PRESPAWN_CHECKPOINT_UNCONFIRMED"
    assert first["executor_invoked"] is False
    assert first["replay_authorized"] is False
    assert first["candidate_ref"] is None
    assert _journal(worker, "invocation_boundary.json")["status"] == "EXECUTION_UNKNOWN"
    assert not (worker.root / "fault-probe-m0r-fault-probe-001" /
                "invocation_spawned.json").exists()
    repeat = worker.execute_action(_action())
    assert repeat["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert github.calls == 2


def test_confirmed_remote_checkpoint_precedes_ef01_popen(tmp_path, monkeypatch):
    class ConfirmedCheckpoint:
        def claim(self, **_kw):
            return "CLAIMED"
        def integrated_source(self, **_kw):
            return {"effective_sha": BASE, "integration_ref": None}
        def pre_spawn_checkpoint(self, **_kw):
            assert _journal(worker, "invocation_boundary.json")["status"] == "EXECUTION_UNKNOWN"
            events.append("remote")
            return "refs/heads/ef02/pre-spawn/test-verified"
        def candidate(self, **_kw):
            raise AssertionError("interrupted before publication")
    class Child:
        pid = 8765
        def communicate(self, timeout=None):
            raise SystemExit("SIMULATED_PARENT_INTERRUPTED")
    events = []
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    def popen(*_a, **_kw):
        events.append("popen")
        assert _journal(worker, "invocation_remote_confirmed.json")["stage"] == "PRESPAWN_REMOTE_REF_CONFIRMED"
        return Child()
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen", popen)
    worker = _worker(tmp_path, ConfirmedCheckpoint())
    with pytest.raises(SystemExit, match="SIMULATED_PARENT_INTERRUPTED"):
        worker.execute_action(_action())
    assert events == ["remote", "popen"]
    assert _journal(worker, "invocation_remote_confirmed.json")["pre_spawn_remote_ref"] == "refs/heads/ef02/pre-spawn/test-verified"
    assert _journal(worker, "invocation_spawned.json")["process_id"] == 8765


def test_ef01_cli_timeout_receipt_remains_unknown_in_ef02_and_never_publishes(
    tmp_path, monkeypatch,
):
    """A completed EF01 wrapper can still report ambiguous provider timeout."""
    class Claimed:
        def claim(self, **_kw):
            return "CLAIMED"
        def integrated_source(self, **_kw):
            return {"effective_sha": BASE, "integration_ref": None}
        def pre_spawn_checkpoint(self, **_kw):
            return "refs/heads/ef02/pre-spawn/mock"
        def candidate(self, **_kw):
            raise AssertionError("UNKNOWN_TIMEOUT_MUST_NOT_PUBLISH")
    class Child:
        pid = 18888
        returncode = 1
        def __init__(self, argv, **_kwargs):
            self.out = Path(argv[argv.index("--out-dir") + 1])
        def communicate(self, timeout=None):
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "engineering_result.json").write_text(json.dumps({
                "status": "EXECUTION_UNKNOWN",
                "error_code": "EXECUTOR_TIMEOUT",
                "source_base_sha": BASE,
                "changed_files": ["tests/test_ef02_m0r_fixture.py"],
                "verify_exit_code": None,
                "replay_authorized": False,
            }), encoding="utf-8")
            return ("fixture CLI returned", "")
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen",
                        lambda argv, **kwargs: Child(argv, **kwargs))
    worker = _worker(tmp_path, Claimed())
    result = worker.execute_action(_action())
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EXECUTOR_TIMEOUT"
    assert result["ef01_status"] == "EXECUTION_UNKNOWN"
    assert result["executor_invoked"] is True
    assert result["verify_exit_code"] is None
    assert result["candidate_ref"] is None
    assert result["candidate_sha"] is None
    assert _persisted(worker)["status"] == "EXECUTION_UNKNOWN"


@pytest.mark.parametrize(
    "receipt_status,process_exit,verify_exit,expected_status,expected_error",
    [
        ("BUILD_VERIFIED_FOR_SCOPE", 1, 0, "EXECUTION_UNKNOWN",
         "EF01_EXIT_NONZERO_WITH_SUCCESS_RECEIPT"),
        ("BUILD_VERIFIED_FOR_SCOPE", 0, 1, "VERIFICATION_FAILED",
         "EF01_VERIFY_EXIT_CONTRADICTS_SUCCESS_RECEIPT"),
        ("BUILD_VERIFIED_FOR_SCOPE", 0, None, "VERIFICATION_FAILED",
         "EF01_VERIFY_EXIT_CONTRADICTS_SUCCESS_RECEIPT"),
        ("CANDIDATE_READY", 0, 0, "EXECUTION_UNKNOWN",
         "EF01_NONPASS_STATUS_INVALID"),
    ],
)
def test_ef01_untrusted_success_receipt_never_promotes_batch_action_to_pass(
    tmp_path, monkeypatch, receipt_status, process_exit, verify_exit,
    expected_status, expected_error,
):
    """Adversarial EF01 receipts cannot override observed process/verify evidence.

    No provider invocation or GitHub API call is performed.
    """
    class FakeGitHub:
        candidate_calls = 0

        def claim(self, **_kwargs):
            return "CLAIMED"

        def integrated_source(self, **_kwargs):
            return {"effective_sha": BASE, "integration_ref": None}

        def candidate(self, **_kwargs):
            self.candidate_calls += 1
            raise AssertionError("UNTRUSTED_RECEIPT_MUST_NOT_PUBLISH")

    class ReceiptChild:
        pid = 20401
        returncode = process_exit

        def __init__(self, argv, **_kwargs):
            self.out = Path(argv[argv.index("--out-dir") + 1])

        def communicate(self, timeout=None):
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "engineering_result.json").write_text(json.dumps({
                "status": receipt_status,
                "source_base_sha": BASE,
                "changed_files": ["tests/test_ef02_m0r_fixture.py"],
                "candidate_file_sha256": {
                    "tests/test_ef02_m0r_fixture.py": "0" * 64},
                "verify_exit_code": verify_exit,
                "error_code": None,
            }), encoding="utf-8")
            return ("fixture-only stdout", "fixture-only stderr")

    starts = []
    def fake_popen(argv, **kwargs):
        starts.append(argv)
        return ReceiptChild(argv, **kwargs)

    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_kw: SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.Popen", fake_popen)
    gh = FakeGitHub()
    worker = _worker(tmp_path, gh)
    result = worker.execute_action(_action())
    assert len(starts) == 1
    assert result["ef01_status"] == receipt_status
    assert result["ef01_exit"] == process_exit
    assert result["verify_exit_code"] == verify_exit
    assert result["status"] == expected_status
    assert result["error_code"] == expected_error
    assert result["receipt_status_untrusted"] is True
    assert result["candidate_ref"] is None
    assert result["candidate_sha"] is None
    assert gh.candidate_calls == 0
    assert _persisted(worker)["status"] == expected_status
