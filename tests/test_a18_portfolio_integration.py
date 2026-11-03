"""M06 native owner fan-in. All observations/receipts are offline fixtures only."""
from __future__ import annotations

import builtins
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import urllib.request

import pytest

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import LearningAdvisory, LearningRegistry, SnapshotPin, export_advisory, read_snapshot
from ai_commerce.learning.demo import _packet, _source
from ai_commerce.learning.reporting import PerformanceReport, build_performance_report, compare_reports
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.cost_ledger import CostEntry, CostLedger
from ai_commerce.opportunity.audience import AudienceLedger
from ai_commerce.opportunity.proposals import propose_opportunities
from ai_commerce.opportunity.signals import SignalLedger
from ai_commerce.opportunity.portfolio import (
    AdvisorySource, ComparisonSource, CostSource, EvidenceBundle, PortfolioError,
    ProposalSource, ReportSource, ReviewSource, SnapshotSource, recommend_many,
    recommend_portfolio, validate_recommendation,
)
from ai_commerce.opportunity.portfolio.schemas import SOURCE_SHA, digest, parse
from ai_commerce.settlement import CommercialReview, SettlementLedger, commercial_review

# Existing native factories are reused without changing or bypassing validators.
from test_a10_m04 import AS_OF as A10_CLOCK, add_series, append_notes
from test_a16_commercial_review import native, document_for, cost as native_cost
from test_a16_settlement import build, receipt, revision

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]
BASELINE = ["2026-10-31T00:00:00Z", "2026-11-01T00:00:00Z"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("M06 attempted network/provider access")
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", deny)


def analytics(tmp_path, name="current", values=(12, 0, None), *, window=None, cohort="synthetic-cohort", denominator=None):
    snapshot, path = _source(tmp_path, name, list(values), window=window or CURRENT,
                             cohort=cohort, denominator=denominator)
    body = snapshot.to_dict()
    pin = SnapshotPin(**{**body["pin"], "observation_ids": tuple(body["pin"]["observation_ids"])})
    report = build_performance_report(snapshot)
    selection = ReportSource(report, sha256(report.to_bytes()).hexdigest(),
                             (SnapshotSource(AnalyticsLedger(path), pin),))
    return snapshot, path, report, selection


def evaluation(snapshot, *, candidate="synthetic-candidate", policy=True, baseline=None):
    plan = snapshot.to_dict()["import"]["plan"]
    metric = deepcopy(plan["metric_definitions"][0])
    body = {"schema_version": "PortfolioEvaluation.v1", "candidate_id": candidate,
        "scope": {"campaign_ref": plan["campaign_ref"], "creative_ref": None, "creative_sha256": None,
            "product_ref": None, "provider_ref": None, "market": None,
            **{k: deepcopy(plan[k]) for k in ("platform", "surface", "account_ref", "cohort", "timezone", "attribution_tier")}},
        "as_of": "2026-11-03T00:00:00Z", "measurement_window": deepcopy(plan["measurement_window"]),
        "baseline_window": baseline, "currency": "USD", "metric": metric, "human_policy": None}
    if policy:
        body["human_policy"] = {"schema_version": "PortfolioHumanPolicy.v1", "policy_id": "fixture-human-v1",
            "authored_by": "HUMAN_DECLARED_NOT_AUTHENTICATED", "valid_from": "2026-10-01T00:00:00Z",
            "valid_until": "2026-12-01T00:00:00Z", "max_evidence_age_seconds": 2592000,
            "metric_definition_sha256": digest(metric), "keep_at_or_above": "100", "stop_at_or_below": "10",
            "between": "CHANGE", "currency": "USD", "requirement": "COMPARABLE_AUTHORITATIVE_OBSERVATIONS"}
    return body


def cost_source(ledger, *, entry_id="cost-1", status="ESTIMATED", amount="1.20", currency="USD", origin="UNVERIFIED", **changes):
    fields = {"entry_id": entry_id, "job_id": "fixture-job", "attempt": 1, "intent_digest": "a" * 64,
        "provider": "fixture-provider", "model": None, "status": status, "amount": amount, "currency": currency,
        "evidence_ref": "fixture:receipt-" + entry_id if status == "CHARGED" else None,
        "evidence_sha256": sha256(entry_id.encode()).hexdigest() if status == "CHARGED" else None,
        "evidence_origin": origin, "observed_at": "2026-11-02T00:00:00Z"}
    fields.update(changes)
    entry = CostEntry(**fields)
    ledger.record(entry)
    return CostSource(entry, sha256(canonical_bytes(entry.to_record())).hexdigest(), ledger)


def files_under(*roots):
    return {str(p): sha256(p.read_bytes()).hexdigest() for root in roots
            for p in ([root] if root.is_file() else root.rglob("*")) if p.is_file()}


def deny_effects(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("M06 attempted a write, provider, process or execution operation")
    for cls, name in ((ArtifactRegistry, "put_immutable"), (ArtifactRegistry, "worker"),
            (CostLedger, "record"), (SettlementLedger, "append"), (LearningRegistry, "append"),
            (AnalyticsLedger, "import_file"), (SignalLedger, "import_bytes"), (AudienceLedger, "append_bytes"),
            (Path, "write_bytes"), (Path, "write_text"), (Path, "mkdir"), (Path, "unlink")):
        monkeypatch.setattr(cls, name, deny)
    monkeypatch.setattr(subprocess, "Popen", deny)
    actual_open, actual_os_open, actual_connect = builtins.open, os.open, sqlite3.connect

    def read_open(file, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            deny()
        return actual_open(file, mode, *args, **kwargs)

    def read_fd(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            deny()
        return actual_os_open(path, flags, *args, **kwargs)

    def read_db(path, *args, **kwargs):
        if "mode=ro" not in str(path) or not kwargs.get("uri"):
            deny()
        return actual_connect(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", read_open)
    monkeypatch.setattr(os, "open", read_fd)
    monkeypatch.setattr(sqlite3, "connect", read_db)


def test_native_a10_a11_a15_a16_fan_in_reopens_and_preserves_custody(tmp_path, native, monkeypatch):
    snapshot, a09, report, report_source = analytics(tmp_path)
    learning = LearningRegistry(tmp_path / "learning.sqlite")
    previous = None
    for _ in range(2):
        previous = learning.append(**_packet(snapshot, report, previous=previous), snapshots=[snapshot], reports=[report]).to_dict()
    advisory = export_advisory(learning)
    signals = SignalLedger(tmp_path / "signals.sqlite")
    series, imported, _ = add_series(signals)
    audience, insight, _ = append_notes(tmp_path, signals, imported)
    proposal, = propose_opportunities(trend_series=[series], signal_ledger=signals,
        audience_ledger=audience, audience_insight_ids=[insight["insight_id"]], as_of=A10_CLOCK)
    proposal_raw = canonical_bytes(proposal)
    registry = ArtifactRegistry(tmp_path / "store")
    costs = CostLedger(registry)
    selected = native_cost(costs)
    entry = costs.get(selected["entry_id"])
    settlements = SettlementLedger(registry)
    settlements.append(build(document_for(native[1], "SETTLED", attr=native[2]), attribution=native[2]))
    review = commercial_review(commerce_run=native[1], settlement_ledger=settlements,
                               cost_ledger=costs, cost_selections=[selected])
    evidence = EvidenceBundle(
        proposals=(ProposalSource(proposal_raw, sha256(proposal_raw).hexdigest(), (canonical_bytes(series),), signals, audience),),
        reports=(report_source,), advisories=(AdvisorySource(advisory, sha256(advisory.to_bytes()).hexdigest(), learning),),
        costs=(CostSource(entry, sha256(canonical_bytes(entry.to_record())).hexdigest(), costs),),
        reviews=(ReviewSource(review, sha256(review.to_bytes()).hexdigest(), native[1], settlements, costs),))
    before = files_under(tmp_path)
    with monkeypatch.context() as guarded:
        deny_effects(guarded)
        one = recommend_portfolio(evaluation(snapshot), evidence)
        two = recommend_portfolio(evaluation(snapshot), evidence)
    assert one.to_bytes() == two.to_bytes()
    assert files_under(tmp_path) == before
    result = one.to_dict()
    assert {s["kind"] for s in result["sources"]} == {"PROPOSAL", "REPORT", "ADVISORY", "COST", "REVIEW"}
    assert all(s["custody"] == "NATIVE_REPLAY" for s in result["sources"])
    assert all(s["attestation"] == "NOT_PROVIDER_ATTESTED" and s["native_refs"] for s in result["sources"])
    assert result["costs"][0]["evidence_sha256"] is None  # native checksum is not a receipt
    assert result["decision"] == "INCONCLUSIVE" and result["evidence_quality"] == "CONFLICTED"
    assert result["live_outcome_verified"] is False
    assert {c["code"] for c in result["counterevidence"]} >= {"A10_COUNTEREVIDENCE", "A11_COUNTEREVIDENCE_OR_RETRACTION"}
    assert "SOURCE_SCOPE_MISMATCH" in result["reason_codes"]  # native A16 is a different scope
    assert result["cost_totals"][0]["estimated"] == "1.2"  # embedded A16 cost not counted twice
    assert result["attribution"]["reviews"][0]["tiers"] == ["A"]
    assert result["attribution"]["reviews"][0]["scope_link"] == "MISMATCH"
    assert validate_recommendation(result) == result


@pytest.mark.parametrize("value,nominal", [(120, "KEEP"), (55, "CHANGE"), (0, "STOP")])
def test_explicit_keep_change_stop_policy_never_upgrades_synthetic_facts(tmp_path, value, nominal):
    snapshot, _, _, source = analytics(tmp_path, values=[value])
    request = evaluation(snapshot)
    record = recommend_portfolio(request, EvidenceBundle(reports=(source,))).to_dict()
    assert record["measurements"][0]["value"] == str(value)
    assert record["sources"][0]["data_kind"] == "SYNTHETIC_FIXTURE"
    assert record["sources"][0]["scope_link"] == "MATCHED"
    # This labeled fixture's nominal band is not a portfolio decision.
    policy = request["human_policy"]
    assert nominal == ("KEEP" if Decimal(str(value)) >= Decimal(policy["keep_at_or_above"]) else
                       "STOP" if Decimal(str(value)) <= Decimal(policy["stop_at_or_below"]) else "CHANGE")
    assert record["decision"] == "INCONCLUSIVE"
    assert "AUTHORITATIVE_EVIDENCE_REQUIRED_FOR_KEEP_CHANGE_STOP" in record["reason_codes"]
    assert not record["live_outcome_verified"]


@pytest.mark.parametrize("values,status,value", [([0], "AVAILABLE", "0"), ([None], "NOT_AVAILABLE", None), ([0, None], "AVAILABLE", "0")])
def test_native_replayed_zero_and_missing_are_distinct(tmp_path, values, status, value):
    snapshot, _, _, source = analytics(tmp_path, values=values)
    record = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(source,))).to_dict()
    measurement = record["measurements"][0]
    assert measurement["status"] == status and measurement["value"] == value
    assert ("REPORT_OBSERVATIONS_PARTIAL" in record["missingness"]) == (None in values)
    assert record["decision"] == "INCONCLUSIVE"


def test_claimed_zero_without_snapshot_custody_is_unverified(tmp_path):
    snapshot, _, report, _ = analytics(tmp_path, values=[0])
    record = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(
        ReportSource(report, sha256(report.to_bytes()).hexdigest()),))).to_dict()
    assert record["sources"][0]["custody"] == "UNVERIFIED"
    assert record["measurements"][0]["status"] == "UNVERIFIED" and record["measurements"][0]["value"] is None


def test_declared_ratio_with_missing_denominator_is_not_numeric_performance(tmp_path):
    snapshot, _, _, source = analytics(tmp_path, values=[25], denominator="impressions")
    record = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(source,))).to_dict()
    assert record["measurements"][0]["status"] == "INSUFFICIENT_DATA"
    assert record["measurements"][0]["denominator_value"] is None
    assert record["measurements"][0]["value"] is None


