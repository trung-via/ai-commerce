"""WakeHub source admission, local route readiness and its private SQLite journal."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
import ntpath
import os
from pathlib import Path
import re
from secrets import token_hex
import sqlite3
from threading import Event as StopEvent
import time
from time import monotonic, time_ns
from types import SimpleNamespace
from typing import Callable, Literal, Protocol
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

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
    BindingError, ResolvedBinding, _CHAT, _REPOSITORY, _ROUTE, _decode_unique, resolve_binding,
)
from ai_commerce.remote.local_completion_handoff import HandoffBlocked, _private_identity
from ai_commerce.remote.local_wake_trigger import GitHubExactRunReader, PinnedRun, TriggerBlocked
from ai_commerce.remote.github_artifact_authority import GitHubRestArtifactAuthority, _API, _HEADERS, _MAX_JSON
from ai_commerce.remote.github_read_retry import GitHubReadBlocked, GitHubReadBudget
from ai_commerce.remote.local_run_registration import (
    REPOSITORY, RegistrationBlocked, RegistrationIntent, _record_valid, _timestamp,
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
_EF02 = "ef02-completion"
_ISSUE = "github-issue-created"
_ISSUE_EVIDENCE = "GITHUB_ISSUE_AUTHENTICATED"
_ISSUE_ACCEPTED = "ISSUE_CREATED_SOURCE_ACCEPTED"
_ISSUE_ROUTE_DEPENDENCY = "ISSUE_CREATED_ROUTE_AUTHORITY_UNAVAILABLE"
_UI_SEND_RESERVED = "LOCAL_UI_SEND_RESERVED"
_UI_STAGING = "LOCAL_UI_STAGING"
_UI_TURN_OBSERVED = cdp_doorbell.LOCAL_UI_OWNER_AUTHORIZED_TURN_OBSERVED
_UI_TURN_RECONCILED = cdp_doorbell.LOCAL_UI_EXISTING_TURN_RECONCILED
_UI_DELIVERY_SCHEMA = "WakeHubLocalUIDelivery.v2"
_MAX_JOURNAL_EVENTS = 128
_MAX_TARGETS = 16
_CHROME_READY = {"CHROME_OWNED", "CHROME_OWNED_LAUNCH_UNRESOLVED"}
# These returns from the fixed task script precede RunEx.
_DEMAND_NOT_ATTEMPTED = frozenset({
    "START_NOT_ATTEMPTED", "AUTOSTART_CONFLICT", "AUTOSTART_RUNTIME_UNAVAILABLE",
    "WINDOWS_OWNER_CONTEXT_UNVERIFIED",
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
    """Legacy EF02 delivery selectors. Preserve the wire and journal fields."""
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


def _github_id(value: object) -> bool:
    return type(value) is int and 0 < value <= 2**63 - 1


@dataclass(frozen=True)
class SourceIdentity:
    kind: Literal["ef02-completion", "github-issue-created"]
    repository: str
    object_id: int  # GitHub run ID or global Issue ID
    attempt: int | None = None
    repository_id: int | None = None

    def __post_init__(self) -> None:
        _require(type(self.kind) is str and type(self.repository) is str and len(self.repository) <= 201
                 and self.repository == self.repository.lower()
                 and _REPOSITORY.fullmatch(self.repository) is not None
                 and not {".", ".."}.intersection(self.repository.split("/"))
                 and _github_id(self.object_id), "SOURCE_IDENTITY_INVALID")
        _require((self.kind == _EF02 and self.repository == REPOSITORY and _github_id(self.attempt)
                  and self.repository_id is None)
                 or (self.kind == _ISSUE and self.attempt is None and _github_id(self.repository_id)),
                 "SOURCE_IDENTITY_INVALID")

    @property
    def key(self) -> str:
        # Use the immutable repository ID for Issues, even if its name changes.
        return digest([self.kind, self.repository_id or self.repository, self.object_id, self.attempt])


@dataclass(frozen=True)
class IssueCreated:
    issue_number: int
    creator_id: int
    created_at: str

    def __post_init__(self) -> None:
        _require(all(_github_id(v) for v in (self.issue_number, self.creator_id)),
                 "ISSUE_CREATION_INVALID")
        try:
            _timestamp(self.created_at)
        except RegistrationBlocked:
            raise RuntimeHold("ISSUE_CREATION_INVALID") from None


@dataclass(frozen=True, repr=False)
class IssueRegistration:
    """Local source admission for one exact creation and route revision."""
    identity: SourceIdentity
    creation: IssueCreated
    local_source_sha: str
    route_marker: str

    def __post_init__(self) -> None:
        _require(type(self.identity) is SourceIdentity and self.identity.kind == _ISSUE
                 and type(self.creation) is IssueCreated
                 and type(self.local_source_sha) is str
                 and re.fullmatch(r"[0-9a-f]{40}", self.local_source_sha) is not None
                 and type(self.route_marker) is str
                 and re.fullmatch(r"wakehub-r1:[0-9a-f]{64}", self.route_marker) is not None,
                 "ISSUE_REGISTRATION_INVALID")

    def check_route(self, registration: Registration) -> None:
        _require(registration.evidence_type == "GITHUB_AUTHENTICATED"
                 and self.local_source_sha == registration.source_sha
                 and self.route_marker == registration.marker, "ISSUE_REGISTRATION_ROUTE_DRIFT")

    @classmethod
    def from_dict(cls, data: dict) -> IssueRegistration:
        _require(type(data) is dict and set(data) == {"identity", "creation", "local_source_sha", "route_marker"}
                 and type(data["identity"]) is dict and type(data["creation"]) is dict,
                 "ISSUE_REGISTRATION_INVALID")
        return cls(SourceIdentity(**data["identity"]), IssueCreated(**data["creation"]),
                   data["local_source_sha"], data["route_marker"])


@dataclass(frozen=True, repr=False)
class SourceEvent:
    """Authenticated source identity, separate from the payload commitment."""
    identity: SourceIdentity
    payload_sha256: str
    payload: Event | IssueRegistration

    def __post_init__(self) -> None:
        _require(type(self.identity) is SourceIdentity and type(self.payload_sha256) is str
                 and re.fullmatch(r"[0-9a-f]{64}", self.payload_sha256) is not None,
                 "SOURCE_EVENT_INVALID")
        if self.identity.kind == _EF02:
            _require(type(self.payload) is Event and self.payload.evidence_type == "GITHUB_AUTHENTICATED"
                     and self.payload.attempt == self.identity.attempt
                     and self.payload.payload_sha256 == self.payload_sha256, "SOURCE_EVENT_INVALID")
        else:
            _require(type(self.payload) is IssueRegistration and self.payload.identity == self.identity
                     and self.payload_sha256 == digest({"identity": asdict(self.identity),
                                                        "creation": asdict(self.payload.creation)}),
                     "SOURCE_EVENT_INVALID")


class EventSource(Protocol):
    def next_event(self) -> Event | SourceEvent | None: ...


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

    def envelope(self, event: Event) -> SourceEvent:
        verified = self.verified_event
        _require(verified is not None and
                 (verified["event_id"], verified["source_sha"], digest(verified)) ==
                 (event.event_id, event.source_sha, event.payload_sha256), "SOURCE_PROOF_MISSING")
        return SourceEvent(SourceIdentity(_EF02, verified["repository"], verified["run_id"],
                                          verified["run_attempt"]), event.payload_sha256, event)


class _ExactAPIRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Exact Issue and repository API reads must retain authenticated custody.
        return None


class GitHubIssueCreatedSource:
    """Verify an owner-pinned creation via exact authenticated GitHub GETs."""

    def __init__(self, target: IssueRegistration, *, token: str, read_budget: GitHubReadBudget):
        self.target, self._token, self.read_budget = target, token, read_budget
        self.verified_event: SourceEvent | None = None

    @staticmethod
    def _read_json(url: str, token: str, *, timeout: float) -> dict:
        # Use the existing GitHub REST headers, limits and JSON decoder. Unlike
        # archive GETs, these requests have no legitimate signed CDN redirect.
        opener = build_opener(ProxyHandler({}), _ExactAPIRedirect())
        request = Request(url, headers={**_HEADERS, "Authorization": "Bearer " + token}, method="GET")
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_JSON + 1)
        _require(len(raw) <= _MAX_JSON, "GITHUB_RESPONSE_TOO_LARGE")
        value = _decode_unique(raw.decode("utf-8"))
        _require(type(value) is dict, "GITHUB_RESPONSE_INVALID")
        return value

    def _get(self, url: str) -> dict:
        return self.read_budget.read(
            lambda u, t: self._read_json(u, t, timeout=self.read_budget.timeout()), url, self._token)

    def _creation(self, root: str, url: str) -> IssueCreated:
        issue = self._get(url)
        user, issue_url, repo_url = (issue.get(k) for k in ("user", "url", "repository_url"))
        _require("pull_request" not in issue and type(issue_url) is str and issue_url.lower() == url
                 and type(repo_url) is str and repo_url.lower() == root
                 and _github_id(issue.get("id")) and issue["id"] == self.target.identity.object_id
                 and type(user) is dict and user.get("type") in ("User", "Bot"), "ISSUE_IDENTITY_UNPROVEN")
        return IssueCreated(issue.get("number"), user.get("id"), issue.get("created_at"))

    def next_event(self) -> SourceEvent:
        self.verified_event = None
        target = self.target
        root = f"{_API}/repos/{target.identity.repository}"
        url = f"{root}/issues/{target.creation.issue_number}"
        candidate = self._creation(root, url)
        repo = self._get(root)
        name = repo.get("full_name")
        _require(type(name) is str and name.lower() == target.identity.repository
                 and _github_id(repo.get("id")) and repo["id"] == target.identity.repository_id,
                 "ISSUE_REPOSITORY_UNPROVEN")
        verified = self._creation(root, url)
        _require(candidate == verified == target.creation, "ISSUE_CREATION_UNPROVEN")
        payload_sha = digest({"identity": asdict(target.identity), "creation": asdict(verified)})
        self.verified_event = SourceEvent(target.identity, payload_sha, target)
        return self.verified_event


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
    source_identity: SourceIdentity | None = None

    def __post_init__(self) -> None:
        identity = self.source_identity
        _require(identity is None or type(identity) is SourceIdentity, "DOORBELL_REQUEST_INVALID")
        issue = identity is not None and identity.kind == _ISSUE
        repository = identity.repository if identity is not None else REPOSITORY
        _require(type(self.event_id) is str
                 and (self.event_id == "github-issue-created-v1:" + identity.key and self.event_key == identity.key
                      if issue else cdp_doorbell._EVENT.fullmatch(self.event_id) is not None)
                 and type(self.event_key) is str and re.fullmatch(r"[0-9a-f]{64}", self.event_key) is not None
                 and type(self.binding) is ResolvedBinding
                 and self.binding.cdp_endpoint == "http://127.0.0.1:9444" and _reference(self.marker)
                 and type(self.canonical_bytes) is bytes
                 and self.canonical_bytes == cdp_doorbell._doorbell_text(
                     self.event_id, repository, send_only=True).encode("utf-8"),
                 "DOORBELL_REQUEST_INVALID")

    @classmethod
    def for_event(cls, event: Event, registration: Registration) -> DoorbellRequest:
        _require(type(event) is Event and event.source_sha == registration.source_sha
                 and event.evidence_type == registration.evidence_type, "DOORBELL_REQUEST_DRIFT")
        return cls(event.event_id, event.key(registration), registration.binding, registration.marker,
                   cdp_doorbell._doorbell_text(event.event_id, "trung-via/ai-commerce",
                                               send_only=True).encode("utf-8"))

    @classmethod
    def for_source(cls, event: SourceEvent, registration: Registration) -> DoorbellRequest:
        identity = event.identity
        if identity.kind == _EF02:
            return replace(cls.for_event(event.payload, registration), source_identity=identity)
        event.payload.check_route(registration)
        event_id = "github-issue-created-v1:" + identity.key
        content = cdp_doorbell._doorbell_text(event_id, identity.repository, send_only=True)
        return cls(event_id, identity.key, registration.binding, registration.marker,
                   content.encode("utf-8"), identity)


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


def _chrome_scope(local: Path, sid: str,
                  env: chrome_acquisition.ChromeEnvironment) -> str:
    paths = (local, env.config_path, env.executable, env.user_data_dir)
    return digest(["WakeHubChromeOwner.v1", sid,
                   [ntpath.normcase(ntpath.normpath(str(path))) for path in paths],
                   env.profile_directory, env.endpoint, env.port])


def _legacy_chrome_scope(local: Path, session: int, sid: str,
                         env: chrome_acquisition.ChromeEnvironment) -> str:
    return digest([str(local), session, sid, str(env.config_path), env.command()])


def _owned_chrome_process(inventory: tuple, desktop: str) -> tuple[str, int]:
    listeners, processes = inventory
    pid = listeners[0][2]
    created = chrome_acquisition._creation_ticks(next(row[-1] for row in processes if row[0] == pid))
    return digest([desktop, pid, created]), created


def _supervised_desktop(session: int, sid: str) -> str:
    """Pin the live, unlocked Windows logon, even if its session ID is reused."""
    import ctypes
    from ctypes import wintypes as w

    from ai_commerce.wakehub.native_authority import _desktop_identity

    _require(os.name == "nt" and not any(
        os.environ.get(key) for key in ("CI", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_EVENT_PATH")
    ) and not any(key.startswith("AI_COMMERCE_GIT_GUARD_") for key in os.environ),
             "LOCAL_OPERATOR_ONLY")

    class Session(ctypes.Structure):
        _fields_ = [("id", w.DWORD), ("station", w.LPWSTR), ("state", ctypes.c_int)]

    class SessionInfo(ctypes.Structure):
        _fields_ = [
            ("id", w.DWORD), ("state", ctypes.c_int), ("flags", w.LONG),
            ("station", w.WCHAR * 33), ("user", w.WCHAR * 21), ("domain", w.WCHAR * 18),
            ("logon", ctypes.c_longlong), ("connect", ctypes.c_longlong),
            ("disconnect", ctypes.c_longlong), ("last_input", ctypes.c_longlong),
            ("current", ctypes.c_longlong), ("counters", w.DWORD * 6),
        ]

    class SessionInfoEx(ctypes.Structure):
        _fields_ = [("level", w.DWORD), ("data", SessionInfo)]

    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    free = wts.WTSFreeMemory
    free.argtypes, free.restype = [ctypes.c_void_p], None
    enumerate_sessions = wts.WTSEnumerateSessionsW
    enumerate_sessions.argtypes = [
        w.HANDLE, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.POINTER(Session)), ctypes.POINTER(w.DWORD),
    ]
    enumerate_sessions.restype = w.BOOL
    rows, count = ctypes.POINTER(Session)(), w.DWORD()
    _require(enumerate_sessions(None, 0, 1, ctypes.byref(rows), ctypes.byref(count)),
             "WINDOWS_INTERACTIVE_REQUIRED")
    try:
        _require(bool(rows) and 0 < count.value <= 256, "WINDOWS_INTERACTIVE_REQUIRED")
        active = [rows[i].id for i in range(count.value) if rows[i].state == 0]  # WTSActive
        _require(active == [session], "CHROME_INTERACTIVE_SESSION_CONFLICT")
    finally:
        free(rows)

    query = wts.WTSQuerySessionInformationW
    query.argtypes = [w.HANDLE, w.DWORD, ctypes.c_int,
                      ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.DWORD)]
    query.restype = w.BOOL
    raw, size = ctypes.c_void_p(), w.DWORD()
    _require(query(None, session, 25, ctypes.byref(raw), ctypes.byref(size)),  # WTSSessionInfoEx
             "WINDOWS_INTERACTIVE_REQUIRED")
    try:
        _require(bool(raw) and size.value >= ctypes.sizeof(SessionInfoEx),
                 "WINDOWS_INTERACTIVE_REQUIRED")
        info = SessionInfoEx.from_address(raw.value)
        data = info.data
        _require(info.level == 1 and data.id == session and data.state == 0 and data.flags == 1
                 and data.user and data.domain and 0 < data.logon <= data.current,
                 "WINDOWS_INTERACTIVE_REQUIRED")
        try:
            _desktop_identity(SimpleNamespace(
                user_sid=sid, session_id=session, user_name=data.domain + "\\" + data.user,
                desktop=r"WinSta0\Default"))
        except ValueError:
            raise RuntimeHold("WINDOWS_INTERACTIVE_REQUIRED") from None
        return digest([session, data.logon])
    finally:
        free(raw)


class NativeChrome9444:
    """Read the enrolled 9444 owner and the exact-chat surface.

    The existing Chrome owner supplies Windows inventory, custody, signature,
    session/SID and protocol checks. The existing doorbell inventory supplies
    the attach and surface read. Neither a URL nor an empty composer attests
    the originating account/marker.
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
        desktop = _supervised_desktop(session, sid) if self.supervised else None
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
            if self.supervised:
                chrome_acquisition._verify_google_chrome(env)
            chrome_acquisition._probe_cdp(env)
        _require(state in ("ABSENT", "OWNED"), "CHROME_OWNER_UNPROVEN")
        if self.supervised:
            _require(_supervised_desktop(session, sid) == desktop, "CHROME_OBSERVATION_CHANGED")
        return state, (local, session, sid, env, identity, desktop)

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

    def local_chat(self, binding: ResolvedBinding, *, marker_digest: str | None = None) -> str:
        """Read one exact tab and optionally its committed USER-turn marker."""
        try:
            with self.adapter.inventory._browser(binding.cdp_endpoint) as browser:
                contexts = list(browser.contexts)
                if len(contexts) != 1:
                    return "EXACT_CHAT_CONTEXT_AMBIGUOUS"
                context = contexts[0]

                def candidates():
                    pages = list(context.pages)
                    _require(len(pages) <= 32, "EXACT_CHAT_INVENTORY_UNBOUNDED")
                    matches = [p for p in pages if type(p.url) is str
                               and p.url.rstrip("/") == binding.chat_url]
                    _require(matches or len(pages) < 32, "EXACT_CHAT_INVENTORY_UNBOUNDED")
                    return matches

                matches = candidates()
                if not matches:
                    return "EXACT_CHAT_ABSENT"
                if len(matches) != 1:
                    return "EXACT_CHAT_TAB_AMBIGUOUS"
                page = matches[0]
                if page.url != binding.chat_url:
                    return "EXACT_CHAT_URL_DRIFT"
                code = cdp_doorbell._preload_surface_code(page)
                if code == "CANDIDATE_READY" and marker_digest is not None:
                    marker = page.evaluate(cdp_doorbell._WH03_COMMITTED_SIGNAL_DOM,
                                           {"digest": marker_digest})
                    code = {
                        "TEST_SIGNAL_MATCH_UNATTESTED": "LOCAL_USER_MARKER_OBSERVED",
                        "TEST_SIGNAL_NOT_FOUND": "LOCAL_USER_MARKER_MISSING",
                        "TEST_SIGNAL_AMBIGUOUS": "LOCAL_USER_MARKER_AMBIGUOUS",
                    }.get(marker, "LOCAL_USER_MARKER_UNPROVEN")
                current_contexts = list(browser.contexts)
                current_tabs = candidates()
                if (len(current_contexts) != 1 or current_contexts[0] is not context
                        or len(current_tabs) != 1 or current_tabs[0] is not page
                        or page.url != binding.chat_url):
                    return "EXACT_CHAT_OBSERVATION_CHANGED"
                return code
        except RuntimeHold as exc:
            return str(exc)
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
            if owner_scope is not None:
                local, _, sid, env, _, _ = before[1]
                if _chrome_scope(local, sid, env) != owner_scope:
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


