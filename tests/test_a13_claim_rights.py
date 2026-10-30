"""C09 offline commercial review; all parties and instruments are test fixtures."""
from __future__ import annotations

import json
import shutil
import socket
import struct
import subprocess
import zlib
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.affiliate_release import (
    AssetUsage, ClaimPlacement, ClaimRightsReleaseReview, ClaimRightsReviewError,
    ClaimRightsReviewRequest, asset_from_e01, claim_rights_review_schema,
    inspect_media_release, review_claim_rights,
)
from ai_commerce.product import (
    ProductError, ProductEvidencePack,
    build_claim, build_evidence_pack, build_rights, canonical_bytes, content_id,
    prepare_bytes, review_claim, review_rights,
)

CAPTURE = "2026-10-30T01:00:00Z"
REVIEWED = "2026-10-30T02:00:00Z"
AS_OF = "2026-10-30T03:00:00Z"
START = "2026-10-30T04:00:00Z"
END = "2026-11-01T04:00:00Z"
EXPIRES = "2027-01-01T00:00:00Z"
ASSET = b"local fixture content; rights assessment does not decode media"
ASSET_SHA = sha256(ASSET).hexdigest()
INDEPENDENT = "INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("C09 attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def snapshot(*, synthetic=False, variant="blue", product="product-1"):
    raw = canonical_bytes({"schema_version": "ManualProductListings.v1", "rows": [{
        "marketplace": "fixture-market", "seller_key": "seller-1",
        "listing_key": "listing-1", "product_key": product, "variant_key": variant,
        "title": "Fixture lamp", "variant_attributes": {"color": variant},
    }]})
    return prepare_bytes(raw, file_format="json", source_ref="fixture-listings",
                         captured_at=CAPTURE, synthetic=synthetic)


def claim(snap, *, origin="SELLER", reviewed=False, statement="Fixture lamp uses 5 watts",
          value="5", role="SUPPORTING", key="power"):
    raw = canonical_bytes({"schema_version": "ProductClaimSource.v1", "claim_key": key,
        "category": "PERFORMANCE", "statement": statement, "value": value, "unit": "W",
        "origin": origin, "role": role, "observed_at": CAPTURE})
    record = build_claim(snap, ordinal=1, raw=raw, source_ref="fixture-claim", captured_at=CAPTURE)
    if reviewed:
        record = review_claim(record, proof_raw=b"separate local fixture measurement, not real certification",
            reviewer_ref="fixture-reviewer", producer_ref="fixture-lab", method="fixture review",
            reviewed_at=REVIEWED, evidence_ref="fixture-report", evidence_kind="INDEPENDENT_MEASUREMENT")
    return record


def rights(snap, *, reviewed=True, declared="GRANTED", expiry=EXPIRES,
           territories=("VN",), channels=("social",), purposes=("COMMERCIAL_USE",),
           asset_ref="lamp.png", asset_raw=ASSET):
    asset_hash = sha256(asset_raw).hexdigest()
    scope = {"territories": list(territories), "media_types": ["IMAGE"],
             "channels": list(channels), "valid_from": "2026-01-01T00:00:00Z", "valid_until": expiry}
    raw = canonical_bytes({"schema_version": "ProductAssetRightsSource.v1", "asset": {
        "asset_ref": asset_ref, "content_sha256": asset_hash, "media_type": "IMAGE",
        "origin": "CREATOR_SUPPLIED", "origin_ref": "fixture-origin",
        "copyright_holder_ref": "fixture-holder", "claimant_ref": "fixture-operator",
        "license_label": "CC0 commercial unlimited worldwide (untrusted label)",
    }, "permissions": [{"purpose": p, "declared_status": declared,
        "license_ref": "fixture-license", "scope": scope} for p in purposes]})
    record = build_rights(snap, ordinal=1, raw=raw, source_ref="fixture-rights", captured_at=CAPTURE)
    if reviewed:
        for purpose in purposes:
            instrument = canonical_bytes({"schema_version": "ProductAssetLicenseEvidence.v1",
                "instrument_kind": "HOLDER_PERMISSION", "asset_sha256": asset_hash,
                "copyright_holder_ref": "fixture-holder", "issuer_ref": "fixture-holder",
                "authority_basis": "COPYRIGHT_HOLDER", "license_ref": "fixture-license",
                "issued_at": "2026-01-01T00:00:00Z", "purpose": purpose, "scope": scope,
                "conclusion": "ALLOWS_EXACT_SCOPED_USE"})
            record = review_rights(record, purpose=purpose, license_raw=instrument,
                reviewer_ref="fixture-rights-reviewer", reviewed_at=REVIEWED,
                method="fixture instrument review", evidence_ref="fixture-license-evidence")
    return record


