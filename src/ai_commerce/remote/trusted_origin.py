"""EF02-D D2: trusted local origin registration, attestation and claim binding."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
from pathlib import Path
import re
import secrets
import time
from typing import Callable, Mapping, Protocol

from .completion_event import CompletionEventError, _SHA, validate_completion_event
from .conversation_binding import (
    ResolvedBinding, _CHAT, _REPOSITORY, _ROUTE, _endpoint,
)
from ._private_state import PrivateJsonState, PrivateStateError

_MAX_REGISTRY_BYTES = 131072


class OriginError(ValueError):
    """Local origin registration, attestation, or claim conflict."""


class OriginAuthority(Protocol):
    """Trusted composition-root dependency, never constructed from request JSON.

    Implementations must independently authenticate the local caller, explicit
    Human consent and the exact operation/context, including registry integrity.
    No production implementation/credential source is provided by D2.
    """
    def authorize_origin(self, attestation: LocalCallerAttestation, *,
                         operation: str, context: Mapping) -> bool: ...


@dataclass(frozen=True, repr=False)
class LocalCallerAttestation:
    """Untrusted caller hints. Flags/token equality alone cannot grant authority."""
    caller_id: str
    is_local: bool
    auth_token: str | None = None
    attestation_verified: bool = False

    def is_trusted(self, expected_token: str | None = None, *,
                   authority: OriginAuthority | None = None, operation: str = "",
                   context: Mapping | None = None) -> bool:
        if (type(self) is not LocalCallerAttestation or self.is_local is not True or
                self.attestation_verified is not True or authority is None):
            return False
        try:
            return authority.authorize_origin(
                self, operation=operation, context=copy.deepcopy(dict(context or {}))) is True
        except Exception:
            return False

    def __repr__(self) -> str:
        return "LocalCallerAttestation(<private, unverified hints>)"


class LocalOriginRegistrar:
    """Private, local-only origin registrar managing chat routes and batch claims."""

    def __init__(self, registry_path: Path, repository_root: Path, *,
                 authority: OriginAuthority | None = None):
        self.registry_path = Path(registry_path)
        self.repository_root = Path(repository_root)
        self.authority = authority
        try:
            self._state = PrivateJsonState(self.registry_path, self.repository_root,
                                           label="REGISTRY", max_bytes=_MAX_REGISTRY_BYTES)
        except PrivateStateError as exc:
            raise OriginError(str(exc)) from None

    def _authorize(self, attestation, operation, context):
        if (type(attestation) is not LocalCallerAttestation or
                not LocalCallerAttestation.is_trusted(attestation, authority=self.authority,
                                                     operation=operation, context=context)):
            raise OriginError("UNVERIFIED_ORIGIN")

    @contextmanager
    def _transaction(self):
        try:
            with self._state.locked() as transaction:
                yield transaction
        except PrivateStateError as exc:
            raise OriginError(str(exc)) from None

    def read_registry(self) -> dict:
        """Safely decode and validate the private local registry file."""
        with self._transaction() as transaction:
            return self._read_registry(transaction)

    def _read_registry(self, transaction):
        return self._validate_registry(transaction.read({"version": 1, "routes": [], "claims": []}))

    def _validate_registry(self, data):
        if (not isinstance(data, dict) or set(data) != {"version", "routes", "claims"}
                or type(data.get("version")) is not int or data["version"] != 1):
            raise OriginError("REGISTRY_INVALID")
        routes, claims = data.get("routes"), data.get("claims")
        if not isinstance(routes, list) or len(routes) > 128 or not isinstance(claims, list) or len(claims) > 256:
            raise OriginError("REGISTRY_INVALID")

        seen_routes: set[str] = set()
        seen_chats: set[str] = set()
        route_keys = {"route_id", "generation", "chat_url", "cdp_endpoint", "consent"}
        for r in routes:
            if not isinstance(r, dict) or set(r) != route_keys:
                raise OriginError("REGISTRY_INVALID")
            if not isinstance(r["route_id"], str) or not _ROUTE.fullmatch(r["route_id"]):
                raise OriginError("ROUTE_INVALID")
            if type(r["generation"]) is not int or r["generation"] < 1:
                raise OriginError("GENERATION_INVALID")
            if r["consent"] is not True:
                raise OriginError("CONSENT_REQUIRED")
            if not _endpoint(r["cdp_endpoint"]):
                raise OriginError("CDP_ENDPOINT_INVALID")
            chat = r["chat_url"]
            matched = _CHAT.fullmatch(chat) if isinstance(chat, str) else None
            if not matched:
                raise OriginError("CHAT_URL_INVALID")
            chat_uuid = matched.group(1)
            if r["route_id"] in seen_routes or chat_uuid in seen_chats:
                raise OriginError("ROUTE_CONFLICT")
            seen_routes.add(r["route_id"])
            seen_chats.add(chat_uuid)

        seen_batches: set[tuple[str, str, int]] = set()
        route_lookup = {r["route_id"]: r for r in routes}
        claim_keys = {"route_id", "generation", "repository", "batch_id", "source_sha", "issue_number"}
        for c in claims:
            if not isinstance(c, dict) or set(c) != claim_keys:
                raise OriginError("REGISTRY_INVALID")
            if not isinstance(c["route_id"], str) or c["route_id"] not in seen_routes:
                raise OriginError("ROUTE_NOT_FOUND")
            if type(c["generation"]) is not int or c["generation"] < 1:
                raise OriginError("GENERATION_INVALID")
            if c["generation"] > route_lookup[c["route_id"]]["generation"]:
                raise OriginError("FUTURE_GENERATION_CLAIM")
            if not isinstance(c["repository"], str) or not _REPOSITORY.fullmatch(c["repository"]):
                raise OriginError("REGISTRY_INVALID")
            if not isinstance(c["batch_id"], str) or not _ROUTE.fullmatch(c["batch_id"]):
                raise OriginError("REGISTRY_INVALID")
            if not isinstance(c["source_sha"], str) or not _SHA.fullmatch(c["source_sha"]):
                raise OriginError("REGISTRY_INVALID")
            if type(c["issue_number"]) is not int or c["issue_number"] <= 0:
                raise OriginError("REGISTRY_INVALID")
            batch_key = (c["repository"], c["batch_id"], c["issue_number"])
            if batch_key in seen_batches:
                raise OriginError("CLAIM_CONFLICT")
            seen_batches.add(batch_key)

        return data

    def write_registry(self, data: dict, *, attestation: LocalCallerAttestation | None = None) -> None:
        """Retained API; replacement could erase claims or roll back generations."""
        self._authorize(attestation, "write_registry", {})
        raise OriginError("REGISTRY_REPLACEMENT_FORBIDDEN")

    def register_route(
        self,
        *,
        route_id: str,
        chat_url: str,
        cdp_endpoint: str,
        consent: bool = False,
        generation: int = 1,
        attestation: LocalCallerAttestation | None = None,
    ) -> dict:
        """Register or advance an exact chat route with explicit human consent."""
        self._authorize(attestation, "register_route", {
            "route_id": route_id, "chat_url": chat_url, "cdp_endpoint": cdp_endpoint,
            "consent": consent, "generation": generation,
        })
        if consent is not True:
            raise OriginError("CONSENT_REQUIRED")
        if not isinstance(route_id, str) or not _ROUTE.fullmatch(route_id):
            raise OriginError("ROUTE_INVALID")
        if type(generation) is not int or generation < 1:
            raise OriginError("GENERATION_INVALID")
        if not isinstance(chat_url, str):
            raise OriginError("CHAT_URL_INVALID")
        matched = _CHAT.fullmatch(chat_url)
        if not matched:
            raise OriginError("CHAT_URL_INVALID")
        chat_uuid = matched.group(1)
        if not _endpoint(cdp_endpoint):
            raise OriginError("CDP_ENDPOINT_INVALID")

        with self._transaction() as transaction:
            registry = self._read_registry(transaction)
            return self._register_route(transaction, registry, route_id, chat_url,
                                        chat_uuid, cdp_endpoint, generation, attestation)

    def _register_route(self, transaction, registry, route_id, chat_url, chat_uuid,
                        cdp_endpoint, generation, attestation):
        self._authorize(attestation, "register_route", {"registry": registry,
                        "route_id": route_id, "chat_url": chat_url, "cdp_endpoint": cdp_endpoint,
                        "consent": True, "generation": generation})
        routes = registry["routes"]

        existing_route = None
        for r in routes:
            r_matched = _CHAT.fullmatch(r["chat_url"])
            r_uuid = r_matched.group(1) if r_matched else None
            if r["route_id"] == route_id:
                if r_uuid != chat_uuid:
                    raise OriginError("ROUTE_CONFLICT")
                existing_route = r
            elif r_uuid == chat_uuid:
                raise OriginError("ROUTE_CONFLICT")

        if existing_route is not None:
            if generation <= existing_route["generation"]:
                raise OriginError("GENERATION_NOT_ADVANCED")
            existing_route["generation"] = generation
            existing_route["chat_url"] = chat_url
            existing_route["cdp_endpoint"] = cdp_endpoint
            existing_route["consent"] = True
            entry = dict(existing_route)
        else:
            if len(routes) >= 128:
                raise OriginError("REGISTRY_TOO_LARGE")
            entry = {
                "route_id": route_id,
                "generation": generation,
                "chat_url": chat_url,
                "cdp_endpoint": cdp_endpoint,
                "consent": True,
            }
            routes.append(entry)

        self._validate_registry(registry)
        transaction.write(registry)
        return dict(entry)

    def bind_claim(
        self,
        *,
        route_id: str,
        repository: str,
        batch_id: str,
        source_sha: str,
        issue_number: int,
        generation: int | None = None,
        attestation: LocalCallerAttestation | None = None,
    ) -> dict:
        """Bind one batch execution to an existing route at its current generation."""
        self._authorize(attestation, "bind_claim", {
            "route_id": route_id, "repository": repository, "batch_id": batch_id,
            "source_sha": source_sha, "issue_number": issue_number, "generation": generation,
        })
        if not isinstance(route_id, str) or not _ROUTE.fullmatch(route_id):
            raise OriginError("ROUTE_INVALID")
        if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
            raise OriginError("REPOSITORY_INVALID")
        if not isinstance(batch_id, str) or not _ROUTE.fullmatch(batch_id):
            raise OriginError("BATCH_ID_INVALID")
        if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
            raise OriginError("SOURCE_SHA_INVALID")
        if type(issue_number) is not int or issue_number <= 0:
            raise OriginError("ISSUE_NUMBER_INVALID")

        with self._transaction() as transaction:
            registry = self._read_registry(transaction)
            return self._bind_claim(transaction, registry, route_id, repository, batch_id,
                                    source_sha, issue_number, generation, attestation)

    def _bind_claim(self, transaction, registry, route_id, repository, batch_id,
                    source_sha, issue_number, generation, attestation):
        routes = {r["route_id"]: r for r in registry["routes"]}
        if route_id not in routes:
            raise OriginError("ROUTE_NOT_FOUND")

        route = routes[route_id]
        target_generation = generation if generation is not None else route["generation"]
        if type(target_generation) is not int or target_generation < 1:
            raise OriginError("GENERATION_INVALID")
        if target_generation < route["generation"]:
            raise OriginError("STALE_GENERATION_CLAIM")
        if target_generation > route["generation"]:
            raise OriginError("FUTURE_GENERATION_CLAIM")

        self._authorize(attestation, "bind_claim", {
            "registry": registry, "route_id": route_id, "repository": repository,
            "batch_id": batch_id, "source_sha": source_sha, "issue_number": issue_number,
            "generation": target_generation,
        })

        claims = registry["claims"]
        batch_key = (repository, batch_id, issue_number)
        for c in claims:
            if (c["repository"], c["batch_id"], c["issue_number"]) == batch_key:
                raise OriginError("CLAIM_CONFLICT")

        if len(claims) >= 256:
            raise OriginError("REGISTRY_TOO_LARGE")

        claim_entry = {
            "route_id": route_id,
            "generation": target_generation,
            "repository": repository,
            "batch_id": batch_id,
            "source_sha": source_sha,
            "issue_number": issue_number,
        }
        claims.append(claim_entry)
        self._validate_registry(registry)
        transaction.write(registry)
        return dict(claim_entry)

    def resolve_claim_route(
        self,
        *,
        repository: str,
        batch_id: str,
        source_sha: str,
        issue_number: int,
        attestation: LocalCallerAttestation | None = None,
    ) -> str:
        """Resolve the independently trusted route_id bound to a batch claim."""
        self._authorize(attestation, "resolve_claim_route", {
            "repository": repository, "batch_id": batch_id, "source_sha": source_sha,
            "issue_number": issue_number,
        })
        if (not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository) or
                not isinstance(batch_id, str) or not _ROUTE.fullmatch(batch_id) or
                not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha) or
                type(issue_number) is not int or issue_number <= 0):
            raise OriginError("CLAIM_IDENTITY_INVALID")

        registry = self.read_registry()
        return self._resolve_claim_route(registry, repository, batch_id, source_sha, issue_number, attestation)

    def _resolve_claim_route(self, registry, repository, batch_id, source_sha, issue_number, attestation):
        self._authorize(attestation, "resolve_claim_route", {
            "registry": registry, "repository": repository, "batch_id": batch_id,
            "source_sha": source_sha, "issue_number": issue_number,
        })
        route_lookup = {r["route_id"]: r for r in registry["routes"]}
        matches = []
        for c in registry["claims"]:
            if (c["repository"] == repository and c["batch_id"] == batch_id
                    and c["source_sha"] == source_sha and c["issue_number"] == issue_number):
                matches.append(c)

        if len(matches) == 0:
            raise OriginError("CLAIM_NOT_FOUND")
        if len(matches) > 1:
            raise OriginError("CLAIM_CONFLICT")

        claim = matches[0]
        route_id = claim["route_id"]
        if route_id not in route_lookup:
            raise OriginError("ROUTE_NOT_FOUND")

        route = route_lookup[route_id]
        if claim["generation"] < route["generation"]:
            raise OriginError("STALE_CLAIM")
        if claim["generation"] > route["generation"]:
            raise OriginError("FUTURE_GENERATION_CLAIM")

        return route_id

    @contextmanager
    def binding_for_delivery(self, event: Mapping, attestation: LocalCallerAttestation | None):
        """Pin one authorized registry generation through the bounded adapter call."""
        try:
            event = validate_completion_event(copy.deepcopy(event))
        except (CompletionEventError, TypeError, ValueError, RecursionError):
            raise OriginError("EVENT_INVALID") from None
        self._authorize(attestation, "deliver_wake", {"event": event})
        with self._transaction() as transaction:
            registry = self._read_registry(transaction)
            route_id = self._resolve_claim_route(
                registry, event["repository"], event["batch_id"], event["source_sha"],
                event["issue_number"], attestation)
            self._authorize(attestation, "deliver_wake", {"event": event, "registry": registry})
            route = next(r for r in registry["routes"] if r["route_id"] == route_id)
            yield ResolvedBinding(route_id=route_id, generation=route["generation"],
                                  chat_url=route["chat_url"], cdp_endpoint=route["cdp_endpoint"])


class RendezvousTrust(Enum):
    """Assigned by the private authority's enrollment, never by an adapter flag."""
    SYNTHETIC = "SYNTHETIC"
    NATIVE = "NATIVE"


