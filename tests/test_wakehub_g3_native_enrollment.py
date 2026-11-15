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
    return SimpleNamespace(signers=signers, signed=signed)


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
    calls = []
    def fail(*args): calls.append(1); raise RuntimeError("synthetic lost reply")
    monkeypatch.setattr(host, "_connected_runtime", fail)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: g3.host.complete(123, g3.status(challenge), b"{}"), range(2)))
    assert len(calls) == 1
    assert {r.decision for r in results} == {op.Decision.UNKNOWN_EFFECT, op.Decision.HOLD}
    assert g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("operation", [START, op.Operation.REUSE])
def test_host_never_reaches_native_effect_boundary(g3, monkeypatch, operation):
    monkeypatch.setattr(rt.WindowsExecutor, "start_dedicated", lambda *a: pytest.fail("START invoked"))
    monkeypatch.setattr(rt.WindowsExecutor, "reuse_owned", lambda *a: pytest.fail("ATTACH invoked"))
    challenge = g3.host.begin(123)
    result = g3.host.complete(123, g3.status(challenge), g3.broker.request(grant(operation)))
    assert result.decision is op.Decision.HOLD and result.effect is op.EffectAttempt.NOT_ATTEMPTED


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
    assert missing.reason is op.Reason.RECONCILE_ONLY
    assert journal.read(original).state is op.JournalState.UNKNOWN
    observation, _ = original_outcome(g3.broker.f, lease)
    for _ in range(2):
        challenge = g3.host.begin(123)
        result = g3.host.complete(123, g3.status(challenge), g3.broker.request(reconcile))
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
    return SimpleNamespace(g3=g3, exchange=host.ConnectedStatusExchange(clock=lambda: g3.clock[0]),
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
    "start", "reuse", "source", "release", "server", "client", "journal", "extra",
    "noncanonical", "oversized", "untyped", "bad_handle"])
def test_peer_request_preflight_never_reserves_untrusted_or_effect_request(status_exchange, fault):
    x = status_exchange
    value = jn.decode(x.request); body = value["body"]
    if fault == "source": body["source"]["source_epoch"] += 1
    elif fault == "release": body["source"]["release_sha"] = "f" * 40
    elif fault in ("server", "client"): body[fault]["created_at"] += 1
    elif fault == "journal": body["journal_id"] = "b" * 32
    elif fault == "extra": body["command"] = "forbidden"
    elif fault in ("start", "reuse"): body["operation"] = (START if fault == "start" else op.Operation.REUSE).value
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
    assert receipt.decision is op.Decision.HOLD and receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    with closing(sqlite3.connect(x.g3.broker.requests)) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone() == (0,)
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
    reopened = host.ConnectedStatusExchange(clock=lambda: T)
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
    # A fresh challenge cannot replay the already consumed issuer request.
    x.exchange = host.ConnectedStatusExchange(clock=lambda: T)
    fresh, _ = offered_exchange(x)
    assert fresh.nonce != challenge.nonce
    result = x.exchange.complete(123, status_reply(x, fresh))
    assert result.decision is op.Decision.HOLD and result.reason is op.Reason.DUPLICATE
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
