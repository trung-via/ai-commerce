"""Offline G3 owner tests: real scratch signatures/SQLite, synthetic OS seams.

No service, real signer enrollment, ACL edit, listener, task, browser or Send.
All artifacts are under pytest's external basetemp. G2 fixtures are reused as
diagnostic machinery, never as evidence that a native installation exists.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
import ctypes
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_commerce.hostops_v3.contract import Denied
from ai_commerce.wakehub import g3_enrollment as en
from ai_commerce.wakehub import g3_desktop_observer as ob
from ai_commerce.wakehub import g3_service_host as host
from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_broker as b
from ai_commerce.wakehub import native_inventory as inv
from ai_commerce.wakehub import native_journal as jn
from ai_commerce.wakehub import native_runtime as rt
from ai_commerce.wakehub import private_operator as op

from test_wakehub_g2_native import crypto, storage, enrolled, SOURCE, TARGET, T, JID, SID, OBSERVER, grant, START, original_outcome
from test_wakehub_g2_broker import broker, signed_read


_SERVICE_INITIALIZE = b.WindowsServiceAuthority.__init__


@pytest.fixture
def pins():
    return en.EnrollmentPins(SOURCE, 1, SID, OBSERVER, (SID,), "1" * 64, "2" * 64,
                             "3" * 64, "4" * 64, "5" * 64, "6" * 64, JID)


def absent(pins):
    return tuple(en.Readback(r.name, "ABSENT") for r in en.desired_resources(pins))


def present(pins):
    return tuple(en.Readback(r.name, "PRESENT", r.value) for r in en.desired_resources(pins))


def test_fixed_plan_is_idempotent_and_never_installs(pins, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("installer invoked"))
    first = en.attended_plan(pins, absent(pins))
    assert first == en.attended_plan(pins, absent(pins), previous_digest=first.digest)
    assert first.state == "READY_FOR_ATTENDED_REVIEW" and first.steps
    assert set(first.native_state) == set(en.NOT_ENROLLED)
    service = jn.decode(next(r.value for r in first.resources if r.name == "service"))
    assert service["image"] == en.SERVICE_IMAGE and service["arguments"] == []
    assert service["interactive"] is False and service["start"] == "DEMAND"
    assert service["account"] == "NT SERVICE\\" + b.SERVICE
    matched = en.postflight(first, pins, present(pins))
    assert matched.state == "READBACK_MATCH_SOURCE_ONLY" and not matched.steps
    assert en.attended_plan(pins, present(pins)) == matched
    assert en.postflight(first, pins, absent(pins)).state == "HOLD_RECONCILE_ONLY"


@pytest.mark.parametrize("name", ["release", "service", "anchors", "public_signers", "broker_custody", "observer_custody", "pipe_policy"])
@pytest.mark.parametrize("fault", ["unknown", "absent", "changed", "untyped"])
def test_each_partial_or_unproven_readback_is_reconciliation_only(pins, name, fault):
    rows = list(present(pins)); i = next(i for i, r in enumerate(rows) if r.name == name)
    rows[i] = {"unknown": en.Readback(name, "UNKNOWN"), "absent": en.Readback(name, "ABSENT"),
               "changed": en.Readback(name, "PRESENT", b'{}'), "untyped": en.Readback(name, True, b'{}')}[fault]
    result = en.attended_plan(pins, tuple(rows))
    assert result.state == "HOLD_RECONCILE_ONLY" and not result.steps


@pytest.mark.parametrize("fault", ["missing", "duplicate", "extra", "wrong_name", "changed_source", "changed_generation"])
def test_readback_cannot_reown_migrate_or_retry_unknown(pins, fault):
    rows = present(pins); old = en.attended_plan(pins, absent(pins))
    if fault == "missing": rows = rows[:-1]
    if fault == "duplicate": rows = (*rows[:-1], rows[0])
    if fault == "extra": rows = (*rows, en.Readback("command", "ABSENT"))
    if fault == "wrong_name": rows = (*rows[:-1], replace(rows[-1], name="installer"))
    if fault == "changed_source": pins = replace(pins, source=replace(SOURCE, release_epoch=2))
    if fault == "changed_generation": pins = replace(pins, generation=2)
    assert en.attended_plan(pins, rows, previous_digest=old.digest).state == "HOLD_RECONCILE_ONLY"


@pytest.mark.parametrize("changes", [dict(generation=True), dict(generation=0), dict(observer_sid=SID),
    dict(observer_sid=b.service_sid()), dict(runner_sids=(b.service_sid(),)), dict(runner_sids=("S-1-5-32-544",)),
    dict(runner_sids=(SID, SID)), dict(issuer_key="5" * 64), dict(broker_key="4" * 64),
    dict(service_sha256="a" * 40), dict(journal_id="unknown")])
def test_role_and_source_pins_fail_closed(pins, changes):
    with pytest.raises(ValueError):
        replace(pins, **changes)


@pytest.fixture(scope="session")
def broker_crypto(crypto):
    key = crypto.root / "g3-broker"
    subprocess.run([crypto.exe, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                   check=True, capture_output=True, timeout=10)
    public = Path(str(key) + ".pub").read_text().split()
    signers = crypto.root / "g3-broker-signers"
    signers.write_text(ob.BROKER_IDENTITY + " " + " ".join(public[:2]) + "\n")
    def signed(body):
        path = crypto.root / (uuid4().hex + ".challenge")
        path.write_bytes(auth.signing_bytes(body))
        subprocess.run([crypto.exe, "-Y", "sign", "-f", str(key), "-n", ob.CHALLENGE_NAMESPACE, str(path)],
                       check=True, capture_output=True, timeout=10)
        return jn.canonical(dict(body=body, signature=base64.b64encode(Path(str(path) + ".sig").read_bytes()).decode("ascii")))
    return SimpleNamespace(key=key, signers=signers, signed=signed)


@pytest.fixture
def g3(broker, broker_crypto, monkeypatch):
    # Restore the G2 constructor after its fixture's fixed-clock wrapper; G3
    # owns its clock explicitly. All Windows boundaries remain synthetic.
    monkeypatch.setattr(b.WindowsServiceAuthority, "__init__", _SERVICE_INITIALIZE)
    path = broker.f.storage.root / "challenges.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(b._REQUEST_SCHEMA); db.execute("INSERT INTO identity VALUES(?)", (JID,))
    monkeypatch.setattr(host, "CHALLENGES_PATH", str(path))
    config = dict(schema="WakeHubG3Host.v1", task_parent=en.TASK_PARENT, source=asdict(SOURCE),
        generation=1, journal_id=JID, target=asdict(TARGET), logon_id="0000000000012345",
        broker_signers=dict(path=str(broker_crypto.signers), sha256=auth._digest(broker_crypto.signers.read_bytes())),
        challenges_path=str(path))
    monkeypatch.setattr(host, "_read_g3_config", lambda: config)
    monkeypatch.setattr(ob, "read_logon_id", lambda pid: config["logon_id"])
    clock = [T]
    instance = host.ConnectedStatusHost(clock=lambda: clock[0])
    def status(challenge, **changes):
        value = ob.observation_body(challenge, now=T, process=broker.client,
            logon_id=config["logon_id"], source=SOURCE, generation=1)
        value.update(changes)
        return broker.f.crypto.signed(value, "observer", "inventory", namespace=ob.STATUS_NAMESPACE)
    def rows():
        with closing(sqlite3.connect(path)) as db:
            return db.execute("SELECT nonce,state,receipt FROM requests").fetchall()
    return SimpleNamespace(broker=broker, config=config, path=path, host=instance, clock=clock,
                           status=status, rows=rows, broker_crypto=broker_crypto)


def test_signed_status_exchange_uses_g2_grants_and_preserves_human_pause(g3, monkeypatch):
    # The G2 fixture's runtime wrapper needs its own explicit clock here.
    monkeypatch.setattr(b.WindowsServiceAuthority, "__init__", lambda self, handle: _SERVICE_INITIALIZE(self, handle, clock=lambda: T))
    g = grant(op.Operation.STATUS)
    _, proof = signed_read(g3.broker, monkeypatch, g)
    monkeypatch.setattr(b.WindowsServiceAuthority, "__init__", _SERVICE_INITIALIZE)
    challenge = g3.host.begin(123)
    assert g3.rows()[0][1:] == ("UNKNOWN", None)
    result = g3.host.complete(123, g3.status(challenge), g3.broker.request(g, proof=proof))
    assert result.decision is op.Decision.STATUS_OBSERVED
    assert result.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert g3.rows()[0][1] == "DONE"
    assert "NOT_PROVEN_BY_SOURCE_OR_TEST_ADAPTER" in result.to_json()
    assert g3.host.complete(123, g3.status(challenge), b"{}").reason is op.Reason.DUPLICATE


def test_real_signatures_and_interactive_transcript_match(g3, monkeypatch):
    challenge = g3.host.begin(123)
    body = ob.verify_status(g3.status(challenge), expected=challenge, anchor=g3.broker.f.anchor, now=T)
    assert body["logon_id"] == challenge.logon_id
    monkeypatch.setattr(b, "read_process_identity", lambda pid: g3.broker.client)
    seen = []
    monkeypatch.setattr(auth, "_desktop_identity", lambda target: seen.append(target))
    transcript = ob.collect_status_transcript(g3.broker_crypto.signed(challenge.body()), expected=challenge,
        broker_signers=g3.broker_crypto.signers, verifier=Path(g3.broker.f.crypto.exe), source=SOURCE,
        generation=1, clock=lambda: T)
    assert transcript == auth.signing_bytes(body) and seen == [TARGET]


@pytest.mark.parametrize("fault", ["schema", "parent", "nonce", "source", "generation", "logon", "sid", "session",
    "pid", "created", "server", "locked", "future", "extra", "signature", "unsigned", "issuer", "namespace"])
def test_forged_wrong_role_or_mismatched_desktop_fails_closed(g3, fault):
    challenge = g3.host.begin(123)
    raw = g3.status(challenge); envelope = jn.decode(raw); value = envelope["body"]
    if fault == "schema": value["schema"] = "WakeHubBrokerDesktop.v1"
    elif fault == "parent": value["task_parent"] = auth.TASK_PARENT
    elif fault == "nonce": value["challenge"]["nonce"] = str(uuid4())
    elif fault == "source": value["challenge"]["source"]["release_sha"] = "f" * 40
    elif fault == "generation": value["challenge"]["generation"] += 1
    elif fault == "logon": value["logon_id"] = "0000000000023456"
    elif fault in ("sid", "session", "pid", "created"):
        key, wrong = {"sid": ("user_sid", OBSERVER), "session": ("session_id", 8), "pid": ("pid", 42), "created": ("created_at", T)}[fault]
        value["process"][key] = wrong
    elif fault == "server": value["challenge"]["server"]["session_id"] = 7
    elif fault == "locked": value["state"] = "LOCKED"
    elif fault == "future": value["observed_at"] = T + 1
    elif fault == "extra": value["url"] = "forbidden"
    if fault == "signature": raw = jn.canonical({**envelope, "signature": "forged"})
    elif fault == "unsigned": raw = jn.canonical(value)
    else: raw = g3.broker.f.crypto.signed(value, "issuer" if fault == "issuer" else "observer", "inventory",
        namespace="wrong-namespace" if fault == "namespace" else ob.STATUS_NAMESPACE)
    result = g3.host.complete(123, raw, g3.broker.request(grant(op.Operation.STATUS)))
    assert result.decision is op.Decision.UNKNOWN_EFFECT and result.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("facet", ["token", "logon", "generation", "source", "anchor", "handle", "expired", "clock_back", "nan"])
def test_service_revalidates_after_challenge_and_never_retries(g3, monkeypatch, facet):
    challenge = g3.host.begin(123); raw = g3.status(challenge)
    if facet == "token": g3.broker.facts["client"]["created_at"] = T
    elif facet == "logon": monkeypatch.setattr(ob, "read_logon_id", lambda pid: "0000000000023456")
    elif facet == "generation": g3.config["generation"] = 2
    elif facet == "source": g3.config["source"]["source_epoch"] = 2
    elif facet == "anchor": g3.broker.anchor["authority"]["runner_sids"] = ["S-1-5-32-544"]
    elif facet == "expired": g3.clock[0] = T + 5
    elif facet == "clock_back": g3.clock[0] = T - 1
    elif facet == "nan": g3.clock[0] = math.nan
    result = g3.host.complete(999 if facet == "handle" else 123, raw, g3.broker.request(grant(op.Operation.STATUS)))
    assert result.decision is op.Decision.UNKNOWN_EFFECT
    assert g3.rows()[0][1:] == ("UNKNOWN", None)
    assert g3.host.complete(123, raw, b"{}").reason is op.Reason.DUPLICATE


@pytest.mark.parametrize("fault", ["generation", "parent", "schema", "journal", "logon", "path", "hash", "extra", "key_role", "key_reused"])
def test_preflight_rejects_unproven_enrollment_before_nonce(g3, fault):
    cfg = g3.config
    if fault == "generation": cfg["generation"] = True
    elif fault == "parent": cfg["task_parent"] = auth.TASK_PARENT
    elif fault == "schema": cfg["schema"] = "untrusted"
    elif fault == "journal": cfg["journal_id"] = "b" * 32
    elif fault == "logon": cfg["logon_id"] = "0" * 16
    elif fault == "path": cfg["challenges_path"] = "caller.sqlite3"
    elif fault == "hash": cfg["broker_signers"]["sha256"] = "0" * 64
    elif fault == "extra": cfg["command"] = "forbidden"
    elif fault in ("key_role", "key_reused"):
        raw = Path(g3.broker.f.anchor["observer_signers"]["path"]).read_text()
        if fault == "key_reused": raw = raw.replace(auth.IDENTITIES["observer"], ob.BROKER_IDENTITY)
        path = g3.broker.f.storage.root / "bad-public-signers"; path.write_text(raw)
        cfg["broker_signers"] = dict(path=str(path), sha256=auth._digest(path.read_bytes()))
    with pytest.raises(ValueError): g3.host.begin(123)
    assert g3.rows() == []


def test_crash_lost_ack_and_reopen_leave_durable_unknown(g3):
    challenge = g3.host.begin(123)
    with pytest.raises(ValueError): g3.host.begin(123)
    g3.host.abandon()
    assert g3.host.complete(123, g3.status(challenge), b"{}").reason is op.Reason.DUPLICATE
    reopened = b.BrokerRequests(g3.path, owner_sid=b.service_sid(), journal_id=JID)
    assert reopened.claim(challenge.nonce, auth._digest(jn.canonical(challenge.body()))) is False
    new_host = host.ConnectedStatusHost(clock=lambda: T)
    new = new_host.begin(123)
    assert new.nonce != challenge.nonce
    assert new_host.complete(123, g3.status(challenge), b"{}").decision is op.Decision.UNKNOWN_EFFECT
    assert all(state == "UNKNOWN" and receipt is None for _, state, receipt in g3.rows())


def test_concurrent_completion_first_wins(g3, monkeypatch):
    challenge = g3.host.begin(123)
    request = g3.broker.request(grant(op.Operation.STATUS))
    calls = []
    def fail(*args): calls.append(1); raise RuntimeError("synthetic lost reply")
    monkeypatch.setattr(host, "_connected_runtime", fail)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: g3.host.complete(123, g3.status(challenge), request), range(2)))
    assert len(calls) == 1
    assert {r.decision for r in results} == {op.Decision.UNKNOWN_EFFECT, op.Decision.HOLD}
    assert g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("operation,request_bound", [
    (START, False), (op.Operation.REUSE, False), (START, True), (op.Operation.REUSE, True),
    (op.Operation.RECONCILE_ONLY, True)])
def test_host_never_reaches_native_effect_boundary(g3, monkeypatch, operation, request_bound):
    monkeypatch.setattr(rt.WindowsExecutor, "start_dedicated", lambda *a: pytest.fail("START invoked"))
    monkeypatch.setattr(rt.WindowsExecutor, "reuse_owned", lambda *a: pytest.fail("ATTACH invoked"))
    monkeypatch.setattr(host, "_connected_runtime", lambda *a: pytest.fail("disallowed request reached runtime"))
    request = g3.broker.request(grant(operation))
    challenge = g3.host.begin(123, request_sha256=auth._digest(request) if request_bound else None)
    result = g3.host.complete(123, g3.status(challenge), request)
    assert result.operation is op.Operation.STATUS
    assert result.decision is op.Decision.HOLD and result.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert result.reason is op.Reason.INVALID_REQUEST
    assert g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("fault", ["session0", "wrong_process", "wrong_signed_challenge", "locked", "logon_race", "wrong_source", "wrong_generation"])
def test_interactive_collector_denies_without_browser_or_service_gui(g3, monkeypatch, fault):
    challenge = g3.host.begin(123)
    process = g3.broker.client
    if fault == "session0": process = g3.broker.server
    if fault == "wrong_process": process = replace(process, pid=42)
    monkeypatch.setattr(b, "read_process_identity", lambda pid: process)
    seen = []
    def desktop(target):
        seen.append(target)
        if fault == "locked": jn.deny()
    monkeypatch.setattr(auth, "_desktop_identity", desktop)
    if fault == "logon_race":
        ids = iter((challenge.logon_id, "0000000000023456"))
        monkeypatch.setattr(ob, "read_logon_id", lambda pid: next(ids))
    signed = g3.broker_crypto.signed(replace(challenge, nonce=str(uuid4())).body() if fault == "wrong_signed_challenge" else challenge.body())
    with pytest.raises(ValueError):
        ob.collect_status_transcript(signed, expected=challenge, broker_signers=g3.broker_crypto.signers,
            verifier=Path(g3.broker.f.crypto.exe), source=replace(SOURCE, release_epoch=2) if fault == "wrong_source" else SOURCE,
            generation=2 if fault == "wrong_generation" else 1, clock=lambda: T)
    if fault in ("session0", "wrong_process", "wrong_signed_challenge"): assert seen == []


def test_scm_stage_is_fixed_stopped_not_live_enrollment(monkeypatch):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    states, names = [], []
    def api(dll, name, args, result):
        if name == "RegisterServiceCtrlHandlerExW":
            return lambda service, handler, context: names.append(service) or 111
        if name == "SetServiceStatus":
            def status(token, ptr):
                states.append((ptr._obj.state, ptr._obj.exit, ptr._obj.specific)); return True
            return status
        assert name == "StartServiceCtrlDispatcherW"
        def dispatch(table):
            assert table[0].name == b.SERVICE and table[1].name is None
            table[0].main(0, None); return True
        return dispatch
    monkeypatch.setattr(b, "_api", api)
    host.staged_scm_entrypoint()
    assert names == [b.SERVICE] and states == [(1, 1066, 1)]


def test_default_host_and_scm_entrypoint_cannot_run_as_runner(monkeypatch):
    monkeypatch.setattr(auth, "windows_only", jn.deny)
    instance = host.ConnectedStatusHost()
    with pytest.raises(ValueError): instance.begin(123)
    with pytest.raises(ValueError): host.staged_scm_entrypoint()
    assert instance.complete(123, b"{}", b"{}").effect is op.EffectAttempt.NOT_ATTEMPTED


def test_g3_original_outcome_reconciliation_preserves_first_wins_keys(g3):
    original = grant()
    journal = g3.broker.f.storage.journal
    lease = journal.reserve(op.PrivateIntent(original), T)
    journal.mark_unknown(op.PrivateIntent(original))
    reconcile = grant(op.Operation.RECONCILE_ONLY, reconciles=original.key)
    challenge = g3.host.begin(123)
    missing = g3.host.complete(123, g3.status(challenge), g3.broker.request(reconcile))
    assert missing.operation is op.Operation.RECONCILE_ONLY
    assert missing.reason is op.Reason.RECONCILE_ONLY
    assert journal.read(original).state is op.JournalState.UNKNOWN
    observation, _ = original_outcome(g3.broker.f, lease)
    for _ in range(2):
        challenge = g3.host.begin(123)
        result = g3.host.complete(123, g3.status(challenge), g3.broker.request(reconcile))
        assert result.operation is op.Operation.RECONCILE_ONLY
        assert result.decision is op.Decision.RECONCILED
        assert result.effect is op.EffectAttempt.NOT_ATTEMPTED
        assert g3.broker.f.storage.reopen().read(original).observation == observation
        assert journal.reserve(op.PrivateIntent(original), T) is None


@pytest.mark.parametrize("fault", ["wrong_lease", "present_only", "forged"])
def test_g3_unknown_effect_never_resolves_from_new_presence_or_wrong_proof(g3, fault):
    original = grant(); journal = g3.broker.f.storage.journal
    lease = journal.reserve(op.PrivateIntent(original), T)
    journal.mark_unknown(op.PrivateIntent(original))
    original_outcome(g3.broker.f, replace(lease, lease_id=uuid4()) if fault == "wrong_lease" else lease,
        basis="CHROME_PRESENT" if fault == "present_only" else "INDEPENDENT_DURABLE_ORIGINAL_OPERATION")
    if fault == "forged":
        (g3.broker.f.storage.root / "outcome.json").write_bytes(b'{"signed":true}')
    reconcile = grant(op.Operation.RECONCILE_ONLY, reconciles=original.key)
    challenge = g3.host.begin(123)
    result = g3.host.complete(123, g3.status(challenge), g3.broker.request(reconcile))
    assert result.operation is op.Operation.RECONCILE_ONLY
    assert result.reason is op.Reason.RECONCILE_ONLY
    assert result.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert journal.read(original).state is op.JournalState.UNKNOWN
    assert journal.reserve(op.PrivateIntent(original), T) is None


def test_journal_exact_verifier_admission_never_accepts_arbitrary_subclasses(storage):
    # A malicious subclass cannot replace the real signature recheck with a
    # truthy result. Its method must not even be called by the journal.
    class SpoofedG3(host.G3ServiceAuthority):
        def __init__(self): pass
        def verify_original_outcome(self, record, raw): pytest.fail("foreign verifier invoked")
    with pytest.raises(ValueError):
        storage.journal.resolve_verified(None, None, b"{}", SpoofedG3())


@pytest.mark.parametrize("fault", [None, "open_process", "open_token", "query", "impersonation", "size"])
def test_logon_luid_primary_token_api_and_handle_cleanup_are_synthetic(monkeypatch, fault):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    closed = []
    def api(dll, name, args, result):
        if name == "CloseHandle": return lambda value: closed.append(value) or True
        if name == "OpenProcess": return lambda access, inherit, pid: 0 if fault == "open_process" else 777
        if name == "OpenProcessToken":
            def token(process, access, out):
                if fault == "open_token": return False
                out._obj.value = 888; return True
            return token
        assert name == "GetTokenInformation"
        def information(token, kind, out, size, needed):
            assert token.value == 888 and kind == 10
            data = out._obj
            data.authentication.high = -1; data.authentication.low = 0x12
            data.kind = 2 if fault == "impersonation" else 1
            needed._obj.value = size - 1 if fault == "size" else size
            return fault != "query"
        return information
    monkeypatch.setattr(b, "_api", api)
    if fault:
        with pytest.raises(ValueError): ob.read_logon_id(42)
    else:
        assert ob.read_logon_id(42) == "ffffffff00000012"
    assert [getattr(value, "value", value) for value in closed] == (
        [] if fault == "open_process" else [777] if fault == "open_token" else [888, 777])


@pytest.mark.parametrize("pid", [True, 0, -1, "42"])
def test_bad_logon_pid_never_opens_a_process(monkeypatch, pid):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: pytest.fail("invalid PID reached native API"))
    with pytest.raises(ValueError): ob.read_logon_id(pid)


def test_all_g3_modules_import_without_native_or_storage_effect(monkeypatch):
    import importlib.util
    import socket
    import sys
    def forbidden(*args, **kwargs): pytest.fail("G3 import crossed a native boundary")
    monkeypatch.setattr(ctypes, "WinDLL", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    for module in (en, ob, host):
        name = "offline_import_" + module.__name__.rsplit(".", 1)[-1]
        spec = importlib.util.spec_from_file_location(name, module.__file__)
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, loaded)
        spec.loader.exec_module(loaded)


def test_hostops_status_preflight_specification_sanitized_and_differentiates():
    default_report = host.assess_hostops_status_preflight()
    assert default_report.service_installed == "SERVICE_NOT_INSTALLED"
    assert default_report.signer_enrolled == "SIGNER_NOT_ENROLLED"
    assert default_report.desktop_observer_enrolled == "DESKTOP_OBSERVER_NOT_ENROLLED"
    assert default_report.loopback_9444_ownership == "LOOPBACK_9444_UNVERIFIED"
    assert default_report.chatgpt_account_session == "ACCOUNT_UNPROVEN"
    assert default_report.route_generation == "ROUTE_GENERATION_UNVERIFIED"
    assert default_report.complete_search_scope == "SEARCH_COVERAGE_UNPROVEN"
    assert default_report.overall_disposition == "OWNER_ESCALATION"
    assert "CHATGPT_ACCOUNT_SESSION_AUTHORITY" in default_report.missing_owners
    assert "COMPLETE_SEARCH_DOMAIN_WITNESS" in default_report.missing_owners

    # Sanitized dict contains ONLY fixed status strings, no paths, URLs, PIDs, or secrets
    data = default_report.to_sanitized_dict()
    assert data["schema_version"] == "HostOpsStatusPreflight.v1"
    assert set(data.keys()) == {
        "schema_version", "service_installed", "signer_enrolled",
        "desktop_observer_enrolled", "loopback_9444_ownership",
        "chatgpt_account_session", "route_generation", "complete_search_scope",
        "overall_disposition", "authority_scope", "missing_owners", "required_human_grants",
    }
    for key, val in data.items():
        if isinstance(val, str):
            assert not any(p in val for p in ("\\", "/", ":", "@", "C:", "http"))

    # Test differentiation across facets
    stopped_report = host.assess_hostops_status_preflight(service_state="STOPPED")
    assert stopped_report.service_installed == "SERVICE_STOPPED"

    conflict_report = host.assess_hostops_status_preflight(cdp_port_owner="CONFLICT")
    assert conflict_report.loopback_9444_ownership == "LOOPBACK_9444_CONFLICT"

    stale_report = host.assess_hostops_status_preflight(route_generation_state="STALE")
    assert stale_report.route_generation == "ROUTE_GENERATION_STALE"

    partial_g3_report = host.assess_hostops_status_preflight(
        service_state="INSTALLED", signer_state="ENROLLED", desktop_state="ENROLLED"
    )
    assert partial_g3_report.service_installed == "SERVICE_INSTALLED"
    assert partial_g3_report.signer_enrolled == "SIGNER_ENROLLED"
    assert partial_g3_report.desktop_observer_enrolled == "DESKTOP_OBSERVER_ENROLLED"
    # Even with G3 OS facets verified, chat account and search coverage remain unproven -> OWNER_ESCALATION
    assert partial_g3_report.overall_disposition == "OWNER_ESCALATION"
    assert "CHATGPT_ACCOUNT_SESSION_AUTHORITY" in partial_g3_report.missing_owners
    assert "COMPLETE_SEARCH_DOMAIN_WITNESS" in partial_g3_report.missing_owners


def test_g3_status_retains_partial_os_evidence_without_enabling_origin_attestation(g3):
    challenge = g3.host.begin(123)
    raw_status = g3.status(challenge)
    body = ob.verify_status(raw_status, expected=challenge, anchor=g3.broker.f.anchor, now=T)
    assert body["state"] == "LIVE_UNLOCKED_OWNER"
    assert body["logon_id"] == challenge.logon_id

    partial = host.G3PartialStatusEvidence(
        challenge_nonce=challenge.nonce,
        logon_id=challenge.logon_id,
        session_id=challenge.client.session_id,
    )
    assert partial.os_identity == "UNVERIFIED"
    assert partial.complete_search_proven is False
    assert partial.origin_attested is False
    assert partial.disposition == "OWNER_ESCALATION"
    assert "CHATGPT_ACCOUNT_SESSION_AUTHORITY" in partial.missing_owners
    assert "COMPLETE_SEARCH_DOMAIN_WITNESS" in partial.missing_owners



def test_status_all_positive_caller_strings_are_advisory_not_native_authority():
    report = host.assess_hostops_status_preflight(
        service_state="INSTALLED", signer_state="ENROLLED", desktop_state="ENROLLED",
        cdp_port_owner="OWNED_CDP_9444", chat_account_state="AUTHENTICATED",
        route_generation_state="CURRENT", search_scope_state="COMPLETE",
    )
    assert report.overall_disposition == "OWNER_ESCALATION"
    assert report.authority_scope == "SOURCE_ONLY_ADVISORY"
    assert {"CHATGPT_ACCOUNT_SESSION_AUTHORITY", "COMPLETE_SEARCH_DOMAIN_WITNESS",
            "WAKEHUB_G3_G4_SERVICE_PACKAGING_OWNER"} <= set(report.missing_owners)
    sanitized = report.to_sanitized_dict()
    assert sanitized["overall_disposition"] == "OWNER_ESCALATION"
    assert sanitized["authority_scope"] == "SOURCE_ONLY_ADVISORY"

    forged = host.G3PartialStatusEvidence(
        os_identity="S-1-5-21-PRIVATE", challenge_nonce="secret_nonce",
        logon_id="private_logon", session_id=123,
        disposition="READY", complete_search_proven=True, origin_attested=True,
    )
    public = forged.to_sanitized_dict()
    assert public["os_identity"] == "UNVERIFIED"
    assert public["disposition"] == "OWNER_ESCALATION"
    assert public["complete_search_proven"] is False
    assert public["origin_attested"] is False
    assert not any(secret in str(public) for secret in
                   ("secret_nonce", "private_logon", "S-1-5-21-PRIVATE", "123"))


@pytest.fixture
def desktop_pins(pins):
    # All readbacks below are OFFLINE claims. Equality cannot enroll a service,
    # establish native ACLs, or attest an interactive desktop on this runner.
    return en.DesktopBootstrapPins(pins, TARGET,
        inv.ProcessIdentity(333, T - 60, en.DESKTOP_IMAGE, SID, 7), "0000000000012345",
        "7" * 64, "8" * 64, r"C:\Private-Fixture\Local\AI-Commerce\Chrome-Acquisition\config.json",
        "9" * 64, "Default", True)


def desktop_readback(pins, *, first_boot=False):
    new = {"desktop_transport", "desktop_outcome"} if first_boot else set()
    return tuple(en.Readback(r.name, "ABSENT") if r.name in new else en.Readback(r.name, "PRESENT", r.value)
                 for r in en.desktop_bootstrap_resources(pins))


@pytest.mark.parametrize("first_boot", [True, False])
def test_desktop_bootstrap_positive_source_requirements_never_native_ready(desktop_pins, monkeypatch, first_boot):
    def forbidden(*a, **kw): pytest.fail("offline bootstrap crossed a native boundary")
    for name in ("run", "Popen"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden)
    rows = desktop_readback(desktop_pins, first_boot=first_boot)
    result = en.desktop_bootstrap_plan(desktop_pins, rows)
    assert result == en.desktop_bootstrap_plan(desktop_pins, rows, previous_digest=result.digest)
    assert result.source_ready
    assert result.state is (en.DesktopBootstrapState.FIRST_BOOT if first_boot else en.DesktopBootstrapState.READBACK_MATCH)
    public = json.loads(result.to_json())
    assert public["status"] == "SOURCE_READY_FOR_ENROLLMENT"
    assert public["first_boot_eligible"] is first_boot
    assert public["start"] == public["reuse"] == "DENY"
    assert public["native_effect"] == "NOT_ATTEMPTED"
    assert tuple(public["missing_owners"]) == en.DESKTOP_MISSING_OWNERS
    resources = {r.name: jn.decode(r.value) for r in result.resources}
    assert resources["pipe_policy"]["operations"] == ["STATUS", "RECONCILE_ONLY"]
    assert resources["desktop_transport"]["status_pipe_effects"] == "FORBIDDEN"
    assert resources["desktop_transport"]["desktop_challenge"] == "EXISTING_G3_SIGNED_NONCE_SOURCE_GENERATION_AND_LOGON_LUID"
    assert resources["desktop_transport"]["status_grants_allow_effects"] is False
    assert resources["desktop_transport"]["reuse"] == "EXACT_OWNED_PROCESS_NEVER_SPAWN"
    assert resources["desktop_outcome"]["unknown"] == "RECONCILE_ONLY_NO_SECOND_START"
    assert resources["chrome_config"]["endpoint"] == "http://127.0.0.1:9444"
    assert resources["effect_lane"]["ledger"] == "EXISTING_G2_DURABLE_FIRST_WINS"
    for secret in (SID, desktop_pins.logon_id, desktop_pins.config_path, TARGET.dedicated_profile,
                   en.DESKTOP_IMAGE, JID, result.digest, "http://127.0.0.1:9444"):
        assert secret not in result.to_json()


@pytest.mark.parametrize("resource,field,value", [
    ("chrome_config", "port", 9333), ("chrome_config", "endpoint", "http://127.0.0.1:9333"),
    ("chrome_config", "executable", r"C:\Foreign\chrome.exe"),
    ("chrome_config", "user_data_dir", r"C:\Foreign\Profile"),
    ("chrome_config", "image_sha256", "0" * 64), ("chrome_config", "sha256", "0" * 64),
    ("chrome_config", "owner", "FOREIGN"), ("chrome_config", "writers", "EVERYONE"),
    ("chrome_config", "protected", False), ("chrome_config", "reparse_points", True),
    ("chrome_config", "hardlinks", True), ("chrome_config", "profile_owner", "FOREIGN"),
    ("chrome_config", "foreign_profile_access", True),
    ("chrome_config", "enrollment", "UNENROLLED"), ("chrome_config", "allow_launch", False),
    ("desktop_identity", "peer", dict(pid=999, created_at=T, executable=en.DESKTOP_IMAGE, user_sid=SID, session_id=7)),
    ("desktop_identity", "logon_id", "0000000000099999"),
    ("desktop_identity", "desktop", r"WinSta0\Winlogon"),
    ("desktop_identity", "token", "IMPERSONATION"), ("desktop_identity", "elevated", True),
    ("desktop_identity", "interactive", False), ("desktop_identity", "unlocked", False),
    ("desktop_identity", "connected", False), ("desktop_identity", "session_zero", True),
    ("desktop_identity", "image_sha256", "0" * 64),
    ("effect_lane", "state", "PENDING"), ("effect_lane", "state", "UNKNOWN"),
    ("effect_lane", "consumed_keys", ["already-consumed"]),
    ("desktop_transport", "generation", 2), ("desktop_transport", "status_pipe_effects", "START"),
    ("desktop_transport", "desktop_challenge", "UNSIGNED"),
    ("desktop_transport", "status_grants_allow_effects", True),
    ("desktop_outcome", "retry", True),
])
def test_desktop_bootstrap_rejects_mismatched_private_readback(desktop_pins, resource, field, value):
    rows = list(desktop_readback(desktop_pins))
    i = next(i for i, row in enumerate(rows) if row.name == resource)
    body = jn.decode(rows[i].value); body[field] = value
    rows[i] = en.Readback(resource, "PRESENT", jn.canonical(body))
    result = en.desktop_bootstrap_plan(desktop_pins, tuple(rows))
    assert result.state is en.DesktopBootstrapState.RECONCILE_ONLY
    assert not result.source_ready


@pytest.mark.parametrize("name", ["release", "service", "anchors", "public_signers", "broker_custody",
    "observer_custody", "pipe_policy", "chrome_config", "desktop_identity", "effect_lane",
    "desktop_transport", "desktop_outcome"])
@pytest.mark.parametrize("state", ["ABSENT", "UNKNOWN"])
def test_desktop_bootstrap_each_missing_or_unknown_resource_is_hold(desktop_pins, name, state):
    rows = tuple(en.Readback(row.name, state) if row.name == name else row
                 for row in desktop_readback(desktop_pins))
    assert en.desktop_bootstrap_plan(desktop_pins, rows).state is en.DesktopBootstrapState.RECONCILE_ONLY


@pytest.mark.parametrize("fault", ["all_absent", "missing", "duplicate", "extra", "untyped", "bad_name", "bad_state"])
def test_desktop_bootstrap_no_unenrolled_or_incomplete_first_boot(desktop_pins, fault):
    rows = desktop_readback(desktop_pins, first_boot=True)
    if fault == "all_absent": rows = tuple(en.Readback(r.name, "ABSENT") for r in rows)
    elif fault == "missing": rows = rows[:-1]
    elif fault == "duplicate": rows = (*rows[:-1], rows[0])
    elif fault == "extra": rows = (*rows, en.Readback("arbitrary_route", "ABSENT"))
    elif fault == "untyped": rows = [*rows]
    elif fault == "bad_name": rows = (en.Readback([], "PRESENT", b"{}"), *rows[1:])
    elif fault == "bad_state": rows = (replace(rows[0], state=True), *rows[1:])
    result = en.desktop_bootstrap_plan(desktop_pins, rows)
    assert not result.source_ready
    assert not json.loads(result.to_json())["first_boot_eligible"]


@pytest.mark.parametrize("facet", ["source", "generation", "hash", "logon", "pid_creation", "config", "launch"])
def test_desktop_bootstrap_stale_pins_cannot_reuse_first_boot_digest(desktop_pins, facet):
    old = en.desktop_bootstrap_plan(desktop_pins, desktop_readback(desktop_pins, first_boot=True))
    changes = {
        "source": dict(enrollment=replace(desktop_pins.enrollment, source=replace(SOURCE, release_epoch=2))),
        "generation": dict(enrollment=replace(desktop_pins.enrollment, generation=2)),
        "hash": dict(executor_sha256="0" * 64), "logon": dict(logon_id="0000000000023456"),
        "pid_creation": dict(peer=replace(desktop_pins.peer, created_at=T - 1)),
        "config": dict(config_sha256="0" * 64), "launch": dict(allow_launch=False),
    }
    changed = replace(desktop_pins, **changes[facet])
    result = en.desktop_bootstrap_plan(changed, desktop_readback(changed), previous_digest=old.digest)
    assert result.state is en.DesktopBootstrapState.RECONCILE_ONLY
    assert en.desktop_bootstrap_postflight(old, changed, desktop_readback(changed)).state is en.DesktopBootstrapState.RECONCILE_ONLY


@pytest.mark.parametrize("fault", ["reservation_error", "spawn_ambiguity", "timeout", "lost_ack", "partially_applied"])
def test_desktop_bootstrap_postflight_unknown_never_becomes_second_first_boot(desktop_pins, fault):
    first = en.desktop_bootstrap_plan(desktop_pins, desktop_readback(desktop_pins, first_boot=True))
    rows = desktop_readback(desktop_pins, first_boot=fault != "partially_applied")
    if fault == "partially_applied": rows = (*rows[:-1], en.Readback(rows[-1].name, "UNKNOWN"))
    # Still-absent resources after ANY prior attempt are not first-boot evidence.
    unknown = en.desktop_bootstrap_postflight(first, desktop_pins, rows)
    assert unknown.state is en.DesktopBootstrapState.RECONCILE_ONLY
    assert not unknown.source_ready
    assert en.desktop_bootstrap_postflight(unknown, desktop_pins, desktop_readback(desktop_pins)).state is en.DesktopBootstrapState.RECONCILE_ONLY
    matching = en.desktop_bootstrap_postflight(first, desktop_pins, desktop_readback(desktop_pins))
    assert matching.state is en.DesktopBootstrapState.READBACK_MATCH
    assert not json.loads(matching.to_json())["first_boot_eligible"]


@pytest.mark.parametrize("fault", ["sid", "session0", "session", "executable", "luid", "hash", "launch_flag"])
def test_desktop_bootstrap_pin_identity_is_exact(desktop_pins, fault):
    changes = {
        "sid": dict(peer=replace(desktop_pins.peer, user_sid=OBSERVER)),
        "session0": dict(peer=replace(desktop_pins.peer, session_id=0)),
        "session": dict(peer=replace(desktop_pins.peer, session_id=8)),
        "executable": dict(peer=replace(desktop_pins.peer, executable=r"C:\Foreign\peer.exe")),
        "luid": dict(logon_id="0" * 16), "hash": dict(chrome_sha256="a" * 40),
        "launch_flag": dict(allow_launch=1),
    }
    with pytest.raises(ValueError): replace(desktop_pins, **changes[fault])


def test_desktop_bootstrap_launch_false_is_preserved_and_public_state_is_closed(desktop_pins):
    pins = replace(desktop_pins, allow_launch=False)
    plan = en.desktop_bootstrap_plan(pins, desktop_readback(pins, first_boot=True))
    config = jn.decode(next(r.value for r in plan.resources if r.name == "chrome_config"))
    assert config["allow_launch"] is False
    assert json.loads(plan.to_json())["start"] == "DENY"
    with pytest.raises(ValueError): replace(plan, state=SimpleNamespace(value="private SID/path")).to_json()


@pytest.fixture
def status_exchange(g3, monkeypatch):
    """Real role signatures/ledgers, explicitly synthetic HANDLE/desktop custody.

    Only scratch OpenSSH sign/verify subprocesses are allowed. Every other
    process, Win32 API, socket/listener, START and REUSE boundary fails the test.
    This fixture never installs a component or proves native readiness.
    """
    import socket
    original_run, original_popen = subprocess.run, subprocess.Popen
    commands = []
    def check_command(argv, kwargs):
        assert type(argv) is list and len(argv) >= 3 and not kwargs.get("shell")
        assert Path(argv[0]) == Path(g3.broker.f.crypto.exe)
        assert argv[1:3] in (["-Y", "sign"], ["-Y", "verify"])
        commands.append(tuple(argv[:3]))
    def checked_run(argv, *args, **kwargs):
        check_command(argv, kwargs)
        return original_run(argv, *args, **kwargs)
    def checked_popen(argv, *args, **kwargs):
        check_command(argv, kwargs)
        return original_popen(argv, *args, **kwargs)
    def forbidden(*args, **kwargs): pytest.fail("source STATUS crossed a native effect boundary")
    monkeypatch.setattr(subprocess, "run", checked_run)
    monkeypatch.setattr(subprocess, "Popen", checked_popen)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(rt.WindowsExecutor, "start_dedicated", forbidden)
    monkeypatch.setattr(rt.WindowsExecutor, "reuse_owned", forbidden)
    monkeypatch.setattr(b, "read_process_identity", lambda pid: g3.broker.client)
    g = grant(op.Operation.STATUS)
    monkeypatch.setattr(b.WindowsServiceAuthority, "__init__",
        lambda self, handle: _SERVICE_INITIALIZE(self, handle, clock=lambda: T))
    _, proof = signed_read(g3.broker, monkeypatch, g)
    monkeypatch.setattr(b.WindowsServiceAuthority, "__init__", _SERVICE_INITIALIZE)
    monotonic = [100.0]
    return SimpleNamespace(g3=g3, exchange=host.ConnectedStatusExchange(
        clock=lambda: g3.clock[0], monotonic=lambda: monotonic[0]), monotonic=monotonic,
        request=g3.broker.request(g, proof=proof), grant=g, proof=proof, commands=commands)


def offered_exchange(x):
    challenge = x.exchange.begin(123, x.request)
    offer = x.exchange.offer(123, x.g3.broker_crypto.signed(challenge.body()))
    return challenge, offer


def status_reply(x, challenge, *, payload=None, role=ob.StatusRole.OBSERVER):
    return ob.StatusFrame(role, payload if payload is not None else x.g3.status(challenge)).to_bytes()


def test_bounded_peer_owner_and_desktop_consumer_signed_status_positive(status_exchange):
    x = status_exchange
    challenge, offer = offered_exchange(x)
    assert challenge.request_sha256 == auth._digest(x.request)
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)
    transcript = ob.collect_status_frame_transcript(offer, expected=challenge,
        broker_signers=x.g3.broker_crypto.signers, verifier=Path(x.g3.broker.f.crypto.exe),
        source=SOURCE, generation=1, clock=lambda: T)
    signed_status = x.g3.status(challenge)
    assert transcript == auth.signing_bytes(jn.decode(signed_status)["body"])
    receipt = x.exchange.complete(123, status_reply(x, challenge, payload=signed_status))
    assert receipt.operation is op.Operation.STATUS and receipt.decision is op.Decision.STATUS_OBSERVED
    assert receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1] == "DONE"
    with closing(sqlite3.connect(x.g3.broker.requests)) as db:
        assert db.execute("SELECT state FROM requests").fetchall() == [("DONE",)]
    assert x.commands and any(command[-1] == "verify" for command in x.commands)
    for secret in (SID, JID, challenge.nonce, challenge.logon_id, challenge.server.executable,
                   TARGET.dedicated_profile, challenge.request_sha256):
        assert secret not in receipt.to_json()
    assert "NOT_PROVEN_BY_SOURCE_OR_TEST_ADAPTER" in receipt.to_json()
    assert x.exchange.complete(123, status_reply(x, challenge)).reason is op.Reason.DUPLICATE


@pytest.mark.parametrize("fault", ["unsigned", "forged", "observer_role", "wrong_namespace",
    "start", "reuse", "reconcile", "source", "release", "server", "client", "journal", "extra",
    "noncanonical", "oversized", "untyped", "bad_handle"])
def test_peer_request_preflight_never_reserves_untrusted_or_effect_request(status_exchange, fault):
    x = status_exchange
    value = jn.decode(x.request); body = value["body"]
    if fault == "source": body["source"]["source_epoch"] += 1
    elif fault == "release": body["source"]["release_sha"] = "f" * 40
    elif fault in ("server", "client"): body[fault]["created_at"] += 1
    elif fault == "journal": body["journal_id"] = "b" * 32
    elif fault == "extra": body["command"] = "forbidden"
    elif fault in ("start", "reuse", "reconcile"):
        body["operation"] = {"start": START, "reuse": op.Operation.REUSE,
                             "reconcile": op.Operation.RECONCILE_ONLY}[fault].value
    raw = x.g3.broker.f.crypto.signed(body, "observer" if fault == "observer_role" else "issuer", "grant",
        namespace="wrong-role" if fault == "wrong_namespace" else b.NAMESPACE.format("request"))
    if fault == "unsigned": raw = jn.canonical(body)
    elif fault == "forged": raw = jn.canonical({**value, "signature": "forged"})
    elif fault == "noncanonical": raw += b"\n"
    elif fault == "oversized": raw = b"x" * (en.STATUS_FRAME_BODY_LIMIT + 1)
    elif fault == "untyped": raw = value
    with pytest.raises(ValueError): x.exchange.begin(True if fault == "bad_handle" else 123, raw)
    assert x.g3.rows() == []
    with pytest.raises(ValueError): x.exchange.begin(123, x.request)


@pytest.mark.parametrize("fault", ["unsigned", "forged", "issuer_role", "nonce", "request",
    "source", "generation", "luid", "sid", "session0", "image", "starttime", "port9333",
    "profile", "desktop", "expired", "noncanonical"])
def test_broker_offer_requires_independent_signature_and_exact_challenge(status_exchange, fault):
    x = status_exchange
    challenge = x.exchange.begin(123, x.request); body = challenge.body()
    if fault == "nonce": body["nonce"] = str(uuid4())
    elif fault == "request": body["request_sha256"] = "0" * 64
    elif fault == "source": body["source"]["release_epoch"] += 1
    elif fault == "generation": body["generation"] += 1
    elif fault == "luid": body["logon_id"] = "0000000000099999"
    elif fault == "sid": body["client"]["user_sid"] = OBSERVER
    elif fault == "session0": body["client"]["session_id"] = 0
    elif fault == "image": body["client"]["executable"] = en.DESKTOP_IMAGE
    elif fault == "starttime": body["client"]["created_at"] += 1
    elif fault == "port9333": body["target"]["port"] = 9333
    elif fault == "profile": body["target"]["dedicated_profile"] += "-foreign"
    elif fault == "desktop": body["target"]["desktop"] = r"WinSta0\Winlogon"
    raw = x.g3.broker_crypto.signed(body)
    if fault == "unsigned": raw = jn.canonical(body)
    elif fault == "forged": raw = jn.canonical({"body": body, "signature": "forged"})
    elif fault == "issuer_role":
        raw = x.g3.broker.f.crypto.signed(body, "issuer", "grant", namespace=ob.CHALLENGE_NAMESPACE)
    elif fault == "noncanonical": raw += b" "
    elif fault == "expired": x.g3.clock[0] = T + 5
    with pytest.raises(ValueError): x.exchange.offer(123, raw)
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)
    assert x.exchange.complete(123, b"{}").reason is op.Reason.DUPLICATE


@pytest.mark.parametrize("fault", ["empty", "partial_header", "partial_body", "length", "oversized",
    "trailing", "two_frames", "noncanonical", "duplicate_key", "extra", "role", "operation", "untyped"])
def test_peer_framing_denies_malformed_in_both_directions(status_exchange, fault):
    x = status_exchange
    challenge, offer = offered_exchange(x)
    reply = status_reply(x, challenge)
    def damage(raw):
        if fault == "empty": return b""
        if fault == "partial_header": return raw[:3]
        if fault == "partial_body": return raw[:-1]
        if fault == "length": return (len(raw) + 99).to_bytes(4, "big") + raw[4:]
        if fault == "oversized": return b"x" * (en.STATUS_FRAME_BODY_LIMIT + 5)
        if fault == "trailing": return raw + b" "
        if fault == "two_frames": return raw + raw
        if fault == "untyped": return bytearray(raw)
        body = jn.decode(raw[4:])
        if fault == "extra": body["pipe"] = "caller-selected"
        elif fault == "role": body["role"] = "wakehub-g2-issuer"
        elif fault == "operation": body["operation"] = START.value
        payload = jn.canonical(body)
        if fault == "noncanonical": payload += b"\n"
        elif fault == "duplicate_key": payload = b'{"operation":"STATUS",' + payload[1:]
        return len(payload).to_bytes(4, "big") + payload
    with pytest.raises(ValueError):
        ob.collect_status_frame_transcript(damage(offer), expected=challenge,
            broker_signers=x.g3.broker_crypto.signers, verifier=Path(x.g3.broker.f.crypto.exe),
            source=SOURCE, generation=1, clock=lambda: T)
    receipt = x.exchange.complete(123, damage(reply))
    assert receipt.decision is op.Decision.UNKNOWN_EFFECT and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)
    assert x.exchange.complete(123, reply).reason is op.Reason.DUPLICATE


@pytest.mark.parametrize("fault", ["unsigned", "forged", "issuer_role", "broker_role", "namespace",
    "nonce", "request", "source", "generation", "luid", "sid", "session0", "pid", "image",
    "starttime", "desktop", "port9333", "profile", "locked", "future", "stale"])
def test_desktop_reply_signature_role_and_full_binding_are_independent(status_exchange, fault):
    x = status_exchange
    challenge, _ = offered_exchange(x)
    body = jn.decode(x.g3.status(challenge))["body"]
    if fault == "nonce": body["challenge"]["nonce"] = str(uuid4())
    elif fault == "request": body["challenge"]["request_sha256"] = "0" * 64
    elif fault == "source": body["challenge"]["source"]["source_epoch"] += 1
    elif fault == "generation": body["challenge"]["generation"] += 1
    elif fault == "luid": body["logon_id"] = "0000000000099999"
    elif fault in ("sid", "session0", "pid", "image", "starttime"):
        key, wrong = {"sid": ("user_sid", OBSERVER), "session0": ("session_id", 0),
            "pid": ("pid", 42), "image": ("executable", en.DESKTOP_IMAGE),
            "starttime": ("created_at", T)}[fault]
        body["process"][key] = wrong
    elif fault == "desktop": body["challenge"]["target"]["desktop"] = r"WinSta0\Winlogon"
    elif fault == "port9333": body["challenge"]["target"]["port"] = 9333
    elif fault == "profile": body["challenge"]["target"]["dedicated_profile"] += "-foreign"
    elif fault == "locked": body["state"] = "LOCKED"
    elif fault == "future": body["observed_at"] = T + 1
    elif fault == "stale": body["observed_at"] = T - 1
    payload = x.g3.broker.f.crypto.signed(body, "issuer" if fault == "issuer_role" else "observer",
        "inventory", namespace="wrong-role" if fault == "namespace" else ob.STATUS_NAMESPACE)
    if fault == "forged": payload = jn.canonical({"body": body, "signature": "forged"})
    if fault == "unsigned":
        raw = jn.canonical(dict(schema="WakeHubG3StatusFrame.v1", operation="STATUS",
            role=ob.StatusRole.OBSERVER.value, payload=body))
        raw = len(raw).to_bytes(4, "big") + raw
    else:
        raw = status_reply(x, challenge, payload=payload,
            role=ob.StatusRole.BROKER if fault == "broker_role" else ob.StatusRole.OBSERVER)
    receipt = x.exchange.complete(123, raw)
    assert receipt.decision is op.Decision.UNKNOWN_EFFECT and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("fault", ["handle", "pipe_acl", "service_acl", "scm", "sid", "session0",
    "image", "starttime", "luid", "generation", "release", "port9333", "profile", "expired", "clock_back"])
def test_peer_reauthenticates_protected_context_after_offer(status_exchange, monkeypatch, fault):
    x = status_exchange
    challenge, _ = offered_exchange(x); reply = status_reply(x, challenge)
    if fault == "pipe_acl": monkeypatch.setattr(b, "observe_connection", lambda handle: jn.deny())
    elif fault == "service_acl": monkeypatch.setattr(b.ServiceCustody, "check", lambda *a, **kw: jn.deny())
    elif fault == "scm": x.g3.broker.facts["scm"]["pid"] += 1
    elif fault in ("sid", "session0", "image", "starttime"):
        key, wrong = {"sid": ("user_sid", OBSERVER), "session0": ("session_id", 0),
            "image": ("executable", en.DESKTOP_IMAGE), "starttime": ("created_at", T)}[fault]
        x.g3.broker.facts["client"][key] = wrong
    elif fault == "luid": monkeypatch.setattr(ob, "read_logon_id", lambda pid: "0000000000099999")
    elif fault == "generation": x.g3.config["generation"] += 1
    elif fault == "release": x.g3.config["source"]["release_epoch"] += 1
    elif fault == "port9333": x.g3.config["target"]["port"] = 9333
    elif fault == "profile": x.g3.config["target"]["dedicated_profile"] += "-foreign"
    elif fault == "expired": x.g3.clock[0] = T + 5
    elif fault == "clock_back": x.g3.clock[0] = T - 1
    receipt = x.exchange.complete(999 if fault == "handle" else 123, reply)
    assert receipt.decision is op.Decision.UNKNOWN_EFFECT and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("fault", ["revoked", "expired_grant", "expired_consent", "wrong_human", "unproven_pause"])
def test_peer_status_still_requires_current_g2_issuer_human_and_revocation(status_exchange, fault):
    x = status_exchange
    if fault != "revoked":
        proof = x.proof
        if fault == "expired_grant":
            old = replace(x.grant, issued_at=T - 10, expires_at=T - 1)
            proof = x.g3.broker.f.proof(old, pause="PAUSED")
            x.request = x.g3.broker.request(old, proof=proof)
        else:
            consent = jn.decode(proof.consent)["body"]
            if fault == "expired_consent": consent["expires_at"] = T - 0.5
            elif fault == "unproven_pause": consent["pause"] = "UNPROVEN"
            raw = x.g3.broker.f.crypto.signed(consent, "issuer" if fault == "wrong_human" else "human",
                "consent", namespace=auth.NAMESPACES["consent"])
            x.request = x.g3.broker.request(x.grant, proof=op.PrivateGrantProof(proof.signature, raw))
    challenge, _ = offered_exchange(x)
    if fault == "revoked":
        x.g3.broker.f.rev["epoch"] += 1
        x.g3.broker.f.rev["revoked"] = [str(x.grant.key.action_id)]
        x.g3.broker.f.save()
    receipt = x.exchange.complete(123, status_reply(x, challenge))
    assert receipt.decision is op.Decision.UNKNOWN_EFFECT and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    with closing(sqlite3.connect(x.g3.broker.requests)) as db:
        assert db.execute("SELECT state,receipt FROM requests").fetchall() == [("UNKNOWN", None)]
    assert x.exchange.complete(123, status_reply(x, challenge)).reason is op.Reason.DUPLICATE


@pytest.mark.parametrize("phase", ["reserved", "offered", "partial_reply", "lost_ack"])
def test_peer_disconnect_crash_and_partial_readback_preserve_unknown(status_exchange, monkeypatch, phase):
    x = status_exchange
    challenge = x.exchange.begin(123, x.request)
    if phase != "reserved": x.exchange.offer(123, x.g3.broker_crypto.signed(challenge.body()))
    if phase == "partial_reply":
        result = x.exchange.complete(123, status_reply(x, challenge)[:-1])
    elif phase == "lost_ack":
        finish = b.BrokerRequests.finish
        def lost_ack(self, *args):
            if self.path == x.g3.path: raise TimeoutError("synthetic challenge journal readback loss")
            return finish(self, *args)
        monkeypatch.setattr(b.BrokerRequests, "finish", lost_ack)
        result = x.exchange.complete(123, status_reply(x, challenge))
    else: result = x.exchange.abandon()
    assert result.decision is op.Decision.UNKNOWN_EFFECT and result.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)
    assert x.exchange.complete(123, status_reply(x, challenge)).reason is op.Reason.DUPLICATE
    with pytest.raises(ValueError): x.exchange.begin(123, x.request)
    monkeypatch.setattr(host, "uuid4", lambda: challenge.nonce)
    reopened = host.ConnectedStatusExchange(clock=lambda: T, monotonic=lambda: 100.0)
    with pytest.raises(ValueError): reopened.begin(123, x.request)
    assert len(x.g3.rows()) == 1


def test_peer_concurrent_completion_and_request_replay_are_first_wins(status_exchange, monkeypatch):
    x = status_exchange
    challenge, _ = offered_exchange(x); reply = status_reply(x, challenge)
    calls = []
    original = host._connected_runtime
    def counted(*args): calls.append(1); return original(*args)
    monkeypatch.setattr(host, "_connected_runtime", counted)
    with ThreadPoolExecutor(2) as pool:
        receipts = list(pool.map(lambda _: x.exchange.complete(123, reply), range(2)))
    assert len(calls) == 1
    assert {r.decision for r in receipts} == {op.Decision.STATUS_OBSERVED, op.Decision.HOLD}
    assert all(r.effect is op.EffectAttempt.NOT_ATTEMPTED for r in receipts)
    # Reject a consumed request before another offer, even with a fresh nonce.
    x.exchange = host.ConnectedStatusExchange(clock=lambda: T, monotonic=lambda: 100.0)
    with pytest.raises(ValueError): x.exchange.begin(123, x.request)
    with closing(sqlite3.connect(x.g3.broker.requests)) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone() == (1,)


@pytest.mark.parametrize("fault", ["issuer_role", "namespace", "forged", "session0", "source", "generation", "locked"])
def test_desktop_consumer_authenticates_even_when_service_offer_is_bypassed(status_exchange, monkeypatch, fault):
    x = status_exchange
    challenge = x.exchange.begin(123, x.request)
    desktop_checks = []
    def desktop(target):
        desktop_checks.append(target)
        if fault == "locked": jn.deny()
    monkeypatch.setattr(auth, "_desktop_identity", desktop)
    raw = x.g3.broker_crypto.signed(challenge.body())
    if fault in ("issuer_role", "namespace"):
        raw = x.g3.broker.f.crypto.signed(challenge.body(), "issuer", "grant",
            namespace=ob.CHALLENGE_NAMESPACE if fault == "issuer_role" else "wrong-namespace")
    elif fault == "forged": raw = jn.canonical({"body": challenge.body(), "signature": "forged"})
    elif fault == "session0": monkeypatch.setattr(b, "read_process_identity", lambda pid: x.g3.broker.server)
    frame = ob.StatusFrame(ob.StatusRole.BROKER, raw).to_bytes()
    signature_errors = {"issuer_role": "SIGNATURE_INVALID", "namespace": "SIGNATURE_INVALID",
                        "forged": "SIGNATURE_UNAVAILABLE"}
    # Crypto denials retain the shared verifier's fixed-code exception type.
    expected_error = Denied if fault in signature_errors else jn.NativeDenied
    with pytest.raises(expected_error) as denied:
        ob.collect_status_frame_transcript(frame, expected=challenge,
            broker_signers=x.g3.broker_crypto.signers, verifier=Path(x.g3.broker.f.crypto.exe),
            source=replace(SOURCE, release_epoch=2) if fault == "source" else SOURCE,
            generation=2 if fault == "generation" else 1, clock=lambda: T)
    assert denied.value.args == (signature_errors.get(fault, "WAKEHUB_NATIVE_CUSTODY_HOLD"),)
    assert desktop_checks == ([] if fault in signature_errors or fault == "session0" else [TARGET])
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)


def test_bound_request_cannot_be_substituted_at_underlying_host(status_exchange, monkeypatch):
    x = status_exchange
    challenge, _ = offered_exchange(x)
    monkeypatch.setattr(host, "_connected_runtime", lambda *args: pytest.fail("substituted request reached runtime"))
    different = x.g3.broker.request(x.grant, proof=x.proof)
    assert different != x.request
    receipt = x.exchange._host.complete(123, x.g3.status(challenge), different)
    assert receipt.decision is op.Decision.UNKNOWN_EFFECT and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)


def test_peer_clock_rollback_within_live_challenge_is_unknown(status_exchange):
    x = status_exchange
    challenge = x.exchange.begin(123, x.request)
    x.g3.clock[0] = T + 1
    x.exchange.offer(123, x.g3.broker_crypto.signed(challenge.body()))
    x.g3.clock[0] = T + 0.5
    receipt = x.exchange.complete(123, status_reply(x, challenge))
    assert receipt.decision is op.Decision.UNKNOWN_EFFECT and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)


def test_peer_unknown_g2_request_readback_cannot_finish_or_retry_challenge(status_exchange, monkeypatch):
    x = status_exchange
    challenge, _ = offered_exchange(x)
    finish = b.BrokerRequests.finish
    def unavailable(self, *args):
        if self.path == x.g3.broker.requests: raise TimeoutError("synthetic G2 readback loss")
        return finish(self, *args)
    monkeypatch.setattr(b.BrokerRequests, "finish", unavailable)
    receipt = x.exchange.complete(123, status_reply(x, challenge))
    assert receipt.decision is op.Decision.UNKNOWN_EFFECT and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)
    with closing(sqlite3.connect(x.g3.broker.requests)) as db:
        assert db.execute("SELECT state,receipt FROM requests").fetchall() == [("UNKNOWN", None)]
    assert x.exchange.complete(123, status_reply(x, challenge)).reason is op.Reason.DUPLICATE


def test_status_transport_contract_is_fixed_source_only_and_effect_guards_remain(monkeypatch):
    def forbidden(*a, **kw): pytest.fail("offline contract reached native code")
    monkeypatch.setattr(ctypes, "WinDLL", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    contract = jn.decode(en.status_transport_contract().value)
    assert contract["service"] == b.SERVICE and contract["pipe"] == b.PIPE
    assert contract["operation"] == "STATUS" and contract["body_limit"] == en.STATUS_FRAME_BODY_LIMIT
    public = en.status_transport_preflight()
    assert public["status"] == "SOURCE_READY_FOR_ENROLLMENT" and public["native_transport"] == "HOLD"
    assert public["dependency"] == "HOLD_DEPENDENCY"
    assert public["start"] == public["reuse"] == "DENY" and public["native_effect"] == "NOT_ATTEMPTED"
    assert tuple(public["missing_owners"]) == en.STATUS_TRANSPORT_MISSING_OWNERS
    for secret in (SID, JID, TARGET.dedicated_profile, en.SERVICE_IMAGE, b.PIPE):
        assert secret not in str(public)
    with pytest.raises(TypeError): en.status_transport_preflight(service_state="RUNNING")
    for operation in (START, op.Operation.REUSE):
        native = rt.WindowsPrivateRuntime()
        native._operator = object()
        receipt = native._run_authenticated(operation)
        assert receipt.decision is op.Decision.HOLD and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    executor = rt.WindowsExecutor()
    with pytest.raises(ValueError): executor.start_dedicated(None)
    with pytest.raises(ValueError): executor.reuse_owned(None)


@pytest.fixture
def pipe_kernel(monkeypatch):
    """Deterministic Win32 ABI seam; never open a real pipe/event/process."""
    x = SimpleNamespace(now=[100.0], side=ob._PipeSide.SERVER, fault=None, raw=b"",
        writes=[], calls=[], waits=[], closed=[], cancel=False, pending=False,
        count=0, error=0, step=0.0, delay=0.0, immediate=False)
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: x.error)
    monkeypatch.setattr(ob, "_SPENT_HANDLES", set())
    monkeypatch.setattr(ob, "_UNFINISHED_IO", [])
    def api(dll, name, args, result):
        def call(*values):
            x.calls.append(name)
            if name == "GetNamedPipeInfo":
                values[1]._obj.value = (1 if x.side is ob._PipeSide.SERVER else 0) | (0 if x.fault == "byte_pipe" else 4)
                return True
            if name == "GetNamedPipeHandleStateW":
                values[1]._obj.value = 0 if x.fault == "byte_read" else 3 if x.fault == "nowait" else 2
                return True
            if name == "NtQueryInformationFile":
                assert values[4] == 16
                values[2]._obj.value = 0x20 if x.fault == "synchronous" else 0
                return 259 if x.fault == "query_pending" else 0
            if name == "CreateEventW":
                assert values == (None, True, False, None)
                return 222
            if name == "CloseHandle":
                x.closed.append(values[0]); return True
            if name in ("ReadFile", "WriteFile"):
                assert values[3] is None and values[4]._obj.event == 223
                x.direction = "read" if name == "ReadFile" else "write"
                if x.fault == x.direction + "_disconnect":
                    x.error = 109; return False
                if name == "WriteFile":
                    x.writes.append(bytes(values[1].raw[:values[2]]))
                    x.count = values[2] - (1 if x.fault == "short_write" else 0)
                else:
                    assert values[2] == en.STATUS_FRAME_BODY_LIMIT + 5
                    count = min(len(x.raw), values[2])
                    ctypes.memmove(values[1], x.raw, count)
                    x.count = count
                    if len(x.raw) > values[2]:
                        x.error = 234; return False
                x.pending = True
                x.error = 997
                return x.immediate
            if name == "GetOverlappedResultEx":
                assert values[4] is False and 0 <= values[3] <= 5000
                x.waits.append(values[3])
                if x.cancel:
                    x.error = 996 if x.fault == "cancel_pending" else 995
                    return False
                if x.fault in (x.direction + "_timeout", "cancel_pending"):
                    x.now[0] += values[3] / 1000
                    x.error = 258; return False
                if x.delay: time.sleep(x.delay)
                x.now[0] += x.step
                if x.fault == "late": x.now[0] = 105.0
                if x.fault == "rollback": x.now[0] = 99.0
                values[2]._obj.value = x.count
                x.pending = False
                return True
            if name == "CancelIoEx":
                x.cancel = True
                assert values[1]._obj.event == 223
                return True
            if name == "PeekNamedPipe":
                values[4]._obj.value = 8 if x.fault == "second_message" else 0
                return True
            pytest.fail("unexpected native API " + name)
        return call
    monkeypatch.setattr(b, "_api", api)
    x.deadline = lambda: ob._StatusDeadline(lambda: x.now[0])
    return x


def offline_wire(role):
    # Only framing tests use this unsigned synthetic body; host/desktop tests
    # below independently verify real, separate scratch role signatures.
    return ob.StatusFrame(role, jn.canonical(dict(body={}, signature="OFFLINE_NOT_AUTHENTIC"))).to_bytes()


@pytest.mark.parametrize("immediate", [False, True])
def test_native_message_io_exact_uint32_frame_and_decreasing_budget(pipe_kernel, immediate):
    x = pipe_kernel; x.immediate = immediate; x.step = 1.25
    pipe = ob._StatusPipeIO(123, ob._PipeSide.SERVER, x.deadline())
    offer = offline_wire(ob.StatusRole.BROKER)
    x.raw = offline_wire(ob.StatusRole.OBSERVER)
    pipe.write_frame(offer)
    assert pipe.read_frame() == x.raw
    assert x.writes == [offer] and int.from_bytes(offer[:4], "big") == len(offer) - 4
    assert x.waits == ([0, 0] if immediate else [5000, 3750])
    with pytest.raises(ValueError): pipe.read_frame()
    with pytest.raises(ValueError): pipe.write_frame(offer)
    pipe.close(); pipe.close()
    assert x.closed == [222, 222, 123]
    assert x.calls.count("ReadFile") == x.calls.count("WriteFile") == 1
    with pytest.raises(ValueError): ob._StatusPipeIO(123, ob._PipeSide.SERVER, x.deadline())


@pytest.mark.parametrize("fault", ["byte_pipe", "byte_read", "nowait", "synchronous", "query_pending"])
def test_native_io_requires_kernel_overlapped_message_mode_before_data(pipe_kernel, fault):
    x = pipe_kernel; x.fault = fault
    with pytest.raises(jn.NativeDenied): ob._StatusPipeIO(123, ob._PipeSide.SERVER, x.deadline())
    assert "ReadFile" not in x.calls and "WriteFile" not in x.calls
    assert x.closed == ([] if fault == "query_pending" else [123])
    assert bool(ob._UNFINISHED_IO) == (fault == "query_pending")


@pytest.mark.parametrize("fault", ["read_timeout", "read_disconnect", "cancel_pending", "late", "rollback"])
def test_native_read_timeout_disconnect_late_ack_and_cancel_are_terminal(pipe_kernel, fault):
    x = pipe_kernel; x.fault = fault; x.raw = offline_wire(ob.StatusRole.OBSERVER)
    pipe = ob._StatusPipeIO(123, ob._PipeSide.SERVER, x.deadline())
    with pytest.raises(jn.NativeDenied): pipe.read_frame()
    with pytest.raises(jn.NativeDenied): pipe.read_frame()
    pipe.close()
    assert x.calls.count("ReadFile") == 1
    assert ("CancelIoEx" in x.calls) == (fault in ("read_timeout", "cancel_pending"))
    if fault == "cancel_pending":
        assert ob._UNFINISHED_IO == [pipe] and x.closed == []
        assert pipe._pending[0].raw[:len(x.raw)] == x.raw
        with pytest.raises(jn.NativeDenied): ob._StatusPipeIO(124, ob._PipeSide.SERVER, x.deadline())
    else:
        assert not ob._UNFINISHED_IO and x.closed == [222, 123]


@pytest.mark.parametrize("fault", ["partial_header", "partial_body", "trailing", "oversize", "second_message"])
def test_native_read_never_assembles_or_retries_invalid_single_message(pipe_kernel, fault):
    x = pipe_kernel; x.fault = fault
    raw = offline_wire(ob.StatusRole.OBSERVER)
    x.raw = {"partial_header": raw[:3], "partial_body": raw[:-1], "trailing": raw + b" ",
        "oversize": b"x" * (en.STATUS_FRAME_BODY_LIMIT + 6), "second_message": raw}[fault]
    pipe = ob._StatusPipeIO(123, ob._PipeSide.SERVER, x.deadline())
    with pytest.raises(jn.NativeDenied): pipe.read_frame()
    pipe.close()
    assert x.calls.count("ReadFile") == 1 and "WriteFile" not in x.calls


def test_native_io_modeled_short_latency_uses_remaining_monotonic_wait(pipe_kernel):
    # Model two 30ms native completions. Never assert a wall-clock minimum:
    # Windows timer resolution/scheduling can complete an actual sleep early.
    x = pipe_kernel; x.step = 0.03
    pipe = ob._StatusPipeIO(123, ob._PipeSide.SERVER, x.deadline())
    x.raw = offline_wire(ob.StatusRole.OBSERVER)
    pipe.write_frame(offline_wire(ob.StatusRole.BROKER)); pipe.read_frame(); pipe.close()
    assert x.now[0] == pytest.approx(100.06)
    assert x.waits[0] == 5000
    assert 4960 <= x.waits[1] < x.waits[0]


def test_connected_native_service_keeps_independent_unknown_before_offer(status_exchange, pipe_kernel):
    x, kernel = status_exchange, pipe_kernel
    kernel.step = 1.5  # 3 seconds of modeled transport latency, no wallclock fixture.
    owner = host.ConnectedStatusIO(clock=lambda: T, monotonic=lambda: kernel.now[0])
    challenge = owner.begin(123, x.request)
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)
    with closing(sqlite3.connect(x.g3.broker.requests)) as db:
        assert db.execute("SELECT state,receipt FROM requests").fetchall() == [("UNKNOWN", None)]
    assert not kernel.writes
    kernel.raw = status_reply(x, challenge)
    receipt = owner.exchange(x.g3.broker_crypto.signed(challenge.body()))
    assert receipt.decision is op.Decision.STATUS_OBSERVED and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert kernel.waits == [5000, 3500] and kernel.closed[-1] == 123
    assert x.g3.rows()[0][1] == "DONE"
    assert owner.exchange(b"{}").reason is op.Reason.DUPLICATE
    for secret in (challenge.nonce, SID, JID, TARGET.dedicated_profile, b.PIPE):
        assert secret not in receipt.to_json()


@pytest.mark.parametrize("fault", ["write_disconnect", "read_disconnect", "write_timeout", "read_timeout",
    "short_write", "late", "second_message"])
def test_connected_native_service_failures_poison_request_without_reconnect(status_exchange, pipe_kernel, fault):
    x, kernel = status_exchange, pipe_kernel
    owner = host.ConnectedStatusIO(clock=lambda: T, monotonic=lambda: kernel.now[0])
    challenge = owner.begin(123, x.request)
    kernel.raw = status_reply(x, challenge); kernel.fault = fault
    receipt = owner.exchange(x.g3.broker_crypto.signed(challenge.body()))
    assert receipt.decision is op.Decision.UNKNOWN_EFFECT and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)
    with closing(sqlite3.connect(x.g3.broker.requests)) as db:
        assert db.execute("SELECT state,receipt FROM requests").fetchall() == [("UNKNOWN", None)]
    assert kernel.calls.count("WriteFile") == 1 and kernel.calls.count("ReadFile") <= 1
    assert owner.exchange(b"{}").reason is op.Reason.DUPLICATE
    # A different HANDLE/exchange cannot replay the issuer request after loss.
    kernel.now[0] = 100.0
    with pytest.raises(ValueError):
        host.ConnectedStatusExchange(clock=lambda: T, monotonic=lambda: 100.0).begin(124, x.request)


def test_connected_native_service_duplicate_owner_cannot_reserve_twice(status_exchange, pipe_kernel):
    x, kernel = status_exchange, pipe_kernel
    first = host.ConnectedStatusIO(clock=lambda: T, monotonic=lambda: kernel.now[0])
    second = host.ConnectedStatusIO(clock=lambda: T, monotonic=lambda: kernel.now[0])
    def begin(owner):
        try:
            owner.begin(123, x.request)
            return owner
        except jn.NativeDenied:
            return None
    with ThreadPoolExecutor(2) as pool:
        winners = [owner for owner in pool.map(begin, (first, second)) if owner is not None]
    assert len(winners) == 1
    assert len(x.g3.rows()) == 1 and not kernel.writes
    assert winners[0].abandon().decision is op.Decision.UNKNOWN_EFFECT


def test_connected_desktop_derives_expectations_from_kernel_and_protected_config(status_exchange, pipe_kernel, monkeypatch):
    x, kernel = status_exchange, pipe_kernel
    challenge, offer = offered_exchange(x)
    kernel.side = ob._PipeSide.CLIENT; kernel.raw = offer
    monkeypatch.setattr(ob, "_observe_client_connection", lambda handle: x.g3.broker.facts)
    peer = ob.ConnectedDesktopStatusIO(clock=lambda: T, monotonic=lambda: kernel.now[0])
    transcript = peer.receive(124)
    signed = x.g3.status(challenge)
    assert transcript == auth.signing_bytes(jn.decode(signed)["body"])
    assert peer.respond(signed) is ob.DesktopStatusOutcome.RESPONSE_WRITTEN
    # Keep the client HANDLE alive until the service's final identity recheck.
    assert 124 not in kernel.closed and len(kernel.writes) == 1
    assert x.exchange.complete(123, kernel.writes[0]).decision is op.Decision.STATUS_OBSERVED
    assert peer.respond(signed) is ob.DesktopStatusOutcome.DUPLICATE
    assert peer.abandon() is ob.DesktopStatusOutcome.UNKNOWN and kernel.closed[-1] == 124


@pytest.mark.parametrize("fault", ["generation", "source", "scm", "luid", "locked", "signature", "timeout"])
def test_connected_desktop_rechecks_after_separate_observer_signing(status_exchange, pipe_kernel, monkeypatch, fault):
    x, kernel = status_exchange, pipe_kernel
    challenge, offer = offered_exchange(x)
    kernel.side = ob._PipeSide.CLIENT; kernel.raw = offer
    monkeypatch.setattr(ob, "_observe_client_connection", lambda handle: x.g3.broker.facts)
    peer = ob.ConnectedDesktopStatusIO(clock=lambda: T, monotonic=lambda: kernel.now[0])
    peer.receive(124)
    signed = x.g3.status(challenge)
    if fault == "generation": x.g3.config["generation"] += 1
    elif fault == "source": x.g3.config["source"]["release_epoch"] += 1
    elif fault == "scm": x.g3.broker.facts["scm"]["pid"] += 1
    elif fault == "luid": monkeypatch.setattr(ob, "read_logon_id", lambda pid: "0000000000099999")
    elif fault == "locked": monkeypatch.setattr(auth, "_desktop_identity", lambda target: jn.deny())
    elif fault == "signature": signed = x.g3.broker_crypto.signed(jn.decode(signed)["body"])
    elif fault == "timeout": kernel.now[0] = 105.0
    assert peer.respond(signed) is ob.DesktopStatusOutcome.UNKNOWN
    assert not kernel.writes and kernel.closed[-1] == 124
    assert peer.respond(signed) is ob.DesktopStatusOutcome.DUPLICATE
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)


def test_exchange_monotonic_expiry_cannot_be_extended_by_wallclock_or_signer(status_exchange):
    x = status_exchange
    challenge, _ = offered_exchange(x)
    reply = status_reply(x, challenge)
    x.monotonic[0] += 5  # Wall clock is still T, not rolled forward to fake expiry.
    assert x.exchange.complete(123, reply).decision is op.Decision.UNKNOWN_EFFECT
    assert x.g3.rows()[0][1:] == ("UNKNOWN", None)


def test_late_durable_ack_is_unknown_and_cannot_reoffer_request(status_exchange, monkeypatch):
    x = status_exchange
    challenge, _ = offered_exchange(x)
    finish = b.BrokerRequests.finish
    def delayed(self, *args):
        result = finish(self, *args)
        if self.path == x.g3.path: x.monotonic[0] += 5
        return result
    monkeypatch.setattr(b.BrokerRequests, "finish", delayed)
    assert x.exchange.complete(123, status_reply(x, challenge)).decision is op.Decision.UNKNOWN_EFFECT
    assert x.exchange.complete(123, status_reply(x, challenge)).reason is op.Reason.DUPLICATE
    with pytest.raises(ValueError):
        host.ConnectedStatusExchange(clock=lambda: T, monotonic=lambda: 100.0).begin(123, x.request)


def test_unknown_reservation_request_is_consumed_even_with_a_new_challenge(status_exchange):
    x = status_exchange
    x.exchange.begin(123, x.request)
    assert x.exchange.abandon().decision is op.Decision.UNKNOWN_EFFECT
    fresh = host.ConnectedStatusExchange(clock=lambda: T, monotonic=lambda: 100.0)
    with pytest.raises(ValueError): fresh.begin(123, x.request)
    with closing(sqlite3.connect(x.g3.broker.requests)) as db:
        assert db.execute("SELECT state,receipt FROM requests").fetchall() == [("UNKNOWN", None)]


@pytest.mark.parametrize("fault", [None, "impersonation", "server_end", "wrong_pipe", "wrong_client_pid",
    "session0", "wrong_session", "acl", "scm"])
def test_client_handle_authentication_uses_kernel_endpoint_and_scm_facts(g3, monkeypatch, fault):
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1008)
    monkeypatch.setattr(os, "getpid", lambda: g3.broker.client.pid)
    name = ctypes.create_unicode_buffer(b.PIPE + ("-foreign" if fault == "wrong_pipe" else ""))
    closed = []
    def api(dll, method, args, result):
        def call(*values):
            if method == "GetCurrentThread": return 777
            if method == "OpenThreadToken":
                if fault == "impersonation": values[3]._obj.value = 888
                return fault == "impersonation"
            if method == "CloseHandle": closed.append(values[0].value); return True
            if method == "GetNamedPipeInfo":
                values[1]._obj.value = 5 if fault == "server_end" else 4
                return True
            if method == "NtQueryObject":
                class UnicodeString(ctypes.Structure):
                    _fields_ = [("length", ob.wt.USHORT), ("maximum", ob.wt.USHORT), ("buffer", ctypes.c_void_p)]
                out = ctypes.cast(values[2], ctypes.POINTER(UnicodeString)).contents
                out.length = len(name.value) * 2; out.maximum = out.length + 2
                out.buffer = ctypes.addressof(name)
                return 0
            side = "Client" if "Client" in method else "Server"
            process = g3.broker.client if side == "Client" else g3.broker.server
            if method.endswith("ProcessId"):
                values[1]._obj.value = process.pid + (1 if fault == "wrong_client_pid" and side == "Client" else 0)
            elif method.endswith("SessionId"):
                values[1]._obj.value = process.session_id + (1 if fault == "wrong_session" and side == "Client" else 0)
            else: pytest.fail("unexpected client authentication API " + method)
            return True
        return call
    monkeypatch.setattr(b, "_api", api)
    monkeypatch.setattr(b, "read_process_identity", lambda pid: (
        replace(g3.broker.client, session_id=0) if fault == "session0" else g3.broker.client
        ) if pid == g3.broker.client.pid else g3.broker.server)
    monkeypatch.setattr(b, "_check_pipe_acl", lambda handle: jn.deny() if fault == "acl" else None)
    monkeypatch.setattr(b, "_scm_process", lambda: dict(g3.broker.facts["scm"],
        pid=g3.broker.server.pid + (1 if fault == "scm" else 0)))
    if fault:
        with pytest.raises(jn.NativeDenied): ob._desktop_context(124)
    else:
        context = ob._desktop_context(124)
        assert context[2:4] == (g3.broker.server, g3.broker.client)
    assert closed == ([888] if fault == "impersonation" else [])


# ---------------------------------------------------------------------------
# Slice (A): Two-SID independent broker + observer signing handoff tests
# ---------------------------------------------------------------------------


@pytest.fixture
def two_sid_handoff(status_exchange, monkeypatch):
    """Real independent scratch Ed25519 keys for broker SID and observer SID.

    Models three separate OS processes across two named pipes:
    - Broker service (service_sid(), Session 0, ServiceCustody)
    - Interactive desktop peer (operator SID, Session 7, NEVER reads observer key)
    - Observer signer (OBSERVER SID, ObserverCustody)
    """
    x = status_exchange
    g3 = x.g3
    # Broker uses its OWN enrolled private key, separate from observer's private key.
    g3.broker.anchor["private_key_path"] = str(g3.broker_crypto.key)
    observer_exe = g3.broker.f.storage.root / "WakeHubObserverSigner.exe"
    observer_exe.write_bytes(b"synthetic-observer-signer-image-v1")
    signer_server = inv.ProcessIdentity(3333, T - 45, str(observer_exe) if os.name == "nt" else ob.OBSERVER_SIGNER_IMAGE, OBSERVER, 2)
    observer_exec_path = signer_server.executable
    if os.name != "nt":
        orig_check = jn.WindowsPathCustody.check
        def mapped_check(self, path, *, directory=False):
            if str(path) == ob.OBSERVER_SIGNER_IMAGE:
                return observer_exe
            return orig_check(self, path, directory=directory)
        monkeypatch.setattr(jn.WindowsPathCustody, "check", mapped_check)
    signer_cfg = dict(
        schema="WakeHubG3ObserverSignerAnchor.v1",
        task_parent=en.TASK_PARENT,
        pipe=b.OBSERVER_SIGNER_PIPE,
        observer_sid=OBSERVER,
        observer_executable=observer_exec_path,
        observer_sha256=auth._digest(observer_exe.read_bytes()),
        observer_key_path=str(g3.broker.f.crypto.keys["observer"]),
    )
    monkeypatch.setattr(ob, "_read_observer_signer_anchor", lambda: signer_cfg)

    observer_custody_calls = []
    def checked_observer_custody(self, path, *, directory=False):
        observer_custody_calls.append((current_pid[0], self.owner_sid, str(path)))
        if self.owner_sid != OBSERVER:
            jn.deny()
        return Path(path)
    monkeypatch.setattr(b.ObserverCustody, "check", checked_observer_custody)

    current_pid = [g3.broker.server.pid]
    monkeypatch.setattr(os, "getpid", lambda: current_pid[0])
    identities = {
        g3.broker.server.pid: g3.broker.server,
        g3.broker.client.pid: g3.broker.client,
        signer_server.pid: signer_server,
    }
    monkeypatch.setattr(b, "read_process_identity", lambda pid: identities[pid] if pid in identities else jn.deny())

    signer_facts = dict(server=asdict(signer_server), client=asdict(g3.broker.client), scm=g3.broker.facts["scm"])
    monkeypatch.setattr(ob, "_observe_client_connection", lambda handle: g3.broker.facts)
    monkeypatch.setattr(ob, "_observe_observer_signer_connection",
                        lambda handle, side, *, observer_sid, operator_sid: signer_facts)

    # Multi-handle deterministic Win32 pipe ABI seam (handles 201/202 = broker pipe, 301/302 = signer pipe).
    now_mono = [100.0]
    monkeypatch.setattr(ob, "_SPENT_HANDLES", set())
    monkeypatch.setattr(ob, "_UNFINISHED_IO", [])
    pipes = {
        201: dict(side=ob._PipeSide.SERVER, peer=202, inbox=[], writes=[]),
        202: dict(side=ob._PipeSide.CLIENT, peer=201, inbox=[], writes=[]),
        301: dict(side=ob._PipeSide.SERVER, peer=302, inbox=[], writes=[]),
        302: dict(side=ob._PipeSide.CLIENT, peer=301, inbox=[], writes=[]),
    }
    closed = []
    error = [0]
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error[0])
    last_count = {}

    def api(dll, name, args, result):
        def call(*values):
            if name == "GetNamedPipeInfo":
                h = values[0]
                values[1]._obj.value = (1 if pipes[h]["side"] is ob._PipeSide.SERVER else 0) | 4
                return True
            if name == "GetNamedPipeHandleStateW":
                values[1]._obj.value = 2
                return True
            if name == "NtQueryInformationFile":
                values[2]._obj.value = 0
                return 0
            if name == "CreateEventW":
                return 900
            if name == "CloseHandle":
                closed.append(values[0])
                return True
            if name == "WriteFile":
                h = values[0]
                data = bytes(values[1].raw[:values[2]])
                pipes[h]["writes"].append(data)
                pipes[pipes[h]["peer"]]["inbox"].append(data)
                last_count[h] = len(data)
                return True
            if name == "ReadFile":
                h = values[0]
                if not pipes[h]["inbox"]:
                    error[0] = 109
                    return False
                data = pipes[h]["inbox"].pop(0)
                ctypes.memmove(values[1], data, len(data))
                last_count[h] = len(data)
                return True
            if name == "GetOverlappedResultEx":
                h = values[0]
                values[2]._obj.value = last_count[h]
                return True
            if name == "PeekNamedPipe":
                h = values[0]
                values[4]._obj.value = len(pipes[h]["inbox"][0]) if pipes[h]["inbox"] else 0
                return True
            if name == "CancelIoEx":
                return True
            pytest.fail("unexpected native API " + name)
        return call
    monkeypatch.setattr(b, "_api", api)

    return SimpleNamespace(
        x=x, g3=g3, signer_cfg=signer_cfg, signer_server=signer_server,
        signer_facts=signer_facts, current_pid=current_pid, identities=identities,
        pipes=pipes, closed=closed, now_mono=now_mono,
        observer_custody_calls=observer_custody_calls, observer_exe=observer_exe,
    )


def test_two_sid_broker_and_observer_signing_handoff_positive(two_sid_handoff):
    h = two_sid_handoff
    # Step 1: Service (service_sid(), Session 0) reserves & signs challenge with broker key.
    h.current_pid[0] = h.g3.broker.server.pid
    broker_io = host.ConnectedStatusIO(clock=lambda: h.g3.clock[0], monotonic=lambda: h.now_mono[0])
    challenge = broker_io.begin(201, h.x.request)
    signed_challenge = host.sign_broker_challenge(
        201, challenge, deadline=broker_io._exchange._deadline, clock=broker_io._exchange.clock,
    )
    offer_raw = broker_io._exchange.offer(201, signed_challenge)
    broker_io._pipe.write_frame(offer_raw)

    # Step 2: Interactive desktop peer (operator SID) receives challenge & sends sign request.
    h.current_pid[0] = h.g3.broker.client.pid
    desktop_peer = ob.ConnectedDesktopStatusIO(clock=lambda: h.g3.clock[0], monotonic=lambda: h.now_mono[0])
    transcript = desktop_peer.receive(202)
    req_frame = ob.ObserverSignRequestFrame(
        desktop_peer._signed_challenge, desktop_peer._observation, auth._digest(transcript),
    )
    # Pre-queue client request on signer pipe server inbox and execute observer-SID signer host.
    h.pipes[301]["inbox"].append(req_frame.to_bytes())
    assert h.observer_custody_calls == []  # Operator SID NEVER touched observer key custody.

    # Step 3: Observer signer (OBSERVER SID) authenticates & signs with ObserverCustody key.
    h.current_pid[0] = h.signer_server.pid
    signer_host = ob.ConnectedObserverSignerHost(clock=lambda: h.g3.clock[0], monotonic=lambda: h.now_mono[0])
    assert signer_host.handle_connection(301) is ob.DesktopStatusOutcome.RESPONSE_WRITTEN
    assert len(h.pipes[301]["writes"]) == 1
    assert all(pid == h.signer_server.pid and sid == OBSERVER for pid, sid, _ in h.observer_custody_calls)

    # Step 4: Interactive desktop peer receives signed frame from observer signer & replies to broker.
    h.current_pid[0] = h.g3.broker.client.pid
    before_client_calls = len(h.observer_custody_calls)
    sign_client = ob.ConnectedObserverSignClient(clock=lambda: h.g3.clock[0])
    signed_status = sign_client.request_signature(
        302,
        signed_challenge=desktop_peer._signed_challenge,
        observation=desktop_peer._observation,
        expected=desktop_peer._challenge,
        deadline=desktop_peer._deadline,
    )
    assert len(h.observer_custody_calls) == before_client_calls  # Client side NEVER reads observer key.
    assert desktop_peer.respond(signed_status) is ob.DesktopStatusOutcome.RESPONSE_WRITTEN

    # Step 5: Service reads signed observer status frame and completes G3 STATUS exchange.
    h.current_pid[0] = h.g3.broker.server.pid
    reply = broker_io._pipe.read_frame()
    receipt = broker_io._exchange.complete(201, reply)
    broker_io._pipe.close()
    assert receipt.operation is op.Operation.STATUS
    assert receipt.decision is op.Decision.STATUS_OBSERVED
    assert receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert h.g3.rows()[0][1] == "DONE"
    assert desktop_peer.abandon() is ob.DesktopStatusOutcome.UNKNOWN
    for secret in (SID, OBSERVER, JID, challenge.nonce, str(h.g3.broker_crypto.key),
                   str(h.g3.broker.f.crypto.keys["observer"])):
        assert secret not in receipt.to_json()


def test_broker_and_desktop_status_owners_end_to_end_single_shot(two_sid_handoff, monkeypatch):
    h = two_sid_handoff
    # Run BrokerStatusOwner end-to-end by driving DesktopStatusOwner + ObserverSignerHost on offer write.
    orig_write = ob._StatusPipeIO.write_frame
    def intercept_offer(self, raw):
        orig_write(self, raw)
        if self.handle == 201:
            # Intercept when broker writes offer -> drive desktop peer + observer signer.
            h.current_pid[0] = h.g3.broker.client.pid
            peer_owner = ob.DesktopStatusOwner(clock=lambda: h.g3.clock[0], monotonic=lambda: h.now_mono[0])
            # When client writes request to signer pipe 302, immediately run observer signer host on 301.
            orig_wmsg = ob._StatusPipeIO.write_message
            def intercept_sign_req(pipe_self, data, validator):
                orig_wmsg(pipe_self, data, validator)
                if pipe_self.handle == 302:
                    saved = h.current_pid[0]
                    h.current_pid[0] = h.signer_server.pid
                    signer_host = ob.ConnectedObserverSignerHost(
                        clock=lambda: h.g3.clock[0], monotonic=lambda: h.now_mono[0],
                    )
                    assert signer_host.handle_connection(301) is ob.DesktopStatusOutcome.RESPONSE_WRITTEN
                    h.current_pid[0] = saved
            monkeypatch.setattr(ob._StatusPipeIO, "write_message", intercept_sign_req)
            assert peer_owner.execute(202, 302) is ob.DesktopStatusOutcome.RESPONSE_WRITTEN
            assert peer_owner.execute(202, 302) is ob.DesktopStatusOutcome.DUPLICATE
            h.current_pid[0] = h.g3.broker.server.pid
    monkeypatch.setattr(ob._StatusPipeIO, "write_frame", intercept_offer)

    h.current_pid[0] = h.g3.broker.server.pid
    owner = host.BrokerStatusOwner(clock=lambda: h.g3.clock[0], monotonic=lambda: h.now_mono[0])
    receipt = owner.execute(201, h.x.request)
    assert receipt.operation is op.Operation.STATUS
    assert receipt.decision is op.Decision.STATUS_OBSERVED
    assert receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert owner.execute(201, h.x.request).reason is op.Reason.DUPLICATE
    assert h.g3.rows()[0][1] == "DONE"
    # Verify observer custody was ONLY checked under observer signer PID, never under operator or service PID.
    assert h.observer_custody_calls
    assert all(pid == h.signer_server.pid for pid, _, _ in h.observer_custody_calls)


@pytest.mark.parametrize("fault", [
    "non_service_sid", "session_nonzero", "wrong_pid", "wrong_key_custody",
    "expired_nonce", "monotonic_expired", "wrong_generation", "wrong_source",
    "wrong_logon", "wrong_key_material", "unbound_challenge",
])
def test_broker_signing_handoff_denies_invalid_identity_or_expiry(two_sid_handoff, monkeypatch, fault):
    h = two_sid_handoff
    h.current_pid[0] = h.g3.broker.server.pid
    broker_io = host.ConnectedStatusIO(clock=lambda: h.g3.clock[0], monotonic=lambda: h.now_mono[0])
    challenge = broker_io.begin(201, h.x.request)
    if fault == "non_service_sid":
        h.identities[h.g3.broker.server.pid] = replace(h.g3.broker.server, user_sid=SID)
    elif fault == "session_nonzero":
        h.identities[h.g3.broker.server.pid] = replace(h.g3.broker.server, session_id=1)
    elif fault == "wrong_pid":
        h.current_pid[0] = h.g3.broker.client.pid
    elif fault == "wrong_key_custody":
        monkeypatch.setattr(b.ServiceCustody, "check", lambda self, p, **kw: jn.deny() if str(p) == str(h.g3.broker_crypto.key) else Path(p))
    elif fault == "expired_nonce":
        h.g3.clock[0] = T + 5
    elif fault == "monotonic_expired":
        h.now_mono[0] = 105.0
    elif fault == "wrong_generation":
        challenge = replace(challenge, generation=challenge.generation + 1)
    elif fault == "wrong_source":
        challenge = replace(challenge, source=replace(SOURCE, release_epoch=SOURCE.release_epoch + 1))
    elif fault == "wrong_logon":
        challenge = replace(challenge, logon_id="0000000000099999")
    elif fault == "wrong_key_material":
        # Point broker private key to observer's key -> signature fails broker_signers verification!
        h.g3.broker.anchor["private_key_path"] = str(h.g3.broker.f.crypto.keys["observer"])
    elif fault == "unbound_challenge":
        challenge = replace(challenge, request_sha256=None)
    with pytest.raises(jn.NativeDenied):
        host.sign_broker_challenge(
            201, challenge, deadline=broker_io._exchange._deadline, clock=broker_io._exchange.clock,
        )
    assert broker_io.abandon().decision is op.Decision.UNKNOWN_EFFECT
    assert h.g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("fault", [
    "observer_sid_equals_operator", "observer_sid_equals_service", "bad_observer_image_hash",
    "wrong_client_sid", "wrong_client_logon", "bad_observer_key_custody",
    "forged_broker_challenge", "wrong_transcript_hash", "future_observed_at",
    "expired_nonce", "replayed_nonce", "wrong_generation", "wrong_source",
    "shared_key_path_with_broker", "wrong_observer_key_material",
])
def test_observer_signer_ipc_denies_forged_or_mismatched_handoff(two_sid_handoff, monkeypatch, fault):
    h = two_sid_handoff
    # Build a valid broker-signed challenge and desktop observation first.
    h.current_pid[0] = h.g3.broker.server.pid
    challenge = h.x.exchange.begin(123, h.x.request)
    signed_challenge = h.g3.broker_crypto.signed(challenge.body())
    obs = ob.observation_body(
        challenge, now=T, process=h.g3.broker.client,
        logon_id=h.g3.config["logon_id"], source=SOURCE, generation=1,
    )
    transcript = auth.signing_bytes(obs)
    req_raw = ob.ObserverSignRequestFrame(signed_challenge, obs, auth._digest(transcript)).to_bytes()

    signer_host = ob.ConnectedObserverSignerHost(clock=lambda: h.g3.clock[0], monotonic=lambda: h.now_mono[0])
    if fault == "replayed_nonce":
        # First signing succeeds on handle 301; second attempt with same nonce returns DUPLICATE.
        h.current_pid[0] = h.signer_server.pid
        h.pipes[301]["inbox"].append(req_raw)
        assert signer_host.handle_connection(301) is ob.DesktopStatusOutcome.RESPONSE_WRITTEN
        h.pipes[303] = dict(side=ob._PipeSide.SERVER, peer=302, inbox=[req_raw], writes=[])
        assert signer_host.handle_connection(303) is ob.DesktopStatusOutcome.DUPLICATE
        assert h.pipes[303]["writes"] == []
        return

    if fault == "observer_sid_equals_operator":
        h.signer_cfg["observer_sid"] = SID
    elif fault == "observer_sid_equals_service":
        h.signer_cfg["observer_sid"] = b.service_sid()
    elif fault == "bad_observer_image_hash":
        h.signer_cfg["observer_sha256"] = "0" * 64
    elif fault == "wrong_client_sid":
        h.signer_facts["client"]["user_sid"] = OBSERVER
    elif fault == "wrong_client_logon":
        monkeypatch.setattr(ob, "read_logon_id", lambda pid: "0000000000099999")
    elif fault == "bad_observer_key_custody":
        monkeypatch.setattr(b.ObserverCustody, "check", lambda *a, **kw: jn.deny())
    elif fault == "forged_broker_challenge":
        forged_sc = h.g3.broker.f.crypto.signed(challenge.body(), "issuer", "grant", namespace=ob.CHALLENGE_NAMESPACE)
        req_raw = ob.ObserverSignRequestFrame(forged_sc, obs, auth._digest(transcript)).to_bytes()
    elif fault == "wrong_transcript_hash":
        payload = jn.decode(req_raw[4:])
        payload["transcript_sha256"] = "0" * 64
        body_bytes = jn.canonical(payload)
        req_raw = len(body_bytes).to_bytes(4, "big") + body_bytes
    elif fault == "future_observed_at":
        future_obs = dict(obs, observed_at=T + 1)
        req_raw = ob.ObserverSignRequestFrame(
            signed_challenge, future_obs, auth._digest(auth.signing_bytes(future_obs)),
        ).to_bytes()
    elif fault == "expired_nonce":
        h.g3.clock[0] = T + 5
    elif fault == "wrong_generation":
        h.g3.config["generation"] = 2
    elif fault == "wrong_source":
        h.g3.config["source"]["release_epoch"] = 2
    elif fault == "shared_key_path_with_broker":
        h.signer_cfg["observer_key_path"] = h.g3.broker.anchor["private_key_path"]
    elif fault == "wrong_observer_key_material":
        h.signer_cfg["observer_key_path"] = str(h.g3.broker.f.crypto.keys["issuer"])

    h.current_pid[0] = h.signer_server.pid
    h.pipes[301]["inbox"].append(req_raw)
    assert signer_host.handle_connection(301) is ob.DesktopStatusOutcome.UNKNOWN
    assert h.pipes[301]["writes"] == []


@pytest.mark.parametrize("bad_sid", [b.service_sid(), "S-1-5-18", "S-1-5-32-544", "not-a-sid"])
def test_observer_custody_rejects_invalid_sids(bad_sid):
    with pytest.raises(jn.NativeDenied):
        b.ObserverCustody(bad_sid)


@pytest.mark.parametrize("fault", [
    None, "operator_read_ace", "service_read_ace", "everyone_ace",
    "unprotected_dacl", "wrong_owner", "missing_observer_read",
])
def test_observer_custody_enforces_protected_dacl_and_excludes_operator(monkeypatch, tmp_path, fault):
    key_file = tmp_path / "observer_ed25519"
    key_file.write_bytes(b"placeholder")
    monkeypatch.setattr(jn.WindowsPathCustody, "check", lambda self, p, **kw: Path(p))
    owner = SID if fault == "wrong_owner" else OBSERVER
    aces = [(0, 0, 0x1f01ff, "S-1-5-18")]
    if fault != "missing_observer_read":
        aces.append((0, 0, 0x80000000, OBSERVER))
    if fault == "operator_read_ace":
        aces.append((0, 0, 0x80000000, SID))
    elif fault == "service_read_ace":
        aces.append((0, 0, 0x80000000, b.service_sid()))
    elif fault == "everyone_ace":
        aces.append((0, 0, 1, "S-1-1-0"))
    monkeypatch.setattr(jn.WindowsPathCustody, "_descriptor", lambda *a, **kw: (owner, aces))
    monkeypatch.setattr(b, "_protected_dacl", lambda *a, **kw: jn.deny() if fault == "unprotected_dacl" else None)
    custody = b.ObserverCustody(OBSERVER)
    if fault:
        with pytest.raises(jn.NativeDenied):
            custody.check(key_file)
    else:
        assert custody.check(key_file) == key_file


@pytest.mark.parametrize("fault", [
    None, "same_sid", "create_instance", "write_dac", "write_owner",
    "generic_all", "generic_write", "service_ace", "foreign_ace",
    "unprotected_dacl", "wrong_owner",
])
def test_observer_signer_pipe_acl_enforces_exact_endpoint_isolation(monkeypatch, fault):
    owner = SID if fault == "wrong_owner" else OBSERVER
    op_mask = {
        "create_instance": 4, "write_dac": 0x40000, "write_owner": 0x80000,
        "generic_all": 0x10000000, "generic_write": 0x40000000,
    }.get(fault, 3)
    aces = [(0, 0, 0x1f01ff, OBSERVER), (0, 0, op_mask, SID)]
    if fault == "service_ace":
        aces.append((0, 0, 3, b.service_sid()))
    elif fault == "foreign_ace":
        aces.append((0, 0, 3, "S-1-5-21-111-222-333-9999"))
    monkeypatch.setattr(jn.WindowsPathCustody, "_descriptor", lambda *a, **kw: (owner, aces))
    monkeypatch.setattr(b, "_protected_dacl", lambda *a, **kw: jn.deny() if fault == "unprotected_dacl" else None)
    if fault:
        with pytest.raises(jn.NativeDenied):
            b._check_observer_signer_pipe_acl(301, SID if fault == "same_sid" else OBSERVER, SID)
    else:
        b._check_observer_signer_pipe_acl(301, OBSERVER, SID)


@pytest.mark.parametrize("side", [ob._PipeSide.SERVER, ob._PipeSide.CLIENT])
@pytest.mark.parametrize("fault", [
    None, "impersonation", "wrong_side", "wrong_pipe", "wrong_self_pid",
    "wrong_observer_sid", "wrong_operator_sid", "session0_client", "same_pid", "bad_acl",
])
def test_observer_signer_kernel_pipe_observation_both_sides(g3, monkeypatch, side, fault):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1008)
    signer_server = inv.ProcessIdentity(3333, T - 45, ob.OBSERVER_SIGNER_IMAGE, OBSERVER, 2)
    self_pid = signer_server.pid if side is ob._PipeSide.SERVER else g3.broker.client.pid
    monkeypatch.setattr(os, "getpid", lambda: self_pid + (1 if fault == "wrong_self_pid" else 0))
    pipe_name = ctypes.create_unicode_buffer(b.OBSERVER_SIGNER_PIPE + ("-bad" if fault == "wrong_pipe" else ""))
    closed = []

    def api(dll, method, args, result):
        def call(*values):
            if method == "GetCurrentThread":
                return 777
            if method == "OpenThreadToken":
                if fault == "impersonation":
                    values[3]._obj.value = 888
                return fault == "impersonation"
            if method == "CloseHandle":
                closed.append(values[0].value)
                return True
            if method == "GetNamedPipeInfo":
                is_server = (side is ob._PipeSide.SERVER) ^ (fault == "wrong_side")
                values[1]._obj.value = (1 if is_server else 0) | 4
                return True
            if method == "NtQueryObject":
                class UnicodeString(ctypes.Structure):
                    _fields_ = [("length", ob.wt.USHORT), ("maximum", ob.wt.USHORT), ("buffer", ctypes.c_void_p)]
                out = ctypes.cast(values[2], ctypes.POINTER(UnicodeString)).contents
                out.length = len(pipe_name.value) * 2
                out.maximum = out.length + 2
                out.buffer = ctypes.addressof(pipe_name)
                return 0
            is_client = "Client" in method
            proc = g3.broker.client if is_client else signer_server
            if method.endswith("ProcessId"):
                values[1]._obj.value = signer_server.pid if fault == "same_pid" else proc.pid
            elif method.endswith("SessionId"):
                values[1]._obj.value = 0 if (fault == "session0_client" and is_client) else proc.session_id
            else:
                pytest.fail("unexpected API " + method)
            return True
        return call

    monkeypatch.setattr(b, "_api", api)
    monkeypatch.setattr(b, "_check_observer_signer_pipe_acl",
                        lambda *a, **kw: jn.deny() if fault == "bad_acl" else None)
    def read_id(pid):
        if pid == signer_server.pid:
            return replace(signer_server, user_sid=SID) if fault == "wrong_observer_sid" else signer_server
        if pid == g3.broker.client.pid:
            if fault == "wrong_operator_sid":
                return replace(g3.broker.client, user_sid=OBSERVER)
            if fault == "session0_client":
                return replace(g3.broker.client, session_id=0)
            return g3.broker.client
        jn.deny()
    monkeypatch.setattr(b, "read_process_identity", read_id)
    monkeypatch.setattr(b, "_scm_process", lambda: g3.broker.facts["scm"])

    if fault:
        with pytest.raises(jn.NativeDenied):
            ob._observe_observer_signer_connection(301, side, observer_sid=OBSERVER, operator_sid=SID)
    else:
        observed = ob._observe_observer_signer_connection(301, side, observer_sid=OBSERVER, operator_sid=SID)
        assert observed["server"]["user_sid"] == OBSERVER
        assert observed["client"]["user_sid"] == SID
    assert closed == ([888] if fault == "impersonation" else [])


# ---------------------------------------------------------------------------
# Slice (B): Admin-attended SCM PID/creation-time binding & readback tests
# ---------------------------------------------------------------------------


@pytest.fixture
def scm_binding_env(g3, monkeypatch):
    """Deterministic admin-attended enrollment/readback fixture with simulated HKLM."""
    root = g3.broker.f.storage.root
    service_bin = root / "WakeHubBroker.exe"
    service_bin.write_bytes(b"preinstalled-wakehub-broker-binary-v1")
    observer_bin = root / "WakeHubObserverSigner.exe"
    observer_bin.write_bytes(b"preinstalled-wakehub-observer-binary-v1")
    desktop_bin = root / "WakeHubDesktopPeer.exe"
    desktop_bin.write_bytes(b"preinstalled-wakehub-desktop-binary-v1")

    service_img_str = str(service_bin) if os.name == "nt" else en.SERVICE_IMAGE
    observer_img_str = str(observer_bin) if os.name == "nt" else ob.OBSERVER_SIGNER_IMAGE
    desktop_img_str = str(desktop_bin) if os.name == "nt" else en.DESKTOP_IMAGE
    monkeypatch.setattr(en, "SERVICE_IMAGE", service_img_str)
    monkeypatch.setattr(en, "CHALLENGES_PATH", str(g3.path))
    monkeypatch.setattr(host, "CHALLENGES_PATH", str(g3.path))

    if os.name != "nt":
        orig_check = jn.WindowsPathCustody.check
        virtual = {
            service_img_str: service_bin,
            observer_img_str: observer_bin,
            desktop_img_str: desktop_bin,
        }
        def mapped_check(self, path, *, directory=False):
            if str(path) in virtual:
                return virtual[str(path)]
            return orig_check(self, path, directory=directory)
        monkeypatch.setattr(jn.WindowsPathCustody, "check", mapped_check)

    monkeypatch.setattr(b, "_check_admin_token", lambda: None)
    monkeypatch.setattr(b.ObserverCustody, "check", lambda self, p, **kw: Path(p))

    pins = en.EnrollmentPins(
        SOURCE, 1, SID, OBSERVER, (SID,),
        auth._digest(service_bin.read_bytes()),
        auth._digest(observer_bin.read_bytes()),
        g3.broker.f.anchor["issuer_signers"]["sha256"],
        g3.broker.f.anchor["human_signers"]["sha256"],
        g3.broker.f.anchor["observer_signers"]["sha256"],
        g3.config["broker_signers"]["sha256"],
        JID,
    )
    desktop_peer = inv.ProcessIdentity(2222, T - 50, desktop_img_str, SID, 7)
    spec = en.AttendedScmBindingSpec(
        pins=pins,
        target=TARGET,
        desktop_peer=desktop_peer,
        desktop_peer_sha256=auth._digest(desktop_bin.read_bytes()),
        logon_id=g3.config["logon_id"],
        verifier_path=g3.broker.f.anchor["verifier"]["path"],
        verifier_sha256=g3.broker.f.anchor["verifier"]["sha256"],
        issuer_signers_path=g3.broker.f.anchor["issuer_signers"]["path"],
        human_signers_path=g3.broker.f.anchor["human_signers"]["path"],
        observer_signers_path=g3.broker.f.anchor["observer_signers"]["path"],
        broker_signers_path=g3.config["broker_signers"]["path"],
        enrollment_path=g3.broker.f.anchor["enrollment"],
        journal_path=g3.broker.f.anchor["journal_path"],
        requests_path=g3.broker.anchor["requests_path"],
        challenges_path=str(g3.path),
        private_key_path=str(g3.broker_crypto.key),
        desktop_path=g3.broker.anchor["desktop_path"],
        observer_executable=observer_img_str,
        observer_key_path=str(g3.broker.f.crypto.keys["observer"]),
    )

    live_state = dict(
        server=inv.ProcessIdentity(4100, T - 30, service_img_str, b.service_sid(), 0),
        scm=dict(pid=4100, binary=service_img_str, account="NT SERVICE\\" + b.SERVICE, kind=0x10, sid_type=1),
    )
    registry = {}
    writes = []

    def write_anchor(subkey, value_name, payload):
        raw = jn.canonical(payload)
        writes.append((subkey, value_name))
        registry[(subkey, value_name)] = raw
        return raw

    def read_reg(subkey, value_name):
        if (subkey, value_name) not in registry:
            raise FileNotFoundError(value_name)
        return jn.decode(registry[(subkey, value_name)])

    monkeypatch.setattr(b, "_write_protected_anchor_value", write_anchor)
    monkeypatch.setattr(b, "_read_broker_anchor", lambda: read_reg(b.ANCHOR, "Anchor"))
    monkeypatch.setattr(host, "_read_g3_config", lambda: read_reg(en.G3_ANCHOR, "Anchor"))
    monkeypatch.setattr(ob, "_read_observer_signer_anchor",
                        lambda: read_reg(en.G3_ANCHOR, ob.OBSERVER_SIGNER_ANCHOR_VALUE))
    monkeypatch.setattr(b, "_scm_process", lambda: dict(live_state["scm"]))
    monkeypatch.setattr(b, "read_process_identity",
                        lambda pid: live_state["server"] if pid == live_state["server"].pid else jn.deny())

    return SimpleNamespace(
        g3=g3, spec=spec, pins=pins, live_state=live_state, registry=registry,
        writes=writes, service_bin=service_bin, observer_bin=observer_bin,
        desktop_bin=desktop_bin, service_img_str=service_img_str, root=root,
    )


def test_attended_scm_pid_binding_binds_only_after_independent_readback(scm_binding_env):
    env = scm_binding_env
    # Before binding, readback fails closed to HOLD_RECONCILE with no bound PID.
    before = en.collect_live_scm_readback(env.spec, clock=lambda: T)
    assert before.state is en.ScmBindingState.HOLD_RECONCILE
    assert before.bound_pid is None and before.bound_created_at is None

    # Initial attended binding writes all 3 protected anchors and verifies independent readback.
    bound = en.attend_scm_pid_binding(env.spec, clock=lambda: T)
    assert bound.state is en.ScmBindingState.PID_BOUND_READBACK_VERIFIED
    assert bound.bound_pid == 4100
    assert bound.bound_created_at == T - 30
    assert bound.retry_allowed is False
    assert len(env.writes) == 3

    # Sanitized public JSON never leaks PID, SID, logon, path, or digest and never issues Wake/Start readiness.
    public_text = bound.to_public_json()
    public = json.loads(public_text)
    assert public["state"] == "PID_BOUND_AFTER_INDEPENDENT_READBACK"
    assert public["pid_bound"] is True and public["readback_verified"] is True
    assert public["retry_allowed"] is False
    assert public["native_effect"] == "NOT_ATTEMPTED"
    assert public["start"] == "DENY" and public["reuse"] == "DENY"
    assert public["wake"] == "NOT_SENT" and public["chatgpt_account_session"] == "ACCOUNT_UNPROVEN"
    for secret in ("4100", SID, OBSERVER, JID, env.spec.logon_id, env.service_img_str, bound.anchor_digest):
        assert secret not in public_text

    # Independent read-only readback now succeeds without performing any writes.
    rb = en.collect_live_scm_readback(env.spec, clock=lambda: T)
    assert rb.state is en.ScmBindingState.READBACK_VERIFIED
    assert rb.bound_pid == 4100 and rb.bound_created_at == T - 30
    assert len(env.writes) == 3

    # Simulated SCM service restart: new dynamic PID 5200 & creation time T-5.
    env.live_state["server"] = inv.ProcessIdentity(5200, T - 5, env.service_img_str, b.service_sid(), 0)
    env.live_state["scm"]["pid"] = 5200
    # Stale readback fails closed to HOLD_RECONCILE.
    stale = en.collect_live_scm_readback(env.spec, clock=lambda: T)
    assert stale.state is en.ScmBindingState.HOLD_RECONCILE and stale.bound_pid is None
    # Attended re-binding updates dynamic PID only after independent readback.
    rebound = en.attend_scm_pid_binding(env.spec, clock=lambda: T)
    assert rebound.state is en.ScmBindingState.PID_BOUND_READBACK_VERIFIED
    assert rebound.bound_pid == 5200 and rebound.bound_created_at == T - 5


@pytest.mark.parametrize("fault", [
    "non_admin", "ci_env", "bad_service_hash", "bad_observer_hash", "bad_signer_hash",
    "unsigned_release", "kill_switch", "wrong_scm_account", "interactive_scm_kind",
    "wrong_scm_binary", "service_session_nonzero", "bad_observer_custody",
    "partial_existing_anchor", "drifted_existing_generation", "drifted_existing_source",
    "pid_changed_before_write", "write_error_ambiguous", "postwrite_readback_mismatch",
    "postwrite_pid_changed", "postwrite_unprotected_dacl",
])
def test_attended_scm_pid_binding_holds_reconcile_on_any_fault(scm_binding_env, monkeypatch, fault):
    env = scm_binding_env
    if fault == "non_admin":
        monkeypatch.setattr(b, "_check_admin_token", jn.deny)
    elif fault == "ci_env":
        monkeypatch.setattr(auth, "windows_only", jn.deny)
    elif fault == "bad_service_hash":
        env.service_bin.write_bytes(b"tampered-service-binary")
    elif fault == "bad_observer_hash":
        env.observer_bin.write_bytes(b"tampered-observer-binary")
    elif fault == "bad_signer_hash":
        env.spec = replace(env.spec, pins=replace(env.pins, broker_key="0" * 64))
    elif fault == "unsigned_release":
        Path(env.spec.enrollment_path).write_bytes(b'{"body":{},"signature":"forged"}')
    elif fault == "kill_switch":
        (env.root / "KILL").write_text("STOP")
    elif fault == "wrong_scm_account":
        env.live_state["scm"]["account"] = "LocalSystem"
    elif fault == "interactive_scm_kind":
        env.live_state["scm"]["kind"] = 0x110  # SERVICE_WIN32_OWN_PROCESS | SERVICE_INTERACTIVE_PROCESS
    elif fault == "wrong_scm_binary":
        env.live_state["scm"]["binary"] = r"C:\Foreign\evil.exe"
    elif fault == "service_session_nonzero":
        env.live_state["server"] = replace(env.live_state["server"], session_id=1)
    elif fault == "bad_observer_custody":
        monkeypatch.setattr(b.ObserverCustody, "check", lambda *a, **kw: jn.deny())
    elif fault == "partial_existing_anchor":
        env.registry[(b.ANCHOR, "Anchor")] = b'{"partial":true}'
    elif fault in ("drifted_existing_generation", "drifted_existing_source"):
        assert en.attend_scm_pid_binding(env.spec, clock=lambda: T).state is en.ScmBindingState.PID_BOUND_READBACK_VERIFIED
        env.writes.clear()
        g3_existing = jn.decode(env.registry[(en.G3_ANCHOR, "Anchor")])
        if fault == "drifted_existing_generation":
            g3_existing["generation"] = 99
        else:
            g3_existing["source"]["release_epoch"] = 99
        env.registry[(en.G3_ANCHOR, "Anchor")] = jn.canonical(g3_existing)
    elif fault == "pid_changed_before_write":
        calls = [0]
        def changing_scm():
            calls[0] += 1
            return dict(env.live_state["scm"], pid=4100 if calls[0] == 1 else 4199)
        monkeypatch.setattr(b, "_scm_process", changing_scm)
    elif fault == "write_error_ambiguous":
        def failing_write(subkey, value_name, payload):
            env.writes.append((subkey, value_name))
            if len(env.writes) == 2:
                raise OSError("simulated registry flush error")
            raw = jn.canonical(payload)
            env.registry[(subkey, value_name)] = raw
            return raw
        monkeypatch.setattr(b, "_write_protected_anchor_value", failing_write)
    elif fault == "postwrite_readback_mismatch":
        orig_read = host._read_g3_config
        def tampered_read():
            data = orig_read()
            if env.writes:
                data["generation"] += 1
            return data
        monkeypatch.setattr(host, "_read_g3_config", tampered_read)
    elif fault == "postwrite_pid_changed":
        def post_change_scm():
            if env.writes:
                return dict(env.live_state["scm"], pid=4999)
            return dict(env.live_state["scm"])
        monkeypatch.setattr(b, "_scm_process", post_change_scm)
    elif fault == "postwrite_unprotected_dacl":
        orig_read_broker = b._read_broker_anchor
        def denied_after_write():
            if env.writes:
                jn.deny()
            return orig_read_broker()
        monkeypatch.setattr(b, "_read_broker_anchor", denied_after_write)

    result = en.attend_scm_pid_binding(env.spec, clock=lambda: T)
    assert result.state is en.ScmBindingState.HOLD_RECONCILE
    assert result.bound_pid is None and result.bound_created_at is None
    assert result.retry_allowed is False
    public = json.loads(result.to_public_json())
    assert public["state"] == "HOLD_RECONCILE_ONLY"
    assert public["pid_bound"] is False and public["readback_verified"] is False
    assert public["retry_allowed"] is False
    if fault not in ("write_error_ambiguous", "postwrite_readback_mismatch", "postwrite_pid_changed", "postwrite_unprotected_dacl"):
        assert env.writes == []


@pytest.mark.parametrize("fault", [None, "impersonation", "non_primary", "unelevated", "not_admin_member"])
def test_admin_token_check_requires_elevated_primary_admin_without_impersonation(monkeypatch, fault):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1008)
    closed, freed = [], []

    def api(dll, name, args, result):
        def call(*values):
            if name == "GetCurrentThread":
                return 701
            if name == "GetCurrentProcess":
                return 702
            if name == "OpenThreadToken":
                if fault == "impersonation":
                    values[3]._obj.value = 801
                return fault == "impersonation"
            if name == "OpenProcessToken":
                values[2]._obj.value = 802
                return True
            if name == "GetTokenInformation":
                kind = values[1]
                values[4]._obj.value = 4
                if kind == 8:
                    values[2]._obj.value = 2 if fault == "non_primary" else 1
                elif kind == 20:
                    values[2]._obj.value = 0 if fault == "unelevated" else 1
                else:
                    pytest.fail("unexpected token info kind")
                return True
            if name == "ConvertStringSidToSidW":
                assert values[0] == "S-1-5-32-544"
                values[1]._obj.value = 903
                return True
            if name == "CheckTokenMembership":
                values[2]._obj.value = 0 if fault == "not_admin_member" else 1
                return True
            if name == "CloseHandle":
                closed.append(getattr(values[0], "value", values[0]))
                return True
            if name == "LocalFree":
                freed.append(getattr(values[0], "value", values[0]))
                return 0
            pytest.fail("unexpected admin API " + name)
        return call

    monkeypatch.setattr(b, "_api", api)
    if fault:
        with pytest.raises(jn.NativeDenied):
            b._check_admin_token()
    else:
        b._check_admin_token()
    if fault == "impersonation":
        assert closed == [801] and freed == []
    else:
        assert closed == [802]
        assert freed == ([903] if fault in (None, "not_admin_member") else [])


def test_g3_cli_entrypoints_fail_closed_with_sanitized_json(capsys):
    for fn, argv in (
        (host.main, ["--mode", "preflight"]),
        (ob.main, ["--mode", "preflight"]),
        (ob.main, ["--mode", "signer"]),
        (en.main, ["--mode", "preflight"]),
        (en.main, ["--mode", "bind-scm-pid"]),
    ):
        rc = fn(argv)
        assert rc == 2
        out = json.loads(capsys.readouterr().out)
        assert out.get("start", "DENY") == "DENY"
        assert out.get("native_effect", "NOT_ATTEMPTED") == "NOT_ATTEMPTED"


# ---------------------------------------------------------------------------
# Windows-only native tests (read-only live probes + fail-closed without host assets)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native G3 custody tests")
def test_windows_native_process_identity_and_logon_luid_live(monkeypatch):
    ident = b.read_process_identity(os.getpid())
    assert ident.pid == os.getpid()
    assert ident.user_sid.startswith("S-1-5-")
    assert ident.created_at > 0
    assert Path(ident.executable).is_file()
    # Read-only Win32 primary-token AuthenticationId probe.
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    luid = ob.read_logon_id(os.getpid())
    assert len(luid) == 16 and luid != "0" * 16 and all(c in "0123456789abcdef" for c in luid)
    with pytest.raises(jn.NativeDenied):
        b.read_process_identity(0)
    with pytest.raises(jn.NativeDenied):
        ob.read_logon_id(0)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native G3 custody tests")
def test_windows_native_owners_fail_closed_without_enrolled_host_assets(pins):
    # Without an installed SCM service, protected HKLM anchors, or enrolled keys,
    # every callable Windows owner fails closed cleanly without side effects.
    svc_receipt = host.BrokerStatusOwner().execute(999999, b"{}")
    assert svc_receipt.decision is op.Decision.HOLD
    assert svc_receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert ob.ConnectedObserverSignerHost().handle_connection(999999) is ob.DesktopStatusOutcome.UNKNOWN
    assert ob.DesktopStatusOwner().execute(999999, 999998) is ob.DesktopStatusOutcome.UNKNOWN
    spec = en.AttendedScmBindingSpec(
        pins=pins,
        target=TARGET,
        desktop_peer=inv.ProcessIdentity(2222, T - 50, en.DESKTOP_IMAGE, SID, 7),
        desktop_peer_sha256="7" * 64,
        logon_id="0000000000012345",
        verifier_path=r"C:\Windows\System32\OpenSSH\ssh-keygen.exe",
        verifier_sha256="8" * 64,
        issuer_signers_path=r"C:\ProgramData\AI-Commerce\WakeHubG3\issuer-signers",
        human_signers_path=r"C:\ProgramData\AI-Commerce\WakeHubG3\human-signers",
        observer_signers_path=r"C:\ProgramData\AI-Commerce\WakeHubG3\observer-signers",
        broker_signers_path=r"C:\ProgramData\AI-Commerce\WakeHubG3\broker-signers",
        enrollment_path=r"C:\ProgramData\AI-Commerce\WakeHubG3\enrollment.json",
        journal_path=en.PRIVATE_ROOT + r"\effects.sqlite3",
        requests_path=en.PRIVATE_ROOT + r"\requests.sqlite3",
        challenges_path=en.CHALLENGES_PATH,
        private_key_path=en.PRIVATE_ROOT + r"\broker_ed25519",
        desktop_path=en.PRIVATE_ROOT + r"\desktop.json",
        observer_executable=ob.OBSERVER_SIGNER_IMAGE,
        observer_key_path=en.OBSERVER_ROOT + r"\observer_ed25519",
    )
    rb = en.collect_live_scm_readback(spec)
    assert rb.state is en.ScmBindingState.HOLD_RECONCILE and rb.bound_pid is None and rb.retry_allowed is False
    bound = en.attend_scm_pid_binding(spec)
    assert bound.state is en.ScmBindingState.HOLD_RECONCILE and bound.bound_pid is None and bound.retry_allowed is False
