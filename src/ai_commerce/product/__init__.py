"""A12 v1 offline product evidence foundation; optional advisory component."""
from .core import (ProductError, canonical_bytes, content_id, digest, parse_contract,
                   parse_json, parse_timestamp, seal_contract, validate_contract)
from .identity import build_identity, build_variant
from .reader import ProductListingSnapshot, prepare_bytes, read_snapshot
from .schemas import READER_VERSION, SOURCE_STATUS, schema

__all__ = [
    "ProductError", "ProductListingSnapshot", "READER_VERSION", "SOURCE_STATUS",
    "build_identity", "build_variant", "canonical_bytes", "content_id", "digest",
    "parse_contract", "parse_json", "parse_timestamp", "prepare_bytes", "read_snapshot",
    "schema", "seal_contract", "validate_contract",
]
