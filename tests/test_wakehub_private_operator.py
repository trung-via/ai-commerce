"""Adversarial OFFLINE simulations. No native signer/operator is installed.

Every approved proof and observation below is a synthetic fixture. The mock
journal models a protocol; its dict/lock are NOT durable Windows atomicity.
Consumer suites remain separate compatibility targets, not runtime callers.
"""
from __future__ import annotations

import ast
import builtins
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import socket
import subprocess
from threading import Lock
from uuid import uuid4

import pytest

from ai_commerce.engineering.three_runner_topology import planned_topology
from ai_commerce.wakehub import native_inventory as inv
from ai_commerce.wakehub import private_operator as op


T = 1800000000.0
SID = "S-1-5-21-111-222-333-1001"
USER = r"fixture-host\fixture-owner"
CHROME = r"C:\Private-Fixture\Chrome\chrome.exe"
PROFILE = r"C:\Private-Fixture\WakeHub-Dedicated"
ROOT = Path(__file__).resolve().parents[1]
ADDITIONS = (
    "src/ai_commerce/wakehub/private_operator.py",
    "tests/test_wakehub_private_operator.py",
    "work_items/mt01-components/wakehub-private-operator.json",
)
ACS = (
    "WAKEHUB.WH01B.lifecycle-typed-grants",
    "WAKEHUB.WH01B.lifecycle-unknown-reconcile",
    "WAKEHUB.WH01B.lifecycle-no-native-without-enrollment",
)
START = op.Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT
REUSE = op.Operation.REUSE


def key():
    return op.EffectKey(uuid4(), uuid4(), uuid4())


def identity(pid=200, executable=CHROME):
    return inv.ProcessIdentity(pid, T - 60, executable, SID, 7)


def synthetic_attestation(snapshot, enrollment, now):
    # Digest equality is ONLY a fixture, not enrolled signature verification.
    return inv.PrivateAttestation(inv._private_digest(snapshot),
        inv._private_digest(enrollment), now)


def rebuild(evidence, *, snapshot=None, enrollment=None):
    snapshot = evidence.snapshot if snapshot is None else snapshot
    enrollment = evidence.enrollment if enrollment is None else enrollment
    attestation = synthetic_attestation(snapshot, enrollment, evidence.attestation.verified_at)
    public = inv.normalize_private_inventory(snapshot, enrollment, attestation,
        now=evidence.attestation.verified_at)
    return replace(evidence, snapshot=snapshot, enrollment=enrollment,
        attestation=attestation, preflight=public.supervisor)


class Clock:
    def __init__(self):
        self.value = T

    def __call__(self):
        value = self.value
        self.value += 0.5
        return value


class SimulatedCrash(BaseException):
    pass


class MockJournal:
    """Process-local model ONLY. Production requires protected durable custody."""
    def __init__(self):
        self.record = op.JournalRecord(op.JournalState.EMPTY)
        self.actions, self.effects, self.keys = set(), set(), set()
        self.lock = Lock()
        self.reservations = 0
        self.fault = None
        self.complete_ack = True
        self.resolve_ack = True
        self.mark_fails = False
        self.read_fails_after_complete = False

    def read(self, grant):
        if self.read_fails_after_complete and self.record.state is op.JournalState.OBSERVED:
            raise TimeoutError("PRIVATE journal path/token must never leak")
        return self.record

    def reserve(self, intent, now):
        with self.lock:
            k = intent.grant.key
            if (self.record.state in (op.JournalState.PENDING, op.JournalState.UNKNOWN)
                    or k.action_id in self.actions or k.effect_id in self.effects
                    or k.one_shot_key in self.keys):
                return None
            self.actions.add(k.action_id)
            self.effects.add(k.effect_id)
            self.keys.add(k.one_shot_key)
            lease = op.PrivateLease(intent, uuid4(), intent.grant.expires_at)
            self.record = op.JournalRecord(op.JournalState.PENDING, lease)
            self.reservations += 1
            if self.fault:
                raise self.fault
            return lease

    def mark_unknown(self, intent):
        if self.mark_fails:
            raise OSError("PRIVATE storage unavailable")
        if self.record.state is op.JournalState.PENDING:
            assert self.record.lease.intent == intent
            self.record = replace(self.record, state=op.JournalState.UNKNOWN)

    def complete(self, lease, observation):
        assert self.record == op.JournalRecord(op.JournalState.PENDING, lease)
        if self.complete_ack:
            self.record = op.JournalRecord(op.JournalState.OBSERVED, lease, observation)
        return self.complete_ack

    def resolve(self, record, observation):
        assert record == self.record
        if self.resolve_ack:
            self.record = op.JournalRecord(op.JournalState.OBSERVED, record.lease, observation)
        return self.resolve_ack


