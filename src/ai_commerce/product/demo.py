"""Deterministic synthetic A12 end-to-end demonstration and golden pack production."""
from __future__ import annotations

import json
from pathlib import Path

from ai_commerce.creative.briefs import intake_brief

from .claims import build_claim, review_claim
from .core import ProductError, canonical_bytes, require, sha256_bytes
from .offers import build_offer
from .pack import (
    ProductEvidencePack, export_pack_from_registry, read_evidence_pack,
    write_evidence_pack,
)
from .reader import read_snapshot
from .registry import ProductEvidenceRegistry
from .rights import build_rights, review_rights

ASSET_IMAGE_BYTES = b"offline synthetic image bytes; no real media or license"
ASSET_SHA = sha256_bytes(ASSET_IMAGE_BYTES)


def run_demo(output_dir: Path | str) -> dict:
    """Run full deterministic offline A12 demonstration and write artifacts."""
    directory = Path(output_dir).resolve()
    require(not directory.exists(), "DEMO_OUTPUT_ALREADY_EXISTS")
    directory.mkdir(parents=True, exist_ok=False)

    # 1. Raw listing snapshot inside ManualProductListings.v1 envelope
    raw_listing = {
        "marketplace": "fixture",
        "seller_key": "merchant",
        "listing_key": "item",
        "product_key": "prod-tee-organic",
        "variant_key": "blue",
        "sku": "SKU-TEE-BLU-L",
        "gtin": "0123456789012",
        "title": "EchoThread Organic Cotton Crewneck",
        "description": "Premium 100% certified organic cotton crewneck t-shirt.",
        "listing_url": "https://marketplace.mock/items/listing-tee-100",
        "observed_at": "2026-10-30T00:00:00Z",
        "price_amount": 29.99,
        "currency": "USD",
        "variant_attributes": {"color": "navy_blue", "size": "L"},
    }
    raw_envelope = {
        "schema_version": "ManualProductListings.v1",
        "rows": [raw_listing],
    }
    raw_bytes = canonical_bytes(raw_envelope)
    raw_file = directory / "listing_raw.json"
    raw_file.write_bytes(raw_bytes)

    snapshot = read_snapshot(
        raw_file,
        root=directory,
        file_format="json",
        source_ref="fixtures/listing_raw.json",
        captured_at="2026-10-30T01:00:00Z",
        source_kind="HUMAN_SUPPLIED",
        declared_verification="Supplier self-declaration; unverified",
        synthetic=False,
    )
    (directory / "snapshot.json").write_bytes(snapshot.to_bytes())

    # 2. Claims: seller-asserted vs independently verified with proof
    seller_claim_source = {
        "schema_version": "ProductClaimSource.v1",
        "claim_key": "composition-organic",
        "category": "COMPOSITION",
        "statement": "Crafted from 100% GOTS certified organic combed cotton",
        "value": "100% Organic Cotton",
        "unit": None,
        "origin": "SELLER",
        "role": "SUPPORTING",
        "observed_at": "2026-10-30T00:00:00Z",
    }
    seller_claim_raw = canonical_bytes(seller_claim_source)
    seller_claim = build_claim(
        snapshot,
        ordinal=1,
        raw=seller_claim_raw,
        source_ref="fixtures/claim_seller.json",
        captured_at="2026-10-30T01:10:00Z",
        source_kind="HUMAN_SUPPLIED",
    )
    (directory / "claim_seller.json").write_bytes(seller_claim.to_bytes())

    proof_raw = canonical_bytes({
        "report_id": "ECO-CERT-2026-9988",
        "test_scope": "blue variant",
        "findings": "Passed Class I OEKO-TEX Standard",
    })
    (directory / "proof_report.json").write_bytes(proof_raw)

    verified_claim_source = {
        "schema_version": "ProductClaimSource.v1",
        "claim_key": "hypoallergenic-safety",
        "category": "SAFETY",
        "statement": "Dermatologically tested and certified safe for sensitive skin",
        "value": "Passed Class I OEKO-TEX Standard",
        "unit": None,
        "origin": "SELLER",
        "role": "SUPPORTING",
        "observed_at": "2026-10-30T00:00:00Z",
    }
    verified_claim_raw = canonical_bytes(verified_claim_source)
    unreviewed_claim = build_claim(
        snapshot,
        ordinal=1,
        raw=verified_claim_raw,
        source_ref="fixtures/claim_verified_source.json",
        captured_at="2026-10-30T01:15:00Z",
        source_kind="HUMAN_SUPPLIED",
    )
    verified_claim = review_claim(
        unreviewed_claim,
        proof_raw=proof_raw,
        reviewer_ref="auditor-alias",
        producer_ref="lab-alias",
        method="Independent blinded laboratory sample analysis",
        reviewed_at="2026-10-30T01:30:00Z",
        evidence_ref="reports/ECO-CERT-2026-9988.json",
        evidence_kind="INDEPENDENT_TEST_REPORT",
    )
    (directory / "claim_verified.json").write_bytes(verified_claim.to_bytes())

    # 3. Offers: recent active observation vs stale observation
    offer_active_source = {
        "schema_version": "ProductOfferSource.v1",
        "observed_at": "2026-10-30T00:00:00Z",
        "expires_at": "2026-11-30T00:00:00Z",
        "price_amount": "29.99",
        "currency": "USD",
        "stock_available": True,
        "commission_rate": "0.08",
        "commission_amount": "2.40",
        "commission_currency": "USD",
    }
    offer_active_raw = canonical_bytes(offer_active_source)
    offer_active = build_offer(
        snapshot,
        ordinal=1,
        raw=offer_active_raw,
        source_ref="fixtures/offer_active.json",
        captured_at="2026-10-30T01:20:00Z",
        source_kind="HUMAN_SUPPLIED",
    )
    (directory / "offer_active.json").write_bytes(offer_active.to_bytes())

    offer_stale_source = {
        "schema_version": "ProductOfferSource.v1",
        "observed_at": "2026-08-01T00:00:00Z",
        "expires_at": "2026-08-31T00:00:00Z",
        "price_amount": "24.99",
        "currency": "USD",
        "stock_available": True,
        "commission_rate": "0.05",
        "commission_amount": "1.25",
        "commission_currency": "USD",
    }
    offer_stale_raw = canonical_bytes(offer_stale_source)
    offer_stale = build_offer(
        snapshot,
        ordinal=1,
        raw=offer_stale_raw,
        source_ref="fixtures/offer_stale.json",
        captured_at="2026-08-01T01:00:00Z",
        source_kind="HUMAN_SUPPLIED",
    )
    (directory / "offer_stale.json").write_bytes(offer_stale.to_bytes())

    # 4. Rights: image asset with deliberate local license review
    rights_source = {
        "schema_version": "ProductAssetRightsSource.v1",
        "asset": {
            "asset_ref": "https://invalid.test/image.jpg",
            "content_sha256": ASSET_SHA,
            "media_type": "IMAGE",
            "origin": "CREATOR_SUPPLIED",
            "origin_ref": "holder-export",
            "copyright_holder_ref": "holder-alias",
            "claimant_ref": "merchant-alias",
            "license_label": "CC-BY-4.0",
        },
        "permissions": [
            {
                "purpose": "COMMERCIAL_USE",
                "declared_status": "GRANTED",
                "license_ref": "license-1",
                "scope": {
                    "territories": ["VN"],
                    "media_types": ["IMAGE"],
                    "channels": ["social-ad"],
                    "valid_from": "2026-10-01T00:00:00Z",
                    "valid_until": "2026-11-01T00:00:00Z",
                },
            },
            {
                "purpose": "ADVERTISEMENT",
                "declared_status": "RESTRICTED",
                "license_ref": None,
                "scope": None,
            },
        ],
    }
    rights_raw = canonical_bytes(rights_source)
    rights_unreviewed = build_rights(
        snapshot,
        ordinal=1,
        raw=rights_raw,
        source_ref="fixtures/rights_manifest.json",
        captured_at="2026-10-30T01:25:00Z",
        source_kind="HUMAN_SUPPLIED",
    )

    license_raw = canonical_bytes({
        "schema_version": "ProductAssetLicenseEvidence.v1",
        "instrument_kind": "HOLDER_PERMISSION",
        "asset_sha256": ASSET_SHA,
        "copyright_holder_ref": "holder-alias",
        "issuer_ref": "holder-alias",
        "authority_basis": "COPYRIGHT_HOLDER",
        "license_ref": "license-1",
        "issued_at": "2026-10-01T00:00:00Z",
        "purpose": "COMMERCIAL_USE",
        "scope": {
            "territories": ["VN"],
            "media_types": ["IMAGE"],
            "channels": ["social-ad"],
            "valid_from": "2026-10-01T00:00:00Z",
            "valid_until": "2026-11-01T00:00:00Z",
        },
        "conclusion": "ALLOWS_EXACT_SCOPED_USE",
    })
    (directory / "license_instrument.json").write_bytes(license_raw)

    rights_reviewed = review_rights(
        rights_unreviewed,
        purpose="COMMERCIAL_USE",
        license_raw=license_raw,
        reviewer_ref="reviewer-alias",
        reviewed_at="2026-10-30T01:30:00Z",
        method="Match local holder instrument to exact asset and use",
        evidence_ref="local-license-export",
    )
    (directory / "rights.json").write_bytes(rights_reviewed.to_bytes())

    # 5. Append-only registry journal
    registry = ProductEvidenceRegistry(directory / "evidence.sqlite", root=directory)
    registry.initialize()
    registry.append(snapshot, stream_key="listing-stream", recorded_at="2026-10-30T02:00:00Z")
    registry.append(seller_claim, stream_key="claim-stream-1", recorded_at="2026-10-30T02:01:00Z")
    registry.append(verified_claim, stream_key="claim-stream-2", recorded_at="2026-10-30T02:02:00Z")
    registry.append(offer_active, stream_key="offer-stream", recorded_at="2026-10-30T02:03:00Z")
    registry.append(offer_stale, stream_key="offer-stale-stream", recorded_at="2026-10-30T02:04:00Z")
    registry.append(rights_reviewed, stream_key="rights-stream", recorded_at="2026-10-30T02:05:00Z")

    integrity = registry.integrity_check()
    require(integrity["integrity"] == "LOCAL_HASH_AND_REPLAY_VERIFIED", "INTEGRITY_CHECK_FAILED")

    # 6. Export ProductEvidencePack
    target_lid = snapshot.to_dict()["listings"][0]["identity"]["listing_id"]
    pack = export_pack_from_registry(
        registry,
        listing_id=target_lid,
        as_of="2026-10-30T03:00:00Z",
        max_age_seconds=30 * 24 * 3600,
    )
    pack_path = directory / "evidence_pack.json"
    write_evidence_pack(pack_path, pack)

    # 7. Replay & Tamper Verification
    registry2 = ProductEvidenceRegistry(directory / "replay.sqlite", root=directory)
    registry2.initialize()
    replay_results = registry2.replay(registry.events())
    require(len(replay_results) == len(registry.events()), "REPLAY_COUNT_MISMATCH")
    require(registry2.integrity_check()["head_hash"] == integrity["head_hash"], "REPLAY_HASH_MISMATCH")

    reloaded = read_evidence_pack(pack_path, expected_id=pack.pack_id)
    require(reloaded.to_bytes() == pack.to_bytes(), "RELOAD_PACK_MISMATCH")

    # Creative intake: pack advisory reference vs freeform
    brief_with_pack = intake_brief({
        "brief_id": "brief:synthetic-tee-01",
        "objective": "Promote sustainable tee",
        "prompt": "Create video ad highlighting organic cotton",
        "product_ref": pack.pack_id,
        "source_refs": [pack.pack_id],
    })
    require(brief_with_pack["product_ref"] == pack.pack_id, "CREATIVE_BRIEF_FAILED")

    brief_freeform = intake_brief("Freeform creative concept without product")
    require(brief_freeform["product_ref"] is None, "FREEFORM_CREATIVE_FAILED")

    # Negative tamper check: upgrade seller claim to verified without proof
    tampered = pack.to_dict()
    tampered["claims"][0]["status"] = "INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE"
    try:
        ProductEvidencePack(tampered)
    except ProductError as exc:
        tamper_code = exc.code
    else:
        raise ProductError("TAMPER_ACCEPTED")

    summary = {
        "schema_version": "A12SyntheticDemo.v1",
        "source_status": "USER_SUPPLIED_NOT_LIVE_VERIFIED",
        "snapshot_id": snapshot.snapshot_id,
        "pack_id": pack.pack_id,
        "event_count": len(registry.events()),
        "head_hash": integrity["head_hash"],
        "claims_count": len(pack.claims),
        "verified_claims_count": sum(1 for c in pack.claims if c["has_independent_proof"]),
        "unverified_seller_claims_count": sum(1 for c in pack.claims if not c["has_independent_proof"]),
        "offer_assessment_status": pack.offers["assessment_status"],
        "active_price": pack.offers["active_offer"]["price_amount"] if pack.offers["active_offer"] else None,
        "rights_effective_status": pack.rights[0]["effective_status"] if pack.rights else None,
        "pack_rights_status": pack.rights_status,
        "tamper_rejected_code": tamper_code,
        "replay_identical": True,
        "creative_freeform_preserved": True,
    }
    (directory / "summary.json").write_bytes(canonical_bytes(summary))
    return summary
