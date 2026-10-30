"""Self-contained A12-01 v1 schemas; published JSON files mirror schema().

Only local JSON references are used. No A02 registration or package-data change
is necessary, including when this component is installed in a wheel.
"""
from __future__ import annotations

from copy import deepcopy

READER_VERSION = "a12-snapshot-reader.v1"
SOURCE_STATUS = "USER_SUPPLIED_NOT_LIVE_VERIFIED"
MAX_RAW_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_ROWS = 2000
MAX_ROW_BYTES = 64 * 1024
MAX_DEPTH = 32
MAX_NODES = 200000
MAX_FIELD_CHARS = 65536
CONTRACT_IDS = {
    "ProductListingSnapshot": ("snapshot_id", "a12-snapshot-"),
    "ProductIdentity": ("identity_id", "a12-identity-"),
    "ProductVariant": ("variant_record_id", "a12-variant-record-"),
    "ProductClaimEvidence": ("claim_id", "a12-claim-"),
    "ProductOfferObservation": ("offer_id", "a12-offer-"),
    "ProductAssetRights": ("rights_id", "a12-rights-"),
}


def text(limit=256):
    return {"type": "string", "minLength": 1, "maxLength": limit, "pattern": r"\S"}


def nullable(value):
    return {"oneOf": [value, {"type": "null"}]}


