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

from dataclasses import dataclass
from enum import Enum
import json
import ntpath
from pathlib import Path

from ai_commerce.remote import chrome_acquisition as acquisition
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.native_authority import WindowsGrantVerifier, windows_only
from ai_commerce.wakehub.native_journal import SQLitePrivateJournal, deny


class NativeCode(Enum):
    CUSTODY_HOLD = "HOLD_PRIVATE_ENROLLMENT_REQUIRED"
    EFFECT_BLOCKED = "BLOCKED_NATIVE_START_ATTACH_OWNER_REQUIRED"
    OPERATOR = "SOURCE_OPERATOR_RESULT"


class Native9444Code(Enum):
    OWNER_REQUIRED = "OWNER_ESCALATION_N3B2_PROTECTED_DESKTOP_EXECUTOR_REQUIRED"
    DESKTOP_TRANSPORT_REQUIRED = "OWNER_ESCALATION_N3B2_AUTHENTICATED_DESKTOP_TRANSPORT_REQUIRED"
    AUTHORITY_REQUIRED = "HOLD_G2_SIGNED_AUTHORITY_REQUIRED"
    INVALID_REQUEST = "HOLD_INVALID_9444_PREFLIGHT"
    EVIDENCE_GAP = "HOLD_INDEPENDENT_9444_EVIDENCE_REQUIRED"
    ADVISORY_HOLD = "HOLD_N3A_9444_ADVISORY"
    TARGET_MISMATCH = "HOLD_EXACT_CONFIG_OWNER_MISMATCH"
    RECONCILE_ONLY = "HOLD_DURABLE_LANE_RECONCILE_ONLY"
    DUPLICATE = "HOLD_CONSUMED_9444_GRANT"


@dataclass(frozen=True, slots=True, repr=False)
class Native9444Contract:
    """Private SOURCE_VERIFIED routing description, never a native capability.

    Selectors come only from verified G2 enrollment and the current fixed N3-A
    config. A future N3-B2 owner must reserve this grant in the EXISTING journal,
    authenticate the returned PrivateLease, and atomically recheck revocation,
    pause/receiver, complete absence (START) or exact process (REUSE), config
    custody, signed executable and non-elevated unlocked owner token/session.
    That owner needs an independently enrolled protected desktop transport and
    independent durable outcome collection. Session0 cannot execute the route.
    No caller-constructed contract/lease, matching SID or test attestation can
    establish that enrollment. This object is not accepted by any effect path.
    """
    grant: op.PrivateGrant
    environment: acquisition.ChromeEnvironment


@dataclass(frozen=True, slots=True, repr=False)
class Native9444Preflight:
    operation: op.Operation
    code: Native9444Code
    reason: op.Reason
    advisory: str = "ABSENCE_UNPROVEN"
    contract: Native9444Contract | None = None

    def to_json(self):
        # Deliberately omit the private contract, PID, SID, config and proof.
        if (type(self.code) is not Native9444Code
                or (self.contract is not None and type(self.contract) is not Native9444Contract)):
            raise ValueError("WAKEHUB_9444_PUBLIC_INPUT_INVALID")
        advisory = acquisition._logon_result(self.advisory)["code"]
        receipt = op.PublicReceipt(self.operation, op.Decision.UNKNOWN_EFFECT
            if self.code is Native9444Code.RECONCILE_ONLY or advisory == "UNKNOWN_EFFECT"
            else op.Decision.HOLD, self.reason)
        return json.dumps({"schema_version": "WakeHubN3B9444PreflightPublic.v1",
            "code": self.code.value, "receipt": json.loads(receipt.to_json()),
            "advisory": advisory,
            "source_validation": "SOURCE_VERIFIED" if self.contract is not None else "NOT_VERIFIED",
            "signer_enrollment": "NOT_PROVEN_BY_SOURCE_OR_OFFLINE_TESTS",
            "desktop_executor": "NOT_ENROLLED_BY_SOURCE",
            "lease": "NOT_RESERVED_BY_PREFLIGHT", "native_effect": "NOT_ATTEMPTED"},
            sort_keys=True, separators=(",", ":"))


