"""A12-02 portable contracts and bounded local evidence payloads.

The A12-01 schemas and public exports stay byte-for-byte compatible. These
definitions use only local references and need neither a checkout nor A02 edits.
"""
from __future__ import annotations

from copy import deepcopy

from .schemas import (DATE, KEY, MAX_RAW_BYTES, SHA, SOURCE_STATUS, identifier,
                      nullable, obj, text)

CLAIM_STATES = (
    "SOURCE_OBSERVED", "SELLER_ASSERTED", "INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE",
    "ILLUSTRATIVE", "UNKNOWN", "CONFLICT",
)
CATEGORIES = ("ATTRIBUTE", "PERFORMANCE", "SAFETY", "COMPOSITION", "ORIGIN",
              "COMPATIBILITY", "WARRANTY", "OTHER")
ROLES = ("SUPPORTING", "COUNTEREVIDENCE", "INCONCLUSIVE")
MAX_EVIDENCE_RECORDS = 2000
MAX_FRESHNESS_SECONDS = 366 * 24 * 60 * 60
DECIMAL_PATTERN = r"^(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,8})?$"
AMOUNT = {"type": "string", "maxLength": 25, "pattern": DECIMAL_PATTERN}
CURRENCY = {"type": "string", "minLength": 3, "maxLength": 3, "pattern": "^[A-Z]{3}$"}
POINTER = {"type": "string", "maxLength": 1024}
PRESENCE = {"enum": ["MISSING", "NULL", "VALUE"]}
SUBJECT = obj({
    "snapshot_id": identifier("a12-snapshot-"),
    "source_snapshot_id": identifier("a12-source-"), "snapshot_raw_sha256": SHA,
    "ordinal": {"type": "integer", "minimum": 1, "maximum": 2000},
    "source_namespace": {**KEY, "maxLength": 128},
    "identity_id": identifier("a12-identity-"),
    "variant_record_id": identifier("a12-variant-record-"),
    "seller_id": identifier("a12-seller-"), "listing_id": identifier("a12-listing-"),
    "product_id": nullable(identifier("a12-product-")),
    "variant_id": nullable(identifier("a12-variant-")), "variant_key": nullable(KEY),
    "synthetic": {"type": "boolean"},
})
EVIDENCE_SOURCE = obj({
    "source_ref": text(2048), "source_kind": {"enum": ["HUMAN_SUPPLIED", "PREEXPORTED_API"]},
    "source_status": {"const": SOURCE_STATUS}, "captured_at": DATE,
    "raw_sha256": SHA, "raw_ref": identifier("sha256:"),
    "raw_size_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_RAW_BYTES},
    "pointer": POINTER,
})
VALUE = {"type": ["string", "number", "boolean", "null"], "maxLength": 4096,
         "minimum": -1e15, "maximum": 1e15}
CLAIM_REFS = {"type": "array", "uniqueItems": True, "maxItems": 32,
              "items": identifier("a12-claim-")}
CLAIM_DATA = obj({
    "claim_key": KEY, "category": {"enum": list(CATEGORIES)}, "statement": text(4096),
    "value": VALUE, "unit": nullable(text(64)),
    "origin": {"enum": ["SELLER", "OBSERVER", "ILLUSTRATION", "UNKNOWN", "CONFLICT"]},
    "role": {"enum": list(ROLES)}, "observed_at": nullable(DATE),
    "conflict_refs": CLAIM_REFS,
})
CLAIM_INPUT = obj({"schema_version": {"const": "ProductClaimSource.v1"},
                   **CLAIM_DATA["properties"]},
                  ["schema_version"] + [key for key in CLAIM_DATA["properties"]
                                         if key != "conflict_refs"])