def notice(record, *, state="NOT_REVOKED", effective_at=REVIEWED, observed_at=REVIEWED,
           valid_until=EXPIRES, synthetic=False, purpose="COMMERCIAL_USE", issuer="fixture-holder"):
    return canonical_bytes({"schema_version": "ReleaseRightsStatusEvidence.v1",
        "rights_id": record.rights_id, "asset_sha256": record.to_dict()["asset"]["content_sha256"],
        "license_ref": "fixture-license", "purpose": purpose, "state": state,
        "issuer_ref": issuer, "source_ref": "fixture-holder-status",
        "observed_at": observed_at, "effective_at": effective_at, "valid_until": valid_until,
        "synthetic": synthetic})


def bundle(*, synthetic=False, reviewed_claim=True, reviewed_rights=True, **rights_args):
    snap = snapshot(synthetic=synthetic)
    c = claim(snap, reviewed=reviewed_claim)
    r = rights(snap, reviewed=reviewed_rights, **rights_args)
    pack = build_evidence_pack(snap, claims=[c], rights=[r], generated_at=REVIEWED)
    subject = pack.subject
    req = ClaimRightsReviewRequest(product_id=subject["product_id"], listing_id=subject["listing_id"],
        variant_id=subject["variant_id"], release_sha256=ASSET_SHA, territory="VN", channel="social",
        as_of=AS_OF, use_from=START, use_until=END)
    inputs = dict(request=req, snapshot=snap,
        claims=(ClaimPlacement("caption", c.to_dict()["claim"]["statement"], "FACTUAL", (c.claim_id,)),),
        assets=(AssetUsage("lamp.png", ASSET_SHA, "IMAGE", ("COMMERCIAL_USE",)),),
        claim_evidence=(c,), rights_evidence=(r,), asset_bytes={"lamp.png": ASSET},
        rights_status_evidence=(notice(r),))
    return pack, inputs, c, r


def reasons(review):
    return set(review.to_dict()["reason_codes"])


def test_schema_mirror_and_positive_local_evidence():
    pack, inputs, _, _ = bundle()
    receipt = review_claim_rights(pack, **inputs)
    definition = claim_rights_review_schema()
    Draft202012Validator.check_schema(definition)
    Draft202012Validator(definition).validate(receipt.to_dict())
    path = Path(__file__).resolve().parents[1] / "contracts/ClaimRightsReleaseReview.v1.schema.json"
    assert json.loads(path.read_bytes()) == definition
    assert receipt.status == "VERIFIED_EVIDENCE"
    value = receipt.to_dict()
    assert value["pack_sha256"] == sha256(pack.to_bytes()).hexdigest()
    assert value["legal_rights_status"] == value["platform_approval"] == "UNKNOWN"
    assert value["account_eligibility"] == value["link_activation"] == "UNKNOWN"
    assert value["publication_status"] == "NOT_ATTEMPTED"
    assert ClaimRightsReleaseReview.from_bytes(receipt.to_bytes(), pack, **inputs) == receipt
    assert review_claim_rights(pack, **inputs).to_bytes() == receipt.to_bytes()
    assert receipt.to_bytes() == canonical_bytes(value)
    value["status"] = "RESTRICTED"
    assert receipt.status == "VERIFIED_EVIDENCE"
    with pytest.raises(FrozenInstanceError):
        receipt._encoded = b"tamper"


@pytest.mark.parametrize("origin,code", [("SELLER", "SELLER_ASSERTION_NOT_INDEPENDENT"),
    ("OBSERVER", "SOURCE_OBSERVATION_NOT_INDEPENDENT"), ("ILLUSTRATION", "ILLUSTRATION_NOT_FACTUAL_EVIDENCE")])
def test_unverified_origins_cannot_support_factual_placement(origin, code):
    pack, inputs, _, r = bundle(reviewed_claim=False)
    c = claim(inputs["snapshot"], origin=origin)
    pack = build_evidence_pack(inputs["snapshot"], claims=[c], rights=[r], generated_at=REVIEWED)
    inputs.update(claims=(ClaimPlacement("caption", c.to_dict()["claim"]["statement"], "FACTUAL", (c.claim_id,)),),
                  claim_evidence=(c,))
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "NEEDS_REVIEW"
    assert code in reasons(receipt)


