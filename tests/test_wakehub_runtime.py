"""WAKEHUB.runtime-r0: R1 source closure, never Windows native host proof.

Only custody observation is stubbed for deterministic cross-OS temporary files.
Windows fixture files inherit an explicit process-SID grant, rather than relying
on the temporary directory's potentially non-inheritable OWNER RIGHTS grant.
Private SQLite durability, provenance checks, actual A03 consumer and P0
receipt validation execute normally. No Git, browser, login or external writes.
"""
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import hmac
import io
import json
import os
import socket
import sqlite3
import subprocess
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
        monkeypatch.setattr(runner.orchestrator, "record_completed", crash)
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
    make, _, _, handoff, _, _ = composition
    spec = handoff.completed.spec
    runner = make(orchestrator=ProductionPipelineOrchestrator(
        authorized_specs=((spec.authorization.authorization_ref, spec.sha256),)))
    assert runner.run_once().status == "UNKNOWN_EFFECT"
    assert runner.orchestrator.dispatch(spec.idempotency_key).status == "NOT_DISPATCHED"


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
    assert runner.run_once() == r.Result("BLOCKED_EXTERNAL", "UNSUPPORTED_NATIVE_OWNER", "GITHUB_AUTHENTICATED")


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
    assert result == asdict(r.Result("HOLD", "SOURCE_READY_NATIVE_UNKNOWN", "GITHUB_AUTHENTICATED"))
    assert len(i["calls"]) == 6  # Reader AND independently authenticated artifact verifier.
    path = r.Path(i["config"]["journal"])
    assert record(path, r.Event(i["event"]["event_id"], "a" * 40, 1, digest(i["event"]),
                               "GITHUB_AUTHENTICATED"), i["registration"])["history"] == ["PENDING"]
    again = r.run_read_only(i["config_path"], i["root"])
    assert again.code == "SOURCE_READY_NATIVE_UNKNOWN"


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
