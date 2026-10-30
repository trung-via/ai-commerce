"""A12-02 claims: source assertions, counterevidence, review gating and replay."""
from __future__ import annotations

import json
import socket
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.product import ProductError, content_id, prepare_bytes, schema, validate_contract
from ai_commerce.product.claims import (INDEPENDENT_STATUS, ProductClaimEvidence, assess_claims,
                                        build_claim, claim_review_digest, review_claim)
from ai_commerce.product.evidence_schemas import CLAIM_STATES
from ai_commerce.product.schemas import MAX_RAW_BYTES

CAPTURE = "2026-10-30T05:00:00Z"
OBSERVED = "2026-10-30T04:00:00Z"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A12-02 claims must remain offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def snapshot(*, synthetic=True, **changes):
    raw = json.dumps({"schema_version": "ManualProductListings.v1", "rows": [{
        "marketplace": "fixture", "seller_key": "seller", "listing_key": "listing",
        "variant_key": "red", **changes}]}).encode()
    return prepare_bytes(raw, file_format="json", source_ref="listing-export", captured_at=CAPTURE,
                         synthetic=synthetic, declared_verification="INDEPENDENTLY_VERIFIED")


def claim_raw(**changes):
    return json.dumps({"schema_version": "ProductClaimSource.v1", "claim_key": "waterproof",
                       "category": "PERFORMANCE", "statement": "Seller says water resistant",
                       "value": True, "unit": None, "origin": "SELLER", "role": "SUPPORTING",
                       "observed_at": OBSERVED, **changes}, ensure_ascii=False).encode("utf-8")


def claim(*, source=None, raw=None, source_kind="HUMAN_SUPPLIED", **changes):
    return build_claim(snapshot() if source is None else source, ordinal=1,
                       raw=claim_raw(**changes) if raw is None else raw,
                       source_ref="https://invalid.test/never-fetch", captured_at=CAPTURE,
                       source_kind=source_kind)


def reseal(value):
    value = deepcopy(value)
    value["claim_id"] = content_id("ProductClaimEvidence", value)
    return value


def review(value, **changes):
    return review_claim(value, **{"proof_raw": b"Independent measurement report fixture; scope red variant.",
                                 "reviewer_ref": "human-reviewer", "producer_ref": "lab-1",
                                 "method": "Read report, match variant and test method",
                                 "reviewed_at": "2026-10-30T06:00:00Z", "evidence_ref": "local-report-1",
                                 "evidence_kind": "INDEPENDENT_TEST_REPORT", **changes})


@pytest.mark.parametrize("source_kind", ["HUMAN_SUPPLIED", "PREEXPORTED_API"])
def test_golden_seller_statement_is_never_independently_verified(source_kind):
    raw = b"\xef\xbb\xbf" + claim_raw() + b"\r\n"
    source = snapshot()
    evidence = claim(raw=raw, source=source, source_kind=source_kind)
    value = evidence.to_dict()
    assert value["status"] == "SELLER_ASSERTED"
    assert value["reviewer_proof"] is None
    assert value["source_status"] == "USER_SUPPLIED_NOT_LIVE_VERIFIED"
    assert value["source"]["raw_sha256"] == sha256(raw).hexdigest()
    assert value["rights_status"] == "UNKNOWN"
    assert value["publication_status"] == "NOT_ATTEMPTED"
    assert evidence.raw_bytes() == raw
    assert evidence == claim(raw=raw, source=source, source_kind=source_kind)
    assert ProductClaimEvidence.from_bytes(evidence.to_bytes(), raw=raw, snapshot=source) == evidence
    assert claim_raw() in raw
    changed = evidence.to_dict()
    changed["claim"]["statement"] = "mutated"
    assert evidence.to_dict()["claim"]["statement"] != "mutated"
    with pytest.raises(FrozenInstanceError):
        evidence._raw_evidence = b"changed"


