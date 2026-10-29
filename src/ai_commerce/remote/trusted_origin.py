"""EF02-D D2: trusted local origin registration, attestation and claim binding."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

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
