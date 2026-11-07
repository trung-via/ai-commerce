"""M0-R deterministic negative-path fault injections: never call real providers.

These tests model faults at EF02 boundary methods on an isolated worker; they
do not assert that a live Windows runner was taken offline or cancelled.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest
from pathlib import Path
from types import SimpleNamespace

from ai_commerce.remote.transport import RemoteTransportError
from ai_commerce.remote.worker import LocalBatchWorker


BASE = "a" * 40


def _workowner_fixture():
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)
    action = _action()
    packet = {"batch_id": "durable-fixture", "source_sha": BASE, "actions": [action]}
    storage.issue_body = packet
    identity = WorkOwner.identity(storage, 506, packet, action)
    return storage, identity, WorkOwner


def test_workowner_orderly_journal_rounds_and_read_only_restart_without_workspace():
    storage, identity, owner_type = _workowner_fixture()
    owner = owner_type.acquire(storage, identity)
    owner.append("CLAIMED")
    owner.append("SPAWN_INTENT")
    owner.append("CHILD_OBSERVED", pid=73)
    owner.append("PROVIDER_INTENT", round=0)
    owner.append("ROUND_VERIFIED", round=0, receipt="a" * 64)
    owner.append("PROVIDER_INTENT", round=1)
    owner.append("ROUND_VERIFIED", round=1, receipt="b" * 64)
    assert len(owner.read()) == 8
    storage.requests.clear()
    restarted = owner_type(storage, identity, "")
    result = restarted.reconcile()
    assert result["stage"] == "ROUND_VERIFIED" and not result["replay_authorized"]
    assert result["status"] == "HOLD_UNKNOWN_EFFECT" and not result["publication_authorized"]
    assert all(method == "GET" for method, *_ in storage.requests)
    with pytest.raises(RemoteTransportError):
        restarted.append("PROVIDER_INTENT", round=2)


def test_workowner_simultaneous_contenders_have_one_authoritative_generation():
    from concurrent.futures import ThreadPoolExecutor
    storage, identity, owner_type = _workowner_fixture()
    def claim():
        try:
            return owner_type.acquire(storage, identity)
        except RemoteTransportError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        owners = list(pool.map(lambda _: claim(), range(8)))
    assert sum(owner is not None for owner in owners) == 1
    assert len(storage.refs) == 1
    assert next(owner for owner in owners if owner).read()[0]["generation"] == 1


@pytest.mark.parametrize("stage", ["OWNED", "CLAIMED", "SPAWN_INTENT", "CHILD_OBSERVED", "PROVIDER_INTENT", "UNKNOWN"])
def test_workowner_crash_at_each_boundary_never_reclaims_or_dispatches(stage):
    storage, identity, owner_type = _workowner_fixture()
    owner = owner_type.acquire(storage, identity)
    for next_stage, data in [("CLAIMED", {}), ("SPAWN_INTENT", {}),
                             ("CHILD_OBSERVED", {"pid": 73}),
                             ("PROVIDER_INTENT", {"round": 0}), ("UNKNOWN", {})]:
        if owner.read()[-1]["stage"] == stage:
            break
        owner.append(next_stage, **data)
    before = dict(storage.refs)
    assert owner_type(storage, identity, "").reconcile()["status"] == "HOLD_UNKNOWN_EFFECT"
    with pytest.raises(RemoteTransportError):
        owner_type.acquire(storage, identity)
    assert storage.refs == before


@pytest.mark.parametrize("fault", ["parent", "digest", "sequence", "source", "attempt", "repo", "fence", "tree", "gap", "truncate"])
def test_workowner_corrupt_reordered_hijacked_or_rolled_back_journal_fails_closed(fault):
    storage, identity, owner_type = _workowner_fixture()
    owner = owner_type.acquire(storage, identity)
    owner.append("CLAIMED")
    last_ref = sorted(storage.refs)[-1]
    commit = storage.commits[storage.refs[last_ref]]
    record = json.loads(commit["message"][len(owner_type.MARKER):])
    if fault == "parent":
        commit["parents"] = [{"sha": BASE}]
    elif fault == "tree":
        commit["tree"]["sha"] = "f" * 40
    elif fault == "gap":
        storage.refs[last_ref[:-3] + "002"] = storage.refs.pop(last_ref)
    elif fault == "truncate":
        del storage.refs[last_ref]
    else:
        if fault in ("source", "attempt", "repo"):
            record["identity"][{"source": "source_sha", "attempt": "attempt_id", "repo": "repo"}[fault]] = "wrong"
        elif fault == "sequence":
            record["sequence"] = 0
        else:
            record[fault] = "0" * 64
        # A recomputed digest cannot hide a wrong identity, fence or sequence.
        if fault != "digest":
            record.pop("digest")
            record["digest"] = owner_type.digest(record)
        commit["message"] = owner_type.MARKER + json.dumps(record)
    requests = len(storage.requests)
    with pytest.raises(RemoteTransportError):
        owner.append("SPAWN_INTENT")
    assert all(method == "GET" for method, *_ in storage.requests[requests:])


@pytest.mark.parametrize("phase", ["read", "create-before", "create-after"])
def test_workowner_transport_unknown_has_no_second_write_or_claim(phase):
    storage, identity, owner_type = _workowner_fixture()
    owner = owner_type.acquire(storage, identity)
    api = storage.api
    def fault(method, path, data=None):
        if phase == "read" and method == "GET":
            raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
        if method == "POST" and path == "/git/refs":
            if phase == "create-after":
                api(method, path, data)
            raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
        return api(method, path, data)
    storage.api = fault
    with pytest.raises(RemoteTransportError):
        owner.append("CLAIMED")
    storage.api = api
    before = len(storage.requests)
    with pytest.raises(RemoteTransportError):
        owner.append("CLAIMED")
    assert all(method == "GET" for method, *_ in storage.requests[before:])
    assert not owner.reconcile()["replay_authorized"]


def test_workowner_stale_owner_duplicate_provider_and_receipt_mismatch_deny_effects():
    storage, identity, owner_type = _workowner_fixture()
    owner = owner_type.acquire(storage, identity)
    stale = owner_type(storage, identity, "untrusted-holder")
    with pytest.raises(RemoteTransportError, match="STALE_FENCE"):
        stale.append("CLAIMED")
    owner.append("CLAIMED")
    owner.append("SPAWN_INTENT")
    owner.append("PROVIDER_INTENT", round=0)
    with pytest.raises(RemoteTransportError):
        owner.append("PROVIDER_INTENT", round=0)
    assert owner.reconcile()["stage"] == "PROVIDER_INTENT"
    with pytest.raises(RemoteTransportError):
        owner.append("ROUND_VERIFIED", round=1, receipt="e" * 64)


def test_workowner_cross_batch_attempt_cannot_claim_existing_work_and_prose_is_not_lineage():
    storage, identity, owner_type = _workowner_fixture()
    owner_type.acquire(storage, identity)
    packet = storage.issue_body
    packet["batch_id"] = "successor"
    packet["actions"][0]["attempt_id"] = "new-attempt"
    packet["actions"][0]["instruction"] = "repair of #466; generation 2 authorized (untrusted prose)"
    successor = owner_type.identity(storage, 506, packet, packet["actions"][0])
    assert successor["work_key"] == identity["work_key"]
    with pytest.raises(RemoteTransportError):
        owner_type.acquire(storage, successor)
    with pytest.raises(RemoteTransportError, match="TRUSTED_ISSUE_REQUIRED"):
        owner_type.identity(storage, 0, packet, packet["actions"][0])


def test_workowner_ingress_mismatch_rejected_before_remote_write():
    storage, identity, owner_type = _workowner_fixture()
    packet = {**storage.issue_body, "source_sha": "b" * 40}
    with pytest.raises(RemoteTransportError, match="INGRESS_MISMATCH"):
        owner_type.identity(storage, 506, packet, packet["actions"][0])
    assert not any(method == "POST" for method, *_ in storage.requests)


@pytest.mark.parametrize("namespace", ["claims", "pre-spawn", "spawned", "candidate"])
def test_workowner_partial_legacy_boundaries_require_reconciliation(namespace):
    storage, identity, owner_type = _workowner_fixture()
    storage.refs["refs/heads/ef02/" + namespace + "/" + storage.attempt_key(identity["attempt_id"])] = BASE
    with pytest.raises(RemoteTransportError, match="LEGACY_BOUNDARY_RECONCILE_ONLY"):
        owner_type.acquire(storage, identity)
    assert not any(method == "POST" for method, *_ in storage.requests)


def _publishable_workowner(tmp_path, monkeypatch):
    """Real candidate bytes + modeled Git snapshot; no provider or Git writes."""
    from ai_commerce.remote import transport
    storage, identity, owner_type = _workowner_fixture()
    owner = owner_type.acquire(storage, identity)
    relative = _action()["allowed_paths"][0]
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x = 1\n")
    hashes = {relative: owner_type.digest("verified-file")}
    import hashlib
    hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    proof = {"version": 1, "status": "CANDIDATE_READY", "review_required": False, "scope_valid": True,
             "out_of_scope_files": [], "changed_files": [relative], "allowed_paths": [relative],
             "candidate_file_sha256": hashes, "tracked_diff_sha256": "b" * 64,
             "source_sha": BASE, "effective_source_sha": BASE,
             "action_id": identity["action_id"], "attempt_id": identity["attempt_id"],
             "fingerprint": identity["fingerprint"],
             "verification": {"status": "BUILD_VERIFIED_FOR_SCOPE", "exit_code": 0,
                "profile": "ef02-tests", "argv": ["python", "-m", "pytest", "tests/test_ef02.py", "-q", "-n", "12"],
                "evidence_sha256": {"tracked_diff": "b" * 64}, "report_sha256": "c" * 64},
             "provenance": {"executor": "codex", "model": "fixture", "effort": "high",
                "dependency_candidates": [], "run_id": None, "run_attempt": None}}
    monkeypatch.setattr(transport, "assert_candidate_git_filter_consistency", lambda *_a: None)
    monkeypatch.setattr(transport, "_snapshot", lambda *_a: ({
        "git_head_after": BASE, "changed_files": [relative], "scope_valid": True,
        "candidate_file_sha256": hashes, "tracked_diff_sha256": "b" * 64}, b""))
    for stage, data in [("CLAIMED", {}), ("SPAWN_INTENT", {}), ("CHILD_OBSERVED", {"pid": 7}),
                        ("PROVIDER_INTENT", {"round": 0}),
                        ("ROUND_VERIFIED", {"round": 0, "receipt": "d" * 64}),
                        ("RESULT_VERIFIED", {"receipt": transport.evidence_digest(proof)}),
                        ("PUBLISH_INTENT", {"receipt": transport.evidence_digest(proof)})]:
        owner.append(stage, **data)
    arguments = {"source_sha": BASE, "attempt_id": identity["attempt_id"], "workspace": tmp_path,
                 "paths": [relative], "expected_hashes": hashes, "evidence": proof, "owner": owner}
    return storage, owner, arguments


@pytest.mark.parametrize("terminal", [False, True])
def test_workowner_verified_terminal_and_lost_publish_response_reconcile_get_only(tmp_path, monkeypatch, terminal):
    from ai_commerce.remote.transport import WorkOwner
    from ai_commerce.remote.candidate_policy import evidence_digest
    storage, owner, arguments = _publishable_workowner(tmp_path, monkeypatch)
    candidate = storage.candidate(**arguments)
    if terminal:
        owner.append("PUBLISHED", receipt=evidence_digest(arguments["evidence"]),
                     candidate=WorkOwner.digest(candidate["candidate_sha"]))
    storage.requests.clear()
    result = WorkOwner(storage, owner.identity, "").reconcile()
    assert result["status"] == "RECONCILED_VERIFIED_TERMINAL"
    assert result["candidate_sha"] == candidate["candidate_sha"]
    assert not result["replay_authorized"] and not result["publication_authorized"]
    assert all(method == "GET" for method, *_ in storage.requests)


def test_workowner_lost_generation_during_candidate_preparation_never_publishes(tmp_path, monkeypatch):
    storage, owner, arguments = _publishable_workowner(tmp_path, monkeypatch)
    api = storage.api
    def lost_fence(method, path, data=None):
        response = api(method, path, data)
        if method == "POST" and path == "/git/trees":
            owner.uncertain = True
        return response
    storage.api = lost_fence
    with pytest.raises(RemoteTransportError, match="STALE_FENCE"):
        storage.candidate(**arguments)
    assert not any("/candidate/" in ref for ref in storage.refs)


def test_workowner_candidate_publication_race_is_create_only(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    storage, owner, arguments = _publishable_workowner(tmp_path, monkeypatch)
    def publish():
        try:
            return storage.candidate(**arguments)
        except RemoteTransportError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert sum(r is not None for r in results) == 1
    assert len([ref for ref in storage.refs if "/candidate/" in ref]) == 1
    assert not any(method in ("PATCH", "PUT", "DELETE") for method, *_ in storage.requests)


def test_workowner_mismatched_terminal_candidate_receipt_is_rejected(tmp_path, monkeypatch):
    from ai_commerce.remote.transport import WorkOwner
    storage, owner, arguments = _publishable_workowner(tmp_path, monkeypatch)
    candidate = storage.candidate(**arguments)
    storage.refs[candidate["candidate_ref"]] = BASE
    before = len(storage.requests)
    with pytest.raises((RemoteTransportError, ValueError)):
        WorkOwner(storage, owner.identity, "").reconcile()
    assert all(method == "GET" for method, *_ in storage.requests[before:])


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
    from test_ef01_repair import DirectWorkOwnerBroker
    monkeypatch.setattr("ai_commerce.remote.worker.WorkOwnerBroker", DirectWorkOwnerBroker)
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
    from test_ef01_repair import memory_owner, WorkOwnerMemoryTransport
    worker = object.__new__(LocalBatchWorker)
    worker.checkout = tmp_path
    worker.root = tmp_path / "evidence"
    worker.root.mkdir()
    worker.batch = {"batch_id": "m0r-negative-fi", "source_sha": BASE}
    worker.profiles = {"ef02-tests": ["python", "-m", "pytest", "tests/test_ef02.py", "-q"]}
    worker.profiles["project-tests"] = ["python", "-m", "pytest", "tests", "-q"]
    worker.github = github
    storage = WorkOwnerMemoryTransport(BASE)
    worker._acquire_owner = lambda action: memory_owner(action, worker.batch, storage)
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
    assert github.calls == 1  # WorkOwner refuses a second distributed claim.
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
    # Isolate the publication boundary; proof validation has its own tamper tests.
    monkeypatch.setattr("ai_commerce.remote.worker._verified_receipt_evidence", lambda *a, **k: True)
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
            inv = {"work_id": "m0r-negative-fi:fault-probe", "attempt_id": _action()["attempt_id"],
                   "invocation_id": _action()["attempt_id"] + ":invocation-000", "repair_round": 0,
                   "source_base_sha": BASE, "provider_return_determinate": True, "verify_completed": True}
            self.owner_fence("PROVIDER_INTENT", inv)
            self.owner_fence("ROUND_VERIFIED", inv)
            out = Path(self.argv[self.argv.index("--out-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "engineering_result.json").write_text(json.dumps({
                "status": "BUILD_VERIFIED_FOR_SCOPE",
                "source_base_sha": BASE,
                "git_head_after": BASE, "scope_valid": True, "executor_exit_code": 0,
                "tracked_diff_sha256": "d" * 64,
                "evidence_sha256": {"tracked_diff": "d" * 64},
                "changed_files": ["tests/test_ef02_m0r_fixture.py"],
                "candidate_file_sha256": {"tests/test_ef02_m0r_fixture.py": "0" * 64},
                "verify_exit_code": 0,
                "error_code": None,
                "invocations": [inv],
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
    assert github.claim_calls == 1


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
    assert github.claim_calls == 1


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
    assert github.calls == 1


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


def test_windows_fresh_subprocess_restart_readonly_recovery(tmp_path):
    """Deterministic real fresh subprocess restart-readonly recovery on Windows.

    Reconstructs owner from source-independent durable GitHub-like state in a real
    fresh Python subprocess. Asserts no duplicate invocation or publication.
    Differentiates fresh-process proof from actual Windows reboot; explicitly records
    gap honestly.
    """
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)
    action = _action()
    packet = {"batch_id": "restart-obs", "source_sha": BASE, "actions": [action]}
    storage.issue_body = packet
    identity = WorkOwner.identity(storage, 506, packet, action)
    owner = WorkOwner.acquire(storage, identity)
    owner.append("CLAIMED")
    owner.append("SPAWN_INTENT")
    owner.append("CHILD_OBSERVED", pid=1024)
    owner.append("PROVIDER_INTENT", round=0)
    owner.append("ROUND_VERIFIED", round=0, receipt="a" * 64)
    owner.append("UNKNOWN")

    state_file = tmp_path / "github_state.json"
    state = {
        "repo": storage.repo,
        "source": storage.source,
        "refs": storage.refs,
        "commits": storage.commits,
        "issue_body": storage.issue_body,
        "identity": identity,
    }
    state_file.write_text(json.dumps(state), encoding="utf-8")

    script = """