def test_fiction_is_not_universally_a_product_fact():
    pack, inputs, _, _ = bundle(reviewed_claim=False)
    inputs["claims"] = (ClaimPlacement("story", "The moon drinks purple tea", "NONFACTUAL", ()),)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "VERIFIED_EVIDENCE"
    assert "NONFACTUAL_OUTSIDE_FACTUAL_REVIEW" in reasons(receipt)
    assert receipt.to_dict()["claim_coverage"] == "CALLER_DECLARED_NOT_EXHAUSTIVE"


def test_nonfactual_label_does_not_hide_referenced_product_fact():
    pack, inputs, c, _ = bundle()
    inputs["claims"] = (replace(inputs["claims"][0], interpretation="NONFACTUAL"),)
    assert "NONFACTUAL_LABEL_CONFLICTS_WITH_EVIDENCE" in reasons(review_claim_rights(pack, **inputs))


@pytest.mark.parametrize("field,value,code", [
    ("variant_id", "a12-variant-" + "0" * 64, "VARIANT_MISMATCH"),
    ("product_id", "a12-product-" + "0" * 64, "PRODUCT_MISMATCH"),
    ("listing_id", "a12-listing-" + "0" * 64, "LISTING_MISMATCH"),
])
def test_exact_requested_identity(field, value, code):
    pack, inputs, _, _ = bundle()
    inputs["request"] = replace(inputs["request"], **{field: value})
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "RESTRICTED"
    assert code in reasons(receipt)


def test_license_label_and_grant_declaration_are_insufficient():
    pack, inputs, _, _ = bundle(reviewed_rights=False)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "UNKNOWN"
    assert "RIGHTS_INSTRUMENT_NOT_REVIEWED" in reasons(receipt)


@pytest.mark.parametrize("change,code", [
    ({"rights_evidence": ()}, "RIGHTS_REPLAY_EVIDENCE_MISSING"),
    ({"rights_status_evidence": ()}, "RIGHTS_REVOCATION_NOT_EVIDENCED"),
    ({"asset_bytes": {}}, "ACTUAL_ASSET_BYTES_MISSING"),
    ({"snapshot": None, "claim_evidence": (), "rights_evidence": ()}, "ORIGINAL_SNAPSHOT_REQUIRED"),
    ({"claim_evidence": ()}, "CLAIM_REPLAY_EVIDENCE_MISSING"),
])
def test_missing_evidence_is_unknown(change, code):
    pack, inputs, _, _ = bundle()
    inputs.update(change)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "UNKNOWN"
    assert code in reasons(receipt)


@pytest.mark.parametrize("state,code", [("REVOKED", "RIGHTS_REVOKED"), ("RESTRICTED", "RIGHTS_STATUS_RESTRICTED")])
def test_negative_status_cannot_be_overridden_by_current_notice(state, code):
    pack, inputs, _, r = bundle()
    inputs["rights_status_evidence"] += (notice(r, state=state, valid_until=None),)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "RESTRICTED"
    assert code in reasons(receipt)


@pytest.mark.parametrize("missing", ["review", "original", "purpose"])
def test_revocation_is_not_hidden_by_missing_grant_evidence(missing):
    pack, inputs, _, r = bundle(reviewed_rights=missing != "review")
    if missing == "original":
        inputs["rights_evidence"] = ()
    if missing == "purpose":
        inputs["assets"] = (replace(inputs["assets"][0], purposes=("ADVERTISEMENT",)),)
        inputs["rights_status_evidence"] += (notice(r, state="REVOKED", valid_until=None, purpose="ADVERTISEMENT"),)
    else:
        inputs["rights_status_evidence"] += (notice(r, state="REVOKED", valid_until=None),)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "RESTRICTED"
    assert "RIGHTS_REVOKED" in reasons(receipt)


def test_scheduled_revocation_during_use_window():
    pack, inputs, _, r = bundle()
    inputs["rights_status_evidence"] += (notice(r, state="REVOKED", effective_at="2026-10-31T00:00:00Z"),)
    assert review_claim_rights(pack, **inputs).status == "RESTRICTED"


