"""Fixed own-process SCM host and bounded local pipe transport SOURCE.

No installation or key/ACL/registry/task writes. Public entrypoint has no
injection or command options and cannot pass the packaging/custody hard gate.
Private adapter seams exercise framing/state offline; they confer no trust.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes as wt
import json
import os
import re
import struct
import sys
import threading
import time

from ai_commerce.wakehub import g3_service_host as host
from ai_commerce.wakehub import g3_desktop_observer as observer
from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub import g4_release_provenance as release
from ai_commerce.wakehub.native_journal import canonical, decode, deny, shape, WindowsPathCustody, OS_TRUSTED_SIDS


PIPE_PATH = r"\\.\pipe\AICommerce-WakeHub-9444-PrivateBroker-v1"
MAX_FRAME = 262144
EXCHANGE_SECONDS = 4.0  # Entire exchange, not a fresh timeout per fragment.
POLL_MS = 25
CANCEL_MS = 250
_QUARANTINED_IO = []  # Keep buffers/OVERLAPPED alive until own-process exit.


class _CancellationUnproven(RuntimeError):
    pass


class _Overlapped(ctypes.Structure):
    _fields_ = [("internal", ctypes.c_size_t), ("internal_high", ctypes.c_size_t),
                ("offset", wt.DWORD), ("offset_high", wt.DWORD), ("event", wt.HANDLE)]


def _unb64(value):
    if type(value) is not str or not 0 < len(value) <= MAX_FRAME:
        deny()
    raw = base64.b64decode(value, validate=True)
    if not raw or len(raw) > MAX_FRAME:
        deny()
    return raw


def _b64(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_FRAME:
        deny()
    return base64.b64encode(raw).decode("ascii")


def _pipe_descriptor(operator_sid):
    if (type(operator_sid) is not str or not re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", operator_sid)
            or operator_sid in {*OS_TRUSTED_SIDS, broker.service_sid()}):
        deny()
    # FILE_GENERIC_WRITE includes FILE_CREATE_PIPE_INSTANCE: NEVER grant it.
    return ("O:" + broker.service_sid() + "D:P(A;;GA;;;SY)(A;;GA;;;BA)"
            + "(A;;GA;;;" + broker.service_sid() + ")(A;;0x0012008b;;;" + operator_sid + ")")


def _strict_pipe_acl(handle, operator_sid):
    owner, aces = WindowsPathCustody._descriptor(handle, 1, handle=True)
    # Object-manager readback may map GA to FILE_ALL_ACCESS.
    normalized = [(kind, flags, 0x1f01ff if mask == 0x10000000 else mask, sid)
                  for kind, flags, mask, sid in aces] if aces is not None else None
    expected = {(0, 0, 0x1f01ff, sid) for sid in {*OS_TRUSTED_SIDS, broker.service_sid()}}
    expected.add((0, 0, 0x12008b, operator_sid))
    if (owner not in {*OS_TRUSTED_SIDS, broker.service_sid()} or aces is None
            or len(aces) != len(expected) or set(normalized) != expected):
        deny()  # Reject inherited, world, extra user, NULL or permissive ACLs.
    broker._protected_dacl(handle, 1, handle=True)


class _NativePipe:
    """Real overlapped Win32 HANDLE adapter, no ambient names or subprocesses."""
    def __init__(self, handle, *, server, stop):
        self.handle, self.server, self.stop = handle, server, stop
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.poisoned = False

    @classmethod
    def create_server(cls, operator_sid, stop):
        auth.windows_only()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        ptr = ctypes.c_void_p
        class Security(ctypes.Structure):
            _fields_ = [("length", wt.DWORD), ("descriptor", ptr), ("inherit", wt.BOOL)]
        sd, length = ptr(), wt.ULONG()
        if not broker._api(adv, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(ptr), ctypes.POINTER(wt.ULONG)], wt.BOOL)(
                _pipe_descriptor(operator_sid), 1, ctypes.byref(sd), ctypes.byref(length)):
            deny()
        try:
            security = Security(ctypes.sizeof(Security), sd, False)
            handle = broker._api(kernel, "CreateNamedPipeW", [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.POINTER(Security)], wt.HANDLE)(
                PIPE_PATH, 3 | 0x40000000 | 0x80000, 8, 1, MAX_FRAME, MAX_FRAME, 1000, ctypes.byref(security))
        finally:
            broker._api(kernel, "LocalFree", [ptr], ptr)(sd)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            deny()
        result = cls(handle, server=True, stop=stop)
        try:
            _strict_pipe_acl(handle, operator_sid)
            return result
        except BaseException:
            result.close()
            raise

    @classmethod
    def connect_client(cls, stop):
        auth.windows_only()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        # Anonymous SQOS prevents a hostile server from impersonating client.
        # OPEN_EXISTING; one attempt only, including ERROR_PIPE_BUSY/absence.
        handle = broker._api(kernel, "CreateFileW", [wt.LPCWSTR, wt.DWORD, wt.DWORD,
            ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE], wt.HANDLE)(
            PIPE_PATH, 0x80000002, 0, None, 3, 0x40000000 | 0x100000, None)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            deny()
        return cls(handle, server=False, stop=stop)

    def _call(self, name, args, result):
        return broker._api(self.kernel, name, args, result)

    def _io(self, name, *, size=0, data=None, deadline):
        if self.poisoned or self.stop.is_set() or time.monotonic() >= deadline:
            raise TimeoutError()
        event = self._call("CreateEventW", [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR], wt.HANDLE)(None, True, False, None)
        if not event:
            deny()
        ov, count = _Overlapped(event=event), wt.DWORD()
        buffer = ctypes.create_string_buffer(data, len(data)) if data is not None else ctypes.create_string_buffer(size)
        pending = False
        quarantined = False
        try:
            if name == "ConnectNamedPipe":
                ok = self._call(name, [wt.HANDLE, ctypes.POINTER(_Overlapped)], wt.BOOL)(self.handle, ctypes.byref(ov))
            else:
                ok = self._call(name, [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                    ctypes.POINTER(wt.DWORD), ctypes.POINTER(_Overlapped)], wt.BOOL)(
                    self.handle, buffer, len(buffer), ctypes.byref(count), ctypes.byref(ov))
            error = 0 if ok else ctypes.get_last_error()
            if name == "ConnectNamedPipe" and error == 535:
                return b""  # A client won the Create/Connect race; connected.
            if not ok and error != 997:
                deny()
            pending = not ok
            while pending:
                if self.stop.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError()
                state = self._call("WaitForSingleObject", [wt.HANDLE, wt.DWORD], wt.DWORD)(event, POLL_MS)
                if state == 258:
                    continue
                if state != 0:
                    deny()
                ok = self._call("GetOverlappedResult", [wt.HANDLE, ctypes.POINTER(_Overlapped),
                    ctypes.POINTER(wt.DWORD), wt.BOOL], wt.BOOL)(self.handle, ctypes.byref(ov), ctypes.byref(count), False)
                pending = not ok and ctypes.get_last_error() == 996
                if not ok:
                    deny()
            if name == "ConnectNamedPipe":
                return b""
            if not 0 < count.value <= len(buffer):
                deny()
            return count.value if name == "WriteFile" else buffer.raw[:count.value]
        finally:
            if pending:
                self._call("CancelIoEx", [wt.HANDLE, ctypes.POINTER(_Overlapped)], wt.BOOL)(self.handle, ctypes.byref(ov))
                state = self._call("WaitForSingleObject", [wt.HANDLE, wt.DWORD], wt.DWORD)(event, CANCEL_MS)
                if state != 0:
                    # CancelIoEx is NOT completion. Never free/reuse its memory.
                    self.poisoned = quarantined = True
                    _QUARANTINED_IO.append((self.handle, event, ov, buffer, count))
                else:
                    complete = self._call("GetOverlappedResult", [wt.HANDLE, ctypes.POINTER(_Overlapped),
                        ctypes.POINTER(wt.DWORD), wt.BOOL], wt.BOOL)(self.handle, ctypes.byref(ov), ctypes.byref(count), False)
                    if not complete and ctypes.get_last_error() == 996:
                        self.poisoned = quarantined = True
                        _QUARANTINED_IO.append((self.handle, event, ov, buffer, count))
            if not quarantined:
                self._call("CloseHandle", [wt.HANDLE], wt.BOOL)(event)
            if quarantined:
                raise _CancellationUnproven()  # Terminate loop; no new request.

    def accept(self, deadline):
        if not self.server:
            deny()
        self._io("ConnectNamedPipe", deadline=deadline)

    def _read(self, count, deadline):
        parts = bytearray()
        while len(parts) < count:
            parts.extend(self._io("ReadFile", size=count - len(parts), deadline=deadline))
        return bytes(parts)

    def recv(self, deadline):
        size = struct.unpack("!I", self._read(4, deadline))[0]
        if not 0 < size <= MAX_FRAME:
            deny()
        return decode(self._read(size, deadline), MAX_FRAME)

    def send(self, value, deadline):
        raw = canonical(value)
        if not 0 < len(raw) <= MAX_FRAME:
            deny()
        raw = struct.pack("!I", len(raw)) + raw
        offset = 0
        while offset < len(raw):
            offset += self._io("WriteFile", data=raw[offset:], deadline=deadline)

    def disconnect(self):
        if self.poisoned:
            raise _CancellationUnproven()
        if not self._call("DisconnectNamedPipe", [wt.HANDLE], wt.BOOL)(self.handle):
            deny()
        # No FlushFileBuffers: a malicious client must not hold shutdown open.

    def close(self):
        if self.handle:
            self._call("CloseHandle", [wt.HANDLE], wt.BOOL)(self.handle)
            self.handle = None


def _validate_connected(pipe):
    release.require_installed_release()
    anchor, config, server, client, source, target = host._context(pipe.handle)
    if server.executable != release.SERVICE_IMAGE:
        deny()
    _strict_pipe_acl(pipe.handle, client.user_sid)
    return anchor, config


class _StatusExchange:
    """Private OFFLINE adapter seam; its result never changes readiness.

    Production constructs this internally with fixed native validation and the
    independently enrolled signer. No public entrypoint accepts these objects.
    """
    def __init__(self, connected_host, signer, validate):
        self.host, self.signer, self.validate = connected_host, signer, validate
        self.lock = threading.Lock()

    def serve(self, pipe):
        if not self.lock.acquire(blocking=False):
            deny()
        began = False
        try:
            deadline = time.monotonic() + EXCHANGE_SECONDS
            anchor, config = self.validate(pipe)
            hello = pipe.recv(deadline)
            shape(hello, "schema operation")
            if hello != dict(schema="WakeHubG4Hello.v1", operation="STATUS"):
                deny()
            self.validate(pipe)
            challenge = self.host.begin(pipe.handle)
            began = True  # G3 has already durably committed UNKNOWN.
            signature = self.signer.sign_once(auth.signing_bytes(challenge.body()),
                identity=observer.BROKER_IDENTITY, namespace=observer.CHALLENGE_NAMESPACE,
                nonce=challenge.nonce, origin_digest=auth._digest(canonical(challenge.body())), deadline=deadline)
            raw = canonical(dict(body=challenge.body(), signature=signature))
            # A signer interface's return value is NOT sufficient authority.
            body = observer._verify(raw, config["broker_signers"]["path"], anchor["authority"]["verifier"]["path"],
                observer.BROKER_IDENTITY, observer.CHALLENGE_NAMESPACE)
            if observer.challenge_from(body) != challenge:
                deny()
            self.validate(pipe)
            pipe.send(dict(schema="WakeHubG4Challenge.v1", signed_challenge=_b64(raw)), deadline)
            value = pipe.recv(deadline)
            shape(value, "schema signed_status signed_request")
            if value["schema"] != "WakeHubG4Completion.v1":
                deny()
            status, request = _unb64(value["signed_status"]), _unb64(value["signed_request"])
            # Shape/policy filter only; G3/G2 still authenticate every byte.
            envelope = decode(request)
            shape(envelope, "body signature")
            if envelope["body"].get("operation") != "STATUS":
                deny()  # Even correctly signed START/REUSE cannot dispatch.
            self.validate(pipe)
            receipt = self.host.complete(pipe.handle, status, request)
            if receipt.operation is not op.Operation.STATUS or receipt.effect is not op.EffectAttempt.NOT_ATTEMPTED:
                deny()
            # Completion journal is durable BEFORE this possibly lost ACK.
            pipe.send(dict(schema="WakeHubG4Receipt.v1", receipt=json.loads(receipt.to_json())), deadline)
            return receipt
        finally:
            if began:
                self.host.abandon()
            self.lock.release()


def _service_loop(stop, ready):
    release.require_installed_release()
    # Startup reads allow only START_PENDING; connected reads require RUNNING.
    anchor = broker._read_broker_anchor()
    config = host._read_g3_config()
    host._validate_config(config, anchor)
    scm = broker._scm_process(start_pending=True)
    process = broker.read_process_identity(os.getpid())
    from dataclasses import asdict
    if (process.executable != release.SERVICE_IMAGE or process.user_sid != broker.service_sid()
            or process.session_id != 0 or anchor["service"]["process"] != asdict(process)
            or scm["pid"] != process.pid or scm["binary"] != '"' + release.SERVICE_IMAGE + '"'
            or scm["account"] != "NT SERVICE\\" + broker.SERVICE or scm["sid_type"] != 1
            or auth._digest(WindowsPathCustody().check(process.executable).read_bytes()) != anchor["service"]["sha256"]):
        deny()
    from ai_commerce.wakehub.g4_desktop_transport import _load_enrolled_signer
    signer = _load_enrolled_signer("broker")
    pipe = _NativePipe.create_server(anchor["authority"]["operator_sid"], stop)
    exchange = _StatusExchange(host.ConnectedStatusHost(), signer, _validate_connected)
    try:
        ready()
        while not stop.is_set():
            try:
                pipe.accept(time.monotonic() + 1)
            except TimeoutError:
                continue
            try:
                exchange.serve(pipe)
            except _CancellationUnproven:
                raise
            except Exception:
                pass  # Never emit raw OS errors or retry the consumed request.
            finally:
                pipe.disconnect()
    finally:
        pipe.close()


def scm_entrypoint():
    """No console fallback/options/install commands; fixed SCM dispatcher.

    The separately packaged executable calls this on its main thread. Source
    Python and CI cannot enroll themselves. Current gate reports STOPPED/1066.
    """
    auth.windows_only()
    if threading.current_thread() is not threading.main_thread() or len(sys.argv) != 1:
        raise release.PackagingOwnerEscalation()
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    main_type = ctypes.WINFUNCTYPE(None, wt.DWORD, ctypes.POINTER(wt.LPWSTR))
    handler_type = ctypes.WINFUNCTYPE(wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p)
    class Status(ctypes.Structure):
        _fields_ = [(name, wt.DWORD) for name in ("kind", "state", "controls", "exit", "specific", "checkpoint", "hint")]
    class Entry(ctypes.Structure):
        _fields_ = [("name", wt.LPWSTR), ("main", main_type)]
    stop, status_lock = threading.Event(), threading.RLock()
    token, current = None, Status(0x10, 2, 0, 0, 0, 1, 5000)
    def report(state, *, error=False):
        nonlocal current
        with status_lock:
            current = Status(0x10, state, 5 if state == 4 else 0,
                1066 if error else 0, 1 if error else 0, 1 if state in (2, 3) else 0,
                1000 if state == 3 else 5000 if state == 2 else 0)
            if not broker._api(adv, "SetServiceStatus", [wt.HANDLE, ctypes.POINTER(Status)], wt.BOOL)(token, ctypes.byref(current)):
                deny()
    @handler_type
    def handler(control, event, data, context):
        try:
            with status_lock:
                if control in (1, 5):
                    stop.set()
                    if current.state != 1:
                        report(3)
                    return 0
                if control == 4:
                    return 0 if broker._api(adv, "SetServiceStatus", [wt.HANDLE, ctypes.POINTER(Status)], wt.BOOL)(token, ctypes.byref(current)) else 1
                return 120
        except BaseException:
            stop.set()
            return 1
    @main_type
    def service_main(argc, argv):
        nonlocal token
        token = broker._api(adv, "RegisterServiceCtrlHandlerExW",
            [wt.LPCWSTR, handler_type, ctypes.c_void_p], wt.HANDLE)(broker.SERVICE, handler, None)
        if not token:
            return
        error = False
        try:
            report(2)
            def ready():
                with status_lock:
                    if stop.is_set():
                        deny()
                    report(4)
            _service_loop(stop, ready)
        except BaseException:
            error = True
        finally:
            try:
                report(1, error=error)
            except BaseException:
                pass
    table = (Entry * 2)(Entry(broker.SERVICE, service_main), Entry(None, main_type()))
    if not broker._api(adv, "StartServiceCtrlDispatcherW", [ctypes.POINTER(Entry)], wt.BOOL)(table):
        deny()


if __name__ == "__main__":
    scm_entrypoint()