def test_native_comparison_is_descriptive_and_requires_explicit_comparable_windows(tmp_path):
    current, _, right, rs = analytics(tmp_path, values=[12])
    _, _, left, ls = analytics(tmp_path, name="baseline", values=[2], window=BASELINE)
    comparison = compare_reports(left, right)
    cs = ComparisonSource(comparison, sha256(comparison.to_bytes()).hexdigest())
    evidence = EvidenceBundle(reports=(ls, rs), comparisons=(cs,))
    record = recommend_portfolio(evaluation(current, baseline=BASELINE), evidence).to_dict()
    assert record["comparisons"][0]["absolute_change"] == "10"
    assert record["comparisons"][0]["relative_change"] == "5"
    assert record["comparisons"][0]["claim"] == "DESCRIPTIVE_NO_CAUSAL_UPLIFT"
    assert record["decision"] == "INCONCLUSIVE"
    unsupported = recommend_portfolio(evaluation(current), evidence).to_dict()
    assert unsupported["comparisons"][0]["status"] == "NOT_COMPARABLE"
    assert unsupported["comparisons"][0]["absolute_change"] is None
    assert unsupported["comparisons"][0]["relative_change"] is None


def test_partial_observations_cannot_produce_numeric_comparison(tmp_path):
    current, _, right, rs = analytics(tmp_path, values=[12, None])
    _, _, left, ls = analytics(tmp_path, name="baseline", values=[2], window=BASELINE)
    comparison = compare_reports(left, right)
    value = recommend_portfolio(evaluation(current, baseline=BASELINE), EvidenceBundle(reports=(ls, rs),
        comparisons=(ComparisonSource(comparison, sha256(comparison.to_bytes()).hexdigest()),))).to_dict()
    assert value["comparisons"][0]["absolute_change"] is None
    assert value["comparisons"][0]["status"] == "NOT_COMPARABLE"


