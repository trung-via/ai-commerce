"""A11-02 L03 descriptive performance reporting and pairwise comparison tests.

Covers:
- Deterministic golden cases (valid comparisons, positive/negative change, rate metrics)
- Explicit typed states: AVAILABLE (including zero), NOT_AVAILABLE, INSUFFICIENT_DATA, NOT_COMPARABLE
- zero != missing != insufficient != incompatible
- Denominator mismatch and zero baseline denominator handling
- Window overlap detection and disjoint window enforcement
- Staleness detection and caveats
- Cross-platform incompatibility under NO_CROSS_PROVIDER_EQUIVALENCE
- Negative cases: malformed schemas, forged hashes, nonfinite numbers, invalid dates/timezones
- Immutability and round-trip serialization of PerformanceReport and ComparisonResult
"""
from __future__ import annotations

import math
import socket
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import (
    EVIDENCE_STATES,
    LearningError,
    ObservationSnapshot,
    SnapshotPin,
    content_id,
    read_snapshot,
    seal_contract,
    validate_contract,
)
from ai_commerce.learning.reporting import (
    ComparisonResult,
    PerformanceReport,
    build_performance_report,
    compare_reports,
    parse_comparison_result,
    parse_performance_report,
    validate_comparison_result,
    validate_performance_report,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A11 reporting must not access the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def make_plan(*, window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"),
              platform="TIKTOK", surface="VIDEO", account_id="00001",
              cohort="synthetic-cohort", timezone="UTC+07:00",
              metric_key="plays", unit="count", denominator=None,
              definition_version="fixture-1"):
    return {
        "schema_version": "MeasurementPlan.v1",
        "plan_id": f"plan-{metric_key}-{platform.lower()}",
        "campaign_ref": "synthetic-campaign",
        "primary_metric": metric_key,
        "denominator": denominator,
        "metric_definition_version": definition_version,
        "platform": platform,
        "surface": surface,
        "account_ref": {
            "namespace": "tiktok_open_id" if platform == "TIKTOK" else
                         "youtube_channel_id" if platform == "YOUTUBE" else
                         "facebook_page_id" if platform == "FACEBOOK" else "instagram_user_id",
            "id": account_id,
        },
        "measurement_window": list(window),
        "timezone": timezone,
        "cohort": cohort,
        "source_mode": "MANUAL_IMPORT",
        "attribution_tier": "NOT_AVAILABLE",
        "missingness_rule": "NOT_AVAILABLE means null; no imputation",
        "source_status": VERIFICATION,
        "metric_definitions": [{
            "schema_version": "MetricDefinition.v1",
            "metric_key": metric_key,
            "provider_name": f"Synthetic {metric_key}",
            "definition_version": definition_version,
            "platform": platform,
            "unit": unit,
            "definition": f"Synthetic {metric_key} definition",
            "denominator": denominator,
            "cohort": cohort,
            "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE",
            "source_status": VERIFICATION,
        }],
    }


def make_snapshot(tmp_path, plan, row_specs, *, file_prefix="snap"):
    rows = []
    for post_id, status, val in row_specs:
        rows.append({
            "provider_post_id": post_id,
            "public_url": None,
            "metric_key": plan["primary_metric"],
            "provider_name": plan["metric_definitions"][0]["provider_name"],
            "definition_version": plan["metric_definitions"][0]["definition_version"],
            "status": status,
            "value": val,
        })
    raw = canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": rows})
    source = tmp_path / f"{file_prefix}.json"
    source.write_bytes(raw)
    ledger = AnalyticsLedger(tmp_path / f"{file_prefix}_a09.sqlite")
    profile = ("tiktok.manual.v1" if plan["platform"] == "TIKTOK" else
               "youtube.manual.v1" if plan["platform"] == "YOUTUBE" else
               "facebook.manual.v1" if plan["platform"] == "FACEBOOK" else "instagram.manual.v1")
    result = ledger.import_file(source, plan=plan, profile=profile, file_format="json", source_kind="SYNTHETIC_FIXTURE")
    record = result["import"]
    pin = SnapshotPin(
        record["import_id"],
        record["context_sha256"],
        record["raw_sha256"],
        tuple(item["observation_id"] for item in result["observations"]),
    )
    return read_snapshot(ledger.path, pin=pin)


# ---------------------------------------------------------------------------
# 1. PerformanceReport: Construction & Aggregation
# ---------------------------------------------------------------------------

def test_build_performance_report_golden_sum(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 10), ("p2", "AVAILABLE", 25)])
    report = build_performance_report(snap)

    assert isinstance(report, PerformanceReport)
    assert report.report_id.startswith("a11-report-")
    assert report.platform == "TIKTOK"
    assert report.surface == "VIDEO"
    assert report.measurement_status == "AVAILABLE"
    assert report.measurement_value == 35
    assert report.available_count == 2
    assert report.not_available_count == 0
    assert report.measurement["unit"] == "count"
    assert report.measurement["numerator"] == 35
    assert report.measurement["denominator"] is None
    assert report.measurement["reason"] is None
    assert report.to_dict()["inference"] == "DESCRIPTIVE_ONLY"
    assert len(report.snapshot_refs) == 1
    assert len(report.evidence_refs) == 2


