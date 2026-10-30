"""A13-E03 Offline-only AffiliateReleaseEvidencePack.v1.

Solely an advisory manual-review artifact integrating:
- C08 media technical inspection (MediaReleaseInspection.v1)
- C09 claim and asset rights release review (ClaimRightsReleaseReview.v1)
- D06 offline affiliate placement evidence (AffiliatePlacementEvidence.v1)
- A12 product/variant snapshot, identity, claim, offer, and rights evidence (ProductEvidencePack.v1)
- A07 channel capability snapshot/decision (ChannelCapabilitySnapshot.v1 / ChannelCapabilityDecision.v1)
- A08 manual distribution handoff package (DistributionPackage.v1, optional)

Revalidates account/product/variant/channel/market/surface/link scope, required
commercial disclosure, active date/expiry/revocation, hostile URL/tampered hash,
and missing or conflicting evidence.
Outputs strictly READY_FOR_MANUAL_REVIEW, HOLD, or UNKNOWN.
Never claims live provider approval, active affiliate link, POSTED/PUBLIC,
sales, OAuth, network/API calls, revenue, or auto-publishing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.distribution.assisted import DistributionError, _check_fields as _check_a08_fields, inspect_package
from ai_commerce.distribution.evaluator import evaluate as evaluate_a07_capability
from ai_commerce.distribution.model import CapabilityError, validate as validate_a07, validate_snapshot as validate_a07_snapshot
from ai_commerce.distribution.registry import CapabilityRegistry
from ai_commerce.product import (
    ProductAssetRights,
    ProductClaimEvidence,
    ProductError,
    ProductEvidencePack,
    ProductListingSnapshot,
    ProductOfferObservation,
    canonical_bytes,
    parse_json,
    parse_timestamp,
)
from ai_commerce.render.manifest import RenderManifest

from .claim_rights import (
    AssetUsage,
    ClaimPlacement,
    ClaimRightsReleaseReview,
    ClaimRightsReviewError,
    ClaimRightsReviewRequest,
    review_claim_rights,
)
from .claim_rights_schemas import claim_rights_review_schema
from .evidence_pack_schemas import PLATFORMS, affiliate_release_pack_schema
from .inspection import (
    DiagnosticOptions,
    InspectionLimits,
    MediaInspectionError,
    MediaReleaseInspection,
    ReleaseAsset,
    inspect_media_release,
)
from .placement import (
    AffiliatePlacementError,
    AffiliatePlacementEvidence,
    EvidenceRef,
    PlacementDisclosure,
    PlacementRequest,
    PlacementTemporal,
    _REJECT,
    _evaluate_disclosure,
    _evaluate_url,
    _normalize_account,
    _normalize_app,
)
from .placement_schemas import SURFACES, affiliate_placement_schema

_SCHEMA = affiliate_release_pack_schema()
_VALIDATOR = Draft202012Validator(_SCHEMA)
_C09_VALIDATOR = Draft202012Validator(claim_rights_review_schema())
_D06_VALIDATOR = Draft202012Validator(affiliate_placement_schema())
_DOCUMENT_LIMIT = 8 * 1024 * 1024
_UNSET = object()


class AffiliateReleaseError(ValueError):
    """Stable bounded error code for AffiliateReleaseEvidencePack.v1."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise AffiliateReleaseError(code)


