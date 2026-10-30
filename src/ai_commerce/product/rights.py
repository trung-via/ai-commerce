"""P05 immutable asset provenance and exact per-purpose local rights review.

Import never grants rights. A copied URL, CC/public-domain label, seller/API
claim or local hash is insufficient. UNKNOWN/RESTRICTED declarations remain
so, including after review attempts. The narrow GRANTED exception requires an
explicit non-synthetic declaration plus separate, structured holder-issued
instrument bytes, complete exact scope and deliberate Human review. Delegated
authority, legal exceptions and unbounded/perpetual terms are UNKNOWN in v1.
Review records a local evidence decision, not legal/license authentication or
independent verification. Use non-personal aliases, never signatures/contact
details. Asset hashes are source assertions; media is never fetched/stored.
Assessing a use requires the actual local bytes as well as scope and time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ai_commerce.core.provenance import sha256_bytes

from .core import (canonical_bytes, content_id, digest, parse_json, parse_timestamp,
                   require, validate_contract)
from .evidence import common_body, raw_payload, validate_payload, verify_source
from .reader import ProductListingSnapshot
from .rights_schemas import (LICENSE_INPUT, MAX_RIGHTS_BYTES, MEDIA_TYPES, PURPOSES,
                             RIGHTS_INPUT)
from .schemas import MAX_RAW_BYTES
from .storage_privacy import check_document, check_raw


def _scope(value: dict | None) -> dict | None:
    if value is None:
        return None
    require(parse_timestamp(value["valid_from"]) < parse_timestamp(value["valid_until"]),
            "RIGHTS_TERM_INVALID")
    require("WORLDWIDE" not in value["territories"] or value["territories"] == ["WORLDWIDE"],
            "RIGHTS_TERRITORY_AMBIGUOUS")
    return {**value, **{key: sorted(value[key]) for key in ("territories", "media_types", "channels")}}


def _project(payload: dict, source_hash: str) -> tuple[dict, list[dict]]:
    validate_payload(payload, RIGHTS_INPUT, "INVALID_RIGHTS_SOURCE")
    check_document(payload)
    asset = {**payload["asset"], "content_ref": "sha256:" + payload["asset"]["content_sha256"],
             "origin_source_sha256": source_hash, "content_verification": "SOURCE_ASSERTED_NOT_RETRIEVED"}
    permissions = []
    seen = set()
    for item in payload["permissions"]:
        require(item["purpose"] not in seen, "DUPLICATE_RIGHTS_PURPOSE")
        seen.add(item["purpose"])
        permissions.append({**item, "scope": _scope(item["scope"]),
                            "status": "RESTRICTED" if item["declared_status"] == "RESTRICTED" else "UNKNOWN",
                            "review": None})
    return asset, sorted(permissions, key=lambda item: item["purpose"])


def permission_digest(value: dict, purpose: str) -> str:
    """Bind a review to exact raw source, listing/variant, asset and declared use."""
    require(type(purpose) is str and purpose in PURPOSES, "INVALID_RIGHTS_PURPOSE")
    permission = next((item for item in value["permissions"] if item["purpose"] == purpose), None)
    require(permission is not None, "RIGHTS_PURPOSE_NOT_FOUND")
    claim = {key: permission[key] for key in ("purpose", "declared_status", "license_ref", "scope")}
    return digest({"subject": value["subject"], "source": value["source"],
                   "asset": value["asset"], "permission": claim})


def _reviewable(value: dict, permission: dict) -> None:
    asset = value["asset"]
    require(permission["declared_status"] == "GRANTED", "RIGHTS_DECLARATION_NOT_GRANTABLE")
    require(not value["subject"]["synthetic"] and asset["origin"] != "UNKNOWN",
            "RIGHTS_NOT_REVIEWABLE")
    require(permission["scope"] is not None and permission["license_ref"] is not None
            and asset["copyright_holder_ref"] is not None and asset["claimant_ref"] is not None
            and asset["origin_ref"] is not None, "RIGHTS_PROVENANCE_INCOMPLETE")


def _verify_review(value: dict, permission: dict, raw: bytes) -> None:
    review, asset = permission["review"], value["asset"]
    _reviewable(value, permission)
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RIGHTS_BYTES, "LICENSE_EVIDENCE_REQUIRED")
    evidence_hash = sha256_bytes(raw)
    require(evidence_hash not in (value["source"]["raw_sha256"], value["subject"]["snapshot_raw_sha256"],
                                 asset["content_sha256"]), "LICENSE_EVIDENCE_NOT_SEPARATE")
    require(review["evidence_sha256"] == evidence_hash and review["evidence_size_bytes"] == len(raw),
            "LICENSE_PROVENANCE_MISMATCH")
    require(review["permission_sha256"] == permission_digest(value, permission["purpose"]),
            "LICENSE_REVIEW_SCOPE_MISMATCH")
    check_raw(raw)
    instrument = parse_json(raw, limit=MAX_RIGHTS_BYTES)
    validate_payload(instrument, LICENSE_INPUT, "INVALID_LICENSE_EVIDENCE")
    require(instrument["asset_sha256"] == asset["content_sha256"]
            and instrument["purpose"] == permission["purpose"]
            and _scope(instrument["scope"]) == permission["scope"]
            and instrument["license_ref"] == permission["license_ref"], "LICENSE_SCOPE_MISMATCH")
    require(instrument["copyright_holder_ref"] == asset["copyright_holder_ref"]
            and instrument["issuer_ref"] == asset["copyright_holder_ref"], "LICENSE_HOLDER_MISMATCH")
    require(review["reviewer_ref"] not in (asset["copyright_holder_ref"], asset["claimant_ref"]),
            "RIGHTS_SEPARATE_REVIEW_REQUIRED")
    reviewed = parse_timestamp(review["reviewed_at"])
    require(reviewed >= parse_timestamp(value["source"]["captured_at"])
            and reviewed >= parse_timestamp(instrument["issued_at"]), "LICENSE_REVIEW_BEFORE_EVIDENCE")


def _verify_rights(value: dict, *, raw: bytes | None, snapshot,
                   license_raw: dict[str, bytes] | None) -> None:
    payload = verify_source(value, raw, snapshot)
    require(len(raw) <= MAX_RIGHTS_BYTES, "RIGHTS_SOURCE_TOO_LARGE")
    check_raw(raw)
    asset, permissions = _project(payload, value["source"]["raw_sha256"])
    require(value["asset"] == asset, "ASSET_REPLAY_MISMATCH")
    require(len(value["permissions"]) == len(permissions), "RIGHTS_REPLAY_MISMATCH")
    proofs = {} if license_raw is None else license_raw
    require(type(proofs) is dict and len(proofs) <= len(PURPOSES), "INVALID_LICENSE_EVIDENCE_MAP")
    granted = set()
    for actual, expected in zip(value["permissions"], permissions):
        require(all(actual[key] == expected[key] for key in
                    ("purpose", "declared_status", "scope", "license_ref")), "RIGHTS_REPLAY_MISMATCH")
        if actual["status"] == "GRANTED":
            require(actual["purpose"] in proofs, "LICENSE_EVIDENCE_REQUIRED")
            _verify_review(value, actual, proofs[actual["purpose"]])
            granted.add(actual["purpose"])
        else:
            require(actual == expected, "RIGHTS_STATUS_UPGRADE")
    require(set(proofs) == granted, "UNEXPECTED_LICENSE_EVIDENCE")
    check_document(value)


@dataclass(frozen=True, slots=True, init=False)
class ProductAssetRights:
    _encoded: bytes = field(repr=False)
    _raw_evidence: bytes = field(repr=False)
    _snapshot: ProductListingSnapshot = field(repr=False)
    _licenses: tuple[tuple[str, bytes], ...] = field(repr=False)

    def __init__(self, value: dict, *, raw: bytes, snapshot: ProductListingSnapshot,
                 license_raw: dict[str, bytes] | None = None):
        checked = validate_contract("ProductAssetRights", value, raw=raw, snapshot=snapshot,
                                    license_raw=license_raw)
        object.__setattr__(self, "_encoded", canonical_bytes(checked))
        object.__setattr__(self, "_raw_evidence", raw)
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_licenses", tuple(sorted((license_raw or {}).items())))

    @classmethod
    def from_bytes(cls, encoded: bytes, *, raw: bytes, snapshot: ProductListingSnapshot,
                   license_raw: dict[str, bytes] | None = None) -> ProductAssetRights:
        return cls(parse_json(encoded), raw=raw, snapshot=snapshot, license_raw=license_raw)

    def to_dict(self) -> dict:
        return parse_json(self._encoded)

    def to_bytes(self) -> bytes:
        return self._encoded

    def raw_bytes(self) -> bytes:
        return self._raw_evidence

    def license_bytes(self) -> dict[str, bytes]:
        return dict(self._licenses)

    @property
    def rights_id(self) -> str:
        return self.to_dict()["rights_id"]


def build_rights(snapshot: ProductListingSnapshot, *, ordinal: int, raw: bytes,
                 source_ref: str, captured_at: str, source_kind: str = "HUMAN_SUPPLIED",
                 pointer: str = "") -> ProductAssetRights:
    """Import source claims only, including untrusted GRANTED/CC declarations."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RIGHTS_BYTES, "RIGHTS_SOURCE_TOO_LARGE")
    body = common_body("ProductAssetRights", snapshot, ordinal, raw, source_ref=source_ref,
                       captured_at=captured_at, source_kind=source_kind, pointer=pointer)
    asset, permissions = _project(raw_payload(raw, pointer), sha256_bytes(raw))
    body.update(asset=asset, permissions=permissions, affiliate_status="UNKNOWN")
    return ProductAssetRights({**body, "rights_id": content_id("ProductAssetRights", body)},
                               raw=raw, snapshot=snapshot)


