"""Portable P05 contract; labels and source declarations are never permission.

The sole GRANTED exception is an explicit local review of a separate structured
rights instrument for the exact asset hash and use scope. It authenticates no
person or legal document. UNKNOWN/RESTRICTED declarations cannot be reviewed
into grants. No blanket rights, affiliate or publication authority is conveyed.
"""
from __future__ import annotations

from copy import deepcopy

from .evidence_schemas import EVIDENCE_SOURCE, SUBJECT
from .schemas import DATE, SHA, SOURCE_STATUS, identifier, nullable, obj, text

PURPOSES = ("COMMERCIAL_USE", "ADVERTISEMENT", "CREATIVE_DERIVATIVE")
MEDIA_TYPES = ("IMAGE", "VIDEO", "AUDIO", "TEXT", "MODEL_3D")
RIGHTS_STATES = ("UNKNOWN", "RESTRICTED", "GRANTED")
MAX_RIGHTS_BYTES = 256 * 1024
ALIAS = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$"}
TERRITORY = {"type": "string", "pattern": r"^(?:[A-Z]{2}|WORLDWIDE)$"}


def choices(items, limit=32):
    return {"type": "array", "minItems": 1, "maxItems": limit,
            "uniqueItems": True, "items": items}


SCOPE = obj({
    "territories": choices(TERRITORY, 64),
    "media_types": choices({"enum": list(MEDIA_TYPES)}, len(MEDIA_TYPES)),
    "channels": choices(ALIAS), "valid_from": DATE, "valid_until": DATE,
})
ASSET_INPUT = obj({
    "asset_ref": text(2048), "content_sha256": SHA,
    "media_type": {"enum": list(MEDIA_TYPES)},
    "origin": {"enum": ["UNKNOWN", "SELLER_SUPPLIED", "CREATOR_SUPPLIED",
                           "LICENSED_LIBRARY", "PUBLIC_DOMAIN_CLAIM", "HUMAN_SUPPLIED"]},
    "origin_ref": nullable(text(2048)),
    "copyright_holder_ref": nullable(ALIAS), "claimant_ref": nullable(ALIAS),
    "license_label": nullable(text(256)),
})
PERMISSION_INPUT = obj({
    "purpose": {"enum": list(PURPOSES)}, "declared_status": {"enum": list(RIGHTS_STATES)},
    "license_ref": nullable(ALIAS), "scope": nullable(SCOPE),
})
RIGHTS_INPUT = obj({
    "schema_version": {"const": "ProductAssetRightsSource.v1"}, "asset": ASSET_INPUT,
    "permissions": choices(PERMISSION_INPUT, len(PURPOSES)),
})
# This is a bounded local transcription of a rights instrument, not an online
# license resolver. No arbitrary attachment, personal signature or token field.
LICENSE_INPUT = obj({
    "schema_version": {"const": "ProductAssetLicenseEvidence.v1"},
    "instrument_kind": {"enum": ["HOLDER_PERMISSION", "LICENSE_INSTRUMENT"]},
    "asset_sha256": SHA, "copyright_holder_ref": ALIAS, "issuer_ref": ALIAS,
    "authority_basis": {"const": "COPYRIGHT_HOLDER"}, "license_ref": ALIAS,
    "issued_at": DATE, "purpose": {"enum": list(PURPOSES)}, "scope": SCOPE,
    "conclusion": {"const": "ALLOWS_EXACT_SCOPED_USE"},
})
REVIEW = obj({
    "reviewer_ref": ALIAS, "reviewed_at": DATE, "method": text(2048),
    "evidence_ref": text(2048), "evidence_sha256": SHA,
    "evidence_size_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_RIGHTS_BYTES},
    "permission_sha256": SHA,
    "verification_scope": {"const": "EXPLICIT_LOCAL_LICENSE_REVIEW_ONLY"},
})
PERMISSION = obj({**PERMISSION_INPUT["properties"],
                  "status": {"enum": list(RIGHTS_STATES)}, "review": nullable(REVIEW)})
PERMISSION["allOf"] = [{
    "if": {"properties": {"status": {"const": "GRANTED"}}},
    "then": {"properties": {"declared_status": {"const": "GRANTED"}, "review": REVIEW,
                             "scope": SCOPE, "license_ref": ALIAS}},
    "else": {"properties": {"review": {"type": "null"}}},
}, {
    "if": {"properties": {"declared_status": {"const": "RESTRICTED"}}},
    "then": {"properties": {"status": {"const": "RESTRICTED"}}},
}, {
    "if": {"properties": {"declared_status": {"const": "UNKNOWN"}}},
    "then": {"properties": {"status": {"const": "UNKNOWN"}}},
}]
ASSET = obj({**ASSET_INPUT["properties"],
             "content_ref": identifier("sha256:"), "origin_source_sha256": SHA,
             "content_verification": {"const": "SOURCE_ASSERTED_NOT_RETRIEVED"}})
RIGHTS = obj({
    "schema_version": {"const": "ProductAssetRights.v1"},
    "rights_id": identifier("a12-rights-"), "source_status": {"const": SOURCE_STATUS},
    "verification_scope": {"const": "LOCAL_BYTES_HASH_AND_REPLAY_ONLY"},
    "rights_status": {"const": "UNKNOWN"}, "affiliate_status": {"const": "UNKNOWN"},
    "publication_status": {"const": "NOT_ATTEMPTED"},
    "subject": {"$ref": "#/$defs/ProductSubject"},
    "source": {"$ref": "#/$defs/ProductEvidenceSource"}, "asset": ASSET,
    "permissions": choices(PERMISSION, len(PURPOSES)),
})


def rights_schema() -> dict:
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:ProductAssetRights:v1", "title": "ProductAssetRights.v1",
        "description": (
            "Offline purpose-scoped rights evidence, not authenticated legal permission. "
            "The content ID hashes canonical JSON excluding rights_id. Semantic validation "
            "requires exact source bytes, the pinned listing snapshot, and separate license "
            "bytes for every reviewed grant. Labels, URLs and UNKNOWN/RESTRICTED declarations "
            "cannot grant use. Scope term is [valid_from, valid_until); missing purposes are "
            "UNKNOWN. Asset bytes are not retrieved or stored; a use assessment also requires "
            "matching local asset bytes. Blanket rights, affiliate and publication stay UNKNOWN."
        ), **RIGHTS,
        "$defs": {"ProductSubject": SUBJECT, "ProductEvidenceSource": EVIDENCE_SOURCE},
    })