class MockVerifier:
    """Synthetic enrolled-custody seam, never real Windows observed evidence."""
    def __init__(self, harness):
        self.h = harness
        self.revoked = False
        self.verify_count = 0
        self.read_count = 0
        self.read_hook = lambda number, evidence: evidence
        self.grant_hook = lambda number, grant: grant
        self.journal_trusted = True
        self.lease_trusted = True
        self.reconciliation = None

    def verify_grant(self, proof, operation, policy, now):
        self.verify_count += 1
        if proof != self.h.proof or self.revoked:
            return None
        return self.grant_hook(self.verify_count, self.h.grant)

    def read_verified(self, grant, now):
        self.read_count += 1
        h = self.h
        runners = tuple((identity(100 + i, s.root + r"\bin\Runner.Listener.exe"),)
                        for i, s in enumerate(planned_topology()))
        browser = inv.ChromeProcess(h.browser_identity, (9444,), ("127.0.0.1",), (PROFILE,))
        identities = tuple(g[0] for g in runners) + ((browser.identity,) if h.owned else ())
        snapshot = inv.PrivateSnapshot(inv.SOURCE_EPOCH, now - 0.1, USER, SID, 7,
            ("READY", "READY", "READY"), runners, (browser,) if h.owned else (),
            identities, identities,
            (inv.SocketListener("127.0.0.1", 9444, 200),) if h.owned else (), True)
        enrollment = inv.PrivateEnrollment(inv.SOURCE_EPOCH, USER, SID, 7, CHROME,
            PROFILE, T - 5, T + 25, True, False, True, False, False)
        attestation = synthetic_attestation(snapshot, enrollment, now)
        public = inv.normalize_private_inventory(snapshot, enrollment, attestation, now=now)
        evidence = op.PrivateEvidence(h.policy.source, snapshot, enrollment, attestation,
            public.supervisor, op.DesktopState.LIVE_UNLOCKED_OWNER, op.ReceiverState.ONLINE,
            op.PauseState.RELEASED_INDEPENDENTLY)
        return self.read_hook(self.read_count, evidence)

    def verify_journal(self, grant, record, now):
        return self.journal_trusted and record == self.h.journal.record

    def verify_lease(self, lease, now):
        return (self.lease_trusted and self.h.journal.record.lease == lease
                and self.h.journal.record.state is op.JournalState.PENDING
                and now < lease.expires_at)

    def reconcile(self, grant, record, now):
        return self.reconciliation


class MockExecutor:
    def __init__(self, harness):
        self.h = harness
        self.calls = []
        self.fault = None

    def _effect(self, name, lease):
        # Assert simulated write-ahead ordering and no user/native command args.
        assert self.h.journal.record == op.JournalRecord(op.JournalState.PENDING, lease)
        assert self.h.verifier.verify_lease(lease, self.h.clock.value - 0.5)
        self.calls.append((name, lease))
        if name == "start":
            self.h.owned = True
            self.h.browser_identity = replace(identity(), created_at=self.h.clock.value - 0.5)
        if self.fault:
            raise self.fault

    def start_dedicated(self, lease):
        self._effect("start", lease)

    def reuse_owned(self, lease):
        assert self.h.owned
        self._effect("reuse", lease)


class Harness:
    def __init__(self, operation=START):
        source = op.SourceBinding(op.PINNED_PARENT_SHA, 1, op.PINNED_PARENT_SHA, 1)
        self.policy = op.LifecyclePolicy(source)
        self.target = op.PrivateTarget(USER, SID, 7, CHROME, PROFILE)
        self.proof = op.PrivateGrantProof(b"offline-signature-fixture", b"offline-local-consent-fixture")
        self.grant = op.PrivateGrant(operation, op._scope(operation), source, self.target,
            key(), T - 2, T + 20, identity() if operation is REUSE else None,
            key() if operation is op.Operation.RECONCILE_ONLY else None)
        self.clock = Clock()
        self.browser_identity = identity()
        self.owned = operation is REUSE
        self.journal = MockJournal()
        self.verifier = MockVerifier(self)
        self.executor = MockExecutor(self)
        self.operator = self.new_operator()

    def new_operator(self):
        return op.PrivateLifecycleOperator(self.policy, verifier=self.verifier,
            executor=self.executor, journal=self.journal, clock=self.clock)

    def run(self):
        return self.operator.run(self.grant.operation, self.proof)

    def reconcile_grant(self):
        original = self.journal.record.lease.intent.grant
        self.grant = op.PrivateGrant(op.Operation.RECONCILE_ONLY,
            op.GrantScope.LOCAL_RECONCILIATION, original.source, original.target,
            key(), T - 2, T + 20, reconciles=original.key)


def assert_no_effect(h, result):
    assert result.decision in (op.Decision.HOLD, op.Decision.UNKNOWN_EFFECT)
    assert not h.executor.calls
    assert result.effect is op.EffectAttempt.NOT_ATTEMPTED


