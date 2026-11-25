"""WAKEHUB.runtime-r0: R0/R1/R2/R3A/R3B2B source closure, never native host proof.

Only custody observation is stubbed for deterministic cross-OS temporary files.
Windows fixture files inherit an explicit process-SID grant, rather than relying
on the temporary directory's potentially non-inheritable OWNER RIGHTS grant.
Private SQLite durability, provenance checks, actual A03 consumer and P0
receipt validation execute normally. No Git, browser, login or external writes.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
import faulthandler
import hashlib
import hmac
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryFile
from types import FunctionType, SimpleNamespace
from urllib.error import HTTPError
import zipfile

import pytest
from _pytest.faulthandler import fault_handler_stderr_fd_key

from ai_commerce.production_orchestration.contracts import ProductionStageReceipt, digest
from ai_commerce.production_orchestration.orchestrator import ProductionPipelineOrchestrator
from ai_commerce.remote.cdp_doorbell import _PlaywrightInventory
from ai_commerce.remote.chrome_acquisition import ChromeEnvironment
from ai_commerce.remote.completion_event import build_completion_event
from ai_commerce.remote.conversation_binding import ResolvedBinding
from ai_commerce.remote.local_wake_trigger import PinnedRun
from ai_commerce.remote.wake_adapter import ProvenanceError
from ai_commerce.wakehub_runtime import runner as r
from ai_commerce.wakehub_runtime.__main__ import main


_EXTERNAL_EFFECTS = frozenset({
    "socket.__new__", "socket.bind", "socket.connect", "socket.sendto", "socket.sendmsg",
    "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo",
    "subprocess.Popen", "_winapi.CreateProcess", "os.system", "os.exec", "os.spawn",
    "os.posix_spawn", "os.fork", "os.forkpty", "os.startfile", "os.startfile/2",
})
_WORKER_MODULES = {"_pytest", "pytest", "pluggy", "xdist", "execnet"}
_ORIGINAL_TRANSPORTS = (socket.socket, socket.create_connection, subprocess.Popen)
_offline_active = False
_offline_hook_installed = False


class _ExternalEffectDenied(AssertionError):
    pass


def _deny_external_effect(event, args):
    if not _offline_active or event not in _EXTERNAL_EFFECTS:
        return
    frame = sys._getframe(1)
    while frame is not None:
        package = frame.f_globals.get("__name__", "").partition(".")[0]
        if package in _WORKER_MODULES:
            return
        if frame.f_globals is globals() or package == "ai_commerce":
            raise _ExternalEffectDenied(f"External operation attempted: {event}")
        frame = frame.f_back


@contextmanager
def _offline_effects():
    global _offline_active, _offline_hook_installed
    if not _offline_hook_installed:
        sys.addaudithook(_deny_external_effect)
        _offline_hook_installed = True
    previous = _offline_active
    _offline_active = True
    try:
        # Fail closed if an existing audit hook prevented registration.
        try:
            sys.audit("socket.__new__", None, socket.AF_INET, socket.SOCK_STREAM, 0)
        except _ExternalEffectDenied:
            pass
        else:
            raise AssertionError("WakeHub offline audit hook unavailable")
        yield
    finally:
        # CPython audit hooks cannot be unregistered. This hook stays inert
        # outside the fixture, including during worker shutdown.
        _offline_active = previous


_COMPOSITION_TEST = "test_composition_persists_each_state_and_typed_consumer_receipt"
_COMPOSITION_FILES = {"test_wakehub_runtime.py", "_private_state.py", "runner.py"}
_COMPOSITION_FRAME = re.compile(
    rb'[/\\](test_wakehub_runtime\.py|_private_state\.py|runner\.py)", line ([0-9]+)')


class _CompositionWorkerDiagnostics:
    def __init__(self, config):
        self.worker = config.workerinput["workerid"]
        # Use the same uncaptured stderr as pytest's native fault handler.
        # On Windows execnet preserves this stream separately from fd 2.
        self.stderr = config.stash.get(fault_handler_stderr_fd_key, None)
        self.phase = None
        self.first_error = False

    def write(self, message):
        if self.stderr is not None:
            try:
                line = f"WakeHub composition [{self.worker}] {message}\n"
                os.write(self.stderr, line.encode("ascii", errors="backslashreplace"))
            except OSError:
                pass  # Diagnostics must never change the worker's outcome.

    def start(self):
        self.phase = "setup-started"
        self.write(f"{self.phase} native_faulthandler={faulthandler.is_enabled()}")

    def report_error(self, phase, excinfo):
        if not self.first_error:
            self.first_error = True
            location = "outside_composition_sources"
            for entry in reversed(excinfo.traceback):
                filename = os.path.basename(str(entry.path))
                if filename in _COMPOSITION_FILES:
                    location = f"{filename}:{entry.lineno + 1}"
                    break
            self.write(f"first_exception phase={phase} type={excinfo.type.__name__} at={location}")
            self.fault_snapshot()

    def fault_snapshot(self):
        # Report a bounded snapshot of live test/owner frames. Keep absolute
        # paths, thread addresses, exception values and locals out of the log.
        try:
            with TemporaryFile() as stack:
                faulthandler.dump_traceback(file=stack)
                stack.seek(0)
                frames = _COMPOSITION_FRAME.findall(stack.read(65536))
        except (OSError, RuntimeError):
            self.write("faulthandler_snapshot=unavailable")
            return
        locations = [f"{file.decode('ascii')}:{line.decode('ascii')}" for file, line in frames[:8]]
        self.write(f"faulthandler_snapshot={','.join(locations) or 'no_composition_frames'}")

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_makereport(self, item, call):
        if item.name == _COMPOSITION_TEST and item.module is sys.modules[__name__]:
            self.phase = f"{call.when}-{'ok' if call.excinfo is None else 'raised'}"
            if call.excinfo is not None:
                self.report_error(call.when, call.excinfo)
            self.write(self.phase)

    @pytest.hookimpl(tryfirst=True)
    def pytest_internalerror(self, excinfo):
        if self.phase is not None:
            self.report_error("worker-internal", excinfo)

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, exitstatus):
        if self.phase is not None:
            self.write(f"pytest_exit={int(exitstatus)} last_phase={self.phase}")
            if self.phase != "teardown-ok" and not self.first_error:
                self.fault_snapshot()


@pytest.fixture(scope="session")
def composition_worker_diagnostics(request):
    if hasattr(request.config, "workerinput"):
        diagnostics = _CompositionWorkerDiagnostics(request.config)
        # Remain registered through sessionfinish so the worker can report its
        # own exit status before xdist processes workerfinished.
        request.config.pluginmanager.register(diagnostics)
        return diagnostics
    return None


@pytest.fixture(autouse=True)
def offline(request, composition_worker_diagnostics):
    if request.node.name == _COMPOSITION_TEST and composition_worker_diagnostics is not None:
        composition_worker_diagnostics.start()
    with _offline_effects():
        yield
    # Exercise the installed hook after deactivation without creating a socket.
    sys.audit("socket.__new__", None, socket.AF_INET, socket.SOCK_STREAM, 0)


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


def test_offline_fixture_denies_effects_without_replacing_transport_globals():
    assert all(current is original for current, original in zip(
        (socket.socket, socket.create_connection, subprocess.Popen), _ORIGINAL_TRANSPORTS))
    with pytest.raises(_ExternalEffectDenied, match=r"socket\.__new__"):
        socket.socket(32767, socket.SOCK_STREAM)
    with pytest.raises(_ExternalEffectDenied, match=r"socket\.getaddrinfo"):
        socket.getaddrinfo("127.0.0.1", 0, flags=socket.AI_NUMERICHOST)
    with pytest.raises(_ExternalEffectDenied, match=r"socket\.connect"):
        sys.audit("socket.connect", None, ("127.0.0.1", 0))
    with pytest.raises(_ExternalEffectDenied, match=r"subprocess\.Popen"):
        subprocess.Popen([""], executable="")

    # Exercise the control-traffic boundary with an audit event and no I/O.
    def worker_socket_event():
        sys.audit("socket.__new__", None, socket.AF_INET, socket.SOCK_STREAM, 0)

    worker_globals = {"__name__": "execnet.gateway_base", "sys": sys, "socket": socket}
    FunctionType(worker_socket_event.__code__, worker_globals)()


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


def test_send_only_records_ui_evidence_and_never_needs_brain(composition):
    make, event, registration, _, path, _ = composition
    runner = make(brain=None)
    expected = r.Result("UI_TURN_OBSERVED", "UI_USER_TURN_OBSERVED", "SYNTHETIC_FIXTURE")
    assert runner.run_once() == expected
    saved = record(path, event, registration)
    assert saved["history"] == ["PENDING", "RESERVED", "UI_TURN_OBSERVED"]
    assert saved["evidence"] == {"user_turn_ref": "fixture:user-turn:" + event.key(registration),
                                 "marker": registration.marker}
    assert runner.run_once() == replace(expected, replayed=True)
    reopened = make(brain=None)
    assert reopened.run_once() == replace(expected, replayed=True)
    assert record(path, event, registration) == saved
    assert runner.sender.calls == 1 and reopened.sender.calls == 0
    assert runner.orchestrator._completed == reopened.orchestrator._completed == {}


class Crash(BaseException):
    pass


@pytest.mark.parametrize("phase", ["RESERVED", "UI_TURN_OBSERVED", "BRAIN_CORRELATED"])
def test_crash_recovery_resumes_after_last_committed_phase(composition, phase, monkeypatch):
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
    expected = (("UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY") if phase == "RESERVED"
                else ("ACK", "CONSUMER_RECEIPT_RECORDED"))
    assert result == r.Result(*expected, "SYNTHETIC_FIXTURE", True)
    assert recovered.sender.calls == 0
    assert bool(recovered.orchestrator._completed) == (phase == "UI_TURN_OBSERVED")
    assert make().run_once().status == expected[0]


@pytest.mark.parametrize("receipt", ["matching", "cross-event", "malformed"])
def test_interrupted_delivery_can_only_reconcile_existing_receipt(composition, monkeypatch, receipt):
    make, event, registration, _, path, _ = composition
    runner = make(brain=None)
    original = runner.sender.send
    sent = []

    def crash_after_send(*args):
        assert record(path, event, registration)["status"] == "RESERVED"
        sent.append(original(*args))
        raise Crash()

    monkeypatch.setattr(runner.sender, "send", crash_after_send)
    with pytest.raises(Crash):
        runner.run_once()
    assert runner.sender.calls == 1
    reopened = make(brain=None)
    lookups = []

    def lookup(current, binding):
        assert current == event and binding == registration
        assert record(path, event, registration)["status"] == "UNKNOWN_EFFECT"
        lookups.append(current)
        observation = sent[0]
        if receipt == "cross-event":
            other = replace(observation.receipt, event_id="ef02-completion-v1:" + "f" * 64)
            return replace(observation, receipt=other)
        if receipt == "malformed":
            return replace(observation, receipt=replace(observation.receipt, generation=True))
        return observation

    monkeypatch.setattr(reopened.sender, "reconcile", lookup)
    monkeypatch.setattr(reopened.sender, "preflight", lambda *args: pytest.fail("Readiness repeated"))
    if receipt == "matching":
        assert reopened.run_once() == r.Result("UI_TURN_OBSERVED", "UI_USER_TURN_OBSERVED",
                                               "SYNTHETIC_FIXTURE", True)
        assert record(path, event, registration)["history"] == [
            "PENDING", "RESERVED", "UNKNOWN_EFFECT", "UI_TURN_OBSERVED"]
        assert reopened.run_once().status == "UI_TURN_OBSERVED"
    else:
        assert reopened.run_once() == r.Result("UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY",
                                               "SYNTHETIC_FIXTURE", True)
        assert record(path, event, registration)["evidence"] == {}
    assert reopened.sender.calls == 0 and lookups == [event]


@pytest.mark.parametrize("failure, receipt", [
    ("timeout", "matching"),
    ("ambiguous", "matching"),
    ("malformed", "matching"),
    ("timeout", "missing"),
    ("ambiguous", "reused"),
    ("timeout", "grant-lost"),
])
def test_uncertain_delivery_can_only_reconcile_existing_receipt(composition, monkeypatch, failure, receipt):
    make, event, registration, _, path, _ = composition
    runner = make(brain=None)
    original = runner.sender.send
    sent = []
    failure_code = {"timeout": "ATTEMPT_UNPROVEN_NO_REPLAY",
                    "ambiguous": "DOORBELL_CLICK_AMBIGUOUS_NO_REPLAY",
                    "malformed": "UI_OBSERVATION_UNPROVEN"}[failure]
    unknown_history = ["PENDING", "RESERVED", "UNKNOWN_EFFECT"]

    def lose_receipt_after_send(*args):
        assert record(path, event, registration)["status"] == "RESERVED"
        sent.append(original(*args))
        if failure == "timeout":
            raise TimeoutError()
        if failure == "ambiguous":
            raise r.RuntimeHold(failure_code)
        return replace(sent[-1], receipt=replace(sent[-1].receipt, generation=True))

    monkeypatch.setattr(runner.sender, "send", lose_receipt_after_send)
    assert runner.run_once() == r.Result("UNKNOWN_EFFECT", failure_code, "SYNTHETIC_FIXTURE")
    assert runner.sender.calls == 1

    if receipt == "reused":
        other = replace(event, event_id="ef02-completion-v1:" + "f" * 64)
        assert make(source=r.SyntheticSource(other), brain=None).run_once().status == "UI_TURN_OBSERVED"
        reused_ref = record(path, other, registration)["evidence"]["user_turn_ref"]

    granted = True
    reopened = make(brain=None, grant_verifier=lambda *args: granted)
    lookups = []

    def lookup(current, binding):
        nonlocal granted
        assert current == event and binding == registration
        assert record(path, event, registration) == {
            "event": asdict(event), "status": "UNKNOWN_EFFECT", "code": failure_code,
            "history": unknown_history, "evidence": {},
        }
        lookups.append(current)
        observation = sent[0]
        if receipt == "missing":
            return None
        if receipt == "reused":
            return replace(observation, user_turn_ref=reused_ref)
        if receipt == "grant-lost":
            granted = False
        return observation

    monkeypatch.setattr(reopened.sender, "reconcile", lookup)
    monkeypatch.setattr(reopened.sender, "preflight", lambda *args: pytest.fail("Readiness repeated"))
    if receipt == "matching":
        assert reopened.run_once() == r.Result("UI_TURN_OBSERVED", "UI_USER_TURN_OBSERVED",
                                               "SYNTHETIC_FIXTURE", True)
        saved = record(path, event, registration)
        assert saved["history"] == [*unknown_history, "UI_TURN_OBSERVED"]
        assert saved["evidence"]["user_turn_ref"] == sent[0].user_turn_ref
        assert reopened.run_once().status == "UI_TURN_OBSERVED"
    else:
        assert reopened.run_once() == r.Result("UNKNOWN_EFFECT", failure_code, "SYNTHETIC_FIXTURE", True)
        saved = record(path, event, registration)
        assert (saved["history"], saved["code"], saved["evidence"]) == (unknown_history, failure_code, {})
    assert saved["event"] == asdict(event)
    assert reopened.sender.calls == 0 and lookups == [event]


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


@pytest.mark.parametrize("blocked", ["DRAFT_PRESENT", "CDP_READ_UNAVAILABLE", "CHROME_OBSERVATION_UNKNOWN",
                                     "PREFLIGHT_UNPROVEN"])
def test_transient_readiness_keeps_same_event_pending_for_later_retry(composition, monkeypatch, blocked):
    make, event, registration, _, path, _ = composition
    runner = make(brain=None)
    reads = []

    def preflight(current):
        assert current == registration
        assert record(path, event, registration)["history"] == ["PENDING"]
        reads.append(current)
        if len(reads) == 1:
            if blocked == "PREFLIGHT_UNPROVEN":
                raise TimeoutError("fixture timeout")
            return blocked
        return None

    monkeypatch.setattr(runner.sender, "preflight", preflight)
    assert runner.run_once() == r.Result("HOLD", blocked, "SYNTHETIC_FIXTURE")
    assert runner.sender.calls == 0 and record(path, event, registration)["status"] == "PENDING"
    assert runner.run_once().status == "UI_TURN_OBSERVED"
    assert runner.run_once().replayed and runner.sender.calls == 1
    assert reads == [registration, registration]


def test_source_outage_and_drift_cannot_consume_a_pending_reservation(composition, monkeypatch):
    make, event, registration, _, path, _ = composition
    runner = make(brain=None)
    with monkeypatch.context() as patch:
        patch.setattr(runner.sender, "preflight", lambda *_: "CDP_READ_UNAVAILABLE")
        assert runner.run_once().code == "CDP_READ_UNAVAILABLE"
    saved = record(path, event, registration)

    def unavailable():
        raise r.GitHubReadBlocked("GITHUB_READ_UNAVAILABLE")

    with monkeypatch.context() as patch:
        patch.setattr(runner.source, "next_event", unavailable)
        assert runner.run_once().code == "GITHUB_READ_UNAVAILABLE"
    assert record(path, event, registration) == saved

    runner.source.event = replace(event, payload_sha256="d" * 64)
    assert runner.run_once().code == "EVENT_IDENTITY_DRIFT"
    assert runner.sender.calls == 0 and record(path, event, registration) == saved
    runner.source.event = event
    assert runner.run_once().status == "UI_TURN_OBSERVED"
    assert runner.sender.calls == 1


@pytest.mark.parametrize("drift", ["chat", "generation", "source", "marker", "event", "turn", "status", "brain",
                                    "witness"])
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
                  "brain": {"brain_continuation_proven": True}, "witness": {"witness_evidence": {"native": True}}}
        return replace(observation, receipt=replace(receipt, **values[drift]))

    monkeypatch.setattr(runner.sender, "send", wrong)
    result = runner.run_once()
    assert result.status == "UNKNOWN_EFFECT" and runner.sender.calls == 1
    assert runner.run_once().replayed and runner.sender.calls == 1
    assert runner.orchestrator._completed == {}


@pytest.mark.parametrize("failure", ["missing", "timeout"])
def test_response_can_be_reconciled_on_restart_without_resending(composition, monkeypatch, failure):
    make, event, registration, _, path, _ = composition
    runner = make()
    def unavailable(*args):
        assert record(path, event, registration)["status"] == "UI_TURN_OBSERVED"
        if failure == "timeout":
            raise TimeoutError("fixture timeout")
        return None

    monkeypatch.setattr(runner.brain, "correlate", unavailable)
    result = runner.run_once()
    assert result == r.Result("UI_TURN_OBSERVED", "BRAIN_RECEIPT_UNAVAILABLE", "SYNTHETIC_FIXTURE")
    saved = record(path, event, registration)
    assert saved["history"] == ["PENDING", "RESERVED", "UI_TURN_OBSERVED"]
    assert runner.orchestrator._completed == {}
    waiting = make(brain=None)
    assert waiting.run_once().status == "UI_TURN_OBSERVED" and waiting.sender.calls == 0
    assert record(path, event, registration) == saved
    recovered = make()
    assert recovered.run_once() == r.Result("ACK", "CONSUMER_RECEIPT_RECORDED", "SYNTHETIC_FIXTURE", True)
    assert recovered.sender.calls == 0 and runner.sender.calls == 1
    assert recovered.orchestrator.dispatch(event.key(registration)).status == "RECEIPT_RECORDED"
    assert record(path, event, registration)["evidence"]["user_turn_ref"] == saved["evidence"]["user_turn_ref"]


def test_legacy_response_failure_retains_observed_delivery_without_rewriting_history(composition):
    make, event, registration, _, path, _ = composition
    assert make(brain=None).run_once().status == "UI_TURN_OBSERVED"
    saved = record(path, event, registration)
    saved.update(status="BLOCKED_EXTERNAL", code="BRAIN_RECEIPT_UNAVAILABLE",
                 history=[*saved["history"], "BLOCKED_EXTERNAL"])
    with sqlite3.connect(path) as db:
        db.execute("UPDATE events SET record=?", (json.dumps(saved),))
    reopened = make()
    assert reopened.run_once() == r.Result("UI_TURN_OBSERVED", "BRAIN_RECEIPT_UNAVAILABLE",
                                           "SYNTHETIC_FIXTURE", True)
    assert reopened.sender.calls == 0 and reopened.orchestrator._completed == {}
    assert record(path, event, registration) == saved


@pytest.mark.parametrize("field", ["event_key", "marker", "user_turn_ref", "evidence_type"])
def test_brain_receipt_requires_exact_correlation(composition, field, monkeypatch):
    make, _, _, _, _, _ = composition
    runner = make()
    original = runner.brain.correlate
    monkeypatch.setattr(runner.brain, "correlate", lambda *args: replace(original(*args), **{field: "wrong"}))
    assert runner.run_once() == r.Result("UI_TURN_OBSERVED", "BRAIN_CORRELATION_MISMATCH", "SYNTHETIC_FIXTURE")
    assert runner.orchestrator._completed == {}


def test_wake_receipt_cannot_substitute_for_consumer_receipt(composition):
    make, _, _, handoff, _, _ = composition
    runner = make()
    runner.brain.handoff = replace(handoff, receipt=runner.sender.send(
        runner.source.event, runner.registration).receipt)
    assert runner.run_once() == r.Result("UI_TURN_OBSERVED", "CONSUMER_RECEIPT_REQUIRED", "SYNTHETIC_FIXTURE")
    assert runner.orchestrator._completed == {}


def test_unverified_consumer_receipt_cannot_ack(composition):
    make, event, registration, handoff, path, _ = composition
    spec = handoff.completed.spec
    runner = make(orchestrator=ProductionPipelineOrchestrator(
        authorized_specs=((spec.authorization.authorization_ref, spec.sha256),)))
    assert runner.run_once() == r.Result("UI_TURN_OBSERVED", "RECEIPT_NOT_VERIFIED", "SYNTHETIC_FIXTURE")
    assert runner.orchestrator.dispatch(spec.idempotency_key).status == "NOT_DISPATCHED"
    saved = record(path, event, registration)
    assert saved["history"] == ["PENDING", "RESERVED", "UI_TURN_OBSERVED"]
    reopened = make()
    assert reopened.run_once() == r.Result("ACK", "CONSUMER_RECEIPT_RECORDED", "SYNTHETIC_FIXTURE", True)
    assert reopened.sender.calls == 0 and runner.sender.calls == 1


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


def test_issue_1317_without_completion_artifact_yields_no_event():
    target = PinnedRun(17, 1, 19, 1317, "fixture-batch", "a" * 40)
    api = "https://api.github.com/repos/trung-via/ai-commerce"
    responses = {
        api + "/actions/runs/17": {
            "id": 17, "run_attempt": 1, "workflow_id": 19, "event": "issues",
            "status": "completed", "repository": {"full_name": "trung-via/ai-commerce"},
        },
        api + "/actions/runs/17/artifacts?per_page=100": {"total_count": 0, "artifacts": []},
    }

    def reject_artifact(*args, **kwargs):
        pytest.fail("Issue alone supplied no artifact to read or verify")

    reader = r.GitHubExactRunReader(token_provider=lambda: "fixture-token",
                                   read_json=lambda url, _: responses[url],
                                   read_archive=reject_artifact)
    authority = SimpleNamespace(verify_artifact=reject_artifact)
    source = r.GitHubEventSource(reader, target, authority)
    assert source.next_event() is None
    assert source.verified_event is None


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
    profile = owned / r.chrome_acquisition._CLEAN_9444_PROFILE
    (profile / "Default").mkdir(parents=True)
    executable = local / "Google" / "Chrome" / "Application" / "chrome.exe"
    executable.parent.mkdir(parents=True)
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
    archives = {api + "/actions/artifacts/29/zip": raw}

    def read_json(url, token, **kwargs):
        assert token == "fixture-read-only-token"
        calls.append(url)
        return json.loads(json.dumps(responses[url]))

    def read_bytes(url, token, **kwargs):
        assert token == "fixture-read-only-token"
        calls.append(url)
        return archives[url]

    monkeypatch.setattr(r.GitHubRestArtifactAuthority, "_read_json", staticmethod(read_json))
    monkeypatch.setattr(r.GitHubRestArtifactAuthority, "_read_bytes", staticmethod(read_bytes))
    return dict(config_path=config_path, config=config, root=root, event=event, registration=registration,
                registered=registered, registry=registry, grants=grants, chrome=chrome, save=save,
                calls=calls, run=run, responses=responses, archives=archives)


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
    sid = r._private_identity(i["config_path"]) if os.name == "nt" else "S-1-5-21-111-222-333-1001"
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
    monkeypatch.setattr(acq, "ensure_clean_9444", forbidden)
    monkeypatch.setattr(r.NativeChrome9444, "send", forbidden)
    monkeypatch.setattr(r.LocalChromeDoorbellAdapter, "send_wake", forbidden)
    monkeypatch.setattr(r.cdp_doorbell, "_PlaywrightInventory", lambda: browser)
    return {**i, "environment": env, "inventory": inventory, "process": process,
            "page": page, "browser": browser, "native_calls": native_calls}


def assert_read_only_pending(i, code="EVENT_PENDING", *, version=2):
    path = r.Path(i["config"]["journal"])
    event = r.Event(i["event"]["event_id"], "a" * 40, 1, digest(i["event"]), "GITHUB_AUTHENTICATED")
    saved = record(path, event, i["registration"])
    assert saved["history"] == ["PENDING"] and saved["code"] == code
    assert saved["evidence"] == {}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
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
    assert runner.run_once() == r.Result("UI_TURN_OBSERVED", "BRAIN_RECEIPT_UNAVAILABLE", "SYNTHETIC_FIXTURE")
    assert record(path, event, registration)["history"] == [
        "PENDING", "RESERVED", "UI_TURN_OBSERVED"]
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
        code = ("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE" if candidate is None
                else "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE")
        with pytest.raises(r.DependencyHold, match=f"^{code}$"):
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
    expected = "UNKNOWN_EFFECT" if boundary == "sender" else "UI_TURN_OBSERVED"
    assert runner.run_once() == r.Result(expected, "SYNTHETIC_NATIVE_PROOF_FORBIDDEN", "SYNTHETIC_FIXTURE")
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
    committed_delivery = phase == "ACK" or (phase == "UI_TURN_OBSERVED" and position == "after")
    assert result.status == ("ACK" if safe_pending or committed_delivery else "UNKNOWN_EFFECT")
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


def test_r3b1_native_recovery_never_repeats_readiness_or_send(r3a_ingress, monkeypatch):
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
        monkeypatch.setattr(reopened.sender, "reconcile",
                            lambda *args: pytest.fail("Unenrolled native receipt lookup invoked"))
        assert reopened.run_once() == r.Result("UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY",
                                               "GITHUB_AUTHENTICATED", True)
        assert reopened.orchestrator._completed == {} and reopened.orchestrator._receipts == {}
    assert i["native_calls"] == calls and i["browser"].reads == reads


@pytest.mark.parametrize("state, fault, expected", [
    ("USER_TURN_AUTHENTICATED", "missing-origin", ("HOLD_DEPENDENCY", "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE")),
    ("USER_TURN_AUTHENTICATED", "invalid-authority",
     ("HOLD_DEPENDENCY", "DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")),
    ("USER_TURN_AUTHENTICATED", "missing-grant", ("HOLD_DEPENDENCY", "DELIVER_WAKE_GRANT_REQUIRED")),
    ("USER_TURN_AUTHENTICATED", "stale-event", ("HOLD", "EVENT_IDENTITY_DRIFT")),
    ("USER_TURN_AUTHENTICATED", "read-only", ("HOLD", "NATIVE_RECEIPT_UNPROVEN")),
    ("BRAIN_CORRELATED", "missing-origin", ("HOLD_DEPENDENCY", "BRAIN_RECEIPT_UNAVAILABLE")),
    ("BRAIN_CORRELATED", "read-only", ("HOLD", "NATIVE_RECEIPT_UNPROVEN")),
    ("UI_TURN_OBSERVED", "missing-origin", ("UI_TURN_OBSERVED", "UI_USER_TURN_OBSERVED")),
])
def test_native_restart_requires_independent_proof_with_brain_omitted(r3a_ingress, monkeypatch,
                                                                    state, fault, expected):
    i = r3a_ingress
    registration = i["registration"]
    event = r.Event(i["event"]["event_id"], registration.source_sha, i["event"]["run_attempt"],
                    digest(i["event"]), "GITHUB_AUTHENTICATED")
    request = r.DoorbellRequest.for_event(event, registration)
    if fault == "missing-grant":
        i["grants"]["grants"].pop()
        i["save"](r.Path(i["config"]["grants"]), i["grants"])
    compose = r.compose_read_only if fault == "read-only" else r.compose_native_source_only
    runner = compose(i["config_path"], i["root"])
    runner.brain = None

    def forbidden(*args, **kwargs):
        pytest.fail("Recovery repeated readiness, a send, or an unenrolled lookup")

    for method in ("preflight", "send", "reconcile"):
        monkeypatch.setattr(type(runner.sender), method, forbidden)
    if fault == "invalid-authority":
        runner.sender.turn_authority = SimpleNamespace(authenticate=forbidden, native_authority=True)

    # The journal accepts these well-shaped claims, which supply no native authentication.
    proof = {"marker": registration.marker, "user_turn_ref": "fixture:stored-message",
             "user_turn_id": "fixture:stored-turn", "turn_provenance_ref": "fixture:stored-provenance",
             "route_generation": registration.binding.generation,
             "doorbell_sha256": r.bytes_digest(request.canonical_bytes)}
    history = ["PENDING", "RESERVED", state]
    if state == "BRAIN_CORRELATED":
        history = ["PENDING", "RESERVED", "USER_TURN_AUTHENTICATED", state]
        proof.update(correlation_ref="fixture:correlation", consumer_id="fixture:consumer",
                     receipt_sha256="1" * 64, completed_sha256="2" * 64)
    stored_event = replace(event, payload_sha256="0" * 64) if fault == "stale-event" else event
    code = {"USER_TURN_AUTHENTICATED": state, "BRAIN_CORRELATED": "BRAIN_RECEIPT_CORRELATED",
            "UI_TURN_OBSERVED": "UI_USER_TURN_OBSERVED"}[state]
    saved = {"event": asdict(stored_event), "status": state, "code": code,
             "history": history, "evidence": proof}
    with runner.journal.locked(registration) as (txn, data):
        data["events"][event.key(registration)] = saved
        txn.write(data)

    result = runner.run_once()
    assert (result.status, result.code) == expected
    assert result.evidence_type == "GITHUB_AUTHENTICATED"
    assert record(runner.journal.path, event, registration) == saved
    assert i["native_calls"] == [] and i["browser"].reads == i["page"].reads == 0
    assert runner.orchestrator._completed == {} and runner.orchestrator._receipts == {}


def test_native_source_only_without_brain_still_requires_enrolled_origin(r3a_ingress):
    i = r3a_ingress
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    runner.brain = None
    assert runner.run_once() == r.Result("HOLD_DEPENDENCY", "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE",
                                        "GITHUB_AUTHENTICATED")
    assert_read_only_pending(i)
    assert i["page"].reads == 2 and runner.sender.owner.adapter.allow_submit is False


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


@pytest.fixture
def r3b2_claims(r3b1_pins):
    """Unverified source-only envelopes; no private provider or grant is issued."""
    p = r3b1_pins
    c = r.cdp_turn
    request = r._turn_request(p.registration, p.event, p.request, p.origin)
    scope = request.scope
    observation = c.BrowserObservation(scope, "fixture:observation", 1, 1, True, True)
    grant = c.SignedWakeGrant(scope, "fixture:grant", "fixture:issuer", "fixture:key",
                              b"unverified-signature", 100, 200)
    reservation = c.DurableReservation(scope, "fixture:reservation", grant.grant_ref,
                                       "fixture:claimed-durability", 110)
    attestation = c.NativeAttestation(
        scope, c.AttestationPhase.PRE_SEND, c.EvidenceSource.NATIVE_PRIVATE_PROVIDER,
        "fixture:attestation", "fixture:issuer", "fixture:key", b"unverified-signature",
        grant.grant_ref, reservation.reservation_ref, observation.observation_ref, 115, 180)
    evidence = c.TurnEvidence(observation, grant, reservation, attestation)
    user_turn = c.ChatGPTUserTurnRef(
        scope, "native-looking-opaque-id", "native-looking-message-ref", attestation.attestation_ref,
        grant.grant_ref, reservation.reservation_ref, c.EvidenceSource.NATIVE_PRIVATE_PROVIDER)
    return SimpleNamespace(p=p, request=request, evidence=evidence, user_turn=user_turn, now=120)


def test_r3b2_bridge_preserves_exact_message_and_detached_scope(r3b2_claims):
    f = r3b2_claims
    p, mapped = f.p, f.request
    assert type(mapped) is r.cdp_turn.WakeRequest
    assert mapped.message_bytes == p.request.canonical_bytes
    assert mapped.scope.event_id == p.event.event_id
    assert mapped.scope.message_sha256 == hashlib.sha256(p.request.canonical_bytes).hexdigest()
    assert asdict(mapped.scope.origin) == {
        "account_id": p.origin.account_id,
        "conversation_id": p.registration.binding.chat_url.rsplit("/", 1)[1],
        "session_ref": p.origin.account_session_ref,
        "route_generation": p.registration.binding.generation,
        "registration_marker": p.registration.marker,
        "browser_custody_ref": p.origin.chrome_custody_ref,
        "chrome_profile_id": p.origin.chrome_profile_id,
        "context_ref": p.origin.context_ref, "tab_ref": p.origin.tab_ref,
        "composer_ref": p.origin.composer_ref, "cdp_endpoint": "http://127.0.0.1:9444"}
    assert asdict(mapped.scope.registered_context) == {
        "chat_url": p.registration.binding.chat_url,
        "windows_sid": p.origin.windows_sid,
        "windows_session_id": p.origin.windows_session_id,
        "windows_logon_id": p.origin.windows_logon_id,
        "desktop": p.origin.desktop,
        "event_payload_sha256": p.event.payload_sha256,
    }
    with pytest.raises(FrozenInstanceError):
        mapped.scope.event_id = "changed"
    assert "native-looking" not in repr(mapped) and "fixture:" not in repr(mapped)
    with pytest.raises(r.DependencyHold, match="^NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE$"):
        r.NativeOriginAuthority().authenticate(p.registration, p.origin)


@pytest.mark.parametrize("fault,code", [
    ("event", "NATIVE_TURN_REQUEST_MISMATCH"),
    ("event_key", "NATIVE_TURN_REQUEST_MISMATCH"),
    ("request_marker", "NATIVE_TURN_REQUEST_MISMATCH"),
    ("request_generation", "NATIVE_TURN_REQUEST_MISMATCH"),
    ("origin_generation", "NATIVE_TURN_ORIGIN_MISMATCH"),
    ("origin_marker", "NATIVE_TURN_ORIGIN_MISMATCH"),
    ("origin_chat", "NATIVE_TURN_ORIGIN_MISMATCH"),
    ("origin_missing", "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE"),
    ("origin_duck", "NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE"),
    ("synthetic", "NATIVE_TURN_SCOPE_UNAVAILABLE"),
    ("source_sha", "NATIVE_TURN_SCOPE_UNAVAILABLE"),
])
def test_r3b2_bridge_rejects_scope_drift_and_synthetic_inputs(r3b1_pins, fault, code):
    p = r3b1_pins
    event, registration, request, origin = p.event, p.registration, p.request, p.origin
    if fault == "event":
        event = replace(event, event_id="ef02-completion-v1:" + "f" * 64)
    elif fault == "event_key":
        request = replace(request, event_key="f" * 64)
    elif fault == "request_marker":
        request = replace(request, marker="fixture:old-marker")
    elif fault == "request_generation":
        request = replace(request, binding=replace(request.binding, generation=2))
    elif fault == "origin_generation":
        origin = replace(origin, binding=replace(origin.binding, generation=2))
    elif fault == "origin_marker":
        origin = replace(origin, marker="fixture:old-marker")
    elif fault == "origin_chat":
        origin = replace(origin, binding=replace(origin.binding,
            chat_url="https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"))
    elif fault == "origin_missing":
        origin = None
    elif fault == "origin_duck":
        origin = SimpleNamespace(**asdict(origin))
    elif fault == "synthetic":
        event = replace(event, evidence_type="SYNTHETIC_FIXTURE")
        registration = replace(registration, evidence_type="SYNTHETIC_FIXTURE")
    else:
        event = replace(event, source_sha="f" * 40)
    with pytest.raises(r.DependencyHold, match="^" + code + "$"):
        r._turn_request(registration, event, request, origin)


@pytest.mark.parametrize("field,value", [
    ("account_id", None), ("account_id", "other-account"),
    ("session_ref", None), ("session_ref", "other-session"),
    ("conversation_id", "other-chat"), ("route_generation", None), ("route_generation", 2),
    ("registration_marker", None), ("registration_marker", "old-marker"),
    ("browser_custody_ref", "other-custody"), ("chrome_profile_id", "other-profile"),
    ("context_ref", "other-context"), ("tab_ref", "other-tab"),
    ("composer_ref", "other-composer"), ("cdp_endpoint", "http://127.0.0.1:9222"),
])
def test_r3b2_observed_identity_never_overrides_expected_pins(r3b2_claims, field, value):
    f = r3b2_claims
    wrong = replace(f.request.scope, origin=replace(f.request.scope.origin, **{field: value}))
    evidence = replace(f.evidence, observation=replace(f.evidence.observation, scope=wrong))
    sender = r.NativeSender(f.p.root, f.p.registration)
    with pytest.raises(r.DependencyHold, match="^DOORBELL_SCOPE_MISMATCH$"):
        sender.require_turn_reference(f.p.request, event=f.p.event, origin=f.p.origin,
                                      evidence=evidence, now=f.now)
    assert sender.owner.adapter.allow_submit is False


@pytest.mark.parametrize("change", ["windows_sid", "windows_session_id", "windows_logon_id",
                                    "event_payload"])
def test_r3b2_private_claims_cannot_rebind_to_changed_runner_context(r3b2_claims, change):
    f = r3b2_claims
    event, origin = f.p.event, f.p.origin
    if change == "event_payload":
        event = replace(event, payload_sha256="e" * 64)
    else:
        values = {"windows_sid": "S-1-5-21-111-222-333-1002", "windows_session_id": 8,
                  "windows_logon_id": "0000000000000002"}
        origin = replace(origin, **{change: values[change]})
    assert r.DoorbellRequest.for_event(event, f.p.registration) == f.p.request
    with pytest.raises(r.DependencyHold, match="^DOORBELL_SCOPE_MISMATCH$"):
        r.NativeTurnAuthority().preflight(f.p.request, registration=f.p.registration,
            event=event, origin=origin, evidence=f.evidence, now=f.now)


@pytest.mark.parametrize("part,changes,code", [
    ("observation", None, "OBSERVATION_MISSING"),
    ("observation", {"context_count": 2}, "SURFACE_NOT_EXCLUSIVE"),
    ("observation", {"tab_count": 2}, "SURFACE_NOT_EXCLUSIVE"),
    ("observation", {"composer_clean": False}, "COMPOSER_DIRTY"),
    ("observation", {"surface_stable": False}, "SURFACE_UNSTABLE"),
    ("grant", None, "GRANT_MISSING"),
    ("grant", {"signature": b""}, "GRANT_INVALID"),
    ("grant", {"expires_at": 120}, "GRANT_NOT_FRESH"),
    ("grant", {"use_count": 1}, "GRANT_REUSED"),
    ("reservation", None, "RESERVATION_MISSING"),
    ("reservation", {"grant_ref": "other-grant"}, "RESERVATION_SCOPE_MISMATCH"),
    ("reservation", {"state": "PENDING"}, "RESERVATION_NOT_DURABLE_RESERVED"),
    ("reservation", {"durable_receipt_ref": ""}, "RESERVATION_NOT_DURABLE_RESERVED"),
    ("attestation", None, "NATIVE_ATTESTATION_MISSING"),
    ("attestation", {"source": r.cdp_turn.EvidenceSource.SYNTHETIC_FIXTURE}, "WRONG_AUTHORITY_SOURCE"),
    ("attestation", {"reservation_ref": "other-reservation"}, "ATTESTATION_SCOPE_MISMATCH"),
])
def test_r3b2_pre_send_dependencies_are_rejection_only(r3b2_claims, part, changes, code):
    f = r3b2_claims
    changed = replace(getattr(f.evidence, part), **changes) if changes is not None else None
    evidence = replace(f.evidence, **{part: changed})
    with pytest.raises(r.DependencyHold, match="^DOORBELL_" + code + "$"):
        r.NativeTurnAuthority().preflight(f.p.request, registration=f.p.registration,
            event=f.p.event, origin=f.p.origin, evidence=evidence, now=f.now)


@pytest.mark.parametrize("part", ["observation", "grant", "reservation", "attestation"])
def test_r3b2_claimed_digest_must_match_canonical_message(r3b2_claims, part):
    f = r3b2_claims
    changed = replace(getattr(f.evidence, part),
                      scope=replace(f.request.scope, message_sha256="0" * 64))
    evidence = replace(f.evidence, **{part: changed})
    with pytest.raises(r.DependencyHold):
        r.NativeTurnAuthority().preflight(f.p.request, registration=f.p.registration,
            event=f.p.event, origin=f.p.origin, evidence=evidence, now=f.now)
    with pytest.raises(r.RuntimeHold, match="^DOORBELL_REQUEST_INVALID$"):
        replace(f.p.request, canonical_bytes=f.p.request.canonical_bytes + b"\n")


@pytest.mark.parametrize("boundary", ["readiness", "authenticate"])
def test_r3b2_complete_native_looking_claims_still_lack_private_provider(r3b2_claims,
                                                                     monkeypatch, boundary):
    f = r3b2_claims
    calls = []
    original = r.cdp_turn.pre_send

    def observe(*args, **kwargs):
        gate = original(*args, **kwargs)
        calls.append(gate)
        return gate

    monkeypatch.setattr(r.cdp_turn, "pre_send", observe)
    if boundary == "authenticate":
        with pytest.raises(r.DependencyHold, match="^DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE$"):
            r.NativeTurnAuthority().authenticate(f.p.request, f.p.turn,
                registration=f.p.registration, event=f.p.event, origin=f.p.origin)
        assert calls == []
        return

    with pytest.raises(r.DependencyHold, match="^NATIVE_PRIVATE_PROVIDER_UNAVAILABLE$"):
        r.NativeSender(f.p.root, f.p.registration).require_turn_reference(f.p.request,
            event=f.p.event, origin=f.p.origin, evidence=f.evidence, now=f.now)
    assert len(calls) == 1
    assert calls[0].code is r.cdp_turn.GateCode.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE
    assert calls[0].status is r.cdp_turn.GateStatus.UNAVAILABLE
    assert calls[0].send_allowed is False and calls[0].authenticated_user_turn is None


@pytest.mark.parametrize("fake", [None, True, SimpleNamespace(send_allowed=True,
    status="READY", authenticated_user_turn="forged", retry_allowed=True)])
def test_r3b2_provider_shaped_gate_result_cannot_enable_send(r3b2_claims, monkeypatch, fake):
    f = r3b2_claims
    monkeypatch.setattr(r.cdp_turn, "pre_send", lambda *a, **k: fake)
    with pytest.raises(r.DependencyHold, match="^DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE$"):
        r.NativeTurnAuthority().preflight(f.p.request, registration=f.p.registration,
            event=f.p.event, origin=f.p.origin, evidence=f.evidence, now=f.now)


def test_r3b2_pre_send_rejection_stays_pending_in_existing_runner(r3a_ingress, r3b1_pins,
                                                               monkeypatch):
    i = r3a_ingress
    runner = r.compose_native_source_only(i["config_path"], i["root"])
    event = r.Event(i["event"]["event_id"], i["event"]["source_sha"],
                    i["event"]["run_attempt"], digest(i["event"]), "GITHUB_AUTHENTICATED")
    origin = replace(r3b1_pins.origin, binding=i["registration"].binding,
                     marker=i["registration"].marker)
    calls = []

    def reject(sender, registration):
        # Fault injection reaches only a rejection gate, never native readiness.
        calls.append("rejection")
        r.NativeSender.require_turn_reference(sender, r.DoorbellRequest.for_event(event, registration),
            event=event, origin=origin, evidence=r.cdp_turn.TurnEvidence(), now=120)

    monkeypatch.setattr(r.NativeSender, "preflight", reject)
    assert runner.run_once() == r.Result("HOLD_DEPENDENCY", "DOORBELL_OBSERVATION_MISSING",
                                       "GITHUB_AUTHENTICATED")
    assert calls == ["rejection"]
    assert_read_only_pending(i)
    assert runner.sender.owner.adapter.allow_submit is False
    assert runner.orchestrator._completed == {} and runner.orchestrator._receipts == {}


@pytest.mark.parametrize("fault,code", [
    ("ambiguous", "DOORBELL_CLICK_AMBIGUOUS_NO_REPLAY"),
    ("timeout", "DOORBELL_TRANSPORT_TIMEOUT_NO_REPLAY"),
    ("missing_turn", "DOORBELL_USER_TURN_MISSING"),
    ("wrong_turn", "DOORBELL_USER_TURN_SCOPE_MISMATCH"),
    ("forged_turn", "DOORBELL_NATIVE_PRIVATE_PROVIDER_UNAVAILABLE"),
    ("digest", "DOORBELL_MESSAGE_DIGEST_MISMATCH"),
    ("not_attempted", "ATTEMPT_UNPROVEN_NO_REPLAY"),
    ("invalid_outcome", "DOORBELL_INVALID_SEND_OUTCOME_NO_REPLAY"),
])
def test_r3b2_source_post_send_classification_is_unknown_without_replay(composition, r3b2_claims,
                                                                    monkeypatch, fault, code):
    """TEST_ONLY_MOCK: persist source rejection via the existing SQLite runner."""
    make, event, registration, _, path, _ = composition
    f, c = r3b2_claims, r.cdp_turn
    evidence = replace(f.evidence, attestation=replace(f.evidence.attestation,
                                                      phase=c.AttestationPhase.POST_SEND))
    request, user_turn, outcome = f.request, f.user_turn, c.SendOutcome.CLICK_REPORTED
    if fault == "ambiguous":
        outcome = c.SendOutcome.CLICK_AMBIGUOUS
    elif fault == "timeout":
        outcome = c.SendOutcome.TRANSPORT_TIMEOUT
    elif fault == "missing_turn":
        user_turn = None
    elif fault == "wrong_turn":
        user_turn = replace(user_turn, scope=replace(request.scope,
            origin=replace(request.scope.origin, account_id="other-account")))
    elif fault == "digest":
        request = replace(request, scope=replace(request.scope, message_sha256="0" * 64))
    elif fault == "not_attempted":
        outcome = c.SendOutcome.NOT_ATTEMPTED
    elif fault == "invalid_outcome":
        outcome = "CLICK_AMBIGUOUS"
    runner = make()
    calls = []

    def rejected_effect(*args):
        assert record(path, event, registration)["status"] == "RESERVED"
        calls.append("source-rejection")
        mapped_code = r.NativeTurnAuthority().post_reservation_code(
            outcome=outcome, request=request, evidence=evidence, user_turn=user_turn, now=f.now)
        assert mapped_code == code
        raise r.RuntimeHold(mapped_code)

    monkeypatch.setattr(runner.sender, "send", rejected_effect)
    assert runner.run_once() == r.Result("UNKNOWN_EFFECT", code, "SYNTHETIC_FIXTURE")
    assert record(path, event, registration)["history"] == ["PENDING", "RESERVED", "UNKNOWN_EFFECT"]
    assert runner.run_once().replayed and calls == ["source-rejection"]
    reopened = make()
    assert reopened.run_once() == r.Result("UNKNOWN_EFFECT", code, "SYNTHETIC_FIXTURE", True)
    assert reopened.sender.calls == 0 and reopened.orchestrator._completed == {}


def test_r3b2_fixed_turn_authority_rejects_subclasses_and_unavailable_post_gate(r3b2_claims,
                                                                          monkeypatch):
    f = r3b2_claims

    class Unregistered(r.NativeTurnAuthority):
        def preflight(self, *args, **kwargs):
            pytest.fail("Unregistered owner invoked")

    sender = r.NativeSender(f.p.root, f.p.registration)
    sender.turn_authority = Unregistered()
    with pytest.raises(r.DependencyHold, match="^DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE$"):
        sender.require_turn_reference(f.p.request, event=f.p.event, origin=f.p.origin,
                                      evidence=f.evidence, now=f.now)
    assert r.NativeTurnAuthority.post_reservation_code(sender.turn_authority,
        outcome=r.cdp_turn.SendOutcome.CLICK_AMBIGUOUS) == "ATTEMPT_UNPROVEN_NO_REPLAY"
    monkeypatch.setattr(r.cdp_turn, "post_send", lambda *a, **k: SimpleNamespace(
        status="HOLD", send_allowed=True, retry_allowed=True))
    assert r.NativeTurnAuthority().post_reservation_code(
        outcome=r.cdp_turn.SendOutcome.CLICK_AMBIGUOUS) == "ATTEMPT_UNPROVEN_NO_REPLAY"


class FixtureStop:
    def __init__(self, clock=None):
        self.clock = clock
        self.stopped = False
        self.delays = []

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.delays.append(timeout)
        if self.clock is not None:
            self.clock.now += timeout
            if self.clock.on_wait is not None:
                self.clock.on_wait(self)
        return self.stopped


@pytest.mark.parametrize("desktop_state", ["unlocked", "wts_locked", "secure_input_desktop"])
def test_protected_desktop_logon_attests_windows_wts_under_actions(monkeypatch, desktop_state):
    import ctypes
    from ctypes import wintypes as w
    from unittest.mock import Mock

    from ai_commerce.wakehub import native_authority as auth

    class SessionRow(ctypes.Structure):
        _fields_ = [("session_id", w.DWORD), ("station", w.LPWSTR), ("state", ctypes.c_int)]

    class SessionData(ctypes.Structure):
        _fields_ = [
            ("session_id", w.DWORD), ("state", ctypes.c_int), ("flags", w.LONG),
            ("station", w.WCHAR * 33), ("username", w.WCHAR * 21), ("domain", w.WCHAR * 18),
            ("times", ctypes.c_longlong * 5), ("counters", w.DWORD * 6),
        ]

    class SessionInfoEx(ctypes.Structure):
        _fields_ = [("level", w.DWORD), ("data", SessionData)]

    session, sid = 7, "S-1-5-21-101-202-303-1001"
    rows = (SessionRow * 1)(SessionRow(session, "Console", 0))
    info = SessionInfoEx(level=1)
    info.data.session_id = session
    info.data.flags = 0 if desktop_state == "wts_locked" else 1
    info.data.username, info.data.domain = "FixtureOwner", "FixtureHost"
    info.data.times[0], info.data.times[4] = 1234, 2000
    active = w.DWORD(0)

    def enumerate_sessions(server, reserved, version, out, count):
        assert (server, reserved, version) == (None, 0, 1)
        ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(rows)
        ctypes.cast(count, ctypes.POINTER(w.DWORD))[0] = len(rows)
        return True

    def query_session(server, session_id, info_class, out, size):
        assert (server, session_id) == (None, session)
        value = {25: info, 8: active}[info_class]
        ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(value)
        ctypes.cast(size, ctypes.POINTER(w.DWORD))[0] = ctypes.sizeof(value)
        return True

    def user_name(format_id, output, size):
        assert format_id == 2
        output.value = r"FixtureHost\FixtureOwner"
        return True

    def object_name(handle, index, output, size, required):
        assert index == 2
        input_desktop = "Winlogon" if desktop_state == "secure_input_desktop" else "Default"
        output.value = {1: "WinSta0", 2: "Default", 3: input_desktop}[handle]
        return True

    wts = SimpleNamespace(WTSEnumerateSessionsW=enumerate_sessions, WTSQuerySessionInformationW=query_session,
                          WTSFreeMemory=Mock())
    user = SimpleNamespace(GetProcessWindowStation=Mock(return_value=1), GetThreadDesktop=Mock(return_value=2),
                           OpenInputDesktop=Mock(return_value=3), CloseDesktop=Mock(),
                           GetUserObjectInformationW=object_name)
    libraries = {
        "wtsapi32": wts, "user32": user, "kernel32": SimpleNamespace(GetCurrentThreadId=Mock(return_value=4)),
        "advapi32": SimpleNamespace(GetUserNameW=Mock()), "secur32": SimpleNamespace(GetUserNameExW=user_name),
    }
    load = Mock(side_effect=lambda name, *, use_last_error: libraries[name])
    actions = SimpleNamespace(name="nt", environ={"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "123"})
    monkeypatch.setattr(r, "os", actions)
    monkeypatch.setattr(auth, "os", actions)
    monkeypatch.setattr(auth, "_windows_process_sid", lambda: sid)
    monkeypatch.setattr(auth, "_interactive_session", lambda: session)
    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)

    with pytest.raises(r.RuntimeHold, match="^LOCAL_OPERATOR_ONLY$"):
        r._supervised_desktop(session, sid)
    load.assert_not_called()
    if desktop_state == "unlocked":
        assert r._desktop_logon(session, sid) == digest([session, 1234])
    else:
        with pytest.raises(r.RuntimeHold, match="^WINDOWS_INTERACTIVE_REQUIRED$"):
            r._desktop_logon(session, sid)

    if desktop_state == "wts_locked":
        user.OpenInputDesktop.assert_not_called()
        assert wts.WTSFreeMemory.call_count == 2
    else:
        user.OpenInputDesktop.assert_called_once_with(0, False, 1)
        user.CloseDesktop.assert_called_once_with(3)
        assert wts.WTSFreeMemory.call_count == 3


@pytest.mark.parametrize("key", ["TF_BUILD", "BUILD_BUILDID", "AI_COMMERCE_GIT_GUARD_TEST"])
def test_local_supervisor_rejects_automation_before_desktop_logon(monkeypatch, key):
    monkeypatch.setattr(r, "os", SimpleNamespace(name="nt", environ={key: "true"}))
    monkeypatch.setattr(r, "_desktop_logon", lambda *args: pytest.fail("unexpected desktop logon"))
    with pytest.raises(r.RuntimeHold, match="^LOCAL_OPERATOR_ONLY$"):
        r._supervised_desktop(7, "unused")


def test_protected_desktop_logon_requires_windows(monkeypatch):
    monkeypatch.setattr(r, "os", SimpleNamespace(name="posix"))
    with pytest.raises(r.RuntimeHold, match="^WINDOWS_INTERACTIVE_REQUIRED$"):
        r._desktop_logon(7, "unused")


@pytest.fixture
def supervisor(native_ingress, monkeypatch):
    """Run the real demand-task APIs with a synthetic Windows transport."""
    i = native_ingress
    acq = r.chrome_acquisition
    state = SimpleNamespace(
        active=False, slots=0, pending=False, task_pending=False, session=7, logon=1,
        task="DEMAND_READY", journal="FRESH", read_code="ALREADY_ENROLLED", d5="ABSENT", launch_busy=False,
        start=None, on_read=None,
    )
    stops, ticks = [], []
    clock = SimpleNamespace(now=0.0, on_wait=None)
    requests = []
    listening = i["inventory"]["listeners"][:]
    running = i["inventory"]["processes"][:]
    local = r.Path(os.environ["LOCALAPPDATA"])
    shell = local / "synthetic-pwsh.exe"
    filetime = acq._creation_ticks("2026-11-18T00:00:00Z")

    def owner():
        return local, state.session, i["process"]["OwnerSid"]

    def set_owned(owned):
        i["inventory"].update(listeners=listening[:] if owned else [], processes=running[:] if owned else [])

    def new_logon(session):
        state.session = session
        state.logon += 1
        i["process"]["SessionId"] = session

    def set_policy(policy="demand-start"):
        i["config"]["chrome_lifecycle"] = policy
        i["save"](i["config_path"], i["config"])

    def start_owned():
        # Reuse the PID with a different creation timestamp each time.
        i["process"]["CreationDate"] = f"2026-11-19T00:00:{len(requests):02d}.0000000Z"
        set_owned(True)
        return "START_REQUESTED"

    def task_transport(args, *, env, input=None, **kwargs):
        assert args[0] == str(shell)
        assert env["AI_COMMERCE_TASK_SID"] == owner()[2]
        assert "AI_COMMERCE_GITHUB_READ_TOKEN" not in env and "AI_COMMERCE_WAKE_HMAC_KEY_HEX" not in env
        if input == acq._CLEAN_9444_TASK_RECONCILE_SCRIPT.encode("utf-8"):
            assert "AI_COMMERCE_TASK_INSTALL" not in env
            if state.on_read is not None:
                state.on_read()
            code = json.dumps({"task": state.task, "journal": state.journal, "d5": state.d5})
        elif args[-1] == acq._CLEAN_9444_TASK_READ_SCRIPT:
            assert env["AI_COMMERCE_TASK_INSTALL"] == "0" and env["AI_COMMERCE_TASK_START_SESSION"] == ""
            code = state.read_code
        else:
            assert args[-1] == acq._CLEAN_9444_TASK_SCRIPT
            assert env["AI_COMMERCE_TASK_INSTALL"] == "0"
            assert env["AI_COMMERCE_TASK_START_SESSION"] == str(owner()[1])
            requests.append((owner(), env["AI_COMMERCE_TASK_ARGUMENTS"]))
            assert state.start is not None, "Unexpected demand start"
            code = state.start()
        return SimpleNamespace(returncode=0, stdout=code.encode("ascii"))

    @contextmanager
    def slot():
        if state.active:
            raise r.RuntimeHold("SUPERVISOR_ACTIVE")
        state.active = True
        state.slots += 1
        try:
            yield
        finally:
            state.active = False

    def make_stop():
        stop = FixtureStop(clock)
        stops.append(stop)
        return stop

    @contextmanager
    def launch_slot():
        if state.launch_busy:
            raise acq._Clean9444Hold("SUPERVISOR_ACTIVE")
        yield

    canonical = r.Runner._run_supervised_event

    def run_supervised_event(runner, chrome):
        result = canonical(runner, chrome)
        ticks.append((result, runner))
        return result

    def run(max_ticks=1, **kwargs):
        return r.run_supervisor(i["config_path"], i["root"], max_ticks=max_ticks,
                                idle_seconds=2, max_backoff_seconds=5, **kwargs)

    def operator(*, attended=True):
        assert not attended
        return owner()

    monkeypatch.setattr(acq, "_enrollment_operator", operator)
    monkeypatch.setattr(r, "_supervised_desktop", lambda session, sid: digest([session, sid, state.logon]))
    monkeypatch.setattr(acq, "_native_known_folder", lambda _: local / "absent-program-files")
    monkeypatch.setattr(acq, "_verify_google_chrome", lambda _: None)
    monkeypatch.setattr(acq, "_historical_legacy_workdir", lambda: None)
    monkeypatch.setattr(acq, "_powershell", lambda: shell)
    monkeypatch.setattr(acq, "_trusted_powershells", lambda: (shell,))
    monkeypatch.setattr(acq, "_clean_supervisor_slot", launch_slot)
    monkeypatch.setattr(acq, "_clean_pending", lambda name: state.task_pending)
    monkeypatch.setattr(acq, "_clean_launch_pending", lambda: state.pending)
    monkeypatch.setattr(acq.subprocess, "run", task_transport)
    monkeypatch.setattr(r, "time_ns", lambda: (filetime - 116444736000000000) * 100)
    monkeypatch.setattr(r, "monotonic", lambda: clock.now)
    monkeypatch.setattr(r, "_supervisor_slot", slot)
    monkeypatch.setattr(r, "StopEvent", make_stop)
    monkeypatch.setattr(r.Runner, "_run_supervised_event", run_supervised_event)
    return SimpleNamespace(i=i, run=run, set_owned=set_owned, new_logon=new_logon, set_policy=set_policy,
                           start_owned=start_owned, requests=requests, owner=owner, ticks=ticks, stops=stops,
                           slot=state, clock=clock, path=r.Path(i["config"]["journal"]))


def chrome_record(supervisor):
    with sqlite3.connect(supervisor.path) as db:
        return json.loads(db.execute("SELECT record FROM chrome_lifecycle").fetchone()[0])


@pytest.fixture
def observe_only(supervisor, monkeypatch):
    s = supervisor
    monkeypatch.setattr(r, "_private_identity", lambda path: s.owner()[2])

    def forbidden(*args, **kwargs):
        pytest.fail("observe-only reached a journal, source or browser effect")

    monkeypatch.setattr(r.Journal, "locked", forbidden)
    monkeypatch.setattr(r, "_compose_target", forbidden)
    monkeypatch.setattr(r._SupervisorChrome, "run_once", forbidden)
    monkeypatch.setattr(r.LocalChromeDoorbellAdapter, "send_wake", forbidden)
    monkeypatch.setattr(r.chrome_acquisition, "_clean_task", forbidden)
    return s


@pytest.mark.parametrize("owned", [False, True])
def test_observe_only_cli_reads_actual_health_without_journal_or_source(observe_only, capsys, owned):
    s = observe_only
    s.set_owned(owned)
    assert main(["supervise", "--observe-only", "--config", str(s.i["config_path"]),
                 "--repository-root", str(s.i["root"]), "--max-ticks", "1"]) == 2
    health = "CHROME_OWNED" if owned else "CHROME_ABSENT"
    captured = capsys.readouterr()
    assert json.loads(captured.out)["chrome_health"] == health
    assert json.loads(captured.err) == {"chrome_health": health}
    assert s.i["browser"].reads == 0 and s.requests == [] and s.ticks == []
    assert not s.path.exists()


@pytest.mark.parametrize("change", ["demand-start", "preload", "delivery", "missing", "observe"])
def test_observe_only_holds_on_config_drift_without_effects(observe_only, change):
    s = observe_only
    health = []

    def drift(code):
        health.append(code)
        config = s.i["config"]
        if change == "missing":
            s.i["config_path"].unlink()
            return
        if change == "preload":
            config["chat_preload"] = True
        elif change == "delivery":
            config["chat_preload"] = False
            config["ui_delivery"] = {"schema": "WakeHubLocalUIDelivery.v2",
                                     "route_marker": s.i["registration"].marker,
                                     "registration_id": "a" * 32, "marker_digest": "b" * 64,
                                     "grants": [{"source_key": "c" * 64, "expires_ms": 2200000000000}]}
        elif change == "observe":
            config["caller_id"] = "different-caller"
        else:
            config["chrome_lifecycle"] = change
        s.i["save"](s.i["config_path"], config)

    result = s.run(max_ticks=2, observe_only=True, on_health=drift)
    expected = {"missing": "LOCAL_AUTHORITY_MISSING", "observe": "LOCAL_CONFIG_CHANGED"}
    assert result.code == expected.get(change, "LOCAL_CONFIG_OBSERVE_ONLY_REQUIRED")
    assert health == ["CHROME_OWNED"]
    assert s.requests == [] and s.ticks == [] and not s.path.exists()


def test_observe_only_emits_private_source_bound_health_under_slot(observe_only, monkeypatch):
    s = observe_only
    config = s.i["config_path"]
    root = s.i["root"]
    _, config_pin = r._observe_only_config(config, root, s.owner(), lambda: False)
    desktop = r._supervised_desktop(s.owner()[1], s.owner()[2])
    deployment = ("a" * 40, "b" * 64, config_pin, desktop, "c" * 32)
    writes = []
    state = {"Health": None}

    def store(name, value):
        assert s.slot.active
        assert name == "Health"
        state[name] = value
        writes.append(json.loads(value) if value is not None else None)

    monkeypatch.setattr(r, "_store_supervisor_deployment_value", store)
    monkeypatch.setattr(r, "_supervisor_deployment_value", state.get)
    monkeypatch.setattr(r, "_supervisor_process", lambda pid, exe, owner: 123456)
    monkeypatch.setattr(r, "monotonic_ns", lambda: 123_000_000)
    result = s.run(observe_only=True, _deployment=deployment)
    assert result.chrome_health == "CHROME_OWNED"
    assert writes == [{"source_sha": deployment[0], "source_digest": deployment[1],
                       "config_digest": config_pin, "desktop": desktop, "nonce": deployment[4],
                       "pid": os.getpid(), "created": 123456, "observed_ns": 123_000_000,
                       "health": "CHROME_OWNED"}, None]
    writes.clear()
    s.slot.pending = True
    assert s.run(observe_only=True, _deployment=deployment).code == "CHROME_LAUNCH_RECONCILE_REQUIRED"
    assert writes == [] and s.slot.pending and not s.path.exists()


def test_flat02_supervisor_acquires_once_then_reuses_owned_9444(supervisor, capsys):
    s = supervisor
    s.set_owned(False)
    s.set_policy()

    def acquire():
        assert chrome_record(s)["launch"] == "UNKNOWN"
        assert s.i["calls"] == []
        return s.start_owned()

    s.slot.start = acquire
    assert main(["supervise", "--config", str(s.i["config_path"]), "--repository-root", str(s.i["root"]),
                 "--max-ticks", "2"]) == 2
    result = s.ticks[-1][0]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == result.to_dict()
    assert json.loads(captured.err) == {"chrome_health": "CHROME_OWNED"}
    assert len(s.requests) == 1 and s.i["browser"].reads == 2
    assert [tick for tick, _ in s.ticks] == [result, result]
    assert result.status == "HOLD" and result.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    assert all(runner.orchestrator._completed == {} for _, runner in s.ticks)
    assert chrome_record(s)["launch"] is None and chrome_record(s)["process"]
    assert_read_only_pending(s.i, version=3)


@pytest.mark.parametrize("event_state", ["PENDING", "UNKNOWN_EFFECT", "UI_TURN_OBSERVED", "NO_EVENT"])
def test_flat02_new_browser_generation_keeps_existing_event(supervisor, monkeypatch, event_state):
    s = supervisor
    s.set_owned(False)
    s.set_policy()
    s.slot.start = s.start_owned

    if event_state == "NO_EVENT":
        monkeypatch.setattr(r.GitHubEventSource, "next_event", lambda _: None)
    assert s.run().chrome_health == "CHROME_OWNED"
    first = chrome_record(s)
    assert first["launch"] is None and s.requests[0][0][1] == 7
    with sqlite3.connect(s.path) as db:
        if event_state in ("UNKNOWN_EFFECT", "UI_TURN_OBSERVED"):
            saved = json.loads(db.execute("SELECT record FROM events").fetchone()[0])
            saved.update(status=event_state, history=["PENDING", "RESERVED", event_state])
            if event_state == "UNKNOWN_EFFECT":
                saved["code"] = "ATTEMPT_UNPROVEN_NO_REPLAY"
            else:
                saved["code"] = "UI_USER_TURN_OBSERVED"
                saved["evidence"] = {"marker": s.i["registration"].marker, "user_turn_ref": "prior-fixture-turn"}
            db.execute("UPDATE events SET record=?", (json.dumps(saved),))
        before = db.execute("SELECT * FROM events").fetchall()
    s.set_owned(False)
    s.new_logon(8)
    health = []
    result = s.run(max_ticks=2, on_health=health.append)
    assert result.chrome_health == "CHROME_OWNED" and health == ["CHROME_OWNED"]
    if event_state == "UNKNOWN_EFFECT":
        assert result.status == event_state and result.replayed
        assert result.code == "ATTEMPT_UNPROVEN_NO_REPLAY"
    elif event_state == "UI_TURN_OBSERVED":
        assert result.status == event_state and result.replayed
        assert result.code == "UI_USER_TURN_OBSERVED"
    elif event_state == "NO_EVENT":
        assert result.code == "NO_EVENT"
    else:
        assert result.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    assert [owner[1] for owner, _ in s.requests] == [7, 8]
    assert s.requests[0][1] == s.requests[1][1]
    current = chrome_record(s)
    assert current["scope"] == first["scope"] and current["process"] != first["process"]
    assert current["launch"] is None
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT * FROM events").fetchall() == before


def test_flat02_supervisor_backs_off_and_reauthenticates_same_pending_event(supervisor, monkeypatch):
    s = supervisor
    s.set_owned(False)
    s.set_policy()
    acq = r.chrome_acquisition
    original_read = r._read_owned_json
    original_next = r.GitHubEventSource.next_event
    original_probe = acq._probe_cdp
    budgets = []

    def starting_once(*args, **kwargs):
        monkeypatch.setattr(r, "_read_owned_json", original_read)
        raise r.RuntimeHold("LOCAL_AUTHORITY_MISSING")

    def next_event(source):
        budgets.append(source.reader.read_budget)
        if len(budgets) == 1:
            raise r.GitHubReadBlocked("GITHUB_READ_UNAVAILABLE")
        return original_next(source)

    def probe_once(environment):
        monkeypatch.setattr(acq, "_probe_cdp", original_probe)
        raise acq.AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")

    def acquire():
        if len(s.requests) == 1:
            return "START_NOT_ATTEMPTED"
        return s.start_owned()

    monkeypatch.setattr(r, "_read_owned_json", starting_once)
    monkeypatch.setattr(r.GitHubEventSource, "next_event", next_event)
    s.slot.start = acquire
    monkeypatch.setattr(acq, "_probe_cdp", probe_once)
    result = s.run(max_ticks=5)
    assert result.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    assert [tick.code for tick, _ in s.ticks] == ["GITHUB_READ_UNAVAILABLE", "CDP_PROTOCOL_UNAVAILABLE",
                                                result.code, result.code]
    assert s.ticks[0][0].chrome_health == "START_NOT_ATTEMPTED"
    assert len(s.requests) == 2 and s.stops[0].delays == [2, 2, 2, 2]
    assert len({id(budget) for budget in budgets}) == 4 and len(s.i["calls"]) == 18
    assert s.i["browser"].reads == 2
    assert_read_only_pending(s.i, version=3)


@pytest.mark.parametrize("new_session", [7, 8])
def test_flat02_request_stays_unresolved_after_logon_change(supervisor, new_session):
    s = supervisor
    s.set_owned(False)
    s.set_policy()

    def acquire():
        s.new_logon(new_session)
        return s.start_owned()

    s.slot.start = acquire
    result = s.run()
    assert result.chrome_health == "CHROME_OWNED_LAUNCH_UNRESOLVED"
    assert result.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    request = chrome_record(s)["request"]
    s.set_owned(False)
    assert s.run().code == r._LAUNCH_UNKNOWN
    assert len(s.requests) == 1 and s.i["browser"].reads == 1
    assert chrome_record(s)["launch"] == "REQUESTED" and chrome_record(s)["request"] == request
    assert_read_only_pending(s.i, version=3)


@pytest.mark.parametrize("fault,code", [
    ("missing_authority", "INTERACTIVE_9444_START_GRANT_REQUIRED"),
    ("paused", "PAUSED_BY_HUMAN"),
    ("target", "CHROME_ACQUISITION_TARGET_MISMATCH"),
    ("task_missing", "CHROME_DEMAND_TASK_UNPROVEN"),
    ("task_mismatch", "CHROME_DEMAND_TASK_UNPROVEN"),
    ("task_busy", "CHROME_DEMAND_TASK_BUSY"),
    ("task_launch_mutex", "SUPERVISOR_ACTIVE"),
    ("task_disabled", "CHROME_DEMAND_TASK_DISABLED"),
    ("task_pending", "CHROME_DEMAND_TASK_UNRESOLVED"),
    ("task_migration", "CHROME_DEMAND_TASK_UNPROVEN"),
    ("task_duplicate", "CHROME_DEMAND_TASK_UNPROVEN"),
    ("locked", "WINDOWS_INTERACTIVE_REQUIRED"),
    ("session_conflict", "CHROME_INTERACTIVE_SESSION_CONFLICT"),
    ("profile_lock", "PROFILE_LOCK_HOLD"),
    ("ownership", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("foreign_port", "WINDOWS_PROBE_INVALID"),
    ("foreign_profile", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("no_listener", "PROFILE_ALREADY_ACTIVE_NO_CDP"),
    ("context", "EXACT_CHAT_CONTEXT_AMBIGUOUS"),
    ("account", "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"),
])
def test_flat02_supervisor_holds_without_acquisition(supervisor, monkeypatch, fault, code):
    s = supervisor
    acq = r.chrome_acquisition
    if fault in ("missing_authority", "paused", "target") or fault.startswith("task_"):
        s.set_owned(False)
    if fault != "missing_authority":
        s.set_policy("paused" if fault == "paused" else "demand-start")
    if fault == "target":
        monkeypatch.setattr(acq, "_clean_target", lambda *args: (
            replace(s.i["environment"], user_data_dir=s.i["environment"].user_data_dir / "Other"), ()))
    if fault == "task_mismatch":
        s.slot.read_code = "AUTOSTART_CONFLICT"
    if fault == "task_missing":
        s.slot.task = "ABSENT"
    if fault == "task_busy":
        s.slot.task = "DEMAND_NOT_READY"
    if fault == "task_launch_mutex":
        s.slot.launch_busy = True
    if fault == "task_disabled":
        s.slot.task = "DEMAND_DISABLED"
    if fault == "task_pending":
        s.slot.task_pending = True
    if fault == "task_migration":
        s.slot.journal = "ROLLBACK_ONLY"
    if fault == "task_duplicate":
        s.slot.task = "MULTIPLE_CANDIDATES"
    if fault == "locked":
        def locked(**kwargs):
            raise acq._EnrollmentHold("WINDOWS_INTERACTIVE_REQUIRED")
        monkeypatch.setattr(acq, "_enrollment_operator", locked)
    if fault == "session_conflict":
        def conflict(*args):
            raise r.RuntimeHold("CHROME_INTERACTIVE_SESSION_CONFLICT")
        monkeypatch.setattr(r, "_supervised_desktop", conflict)
    if fault == "profile_lock":
        s.set_owned(False)
        env = s.i["environment"]
        (env.user_data_dir / env.profile_directory / acq._MARKERS[0]).touch()
    if fault == "ownership":
        s.i["process"]["SessionId"] = 8
    if fault == "foreign_port":
        s.i["inventory"]["listeners"][0]["LocalPort"] = 9333
    if fault == "foreign_profile":
        command = s.i["environment"].command()
        command[2] = "--profile-directory=Other"
        s.i["process"]["CommandLine"] = json.dumps(command)
    if fault == "no_listener":
        s.i["inventory"]["listeners"].clear()
    if fault == "context":
        s.i["browser"].items *= 2
    if fault == "account":
        s.i["page"].account = "different-fixture-account"
    result = s.run()
    assert result.code == code
    assert result.status == ("HOLD_DEPENDENCY" if fault == "missing_authority" else "HOLD")
    assert s.requests == []
    assert_read_only_pending(s.i, version=3)


def test_flat02_caller_boolean_cannot_supply_consent(supervisor):
    s = supervisor
    s.set_owned(False)
    s.set_policy(True)
    assert s.run().code == "LOCAL_CONFIG_INVALID"
    assert s.requests == [] and not s.path.exists()


@pytest.mark.parametrize("receipt", [None, "UNKNOWN_EFFECT", "WINDOWS_PROBE_UNAVAILABLE", "STARTED"])
def test_flat02_ambiguous_launch_is_durable_and_never_repeated(supervisor, receipt):
    s = supervisor
    s.set_owned(False)
    s.set_policy()

    def acquire():
        if receipt is None:
            raise subprocess.TimeoutExpired("synthetic-demand-task", 15)
        return receipt

    s.slot.start = acquire
    result = s.run()
    assert result.status == "UNKNOWN_EFFECT" and result.code == r._LAUNCH_UNKNOWN
    assert_read_only_pending(s.i, version=3)
    original = chrome_record(s)
    s.new_logon(8)
    assert s.run().code == r._LAUNCH_UNKNOWN
    s.set_owned(True)
    reopened = s.run(max_ticks=2)
    assert reopened.chrome_health == "CHROME_OWNED_LAUNCH_UNRESOLVED"
    assert reopened.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    current = chrome_record(s)
    assert current["launch"] == "UNKNOWN" and current["request"] == original["request"]
    s.set_owned(False)
    assert s.run().code == r._LAUNCH_UNKNOWN
    assert len(s.requests) == 1 and s.i["browser"].reads == 2
    assert_read_only_pending(s.i, version=3)


@pytest.mark.parametrize("pending", [False, True])
def test_flat02_start_report_requires_independent_completion(supervisor, pending):
    s = supervisor
    s.set_owned(False)
    s.set_policy()

    def acquire():
        s.slot.pending = pending
        s.slot.task = "DEMAND_NOT_READY"
        return "START_REQUESTED"

    s.slot.start = acquire
    result = s.run(max_ticks=3)
    assert result.code == (r._LAUNCH_UNKNOWN if pending else "CHROME_ACQUISITION_NOT_READY")
    assert chrome_record(s)["launch"] == "REQUESTED"
    assert len(s.requests) == 1 and s.stops[0].delays == [2, 2] and s.i["browser"].reads == 0

    s.start_owned()
    # A valid current browser can be used while the task is still in flight.
    assert s.run().chrome_health == "CHROME_OWNED_LAUNCH_UNRESOLVED"
    s.slot.pending = False
    s.slot.task = "DEMAND_READY"
    assert s.run().chrome_health == "CHROME_OWNED"
    assert chrome_record(s)["launch"] is None and len(s.requests) == 1


def test_flat02_reused_pid_requires_a_new_creation_time_in_the_same_logon(supervisor):
    s = supervisor
    s.set_policy()
    s.set_owned(False)
    s.slot.start = lambda: "START_REQUESTED"
    assert s.run().chrome_health == "CHROME_ACQUISITION_NOT_READY"

    s.set_owned(True)
    assert s.run().chrome_health == "CHROME_OWNED_LAUNCH_UNRESOLVED"
    first_process = chrome_record(s)["process"]
    s.start_owned()
    assert s.run().chrome_health == "CHROME_OWNED"
    assert chrome_record(s)["process"] != first_process
    assert chrome_record(s)["launch"] is None and len(s.requests) == 1


def test_flat02_restart_during_launch_observes_native_pending_without_replaying(supervisor):
    s = supervisor
    s.set_owned(False)
    s.set_policy()

    def interrupt():
        s.slot.pending = True
        s.start_owned()
        raise Crash()

    s.slot.start = interrupt
    with pytest.raises(Crash):
        s.run()
    assert chrome_record(s)["launch"] == "UNKNOWN" and not s.slot.active
    s.set_owned(False)
    s.new_logon(8)

    def forbidden(*args):
        pytest.fail("An interrupted acquisition must not request a new grant")

    s.slot.start = forbidden
    assert s.run().code == r._LAUNCH_UNKNOWN
    s.set_owned(True)
    assert s.run().chrome_health == "CHROME_OWNED_LAUNCH_UNRESOLVED"
    assert s.i["browser"].reads == 1 and chrome_record(s)["launch"] == "UNKNOWN"
    assert len(s.requests) == 1


@pytest.mark.parametrize("legacy_code", [r._LAUNCH_UNKNOWN, r._ACQUISITION_REPORTED])
def test_flat02_migrates_legacy_launch_record_without_blocking_other_event_readiness(
        supervisor, monkeypatch, legacy_code):
    s = supervisor
    runner = r.compose_read_only(s.i["config_path"], s.i["root"])

    def read_only_tick():
        with monkeypatch.context() as patch:
            patch.setattr(r.chrome_acquisition, "_enrollment_operator", s.owner)
            return runner.run_once()

    assert read_only_tick().code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    registration = s.i["registration"]
    event = r.Event(s.i["event"]["event_id"], "a" * 40, 1, digest(s.i["event"]), "GITHUB_AUTHENTICATED")
    historic = replace(event, event_id="ef02-completion-v1:" + "d" * 64)
    with runner.journal.locked(registration) as (txn, data):
        original = {"event": asdict(historic), "status": "PENDING", "code": legacy_code,
                    "history": ["PENDING"], "evidence": {}}
        data["events"][historic.key(registration)] = original
        txn.write(data)
    with sqlite3.connect(s.path) as db:
        before = db.execute("SELECT * FROM events ORDER BY event_key").fetchall()
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
    s.set_owned(False)
    assert s.run().code == r._LAUNCH_UNKNOWN
    s.set_owned(True)
    result = s.run()
    assert result.chrome_health == "CHROME_OWNED_LAUNCH_UNRESOLVED"
    assert result.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    assert read_only_tick().code == result.code
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT * FROM events ORDER BY event_key").fetchall() == before
        assert db.execute("PRAGMA user_version").fetchone() == (3,)


@pytest.mark.parametrize("launch", [None, "REPORTED", "UNKNOWN"])
def test_flat02_migrates_v3_scope_only_with_an_exact_observed_preimage(supervisor, launch):
    s = supervisor
    assert s.run().chrome_health == "CHROME_OWNED"
    current = chrome_record(s)
    local, session, sid = s.owner()
    env = s.i["environment"]
    process = s.i["process"]
    legacy = {"scope": digest([str(local), session, sid, str(env.config_path), env.command()]),
              "process": digest([process["ProcessId"], r.chrome_acquisition._creation_ticks(process["CreationDate"])]),
              "launch": launch}
    with sqlite3.connect(s.path) as db:
        db.execute("UPDATE chrome_lifecycle SET record=?", (json.dumps(legacy),))
        events = db.execute("SELECT * FROM events").fetchall()
    s.new_logon(8)
    assert s.run().code == "CHROME_LEGACY_SCOPE_UNPROVEN"
    assert chrome_record(s) == legacy
    s.new_logon(session)
    assert s.run().chrome_health == ("CHROME_OWNED" if launch is None else "CHROME_OWNED_LAUNCH_UNRESOLVED")
    migrated = chrome_record(s)
    assert migrated["scope"] == current["scope"] and migrated["launch"] == launch
    s.new_logon(9)
    assert s.run().chrome_health == ("CHROME_OWNED" if launch is None else "CHROME_OWNED_LAUNCH_UNRESOLVED")
    assert s.requests == []
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT * FROM events").fetchall() == events


@pytest.mark.parametrize("drift", ["sid", "profile"])
def test_flat02_rejects_stable_owner_drift_even_when_absent(supervisor, monkeypatch, drift):
    s = supervisor
    assert s.run().chrome_health == "CHROME_OWNED"
    current = chrome_record(s)
    s.set_owned(False)
    s.new_logon(8)
    s.set_policy()
    if drift == "sid":
        s.i["process"]["OwnerSid"] = "S-1-5-21-444-555-666-1001"
    else:
        env = s.i["environment"]
        (env.user_data_dir / "Other").mkdir()
        s.i["chrome"]["profile_directory"] = "Other"
        s.i["save"](env.config_path, s.i["chrome"])
        acq = r.chrome_acquisition
        monkeypatch.setattr(acq, "_read_9444_config",
                            lambda config, local, root, sid: acq.load_environment(config, root, local_app_data=local))
    assert s.run().code == "CHROME_OWNER_SCOPE_CHANGED"
    assert chrome_record(s) == current and s.requests == []


def test_flat02_session_drift_between_observations_holds(supervisor, monkeypatch):
    s = supervisor
    s.set_owned(False)
    s.set_policy()
    acq = r.chrome_acquisition
    query = acq._query_windows

    def switch_after_query(port):
        result = query(port)
        s.new_logon(8)
        return result

    monkeypatch.setattr(acq, "_query_windows", switch_after_query)
    assert s.run().code == "CHROME_OBSERVATION_CHANGED"
    assert s.requests == [] and chrome_record(s)["scope"] is None


def test_flat02_busy_task_and_private_pause_are_rechecked_before_effect(supervisor):
    s = supervisor
    s.set_owned(False)
    s.set_policy()
    reads = []

    def change_task():
        reads.append(None)
        if len(reads) == 2:
            assert chrome_record(s)["launch"] == "UNKNOWN"
            s.slot.task = "DEMAND_NOT_READY"

    s.slot.on_read = change_task
    assert s.run().code == "CHROME_DEMAND_TASK_BUSY"
    assert s.requests == [] and chrome_record(s)["launch"] is None

    s.slot.task = "DEMAND_READY"
    s.slot.on_read = lambda: s.set_policy("paused")
    assert s.run().chrome_health == "LOCAL_AUTHORITY_DRIFT"
    assert s.requests == [] and chrome_record(s)["launch"] is None
    assert s.run().code == "PAUSED_BY_HUMAN"

    s.slot.on_read = None
    s.set_policy()
    s.slot.start = s.start_owned
    assert s.run().chrome_health == "CHROME_OWNED"
    assert len(s.requests) == 1


def test_flat02_supervisor_stops_before_acquisition_releases_slot_and_rejects_overlap(supervisor):
    s = supervisor
    s.set_owned(False)
    s.set_policy()

    def stop_during_authority():
        s.slot.on_read = None
        reads = list(s.i["calls"])
        assert s.run().code == "SUPERVISOR_ACTIVE"
        assert s.i["calls"] == reads
        s.stops[0].stopped = True

    s.slot.on_read = stop_during_authority
    assert s.run(max_ticks=None).code == "SUPERVISOR_STOPPED"
    assert s.ticks == [] and s.stops[0].delays == [] and not s.slot.active and s.requests == []
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)
    assert s.run(dry_run=True).code == "DRY_RUN_NO_EFFECTS" and s.slot.slots == 1
    with pytest.raises(SystemExit) as error:
        main(["supervise", "--native-source-only"])
    assert error.value.code == 2


def test_flat02_supervised_ticks_reconcile_ambiguous_fixture_send_without_repeating_it(
        composition, monkeypatch):
    make, event, registration, _, path, _ = composition
    attempts, lookups = [], []

    def send(*args):
        attempts.append(args)
        raise TimeoutError("fixture Send timeout")

    def tick():
        runner = make(brain=None)
        monkeypatch.setattr(runner.sender, "send", send)
        monkeypatch.setattr(runner.sender, "reconcile", lambda *args: lookups.append(args))
        return runner.run_once()

    @contextmanager
    def slot():
        yield

    monkeypatch.setattr(r, "_supervisor_slot", slot)
    stop = FixtureStop()
    result = r.supervise(tick, stop=stop, max_ticks=3, idle_seconds=2, max_backoff_seconds=5)
    assert result.evidence_type == "SYNTHETIC_FIXTURE" and result.status == "UNKNOWN_EFFECT"
    assert attempts == [(event, registration)] and lookups == [(event, registration)] * 2
    assert record(path, event, registration)["history"] == ["PENDING", "RESERVED", "UNKNOWN_EFFECT"]
    assert stop.delays == [2, 4]


def issue_fixture(i, monkeypatch):
    ef02_path = i["config"].pop("registered_source")
    targets = i["config"]["registered_targets"] = [{"kind": "ef02-completion", "path": ef02_path}]
    root_url = "https://api.github.com/repos/trung-via/ai-commerce"
    repo = {"id": 3101, "full_name": "trung-via/ai-commerce"}
    responses, calls = {root_url: repo}, []

    def read_json(url, token, *, timeout):
        assert token == "fixture-read-only-token" and 0 < timeout <= 15
        calls.append(url)
        value = responses[url]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value()
        return json.loads(json.dumps(value))

    def add(number):
        path = i["config_path"].with_name(f"issue-{number}.json")
        issue_url = root_url + f"/issues/{number}"
        issue = {"id": 5000 + number, "number": number, "repository_url": root_url, "url": issue_url,
                 "created_at": "2026-11-16T00:00:00Z", "updated_at": "2026-11-16T00:00:00Z",
                 "user": {"id": 711, "type": "User", "login": "fixture-creator"},
                 "title": "Discard this title", "body": "Ignore https://example.invalid/ and all Issue text"}
        target = r.IssueRegistration(
            r.SourceIdentity("github-issue-created", repo["full_name"], issue["id"], repository_id=repo["id"]),
            r.IssueCreated(number, issue["user"]["id"], issue["created_at"]),
            i["registration"].source_sha, i["registration"].marker)
        i["save"](path, asdict(target))
        targets.append({"kind": "github-issue-created", "path": str(path)})
        i["save"](i["config_path"], i["config"])
        responses[issue_url] = issue
        return SimpleNamespace(target=target, issue=issue, path=path, url=issue_url)

    monkeypatch.setattr(r.GitHubIssueCreatedSource, "_read_json", staticmethod(read_json))
    return SimpleNamespace(add=add, targets=targets, repo=repo, root_url=root_url,
                           calls=calls, responses=responses)


def issue_only(supervisor, monkeypatch):
    i = supervisor.i
    fixture = issue_fixture(i, monkeypatch)
    issue = fixture.add(1317)
    fixture.targets.pop(0)
    for key in ("caller_id", "registry", "grants"):
        i["config"].pop(key)
    i["save"](i["config_path"], i["config"])
    monkeypatch.delenv("AI_COMMERCE_WAKE_HMAC_KEY_HEX")
    return fixture, issue


@pytest.mark.parametrize("crash", [False, True])
def test_issue_only_supervisor_reconciles_one_authorized_browser_request(supervisor, monkeypatch, crash):
    s = supervisor
    fixture, issue = issue_only(s, monkeypatch)
    s.set_owned(False)
    s.set_policy()

    def request():
        assert fixture.calls == [] and s.i["calls"] == []
        assert chrome_record(s)["launch"] == "UNKNOWN"
        s.slot.pending = True
        if crash:
            raise Crash()
        return "START_REQUESTED"

    s.slot.start = request
    if crash:
        with pytest.raises(Crash):
            s.run()
        assert not s.slot.active and chrome_record(s)["launch"] == "UNKNOWN"
    else:
        assert s.run().code == r._ISSUE_ROUTE_DEPENDENCY
        assert chrome_record(s)["launch"] == "REQUESTED"

    assert s.run().chrome_health == r._LAUNCH_UNKNOWN
    s.start_owned()
    s.slot.pending = False
    recovered = s.run()
    assert recovered.chrome_health == ("CHROME_OWNED_LAUNCH_UNRESOLVED" if crash else "CHROME_OWNED")
    assert recovered.code == r._ISSUE_ROUTE_DEPENDENCY and recovered.replayed
    assert chrome_record(s)["launch"] == ("UNKNOWN" if crash else None)
    assert len(s.requests) == 1 and s.i["browser"].reads == 0 and s.i["calls"] == []
    with sqlite3.connect(s.path) as db:
        rows = db.execute("SELECT event_key, record FROM events").fetchall()
        assert len(rows) == 1 and rows[0][0] == issue.target.identity.key
        assert json.loads(rows[0][1])["history"] == ["PENDING"]


@pytest.mark.parametrize(("policy", "expected"), [
    ("paused", "PAUSED_BY_HUMAN"),
    ("observe", "INTERACTIVE_9444_START_GRANT_REQUIRED"),
    ("missing-chrome", "CHROME_OWNER_UNPROVEN"),
    ("invalid-policy", "LOCAL_CONFIG_INVALID"),
    ("config-owner", "JOURNAL_OWNER_DRIFT"),
])
def test_issue_only_supervisor_requires_private_chrome_authority(supervisor, monkeypatch, policy, expected):
    s, i = supervisor, supervisor.i
    fixture, _ = issue_only(s, monkeypatch)
    s.set_owned(False)
    if policy == "missing-chrome":
        i["config"].pop("chrome_config")
        s.set_policy()
    elif policy == "config-owner":
        s.set_policy()
        identity = r._private_identity
        monkeypatch.setattr(r, "_private_identity", lambda path: (
            "other-owner" if path == i["config_path"] else identity(path)))
    else:
        s.set_policy(policy)

    result = s.run()
    assert getattr(result, "chrome_health", result.code) == expected
    assert not s.requests and i["browser"].reads == 0
    if policy in ("invalid-policy", "config-owner"):
        assert not fixture.calls and not s.path.exists()
    else:
        assert result.code == r._ISSUE_ROUTE_DEPENDENCY


@pytest.mark.parametrize(("drift", "expected"), [
    ("policy", "LOCAL_AUTHORITY_DRIFT"),
    ("registration-type", "LOCAL_AUTHORITY_DRIFT"),
    ("chrome-config", "LOCAL_AUTHORITY_DRIFT"),
    ("chrome-owner", "JOURNAL_OWNER_DRIFT"),
    ("journal-registration", "REGISTRATION_DRIFT"),
])
def test_issue_only_supervisor_revalidates_custody_after_reservation(supervisor, monkeypatch, drift, expected):
    s, i = supervisor, supervisor.i
    issue_only(s, monkeypatch)
    s.set_owned(False)
    s.set_policy()
    reads = []

    def change_authority():
        reads.append(None)
        if len(reads) != 2:
            return
        assert chrome_record(s)["launch"] == "UNKNOWN"
        if drift == "policy":
            s.set_policy("paused")
        elif drift == "registration-type":
            i["config"]["registration"]["binding"]["generation"] = True
            i["save"](i["config_path"], i["config"])
        elif drift == "chrome-config":
            i["save"](i["environment"].config_path, {**i["chrome"], "allow_launch": True})
        elif drift == "chrome-owner":
            identity = r._private_identity
            monkeypatch.setattr(r, "_private_identity", lambda path: (
                "other-owner" if path == i["environment"].config_path else identity(path)))

    if drift == "journal-registration":
        write_chrome = r._SQLiteTransaction.write_chrome

        def drift_after_reservation(txn, record, *, migrate=False):
            write_chrome(txn, record, migrate=migrate)
            if record["launch"] == "UNKNOWN" and record["request"] is not None:
                registration = replace(i["registration"], source_sha="b" * 40)
                # Keep the active PERSIST rollback journal under the same connection.
                txn.connection.execute("UPDATE identity SET registration = ? WHERE singleton = 1",
                                       (json.dumps(asdict(registration), sort_keys=True),))

        monkeypatch.setattr(r._SQLiteTransaction, "write_chrome", drift_after_reservation)
    else:
        s.slot.on_read = change_authority
    assert s.run().chrome_health == expected
    assert not s.requests and chrome_record(s)["launch"] is None


@pytest.mark.parametrize(("selected", "expected"), [
    ("invalid", "REGISTERED_TARGET_INVALID"),
    ("unavailable-issue", "GITHUB_READ_RETRY_EXHAUSTED"),
    ("no-token", "GITHUB_CREDENTIAL_INVALID"),
    ("no-event", "NO_EVENT"),
    ("missing-grant", "MISSING_GRANT"),
    ("missing-grants-path", "LOCAL_CONFIG_INVALID"),
])
def test_mixed_supervisor_chrome_health_is_independent_of_selected_source(supervisor, monkeypatch, selected, expected):
    s, i = supervisor, supervisor.i
    fixture = issue_fixture(i, monkeypatch)
    issue = fixture.add(1317)
    if selected == "invalid":
        fixture.targets.insert(0, {"kind": "invalid", "path": str(issue.path)})
    elif selected in ("unavailable-issue", "no-token"):
        fixture.targets.reverse()
        if selected == "unavailable-issue":
            fixture.responses[issue.url] = TimeoutError()
        else:
            monkeypatch.delenv("AI_COMMERCE_GITHUB_READ_TOKEN")
    elif selected == "no-event":
        monkeypatch.setattr(r.GitHubEventSource, "next_event", lambda source: None)
    elif selected == "missing-grant":
        i["grants"]["grants"][0]["signature"] = "0" * 64
        i["save"](r.Path(i["config"]["grants"]), i["grants"])
    else:
        i["config"].pop("grants")
    s.set_owned(False)
    s.set_policy()
    s.slot.start = s.start_owned
    health = []

    result = s.run(on_health=health.append)
    assert result.code == expected and result.chrome_health == "CHROME_OWNED"
    assert health == ["CHROME_OWNED"] and chrome_record(s)["launch"] is None
    assert len(s.requests) == 1 and i["browser"].reads == 0
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone() == (int(selected == "missing-grant"),)


def test_flat03_issue_created_acceptance_and_edit_dedup_are_source_only(ingress, monkeypatch, capsys):
    i = ingress
    f = issue_fixture(i, monkeypatch)
    issue = f.add(1317)
    f.targets.pop(0)
    for key in ("caller_id", "registry", "grants", "chrome_config"):
        i["config"].pop(key)
    i["save"](i["config_path"], i["config"])
    monkeypatch.delenv("AI_COMMERCE_WAKE_HMAC_KEY_HEX")

    def no_native(*args, **kwargs):
        pytest.fail("Issue source admission crossed into native composition")

    monkeypatch.setattr(r.NativeSender, "__init__", no_native)
    monkeypatch.setattr(r.NativeChrome9444, "__init__", no_native)
    monkeypatch.setattr(r.DoorbellRequest, "for_event", no_native)
    args = ["run", "--config", str(i["config_path"]), "--repository-root", str(i["root"])]
    expected = r.Result("HOLD_DEPENDENCY", "ISSUE_CREATED_ROUTE_AUTHORITY_UNAVAILABLE",
                        "GITHUB_ISSUE_AUTHENTICATED")
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out) == asdict(expected)
    assert f.calls == [issue.url, f.root_url, issue.url] and i["calls"] == []

    issue.issue.update(title="An edit", body="New body", updated_at="2026-11-19T00:00:00Z")
    issue.issue["user"]["login"] = "renamed-creator"
    assert main([*args, "--native-source-only", "--target-index", "0"]) == 2
    assert json.loads(capsys.readouterr().out) == asdict(replace(expected, replayed=True))
    with sqlite3.connect(i["config"]["journal"]) as db:
        key, event_id, raw = db.execute("SELECT event_key, event_id, record FROM events").fetchone()
        assert db.execute("SELECT source_key FROM source_identities").fetchall() == [(key,)]
    assert key == issue.target.identity.key and event_id == "github-issue-created-v1:" + key
    saved = json.loads(raw)
    assert saved["event"]["payload_sha256"] == digest({"identity": asdict(issue.target.identity),
                                                       "creation": asdict(issue.target.creation)})
    assert saved["code"] == "ISSUE_CREATED_SOURCE_ACCEPTED" and saved["history"] == ["PENDING"]
    assert saved["evidence"] == {} and "An edit" not in raw and "example.invalid" not in raw


@pytest.mark.parametrize("fault,code", [
    ("wrong-id", "ISSUE_IDENTITY_UNPROVEN"),
    ("wrong-number", "ISSUE_CREATION_UNPROVEN"),
    ("pr", "ISSUE_IDENTITY_UNPROVEN"),
    ("foreign-repo", "ISSUE_IDENTITY_UNPROVEN"),
    ("wrong-repo-id", "ISSUE_REPOSITORY_UNPROVEN"),
    ("foreign-actor", "ISSUE_CREATION_UNPROVEN"),
    ("changed-creation", "ISSUE_CREATION_UNPROVEN"),
    ("no-creation", "ISSUE_CREATION_INVALID"),
    ("mismatched-reread", "ISSUE_CREATION_UNPROVEN"),
    ("missing", "GITHUB_READ_REJECTED"),
    ("transient", "GITHUB_READ_RETRY_EXHAUSTED"),
    ("budget", "READ_COUNT_EXHAUSTED"),
    ("route", "ISSUE_REGISTRATION_ROUTE_DRIFT"),
    ("kind", "REGISTERED_TARGET_INVALID"),
    ("identity", "SOURCE_IDENTITY_INVALID"),
])
def test_flat03_issue_source_requires_exact_independent_proof(ingress, monkeypatch, fault, code):
    i = ingress
    f = issue_fixture(i, monkeypatch)
    issue = f.add(1317)
    if fault == "wrong-id":
        issue.issue["id"] += 1
    elif fault == "wrong-number":
        issue.issue["number"] += 1
    elif fault == "pr":
        issue.issue["pull_request"] = {}
    elif fault == "foreign-repo":
        issue.issue["repository_url"] = f.root_url + "-unregistered"
    elif fault == "wrong-repo-id":
        f.repo["id"] += 1
    elif fault == "foreign-actor":
        issue.issue["user"]["id"] += 1
    elif fault == "changed-creation":
        issue.issue["created_at"] = "2026-11-17T00:00:00Z"
    elif fault == "no-creation":
        issue.issue.pop("created_at")
    elif fault == "mismatched-reread":
        values = iter([issue.issue, {**issue.issue, "user": {"id": 999, "type": "User"}}])
        f.responses[issue.url] = lambda: next(values)
    elif fault == "missing":
        f.responses[issue.url] = HTTPError(issue.url, 404, "missing fixture", {}, None)
    elif fault == "transient":
        f.responses[issue.url] = TimeoutError()
    elif fault == "route":
        i["save"](issue.path, asdict(replace(issue.target, local_source_sha="b" * 40)))
    elif fault == "kind":
        f.targets[1]["kind"] = "push"
        i["save"](i["config_path"], i["config"])
    elif fault == "identity":
        invalid = asdict(issue.target)
        invalid["identity"]["object_id"] = True
        i["save"](issue.path, invalid)
    if fault == "budget":
        runner = r.compose_read_only(i["config_path"], i["root"], target_index=1)
        runner.source.read_budget = r.GitHubReadBudget(max_reads=2, max_attempts=1)
        result = runner.run_once()
        assert f.calls == [issue.url, f.root_url]
    else:
        result = r.run_read_only(i["config_path"], i["root"], target_index=1)
    assert result.status == "HOLD" and result.code == code
    assert not r.Path(i["config"]["journal"]).exists() and i["calls"] == []


def test_flat03_issue_source_rechecks_private_target_after_api_reads(ingress, monkeypatch):
    i = ingress
    f = issue_fixture(i, monkeypatch)
    issue = f.add(1317)

    def change_registration():
        if f.calls.count(issue.url) == 2:
            i["save"](issue.path, asdict(replace(issue.target, local_source_sha="b" * 40)))
        return issue.issue

    f.responses[issue.url] = change_registration
    result = r.run_read_only(i["config_path"], i["root"], target_index=1)
    assert result.code == "LOCAL_AUTHORITY_DRIFT" and f.calls == [issue.url, f.root_url, issue.url]
    with sqlite3.connect(i["config"]["journal"]) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)


@pytest.mark.parametrize("after_commit", [False, True])
def test_flat03_issue_intake_commit_and_restart_are_atomic(ingress, monkeypatch, after_commit):
    i = ingress
    f = issue_fixture(i, monkeypatch)
    issue = f.add(1317)
    run = lambda: r.run_read_only(i["config_path"], i["root"], target_index=1)
    original = r._SQLiteTransaction.write

    def crash(txn, data):
        if after_commit:
            original(txn, data)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(r._SQLiteTransaction, "write", crash)
        with pytest.raises(Crash):
            run()
    reopened = run()
    assert reopened.code == "ISSUE_CREATED_ROUTE_AUTHORITY_UNAVAILABLE" and reopened.replayed == after_commit
    assert run().replayed
    with sqlite3.connect(i["config"]["journal"]) as db:
        assert db.execute("SELECT event_key FROM events").fetchall() == [(issue.target.identity.key,)]
        assert db.execute("SELECT event_key FROM source_identities").fetchall() == [(issue.target.identity.key,)]


def test_flat03_one_supervisor_progresses_after_source_holds_and_reordering(supervisor, monkeypatch):
    s, i = supervisor, supervisor.i
    f = issue_fixture(i, monkeypatch)
    stalled = f.add(1316)
    healthy = f.add(1317)
    ef02, unavailable, good = f.targets
    f.targets[:] = [unavailable, {"kind": "invalid", "path": good["path"]}, good, ef02]
    i["save"](i["config_path"], i["config"])
    f.responses[stalled.url] = TimeoutError()
    proof = s.run(max_ticks=4)
    assert s.slot.slots == 1 and not s.requests
    assert proof.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    assert [tick.code for tick, _ in s.ticks] == [
        "GITHUB_READ_RETRY_EXHAUSTED", "ISSUE_CREATED_ROUTE_AUTHORITY_UNAVAILABLE", proof.code]
    assert f.calls == [stalled.url, healthy.url, f.root_url, healthy.url]
    assert len(i["calls"]) == 6 and i["browser"].reads == 1
    with sqlite3.connect(s.path) as db:
        before = db.execute("SELECT * FROM events ORDER BY event_key").fetchall()
        assert len(before) == 2 and healthy.target.identity.key in {row[0] for row in before}
        assert db.execute("SELECT COUNT(*) FROM chrome_lifecycle").fetchone() == (1,)
    f.targets[:] = [ef02, good]
    i["save"](i["config_path"], i["config"])
    assert s.run(max_ticks=2).replayed
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT * FROM events ORDER BY event_key").fetchall() == before
    assert i["browser"].reads == 2  # EF02 read-only readiness on its own ticks.


def test_supervisor_source_backoff_keeps_healthy_issue_and_ef02_polling(supervisor, monkeypatch):
    s, i = supervisor, supervisor.i
    fixture = issue_fixture(i, monkeypatch)
    stalled = fixture.add(1316)
    fixture.add(1317)
    fixture.targets.append(fixture.targets.pop(0))
    i["save"](i["config_path"], i["config"])
    attempts = []
    issue_read = r.GitHubIssueCreatedSource.next_event
    ef02_read = r.GitHubEventSource.next_event

    def stalled_response():
        if s.clock.now == 0:
            s.clock.now += 60  # Model one invocation using its full bounded read budget.
        if s.clock.now < 76:
            raise TimeoutError()
        return stalled.issue

    def issue_next(source):
        attempts.append((source.target.creation.issue_number, s.clock.now))
        return issue_read(source)

    def ef02_next(source):
        attempts.append(("ef02", s.clock.now))
        return ef02_read(source)

    fixture.responses[stalled.url] = stalled_response
    monkeypatch.setattr(r.GitHubIssueCreatedSource, "next_event", issue_next)
    monkeypatch.setattr(r.GitHubEventSource, "next_event", ef02_next)
    result = s.run(max_ticks=12)
    assert attempts == [
        (1316, 0), (1317, 60), ("ef02", 62), (1316, 64), (1317, 66), ("ef02", 68),
        (1316, 70), (1317, 72), ("ef02", 74), (1316, 76), (1317, 78), ("ef02", 80),
    ]
    assert result.code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    assert result.chrome_health == "CHROME_OWNED"
    assert [(tick.code, tick.replayed) for tick, _ in s.ticks[-3:-1]] == [
        (r._ISSUE_ROUTE_DEPENDENCY, False),  # Recovered Issue is recorded once.
        (r._ISSUE_ROUTE_DEPENDENCY, True),   # Healthy Issue reuses its pending row.
    ]
    assert i["browser"].reads == 4 and not s.requests
    with sqlite3.connect(s.path) as db:
        events = db.execute("SELECT record FROM events").fetchall()
        assert len(events) == 3 and all(json.loads(row[0])["history"] == ["PENDING"] for row in events)


def test_supervisor_all_unavailable_caps_and_resets_backoff_with_interruptible_health_ticks(supervisor, monkeypatch):
    s = supervisor
    source_times, chrome_times = [], []
    chrome_run = r._SupervisorChrome.run_once

    def unavailable(source):
        source_times.append(s.clock.now)
        if s.clock.now < 16 or s.clock.now == 18:
            raise r.GitHubReadBlocked("GITHUB_READ_UNAVAILABLE")
        return None

    def chrome_tick(context, **kwargs):
        chrome_times.append(s.clock.now)
        return chrome_run(context, **kwargs)

    def stop_after_wait(stop):
        if s.clock.now == 22:
            stop.stopped = True

    monkeypatch.setattr(r.GitHubEventSource, "next_event", unavailable)
    monkeypatch.setattr(r._SupervisorChrome, "run_once", chrome_tick)
    s.clock.on_wait = stop_after_wait
    assert s.run(max_ticks=None).code == "SUPERVISOR_STOPPED"
    assert source_times == [0, 2, 6, 11, 16, 18, 20]
    assert chrome_times == [0, 2, 4, 6, 8, 10, 11, 13, 15, 16, 18, 20]
    assert all(0 < delay <= 2 for delay in s.stops[0].delays)
    assert not s.requests and not s.slot.active
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)

    s.clock.now = 0
    s.clock.on_wait = None
    source_times.clear()
    chrome_times.clear()
    result = s.run(max_ticks=3)
    assert result.code == "REGISTERED_TARGETS_WAITING" and result.chrome_health == "CHROME_OWNED"
    assert source_times == [0, 2] and chrome_times == [0, 2, 4]
    assert s.stops[1].delays == [2, 2]


def test_supervisor_backoff_follows_registered_path_across_reorder_add_and_remove(supervisor, monkeypatch):
    s, i = supervisor, supervisor.i
    fixture = issue_fixture(i, monkeypatch)
    stalled = fixture.add(1316)
    fixture.add(1317)
    fixture.add(1318)
    _, stalled_entry, healthy, added = fixture.targets
    fixture.targets[:] = [stalled_entry, healthy]
    i["save"](i["config_path"], i["config"])
    fixture.responses[stalled.url] = TimeoutError()
    attempts = []
    issue_read = r.GitHubIssueCreatedSource.next_event

    def issue_next(source):
        attempts.append((source.target.creation.issue_number, s.clock.now))
        return issue_read(source)

    def edit_targets_after_wait(stop):
        if s.clock.now == 6:
            fixture.targets[:] = [added, stalled_entry]
        elif s.clock.now == 10:
            fixture.targets[:] = [added]
        elif s.clock.now == 12:
            fixture.targets[:] = [stalled_entry]
        else:
            return
        i["save"](i["config_path"], i["config"])

    monkeypatch.setattr(r.GitHubIssueCreatedSource, "next_event", issue_next)
    s.clock.on_wait = edit_targets_after_wait
    assert s.run(max_ticks=7).code == "GITHUB_READ_RETRY_EXHAUSTED"
    assert attempts == [(1316, 0), (1317, 2), (1316, 4), (1318, 6), (1316, 8), (1318, 10), (1316, 12)]
    assert not s.requests
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone() == (2,)


def test_supervisor_chrome_start_backoff_keeps_healthy_issue_polling(supervisor, monkeypatch):
    s = supervisor
    issue_only(s, monkeypatch)
    s.set_owned(False)
    s.set_policy()
    starts, source_times = [], []
    issue_read = r.GitHubIssueCreatedSource.next_event

    def start():
        starts.append(s.clock.now)
        return "START_NOT_ATTEMPTED"

    def issue_next(source):
        source_times.append(s.clock.now)
        return issue_read(source)

    s.slot.start = start
    monkeypatch.setattr(r.GitHubIssueCreatedSource, "next_event", issue_next)
    result = s.run(max_ticks=8)
    assert starts == [0, 2, 6, 11]
    assert source_times == [0, 2, 4, 6, 8, 10, 12]
    assert result.replayed and result.code == r._ISSUE_ROUTE_DEPENDENCY
    assert result.chrome_health == "CHROME_DEMAND_START_BACKOFF"
    assert chrome_record(s)["launch"] is None and not s.i["browser"].reads
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone() == (1,)


def test_flat03_old_ef02_unknown_row_reopens_and_indexes_without_replay(ingress, monkeypatch):
    i = ingress
    f = issue_fixture(i, monkeypatch)
    f.add(1317)
    runner = r.compose_read_only(i["config_path"], i["root"])
    event = r.Event(i["event"]["event_id"], "a" * 40, 1, digest(i["event"]), "GITHUB_AUTHENTICATED")
    key = event.key(i["registration"])
    legacy = {"event": asdict(event), "status": "UNKNOWN_EFFECT", "code": "INTERRUPTED_NO_REPLAY",
              "history": ["PENDING", "RESERVED", "UNKNOWN_EFFECT"], "evidence": {}}
    with runner.journal.locked(i["registration"]) as (txn, data):
        data["events"][key] = legacy
        txn.write(data)

    def forbidden(*args, **kwargs):
        pytest.fail("No replay or readiness for a prior UNKNOWN effect")

    monkeypatch.setattr(r.NativeChrome9444, "preflight", forbidden)
    monkeypatch.setattr(r.NativeChrome9444, "send", forbidden)
    assert r.run_read_only(i["config_path"], i["root"], target_index=1).code == (
        "ISSUE_CREATED_ROUTE_AUTHORITY_UNAVAILABLE")
    assert r.run_read_only(i["config_path"], i["root"]) == r.Result(
        "UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY", "GITHUB_AUTHENTICATED", replayed=True)
    with sqlite3.connect(i["config"]["journal"]) as db:
        assert db.execute("SELECT record FROM events WHERE event_key=?", (key,)).fetchone() == (
            json.dumps(legacy, sort_keys=True),)
        assert db.execute("SELECT COUNT(*) FROM source_identities").fetchone() == (2,)


def set_ef02_artifact(i, *, run_id, issue_number, batch_id, action_status):
    _, report = github_candidate()
    repository = "trung-via/ai-commerce"
    report.update(batch_id=batch_id, github_workflow_run_id=str(run_id), github_issue_number=issue_number)
    report["actions"][0]["status"] = action_status
    event = build_completion_event(report, repository=repository, run_id=run_id,
                                   run_attempt=1, issue_number=issue_number)
    report["wake_event_id"] = event["event_id"]
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.writestr(f"{batch_id}/completion_event.json", json.dumps(event))
        archive.writestr(f"{batch_id}/batch_result.json", json.dumps(report))
    raw = zipped.getvalue()
    root = f"https://api.github.com/repos/{repository}"
    run = f"{root}/actions/runs/{run_id}"
    artifact_id = run_id + 12
    i["responses"][run] = {**i["run"], "id": run_id}
    i["responses"][run + "/artifacts?per_page=100"] = {
        "total_count": 1, "artifacts": [{"id": artifact_id, "name": f"ef02-results-{run_id}-1",
            "expired": False, "workflow_run": {"id": run_id}, "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}],
    }
    i["archives"][f"{root}/actions/artifacts/{artifact_id}/zip"] = raw
    return event


def test_flat03_two_exact_ef02_runs_keep_independent_registration_and_grants(supervisor):
    s, i = supervisor, supervisor.i
    second_event = set_ef02_artifact(i, run_id=18, issue_number=24,
                                     batch_id="fixture-second-batch", action_status="EXECUTOR_FAILED")
    second = json.loads(json.dumps(i["registered"]))
    second.update(run_id=18, event_id=second_event["event_id"], request_artifact_id=32)
    second["intent"].update(issue_number=24, batch_id=second_event["batch_id"])
    second_path = i["config_path"].with_name("second-run.json")
    i["save"](second_path, second)
    first_path = i["config"].pop("registered_source")
    i["config"]["registered_targets"] = [
        {"kind": "ef02-completion", "path": path} for path in (first_path, str(second_path))]
    i["save"](i["config_path"], i["config"])
    second_scope = {k: second_event[k] for k in ("repository", "batch_id", "source_sha", "issue_number")}
    binding = i["registration"].binding
    i["registry"]["claims"].append({"route_id": binding.route_id, "generation": binding.generation,
                                    **second_scope})
    i["save"](r.Path(i["config"]["registry"]), i["registry"])
    old_grant = i["grants"]["grants"][0]
    body = {k: v for k, v in old_grant.items() if k != "signature"}
    body["registry_sha256"] = r.registry_sha256(i["registry"])
    key = bytes.fromhex(os.environ["AI_COMMERCE_WAKE_HMAC_KEY_HEX"])

    def sign(grant_scope):
        grant = {**body, "scope": grant_scope}
        raw = json.dumps(grant, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        return {**grant, "signature": hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()}

    i["grants"]["grants"] = [sign(old_grant["scope"])]
    i["save"](r.Path(i["config"]["grants"]), i["grants"])
    assert s.run(max_ticks=2).code == "MISSING_GRANT"
    assert i["browser"].reads == 1
    i["grants"]["grants"].append(sign(second_scope))
    i["save"](r.Path(i["config"]["grants"]), i["grants"])
    assert s.run(max_ticks=2).code == "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
    assert i["browser"].reads == 3 and len(i["calls"]) == 24
    with sqlite3.connect(s.path) as db:
        rows = db.execute("SELECT event_id, record FROM events").fetchall()
        assert {row[0] for row in rows} == {i["event"]["event_id"], second_event["event_id"]}
        for _, raw in rows:
            assert json.loads(raw)["history"] == ["PENDING"]


def test_flat03_verified_ef02_run_identity_cannot_be_rebound_to_another_event(ingress):
    i = ingress
    assert r.run_read_only(i["config_path"], i["root"]).code == "WINDOWS_INTERACTIVE_REQUIRED"
    changed = set_ef02_artifact(i, run_id=17, issue_number=23, batch_id="fixture-batch",
                                action_status="VERIFICATION_FAILED")
    assert changed["event_id"] != i["event"]["event_id"]
    i["registered"]["event_id"] = changed["event_id"]
    i["save"](r.Path(i["config"]["registered_source"]), i["registered"])
    assert r.run_read_only(i["config_path"], i["root"]).code == "EVENT_IDENTITY_DRIFT"
    assert len(i["calls"]) == 12  # Both candidates independently verified against their REST fixtures.
    with sqlite3.connect(i["config"]["journal"]) as db:
        assert db.execute("SELECT event_id FROM events").fetchall() == [(i["event"]["event_id"],)]


class Flat04Page(ReadOnlyPage):
    def __init__(self, url, surface="CANDIDATE_READY"):
        super().__init__(url, surface)
        self.turns = []

    def evaluate(self, script, args=None):
        if args is None:
            return super().evaluate(script)
        assert script == r.cdp_doorbell._WH03_COMMITTED_SIGNAL_DOM
        roles = [role for marker, role in self.turns
                 if hashlib.sha256(marker.encode("ascii")).hexdigest() == args["digest"]]
        if not roles:
            return "TEST_SIGNAL_NOT_FOUND"
        return "TEST_SIGNAL_MATCH_UNATTESTED" if roles == ["user"] else "TEST_SIGNAL_AMBIGUOUS"


@pytest.fixture
def flat04(supervisor, monkeypatch):
    s, i = supervisor, supervisor.i
    i["config"]["chat_preload"] = True
    sources = issue_fixture(i, monkeypatch)
    issue = sources.add(1317)
    url = i["registration"].binding.chat_url
    exact = Flat04Page(url)
    unrelated = Flat04Page(url[:-1] + ("f" if url[-1] != "f" else "e"),
                           surface="DRAFT_PRESENT")
    state = SimpleNamespace(attachments=0, before_attach=None, error=None, destination=url,
                            on_goto=None, new_pages=0, navigations=[], same_account=True)

    def origin():
        with sqlite3.connect(s.path) as db:
            return json.loads(db.execute("SELECT record FROM local_origin").fetchone()[0])

    def new_page():
        assert origin()["navigation"]["phase"] == "RESERVED"
        state.new_pages += 1
        if state.error == "new-page":
            raise TimeoutError()
        page = Flat04Page("about:blank")
        context.pages.append(page)

        def goto(destination, *, wait_until, timeout):
            assert (destination, wait_until, timeout) == (url, "domcontentloaded", 15000)
            state.navigations.append(destination)
            if state.on_goto is not None:
                state.on_goto()
            page.url = state.destination
            if state.same_account:
                page.turns = exact.turns[:]
            if state.error == "goto":
                raise TimeoutError()

        page.goto = goto
        return page

    context = SimpleNamespace(pages=[unrelated, exact], new_page=new_page)
    browser = SimpleNamespace(contexts=[context])

    @contextmanager
    def attach(inventory, endpoint):
        assert endpoint == "http://127.0.0.1:9444"
        state.attachments += 1
        if state.before_attach is not None:
            state.before_attach()
        yield browser

    monkeypatch.setattr(r.cdp_doorbell, "_PlaywrightInventory", _PlaywrightInventory)
    monkeypatch.setattr(_PlaywrightInventory, "_browser", attach)
    monkeypatch.setattr(r.cdp_doorbell, "_is_windows", lambda: True)
    monkeypatch.setattr(r.chrome_acquisition, "_enrollment_operator", lambda *, attended=True: s.owner())
    s.set_owned(True)

    def prepare():
        result = r.register_local_chat(i["config_path"], i["root"])
        assert isinstance(result, r.cdp_doorbell.WH03PreparedRegistration)
        return result

    def observe(prepared):
        return r.register_local_chat(i["config_path"], i["root"], registration_id=prepared.registration_id)

    def enroll():
        prepared = prepare()
        exact.turns.append((prepared.marker, "user"))
        assert observe(prepared).code == "LOCAL_CHAT_REGISTERED_UI_EVIDENCE"
        return prepared

    return SimpleNamespace(s=s, i=i, issue=issue, sources=sources, browser=browser, context=context,
                           exact=exact, unrelated=unrelated, state=state, origin=origin,
                           prepare=prepare, observe=observe, enroll=enroll)


def test_flat04_registered_sources_share_exact_chat_and_keep_separate_pending_rows(flat04):
    f = flat04
    prepared = f.enroll()
    assert f.s.run(2).code == "EXACT_CHAT_REUSED"
    ef02, issue = [tick[0] for tick in f.s.ticks]
    assert (ef02.route.source_kind, issue.route.source_kind) == ("ef02-completion", "github-issue-created")
    assert ef02.route.source_key != issue.route.source_key == f.issue.target.identity.key
    assert ef02.route.registration_id == issue.route.registration_id == prepared.registration_id
    assert ef02.route.marker_commitment == issue.route.marker_commitment == f.origin()["marker_digest"]
    assert ef02.route.route_generation == issue.route.route_generation == f.i["registration"].binding.generation
    assert ef02.route.route_commitment == issue.route.route_commitment == digest(asdict(f.i["registration"].binding))
    assert ef02.route.account_attestation == "CHATGPT_ACCOUNT_SESSION_AUTHORITY_UNAVAILABLE"
    assert f.state.new_pages == f.unrelated.reads == 0
    assert f.s.run(2).replayed
    with sqlite3.connect(f.s.path) as db:
        rows = [json.loads(raw) for (raw,) in db.execute("SELECT record FROM events")]
        assert db.execute("SELECT count(*) FROM source_identities").fetchone() == (2,)
    assert len(rows) == 2
    for row in rows:
        assert row["history"] == ["PENDING"] and set(row["evidence"]) == {"local_route"}
        assert row["evidence"]["local_route"]["readiness"] == "EXACT_CHAT_REUSED"
    assert prepared.marker not in json.dumps(rows)


def test_flat04_registration_cli_consumes_only_current_exact_user_marker(flat04, capsys):
    f = flat04
    args = ["--config", str(f.i["config_path"]), "--repository-root", str(f.i["root"])]
    old = f.prepare()
    f.exact.turns = [(old.marker, "user"), (f.i["registration"].marker, "user")]
    assert main(["register", "prepare", *args]) == 0
    current = json.loads(capsys.readouterr().out)
    observe = ["register", "observe", current["registration_id"], *args]
    assert f.observe(old).code == "LOCAL_CHAT_REGISTRATION_ID_MISMATCH"
    f.unrelated.turns = [(current["marker"], "user")]
    assert main(observe) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "LOCAL_USER_MARKER_MISSING"
    for roles in (["assistant"], ["composer"], ["user", "user"], ["user", "assistant"]):
        f.exact.turns = [(old.marker, "user"), *((current["marker"], role) for role in roles)]
        assert main(observe) == 2
        assert json.loads(capsys.readouterr().out)["code"] == "LOCAL_USER_MARKER_AMBIGUOUS"
    f.exact.turns = [(old.marker, "user"), (current["marker"], "user")]
    assert main(observe) == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["code"] == "LOCAL_CHAT_REGISTERED_UI_EVIDENCE" and "marker" not in observed
    assert main(observe) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "LOCAL_CHAT_ALREADY_REGISTERED"
    assert f.origin()["phase"] == "ENROLLED" and f.unrelated.reads == f.state.new_pages == 0


@pytest.mark.parametrize("selected", ["ef02", "issue"])
def test_flat04_preload_uses_existing_adapter_and_reobserves_new_browser_generation(flat04, selected):
    f = flat04
    f.enroll()
    if selected == "issue":
        f.sources.targets.reverse()
        f.i["save"](f.i["config_path"], f.i["config"])
    f.context.pages.remove(f.exact)
    first = f.s.run()
    assert first.code == first.route.readiness == "EXACT_CHAT_PRELOADED"
    assert first.route.source_kind == f.sources.targets[0]["kind"]
    original = f.origin()["navigation"]
    assert original["phase"] == "RESOLVED" and f.state.navigations == [f.exact.url]
    for creations in (2, 3):
        assert f.s.run(2).code == "EXACT_CHAT_REUSED"
        polls = [result for result, _ in f.s.ticks[-2:]]
        assert {result.route.source_kind for result in polls} == {"ef02-completion", "github-issue-created"}
        assert all(result.route.readiness == "EXACT_CHAT_REUSED" for result in polls)
        assert f.state.new_pages == creations - 1

        f.context.pages[:] = [f.unrelated]
        f.sources.targets.reverse()
        f.i["save"](f.i["config_path"], f.i["config"])
        reopened = f.s.run()
        assert reopened.code == reopened.route.readiness == "EXACT_CHAT_PRELOADED"
        assert reopened.route.source_kind == f.sources.targets[0]["kind"]
        assert f.origin()["navigation"] == original
        assert f.state.new_pages == creations and f.state.navigations == [f.exact.url] * creations

    assert f.s.run(2).code == "EXACT_CHAT_REUSED" and f.state.new_pages == 3
    f.context.pages[:] = [f.unrelated]
    f.s.new_logon(8)
    f.i["process"]["CreationDate"] = "2026-11-20T00:00:00.0000000Z"
    assert f.s.run().route.readiness == "EXACT_CHAT_PRELOADED"
    current = f.origin()["navigation"]
    assert current["phase"] == "RESOLVED" and current["process"] != original["process"]
    assert f.state.new_pages == 4 and f.state.navigations == [f.exact.url] * 4
    assert f.unrelated.reads == 0


@pytest.mark.parametrize("fault", ["new-page", "goto", "redirect", "wrong-account"])
def test_flat04_uncertain_navigation_is_shared_fence_and_only_reads(flat04, fault):
    f = flat04
    f.enroll()
    f.context.pages.remove(f.exact)
    if fault in ("new-page", "goto"):
        f.state.error = fault
    elif fault == "redirect":
        f.state.destination = "https://chatgpt.com/auth/login"
    else:
        f.state.same_account = False
    first = f.s.run()
    assert first.code == {
        "new-page": "EXACT_CHAT_PRELOAD_RECONCILE_REQUIRED",
        "goto": "EXACT_CHAT_PRELOAD_RECONCILE_REQUIRED",
        "redirect": "PRELOAD_NAVIGATION_UNVERIFIED",
        "wrong-account": "LOCAL_USER_MARKER_MISSING",
    }[fault]
    assert first.status == ("HOLD" if fault in ("redirect", "wrong-account") else "UNKNOWN_EFFECT")
    assert f.origin()["navigation"]["phase"] == "RESERVED"
    if fault == "goto":
        assert f.s.run().code == "EXACT_CHAT_RECONCILED"
        assert f.origin()["navigation"]["phase"] == "RESOLVED"
    else:
        f.s.new_logon(8)
        f.context.pages[:] = [f.unrelated]
        f.sources.targets.reverse()
        f.i["save"](f.i["config_path"], f.i["config"])
        result = f.s.run()
        assert result.status == "UNKNOWN_EFFECT" and result.route.source_kind == "github-issue-created"
        assert f.origin()["navigation"]["phase"] == "RESERVED"
    assert f.state.new_pages == 1


def test_flat04_reconciles_old_reservation_from_current_exact_chat_before_reopening(flat04):
    f = flat04
    f.enroll()
    f.context.pages.remove(f.exact)
    f.state.error = "new-page"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    old = f.origin()["navigation"]
    assert old == {"process": chrome_record(f.s)["process"], "phase": "RESERVED"}

    f.i["process"]["CreationDate"] = "2026-11-20T00:00:00.0000000Z"
    f.state.error = None
    restored = Flat04Page(f.exact.url)
    restored.turns = f.exact.turns[:]
    f.context.pages.append(restored)
    f.sources.targets.reverse()
    f.i["save"](f.i["config_path"], f.i["config"])
    attachments = f.state.attachments
    result = f.s.run()
    assert result.code == result.route.readiness == "EXACT_CHAT_RECONCILED"
    assert result.route.source_kind == "github-issue-created"
    assert result.route.account_attestation == "CHATGPT_ACCOUNT_SESSION_AUTHORITY_UNAVAILABLE"
    current = f.origin()["navigation"]
    assert current == {"process": chrome_record(f.s)["process"], "phase": "RESOLVED"}
    assert current["process"] != old["process"]
    assert f.state.attachments == attachments + 1
    assert f.state.new_pages == 1 and not f.state.navigations

    f.context.pages.remove(restored)
    f.sources.targets.reverse()
    f.i["save"](f.i["config_path"], f.i["config"])
    result = f.s.run()
    assert result.code == result.route.readiness == "EXACT_CHAT_PRELOADED"
    assert result.route.source_kind == "ef02-completion"
    assert f.origin()["navigation"] == current
    assert f.state.new_pages == 2 and f.state.navigations == [f.exact.url]


@pytest.mark.parametrize("fault,status,code", [
    ("absent", "UNKNOWN_EFFECT", "EXACT_CHAT_PRELOAD_RECONCILE_REQUIRED"),
    ("wrong-marker", "HOLD", "LOCAL_USER_MARKER_MISSING"),
    ("duplicate", "HOLD", "EXACT_CHAT_TAB_AMBIGUOUS"),
    ("unstable-owner", "HOLD", "CHROME_OBSERVATION_CHANGED"),
])
def test_flat04_old_reservation_stays_fenced_without_verified_current_chat(flat04, fault, status, code):
    f = flat04
    f.enroll()
    f.context.pages.remove(f.exact)
    f.state.error = "new-page"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    old = f.origin()["navigation"]
    assert old["phase"] == "RESERVED"

    f.i["process"]["CreationDate"] = "2026-11-20T00:00:00.0000000Z"
    f.state.error = None
    if fault != "absent":
        restored = Flat04Page(f.exact.url)
        restored.turns = ([(f.i["registration"].marker, "user")] if fault == "wrong-marker"
                          else f.exact.turns[:])
        f.context.pages.append(restored)
        if fault == "duplicate":
            f.context.pages.append(f.exact)
        elif fault == "unstable-owner":
            f.state.before_attach = lambda: f.s.new_logon(8)

    result = f.s.run()
    assert (result.status, result.code) == (status, code)
    assert chrome_record(f.s)["process"] != old["process"]
    assert f.origin()["navigation"] == old
    assert f.state.new_pages == 1 and not f.state.navigations


@pytest.mark.parametrize("fault,expected", [
    ("opt-in", "EXACT_CHAT_PRELOAD_OPT_IN_REQUIRED"), ("pause", "PAUSED_BY_HUMAN"),
    ("draft", "PRELOAD_DRAFT_PRESENT"), ("busy-or-login", "PRELOAD_SURFACE_UNPROVEN"),
    ("duplicate", "EXACT_CHAT_TAB_AMBIGUOUS"), ("contexts", "EXACT_CHAT_CONTEXT_AMBIGUOUS"),
    ("marker-missing", "LOCAL_USER_MARKER_MISSING"), ("url", "EXACT_CHAT_URL_DRIFT"),
    ("source", "MISSING_GRANT"),
])
def test_flat04_holds_before_preload_for_ineligible_source_or_surface(flat04, fault, expected):
    f = flat04
    f.enroll()
    if fault == "opt-in":
        f.i["config"]["chat_preload"] = False
        f.context.pages.remove(f.exact)
    elif fault == "pause":
        f.i["config"]["chrome_lifecycle"] = "paused"
        f.context.pages.remove(f.exact)
    elif fault == "draft":
        f.exact.surface = "DRAFT_PRESENT"
    elif fault == "busy-or-login":
        f.exact.surface = "SURFACE_UNPROVEN"
    elif fault == "duplicate":
        f.context.pages.append(Flat04Page(f.exact.url))
    elif fault == "contexts":
        f.browser.contexts.append(SimpleNamespace(pages=[]))
    elif fault == "marker-missing":
        f.exact.turns.clear()
    elif fault == "url":
        f.exact.url += "/"
    else:
        f.i["grants"]["grants"][0]["signature"] = "0" * 64
        f.i["save"](r.Path(f.i["config"]["grants"]), f.i["grants"])
    f.i["save"](f.i["config_path"], f.i["config"])
    assert f.s.run().code == expected
    assert f.state.new_pages == 0 and f.origin()["navigation"] is None


@pytest.mark.parametrize("fault,expected", [
    ("transient", "PRELOAD_CDP_UNAVAILABLE"), ("config", "LOCAL_AUTHORITY_DRIFT"),
    ("source", "LOCAL_AUTHORITY_DRIFT"), ("browser", "CHROME_OBSERVATION_CHANGED"),
])
def test_flat04_pre_effect_revalidate_and_retry_only_unattempted_preload(flat04, fault, expected):
    f = flat04
    f.enroll()
    f.context.pages.remove(f.exact)
    attach = f.state.attachments + 2

    def during_attach():
        if f.state.attachments != attach:
            return
        if fault == "transient":
            raise TimeoutError()
        if fault == "config":
            f.i["save"](f.i["config_path"], {**f.i["config"], "chrome_lifecycle": "paused"})
        elif fault == "source":
            f.i["save"](r.Path(f.sources.targets[0]["path"]), {})
        else:
            f.s.new_logon(8)

    f.state.before_attach = during_attach
    assert f.s.run().code == expected
    assert f.origin()["navigation"] is None and f.state.new_pages == 0
    if fault == "transient":
        assert f.s.run().code == "EXACT_CHAT_PRELOADED" and f.state.new_pages == 1


def test_flat04_source_admission_and_read_only_routes_survive_chrome_unavailability(flat04):
    f = flat04
    f.enroll()
    f.context.pages.remove(f.exact)
    f.s.set_owned(False)
    assert f.s.run(2).route.readiness == "LOCAL_ROUTE_REGISTERED"
    assert all(result.route.readiness == "LOCAL_ROUTE_REGISTERED" for result, _ in f.s.ticks)
    chrome_config = f.i["config"].pop("chrome_config")
    f.i["save"](f.i["config_path"], f.i["config"])
    assert f.s.run().route.readiness == "LOCAL_ROUTE_REGISTERED"
    f.i["config"]["chrome_config"] = chrome_config
    f.i["save"](f.i["config_path"], f.i["config"])
    f.s.set_owned(True)
    for index in (0, 1):
        result = r.run_read_only(f.i["config_path"], f.i["root"], target_index=index)
        assert result.code == "EXACT_CHAT_SUPERVISOR_REQUIRED" and result.replayed
    assert f.state.new_pages == 0
    assert f.s.run(2).code == "EXACT_CHAT_REUSED" and f.state.new_pages == 1


def test_flat04_issue_creator_drift_withholds_route_and_preload(flat04):
    f = flat04
    f.enroll()
    f.context.pages.remove(f.exact)
    f.sources.targets.reverse()
    f.i["save"](f.i["config_path"], f.i["config"])
    f.issue.issue["user"]["id"] += 1
    result = f.s.run()
    assert result.code == "ISSUE_CREATION_UNPROVEN" and not hasattr(result, "route")
    assert f.state.new_pages == 0
    with sqlite3.connect(f.s.path) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone() == (0,)


def test_flat04_legacy_unknown_row_is_preserved_when_issue_preloads(flat04):
    f = flat04
    f.enroll()
    event = r.Event(f.i["event"]["event_id"], f.i["registration"].source_sha, 1,
                    digest(f.i["event"]), "GITHUB_AUTHENTICATED")
    key = event.key(f.i["registration"])
    legacy = {"event": asdict(event), "status": "UNKNOWN_EFFECT", "code": "INTERRUPTED_NO_REPLAY",
              "history": ["PENDING", "RESERVED", "UNKNOWN_EFFECT"], "evidence": {}}
    runner = r.compose_read_only(f.i["config_path"], f.i["root"])
    with runner.journal.locked(f.i["registration"]) as (txn, data):
        data["events"][key] = legacy
        txn.write(data)
    f.context.pages.remove(f.exact)
    assert f.s.run(2).route.readiness == "EXACT_CHAT_PRELOADED"
    assert f.s.ticks[0][0].code == "INTERRUPTED_NO_REPLAY"
    assert f.state.new_pages == 1
    with sqlite3.connect(f.s.path) as db:
        assert db.execute("SELECT record FROM events WHERE event_key=?", (key,)).fetchone() == (
            json.dumps(legacy, sort_keys=True),)


def test_flat04_changed_route_cannot_rebind_existing_local_registration(flat04):
    f = flat04
    f.enroll()
    registered = f.i["registration"]
    changed = replace(registered, binding=replace(registered.binding, generation=registered.binding.generation + 1))
    f.i["config"].update(registration=asdict(changed), marker=changed.marker)
    f.i["save"](f.i["config_path"], f.i["config"])
    f.context.pages.remove(f.exact)
    assert f.s.run().code == "REGISTRATION_DRIFT"
    assert f.state.new_pages == 0 and f.origin()["phase"] == "ENROLLED"


def test_flat04_post_effect_config_drift_keeps_durable_fence(flat04):
    f = flat04
    f.enroll()
    f.context.pages.remove(f.exact)

    def pause_during_navigation():
        f.i["config"]["chrome_lifecycle"] = "paused"
        f.i["save"](f.i["config_path"], f.i["config"])

    f.state.on_goto = pause_during_navigation
    first = f.s.run()
    assert first.status == "UNKNOWN_EFFECT" and first.route.readiness == "LOCAL_ROUTE_REGISTERED"
    assert f.origin()["navigation"]["phase"] == "RESERVED"
    assert f.s.run().code == "EXACT_CHAT_RECONCILED"
    assert f.state.new_pages == 1


class SimulatedWakeHubCrash(BaseException):
    pass


@pytest.fixture
def flat05(flat04, r3a_ingress, monkeypatch):
    f = flat04
    prepared = f.enroll()
    event = f.i["event"]
    f.ef02 = r.SourceIdentity("ef02-completion", event["repository"], event["run_id"], event["run_attempt"])
    f.consent = {
        "schema": "WakeHubLocalUIDelivery.v3",
        "transport": "playwright",
        "route_marker": f.i["registration"].marker,
        "registration_id": prepared.registration_id,
        "marker_digest": f.origin()["marker_digest"],
        "grants": [{"source_key": key, "expires_ms": prepared.expires_ms}
                   for key in (f.ef02.key, f.issue.target.identity.key)],
    }
    f.i["config"]["ui_delivery"] = f.consent

    def save():
        f.i["save"](f.i["config_path"], f.i["config"])

    def rows():
        with sqlite3.connect(f.s.path) as db:
            return {event_id: json.loads(raw) for event_id, raw in db.execute("SELECT event_id, record FROM events")}

    f.save = save
    f.rows = rows
    # The default inventory sees an idle composer on a page with a closed account menu.
    # The fake browser provides neither an account identifier nor a session reference.
    f.ui = SimpleNamespace(clock=12345, target="a" * 32, context="b" * 32,
                           outbound={}, draft="", inserts=0, clicks=0, observations=0,
                           fail_at=None, on_stage={}, ref_mode=None, blocked=None)
    ui = f.ui
    adapter = r.cdp_doorbell

    def new_cdp_session(page):
        assert page is f.exact

        def send(method):
            assert method == "Target.getTargetInfo"
            return {"targetInfo": {"type": "page", "targetId": ui.target, "browserContextId": ui.context}}

        return SimpleNamespace(send=send, detach=lambda: None)

    def select(kind):
        f.sources.targets[:] = [target for target in f.sources.targets if target["kind"] == kind]
        save()
        return (event["event_id"] if kind == "ef02-completion" else
                "github-issue-created-v1:" + f.issue.target.identity.key)

    f.select = select

    def evaluate(script, args=None):
        if script == adapter._DOCUMENT_CLOCK:
            return ui.clock
        if script in (adapter._CHAT_A_READ_ONLY_SURFACE, adapter._WH03_COMMITTED_SIGNAL_DOM):
            return Flat04Page.evaluate(f.exact, script, args)
        assert args["url"] == f.exact.url and args["documentClock"] == ui.clock
        text = args["text"]
        if script == adapter._PRE_STAGE_REFS:
            return list(ui.outbound)
        if script == adapter._AGY_SURFACE_READY:
            return ui.blocked is None and not ui.draft
        if script == adapter._EXISTING_USER_TURN:
            ui.observations += 1
            assert any(row["status"] == "UNKNOWN_EFFECT" for row in rows().values())
            if "observe" in ui.on_stage:
                ui.on_stage["observe"]()
            refs = [ref for ref, content in ui.outbound.items() if content == text]
            if (len(refs) == 1 and refs[0] not in args["priorRefs"] and not ui.draft
                    and ui.blocked is None and f.exact.surface == "CANDIDATE_READY"):
                return refs[0]
            return False
        phase = {adapter._CHECK: "check", adapter._INSERT: "insert", adapter._READY: "ready",
                 adapter._CLICK: "click", adapter._PROVE: "prove"}[script]
        reserved = next(row for row in rows().values() if row["status"] == "RESERVED")
        reservation = reserved["evidence"]["local_send"]
        if phase == "check" and reservation.get("transport") == "agy":
            assert reservation["phase"] in ("PRE_STAGE", "STAGING")
        else:
            assert reservation["phase"] == ("PRE_STAGE" if phase == "check" else "STAGING")
        if phase in ui.on_stage:
            ui.on_stage[phase]()
        if phase == "check":
            if ui.fail_at == phase:
                raise SimulatedWakeHubCrash()
            if ui.blocked is not None:
                return ui.blocked
            return "OUTBOUND_ALREADY_PRESENT" if text in ui.outbound.values() else "READY"
        if phase == "insert":
            ui.inserts += 1
            ui.draft = text
            if ui.fail_at == phase:
                raise SimulatedWakeHubCrash()
            return True
        if phase == "ready":
            return ui.blocked is None and ui.draft == text
        if phase == "click":
            if ui.blocked is not None:
                return False
            assert ui.draft == text and text not in ui.outbound.values()
            prior = list(ui.outbound)
            ui.clicks += 1
            if ui.ref_mode != "missing":
                ui.outbound[f"turn-{ui.clicks}"] = text
            ui.draft = ""
            if ui.fail_at == phase:
                raise SimulatedWakeHubCrash()
            return prior
        if ui.fail_at == phase:
            raise SimulatedWakeHubCrash()
        if ui.ref_mode == "reused":
            return "turn-old"
        if ui.ref_mode == "boolean":
            return True
        return next((ref for ref, content in ui.outbound.items() if content == text), False)

    def wait(script, *, arg, timeout, polling):
        assert polling == 100
        if not evaluate(script, arg):
            raise TimeoutError()

    def bind_browser_primitives():
        f.context.new_cdp_session = new_cdp_session
        f.exact.evaluate = evaluate
        f.exact.wait_for_function = wait
        f.exact.set_default_timeout = lambda timeout: None

    def replace_process():
        process = f.i["process"]
        process["ProcessId"] += 1
        process["CreationDate"] = "2026-11-19T00:00:00.0000000Z"
        f.i["inventory"]["listeners"][0]["OwningProcess"] = process["ProcessId"]

    def reboot():
        f.s.new_logon(8)  # New Windows logon, same owner SID and dedicated profile.
        replace_process()
        page = Flat04Page(f.exact.url, f.exact.surface)
        page.turns[:] = f.exact.turns
        f.exact = page
        f.context = SimpleNamespace(pages=[f.unrelated, page])
        f.browser.contexts[:] = [f.context]
        ui.target = "c" * 32
        ui.context = "d" * 32
        ui.clock += 1
        bind_browser_primitives()

    f.replace_process = replace_process
    f.reboot = reboot
    bind_browser_primitives()

    def read_account(*args):
        pytest.fail("Local UI delivery requested account evidence")

    monkeypatch.setattr(_PlaywrightInventory, "read_enrolled_local_ui_account", read_account, raising=False)
    save()
    return f


def test_flat05_supervisor_sends_each_authenticated_source_once(flat05):
    f = flat05
    observed = "LOCAL_UI_OWNER_AUTHORIZED_TURN_OBSERVED"
    assert f.s.run(3).code == observed
    first, second, duplicate = (tick[0] for tick in f.s.ticks)
    assert first.code == second.code == observed and duplicate.replayed
    assert first.route.account_attestation == second.route.account_attestation == (
        "CHATGPT_ACCOUNT_SESSION_AUTHORITY_UNAVAILABLE")
    assert first.route.source_key == f.ef02.key
    assert second.route.source_key == f.issue.target.identity.key != first.route.source_key
    issue_id = "github-issue-created-v1:" + second.route.source_key
    event_ids = (f.i["event"]["event_id"], issue_id)
    for index, event_id in enumerate(event_ids, start=1):
        text = f.ui.outbound[f"turn-{index}"]
        assert text == r.cdp_doorbell._doorbell_text(event_id, "trung-via/ai-commerce", send_only=True)
        row = f.rows()[event_id]
        assert row["code"] == observed
        assert row["history"] == ["PENDING", "RESERVED", "UI_TURN_OBSERVED"]
        assert set(row["evidence"]) == {"local_route", "local_send", "marker", "user_turn_ref"}
        assert row["evidence"]["user_turn_ref"] == f"turn-{index}"
        assert "Discard this title" not in text and "@" not in text
    assert "attempt" not in f.rows()[issue_id]["event"]
    f.sources.targets.reverse()
    f.save()
    assert f.s.run(2).replayed
    assert f.ui.inserts == f.ui.clicks == 2 and f.unrelated.reads == f.state.new_pages == 0


@pytest.fixture
def dual_route(flat05, monkeypatch):
    f = flat05
    del f.consent["transport"]  # Intentional v3 opt-in selects AGY by default.
    f.consent["agy"] = {
        "executable": str(f.i["config_path"].with_name("agy.exe")), "executable_sha256": "a" * 64,
        "workspace": str(f.i["config_path"].parent), "skill_sha256": "b" * 64,
    }
    f.agy = SimpleNamespace(calls=[], effect=lambda request: "AGY_REPORTED_SEND")

    def launcher(runtime, config, request, marker_digest, surface, expires_ms, stop, revalidate):
        row = f.rows()[request.event_id]
        reservation = row["evidence"]["local_send"]
        assert row["status"] == "RESERVED" and reservation["phase"] == "STAGING"
        assert reservation["transport"] == "agy" and reservation["surface"]["document"] == surface.document
        assert reservation["doorbell_sha256"] == hashlib.sha256(request.canonical_bytes).hexdigest()
        assert marker_digest == f.consent["marker_digest"] and not stop()
        revalidate()
        f.agy.calls.append(request)
        return r._AgyOutcome(f.agy.effect(request), 37)

    monkeypatch.setattr(r, "_launch_agy", launcher)
    f.save()
    return f


def test_dual_route_default_uses_fixed_doorbell_and_independent_witness(dual_route):
    f = dual_route
    event_id = f.select("github-issue-created")

    def sent(request):
        text = request.canonical_bytes.decode()
        assert request.event_id == event_id and "Discard this title" not in text
        f.ui.outbound["turn-agy"] = text
        return "AGY_REPORTED_SEND"

    f.agy.effect = sent
    assert f.s.run().code == "LOCAL_UI_EXISTING_TURN_RECONCILED"
    row = f.rows()[event_id]
    assert row["evidence"]["user_turn_ref"] == "turn-agy"
    assert row["evidence"]["local_send"]["agy_attempt"] == {"code": "AGY_REPORTED_SEND", "elapsed_ms": 37}
    del f.consent["agy"]
    f.consent["transport"] = "playwright"
    f.save()
    assert f.s.run().replayed and f.rows()[event_id] == row
    assert len(f.agy.calls) == f.ui.observations == 1 and f.ui.inserts == f.ui.clicks == 0


@pytest.mark.parametrize("code", ["AGY_LAUNCH_NOT_ATTEMPTED", "AGY_PREINVOCATION_QUOTA"])
@pytest.mark.parametrize("ready", [True, False])
def test_dual_route_fallback_requires_trusted_no_invocation_and_ready_standby(dual_route, code, ready):
    f = dual_route
    event_id = f.select("ef02-completion")

    def unattempted(_):
        if not ready:
            f.ui.blocked = "DRAFT_PRESENT"
        return code

    f.agy.effect = unattempted
    result = f.s.run()
    row = f.rows()[event_id]
    assert len(f.agy.calls) == 1
    if ready:
        assert result.status == "UI_TURN_OBSERVED" and f.ui.inserts == f.ui.clicks == 1
        reservation = row["evidence"]["local_send"]
        assert reservation["transport"] == "playwright" and reservation["agy_attempt"]["code"] == code
        assert f.s.run().replayed and len(f.agy.calls) == f.ui.clicks == 1
    else:
        assert result.code == "DRAFT_PRESENT" and row["history"] == ["PENDING"]
        assert f.ui.inserts == f.ui.clicks == 0


@pytest.mark.parametrize("reported", ["AGY_REPORTED_SEND", "AGY_REPORTED_HOLD", "AGY_TIMEOUT", "malformed", "crash"])
def test_dual_route_unknown_survives_restart_and_transport_change(dual_route, reported):
    f = dual_route
    event_id = f.select("ef02-completion")

    def ambiguous(request):
        if reported == "crash":
            raise SimulatedWakeHubCrash()
        if reported == "malformed":
            return r._agy_report(0, b'{"status":"SUCCESS","response":"sent"}', b"", request.event_key)
        return reported

    f.agy.effect = ambiguous
    if reported == "crash":
        with pytest.raises(SimulatedWakeHubCrash):
            f.s.run()
        assert f.rows()[event_id]["status"] == "RESERVED"
    else:
        assert f.s.run().status == "UNKNOWN_EFFECT"
    del f.consent["agy"]
    f.consent["transport"] = "playwright"
    f.save()
    resumed = f.s.run()
    assert resumed.status == "UNKNOWN_EFFECT" and resumed.replayed
    if reported == "malformed":
        assert resumed.code == "AGY_RECEIPT_UNPROVEN"
    assert len(f.agy.calls) == 1 and f.ui.inserts == f.ui.clicks == 0


def test_dual_route_unknown_reconciles_only_with_independent_user_turn(dual_route):
    f = dual_route
    event_id = f.select("ef02-completion")
    f.agy.effect = lambda request: "AGY_TIMEOUT"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    assert f.s.run().code == "AGY_TIMEOUT"
    f.ui.outbound["turn-agy"] = f.agy.calls[0].canonical_bytes.decode()
    assert f.s.run().code == "LOCAL_UI_EXISTING_TURN_RECONCILED"
    assert f.rows()[event_id]["evidence"]["local_unknown_code"] == "AGY_TIMEOUT"
    assert len(f.agy.calls) == 1 and f.ui.clicks == 0


@pytest.mark.parametrize("output, expected", [
    (b"quota exhausted", "AGY_RECEIPT_UNPROVEN"),
    (b"x" * (r._AGY_OUTPUT_LIMIT + 1), "AGY_OUTPUT_LIMIT"),
    (None, "AGY_REPORTED_SEND"),
])
def test_dual_route_powershell_launch_uses_fixed_argv_and_bounded_receipt(flat05, monkeypatch, output, expected):
    from ai_commerce.remote import worker

    f = flat05
    runtime = r._read_runtime_config(f.i["config_path"], f.i["root"])
    issue = f.issue.target
    event = r.SourceEvent(issue.identity, digest({"identity": asdict(issue.identity), "creation": asdict(issue.creation)}),
                          issue)
    request = r.DoorbellRequest.for_source(event, runtime.registration)
    workspace = f.i["config_path"].parent
    executable = workspace / "agy.exe"
    grant = f.consent["grants"][1]
    surface = r.cdp_doorbell.LocalUISurface("d" * 64, 12345, ())
    effects = []

    def popen(argv, *, cwd, env, shell, stdin, **kwargs):
        effects.append("launch")
        assert argv[1:] == ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", r._AGY_POWERSHELL]
        assert "--model gemini-4-hydrogen-low --effort low" in argv[-1]
        assert request.binding.chat_url not in argv[-1]
        assert cwd == workspace and shell is False and stdin == subprocess.DEVNULL
        assert env["AI_COMMERCE_WAKE_AGY_EXE"] == str(executable) and "GH_TOKEN" not in env
        header, body = env["AI_COMMERCE_WAKE_AGY_PROMPT"].split("\n", 1)
        assert header.startswith("/wakehub-executor ")
        assert json.loads(body)["message"].encode() == request.canonical_bytes
        report = json.dumps({"conversation_id": "fake-conversation", "status": "SUCCESS", "response": json.dumps({
            "event_key": request.event_key, "outcome": "SEND_ATTEMPTED",
        })}).encode()
        return SimpleNamespace(stdout=io.BytesIO(report if output is None else output), stderr=io.BytesIO(),
                               poll=lambda: 0, returncode=0)

    monkeypatch.setenv("GH_TOKEN", "synthetic-token-not-for-agy")
    monkeypatch.setattr(r, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE,
                                                        DEVNULL=subprocess.DEVNULL))
    monkeypatch.setattr(r, "_agy_local_paths", lambda *args: (executable, workspace))
    monkeypatch.setattr(worker, "_windows_diagnostic_job", lambda: SimpleNamespace(
        attach=lambda child: effects.append("attach"), close=lambda: effects.append("close")))
    result = r._launch_agy(runtime, {}, request, f.consent["marker_digest"], surface,
                           grant["expires_ms"], lambda: False, lambda: effects.append("validate"))
    assert result.code == expected and result.elapsed_ms >= 0
    assert effects == ["validate", "launch", "attach", "close"]


@pytest.mark.parametrize("fault, expected", [
    ("consent-route", "LOCAL_UI_ENROLLMENT_DRIFT"),
    ("wrong-grant", "LOCAL_UI_DELIVERY_GRANT_REQUIRED"),
    ("verification", "SURFACE_UNPROVEN"),
    ("busy", "GENERATION_ACTIVE"),
    ("draft", "DRAFT_PRESENT"),
    ("permission", "AGY_PERMISSION_REQUIRED"),
])
def test_dual_route_shared_holds_prevent_send(dual_route, fault, expected):
    f = dual_route
    event_id = f.select("ef02-completion")
    if fault == "consent-route":
        f.consent["route_marker"] = "wakehub-r1:" + "a" * 64
    elif fault == "wrong-grant":
        f.consent["grants"].pop(0)
    elif fault == "permission":
        f.agy.effect = lambda request: "AGY_PERMISSION_REQUIRED"
    else:
        f.ui.blocked = expected
    f.save()
    assert f.s.run().code == expected
    assert f.rows()[event_id]["history"] == ["PENDING"]
    assert len(f.agy.calls) == int(fault == "permission") and f.ui.inserts == f.ui.clicks == 0


def test_dual_route_legacy_grant_is_read_only_and_old_row_can_reconcile(flat05):
    f = flat05
    event_id = f.select("ef02-completion")
    legacy = {key: value for key, value in f.consent.items() if key != "transport"}
    legacy["schema"] = "WakeHubLocalUIDelivery.v2"
    f.i["config"]["ui_delivery"] = legacy
    f.save()
    assert f.s.run().code == "EXACT_CHAT_REUSED"
    assert f.rows()[event_id]["history"] == ["PENDING"] and f.ui.clicks == 0

    f.i["config"]["ui_delivery"] = f.consent
    f.save()
    f.ui.ref_mode = "boolean"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    runner = r.compose_read_only(f.i["config_path"], f.i["root"])
    with runner.journal.locked(runner.registration) as (txn, data):
        row, = data["events"].values()
        reservation = row["evidence"]["local_send"]
        del reservation["transport"]
        reservation["consent_sha256"] = runner._ui_consent_digest(legacy, legacy["grants"][0])
        txn.write(data)
    assert f.s.run().code == "LOCAL_UI_EXISTING_TURN_RECONCILED"
    assert f.ui.clicks == f.ui.observations == 1


def test_dual_route_journal_rejects_agy_staging_without_surface(dual_route):
    f = dual_route
    f.select("ef02-completion")
    f.agy.effect = lambda request: "AGY_TIMEOUT"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    before = f.rows()
    runner = r.compose_read_only(f.i["config_path"], f.i["root"])
    with runner.journal.locked(runner.registration) as (txn, data):
        row, = data["events"].values()
        del row["evidence"]["local_send"]["surface"]
        with pytest.raises(r.RuntimeHold, match="LOCAL_UI_RESERVATION_INVALID"):
            txn.write(data)
    assert f.rows() == before


@pytest.mark.parametrize("schema", ["WakeHubLocalUIDelivery.v1", "WakeHubLocalUIDelivery.v99"])
def test_flat05_supervisor_rejects_old_or_unrecognized_opt_in(flat05, schema):
    f = flat05
    f.consent["schema"] = schema
    if schema.endswith(".v1"):
        f.consent.update(account_sha256="a" * 64, session_sha256=None)
    f.save()
    assert f.s.run().code == "LOCAL_UI_DELIVERY_CONFIG_INVALID"
    assert f.rows() == {} and f.ui.inserts == f.ui.clicks == 0


@pytest.mark.parametrize("fault,expected", [
    ("disabled", "EXACT_CHAT_REUSED"),
    ("wrong-grant", "LOCAL_UI_DELIVERY_GRANT_REQUIRED"),
    ("expired", "LOCAL_UI_DELIVERY_GRANT_EXPIRED"),
    ("signed-grant", "DELIVER_WAKE_GRANT_REQUIRED"),
    ("registration", "LOCAL_UI_ENROLLMENT_DRIFT"),
    ("consent-route", "LOCAL_UI_ENROLLMENT_DRIFT"),
    ("consent-marker", "LOCAL_UI_ENROLLMENT_DRIFT"),
    ("profile", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("marker", "LOCAL_USER_MARKER_MISSING"),
    ("draft", "PRELOAD_DRAFT_PRESENT"),
    ("busy-or-login", "PRELOAD_SURFACE_UNPROVEN"),
    ("verification-or-confirmation", "SURFACE_UNPROVEN"),
    ("disabled-composer", "SURFACE_UNPROVEN"),
    ("attachment", "DRAFT_PRESENT"),
    ("tab", "EXACT_CHAT_TAB_AMBIGUOUS"),
    ("context", "EXACT_CHAT_CONTEXT_AMBIGUOUS"),
    ("existing", "OUTBOUND_ALREADY_PRESENT"),
    ("config-after-reservation", "LOCAL_AUTHORITY_DRIFT"),
    ("source-after-reservation", "LOCAL_AUTHORITY_DRIFT"),
    ("document-after-reservation", "TARGET_PAGE_CHANGED"),
    ("route-after-reservation", "TARGET_PAGE_CHANGED"),
    ("stale-launch", "CHROME_LAUNCH_RECONCILE_REQUIRED"),
])
def test_flat05_determinate_holds_leave_send_unattempted(flat05, fault, expected):
    f = flat05
    event_id = f.i["event"]["event_id"]
    if fault == "disabled":
        del f.i["config"]["ui_delivery"]
    elif fault == "wrong-grant":
        f.consent["grants"].pop(0)
    elif fault == "expired":
        f.consent["grants"][0]["expires_ms"] = 1
    elif fault == "signed-grant":
        f.i["grants"]["grants"].pop()
        f.i["save"](r.Path(f.i["config"]["grants"]), f.i["grants"])
    elif fault == "registration":
        f.consent["registration_id"] = "a" * 32
    elif fault == "consent-route":
        f.consent["route_marker"] = "wakehub-r1:" + "a" * 64
    elif fault == "consent-marker":
        f.consent["marker_digest"] = "a" * 64
    elif fault == "profile":
        argv = f.i["environment"].command()
        argv[2] = "--profile-directory=Other"
        f.i["process"]["CommandLine"] = json.dumps(argv)
    elif fault == "marker":
        f.exact.turns.clear()
    elif fault in ("draft", "busy-or-login"):
        f.exact.surface = "DRAFT_PRESENT" if fault == "draft" else "SURFACE_UNPROVEN"
    elif fault in ("verification-or-confirmation", "disabled-composer", "attachment"):
        f.ui.blocked = "DRAFT_PRESENT" if fault == "attachment" else "SURFACE_UNPROVEN"
    elif fault == "tab":
        f.context.pages.append(Flat04Page(f.exact.url))
    elif fault == "context":
        f.browser.contexts.append(SimpleNamespace(pages=[]))
    elif fault == "existing":
        f.ui.outbound["turn-old"] = r.cdp_doorbell._doorbell_text(event_id, "trung-via/ai-commerce", send_only=True)
    elif fault == "config-after-reservation":
        def pause():
            f.i["config"]["chrome_lifecycle"] = "paused"
            f.save()
        f.ui.on_stage["check"] = pause
    elif fault == "source-after-reservation":
        path = r.Path(f.sources.targets[0]["path"])
        f.ui.on_stage["check"] = lambda: f.i["save"](path, {**f.i["registered"], "run_id": 18})
    elif fault == "document-after-reservation":
        f.ui.on_stage["check"] = lambda: setattr(f.ui, "clock", f.ui.clock + 1)
    elif fault == "route-after-reservation":
        f.ui.on_stage["check"] = lambda: setattr(f.exact, "url", f.unrelated.url)
    elif fault == "stale-launch":
        f.s.slot.pending = True
    f.save()
    assert f.s.run().code == expected
    assert f.rows()[event_id]["history"] == ["PENDING"]
    assert f.ui.inserts == f.ui.clicks == 0


@pytest.mark.parametrize("kind", ["ef02-completion", "github-issue-created"])
@pytest.mark.parametrize("phase", ["check", "insert", "click", "prove", "after-observation"])
def test_flat06_restart_only_retries_unstaged_reservation(flat05, monkeypatch, kind, phase):
    f = flat05
    event_id = f.select(kind)
    f.ui.fail_at = phase
    with monkeypatch.context() as patch:
        if phase == "after-observation":
            write = r._SQLiteTransaction.write

            def crash_before_proof_commit(txn, data):
                if any(row["status"] == "UI_TURN_OBSERVED" for row in data["events"].values()):
                    assert f.ui.outbound and f.ui.clicks == 1
                    raise SimulatedWakeHubCrash()
                write(txn, data)

            patch.setattr(r._SQLiteTransaction, "write", crash_before_proof_commit)
        with pytest.raises(SimulatedWakeHubCrash):
            f.s.run()
    reservation = f.rows()[event_id]["evidence"]["local_send"]
    assert reservation["phase"] == ("PRE_STAGE" if phase == "check" else "STAGING")
    f.ui.fail_at = None
    resumed = f.s.run()
    if phase == "check":
        assert resumed.code == "LOCAL_UI_OWNER_AUTHORIZED_TURN_OBSERVED" and f.ui.clicks == 1
        assert f.ui.observations == 0
    elif phase == "insert":
        assert (resumed.status, resumed.code) == ("UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY")
        assert f.rows()[event_id]["history"] == ["PENDING", "RESERVED", "UNKNOWN_EFFECT"]
        assert f.ui.inserts == 1 and f.ui.clicks == 0
    else:
        assert (resumed.status, resumed.code, resumed.replayed) == (
            "UI_TURN_OBSERVED", "LOCAL_UI_EXISTING_TURN_RECONCILED", True)
        row = f.rows()[event_id]
        assert row["history"] == ["PENDING", "RESERVED", "UNKNOWN_EFFECT", "UI_TURN_OBSERVED"]
        assert row["evidence"]["local_unknown_code"] == "INTERRUPTED_NO_REPLAY"
        assert row["evidence"]["user_turn_ref"] == "turn-1" and row["evidence"]["local_send"] == reservation
        assert f.s.run() == resumed and f.rows()[event_id] == row
        assert f.ui.inserts == f.ui.clicks == f.ui.observations == 1
        assert f.unrelated.reads == f.state.new_pages == 0 and not f.state.navigations


@pytest.mark.parametrize("kind", ["ef02-completion", "github-issue-created"])
@pytest.mark.parametrize("transition", ["reboot", "reload", "reboot-without-turn"])
def test_flat06_reconciles_current_document_without_resending(flat05, kind, transition):
    f = flat05
    event_id = f.select(kind)
    f.ui.ref_mode = "boolean"
    original = f.s.run()
    assert (original.status, original.code) == ("UNKNOWN_EFFECT", "SUBMISSION_UNPROVEN")
    before = f.rows()[event_id]
    reservation = before["evidence"]["local_send"]
    old_context, old_page = f.context, f.exact
    old_clock, old_context_id, old_target = f.ui.clock, f.ui.context, f.ui.target
    if transition == "reload":
        f.ui.clock += 1
    else:
        f.reboot()
        assert f.context is not old_context and f.exact is not old_page
        assert f.ui.context != old_context_id and f.ui.target != old_target
    assert f.ui.clock != old_clock
    if transition == "reboot-without-turn":
        f.ui.outbound.clear()

    resumed = f.s.run()
    assert (chrome_record(f.s)["process"] == reservation["surface"]["process"]) == (transition == "reload")
    assert f.ui.observations == f.ui.inserts == f.ui.clicks == 1
    assert f.unrelated.reads == f.state.new_pages == 0 and not f.state.navigations and not f.s.requests
    row = f.rows()[event_id]
    if transition == "reboot-without-turn":
        assert resumed == replace(original, replayed=True) and row == before
        return
    assert (resumed.status, resumed.code, resumed.replayed) == (
        "UI_TURN_OBSERVED", "LOCAL_UI_EXISTING_TURN_RECONCILED", True)
    assert row["history"] == [*before["history"], "UI_TURN_OBSERVED"]
    assert row["evidence"]["local_unknown_code"] == original.code
    assert row["evidence"]["local_send"] == reservation and row["evidence"]["user_turn_ref"] == "turn-1"


@pytest.mark.parametrize("kind", ["ef02-completion", "github-issue-created"])
@pytest.mark.parametrize("fault", [
    "absent", "duplicate", "wrong-text", "prior-ref", "marker", "owner", "profile", "route",
    "context", "context-during-read", "context-id-during-read", "target-during-read", "page-during-read",
    "document-during-read", "process-during-read", "revoked", "expired", "expiry-during-read", "changed-grant",
    "changed-registration", "paused", "pause-during-read", "stop-during-read", "draft", "login", "verification", "modal",
    "unproven-history",
])
def test_flat06_inconclusive_observation_preserves_unknown(flat05, monkeypatch, kind, fault):
    f = flat05
    event_id = f.select(kind)
    f.ui.outbound["turn-old"] = "An unrelated earlier turn"
    f.ui.ref_mode = "boolean"
    original = f.s.run()
    assert (original.status, original.code) == ("UNKNOWN_EFFECT", "SUBMISSION_UNPROVEN")
    text = f.ui.outbound["turn-1"]
    grant = f.consent["grants"][0 if kind == "ef02-completion" else 1]
    if fault == "absent":
        del f.ui.outbound["turn-1"]
    elif fault == "duplicate":
        f.ui.outbound["turn-duplicate"] = text
    elif fault == "wrong-text":
        f.ui.outbound["turn-1"] += "\nextra"
    elif fault == "prior-ref":
        f.ui.outbound["turn-old"] = f.ui.outbound.pop("turn-1")
    elif fault == "marker":
        f.exact.turns.clear()
    elif fault == "owner":
        owner = f.s.owner()
        monkeypatch.setattr(r.chrome_acquisition, "_enrollment_operator", lambda *, attended=True: owner)
        f.i["process"]["OwnerSid"] = "S-1-5-21-111-222-333-1002"
    elif fault == "profile":
        argv = f.i["environment"].command()
        argv[2] = "--profile-directory=Other"
        f.i["process"]["CommandLine"] = json.dumps(argv)
    elif fault == "route":
        f.exact.url = f.unrelated.url
    elif fault == "context":
        f.browser.contexts.append(SimpleNamespace(pages=[]))
    elif fault == "context-during-read":
        def replace_context():
            f.browser.contexts[:] = [SimpleNamespace(pages=f.context.pages)]
        f.ui.on_stage["observe"] = replace_context
    elif fault == "context-id-during-read":
        f.ui.on_stage["observe"] = lambda: setattr(f.ui, "context", "c" * 32)
    elif fault == "target-during-read":
        f.ui.on_stage["observe"] = lambda: setattr(f.ui, "target", "c" * 32)
    elif fault == "page-during-read":
        def replace_page():
            f.context.pages[-1] = Flat04Page(f.exact.url)
        f.ui.on_stage["observe"] = replace_page
    elif fault == "document-during-read":
        f.ui.on_stage["observe"] = lambda: setattr(f.ui, "clock", f.ui.clock + 1)
    elif fault == "process-during-read":
        f.ui.on_stage["observe"] = f.replace_process
    elif fault == "revoked":
        f.consent["grants"].remove(grant)
    elif fault == "expired":
        monkeypatch.setattr(r, "time", SimpleNamespace(time_ns=lambda: grant["expires_ms"] * 1_000_000))
    elif fault == "expiry-during-read":
        def expire():
            monkeypatch.setattr(r, "time", SimpleNamespace(time_ns=lambda: grant["expires_ms"] * 1_000_000))
        f.ui.on_stage["observe"] = expire
    elif fault == "changed-grant":
        grant["expires_ms"] += 1
    elif fault == "changed-registration":
        f.consent["registration_id"] = "a" * 32
    elif fault == "paused":
        f.i["config"]["chrome_lifecycle"] = "paused"
    elif fault == "pause-during-read":
        def pause():
            f.i["config"]["chrome_lifecycle"] = "paused"
            f.save()
        f.ui.on_stage["observe"] = pause
    elif fault == "stop-during-read":
        f.ui.on_stage["observe"] = lambda: setattr(f.s.stops[-1], "stopped", True)
    elif fault == "draft":
        f.ui.draft = text
    elif fault in ("login", "verification", "modal"):
        f.ui.blocked = fault
    elif fault == "unproven-history":
        runner = r.compose_read_only(f.i["config_path"], f.i["root"])
        with runner.journal.locked(runner.registration) as (txn, data):
            del next(iter(data["events"].values()))["evidence"]["local_send"]["surface"]
            txn.write(data)
    f.save()
    before = f.rows()
    resumed = f.s.run()
    if fault == "stop-during-read":
        assert resumed.code == "SUPERVISOR_STOPPED"
        resumed = f.s.ticks[-1][0]
    expected = {
        "revoked": ("HOLD", "LOCAL_UI_DELIVERY_GRANT_REQUIRED"),
        "expired": ("HOLD", "LOCAL_UI_DELIVERY_GRANT_EXPIRED"),
        "expiry-during-read": ("HOLD", "LOCAL_UI_DELIVERY_GRANT_EXPIRED"),
    }.get(fault, (original.status, original.code))
    assert (resumed.status, resumed.code) == expected and resumed.replayed
    assert f.rows() == before and event_id in before
    assert f.ui.inserts == f.ui.clicks == 1
    assert f.ui.observations <= 1 and f.unrelated.reads == f.state.new_pages == 0
    if fault.endswith("-during-read"):
        assert f.ui.observations == 1


@pytest.mark.parametrize("operation", ["deliver_wake", "resolve_claim_route"])
def test_flat06_observation_requires_current_route_permission(flat05, operation):
    f = flat05
    event_id = f.select("ef02-completion")
    f.ui.ref_mode = "boolean"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    before = f.rows()[event_id]
    grants = f.i["grants"]
    grants["grants"][:] = [grant for grant in grants["grants"] if grant["operation"] != operation]
    f.i["save"](r.Path(f.i["config"]["grants"]), grants)
    result = f.s.run()
    if operation == "deliver_wake":
        assert result.code == "LOCAL_UI_EXISTING_TURN_RECONCILED"
    else:
        assert (result.status, result.code) == ("HOLD", "MISSING_GRANT")
        assert f.rows()[event_id] == before
        assert f.ui.observations == 0
    assert f.ui.inserts == f.ui.clicks == 1


@pytest.mark.parametrize("kind,expected", [
    ("ef02-completion", "RUN_IDENTITY_MISMATCH"),
    ("github-issue-created", "ISSUE_IDENTITY_UNPROVEN"),
])
def test_flat06_observation_requires_original_authenticated_source(flat05, kind, expected):
    f = flat05
    event_id = f.select(kind)
    f.ui.ref_mode = "boolean"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    before = f.rows()[event_id]
    if kind == "ef02-completion":
        f.i["run"]["run_attempt"] += 1
    else:
        f.issue.issue["id"] += 1
    assert f.s.run().code == expected
    assert f.rows()[event_id] == before and f.ui.observations == 0
    assert f.ui.inserts == f.ui.clicks == 1


def test_flat06_reconciliation_rejects_reference_owned_by_another_source(flat05):
    f = flat05
    assert f.s.run().code == "LOCAL_UI_OWNER_AUTHORIZED_TURN_OBSERVED"
    f.ui.outbound.clear()
    event_id = f.select("github-issue-created")
    f.ui.ref_mode = "boolean"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    f.ui.outbound["turn-1"] = f.ui.outbound.pop("turn-2")
    before = f.rows()
    assert "turn-1" not in before[event_id]["evidence"]["local_send"]["surface"]["prior_refs"]
    assert f.s.run().status == "UNKNOWN_EFFECT"
    assert f.rows() == before and f.ui.observations == 1
    assert f.ui.inserts == f.ui.clicks == 2


def test_flat06_full_journal_preserves_prior_dedup_and_unknown(flat05):
    f = flat05
    assert f.s.run().code == "LOCAL_UI_OWNER_AUTHORIZED_TURN_OBSERVED"
    ef02, issue = f.sources.targets
    f.sources.targets[:] = [issue, ef02]
    f.save()
    f.ui.ref_mode = "missing"
    assert f.s.run().status == "UNKNOWN_EFFECT"
    runner = r.compose_read_only(f.i["config_path"], f.i["root"])
    registration = runner.registration
    with runner.journal.locked(registration) as (txn, data):
        legacy = next(row for row in data["events"].values() if row["status"] == "UI_TURN_OBSERVED")
        del legacy["evidence"]["local_send"]["surface"]
        for index in range(2, 128):
            event = r.Event(f"ef02-completion-v1:{index:064x}", registration.source_sha, index,
                            "d" * 64, "GITHUB_AUTHENTICATED")
            data["events"][event.key(registration)] = {
                "event": asdict(event), "status": "PENDING", "code": "EVENT_PENDING",
                "history": ["PENDING"], "evidence": {},
            }
        txn.write(data)

    def raw_rows():
        with sqlite3.connect(f.s.path) as db:
            return tuple(db.execute("SELECT * FROM events ORDER BY event_key")), tuple(db.execute(
                "SELECT * FROM source_identities ORDER BY source_key"))

    before = raw_rows()
    assert len(before[0]) == 128 and len(before[1]) == 2
    f.sources.add(1318)
    new = f.sources.targets[-1]
    f.sources.targets[:] = [new, ef02, issue]
    f.save()
    assert f.s.run().code == "JOURNAL_FULL"
    f.sources.targets[:] = [ef02, issue]
    f.save()
    assert f.s.run().code == "LOCAL_UI_OWNER_AUTHORIZED_TURN_OBSERVED"
    f.sources.targets.reverse()
    f.save()
    assert f.s.run().status == "UNKNOWN_EFFECT"
    assert raw_rows() == before and f.ui.inserts == f.ui.clicks == 2


@pytest.mark.parametrize("fault,expected,clicks", [
    ("blocked-send", "INSERT_BLOCKED", 0),
    ("prompt-before-click", "SEND_BLOCKED", 0),
    ("owner", "CHROME_OBSERVATION_CHANGED", 0),
    ("document", "TARGET_PAGE_CHANGED", 0),
    ("marker", "LOCAL_UI_MARKER_UNPROVEN", 0),
    ("missing", "SUBMISSION_UNPROVEN", 1),
    ("reused", "SUBMISSION_UNPROVEN", 1),
    ("boolean", "SUBMISSION_UNPROVEN", 1),
    ("config-after-click", "LOCAL_AUTHORITY_DRIFT", 1),
])
def test_flat05_staging_or_click_ambiguity_cannot_replay(flat05, fault, expected, clicks):
    f = flat05
    f.sources.targets.pop()
    f.save()
    if fault == "blocked-send":
        f.ui.on_stage["insert"] = lambda: setattr(f.ui, "blocked", "SURFACE_UNPROVEN")
    elif fault == "prompt-before-click":
        f.ui.on_stage["click"] = lambda: setattr(f.ui, "blocked", "SURFACE_UNPROVEN")
    elif fault == "owner":
        f.ui.on_stage["insert"] = lambda: f.s.new_logon(8)
    elif fault == "document":
        # Drift on the final _READY call, before the preceding identity is used to click.
        def reload_after_ready():
            f.ui.on_stage["ready"] = lambda: setattr(f.ui, "clock", f.ui.clock + 1)
        f.ui.on_stage["ready"] = reload_after_ready
    elif fault == "marker":
        f.ui.on_stage["insert"] = f.exact.turns.clear
    elif fault == "config-after-click":
        def pause():
            f.i["config"]["chrome_lifecycle"] = "paused"
            f.save()
        f.ui.on_stage["click"] = pause
    else:
        f.ui.ref_mode = fault
        f.ui.outbound["turn-old"] = "A different message"
    first = f.s.run()
    assert (first.status, first.code) == ("UNKNOWN_EFFECT", expected)
    assert f.s.run().replayed and f.ui.inserts == 1 and f.ui.clicks == clicks


def test_flat05_other_modes_and_legacy_unknown_do_not_send(flat05):
    f = flat05
    config, root, registration = f.i["config_path"], f.i["root"], f.i["registration"]
    assert r.run_read_only(config, root).code == "EXACT_CHAT_SUPERVISOR_REQUIRED"
    f.context.pages.remove(f.unrelated)
    assert r.run_native_source_only(config, root).status == "HOLD_DEPENDENCY"
    event_id = f.i["event"]["event_id"]
    event = r.Event(event_id, registration.source_sha, 1, digest(f.i["event"]), "GITHUB_AUTHENTICATED")
    legacy = {"event": asdict(event), "status": "UNKNOWN_EFFECT", "code": "INTERRUPTED_NO_REPLAY",
              "history": ["PENDING", "RESERVED", "UNKNOWN_EFFECT"], "evidence": {}}
    journal = r.compose_read_only(config, root).journal
    with journal.locked(registration) as (txn, data):
        data["events"][event.key(registration)] = legacy
        txn.write(data)
    assert f.s.run().code == legacy["code"] and f.rows()[event_id] == legacy
    f.sources.targets.reverse()
    f.save()
    assert f.s.run().code == "LOCAL_UI_OWNER_AUTHORIZED_TURN_OBSERVED"
    assert f.rows()[event_id] == legacy and f.ui.clicks == 1
