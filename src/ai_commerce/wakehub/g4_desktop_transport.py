"""Interactive one-shot STATUS client, with independently enrolled signer ABI.

No signer generation, secret file reader, generic executable launcher, resident
UI, browser, impersonation or retry. The production signer loader deliberately
raises PACKAGING_OWNER_ESCALATION until a separate custody owner implements and
witnesses it. An injected offline signer never advances readiness.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from dataclasses import asdict
import os
from pathlib import Path
import sys
import threading
import time
from typing import Protocol

from ai_commerce.wakehub import g3_desktop_observer as observer
from ai_commerce.wakehub import g3_service_host as host
from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub import g4_release_provenance as release
from ai_commerce.wakehub.native_journal import canonical, decode, deny, shape


STATUS_REQUEST_PATH = r"C:\ProgramData\AI-Commerce\WakeHubG3\StatusRequest.json"


class SeparatelyEnrolledSigner(Protocol):
    """Required PRIVATE custody ABI, not a claim that a signer is installed.

    Validate live caller/token, protected origin/source generation and fixed
    role/domain. Atomically commit nonce + exact transcript digest as UNKNOWN
    in a pre-enrolled private journal BEFORE signing. Duplicate/restart/lost ACK
    may only read the original outcome, never sign again. Bound work by the
    supplied monotonic deadline. Return an OpenSSH armored signature as base64.
    The broker role cannot read observer/issuer/Human keys or their journals;
    the observer role cannot read broker/issuer/Human keys or service journals.
    """
    def sign_once(self, payload: bytes, *, identity: str, namespace: str, nonce: str,
                  origin_digest: str, deadline: float) -> str: ...


def _load_enrolled_signer(role: str) -> SeparatelyEnrolledSigner:
    if role not in ("broker", "observer"):
        deny()
    # No caller-supplied object, environment switch, CI fixture, shared SSH
    # agent, private-key path or mock trust branch in this production factory.
    raise release.PackagingOwnerEscalation()


def _observe_client_connection(handle):
    """Read-only local endpoint/SCM identity, on the CLIENT end of fixed pipe."""
    auth.windows_only()
    if type(handle) is not int or handle <= 0:
        deny()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    token = wt.HANDLE()
    thread = broker._api(kernel, "GetCurrentThread", [], wt.HANDLE)()
    if broker._api(adv, "OpenThreadToken", [wt.HANDLE, wt.DWORD, wt.BOOL, ctypes.POINTER(wt.HANDLE)], wt.BOOL)(thread, 8, True, ctypes.byref(token)):
        broker._api(kernel, "CloseHandle", [wt.HANDLE], wt.BOOL)(token)
        deny()
    if ctypes.get_last_error() != 1008:
        deny()
    flags = wt.DWORD()
    if (not broker._api(kernel, "GetNamedPipeInfo", [wt.HANDLE, ctypes.POINTER(wt.DWORD),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], wt.BOOL)(handle, ctypes.byref(flags), None, None, None)
            or flags.value & 1):
        deny()
    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wt.USHORT), ("maximum", wt.USHORT), ("buffer", ctypes.c_void_p)]
    buffer, needed = ctypes.create_string_buffer(4096), wt.ULONG()
    ntdll = ctypes.WinDLL("ntdll")
    if broker._api(ntdll, "NtQueryObject", [wt.HANDLE, wt.ULONG, ctypes.c_void_p,
            wt.ULONG, ctypes.POINTER(wt.ULONG)], wt.LONG)(handle, 1, buffer, len(buffer), ctypes.byref(needed)) != 0:
        deny()
    name = ctypes.cast(buffer, ctypes.POINTER(UnicodeString)).contents
    if not name.buffer or name.length % 2 or ctypes.wstring_at(name.buffer, name.length // 2) != broker.PIPE:
        deny()
    facts = {}
    for side in ("Client", "Server"):
        for facet in ("ProcessId", "SessionId"):
            value = wt.ULONG()
            if not broker._api(kernel, "GetNamedPipe" + side + facet,
                    [wt.HANDLE, ctypes.POINTER(wt.ULONG)], wt.BOOL)(handle, ctypes.byref(value)):
                deny()
            facts[side + facet] = value.value
    if facts["ClientProcessId"] != os.getpid():
        deny()
    server = broker.read_process_identity(facts["ServerProcessId"])
    client = broker.read_process_identity(facts["ClientProcessId"])
    if (server.session_id != facts["ServerSessionId"] or client.session_id != facts["ClientSessionId"]
            or client.session_id <= 0):
        deny()
    return dict(server=asdict(server), client=asdict(client), scm=broker._scm_process())


def _validate_client(pipe):
    release.require_installed_release()
    from ai_commerce.wakehub.g4_service_transport import _strict_pipe_acl
    anchor = broker._read_broker_anchor()
    server, client = broker.validate_peer(anchor, _observe_client_connection(pipe.handle))
    _strict_pipe_acl(pipe.handle, client.user_sid)
    config = host._read_g3_config()
    source, target = host._validate_config(config, anchor)
    if (server.executable != release.SERVICE_IMAGE or client.pid != os.getpid()
            or client.session_id <= 0 or client.user_sid != target.user_sid
            or client.session_id != target.session_id or observer.read_logon_id(client.pid) != config["logon_id"]):
        deny()
    auth._validate_anchor_material(anchor["authority"])
    for pin in (anchor["service"], anchor["desktop_peer"]):
        path = host.WindowsPathCustody().check(pin["process"]["executable"])
        if auth._digest(path.read_bytes()) != pin["sha256"]:
            deny()
    return anchor, config


def _one_shot(pipe, signed_request: bytes, signer: SeparatelyEnrolledSigner, validate, *, clock=time.time):
    """Private offline adapter seam. No loop/reconnect or native trust receipt."""
    from ai_commerce.wakehub.g4_service_transport import _b64, _unb64, EXCHANGE_SECONDS
    deadline = time.monotonic() + EXCHANGE_SECONDS
    anchor, config = validate(pipe)
    expected_digest = auth._digest(canonical(dict(anchor=anchor, config=config)))
    def recheck():
        current = validate(pipe)
        if auth._digest(canonical(dict(anchor=current[0], config=current[1]))) != expected_digest:
            deny()
    envelope = decode(signed_request)
    shape(envelope, "body signature")
    body = envelope["body"]
    if (type(body) is not dict or body.get("operation") != "STATUS"
            or body.get("source") != config["source"] or body.get("journal_id") != config["journal_id"]
            or body.get("client") != anchor["desktop_peer"]["process"]
            or body.get("server") != anchor["service"]["process"]):
        deny()
    pipe.send(dict(schema="WakeHubG4Hello.v1", operation="STATUS"), deadline)
    message = pipe.recv(deadline)
    shape(message, "schema signed_challenge")
    if message["schema"] != "WakeHubG4Challenge.v1":
        deny()
    raw = _unb64(message["signed_challenge"])
    body = observer._verify(raw, config["broker_signers"]["path"], anchor["authority"]["verifier"]["path"],
        observer.BROKER_IDENTITY, observer.CHALLENGE_NAMESPACE)
    challenge = observer.challenge_from(body)
    # Expected origin comes from protected enrollment + kernel readback, never
    # merely the source/process/logon fields self-declared by a signed message.
    if (asdict(challenge.source) != config["source"] or challenge.generation != config["generation"]
            or challenge.journal_id != config["journal_id"] or asdict(challenge.target) != config["target"]
            or challenge.logon_id != config["logon_id"]
            or asdict(challenge.server) != anchor["service"]["process"]
            or asdict(challenge.client) != anchor["desktop_peer"]["process"]):
        deny()
    recheck()
    transcript = observer.collect_status_transcript(raw, expected=challenge,
        broker_signers=Path(config["broker_signers"]["path"]), verifier=Path(anchor["authority"]["verifier"]["path"]),
        source=challenge.source, generation=config["generation"], clock=clock)
    # Collector emits the existing exact G3 signing transcript. Recover its
    # body without changing the transcript's bytes or time representation.
    signing = decode(transcript)
    status_body = decode(_unb64(signing["payload_b64"]))
    signature = signer.sign_once(transcript, identity=auth.IDENTITIES["observer"],
        namespace=observer.STATUS_NAMESPACE, nonce=challenge.nonce,
        origin_digest=auth._digest(canonical(challenge.body())), deadline=deadline)
    status = canonical(dict(body=status_body, signature=signature))
    observer.verify_status(status, expected=challenge, anchor=anchor["authority"], now=clock())
    recheck()
    pipe.send(dict(schema="WakeHubG4Completion.v1", signed_status=_b64(status), signed_request=_b64(signed_request)), deadline)
    message = pipe.recv(deadline)
    shape(message, "schema receipt")
    if message["schema"] != "WakeHubG4Receipt.v1":
        deny()
    value = message["receipt"]
    receipt = op.PublicReceipt(op.Operation(value["operation"]), op.Decision(value["decision"]),
        op.Reason(value["reason"]), op.EffectAttempt(value["effect"]))
    if (receipt.operation is not op.Operation.STATUS or receipt.effect is not op.EffectAttempt.NOT_ATTEMPTED
            or canonical(decode(receipt.to_json().encode())) != canonical(value)):
        deny()
    recheck()
    return receipt


def status_once(signed_request: bytes) -> op.PublicReceipt:
    """Production one-shot API; no injected adapters, origins, signers or PIDs."""
    release.require_installed_release()
    # Check the actual primary token BEFORE connecting or any desktop API.
    process = broker.read_process_identity(os.getpid())
    if process.session_id <= 0:
        deny()
    signer = _load_enrolled_signer("observer")
    from ai_commerce.wakehub.g4_service_transport import _NativePipe
    pipe = _NativePipe.connect_client(threading.Event())
    try:
        return _one_shot(pipe, signed_request, signer, _validate_client)
    finally:
        pipe.close()


def desktop_entrypoint() -> op.PublicReceipt:
    """Fixed packaged one-shot entry; no CLI paths or public Issue ingress."""
    release.require_installed_release()
    auth.windows_only()
    if len(sys.argv) != 1 or broker.read_process_identity(os.getpid()).session_id <= 0:
        deny()
    path = host.WindowsPathCustody().check(STATUS_REQUEST_PATH)
    broker._protected_dacl(str(path), 1)
    if not 0 < path.stat().st_size <= 262144:
        deny()
    return status_once(path.read_bytes())


if __name__ == "__main__":
    desktop_entrypoint()