class RendezvousPhase(Enum):
    PRECOMMITTED = "PRECOMMITTED"
    EMITTED = "EMITTED"
    VERIFYING = "VERIFYING"
    CONSUMED = "CONSUMED"
    HOLD = "HOLD"
    UNKNOWN = "UNKNOWN"


class RendezvousAuthority(OriginAuthority, Protocol):
    """Composition-root enrollment of exact adapter objects and their credentials.

    Implementations independently authenticate the carrier's same-chat output
    channel and observer's private browser session. Request JSON, object types,
    flags, matching tokens and Issue comments cannot enroll an adapter. No native
    authority, carrier or browser observer is supplied by this source module.
    """
    def authenticate_rendezvous_adapter(self, adapter: object, *, role: str,
                                       context: Mapping) -> RendezvousTrust | None: ...


class _PrivateRendezvousValue:
    def __repr__(self) -> str:
        return type(self).__name__ + "(<private>)"


@dataclass(frozen=True, repr=False)
class PrivateOriginSnapshot(_PrivateRendezvousValue):
    route_id: str
    generation: int
    chat_url: str
    cdp_endpoint: str
    profile_id: str
    account_id: str
    session_id: str
    login_id: str


@dataclass(frozen=True, repr=False)
class RendezvousEmission(_PrivateRendezvousValue):
    request_id: str
    marker: str
    marker_digest: str
    origin: PrivateOriginSnapshot