def test_evidence_order_does_not_change_canonical_record_and_native_refs_are_exact(tmp_path):
    current, _, right, rs = analytics(tmp_path, values=[12])
    baseline, _, left, ls = analytics(tmp_path, name="baseline", values=[2], window=BASELINE)
    comparison = compare_reports(left, right)
    comp = ComparisonSource(comparison, sha256(comparison.to_bytes()).hexdigest())
    one = recommend_portfolio(evaluation(current, baseline=BASELINE), EvidenceBundle(reports=(ls, rs), comparisons=(comp,)))
    two = recommend_portfolio(evaluation(current, baseline=BASELINE), EvidenceBundle(reports=(rs, ls), comparisons=(comp,)))
    assert one.to_bytes() == two.to_bytes()
    receipt = next(s for s in one.to_dict()["sources"] if s["source_id"] == right.report_id)
    assert {"ref": current.snapshot_id, "sha256": sha256(current.to_bytes()).hexdigest()} in receipt["native_refs"]


def test_native_float_extremes_remain_bounded_decimal_strings(tmp_path):
    snapshot, _, _, rs = analytics(tmp_path, values=[1e-100])
    result = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs,))).to_dict()
    assert Decimal(result["measurements"][0]["value"]) == Decimal("1e-100")


@pytest.mark.parametrize("mismatch", ["account", "campaign", "product", "platform", "metric", "denominator", "window", "tier"])
def test_mismatched_identity_or_definition_cannot_produce_numeric_delta(tmp_path, mismatch):
    current, _, right, rs = analytics(tmp_path, values=[12])
    _, _, left, ls = analytics(tmp_path, name="baseline", values=[2], window=BASELINE)
    comparison = compare_reports(left, right)
    request = evaluation(current, baseline=BASELINE)
    if mismatch == "account":
        request["scope"]["account_ref"]["id"] = "other-account"
    elif mismatch == "campaign":
        request["scope"]["campaign_ref"] = "other-campaign"
    elif mismatch == "product":
        request["scope"]["product_ref"] = "unrelated-product"
    elif mismatch == "platform":
        request["scope"]["platform"] = request["metric"]["platform"] = "YOUTUBE"
    elif mismatch == "metric":
        request["metric"]["definition_version"] = "different-definition"
    elif mismatch == "denominator":
        request["metric"]["denominator"] = "other-denominator"
    elif mismatch == "window":
        request["measurement_window"] = ["2026-11-01T01:00:00Z", "2026-11-02T01:00:00Z"]
    else:
        request["scope"]["attribution_tier"] = "A"
    record = recommend_portfolio(request, EvidenceBundle(reports=(rs, ls), comparisons=(
        ComparisonSource(comparison, sha256(comparison.to_bytes()).hexdigest()),))).to_dict()
    assert record["comparisons"][0]["absolute_change"] is None
    assert record["comparisons"][0]["relative_change"] is None
    assert record["decision"] == "INCONCLUSIVE"