@pytest.mark.parametrize("operation", list(op.Operation))
def test_default_deny_and_no_authority_from_constructing_claims(operation):
    h = Harness(operation)
    operator = op.PrivateLifecycleOperator(h.policy, executor=h.executor, journal=h.journal)
    assert_no_effect(h, operator.run(operation, h.proof))
    assert_no_effect(h, operator.run(operation, h.grant))
    assert_no_effect(h, operator.run(operation))


@pytest.mark.parametrize("payload", [None, {}, "STATUS", "start chrome", 0, True])
def test_closed_request_surface(payload):
    h = Harness()
    result = h.operator.run(payload, h.proof)
    assert_no_effect(h, result)
    assert result.reason is op.Reason.INVALID_REQUEST
    assert h.verifier.verify_count == 0


@pytest.mark.parametrize("operation", [op.Operation.STATUS, REUSE, START])
def test_positive_simulated_operations_are_bounded_and_not_native_proof(operation):
    h = Harness(operation)
    result = h.run()
    expected = {op.Operation.STATUS: op.Decision.STATUS_OBSERVED,
                REUSE: op.Decision.REUSE_OBSERVED, START: op.Decision.START_OBSERVED}
    assert result.decision is expected[operation]
    assert [name for name, _ in h.executor.calls] == ([] if operation is op.Operation.STATUS
                                                     else ["reuse" if operation is REUSE else "start"])
    assert h.journal.reservations == (0 if operation is op.Operation.STATUS else 1)
    assert h.verifier.read_count == (1 if operation is op.Operation.STATUS else 3)
    public = json.loads(result.to_json())
    assert public["native_authority"] == "NOT_PROVEN_BY_SOURCE_OR_TEST_ADAPTER"
    assert public["wake"] == "NOT_SENT"
    assert public["deployment"] == "G2_SEPARATE_GRANT_REQUIRED"


@pytest.mark.parametrize("operation", [START, REUSE])
@pytest.mark.parametrize("pause", list(op.PauseState))
def test_pause_requires_independent_explicit_release(operation, pause):
    h = Harness(operation)
    h.verifier.read_hook = lambda n, e: replace(e, pause=pause)
    result = h.run()
    if pause is op.PauseState.RELEASED_INDEPENDENTLY:
        assert result.decision in (op.Decision.START_OBSERVED, op.Decision.REUSE_OBSERVED)
    else:
        assert_no_effect(h, result)
        assert result.reason is op.Reason.HUMAN_PAUSED
        assert h.journal.reservations == 0


@pytest.mark.parametrize(("field", "value"), [
    ("muted", True), ("read_allowed", False), ("interactive_session_proven", False),
    ("trusted_human_pause", True), ("unknown_effect", True),
    ("source_epoch", "f" * 40), ("user_name", "foreign-owner"),
    ("user_sid", "S-1-5-21-999-1001"), ("session_id", 8),
    ("chrome_executable", r"C:\Foreign\chrome.exe"),
    ("dedicated_profile", r"C:\Foreign\Profile"),
    ("issued_at", T + 1), ("expires_at", T - 1),
])
def test_enrollment_rejection_even_with_synthetic_matching_digests(field, value):
    h = Harness()
    def attack(n, e):
        changes = {field: value}
        if field == "expires_at":
            changes["issued_at"] = T - 30
        if field == "issued_at":
            changes["expires_at"] = T + 25
        return rebuild(e, enrollment=replace(e.enrollment, **changes))
    h.verifier.read_hook = attack
    assert_no_effect(h, h.run())


@pytest.mark.parametrize(("field", "value"), [
    ("source_epoch", "f" * 40), ("user_name", "foreign-owner"),
    ("user_sid", "S-1-5-21-999-1001"), ("session_id", 0), ("session_id", 8),
    ("observed_at", T - 31), ("observed_at", T + 1), ("complete", False),
])
def test_snapshot_identity_partial_read_and_staleness(field, value):
    h = Harness()
    h.verifier.read_hook = lambda n, e: rebuild(e, snapshot=replace(e.snapshot, **{field: value}))
    assert_no_effect(h, h.run())


@pytest.mark.parametrize("desktop", [op.DesktopState.LOCKED, op.DesktopState.UNPROVEN])
def test_unlocked_owning_desktop_is_required(desktop):
    h = Harness()
    h.verifier.read_hook = lambda n, e: replace(e, desktop=desktop)
    assert_no_effect(h, h.run())


@pytest.mark.parametrize("receiver", [op.ReceiverState.OFFLINE, op.ReceiverState.MUTED,
                                     op.ReceiverState.UNPROVEN])
def test_process_presence_is_not_online_event_receiver(receiver):
    h = Harness()
    h.verifier.read_hook = lambda n, e: replace(e, receiver=receiver)
    result = h.run()
    assert_no_effect(h, result)
    assert result.reason is op.Reason.RECEIVER_UNAVAILABLE


