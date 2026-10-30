"""C09 local evidence review, never a rights grant, fact checker or release action.

Only supplied bytes are read. References are inert identifiers, never paths to
open or URLs to visit. Original A12 evidence is replayed with its actual APIs;
the summarized pack alone cannot establish reviewed claims or permissions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.product import (
    ProductAssetRights, ProductClaimEvidence, ProductError, ProductEvidencePack,
    ProductListingSnapshot, assess_rights, build_evidence_pack, canonical_bytes,
    parse_json, parse_timestamp,
)
from ai_commerce.render.manifest import RenderManifest

from .claim_rights_schemas import (
    ASSET_USAGE, CLAIM_PLACEMENT, REQUEST, RIGHTS_STATUS_EVIDENCE,
    claim_rights_review_schema,
)
from .inspection import MediaReleaseInspection

_VALIDATOR = Draft202012Validator(claim_rights_review_schema())
_INDEPENDENT = "INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE"
_RANK = {"VERIFIED_EVIDENCE": 0, "UNKNOWN": 1, "NEEDS_REVIEW": 2, "RESTRICTED": 3}
_DOCUMENT_LIMIT = 8 * 1024 * 1024
_ASSET_LIMIT = 256 * 1024 * 1024
_TOTAL_LIMIT = 512 * 1024 * 1024


class ClaimRightsReviewError(ValueError):
    """Stable bounded code; errors do not echo source documents or identifiers."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(ok, code):
    if not ok:
        raise ClaimRightsReviewError(code)


def _shape(value, definition, code):
    canonical_bytes(value)
    _require(Draft202012Validator(definition).is_valid(value), code)


def _aggregate(results):
    return max((r["status"] for r in results), key=_RANK.__getitem__, default="VERIFIED_EVIDENCE")


def _result(status, *codes):
    return {"status": status, "reason_codes": sorted(set(codes))}


def _combined(results, default_code):
    return _result(_aggregate(results), *(set().union(*(r["reason_codes"] for r in results))
                                         if results else {default_code}))


@dataclass(frozen=True)
class ClaimRightsReviewRequest:
    product_id: str
    listing_id: str
    variant_id: str
    release_sha256: str
    territory: str
    channel: str
    as_of: str
    use_from: str
    use_until: str

    def __post_init__(self):
        _shape(asdict(self), REQUEST, "INVALID_REVIEW_REQUEST")
        _require(parse_timestamp(self.as_of) <= parse_timestamp(self.use_from)
                 < parse_timestamp(self.use_until), "INVALID_RELEASE_USE_WINDOW")


@dataclass(frozen=True)
class ClaimPlacement:
    placement_ref: str
    statement: str
    interpretation: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self):
        _require(type(self.evidence_refs) is tuple, "IMMUTABLE_CLAIM_REFS_REQUIRED")
        _shape(self.to_dict(), CLAIM_PLACEMENT, "INVALID_CLAIM_PLACEMENT")

    def to_dict(self):
        return {"placement_ref": self.placement_ref, "statement": self.statement,
                "interpretation": self.interpretation, "evidence_refs": sorted(self.evidence_refs)}


@dataclass(frozen=True)
class AssetUsage:
    asset_ref: str
    content_sha256: str
    media_type: str
    purposes: tuple[str, ...] = ("COMMERCIAL_USE",)

    def __post_init__(self):
        _require(type(self.purposes) is tuple, "IMMUTABLE_ASSET_PURPOSES_REQUIRED")
        _shape(self.to_dict(), ASSET_USAGE, "INVALID_ASSET_USAGE")
        _require("COMMERCIAL_USE" in self.purposes or "ADVERTISEMENT" in self.purposes,
                 "COMMERCIAL_PLACEMENT_PURPOSE_REQUIRED")

    def to_dict(self):
        return {"asset_ref": self.asset_ref, "content_sha256": self.content_sha256,
                "media_type": self.media_type, "purposes": sorted(self.purposes)}