@pytest.mark.parametrize("origin,status,value,role", [
    ("OBSERVER", "SOURCE_OBSERVED", False, "COUNTEREVIDENCE"),
    ("ILLUSTRATION", "ILLUSTRATIVE", "fiction", "INCONCLUSIVE"),
    ("UNKNOWN", "UNKNOWN", None, "INCONCLUSIVE"),
    ("SELLER", "SELLER_ASSERTED", 0, "SUPPORTING"),
])
def test_claim_categories_status_value_unit_and_negative_evidence(origin, status, value, role):
    evidence = claim(origin=origin, value=value, role=role, unit="test-unit", category="SAFETY")
    data = evidence.to_dict()
    assert data["status"] == status
    assert data["claim"]["value"] == value
    assert data["claim"]["unit"] == "test-unit"
    assert data["claim"]["role"] == role


def test_report_and_scoped_human_review_required_and_original_preserved():
    original = claim(source=snapshot(synthetic=False))
    reviewed = review(original)
    value = reviewed.to_dict()
    assert value["status"] == INDEPENDENT_STATUS
    assert original.to_dict()["status"] == "SELLER_ASSERTED"
    assert original.raw_bytes() == reviewed.raw_bytes()
    assert original.claim_id != reviewed.claim_id
    assert value["reviewer_proof"]["claim_sha256"] == claim_review_digest(original.to_dict())
    assert value["source_status"] == "USER_SUPPLIED_NOT_LIVE_VERIFIED"
    assert ProductClaimEvidence.from_bytes(reviewed.to_bytes(), raw=reviewed.raw_bytes(),
                                          snapshot=original._snapshot, proof_raw=reviewed.proof_bytes()) == reviewed
    for raw in (None, b"", b" ", b"\x00" * (MAX_RAW_BYTES + 1)):
        with pytest.raises(ProductError, match="INDEPENDENT_EVIDENCE_REQUIRED"):
            review(original, proof_raw=raw)


@pytest.mark.parametrize("changes,code", [
    ({"reviewer_ref": "seller"}, "SELLER_REVIEW_NOT_INDEPENDENT"),
    ({"producer_ref": "seller"}, "SELLER_REVIEW_NOT_INDEPENDENT"),
    ({"reviewed_at": "2026-10-30T04:59:59Z"}, "REVIEW_BEFORE_EVIDENCE"),
    ({"reviewed_at": "2026-10-30T06:00:00"}, "INVALID_PRODUCTCLAIMEVIDENCE"),
    ({"method": " "}, "INVALID_PRODUCTCLAIMEVIDENCE"),
    ({"evidence_kind": "SCRAPE"}, "INVALID_PRODUCTCLAIMEVIDENCE"),
    ({"evidence_kind": "API_RESPONSE"}, "INVALID_PRODUCTCLAIMEVIDENCE"),
])
def test_unsupported_or_seller_review_does_not_certify(changes, code):
    with pytest.raises(ProductError, match=code):
        review(claim(source=snapshot(synthetic=False)), **changes)


def test_source_bytes_cannot_be_their_own_independent_proof():
    original = claim(source=snapshot(synthetic=False))
    for raw in (original.raw_bytes(), original._snapshot.raw_bytes()):
        with pytest.raises(ProductError, match="INDEPENDENT_EVIDENCE_NOT_SEPARATE"):
            review(original, proof_raw=raw)
    with pytest.raises(ProductError, match="INVALID_PRODUCTCLAIMEVIDENCE"):
        review(claim())
    for origin, value in (("ILLUSTRATION", "fiction"), ("UNKNOWN", None)):
        with pytest.raises(ProductError, match="CLAIM_NOT_VERIFIABLE"):
            review(claim(source=snapshot(synthetic=False), origin=origin, value=value, role="INCONCLUSIVE"))


