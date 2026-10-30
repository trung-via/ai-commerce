"""A13-E03 Offline-only AffiliateReleaseEvidencePack.v1, CLI, and demo tests.

Verifies:
- Self-contained Draft 2020-12 schema and contract mirror at contracts/AffiliateReleaseEvidencePack.v1.schema.json
- Integration of C08 media inspection, C09 claim/rights review, D06 placement evidence,
  A12 product/variant snapshot/identity/claim/offer/rights evidence, A07 capability, and A08 manual handoff
- Revalidation of account/product/variant/channel/market/surface/link scope
- Revalidation of required commercial disclosure
- Revalidation of active date, expiry, and revocation across release, offer, rights, placement, and capability
- Revalidation of hostile URLs, SSRF, credentials, tampered hashes, and resealed receipts
- Revalidation of missing (UNKNOWN) vs conflicting/restricted/failed (HOLD) evidence
- Advisory-only state outputs READY_FOR_MANUAL_REVIEW / HOLD / UNKNOWN; never live approval, active link,
  POSTED/PUBLIC, sales, OAuth, network/API calls, revenue, or auto-publishing
- Deterministic CLI and demo execution
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from datetime import datetime
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess

from jsonschema import Draft202012Validator
import pytest

from ai_commerce.affiliate_release import (
    AffiliatePlacementEvidence,
    AffiliateReleaseError,
    AffiliateReleaseEvidencePack,
    AffiliateReleaseRequest,
    AssetUsage,
    ClaimPlacement,
    ClaimRightsReleaseReview,
    ClaimRightsReviewRequest,
    EvidenceRef,
    MediaReleaseInspection,
    PlacementDisclosure,
    ReleaseAsset,
    affiliate_release_pack_schema,
    asset_from_e01,
    build_affiliate_release_pack,
    evaluate_affiliate_placement,
    evaluate_affiliate_release_pack,
    evidence_pack_schema,
    inspect_media_release,
    read_affiliate_release_pack,
    review_affiliate_release_pack,
    review_claim_rights,
    run_demo,
    write_affiliate_release_pack,
)
from ai_commerce.affiliate_release.cli import main as cli_main
from ai_commerce.affiliate_release.demo import _deterministic_c08_receipt
from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.distribution.assisted import export_package
from ai_commerce.distribution.evaluator import evaluate as evaluate_capability
from ai_commerce.distribution.model import validate_snapshot as validate_capability_snapshot
from ai_commerce.distribution.registry import CapabilityRegistry
from ai_commerce.e2e import run as e01_run
from ai_commerce.product import (
    build_claim,
    build_evidence_pack,
    build_offer,
    build_rights,
    canonical_bytes,
    prepare_bytes,
    review_claim,
    review_rights,
)

CAPTURE = "2026-10-30T01:00:00Z"
REVIEWED = "2026-10-30T02:00:00Z"
AS_OF = "2026-10-30T03:00:00Z"
VALID_FROM = "2026-10-30T02:00:00Z"
USE_FROM = "2026-10-30T04:00:00Z"
USE_UNTIL = "2026-11-01T04:00:00Z"
VALID_UNTIL = "2026-11-30T02:00:00Z"
EXPIRES = "2027-01-01T00:00:00Z"

RELEASE_BYTES = b"offline A13-E03 fixture video asset bytes; no live network or publish"
RELEASE_SHA = sha256(RELEASE_BYTES).hexdigest()

ACCOUNT = {"namespace": "tiktok_open_id", "id": "acc-creator-123"}
APP = {"namespace": "tiktok_client_key", "id": "app-tiktok-shop"}
LINK_URL = "https://shop.tiktok.com/view/product/123456?affiliate_id=acc-creator-123"
DISCLOSURE_STMT = "Includes paid affiliate link; commission earned on qualifying orders. #ad #affiliate"


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch):
    """Enforce strict offline execution; no socket or network access allowed."""
    def forbidden(*args, **kwargs):
        pytest.fail("A13-E03 attempted forbidden network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def make_snapshot(*, synthetic: bool = False, variant: str = "blue", product: str = "prod-1"):
    raw = canonical_bytes({
        "schema_version": "ManualProductListings.v1",
        "rows": [
            {
                "marketplace": "fixture-market",
                "seller_key": "seller-1",
                "listing_key": "listing-1",
                "product_key": product,
                "variant_key": variant,
                "title": "Fixture lamp",
                "price_amount": 29.99,
                "currency": "USD",
                "variant_attributes": {"color": variant},
            }
        ],
    })
    return prepare_bytes(
        raw,
        file_format="json",
        source_ref="fixture-listings",
        captured_at=CAPTURE,
        synthetic=synthetic,
    )


def make_claim(
    snap,
    *,
    reviewed: bool = True,
    statement: str = "Fixture lamp uses 5 watts",
    value: str = "5",
    role: str = "SUPPORTING",
    key: str = "power",
):
    raw = canonical_bytes({
        "schema_version": "ProductClaimSource.v1",
        "claim_key": key,
        "category": "PERFORMANCE",
        "statement": statement,
        "value": value,
        "unit": "W",
        "origin": "SELLER",
        "role": role,
        "observed_at": CAPTURE,
    })
    rec = build_claim(snap, ordinal=1, raw=raw, source_ref="fixture-claim", captured_at=CAPTURE)
    if reviewed:
        rec = review_claim(
            rec,
            proof_raw=b"independent fixture lab report bytes",
            reviewer_ref="fixture-reviewer",
            producer_ref="fixture-lab",
            method="wattmeter test",
            reviewed_at=REVIEWED,
            evidence_ref="fixture-report",
            evidence_kind="INDEPENDENT_MEASUREMENT",
        )
    return rec


def make_offer(
    snap,
    *,
    observed_at: str = CAPTURE,
    expires_at: str = EXPIRES,
    price: str = "29.99",
    currency: str = "USD",
    stock_available: bool = True,
):
    raw = canonical_bytes({
        "schema_version": "ProductOfferSource.v1",
        "observed_at": observed_at,
        "expires_at": expires_at,
        "price_amount": price,
        "currency": currency,
        "stock_available": stock_available,
        "commission_rate": "0.10",
        "commission_amount": "3.00",
        "commission_currency": currency,
    })
    return build_offer(snap, ordinal=1, raw=raw, source_ref="fixture-offer", captured_at=observed_at)


def make_rights(
    snap,
    *,
    reviewed: bool = True,
    declared: str = "GRANTED",
    expiry: str = EXPIRES,
    territories: tuple[str, ...] = ("VN",),
    channels: tuple[str, ...] = ("social",),
    purposes: tuple[str, ...] = ("COMMERCIAL_USE",),
    media_type: str = "VIDEO",
    asset_ref: str = "release.mp4",
    asset_raw: bytes = RELEASE_BYTES,
):
    asset_hash = sha256(asset_raw).hexdigest()
    scope = {
        "territories": list(territories),
        "media_types": [media_type],
        "channels": list(channels),
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_until": expiry,
    }
    raw = canonical_bytes({
        "schema_version": "ProductAssetRightsSource.v1",
        "asset": {
            "asset_ref": asset_ref,
            "content_sha256": asset_hash,
            "media_type": media_type,
            "origin": "CREATOR_SUPPLIED",
            "origin_ref": "fixture-origin",
            "copyright_holder_ref": "fixture-holder",
            "claimant_ref": "fixture-operator",
            "license_label": "Commercial license",
        },
        "permissions": [
            {
                "purpose": p,
                "declared_status": declared,
                "license_ref": "fixture-license",
                "scope": scope,
            }
            for p in purposes
        ],
    })
    rec = build_rights(snap, ordinal=1, raw=raw, source_ref="fixture-rights", captured_at=CAPTURE)
    if reviewed:
        for purpose in purposes:
            inst = canonical_bytes({
                "schema_version": "ProductAssetLicenseEvidence.v1",
                "instrument_kind": "HOLDER_PERMISSION",
                "asset_sha256": asset_hash,
                "copyright_holder_ref": "fixture-holder",
                "issuer_ref": "fixture-holder",
                "authority_basis": "COPYRIGHT_HOLDER",
                "license_ref": "fixture-license",
                "issued_at": "2026-01-01T00:00:00Z",
                "purpose": purpose,
                "scope": scope,
                "conclusion": "ALLOWS_EXACT_SCOPED_USE",
            })
            rec = review_rights(
                rec,
                purpose=purpose,
                license_raw=inst,
                reviewer_ref="fixture-rights-reviewer",
                reviewed_at=REVIEWED,
                method="fixture instrument review",
                evidence_ref="fixture-license-evidence",
            )
    return rec


def make_status_notice(
    rights_rec,
    *,
    state: str = "NOT_REVOKED",
    effective_at: str = REVIEWED,
    observed_at: str = REVIEWED,
    valid_until: str = EXPIRES,
    synthetic: bool = False,
    purpose: str = "COMMERCIAL_USE",
):
    return canonical_bytes({
        "schema_version": "ReleaseRightsStatusEvidence.v1",
        "rights_id": rights_rec.rights_id,
        "asset_sha256": rights_rec.to_dict()["asset"]["content_sha256"],
        "license_ref": "fixture-license",
        "purpose": purpose,
        "state": state,
        "issuer_ref": "fixture-holder",
        "source_ref": "fixture-holder-status",
        "observed_at": observed_at,
        "effective_at": effective_at,
        "valid_until": valid_until,
        "synthetic": synthetic,
    })


def make_capability_snapshot(
    *,
    platform: str = "TIKTOK",
    account_ref: dict[str, str] = ACCOUNT,
    app_ref: dict[str, str] = APP,
    market: str = "VN",
    surface: str = "VIDEO",
    action: str = "MANUAL_POST",
    state: str = "ALLOWED",
    observed_at: str = CAPTURE,
    recorded_at: str = REVIEWED,
    expires_at: str = EXPIRES,
    revoked_at: str | None = None,
):
    return validate_capability_snapshot({
        "schema_version": "ChannelCapabilitySnapshot.v1",
        "snapshot_id": "ccs_fixture_001",
        "profile_version": "v1.0",
        "platform": platform,
        "account_ref": account_ref,
        "app_ref": app_ref,
        "market": market,
        "surface": surface,
        "action": action,
        "state": state,
        "source_mode": "OBSERVED",
        "observed_at": observed_at,
        "recorded_at": recorded_at,
        "expires_at": expires_at,
        "revoked_at": revoked_at,
        "scope_claims": [
            {"name": "user.info.basic", "status": "GRANTED"},
            {"name": "video.publish", "status": "GRANTED"},
            {"name": "product.anchor", "status": "GRANTED"},
        ],
        "constraints": {"commerce_region": market},
        "evidence_refs": [
            {
                "evidence_id": "ev_auth_01",
                "kind": "ACCOUNT_AUTHORIZATION",
                "integrity": "HASHED",
                "sha256": "a" * 64,
                "scope": "TARGET",
                "subject": {"account_ref": account_ref, "app_ref": app_ref},
                "captured_at": observed_at,
                "uri": "offline://fixtures/account_authorization.json",
            },
            {
                "evidence_id": "ev_scope_01",
                "kind": "SCOPE_OBSERVATION",
                "integrity": "HASHED",
                "sha256": "b" * 64,
                "scope": "TARGET",
                "subject": {"account_ref": account_ref, "app_ref": app_ref},
                "captured_at": observed_at,
                "uri": "offline://fixtures/scope_observation.json",
            },
        ],
    })


def standard_bundle(**overrides):
    snap = make_snapshot()
    c = make_claim(snap, reviewed=True)
    o = make_offer(snap)
    r = make_rights(snap, reviewed=True)
    pack = build_evidence_pack(
        snap,
        claims=[c],
        offers=[o],
        rights=[r],
        as_of=AS_OF,
        generated_at=REVIEWED,
    )
    subject = pack.subject
    c08 = _deterministic_c08_receipt(RELEASE_SHA, len(RELEASE_BYTES))
    notice = make_status_notice(r)
    c09_req = ClaimRightsReviewRequest(
        product_id=subject["product_id"],
        listing_id=subject["listing_id"],
        variant_id=subject["variant_id"],
        release_sha256=RELEASE_SHA,
        territory="VN",
        channel="social",
        as_of=AS_OF,
        use_from=USE_FROM,
        use_until=USE_UNTIL,
    )
    c09_claims = (ClaimPlacement("caption", c.to_dict()["claim"]["statement"], "FACTUAL", (c.claim_id,)),)
    c09_assets = (AssetUsage("release.mp4", RELEASE_SHA, "VIDEO", ("COMMERCIAL_USE",)),)
    c09 = review_claim_rights(
        pack,
        request=c09_req,
        snapshot=snap,
        claims=c09_claims,
        assets=c09_assets,
        claim_evidence=(c,),
        rights_evidence=(r,),
        asset_bytes={"release.mp4": RELEASE_BYTES},
        rights_status_evidence=(notice,),
        media_inspection=c08,
    )
    cap_snap = make_capability_snapshot()
    disc = PlacementDisclosure(
        statement=DISCLOSURE_STMT,
        mode="CLEAR_AND_CONSPICUOUS",
        tags=("#ad", "#affiliate"),
    )
    d06 = evaluate_affiliate_placement(
        account_ref=ACCOUNT,
        app_ref=APP,
        market="VN",
        surface="VIDEO",
        product_id=subject["product_id"],
        variant_id=subject["variant_id"],
        link_url=LINK_URL,
        as_of=AS_OF,
        valid_from=VALID_FROM,
        valid_until=VALID_UNTIL,
        disclosure=disc,
        capability_snapshot=cap_snap,
        evidence_pack=pack,
        offer_observation=o,
        source_kind="A12_PRODUCT_PACK_BOUND",
    )
    base = {
        "platform": "TIKTOK",
        "account_ref": ACCOUNT,
        "app_ref": APP,
        "market": "VN",
        "territory": "VN",
        "surface": "VIDEO",
        "channel": "social",
        "product_id": subject["product_id"],
        "listing_id": subject["listing_id"],
        "variant_id": subject["variant_id"],
        "release_sha256": RELEASE_SHA,
        "link_url": LINK_URL,
        "as_of": AS_OF,
        "valid_from": VALID_FROM,
        "use_from": USE_FROM,
        "use_until": USE_UNTIL,
        "valid_until": VALID_UNTIL,
        "disclosure": disc,
        "media_inspection": c08,
        "release_asset_bytes": RELEASE_BYTES,
        "claim_rights_review": c09,
        "claims": c09_claims,
        "assets": c09_assets,
        "claim_evidence": (c,),
        "rights_evidence": (r,),
        "asset_bytes": {"release.mp4": RELEASE_BYTES},
        "rights_status_evidence": (notice,),
        "placement_evidence": d06,
        "product_pack": pack,
        "snapshot": snap,
        "offer_observation": o,
        "capability_snapshot": cap_snap,
    }
    base.update(overrides)
    return base


# ============================================================================
# 1. Schema, Contract Mirror & Advisory Invariants
# ============================================================================


def test_schema_mirror_and_validation():
    schema = affiliate_release_pack_schema()
    assert schema == evidence_pack_schema()
    Draft202012Validator.check_schema(schema)

    contract_path = (
        Path(__file__).resolve().parents[1]
        / "contracts/AffiliateReleaseEvidencePack.v1.schema.json"
    )
    assert contract_path.is_file(), "contracts/AffiliateReleaseEvidencePack.v1.schema.json must exist"
    contract_json = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract_json == schema

    # Detached copy check
    mutated = affiliate_release_pack_schema()
    mutated["properties"]["state"]["enum"].clear()
    assert affiliate_release_pack_schema()["properties"]["state"]["enum"] == [
        "READY_FOR_MANUAL_REVIEW",
        "HOLD",
        "UNKNOWN",
    ]


def test_ready_for_manual_review_and_protective_non_claim_invariants():
    inputs = standard_bundle()
    pack = build_affiliate_release_pack(**inputs)
    record = pack.to_dict()

    Draft202012Validator(affiliate_release_pack_schema()).validate(record)
    assert pack.state == "READY_FOR_MANUAL_REVIEW"
    assert pack.decision == "READY_FOR_MANUAL_REVIEW"
    assert pack.status == "READY_FOR_MANUAL_REVIEW"
    assert pack.pack_id.startswith("a13-pack-")
    assert "READY_FOR_HUMAN_MANUAL_REVIEW_ONLY" in pack.reason_codes

    # Protective non-claim constants
    assert record["advisory_only"] is True
    assert record["manual_review_required"] is True
    assert record["live_provider_approval"] == "UNKNOWN"
    assert record["live_link_verified"] is False
    assert record["affiliate_link_status"] == "UNKNOWN_OFFLINE_ONLY"
    assert record["account_eligibility"] == "UNKNOWN"
    assert record["link_activation"] == "UNKNOWN"
    assert record["platform_approval"] == "UNKNOWN"
    assert record["legal_rights_status"] == "UNKNOWN"
    assert record["publication_status"] == "NOT_ATTEMPTED"
    assert record["auto_publishing"] == "NEVER_PERFORMED"
    assert record["oauth_performed"] is False
    assert record["network_calls_performed"] is False
    assert record["sales_claims_allowed"] is False
    assert record["earnings_claims_allowed"] is False
    assert record["sales_earnings_claims"] == "NOT_PERMITTED"
    assert record["revenue_status"] == "NOT_CLAIMED"

    # Revalidation flags
    assert pack.revalidation == {
        "scope_consistent": True,
        "disclosure_compliant": True,
        "temporal_active": True,
        "url_safe": True,
        "hashes_intact": True,
        "evidence_complete": True,
        "conflicts_detected": False,
    }


def test_auto_build_c09_and_capability_registry_integration(tmp_path: Path):
    inputs = standard_bundle(claim_rights_review=None)
    cap_db = tmp_path / "cap.sqlite"
    reg = CapabilityRegistry(cap_db)
    reg.import_snapshot(inputs["capability_snapshot"])
    inputs["capability_registry"] = reg

    pack = evaluate_affiliate_release_pack(**inputs)
    assert pack.state == "READY_FOR_MANUAL_REVIEW"
    assert pack.components["claim_rights_review"]["status"] == "VERIFIED_EVIDENCE"
    assert pack.components["capability_evidence"]["decision"] == "ALLOW"


def test_real_media_e01_and_a08_distribution_handoff_integration(tmp_path: Path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("Real media/A06/A08 integration requires ffmpeg and ffprobe")

    # Create a tiny valid PNG for A06 E01 run
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 48, 32, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + b"\x10\x20\x30" * 48 for _ in range(32))
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw_scanlines))
        + chunk(b"IEND", b"")
    )
    img_path = tmp_path / "still.png"
    img_path.write_bytes(png_bytes)

    e01_dir = tmp_path / "e01_out"
    e01_res = e01_run("Create organic serum showcase", [img_path], e01_dir, width=48, height=32, fps=10, frames=5)
    assert e01_res["state"] == "DELIVERED"
    video_bytes = (e01_dir / "video.mp4").read_bytes()
    video_sha = sha256_bytes(video_bytes)

    # C08 inspection from E01
    manifest_sha = sha256_bytes((e01_dir / "e01-manifest.json").read_bytes())
    rel_asset = asset_from_e01(e01_dir, expected_manifest_sha256=manifest_sha)
    c08_receipt = inspect_media_release(e01_dir, [rel_asset])
    assert c08_receipt.to_dict()["status"] == "PASS"

    # A12 product pack bound to the rendered video_sha
    snap = make_snapshot()
    c = make_claim(snap, reviewed=True)
    o = make_offer(snap)
    r = make_rights(snap, reviewed=True, asset_ref="video.mp4", asset_raw=video_bytes)
    pack = build_evidence_pack(snap, claims=[c], offers=[o], rights=[r], as_of=AS_OF, generated_at=REVIEWED)
    subj = pack.subject

    # A08 distribution package export
    a08_dir = tmp_path / "a08_pkg"
    a08_pkg = export_package(
        e01_dir,
        a08_dir,
        platform="TIKTOK",
        surface="VIDEO",
        account="tiktok_open_id:acc-creator-123",
        market="VN",
        caption="Organic serum showcase",
        title="Serum Showcase",
        hashtags=["ad", "affiliate"],
        product_ref=subj["product_id"],
        disclosure=DISCLOSURE_STMT,
    )
    assert a08_pkg["state"] == "EXPORTED_FOR_MANUAL_HANDOFF"

    cap_snap = make_capability_snapshot(surface="VIDEO", action="MANUAL_POST")
    disc = PlacementDisclosure(statement=DISCLOSURE_STMT, mode="CLEAR_AND_CONSPICUOUS", tags=("#ad", "#affiliate"))
    d06 = evaluate_affiliate_placement(
        account_ref=ACCOUNT,
        app_ref=APP,
        market="VN",
        surface="VIDEO",
        product_id=subj["product_id"],
        variant_id=subj["variant_id"],
        link_url=LINK_URL,
        as_of=AS_OF,
        valid_from=VALID_FROM,
        valid_until=VALID_UNTIL,
        disclosure=disc,
        capability_snapshot=cap_snap,
        evidence_pack=pack,
        offer_observation=o,
        source_kind="A12_PRODUCT_PACK_BOUND",
    )

    notice = make_status_notice(r)
    e03_pack = build_affiliate_release_pack(
        platform="TIKTOK",
        account_ref=ACCOUNT,
        app_ref=APP,
        market="VN",
        territory="VN",
        surface="VIDEO",
        channel="social",
        product_id=subj["product_id"],
        listing_id=subj["listing_id"],
        variant_id=subj["variant_id"],
        release_sha256=video_sha,
        link_url=LINK_URL,
        as_of=AS_OF,
        valid_from=VALID_FROM,
        use_from=USE_FROM,
        use_until=USE_UNTIL,
        valid_until=VALID_UNTIL,
        disclosure=disc,
        media_inspection=c08_receipt,
        release_asset_bytes=video_bytes,
        claims=(ClaimPlacement("caption", c.to_dict()["claim"]["statement"], "FACTUAL", (c.claim_id,)),),
        assets=(AssetUsage("video.mp4", video_sha, "VIDEO", ("COMMERCIAL_USE",)),),
        claim_evidence=(c,),
        rights_evidence=(r,),
        asset_bytes={"video.mp4": video_bytes},
        rights_status_evidence=(notice,),
        placement_evidence=d06,
        product_pack=pack,
        snapshot=snap,
        offer_observation=o,
        capability_snapshot=cap_snap,
        distribution_package=a08_dir,
    )
    assert e03_pack.state == "READY_FOR_MANUAL_REVIEW"
    assert e03_pack.components["distribution_handoff"]["present"] is True
    assert e03_pack.components["distribution_handoff"]["state"] == "EXPORTED_FOR_MANUAL_HANDOFF"
    assert "DISTRIBUTION_HANDOFF_VERIFIED" in e03_pack.reason_codes


# ============================================================================
# 2. Scope Revalidation Tests (Account / Product / Variant / Market / Surface / Link / Hash)
# ============================================================================


def test_cross_account_mismatch_causes_hold():
    # URL query param mismatch
    pack1 = build_affiliate_release_pack(
        **standard_bundle(link_url="https://shop.tiktok.com/view/product/123456?affiliate_id=acc-other-999")
    )
    assert pack1.state == "HOLD"
    assert "CROSS_ACCOUNT_MISMATCH" in pack1.reason_codes
    assert pack1.revalidation["scope_consistent"] is False

    # Capability snapshot account mismatch
    bad_cap = make_capability_snapshot(account_ref={"namespace": "tiktok_open_id", "id": "acc-wrong-777"})
    pack2 = build_affiliate_release_pack(**standard_bundle(capability_snapshot=bad_cap))
    assert pack2.state == "HOLD"
    assert "CROSS_ACCOUNT_MISMATCH" in pack2.reason_codes


def test_app_platform_market_surface_channel_mismatches_cause_hold():
    # App mismatch
    bad_app_cap = make_capability_snapshot(app_ref={"namespace": "tiktok_client_key", "id": "app-other"})
    p_app = build_affiliate_release_pack(**standard_bundle(capability_snapshot=bad_app_cap))
    assert p_app.state == "HOLD"
    assert "APP_MISMATCH" in p_app.reason_codes

    # Market vs territory mismatch
    p_mkt = build_affiliate_release_pack(**standard_bundle(market="VN", territory="US"))
    assert p_mkt.state == "HOLD"
    assert "MARKET_TERRITORY_MISMATCH" in p_mkt.reason_codes
    assert "MARKET_MISMATCH" in p_mkt.reason_codes

    # Surface mismatch between request and D06/capability
    p_surf = build_affiliate_release_pack(**standard_bundle(surface="BIO"))
    assert p_surf.state == "HOLD"
    assert "SURFACE_MISMATCH" in p_surf.reason_codes

    # Channel mismatch between request and C09
    p_chan = build_affiliate_release_pack(**standard_bundle(channel="email-newsletter"))
    assert p_chan.state == "HOLD"
    assert "CHANNEL_MISMATCH" in p_chan.reason_codes


def test_product_listing_variant_release_sha_mismatches_cause_hold():
    # Product mismatch
    p_prod = build_affiliate_release_pack(**standard_bundle(product_id="a12-product-" + "9" * 64))
    assert p_prod.state == "HOLD"
    assert "PRODUCT_MISMATCH" in p_prod.reason_codes

    # Listing mismatch
    p_list = build_affiliate_release_pack(**standard_bundle(listing_id="a12-listing-" + "8" * 64))
    assert p_list.state == "HOLD"
    assert "LISTING_MISMATCH" in p_list.reason_codes

    # Variant mismatch
    p_var = build_affiliate_release_pack(**standard_bundle(variant_id="a12-variant-" + "7" * 64))
    assert p_var.state == "HOLD"
    assert "VARIANT_MISMATCH" in p_var.reason_codes

    # Release SHA-256 mismatch
    p_sha = build_affiliate_release_pack(**standard_bundle(release_sha256="e" * 64))
    assert p_sha.state == "HOLD"
    assert "RELEASE_SHA256_MISMATCH" in p_sha.reason_codes


# ============================================================================
# 3. Disclosure Revalidation Tests
# ============================================================================


def test_missing_or_non_compliant_disclosure_causes_hold():
    p_none = build_affiliate_release_pack(**standard_bundle(disclosure=None))
    assert p_none.state == "HOLD"
    assert "MISSING_REQUIRED_DISCLOSURE" in p_none.reason_codes
    assert p_none.revalidation["disclosure_compliant"] is False

    bad_disc = PlacementDisclosure(
        statement="Check out my favorite everyday product!",
        mode="CLEAR_AND_CONSPICUOUS",
        tags=("#lifestyle",),
    )
    p_bad = build_affiliate_release_pack(**standard_bundle(disclosure=bad_disc))
    assert p_bad.state == "HOLD"
    assert "MISSING_REQUIRED_DISCLOSURE" in p_bad.reason_codes

    # Valid disclosure with different text than D06 placement_evidence
    diff_disc = PlacementDisclosure(
        statement="Sponsored promotion with affiliate commission. #ad",
        mode="CLEAR_AND_CONSPICUOUS",
        tags=("#ad",),
    )
    p_diff = build_affiliate_release_pack(**standard_bundle(disclosure=diff_disc))
    assert p_diff.state == "HOLD"
    assert "DISCLOSURE_MISMATCH" in p_diff.reason_codes


# ============================================================================
# 4. Active Date, Expiry & Revocation Revalidation Tests
# ============================================================================


def test_temporal_expiry_revocation_future_and_invalid_window_cause_hold():
    # Expired at as_of
    p_exp = build_affiliate_release_pack(
        **standard_bundle(
            as_of="2026-12-01T00:00:00Z",
            valid_from="2026-10-01T00:00:00Z",
            use_from="2026-10-02T00:00:00Z",
            use_until="2026-11-01T00:00:00Z",
            valid_until="2026-11-30T00:00:00Z",
        )
    )
    assert p_exp.state == "HOLD"
    assert "PLACEMENT_EXPIRED" in p_exp.reason_codes
    assert p_exp.revalidation["temporal_active"] is False

    # Revoked placement
    p_rev = build_affiliate_release_pack(**standard_bundle(revoked_at="2026-10-30T02:30:00Z"))
    assert p_rev.state == "HOLD"
    assert "PLACEMENT_REVOKED" in p_rev.reason_codes
    assert p_rev.revalidation["temporal_active"] is False

    # Future valid_from (not yet effective)
    p_fut = build_affiliate_release_pack(
        **standard_bundle(
            as_of=AS_OF,
            valid_from="2026-11-01T00:00:00Z",
            use_from="2026-11-01T00:00:00Z",
            use_until="2026-11-15T00:00:00Z",
            valid_until="2026-11-30T00:00:00Z",
        )
    )
    assert p_fut.state == "HOLD"
    assert "PLACEMENT_NOT_YET_EFFECTIVE" in p_fut.reason_codes

    # Invalid window (use_from >= use_until)
    p_inv = build_affiliate_release_pack(
        **standard_bundle(
            use_from="2026-11-10T00:00:00Z",
            use_until="2026-11-01T00:00:00Z",
        )
    )
    assert p_inv.state == "HOLD"
    assert "INVALID_TEMPORAL_WINDOW" in p_inv.reason_codes


def test_revoked_rights_expired_offer_and_revoked_capability_cause_hold():
    # Revoked C09 rights status notice
    snap = make_snapshot()
    c = make_claim(snap, reviewed=True)
    o = make_offer(snap)
    r = make_rights(snap, reviewed=True)
    pack = build_evidence_pack(snap, claims=[c], offers=[o], rights=[r], as_of=AS_OF, generated_at=REVIEWED)
    rev_notice = make_status_notice(r, state="REVOKED", effective_at="2026-10-30T02:30:00Z", valid_until=None)
    p_rights_rev = build_affiliate_release_pack(
        **standard_bundle(
            claim_rights_review=None,
            product_pack=pack,
            snapshot=snap,
            rights_evidence=(r,),
            rights_status_evidence=(rev_notice,),
        )
    )
    assert p_rights_rev.state == "HOLD"
    assert "RIGHTS_REVOKED" in p_rights_rev.reason_codes
    assert "CLAIM_RIGHTS_RESTRICTED" in p_rights_rev.reason_codes

    # Out of stock offer
    o_oos = make_offer(snap, stock_available=False)
    pack_oos = build_evidence_pack(snap, claims=[c], offers=[o_oos], rights=[r], as_of=AS_OF, generated_at=REVIEWED)
    p_oos = build_affiliate_release_pack(**standard_bundle(product_pack=pack_oos, offer_observation=o_oos))
    assert p_oos.state == "HOLD"
    assert "OFFER_OUT_OF_STOCK" in p_oos.reason_codes

    # Revoked capability snapshot
    cap_rev = make_capability_snapshot(state="REVOKED", revoked_at="2026-10-30T02:15:00Z")
    p_cap_rev = build_affiliate_release_pack(**standard_bundle(capability_snapshot=cap_rev))
    assert p_cap_rev.state == "HOLD"
    assert "CAPABILITY_DENIED_OR_REVOKED" in p_cap_rev.reason_codes


# ============================================================================
# 5. Hostile URL, SSRF & Credential Rejection Tests
# ============================================================================


@pytest.mark.parametrize(
    "hostile_url, expected_code",
    [
        ("javascript:alert(1)", "HOSTILE_URL_SCHEME"),
        ("file:///etc/passwd", "HOSTILE_URL_SCHEME"),
        ("http://169.254.169.254/latest/meta-data", "INTERNAL_OR_RESERVED_TARGET"),
        ("http://127.0.0.1/admin", "INTERNAL_OR_RESERVED_TARGET"),
        ("http://localhost/internal", "INTERNAL_NETWORK_TARGET"),
        ("https://user:pass@shop.tiktok.com/item", "CREDENTIALS_IN_URL"),
        ("https://shop.tiktok.com:22/item", "SUSPICIOUS_PORT"),
        ("https://shop.tiktok.com/view/../../secret", "INJECTION_PATTERN_DETECTED"),
        ("https://shop.tiktok.com/view?q=<script>evil()</script>", "INJECTION_PATTERN_DETECTED"),
    ],
)
def test_hostile_url_causes_hold(hostile_url: str, expected_code: str):
    pack = build_affiliate_release_pack(**standard_bundle(link_url=hostile_url))
    assert pack.state == "HOLD"
    assert pack.revalidation["url_safe"] is False
    assert "HOSTILE_URL" in pack.reason_codes
    assert expected_code in pack.reason_codes


def test_credential_material_causes_hold():
    pack = build_affiliate_release_pack(
        **standard_bundle(link_url="https://shop.tiktok.com/item?access_token=secret_oauth_token_123")
    )
    assert pack.state == "HOLD"
    assert "CREDENTIAL_MATERIAL_NOT_ALLOWED" in pack.reason_codes


# ============================================================================
# 6. Tampered Hash, Resealing & Integrity Detection Tests
# ============================================================================


def test_tampered_upstream_components_and_evidence_refs_cause_hold():
    # Tampered C08 bytes
    p_c08 = build_affiliate_release_pack(**standard_bundle(media_inspection=b'{"tampered": true}'))
    assert p_c08.state == "HOLD"
    assert "MEDIA_INSPECTION_TAMPERED" in p_c08.reason_codes
    assert "TAMPERED_HASH" in p_c08.reason_codes
    assert p_c08.revalidation["hashes_intact"] is False

    # Tampered C09 bytes
    p_c09 = build_affiliate_release_pack(**standard_bundle(claim_rights_review=b'{"tampered": true}'))
    assert p_c09.state == "HOLD"
    assert "CLAIM_RIGHTS_REVIEW_TAMPERED" in p_c09.reason_codes
    assert "TAMPERED_HASH" in p_c09.reason_codes

    # Tampered D06 bytes
    p_d06 = build_affiliate_release_pack(**standard_bundle(placement_evidence=b'{"tampered": true}'))
    assert p_d06.state == "HOLD"
    assert "PLACEMENT_EVIDENCE_TAMPERED" in p_d06.reason_codes
    assert "TAMPERED_HASH" in p_d06.reason_codes

    # Tampered A12 product pack bytes
    p_a12 = build_affiliate_release_pack(**standard_bundle(product_pack=b'{"tampered": true}'))
    assert p_a12.state == "HOLD"
    assert "PRODUCT_PACK_TAMPERED" in p_a12.reason_codes
    assert "TAMPERED_HASH" in p_a12.reason_codes

    # Tampered release asset bytes
    p_bytes = build_affiliate_release_pack(**standard_bundle(release_asset_bytes=b"corrupted bytes"))
    assert p_bytes.state == "HOLD"
    assert "ASSET_HASH_MISMATCH" in p_bytes.reason_codes
    assert "TAMPERED_HASH" in p_bytes.reason_codes

    # Tampered EvidenceRef raw_bytes
    bad_ref = EvidenceRef(
        evidence_id="ev_tamper",
        kind="CUSTOM_EVIDENCE",
        record_sha256="0" * 64,
        raw_bytes=b"actual content",
    )
    p_ref = build_affiliate_release_pack(**standard_bundle(evidence_refs=[bad_ref]))
    assert p_ref.state == "HOLD"
    assert "TAMPERED_HASH" in p_ref.reason_codes


def test_from_bytes_rejects_tampered_and_resealed_packs():
    inputs = standard_bundle()
    pack = build_affiliate_release_pack(**inputs)
    raw = pack.to_bytes()

    # Exact replay succeeds
    restored = AffiliateReleaseEvidencePack.from_bytes(raw, expected_id=pack.pack_id, **inputs)
    assert restored.to_bytes() == raw

    # Wrong expected_id rejected
    with pytest.raises(AffiliateReleaseError, match="PACK_ID_MISMATCH"):
        AffiliateReleaseEvidencePack.from_bytes(raw, expected_id="a13-pack-" + "0" * 64)

    # Corrupted record_sha256 rejected
    bad_sha = pack.to_dict()
    bad_sha["record_sha256"] = "f" * 64
    with pytest.raises(AffiliateReleaseError, match="PACK_DIGEST_MISMATCH"):
        AffiliateReleaseEvidencePack.from_bytes(canonical_bytes(bad_sha))

    # Resealed HOLD pack claiming READY_FOR_MANUAL_REVIEW rejected
    hold_pack = build_affiliate_release_pack(**standard_bundle(link_url="http://127.0.0.1/admin"))
    resealed = hold_pack.to_dict()
    resealed["state"] = "READY_FOR_MANUAL_REVIEW"
    resealed["decision"] = "READY_FOR_MANUAL_REVIEW"
    resealed["status"] = "READY_FOR_MANUAL_REVIEW"
    body_no_id = {k: v for k, v in resealed.items() if k not in ("pack_id", "record_sha256")}
    resealed["pack_id"] = "a13-pack-" + sha256_bytes(canonical_bytes(body_no_id))
    body_no_sha = {k: v for k, v in resealed.items() if k != "record_sha256"}
    resealed["record_sha256"] = sha256_bytes(canonical_bytes(body_no_sha))

    with pytest.raises(AffiliateReleaseError, match="PACK_CONSISTENCY_INVALID"):
        AffiliateReleaseEvidencePack.from_bytes(canonical_bytes(resealed))

    # Immutability check
    with pytest.raises(FrozenInstanceError):
        pack._encoded = b"mutated"


# ============================================================================
# 7. Missing (UNKNOWN) vs Conflicting (HOLD) Evidence Tests
# ============================================================================


def test_missing_components_yield_unknown():
    # Missing C08 media inspection
    p_no_c08 = build_affiliate_release_pack(**standard_bundle(media_inspection=None))
    assert p_no_c08.state == "UNKNOWN"
    assert "MEDIA_INSPECTION_MISSING" in p_no_c08.reason_codes
    assert p_no_c08.revalidation["evidence_complete"] is False

    # Missing C09 claim/rights review (and no auto-build inputs)
    p_no_c09 = build_affiliate_release_pack(
        **standard_bundle(
            claim_rights_review=None,
            claims=None,
            assets=None,
            claim_evidence=None,
            rights_evidence=None,
            asset_bytes=None,
            rights_status_evidence=None,
        )
    )
    assert p_no_c09.state == "UNKNOWN"
    assert "CLAIM_RIGHTS_REVIEW_MISSING" in p_no_c09.reason_codes

    # Missing D06 placement evidence
    p_no_d06 = build_affiliate_release_pack(**standard_bundle(placement_evidence=None))
    assert p_no_d06.state == "UNKNOWN"
    assert "PLACEMENT_EVIDENCE_MISSING" in p_no_d06.reason_codes

    # Missing A12 product pack
    p_no_a12 = build_affiliate_release_pack(
        **standard_bundle(
            product_pack=None,
            claim_rights_review=None,
            claims=None,
            assets=None,
            claim_evidence=None,
            rights_evidence=None,
            asset_bytes=None,
            rights_status_evidence=None,
        )
    )
    assert p_no_a12.state == "UNKNOWN"
    assert "PRODUCT_EVIDENCE_PACK_MISSING" in p_no_a12.reason_codes

    # Missing A07 capability
    p_no_cap = build_affiliate_release_pack(
        **standard_bundle(capability_snapshot=None, capability_decision=None)
    )
    assert p_no_cap.state == "UNKNOWN"
    assert "CAPABILITY_EVIDENCE_MISSING" in p_no_cap.reason_codes


def test_conflicting_claims_and_offers_yield_hold():
    snap = make_snapshot()
    c1 = make_claim(snap, reviewed=True, statement="Lamp uses 5 watts", value="5", key="power")
    c2 = make_claim(
        snap,
        reviewed=False,
        statement="Lamp uses 12 watts",
        value="12",
        role="COUNTEREVIDENCE",
        key="power",
    )
    o = make_offer(snap)
    r = make_rights(snap, reviewed=True)
    pack = build_evidence_pack(snap, claims=[c1, c2], offers=[o], rights=[r], as_of=AS_OF, generated_at=REVIEWED)

    p_conflict = build_affiliate_release_pack(
        **standard_bundle(
            claim_rights_review=None,
            product_pack=pack,
            snapshot=snap,
            claims=(ClaimPlacement("caption", "Lamp uses 5 watts", "FACTUAL", (c1.claim_id,)),),
            claim_evidence=(c1, c2),
            rights_evidence=(r,),
            rights_status_evidence=(make_status_notice(r),),
        )
    )
    assert p_conflict.state == "HOLD"
    assert p_conflict.revalidation["conflicts_detected"] is True
    assert "CONFLICTING_EVIDENCE" in p_conflict.reason_codes
    assert "CLAIM_CONFLICT_OR_COUNTEREVIDENCE" in p_conflict.reason_codes


# ============================================================================
# 8. Deterministic Demo & CLI Tests
# ============================================================================


def test_deterministic_demo_bit_exact_and_reproducible(tmp_path: Path):
    dir1 = tmp_path / "demo_run_1"
    dir2 = tmp_path / "demo_run_2"

    sum1 = run_demo(dir1)
    sum2 = run_demo(dir2)

    assert sum1 == sum2
    assert sum1["schema_version"] == "A13AffiliateReleaseDemo.v1"
    assert sum1["ready_state"] == "READY_FOR_MANUAL_REVIEW"
    assert sum1["hold_hostile_state"] == "HOLD"
    assert sum1["hold_revoked_state"] == "HOLD"
    assert sum1["hold_mismatch_state"] == "HOLD"
    assert sum1["unknown_missing_state"] == "UNKNOWN"
    assert sum1["reload_identical"] is True
    assert (dir1 / "release_evidence_pack.json").read_bytes() == (
        dir2 / "release_evidence_pack.json"
    ).read_bytes()

    with pytest.raises(AffiliateReleaseError, match="DEMO_OUTPUT_ALREADY_EXISTS"):
        run_demo(dir1)


def test_cli_schema_demo_verify_and_evaluate(tmp_path: Path):
    def invoke(*args: str, expected_code: int = 0) -> dict:
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            code = cli_main(list(args))
        assert code == expected_code, f"Expected {expected_code}, got {code}, stderr={err_buf.getvalue()}"
        payload = out_buf.getvalue().strip() if expected_code == 0 else err_buf.getvalue().strip()
        return json.loads(payload)

    # 1. schema command
    schema_out = invoke("schema", "--contract", "pack")
    assert schema_out["title"] == "AffiliateReleaseEvidencePack.v1"

    # 2. demo command
    demo_dir = tmp_path / "cli_demo"
    demo_out = invoke("demo", "--output", str(demo_dir))
    assert demo_out["ready_state"] == "READY_FOR_MANUAL_REVIEW"

    # 3. verify command on generated pack
    pack_file = demo_dir / "release_evidence_pack.json"
    ver_out = invoke("verify", "--file", str(pack_file), "--expected-id", demo_out["ready_pack_id"])
    assert ver_out["pack_id"] == demo_out["ready_pack_id"]
    assert ver_out["state"] == "READY_FOR_MANUAL_REVIEW"

    # 4. verify with wrong expected-id fails with code 73
    err_out = invoke("verify", "--file", str(pack_file), "--expected-id", "a13-pack-" + "0" * 64, expected_code=73)
    assert err_out["error"] == "PACK_ID_MISMATCH"

    # 5. evaluate command from files
    eval_out_file = tmp_path / "evaluated_pack.json"
    eval_res = invoke(
        "evaluate",
        "--platform", "TIKTOK",
        "--account", "tiktok_open_id:acc-creator-demo-001",
        "--app", "tiktok_client_key:app-tiktok-shop-demo",
        "--market", "VN",
        "--territory", "VN",
        "--surface", "VIDEO",
        "--channel", "social",
        "--product-id", ver_out["release_scope"]["product_id"],
        "--listing-id", ver_out["release_scope"]["listing_id"],
        "--variant-id", ver_out["release_scope"]["variant_id"],
        "--release-sha256", ver_out["release_scope"]["release_sha256"],
        "--link-url", ver_out["release_scope"]["link_url"],
        "--as-of", AS_OF,
        "--valid-from", VALID_FROM,
        "--use-from", USE_FROM,
        "--use-until", USE_UNTIL,
        "--valid-until", VALID_UNTIL,
        "--disclosure", ver_out["disclosure"]["statement"],
        "--media-inspection", str(demo_dir / "media_inspection.json"),
        "--claim-rights-review", str(demo_dir / "claim_rights_review.json"),
        "--placement-evidence", str(demo_dir / "placement_evidence.json"),
        "--product-pack", str(demo_dir / "product_pack.json"),
        "--snapshot", str(demo_dir / "snapshot.json"),
        "--snapshot-raw", str(demo_dir / "listing_raw.json"),
        "--offer-observation", str(demo_dir / "offer_active.json"),
        "--capability-decision", str(demo_dir / "capability_decision.json"),
        "--capability-snapshot", str(demo_dir / "capability_snapshot.json"),
        "--output", str(eval_out_file),
    )
    assert eval_res["state"] == "READY_FOR_MANUAL_REVIEW"
    assert eval_out_file.is_file()


def test_offer_conflict_between_observation_and_pack_causes_hold():
    snap = make_snapshot()
    o_alt = make_offer(snap, price="49.99")
    pack = build_affiliate_release_pack(**standard_bundle(offer_observation=o_alt))
    assert pack.state == "HOLD"
    assert pack.revalidation["conflicts_detected"] is True
    assert "OFFER_CONFLICT" in pack.reason_codes
    assert "CONFLICTING_EVIDENCE" in pack.reason_codes


def test_distribution_package_mismatch_and_tamper_cause_hold():
    inputs = standard_bundle()
    subj = inputs["product_pack"].subject

    def seal_a08(overrides: dict) -> dict:
        v_sha = overrides.get("video_sha256", RELEASE_SHA)
        body = {
            "schema_version": "DistributionPackage.v1",
            "source_run_id": "e01-" + "1" * 24,
            "source_manifest_sha256": "2" * 64,
            "video_sha256": v_sha,
            "platform": "TIKTOK",
            "surface": "VIDEO",
            "account_ref": ACCOUNT,
            "market": "VN",
            "app_ref": APP,
            "title": "Serum Showcase",
            "caption": "Organic serum showcase",
            "hashtags": ["ad", "affiliate"],
            "product_ref": subj["product_id"],
            "disclosure": DISCLOSURE_STMT,
            "capability": {
                "decision": "UNKNOWN",
                "verification": "OFFLINE_RECORDED_NOT_LIVE_VERIFIED",
                "reason_codes": ["NOT_EVALUATED_NO_REGISTRY"],
            },
            "files": {
                "video.mp4": v_sha,
                "caption.txt": "3" * 64,
                "hashtags.txt": "4" * 64,
                "title.txt": "5" * 64,
                "publish-instructions.md": "6" * 64,
            },
            "state": "EXPORTED_FOR_MANUAL_HANDOFF",
            "publication_status": "NOT_ATTEMPTED",
        }
        body.update(overrides)
        body["package_id"] = "a08-" + sha256_bytes(canonical_bytes(body))[:24]
        body["package_sha256"] = sha256_bytes(canonical_bytes(body))
        return body

    valid_a08 = seal_a08({})
    p_ok = build_affiliate_release_pack(**standard_bundle(distribution_package=valid_a08))
    assert p_ok.state == "READY_FOR_MANUAL_REVIEW"
    assert p_ok.components["distribution_handoff"]["present"] is True
    assert "DISTRIBUTION_HANDOFF_VERIFIED" in p_ok.reason_codes

    # Video SHA mismatch in distribution package
    mismatch_a08 = seal_a08({"video_sha256": "9" * 64})
    p_mis = build_affiliate_release_pack(**standard_bundle(distribution_package=mismatch_a08))
    assert p_mis.state == "HOLD"
    assert "RELEASE_SHA256_MISMATCH" in p_mis.reason_codes

    # Tampered distribution package bytes
    p_tamp = build_affiliate_release_pack(**standard_bundle(distribution_package=b'{"not_a08": true}'))
    assert p_tamp.state == "HOLD"
    assert "DISTRIBUTION_PACKAGE_TAMPERED" in p_tamp.reason_codes
    assert "TAMPERED_HASH" in p_tamp.reason_codes


def test_release_request_object_and_file_read_write_roundtrip(tmp_path: Path):
    inputs = standard_bundle()
    req = AffiliateReleaseRequest(
        platform=inputs.pop("platform"),
        account_ref=inputs.pop("account_ref"),
        app_ref=inputs.pop("app_ref"),
        market=inputs.pop("market"),
        territory=inputs.pop("territory"),
        surface=inputs.pop("surface"),
        channel=inputs.pop("channel"),
        product_id=inputs.pop("product_id"),
        listing_id=inputs.pop("listing_id"),
        variant_id=inputs.pop("variant_id"),
        release_sha256=inputs.pop("release_sha256"),
        link_url=inputs.pop("link_url"),
        as_of=inputs.pop("as_of"),
        valid_from=inputs.pop("valid_from"),
        use_from=inputs.pop("use_from"),
        use_until=inputs.pop("use_until"),
        valid_until=inputs.pop("valid_until"),
    )
    pack = review_affiliate_release_pack(request=req, **inputs)
    assert pack.state == "READY_FOR_MANUAL_REVIEW"

    dest = tmp_path / "pack.json"
    write_affiliate_release_pack(dest, pack)
    with pytest.raises(AffiliateReleaseError, match="PACK_OUTPUT_ALREADY_EXISTS"):
        write_affiliate_release_pack(dest, pack, overwrite=False)
    write_affiliate_release_pack(dest, pack, overwrite=True)

    loaded = read_affiliate_release_pack(dest, expected_id=pack.pack_id)
    assert loaded.to_bytes() == pack.to_bytes()


def test_capability_expired_and_unknown_decisions():
    cap_exp = make_capability_snapshot(state="EXPIRED", expires_at="2026-10-30T01:30:00Z")
    p_exp = build_affiliate_release_pack(**standard_bundle(capability_snapshot=cap_exp))
    assert p_exp.state == "HOLD"
    assert "CAPABILITY_EXPIRED" in p_exp.reason_codes
    assert p_exp.revalidation["temporal_active"] is False

    cap_unk = make_capability_snapshot(state="UNKNOWN")
    d06_unk = evaluate_affiliate_placement(
        account_ref=ACCOUNT,
        app_ref=APP,
        market="VN",
        surface="VIDEO",
        product_id=p_exp.release_scope["product_id"],
        variant_id=p_exp.release_scope["variant_id"],
        link_url=LINK_URL,
        as_of=AS_OF,
        valid_from=VALID_FROM,
        valid_until=VALID_UNTIL,
        disclosure=PlacementDisclosure(statement=DISCLOSURE_STMT, mode="CLEAR_AND_CONSPICUOUS", tags=("#ad",)),
        capability_snapshot=cap_unk,
        source_kind="OFFLINE_EVIDENCE_REPLAY",
    )
    p_unk = build_affiliate_release_pack(
        **standard_bundle(capability_snapshot=cap_unk, placement_evidence=d06_unk)
    )
    assert p_unk.state == "UNKNOWN"
    assert "CAPABILITY_STATE_UNKNOWN" in p_unk.reason_codes

