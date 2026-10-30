"""A12 v1 offline product evidence foundation; optional advisory component."""
from .claims import ProductClaimEvidence, build_claim, review_claim
from .core import (
    ProductError, canonical_bytes, content_id, digest, parse_contract,
    parse_json, parse_timestamp, seal_contract, validate_contract,
)
from .identity import build_identity, build_variant
from .offers import ProductOfferObservation, assess_offers, build_offer
from .pack import (
    ProductEvidencePack, build_evidence_pack, export_pack_from_registry,
    read_evidence_pack, write_evidence_pack,
)
from .pack_schemas import PACK_VERSION, pack_schema
from .reader import ProductListingSnapshot, prepare_bytes, read_snapshot
from .registry import EvidenceEvent, ProductEvidenceRegistry
from .rights import ProductAssetRights, assess_rights, build_rights, review_rights
from .schemas import READER_VERSION, SOURCE_STATUS, schema

__all__ = [
    "EvidenceEvent", "PACK_VERSION", "ProductAssetRights", "ProductClaimEvidence",
    "ProductError", "ProductEvidencePack", "ProductEvidenceRegistry",
    "ProductListingSnapshot", "ProductOfferObservation", "READER_VERSION",
    "SOURCE_STATUS", "assess_offers", "assess_rights", "build_claim",
    "build_evidence_pack", "build_identity", "build_offer", "build_rights",
    "build_variant", "canonical_bytes", "content_id", "digest",
    "export_pack_from_registry", "pack_schema", "parse_contract",
    "parse_json", "parse_timestamp", "prepare_bytes", "read_evidence_pack",
    "read_snapshot", "review_claim", "review_rights", "schema", "seal_contract",
    "validate_contract", "write_evidence_pack",
]