def test_rehashed_status_laundering_is_rejected():
    original = claim()
    value = original.to_dict()
    value["status"] = "SOURCE_OBSERVED"
    with pytest.raises(ProductError, match="CLAIM_STATUS_UPGRADE"):
        validate_contract("ProductClaimEvidence", reseal(value), raw=original.raw_bytes(), snapshot=original._snapshot)
    value["status"] = INDEPENDENT_STATUS
    with pytest.raises(ProductError, match="INVALID_PRODUCTCLAIMEVIDENCE"):
        validate_contract("ProductClaimEvidence", reseal(value), raw=original.raw_bytes(), snapshot=original._snapshot)


def test_rehashed_value_source_subject_and_proof_forgery_rejected():
    original = claim(source=snapshot(synthetic=False))
    value = original.to_dict()
    value["claim"]["value"] = False
    with pytest.raises(ProductError, match="CLAIM_REPLAY_MISMATCH"):
        validate_contract("ProductClaimEvidence", reseal(value), raw=original.raw_bytes(), snapshot=original._snapshot)
    with pytest.raises(ProductError, match="RAW_PROVENANCE_MISMATCH"):
        ProductClaimEvidence.from_bytes(original.to_bytes(), raw=original.raw_bytes() + b"\n", snapshot=original._snapshot)
    with pytest.raises(ProductError, match="PRODUCT_SUBJECT_MISMATCH"):
        ProductClaimEvidence.from_bytes(original.to_bytes(), raw=original.raw_bytes(), snapshot=snapshot(synthetic=False, variant_key="blue"))
    reviewed = review(original)
    with pytest.raises(ProductError, match="REVIEW_PROVENANCE_MISMATCH"):
        ProductClaimEvidence.from_bytes(reviewed.to_bytes(), raw=original.raw_bytes(), snapshot=original._snapshot, proof_raw=b"wrong")
    for field in ("claim_sha256", "evidence_sha256"):
        changed = reviewed.to_dict()
        changed["reviewer_proof"][field] = "f" * 64
        with pytest.raises(ProductError, match="REVIEW_SCOPE_MISMATCH|REVIEW_PROVENANCE_MISMATCH"):
            validate_contract("ProductClaimEvidence", reseal(changed), raw=original.raw_bytes(), snapshot=original._snapshot, proof_raw=reviewed.proof_bytes())


def test_claim_conflicts_counterevidence_and_illustration_are_retained():
    positive = claim()
    assert positive.claim_id == "a12-claim-5e8163058fc8f0c5af852e9b77e3d11c858d6a524a4f1c88128b6d5a765bb071"
    negative = claim(value=False, origin="OBSERVER", role="COUNTEREVIDENCE")
    fiction = claim(value="an imaginary result", origin="ILLUSTRATION", role="INCONCLUSIVE")
    report = assess_claims([positive, negative, fiction, positive])
    group = report["groups"][0]
    assert group["status"] == "CONFLICT"
    assert len(group["claim_ids"]) == 3
    assert group["counterevidence_ids"] == [negative.claim_id]
    assert len(group["evidence"]) == 3
    assert assess_claims([positive, fiction])["groups"][0]["status"] == "NO_CONFLICT_DETECTED"
    other_variant = claim(source=snapshot(variant_key="blue"), value=False)
    other_seller = claim(source=snapshot(seller_key="another"), value=False)
    groups = assess_claims([positive, other_variant, other_seller])["groups"]
    assert len(groups) == 3
    assert all(item["status"] == "NO_CONFLICT_DETECTED" for item in groups)
    assert assess_claims([negative, positive, fiction]) == assess_claims([fiction, positive, negative])


def test_source_declared_conflict_references_are_preserved_as_assertions():
    refs = [claim().claim_id, claim(value=False).claim_id]
    evidence = claim(origin="CONFLICT", value=None, role="INCONCLUSIVE", conflict_refs=refs)
    assert evidence.to_dict()["status"] == "CONFLICT"
    assert evidence.to_dict()["claim"]["conflict_refs"] == refs
    assert assess_claims([evidence])["groups"][0]["status"] == "CONFLICT"


