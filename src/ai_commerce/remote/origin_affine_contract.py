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
