"""Self-contained Draft 2020-12 contract for AffiliateReleaseEvidencePack.v1 (A13-E03).

All references are local; no external $ref, policy, or network lookup.
Outputs strictly READY_FOR_MANUAL_REVIEW, HOLD, or UNKNOWN.
Never claims live provider approval, active affiliate link, POSTED/PUBLIC,
sales, OAuth, network/API calls, revenue, or auto-publishing.
"""
from __future__ import annotations

from copy import deepcopy

from ai_commerce.product.schemas import DATE, SHA

from .placement_schemas import ACCOUNT_REF, APP_REF, CODES, DISCLOSURE, EVIDENCE_REF, SURFACES, URL_EVALUATION


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def text(limit: int = 512) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": limit}


def nullable(value: dict) -> dict:
    return {"anyOf": [value, {"type": "null"}]}


def array(value: dict, maximum: int = 64, minimum: int = 0) -> dict:
    return {
        "type": "array",
        "items": value,
        "minItems": minimum,
        "maxItems": maximum,
        "uniqueItems": True,
    }


PACK_ID = {
    "type": "string",
    "pattern": "^a13-pack-[0-9a-f]{64}$",
    "minLength": 73,
    "maxLength": 73,
}

PLATFORMS = ["TIKTOK", "FACEBOOK", "INSTAGRAM", "YOUTUBE"]

STATE = {"enum": ["READY_FOR_MANUAL_REVIEW", "HOLD", "UNKNOWN"]}

RELEASE_SCOPE = obj({
    "platform": {"enum": PLATFORMS},
    "account_ref": ACCOUNT_REF,
    "app_ref": APP_REF,
    "market": {"type": "string", "pattern": "^[A-Z]{2}$", "minLength": 2, "maxLength": 2},
    "territory": {"type": "string", "pattern": "^[A-Z]{2}$", "minLength": 2, "maxLength": 2},
    "surface": {"enum": SURFACES},
    "channel": text(128),
    "product_id": text(128),
    "listing_id": nullable(text(128)),
    "variant_id": text(128),
    "release_sha256": SHA,
    "link_url": text(4096),
})

RELEASE_TEMPORAL = obj({
    "as_of": DATE,
    "valid_from": DATE,
    "use_from": DATE,
    "use_until": DATE,
    "valid_until": DATE,
    "revoked_at": nullable(DATE),
    "status": {"enum": ["ACTIVE", "EXPIRED", "REVOKED", "NOT_YET_EFFECTIVE", "INVALID_WINDOW"]},
})

MEDIA_COMPONENT = obj({
    "present": {"type": "boolean"},
    "record_sha256": nullable(SHA),
    "status": {"enum": ["PASS", "FAIL", "UNKNOWN", "MISSING", "TAMPERED"]},
    "release_asset_matched": {"type": "boolean"},
    "full_decode": {"type": "boolean"},
    "tamper_checked": {"type": "boolean"},
    "reason_codes": CODES,
})

CLAIM_RIGHTS_COMPONENT = obj({
    "present": {"type": "boolean"},
    "record_sha256": nullable(SHA),
    "pack_id": nullable(text(128)),
    "pack_sha256": nullable(SHA),
    "snapshot_sha256": nullable(SHA),
    "claim_status": {
        "enum": ["VERIFIED_EVIDENCE", "UNKNOWN", "NEEDS_REVIEW", "RESTRICTED", "MISSING", "TAMPERED"]
    },
    "rights_status": {
        "enum": ["VERIFIED_EVIDENCE", "UNKNOWN", "NEEDS_REVIEW", "RESTRICTED", "MISSING", "TAMPERED"]
    },
    "status": {
        "enum": ["VERIFIED_EVIDENCE", "UNKNOWN", "NEEDS_REVIEW", "RESTRICTED", "MISSING", "TAMPERED"]
    },
    "scope_matched": {"type": "boolean"},
    "tamper_checked": {"type": "boolean"},
    "reason_codes": CODES,
})

PLACEMENT_COMPONENT = obj({
    "present": {"type": "boolean"},
    "record_sha256": nullable(SHA),
    "decision": {"enum": ["REVIEW", "HOLD", "UNKNOWN", "MISSING", "TAMPERED"]},
    "provenance_status": {"enum": ["PROVEN", "UNPROVEN", "TAMPERED", "MISSING"]},
    "scope_matched": {"type": "boolean"},
    "tamper_checked": {"type": "boolean"},
    "reason_codes": CODES,
})

PRODUCT_COMPONENT = obj({
    "present": {"type": "boolean"},
    "pack_id": nullable(text(128)),
    "pack_sha256": nullable(SHA),
    "snapshot_id": nullable(text(128)),
    "snapshot_sha256": nullable(SHA),
    "offer_assessment_status": {
        "enum": [
            "VALID_OBSERVATION",
            "STALE",
            "EXPIRED",
            "CONFLICT",
            "NO_OFFERS",
            "UNKNOWN",
            "MISSING",
            "TAMPERED",
        ]
    },
    "active_offer_id": nullable(text(128)),
    "stock_available": nullable({"type": "boolean"}),
    "rights_status": {"enum": ["GRANTED", "UNKNOWN", "RESTRICTED", "MISSING", "TAMPERED"]},
    "synthetic": {"type": "boolean"},
    "scope_matched": {"type": "boolean"},
    "tamper_checked": {"type": "boolean"},
    "status": {"enum": ["VERIFIED", "HOLD", "UNKNOWN", "MISSING", "TAMPERED"]},
    "reason_codes": CODES,
})