def test_build_performance_report_mean_and_count_aggregations(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 10), ("p2", "AVAILABLE", 20)])
    
    rep_mean = build_performance_report(snap, aggregation="MEAN")
    assert rep_mean.measurement_value == 15.0
    assert rep_mean.measurement_status == "AVAILABLE"

    rep_count = build_performance_report(snap, aggregation="COUNT")
    assert rep_count.measurement_value == 2.0
    assert rep_count.measurement_status == "AVAILABLE"


def test_build_performance_report_with_explicit_denominator(tmp_path):
    plan = make_plan(metric_key="ctr", unit="ratio", denominator="impressions")
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 5), ("p2", "AVAILABLE", 15)])
    
    # Denominator value provided = 1000
    report = build_performance_report(snap, denominator_value=1000)
    assert report.measurement_status == "AVAILABLE"
    assert report.measurement_value == 0.02
    assert report.measurement["numerator"] == 20
    assert report.measurement["denominator"] == 1000

    # Denominator value = 0 -> INSUFFICIENT_DATA
    report_zero = build_performance_report(snap, denominator_value=0)
    assert report_zero.measurement_status == "INSUFFICIENT_DATA"
    assert report_zero.measurement_value is None
    assert report_zero.measurement["denominator"] == 0
    assert report_zero.measurement["reason"] == "Zero denominator"


# ---------------------------------------------------------------------------
# 2. Zero vs Missing vs Insufficient Data
# ---------------------------------------------------------------------------

def test_zero_not_coerced_to_missing(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 0)])
    report = build_performance_report(snap)

    assert report.measurement_status == "AVAILABLE"
    assert report.measurement_value == 0
    assert report.available_count == 1
    assert report.not_available_count == 0


def test_all_missing_is_typed_not_available(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "NOT_AVAILABLE", None), ("p2", "NOT_AVAILABLE", None)])
    report = build_performance_report(snap)

    assert report.measurement_status == "NOT_AVAILABLE"
    assert report.measurement_value is None
    assert report.available_count == 0
    assert report.not_available_count == 2
    assert report.measurement["reason"] == "ALL_OBSERVATIONS_NOT_AVAILABLE"


def test_partial_missing_preserves_zero_and_available(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 0), ("p2", "AVAILABLE", 10), ("p3", "NOT_AVAILABLE", None)])
    report = build_performance_report(snap)

    assert report.measurement_status == "AVAILABLE"
    assert report.measurement_value == 10
    assert report.available_count == 2
    assert report.not_available_count == 1
    assert any("1 of 3 observation(s) NOT_AVAILABLE" in c for c in report.caveats)


# ---------------------------------------------------------------------------
# 3. ComparisonResult: Golden Cases
# ---------------------------------------------------------------------------