@pytest.mark.parametrize("rights_args,code", [
    ({"expiry": START}, "RIGHTS_EXPIRED"),
    ({"expiry": "2026-10-31T00:00:00Z"}, "RIGHTS_USE_WINDOW_EXCEEDS_TERM"),
    ({"territories": ("US",)}, "RIGHTS_TERRITORY_OUTSIDE_SCOPE"),
    ({"channels": ("other",)}, "RIGHTS_CHANNEL_OUTSIDE_SCOPE"),
    ({"declared": "RESTRICTED", "reviewed_rights": False}, "RIGHTS_SOURCE_RESTRICTED"),
])
def test_scope_and_restriction_boundaries(rights_args, code):
    pack, inputs, _, _ = bundle(**rights_args)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "RESTRICTED"
    assert code in reasons(receipt)


def test_end_of_license_is_exclusive_but_use_end_can_equal_it():
    pack, inputs, _, _ = bundle(expiry=END)
    assert review_claim_rights(pack, **inputs).status == "VERIFIED_EVIDENCE"


@pytest.mark.parametrize("notice_args,code,status", [
    ({"valid_until": START}, "RIGHTS_STATUS_EXPIRED", "UNKNOWN"),
    ({"issuer": "unrelated-party"}, "RIGHTS_STATUS_SOURCE_MISMATCH", "NEEDS_REVIEW"),
    ({"observed_at": START}, "RIGHTS_STATUS_FROM_FUTURE", "NEEDS_REVIEW"),
    ({"synthetic": True}, "SYNTHETIC_RIGHTS_STATUS", "NEEDS_REVIEW"),
    ({"state": "UNKNOWN"}, "RIGHTS_REVOCATION_UNKNOWN", "UNKNOWN"),
])
def test_status_provenance_is_bounded_and_never_authenticated(notice_args, code, status):
    pack, inputs, _, r = bundle()
    inputs["rights_status_evidence"] = (notice(r, **notice_args),)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == status
    assert code in reasons(receipt)


def test_conflicting_unknown_status_is_not_hidden_by_positive_notice():
    pack, inputs, _, r = bundle()
    inputs["rights_status_evidence"] += (notice(r, state="UNKNOWN"),)
    assert review_claim_rights(pack, **inputs).status == "UNKNOWN"


def test_credible_synthetic_fixture_cannot_claim_real_verification():
    pack, inputs, _, _ = bundle(synthetic=True, reviewed_claim=False, reviewed_rights=False)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "NEEDS_REVIEW"
    assert "SYNTHETIC_PRODUCT_EVIDENCE" in reasons(receipt)
    with pytest.raises(ProductError):
        claim(inputs["snapshot"], reviewed=True)
    with pytest.raises(ProductError):
        rights(inputs["snapshot"], reviewed=True)


def test_changed_bytes_are_restricted():
    pack, inputs, _, _ = bundle()
    inputs["asset_bytes"] = {"lamp.png": ASSET + b"changed"}
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "RESTRICTED"
    assert "ACTUAL_ASSET_HASH_MISMATCH" in reasons(receipt)


def test_only_actually_used_assets_affect_rights():
    pack, inputs, c, r = bundle()
    unused = rights(inputs["snapshot"], reviewed=False, declared="RESTRICTED",
                    asset_ref="unused.png", asset_raw=b"unused fixture")
    pack = build_evidence_pack(inputs["snapshot"], claims=[c], rights=[r, unused], generated_at=REVIEWED)
    assert pack.rights_status == "RESTRICTED"
    assert review_claim_rights(pack, **inputs).status == "VERIFIED_EVIDENCE"


def test_missing_usage_cannot_produce_verified_receipt():
    pack, inputs, _, _ = bundle()
    inputs.update(assets=(), asset_bytes={})
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "UNKNOWN"
    assert "ACTUAL_ASSET_USAGE_MISSING" in reasons(receipt)


def test_no_rights_record_for_actual_asset_remains_unknown():
    _, inputs, c, _ = bundle()
    pack = build_evidence_pack(inputs["snapshot"], claims=[c], generated_at=REVIEWED)
    inputs.update(rights_evidence=(), rights_status_evidence=())
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "UNKNOWN"
    assert "ASSET_RIGHTS_MISSING" in reasons(receipt)