def review_rights(rights: ProductAssetRights, *, purpose: str, license_raw: bytes,
                  reviewer_ref: str, reviewed_at: str, method: str,
                  evidence_ref: str) -> ProductAssetRights:
    """Explicit scoped review; return a new record while preserving raw claims.

    This function is never called by import, registry replay or consumer export.
    Append the result as a revision to retain the review's preceding observation.
    """
    require(type(rights) is ProductAssetRights, "INVALID_EVIDENCE_RECORD")
    require(type(purpose) is str and purpose in PURPOSES, "INVALID_RIGHTS_PURPOSE")
    require(type(license_raw) is bytes and 0 < len(license_raw) <= MAX_RIGHTS_BYTES,
            "LICENSE_EVIDENCE_REQUIRED")
    body = rights.to_dict()
    body.pop("rights_id")
    permission = next((item for item in body["permissions"] if item["purpose"] == purpose), None)
    require(permission is not None, "RIGHTS_PURPOSE_NOT_FOUND")
    _reviewable(body, permission)
    permission.update(status="GRANTED", review={
        "reviewer_ref": reviewer_ref, "reviewed_at": reviewed_at, "method": method,
        "evidence_ref": evidence_ref, "evidence_sha256": sha256_bytes(license_raw),
        "evidence_size_bytes": len(license_raw), "permission_sha256": permission_digest(body, purpose),
        "verification_scope": "EXPLICIT_LOCAL_LICENSE_REVIEW_ONLY",
    })
    licenses = {**rights.license_bytes(), purpose: license_raw}
    return ProductAssetRights({**body, "rights_id": content_id("ProductAssetRights", body)},
                               raw=rights.raw_bytes(), snapshot=rights._snapshot, license_raw=licenses)