def test_online_claim_cannot_override_absent_hostops_process():
    h = Harness()
    def absent_receiver(n, e):
        s = e.snapshot
        identities = tuple(p for p in s.initial_processes if p.pid != 102)
        return rebuild(e, snapshot=replace(s, runner_processes=(*s.runner_processes[:2], ()),
            initial_processes=identities, confirmed_processes=identities))
    h.verifier.read_hook = absent_receiver
    assert_no_effect(h, h.run())


@pytest.mark.parametrize(("field", "value"), [
    ("pid", 999), ("created_at", T - 59), ("user_sid", "S-1-5-21-999-1001"),
    ("session_id", 0), ("session_id", 8), ("executable", r"C:\Foreign\chrome.exe"),
])
def test_reuse_requires_exact_process_identity_across_fresh_reread(field, value):
    h = Harness(REUSE)
    def change(n, e):
        if n != 2:
            return e
        s = e.snapshot
        old = s.chrome_processes[0]
        new = replace(old.identity, **{field: value})
        identities = tuple(new if p == old.identity else p for p in s.initial_processes)
        listeners = (replace(s.listeners[0], pid=new.pid),)
        return rebuild(e, snapshot=replace(s,
            chrome_processes=(replace(old, identity=new),), initial_processes=identities,
            confirmed_processes=identities, listeners=listeners))
    h.verifier.read_hook = change
    assert_no_effect(h, h.run())
    assert h.journal.record.state is op.JournalState.UNKNOWN
    assert_no_effect(h, h.new_operator().run(REUSE, h.proof))


@pytest.mark.parametrize(("field", "value"), [
    ("ports", (9333,)), ("ports", (9222,)), ("ports", (9444, 9444)),
    ("addresses", ("0.0.0.0",)), ("addresses", ("::1",)),
    ("profiles", (r"C:\Foreign\Profile",)), ("profiles", (PROFILE, PROFILE)),
])
def test_wrong_port_profile_loopback_and_conflict_cannot_attach(field, value):
    h = Harness(REUSE)
    h.verifier.read_hook = lambda n, e: rebuild(e, snapshot=replace(e.snapshot,
        chrome_processes=(replace(e.snapshot.chrome_processes[0], **{field: value}),)))
    assert_no_effect(h, h.run())


def test_duplicate_profile_owner_and_partial_process_confirmation_hold():
    for collision in (True, False):
        h = Harness(REUSE)
        def attack(n, e):
            s = e.snapshot
            return rebuild(e, snapshot=replace(s,
                chrome_processes=s.chrome_processes * 2 if collision else s.chrome_processes,
                confirmed_processes=s.confirmed_processes if collision else s.confirmed_processes[:-1]))
        h.verifier.read_hook = attack
        assert_no_effect(h, h.run())


@pytest.mark.parametrize("operation", [START, REUSE])
def test_operation_never_falls_through_to_another_effect(operation):
    h = Harness(operation)
    h.owned = operation is START  # START sees owned; REUSE sees absent.
    result = h.run()
    assert_no_effect(h, result)
    assert result.reason is (op.Reason.ABSENCE_REQUIRED if operation is START
                             else op.Reason.OWNERSHIP_REQUIRED)


def test_http_failure_is_not_absence_and_foreign_socket_conflicts():
    h = Harness()
    h.verifier.read_hook = lambda n, e: rebuild(e, snapshot=replace(e.snapshot,
        listeners=(inv.SocketListener("127.0.0.1", 9444, 999),)))
    assert_no_effect(h, h.run())
    h.verifier.read_hook = lambda n, e: None  # No independent native read.
    assert_no_effect(h, h.run())


@pytest.mark.parametrize(("field", "value"), [
    ("source_sha", "a" * 40), ("release_sha", "b" * 40),
    ("source_epoch", 2), ("release_epoch", 2),
])
def test_exact_source_release_and_epochs_required(field, value):
    h = Harness()
    h.grant = replace(h.grant, source=replace(h.grant.source, **{field: value}))
    assert_no_effect(h, h.run())
    h = Harness()
    h.verifier.read_hook = lambda n, e: replace(e, source=replace(e.source, **{field: value}))
    assert_no_effect(h, h.run())


@pytest.mark.parametrize("mutation", ["expired", "future", "revoked", "wrong_operation",
                                      "wrong_owner", "wrong_session", "wrong_profile"])