def obj(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


def identifier(prefix):
    return {"type": "string", "pattern": "^" + prefix + "[a-f0-9]{64}$"}


KEY = {**text(), "pattern": r"^(?![\s\S]*[\x00-\x1f\x7f])\S(?:.*\S)?$"}
TIMESTAMP_PATTERN = (
    r"^(?!.*-00:00$)[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"[Tt](?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$"
)
DATE = {**text(40), "format": "date-time", "pattern": TIMESTAMP_PATTERN}
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
ATTRIBUTES = {"type": "object", "maxProperties": 32,
              "propertyNames": text(128), "additionalProperties": text(256)}
ROW_PROPERTIES = {
    "marketplace": {**KEY, "maxLength": 128}, "seller_key": KEY, "listing_key": KEY,
    "product_key": nullable(KEY), "variant_key": nullable(KEY),
    "sku": nullable(KEY), "gtin": nullable(KEY),
    "title": nullable(text(1024)), "description": nullable(text(4096)),
    "listing_url": nullable(text(2048)),
    "observed_at": nullable(DATE),
    "price_amount": {"type": ["number", "null"], "minimum": 0, "maximum": 1e15},
    "currency": nullable({"type": "string", "pattern": "^[A-Z]{3}$"}),
    "variant_attributes": ATTRIBUTES,
}
REQUIRED_ROW_FIELDS = ("marketplace", "seller_key", "listing_key")
MANUAL_ROW = obj(ROW_PROPERTIES, list(REQUIRED_ROW_FIELDS))

IDENTITY = obj({
    "schema_version": {"const": "ProductIdentity.v1"},
    "identity_id": identifier("a12-identity-"),
    "source_status": {"const": SOURCE_STATUS},
    "identity_status": {"const": "SOURCE_SCOPED_ASSERTION"},
    "marketplace": ROW_PROPERTIES["marketplace"],
    "asserted_keys": obj({"seller": KEY, "listing": KEY, "product": nullable(KEY)}),
    "seller_id": identifier("a12-seller-"), "listing_id": identifier("a12-listing-"),
    "product_id": nullable(identifier("a12-product-")),
    "canonical_product_id": {"type": "null"},
})
VARIANT = obj({
    "schema_version": {"const": "ProductVariant.v1"},
    "variant_record_id": identifier("a12-variant-record-"),
    "source_status": {"const": SOURCE_STATUS},
    "identity_status": {"const": "SOURCE_SCOPED_ASSERTION"},
    "listing_id": identifier("a12-listing-"),
    "product_id": nullable(identifier("a12-product-")),
    "variant_id": nullable(identifier("a12-variant-")),
    "asserted_variant_key": nullable(KEY), "asserted_sku": nullable(KEY),
    "asserted_gtin": nullable(KEY), "attributes": ATTRIBUTES,
})
MAPPING = {"type": "object", "minProperties": 3, "maxProperties": len(ROW_PROPERTIES),
           "propertyNames": {"enum": list(ROW_PROPERTIES)}, "additionalProperties": text(256),
           "required": list(REQUIRED_ROW_FIELDS)}
OPTIONS = obj({"json_rows_pointer": nullable({"type": "string", "maxLength": 1024}),
               "field_map": nullable(MAPPING)})
SOURCE = obj({
    "source_snapshot_id": identifier("a12-source-"),
    "source_kind": {"enum": ["HUMAN_SUPPLIED", "PREEXPORTED_API"]},
    "source_ref": text(2048), "source_url": nullable(text(2048)),
    "source_status": {"const": SOURCE_STATUS},
    "declared_verification": nullable(text(256)), "synthetic": {"type": "boolean"},
    "captured_at": DATE, "format": {"enum": ["json", "csv"]},
    "raw_sha256": SHA, "raw_ref": identifier("sha256:"),
    "raw_size_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_RAW_BYTES},
})
PRICE = obj({
    "status": {"enum": ["UNKNOWN", "SOURCE_ASSERTED_NOT_LIVE"]},
    "amount": ROW_PROPERTIES["price_amount"], "currency": ROW_PROPERTIES["currency"],
})
PRICE["allOf"] = [{
    "if": {"properties": {"status": {"const": "UNKNOWN"}}},
    "then": {"properties": {"amount": {"type": "null"}}},
    "else": {"properties": {"amount": {"type": "number"},
                              "currency": {"type": "string", "pattern": "^[A-Z]{3}$"}}},
}]
LISTING = obj({
    "ordinal": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS},
    "source_row": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS + 1},
    "source_pointer": nullable({"type": "string", "maxLength": 1040}),
    "provided_fields": {"type": "array", "uniqueItems": True, "maxItems": len(ROW_PROPERTIES),
                        "items": {"enum": list(ROW_PROPERTIES)}},
    "identity": {"$ref": "#/$defs/ProductIdentity"},
    "variant": {"$ref": "#/$defs/ProductVariant"},
    "title": ROW_PROPERTIES["title"], "description": ROW_PROPERTIES["description"],
    "listing_url": ROW_PROPERTIES["listing_url"], "observed_at": ROW_PROPERTIES["observed_at"],
    "price": PRICE,
})
SNAPSHOT = obj({
    "schema_version": {"const": "ProductListingSnapshot.v1"},
    "snapshot_id": identifier("a12-snapshot-"), "reader_version": {"const": READER_VERSION},
    "verification_scope": {"const": "LOCAL_BYTES_HASH_AND_REPLAY_ONLY"},
    "rights_status": {"const": "UNKNOWN"}, "affiliate_status": {"const": "UNKNOWN"},
    "publication_status": {"const": "NOT_ATTEMPTED"},
    "source": SOURCE, "options": OPTIONS,
    "listings": {"type": "array", "minItems": 1, "maxItems": MAX_ROWS, "items": LISTING},
})


def schema(name: str) -> dict:
    """Return a detached portable schema; unknown names have a bounded error."""
    from .core import require
    require(type(name) is str and name in CONTRACT_IDS, "UNKNOWN_PRODUCT_CONTRACT")
    if name == "ProductAssetRights":
        from .rights_schemas import rights_schema
        return rights_schema()
    if name in ("ProductClaimEvidence", "ProductOfferObservation"):
        from .evidence_schemas import evidence_schema
        return evidence_schema(name)
    definition = {"ProductListingSnapshot": SNAPSHOT, "ProductIdentity": IDENTITY,
                  "ProductVariant": VARIANT}[name]
    result = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "$id": "urn:ai-commerce:" + name + ":v1", "title": name + ".v1",
              "description": (
                  "A12 offline source assertions, never independent verification, live offers or rights. "
                  "Content ID is the prefixed SHA256 of canonical UTF-8 JSON excluding its own ID. "
                  "Snapshot validation additionally requires exact raw bytes and deterministic replay."
              ), **definition}
    if name == "ProductListingSnapshot":
        result["$defs"] = {"ProductIdentity": IDENTITY, "ProductVariant": VARIANT}
    return deepcopy(result)
