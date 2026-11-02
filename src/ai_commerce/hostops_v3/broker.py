"""Local-only authenticated IPC. CI never hosts code under the broker token.

No network listener. Windows uses a remote-rejecting named pipe with an explicit
SID DACL and peer-token check; Linux uses AF_UNIX and SO_PEERCRED. All frames are
bounded bytes, never pickle. Mutual HMAC binds responses to a fresh challenge.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import multiprocessing
import os
from pathlib import Path
import socket
import struct
import time

from .auth import verify_release
from .contract import MAX_WIRE, Denied, canonical, decode, deny, envelope, dual_repo_envelope, public_receipt, receipt, shape
from .journal import Journal
from .security import admit, load_config, windows_identity
from .worker import Worker

IPC_LIMIT = 262144
IPC_TIMEOUT = 45  # Greater than the 30s operation + 8s startup watchdog budget.


def mac(key, role, nonce, data=b""):
    return hmac.new(key, role + nonce + data, hashlib.sha256).hexdigest()


class Channel:
    def read_exact(self, size):
        end, out = time.monotonic() + IPC_TIMEOUT, bytearray()
        while len(out) < size:
            if time.monotonic() > end:
                deny("IPC_TIMEOUT")
            part = self.read(size - len(out))
            if not part:
                deny("IPC_DISCONNECTED")
            out.extend(part)
        return bytes(out)

    def receive(self):
        length = struct.unpack("!I", self.read_exact(4))[0]
        if not 0 < length <= IPC_LIMIT:
            deny("IPC_FRAME_LIMIT")
        return self.read_exact(length)

    def send(self, data):
        if not 0 < len(data) <= IPC_LIMIT:
            deny("IPC_FRAME_LIMIT")
        self.write(struct.pack("!I", len(data)) + data)


class UnixChannel(Channel):
    def __init__(self, connection):
        self.connection = connection
        connection.settimeout(IPC_TIMEOUT)

    def read(self, size):
        return self.connection.recv(size)

    def write(self, data):
        self.connection.sendall(data)

    def close(self):
        self.connection.close()


class WindowsChannel(Channel):
    def __init__(self, handle):
        self.handle = handle
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.PeekNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
        self.kernel.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
        self.kernel.WriteFile.argtypes = self.kernel.ReadFile.argtypes
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)

    def read(self, size):
        from ctypes import wintypes
        available = wintypes.DWORD()
        end = time.monotonic() + IPC_TIMEOUT
        while time.monotonic() < end:
            if not self.kernel.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
                deny("IPC_DISCONNECTED")
            if available.value:
                count = wintypes.DWORD()
                buffer = ctypes.create_string_buffer(min(size, available.value))
                if not self.kernel.ReadFile(self.handle, buffer, len(buffer), ctypes.byref(count), None):
                    deny("IPC_DISCONNECTED")
                return buffer.raw[:count.value]
            time.sleep(0.01)
        deny("IPC_TIMEOUT")

    def write(self, data):
        from ctypes import wintypes
        count = wintypes.DWORD()
        if not self.kernel.WriteFile(self.handle, data, len(data), ctypes.byref(count), None) or count.value != len(data):
            deny("IPC_DISCONNECTED")

    def close(self):
        self.kernel.CloseHandle(self.handle)


def validate_pipe_name(endpoint):
    import re
    if not re.fullmatch(r"\\\\\.\\pipe\\(?:LOCAL\\)?AICommerceHostOpsV3-[A-Za-z0-9_-]{8,64}", endpoint):
        deny("NONLOCAL_ENDPOINT")


def windows_listener(config):
    from ctypes import wintypes
    endpoint = config["endpoint"]
    validate_pipe_name(endpoint)
    for sid in [config["broker_identity"], config["transport_identity"], *config["trusted_owners"]]:
        import re
        if not re.fullmatch(r"S-1-[0-9-]{2,180}", sid):
            deny("BAD_SID")
    sids = {config["broker_identity"], config["transport_identity"], *config["trusted_owners"]}
    descriptor = ctypes.c_void_p()
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
                                                                      ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
    dacl = "D:P" + "".join("(A;;GA;;;" + sid + ")" for sid in sorted(sids))
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(dacl, 1, ctypes.byref(descriptor), None):
        deny("PIPE_ACL_UNAVAILABLE")
    class Attributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]
    attrs = Attributes(ctypes.sizeof(Attributes), descriptor, False)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Attributes))
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    try:
        handle = kernel.CreateNamedPipeW(endpoint, 3 | 0x00080000, 1 | 8, 1, IPC_LIMIT + 4, IPC_LIMIT + 4, 1000, ctypes.byref(attrs))
    finally:
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree(descriptor)
    if handle == ctypes.c_void_p(-1).value:
        deny("PIPE_UNAVAILABLE")
    return WindowsChannel(handle)


def windows_wait_connected(channel, *, stop=None, timeout=None):
    """Wait until a local client is *actually connected* on a PIPE_NOWAIT handle.

    With PIPE_NOWAIT, an initial nonzero ConnectNamedPipe result only says that
    the instance is ready for a client; it is NOT connection proof. Only
    ERROR_PIPE_CONNECTED (535) proves attachment. 536 means still listening.
    Keep the existing bounded poll/KILL behavior; never skip windows_peer().
    """
    from ctypes import wintypes
    channel.kernel.ConnectNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    channel.kernel.ConnectNamedPipe.restype = wintypes.BOOL
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        if stop is not None and stop():
            return False
        ready = channel.kernel.ConnectNamedPipe(channel.handle, None)
        if not ready:
            error = ctypes.get_last_error()
            if error == 535:  # ERROR_PIPE_CONNECTED: actual client attached.
                return True
            if error != 536:  # ERROR_PIPE_LISTENING: no client yet.
                deny("PIPE_UNAVAILABLE")
        # A nonzero PIPE_NOWAIT result may just mean first-time ready state.
        if deadline is not None and time.monotonic() >= deadline:
            deny("PIPE_CONNECT_TIMEOUT")
        time.sleep(0.01)


def windows_peer(channel, config):
    from ctypes import wintypes
    kernel = channel.kernel
    pid = wintypes.ULONG()
    kernel.GetNamedPipeClientProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
    if not kernel.GetNamedPipeClientProcessId(channel.handle, ctypes.byref(pid)):
        deny("IPC_PEER_UNAVAILABLE")
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        deny("IPC_PEER_UNAVAILABLE")
    try:
        peer, elevated = windows_identity(process)
        if peer != config["transport_identity"] or elevated:
            deny("IPC_PEER_DENIED")
    finally:
        kernel.CloseHandle(process)


def server_exchange(channel, key, execute):
    nonce = os.urandom(32)
    channel.send(canonical({"nonce": nonce.hex(), "mac": mac(key, b"server", nonce)}))
    message = decode(channel.receive(), IPC_LIMIT)
    shape(message, "payload_b64 mac")
    try:
        payload = base64.b64decode(message["payload_b64"], validate=True)
    except (ValueError, TypeError):
        deny("IPC_AUTH_FAILED")
    if len(payload) > MAX_WIRE or type(message["mac"]) is not str or not hmac.compare_digest(message["mac"], mac(key, b"client", nonce, payload)):
        deny("IPC_AUTH_FAILED")
    body = canonical(execute(payload))
    channel.send(canonical({"response_b64": base64.b64encode(body).decode("ascii"),
                            "mac": mac(key, b"response", nonce, body)}))
    # Acknowledgement prevents Windows discarding unread bytes on pipe close.
    ack = decode(channel.receive())
    shape(ack, "ack")
    if ack["ack"] != mac(key, b"ack", nonce, body):
        deny("IPC_AUTH_FAILED")


def client_exchange(channel, key, raw):
    greeting = decode(channel.receive())
    shape(greeting, "nonce mac")
    try:
        nonce = bytes.fromhex(greeting["nonce"])
    except (TypeError, ValueError):
        deny("IPC_AUTH_FAILED")
    if len(nonce) != 32 or type(greeting["mac"]) is not str or not hmac.compare_digest(greeting["mac"], mac(key, b"server", nonce)):
        deny("IPC_AUTH_FAILED")
    channel.send(canonical({"payload_b64": base64.b64encode(raw).decode("ascii"), "mac": mac(key, b"client", nonce, raw)}))
    response = decode(channel.receive(), IPC_LIMIT)
    shape(response, "response_b64 mac")
    try:
        body = base64.b64decode(response["response_b64"], validate=True)
    except (TypeError, ValueError):
        deny("IPC_AUTH_FAILED")
    if type(response["mac"]) is not str or not hmac.compare_digest(response["mac"], mac(key, b"response", nonce, body)):
        deny("IPC_AUTH_FAILED")
    value = decode(body)
    if type(value) is not dict or not {"receipt"} <= value.keys() or not value.keys() <= {"receipt", "disclosure_b64"}:
        deny("BAD_RECEIPT")
    public_receipt(value["receipt"])
    channel.send(canonical({"ack": mac(key, b"ack", nonce, body)}))
    return value


def connect(endpoint):
    if os.name == "nt":
        validate_pipe_name(endpoint)
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        handle = kernel.CreateFileW(endpoint, 0xC0000000, 0, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise Denied("BROKER_UNAVAILABLE") from ctypes.WinError(ctypes.get_last_error())
        return WindowsChannel(handle)
    if not Path(endpoint).is_absolute():
        deny("NONLOCAL_ENDPOINT")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(10)
    try:
        connection.connect(endpoint)
    except OSError:
        connection.close()
        deny("BROKER_UNAVAILABLE")
    return UnixChannel(connection)


def child_execute(config_path, payload, pipe):
    # Fresh process: a blocking UNC/filesystem call cannot outlive the request budget.
    try:
        config = load_config(config_path)
        admit(config, config_path)
        _, authority = verify_release(config)
        journal = Journal(Path(config["state_dir"]) / "journal.sqlite")
        try:
            mode, raw = ipc_request(payload)
            result = Worker(config, authority, journal).execute(raw, reconcile=mode == "reconcile")
            pipe.send_bytes(canonical(result))
        finally:
            journal.close()
    except (Denied, OSError):
        try:
            _, raw = ipc_request(payload)
            _, request = envelope(raw)
        except Denied:
            request = None
        pipe.send_bytes(canonical({"receipt": receipt(request, "UNAVAILABLE", "BROKER_UNAVAILABLE")}))
    finally:
        pipe.close()


def isolated_execute(config_path, payload):
    _, raw = ipc_request(payload)
    _, request = envelope(raw)
    ctx = multiprocessing.get_context("spawn")
    incoming, outgoing = ctx.Pipe(duplex=False)
    process = ctx.Process(target=child_execute, args=(str(config_path), payload, outgoing))
    process.start()
    outgoing.close()
    try:
        end = time.monotonic() + request.value["timeout_seconds"] + 8
        while time.monotonic() < end:
            if incoming.poll(0.1):
                return decode(incoming.recv_bytes(IPC_LIMIT), IPC_LIMIT)
            config = load_config(config_path)
            if not config["enabled"] or (Path(config["policy_dir"]) / "KILL").exists():
                break
        return {"receipt": receipt(request, "UNKNOWN", "RECONCILIATION_REQUIRED")}
    except (EOFError, OSError):
        return {"receipt": receipt(request, "UNKNOWN", "RECONCILIATION_REQUIRED")}
    finally:
        if process.is_alive():
            process.terminate()
        process.join(2)
        incoming.close()


def ipc_request(payload):
    message = decode(payload)
    shape(message, "schema mode envelope")
    if message["schema"] != "hostops.ipc.v3" or message["mode"] not in ("execute", "reconcile"):
        deny("BAD_IPC_MODE")
    raw = canonical(message["envelope"])
    envelope(raw)
    return message["mode"], raw


def serve(config_path):
    config = load_config(config_path)
    admit(config, config_path)
    verify_release(config)
    key = (Path(config["policy_dir"]) / "ipc.key").read_bytes()
    if len(key) != 32:
        deny("IPC_KEY_INVALID")
    if os.name == "nt":
        while not (Path(config["policy_dir"]) / "KILL").exists():
            channel = windows_listener(config)
            try:
                if not windows_wait_connected(
                        channel, stop=lambda: (Path(config["policy_dir"]) / "KILL").exists()):
                    return
                windows_peer(channel, config)
                server_exchange(channel, key, lambda raw: isolated_execute(config_path, raw))
            except (Denied, OSError):
                pass  # No untrusted request/exception text in logs.
            finally:
                channel.close()
        return
    endpoint = Path(config["endpoint"])
    if not endpoint.is_absolute() or endpoint.parent != Path(config["state_dir"]):
        deny("NONLOCAL_ENDPOINT")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(endpoint))  # Refuse stale endpoints; never unlink an unknown file.
    os.chmod(endpoint, 0o660)
    listener.listen(1)
    listener.settimeout(1)
    try:
        while not (Path(config["policy_dir"]) / "KILL").exists():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            channel = UnixChannel(connection)
            try:
                _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if str(uid) != config["transport_identity"]:
                    deny("IPC_PEER_DENIED")
                server_exchange(channel, key, lambda raw: isolated_execute(config_path, raw))
            except (Denied, OSError):
                pass
            finally:
                channel.close()
    finally:
        listener.close()
        endpoint.unlink()



def ipc_request_v4(payload):
    """Strict V4 IPC framing, separate from v3 broker/CLI dispatch."""
    message = decode(payload)
    shape(message, "schema mode envelope")
    if message["schema"] != "hostops.ipc.v4" or message["mode"] not in ("execute", "reconcile"):
        deny("BAD_IPC_MODE")
    raw = canonical(message["envelope"])
    dual_repo_envelope(raw)
    return message["mode"], raw