class WindowsExecutor:
    """Fail closed even if somebody directly calls an executor method."""
    def start_dedicated(self, lease: op.PrivateLease):
        deny()

    def reuse_owned(self, lease: op.PrivateLease):
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

    def preflight_9444(self, checkout: Path, operation: op.Operation,
                       proof: op.PrivateGrantProof | None = None) -> Native9444Preflight:
        """Private read-only N3-B1 contract; never dispatch or reserve an effect.

        This is separate from run/service requests and the hard-denied N3-A seam.
        Reuse G2 signature/consent/release/revocation, journal and attestation
        checks without substituting public advisory dictionaries for authority.
        Even a fully checked route returns HOLD until the N3-B2 owner exists.
        PENDING/UNKNOWN, including a valid old lease, require reconciliation;
        this method cannot adopt, retry, steal or create a lease.
        """
        if (type(operation) is not op.Operation or operation not in
                (op.Operation.REUSE, op.Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT)):
            return Native9444Preflight(op.Operation.STATUS, Native9444Code.INVALID_REQUEST,
                op.Reason.INVALID_REQUEST)

        def hold(code, reason=op.Reason.EVIDENCE_GAP, advisory="ABSENCE_UNPROVEN"):
            return Native9444Preflight(operation, code, reason, advisory)

        if self._unresolved:
            return hold(Native9444Code.RECONCILE_ONLY, op.Reason.RECONCILE_ONLY)
        # Service authentication does not provide a GUI executor. In particular
        # never run N3-A's local desktop reads as the service/runner identity.
        if hasattr(self, "_broker"):
            return hold(Native9444Code.DESKTOP_TRANSPORT_REQUIRED, op.Reason.AUTHORITY_REQUIRED)
        authority, operator = self._authority, self._operator
        if (not isinstance(authority, WindowsGrantVerifier) or operator is None
                or operator._verifier is not authority or operator._journal is not authority.journal
                or type(proof) is not op.PrivateGrantProof):
            return hold(Native9444Code.AUTHORITY_REQUIRED, op.Reason.AUTHORITY_REQUIRED)
        try:
            now = operator._now()
            grant = operator._grant(proof, operation, now)
            if grant is None:
                return hold(Native9444Code.AUTHORITY_REQUIRED, op.Reason.AUTHORITY_REQUIRED)
            record = operator._record(grant, now)
            if record.state in (op.JournalState.PENDING, op.JournalState.UNKNOWN):
                return hold(Native9444Code.RECONCILE_ONLY, op.Reason.RECONCILE_ONLY)
            # The existing journal selects consumed keys across ALL key roles;
            # an OBSERVED record is still consumed, including NO_EFFECT.
            if record.lease is not None:
                return hold(Native9444Code.DUPLICATE, op.Reason.DUPLICATE)
            if Path(checkout).resolve(strict=True) != Path(authority.enrolled["release_root"]).resolve(strict=True):
                return hold(Native9444Code.TARGET_MISMATCH, op.Reason.OWNERSHIP_REQUIRED)
            evidence, public, now = operator._read(grant, now)
            reason = operator._gate(grant, evidence, public)
            if reason is not None:
                return hold(Native9444Code.EVIDENCE_GAP, reason)
            advisory = acquisition._assess_logon_9444_private(checkout)
            if type(advisory) is not acquisition._Logon9444Assessment:
                return hold(Native9444Code.EVIDENCE_GAP)
            required = ("REUSE_ONLY" if operation is op.Operation.REUSE else "BLOCKED_NOT_DEPLOYED")
            if advisory.code != required or advisory.observation is None:
                return hold(Native9444Code.ADVISORY_HOLD, advisory=advisory.code)
            (local, session, sid), env, (listeners, processes) = advisory.observation
            target = grant.target
            same_path = lambda a, b: ntpath.normcase(str(a)) == ntpath.normcase(str(b))
            if (type(env) is not acquisition.ChromeEnvironment
                    or (sid, session) != (target.user_sid, target.session_id)
                    or not same_path(env.executable, target.chrome_executable)
                    or not same_path(env.user_data_dir, target.dedicated_profile)
                    or env.endpoint != "http://127.0.0.1:9444" or env.port != 9444
                    or env.config_path != local / "AI-Commerce" / "Chrome-Acquisition" / "config.json"
                    or (operation is not op.Operation.REUSE and env.allow_launch is not True)):
                return hold(Native9444Code.TARGET_MISMATCH, op.Reason.OWNERSHIP_REQUIRED, advisory.code)
            if operation is op.Operation.REUSE:
                expected = grant.expected_process
                owners = [p for p in processes if p[0] == expected.pid]
                if (listeners != (("127.0.0.1", 9444, expected.pid),) or len(owners) != 1
                        or not same_path(owners[0][1], expected.executable)
                        or owners[0][3:5] != (expected.session_id, expected.user_sid)
                        # G2's fixed READ uses Unix milliseconds. N3-A also pins
                        # the full 100ns CreationDate across its two reads.
                        or (acquisition._creation_ticks(owners[0][5]) - 116444736000000000)
                            // 10000 / 1000 != expected.created_at):
                    return hold(Native9444Code.TARGET_MISMATCH, op.Reason.OWNERSHIP_REQUIRED, advisory.code)
            elif listeners or processes:
                return hold(Native9444Code.EVIDENCE_GAP, op.Reason.ABSENCE_REQUIRED, advisory.code)
            fresh, fresh_public, now = operator._read(grant, now)
            if (fresh.snapshot.observed_at <= evidence.snapshot.observed_at
                    or operator._gate(grant, fresh, fresh_public) is not None):
                return hold(Native9444Code.EVIDENCE_GAP, advisory=advisory.code)
            now = operator._now(now)
            if operator._grant(proof, operation, now) != grant:
                return hold(Native9444Code.AUTHORITY_REQUIRED, op.Reason.AUTHORITY_REQUIRED, advisory.code)
            # A concurrent logon/reservation or lost ACK can never be adopted.
            if operator._record(grant, now) != record:
                return hold(Native9444Code.RECONCILE_ONLY, op.Reason.RECONCILE_ONLY, advisory.code)
            return Native9444Preflight(operation, Native9444Code.OWNER_REQUIRED,
                op.Reason.AUTHORITY_REQUIRED, advisory.code, Native9444Contract(grant, env))
        except Exception:
            return hold(Native9444Code.EVIDENCE_GAP)

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
