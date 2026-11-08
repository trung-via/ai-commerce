"""Separately instantiable private Windows binding; no import-time activity.

STATUS uses the existing fixed READ collector plus independent signed observer
evidence. RECONCILE_ONLY accepts only signed original-operation evidence. START
and attach cannot yet be secured with the allowed source closure, so both deny
BEFORE reservation/native boundary. There is no shell/URL/Issue dispatcher,
Chrome start/stop, tab navigation, Wake, plugin Send or Scheduled Task write.

OWNER_ESCALATION: separate private administrative enrollment/provisioning and
an authenticated independent observation service are required. A future exact
start/attach owner must define an atomic native lease/identity/desktop boundary
and independent outcome collection before either effect can be enabled. No
existing HostOps/D5 keys or native bridge may be repurposed for this.
"""
from __future__ import annotations

from enum import Enum
import json

from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.native_authority import WindowsGrantVerifier, windows_only
from ai_commerce.wakehub.native_journal import SQLitePrivateJournal, deny


class NativeCode(Enum):
    CUSTODY_HOLD = "HOLD_PRIVATE_ENROLLMENT_REQUIRED"
    EFFECT_BLOCKED = "BLOCKED_NATIVE_START_ATTACH_OWNER_REQUIRED"
    OPERATOR = "SOURCE_OPERATOR_RESULT"


class WindowsExecutor:
    """Fail closed even if somebody directly calls an executor method."""
    def start_dedicated(self, lease):
        deny()

    def reuse_owned(self, lease):
        deny()


