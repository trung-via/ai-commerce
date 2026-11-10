"""B4.1a: offline A12-native product-feed review, not a marketplace adapter.

The immutable packet is a consumer of independently pinned A12 local facts.
No feed auth, provider connection, rights grant, asset change, or export occurs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
import re

from ai_commerce.product import (
    ProductEvidenceRegistry, export_pack_from_registry, parse_timestamp,
)
from ai_commerce.product.core import canonical_bytes
from ai_commerce.product.evidence_schemas import MAX_FRESHNESS_SECONDS

SCHEMA = "B4FeedReadiness.v1"
MAX_PACKET_BYTES = 65536
_SHA = re.compile(r"[a-f0-9]{64}\Z")

LIMITS = {
    "source_of_truth": "A12_LOCAL_EVIDENCE_REGISTRY",
    "provider_connection": "NOT_ATTEMPTED",
    "provider_entitlement": "UNVERIFIED",
    "affiliate_eligibility": "UNKNOWN",
    "marketplace_current_price": "NOT_LIVE_VERIFIED",
    "external_revocation": "NOT_VERIFIED",
    "license_authentication": "NOT_ESTABLISHED",
    "cross_owner_asset_binding": "UNVERIFIED",
    "rights_to_execute": "NONE",
    "budget_or_publish_permission": "NONE",
    "local_hash_is_provider_attestation": False,
    "writes_performed": False,
}
REASONS = frozenset({
    "SOURCE_STALE", "SOURCE_CAPTURE_AFTER_AS_OF", "SYNTHETIC_SOURCE",
    "OFFER_STALE", "OFFER_CONFLICT", "OFFER_NO_DATA", "OFFER_NOT_YET_OBSERVED",
    "RIGHTS_UNKNOWN", "RIGHTS_RESTRICTED", "RIGHTS_MIXED",
    "RIGHTS_SCOPE_EXPIRED_OR_FUTURE", "COUNTEREVIDENCE_REVIEW_REQUIRED",
})


class B4FeedHold(ValueError):
    """Safe local evidence errors without leaking raw product data or paths."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise B4FeedHold(code)


@dataclass(frozen=True, slots=True)
class B4FeedReadiness:
    raw: bytes

    def __post_init__(self):
        _require(type(self.raw) is bytes and 0 < len(self.raw) <= MAX_PACKET_BYTES,
                 "B41_PACKET_SIZE_INVALID")
        try:
            record = json.loads(self.raw)
            _require(type(record) is dict and canonical_bytes(record) == self.raw,
                     "B41_PACKET_NOT_CANONICAL")
            _require(set(record) == {
                "schema_version", "status", "registry_head_sha256", "registry_event_count",
                "pack_id", "as_of", "subject", "source", "offer_assessment",
                "rights_status", "related_counterevidence_event_ids",
                "affected_rights_ids", "reason_codes", "limits", "record_sha256",
            }, "B41_PACKET_FIELDS_INVALID")
            _require(record["schema_version"] == SCHEMA, "B41_PACKET_SCHEMA_INVALID")
            body = {key: val for key, val in record.items() if key != "record_sha256"}
            _require(record["record_sha256"] == sha256(canonical_bytes(body)).hexdigest(),
                     "B41_PACKET_DIGEST_INVALID")
            _require(record["limits"] == LIMITS, "B41_PACKET_AUTHORITY_INVALID")
            reasons = record["reason_codes"]
            _require(type(reasons) is list and reasons == sorted(set(reasons))
                     and set(reasons).issubset(REASONS), "B41_PACKET_REASONS_INVALID")
            expected_status = "LOCAL_EVIDENCE_HOLD" if reasons else "OFFLINE_ADVISORY_ONLY"
            _require(record["status"] == expected_status, "B41_PACKET_STATUS_INVALID")
            _require(record["related_counterevidence_event_ids"] ==
                     sorted(set(record["related_counterevidence_event_ids"])),
                     "B41_PACKET_COUNTEREVIDENCE_INVALID")
        except B4FeedHold:
            raise
        except (TypeError, KeyError, ValueError, OverflowError, AttributeError):
            raise B4FeedHold("B41_PACKET_INVALID") from None

    def to_dict(self) -> dict:
        return json.loads(self.raw)


