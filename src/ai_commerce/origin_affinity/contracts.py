"""Pure D5 origin-affine return *draft* contract (no authority or effects).

A ticket is a content-free correlation selector, NOT proof of a browser origin,
completion provenance, private D2 registry, D3 grant, or Send permission.

The real origin issuer / D2 registrar / completion verifier / grant authority
MUST authenticate inputs independently before any external Wake operation.
This module intentionally performs no storage, network, OS or browser calls.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Mapping


class OriginTicketError(ValueError):
    """Bounded structural failure; never formats private caller data."""


VERSION = "OriginReturnTicket.v1"
METHODS = frozenset({"OPAQUE_RENDEZVOUS_V1", "PAGE_SCOPED_GESTURE_V1"})
_FIELDS = frozenset({
    "schema_version", "route_id", "generation", "origin_method",
    "origin_proof_sha256", "work_namespace", "work_id", "event_ref",
    "completion_evidence_sha256", "source_sha", "delivery_key",
    "delivery_mode", "authorization", "effect",
})
_BASE_FIELDS = _FIELDS - {"delivery_key"}
_ROUTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_NAMESPACE = re.compile(r"[A-Z][A-Z0-9_]{1,31}\Z")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_EVENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DOMAIN = b"AI_COMMERCE_ORIGIN_RETURN_TICKET_V1\x00"


def _match(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _base(value: Mapping[str, object]) -> dict:
    """Validate only a data-only, non-authorizing content-free record."""
    if type(value) is not dict or set(value) != _BASE_FIELDS:
        raise OriginTicketError("TICKET_FIELDS_INVALID")
    if (value["schema_version"] != VERSION or
            not _match(value["route_id"], _ROUTE) or
            type(value["generation"]) is not int or
            not 1 <= value["generation"] <= 2147483647 or
            type(value["origin_method"]) is not str or
            value["origin_method"] not in METHODS or
            not _match(value["origin_proof_sha256"], _DIGEST) or
            not _match(value["work_namespace"], _NAMESPACE) or
            not _match(value["work_id"], _REF) or
            not _match(value["event_ref"], _EVENT) or
            not _match(value["completion_evidence_sha256"], _DIGEST) or
            not _match(value["source_sha"], _SHA) or
            value["delivery_mode"] != "SEND_ONLY" or
            value["authorization"] != "NOT_VERIFIED" or
            value["effect"] != "NOT_ATTEMPTED"):
        raise OriginTicketError("TICKET_VALUE_INVALID")
    return dict(value)


def _delivery_key(base: dict) -> str:
    """Deterministic correlation only; hash is NOT a signature or consent."""
    raw = json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(_DOMAIN + raw.encode("ascii")).hexdigest()


def draft_return_ticket(
    *,
    route_id: str,
    generation: int,
    origin_method: str,
    origin_proof_sha256: str,
    work_namespace: str,
    work_id: str,
    event_ref: str,
    completion_evidence_sha256: str,
    source_sha: str,
) -> dict:
    """Construct a closed, inert ticket for future independently verified work.

    This does not confirm that origin evidence or completion actually exists.
    It never generates an origin marker or invokes EF02, PULSE or CDP.
    """
    value = _base({
        "schema_version": VERSION,
        "route_id": route_id,
        "generation": generation,
        "origin_method": origin_method,
        "origin_proof_sha256": origin_proof_sha256,
        "work_namespace": work_namespace,
        "work_id": work_id,
        "event_ref": event_ref,
        "completion_evidence_sha256": completion_evidence_sha256,
        "source_sha": source_sha,
        "delivery_mode": "SEND_ONLY",
        "authorization": "NOT_VERIFIED",
        "effect": "NOT_ATTEMPTED",
    })
    value["delivery_key"] = _delivery_key(value)
    return value


def validate_draft_return_ticket(value: Mapping[str, object]) -> dict:
    """Validate syntax + deterministic binding; authenticate nothing."""
    if type(value) is not dict or set(value) != _FIELDS:
        raise OriginTicketError("TICKET_FIELDS_INVALID")
    base = {key: item for key, item in value.items() if key != "delivery_key"}
    _base(base)
    if (not _match(value["delivery_key"], _DIGEST) or
            value["delivery_key"] != _delivery_key(base)):
        raise OriginTicketError("TICKET_IDENTITY_MISMATCH")
    return dict(value)


# WH-03C: structural correlation diagnostics only. The current ChatGPT host
# does not expose an authenticated invoking-chat carrier to this plugin.
# A Python object, Issue body, URL, marker or matching digest cannot create
# private host authority or enroll an interactive Windows signer.
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class HostOriginHints:
    """UNTRUSTED synthetic hints, not authenticated plugin-host metadata."""
    route_id: str
    generation: int
    account_sha256: str
    session_sha256: str
    conversation_sha256: str
    work_namespace: str
    work_id: str
    event_ref: str
    source_sha: str
    nonce_state: str = "FRESH"


@dataclass(frozen=True, repr=False)
class NativeChatHints:
    """UNTRUSTED native-observation hints, not an enrolled signer receipt."""
    route_id: str
    generation: int
    account_sha256: str
    session_sha256: str
    conversation_sha256: str
    marker_state: str = "CURRENT_ONLY"


@dataclass(frozen=True, repr=False)
class HostOriginAssessment:
    """Never authorizes real Wake; all outputs remain source-only HOLD."""
    reason: str
    structural_match: bool = False
    @property
    def authorization(self) -> str:
        return "NOT_VERIFIED"

    @property
    def effect(self) -> str:
        return "NOT_ATTEMPTED"


def _host_hints_valid(value: object) -> bool:
    return (
        type(value) is HostOriginHints
        and _match(value.route_id, _ROUTE)
        and type(value.generation) is int and 1 <= value.generation <= 2147483647
        and all(_match(digest, _DIGEST) for digest in (
            value.account_sha256, value.session_sha256, value.conversation_sha256))
        and _match(value.work_namespace, _NAMESPACE)
        and _match(value.work_id, _REF)
        and _match(value.event_ref, _EVENT)
        and _match(value.source_sha, _SHA)
        and type(value.nonce_state) is str
        and value.nonce_state in {"FRESH", "EXPIRED", "REVOKED", "REUSED"}
    )


def _native_hints_valid(value: object) -> bool:
    return (
        type(value) is NativeChatHints
        and _match(value.route_id, _ROUTE)
        and type(value.generation) is int and 1 <= value.generation <= 2147483647
        and all(_match(digest, _DIGEST) for digest in (
            value.account_sha256, value.session_sha256, value.conversation_sha256))
        and type(value.marker_state) is str
        and value.marker_state in {
            "CURRENT_ONLY", "CURRENT_WITH_DISTINCT_OLD",
            "COPIED_TO_CHAT_B", "DUPLICATE_CURRENT", "CURRENT_MISSING"}
    )


def inspect_host_origin_carrier(
    ticket: Mapping[str, object],
    *,
    host: HostOriginHints | None = None,
    native: NativeChatHints | None = None,
) -> HostOriginAssessment:
    """Diagnose ONLY structural correlation; never a live Chat A admission.

    Arguments are public/fixture-constructible. Even completely matching hints
    are not authenticated: a real ChatGPT-host origin carrier and independently
    enrolled local Windows signer must be added by a *separate trusted owner*.
    Do not use this result as a Send grant or TEST_REGISTERED witness.
    """
    try:
        ticket = validate_draft_return_ticket(ticket)
    except (OriginTicketError, TypeError, ValueError):
        return HostOriginAssessment("TICKET_NOT_VERIFIED")
    if host is None:
        return HostOriginAssessment("HOST_ORIGIN_CARRIER_UNAVAILABLE")
    if type(host) is not HostOriginHints:
        return HostOriginAssessment("UNTRUSTED_HOST_INPUT")
    if not _host_hints_valid(host):
        return HostOriginAssessment("HOST_HINT_INVALID")
    if (host.route_id != ticket["route_id"]
            or host.generation != ticket["generation"]):
        return HostOriginAssessment("ROUTE_GENERATION_MISMATCH")
    if (host.work_namespace != ticket["work_namespace"]
            or host.work_id != ticket["work_id"]):
        return HostOriginAssessment("WORK_BINDING_MISMATCH")
    if (host.event_ref != ticket["event_ref"]
            or host.source_sha != ticket["source_sha"]):
        return HostOriginAssessment("EVENT_PROVENANCE_MISMATCH")
    if host.nonce_state != "FRESH":
        return HostOriginAssessment({
            "EXPIRED": "NONCE_EXPIRED",
            "REVOKED": "NONCE_REVOKED",
            "REUSED": "NONCE_REPLAYED",
        }[host.nonce_state])
    if native is None:
        return HostOriginAssessment("NATIVE_OWNER_NOT_ENROLLED")
    if not _native_hints_valid(native):
        return HostOriginAssessment("NATIVE_HINT_INVALID")
    if (native.route_id != host.route_id
            or native.generation != host.generation):
        return HostOriginAssessment("NATIVE_ROUTE_MISMATCH")
    if native.account_sha256 != host.account_sha256:
        return HostOriginAssessment("ACCOUNT_SCOPE_MISMATCH")
    if native.session_sha256 != host.session_sha256:
        return HostOriginAssessment("SESSION_SCOPE_MISMATCH")
    if native.conversation_sha256 != host.conversation_sha256:
        return HostOriginAssessment("CONVERSATION_SCOPE_MISMATCH")
    marker_holds = {
        "COPIED_TO_CHAT_B": "MARKER_COPIED_TO_CHAT_B",
        "DUPLICATE_CURRENT": "MARKER_CURRENT_DUPLICATE",
        "CURRENT_MISSING": "MARKER_CURRENT_MISSING",
    }
    if native.marker_state in marker_holds:
        return HostOriginAssessment(marker_holds[native.marker_state])
    # Distinct historical markers in the exact chat are legitimate. A valid
    # structural match still cannot authenticate the host or local signer.
    return HostOriginAssessment("HOST_AUTHORITY_AND_NATIVE_SIGNER_UNAVAILABLE",
                                structural_match=True)