@dataclass(frozen=True, repr=False)
class RendezvousAck(_PrivateRendezvousValue):
    request_id: str
    marker_digest: str
    origin: PrivateOriginSnapshot
    message_id: str


@dataclass(frozen=True, repr=False)
class RendezvousHit(_PrivateRendezvousValue):
    marker: str
    origin: PrivateOriginSnapshot
    message_id: str


@dataclass(frozen=True, repr=False)
class RendezvousObservation(_PrivateRendezvousValue):
    request_id: str
    marker_digest: str
    nonce: str
    hits: tuple[RendezvousHit, ...]
    opened_origin: PrivateOriginSnapshot


class TrustedSameChatCarrier(Protocol):
    """Authenticated, bounded, same-turn output callback; invoked at most once.

    An ACK identifies the actual output message in the pinned originating chat.
    The skills-only plugin does not implement this interface.
    """
    def emit_marker(self, emission: RendezvousEmission) -> RendezvousAck: ...


class TrustedPrivateBrowserObserver(Protocol):
    """Authenticated read-only observer, independent of the output carrier.

    Capture proves the explicitly supplied opened route and its real logged-in
    profile/account/session, without selecting latest/focused tabs or inferring
    a Chat URL from host/session correlations. Search must enumerate ALL complete
    exact marker occurrences in the enrolled browser search domain (including
    copies), then freshly re-prove the actual opened origin with the nonce.
    Missing/incomplete search coverage must raise, never return a unique hit.
    No native private 9444 observer is enrolled by this module.
    """
    def capture_origin(self, *, request_id: str, binding: ResolvedBinding) -> PrivateOriginSnapshot: ...

    def search_and_reprove(self, emission: RendezvousEmission, *,
                           nonce: str) -> RendezvousObservation: ...