def test_cross_cohort_native_comparison_cannot_become_ranked_roi(tmp_path):
    current, _, right, rs = analytics(tmp_path, values=[12])
    _, _, left, ls = analytics(tmp_path, name="baseline", values=[2], window=BASELINE, cohort="different-cohort")
    comp = compare_reports(left, right)
    assert comp.status == "NOT_COMPARABLE"
    value = recommend_portfolio(evaluation(current, baseline=BASELINE), EvidenceBundle(reports=(ls, rs),
        comparisons=(ComparisonSource(comp, sha256(comp.to_bytes()).hexdigest()),))).to_dict()
    assert value["comparisons"][0]["status"] == "NOT_COMPARABLE"
    assert value["comparisons"][0]["absolute_change"] is None


def test_comparison_reference_swap_or_forged_uplift_fails_native_replay(tmp_path):
    current, _, right, rs = analytics(tmp_path, values=[12])
    _, _, left, ls = analytics(tmp_path, name="baseline", values=[2], window=BASELINE)
    from ai_commerce.learning import seal_contract
    body = compare_reports(left, right).to_dict()
    body.pop("comparison_id")
    body["absolute_change"]["value"] = 9999
    forged = compare_reports(left, right).__class__(seal_contract("ComparisonResult", body))
    with pytest.raises(PortfolioError, match="COMPARISON_REPLAY_MISMATCH"):
        recommend_portfolio(evaluation(current), EvidenceBundle(reports=(ls, rs), comparisons=(
            ComparisonSource(forged, sha256(forged.to_bytes()).hexdigest()),)))


def test_cost_estimates_supplied_charges_and_synthetic_charges_stay_separate(tmp_path):
    snapshot, _, _, rs = analytics(tmp_path)
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    selections = (cost_source(ledger), cost_source(ledger, entry_id="manual", status="CHARGED", amount="2.30", origin="USER_SUPPLIED"),
        cost_source(ledger, entry_id="synthetic", status="CHARGED", amount="99.99", origin="SYNTHETIC_FIXTURE"),
        cost_source(ledger, entry_id="vnd", amount="1000", currency="VND", intent_digest="b" * 64),
        cost_source(ledger, entry_id="pending", status="PENDING", amount=None),
        cost_source(ledger, entry_id="unknown", status="UNKNOWN", amount=None, currency=None))
    value = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs,), costs=selections)).to_dict()
    buckets = {row["currency"]: row for row in value["cost_totals"]}
    assert buckets["USD"] == {"currency": "USD", "estimated": "1.2", "supplied_charged": "2.3", "synthetic_charged": "99.99",
        "pending_count": 1, "unknown_count": 0, "complete": False, "provider_charge_verified": False}
    assert buckets["VND"]["estimated"] == "1000" and buckets["VND"]["supplied_charged"] is None
    assert "CURRENCY_MISMATCH_NO_FX" in value["reason_codes"]
    assert all(row["allocation"] == "UNVERIFIED_NO_NATIVE_CAMPAIGN_BINDING" for row in value["costs"])
    assert "COST_AMOUNT_OR_CURRENCY" in value["missingness"]
    assert value["decision"] == "INCONCLUSIVE"


