"""Interactive STATUS observation and exact signing transcripts, no GUI action.

Private signing is an external, independently enrolled role. This source never
generates keys, enrolls a signer, or treats an unsigned observation as evidence.
The service verifies the signed response against its OWN pending challenge.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import re
import time
from uuid import UUID

from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub import native_inventory as inv
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.g3_enrollment import TASK_PARENT
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
                or not all(_number(v) for v in (self.issued_at, self.expires_at))
                or not 0 < self.issued_at < self.expires_at < 2**53
                or self.expires_at - self.issued_at > 5):
            deny()

    def body(self):
        return dict(schema="WakeHubG3DesktopChallenge.v1", task_parent=TASK_PARENT,
                    operation="STATUS", **asdict(self))

    def fresh(self, now):
        if not _number(now) or not self.issued_at <= now < self.expires_at:
            deny()


def challenge_from(body) -> Challenge:
    shape(body, "schema task_parent operation nonce source generation journal_id server client target logon_id issued_at expires_at")
    if (body["schema"] != "WakeHubG3DesktopChallenge.v1" or body["task_parent"] != TASK_PARENT
            or body["operation"] != "STATUS"):
        deny()
    return Challenge(body["nonce"], op.SourceBinding(**body["source"]), body["generation"], body["journal_id"],
                     inv.ProcessIdentity(**body["server"]), inv.ProcessIdentity(**body["client"]),
                     op.PrivateTarget(**body["target"]), body["logon_id"], body["issued_at"], body["expires_at"])


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
