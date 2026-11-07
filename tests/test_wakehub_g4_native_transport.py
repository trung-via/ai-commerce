"""G4 OFFLINE framing/state/crypto tests plus read-only Windows kernel seam.

Synthetic PE/SCM/HANDLE/ACL/signer fixtures are NEVER native deployment proof.
No service, named pipe listener, enrollment, registry/ACL/task or browser write.
Scratch-only signatures and SQLite reuse the source-owned G2/G3 harness.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
import ctypes
import json
import os
from pathlib import Path
import queue
import sqlite3
import struct
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_commerce.wakehub import g4_service_transport as tr
from ai_commerce.wakehub import g4_desktop_transport as dt
from ai_commerce.wakehub import g4_release_provenance as rel
from ai_commerce.wakehub import g3_service_host as host
from ai_commerce.wakehub import g3_desktop_observer as ob
from ai_commerce.wakehub import native_broker as b
from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_journal as jn
from ai_commerce.wakehub import native_runtime as rt
from ai_commerce.wakehub import private_operator as op

from test_wakehub_g2_native import crypto, storage, enrolled, grant, SOURCE, TARGET, T, JID, SID, START
from test_wakehub_g2_broker import broker, signed_read
from test_wakehub_g3_native_enrollment import g3, broker_crypto, _SERVICE_INITIALIZE


class MemoryPipe(tr._NativePipe):
    """Fragmented byte stream; uses the actual production send/recv framing."""
    def __init__(self):
        self.handle = 123
        self.inbound = queue.Queue()
        self.buffer = bytearray()
        self.sent = []
        self.closed = False
        self.peer = None

    def _io(self, name, *, size=0, data=None, deadline):
        if self.closed or time.monotonic() >= deadline:
            raise TimeoutError()
        if name == "WriteFile":
            self.sent.append(data)
            for i in range(0, len(data), 7):
                self.peer.inbound.put(data[i:i + 7])
            return len(data)
        assert name == "ReadFile"
        if not self.buffer:
            try:
                self.buffer.extend(self.inbound.get(timeout=max(0, deadline - time.monotonic())))
            except queue.Empty:
                raise TimeoutError() from None
        result = bytes(self.buffer[:size]); del self.buffer[:size]
        return result


def pair():
    first, second = MemoryPipe(), MemoryPipe()
    first.peer, second.peer = second, first
    return first, second


class OfflineSigner:
    """Separate scratch replay journal, real signatures, no enrolled trust."""
    def __init__(self, g3, root, role):
        self.g3, self.role = g3, role
        self.path = root / (role + ".sqlite3")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE nonces(nonce TEXT PRIMARY KEY,digest TEXT,state TEXT)")

    def sign_once(self, payload, *, identity, namespace, nonce, origin_digest, deadline):
        assert time.monotonic() < deadline
        assert identity == (ob.BROKER_IDENTITY if self.role == "broker" else auth.IDENTITIES["observer"])
        assert namespace == (ob.CHALLENGE_NAMESPACE if self.role == "broker" else ob.STATUS_NAMESPACE)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO nonces VALUES(?,?,'UNKNOWN')", (nonce, rel.sha256(payload)))
            db.commit()  # Durable poison BEFORE touching the scratch key.
        body = jn.decode(base64.b64decode(jn.decode(payload)["payload_b64"]))
        challenge = body if self.role == "broker" else body["challenge"]
        assert origin_digest == rel.sha256(jn.canonical(challenge))
        signed = (self.g3.broker_crypto.signed(body) if self.role == "broker"
            else self.g3.broker.f.crypto.signed(body, "observer", "inventory", namespace=namespace))
        return jn.decode(signed)["signature"]


@pytest.fixture
def wire(g3, monkeypatch, tmp_path):
    monkeypatch.setattr(b.WindowsServiceAuthority, "__init__",
        lambda self, handle: _SERVICE_INITIALIZE(self, handle, clock=lambda: T))
    g = grant(op.Operation.STATUS)
    _, proof = signed_read(g3.broker, monkeypatch, g)
    monkeypatch.setattr(b.WindowsServiceAuthority, "__init__", _SERVICE_INITIALIZE)
    monkeypatch.setattr(b, "read_process_identity", lambda pid: g3.broker.client)
    monkeypatch.setattr(auth, "_desktop_identity", lambda target: None)
    server, client = pair()
    validate = lambda pipe: (g3.broker.anchor, g3.config)
    signer = OfflineSigner(g3, tmp_path, "broker")
    desktop_signer = OfflineSigner(g3, tmp_path, "observer")
    engine = tr._StatusExchange(g3.host, signer, validate)
    return SimpleNamespace(g3=g3, server=server, client=client, validate=validate, signer=signer,
        desktop_signer=desktop_signer, engine=engine, request=g3.broker.request(g, proof=proof))


def exchange(wire):
    with ThreadPoolExecutor(2) as pool:
        server = pool.submit(wire.engine.serve, wire.server)
        client = pool.submit(dt._one_shot, wire.client, wire.request, wire.desktop_signer,
                             wire.validate, clock=lambda: T)
        return server.result(timeout=8), client.result(timeout=8)


def test_real_signed_status_over_fragmented_frames_preserves_pause_and_privacy(wire):
    server, client = exchange(wire)
    assert server == client and client.decision is op.Decision.STATUS_OBSERVED
    assert client.effect is op.EffectAttempt.NOT_ATTEMPTED
    raw = client.to_json()
    for secret in ("https://chatgpt.com/", SID, sys.executable, "payload_b64", "signature", "nonce"):
        assert secret not in raw
    assert wire.g3.rows()[0][1] == "DONE"
    assert wire.g3.broker.f.storage.reopen().read(grant()).state is op.JournalState.EMPTY
    assert asdict(rel.SourceReadiness())["STATUS_NATIVE_OBSERVED"] is False
    for signer in (wire.signer, wire.desktop_signer):
        with closing(sqlite3.connect(signer.path)) as db:
            assert db.execute("SELECT state FROM nonces").fetchall() == [("UNKNOWN",)]


def test_lost_ack_reopen_reads_exact_original_without_reexecution(wire, monkeypatch):
    original = wire.server.send
    def lose_ack(value, deadline):
        if value["schema"] == "WakeHubG4Receipt.v1":
            raise BrokenPipeError()
        return original(value, deadline)
    monkeypatch.setattr(wire.server, "send", lose_ack)
    # Drive completion from a peer which does not wait for the intentionally lost ACK.
    with ThreadPoolExecutor(2) as pool:
        result = pool.submit(wire.engine.serve, wire.server)
        wire.client.send(dict(schema="WakeHubG4Hello.v1", operation="STATUS"), time.monotonic() + 1)
        frame = wire.client.recv(time.monotonic() + 2)
        challenge = ob.challenge_from(jn.decode(tr._unb64(frame["signed_challenge"]))["body"])
        wire.client.send(dict(schema="WakeHubG4Completion.v1", signed_status=tr._b64(wire.g3.status(challenge)),
                              signed_request=tr._b64(wire.request)), time.monotonic() + 2)
        with pytest.raises(BrokenPipeError): result.result(timeout=5)
    journal = b.BrokerRequests(wire.g3.broker.requests, owner_sid=b.service_sid(), journal_id=JID)
    body = jn.decode(wire.request)["body"]; digest = rel.sha256(jn.canonical(body))
    original = journal.readback(body["nonce"], digest)
    assert jn.decode(original)["decision"] == op.Decision.STATUS_OBSERVED.value
    assert not journal.claim(body["nonce"], digest)
    assert journal.readback(body["nonce"], digest) == original
    with pytest.raises(ValueError): journal.readback(body["nonce"], "0" * 64)


@pytest.mark.parametrize("operation", [START, op.Operation.REUSE, op.Operation.RECONCILE_ONLY])
def test_hostile_signed_operation_is_blocked_at_transport_before_runtime(wire, monkeypatch, operation):
    wire.request = wire.g3.broker.request(grant(operation))
    monkeypatch.setattr(host, "_connected_runtime", lambda *a: pytest.fail("non-STATUS dispatch"))
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(wire.engine.serve, wire.server)
        wire.client.send(dict(schema="WakeHubG4Hello.v1", operation="STATUS"), time.monotonic() + 1)
        frame = wire.client.recv(time.monotonic() + 2)
        challenge = ob.challenge_from(jn.decode(tr._unb64(frame["signed_challenge"]))["body"])
        wire.client.send(dict(schema="WakeHubG4Completion.v1", signed_status=tr._b64(wire.g3.status(challenge)),
            signed_request=tr._b64(wire.request)), time.monotonic() + 2)
        with pytest.raises(ValueError): future.result(timeout=5)
    assert wire.g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("fault", ["extra_path", "extra_pid", "extra_command", "start", "wrong_schema"])
def test_initial_message_cannot_select_an_os_target(wire, fault):
    hello = dict(schema="WakeHubG4Hello.v1", operation="STATUS")
    if fault.startswith("extra_"): hello[fault[6:]] = "attacker-selected"
    elif fault == "start": hello["operation"] = "START"
    else: hello["schema"] = "GitHubIssue.v1"
    wire.client.send(hello, time.monotonic() + 1)
    with pytest.raises(ValueError): wire.engine.serve(wire.server)
    assert wire.g3.rows() == []


@pytest.mark.parametrize("fault", ["zero", "oversize", "truncated", "duplicate_json", "nan_json"])
def test_bounded_parser_rejects_bad_or_partial_frames_without_retry(fault):
    server, client = pair()
    raw = {"zero": struct.pack("!I", 0), "oversize": struct.pack("!I", tr.MAX_FRAME + 1),
           "truncated": struct.pack("!I", 100) + b'{}',
           "duplicate_json": struct.pack("!I", 13) + b'{"a":1,"a":2}',
           "nan_json": struct.pack("!I", 9) + b'{"a":NaN}'}[fault]
    server.inbound.put(raw)
    with pytest.raises((ValueError, TimeoutError)):
        server.recv(time.monotonic() + 0.05)


def test_handler_crash_mid_frame_leaves_nonce_poisoned(wire, monkeypatch):
    deadline = time.monotonic() + 1
    wire.client.send(dict(schema="WakeHubG4Hello.v1", operation="STATUS"), deadline)
    receive = wire.server.recv
    calls = 0
    def fail_after_challenge(deadline):
        nonlocal calls
        calls += 1
        if calls == 2: raise EOFError()
        return receive(deadline)
    monkeypatch.setattr(wire.server, "recv", fail_after_challenge)
    with pytest.raises(EOFError): wire.engine.serve(wire.server)
    nonce, state, receipt = wire.g3.rows()[0]
    assert (state, receipt) == ("UNKNOWN", None)
    journal = b.BrokerRequests(wire.g3.path, owner_sid=b.service_sid(), journal_id=JID)
    with closing(sqlite3.connect(wire.g3.path)) as db:
        digest, = db.execute("SELECT digest FROM requests WHERE nonce=?", (nonce,)).fetchone()
    assert journal.readback(nonce, digest) is None and not journal.claim(nonce, digest)


def test_two_clients_cannot_abandon_or_complete_each_others_request(wire, monkeypatch):
    entered, proceed = threading.Event(), threading.Event()
    sign = wire.signer.sign_once
    def blocked(*a, **kw):
        entered.set(); assert proceed.wait(2)
        return sign(*a, **kw)
    monkeypatch.setattr(wire.signer, "sign_once", blocked)
    wire.client.send(dict(schema="WakeHubG4Hello.v1", operation="STATUS"), time.monotonic() + 1)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(wire.engine.serve, wire.server)
        assert entered.wait(2)
        with pytest.raises(ValueError): wire.engine.serve(MemoryPipe())
        assert wire.g3.host._pending is not None and len(wire.g3.rows()) == 1
        wire.server.closed = True; proceed.set()
        with pytest.raises(TimeoutError): first.result(timeout=4)
    assert wire.g3.rows()[0][1:] == ("UNKNOWN", None)


@pytest.mark.parametrize("fault", ["source", "generation", "logon", "token", "locked", "revoked", "corrupt_journal"])
def test_connected_exchange_revalidates_authority_at_completion(wire, monkeypatch, fault):
    original = wire.server.recv
    calls = 0
    def drift(deadline):
        nonlocal calls
        value = original(deadline); calls += 1
        if calls == 2:
            if fault == "source": wire.g3.config["source"]["source_epoch"] += 1
            elif fault == "generation": wire.g3.config["generation"] += 1
            elif fault == "logon": wire.g3.config["logon_id"] = "0000000000099999"
            elif fault == "token": wire.g3.broker.facts["client"]["created_at"] = T
            elif fault == "locked": monkeypatch.setattr(ob, "verify_status", jn.deny)
            elif fault == "revoked": monkeypatch.setattr(auth.WindowsGrantVerifier, "verify_grant", lambda *a: None)
            elif fault == "corrupt_journal":
                with closing(sqlite3.connect(wire.g3.path)) as db, db:
                    db.execute("UPDATE identity SET journal_id='corrupted'")
        return value
    monkeypatch.setattr(wire.server, "recv", drift)
    with ThreadPoolExecutor(2) as pool:
        server = pool.submit(wire.engine.serve, wire.server)
        client = pool.submit(dt._one_shot, wire.client, wire.request, wire.desktop_signer,
                             wire.validate, clock=lambda: T)
        for future in (server, client):
            try:
                receipt = future.result(timeout=8)
            except (jn.NativeDenied, TimeoutError):
                pass  # Changed custody or no ACK is a refusal, never success.
            else:
                assert receipt.decision in (op.Decision.HOLD, op.Decision.UNKNOWN_EFFECT)
                assert receipt.effect is op.EffectAttempt.NOT_ATTEMPTED
    _, state, original = wire.g3.rows()[0]
    if state == "DONE":
        assert jn.decode(original)["decision"] in (op.Decision.HOLD.value, op.Decision.UNKNOWN_EFFECT.value)
    else:
        assert (state, original) == ("UNKNOWN", None)


@pytest.mark.parametrize("fault", ["inherited", "world", "user_write", "null", "unprotected", "wrong_owner"])
def test_strict_pipe_acl_rejects_foreign_or_inherited_authority(monkeypatch, fault):
    aces = [(0, 0, 0x10000000, sid) for sid in {*jn.OS_TRUSTED_SIDS, b.service_sid()}]
    aces.append((0, 0, 0x12008b, SID)); owner = b.service_sid()
    if fault == "inherited": aces[-1] = (0, 16, 0x12008b, SID)
    elif fault == "world": aces.append((0, 0, 3, "S-1-1-0"))
    elif fault == "user_write": aces[-1] = (0, 0, 0x120007, SID)
    elif fault == "null": aces = None
    elif fault == "wrong_owner": owner = SID
    monkeypatch.setattr(jn.WindowsPathCustody, "_descriptor", lambda *a, **kw: (owner, aces))
    monkeypatch.setattr(b, "_protected_dacl", lambda *a, **kw: jn.deny() if fault == "unprotected" else None)
    with pytest.raises(ValueError): tr._strict_pipe_acl(123, SID)


def test_fixed_pipe_api_contract_and_no_inherited_handle(monkeypatch):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(tr, "_strict_pipe_acl", lambda *a: None)
    observed = []
    def api(dll, name, args, result):
        if name == "ConvertStringSecurityDescriptorToSecurityDescriptorW":
            def convert(sddl, revision, sd, size):
                assert "D:P" in sddl and "0x0012008b" in sddl and "WD" not in sddl
                sd._obj.value = 999; return True
            return convert
        if name == "CreateNamedPipeW":
            def create(path, mode, flags, instances, output, input_, timeout, sa):
                assert path == tr.PIPE_PATH and mode == 3 | 0x40000000 | 0x80000
                assert flags == 8 and instances == 1 and not sa._obj.inherit
                observed.append(name); return 123
            return create
        if name == "CreateFileW":
            def client(path, access, share, sa, disposition, flags, template):
                assert path == tr.PIPE_PATH and access == 0x80000002 and disposition == 3
                assert flags == 0x40000000 | 0x100000 and sa is None
                observed.append(name); return 124
            return client
        assert name in ("LocalFree", "CloseHandle")
        return lambda *a: True
    monkeypatch.setattr(b, "_api", api)
    tr._NativePipe.create_server(SID, threading.Event()).close()
    tr._NativePipe.connect_client(threading.Event()).close()
    assert observed == ["CreateNamedPipeW", "CreateFileW"]


@pytest.mark.parametrize("confirmed", [True, False])
def test_cancel_is_not_completion_and_pending_memory_is_retained(monkeypatch, confirmed):
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 997)
    stop = threading.Event(); pipe = tr._NativePipe(123, server=True, stop=stop)
    calls = []; before = len(tr._QUARANTINED_IO)
    def api(dll, name, args, result):
        def call(*a):
            calls.append(name)
            if name == "CreateEventW": return 777
            if name == "ReadFile": return False
            if name == "WaitForSingleObject":
                if "CancelIoEx" in calls: return 0 if confirmed else 258
                stop.set(); return 258
            return True
        return call
    monkeypatch.setattr(b, "_api", api)
    with pytest.raises(TimeoutError if confirmed else tr._CancellationUnproven):
        pipe._io("ReadFile", size=8, deadline=time.monotonic() + 1)
    assert "CancelIoEx" in calls
    assert ("CloseHandle" in calls) is confirmed
    assert len(tr._QUARANTINED_IO) == before + (not confirmed)
    assert pipe.poisoned is (not confirmed)


def test_scm_gate_reports_stopped_without_listener_or_trust(monkeypatch):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(sys, "argv", ["WakeHubBroker.exe"])
    monkeypatch.setattr(tr._NativePipe, "create_server", lambda *a: pytest.fail("listener before trust"))
    states = []
    def api(dll, name, args, result):
        if name == "RegisterServiceCtrlHandlerExW":
            return lambda service, handler, context: 123 if service == b.SERVICE else 0
        if name == "SetServiceStatus":
            return lambda token, ptr: states.append((ptr._obj.state, ptr._obj.exit)) or True
        assert name == "StartServiceCtrlDispatcherW"
        def dispatch(table):
            assert table[0].name == b.SERVICE and table[1].name is None
            table[0].main(0, None); return True
        return dispatch
    monkeypatch.setattr(b, "_api", api)
    tr.scm_entrypoint()
    assert states == [(2, 0), (1, 1066)]


def test_scm_control_lifecycle_is_only_a_simulation_not_enrollment(monkeypatch):
    monkeypatch.setattr(auth, "windows_only", lambda: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(sys, "argv", ["WakeHubBroker.exe"])
    states, callbacks = [], []
    def api(dll, name, args, result):
        if name == "RegisterServiceCtrlHandlerExW":
            return lambda service, handler, context: callbacks.append(handler) or 123
        if name == "SetServiceStatus":
            def report(token, ptr):
                row = ptr._obj
                states.append((row.state, row.controls, row.hint)); return True
            return report
        assert name == "StartServiceCtrlDispatcherW"
        return lambda table: table[0].main(0, None) or True
    def loop(stop, ready):
        ready()
        assert callbacks[0](99, 0, None, None) == 120
        assert callbacks[0](4, 0, None, None) == 0
        assert callbacks[0](1, 0, None, None) == 0 and stop.is_set()
    monkeypatch.setattr(b, "_api", api)
    monkeypatch.setattr(tr, "_service_loop", loop)
    tr.scm_entrypoint()
    assert [row[0] for row in states] == [2, 4, 4, 3, 1]
    assert states[-2][1:] == (0, 1000)
    assert rel.SourceReadiness().SERVICE_INSTALLED_PROVEN is False


@pytest.mark.parametrize("fault", [None, "absent", "wrong_start", "dependencies", "wrong_state", "wrong_type", "wrong_sid"])
def test_read_only_scm_contract_requires_fixed_installed_own_process(monkeypatch, fault):
    from ctypes import wintypes as wt
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: object())
    monkeypatch.setattr(jn.WindowsPathCustody, "_descriptor", lambda *a, **kw: ("S-1-5-18", []))
    monkeypatch.setattr(b, "_protected_dacl", lambda *a, **kw: None)
    class Config(ctypes.Structure):
        _fields_ = [("kind", wt.DWORD), ("start", wt.DWORD), ("error", wt.DWORD),
            ("binary", wt.LPWSTR), ("group", wt.LPWSTR), ("tag", wt.DWORD),
            ("dependencies", wt.LPWSTR), ("account", wt.LPWSTR), ("display", wt.LPWSTR)]
    strings = [ctypes.create_unicode_buffer(value) for value in
        ('"' + rel.SERVICE_IMAGE + '"', "NT SERVICE\\" + b.SERVICE, "unexpected")]
    closed = []
    def api(dll, name, args, result):
        if name == "OpenSCManagerW":
            return lambda machine, database, access: 100 if (machine, database, access) == (None, None, 1) else 0
        if name == "OpenServiceW":
            return lambda manager, service, access: 0 if fault == "absent" else 200 if (service, access) == (b.SERVICE, 5) else 0
        if name == "CloseServiceHandle": return lambda handle: closed.append(handle) or True
        if name == "QueryServiceConfigW":
            def config(service, raw, size, needed):
                cfg = ctypes.cast(raw, ctypes.POINTER(Config)).contents
                cfg.kind = 0x10; cfg.start = 2 if fault == "wrong_start" else 3
                cfg.binary = ctypes.cast(strings[0], wt.LPWSTR)
                cfg.account = ctypes.cast(strings[1], wt.LPWSTR)
                if fault == "dependencies": cfg.dependencies = ctypes.cast(strings[2], wt.LPWSTR)
                return True
            return config
        if name == "QueryServiceStatusEx":
            def status(service, kind, raw, size, needed):
                raw[0] = 0x20 if fault == "wrong_type" else 0x10
                raw[1] = 2 if fault == "wrong_state" else 4; raw[7] = 1111
                return True
            return status
        assert name == "QueryServiceConfig2W"
        def sid(service, kind, raw, size, needed):
            raw._obj.value = 0 if fault == "wrong_sid" else 1; return True
        return sid
    monkeypatch.setattr(b, "_api", api)
    if fault:
        with pytest.raises(ValueError): b._scm_process()
    else:
        result = b._scm_process()
        assert result["pid"] == 1111 and result["account"] == "NT SERVICE\\" + b.SERVICE
    assert closed == ([100] if fault == "absent" else [200, 100])


def test_public_entrypoints_never_accept_ci_or_issue_as_enrollment(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for call in (rel.require_installed_release, lambda: dt.status_once(b'{"issue":564}'),
                 lambda: dt._load_enrolled_signer("broker"), lambda: dt._load_enrolled_signer("observer")):
        with pytest.raises(rel.PackagingOwnerEscalation, match="^PACKAGING_OWNER_ESCALATION$"):
            call()
    for field in asdict(rel.SourceReadiness()):
        with pytest.raises(ValueError): rel.SourceReadiness(**{field: False if field == "SOURCE_CANDIDATE" else True})


def pe_fixture():
    """Deliberately fake structural fixture, never a compiled service binary."""
    raw = bytearray(512); raw[:2] = b"MZ"; struct.pack_into("<I", raw, 0x3c, 128)
    raw[128:132] = b"PE\0\0"; struct.pack_into("<HH", raw, 132, 0x8664, 1)
    struct.pack_into("<HH", raw, 148, 240, 2); struct.pack_into("<H", raw, 152, 0x20b)
    struct.pack_into("<H", raw, 220, 3)
    return bytes(raw)


@pytest.fixture
def artifacts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = rel.build_specification(root, {name: "1" * 64 for name in rel.BUILD_PINS})
    builds = [tmp_path / name for name in ("first", "second", "signed")]
    for build in builds:
        build.mkdir()
        for name in ("WakeHubBroker.exe", "WakeHubDesktop.exe"): (build / name).write_bytes(pe_fixture())
    pins = {name: rel.sha256(pe_fixture()) for name in ("WakeHubBroker.exe", "WakeHubDesktop.exe")}
    manifest = dict(schema="WakeHubG4ReleaseManifest.v1", spec_sha256=rel.sha256(jn.canonical(spec)),
                    source_generation=1, unsigned=dict(pins), signed=dict(pins))
    return SimpleNamespace(root=root, spec=spec, builds=builds, manifest=manifest)


def readback(a):
    return rel.verify_release_readback(a.spec, a.manifest, source_root=a.root,
        first_build=a.builds[0], second_build=a.builds[1], signed_build=a.builds[2])


def test_actual_artifact_readback_never_promotes_structural_pe_to_native_proof(artifacts):
    result = readback(artifacts)
    assert result["state"] == "ARTIFACT_BYTES_MATCH_SOURCE_ONLY"
    assert result["readiness"] == asdict(rel.SourceReadiness())
    assert sum(result["readiness"].values()) == 1


@pytest.mark.parametrize("fault", ["unsigned_drift", "extra_file", "incompatible_pe", "unpinned", "source", "generation", "toolchain", "path"])
def test_release_readback_rejects_incomplete_or_manipulated_builds(artifacts, fault):
    a = artifacts
    if fault == "unsigned_drift": (a.builds[1] / "WakeHubBroker.exe").write_bytes(pe_fixture() + b"drift")
    elif fault == "extra_file": (a.builds[0] / "unreviewed.pth").write_text("import os")
    elif fault == "incompatible_pe":
        for build in a.builds: (build / "WakeHubBroker.exe").write_bytes(b"python module")
        for field in ("unsigned", "signed"): a.manifest[field]["WakeHubBroker.exe"] = rel.sha256(b"python module")
    elif fault == "unpinned": a.manifest["signed"]["WakeHubBroker.exe"] = "0" * 64
    elif fault == "source": a.spec["sources"][rel.SOURCE_CLOSURE[0]] = "0" * 64
    elif fault == "generation": a.manifest["source_generation"] = True
    elif fault == "toolchain": a.spec["toolchain"].pop("bootloader")
    elif fault == "path": a.manifest["signed"]["../WakeHubBroker.exe"] = "0" * 64
    with pytest.raises((ValueError, KeyError)): readback(a)


def test_real_windows_kernel_token_and_logon_read_only():
    if os.name != "nt": pytest.skip("Windows kernel read-only seam")
    process = b.read_process_identity(os.getpid())
    assert process.pid == os.getpid() and process.created_at > 0
    logon = ob.read_logon_id(os.getpid())
    assert len(logon) == 16 and int(logon, 16) > 0
    assert rel.SourceReadiness().SERVICE_INSTALLED_PROVEN is False