def test_same_currency_exact_decimal_zero_and_ambient_precision_do_not_change_result(tmp_path):
    snapshot, _, _, rs = analytics(tmp_path)
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    selected = (cost_source(ledger, entry_id="zero", amount="0.000000"),
                cost_source(ledger, entry_id="fraction", amount="0.123456", intent_digest="b" * 64))
    evidence = EvidenceBundle(reports=(rs,), costs=selected)
    one = recommend_portfolio(evaluation(snapshot), evidence)
    with localcontext() as ctx:
        ctx.prec = 2
        two = recommend_portfolio(evaluation(snapshot), evidence)
    assert one.to_bytes() == two.to_bytes()
    assert one.to_dict()["cost_totals"][0]["estimated"] == "0.123456"
    assert "CURRENCY_MISMATCH_NO_FX" not in one.to_dict()["reason_codes"]


def test_duplicate_sources_costs_and_same_receipt_aliases_rejected(tmp_path):
    snapshot, _, _, rs = analytics(tmp_path)
    with pytest.raises(PortfolioError, match="DUPLICATE_SOURCE"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs, rs)))
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    first = cost_source(ledger, entry_id="one", status="CHARGED", origin="USER_SUPPLIED")
    alias = cost_source(ledger, entry_id="two", status="CHARGED", origin="USER_SUPPLIED",
                        evidence_ref=first.entry.evidence_ref, evidence_sha256=first.entry.evidence_sha256)
    with pytest.raises(PortfolioError, match="DUPLICATE_COST_OBSERVATION|DUPLICATE_CHARGE_RECEIPT"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(costs=(first, alias)))


def test_changed_cost_version_cannot_reuse_prior_result(tmp_path):
    snapshot, _, _, rs = analytics(tmp_path)
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    source = cost_source(ledger)
    old = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs,), costs=(source,)))
    changed = replace(source.entry, amount="9.99")
    ledger.registry.safe_path(CostLedger.ref(source.entry.entry_id)).write_bytes(canonical_bytes(changed.to_record()))
    with pytest.raises(PortfolioError, match="COST_REPLAY_MISMATCH"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs,), costs=(source,)))
    updated = CostSource(changed, sha256(canonical_bytes(changed.to_record())).hexdigest(), ledger)
    assert recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs,), costs=(updated,))).recommendation_id != old.recommendation_id


def test_cost_corrupt_checksum_or_swapped_origin_cannot_pass(tmp_path):
    snapshot, _, _, _ = analytics(tmp_path)
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    source = cost_source(ledger)
    body = source.entry.to_record()
    body["amount"] = "888.88"
    ledger.registry.safe_path(CostLedger.ref(source.entry.entry_id)).write_bytes(canonical_bytes(body))
    with pytest.raises(PortfolioError, match="NATIVE_SOURCE_VERIFICATION_FAILED"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(costs=(source,)))
    with pytest.raises(PortfolioError, match="SOURCE_HASH_PIN_MISMATCH"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(costs=(replace(source, sha256="0" * 64),)))


def test_report_arithmetic_and_snapshot_ref_swap_cannot_pass(tmp_path):
    snapshot, _, report, rs = analytics(tmp_path, values=[0])
    body = report.to_dict()
    body.pop("report_id")
    body["measurement"]["value"] = body["measurement"]["numerator"] = 1000
    forged = PerformanceReport(body)
    with pytest.raises(PortfolioError, match="REPORT_REPLAY_MISMATCH"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(replace(rs, report=forged, sha256=sha256(forged.to_bytes()).hexdigest()),)))
    _, _, _, other = analytics(tmp_path, name="other", values=[999])
    with pytest.raises(PortfolioError, match="REPORT_REPLAY_MISMATCH"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(replace(rs, snapshots=other.snapshots),)))


def test_duplicate_report_observations_cannot_be_independent_support(tmp_path):
    snapshot, _, report, rs = analytics(tmp_path, values=[1, 3])
    mean = build_performance_report(snapshot, aggregation="MEAN")
    other = replace(rs, report=mean, sha256=sha256(mean.to_bytes()).hexdigest(), aggregation="MEAN")
    with pytest.raises(PortfolioError, match="DUPLICATE_REPORT_OBSERVATION"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs, other)))


def test_source_pin_and_context_pin_are_mandatory_on_each_read(tmp_path):
    snapshot, path, _, rs = analytics(tmp_path)
    with pytest.raises(PortfolioError, match="SOURCE_HASH_PIN_MISMATCH"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(replace(rs, sha256="f" * 64),)))
    pin = replace(rs.snapshots[0].pin, context_sha256="f" * 64)
    with pytest.raises(PortfolioError, match="NATIVE_SOURCE_VERIFICATION_FAILED"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(replace(rs, snapshots=(SnapshotSource(AnalyticsLedger(path), pin),)),)))
    path.unlink()
    with pytest.raises(PortfolioError, match="NATIVE_SOURCE_VERIFICATION_FAILED"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs,)))


