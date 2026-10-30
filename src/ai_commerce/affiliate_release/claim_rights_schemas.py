"""Portable C09 contract. All references are local; no policy/network lookup."""
from __future__ import annotations

from copy import deepcopy

from ai_commerce.product.evidence_schemas import SUBJECT
from ai_commerce.product.rights_schemas import MEDIA_TYPES, PURPOSES
from ai_commerce.product.schemas import DATE


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def text(limit=512):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


def array(value, maximum=64, minimum=0):
    return {"type": "array", "items": value, "minItems": minimum, "maxItems": maximum,
            "uniqueItems": True}


SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$", "minLength": 64, "maxLength": 64}
ALIAS = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9._:-]{0,127}$", "maxLength": 128,
         "not": {"pattern": "[^A-Za-z0-9._:-]"}}
STATUS = {"enum": ["VERIFIED_EVIDENCE", "UNKNOWN", "NEEDS_REVIEW", "RESTRICTED"]}
CODES = array({"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,95}$", "maxLength": 96}, 128, 1)


def identifier(prefix):
    return {"type": "string", "pattern": "^" + prefix + "[0-9a-f]{64}$",
            "minLength": len(prefix) + 64, "maxLength": len(prefix) + 64}


REQUEST = obj({
    "product_id": identifier("a12-product-"), "listing_id": identifier("a12-listing-"),
    "variant_id": identifier("a12-variant-"), "release_sha256": SHA,
    "territory": {"type": "string", "pattern": "^[A-Z]{2}$", "minLength": 2, "maxLength": 2},
    "channel": ALIAS, "as_of": DATE, "use_from": DATE, "use_until": DATE,
})
CLAIM_PLACEMENT = obj({
    "placement_ref": ALIAS, "statement": text(4096),
    "interpretation": {"enum": ["FACTUAL", "NONFACTUAL"]},
    "evidence_refs": array(identifier("a12-claim-"), 32),
})
ASSET_USAGE = obj({
    "asset_ref": text(2048), "content_sha256": SHA, "media_type": {"enum": list(MEDIA_TYPES)},
    "purposes": array({"enum": list(PURPOSES)}, len(PURPOSES), 1),
})
RIGHTS_STATUS_EVIDENCE = obj({
    "schema_version": {"const": "ReleaseRightsStatusEvidence.v1"},
    "rights_id": identifier("a12-rights-"), "asset_sha256": SHA,
    "license_ref": ALIAS, "purpose": {"enum": list(PURPOSES)},
    "state": {"enum": ["NOT_REVOKED", "REVOKED", "RESTRICTED", "UNKNOWN"]},
    "issuer_ref": ALIAS, "source_ref": ALIAS,
    "observed_at": DATE, "effective_at": DATE, "valid_until": nullable(DATE),
    "synthetic": {"type": "boolean"},
})


def claim_rights_review_schema() -> dict:
    """Return an independent Draft 2020-12 schema, including status-source shape."""
    result = obj({
        "schema_version": {"const": "ClaimRightsReleaseReview.v1"},
        "reviewer_version": {"const": "a13-c09/1.0"},
        "scope": {"const": "COMMERCIAL_PLACEMENT_CLAIMS_AND_RIGHTS_ONLY"},
        "verification_scope": {"const": "LOCAL_EVIDENCE_REPLAY_ONLY_NOT_EXTERNAL_OR_LEGAL_APPROVAL"},
        "source_authenticity": {"const": "NOT_ATTESTED"},
        "claim_coverage": {"const": "CALLER_DECLARED_NOT_EXHAUSTIVE"},
        "request": REQUEST, "subject": SUBJECT,
        "pack_id": identifier("a12-pack-"), "pack_sha256": SHA,
        "snapshot_sha256": nullable(SHA),
        "media_inspection_sha256": nullable(SHA), "render_manifest_sha256": nullable(SHA),
        "usage_verification": {"enum": ["DIRECT_ASSET_BYTES", "RENDER_SOURCE_HASHES", "UNPROVEN"]},
        "claim_evidence_refs": array(obj({
            "claim_id": identifier("a12-claim-"), "record_sha256": SHA, "source_sha256": SHA,
            "proof_sha256": nullable(SHA),
        }), 2000),
        "rights_evidence_refs": array(obj({
            "rights_id": identifier("a12-rights-"), "record_sha256": SHA, "source_sha256": SHA,
            "license_sha256s": array(obj({"purpose": {"enum": list(PURPOSES)}, "sha256": SHA}), 3),
        }), 100),
        "rights_status_evidence": array(obj({**RIGHTS_STATUS_EVIDENCE["properties"], "evidence_sha256": SHA})),
        "claim_reviews": array(obj({**CLAIM_PLACEMENT["properties"],
                                    "status": STATUS, "reason_codes": CODES}), 128),
        "asset_reviews": array(obj({**ASSET_USAGE["properties"],
            "observed_sha256": nullable(SHA),
            "size_bytes": nullable({"type": "integer", "minimum": 1, "maximum": 256 * 1024 * 1024}),
            "rights_refs": array(identifier("a12-rights-"), 100),
            "purpose_reviews": array(obj({"purpose": {"enum": list(PURPOSES)},
                                          "status": STATUS, "reason_codes": CODES}), 3, 1),
            "status": STATUS, "reason_codes": CODES,
        })),
        "claim_status": STATUS, "rights_status": STATUS, "status": STATUS, "reason_codes": CODES,
        "legal_rights_status": {"const": "UNKNOWN"}, "platform_approval": {"const": "UNKNOWN"},
        "account_eligibility": {"const": "UNKNOWN"}, "link_activation": {"const": "UNKNOWN"},
        "publication_status": {"const": "NOT_ATTEMPTED"}, "record_sha256": SHA,
    })
    result.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:ClaimRightsReleaseReview:v1", "title": "ClaimRightsReleaseReview.v1",
        "description": (
            "Deterministic, offline commercial placement review of declared claims and actual asset bytes. "
            "VERIFIED_EVIDENCE describes exact local evidence replay, never external truth, legal permission, "
            "platform approval, account eligibility or publication. Nonfactual declarations are outside factual "
            "claim review. Status evidence is a supplied, dated holder observation, not issuer authentication. "
            "Reading requires original inputs and full recomputation; a resealed digest alone is insufficient."
        ), "$defs": {"ReleaseRightsStatusEvidence": RIGHTS_STATUS_EVIDENCE}})
    return deepcopy(result)
