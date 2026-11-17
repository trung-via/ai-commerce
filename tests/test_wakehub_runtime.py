"""WAKEHUB.runtime-r0: R0/R1/R2/R3A/R3B1 source closure, never native host proof.

Only custody observation is stubbed for deterministic cross-OS temporary files.
Windows fixture files inherit an explicit process-SID grant, rather than relying
on the temporary directory's potentially non-inheritable OWNER RIGHTS grant.
Private SQLite durability, provenance checks, actual A03 consumer and P0
receipt validation execute normally. No Git, browser, login or external writes.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import hmac
import io
import json
import os
import socket
import sqlite3
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from ai_commerce.production_orchestration.contracts import ProductionStageReceipt, digest
from ai_commerce.production_orchestration.orchestrator import ProductionPipelineOrchestrator
from ai_commerce.remote.chrome_acquisition import ChromeEnvironment
from ai_commerce.remote.completion_event import build_completion_event
from ai_commerce.remote.conversation_binding import ResolvedBinding
from ai_commerce.remote.local_wake_trigger import PinnedRun
from ai_commerce.remote.wake_adapter import ProvenanceError
from ai_commerce.wakehub_runtime import runner as r
from ai_commerce.wakehub_runtime.__main__ import main


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("External operation attempted")
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)


@pytest.fixture
def composition(tmp_path, monkeypatch):
    # Synthetic custody, explicitly not a native security or ACL attestation.
    # Windows file creation still needs the actual current token SID.
    if os.name == "nt":
        from ai_commerce.remote.local_wake_entry import _windows_process_sid
        principal = _windows_process_sid()
    else:
        principal = "uid:" + str(os.geteuid())
    monkeypatch.setattr(r, "_private_identity", lambda path: principal)
    private = tmp_path / "synthetic-private"
    private.mkdir(mode=0o700)
    if os.name == "nt":
        # Provision only this newly created fixture directory. A custody stub
        # cannot supply the access needed by D5's real lock/fsync/replace calls.
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
                            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
        convert.restype = wintypes.BOOL
        apply = advapi.SetFileSecurityW
        apply.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
        apply.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        descriptor = ctypes.c_void_p()
        assert convert(f"O:{principal}D:P(A;OICI;FA;;;{principal})", 1,
                       ctypes.byref(descriptor), None), "fixture security descriptor failed"
        try:
            assert apply(str(private), 0x80000005, descriptor), "fixture ACL provisioning failed"
        finally:
            kernel.LocalFree(descriptor)
    root = tmp_path / "checkout"
    root.mkdir()
    registration = r.Registration(ResolvedBinding("fixture-chat-a", 1,
        "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001", "http://127.0.0.1:9444"),
        "a" * 40, "SYNTHETIC_FIXTURE")
    event = r.Event("ef02-completion-v1:" + "b" * 64, registration.source_sha,
                    1, "c" * 64, "SYNTHETIC_FIXTURE")
    path = private / "runtime.sqlite3"
    handoff = r.synthetic_handoff(event, registration)
    spec, receipt = handoff.completed.spec, handoff.receipt

    def make(**overrides):
        orchestrator = ProductionPipelineOrchestrator(
            authorized_specs=((spec.authorization.authorization_ref, spec.sha256),),
            verified_receipts=((receipt.consumer_id, receipt.sha256),))
        params = dict(source=r.SyntheticSource(event), registration=registration,
                      journal=r.Journal(path, root), sender=r.SyntheticSender(),
                      brain=r.SyntheticBrain(handoff), orchestrator=orchestrator,
                      grant_verifier=lambda e, registration: True)
        return r.Runner(**{**params, **overrides})

    return make, event, registration, handoff, path, root


def record(path, event, registration):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        return json.loads(db.execute("SELECT record FROM events WHERE event_key=?",
                                     (event.key(registration),)).fetchone()[0])
    finally:
        db.close()


def test_composition_persists_each_state_and_typed_consumer_receipt(composition):
    make, event, registration, handoff, path, _ = composition
    runner = make()
    result = runner.run_once()
    assert result == r.Result("ACK", "CONSUMER_RECEIPT_RECORDED", "SYNTHETIC_FIXTURE")
    assert runner.sender.calls == 1
    saved = record(path, event, registration)
    assert saved["history"] == ["PENDING", "RESERVED", "UI_TURN_OBSERVED", "BRAIN_CORRELATED", "ACK"]
    assert saved["evidence"]["consumer_id"] == handoff.completed.spec.consumer_id
    assert saved["evidence"]["receipt_sha256"] == handoff.receipt.sha256
    assert type(handoff.receipt) is ProductionStageReceipt
    assert handoff.receipt.provenance.evidence_type == "SYNTHETIC_FIXTURE"
    assert runner.orchestrator.dispatch(event.key(registration)).event.receipt == handoff.receipt


@pytest.mark.skipif(os.name != "nt", reason="Windows fixture ACL inheritance")
def test_windows_fixture_grants_real_access_to_journal_and_lock(composition):
    from ai_commerce.remote.local_completion_handoff import _private_identity

    make, _, _, _, path, _ = composition
    runner = make()
    assert runner.run_once().status == "ACK"
    # Bypass the synthetic custody observation for these test-owned objects.
    # This validates fixture inheritance, never deployment or host readiness.
    principal = _private_identity(path.parent)
    assert _private_identity(path) == principal
    assert _private_identity(path.with_name(path.name + ".lock")) == principal
    assert make().run_once().replayed


def test_duplicate_delivery_and_reopen_do_not_resend_or_handoff(composition):
    make, event, registration, _, path, _ = composition
    runner = make()
    assert runner.run_once().status == "ACK"
    first_bytes = path.read_bytes()
    assert runner.run_once().replayed and runner.sender.calls == 1
    reopened = make()
    result = reopened.run_once()
    assert result.status == "ACK" and result.replayed and reopened.sender.calls == 0
    # A new P0 object receives no duplicate handoff: the durable journal owns it.
    assert reopened.orchestrator._completed == {}
    assert path.read_bytes() == first_bytes


class Crash(BaseException):
    pass


@pytest.mark.parametrize("phase", ["RESERVED", "UI_TURN_OBSERVED", "BRAIN_CORRELATED"])
def test_crash_recovery_never_replays_ambiguous_effect(composition, phase, monkeypatch):
    make, event, registration, _, path, _ = composition
    runner = make()

    def crash(*args, **kwargs):
        assert record(path, event, registration)["status"] == phase
        raise Crash()

    if phase == "RESERVED":
        monkeypatch.setattr(runner.sender, "send", crash)
    elif phase == "UI_TURN_OBSERVED":
        monkeypatch.setattr(runner.brain, "correlate", crash)
    else:
        # Correlation is now journaled only after P0 verifies both pins.
        monkeypatch.setattr(runner, "_finish", crash)
    with pytest.raises(Crash):
        runner.run_once()
    recovered = make()
    result = recovered.run_once()
    assert result == r.Result("UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY", "SYNTHETIC_FIXTURE", True)
    assert recovered.sender.calls == 0 and recovered.orchestrator._completed == {}
    assert make().run_once().status == "UNKNOWN_EFFECT"


def test_pending_recovery_is_safe_before_reservation(composition, monkeypatch):
    make, event, registration, _, path, _ = composition
    runner = make()

    def crash(*args):
        assert record(path, event, registration)["status"] == "PENDING"
        raise Crash()

    monkeypatch.setattr(runner.sender, "preflight", crash)
    with pytest.raises(Crash):
        runner.run_once()
    recovered = make()
    assert recovered.run_once().status == "ACK" and recovered.sender.calls == 1


@pytest.mark.parametrize("drift", ["chat", "generation", "source", "marker", "event", "turn", "status", "brain"])
def test_sender_observation_is_not_brain_success(composition, drift, monkeypatch):
    make, _, _, _, _, _ = composition
    runner = make()
    original = runner.sender.send

    def wrong(event, registration):
        observation = original(event, registration)
        receipt = observation.receipt
        if drift == "chat":
            return replace(observation, chat_url=registration.binding.chat_url[:-1] + "2")
        if drift == "marker":
            return replace(observation, marker="other-generation")
        if drift == "turn":
            return replace(observation, user_turn_ref="")
        values = {"generation": {"generation": 2}, "source": {"route_id": "different-route"},
                  "event": {"event_id": "ef02-completion-v1:" + "d" * 64},
                  "status": {"delivery_status": "CONFIRMED"},
                  "brain": {"brain_continuation_proven": True}}
        return replace(observation, receipt=replace(receipt, **values[drift]))

    monkeypatch.setattr(runner.sender, "send", wrong)
    result = runner.run_once()
    assert result.status == "UNKNOWN_EFFECT" and runner.sender.calls == 1
    assert runner.run_once().replayed and runner.sender.calls == 1
    assert runner.orchestrator._completed == {}


def test_ui_user_turn_without_brain_receipt_is_blocked(composition, monkeypatch):
    make, event, registration, _, path, _ = composition
    runner = make()
    monkeypatch.setattr(runner.brain, "correlate", lambda *args: None)
    result = runner.run_once()
    assert result.status == "BLOCKED_EXTERNAL" and result.code == "BRAIN_RECEIPT_UNAVAILABLE"
    assert record(path, event, registration)["history"][-2:] == ["UI_TURN_OBSERVED", "BLOCKED_EXTERNAL"]
    assert runner.orchestrator._completed == {}


@pytest.mark.parametrize("field", ["event_key", "marker", "user_turn_ref", "evidence_type"])
def test_brain_receipt_requires_exact_correlation(composition, field, monkeypatch):
    make, _, _, _, _, _ = composition
    runner = make()
    original = runner.brain.correlate
    monkeypatch.setattr(runner.brain, "correlate", lambda *args: replace(original(*args), **{field: "wrong"}))
    assert runner.run_once().status == "UNKNOWN_EFFECT"
    assert runner.orchestrator._completed == {}


def test_wake_receipt_cannot_substitute_for_consumer_receipt(composition):
    make, _, _, handoff, _, _ = composition
    runner = make()
    runner.brain.handoff = replace(handoff, receipt=runner.sender.send(
        runner.source.event, runner.registration).receipt)
    assert runner.run_once().status == "UNKNOWN_EFFECT"
    assert runner.orchestrator._completed == {}


def test_unverified_consumer_receipt_cannot_ack(composition):
    make, event, registration, handoff, path, _ = composition
    spec = handoff.completed.spec
    runner = make(orchestrator=ProductionPipelineOrchestrator(
        authorized_specs=((spec.authorization.authorization_ref, spec.sha256),)))
    assert runner.run_once().status == "UNKNOWN_EFFECT"
    assert runner.orchestrator.dispatch(spec.idempotency_key).status == "NOT_DISPATCHED"
    assert "BRAIN_CORRELATED" not in record(path, event, registration)["history"]


def test_missing_grant_is_durable_without_sender_call(composition):
    make, event, registration, _, path, _ = composition
    runner = make(grant_verifier=lambda *args: False)
    assert runner.run_once() == r.Result("BLOCKED_EXTERNAL", "MISSING_GRANT", "SYNTHETIC_FIXTURE")
    assert runner.sender.calls == 0
    assert record(path, event, registration)["history"] == ["PENDING", "BLOCKED_EXTERNAL"]
    assert make().run_once().replayed


def test_no_event_is_hold_without_creating_a_journal(composition):
    make, _, _, _, path, _ = composition

    class EmptySource:
        def next_event(self):
            return None

    runner = make(source=EmptySource())
    assert runner.run_once().code == "NO_EVENT"
    assert runner.sender.calls == 0 and not path.exists()


def test_source_provenance_failure_is_hold_with_exact_reason(composition):
    make, _, _, _, path, _ = composition

    class UnverifiedSource:
        def next_event(self):
            raise ProvenanceError("PROVENANCE_UNVERIFIED")

    runner = make(source=UnverifiedSource())
    assert runner.run_once() == r.Result("HOLD", "PROVENANCE_UNVERIFIED", "SYNTHETIC_FIXTURE")
    assert runner.sender.calls == 0 and not path.exists()


def test_synthetic_event_cannot_enter_real_evidence_mode(composition):
    make, event, _, _, _, _ = composition
    runner = make(source=r.SyntheticSource(replace(event, evidence_type="GITHUB_AUTHENTICATED")))
    assert runner.run_once().code == "EVIDENCE_MODE_MISMATCH"
    assert runner.sender.calls == 0


def test_real_mode_cannot_bypass_native_owner_with_injected_readiness(composition):
    make, event, registration, _, _, _ = composition

    class UnavailableNativeSender:
        evidence_type = "GITHUB_AUTHENTICATED"

        def preflight(self, registration):
            raise AssertionError("Unreviewed native sender must never run")

        def send(self, *args):
            raise AssertionError("Native Send must never run")

    runner = make(source=r.SyntheticSource(replace(event, evidence_type="GITHUB_AUTHENTICATED")),
                  registration=replace(registration, evidence_type="GITHUB_AUTHENTICATED"),
                  sender=UnavailableNativeSender())
    assert runner.run_once() == r.Result("HOLD_DEPENDENCY", "UNSUPPORTED_NATIVE_OWNER", "GITHUB_AUTHENTICATED")


def test_sender_timeout_is_unknown_and_not_retried(composition, monkeypatch):
    make, _, _, _, _, _ = composition
    runner = make()
    calls = []

    def timeout(*args):
        calls.append(1)
        raise TimeoutError("private details must not appear in result")

    monkeypatch.setattr(runner.sender, "send", timeout)
    result = runner.run_once()
    assert result == r.Result("UNKNOWN_EFFECT", "ATTEMPT_UNPROVEN_NO_REPLAY", "SYNTHETIC_FIXTURE")
    assert runner.run_once().replayed and calls == [1]


@pytest.mark.parametrize("change", [{"generation": 2}, {"chat_url":
    "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"}])
def test_registration_generation_and_chat_are_immutable(composition, change):
    make, _, registration, _, path, _ = composition
    assert make().run_once().status == "ACK"
    before = path.read_bytes()
    changed = replace(registration, binding=replace(registration.binding, **change))
    runner = make(registration=changed)
    assert runner.run_once().code == "REGISTRATION_DRIFT"
    assert runner.sender.calls == 0 and path.read_bytes() == before
    with pytest.raises(FrozenInstanceError):
        registration.source_sha = "d" * 40


def test_source_drift_blocks_before_reservation(composition):
    make, event, _, _, _, _ = composition
    runner = make(source=r.SyntheticSource(replace(event, source_sha="d" * 40)))
    assert runner.run_once().code == "SOURCE_DRIFT" and runner.sender.calls == 0


@pytest.mark.parametrize("change", [{"source_sha": "d" * 40}, {"attempt": 2},
                                    {"payload_sha256": "d" * 64}])
def test_event_id_cannot_be_rebound_or_retried(composition, change):
    make, event, _, _, path, _ = composition
    assert make().run_once().status == "ACK"
    before = path.read_bytes()
    runner = make(source=r.SyntheticSource(replace(event, **change)))
    assert runner.run_once().code == "EVENT_IDENTITY_DRIFT"
    assert runner.sender.calls == 0 and path.read_bytes() == before


@pytest.mark.parametrize("state,code", [("absent", "PROFILE_ABSENT"),
    ("dirty", "PROFILE_ALREADY_ACTIVE_NO_CDP"), ("owned", "CONSUMER_RECEIPT_RECORDED")])
def test_existing_chrome_acquisition_distinguishes_absent_dirty_owned(composition, tmp_path, state, code):
    make, _, _, _, _, _ = composition
    executable = tmp_path / "synthetic-chrome.exe"
    executable.touch()
    profile = tmp_path / "existing-profile"
    profile.mkdir()
    env = ChromeEnvironment(executable.resolve(), profile.resolve(), "Default", "http://127.0.0.1:9444",
                            9444, False, tmp_path / "unused-config.json")
    argv = env.command()
    process = {"ProcessId": 13, "ExecutablePath": str(executable), "CommandLine": json.dumps(argv), "SessionId": 7}
    inventory = {"listeners": [], "processes": [] if state == "absent" else [process]}
    if state == "owned":
        inventory["listeners"] = [{"LocalAddress": "127.0.0.1", "OwningProcess": 13}]
    sender = r.SyntheticSender(env, inventory)
    result = make(sender=sender).run_once()
    assert result.code == code and sender.calls == (1 if state == "owned" else 0)


def test_native_owner_is_unsupported_and_never_invokes_ui(composition, monkeypatch):
    _, _, registration, _, _, root = composition
    calls = []
    monkeypatch.setattr(r.chrome_acquisition, "_start_or_reuse_9444",
                        lambda *args, **kwargs: calls.append(kwargs) or {"status": "HOLD"})
    sender = r.NativeChrome9444(root)
    assert sender.adapter.allow_submit is False
    assert sender.preflight(registration) == "UNSUPPORTED_NATIVE_OWNER"
    assert calls == []  # R1 must not even call the native acquisition seam.
    with pytest.raises(r.RuntimeHold, match="UNSUPPORTED_NATIVE_OWNER"):
        sender.send(None, registration)


def test_private_journal_refuses_checkout_and_corrupt_state(composition):
    make, _, _, _, path, root = composition
    with pytest.raises(ValueError):
        r.Journal(root / "journal.json", root)
    path.write_text("{}")
    runner = make()
    assert runner.run_once().code == "JOURNAL_MIGRATION_REQUIRED" and runner.sender.calls == 0
    assert path.read_text() == "{}"


def test_diagnostic_reopens_real_journal_and_labels_synthetic(composition):
    _, _, _, _, path, root = composition
    assert r.diagnostic(path, root).status == "ACK"
    result = r.diagnostic(path, root)
    assert result.evidence_type == "SYNTHETIC_FIXTURE" and result.replayed


def test_cli_help_dry_run_and_default_never_claim_success(capsys, monkeypatch):
    monkeypatch.delenv("AI_COMMERCE_WAKEHUB_CONFIG", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--help"])
    assert exc.value.code == 0
    assert "synthetic" in capsys.readouterr().out
    assert main(["run", "--dry-run"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "DRY_RUN_NO_EFFECTS"
    assert main(["run"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "LOCAL_AUTHORITY_MISSING"


def github_candidate():
    # Entirely offline evidence and fixture authority, not a real GitHub attestation.
    report = {"kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
              "batch_id": "fixture-batch", "source_sha": "a" * 40, "status": "PARTIAL_OR_FAILED",
              "actions": [{"action_id": "fixture-action", "attempt_id": "fixture-attempt", "status": "EXECUTOR_FAILED"}],
              "total": 1, "succeeded": 0, "github_workflow_run_id": "17", "github_issue_number": 23}
    event = build_completion_event(report, repository="trung-via/ai-commerce", run_id=17,
                                   run_attempt=1, issue_number=23)
    report.update(wake_event_id=event["event_id"], wake_event_status="RECORDED_UNBOUND")
    return event, report


@pytest.mark.parametrize("verified", [True, False])
def test_source_composes_existing_d5_provenance_verifier(verified):
    event, report = github_candidate()
    calls = []

    class CandidateReader:
        def candidate(self, target):
            assert target.run_id == 17
            return event, report

    class FixtureAuthority:
        def verify_artifact(self, provenance, **kwargs):
            calls.append(provenance)
            assert kwargs == {"event": event, "batch_result": report}
            return verified

    source = r.GitHubEventSource(CandidateReader(), PinnedRun(17, 1, 19, 23, "fixture-batch", "a" * 40),
                               FixtureAuthority())
    if verified:
        selected = source.next_event()
        assert selected.event_id == event["event_id"] and selected.payload_sha256 == digest(event)
    else:
        with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
            source.next_event()
    assert len(calls) == 1


def test_source_rejects_matching_blobs_without_final_report_proof():
    event, report = github_candidate()
    report["github_issue_number"] = 99

    class CandidateReader:
        def candidate(self, target):
            return event, report

    class FixtureAuthority:
        def verify_artifact(self, *args, **kwargs):
            raise AssertionError("Invalid report should fail before authority")

    source = r.GitHubEventSource(CandidateReader(), PinnedRun(17, 1, 19, 23, "fixture-batch", "a" * 40),
                               FixtureAuthority())
    with pytest.raises(ProvenanceError, match="ISSUE_NUMBER_MISMATCH"):
        source.next_event()


@pytest.mark.parametrize("raw,code", [
    (b'{"schema":"WakeHubRuntimeJournal.v1","events":{}}', "JOURNAL_MIGRATION_REQUIRED"),
    (b"", "JOURNAL_CORRUPT"), (b"not a database", "JOURNAL_CORRUPT"),
    (b"SQLite format 3\0" + b"corrupted" * 100, "JOURNAL_CORRUPT"),
])
def test_legacy_and_corrupt_journals_are_refused_without_reset(composition, raw, code):
    make, _, _, _, path, _ = composition
    path.write_bytes(raw)
    assert make().run_once().code == code
    assert path.read_bytes() == raw


@pytest.mark.parametrize("mutation,code", [
    ("PRAGMA user_version=99", "JOURNAL_SCHEMA_INCOMPATIBLE"),
    ("UPDATE identity SET owner='other-owner'", "JOURNAL_OWNER_DRIFT"),
    ("CREATE TABLE unexpected (value TEXT)", "JOURNAL_SCHEMA_INCOMPATIBLE"),
])
def test_sqlite_schema_and_identity_never_reinitialize(composition, mutation, code):
    make, _, _, _, path, _ = composition
    assert make().run_once().status == "ACK"
    with sqlite3.connect(path) as db:
        db.execute(mutation)
    before = path.read_bytes()
    runner = make()
    assert runner.run_once().code == code and runner.sender.calls == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("suffix", ["", ".lock", "-journal"])
def test_sqlite_sidecars_require_same_private_owner(composition, monkeypatch, suffix):
    make, _, _, _, path, _ = composition
    assert make().run_once().status == "ACK"
    original = r._private_identity
    target = path.with_name(path.name + suffix)
    monkeypatch.setattr(r, "_private_identity", lambda p: "wrong-owner" if p == target else original(p))
    runner = make()
    assert runner.run_once().code == "JOURNAL_OWNER_DRIFT" and runner.sender.calls == 0


def test_sqlite_reservation_is_committed_to_reopened_reader(composition, monkeypatch):
    make, event, registration, _, path, _ = composition
    runner = make()
    original = runner.sender.send

    def inspect_before_effect(*args):
        assert path.read_bytes().startswith(b"SQLite format 3\0")
        assert record(path, event, registration)["status"] == "RESERVED"
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        return original(*args)

    monkeypatch.setattr(runner.sender, "send", inspect_before_effect)
    assert runner.run_once().status == "ACK"


def test_competing_runner_cannot_reserve_during_an_effect(composition, monkeypatch):
    make, _, _, _, _, _ = composition
    runner = make()
    original = runner.sender.send

    def compete(*args):
        other = make()
        assert other.run_once().code == "JOURNAL_BUSY"
        assert other.sender.calls == 0
        return original(*args)

    monkeypatch.setattr(runner.sender, "send", compete)
    assert runner.run_once().status == "ACK"


def test_missing_saved_consumer_proof_cannot_replay_ack(composition):
    make, event, registration, _, path, _ = composition
    assert make().run_once().status == "ACK"
    saved = record(path, event, registration)
    saved["evidence"].pop("receipt_sha256")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE events SET record=?", (json.dumps(saved),))
    assert make().run_once().code == "JOURNAL_PROOF_MISSING"


def test_missing_database_after_enrollment_is_not_silently_recreated(composition):
    make, _, _, _, path, _ = composition
    assert make().run_once().status == "ACK"
    path.unlink()
    runner = make()
    assert runner.run_once().code == "JOURNAL_PROOF_MISSING"
    assert not path.exists() and runner.sender.calls == 0


def test_stored_consumer_receipt_is_typed_and_bound_on_reopen(composition):
    make, event, registration, _, path, _ = composition
    assert make().run_once().status == "ACK"
    saved = record(path, event, registration)
    assert type(ProductionStageReceipt.from_dict(saved["evidence"]["consumer_receipt"])) is ProductionStageReceipt
    saved["evidence"]["receipt_sha256"] = "f" * 64
    with sqlite3.connect(path) as db:
        db.execute("UPDATE events SET record=?", (json.dumps(saved),))
    assert make().run_once().code == "JOURNAL_PROOF_INVALID"


def test_crash_recovery_does_not_require_event_redelivery(composition, monkeypatch):
    make, event, registration, _, path, _ = composition
    runner = make()

    def crash(*args):
        raise Crash()

    monkeypatch.setattr(runner.sender, "send", crash)
    with pytest.raises(Crash):
        runner.run_once()
    recovered = make()
    monkeypatch.setattr(recovered.source, "next_event", lambda: None)
    assert recovered.run_once().code == "NO_EVENT"
    assert record(path, event, registration)["status"] == "UNKNOWN_EFFECT"
    assert recovered.sender.calls == 0


@pytest.fixture
def ingress(composition, monkeypatch):
    """Actual D5 reader/authority over offline REST/archive adapters.

    File custody and GitHub HTTP responses are fixtures, never native evidence.
    The production ingress still executes all real owner/consumer contracts.
    """
    _, _, fixture_registration, _, fixture_path, root = composition
    private = fixture_path.parent
    event, report = github_candidate()
    registration = replace(fixture_registration, evidence_type="GITHUB_AUTHENTICATED")
    intent = r.RegistrationIntent("trung-via/ai-commerce", 23, "fixture-batch", "a" * 40,
                                 19, 1, "trung-via", "HUMAN", "d" * 64,
                                 "2026-11-17T00:00:00Z", "2026-11-17T01:00:00Z")
    registered = {"version": 1, "intent": asdict(intent), "run_id": 17,
                  "event_id": event["event_id"], "request_artifact_id": 31, "request_digest": "e" * 64}
    binding = registration.binding
    registry = {"version": 1, "routes": [{**asdict(binding), "consent": True}],
                "claims": [{"route_id": binding.route_id, "generation": binding.generation,
                            **{k: event[k] for k in ("repository", "batch_id", "source_sha", "issue_number")}}]}
    from ai_commerce.remote.signed_origin_consent import registry_sha256
    key = b"fixture-origin-key-for-read-only!"
    body = {"kind": "AI_COMMERCE_SIGNED_ORIGIN_CONSENT", "version": 1,
            "caller_id": "fixture-local", "principal": r._private_identity(private),
            "operation": "resolve_claim_route", "scope": {k: event[k] for k in
                ("repository", "batch_id", "source_sha", "issue_number")},
            "registry_sha256": registry_sha256(registry), "valid_until": 4102444800,
            "human_consent": True}
    signature = hmac.new(key, json.dumps(body, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=True, allow_nan=False).encode(), hashlib.sha256).hexdigest()
    grants = {"version": 1, "grants": [{**body, "signature": signature}]}
    local = private / "local"
    owned = local / "AI-Commerce"
    profile = owned / "chrome-profile"
    (profile / "Default").mkdir(parents=True)
    executable = private / "chrome.exe"
    executable.touch()
    chrome_config = owned / "chrome.json"
    config_path = private / "runtime-config.json"
    config = {"schema": "WakeHubRuntimeR1.v1", "registration": asdict(registration),
              "marker": registration.marker, "registered_source": str(private / "registered.json"),
              "registry": str(private / "routes.json"), "journal": str(private / "ingress.sqlite3"),
              "chrome_config": str(chrome_config), "grants": str(private / "grants.json"),
              "caller_id": "fixture-local"}

    def save(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)

    save(config_path, config)
    save(r.Path(config["registered_source"]), registered)
    save(r.Path(config["registry"]), registry)
    save(r.Path(config["grants"]), grants)
    chrome = {"version": 1, "executable": str(executable), "user_data_dir": str(profile),
              "profile_directory": "Default", "cdp_endpoint": "http://127.0.0.1:9444",
              "allow_launch": False, "exclusive_user_data": True}
    save(chrome_config, chrome)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("AI_COMMERCE_GITHUB_READ_TOKEN", "fixture-read-only-token")
    monkeypatch.setenv("AI_COMMERCE_WAKE_HMAC_KEY_HEX", key.hex())

    def no_native_operator():
        raise r.chrome_acquisition._EnrollmentHold("WINDOWS_INTERACTIVE_REQUIRED")

    # All native identity/desktop reads require an explicit offline adapter in
    # these tests, including when EF01 executes them on a Windows CI host.
    monkeypatch.setattr(r.chrome_acquisition, "_enrollment_operator", no_native_operator)
    calls = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("fixture-batch/completion_event.json", json.dumps(event))
        zipped.writestr("fixture-batch/batch_result.json", json.dumps(report))
    raw = archive.getvalue()
    api = "https://api.github.com/repos/trung-via/ai-commerce"
    run = {"id": 17, "run_attempt": 1, "workflow_id": 19, "event": "issues", "status": "completed",
           "repository": {"full_name": "trung-via/ai-commerce"}}
    listing = {"total_count": 1, "artifacts": [{"id": 29, "name": "ef02-results-17-1",
                "expired": False, "workflow_run": {"id": 17},
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}]}
    responses = {api + "/actions/runs/17": run,
                 api + "/actions/runs/17/artifacts?per_page=100": listing}

    def read_json(url, token, **kwargs):
        assert token == "fixture-read-only-token"
        calls.append(url)
        return json.loads(json.dumps(responses[url]))

    def read_bytes(url, token, **kwargs):
        assert url == api + "/actions/artifacts/29/zip" and token == "fixture-read-only-token"
        calls.append(url)
        return raw

    monkeypatch.setattr(r.GitHubRestArtifactAuthority, "_read_json", staticmethod(read_json))
    monkeypatch.setattr(r.GitHubRestArtifactAuthority, "_read_bytes", staticmethod(read_bytes))
    return dict(config_path=config_path, config=config, root=root, event=event, registration=registration,
                registered=registered, registry=registry, grants=grants, chrome=chrome, save=save,
                calls=calls, run=run)


def test_cli_composes_actual_exact_run_reader_and_independent_d5_authority(ingress, capsys):
    i = ingress
    assert main(["run", "--config", str(i["config_path"]), "--repository-root", str(i["root"])]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == asdict(r.Result("HOLD", "WINDOWS_INTERACTIVE_REQUIRED", "GITHUB_AUTHENTICATED"))
    assert len(i["calls"]) == 6  # Reader AND independently authenticated artifact verifier.
    path = r.Path(i["config"]["journal"])
    assert record(path, r.Event(i["event"]["event_id"], "a" * 40, 1, digest(i["event"]),
                               "GITHUB_AUTHENTICATED"), i["registration"])["history"] == ["PENDING"]
    again = r.run_read_only(i["config_path"], i["root"])
    assert again.code == "WINDOWS_INTERACTIVE_REQUIRED"


@pytest.mark.parametrize("drift,code", [
    ("workflow", "RUN_IDENTITY_MISMATCH"), ("attempt", "RUN_IDENTITY_MISMATCH"),
    ("registered_event", "REGISTERED_EVENT_DRIFT"), ("registered_source", "SOURCE_TARGET_DRIFT"),
    ("chat", "REGISTRATION_DRIFT"), ("generation", "ROUTE_EVENT_MISMATCH"),
    ("grant", "MISSING_GRANT"), ("marker", "REGISTRATION_MARKER_DRIFT"),
    ("profile", "PROFILE_UNAVAILABLE"), ("port", "CDP_OWNER_MISMATCH"),
    ("injected_credential", "LOCAL_CONFIG_INVALID"),
])
def test_real_ingress_rejects_wrong_source_profile_grant_and_route(ingress, drift, code):
    i = ingress
    if drift in ("workflow", "attempt"):
        i["run"]["workflow_id" if drift == "workflow" else "run_attempt"] = 999
    elif drift in ("registered_event", "registered_source"):
        if drift == "registered_event":
            i["registered"]["event_id"] = "ef02-completion-v1:" + "f" * 64
        else:
            i["registered"]["intent"]["source_sha"] = "f" * 40
        i["save"](r.Path(i["config"]["registered_source"]), i["registered"])
    elif drift in ("chat", "generation"):
        i["registry"]["routes"][0]["chat_url" if drift == "chat" else "generation"] = (
            "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002" if drift == "chat" else 2)
        i["save"](r.Path(i["config"]["registry"]), i["registry"])
    elif drift == "grant":
        i["grants"]["grants"][0]["signature"] = "f" * 64
        i["save"](r.Path(i["config"]["grants"]), i["grants"])
    elif drift in ("profile", "port"):
        i["chrome"]["profile_directory" if drift == "profile" else "cdp_endpoint"] = (
            "Missing" if drift == "profile" else "http://127.0.0.1:9555")
        i["save"](r.Path(i["config"]["chrome_config"]), i["chrome"])
    else:
        i["config"]["marker" if drift == "marker" else "token"] = "injected"
        i["save"](i["config_path"], i["config"])
    result = r.run_read_only(i["config_path"], i["root"])
    assert result.code == code and result.status != "ACK"


def test_dry_run_reads_no_authority_events_or_journal(ingress, monkeypatch, capsys):
    i = ingress
    monkeypatch.setattr(r, "compose_read_only", lambda *a: pytest.fail("dry-run composed real ingress"))
    for diagnostic in (False, True):
        args = ["run", "--dry-run", "--config", str(i["config_path"]),
                "--repository-root", str(i["root"])]
        if diagnostic:
            args.extend(["--journal", str(r.Path(i["config"]["journal"]))])
        assert main(args) == 2
        assert json.loads(capsys.readouterr().out)["code"] == "DRY_RUN_NO_EFFECTS"
    assert i["calls"] == [] and not r.Path(i["config"]["journal"]).exists()


def test_diagnostic_cli_ack_remains_explicitly_synthetic(composition, capsys):
    _, _, _, _, path, root = composition
    assert main(["run", "--diagnostic", "--journal", str(path), "--repository-root", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["evidence_type"] == "SYNTHETIC_FIXTURE" and result["status"] == "ACK"


def test_ingress_cannot_skip_independent_artifact_authentication(ingress, monkeypatch):
    calls = []

    def refuse(self, provenance, **kwargs):
        calls.append(provenance)
        return False

    monkeypatch.setattr(r.GitHubRestArtifactAuthority, "verify_artifact", refuse)
    result = r.run_read_only(ingress["config_path"], ingress["root"])
    assert result.code == "PROVENANCE_UNVERIFIED" and len(calls) == 1
    assert not r.Path(ingress["config"]["journal"]).exists()


@pytest.mark.parametrize("name,code", [
    ("AI_COMMERCE_GITHUB_READ_TOKEN", "GITHUB_CREDENTIAL_INVALID"),
    ("AI_COMMERCE_WAKE_HMAC_KEY_HEX", "WAKE_CREDENTIAL_INVALID"),
    ("LOCALAPPDATA", "PROFILE_AUTHORITY_MISSING"),
])
def test_ingress_requires_existing_operator_authority(ingress, monkeypatch, name, code):
    monkeypatch.delenv(name, raising=False)
    result = r.run_read_only(ingress["config_path"], ingress["root"])
    assert result.code == code and ingress["calls"] == []
    assert not r.Path(ingress["config"]["journal"]).exists()


def test_ingress_rechecks_local_profile_config_before_admission(ingress):
    i = ingress
    runner = r.compose_read_only(i["config_path"], i["root"])
    i["chrome"]["profile_directory"] = "Other"
    i["save"](r.Path(i["config"]["chrome_config"]), i["chrome"])
    assert runner.run_once().code == "LOCAL_AUTHORITY_DRIFT"
    assert runner.orchestrator._completed == {}


def test_typed_consumer_receipt_from_another_event_cannot_ack(composition):
    make, event, registration, _, _, _ = composition
    other = replace(event, event_id="ef02-completion-v1:" + "f" * 64)
    runner = make(brain=r.SyntheticBrain(r.synthetic_handoff(other, registration)))
    assert runner.run_once().code == "HANDOFF_EVENT_MISMATCH"
    assert runner.orchestrator._completed == {}


class ReadOnlyPage:
    def __init__(self, url, surface="CANDIDATE_READY"):
        self.url, self.surface = url, surface
        self.reads = 0

    def evaluate(self, script):
        assert script == r.cdp_doorbell._CHAT_A_READ_ONLY_SURFACE
        self.reads += 1
        return self.surface


class ReadOnlyBrowser:
    def __init__(self, pages):
        self.items = [SimpleNamespace(pages=pages)]
        self.reads = 0

    @contextmanager
    def contexts(self, endpoint):
        assert endpoint == "http://127.0.0.1:9444"
        self.reads += 1
        yield self.items

    def preload(self, *args, **kwargs):
        pytest.fail("R2 has no native preload capability")


@pytest.fixture
def native_ingress(ingress, monkeypatch):
    """Mock native adapters; execute the actual Chrome identity/surface policy.

    A successful mocked owner observation remains source-only and cannot attest
    an account, authorize navigation, or produce a native success receipt.
    """
    i = ingress
    acq = r.chrome_acquisition
    env = acq.load_environment(r.Path(i["config"]["chrome_config"]), i["root"],
                               local_app_data=r.Path(os.environ["LOCALAPPDATA"]))
    sid = "S-1-5-21-111-222-333-1001"
    process = {"ProcessId": 13, "ExecutablePath": str(env.executable),
               "CommandLine": json.dumps(env.command()), "SessionId": 7,
               "OwnerSid": sid, "CreationDate": "2026-11-17T00:00:00.0000000Z"}
    inventory = {"listeners": [{"LocalAddress": "127.0.0.1", "LocalPort": 9444,
                                 "OwningProcess": 13}], "processes": [process]}
    page = ReadOnlyPage(i["registration"].binding.chat_url)
    browser = ReadOnlyBrowser([page])
    native_calls = []
    monkeypatch.setattr(acq, "_enrollment_operator",
                        lambda: (r.Path(os.environ["LOCALAPPDATA"]), 7, sid))
    # These three operations require actual Windows custody/signature evidence.
    monkeypatch.setattr(acq, "_read_9444_config", lambda *a: env)
    monkeypatch.setattr(acq, "_require_operator_control", lambda *a: None)
    monkeypatch.setattr(acq, "_check_enrollment_custody", lambda *a: None)
    monkeypatch.setattr(acq, "_commandline_argv", json.loads)

    def query(port):
        assert port == 9444
        native_calls.append("inventory")
        return json.loads(json.dumps(inventory))

    def probe(current):
        assert current == env
        native_calls.append("protocol")

    def forbidden(*args, **kwargs):
        pytest.fail("R2 crossed a native effect or Send boundary")

    monkeypatch.setattr(acq, "_query_windows", query)
    monkeypatch.setattr(acq, "_probe_cdp", probe)
    monkeypatch.setattr(acq, "run", forbidden)
    monkeypatch.setattr(acq, "_start_or_reuse_9444", forbidden)
    monkeypatch.setattr(r.NativeChrome9444, "send", forbidden)
    monkeypatch.setattr(r.LocalChromeDoorbellAdapter, "send_wake", forbidden)
    monkeypatch.setattr(r.cdp_doorbell, "_PlaywrightInventory", lambda: browser)
    return {**i, "environment": env, "inventory": inventory, "process": process,
            "page": page, "browser": browser, "native_calls": native_calls}


def assert_read_only_pending(i):
    path = r.Path(i["config"]["journal"])
    event = r.Event(i["event"]["event_id"], "a" * 40, 1, digest(i["event"]), "GITHUB_AUTHENTICATED")
    saved = record(path, event, i["registration"])
    assert saved["history"] == ["PENDING"] and saved["code"] == "EVENT_PENDING"
    assert saved["evidence"] == {}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    return saved


def test_r2_owned_reuse_reads_exact_surface_and_never_reserves_or_duplicates(native_ingress):
    i = native_ingress
    runner = r.compose_read_only(i["config_path"], i["root"])
    assert runner.sender.adapter.allow_submit is False
    for _ in range(2):
        assert runner.run_once() == r.Result(
            "HOLD", "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED", "GITHUB_AUTHENTICATED")
        assert_read_only_pending(i)
        assert runner.orchestrator._completed == {}
    assert i["native_calls"] == ["inventory", "protocol"] * 6
    assert i["page"].reads == i["browser"].reads == 2


@pytest.mark.parametrize("allow_launch", [False, True])
def test_r2_clean_absence_never_launches_even_with_launch_config(native_ingress, monkeypatch, allow_launch):
    i = native_ingress
    i["inventory"].update(listeners=[], processes=[])
    i["chrome"]["allow_launch"] = allow_launch
    i["save"](r.Path(i["config"]["chrome_config"]), i["chrome"])
    env = replace(i["environment"], allow_launch=allow_launch)
    monkeypatch.setattr(r.chrome_acquisition, "_read_9444_config", lambda *a: env)
    result = r.run_read_only(i["config_path"], i["root"])
    assert result == r.Result("HOLD", "CHROME_NOT_RUNNING_NATIVE_LAUNCH_CAPABILITY_REQUIRED",
                              "GITHUB_AUTHENTICATED")
    assert i["native_calls"] == ["inventory", "inventory"] and i["browser"].reads == 0
    assert_read_only_pending(i)


@pytest.mark.parametrize("fault,code", [
    ("dirty", "PROFILE_ALREADY_ACTIVE_NO_CDP"),
    ("session", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("sid", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("profile", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("foreign", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("two_listeners", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("two_owners", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("orphan_child", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("wrong_port", "WINDOWS_PROBE_INVALID"),
    ("wildcard", "CDP_NOT_LOOPBACK_OWNED"),
    ("boolean", "WINDOWS_PROBE_INVALID"),
])
def test_r2_rejects_native_identity_failures_before_browser_read(native_ingress, monkeypatch, fault, code):
    i = native_ingress
    if fault == "dirty":
        i["inventory"]["listeners"] = []
    elif fault in ("session", "sid", "foreign"):
        field, value = {"session": ("SessionId", 0), "sid": ("OwnerSid", "S-1-5-18"),
                        "foreign": ("ExecutablePath", "foreign.exe")}[fault]
        i["process"][field] = value
    elif fault == "profile":
        argv = i["environment"].command()
        argv[2] = "--profile-directory=Other"
        i["process"]["CommandLine"] = json.dumps(argv)
    elif fault == "two_listeners":
        i["inventory"]["listeners"] *= 2
    elif fault == "two_owners":
        i["inventory"]["processes"].append({**i["process"], "ProcessId": 14})
    elif fault == "orphan_child":
        i["inventory"]["listeners"] = []
        i["process"]["CommandLine"] = json.dumps([str(i["environment"].executable), "--type=renderer"])
    elif fault in ("wrong_port", "wildcard"):
        i["inventory"]["listeners"][0]["LocalPort" if fault == "wrong_port" else "LocalAddress"] = (
            9333 if fault == "wrong_port" else "0.0.0.0")
    else:
        monkeypatch.setattr(r.chrome_acquisition, "_query_windows", lambda *a: True)
    result = r.run_read_only(i["config_path"], i["root"])
    assert result.status == "HOLD" and result.code == code
    assert i["browser"].reads == 0 and "protocol" not in i["native_calls"]
    assert_read_only_pending(i)


@pytest.mark.parametrize("fault,code", [
    ("cdp", "CDP_PROTOCOL_UNAVAILABLE"),
    ("inventory", "WINDOWS_PROBE_UNAVAILABLE"),
    ("lock", "ENROLLMENT_PROFILE_LOCK_UNPROVEN"),
    ("profile_lock", "PROFILE_LOCK_HOLD"),
    ("config", "CHROME_ENROLLMENT_DRIFT"),
])
def test_r2_owner_dependencies_return_typed_holds(native_ingress, monkeypatch, fault, code):
    i = native_ingress

    def blocked(*args):
        raise r.chrome_acquisition.AcquisitionBlocked(code)

    if fault == "config":
        monkeypatch.setattr(r.chrome_acquisition, "_read_9444_config",
                            lambda *a: replace(i["environment"], profile_directory="Other"))
    elif fault == "profile_lock":
        (i["environment"].user_data_dir / i["environment"].profile_directory /
         r.chrome_acquisition._MARKERS[0]).touch()
    else:
        method = {"cdp": "_probe_cdp", "inventory": "_query_windows",
                  "lock": "_check_enrollment_custody"}[fault]
        monkeypatch.setattr(r.chrome_acquisition, method, blocked)
    result = r.run_read_only(i["config_path"], i["root"])
    assert result.status == "HOLD" and result.code == code
    assert i["browser"].reads == 0
    assert_read_only_pending(i)


@pytest.mark.parametrize("fault,code", [
    ("wrong_chat", "EXACT_CHAT_ABSENT_NATIVE_PRELOAD_CAPABILITY_REQUIRED"),
    ("two_contexts", "EXACT_CHAT_CONTEXT_AMBIGUOUS"),
    ("two_exact_tabs", "EXACT_CHAT_TAB_AMBIGUOUS"),
    ("draft", "PRELOAD_DRAFT_PRESENT"),
    ("login", "PRELOAD_SURFACE_UNPROVEN"),
    ("wrong_account", "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"),
    ("unattested_marker", "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"),
    ("boolean_surface", "PRELOAD_SURFACE_UNPROVEN"),
])
def test_r2_exact_chat_candidate_is_not_native_attestation(native_ingress, fault, code):
    i = native_ingress
    browser, page = i["browser"], i["page"]
    if fault == "wrong_chat":
        page.url = page.url[:-1] + "2"
    elif fault == "two_contexts":
        browser.items.append(SimpleNamespace(pages=[]))
    elif fault == "two_exact_tabs":
        browser.items[0].pages.append(ReadOnlyPage(page.url))
    elif fault in ("draft", "login", "boolean_surface"):
        page.surface = {"draft": "DRAFT_PRESENT", "login": "SURFACE_UNPROVEN",
                        "boolean_surface": True}[fault]
    elif fault == "wrong_account":
        page.account = "another-account"  # A URL/surface does not verify this.
    else:
        page.marker = i["registration"].marker  # Nor can copied marker text do so.
    original_url, original_surface = page.url, page.surface
    result = r.run_read_only(i["config_path"], i["root"])
    assert result.status == "HOLD" and result.code == code
    assert (page.url, page.surface) == (original_url, original_surface)
    assert_read_only_pending(i)


def test_r2_preserves_unrelated_tabs_and_drafts(native_ingress):
    i = native_ingress
    unrelated = ReadOnlyPage("https://chatgpt.com/c/00000000-0000-0000-0000-000000000002", "DRAFT_PRESENT")
    i["browser"].items[0].pages.append(unrelated)
    result = r.run_read_only(i["config_path"], i["root"])
    assert result.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    assert unrelated.reads == 0 and unrelated.surface == "DRAFT_PRESENT"
    assert_read_only_pending(i)


def test_r2_no_preload_capability_even_with_launch_enabled_config(native_ingress, monkeypatch):
    i = native_ingress
    i["browser"].items[0].pages.clear()
    i["chrome"]["allow_launch"] = True
    i["save"](r.Path(i["config"]["chrome_config"]), i["chrome"])
    env = replace(i["environment"], allow_launch=True)
    monkeypatch.setattr(r.chrome_acquisition, "_read_9444_config", lambda *a: env)
    # The protocol mock checks the original fixture environment by design.
    monkeypatch.setattr(r.chrome_acquisition, "_probe_cdp", lambda *a: None)
    assert r.run_read_only(i["config_path"], i["root"]).code == (
        "EXACT_CHAT_ABSENT_NATIVE_PRELOAD_CAPABILITY_REQUIRED")
    assert i["browser"].items[0].pages == []
    assert_read_only_pending(i)


def test_r2_missing_origin_grant_prevents_all_native_reads(native_ingress):
    i = native_ingress
    i["grants"]["grants"][0]["signature"] = "f" * 64
    i["save"](r.Path(i["config"]["grants"]), i["grants"])
    assert r.run_read_only(i["config_path"], i["root"]).code == "MISSING_GRANT"
    assert i["native_calls"] == [] and i["browser"].reads == 0
    assert_read_only_pending(i)


def test_r2_lost_native_evidence_is_unknown_and_stays_pending(native_ingress, monkeypatch):
    i = native_ingress
    calls = []

    def query(port):
        assert port == 9444
        calls.append(port)
        if len(calls) > 1:
            raise r.chrome_acquisition.AcquisitionBlocked("WINDOWS_PROBE_UNAVAILABLE")
        return json.loads(json.dumps(i["inventory"]))

    monkeypatch.setattr(r.chrome_acquisition, "_query_windows", query)
    assert r.run_read_only(i["config_path"], i["root"]).code == "CHROME_OBSERVATION_UNKNOWN"
    assert i["browser"].reads == 0
    assert_read_only_pending(i)


@pytest.mark.parametrize("drift", ["generation", "marker", "source", "chat", "grant", "profile"])
def test_r2_rechecks_immutable_authority_after_browser_read(native_ingress, monkeypatch, drift):
    i = native_ingress
    original = i["page"].evaluate

    def change(script):
        code = original(script)
        if drift in ("generation", "chat"):
            i["registry"]["routes"][0]["generation" if drift == "generation" else "chat_url"] = (
                2 if drift == "generation" else i["registration"].binding.chat_url[:-1] + "2")
            i["save"](r.Path(i["config"]["registry"]), i["registry"])
        elif drift == "source":
            i["registered"]["intent"]["source_sha"] = "f" * 40
            i["save"](r.Path(i["config"]["registered_source"]), i["registered"])
        elif drift == "grant":
            i["grants"]["grants"][0]["signature"] = "f" * 64
            i["save"](r.Path(i["config"]["grants"]), i["grants"])
        elif drift == "profile":
            i["chrome"]["profile_directory"] = "Other"
            i["save"](r.Path(i["config"]["chrome_config"]), i["chrome"])
        else:
            i["config"]["marker"] = "stale"
            i["save"](i["config_path"], i["config"])
        return code

    monkeypatch.setattr(i["page"], "evaluate", change)
    assert r.run_read_only(i["config_path"], i["root"]).code == "LOCAL_AUTHORITY_DRIFT"
    assert_read_only_pending(i)


@pytest.mark.parametrize("fault", ["restart", "crash", "url"])
def test_r2_native_drift_or_crash_during_surface_read_never_replays(native_ingress, monkeypatch, fault):
    i = native_ingress
    original = i["page"].evaluate

    def change(script):
        code = original(script)
        if fault == "restart":
            i["process"]["CreationDate"] = "2026-11-17T00:01:00.0000000Z"
        elif fault == "crash":
            i["inventory"].update(listeners=[], processes=[])
        else:
            i["page"].url = i["page"].url[:-1] + "2"
        return code

    monkeypatch.setattr(i["page"], "evaluate", change)
    runner = r.compose_read_only(i["config_path"], i["root"])
    assert runner.run_once().code == ("EXACT_CHAT_URL_DRIFT" if fault == "url" else "CHROME_OBSERVATION_CHANGED")
    assert_read_only_pending(i)
    assert runner.orchestrator._completed == {}


def test_r2_unknown_effect_does_not_even_repeat_readiness(native_ingress):
    i = native_ingress
    runner = r.compose_read_only(i["config_path"], i["root"])
    assert runner.run_once().status == "HOLD"
    path = r.Path(i["config"]["journal"])
    with sqlite3.connect(path) as db:
        saved = json.loads(db.execute("SELECT record FROM events").fetchone()[0])
        saved.update(status="RESERVED", code="SEND_RESERVED", history=["PENDING", "RESERVED"])
        db.execute("UPDATE events SET record=?", (json.dumps(saved),))
    calls = list(i["native_calls"])
    for _ in range(2):
        reopened = r.compose_read_only(i["config_path"], i["root"])
        assert reopened.run_once() == r.Result("UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY",
                                               "GITHUB_AUTHENTICATED", True)
    assert i["native_calls"] == calls and i["browser"].reads == 1
    assert runner.orchestrator._completed == {}


@pytest.mark.parametrize("value", [True, None, {"status": "READY_ATTACH"}, "ACK"])
def test_r2_preflight_result_cannot_escape_read_only_hold(native_ingress, monkeypatch, value):
    i = native_ingress
    monkeypatch.setattr(r.NativeChrome9444, "preflight", lambda *a: value)
    result = r.run_read_only(i["config_path"], i["root"])
    assert result.status == "HOLD"
    assert result.code == ("ACK" if value == "ACK" else "PREFLIGHT_RESULT_INVALID")
    assert_read_only_pending(i)


@pytest.fixture
def r3a_ingress(native_ingress):
    """TEST_ONLY_MOCK Windows owner and signed fixture grants; no native authority."""
    i = native_ingress

    def sign(body):
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode()
        key = bytes.fromhex(os.environ["AI_COMMERCE_WAKE_HMAC_KEY_HEX"])
        return {**body, "signature": hmac.new(key, raw, hashlib.sha256).hexdigest()}

    body = {k: v for k, v in i["grants"]["grants"][0].items() if k != "signature"}
    body.update(operation="deliver_wake", scope={"event": json.loads(json.dumps(i["event"]))})
    i["grants"]["grants"].append(sign(body))
    i["save"](r.Path(i["config"]["grants"]), i["grants"])
    return {**i, "deliver_body": body, "sign": sign}


def test_r3a_requires_separate_deliver_wake_grant_before_native_reads(native_ingress):
    i = native_ingress
    result = r.run_native_source_only(i["config_path"], i["root"])
    assert result == r.Result("HOLD_DEPENDENCY", "DELIVER_WAKE_GRANT_REQUIRED", "GITHUB_AUTHENTICATED")
    assert i["native_calls"] == [] and i["browser"].reads == 0
    assert_read_only_pending(i)


@pytest.mark.parametrize("fault", ["signature", "event", "registry", "principal", "caller", "expired"])
def test_r3a_signed_effect_grant_is_exact_and_current(r3a_ingress, fault):
    i = r3a_ingress
    body = i["deliver_body"]
    if fault == "event":
        body["scope"]["event"]["event_id"] = "ef02-completion-v1:" + "f" * 64
    elif fault == "registry":
        body["registry_sha256"] = "f" * 64
    elif fault in ("principal", "caller"):
        body["principal" if fault == "principal" else "caller_id"] = "other-operator"
    elif fault == "expired":
        body["valid_until"] = 1
    grant = i["sign"](body)
    if fault == "signature":
        grant["signature"] = "f" * 64
    i["grants"]["grants"][1] = grant
    i["save"](r.Path(i["config"]["grants"]), i["grants"])
    assert r.run_native_source_only(i["config_path"], i["root"]).code == "DELIVER_WAKE_GRANT_REQUIRED"
    assert i["native_calls"] == [] and i["browser"].reads == 0
    assert_read_only_pending(i)


def test_r3a_candidate_holds_dependency_without_reserving_or_enabling_send(r3a_ingress):
    i = r3a_ingress
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    for _ in range(2):
        result = runner.run_once()
        assert result == r.Result("HOLD_DEPENDENCY", "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE",
                                  "GITHUB_AUTHENTICATED")
        assert_read_only_pending(i)
    assert i["page"].reads == 4  # Two empty observations per read-only iteration.
    assert runner.sender.owner.adapter.allow_submit is False
    assert runner.orchestrator._completed == {} and runner.orchestrator._receipts == {}
    with pytest.raises(r.DependencyHold, match="^DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE$"):
        runner.sender.send(None, i["registration"])


@pytest.mark.parametrize("hint", ["account", "profile", "session", "marker", "wh03", "native_authority"])
def test_r3a_caller_or_ui_identity_hints_never_attest_origin(r3a_ingress, hint):
    i = r3a_ingress
    value = {"account": "wrong-account", "profile": "wrong-profile", "session": "old-session",
             "marker": i["registration"].marker, "wh03": "wh03." + "f" * 64,
             "native_authority": True}[hint]
    setattr(i["page"], hint, value)
    result = r.run_native_source_only(i["config_path"], i["root"])
    assert result.status == "HOLD_DEPENDENCY" and result.code == "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE"
    assert_read_only_pending(i)


@pytest.mark.parametrize("fault,code", [
    ("chat", "EXACT_CHAT_ABSENT_NATIVE_PRELOAD_CAPABILITY_REQUIRED"),
    ("tab", "EXACT_CHAT_SINGLE_TAB_REQUIRED"),
    ("context", "EXACT_CHAT_CONTEXT_AMBIGUOUS"),
    ("draft", "PRELOAD_DRAFT_PRESENT"),
    ("profile", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("owner", "BROWSER_OWNERSHIP_UNPROVEN"),
])
def test_r3a_keeps_strict_owner_and_single_empty_tab_gates(r3a_ingress, fault, code):
    i = r3a_ingress
    if fault == "chat":
        i["page"].url = i["page"].url[:-1] + "2"
    elif fault == "tab":
        i["browser"].items[0].pages.append(ReadOnlyPage("https://example.invalid"))
    elif fault == "context":
        i["browser"].items.append(SimpleNamespace(pages=[]))
    elif fault == "draft":
        i["page"].surface = "DRAFT_PRESENT"
    elif fault == "profile":
        argv = i["environment"].command()
        argv[2] = "--profile-directory=Other"
        i["process"]["CommandLine"] = json.dumps(argv)
    else:
        i["inventory"]["listeners"] *= 2
    assert r.run_native_source_only(i["config_path"], i["root"]) == r.Result("HOLD", code, "GITHUB_AUTHENTICATED")
    assert_read_only_pending(i)


def test_r3a_composer_must_remain_empty_on_second_observation(r3a_ingress, monkeypatch):
    i = r3a_ingress
    original = i["page"].evaluate

    def changed(script):
        result = original(script)
        i["page"].surface = "DRAFT_PRESENT"
        return result

    monkeypatch.setattr(i["page"], "evaluate", changed)
    assert r.run_native_source_only(i["config_path"], i["root"]).code == "PRELOAD_DRAFT_PRESENT"
    assert i["page"].reads == 2
    assert_read_only_pending(i)


@pytest.mark.parametrize("change", [{"generation": 2}, {"chat_url":
    "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"}])
def test_r3a_sender_registration_cannot_change_generation_or_chat(r3a_ingress, change):
    i = r3a_ingress
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    changed = replace(i["registration"], binding=replace(i["registration"].binding, **change))
    with pytest.raises(r.RuntimeHold, match="^REGISTRATION_DRIFT$"):
        runner.sender.preflight(changed)
    assert i["native_calls"] == []


def test_r3a_old_marker_cannot_enter_native_preflight(r3a_ingress):
    i = r3a_ingress
    i["config"]["marker"] = replace(i["registration"], binding=replace(
        i["registration"].binding, generation=2)).marker
    i["save"](i["config_path"], i["config"])
    assert r.run_native_source_only(i["config_path"], i["root"]).code == "REGISTRATION_MARKER_DRIFT"
    assert i["native_calls"] == [] and not r.Path(i["config"]["journal"]).exists()


def test_r3a_json_native_authority_flag_cannot_enroll_a_provider(r3a_ingress):
    i = r3a_ingress
    i["config"]["native_authority"] = True
    i["save"](i["config_path"], i["config"])
    assert r.run_native_source_only(i["config_path"], i["root"]).code == "LOCAL_CONFIG_INVALID"
    assert i["calls"] == [] and i["native_calls"] == []


@pytest.mark.parametrize("name", ["grants", "registered_source", "registry", "chrome_config"])
def test_r3a_dependency_does_not_hide_authority_drift_during_reads(r3a_ingress, monkeypatch, name):
    i = r3a_ingress
    original = i["page"].evaluate

    def changed(script):
        result = original(script)
        i["save"](r.Path(i["config"][name]), {"changed": True})
        return result

    monkeypatch.setattr(i["page"], "evaluate", changed)
    assert r.run_native_source_only(i["config_path"], i["root"]).code == "LOCAL_AUTHORITY_DRIFT"
    assert_read_only_pending(i)


@pytest.mark.parametrize("fault", ["revoke", "drift", "transport"])
def test_r3a_final_authority_is_revalidated_after_durable_reservation(composition, fault):
    make, event, registration, _, path, _ = composition
    calls = []

    def grant(*args):
        calls.append(1)
        if len(calls) == 1:
            return True
        assert record(path, event, registration)["status"] == "RESERVED"
        if fault == "drift":
            raise r.RuntimeHold("LOCAL_AUTHORITY_DRIFT")
        if fault == "transport":
            raise TimeoutError("private details")
        return False

    runner = make(grant_verifier=grant)
    result = runner.run_once()
    assert result == r.Result("BLOCKED_EXTERNAL", {"revoke": "MISSING_GRANT",
        "drift": "LOCAL_AUTHORITY_DRIFT", "transport": "FINAL_AUTHORITY_UNPROVEN"}[fault], "SYNTHETIC_FIXTURE")
    assert runner.sender.calls == 0 and len(calls) == 2
    assert record(path, event, registration)["history"] == ["PENDING", "RESERVED", "BLOCKED_EXTERNAL"]
    assert runner.run_once().replayed and len(calls) == 2


@pytest.mark.parametrize("phase", ["stage", "click", "transport"])
def test_r3a_test_only_mock_ambiguous_attempt_never_retries(composition, monkeypatch, phase):
    make, event, registration, _, path, _ = composition
    runner = make()
    calls = []

    def ambiguous(*args):
        assert record(path, event, registration)["status"] == "RESERVED"
        calls.append(phase)
        raise TimeoutError("TEST_ONLY_MOCK " + phase)

    monkeypatch.setattr(runner.sender, "send", ambiguous)
    assert runner.run_once().status == "UNKNOWN_EFFECT"
    assert runner.run_once().replayed and calls == [phase]
    reopened = make()
    assert reopened.run_once().status == "UNKNOWN_EFFECT" and reopened.sender.calls == 0
    assert reopened.orchestrator._completed == {}


def test_r3a_missing_native_brain_transport_never_accepts_ui_as_brain(composition):
    make, event, registration, _, path, _ = composition
    runner = make(brain=r.NativeBrainAdapter())
    assert runner.run_once() == r.Result("BLOCKED_EXTERNAL", "BRAIN_RECEIPT_UNAVAILABLE", "SYNTHETIC_FIXTURE")
    assert record(path, event, registration)["history"] == [
        "PENDING", "RESERVED", "UI_TURN_OBSERVED", "BLOCKED_EXTERNAL"]
    assert runner.orchestrator._completed == {}


def test_r3a_r2_sender_cannot_acquire_effect_authority_by_clearing_read_only(native_ingress):
    i = native_ingress
    runner = r.compose_read_only(i["config_path"], i["root"])
    runner.read_only = False
    assert runner.run_once().code == "UNSUPPORTED_NATIVE_OWNER"
    assert i["native_calls"] == [] and runner.orchestrator._completed == {}


def test_r3a_synthetic_adapters_cannot_produce_authenticated_evidence(composition):
    make, event, registration, _, _, root = composition
    real_event = replace(event, evidence_type="GITHUB_AUTHENTICATED")
    real_registration = replace(registration, evidence_type="GITHUB_AUTHENTICATED")
    runner = make()
    observation = runner.sender.send(event, registration)
    for action in (lambda: runner.sender.send(real_event, real_registration),
                   lambda: runner.brain.correlate(real_event, real_registration, observation)):
        with pytest.raises(r.RuntimeHold, match="^SYNTHETIC_ONLY$"):
            action()
    native = make(source=r.SyntheticSource(real_event), registration=real_registration,
                  sender=r.NativeSender(root, real_registration))
    assert native.run_once().code == "AUTHENTICATED_SOURCE_REQUIRED"
    assert native.orchestrator._completed == {}


def test_r3a_test_only_mock_hands_off_to_exactly_one_existing_orchestrator(composition, monkeypatch):
    """TEST_ONLY_MOCK: real A03/P0 offline contracts, synthetic evidence only."""
    make, _, _, _, _, _ = composition
    runner = make()
    calls = []
    for name in ("record_completed", "record_receipt"):
        original = getattr(runner.orchestrator, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            calls.append((_name, id(runner.orchestrator)))
            return _original(*args, **kwargs)

        monkeypatch.setattr(runner.orchestrator, name, counted)
    assert runner.run_once() == r.Result("ACK", "CONSUMER_RECEIPT_RECORDED", "SYNTHETIC_FIXTURE")
    assert runner.run_once().replayed
    assert calls == [("record_completed", id(runner.orchestrator)), ("record_receipt", id(runner.orchestrator))]


def test_r3a_cli_is_opt_in_dry_run_and_synthetic_modes_stay_separate(r3a_ingress, monkeypatch, capsys):
    i = r3a_ingress
    args = ["run", "--config", str(i["config_path"]), "--repository-root", str(i["root"])]
    assert main(args + ["--native-source-only", "--dry-run"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "DRY_RUN_NO_EFFECTS"
    assert i["calls"] == [] and i["native_calls"] == []
    assert main(args + ["--native-source-only"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "HOLD_DEPENDENCY"
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    with pytest.raises(SystemExit) as error:
        main(["run", "--diagnostic", "--native-source-only"])
    assert error.value.code == 2
    assert_read_only_pending(i)


@pytest.fixture
def r3b1_pins(composition):
    """TEST_ONLY_MOCK: complete-looking values, deliberately NO authentication."""
    _, event, registration, handoff, _, root = composition
    event = replace(event, evidence_type="GITHUB_AUTHENTICATED")
    registration = replace(registration, evidence_type="GITHUB_AUTHENTICATED")
    origin = r.NativeOriginPins(
        "S-1-5-21-111-222-333-1001", 7, "0000000000000001", r"WinSta0\Default",
        "fixture:profile", "fixture:custody", "fixture:account", "fixture:account-session",
        registration.binding, registration.marker, "fixture:context", "fixture:tab",
        "fixture:composer", "fixture:origin-proof")
    request = r.DoorbellRequest.for_event(event, registration)
    turn = r.AuthenticatedUserTurn(request, "fixture:user-turn-id", "fixture:message-ref",
                                   "fixture:turn-proof", "USER")
    brain = r.AuthenticatedBrainReceipt(
        event.event_id, event.key(registration), registration.marker, turn.user_turn_id,
        turn.message_ref, registration.binding.generation, "fixture:brain-proof",
        handoff.completed.spec.authorization.authorization_ref, handoff.completed.sha256,
        handoff.receipt.consumer_id, handoff.receipt.sha256)
    grant = r.SignedWakeAuthorization(event, registration, "d" * 64, origin.windows_sid,
                                     "fixture:caller", ("resolve_claim_route", "deliver_wake"))
    return SimpleNamespace(event=event, registration=registration, origin=origin, request=request,
                           turn=turn, brain=brain, grant=grant, root=root)


@pytest.mark.parametrize("name", ["origin", "request", "turn", "brain", "grant"])
def test_r3b1_boundary_values_are_frozen_and_private(r3b1_pins, name):
    value = getattr(r3b1_pins, name)
    field = next(iter(value.__dataclass_fields__))
    with pytest.raises(FrozenInstanceError):
        setattr(value, field, "changed")
    assert "fixture:" not in repr(value) and "chatgpt.com" not in repr(value)


@pytest.mark.parametrize("change", [
    {"account_id": "fixture:wrong-account"}, {"account_session_ref": "fixture:stale-session"},
    {"chrome_profile_id": "fixture:other-profile"}, {"windows_session_id": 8},
    {"windows_sid": "S-1-5-21-111-222-333-1002"}, {"marker": "fixture:copied-marker"},
    {"composer_ref": "fixture:replacement-composer"}, {"context_ref": "fixture:other-context"},
    {"tab_ref": "fixture:other-tab"}, {"generation": 2}, {},
])
def test_r3b1_native_pins_never_authenticate_from_caller_values(r3b1_pins, change):
    p = r3b1_pins
    if "generation" in change:
        candidate = replace(p.origin, binding=replace(p.origin.binding, **change))
    else:
        candidate = replace(p.origin, **change)
    with pytest.raises(r.DependencyHold, match="^NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE$"):
        r.NativeOriginAuthority().authenticate(p.registration, candidate)


@pytest.mark.parametrize("provider", ["origin", "turn", "brain", "grant"])
def test_r3b1_forged_provider_callbacks_are_never_authority(r3a_ingress, provider):
    i = r3a_ingress
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    calls = []

    def forged(*args, **kwargs):
        calls.append(provider)
        return True

    fake = SimpleNamespace(authenticate=forged, correlate=forged, preflight=forged,
                           authorize=forged, attestation_verified=True, native_authority=True)
    if provider == "origin":
        runner.sender.origin_authority = fake
        code = "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE"
    elif provider == "turn":
        runner.sender.turn_authority = fake
        event = runner.source.next_event()
        with pytest.raises(r.DependencyHold, match="^DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE$"):
            runner.sender.require_turn_reference(r.DoorbellRequest.for_event(event, i["registration"]))
        code = "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE"
    elif provider == "brain":
        runner.brain = fake
        code = "BRAIN_RECEIPT_UNAVAILABLE"
    else:
        runner.grant_verifier = forged
        code = "SIGNED_WAKE_AUTHORITY_UNAVAILABLE"
    assert runner.run_once() == r.Result("HOLD_DEPENDENCY", code, "GITHUB_AUTHENTICATED")
    assert calls == []
    assert_read_only_pending(i)
    assert runner.sender.owner.adapter.allow_submit is False
    assert runner.orchestrator._completed == {} and runner.orchestrator._receipts == {}
    if provider in ("brain", "grant"):
        assert i["native_calls"] == [] and i["browser"].reads == 0


def test_r3b1_signed_authority_returns_detached_exact_scope(r3a_ingress):
    i = r3a_ingress
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    event = runner.source.next_event()
    grant = runner.grant_verifier.authorize(event, i["registration"])
    assert type(grant) is r.SignedWakeAuthorization
    assert grant.event == event and grant.registration == i["registration"]
    assert grant.operations == ("resolve_claim_route", "deliver_wake")
    assert grant.registry_sha256 == r.registry_sha256(i["registry"])
    i["registry"]["routes"][0]["generation"] = 2
    i["save"](r.Path(i["config"]["registry"]), i["registry"])
    with pytest.raises(r.RuntimeHold, match="^LOCAL_AUTHORITY_DRIFT$"):
        runner.grant_verifier.authorize(event, i["registration"])
    assert i["native_calls"] == [] and not r.Path(i["config"]["journal"]).exists()


@pytest.mark.parametrize("operation", ["resolve_claim_route", "deliver_wake"])
@pytest.mark.parametrize("fault", ["missing", "expired"])
def test_r3b1_ineligible_grant_holds_dependency_before_native_reads(r3a_ingress, operation, fault):
    i = r3a_ingress
    grants = i["grants"]["grants"]
    index = next(n for n, grant in enumerate(grants) if grant["operation"] == operation)
    if fault == "missing":
        grants.pop(index)
    else:
        body = {k: v for k, v in grants[index].items() if k != "signature"}
        body["valid_until"] = 1
        grants[index] = i["sign"](body)
    i["save"](r.Path(i["config"]["grants"]), i["grants"])
    code = "MISSING_GRANT" if operation == "resolve_claim_route" else "DELIVER_WAKE_GRANT_REQUIRED"
    assert r.run_native_source_only(i["config_path"], i["root"]) == r.Result(
        "HOLD_DEPENDENCY", code, "GITHUB_AUTHENTICATED")
    assert i["native_calls"] == [] and i["browser"].reads == 0
    assert_read_only_pending(i)


@pytest.mark.parametrize("operation", ["resolve_claim_route", "deliver_wake"])
def test_r3b1_grant_expiry_during_surface_reads_never_reserves(r3a_ingress, monkeypatch, operation):
    i = r3a_ingress
    grants = i["grants"]["grants"]
    index = next(n for n, grant in enumerate(grants) if grant["operation"] == operation)
    body = {k: v for k, v in grants[index].items() if k != "signature"}
    body["valid_until"] = 1800000001
    grants[index] = i["sign"](body)
    i["save"](r.Path(i["config"]["grants"]), i["grants"])
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    now = [1800000000]
    monkeypatch.setattr(runner.grant_verifier.origin, "_clock", lambda: now[0])
    original = i["page"].evaluate

    def expired(script):
        result = original(script)
        now[0] = 1800000002
        return result

    monkeypatch.setattr(i["page"], "evaluate", expired)
    code = "MISSING_GRANT" if operation == "resolve_claim_route" else "DELIVER_WAKE_GRANT_REQUIRED"
    assert runner.run_once() == r.Result("HOLD_DEPENDENCY", code, "GITHUB_AUTHENTICATED")
    assert_read_only_pending(i)
    assert runner.sender.owner.adapter.allow_submit is False
    assert runner.orchestrator._completed == {}


def test_r3b1_readiness_callback_cannot_skip_independent_dependencies(r3a_ingress, monkeypatch):
    i = r3a_ingress
    monkeypatch.setattr(r.NativeSender, "preflight", lambda *args: None)
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    assert runner.run_once() == r.Result("HOLD_DEPENDENCY", "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE",
                                       "GITHUB_AUTHENTICATED")
    assert_read_only_pending(i)
    assert i["native_calls"] == [] and runner.orchestrator._completed == {}


def test_r3b1_canonical_request_and_missing_remote_api_cannot_send(r3b1_pins, monkeypatch):
    p = r3b1_pins
    assert p.request.canonical_bytes == r.cdp_doorbell._doorbell_text(
        p.event.event_id, "trung-via/ai-commerce", send_only=True).encode("utf-8")
    assert p.request.binding == p.registration.binding and p.request.marker == p.registration.marker
    calls = []
    # A dynamically attached callback is not a reviewed remote-owner capability.
    monkeypatch.setattr(r.LocalChromeDoorbellAdapter, "send_wake_authenticated",
                        lambda *a, **k: calls.append("send"), raising=False)
    sender = r.NativeSender(p.root, p.registration)
    with pytest.raises(r.DependencyHold, match="^DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE$"):
        sender.require_turn_reference(p.request)
    assert calls == [] and sender.owner.adapter.allow_submit is False
    with pytest.raises(r.RuntimeHold, match="^DOORBELL_REQUEST_INVALID$"):
        replace(p.request, canonical_bytes=p.request.canonical_bytes + b"\n@plugin")


@pytest.mark.parametrize("turn_id", ["", None, 0, [], ["same", "same"], " \n"])
def test_r3b1_zero_missing_or_multiple_user_turn_ids_are_invalid(r3b1_pins, turn_id):
    with pytest.raises(r.RuntimeHold, match="^AUTHENTICATED_USER_TURN_INVALID$"):
        replace(r3b1_pins.turn, user_turn_id=turn_id)


@pytest.mark.parametrize("change", [{}, {"role": "ASSISTANT"}, {"message_ref": ""}])
def test_r3b1_typed_or_legacy_receipt_never_authenticates_a_native_turn(r3b1_pins, change):
    p = r3b1_pins
    if change:
        with pytest.raises(r.RuntimeHold, match="^AUTHENTICATED_USER_TURN_INVALID$"):
            replace(p.turn, **change)
        return
    receipt = r.WakeReceipt(event_id=p.event.event_id, route_id=p.registration.binding.route_id,
                            generation=p.registration.binding.generation, delivery_status="UNKNOWN",
                            error_code="UI_USER_TURN_OBSERVED")
    for candidate in (None, p.turn):
        observation = r.Observation(receipt, p.registration.binding.chat_url, p.registration.marker,
                                    p.turn.message_ref, candidate)
        with pytest.raises(r.DependencyHold, match="^DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE$"):
            r.NativeSender(p.root, p.registration).authenticate_observation(
                p.event, p.registration, observation)


@pytest.mark.parametrize("source", ["ui-user-turn", "bot-text", "github-comment", "mock-fixture"])
def test_r3b1_brain_receipt_pins_require_independent_transport(r3b1_pins, composition, source):
    p = r3b1_pins
    make, _, _, _, _, _ = composition
    candidate = replace(p.brain, provenance_ref="fixture:" + source)
    brain = r.NativeBrainAdapter()
    assert brain.correlate(p.event, p.registration, None) is None
    with pytest.raises(r.DependencyHold, match="^BRAIN_RECEIPT_UNAVAILABLE$"):
        brain.authenticate_receipt(p.event, p.registration, None, candidate)
    runner = make()
    assert runner.orchestrator._completed == {} and runner.orchestrator._receipts == {}


@pytest.mark.parametrize("boundary", ["sender", "brain"])
def test_r3b1_synthetic_mode_cannot_promote_native_receipt_values(composition, r3b1_pins,
                                                               monkeypatch, boundary):
    make, event, registration, _, path, _ = composition
    runner = make()
    if boundary == "sender":
        original = runner.sender.send
        monkeypatch.setattr(runner.sender, "send", lambda *args: replace(
            original(*args), native_turn=r3b1_pins.turn))
    else:
        original = runner.brain.correlate
        monkeypatch.setattr(runner.brain, "correlate", lambda *args: replace(
            original(*args), authenticated_receipt=r3b1_pins.brain))
    assert runner.run_once() == r.Result("UNKNOWN_EFFECT", "SYNTHETIC_NATIVE_PROOF_FORBIDDEN",
                                       "SYNTHETIC_FIXTURE")
    assert "BRAIN_CORRELATED" not in record(path, event, registration)["history"]
    assert runner.orchestrator._completed == {} and runner.orchestrator._receipts == {}


@pytest.mark.parametrize("reference", ["", None, 0, ["same", "same"], " \n"])
def test_r3b1_invalid_observed_turn_reference_is_unknown_without_retry(composition, monkeypatch, reference):
    make, event, registration, _, path, _ = composition
    runner = make()
    original = runner.sender.send
    monkeypatch.setattr(runner.sender, "send", lambda *args: replace(
        original(*args), user_turn_ref=reference))
    assert runner.run_once() == r.Result("UNKNOWN_EFFECT", "UI_OBSERVATION_UNPROVEN", "SYNTHETIC_FIXTURE")
    assert record(path, event, registration)["history"] == ["PENDING", "RESERVED", "UNKNOWN_EFFECT"]
    assert runner.run_once().replayed and runner.sender.calls == 1
    reopened = make()
    assert reopened.run_once().status == "UNKNOWN_EFFECT" and reopened.sender.calls == 0


def test_r3b1_one_user_turn_reference_cannot_satisfy_two_events(composition, monkeypatch):
    make, event, registration, _, path, _ = composition
    runner = make()
    assert runner.run_once().status == "ACK"
    first_ref = record(path, event, registration)["evidence"]["user_turn_ref"]
    next_event = replace(event, event_id="ef02-completion-v1:" + "d" * 64)
    runner.source.event = next_event
    original = runner.sender.send
    monkeypatch.setattr(runner.sender, "send", lambda *args: replace(
        original(*args), user_turn_ref=first_ref))
    assert runner.run_once() == r.Result("UNKNOWN_EFFECT", "USER_TURN_REFERENCE_REUSED", "SYNTHETIC_FIXTURE")
    assert runner.run_once().replayed and runner.sender.calls == 2
    assert record(path, next_event, registration)["history"] == ["PENDING", "RESERVED", "UNKNOWN_EFFECT"]
    reopened = make(source=r.SyntheticSource(next_event))
    assert reopened.run_once().status == "UNKNOWN_EFFECT" and reopened.sender.calls == 0


@pytest.mark.parametrize("phase", ["RESERVED", "UI_TURN_OBSERVED", "ACK"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_r3b1_commit_failure_preserves_durable_effect_boundary(composition, monkeypatch, phase, position):
    make, event, registration, _, path, _ = composition
    runner = make()
    original = r._SQLiteTransaction.write

    def interrupted(txn, data):
        selected = data["events"][event.key(registration)]["status"] == phase
        if selected and position == "before":
            raise Crash()
        original(txn, data)
        if selected and position == "after":
            raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(r._SQLiteTransaction, "write", interrupted)
        with pytest.raises(Crash):
            runner.run_once()
    recovered = make()
    result = recovered.run_once()
    safe_pending = phase == "RESERVED" and position == "before"
    committed_ack = phase == "ACK" and position == "after"
    assert result.status == ("ACK" if safe_pending or committed_ack else "UNKNOWN_EFFECT")
    assert recovered.sender.calls == (1 if safe_pending else 0)
    assert runner.sender.calls == (0 if phase == "RESERVED" else 1)
    assert recovered.run_once().replayed


@pytest.mark.parametrize("failure", ["missing", "timeout", "dependency"])
def test_r3b1_missing_post_reserve_witness_never_replays(composition, monkeypatch, failure):
    """TEST_ONLY_MOCK effect seam; never a successful native provider."""
    make, event, registration, _, path, _ = composition
    runner = make()
    calls = []

    def no_witness(*args):
        assert record(path, event, registration)["status"] == "RESERVED"
        calls.append(failure)
        if failure == "timeout":
            raise TimeoutError()
        if failure == "dependency":
            raise r.DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")
        return None

    monkeypatch.setattr(runner.sender, "send", no_witness)
    assert runner.run_once().status == "UNKNOWN_EFFECT"
    assert runner.run_once().replayed and calls == [failure]
    reopened = make()
    assert reopened.run_once().status == "UNKNOWN_EFFECT" and reopened.sender.calls == 0
    assert reopened.orchestrator._completed == {}


def test_r3b1_native_recovery_never_repeats_readiness_or_send(r3a_ingress):
    i = r3a_ingress
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    assert runner.run_once().status == "HOLD_DEPENDENCY"
    path = r.Path(i["config"]["journal"])
    with sqlite3.connect(path) as db:
        saved = json.loads(db.execute("SELECT record FROM events").fetchone()[0])
        saved.update(status="RESERVED", code="SEND_RESERVED", history=["PENDING", "RESERVED"])
        db.execute("UPDATE events SET record=?", (json.dumps(saved),))
    calls, reads = list(i["native_calls"]), i["browser"].reads
    for _ in range(2):
        reopened = r.compose_native_source_only(i["config_path"], i["root"])
        assert reopened.run_once() == r.Result("UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY",
                                               "GITHUB_AUTHENTICATED", True)
        assert reopened.orchestrator._completed == {} and reopened.orchestrator._receipts == {}
    assert i["native_calls"] == calls and i["browser"].reads == reads


def test_r3b1_changing_composer_labels_do_not_supply_stable_native_identity(r3a_ingress, monkeypatch):
    i = r3a_ingress
    original = i["page"].evaluate

    def replaced_composer(script):
        result = original(script)
        i["page"].composer_ref = "DOM-label-" + str(i["page"].reads)
        return result

    monkeypatch.setattr(i["page"], "evaluate", replaced_composer)
    assert r.run_native_source_only(i["config_path"], i["root"]) == r.Result(
        "HOLD_DEPENDENCY", "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE", "GITHUB_AUTHENTICATED")
    assert i["page"].reads == 2
    assert_read_only_pending(i)
