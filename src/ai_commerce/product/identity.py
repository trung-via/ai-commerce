"""Source-scoped asserted identities, without GTIN/SKU/title deduplication."""
from __future__ import annotations

from .core import digest, require, seal_contract, validate_contract
from .schemas import SOURCE_STATUS


def _scoped_id(kind: str, **keys) -> str:
    return "a12-" + kind + "-" + digest({"namespace_version": "a12-asserted-keys.v1",
                                        "kind": kind, **keys})


def _ids(marketplace, seller_key, listing_key, product_key):
    seller = _scoped_id("seller", marketplace=marketplace, seller_key=seller_key)
    listing = _scoped_id("listing", seller_id=seller, listing_key=listing_key)
    product = None if product_key is None else _scoped_id(
        "product", seller_id=seller, product_key=product_key)
    return seller, listing, product


def build_identity(*, marketplace: str, seller_key: str, listing_key: str,
                   product_key: str | None = None) -> dict:
    """Keys are literal, case-sensitive assertions; no whitespace normalization.

    Product keys are scoped to marketplace + seller. Unknown product keys remain
    null; no canonical identity is inferred from a title, GTIN, SKU or URL.
    """
    seller, listing, product = _ids(marketplace, seller_key, listing_key, product_key)
    return seal_contract("ProductIdentity", {
        "schema_version": "ProductIdentity.v1", "source_status": SOURCE_STATUS,
        "identity_status": "SOURCE_SCOPED_ASSERTION", "marketplace": marketplace,
        "asserted_keys": {"seller": seller_key, "listing": listing_key, "product": product_key},
        "seller_id": seller, "listing_id": listing, "product_id": product,
        "canonical_product_id": None,
    })


def _verify_identity(value: dict) -> None:
    keys = value["asserted_keys"]
    expected = _ids(value["marketplace"], keys["seller"], keys["listing"], keys["product"])
    require(tuple(value[key] for key in ("seller_id", "listing_id", "product_id")) == expected,
            "ASSERTED_IDENTITY_MISMATCH")


def build_variant(identity: dict, *, variant_key: str | None = None, sku: str | None = None,
                  gtin: str | None = None, attributes: dict | None = None) -> dict:
    identity = validate_contract("ProductIdentity", identity)
    listing = identity["listing_id"]
    return seal_contract("ProductVariant", {
        "schema_version": "ProductVariant.v1", "source_status": SOURCE_STATUS,
        "identity_status": "SOURCE_SCOPED_ASSERTION", "listing_id": listing,
        "product_id": identity["product_id"],
        "variant_id": None if variant_key is None else _scoped_id(
            "variant", listing_id=listing, variant_key=variant_key),
        "asserted_variant_key": variant_key, "asserted_sku": sku, "asserted_gtin": gtin,
        "attributes": {} if attributes is None else attributes,
    })


def _verify_variant(value: dict) -> None:
    key = value["asserted_variant_key"]
    expected = None if key is None else _scoped_id(
        "variant", listing_id=value["listing_id"], variant_key=key)
    require(value["variant_id"] == expected, "ASSERTED_VARIANT_MISMATCH")