def review_local_product_feed(
    registry: ProductEvidenceRegistry, *, listing_id: str, variant_id: str,
    as_of: str, expected_head_sha256: str,
    max_age_seconds: int = 86400,
) -> B4FeedReadiness:
    """Read ONLY native A12. A pinned hash is local integrity, not external trust.

    A12 pack is regenerated at the explicit review instant; a retained older
    pack is never assumed current. Counterevidence is an impact REVIEW trigger,
    not a proven external revocation or a right to modify media.
    """
    _require(type(registry) is ProductEvidenceRegistry, "B41_REGISTRY_REQUIRED")
    _require(type(expected_head_sha256) is str
             and bool(_SHA.fullmatch(expected_head_sha256)),
             "B41_HEAD_PIN_INVALID")
    _require(type(listing_id) is str and listing_id.startswith("a12-listing-")
             and type(variant_id) is str and variant_id.startswith("a12-variant-"),
             "B41_EXACT_VARIANT_REQUIRED")
    _require(type(max_age_seconds) is int
             and 0 <= max_age_seconds <= MAX_FRESHNESS_SECONDS,
             "B41_FRESHNESS_LIMIT_INVALID")
    try:
        instant = parse_timestamp(as_of)
    except Exception:
        raise B4FeedHold("B41_REVIEW_TIME_INVALID") from None
    try:
        first = registry.integrity_check()
        _require(first["head_hash"] == expected_head_sha256,
                 "B41_REGISTRY_HEAD_CHANGED")
        pack = export_pack_from_registry(
            registry, listing_id=listing_id, variant_id=variant_id,
            as_of=as_of, max_age_seconds=max_age_seconds, generated_at=as_of,
        ).to_dict()
        events = registry.events()
        last = registry.integrity_check()
        _require(first == last and len(events) == last["event_count"],
                 "B41_REGISTRY_CHANGED_DURING_READ")
        refs = set(pack["registry_event_refs"])
        ids = {event.event_id for event in events}
        _require(refs.issubset(ids), "B41_REGISTRY_REFERENCES_MISSING")
        impacted = sorted(event.event_id for event in events
                          if event.event_id in refs
                          and event.to_dict()["operation"] == "COUNTEREVIDENCE")
    except B4FeedHold:
        raise
    except Exception:
        raise B4FeedHold("B41_A12_NATIVE_READ_HOLD") from None

    _require(pack["subject"]["listing_id"] == listing_id
             and pack["subject"]["variant_id"] == variant_id,
             "B41_VARIANT_MISMATCH")
    reasons = set()
    capture = parse_timestamp(pack["source"]["captured_at"])
    if instant < capture:
        reasons.add("SOURCE_CAPTURE_AFTER_AS_OF")
    elif instant - capture > timedelta(seconds=max_age_seconds):
        reasons.add("SOURCE_STALE")
    if pack["subject"]["synthetic"]:
        reasons.add("SYNTHETIC_SOURCE")

    offer_status = pack["offers"]["assessment_status"]
    offer_reason = {
        "STALE": "OFFER_STALE",
        "CONFLICT": "OFFER_CONFLICT",
        "NO_OFFERS": "OFFER_NO_DATA",
        "NOT_YET_OBSERVED": "OFFER_NOT_YET_OBSERVED",
    }.get(offer_status)
    if offer_reason:
        reasons.add(offer_reason)

    rights_status = pack["rights_status"]
    if rights_status != "GRANTED":
        reasons.add({
            "UNKNOWN": "RIGHTS_UNKNOWN", "RESTRICTED": "RIGHTS_RESTRICTED",
            "MIXED": "RIGHTS_MIXED",
        }[rights_status])
    for item in pack["rights"]:
        for permission in item["permissions"]:
            if permission["status"] == "GRANTED":
                scope = permission["scope"]
                if scope is None or not (
                    parse_timestamp(scope["valid_from"]) <= instant <
                    parse_timestamp(scope["valid_until"])
                ):
                    reasons.add("RIGHTS_SCOPE_EXPIRED_OR_FUTURE")
    if impacted:
        reasons.add("COUNTEREVIDENCE_REVIEW_REQUIRED")

    body = {
        "schema_version": SCHEMA,
        "status": "LOCAL_EVIDENCE_HOLD" if reasons else "OFFLINE_ADVISORY_ONLY",
        "registry_head_sha256": first["head_hash"],
        "registry_event_count": first["event_count"],
        "pack_id": pack["pack_id"],
        "as_of": as_of,
        "subject": {"listing_id": listing_id, "variant_id": variant_id},
        "source": {
            "kind": pack["source"]["source_kind"],
            "captured_at": pack["source"]["captured_at"],
            "raw_sha256": pack["source"]["raw_sha256"],
        },
        "offer_assessment": {
            "status": offer_status,
            "active_offer_id": None if pack["offers"]["active_offer"] is None
                               else pack["offers"]["active_offer"]["offer_id"],
            "live_price_status": "NOT_LIVE_VERIFIED",
        },
        "rights_status": rights_status,
        "related_counterevidence_event_ids": impacted,
        "affected_rights_ids": sorted(item["rights_id"] for item in pack["rights"]),
        "reason_codes": sorted(reasons),
        "limits": dict(LIMITS),
    }
    return B4FeedReadiness(canonical_bytes({
        **body, "record_sha256": sha256(canonical_bytes(body)).hexdigest(),
    }))
