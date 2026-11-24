"""One bounded WakeHub iteration and its private SQLite journal.

A durable reservation precedes delivery. Recovery can look for a receipt but
cannot send again. Delivery evidence survives optional response and consumer
receipt processing. The enrolled native authorities are still unavailable.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Event as StopEvent
from types import SimpleNamespace
from typing import Callable, Literal, Protocol

from ai_commerce.production_orchestration.contracts import (
    CONSUMER_ROUTES, OrchestrationError, ProductionStageCompleted, ProductionStageReceipt,
    Provenance, RouteAuthorization, StageSpec, bytes_digest, digest,
)
from ai_commerce.production_orchestration.orchestrator import ProductionPipelineOrchestrator
from ai_commerce.remote import cdp_doorbell, chrome_acquisition
from ai_commerce.remote._private_state import (
    PrivateStateError, _Transaction, _linked, _regular, _windows_handle,
)
from ai_commerce.remote.cdp_doorbell import LocalChromeDoorbellAdapter
from ai_commerce.remote.conversation_binding import (
    BindingError, ResolvedBinding, _CHAT, _ROUTE, _decode_unique, resolve_binding,
)
from ai_commerce.remote.local_completion_handoff import HandoffBlocked, _private_identity
from ai_commerce.remote.local_wake_trigger import GitHubExactRunReader, PinnedRun, TriggerBlocked
from ai_commerce.remote.github_artifact_authority import GitHubRestArtifactAuthority
from ai_commerce.remote.github_read_retry import GitHubReadBlocked, GitHubReadBudget
from ai_commerce.remote.local_run_registration import (
    RegistrationBlocked, RegistrationIntent, _record_valid,
)
from ai_commerce.remote.signed_origin_consent import SignedOriginConsentAuthority, registry_sha256
from ai_commerce.remote.trusted_origin import LocalCallerAttestation
from ai_commerce.remote.wake_adapter import (
    ArtifactAuthority, GitHubArtifactProvenance, ProvenanceError, WakeReceipt, verify_provenance,
)
from ai_commerce.wakehub_cdp import authenticated_turn as cdp_turn

Evidence = Literal["GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE"]
Status = Literal["PENDING", "RESERVED", "UI_TURN_OBSERVED", "USER_TURN_AUTHENTICATED", "BRAIN_CORRELATED",
                 "ACK", "BLOCKED_EXTERNAL", "FAILED", "UNKNOWN_EFFECT", "HOLD", "HOLD_DEPENDENCY"]
_CODE_ROOT = Path(__file__).resolve().parents[3]
_DELIVERY_STATES = {"UI_TURN_OBSERVED", "USER_TURN_AUTHENTICATED"}
_LAUNCH_UNKNOWN = "CHROME_LAUNCH_RECONCILE_REQUIRED"
_ACQUISITION_REPORTED = "CHROME_ACQUISITION_REPORTED"
_CHROME_READY = {"CHROME_OWNED", "CHROME_OWNED_LAUNCH_UNRESOLVED"}
# The acquisition API also uses WINDOWS_PROBE_UNAVAILABLE to normalize
# unrecognized subprocess output. That receipt cannot prove a no-effect HOLD.
_ACQUISITION_NO_EFFECT = frozenset({
    "LOCAL_OPERATOR_ONLY", "WINDOWS_INTERACTIVE_REQUIRED", "WINDOWS_TOKEN_UNAVAILABLE",
    "ELEVATED_CHROME_DISALLOWED", "ENROLLMENT_IDENTITY_UNPROVEN", "OPT_IN_REQUIRED",
    "WINDOWS_OWNER_CONTEXT_UNVERIFIED", "PROFILE_UNAVAILABLE", "PROFILE_LOCKED",
    "PROFILE_ALREADY_ACTIVE_NO_CDP", "CHROME_PATH_INVALID", "CHROME_EXECUTABLE_UNTRUSTED",
    "SUPERVISOR_ACTIVE", "WINDOWS_PROBE_INVALID", "FOREIGN_PORT_OWNER",
    "BROWSER_OWNERSHIP_UNPROVEN", "CDP_PROTOCOL_UNAVAILABLE",
})
_NEXT = {
    "PENDING": {"RESERVED", "BLOCKED_EXTERNAL", "FAILED"},
    "RESERVED": _DELIVERY_STATES | {"UNKNOWN_EFFECT", "BLOCKED_EXTERNAL"},
    "UNKNOWN_EFFECT": _DELIVERY_STATES,
    "USER_TURN_AUTHENTICATED": {"BRAIN_CORRELATED"},
    # Legacy journals also recorded response failures as delivery failures.
    "UI_TURN_OBSERVED": {"BRAIN_CORRELATED", "BLOCKED_EXTERNAL", "UNKNOWN_EFFECT"},
    "BRAIN_CORRELATED": {"ACK", "BLOCKED_EXTERNAL", "UNKNOWN_EFFECT"},
}


class RuntimeHold(ValueError):
    """Only fixed diagnostic codes; no private routes or remote payloads."""


class DependencyHold(RuntimeHold):
    """A missing enrolled source owner, never a journal state or a send retry."""


def _supervisor_start_authority(registration: Registration, owner: tuple[Path, int, str],
                                environment: chrome_acquisition.ChromeEnvironment) -> Callable[[], dict]:
    """Resolve a fresh, target-bound acquisition from the local 9444 owner.

    The owner must enforce unattended consent, pause release, receiver and
    current registration/session at invocation, using its existing launch
    arbitration and receipt. The attended opt-in and installed task cannot
    supply that operation to this supervisor.
    """
    raise DependencyHold("INTERACTIVE_9444_START_AUTHORITY_UNAVAILABLE")


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeHold(code)


def _safe_code(error: Exception, fallback: str) -> str:
    code = str(error)
    return code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) else fallback


def _reference(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 1024
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))


@dataclass(frozen=True, repr=False)
class Registration:
    """One immutable Chat A, source revision, route and marker generation."""
    binding: ResolvedBinding
    source_sha: str
    evidence_type: Evidence

    def __post_init__(self) -> None:
        b = self.binding
        _require(type(b) is ResolvedBinding and type(b.route_id) is str
                 and _ROUTE.fullmatch(b.route_id) is not None
                 and type(b.generation) is int and b.generation > 0
                 and type(b.chat_url) is str and _CHAT.fullmatch(b.chat_url) is not None
                 and b.cdp_endpoint == "http://127.0.0.1:9444", "REGISTRATION_INVALID")
        _require(type(self.source_sha) is str and re.fullmatch(r"[0-9a-f]{40}", self.source_sha)
                 is not None, "SOURCE_INVALID")
        _require(self.evidence_type in ("GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE"), "EVIDENCE_INVALID")

    @property
    def marker(self) -> str:
        # A correlation commitment, not a grant or native origin attestation.
        return "wakehub-r1:" + digest(asdict(self))


@dataclass(frozen=True)
class Event:
    """Detached selectors returned only by a trusted, provenance-checking source."""
    event_id: str
    source_sha: str
    attempt: int
    payload_sha256: str
    evidence_type: Evidence

    def __post_init__(self) -> None:
        _require(type(self.event_id) is str and re.fullmatch(r"ef02-completion-v1:[0-9a-f]{64}",
                                                          self.event_id) is not None, "EVENT_INVALID")
        _require(type(self.source_sha) is str and re.fullmatch(r"[0-9a-f]{40}", self.source_sha)
                 is not None and type(self.attempt) is int and self.attempt > 0, "EVENT_INVALID")
        _require(type(self.payload_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", self.payload_sha256)
                 is not None and self.evidence_type in ("GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE"),
                 "EVENT_INVALID")

    def key(self, registration: Registration) -> str:
        return digest({"event_id": self.event_id, "source_sha": self.source_sha,
                       "route_generation": registration.binding.generation, "attempt": self.attempt})


class EventSource(Protocol):
    def next_event(self) -> Event | None: ...


class GitHubEventSource:
    """Compose the existing exact-run reader and independent D5 authentication.

    Reader results are candidates, never authority. No polling loop, new queue,
    GitHub writes, or tokens stored in the journal. Callers inject authenticated
    existing implementations at a trusted local composition root.
    """
    def __init__(self, reader: GitHubExactRunReader, target: PinnedRun,
                 authority: ArtifactAuthority, *, expected_event_id: str | None = None):
        self.reader, self.target, self.authority = reader, target, authority
        self.expected_event_id = expected_event_id
        self.verified_event: dict | None = None

    def next_event(self) -> Event | None:
        self.verified_event = None
        candidate = self.reader.candidate(self.target)
        if candidate is None:
            return None
        event, report = deepcopy(candidate)
        provenance = GitHubArtifactProvenance(
            repository=event["repository"], run_id=self.target.run_id,
            run_attempt=self.target.run_attempt,
            artifact_name=f"ef02-results-{self.target.run_id}-{self.target.run_attempt}",
            workflow_run_status="completed", attestation_verified=True)
        verify_provenance(event, report, provenance, authority=self.authority)
        _require(event["source_sha"] == self.target.source_sha
                 and event["issue_number"] == self.target.issue_number
                 and event["batch_id"] == self.target.batch_id
                 and event["run_id"] == self.target.run_id
                 and event["run_attempt"] == self.target.run_attempt, "SOURCE_TARGET_DRIFT")
        _require(self.expected_event_id is None or event["event_id"] == self.expected_event_id,
                 "REGISTERED_EVENT_DRIFT")
        self.verified_event = deepcopy(event)
        return Event(event["event_id"], event["source_sha"], event["run_attempt"],
                     digest(event), "GITHUB_AUTHENTICATED")


@dataclass(frozen=True, repr=False)
class NativeOriginPins:
    """Private expected pins, NOT proof merely because this value is typed.

    A future independently enrolled owner must authenticate all pins together.
    Profile, account, session and composer references must be stable identities,
    never DOM labels, flags, WH03 markers or hashes invented by this runner.
    """
    windows_sid: str
    windows_session_id: int
    windows_logon_id: str
    desktop: str
    chrome_profile_id: str
    chrome_custody_ref: str
    account_id: str
    account_session_ref: str
    binding: ResolvedBinding
    marker: str
    context_ref: str
    tab_ref: str
    composer_ref: str
    provenance_ref: str

    def __post_init__(self) -> None:
        _require(type(self.windows_session_id) is int and self.windows_session_id > 0
                 and self.desktop == r"WinSta0\Default"
                 and type(self.binding) is ResolvedBinding
                 and self.binding.cdp_endpoint == "http://127.0.0.1:9444"
                 and all(_reference(getattr(self, name)) for name in
                         ("windows_sid", "windows_logon_id", "chrome_profile_id", "chrome_custody_ref",
                          "account_id", "account_session_ref", "marker", "context_ref", "tab_ref",
                          "composer_ref", "provenance_ref")), "NATIVE_ORIGIN_PINS_INVALID")


@dataclass(frozen=True, repr=False)
class SignedWakeAuthorization:
    """Detached scope after existing signed-origin checks; not native identity."""
    event: Event
    registration: Registration
    registry_sha256: str
    principal: str
    caller_id: str
    operations: tuple[Literal["resolve_claim_route", "deliver_wake"], ...]

    def __post_init__(self) -> None:
        _require(type(self.event) is Event and type(self.registration) is Registration
                 and type(self.registry_sha256) is str
                 and re.fullmatch(r"[0-9a-f]{64}", self.registry_sha256) is not None
                 and _reference(self.principal) and _reference(self.caller_id)
                 and type(self.operations) is tuple and self.operations in
                 (("resolve_claim_route",), ("resolve_claim_route", "deliver_wake")),
                 "SIGNED_WAKE_AUTHORIZATION_INVALID")


@dataclass(frozen=True, repr=False)
class DoorbellRequest:
    event_id: str
    event_key: str
    binding: ResolvedBinding
    marker: str
    canonical_bytes: bytes

    def __post_init__(self) -> None:
        _require(type(self.event_id) is str
                 and re.fullmatch(r"ef02-completion-v1:[0-9a-f]{64}", self.event_id) is not None
                 and type(self.event_key) is str and re.fullmatch(r"[0-9a-f]{64}", self.event_key) is not None
                 and type(self.binding) is ResolvedBinding
                 and self.binding.cdp_endpoint == "http://127.0.0.1:9444" and _reference(self.marker)
                 and type(self.canonical_bytes) is bytes
                 and self.canonical_bytes == cdp_doorbell._doorbell_text(
                     self.event_id, "trung-via/ai-commerce", send_only=True).encode("utf-8"),
                 "DOORBELL_REQUEST_INVALID")

    @classmethod
    def for_event(cls, event: Event, registration: Registration) -> DoorbellRequest:
        _require(event.source_sha == registration.source_sha
                 and event.evidence_type == registration.evidence_type, "DOORBELL_REQUEST_DRIFT")
        return cls(event.event_id, event.key(registration), registration.binding, registration.marker,
                   cdp_doorbell._doorbell_text(event.event_id, "trung-via/ai-commerce",
                                               send_only=True).encode("utf-8"))


def _turn_request(registration: Registration, event: Event, request: DoorbellRequest,
                  origin: NativeOriginPins | None) -> cdp_turn.WakeRequest:
    """Bind the registered URL, Windows session and completion artifact to the claims.

    The runtime independently verifies the completion artifact at ingress.
    Account, session and custody still require an independently enrolled owner.
    """
    if (type(registration) is not Registration or type(event) is not Event
            or registration.evidence_type != "GITHUB_AUTHENTICATED"
            or event.evidence_type != "GITHUB_AUTHENTICATED"
            or event.source_sha != registration.source_sha):
        raise DependencyHold("NATIVE_TURN_SCOPE_UNAVAILABLE")
    if (type(request) is not DoorbellRequest
            or request != DoorbellRequest.for_event(event, registration)):
        raise DependencyHold("NATIVE_TURN_REQUEST_MISMATCH")
    if type(origin) is not NativeOriginPins:
        raise DependencyHold("NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE")
    if origin.binding != registration.binding or origin.marker != registration.marker:
        raise DependencyHold("NATIVE_TURN_ORIGIN_MISMATCH")
    pins = cdp_turn.OriginPins(
        account_id=origin.account_id,
        conversation_id=_CHAT.fullmatch(registration.binding.chat_url).group(1),
        session_ref=origin.account_session_ref,
        route_generation=registration.binding.generation,
        registration_marker=registration.marker,
        browser_custody_ref=origin.chrome_custody_ref,
        chrome_profile_id=origin.chrome_profile_id,
        context_ref=origin.context_ref, tab_ref=origin.tab_ref,
        composer_ref=origin.composer_ref, cdp_endpoint=registration.binding.cdp_endpoint)
    context = cdp_turn.RegisteredTurnContext(
        chat_url=registration.binding.chat_url,
        windows_sid=origin.windows_sid,
        windows_session_id=origin.windows_session_id,
        windows_logon_id=origin.windows_logon_id,
        desktop=origin.desktop,
        event_payload_sha256=event.payload_sha256)
    scope = cdp_turn.TurnScope(pins, event.event_id, bytes_digest(request.canonical_bytes),
                              registered_context=context)
    return cdp_turn.WakeRequest(scope, request.canonical_bytes)


@dataclass(frozen=True, repr=False)
class AuthenticatedUserTurn:
    """Required remote-owner output shape; construction supplies NO authentication."""
    request: DoorbellRequest
    user_turn_id: str
    message_ref: str
    provenance_ref: str
    role: Literal["USER"]

    def __post_init__(self) -> None:
        _require(type(self.request) is DoorbellRequest and self.role == "USER"
                 and all(_reference(value) for value in
                         (self.user_turn_id, self.message_ref, self.provenance_ref)),
                 "AUTHENTICATED_USER_TURN_INVALID")


@dataclass(frozen=True, repr=False)
class Observation:
    receipt: WakeReceipt
    chat_url: str
    marker: str
    user_turn_ref: str
    native_turn: AuthenticatedUserTurn | None = None


@dataclass(frozen=True)
class NativeOriginAuthority:
    """Closed source-owned boundary, with no injectable authentication callback.

    trusted_origin.VerifiedNativeOriginEvidenceConsumer explicitly cannot attest
    a native ChatGPT account/session. Chrome custody and signed consent remain
    necessary independent checks, but neither can fill this missing owner.
    """
    def authenticate(self, registration: Registration,
                     candidate: NativeOriginPins | None = None) -> NativeOriginPins:
        raise DependencyHold("NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE")


@dataclass(frozen=True)
class NativeTurnAuthority:
    """Consume R3B2A rejection gates, with no enrolled private turn authority.

    Complete claims still terminate in UNAVAILABLE. Missing durable reservation
    evidence is a dependency before RESERVED, never a reason to create one to
    satisfy the gate. No caller callback, token or typed fixture permits Send.
    The remote adapter and actual private provider remain separate owner work.
    """
    def preflight(self, request: DoorbellRequest, *, registration: Registration | None = None,
                  event: Event | None = None, origin: NativeOriginPins | None = None,
                  evidence: cdp_turn.TurnEvidence | None = None, now: int | None = None) -> None:
        if (type(self) is not NativeTurnAuthority or registration is None or event is None):
            raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")
        mapped = _turn_request(registration, event, request, origin)
        try:
            gate = cdp_turn.pre_send(mapped, evidence, now=now)
        except Exception:
            raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE") from None
        if (type(gate) is cdp_turn.GateResult
                and gate.phase is cdp_turn.AttestationPhase.PRE_SEND
                and type(gate.code) is cdp_turn.GateCode):
            if gate.status is cdp_turn.GateStatus.HOLD:
                raise DependencyHold("DOORBELL_" + gate.code.value)
            if (gate.status is cdp_turn.GateStatus.UNAVAILABLE
                    and gate.code is cdp_turn.GateCode.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE):
                raise DependencyHold(gate.code.value)
        raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")

    def authenticate(self, request: DoorbellRequest,
                     candidate: AuthenticatedUserTurn | None, *,
                     registration: Registration | None = None, event: Event | None = None,
                     origin: NativeOriginPins | None = None) -> AuthenticatedUserTurn:
        if (type(self) is not NativeTurnAuthority or registration is None or event is None
                or candidate is None):
            raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")
        _turn_request(registration, event, request, origin)
        _require(type(candidate) is AuthenticatedUserTurn and candidate.request == request,
                 "AUTHENTICATED_USER_TURN_MISMATCH")
        # A matching claim still needs independent post-effect proof.
        raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")

    def post_reservation_code(self, *, outcome: cdp_turn.SendOutcome,
                              request: cdp_turn.WakeRequest | None = None,
                              evidence: cdp_turn.TurnEvidence | None = None,
                              user_turn: cdp_turn.ChatGPTUserTurnRef | None = None,
                              now: int | None = None) -> str:
        """Diagnostic for the existing RESERVED -> UNKNOWN_EFFECT transition only.

        Absent claims stay absent; never invent account/turn/reservation proof.
        R3B2A rejects an absent request for CLICK_REPORTED and independently
        classifies ambiguity/timeout. Even NOT_ATTEMPTED cannot undo a durable
        runner reservation or authorize replay through this post-effect path.
        """
        if type(self) is NativeTurnAuthority:
            try:
                gate = cdp_turn.post_send(request, evidence, outcome=outcome,
                                          user_turn=user_turn, now=now)
            except Exception:
                return "ATTEMPT_UNPROVEN_NO_REPLAY"
            if (type(gate) is cdp_turn.GateResult
                    and gate.phase is cdp_turn.AttestationPhase.POST_SEND
                    and gate.status is cdp_turn.GateStatus.UNKNOWN_EFFECT
                    and type(gate.code) is cdp_turn.GateCode):
                return "DOORBELL_" + gate.code.value
        return "ATTEMPT_UNPROVEN_NO_REPLAY"


class Sender(Protocol):
    """Trusted source adapter for readiness, a reserved effect and receipt lookup.

    Preflight is read-only. A code means readiness can be checked later. Send is
    called once after the durable reservation and the final authorization check.
    Reconcile may read evidence for that same reservation without staging,
    clicking, or initiating a new turn. None leaves the effect unknown.
    """
    evidence_type: Evidence

    def preflight(self, registration: Registration) -> str | None: ...
    def send(self, event: Event, registration: Registration) -> Observation: ...
    def reconcile(self, event: Event, registration: Registration) -> Observation | None: ...


def _chrome_scope(local: Path, session: int, sid: str,
                  env: chrome_acquisition.ChromeEnvironment) -> str:
    return digest([str(local), session, sid, str(env.config_path), env.command()])


class NativeChrome9444:
    """Read-only reuse of the enrolled 9444 owner, never launch/preload/Send.

    The existing Chrome owner supplies Windows inventory, custody, signature,
    session/SID and protocol checks. The existing doorbell inventory supplies
    the attach and surface read. Neither a URL nor an empty composer attests
    the originating account/marker. R3/HostOps must supply that missing native
    capability and a separate effect grant before any new tab or launch.
    """
    evidence_type = "GITHUB_AUTHENTICATED"

    def __init__(self, repository_root: Path,
                 environment: chrome_acquisition.ChromeEnvironment | None = None,
                 *, supervised: bool = False):
        self.repository_root = repository_root
        self.environment = environment
        self.supervised = supervised
        self.adapter = LocalChromeDoorbellAdapter(allow_submit=False)

    def _owner_snapshot(self) -> tuple:
        env = self.environment
        _require(type(env) is chrome_acquisition.ChromeEnvironment
                 and env.port == 9444 and env.endpoint == "http://127.0.0.1:9444",
                 "CDP_OWNER_MISMATCH")
        operator = chrome_acquisition._enrollment_operator
        local, session, sid = operator(attended=False) if self.supervised else operator()
        current = chrome_acquisition._read_9444_config(
            env.config_path, local, self.repository_root, sid)
        _require(current == env, "CHROME_ENROLLMENT_DRIFT")
        inventory = chrome_acquisition._query_windows(9444)
        # Evaluate first so a live profile without CDP remains a distinct HOLD,
        # rather than treating a missing listener or HTTP failure as clean OFF.
        state, _ = chrome_acquisition.evaluate(
            env, inventory, session_id=session, expected_sid=sid,
            parse=chrome_acquisition._commandline_argv)
        lifecycle_state, identity = chrome_acquisition._lifecycle_inventory(
            env, inventory, session=session, sid=sid)
        _require(lifecycle_state == state, "CHROME_OWNER_UNPROVEN")
        chrome_acquisition._require_operator_control(env)
        chrome_acquisition._check_enrollment_custody(env, local, sid)
        # The enrollment helper checks root markers; a selected-profile marker
        # also needs an independently proven holder, which R2 cannot supply.
        for name in chrome_acquisition._MARKERS:
            try:
                (env.user_data_dir / env.profile_directory / name).lstat()
            except FileNotFoundError:
                continue
            raise RuntimeHold("PROFILE_LOCK_HOLD")
        if state == "OWNED":
            chrome_acquisition._probe_cdp(env)
        _require(state in ("ABSENT", "OWNED"), "CHROME_OWNER_UNPROVEN")
        return state, (local, session, sid, env, identity)

    def lifecycle_snapshot(self) -> tuple:
        """Read the native launch marker around two independent owner observations."""
        observed = self._owner_snapshot()
        pending = chrome_acquisition._clean_launch_pending()
        _require(self._owner_snapshot() == observed
                 and chrome_acquisition._clean_launch_pending() == pending,
                 "CHROME_OBSERVATION_CHANGED")
        return (*observed, pending)

    def _exact_chat_candidate(self, registration: Registration, *, exclusive: bool = False) -> str:
        inventory = self.adapter.inventory
        if not callable(getattr(inventory, "contexts", None)):
            return "EXACT_CHAT_CONTEXT_UNPROVEN"
        binding = registration.binding
        try:
            with inventory.contexts(binding.cdp_endpoint) as contexts:
                if len(contexts) != 1:
                    return "EXACT_CHAT_CONTEXT_AMBIGUOUS"
                pages = list(contexts[0].pages)
                if len(pages) > 32:
                    return "EXACT_CHAT_INVENTORY_UNBOUNDED"
                if exclusive and len(pages) != 1:
                    return "EXACT_CHAT_SINGLE_TAB_REQUIRED"
                matches = [p for p in pages if type(getattr(p, "url", None)) is str
                           and p.url.rstrip("/") == binding.chat_url]
                if len(matches) > 1:
                    return "EXACT_CHAT_TAB_AMBIGUOUS"
                if not matches:
                    # resolve_claim_route is not a native navigation grant.
                    # Never call preload(), even if allow_launch is configured.
                    return "EXACT_CHAT_ABSENT_NATIVE_PRELOAD_CAPABILITY_REQUIRED"
                page = matches[0]
                code = cdp_doorbell._preload_surface_code(page)
                if page.url.rstrip("/") != binding.chat_url:
                    return "EXACT_CHAT_URL_DRIFT"
                if code != "CANDIDATE_READY":
                    return code
                if exclusive:
                    # R3A requires two empty, stable surface reads on one tab.
                    code = cdp_doorbell._preload_surface_code(page)
                    if page.url.rstrip("/") != binding.chat_url:
                        return "EXACT_CHAT_URL_DRIFT"
                    if code != "CANDIDATE_READY":
                        return code
                return "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED"
        except Exception:
            return "CDP_READ_UNAVAILABLE"

    def preflight(self, registration: Registration, *, exclusive: bool = False,
                  owner_scope: str | None = None) -> str:
        if self.environment is None:
            return "UNSUPPORTED_NATIVE_OWNER"
        observed = False
        try:
            _require(registration.evidence_type == self.evidence_type
                     and registration.binding.cdp_endpoint == self.environment.endpoint,
                     "CDP_OWNER_MISMATCH")
            before = self._owner_snapshot()
            observed = True
            # Two identical native observations are needed even for clean OFF.
            if self._owner_snapshot() != before:
                return "CHROME_OBSERVATION_CHANGED"
            if owner_scope is not None and _chrome_scope(*before[1][:4]) != owner_scope:
                return "CHROME_OWNER_SCOPE_CHANGED"
            if before[0] == "ABSENT":
                return "CHROME_NOT_RUNNING_NATIVE_LAUNCH_CAPABILITY_REQUIRED"
            code = self._exact_chat_candidate(registration, exclusive=exclusive)
            if self._owner_snapshot() != before:
                return "CHROME_OBSERVATION_CHANGED"
            return code
        except DependencyHold:
            raise
        except (chrome_acquisition.AcquisitionBlocked, chrome_acquisition._EnrollmentHold,
                chrome_acquisition._Lifecycle9444Hold,
                RuntimeHold, HandoffBlocked) as exc:
            return ("CHROME_OBSERVATION_UNKNOWN" if observed
                    else _safe_code(exc, "CHROME_OWNER_UNPROVEN"))
        except Exception:
            return "CHROME_OBSERVATION_UNKNOWN" if observed else "CHROME_OWNER_UNPROVEN"

    def send(self, event: Event, registration: Registration) -> Observation:
        raise RuntimeHold("UNSUPPORTED_NATIVE_OWNER")

    def reconcile(self, event: Event, registration: Registration) -> None:
        return None


class NativeSender:
    """R3B2B rejection gates for the existing doorbell; no native effects.

    LocalChromeObserverAdapter.capture_origin only reads caller-enrolled identity;
    VerifiedNativeOriginEvidenceConsumer.is_complete_origin_attested is always
    False. Neither API supplies an enrolled immutable account/session/Chat A/
    marker verifier. WH03 test markers and native_authority flags cannot fill it.

    This is the fixed native implementation of the Sender seam. An enrolled
    origin owner must prove account/session and surface scope before reservation.
    An enrolled turn provider must bind the durable reservation to the exact
    request before dispatch and independently authenticate USER-turn receipts.
    The legacy doorbell supplies only a UI observation.
    """
    evidence_type = "GITHUB_AUTHENTICATED"

    def __init__(self, repository_root: Path, registration: Registration,
                 environment: chrome_acquisition.ChromeEnvironment | None = None):
        _require(registration.evidence_type == self.evidence_type, "EVIDENCE_MODE_MISMATCH")
        self.registration = registration
        self.owner = NativeChrome9444(repository_root, environment)
        self.origin_authority = NativeOriginAuthority()
        self.turn_authority = NativeTurnAuthority()

    def _origin(self, registration: Registration) -> NativeOriginPins:
        if type(self.origin_authority) is not NativeOriginAuthority:
            raise DependencyHold("NATIVE_ORIGIN_AUTHORITY_UNAVAILABLE")
        return NativeOriginAuthority.authenticate(self.origin_authority, registration)

    def preflight(self, registration: Registration) -> str:
        _require(registration == self.registration, "REGISTRATION_DRIFT")
        code = self.owner.preflight(registration, exclusive=True)
        if code != "EXACT_CHAT_CANDIDATE_ACCOUNT_MARKER_UNATTESTED":
            return code
        self._origin(registration)
        raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")

    def require_dependencies(self, event: Event, registration: Registration,
                             authorization: SignedWakeAuthorization) -> None:
        """Runner gate before RESERVED, independent of a sender readiness flag."""
        _require(registration == self.registration, "REGISTRATION_DRIFT")
        if (type(authorization) is not SignedWakeAuthorization
                or authorization.event != event or authorization.registration != registration
                or authorization.operations != ("resolve_claim_route", "deliver_wake")):
            raise DependencyHold("SIGNED_WAKE_AUTHORITY_UNAVAILABLE")
        origin = NativeSender._origin(self, registration)
        NativeSender.require_turn_reference(self, DoorbellRequest.for_event(event, registration),
                                            event=event, origin=origin)

    def require_turn_reference(self, request: DoorbellRequest, *, event: Event | None = None,
                               origin: NativeOriginPins | None = None,
                               evidence: cdp_turn.TurnEvidence | None = None,
                               now: int | None = None) -> None:
        if type(self.turn_authority) is not NativeTurnAuthority:
            raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")
        NativeTurnAuthority.preflight(self.turn_authority, request,
                                      registration=self.registration, event=event,
                                      origin=origin, evidence=evidence, now=now)

    def authenticate_observation(self, event: Event, registration: Registration,
                                 observation: Observation) -> AuthenticatedUserTurn:
        candidate = observation.native_turn
        if type(self.turn_authority) is not NativeTurnAuthority or candidate is None:
            raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")
        _require(registration == self.registration, "REGISTRATION_DRIFT")
        request = DoorbellRequest.for_event(event, registration)
        _require(type(candidate) is AuthenticatedUserTurn and candidate.request == request
                 and candidate.message_ref == observation.user_turn_ref, "AUTHENTICATED_USER_TURN_MISMATCH")
        origin = NativeSender._origin(self, registration)
        turn = NativeTurnAuthority.authenticate(self.turn_authority, request, candidate,
                                                registration=registration, event=event, origin=origin)
        _require(type(turn) is AuthenticatedUserTurn and turn == candidate, "AUTHENTICATED_USER_TURN_MISMATCH")
        return turn

    def send(self, event: Event, registration: Registration) -> Observation:
        # A source-only seam must fail closed even if preflight is bypassed.
        raise DependencyHold("DOORBELL_AUTHENTICATED_TURN_REF_UNAVAILABLE")

    def reconcile(self, event: Event, registration: Registration) -> None:
        # Await a private, read-only lookup bound to the original reservation.
        return None


@dataclass(frozen=True, repr=False)
class ProductionHandoff:
    completed: ProductionStageCompleted
    source_bytes: bytes
    input_json: str
    output_json: str
    receipt: ProductionStageReceipt


@dataclass(frozen=True, repr=False)
class AuthenticatedBrainReceipt:
    """Required independent transport pins, never authority from caller hashes."""
    event_id: str
    event_key: str
    marker: str
    user_turn_id: str
    user_turn_ref: str
    route_generation: int
    provenance_ref: str
    authorization_ref: str
    completed_sha256: str
    consumer_id: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        _require(type(self.route_generation) is int and self.route_generation > 0
                 and all(_reference(getattr(self, name)) for name in
                         ("event_id", "event_key", "marker", "user_turn_id", "user_turn_ref",
                          "provenance_ref", "authorization_ref", "consumer_id"))
                 and all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None
                         for value in (self.completed_sha256, self.receipt_sha256)),
                 "AUTHENTICATED_BRAIN_RECEIPT_INVALID")


@dataclass(frozen=True, repr=False)
class BrainCorrelation:
    event_key: str
    marker: str
    user_turn_ref: str
    correlation_ref: str
    evidence_type: Evidence
    handoff: ProductionHandoff
    authenticated_receipt: AuthenticatedBrainReceipt | None = None


class BrainAdapter(Protocol):
    """Independently authenticate Brain correlation AND consumer-specific receipt.

    A UI user turn, assistant text, or callback flag is insufficient. The caller
    supplies a scoped P0 orchestrator with independently verified receipt pins;
    the runner never constructs a global production orchestrator or real pins.
    Correlation reads an existing response and may be retried after delivery.
    """
    def correlate(self, event: Event, registration: Registration,
                  observation: Observation) -> BrainCorrelation | None: ...


@dataclass(frozen=True)
class NativeBrainAdapter:
    """Closed boundary until an independent authenticated Brain transport exists.

    No existing owner authenticates event/marker/user-turn-bound completion and
    Product receipts or enrolls their provenance/receipt pins. P0 caller-supplied
    hashes, assistant text, DOM and mock correlations do not provide authority.
    A future owner must supply that transport before changing this boundary.
    """
    def correlate(self, event: Event, registration: Registration,
                  observation: Observation) -> None:
        return None  # Runner reports BRAIN_RECEIPT_UNAVAILABLE, never ACK.

    def authenticate_receipt(self, event: Event, registration: Registration,
                             observation: Observation,
                             candidate: AuthenticatedBrainReceipt | None) -> AuthenticatedBrainReceipt:
        # A typed receipt or orchestrator pin supplied by a caller is not an
        # independent Brain/consumer transport, even if all hashes match.
        raise DependencyHold("BRAIN_RECEIPT_UNAVAILABLE")


@dataclass(frozen=True)
class Result:
    status: Status
    code: str
    evidence_type: str
    replayed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, kw_only=True)
class SupervisorTickResult(Result):
    chrome_health: str


def _private_path(path: Path, repository_root: Path) -> None:
    """No checkout, links, reparse points, hard links, UNC or alternate streams."""
    _require(path.is_absolute() and repository_root.is_absolute()
             and ".." not in path.parts and not path.drive.startswith("\\\\")
             and ":" not in path.name and not path.name.endswith((".", " ")),
             "JOURNAL_LOCATION_INVALID")
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            _require(not _linked(part.lstat()), "JOURNAL_LOCATION_INVALID")
    for root in (repository_root.resolve(), _CODE_ROOT):
        _require(path.resolve() != root and root not in path.resolve().parents,
                 "JOURNAL_LOCATION_INVALID")
    _require(path.parent.is_dir() and (not path.exists() or _regular(path.lstat())),
             "JOURNAL_LOCATION_INVALID")


@contextmanager
def _private_access(path: Path, repository_root: Path):
    """Pin ancestors while SQLite opens its own handles; never write JSON state."""
    handles = []
    try:
        _private_path(path, repository_root)
        if os.name == "nt":
            for part in reversed(path.parents):
                handles.append(_windows_handle(part, 0, directory=True))
            directory_fd = None
        else:
            directory_fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            handles.append(directory_fd)
            for part in path.parent.parts[1:]:
                directory_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=directory_fd)
                handles.append(directory_fd)
        owner = _private_identity(path.parent)
        if path.exists():
            _require(_private_identity(path) == owner, "JOURNAL_OWNER_DRIFT")
        # Reuse only D5's safe handle opener, never its JSON read/write path.
        yield owner, _Transaction(SimpleNamespace(path=path), directory_fd,
                                  new_file_owner_sid=owner if os.name == "nt" else None)
    finally:
        for handle in reversed(handles):
            if os.name == "nt":
                handle[1](handle[0])
            else:
                os.close(handle)


_TABLES = {
    "identity": "CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "owner TEXT NOT NULL, registration TEXT NOT NULL)",
    "events": "CREATE TABLE events (event_key TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, "
              "source_sha TEXT NOT NULL, attempt INTEGER NOT NULL, generation INTEGER NOT NULL, "
              "record TEXT NOT NULL)",
}
_CHROME_TABLE = ("CREATE TABLE chrome_lifecycle (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                 "record TEXT NOT NULL)")
_APP_ID = 0x57485231


def _validate_chrome(record: dict) -> None:
    _require(type(record) is dict and set(record) == {"scope", "process", "launch"}
             and all(record[k] is None or (type(record[k]) is str
                     and re.fullmatch(r"[a-f0-9]{64}", record[k])) for k in ("scope", "process"))
             and record["launch"] in (None, "UNKNOWN", "REPORTED")
             and (record["scope"] is not None or
                  (record["process"] is None and record["launch"] != "REPORTED")),
             "CHROME_JOURNAL_INVALID")


def _validate_records(data: dict, registration: Registration) -> None:
    _require(type(data["events"]) is dict and len(data["events"]) <= 128, "JOURNAL_INVALID")
    seen, seen_turns, seen_native_turns = set(), set(), set()
    for key, record in data["events"].items():
        _require(type(record) is dict and set(record) == {"event", "status", "code", "history", "evidence"}
                 and type(record["event"]) is dict, "JOURNAL_INVALID")
        try:
            saved = Event(**record["event"])
        except (TypeError, RuntimeHold):
            raise RuntimeHold("JOURNAL_INVALID") from None
        _require(saved.key(registration) == key and saved.event_id not in seen
                 and type(record["history"]) is list and 1 <= len(record["history"]) <= 6
                 and record["history"][0] == "PENDING" and record["status"] == record["history"][-1]
                 and type(record["code"]) is str
                 and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", record["code"]) is not None
                 and type(record["evidence"]) is dict, "JOURNAL_INVALID")
        seen.add(saved.event_id)
        _require(all(b in _NEXT.get(a, set()) for a, b in
                     zip(record["history"], record["history"][1:])), "JOURNAL_INVALID")
        proof = record["evidence"]
        delivery = _DELIVERY_STATES.intersection(record["history"])
        if delivery:
            _require(sum(state in _DELIVERY_STATES for state in record["history"]) == 1
                     and saved.evidence_type == registration.evidence_type
                     and saved.source_sha == registration.source_sha
                     and proof.get("marker") == registration.marker
                     and _reference(proof.get("user_turn_ref")),
                     "JOURNAL_PROOF_MISSING")
            _require(proof["user_turn_ref"] not in seen_turns, "USER_TURN_REFERENCE_REUSED")
            seen_turns.add(proof["user_turn_ref"])
        if "USER_TURN_AUTHENTICATED" in delivery or "user_turn_id" in proof:
            request = DoorbellRequest.for_event(saved, registration)
            _require(bool(delivery) and saved.evidence_type == "GITHUB_AUTHENTICATED"
                     and _reference(proof.get("user_turn_id"))
                     and _reference(proof.get("turn_provenance_ref"))
                     and type(proof.get("route_generation")) is int
                     and proof["route_generation"] == request.binding.generation
                     and proof.get("doorbell_sha256") == bytes_digest(request.canonical_bytes),
                     "JOURNAL_PROOF_INVALID")
            _require(proof["user_turn_id"] not in seen_native_turns, "USER_TURN_REFERENCE_REUSED")
            seen_native_turns.add(proof["user_turn_id"])
        if "BRAIN_CORRELATED" in record["history"]:
            _require(all(type(proof.get(k)) is str and bool(proof[k]) for k in
                         ("correlation_ref", "consumer_id", "receipt_sha256", "completed_sha256")),
                     "JOURNAL_PROOF_MISSING")
        if record["status"] == "ACK" or "consumer_receipt" in proof or "completed" in proof:
            _require(type(proof.get("consumer_receipt")) is dict
                     and type(proof.get("completed")) is dict, "JOURNAL_PROOF_MISSING")
            try:
                receipt = ProductionStageReceipt.from_dict(proof["consumer_receipt"])
                completed = ProductionStageCompleted.from_dict(proof["completed"])
            except (OrchestrationError, TypeError, KeyError, ValueError):
                raise RuntimeHold("JOURNAL_PROOF_INVALID") from None
            expected_kind = "SYNTHETIC_FIXTURE" if saved.evidence_type == "SYNTHETIC_FIXTURE" else "HUMAN_SUPPLIED"
            _require("BRAIN_CORRELATED" in record["history"]
                     and (record["status"] != "ACK" or record["code"] == "CONSUMER_RECEIPT_RECORDED")
                     and receipt.sha256 == proof["receipt_sha256"]
                     and completed.sha256 == proof["completed_sha256"]
                     and receipt.completed_sha256 == completed.sha256
                     and receipt.spec == completed.spec
                     and receipt.consumer_id == proof["consumer_id"]
                     and completed.spec.idempotency_key == key
                     and completed.spec.provenance.source_ref == "wakehub-event:" + key
                     and receipt.provenance.source_ref == "wakehub-event:" + key
                     and receipt.provenance.evidence_type == completed.spec.provenance.evidence_type == expected_kind
                     and completed.source_sha256 == bytes_digest(
                         json.dumps(asdict(saved), sort_keys=True).encode("utf-8")),
                     "JOURNAL_PROOF_INVALID")


class _SQLiteTransaction:
    def __init__(self, connection, journal, registration, owner):
        self.connection, self.journal = connection, journal
        self.registration, self.owner = registration, owner

    def write(self, data: dict) -> None:
        _validate_records(data, self.registration)
        self.journal._owner(self.owner)
        db = self.connection
        try:
            db.execute("BEGIN IMMEDIATE")
            for key, record in data["events"].items():
                event = record["event"]
                db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?) "
                           "ON CONFLICT(event_key) DO UPDATE SET record=excluded.record",
                           (key, event["event_id"], event["source_sha"], event["attempt"],
                            self.registration.binding.generation, json.dumps(record, sort_keys=True)))
            # Includes the rollback sidecar created by SQLite, before commit.
            self.journal._owner(self.owner)
            db.commit()  # FULL/EXTRA synchronous; no effect may precede this commit.
        except BaseException:
            db.rollback()
            raise

    def write_chrome(self, record: dict, *, migrate: bool = False) -> None:
        _validate_chrome(record)
        self.journal._owner(self.owner)
        db = self.connection
        try:
            db.execute("BEGIN IMMEDIATE")
            if migrate:
                db.execute(_CHROME_TABLE)
                db.execute("PRAGMA user_version=3")
            db.execute("INSERT INTO chrome_lifecycle VALUES (1, ?) "
                       "ON CONFLICT(singleton) DO UPDATE SET record=excluded.record",
                       (json.dumps(record, sort_keys=True),))
            self.journal._owner(self.owner)
            db.commit()
        except BaseException:
            db.rollback()
            raise


class Journal:
    """One SQLite database, private custody and one OS lock across an iteration.

    Individual state transitions commit durably before crossing an effect seam.
    JSON is refused, not migrated implicitly. Existing empty/corrupt databases
    are never initialized. Missing databases are enrolled only under the lock.
    """
    def __init__(self, path: Path, repository_root: Path):
        self.path, self.repository_root = Path(path), Path(repository_root)
        _private_path(self.path, self.repository_root)
        _require(not self.path.name.endswith((".lock", ".part", "-journal", "-wal", "-shm")),
                 "JOURNAL_LOCATION_INVALID")

    def _owner(self, expected: str) -> None:
        _require(_private_identity(self.path.parent) == expected, "JOURNAL_OWNER_DRIFT")
        for suffix in ("", ".lock", "-journal", "-wal", "-shm"):
            path = self.path.with_name(self.path.name + suffix)
            _private_path(path, self.repository_root)
            if path.exists():
                _require(_private_identity(path) == expected, "JOURNAL_OWNER_DRIFT")
                _require(suffix not in ("-wal", "-shm"), "JOURNAL_SCHEMA_INCOMPATIBLE")

    @contextmanager
    def locked(self, registration: Registration):
        db, lock_fd, unlock = None, None, None
        try:
            with _private_access(self.path, self.repository_root) as (owner, files):
                self._owner(owner)
                lock_existed = self.path.with_name(self.path.name + ".lock").exists()
                lock_fd = files.open(self.path.name + ".lock", os.O_RDWR | os.O_CREAT)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                        unlock = lambda: (os.lseek(lock_fd, 0, os.SEEK_SET),
                                          msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1))
                    else:
                        import fcntl
                        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        unlock = lambda: fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    raise RuntimeHold("JOURNAL_BUSY") from None
                self._owner(owner)
                new = not self.path.exists()
                if new:
                    _require(not lock_existed
                             and not self.path.with_name(self.path.name + "-journal").exists(),
                             "JOURNAL_PROOF_MISSING")
                    fd = files.open(self.path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                    os.close(fd)
                else:
                    fd = files.open(self.path.name, os.O_RDONLY)
                    with os.fdopen(fd, "rb") as stream:
                        header = stream.read(16)
                    _require(not header.lstrip().startswith((b"{", b"[")), "JOURNAL_MIGRATION_REQUIRED")
                    _require(header == b"SQLite format 3\x00", "JOURNAL_CORRUPT")
                self._owner(owner)
                db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0,
                                     isolation_level=None)
                db.execute("PRAGMA trusted_schema=OFF")
                _require(db.execute("PRAGMA quick_check").fetchall() == [("ok",)], "JOURNAL_CORRUPT")
                version = 2
                if not new:
                    version, = db.execute("PRAGMA user_version").fetchone()
                    tables = dict(_TABLES, chrome_lifecycle=_CHROME_TABLE) if version == 3 else _TABLES
                    _require(version in (2, 3)
                             and db.execute("PRAGMA application_id").fetchone() == (_APP_ID,)
                             and db.execute("SELECT type, name, sql FROM sqlite_master "
                                            "WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                             == [("table", name, sql) for name, sql in sorted(tables.items())],
                             "JOURNAL_SCHEMA_INCOMPATIBLE")
                    rows = db.execute("SELECT owner, registration FROM identity WHERE singleton=1").fetchall()
                    _require(len(rows) == 1 and rows[0][0] == owner, "JOURNAL_OWNER_DRIFT")
                    _require(_decode_unique(rows[0][1]) == asdict(registration), "REGISTRATION_DRIFT")
                # Provision the persistent rollback sidecar with the same owner.
                fd = files.open(self.path.name + "-journal", os.O_RDWR | os.O_CREAT)
                os.close(fd)
                _require(db.execute("PRAGMA journal_mode=PERSIST").fetchone() == ("persist",),
                         "JOURNAL_SCHEMA_INCOMPATIBLE")
                db.execute("PRAGMA synchronous=EXTRA")
                if new:
                    db.execute("BEGIN IMMEDIATE")
                    for sql in _TABLES.values():
                        db.execute(sql)
                    db.execute("INSERT INTO identity VALUES (1, ?, ?)",
                               (owner, json.dumps(asdict(registration), sort_keys=True)))
                    db.execute("PRAGMA user_version=2")
                    db.execute(f"PRAGMA application_id={_APP_ID}")
                    self._owner(owner)
                    db.commit()
                    if os.name != "nt":
                        os.fsync(files.directory_fd)
                data = {"events": {}}
                for key, event_id, sha, attempt, generation, raw in db.execute("SELECT * FROM events"):
                    record = _decode_unique(raw)
                    _require(type(record) is dict and type(record.get("event")) is dict, "JOURNAL_INVALID")
                    event = record["event"]
                    _require((event_id, sha, attempt, generation) ==
                             (event.get("event_id"), event.get("source_sha"), event.get("attempt"),
                              registration.binding.generation), "JOURNAL_INVALID")
                    data["events"][key] = record
                _validate_records(data, registration)
                if version == 3:
                    rows = db.execute("SELECT singleton, record FROM chrome_lifecycle").fetchall()
                    _require(len(rows) == 1 and rows[0][0] == 1, "CHROME_JOURNAL_INVALID")
                    data["chrome"] = _decode_unique(rows[0][1])
                    _validate_chrome(data["chrome"])
                yield _SQLiteTransaction(db, self, registration, owner), data
                self._owner(owner)
        except sqlite3.DatabaseError as exc:
            code = "JOURNAL_BUSY" if isinstance(exc, sqlite3.OperationalError) and \
                getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) \
                else "JOURNAL_CORRUPT"
            raise RuntimeHold(code) from None
        finally:
            if db is not None:
                db.close()
            if unlock is not None:
                unlock()
            if lock_fd is not None:
                os.close(lock_fd)


class Runner:
    """Process one validated event under the journal lock."""

    def __init__(self, *, source: EventSource, registration: Registration, journal: Journal,
                 sender: Sender, orchestrator: ProductionPipelineOrchestrator,
                 grant_verifier: Callable[[Event, Registration], bool],
                 brain: BrainAdapter | None = None, read_only: bool = False,
                 supervisor_stop: Callable[[], bool] | None = None):
        _require(type(orchestrator) is ProductionPipelineOrchestrator, "PRODUCTION_ORCHESTRATOR_REQUIRED")
        _require(not (read_only and supervisor_stop is not None), "SUPERVISOR_MODE_INVALID")
        _require(supervisor_stop is None or registration.evidence_type == "GITHUB_AUTHENTICATED",
                 "SUPERVISOR_MODE_INVALID")
        self.source, self.registration, self.journal = source, registration, journal
        self.sender, self.brain, self.orchestrator = sender, brain, orchestrator
        self.grant_verifier = grant_verifier
        self.read_only = read_only
        self._supervisor_stop = supervisor_stop

    @property
    def _observe_only(self) -> bool:
        return self.read_only or self._supervisor_stop is not None

    @property
    def _native(self) -> bool:
        return not self._observe_only and self.registration.evidence_type == "GITHUB_AUTHENTICATED"

    def run_once(self) -> Result:
        if self._supervisor_stop is None:
            return self._run_event()
        chrome = self._supervise_chrome()
        if self._supervisor_stop():
            return chrome
        event = self._run_event(chrome)
        return SupervisorTickResult(event.status, event.code, event.evidence_type, event.replayed,
                                    chrome_health=chrome.code)

    def _run_event(self, chrome: Result | None = None) -> Result:
        evidence = self.registration.evidence_type
        try:
            event = self.source.next_event()
            if event is None:
                if self.journal.path.exists():
                    with self.journal.locked(self.registration) as (txn, data):
                        self._recover_reservations(txn, data)
                return Result("HOLD", "NO_EVENT", evidence)
            _require(type(event) is Event, "EVENT_REQUIRED")
            with self.journal.locked(self.registration) as (txn, data):
                key = event.key(self.registration)
                records = data["events"]
                if key in records:
                    record = records[key]
                    _require(record["event"] == asdict(event), "EVENT_IDENTITY_DRIFT")
                else:
                    _require(not any(r["event"]["event_id"] == event.event_id for r in records.values()),
                             "EVENT_IDENTITY_DRIFT")
                    _require(len(records) < 128, "JOURNAL_FULL")
                    record = {"event": asdict(event), "status": "PENDING", "code": "EVENT_PENDING",
                              "history": ["PENDING"], "evidence": {}}
                    records[key] = record
                    txn.write(data)
                self._recover_reservations(txn, data)
                if record["status"] != "PENDING":
                    return self._resume(txn, data, record, event)
                return self._process_pending(txn, data, record, event, chrome)
        except DependencyHold as exc:
            return Result("HOLD_DEPENDENCY", _safe_code(exc, "NATIVE_DEPENDENCY_UNAVAILABLE"), evidence)
        except RuntimeHold as exc:
            return Result("HOLD", str(exc), evidence)
        except (ProvenanceError, TriggerBlocked, PrivateStateError, HandoffBlocked,
                BindingError, RegistrationBlocked, GitHubReadBlocked) as exc:
            return Result("HOLD", _safe_code(exc, "SOURCE_OR_JOURNAL_UNPROVEN"), evidence)
        except Exception:
            return Result("HOLD", "SOURCE_OR_JOURNAL_UNPROVEN", evidence)

    @staticmethod
    def _recover_reservations(txn, data: dict) -> None:
        interrupted = [record for record in data["events"].values() if record["status"] == "RESERVED"]
        for record in interrupted:
            record.update(status="UNKNOWN_EFFECT", code="INTERRUPTED_NO_REPLAY",
                          history=[*record["history"], "UNKNOWN_EFFECT"])
        if interrupted:
            txn.write(data)

    def _check_composition(self) -> None:
        if self.registration.evidence_type != "GITHUB_AUTHENTICATED":
            _require(not self.read_only, "READ_ONLY_COMPOSITION_INVALID")
            return
        native_owner = NativeChrome9444 if self._observe_only else NativeSender
        if type(self.sender) is not native_owner:
            raise DependencyHold("UNSUPPORTED_NATIVE_OWNER")
        _require(type(self.source) is GitHubEventSource, "AUTHENTICATED_SOURCE_REQUIRED")
        if self._native or self._supervisor_stop is not None:
            if (type(self.grant_verifier) is not _RegisteredWakeAuthority
                    or self.grant_verifier.native_source_only is not self._native
                    or self.grant_verifier.source is not self.source):
                raise DependencyHold("SIGNED_WAKE_AUTHORITY_UNAVAILABLE")
        if self._native:
            # Reject a substituted native response owner. Omitting response
            # acquisition, or using the closed source owner, permits Send-only.
            if self.brain is not None and type(self.brain) is not NativeBrainAdapter:
                raise DependencyHold("BRAIN_RECEIPT_UNAVAILABLE")

    def _process_pending(self, txn, data: dict, record: dict, event: Event,
                         chrome: Result | None = None) -> Result:
        registration = self.registration
        evidence = registration.evidence_type
        if event.source_sha != registration.source_sha:
            return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "SOURCE_DRIFT")
        if event.evidence_type != evidence or self.sender.evidence_type != evidence:
            return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "EVIDENCE_MODE_MISMATCH")
        if record["code"] == _LAUNCH_UNKNOWN and chrome is None:
            return Result("UNKNOWN_EFFECT", _LAUNCH_UNKNOWN, evidence, replayed=True)
        self._check_composition()
        try:
            authorization = self.grant_verifier.authorize(event, registration) if self._native else None
            granted = (authorization is not None if self._native else
                       self.grant_verifier(event, registration) is True)
            if not granted:
                if self._native:
                    return Result("HOLD_DEPENDENCY", "MISSING_GRANT", evidence)
                if self._observe_only:
                    return Result("HOLD", "MISSING_GRANT", evidence)
                return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "MISSING_GRANT")
            if chrome is not None and chrome.code not in _CHROME_READY:
                return chrome
            if self._supervisor_stop is not None and self._supervisor_stop():
                return Result("HOLD", "SUPERVISOR_STOPPED", evidence)
            code = (self.sender.preflight(registration) if chrome is None else
                    self.sender.preflight(registration, owner_scope=data["chrome"]["scope"]))
            valid_code = type(code) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) is not None
            _require(valid_code or (code is None and not self._observe_only), "PREFLIGHT_RESULT_INVALID")
            if self._observe_only:
                _require(self.grant_verifier(event, registration) is True, "MISSING_GRANT")
                return Result("HOLD", code, evidence)
            if code is not None:
                if self._native and self.grant_verifier(event, registration) is not True:
                    return Result("HOLD_DEPENDENCY", "MISSING_GRANT", evidence)
                return Result("HOLD", code, evidence)
            if self._native:
                NativeSender.require_dependencies(self.sender, event, registration, authorization)
        except DependencyHold as exc:
            # Check for private authority drift during read-only observations.
            if self.grant_verifier(event, registration) is not True:
                raise DependencyHold("MISSING_GRANT")
            return Result("HOLD_DEPENDENCY", _safe_code(exc, "NATIVE_DEPENDENCY_UNAVAILABLE"), evidence)
        except (RuntimeHold, BindingError, HandoffBlocked) as exc:
            return Result("HOLD", _safe_code(exc, "PREFLIGHT_UNPROVEN"), evidence)
        except Exception:
            return Result("HOLD", "PREFLIGHT_UNPROVEN", evidence)

        self._move(txn, data, record, "RESERVED", "SEND_RESERVED")
        try:
            _require(self.grant_verifier(event, registration) is True, "MISSING_GRANT")
        except (RuntimeHold, BindingError, HandoffBlocked) as exc:
            return self._finish(txn, data, record, "BLOCKED_EXTERNAL",
                                _safe_code(exc, "FINAL_AUTHORITY_UNPROVEN"))
        except Exception:
            return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "FINAL_AUTHORITY_UNPROVEN")

        try:
            observation = self.sender.send(event, registration)
            status, proof = self._delivery_proof(data, record, event, observation)
        except TimeoutError:
            return self._finish(txn, data, record, "UNKNOWN_EFFECT",
                                self._effect_code(cdp_turn.SendOutcome.TRANSPORT_TIMEOUT))
        except RuntimeHold as exc:
            return self._finish(txn, data, record, "UNKNOWN_EFFECT",
                                self._effect_code(cdp_turn.SendOutcome.CLICK_REPORTED, exc))
        except Exception:
            return self._finish(txn, data, record, "UNKNOWN_EFFECT",
                                self._effect_code(cdp_turn.SendOutcome.CLICK_AMBIGUOUS))

        self._save_delivery(txn, data, record, status, proof)
        return self._respond(txn, data, record, event, observation, replayed=False)

    def _supervise_chrome(self) -> Result:
        evidence = self.registration.evidence_type
        try:
            self._check_composition()
            if self._supervisor_stop():
                return Result("HOLD", "SUPERVISOR_STOPPED", evidence)
            with self.journal.locked(self.registration) as (txn, data):
                saved = data.get("chrome")
                if saved is None:
                    # Legacy receipts have no process or session witness. Keep
                    # the original event rows and fence starts until reconciled.
                    legacy = any(r["code"] in (_LAUNCH_UNKNOWN, _ACQUISITION_REPORTED)
                                 for r in data["events"].values())
                    saved = {"scope": None, "process": None, "launch": "UNKNOWN" if legacy else None}
                    txn.write_chrome(saved, migrate=True)
                snapshot = self.sender.lifecycle_snapshot()
                code = self._reconcile_chrome(txn, saved, snapshot)
                if code is None:
                    code = self._acquire_chrome(txn, saved, snapshot)
            return Result("UNKNOWN_EFFECT" if code == _LAUNCH_UNKNOWN else "HOLD", code, evidence)
        except DependencyHold as exc:
            return Result("HOLD_DEPENDENCY", _safe_code(exc, "NATIVE_DEPENDENCY_UNAVAILABLE"), evidence)
        except (chrome_acquisition.AcquisitionBlocked, chrome_acquisition._EnrollmentHold,
                chrome_acquisition._Lifecycle9444Hold, chrome_acquisition._Clean9444Hold,
                RuntimeHold, HandoffBlocked) as exc:
            return Result("HOLD", _safe_code(exc, "CHROME_OWNER_UNPROVEN"), evidence)
        except Exception:
            return Result("HOLD", "CHROME_OBSERVATION_UNKNOWN", evidence)

    @staticmethod
    def _reconcile_chrome(txn, saved: dict, snapshot: tuple) -> str | None:
        state, (local, session, sid, env, inventory), pending = snapshot
        scope = _chrome_scope(local, session, sid, env)
        launch = saved["launch"]
        if saved["scope"] not in (None, scope):
            if launch == "REPORTED":
                txn.write_chrome(dict(saved, launch="UNKNOWN"))
            return "CHROME_OWNER_SCOPE_CHANGED"

        process = None
        if state == "OWNED":
            listeners, processes = inventory
            pid = listeners[0][2]
            created = next(row[-1] for row in processes if row[0] == pid)
            process = digest([pid, chrome_acquisition._creation_ticks(created)])
        if pending or (launch == "REPORTED" and process is not None and process == saved["process"]):
            launch = "UNKNOWN"
        elif launch == "REPORTED" and process is not None:
            # The owner reported completion and independently verified Chrome
            # is present in the same scope. Presence alone never clears UNKNOWN.
            launch = None
        current = dict(saved, launch=launch)
        if process is not None or pending:
            current.update(scope=scope, process=process or saved["process"])
        if current != saved:
            txn.write_chrome(current)
            saved.update(current)
        if process is not None:
            return "CHROME_OWNED" if launch is None else "CHROME_OWNED_LAUNCH_UNRESOLVED"
        if launch == "UNKNOWN":
            return _LAUNCH_UNKNOWN
        if launch == "REPORTED":
            return "CHROME_ACQUISITION_NOT_READY"
        return None

    def _acquire_chrome(self, txn, saved: dict, snapshot: tuple) -> str:
        _, (local, session, sid, env, _), _ = snapshot
        owner = (local, session, sid)
        target, _ = chrome_acquisition._clean_target(owner)
        _require(target.command() == env.command() and target.endpoint == env.endpoint,
                 "CHROME_ACQUISITION_TARGET_MISMATCH")
        acquire = _supervisor_start_authority(self.registration, owner, env)
        if not callable(acquire):
            raise DependencyHold("INTERACTIVE_9444_START_AUTHORITY_UNAVAILABLE")
        if self._supervisor_stop():
            return "SUPERVISOR_STOPPED"
        _require(self.sender.lifecycle_snapshot() == snapshot, "CHROME_OBSERVATION_CHANGED")
        self.grant_verifier.revalidate_local()
        reserved = dict(saved, scope=_chrome_scope(*owner, env), launch="UNKNOWN")
        txn.write_chrome(reserved)
        if self._supervisor_stop():
            txn.write_chrome(saved)
            return "SUPERVISOR_STOPPED"
        try:
            receipt = acquire()
        except Exception:
            return _LAUNCH_UNKNOWN
        if (type(receipt) is not dict or set(receipt) != {"status", "code"}
                or type(receipt["status"]) is not str or type(receipt["code"]) is not str):
            return _LAUNCH_UNKNOWN
        status, code = receipt["status"], receipt["code"]
        if status == "HOLD" and code in _ACQUISITION_NO_EFFECT:
            txn.write_chrome(saved)
            return code
        if status in ("STARTED", "REUSE") and code == "CDP_VERIFIED":
            reserved["launch"] = "REPORTED"
            txn.write_chrome(reserved)
            return self._reconcile_chrome(txn, reserved, self.sender.lifecycle_snapshot())
        return _LAUNCH_UNKNOWN

    def _effect_code(self, outcome: cdp_turn.SendOutcome, error: Exception | None = None) -> str:
        if self._native:
            return NativeTurnAuthority.post_reservation_code(self.sender.turn_authority, outcome=outcome)
        return _safe_code(error, "ATTEMPT_UNPROVEN_NO_REPLAY") if error else "ATTEMPT_UNPROVEN_NO_REPLAY"

    def _resume(self, txn, data: dict, record: dict, event: Event) -> Result:
        evidence = self.registration.evidence_type
        status, code = record["status"], record["code"]
        saved = Result(status, code, evidence, replayed=True)
        if evidence == "GITHUB_AUTHENTICATED":
            if self._observe_only and status in ("USER_TURN_AUTHENTICATED", "BRAIN_CORRELATED", "ACK"):
                return Result("HOLD", "NATIVE_RECEIPT_UNPROVEN", evidence, replayed=True)
            if status in ("BRAIN_CORRELATED", "ACK"):
                return Result("HOLD_DEPENDENCY", "BRAIN_RECEIPT_UNAVAILABLE", evidence, replayed=True)
        if status == "BRAIN_CORRELATED":
            if not self._observe_only and "consumer_receipt" in record["evidence"]:
                self._move(txn, data, record, "ACK", "CONSUMER_RECEIPT_RECORDED")
                return Result("ACK", "CONSUMER_RECEIPT_RECORDED", evidence, replayed=True)
            return saved
        if status in _DELIVERY_STATES and not self._observe_only:
            observation = self._saved_observation(record, event)
            if status == "USER_TURN_AUTHENTICATED":
                self._check_composition()
                _require(self.grant_verifier(event, self.registration) is True, "MISSING_GRANT")
                NativeSender.authenticate_observation(self.sender, event, self.registration, observation)
                _require(self.grant_verifier(event, self.registration) is True, "MISSING_GRANT")
            return self._respond(txn, data, record, event, observation, replayed=True)
        if status in ("UNKNOWN_EFFECT", "BLOCKED_EXTERNAL") and "UI_TURN_OBSERVED" in record["history"]:
            # Legacy rows retain their original history and their delivery evidence.
            return Result("UI_TURN_OBSERVED", code, evidence, replayed=True)
        if (self._observe_only or status != "UNKNOWN_EFFECT" or record["evidence"]
                or record["history"] != ["PENDING", "RESERVED", "UNKNOWN_EFFECT"]):
            return saved

        try:
            self._check_composition()
            _require(event.evidence_type == self.sender.evidence_type == evidence
                     and event.source_sha == self.registration.source_sha, "EVENT_IDENTITY_DRIFT")
            _require(self.grant_verifier(event, self.registration) is True, "MISSING_GRANT")
            observation = (NativeSender.reconcile(self.sender, event, self.registration) if self._native
                           else self.sender.reconcile(event, self.registration))
            if observation is None:
                return saved
            status, proof = self._delivery_proof(data, record, event, observation)
            _require(self.grant_verifier(event, self.registration) is True, "MISSING_GRANT")
        except Exception:
            return saved
        self._save_delivery(txn, data, record, status, proof)
        return self._respond(txn, data, record, event, observation, replayed=True)

    def _delivery_proof(self, data: dict, record: dict, event: Event,
                        observation: Observation) -> tuple[Status, dict]:
        _require(type(observation) is Observation and type(observation.receipt) is WakeReceipt,
                 "UI_RECEIPT_INVALID")
        receipt = observation.receipt
        binding = self.registration.binding
        _require(type(receipt.generation) is int
                 and (receipt.event_id, receipt.route_id, receipt.generation) ==
                     (event.event_id, binding.route_id, binding.generation)
                 and observation.chat_url == binding.chat_url
                 and observation.marker == self.registration.marker
                 and _reference(observation.user_turn_ref)
                 and receipt.delivery_status == "UNKNOWN"
                 and receipt.error_code == "UI_USER_TURN_OBSERVED"
                 and receipt.chat_delivery_proven is False
                 and receipt.brain_continuation_proven is False
                 and receipt.witness_evidence is None
                 and receipt.replayed is False, "UI_OBSERVATION_UNPROVEN")
        proof: dict[str, str | int] = {"user_turn_ref": observation.user_turn_ref, "marker": observation.marker}
        status = "UI_TURN_OBSERVED"
        if observation.native_turn is not None:
            _require(self._native, "SYNTHETIC_NATIVE_PROOF_FORBIDDEN")
            native_turn = NativeSender.authenticate_observation(self.sender, event, self.registration, observation)
            status = "USER_TURN_AUTHENTICATED"
            proof.update(user_turn_id=native_turn.user_turn_id,
                         route_generation=native_turn.request.binding.generation,
                         doorbell_sha256=bytes_digest(native_turn.request.canonical_bytes),
                         turn_provenance_ref=native_turn.provenance_ref)
        for other in data["events"].values():
            if other is not record:
                other_proof = other["evidence"]
                _require(other_proof.get("user_turn_ref") != proof["user_turn_ref"]
                         and ("user_turn_id" not in proof or other_proof.get("user_turn_id") != proof["user_turn_id"]),
                         "USER_TURN_REFERENCE_REUSED")
        return status, proof

    def _save_delivery(self, txn, data: dict, record: dict, status: Status, proof: dict) -> None:
        code = "UI_USER_TURN_OBSERVED" if status == "UI_TURN_OBSERVED" else status
        self._move(txn, data, record, status, code, proof=proof)

    def _saved_observation(self, record: dict, event: Event) -> Observation:
        registration, proof = self.registration, record["evidence"]
        binding = registration.binding
        turn = None
        if record["status"] == "USER_TURN_AUTHENTICATED":
            turn = AuthenticatedUserTurn(DoorbellRequest.for_event(event, registration),
                                         proof["user_turn_id"], proof["user_turn_ref"],
                                         proof["turn_provenance_ref"], "USER")
        return Observation(WakeReceipt(event.event_id, binding.route_id, binding.generation,
                                       "UNKNOWN", error_code="UI_USER_TURN_OBSERVED"),
                           binding.chat_url, proof["marker"], proof["user_turn_ref"], turn)

    def _respond(self, txn, data: dict, record: dict, event: Event,
                 observation: Observation, *, replayed: bool) -> Result:
        evidence = self.registration.evidence_type
        status, code = record["status"], record["code"]
        if self.brain is None or (self._native and status != "USER_TURN_AUTHENTICATED"):
            return Result(status, code, evidence, replayed)
        try:
            if self._native:
                self._check_composition()
            proof = self._response_proof(event, observation)
        except (RuntimeHold, OrchestrationError) as exc:
            return Result(status, _safe_code(exc, "BRAIN_RECEIPT_UNAVAILABLE"), evidence, replayed)
        except Exception:
            return Result(status, "BRAIN_RECEIPT_UNAVAILABLE", evidence, replayed)
        # P0 validates and records existing receipts without executing a consumer.
        # Commit the complete response proof before reporting ACK.
        self._move(txn, data, record, "BRAIN_CORRELATED", "BRAIN_RECEIPT_CORRELATED", proof=proof)
        result = self._finish(txn, data, record, "ACK", "CONSUMER_RECEIPT_RECORDED")
        return replace(result, replayed=replayed)

    def _response_proof(self, event: Event, observation: Observation) -> dict:
        registration = self.registration
        evidence, key = registration.evidence_type, event.key(registration)
        correlation = self.brain.correlate(event, registration, observation)
        _require(correlation is not None, "BRAIN_RECEIPT_UNAVAILABLE")
        _require(type(correlation) is BrainCorrelation
                 and (correlation.event_key, correlation.marker, correlation.user_turn_ref,
                      correlation.evidence_type) == (key, registration.marker, observation.user_turn_ref, evidence)
                 and _reference(correlation.correlation_ref), "BRAIN_CORRELATION_MISMATCH")
        handoff = correlation.handoff
        _require(type(handoff) is ProductionHandoff
                 and type(handoff.completed) is ProductionStageCompleted
                 and type(handoff.receipt) is ProductionStageReceipt, "CONSUMER_RECEIPT_REQUIRED")
        if self._native:
            native_turn = observation.native_turn
            brain_proof = NativeBrainAdapter.authenticate_receipt(
                self.brain, event, registration, observation, correlation.authenticated_receipt)
            _require(type(brain_proof) is AuthenticatedBrainReceipt
                     and (brain_proof.event_id, brain_proof.event_key, brain_proof.marker,
                          brain_proof.user_turn_id, brain_proof.user_turn_ref,
                          brain_proof.route_generation, brain_proof.authorization_ref,
                          brain_proof.completed_sha256, brain_proof.consumer_id, brain_proof.receipt_sha256) ==
                         (event.event_id, key, registration.marker, native_turn.user_turn_id,
                          observation.user_turn_ref, registration.binding.generation,
                          handoff.completed.spec.authorization.authorization_ref,
                          handoff.completed.sha256, handoff.receipt.consumer_id, handoff.receipt.sha256),
                     "BRAIN_CORRELATION_MISMATCH")
        else:
            _require(correlation.authenticated_receipt is None, "SYNTHETIC_NATIVE_PROOF_FORBIDDEN")
        expected_kind = "HUMAN_SUPPLIED" if self._native else "SYNTHETIC_FIXTURE"
        _require(handoff.receipt.provenance.evidence_type == expected_kind
                 and handoff.completed.spec.provenance.evidence_type == expected_kind
                 and handoff.receipt.spec == handoff.completed.spec
                 and handoff.receipt.completed_sha256 == handoff.completed.sha256
                 and handoff.completed.spec.idempotency_key == key
                 and handoff.completed.spec.provenance.source_ref == "wakehub-event:" + key
                 and handoff.receipt.provenance.source_ref == "wakehub-event:" + key
                 and handoff.receipt.provenance.source_sha256 == bytes_digest(handoff.source_bytes)
                 and handoff.source_bytes == json.dumps(asdict(event), sort_keys=True).encode("utf-8"),
                 "HANDOFF_EVENT_MISMATCH")
        self.orchestrator.record_completed(
            handoff.completed, source_bytes=handoff.source_bytes,
            input_value=json.loads(handoff.input_json), output=json.loads(handoff.output_json))
        dispatched = self.orchestrator.record_receipt(handoff.receipt)
        _require(dispatched.status == "RECEIPT_RECORDED"
                 and dispatched.accepted_consumer == handoff.receipt.consumer_id
                 and dispatched.event is not None
                 and dispatched.event.receipt == handoff.receipt, "CONSUMER_RECEIPT_NOT_RECORDED")
        return {"correlation_ref": correlation.correlation_ref, "consumer_id": handoff.receipt.consumer_id,
                "receipt_sha256": handoff.receipt.sha256, "completed_sha256": handoff.completed.sha256,
                "consumer_receipt": handoff.receipt.to_dict(), "completed": handoff.completed.to_dict()}

    @staticmethod
    def _move(txn, data: dict, record: dict, status: Status, code: str,
              *, proof: dict | None = None) -> None:
        _require(status in _NEXT.get(record["status"], set()), "INVALID_TRANSITION")
        record.update(status=status, code=code, history=[*record["history"], status])
        if proof is not None:
            record["evidence"].update(proof)
        txn.write(data)

    def _finish(self, txn, data: dict, record: dict, status: Status, code: str,
                *, proof: dict | None = None) -> Result:
        self._move(txn, data, record, status, code, proof=proof)
        return Result(status, code, self.registration.evidence_type)


def synthetic_handoff(event: Event, registration: Registration) -> ProductionHandoff:
    """Actually call the local A03 consumer, label every resulting claim synthetic."""
    from ai_commerce.creative.briefs import intake_brief

    _require(event.evidence_type == registration.evidence_type == "SYNTHETIC_FIXTURE", "SYNTHETIC_ONLY")
    source = json.dumps(asdict(event), sort_keys=True).encode("utf-8")
    input_value = intake_brief("Synthetic WakeHub R1 diagnostic: a quiet cloud.")
    output = intake_brief(input_value)
    route = "a03-creative-brief"
    consumer, path = CONSUMER_ROUTES[route]
    key = event.key(registration)
    provenance = Provenance(actor_ref="fixture:wakehub-runtime", source_ref="wakehub-event:" + key,
                            source_sha256=bytes_digest(source), evidence_type="SYNTHETIC_FIXTURE")
    spec = StageSpec(stage_id="fixture:wakehub-runtime", route_id=route, consumer_id=consumer,
                     consumer_path=path, source_sha256=bytes_digest(source), input_sha256=digest(input_value),
                     output_sha256=digest(output), idempotency_key=key, provenance=provenance,
                     authorization=RouteAuthorization(route_id=route, consumer_id=consumer,
                         consumer_path=path, authorization_ref="fixture:wakehub-runtime"))
    completed = ProductionStageCompleted(spec=spec, completion_ref="fixture:completed:" + key,
        source_sha256=spec.source_sha256, input_sha256=spec.input_sha256, output_sha256=spec.output_sha256)
    receipt = ProductionStageReceipt(spec=spec, completed_sha256=completed.sha256,
        output_sha256=digest(output), consumer_id=consumer, receipt_ref="fixture:receipt:" + key,
        execution_ref="fixture:execution:" + key, provenance=replace(provenance, actor_ref=consumer),
        consumer_executed=True)
    return ProductionHandoff(completed, source, json.dumps(input_value), json.dumps(output), receipt)


class SyntheticSource:
    def __init__(self, event: Event):
        self.event = event

    def next_event(self) -> Event:
        return self.event  # Deliberately redeliver to exercise durable deduplication.


class SyntheticSender:
    """No browser access. Optional supplied acquisition snapshot exercises D5 evaluation."""
    evidence_type = "SYNTHETIC_FIXTURE"

    def __init__(self, environment=None, inventory=None):
        self.environment, self.inventory = environment, inventory
        self.calls = 0

    def preflight(self, registration: Registration) -> str | None:
        if self.environment is not None:
            _require(self.environment.port == 9444 and self.environment.endpoint ==
                     registration.binding.cdp_endpoint, "CDP_OWNER_MISMATCH")
            try:
                state, _ = chrome_acquisition.evaluate(
                    self.environment, self.inventory, parse=json.loads, session_id=7)
            except chrome_acquisition.AcquisitionBlocked as exc:
                return str(exc)
            if state != "OWNED":
                return "PROFILE_ABSENT"
        return None

    def send(self, event: Event, registration: Registration) -> Observation:
        _require(event.evidence_type == registration.evidence_type == "SYNTHETIC_FIXTURE", "SYNTHETIC_ONLY")
        self.calls += 1
        b = registration.binding
        return Observation(WakeReceipt(event_id=event.event_id, route_id=b.route_id,
            generation=b.generation, delivery_status="UNKNOWN", error_code="UI_USER_TURN_OBSERVED"),
            b.chat_url, registration.marker, "fixture:user-turn:" + event.key(registration))

    def reconcile(self, event: Event, registration: Registration) -> None:
        return None


class SyntheticBrain:
    def __init__(self, handoff: ProductionHandoff):
        self.handoff = handoff

    def correlate(self, event: Event, registration: Registration,
                  observation: Observation) -> BrainCorrelation:
        _require(event.evidence_type == registration.evidence_type == "SYNTHETIC_FIXTURE", "SYNTHETIC_ONLY")
        return BrainCorrelation(event.key(registration), registration.marker, observation.user_turn_ref,
                                "fixture:brain-correlation", "SYNTHETIC_FIXTURE", self.handoff)


def _read_owned_json(path: Path, repository_root: Path, *, limit: int = 131072) -> dict:
    """Read existing operator files without creating locks, state or credentials."""
    _require(path.exists(), "LOCAL_AUTHORITY_MISSING")
    with _private_access(path, repository_root) as (owner, files):
        fd = files.open(path.name, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(limit + 1)
        _require(len(raw) <= limit, "LOCAL_AUTHORITY_INVALID")
        value = _decode_unique(raw.decode("utf-8-sig"))
        _require(type(value) is dict and _private_identity(path) == owner,
                 "LOCAL_AUTHORITY_INVALID")
        return value


@dataclass(frozen=True, repr=False)
class _RegisteredWakeAuthority:
    """Existing signed-origin composition, not a caller readiness callback.

    Snapshot JSON is detached and immutable. This authenticates only signed
    operation scope and private file custody, never interactive native origin.
    """
    source: GitHubEventSource
    registration: Registration
    repository_root: Path
    config_path: Path
    registry_path: Path
    snapshots: tuple[tuple[Path, str], ...]
    registry_json: str
    origin: SignedOriginConsentAuthority
    caller: LocalCallerAttestation
    principal: str
    native_source_only: bool

    def revalidate_local(self) -> None:
        for path, snapshot_json in self.snapshots:
            _require(_read_owned_json(path, self.repository_root) == json.loads(snapshot_json),
                     "LOCAL_AUTHORITY_DRIFT")
        _require(_private_identity(self.config_path) == self.principal, "JOURNAL_OWNER_DRIFT")

    def authorize(self, event: Event, current: Registration) -> SignedWakeAuthorization | None:
        _require(current == self.registration, "REGISTRATION_DRIFT")
        full_event = self.source.verified_event
        _require(full_event is not None and digest(full_event) == event.payload_sha256,
                 "SOURCE_PROOF_MISSING")
        self.revalidate_local()
        binding = resolve_binding(full_event, route_id=current.binding.route_id,
                                  registry_path=self.registry_path, repository_root=self.repository_root)
        _require(binding == current.binding, "REGISTRATION_DRIFT")
        registry = json.loads(self.registry_json)
        _require(_read_owned_json(self.registry_path, self.repository_root) == registry,
                 "LOCAL_AUTHORITY_DRIFT")
        _require(type(self.origin) is SignedOriginConsentAuthority, "SIGNED_WAKE_AUTHORITY_UNAVAILABLE")
        context = {k: full_event[k] for k in ("repository", "batch_id", "source_sha", "issue_number")}
        if not SignedOriginConsentAuthority.authorize_origin(
                self.origin, self.caller, operation="resolve_claim_route",
                context={**context, "registry": registry}):
            return None
        operations = ("resolve_claim_route",)
        if self.native_source_only:
            if not SignedOriginConsentAuthority.authorize_origin(
                self.origin, self.caller, operation="deliver_wake",
                context={"event": full_event, "registry": registry}):
                raise DependencyHold("DELIVER_WAKE_GRANT_REQUIRED")
            operations += ("deliver_wake",)
        return SignedWakeAuthorization(event, current, registry_sha256(registry),
                                       self.principal, self.caller.caller_id, operations)

    def __call__(self, event: Event, current: Registration) -> bool:
        return self.authorize(event, current) is not None


def compose_read_only(config_path: Path, repository_root: Path) -> Runner:
    """One R1 ingress plus R2 read-only Chrome/exact-chat readiness.

    All paths/selectors come from an existing private local operator file. No
    Issue/event may provide credentials, URLs, profile selection or authority.
    A resolve-claim grant is required; it does not authorize Send. P0 has no real
    receipt pins, so neither native Brain nor consumer success is possible.
    Retain the R1 private config and journal schemas without implicit migration.
    """
    return _compose_registered(config_path, repository_root, native_source_only=False)


def compose_native_source_only(config_path: Path, repository_root: Path) -> Runner:
    """R3B1 dependency evaluation, not enrollment or permission to perform effects.

    Reuse the existing private R1 schema and signed-origin operator. Require both
    resolve_claim_route and exact-event deliver_wake grants. There is no native
    origin verifier, authenticated doorbell turn-ref or Brain receipt transport
    to enroll in this revision; those remain explicit dependencies, never flags.
    """
    return _compose_registered(config_path, repository_root, native_source_only=True)


def _compose_registered(config_path: Path, repository_root: Path, *, native_source_only: bool,
                        supervisor_stop: Callable[[], bool] | None = None) -> Runner:
    config = _read_owned_json(config_path, repository_root)
    _require(set(config) == {"schema", "registration", "marker", "registered_source",
             "registry", "journal", "chrome_config", "grants", "caller_id"}
             and config["schema"] == "WakeHubRuntimeR1.v1", "LOCAL_CONFIG_INVALID")
    raw_registration = config["registration"]
    _require(type(raw_registration) is dict
             and set(raw_registration) == {"binding", "source_sha", "evidence_type"}
             and type(raw_registration["binding"]) is dict, "LOCAL_CONFIG_INVALID")
    registration = Registration(ResolvedBinding(**raw_registration["binding"]),
                                raw_registration["source_sha"], raw_registration["evidence_type"])
    _require(registration.evidence_type == "GITHUB_AUTHENTICATED"
             and config["marker"] == registration.marker, "REGISTRATION_MARKER_DRIFT")
    _require(type(config["caller_id"]) is str and _ROUTE.fullmatch(config["caller_id"]) is not None,
             "LOCAL_CONFIG_INVALID")
    paths = {}
    for name in ("registered_source", "registry", "journal", "chrome_config", "grants"):
        _require(type(config[name]) is str, "LOCAL_CONFIG_INVALID")
        paths[name] = Path(config[name])
        _private_path(paths[name], repository_root)
    _require(len(set(paths.values()) | {config_path}) == 6, "LOCAL_CONFIG_INVALID")
    registered = _read_owned_json(paths["registered_source"], repository_root, limit=8192)
    _require(type(registered.get("intent")) is dict, "REGISTERED_SOURCE_INVALID")
    intent = RegistrationIntent(**registered["intent"])
    _record_valid(registered, intent)
    target = intent.target(registered["run_id"])
    _require(target.source_sha == registration.source_sha, "SOURCE_TARGET_DRIFT")
    registry = _read_owned_json(paths["registry"], repository_root)
    grants = _read_owned_json(paths["grants"], repository_root)
    _require(set(grants) == {"version", "grants"} and type(grants["version"]) is int
             and grants["version"] == 1 and type(grants["grants"]) is list
             and 1 <= len(grants["grants"]) <= 128
             and all(type(g) is dict for g in grants["grants"]), "GRANTS_INVALID")
    chrome_config = _read_owned_json(paths["chrome_config"], repository_root, limit=8192)
    local = os.environ.get("LOCALAPPDATA")
    _require(bool(local), "PROFILE_AUTHORITY_MISSING")
    environment = chrome_acquisition.load_environment(paths["chrome_config"], repository_root,
                                                       local_app_data=Path(local))
    _require(environment.endpoint == registration.binding.cdp_endpoint and environment.port == 9444,
             "CDP_OWNER_MISMATCH")
    # Existing operator-only environment contracts; never read from Issue JSON.
    token = os.environ.get("AI_COMMERCE_GITHUB_READ_TOKEN", "")
    _require(bool(token) and len(token) <= 4096 and not any(c in token for c in "\r\n\0"),
             "GITHUB_CREDENTIAL_INVALID")
    key_hex = os.environ.get("AI_COMMERCE_WAKE_HMAC_KEY_HEX", "")
    _require(re.fullmatch(r"[0-9a-fA-F]{64,8192}", key_hex) is not None and len(key_hex) % 2 == 0,
             "WAKE_CREDENTIAL_INVALID")
    key = bytes.fromhex(key_hex)
    owner = _private_identity(config_path)
    origin = SignedOriginConsentAuthority(grants=grants["grants"], secret_provider=lambda: key,
                                          principal_provider=lambda: _private_identity(config_path))
    caller = LocalCallerAttestation(config["caller_id"], is_local=True, attestation_verified=True)
    budget = GitHubReadBudget(max_reads=16, max_seconds=60, max_attempts=1)
    reader = GitHubExactRunReader(token_provider=lambda: token, read_budget=budget)
    authority = GitHubRestArtifactAuthority(repository=intent.repository, workflow_id=target.workflow_id,
                                             token_provider=lambda: token, read_budget=budget)
    source = GitHubEventSource(reader, target, authority, expected_event_id=registered["event_id"])

    snapshots = tuple((path, json.dumps(snapshot, sort_keys=True)) for path, snapshot in
                      ((config_path, config), (paths["registered_source"], registered),
                       (paths["registry"], registry), (paths["grants"], grants),
                       (paths["chrome_config"], chrome_config)))
    verify_local_authority = _RegisteredWakeAuthority(
        source, registration, repository_root, config_path, paths["registry"], snapshots,
        json.dumps(registry, sort_keys=True), origin, caller, owner, native_source_only)

    return Runner(source=source, registration=registration,
                  journal=Journal(paths["journal"], repository_root),
                  sender=(NativeSender(repository_root, registration, environment) if native_source_only
                          else NativeChrome9444(repository_root, environment,
                                                supervised=supervisor_stop is not None)), brain=NativeBrainAdapter(),
                  orchestrator=ProductionPipelineOrchestrator(), grant_verifier=verify_local_authority,
                  read_only=not native_source_only and supervisor_stop is None,
                  supervisor_stop=supervisor_stop)


def run_read_only(config_path: Path | None, repository_root: Path, *, dry_run: bool = False) -> Result:
    return _run_registered(config_path, repository_root, dry_run=dry_run, compose=compose_read_only)


def run_native_source_only(config_path: Path | None, repository_root: Path, *, dry_run: bool = False) -> Result:
    return _run_registered(config_path, repository_root, dry_run=dry_run, compose=compose_native_source_only)


def _run_registered(config_path: Path | None, repository_root: Path, *, dry_run: bool,
                    compose: Callable[[Path, Path], Runner]) -> Result:
    if dry_run:
        return Result("HOLD", "DRY_RUN_NO_EFFECTS", "SOURCE_ONLY")
    if config_path is None:
        return Result("HOLD", "LOCAL_AUTHORITY_MISSING", "GITHUB_AUTHENTICATED")
    try:
        return compose(config_path, repository_root).run_once()
    except DependencyHold as exc:
        return Result("HOLD_DEPENDENCY", _safe_code(exc, "NATIVE_DEPENDENCY_UNAVAILABLE"), "GITHUB_AUTHENTICATED")
    except (RuntimeHold, BindingError, RegistrationBlocked, TriggerBlocked, GitHubReadBlocked,
            PrivateStateError, HandoffBlocked, chrome_acquisition.AcquisitionBlocked) as exc:
        return Result("HOLD", _safe_code(exc, "LOCAL_AUTHORITY_UNPROVEN"), "GITHUB_AUTHENTICATED")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return Result("HOLD", "LOCAL_AUTHORITY_INVALID", "GITHUB_AUTHENTICATED")


@contextmanager
def _supervisor_slot():
    """One receiver across Windows sessions, released automatically on exit."""
    if os.name != "nt":
        raise RuntimeHold("WINDOWS_INTERACTIVE_REQUIRED")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexExW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.CreateMutexExW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    # The existing clean-launch mutex remains owned by the acquisition command.
    handle = kernel.CreateMutexExW(None, r"Global\AI-Commerce-WakeHub-9444-Receiver-v1", 0, 0x100000)
    error = ctypes.get_last_error()
    _require(bool(handle), "SUPERVISOR_SLOT_UNAVAILABLE")
    try:
        _require(error != 183, "SUPERVISOR_ACTIVE")  # ERROR_ALREADY_EXISTS
        yield
    finally:
        kernel.CloseHandle(handle)


class SupervisorStop(Protocol):
    def is_set(self) -> bool: ...
    def wait(self, timeout: float) -> bool: ...


def supervise(tick: Callable[[], Result], *, stop: SupervisorStop, max_ticks: int | None = None,
              idle_seconds: int = 30, max_backoff_seconds: int = 300,
              on_health: Callable[[str], None] | None = None) -> Result:
    """Run serial ticks under a receiver slot, interruptible between ticks."""
    stopped = Result("HOLD", "SUPERVISOR_STOPPED", "SOURCE_ONLY")
    if (type(idle_seconds) is not int or type(max_backoff_seconds) is not int
            or not 1 <= idle_seconds <= max_backoff_seconds <= 3600
            or (max_ticks is not None and (type(max_ticks) is not int or max_ticks < 1))):
        return Result("HOLD", "SUPERVISOR_BUDGET_INVALID", "SOURCE_ONLY")
    if stop.is_set():
        return stopped
    try:
        with _supervisor_slot():
            delay = idle_seconds
            count = 0
            health = None
            while not stop.is_set():
                result = tick()
                current_health = result.chrome_health if isinstance(result, SupervisorTickResult) else None
                if on_health is not None and current_health is not None and current_health != health:
                    health = current_health
                    on_health(health)
                count += 1
                if stop.is_set():
                    break
                if count == max_ticks:
                    return result
                idle = (current_health in (None, "CHROME_OWNED")
                        and (result.code == "NO_EVENT" or result.status in (
                            "ACK", "UI_TURN_OBSERVED", "USER_TURN_AUTHENTICATED")))
                if idle:
                    delay = idle_seconds
                if stop.wait(delay):
                    break
                if not idle:
                    delay = min(max_backoff_seconds, 2 * delay)
    except RuntimeHold as exc:
        return Result("HOLD", _safe_code(exc, "SUPERVISOR_SLOT_UNAVAILABLE"), "SOURCE_ONLY")
    except KeyboardInterrupt:
        pass
    return stopped


def run_supervisor(config_path: Path | None, repository_root: Path, *, dry_run: bool = False,
                   max_ticks: int | None = None, idle_seconds: int = 30,
                   max_backoff_seconds: int = 300, on_health: Callable[[str], None] | None = None) -> Result:
    if dry_run:
        return Result("HOLD", "DRY_RUN_NO_EFFECTS", "SOURCE_ONLY")
    stop = StopEvent()

    def compose(path: Path, root: Path) -> Runner:
        return _compose_registered(path, root, native_source_only=False, supervisor_stop=stop.is_set)

    return supervise(lambda: _run_registered(config_path, repository_root, dry_run=False, compose=compose),
                     stop=stop, max_ticks=max_ticks, idle_seconds=idle_seconds,
                     max_backoff_seconds=max_backoff_seconds, on_health=on_health)


def diagnostic(path: Path, repository_root: Path) -> Result:
    """Offline composition only; no native readiness or real Brain success claim."""
    registration = Registration(ResolvedBinding("fixture-chat-a", 1,
        "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001", "http://127.0.0.1:9444"),
        "a" * 40, "SYNTHETIC_FIXTURE")
    event = Event("ef02-completion-v1:" + "b" * 64, registration.source_sha, 1,
                  "c" * 64, "SYNTHETIC_FIXTURE")
    handoff = synthetic_handoff(event, registration)
    spec, receipt = handoff.completed.spec, handoff.receipt
    # Pins are fixture-only, never promoted to real authenticated authority.
    orchestrator = ProductionPipelineOrchestrator(
        authorized_specs=((spec.authorization.authorization_ref, spec.sha256),),
        verified_receipts=((receipt.consumer_id, receipt.sha256),))
    return Runner(source=SyntheticSource(event), registration=registration,
                  journal=Journal(path, repository_root), sender=SyntheticSender(),
                  brain=SyntheticBrain(handoff), orchestrator=orchestrator,
                  grant_verifier=lambda e, r: e.evidence_type == r.evidence_type == "SYNTHETIC_FIXTURE"
                  ).run_once()
