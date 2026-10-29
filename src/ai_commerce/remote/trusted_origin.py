"""EF02-D D2: trusted local origin registration, attestation and claim binding."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .completion_event import _ID, _REPO, _SHA
from .conversation_binding import (
    _CHAT, _REPOSITORY, _ROUTE, BindingError,
    _decode_unique, _endpoint, _outside_repo,
)

_MAX_REGISTRY_BYTES = 131072


class OriginError(ValueError):
    """Local origin registration, attestation, or claim conflict."""


@dataclass(frozen=True, repr=False)
class LocalCallerAttestation:
    """Attestation proving the caller is local and authorized to bind/wake routes.

    repr=False prevents leaking credentials or origin tokens into logs/reprs.
    """
    caller_id: str
    is_local: bool
    auth_token: str | None = None
    attestation_verified: bool = False

    def is_trusted(self, expected_token: str | None = None) -> bool:
        if not self.is_local or not self.attestation_verified:
            return False
        if expected_token is not None:
            return self.auth_token == expected_token
        return True

    def __repr__(self) -> str:
        return (
            f"LocalCallerAttestation(caller_id={self.caller_id!r}, "
            f"is_local={self.is_local!r}, verified={self.attestation_verified!r})"
        )


class LocalOriginRegistrar:
    """Private, local-only origin registrar managing chat routes and batch claims."""

    def __init__(self, registry_path: Path, repository_root: Path):
        self.registry_path = Path(registry_path)
        self.repository_root = Path(repository_root)
        if not self.registry_path.is_absolute() or not _outside_repo(self.registry_path, self.repository_root):
            raise OriginError("REGISTRY_LOCATION_INVALID")

    def read_registry(self) -> dict:
        """Safely decode and validate the private local registry file."""
        if not self.registry_path.exists():
            return {"version": 1, "routes": [], "claims": []}
        try:
            if self.registry_path.stat().st_size > _MAX_REGISTRY_BYTES:
                raise OriginError("REGISTRY_TOO_LARGE")
            raw = self.registry_path.read_text(encoding="utf-8")
            data = _decode_unique(raw)
        except BindingError as exc:
            if str(exc) == "REGISTRY_DUPLICATE_KEY":
                raise OriginError("REGISTRY_DUPLICATE_KEY") from exc
            raise OriginError("REGISTRY_UNAVAILABLE") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OriginError("REGISTRY_UNAVAILABLE") from exc

        if (not isinstance(data, dict) or set(data) != {"version", "routes", "claims"}
                or data.get("version") != 1):
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
        claim_keys = {"route_id", "generation", "repository", "batch_id", "source_sha", "issue_number"}
        for c in claims:
            if not isinstance(c, dict) or set(c) != claim_keys:
                raise OriginError("REGISTRY_INVALID")
            if not isinstance(c["route_id"], str) or c["route_id"] not in seen_routes:
                raise OriginError("ROUTE_NOT_FOUND")
            if type(c["generation"]) is not int or c["generation"] < 1:
                raise OriginError("GENERATION_INVALID")
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

    def write_registry(self, data: dict) -> None:
        """Atomically persist registry data outside repo within bounded size."""
        if not _outside_repo(self.registry_path, self.repository_root):
            raise OriginError("REGISTRY_LOCATION_INVALID")
        raw = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
        encoded = raw.encode("utf-8")
        if len(encoded) > _MAX_REGISTRY_BYTES:
            raise OriginError("REGISTRY_TOO_LARGE")
        temporary = self.registry_path.with_suffix(self.registry_path.suffix + ".part")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(self.registry_path)

    def register_route(
        self,
        *,
        route_id: str,
        chat_url: str,
        cdp_endpoint: str,
        consent: bool = True,
        generation: int = 1,
        attestation: LocalCallerAttestation | None = None,
    ) -> dict:
        """Register or advance an exact chat route with explicit human consent."""
        if attestation is None or not attestation.is_trusted():
            raise OriginError("UNVERIFIED_ORIGIN")
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

        registry = self.read_registry()
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

        self.write_registry(registry)
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
        if attestation is None or not attestation.is_trusted():
            raise OriginError("UNVERIFIED_ORIGIN")
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

        registry = self.read_registry()
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
        self.write_registry(registry)
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
        if attestation is None or not attestation.is_trusted():
            raise OriginError("UNVERIFIED_ORIGIN")

        registry = self.read_registry()
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