def test_review_uses_only_supplied_bytes_and_never_opens_references(monkeypatch):
    import builtins
    pack, inputs, _, _ = bundle()
    logical_ref = "https://example.invalid/do-not-visit"
    inputs["assets"] = (replace(inputs["assets"][0], asset_ref=logical_ref),)
    inputs["asset_bytes"] = {logical_ref: ASSET}
    def forbidden(*args, **kwargs):
        pytest.fail("C09 attempted filesystem or subprocess access")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "VERIFIED_EVIDENCE"
    assert ClaimRightsReleaseReview.from_bytes(receipt.to_bytes(), pack, **inputs) == receipt


def test_paraphrase_and_missing_refs_need_evidence():
    pack, inputs, _, _ = bundle()
    inputs["claims"] = (replace(inputs["claims"][0], statement="Guaranteed to cut every bill in half"),)
    assert "CLAIM_STATEMENT_MISMATCH" in reasons(review_claim_rights(pack, **inputs))
    inputs["claims"] = (replace(inputs["claims"][0], evidence_refs=()),)
    assert review_claim_rights(pack, **inputs).status == "UNKNOWN"


def test_counterevidence_in_same_claim_family_cannot_be_cherry_picked():
    pack, inputs, c, r = bundle()
    counter = claim(inputs["snapshot"], role="COUNTEREVIDENCE", value="12", statement="Measured 12 watts")
    pack = build_evidence_pack(inputs["snapshot"], claims=[c, counter], rights=[r], generated_at=REVIEWED)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "NEEDS_REVIEW"
    assert "CLAIM_CONFLICT_OR_COUNTEREVIDENCE" in reasons(receipt)


def test_original_evidence_from_other_variant_is_rejected():
    pack, inputs, _, _ = bundle()
    inputs["claim_evidence"] = (claim(snapshot(variant="red"), reviewed=True),)
    with pytest.raises(ClaimRightsReviewError):
        review_claim_rights(pack, **inputs)


@pytest.mark.parametrize("field", ["status", "claim_status", "rights_status", "reason_codes", "pack_sha256"])
def test_resealed_receipt_tamper_is_rejected_by_source_replay(field):
    pack, inputs, _, _ = bundle()
    receipt = review_claim_rights(pack, **inputs)
    value = receipt.to_dict()
    value[field] = [] if field == "reason_codes" else ("0" * 64 if field == "pack_sha256" else "UNKNOWN")
    value["record_sha256"] = sha256(canonical_bytes({k: v for k, v in value.items() if k != "record_sha256"})).hexdigest()
    with pytest.raises(ClaimRightsReviewError):
        ClaimRightsReleaseReview.from_bytes(canonical_bytes(value), pack, **inputs)


def test_resealed_false_pack_claim_is_not_accepted_as_verified():
    pack, inputs, _, _ = bundle()
    value = pack.to_dict()
    value["claims"][0]["statement"] = "Guaranteed cure for every illness"
    value["pack_id"] = content_id("ProductEvidencePack", value)
    forged = ProductEvidencePack(value)
    with pytest.raises(ClaimRightsReviewError, match="PACK_CLAIM_REPLAY_MISMATCH"):
        review_claim_rights(forged, **inputs)


def test_false_verified_summary_needs_original_claim_and_proof():
    pack, inputs, c, r = bundle(reviewed_claim=False)
    reviewed = claim(inputs["snapshot"], reviewed=True)
    value = pack.to_dict()
    value["claims"][0].update(status=INDEPENDENT, has_independent_proof=True,
                               reviewer_proof=reviewed.to_dict()["reviewer_proof"])
    value["pack_id"] = content_id("ProductEvidencePack", value)
    forged = ProductEvidencePack(value)
    with pytest.raises(ClaimRightsReviewError, match="PACK_CLAIM_REPLAY_MISMATCH"):
        review_claim_rights(forged, **inputs)
    inputs["claim_evidence"] = ()
    assert review_claim_rights(forged, **inputs).status == "UNKNOWN"


@pytest.mark.parametrize("kind", ["proof", "license", "claim_source", "rights_source"])
def test_tampered_original_records_are_revalidated(kind):
    pack, inputs, c, r = bundle()
    if kind in ("proof", "claim_source"):
        altered = deepcopy(c)
        field = "_proof_raw" if kind == "proof" else "_raw_evidence"
        object.__setattr__(altered, field, b"altered evidence")
        inputs["claim_evidence"] = (altered,)
    else:
        altered = deepcopy(r)
        field, content = ("_licenses", ()) if kind == "license" else ("_raw_evidence", b"altered evidence")
        object.__setattr__(altered, field, content)
        inputs["rights_evidence"] = (altered,)
    with pytest.raises(ClaimRightsReviewError):
        review_claim_rights(pack, **inputs)