import sys, json, os
from pathlib import Path
from test_ef01_repair import WorkOwnerMemoryTransport
from ai_commerce.remote.transport import WorkOwner

state_path = Path(sys.argv[1])
state = json.loads(state_path.read_text(encoding="utf-8"))

storage = WorkOwnerMemoryTransport(state["source"])
storage.refs = state["refs"]
storage.commits = state["commits"]
storage.issue_body = state["issue_body"]
storage.requests.clear()

reconstructed = WorkOwner(storage, state["identity"], "")
reconciled = reconstructed.reconcile()

get_only = all(method == "GET" for method, *_ in storage.requests)
post_count = sum(1 for method, *_ in storage.requests if method == "POST")

output = {
    "reconciled": reconciled,
    "get_only": get_only,
    "post_count": post_count,
    "process_pid": os.getpid(),
    "fresh_process_proven": True,
    "windows_reboot_gap": "FRESH_SUBPROCESS_CONFIRMED_HOSTOPS_REBOOT_NOT_GRANTED",
}
print(json.dumps(output))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + str(Path(__file__).resolve().parent)
    for k in list(env):
        if k.startswith("GIT_CONFIG_"):
            env.pop(k, None)

    proc = subprocess.run(
        [sys.executable, "-c", script, str(state_file)],
        capture_output=True, text=True, check=True, env=env
    )
    result = json.loads(proc.stdout)
    assert result["fresh_process_proven"] is True
    assert result["get_only"] is True
    assert result["post_count"] == 0
    assert result["reconciled"]["stage"] == "UNKNOWN"
    assert result["reconciled"]["replay_authorized"] is False
    assert result["reconciled"]["publication_authorized"] is False
    assert result["windows_reboot_gap"] == "FRESH_SUBPROCESS_CONFIRMED_HOSTOPS_REBOOT_NOT_GRANTED"
    assert result["process_pid"] != os.getpid()


