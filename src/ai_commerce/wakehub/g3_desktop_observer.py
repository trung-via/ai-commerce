"""Interactive STATUS observation and exact signing transcripts, no GUI action.

Private signing is an external, independently enrolled role. This source never
generates keys, enrolls a signer, or treats an unsigned observation as evidence.
The service verifies the signed response against its OWN pending challenge.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from dataclasses import asdict, dataclass
from enum import Enum
import math
import os
from pathlib import Path
import re
import threading
import time
from uuid import UUID

from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub import native_inventory as inv
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.g3_enrollment import TASK_PARENT, STATUS_FRAME_BODY_LIMIT
from ai_commerce.wakehub.native_journal import canonical, decode, deny, shape, WindowsPathCustody


CHALLENGE_NAMESPACE = "wakehub-g3-9444-broker-challenge@ai-commerce"
STATUS_NAMESPACE = "wakehub-g3-9444-desktop-status@ai-commerce"
BROKER_IDENTITY = "wakehub-g3-broker"


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class Challenge:
    nonce: str
    source: op.SourceBinding
    generation: int
    journal_id: str
    server: inv.ProcessIdentity
    client: inv.ProcessIdentity
    target: op.PrivateTarget
    logon_id: str
    issued_at: float
    expires_at: float
    request_sha256: str | None = None

    def __post_init__(self):
        if (type(self.nonce) is not str or str(UUID(self.nonce)) != self.nonce
                or type(self.source) is not op.SourceBinding
                or type(self.generation) is not int or not 1 <= self.generation < 2**53
                or type(self.journal_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", self.journal_id)
                or type(self.server) is not inv.ProcessIdentity or type(self.client) is not inv.ProcessIdentity
                or type(self.target) is not op.PrivateTarget
                or self.server.user_sid != broker.service_sid() or self.server.session_id != 0
                or self.client.pid == self.server.pid or self.client.user_sid == broker.service_sid()
                or self.client.session_id <= 0 or self.client.user_sid != self.target.user_sid
                or self.client.session_id != self.target.session_id
                or type(self.logon_id) is not str or not re.fullmatch(r"[0-9a-f]{16}", self.logon_id)
                or self.logon_id == "0" * 16
                or (self.request_sha256 is not None and (type(self.request_sha256) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", self.request_sha256)))
                or not all(_number(v) for v in (self.issued_at, self.expires_at))
                or not 0 < self.issued_at < self.expires_at < 2**53
                or self.expires_at - self.issued_at > 5):
            deny()

    def body(self):
        fields = asdict(self)
        if self.request_sha256 is None:
            del fields["request_sha256"]  # Preserve the existing G3 transcript.
        return dict(schema="WakeHubG3DesktopChallenge.v1", task_parent=TASK_PARENT,
                    operation="STATUS", **fields)

    def fresh(self, now):
        if not _number(now) or not self.issued_at <= now < self.expires_at:
            deny()


def challenge_from(body) -> Challenge:
    extra = " request_sha256" if type(body) is dict and "request_sha256" in body else ""
    shape(body, "schema task_parent operation nonce source generation journal_id server client target logon_id issued_at expires_at" + extra)
    if (body["schema"] != "WakeHubG3DesktopChallenge.v1" or body["task_parent"] != TASK_PARENT
            or body["operation"] != "STATUS"):
        deny()
    return Challenge(body["nonce"], op.SourceBinding(**body["source"]), body["generation"], body["journal_id"],
                     inv.ProcessIdentity(**body["server"]), inv.ProcessIdentity(**body["client"]),
                     op.PrivateTarget(**body["target"]), body["logon_id"], body["issued_at"], body["expires_at"],
                     body.get("request_sha256"))


class StatusRole(Enum):
    BROKER = BROKER_IDENTITY
    OBSERVER = auth.IDENTITIES["observer"]


@dataclass(frozen=True, slots=True, repr=False)
class StatusFrame:
    """Bounded typed wire data, NEVER authentication or an effect capability.

    Only complete single frames are accepted. Native I/O below requires a
    message-mode overlapped pipe; signing remains a separate enrolled owner.
    The consumer independently verifies the existing role signature; outer
    framing can never change its role, nonce, source or request binding.
    """
    role: StatusRole
    signed_payload: bytes

    def _body(self):
        if type(self.role) is not StatusRole:
            deny()
        value = decode(self.signed_payload, STATUS_FRAME_BODY_LIMIT)
        shape(value, "body signature")
        if (type(value["body"]) is not dict or type(value["signature"]) is not str
                or not value["signature"] or canonical(value) != self.signed_payload):
            deny()
        return value

    def check_binding(self, expected: Challenge):
        if type(expected) is not Challenge or expected.request_sha256 is None:
            deny()
        body = self._body()["body"]
        if self.role is StatusRole.BROKER:
            # Canonical comparison also rejects int/float and bool aliases.
            challenge_from(body)
            if canonical(body) != canonical(expected.body()):
                deny()
        else:
            shape(body, "schema task_parent challenge state observed_at process logon_id")
            if (body["schema"] != "WakeHubG3DesktopStatus.v1" or body["task_parent"] != TASK_PARENT
                    or canonical(body["challenge"]) != canonical(expected.body())):
                deny()

    def to_bytes(self) -> bytes:
        payload = self._body()
        raw = canonical(dict(schema="WakeHubG3StatusFrame.v1", operation="STATUS",
                             role=self.role.value, payload=payload))
        if len(raw) > STATUS_FRAME_BODY_LIMIT:
            deny()
        return len(raw).to_bytes(4, "big") + raw

    @classmethod
    def _parse(cls, raw: bytes, role: StatusRole) -> StatusFrame:
        if (type(raw) is not bytes or not 4 < len(raw) <= STATUS_FRAME_BODY_LIMIT + 4
                or int.from_bytes(raw[:4], "big") != len(raw) - 4 or type(role) is not StatusRole):
            deny()
        value = decode(raw[4:], STATUS_FRAME_BODY_LIMIT)
        shape(value, "schema operation role payload")
        if (canonical(value) != raw[4:] or value["schema"] != "WakeHubG3StatusFrame.v1"
                or value["operation"] != "STATUS" or value["role"] != role.value):
            deny()
        frame = cls(role, canonical(value["payload"]))
        frame._body()
        return frame

    @classmethod
    def from_bytes(cls, raw: bytes, *, expected: Challenge, role: StatusRole) -> StatusFrame:
        frame = cls._parse(raw, role)
        frame.check_binding(expected)
        return frame


class _StatusDeadline:
    """One local monotonic five-second budget, including external signing."""
    def __init__(self, monotonic=time.monotonic):
        self._clock = monotonic
        self._last = monotonic()
        if not _number(self._last):
            deny()
        self._end = self._last + 5

    def check(self):
        now = self._clock()
        if not _number(now) or not self._last <= now < self._end:
            deny()
        self._last = now
        return self._end - now

    def constrain(self, challenge, now):
        challenge.fresh(now)
        self.check()
        self._end = min(self._end, self._last + challenge.expires_at - now)
        self.check()

    def milliseconds(self):
        # Round down; there is never an extra millisecond or infinite wait.
        return max(0, min(5000, int(self.check() * 1000)))


class _PipeSide(Enum):
    SERVER = "SERVER"
    CLIENT = "CLIENT"


_PIPE_LOCK = threading.Lock()
_SPENT_HANDLES = set()
_UNFINISHED_IO = []


class _Overlapped(ctypes.Structure):
    _fields_ = [("internal", ctypes.c_size_t), ("internal_high", ctypes.c_size_t),
                ("offset", wt.DWORD), ("offset_high", wt.DWORD), ("event", wt.HANDLE)]


class _StatusPipeIO:
    """Owned connected HANDLE, ONE message per direction; no open or reconnect.

    Message mode supplies the exact frame boundary, including partial/trailing
    data rejection. Synchronous handles are denied before ReadFile/WriteFile.
    CancelIoEx never proves completion. If cancellation is still outstanding,
    retain the HANDLE/event/OVERLAPPED/buffer and refuse further adapters in this
    process. Never free kernel-owned memory, wait indefinitely, or retry I/O.
    """
    def __init__(self, handle: int, side: _PipeSide, deadline: _StatusDeadline):
        auth.windows_only()
        if (type(handle) is not int or handle <= 0 or type(side) is not _PipeSide
                or type(deadline) is not _StatusDeadline):
            deny()
        with _PIPE_LOCK:
            if handle in _SPENT_HANDLES or _UNFINISHED_IO:
                deny()
            _SPENT_HANDLES.add(handle)  # Never readopt a consumed HANDLE value.
        self.handle, self.side, self.deadline = handle, side, deadline
        self.closed, self._pending = False, None
        self._read_done = self._write_done = False
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            deadline.check()
            flags, state = wt.DWORD(), wt.DWORD()
            if (not self._api("GetNamedPipeInfo", [wt.HANDLE, ctypes.POINTER(wt.DWORD),
                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], wt.BOOL)(
                    handle, ctypes.byref(flags), None, None, None)
                    or bool(flags.value & 1) != (side is _PipeSide.SERVER) or not flags.value & 4):
                deny()
            if (not self._api("GetNamedPipeHandleStateW", [wt.HANDLE, ctypes.POINTER(wt.DWORD),
                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wt.LPWSTR, wt.DWORD], wt.BOOL)(
                    handle, ctypes.byref(state), None, None, None, None, 0)
                    or not state.value & 2 or state.value & 1):
                deny()  # PIPE_READMODE_MESSAGE, PIPE_WAIT; never PIPE_NOWAIT.
            class IoStatus(ctypes.Structure):
                _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]
            iosb, mode = IoStatus(), wt.ULONG()
            status = broker._api(ctypes.WinDLL("ntdll"), "NtQueryInformationFile",
                [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wt.ULONG, wt.ULONG], wt.LONG)(
                handle, ctypes.byref(iosb), ctypes.byref(mode), ctypes.sizeof(mode), 16)
            if status == 259:  # STATUS_PENDING: keep query output alive too.
                self._retain((iosb, mode))
                deny()
            if status != 0 or mode.value & 0x30:
                deny()  # FILE_SYNCHRONOUS_IO_ALERT / NONALERT cannot be bounded.
            deadline.check()
        except Exception:
            self.close()
            deny()

    def _api(self, name, args, result):
        return broker._api(self._kernel, name, args, result)

    def _retain(self, packet):
        self._pending = packet
        with _PIPE_LOCK:
            _UNFINISHED_IO.append(self)

    def _transfer(self, raw=None):
        self.deadline.check()
        if self.closed or self._pending is not None:
            deny()
        size = STATUS_FRAME_BODY_LIMIT + 5 if raw is None else len(raw)
        buffer = ctypes.create_string_buffer(size) if raw is None else ctypes.create_string_buffer(raw, size)
        event = self._api("CreateEventW", [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR], wt.HANDLE)(
            None, True, False, None)
        if not event:
            deny()
        # Low event bit suppresses an IOCP packet owned by an external host.
        overlapped = _Overlapped(event=event | 1)
        count, pending = wt.DWORD(), False
        result = self._api("GetOverlappedResultEx", [wt.HANDLE, ctypes.POINTER(_Overlapped),
            ctypes.POINTER(wt.DWORD), wt.DWORD, wt.BOOL], wt.BOOL)
        try:
            self.deadline.check()
            pending = True
            ok = self._api("ReadFile" if raw is None else "WriteFile", [wt.HANDLE, ctypes.c_void_p,
                wt.DWORD, ctypes.c_void_p, ctypes.POINTER(_Overlapped)], wt.BOOL)(
                self.handle, buffer, size, None, ctypes.byref(overlapped))
            if not ok and ctypes.get_last_error() != 997:
                pending = False
                deny()  # EOF, broken pipe, MORE_DATA or any other finite failure.
            ok = result(self.handle, ctypes.byref(overlapped), ctypes.byref(count),
                        self.deadline.milliseconds() if not ok else 0, False)
            if not ok:
                pending = ctypes.get_last_error() not in (38, 109, 232, 234, 995)
                deny()
            pending = False
            self.deadline.check()  # Late success is UNKNOWN, never an ACK.
            if not 0 < count.value <= size or (raw is not None and count.value != size):
                deny()
            return bytes(buffer.raw[:count.value]) if raw is None else None
        finally:
            if pending:
                self._api("CancelIoEx", [wt.HANDLE, ctypes.POINTER(_Overlapped)], wt.BOOL)(
                    self.handle, ctypes.byref(overlapped))
                done = result(self.handle, ctypes.byref(overlapped), ctypes.byref(count), 0, False)
                if not done and ctypes.get_last_error() not in (38, 109, 232, 234, 995):
                    self._retain((buffer, overlapped, event, count))
            if self._pending is None:
                self._api("CloseHandle", [wt.HANDLE], wt.BOOL)(event)

    def read_frame(self):
        if self._read_done:
            deny()
        self._read_done = True
        raw = self._transfer()
        StatusFrame._parse(raw, StatusRole.OBSERVER if self.side is _PipeSide.SERVER else StatusRole.BROKER)
        # Reject already queued second messages without starting another read.
        available = wt.DWORD()
        if (not self._api("PeekNamedPipe", [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                ctypes.POINTER(wt.DWORD), ctypes.c_void_p], wt.BOOL)(
                self.handle, None, 0, None, ctypes.byref(available), None) or available.value):
            deny()
        self.deadline.check()
        return raw

    def write_frame(self, raw: bytes):
        if self._write_done:
            deny()
        self._write_done = True
        StatusFrame._parse(raw, StatusRole.BROKER if self.side is _PipeSide.SERVER else StatusRole.OBSERVER)
        self._transfer(raw)

    def close(self):
        if not self.closed:
            self.closed = True
            if self._pending is None:
                self._api("CloseHandle", [wt.HANDLE], wt.BOOL)(self.handle)


def _verify(raw, signers, verifier, identity, namespace):
    value = decode(raw)
    shape(value, "body signature")
    if type(value["body"]) is not dict or type(value["signature"]) is not str:
        deny()
    auth.verify_signature(auth.signing_payload(value["body"]), value["signature"],
                          Path(signers), identity, namespace, Path(verifier))
    return value["body"]


def read_logon_id(pid: int) -> str:
    """Read-only primary-token AuthenticationId diagnostic; no authority.

    Like broker.read_process_identity this can run in Windows CI without
    enrollment. Collectors/hosts retain their separate windows_only/custody
    gates BEFORE using the result or opening any input desktop.
    """
    if os.name != "nt":
        deny()
    if type(pid) is not int or not 0 < pid < 2**32:
        deny()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    close = broker._api(kernel, "CloseHandle", [wt.HANDLE], wt.BOOL)
    process = broker._api(kernel, "OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE)(0x1000, False, pid)
    if not process:
        deny()
    token = wt.HANDLE()
    class LUID(ctypes.Structure):
        _fields_ = [("low", wt.DWORD), ("high", wt.LONG)]
    class Statistics(ctypes.Structure):
        _fields_ = [("token", LUID), ("authentication", LUID), ("expiration", ctypes.c_longlong),
                    ("kind", wt.DWORD), ("impersonation", wt.DWORD), ("charged", wt.DWORD),
                    ("available", wt.DWORD), ("groups", wt.DWORD), ("privileges", wt.DWORD), ("modified", LUID)]
    try:
        if not broker._api(adv, "OpenProcessToken", [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)], wt.BOOL)(process, 8, ctypes.byref(token)):
            deny()
        data, needed = Statistics(), wt.DWORD()
        if (not broker._api(adv, "GetTokenInformation", [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL)(token, 10, ctypes.byref(data), ctypes.sizeof(data), ctypes.byref(needed))
                or needed.value != ctypes.sizeof(data) or data.kind != 1):
            deny()
        return f"{data.authentication.high & 0xffffffff:08x}{data.authentication.low:08x}"
    finally:
        if token:
            close(token)
        close(process)


def observation_body(challenge: Challenge, *, now: float, process: inv.ProcessIdentity,
                     logon_id: str, source: op.SourceBinding, generation: int) -> dict:
    """Pure OFFLINE policy seam; this function alone cannot attest a desktop."""
    challenge.fresh(now)
    if (type(process) is not inv.ProcessIdentity or process != challenge.client
            or process.session_id <= 0 or logon_id != challenge.logon_id
            or source != challenge.source or type(generation) is not int or generation != challenge.generation):
        deny()
    return dict(schema="WakeHubG3DesktopStatus.v1", task_parent=TASK_PARENT,
                challenge=challenge.body(), state="LIVE_UNLOCKED_OWNER", observed_at=now,
                process=asdict(process), logon_id=logon_id)


def collect_status_transcript(signed_challenge: bytes, *, expected: Challenge,
                              broker_signers: Path, verifier: Path, source: op.SourceBinding,
                              generation: int, clock=time.time) -> bytes:
    """Interactive-only collector; returns bytes for the external observer signer.

    `expected`, source and generation come from protected enrollment, never a
    pipe request's self-declaration. No unsigned output is accepted by the host.
    No service/Session 0 path calls this collector.
    """
    auth.windows_only()
    # Check own identity BEFORE opening an input desktop. Session 0 cannot
    # reach even the read-only desktop check.
    process = broker.read_process_identity(os.getpid())
    if process != expected.client or process.session_id <= 0:
        deny()
    custody = WindowsPathCustody()
    body = _verify(signed_challenge, custody.check(broker_signers), custody.check(verifier),
                   BROKER_IDENTITY, CHALLENGE_NAMESPACE)
    if challenge_from(body) != expected:
        deny()
    before = read_logon_id(process.pid)
    auth._desktop_identity(expected.target)
    observed_at = clock()
    # A process/token change while reading is unknown, never an unlocked claim.
    if broker.read_process_identity(os.getpid()) != process or read_logon_id(process.pid) != before:
        deny()
    return auth.signing_bytes(observation_body(expected, now=observed_at, process=process,
        logon_id=before, source=source, generation=generation))


def collect_status_frame_transcript(raw: bytes, *, expected: Challenge,
                                    broker_signers: Path, verifier: Path, source: op.SourceBinding,
                                    generation: int, clock=time.time) -> bytes:
    """Bounded exchange consumer for the separately enrolled desktop observer.

    Expected pins MUST come from protected enrollment and an independently
    authenticated client HANDLE, never from decoding the incoming frame. This
    source seam neither opens that HANDLE nor invents that missing native owner.
    Output goes only to the existing external observer signer, never the wire
    as unsigned STATUS. Even a valid transcript confers no START/REUSE authority.
    """
    frame = StatusFrame.from_bytes(raw, expected=expected, role=StatusRole.BROKER)
    return collect_status_transcript(frame.signed_payload, expected=expected,
        broker_signers=broker_signers, verifier=verifier, source=source,
        generation=generation, clock=clock)


def _observe_client_connection(handle):
    """Independent client-end kernel observation; no wire PID or SID facts."""
    auth.windows_only()
    if type(handle) is not int or handle <= 0:
        deny()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    thread = broker._api(kernel, "GetCurrentThread", [], wt.HANDLE)()
    token = wt.HANDLE()
    if broker._api(adv, "OpenThreadToken", [wt.HANDLE, wt.DWORD, wt.BOOL,
            ctypes.POINTER(wt.HANDLE)], wt.BOOL)(thread, 8, True, ctypes.byref(token)):
        broker._api(kernel, "CloseHandle", [wt.HANDLE], wt.BOOL)(token)
        deny()
    if ctypes.get_last_error() != 1008:
        deny()
    flags = wt.DWORD()
    if (not broker._api(kernel, "GetNamedPipeInfo", [wt.HANDLE, ctypes.POINTER(wt.DWORD),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], wt.BOOL)(
            handle, ctypes.byref(flags), None, None, None) or flags.value & 1):
        deny()
    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wt.USHORT), ("maximum", wt.USHORT), ("buffer", ctypes.c_void_p)]
    buffer, needed = ctypes.create_string_buffer(4096), wt.ULONG()
    if broker._api(ctypes.WinDLL("ntdll"), "NtQueryObject", [wt.HANDLE, wt.ULONG, ctypes.c_void_p,
            wt.ULONG, ctypes.POINTER(wt.ULONG)], wt.LONG)(handle, 1, buffer, len(buffer), ctypes.byref(needed)) != 0:
        deny()
    name = ctypes.cast(buffer, ctypes.POINTER(UnicodeString)).contents
    if not name.buffer or ctypes.wstring_at(name.buffer, name.length // 2) != broker.PIPE:
        deny()
    facts = {}
    for side in ("Client", "Server"):
        for facet in ("ProcessId", "SessionId"):
            value = wt.ULONG()
            if not broker._api(kernel, "GetNamedPipe" + side + facet, [wt.HANDLE,
                    ctypes.POINTER(wt.ULONG)], wt.BOOL)(handle, ctypes.byref(value)):
                deny()
            facts[side + facet] = value.value
    if facts["ClientProcessId"] != os.getpid():
        deny()
    broker._check_pipe_acl(handle)
    server = broker.read_process_identity(facts["ServerProcessId"])
    client = broker.read_process_identity(facts["ClientProcessId"])
    if (server.session_id != facts["ServerSessionId"] or client.session_id != facts["ClientSessionId"]
            or client.session_id <= 0):
        deny()
    return dict(server=asdict(server), client=asdict(client), scm=broker._scm_process())


def _desktop_context(handle):
    # Reuse the protected G2/G3 pins, without reading the service-private ledgers.
    from ai_commerce.wakehub.g3_service_host import _peer_context
    return _peer_context(_observe_client_connection(handle))


class DesktopStatusOutcome(Enum):
    RESPONSE_WRITTEN = "RESPONSE_WRITTEN"  # Never an assertion of a service ACK.
    UNKNOWN = "UNKNOWN_RECONCILE_ONLY"
    DUPLICATE = "HOLD_DUPLICATE"
    HOLD_DEPENDENCY = "HOLD_DEPENDENCY"


class ConnectedDesktopStatusIO:
    """Separately enrolled interactive peer; consumes an existing client HANDLE.

    receive authenticates native endpoints/protected pins before decoding, then
    verifies the broker signature before adopting its challenge. Only a private
    transcript leaves for the external observer signer. respond independently
    verifies that role's signature and the exact transcript before one write.
    No caller selects pins, process facts, signer, path or callback. A successful
    write retains the HANDLE for the enrolled owner to close via abandon after
    the service outcome; closing immediately would race server authentication.
    """
    def __init__(self, *, clock=time.time, monotonic=time.monotonic):
        self._clock, self._monotonic = clock, monotonic
        self._pipe = None
        self._lock = threading.Lock()
        self._state = "NEW"

    def _recheck(self):
        self._deadline.check()
        context = _desktop_context(self._pipe.handle)
        if auth._digest(canonical(context[:2])) != self._pins_digest:
            deny()
        _, config, server, client, source, target = context
        if ((server, client, source, target, config["generation"], config["journal_id"], config["logon_id"])
                != (self._challenge.server, self._challenge.client, self._challenge.source,
                    self._challenge.target, self._challenge.generation, self._challenge.journal_id,
                    self._challenge.logon_id) or read_logon_id(client.pid) != self._challenge.logon_id):
            deny()
        now = self._clock()
        self._challenge.fresh(now)
        if now < self._last_time:
            deny()
        self._last_time = now
        self._deadline.check()
        return context

    def receive(self, handle: int) -> bytes:
        with self._lock:
            if self._state != "NEW":
                deny()
            self._state = "CLOSED"
            try:
                self._deadline = _StatusDeadline(self._monotonic)
                context = _desktop_context(handle)
                anchor, config, server, client, source, target = context
                self._pins_digest = auth._digest(canonical(context[:2]))
                self._pipe = _StatusPipeIO(handle, _PipeSide.CLIENT, self._deadline)
                frame = StatusFrame._parse(self._pipe.read_frame(), StatusRole.BROKER)
                body = _verify(frame.signed_payload, config["broker_signers"]["path"],
                    anchor["authority"]["verifier"]["path"], BROKER_IDENTITY, CHALLENGE_NAMESPACE)
                self._challenge = challenge_from(body)
                frame.check_binding(self._challenge)
                self._last_time = self._clock()
                self._deadline.constrain(self._challenge, self._last_time)
                self._recheck()
                self._transcript = collect_status_transcript(frame.signed_payload, expected=self._challenge,
                    broker_signers=Path(config["broker_signers"]["path"]),
                    verifier=Path(anchor["authority"]["verifier"]["path"]), source=source,
                    generation=config["generation"], clock=self._clock)
                self._recheck()
                self._state = "OBSERVED"
                return self._transcript
            except Exception:
                if self._pipe is not None:
                    self._pipe.close()
                deny()

    def respond(self, signed_status: bytes) -> DesktopStatusOutcome:
        if not self._lock.acquire(blocking=False):
            return DesktopStatusOutcome.DUPLICATE
        try:
            if self._state != "OBSERVED":
                return DesktopStatusOutcome.DUPLICATE
            self._state = "CLOSED"
            try:
                anchor, *_ = self._recheck()
                frame = StatusFrame(StatusRole.OBSERVER, signed_status)
                frame.check_binding(self._challenge)
                body = verify_status(signed_status, expected=self._challenge,
                    anchor=anchor["authority"], now=self._clock())
                if auth.signing_bytes(body) != self._transcript:
                    deny()
                auth._desktop_identity(self._challenge.target)
                self._recheck()
                self._pipe.write_frame(frame.to_bytes())
                self._recheck()
                self._state = "WRITTEN"
                return DesktopStatusOutcome.RESPONSE_WRITTEN
            except Exception:
                return DesktopStatusOutcome.UNKNOWN
            finally:
                if self._state != "WRITTEN":
                    self._pipe.close()
        finally:
            self._lock.release()

    def abandon(self) -> DesktopStatusOutcome:
        with self._lock:
            pending = self._state in ("OBSERVED", "WRITTEN")
            self._state = "CLOSED"
            if self._pipe is not None:
                self._pipe.close()
            return DesktopStatusOutcome.UNKNOWN if pending else DesktopStatusOutcome.DUPLICATE


def verify_status(raw: bytes, *, expected: Challenge, anchor: dict, now: float) -> dict:
    """Exact signed observer role, nonce, source, generation and live logon."""
    expected.fresh(now)
    value = _verify(raw, anchor["observer_signers"]["path"], anchor["verifier"]["path"],
                    auth.IDENTITIES["observer"], STATUS_NAMESPACE)
    shape(value, "schema task_parent challenge state observed_at process logon_id")
    if (value["schema"] != "WakeHubG3DesktopStatus.v1" or value["task_parent"] != TASK_PARENT
            or canonical(value["challenge"]) != canonical(expected.body())
            or value["state"] != "LIVE_UNLOCKED_OWNER" or not _number(value["observed_at"])
            or not expected.issued_at <= value["observed_at"] <= now < expected.expires_at
            or canonical(value["process"]) != canonical(asdict(expected.client))
            or value["logon_id"] != expected.logon_id):
        deny()
    return value
