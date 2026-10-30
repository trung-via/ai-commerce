"""P05 local rights evidence: declarations, exact review, scope and non-upgrades."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path
import socket

from jsonschema import Draft202012Validator
import pytest

from ai_commerce.product import (ProductError, content_id, parse_contract, prepare_bytes,
                                 schema, seal_contract, validate_contract)
from ai_commerce.product.rights import (ProductAssetRights, assess_rights, build_rights,
                                        permission_digest, review_rights)
from ai_commerce.product.rights_schemas import MAX_RIGHTS_BYTES, PURPOSES
from ai_commerce.product.schemas import MAX_RAW_BYTES

CAPTURE = "2026-10-30T05:00:00Z"
REVIEWED = "2026-10-30T06:00:00Z"
ASSET = b"offline synthetic image bytes; no real media or license"
HASH = sha256(ASSET).hexdigest()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A12 rights must remain offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def snapshot(*, synthetic=False, variant="red"):
    raw = json.dumps({"schema_version": "ManualProductListings.v1", "rows": [{
        "marketplace": "fixture", "seller_key": "merchant", "listing_key": "item",
        "variant_key": variant}]}).encode()
    return prepare_bytes(raw, file_format="json", source_ref="listing-export", captured_at=CAPTURE,
                         synthetic=synthetic)


def scope(**changes):
    return {"territories": ["VN"], "media_types": ["IMAGE"], "channels": ["social-ad"],
            "valid_from": "2026-10-01T00:00:00Z", "valid_until": "2026-11-01T00:00:00Z", **changes}


def source(*, declared="GRANTED", purposes=PURPOSES, asset=None, use_scope=None, **changes):
    return {"schema_version": "ProductAssetRightsSource.v1", "asset": {
        "asset_ref": "https://invalid.test/image.jpg", "content_sha256": HASH, "media_type": "IMAGE",
        "origin": "CREATOR_SUPPLIED", "origin_ref": "holder-export",
        "copyright_holder_ref": "holder-alias", "claimant_ref": "merchant-alias",
        "license_label": "CC-BY-4.0", **(asset or {})},
        "permissions": [{"purpose": purpose, "declared_status": declared, "license_ref": "license-1",
                         "scope": scope() if use_scope is None else use_scope} for purpose in purposes], **changes}


def rights(*, payload=None, raw=None, listing=None, **metadata):
    return build_rights(snapshot() if listing is None else listing, ordinal=1,
                        raw=json.dumps(source() if payload is None else payload).encode() if raw is None else raw,
                        **{"source_ref": "rights-export", "captured_at": CAPTURE, **metadata})


def instrument(purpose="COMMERCIAL_USE", **changes):
    return json.dumps({"schema_version": "ProductAssetLicenseEvidence.v1",
                       "instrument_kind": "HOLDER_PERMISSION", "asset_sha256": HASH,
                       "copyright_holder_ref": "holder-alias", "issuer_ref": "holder-alias",
                       "authority_basis": "COPYRIGHT_HOLDER", "license_ref": "license-1",
                       "issued_at": "2026-10-01T00:00:00Z", "purpose": purpose, "scope": scope(),
                       "conclusion": "ALLOWS_EXACT_SCOPED_USE", **changes}).encode()


def review(item=None, *, purpose="COMMERCIAL_USE", **changes):
    return review_rights(rights() if item is None else item, purpose=purpose,
                         **{"license_raw": instrument(purpose), "reviewer_ref": "reviewer-alias",
                            "reviewed_at": REVIEWED, "method": "Match local holder instrument to exact asset and use",
                            "evidence_ref": "local-license-export", **changes})


def assessment(item, **changes):
    return assess_rights(item, **{"purpose": "COMMERCIAL_USE", "territory": "VN", "media_type": "IMAGE",
                                  "channel": "social-ad", "as_of": REVIEWED, "asset_raw": ASSET, **changes})


def reseal(value):
    result = deepcopy(value)
    result["rights_id"] = content_id("ProductAssetRights", result)
    return result


@pytest.mark.parametrize("kind", ["HUMAN_SUPPLIED", "PREEXPORTED_API"])
@pytest.mark.parametrize("label", ["CC-BY-4.0", "Creative Commons", "public domain", "royalty free", None])
def test_urls_and_license_labels_never_grant_permission(kind, label):
    raw = b"\xef\xbb\xbf" + json.dumps(source(asset={"license_label": label})).encode() + b"\r\n"
    item = rights(raw=raw, source_kind=kind)
    value = item.to_dict()
    assert item.raw_bytes() == raw
    assert value["source"]["raw_sha256"] == sha256(raw).hexdigest()
    assert value["asset"]["origin_source_sha256"] == sha256(raw).hexdigest()
    assert value["asset"]["content_sha256"] == HASH
    assert value["asset"]["content_ref"] == "sha256:" + HASH
    assert value["asset"]["license_label"] == label
    assert value["asset"]["content_verification"] == "SOURCE_ASSERTED_NOT_RETRIEVED"
    assert {p["status"] for p in value["permissions"]} == {"UNKNOWN"}
    assert value["rights_status"] == value["affiliate_status"] == "UNKNOWN"
    assert value["publication_status"] == "NOT_ATTEMPTED"
    assert value["source_status"] == "USER_SUPPLIED_NOT_LIVE_VERIFIED"
    assert assessment(item)["status"] == "UNKNOWN"


@pytest.mark.parametrize("declared,expected", [("UNKNOWN", "UNKNOWN"), ("RESTRICTED", "RESTRICTED")])
@pytest.mark.parametrize("purpose", PURPOSES)
def test_unknown_and_restricted_cannot_be_reviewed_into_grants(declared, expected, purpose):
    original = rights(payload=source(declared=declared))
    assert assessment(original, purpose=purpose)["status"] == expected
    with pytest.raises(ProductError, match="RIGHTS_DECLARATION_NOT_GRANTABLE"):
        review(original, purpose=purpose)
    assert all(item["status"] == expected for item in original.to_dict()["permissions"])


def test_exact_review_is_immutable_and_only_grants_its_evidenced_purpose():
    original = rights()
    reviewed = review(original)
    assert original.rights_id != reviewed.rights_id
    assert reviewed.raw_bytes() == original.raw_bytes()
    assert assessment(reviewed)["status"] == "GRANTED"
    assert assessment(reviewed, purpose="ADVERTISEMENT")["status"] == "UNKNOWN"
    assert assessment(reviewed, purpose="CREATIVE_DERIVATIVE")["status"] == "UNKNOWN"
    assert all(p["status"] == "UNKNOWN" for p in original.to_dict()["permissions"])
    assert reviewed.to_dict()["rights_status"] == "UNKNOWN"
    assert reviewed.to_dict()["source_status"] == "USER_SUPPLIED_NOT_LIVE_VERIFIED"
    assert review(original) == reviewed
    value = reviewed.to_dict()
    grant = next(p for p in value["permissions"] if p["purpose"] == "COMMERCIAL_USE")
    assert grant["review"]["permission_sha256"] == permission_digest(original.to_dict(), "COMMERCIAL_USE")
    assert ProductAssetRights.from_bytes(reviewed.to_bytes(), raw=reviewed.raw_bytes(), snapshot=reviewed._snapshot,
                                          license_raw=reviewed.license_bytes()) == reviewed
    value["asset"]["license_label"] = "changed"
    detached = reviewed.license_bytes()
    detached.clear()
    assert reviewed.to_dict()["asset"]["license_label"] == "CC-BY-4.0"
    assert reviewed.license_bytes()
    with pytest.raises(FrozenInstanceError):
        reviewed._raw_evidence = b"changed"


def test_three_separate_reviews_are_required_and_retained():
    item = rights()
    for purpose in PURPOSES:
        item = review(item, purpose=purpose)
    assert set(item.license_bytes()) == set(PURPOSES)
    assert all(assessment(item, purpose=purpose)["status"] == "GRANTED" for purpose in PURPOSES)
    assert all(p["review"] is not None for p in item.to_dict()["permissions"])


@pytest.mark.parametrize("changes,status,reason", [
    ({"territory": "US"}, "RESTRICTED", "OUTSIDE_REVIEWED_SCOPE"),
    ({"media_type": "VIDEO"}, "RESTRICTED", "OUTSIDE_REVIEWED_SCOPE"),
    ({"channel": "social-organic"}, "RESTRICTED", "OUTSIDE_REVIEWED_SCOPE"),
    ({"as_of": "2026-09-30T23:59:59Z"}, "RESTRICTED", "OUTSIDE_REVIEWED_SCOPE"),
    ({"as_of": "2026-11-01T00:00:00Z"}, "RESTRICTED", "OUTSIDE_REVIEWED_SCOPE"),
    ({"as_of": "2026-11-01T07:00:00+07:00"}, "RESTRICTED", "OUTSIDE_REVIEWED_SCOPE"),
    ({"as_of": "2026-10-01T00:00:00Z"}, "GRANTED", "EXACT_LOCAL_REVIEWED_SCOPE"),
    ({"as_of": "2026-10-31T23:59:59.999999Z"}, "GRANTED", "EXACT_LOCAL_REVIEWED_SCOPE"),
    ({"asset_raw": None}, "UNKNOWN", "ASSET_BYTES_REQUIRED"),
    ({"asset_raw": b"another image"}, "RESTRICTED", "ASSET_HASH_MISMATCH"),
])
def test_scope_terms_are_exact_and_end_exclusive(changes, status, reason):
    result = assessment(review(), **changes)
    assert (result["status"], result["reason_code"]) == (status, reason)
    assert result["verification_scope"] == "LOCAL_EVIDENCE_ONLY_NOT_LEGAL_AUTHENTICATION"


def test_missing_purpose_and_unknown_terms_stay_unknown_and_worldwide_is_explicit():
    data = source(purposes=("ADVERTISEMENT",))
    data["permissions"][0]["scope"] = None
    item = rights(payload=data)
    assert assessment(item)["status"] == "UNKNOWN"
    with pytest.raises(ProductError, match="RIGHTS_PROVENANCE_INCOMPLETE"):
        review(item, purpose="ADVERTISEMENT")
    with pytest.raises(ProductError, match="RIGHTS_PURPOSE_NOT_FOUND"):
        review(item)
    worldwide = scope(territories=["WORLDWIDE"])
    item = review(rights(payload=source(use_scope=worldwide)), license_raw=instrument(scope=worldwide))
    assert assessment(item, territory="US")["status"] == "GRANTED"


@pytest.mark.parametrize("changes,code", [
    ({"asset_sha256": "f" * 64}, "LICENSE_SCOPE_MISMATCH"),
    ({"purpose": "ADVERTISEMENT"}, "LICENSE_SCOPE_MISMATCH"),
    ({"license_ref": "license-2"}, "LICENSE_SCOPE_MISMATCH"),
    ({"scope": scope(territories=["US"])}, "LICENSE_SCOPE_MISMATCH"),
    ({"scope": scope(channels=["other"])}, "LICENSE_SCOPE_MISMATCH"),
    ({"scope": scope(valid_until="2026-12-01T00:00:00Z")}, "LICENSE_SCOPE_MISMATCH"),
    ({"issuer_ref": "seller-alias"}, "LICENSE_HOLDER_MISMATCH"),
    ({"copyright_holder_ref": "other-holder"}, "LICENSE_HOLDER_MISMATCH"),
    ({"authority_basis": "AUTHORIZED_AGENT"}, "INVALID_LICENSE_EVIDENCE"),
    ({"instrument_kind": "CC_LABEL"}, "INVALID_LICENSE_EVIDENCE"),
    ({"conclusion": "UNKNOWN"}, "INVALID_LICENSE_EVIDENCE"),
    ({"issued_at": "2026-10-31T00:00:00Z"}, "LICENSE_REVIEW_BEFORE_EVIDENCE"),
    ({"issued_at": "2026-02-30T00:00:00Z"}, "INVALID_TIMESTAMP"),
])
def test_direct_instrument_must_match_holder_hash_and_complete_use(changes, code):
    with pytest.raises(ProductError, match=code):
        review(license_raw=instrument(**changes))


@pytest.mark.parametrize("raw", [None, b"", b"https://invalid.test/license", b"CC-BY-4.0", b"{}", b"null",
                                 b"not a rights instrument", b"\0" * (MAX_RIGHTS_BYTES + 1)],
                         ids=["missing", "empty", "url", "label", "object", "null", "text", "oversized"])
def test_missing_url_label_or_arbitrary_attachment_is_insufficient(raw):
    with pytest.raises(ProductError):
        review(license_raw=raw)


@pytest.mark.parametrize("asset_changes", [
    {"origin": "UNKNOWN"}, {"origin_ref": None}, {"copyright_holder_ref": None}, {"claimant_ref": None},
])
def test_incomplete_origin_or_holder_cannot_grant(asset_changes):
    with pytest.raises(ProductError, match="RIGHTS_NOT_REVIEWABLE|RIGHTS_PROVENANCE_INCOMPLETE"):
        review(rights(payload=source(asset=asset_changes)))


def test_synthetic_source_or_self_review_does_not_grant():
    with pytest.raises(ProductError, match="RIGHTS_NOT_REVIEWABLE"):
        review(rights(listing=snapshot(synthetic=True)))
    for reviewer in ("holder-alias", "merchant-alias"):
        with pytest.raises(ProductError, match="RIGHTS_SEPARATE_REVIEW_REQUIRED"):
            review(reviewer_ref=reviewer)
    with pytest.raises(ProductError, match="LICENSE_REVIEW_BEFORE_EVIDENCE"):
        review(reviewed_at="2026-10-30T04:59:59Z")


def test_source_cannot_be_its_own_separate_instrument():
    original = rights()
    for raw in (original.raw_bytes(), original._snapshot.raw_bytes(), ASSET):
        with pytest.raises(ProductError, match="LICENSE_EVIDENCE_NOT_SEPARATE"):
            review(original, license_raw=raw)


def test_content_id_cannot_launder_rights_source_asset_review_or_subject():
    item = rights()
    value = item.to_dict()
    value["asset"]["content_sha256"] = "f" * 64
    with pytest.raises(ProductError, match="ASSET_REPLAY_MISMATCH"):
        ProductAssetRights(reseal(value), raw=item.raw_bytes(), snapshot=item._snapshot)
    value = item.to_dict()
    value["permissions"][0]["declared_status"] = "RESTRICTED"
    value["permissions"][0]["status"] = "RESTRICTED"
    with pytest.raises(ProductError, match="RIGHTS_REPLAY_MISMATCH"):
        ProductAssetRights(reseal(value), raw=item.raw_bytes(), snapshot=item._snapshot)
    with pytest.raises(ProductError, match="PRODUCT_SUBJECT_MISMATCH"):
        ProductAssetRights(item.to_dict(), raw=item.raw_bytes(), snapshot=snapshot(variant="blue"))
    with pytest.raises(ProductError, match="RAW_PROVENANCE_MISMATCH"):
        ProductAssetRights(item.to_dict(), raw=item.raw_bytes() + b"\n", snapshot=item._snapshot)
    reviewed = review(item)
    with pytest.raises(ProductError, match="LICENSE_EVIDENCE_REQUIRED"):
        ProductAssetRights(reviewed.to_dict(), raw=item.raw_bytes(), snapshot=item._snapshot)
    with pytest.raises(ProductError, match="LICENSE_PROVENANCE_MISMATCH"):
        ProductAssetRights(reviewed.to_dict(), raw=item.raw_bytes(), snapshot=item._snapshot,
                           license_raw={"COMMERCIAL_USE": instrument() + b"\n"})
    changed = reviewed.to_dict()
    next(p for p in changed["permissions"] if p["review"])["review"]["permission_sha256"] = "0" * 64
    with pytest.raises(ProductError, match="LICENSE_REVIEW_SCOPE_MISMATCH"):
        ProductAssetRights(reseal(changed), raw=item.raw_bytes(), snapshot=item._snapshot,
                           license_raw=reviewed.license_bytes())
    with pytest.raises(ProductError, match="UNEXPECTED_LICENSE_EVIDENCE"):
        ProductAssetRights(item.to_dict(), raw=item.raw_bytes(), snapshot=item._snapshot,
                           license_raw={"ADVERTISEMENT": instrument("ADVERTISEMENT")})


@pytest.mark.parametrize("declared", ["UNKNOWN", "RESTRICTED"])
def test_schema_and_semantics_both_reject_rehashed_negative_to_granted(declared):
    original = rights(payload=source(declared=declared))
    granted = review().to_dict()
    changed = original.to_dict()
    changed["permissions"] = granted["permissions"]
    with pytest.raises(ProductError, match="RIGHTS_REPLAY_MISMATCH"):
        ProductAssetRights(reseal(changed), raw=original.raw_bytes(), snapshot=original._snapshot,
                           license_raw={"COMMERCIAL_USE": instrument()})
    changed = original.to_dict()
    changed["permissions"][0]["status"] = "GRANTED"
    assert not Draft202012Validator(schema("ProductAssetRights")).is_valid(reseal(changed))


@pytest.mark.parametrize("date", ["2026-10-30", "2026-10-30T00:00:00", "2026-02-30T00:00:00Z",
                                  "2026-10-30T00:00:00-00:00", "0001-01-01T00:00:00+23:59",
                                  "9999-12-31T23:59:59-23:59", "2026-10-30T00:00:00.1234567Z"])
def test_unsafe_timestamps_are_rejected(date):
    with pytest.raises(ProductError):
        rights(captured_at=date)
    with pytest.raises(ProductError):
        rights(payload=source(use_scope=scope(valid_from=date)))
    with pytest.raises(ProductError):
        assessment(review(), as_of=date)


@pytest.mark.parametrize("changes", [
    {"valid_until": "2026-10-01T00:00:00Z"}, {"valid_until": "2026-09-01T00:00:00Z"},
    {"territories": ["WORLDWIDE", "VN"]}, {"territories": []}, {"territories": ["VN", "VN"]},
    {"channels": []}, {"channels": ["*"]}, {"media_types": ["*"]},
])
def test_invalid_or_ambiguous_scope_is_rejected(changes):
    with pytest.raises(ProductError):
        rights(payload=source(use_scope=scope(**changes)))


@pytest.mark.parametrize("field,value", [("purpose", "ALL"), ("territory", "WORLDWIDE"),
                                       ("territory", "vn"), ("media_type", "*"), ("channel", "*")])
def test_use_queries_require_concrete_valid_scope(field, value):
    with pytest.raises(ProductError):
        assessment(review(), **{field: value})


def test_input_bounds_duplicates_pointer_and_raw_json_rules():
    for raw in (None, b"", b"x" * (MAX_RIGHTS_BYTES + 1)):
        with pytest.raises(ProductError, match="RIGHTS_SOURCE_TOO_LARGE"):
            build_rights(snapshot(), ordinal=1, raw=raw, source_ref="rights-export", captured_at=CAPTURE)
    duplicate = source()
    duplicate["permissions"].append(deepcopy(duplicate["permissions"][0]))
    with pytest.raises(ProductError):
        rights(payload=duplicate)
    repeated = source(purposes=("COMMERCIAL_USE", "COMMERCIAL_USE"))
    with pytest.raises(ProductError):
        rights(payload=repeated)
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'[]'):
        with pytest.raises(ProductError):
            rights(raw=raw)
    wrapped = json.dumps({"preexport": [source()]}).encode()
    item = rights(raw=wrapped, pointer="/preexport/0", source_kind="PREEXPORTED_API")
    assert item.to_dict()["source"]["pointer"] == "/preexport/0"
    assert item.raw_bytes() == wrapped
    for pointer in ("bad", "/absent", "/~2bad", "/preexport/01"):
        with pytest.raises(ProductError):
            rights(raw=wrapped, pointer=pointer)
    for raw in (b"", b"x" * (MAX_RAW_BYTES + 1), "bytes-required"):
        with pytest.raises(ProductError, match="ASSET_EMPTY_OR_TOO_LARGE"):
            assessment(review(), asset_raw=raw)


def test_published_schema_and_shared_product_validation_surfaces():
    path = Path(__file__).resolve().parents[1] / "contracts/ProductAssetRights.v1.schema.json"
    definition = schema("ProductAssetRights")
    assert json.loads(path.read_bytes()) == definition
    Draft202012Validator.check_schema(definition)
    item = review()
    kwargs = {"raw": item.raw_bytes(), "snapshot": item._snapshot, "license_raw": item.license_bytes()}
    assert validate_contract("ProductAssetRights", item.to_dict(), **kwargs) == item.to_dict()
    assert parse_contract("ProductAssetRights", item.to_bytes(), **kwargs) == item.to_dict()
    body = item.to_dict()
    body.pop("rights_id")
    assert seal_contract("ProductAssetRights", body, **kwargs) == item.to_dict()
    definition["properties"].clear()
    assert schema("ProductAssetRights")["properties"]
    changed = item.to_dict()
    changed["publication_status"] = "PUBLISHED"
    with pytest.raises(ProductError, match="INVALID_PRODUCTASSETRIGHTS"):
        validate_contract("ProductAssetRights", reseal(changed), **kwargs)


@pytest.mark.parametrize("text", ["person@example.test", "+84 912 345 678", "Bearer abcdefghijklmnopqrst",
                                  "api_key=private-value", "https://invalid.test/asset?token=value"])
def test_contact_data_and_secret_bearing_metadata_are_rejected(text):
    with pytest.raises(ProductError) as error:
        rights(payload=source(asset={"origin_ref": text}))
    assert text not in str(error.value)