def _records(records, cls, limit):
    _require(type(records) in (tuple, list) and len(records) <= limit
             and all(type(r) is cls for r in records), "INVALID_OR_EXCESS_REVIEW_RECORDS")
    return list(records)


def _replay(pack, snapshot, claims, rights):
    """Reconstruct original records with public A12 APIs and compare projections."""
    _require(type(pack) is ProductEvidencePack, "PRODUCT_EVIDENCE_PACK_REQUIRED")
    _require(len(pack.to_bytes()) <= _DOCUMENT_LIMIT, "REVIEW_BYTE_LIMIT")
    # Even in-memory frozen objects are re-read; summary flags are not credentials.
    checked = ProductEvidencePack.from_bytes(pack.to_bytes(), snapshot=snapshot)
    value = checked.to_dict()
    for name in ("claims", "rights"):
        ids = [r["claim_id" if name == "claims" else "rights_id"] for r in value[name]]
        _require(len(ids) == len(set(ids)), "PACK_DUPLICATE_EVIDENCE_ID")
    claim_map, rights_map = {}, {}
    if snapshot is None:
        _require(not claims and not rights, "ORIGINAL_SNAPSHOT_REQUIRED_FOR_REPLAY")
        return checked, None, claim_map, rights_map
    _require(type(snapshot) is ProductListingSnapshot, "ORIGINAL_SNAPSHOT_REQUIRED")
    snap = ProductListingSnapshot.from_bytes(snapshot.to_bytes(), raw=snapshot.raw_bytes())
    for record in claims:
        item = ProductClaimEvidence.from_bytes(record.to_bytes(), raw=record.raw_bytes(), snapshot=snap,
                                                proof_raw=record.proof_bytes())
        _require(item.to_dict()["subject"] == value["subject"], "CLAIM_EXACT_SUBJECT_MISMATCH")
        _require(item.claim_id not in claim_map, "DUPLICATE_CLAIM_EVIDENCE")
        claim_map[item.claim_id] = item
    for record in rights:
        item = ProductAssetRights.from_bytes(record.to_bytes(), raw=record.raw_bytes(), snapshot=snap,
                                             license_raw=record.license_bytes())
        _require(item.to_dict()["subject"] == value["subject"], "RIGHTS_EXACT_SUBJECT_MISMATCH")
        _require(item.rights_id not in rights_map, "DUPLICATE_RIGHTS_EVIDENCE")
        rights_map[item.rights_id] = item
    replay = build_evidence_pack(snap, ordinal=value["subject"]["ordinal"], claims=list(claim_map.values()),
                                rights=list(rights_map.values())).to_dict()
    for key in ("subject", "source", "listing"):
        _require(replay[key] == value[key], "PACK_SOURCE_REPLAY_MISMATCH")
    for key, id_key in (("claims", "claim_id"), ("rights", "rights_id")):
        original = {r[id_key]: r for r in value[key]}
        for record in replay[key]:
            _require(original.get(record[id_key]) == record, "PACK_" + id_key.split("_")[0].upper()
                     + "_REPLAY_MISMATCH")
    return checked, snap, claim_map, rights_map


def _status_sources(records):
    _require(type(records) in (tuple, list) and len(records) <= 64, "RIGHTS_STATUS_EVIDENCE_LIMIT")
    result = {}
    _require(all(type(raw) is bytes and 0 < len(raw) <= 256 * 1024 for raw in records)
             and sum(map(len, records)) <= 4 * 1024 * 1024, "RIGHTS_STATUS_BYTE_LIMIT")
    for raw in records:
        item = parse_json(raw, limit=256 * 1024)
        _shape(item, RIGHTS_STATUS_EVIDENCE, "INVALID_RIGHTS_STATUS_EVIDENCE")
        observed, effective = (parse_timestamp(item[k]) for k in ("observed_at", "effective_at"))
        until = None if item["valid_until"] is None else parse_timestamp(item["valid_until"])
        _require(until is None or until > effective, "INVALID_RIGHTS_STATUS_TERM")
        if item["state"] == "NOT_REVOKED":
            _require(until is not None and effective <= observed < until, "INVALID_RIGHTS_STATUS_TERM")
        digest = sha256_bytes(raw)
        _require(digest not in result, "DUPLICATE_RIGHTS_STATUS_EVIDENCE")
        result[digest] = {**item, "evidence_sha256": digest}
    return [result[key] for key in sorted(result)]


