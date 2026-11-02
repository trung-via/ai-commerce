"""E04 integration against real A06/A12/A13/A07/A08/L04 APIs, entirely offline.

All product, rights, channel and attribution records here are test fixtures,
never live account entitlements, provider orders, receipts or settlement proof.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys

from jsonschema import Draft202012Validator
import pytest

from ai_commerce.affiliate_attribution import build_attribution_evidence
from ai_commerce.affiliate_commerce import (
    AffiliateCommerceRun, CommerceError, CommerceRunHistory,
    affiliate_commerce_run_schema, build_affiliate_commerce_run,
)
from ai_commerce.affiliate_commerce.service import _derive, blob
from ai_commerce.affiliate_release import (
    AssetUsage, ClaimPlacement, PlacementDisclosure, asset_from_e01,
    build_affiliate_release_pack, evaluate_affiliate_placement, inspect_media_release,
)
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.distribution.assisted import export_package
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.e2e import run as creative_run
from ai_commerce.product import build_evidence_pack

# Self-contained native fixtures adapted from pinned main A06/A13 tests.
# Keeping them here avoids importing test modules and their unrelated CLI consumers.
import struct
import zlib
from ai_commerce.product import build_claim, build_offer, build_rights, prepare_bytes, review_claim, review_rights
from ai_commerce.distribution.model import validate_snapshot as validate_capability_snapshot

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

def png(red: int) -> bytes:
    width = height = 16
    row = b"\x00" + bytes((red, 30, 170)) * width
    pixels = zlib.compress(row * height)
    def chunk(kind, content):
        return (struct.pack(">I", len(content)) + kind + content
                + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", pixels) + chunk(b"IEND", b""))


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("E04 must not access a network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("e04-native")
    still = root / "still.png"
    still.write_bytes(png(80))
    creative = root / "creative"
    creative_run("Offline fictional product showcase", [still], creative, frames=2)
    manifest = json.loads((creative / "e01-manifest.json").read_bytes())
    video = (creative / "video.mp4").read_bytes()
    snapshot = make_snapshot()
    claim = make_claim(snapshot, reviewed=True)
    offer = make_offer(snapshot)
    rights = make_rights(snapshot, reviewed=True, asset_ref="video.mp4", asset_raw=video)
    product = build_evidence_pack(snapshot, claims=[claim], offers=[offer], rights=[rights],
                                 as_of=AS_OF, generated_at=REVIEWED)
    cap = make_capability_snapshot(surface="VIDEO", action="MANUAL_POST")
    distribution = root / "distribution"
    export_package(creative, distribution, platform="TIKTOK", surface="VIDEO",
        account="tiktok_open_id:acc-creator-123", market="VN", app_ref=APP,
        caption="Offline fictional product showcase", hashtags=["ad", "affiliate"],
        product_ref=product.subject["product_id"], disclosure=DISCLOSURE_STMT)
    disclosure = PlacementDisclosure(statement=DISCLOSURE_STMT,
        mode="CLEAR_AND_CONSPICUOUS", tags=("#ad", "#affiliate"))
    placement = evaluate_affiliate_placement(account_ref=ACCOUNT, app_ref=APP,
        market="VN", surface="VIDEO", product_id=product.subject["product_id"],
        variant_id=product.subject["variant_id"], link_url=LINK_URL, as_of=AS_OF,
        valid_from=VALID_FROM, valid_until=VALID_UNTIL, disclosure=disclosure,
        capability_snapshot=cap, evidence_pack=product, offer_observation=offer,
        source_kind="A12_PRODUCT_PACK_BOUND")
    media = inspect_media_release(creative, [asset_from_e01(creative,
        expected_manifest_sha256=sha256((creative / "e01-manifest.json").read_bytes()).hexdigest())])
    release_inputs = dict(platform="TIKTOK", account_ref=ACCOUNT, app_ref=APP, market="VN",
        territory="VN", surface="VIDEO", channel="social", product_id=product.subject["product_id"],
        listing_id=product.subject["listing_id"], variant_id=product.subject["variant_id"],
        release_sha256=manifest["output_sha256"], link_url=LINK_URL, as_of=AS_OF,
        valid_from=VALID_FROM, use_from=USE_FROM, use_until=USE_UNTIL, valid_until=VALID_UNTIL,
        disclosure=disclosure, media_inspection=media, release_asset_bytes=video,
        claims=(ClaimPlacement("caption", claim.to_dict()["claim"]["statement"], "FACTUAL", (claim.claim_id,)),),
        assets=(AssetUsage("video.mp4", manifest["output_sha256"], "VIDEO", ("COMMERCIAL_USE",)),),
        claim_evidence=(claim,), rights_evidence=(rights,), asset_bytes={"video.mp4": video},
        rights_status_evidence=(make_status_notice(rights),), placement_evidence=placement,
        product_pack=product, snapshot=snapshot, offer_observation=offer,
        capability_snapshot=cap, distribution_package=distribution)
    release = build_affiliate_release_pack(**release_inputs)
    assert release.state == "READY_FOR_MANUAL_REVIEW"
    scope = dict(provider_ref="offline-provider-fixture", platform="TIKTOK", account_ref=ACCOUNT,
        app_ref=APP, market="VN", surface="VIDEO", channel="social",
        product_id=product.subject["product_id"], listing_id=product.subject["listing_id"],
        variant_id=product.subject["variant_id"], link_ref="fixture-link",
        link_url_sha256=sha256(LINK_URL.encode()).hexdigest(), creative_ref=manifest["run_id"],
        creative_sha256=manifest["output_sha256"], timezone="UTC",
        measurement_window=["2026-10-30T02:00:00Z", AS_OF])
    request = dict(schema_version="AffiliateCommerceRequest.v1", idempotency_key="fixture-run", as_of=AS_OF, scope=scope)
    attr_scope = {k: scope[k] for k in ("provider_ref", "platform", "account_ref", "market", "surface",
                                      "measurement_window", "timezone")}
    attr_scope.update(app_ref=APP["id"], cohort_ref=None)
    attr_source = dict(schema_version="AffiliateAttributionSource.v1", source_kind="SYNTHETIC_FIXTURE",
        idempotency_key="fixture-attribution", scope=attr_scope, upstream_refs=[
            {"component": "A06", "evidence_ref": manifest["run_id"],
             "raw_sha256": sha256((creative / "e01-manifest.json").read_bytes()).hexdigest()}], events=[
        dict(kind="LINK_CREATIVE", event_ref="fixture-link-event", observed_at="2026-10-30T02:10:00Z",
             link_ref=scope["link_ref"], creative_ref=scope["creative_ref"], creative_sha256=scope["creative_sha256"],
             reported_link_status="UNKNOWN"),
        dict(kind="CLICK_LINK", event_ref="fixture-click-event", observed_at="2026-10-30T02:20:00Z",
             click_ref="fixture-click", link_ref=scope["link_ref"]),
    ])
    args = dict(request=request, creative_directory=creative, product_pack=product,
        release_pack=release, capability_snapshot=cap, distribution_directory=distribution,
        attribution=build_attribution_evidence(canonical_bytes(attr_source)))
    return args, attr_source, release_inputs


def test_real_offline_vertical_slice_is_partial_and_deterministic(inputs):
    args, _, _ = inputs
    run = build_affiliate_commerce_run(**args)
    value = run.to_dict()
    assert value["status"] == "PARTIAL"
    assert value["release_decision"] == "READY_FOR_MANUAL_REVIEW"
    assert value["stages"]["creative"]["status"] == "VERIFIED_LOCAL"
    assert value["stages"]["distribution"]["status"] == "EXPORTED_FOR_MANUAL_HANDOFF"
    assert value["publication_status"] == "NOT_ATTEMPTED"
    assert value["visibility_status"] == value["link_status"] == "UNKNOWN"
    assert value["actual_orders_verified"] is False
    assert value["actual_revenue"] == value["actual_payout"] == value["settlement_status"] == "UNKNOWN"
    assert value["actual_affiliate_entitlement"] == "UNKNOWN"
    assert value["stages"]["attribution"]["status"] == "PARTIAL"
    attr = json.loads(value["evidence"]["attribution"]["raw_utf8"])
    assert [e["kind"] for e in attr["source"]["document"]["events"]] == ["LINK_CREATIVE", "CLICK_LINK"]
    assert attr["results"] == []  # Click evidence creates no order, even a zero-valued order.
    assert build_affiliate_commerce_run(**args).to_bytes() == run.to_bytes()
    assert AffiliateCommerceRun.from_bytes(run.to_bytes(), expected_id=run.run_id).to_bytes() == run.to_bytes()
    assert Draft202012Validator(affiliate_commerce_run_schema()).is_valid(value)
    for item in value["evidence"].values():
        if item:
            assert sha256(item["raw_utf8"].encode()).hexdigest() == item["raw_sha256"]
    value["request"]["scope"]["account_ref"]["id"] = "mutated"
    assert run.to_dict()["request"]["scope"]["account_ref"] == ACCOUNT
    with pytest.raises(FrozenInstanceError):
        run._raw = b"changed"


def test_missing_commercial_evidence_keeps_actual_creative(inputs):
    args, _, _ = inputs
    run = build_affiliate_commerce_run(request=args["request"], creative_directory=args["creative_directory"])
    value = run.to_dict()
    assert value["creative_output"]["sha256"] == args["request"]["scope"]["creative_sha256"]
    assert value["status"] == "PARTIAL" and value["release_decision"] == "UNKNOWN"
    assert all(value["stages"][s]["status"] == "UNKNOWN" for s in
               ("product", "release", "capability", "distribution", "publication", "attribution", "commercial"))


@pytest.mark.parametrize("key,value,code", [
    ("creative_ref", "wrong-creative", "CREATIVE_IDENTITY_MISMATCH"),
    ("creative_sha256", "f" * 64, "CREATIVE_IDENTITY_MISMATCH"),
    ("product_id", "wrong-product", "PRODUCT_IDENTITY_MISMATCH"),
    ("variant_id", "wrong-variant", "PRODUCT_IDENTITY_MISMATCH"),
    ("listing_id", "wrong-listing", "PRODUCT_IDENTITY_MISMATCH"),
    ("account_ref", {"namespace": "internal_alias", "id": "other"}, "TARGET_IDENTITY_MISMATCH"),
    ("app_ref", {"namespace": "internal_alias", "id": "other-app"}, "TARGET_IDENTITY_MISMATCH"),
    ("market", "US", "TARGET_IDENTITY_MISMATCH"),
    ("channel", "different-channel", "RELEASE_IDENTITY_MISMATCH"),
    ("link_ref", "wrong-link", "ATTRIBUTION_LINK_MISMATCH"),
    ("link_url_sha256", "a" * 64, "RELEASE_LINK_OR_CREATIVE_MISMATCH"),
    ("provider_ref", "other-provider", "ATTRIBUTION_SCOPE_MISMATCH"),
])
def test_cross_component_identity_is_never_coerced(inputs, key, value, code):
    args, _, _ = inputs
    changed = dict(args, request=deepcopy(args["request"]))
    changed["request"]["scope"][key] = value
    with pytest.raises(CommerceError, match=code):
        build_affiliate_commerce_run(**changed)


@pytest.mark.parametrize("window", [
    [AS_OF, AS_OF], ["2026-10-31T00:00:00Z", AS_OF],
    ["2026-10-30T02:00:00Z", "2026-10-31T00:00:00Z"],
])
def test_invalid_time_windows(inputs, window):
    args, _, _ = inputs
    request = deepcopy(args["request"])
    request["scope"]["measurement_window"] = window
    with pytest.raises(CommerceError, match="INVALID_MEASUREMENT_WINDOW"):
        build_affiliate_commerce_run(**dict(args, request=request))


def test_attribution_window_must_match_exactly(inputs):
    args, source, _ = inputs
    changed = deepcopy(source)
    changed["scope"]["measurement_window"][0] = "2026-10-30T01:00:00Z"
    with pytest.raises(CommerceError, match="ATTRIBUTION_SCOPE_MISMATCH"):
        build_affiliate_commerce_run(**dict(args, attribution=build_attribution_evidence(canonical_bytes(changed))))


def test_attribution_upstream_hash_is_opened(inputs):
    args, source, _ = inputs
    changed = deepcopy(source)
    changed["upstream_refs"][0]["raw_sha256"] = "a" * 64
    with pytest.raises(CommerceError, match="ATTRIBUTION_UPSTREAM_PIN_MISMATCH"):
        build_affiliate_commerce_run(**dict(args, attribution=build_attribution_evidence(canonical_bytes(changed))))


def test_unknown_rights_cannot_become_entitlement_or_block_creative(inputs):
    args, _, release_inputs = inputs
    unknown = build_evidence_pack(make_snapshot(), as_of=AS_OF, generated_at=REVIEWED)
    unknown_release = build_affiliate_release_pack(**dict(release_inputs,
        product_pack=unknown, claims=(), assets=(), claim_evidence=(), rights_evidence=(),
        rights_status_evidence=(), placement_evidence=None, asset_bytes={}))
    value = build_affiliate_commerce_run(**dict(args, product_pack=unknown, release_pack=unknown_release)).to_dict()
    assert value["stages"]["product"]["status"] == "UNKNOWN"
    assert value["release_decision"] in {"UNKNOWN", "HOLD"}
    assert value["stages"]["creative"]["status"] == "VERIFIED_LOCAL"
    assert value["actual_affiliate_entitlement"] == "UNKNOWN"


def test_expired_release_is_hold_without_discarding_creative(inputs):
    args, _, _ = inputs
    request = deepcopy(args["request"])
    request["as_of"] = "2026-12-01T00:00:00Z"
    value = build_affiliate_commerce_run(**dict(args, request=request)).to_dict()
    assert value["release_decision"] == value["status"] == "HOLD"
    assert value["stages"]["creative"]["status"] == "VERIFIED_LOCAL"


def test_a08_submission_is_neither_public_nor_receipted(inputs, tmp_path, monkeypatch):
    args, _, _ = inputs
    monkeypatch.setattr("ai_commerce.distribution.receipt._now", lambda: "2026-10-30T02:50:00Z")
    journal = PublicationJournal(tmp_path / "synthetic-manual.db")
    initial = journal.create(args["distribution_directory"], idempotency_key="fixture-manual-action")
    submitted = journal.record(initial["publication_id"], "SUBMISSION_REPORTED")
    assert submitted["receipt_sha256"] is None
    value = build_affiliate_commerce_run(**dict(args, publication_db=journal.path,
                                              publication_id=initial["publication_id"])).to_dict()
    assert value["publication_status"] == "SUBMITTED"
    assert value["visibility_status"] == "UNKNOWN" and value["actual_orders_verified"] is False
    retained = json.loads(value["evidence"]["publication_record"]["raw_utf8"])["receipt"]
    assert retained["receipt_sha256"] is None and retained["receipt_refs"] == []


def test_publication_event_time_is_checked_not_just_creation(inputs, tmp_path, monkeypatch):
    args, _, _ = inputs
    monkeypatch.setattr("ai_commerce.distribution.receipt._now", lambda: "2026-10-30T02:50:00Z")
    journal = PublicationJournal(tmp_path / "future-submission.db")
    initial = journal.create(args["distribution_directory"], idempotency_key="fixture-future-submission")
    monkeypatch.setattr("ai_commerce.distribution.receipt._now", lambda: "2026-10-30T03:01:00Z")
    journal.record(initial["publication_id"], "SUBMISSION_REPORTED")
    with pytest.raises(CommerceError, match="PUBLICATION_EVENT_OUTSIDE_TIME_WINDOW"):
        build_affiliate_commerce_run(**dict(args, publication_db=journal.path,
                                           publication_id=initial["publication_id"]))


def test_missing_product_snapshot_does_not_block_existing_export(inputs):
    args, _, _ = inputs
    value = build_affiliate_commerce_run(**dict(args, product_pack=None)).to_dict()
    assert value["status"] == "PARTIAL"
    assert value["release_decision"] == "UNKNOWN"
    assert value["stages"]["distribution"]["status"] == "EXPORTED_FOR_MANUAL_HANDOFF"


@pytest.mark.parametrize("field,value", [
    ("account_ref", {"namespace": "youtube_channel_id", "id": "fixture-account"}),
    ("app_ref", {"namespace": "google_cloud_project_id", "id": "fixture-app"}),
    ("surface", "PAGE_REEL"),
])
def test_incompatible_native_namespaces_or_surfaces_fail(inputs, field, value):
    args, _, _ = inputs
    request = deepcopy(args["request"])
    request["scope"][field] = value
    with pytest.raises(CommerceError, match="TARGET_NAMESPACE_OR_SURFACE_INVALID"):
        build_affiliate_commerce_run(**dict(args, request=request))


def test_expired_or_fixture_capability_does_not_upgrade_release(inputs):
    args, _, release_inputs = inputs
    cap = deepcopy(args["capability_snapshot"])
    cap["expires_at"] = AS_OF
    release = build_affiliate_release_pack(**dict(release_inputs, capability_snapshot=cap))
    value = build_affiliate_commerce_run(**dict(args, capability_snapshot=cap, release_pack=release)).to_dict()
    assert value["release_decision"] in {"UNKNOWN", "HOLD"}
    assert value["stages"]["capability"]["status"] == "UNKNOWN"
    assert value["stages"]["creative"]["status"] == "VERIFIED_LOCAL"


def test_manifest_corruption_and_render_or_export_substitution_fail(inputs, tmp_path):
    import shutil
    args, _, _ = inputs
    creative = tmp_path / "corrupt-creative"
    shutil.copytree(args["creative_directory"], creative)
    path = creative / "e01-manifest.json"
    changed = json.loads(path.read_bytes())
    changed["composition"]["id"] = "unrelated"
    path.write_bytes(canonical_bytes(changed))
    with pytest.raises(CommerceError):
        build_affiliate_commerce_run(**dict(args, creative_directory=creative))
    distribution = tmp_path / "corrupt-export"
    shutil.copytree(args["distribution_directory"], distribution)
    (distribution / "caption.txt").write_bytes(b"substitution")
    with pytest.raises(CommerceError):
        build_affiliate_commerce_run(**dict(args, distribution_directory=distribution))


def test_resealed_status_upgrades_and_embedded_tampering_fail(inputs):
    args, _, _ = inputs
    original = build_affiliate_commerce_run(**args)
    for key, upgraded in (("release_decision", "APPROVED"), ("publication_status", "PUBLISHED_CONFIRMED"),
                           ("visibility_status", "PUBLIC"), ("actual_orders_verified", True),
                           ("actual_revenue", "100"), ("settlement_status", "SETTLED")):
        changed = original.to_dict()
        changed[key] = upgraded
        payload = {k: v for k, v in changed.items() if k not in {"run_id", "record_sha256"}}
        changed["record_sha256"] = sha256(canonical_bytes(payload)).hexdigest()
        changed["run_id"] = "a14-commerce-" + changed["record_sha256"]
        with pytest.raises(CommerceError):
            AffiliateCommerceRun.from_bytes(canonical_bytes(changed))
    changed = original.to_dict()
    changed["evidence"]["e01_manifest"]["raw_utf8"] += " "
    with pytest.raises(CommerceError, match="EVIDENCE_HASH_MISMATCH"):
        AffiliateCommerceRun.from_bytes(canonical_bytes(changed))
    with pytest.raises(CommerceError, match="RUN_PIN_MISMATCH"):
        AffiliateCommerceRun.from_bytes(original.to_bytes(), expected_id="different")


def test_history_idempotency_conflict_and_atomic_identity_checks(inputs):
    args, _, _ = inputs
    first = build_affiliate_commerce_run(**args)
    history = CommerceRunHistory()
    assert history.append(first) is True
    raw = history.to_bytes()
    assert history.append(first) is False and history.to_bytes() == raw
    request = deepcopy(args["request"])
    request["as_of"] = "2026-10-30T03:01:00Z"
    conflict = build_affiliate_commerce_run(**dict(args, request=request))
    with pytest.raises(CommerceError, match="IDEMPOTENCY_KEY_COLLISION"):
        history.append(conflict)
    assert history.to_bytes() == raw
    # A different window/key does not authorize reusing a link alias for a different URL.
    value = first.to_dict()
    value["request"]["idempotency_key"] = "new-key"
    value["request"]["scope"]["link_url_sha256"] = "f" * 64
    value["evidence"]["release_pack"] = None
    rebound = AffiliateCommerceRun.from_bytes(canonical_bytes(_derive(value["request"], value["evidence"])))
    with pytest.raises(CommerceError, match="IDENTITY_BINDING_CONFLICT"):
        history.append(rebound)
    assert history.to_bytes() == raw
    assert CommerceRunHistory.from_bytes(raw).to_bytes() == raw


def test_schema_mirror_and_standalone_portable_verify(inputs):
    schema = affiliate_commerce_run_schema()
    Draft202012Validator.check_schema(schema)
    root = Path(__file__).resolve().parents[1]
    assert json.loads((root / "contracts/AffiliateCommerceRun.v1.schema.json").read_bytes()) == schema
    run = build_affiliate_commerce_run(**inputs[0])
    # Use the same import root as the packaged API without touching shared CLI.
    result = subprocess.run([sys.executable, "-m", "ai_commerce.affiliate_commerce", "verify"],
        input=run.to_bytes(), capture_output=True, cwd=root / "src", timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip(b"\n") == run.to_bytes()


def test_e04_component_is_registered_for_targeted_verification():
    from ai_commerce.engineering.minimum_test import approved_groups
    root = Path(__file__).resolve().parents[1]
    groups = {group.name: group for group in approved_groups()}
    assert "a14-e04" in groups
    assert "tests/test_a14_commerce_e2e.py" in groups["a14-e04"].tests
    profiles = json.loads((root / ".github/ef02/verify-profiles.json").read_bytes())
    assert profiles["mt01-a14-e04-tests"] == [
        "python", "-m", "pytest", "tests/test_a14_commerce_e2e.py", "-q", "-n", "12",
    ]