@dataclass(frozen=True)
class RouteReadiness:
    source_kind: Literal["ef02-completion", "github-issue-created"]
    source_key: str
    route_generation: int
    route_commitment: str
    registration_id: str
    marker_commitment: str
    readiness: Literal["LOCAL_ROUTE_REGISTERED", "EXACT_CHAT_REUSED", "EXACT_CHAT_PRELOADED",
                       "EXACT_CHAT_RECONCILED"] = "LOCAL_ROUTE_REGISTERED"
    account_attestation: Literal["CHATGPT_ACCOUNT_SESSION_AUTHORITY_UNAVAILABLE"] = (
        "CHATGPT_ACCOUNT_SESSION_AUTHORITY_UNAVAILABLE")


@dataclass(frozen=True, kw_only=True)
class RouteResult(Result):
    route: RouteReadiness


@dataclass(frozen=True, kw_only=True)
class SupervisorRouteTickResult(SupervisorTickResult):
    route: RouteReadiness


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
_SOURCE_TABLE = ("CREATE TABLE source_identities (source_key TEXT PRIMARY KEY, "
                 "event_key TEXT NOT NULL UNIQUE REFERENCES events(event_key), identity TEXT NOT NULL)")
_ORIGIN_TABLE = ("CREATE TABLE local_origin (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                 "record TEXT NOT NULL)")
_APP_ID = 0x57485231


def _validate_chrome(record: dict) -> None:
    fields = {"scope", "process", "launch"}
    _require(type(record) is dict and record.keys() in (fields, fields | {"request"})
             and all(record[k] is None or _chrome_digest(record[k]) for k in ("scope", "process"))
             and record["launch"] in (None, "UNKNOWN", "REPORTED", "REQUESTED")
             and (record["scope"] is not None or
                  (record["process"] is None and record["launch"] in (None, "UNKNOWN"))),
             "CHROME_JOURNAL_INVALID")
    request = record.get("request")
    if request is not None:
        _require(type(request) is dict and request.keys() == {"desktop", "task", "after"}
                 and _chrome_digest(request["desktop"]) and _chrome_digest(request["task"])
                 and type(request["after"]) is int and 0 < request["after"] < 2**63
                 and record["scope"] is not None and record["launch"] in ("UNKNOWN", "REQUESTED"),
                 "CHROME_JOURNAL_INVALID")
    _require(record["launch"] != "REQUESTED" or request is not None, "CHROME_JOURNAL_INVALID")