def test_resealed_false_pack_rights_are_not_accepted():
    pack, inputs, _, _ = bundle()
    value = pack.to_dict()
    value["rights"][0]["permissions"][0]["scope"]["territories"] = ["WORLDWIDE"]
    value["pack_id"] = content_id("ProductEvidencePack", value)
    forged = ProductEvidencePack(value)
    with pytest.raises(ClaimRightsReviewError, match="PACK_RIGHTS_REPLAY_MISMATCH"):
        review_claim_rights(forged, **inputs)


@pytest.mark.parametrize("raw", [b'{}', b'{"status":1,"status":2}', b'NaN', b'[]', b'\xff'])
def test_malformed_review_json(raw):
    pack, inputs, _, _ = bundle()
    with pytest.raises(ClaimRightsReviewError):
        ClaimRightsReleaseReview.from_bytes(raw, pack, **inputs)


def test_receipt_cannot_be_replayed_after_rights_revocation():
    pack, inputs, _, r = bundle()
    raw = review_claim_rights(pack, **inputs).to_bytes()
    inputs["rights_status_evidence"] += (notice(r, state="REVOKED", valid_until=None),)
    with pytest.raises(ClaimRightsReviewError):
        ClaimRightsReleaseReview.from_bytes(raw, pack, **inputs)


def test_contract_accessor_returns_detached_copy():
    definition = claim_rights_review_schema()
    definition["properties"]["status"] = {"const": "PUBLISHED"}
    assert "enum" in claim_rights_review_schema()["properties"]["status"]


@pytest.mark.parametrize("field", ["platform_approval", "legal_rights_status", "publication_status"])
def test_receipt_cannot_assert_external_approval(field):
    pack, inputs, _, _ = bundle()
    value = review_claim_rights(pack, **inputs).to_dict()
    value[field] = "APPROVED"
    value["record_sha256"] = sha256(canonical_bytes({k: v for k, v in value.items() if k != "record_sha256"})).hexdigest()
    with pytest.raises(ClaimRightsReviewError):
        ClaimRightsReleaseReview.from_bytes(canonical_bytes(value), pack, **inputs)


@pytest.mark.parametrize("change", [{"channel": "social\n"}, {"territory": "VN\n"},
    {"use_from": END}, {"as_of": "2026-10-30T03:00:00"}, {"variant_id": "a12-variant-" + "0" * 64 + "\n"}])
def test_invalid_or_ambiguous_request_rejected(change):
    _, inputs, _, _ = bundle()
    with pytest.raises(ValueError):
        replace(inputs["request"], **change)


@pytest.mark.parametrize("raw", [b'{}', b'{"state":1,"state":2}', b'NaN', b'[]', b'\xff'])
def test_invalid_status_source_rejected(raw):
    pack, inputs, _, _ = bundle()
    inputs["rights_status_evidence"] = (raw,)
    with pytest.raises(ClaimRightsReviewError):
        review_claim_rights(pack, **inputs)


def test_current_status_requires_bounded_dated_source():
    pack, inputs, _, r = bundle()
    for change in ({"valid_until": None}, {"source_ref": ""}, {"source_ref": "fixture\n"},
                   {"observed_at": "2026-10-30T03:00:00"}):
        value = json.loads(notice(r))
        value.update(change)
        inputs["rights_status_evidence"] = (canonical_bytes(value),)
        with pytest.raises(ClaimRightsReviewError):
            review_claim_rights(pack, **inputs)


def test_declarations_and_bytes_are_bounded_and_unambiguous():
    pack, inputs, c, r = bundle()
    for change in ({"claims": inputs["claims"] * 129}, {"assets": inputs["assets"] * 65},
                   {"rights_status_evidence": (notice(r),) * 65},
                   {"claim_evidence": (c, c)}, {"rights_evidence": (r, r)},
                   {"asset_bytes": {"unused": ASSET}}, {"assets": inputs["assets"] * 2},
                   {"asset_bytes": {"lamp.png": bytearray(ASSET)}}):
        with pytest.raises(ClaimRightsReviewError):
            review_claim_rights(pack, **{**inputs, **change})