def test_workowner_lineage_cross_issue_hijack_or_forged_repair_of():
    """Negative tests: cross-issue hijack, non-existent issue, forged claim fingerprint, cycle."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    action = _action()
    action["repair_of"] = "#9999"
    packet = {"batch_id": "hijack-probe", "source_sha": BASE, "actions": [action]}
    storage.issues[508] = packet
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID"):
        WorkOwner.identity(storage, 508, packet, action)

    pred_action = {**_action(), "action_id": "other-action", "attempt_id": "other-attempt"}
    pred_packet = {"batch_id": "other-batch", "source_sha": BASE, "actions": [pred_action]}
    storage.issues[506] = pred_packet
    action["repair_of"] = "#506:target-attempt"
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_ACTION_NOT_FOUND"):
        WorkOwner.identity(storage, 508, packet, action)

    pred_action2 = {**_action(), "action_id": "target-action", "attempt_id": "target-attempt"}
    pred_packet2 = {"batch_id": "pred-batch", "source_sha": BASE, "actions": [pred_action2]}
    storage.issues[506] = pred_packet2
    action["repair_of"] = "#506"
    action["action_id"] = "target-action"
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_CLAIM_MISSING"):
        WorkOwner.identity(storage, 508, packet, action)

    action_in_506 = {**pred_action2, "repair_of": "#508"}
    storage.issues[506] = {**pred_packet2, "actions": [action_in_506]}
    storage.issues[508] = {**packet, "actions": [{**action, "repair_of": "#506"}]}
    fp506 = hashlib.sha256(json.dumps({"batch_id": pred_packet2["batch_id"], "source_sha": pred_packet2["source_sha"],
        "action": action_in_506}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    claim_key = storage.attempt_key("target-attempt")
    claim_sha = hashlib.sha1(b"claim506").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key}"] = claim_sha
    storage.commits[claim_sha] = {"message": f"EF02 CLAIM\n\nfingerprint={fp506}\nattempt_id=target-attempt", "parents": [{"sha": BASE}]}
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_CYCLE_DETECTED"):
        WorkOwner.identity(storage, 508, storage.issues[508], storage.issues[508]["actions"][0])


def _make_valid_proof(action_id, attempt_id, source_sha, fingerprint):
    diff_hash = "2" * 64
    return {
        "version": 1,
        "status": "CANDIDATE_READY",
        "scope_valid": True,
        "review_required": False,
        "out_of_scope_files": [],
        "changed_files": ["tests/test_ef02.py"],
        "allowed_paths": ["tests/test_ef02.py"],
        "candidate_file_sha256": {"tests/test_ef02.py": "1" * 64},
        "tracked_diff_sha256": diff_hash,
        "source_sha": source_sha,
        "effective_source_sha": source_sha,
        "action_id": action_id,
        "attempt_id": attempt_id,
        "fingerprint": fingerprint,
        "verification": {
            "status": "BUILD_VERIFIED_FOR_SCOPE",
            "exit_code": 0,
            "profile": "project-tests",
            "argv": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
            "evidence_sha256": {"tracked_diff": diff_hash},
            "report_sha256": "e" * 64,
        },
        "provenance": {
            "executor": "antigravity",
            "model": "gemini-3.8-flash",
            "effort": "high",
            "dependency_candidates": [],
            "run_id": None,
            "run_attempt": None,
        },
    }


def _publish_owner(storage, owner, action, source_sha=BASE, attempt_id=None):
    from ai_commerce.remote.transport import WorkOwner
    from ai_commerce.remote.candidate_policy import canonical, evidence_digest, EVIDENCE_MARKER
    attempt_id = attempt_id or action["attempt_id"]
    issue_data = None
    if hasattr(owner, "identity") and isinstance(owner.identity, dict):
        issue_num = owner.identity.get("current_issue") or owner.identity.get("issue")
        if issue_num and isinstance(storage.issues, dict):
            issue_data = storage.issues.get(issue_num)
    if issue_data is None and isinstance(storage.issue_body, dict):
        issue_data = storage.issue_body
    batch_id = issue_data.get("batch_id", "b1") if isinstance(issue_data, dict) else "b1"
    fp = hashlib.sha256(json.dumps({"batch_id": batch_id,
        "source_sha": source_sha, "action": {k: v for k, v in action.items() if k != "resolved_dependencies"}},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    claim_key = storage.attempt_key(attempt_id)
    claim_sha = hashlib.sha1((f"claim-{attempt_id}").encode()).hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key}"] = claim_sha
    storage.commits[claim_sha] = {"message": f"EF02 CLAIM\n\nfingerprint={fp}\nattempt_id={attempt_id}", "parents": [{"sha": source_sha}]}

    proof = _make_valid_proof(action["action_id"], attempt_id, source_sha, fp)
    receipt = evidence_digest(proof)

    owner.append("CLAIMED")
    owner.append("SPAWN_INTENT")
    owner.append("PROVIDER_INTENT", round=0)
    owner.append("ROUND_VERIFIED", round=0, receipt=receipt)
    owner.append("RESULT_VERIFIED", receipt=receipt)
    owner.append("PUBLISH_INTENT", receipt=receipt)

    cand_sha = hashlib.sha1((f"cand-{attempt_id}").encode()).hexdigest()
    storage.refs[f"refs/heads/ef02/candidate/{claim_key}"] = cand_sha
    cand_msg = f"EF02 candidate attempt {attempt_id}{EVIDENCE_MARKER}{canonical(proof)}"
    storage.commits[cand_sha] = {"message": cand_msg, "tree": {"sha": "e" * 40}, "parents": [{"sha": source_sha}]}
    owner.append("PUBLISHED", receipt=receipt, candidate=WorkOwner.digest(cand_sha), attempt_id=attempt_id)
    return cand_sha, receipt


def test_workowner_conflicting_successors_one_generation_winner():
    """Two concurrent successors attempt to claim generation 2; exactly one wins create-only primitive."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    action1 = _action()
    action1["attempt_id"] = "attempt-r1"
    packet1 = {"batch_id": "b-root", "source_sha": BASE, "actions": [action1]}
    storage.issues[506] = packet1
    storage.issue_body = packet1
    id1 = WorkOwner.identity(storage, 506, packet1, action1)
    owner1 = WorkOwner.acquire(storage, id1)
    _publish_owner(storage, owner1, action1, attempt_id="attempt-r1")

    action_a = {**_action(), "attempt_id": "attempt-r2a", "repair_of": "#506"}
    packet_a = {"batch_id": "b-succ-a", "source_sha": BASE, "actions": [action_a]}
    storage.issues[508] = packet_a
    storage.issue_body = packet_a
    id_a = WorkOwner.identity(storage, 508, packet_a, action_a)
    assert id_a["work_key"] == id1["work_key"]
    assert id_a["generation"] == 2

    action_b = {**_action(), "attempt_id": "attempt-r2b", "repair_of": "#506"}
    packet_b = {"batch_id": "b-succ-b", "source_sha": BASE, "actions": [action_b]}
    storage.issues[509] = packet_b
    id_b = WorkOwner.identity(storage, 509, packet_b, action_b)
    assert id_b["work_key"] == id1["work_key"]
    assert id_b["generation"] == 2

    owner_a = WorkOwner.acquire(storage, id_a)
    assert owner_a.generation == 2

    with pytest.raises(RemoteTransportError, match="WORK_OWNER_TRANSFER_CONFLICT"):
        WorkOwner.acquire(storage, id_b)


