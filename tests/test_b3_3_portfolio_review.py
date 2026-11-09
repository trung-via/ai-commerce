"""B3.3 Windows native owner/consumer tests; no real campaign assertion."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import os

import pytest

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.creative_quality import b3_next_brief_handoff as b3
from ai_commerce.opportunity.portfolio import b3_review_bridge as bridge
from test_a18_portfolio import request as a18_request
from test_a18_portfolio_integration import analytics, evaluation, cost_source
from test_bq_06b_fresh_a09_replay import actual_case, make_root_or_correction, reprocess
from test_a17_variants import file_bytes


@pytest.fixture(autouse=True)
def windows_only():
    assert os.name == "nt", "B3_3_NATIVE_WINDOWS_REQUIRED"


def native(tmp_path, source_kind="SYNTHETIC_FIXTURE"):
    args, advisory, learning, ledger, pin, prior = actual_case(
        tmp_path, source_kind=source_kind)
    request = a18_request(candidate="b3-3-local-context")
    request["metric"] = deepcopy(args["expected_metric"])
    request["scope"]["platform"] = request["metric"]["platform"]
    request["scope"]["cohort"] = args["expected_context"]["cohort"]
    request["scope"]["timezone"] = args["expected_context"]["timezone"]
    request["measurement_window"] = deepcopy(args["expected_context"]["measurement_window"])
    return args, ledger, pin, prior, request


def review(data, evidence=None):
    args, ledger, pin, prior, request = data
    return bridge.review_b3_portfolio(
        ledger_path=str(ledger.path), pin=pin, bq06a_raw=prior.raw,
        source_arguments=args, evaluation=request, evidence=evidence).to_dict()


def test_native_comparable_context_advisory_unlinked_and_immutable(tmp_path):
    data = native(tmp_path)
    before = file_bytes(tmp_path)
    result = review(data)
    assert result["status"] == bridge.ADVISORY, result
    assert result["reason_codes"] == ["CROSS_SOURCE_JOIN_UNVERIFIED"]
    assert result["comparable_dimensions"] == {
        "metric": True, "cohort": True, "window": True, "timezone": True}
    assert result["b3_counterevidence_count"] > 0
    assert result["a18_source_count"] == 0
    assert result["a18_evidence_quality"] == "MISSING"
    assert "COMPARABLE_CURRENT_PERFORMANCE" in result["a18_missingness"]
    assert result["a18_local_advisory"] == "INCONCLUSIVE"
    assert result["authority"] == bridge.BOUNDARY
    assert result["next_brief_sha256"]
    assert review(data) == result
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("field", ["metric", "cohort", "window", "timezone"])
def test_incompatible_dimensions_hold_without_join_or_brief(tmp_path, field):
    data = list(native(tmp_path))
    req = deepcopy(data[4])
    if field == "metric":
        req["metric"]["definition_version"] = "changed-definition"
    elif field == "cohort":
        req["scope"]["cohort"] = "other-cohort"
        req["metric"]["cohort"] = "other-cohort"
    elif field == "window":
        req["measurement_window"] = ["2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"]
    else:
        req["scope"]["timezone"] = "Asia/Ho_Chi_Minh"
    data[4] = req
    result = review(data)
    assert result["status"] == bridge.HOLD
    assert result["reason_codes"] == ["INCOMPARABLE_CONTEXT"], result
    assert result["next_brief_sha256"] is None
    assert result["authority"]["execution_authority"] == "NONE"


@pytest.mark.parametrize("child", ["correction", "reprocess"])
def test_stale_native_a09_prevents_portfolio_handoff(tmp_path, child):
    data = native(tmp_path)
    args, ledger, pin, prior, req = data
    if child == "correction":
        make_root_or_correction(ledger, pin, tmp_path, parent=pin.import_id, number=88)
    else:
        reprocess(ledger, pin.import_id)
    result = review(data)
    assert result["status"] == bridge.HOLD and result["reason_codes"] == ["B3_SOURCE_HOLD"]
    assert result["next_brief_sha256"] is None
    assert result["a18_local_advisory"] is None


def test_no_native_a18_data_is_not_customer_outcome(tmp_path):
    data = native(tmp_path, "USER_PROVIDED_PLATFORM_EXPORT")
    result = review(data)
    assert result["status"] == bridge.ADVISORY
    assert result["b3_source_kind"] == "USER_PROVIDED_PLATFORM_EXPORT"
    assert result["authority"]["provider_attested"] is False
    assert result["authority"]["roi"] is None and result["authority"]["causal_lift"] is None


def test_native_a18_report_from_other_context_does_not_create_identity_join(tmp_path):
    data = native(tmp_path / "brain")
    # A18 replayed report is independently native but NOT the A17 campaign.
    (tmp_path / "a18").mkdir()
    snapshot, path, report, report_source = analytics(tmp_path / "a18")
    from ai_commerce.opportunity.portfolio import EvidenceBundle
    result = review(data, EvidenceBundle(reports=(report_source,)))
    assert result["authority"]["source_join"] == "UNVERIFIED"
    assert result["authority"]["execution_authority"] == "NONE"
    assert result["a18_source_count"] == 1
    assert result["status"] == bridge.ADVISORY


def test_forged_packet_digest_and_authority_fail_closed(tmp_path):
    data = native(tmp_path)
    original = review(data)
    forged = deepcopy(original)
    forged["authority"]["financial_commitment"] = "EXECUTE"
    with pytest.raises(ValueError, match="B3_3_PACKET_DIGEST_INVALID"):
        bridge.B3PortfolioReview(canonical_bytes(forged))
    forged["record_sha256"] = sha256(canonical_bytes({
        k: v for k, v in forged.items() if k != "record_sha256"})).hexdigest()
    with pytest.raises(ValueError, match="B3_3_PACKET_AUTHORITY_INVALID"):
        bridge.B3PortfolioReview(canonical_bytes(forged))


def test_invalid_a18_source_input_returns_hold_no_authority(tmp_path):
    data = list(native(tmp_path))
    data[4] = {"fabricated": "fake campaign winner"}
    result = review(data)
    assert result["status"] == bridge.HOLD
    assert result["reason_codes"] == ["A18_NATIVE_EVALUATION_FAILED"]
    assert result["next_brief_sha256"] is None
    assert result["authority"] == bridge.BOUNDARY
