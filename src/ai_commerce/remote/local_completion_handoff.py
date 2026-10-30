"""Finite operator-enrolled completion hints. No scheduler or delivery authority.

One private record holds one immutable enrollment, one selector-only hint and
one terminal observation/UNKNOWN. GitHub GET authorities, never the hint,
authenticate the request and result. D5.1 owns optional D3.2/D2/D4 delivery.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import math
import os
from pathlib import Path
import re
import stat
import time
import json

from ._private_state import PrivateJsonState, PrivateStateError, _linked, _regular
from .completion_event import build_completion_event, validate_completion_event
from .github_artifact_authority import GitHubRestArtifactAuthority
from .github_read_retry import GitHubReadBudget
from .local_run_registration import (
    REPOSITORY, RegistrationIntent, IssueRunRegistrar, _record_valid, _timestamp,
)
from .local_wake_entry import _windows_process_sid, send_one
from .local_wake_trigger import GitHubExactRunReader, _secret_inputs
from .validation import fingerprint
from .wake_adapter import GitHubArtifactProvenance

_CODE_ROOT = Path(__file__).resolve().parents[3]
_EVENT = re.compile(r"ef02-completion-v1:[0-9a-f]{64}\Z")
_PRINCIPAL = re.compile(r"(?:S-1-(?:\d+-){1,14}\d+|uid:\d+)\Z")
_ENROLLMENT = ("intent", "run_id", "operator_principal", "brain_issuers",
               "mode", "enrolled_at", "expires_at")
_FIELDS = {*_ENROLLMENT, "version", "enrollment_fingerprint", "phase", "hint",
           "completed_at", "registration", "live_wake_status",
           "chat_delivery_proven", "brain_continuation_proven"}


class HandoffBlocked(ValueError):
    """Only fixed diagnostic codes may cross this boundary."""


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _on_windows():
    return os.name == "nt"


def _check_windows_acl(owner, aces, principal):
    # SYSTEM and local Administrators are the trusted OS administration boundary.
    # Every other allow ACE, including a read-only group ACE, is refused.
    # OWNER RIGHTS (S-1-3-4) refers to this object's independently checked owner.
    trusted = {principal, "S-1-5-18", "S-1-5-32-544", "S-1-3-4"}
    if (owner != principal or not aces or len(aces) > 128 or
            not any(kind == "allow" and sid == principal for kind, sid in aces) or
            any(kind not in {"allow", "deny"} or
                (kind == "allow" and sid not in trusted) for kind, sid in aces)):
        raise HandoffBlocked("HANDOFF_PERMISSIONS_INVALID")


def _windows_acl(path):
    """Inspect native owner/DACL without a shell, username lookup or ACL changes."""
    import ctypes
    from ctypes import wintypes as w

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, w.DWORD, w.DWORD,
                                        ctypes.POINTER(ptr), ptr, ctypes.POINTER(ptr),
                                        ptr, ctypes.POINTER(ptr)]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    adv.GetAclInformation.argtypes = [ptr, ptr, w.DWORD, ctypes.c_int]
    adv.GetAclInformation.restype = w.BOOL
    adv.GetAce.argtypes = [ptr, w.DWORD, ctypes.POINTER(ptr)]
    adv.GetAce.restype = w.BOOL
    adv.IsValidSid.argtypes = [ptr]
    adv.IsValidSid.restype = w.BOOL
    adv.GetLengthSid.argtypes = [ptr]
    adv.GetLengthSid.restype = w.DWORD
    kernel.LocalFree.argtypes = [ptr]
    kernel.LocalFree.restype = ptr

    def sid_text(sid):
        text = w.LPWSTR()
        if not sid or not adv.IsValidSid(sid) or not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise HandoffBlocked("HANDOFF_PERMISSIONS_UNAVAILABLE")
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ptr))

    owner, dacl, descriptor = ptr(), ptr(), ptr()
    if adv.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                ctypes.byref(dacl), None, ctypes.byref(descriptor)):
        raise HandoffBlocked("HANDOFF_PERMISSIONS_UNAVAILABLE")
    try:
        # NULL DACL means everyone has full access.
        if not dacl:
            raise HandoffBlocked("HANDOFF_PERMISSIONS_INVALID")
        info = (w.DWORD * 3)()
        if not adv.GetAclInformation(dacl, info, ctypes.sizeof(info), 2) or not 1 <= info[0] <= 128:
            raise HandoffBlocked("HANDOFF_PERMISSIONS_INVALID")
        aces = []
        for index in range(info[0]):
            ace = ptr()
            if not adv.GetAce(dacl, index, ctypes.byref(ace)):
                raise HandoffBlocked("HANDOFF_PERMISSIONS_UNAVAILABLE")
            header = ctypes.string_at(ace, 4)
            size = int.from_bytes(header[2:4], "little")
            if header[0] not in (0, 1) or size < 16:
                raise HandoffBlocked("HANDOFF_PERMISSIONS_INVALID")
            sid = ptr(ace.value + 8)
            if not adv.IsValidSid(sid) or adv.GetLengthSid(sid) > size - 8:
                raise HandoffBlocked("HANDOFF_PERMISSIONS_INVALID")
            aces.append(("allow" if header[0] == 0 else "deny", sid_text(sid)))
        return sid_text(owner), tuple(aces)
    finally:
        kernel.LocalFree(descriptor)


def _private_identity(path):
    info = path.lstat()
    if _linked(info) or not (stat.S_ISDIR(info.st_mode) or _regular(info)):
        raise HandoffBlocked("HANDOFF_PATH_UNSAFE")
    if os.name == "nt":
        principal = _windows_process_sid()
        _check_windows_acl(*_windows_acl(path), principal)
        return principal
    if os.name == "posix" and hasattr(os, "geteuid"):
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise HandoffBlocked("HANDOFF_PERMISSIONS_INVALID")
        return "uid:" + str(os.geteuid())
    raise HandoffBlocked("HANDOFF_PERMISSIONS_UNAVAILABLE")


def _interactive_sid():
    """Reject service/session-zero and unavailable interactive desktops."""
    if os.name != "nt" or os.environ.get("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED") != "true":
        raise HandoffBlocked("LOCAL_SEND_GATE_CLOSED")
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel.GetCurrentProcessId.restype = w.DWORD
    kernel.ProcessIdToSessionId.argtypes = [w.DWORD, ctypes.POINTER(w.DWORD)]
    kernel.ProcessIdToSessionId.restype = w.BOOL
    user.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    user.OpenInputDesktop.restype = w.HANDLE
    user.CloseDesktop.argtypes = [w.HANDLE]
    user.CloseDesktop.restype = w.BOOL
    session = w.DWORD()
    if not kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(), ctypes.byref(session)) or not session.value:
        raise HandoffBlocked("WINDOWS_INTERACTIVE_REQUIRED")
    desktop = user.OpenInputDesktop(0, False, 1)
    if not desktop:
        raise HandoffBlocked("WINDOWS_INTERACTIVE_REQUIRED")
    user.CloseDesktop(desktop)
    return _windows_process_sid()


def _hint(record, event_id):
    intent = record["intent"]
    return {**{k: intent[k] for k in ("repository", "issue_number", "batch_id",
                                     "source_sha", "workflow_id", "run_attempt")},
            "run_id": record["run_id"], "event_id": event_id}


def _validate(record):
    try:
        if not isinstance(record, dict) or set(record) != _FIELDS:
            raise ValueError
        intent = RegistrationIntent(**record["intent"])
        intent.target(record["run_id"])  # Strict positive ints; bool is not an ID.
        if (type(record["version"]) is not int or record["version"] != 1 or
                not isinstance(record["operator_principal"], str) or
                not _PRINCIPAL.fullmatch(record["operator_principal"]) or
                type(record["brain_issuers"]) is not list or len(record["brain_issuers"]) > 32 or
                any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?", v)
                    for v in record["brain_issuers"]) or
                record["mode"] not in ("observe", "deliver") or
                not _number(record["enrolled_at"]) or
                not 0 < _timestamp(record["expires_at"]) - record["enrolled_at"] <= 3600 or
                record["enrollment_fingerprint"] != fingerprint({k: record[k] for k in _ENROLLMENT}) or
                record["live_wake_status"] != "LIVE_WAKE_NOT_PROVEN" or
                record["chat_delivery_proven"] is not False or
                record["brain_continuation_proven"] is not False or
                record["phase"] not in ("ARMED", "SIGNALED", "OBSERVED", "UNKNOWN")):
            raise ValueError
        if record["phase"] == "ARMED":
            if any(record[k] is not None for k in ("hint", "completed_at", "registration")):
                raise ValueError
        else:
            hint = record["hint"]
            if (not isinstance(hint, dict) or
                    not isinstance(hint.get("event_id"), str) or not _EVENT.fullmatch(hint["event_id"]) or
                    fingerprint(hint) != fingerprint(_hint(record, hint["event_id"])) or
                    not _number(record["completed_at"]) or
                    not record["enrolled_at"] < record["completed_at"] < _timestamp(record["expires_at"])):
                raise ValueError
            saved = record["registration"]
            if saved is not None:
                _record_valid(saved, intent)
                if saved["run_id"] != record["run_id"] or saved["event_id"] != hint["event_id"]:
                    raise ValueError
            if (record["phase"] in ("OBSERVED", "UNKNOWN")) != (saved is not None):
                raise ValueError
            if record["phase"] == "UNKNOWN" and record["mode"] != "deliver":
                raise ValueError
        return record
    except Exception:
        raise HandoffBlocked("HANDOFF_STATE_INVALID") from None


class HandoffStore:
    """One bounded operator-provisioned record; never create its directory."""

    def __init__(self, path: Path, repository_root: Path):
        self.repository_root = Path(repository_root)
        PrivateJsonState(path, _CODE_ROOT, label="HANDOFF", max_bytes=16384)
        self.state = PrivateJsonState(path, self.repository_root, label="HANDOFF", max_bytes=16384)
        if self.state.path.name.endswith((".lock", ".part")):
            raise HandoffBlocked("HANDOFF_LOCATION_INVALID")
        self._permissions()

    def _permissions(self):
        principal = _private_identity(self.state.path.parent)
        for path in (self.state.path, self.state.path.with_name(self.state.path.name + ".lock")):
            if path.exists() or path.is_symlink():
                if _private_identity(path) != principal:
                    raise HandoffBlocked("HANDOFF_OWNER_MISMATCH")
        return principal

    @contextmanager
    def locked(self, *, wait_seconds=5):
        principal = self._permissions()
        with self.state.locked(wait_seconds=wait_seconds) as transaction:
            if self._permissions() != principal:
                raise HandoffBlocked("HANDOFF_OWNER_MISMATCH")
            record = transaction.read({})
            if record == {} and not self.state.path.exists():
                record = None
            else:
                _validate(record)
                if record["operator_principal"] != principal:
                    raise HandoffBlocked("HANDOFF_OWNER_MISMATCH")
            yield transaction, record, principal


def enroll(store, *, intent, run_id, expires_at, opt_in=False, mode="observe",
           brain_issuers=(), wall_clock=time.time):
    if opt_in is not True:
        raise HandoffBlocked("LITERAL_OPERATOR_OPT_IN_REQUIRED")
    intent.target(run_id)
    now = wall_clock()
    if (not _number(now) or not 0 < _timestamp(expires_at) - now <= 3600 or
            not now - 7 * 86400 <= _timestamp(intent.created_after) <
            _timestamp(intent.created_before) <= now):
        raise HandoffBlocked("HANDOFF_WINDOW_INVALID")
    with store.locked() as (transaction, current, principal):
        if mode == "deliver" and _interactive_sid() != principal:
            raise HandoffBlocked("HANDOFF_OWNER_MISMATCH")
        record = dict(version=1, intent=asdict(intent), run_id=run_id,
                      operator_principal=principal, brain_issuers=list(brain_issuers),
                      mode=mode, enrolled_at=now, expires_at=expires_at, phase="ARMED",
                      hint=None, completed_at=None, registration=None,
                      live_wake_status="LIVE_WAKE_NOT_PROVEN",
                      chat_delivery_proven=False, brain_continuation_proven=False)
        if current is not None:
            record["enrolled_at"] = current["enrolled_at"]
        record["enrollment_fingerprint"] = fingerprint({k: record[k] for k in _ENROLLMENT})
        _validate(record)
        if current is not None:
            if current["enrollment_fingerprint"] != record["enrollment_fingerprint"]:
                raise HandoffBlocked("HANDOFF_ENROLLMENT_CONFLICT")
            return current  # Never rearm or extend an existing record.
        transaction.write(record)
        return record


def _emit_worker_signal(*, batch, event, report, repository_root, issuer_login,
                        completed_at, environ, wall_clock=time.time):
    """One local atomic write; a busy OS lock drops the optional hint."""
    if not _on_windows() or environ.get("AI_COMMERCE_LOCAL_HANDOFF_ENABLED") != "true":
        return
    path = environ.get("AI_COMMERCE_LOCAL_HANDOFF_STATE", "")
    if not path or len(path) > 4096:
        return
    event = validate_completion_event(event)
    if (environ.get("GITHUB_REPOSITORY") != REPOSITORY or
            environ.get("GITHUB_EVENT_NAME") != "issues" or
            not environ.get("GITHUB_WORKFLOW_REF", "").startswith(
                REPOSITORY + "/.github/workflows/ef02-batch.yml@refs/") or
            environ.get("GITHUB_RUN_ID") != str(event["run_id"]) or
            environ.get("GITHUB_RUN_ATTEMPT") != str(event["run_attempt"]) or
            build_completion_event(report, repository=REPOSITORY, run_id=event["run_id"],
                run_attempt=event["run_attempt"], issue_number=event["issue_number"]) != event or
            report.get("wake_event_status") != "RECORDED_UNBOUND" or
            report.get("wake_event_id") != event["event_id"] or
            report.get("github_issue_number") != event["issue_number"] or
            str(report.get("github_workflow_run_id")) != str(event["run_id"])):
        return
    store = HandoffStore(Path(path), repository_root)
    with store.locked(wait_seconds=0) as (transaction, record, _):
        now = wall_clock()
        if (record is None or not _number(now) or not _number(completed_at) or
                not record["enrolled_at"] < completed_at <= now < _timestamp(record["expires_at"])):
            return
        intent = record["intent"]
        if (intent["issuer_login"] != issuer_login or intent["actor"] != batch.get("actor") or
                intent["request_fingerprint"] != fingerprint(batch)):
            return
        hint = _hint(record, event["event_id"])
        if any(event.get(k) != hint[k] for k in
               ("repository", "issue_number", "run_id", "run_attempt", "batch_id", "source_sha")):
            return
        if record["phase"] != "ARMED":
            return  # Duplicate/conflicting signals cannot reset any state.
        record.update(phase="SIGNALED", hint=hint, completed_at=completed_at)
        transaction.write(_validate(record))


def start_worker_signal(**options):
    """No lock wait, retries, GETs, browser, or changes to the EF02 result."""
    if not _on_windows() or os.environ.get("AI_COMMERCE_LOCAL_HANDOFF_ENABLED") != "true":
        return
    # Copy only selector environment entries, never the worker's token/key env.
    names = ("AI_COMMERCE_LOCAL_HANDOFF_ENABLED", "AI_COMMERCE_LOCAL_HANDOFF_STATE",
             "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW_REF",
             "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    environ = {k: os.environ.get(k, "") for k in names}

    try:
        _emit_worker_signal(**options, environ=environ)
    except Exception:
        pass  # Never log private paths/payloads or affect the real result.


class _Registration:
    """D5.3 store adapter under the already-held handoff transaction lock."""
    def __init__(self, record):
        self.record = record

    def read(self, intent):
        if fingerprint(asdict(intent)) != fingerprint(self.record["intent"]):
            raise HandoffBlocked("HANDOFF_ENROLLMENT_CONFLICT")
        return self.record["registration"]

    def register(self, intent, record):
        _record_valid(record, intent)
        saved = self.read(intent)
        if saved is not None and saved != record:
            raise HandoffBlocked("HANDOFF_PROVENANCE_CHANGED")
        self.record["registration"] = record
        return record


def _result(status, code, *, registered=False):
    return dict(status=status, error_code=code,
                registration_status="REGISTERED" if registered else "NOT_REGISTERED",
                live_wake_status="LIVE_WAKE_NOT_PROVEN",
                chat_delivery_proven=False, brain_continuation_proven=False)


def _delivery_inputs(store, principal, delivery):
    if _interactive_sid() != principal:
        raise HandoffBlocked("HANDOFF_OWNER_MISMATCH")
    if (not isinstance(delivery, dict) or
            set(delivery) != {"registry", "idempotency", "grants", "caller_id"} or
            not isinstance(delivery["caller_id"], str) or
            any(not isinstance(delivery[k], (str, Path)) for k in ("registry", "idempotency", "grants")) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", delivery["caller_id"])):
        raise HandoffBlocked("LOCAL_SEND_CONFIG_INVALID")
    occupied = {store.state.path, store.state.path.with_name(store.state.path.name + ".lock")}
    for key in ("registry", "idempotency", "grants"):
        path = Path(delivery[key])
        private = HandoffStore(path, store.repository_root)
        if private._permissions() != principal:
            raise HandoffBlocked("HANDOFF_OWNER_MISMATCH")
        pair = {path, path.with_name(path.name + ".lock")}
        if pair & occupied:
            raise HandoffBlocked("LOCAL_STATE_PATH_CONFLICT")
        occupied |= pair
    return _secret_inputs()


def listen(store, *, deliver=False, delivery=None, max_polls=120, interval_seconds=5,
           max_seconds=600, max_reads=200, read_attempts=3, reader_factory=None,
           sender=None, wall_clock=time.time, clock=time.monotonic, sleep=time.sleep):
    """Finite session, default observe. No signal/enrollment means zero GETs."""
    if (type(deliver) is not bool or type(max_polls) is not int or not 1 <= max_polls <= 120 or
            type(interval_seconds) is not int or not 1 <= interval_seconds <= 600):
        raise HandoffBlocked("HANDOFF_POLL_BUDGET_INVALID")
    budget = GitHubReadBudget(max_seconds=max_seconds, max_reads=max_reads,
                             max_attempts=read_attempts, clock=clock,
                             wall_clock=wall_clock, sleep=sleep)
    reader = None
    for index in range(max_polls):
        # The lock covers evidence authentication through terminal persistence/
        # handoff. Concurrent/restarted listeners cannot pass the send boundary.
        with store.locked() as (transaction, record, principal):
            budget.timeout()
            if record is None:
                return _result("UNPROVEN", "NO_ENROLLMENT")
            if record["phase"] in ("OBSERVED", "UNKNOWN"):
                return _result("UNKNOWN" if record["phase"] == "UNKNOWN" else
                               "CANDIDATE_READY_NOT_SENT", "HANDOFF_ALREADY_CONSUMED", registered=True)
            now = wall_clock()
            if not _number(now) or not record["enrolled_at"] <= now < _timestamp(record["expires_at"]):
                raise HandoffBlocked("HANDOFF_WINDOW_CLOSED")
            budget.deadline = min(budget.deadline, clock() + _timestamp(record["expires_at"]) - now)
            if record["phase"] == "SIGNALED":
                if now < record["completed_at"]:
                    raise HandoffBlocked("HANDOFF_SIGNAL_OUT_OF_ORDER")
                if deliver:
                    if record["mode"] != "deliver":
                        raise HandoffBlocked("LOCAL_DELIVERY_NOT_ENROLLED")
                    token, key = _delivery_inputs(store, principal, delivery)
                if reader is None:
                    reader = (reader_factory(budget) if reader_factory else GitHubExactRunReader(
                        token_provider=lambda: os.environ.get("AI_COMMERCE_GITHUB_READ_TOKEN", ""),
                        read_budget=budget))
                intent = RegistrationIntent(**record["intent"])
                registrar = IssueRunRegistrar(reader=reader, intent=intent,
                    store=_Registration(record), repository_root=store.repository_root,
                    brain_issuers=tuple(record["brain_issuers"]), wall_clock=wall_clock)
                selected = registrar.resolve(run_id=record["run_id"])
                if selected is not None:
                    target, event, report = selected
                    if _hint(record, event["event_id"]) != record["hint"]:
                        raise HandoffBlocked("HANDOFF_HINT_MISMATCH")
                    authority = GitHubRestArtifactAuthority(repository=REPOSITORY,
                        workflow_id=target.workflow_id, token_provider=reader.credential, read_budget=budget)
                    provenance = GitHubArtifactProvenance(repository=REPOSITORY,
                        run_id=target.run_id, run_attempt=target.run_attempt,
                        artifact_name=f"ef02-results-{target.run_id}-{target.run_attempt}",
                        workflow_run_status="completed")
                    if authority.verify_artifact(provenance, event=event, batch_result=report) is not True:
                        raise HandoffBlocked("HANDOFF_PROVENANCE_UNVERIFIED")
                    budget.timeout()
                    if not record["enrolled_at"] <= wall_clock() < _timestamp(record["expires_at"]):
                        raise HandoffBlocked("HANDOFF_WINDOW_CLOSED")
                    if deliver:
                        # Recheck the interactive/environment gate immediately
                        # before durable claim; send_one independently checks
                        # both signed grants, artifact authority, D2 and D4.
                        token, key = _delivery_inputs(store, principal, delivery)
                    record["phase"] = "UNKNOWN" if deliver else "OBSERVED"
                    transaction.write(_validate(record))
                    if not deliver:
                        return _result("CANDIDATE_READY_NOT_SENT", "OBSERVED", registered=True)
                    try:
                        (sender or send_one)(event=event, report=report, workflow_id=target.workflow_id,
                            caller_id=delivery["caller_id"], grants_path=Path(delivery["grants"]),
                            registry_path=Path(delivery["registry"]),
                            idempotency_path=Path(delivery["idempotency"]),
                            repository_root=store.repository_root, token_provider=lambda: token,
                            secret_provider=lambda: key, principal_provider=lambda: principal,
                            read_budget=budget)
                    except Exception:
                        pass  # UNKNOWN is already durable; never replay this call.
                    return _result("UNKNOWN", "DELIVERY_ATTEMPT_UNPROVEN", registered=True)
        if index + 1 < max_polls:
            budget.pause(interval_seconds)
    return _result("UNPROVEN", "HANDOFF_NOT_READY")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.remote.local_completion_handoff")
    parser.add_argument("command", choices=("enroll", "listen"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--opt-in", action="store_true")
    parser.add_argument("--repository", default=REPOSITORY)
    for name in ("issue-number", "run-id", "workflow-id", "run-attempt"):
        parser.add_argument("--" + name, type=int)
    for name in ("batch-id", "source-sha", "issuer-login", "actor", "request-fingerprint",
                 "created-after", "created-before", "expires-at"):
        parser.add_argument("--" + name)
    parser.add_argument("--brain-issuer", action="append", default=[])
    parser.add_argument("--allow-delivery", action="store_true")
    parser.add_argument("--deliver", action="store_true")
    for name in ("registry", "idempotency", "grants"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--caller-id")
    for name, default in (("max-polls", 120), ("interval-seconds", 5), ("max-seconds", 600),
                          ("max-reads", 200), ("read-attempts", 3)):
        parser.add_argument("--" + name, type=int, default=default)
    args = parser.parse_args(argv)
    try:
        checkout = args.checkout.resolve(strict=True)
        if not checkout.is_dir() or args.repository != REPOSITORY:
            raise HandoffBlocked("HANDOFF_LOCATION_INVALID")
        store = HandoffStore(args.state, checkout)
        if args.command == "enroll":
            if args.deliver or any((args.registry, args.idempotency, args.grants, args.caller_id)):
                raise HandoffBlocked("LOCAL_SEND_CONFIG_INVALID")
            intent = RegistrationIntent(args.repository, args.issue_number, args.batch_id,
                args.source_sha, args.workflow_id, args.run_attempt, args.issuer_login,
                args.actor, args.request_fingerprint, args.created_after, args.created_before)
            enroll(store, intent=intent, run_id=args.run_id, expires_at=args.expires_at,
                   opt_in=args.opt_in, mode="deliver" if args.allow_delivery else "observe",
                   brain_issuers=tuple(args.brain_issuer))
            result = _result("ENROLLED", "AWAITING_SIGNAL")
        else:
            if any((args.opt_in, args.allow_delivery, args.issue_number, args.run_id, args.workflow_id,
                    args.run_attempt, args.batch_id, args.source_sha, args.issuer_login, args.actor,
                    args.request_fingerprint, args.created_after, args.created_before,
                    args.expires_at, args.brain_issuer)):
                raise HandoffBlocked("HANDOFF_ENROLLMENT_CONFLICT")
            delivery = dict(registry=args.registry, idempotency=args.idempotency,
                            grants=args.grants, caller_id=args.caller_id)
            result = listen(store, deliver=args.deliver, delivery=delivery,
                max_polls=args.max_polls, interval_seconds=args.interval_seconds,
                max_seconds=args.max_seconds, max_reads=args.max_reads, read_attempts=args.read_attempts)
        print(json.dumps(result))
        return 0 if result["status"] in ("ENROLLED", "CANDIDATE_READY_NOT_SENT") else 3
    except HandoffBlocked as error:
        print(json.dumps(_result("BLOCKED", str(error))))
        return 2
    except Exception:
        # Exceptions may contain private paths, credentials or remote payloads.
        print(json.dumps(_result("BLOCKED", "LOCAL_HANDOFF_BLOCKED")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