def test_workowner_stale_generation_rejected_at_all_fences():
    """Generation 1 attempts to append or publish after Generation 2 transferred; rejected at all fences."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    action1 = {**_action(), "attempt_id": "attempt-r1"}
    packet1 = {"batch_id": "b1", "source_sha": BASE, "actions": [action1]}
    storage.issues[506] = packet1
    storage.issue_body = packet1
    id1 = WorkOwner.identity(storage, 506, packet1, action1)
    owner1 = WorkOwner.acquire(storage, id1)
    _publish_owner(storage, owner1, action1, attempt_id="attempt-r1")

    action2 = {**_action(), "attempt_id": "attempt-r2", "repair_of": "#506"}
    packet2 = {"batch_id": "b2", "source_sha": BASE, "actions": [action2]}
    storage.issues[508] = packet2
    storage.issue_body = packet2
    id2 = WorkOwner.identity(storage, 508, packet2, action2)
    owner2 = WorkOwner.acquire(storage, id2)
    assert owner2.generation == 2

    with pytest.raises(RemoteTransportError, match="WORK_OWNER_STALE_FENCE"):
        owner1.append("PROVIDER_INTENT", round=0)

    with pytest.raises(RemoteTransportError, match="WORK_OWNER_STALE_FENCE"):
        owner1.assert_current()


def test_workowner_unknown_or_unresolved_predecessor_blocks_transfer():
    """Unresolved predecessor (e.g. at PROVIDER_INTENT or UNKNOWN) denies generation transfer."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    action1 = {**_action(), "attempt_id": "attempt-r1"}
    packet1 = {"batch_id": "b1", "source_sha": BASE, "actions": [action1]}
    storage.issues[506] = packet1
    storage.issue_body = packet1
    id1 = WorkOwner.identity(storage, 506, packet1, action1)
    owner1 = WorkOwner.acquire(storage, id1)
    owner1.append("CLAIMED")
    owner1.append("SPAWN_INTENT")
    owner1.append("PROVIDER_INTENT", round=0)

    fp1 = hashlib.sha256(json.dumps({"batch_id": "b1", "source_sha": BASE, "action": action1},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    claim_key1 = storage.attempt_key("attempt-r1")
    claim_sha1 = hashlib.sha1(b"c1").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key1}"] = claim_sha1
    storage.commits[claim_sha1] = {"message": f"EF02 CLAIM\n\nfingerprint={fp1}\nattempt_id=attempt-r1", "parents": [{"sha": BASE}]}

    action2 = {**_action(), "attempt_id": "attempt-r2", "repair_of": "#506"}
    packet2 = {"batch_id": "b2", "source_sha": BASE, "actions": [action2]}
    storage.issues[508] = packet2
    storage.issue_body = packet2
    id2 = WorkOwner.identity(storage, 508, packet2, action2)

    with pytest.raises(RemoteTransportError, match="WORK_OWNER_PREDECESSOR_UNRESOLVED"):
        WorkOwner.acquire(storage, id2)

    # Predecessor ending in UNKNOWN also strictly denies transfer (UNKNOWN_FAIL_CLOSED)
    owner1.append("UNKNOWN")
    with pytest.raises(RemoteTransportError, match="WORK_OWNER_PREDECESSOR_UNRESOLVED"):
        WorkOwner.acquire(storage, id2)


def test_workowner_lost_workspace_recovery_readonly_no_duplicate(tmp_path):
    """Local workspace deleted; owner reconstructed from GitHub storage; GET-only calls; no duplicate dispatch."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import shutil
    storage = WorkOwnerMemoryTransport(BASE)
    action = _action()
    packet = {"batch_id": "lost-ws", "source_sha": BASE, "actions": [action]}
    storage.issue_body = packet
    id1 = WorkOwner.identity(storage, 506, packet, action)
    owner = WorkOwner.acquire(storage, id1)
    owner.append("CLAIMED")
    owner.append("SPAWN_INTENT")
    owner.append("UNKNOWN")

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "file.txt").write_text("hello")
    shutil.rmtree(ws)
    assert not ws.exists()

    storage.requests.clear()
    recovered = WorkOwner(storage, id1, "")
    result = recovered.reconcile()
    assert result["status"] == "HOLD_UNKNOWN_EFFECT"
    assert result["stage"] == "UNKNOWN"
    assert result["replay_authorized"] is False
    assert result["publication_authorized"] is False
    assert all(method == "GET" for method, *_ in storage.requests)
    assert not any(method == "POST" for method, *_ in storage.requests)


def test_workowner_422_or_timeout_during_transfer_holds_without_retry():
    """Transport failure during transfer record creation raises without retry."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    action1 = {**_action(), "attempt_id": "attempt-r1"}
    packet1 = {"batch_id": "b1", "source_sha": BASE, "actions": [action1]}
    storage.issues[506] = packet1
    storage.issue_body = packet1
    id1 = WorkOwner.identity(storage, 506, packet1, action1)
    owner1 = WorkOwner.acquire(storage, id1)
    _publish_owner(storage, owner1, action1, attempt_id="attempt-r1")

    action2 = {**_action(), "attempt_id": "attempt-r2", "repair_of": "#506"}
    packet2 = {"batch_id": "b2", "source_sha": BASE, "actions": [action2]}
    storage.issues[508] = packet2
    storage.issue_body = packet2
    id2 = WorkOwner.identity(storage, 508, packet2, action2)

    def fail_ref(data):
        if "workowner" in data.get("ref", ""):
            raise RemoteTransportError("GITHUB_HTTP_422")
    storage.before_write = fail_ref

    with pytest.raises(RemoteTransportError, match="WORK_OWNER_TRANSFER_CONFLICT"):
        WorkOwner.acquire(storage, id2)

    def fail_timeout(data):
        if "workowner" in data.get("ref", ""):
            raise RemoteTransportError("GITHUB_TIMEOUT")
    storage.before_write = fail_timeout

    with pytest.raises(RemoteTransportError, match="GITHUB_TIMEOUT"):
        WorkOwner.acquire(storage, id2)


def test_workowner_exact_506_508_520_523_legacy_repair_of_sequence():
    """Exact sequence: #506 -> #508 -> #520 -> #523 -> R5 with raw attempt string lookup."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    # #506: action R1-EPWO-WP02-WORKOWNER-001 attempt r1_epwo_wp02_5f316c0e4_20261106_r1 no repair_of
    a506 = {"action_id": "R1-EPWO-WP02-WORKOWNER-001", "attempt_id": "r1_epwo_wp02_5f316c0e4_20261106_r1",
            "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    p506 = {"batch_id": "batch-506", "source_sha": BASE, "actions": [a506]}
    storage.issues[506] = p506

    fp506 = hashlib.sha256(json.dumps({"batch_id": "batch-506", "source_sha": BASE, "action": a506},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    k506 = storage.attempt_key(a506["attempt_id"])
    c506 = hashlib.sha1(b"c506").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{k506}"] = c506
    storage.commits[c506] = {"message": f"EF02 CLAIM\n\nfingerprint={fp506}\nattempt_id={a506['attempt_id']}",
                             "parents": [{"sha": BASE}]}

    # #508: action R1-WP02-CODEX-R2 attempt r1_wp02_codex_r2_20261106 repair_of r1_epwo_wp02_5f316c0e4_20261106_r1
    a508 = {"action_id": "R1-WP02-CODEX-R2", "attempt_id": "r1_wp02_codex_r2_20261106",
            "repair_of": "r1_epwo_wp02_5f316c0e4_20261106_r1", "executor": "codex", "model": "gpt-5", "effort": "high"}
    p508 = {"batch_id": "batch-508", "source_sha": BASE, "actions": [a508]}
    storage.issues[508] = p508

    fp508 = hashlib.sha256(json.dumps({"batch_id": "batch-508", "source_sha": BASE, "action": a508},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    k508 = storage.attempt_key(a508["attempt_id"])
    c508 = hashlib.sha1(b"c508").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{k508}"] = c508
    storage.commits[c508] = {"message": f"EF02 CLAIM\n\nfingerprint={fp508}\nattempt_id={a508['attempt_id']}",
                             "parents": [{"sha": BASE}]}

    # #520: action R1-WP02-CODEX-R3 attempt r1_wp02_codex_r3_20261106 repair_of r1_wp02_codex_r2_20261106
    a520 = {"action_id": "R1-WP02-CODEX-R3", "attempt_id": "r1_wp02_codex_r3_20261106",
            "repair_of": "r1_wp02_codex_r2_20261106", "executor": "codex", "model": "gpt-5", "effort": "high"}
    p520 = {"batch_id": "batch-520", "source_sha": BASE, "actions": [a520]}
    storage.issues[520] = p520

    fp520 = hashlib.sha256(json.dumps({"batch_id": "batch-520", "source_sha": BASE, "action": a520},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    k520 = storage.attempt_key(a520["attempt_id"])
    c520 = hashlib.sha1(b"c520").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{k520}"] = c520
    storage.commits[c520] = {"message": f"EF02 CLAIM\n\nfingerprint={fp520}\nattempt_id={a520['attempt_id']}",
                             "parents": [{"sha": BASE}]}

    # #523: action R1-WP02-AGY-R4 attempt r1_wp02_agy38_high_r4_20261106 repair_of r1_wp02_codex_r3_20261106
    a523 = {"action_id": "R1-WP02-AGY-R4", "attempt_id": "r1_wp02_agy38_high_r4_20261106",
            "repair_of": "r1_wp02_codex_r3_20261106", "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    p523 = {"batch_id": "batch-523", "source_sha": BASE, "actions": [a523]}
    storage.issues[523] = p523

    fp523 = hashlib.sha256(json.dumps({"batch_id": "batch-523", "source_sha": BASE, "action": a523},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    k523 = storage.attempt_key(a523["attempt_id"])
    c523 = hashlib.sha1(b"c523").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{k523}"] = c523
    storage.commits[c523] = {"message": f"EF02 CLAIM\n\nfingerprint={fp523}\nattempt_id={a523['attempt_id']}",
                             "parents": [{"sha": BASE}]}

    # R5: repair_of r1_wp02_agy38_high_r4_20261106
    a_r5 = {"action_id": "R1-WP02-AGY-R5", "attempt_id": "r1_wp02_agy38_high_r5_20261106",
            "repair_of": "r1_wp02_agy38_high_r4_20261106", "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    p_r5 = {"batch_id": "batch-r5", "source_sha": BASE, "actions": [a_r5]}
    storage.issues[524] = p_r5

    id_r5 = WorkOwner.identity(storage, 524, p_r5, a_r5)
    assert id_r5["root_issue"] == 506
    assert id_r5["root_action_id"] == "R1-EPWO-WP02-WORKOWNER-001"
    assert id_r5["generation"] == 5
    assert len(id_r5["lineage"]) == 4
    assert [p["issue"] for p in id_r5["lineage"]] == [523, 520, 508, 506]
    assert [p["attempt_id"] for p in id_r5["lineage"]] == [
        "r1_wp02_agy38_high_r4_20261106",
        "r1_wp02_codex_r3_20261106",
        "r1_wp02_codex_r2_20261106",
        "r1_epwo_wp02_5f316c0e4_20261106_r1"
    ]

    # Explicit future #issue:attempt validation
    a_r5_explicit = {**a_r5, "repair_of": "#523:r1_wp02_agy38_high_r4_20261106"}
    p_r5_explicit = {"batch_id": "batch-r5", "source_sha": BASE, "actions": [a_r5_explicit]}
    storage.issues[524] = p_r5_explicit
    id_r5_explicit = WorkOwner.identity(storage, 524, p_r5_explicit, a_r5_explicit)
    assert id_r5_explicit["root_issue"] == 506
    assert id_r5_explicit["generation"] == 5


def test_workowner_unknown_predecessor_strictly_denies_transfer():
    """Defect 2: UNKNOWN predecessor provider/publication/reownership strictly denied."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    action1 = {"action_id": "R1-WP02-CODEX-R3", "attempt_id": "r1_wp02_codex_r3_20261106",
               "executor": "codex", "model": "gpt-5", "effort": "high"}
    packet1 = {"batch_id": "b-r3", "source_sha": BASE, "actions": [action1]}
    storage.issues[520] = packet1
    storage.issue_body = packet1
    id1 = WorkOwner.identity(storage, 520, packet1, action1)
    owner1 = WorkOwner.acquire(storage, id1)
    owner1.append("CLAIMED")
    owner1.append("SPAWN_INTENT")
    owner1.append("UNKNOWN")

    fp1 = hashlib.sha256(json.dumps({"batch_id": "b-r3", "source_sha": BASE, "action": action1},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    k1 = storage.attempt_key(action1["attempt_id"])
    c1 = hashlib.sha1(b"c1").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{k1}"] = c1
    storage.commits[c1] = {"message": f"EF02 CLAIM\n\nfingerprint={fp1}\nattempt_id={action1['attempt_id']}",
                           "parents": [{"sha": BASE}]}

    # Successor attempts acquire
    action2 = {"action_id": "R1-WP02-AGY-R4", "attempt_id": "r1_wp02_agy38_high_r4_20261106",
               "repair_of": "r1_wp02_codex_r3_20261106", "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    packet2 = {"batch_id": "b-r4", "source_sha": BASE, "actions": [action2]}
    storage.issues[523] = packet2
    storage.issue_body = packet2
    id2 = WorkOwner.identity(storage, 523, packet2, action2)

    with pytest.raises(RemoteTransportError, match="WORK_OWNER_PREDECESSOR_UNRESOLVED"):
        WorkOwner.acquire(storage, id2)

    # Reconcile reflects UNKNOWN, never authorized
    reconciled = WorkOwner(storage, id1, "").reconcile()
    assert reconciled["status"] == "HOLD_UNKNOWN_EFFECT"
    assert reconciled["stage"] == "UNKNOWN"
    assert reconciled["replay_authorized"] is False
    assert reconciled["publication_authorized"] is False
    assert reconciled["lineage_gap"] == "PREDECESSOR_EXECUTION_UNKNOWN"


def test_workowner_v1_legacy_journal_reader_and_bridge():
    """Defect 3: Historic V1 journal reader and bridge with immutable hash chain and candidate verification."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    # Predecessor issue #508
    a508 = {"action_id": "R1-WP02-CODEX-R2", "attempt_id": "r1_wp02_codex_r2_20261106",
            "executor": "codex", "model": "gpt-5", "effort": "high"}
    p508 = {"batch_id": "batch-508", "source_sha": BASE, "actions": [a508]}
    storage.issues[508] = p508

    fp508 = hashlib.sha256(json.dumps({"batch_id": "batch-508", "source_sha": BASE, "action": a508},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    k508 = storage.attempt_key(a508["attempt_id"])
    c508 = hashlib.sha1(b"claim-508").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{k508}"] = c508
    storage.commits[c508] = {"message": f"EF02 CLAIM\n\nfingerprint={fp508}\nattempt_id={a508['attempt_id']}",
                             "parents": [{"sha": BASE}]}

    cand_sha = hashlib.sha1(b"cand-508").hexdigest()
    storage.refs[f"refs/heads/ef02/candidate/{k508}"] = cand_sha
    proof = _make_valid_proof(a508["action_id"], a508["attempt_id"], BASE, fp508)
    from ai_commerce.remote.candidate_policy import canonical, EVIDENCE_MARKER
    storage.commits[cand_sha] = {
        "message": f"EF02 candidate attempt {a508['attempt_id']}{EVIDENCE_MARKER}{canonical(proof)}",
        "tree": {"sha": "e" * 40}, "parents": [{"sha": BASE}]
    }

    # Write a deterministic historic V1 journal under #508's WorkId
    v1_base_id = {"repo": storage.repo, "issue": 508, "action_id": a508["action_id"]}
    v1_work_key = WorkOwner.digest(v1_base_id)
    v1_prefix = f"ef02/workowner/{v1_work_key}/"

    v1_full_identity = {
        **v1_base_id,
        "work_key": v1_work_key,
        "source_sha": BASE,
        "batch_id": "batch-508",
        "attempt_id": a508["attempt_id"],
        "fingerprint": fp508
    }

    fence = "f" * 64
    receipt = WorkOwner.digest(proof)
    stages = [
        ("OWNED", {}),
        ("CLAIMED", {}),
        ("SPAWN_INTENT", {}),
        ("PROVIDER_INTENT", {"round": 0}),
        ("ROUND_VERIFIED", {"round": 0, "receipt": receipt}),
        ("RESULT_VERIFIED", {"receipt": receipt}),
        ("PUBLISH_INTENT", {"receipt": receipt}),
        ("PUBLISHED", {"receipt": receipt, "candidate": WorkOwner.digest(cand_sha), "attempt_id": a508["attempt_id"]})
    ]

    prior_digest = None
    prior_sha = BASE
    for idx, (stage, data) in enumerate(stages):
        rec = {
            "kind": "WorkOwner", "version": 1, "identity": v1_full_identity,
            "generation": 1, "fence": fence, "sequence": idx,
            "previous": prior_digest, "stage": stage, "data": data
        }
        digest = WorkOwner.digest(rec)
        prior_digest = digest
        c_sha = hashlib.sha1(f"v1-commit-{idx}".encode()).hexdigest()
        storage.commits[c_sha] = {
            "message": WorkOwner.MARKER + json.dumps({**rec, "digest": digest}, sort_keys=True, separators=(",", ":")),
            "tree": {"sha": "e" * 40},
            "parents": [{"sha": prior_sha}]
        }
        prior_sha = c_sha
        storage.refs[f"refs/heads/{v1_prefix}{idx:03d}"] = c_sha

    # Successor R3 on #520 with repair_of r1_wp02_codex_r2_20261106
    a520 = {"action_id": "R1-WP02-CODEX-R3", "attempt_id": "r1_wp02_codex_r3_20261106",
            "repair_of": "r1_wp02_codex_r2_20261106", "executor": "codex", "model": "gpt-5", "effort": "high"}
    p520 = {"batch_id": "batch-520", "source_sha": BASE, "actions": [a520]}
    storage.issues[520] = p520
    storage.issue_body = p520

    id520 = WorkOwner.identity(storage, 520, p520, a520)
    assert id520["generation"] == 2

    # V2 owner successfully reads and bridges the legacy V1 journal
    owner2 = WorkOwner.acquire(storage, id520)
    assert owner2.generation == 2
    chain = owner2.read()
    assert len(chain) == 9  # 8 legacy + 1 new generation OWNED
    assert chain[0]["identity"] == v1_full_identity
    assert chain[-1]["generation"] == 2

    # Honest negative case: tampered digest in legacy record raises LEGACY_MIGRATION_UNPROVEN
    bad_storage = WorkOwnerMemoryTransport(BASE)
    bad_storage.issues[508] = p508
    bad_storage.issues[520] = p520
    bad_storage.issue_body = p520
    bad_storage.refs = dict(storage.refs)
    bad_storage.commits = {k: dict(v) for k, v in storage.commits.items()}
    # Tamper with message digest of record 000
    first_sha = bad_storage.refs[f"refs/heads/{v1_prefix}000"]
    bad_storage.commits[first_sha]["message"] = WorkOwner.MARKER + json.dumps({
        "kind": "WorkOwner", "version": 1, "identity": v1_full_identity,
        "generation": 1, "fence": fence, "sequence": 0,
        "previous": None, "stage": "OWNED", "data": {}, "digest": "0" * 64
    })
    bad_owner = WorkOwner(bad_storage, id520, "")
    with pytest.raises(RemoteTransportError, match="LEGACY_MIGRATION_UNPROVEN"):
        bad_owner.read()

    reconciled = bad_owner.reconcile()
    assert reconciled["status"] == "HOLD_UNKNOWN_EFFECT"
    assert reconciled["lineage_gap"] == "LEGACY_MIGRATION_UNPROVEN"


def test_workowner_tampered_claim_candidate_and_cycle():
    """Negative tests: missing attempt, ambiguous attempt, forged claim, candidate proof mismatch, cycle."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    import hashlib
    storage = WorkOwnerMemoryTransport(BASE)

    action = {"action_id": "R1-WP02-AGY-R5", "attempt_id": "attempt-r5",
              "repair_of": "non-existent-attempt", "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    packet = {"batch_id": "b-r5", "source_sha": BASE, "actions": [action]}
    storage.issues[524] = packet

    # 1. Missing attempt ID
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_ATTEMPT_NOT_FOUND"):
        WorkOwner.identity(storage, 524, packet, action)

    # 2. Ambiguous attempt ID across two issues
    dup_action = {"action_id": "dup", "attempt_id": "dup-attempt", "executor": "codex", "model": "gpt-5", "effort": "high"}
    storage.issues[506] = {"batch_id": "b1", "source_sha": BASE, "actions": [dup_action]}
    storage.issues[508] = {"batch_id": "b2", "source_sha": BASE, "actions": [dup_action]}
    action["repair_of"] = "dup-attempt"
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_ATTEMPT_AMBIGUOUS"):
        WorkOwner.identity(storage, 524, packet, action)

    # 3. Forged claim fingerprint
    storage.issues.clear()
    pred_action = {"action_id": "R1-WP02-CODEX-R2", "attempt_id": "target-attempt", "executor": "codex", "model": "gpt-5", "effort": "high"}
    pred_packet = {"batch_id": "b-pred", "source_sha": BASE, "actions": [pred_action]}
    storage.issues[508] = pred_packet
    storage.issues[524] = {"batch_id": "b-r5", "source_sha": BASE, "actions": [{**action, "repair_of": "target-attempt"}]}

    claim_key = storage.attempt_key("target-attempt")
    claim_sha = hashlib.sha1(b"claim-forged").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key}"] = claim_sha
    storage.commits[claim_sha] = {"message": f"EF02 CLAIM\n\nfingerprint={'0'*64}\nattempt_id=target-attempt", "parents": [{"sha": BASE}]}

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_CLAIM_MISMATCH"):
        WorkOwner.identity(storage, 524, storage.issues[524], storage.issues[524]["actions"][0])

    # 4. Candidate proof mismatch
    fp_pred = hashlib.sha256(json.dumps({"batch_id": "b-pred", "source_sha": BASE, "action": pred_action},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    storage.commits[claim_sha] = {"message": f"EF02 CLAIM\n\nfingerprint={fp_pred}\nattempt_id=target-attempt", "parents": [{"sha": BASE}]}

    cand_sha = hashlib.sha1(b"cand-bad").hexdigest()
    storage.refs[f"refs/heads/ef02/candidate/{claim_key}"] = cand_sha
    bad_proof = {
        "action_id": "WRONG-ACTION", "attempt_id": "target-attempt",
        "effective_source_sha": BASE, "source_sha": BASE,
        "changed_files": ["tests/test_ef02.py"], "candidate_file_sha256": {"tests/test_ef02.py": "1" * 64},
        "allowed_paths": ["tests/test_ef02.py"], "status": "CANDIDATE_READY",
        "review_required": False, "fingerprint": fp_pred, "scope_valid": True, "version": 1
    }
    storage.commits[cand_sha] = {
        "message": f"EF02 candidate attempt target-attempt\n\nEF02-CANDIDATE-EVIDENCE-V1\n{json.dumps(bad_proof, sort_keys=True)}",
        "tree": {"sha": "e" * 40}, "parents": [{"sha": BASE}]
    }

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_CANDIDATE_MISMATCH"):
        WorkOwner.identity(storage, 524, storage.issues[524], storage.issues[524]["actions"][0])


def test_workowner_true_absence_zero_refs():
    """Defect 3: True absence with zero refs on GitHub reports ABSENT, never PASS."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)
    storage.refs.clear()

    action = _action()
    packet = {"batch_id": "absent-batch", "source_sha": BASE, "actions": [action]}
    storage.issue_body = packet
    storage.issues[506] = packet

    id1 = WorkOwner.identity(storage, 506, packet, action)
    owner = WorkOwner(storage, id1, "")
    reconciled = owner.reconcile()

    assert reconciled["status"] == "ABSENT"
    assert reconciled["stage"] == "ABSENT"
    assert reconciled["proves_current_mt01_pass"] is False
    assert reconciled["replay_authorized"] is False
    assert reconciled["publication_authorized"] is False
    assert reconciled["generation"] == 1


def test_workowner_lineage_index_issuer_mismatch():
    """Negative test: durable index creation and lookup detect issuer mismatch."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)

    pred_action = {"action_id": "R1-WP02-CODEX-R2", "attempt_id": "target-attempt", "executor": "codex", "model": "gpt-5", "effort": "high"}
    pred_packet = {"batch_id": "b-pred", "source_sha": BASE, "actions": [pred_action]}
    storage.issues[508] = {**pred_packet, "user": {"login": "authorized-owner"}}

    pred_fp = hashlib.sha256(json.dumps({
        "batch_id": "b-pred", "source_sha": BASE, "action": pred_action
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    # Mismatched issuer at index creation is rejected immediately before commit
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_INDEX_ISSUER_MISMATCH"):
        storage.index_attempt(
            issue_number=508, source_sha=BASE, attempt_id="target-attempt",
            fingerprint=pred_fp, issuer="attacker"
        )

    storage.index_attempt(
        issue_number=508, source_sha=BASE, attempt_id="target-attempt",
        fingerprint=pred_fp, issuer="authorized-owner"
    )

    # If the issue author on lookup does not match the indexed issuer, lookup rejects it
    storage.issues[508] = {**pred_packet, "user": {"login": "attacker"}}
    action = {"action_id": "R1-WP02-AGY-R8", "attempt_id": "attempt-r8", "repair_of": "target-attempt",
              "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    packet = {"batch_id": "b-r8", "source_sha": BASE, "actions": [action]}
    storage.issues[524] = packet

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_INDEX_ISSUER_MISMATCH"):
        WorkOwner.identity(storage, 524, packet, action)


def test_workowner_lineage_index_collision():
    """Negative test: index collision when re-indexing attempt with conflicting metadata."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)
    storage.issues[508] = {"batch_id": "b1", "source_sha": BASE, "actions": [], "user": {"login": "owner"}}
    storage.issues[510] = {"batch_id": "b2", "source_sha": BASE, "actions": [], "user": {"login": "owner"}}

    storage.index_attempt(
        issue_number=508, source_sha=BASE, attempt_id="attempt-coll",
        fingerprint="1" * 64, issuer="owner"
    )

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_INDEX_COLLISION"):
        storage.index_attempt(
            issue_number=510, source_sha=BASE, attempt_id="attempt-coll",
            fingerprint="1" * 64, issuer="owner"
        )

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_INDEX_COLLISION"):
        storage.index_attempt(
            issue_number=508, source_sha=BASE, attempt_id="attempt-coll",
            fingerprint="2" * 64, issuer="owner"
        )


def test_workowner_lineage_unindexed_pagination_150_issues_and_hidden_duplicate():
    """150+ issues: resolves attempt on page 2, and detects hidden duplicate across pages."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)

    for i in range(1, 151):
        storage.issues[i] = {
            "batch_id": f"b-{i}",
            "source_sha": BASE,
            "actions": [{"action_id": f"act-{i}", "attempt_id": f"att-{i}", "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}]
        }

    target_action = {"action_id": "target-act", "attempt_id": "target-page2", "executor": "codex", "model": "gpt-5", "effort": "high"}
    storage.issues[150] = {"batch_id": "b-150", "source_sha": BASE, "actions": [target_action]}

    id150 = WorkOwner.identity(storage, 150, storage.issues[150], target_action)
    owner150 = WorkOwner.acquire(storage, id150)
    _publish_owner(storage, owner150, target_action, attempt_id="target-page2")

    succ_action = {"action_id": "succ-act", "attempt_id": "succ-att", "repair_of": "target-page2",
                   "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    succ_packet = {"batch_id": "b-succ", "source_sha": BASE, "actions": [succ_action]}
    storage.issues[200] = succ_packet

    id200 = WorkOwner.identity(storage, 200, succ_packet, succ_action)
    assert id200["generation"] == 2
    assert id200["root_issue"] == 150

    storage.issues[10] = {"batch_id": "b-10", "source_sha": BASE, "actions": [target_action]}
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_ATTEMPT_AMBIGUOUS"):
        WorkOwner.identity(storage, 200, succ_packet, succ_action)


def test_workowner_lineage_page_overflow():
    """Negative test: cap expiry / page overflow prevents false global uniqueness."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)

    for i in range(1, 1101):
        storage.issues[i] = {
            "batch_id": f"b-{i}",
            "source_sha": BASE,
            "actions": [{"action_id": f"act-{i}", "attempt_id": f"att-{i}", "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}]
        }

    succ_action = {"action_id": "succ", "attempt_id": "succ-att", "repair_of": "never-found-att",
                   "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    succ_packet = {"batch_id": "b-succ", "source_sha": BASE, "actions": [succ_action]}
    storage.issues[1200] = succ_packet

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PAGE_OVERFLOW"):
        WorkOwner.identity(storage, 1200, succ_packet, succ_action)


def test_workowner_lineage_missing_claim_parent_fails_closed():
    """Negative test: claim with missing parent strictly rejected."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)

    pred_action = {"action_id": "R1-WP02-CODEX-R2", "attempt_id": "target-attempt", "executor": "codex", "model": "gpt-5", "effort": "high"}
    pred_packet = {"batch_id": "b-pred", "source_sha": BASE, "actions": [pred_action]}
    storage.issues[508] = pred_packet

    fp_pred = hashlib.sha256(json.dumps({"batch_id": "b-pred", "source_sha": BASE, "action": pred_action},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    claim_key = storage.attempt_key("target-attempt")
    claim_sha = hashlib.sha1(b"claim-noparent").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key}"] = claim_sha
    storage.commits[claim_sha] = {"message": f"EF02 CLAIM\n\nfingerprint={fp_pred}\nattempt_id=target-attempt", "parents": []}

    action = {"action_id": "R1-WP02-AGY-R8", "attempt_id": "attempt-r8", "repair_of": "target-attempt",
              "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    packet = {"batch_id": "b-r8", "source_sha": BASE, "actions": [action]}
    storage.issues[524] = packet

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_CLAIM_MISMATCH"):
        WorkOwner.identity(storage, 524, packet, action)


def test_workowner_lineage_evidenceless_candidate_rejected():
    """Negative test: candidate present without non-null signed evidence marker is rejected."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)

    pred_action = {"action_id": "R1-WP02-CODEX-R2", "attempt_id": "target-attempt", "executor": "codex", "model": "gpt-5", "effort": "high"}
    pred_packet = {"batch_id": "b-pred", "source_sha": BASE, "actions": [pred_action]}
    storage.issues[508] = pred_packet

    fp_pred = hashlib.sha256(json.dumps({"batch_id": "b-pred", "source_sha": BASE, "action": pred_action},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    claim_key = storage.attempt_key("target-attempt")
    claim_sha = hashlib.sha1(b"claim-valid").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key}"] = claim_sha
    storage.commits[claim_sha] = {"message": f"EF02 CLAIM\n\nfingerprint={fp_pred}\nattempt_id=target-attempt", "parents": [{"sha": BASE}]}

    cand_sha = hashlib.sha1(b"cand-no-evidence").hexdigest()
    storage.refs[f"refs/heads/ef02/candidate/{claim_key}"] = cand_sha
    storage.commits[cand_sha] = {
        "message": "EF02 candidate attempt target-attempt",
        "tree": {"sha": "e" * 40},
        "parents": [{"sha": BASE}]
    }

    action = {"action_id": "R1-WP02-AGY-R8", "attempt_id": "attempt-r8", "repair_of": "target-attempt",
              "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    packet = {"batch_id": "b-r8", "source_sha": BASE, "actions": [action]}
    storage.issues[524] = packet

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_CANDIDATE_MISMATCH"):
        WorkOwner.identity(storage, 524, packet, action)


def test_workowner_lineage_literal_r1_r7_repair_of():
    """Negative and positive tests for literal R1..R7 repair_of attempt IDs."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)

    action = {"action_id": "R1-WP02-AGY-R8", "attempt_id": "attempt-r8",
              "repair_of": "r1_wp02_agy38_high_r5_20261106",
              "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    packet = {"batch_id": "b-r8", "source_sha": BASE, "actions": [action]}
    storage.issues[543] = packet

    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_PREDECESSOR_ATTEMPT_NOT_FOUND"):
        WorkOwner.identity(storage, 543, packet, action)

    r5_action = {"action_id": "R1-WP02-AGY-R5", "attempt_id": "r1_wp02_agy38_high_r5_20261106",
                 "executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"}
    storage.issues[524] = {"batch_id": "b-r5", "source_sha": BASE, "actions": [r5_action]}
    r5_id = WorkOwner.identity(storage, 524, storage.issues[524], r5_action)
    r5_owner = WorkOwner.acquire(storage, r5_id)
    _publish_owner(storage, r5_owner, r5_action, attempt_id="r1_wp02_agy38_high_r5_20261106")

    r5_fp = hashlib.sha256(json.dumps({
        "batch_id": "b-r5", "source_sha": BASE, "action": r5_action
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    storage.index_attempt(
        issue_number=524, source_sha=BASE, attempt_id="r1_wp02_agy38_high_r5_20261106",
        fingerprint=r5_fp, issuer="trung-via"
    )

    ident = WorkOwner.identity(storage, 543, packet, action)
    assert ident["generation"] == 2
    assert ident["root_issue"] == 524


def test_workowner_acquire_unknown_and_stale_generation():
    """Negative tests: UNKNOWN predecessor denied, stale/skipped generations rejected."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner
    storage = WorkOwnerMemoryTransport(BASE)

    action1 = _action()
    packet1 = {"batch_id": "b1", "source_sha": BASE, "actions": [action1]}
    storage.issue_body = packet1
    id1 = WorkOwner.identity(storage, 506, packet1, action1)
    owner1 = WorkOwner.acquire(storage, id1)
    owner1.append("CLAIMED")
    owner1.append("UNKNOWN")

    id2 = dict(id1)
    id2["generation"] = 2
    id2["attempt_id"] = "attempt-gen2"
    with pytest.raises(RemoteTransportError, match="WORK_OWNER_PREDECESSOR_UNRESOLVED"):
        WorkOwner.acquire(storage, id2)

    with pytest.raises(RemoteTransportError, match="(?:WORK_OWNER_TRANSFER_CONFLICT|WORK_OWNER_EXISTS_RECONCILE_ONLY)"):
        WorkOwner.acquire(storage, id1)

    id3 = dict(id1)
    id3["generation"] = 3
    id3["attempt_id"] = "attempt-gen3"
    with pytest.raises(RemoteTransportError, match="WORK_OWNER_PREDECESSOR_UNRESOLVED"):
        WorkOwner.acquire(storage, id3)

    action_pub = _action()
    action_pub["attempt_id"] = "attempt-pub"
    packet_pub = {"batch_id": "b-pub", "source_sha": BASE, "actions": [action_pub]}
    storage.issues[600] = packet_pub
    id_pub = WorkOwner.identity(storage, 600, packet_pub, action_pub)
    owner_pub = WorkOwner.acquire(storage, id_pub)
    _publish_owner(storage, owner_pub, action_pub, attempt_id="attempt-pub")

    id_skipped = dict(id_pub)
    id_skipped["generation"] = 3
    id_skipped["attempt_id"] = "attempt-gen3-skipped"
    with pytest.raises(RemoteTransportError, match="WORK_OWNER_STALE_OR_SKIPPED_GENERATION"):
        WorkOwner.acquire(storage, id_skipped)

    id_gen2 = dict(id_pub)
    id_gen2["generation"] = 2
    id_gen2["attempt_id"] = "attempt-gen2-ok"
    owner_gen2 = WorkOwner.acquire(storage, id_gen2)

    id_stale = dict(id_pub)
    id_stale["generation"] = 2
    id_stale["attempt_id"] = "attempt-gen2-conflict"
    with pytest.raises(RemoteTransportError, match="WORK_OWNER_TRANSFER_CONFLICT"):
        WorkOwner.acquire(storage, id_stale)


def _issue_544_packet(source_sha=BASE):
    return {
        "kind": "AI_COMMERCE_REMOTE_ACTION_BATCH",
        "version": 1,
        "batch_id": "ef02-batch-544",
        "source_sha": source_sha,
        "actor": "HUMAN",
        "authorization_ref": "human-approval-544",
        "max_parallel": 2,
        "actions": [{
            "action_id": "R1-WP02-ARGON-R9",
            "attempt_id": "r1_wp02_argon_medium_r9_20261107",
            "repair_of": "r1_wp02_agy38_high_r8_20261107",
            "executor": "antigravity",
            "model": "gemini-4-argon-medium",
            "effort": "medium",
            "instruction": "Fix Defect A and Defect B.\nMT01_ACCEPTANCE_JSON:[\"EF02.action-and-fanin\"]",
            "allowed_paths": ["src/ai_commerce/remote/transport.py", "src/ai_commerce/remote/worker.py"],
            "verification_profile": "ef02-tests",
            "depends_on": []
        }]
    }


def test_r9_defect_a_and_b_cli_ingress_544_packet_and_successor_r10_lineage_round_trip(tmp_path, monkeypatch):
    """Real RemoteActionBatch CLI ingress -> LocalBatchWorker -> index_attempt -> successor R10 lineage.

    Tests actual #544 packet semantics (actor='HUMAN', no issuer_login field in packet,
    authenticated GitHub Issue creator='trung-via'), verifies index commit records
    issuer='trung-via' (never 'HUMAN'), and verifies successor R10 resolves predecessor
    via durable index without WORK_LINEAGE_INDEX_ISSUER_MISMATCH. Also tests BRAIN actor
    with authorized brain issuer login.
    """
    from ai_commerce.remote import __main__ as cli
    from test_ef01_repair import WorkOwnerMemoryTransport
    from ai_commerce.remote.transport import WorkOwner

    storage = WorkOwnerMemoryTransport(BASE)

    # Predecessor R8 on issue #543
    r8_action = {
        "action_id": "R1-WP02-AGY-R8",
        "attempt_id": "r1_wp02_agy38_high_r8_20261107",
        "executor": "antigravity",
        "model": "gemini-3.8-flash",
        "effort": "high",
        "instruction": "R8 attempt",
        "allowed_paths": ["src/ai_commerce/remote/transport.py"],
        "verification_profile": "ef02-tests",
        "depends_on": []
    }
    r8_packet = {
        "kind": "AI_COMMERCE_REMOTE_ACTION_BATCH",
        "version": 1,
        "batch_id": "ef02-batch-543",
        "source_sha": BASE,
        "actor": "HUMAN",
        "authorization_ref": "human-543",
        "actions": [r8_action]
    }
    storage.issues[543] = r8_packet
    WorkOwner.index_ingress(storage, 543, r8_packet, r8_action, issuer="trung-via")
    id_r8 = WorkOwner.identity(storage, 543, r8_packet, r8_action)
    owner_r8 = WorkOwner.acquire(storage, id_r8)
    _publish_owner(storage, owner_r8, r8_action, attempt_id=r8_action["attempt_id"])

    # Issue #544 (R9) with actor='HUMAN' and no issuer_login field in packet
    p544 = _issue_544_packet(BASE)
    assert "issuer_login" not in p544
    assert p544["actor"] == "HUMAN"
    storage.issues[544] = p544

    req_file = tmp_path / "request_544.json"
    req_file.write_text(json.dumps(p544), encoding="utf-8")

    # Setup checkout files required by CLI main()
    (tmp_path / ".github" / "ef02").mkdir(parents=True)
    (tmp_path / "contracts").mkdir(parents=True)
    root_repo = Path(__file__).resolve().parents[1]
    (tmp_path / ".github" / "ef02" / "verify-profiles.json").write_bytes(
        (root_repo / ".github" / "ef02" / "verify-profiles.json").read_bytes()
    )
    (tmp_path / ".github" / "ef02" / "approved-candidates.v1.json").write_text(
        json.dumps({"version": 1, "approved_source_sha": BASE, "candidates": {}}), encoding="utf-8"
    )
    (tmp_path / "contracts" / "RemoteActionBatch.v1.schema.json").write_bytes(
        (root_repo / "contracts" / "RemoteActionBatch.v1.schema.json").read_bytes()
    )

    monkeypatch.setattr(cli, "check_source", lambda *_a, **_kw: None)
    monkeypatch.setattr("ai_commerce.remote.worker.check_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "GitHubTransport", lambda repo, token: storage)
    storage.comment = lambda issue_num, body: None

    # Stop at pre_spawn_checkpoint so we test full admission + index + owner + claim without launching real EF01
    def stop_at_prespawn(**_kw):
        raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
    storage.pre_spawn_checkpoint = stop_at_prespawn
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=""))
    _forbid_model(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture/repository")
    monkeypatch.setenv("GITHUB_RUN_ID", "544001")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    rc = cli.main([
        "run", "--request-file", str(req_file), "--checkout", str(tmp_path),
        "--issuer-login", "trung-via", "--owner-login", "trung-via",
        "--issue-number", "544"
    ])
    assert rc == 1  # Stopped at prespawn checkpoint as designed

    # Check indexed commit for R9
    r9_key = storage.attempt_key("r1_wp02_argon_medium_r9_20261107")
    r9_index_sha = storage.refs[f"refs/heads/ef02/index/{r9_key}"]
    r9_index_commit = storage.commits[r9_index_sha]
    parsed = storage._parse_index_message(r9_index_commit["message"])
    assert parsed["issuer"] == "trung-via"
    assert parsed["issue"] == 544
    assert parsed["attempt_id"] == "r1_wp02_argon_medium_r9_20261107"

    # Now publish R9 on its WorkOwner so R10 can transfer generation 3
    # First find the owner created for R9 and publish it in storage
    r9_action = p544["actions"][0]
    r9_fp = hashlib.sha256(json.dumps({
        "batch_id": p544["batch_id"], "source_sha": BASE, "action": r9_action
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    proof_r9 = _make_valid_proof(r9_action["action_id"], r9_action["attempt_id"], BASE, r9_fp)
    from ai_commerce.remote.candidate_policy import canonical, evidence_digest, EVIDENCE_MARKER
    receipt_r9 = evidence_digest(proof_r9)
    cand_r9 = hashlib.sha1(b"cand-r9").hexdigest()
    storage.refs[f"refs/heads/ef02/candidate/{r9_key}"] = cand_r9
    storage.commits[cand_r9] = {
        "message": f"EF02 candidate attempt {r9_action['attempt_id']}{EVIDENCE_MARKER}{canonical(proof_r9)}",
        "tree": {"sha": "e" * 40}, "parents": [{"sha": BASE}]
    }
    # Replace the trailing UNKNOWN record (written when pre_spawn_checkpoint raised) with PUBLISHED for R10 transfer test
    work_key = id_r8["work_key"]
    prefix = f"refs/heads/ef02/workowner/{work_key}/"
    wo_refs = sorted(r for r in storage.refs if r.startswith(prefix))
    # Remove UNKNOWN record at the tip and append PROVIDER_INTENT..PUBLISHED using the gen 2 fence
    last_ref = wo_refs[-1]
    last_commit = storage.commits[storage.refs[last_ref]]
    last_rec = json.loads(last_commit["message"][len(WorkOwner.MARKER):])
    assert last_rec["stage"] == "UNKNOWN"
    del storage.refs[last_ref]
    prev_ref = wo_refs[-2]
    prev_sha = storage.refs[prev_ref]
    prev_rec = json.loads(storage.commits[prev_sha]["message"][len(WorkOwner.MARKER):])
    fence_digest = prev_rec["fence"]
    seq = prev_rec["sequence"]
    prior_dig = prev_rec["digest"]
    for stg, dt in [
        ("SPAWN_INTENT", {}),
        ("PROVIDER_INTENT", {"round": 0}),
        ("ROUND_VERIFIED", {"round": 0, "receipt": receipt_r9}),
        ("RESULT_VERIFIED", {"receipt": receipt_r9}),
        ("PUBLISH_INTENT", {"receipt": receipt_r9}),
        ("PUBLISHED", {"receipt": receipt_r9, "candidate": WorkOwner.digest(cand_r9), "attempt_id": r9_action["attempt_id"]})
    ]:
        seq += 1
        rec = {
            "kind": "WorkOwner", "version": 1, "identity": prev_rec["identity"],
            "generation": 2, "fence": fence_digest, "sequence": seq,
            "previous": prior_dig, "stage": stg, "data": dt
        }
        prior_dig = WorkOwner.digest(rec)
        rec["digest"] = prior_dig
        c_sha = hashlib.sha1(json.dumps(rec, sort_keys=True).encode()).hexdigest()
        storage.commits[c_sha] = {
            "message": WorkOwner.MARKER + canonical(rec),
            "tree": {"sha": "e" * 40}, "parents": [{"sha": prev_sha}]
        }
        prev_sha = c_sha
        storage.refs[f"{prefix}{seq:03d}"] = c_sha

    # Successor R10 on issue #545 with actor='BRAIN' and authorized brain issuer 'ai-commerce-brain[bot]'
    r10_action = {
        **r9_action,
        "action_id": "R1-WP02-ARGON-R10",
        "attempt_id": "r1_wp02_argon_medium_r10_20261107",
        "repair_of": "r1_wp02_argon_medium_r9_20261107"
    }
    p545 = {
        **p544,
        "batch_id": "ef02-batch-545",
        "actor": "BRAIN",
        "actions": [r10_action]
    }
    storage.issues[545] = p545
    storage.issue_users = {545: {"login": "ai-commerce-brain[bot]"}}
    orig_storage_api = storage.api
    def api_with_user(method, path, data=None):
        res = orig_storage_api(method, path, data)
        if method == "GET" and path == "/issues/545":
            res["user"] = {"login": "ai-commerce-brain[bot]"}
        return res
    storage.api = api_with_user
    req_545 = tmp_path / "request_545.json"
    req_545.write_text(json.dumps(p545), encoding="utf-8")

    rc10 = cli.main([
        "run", "--request-file", str(req_545), "--checkout", str(tmp_path),
        "--issuer-login", "ai-commerce-brain[bot]", "--owner-login", "trung-via",
        "--brain-issuers", "ai-commerce-brain[bot]", "--issue-number", "545"
    ])
    assert rc10 == 1
    r10_key = storage.attempt_key("r1_wp02_argon_medium_r10_20261107")
    r10_index_sha = storage.refs[f"refs/heads/ef02/index/{r10_key}"]
    parsed10 = storage._parse_index_message(storage.commits[r10_index_sha]["message"])
    assert parsed10["issuer"] == "ai-commerce-brain[bot]"
    assert parsed10["issue"] == 545

    batch_res_10 = json.loads((tmp_path / ".local" / "ef02" / "ef02-batch-545" / "batch_result.json").read_text(encoding="utf-8"))
    act_res_10 = batch_res_10["actions"][0]
    assert "work_owner" in act_res_10, (act_res_10.get("status"), act_res_10.get("error_code"), act_res_10.get("diagnostic"))
    assert act_res_10["work_owner"]["generation"] == 3
    assert act_res_10["work_owner"]["root_issue"] == 543


@pytest.mark.parametrize("failure_mode,expected_code", [
    ("role_name_human", "WORK_INDEX_ISSUER_INVALID"),
    ("role_name_brain", "WORK_INDEX_ISSUER_INVALID"),
    ("missing_issue_creator", "WORK_INDEX_ISSUE_CREATOR_MISSING"),
    ("issuer_mismatch_with_issue", "WORK_LINEAGE_INDEX_ISSUER_MISMATCH"),
    ("unreachable_get_issue", "GITHUB_HTTP_503"),
    ("unreachable_get_ref_403", "GITHUB_HTTP_403"),
    ("unreachable_get_ref_429", "GITHUB_HTTP_429"),
    ("unreachable_post_commit_500", "GITHUB_HTTP_500"),
    ("unreachable_post_ref_timeout", "GITHUB_TRANSPORT_UNKNOWN"),
    ("unreachable_verify_ref", "GITHUB_HTTP_502"),
    ("malformed_existing_ref", "WORK_LINEAGE_INDEX_REF_INVALID"),
    ("malformed_existing_commit", "WORK_LINEAGE_INDEX_COMMIT_INVALID"),
    ("readback_mismatch_collision", "WORK_LINEAGE_INDEX_COLLISION"),
    ("readback_404_after_422", "GITHUB_HTTP_404"),
])
def test_r9_defect_b_index_admission_failures_fail_closed_zero_claims_zero_spawns(
    tmp_path, monkeypatch, failure_mode, expected_code
):
    """Every index GET/POST/verify failure or issuer error fails CLOSED before any claim or Popen."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    storage = WorkOwnerMemoryTransport(BASE)
    p544 = _issue_544_packet(BASE)
    action = p544["actions"][0]
    # Remove repair_of so we isolate index admission of the current action
    action["repair_of"] = None
    storage.issues[544] = p544

    if failure_mode == "missing_issue_creator":
        storage.issues[544] = {**p544, "user": {"login": ""}}

    key = storage.attempt_key(action["attempt_id"])
    ref_path = f"/git/ref/heads/ef02/index/{key}"

    if failure_mode == "malformed_existing_ref":
        storage.refs[f"refs/heads/ef02/index/{key}"] = "not-a-valid-sha"
    elif failure_mode == "malformed_existing_commit":
        bad_sha = "b" * 40
        storage.refs[f"refs/heads/ef02/index/{key}"] = bad_sha
        storage.commits[bad_sha] = {"message": "NOT AN INDEX COMMIT", "parents": [{"sha": BASE}]}

    orig_api = storage.api
    ref_get_count = 0

    def faulty_api(method, path, data=None):
        nonlocal ref_get_count
        if failure_mode == "unreachable_get_issue" and method == "GET" and path == "/issues/544":
            raise RemoteTransportError("GITHUB_HTTP_503")
        if method == "GET" and path == ref_path:
            ref_get_count += 1
            if failure_mode == "unreachable_get_ref_403" and ref_get_count == 1:
                raise RemoteTransportError("GITHUB_HTTP_403")
            if failure_mode == "unreachable_get_ref_429" and ref_get_count == 1:
                raise RemoteTransportError("GITHUB_HTTP_429")
            if failure_mode == "unreachable_verify_ref" and ref_get_count == 2:
                raise RemoteTransportError("GITHUB_HTTP_502")
            if failure_mode == "readback_404_after_422" and ref_get_count == 2:
                raise RemoteTransportError("GITHUB_HTTP_404")
        if failure_mode == "unreachable_post_commit_500" and method == "POST" and path == "/git/commits":
            raise RemoteTransportError("GITHUB_HTTP_500")
        if method == "POST" and path == "/git/refs" and data and "ef02/index/" in data.get("ref", ""):
            if failure_mode == "unreachable_post_ref_timeout":
                raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN")
            if failure_mode == "readback_404_after_422":
                raise RemoteTransportError("GITHUB_HTTP_422")
            if failure_mode == "readback_mismatch_collision":
                # Simulate another writer winning the 422 race with conflicting issue number
                other_sha = "c" * 40
                storage.commits[other_sha] = {
                    "message": f"EF02 INDEX\n\nattempt_id={action['attempt_id']}\nissue=999\nsource_sha={BASE}\nfingerprint={'0'*64}\nissuer=trung-via",
                    "tree": {"sha": "e" * 40},
                    "parents": [{"sha": BASE}]
                }
                storage.refs[f"refs/heads/ef02/index/{key}"] = other_sha
                raise RemoteTransportError("GITHUB_HTTP_422")
        return orig_api(method, path, data)

    storage.api = faulty_api
    _forbid_model(monkeypatch)
    monkeypatch.setattr("ai_commerce.remote.worker.check_source", lambda *_a, **_kw: None)
    monkeypatch.setattr("ai_commerce.remote.worker.load_approved_candidates", lambda *_a: {"version": 1, "candidates": {}})

    issuer_arg = "trung-via"
    if failure_mode == "role_name_human":
        issuer_arg = "HUMAN"
    elif failure_mode == "role_name_brain":
        issuer_arg = "BRAIN"
    elif failure_mode == "issuer_mismatch_with_issue":
        issuer_arg = "other-user"

    if failure_mode in ("role_name_human", "role_name_brain"):
        with pytest.raises(RemoteTransportError, match="WORK_INDEX_ISSUER_INVALID"):
            LocalBatchWorker(
                checkout=tmp_path, batch=p544,
                profiles={"ef02-tests": ["python", "-m", "pytest", "tests/test_ef02.py", "-q"]},
                github=storage, root=tmp_path / "ev", issue_number=544, issuer_login=issuer_arg
            )
        return

    worker = LocalBatchWorker(
        checkout=tmp_path, batch=p544,
        profiles={"ef02-tests": ["python", "-m", "pytest", "tests/test_ef02.py", "-q"]},
        github=storage, root=tmp_path / "ev", issue_number=544, issuer_login=issuer_arg
    )
    res = worker.execute_action(action)
    assert res["status"] == "HOLD_WORK_OWNER"
    assert res["error_code"] == expected_code
    assert res["executor_invoked"] is False
    assert res["replay_authorized"] is False
    # Zero claims, zero pre-spawn, zero spawned, zero workowner refs created
    assert not any("/claims/" in r or "/pre-spawn/" in r or "/spawned/" in r or "/workowner/" in r for r in storage.refs)


def test_r9_defect_b_idempotent_same_payload_and_concurrent_race(tmp_path):
    """Idempotent existing index and concurrent 422 race with identical payload succeed; conflicting payload fails."""
    from test_ef01_repair import WorkOwnerMemoryTransport
    from concurrent.futures import ThreadPoolExecutor
    storage = WorkOwnerMemoryTransport(BASE)
    p544 = _issue_544_packet(BASE)
    action = p544["actions"][0]
    storage.issues[544] = p544
    fp = hashlib.sha256(json.dumps({
        "batch_id": p544["batch_id"], "source_sha": BASE, "action": action
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    # Concurrent writers with identical payload all succeed via read-back verification
    def write_same():
        return storage.index_attempt(
            issue_number=544, source_sha=BASE, attempt_id=action["attempt_id"],
            fingerprint=fp, issuer="trung-via"
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: write_same(), range(6)))
    expected_ref = f"refs/heads/ef02/index/{storage.attempt_key(action['attempt_id'])}"
    assert all(r == expected_ref for r in results)

    # Subsequent idempotent call without explicit issuer derives from issue #544 and succeeds
    assert storage.index_attempt(
        issue_number=544, source_sha=BASE, attempt_id=action["attempt_id"],
        fingerprint=fp, issuer=None
    ) == expected_ref

