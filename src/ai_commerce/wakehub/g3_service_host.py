"""Source-only SCM staging and authenticated connected-HANDLE broker hosting.

The packaged SCM transport is deliberately not installed or started here. The
entrypoint reports STOPPED with a custody HOLD until that separate enrollment;
it never claims RUNNING because a Python function or SID derivation succeeded.
The connected-HANDLE host below is the fixed transport handoff. No caller may
supply its own identity facts, authority factory, executor, command or URL.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as wt
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from uuid import UUID, uuid4

from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub import native_runtime as runtime
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub import g3_desktop_observer as observer
from ai_commerce.wakehub.g3_enrollment import G3_ANCHOR, PRIVATE_ROOT, TASK_PARENT
from ai_commerce.wakehub.native_journal import canonical, decode, deny, shape, WindowsPathCustody, OS_TRUSTED_SIDS


CHALLENGES_PATH = PRIVATE_ROOT + r"\challenges.sqlite3"


def _read_g3_config():
    """Read the one admin-owned protected anchor, never a request-selected file."""
    auth.windows_only()
    import winreg
    for suffix in (r"SOFTWARE", r"SOFTWARE\AI-Commerce", G3_ANCHOR):
        WindowsPathCustody.check_descriptor(*WindowsPathCustody._descriptor("MACHINE\\" + suffix, 4), OS_TRUSTED_SIDS)
    broker._protected_dacl("MACHINE\\" + G3_ANCHOR, 4)
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, G3_ANCHOR, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        raw, kind = winreg.QueryValueEx(key, "Anchor")
    if kind != winreg.REG_SZ or type(raw) is not str:
        deny()
    return decode(raw.encode("utf-8"), 32768)


def _validate_config(config, anchor):
    shape(config, "schema task_parent source generation journal_id target logon_id broker_signers challenges_path")
    if (config["schema"] != "WakeHubG3Host.v1" or config["task_parent"] != TASK_PARENT
            or type(config["generation"]) is not int or not 1 <= config["generation"] < 2**53
            or config["journal_id"] != anchor["authority"]["journal_id"]
            or type(config["logon_id"]) is not str or not re.fullmatch(r"[0-9a-f]{16}", config["logon_id"])
            or config["logon_id"] == "0" * 16 or config["challenges_path"] != CHALLENGES_PATH):
        deny()
    source = op.SourceBinding(**config["source"])
    target = op.PrivateTarget(**config["target"])
    shape(config["broker_signers"], "path sha256")
    pin = config["broker_signers"]
    if type(pin["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]):
        deny()
    raw = WindowsPathCustody().check(pin["path"]).read_bytes()
    lines = [line.split() for line in raw.decode("ascii").splitlines() if line.strip()]
    if (auth._digest(raw) != pin["sha256"] or len(lines) != 1 or len(lines[0]) != 3
            or lines[0][:2] != [observer.BROKER_IDENTITY, "ssh-ed25519"]):
        deny()
    # The broker must never hold or share any issuer/Human/observer signing key.
    for role in ("issuer", "human", "observer"):
        old = WindowsPathCustody().check(anchor["authority"][role + "_signers"]["path"]).read_text().split()
        if len(old) != 3 or old[2] == lines[0][2]:
            deny()
    return source, target


def _peer_context(facts):
    """Shared protected pins; facts come ONLY from the appropriate kernel end."""
    anchor = broker._read_broker_anchor()
    server, client = broker.validate_peer(anchor, facts)
    auth._validate_anchor_material(anchor["authority"])
    for pin in (anchor["service"], anchor["desktop_peer"]):
        path = WindowsPathCustody().check(pin["process"]["executable"])
        if auth._digest(path.read_bytes()) != pin["sha256"]:
            deny()
    config = _read_g3_config()
    source, target = _validate_config(config, anchor)
    if (client.user_sid != target.user_sid or client.session_id != target.session_id
            or client.user_sid != anchor["authority"]["operator_sid"]
            or observer.read_logon_id(client.pid) != config["logon_id"]
            or broker.service_sid() in (client.user_sid, *anchor["authority"]["runner_sids"])
            or set(anchor["authority"]["runner_sids"]) & OS_TRUSTED_SIDS):
        deny()
    return anchor, config, server, client, source, target


def _context(handle):
    # The desktop end uses the public checks above, never service-private files.
    auth.windows_only()
    context = _peer_context(broker.observe_connection(handle))
    anchor, config, *_ = context
    private = (anchor["authority"]["journal_path"], anchor["requests_path"],
               anchor["private_key_path"], anchor["desktop_path"], config["challenges_path"])
    if len(set(private)) != len(private):
        deny()
    for path in private:
        broker.ServiceCustody().check(path)
    return context


class G3ServiceAuthority(broker.WindowsServiceAuthority):
    """G2 signed grants/custody with fresh G3 desktop proof at EVERY boundary."""
    def __init__(self, handle, challenge, signed_status, config_digest, *, clock=time.time):
        self.challenge, self.signed_status, self.config_digest = challenge, signed_status, config_digest
        super().__init__(handle, clock=clock)

    def _read_anchor(self):
        anchor = super()._read_anchor()
        config = _read_g3_config()
        source, target = _validate_config(config, self.broker)
        if (auth._digest(canonical(config)) != self.config_digest
                or source != self.challenge.source or target != self.challenge.target
                or config["generation"] != self.challenge.generation
                or config["logon_id"] != self.challenge.logon_id):
            deny()
        return anchor

    def _check_desktop(self, target):
        challenge = self.challenge
        if (target != challenge.target or self._loading_source != challenge.source
                or self.server != challenge.server or self.client != challenge.client
                or observer.read_logon_id(self.client.pid) != challenge.logon_id):
            deny()
        # No input desktop API, impersonation, browser operation or GUI in Session 0.
        observer.verify_status(self.signed_status, expected=challenge,
                               anchor=self.broker["authority"], now=self.clock())


def _connected_runtime(handle, challenge, raw_status, config_digest, clock):
    authority = G3ServiceAuthority(handle, challenge, raw_status, config_digest, clock=clock)
    value = authority.enrolled
    journal = broker.ServiceEffectJournal(value["journal_path"], owner_sid=broker.service_sid(),
        journal_id=value["journal_id"], lane=value["lane"])
    authority.journal = journal
    authority._load(clock())
    result = runtime.WindowsPrivateRuntime()
    result._authority = authority
    result._broker = broker.BrokerRequests(authority.broker["requests_path"],
        owner_sid=broker.service_sid(), journal_id=value["journal_id"])
    result._operator = op.PrivateLifecycleOperator(op.LifecyclePolicy(authority.source),
        verifier=authority, journal=journal, executor=runtime.WindowsExecutor(), clock=clock)
    return result


def _unknown():
    return op.PublicReceipt(op.Operation.STATUS, op.Decision.UNKNOWN_EFFECT, op.Reason.RECONCILE_ONLY)


class ConnectedStatusHost:
    """Single-flight staged transport handoff; only live server HANDLEs accepted.

    begin's exact transcript must be signed by the separately enrolled broker
    role and delivered to the pinned desktop observer. complete takes the
    observer signature plus an existing G2 signed request/grant/Human consent.
    All ledgers must already exist. No automatic creation, retry or reset.
    """
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self._pending = None
        self._lock = threading.Lock()
        self._request_reservation = None

    def begin(self, handle, *, request_sha256=None) -> observer.Challenge:
        with self._lock:
            if self._pending is not None:
                deny()
            anchor, config, server, client, source, target = _context(handle)
            now = self.clock()
            challenge = observer.Challenge(str(uuid4()), source, config["generation"], config["journal_id"],
                server, client, target, config["logon_id"], now, now + 5, request_sha256)
            journal = broker.BrokerRequests(config["challenges_path"], owner_sid=broker.service_sid(),
                                            journal_id=config["journal_id"])
            digest = auth._digest(canonical(challenge.body()))
            if not journal.claim(challenge.nonce, digest):
                deny()
            # Durable UNKNOWN precedes handing the challenge to any caller.
            self._pending = (handle, challenge, journal, digest, auth._digest(canonical(config)))
            return challenge

    def abandon(self):
        with self._lock:
            self._pending = None  # Durable UNKNOWN remains permanently consumed.

    def complete(self, handle, signed_status: bytes, signed_request: bytes) -> op.PublicReceipt:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.DUPLICATE)
        original_handle, challenge, journal, digest, config_digest = pending
        try:
            if type(handle) is not int or handle != original_handle:
                deny()
            if (challenge.request_sha256 is not None and (type(signed_request) is not bytes
                    or auth._digest(signed_request) != challenge.request_sha256)):
                deny()
            challenge.fresh(self.clock())
            request = decode(signed_request, observer.STATUS_FRAME_BODY_LIMIT)
            shape(request, "body signature")
            if type(request["body"]) is not dict or canonical(request) != signed_request:
                deny()
            # Preserve signed G2 reconciliation on the existing unbound
            # handoff. Request-bound frame exchanges remain strictly STATUS.
            allowed = ("STATUS",) if challenge.request_sha256 is not None else ("STATUS", "RECONCILE_ONLY")
            operation = request["body"].get("operation")
            if operation not in allowed:
                return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.INVALID_REQUEST)
            operation = op.Operation(operation)
            # Reopen native custody, SCM, token, source, desktop and both
            # journals. A changed pipe/token/anchor never inherits old trust.
            native = _connected_runtime(handle, challenge, signed_status, config_digest, self.clock)
            receipt = (native.run_service_request(signed_request) if self._request_reservation is None
                       else self._request_reservation.complete(native, signed_request))
            challenge.fresh(self.clock())  # A late verification/ACK cannot finish the nonce.
            if (type(receipt) is not op.PublicReceipt or receipt.operation is not operation
                    or receipt.effect is not op.EffectAttempt.NOT_ATTEMPTED):
                deny()
            if receipt.decision is op.Decision.UNKNOWN_EFFECT:
                return receipt  # Keep the challenge UNKNOWN as well as consumed.
            journal.finish(challenge.nonce, digest, receipt)
            challenge.fresh(self.clock())
            return receipt
        except Exception:
            return _unknown()


class _StatusRequestReservation:
    """Existing G2 request ledger, independently UNKNOWN before the G3 offer."""
    def __init__(self, anchor, body):
        nonce = body["nonce"]
        if type(nonce) is not str or str(UUID(nonce)) != nonce:
            deny()
        self.nonce, self.digest = nonce, auth._digest(canonical(body))
        self.requests = broker.BrokerRequests(anchor["requests_path"], owner_sid=broker.service_sid(),
                                              journal_id=body["journal_id"])
        if not self.requests.claim(self.nonce, self.digest):
            deny()
        self._entered = False

    def complete(self, native, raw):
        # Keep G2's verifier, Human consent/pause and revocation rechecks. Only
        # its second claim is omitted: this exact request is already consumed.
        if self._entered:
            deny()
        self._entered = True
        operation, proof, nonce, digest = native._authority.verify_request(raw)
        if (operation is not op.Operation.STATUS or (nonce, digest) != (self.nonce, self.digest)
                or native._broker.path != self.requests.path
                or native._broker.journal_id != self.requests.journal_id):
            deny()
        with self.requests._transaction() as db:
            if db.execute("SELECT digest,state,receipt FROM requests WHERE nonce=?", (nonce,)).fetchall() != [
                    (digest, "UNKNOWN", None)]:
                deny()
        native._authority._load(native._authority.clock())
        result = native._run_authenticated(op.Operation.STATUS, proof)
        native._authority.clock()  # Includes the connected monotonic deadline.
        if (type(result) is not op.PublicReceipt or result.operation is not op.Operation.STATUS
                or result.effect is not op.EffectAttempt.NOT_ATTEMPTED):
            deny()
        if result.decision is not op.Decision.UNKNOWN_EFFECT:
            self.requests.finish(nonce, digest, result)
        return result


class ConnectedStatusExchange:
    """One bounded source-only exchange on an independently owned server HANDLE.

    No listener, SCM loop, signer, I/O callback or substitute native readiness.
    begin -> external broker signature -> offer -> external observer signature
    -> complete. A reserved attempt is terminal on ANY loss/failure; callers
    must abandon on partial I/O or timeout. All durable claims remain in the
    existing G3 challenge and G2 request/effect ledgers.
    """
    def __init__(self, *, clock=time.time, monotonic=time.monotonic):
        self._wall_clock, self._monotonic = clock, monotonic
        self._deadline = None
        self.clock = self._now
        self._host = ConnectedStatusHost(clock=self.clock)
        self._lock = threading.Lock()
        self._state = "NEW"

    def _now(self):
        if self._deadline is not None:
            self._deadline.check()
        return self._wall_clock()

    def _recheck(self, handle):
        if type(handle) is not int or handle != self._handle:
            deny()
        now = self.clock()
        self._challenge.fresh(now)
        if now < self._last_time:
            deny()
        context = _context(handle)
        anchor, config, server, client, source, target = context
        if (auth._digest(canonical([anchor, config])) != self._pins_digest
                or (server, client, source, target) != (self._challenge.server,
                    self._challenge.client, self._challenge.source, self._challenge.target)
                or config["logon_id"] != self._challenge.logon_id):
            deny()
        self._last_time = now
        return anchor, config

    def begin(self, handle, signed_request: bytes) -> observer.Challenge:
        with self._lock:
            if self._state != "NEW":
                deny()
            self._state = "CLOSED"
            try:
                self._deadline = observer._StatusDeadline(self._monotonic)
                if type(handle) is not int or handle <= 0:
                    deny()
                value = decode(signed_request, observer.STATUS_FRAME_BODY_LIMIT)
                if canonical(value) != signed_request:
                    deny()
                anchor, config, server, client, source, target = _context(handle)
                body = broker._signed(signed_request, "issuer", "request", anchor["authority"])
                shape(body, "schema task_parent journal_id source server client operation proof nonce issued_at expires_at")
                auth._fresh(body, self.clock())
                if (body["schema"] != "WakeHubBrokerRequest.v1" or body["task_parent"] != auth.TASK_PARENT
                        or body["operation"] != "STATUS" or body["journal_id"] != config["journal_id"]
                        or canonical(body["source"]) != canonical(asdict(source))
                        or canonical(body["server"]) != canonical(asdict(server))
                        or canonical(body["client"]) != canonical(asdict(client))):
                    deny()
                self._pins_digest = auth._digest(canonical([anchor, config]))
                self._handle, self._request = handle, signed_request
                self._challenge = self._host.begin(handle, request_sha256=auth._digest(signed_request))
                self._host._request_reservation = _StatusRequestReservation(anchor, body)
                self._last_time = self._challenge.issued_at
                self._recheck(handle)
                self._state = "RESERVED"
                return self._challenge
            except Exception:
                self._host.abandon()
                deny()

    def offer(self, handle, signed_challenge: bytes) -> bytes:
        with self._lock:
            if self._state != "RESERVED":
                deny()
            self._state = "CLOSED"
            try:
                anchor, config = self._recheck(handle)
                frame = observer.StatusFrame(observer.StatusRole.BROKER, signed_challenge)
                frame.check_binding(self._challenge)
                observer._verify(signed_challenge, config["broker_signers"]["path"],
                    anchor["authority"]["verifier"]["path"], observer.BROKER_IDENTITY,
                    observer.CHALLENGE_NAMESPACE)
                self._recheck(handle)
                raw = frame.to_bytes()
                self._state = "OFFERED"
                return raw
            except Exception:
                self._host.abandon()
                deny()

    def complete(self, handle, raw: bytes) -> op.PublicReceipt:
        # Never queue a competing completion behind verification/I/O latency.
        if not self._lock.acquire(blocking=False):
            return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.DUPLICATE)
        try:
            if self._state != "OFFERED":
                reserved = self._state == "RESERVED"
                self._state = "CLOSED"
                self._host.abandon()
                return _unknown() if reserved else op.PublicReceipt(
                    op.Operation.STATUS, op.Decision.HOLD, op.Reason.DUPLICATE)
            self._state = "CLOSED"  # First wins before decoding or verification.
            try:
                self._recheck(handle)
                frame = observer.StatusFrame.from_bytes(raw, expected=self._challenge,
                    role=observer.StatusRole.OBSERVER)
                receipt = self._host.complete(handle, frame.signed_payload, self._request)
                if (type(receipt) is not op.PublicReceipt or receipt.operation is not op.Operation.STATUS
                        or receipt.effect is not op.EffectAttempt.NOT_ATTEMPTED):
                    deny()
                return receipt
            except Exception:
                self._host.abandon()
                return _unknown()
        finally:
            self._lock.release()

    def abandon(self) -> op.PublicReceipt:
        with self._lock:
            reserved = self._state in ("RESERVED", "OFFERED")
            self._state = "CLOSED"
            self._host.abandon()
            return _unknown() if reserved else op.PublicReceipt(
                op.Operation.STATUS, op.Decision.HOLD, op.Reason.DUPLICATE)


class ConnectedStatusIO:
    """Private, one-shot service adapter; consumes an existing server HANDLE.

    begin returns the transcript for the separately enrolled broker signer.
    exchange accepts its signature, writes ONE offer and reads ONE reply. No
    callback, listener, native open, signer, retry or readiness assertion exists.
    The installed owner must transfer sole ownership of an overlapped,
    message-mode HANDLE; a competing adapter is rejected before any I/O.
    """
    def __init__(self, *, clock=time.time, monotonic=time.monotonic):
        self._exchange = ConnectedStatusExchange(clock=clock, monotonic=monotonic)
        self._pipe = None
        self._lock = threading.Lock()
        self._used = False

    def begin(self, handle, signed_request: bytes) -> observer.Challenge:
        with self._lock:
            if self._used:
                deny()
            self._used = True
            try:
                # Authenticate before taking ownership or touching pipe data.
                _context(handle)
                deadline = observer._StatusDeadline(self._exchange._monotonic)
                self._pipe = observer._StatusPipeIO(handle, observer._PipeSide.SERVER, deadline)
                challenge = self._exchange.begin(handle, signed_request)
                self._exchange._deadline = deadline  # Never restart the budget.
                deadline.check()
                return challenge
            except Exception:
                self._exchange.abandon()
                if self._pipe is not None:
                    self._pipe.close()
                deny()

    def exchange(self, signed_challenge: bytes) -> op.PublicReceipt:
        if not self._lock.acquire(blocking=False):
            return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.DUPLICATE)
        try:
            if self._pipe is None or self._pipe.closed:
                return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.DUPLICATE)
            pipe = self._pipe
            try:
                raw = self._exchange.offer(pipe.handle, signed_challenge)
                pipe.write_frame(raw)
                self._exchange._recheck(pipe.handle)
                reply = pipe.read_frame()
                self._exchange._recheck(pipe.handle)
                return self._exchange.complete(pipe.handle, reply)
            except Exception:
                self._exchange.abandon()
                return _unknown()
            finally:
                pipe.close()
        finally:
            self._lock.release()

    def abandon(self) -> op.PublicReceipt:
        with self._lock:
            result = self._exchange.abandon()
            if self._pipe is not None:
                self._pipe.close()
            return result


def staged_scm_entrypoint():
    """Fixed SCM entrypoint. Never install/start a service or create a listener.

    This source stage has no enrolled transport loop. Correct SCM registration
    therefore reports STOPPED / service-specific custody HOLD, never RUNNING.
    It is intentionally unusable as a console/runner substitute for enrollment.
    """
    auth.windows_only()
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    main_type = ctypes.WINFUNCTYPE(None, wt.DWORD, ctypes.POINTER(wt.LPWSTR))
    handler_type = ctypes.WINFUNCTYPE(wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p)
    class Status(ctypes.Structure):
        _fields_ = [(name, wt.DWORD) for name in ("kind", "state", "controls", "exit", "specific", "checkpoint", "hint")]
    class Entry(ctypes.Structure):
        _fields_ = [("name", wt.LPWSTR), ("main", main_type)]
    @handler_type
    def handler(control, event, data, context):
        return 0 if control in (1, 4, 5) else 120
    @main_type
    def service_main(argc, argv):
        token = broker._api(adv, "RegisterServiceCtrlHandlerExW",
            [wt.LPCWSTR, handler_type, ctypes.c_void_p], wt.HANDLE)(broker.SERVICE, handler, None)
        if not token:
            return
        # Even an SCM invocation is NOT proof of enrolled source/signers or a
        # working authenticated transport. Remain stopped and fail closed.
        status = Status(0x10, 1, 0, 1066, 1, 0, 0)
        broker._api(adv, "SetServiceStatus", [wt.HANDLE, ctypes.POINTER(Status)], wt.BOOL)(token, ctypes.byref(status))
    table = (Entry * 2)(Entry(broker.SERVICE, service_main), Entry(None, main_type()))
    if not broker._api(adv, "StartServiceCtrlDispatcherW", [ctypes.POINTER(Entry)], wt.BOOL)(table):
        deny()


@dataclass(frozen=True)
class HostOpsStatusPreflightReport:
    """SOURCE-ONLY advisory projection; NEVER authenticated native readiness."""
    schema_version: str = "HostOpsStatusPreflight.v1"
    service_installed: str = "SERVICE_NOT_INSTALLED"
    signer_enrolled: str = "SIGNER_NOT_ENROLLED"
    desktop_observer_enrolled: str = "DESKTOP_OBSERVER_NOT_ENROLLED"
    loopback_9444_ownership: str = "LOOPBACK_9444_UNVERIFIED"
    chatgpt_account_session: str = "ACCOUNT_UNPROVEN"
    route_generation: str = "ROUTE_GENERATION_UNVERIFIED"
    complete_search_scope: str = "SEARCH_COVERAGE_UNPROVEN"
    overall_disposition: str = "OWNER_ESCALATION"
    authority_scope: str = "SOURCE_ONLY_ADVISORY"
    missing_owners: tuple[str, ...] = (
        "WAKEHUB_SERVICE_INSTALLER",
        "WAKEHUB_SIGNER_ENROLLMENT",
        "CHATGPT_ACCOUNT_SESSION_AUTHORITY",
        "COMPLETE_SEARCH_DOMAIN_WITNESS",
    )
    required_human_grants: tuple[str, ...] = (
        "HUMAN_PER_EFFECT_GRANT_SERVICE_INSTALL",
        "HUMAN_PER_EFFECT_GRANT_SIGNER_ENROLLMENT",
        "HUMAN_PER_EFFECT_GRANT_CDP_ATTACH",
    )

    def to_sanitized_dict(self) -> dict:
        """Sanitized fixed statuses ONLY: no PID, path, full URL, cookie, marker, or secrets."""
        return {
            "schema_version": self.schema_version,
            "service_installed": self.service_installed,
            "signer_enrolled": self.signer_enrolled,
            "desktop_observer_enrolled": self.desktop_observer_enrolled,
            "loopback_9444_ownership": self.loopback_9444_ownership,
            "chatgpt_account_session": self.chatgpt_account_session,
            "route_generation": self.route_generation,
            "complete_search_scope": self.complete_search_scope,
            "overall_disposition": "OWNER_ESCALATION",
            "authority_scope": "SOURCE_ONLY_ADVISORY",
            "missing_owners": list(self.missing_owners),
            "required_human_grants": list(self.required_human_grants),
        }


def assess_hostops_status_preflight(*,
                                    service_state: str = "NOT_INSTALLED",
                                    signer_state: str = "NOT_ENROLLED",
                                    desktop_state: str = "NOT_ENROLLED",
                                    cdp_port_owner: str = "UNVERIFIED",
                                    chat_account_state: str = "UNPROVEN",
                                    route_generation_state: str = "UNVERIFIED",
                                    search_scope_state: str = "UNPROVEN") -> HostOpsStatusPreflightReport:
    """Project caller-reported facets only. No SCM/signer/ChatGPT authentication.

    Positive input strings do not constitute native evidence, readiness or
    permission to install, attach, navigate or Send. Always require owner.
    """
    svc = (
        "SERVICE_INSTALLED" if service_state == "INSTALLED" else
        "SERVICE_STOPPED" if service_state == "STOPPED" else
        "SERVICE_NOT_INSTALLED"
    )
    signer = "SIGNER_ENROLLED" if signer_state == "ENROLLED" else "SIGNER_NOT_ENROLLED"
    desktop = "DESKTOP_OBSERVER_ENROLLED" if desktop_state == "ENROLLED" else "DESKTOP_OBSERVER_NOT_ENROLLED"
    cdp = (
        "OWNED_CDP_9444" if cdp_port_owner == "OWNED_CDP_9444" else
        "LOOPBACK_9444_CONFLICT" if cdp_port_owner == "CONFLICT" else
        "LOOPBACK_9444_ABSENT" if cdp_port_owner == "ABSENT" else
        "LOOPBACK_9444_UNVERIFIED"
    )
    acct = (
        "ACCOUNT_SESSION_AUTHENTICATED" if chat_account_state == "AUTHENTICATED" else
        "ACCOUNT_SESSION_MISMATCH" if chat_account_state == "MISMATCH" else
        "ACCOUNT_UNPROVEN"
    )
    gen = (
        "ROUTE_GENERATION_CURRENT" if route_generation_state == "CURRENT" else
        "ROUTE_GENERATION_STALE" if route_generation_state == "STALE" else
        "ROUTE_GENERATION_UNVERIFIED"
    )
    scope = (
        "SEARCH_SCOPE_COMPLETE" if search_scope_state == "COMPLETE" else
        "SEARCH_COVERAGE_UNPROVEN"
    )

    missing = []
    if svc != "SERVICE_INSTALLED":
        missing.append("WAKEHUB_SERVICE_INSTALLER")
    if signer != "SIGNER_ENROLLED":
        missing.append("WAKEHUB_SIGNER_ENROLLMENT")
    if acct != "ACCOUNT_SESSION_AUTHENTICATED":
        missing.append("CHATGPT_ACCOUNT_SESSION_AUTHORITY")
    if scope != "SEARCH_SCOPE_COMPLETE":
        missing.append("COMPLETE_SEARCH_DOMAIN_WITNESS")

    ready = (
        svc == "SERVICE_INSTALLED" and signer == "SIGNER_ENROLLED" and
        desktop == "DESKTOP_OBSERVER_ENROLLED" and cdp == "OWNED_CDP_9444" and
        acct == "ACCOUNT_SESSION_AUTHENTICATED" and gen == "ROUTE_GENERATION_CURRENT" and
        scope == "SEARCH_SCOPE_COMPLETE"
    )
    # A caller may supply every positive string; without independently
    # authenticated private receipts these remain claims, NOT a READY gate.
    disposition = "OWNER_ESCALATION"

    missing.extend(("WAKEHUB_G3_G4_SERVICE_PACKAGING_OWNER",
                    "CHATGPT_ACCOUNT_SESSION_AUTHORITY",
                    "COMPLETE_SEARCH_DOMAIN_WITNESS"))

    return HostOpsStatusPreflightReport(
        service_installed=svc,
        signer_enrolled=signer,
        desktop_observer_enrolled=desktop,
        loopback_9444_ownership=cdp,
        chatgpt_account_session=acct,
        route_generation=gen,
        complete_search_scope=scope,
        overall_disposition=disposition,
        missing_owners=tuple(dict.fromkeys(missing)),
    )


@dataclass(frozen=True)
class G3PartialStatusEvidence:
    """Retained source-level partial STATUS evidence without enabling ORIGIN_ATTESTED."""
    schema_version: str = "WakeHubG3PartialStatusEvidence.v1"
    os_identity: str = "UNVERIFIED"
    challenge_nonce: str = ""
    logon_id: str = ""
    session_id: int = 0
    disposition: str = "OWNER_ESCALATION"
    complete_search_proven: bool = False
    origin_attested: bool = False
    missing_owners: tuple[str, ...] = (
        "CHATGPT_ACCOUNT_SESSION_AUTHORITY",
        "COMPLETE_SEARCH_DOMAIN_WITNESS",
        "WAKEHUB_G3_G4_SERVICE_PACKAGING_OWNER",
    )

    def to_sanitized_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "os_identity": "UNVERIFIED",
            "disposition": "OWNER_ESCALATION",
            "complete_search_proven": False,
            "origin_attested": False,
            "missing_owners": list(self.missing_owners),
        }


def sign_broker_challenge(handle: int, challenge: observer.Challenge, *,
                          deadline: observer._StatusDeadline | None = None,
                          clock=time.time) -> bytes:
    """Service-only broker challenge signer; never shares keys with observer/operator.

    Re-authenticates kernel pipe endpoints, Session 0 service primary token SID,
    live SCM process, image hashes, protected DACLs, ServiceCustody on the
    enrolled private key, and the five-second challenge nonce before signing.
    Returns only the short-lived canonical signed challenge payload.
    """
    auth.windows_only()
    if type(handle) is not int or handle <= 0 or type(challenge) is not observer.Challenge:
        deny()
    if challenge.request_sha256 is None:
        deny()
    if deadline is not None and type(deadline) is not observer._StatusDeadline:
        deny()
    anchor, config, server, client, source, target = _context(handle)
    current = broker.read_process_identity(os.getpid())
    if current != server or server.user_sid != broker.service_sid() or server.session_id != 0:
        deny()
    if ((challenge.source, challenge.generation, challenge.journal_id, challenge.server,
            challenge.client, challenge.target, challenge.logon_id)
            != (source, config["generation"], config["journal_id"], server, client, target,
                config["logon_id"])):
        deny()
    now = clock()
    challenge.fresh(now)
    remaining = challenge.expires_at - now
    if deadline is not None:
        remaining = min(remaining, deadline.check())
    if not 0 < remaining <= 5.0:
        deny()
    key_path = broker.ServiceCustody().check(anchor["private_key_path"])
    verifier_path = WindowsPathCustody().check(anchor["authority"]["verifier"]["path"])
    signers_path = WindowsPathCustody().check(config["broker_signers"]["path"])
    body = challenge.body()
    signed = broker.sign_with_enrolled_key(
        auth.signing_bytes(body),
        key_path=key_path,
        verifier_path=verifier_path,
        signers_path=signers_path,
        identity=observer.BROKER_IDENTITY,
        namespace=observer.CHALLENGE_NAMESPACE,
        expected_body=body,
        timeout=remaining,
    )
    # Re-authenticate pipe/SCM/pins and freshness after signing.
    after_anchor, after_config, after_server, after_client, after_source, after_target = _context(handle)
    if (auth._digest(canonical([after_anchor, after_config])) != auth._digest(canonical([anchor, config]))
            or (after_server, after_client, after_source, after_target) != (server, client, source, target)):
        deny()
    challenge.fresh(clock())
    if deadline is not None:
        deadline.check()
    return signed


class BrokerStatusOwner:
    """Concrete Windows-native service-side STATUS owner on a connected HANDLE.

    Wires ConnectedStatusIO and service-only broker challenge signing into one
    single-shot, bounded five-second handoff. Never installs a service, opens a
    listener, retries an UNKNOWN reservation, or touches interactive GUI.
    """
    def __init__(self, *, clock=time.time, monotonic=time.monotonic):
        self._io = ConnectedStatusIO(clock=clock, monotonic=monotonic)
        self._lock = threading.Lock()
        self._used = False

    def execute(self, handle: int, signed_request: bytes) -> op.PublicReceipt:
        if not self._lock.acquire(blocking=False):
            return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.DUPLICATE)
        try:
            if self._used:
                return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.DUPLICATE)
            self._used = True
            reserved = False
            try:
                challenge = self._io.begin(handle, signed_request)
                reserved = True
                signed_challenge = sign_broker_challenge(
                    handle,
                    challenge,
                    deadline=self._io._exchange._deadline,
                    clock=self._io._exchange.clock,
                )
                return self._io.exchange(signed_challenge)
            except Exception:
                if reserved:
                    self._io.abandon()
                    return _unknown()
                return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.AUTHORITY_REQUIRED)
        finally:
            self._lock.release()

    def abandon(self) -> op.PublicReceipt:
        with self._lock:
            self._used = True
            return self._io.abandon()


def main(argv: list[str] | None = None) -> int:
    """Windows-callable fail-closed service host CLI entrypoint."""
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.wakehub.g3_service_host")
    parser.add_argument("--mode", choices=("preflight", "scm"), default="preflight")
    args = parser.parse_args(argv)
    if args.mode == "scm":
        try:
            staged_scm_entrypoint()
            return 0
        except Exception:
            sys.stdout.write(json.dumps(HostOpsStatusPreflightReport().to_sanitized_dict(), sort_keys=True) + "\n")
            return 2
    sys.stdout.write(json.dumps(HostOpsStatusPreflightReport().to_sanitized_dict(), sort_keys=True) + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