def test_grant_mismatch_and_revocation_before_reservation(mutation):
    h = Harness()
    if mutation == "revoked":
        h.verifier.revoked = True
    elif mutation == "expired":
        h.grant = replace(h.grant, issued_at=T - 30, expires_at=T)
    elif mutation == "future":
        h.grant = replace(h.grant, issued_at=T + 1, expires_at=T + 20)
    elif mutation == "wrong_operation":
        h.verifier.grant_hook = lambda n, g: replace(g, operation=op.Operation.STATUS,
            scope=op.GrantScope.PRIVATE_READ)
    else:
        changes = {"wrong_owner": {"user_sid": "S-1-5-21-999-1001"},
                   "wrong_session": {"session_id": 8},
                   "wrong_profile": {"dedicated_profile": r"C:\Foreign\Profile"}}[mutation]
        h.grant = replace(h.grant, target=replace(h.target, **changes))
    assert_no_effect(h, h.operator.run(START, h.proof))
    assert h.journal.reservations == 0


@pytest.mark.parametrize("when", [2, 3])
def test_revocation_or_expiry_during_effect_flow_is_unknown(when):
    h = Harness()
    h.verifier.grant_hook = lambda n, g: None if n >= when else g
    result = h.run()
    assert result.decision is op.Decision.UNKNOWN_EFFECT
    assert len(h.executor.calls) == (0 if when == 2 else 1)
    h.verifier.grant_hook = lambda n, g: g
    h.new_operator().run(START, h.proof)
    assert len(h.executor.calls) == (0 if when == 2 else 1)


def test_fake_self_signed_proof_and_fake_digest_attestation_do_not_grant_authority():
    h = Harness()
    fake = op.PrivateGrantProof(b"attacker self-signed", b"github owner comment")
    assert_no_effect(h, h.operator.run(START, fake))
    assert h.verifier.read_count == 0
    h.verifier.read_hook = lambda n, e: replace(e, attestation=inv.PrivateAttestation(
        "0" * 64, "0" * 64, T))
    assert_no_effect(h, h.run())


def test_forged_public_inventory_and_supervisor_are_never_authority():
    h = Harness()
    e = h.verifier.read_verified(h.grant, T)
    public = inv.normalize_private_inventory(e.snapshot, e.enrollment, e.attestation, now=T)
    assert_no_effect(h, h.operator.run(START, public))
    assert_no_effect(h, h.operator.run(START, public.supervisor))
    h.verifier.read_hook = lambda n, e: replace(e, preflight=replace(e.preflight,
        start_permitted=True, wake_send_authorized=True))
    assert_no_effect(h, h.run())


@pytest.mark.parametrize("field", ["action_id", "effect_id", "one_shot_key"])
@pytest.mark.parametrize("operation", [START, REUSE])
def test_completed_first_wins_keys_never_replay(field, operation):
    h = Harness(operation)
    assert h.run().decision in (op.Decision.START_OBSERVED, op.Decision.REUSE_OBSERVED)
    previous = h.grant.key
    h.grant = replace(h.grant, key=replace(key(), **{field: getattr(previous, field)}))
    if operation is START:
        h.owned = False  # Even later absence cannot authorize replay.
    result = h.new_operator().run(operation, h.proof)
    assert result.reason is op.Reason.DUPLICATE
    assert len(h.executor.calls) == 1
    assert h.journal.reservations == 1


@pytest.mark.parametrize("fault", [TimeoutError("PRIVATE lost ACK"), RuntimeError("PRIVATE crash")])
@pytest.mark.parametrize("operation", [START, REUSE])
def test_timeout_after_effect_never_second_start_or_attach(operation, fault):
    h = Harness(operation)
    h.executor.fault = fault
    result = h.run()
    assert result.decision is op.Decision.UNKNOWN_EFFECT
    assert result.effect is op.EffectAttempt.ATTEMPTED
    assert h.journal.record.state is op.JournalState.UNKNOWN
    h.executor.fault = None
    if operation is START:
        h.owned = False
    h.new_operator().run(operation, h.proof)
    h.grant = replace(h.grant, key=key())
    assert h.new_operator().run(operation, h.proof).decision is op.Decision.UNKNOWN_EFFECT
    assert len(h.executor.calls) == 1


@pytest.mark.parametrize("fault", [TimeoutError("PRIVATE reservation ACK"), SimulatedCrash()])
@pytest.mark.parametrize("operation", [START, REUSE])
def test_crash_after_durable_mock_reservation_is_not_retried(fault, operation):
    h = Harness(operation)
    h.journal.fault = fault
    if isinstance(fault, SimulatedCrash):
        with pytest.raises(SimulatedCrash):
            h.run()
    else:
        assert_no_effect(h, h.run())
    assert h.journal.record.state in (op.JournalState.PENDING, op.JournalState.UNKNOWN)
    h.journal.fault = None
    h.grant = replace(h.grant, key=key())
    assert_no_effect(h, h.new_operator().run(operation, h.proof))
    assert h.journal.reservations == 1


def test_pending_lane_survives_failed_unknown_write_and_expired_lease():
    h = Harness()
    h.journal.mark_fails = True
    h.journal.fault = TimeoutError("PRIVATE lost lease")
    assert_no_effect(h, h.run())
    assert h.journal.record.state is op.JournalState.PENDING
    h.journal.fault = None
    h.clock.value = T + 21
    h.grant = replace(h.grant, key=key(), issued_at=T + 20, expires_at=T + 24)
    assert_no_effect(h, h.new_operator().run(START, h.proof))
    assert h.journal.reservations == 1