@pytest.mark.parametrize("changes,code", [
    ({"category": "bogus"}, "INVALID_CLAIM_SOURCE"),
    ({"origin": "INDEPENDENTLY_VERIFIED"}, "INVALID_CLAIM_SOURCE"),
    ({"verified": True}, "INVALID_CLAIM_SOURCE"),
    ({"value": None}, "CLAIM_VALUE_REQUIRED"),
    ({"value": {"nested": "unsupported"}}, "INVALID_CLAIM_SOURCE"),
    ({"value": 1e16}, "INVALID_CLAIM_SOURCE"),
    ({"origin": "UNKNOWN", "value": "guessed"}, "INVALID_UNKNOWN_CLAIM"),
    ({"origin": "UNKNOWN", "value": None}, "INVALID_UNKNOWN_CLAIM"),
    ({"origin": "CONFLICT", "conflict_refs": []}, "INVALID_CLAIM_CONFLICT_REFS"),
    ({"conflict_refs": ["a12-claim-" + "0" * 64]}, "INVALID_CLAIM_CONFLICT_REFS"),
    ({"observed_at": "2026-10-30T05:00:00.000001Z"}, "OBSERVED_AFTER_CAPTURE"),
    ({"observed_at": "2026-02-30T00:00:00Z"}, "INVALID_TIMESTAMP"),
    ({"observed_at": "0001-01-01T00:00:00+23:00"}, "INVALID_TIMESTAMP"),
    ({"statement": "x" * 4097}, "INVALID_CLAIM_SOURCE"),
])
def test_invalid_claim_input(changes, code):
    with pytest.raises(ProductError, match=code):
        claim(**changes)


def test_claim_raw_and_context_are_mandatory_bounded_and_strict():
    original = claim()
    with pytest.raises(ProductError, match="RAW_EVIDENCE_REQUIRED"):
        validate_contract("ProductClaimEvidence", original.to_dict(), snapshot=original._snapshot)
    with pytest.raises(ProductError, match="LISTING_SNAPSHOT_REQUIRED"):
        validate_contract("ProductClaimEvidence", original.to_dict(), raw=original.raw_bytes())
    for raw, code in ((b"", "SOURCE_EMPTY_OR_TOO_LARGE"),
                      (b" " * (MAX_RAW_BYTES + 1), "SOURCE_EMPTY_OR_TOO_LARGE"),
                      (b'{"x":1,"x":2}', "JSON_DUPLICATE_KEY"),
                      (b'{"x":NaN}', "JSON_NONFINITE"),
                      (b"[1,2]", "EVIDENCE_OBJECT_REQUIRED")):
        with pytest.raises(ProductError, match=code):
            claim(raw=raw)
    for values in (iter([original]), [original.to_dict()], [original] * 2001):
        with pytest.raises(ProductError):
            assess_claims(values)


def test_preexported_pointer_and_schema_mirror_without_a02_registration(monkeypatch):
    payload = json.loads(claim_raw())
    raw = json.dumps({"data": {"claim/records": [payload]}, "ignored": "Preserve raw seller data"}).encode()
    evidence = build_claim(snapshot(), ordinal=1, raw=raw, source_ref="export-1", captured_at=CAPTURE,
                           source_kind="PREEXPORTED_API", pointer="/data/claim~1records/0")
    assert evidence.to_dict()["claim"]["value"] is True
    assert evidence.raw_bytes() == raw
    definition = schema("ProductClaimEvidence")
    assert definition == json.loads((ROOT / "contracts/ProductClaimEvidence.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(definition)
    assert set(definition["properties"]["status"]["enum"]) == set(CLAIM_STATES)
    from ai_commerce.contracts.schema import SCHEMAS
    assert "ProductClaimEvidence" not in SCHEMAS
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("Runtime must not read repo schemas"))
    assert schema("ProductClaimEvidence") == definition
    assert claim().to_dict()["status"] == "SELLER_ASSERTED"