@dataclass(frozen=True)
class AffiliateReleaseRequest:
    account_ref: dict[str, str] | str
    app_ref: dict[str, str] | str | None = None
    platform: str = "TIKTOK"
    market: str = "VN"
    territory: str | None = None
    surface: str = "PRODUCT_ANCHOR"
    channel: str = "social"
    product_id: str = ""
    listing_id: str | None = None
    variant_id: str = ""
    release_sha256: str = ""
    link_url: str = ""
    as_of: str = ""
    valid_from: str | None = None
    use_from: str | None = None
    use_until: str = ""
    valid_until: str | None = None
    revoked_at: str | None = None


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _evaluate_release_temporal(
    *,
    as_of: str,
    valid_from: str,
    use_from: str,
    use_until: str,
    valid_until: str,
    revoked_at: str | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    codes: list[str] = []
    dt_as_of = parse_timestamp(as_of)
    dt_valid_from = parse_timestamp(valid_from)
    dt_use_from = parse_timestamp(use_from)
    dt_use_until = parse_timestamp(use_until)
    dt_valid_until = parse_timestamp(valid_until)
    dt_revoked = parse_timestamp(revoked_at) if revoked_at is not None else None

    if (
        dt_valid_from >= dt_valid_until
        or dt_use_from >= dt_use_until
        or dt_use_from < dt_valid_from
        or dt_use_until > dt_valid_until
    ):
        status = "INVALID_WINDOW"
        codes.append("INVALID_TEMPORAL_WINDOW")
    elif dt_revoked is not None and (dt_revoked <= dt_as_of or dt_revoked < dt_use_until):
        status = "REVOKED"
        codes.append("PLACEMENT_REVOKED")
    elif dt_as_of >= dt_valid_until or dt_as_of >= dt_use_until:
        status = "EXPIRED"
        codes.append("PLACEMENT_EXPIRED")
    elif dt_as_of < dt_valid_from:
        status = "NOT_YET_EFFECTIVE"
        codes.append("PLACEMENT_NOT_YET_EFFECTIVE")
    else:
        status = "ACTIVE"
        codes.append("TEMPORAL_WINDOW_VALID")

    return {
        "as_of": as_of,
        "valid_from": valid_from,
        "use_from": use_from,
        "use_until": use_until,
        "valid_until": valid_until,
        "revoked_at": revoked_at,
        "status": status,
    }, codes, status


def _revalidate_media_inspection(
    media_inspection: MediaReleaseInspection | bytes | dict[str, Any] | None,
    *,
    release_directory: Path | str | None,
    release_assets: Iterable[ReleaseAsset] | None,
    diagnostics: DiagnosticOptions | None,
    limits: InspectionLimits | None,
    release_sha256: str,
    release_asset_bytes: bytes | None,
) -> tuple[dict[str, Any], MediaReleaseInspection | None, list[dict[str, Any]], bool, bool, bool, bool]:
    """Return (component_dict, verified_obj, evidence_refs, hold, unknown, scope_ok, hash_ok)."""
    refs: list[dict[str, Any]] = []
    if media_inspection is None and release_directory is not None and release_assets is not None:
        media_inspection = inspect_media_release(
            release_directory,
            list(release_assets),
            diagnostics=diagnostics,
            limits=limits,
        )

    if media_inspection is None:
        return (
            {
                "present": False,
                "record_sha256": None,
                "status": "MISSING",
                "release_asset_matched": False,
                "full_decode": False,
                "tamper_checked": False,
                "reason_codes": ["MEDIA_INSPECTION_MISSING"],
            },
            None,
            refs,
            False,
            True,
            True,
            True,
        )

    try:
        if isinstance(media_inspection, MediaReleaseInspection):
            raw = media_inspection.to_bytes()
        elif isinstance(media_inspection, bytes):
            raw = media_inspection
        elif isinstance(media_inspection, dict):
            raw = canonical_bytes(media_inspection)
        else:
            raise MediaInspectionError("MEDIA_INSPECTION_TYPE_INVALID")
        verified = MediaReleaseInspection.from_bytes(raw)
        insp_dict = verified.to_dict()
        rec_sha = insp_dict["record_sha256"]
    except (MediaInspectionError, ProductError, ValueError, TypeError, KeyError):
        return (
            {
                "present": True,
                "record_sha256": None,
                "status": "TAMPERED",
                "release_asset_matched": False,
                "full_decode": False,
                "tamper_checked": True,
                "reason_codes": ["MEDIA_INSPECTION_TAMPERED", "TAMPERED_HASH"],
            },
            None,
            refs,
            True,
            False,
            True,
            False,
        )

    codes: set[str] = set()
    hold = False
    unknown = False
    scope_ok = True
    hash_ok = True

    matching = [a for a in insp_dict["assets"] if a["expected_sha256"] == release_sha256]
    if not matching:
        release_matched = False
        scope_ok = False
        hold = True
        codes.add("RELEASE_SHA256_MISMATCH")
        codes.add("MEDIA_INSPECTION_RELEASE_MISMATCH")
        full_decode = False
    else:
        release_matched = True
        full_decode = all(bool(a.get("full_decode")) for a in matching)
        for asset in matching:
            codes.update(asset.get("reason_codes", []))
            if asset.get("observed_sha256") not in (None, release_sha256) or "ASSET_HASH_MISMATCH" in asset.get("reason_codes", []):
                hash_ok = False
                hold = True
                codes.add("ASSET_HASH_MISMATCH")
                codes.add("TAMPERED_HASH")

    if release_asset_bytes is not None:
        if not isinstance(release_asset_bytes, bytes) or sha256_bytes(release_asset_bytes) != release_sha256:
            hash_ok = False
            hold = True
            codes.add("ASSET_HASH_MISMATCH")
            codes.add("TAMPERED_HASH")

    raw_status = insp_dict["status"]
    if not hash_ok:
        comp_status = "TAMPERED"
    elif raw_status == "FAIL" or any(a.get("status") == "FAIL" for a in matching):
        comp_status = "FAIL"
        hold = True
        codes.add("MEDIA_INSPECTION_FAILED")
    elif not release_matched:
        comp_status = "FAIL"
        hold = True
    elif raw_status == "UNKNOWN" or any(a.get("status") == "UNKNOWN" for a in matching) or not full_decode:
        comp_status = "UNKNOWN"
        unknown = True
        codes.add("MEDIA_INSPECTION_UNKNOWN")
    else:
        comp_status = "PASS"
        codes.add("MEDIA_INSPECTION_VERIFIED")

    refs.append({
        "evidence_id": "c08-media-" + rec_sha[:16],
        "kind": "C08_MEDIA_RELEASE_INSPECTION",
        "record_sha256": rec_sha,
        "source_sha256": release_sha256 if release_matched else None,
        "tamper_checked": True,
    })

    return (
        {
            "present": True,
            "record_sha256": rec_sha,
            "status": comp_status,
            "release_asset_matched": release_matched,
            "full_decode": full_decode,
            "tamper_checked": True,
            "reason_codes": sorted(codes),
        },
        verified,
        refs,
        hold,
        unknown,
        scope_ok,
        hash_ok,
    )


def _revalidate_product_evidence(
    product_pack: ProductEvidencePack | bytes | dict[str, Any] | None,
    *,
    snapshot: ProductListingSnapshot | bytes | dict[str, Any] | None,
    snapshot_raw: bytes | None,
    offer_observation: ProductOfferObservation | bytes | dict[str, Any] | None,
    norm_product: str,
    norm_listing: str | None,
    norm_variant: str,
    as_of: str,
    use_from: str,
) -> tuple[
    dict[str, Any],
    ProductEvidencePack | None,
    ProductListingSnapshot | None,
    list[dict[str, Any]],
    bool,
    bool,
    bool,
    bool,
    bool,
]:
    """Return (comp_dict, pack_obj, snap_obj, refs, hold, unknown, scope_ok, hash_ok, conflict)."""
    refs: list[dict[str, Any]] = []
    if product_pack is None:
        return (
            {
                "present": False,
                "pack_id": None,
                "pack_sha256": None,
                "snapshot_id": None,
                "snapshot_sha256": None,
                "offer_assessment_status": "MISSING",
                "active_offer_id": None,
                "stock_available": None,
                "rights_status": "MISSING",
                "synthetic": False,
                "scope_matched": False,
                "tamper_checked": False,
                "status": "MISSING",
                "reason_codes": ["PRODUCT_EVIDENCE_PACK_MISSING"],
            },
            None,
            None,
            refs,
            False,
            True,
            True,
            True,
            False,
        )

    checked_snap: ProductListingSnapshot | None = None
    snap_sha: str | None = None
    snap_id: str | None = None
    try:
        if snapshot is not None:
            if isinstance(snapshot, ProductListingSnapshot):
                checked_snap = ProductListingSnapshot.from_bytes(
                    snapshot.to_bytes(),
                    raw=snapshot_raw if snapshot_raw is not None else snapshot.raw_bytes(),
                )
            elif isinstance(snapshot, bytes):
                checked_snap = ProductListingSnapshot.from_bytes(snapshot, raw=snapshot_raw)
            elif isinstance(snapshot, dict):
                checked_snap = ProductListingSnapshot.from_bytes(canonical_bytes(snapshot), raw=snapshot_raw)
            else:
                raise ProductError("INVALID_SNAPSHOT")
            snap_sha = sha256_bytes(checked_snap.to_bytes())
            snap_id = checked_snap.snapshot_id

        if isinstance(product_pack, ProductEvidencePack):
            raw_pack = product_pack.to_bytes()
        elif isinstance(product_pack, bytes):
            raw_pack = product_pack
        elif isinstance(product_pack, dict):
            raw_pack = canonical_bytes(product_pack)
        else:
            raise ProductError("INVALID_PRODUCT_PACK")
        checked_pack = ProductEvidencePack.from_bytes(raw_pack, snapshot=checked_snap)
        pack_dict = checked_pack.to_dict()
        pack_sha = sha256_bytes(checked_pack.to_bytes())
    except (ProductError, ValueError, TypeError, KeyError):
        return (
            {
                "present": True,
                "pack_id": None,
                "pack_sha256": None,
                "snapshot_id": snap_id,
                "snapshot_sha256": snap_sha,
                "offer_assessment_status": "TAMPERED",
                "active_offer_id": None,
                "stock_available": None,
                "rights_status": "TAMPERED",
                "synthetic": False,
                "scope_matched": False,
                "tamper_checked": True,
                "status": "TAMPERED",
                "reason_codes": ["PRODUCT_PACK_TAMPERED", "TAMPERED_HASH"],
            },
            None,
            None,
            refs,
            True,
            False,
            True,
            False,
            False,
        )

    codes: set[str] = set()
    hold = False
    unknown = False
    scope_ok = True
    hash_ok = True
    conflict = False

    subj = pack_dict["subject"]
    if subj["product_id"] != norm_product:
        codes.add("PRODUCT_MISMATCH")
        scope_ok = False
        hold = True
    if subj["variant_id"] != norm_variant:
        codes.add("VARIANT_MISMATCH")
        scope_ok = False
        hold = True
    if norm_listing is not None and subj["listing_id"] != norm_listing:
        codes.add("LISTING_MISMATCH")
        scope_ok = False
        hold = True

    synthetic = bool(subj.get("synthetic", False))
    if synthetic:
        codes.add("SYNTHETIC_PRODUCT_EVIDENCE")
        hold = True

    dt_as_of = parse_timestamp(as_of)
    dt_use_from = parse_timestamp(use_from)
    if parse_timestamp(pack_dict["generated_at"]) > dt_as_of:
        codes.add("PRODUCT_PACK_FROM_FUTURE")
        hold = True

    offers_block = pack_dict["offers"]
    offer_status = offers_block["assessment_status"]
    active_offer = offers_block.get("active_offer")
    active_offer_id = active_offer["offer_id"] if active_offer else None
    stock_available = active_offer["stock_available"] if active_offer else None

    if offer_status == "CONFLICT" or offers_block.get("conflict_fields"):
        codes.add("OFFER_CONFLICT")
        codes.add("CONFLICTING_EVIDENCE")
        conflict = True
        hold = True
    elif offer_status in ("EXPIRED", "STALE"):
        codes.add("OFFER_EXPIRED_OR_STALE")
        hold = True
    elif offer_status == "NO_OFFERS" or active_offer is None:
        codes.add("OFFER_EVIDENCE_MISSING")
        unknown = True
    else:
        if stock_available is False:
            codes.add("OFFER_OUT_OF_STOCK")
            hold = True
        for obs_row in offers_block.get("observations", []):
            if obs_row.get("offer_id") == active_offer_id:
                exp = obs_row.get("expires_at")
                if exp is not None and parse_timestamp(exp) <= max(dt_as_of, dt_use_from):
                    offer_status = "EXPIRED"
                    codes.add("OFFER_EXPIRED_OR_STALE")
                    hold = True
                obs_at = obs_row.get("observed_at")
                if obs_at is not None and parse_timestamp(obs_at) > dt_as_of:
                    codes.add("OFFER_FROM_FUTURE")
                    hold = True

    if offer_observation is not None:
        try:
            if hasattr(offer_observation, "to_dict"):
                oo_dict = offer_observation.to_dict()
                oo_bytes = offer_observation.to_bytes()
            elif isinstance(offer_observation, bytes):
                oo_dict = parse_json(offer_observation)
                oo_bytes = offer_observation
            elif isinstance(offer_observation, dict):
                oo_dict = offer_observation
                oo_bytes = canonical_bytes(offer_observation)
            else:
                raise ProductError("INVALID_OFFER_OBSERVATION")

            oo_subj = oo_dict.get("subject", oo_dict)
            oo_prod = oo_subj.get("product_id") or oo_dict.get("product_id")
            oo_var = oo_subj.get("variant_id") or oo_dict.get("variant_id")
            if oo_prod and oo_prod != norm_product:
                codes.add("PRODUCT_MISMATCH")
                scope_ok = False
                hold = True
            if oo_var and oo_var != norm_variant:
                codes.add("VARIANT_MISMATCH")
                scope_ok = False
                hold = True

            oo_offer = oo_dict.get("offer", oo_dict)
            oo_price_obj = oo_offer.get("price", {}) if isinstance(oo_offer.get("price"), dict) else {}
            oo_price_amt = oo_offer.get("price_amount", oo_price_obj.get("amount"))
            oo_currency = oo_offer.get("currency", oo_price_obj.get("currency"))
            if active_offer is not None:
                if (
                    oo_price_amt is not None
                    and str(oo_price_amt) != str(active_offer["price_amount"])
                ) or (
                    oo_currency is not None
                    and str(oo_currency) != str(active_offer["currency"])
                ):
                    codes.add("OFFER_CONFLICT")
                    codes.add("CONFLICTING_EVIDENCE")
                    offer_status = "CONFLICT"
                    conflict = True
                    hold = True
            if oo_offer.get("stock_available") is False:
                codes.add("OFFER_OUT_OF_STOCK")
                hold = True
            oo_exp = oo_offer.get("expires_at")
            if oo_exp is not None and parse_timestamp(oo_exp) <= max(dt_as_of, dt_use_from):
                codes.add("OFFER_EXPIRED_OR_STALE")
                offer_status = "EXPIRED"
                hold = True

            refs.append({
                "evidence_id": str(oo_dict.get("offer_id", "a12-offer-observation")),
                "kind": "A12_PRODUCT_OFFER_OBSERVATION",
                "record_sha256": sha256_bytes(oo_bytes),
                "source_sha256": None,
                "tamper_checked": True,
            })
        except (ProductError, ValueError, TypeError, KeyError):
            codes.add("OFFER_OBSERVATION_TAMPERED")
            codes.add("TAMPERED_HASH")
            hash_ok = False
            hold = True

    for claim_row in pack_dict.get("claims", []):
        if claim_row.get("status") == "CONFLICT" or claim_row.get("role") == "COUNTEREVIDENCE":
            codes.add("CLAIM_CONFLICT_OR_COUNTEREVIDENCE")
            codes.add("CONFLICTING_EVIDENCE")
            conflict = True
            hold = True

    if hold:
        status = "HOLD"
    elif unknown:
        status = "UNKNOWN"
    else:
        status = "VERIFIED"
        codes.add("PRODUCT_EVIDENCE_VERIFIED")

    refs.append({
        "evidence_id": checked_pack.pack_id,
        "kind": "A12_PRODUCT_EVIDENCE_PACK",
        "record_sha256": pack_sha,
        "source_sha256": snap_sha,
        "tamper_checked": True,
    })
    if checked_snap is not None and snap_sha is not None and snap_id is not None:
        refs.append({
            "evidence_id": snap_id,
            "kind": "A12_PRODUCT_LISTING_SNAPSHOT",
            "record_sha256": snap_sha,
            "source_sha256": checked_snap.to_dict()["source"]["raw_sha256"],
            "tamper_checked": True,
        })

    return (
        {
            "present": True,
            "pack_id": checked_pack.pack_id,
            "pack_sha256": pack_sha,
            "snapshot_id": snap_id or pack_dict["source"].get("snapshot_id"),
            "snapshot_sha256": snap_sha,
            "offer_assessment_status": offer_status,
            "active_offer_id": active_offer_id,
            "stock_available": stock_available,
            "rights_status": pack_dict["rights_status"],
            "synthetic": synthetic,
            "scope_matched": scope_ok,
            "tamper_checked": True,
            "status": status,
            "reason_codes": sorted(codes),
        },
        checked_pack,
        checked_snap,
        refs,
        hold,
        unknown,
        scope_ok,
        hash_ok,
        conflict,
    )


def _revalidate_claim_rights_review(
    claim_rights_review: ClaimRightsReleaseReview | bytes | dict[str, Any] | None,
    *,
    product_pack: ProductEvidencePack | None,
    snapshot: ProductListingSnapshot | None,
    claims: Iterable[ClaimPlacement] | None,
    assets: Iterable[AssetUsage] | None,
    claim_evidence: Iterable[ProductClaimEvidence] | None,
    rights_evidence: Iterable[ProductAssetRights] | None,
    asset_bytes: dict[str, bytes] | None,
    rights_status_evidence: Iterable[bytes] | None,
    render_manifest: RenderManifest | None,
    media_inspection: MediaReleaseInspection | None,
    norm_product: str,
    norm_listing: str | None,
    norm_variant: str,
    norm_release_sha: str,
    norm_territory: str,
    norm_channel: str,
    as_of: str,
    use_from: str,
    use_until: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool, bool, bool, bool, bool]:
    """Return (comp_dict, refs, hold, unknown, scope_ok, hash_ok, conflict)."""
    refs: list[dict[str, Any]] = []
    has_c09_build_inputs = any(
        x is not None
        for x in (claims, assets, claim_evidence, rights_evidence, asset_bytes, rights_status_evidence)
    )
    if claim_rights_review is None and product_pack is not None and has_c09_build_inputs:
        c09_as_of = as_of
        c09_use_from = use_from
        if parse_timestamp(c09_as_of) > parse_timestamp(c09_use_from):
            c09_use_from = c09_as_of
        c09_req = ClaimRightsReviewRequest(
            product_id=norm_product,
            listing_id=norm_listing or product_pack.subject["listing_id"],
            variant_id=norm_variant,
            release_sha256=norm_release_sha,
            territory=norm_territory,
            channel=norm_channel,
            as_of=c09_as_of,
            use_from=c09_use_from,
            use_until=use_until,
        )
        claim_rights_review = review_claim_rights(
            product_pack,
            request=c09_req,
            snapshot=snapshot,
            claims=tuple(claims or ()),
            assets=tuple(assets or ()),
            claim_evidence=tuple(claim_evidence or ()),
            rights_evidence=tuple(rights_evidence or ()),
            asset_bytes=asset_bytes,
            rights_status_evidence=tuple(rights_status_evidence or ()),
            render_manifest=render_manifest,
            media_inspection=media_inspection,
        )

    if claim_rights_review is None:
        return (
            {
                "present": False,
                "record_sha256": None,
                "pack_id": None,
                "pack_sha256": None,
                "snapshot_sha256": None,
                "claim_status": "MISSING",
                "rights_status": "MISSING",
                "status": "MISSING",
                "scope_matched": False,
                "tamper_checked": False,
                "reason_codes": ["CLAIM_RIGHTS_REVIEW_MISSING"],
            },
            refs,
            False,
            True,
            True,
            True,
            False,
        )

    try:
        if isinstance(claim_rights_review, ClaimRightsReleaseReview):
            raw = claim_rights_review.to_bytes()
        elif isinstance(claim_rights_review, bytes):
            raw = claim_rights_review
        elif isinstance(claim_rights_review, dict):
            raw = canonical_bytes(claim_rights_review)
        else:
            raise ClaimRightsReviewError("INVALID_CLAIM_RIGHTS_REVIEW")

        _require(0 < len(raw) <= _DOCUMENT_LIMIT, "CLAIM_RIGHTS_BYTE_LIMIT")
        c09_dict = parse_json(raw, limit=_DOCUMENT_LIMIT)
        if not _C09_VALIDATOR.is_valid(c09_dict):
            raise ClaimRightsReviewError("CLAIM_RIGHTS_SCHEMA_INVALID")
        body = {k: v for k, v in c09_dict.items() if k != "record_sha256"}
        if c09_dict["record_sha256"] != sha256_bytes(canonical_bytes(body)):
            raise ClaimRightsReviewError("CLAIM_RIGHTS_DIGEST_MISMATCH")

        # Verify internal semantic consistency of C09 status aggregation
        rank = {"VERIFIED_EVIDENCE": 0, "UNKNOWN": 1, "NEEDS_REVIEW": 2, "RESTRICTED": 3}
        expected_overall = max(
            rank[c09_dict["claim_status"]],
            rank[c09_dict["rights_status"]],
        )
        if rank[c09_dict["status"]] != expected_overall:
            raise ClaimRightsReviewError("CLAIM_RIGHTS_STATUS_INCONSISTENT")

        if (
            product_pack is not None
            and has_c09_build_inputs
            and (c09_dict.get("media_inspection_sha256") is None) == (media_inspection is None)
            and (c09_dict.get("render_manifest_sha256") is None) == (render_manifest is None)
        ):
            c09_req_dict = c09_dict["request"]
            c09_req_obj = ClaimRightsReviewRequest(**c09_req_dict)
            ClaimRightsReleaseReview.from_bytes(
                raw,
                product_pack,
                request=c09_req_obj,
                snapshot=snapshot,
                claims=tuple(claims or ()),
                assets=tuple(assets or ()),
                claim_evidence=tuple(claim_evidence or ()),
                rights_evidence=tuple(rights_evidence or ()),
                asset_bytes=asset_bytes,
                rights_status_evidence=tuple(rights_status_evidence or ()),
                render_manifest=render_manifest,
                media_inspection=media_inspection,
            )
    except (ClaimRightsReviewError, AffiliateReleaseError, ProductError, ValueError, TypeError, KeyError):
        return (
            {
                "present": True,
                "record_sha256": None,
                "pack_id": None,
                "pack_sha256": None,
                "snapshot_sha256": None,
                "claim_status": "TAMPERED",
                "rights_status": "TAMPERED",
                "status": "TAMPERED",
                "scope_matched": False,
                "tamper_checked": True,
                "reason_codes": ["CLAIM_RIGHTS_REVIEW_TAMPERED", "TAMPERED_HASH"],
            },
            refs,
            True,
            False,
            True,
            False,
            False,
        )

    codes: set[str] = set(c09_dict.get("reason_codes", []))
    hold = False
    unknown = False
    scope_ok = True
    conflict = False

    req_block = c09_dict["request"]
    subj_block = c09_dict["subject"]
    if req_block["product_id"] != norm_product or subj_block["product_id"] != norm_product:
        codes.add("PRODUCT_MISMATCH")
        scope_ok = False
        hold = True
    if req_block["variant_id"] != norm_variant or subj_block["variant_id"] != norm_variant:
        codes.add("VARIANT_MISMATCH")
        scope_ok = False
        hold = True
    if norm_listing is not None and (
        req_block["listing_id"] != norm_listing or subj_block["listing_id"] != norm_listing
    ):
        codes.add("LISTING_MISMATCH")
        scope_ok = False
        hold = True
    if req_block["release_sha256"] != norm_release_sha:
        codes.add("RELEASE_SHA256_MISMATCH")
        scope_ok = False
        hold = True
    if req_block["territory"] != norm_territory:
        codes.add("MARKET_MISMATCH")
        scope_ok = False
        hold = True
    if req_block["channel"] != norm_channel:
        codes.add("CHANNEL_MISMATCH")
        scope_ok = False
        hold = True

    dt_use_from = parse_timestamp(use_from)
    dt_use_until = parse_timestamp(use_until)
    if (
        parse_timestamp(req_block["use_from"]) > dt_use_from
        or parse_timestamp(req_block["use_until"]) < dt_use_until
    ):
        codes.add("CLAIM_RIGHTS_WINDOW_MISMATCH")
        scope_ok = False
        hold = True

    if product_pack is not None:
        expected_pack_sha = sha256_bytes(product_pack.to_bytes())
        if c09_dict["pack_id"] != product_pack.pack_id or c09_dict["pack_sha256"] != expected_pack_sha:
            codes.add("CLAIM_RIGHTS_PACK_MISMATCH")
            scope_ok = False
            hold = True

    if media_inspection is not None and c09_dict.get("media_inspection_sha256") is not None:
        if c09_dict["media_inspection_sha256"] != sha256_bytes(media_inspection.to_bytes()):
            codes.add("CLAIM_RIGHTS_MEDIA_MISMATCH")
            scope_ok = False
            hold = True

    c09_status = c09_dict["status"]
    if any(
        c in codes
        for c in (
            "CLAIM_CONFLICT_OR_COUNTEREVIDENCE",
            "NONFACTUAL_LABEL_CONFLICTS_WITH_EVIDENCE",
            "CLAIM_STATEMENT_MISMATCH",
            "RIGHTS_STATUS_SOURCE_MISMATCH",
        )
    ):
        conflict = True
        codes.add("CONFLICTING_EVIDENCE")

    if c09_status == "RESTRICTED":
        hold = True
        codes.add("CLAIM_RIGHTS_RESTRICTED")
    elif c09_status == "NEEDS_REVIEW":
        hold = True
        codes.add("CLAIM_RIGHTS_NEEDS_REVIEW")
    elif (
        c09_status == "UNKNOWN"
        or c09_dict.get("usage_verification") == "UNPROVEN"
        or c09_dict.get("snapshot_sha256") is None
    ):
        unknown = True
        codes.add("CLAIM_RIGHTS_UNKNOWN")
    else:
        codes.add("CLAIM_RIGHTS_VERIFIED")

    rec_sha = c09_dict["record_sha256"]
    refs.append({
        "evidence_id": "c09-review-" + rec_sha[:16],
        "kind": "C09_CLAIM_RIGHTS_RELEASE_REVIEW",
        "record_sha256": rec_sha,
        "source_sha256": c09_dict["pack_sha256"],
        "tamper_checked": True,
    })

    return (
        {
            "present": True,
            "record_sha256": rec_sha,
            "pack_id": c09_dict["pack_id"],
            "pack_sha256": c09_dict["pack_sha256"],
            "snapshot_sha256": c09_dict["snapshot_sha256"],
            "claim_status": c09_dict["claim_status"],
            "rights_status": c09_dict["rights_status"],
            "status": c09_status,
            "scope_matched": scope_ok,
            "tamper_checked": True,
            "reason_codes": sorted(codes),
        },
        refs,
        hold,
        unknown,
        scope_ok,
        True,
        conflict,
    )


def _revalidate_placement_evidence(
    placement_evidence: AffiliatePlacementEvidence | bytes | dict[str, Any] | None,
    *,
    norm_account: dict[str, str],
    norm_app: dict[str, str] | None,
    norm_market: str,
    norm_surface: str,
    norm_product: str,
    norm_variant: str,
    norm_link: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]], bool, bool, bool, bool]:
    """Return (comp_dict, d06_dict, refs, hold, unknown, scope_ok, hash_ok)."""
    refs: list[dict[str, Any]] = []
    if placement_evidence is None:
        return (
            {
                "present": False,
                "record_sha256": None,
                "decision": "MISSING",
                "provenance_status": "MISSING",
                "scope_matched": False,
                "tamper_checked": False,
                "reason_codes": ["PLACEMENT_EVIDENCE_MISSING"],
            },
            None,
            refs,
            False,
            True,
            True,
            True,
        )

    try:
        if isinstance(placement_evidence, AffiliatePlacementEvidence):
            raw = placement_evidence.to_bytes()
        elif isinstance(placement_evidence, bytes):
            raw = placement_evidence
        elif isinstance(placement_evidence, dict):
            raw = canonical_bytes(placement_evidence)
        else:
            raise AffiliatePlacementError("INVALID_PLACEMENT_EVIDENCE")

        verified = AffiliatePlacementEvidence.from_bytes(raw)
        d06_dict = verified.to_dict()
        # Verify internal consistency against resealing attacks
        re_url, _, re_hostile = _evaluate_url(d06_dict["placement"]["link_url"])
        if d06_dict["url_evaluation"]["is_hostile"] != re_hostile:
            raise AffiliatePlacementError("PLACEMENT_URL_CONSISTENCY_MISMATCH")
        if (
            re_hostile
            or d06_dict["disclosure"]["status"] != "COMPLIANT"
            or d06_dict["temporal"]["status"] != "ACTIVE"
            or d06_dict["evidence_provenance"]["provenance_status"] == "TAMPERED"
        ) and d06_dict["decision"] != "HOLD":
            raise AffiliatePlacementError("PLACEMENT_DECISION_CONSISTENCY_MISMATCH")
        if (
            d06_dict["evidence_provenance"]["provenance_status"] == "UNPROVEN"
            and d06_dict["decision"] == "REVIEW"
        ):
            raise AffiliatePlacementError("PLACEMENT_PROVENANCE_CONSISTENCY_MISMATCH")
    except (AffiliatePlacementError, ProductError, ValueError, TypeError, KeyError):
        return (
            {
                "present": True,
                "record_sha256": None,
                "decision": "TAMPERED",
                "provenance_status": "TAMPERED",
                "scope_matched": False,
                "tamper_checked": True,
                "reason_codes": ["PLACEMENT_EVIDENCE_TAMPERED", "TAMPERED_HASH"],
            },
            None,
            refs,
            True,
            False,
            True,
            False,
        )

    codes: set[str] = set(d06_dict.get("reason_codes", []))
    hold = False
    unknown = False
    scope_ok = True
    hash_ok = True

    plc = d06_dict["placement"]
    if plc["account_ref"] != norm_account:
        codes.add("CROSS_ACCOUNT_MISMATCH")
        scope_ok = False
        hold = True
    if plc["app_ref"] != norm_app:
        codes.add("APP_MISMATCH")
        scope_ok = False
        hold = True
    if plc["market"] != norm_market:
        codes.add("MARKET_MISMATCH")
        scope_ok = False
        hold = True
    if plc["surface"] != norm_surface:
        codes.add("SURFACE_MISMATCH")
        scope_ok = False
        hold = True
    if plc["product_id"] != norm_product:
        codes.add("PRODUCT_MISMATCH")
        scope_ok = False
        hold = True
    if plc["variant_id"] != norm_variant:
        codes.add("VARIANT_MISMATCH")
        scope_ok = False
        hold = True
    if plc["link_url"] != norm_link:
        codes.add("LINK_URL_MISMATCH")
        scope_ok = False
        hold = True

    prov_status = d06_dict["evidence_provenance"]["provenance_status"]
    d06_decision = d06_dict["decision"]
    if prov_status == "TAMPERED" or "TAMPERED_HASH" in codes:
        hash_ok = False
        hold = True
        codes.add("TAMPERED_HASH")

    if d06_decision == "HOLD":
        hold = True
        codes.add("PLACEMENT_DECISION_HOLD")
    elif d06_decision == "UNKNOWN" or prov_status == "UNPROVEN":
        unknown = True
        codes.add("PLACEMENT_DECISION_UNKNOWN")
    else:
        codes.add("PLACEMENT_EVIDENCE_VERIFIED")

    rec_sha = d06_dict["record_sha256"]
    refs.append({
        "evidence_id": "d06-placement-" + rec_sha[:16],
        "kind": "D06_AFFILIATE_PLACEMENT_EVIDENCE",
        "record_sha256": rec_sha,
        "source_sha256": None,
        "tamper_checked": True,
    })

    return (
        {
            "present": True,
            "record_sha256": rec_sha,
            "decision": d06_decision,
            "provenance_status": prov_status,
            "scope_matched": scope_ok,
            "tamper_checked": True,
            "reason_codes": sorted(codes),
        },
        d06_dict,
        refs,
        hold,
        unknown,
        scope_ok,
        hash_ok,
    )


