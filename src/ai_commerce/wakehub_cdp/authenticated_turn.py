"""R3B2A: immutable claims and fail-closed PRE_SEND / POST_SEND gates.

This owner has NO private native provider, signature verifier, durable ledger,
browser implementation, or Send implementation. Typed values, signatures and
matching IDs are unverified claims. DOM/URL/screenshots, local hashes/time and
provider-shaped callbacks cannot authenticate a ChatGPT account or USER turn.

Local checks only reject inconsistent claims. Passing them always terminates
in NATIVE_PRIVATE_PROVIDER_UNAVAILABLE; it never permits Send or returns proof.
The future private owner must independently authenticate origin, grant freshness
and atomic one-shot consumption, durable RESERVED intent, current exclusive
surface, and the server USER-turn receipt, all bound to the exact same scope.
R3B2B consumer integration is deliberately separate. No UNKNOWN effect may be
replayed; absence of a receipt is never evidence that a click did not execute.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Literal


class AttestationPhase(str, Enum):
    PRE_SEND = "PRE_SEND"
    POST_SEND = "POST_SEND"


class EvidenceSource(str, Enum):
    NATIVE_PRIVATE_PROVIDER = "NATIVE_PRIVATE_PROVIDER"
    DOM = "DOM"
    URL = "URL"
    SCREENSHOT = "SCREENSHOT"
    LOCAL_DIGEST = "LOCAL_DIGEST"
    LOCAL_TIME = "LOCAL_TIME"
    CALLBACK = "CALLBACK"
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"


class SendOutcome(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    CLICK_REPORTED = "CLICK_REPORTED"
    CLICK_AMBIGUOUS = "CLICK_AMBIGUOUS"
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"


class GateStatus(str, Enum):
    HOLD = "HOLD"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"


class GateCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_CLOCK = "INVALID_CLOCK"
    MESSAGE_DIGEST_MISMATCH = "MESSAGE_DIGEST_MISMATCH"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    OBSERVATION_MISSING = "OBSERVATION_MISSING"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    SURFACE_NOT_EXCLUSIVE = "SURFACE_NOT_EXCLUSIVE"
    COMPOSER_DIRTY = "COMPOSER_DIRTY"
    SURFACE_UNSTABLE = "SURFACE_UNSTABLE"
    GRANT_MISSING = "GRANT_MISSING"
    GRANT_SCOPE_MISMATCH = "GRANT_SCOPE_MISMATCH"
    GRANT_INVALID = "GRANT_INVALID"
    GRANT_REUSED = "GRANT_REUSED"
    GRANT_NOT_FRESH = "GRANT_NOT_FRESH"
    RESERVATION_MISSING = "RESERVATION_MISSING"
    RESERVATION_SCOPE_MISMATCH = "RESERVATION_SCOPE_MISMATCH"
    RESERVATION_NOT_DURABLE_RESERVED = "RESERVATION_NOT_DURABLE_RESERVED"
    NATIVE_ATTESTATION_MISSING = "NATIVE_ATTESTATION_MISSING"
    WRONG_AUTHORITY_SOURCE = "WRONG_AUTHORITY_SOURCE"
    ATTESTATION_SCOPE_MISMATCH = "ATTESTATION_SCOPE_MISMATCH"
    ATTESTATION_PHASE_MISMATCH = "ATTESTATION_PHASE_MISMATCH"
    ATTESTATION_INVALID = "ATTESTATION_INVALID"
    ATTESTATION_NOT_FRESH = "ATTESTATION_NOT_FRESH"
    USER_TURN_MISSING = "USER_TURN_MISSING"
    USER_TURN_INVALID = "USER_TURN_INVALID"
    USER_TURN_SCOPE_MISMATCH = "USER_TURN_SCOPE_MISMATCH"
    NATIVE_PRIVATE_PROVIDER_UNAVAILABLE = "NATIVE_PRIVATE_PROVIDER_UNAVAILABLE"
    SEND_NOT_ATTEMPTED = "SEND_NOT_ATTEMPTED"
    CLICK_AMBIGUOUS_NO_REPLAY = "CLICK_AMBIGUOUS_NO_REPLAY"
    TRANSPORT_TIMEOUT_NO_REPLAY = "TRANSPORT_TIMEOUT_NO_REPLAY"
    INVALID_SEND_OUTCOME_NO_REPLAY = "INVALID_SEND_OUTCOME_NO_REPLAY"


@dataclass(frozen=True, slots=True, repr=False)
class OriginPins:
    account_id: str
    conversation_id: str
    session_ref: str
    route_generation: int
    registration_marker: str
    browser_custody_ref: str
    chrome_profile_id: str
    context_ref: str
    tab_ref: str
    composer_ref: str
    cdp_endpoint: str = "http://127.0.0.1:9444"


@dataclass(frozen=True, slots=True, repr=False)
class TurnScope:
    origin: OriginPins
    event_id: str
    message_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class WakeRequest:
    scope: TurnScope
    message_bytes: bytes


@dataclass(frozen=True, slots=True, repr=False)
class SignedWakeGrant:
    """Opaque signature is a claim, never a locally verified grant or credential."""

    scope: TurnScope
    grant_ref: str
    issuer_ref: str
    key_id: str
    signature: bytes
    issued_at: int
    expires_at: int
    operation: Literal["deliver_wake"] = "deliver_wake"
    one_shot: bool = True
    use_count: int = 0


@dataclass(frozen=True, slots=True, repr=False)
class DurableReservation:
    """Claimed persisted intent; fields/flags alone cannot prove durability."""

    scope: TurnScope
    reservation_ref: str
    grant_ref: str
    durable_receipt_ref: str
    reserved_at: int
    state: Literal["RESERVED"] = "RESERVED"


@dataclass(frozen=True, slots=True, repr=False)
class BrowserObservation:
    """Surface observation is a safety input, never account authentication."""

    scope: TurnScope
    observation_ref: str
    context_count: int
    tab_count: int
    composer_clean: bool
    surface_stable: bool


@dataclass(frozen=True, slots=True, repr=False)
class NativeAttestation:
    """Unverified signed envelope; source labels and constructor grant no trust."""

    scope: TurnScope
    phase: AttestationPhase
    source: EvidenceSource
    attestation_ref: str
    issuer_ref: str
    key_id: str
    signature: bytes
    grant_ref: str
    reservation_ref: str
    observation_ref: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True, slots=True, repr=False)
class ChatGPTUserTurnRef:
    """Candidate server USER reference; construction is NOT authentication.

    A forged opaque native-looking ID cannot be rejected by syntax alone.
    This owner never returns this claim as an authenticated result.
    """

    scope: TurnScope
    user_turn_id: str
    message_ref: str
    attestation_ref: str
    grant_ref: str
    reservation_ref: str
    source: EvidenceSource
    role: Literal["USER"] = "USER"


@dataclass(frozen=True, slots=True, repr=False)
class TurnEvidence:
    observation: BrowserObservation | None = None
    grant: SignedWakeGrant | None = None
    reservation: DurableReservation | None = None
    attestation: NativeAttestation | None = None


@dataclass(frozen=True, slots=True)
class GateResult:
    """Safe diagnostic only: no secrets, permit, replay token or proof."""

    phase: AttestationPhase
    status: GateStatus
    code: GateCode

    @property
    def send_allowed(self) -> Literal[False]:
        return False

    @property
    def retry_allowed(self) -> Literal[False]:
        return False

    @property
    def authenticated_user_turn(self) -> None:
        return None


def _ref(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= 256
            and all(33 <= ord(char) <= 126 for char in value))


def _timestamp(value: object) -> bool:
    return type(value) is int and value >= 0


def _fresh(issued_at: object, expires_at: object, now: int) -> bool:
    return (_timestamp(issued_at) and _timestamp(expires_at)
            and issued_at <= now < expires_at)


def _signature(value: object) -> bool:
    # Shape only. No key material, local signing, or signature verification.
    return type(value) is bytes and 1 <= len(value) <= 8192


def _valid_scope(scope: object) -> bool:
    if type(scope) is not TurnScope or type(scope.origin) is not OriginPins:
        return False
    pins = scope.origin
    return (type(pins.route_generation) is int and pins.route_generation > 0
            and type(pins.cdp_endpoint) is str
            and pins.cdp_endpoint == "http://127.0.0.1:9444"
            and all(_ref(getattr(pins, field)) for field in (
                "account_id", "conversation_id", "session_ref", "registration_marker",
                "browser_custody_ref", "chrome_profile_id", "context_ref", "tab_ref", "composer_ref"))
            and type(scope.event_id) is str
            and re.fullmatch(r"ef02-completion-v1:[0-9a-f]{64}", scope.event_id) is not None
            and type(scope.message_sha256) is str
            and re.fullmatch(r"[0-9a-f]{64}", scope.message_sha256) is not None)


def _same_scope(candidate: object, expected: TurnScope) -> bool:
    return _valid_scope(candidate) and candidate == expected


def _claims_code(request: WakeRequest, evidence: TurnEvidence, now: int,
                 phase: AttestationPhase) -> GateCode | None:
    if (type(request) is not WakeRequest or not _valid_scope(request.scope)
            or type(request.message_bytes) is not bytes or not request.message_bytes):
        return GateCode.INVALID_REQUEST
    if not _timestamp(now):
        return GateCode.INVALID_CLOCK
    if hashlib.sha256(request.message_bytes).hexdigest() != request.scope.message_sha256:
        return GateCode.MESSAGE_DIGEST_MISMATCH
    if type(evidence) is not TurnEvidence:
        return GateCode.INVALID_EVIDENCE
    scope = request.scope
    observation = evidence.observation
    if type(observation) is not BrowserObservation:
        return GateCode.OBSERVATION_MISSING
    if not _same_scope(observation.scope, scope):
        return GateCode.SCOPE_MISMATCH
    if (type(observation.context_count) is not int or observation.context_count != 1
            or type(observation.tab_count) is not int or observation.tab_count != 1):
        return GateCode.SURFACE_NOT_EXCLUSIVE
    if observation.composer_clean is not True:
        return GateCode.COMPOSER_DIRTY
    if observation.surface_stable is not True or not _ref(observation.observation_ref):
        return GateCode.SURFACE_UNSTABLE
    grant = evidence.grant
    if type(grant) is not SignedWakeGrant:
        return GateCode.GRANT_MISSING
    if not _same_scope(grant.scope, scope):
        return GateCode.GRANT_SCOPE_MISMATCH
    if (type(grant.operation) is not str or grant.operation != "deliver_wake"
            or grant.one_shot is not True or not _signature(grant.signature)
            or not all(_ref(v) for v in (grant.grant_ref, grant.issuer_ref, grant.key_id))):
        return GateCode.GRANT_INVALID
    if type(grant.use_count) is not int or grant.use_count != 0:
        return GateCode.GRANT_REUSED
    if not _fresh(grant.issued_at, grant.expires_at, now):
        return GateCode.GRANT_NOT_FRESH
    reservation = evidence.reservation
    if type(reservation) is not DurableReservation:
        return GateCode.RESERVATION_MISSING
    if not _same_scope(reservation.scope, scope) or reservation.grant_ref != grant.grant_ref:
        return GateCode.RESERVATION_SCOPE_MISMATCH
    if (type(reservation.state) is not str or reservation.state != "RESERVED"
            or not all(_ref(v) for v in (reservation.reservation_ref, reservation.durable_receipt_ref))
            or not _timestamp(reservation.reserved_at)
            or not grant.issued_at <= reservation.reserved_at <= now):
        return GateCode.RESERVATION_NOT_DURABLE_RESERVED
    attestation = evidence.attestation
    if type(attestation) is not NativeAttestation:
        return GateCode.NATIVE_ATTESTATION_MISSING
    if attestation.source is not EvidenceSource.NATIVE_PRIVATE_PROVIDER:
        return GateCode.WRONG_AUTHORITY_SOURCE
    if (not _same_scope(attestation.scope, scope) or attestation.grant_ref != grant.grant_ref
            or attestation.reservation_ref != reservation.reservation_ref
            or attestation.observation_ref != observation.observation_ref):
        return GateCode.ATTESTATION_SCOPE_MISMATCH
    if attestation.phase is not phase:
        return GateCode.ATTESTATION_PHASE_MISMATCH
    if (not _signature(attestation.signature)
            or not all(_ref(v) for v in (attestation.attestation_ref,
                                        attestation.issuer_ref, attestation.key_id))):
        return GateCode.ATTESTATION_INVALID
    if (not _fresh(attestation.issued_at, attestation.expires_at, now)
            or attestation.issued_at < reservation.reserved_at):
        return GateCode.ATTESTATION_NOT_FRESH
    return None


def pre_send(request: WakeRequest, evidence: TurnEvidence, *, now: int) -> GateResult:
    """Reject invalid claims before any possible staging/click; never Send-ready.

    No injectable verifier, provider callback or caller-set verified flag exists.
    Wall time is only a rejection input. The missing private authority must
    establish actual freshness and durable one-shot state independently.
    """
    code = _claims_code(request, evidence, now, AttestationPhase.PRE_SEND)
    return GateResult(AttestationPhase.PRE_SEND,
                      GateStatus.HOLD if code else GateStatus.UNAVAILABLE,
                      code or GateCode.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE)


def post_send(request: WakeRequest, evidence: TurnEvidence, *, outcome: SendOutcome,
              user_turn: ChatGPTUserTurnRef | None = None, now: int) -> GateResult:
    """Classify evidence only. Every possible click remains UNKNOWN, no replay.

    Never downgrade an uncertain effect to a retryable pre-send HOLD, including
    when its scope, grant, reservation, receipt or provider cannot be verified.
    This function does not imply that this package actually attempted a click.
    """
    phase = AttestationPhase.POST_SEND
    if type(outcome) is not SendOutcome:
        return GateResult(phase, GateStatus.UNKNOWN_EFFECT, GateCode.INVALID_SEND_OUTCOME_NO_REPLAY)
    if outcome is SendOutcome.NOT_ATTEMPTED:
        return GateResult(phase, GateStatus.HOLD, GateCode.SEND_NOT_ATTEMPTED)
    if outcome is SendOutcome.CLICK_AMBIGUOUS:
        return GateResult(phase, GateStatus.UNKNOWN_EFFECT, GateCode.CLICK_AMBIGUOUS_NO_REPLAY)
    if outcome is SendOutcome.TRANSPORT_TIMEOUT:
        return GateResult(phase, GateStatus.UNKNOWN_EFFECT, GateCode.TRANSPORT_TIMEOUT_NO_REPLAY)
    code = _claims_code(request, evidence, now, phase)
    if code is None:
        if type(user_turn) is not ChatGPTUserTurnRef:
            code = GateCode.USER_TURN_MISSING
        elif user_turn.source is not EvidenceSource.NATIVE_PRIVATE_PROVIDER:
            code = GateCode.WRONG_AUTHORITY_SOURCE
        elif (not _same_scope(user_turn.scope, request.scope)
              or user_turn.attestation_ref != evidence.attestation.attestation_ref
              or user_turn.grant_ref != evidence.grant.grant_ref
              or user_turn.reservation_ref != evidence.reservation.reservation_ref):
            code = GateCode.USER_TURN_SCOPE_MISMATCH
        elif (type(user_turn.role) is not str or user_turn.role != "USER"
              or not all(_ref(v) for v in (user_turn.user_turn_id, user_turn.message_ref))):
            code = GateCode.USER_TURN_INVALID
    # No actual private provider/cryptographic trust root is installed. Even a
    # native-looking forged reference and signature can never reach a PASS.
    return GateResult(phase, GateStatus.UNKNOWN_EFFECT,
                      code or GateCode.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE)