def _claim_review(placement, pack, originals, request):
    rows = {c["claim_id"]: c for c in pack["claims"]}
    refs = placement.evidence_refs
    if placement.interpretation == "NONFACTUAL":
        if any(ref not in rows or rows[ref]["origin"] != "ILLUSTRATION" for ref in refs):
            return _result("NEEDS_REVIEW", "NONFACTUAL_LABEL_CONFLICTS_WITH_EVIDENCE")
        return _result("VERIFIED_EVIDENCE", "NONFACTUAL_OUTSIDE_FACTUAL_REVIEW")
    if not refs:
        return _result("UNKNOWN", "CLAIM_EVIDENCE_REFS_MISSING")
    results = []
    for ref in refs:
        c = rows.get(ref)
        if c is None:
            results.append(_result("UNKNOWN", "CLAIM_EVIDENCE_REF_NOT_IN_PACK"))
            continue
        family = [r for r in pack["claims"] if (r["claim_key"], r["category"]) == (c["claim_key"], c["category"])
                  and r["origin"] not in ("ILLUSTRATION", "UNKNOWN")]
        signatures = {canonical_bytes({"value": r["value"], "unit": r["unit"]}) for r in family}
        if len(signatures) > 1 or any(r["role"] == "COUNTEREVIDENCE" or r["status"] == "CONFLICT" for r in family):
            results.append(_result("NEEDS_REVIEW", "CLAIM_CONFLICT_OR_COUNTEREVIDENCE"))
        if placement.statement != c["statement"]:
            results.append(_result("NEEDS_REVIEW", "CLAIM_STATEMENT_MISMATCH"))
        if c["role"] != "SUPPORTING":
            results.append(_result("NEEDS_REVIEW", "CLAIM_NOT_SUPPORTING"))
        if c["observed_at"] is not None and parse_timestamp(c["observed_at"]) > parse_timestamp(request.as_of):
            results.append(_result("NEEDS_REVIEW", "CLAIM_OBSERVATION_FROM_FUTURE"))
        if c["status"] == _INDEPENDENT:
            original = originals.get(ref)
            if original is None:
                results.append(_result("UNKNOWN", "CLAIM_REPLAY_EVIDENCE_MISSING"))
            elif (parse_timestamp(c["reviewer_proof"]["reviewed_at"]) > parse_timestamp(request.as_of)
                  or parse_timestamp(original.to_dict()["source"]["captured_at"]) > parse_timestamp(request.as_of)):
                results.append(_result("NEEDS_REVIEW", "CLAIM_REVIEW_FROM_FUTURE"))
            else:
                results.append(_result("VERIFIED_EVIDENCE", "EXACT_LOCAL_CLAIM_EVIDENCE_REPLAYED"))
        else:
            code = {"SELLER_ASSERTED": "SELLER_ASSERTION_NOT_INDEPENDENT",
                    "SOURCE_OBSERVED": "SOURCE_OBSERVATION_NOT_INDEPENDENT",
                    "ILLUSTRATIVE": "ILLUSTRATION_NOT_FACTUAL_EVIDENCE",
                    "CONFLICT": "CLAIM_CONFLICT_OR_COUNTEREVIDENCE", "UNKNOWN": "CLAIM_EVIDENCE_UNKNOWN"}[c["status"]]
            results.append(_result("UNKNOWN" if c["status"] == "UNKNOWN" else "NEEDS_REVIEW", code))
    return _combined(results, "CLAIM_EVIDENCE_UNKNOWN")


