"""A13-D06 Offline affiliate placement evidence tests.

Verifies:
- 7 dimensions: account x app x market x surface x product x variant x link
- Evidence provenance & tamper detection
- Required commercial affiliate disclosure
- Expiry and revocation
- Hostile URL detection (SSRF, malicious schemes, credentials, internal networks)
- Cross-account, cross-app, cross-market, cross-surface, cross-product mismatches
- Parsed URL alone MUST NOT imply active eligible link; decisions HOLD/UNKNOWN/REVIEW only
- No OAuth, live provider calls, posting, real approvals, sales or earnings claims
"""
from __future__ import annotations

import json
import socket
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.affiliate_release import (
    AffiliatePlacementError,
    AffiliatePlacementEvidence,
    EvidenceRef,
    PlacementDisclosure,
    PlacementRequest,
    PlacementTemporal,
    affiliate_placement_schema,
    evaluate_affiliate_placement,
    review_affiliate_placement,
)
from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.product import canonical_bytes

CAPTURE = "2026-10-30T01:00:00Z"
AS_OF = "2026-10-30T03:00:00Z"
START = "2026-10-30T02:00:00Z"
END = "2026-11-30T02:00:00Z"
EXPIRED_END = "2026-10-29T00:00:00Z"
REVOKED_AT = "2026-10-30T02:30:00Z"