@pytest.mark.parametrize("failure", ["lease", "journal", "completion", "readback",
                                    "reread", "after_read", "clock_back", "clock_expiry"])
def test_protected_evidence_and_storage_fail_closed(failure):
    h = Harness()
    if failure == "lease":
        h.verifier.lease_trusted = False
    elif failure == "journal":
        h.verifier.journal_trusted = False
    elif failure == "completion":
        h.journal.complete_ack = False
    elif failure == "readback":
        h.journal.read_fails_after_complete = True
    elif failure in ("reread", "after_read"):
        number = 2 if failure == "reread" else 3
        h.verifier.read_hook = lambda n, e: None if n == number else e
    else:
        values = iter([T, T - 1 if failure == "clock_back" else T + 21])
        h.operator = op.PrivateLifecycleOperator(h.policy, verifier=h.verifier,
            executor=h.executor, journal=h.journal, clock=lambda: next(values))
    result = h.run()
    assert result.decision in (op.Decision.HOLD, op.Decision.UNKNOWN_EFFECT)
    h.journal.read_fails_after_complete = False
    h.operator = h.new_operator()
    if h.owned:
        h.owned = False
    h.run()
    assert len(h.executor.calls) <= 1


def test_reread_must_be_fresh_not_replayed():
    h = Harness()
    first = []
    def replay(n, e):
        first.append(e)
        return first[0]
    h.verifier.read_hook = replay
    assert_no_effect(h, h.run())
    assert h.journal.record.state is op.JournalState.UNKNOWN


@pytest.mark.parametrize("read_number", [1, 2, 3])
def test_grant_expiry_during_private_read_is_checked_at_completion(read_number):
    h = Harness()
    def delayed(n, evidence):
        if n == read_number:
            h.clock.value = T + 21
        return evidence
    h.verifier.read_hook = delayed
    result = h.run()
    assert result.decision is (op.Decision.HOLD if read_number == 1 else op.Decision.UNKNOWN_EFFECT)
    assert len(h.executor.calls) == (1 if read_number == 3 else 0)
    if read_number != 1:
        assert h.journal.record.state is op.JournalState.UNKNOWN


def test_start_cannot_claim_an_old_process_as_its_observed_result():
    h = Harness()
    def old_process(n, evidence):
        if n != 3:
            return evidence
        s = evidence.snapshot
        browser = s.chrome_processes[0]
        old = replace(browser.identity, created_at=T - 1)
        identities = tuple(old if p == browser.identity else p for p in s.initial_processes)
        return rebuild(evidence, snapshot=replace(s,
            chrome_processes=(replace(browser, identity=old),),
            initial_processes=identities, confirmed_processes=identities))
    h.verifier.read_hook = old_process
    assert h.run().decision is op.Decision.UNKNOWN_EFFECT
    h.owned = False
    assert h.new_operator().run(START, h.proof).decision is op.Decision.UNKNOWN_EFFECT
    assert len(h.executor.calls) == 1


def test_mock_atomic_reservation_has_one_first_winner_only():
    h = Harness()
    intent = op.PrivateIntent(h.grant)
    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = list(pool.map(lambda _: h.journal.reserve(intent, T), range(24)))
    assert sum(lease is not None for lease in leases) == 1
    assert h.journal.reservations == 1
    assert not h.executor.calls  # Model test, no claim about durable native storage.


@pytest.mark.parametrize("outcome", list(op.ObservedOutcome))
def test_reconcile_only_uses_independently_observed_original_outcome(outcome):
    operation = REUSE if outcome is op.ObservedOutcome.REUSE_OBSERVED else START
    h = Harness(operation)
    lease = h.journal.reserve(op.PrivateIntent(h.grant), T)
    h.journal.mark_unknown(lease.intent)
    h.reconcile_grant()
    h.verifier.reconciliation = op.ObservedEffect(lease, outcome, T - 0.1,
        None if outcome is op.ObservedOutcome.NO_EFFECT_OBSERVED else
        identity() if operation is REUSE else replace(identity(), created_at=T - 1))
    result = h.run()
    assert result.decision is op.Decision.RECONCILED
    assert result.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert not h.executor.calls
    original = lease.intent.grant
    h.grant = original
    h.operator = h.new_operator()
    h.owned = operation is REUSE
    assert h.run().reason is op.Reason.DUPLICATE
    assert not h.executor.calls


@pytest.mark.parametrize("gap", ["no_observation", "wrong_key", "wrong_source", "wrong_target",
                                "wrong_lease", "future_observation", "untrusted_journal",
                                "resolve_ack", "empty"])