def _revocation(summary, purpose, permission, reviewed_at, notices, request):
    # Negative status evidence applies even when the original grant, its review,
    # or the requested purpose is absent. Missing positive proof cannot hide it.
    license_refs = ({permission["license_ref"]} if permission is not None else
                    {p["license_ref"] for p in summary["permissions"] if p["license_ref"] is not None})
    matching = [n for n in notices if n["rights_id"] == summary["rights_id"] and n["purpose"] == purpose]
    issues, current = [], False
    for item in matching:
        if (item["asset_sha256"] != summary["content_sha256"] or item["license_ref"] not in license_refs
                or item["issuer_ref"] != summary["copyright_holder_ref"]):
            issues.append(_result("NEEDS_REVIEW", "RIGHTS_STATUS_SOURCE_MISMATCH"))
        elif item["synthetic"]:
            issues.append(_result("NEEDS_REVIEW", "SYNTHETIC_RIGHTS_STATUS"))
        elif parse_timestamp(item["observed_at"]) > parse_timestamp(request.as_of):
            issues.append(_result("NEEDS_REVIEW", "RIGHTS_STATUS_FROM_FUTURE"))
        elif item["state"] in ("REVOKED", "RESTRICTED"):
            if parse_timestamp(item["effective_at"]) < parse_timestamp(request.use_until):
                issues.append(_result("RESTRICTED", "RIGHTS_REVOKED" if item["state"] == "REVOKED"
                                      else "RIGHTS_STATUS_RESTRICTED"))
        elif item["state"] == "NOT_REVOKED":
            if ((reviewed_at is not None and parse_timestamp(item["observed_at"]) < parse_timestamp(reviewed_at))
                    or parse_timestamp(item["effective_at"]) > parse_timestamp(request.use_from)):
                issues.append(_result("NEEDS_REVIEW", "RIGHTS_STATUS_PREDATES_REVIEW_OR_USE"))
            elif (item["valid_until"] is None or parse_timestamp(item["valid_until"]) <= parse_timestamp(request.as_of)
                  or parse_timestamp(item["valid_until"]) <= parse_timestamp(request.use_from)
                  or parse_timestamp(item["valid_until"]) < parse_timestamp(request.use_until)):
                issues.append(_result("UNKNOWN", "RIGHTS_STATUS_EXPIRED"))
            else:
                current = True
        elif item["state"] == "UNKNOWN":
            issues.append(_result("UNKNOWN", "RIGHTS_REVOCATION_UNKNOWN"))
    if not current and not issues:
        issues.append(_result("UNKNOWN", "RIGHTS_REVOCATION_NOT_EVIDENCED"))
    return _combined(issues, "RIGHTS_NOT_REVOKED_IN_SUPPLIED_EVIDENCE")