def test_compare_reports_valid_positive_growth(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 100)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 150)], file_prefix="sb")

    rep_a = build_performance_report(snap_a)
    rep_b = build_performance_report(snap_b)

    comp = compare_reports(rep_a, rep_b)

    assert isinstance(comp, ComparisonResult)
    assert comp.comparison_id.startswith("a11-comparison-")
    assert comp.status == "AVAILABLE"
    assert comp.is_available is True
    assert comp.is_comparable is True
    assert comp.absolute_value == 50
    assert comp.relative_value == 0.5  # +50%
    assert comp.absolute_change["status"] == "AVAILABLE"
    assert comp.relative_change["status"] == "AVAILABLE"
    assert comp.comparison_policy == "NO_CROSS_PROVIDER_EQUIVALENCE"


def test_compare_reports_valid_negative_growth(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 100)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 60)], file_prefix="sb")

    rep_a = build_performance_report(snap_a)
    rep_b = build_performance_report(snap_b)

    comp = compare_reports(rep_a, rep_b)

    assert comp.status == "AVAILABLE"
    assert comp.absolute_value == -40
    assert comp.relative_value == -0.4  # -40%


def test_compare_reports_zero_baseline_insufficient_relative_data(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 0)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    rep_a = build_performance_report(snap_a)
    rep_b = build_performance_report(snap_b)

    comp = compare_reports(rep_a, rep_b)

    assert comp.status == "AVAILABLE"
    assert comp.absolute_change["status"] == "AVAILABLE"
    assert comp.absolute_value == 20
    assert comp.relative_change["status"] == "INSUFFICIENT_DATA"
    assert comp.relative_value is None
    assert comp.relative_change["reason"] == "Zero denominator"
    assert "ZERO_DENOMINATOR" in comp.reason_codes


def test_compare_reports_both_zero(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 0)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 0)], file_prefix="sb")

    comp = compare_reports(build_performance_report(snap_a), build_performance_report(snap_b))

    assert comp.status == "AVAILABLE"
    assert comp.absolute_value == 0
    assert comp.relative_change["status"] == "INSUFFICIENT_DATA"


def test_compare_reports_missing_baseline_is_not_available(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "NOT_AVAILABLE", None)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 50)], file_prefix="sb")

    comp = compare_reports(build_performance_report(snap_a), build_performance_report(snap_b))

    assert comp.status == "NOT_AVAILABLE"
    assert comp.absolute_change["status"] == "NOT_AVAILABLE"
    assert comp.absolute_value is None
    assert comp.relative_change["status"] == "NOT_AVAILABLE"
    assert comp.relative_value is None
    assert "MEASUREMENT_NOT_AVAILABLE" in comp.reason_codes


# ---------------------------------------------------------------------------
# 4. Incompatibility Checks (NOT_COMPARABLE)
# ---------------------------------------------------------------------------

def test_compare_reports_same_report_rejected(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 10)])
    rep = build_performance_report(snap)

    comp = compare_reports(rep, rep)
    assert comp.status == "NOT_COMPARABLE"
    assert "SAME_REPORT" in comp.reason_codes
    assert comp.absolute_value is None
    assert comp.relative_value is None


def test_compare_reports_cross_platform_prohibited(tmp_path):
    plan_tt = make_plan(platform="TIKTOK", window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_yt = make_plan(platform="YOUTUBE", window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))

    snap_tt = make_snapshot(tmp_path, plan_tt, [("p1", "AVAILABLE", 10)], file_prefix="tt")
    snap_yt = make_snapshot(tmp_path, plan_yt, [("p2", "AVAILABLE", 20)], file_prefix="yt")

    comp = compare_reports(build_performance_report(snap_tt), build_performance_report(snap_yt))
    assert comp.status == "NOT_COMPARABLE"
    assert "CROSS_PLATFORM_INCOMPATIBLE" in comp.reason_codes
    assert comp.comparison_policy == "NO_CROSS_PROVIDER_EQUIVALENCE"


def test_compare_reports_denominator_mismatch(tmp_path):
    plan_a = make_plan(metric_key="rate", denominator="impressions",
                       window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(metric_key="rate", denominator="views",
                       window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))

    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    comp = compare_reports(build_performance_report(snap_a), build_performance_report(snap_b))
    assert comp.status == "NOT_COMPARABLE"
    assert "DENOMINATOR_MISMATCH" in comp.reason_codes


