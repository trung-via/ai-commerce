"""Deterministic offline A13-E03 demonstration and golden evidence pack production.

Integrates A12 product evidence, C08 media inspection, C09 claim/rights review,
A07 channel capability, and D06 affiliate placement evidence into an advisory
AffiliateReleaseEvidencePack.v1.
Strictly offline; never contacts external providers or claims live approval.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.distribution.evaluator import evaluate as evaluate_capability
from ai_commerce.distribution.model import validate_snapshot as validate_capability_snapshot
from ai_commerce.distribution.registry import CapabilityRegistry
from ai_commerce.product import (
    build_claim,
    build_evidence_pack,
    build_offer,
    build_rights,
    canonical_bytes,
    prepare_bytes,
    review_claim,
    review_rights,
    write_evidence_pack,
)

from .claim_rights import AssetUsage, ClaimPlacement, ClaimRightsReviewRequest, review_claim_rights
from .evidence_pack import (
    AffiliateReleaseError,
    AffiliateReleaseEvidencePack,
    AffiliateReleaseRequest,
    build_affiliate_release_pack,
    read_affiliate_release_pack,
    write_affiliate_release_pack,
)
from .inspection import MediaReleaseInspection
from .placement import PlacementDisclosure, PlacementTemporal, evaluate_affiliate_placement

CAPTURE = "2026-10-30T01:00:00Z"
REVIEWED = "2026-10-30T02:00:00Z"
AS_OF = "2026-10-30T03:00:00Z"
VALID_FROM = "2026-10-30T02:00:00Z"
USE_FROM = "2026-10-30T04:00:00Z"
USE_UNTIL = "2026-11-01T04:00:00Z"
VALID_UNTIL = "2026-11-30T02:00:00Z"
EXPIRES = "2027-01-01T00:00:00Z"

DEMO_RELEASE_BYTES = b"offline deterministic A13-E03 release asset bytes; advisory manual review only"
DEMO_RELEASE_SHA = sha256_bytes(DEMO_RELEASE_BYTES)

ACCOUNT = {"namespace": "tiktok_open_id", "id": "acc-creator-demo-001"}
APP = {"namespace": "tiktok_client_key", "id": "app-tiktok-shop-demo"}
LINK_URL = "https://shop.tiktok.com/view/product/987654?affiliate_id=acc-creator-demo-001"
DISCLOSURE_TEXT = "Paid affiliate link; commission may be earned on qualifying orders. #ad #affiliate"


def _deterministic_c08_receipt(release_sha256: str, size_bytes: int) -> MediaReleaseInspection:
    """Construct a deterministic, self-verified MediaReleaseInspection.v1 receipt."""
    payload = {
        "schema_version": "MediaReleaseInspection.v1",
        "inspector_version": "a13-c08/1.0",
        "scope": "COMMERCIAL_PLACEMENT_TECHNICAL_INSPECTION_ONLY",
        "status": "PASS",
        "limits": {
            "max_asset_bytes": 268435456,
            "max_total_bytes": 536870912,
            "max_assets": 16,
            "tool_timeout_seconds": 30,
            "max_tool_output_bytes": 1048576,
        },
        "diagnostics_requested": {"black": False, "silence": False},
        "tools": {"ffprobe": "ffprobe-deterministic-fixture/1.0", "ffmpeg": "ffmpeg-deterministic-fixture/1.0"},
        "assets": [
            {
                "asset_ref": "release.mp4",
                "expected_sha256": release_sha256,
                "mime_type": "video/mp4",
                "observed_sha256": release_sha256,
                "size_bytes": size_bytes,
                "status": "PASS",
                "reason_codes": [],
                "full_decode": True,
                "facts": {
                    "container": "mp4",
                    "format_names": ["mov", "mp4", "m4a", "3gp", "3g2", "mj2"],
                    "duration_seconds": "1/2",
                    "streams": [
                        {
                            "index": 0,
                            "kind": "video",
                            "codec": "h264",
                            "width": 48,
                            "height": 32,
                            "fps": "10",
                            "duration_seconds": "1/2",
                            "decoded_frames": 5,
                            "sample_rate": None,
                            "channels": None,
                        }
                    ],
                },
                "diagnostics": {
                    "black": {
                        "status": "NOT_REQUESTED",
                        "parameters": {
                            "minimum_duration_seconds": "1/10",
                            "pixel_threshold": "1/10",
                            "picture_black_ratio": "49/50",
                        },
                        "segments": [],
                        "reason_codes": [],
                    },
                    "silence": {
                        "status": "NOT_REQUESTED",
                        "parameters": {"minimum_duration_seconds": "1/10", "noise_db": -50},
                        "segments": [],
                        "reason_codes": [],
                    },
                },
                "source": {"kind": "LOCAL_EXPECTED_HASH", "custody_verification": "NOT_PERFORMED"},
            }
        ],
        "rights_status": "UNKNOWN",
        "account_eligibility": "UNKNOWN",
        "link_activation": "UNKNOWN",
        "publication_status": "NOT_ATTEMPTED",
    }
    payload["record_sha256"] = sha256_bytes(canonical_bytes(payload))
    return MediaReleaseInspection.from_bytes(canonical_bytes(payload))


def run_demo(output_dir: Path | str) -> dict[str, Any]:
    """Run deterministic offline A13-E03 demonstration and write artifacts to output_dir."""
    directory = Path(output_dir).resolve()
    if directory.exists():
        raise AffiliateReleaseError("DEMO_OUTPUT_ALREADY_EXISTS")
    directory.mkdir(parents=True, exist_ok=False)

    # 1. A12 Product Evidence Foundation (snapshot, claim, offer, rights, pack)
    raw_listing = {
        "schema_version": "ManualProductListings.v1",
        "rows": [
            {
                "marketplace": "fixture-market",
                "seller_key": "merchant-demo",
                "listing_key": "item-serum-01",
                "product_key": "prod-serum-c",
                "variant_key": "30ml-dropper",
                "sku": "SKU-SERUM-30ML",
                "title": "LumiDrop Vitamin C Brightening Serum 30ml",
                "description": "Stabilized 10% L-Ascorbic Acid daily facial serum.",
                "listing_url": "https://marketplace.mock/items/item-serum-01",
                "observed_at": "2026-10-30T00:00:00Z",
                "price_amount": 24.50,
                "currency": "USD",
                "variant_attributes": {"volume": "30ml", "packaging": "dropper"},
            }
        ],
    }
    raw_listing_bytes = canonical_bytes(raw_listing)
    (directory / "listing_raw.json").write_bytes(raw_listing_bytes)

    snapshot = prepare_bytes(
        raw_listing_bytes,
        file_format="json",
        source_ref="fixtures/listing_raw.json",
        captured_at=CAPTURE,
        synthetic=False,
    )
    (directory / "snapshot.json").write_bytes(snapshot.to_bytes())

    claim_statement = "Dermatologically tested 10% L-Ascorbic Acid formula"
    claim_raw = canonical_bytes({
        "schema_version": "ProductClaimSource.v1",
        "claim_key": "dermatology-tested-10pct",
        "category": "COMPOSITION",
        "statement": claim_statement,
        "value": "10%",
        "unit": "w/w",
        "origin": "SELLER",
        "role": "SUPPORTING",
        "observed_at": CAPTURE,
    })
    proof_raw = canonical_bytes({
        "report_id": "LAB-2026-SERUM-01",
        "method": "HPLC assay and patch test",
        "conclusion": "Verified 10% L-Ascorbic Acid",
    })
    claim_unreviewed = build_claim(
        snapshot,
        ordinal=1,
        raw=claim_raw,
        source_ref="fixtures/claim_source.json",
        captured_at=CAPTURE,
    )
    claim_verified = review_claim(
        claim_unreviewed,
        proof_raw=proof_raw,
        reviewer_ref="auditor-alias",
        producer_ref="lab-alias",
        method="HPLC assay verification",
        reviewed_at=REVIEWED,
        evidence_ref="reports/LAB-2026-SERUM-01.json",
        evidence_kind="INDEPENDENT_TEST_REPORT",
    )
    (directory / "claim_verified.json").write_bytes(claim_verified.to_bytes())

    offer_raw = canonical_bytes({
        "schema_version": "ProductOfferSource.v1",
        "observed_at": CAPTURE,
        "expires_at": EXPIRES,
        "price_amount": "24.50",
        "currency": "USD",
        "stock_available": True,
        "commission_rate": "0.10",
        "commission_amount": "2.45",
        "commission_currency": "USD",
    })
    offer_active = build_offer(
        snapshot,
        ordinal=1,
        raw=offer_raw,
        source_ref="fixtures/offer_active.json",
        captured_at=CAPTURE,
    )
    (directory / "offer_active.json").write_bytes(offer_active.to_bytes())

    rights_scope = {
        "territories": ["VN"],
        "media_types": ["VIDEO"],
        "channels": ["social"],
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_until": EXPIRES,
    }
    rights_raw = canonical_bytes({
        "schema_version": "ProductAssetRightsSource.v1",
        "asset": {
            "asset_ref": "release.mp4",
            "content_sha256": DEMO_RELEASE_SHA,
            "media_type": "VIDEO",
            "origin": "CREATOR_SUPPLIED",
            "origin_ref": "creator-studio",
            "copyright_holder_ref": "holder-alias",
            "claimant_ref": "merchant-alias",
            "license_label": "Commercial Brand License",
        },
        "permissions": [
            {
                "purpose": "COMMERCIAL_USE",
                "declared_status": "GRANTED",
                "license_ref": "license-commercial-01",
                "scope": rights_scope,
            }
        ],
    })
    license_instrument = canonical_bytes({
        "schema_version": "ProductAssetLicenseEvidence.v1",
        "instrument_kind": "HOLDER_PERMISSION",
        "asset_sha256": DEMO_RELEASE_SHA,
        "copyright_holder_ref": "holder-alias",
        "issuer_ref": "holder-alias",
        "authority_basis": "COPYRIGHT_HOLDER",
        "license_ref": "license-commercial-01",
        "issued_at": "2026-01-01T00:00:00Z",
        "purpose": "COMMERCIAL_USE",
        "scope": rights_scope,
        "conclusion": "ALLOWS_EXACT_SCOPED_USE",
    })
    rights_unreviewed = build_rights(
        snapshot,
        ordinal=1,
        raw=rights_raw,
        source_ref="fixtures/rights_source.json",
        captured_at=CAPTURE,
    )
    rights_reviewed = review_rights(
        rights_unreviewed,
        purpose="COMMERCIAL_USE",
        license_raw=license_instrument,
        reviewer_ref="rights-reviewer",
        reviewed_at=REVIEWED,
        method="Local holder license match",
        evidence_ref="licenses/license-commercial-01.json",
    )
    (directory / "rights_reviewed.json").write_bytes(rights_reviewed.to_bytes())

    product_pack = build_evidence_pack(
        snapshot,
        claims=[claim_verified],
        offers=[offer_active],
        rights=[rights_reviewed],
        as_of=AS_OF,
        generated_at=REVIEWED,
    )
    write_evidence_pack(directory / "product_pack.json", product_pack)

    subject = product_pack.subject
    product_id = subject["product_id"]
    listing_id = subject["listing_id"]
    variant_id = subject["variant_id"]

    # 2. C08 Media Release Inspection
    media_inspection = _deterministic_c08_receipt(DEMO_RELEASE_SHA, len(DEMO_RELEASE_BYTES))
    (directory / "media_inspection.json").write_bytes(media_inspection.to_bytes())

    # 3. C09 Claim & Rights Release Review
    status_notice = canonical_bytes({
        "schema_version": "ReleaseRightsStatusEvidence.v1",
        "rights_id": rights_reviewed.rights_id,
        "asset_sha256": DEMO_RELEASE_SHA,
        "license_ref": "license-commercial-01",
        "purpose": "COMMERCIAL_USE",
        "state": "NOT_REVOKED",
        "issuer_ref": "holder-alias",
        "source_ref": "holder-status-check",
        "observed_at": REVIEWED,
        "effective_at": REVIEWED,
        "valid_until": EXPIRES,
        "synthetic": False,
    })
    c09_request = ClaimRightsReviewRequest(
        product_id=product_id,
        listing_id=listing_id,
        variant_id=variant_id,
        release_sha256=DEMO_RELEASE_SHA,
        territory="VN",
        channel="social",
        as_of=AS_OF,
        use_from=USE_FROM,
        use_until=USE_UNTIL,
    )
    c09_claims = (ClaimPlacement("caption", claim_statement, "FACTUAL", (claim_verified.claim_id,)),)
    c09_assets = (AssetUsage("release.mp4", DEMO_RELEASE_SHA, "VIDEO", ("COMMERCIAL_USE",)),)
    claim_rights_review = review_claim_rights(
        product_pack,
        request=c09_request,
        snapshot=snapshot,
        claims=c09_claims,
        assets=c09_assets,
        claim_evidence=(claim_verified,),
        rights_evidence=(rights_reviewed,),
        asset_bytes={"release.mp4": DEMO_RELEASE_BYTES},
        rights_status_evidence=(status_notice,),
        media_inspection=media_inspection,
    )
    (directory / "claim_rights_review.json").write_bytes(claim_rights_review.to_bytes())

    # 4. A07 Channel Capability Snapshot & Decision
    cap_snapshot = validate_capability_snapshot({
        "schema_version": "ChannelCapabilitySnapshot.v1",
        "snapshot_id": "ccs_demo_tiktok_anchor_001",
        "profile_version": "v1.0",
        "platform": "TIKTOK",
        "account_ref": ACCOUNT,
        "app_ref": APP,
        "market": "VN",
        "surface": "VIDEO",
        "action": "MANUAL_POST",
        "state": "ALLOWED",
        "source_mode": "OBSERVED",
        "observed_at": CAPTURE,
        "recorded_at": REVIEWED,
        "expires_at": EXPIRES,
        "revoked_at": None,
        "scope_claims": [
            {"name": "user.info.basic", "status": "GRANTED"},
            {"name": "video.publish", "status": "GRANTED"},
            {"name": "product.anchor", "status": "GRANTED"},
        ],
        "constraints": {"commerce_region": "VN"},
        "evidence_refs": [
            {
                "evidence_id": "ev_acc_auth_001",
                "kind": "ACCOUNT_AUTHORIZATION",
                "integrity": "HASHED",
                "sha256": "a" * 64,
                "scope": "TARGET",
                "subject": {"account_ref": ACCOUNT, "app_ref": APP},
                "captured_at": CAPTURE,
                "uri": "offline://fixtures/account_authorization.json",
            },
            {
                "evidence_id": "ev_scope_obs_001",
                "kind": "SCOPE_OBSERVATION",
                "integrity": "HASHED",
                "sha256": "b" * 64,
                "scope": "TARGET",
                "subject": {"account_ref": ACCOUNT, "app_ref": APP},
                "captured_at": CAPTURE,
                "uri": "offline://fixtures/scope_observation.json",
            },
        ],
    })
    (directory / "capability_snapshot.json").write_bytes(canonical_bytes(cap_snapshot))

    cap_reg = CapabilityRegistry(directory / "capability.sqlite")
    cap_reg.import_snapshot(cap_snapshot)
    cap_decision = evaluate_capability(
        {
            "schema_version": "ChannelCapabilityRequest.v1",
            "platform": "TIKTOK",
            "account_ref": ACCOUNT,
            "app_ref": APP,
            "market": "VN",
            "surface": "VIDEO",
            "action": "MANUAL_POST",
        },
        cap_reg,
        at=
        __import__("datetime").datetime.fromisoformat(AS_OF.replace("Z", "+00:00")),
    )
    (directory / "capability_decision.json").write_bytes(canonical_bytes(cap_decision))

    # 5. D06 Affiliate Placement Evidence
    disclosure = PlacementDisclosure(
        statement=DISCLOSURE_TEXT,
        mode="CLEAR_AND_CONSPICUOUS",
        tags=("#ad", "#affiliate"),
    )
    placement_evidence = evaluate_affiliate_placement(
        account_ref=ACCOUNT,
        app_ref=APP,
        market="VN",
        surface="VIDEO",
        product_id=product_id,
        variant_id=variant_id,
        link_url=LINK_URL,
        as_of=AS_OF,
        valid_from=VALID_FROM,
        valid_until=VALID_UNTIL,
        disclosure=disclosure,
        capability_snapshot=cap_snapshot,
        evidence_pack=product_pack,
        offer_observation=offer_active,
        source_kind="A12_PRODUCT_PACK_BOUND",
    )
    (directory / "placement_evidence.json").write_bytes(placement_evidence.to_bytes())

    # 6. A13-E03 AffiliateReleaseEvidencePack (READY_FOR_MANUAL_REVIEW)
    release_request = AffiliateReleaseRequest(
        platform="TIKTOK",
        account_ref=ACCOUNT,
        app_ref=APP,
        market="VN",
        territory="VN",
        surface="VIDEO",
        channel="social",
        product_id=product_id,
        listing_id=listing_id,
        variant_id=variant_id,
        release_sha256=DEMO_RELEASE_SHA,
        link_url=LINK_URL,
        as_of=AS_OF,
        valid_from=VALID_FROM,
        use_from=USE_FROM,
        use_until=USE_UNTIL,
        valid_until=VALID_UNTIL,
    )
    ready_inputs = dict(
        request=release_request,
        disclosure=disclosure,
        media_inspection=media_inspection,
        release_asset_bytes=DEMO_RELEASE_BYTES,
        claim_rights_review=claim_rights_review,
        claims=c09_claims,
        assets=c09_assets,
        claim_evidence=(claim_verified,),
        rights_evidence=(rights_reviewed,),
        asset_bytes={"release.mp4": DEMO_RELEASE_BYTES},
        rights_status_evidence=(status_notice,),
        placement_evidence=placement_evidence,
        product_pack=product_pack,
        snapshot=snapshot,
        offer_observation=offer_active,
        capability_decision=cap_decision,
        capability_snapshot=cap_snapshot,
    )
    ready_pack = build_affiliate_release_pack(**ready_inputs)
    ready_path = write_affiliate_release_pack(directory / "release_evidence_pack.json", ready_pack)
    reloaded = read_affiliate_release_pack(ready_path, expected_id=ready_pack.pack_id, **ready_inputs)
    if reloaded.to_bytes() != ready_pack.to_bytes():
        raise AffiliateReleaseError("DEMO_RELOAD_MISMATCH")

    # 7. Negative / Hold & Unknown Cases
    hold_hostile = build_affiliate_release_pack(
        **{**ready_inputs, "link_url": "http://169.254.169.254/latest/meta-data"}
    )
    write_affiliate_release_pack(directory / "hold_hostile_url_pack.json", hold_hostile)

    hold_revoked = build_affiliate_release_pack(
        **{**ready_inputs, "revoked_at": "2026-10-30T02:30:00Z"}
    )
    write_affiliate_release_pack(directory / "hold_revoked_pack.json", hold_revoked)

    hold_mismatch = build_affiliate_release_pack(
        **{
            **ready_inputs,
            "link_url": "https://shop.tiktok.com/view/product/987654?affiliate_id=acc-other-999",
        }
    )
    write_affiliate_release_pack(directory / "hold_scope_mismatch_pack.json", hold_mismatch)

    unknown_missing = build_affiliate_release_pack(
        request=release_request,
        disclosure=disclosure,
        product_pack=product_pack,
        snapshot=snapshot,
        media_inspection=None,
        claim_rights_review=None,
        placement_evidence=None,
        capability_decision=None,
        capability_snapshot=None,
    )
    write_affiliate_release_pack(directory / "unknown_missing_evidence_pack.json", unknown_missing)

    # 8. Tamper check on resealed pack
    tampered_dict = hold_hostile.to_dict()
    tampered_dict["state"] = "READY_FOR_MANUAL_REVIEW"
    tampered_dict["decision"] = "READY_FOR_MANUAL_REVIEW"
    tampered_dict["status"] = "READY_FOR_MANUAL_REVIEW"
    body_no_id = {k: v for k, v in tampered_dict.items() if k not in ("pack_id", "record_sha256")}
    tampered_dict["pack_id"] = "a13-pack-" + sha256_bytes(canonical_bytes(body_no_id))
    body_no_sha = {k: v for k, v in tampered_dict.items() if k != "record_sha256"}
    tampered_dict["record_sha256"] = sha256_bytes(canonical_bytes(body_no_sha))
    try:
        AffiliateReleaseEvidencePack.from_bytes(canonical_bytes(tampered_dict))
    except AffiliateReleaseError as exc:
        tamper_rejected_code = exc.code
    else:
        raise AffiliateReleaseError("DEMO_TAMPER_ACCEPTED")

    summary = {
        "schema_version": "A13AffiliateReleaseDemo.v1",
        "advisory_only": True,
        "manual_review_required": True,
        "product_pack_id": product_pack.pack_id,
        "media_inspection_sha256": media_inspection.to_dict()["record_sha256"],
        "claim_rights_review_sha256": claim_rights_review.to_dict()["record_sha256"],
        "placement_evidence_sha256": placement_evidence.record_sha256,
        "ready_pack_id": ready_pack.pack_id,
        "ready_pack_sha256": ready_pack.record_sha256,
        "ready_state": ready_pack.state,
        "hold_hostile_state": hold_hostile.state,
        "hold_revoked_state": hold_revoked.state,
        "hold_mismatch_state": hold_mismatch.state,
        "unknown_missing_state": unknown_missing.state,
        "tamper_rejected_code": tamper_rejected_code,
        "reload_identical": True,
        "live_provider_approval": "UNKNOWN",
        "live_link_verified": False,
        "publication_status": "NOT_ATTEMPTED",
        "sales_claims_allowed": False,
        "earnings_claims_allowed": False,
    }
    (directory / "summary.json").write_bytes(canonical_bytes(summary))
    return summary