def _permission_review(summary, record, purpose, usage, asset_raw, notices, request):
    permission = next((p for p in summary["permissions"] if p["purpose"] == purpose), None)
    original = (None if record is None else
                next((p for p in record.to_dict()["permissions"] if p["purpose"] == purpose), None))
    reviewed_at = None if original is None or original["review"] is None else original["review"]["reviewed_at"]
    results = [_revocation(summary, purpose, permission, reviewed_at, notices, request)]
    if permission is not None and permission["status"] == "RESTRICTED":
        return _combined(results + [_result("RESTRICTED", "RIGHTS_SOURCE_RESTRICTED")], "ASSET_RIGHTS_MISSING")
    if record is None:
        return _combined(results + [_result("UNKNOWN", "RIGHTS_REPLAY_EVIDENCE_MISSING")], "ASSET_RIGHTS_MISSING")
    if permission is None:
        return _combined(results + [_result("UNKNOWN", "RIGHTS_PURPOSE_NOT_EVIDENCED")], "ASSET_RIGHTS_MISSING")
    if permission["status"] != "GRANTED":
        return _combined(results + [_result("UNKNOWN", "RIGHTS_INSTRUMENT_NOT_REVIEWED")], "ASSET_RIGHTS_MISSING")
    if parse_timestamp(original["review"]["reviewed_at"]) > parse_timestamp(request.as_of):
        results.append(_result("NEEDS_REVIEW", "RIGHTS_REVIEW_FROM_FUTURE"))
    scope = original["scope"]
    if request.territory not in scope["territories"] and scope["territories"] != ["WORLDWIDE"]:
        results.append(_result("RESTRICTED", "RIGHTS_TERRITORY_OUTSIDE_SCOPE"))
    if request.channel not in scope["channels"]:
        results.append(_result("RESTRICTED", "RIGHTS_CHANNEL_OUTSIDE_SCOPE"))
    if usage.media_type not in scope["media_types"]:
        results.append(_result("RESTRICTED", "RIGHTS_MEDIA_TYPE_OUTSIDE_SCOPE"))
    start, end = parse_timestamp(request.use_from), parse_timestamp(request.use_until)
    if start < parse_timestamp(scope["valid_from"]):
        results.append(_result("RESTRICTED", "RIGHTS_USE_BEFORE_TERM"))
    if start >= parse_timestamp(scope["valid_until"]):
        results.append(_result("RESTRICTED", "RIGHTS_EXPIRED"))
    elif end > parse_timestamp(scope["valid_until"]):
        results.append(_result("RESTRICTED", "RIGHTS_USE_WINDOW_EXCEEDS_TERM"))
    # Retain A12's concrete local byte/scope assessment as the authority for a grant.
    assessment = assess_rights(record, purpose=purpose, territory=request.territory,
        media_type=usage.media_type, channel=request.channel, as_of=request.use_from, asset_raw=asset_raw)
    if assessment["status"] == "GRANTED":
        results.append(_result("VERIFIED_EVIDENCE", "EXACT_LOCAL_RIGHTS_EVIDENCE_REPLAYED"))
    elif assessment["status"] == "UNKNOWN":
        results.append(_result("UNKNOWN", "ACTUAL_ASSET_BYTES_MISSING"))
    else:
        results.append(_result("RESTRICTED", "A12_EXACT_USE_RESTRICTED"))
    return _combined(results, "RIGHTS_PURPOSE_NOT_EVIDENCED")


def _asset_review(usage, pack, originals, asset_raw, notices, request, derivative):
    observed = None if asset_raw is None else sha256_bytes(asset_raw)
    shared = []
    if observed is None:
        shared.append(_result("UNKNOWN", "ACTUAL_ASSET_BYTES_MISSING"))
    elif observed != usage.content_sha256:
        shared.append(_result("RESTRICTED", "ACTUAL_ASSET_HASH_MISMATCH"))
    matching = [r for r in pack["rights"] if r["content_sha256"] == usage.content_sha256
                and r["media_type"] == usage.media_type]
    if not matching:
        same_ref = any(r["asset_ref"] == usage.asset_ref for r in pack["rights"])
        shared.append(_result("RESTRICTED" if same_ref else "UNKNOWN",
                              "RIGHTS_ASSET_HASH_OR_TYPE_MISMATCH" if same_ref else "ASSET_RIGHTS_MISSING"))
    purposes = sorted(set(usage.purposes) | ({"CREATIVE_DERIVATIVE"} if derivative else set()))
    reviews = []
    for purpose in purposes:
        decisions = shared + [_permission_review(r, originals.get(r["rights_id"]), purpose, usage,
                                                 asset_raw, notices, request) for r in matching]
        reviews.append({"purpose": purpose, **_combined(decisions, "ASSET_RIGHTS_MISSING")})
    result = {**usage.to_dict(), "purposes": purposes,
        "observed_sha256": observed, "size_bytes": None if asset_raw is None else len(asset_raw),
        "rights_refs": sorted(r["rights_id"] for r in matching), "purpose_reviews": reviews,
        **_combined(reviews, "ASSET_RIGHTS_MISSING")}
    return result