class WindowsPrivateRuntime:
    """Fixed operation surface. Factory never installs/enrolls anything.

    The private factory is intentionally not an Issue, CLI, task or runner
    integration. Default construction gives a DENY runtime without OS reads.
    Only PublicReceipt / to_json's closed allowlist may leave private custody.
    """
    def __init__(self):
        self._operator = None
        self._authority = None
        self._unresolved = False

    @classmethod
    def from_local_enrollment(cls):
        runtime = cls()
        try:
            windows_only()
            authority = WindowsGrantVerifier()
            value = authority.enrolled
            runtime._unresolved = True
            journal = SQLitePrivateJournal(value["journal_path"], owner_sid=authority.target.user_sid,
                journal_id=value["journal_id"], lane=value["lane"])
            authority.journal = journal
            authority._load(authority.clock())
            runtime._operator = op.PrivateLifecycleOperator(op.LifecyclePolicy(authority.source),
                verifier=authority, journal=journal, executor=WindowsExecutor(), clock=authority.clock)
            runtime._authority = authority
            runtime._unresolved = False
        except Exception:
            pass  # No private OS errors, reprs, paths or transcript in receipts.
        return runtime

    @classmethod
    def from_service_connection(cls, pipe_handle):
        """Called only by a separately installed service, on its server handle.

        A raw pipe message, shared user SID, or source checkout is insufficient.
        No listener, service installation, key creation or GUI action occurs.
        """
        from ai_commerce.wakehub.native_broker import WindowsServiceAuthority, BrokerRequests, ServiceEffectJournal
        runtime = cls()
        runtime._broker = None
        try:
            windows_only()
            authority = WindowsServiceAuthority(pipe_handle)
            value = authority.enrolled
            runtime._unresolved = True
            journal = ServiceEffectJournal(value["journal_path"],
                owner_sid=authority.journal_owner(authority.target),
                journal_id=value["journal_id"], lane=value["lane"])
            authority.journal = journal
            authority._load(authority.clock())
            requests = BrokerRequests(authority.broker["requests_path"],
                owner_sid=authority.service_sid, journal_id=value["journal_id"])
            runtime._authority = authority
            runtime._operator = op.PrivateLifecycleOperator(op.LifecyclePolicy(authority.source),
                verifier=authority, journal=journal, executor=WindowsExecutor(), clock=authority.clock)
            runtime._broker = requests
            runtime._unresolved = False
        except Exception:
            # Partial initialization can never fall back to the desktop path.
            runtime._operator = None
            runtime._authority = None
        return runtime

    def run_service_request(self, raw):
        """Only a signed fixed broker envelope can reach service policy."""
        operation = op.Operation.STATUS
        claimed = False
        try:
            authority = self._authority
            if getattr(self, "_broker", None) is None:
                raise ValueError()
            operation, proof, nonce, digest = authority.verify_request(raw)
            # Persist UNKNOWN before dispatch. Lost ACK/timeout never retries.
            if not self._broker.claim(nonce, digest):
                return op.PublicReceipt(operation, op.Decision.HOLD, op.Reason.DUPLICATE)
            claimed = True
            authority._load(authority.clock())
            result = self._run_authenticated(operation, proof)
            self._broker.finish(nonce, digest, result)
            return result
        except Exception:
            return op.PublicReceipt(operation, op.Decision.UNKNOWN_EFFECT
                if claimed or self._unresolved else op.Decision.HOLD,
                op.Reason.RECONCILE_ONLY if claimed or self._unresolved
                else op.Reason.AUTHORITY_REQUIRED)

    def run(self, operation, proof=None):
        # Service callers must authenticate the pipe AND signed envelope first.
        if hasattr(self, "_broker"):
            return op.PublicReceipt(operation if type(operation) is op.Operation else op.Operation.STATUS,
                op.Decision.HOLD, op.Reason.AUTHORITY_REQUIRED)
        return self._run_authenticated(operation, proof)

    def _run_authenticated(self, operation, proof=None):
        if type(operation) is not op.Operation:
            return op.PublicReceipt(op.Operation.STATUS, op.Decision.HOLD, op.Reason.INVALID_REQUEST)
        if self._operator is None:
            if self._unresolved:
                return op.PublicReceipt(operation, op.Decision.UNKNOWN_EFFECT, op.Reason.RECONCILE_ONLY)
            return op.PublicReceipt(operation, op.Decision.HOLD, op.Reason.AUTHORITY_REQUIRED)
        if operation in (op.Operation.REUSE, op.Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT):
            return op.PublicReceipt(operation, op.Decision.HOLD, op.Reason.AUTHORITY_REQUIRED)
        if operation is op.Operation.RECONCILE_ONLY:
            return self._reconcile(proof)
        return self._operator.run(operation, proof)

    def _reconcile(self, proof):
        # The existing protocol requires identical SourceBinding on an old
        # intent. This concrete binding authenticates the CURRENT release and
        # independently signed ORIGINAL lease, allowing safe reconciliation
        # across release/session changes without relaxing source authentication.
        operation = op.Operation.RECONCILE_ONLY
        try:
            authority = self._authority
            now = authority.clock()
            grant = authority.verify_grant(proof, operation, op.LifecyclePolicy(authority.source), now)
            if grant is None:
                return op.PublicReceipt(operation, op.Decision.HOLD, op.Reason.AUTHORITY_REQUIRED)
            record = authority.journal.read(grant)
            observation = authority.reconcile(grant, record, now)
            if observation is None:
                return op.PublicReceipt(operation, op.Decision.HOLD, op.Reason.RECONCILE_ONLY)
            confirmed = authority.journal.read(grant)
            if confirmed.state is not op.JournalState.OBSERVED or confirmed.observation != observation:
                return op.PublicReceipt(operation, op.Decision.UNKNOWN_EFFECT, op.Reason.RECONCILE_ONLY)
            return op.PublicReceipt(operation, op.Decision.RECONCILED, op.Reason.OBSERVED)
        except Exception:
            return op.PublicReceipt(operation, op.Decision.HOLD, op.Reason.RECONCILE_ONLY)

    def to_json(self, operation, proof=None):
        receipt = self.run(operation, proof)
        code = (NativeCode.CUSTODY_HOLD if self._operator is None else NativeCode.EFFECT_BLOCKED
                if operation in (op.Operation.REUSE, op.Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT)
                else NativeCode.OPERATOR)
        return json.dumps({"schema_version": "WakeHubG2NativePublic.v1", "code": code.value,
            "receipt": json.loads(receipt.to_json()), "source": "SOURCE_ONLY",
            "signer_enrollment": "NOT_PROVEN_BY_SOURCE_OR_OFFLINE_TESTS",
            "native_effect": "NOT_ATTEMPTED", "logon_task": "NOT_INSTALLED", "wake": "NOT_SENT"},
            sort_keys=True, separators=(",", ":"))
