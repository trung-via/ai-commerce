"""Portable ProductEvidencePack.v1 contract; advisory export for Creative/Opportunity.

The pack is an immutable, versioned snapshot synthesizing source listing, exact variant
identity, seller vs verified claims, time-bounded offers with freshness evaluation,
and asset rights manifests with explicit UNKNOWN/RESTRICTED protections.
It never claims live marketplace verification, affiliate permission, commercial media
license or published state.
"""
from __future__ import annotations

from copy import deepcopy

from .evidence_schemas import (
    AMOUNT, CATEGORIES, CLAIM_DATA, CLAIM_REFS, CLAIM_STATES, CURRENCY,
    DECIMAL_PATTERN, EVIDENCE_SOURCE, MAX_FRESHNESS_SECONDS, PROOF, ROLES,
    SUBJECT, VALUE,
)
from .rights_schemas import (
    ALIAS, MEDIA_TYPES, PURPOSES, RIGHTS_STATES, SCOPE,
)
from .schemas import (
    ATTRIBUTES, DATE, KEY, ROW_PROPERTIES, SHA, SOURCE_STATUS, identifier,
    nullable, obj, text,
)

PACK_VERSION = "a12-pack.v1"

ADVISORY_CAVEATS = (
    "Human advisory consultation only; no automatic publishing, action promotion or Creative mutation.",
    "Raw source hash proves local file bytes, not external marketplace truth, authorized reseller status or live state.",
    "Seller statements are unverified assertions unless accompanied by explicit independent reviewer proof.",
    "Offer prices and commissions are point-in-time observations, not guaranteed or live prices; stale offers are not current.",
    "Asset media rights are UNKNOWN or RESTRICTED without verified independent license instruments; commercial use is not granted.",
    "ProductEvidencePack is optional for Creative; creative briefs may omit product reference entirely.",
)

PACK_CLAIM = obj({
    "claim_id": identifier("a12-claim-"),
    "claim_key": KEY,
    "category": {"enum": list(CATEGORIES)},
    "statement": text(4096),
    "value": VALUE,
    "unit": nullable(text(64)),
    "origin": {"enum": ["SELLER", "OBSERVER", "ILLUSTRATION", "UNKNOWN", "CONFLICT"]},
    "role": {"enum": list(ROLES)},
    "status": {"enum": list(CLAIM_STATES)},
    "observed_at": nullable(DATE),
    "has_independent_proof": {"type": "boolean"},
    "reviewer_proof": nullable(PROOF),
    "conflict_refs": CLAIM_REFS,
})
PACK_CLAIM["allOf"] = [{
    "if": {"properties": {"status": {"const": CLAIM_STATES[2]}}},
    "then": {"properties": {"has_independent_proof": {"const": True}, "reviewer_proof": PROOF}},
    "else": {"properties": {"has_independent_proof": {"const": False}, "reviewer_proof": {"type": "null"}}},
}]

PACK_OFFER_OBSERVATION = obj({
    "offer_id": identifier("a12-offer-"),
    "observed_at": DATE,
    "expires_at": nullable(DATE),
    "price": obj({
        "amount": nullable(AMOUNT),
        "currency": nullable(CURRENCY),
        "amount_presence": {"enum": ["MISSING", "NULL", "VALUE"]},
        "status": {"enum": ["UNKNOWN", "SOURCE_ASSERTED_NOT_LIVE"]},
    }),
    "stock": obj({
        "available": {"type": ["boolean", "null"]},
        "presence": {"enum": ["MISSING", "NULL", "VALUE"]},
    }),
    "commission": obj({
        "rate": nullable(AMOUNT),
        "rate_presence": {"enum": ["MISSING", "NULL", "VALUE"]},
        "amount": nullable(AMOUNT),
        "amount_presence": {"enum": ["MISSING", "NULL", "VALUE"]},
        "currency": nullable(CURRENCY),
        "status": {"const": "NOT_ELIGIBILITY_VERIFIED"},
    }),
    "freshness": {"enum": ["RECENT_OBSERVATION", "STALE", "NOT_YET_OBSERVED"]},
    "reason_codes": {"type": "array", "maxItems": 16, "items": text(64)},
})

PACK_ACTIVE_OFFER = obj({
    "offer_id": identifier("a12-offer-"),
    "price_amount": nullable(AMOUNT),
    "currency": nullable(CURRENCY),
    "stock_available": {"type": ["boolean", "null"]},
    "commission_rate": nullable(AMOUNT),
    "commission_currency": nullable(CURRENCY),
})