def _usage_sources(request, assets, raw_by_ref, render_manifest, media_inspection):
    issues, source_hashes, render_hash, inspection_hash = [], set(), None, None
    if media_inspection is not None:
        _require(type(media_inspection) is MediaReleaseInspection, "INVALID_MEDIA_INSPECTION")
        inspection = MediaReleaseInspection.from_bytes(media_inspection.to_bytes()).to_dict()
        pins = [a for a in inspection["assets"] if a["expected_sha256"] == request.release_sha256]
        _require(bool(pins), "MEDIA_INSPECTION_RELEASE_MISMATCH")
        _require(all(a["observed_sha256"] in (None, request.release_sha256) for a in pins),
                 "MEDIA_INSPECTION_BYTES_MISMATCH")
        inspection_hash = sha256_bytes(media_inspection.to_bytes())
    if render_manifest is not None:
        _require(type(render_manifest) is RenderManifest, "INVALID_RENDER_MANIFEST")
        render = RenderManifest.from_dict(render_manifest.to_dict()).to_dict()
        _require(render["state"] == "SUCCEEDED" and render["output"]["sha256"] == request.release_sha256,
                 "RENDER_RELEASE_MISMATCH")
        render_hash = sha256_bytes(canonical_bytes(render))
        source_hashes = set(render["source_sha256s"])
        used = {a.content_sha256 for a in assets}
        if not source_hashes <= used:
            issues.append(_result("RESTRICTED", "RENDER_SOURCE_USAGE_MISSING"))
        if used - source_hashes - {request.release_sha256}:
            issues.append(_result("NEEDS_REVIEW", "ASSET_USAGE_NOT_IN_RENDER"))
        mode = "RENDER_SOURCE_HASHES" if not issues else "UNPROVEN"
    else:
        direct = (len(assets) == 1 and assets[0].content_sha256 == request.release_sha256
                  and raw_by_ref.get(assets[0].asset_ref) is not None
                  and sha256_bytes(raw_by_ref[assets[0].asset_ref]) == request.release_sha256)
        mode = "DIRECT_ASSET_BYTES" if direct else "UNPROVEN"
        if assets and not direct:
            issues.append(_result("UNKNOWN", "ACTUAL_USAGE_NOT_EVIDENCED"))
    if not assets:
        issues.append(_result("UNKNOWN", "ACTUAL_ASSET_USAGE_MISSING"))
    return mode, source_hashes, render_hash, inspection_hash, issues


