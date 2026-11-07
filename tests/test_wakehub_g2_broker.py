"""Adversarial OFFLINE service fixtures; real OpenSSH crypto and disk SQLite.

SCM, pipe and ACL seams are mocked explicitly. No service is installed, signer
enrolled, pipe listener created, Chrome touched or Wake sent by these fixtures.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
import base64
import json
import os
from pathlib import Path
import sqlite3
import sys
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_commerce.wakehub import native_broker as b
from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_journal as jn
from ai_commerce.wakehub import native_runtime as rt
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub import native_inventory as inv
from ai_commerce.engineering.three_runner_topology import planned_topology

# Reuse the preexisting OFFLINE signing/storage harness, not its authority.
from test_wakehub_g2_native import (
    crypto, storage, enrolled, grant, effect_key, original_outcome,
    T, SID, SOURCE, TARGET, JID, START,
)


@pytest.fixture
def broker(enrolled, monkeypatch):
    f = enrolled
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(rt, "windows_only", lambda: None)
    initialize = b.WindowsServiceAuthority.__init__
    monkeypatch.setattr(b.WindowsServiceAuthority, "__init__",
        lambda self, handle: initialize(self, handle, clock=lambda: T))
    monkeypatch.setattr(b.ServiceCustody, "check", lambda self, p, **kw: Path(p))
    # Shared Windows login is intentional; the service remains independent.
    f.anchor["runner_sids"] = [SID]
    f.value["observer_sid"] = b.service_sid()
    f.save()
    server = inv.ProcessIdentity(1111, T - 60, sys.executable, b.service_sid(), 0)
    client = inv.ProcessIdentity(2222, T - 50, sys.executable, SID, 7)
    requests = f.storage.root / "broker-requests.sqlite3"
    with closing(sqlite3.connect(requests)) as db, db:
        db.executescript(b._REQUEST_SCHEMA)
        db.execute("INSERT INTO identity VALUES(?)", (JID,))
    anchor = dict(schema="WakeHubPrivateBrokerAnchor.v1", task_parent=auth.TASK_PARENT,
        authority=f.anchor,
        service=dict(name=b.SERVICE, process=asdict(server), sha256=auth._digest(Path(sys.executable).read_bytes())),
        desktop_peer=dict(process=asdict(client), sha256=auth._digest(Path(sys.executable).read_bytes())),
        requests_path=str(requests), private_key_path=str(f.crypto.keys["observer"]),
        desktop_path=str(f.storage.root / "desktop.json"))
    facts = dict(server=asdict(server), client=asdict(client), scm=dict(pid=server.pid,
        binary=server.executable, account="NT SERVICE\\" + b.SERVICE, kind=0x10, sid_type=1))
    monkeypatch.setattr(b, "_read_broker_anchor", lambda: anchor)
    monkeypatch.setattr(b, "observe_connection", lambda handle: facts)
    desktop = dict(schema="WakeHubBrokerDesktop.v1", task_parent=auth.TASK_PARENT,
        journal_id=JID, source=asdict(SOURCE), target=asdict(TARGET),
        server=asdict(server), client=asdict(client), state="LIVE_UNLOCKED_OWNER",
        issued_at=T - 1, expires_at=T + 20)
    def save_desktop():
        Path(anchor["desktop_path"]).write_bytes(f.crypto.signed(desktop, "observer", "inventory",
            namespace=b.NAMESPACE.format("desktop")))
    save_desktop()
    def runtime():
        return rt.WindowsPrivateRuntime.from_service_connection(123)
    def request(g=None, *, nonce=None, changes=None, proof=None, namespace=None):
        g = grant(op.Operation.STATUS) if g is None else g
        proof = proof or f.proof(g, pause="PAUSED" if g.operation is op.Operation.STATUS else "RELEASED_INDEPENDENTLY")
        body = dict(schema="WakeHubBrokerRequest.v1", task_parent=auth.TASK_PARENT,
            journal_id=JID, source=asdict(SOURCE), server=asdict(server), client=asdict(client),
            operation=g.operation.value,
            proof={k: base64.b64encode(getattr(proof, k)).decode("ascii") for k in ("signature", "consent")},
            nonce=nonce or str(uuid4()), issued_at=T - 1, expires_at=T + 20)
        body.update(changes or {})
        return f.crypto.signed(body, "issuer", "grant", namespace=namespace or b.NAMESPACE.format("request"))
    return SimpleNamespace(f=f, anchor=anchor, facts=facts, server=server, client=client,
        desktop=desktop, save_desktop=save_desktop, runtime=runtime, request=request, requests=requests)


def signed_read(broker, monkeypatch, g):
    runners = tuple((inv.ProcessIdentity(100 + i, T - 60, s.root + r"\bin\Runner.Listener.exe", SID, 7),)
                    for i, s in enumerate(planned_topology()))
    identities = tuple(group[0] for group in runners)
    snapshot = inv.PrivateSnapshot(inv.SOURCE_EPOCH, T, TARGET.user_name, SID, 7,
        ("READY",) * 3, runners, (), identities, identities, (), True)
    service_snapshot = replace(snapshot, user_name="service-account", user_sid=b.service_sid(), session_id=0)
    monkeypatch.setattr(inv.WindowsReadBackend, "read_fixed_inventory", lambda self: service_snapshot)
    runtime = broker.runtime()
    assert type(runtime._authority) is b.WindowsServiceAuthority
    proof = broker.f.proof(g, pause="PAUSED")
    authority = runtime._authority
    assert authority.verify_grant(proof, g.operation, op.LifecyclePolicy(SOURCE), T) == g
    body = dict(schema="WakeHubG2Inventory.v1", journal_id=JID,
        grant_sha256=auth._digest(jn.canonical(jn.grant_data(g))), snapshot=asdict(snapshot),
        enrollment=asdict(authority.authorize_read()), desktop=TARGET.desktop,
        pause="PAUSED", receiver="ONLINE", verified_at=T)
    (broker.f.storage.root / "inventory.json").write_bytes(broker.f.crypto.signed(body, "observer", "inventory"))
    return runtime, proof


def test_shared_sid_without_broker_is_hold(monkeypatch):
    monkeypatch.setattr(b, "_read_broker_anchor", jn.deny)
    runtime = rt.WindowsPrivateRuntime.from_service_connection(123)
    assert runtime.run_service_request(b'{}').decision is op.Decision.HOLD
    assert runtime.run(START).effect is op.EffectAttempt.NOT_ATTEMPTED


def test_offline_service_fixture_has_separate_custody_not_install_proof(broker, monkeypatch):
    g = grant(op.Operation.STATUS)
    runtime, proof = signed_read(broker, monkeypatch, g)
    assert runtime._authority.journal_owner(TARGET) == b.service_sid() != SID
    result = runtime.run_service_request(broker.request(g, proof=proof))
    assert result.decision is op.Decision.STATUS_OBSERVED
    assert result.effect is op.EffectAttempt.NOT_ATTEMPTED
    public = json.loads(result.to_json())
    assert public["native_authority"] == "NOT_PROVEN_BY_SOURCE_OR_TEST_ADAPTER"
    assert "service-account" not in result.to_json()
    assert runtime.run(op.Operation.STATUS, proof).decision is op.Decision.HOLD
    assert runtime._authority.read_verified(g, T).pause is op.PauseState.PAUSED


@pytest.mark.parametrize("facet", ["sid", "session", "start", "image", "pid", "scm_pid", "scm_binary", "scm_account", "scm_type", "scm_sid", "service_name", "hash"])
def test_wrong_service_or_scm_token_denied(broker, facet):
    facts = broker.facts
    if facet in ("sid", "session", "start", "image", "pid"):
        key, value = {"sid": ("user_sid", SID), "session": ("session_id", 7),
            "start": ("created_at", T), "image": ("executable", r"C:\evil.exe"), "pid": ("pid", 42)}[facet]
        facts["server"][key] = value
    elif facet.startswith("scm_"):
        key = facet[4:]
        key = {"type": "kind", "sid": "sid_type"}.get(key, key)
        facts["scm"][key] = {"pid": 42, "binary": r"C:\evil.exe", "account": TARGET.user_name, "kind": 0x20, "sid_type": 0}[key]
    elif facet == "service_name":
        broker.anchor["service"]["name"] = "IssueControlledService"
    else:
        broker.anchor["service"]["sha256"] = "0" * 64
    assert broker.runtime()._operator is None


@pytest.mark.parametrize("facet,value", [("user_sid", b.service_sid()), ("session_id", 8), ("created_at", T),
    ("executable", r"C:\evil.exe"), ("pid", 42)])
def test_pipe_client_owner_session_start_image_pinned(broker, facet, value):
    broker.facts["client"][facet] = value
    assert broker.runtime()._operator is None


@pytest.mark.parametrize("sid", sorted(jn.OS_TRUSTED_SIDS))
def test_privileged_runner_cannot_claim_acl_isolation(broker, sid):
    broker.anchor["authority"]["runner_sids"] = [sid]
    assert broker.runtime()._operator is None


@pytest.mark.parametrize("facet", ["service_acl", "journal_acl", "signer_acl", "private_key_acl", "pipe_acl"])
def test_missing_or_foreign_custody_denies_before_dispatch(broker, monkeypatch, facet):
    if facet == "service_acl" or facet == "pipe_acl":
        monkeypatch.setattr(b, "observe_connection", lambda handle: jn.deny())
    elif facet == "signer_acl":
        monkeypatch.setattr(jn.WindowsPathCustody, "check", lambda *a, **kw: jn.deny())
    else:
        bad = broker.anchor["authority"]["journal_path"] if facet == "journal_acl" else broker.anchor["private_key_path"]
        monkeypatch.setattr(b.ServiceCustody, "check", lambda self, p, **kw: jn.deny() if str(p) == bad else Path(p))
    assert broker.runtime()._operator is None


@pytest.mark.parametrize("owner,aces", [(SID, []), (b.service_sid(), None),
    (b.service_sid(), [(0, 0, 1, SID)]), (b.service_sid(), [(0, 0, 0x40000, SID)])])
def test_private_acl_does_not_trust_shared_login(owner, aces):
    with pytest.raises(jn.NativeDenied):
        jn.WindowsPathCustody.check_descriptor(owner, aces, {*jn.OS_TRUSTED_SIDS, b.service_sid()}, private=True)


@pytest.mark.parametrize("mask", [4, 0x40000, 0x80000, 0x10000, 0x40000000, 0x10000000])
def test_pipe_acl_cannot_create_server_or_take_control(monkeypatch, mask):
    monkeypatch.setattr(jn.WindowsPathCustody, "_descriptor", lambda *a, **kw: (b.service_sid(), [(0, 0, mask, SID)]))
    with pytest.raises(jn.NativeDenied):
        b._check_pipe_acl(123)


def test_missing_protected_pipe_dacl_denies(monkeypatch):
    monkeypatch.setattr(jn.WindowsPathCustody, "_descriptor", lambda *a, **kw: (b.service_sid(), [(0, 0, 3, SID)]))
    monkeypatch.setattr(b, "_protected_dacl", lambda *a, **kw: jn.deny())
    with pytest.raises(jn.NativeDenied):
        b._check_pipe_acl(123)


@pytest.mark.parametrize("facet", ["schema", "source", "epoch", "server", "client", "expired", "future", "extra", "nonce", "namespace", "forged", "unsigned", "raw_grant"])
def test_forged_stale_and_unbound_signed_requests_deny(broker, facet):
    runtime = broker.runtime()
    changes = {"schema": {"schema": "HostOps9333"}, "source": {"source": asdict(replace(SOURCE, source_sha="f" * 40))},
        "epoch": {"source": asdict(replace(SOURCE, source_epoch=2))}, "server": {"server": asdict(replace(broker.server, pid=42))},
        "client": {"client": asdict(replace(broker.client, session_id=8))}, "expired": {"issued_at": T - 20, "expires_at": T - 1},
        "future": {"issued_at": T + 1, "expires_at": T + 20}, "extra": {"command": "anything"}, "nonce": {"nonce": "not-a-nonce"}}
    raw = broker.request(changes=changes.get(facet), namespace="hostops-9333" if facet == "namespace" else None)
    if facet in ("forged", "unsigned"):
        value = jn.decode(raw)
        value["body"]["nonce"] = str(uuid4())
        raw = jn.canonical(value["body"] if facet == "unsigned" else value)
    elif facet == "raw_grant":
        raw = broker.f.proof(grant()).signature
    receipt = runtime.run_service_request(raw)
    assert receipt.decision is op.Decision.HOLD
    assert receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    with closing(sqlite3.connect(broker.requests)) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone() == (0,)


@pytest.mark.parametrize("mutation", ["locked", "target", "client", "server", "stale", "source", "9333", "revoked", "journal_move"])
def test_fresh_desktop_source_and_revocation_rechecked(broker, mutation):
    runtime = broker.runtime()
    g = grant(op.Operation.STATUS)
    raw = broker.request(g)
    if mutation == "locked":
        broker.desktop["state"] = "LOCKED"
    elif mutation in ("client", "server"):
        broker.desktop[mutation]["pid"] = 42
    elif mutation == "target":
        broker.desktop["target"]["session_id"] = 8
    elif mutation == "stale":
        broker.desktop["expires_at"] = T - 1
    elif mutation == "source":
        broker.desktop["source"]["source_epoch"] = 2
    elif mutation == "9333":
        broker.desktop["target"]["port"] = 9333
    elif mutation == "revoked":
        broker.f.rev["revoked"] = [str(g.key.action_id)]
        broker.f.save()
    else:
        broker.f.value["journal_path"] = str(broker.f.storage.root / "replacement.sqlite3")
        broker.f.save()
    broker.save_desktop()
    assert runtime.run_service_request(raw).decision is op.Decision.HOLD


def test_request_replay_survives_reopen_and_lost_ack(broker, monkeypatch):
    g = grant(op.Operation.STATUS)
    runtime, proof = signed_read(broker, monkeypatch, g)
    raw = broker.request(g, proof=proof)
    monkeypatch.setattr(runtime._broker, "finish", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    assert runtime.run_service_request(raw).decision is op.Decision.UNKNOWN_EFFECT
    reopened = broker.runtime()
    assert reopened.run_service_request(raw).reason is op.Reason.DUPLICATE
    with closing(sqlite3.connect(broker.requests)) as db:
        assert db.execute("SELECT state,receipt FROM requests").fetchall() == [("UNKNOWN", None)]


def test_two_request_connections_first_wins_sqlite_restart(broker):
    def reopen():
        return b.BrokerRequests(broker.requests, owner_sid=b.service_sid(), journal_id=JID)
    ledgers = [reopen(), reopen()]
    barrier, nonce, digest = Barrier(2), str(uuid4()), "a" * 64
    def claim(i):
        barrier.wait()
        try:
            return ledgers[i].claim(nonce, digest)
        except jn.NativeDenied:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(claim, range(2))) == 1
    assert not reopen().claim(nonce, digest)
    assert not reopen().claim(str(uuid4()), digest)


@pytest.mark.parametrize("damage", ["missing", "empty", "corrupt", "schema", "identity", "receipt"])
def test_request_ledger_corruption_never_recreates_or_retries(broker, damage):
    if damage == "missing":
        broker.requests.unlink()
    elif damage in ("empty", "corrupt"):
        broker.requests.write_bytes(b"" if damage == "empty" else b"corrupt bytes")
    else:
        with closing(sqlite3.connect(broker.requests)) as db, db:
            if damage == "schema":
                db.execute("CREATE TRIGGER ignore_insert BEFORE INSERT ON requests BEGIN SELECT RAISE(IGNORE); END")
            elif damage == "identity":
                db.execute("UPDATE identity SET journal_id='different'")
            else:
                db.execute("INSERT INTO requests VALUES(?,?,'DONE',?)", (str(uuid4()), "a" * 64, b'{"secret":"never public"}'))
    runtime = broker.runtime()
    assert runtime._operator is None
    assert runtime.run_service_request(b'{}').decision in (op.Decision.HOLD, op.Decision.UNKNOWN_EFFECT)


def test_signed_original_only_reconciliation_preserves_effect_keys(broker):
    runtime = broker.runtime()
    g = grant()
    journal = runtime._authority.journal
    lease = journal.reserve(op.PrivateIntent(g), T)
    journal.mark_unknown(op.PrivateIntent(g))
    reconcile = grant(op.Operation.RECONCILE_ONLY, reconciles=g.key)
    missing = runtime.run_service_request(broker.request(reconcile))
    assert missing.reason is op.Reason.RECONCILE_ONLY
    observation, raw = original_outcome(broker.f, lease)
    result = broker.runtime().run_service_request(broker.request(reconcile))
    assert result.decision is op.Decision.RECONCILED
    assert broker.f.storage.reopen().read(g).observation == observation
    assert broker.f.storage.reopen().reserve(op.PrivateIntent(g), T) is None


@pytest.mark.parametrize("operation", [START, op.Operation.REUSE])
def test_even_signed_effect_request_blocked_without_native_interface(broker, operation):
    runtime = broker.runtime()
    result = runtime.run_service_request(broker.request(grant(operation)))
    assert result.decision is op.Decision.HOLD
    assert result.effect is op.EffectAttempt.NOT_ATTEMPTED
    assert broker.f.storage.reopen().read(grant()).state is op.JournalState.EMPTY


def test_read_only_real_windows_primary_token_seam():
    if os.name != "nt":
        pytest.skip("actual Windows token seam")
    identity = b.read_process_identity(os.getpid())
    assert identity.pid == os.getpid()
    assert os.path.samefile(identity.executable, getattr(sys, "_base_executable", sys.executable))
    assert identity.created_at > 0


def test_ci_and_raw_pipe_handle_cannot_enroll(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "arbitrary-nonempty")
    for handle in (0, 123, -1, "Issue selected"):
        runtime = rt.WindowsPrivateRuntime.from_service_connection(handle)
        assert runtime._operator is None
        assert runtime.run_service_request(b'{}').effect is op.EffectAttempt.NOT_ATTEMPTED


@pytest.mark.parametrize("defect", ["unprotected", "no_service_ace", "foreign_read", "foreign_owner"])
def test_service_private_tree_requires_real_acl_readback(tmp_path, monkeypatch, defect):
    path = tmp_path / "private-key"
    path.write_bytes(b'offline fixture')
    monkeypatch.setattr(jn.WindowsPathCustody, "check", lambda self, p, **kw: Path(p))
    aces = [(0, 0, 0x1f01ff, b.service_sid())]
    if defect == "no_service_ace":
        aces = [(0, 0, 0x1f01ff, "S-1-5-18")]
    elif defect == "foreign_read":
        aces.append((0, 0, 1, SID))
    owner = SID if defect == "foreign_owner" else b.service_sid()
    monkeypatch.setattr(jn.WindowsPathCustody, "_descriptor", lambda *a, **kw: (owner, aces))
    monkeypatch.setattr(b, "_protected_dacl", lambda *a, **kw: jn.deny() if defect == "unprotected" else None)
    with pytest.raises(jn.NativeDenied):
        b.ServiceCustody().check(path)


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_request_crash_does_not_turn_unknown_into_retry(broker, monkeypatch, phase):
    journal = b.BrokerRequests(broker.requests, owner_sid=b.service_sid(), journal_id=JID)
    nonce, digest = str(uuid4()), "d" * 64
    if phase == "before_commit":
        validate = journal._validate
        calls = 0
        def crash(db):
            nonlocal calls
            calls += 1
            validate(db)
            if calls == 2:
                raise SystemExit("offline crash before durable claim; no dispatch occurred")
        monkeypatch.setattr(journal, "_validate", crash)
        with pytest.raises(SystemExit):
            journal.claim(nonce, digest)
        with closing(sqlite3.connect(broker.requests)) as db:
            assert db.execute("SELECT count(*) FROM requests").fetchone() == (0,)
    else:
        assert journal.claim(nonce, digest)  # Simulate losing ACK after commit.
        reopened = b.BrokerRequests(broker.requests, owner_sid=b.service_sid(), journal_id=JID)
        assert not reopened.claim(nonce, digest)


def test_broker_import_has_no_native_effect(monkeypatch):
    import importlib.util
    import ctypes
    import socket
    import subprocess
    def forbidden(*args, **kwargs):
        pytest.fail("import must not cross any native boundary")
    with monkeypatch.context() as patch:
        patch.setattr(ctypes, "WinDLL", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        spec = importlib.util.spec_from_file_location("offline_broker_import", b.__file__)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_prior_manifest_drift_regression_without_worktree_write(monkeypatch):
    from ai_commerce.engineering.impact_v21 import load_baseline_component_owner_records
    from ai_commerce.engineering.minimum_test import approved_groups
    root = Path(__file__).resolve().parents[1]
    old = root / "work_items/mt01-components/wakehub-g2-native.json"
    baseline = old.read_bytes()
    records = load_baseline_component_owner_records(root=root, base_sha=auth.TASK_PARENT, existing_groups=approved_groups())
    assert any(r["group"].name == "wakehub-g2-native" for r in records)
    read = Path.read_bytes
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", lambda p: b'{}' if p == old else read(p))
        with pytest.raises(ValueError, match="^ONBOARD_BASELINE_MANIFEST_DRIFT$"):
            load_baseline_component_owner_records(root=root, base_sha=auth.TASK_PARENT, existing_groups=approved_groups())
    assert old.read_bytes() == baseline