def test_native_a10_hypothesis_alone_no_demand_and_removed_origin_fails_reopen(tmp_path):
    snapshot, _, _, _ = analytics(tmp_path)
    signals = SignalLedger(tmp_path / "signals.sqlite")
    series, _, _ = add_series(signals)
    proposal, = propose_opportunities(trend_series=[series], signal_ledger=signals, as_of=A10_CLOCK)
    raw = canonical_bytes(proposal)
    source = ProposalSource(raw, sha256(raw).hexdigest(), (canonical_bytes(series),), signals)
    value = recommend_portfolio(evaluation(snapshot), EvidenceBundle(proposals=(source,))).to_dict()
    assert value["sources"][0]["epistemic_status"] == "HYPOTHESIS"
    assert value["decision"] == "INCONCLUSIVE" and value["measurements"] == []
    assert "A10_HYPOTHESIS_NOT_OBSERVED_DEMAND" in value["reason_codes"]
    Path(signals.path).unlink()
    with pytest.raises(PortfolioError, match="NATIVE_SOURCE_VERIFICATION_FAILED"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(proposals=(source,)))


def test_native_proposal_without_custody_is_explicitly_unverified(tmp_path):
    snapshot, _, _, _ = analytics(tmp_path)
    signals = SignalLedger(tmp_path / "signals.sqlite")
    series, _, _ = add_series(signals)
    proposal, = propose_opportunities(trend_series=[series], signal_ledger=signals, as_of=A10_CLOCK)
    raw = canonical_bytes(proposal)
    value = recommend_portfolio(evaluation(snapshot), EvidenceBundle(proposals=(ProposalSource(raw, sha256(raw).hexdigest()),))).to_dict()
    assert value["sources"][0]["custody"] == "UNVERIFIED" and value["decision"] == "INCONCLUSIVE"


def test_advisory_replacement_or_new_revision_invalidates_retained_export(tmp_path):
    snapshot, _, report, _ = analytics(tmp_path)
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    previous = registry.append(**_packet(snapshot, report), snapshots=[snapshot], reports=[report]).to_dict()
    advisory = export_advisory(registry)
    source = AdvisorySource(advisory, sha256(advisory.to_bytes()).hexdigest(), registry)
    original = recommend_portfolio(evaluation(snapshot), EvidenceBundle(advisories=(source,)))
    registry.append(**_packet(snapshot, report, previous=previous), snapshots=[snapshot], reports=[report])
    with pytest.raises(PortfolioError, match="ADVISORY_REPLAY_MISMATCH"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(advisories=(source,)))
    newer = export_advisory(registry)
    updated = recommend_portfolio(evaluation(snapshot), EvidenceBundle(advisories=(AdvisorySource(newer, sha256(newer.to_bytes()).hexdigest(), registry),)))
    assert original.recommendation_id != updated.recommendation_id
    assert updated.to_dict()["counterevidence"]


def test_forged_advisory_hash_chain_and_attestation_are_revalidated(tmp_path):
    snapshot, _, report, _ = analytics(tmp_path)
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    registry.append(**_packet(snapshot, report), snapshots=[snapshot], reports=[report])
    body = export_advisory(registry).to_dict()
    body["entries"][0]["event_hash"] = "0" * 64
    body["export_id"] = "a11-advisory-" + digest({k: v for k, v in body.items() if k != "export_id"})
    forged = object.__new__(LearningAdvisory)
    object.__setattr__(forged, "_raw", canonical_bytes(body))
    with pytest.raises(PortfolioError, match="NATIVE_SOURCE_VERIFICATION_FAILED"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(advisories=(AdvisorySource(forged, sha256(forged.to_bytes()).hexdigest()),)))


@pytest.mark.parametrize("status", ["PENDING", "SETTLED"])
def test_native_a16_pending_and_supplied_settled_are_not_paid_proof(tmp_path, native, status):
    snapshot, _, _, _ = analytics(tmp_path)
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    settlements.append(build(document_for(native[1], status, attr=native[2]), attribution=native[2]))
    review = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs)
    source = ReviewSource(review, sha256(review.to_bytes()).hexdigest(), native[1], settlements, costs)
    value = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reviews=(source,))).to_dict()
    assert value["decision"] == "INCONCLUSIVE" and value["attribution"]["paid_proof_verified"] is False
    assert "PAID_SETTLEMENT_PROOF" in value["missingness"]
    assert value["attribution"]["reviews"][0]["tiers"] == ["A"]


@pytest.mark.parametrize("field", ["realized_profit", "actual_settlement_status", "actual_orders_verified", "external_provider_charge_verified"])
def test_forged_native_roi_settlement_or_charge_flags_fail_replay(tmp_path, native, field):
    snapshot, _, _, _ = analytics(tmp_path)
    registry = ArtifactRegistry(tmp_path / "store")
    review = commercial_review(commerce_run=native[0], settlement_ledger=SettlementLedger(registry), cost_ledger=CostLedger(registry))
    body = review.to_dict()
    body[field] = ({"status": "VERIFIED", "amount": "100", "verified": True} if field == "realized_profit" else
                   "SETTLED" if field == "actual_settlement_status" else True)
    forged = object.__new__(CommercialReview)
    object.__setattr__(forged, "_raw", canonical_bytes(body))
    with pytest.raises(PortfolioError, match="NATIVE_SOURCE_VERIFICATION_FAILED"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reviews=(ReviewSource(forged, sha256(forged.to_bytes()).hexdigest()),)))