def _revalidate_capability_evidence(
    *,
    capability_decision: dict[str, Any] | bytes | None,
    capability_snapshot: dict[str, Any] | bytes | None,
    capability_registry: CapabilityRegistry | Path | str | None,
    capability_action: str,
    norm_platform: str,
    norm_account: dict[str, str],
    norm_app: dict[str, str] | None,
    norm_market: str,
    norm_surface: str,
    as_of: str,
    use_from: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool, bool, bool, bool]:
    """Return (comp_dict, refs, hold, unknown, scope_ok, hash_ok)."""
    refs: list[dict[str, Any]] = []
    dt_as_of = parse_timestamp(as_of)
    dt_use_from = parse_timestamp(use_from)

    if capability_registry is not None and capability_decision is None:
        try:
            reg = (
                capability_registry
                if isinstance(capability_registry, CapabilityRegistry)
                else CapabilityRegistry(capability_registry)
            )
            req = {
                "schema_version": "ChannelCapabilityRequest.v1",
                "platform": norm_platform,
                "account_ref": norm_account,
                "app_ref": norm_app,
                "market": norm_market,
                "surface": norm_surface,
                "action": capability_action,
            }
            capability_decision = evaluate_a07_capability(req, reg, at=dt_as_of)
        except (CapabilityError, ValueError, TypeError, KeyError, OSError):
            return (
                {
                    "present": True,
                    "snapshot_id": None,
                    "record_sha256": None,
                    "decision": "TAMPERED",
                    "scope_matched": False,
                    "tamper_checked": True,
                    "reason_codes": ["CAPABILITY_EVIDENCE_TAMPERED", "TAMPERED_HASH"],
                },
                refs,
                True,
                False,
                True,
                False,
            )

    if capability_decision is None and capability_snapshot is None:
        return (
            {
                "present": False,
                "snapshot_id": None,
                "record_sha256": None,
                "decision": "MISSING",
                "scope_matched": False,
                "tamper_checked": False,
                "reason_codes": ["CAPABILITY_EVIDENCE_MISSING"],
            },
            refs,
            False,
            True,
            True,
            True,
        )

    codes: set[str] = set()
    hold = False
    unknown = False
    scope_ok = True
    snap_id: str | None = None
    rec_sha: str | None = None
    final_decision = "UNKNOWN"

    if capability_decision is not None:
        try:
            if isinstance(capability_decision, bytes):
                dec_dict = parse_json(capability_decision)
            elif isinstance(capability_decision, dict):
                dec_dict = capability_decision
            else:
                raise CapabilityError("INVALID_CAPABILITY_DECISION")
            validate_a07("ChannelCapabilityDecision", dec_dict)
            rec_bytes = canonical_bytes(dec_dict)
            rec_sha = sha256_bytes(rec_bytes)
        except (CapabilityError, ProductError, ValueError, TypeError, KeyError):
            return (
                {
                    "present": True,
                    "snapshot_id": None,
                    "record_sha256": None,
                    "decision": "TAMPERED",
                    "scope_matched": False,
                    "tamper_checked": True,
                    "reason_codes": ["CAPABILITY_EVIDENCE_TAMPERED", "TAMPERED_HASH"],
                },
                refs,
                True,
                False,
                True,
                False,
            )

        req_block = dec_dict["request"]
        if req_block.get("platform") != norm_platform:
            codes.add("PLATFORM_MISMATCH")
            scope_ok = False
            hold = True
        if req_block.get("account_ref") != norm_account:
            codes.add("CROSS_ACCOUNT_MISMATCH")
            scope_ok = False
            hold = True
        if req_block.get("app_ref") is not None and norm_app is not None and req_block.get("app_ref") != norm_app:
            codes.add("APP_MISMATCH")
            scope_ok = False
            hold = True
        if req_block.get("market") is not None and req_block.get("market") != norm_market:
            codes.add("MARKET_MISMATCH")
            scope_ok = False
            hold = True
        if req_block.get("surface") != norm_surface:
            codes.add("SURFACE_MISMATCH")
            scope_ok = False
            hold = True

        snap_ids = dec_dict.get("snapshot_ids", [])
        snap_id = snap_ids[0] if snap_ids else "a07-decision"
        codes.update(dec_dict.get("reason_codes", []))
        raw_dec = dec_dict["decision"]
        if raw_dec == "DENY":
            final_decision = "DENY"
            codes.add("CAPABILITY_DENIED_OR_REVOKED")
            hold = True
        elif raw_dec == "NEEDS_RECHECK":
            final_decision = "NEEDS_RECHECK"
            if any(
                c in codes
                for c in ("SNAPSHOT_EXPIRED", "EVIDENCE_EXPIRED_OR_UNBOUNDED", "SNAPSHOT_FROM_FUTURE")
            ):
                codes.add("CAPABILITY_EXPIRED")
                hold = True
            else:
                codes.add("CAPABILITY_NEEDS_RECHECK")
                unknown = True
        elif raw_dec == "UNKNOWN":
            final_decision = "UNKNOWN"
            codes.add("CAPABILITY_DECISION_UNKNOWN")
            unknown = True
        else:
            final_decision = "ALLOW"
            codes.add("CAPABILITY_VERIFIED")

        refs.append({
            "evidence_id": snap_id,
            "kind": "A07_CAPABILITY_DECISION",
            "record_sha256": rec_sha,
            "source_sha256": None,
            "tamper_checked": True,
        })

    if capability_snapshot is not None:
        try:
            if isinstance(capability_snapshot, bytes):
                snap_dict = parse_json(capability_snapshot)
            elif isinstance(capability_snapshot, dict):
                snap_dict = capability_snapshot
            else:
                raise CapabilityError("INVALID_CAPABILITY_SNAPSHOT")
            if snap_dict.get("schema_version") == "ChannelCapabilitySnapshot.v1":
                validate_a07_snapshot(snap_dict)
            snap_bytes = canonical_bytes(snap_dict)
            snap_hash = sha256_bytes(snap_bytes)
            if rec_sha is None:
                rec_sha = snap_hash
        except (CapabilityError, ProductError, ValueError, TypeError, KeyError):
            return (
                {
                    "present": True,
                    "snapshot_id": None,
                    "record_sha256": None,
                    "decision": "TAMPERED",
                    "scope_matched": False,
                    "tamper_checked": True,
                    "reason_codes": ["CAPABILITY_EVIDENCE_TAMPERED", "TAMPERED_HASH"],
                },
                refs,
                True,
                False,
                True,
                False,
            )

        snap_id = str(snap_dict.get("snapshot_id", snap_id or "ccs_capability_snapshot"))
        snap_platform = snap_dict.get("platform")
        if snap_platform and snap_platform != norm_platform:
            codes.add("PLATFORM_MISMATCH")
            scope_ok = False
            hold = True

        snap_acc = snap_dict.get("account_ref")
        if snap_acc is not None:
            norm_snap_acc = _normalize_account(snap_acc)
            if norm_snap_acc["id"] != norm_account["id"] or (
                snap_acc.get("namespace") if isinstance(snap_acc, dict) else None
            ) not in (None, norm_account["namespace"]):
                codes.add("CROSS_ACCOUNT_MISMATCH")
                scope_ok = False
                hold = True

        snap_app = snap_dict.get("app_ref")
        if snap_app is not None and norm_app is not None:
            norm_snap_app = _normalize_app(snap_app)
            if norm_snap_app and norm_snap_app["id"] != norm_app["id"]:
                codes.add("APP_MISMATCH")
                scope_ok = False
                hold = True

        snap_market = snap_dict.get("market")
        if snap_market and snap_market != norm_market:
            codes.add("MARKET_MISMATCH")
            scope_ok = False
            hold = True

        snap_surface = snap_dict.get("surface")
        if snap_surface and snap_surface != norm_surface:
            codes.add("SURFACE_MISMATCH")
            scope_ok = False
            hold = True

        rev_at = snap_dict.get("revoked_at")
        exp_at = snap_dict.get("expires_at")
        obs_at = snap_dict.get("observed_at")
        snap_state = str(snap_dict.get("state", "UNKNOWN"))

        if rev_at is not None and parse_timestamp(rev_at) <= max(dt_as_of, dt_use_from):
            codes.add("CAPABILITY_DENIED_OR_REVOKED")
            final_decision = "DENY"
            hold = True
        elif snap_state in ("DENIED", "REVOKED", "DENY"):
            codes.add("CAPABILITY_DENIED_OR_REVOKED")
            final_decision = "DENY"
            hold = True
        elif snap_state == "EXPIRED" or (
            exp_at is not None and parse_timestamp(exp_at) <= max(dt_as_of, dt_use_from)
        ):
            codes.add("CAPABILITY_EXPIRED")
            final_decision = "DENY"
            hold = True
        elif obs_at is not None and parse_timestamp(obs_at) > dt_as_of:
            codes.add("CAPABILITY_FROM_FUTURE")
            final_decision = "NEEDS_RECHECK"
            hold = True
        elif snap_state in ("UNKNOWN", "NEEDS_RECHECK", "LIMITED"):
            if capability_decision is None:
                final_decision = "NEEDS_RECHECK" if snap_state != "UNKNOWN" else "UNKNOWN"
                codes.add("CAPABILITY_STATE_UNKNOWN")
                unknown = True
        elif snap_state in ("ALLOWED", "ALLOW"):
            if capability_decision is None:
                final_decision = "ALLOW"
                codes.add("CAPABILITY_VERIFIED")
        else:
            final_decision = "UNKNOWN"
            codes.add("CAPABILITY_STATE_UNKNOWN")
            unknown = True

        refs.append({
            "evidence_id": snap_id,
            "kind": "A07_CAPABILITY_SNAPSHOT",
            "record_sha256": snap_hash,
            "source_sha256": None,
            "tamper_checked": True,
        })

    return (
        {
            "present": True,
            "snapshot_id": snap_id,
            "record_sha256": rec_sha,
            "decision": final_decision,
            "scope_matched": scope_ok,
            "tamper_checked": True,
            "reason_codes": sorted(codes),
        },
        refs,
        hold,
        unknown,
        scope_ok,
        True,
    )