def test_compare_reports_overlapping_windows(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-02T00:00:00+07:00", "2026-10-04T00:00:00+07:00"))

    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    comp = compare_reports(build_performance_report(snap_a), build_performance_report(snap_b))
    assert comp.status == "NOT_COMPARABLE"
    assert "OVERLAPPING_WINDOWS" in comp.reason_codes
    assert any(c["code"] == "OVERLAPPING_WINDOWS" for c in comp.confounders)


def test_compare_reports_window_duration_mismatch(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))  # 2 days
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-10T00:00:00+07:00"))  # 7 days

    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    comp = compare_reports(build_performance_report(snap_a), build_performance_report(snap_b))
    assert comp.status == "NOT_COMPARABLE"
    assert "WINDOW_DURATION_MISMATCH" in comp.reason_codes


def test_compare_reports_cohort_and_timezone_mismatch(tmp_path):
    plan_a = make_plan(cohort="cohort-A", timezone="UTC+07:00", window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(cohort="cohort-B", timezone="UTC+07:00", window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))

    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    comp_cohort = compare_reports(build_performance_report(snap_a), build_performance_report(snap_b))
    assert comp_cohort.status == "NOT_COMPARABLE"
    assert "COHORT_MISMATCH" in comp_cohort.reason_codes

    plan_c = make_plan(cohort="cohort-A", timezone="UTC", window=("2026-10-03T00:00:00Z", "2026-10-05T00:00:00Z"))
    snap_c = make_snapshot(tmp_path, plan_c, [("p3", "AVAILABLE", 20)], file_prefix="sc")

    comp_tz = compare_reports(build_performance_report(snap_a), build_performance_report(snap_c))
    assert comp_tz.status == "NOT_COMPARABLE"
    assert "TIMEZONE_MISMATCH" in comp_tz.reason_codes


def test_compare_reports_staleness(tmp_path):
    plan_a = make_plan(window=("2026-01-01T00:00:00+07:00", "2026-01-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-01-03T00:00:00+07:00", "2026-01-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    comp = compare_reports(
        build_performance_report(snap_a),
        build_performance_report(snap_b),
        as_of="2026-10-30T00:00:00+07:00",
        max_staleness_days=30,
    )
    assert comp.status == "NOT_COMPARABLE"
    assert "STALE_DATA" in comp.reason_codes
    assert any(c["code"] == "STALE_DATA" for c in comp.confounders)


# ---------------------------------------------------------------------------
# 5. Immutability, Serialization & Contract Verification
# ---------------------------------------------------------------------------

def test_performance_report_and_comparison_result_immutability(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    rep = build_performance_report(snap_a)
    with pytest.raises(FrozenInstanceError):
        rep._raw = b"mutated"

    comp = compare_reports(rep, build_performance_report(snap_b))
    with pytest.raises(FrozenInstanceError):
        comp._raw = b"mutated"

    # Detached copy does not mutate original instance
    d = rep.to_dict()
    d["measurement"]["value"] = 999
    assert rep.measurement_value == 10

    # Serialization round-trip
    rep_bytes = rep.to_bytes()
    assert PerformanceReport.from_bytes(rep_bytes).to_bytes() == rep_bytes
    assert parse_performance_report(rep_bytes).report_id == rep.report_id

    comp_bytes = comp.to_bytes()
    assert ComparisonResult.from_bytes(comp_bytes).to_bytes() == comp_bytes
    assert parse_comparison_result(comp_bytes).comparison_id == comp.comparison_id


# ---------------------------------------------------------------------------
# 6. Negative Cases & Schema Integrity
# ---------------------------------------------------------------------------

def test_content_id_detects_report_tampering(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 10)])
    rep_dict = build_performance_report(snap).to_dict()

    rep_dict["measurement"]["value"] = 999
    with pytest.raises(LearningError, match="CONTENT_ID_MISMATCH"):
        validate_performance_report(rep_dict)