def test_reconcile_evidence_gaps_hold_and_never_infer_from_browser_presence(gap):
    h = Harness()
    lease = h.journal.reserve(op.PrivateIntent(h.grant), T)
    h.reconcile_grant()
    observation = op.ObservedEffect(lease, op.ObservedOutcome.START_OBSERVED, T - 0.1,
        replace(identity(), created_at=T - 1))
    h.verifier.reconciliation = observation
    h.owned = True  # Presence alone cannot establish this operation's outcome.
    if gap == "no_observation":
        h.verifier.reconciliation = None
    elif gap == "wrong_key":
        h.grant = replace(h.grant, reconciles=key())
    elif gap == "wrong_source":
        old = lease.intent.grant
        foreign = replace(lease, intent=op.PrivateIntent(replace(old,
            source=replace(old.source, release_epoch=2))))
        h.journal.record = op.JournalRecord(op.JournalState.UNKNOWN, foreign)
    elif gap == "wrong_target":
        h.grant = replace(h.grant, target=replace(h.target, session_id=8))
    elif gap == "wrong_lease":
        h.verifier.reconciliation = replace(observation, lease=replace(lease, lease_id=uuid4()))
    elif gap == "future_observation":
        h.verifier.reconciliation = replace(observation, observed_at=T + 10)
    elif gap == "untrusted_journal":
        h.verifier.journal_trusted = False
    elif gap == "resolve_ack":
        h.journal.resolve_ack = False
    else:
        h.journal.record = op.JournalRecord(op.JournalState.EMPTY)
    assert_no_effect(h, h.run())


def test_status_preserves_pause_and_unknown_without_writes():
    h = Harness(op.Operation.STATUS)
    h.verifier.read_hook = lambda n, e: rebuild(replace(e, pause=op.PauseState.PAUSED),
        enrollment=replace(e.enrollment, trusted_human_pause=True))
    assert h.run().decision is op.Decision.STATUS_OBSERVED
    assert h.journal.reservations == 0 and not h.executor.calls
    h = Harness()
    h.journal.reserve(op.PrivateIntent(h.grant), T)
    original = h.grant
    h.grant = op.PrivateGrant(op.Operation.STATUS, op.GrantScope.PRIVATE_READ,
        original.source, original.target, key(), T - 2, T + 20)
    assert h.run().decision is op.Decision.UNKNOWN_EFFECT
    assert not h.executor.calls and h.journal.reservations == 1


@pytest.mark.parametrize(("kind", "changes"), [
    ("target", {"session_id": 0}), ("target", {"session_id": True}),
    ("target", {"desktop": r"WinSta0\Other"}), ("target", {"address": "0.0.0.0"}),
    ("target", {"port": 9333}), ("target", {"chrome_executable": r"C:\cmd.exe"}),
    ("target", {"dedicated_profile": r"C:\Profile\..\Other"}),
    ("source", {"source_ref": "refs/pull/512/head"}),
    ("source", {"parent_sha": "f" * 40}), ("source", {"source_epoch": True}),
    ("grant", {"scope": "INSTALLER"}), ("grant", {"operation": "START"}),
    ("grant", {"scope": op.GrantScope.PRIVATE_READ}),
    ("grant", {"expires_at": T + 29}), ("grant", {"issued_at": float("nan")}),
    ("grant", {"expected_process": identity()}), ("grant", {"reconciles": key()}),
    ("grant", {"key": {"action_id": "github issue"}}),
])
def test_strict_immutable_closed_models_reject_malformed_claims(kind, changes):
    h = Harness()
    value = {"target": h.target, "source": h.policy.source, "grant": h.grant}[kind]
    with pytest.raises(ValueError, match="^WAKEHUB_LIFECYCLE_INPUT_INVALID$"):
        replace(value, **changes)
    with pytest.raises(FrozenInstanceError):
        h.grant.issued_at = T
    with pytest.raises(ValueError):
        op.PrivateGrantProof(b"", b"consent")
    with pytest.raises(ValueError):
        op.PrivateGrantProof(b"x" * 8193, b"consent")


@pytest.mark.parametrize("operation", [START, REUSE])
def test_public_receipt_contains_no_private_or_arbitrary_data(operation):
    h = Harness(operation)
    h.executor.fault = TimeoutError(USER + SID + CHROME + PROFILE + " token secret chat-url ws://private")
    receipt = h.run()
    payload = receipt.to_json()
    for secret in (USER, SID, CHROME, PROFILE, "token", "secret", "chat-url", "ws://",
                   str(h.grant.key.action_id), op.PINNED_PARENT_SHA):
        assert secret not in payload
    assert all(type(value) is str for value in json.loads(payload).values())
    assert not any(secret in repr(h.grant) for secret in (SID, CHROME, PROFILE))
    with pytest.raises(ValueError, match="^WAKEHUB_LIFECYCLE_INPUT_INVALID$"):
        replace(receipt, reason=SID).to_json()