@dataclass(frozen=True)
class RendezvousReceipt:
    """Public, content-free diagnostic; no marker, digest or private selectors."""
    status: str
    code: str
    live_authority: bool = False


_MARKER = re.compile(r"wh03\.[0-9a-f]{64}\Z")
_RENDEZVOUS_CODES = frozenset({
    "CARRIER_UNAVAILABLE", "NATIVE_OBSERVER_NOT_CONNECTED", "UNVERIFIED_ORIGIN",
    "RENDEZVOUS_INPUT_INVALID", "RENDEZVOUS_ADAPTER_UNTRUSTED", "RENDEZVOUS_TRUST_MISMATCH",
    "RENDEZVOUS_TEST_ENTROPY_FORBIDDEN", "RENDEZVOUS_ENTROPY_INVALID",
    "RENDEZVOUS_STATE_INVALID", "RENDEZVOUS_STORE_FULL", "RENDEZVOUS_REPLAY",
    "RENDEZVOUS_NOT_FOUND", "RENDEZVOUS_ROUTE_CHANGED", "RENDEZVOUS_ORIGIN_INVALID",
    "RENDEZVOUS_EXPIRED", "RENDEZVOUS_ACK_UNKNOWN", "RENDEZVOUS_PRECOMMIT_UNKNOWN",
    "RENDEZVOUS_OBSERVATION_UNKNOWN", "RENDEZVOUS_OBSERVATION_INVALID",
    "RENDEZVOUS_SEARCH_NOT_UNIQUE", "RENDEZVOUS_ORIGIN_MISMATCH",
})