PROOF = obj({
    "reviewer_ref": text(), "producer_ref": text(), "method": text(2048),
    "reviewed_at": DATE, "evidence_ref": text(2048),
    "evidence_kind": {"enum": ["INDEPENDENT_TEST_REPORT", "INDEPENDENT_CERTIFICATE",
                                "INDEPENDENT_MEASUREMENT"]},
    "evidence_sha256": SHA, "evidence_ref_sha256": identifier("sha256:"),
    "evidence_size_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_RAW_BYTES},
    "claim_sha256": SHA, "conclusion": {"const": "SUPPORTS"},
    "verification_scope": {"const": "EXPLICIT_REVIEW_OF_LOCAL_EVIDENCE_ONLY"},
})
COMMON = {
    "source_status": {"const": SOURCE_STATUS},
    "verification_scope": {"const": "LOCAL_BYTES_HASH_AND_REPLAY_ONLY"},
    "rights_status": {"const": "UNKNOWN"}, "publication_status": {"const": "NOT_ATTEMPTED"},
    "subject": {"$ref": "#/$defs/ProductSubject"},
    "source": {"$ref": "#/$defs/ProductEvidenceSource"},
}
CLAIM = obj({
    "schema_version": {"const": "ProductClaimEvidence.v1"},
    "claim_id": identifier("a12-claim-"), **COMMON,
    "claim": CLAIM_DATA, "status": {"enum": list(CLAIM_STATES)},
    "reviewer_proof": nullable(PROOF),
})
CLAIM["allOf"] = [{
    "if": {"properties": {"status": {"const": CLAIM_STATES[2]}}},
    "then": {"properties": {"reviewer_proof": PROOF,
                              "subject": {"properties": {"synthetic": {"const": False}}}}},
    "else": {"properties": {"reviewer_proof": {"type": "null"}}},
}, {
    "if": {"properties": {"status": {"const": "CONFLICT"}}},
    "then": {"properties": {"claim": {"properties": {
        "conflict_refs": {**CLAIM_REFS, "minItems": 2}}}}},
}]
OFFER_INPUT_PROPERTIES = {
    "schema_version": {"const": "ProductOfferSource.v1"}, "observed_at": DATE,
    "expires_at": nullable(DATE), "price_amount": nullable(AMOUNT),
    "currency": nullable(CURRENCY), "stock_available": {"type": ["boolean", "null"]},
    "commission_rate": nullable(AMOUNT), "commission_amount": nullable(AMOUNT),
    "commission_currency": nullable(CURRENCY),
}
OFFER_INPUT = obj(OFFER_INPUT_PROPERTIES, ["schema_version", "observed_at"])
MONEY = obj({"amount": nullable(AMOUNT), "currency": nullable(CURRENCY),
             "amount_presence": PRESENCE, "status": {"enum": ["UNKNOWN", "SOURCE_ASSERTED_NOT_LIVE"]}})
MONEY["allOf"] = [{
    "if": {"properties": {"amount_presence": {"const": "VALUE"}}},
    "then": {"properties": {"amount": AMOUNT, "currency": CURRENCY,
                              "status": {"const": "SOURCE_ASSERTED_NOT_LIVE"}}},
    "else": {"properties": {"amount": {"type": "null"}, "status": {"const": "UNKNOWN"}}},
}]
OFFER = obj({
    "schema_version": {"const": "ProductOfferObservation.v1"},
    "offer_id": identifier("a12-offer-"), **COMMON,
    "observed_at": DATE, "expires_at": nullable(DATE),
    "provided_fields": {"type": "array", "uniqueItems": True,
                        "maxItems": len(OFFER_INPUT_PROPERTIES),
                        "items": {"enum": list(OFFER_INPUT_PROPERTIES)}},
    "price": MONEY,
    "stock": obj({"available": {"type": ["boolean", "null"]}, "presence": PRESENCE}),
    "commission": obj({
        "rate": nullable(AMOUNT), "rate_presence": PRESENCE,
        "amount": nullable(AMOUNT), "amount_presence": PRESENCE,
        "currency": nullable(CURRENCY), "status": {"const": "NOT_ELIGIBILITY_VERIFIED"},
    }),
    "live_price_status": {"const": "NOT_LIVE_VERIFIED"},
})
OFFER["properties"]["subject"] = {
    "allOf": [{"$ref": "#/$defs/ProductSubject"}, {"properties": {
        "variant_id": identifier("a12-variant-"), "variant_key": KEY}}],
}


def evidence_schema(name: str) -> dict:
    definition = {"ProductClaimEvidence": CLAIM, "ProductOfferObservation": OFFER}[name]
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:" + name + ":v1", "title": name + ".v1",
        "description": (
            "A12 offline evidence; source and report authenticity are not externally attested. "
            "Content ID hashes canonical JSON excluding its own ID. Validation requires the "
            "exact raw evidence and pinned A12-01 snapshot; independent claim status also "
            "requires separate evidence bytes and explicit scoped reviewer proof. "
            "Amounts are strict nonnegative decimal strings, never binary floats. "
            "No live price, affiliate eligibility, rights grant or publication is implied."
        ), **definition,
        "$defs": {"ProductSubject": SUBJECT, "ProductEvidenceSource": EVIDENCE_SOURCE},
    })
