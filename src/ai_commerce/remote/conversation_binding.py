"""EF02-D D1: isolated, read-only local route matching (no browser access)."""
from __future__ import annotations

import ipaddress
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CHAT = re.compile(r"https://chatgpt\.com/(?:c|g/g-[A-Za-z0-9_-]+/c)/(" + _UUID + r")\Z")
_ROUTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class BindingError(ValueError):
    """No exact, consented, locally owned originating conversation binding."""


@dataclass(frozen=True, repr=False)
class ResolvedBinding:
    route_id: str
    generation: int
    chat_url: str  # private: never print or serialize into GitHub artifacts
    cdp_endpoint: str  # private: no GitHub logs or events


def _decode_unique(raw: str) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BindingError("REGISTRY_DUPLICATE_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def _endpoint(endpoint: object) -> bool:
    if not isinstance(endpoint, str):
        return False
    try:
        parts = urlsplit(endpoint)
        if (parts.scheme != "http" or not parts.hostname or not parts.port or
                parts.username or parts.password or parts.path not in ("", "/") or
                parts.query or parts.fragment):
            return False
        return parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname).is_loopback
    except (ValueError, AttributeError):
        return False


def _outside_repo(path: Path, repository_root: Path) -> bool:
    if path.is_symlink():
        return False
    path = path.resolve()  # also reject symlinked parent directories into the repo
    root = repository_root.resolve()
    return path != root and root not in path.parents


def resolve_binding(event: dict, *, route_id: str, registry_path: Path,
                    repository_root: Path) -> ResolvedBinding:
    """Only an independently trusted origin issuer may supply ``route_id``.

    Never infer a route from GitHub request bodies, newest/active browser tabs,
    or model memory. This D1 matcher cannot issue a route or prove live origin.
    """
    if not isinstance(route_id, str) or not _ROUTE.fullmatch(route_id):
        raise BindingError("ROUTE_INVALID")
    if (event.get("kind") != "AI_COMMERCE_EF02_COMPLETION_EVENT" or event.get("version") != 1 or
            event.get("origin_binding_status") != "UNBOUND" or event.get("delivery_status") != "NOT_ATTEMPTED"):
        raise BindingError("EVENT_NOT_ELIGIBLE")
    if (not _REPOSITORY.fullmatch(str(event.get("repository", ""))) or
            not _SHA.fullmatch(str(event.get("source_sha", "")))):
        raise BindingError("EVENT_IDENTITY_INVALID")
    immutable_keys = ("repository", "run_id", "run_attempt", "issue_number",
                      "batch_id", "source_sha", "batch_status", "actions")
    try:
        immutable = {key: event[key] for key in immutable_keys}
        expected = "ef02-completion-v1:" + hashlib.sha256(json.dumps(
            immutable, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False).encode("utf-8")).hexdigest()
        if event.get("event_id") != expected or event.get("chat_delivery_proven") is not False:
            raise BindingError("EVENT_IDENTITY_INVALID")
    except (KeyError, TypeError, ValueError) as exc:
        raise BindingError("EVENT_IDENTITY_INVALID") from exc
    path = Path(registry_path)
    if not path.is_absolute() or not _outside_repo(path, Path(repository_root)):
        raise BindingError("REGISTRY_LOCATION_INVALID")
    try:
        if path.stat().st_size > 131072:
            raise BindingError("REGISTRY_TOO_LARGE")
        registry = _decode_unique(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BindingError("REGISTRY_UNAVAILABLE") from exc
    if (not isinstance(registry, dict) or set(registry) != {"version", "routes", "claims"}
            or type(registry["version"]) is not int or registry["version"] != 1):
        raise BindingError("REGISTRY_INVALID")
    routes, claims = registry["routes"], registry["claims"]
    if (not isinstance(routes, list) or len(routes) > 128 or
            not isinstance(claims, list) or len(claims) > 256):
        raise BindingError("REGISTRY_INVALID")
    seen_routes, seen_chats, route_lookup = set(), set(), {}
    route_keys = {"route_id", "generation", "chat_url", "cdp_endpoint", "consent"}
    for route in routes:
        if (not isinstance(route, dict) or set(route) != route_keys or
                not isinstance(route["route_id"], str) or not _ROUTE.fullmatch(route["route_id"]) or
                type(route["generation"]) is not int or route["generation"] < 1 or
                route["consent"] is not True or not _endpoint(route["cdp_endpoint"])):
            raise BindingError("REGISTRY_INVALID")
        chat = route["chat_url"]
        matched = _CHAT.fullmatch(chat) if isinstance(chat, str) else None
        if not matched:
            raise BindingError("CHAT_URL_INVALID")
        chat_identity = matched.group(1)
        if route["route_id"] in seen_routes or chat_identity in seen_chats:
            raise BindingError("ROUTE_CONFLICT")
        seen_routes.add(route["route_id"])
        seen_chats.add(chat_identity)
        route_lookup[route["route_id"]] = route
    claim_keys = {"route_id", "generation", "repository", "batch_id", "source_sha", "issue_number"}
    seen_batches, claim_matches = set(), []
    for claim in claims:
        if (not isinstance(claim, dict) or set(claim) != claim_keys or
                not isinstance(claim["route_id"], str) or claim["route_id"] not in route_lookup or
                type(claim["generation"]) is not int or
                claim["generation"] != route_lookup[claim["route_id"]]["generation"] or
                not isinstance(claim["repository"], str) or not _REPOSITORY.fullmatch(claim["repository"]) or
                not isinstance(claim["batch_id"], str) or not _ROUTE.fullmatch(claim["batch_id"]) or
                not isinstance(claim["source_sha"], str) or not _SHA.fullmatch(claim["source_sha"]) or
                type(claim["issue_number"]) is not int or claim["issue_number"] <= 0):
            raise BindingError("REGISTRY_INVALID")
        batch_key = (claim["repository"], claim["batch_id"], claim["issue_number"])
        if batch_key in seen_batches:
            raise BindingError("CLAIM_CONFLICT")
        seen_batches.add(batch_key)
        if all(event.get(k) == claim[k] for k in ("repository", "batch_id", "source_sha", "issue_number")):
            claim_matches.append(claim)
    if len(claim_matches) != 1 or claim_matches[0]["route_id"] != route_id:
        raise BindingError("ROUTE_EVENT_MISMATCH")
    route = route_lookup[route_id]
    return ResolvedBinding(route_id=route_id, generation=route["generation"],
                           chat_url=route["chat_url"], cdp_endpoint=route["cdp_endpoint"])