def _private_id(value: object) -> bool:
    return type(value) is str and _ROUTE.fullmatch(value) is not None


def _valid_origin(value: object) -> bool:
    return (type(value) is PrivateOriginSnapshot and _private_id(value.route_id) and
            type(value.generation) is int and value.generation >= 1 and
            type(value.chat_url) is str and _CHAT.fullmatch(value.chat_url) is not None and
            type(value.cdp_endpoint) is str and _endpoint(value.cdp_endpoint) and
            all(_private_id(item) for item in (value.profile_id, value.account_id,
                                             value.session_id, value.login_id)))


class PrivateOriginRendezvous:
    """Durable one-shot issuer and verification boundary; no browser mutations.

    The operator provisions a repository-external private directory with suitable
    Windows ACLs, as for D2. A PRECOMMITTED or VERIFYING record found after a crash
    is UNKNOWN and is never retried. An acknowledged emission can be verified
    after restart once. Records are never deleted/rebound to recycle a request.
    All dependencies, including the optional deterministic entropy seam, are
    trusted-code injection only and must be independently enrolled by authority.
    """
    def __init__(self, registrar: LocalOriginRegistrar, state_path: Path, *,
                 carrier: TrustedSameChatCarrier | None = None,
                 observer: TrustedPrivateBrowserObserver | None = None,
                 _test_entropy: Callable[[int], bytes] | None = None):
        if type(registrar) is not LocalOriginRegistrar:
            raise OriginError("UNVERIFIED_ORIGIN")
        self.registrar, self.carrier, self.observer = registrar, carrier, observer
        self._test_entropy = _test_entropy
        try:
            self._state = PrivateJsonState(Path(state_path), registrar.repository_root,
                                           label="RENDEZVOUS", max_bytes=524288)
            paths = {self._state.path.resolve(),
                     self._state.path.with_name(self._state.path.name + ".lock").resolve()}
            registry = registrar.registry_path
            if paths & {registry.resolve(), registry.with_name(registry.name + ".lock").resolve()}:
                raise OriginError("RENDEZVOUS_STATE_LOCATION_INVALID")
        except (PrivateStateError, OSError, TypeError, ValueError):
            raise OriginError("RENDEZVOUS_STATE_LOCATION_INVALID") from None

    def _adapters(self, context: dict) -> RendezvousTrust:
        if self.carrier is None:
            raise OriginError("CARRIER_UNAVAILABLE")
        if self.observer is None:
            raise OriginError("NATIVE_OBSERVER_NOT_CONNECTED")
        if self.carrier is self.observer:
            raise OriginError("RENDEZVOUS_ADAPTER_UNTRUSTED")
        try:
            authenticate = self.registrar.authority.authenticate_rendezvous_adapter
            carrier = authenticate(self.carrier, role="carrier", context=copy.deepcopy(context))
            observer = authenticate(self.observer, role="observer", context=copy.deepcopy(context))
            entropy_trust = (authenticate(self._test_entropy, role="test_entropy",
                             context=copy.deepcopy(context)) if self._test_entropy is not None else None)
        except Exception:
            raise OriginError("RENDEZVOUS_ADAPTER_UNTRUSTED") from None
        if type(carrier) is not RendezvousTrust or type(observer) is not RendezvousTrust:
            raise OriginError("RENDEZVOUS_ADAPTER_UNTRUSTED")
        if carrier is not observer:
            raise OriginError("RENDEZVOUS_TRUST_MISMATCH")
        if self._test_entropy is not None and (
                carrier is not RendezvousTrust.SYNTHETIC or entropy_trust is not RendezvousTrust.SYNTHETIC):
            raise OriginError("RENDEZVOUS_TEST_ENTROPY_FORBIDDEN")
        return carrier

    def _read(self, transaction) -> dict:
        data = transaction.read({"version": 1, "requests": {}})
        if (type(data) is not dict or set(data) != {"version", "requests"} or
                type(data["version"]) is not int or data["version"] != 1 or
                type(data["requests"]) is not dict or len(data["requests"]) > 128):
            raise OriginError("RENDEZVOUS_STATE_INVALID")
        keys = {"request_id", "origin", "marker", "digest", "trust", "phase",
                "issued_ms", "expires_ms", "message_id"}
        digests = set()
        for request_id, record in data["requests"].items():
            if (not _private_id(request_id) or type(record) is not dict or set(record) != keys or
                    record["request_id"] != request_id):
                raise OriginError("RENDEZVOUS_STATE_INVALID")
            try:
                origin = PrivateOriginSnapshot(**record["origin"])
                phase = RendezvousPhase(record["phase"])
                RendezvousTrust(record["trust"])
            except (TypeError, ValueError):
                raise OriginError("RENDEZVOUS_STATE_INVALID") from None
            marker = record["marker"]
            if (not _valid_origin(origin) or type(marker) is not str or not _MARKER.fullmatch(marker) or
                    type(record["digest"]) is not str or
                    hashlib.sha256(marker.encode("ascii")).hexdigest() != record["digest"] or
                    record["digest"] in digests or
                    type(record["issued_ms"]) is not int or record["issued_ms"] < 0 or
                    type(record["expires_ms"]) is not int or
                    not 1 <= record["expires_ms"] - record["issued_ms"] <= 300000 or
                    (record["message_id"] is not None and not _private_id(record["message_id"])) or
                    (phase in {RendezvousPhase.EMITTED, RendezvousPhase.VERIFYING,
                               RendezvousPhase.CONSUMED} and record["message_id"] is None) or
                    (phase is RendezvousPhase.PRECOMMITTED and record["message_id"] is not None)):
                raise OriginError("RENDEZVOUS_STATE_INVALID")
            digests.add(record["digest"])
        return data

    @staticmethod
    def _route(registry, route_id, generation):
        route = next((r for r in registry["routes"] if r["route_id"] == route_id), None)
        if route is None or route["generation"] != generation:
            raise OriginError("RENDEZVOUS_ROUTE_CHANGED")
        return route

    @staticmethod
    def _matches_route(origin, route):
        return _valid_origin(origin) and all(getattr(origin, key) == route[key] for key in (
            "route_id", "generation", "chat_url", "cdp_endpoint"))

    @staticmethod
    def _lease(record):
        now = time.time_ns() // 1000000
        if not record["issued_ms"] <= now < record["expires_ms"]:
            raise OriginError("RENDEZVOUS_EXPIRED")

    @staticmethod
    def _finish(transaction, data, record, phase, code):
        record["phase"] = phase.value
        transaction.write(data)
        return RendezvousReceipt(phase.value, code if code in _RENDEZVOUS_CODES else
                                 "RENDEZVOUS_STORE_UNAVAILABLE")

    def issue(self, *, request_id: str, route_id: str, generation: int,
              attestation: LocalCallerAttestation | None, lease_seconds: int = 60) -> RendezvousReceipt:
        """Precommit the private marker and origin BEFORE invoking the carrier."""
        try:
            return self._issue(request_id, route_id, generation, attestation, lease_seconds)
        except OriginError as exc:
            return RendezvousReceipt("HOLD", str(exc) if str(exc) in _RENDEZVOUS_CODES else
                                     "RENDEZVOUS_STORE_UNAVAILABLE")
        except Exception:
            return RendezvousReceipt("UNKNOWN", "RENDEZVOUS_STORE_UNAVAILABLE")

    def _issue(self, request_id, route_id, generation, attestation, lease_seconds):
        if (not _private_id(request_id) or not _private_id(route_id) or
                type(generation) is not int or generation < 1 or
                type(lease_seconds) is not int or not 1 <= lease_seconds <= 300):
            raise OriginError("RENDEZVOUS_INPUT_INVALID")
        context = {"request_id": request_id, "route_id": route_id,
                   "generation": generation, "lease_seconds": lease_seconds}
        self.registrar._authorize(attestation, "rendezvous_issue", context)
        trust = self._adapters(context)
        # Registry first is the lock order for both operations; its generation
        # stays pinned through the bounded callback, as in D2 delivery.
        with self.registrar._transaction() as rt, self._state.locked() as transaction:
            registry, data = self.registrar._read_registry(rt), self._read(transaction)
            if request_id in data["requests"]:
                raise OriginError("RENDEZVOUS_REPLAY")
            if len(data["requests"]) >= 128:
                raise OriginError("RENDEZVOUS_STORE_FULL")
            route = self._route(registry, route_id, generation)
            context.update(registry=registry, state=copy.deepcopy(data))
            self.registrar._authorize(attestation, "rendezvous_issue", context)
            try:
                origin = self.observer.capture_origin(
                    request_id=request_id, binding=ResolvedBinding(route_id, generation,
                    route["chat_url"], route["cdp_endpoint"]))
            except Exception:
                raise OriginError("RENDEZVOUS_ORIGIN_INVALID") from None
            if not self._matches_route(origin, route):
                raise OriginError("RENDEZVOUS_ORIGIN_INVALID")
            context.update(registry=registry, origin=asdict(origin))
            self.registrar._authorize(attestation, "rendezvous_issue", context)
            if self._adapters(context) is not trust:
                raise OriginError("RENDEZVOUS_TRUST_MISMATCH")
            try:
                entropy = secrets.token_bytes(32) if self._test_entropy is None else self._test_entropy(32)
            except Exception:
                raise OriginError("RENDEZVOUS_ENTROPY_INVALID") from None
            if type(entropy) is not bytes or len(entropy) != 32:
                raise OriginError("RENDEZVOUS_ENTROPY_INVALID")
            marker = "wh03." + entropy.hex()
            digest = hashlib.sha256(marker.encode("ascii")).hexdigest()
            if any(r["digest"] == digest for r in data["requests"].values()):
                raise OriginError("RENDEZVOUS_REPLAY")
            issued_ms = time.time_ns() // 1000000
            record = dict(request_id=request_id, origin=asdict(origin), marker=marker, digest=digest,
                          trust=trust.value, phase=RendezvousPhase.PRECOMMITTED.value,
                          issued_ms=issued_ms, expires_ms=issued_ms + lease_seconds * 1000,
                          message_id=None)
            data["requests"][request_id] = record
            transaction.write(data)  # fsync/replace completes before the callback can see the marker
            emission = RendezvousEmission(request_id, marker, digest, origin)
            try:
                context.update(record=copy.deepcopy(record))
                self.registrar._authorize(attestation, "rendezvous_issue", context)
                self._lease(record)
                ack = self.carrier.emit_marker(emission)
                if (type(ack) is not RendezvousAck or type(ack.request_id) is not str or
                        ack.request_id != request_id or type(ack.marker_digest) is not str or
                        ack.marker_digest != digest or not _valid_origin(ack.origin) or
                        ack.origin != origin or not _private_id(ack.message_id)):
                    raise OriginError("RENDEZVOUS_ACK_UNKNOWN")
                self._lease(record)
                if self.registrar._read_registry(rt) != registry:
                    raise OriginError("RENDEZVOUS_ROUTE_CHANGED")
                self.registrar._authorize(attestation, "rendezvous_issue", context)
                if self._adapters(context) is not trust:
                    raise OriginError("RENDEZVOUS_TRUST_MISMATCH")
            except OriginError as exc:
                return self._finish(transaction, data, record, RendezvousPhase.UNKNOWN, str(exc))
            except Exception:
                return self._finish(transaction, data, record, RendezvousPhase.UNKNOWN,
                                    "RENDEZVOUS_ACK_UNKNOWN")
            record.update(message_id=ack.message_id, phase=RendezvousPhase.EMITTED.value)
            transaction.write(data)
            return RendezvousReceipt("SYNTHETIC_PENDING" if trust is RendezvousTrust.SYNTHETIC else
                                     "PENDING", "SYNTHETIC_EMISSION_ACKNOWLEDGED" if
                                     trust is RendezvousTrust.SYNTHETIC else "EMISSION_ACKNOWLEDGED")

    def verify(self, *, request_id: str,
               attestation: LocalCallerAttestation | None) -> RendezvousReceipt:
        """Consume one exhaustive search + fresh opened-origin proof, or HOLD."""
        try:
            return self._verify(request_id, attestation)
        except OriginError as exc:
            return RendezvousReceipt("HOLD", str(exc) if str(exc) in _RENDEZVOUS_CODES else
                                     "RENDEZVOUS_STORE_UNAVAILABLE")
        except Exception:
            return RendezvousReceipt("UNKNOWN", "RENDEZVOUS_STORE_UNAVAILABLE")

    def _verify(self, request_id, attestation):
        if not _private_id(request_id):
            raise OriginError("RENDEZVOUS_INPUT_INVALID")
        context = {"request_id": request_id}
        self.registrar._authorize(attestation, "rendezvous_verify", context)
        with self.registrar._transaction() as rt, self._state.locked() as transaction:
            registry, data = self.registrar._read_registry(rt), self._read(transaction)
            record = data["requests"].get(request_id)
            if record is None:
                raise OriginError("RENDEZVOUS_NOT_FOUND")
            context.update(registry=registry, record=copy.deepcopy(record))
            self.registrar._authorize(attestation, "rendezvous_verify", context)
            phase = RendezvousPhase(record["phase"])
            if phase in {RendezvousPhase.PRECOMMITTED, RendezvousPhase.VERIFYING}:
                return self._finish(transaction, data, record, RendezvousPhase.UNKNOWN,
                                    "RENDEZVOUS_PRECOMMIT_UNKNOWN" if phase is
                                    RendezvousPhase.PRECOMMITTED else "RENDEZVOUS_OBSERVATION_UNKNOWN")
            if phase is not RendezvousPhase.EMITTED:
                raise OriginError("RENDEZVOUS_REPLAY")
            try:
                trust = self._adapters(context)
                if trust.value != record["trust"]:
                    raise OriginError("RENDEZVOUS_TRUST_MISMATCH")
                self._lease(record)
                origin = PrivateOriginSnapshot(**record["origin"])
                route = self._route(registry, origin.route_id, origin.generation)
                if not self._matches_route(origin, route):
                    raise OriginError("RENDEZVOUS_ROUTE_CHANGED")
            except OriginError as exc:
                return self._finish(transaction, data, record, RendezvousPhase.HOLD, str(exc))
            record["phase"] = RendezvousPhase.VERIFYING.value
            transaction.write(data)  # search itself also has a one-shot crash boundary
            emission = RendezvousEmission(request_id, record["marker"], record["digest"], origin)
            nonce = secrets.token_hex(32)
            try:
                proof = self.observer.search_and_reprove(emission, nonce=nonce)
            except Exception:
                return self._finish(transaction, data, record, RendezvousPhase.UNKNOWN,
                                    "RENDEZVOUS_OBSERVATION_UNKNOWN")
            code = self._check_proof(proof, emission, nonce, record["message_id"])
            try:
                self._lease(record)
                if self.registrar._read_registry(rt) != registry:
                    raise OriginError("RENDEZVOUS_ROUTE_CHANGED")
                self.registrar._authorize(attestation, "rendezvous_verify", context)
                if self._adapters(context) is not trust:
                    raise OriginError("RENDEZVOUS_TRUST_MISMATCH")
            except OriginError as exc:
                code = str(exc)
            if code is not None:
                return self._finish(transaction, data, record, RendezvousPhase.HOLD, code)
            record["phase"] = RendezvousPhase.CONSUMED.value
            transaction.write(data)
            native = trust is RendezvousTrust.NATIVE
            return RendezvousReceipt("ORIGIN_ATTESTED" if native else "SYNTHETIC_MATCH",
                                     "ORIGIN_ATTESTED" if native else "NO_LIVE_AUTHORITY", native)

    @staticmethod
    def _check_proof(proof, emission, nonce, message_id):
        if (type(proof) is not RendezvousObservation or type(proof.request_id) is not str or
                proof.request_id != emission.request_id or type(proof.marker_digest) is not str or
                proof.marker_digest != emission.marker_digest or type(proof.nonce) is not str or
                proof.nonce != nonce or type(proof.hits) is not tuple):
            return "RENDEZVOUS_OBSERVATION_INVALID"
        if len(proof.hits) != 1:
            return "RENDEZVOUS_SEARCH_NOT_UNIQUE"
        hit = proof.hits[0]
        if (type(hit) is not RendezvousHit or type(hit.marker) is not str or
                hit.marker != emission.marker or type(hit.message_id) is not str or
                hit.message_id != message_id or not _valid_origin(hit.origin) or
                not _valid_origin(proof.opened_origin)):
            return "RENDEZVOUS_OBSERVATION_INVALID"
        if hit.origin != emission.origin or proof.opened_origin != emission.origin:
            return "RENDEZVOUS_ORIGIN_MISMATCH"
        return None
