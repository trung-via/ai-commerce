"""D3.2: operator-signed local origin grants, bound to exact operation and registry.

This is a fail-closed OriginAuthority implementation. The trusted application
supplies immutable signed grants, an HMAC key provider and an independently
observed OS principal. Neither origin flags nor a token supplied with an event
can issue a grant. This module deliberately does not sign grants or send Wake.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping

from .trusted_origin import LocalCallerAttestation

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED = frozenset({
    "register_route", "bind_claim", "resolve_claim_route", "deliver_wake",
})
_GRANT_FIELDS = frozenset({
    "kind", "version", "caller_id", "principal", "operation", "scope",
    "registry_sha256", "valid_until", "human_consent", "signature",
})
_SCOPE_FIELDS = {
    "register_route": frozenset({
        "route_id", "chat_url", "cdp_endpoint", "consent", "generation",
    }),
    "bind_claim": frozenset({
        "route_id", "repository", "batch_id", "source_sha", "issue_number",
        "generation",
    }),
    "resolve_claim_route": frozenset({
        "repository", "batch_id", "source_sha", "issue_number",
    }),
    "deliver_wake": frozenset({"event"}),
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def registry_sha256(registry: Mapping) -> str:
    """Digest the exact validated registry snapshot, not caller-submitted flags."""
    if not isinstance(registry, Mapping):
        raise ValueError("REGISTRY_INVALID")
    return hashlib.sha256(_canonical(registry)).hexdigest()


class SignedOriginConsentAuthority:
    """Validate operator-enrolled, cryptographically scoped consent.

    Required trusted composition-root dependencies:
    - grants: operator-issued signed data NOT sourced from an Issue or event;
    - secret_provider: operator-owned HMAC key of at least 32 bytes;
    - principal_provider: independently authenticated local OS/process identity;
    - clock: trusted time source (defaults to system time).

    Grant signing and Human approval are **outside** this runtime component.
    To authorize an operation that reads/modifies the registry, the caller must
    hold a grant pinned to the *current* registry SHA. A registry change makes
    the old grant unusable for later operations. The initial preflight has no
    registry snapshot; the final D2 check under its state lock has one.
    """

    def __init__(
        self, *,
        grants: Iterable[Mapping],
        secret_provider: Callable[[], bytes],
        principal_provider: Callable[[], str],
        clock: Callable[[], float] = time.time,
    ):
        if not callable(secret_provider) or not callable(principal_provider) or not callable(clock):
            raise ValueError("ORIGIN_AUTHORITY_CONFIG_INVALID")
        self._grants = tuple(copy.deepcopy(dict(grant)) for grant in grants)
        if len(self._grants) > 128:
            raise ValueError("ORIGIN_AUTHORITY_CONFIG_INVALID")
        self._secret_provider = secret_provider
        self._principal_provider = principal_provider
        self._clock = clock

    def authorize_origin(
        self, attestation: LocalCallerAttestation, *,
        operation: str, context: Mapping,
    ) -> bool:
        try:
            if (type(attestation) is not LocalCallerAttestation or
                    attestation.is_local is not True or
                    attestation.attestation_verified is not True or
                    operation not in _ALLOWED or not isinstance(context, Mapping)):
                return False
            request = dict(context)
            registry = request.pop("registry", None)
            if set(request) != _SCOPE_FIELDS[operation]:
                return False
            if operation == "register_route" and request["consent"] is not True:
                return False
            if operation == "bind_claim" and (
                    type(request["generation"]) is not int or request["generation"] < 1):
                # The operator must pin the generation, never silently choose
                # the latest route generation for a signed claim.
                return False
            if registry is not None:
                current_sha = registry_sha256(registry)
            else:
                current_sha = None
            principal = self._principal_provider()
            if not isinstance(principal, str) or not principal or len(principal) > 256:
                return False
            key = self._secret_provider()
            if type(key) is not bytes or len(key) < 32 or len(key) > 4096:
                return False
            now = self._clock()
            if isinstance(now, bool) or not isinstance(now, (int, float)):
                return False

            for grant in self._grants:
                if not isinstance(grant, dict) or set(grant) != _GRANT_FIELDS:
                    continue
                sig = grant["signature"]
                if not isinstance(sig, str) or not _HASH.fullmatch(sig):
                    continue
                body = {name: value for name, value in grant.items() if name != "signature"}
                if (body.get("kind") != "AI_COMMERCE_SIGNED_ORIGIN_CONSENT" or
                        type(body.get("version")) is not int or body["version"] != 1 or
                        body.get("human_consent") is not True or
                        type(body.get("valid_until")) is not int or body["valid_until"] <= now or
                        body.get("caller_id") != attestation.caller_id or
                        body.get("principal") != principal or
                        body.get("operation") != operation or
                        body.get("scope") != request or
                        not isinstance(body.get("registry_sha256"), str) or
                        not _HASH.fullmatch(body["registry_sha256"])):
                    continue
                if current_sha is not None and not hmac.compare_digest(
                        body["registry_sha256"], current_sha):
                    continue
                signed = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
                if hmac.compare_digest(sig, signed):
                    return True
        except (TypeError, KeyError, ValueError, OverflowError, RecursionError):
            return False
        except Exception:
            # Fail closed; external providers may raise secret-bearing errors.
            return False
        return False