ACCOUNT = {"namespace": "tiktok_open_id", "id": "acc-creator-123"}
APP = {"namespace": "tiktok_client_key", "id": "app-tiktok-shop"}
PRODUCT_ID = "a12-product-" + "1" * 64
VARIANT_ID = "a12-variant-" + "2" * 64
LINK_URL = "https://shop.tiktok.com/view/product/123456?affiliate_id=acc-creator-123"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Enforce strict offline execution; no network/socket connections allowed."""
    def forbidden(*args, **kwargs):
        pytest.fail("A13-D06 attempted forbidden network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def standard_evidence_ref():
    raw_evidence = canonical_bytes({
        "schema_version": "ChannelCapabilitySnapshot.v1",
        "snapshot_id": "ccs_test_001",
        "platform": "TIKTOK",
        "account_ref": ACCOUNT,
        "app_ref": APP,
        "market": "VN",
        "surface": "PRODUCT_ANCHOR",
        "action": "PRODUCT_ANCHOR",
        "state": "ALLOWED",
    })
    return EvidenceRef(
        evidence_id="ev_capability_001",
        kind="A07_CAPABILITY_SNAPSHOT",
        record_sha256=sha256_bytes(raw_evidence),
        source_sha256=None,
        tamper_checked=True,
        raw_bytes=raw_evidence,
    )


def standard_inputs(**overrides):
    base = {
        "account_ref": ACCOUNT,
        "app_ref": APP,
        "market": "VN",
        "surface": "PRODUCT_ANCHOR",
        "product_id": PRODUCT_ID,
        "variant_id": VARIANT_ID,
        "link_url": LINK_URL,
        "as_of": AS_OF,
        "valid_from": START,
        "valid_until": END,
        "disclosure": PlacementDisclosure(
            statement="Includes paid affiliate link; commission earned on qualifying orders.",
            mode="CLEAR_AND_CONSPICUOUS",
            tags=("#ad", "#affiliate"),
        ),
        "evidence_refs": [standard_evidence_ref()],
        "source_kind": "OFFLINE_EVIDENCE_REPLAY",
    }
    base.update(overrides)
    return base


# ============================================================================
# 1. Schema & Contract Mirror Tests
# ============================================================================


def test_schema_mirror_and_validation():
    schema = affiliate_placement_schema()
    Draft202012Validator.check_schema(schema)

    contract_path = Path(__file__).resolve().parents[1] / "contracts/AffiliatePlacementEvidence.v1.schema.json"
    assert contract_path.exists(), "Schema contract file must exist in contracts/"
    contract_json = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract_json == schema, "contracts/AffiliatePlacementEvidence.v1.schema.json must mirror affiliate_placement_schema()"

    # Validate standard receipt against schema
    evidence = evaluate_affiliate_placement(**standard_inputs())
    Draft202012Validator(schema).validate(evidence.to_dict())
    assert evidence.decision == "REVIEW"
    assert evidence.status == "REVIEW"


# ============================================================================
# 2. Parsed URL Alone MUST NOT Imply Active Eligible Link
# ============================================================================


def test_parsed_url_alone_does_not_imply_active_eligible_link():
    """Even with a syntactically valid URL, decision is REVIEW (or UNKNOWN/HOLD), never ACTIVE/APPROVED."""
    evidence = evaluate_affiliate_placement(**standard_inputs())
    record = evidence.to_dict()

    # Guardrails must be explicitly protective and inactive
    assert record["live_link_verified"] is False
    assert record["account_eligibility"] == "UNKNOWN"
    assert record["link_activation"] == "UNKNOWN"
    assert record["platform_approval"] == "UNKNOWN"
    assert record["publication_status"] == "NOT_ATTEMPTED"
    assert record["sales_claims_allowed"] is False
    assert record["earnings_claims_allowed"] is False
    assert record["sales_earnings_claims"] == "NOT_PERMITTED"

    # Decisions must be HOLD / UNKNOWN / REVIEW only
    assert evidence.decision in ("HOLD", "UNKNOWN", "REVIEW")
    assert evidence.decision == "REVIEW"
    assert "URL_OFFLINE_PARSED_ONLY" in evidence.reason_codes
    assert "URL_SYNTAX_VALID" in evidence.reason_codes


def test_url_alone_without_evidence_is_unknown():
    """A parseable URL without supporting evidence provenance produces UNKNOWN, not REVIEW or ACTIVE."""
    inputs = standard_inputs(evidence_refs=[])
    evidence = evaluate_affiliate_placement(**inputs)
    assert evidence.decision == "UNKNOWN"
    assert "EVIDENCE_UNPROVEN" in evidence.reason_codes
    assert evidence.to_dict()["live_link_verified"] is False


# ============================================================================
# 3. 7 Dimensions (Account x App x Market x Surface x Product x Variant x Link)
# ============================================================================


def test_all_seven_dimensions_reflected_in_placement():
    inputs = standard_inputs()
    evidence = evaluate_affiliate_placement(**inputs)
    placement = evidence.placement

    assert placement["account_ref"] == ACCOUNT
    assert placement["app_ref"] == APP
    assert placement["market"] == "VN"
    assert placement["surface"] == "PRODUCT_ANCHOR"
    assert placement["product_id"] == PRODUCT_ID
    assert placement["variant_id"] == VARIANT_ID
    assert placement["link_url"] == LINK_URL


def test_missing_dimension_raises_bounded_error():
    with pytest.raises(AffiliatePlacementError, match="ACCOUNT_REF_REQUIRED"):
        evaluate_affiliate_placement(**standard_inputs(account_ref=None))

    with pytest.raises(AffiliatePlacementError, match="LINK_URL_REQUIRED"):
        evaluate_affiliate_placement(**standard_inputs(link_url=None))

    with pytest.raises(AffiliatePlacementError, match="PRODUCT_ID_REQUIRED"):
        evaluate_affiliate_placement(**standard_inputs(product_id=""))

    with pytest.raises(AffiliatePlacementError, match="VARIANT_ID_REQUIRED"):
        evaluate_affiliate_placement(**standard_inputs(variant_id=""))


# ============================================================================
# 4. Required Commercial Disclosure Tests
# ============================================================================


def test_required_disclosure_valid_statement_and_tags():
    disclosure = PlacementDisclosure(
        statement="Paid partnership with Shop. Commission earned.",
        mode="CLEAR_AND_CONSPICUOUS",
        tags=("#ad", "#sponsored"),
    )
    evidence = evaluate_affiliate_placement(**standard_inputs(disclosure=disclosure))
    assert evidence.decision == "REVIEW"
    assert evidence.disclosure["status"] == "COMPLIANT"
    assert evidence.disclosure["has_affiliate_notice"] is True
    assert "REQUIRED_DISCLOSURE_PRESENT" in evidence.reason_codes


def test_missing_required_disclosure_causes_hold():
    # None disclosure
    evidence = evaluate_affiliate_placement(**standard_inputs(disclosure=None))
    assert evidence.decision == "HOLD"
    assert evidence.disclosure["status"] == "NON_COMPLIANT"
    assert "MISSING_REQUIRED_DISCLOSURE" in evidence.reason_codes


def test_non_affiliate_disclosure_statement_causes_hold():
    # Statement lacks any commercial/affiliate notice and has no affiliate tags
    disclosure = PlacementDisclosure(
        statement="Check out this cool new product I found yesterday!",
        mode="CLEAR_AND_CONSPICUOUS",
        tags=("#cool", "#trending"),
    )
    evidence = evaluate_affiliate_placement(**standard_inputs(disclosure=disclosure))
    assert evidence.decision == "HOLD"
    assert evidence.disclosure["status"] == "NON_COMPLIANT"
    assert evidence.disclosure["has_affiliate_notice"] is False
    assert "MISSING_REQUIRED_DISCLOSURE" in evidence.reason_codes


# ============================================================================
# 5. Expiry and Revocation Tests
# ============================================================================


def test_expired_placement_causes_hold():
    evidence = evaluate_affiliate_placement(**standard_inputs(
        as_of=AS_OF,
        valid_from="2026-10-01T00:00:00Z",
        valid_until=EXPIRED_END,
    ))
    assert evidence.decision == "HOLD"
    assert evidence.temporal["status"] == "EXPIRED"
    assert "PLACEMENT_EXPIRED" in evidence.reason_codes


def test_revoked_placement_causes_hold():
    evidence = evaluate_affiliate_placement(**standard_inputs(
        as_of=AS_OF,
        valid_from=START,
        valid_until=END,
        revoked_at=REVOKED_AT,
    ))
    assert evidence.decision == "HOLD"
    assert evidence.temporal["status"] == "REVOKED"
    assert "PLACEMENT_REVOKED" in evidence.reason_codes


def test_future_effective_date_causes_hold():
    evidence = evaluate_affiliate_placement(**standard_inputs(
        as_of=AS_OF,
        valid_from="2026-11-01T00:00:00Z",
        valid_until="2026-12-01T00:00:00Z",
    ))
    assert evidence.decision == "HOLD"
    assert evidence.temporal["status"] == "NOT_YET_EFFECTIVE"
    assert "PLACEMENT_NOT_YET_EFFECTIVE" in evidence.reason_codes


def test_invalid_temporal_window_causes_hold():
    evidence = evaluate_affiliate_placement(**standard_inputs(
        as_of=AS_OF,
        valid_from="2026-11-01T00:00:00Z",
        valid_until="2026-10-01T00:00:00Z",
    ))
    assert evidence.decision == "HOLD"
    assert evidence.temporal["status"] == "INVALID_WINDOW"
    assert "INVALID_TEMPORAL_WINDOW" in evidence.reason_codes


# ============================================================================
# 6. Hostile URL and SSRF Prevention Tests
# ============================================================================


@pytest.mark.parametrize("hostile_url, expected_code", [
    ("javascript:alert('pwned')", "HOSTILE_URL_SCHEME"),
    ("data:text/html,<script>alert(1)</script>", "HOSTILE_URL_SCHEME"),
    ("file:///etc/passwd", "HOSTILE_URL_SCHEME"),
    ("ftp://ftp.example.com/file", "HOSTILE_URL_SCHEME"),
    ("https://user:password@shop.tiktok.com/item", "CREDENTIALS_IN_URL"),
    ("http://localhost/view/item", "INTERNAL_NETWORK_TARGET"),
    ("http://127.0.0.1/view/item", "INTERNAL_OR_RESERVED_TARGET"),
    ("http://169.254.169.254/latest/meta-data", "INTERNAL_OR_RESERVED_TARGET"),
    ("http://10.0.0.1/affiliate", "INTERNAL_OR_RESERVED_TARGET"),
    ("http://192.168.1.1/affiliate", "INTERNAL_OR_RESERVED_TARGET"),
    ("https://shop.tiktok.com:22/item", "SUSPICIOUS_PORT"),
    ("https://shop.tiktok.com:25/item", "SUSPICIOUS_PORT"),
    ("https://shop.tiktok.com/view/../../secret", "INJECTION_PATTERN_DETECTED"),
    ("https://shop.tiktok.com/view?search=<script>evil()</script>", "INJECTION_PATTERN_DETECTED"),
    ("https:///no-host-here", "MISSING_HOST"),
    ("https://shop.tiktok.com/view\r\ninjected", "CONTROL_CHARACTERS_IN_URL"),
])
def test_hostile_url_rejected_with_hold(hostile_url, expected_code):
    evidence = evaluate_affiliate_placement(**standard_inputs(link_url=hostile_url))
    assert evidence.decision == "HOLD"
    assert evidence.url_evaluation["is_hostile"] is True
    assert "HOSTILE_URL" in evidence.reason_codes
    assert expected_code in evidence.reason_codes


# ============================================================================
# 7. Cross-Account and Entity Mismatch Tests
# ============================================================================


def test_cross_account_mismatch_with_link_param_causes_hold():
    # Link explicitly embeds a different creator account ID
    mismatched_url = "https://shop.tiktok.com/view/product/123456?affiliate_id=acc-other-999"
    evidence = evaluate_affiliate_placement(**standard_inputs(link_url=mismatched_url))
    assert evidence.decision == "HOLD"
    assert "CROSS_ACCOUNT_MISMATCH" in evidence.reason_codes


def test_cross_account_mismatch_with_capability_snapshot_causes_hold():
    snap = {
        "snapshot_id": "ccs_mismatch_acc",
        "account_ref": {"namespace": "tiktok_open_id", "id": "acc-different-456"},
        "app_ref": APP,
        "market": "VN",
        "surface": "PRODUCT_ANCHOR",
        "state": "ALLOWED",
    }
    evidence = evaluate_affiliate_placement(**standard_inputs(capability_snapshot=snap))
    assert evidence.decision == "HOLD"
    assert "CROSS_ACCOUNT_MISMATCH" in evidence.reason_codes


def test_app_mismatch_causes_hold():
    snap = {
        "snapshot_id": "ccs_mismatch_app",
        "account_ref": ACCOUNT,
        "app_ref": {"namespace": "tiktok_client_key", "id": "app-different"},
        "market": "VN",
        "surface": "PRODUCT_ANCHOR",
        "state": "ALLOWED",
    }
    evidence = evaluate_affiliate_placement(**standard_inputs(capability_snapshot=snap))
    assert evidence.decision == "HOLD"
    assert "APP_MISMATCH" in evidence.reason_codes


def test_market_mismatch_causes_hold():
    snap = {
        "snapshot_id": "ccs_mismatch_market",
        "account_ref": ACCOUNT,
        "app_ref": APP,
        "market": "US",
        "surface": "PRODUCT_ANCHOR",
        "state": "ALLOWED",
    }
    evidence = evaluate_affiliate_placement(**standard_inputs(capability_snapshot=snap))
    assert evidence.decision == "HOLD"
    assert "MARKET_MISMATCH" in evidence.reason_codes


def test_surface_mismatch_causes_hold():
    snap = {
        "snapshot_id": "ccs_mismatch_surface",
        "account_ref": ACCOUNT,
        "app_ref": APP,
        "market": "VN",
        "surface": "BIO",
        "state": "ALLOWED",
    }
    evidence = evaluate_affiliate_placement(**standard_inputs(capability_snapshot=snap))
    assert evidence.decision == "HOLD"
    assert "SURFACE_MISMATCH" in evidence.reason_codes


def test_product_mismatch_causes_hold():
    pack = {
        "pack_id": "a12-pack-prod-mismatch",
        "subject": {
            "product_id": "a12-product-" + "9" * 64,
            "variant_id": VARIANT_ID,
        },
    }
    evidence = evaluate_affiliate_placement(**standard_inputs(evidence_pack=pack))
    assert evidence.decision == "HOLD"
    assert "PRODUCT_MISMATCH" in evidence.reason_codes


def test_variant_mismatch_causes_hold():
    pack = {
        "pack_id": "a12-pack-var-mismatch",
        "subject": {
            "product_id": PRODUCT_ID,
            "variant_id": "a12-variant-" + "8" * 64,
        },
    }
    evidence = evaluate_affiliate_placement(**standard_inputs(evidence_pack=pack))
    assert evidence.decision == "HOLD"
    assert "VARIANT_MISMATCH" in evidence.reason_codes


# ============================================================================
# 8. Tampered Hash and Integrity Tests
# ============================================================================


def test_tampered_evidence_ref_hash_causes_hold():
    raw_content = b'{"tampered": true}'
    ref = EvidenceRef(
        evidence_id="ev_tampered_001",
        kind="TAMPERED_RECORD",
        record_sha256="0" * 64,  # Intentionally wrong hash
        source_sha256=None,
        tamper_checked=False,
        raw_bytes=raw_content,
    )
    evidence = evaluate_affiliate_placement(**standard_inputs(evidence_refs=[ref]))
    assert evidence.decision == "HOLD"
    assert "TAMPERED_HASH" in evidence.reason_codes
    assert evidence.evidence_provenance["provenance_status"] == "TAMPERED"


def test_tampered_receipt_bytes_in_from_bytes_rejected():
    evidence = evaluate_affiliate_placement(**standard_inputs())
    raw = evidence.to_bytes()

    # Tamper with the bytes
    tampered_dict = json.loads(raw.decode("utf-8"))
    tampered_dict["decision"] = "REVIEW"
    tampered_dict["link_activation"] = "ACTIVE"  # Illegal modification!
    tampered_bytes = json.dumps(tampered_dict).encode("utf-8")

    with pytest.raises(AffiliatePlacementError, match="PLACEMENT_SCHEMA_INVALID|PLACEMENT_DIGEST_MISMATCH"):
        AffiliatePlacementEvidence.from_bytes(tampered_bytes)


def test_tampered_record_sha256_rejected():
    evidence = evaluate_affiliate_placement(**standard_inputs())
    tampered_dict = evidence.to_dict()
    tampered_dict["record_sha256"] = "f" * 64  # Corrupted digest
    tampered_bytes = canonical_bytes(tampered_dict)

    with pytest.raises(AffiliatePlacementError, match="PLACEMENT_DIGEST_MISMATCH"):
        AffiliatePlacementEvidence.from_bytes(tampered_bytes)


# ============================================================================
# 9. Credential Rejection Tests (No OAuth / Secrets)
# ============================================================================


@pytest.mark.parametrize("credential_payload", [
    "bearer secret_access_token_12345",
    "access_token: secret_oauth_token",
    "?access_token=secret12345",
    "?client_secret=supersecret",
])
def test_credential_material_rejected(credential_payload):
    tainted_url = f"https://shop.tiktok.com/item{credential_payload}"
    evidence = evaluate_affiliate_placement(**standard_inputs(link_url=tainted_url))
    assert evidence.decision == "HOLD"
    assert "CREDENTIAL_MATERIAL_NOT_ALLOWED" in evidence.reason_codes


# ============================================================================
# 10. Immutability, Serialization, Replay, and Determinism
# ============================================================================


def test_receipt_immutability():
    evidence = evaluate_affiliate_placement(**standard_inputs())
    with pytest.raises(FrozenInstanceError):
        evidence._encoded = b"hacked"

    # Mutating returned dictionary does not mutate the frozen instance
    as_dict = evidence.to_dict()
    as_dict["decision"] = "HOLD"
    assert evidence.decision == "REVIEW"


def test_from_bytes_exact_replay():
    inputs = standard_inputs()
    evidence = evaluate_affiliate_placement(**inputs)
    raw = evidence.to_bytes()

    restored = AffiliatePlacementEvidence.from_bytes(raw, **inputs)
    assert restored.to_bytes() == raw
    assert restored.decision == "REVIEW"
    assert restored.record_sha256 == evidence.record_sha256


def test_from_bytes_without_inputs_verifies_integrity():
    evidence = evaluate_affiliate_placement(**standard_inputs())
    raw = evidence.to_bytes()

    restored = AffiliatePlacementEvidence.from_bytes(raw)
    assert restored.to_bytes() == raw
    assert restored.decision == "REVIEW"


def test_deterministic_bytes():
    inputs = standard_inputs()
    first = evaluate_affiliate_placement(**inputs)
    second = review_affiliate_placement(**inputs)

    assert first.to_bytes() == second.to_bytes()
    assert first.record_sha256 == second.record_sha256