def _chrome_digest(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _origin_commitment(origin: dict, binding: ResolvedBinding) -> cdp_doorbell.WH03SignalCommitment:
    return cdp_doorbell.WH03SignalCommitment(
        origin["registration_id"], origin["marker_digest"], binding.chat_url,
        binding.generation, origin["issued_ms"], origin["expires_ms"])


def _validate_local_origin(origin: dict, registration: Registration) -> None:
    _require(type(origin) is dict and origin.keys() == {
        "registration_id", "marker_digest", "issued_ms", "expires_ms",
        "owner_scope", "chrome_config", "phase", "navigation",
    } and registration.evidence_type == "GITHUB_AUTHENTICATED"
        and origin["phase"] in ("PREPARED", "ENROLLED")
        and _chrome_digest(origin["owner_scope"]) and _chrome_digest(origin["chrome_config"])
        and type(origin["registration_id"]) is str
        and re.fullmatch(r"[0-9a-f]{32}", origin["registration_id"]) is not None
        and cdp_doorbell._wh03_commitment_gate_shape(
            _origin_commitment(origin, registration.binding)) == "READY",
        "LOCAL_ORIGIN_JOURNAL_INVALID")
    navigation = origin["navigation"]
    _require(navigation is None or (
        origin["phase"] == "ENROLLED" and type(navigation) is dict
        and navigation.keys() == {"process", "phase"}
        and _chrome_digest(navigation["process"])
        and navigation["phase"] in ("RESERVED", "RESOLVED")), "LOCAL_ORIGIN_JOURNAL_INVALID")


def _route_receipt(identity: SourceIdentity, registration: Registration, origin: dict) -> RouteReadiness:
    return RouteReadiness(identity.kind, identity.key, registration.binding.generation,
                          digest(asdict(registration.binding)), origin["registration_id"],
                          origin["marker_digest"])


_EXACT_ROUTE_READY = ("EXACT_CHAT_REUSED", "EXACT_CHAT_PRELOADED", "EXACT_CHAT_RECONCILED")


def _validate_route_receipt(proof: object, identity: SourceIdentity | None,
                            registration: Registration, origin: dict | None) -> None:
    _require(type(proof) is dict and identity is not None and origin is not None
             and origin["phase"] == "ENROLLED"
             and proof.get("readiness") in ("LOCAL_ROUTE_REGISTERED", *_EXACT_ROUTE_READY),
             "LOCAL_ROUTE_RECEIPT_INVALID")
    expected = replace(_route_receipt(identity, registration, origin), readiness=proof["readiness"])
    _require(type(proof.get("route_generation")) is int and proof == asdict(expected),
             "LOCAL_ROUTE_RECEIPT_INVALID")


def _demand_target(owner: tuple, env: chrome_acquisition.ChromeEnvironment) -> tuple:
    target, candidates = chrome_acquisition._clean_target(owner)
    _require(target.command() == env.command() and target.endpoint == env.endpoint,
             "CHROME_ACQUISITION_TARGET_MISMATCH")
    return target, candidates


def _ready_demand_task(owner: tuple, target, candidates: tuple) -> str:
    """Check an idle, exact native demand task and return its expected action pin."""
    acq = chrome_acquisition
    receipt = acq._clean_reconcile_read(owner, target, candidates)
    task = receipt.get("task")
    _require(task != "DEMAND_DISABLED", "CHROME_DEMAND_TASK_DISABLED")
    _require(task != "DEMAND_NOT_READY", "CHROME_DEMAND_TASK_BUSY")
    _require(task == "DEMAND_READY" and receipt.get("journal") in ("FRESH", "COMPLETE")
             and receipt.get("d5") in ("ABSENT", "ENABLED_ATTESTED", "DISABLED_ATTESTED"),
             "CHROME_DEMAND_TASK_UNPROVEN")
    _require(acq._clean_task(owner, target, candidates, install=False) == "ALREADY_ENROLLED",
             "CHROME_DEMAND_TASK_UNPROVEN")
    shell = acq._powershell()
    gui, arguments = acq._clean_demand_action(shell, acq._clean_launcher(owner, target, candidates))
    # Probe the existing launch lane. Release it before RunEx so the demand
    # action can acquire its own mutex and perform its native checks.
    with acq._clean_supervisor_slot():
        _require(not acq._clean_pending(acq._CLEAN_TASK_PENDING), "CHROME_DEMAND_TASK_UNRESOLVED")
    return digest([str(gui), arguments])


def _issue_record_event(raw: dict, registration: Registration) -> SourceEvent:
    _require(set(raw) == {"kind", "payload_sha256", "registration"} and raw["kind"] == _ISSUE,
             "JOURNAL_INVALID")
    issue = IssueRegistration.from_dict(raw["registration"])
    issue.check_route(registration)
    return SourceEvent(issue.identity, raw["payload_sha256"], issue)


def _event_columns(raw: dict, registration: Registration) -> tuple:
    if raw.get("kind") == _ISSUE:
        try:
            issue = _issue_record_event(raw, registration).payload
        except (TypeError, RuntimeHold):
            raise RuntimeHold("JOURNAL_INVALID") from None
        # Zero is a storage sentinel. Issues have no run attempt or GitHub SHA.
        return "github-issue-created-v1:" + issue.identity.key, issue.local_source_sha, 0
    return raw.get("event_id"), raw.get("source_sha"), raw.get("attempt")


def _validate_local_send(record: dict, request: DoorbellRequest, registration: Registration) -> None:
    proof = record["evidence"]
    reservation = proof["local_send"]
    fields = {"phase", "doorbell_sha256", "consent_sha256"}
    _require(type(reservation) is dict and fields <= reservation.keys() <= fields | {"surface"}
        and reservation["doorbell_sha256"] == bytes_digest(request.canonical_bytes)
        and _chrome_digest(reservation["consent_sha256"])
        and proof.get("local_route", {}).get("readiness") in _EXACT_ROUTE_READY,
        "LOCAL_UI_RESERVATION_INVALID")
    status = record["status"]
    phase = reservation["phase"]
    if "surface" in reservation:
        scope = reservation["surface"]
        _require(phase == "STAGING" and type(scope) is dict
                 and scope.keys() == {"process", "document", "prior_refs"}
                 and _chrome_digest(scope["process"]) and _chrome_digest(scope["document"]),
                 "LOCAL_UI_RESERVATION_INVALID")
        refs = scope["prior_refs"]
        _require(type(refs) is list and len(refs) <= 8192
                 and all(type(ref) is str and cdp_doorbell._UI_TURN_REF.fullmatch(ref) for ref in refs)
                 and refs == sorted(set(refs)), "LOCAL_UI_RESERVATION_INVALID")
    expected_history = ["PENDING", "RESERVED"]
    expected_proof = {"local_route", "local_send"}
    if status == "RESERVED":
        _require((phase, record["code"]) in (("PRE_STAGE", _UI_SEND_RESERVED), ("STAGING", _UI_STAGING)),
                 "LOCAL_UI_RESERVATION_INVALID")
    else:
        _require(status in ("UI_TURN_OBSERVED", "UNKNOWN_EFFECT") and phase == "STAGING",
                 "LOCAL_UI_RESERVATION_INVALID")
        if status == "UI_TURN_OBSERVED":
            ref = proof.get("user_turn_ref")
            _require(type(ref) is str and cdp_doorbell._UI_TURN_REF.fullmatch(ref) is not None
                     and proof.get("marker") == registration.marker,
                     "JOURNAL_PROOF_INVALID")
            expected_proof.update(("user_turn_ref", "marker"))
            if record["code"] == _UI_TURN_RECONCILED:
                _require("surface" in reservation and ref not in reservation["surface"]["prior_refs"]
                         and type(proof.get("local_unknown_code")) is str
                         and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", proof["local_unknown_code"])
                         and proof["local_unknown_code"] not in (_UI_TURN_OBSERVED, _UI_TURN_RECONCILED),
                         "JOURNAL_PROOF_INVALID")
                expected_history.append("UNKNOWN_EFFECT")
                expected_proof.add("local_unknown_code")
            else:
                _require(record["code"] == _UI_TURN_OBSERVED, "JOURNAL_PROOF_INVALID")
        expected_history.append(status)
    _require(record["history"] == expected_history and set(proof) == expected_proof,
             "LOCAL_UI_RESERVATION_INVALID")


def _validate_records(data: dict, registration: Registration) -> None:
    _require(type(data["events"]) is dict and len(data["events"]) <= _MAX_JOURNAL_EVENTS, "JOURNAL_INVALID")
    sources = data["source_identities"]
    _require(len(sources) <= len(data["events"]), "JOURNAL_INVALID")
    by_event = {}
    for source_key, (event_key, raw_identity) in sources.items():
        try:
            identity = SourceIdentity(**raw_identity)
        except (TypeError, RuntimeHold):
            raise RuntimeHold("JOURNAL_INVALID") from None
        _require(source_key == identity.key and event_key in data["events"] and event_key not in by_event,
                 "JOURNAL_INVALID")
        by_event[event_key] = identity
    seen, seen_turns, seen_native_turns = set(), set(), set()
    for key, record in data["events"].items():
        _require(type(record) is dict and set(record) == {"event", "status", "code", "history", "evidence"}
                 and type(record["event"]) is dict and type(record["evidence"]) is dict, "JOURNAL_INVALID")
        if "local_route" in record["evidence"]:
            _validate_route_receipt(record["evidence"]["local_route"], by_event.get(key),
                                    registration, data.get("local_origin"))
        is_issue = record["event"].get("kind") == _ISSUE
        try:
            if is_issue:
                issue = _issue_record_event(record["event"], registration)
                _require(key == issue.identity.key and by_event.get(key) == issue.identity, "JOURNAL_INVALID")
                if "local_send" not in record["evidence"]:
                    _require(record["status"] == "PENDING" and record["history"] == ["PENDING"]
                             and record["code"] == _ISSUE_ACCEPTED and record["evidence"].keys() <= {"local_route"},
                             "JOURNAL_INVALID")
                    continue
            else:
                saved = Event(**record["event"])
                _require(saved.key(registration) == key and saved.event_id not in seen, "JOURNAL_INVALID")
                seen.add(saved.event_id)
                if key in by_event:
                    identity = by_event[key]
                    _require(identity.kind == _EF02 and identity.attempt == saved.attempt
                             and saved.evidence_type == "GITHUB_AUTHENTICATED", "JOURNAL_INVALID")
        except (TypeError, RuntimeHold):
            raise RuntimeHold("JOURNAL_INVALID") from None
        _require(type(record["history"]) is list and 1 <= len(record["history"]) <= 6
                 and record["history"][0] == "PENDING" and record["status"] == record["history"][-1]
                 and type(record["code"]) is str
                 and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", record["code"]) is not None, "JOURNAL_INVALID")
        _require(all(b in _NEXT.get(a, set()) for a, b in
                     zip(record["history"], record["history"][1:])), "JOURNAL_INVALID")
        proof = record["evidence"]
        if "local_send" in proof:
            _require(key in by_event, "LOCAL_UI_RESERVATION_INVALID")
            source = issue if is_issue else SourceEvent(by_event[key], saved.payload_sha256, saved)
            _validate_local_send(record, DoorbellRequest.for_source(source, registration), registration)
            if record["status"] == "UI_TURN_OBSERVED":
                _require(proof["user_turn_ref"] not in seen_turns, "USER_TURN_REFERENCE_REUSED")
                seen_turns.add(proof["user_turn_ref"])
            continue
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
    def __init__(self, connection, journal, registration, owner, source_table):
        self.connection, self.journal = connection, journal
        self.registration, self.owner = registration, owner
        self.source_table = source_table

    def write(self, data: dict) -> None:
        _validate_records(data, self.registration)
        self.journal._owner(self.owner)
        db = self.connection
        try:
            db.execute("BEGIN IMMEDIATE")
            for key, record in data["events"].items():
                db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?) "
                           "ON CONFLICT(event_key) DO UPDATE SET record=excluded.record",
                           (key, *_event_columns(record["event"], self.registration),
                            self.registration.binding.generation, json.dumps(record, sort_keys=True)))
            sources = data["source_identities"]
            if sources and not self.source_table:
                db.execute(_SOURCE_TABLE)
            for key, (event_key, identity) in sources.items():
                db.execute("INSERT INTO source_identities VALUES (?, ?, ?) ON CONFLICT(source_key) DO NOTHING",
                           (key, event_key, json.dumps(identity, sort_keys=True)))
            # Includes the rollback sidecar created by SQLite, before commit.
            self.journal._owner(self.owner)
            db.commit()  # FULL/EXTRA synchronous; no effect may precede this commit.
            self.source_table = self.source_table or bool(sources)
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

    def write_local_origin(self, record: dict) -> None:
        _validate_local_origin(record, self.registration)
        self.journal._owner(self.owner)
        db = self.connection
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(_ORIGIN_TABLE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
            db.execute("INSERT INTO local_origin VALUES (1, ?) "
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

    @staticmethod
    def _check_registration(db, owner: str, registration: Registration) -> None:
        rows = db.execute("SELECT owner, registration FROM identity WHERE singleton=1").fetchall()
        _require(len(rows) == 1 and rows[0][0] == owner, "JOURNAL_OWNER_DRIFT")
        actual = json.dumps(_decode_unique(rows[0][1]), sort_keys=True)
        _require(actual == json.dumps(asdict(registration), sort_keys=True), "REGISTRATION_DRIFT")

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
                version, source_table, origin_table = 2, False, False
                if not new:
                    version, = db.execute("PRAGMA user_version").fetchone()
                    tables = dict(_TABLES, chrome_lifecycle=_CHROME_TABLE) if version == 3 else _TABLES
                    stored_tables = db.execute("SELECT type, name, sql FROM sqlite_master "
                                               "WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                    source_table = ("table", "source_identities", _SOURCE_TABLE) in stored_tables
                    if source_table:
                        tables = dict(tables, source_identities=_SOURCE_TABLE)
                    origin_table = ("table", "local_origin", _ORIGIN_TABLE) in stored_tables
                    if origin_table:
                        tables = dict(tables, local_origin=_ORIGIN_TABLE)
                    _require(version in (2, 3)
                             and db.execute("PRAGMA application_id").fetchone() == (_APP_ID,)
                             and stored_tables == [("table", name, sql) for name, sql in sorted(tables.items())],
                             "JOURNAL_SCHEMA_INCOMPATIBLE")
                    self._check_registration(db, owner, registration)
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
                data = {"events": {}, "source_identities": {}}
                if origin_table:
                    rows = db.execute("SELECT singleton, record FROM local_origin").fetchall()
                    _require(len(rows) == 1 and rows[0][0] == 1, "LOCAL_ORIGIN_JOURNAL_INVALID")
                    data["local_origin"] = _decode_unique(rows[0][1])
                    _validate_local_origin(data["local_origin"], registration)
                if source_table:
                    for source_key, event_key, identity in db.execute("SELECT * FROM source_identities"):
                        _require(len(data["source_identities"]) < _MAX_JOURNAL_EVENTS, "JOURNAL_INVALID")
                        data["source_identities"][source_key] = (event_key, _decode_unique(identity))
                for key, event_id, sha, attempt, generation, raw in db.execute("SELECT * FROM events"):
                    _require(len(data["events"]) < _MAX_JOURNAL_EVENTS, "JOURNAL_INVALID")
                    record = _decode_unique(raw)
                    _require(type(record) is dict and type(record.get("event")) is dict, "JOURNAL_INVALID")
                    _require((event_id, sha, attempt, generation) ==
                             (*_event_columns(record["event"], registration),
                              registration.binding.generation), "JOURNAL_INVALID")
                    data["events"][key] = record
                _validate_records(data, registration)
                if version == 3:
                    rows = db.execute("SELECT singleton, record FROM chrome_lifecycle").fetchall()
                    _require(len(rows) == 1 and rows[0][0] == 1, "CHROME_JOURNAL_INVALID")
                    data["chrome"] = _decode_unique(rows[0][1])
                    _validate_chrome(data["chrome"])
                yield _SQLiteTransaction(db, self, registration, owner, source_table), data
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
                 sender: Sender | None, orchestrator: ProductionPipelineOrchestrator,
                 grant_verifier: Callable[[Event, Registration], bool] | _RegisteredIssueAuthority,
                 brain: BrainAdapter | None = None, read_only: bool = False,
                 supervisor_stop: Callable[[], bool] | None = None,
                 route_runtime: _RuntimeConfig | None = None,
                 route_chrome: _SupervisorChrome | None = None):
        _require(type(orchestrator) is ProductionPipelineOrchestrator, "PRODUCTION_ORCHESTRATOR_REQUIRED")
        _require(not (read_only and supervisor_stop is not None), "SUPERVISOR_MODE_INVALID")
        _require(supervisor_stop is None or registration.evidence_type == "GITHUB_AUTHENTICATED",
                 "SUPERVISOR_MODE_INVALID")
        self.source, self.registration, self.journal = source, registration, journal
        self.sender, self.brain, self.orchestrator = sender, brain, orchestrator
        self.grant_verifier = grant_verifier
        self.read_only = read_only
        self._supervisor_stop = supervisor_stop
        self._route_runtime, self._route_chrome = route_runtime, route_chrome

    @property
    def _observe_only(self) -> bool:
        return self.read_only or self._supervisor_stop is not None

    @property
    def _native(self) -> bool:
        return not self._observe_only and self.registration.evidence_type == "GITHUB_AUTHENTICATED"

    def run_once(self) -> Result:
        _require(self._supervisor_stop is None, "SUPERVISOR_MODE_INVALID")
        return self._run_event()

    def _run_supervised_event(self, chrome: Result) -> Result:
        _require(self._supervisor_stop is not None, "SUPERVISOR_MODE_INVALID")
        if self._supervisor_stop():
            return chrome
        event = self._run_event(chrome)
        if isinstance(event, RouteResult):
            return SupervisorRouteTickResult(event.status, event.code, event.evidence_type, event.replayed,
                                             chrome_health=chrome.code, route=event.route)
        return SupervisorTickResult(event.status, event.code, event.evidence_type, event.replayed,
                                    chrome_health=chrome.code)

    def _run_event(self, chrome: Result | None = None) -> Result:
        evidence = self.registration.evidence_type
        if type(self.source) is GitHubIssueCreatedSource:
            evidence = _ISSUE_EVIDENCE
        try:
            selected = self.source.next_event()
            if selected is None:
                if self.journal.path.exists():
                    with self.journal.locked(self.registration) as (txn, data):
                        self._recover_reservations(txn, data)
                return Result("HOLD", "NO_EVENT", evidence)
            envelope = None
            issue = type(selected) is SourceEvent
            if issue:
                _require(selected.identity.kind == _ISSUE
                         and type(self.source) is GitHubIssueCreatedSource
                         and type(self.grant_verifier) is _RegisteredIssueAuthority
                         and self.grant_verifier.source is self.source, "AUTHENTICATED_SOURCE_REQUIRED")
                envelope = selected
                key = envelope.identity.key
                encoded = {"kind": _ISSUE, "payload_sha256": envelope.payload_sha256,
                           "registration": asdict(envelope.payload)}
                code = _ISSUE_ACCEPTED
            else:
                _require(type(selected) is Event and type(self.source) is not GitHubIssueCreatedSource,
                         "EVENT_REQUIRED")
                if type(self.source) is GitHubEventSource:
                    envelope = self.source.envelope(selected)
                key = selected.key(self.registration)
                encoded = asdict(selected)
                code = "EVENT_PENDING"
            with self.journal.locked(self.registration) as (txn, data):
                if issue:
                    self.grant_verifier.authorize(selected, self.registration)
                records = data["events"]
                sources = data["source_identities"]
                previous = None
                if envelope is not None:
                    identity = envelope.identity
                    previous = sources.get(identity.key)
                    _require(previous in (None, (key, asdict(identity))), "EVENT_IDENTITY_DRIFT")
                    _require(previous is not None or not any(k == key for k, _ in sources.values()),
                             "EVENT_IDENTITY_DRIFT")
                if key in records:
                    record = records[key]
                    _require(record["event"] == encoded, "EVENT_IDENTITY_DRIFT")
                    replayed = True
                else:
                    _require(issue or not any(
                        r["event"].get("event_id") == selected.event_id for r in records.values()),
                             "EVENT_IDENTITY_DRIFT")
                    _require(len(records) < _MAX_JOURNAL_EVENTS, "JOURNAL_FULL")
                    record = {"event": encoded, "status": "PENDING", "code": code,
                              "history": ["PENDING"], "evidence": {}}
                    records[key] = record
                    replayed = False
                if envelope is not None and previous is None:
                    sources[identity.key] = (key, asdict(identity))
                if not replayed or (envelope is not None and previous is None):
                    txn.write(data)
                self._recover_reservations(txn, data)
                if "local_send" in record["evidence"]:
                    route = RouteReadiness(**record["evidence"]["local_route"])
                    if record["status"] == "UNKNOWN_EFFECT" and envelope is not None:
                        self._reconcile_local_ui(txn, data, record, envelope, chrome)
                    return RouteResult(record["status"], record["code"], evidence, replayed=True, route=route)
                if (self._route_runtime is not None and envelope is not None
                        and record["status"] == "PENDING"
                        and not (record["code"] == _LAUNCH_UNKNOWN and chrome is None)):
                    return self._run_local_route(txn, data, record, envelope, evidence, replayed, chrome)
                if issue:
                    return Result("HOLD_DEPENDENCY", _ISSUE_ROUTE_DEPENDENCY, evidence, replayed)
                if record["status"] != "PENDING":
                    return self._resume(txn, data, record, selected)
                return self._process_pending(txn, data, record, selected, chrome)
        except DependencyHold as exc:
            return Result("HOLD_DEPENDENCY", _safe_code(exc, "NATIVE_DEPENDENCY_UNAVAILABLE"), evidence)
        except RuntimeHold as exc:
            return Result("HOLD", str(exc), evidence)
        except (ProvenanceError, TriggerBlocked, PrivateStateError, HandoffBlocked,
                BindingError, RegistrationBlocked, GitHubReadBlocked) as exc:
            return Result("HOLD", _safe_code(exc, "SOURCE_OR_JOURNAL_UNPROVEN"), evidence)
        except Exception:
            return Result("HOLD", "SOURCE_OR_JOURNAL_UNPROVEN", evidence)

    def _run_local_route(self, txn, data: dict, record: dict, event: SourceEvent,
                         evidence: str, replayed: bool, chrome: Result | None) -> Result:
        registration = self.registration
        _require(txn.owner == self._route_runtime.principal, "JOURNAL_OWNER_DRIFT")

        def authorize() -> None:
            self._authorize_local_source(event)

        authorize()
        origin = data.get("local_origin")
        if origin is None or origin["phase"] != "ENROLLED":
            return Result("HOLD_DEPENDENCY", "LOCAL_CHAT_REGISTRATION_REQUIRED", evidence, replayed)
        route = _route_receipt(event.identity, registration, origin)

        def save(receipt: RouteReadiness) -> None:
            proof = asdict(receipt)
            if record["evidence"].get("local_route") != proof:
                record["evidence"]["local_route"] = proof
                txn.write(data)

        if "local_route" not in record["evidence"]:
            save(route)
        if chrome is None:
            code = "EXACT_CHAT_SUPERVISOR_REQUIRED"
        elif chrome.code not in _CHROME_READY or self._route_chrome is None:
            code = chrome.code
        else:
            code = self._route_chrome.ready_route(txn, data, origin, authorize)
        if code in _EXACT_ROUTE_READY:
            route = replace(route, readiness=code)
            save(route)
            if self._supervisor_stop is not None and self._route_runtime.raw.get("ui_delivery") is not None:
                return self._send_local_ui(txn, data, record, event, route, evidence, replayed, authorize)
        status: Status = "HOLD"
        if chrome is not None and code == chrome.code:
            status = chrome.status
        elif code in (_LAUNCH_UNKNOWN, "EXACT_CHAT_PRELOAD_RECONCILE_REQUIRED"):
            status = "UNKNOWN_EFFECT"
        return RouteResult(status, code, evidence, replayed, route=route)

    def _authorize_local_source(self, event: SourceEvent) -> None:
        authority = self.grant_verifier
        if type(authority) is _RegisteredIssueAuthority and authority.source is self.source:
            authority.authorize(event, self.registration)
        else:
            _require(type(authority) is _RegisteredWakeAuthority
                     and authority.source is self.source and not authority.native_source_only,
                     "SIGNED_WAKE_AUTHORITY_UNAVAILABLE")
            _require(authority.source.envelope(event.payload) == event, "SOURCE_PROOF_MISSING")
            _require(authority.authorize(event.payload, self.registration) is not None, "MISSING_GRANT")

    @staticmethod
    def _ui_consent_digest(consent: dict, grant: dict) -> str:
        return digest({**{k: v for k, v in consent.items() if k != "grants"}, "grant": grant})

    def _reconcile_local_ui(self, txn, data: dict, record: dict, event: SourceEvent,
                            health: Result | None) -> None:
        runtime, chrome = self._route_runtime, self._route_chrome
        proof = record["evidence"]
        reservation = proof["local_send"]
        scope = reservation.get("surface")
        if (scope is None or self._supervisor_stop is None or runtime is None or chrome is None
                or health is None or health.code != "CHROME_OWNED" or runtime.chrome_lifecycle == "paused"):
            return
        consent = runtime.raw.get("ui_delivery")
        if consent is None:
            return
        grant = next((g for g in consent["grants"] if g["source_key"] == event.identity.key), None)
        if grant is None or self._ui_consent_digest(consent, grant) != reservation["consent_sha256"]:
            return
        origin = data["local_origin"]
        request = DoorbellRequest.for_source(event, self.registration)
        if (consent["route_marker"], consent["registration_id"], consent["marker_digest"]) != (
                request.marker, origin["registration_id"], origin["marker_digest"]):
            return

        def revalidate() -> None:
            _require(grant["expires_ms"] > time.time_ns() // 1_000_000, "LOCAL_UI_DELIVERY_GRANT_EXPIRED")
            _require(data["chrome"]["launch"] is None and not chrome_acquisition._clean_launch_pending(),
                     "CHROME_LAUNCH_RECONCILE_REQUIRED")
            _require(data["chrome"]["process"] == scope["process"], "CHROME_OBSERVATION_CHANGED")
            current = chrome._route_snapshot(txn, origin)
            _require(_owned_chrome_process(current[4], current[5])[0] == scope["process"],
                     "CHROME_OBSERVATION_CHANGED")
            # The exact v2 owner opt-in and current source route permission
            # gate the read. The EF02 deliver_wake operation is only for Send.
            self._authorize_local_source(event)
            _require(not self._supervisor_stop(), "SUPERVISOR_STOPPED")

        try:
            revalidate()
            adapter = LocalChromeDoorbellAdapter(repository=event.identity.repository, send_only=True,
                                                 inventory=chrome.sender.adapter.inventory)
            outcome = adapter.observe_registered_ui(
                binding=request.binding, event_id=request.event_id, marker_digest=origin["marker_digest"],
                revalidate=revalidate, surface=scope["document"], prior_refs=scope["prior_refs"])
        except Exception:
            return  # An inconclusive observation leaves the original row intact.
        ref = outcome.turn_ref
        if outcome.code != _UI_TURN_RECONCILED or ref is None or self._supervisor_stop() or any(
                ref in (row["evidence"].get("user_turn_ref"), row["evidence"].get("user_turn_id"))
                for row in data["events"].values()):
            return
        self._move(txn, data, record, "UI_TURN_OBSERVED", _UI_TURN_RECONCILED,
                   proof={"marker": request.marker, "user_turn_ref": ref, "local_unknown_code": record["code"]})

    def _send_local_ui(self, txn, data: dict, record: dict, event: SourceEvent,
                       route: RouteReadiness, evidence: str, replayed: bool,
                       authorize_source: Callable[[], None]) -> RouteResult:
        runtime, chrome = self._route_runtime, self._route_chrome
        registration = self.registration
        origin = data["local_origin"]
        consent = runtime.raw["ui_delivery"]
        request = DoorbellRequest.for_source(event, registration)

        def result(status: Status, code: str) -> RouteResult:
            return RouteResult(status, code, evidence, replayed, route=route)

        grant = next((grant for grant in consent["grants"] if grant["source_key"] == event.identity.key), None)
        if grant is None:
            return result("HOLD_DEPENDENCY", "LOCAL_UI_DELIVERY_GRANT_REQUIRED")

        initial = chrome._route_snapshot(txn, origin)
        process, _ = _owned_chrome_process(initial[4], initial[5])
        _require(process == data["chrome"]["process"], "CHROME_OBSERVATION_CHANGED")
        if event.identity.kind == _EF02:
            # A historical row lacks a run ID until its exact event is observed.
            # An unresolved same-attempt row may alias this source.
            indexed = {key for key, _ in data["source_identities"].values()}
            if any(key not in indexed and old["status"] != "PENDING"
                   and old["event"].get("evidence_type") == evidence
                   and old["event"].get("attempt") == event.identity.attempt
                   for key, old in data["events"].items()):
                return result("HOLD_DEPENDENCY", "LEGACY_EF02_SOURCE_IDENTITY_UNAVAILABLE")

        def revalidate() -> None:
            _require(runtime.chrome_lifecycle != "paused", "PAUSED_BY_HUMAN")
            _require(data["chrome"]["launch"] is None and not chrome_acquisition._clean_launch_pending(),
                     "CHROME_LAUNCH_RECONCILE_REQUIRED")
            _require(chrome._route_snapshot(txn, origin) == initial, "CHROME_OBSERVATION_CHANGED")
            _require((consent["route_marker"], consent["registration_id"], consent["marker_digest"])
                     == (registration.marker, origin["registration_id"], origin["marker_digest"]),
                     "LOCAL_UI_ENROLLMENT_DRIFT")
            if event.identity.kind == _EF02:
                authority = self.grant_verifier
                _require(type(authority) is _RegisteredWakeAuthority and authority.source is self.source
                         and authority.source.envelope(event.payload) == event, "SOURCE_PROOF_MISSING")
                _require(authority.authorize(event.payload, registration, require_delivery=True) is not None,
                         "MISSING_GRANT")
            else:
                authorize_source()
            _require(grant["expires_ms"] > time.time_ns() // 1_000_000, "LOCAL_UI_DELIVERY_GRANT_EXPIRED")
            _require(not chrome.stop(), "SUPERVISOR_STOPPED")

        try:
            revalidate()
        except DependencyHold as exc:
            return result("HOLD_DEPENDENCY", _safe_code(exc, "LOCAL_UI_AUTHORITY_UNPROVEN"))
        except Exception as exc:
            return result("HOLD", _safe_code(exc, "LOCAL_UI_AUTHORITY_UNPROVEN"))

        adapter = LocalChromeDoorbellAdapter(repository=event.identity.repository, allow_submit=True,
                                             send_only=True, inventory=chrome.sender.adapter.inventory)
        reservation = {
            "phase": "PRE_STAGE",
            "doorbell_sha256": bytes_digest(request.canonical_bytes),
            "consent_sha256": self._ui_consent_digest(consent, grant),
        }
        self._move(txn, data, record, "RESERVED", _UI_SEND_RESERVED, proof={"local_send": reservation})

        def before_stage(surface: str, refs: list[str]) -> None:
            # On restart, an interrupted PRE_STAGE reservation may be retried.
            # STAGING is durable before the first page mutation can start.
            reservation["phase"] = "STAGING"
            reservation["surface"] = {"process": process, "document": surface, "prior_refs": refs}
            record["code"] = _UI_STAGING
            txn.write(data)

        outcome = adapter.send_registered_ui(
            binding=request.binding, event_id=request.event_id, marker_digest=route.marker_commitment,
            revalidate=revalidate, before_stage=before_stage)
        if not outcome.staged:
            self._release_local_reservation(record)
            txn.write(data)
            status = "HOLD_DEPENDENCY" if outcome.code == "DELIVER_WAKE_GRANT_REQUIRED" else "HOLD"
            return result(status, outcome.code)

        ref = outcome.turn_ref
        if outcome.code == _UI_TURN_OBSERVED and ref is not None:
            if any(other["evidence"].get("user_turn_ref") == ref for other in data["events"].values()):
                self._move(txn, data, record, "UNKNOWN_EFFECT", "USER_TURN_REFERENCE_REUSED")
                return result("UNKNOWN_EFFECT", "USER_TURN_REFERENCE_REUSED")
            self._move(txn, data, record, "UI_TURN_OBSERVED", _UI_TURN_OBSERVED,
                       proof={"marker": registration.marker, "user_turn_ref": ref})
            return result("UI_TURN_OBSERVED", _UI_TURN_OBSERVED)
        code = outcome.code if outcome.code != _UI_TURN_OBSERVED else "SUBMISSION_UNPROVEN"
        self._move(txn, data, record, "UNKNOWN_EFFECT", code)
        return result("UNKNOWN_EFFECT", code)

    @staticmethod
    def _release_local_reservation(record: dict) -> None:
        del record["evidence"]["local_send"]
        pending = _ISSUE_ACCEPTED if record["event"].get("kind") == _ISSUE else "EVENT_PENDING"
        record.update(status="PENDING", code=pending, history=["PENDING"])

    @staticmethod
    def _recover_reservations(txn, data: dict) -> None:
        interrupted = [record for record in data["events"].values() if record["status"] == "RESERVED"]
        for record in interrupted:
            if record["evidence"].get("local_send", {}).get("phase") == "PRE_STAGE":
                Runner._release_local_reservation(record)
            else:
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
        if self._supervisor_stop is not None and type(self.source) is GitHubIssueCreatedSource:
            _require(type(self.grant_verifier) is _RegisteredIssueAuthority
                     and self.grant_verifier.source is self.source, "AUTHENTICATED_SOURCE_REQUIRED")
            return
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
        if self._native:
            # Old EF02 rows omitted the run ID. Re-read their exact registered
            # events to index them before any new Send that could alias them.
            indexed = {key for key, _ in data["source_identities"].values()}
            if any(key not in indexed and old["status"] != "PENDING"
                   and old["event"].get("evidence_type") == evidence
                   and old["event"].get("attempt") == event.attempt
                   for key, old in data["events"].items()):
                raise DependencyHold("LEGACY_EF02_SOURCE_IDENTITY_UNAVAILABLE")
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
        if (self._observe_only or status != "UNKNOWN_EFFECT" or record["evidence"].keys() - {"local_route"}
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


def _revalidate_owned(snapshots: tuple[tuple[Path, str], ...], repository_root: Path,
                      config_path: Path, principal: str) -> None:
    for path, snapshot_json in snapshots:
        _require(json.dumps(_read_owned_json(path, repository_root), sort_keys=True) == snapshot_json,
                 "LOCAL_AUTHORITY_DRIFT")
    _require(_private_identity(config_path) == principal, "JOURNAL_OWNER_DRIFT")


@dataclass(frozen=True, repr=False)
class _RegisteredIssueAuthority:
    source: GitHubIssueCreatedSource
    registration: Registration
    repository_root: Path
    config_path: Path
    target_path: Path
    snapshots: tuple[tuple[Path, str], ...]
    principal: str

    def revalidate_local(self) -> None:
        _revalidate_owned(self.snapshots, self.repository_root, self.config_path, self.principal)
        _require(_private_identity(self.target_path) == self.principal, "ISSUE_REGISTRATION_OWNER_DRIFT")

    def authorize(self, event: SourceEvent, current: Registration) -> None:
        _require(current == self.registration and event == self.source.verified_event
                 and event.payload == self.source.target, "ISSUE_SOURCE_PROOF_MISSING")
        self.source.target.check_route(current)
        self.revalidate_local()


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
        _revalidate_owned(self.snapshots, self.repository_root, self.config_path, self.principal)

    def authorize(self, event: Event, current: Registration, *,
                  require_delivery: bool = False) -> SignedWakeAuthorization | None:
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
        if self.native_source_only or require_delivery:
            if not SignedOriginConsentAuthority.authorize_origin(
                self.origin, self.caller, operation="deliver_wake",
                context={"event": full_event, "registry": registry}):
                raise DependencyHold("DELIVER_WAKE_GRANT_REQUIRED")
            operations += ("deliver_wake",)
        return SignedWakeAuthorization(event, current, registry_sha256(registry),
                                       self.principal, self.caller.caller_id, operations)

    def __call__(self, event: Event, current: Registration) -> bool:
        return self.authorize(event, current) is not None


def compose_read_only(config_path: Path, repository_root: Path, *, target_index: int = 0) -> Runner:
    """Select one exact registered target for read-only processing.

    R1 config can use registered_source or a bounded registered_targets list of
    {kind, path} entries. EF02 paths point to existing run registration
    records. Issue paths point to private IssueRegistration JSON owned by the
    runtime config owner. Every target uses the journal's exact route revision.
    """
    return _compose_registered(config_path, repository_root, native_source_only=False, target_index=target_index)


def compose_native_source_only(config_path: Path, repository_root: Path, *, target_index: int = 0) -> Runner:
    """Evaluate the selected source and its downstream dependencies.

    EF02 requires both resolve_claim_route and exact-event deliver_wake
    grants. Issue-created uses its local route when enrolled.
    """
    return _compose_registered(config_path, repository_root, native_source_only=True, target_index=target_index)


def _config_path(value: object, repository_root: Path) -> Path:
    _require(type(value) is str, "LOCAL_CONFIG_INVALID")
    path = Path(value)
    _private_path(path, repository_root)
    return path


def _chrome_environment(path: Path, repository_root: Path, registration: Registration):
    chrome_config = _read_owned_json(path, repository_root, limit=8192)
    local = os.environ.get("LOCALAPPDATA")
    _require(bool(local), "PROFILE_AUTHORITY_MISSING")
    environment = chrome_acquisition.load_environment(path, repository_root, local_app_data=Path(local))
    _require(environment.endpoint == registration.binding.cdp_endpoint and environment.port == 9444,
             "CDP_OWNER_MISMATCH")
    return environment, chrome_config


def _snapshots(*entries: tuple[Path, dict]) -> tuple[tuple[Path, str], ...]:
    return tuple((path, json.dumps(snapshot, sort_keys=True)) for path, snapshot in entries)


_EF02_CONFIG = frozenset({"registry", "chrome_config", "grants", "caller_id"})


def _validate_ui_delivery(consent: object) -> None:
    """Validate private owner opt-in and exact event scopes, independently of source JSON."""
    _require(type(consent) is dict and set(consent) == {
        "schema", "route_marker", "registration_id", "marker_digest", "grants",
    } and consent["schema"] == _UI_DELIVERY_SCHEMA
        and type(consent["route_marker"]) is str
        and re.fullmatch(r"wakehub-r1:[0-9a-f]{64}", consent["route_marker"]) is not None
        and type(consent["registration_id"]) is str
        and re.fullmatch(r"[0-9a-f]{32}", consent["registration_id"]) is not None
        and _chrome_digest(consent["marker_digest"])
        and type(consent["grants"]) is list and 1 <= len(consent["grants"]) <= _MAX_TARGETS,
        "LOCAL_UI_DELIVERY_CONFIG_INVALID")
    seen = set()
    for grant in consent["grants"]:
        _require(type(grant) is dict and set(grant) == {"source_key", "expires_ms"}
                 and _chrome_digest(grant["source_key"]) and grant["source_key"] not in seen
                 and _github_id(grant["expires_ms"]), "LOCAL_UI_DELIVERY_CONFIG_INVALID")
        seen.add(grant["source_key"])


@dataclass(frozen=True, repr=False)
class _RuntimeConfig:
    path: Path
    raw: dict
    principal: str
    registration: Registration
    journal: Journal
    chrome_lifecycle: str
    targets: list


def _read_runtime_config(config_path: Path, repository_root: Path) -> _RuntimeConfig:
    config = _read_owned_json(config_path, repository_root)
    source_key = "registered_targets" if "registered_targets" in config else "registered_source"
    required = {"schema", "registration", "marker", "journal", source_key}
    _require(required <= config.keys() <= required | _EF02_CONFIG | {"chrome_lifecycle", "chat_preload", "ui_delivery"}
             and config["schema"] == "WakeHubRuntimeR1.v1", "LOCAL_CONFIG_INVALID")
    _require(type(config.get("chat_preload", False)) is bool, "LOCAL_CONFIG_INVALID")
    if "ui_delivery" in config:
        _require("chat_preload" in config, "LOCAL_CHAT_REGISTRATION_CONFIG_REQUIRED")
        _validate_ui_delivery(config["ui_delivery"])
    targets = (config[source_key] if source_key == "registered_targets"
               else [{"kind": _EF02, "path": config[source_key]}])
    _require(type(targets) is list and 1 <= len(targets) <= _MAX_TARGETS, "REGISTERED_TARGETS_INVALID")
    policy = config.get("chrome_lifecycle", "observe")
    _require(policy in ("observe", "paused", "demand-start"), "LOCAL_CONFIG_INVALID")
    raw = config["registration"]
    _require(type(raw) is dict and set(raw) == {"binding", "source_sha", "evidence_type"}
             and type(raw["binding"]) is dict, "LOCAL_CONFIG_INVALID")
    registration = Registration(ResolvedBinding(**raw["binding"]), raw["source_sha"], raw["evidence_type"])
    _require(registration.evidence_type == "GITHUB_AUTHENTICATED"
             and config["marker"] == registration.marker, "REGISTRATION_MARKER_DRIFT")
    journal_path = _config_path(config["journal"], repository_root)
    _require(journal_path != config_path, "LOCAL_CONFIG_INVALID")
    return _RuntimeConfig(config_path, config, _private_identity(config_path), registration,
                          Journal(journal_path, repository_root), policy, targets)


@dataclass(repr=False)
class _SupervisorChrome:
    """One tick of the canonical local Chrome lifecycle."""

    runtime: _RuntimeConfig
    sender: NativeChrome9444
    config: dict
    stop: Callable[[], bool]
    evaluated_start: bool = False

    def _route_snapshot(self, txn, origin: dict | None = None) -> tuple:
        runtime = self.runtime
        root = runtime.journal.repository_root
        env = self.sender.environment
        _require(not self.stop(), "SUPERVISOR_STOPPED")
        state, snapshot = self.sender._owner_snapshot()
        _revalidate_owned(_snapshots((runtime.path, runtime.raw), (env.config_path, self.config)),
                          root, runtime.path, runtime.principal)
        runtime.journal._owner(txn.owner)
        _require(state == "OWNED", "CHROME_NOT_RUNNING_NATIVE_LAUNCH_CAPABILITY_REQUIRED")
        local, _, sid, env, _, _ = snapshot
        _require(_private_identity(env.config_path) == txn.owner == runtime.principal
                 and (os.name != "nt" or sid == txn.owner), "CHROME_OWNER_UNPROVEN")
        if origin is not None:
            _require(origin["owner_scope"] == _chrome_scope(local, sid, env),
                     "CHROME_OWNER_SCOPE_CHANGED")
            _require(origin["chrome_config"] == digest(self.config), "CHROME_ENROLLMENT_DRIFT")
        return snapshot

    def ready_route(self, txn, data: dict, origin: dict, authorize: Callable[[], None]) -> str:
        binding = self.runtime.registration.binding
        initial = self._route_snapshot(txn, origin)
        process, _ = _owned_chrome_process(initial[4], initial[5])
        chrome = data["chrome"]
        _require(chrome["process"] == process, "CHROME_OBSERVATION_CHANGED")
        navigation = origin["navigation"]
        navigation_reserved = navigation is not None and navigation["phase"] == "RESERVED"

        def revalidate() -> None:
            _require(self._route_snapshot(txn, origin) == initial, "CHROME_OBSERVATION_CHANGED")
            authorize()
            _require(not self.stop(), "SUPERVISOR_STOPPED")

        def observe() -> str:
            code = self.sender.local_chat(binding, marker_digest=origin["marker_digest"])
            revalidate()
            return code

        code = observe()
        if code == "LOCAL_USER_MARKER_OBSERVED":
            if navigation_reserved:
                # Current readiness resolves the desired route state, with no
                # causal proof about the earlier navigation.
                txn.write_local_origin(dict(origin, navigation={"process": process, "phase": "RESOLVED"}))
                return "EXACT_CHAT_RECONCILED"
            return "EXACT_CHAT_REUSED"
        if code != "EXACT_CHAT_ABSENT":
            return code
        if navigation_reserved:
            return "EXACT_CHAT_PRELOAD_RECONCILE_REQUIRED"
        if not self.runtime.raw["chat_preload"]:
            return "EXACT_CHAT_PRELOAD_OPT_IN_REQUIRED"
        if self.runtime.chrome_lifecycle == "paused":
            return "PAUSED_BY_HUMAN"
        if chrome["launch"] is not None:
            return _LAUNCH_UNKNOWN

        reserved = dict(origin, navigation={"process": process, "phase": "RESERVED"})
        checks = 0
        crossed = False
        guard_error = None

        def current_binding() -> ResolvedBinding | None:
            nonlocal checks, crossed, guard_error
            checks += 1
            try:
                revalidate()
                # The adapter checks once before attaching and again after its
                # one-context inventory, immediately before reuse or new_page.
                if checks == 2:
                    txn.write_local_origin(reserved)
                    try:
                        revalidate()
                    except Exception:
                        txn.write_local_origin(origin)
                        raise
                    crossed = True
                return binding
            except Exception as exc:
                guard_error = _safe_code(exc, "PRELOAD_AUTHORITY_UNPROVEN")
                return None

        outcome = self.sender.adapter.inventory.preload(binding, current_binding=current_binding)
        if not crossed:
            _require(guard_error is None, guard_error or "PRELOAD_AUTHORITY_UNPROVEN")
            return outcome
        try:
            code = observe()
        except Exception:
            return "EXACT_CHAT_PRELOAD_RECONCILE_REQUIRED"
        if outcome in ("PRELOAD_NAVIGATION_UNVERIFIED", "PRELOAD_DRAFT_PRESENT", "PRELOAD_SURFACE_UNPROVEN"):
            return outcome
        if outcome not in ("EXACT_CHAT_OPENED", "EXACT_CHAT_PRESENT"):
            return "EXACT_CHAT_PRELOAD_RECONCILE_REQUIRED"
        if code != "LOCAL_USER_MARKER_OBSERVED":
            return code
        txn.write_local_origin(dict(origin, navigation={"process": process, "phase": "RESOLVED"}))
        return "EXACT_CHAT_PRELOADED" if outcome == "EXACT_CHAT_OPENED" else "EXACT_CHAT_REUSED"

    def run_once(self, *, start_after: float) -> Result:
        runtime = self.runtime
        evidence = runtime.registration.evidence_type
        try:
            if self.stop():
                return Result("HOLD", "SUPERVISOR_STOPPED", evidence)
            runtime.journal._owner(runtime.principal)
            with runtime.journal.locked(runtime.registration) as (txn, data):
                _require(txn.owner == runtime.principal, "JOURNAL_OWNER_DRIFT")
                saved = data.get("chrome")
                if saved is None:
                    # Legacy receipts have no process or session witness. Keep
                    # the original event rows and fence starts until reconciled.
                    legacy = any(r["code"] in (_LAUNCH_UNKNOWN, _ACQUISITION_REPORTED)
                                 for r in data["events"].values())
                    saved = {"scope": None, "process": None, "launch": "UNKNOWN" if legacy else None,
                             "request": None}
                    txn.write_chrome(saved, migrate=True)
                snapshot = self.sender.lifecycle_snapshot()
                code = self._reconcile_chrome(txn, saved, snapshot)
                if code is None:
                    code = self._acquire_chrome(txn, saved, snapshot, start_after)
            return Result("UNKNOWN_EFFECT" if code == _LAUNCH_UNKNOWN else "HOLD", code, evidence)
        except DependencyHold as exc:
            return Result("HOLD_DEPENDENCY", _safe_code(exc, "NATIVE_DEPENDENCY_UNAVAILABLE"), evidence)
        except (chrome_acquisition.AcquisitionBlocked, chrome_acquisition._EnrollmentHold,
                chrome_acquisition._Lifecycle9444Hold, chrome_acquisition._Clean9444Hold,
                RuntimeHold, HandoffBlocked) as exc:
            return Result("HOLD", _safe_code(exc, "CHROME_OWNER_UNPROVEN"), evidence)
        except Exception:
            return Result("HOLD", "CHROME_OBSERVATION_UNKNOWN", evidence)

    def _reconcile_chrome(self, txn, saved: dict, snapshot: tuple) -> str | None:
        state, (local, session, sid, env, inventory, desktop), pending = snapshot
        scope = _chrome_scope(local, sid, env)
        if "request" not in saved:
            # The old digest has no recoverable session component. A verified
            # current observation can prove the exact preimage. Never guess it.
            _require(saved["scope"] in (None, _legacy_chrome_scope(local, session, sid, env)),
                     "CHROME_LEGACY_SCOPE_UNPROVEN")
        else:
            _require(saved["scope"] in (None, scope), "CHROME_OWNER_SCOPE_CHANGED")

        launch = saved["launch"]
        request = saved.get("request")
        process = saved["process"]
        if pending and launch is None:
            launch = "UNKNOWN"
        if state == "OWNED":
            process, created = _owned_chrome_process(inventory, desktop)
            if (launch == "REQUESTED" and not pending and request["desktop"] == desktop
                    and created > request["after"]):
                owner = local, session, sid
                try:
                    task = _ready_demand_task(owner, *_demand_target(owner, env))
                except (OSError, ValueError):
                    task = None
                _require(self.sender.lifecycle_snapshot() == snapshot, "CHROME_OBSERVATION_CHANGED")
                if task == request["task"]:
                    # RunEx acknowledged the request, the exact task is idle,
                    # and the verified current browser and launch lane are usable.
                    # The task does not publish a causal per-request STARTED receipt.
                    launch, request = None, None
        current = dict(scope=scope, process=process, launch=launch, request=request)
        if current != saved:
            txn.write_chrome(current)
            saved.update(current)
        if state == "OWNED":
            return "CHROME_OWNED" if launch is None else "CHROME_OWNED_LAUNCH_UNRESOLVED"
        if launch == "REQUESTED" and not pending and request["desktop"] == desktop:
            return "CHROME_ACQUISITION_NOT_READY"
        if launch is not None:
            return _LAUNCH_UNKNOWN
        return None

    def _acquire_chrome(self, txn, saved: dict, snapshot: tuple, start_after: float) -> str:
        _, (local, session, sid, env, _, desktop), _ = snapshot
        owner = (local, session, sid)
        runtime = self.runtime
        if runtime.chrome_lifecycle == "paused":
            return "PAUSED_BY_HUMAN"
        if runtime.chrome_lifecycle != "demand-start":
            raise DependencyHold("INTERACTIVE_9444_START_GRANT_REQUIRED")
        if self.stop():
            return "SUPERVISOR_STOPPED"
        _require(os.name != "nt" or sid == runtime.principal, "CHROME_OWNER_UNPROVEN")
        if monotonic() < start_after:
            return "CHROME_DEMAND_START_BACKOFF"
        self.evaluated_start = True
        path = env.config_path
        snapshots = _snapshots((runtime.path, runtime.raw), (path, self.config))
        target, candidates = _demand_target(owner, env)
        task = _ready_demand_task(owner, target, candidates)
        chrome_acquisition._verify_google_chrome(target)
        reserved = dict(saved, launch="UNKNOWN", request={
            "desktop": desktop, "task": task, "after": time_ns() // 100 + 116444736000000000,
        })
        txn.write_chrome(reserved)
        try:
            _require(_demand_target(owner, env) == (target, candidates),
                     "CHROME_ACQUISITION_TARGET_MISMATCH")
            _require(_ready_demand_task(owner, target, candidates) == task,
                     "CHROME_DEMAND_TASK_CHANGED")
            _require(self.sender.lifecycle_snapshot() == snapshot, "CHROME_OBSERVATION_CHANGED")
            # These private files are checked against the current Windows token
            # under the same owner custody as the native Chrome enrollment.
            _revalidate_owned(snapshots, runtime.journal.repository_root, runtime.path, runtime.principal)
            _require(_private_identity(path) == runtime.principal, "CHROME_OWNER_UNPROVEN")
            runtime.journal._owner(runtime.principal)
            runtime.journal._check_registration(txn.connection, runtime.principal, runtime.registration)
            _require(_supervised_desktop(session, sid) == desktop, "CHROME_OBSERVATION_CHANGED")
        except Exception:
            txn.write_chrome(saved)  # The demand effect has not been called.
            raise
        if self.stop():
            txn.write_chrome(saved)
            return "SUPERVISOR_STOPPED"
        try:
            code = chrome_acquisition._clean_task(owner, target, candidates, install=False, start=True)
        except Exception:
            return _LAUNCH_UNKNOWN
        if code in _DEMAND_NOT_ATTEMPTED:
            txn.write_chrome(saved)
            return code
        if code == "START_REQUESTED":
            reserved["launch"] = "REQUESTED"
            txn.write_chrome(reserved)
            return self._reconcile_chrome(txn, reserved, self.sender.lifecycle_snapshot())
        return _LAUNCH_UNKNOWN


def _compose_supervisor_chrome(runtime: _RuntimeConfig, stop: Callable[[], bool]) -> _SupervisorChrome:
    _require("chrome_config" in runtime.raw, "CHROME_OWNER_UNPROVEN")
    root = runtime.journal.repository_root
    path = _config_path(runtime.raw["chrome_config"], root)
    _require(path not in (runtime.path, runtime.journal.path), "LOCAL_CONFIG_INVALID")
    environment, config = _chrome_environment(path, root, runtime.registration)
    _require(_private_identity(path) == runtime.principal, "CHROME_OWNER_UNPROVEN")
    return _SupervisorChrome(runtime, NativeChrome9444(root, environment, supervised=True), config, stop)


def register_local_chat(config_path: Path | None, repository_root: Path, *,
                        registration_id: str | None = None) -> Result | cdp_doorbell.WH03PreparedRegistration:
    """Prepare or consume a short-lived, manually presented local marker."""
    prepared = None

    def register() -> Result:
        nonlocal prepared
        _require(config_path is not None, "LOCAL_AUTHORITY_MISSING")
        _, _, sid = chrome_acquisition._enrollment_operator()
        runtime = _read_runtime_config(config_path, repository_root)
        _require(os.name != "nt" or sid == runtime.principal, "CHROME_OWNER_UNPROVEN")
        _require("chat_preload" in runtime.raw, "LOCAL_CHAT_REGISTRATION_CONFIG_REQUIRED")
        chrome = _compose_supervisor_chrome(runtime, lambda: False)
        binding = runtime.registration.binding
        if registration_id is not None:
            _require(type(registration_id) is str
                     and re.fullmatch(r"[0-9a-f]{32}", registration_id) is not None
                     and runtime.journal.path.exists(), "LOCAL_CHAT_REGISTRATION_ID_MISMATCH")
        with runtime.journal.locked(runtime.registration) as (txn, data):
            origin = data.get("local_origin")
            _require(origin is None or origin["phase"] != "ENROLLED", "LOCAL_CHAT_ALREADY_REGISTERED")
            before = chrome._route_snapshot(txn, origin)
            commitment = None
            if registration_id is not None:
                _require(origin is not None and origin["registration_id"] == registration_id,
                         "LOCAL_CHAT_REGISTRATION_ID_MISMATCH")
                commitment = _origin_commitment(origin, binding)
                gate = cdp_doorbell._wh03_commitment_gate(commitment)
                _require(gate == "READY", gate)
            code = chrome.sender.local_chat(binding, marker_digest=commitment.marker_digest if commitment else None)
            _require(chrome._route_snapshot(txn, origin) == before, "CHROME_OBSERVATION_CHANGED")
            if commitment is not None:
                _require(code == "LOCAL_USER_MARKER_OBSERVED", code)
                gate = cdp_doorbell._wh03_commitment_gate(commitment)
                _require(gate == "READY", gate)
                txn.write_local_origin(dict(origin, phase="ENROLLED"))
                return Result("HOLD", "LOCAL_CHAT_REGISTERED_UI_EVIDENCE", "SOURCE_ONLY")

            _require(code == "CANDIDATE_READY", code)
            local, _, owner, env, _, _ = before
            marker = "WAKEHUB_TEST_REGISTER_V1:" + token_hex(32)
            marker_digest = bytes_digest(marker.encode("ascii"))
            attempt_id = token_hex(16)
            _require(origin is None or (origin["registration_id"] != attempt_id
                                        and origin["marker_digest"] != marker_digest),
                     "LOCAL_CHAT_REGISTRATION_REPLAY")
            issued_ms = time.time_ns() // 1_000_000
            expires_ms = issued_ms + 300_000
            txn.write_local_origin({
                "registration_id": attempt_id, "marker_digest": marker_digest,
                "issued_ms": issued_ms, "expires_ms": expires_ms, "phase": "PREPARED",
                "owner_scope": _chrome_scope(local, owner, env), "chrome_config": digest(chrome.config),
                "navigation": None,
            })
            prepared = cdp_doorbell.WH03PreparedRegistration(attempt_id, marker, expires_ms)
            return Result("HOLD", "LOCAL_CHAT_MARKER_PREPARED", "SOURCE_ONLY")

    result = _registered_result(register)
    return prepared if prepared is not None and result.code == "LOCAL_CHAT_MARKER_PREPARED" else result


def _compose_registered(config_path: Path, repository_root: Path, *, native_source_only: bool,
                        target_index: int = 0) -> Runner:
    runtime = _read_runtime_config(config_path, repository_root)
    _require(type(target_index) is int and 0 <= target_index < len(runtime.targets),
             "REGISTERED_TARGET_INDEX_INVALID")
    return _compose_target(runtime, runtime.targets[target_index], native_source_only=native_source_only)


def _compose_target(runtime: _RuntimeConfig, target_entry: object, *, native_source_only: bool = False,
                    supervisor_stop: Callable[[], bool] | None = None,
                    chrome: _SupervisorChrome | None = None) -> Runner:
    config_path, config, owner = runtime.path, runtime.raw, runtime.principal
    registration, journal = runtime.registration, runtime.journal
    repository_root = journal.repository_root
    _require(type(target_entry) is dict and set(target_entry) == {"kind", "path"}
             and target_entry["kind"] in (_EF02, _ISSUE), "REGISTERED_TARGET_INVALID")
    kind = target_entry["kind"]
    route_runtime = runtime if "chat_preload" in config and (not native_source_only or kind == _ISSUE) else None
    paths = {"registered_source": _config_path(target_entry["path"], repository_root),
             "journal": journal.path}
    _require(len(set(paths.values()) | {config_path}) == 3, "LOCAL_CONFIG_INVALID")
    token = os.environ.get("AI_COMMERCE_GITHUB_READ_TOKEN", "")

    if kind == _ISSUE:
        registered = _read_owned_json(paths["registered_source"], repository_root, limit=8192)
        issue = IssueRegistration.from_dict(registered)
        issue.check_route(registration)
        _require(bool(token) and len(token) <= 4096 and not any(c in token for c in "\r\n\0"),
                 "GITHUB_CREDENTIAL_INVALID")
        _require(_private_identity(paths["registered_source"]) == owner, "ISSUE_REGISTRATION_OWNER_DRIFT")
        snapshots = _snapshots((config_path, config), (paths["registered_source"], registered))
        source = GitHubIssueCreatedSource(
            issue, token=token, read_budget=GitHubReadBudget(max_reads=16, max_seconds=60, max_attempts=1))
        issue_authority = _RegisteredIssueAuthority(source, registration, repository_root, config_path,
                                                   paths["registered_source"], snapshots, owner)
        return Runner(source=source, registration=registration, journal=journal,
                      sender=chrome.sender if chrome is not None else None,
                      orchestrator=ProductionPipelineOrchestrator(), grant_verifier=issue_authority,
                      read_only=supervisor_stop is None, supervisor_stop=supervisor_stop,
                      route_runtime=route_runtime, route_chrome=chrome)

    source_fields = _EF02_CONFIG if route_runtime is None else _EF02_CONFIG - {"chrome_config"}
    _require(source_fields <= config.keys(), "LOCAL_CONFIG_INVALID")
    _require(type(config["caller_id"]) is str and _ROUTE.fullmatch(config["caller_id"]) is not None,
             "LOCAL_CONFIG_INVALID")
    authority_paths = ("registry", "grants", "chrome_config") if route_runtime is None else ("registry", "grants")
    for name in authority_paths:
        paths[name] = _config_path(config[name], repository_root)
    _require(len(set(paths.values()) | {config_path}) == len(paths) + 1, "LOCAL_CONFIG_INVALID")
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
    if route_runtime is not None:
        # Browser readiness and its private config are checked on the route
        # lane, after persisting authenticated source admission.
        environment = chrome.sender.environment if chrome is not None else None
    elif supervisor_stop is not None:
        _require(chrome is not None, "CHROME_OWNER_UNPROVEN")
        environment, chrome_config = chrome.sender.environment, chrome.config
    else:
        environment, chrome_config = _chrome_environment(paths["chrome_config"], repository_root, registration)
    # Existing operator-only environment contracts; never read from Issue JSON.
    _require(bool(token) and len(token) <= 4096 and not any(c in token for c in "\r\n\0"),
             "GITHUB_CREDENTIAL_INVALID")
    key_hex = os.environ.get("AI_COMMERCE_WAKE_HMAC_KEY_HEX", "")
    _require(re.fullmatch(r"[0-9a-fA-F]{64,8192}", key_hex) is not None and len(key_hex) % 2 == 0,
             "WAKE_CREDENTIAL_INVALID")
    key = bytes.fromhex(key_hex)
    origin = SignedOriginConsentAuthority(grants=grants["grants"], secret_provider=lambda: key,
                                          principal_provider=lambda: _private_identity(config_path))
    caller = LocalCallerAttestation(config["caller_id"], is_local=True, attestation_verified=True)
    budget = GitHubReadBudget(max_reads=16, max_seconds=60, max_attempts=1)
    reader = GitHubExactRunReader(token_provider=lambda: token, read_budget=budget)
    authority = GitHubRestArtifactAuthority(repository=intent.repository, workflow_id=target.workflow_id,
                                             token_provider=lambda: token, read_budget=budget)
    source = GitHubEventSource(reader, target, authority, expected_event_id=registered["event_id"])

    snapshots = _snapshots((config_path, config), (paths["registered_source"], registered),
                           (paths["registry"], registry), (paths["grants"], grants))
    if route_runtime is None:
        snapshots += _snapshots((paths["chrome_config"], chrome_config))
    verify_local_authority = _RegisteredWakeAuthority(
        source, registration, repository_root, config_path, paths["registry"], snapshots,
        json.dumps(registry, sort_keys=True), origin, caller, owner, native_source_only)
    if chrome is not None:
        sender = chrome.sender
    elif native_source_only:
        sender = NativeSender(repository_root, registration, environment)
    else:
        sender = NativeChrome9444(repository_root, environment)
    return Runner(source=source, registration=registration, journal=journal,
                  sender=sender, brain=NativeBrainAdapter(),
                  orchestrator=ProductionPipelineOrchestrator(), grant_verifier=verify_local_authority,
                  read_only=not native_source_only and supervisor_stop is None,
                  supervisor_stop=supervisor_stop, route_runtime=route_runtime, route_chrome=chrome)


def run_read_only(config_path: Path | None, repository_root: Path, *, dry_run: bool = False,
                  target_index: int = 0) -> Result:
    return _run_registered(config_path, repository_root, dry_run=dry_run,
                           compose=lambda p, r: compose_read_only(p, r, target_index=target_index))


def run_native_source_only(config_path: Path | None, repository_root: Path, *, dry_run: bool = False,
                           target_index: int = 0) -> Result:
    return _run_registered(
        config_path, repository_root, dry_run=dry_run,
        compose=lambda p, r: compose_native_source_only(p, r, target_index=target_index))


def _run_registered(config_path: Path | None, repository_root: Path, *, dry_run: bool,
                    compose: Callable[[Path, Path], Runner]) -> Result:
    if dry_run:
        return Result("HOLD", "DRY_RUN_NO_EFFECTS", "SOURCE_ONLY")
    if config_path is None:
        return Result("HOLD", "LOCAL_AUTHORITY_MISSING", "GITHUB_AUTHENTICATED")
    return _registered_result(lambda: compose(config_path, repository_root).run_once())


def _registered_result(run: Callable[[], Result]) -> Result:
    try:
        return run()
    except DependencyHold as exc:
        return Result("HOLD_DEPENDENCY", _safe_code(exc, "NATIVE_DEPENDENCY_UNAVAILABLE"), "GITHUB_AUTHENTICATED")
    except (RuntimeHold, BindingError, RegistrationBlocked, TriggerBlocked, GitHubReadBlocked,
            PrivateStateError, HandoffBlocked, chrome_acquisition.AcquisitionBlocked,
            chrome_acquisition._EnrollmentHold, chrome_acquisition._Lifecycle9444Hold) as exc:
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
              on_health: Callable[[str], None] | None = None,
              _wait_after_tick: Callable[[], float] | None = None) -> Result:
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
                if _wait_after_tick is not None:
                    if stop.wait(_wait_after_tick()):
                        break
                    continue
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


@dataclass(frozen=True)
class _Retry:
    due: float = float("-inf")
    delay: int = 0

    def after(self, *, failed: bool, idle: int, cap: int) -> _Retry:
        delay = min(cap, max(idle, 2 * self.delay)) if failed else 0
        return _Retry(monotonic() + (delay or idle), delay)


def _target_key(entry: object) -> tuple[str, Path | str]:
    if (type(entry) is dict and set(entry) == {"kind", "path"}
            and entry["kind"] in (_EF02, _ISSUE) and type(entry["path"]) is str):
        return entry["kind"], Path(entry["path"])
    # Invalid entries get their own retry slot without inheriting a valid
    # target's schedule or depending on their position in the local list.
    return "invalid", bytes_digest(json.dumps(entry, sort_keys=True).encode())


def _normal_source_wait(result: Result, chrome: Result) -> bool:
    return (result.code == chrome.code or result.status in (
        "ACK", "UI_TURN_OBSERVED", "USER_TURN_AUTHENTICATED", "HOLD_DEPENDENCY")
        or result.code in ("NO_EVENT", "MISSING_GRANT", "PRELOAD_DRAFT_PRESENT", "PRELOAD_SURFACE_UNPROVEN")
        or result.code.startswith("EXACT_CHAT_"))


def run_supervisor(config_path: Path | None, repository_root: Path, *, dry_run: bool = False,
                   max_ticks: int | None = None, idle_seconds: int = 30,
                   max_backoff_seconds: int = 300, on_health: Callable[[str], None] | None = None) -> Result:
    """One Chrome health tick and at most one due source per iteration.

    max_ticks counts iterations, including health-only ticks while sources wait.
    """
    if dry_run:
        return Result("HOLD", "DRY_RUN_NO_EFFECTS", "SOURCE_ONLY")
    stop = StopEvent()
    retries: dict[tuple[str, Path | str], _Retry] = {}
    chrome_retry = _Retry()
    next_source_at = float("-inf")
    wake_at = 0.0

    def registered_tick() -> Result:
        nonlocal chrome_retry, next_source_at, wake_at
        _require(config_path is not None, "LOCAL_AUTHORITY_MISSING")
        runtime = _read_runtime_config(config_path, repository_root)
        chrome_context = None

        def chrome_tick() -> Result:
            nonlocal chrome_context
            chrome_context = _compose_supervisor_chrome(runtime, stop.is_set)
            return chrome_context.run_once(start_after=chrome_retry.due)

        chrome = _registered_result(chrome_tick)
        if chrome.code in _CHROME_READY or chrome.code in (
                "CHROME_ACQUISITION_NOT_READY", "PAUSED_BY_HUMAN", "INTERACTIVE_9444_START_GRANT_REQUIRED"):
            chrome_retry = _Retry()
        elif chrome_context is not None and chrome_context.evaluated_start:
            chrome_retry = chrome_retry.after(failed=True, idle=idle_seconds, cap=max_backoff_seconds)
        if stop.is_set():
            return chrome
        if chrome_retry.due > monotonic():
            wake_at = min(wake_at, chrome_retry.due)

        entries = {_target_key(entry): entry for entry in runtime.targets}
        for key in tuple(retries):
            if key not in entries:
                del retries[key]
        for key in entries:
            retries.setdefault(key, _Retry())

        result = Result("HOLD", "REGISTERED_TARGETS_WAITING", runtime.registration.evidence_type)
        now = monotonic()
        if now >= next_source_at:
            key = next((key for key, retry in retries.items() if retry.due <= now), None)
            if key is not None:
                next_source_at = now + idle_seconds

                def run_source() -> Result:
                    runner = _compose_target(runtime, entries[key], supervisor_stop=stop.is_set, chrome=chrome_context)
                    return runner._run_supervised_event(chrome)

                result = _registered_result(run_source)
                # Rotate only the selected key, retaining fairness across config
                # reorderings. Removed keys leave no scheduler state behind.
                retry = retries.pop(key)
                retries[key] = retry.after(failed=not _normal_source_wait(result, chrome),
                                           idle=idle_seconds, cap=max_backoff_seconds)
        wake_at = min(wake_at, max(next_source_at, min(retry.due for retry in retries.values())))
        if isinstance(result, SupervisorTickResult):
            return result
        return SupervisorTickResult(result.status, result.code, result.evidence_type, result.replayed,
                                    chrome_health=chrome.code)

    def tick() -> Result:
        nonlocal wake_at
        wake_at = monotonic() + idle_seconds
        return _registered_result(registered_tick)

    return supervise(tick, stop=stop, max_ticks=max_ticks, idle_seconds=idle_seconds,
                     max_backoff_seconds=max_backoff_seconds, on_health=on_health,
                     _wait_after_tick=lambda: max(0.0, wake_at - monotonic()))


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