def _build(pack, *, request, claims=(), assets=(), snapshot=None, claim_evidence=(), rights_evidence=(),
           asset_bytes=None, rights_status_evidence=(), render_manifest=None, media_inspection=None):
    _require(type(request) is ClaimRightsReviewRequest, "REVIEW_REQUEST_REQUIRED")
    # Revalidate frozen public input objects as well as their contents.
    request = ClaimRightsReviewRequest(**asdict(request))
    placements = _records(claims, ClaimPlacement, 128)
    usages = _records(assets, AssetUsage, 64)
    for item in placements + usages:
        item.__post_init__()
    _require(len({c.placement_ref for c in placements}) == len(placements), "DUPLICATE_CLAIM_PLACEMENT")
    _require(len({a.asset_ref for a in usages}) == len(usages), "DUPLICATE_ASSET_USAGE")
    original_claims = _records(claim_evidence, ProductClaimEvidence, 2000)
    original_rights = _records(rights_evidence, ProductAssetRights, 100)
    evidence_size = sum(len(c.to_bytes()) + len(c.raw_bytes()) + len(c.proof_bytes() or b"")
                        for c in original_claims)
    evidence_size += sum(len(r.to_bytes()) + len(r.raw_bytes()) + sum(map(len, r.license_bytes().values()))
                         for r in original_rights)
    _require(evidence_size <= 32 * 1024 * 1024, "ORIGINAL_EVIDENCE_BYTE_LIMIT")
    pack, snap, original_claims, original_rights = _replay(pack, snapshot, original_claims, original_rights)
    value = pack.to_dict()
    raw_by_ref = {} if asset_bytes is None else asset_bytes
    _require(type(raw_by_ref) is dict and len(raw_by_ref) <= 64
             and set(raw_by_ref) <= {a.asset_ref for a in usages}, "INVALID_ASSET_BYTES_MAP")
    _require(all(type(raw) is bytes and 0 < len(raw) <= _ASSET_LIMIT for raw in raw_by_ref.values())
             and sum(map(len, raw_by_ref.values())) <= _TOTAL_LIMIT, "ASSET_BYTES_LIMIT")
    raw_by_ref = dict(raw_by_ref)
    notices = _status_sources(rights_status_evidence)
    known_rights = {r["rights_id"] for r in value["rights"]}
    _require(all(n["rights_id"] in known_rights for n in notices), "RIGHTS_STATUS_REF_NOT_IN_PACK")
    mode, derivative_hashes, render_hash, inspection_hash, usage_issues = _usage_sources(
        request, usages, raw_by_ref, render_manifest, media_inspection)
    common = []
    for key, code in (("product_id", "PRODUCT_MISMATCH"), ("listing_id", "LISTING_MISMATCH"),
                      ("variant_id", "VARIANT_MISMATCH")):
        if value["subject"][key] != getattr(request, key):
            common.append(_result("RESTRICTED", code))
    if snap is None:
        common.append(_result("UNKNOWN", "ORIGINAL_SNAPSHOT_REQUIRED"))
    if value["subject"]["synthetic"]:
        common.append(_result("NEEDS_REVIEW", "SYNTHETIC_PRODUCT_EVIDENCE"))
    if parse_timestamp(value["generated_at"]) > parse_timestamp(request.as_of):
        common.append(_result("NEEDS_REVIEW", "PRODUCT_PACK_FROM_FUTURE"))
    claim_reviews = [{**p.to_dict(), **_claim_review(p, value, original_claims, request)}
                     for p in sorted(placements, key=lambda p: p.placement_ref)]
    asset_reviews = [_asset_review(a, value, original_rights, raw_by_ref.get(a.asset_ref), notices,
                                   request, a.content_sha256 in derivative_hashes)
                     for a in sorted(usages, key=lambda a: a.asset_ref)]
    claim_decision = _combined(claim_reviews + common, "NO_FACTUAL_CLAIMS_DECLARED")
    rights_decision = _combined(asset_reviews + common + usage_issues, "ACTUAL_ASSET_USAGE_MISSING")
    result = {
        "schema_version": "ClaimRightsReleaseReview.v1", "reviewer_version": "a13-c09/1.0",
        "scope": "COMMERCIAL_PLACEMENT_CLAIMS_AND_RIGHTS_ONLY",
        "verification_scope": "LOCAL_EVIDENCE_REPLAY_ONLY_NOT_EXTERNAL_OR_LEGAL_APPROVAL",
        "source_authenticity": "NOT_ATTESTED", "claim_coverage": "CALLER_DECLARED_NOT_EXHAUSTIVE",
        "request": asdict(request), "subject": value["subject"], "pack_id": pack.pack_id,
        "pack_sha256": sha256_bytes(pack.to_bytes()),
        "snapshot_sha256": None if snap is None else sha256_bytes(snap.to_bytes()),
        "media_inspection_sha256": inspection_hash, "render_manifest_sha256": render_hash,
        "usage_verification": mode,
        "claim_evidence_refs": [{"claim_id": c.claim_id, "record_sha256": sha256_bytes(c.to_bytes()),
            "source_sha256": sha256_bytes(c.raw_bytes()),
            "proof_sha256": None if c.proof_bytes() is None else sha256_bytes(c.proof_bytes())}
            for _, c in sorted(original_claims.items())],
        "rights_evidence_refs": [{"rights_id": r.rights_id, "record_sha256": sha256_bytes(r.to_bytes()),
            "source_sha256": sha256_bytes(r.raw_bytes()),
            "license_sha256s": [{"purpose": p, "sha256": sha256_bytes(raw)} for p, raw in sorted(r.license_bytes().items())]}
            for _, r in sorted(original_rights.items())],
        "rights_status_evidence": notices, "claim_reviews": claim_reviews, "asset_reviews": asset_reviews,
        "claim_status": claim_decision["status"], "rights_status": rights_decision["status"],
        **_combined([claim_decision, rights_decision], "ACTUAL_ASSET_USAGE_MISSING"),
        "legal_rights_status": "UNKNOWN", "platform_approval": "UNKNOWN", "account_eligibility": "UNKNOWN",
        "link_activation": "UNKNOWN", "publication_status": "NOT_ATTEMPTED",
    }
    result["record_sha256"] = sha256_bytes(canonical_bytes(result))
    _require(_VALIDATOR.is_valid(result), "REVIEW_SCHEMA_INVALID")
    _require(len(canonical_bytes(result)) <= _DOCUMENT_LIMIT, "REVIEW_BYTE_LIMIT")
    return result