def test_content_id_detects_comparison_tampering(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    comp_dict = compare_reports(build_performance_report(snap_a), build_performance_report(snap_b)).to_dict()
    comp_dict["caveats"].append("Tampered caveat without recalculating hash")
    with pytest.raises(LearningError, match="CONTENT_ID_MISMATCH"):
        validate_comparison_result(comp_dict)


def test_reject_untrusted_live_verified_claim(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 10)])
    rep_dict = build_performance_report(snap).to_dict()

    rep_dict["source_status"] = "LIVE_VERIFIED"
    with pytest.raises(LearningError):
        seal_contract("PerformanceReport", {k: v for k, v in rep_dict.items() if k != "report_id"})


def test_as_of_before_window_start_rejected(tmp_path):
    plan = make_plan(window=("2026-10-05T00:00:00+07:00", "2026-10-07T00:00:00+07:00"))
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 10)])
    with pytest.raises(LearningError, match="AS_OF_BEFORE_WINDOW_START"):
        build_performance_report(snap, as_of="2026-10-01T00:00:00+07:00")


def test_build_performance_report_multiple_snapshots_combined(tmp_path):
    plan = make_plan()
    snap1 = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 10)], file_prefix="s1")
    snap2 = make_snapshot(tmp_path, plan, [("p2", "AVAILABLE", 20)], file_prefix="s2")

    report = build_performance_report([snap1, snap2])
    assert report.measurement_value == 30
    assert report.available_count == 2
    assert len(report.snapshot_refs) == 2
    assert len(report.evidence_refs) == 2


def test_build_performance_report_snapshot_context_mismatch(tmp_path):
    plan1 = make_plan(platform="TIKTOK", window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan2 = make_plan(platform="TIKTOK", window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))

    snap1 = make_snapshot(tmp_path, plan1, [("p1", "AVAILABLE", 10)], file_prefix="s1")
    snap2 = make_snapshot(tmp_path, plan2, [("p2", "AVAILABLE", 20)], file_prefix="s2")

    with pytest.raises(LearningError, match="SNAPSHOT_CONTEXT_MISMATCH"):
        build_performance_report([snap1, snap2])


def test_build_performance_report_negative_values(tmp_path):
    plan = make_plan()
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", -15), ("p2", "AVAILABLE", -5)])
    report = build_performance_report(snap)

    assert report.measurement_status == "AVAILABLE"
    assert report.measurement_value == -20
    assert report.available_count == 2


