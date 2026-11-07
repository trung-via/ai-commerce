"""Source adapter for a SEPARATELY provisioned Windows virtual-account service.

No installation, listener, signing, key generation, GUI or browser effect exists
here. In particular a service SID string or an offline fixture is not enrollment.
The service host must supply its connected server pipe HANDLE. Every boundary
rechecks the admin-protected anchor, SCM, process primary tokens, pipe endpoints,
protected signer/ledger ACLs and signed desktop observation. Client input never
selects a service, path, PID, port, URL, executable or command.

Broker request replay protection and the original effect ledger are distinct:
requests enter durable UNKNOWN BEFORE dispatch, and are never retried. An
unanswered request can only be followed by a NEW signed RECONCILE_ONLY request
for the original effect; it cannot cause the original native operation again.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import ctypes
from ctypes import wintypes as wt
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID

from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_inventory as inv
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.native_journal import (
    WindowsPathCustody, OS_TRUSTED_SIDS, canonical, decode, deny, shape,
    SQLitePrivateJournal,
)


SERVICE = "AICommerceWakeHub9444Broker"
PIPE = r"\Device\NamedPipe\AICommerce-WakeHub-9444-PrivateBroker-v1"
ANCHOR = r"SOFTWARE\AI-Commerce\WakeHubG2PrivateBroker"
NAMESPACE = "wakehub-private-broker-9444-{}@ai-commerce"


def service_sid():
    # Windows' service SID derivation. This value alone establishes NO trust.
    import struct
    parts = struct.unpack("<5I", hashlib.sha1(SERVICE.upper().encode("utf-16-le")).digest())
    return "S-1-5-80-" + "-".join(map(str, parts))


def _signed(raw, role, purpose, anchor):
    body = decode(raw)
    shape(body, "body signature")
    if purpose not in ("request", "desktop") or type(body["signature"]) is not str:
        deny()
    auth.verify_signature(auth.signing_payload(body["body"]), body["signature"],
        Path(anchor[role + "_signers"]["path"]), auth.IDENTITIES[role],
        NAMESPACE.format(purpose), Path(anchor["verifier"]["path"]))
    return body["body"]


class ServiceCustody(WindowsPathCustody):
    """Private leaves AND containing directories exclude desktop/runner reads.

    Public code/registry anchors remain admin-owned. The private service tree
    also requires a protected DACL; inherited user grants are not a substitute.
    """
    def __init__(self):
        super().__init__(service_sid())

    def check(self, path, *, directory=False):
        path = super().check(path, directory=directory)
        for item in ((path,) if directory else (path, path.parent)):
            owner, aces = self._descriptor(str(item))
            self.check_descriptor(owner, aces, {*OS_TRUSTED_SIDS, service_sid()}, private=True)
            _protected_dacl(str(item), 1)
            if not any(kind == 0 and not flags & 8 and sid == service_sid()
                       and (mask & 0x10000000 or mask & 0x1f01ff == 0x1f01ff)
                       for kind, flags, mask, sid in aces):
                deny()
        return path


def _protected_dacl(name, object_type, *, handle=False):
    auth.windows_only()
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [wt.LPWSTR, wt.DWORD, wt.DWORD,
        ptr, ptr, ptr, ptr, ctypes.POINTER(ptr)]
    adv.GetNamedSecurityInfoW.restype = wt.DWORD
    adv.GetSecurityInfo.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD,
        ptr, ptr, ptr, ptr, ctypes.POINTER(ptr)]
    adv.GetSecurityInfo.restype = wt.DWORD
    adv.GetSecurityDescriptorControl.argtypes = [ptr, ctypes.POINTER(wt.WORD), ctypes.POINTER(wt.DWORD)]
    adv.GetSecurityDescriptorControl.restype = wt.BOOL
    kernel.LocalFree.argtypes = [ptr]
    sd, control, revision = ptr(), wt.WORD(), wt.DWORD()
    reader = adv.GetSecurityInfo if handle else adv.GetNamedSecurityInfoW
    if reader(name, object_type, 4, None, None, None, None, ctypes.byref(sd)):
        deny()
    try:
        if not adv.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)) or not control.value & 0x1000:
            deny()
    finally:
        kernel.LocalFree(sd)


def _api(dll, name, args, result):
    fn = getattr(dll, name)
    fn.argtypes, fn.restype = args, result
    return fn


def read_process_identity(pid):
    """Read-only kernel seam, also usable diagnostically without enrollment.

    It proves one live primary token/image/start/session, NOT signer custody,
    desktop unlocked state, service installation, or browser ownership.
    """
    if os.name != "nt" or type(pid) is not int or not 0 < pid < 2**32:
        deny()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    ptr = ctypes.c_void_p
    close = _api(kernel, "CloseHandle", [wt.HANDLE], wt.BOOL)
    process = _api(kernel, "OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE)(0x101000, False, pid)
    if not process:
        deny()
    token = wt.HANDLE()
    try:
        if not _api(adv, "OpenProcessToken", [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)], wt.BOOL)(process, 8, ctypes.byref(token)):
            deny()
        get = _api(adv, "GetTokenInformation", [wt.HANDLE, ctypes.c_int, ptr, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL)
        def info(kind):
            size = wt.DWORD()
            get(token, kind, None, 0, ctypes.byref(size))
            if not 0 < size.value <= 65536:
                deny()
            buffer = ctypes.create_string_buffer(size.value)
            if not get(token, kind, buffer, size.value, ctypes.byref(size)):
                deny()
            return buffer
        if ctypes.cast(info(8), ctypes.POINTER(wt.DWORD)).contents.value != 1:
            deny()  # TokenPrimary only; no desktop impersonation token.
        sid_buffer = info(1)
        sid = ctypes.cast(sid_buffer, ctypes.POINTER(ptr)).contents
        out = wt.LPWSTR()
        if not _api(adv, "ConvertSidToStringSidW", [ptr, ctypes.POINTER(wt.LPWSTR)], wt.BOOL)(sid, ctypes.byref(out)):
            deny()
        try:
            user_sid = out.value
        finally:
            _api(kernel, "LocalFree", [ptr], ptr)(ctypes.cast(out, ptr))
        session = ctypes.cast(info(12), ctypes.POINTER(wt.DWORD)).contents.value
        path, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
        if not _api(kernel, "QueryFullProcessImageNameW", [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)], wt.BOOL)(process, 0, path, ctypes.byref(size)):
            deny()
        times = [wt.FILETIME() for _ in range(4)]
        if not _api(kernel, "GetProcessTimes", [wt.HANDLE, *([ctypes.POINTER(wt.FILETIME)] * 4)], wt.BOOL)(process, *map(ctypes.byref, times)):
            deny()
        if _api(kernel, "WaitForSingleObject", [wt.HANDLE, wt.DWORD], wt.DWORD)(process, 0) != 258:
            deny()
        created = ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 1e7 - 11644473600
        return inv.ProcessIdentity(pid, created, path.value, user_sid, session)
    finally:
        if token:
            close(token)
        close(process)


def _scm_process():
    """Query the one fixed SCM object; never open change/start/stop rights."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    ptr = ctypes.c_void_p
    manager = _api(adv, "OpenSCManagerW", [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD], wt.HANDLE)(None, None, 1)
    if not manager:
        deny()
    close = _api(adv, "CloseServiceHandle", [wt.HANDLE], wt.BOOL)
    service = None
    try:
        service = _api(adv, "OpenServiceW", [wt.HANDLE, wt.LPCWSTR, wt.DWORD], wt.HANDLE)(manager, SERVICE, 5)
        if not service:
            deny()
        class Config(ctypes.Structure):
            _fields_ = [("kind", wt.DWORD), ("start", wt.DWORD), ("error", wt.DWORD),
                ("binary", wt.LPWSTR), ("group", wt.LPWSTR), ("tag", wt.DWORD),
                ("dependencies", wt.LPWSTR), ("account", wt.LPWSTR), ("display", wt.LPWSTR)]
        buffer, needed = ctypes.create_string_buffer(8192), wt.DWORD()
        if not _api(adv, "QueryServiceConfigW", [wt.HANDLE, ptr, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL)(service, buffer, len(buffer), ctypes.byref(needed)):
            deny()
        cfg = ctypes.cast(buffer, ctypes.POINTER(Config)).contents
        binary, account, kind = cfg.binary, cfg.account, cfg.kind
        status = (wt.DWORD * 9)()
        if not _api(adv, "QueryServiceStatusEx", [wt.HANDLE, ctypes.c_int, ptr, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL)(service, 0, status, ctypes.sizeof(status), ctypes.byref(needed)) or status[1] != 4 or status[0] != 0x10:
            deny()
        sid_type = wt.DWORD()
        if not _api(adv, "QueryServiceConfig2W", [wt.HANDLE, wt.DWORD, ptr, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL)(service, 5, ctypes.byref(sid_type), 4, ctypes.byref(needed)) or sid_type.value not in (1, 3):
            deny()
        owner, aces = WindowsPathCustody._descriptor(SERVICE, 5)
        # Service control/configuration rights are different from file rights.
        if owner not in OS_TRUSTED_SIDS or aces is None:
            deny()
        for ace, flags, mask, sid in aces:
            if ace not in (0, 1) or (ace == 0 and not flags & 8 and sid not in OS_TRUSTED_SIDS
                    and mask & (0xF0000 | 0x10000000 | 0x40000000 | 0x1f2)):
                deny()
        _protected_dacl(SERVICE, 5)
        return dict(pid=status[7], binary=binary, account=account, kind=kind, sid_type=sid_type.value)
    finally:
        if service:
            close(service)
        close(manager)


def observe_connection(handle):
    """Server-side local kernel authentication, never client-supplied facts."""
    auth.windows_only()
    if type(handle) is not int or handle <= 0:
        deny()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    # A service host may not authenticate while impersonating a desktop token.
    thread = _api(kernel, "GetCurrentThread", [], wt.HANDLE)()
    token = wt.HANDLE()
    if _api(adv, "OpenThreadToken", [wt.HANDLE, wt.DWORD, wt.BOOL, ctypes.POINTER(wt.HANDLE)], wt.BOOL)(thread, 8, True, ctypes.byref(token)):
        _api(kernel, "CloseHandle", [wt.HANDLE], wt.BOOL)(token)
        deny()
    if ctypes.get_last_error() != 1008:
        deny()
    flags = wt.DWORD()
    if not _api(kernel, "GetNamedPipeInfo", [wt.HANDLE, ctypes.POINTER(wt.DWORD), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], wt.BOOL)(handle, ctypes.byref(flags), None, None, None) or not flags.value & 1:
        deny()
    # Query the kernel object name; a handle to a differently named pipe fails.
    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wt.USHORT), ("maximum", wt.USHORT), ("buffer", ctypes.c_void_p)]
    buffer, needed = ctypes.create_string_buffer(4096), wt.ULONG()
    ntdll = ctypes.WinDLL("ntdll")
    if _api(ntdll, "NtQueryObject", [wt.HANDLE, wt.ULONG, ctypes.c_void_p, wt.ULONG, ctypes.POINTER(wt.ULONG)], wt.LONG)(handle, 1, buffer, len(buffer), ctypes.byref(needed)) != 0:
        deny()
    name = ctypes.cast(buffer, ctypes.POINTER(UnicodeString)).contents
    if not name.buffer or ctypes.wstring_at(name.buffer, name.length // 2) != PIPE:
        deny()
    facts = {}
    for side in ("Client", "Server"):
        for facet in ("ProcessId", "SessionId"):
            value = wt.ULONG()
            if not _api(kernel, "GetNamedPipe" + side + facet, [wt.HANDLE, ctypes.POINTER(wt.ULONG)], wt.BOOL)(handle, ctypes.byref(value)):
                deny()
            facts[side + facet] = value.value
    if facts["ServerProcessId"] != os.getpid():
        deny()
    _check_pipe_acl(handle)
    server = read_process_identity(facts["ServerProcessId"])
    client = read_process_identity(facts["ClientProcessId"])
    if (server.session_id != facts["ServerSessionId"] or client.session_id != facts["ClientSessionId"]):
        deny()
    return dict(server=asdict(server), client=asdict(client), scm=_scm_process())


def _check_pipe_acl(handle):
    owner, aces = WindowsPathCustody._descriptor(handle, 1, handle=True)
    if owner not in {*OS_TRUSTED_SIDS, service_sid()} or aces is None:
        deny()
    # Client read/write-data is permitted. Creating another server instance,
    # replacing/deleting the pipe or changing its ACL/owner is forbidden.
    for kind, flags, mask, sid in aces:
        if kind not in (0, 1) or (kind == 0 and not flags & 8
                and sid not in {*OS_TRUSTED_SIDS, service_sid()}
                and mask & (0xD0000 | 0x10000000 | 0x40000000 | 4)):
            deny()
    _protected_dacl(handle, 1, handle=True)


def validate_peer(broker, facts):
    """Offline policy seam; calling it does not establish actual Windows trust."""
    shape(broker, "schema task_parent authority service desktop_peer requests_path private_key_path desktop_path")
    shape(broker["service"], "name process sha256")
    shape(broker["desktop_peer"], "process sha256")
    shape(facts, "server client scm")
    shape(facts["scm"], "pid binary account kind sid_type")
    server = inv.ProcessIdentity(**broker["service"]["process"])
    client = inv.ProcessIdentity(**broker["desktop_peer"]["process"])
    scm = facts["scm"]
    if (broker["schema"] != "WakeHubPrivateBrokerAnchor.v1" or broker["task_parent"] != auth.TASK_PARENT
            or broker["service"]["name"] != SERVICE or server.user_sid != service_sid()
            or server.session_id != 0 or client.session_id <= 0 or client.user_sid == service_sid()
            or facts["server"] != asdict(server) or facts["client"] != asdict(client)
            or scm["pid"] != server.pid or scm["binary"] not in (server.executable, '"' + server.executable + '"')
            or scm["account"] != "NT SERVICE\\" + SERVICE or scm["kind"] != 0x10
            or scm["sid_type"] not in (1, 3)
            or any(type(scm[k]) is not int for k in ("pid", "kind", "sid_type"))):
        deny()
    for pin in (broker["service"], broker["desktop_peer"]):
        if type(pin["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]):
            deny()
    return server, client


def _read_broker_anchor():
    auth.windows_only()
    import winreg
    for suffix in (r"SOFTWARE", r"SOFTWARE\AI-Commerce", ANCHOR):
        WindowsPathCustody.check_descriptor(*WindowsPathCustody._descriptor("MACHINE\\" + suffix, 4), OS_TRUSTED_SIDS)
    _protected_dacl("MACHINE\\" + ANCHOR, 4)
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ANCHOR, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        raw, kind = winreg.QueryValueEx(key, "Anchor")
    if kind != winreg.REG_SZ or type(raw) is not str:
        deny()
    return decode(raw.encode("utf-8"), 32768)


class WindowsServiceAuthority(auth.WindowsGrantVerifier):
    """A source binding, never a substitute for installing/enrolling the service."""
    def __init__(self, pipe_handle, **kwargs):
        self.pipe_handle = pipe_handle
        self._broker_digest = None
        self.service_sid = service_sid()
        super().__init__(**kwargs)
        self.backend = _ServiceReadBackend(self)

    def _read_anchor(self):
        broker = _read_broker_anchor()
        server, client = validate_peer(broker, observe_connection(self.pipe_handle))
        digest = auth._digest(canonical(broker))
        if self._broker_digest is not None and digest != self._broker_digest:
            deny()
        self._broker_digest = digest
        for pin in (broker["service"], broker["desktop_peer"]):
            path = WindowsPathCustody().check(pin["process"]["executable"])
            if auth._digest(path.read_bytes()) != pin["sha256"]:
                deny()
        anchor = broker["authority"]
        shape(anchor, "schema issuer_signers human_signers observer_signers verifier enrollment operator_sid runner_sids journal_path journal_id lane")
        if (anchor["schema"] != "WakeHubG2Anchor.v1" or type(anchor["runner_sids"]) is not list
                or not anchor["runner_sids"] or any(type(s) is not str or not re.fullmatch(r"S-1-(?:[0-9]+-)+[0-9]+", s) for s in anchor["runner_sids"])
                or set(anchor["runner_sids"]) & OS_TRUSTED_SIDS
                or service_sid() in (anchor["operator_sid"], *anchor["runner_sids"])
                or anchor["operator_sid"] != client.user_sid):
            deny()
        # Sharing runner/desktop SID is permitted ONLY after all broker checks.
        auth._validate_anchor_material(anchor)
        custody = ServiceCustody()
        paths = [anchor["journal_path"], broker["requests_path"], broker["private_key_path"], broker["desktop_path"]]
        if len(set(paths)) != len(paths):
            deny()
        for path in paths:
            custody.check(path)
        self.broker, self.server, self.client = broker, server, client
        return anchor

    def _check_principals(self, anchor, value, target):
        if (target.user_sid != self.client.user_sid or target.session_id != self.client.session_id
                or target.user_sid != anchor["operator_sid"] or value["observer_sid"] != self.service_sid
                or value["source"] != asdict(self._loading_source)):
            deny()
        ServiceCustody().check(value["observer_root"], directory=True)

    def journal_owner(self, target):
        return self.service_sid

    def _check_desktop(self, target):
        # Session 0 never uses/impersonates an interactive input desktop. A
        # separate observer must authenticate the pinned live desktop process.
        body = _signed(ServiceCustody().check(self.broker["desktop_path"]).read_bytes(), "observer", "desktop", self.broker["authority"])
        shape(body, "schema task_parent journal_id source target server client state issued_at expires_at")
        auth._fresh(body, self.clock())
        if (body["schema"] != "WakeHubBrokerDesktop.v1" or body["task_parent"] != auth.TASK_PARENT
                or body["journal_id"] != self.broker["authority"]["journal_id"]
                or body["source"] != asdict(self._loading_source)
                or body["target"] != asdict(target) or body["server"] != asdict(self.server)
                or body["client"] != asdict(self.client) or body["state"] != "LIVE_UNLOCKED_OWNER"):
            deny()

    def _load(self, now):
        # The signed enrollment itself supplies the source used by the desktop
        # assertion. Super reloads/signature-checks it and enforces monotonic DB
        # checkpoints; this early read cannot grant authority on its own.
        anchor = self._read_anchor()
        value = auth._signed(WindowsPathCustody().check(anchor["enrollment"]).read_bytes(), "issuer", "enrollment", anchor)
        self._loading_source = op.SourceBinding(**value["source"])
        return super()._load(now)

    def verify_request(self, raw):
        now = self.clock()
        self._load(now)
        body = _signed(raw, "issuer", "request", self.anchor)
        shape(body, "schema task_parent journal_id source server client operation proof nonce issued_at expires_at")
        auth._fresh(body, now)
        if (body["schema"] != "WakeHubBrokerRequest.v1" or body["task_parent"] != auth.TASK_PARENT
                or body["journal_id"] != self.enrolled["journal_id"] or body["source"] != asdict(self.source)
                or body["server"] != asdict(self.server) or body["client"] != asdict(self.client)
                or type(body["nonce"]) is not str or str(UUID(body["nonce"])) != body["nonce"]):
            deny()
        operation = op.Operation(body["operation"])
        shape(body["proof"], "signature consent")
        proof = op.PrivateGrantProof(*(base64.b64decode(body["proof"][k], validate=True) for k in ("signature", "consent")))
        # Verify fresh grant, Human consent/pause and revocations before claiming
        # a request. START/REUSE are still unconditionally blocked by runtime.
        if self.verify_grant(proof, operation, op.LifecyclePolicy(self.source), now) is None:
            deny()
        return operation, proof, body["nonce"], auth._digest(canonical(body))


class _ServiceReadBackend:
    def __init__(self, authority):
        self.authority = authority

    def read_fixed_inventory(self):
        # Fixed READ only. Service user/session metadata is NEVER presented as
        # desktop ownership. The independently signed inventory must agree on
        # the entire machine snapshot; desktop metadata comes from the already
        # authenticated peer/observer, never service impersonation.
        authority = self.authority
        snapshot = inv.WindowsReadBackend(authority).read_fixed_inventory()
        authority._check_desktop(authority.target)
        from dataclasses import replace
        return replace(snapshot, user_name=authority.target.user_name,
            user_sid=authority.target.user_sid, session_id=authority.target.session_id)


class ServiceEffectJournal(SQLitePrivateJournal):
    def __init__(self, path, *, owner_sid, journal_id, lane):
        if owner_sid != service_sid():
            deny()
        super().__init__(path, owner_sid=owner_sid, journal_id=journal_id, lane=lane)
        self.custody = ServiceCustody()
        with self._transaction():
            pass


_REQUEST_SCHEMA = """
CREATE TABLE identity(journal_id TEXT PRIMARY KEY);
CREATE TABLE requests(nonce TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('UNKNOWN','DONE')), receipt BLOB);
"""


class BrokerRequests:
    """Preprovisioned, service-only request ledger; never create/reset/migrate.

    UNKNOWN is permanent request poison, including after lease expiry/reopen.
    Only the separate effect journal accepts signed original-outcome resolution.
    A finished request is still consumed forever; its result cannot be replaced.
    """
    def __init__(self, path, *, owner_sid, journal_id):
        if owner_sid != service_sid():
            deny()
        self.path, self.journal_id = Path(path), journal_id
        self._identity = None
        with self._transaction():
            pass

    def _guard(self):
        custody = ServiceCustody()
        custody.check(self.path)
        for suffix in ("-wal", "-shm", "-journal"):
            side = Path(str(self.path) + suffix)
            if side.exists():
                custody.check(side)
        stat = self.path.stat()
        identity = stat.st_dev, stat.st_ino
        if self._identity is not None and self._identity != identity:
            deny()
        self._identity = identity

    def _validate(self, db):
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            deny()
        normalize = lambda s: " ".join(s.split()).casefold()
        objects = db.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','trigger','view')").fetchall()
        if {normalize(s) for s, in objects} != {normalize(s) for s in _REQUEST_SCHEMA.split(";") if s.strip()}:
            deny()
        if db.execute("SELECT * FROM identity").fetchall() != [(self.journal_id,)]:
            deny()
        for nonce, digest, state, receipt in db.execute("SELECT * FROM requests"):
            if str(UUID(nonce)) != nonce or not re.fullmatch(r"[0-9a-f]{64}", digest):
                deny()
            if state == "UNKNOWN":
                if receipt is not None:
                    deny()
            elif state != "DONE" or receipt is None or canonical(decode(receipt)) != receipt:
                deny()
            else:
                value = decode(receipt)
                # Stored public output remains a finite, validated enum object.
                expected = op.PublicReceipt(op.Operation(value["operation"]), op.Decision(value["decision"]),
                    op.Reason(value["reason"]), op.EffectAttempt(value["effect"]))
                if canonical(decode(expected.to_json().encode())) != receipt:
                    deny()

    @contextmanager
    def _transaction(self):
        db = None
        try:
            self._guard()
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0.1, isolation_level=None)
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                deny()
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA synchronous").fetchone()[0] != 2:
                deny()
            db.execute("BEGIN IMMEDIATE")
            self._guard()
            self._validate(db)
            yield db
            self._validate(db)
            self._guard()
            db.commit()
        except Exception:
            if db:
                try:
                    db.rollback()
                except Exception:
                    pass
            deny()
        finally:
            if db:
                db.close()

    def claim(self, nonce, digest):
        if (type(nonce) is not str or str(UUID(nonce)) != nonce or type(digest) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            deny()
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM requests WHERE nonce=? OR digest=?", (nonce, digest)).fetchone():
                return False
            db.execute("INSERT INTO requests VALUES(?,?,'UNKNOWN',NULL)", (nonce, digest))
        return True

    def finish(self, nonce, digest, receipt):
        if type(receipt) is not op.PublicReceipt or receipt.effect is not op.EffectAttempt.NOT_ATTEMPTED:
            deny()
        with self._transaction() as db:
            if db.execute("UPDATE requests SET state='DONE',receipt=? WHERE nonce=? AND digest=? AND state='UNKNOWN'",
                    (canonical(decode(receipt.to_json().encode())), nonce, digest)).rowcount != 1:
                deny()