def assess_rights(rights: ProductAssetRights, *, purpose: str, territory: str,
                  media_type: str, channel: str, as_of: str,
                  asset_raw: bytes | None = None) -> dict:
    """Exact local use assessment; a derivative also needs its commercial/ad use.

    GRANTED for one purpose never implies the other two. Missing terms/purposes
    and absent asset bytes remain UNKNOWN. Expired/outside scope is RESTRICTED.
    """
    require(type(rights) is ProductAssetRights, "INVALID_EVIDENCE_RECORD")
    require(type(purpose) is str and purpose in PURPOSES, "INVALID_RIGHTS_PURPOSE")
    require(type(territory) is str and re.fullmatch(r"[A-Z]{2}", territory) is not None,
            "INVALID_RIGHTS_TERRITORY")
    require(type(media_type) is str and media_type in MEDIA_TYPES, "INVALID_RIGHTS_MEDIA_TYPE")
    require(type(channel) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,127}", channel) is not None,
            "INVALID_RIGHTS_CHANNEL")
    instant = parse_timestamp(as_of)
    value = rights.to_dict()
    permission = next((item for item in value["permissions"] if item["purpose"] == purpose), None)
    status = permission["status"] if permission else "UNKNOWN"
    reason = "PURPOSE_NOT_EVIDENCED" if status == "UNKNOWN" else "SOURCE_RESTRICTED"
    if status == "GRANTED":
        scope = permission["scope"]
        inside = ((territory in scope["territories"] or scope["territories"] == ["WORLDWIDE"])
                  and media_type == value["asset"]["media_type"] and media_type in scope["media_types"]
                  and channel in scope["channels"]
                  and parse_timestamp(scope["valid_from"]) <= instant < parse_timestamp(scope["valid_until"]))
        if not inside:
            status, reason = "RESTRICTED", "OUTSIDE_REVIEWED_SCOPE"
        elif asset_raw is None:
            status, reason = "UNKNOWN", "ASSET_BYTES_REQUIRED"
        else:
            require(type(asset_raw) is bytes and 0 < len(asset_raw) <= MAX_RAW_BYTES,
                    "ASSET_EMPTY_OR_TOO_LARGE")
            if sha256_bytes(asset_raw) != value["asset"]["content_sha256"]:
                status, reason = "RESTRICTED", "ASSET_HASH_MISMATCH"
            else:
                reason = "EXACT_LOCAL_REVIEWED_SCOPE"
    return {"schema_version": "ProductAssetUseAssessment.v1", "rights_id": rights.rights_id,
            "purpose": purpose, "territory": territory, "media_type": media_type,
            "channel": channel, "as_of": as_of, "status": status, "reason_code": reason,
            "verification_scope": "LOCAL_EVIDENCE_ONLY_NOT_LEGAL_AUTHENTICATION",
            "source_status": value["source_status"], "publication_status": "NOT_ATTEMPTED"}