def test_compare_reports_inverted_window_order(tmp_path):
    # Baseline starts after comparison
    plan_a = make_plan(window=("2026-10-05T00:00:00+07:00", "2026-10-07T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    comp = compare_reports(build_performance_report(snap_a), build_performance_report(snap_b))
    assert comp.status == "NOT_COMPARABLE"
    assert "INVERTED_WINDOW_ORDER" in comp.reason_codes


def test_compare_reports_surface_and_account_mismatch(tmp_path):
    plan_a = make_plan(surface="VIDEO", account_id="acc1", window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(surface="PROFILE_VIDEO", account_id="acc1", window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    plan_c = make_plan(surface="VIDEO", account_id="acc2", window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))

    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")
    snap_c = make_snapshot(tmp_path, plan_c, [("p3", "AVAILABLE", 30)], file_prefix="sc")

    rep_a = build_performance_report(snap_a)
    rep_b = build_performance_report(snap_b)
    rep_c = build_performance_report(snap_c)

    comp_surf = compare_reports(rep_a, rep_b)
    assert comp_surf.status == "NOT_COMPARABLE"
    assert "SURFACE_MISMATCH" in comp_surf.reason_codes

    # With allow_cross_surface=True
    comp_surf_allowed = compare_reports(rep_a, rep_b, allow_cross_surface=True)
    assert comp_surf_allowed.status == "AVAILABLE"
    assert comp_surf_allowed.absolute_value == 10

    comp_acc = compare_reports(rep_a, rep_c)
    assert comp_acc.status == "NOT_COMPARABLE"
    assert "ACCOUNT_MISMATCH" in comp_acc.reason_codes

    # With allow_cross_account=True
    comp_acc_allowed = compare_reports(rep_a, rep_c, allow_cross_account=True)
    assert comp_acc_allowed.status == "AVAILABLE"
    assert comp_acc_allowed.absolute_value == 20


def test_compare_reports_metric_key_provider_version_unit_mismatch(tmp_path):
    w1 = ("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00")
    w2 = ("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00")

    plan_base = make_plan(metric_key="plays", unit="count", definition_version="fixture-1", window=w1)
    plan_key = make_plan(metric_key="likes", unit="count", definition_version="fixture-1", window=w2)
    plan_ver = make_plan(metric_key="plays", unit="count", definition_version="fixture-2", window=w2)
    plan_unit = make_plan(metric_key="plays", unit="seconds", definition_version="fixture-1", window=w2)

    snap_base = make_snapshot(tmp_path, plan_base, [("p1", "AVAILABLE", 10)], file_prefix="s_base")
    snap_key = make_snapshot(tmp_path, plan_key, [("p2", "AVAILABLE", 20)], file_prefix="s_key")
    snap_ver = make_snapshot(tmp_path, plan_ver, [("p3", "AVAILABLE", 30)], file_prefix="s_ver")
    snap_unit = make_snapshot(tmp_path, plan_unit, [("p4", "AVAILABLE", 40)], file_prefix="s_unit")

    rep_base = build_performance_report(snap_base)

    comp_key = compare_reports(rep_base, build_performance_report(snap_key))
    assert comp_key.status == "NOT_COMPARABLE"
    assert "METRIC_KEY_MISMATCH" in comp_key.reason_codes

    comp_ver = compare_reports(rep_base, build_performance_report(snap_ver))
    assert comp_ver.status == "NOT_COMPARABLE"
    assert "METRIC_VERSION_MISMATCH" in comp_ver.reason_codes

    comp_unit = compare_reports(rep_base, build_performance_report(snap_unit))
    assert comp_unit.status == "NOT_COMPARABLE"
    assert "UNIT_MISMATCH" in comp_unit.reason_codes


def test_compare_reports_allow_overlapping_windows(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-02T00:00:00+07:00", "2026-10-04T00:00:00+07:00"))

    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 30)], file_prefix="sb")

    comp = compare_reports(
        build_performance_report(snap_a),
        build_performance_report(snap_b),
        allow_overlapping_windows=True,
    )
    assert comp.status == "AVAILABLE"
    assert comp.absolute_value == 20
    assert "OVERLAPPING_WINDOWS" in comp.reason_codes
    assert any(c["code"] == "OVERLAPPING_WINDOWS" for c in comp.confounders)


def test_performance_report_and_comparison_classmethod_build(tmp_path):
    plan_a = make_plan(window=("2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"))
    plan_b = make_plan(window=("2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"))
    snap_a = make_snapshot(tmp_path, plan_a, [("p1", "AVAILABLE", 10)], file_prefix="sa")
    snap_b = make_snapshot(tmp_path, plan_b, [("p2", "AVAILABLE", 20)], file_prefix="sb")

    rep_a = PerformanceReport.build(snap_a)
    rep_b = PerformanceReport.build(snap_b)
    comp = ComparisonResult.build(rep_a, rep_b)

    assert rep_a.report_ref() == {"report_id": rep_a.report_id}
    assert comp.comparison_ref() == {"comparison_id": comp.comparison_id}
    assert comp.is_available is True
    assert comp.absolute_value == 10


def test_empty_or_invalid_snapshot_inputs():
    with pytest.raises(LearningError, match="SNAPSHOT_REQUIRED"):
        build_performance_report([])
    with pytest.raises(LearningError, match="SNAPSHOT_REQUIRED"):
        build_performance_report(None)
    with pytest.raises(LearningError, match="INVALID_SNAPSHOT"):
        build_performance_report(["not-a-snapshot"])


def test_metric_not_found_in_snapshot(tmp_path):
    plan = make_plan(metric_key="plays")
    snap = make_snapshot(tmp_path, plan, [("p1", "AVAILABLE", 10)])
    with pytest.raises(LearningError, match="METRIC_NOT_FOUND_IN_SNAPSHOT"):
        build_performance_report(snap, metric_key="nonexistent_metric")