def _revalidate_distribution_handoff(
    distribution_package: Path | str | dict[str, Any] | bytes | None,
    *,
    norm_platform: str,
    norm_account: dict[str, str],
    norm_app: dict[str, str] | None,
    norm_market: str,
    norm_surface: str,
    norm_product: str,
    norm_listing: str | None,
    product_pack_id: str | None,
    norm_release_sha: str,
    disclosure_statement: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool, bool, bool]:
    """Return (comp_dict, refs, hold, scope_ok, hash_ok)."""
    refs: list[dict[str, Any]] = []
    if distribution_package is None:
        return (
            {
                "present": False,
                "package_id": None,
                "package_sha256": None,
                "video_sha256": None,
                "state": "NOT_PROVIDED",
                "scope_matched": True,
                "tamper_checked": False,
                "reason_codes": ["DISTRIBUTION_PACKAGE_NOT_PROVIDED"],
            },
            refs,
            False,
            True,
            True,
        )

    try:
        if isinstance(distribution_package, (Path, str)):
            pkg_dict = inspect_package(distribution_package)
        elif isinstance(distribution_package, bytes):
            pkg_dict = parse_json(distribution_package)
            _check_a08_fields(pkg_dict)
        elif isinstance(distribution_package, dict):
            pkg_dict = dict(distribution_package)
            _check_a08_fields(pkg_dict)
        else:
            raise DistributionError("INVALID_DISTRIBUTION_PACKAGE")
    except (DistributionError, CapabilityError, ProductError, ValueError, TypeError, KeyError, OSError):
        return (
            {
                "present": True,
                "package_id": None,
                "package_sha256": None,
                "video_sha256": None,
                "state": "TAMPERED",
                "scope_matched": False,
                "tamper_checked": True,
                "reason_codes": ["DISTRIBUTION_PACKAGE_TAMPERED", "TAMPERED_HASH"],
            },
            refs,
            True,
            True,
            False,
        )

    codes: set[str] = set()
    hold = False
    scope_ok = True

    if pkg_dict.get("platform") != norm_platform:
        codes.add("PLATFORM_MISMATCH")
        scope_ok = False
        hold = True
    if pkg_dict.get("account_ref") != norm_account:
        codes.add("CROSS_ACCOUNT_MISMATCH")
        scope_ok = False
        hold = True
    if pkg_dict.get("app_ref") is not None and norm_app is not None and pkg_dict.get("app_ref") != norm_app:
        codes.add("APP_MISMATCH")
        scope_ok = False
        hold = True
    if pkg_dict.get("market") is not None and pkg_dict.get("market") != norm_market:
        codes.add("MARKET_MISMATCH")
        scope_ok = False
        hold = True
    if pkg_dict.get("surface") != norm_surface:
        codes.add("SURFACE_MISMATCH")
        scope_ok = False
        hold = True
    if pkg_dict.get("video_sha256") != norm_release_sha:
        codes.add("RELEASE_SHA256_MISMATCH")
        scope_ok = False
        hold = True

    pkg_prod = pkg_dict.get("product_ref")
    allowed_prod_refs = {r for r in (norm_product, norm_listing, product_pack_id) if r}
    if pkg_prod is not None and pkg_prod not in allowed_prod_refs:
        codes.add("PRODUCT_MISMATCH")
        scope_ok = False
        hold = True

    pkg_disc = pkg_dict.get("disclosure")
    if pkg_disc is not None:
        _, _, pkg_disc_ok = _evaluate_disclosure(pkg_disc)
        if not pkg_disc_ok or (disclosure_statement != "UNSPECIFIED" and pkg_disc.strip() != disclosure_statement):
            codes.add("DISCLOSURE_PACKAGE_MISMATCH")
            scope_ok = False
            hold = True

    cap_block = pkg_dict.get("capability", {})
    if cap_block.get("decision") == "DENY":
        codes.add("CAPABILITY_DENIED_OR_REVOKED")
        hold = True

    if not hold:
        codes.add("DISTRIBUTION_HANDOFF_VERIFIED")

    pkg_id = pkg_dict["package_id"]
    pkg_sha = pkg_dict["package_sha256"]
    refs.append({
        "evidence_id": pkg_id,
        "kind": "A08_DISTRIBUTION_PACKAGE",
        "record_sha256": pkg_sha,
        "source_sha256": pkg_dict["source_manifest_sha256"],
        "tamper_checked": True,
    })

    return (
        {
            "present": True,
            "package_id": pkg_id,
            "package_sha256": pkg_sha,
            "video_sha256": pkg_dict["video_sha256"],
            "state": "EXPORTED_FOR_MANUAL_HANDOFF",
            "scope_matched": scope_ok,
            "tamper_checked": True,
            "reason_codes": sorted(codes),
        },
        refs,
        hold,
        scope_ok,
        True,
    )