def test_a16_reopening_detects_changed_settlement_history_and_refund(tmp_path, native):
    snapshot, _, _, _ = analytics(tmp_path)
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    paid = build(document_for(native[1], "SETTLED", attr=native[2]), attribution=native[2])
    settlements.append(paid)
    review = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs)
    source = ReviewSource(review, sha256(review.to_bytes()).hexdigest(), native[1], settlements, costs)
    recommend_portfolio(evaluation(snapshot), EvidenceBundle(reviews=(source,)))
    doc = revision(paid, "REFUNDED")
    receipt(doc, "REFUND", "100.00")
    settlements.append(build(doc, attribution=native[2], correction_of=paid))
    with pytest.raises(PortfolioError, match="REVIEW_REPLAY_MISMATCH"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reviews=(source,)))
    changed = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs)
    updated = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reviews=(
        ReviewSource(changed, sha256(changed.to_bytes()).hexdigest(), native[1], settlements, costs),))).to_dict()
    assert {c["code"] for c in updated["counterevidence"]} == {"A16_UPSTREAM_HOLD", "SETTLEMENT_REFUND_OR_REVOCATION"}


def test_a16_embedded_cost_version_must_match_selected_a15(tmp_path, native):
    snapshot, _, _, _ = analytics(tmp_path)
    registry = ArtifactRegistry(tmp_path / "store")
    ledger = CostLedger(registry)
    selected = native_cost(ledger)
    entry = ledger.get(selected["entry_id"])
    review = commercial_review(commerce_run=native[0], settlement_ledger=SettlementLedger(registry),
                               cost_ledger=ledger, cost_selections=[selected])
    changed = replace(entry, amount="9.99")
    ledger.registry.safe_path(CostLedger.ref(entry.entry_id)).write_bytes(canonical_bytes(changed.to_record()))
    with pytest.raises(PortfolioError, match="COST_VERSION_CONFLICT"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(
            costs=(CostSource(changed, sha256(canonical_bytes(changed.to_record())).hexdigest(), ledger),),
            reviews=(ReviewSource(review, sha256(review.to_bytes()).hexdigest()),)))


def test_stale_policy_and_source_do_not_renew_with_new_collection_clock(tmp_path):
    snapshot, _, _, rs = analytics(tmp_path)
    request = evaluation(snapshot)
    request["human_policy"]["max_evidence_age_seconds"] = 10
    request["human_policy"]["valid_until"] = "2026-11-02T00:00:00Z"
    value = recommend_portfolio(request, EvidenceBundle(reports=(rs,))).to_dict()
    assert {"STALE_EVIDENCE", "POLICY_OUTSIDE_VALIDITY"} <= set(value["reason_codes"])
    assert value["evidence_quality"] == "CONFLICTED" and value["decision"] == "INCONCLUSIVE"


def test_concurrent_native_replay_no_writes_and_candidates_do_not_share_account_scope(tmp_path, monkeypatch):
    snapshot, _, _, rs = analytics(tmp_path)
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    cs = cost_source(ledger)
    evidence = EvidenceBundle(reports=(rs,), costs=(cs,))
    request = evaluation(snapshot)
    before = files_under(tmp_path)
    with monkeypatch.context() as guarded:
        deny_effects(guarded)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: recommend_portfolio(request, evidence).to_bytes(), range(8)))
        other = deepcopy(request)
        other["candidate_id"] = "other"
        other["scope"]["account_ref"]["id"] = "different-account"
        portfolio = recommend_many(((request, evidence), (other, evidence)))
    assert len(set(results)) == 1 and files_under(tmp_path) == before
    by_id = {p.to_dict()["request"]["candidate_id"]: p.to_dict() for p in portfolio}
    assert by_id["other"]["measurements"][0]["value"] is None
    assert by_id["synthetic-candidate"]["measurements"][0]["value"] == "12"


def test_native_bundle_and_source_reference_limits(tmp_path):
    snapshot, _, _, rs = analytics(tmp_path)
    with pytest.raises(PortfolioError, match="TYPED_SOURCE_TUPLE_REQUIRED|SOURCE_BUNDLE_LIMIT"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(rs,) * 33))
    with pytest.raises(PortfolioError, match="SNAPSHOT_LIMIT"):
        recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(replace(rs, snapshots=rs.snapshots * 9),)))


