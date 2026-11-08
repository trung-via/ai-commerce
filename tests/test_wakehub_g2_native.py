"""Offline Windows fixtures, real ephemeral OpenSSH signatures and SQLite files.

OS custody/desktop/READ seams are explicitly mocked. Keys exist only in pytest
scratch, never enroll a signer or start/attach Chrome. These tests prove source
semantics and disk transactions, NOT native custody or any actual host effect.
No fixture modifies Git configuration, hooks, guards, refs or the worktree.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_commerce.engineering import impact_v21, minimum_test
from ai_commerce.engineering.three_runner_topology import planned_topology
from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_inventory as inv
from ai_commerce.wakehub import native_journal as jn
from ai_commerce.wakehub import native_runtime as rt
from ai_commerce.wakehub import private_operator as op


ROOT = Path(__file__).resolve().parents[1]
T = 1800000000.0
SID = "S-1-5-21-111-222-333-1001"
OBSERVER = "S-1-5-21-111-222-333-1002"
RUNNER = "S-1-5-21-111-222-333-1003"
TARGET = op.PrivateTarget(r"fixture-host\private-owner", SID, 7,
    r"C:\Private-Fixture\Chrome\chrome.exe", r"C:\Private-Fixture\Dedicated")
SOURCE = op.SourceBinding(auth.TASK_PARENT, 1, "a" * 40, 1)
JID = "a" * 32
LANE = jn.lane_for(TARGET)
PINS = dict(source_sha=SOURCE.source_sha, release_sha=SOURCE.release_sha,
            revocation_sha=auth._digest(jn.canonical([])))
START = op.Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT
PATHS = ("src/ai_commerce/wakehub/native_runtime.py",
         "src/ai_commerce/wakehub/native_authority.py",
         "src/ai_commerce/wakehub/native_journal.py",
         "tests/test_wakehub_g2_native.py",
         "work_items/mt01-components/wakehub-g2-native.json")


def effect_key():
    return op.EffectKey(uuid4(), uuid4(), uuid4())


def grant(operation=START, **changes):
    expected = inv.ProcessIdentity(200, T - 60, TARGET.chrome_executable, SID, 7)
    value = op.PrivateGrant(operation, op._scope(operation), SOURCE, TARGET, effect_key(), T - 1, T + 20,
        expected if operation is op.Operation.REUSE else None,
        effect_key() if operation is op.Operation.RECONCILE_ONLY else None)
    return replace(value, **changes)


def provision_fixture(path, journal_id=JID, lane=LANE):
    # OFFLINE provisioning only. Production deliberately has no create/reset.
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(jn._SCHEMA)
        db.execute("INSERT INTO meta VALUES(1,?,?,1,1,1,?,?,?,?)",
                   (journal_id, lane, T - 2, *PINS.values()))
    return path


@pytest.fixture
def storage(tmp_path, monkeypatch):
    def fixture_path(self, path, *, directory=False):
        path = Path(path)
        if (not path.is_absolute() or not path.exists() or path.is_symlink()
                or not (tmp_path == path or tmp_path in path.parents or ROOT == path or ROOT in path.parents)):
            jn.deny()
        return path
    monkeypatch.setattr(jn.WindowsPathCustody, "check", fixture_path)
    path = provision_fixture(tmp_path / "fixture.sqlite3")
    def reopen():
        return jn.SQLitePrivateJournal(path, owner_sid=SID, journal_id=JID, lane=LANE)
    return SimpleNamespace(path=path, journal=reopen(), reopen=reopen, root=tmp_path)


def test_pending_reopens_poisoned_across_release_session_case_and_expiry(storage):
    first = grant()
    lease = storage.journal.reserve(op.PrivateIntent(first), T)
    changed = grant(source=replace(SOURCE, source_epoch=2, release_epoch=2, release_sha="b" * 40),
                    target=replace(TARGET, session_id=8, dedicated_profile=TARGET.dedicated_profile.upper()),
                    issued_at=T + 30, expires_at=T + 40)
    journal = storage.reopen()
    journal.checkpoint(2, 2, 2, T + 30, **{**PINS, "release_sha": changed.source.release_sha})
    assert journal.read(changed).state is op.JournalState.PENDING
    assert journal.read(changed).lease == lease
    assert journal.reserve(op.PrivateIntent(changed), T + 31) is None
    journal.mark_unknown(op.PrivateIntent(first))
    assert storage.reopen().read(changed).state is op.JournalState.UNKNOWN


@pytest.mark.parametrize("key_field", ["action_id", "effect_id", "one_shot_key"])
@pytest.mark.parametrize("new_field", ["action_id", "effect_id", "one_shot_key"])
def test_each_key_consumed_forever_even_cross_role(storage, key_field, new_field):
    first = grant()
    storage.journal.reserve(op.PrivateIntent(first), T)
    # Offline independent-outcome fixture; protocol complete cannot do this.
    with closing(sqlite3.connect(storage.path)) as db, db:
        lease = storage.journal.read(first).lease
        observed = op.ObservedEffect(lease, op.ObservedOutcome.NO_EFFECT_OBSERVED, T, None)
        # Syntactic fixture only; WindowsGrantVerifier audits the retained
        # signature before using a ledger for any authorized operation.
        db.execute("UPDATE intents SET state='OBSERVED',observation=?,proof=?",
                   (jn.canonical(jn.observation_data(observed)), b'{"body":{},"signature":"fixture"}'))
    second = grant(key=replace(effect_key(), **{new_field: getattr(first.key, key_field)}))
    assert storage.reopen().reserve(op.PrivateIntent(second), T + 1) is None
    assert storage.reopen().read(first).state is op.JournalState.OBSERVED


def test_concurrent_independent_connections_first_wins(storage):
    barrier = Barrier(2)
    journals = [storage.reopen(), storage.reopen()]
    intents = [op.PrivateIntent(grant()), op.PrivateIntent(grant())]
    def attempt(i):
        barrier.wait()
        try:
            return journals[i].reserve(intents[i], T)
        except jn.NativeDenied:
            return None  # bounded lock failure is a denial, never absence
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(type(v) is op.PrivateLease for v in results) == 1
    with closing(sqlite3.connect(storage.path)) as db, db:
        assert db.execute("SELECT count(*) FROM intents").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM consumed").fetchone() == (3,)


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
def test_crash_and_lost_reservation_ack(storage, monkeypatch, fault):
    original = jn.SQLitePrivateJournal._validate
    count = 0
    def crash(self, db):
        nonlocal count
        count += 1
        if fault == "before_commit" and count == 2:
            raise SystemExit("offline crash")
        original(self, db)
    monkeypatch.setattr(jn.SQLitePrivateJournal, "_validate", crash)
    value = grant()
    if fault == "before_commit":
        with pytest.raises(SystemExit):
            storage.journal.reserve(op.PrivateIntent(value), T)
        monkeypatch.setattr(jn.SQLitePrivateJournal, "_validate", original)
        assert storage.reopen().read(value).state is op.JournalState.EMPTY
    else:
        storage.journal.reserve(op.PrivateIntent(value), T)  # returned ACK is deliberately lost
        assert storage.reopen().read(grant()).state is op.JournalState.PENDING
        assert storage.reopen().reserve(op.PrivateIntent(grant()), T + 1) is None


def test_locked_database_is_hold_never_empty(storage):
    db = sqlite3.connect(storage.path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(jn.NativeDenied, match="^WAKEHUB_NATIVE_CUSTODY_HOLD$"):
            storage.reopen()
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("damage", ["corrupt", "empty", "missing", "orphan", "schema", "wrong_id"])
def test_corruption_or_replaced_ledger_never_bootstraps(storage, damage):
    if damage in ("corrupt", "empty"):
        storage.path.write_bytes(b"corrupted fixture" if damage == "corrupt" else b"")
    elif damage == "missing":
        storage.path.unlink()
    else:
        with closing(sqlite3.connect(storage.path)) as db, db:
            if damage == "orphan":
                db.execute("INSERT INTO consumed VALUES('orphan',999)")
            elif damage == "schema":
                db.execute("CREATE TRIGGER drop_keys AFTER INSERT ON intents BEGIN DELETE FROM consumed; END")
            else:
                db.execute("UPDATE meta SET journal_id='replaced'")
    with pytest.raises(jn.NativeDenied):
        storage.reopen()


@pytest.mark.parametrize("values", [(0, 1, 1, T), (1, 0, 1, T), (1, 1, 0, T), (1, 1, 1, T - 10)])
def test_epoch_clock_rollback_denied(storage, values):
    storage.journal.checkpoint(2, 2, 2, T, **PINS)
    with pytest.raises(jn.NativeDenied):
        storage.reopen().checkpoint(*values, **PINS)


@pytest.mark.parametrize("pin", ["source_sha", "release_sha", "revocation_sha"])
def test_epoch_cannot_be_reused_for_different_signed_content(storage, pin):
    pins = {**PINS, pin: "f" * (64 if pin == "revocation_sha" else 40)}
    with pytest.raises(jn.NativeDenied):
        storage.reopen().checkpoint(1, 1, 1, T, **pins)


def test_expired_future_duplicate_keys_and_wrong_lane(storage):
    values = [grant(issued_at=T - 29, expires_at=T - 1),
              grant(issued_at=T + 1, expires_at=T + 20),
              grant(target=replace(TARGET, dedicated_profile=r"C:\Private-Fixture\Other"))]
    key = effect_key()
    values.append(grant(key=replace(key, effect_id=key.action_id)))
    for value in values:
        with pytest.raises(jn.NativeDenied):
            storage.journal.reserve(op.PrivateIntent(value), T)
    assert storage.journal.read(grant()).state is op.JournalState.EMPTY


@pytest.mark.parametrize("owner,aces", [
    (RUNNER, []), (SID, None), (SID, [(0, 0, 0x40000000, RUNNER)]),
    (SID, [(0, 0, 0x40000, RUNNER)]), (SID, [(5, 0, 1, SID)]),
])
def test_acl_readback_rejects_foreign_custody(owner, aces):
    with pytest.raises(jn.NativeDenied):
        jn.WindowsPathCustody.check_descriptor(owner, aces, {SID, "S-1-5-18"})


def test_acl_readback_allows_private_owner_and_ignores_inherit_only():
    jn.WindowsPathCustody.check_descriptor(SID,
        [(0, 0, 0x1f01ff, SID), (0, 8, 0x40000000, RUNNER)], {SID})


def test_private_journal_acl_cannot_disclose_to_read_only_foreign_principal():
    with pytest.raises(jn.NativeDenied):
        jn.WindowsPathCustody.check_descriptor(SID, [(0, 0, 0x80000000, RUNNER)], {SID}, private=True)


@pytest.fixture(scope="session")
def crypto(tmp_path_factory):
    exe = shutil.which("ssh-keygen")
    if not exe:
        pytest.fail("Offline signature tests require the real OpenSSH verifier")
    root = tmp_path_factory.mktemp("offline-wakehub-signatures")
    keys, signers = {}, {}
    for role in ("issuer", "human", "observer"):
        key = root / role
        subprocess.run([exe, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       check=True, capture_output=True, timeout=10)
        public = Path(str(key) + ".pub").read_text().split()
        signers[role] = root / (role + "-signers")
        signers[role].write_text(auth.IDENTITIES[role] + " " + " ".join(public[:2]) + "\n")
        keys[role] = key
    def signed(body, role, purpose, *, namespace=None):
        file = root / (uuid4().hex + ".json")
        file.write_bytes(auth.signing_bytes(body))
        subprocess.run([exe, "-Y", "sign", "-f", str(keys[role]), "-n",
                       namespace or auth.NAMESPACES[purpose], str(file)],
                       check=True, capture_output=True, timeout=10)
        signature = base64.b64encode(Path(str(file) + ".sig").read_bytes()).decode("ascii")
        return jn.canonical({"body": body, "signature": signature})
    return SimpleNamespace(exe=exe, root=root, keys=keys, signers=signers, signed=signed)


@pytest.fixture
def enrolled(storage, monkeypatch, crypto):
    # This fixture bypasses OS custody only; signature validation still invokes
    # the real reviewed OpenSSH helper for independent keys/namespaces.
    monkeypatch.setattr(auth, "_desktop_identity", lambda target: None)
    monkeypatch.setattr(jn.WindowsPathCustody, "check", lambda self, path, **kw: Path(path))
    value = dict(schema="WakeHubG2Enrollment.v1", task_parent=auth.TASK_PARENT,
        source=asdict(SOURCE), target=asdict(TARGET), release_root=str(ROOT),
        files={}, journal_path=str(storage.path), journal_id=JID, lane=LANE,
        observer_root=str(storage.root), observer_sid=OBSERVER,
        issued_at=T - 60, expires_at=T + 60, enabled=True)
    for name, module in tuple(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if file and (name == "ai_commerce" or name.startswith("ai_commerce.")):
            path = Path(file)
            value["files"][path.relative_to(ROOT).as_posix()] = auth._digest(path.read_bytes())
    enrollment_path = storage.root / "enrollment.json"
    anchor = dict(schema="WakeHubG2Anchor.v1", enrollment=str(enrollment_path),
        operator_sid=SID, runner_sids=[RUNNER], journal_path=str(storage.path), journal_id=JID, lane=LANE,
        verifier=dict(path=crypto.exe, sha256=auth._digest(Path(crypto.exe).read_bytes())))
    for role, path in crypto.signers.items():
        anchor[role + "_signers"] = dict(path=str(path), sha256=auth._digest(path.read_bytes()))
    rev = dict(schema="WakeHubG2Revocations.v1", journal_id=JID, epoch=1, source_epoch=1,
               release_epoch=1, issued_at=T - 1, expires_at=T + 20, revoked=[])
    def save():
        enrollment_path.write_bytes(crypto.signed(value, "issuer", "enrollment"))
        (storage.root / "revocations.json").write_bytes(crypto.signed(rev, "issuer", "revocations"))
    save()
    monkeypatch.setattr(auth, "_load_anchor", lambda: anchor)
    verifier = auth.WindowsGrantVerifier(clock=lambda: T)
    verifier.journal = storage.journal
    verifier._load(T)
    def proof(value, *, pause="RELEASED_INDEPENDENTLY", receiver="ONLINE", namespace=None):
        body = dict(schema="WakeHubG2Grant.v1", task_parent=auth.TASK_PARENT,
                    journal_id=JID, grant=jn.grant_data(value))
        consent = dict(schema="WakeHubG2Consent.v1", task_parent=auth.TASK_PARENT,
            journal_id=JID, grant_sha256=auth._digest(jn.canonical(body)),
            issued_at=value.issued_at, expires_at=value.expires_at, pause=pause, receiver=receiver)
        return op.PrivateGrantProof(crypto.signed(body, "issuer", "grant", namespace=namespace),
                                   crypto.signed(consent, "human", "consent"))
    return SimpleNamespace(verifier=verifier, storage=storage, value=value, rev=rev,
                           anchor=anchor, save=save, proof=proof, crypto=crypto)


def test_real_signatures_verify_only_exact_grant_and_separate_consent(enrolled):
    g = grant()
    p = enrolled.proof(g)
    assert enrolled.verifier.verify_grant(p, START, op.LifecyclePolicy(SOURCE), T) == g
    assert enrolled.verifier.verify_grant(p, op.Operation.REUSE, op.LifecyclePolicy(SOURCE), T) is None
    assert enrolled.verifier.verify_grant(op.PrivateGrantProof(p.signature, p.signature), START,
                                          op.LifecyclePolicy(SOURCE), T) is None


@pytest.mark.parametrize("namespace", ["hostops-v3-request", "hostops-d5", "wakehub-g2-9444-consent@ai-commerce"])
def test_d5_hostops_and_wrong_signature_domains_deny(enrolled, namespace):
    g = grant()
    assert enrolled.verifier.verify_grant(enrolled.proof(g, namespace=namespace), START,
                                          op.LifecyclePolicy(SOURCE), T) is None


@pytest.mark.parametrize("mutation", ["signature", "body", "consent", "unsigned", "boolean"])
def test_fabricated_signed_objects_never_authority(enrolled, mutation):
    g = grant()
    p = enrolled.proof(g)
    signature = jn.decode(p.signature)
    if mutation == "signature":
        signature["signature"] = base64.b64encode(b"not a signature" * 10).decode()
    elif mutation == "body":
        signature["body"]["grant"]["source"]["source_sha"] = "f" * 40
    elif mutation == "unsigned":
        signature = signature["body"]
    elif mutation == "boolean":
        signature = {"signed": True, "enabled": True}
    proof = op.PrivateGrantProof(jn.canonical(signature), b"{}" if mutation == "consent" else p.consent)
    assert enrolled.verifier.verify_grant(proof, START, op.LifecyclePolicy(SOURCE), T) is None


@pytest.mark.parametrize("change", ["source", "release", "epoch", "future", "expired", "30seconds", "sid", "session", "user", "replayed_key"])
def test_even_valid_signatures_cannot_override_local_binding(enrolled, change):
    g = grant()
    changes = {
        "source": dict(source=replace(SOURCE, source_sha="f" * 40)),
        "release": dict(source=replace(SOURCE, release_sha="f" * 40)),
        "epoch": dict(source=replace(SOURCE, source_epoch=2)),
        "future": dict(issued_at=T + 1, expires_at=T + 20),
        "expired": dict(issued_at=T - 20, expires_at=T - 1),
        "30seconds": dict(issued_at=T - 10, expires_at=T + 20),
        "sid": dict(target=replace(TARGET, user_sid=RUNNER)),
        "session": dict(target=replace(TARGET, session_id=8)),
        "user": dict(target=replace(TARGET, user_name="foreign-owner")),
        "replayed_key": dict(key=replace(g.key, effect_id=g.key.action_id)),
    }
    g = replace(g, **changes[change])
    assert enrolled.verifier.verify_grant(enrolled.proof(g), START, op.LifecyclePolicy(SOURCE), T) is None


@pytest.mark.parametrize("pause,receiver", [("PAUSED", "ONLINE"), ("RELEASED_INDEPENDENTLY", "OFFLINE"),
                                           ("RELEASED_INDEPENDENTLY", "UNPROVEN"), ("RELEASED_INDEPENDENTLY", "MUTED")])
def test_human_stop_and_receiver_are_not_released_by_presence(enrolled, pause, receiver):
    g = grant()
    assert enrolled.verifier.verify_grant(enrolled.proof(g, pause=pause, receiver=receiver), START,
                                          op.LifecyclePolicy(SOURCE), T) is None


@pytest.mark.parametrize("mutation", ["task_parent", "disabled", "same_runner_sid", "journal_move", "file_hash", "source_epoch", "revoked", "kill", "locked"])
def test_independent_enrollment_and_revocation_rechecked(enrolled, monkeypatch, mutation):
    g = grant()
    proof = enrolled.proof(g)
    assert enrolled.verifier.verify_grant(proof, START, op.LifecyclePolicy(SOURCE), T) == g
    if mutation == "task_parent":
        enrolled.value["task_parent"] = "f" * 40
    elif mutation == "disabled":
        enrolled.value["enabled"] = False
    elif mutation == "same_runner_sid":
        enrolled.anchor["runner_sids"].append(SID)
    elif mutation == "journal_move":
        enrolled.value["journal_id"] = "b" * 32
    elif mutation == "file_hash":
        enrolled.value["files"]["src/ai_commerce/wakehub/native_runtime.py"] = "0" * 64
    elif mutation == "source_epoch":
        enrolled.rev["source_epoch"] = 2
    elif mutation == "revoked":
        enrolled.rev["revoked"] = [str(g.key.one_shot_key)]
    elif mutation == "kill":
        (enrolled.storage.root / "KILL").touch()
    else:
        monkeypatch.setattr(auth, "_desktop_identity", lambda target: jn.deny())
    enrolled.save()
    assert enrolled.verifier.verify_grant(proof, START, op.LifecyclePolicy(SOURCE), T) is None


def read_fixture(enrolled, *, owned=False):
    g = grant(op.Operation.STATUS)
    proof = enrolled.proof(g, pause="PAUSED", receiver="OFFLINE")
    assert enrolled.verifier.verify_grant(proof, g.operation, op.LifecyclePolicy(SOURCE), T) == g
    runners = tuple((inv.ProcessIdentity(100 + i, T - 60, s.root + r"\bin\Runner.Listener.exe", SID, 7),)
                    for i, s in enumerate(planned_topology()))
    browser = inv.ChromeProcess(inv.ProcessIdentity(200, T - 60, TARGET.chrome_executable, SID, 7),
                                (9444,), ("127.0.0.1",), (TARGET.dedicated_profile,))
    identities = tuple(g[0] for g in runners) + ((browser.identity,) if owned else ())
    snap = inv.PrivateSnapshot(inv.SOURCE_EPOCH, T, TARGET.user_name, SID, 7, ("READY",) * 3,
        runners, (browser,) if owned else (), identities, identities,
        (inv.SocketListener("127.0.0.1", 9444, 200),) if owned else (), True)
    enrollment = enrolled.verifier.authorize_read()
    def save(snapshot=snap, sign=True):
        body = dict(schema="WakeHubG2Inventory.v1", journal_id=JID,
            grant_sha256=auth._digest(jn.canonical(jn.grant_data(g))), snapshot=asdict(snapshot),
            enrollment=asdict(enrollment), desktop=TARGET.desktop, pause="PAUSED", receiver="OFFLINE", verified_at=T)
        (enrolled.storage.root / "inventory.json").write_bytes(
            enrolled.crypto.signed(body, "observer", "inventory") if sign else jn.canonical(body))
    save()
    enrolled.verifier.backend = SimpleNamespace(read_fixed_inventory=lambda: snap)
    return SimpleNamespace(grant=g, proof=proof, snapshot=snap, save=save)


def test_status_preserves_deliberate_stop_with_independent_signed_read(enrolled):
    f = read_fixture(enrolled)
    operator = op.PrivateLifecycleOperator(op.LifecyclePolicy(SOURCE), verifier=enrolled.verifier,
        journal=enrolled.storage.journal, executor=rt.WindowsExecutor(), clock=lambda: T)
    receipt = operator.run(op.Operation.STATUS, f.proof)
    assert receipt.decision is op.Decision.STATUS_OBSERVED
    evidence = enrolled.verifier.read_verified(f.grant, T)
    assert evidence.pause is op.PauseState.PAUSED
    assert evidence.preflight.code == "PAUSED_BY_HUMAN"
    assert receipt.effect is op.EffectAttempt.NOT_ATTEMPTED


def test_separate_runtime_factory_binds_concrete_status_offline(enrolled, monkeypatch):
    f = read_fixture(enrolled)
    monkeypatch.setattr(rt, "windows_only", lambda: None)
    monkeypatch.setattr(rt, "WindowsGrantVerifier", lambda: enrolled.verifier)
    runtime = rt.WindowsPrivateRuntime.from_local_enrollment()
    assert runtime.run(op.Operation.STATUS, f.proof).decision is op.Decision.STATUS_OBSERVED
    assert runtime._authority.journal.path == enrolled.storage.path


def test_factory_journal_failure_is_unknown_reconcile_without_effect(enrolled, monkeypatch):
    monkeypatch.setattr(rt, "windows_only", lambda: None)
    monkeypatch.setattr(rt, "WindowsGrantVerifier", lambda: enrolled.verifier)
    monkeypatch.setattr(rt, "SQLitePrivateJournal", lambda *a, **kw: jn.deny())
    result = rt.WindowsPrivateRuntime.from_local_enrollment().run(START)
    assert result.decision is op.Decision.UNKNOWN_EFFECT
    assert json.loads(result.to_json())["next_operation"] == "RECONCILE_ONLY"
    assert result.effect is op.EffectAttempt.NOT_ATTEMPTED


@pytest.mark.parametrize("mutation", ["wrong_port", "ipv6", "wildcard", "duplicate", "profile", "race", "incomplete", "timeout", "unsigned", "observer_disagrees"])
def test_private_read_negatives_never_infer_absence(enrolled, mutation):
    f = read_fixture(enrolled, owned=True)
    snap = f.snapshot
    browser = snap.chrome_processes[0]
    if mutation in ("wrong_port", "ipv6", "wildcard", "profile"):
        changes = {"wrong_port": dict(ports=(9333,)), "ipv6": dict(addresses=("::1",)),
                   "wildcard": dict(addresses=("0.0.0.0",)), "profile": dict(profiles=(r"C:\Other",))}
        snap = replace(snap, chrome_processes=(replace(browser, **changes[mutation]),))
    elif mutation == "duplicate":
        snap = replace(snap, chrome_processes=(browser, browser))
    elif mutation == "race":
        snap = replace(snap, confirmed_processes=snap.confirmed_processes[:-1])
    elif mutation == "incomplete":
        snap = replace(snap, complete=False)
    elif mutation == "timeout":
        enrolled.verifier.backend = SimpleNamespace(read_fixed_inventory=lambda: (_ for _ in ()).throw(TimeoutError()))
    elif mutation == "unsigned":
        f.save(sign=False)
    if mutation not in ("timeout", "unsigned"):
        enrolled.verifier.backend = SimpleNamespace(read_fixed_inventory=lambda: snap)
        if mutation != "observer_disagrees":
            f.save(snap)
    if mutation == "observer_disagrees":
        # A matching unsigned digest would not detect this foreign observer read.
        f.save(replace(snap, complete=False))
    with pytest.raises(Exception):
        enrolled.verifier.read_verified(f.grant, T)
    assert enrolled.storage.journal.read(f.grant).state is op.JournalState.EMPTY


def original_outcome(enrolled, lease, *, basis="INDEPENDENT_DURABLE_ORIGINAL_OPERATION"):
    observation = op.ObservedEffect(lease, op.ObservedOutcome.NO_EFFECT_OBSERVED, T, None)
    body = dict(schema="WakeHubG2OriginalOutcome.v1", journal_id=JID,
        lease=jn.lease_data(lease), original_grant_sha256=auth._digest(jn.canonical(jn.grant_data(lease.intent.grant))),
        observation=jn.observation_data(observation), basis=basis)
    raw = enrolled.crypto.signed(body, "observer", "outcome")
    (enrolled.storage.root / "outcome.json").write_bytes(raw)
    return observation, raw


def fixture_runtime(enrolled):
    runtime = rt.WindowsPrivateRuntime()
    runtime._authority = enrolled.verifier
    runtime._operator = op.PrivateLifecycleOperator(op.LifecyclePolicy(enrolled.verifier.source),
        verifier=enrolled.verifier, journal=enrolled.storage.journal, executor=rt.WindowsExecutor(), clock=lambda: T)
    return runtime


def test_signed_original_no_effect_reconciliation_retains_all_keys(enrolled):
    g = grant()
    lease = enrolled.storage.journal.reserve(op.PrivateIntent(g), T)
    enrolled.storage.journal.mark_unknown(op.PrivateIntent(g))
    observation, raw = original_outcome(enrolled, lease)
    reconcile = grant(op.Operation.RECONCILE_ONLY, reconciles=g.key)
    result = fixture_runtime(enrolled).run(reconcile.operation, enrolled.proof(reconcile))
    assert result.decision is op.Decision.RECONCILED
    assert enrolled.storage.reopen().read(g).observation == observation
    assert enrolled.storage.reopen().reserve(op.PrivateIntent(g), T) is None
    # Lost final ACK/reconciliation replay can only read the same observation.
    assert fixture_runtime(enrolled).run(reconcile.operation, enrolled.proof(reconcile)).decision is op.Decision.RECONCILED


@pytest.mark.parametrize("invalid", ["present_only", "missing", "fake", "wrong_lease"])
def test_unknown_effect_requires_original_proof_not_chrome_presence(enrolled, invalid):
    g = grant()
    lease = enrolled.storage.journal.reserve(op.PrivateIntent(g), T)
    original_outcome(enrolled, replace(lease, lease_id=uuid4()) if invalid == "wrong_lease" else lease,
                     basis="CHROME_PRESENT" if invalid == "present_only" else "INDEPENDENT_DURABLE_ORIGINAL_OPERATION")
    path = enrolled.storage.root / "outcome.json"
    if invalid == "missing":
        path.unlink()
    elif invalid == "fake":
        path.write_bytes(b'{"signed":true}')
    r = grant(op.Operation.RECONCILE_ONLY, reconciles=g.key)
    assert fixture_runtime(enrolled).run(r.operation, enrolled.proof(r)).decision is op.Decision.HOLD
    assert enrolled.storage.reopen().read(grant()).state is op.JournalState.PENDING
    assert enrolled.storage.reopen().reserve(op.PrivateIntent(grant()), T) is None


def test_bare_observation_or_ack_cannot_clear_journal(storage):
    g = grant()
    lease = storage.journal.reserve(op.PrivateIntent(g), T)
    obs = op.ObservedEffect(lease, op.ObservedOutcome.NO_EFFECT_OBSERVED, T, None)
    record = storage.journal.read(g)
    assert storage.journal.complete(lease, obs) is False
    assert storage.journal.resolve(record, obs) is False
    with pytest.raises(jn.NativeDenied):
        storage.journal.resolve_verified(record, obs, b"fake", object())
    assert storage.reopen().read(g).state is op.JournalState.PENDING


@pytest.mark.parametrize("invalid", ["expired", "revoked", "forged_lease"])
def test_concrete_lease_is_rechecked_after_durable_reservation(enrolled, invalid):
    g = grant()
    assert enrolled.verifier.verify_grant(enrolled.proof(g), START, op.LifecyclePolicy(SOURCE), T) == g
    lease = enrolled.storage.journal.reserve(op.PrivateIntent(g), T)
    assert enrolled.verifier.verify_lease(lease, T)
    if invalid == "forged_lease":
        assert not enrolled.verifier.verify_lease(replace(lease, lease_id=uuid4()), T)
    else:
        if invalid == "revoked":
            enrolled.rev["revoked"] = [str(g.key.action_id)]
            enrolled.save()
        with pytest.raises(jn.NativeDenied):
            enrolled.verifier.verify_lease(lease, T + 22 if invalid == "expired" else T)
    assert enrolled.storage.reopen().read(grant()).state is op.JournalState.PENDING


def test_native_effects_are_blocked_before_reservation_even_if_enrolled(enrolled, monkeypatch):
    runtime = fixture_runtime(enrolled)
    monkeypatch.setattr(enrolled.storage.journal, "reserve", lambda *a: pytest.fail("no reservation"))
    for operation in (START, op.Operation.REUSE):
        result = runtime.to_json(operation, enrolled.proof(grant(operation)))
        assert json.loads(result)["code"] == "BLOCKED_NATIVE_START_ATTACH_OWNER_REQUIRED"
        assert json.loads(result)["receipt"]["effect"] == "NOT_ATTEMPTED"
    with pytest.raises(jn.NativeDenied):
        rt.WindowsExecutor().start_dedicated(None)
    with pytest.raises(jn.NativeDenied):
        rt.WindowsExecutor().reuse_owned(None)


@pytest.mark.parametrize("ci", ["CI", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "TF_BUILD"])
def test_ci_runner_has_no_native_authority(monkeypatch, ci):
    monkeypatch.setenv(ci, "true")
    monkeypatch.setattr(rt, "WindowsGrantVerifier", lambda: pytest.fail("must not enroll in CI"))
    runtime = rt.WindowsPrivateRuntime.from_local_enrollment()
    assert runtime.run(START).decision is op.Decision.HOLD


def test_no_implicit_enrollment_and_no_issue_commands(monkeypatch):
    monkeypatch.setattr(rt, "WindowsGrantVerifier", lambda: (_ for _ in ()).throw(PermissionError("private fixture")))
    runtime = rt.WindowsPrivateRuntime.from_local_enrollment()
    for operation in ("STATUS", "START", {"issue": 499}, None):
        assert runtime.run(operation).reason is op.Reason.INVALID_REQUEST
    assert runtime.run(op.Operation.STATUS).reason is op.Reason.AUTHORITY_REQUIRED
    assert rt.WindowsPrivateRuntime().run(START).effect is op.EffectAttempt.NOT_ATTEMPTED


def test_public_receipts_do_not_disclose_private_values(enrolled):
    runtime = fixture_runtime(enrolled)
    result = runtime.to_json(START, enrolled.proof(grant()))
    for private in (SID, TARGET.user_name, TARGET.dedicated_profile, str(enrolled.storage.path),
                    "webSocket", "cookie", "token", "--user-data-dir"):
        assert private not in result
    body = json.loads(result)
    assert body["source"] == "SOURCE_ONLY"
    assert body["native_effect"] == "NOT_ATTEMPTED"


@pytest.mark.parametrize("invalid", ["runner", "same_keys", "digest", "foreign_principal"])
def test_admin_anchor_policy_cannot_be_fabricated_or_reuse_roles(enrolled, monkeypatch, tmp_path, invalid):
    anchor = json.loads(jn.canonical(enrolled.anchor))
    assert auth._validate_anchor(anchor) == anchor
    if invalid == "runner":
        anchor["operator_sid"] = RUNNER
    elif invalid == "same_keys":
        # Same public key under the other principal still fails independence.
        source = enrolled.crypto.signers["issuer"].read_text().split()
        path = tmp_path / "same-key-signers"
        path.write_text(auth.IDENTITIES["human"] + " " + " ".join(source[1:]) + "\n")
        anchor["human_signers"] = dict(path=str(path), sha256=auth._digest(path.read_bytes()))
    elif invalid == "digest":
        anchor["verifier"]["sha256"] = "0" * 64
    else:
        path = tmp_path / "foreign-signers"
        path.write_text(enrolled.crypto.signers["issuer"].read_text().replace("wakehub-g2-issuer", "hostops-human"))
        anchor["issuer_signers"] = dict(path=str(path), sha256=auth._digest(path.read_bytes()))
    with pytest.raises(jn.NativeDenied):
        auth._validate_anchor(anchor)


@pytest.mark.parametrize("sid,session", [(RUNNER, 7), (SID, 0), (SID, 8)])
def test_native_identity_rejects_runner_session_zero_and_foreign_session(monkeypatch, sid, session):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(auth, "_windows_process_sid", lambda: sid)
    monkeypatch.setattr(auth, "_interactive_session", lambda: session)
    with pytest.raises(jn.NativeDenied):
        auth._desktop_identity(TARGET)


def test_non_windows_denies_before_loading_any_native_api(monkeypatch):
    # Replace only the authority's module reference, never global os.name.
    monkeypatch.setattr(auth, "os", SimpleNamespace(name="posix", environ={}))
    with pytest.raises(jn.NativeDenied):
        auth.windows_only()


def test_durable_outcome_proof_is_reverified_after_reopen(enrolled):
    g = grant()
    lease = enrolled.storage.journal.reserve(op.PrivateIntent(g), T)
    observation, raw = original_outcome(enrolled, lease)
    record = enrolled.storage.journal.read(g)
    assert enrolled.storage.journal.resolve_verified(record, observation, raw, enrolled.verifier)
    with closing(sqlite3.connect(enrolled.storage.path)) as db, db:
        db.execute("UPDATE intents SET proof=?", (b'{"body":{},"signature":"forged"}',))
    # A different key cannot skip a forged older OBSERVED row.
    enrolled.verifier.journal = enrolled.storage.reopen()
    fresh = grant()
    assert enrolled.verifier.verify_grant(enrolled.proof(fresh), START,
                                          op.LifecyclePolicy(SOURCE), T) is None


def test_reconcile_original_outcome_across_release_and_session_changes(enrolled):
    g = grant()
    lease = enrolled.storage.journal.reserve(op.PrivateIntent(g), T)
    observation, raw = original_outcome(enrolled, lease)
    next_source = replace(SOURCE, source_epoch=2, release_epoch=2, release_sha="b" * 40)
    next_target = replace(TARGET, session_id=8)
    enrolled.value["source"] = asdict(next_source)
    enrolled.value["target"] = asdict(next_target)
    enrolled.rev.update(source_epoch=2, release_epoch=2, epoch=2)
    enrolled.save()
    enrolled.verifier._load(T)
    reconcile = grant(op.Operation.RECONCILE_ONLY, source=next_source, target=next_target, reconciles=g.key)
    assert fixture_runtime(enrolled).run(reconcile.operation, enrolled.proof(reconcile)).decision is op.Decision.RECONCILED
    assert enrolled.storage.reopen().read(g).observation == observation


def test_owner_admission_and_existing_consumer_closure():
    # Trusted repository planner loads immutable base owners; no registry edits,
    # fake owner comments, Git writes or environment/guard changes are used.
    groups = minimum_test.approved_groups()
    records = impact_v21.load_baseline_component_owner_records(root=ROOT,
        base_sha=auth.TASK_PARENT, existing_groups=groups)
    baseline = (*groups, *(r["group"] for r in records))
    data = json.loads((ROOT / PATHS[-1]).read_text())
    # This owner is now committed on the authorized baseline. Re-admitting its
    # manifest as NEW would defeat the immutable owner rule behind #541.
    assert any(r["group"].name == "wakehub-g2-native" for r in records)
    changed = PATHS[:-1]
    impact = impact_v21.select_impact(root=ROOT, changed_paths=changed, groups=baseline)
    assert impact.runnable, impact.missing_mapping
    assert {"wakehub-g2-native", "wakehub-private-operator", "wakehub-native-inventory"} <= set(impact.selected_groups)
    assert set(impact.selected_tests) == {PATHS[3], "tests/test_wakehub_private_operator.py", "tests/test_wakehub_native_inventory.py"}
    assert "ONBOARD_CONSUMER_OWNER_CLOSURE" in impact.reason_codes
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    candidate = minimum_test.file_hashes(ROOT)
    base = dict(candidate)
    plan = impact_v21.registered_action_plan(repo=ROOT, base_sha=auth.TASK_PARENT,
        base_files=base, candidate_files=candidate, changed_paths=changed, required_acs=data["acs"],
        fallback_argv=profiles["mt01-component-onboarding"], groups=groups,
        registered_profiles=profiles, allowed_paths=changed)
    assert plan["selected_groups"] == sorted(impact.selected_groups)
    assert not plan["unknown_acs"]
    assert all(g["execute"] for g in plan["groups"]), plan["reason_codes"]
    for selected in plan["groups"]:
        assert selected["selected_profile"] == "mt01-component-onboarding"
        assert impact_v21.verified_onboarding_binding(workspace=ROOT, binding=selected["binding"],
            changed_paths=changed, allowed_paths=changed, base_sha=auth.TASK_PARENT,
            registered_profiles=profiles, expected_group=selected["group"])
