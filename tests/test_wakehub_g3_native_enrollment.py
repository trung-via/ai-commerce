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

from ai_commerce.wakehub import g3_enrollment as en
from ai_commerce.wakehub import g3_desktop_observer as ob
from ai_commerce.wakehub import g3_service_host as host
from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_broker as b
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
        "overall_disposition", "missing_owners", "required_human_grants",
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
    assert partial.os_identity == "LIVE_UNLOCKED_OWNER"
    assert partial.complete_search_proven is False
    assert partial.origin_attested is False
    assert partial.disposition == "OWNER_ESCALATION"
    assert "CHATGPT_ACCOUNT_SESSION_AUTHORITY" in partial.missing_owners
    assert "COMPLETE_SEARCH_DOMAIN_WITNESS" in partial.missing_owners

