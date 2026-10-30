"""Advisory Product Evidence Pack: synthesizing variant, claims, offers and rights.

The ProductEvidencePack is a self-contained, immutable advisory artifact
designed for Creative and Opportunity consultation. It preserves source listing
facts and exact variant identity, differentiates source-observed/seller-asserted
claims from independently verified claims with reviewer proof, assesses offer
freshness/staleness and pricing conflicts, and encapsulates asset rights manifests
with strict UNKNOWN/RESTRICTED protections.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from ai_commerce.core.provenance import sha256_bytes

from .claims import ProductClaimEvidence
from .core import (
    ProductError, canonical_bytes, content_id, parse_json, parse_timestamp,
    require, seal_contract, validate_contract,
)
from .evidence import subject_for
from .evidence_schemas import CLAIM_STATES, MAX_FRESHNESS_SECONDS
from .offers import ProductOfferObservation, _freshness, assess_offers
from .pack_schemas import ADVISORY_CAVEATS, PACK_VERSION
from .reader import ProductListingSnapshot
from .registry import EvidenceEvent, ProductEvidenceRegistry
from .rights import ProductAssetRights
from .schemas import MAX_DOCUMENT_BYTES, SOURCE_STATUS


def _verify_pack(value: dict, *, raw: bytes | None = None, snapshot=None) -> None:
    """Semantic validation of ProductEvidencePack."""
    # Timestamp checks
    gen_time = parse_timestamp(value["generated_at"])
    cap_time = parse_timestamp(value["source"]["captured_at"])
    as_of_time = parse_timestamp(value["offers"]["as_of"])
    require(gen_time >= cap_time, "PACK_GENERATED_BEFORE_CAPTURE")

    # Snapshot subject checks
    if snapshot is not None:
        require(value["subject"]["snapshot_id"] == snapshot.snapshot_id, "PACK_SNAPSHOT_MISMATCH")
        require(value["subject"]["snapshot_raw_sha256"] == snapshot.to_dict()["source"]["raw_sha256"],
                "PACK_SNAPSHOT_RAW_MISMATCH")
        ordinal = value["subject"]["ordinal"]
        listings = snapshot.to_dict()["listings"]
        require(1 <= ordinal <= len(listings), "INVALID_LISTING_ORDINAL")
        row = listings[ordinal - 1]
        require(value["subject"]["listing_id"] == row["identity"]["listing_id"],
                "PACK_SUBJECT_LISTING_MISMATCH")
        require(value["subject"]["variant_id"] == row["variant"]["variant_id"],
                "PACK_SUBJECT_VARIANT_MISMATCH")
        require(value["listing"]["marketplace"] == row["identity"]["marketplace"],
                "PACK_LISTING_MARKETPLACE_MISMATCH")

    # Claims checks: observed seller claims vs verified-only-with-proof
    for claim in value["claims"]:
        if claim["status"] == CLAIM_STATES[2]:  # INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE
            require(claim["has_independent_proof"] is True, "PROOF_FLAG_MISMATCH")
            require(claim["reviewer_proof"] is not None, "REVIEWER_PROOF_REQUIRED")
            require(not value["subject"]["synthetic"], "SYNTHETIC_CANNOT_BE_VERIFIED")
            require(claim["origin"] in ("SELLER", "OBSERVER"), "CLAIM_NOT_VERIFIABLE")
            proof = claim["reviewer_proof"]
            require(proof["verification_scope"] == "EXPLICIT_REVIEW_OF_LOCAL_EVIDENCE_ONLY",
                    "INVALID_VERIFICATION_SCOPE")
        else:
            require(claim["has_independent_proof"] is False, "PROOF_FLAG_MISMATCH")
            require(claim["reviewer_proof"] is None, "UNEXPECTED_REVIEW_EVIDENCE")
            require(claim["status"] != CLAIM_STATES[2], "UNVERIFIED_CLAIM_UPGRADE")

        if claim["origin"] == "SELLER":
            require(claim["status"] in ("SELLER_ASSERTED", CLAIM_STATES[2]),
                    "INVALID_SELLER_CLAIM_STATUS")
        elif claim["origin"] == "UNKNOWN":
            require(claim["value"] is None and claim["role"] == "INCONCLUSIVE",
                    "INVALID_UNKNOWN_CLAIM")

    # Offers checks: freshness, expiration, conflict assessment
    max_age = value["offers"]["freshness_limit_seconds"]
    require(0 <= max_age <= MAX_FRESHNESS_SECONDS, "INVALID_FRESHNESS_LIMIT")
    observations = value["offers"]["observations"]
    for obs in observations:
        exp_freshness, exp_reasons = _freshness(obs, as_of_time, max_age)
        require(obs["freshness"] == exp_freshness, "OFFER_FRESHNESS_MISMATCH")
        require(sorted(obs["reason_codes"]) == sorted(exp_reasons), "OFFER_REASONS_MISMATCH")

    conflict_fields = value["offers"]["conflict_fields"]
    assessment_status = value["offers"]["assessment_status"]
    active_offer = value["offers"]["active_offer"]

    if not observations:
        require(assessment_status == "NO_OFFERS" and active_offer is None, "INVALID_OFFER_ASSESSMENT")
    elif conflict_fields:
        require(assessment_status == "CONFLICT" and active_offer is None, "INVALID_OFFER_ASSESSMENT")
    elif active_offer is not None:
        require(assessment_status == "VALID_OBSERVATION", "INVALID_OFFER_ASSESSMENT")
        require(any(obs["offer_id"] == active_offer["offer_id"] and obs["freshness"] == "RECENT_OBSERVATION"
                    for obs in observations), "ACTIVE_OFFER_NOT_RECENT")
    else:
        require(assessment_status in ("STALE", "NOT_YET_OBSERVED"), "INVALID_OFFER_ASSESSMENT")

    # Rights checks: UNKNOWN/RESTRICTED protection
    for r_item in value["rights"]:
        for perm in r_item["permissions"]:
            if perm["status"] == "GRANTED":
                require(perm["declared_status"] == "GRANTED", "RIGHTS_STATUS_UPGRADE")
                require(perm["reviewed"] is True, "REVIEW_REQUIRED_FOR_GRANT")
                require(not value["subject"]["synthetic"], "SYNTHETIC_CANNOT_BE_GRANTED")
            elif perm["declared_status"] in ("UNKNOWN", "RESTRICTED"):
                require(perm["status"] == perm["declared_status"], "RIGHTS_STATUS_UPGRADE")
                require(perm["reviewed"] is False, "UNEXPECTED_RIGHTS_REVIEW")

    if any(r["effective_status"] == "RESTRICTED" for r in value["rights"]):
        require(value["rights_status"] in ("RESTRICTED", "MIXED", "UNKNOWN"),
                "INVALID_PACK_RIGHTS_STATUS")
    if value["rights_status"] == "GRANTED":
        require(value["rights"] and all(r["effective_status"] == "GRANTED" for r in value["rights"]),
                "INVALID_PACK_RIGHTS_STATUS")

    # Human advisory caveats check
    require(all(caveat in value["advisory_caveats"] for caveat in ADVISORY_CAVEATS),
            "ADVISORY_CAVEATS_INCOMPLETE")


@dataclass(frozen=True, slots=True, init=False)
class ProductEvidencePack:
    """Immutable, versioned Product Evidence Pack for advisory consultation."""

    _encoded: bytes = field(repr=False)

    def __init__(self, value: dict, *, snapshot=None):
        checked = validate_contract("ProductEvidencePack", value, snapshot=snapshot)
        object.__setattr__(self, "_encoded", canonical_bytes(checked))

    @classmethod
    def from_bytes(cls, encoded: bytes, *, snapshot=None) -> ProductEvidencePack:
        require(type(encoded) is bytes and 0 < len(encoded) <= MAX_DOCUMENT_BYTES,
                "DOCUMENT_EMPTY_OR_TOO_LARGE")
        return cls(parse_json(encoded), snapshot=snapshot)

    def to_dict(self) -> dict:
        return parse_json(self._encoded)

    def to_bytes(self) -> bytes:
        return self._encoded

    @property
    def pack_id(self) -> str:
        return self.to_dict()["pack_id"]

    @property
    def subject(self) -> dict:
        return self.to_dict()["subject"]

    @property
    def listing(self) -> dict:
        return self.to_dict()["listing"]

    @property
    def source(self) -> dict:
        return self.to_dict()["source"]

    @property
    def claims(self) -> list[dict]:
        return self.to_dict()["claims"]

    @property
    def offers(self) -> dict:
        return self.to_dict()["offers"]

    @property
    def rights(self) -> list[dict]:
        return self.to_dict()["rights"]

    @property
    def advisory_caveats(self) -> list[str]:
        return self.to_dict()["advisory_caveats"]

    @property
    def generated_at(self) -> str:
        return self.to_dict()["generated_at"]

    @property
    def rights_status(self) -> str:
        return self.to_dict()["rights_status"]

    @property
    def affiliate_status(self) -> str:
        return self.to_dict()["affiliate_status"]

    @property
    def publication_status(self) -> str:
        return self.to_dict()["publication_status"]


def build_evidence_pack(
    snapshot: ProductListingSnapshot,
    *,
    ordinal: int = 1,
    claims: Sequence[ProductClaimEvidence] = (),
    offers: Sequence[ProductOfferObservation] = (),
    rights: Sequence[ProductAssetRights] = (),
    as_of: str | None = None,
    max_age_seconds: int = 30 * 24 * 3600,
    generated_at: str | None = None,
    registry_event_refs: Sequence[str] = (),
) -> ProductEvidencePack:
    """Build a versioned ProductEvidencePack from validated in-memory components."""
    require(isinstance(snapshot, ProductListingSnapshot), "LISTING_SNAPSHOT_REQUIRED")
    snap_dict = snapshot.to_dict()
    require(1 <= ordinal <= len(snap_dict["listings"]), "INVALID_LISTING_ORDINAL")
    row = snap_dict["listings"][ordinal - 1]
    identity, variant = row["identity"], row["variant"]
    subject = subject_for(snapshot, ordinal, exact_variant=False)

    listing = {
        "marketplace": identity["marketplace"],
        "seller_key": identity["asserted_keys"]["seller"],
        "listing_key": identity["asserted_keys"]["listing"],
        "title": row.get("title"),
        "description": row.get("description"),
        "listing_url": row.get("listing_url"),
        "sku": variant.get("asserted_sku"),
        "gtin": variant.get("asserted_gtin"),
        "variant_attributes": variant.get("attributes", {}),
    }

    source = {
        "source_ref": snap_dict["source"]["source_ref"],
        "source_kind": snap_dict["source"]["source_kind"],
        "source_status": snap_dict["source"]["source_status"],
        "captured_at": snap_dict["source"]["captured_at"],
        "raw_sha256": snap_dict["source"]["raw_sha256"],
        "raw_ref": snap_dict["source"]["raw_ref"],
        "raw_size_bytes": snap_dict["source"]["raw_size_bytes"],
        "pointer": row.get("source_pointer") or "",
    }

    # Process claims
    validated_claims = []
    for claim in claims:
        require(isinstance(claim, ProductClaimEvidence), "INVALID_EVIDENCE_RECORD")
        c_dict = claim.to_dict()
        require(c_dict["subject"]["listing_id"] == subject["listing_id"], "CLAIM_SUBJECT_MISMATCH")
        if subject["variant_id"] is not None and c_dict["subject"]["variant_id"] is not None:
            require(c_dict["subject"]["variant_id"] == subject["variant_id"], "CLAIM_VARIANT_MISMATCH")
        validated_claims.append({
            "claim_id": c_dict["claim_id"],
            "claim_key": c_dict["claim"]["claim_key"],
            "category": c_dict["claim"]["category"],
            "statement": c_dict["claim"]["statement"],
            "value": c_dict["claim"]["value"],
            "unit": c_dict["claim"]["unit"],
            "origin": c_dict["claim"]["origin"],
            "role": c_dict["claim"]["role"],
            "status": c_dict["status"],
            "observed_at": c_dict["claim"]["observed_at"],
            "has_independent_proof": c_dict["status"] == CLAIM_STATES[2],
            "reviewer_proof": c_dict["reviewer_proof"],
            "conflict_refs": sorted(c_dict["claim"].get("conflict_refs", [])),
        })
    validated_claims.sort(key=lambda item: (item["claim_key"], item["claim_id"]))

    # Process offers
    eval_as_of = as_of or snap_dict["source"]["captured_at"]
    if offers:
        for offer in offers:
            require(isinstance(offer, ProductOfferObservation), "INVALID_EVIDENCE_RECORD")
            o_dict = offer.to_dict()
            require(o_dict["subject"]["listing_id"] == subject["listing_id"], "OFFER_SUBJECT_MISMATCH")
            if subject["variant_id"] is not None:
                require(o_dict["subject"]["variant_id"] == subject["variant_id"], "OFFER_VARIANT_MISMATCH")
        assessment = assess_offers(offers, as_of=eval_as_of, max_age_seconds=max_age_seconds)
        matching_group = next((g for g in assessment["groups"]
                               if g["listing_id"] == subject["listing_id"]
                               and g["variant_id"] == subject["variant_id"]), None)
        if matching_group:
            pack_observations = []
            for obs_item in matching_group["observations"]:
                o = obs_item["observation"]
                pack_observations.append({
                    "offer_id": o["offer_id"],
                    "observed_at": o["observed_at"],
                    "expires_at": o["expires_at"],
                    "price": o["price"],
                    "stock": o["stock"],
                    "commission": o["commission"],
                    "freshness": obs_item["freshness"],
                    "reason_codes": sorted(obs_item["reason_codes"]),
                })
            pack_observations.sort(key=lambda item: item["observed_at"], reverse=True)
            conflict_fields = sorted(matching_group["conflicting_fields"])
            active_obs = [obs for obs in pack_observations if obs["freshness"] == "RECENT_OBSERVATION"]
            if conflict_fields:
                assessment_status = "CONFLICT"
                active_offer = None
            elif active_obs:
                assessment_status = "VALID_OBSERVATION"
                latest = active_obs[0]
                active_offer = {
                    "offer_id": latest["offer_id"],
                    "price_amount": latest["price"]["amount"],
                    "currency": latest["price"]["currency"],
                    "stock_available": latest["stock"]["available"],
                    "commission_rate": latest["commission"]["rate"],
                    "commission_currency": latest["commission"]["currency"],
                }
            elif any(obs["freshness"] == "STALE" for obs in pack_observations):
                assessment_status = "STALE"
                active_offer = None
            else:
                assessment_status = "NOT_YET_OBSERVED"
                active_offer = None
            pack_offers = {
                "as_of": eval_as_of,
                "freshness_limit_seconds": max_age_seconds,
                "assessment_status": assessment_status,
                "active_offer": active_offer,
                "conflict_fields": conflict_fields,
                "observations": pack_observations,
            }
        else:
            pack_offers = {
                "as_of": eval_as_of,
                "freshness_limit_seconds": max_age_seconds,
                "assessment_status": "NO_OFFERS",
                "active_offer": None,
                "conflict_fields": [],
                "observations": [],
            }
    else:
        pack_offers = {
            "as_of": eval_as_of,
            "freshness_limit_seconds": max_age_seconds,
            "assessment_status": "NO_OFFERS",
            "active_offer": None,
            "conflict_fields": [],
            "observations": [],
        }

    # Process rights
    pack_rights = []
    for r in rights:
        require(isinstance(r, ProductAssetRights), "INVALID_EVIDENCE_RECORD")
        r_dict = r.to_dict()
        require(r_dict["subject"]["listing_id"] == subject["listing_id"], "RIGHTS_SUBJECT_MISMATCH")
        if subject["variant_id"] is not None and r_dict["subject"]["variant_id"] is not None:
            require(r_dict["subject"]["variant_id"] == subject["variant_id"], "RIGHTS_VARIANT_MISMATCH")
        perms = []
        for p in r_dict["permissions"]:
            perms.append({
                "purpose": p["purpose"],
                "declared_status": p["declared_status"],
                "status": p["status"],
                "license_ref": p.get("license_ref"),
                "scope": p.get("scope"),
                "reviewed": p.get("review") is not None,
            })
        perms.sort(key=lambda item: item["purpose"])
        statuses = {p["status"] for p in perms}
        if statuses == {"GRANTED"}:
            eff_status = "GRANTED"
        elif "RESTRICTED" in statuses:
            eff_status = "RESTRICTED"
        elif "GRANTED" in statuses:
            eff_status = "MIXED"
        else:
            eff_status = "UNKNOWN"
        pack_rights.append({
            "rights_id": r_dict["rights_id"],
            "asset_ref": r_dict["asset"]["asset_ref"],
            "content_sha256": r_dict["asset"]["content_sha256"],
            "media_type": r_dict["asset"]["media_type"],
            "origin": r_dict["asset"]["origin"],
            "origin_ref": r_dict["asset"].get("origin_ref"),
            "copyright_holder_ref": r_dict["asset"].get("copyright_holder_ref"),
            "claimant_ref": r_dict["asset"].get("claimant_ref"),
            "effective_status": eff_status,
            "permissions": perms,
        })
    pack_rights.sort(key=lambda item: item["rights_id"])

    if not pack_rights:
        pack_rights_status = "UNKNOWN"
    elif any(r["effective_status"] == "RESTRICTED" for r in pack_rights):
        pack_rights_status = "RESTRICTED"
    elif all(r["effective_status"] == "GRANTED" for r in pack_rights):
        pack_rights_status = "GRANTED"
    elif any(r["effective_status"] == "GRANTED" for r in pack_rights):
        pack_rights_status = "MIXED"
    else:
        pack_rights_status = "UNKNOWN"

    gen_time = generated_at or eval_as_of

    body = {
        "schema_version": "ProductEvidencePack.v1",
        "pack_version": PACK_VERSION,
        "source_status": SOURCE_STATUS,
        "verification_scope": "LOCAL_BYTES_HASH_AND_REPLAY_ONLY",
        "rights_status": pack_rights_status,
        "affiliate_status": "UNKNOWN",
        "publication_status": "NOT_ATTEMPTED",
        "subject": subject,
        "listing": listing,
        "source": source,
        "claims": validated_claims,
        "offers": pack_offers,
        "rights": pack_rights,
        "advisory_caveats": list(ADVISORY_CAVEATS),
        "registry_event_refs": sorted(set(registry_event_refs)),
        "generated_at": gen_time,
    }

    return ProductEvidencePack(seal_contract("ProductEvidencePack", body, snapshot=snapshot), snapshot=snapshot)


def export_pack_from_registry(
    registry: ProductEvidenceRegistry,
    *,
    listing_id: str | None = None,
    variant_id: str | None = None,
    as_of: str | None = None,
    max_age_seconds: int = 30 * 24 * 3600,
    generated_at: str | None = None,
) -> ProductEvidencePack:
    """Export an advisory ProductEvidencePack from an append-only registry."""
    require(isinstance(registry, ProductEvidenceRegistry), "REGISTRY_REQUIRED")
    events = registry.events()
    require(len(events) > 0, "REGISTRY_EMPTY")

    # Find candidate listing IDs from snapshots
    snapshots_found = []
    for ev in events:
        if ev.to_dict()["evidence"]["kind"] == "ProductListingSnapshot":
            snap = ev.evidence()
            snapshots_found.append((ev.event_id, snap))

    require(len(snapshots_found) > 0, "NO_SNAPSHOTS_IN_REGISTRY")

    all_listings = [
        (snap, idx + 1, row["identity"]["listing_id"], row["variant"]["variant_id"], ev_id)
        for ev_id, snap in snapshots_found
        for idx, row in enumerate(snap.to_dict()["listings"])
    ]

    if listing_id is None:
        distinct_ids = sorted({item[2] for item in all_listings})
        require(len(distinct_ids) == 1, "LISTING_ID_REQUIRED")
        listing_id = distinct_ids[0]

    matching = [
        (snap, ord_idx, lid, vid, ev_id)
        for snap, ord_idx, lid, vid, ev_id in all_listings
        if lid == listing_id and (variant_id is None or vid == variant_id)
    ]
    require(len(matching) > 0, "LISTING_NOT_FOUND_IN_REGISTRY")
    target_snap, target_ord, _, _, snap_ev_id = matching[0]

    event_refs = [snap_ev_id]
    claims = []
    offers = []
    rights = []

    for ev in events:
        kind = ev.to_dict()["evidence"]["kind"]
        if kind == "ProductClaimEvidence":
            claim = ev.evidence()
            c_subj = claim.to_dict()["subject"]
            if c_subj["listing_id"] == listing_id:
                if variant_id is None or c_subj["variant_id"] in (None, variant_id):
                    claims.append(claim)
                    event_refs.append(ev.event_id)
        elif kind == "ProductOfferObservation":
            offer = ev.evidence()
            o_subj = offer.to_dict()["subject"]
            if o_subj["listing_id"] == listing_id:
                if variant_id is None or o_subj["variant_id"] == variant_id:
                    offers.append(offer)
                    event_refs.append(ev.event_id)
        elif kind == "ProductAssetRights":
            r_obj = ev.evidence()
            r_subj = r_obj.to_dict()["subject"]
            if r_subj["listing_id"] == listing_id:
                if variant_id is None or r_subj["variant_id"] in (None, variant_id):
                    rights.append(r_obj)
                    event_refs.append(ev.event_id)

    return build_evidence_pack(
        target_snap,
        ordinal=target_ord,
        claims=claims,
        offers=offers,
        rights=rights,
        as_of=as_of,
        max_age_seconds=max_age_seconds,
        generated_at=generated_at,
        registry_event_refs=event_refs,
    )


def write_evidence_pack(path: Path | str, pack: ProductEvidencePack, *, overwrite: bool = False) -> None:
    """Create an explicit local file; never overwrite existing data unless specified."""
    require(isinstance(pack, ProductEvidencePack), "PRODUCT_EVIDENCE_PACK_REQUIRED")
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        target.write_bytes(pack.to_bytes())
        return
    fd = None
    created = False
    try:
        fd = os.open(
            str(target),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        created = True
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(pack.to_bytes())
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        if created:
            target.unlink(missing_ok=True)
        raise ProductError("PACK_OUTPUT_FILE_ERROR") from exc


def read_evidence_pack(path: Path | str, *, expected_id: str | None = None) -> ProductEvidencePack:
    """Read and validate a ProductEvidencePack from a local JSON file."""
    target = Path(path).resolve()
    require(target.is_file(), "PACK_FILE_NOT_FOUND")
    raw = target.read_bytes()
    pack = ProductEvidencePack.from_bytes(raw)
    if expected_id is not None:
        require(pack.pack_id == expected_id, "PACK_ID_MISMATCH")
    return pack
