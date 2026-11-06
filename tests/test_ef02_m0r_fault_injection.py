"""M0-R deterministic negative-path fault injections: never call real providers.

These tests model faults at EF02 boundary methods on an isolated worker; they
do not assert that a live Windows runner was taken offline or cancelled.
"""
from __future__ import annotations

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
    storage.commits[claim_sha] = {"message": f"EF02 CLAIM\n\nfingerprint={fp506}\nattempt_id=target-attempt"}
    with pytest.raises(RemoteTransportError, match="WORK_LINEAGE_CYCLE_DETECTED"):
        WorkOwner.identity(storage, 508, storage.issues[508], storage.issues[508]["actions"][0])


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
    owner1.append("CLAIMED")
    owner1.append("UNKNOWN")

    fp1 = hashlib.sha256(json.dumps({"batch_id": "b-root", "source_sha": BASE, "action": action1},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    claim_key1 = storage.attempt_key("attempt-r1")
    claim_sha1 = hashlib.sha1(b"claim1").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key1}"] = claim_sha1
    storage.commits[claim_sha1] = {"message": f"EF02 CLAIM\n\nfingerprint={fp1}\nattempt_id=attempt-r1"}

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
    owner1.append("CLAIMED")
    owner1.append("UNKNOWN")

    fp1 = hashlib.sha256(json.dumps({"batch_id": "b1", "source_sha": BASE, "action": action1},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    claim_key1 = storage.attempt_key("attempt-r1")
    claim_sha1 = hashlib.sha1(b"c1").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key1}"] = claim_sha1
    storage.commits[claim_sha1] = {"message": f"EF02 CLAIM\n\nfingerprint={fp1}\nattempt_id=attempt-r1"}

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
    """Unresolved predecessor (e.g. at PROVIDER_INTENT) denies generation transfer."""
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
    storage.commits[claim_sha1] = {"message": f"EF02 CLAIM\n\nfingerprint={fp1}\nattempt_id=attempt-r1"}

    action2 = {**_action(), "attempt_id": "attempt-r2", "repair_of": "#506"}
    packet2 = {"batch_id": "b2", "source_sha": BASE, "actions": [action2]}
    storage.issues[508] = packet2
    storage.issue_body = packet2
    id2 = WorkOwner.identity(storage, 508, packet2, action2)

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
    owner1.append("CLAIMED")
    owner1.append("UNKNOWN")

    fp1 = hashlib.sha256(json.dumps({"batch_id": "b1", "source_sha": BASE, "action": action1},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    claim_key1 = storage.attempt_key("attempt-r1")
    claim_sha1 = hashlib.sha1(b"c1").hexdigest()
    storage.refs[f"refs/heads/ef02/claims/{claim_key1}"] = claim_sha1
    storage.commits[claim_sha1] = {"message": f"EF02 CLAIM\n\nfingerprint={fp1}\nattempt_id=attempt-r1"}

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