@pytest.fixture(scope="module")
def real_delivery(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real A05/A06 integration requires local FFmpeg and FFprobe")
    from ai_commerce.e2e import inspect, run
    from ai_commerce.render.manifest import RenderManifest

    root = tmp_path_factory.mktemp("c09-delivery")
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    image_raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\x33\x55\x77" * 16) * 16)) + chunk(b"IEND", b""))
    image = root / "source.png"
    image.write_bytes(image_raw)
    delivery = root / "delivery"
    run("Abstract moon story, no factual product assertion", [image], delivery,
        width=48, height=32, fps=10, frames=3, silence=False)
    index = inspect(delivery)
    render = RenderManifest((delivery / "custody" / index["render"]["manifest_ref"]).read_bytes())
    manifest_hash = sha256((delivery / "e01-manifest.json").read_bytes()).hexdigest()
    inspection = inspect_media_release(delivery, [asset_from_e01(delivery, expected_manifest_sha256=manifest_hash)])
    assert inspection.status == "PASS"
    return image_raw, render, inspection


def render_bundle(real_delivery, *, derivative=True):
    image_raw, render, inspection = real_delivery
    snap = snapshot()
    purposes = ("COMMERCIAL_USE", "CREATIVE_DERIVATIVE") if derivative else ("COMMERCIAL_USE",)
    r = rights(snap, purposes=purposes, asset_ref="source.png", asset_raw=image_raw)
    pack = build_evidence_pack(snap, rights=[r], generated_at=REVIEWED)
    req = ClaimRightsReviewRequest(product_id=pack.subject["product_id"], listing_id=pack.subject["listing_id"],
        variant_id=pack.subject["variant_id"], release_sha256=render.output["sha256"], territory="VN", channel="social",
        as_of=AS_OF, use_from=START, use_until=END)
    return pack, dict(request=req, snapshot=snap,
        assets=(AssetUsage("source.png", sha256(image_raw).hexdigest(), "IMAGE"),),
        rights_evidence=(r,), asset_bytes={"source.png": image_raw},
        rights_status_evidence=tuple(notice(r, purpose=p) for p in purposes),
        render_manifest=render, media_inspection=inspection)


def test_real_a05_a06_c08_fan_in_and_deterministic_replay(real_delivery):
    pack, inputs = render_bundle(real_delivery)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "VERIFIED_EVIDENCE"
    value = receipt.to_dict()
    assert value["usage_verification"] == "RENDER_SOURCE_HASHES"
    assert value["render_manifest_sha256"] and value["media_inspection_sha256"]
    assert value["asset_reviews"][0]["purposes"] == ["COMMERCIAL_USE", "CREATIVE_DERIVATIVE"]
    assert ClaimRightsReleaseReview.from_bytes(receipt.to_bytes(), pack, **inputs) == receipt
    reordered = {**inputs, "rights_status_evidence": tuple(reversed(inputs["rights_status_evidence"]))}
    assert review_claim_rights(pack, **reordered) == receipt


def test_render_cannot_omit_source_asset_usage(real_delivery):
    pack, inputs = render_bundle(real_delivery)
    inputs.update(assets=(), asset_bytes={})
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "RESTRICTED"
    assert "RENDER_SOURCE_USAGE_MISSING" in reasons(receipt)


def test_render_requires_separate_derivative_evidence(real_delivery):
    pack, inputs = render_bundle(real_delivery, derivative=False)
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "UNKNOWN"
    assert "RIGHTS_PURPOSE_NOT_EVIDENCED" in reasons(receipt)


def test_usage_cannot_be_inferred_from_unbound_original_bytes(real_delivery):
    pack, inputs = render_bundle(real_delivery)
    inputs["render_manifest"] = None
    receipt = review_claim_rights(pack, **inputs)
    assert receipt.status == "UNKNOWN"
    assert "ACTUAL_USAGE_NOT_EVIDENCED" in reasons(receipt)


@pytest.mark.parametrize("field,code", [("media_inspection", "MEDIA_INSPECTION_RELEASE_MISMATCH"),
                                      ("render_manifest", "RENDER_RELEASE_MISMATCH")])
def test_existing_media_receipts_are_bound_to_exact_release(real_delivery, field, code):
    pack, inputs, _, _ = bundle()
    inputs[field] = real_delivery[2 if field == "media_inspection" else 1]
    with pytest.raises(ClaimRightsReviewError, match=code):
        review_claim_rights(pack, **inputs)