def _build_release_pack(
    *,
    request: AffiliateReleaseRequest | PlacementRequest | dict[str, Any] | None = None,
    platform: str | None = None,
    account_ref: dict[str, str] | str | None = None,
    app_ref: dict[str, str] | str | None = None,
    market: str | None = None,
    territory: str | None = None,
    surface: str | None = None,
    channel: str | None = None,
    product_id: str | None = None,
    listing_id: str | None = None,
    variant_id: str | None = None,
    release_sha256: str | None = None,
    link_url: str | None = None,
    temporal: PlacementTemporal | dict[str, Any] | None = None,
    as_of: str | None = None,
    valid_from: str | None = None,
    use_from: str | None = None,
    use_until: str | None = None,
    valid_until: str | None = None,
    revoked_at: str | None = None,
    disclosure: Any = _UNSET,
    media_inspection: MediaReleaseInspection | bytes | dict[str, Any] | None = None,
    release_directory: Path | str | None = None,
    release_assets: Iterable[ReleaseAsset] | None = None,
    diagnostics: DiagnosticOptions | None = None,
    limits: InspectionLimits | None = None,
    release_asset_bytes: bytes | None = None,
    claim_rights_review: ClaimRightsReleaseReview | bytes | dict[str, Any] | None = None,
    claims: Iterable[ClaimPlacement] | None = None,
    assets: Iterable[AssetUsage] | None = None,
    claim_evidence: Iterable[ProductClaimEvidence] | None = None,
    rights_evidence: Iterable[ProductAssetRights] | None = None,
    asset_bytes: dict[str, bytes] | None = None,
    rights_status_evidence: Iterable[bytes] | None = None,
    render_manifest: RenderManifest | None = None,
    placement_evidence: AffiliatePlacementEvidence | bytes | dict[str, Any] | None = None,
    product_pack: ProductEvidencePack | bytes | dict[str, Any] | None = None,
    evidence_pack: ProductEvidencePack | bytes | dict[str, Any] | None = None,
    snapshot: ProductListingSnapshot | bytes | dict[str, Any] | None = None,
    snapshot_raw: bytes | None = None,
    offer_observation: ProductOfferObservation | bytes | dict[str, Any] | None = None,
    capability_decision: dict[str, Any] | bytes | None = None,
    capability_snapshot: dict[str, Any] | bytes | None = None,
    capability_registry: CapabilityRegistry | Path | str | None = None,
    capability_action: str = "MANUAL_POST",
    distribution_package: Path | str | dict[str, Any] | bytes | None = None,
    evidence_refs: Iterable[EvidenceRef | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    all_reason_codes: set[str] = set()
    hold = False
    unknown = False
    scope_consistent = True
    hashes_intact = True
    conflicts_detected = False

    if product_pack is None and evidence_pack is not None:
        product_pack = evidence_pack

    # 1. Extract request fields
    if request is not None:
        if isinstance(request, AffiliateReleaseRequest):
            req_dict = {
                "platform": request.platform,
                "account_ref": request.account_ref,
                "app_ref": request.app_ref,
                "market": request.market,
                "territory": request.territory,
                "surface": request.surface,
                "channel": request.channel,
                "product_id": request.product_id,
                "listing_id": request.listing_id,
                "variant_id": request.variant_id,
                "release_sha256": request.release_sha256,
                "link_url": request.link_url,
                "as_of": request.as_of,
                "valid_from": request.valid_from,
                "use_from": request.use_from,
                "use_until": request.use_until,
                "valid_until": request.valid_until,
                "revoked_at": request.revoked_at,
            }
        elif isinstance(request, PlacementRequest):
            req_dict = {
                "account_ref": request.account_ref,
                "app_ref": request.app_ref,
                "market": request.market,
                "surface": request.surface,
                "product_id": request.product_id,
                "variant_id": request.variant_id,
                "link_url": request.link_url,
            }
        elif isinstance(request, dict):
            req_dict = dict(request)
        else:
            raise AffiliateReleaseError("INVALID_RELEASE_REQUEST")
    else:
        req_dict = {}

    acc_raw = account_ref if account_ref is not None else req_dict.get("account_ref")
    app_raw = app_ref if app_ref is not None else req_dict.get("app_ref")
    plat_raw = platform or req_dict.get("platform") or "TIKTOK"
    mkt_raw = market or req_dict.get("market")
    terr_raw = territory or req_dict.get("territory")
    surf_raw = surface or req_dict.get("surface") or "VIDEO"
    chan_raw = channel or req_dict.get("channel") or "social"
    prod_raw = product_id if product_id is not None else req_dict.get("product_id")
    list_raw = listing_id if listing_id is not None else req_dict.get("listing_id")
    var_raw = variant_id if variant_id is not None else req_dict.get("variant_id")
    rel_sha_raw = release_sha256 if release_sha256 is not None else req_dict.get("release_sha256")
    link_raw = link_url if link_url is not None else req_dict.get("link_url")

    _require(acc_raw is not None, "ACCOUNT_REF_REQUIRED")
    _require(link_raw is not None and str(link_raw).strip() != "", "LINK_URL_REQUIRED")
    _require(prod_raw is not None and str(prod_raw).strip() != "", "PRODUCT_ID_REQUIRED")
    _require(var_raw is not None and str(var_raw).strip() != "", "VARIANT_ID_REQUIRED")
    _require(rel_sha_raw is not None and _is_sha256(str(rel_sha_raw)), "RELEASE_SHA256_REQUIRED")

    try:
        norm_account = _normalize_account(acc_raw)
        norm_app = _normalize_app(app_raw)
    except AffiliatePlacementError as exc:
        raise AffiliateReleaseError(exc.code) from exc

    _require(bool(norm_account["id"].strip()), "ACCOUNT_REF_REQUIRED")
    norm_platform = str(plat_raw).upper()
    _require(norm_platform in PLATFORMS, "INVALID_PLATFORM")

    if mkt_raw is not None and terr_raw is not None and str(mkt_raw).upper() != str(terr_raw).upper():
        all_reason_codes.add("MARKET_TERRITORY_MISMATCH")
        all_reason_codes.add("MARKET_MISMATCH")
        scope_consistent = False
        hold = True

    norm_market = str(mkt_raw or terr_raw or "VN").upper()
    norm_territory = str(terr_raw or mkt_raw or "VN").upper()
    _require(
        len(norm_market) == 2 and norm_market.isalpha() and len(norm_territory) == 2 and norm_territory.isalpha(),
        "INVALID_MARKET_OR_TERRITORY",
    )

    norm_surface = str(surf_raw)
    _require(norm_surface in SURFACES, "INVALID_SURFACE")
    norm_channel = str(chan_raw).strip()
    _require(bool(norm_channel), "INVALID_CHANNEL")

    norm_product = str(prod_raw).strip()
    norm_variant = str(var_raw).strip()
    norm_release_sha = str(rel_sha_raw).strip()
    norm_link = str(link_raw).strip()
    norm_listing: str | None = str(list_raw).strip() if list_raw else None

    # Resolve listing_id from product_pack if not explicitly provided
    if norm_listing is None and product_pack is not None:
        try:
            if hasattr(product_pack, "subject"):
                norm_listing = product_pack.subject.get("listing_id")
            elif isinstance(product_pack, dict):
                norm_listing = product_pack.get("subject", {}).get("listing_id")
        except Exception:
            pass

    # 2. Temporal resolution and evaluation
    if temporal is not None:
        if isinstance(temporal, PlacementTemporal):
            t_as_of = as_of or temporal.as_of
            t_valid_from = valid_from or temporal.valid_from
            t_use_from = use_from or temporal.valid_from
            t_use_until = use_until or temporal.valid_until
            t_valid_until = valid_until or temporal.valid_until
            t_revoked = revoked_at if revoked_at is not None else temporal.revoked_at
        elif isinstance(temporal, dict):
            t_as_of = as_of or str(temporal.get("as_of", ""))
            t_valid_from = valid_from or str(temporal.get("valid_from") or temporal.get("use_from") or "")
            t_use_from = use_from or str(temporal.get("use_from") or temporal.get("valid_from") or "")
            t_use_until = use_until or str(temporal.get("use_until") or temporal.get("valid_until") or "")
            t_valid_until = valid_until or str(temporal.get("valid_until") or temporal.get("use_until") or "")
            t_revoked = revoked_at if revoked_at is not None else temporal.get("revoked_at")
        else:
            raise AffiliateReleaseError("INVALID_TEMPORAL_INPUT")
    else:
        t_as_of = as_of or str(req_dict.get("as_of") or "")
        raw_vf = valid_from or req_dict.get("valid_from")
        raw_uf = use_from or req_dict.get("use_from")
        raw_uu = use_until or req_dict.get("use_until")
        raw_vu = valid_until or req_dict.get("valid_until")
        t_revoked = revoked_at if revoked_at is not None else req_dict.get("revoked_at")

        t_valid_from = str(raw_vf or raw_uf or "")
        t_use_from = str(raw_uf or raw_vf or "")
        t_use_until = str(raw_uu or raw_vu or "")
        t_valid_until = str(raw_vu or raw_uu or "")

    _require(
        bool(t_as_of and t_valid_from and t_use_from and t_use_until and t_valid_until),
        "TEMPORAL_FIELDS_REQUIRED",
    )

    temporal_eval, temporal_codes, temporal_status = _evaluate_release_temporal(
        as_of=t_as_of,
        valid_from=t_valid_from,
        use_from=t_use_from,
        use_until=t_use_until,
        valid_until=t_valid_until,
        revoked_at=t_revoked,
    )
    all_reason_codes.update(temporal_codes)
    temporal_active = temporal_status == "ACTIVE"
    if not temporal_active:
        hold = True

    # 3. URL & credential evaluation
    disc_input = req_dict.get("disclosure") if disclosure is _UNSET else disclosure
    payload_probe = (
        f"{norm_account} {norm_app} {norm_link} {disc_input if disc_input is not _UNSET else ''}"
    )
    if _REJECT.search(payload_probe):
        all_reason_codes.add("CREDENTIAL_MATERIAL_NOT_ALLOWED")
        hold = True

    url_eval, url_codes, is_hostile = _evaluate_url(norm_link)
    all_reason_codes.update(url_codes)
    url_safe = not is_hostile
    if is_hostile:
        hold = True

    try:
        query_params = parse_qs(urlsplit(norm_link).query)
        for param in ("affiliate_id", "aff_id", "creator_id", "partner_id", "account_id"):
            if param in query_params and query_params[param][0] != norm_account["id"]:
                all_reason_codes.add("CROSS_ACCOUNT_MISMATCH")
                scope_consistent = False
                hold = True
        for param in ("product_id", "item_id"):
            if param in query_params and query_params[param][0] != norm_product:
                all_reason_codes.add("PRODUCT_MISMATCH")
                scope_consistent = False
                hold = True
        for param in ("variant_id", "sku_id"):
            if param in query_params and query_params[param][0] != norm_variant:
                all_reason_codes.add("VARIANT_MISMATCH")
                scope_consistent = False
                hold = True
        if "market" in query_params and query_params["market"][0].upper() != norm_market:
            all_reason_codes.add("MARKET_MISMATCH")
            scope_consistent = False
            hold = True
    except Exception:
        pass

    # 4. Disclosure evaluation
    disclosure_eval, disclosure_codes, disclosure_compliant = _evaluate_disclosure(
        None if disc_input is _UNSET else disc_input
    )
    all_reason_codes.update(disclosure_codes)
    if not disclosure_compliant:
        hold = True

    # 5. Revalidate C08 Media Inspection
    (
        media_comp,
        verified_media,
        media_refs,
        media_hold,
        media_unknown,
        media_scope_ok,
        media_hash_ok,
    ) = _revalidate_media_inspection(
        media_inspection,
        release_directory=release_directory,
        release_assets=release_assets,
        diagnostics=diagnostics,
        limits=limits,
        release_sha256=norm_release_sha,
        release_asset_bytes=release_asset_bytes,
    )
    all_reason_codes.update(media_comp["reason_codes"])
    hold = hold or media_hold
    unknown = unknown or media_unknown
    scope_consistent = scope_consistent and media_scope_ok
    hashes_intact = hashes_intact and media_hash_ok

    # 6. Revalidate A12 Product Evidence
    (
        prod_comp,
        verified_pack,
        verified_snap,
        prod_refs,
        prod_hold,
        prod_unknown,
        prod_scope_ok,
        prod_hash_ok,
        prod_conflict,
    ) = _revalidate_product_evidence(
        product_pack,
        snapshot=snapshot,
        snapshot_raw=snapshot_raw,
        offer_observation=offer_observation,
        norm_product=norm_product,
        norm_listing=norm_listing,
        norm_variant=norm_variant,
        as_of=t_as_of,
        use_from=t_use_from,
    )
    all_reason_codes.update(prod_comp["reason_codes"])
    hold = hold or prod_hold
    unknown = unknown or prod_unknown
    scope_consistent = scope_consistent and prod_scope_ok
    hashes_intact = hashes_intact and prod_hash_ok
    conflicts_detected = conflicts_detected or prod_conflict
    if norm_listing is None and verified_pack is not None:
        norm_listing = verified_pack.subject.get("listing_id")

    if any(c in prod_comp["reason_codes"] for c in ("OFFER_EXPIRED_OR_STALE", "OFFER_FROM_FUTURE", "PRODUCT_PACK_FROM_FUTURE")):
        temporal_active = False

    # 7. Revalidate C09 Claim & Rights Review
    (
        c09_comp,
        c09_refs,
        c09_hold,
        c09_unknown,
        c09_scope_ok,
        c09_hash_ok,
        c09_conflict,
    ) = _revalidate_claim_rights_review(
        claim_rights_review,
        product_pack=verified_pack,
        snapshot=verified_snap,
        claims=claims,
        assets=assets,
        claim_evidence=claim_evidence,
        rights_evidence=rights_evidence,
        asset_bytes=asset_bytes,
        rights_status_evidence=rights_status_evidence,
        render_manifest=render_manifest,
        media_inspection=verified_media,
        norm_product=norm_product,
        norm_listing=norm_listing,
        norm_variant=norm_variant,
        norm_release_sha=norm_release_sha,
        norm_territory=norm_territory,
        norm_channel=norm_channel,
        as_of=t_as_of,
        use_from=t_use_from,
        use_until=t_use_until,
    )
    all_reason_codes.update(c09_comp["reason_codes"])
    hold = hold or c09_hold
    unknown = unknown or c09_unknown
    scope_consistent = scope_consistent and c09_scope_ok
    hashes_intact = hashes_intact and c09_hash_ok
    conflicts_detected = conflicts_detected or c09_conflict

    if any(
        c in c09_comp["reason_codes"]
        for c in (
            "RIGHTS_REVOKED",
            "RIGHTS_STATUS_RESTRICTED",
            "RIGHTS_EXPIRED",
            "RIGHTS_STATUS_EXPIRED",
            "RIGHTS_USE_BEFORE_TERM",
            "RIGHTS_USE_WINDOW_EXCEEDS_TERM",
            "CLAIM_OBSERVATION_FROM_FUTURE",
            "CLAIM_REVIEW_FROM_FUTURE",
            "RIGHTS_REVIEW_FROM_FUTURE",
            "RIGHTS_STATUS_FROM_FUTURE",
        )
    ):
        temporal_active = False

    # 8. Revalidate D06 Placement Evidence
    (
        d06_comp,
        d06_dict,
        d06_refs,
        d06_hold,
        d06_unknown,
        d06_scope_ok,
        d06_hash_ok,
    ) = _revalidate_placement_evidence(
        placement_evidence,
        norm_account=norm_account,
        norm_app=norm_app,
        norm_market=norm_market,
        norm_surface=norm_surface,
        norm_product=norm_product,
        norm_variant=norm_variant,
        norm_link=norm_link,
    )
    all_reason_codes.update(d06_comp["reason_codes"])
    hold = hold or d06_hold
    unknown = unknown or d06_unknown
    scope_consistent = scope_consistent and d06_scope_ok
    hashes_intact = hashes_intact and d06_hash_ok

    if d06_dict is not None:
        if d06_dict["temporal"]["status"] != "ACTIVE":
            temporal_active = False
            hold = True
        if d06_dict["disclosure"]["status"] != "COMPLIANT":
            disclosure_compliant = False
            hold = True
        elif disc_input is not _UNSET and disc_input is not None:
            if d06_dict["disclosure"]["statement"] != disclosure_eval["statement"]:
                all_reason_codes.add("DISCLOSURE_MISMATCH")
                scope_consistent = False
                hold = True

    # 9. Revalidate A07 Capability Evidence
    (
        cap_comp,
        cap_refs,
        cap_hold,
        cap_unknown,
        cap_scope_ok,
        cap_hash_ok,
    ) = _revalidate_capability_evidence(
        capability_decision=capability_decision,
        capability_snapshot=capability_snapshot,
        capability_registry=capability_registry,
        capability_action=capability_action,
        norm_platform=norm_platform,
        norm_account=norm_account,
        norm_app=norm_app,
        norm_market=norm_market,
        norm_surface=norm_surface,
        as_of=t_as_of,
        use_from=t_use_from,
    )
    all_reason_codes.update(cap_comp["reason_codes"])
    hold = hold or cap_hold
    unknown = unknown or cap_unknown
    scope_consistent = scope_consistent and cap_scope_ok
    hashes_intact = hashes_intact and cap_hash_ok
    if any(c in cap_comp["reason_codes"] for c in ("CAPABILITY_DENIED_OR_REVOKED", "CAPABILITY_EXPIRED", "CAPABILITY_FROM_FUTURE")):
        temporal_active = False

    # 10. Revalidate Optional A08 Manual Distribution Handoff
    (
        a08_comp,
        a08_refs,
        a08_hold,
        a08_scope_ok,
        a08_hash_ok,
    ) = _revalidate_distribution_handoff(
        distribution_package,
        norm_platform=norm_platform,
        norm_account=norm_account,
        norm_app=norm_app,
        norm_market=norm_market,
        norm_surface=norm_surface,
        norm_product=norm_product,
        norm_listing=norm_listing,
        product_pack_id=prod_comp["pack_id"],
        norm_release_sha=norm_release_sha,
        disclosure_statement=disclosure_eval["statement"],
    )
    if a08_comp["present"]:
        all_reason_codes.update(a08_comp["reason_codes"])
    hold = hold or a08_hold
    scope_consistent = scope_consistent and a08_scope_ok
    hashes_intact = hashes_intact and a08_hash_ok

    # 11. Revalidate caller-supplied evidence_refs
    collected_refs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ref_item in media_refs + prod_refs + c09_refs + d06_refs + cap_refs + a08_refs:
        key = (ref_item["kind"], ref_item["evidence_id"], ref_item["record_sha256"])
        collected_refs[key] = ref_item

    if evidence_refs is not None:
        for ref in evidence_refs:
            if isinstance(ref, EvidenceRef):
                r_id = ref.evidence_id
                r_kind = ref.kind
                r_sha = ref.record_sha256
                r_src = ref.source_sha256
                r_tamper = ref.tamper_checked
                raw = ref.raw_bytes
            elif isinstance(ref, dict):
                r_id = str(ref.get("evidence_id", ""))
                r_kind = str(ref.get("kind", ""))
                r_sha = str(ref.get("record_sha256", ""))
                r_src = ref.get("source_sha256")
                r_tamper = bool(ref.get("tamper_checked", True))
                raw = ref.get("raw_bytes")
            else:
                raise AffiliateReleaseError("INVALID_EVIDENCE_REF")

            _require(bool(r_id and r_kind and _is_sha256(r_sha)), "INVALID_EVIDENCE_REF")
            if raw is not None:
                calc_sha = sha256_bytes(raw if isinstance(raw, bytes) else canonical_bytes(raw))
                if calc_sha != r_sha:
                    hashes_intact = False
                    r_tamper = True
                    all_reason_codes.add("TAMPERED_HASH")
                    hold = True
                else:
                    r_tamper = True

            key = (r_kind, r_id, r_sha)
            collected_refs[key] = {
                "evidence_id": r_id,
                "kind": r_kind,
                "record_sha256": r_sha,
                "source_sha256": r_src,
                "tamper_checked": r_tamper,
            }

    sorted_refs = [collected_refs[k] for k in sorted(collected_refs)]

    evidence_complete = (
        media_comp["present"]
        and media_comp["status"] == "PASS"
        and prod_comp["present"]
        and prod_comp["status"] == "VERIFIED"
        and c09_comp["present"]
        and c09_comp["status"] == "VERIFIED_EVIDENCE"
        and d06_comp["present"]
        and d06_comp["decision"] == "REVIEW"
        and cap_comp["present"]
        and cap_comp["decision"] == "ALLOW"
    )

    if (
        not scope_consistent
        or not disclosure_compliant
        or not temporal_active
        or not url_safe
        or not hashes_intact
        or conflicts_detected
    ):
        hold = True

    if hold:
        state = "HOLD"
    elif unknown or not evidence_complete:
        state = "UNKNOWN"
    else:
        state = "READY_FOR_MANUAL_REVIEW"
        all_reason_codes.add("READY_FOR_HUMAN_MANUAL_REVIEW_ONLY")

    body = {
        "schema_version": "AffiliateReleaseEvidencePack.v1",
        "packager_version": "a13-e03/1.0",
        "scope": "ADVISORY_MANUAL_REVIEW_AFFILIATE_RELEASE_EVIDENCE_PACK_ONLY",
        "verification_scope": (
            "LOCAL_OFFLINE_EVIDENCE_REPLAY_ONLY_NOT_LIVE_PROVIDER_OR_LEGAL_APPROVAL"
        ),
        "release_scope": {
            "platform": norm_platform,
            "account_ref": norm_account,
            "app_ref": norm_app,
            "market": norm_market,
            "territory": norm_territory,
            "surface": norm_surface,
            "channel": norm_channel,
            "product_id": norm_product,
            "listing_id": norm_listing,
            "variant_id": norm_variant,
            "release_sha256": norm_release_sha,
            "link_url": norm_link,
        },
        "temporal": temporal_eval,
        "disclosure": disclosure_eval,
        "url_evaluation": url_eval,
        "components": {
            "media_inspection": media_comp,
            "claim_rights_review": c09_comp,
            "placement_evidence": d06_comp,
            "product_evidence": prod_comp,
            "capability_evidence": cap_comp,
            "distribution_handoff": a08_comp,
        },
        "evidence_refs": sorted_refs,
        "revalidation": {
            "scope_consistent": scope_consistent,
            "disclosure_compliant": disclosure_compliant,
            "temporal_active": temporal_active,
            "url_safe": url_safe,
            "hashes_intact": hashes_intact,
            "evidence_complete": evidence_complete,
            "conflicts_detected": conflicts_detected,
        },
        "state": state,
        "decision": state,
        "status": state,
        "reason_codes": sorted(all_reason_codes),
        "advisory_only": True,
        "manual_review_required": True,
        "live_provider_approval": "UNKNOWN",
        "live_link_verified": False,
        "affiliate_link_status": "UNKNOWN_OFFLINE_ONLY",
        "account_eligibility": "UNKNOWN",
        "link_activation": "UNKNOWN",
        "platform_approval": "UNKNOWN",
        "legal_rights_status": "UNKNOWN",
        "publication_status": "NOT_ATTEMPTED",
        "auto_publishing": "NEVER_PERFORMED",
        "oauth_performed": False,
        "network_calls_performed": False,
        "sales_claims_allowed": False,
        "earnings_claims_allowed": False,
        "sales_earnings_claims": "NOT_PERMITTED",
        "revenue_status": "NOT_CLAIMED",
    }

    pack_id = "a13-pack-" + sha256_bytes(canonical_bytes(body))
    result = {**body, "pack_id": pack_id}
    result["record_sha256"] = sha256_bytes(canonical_bytes(result))
    _require(_VALIDATOR.is_valid(result), "PACK_SCHEMA_INVALID")
    _require(len(canonical_bytes(result)) <= _DOCUMENT_LIMIT, "PACK_BYTE_LIMIT")
    return result


def _verify_deserialized_consistency(value: dict[str, Any]) -> None:
    """Reject resealed packs whose internal fields contradict their state/evaluation."""
    _require(
        value["state"] == value["decision"] == value["status"],
        "PACK_STATE_MISMATCH",
    )
    scope = value["release_scope"]
    if _REJECT.search(
        f"{scope['account_ref']} {scope['app_ref']} {scope['link_url']} {value['disclosure']}"
    ):
        _require(
            value["state"] == "HOLD" and "CREDENTIAL_MATERIAL_NOT_ALLOWED" in value["reason_codes"],
            "PACK_CONSISTENCY_INVALID",
        )

    url_eval, _, is_hostile = _evaluate_url(scope["link_url"])
    _require(
        value["url_evaluation"] == url_eval,
        "PACK_URL_EVALUATION_MISMATCH",
    )

    temp = value["temporal"]
    temp_eval, _, temp_status = _evaluate_release_temporal(
        as_of=temp["as_of"],
        valid_from=temp["valid_from"],
        use_from=temp["use_from"],
        use_until=temp["use_until"],
        valid_until=temp["valid_until"],
        revoked_at=temp["revoked_at"],
    )
    _require(value["temporal"] == temp_eval, "PACK_TEMPORAL_MISMATCH")

    disc = value["disclosure"]
    _, _, disc_ok = _evaluate_disclosure(
        PlacementDisclosure(
            statement="" if disc["statement"] == "UNSPECIFIED" else disc["statement"],
            mode=disc["mode"],
            tags=tuple(disc["tags"]),
            has_affiliate_notice=disc["has_affiliate_notice"],
        )
    )
    _require(
        (disc["status"] == "COMPLIANT") == disc_ok,
        "PACK_DISCLOSURE_MISMATCH",
    )

    rev = value["revalidation"]
    comps = value["components"]

    expected_complete = (
        comps["media_inspection"]["present"]
        and comps["media_inspection"]["status"] == "PASS"
        and comps["media_inspection"]["release_asset_matched"] is True
        and comps["media_inspection"]["full_decode"] is True
        and comps["product_evidence"]["present"]
        and comps["product_evidence"]["status"] == "VERIFIED"
        and comps["product_evidence"]["offer_assessment_status"] == "VALID_OBSERVATION"
        and comps["product_evidence"]["stock_available"] is True
        and comps["product_evidence"]["synthetic"] is False
        and comps["claim_rights_review"]["present"]
        and comps["claim_rights_review"]["status"] == "VERIFIED_EVIDENCE"
        and comps["placement_evidence"]["present"]
        and comps["placement_evidence"]["decision"] == "REVIEW"
        and comps["placement_evidence"]["provenance_status"] == "PROVEN"
        and comps["capability_evidence"]["present"]
        and comps["capability_evidence"]["decision"] == "ALLOW"
    )
    _require(rev["evidence_complete"] == expected_complete, "PACK_CONSISTENCY_INVALID")
    _require(rev["url_safe"] == (not is_hostile), "PACK_CONSISTENCY_INVALID")
    _require(rev["disclosure_compliant"] == disc_ok, "PACK_CONSISTENCY_INVALID")
    if temp_status != "ACTIVE":
        _require(rev["temporal_active"] is False, "PACK_CONSISTENCY_INVALID")

    if (
        not rev["scope_consistent"]
        or not rev["disclosure_compliant"]
        or not rev["temporal_active"]
        or not rev["url_safe"]
        or not rev["hashes_intact"]
        or rev["conflicts_detected"]
        or comps["media_inspection"]["status"] in ("FAIL", "TAMPERED")
        or comps["claim_rights_review"]["status"] in ("RESTRICTED", "NEEDS_REVIEW", "TAMPERED")
        or comps["placement_evidence"]["decision"] in ("HOLD", "TAMPERED")
        or comps["product_evidence"]["status"] in ("HOLD", "TAMPERED")
        or comps["capability_evidence"]["decision"] in ("DENY", "TAMPERED")
        or comps["distribution_handoff"]["state"] in ("INVALID", "TAMPERED")
    ):
        _require(value["state"] == "HOLD", "PACK_CONSISTENCY_INVALID")
    elif not rev["evidence_complete"]:
        _require(value["state"] == "UNKNOWN", "PACK_CONSISTENCY_INVALID")
    else:
        _require(value["state"] == "READY_FOR_MANUAL_REVIEW", "PACK_CONSISTENCY_INVALID")


@dataclass(frozen=True, slots=True, init=False)
class AffiliateReleaseEvidencePack:
    """Immutable AffiliateReleaseEvidencePack.v1 advisory manual-review receipt."""

    _encoded: bytes = field(repr=False)

    def __init__(self, **inputs: Any):
        try:
            value = _build_release_pack(**inputs)
            object.__setattr__(self, "_encoded", canonical_bytes(value))
        except AffiliateReleaseError:
            raise
        except (
            AffiliatePlacementError,
            ClaimRightsReviewError,
            MediaInspectionError,
            ProductError,
        ) as exc:
            code = getattr(exc, "code", str(exc))
            raise AffiliateReleaseError(code) from exc
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
            raise AffiliateReleaseError("INVALID_RELEASE_PACK_INPUT") from exc

    @classmethod
    def from_bytes(
        cls,
        raw: bytes,
        *,
        expected_id: str | None = None,
        **inputs: Any,
    ) -> AffiliateReleaseEvidencePack:
        try:
            _require(type(raw) is bytes and 0 < len(raw) <= _DOCUMENT_LIMIT, "PACK_BYTE_LIMIT")
            value = parse_json(raw, limit=_DOCUMENT_LIMIT)
            _require(_VALIDATOR.is_valid(value), "PACK_SCHEMA_INVALID")

            body_no_sha = {k: v for k, v in value.items() if k != "record_sha256"}
            _require(
                value["record_sha256"] == sha256_bytes(canonical_bytes(body_no_sha)),
                "PACK_DIGEST_MISMATCH",
            )

            body_no_id = {k: v for k, v in value.items() if k not in ("pack_id", "record_sha256")}
            expected_pack_id = "a13-pack-" + sha256_bytes(canonical_bytes(body_no_id))
            _require(value["pack_id"] == expected_pack_id, "PACK_ID_MISMATCH")
            if expected_id is not None:
                _require(value["pack_id"] == expected_id, "PACK_ID_MISMATCH")

            _verify_deserialized_consistency(value)

            if inputs:
                expected = cls(**inputs)
                _require(canonical_bytes(value) == expected.to_bytes(), "PACK_REPLAY_MISMATCH")
                return expected

            instance = cls.__new__(cls)
            object.__setattr__(instance, "_encoded", canonical_bytes(value))
            return instance
        except AffiliateReleaseError:
            raise
        except ProductError as exc:
            raise AffiliateReleaseError("PACK_JSON_INVALID") from exc

    def to_dict(self) -> dict[str, Any]:
        return parse_json(self._encoded, limit=_DOCUMENT_LIMIT)

    def to_bytes(self) -> bytes:
        return self._encoded

    @property
    def pack_id(self) -> str:
        return self.to_dict()["pack_id"]

    @property
    def state(self) -> str:
        return self.to_dict()["state"]

    @property
    def decision(self) -> str:
        return self.to_dict()["decision"]

    @property
    def status(self) -> str:
        return self.to_dict()["status"]

    @property
    def reason_codes(self) -> list[str]:
        return self.to_dict()["reason_codes"]

    @property
    def release_scope(self) -> dict[str, Any]:
        return self.to_dict()["release_scope"]

    @property
    def temporal(self) -> dict[str, Any]:
        return self.to_dict()["temporal"]

    @property
    def disclosure(self) -> dict[str, Any]:
        return self.to_dict()["disclosure"]

    @property
    def url_evaluation(self) -> dict[str, Any]:
        return self.to_dict()["url_evaluation"]

    @property
    def components(self) -> dict[str, Any]:
        return self.to_dict()["components"]

    @property
    def revalidation(self) -> dict[str, Any]:
        return self.to_dict()["revalidation"]

    @property
    def evidence_refs(self) -> list[dict[str, Any]]:
        return self.to_dict()["evidence_refs"]

    @property
    def record_sha256(self) -> str:
        return self.to_dict()["record_sha256"]


def build_affiliate_release_pack(**inputs: Any) -> AffiliateReleaseEvidencePack:
    """Build and evaluate an offline advisory AffiliateReleaseEvidencePack.v1."""
    return AffiliateReleaseEvidencePack(**inputs)


def evaluate_affiliate_release_pack(**inputs: Any) -> AffiliateReleaseEvidencePack:
    """Evaluate offline affiliate release evidence and return AffiliateReleaseEvidencePack.v1."""
    return AffiliateReleaseEvidencePack(**inputs)


def review_affiliate_release_pack(**inputs: Any) -> AffiliateReleaseEvidencePack:
    """Review offline affiliate release evidence and return AffiliateReleaseEvidencePack.v1."""
    return AffiliateReleaseEvidencePack(**inputs)


def write_affiliate_release_pack(
    path: Path | str,
    pack: AffiliateReleaseEvidencePack,
    *,
    overwrite: bool = False,
) -> Path:
    """Write canonical UTF-8 AffiliateReleaseEvidencePack.v1 bytes to disk."""
    _require(type(pack) is AffiliateReleaseEvidencePack, "RELEASE_PACK_REQUIRED")
    target = Path(path).resolve()
    if target.exists() and not overwrite:
        raise AffiliateReleaseError("PACK_OUTPUT_ALREADY_EXISTS")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pack.to_bytes())
    return target


def read_affiliate_release_pack(
    path: Path | str,
    *,
    expected_id: str | None = None,
    **inputs: Any,
) -> AffiliateReleaseEvidencePack:
    """Read and strictly validate an AffiliateReleaseEvidencePack.v1 file from disk."""
    target = Path(path).resolve()
    try:
        _require(target.is_file() and not target.is_symlink(), "PACK_FILE_INVALID")
        raw = target.read_bytes()
    except OSError as exc:
        raise AffiliateReleaseError("PACK_FILE_READ_FAILED") from exc
    return AffiliateReleaseEvidencePack.from_bytes(raw, expected_id=expected_id, **inputs)