PACK_OFFERS = obj({
    "as_of": DATE,
    "freshness_limit_seconds": {"type": "integer", "minimum": 0, "maximum": MAX_FRESHNESS_SECONDS},
    "assessment_status": {"enum": ["VALID_OBSERVATION", "STALE", "CONFLICT", "NO_OFFERS", "NOT_YET_OBSERVED"]},
    "active_offer": nullable(PACK_ACTIVE_OFFER),
    "conflict_fields": {"type": "array", "maxItems": 8, "uniqueItems": True, "items": text(64)},
    "observations": {"type": "array", "maxItems": 1000, "items": PACK_OFFER_OBSERVATION},
})

PACK_PERMISSION = obj({
    "purpose": {"enum": list(PURPOSES)},
    "declared_status": {"enum": list(RIGHTS_STATES)},
    "status": {"enum": list(RIGHTS_STATES)},
    "license_ref": nullable(ALIAS),
    "scope": nullable(SCOPE),
    "reviewed": {"type": "boolean"},
})

PACK_RIGHTS_ITEM = obj({
    "rights_id": identifier("a12-rights-"),
    "asset_ref": text(2048),
    "content_sha256": SHA,
    "media_type": {"enum": list(MEDIA_TYPES)},
    "origin": {"enum": ["UNKNOWN", "SELLER_SUPPLIED", "CREATOR_SUPPLIED", "LICENSED_LIBRARY", "PUBLIC_DOMAIN_CLAIM", "HUMAN_SUPPLIED"]},
    "origin_ref": nullable(text(2048)),
    "copyright_holder_ref": nullable(ALIAS),
    "claimant_ref": nullable(ALIAS),
    "effective_status": {"enum": ["UNKNOWN", "RESTRICTED", "GRANTED", "MIXED"]},
    "permissions": {"type": "array", "minItems": 1, "maxItems": len(PURPOSES), "items": PACK_PERMISSION},
})

PACK_LISTING = obj({
    "marketplace": ROW_PROPERTIES["marketplace"],
    "seller_key": ROW_PROPERTIES["seller_key"],
    "listing_key": ROW_PROPERTIES["listing_key"],
    "title": ROW_PROPERTIES["title"],
    "description": ROW_PROPERTIES["description"],
    "listing_url": ROW_PROPERTIES["listing_url"],
    "sku": ROW_PROPERTIES["sku"],
    "gtin": ROW_PROPERTIES["gtin"],
    "variant_attributes": ATTRIBUTES,
})

PACK = obj({
    "schema_version": {"const": "ProductEvidencePack.v1"},
    "pack_id": identifier("a12-pack-"),
    "pack_version": {"const": PACK_VERSION},
    "source_status": {"const": SOURCE_STATUS},
    "verification_scope": {"const": "LOCAL_BYTES_HASH_AND_REPLAY_ONLY"},
    "rights_status": {"enum": ["UNKNOWN", "RESTRICTED", "MIXED", "GRANTED"]},
    "affiliate_status": {"const": "UNKNOWN"},
    "publication_status": {"const": "NOT_ATTEMPTED"},
    "subject": {"$ref": "#/$defs/ProductSubject"},
    "listing": PACK_LISTING,
    "source": {"$ref": "#/$defs/ProductEvidenceSource"},
    "claims": {"type": "array", "maxItems": 2000, "items": PACK_CLAIM},
    "offers": PACK_OFFERS,
    "rights": {"type": "array", "maxItems": 100, "items": PACK_RIGHTS_ITEM},
    "advisory_caveats": {"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True, "items": text(512)},
    "registry_event_refs": {"type": "array", "maxItems": 2048, "uniqueItems": True, "items": identifier("a12-event-")},
    "generated_at": DATE,
})


def pack_schema() -> dict:
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:ProductEvidencePack:v1",
        "title": "ProductEvidencePack.v1",
        "description": (
            "Offline product evidence pack for Creative and Opportunity advisory consultation. "
            "Synthesizes source listing snapshot, exact variant identity, seller vs independently "
            "verified claims, time-bounded offer observations with freshness assessment, and asset "
            "rights manifests with explicit UNKNOWN/RESTRICTED protections. "
            "The content ID hashes canonical JSON excluding pack_id. "
            "Never implies live marketplace verification, affiliate permission, commercial media "
            "license or published state."
        ),
        **PACK,
        "$defs": {
            "ProductSubject": SUBJECT,
            "ProductEvidenceSource": EVIDENCE_SOURCE,
        },
    })
