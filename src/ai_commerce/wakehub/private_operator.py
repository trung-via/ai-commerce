"""Source-only private lifecycle policy; nothing is enrolled or installed here.

The three injected interfaces are PROTECTED native custody boundaries, not
plugins selected by an Issue. Their production implementations must be enrolled
separately under G2. There is deliberately no OS, subprocess, browser, network,
signer, journal-file or task implementation here. Python types, matching hashes,
an owner comment and an injected test double cannot establish native authority.

Only run(operation, opaque_signature_and_consent) is a request surface. All
identities and effect selectors come back from the separately enrolled verifier.
Private objects, their reprs/digests and exception text are not public receipts.
Only PublicReceipt.to_json() is suitable for publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import ntpath
import re
import time
from typing import Callable, Protocol
from uuid import UUID

from .supervisor_preflight import WakeHubPreflight
from ai_commerce.wakehub.native_inventory import (
    PrivateAttestation, PrivateEnrollment, PrivateSnapshot, ProcessIdentity,
    PublicInventory, normalize_private_inventory,
)


PINNED_PARENT_SHA = "bc5560b4af7d1f7ec1b747fb800c59cd53377ed8"


class Operation(Enum):
    STATUS = "STATUS"
    REUSE = "REUSE"
    START_IF_INDEPENDENTLY_VERIFIED_ABSENT = "START_IF_INDEPENDENTLY_VERIFIED_ABSENT"
    RECONCILE_ONLY = "RECONCILE_ONLY"


class GrantScope(Enum):
    PRIVATE_READ = "PRIVATE_READ"
    OWNED_ATTACH_ONLY = "OWNED_ATTACH_ONLY"
    EXACT_ABSENT_START = "EXACT_ABSENT_START"
    LOCAL_RECONCILIATION = "LOCAL_RECONCILIATION"


class DesktopState(Enum):
    LIVE_UNLOCKED_OWNER = "LIVE_UNLOCKED_OWNER"
    LOCKED = "LOCKED"
    UNPROVEN = "UNPROVEN"


class PauseState(Enum):
    PAUSED = "PAUSED"
    RELEASED_INDEPENDENTLY = "RELEASED_INDEPENDENTLY"
    UNPROVEN = "UNPROVEN"


class ReceiverState(Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    MUTED = "MUTED"
    UNPROVEN = "UNPROVEN"


class JournalState(Enum):
    EMPTY = "EMPTY"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"
    OBSERVED = "OBSERVED"


class ObservedOutcome(Enum):
    START_OBSERVED = "START_OBSERVED"
    REUSE_OBSERVED = "REUSE_OBSERVED"
    NO_EFFECT_OBSERVED = "NO_EFFECT_OBSERVED"


class Decision(Enum):
    HOLD = "HOLD"
    STATUS_OBSERVED = "STATUS_OBSERVED"
    START_OBSERVED = "START_OBSERVED"
    REUSE_OBSERVED = "REUSE_OBSERVED"
    RECONCILED = "RECONCILED"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"


class Reason(Enum):
    AUTHORITY_REQUIRED = "AUTHORITY_REQUIRED"
    INVALID_REQUEST = "INVALID_REQUEST"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    HUMAN_PAUSED = "HUMAN_PAUSED"
    RECEIVER_UNAVAILABLE = "RECEIVER_UNAVAILABLE"
    OWNERSHIP_REQUIRED = "OWNERSHIP_REQUIRED"
    ABSENCE_REQUIRED = "ABSENCE_REQUIRED"
    DUPLICATE = "DUPLICATE"
    RECONCILE_ONLY = "RECONCILE_ONLY"
    OBSERVED = "OBSERVED"


class EffectAttempt(Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    ATTEMPTED = "ATTEMPTED"


def _invalid() -> ValueError:
    return ValueError("WAKEHUB_LIFECYCLE_INPUT_INVALID")


def _timestamp(value: object) -> bool:
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 < value < 2**53)


def _path(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 1024
            and re.match(r"^[A-Za-z]:\\", value) is not None
            and ntpath.normpath(value) == value
            and not any(c in value for c in ('/', '..', '*', '?', '"'))
            and all(ord(c) >= 32 for c in value))


@dataclass(frozen=True, slots=True, repr=False)
class SourceBinding:
    """Expected running source/release, independently checked by native custody.

    These are claims, never proof that these bytes were deployed. The parent is
    fixed; the future verified release supplies its own exact source/release SHA
    and monotonic epochs. A branch name alone cannot establish this binding.
    """
    source_sha: str
    source_epoch: int
    release_sha: str
    release_epoch: int
    source_ref: str = "refs/heads/main"
    parent_sha: str = PINNED_PARENT_SHA

    def __post_init__(self):
        if (any(type(v) is not str or re.fullmatch(r"[0-9a-f]{40}", v) is None
                for v in (self.source_sha, self.release_sha))
                or any(type(v) is not int or not 1 <= v < 2**53
                       for v in (self.source_epoch, self.release_epoch))
                or self.source_ref != "refs/heads/main"
                or type(self.source_ref) is not str
                or type(self.parent_sha) is not str
                or self.parent_sha != PINNED_PARENT_SHA):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class LifecyclePolicy:
    source: SourceBinding

    def __post_init__(self):
        if type(self.source) is not SourceBinding:
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class PrivateTarget:
    user_name: str
    user_sid: str
    session_id: int
    chrome_executable: str
    dedicated_profile: str
    desktop: str = r"WinSta0\Default"
    address: str = "127.0.0.1"
    port: int = 9444

    def __post_init__(self):
        if (type(self.user_name) is not str or not 0 < len(self.user_name) <= 256
                or any(ord(c) < 32 for c in self.user_name)
                or type(self.user_sid) is not str
                or re.fullmatch(r"S-1-(?:[0-9]+-)+[0-9]+", self.user_sid) is None
                or len(self.user_sid) > 184
                or type(self.session_id) is not int or not 1 <= self.session_id < 2**32
                or not _path(self.chrome_executable)
                or ntpath.basename(self.chrome_executable).casefold() != "chrome.exe"
                or not _path(self.dedicated_profile)
                or type(self.desktop) is not str or self.desktop != r"WinSta0\Default"
                or type(self.address) is not str or self.address != "127.0.0.1"
                or type(self.port) is not int or self.port != 9444):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class EffectKey:
    action_id: UUID
    effect_id: UUID
    one_shot_key: UUID

    def __post_init__(self):
        if any(type(v) is not UUID for v in
               (self.action_id, self.effect_id, self.one_shot_key)):
            raise _invalid()

    def overlaps(self, other: EffectKey) -> bool:
        return (self.action_id == other.action_id or self.effect_id == other.effect_id
                or self.one_shot_key == other.one_shot_key)


@dataclass(frozen=True, slots=True, repr=False)
class PrivateGrantProof:
    """Bounded opaque proof, passed ONLY to separately enrolled native verification.

    No decoder, self-signed acceptance, Issue parser or signer is provided here.
    Signature AND independently authenticated local consent must be verified.
    """
    signature: bytes
    consent: bytes

    def __post_init__(self):
        if any(type(v) is not bytes or not 1 <= len(v) <= 8192
               for v in (self.signature, self.consent)):
            raise _invalid()


def _scope(operation: Operation) -> GrantScope:
    return (GrantScope.PRIVATE_READ if operation is Operation.STATUS else
            GrantScope.OWNED_ATTACH_ONLY if operation is Operation.REUSE else
            GrantScope.EXACT_ABSENT_START if operation is
            Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT else
            GrantScope.LOCAL_RECONCILIATION)


@dataclass(frozen=True, slots=True, repr=False)
class PrivateGrant:
    """Verifier output claims. Constructing this dataclass grants NO authority."""
    operation: Operation
    scope: GrantScope
    source: SourceBinding
    target: PrivateTarget
    key: EffectKey
    issued_at: float
    expires_at: float
    expected_process: ProcessIdentity | None = None
    reconciles: EffectKey | None = None

    def __post_init__(self):
        if (type(self.operation) is not Operation or type(self.scope) is not GrantScope
                or self.scope is not _scope(self.operation)
                or type(self.source) is not SourceBinding
                or type(self.target) is not PrivateTarget or type(self.key) is not EffectKey
                or not _timestamp(self.issued_at) or not _timestamp(self.expires_at)
                or not 0 < self.expires_at - self.issued_at <= 30
                or (self.expected_process is not None
                    and type(self.expected_process) is not ProcessIdentity)
                or (self.operation is Operation.REUSE) != (self.expected_process is not None)
                or (self.reconciles is not None and type(self.reconciles) is not EffectKey)
                or (self.operation is Operation.RECONCILE_ONLY) != (self.reconciles is not None)):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class PrivateEvidence:
    """Independently attested full read, including live local consent/desktop.

    Receiver ONLINE means authenticated connectivity, not Runner.Listener
    presence. RELEASED_INDEPENDENTLY requires explicit authenticated Human
    release of the standing pause, separately from an effect signature.
    """
    source: SourceBinding
    snapshot: PrivateSnapshot
    enrollment: PrivateEnrollment
    attestation: PrivateAttestation
    preflight: WakeHubPreflight
    desktop: DesktopState
    receiver: ReceiverState
    pause: PauseState = PauseState.PAUSED

    def __post_init__(self):
        for value, kind in ((self.source, SourceBinding), (self.snapshot, PrivateSnapshot),
                            (self.enrollment, PrivateEnrollment),
                            (self.attestation, PrivateAttestation),
                            (self.preflight, WakeHubPreflight), (self.desktop, DesktopState),
                            (self.receiver, ReceiverState), (self.pause, PauseState)):
            if type(value) is not kind:
                raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class PrivateIntent:
    grant: PrivateGrant

    def __post_init__(self):
        if (type(self.grant) is not PrivateGrant
                or self.grant.operation not in (Operation.REUSE,
                    Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT)):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class PrivateLease:
    intent: PrivateIntent
    lease_id: UUID
    expires_at: float

    def __post_init__(self):
        if (type(self.intent) is not PrivateIntent or type(self.lease_id) is not UUID
                or not _timestamp(self.expires_at)
                or not self.intent.grant.issued_at < self.expires_at <= self.intent.grant.expires_at):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class ObservedEffect:
    lease: PrivateLease
    outcome: ObservedOutcome
    observed_at: float
    process: ProcessIdentity | None

    def __post_init__(self):
        if (type(self.lease) is not PrivateLease or type(self.outcome) is not ObservedOutcome
                or not _timestamp(self.observed_at)
                or self.observed_at < self.lease.intent.grant.issued_at
                or (self.process is not None and type(self.process) is not ProcessIdentity)
                or (self.outcome is ObservedOutcome.NO_EFFECT_OBSERVED) != (self.process is None)
                or (self.process is not None and (
                    self.process.user_sid != self.lease.intent.grant.target.user_sid
                    or self.process.session_id != self.lease.intent.grant.target.session_id
                    or ntpath.normcase(self.process.executable) != ntpath.normcase(
                        self.lease.intent.grant.target.chrome_executable)
                    or self.process.created_at > self.observed_at))
                or (self.outcome is ObservedOutcome.START_OBSERVED
                    and (self.lease.intent.grant.operation is not
                         Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT
                         or not self.lease.intent.grant.issued_at <=
                         self.process.created_at < self.lease.expires_at))
                or (self.outcome is ObservedOutcome.REUSE_OBSERVED
                    and (self.lease.intent.grant.operation is not Operation.REUSE
                         or self.process != self.lease.intent.grant.expected_process))):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class JournalRecord:
    state: JournalState
    lease: PrivateLease | None = None
    observation: ObservedEffect | None = None

    def __post_init__(self):
        if (type(self.state) is not JournalState
                or (self.lease is not None and type(self.lease) is not PrivateLease)
                or (self.state is JournalState.EMPTY) != (self.lease is None)
                or (self.observation is not None and type(self.observation) is not ObservedEffect)
                or (self.state is JournalState.OBSERVED) != (self.observation is not None)
                or (self.observation is not None and self.observation.lease != self.lease)):
            raise _invalid()


class PrivateGrantVerifier(Protocol):
    """Must live in separately enrolled, pinned private custody; default DENY.

    Verify signature against a protected enrolled trust anchor, explicit local
    consent and revocation, exact scope/key/source/release/ref/epochs, current
    Windows owner SID, nonzero interactive session, desktop, dedicated executable
    and profile, fixed endpoint and <=30s expiry. Reject fake/self-signed evidence.
    read_verified must independently authenticate ALL returned evidence, including
    snapshot/enrollment digests, unlocked desktop, online receiver and pause
    release. Never implement it by echoing candidate hashes or caller booleans.
    Lease/journal checks must authenticate the private durable store and local
    ownership; reconciliation must prove the ORIGINAL operation outcome from an
    independent durable observation, never infer causality from Chrome presence.
    """
    def verify_grant(self, proof: PrivateGrantProof, operation: Operation,
                     policy: LifecyclePolicy, now: float) -> PrivateGrant | None: ...
    def read_verified(self, grant: PrivateGrant, now: float) -> PrivateEvidence | None: ...
    def verify_journal(self, grant: PrivateGrant, record: JournalRecord, now: float) -> bool: ...
    def verify_lease(self, lease: PrivateLease, now: float) -> bool: ...
    def reconcile(self, grant: PrivateGrant, record: JournalRecord,
                  now: float) -> ObservedEffect | None: ...


class PrivateExecutor(Protocol):
    """No implementation here. Pin externally alongside the enrolled verifier.

    Each adapter must atomically recheck lease, revocation, live owning desktop,
    pause release and exact observed target immediately at its native boundary.
    start_dedicated must recheck complete absence/conflicts; reuse_owned must
    recheck PID/creation time/exe/profile/SID/session/loopback. Fail ambiguous on
    any race. No tabs, Send, runner/task operation, kill, relaunch or generic
    command surface. A returned ACK is never proof of an observed outcome.
    """
    def start_dedicated(self, lease: PrivateLease) -> None: ...
    def reuse_owned(self, lease: PrivateLease) -> None: ...


class PrivateJournal(Protocol):
    """Future protected durable write-ahead protocol; NOT installed by this file.

    read includes ANY unresolved intent in the single owner/profile effect lane,
    even under another key, target spelling or release. reserve must atomically
    persist PENDING intent AND exclusive local lease BEFORE returning, with
    first-wins uniqueness for action_id, effect_id and one_shot_key forever.
    Concurrent requests, expired leases, restarts and changed releases cannot
    bypass unresolved work. No lease stealing or automatic retry. A durability
    failure poisons the lane until independent reconciliation. mark_unknown is
    monotonic. complete/resolve persist an independently observed result and
    retain all consumed keys, including a proven NO_EFFECT result. They never
    perform a native effect. In-memory test doubles establish none of these
    native durability/atomicity properties.
    """
    def read(self, grant: PrivateGrant) -> JournalRecord: ...
    def reserve(self, intent: PrivateIntent, now: float) -> PrivateLease | None: ...
    def mark_unknown(self, intent: PrivateIntent) -> None: ...
    def complete(self, lease: PrivateLease, observation: ObservedEffect) -> bool: ...
    def resolve(self, record: JournalRecord, observation: ObservedEffect) -> bool: ...


class _DenyVerifier:
    def verify_grant(self, proof, operation, policy, now):
        return None


class _DenyExecutor:
    def start_dedicated(self, lease):
        raise PermissionError("WAKEHUB_NATIVE_NOT_ENROLLED")

    def reuse_owned(self, lease):
        raise PermissionError("WAKEHUB_NATIVE_NOT_ENROLLED")


class _DenyJournal:
    def read(self, grant):
        raise PermissionError("WAKEHUB_JOURNAL_NOT_ENROLLED")


@dataclass(frozen=True, slots=True)
class PublicReceipt:
    operation: Operation
    decision: Decision
    reason: Reason
    effect: EffectAttempt = EffectAttempt.NOT_ATTEMPTED

    def to_json(self) -> str:
        for value, kind in ((self.operation, Operation), (self.decision, Decision),
                            (self.reason, Reason), (self.effect, EffectAttempt)):
            if type(value) is not kind:
                raise _invalid()
        return json.dumps({
            "schema_version": "WakeHubPrivateOperatorPublic.v1",
            "operation": self.operation.value, "decision": self.decision.value,
            "reason": self.reason.value, "effect": self.effect.value,
            "next_operation": (Operation.RECONCILE_ONLY.value
                if self.decision is Decision.UNKNOWN_EFFECT else Operation.STATUS.value),
            "native_authority": "NOT_PROVEN_BY_SOURCE_OR_TEST_ADAPTER",
            "deployment": "G2_SEPARATE_GRANT_REQUIRED",
            "logon_task": "NOT_INSTALLED_BY_SOURCE", "wake": "NOT_SENT",
        }, sort_keys=True, separators=(",", ":"))


class PrivateLifecycleOperator:
    def __init__(self, policy: LifecyclePolicy, *,
                 verifier: PrivateGrantVerifier | None = None,
                 executor: PrivateExecutor | None = None,
                 journal: PrivateJournal | None = None,
                 clock: Callable[[], float] = time.time):
        if type(policy) is not LifecyclePolicy:
            raise _invalid()
        self._policy = policy
        self._verifier = verifier if verifier is not None else _DenyVerifier()
        self._executor = executor if executor is not None else _DenyExecutor()
        self._journal = journal if journal is not None else _DenyJournal()
        self._clock = clock

    def _now(self, previous: float = 0) -> float:
        now = self._clock()
        if not _timestamp(now) or now < previous:
            raise _invalid()
        return now

    def _grant(self, proof, operation, now):
        grant = self._verifier.verify_grant(proof, operation, self._policy, now)
        if (type(grant) is not PrivateGrant or grant.operation is not operation
                or grant.source != self._policy.source
                or not grant.issued_at <= now < grant.expires_at):
            return None
        return grant

    def _read(self, grant, now):
        evidence = self._verifier.read_verified(grant, now)
        # Read/verification can block. Validate against completion time, not a
        # timestamp captured before the private read began.
        now = self._now(now)
        if type(evidence) is not PrivateEvidence or evidence.source != grant.source:
            raise _invalid()
        s, e, target = evidence.snapshot, evidence.enrollment, grant.target
        if (any((value.user_name, value.user_sid, value.session_id) !=
                (target.user_name, target.user_sid, target.session_id) for value in (s, e))
                or ntpath.normcase(e.chrome_executable) != ntpath.normcase(target.chrome_executable)
                or ntpath.normcase(e.dedicated_profile) != ntpath.normcase(target.dedicated_profile)
                or not grant.issued_at <= now < grant.expires_at
                or not e.issued_at <= now < e.expires_at
                or s.observed_at < grant.issued_at
                or evidence.desktop is not DesktopState.LIVE_UNLOCKED_OWNER):
            raise _invalid()
        public = normalize_private_inventory(s, e, evidence.attestation, now=now)
        if (public.inventory_status not in ("VERIFIED_READ", "HOLD_RECONCILE")
                or public.supervisor != evidence.preflight):
            raise _invalid()
        return evidence, public, now

    @staticmethod
    def _gate(grant, evidence, public, *, after=False):
        if evidence.enrollment.unknown_effect:
            return Reason.RECONCILE_ONLY
        if (evidence.pause is not PauseState.RELEASED_INDEPENDENTLY
                or evidence.enrollment.trusted_human_pause):
            return Reason.HUMAN_PAUSED
        if (evidence.receiver is not ReceiverState.ONLINE
                or public.runner_listeners[2] != "PRESENT"):
            return Reason.RECEIVER_UNAVAILABLE
        s = evidence.snapshot
        if grant.operation is Operation.REUSE or after:
            if (public.chrome_ownership != "OWNED_CDP_9444"
                    or len(s.chrome_processes) != 1):
                return Reason.OWNERSHIP_REQUIRED
            if grant.operation is Operation.REUSE and s.chrome_processes[0].identity != grant.expected_process:
                return Reason.OWNERSHIP_REQUIRED
        elif public.chrome_ownership != "ABSENT_INDEPENDENTLY_VERIFIED":
            return Reason.ABSENCE_REQUIRED
        return None

    def _record(self, grant, now):
        record = self._journal.read(grant)
        if (type(record) is not JournalRecord
                or self._verifier.verify_journal(grant, record, now) is not True):
            raise _invalid()
        return record

    def _unknown(self, operation, intent, attempted):
        if intent is not None:
            try:
                self._journal.mark_unknown(intent)
            except Exception:
                pass  # PENDING/unacknowledged storage must remain a poisoned lane.
        return PublicReceipt(operation, Decision.UNKNOWN_EFFECT, Reason.RECONCILE_ONLY,
            EffectAttempt.ATTEMPTED if attempted else EffectAttempt.NOT_ATTEMPTED)

    def _reconcile(self, grant, record, now):
        if (record.state not in (JournalState.PENDING, JournalState.UNKNOWN, JournalState.OBSERVED)
                or record.lease.intent.grant.key != grant.reconciles
                or record.lease.intent.grant.source != grant.source
                or record.lease.intent.grant.target != grant.target):
            return PublicReceipt(grant.operation, Decision.HOLD, Reason.EVIDENCE_GAP)
        observation = self._verifier.reconcile(grant, record, now)
        if (type(observation) is not ObservedEffect or observation.lease != record.lease
                or observation.observed_at > now
                or (record.observation is not None and record.observation != observation)):
            return PublicReceipt(grant.operation, Decision.HOLD, Reason.EVIDENCE_GAP)
        if record.state is not JournalState.OBSERVED:
            if self._journal.resolve(record, observation) is not True:
                return self._unknown(grant.operation, None, False)
        confirmed = self._record(grant, self._now(now))
        if confirmed.state is not JournalState.OBSERVED or confirmed.observation != observation:
            return self._unknown(grant.operation, None, False)
        return PublicReceipt(grant.operation, Decision.RECONCILED, Reason.OBSERVED)

    def run(self, operation: Operation, proof: PrivateGrantProof | None = None) -> PublicReceipt:
        """Bounded control flow. Exceptions/ACK loss never authorize a retry.

        STATUS reads only. REUSE never falls through to START. RECONCILE_ONLY
        cannot call an effect adapter. No operation can send ChatGPT or Wake.
        """
        if type(operation) is not Operation:
            return PublicReceipt(Operation.STATUS, Decision.HOLD, Reason.INVALID_REQUEST)
        if type(proof) is not PrivateGrantProof:
            return PublicReceipt(operation, Decision.HOLD, Reason.AUTHORITY_REQUIRED)
        intent, reserved, attempted = None, False, False
        try:
            now = self._now()
            grant = self._grant(proof, operation, now)
            if grant is None:
                return PublicReceipt(operation, Decision.HOLD, Reason.AUTHORITY_REQUIRED)
            record = self._record(grant, now)
            if operation is Operation.RECONCILE_ONLY:
                return self._reconcile(grant, record, now)
            if record.state in (JournalState.PENDING, JournalState.UNKNOWN):
                return self._unknown(operation, None, False)
            if (operation is not Operation.STATUS and record.lease is not None
                    and record.lease.intent.grant.key.overlaps(grant.key)):
                return PublicReceipt(operation, Decision.HOLD, Reason.DUPLICATE)
            evidence, public, now = self._read(grant, now)
            if operation is Operation.STATUS:
                if evidence.enrollment.unknown_effect:
                    return self._unknown(operation, None, False)
                return PublicReceipt(operation, Decision.STATUS_OBSERVED, Reason.OBSERVED)
            reason = self._gate(grant, evidence, public)
            if reason is not None:
                if reason is Reason.RECONCILE_ONLY:
                    return self._unknown(operation, None, False)
                return PublicReceipt(operation, Decision.HOLD, reason)
            intent = PrivateIntent(grant)
            # Even reservation ACK loss is ambiguous. Never attempt the effect
            # unless the durable lease is returned AND independently verified.
            reserved = True
            lease = self._journal.reserve(intent, now)
            if lease is None:
                return PublicReceipt(operation, Decision.HOLD, Reason.DUPLICATE)
            now = self._now(now)
            if (type(lease) is not PrivateLease or lease.intent != intent
                    or not now < lease.expires_at
                    or self._verifier.verify_lease(lease, now) is not True):
                return self._unknown(operation, intent, False)
            fresh, fresh_public, now = self._read(grant, now)
            if (fresh.snapshot.observed_at <= evidence.snapshot.observed_at
                    or self._gate(grant, fresh, fresh_public) is not None):
                return self._unknown(operation, intent, False)
            now = self._now(now)
            if (self._grant(proof, operation, now) != grant or not now < lease.expires_at
                    or self._verifier.verify_lease(lease, now) is not True):
                return self._unknown(operation, intent, False)
            attempted = True
            if operation is Operation.REUSE:
                self._executor.reuse_owned(lease)
            else:
                self._executor.start_dedicated(lease)
            now = self._now(now)
            if self._grant(proof, operation, now) != grant or not now < lease.expires_at:
                return self._unknown(operation, intent, True)
            observed, observed_public, now = self._read(grant, now)
            if (observed.snapshot.observed_at <= fresh.snapshot.observed_at
                    or self._gate(grant, observed, observed_public, after=True) is not None):
                return self._unknown(operation, intent, True)
            if (operation is Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT
                    and observed.snapshot.chrome_processes[0].identity.created_at <=
                    fresh.snapshot.observed_at):
                return self._unknown(operation, intent, True)
            outcome = (ObservedOutcome.REUSE_OBSERVED if operation is Operation.REUSE
                       else ObservedOutcome.START_OBSERVED)
            observation = ObservedEffect(lease, outcome, observed.snapshot.observed_at,
                observed.snapshot.chrome_processes[0].identity)
            if self._journal.complete(lease, observation) is not True:
                return self._unknown(operation, intent, True)
            confirmed = self._record(grant, self._now(now))
            if confirmed.state is not JournalState.OBSERVED or confirmed.observation != observation:
                return self._unknown(operation, intent, True)
            return PublicReceipt(operation, Decision.REUSE_OBSERVED if operation is Operation.REUSE
                else Decision.START_OBSERVED, Reason.OBSERVED, EffectAttempt.ATTEMPTED)
        except Exception:
            if reserved:
                return self._unknown(operation, intent, attempted)
            return PublicReceipt(operation, Decision.HOLD, Reason.EVIDENCE_GAP)
