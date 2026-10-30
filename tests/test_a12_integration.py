"""Actual A12-04 fan-in integration tests: ProductEvidencePack, registry, replay, and Creative consumer."""
from __future__ import annotations

import json
import socket
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.creative.briefs import intake_brief, revise_brief
from ai_commerce.product import (
    ProductClaimEvidence, ProductError, ProductEvidencePack,
    ProductEvidenceRegistry, ProductListingSnapshot, ProductOfferObservation,
    build_claim, build_evidence_pack, build_offer, build_rights,
    canonical_bytes, content_id, export_pack_from_registry, pack_schema,
    parse_contract, prepare_bytes, read_evidence_pack, read_snapshot,
    review_claim, review_rights, schema, seal_contract, validate_contract,
    write_evidence_pack,
)
from ai_commerce.product.demo import run_demo

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = "2026-10-30T01:00:00Z"
ASSET_BYTES = b"offline test synthetic image bytes 123456"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Enforce strict offline-first execution: network access is forbidden."""
    def forbidden(*args, **kwargs):
        pytest.fail("A12 integration attempted network operation")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def _sample_snapshot(tmp_path: Path, *, synthetic: bool = False) -> ProductListingSnapshot:
    raw = canonical_bytes({
        "schema_version": "ManualProductListings.v1",
        "rows": [
            {
                "marketplace": "mock-market",
                "seller_key": "seller-eco",
                "listing_key": "item-tee-01",
                "product_key": "prod-tee",
                "variant_key": "blue-l",
                "sku": "SKU-BLU-L",
                "gtin": "0123456789012",
                "title": "Eco Organic Cotton Tee",
                "description": "Certified organic crewneck",
                "listing_url": "https://mock-market.test/items/tee-01",
                "observed_at": "2026-10-30T00:00:00Z",
                "price_amount": 29.99,
                "currency": "USD",
                "variant_attributes": {"color": "blue", "size": "L"},
            },
            {
                "marketplace": "mock-market",
                "seller_key": "seller-eco",
                "listing_key": "item-tee-01",
                "product_key": "prod-tee",
                "variant_key": "green-m",
                "sku": "SKU-GRN-M",
                "gtin": "0123456789013",
                "title": "Eco Organic Cotton Tee",
                "description": "Certified organic crewneck",
                "listing_url": "https://mock-market.test/items/tee-01",
                "observed_at": "2026-10-30T00:00:00Z",
                "price_amount": 29.99,
                "currency": "USD",
                "variant_attributes": {"color": "green", "size": "M"},
            },
        ],
    })
    path = tmp_path / "raw_listings.json"
    path.write_bytes(raw)
    return read_snapshot(
        path,
        root=tmp_path,
        file_format="json",
        source_ref="fixtures/raw_listings.json",
        captured_at=CAPTURE,
        synthetic=synthetic,
    )


def _sample_pack_components(tmp_path: Path, snapshot: ProductListingSnapshot):
    # Claim 1: seller asserted
    claim1_raw = canonical_bytes({
        "schema_version": "ProductClaimSource.v1",
        "claim_key": "material-composition",
        "category": "COMPOSITION",
        "statement": "100% GOTS certified organic combed cotton",
        "value": "100% Organic Cotton",
        "unit": None,
        "origin": "SELLER",
        "role": "SUPPORTING",
        "observed_at": "2026-10-30T00:00:00Z",
    })
    claim1 = build_claim(snapshot, ordinal=1, raw=claim1_raw, source_ref="claim1.json", captured_at=CAPTURE)

    # Claim 2: verified with reviewer proof
    claim2_raw = canonical_bytes({
        "schema_version": "ProductClaimSource.v1",
        "claim_key": "hypoallergenic",
        "category": "SAFETY",
        "statement": "Tested safe for sensitive skin",
        "value": "OEKO-TEX Class I Passed",
        "unit": None,
        "origin": "SELLER",
        "role": "SUPPORTING",
        "observed_at": "2026-10-30T00:00:00Z",
    })
    claim2_unreviewed = build_claim(snapshot, ordinal=1, raw=claim2_raw, source_ref="claim2.json", captured_at=CAPTURE)
    proof_raw = canonical_bytes({"report": "LAB-TEST-001", "result": "PASSED"})
    claim2 = review_claim(
        claim2_unreviewed,
        proof_raw=proof_raw,
        reviewer_ref="auditor-01",
        producer_ref="lab-01",
        method="Blinded chemical test",
        reviewed_at="2026-10-30T01:30:00Z",
        evidence_ref="reports/LAB-TEST-001.json",
        evidence_kind="INDEPENDENT_TEST_REPORT",
    )

    # Offer: active
    offer_raw = canonical_bytes({
        "schema_version": "ProductOfferSource.v1",
        "observed_at": "2026-10-30T00:00:00Z",
        "expires_at": "2026-11-30T00:00:00Z",
        "price_amount": "29.99",
        "currency": "USD",
        "stock_available": True,
        "commission_rate": "0.10",
        "commission_amount": "3.00",
        "commission_currency": "USD",
    })
    offer = build_offer(snapshot, ordinal=1, raw=offer_raw, source_ref="offer.json", captured_at=CAPTURE)

    # Rights: reviewed commercial use
    from ai_commerce.core.provenance import sha256_bytes
    asset_sha = sha256_bytes(ASSET_BYTES)
    rights_raw = canonical_bytes({
        "schema_version": "ProductAssetRightsSource.v1",
        "asset": {
            "asset_ref": "images/tee.jpg",
            "content_sha256": asset_sha,
            "media_type": "IMAGE",
            "origin": "CREATOR_SUPPLIED",
            "origin_ref": "contracts/photo.pdf",
            "copyright_holder_ref": "photo-holder",
            "claimant_ref": "merchant-user",
            "license_label": "Standard commercial release",
        },
        "permissions": [
            {
                "purpose": "COMMERCIAL_USE",
                "declared_status": "GRANTED",
                "license_ref": "license-tee-01",
                "scope": {
                    "territories": ["US"],
                    "media_types": ["IMAGE"],
                    "channels": ["social-ad"],
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_until": "2027-01-01T00:00:00Z",
                },
            },
            {
                "purpose": "ADVERTISEMENT",
                "declared_status": "RESTRICTED",
                "license_ref": None,
                "scope": None,
            },
        ],
    })
    rights_unreviewed = build_rights(snapshot, ordinal=1, raw=rights_raw, source_ref="rights.json", captured_at=CAPTURE)
    license_raw = canonical_bytes({
        "schema_version": "ProductAssetLicenseEvidence.v1",
        "instrument_kind": "HOLDER_PERMISSION",
        "asset_sha256": asset_sha,
        "copyright_holder_ref": "photo-holder",
        "issuer_ref": "photo-holder",
        "authority_basis": "COPYRIGHT_HOLDER",
        "license_ref": "license-tee-01",
        "issued_at": "2026-01-01T00:00:00Z",
        "purpose": "COMMERCIAL_USE",
        "scope": {
            "territories": ["US"],
            "media_types": ["IMAGE"],
            "channels": ["social-ad"],
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": "2027-01-01T00:00:00Z",
        },
        "conclusion": "ALLOWS_EXACT_SCOPED_USE",
    })
    rights = review_rights(
        rights_unreviewed,
        purpose="COMMERCIAL_USE",
        license_raw=license_raw,
        reviewer_ref="legal-counsel",
        reviewed_at="2026-10-30T01:30:00Z",
        method="Verified photography licensing contract",
        evidence_ref="licenses/license-tee-01.json",
    )

    return claim1, claim2, offer, rights


def test_published_schema_exact_mirror():
    """Verify contracts/ProductEvidencePack.v1.schema.json matches schema() byte-for-byte."""
    path = ROOT / "contracts" / "ProductEvidencePack.v1.schema.json"
    assert path.is_file()
    definition = schema("ProductEvidencePack")
    assert json.loads(path.read_bytes()) == definition
    Draft202012Validator.check_schema(definition)
    assert definition == pack_schema()


def test_evidence_pack_content_id_and_sealing(tmp_path):
    snapshot = _sample_snapshot(tmp_path)
    claim1, claim2, offer, rights = _sample_pack_components(tmp_path, snapshot)
    pack = build_evidence_pack(snapshot, ordinal=1, claims=[claim1, claim2], offers=[offer], rights=[rights])

    d = pack.to_dict()
    assert d["pack_id"].startswith("a12-pack-")
    assert pack.pack_id == d["pack_id"]
    assert content_id("ProductEvidencePack", d) == pack.pack_id

    # validate_contract, parse_contract, seal_contract
    assert validate_contract("ProductEvidencePack", d, snapshot=snapshot) == d
    assert parse_contract("ProductEvidencePack", pack.to_bytes(), snapshot=snapshot) == d

    body = deepcopy(d)
    body.pop("pack_id")
    sealed = seal_contract("ProductEvidencePack", body, snapshot=snapshot)
    assert sealed == d

    # Tampered pack_id
    tampered = deepcopy(d)
    tampered["pack_id"] = "a12-pack-" + "0" * 64
    with pytest.raises(ProductError, match="CONTENT_ID_MISMATCH"):
        validate_contract("ProductEvidencePack", tampered)

    # Immutability
    with pytest.raises(FrozenInstanceError):
        pack._encoded = b"changed"


def test_offline_full_e2e_pipeline(tmp_path):
    snapshot = _sample_snapshot(tmp_path)
    claim1, claim2, offer, rights = _sample_pack_components(tmp_path, snapshot)

    # Registry journal
    registry = ProductEvidenceRegistry(tmp_path / "evidence.sqlite", root=tmp_path)
    registry.initialize()
    ev1 = registry.append(snapshot, stream_key="snap-s", recorded_at="2026-10-30T02:00:00Z")
    ev2 = registry.append(claim1, stream_key="c1-s", recorded_at="2026-10-30T02:01:00Z")
    ev3 = registry.append(claim2, stream_key="c2-s", recorded_at="2026-10-30T02:02:00Z")
    ev4 = registry.append(offer, stream_key="off-s", recorded_at="2026-10-30T02:03:00Z")
    ev5 = registry.append(rights, stream_key="r-s", recorded_at="2026-10-30T02:04:00Z")

    integrity = registry.integrity_check()
    assert integrity["integrity"] == "LOCAL_HASH_AND_REPLAY_VERIFIED"
    assert integrity["event_count"] == 5

    # Export
    lid = snapshot.to_dict()["listings"][0]["identity"]["listing_id"]
    vid = snapshot.to_dict()["listings"][0]["variant"]["variant_id"]
    pack = export_pack_from_registry(registry, listing_id=lid, variant_id=vid, as_of="2026-10-30T03:00:00Z")

    assert pack.subject["listing_id"] == lid
    assert pack.subject["variant_id"] == vid
    assert len(pack.claims) == 2
    assert pack.offers["assessment_status"] == "VALID_OBSERVATION"
    assert pack.offers["active_offer"]["price_amount"] == "29.99"
    assert pack.rights[0]["effective_status"] == "RESTRICTED"
    assert pack.rights_status == "RESTRICTED"
    assert len(pack.advisory_caveats) == 6

    # File write and reload
    pack_file = tmp_path / "pack.json"
    write_evidence_pack(pack_file, pack)
    reloaded = read_evidence_pack(pack_file, expected_id=pack.pack_id)
    assert reloaded.to_bytes() == pack.to_bytes()

    # Replay to another registry
    registry2 = ProductEvidenceRegistry(tmp_path / "replay.sqlite", root=tmp_path)
    registry2.initialize()
    results = registry2.replay(registry.events())
    assert len(results) == 5
    assert registry2.integrity_check()["head_hash"] == integrity["head_hash"]


def test_demo_script_determinism(tmp_path):
    """Verify demo runs deterministically producing identical outputs in two runs."""
    dir1 = tmp_path / "demo_1"
    dir2 = tmp_path / "demo_2"
    res1 = run_demo(dir1)
    res2 = run_demo(dir2)

    assert res1["pack_id"] == res2["pack_id"]
    assert res1["head_hash"] == res2["head_hash"]
    assert res1["snapshot_id"] == res2["snapshot_id"]
    assert res1 == res2

    # Compare bytes of exported packs
    p1 = (dir1 / "evidence_pack.json").read_bytes()
    p2 = (dir2 / "evidence_pack.json").read_bytes()
    assert p1 == p2


def test_creative_brief_consumer_advisory_intake(tmp_path):
    """Verify Creative brief consumer works with pack_id and with freeform (None)."""
    snapshot = _sample_snapshot(tmp_path)
    claim1, claim2, offer, rights = _sample_pack_components(tmp_path, snapshot)
    pack = build_evidence_pack(snapshot, ordinal=1, claims=[claim1, claim2], offers=[offer], rights=[rights])

    # Consumer with product pack reference
    brief = intake_brief({
        "brief_id": "brief:test-with-pack",
        "objective": "Promote sustainable apparel",
        "prompt": "Highlight organic cotton authenticity",
        "product_ref": pack.pack_id,
        "source_refs": [pack.pack_id],
    })
    assert brief["product_ref"] == pack.pack_id

    # Revise brief preserving product_ref
    revised = revise_brief(brief, {"prompt": "Highlight durability and softness"})
    assert revised["product_ref"] == pack.pack_id

    # Freeform creative brief without product (product_ref is None)
    freeform = intake_brief("Freeform creative story about outdoor exploration")
    assert freeform["product_ref"] is None


def test_tamper_seller_claim_to_verified_rejected(tmp_path):
    snapshot = _sample_snapshot(tmp_path)
    claim1, claim2, offer, rights = _sample_pack_components(tmp_path, snapshot)
    pack = build_evidence_pack(snapshot, ordinal=1, claims=[claim1, claim2], offers=[offer], rights=[rights])

    # 1. Modify unverified seller claim status to INDEPENDENTLY_VERIFIED without proof
    tampered = pack.to_dict()
    unverified_claim = next(c for c in tampered["claims"] if not c["has_independent_proof"])
    unverified_claim["status"] = "INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE"
    tampered["pack_id"] = content_id("ProductEvidencePack", tampered)
    with pytest.raises(ProductError, match="PROOF_FLAG_MISMATCH|INVALID_PRODUCTEVIDENCEPACK"):
        ProductEvidencePack(tampered)

    # 2. Modify has_independent_proof to True without reviewer proof
    tampered = pack.to_dict()
    unverified_claim = next(c for c in tampered["claims"] if not c["has_independent_proof"])
    unverified_claim["has_independent_proof"] = True
    tampered["pack_id"] = content_id("ProductEvidencePack", tampered)
    with pytest.raises(ProductError, match="PROOF_FLAG_MISMATCH|INVALID_PRODUCTEVIDENCEPACK"):
        ProductEvidencePack(tampered)


def test_tamper_rights_upgrade_rejected(tmp_path):
    snapshot = _sample_snapshot(tmp_path)
    claim1, claim2, offer, rights = _sample_pack_components(tmp_path, snapshot)
    pack = build_evidence_pack(snapshot, ordinal=1, claims=[claim1, claim2], offers=[offer], rights=[rights])

    # Upgrade declared RESTRICTED to GRANTED without review
    tampered = pack.to_dict()
    tampered["rights"][0]["permissions"][0]["status"] = "GRANTED"
    tampered["rights"][0]["permissions"][0]["declared_status"] = "RESTRICTED"
    tampered["pack_id"] = "a12-pack-" + "3" * 64
    with pytest.raises(ProductError):
        ProductEvidencePack(tampered)

    # Force pack level rights_status to GRANTED when asset is RESTRICTED
    tampered = pack.to_dict()
    tampered["rights_status"] = "GRANTED"
    tampered["pack_id"] = content_id("ProductEvidencePack", tampered)
    with pytest.raises(ProductError, match="INVALID_PACK_RIGHTS_STATUS"):
        ProductEvidencePack(tampered)


def test_stale_offers_and_freshness_boundary(tmp_path):
    snapshot = _sample_snapshot(tmp_path)
    stale_raw = canonical_bytes({
        "schema_version": "ProductOfferSource.v1",
        "observed_at": "2026-06-01T00:00:00Z",
        "expires_at": "2026-07-01T00:00:00Z",
        "price_amount": "19.99",
        "currency": "USD",
        "stock_available": True,
        "commission_rate": "0.05",
        "commission_amount": "1.00",
        "commission_currency": "USD",
    })
    stale_offer = build_offer(snapshot, ordinal=1, raw=stale_raw, source_ref="stale.json", captured_at=CAPTURE)
    pack = build_evidence_pack(snapshot, ordinal=1, offers=[stale_offer], as_of="2026-10-30T03:00:00Z")

    assert pack.offers["assessment_status"] == "STALE"
    assert pack.offers["active_offer"] is None
    obs = pack.offers["observations"][0]
    assert obs["freshness"] == "STALE"
    assert "EXPIRED" in obs["reason_codes"]
    assert "MAX_AGE_EXCEEDED" in obs["reason_codes"]


def test_conflicting_offers_detection(tmp_path):
    snapshot = _sample_snapshot(tmp_path)
    offer1_raw = canonical_bytes({
        "schema_version": "ProductOfferSource.v1",
        "observed_at": "2026-10-30T00:00:00Z",
        "expires_at": "2026-11-30T00:00:00Z",
        "price_amount": "29.99",
        "currency": "USD",
        "stock_available": True,
        "commission_rate": "0.05",
        "commission_amount": "1.50",
        "commission_currency": "USD",
    })
    offer2_raw = canonical_bytes({
        "schema_version": "ProductOfferSource.v1",
        "observed_at": "2026-10-30T00:00:00Z",
        "expires_at": "2026-11-30T00:00:00Z",
        "price_amount": "39.99",
        "currency": "USD",
        "stock_available": True,
        "commission_rate": "0.05",
        "commission_amount": "2.00",
        "commission_currency": "USD",
    })
    o1 = build_offer(snapshot, ordinal=1, raw=offer1_raw, source_ref="o1.json", captured_at=CAPTURE)
    o2 = build_offer(snapshot, ordinal=1, raw=offer2_raw, source_ref="o2.json", captured_at=CAPTURE)

    pack = build_evidence_pack(snapshot, ordinal=1, offers=[o1, o2], as_of="2026-10-30T01:00:00Z")
    assert pack.offers["assessment_status"] == "CONFLICT"
    assert "PRICE" in pack.offers["conflict_fields"]
    assert pack.offers["active_offer"] is None


def test_variant_isolation_and_conflict(tmp_path):
    snapshot = _sample_snapshot(tmp_path)
    # Build offer for variant 2 (green-m)
    offer_v2_raw = canonical_bytes({
        "schema_version": "ProductOfferSource.v1",
        "observed_at": "2026-10-30T00:00:00Z",
        "expires_at": "2026-11-30T00:00:00Z",
        "price_amount": "29.99",
        "currency": "USD",
        "stock_available": True,
        "commission_rate": "0.05",
        "commission_amount": "1.50",
        "commission_currency": "USD",
    })
    offer_v2 = build_offer(snapshot, ordinal=2, raw=offer_v2_raw, source_ref="o2.json", captured_at=CAPTURE)

    # Attempt to attach variant 2 offer to variant 1 pack
    with pytest.raises(ProductError, match="OFFER_VARIANT_MISMATCH"):
        build_evidence_pack(snapshot, ordinal=1, offers=[offer_v2])


def test_missing_advisory_caveats_rejected(tmp_path):
    snapshot = _sample_snapshot(tmp_path)
    pack = build_evidence_pack(snapshot, ordinal=1)
    tampered = pack.to_dict()
    tampered["advisory_caveats"] = ["Only one caveat"]
    tampered["pack_id"] = content_id("ProductEvidencePack", tampered)
    with pytest.raises(ProductError, match="ADVISORY_CAVEATS_INCOMPLETE"):
        ProductEvidencePack(tampered)