@dataclass(frozen=True, slots=True, init=False)
class ClaimRightsReleaseReview:
    """Immutable receipt. Restoration always replays the caller's original inputs."""

    _encoded: bytes = field(repr=False)

    def __init__(self, pack: ProductEvidencePack, **inputs):
        try:
            value = _build(pack, **inputs)
            object.__setattr__(self, "_encoded", canonical_bytes(value))
        except ClaimRightsReviewError:
            raise
        except ProductError as exc:
            raise ClaimRightsReviewError("A12_" + exc.code) from exc
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
            raise ClaimRightsReviewError("INVALID_REVIEW_INPUT") from exc

    @classmethod
    def from_bytes(cls, raw: bytes, pack: ProductEvidencePack, **inputs) -> ClaimRightsReleaseReview:
        try:
            _require(type(raw) is bytes and 0 < len(raw) <= _DOCUMENT_LIMIT, "REVIEW_BYTE_LIMIT")
            value = parse_json(raw, limit=_DOCUMENT_LIMIT)
            _require(_VALIDATOR.is_valid(value), "REVIEW_SCHEMA_INVALID")
            body = {k: v for k, v in value.items() if k != "record_sha256"}
            _require(value["record_sha256"] == sha256_bytes(canonical_bytes(body)), "REVIEW_DIGEST_MISMATCH")
            expected = cls(pack, **inputs)
            _require(canonical_bytes(value) == expected.to_bytes(), "REVIEW_REPLAY_MISMATCH")
            return expected
        except ClaimRightsReviewError:
            raise
        except ProductError as exc:
            raise ClaimRightsReviewError("REVIEW_JSON_INVALID") from exc

    def to_dict(self) -> dict:
        return parse_json(self._encoded, limit=_DOCUMENT_LIMIT)

    def to_bytes(self) -> bytes:
        return self._encoded

    @property
    def status(self) -> str:
        return self.to_dict()["status"]


def review_claim_rights(pack: ProductEvidencePack, **inputs) -> ClaimRightsReleaseReview:
    """Review explicit commercial placements; never perform or authorize release.

    ``request`` is required. Optional inputs are ``snapshot``, ``claims``
    (ClaimPlacement), ``assets`` (AssetUsage), original A12 ``claim_evidence``
    and ``rights_evidence``, ``asset_bytes`` by logical reference, bounded JSON
    ``rights_status_evidence``, and existing A05/C08 ``render_manifest`` and
    ``media_inspection``. Missing evidence remains explicit in the receipt.
    """
    return ClaimRightsReleaseReview(pack, **inputs)
