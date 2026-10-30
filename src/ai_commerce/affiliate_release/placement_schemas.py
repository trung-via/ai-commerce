"""Portable D06 contract. All references are local; no policy/network lookup."""
from __future__ import annotations

from copy import deepcopy

from ai_commerce.product.schemas import DATE, SHA


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def text(limit=512):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


def array(value, maximum=64, minimum=0):
    return {
        "type": "array",
        "items": value,
        "minItems": minimum,
        "maxItems": maximum,
        "uniqueItems": True,
    }


ALIAS = {
    "type": "string",
    "pattern": "^[A-Za-z][A-Za-z0-9._:-]{0,127}$",
    "maxLength": 128,
    "not": {"pattern": "[^A-Za-z0-9._:-]"},
}
DECISION = {"enum": ["HOLD", "UNKNOWN", "REVIEW"]}
CODES = array({"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,95}$", "maxLength": 96}, 128, 1)

ACCOUNT_REF = obj({
    "namespace": text(128),
    "id": text(256),
})

APP_REF = nullable(obj({
    "namespace": text(128),
    "id": text(256),
}))

SURFACES = [
    "PRODUCT_ANCHOR",
    "VIDEO_STICKER",
    "BIO",
    "POST",
    "STORY",
    "COMMENT",
    "VIDEO",
    "REEL",
    "SHORT",
    "PAGE_REEL",
    "PROFILE_VIDEO",
]

PLACEMENT = obj({
    "account_ref": ACCOUNT_REF,
    "app_ref": APP_REF,
    "market": {"type": "string", "pattern": "^[A-Z]{2}$", "minLength": 2, "maxLength": 2},
    "surface": {"enum": SURFACES},
    "product_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "variant_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "link_url": text(4096),
})

TEMPORAL = obj({
    "as_of": DATE,
    "valid_from": DATE,
    "valid_until": DATE,
    "revoked_at": nullable(DATE),
    "status": {"enum": ["ACTIVE", "EXPIRED", "REVOKED", "NOT_YET_EFFECTIVE", "INVALID_WINDOW"]},
})

DISCLOSURE = obj({
    "statement": text(4096),
    "mode": {"enum": ["CLEAR_AND_CONSPICUOUS", "PROMINENT_TEXT", "PLATFORM_DISCLOSURE_LABEL"]},
    "has_affiliate_notice": {"type": "boolean"},
    "tags": array({"type": "string", "pattern": "^#[A-Za-z0-9_]+$", "maxLength": 64}, 16),
    "status": {"enum": ["COMPLIANT", "NON_COMPLIANT"]},
})

URL_EVALUATION = obj({
    "parsed_url": text(4096),
    "scheme": text(32),
    "host": text(256),
    "is_hostile": {"type": "boolean"},
    "reason_codes": CODES,
})

EVIDENCE_REF = obj({
    "evidence_id": text(256),
    "kind": text(128),
    "record_sha256": SHA,
    "source_sha256": nullable(SHA),
    "tamper_checked": {"type": "boolean"},
})

EVIDENCE_PROVENANCE = obj({
    "source_kind": {
        "enum": [
            "A12_OFFER_OBSERVATION",
            "A12_PRODUCT_PACK_BOUND",
            "A07_CAPABILITY_BOUND",
            "OFFLINE_EVIDENCE_REPLAY",
            "TEST_FIXTURE",
        ]
    },
    "evidence_refs": array(EVIDENCE_REF, 64),
    "provenance_status": {"enum": ["PROVEN", "UNPROVEN", "TAMPERED"]},
})


def affiliate_placement_schema() -> dict:
    """Return an independent Draft 2020-12 schema for AffiliatePlacementEvidence.v1."""
    result = obj({
        "schema_version": {"const": "AffiliatePlacementEvidence.v1"},
        "reviewer_version": {"const": "a13-d06/1.0"},
        "scope": {"const": "COMMERCIAL_AFFILIATE_PLACEMENT_OFFLINE_EVIDENCE_ONLY"},
        "verification_scope": {"const": "LOCAL_OFFLINE_EVIDENCE_ONLY_NOT_EXTERNAL_OR_PROVIDER_APPROVAL"},
        "placement": PLACEMENT,
        "temporal": TEMPORAL,
        "disclosure": DISCLOSURE,
        "url_evaluation": URL_EVALUATION,
        "evidence_provenance": EVIDENCE_PROVENANCE,
        "decision": DECISION,
        "reason_codes": CODES,
        "live_link_verified": {"const": False},
        "account_eligibility": {"const": "UNKNOWN"},
        "link_activation": {"const": "UNKNOWN"},
        "platform_approval": {"const": "UNKNOWN"},
        "publication_status": {"const": "NOT_ATTEMPTED"},
        "sales_claims_allowed": {"const": False},
        "earnings_claims_allowed": {"const": False},
        "sales_earnings_claims": {"const": "NOT_PERMITTED"},
        "record_sha256": SHA,
    })
    result.update({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:AffiliatePlacementEvidence:v1",
        "title": "AffiliatePlacementEvidence.v1",
        "description": (
            "Deterministic, offline commercial affiliate placement evidence review. "
            "Evaluates account x app x market x surface x product x variant x link, "
            "evidence provenance, required disclosure, expiry/revocation, hostile URLs, "
            "cross-account mismatches, and tampered hashes. Parsed URL alone never implies "
            "an active eligible link; decisions are strictly HOLD, UNKNOWN, or REVIEW. "
            "No OAuth, live provider calls, posting, real approvals, sales or earnings claims."
        ),
        "$defs": {
            "AccountRef": ACCOUNT_REF,
            "AppRef": APP_REF,
            "PlacementSubject": PLACEMENT,
            "PlacementTemporal": TEMPORAL,
            "PlacementDisclosure": DISCLOSURE,
            "UrlEvaluation": URL_EVALUATION,
            "EvidenceRef": EVIDENCE_REF,
            "EvidenceProvenance": EVIDENCE_PROVENANCE,
        },
    })
    return deepcopy(result)