def test_actual_registered_onboarding_planner_selects_new_owner_and_consumer_closure():
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    from ai_commerce.engineering.minimum_test import approved_groups, file_hashes
    work = json.loads((ROOT / "work_items/A18-1-PORTFOLIO-001.json").read_bytes())
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_bytes())
    files = file_hashes(ROOT)
    allowed = work["allowed_paths"]
    plan = registered_action_plan(repo=ROOT, base_sha=SOURCE_SHA,
        base_files={p: v for p, v in files.items() if p not in allowed}, candidate_files=files,
        changed_paths=allowed, required_acs=work["acceptance"], allowed_paths=allowed,
        fallback_argv=profiles["mt01-component-onboarding"], groups=approved_groups(), registered_profiles=profiles)
    assert plan["selected_groups"] == ["a10", "a11", "a15", "a16", "a18-1"]
    assert not plan["impact_v21"]["missing_mapping"]
    assert plan["unknown_acs"] == [] and not plan["fallback"]
    assert all(group["execute"] and group["state"] == "EXECUTE" for group in plan["groups"])
    assert {g["group"] for g in plan["groups"]} == set(plan["selected_groups"])
    for group in plan["groups"]:
        assert group["binding"]["argv"][-2:] == ["-n", "12"]
        if group["group"] == "a18-1":
            assert group["selected_profile"] == "mt01-component-onboarding"
            assert group["binding"]["onboarding_data_only"] is True
            assert group["test_files_count"] == 2
            assert all(path in group["binding"]["candidate_files"] for path in allowed)


def manual_provisional_case(tmp_path, value):
    """Local user-authored *test* rows, never provider-attested live observations."""
    name = "seed-" + str(value)
    seed, _, _, _ = analytics(tmp_path, name=name, values=(value,))
    plan = deepcopy(seed.to_dict()["import"]["plan"])
    plan["plan_id"] = "manual-" + str(value)
    ledger = AnalyticsLedger(tmp_path / ("manual-" + str(value) + ".sqlite"))
    imported = ledger.import_file(tmp_path / (name + "-rows.json"), plan=plan,
                                  profile="tiktok.manual.v1", file_format="json",
                                  source_kind="USER_AUTHORED_MANUAL")
    row = imported["import"]
    pin = SnapshotPin(row["import_id"], row["context_sha256"], row["raw_sha256"],
                      tuple(v["observation_id"] for v in imported["observations"]))
    snapshot = read_snapshot(ledger.path, pin=pin)
    report = build_performance_report(snapshot)
    return snapshot, ReportSource(report, sha256(report.to_bytes()).hexdigest(),
                                  (SnapshotSource(ledger, pin),))


@pytest.mark.parametrize("value,band", [(120, "KEEP"), (55, "CHANGE"), (0, "STOP")])
def test_manual_native_reporting_only_provisional_keep_change_stop(tmp_path, value, band):
    snapshot, source = manual_provisional_case(tmp_path, value)
    req = evaluation(snapshot)
    req["human_policy"]["requirement"] = "PROVISIONAL_LOCAL_DESCRIPTIVE"
    result = recommend_portfolio(req, EvidenceBundle(reports=(source,))).to_dict()
    assert result["decision"] == band
    assert result["confidence"] == "PROVISIONAL_USER_SUPPLIED_UNATTESTED"
    assert result["human_action"] == {"kind": "REVIEW_PROVISIONAL_RECOMMENDATION",
                                      "proposed_only": True, "execution_authority": "NONE"}
    assert result["sources"][0]["custody"] == "NATIVE_REPLAY"
    assert result["sources"][0]["data_kind"] == "USER_SUPPLIED"
    assert result["sources"][0]["attestation"] == "NOT_PROVIDER_ATTESTED"
    assert "PROVISIONAL_LOCAL_PERFORMANCE_ONLY" in result["reason_codes"]
    assert "AUTHORITATIVE_OBSERVATIONS" in result["missingness"]
    assert not result["live_outcome_verified"] and result["financial_commitment"] == "NONE"
    assert validate_recommendation(result) == result
    assert recommend_portfolio(req, EvidenceBundle(reports=(source,))).to_dict() == result


@pytest.mark.parametrize("failure", ["authoritative", "expired", "wrong_metric", "baseline", "missing"])
def test_provisional_mode_rejects_unsupported_local_evidence(tmp_path, failure):
    snapshot, source = manual_provisional_case(tmp_path, None if failure == "missing" else 120)
    req = evaluation(snapshot)
    req["human_policy"]["requirement"] = "PROVISIONAL_LOCAL_DESCRIPTIVE"
    if failure == "authoritative":
        req["human_policy"]["requirement"] = "COMPARABLE_AUTHORITATIVE_OBSERVATIONS"
    elif failure == "expired":
        req["human_policy"]["valid_until"] = "2026-11-02T00:00:00Z"
    elif failure == "wrong_metric":
        req["human_policy"]["metric_definition_sha256"] = "0" * 64
    elif failure == "baseline":
        req["baseline_window"] = BASELINE
    result = recommend_portfolio(req, EvidenceBundle(reports=(source,))).to_dict()
    assert result["decision"] == "INCONCLUSIVE"
    assert result["confidence"] == "INSUFFICIENT_FOR_DECISIVE_RECOMMENDATION"


def test_synthetic_source_never_gets_provisional_decision(tmp_path):
    snapshot, _, _, source = analytics(tmp_path, name="syn-provisional", values=(120,))
    req = evaluation(snapshot)
    req["human_policy"]["requirement"] = "PROVISIONAL_LOCAL_DESCRIPTIVE"
    result = recommend_portfolio(req, EvidenceBundle(reports=(source,))).to_dict()
    assert result["sources"][0]["data_kind"] == "SYNTHETIC_FIXTURE"
    assert result["decision"] == "INCONCLUSIVE"