CAPABILITY_COMPONENT = obj({
    "present": {"type": "boolean"},
    "snapshot_id": nullable(text(128)),
    "record_sha256": nullable(SHA),
    "decision": {"enum": ["ALLOW", "DENY", "NEEDS_RECHECK", "UNKNOWN", "MISSING", "TAMPERED"]},
    "scope_matched": {"type": "boolean"},
    "tamper_checked": {"type": "boolean"},
    "reason_codes": CODES,
})

HANDOFF_COMPONENT = obj({
    "present": {"type": "boolean"},
    "package_id": nullable(text(128)),
    "package_sha256": nullable(SHA),
    "video_sha256": nullable(SHA),
    "state": {"enum": ["EXPORTED_FOR_MANUAL_HANDOFF", "NOT_PROVIDED", "INVALID", "TAMPERED"]},
    "scope_matched": {"type": "boolean"},
    "tamper_checked": {"type": "boolean"},
    "reason_codes": CODES,
})

COMPONENTS = obj({
    "media_inspection": MEDIA_COMPONENT,
    "claim_rights_review": CLAIM_RIGHTS_COMPONENT,
    "placement_evidence": PLACEMENT_COMPONENT,
    "product_evidence": PRODUCT_COMPONENT,
    "capability_evidence": CAPABILITY_COMPONENT,
    "distribution_handoff": HANDOFF_COMPONENT,
})

REVALIDATION = obj({
    "scope_consistent": {"type": "boolean"},
    "disclosure_compliant": {"type": "boolean"},
    "temporal_active": {"type": "boolean"},
    "url_safe": {"type": "boolean"},
    "hashes_intact": {"type": "boolean"},
    "evidence_complete": {"type": "boolean"},
    "conflicts_detected": {"type": "boolean"},
})


def affiliate_release_pack_schema() -> dict:
    """Return an independent Draft 2020-12 schema for AffiliateReleaseEvidencePack.v1."""
    result = obj({
        "schema_version": {"const": "AffiliateReleaseEvidencePack.v1"},
        "packager_version": {"const": "a13-e03/1.0"},
        "pack_id": PACK_ID,
        "scope": {"const": "ADVISORY_MANUAL_REVIEW_AFFILIATE_RELEASE_EVIDENCE_PACK_ONLY"},
        "verification_scope": {
            "const": "LOCAL_OFFLINE_EVIDENCE_REPLAY_ONLY_NOT_LIVE_PROVIDER_OR_LEGAL_APPROVAL"
        },
        "release_scope": RELEASE_SCOPE,
        "temporal": RELEASE_TEMPORAL,
        "disclosure": DISCLOSURE,
        "url_evaluation": URL_EVALUATION,
        "components": COMPONENTS,
        "evidence_refs": array(EVIDENCE_REF, 128),
        "revalidation": REVALIDATION,
        "state": STATE,
        "decision": STATE,
        "status": STATE,
        "reason_codes": CODES,
        "advisory_only": {"const": True},
        "manual_review_required": {"const": True},
        "live_provider_approval": {"const": "UNKNOWN"},
        "live_link_verified": {"const": False},
        "affiliate_link_status": {"const": "UNKNOWN_OFFLINE_ONLY"},
        "account_eligibility": {"const": "UNKNOWN"},
        "link_activation": {"const": "UNKNOWN"},
        "platform_approval": {"const": "UNKNOWN"},
        "legal_rights_status": {"const": "UNKNOWN"},
        "publication_status": {"const": "NOT_ATTEMPTED"},
        "auto_publishing": {"const": "NEVER_PERFORMED"},
        "oauth_performed": {"const": False},
        "network_calls_performed": {"const": False},
        "sales_claims_allowed": {"const": False},
        "earnings_claims_allowed": {"const": False},
        "sales_earnings_claims": {"const": "NOT_PERMITTED"},
        "revenue_status": {"const": "NOT_CLAIMED"},
        "record_sha256": SHA,
    })
    result.update({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:AffiliateReleaseEvidencePack:v1",
        "title": "AffiliateReleaseEvidencePack.v1",
        "description": (
            "Offline-only advisory manual-review AffiliateReleaseEvidencePack.v1 integrating "
            "C08 media technical inspection, C09 claim and asset rights release review, "
            "D06 affiliate placement evidence, A12 product/variant snapshot/identity/claim/offer/rights "
            "evidence, A07 channel capability, and optional A08 manual distribution handoff. "
            "Outputs strictly READY_FOR_MANUAL_REVIEW, HOLD, or UNKNOWN. Never claims live provider "
            "approval, active affiliate link, POSTED/PUBLIC, sales, OAuth, network/API calls, "
            "revenue, or auto-publishing."
        ),
        "$defs": {
            "AccountRef": ACCOUNT_REF,
            "AppRef": APP_REF,
            "ReleaseScope": RELEASE_SCOPE,
            "ReleaseTemporal": RELEASE_TEMPORAL,
            "PlacementDisclosure": DISCLOSURE,
            "UrlEvaluation": URL_EVALUATION,
            "EvidenceRef": EVIDENCE_REF,
            "MediaComponent": MEDIA_COMPONENT,
            "ClaimRightsComponent": CLAIM_RIGHTS_COMPONENT,
            "PlacementComponent": PLACEMENT_COMPONENT,
            "ProductComponent": PRODUCT_COMPONENT,
            "CapabilityComponent": CAPABILITY_COMPONENT,
            "HandoffComponent": HANDOFF_COMPONENT,
            "Components": COMPONENTS,
            "Revalidation": REVALIDATION,
        },
    })
    return deepcopy(result)


evidence_pack_schema = affiliate_release_pack_schema