def test_import_has_no_native_or_io_effects(monkeypatch):
    # Compile already-read source first, then execute it with OS/IO blocked.
    source = (ROOT / ADDITIONS[0]).read_text(encoding="utf-8")
    code = compile(source, ADDITIONS[0], "exec")
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected import-time native/IO effect")
    for owner, name in ((subprocess, "run"), (subprocess, "Popen"), (os, "system"),
                        (socket, "socket"), (builtins, "open"), (Path, "open")):
        monkeypatch.setattr(owner, name, forbidden)
    # Use a registered module name so dataclasses resolve annotations normally.
    namespace = {"__name__": op.__name__}
    exec(code, namespace)
    tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "ai_commerce.wakehub.native_inventory" in imports
    assert not {"subprocess", "os", "socket", "playwright"} & imports
    assert not hasattr(op.PrivateExecutor, "send")
    assert not hasattr(op.PrivateExecutor, "run_command")


def test_exact_new_owner_is_admitted_by_pinned_trusted_registry_with_consumer_closure():
    """Exercise trusted registration, not a handwritten argv/owner substitute."""
    from ai_commerce.engineering.impact_v21 import (
        load_baseline_component_owner_records, registered_action_plan,
        validate_component_onboarding_manifest,
    )
    from ai_commerce.engineering.minimum_test import approved_groups, file_hashes

    # Read-only Git: the executor does not create refs or publish candidates.
    base = subprocess.run(["git", "rev-parse", op.PINNED_PARENT_SHA], cwd=ROOT,
        check=True, capture_output=True, text=True).stdout.strip()
    assert base == op.PINNED_PARENT_SHA
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text(encoding="utf-8"))
    assert profiles["mt01-component-onboarding"] == [
        "python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    manifest = json.loads((ROOT / ADDITIONS[2]).read_text(encoding="utf-8"))
    assert manifest == {
        "schema_version": "MT01ComponentOwner.v1", "component": "wakehub-private-operator",
        "sources": [ADDITIONS[0]], "tests": [ADDITIONS[1]], "acs": list(ACS),
        "consumers": ["src/ai_commerce/wakehub/native_inventory.py",
                      "src/ai_commerce/engineering/three_runner_topology.py"], "contracts": [],
    }
    groups = approved_groups()
    records = load_baseline_component_owner_records(root=ROOT, base_sha=base, existing_groups=groups)
    baseline_groups = (*groups, *(r["group"] for r in records))
    # Trusted persisted ownership proves both existing sources, without claiming
    # either currently imports/calls this new policy.
    for consumer in manifest["consumers"]:
        assert any(consumer in g.paths for g in baseline_groups)
        assert (ROOT / consumer).is_file()
    candidate = file_hashes(ROOT)
    base_files = {p: value for p, value in candidate.items() if p not in ADDITIONS}
    admission = validate_component_onboarding_manifest(manifest, manifest_path=ADDITIONS[2],
        changed_paths=ADDITIONS, base_paths=tuple(base_files), existing_groups=baseline_groups,
        allowed_paths=ADDITIONS, root=ROOT)
    assert admission["status"] == "VALIDATED_DATA_ONLY_NOT_VERIFICATION_PASS"
    plan = registered_action_plan(repo=ROOT, base_sha=base, base_files=base_files,
        candidate_files=candidate, changed_paths=ADDITIONS, required_acs=ACS,
        fallback_argv=profiles["mt01-component-onboarding"], groups=groups,
        registered_profiles=profiles, allowed_paths=ADDITIONS)
    assert all(entry["execute"] for entry in plan["groups"]), plan["reason_codes"]
    by_name = {entry["group"]: entry for entry in plan["groups"]}
    assert set(by_name) == {"wakehub-private-operator", "wakehub-native-inventory", "three-runner-topology"}
    assert set(plan["impact_v21"]["selected_tests"]) == {
        ADDITIONS[1], "tests/test_wakehub_native_inventory.py", "tests/test_three_runner_topology.py"}
    for entry in by_name.values():
        assert entry["binding"]["argv"][-3:] == ["-q", "-n", "12"]
    assert by_name["wakehub-private-operator"]["binding"]["onboarding_data_only"] is True


def test_existing_inventory_and_supervisor_remain_read_only_compatibility_targets():
    h = Harness(REUSE)
    evidence = h.verifier.read_verified(h.grant, T)
    public = inv.normalize_private_inventory(evidence.snapshot, evidence.enrollment,
        evidence.attestation, now=T)
    assert public.chrome_ownership == "OWNED_CDP_9444"
    assert public.supervisor == evidence.preflight
    assert not public.supervisor.start_permitted
    assert not public.supervisor.wake_send_authorized
    assert not public.supervisor.effect_attempted
    assert not json.loads(public.to_json())["start_permitted"]
