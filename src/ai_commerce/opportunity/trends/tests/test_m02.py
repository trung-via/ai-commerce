"""M02 acceptance cases using the actual immutable M01 importer/replay API."""
from __future__ import annotations

import json
import socket
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.opportunity.core.models import SignalLedgerError, SignalValidationError, digest
from ai_commerce.opportunity.core.schemas import SOURCE_STATUS
from ai_commerce.opportunity.signals import SignalLedger
from ai_commerce.opportunity.trends import (
    TrendRule, build_series, evaluate_trend, schema, validate_contract, validate_series,
)

AS_OF = "2026-10-29T12:00:00Z"
COMPONENT = Path(__file__).resolve().parents[1]


def context(**changes):
    result = {
        "schema_version": "TrendComparisonContext.v1", "metric": "manual_mentions",
        "metric_kind": "COUNT", "baseline_id": "human-counting-protocol.v1",
        "normalization_id": "raw-count.v1", "query_batch": None, "relative_scale": None,
        "window_kind": "POINT", "evidence_refs": ["fixture://human-comparison-note"],
        "source_status": SOURCE_STATUS,
    }
    result.update(changes)
    return result


def rows(values=(10, 10, 20), **changes):
    result = []
    for n, value in enumerate(values):
        instant = datetime(2026, 10, 27, tzinfo=timezone.utc) + timedelta(days=n)
        row = {
            "topic": "coffee", "signal_type": "TOPIC", "source_type": "MANUAL_NOTES",
            "locale": "vi-VN", "geo": "VN", "query_context": "coffee:manual-observations",
            "observed_at": instant.isoformat(), "collected_at": AS_OF, "timezone": "UTC",
            "unit": "count", "denominator": None, "observation_window": None,
            "status": "NOT_AVAILABLE" if value is None else "AVAILABLE", "value": value,
            "rights_status": "UNKNOWN", "evidence_refs": ["fixture://original-human-text"],
        }
        row.update(changes)
        result.append(row)
    return result


@pytest.fixture
def ledger(tmp_path):
    return SignalLedger(tmp_path / "signals.sqlite")


def add(ledger, items=None, *, bom=False, correction_of=None, **snapshot_changes):
    raw = canonical_bytes({"schema_version": "ManualSignalRows.v1", "rows": rows() if items is None else items})
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    snapshot = {
        "schema_version": "SignalSourceSnapshot.v1", "source_id": "human-signal-source",
        "source_kind": "HUMAN_SUPPLIED_FILE", "source_ref": "fixture://signals.json",
        "captured_at": AS_OF, "collected_at": AS_OF, "sha256": sha256_bytes(raw),
        "rights_status": "UNKNOWN", "verification_status": SOURCE_STATUS, "geo": "VN", "locale": "vi-VN",
        "notes": "Synthetic local test data; no provider facts.",
    }
    snapshot.update(snapshot_changes)
    return ledger.import_bytes(raw, source_snapshot=snapshot, file_format="json", correction_of=correction_of), raw


def series_for(ledger, values=(10, 10, 20), *, ctx=None, **changes):
    imported, raw = add(ledger, rows(values, **changes))
    import_id = imported["import"]["import_id"]
    return build_series(ledger, import_ids=[import_id], topic="coffee", signal_type=changes.get("signal_type", "TOPIC"),
                        comparison_contexts={import_id: context() if ctx is None else ctx}), imported, raw


def evaluate(ledger, values=(10, 10, 20), **changes):
    series, _, _ = series_for(ledger, values, **changes)
    return evaluate_trend(series, as_of=AS_OF)


@pytest.mark.parametrize("name", ["TrendComparisonContext", "TrendSeries", "TrendHypothesis"])
def test_component_schema_mirrors_are_portable_and_valid(name):
    definition = schema(name)
    Draft202012Validator.check_schema(definition)
    assert json.loads((COMPONENT / (name + ".v1.schema.json")).read_bytes()) == definition
    assert b"\r\n" not in (COMPONENT / (name + ".v1.schema.json")).read_bytes()


@pytest.mark.parametrize("values,expected", [
    ((10, 10, 20), "GROWING"), ((20, 20, 10), "DROPPING"),
    ((10, 11, 11), "STABLE"), ((0, 0, 0), "STABLE"), ((10, 10, 12), "STABLE"),
])
def test_transparent_direction_and_strict_threshold(ledger, values, expected):
    hypothesis = evaluate(ledger, values)
    assert hypothesis["status"] == hypothesis["direction"] == expected
    assert hypothesis["conditions"] == []
    assert hypothesis["independent_timestamp_count"] == 3
    assert len(hypothesis["supporting_signal_ids"]) == 3
    assert hypothesis["epistemic_status"] == "INFERRED"
    assert hypothesis["rule"]["version"] == "median-baseline-delta.v1"
    validate_contract("TrendHypothesis", hypothesis)


@pytest.mark.parametrize("values", [(10,), (10, 20), (None,), (0, None, 0)])
def test_sparse_history_cannot_claim_direction(ledger, values):
    hypothesis = evaluate(ledger, values)
    assert "INSUFFICIENT_HISTORY" in hypothesis["conditions"]
    assert hypothesis["direction"] is None
    assert hypothesis["comparison"]["delta"] is None


def test_empty_selection_is_honest_and_never_creates_a_ledger(ledger):
    series = build_series(ledger, import_ids=[], topic="coffee")
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["observation_count"] == 0
    assert not Path(ledger.path).exists()


def test_zero_is_observed_and_zero_baseline_has_no_invented_percentage(ledger):
    hypothesis = evaluate(ledger, (0, 0, 10))
    assert hypothesis["status"] == "GROWING"
    assert hypothesis["comparison"]["baseline_median"] == 0
    assert hypothesis["comparison"]["delta"] == 10
    assert hypothesis["comparison"]["relative_change"] is None
    assert "ZERO_BASELINE_RELATIVE_CHANGE_UNDEFINED" in hypothesis["reason_codes"]
    assert hypothesis["unavailable_signal_ids"] == []


def test_unavailable_remains_counterevidence_and_is_never_zero(ledger):
    result = evaluate(ledger, (0, None, 5))
    assert result["independent_timestamp_count"] == 2
    assert len(result["unavailable_signal_ids"]) == 1
    assert set(result["unavailable_signal_ids"]).issubset(result["counterevidence_signal_ids"])
    assert result["direction"] is None


def test_stale_is_preserved_alongside_sparse_history(ledger):
    series, _, _ = series_for(ledger, (10,))
    result = evaluate_trend(series, as_of="2026-11-29T12:00:00Z")
    assert result["status"] == "STALE"
    assert set(result["conditions"]) == {"STALE", "INSUFFICIENT_HISTORY"}
    assert result["direction"] is None


def test_stale_three_observations_never_report_current_growth(ledger):
    series, _, _ = series_for(ledger)
    result = evaluate_trend(series, as_of="2026-11-29T12:00:00Z")
    assert result["status"] == "STALE"
    assert result["comparison"]["delta"] == 10
    assert result["direction"] is None


def test_material_reversal_preserves_both_evidence_directions(ledger):
    result = evaluate(ledger, (10, 40, 10))
    assert result["status"] == "CONTRADICTORY"
    assert result["direction"] is None
    assert len(result["counterevidence_signal_ids"]) >= 2
    assert "MATERIAL_DIRECTION_REVERSALS" in result["reason_codes"]


@pytest.mark.parametrize("field,value", [
    ("unit", "views"), ("denominator", "impressions"), ("query_context", "different-query"),
    ("locale", "en-US"), ("geo", "US"), ("timezone", "UTC+00:00"), ("source_type", "OTHER_MANUAL_SOURCE"),
])
def test_signal_cohorts_must_match_exactly(ledger, field, value):
    items = rows()
    items[-1][field] = value
    imported, _ = add(ledger, items)
    import_id = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[import_id], topic="coffee", comparison_contexts={import_id: context()})
    result = evaluate_trend(series, as_of=AS_OF)
    assert series["comparison_status"] == result["status"] == "INCOMPARABLE"
    assert len(series["cohorts"]) == 2
    assert result["direction"] is None
    assert len(result["counterevidence_signal_ids"]) == 3
    assert result["comparison"]["baseline_median"] is None


@pytest.mark.parametrize("field,value", [
    ("metric", "manual_views"), ("baseline_id", "other-baseline"),
    ("normalization_id", "other-scale"), ("query_batch", "different-batch"),
])
def test_supplemental_normalization_is_part_of_exact_cohort(ledger, field, value):
    first, _ = add(ledger, rows((10, 10)))
    last, _ = add(ledger, [rows()[-1]])
    a, b = first["import"]["import_id"], last["import"]["import_id"]
    series = build_series(ledger, import_ids=[a, b], topic="coffee", comparison_contexts={a: context(), b: context(**{field: value})})
    assert evaluate_trend(series, as_of=AS_OF)["status"] == "INCOMPARABLE"


def test_accounts_are_separate_but_raw_file_refs_do_not_define_metric_identity(ledger):
    first, _ = add(ledger, rows((10, 10)), source_ref="fixture://part-1.json")
    last, _ = add(ledger, [rows()[-1]], source_ref="fixture://part-2.json")
    ids = [first["import"]["import_id"], last["import"]["import_id"]]
    series = build_series(ledger, import_ids=ids, topic="coffee", comparison_contexts={i: context() for i in ids})
    assert evaluate_trend(series, as_of=AS_OF)["status"] == "GROWING"
    other, _ = add(ledger, [rows()[-1]], source_id="different-human-source")
    ids[-1] = other["import"]["import_id"]
    series = build_series(ledger, import_ids=ids, topic="coffee", comparison_contexts={i: context() for i in ids})
    assert evaluate_trend(series, as_of=AS_OF)["status"] == "INCOMPARABLE"


def test_topic_and_creative_format_are_separate_categories(ledger):
    imported, _ = add(ledger, rows() + rows(signal_type="CREATIVE_FORMAT"))
    import_id = imported["import"]["import_id"]
    for category in ("TOPIC", "CREATIVE_FORMAT"):
        series = build_series(ledger, import_ids=[import_id], topic="coffee", signal_type=category,
                              comparison_contexts={import_id: context()})
        assert series["category"] == category
        assert len(series["points"]) == len(series["excluded_signal_ids"]) == 3
        assert evaluate_trend(series, as_of=AS_OF)["independent_timestamp_count"] == 3


def test_unknown_category_is_not_guessed(ledger):
    result = evaluate(ledger, signal_type="some-undefined-kind")
    assert result["status"] == "UNKNOWN"
    assert "UNKNOWN_CATEGORY" in result["reason_codes"]


def relative_context(**changes):
    return context(metric="relative_search_interest", metric_kind="RELATIVE_INDEX",
                   normalization_id="human-described-scale.v1", query_batch="coffee-and-tea:batch-1",
                   relative_scale=[0, 100], **changes)


def test_google_trends_is_relative_not_absolute_search_counts(ledger):
    result = evaluate(ledger, ctx=relative_context(), unit="index", source_type="GOOGLE_TRENDS")
    assert result["status"] == "GROWING"
    result = evaluate(ledger, unit="index", source_type="GOOGLE_TRENDS")
    assert result["status"] == "UNKNOWN"
    assert "RELATIVE_INDEX_IS_NOT_ABSOLUTE_COUNT_OR_RATE" in result["reason_codes"]


@pytest.mark.parametrize("field,value", [("relative_scale", None), ("query_batch", None), ("normalization_id", None), ("baseline_id", None)])
def test_missing_relative_context_prevents_direction(ledger, field, value):
    ctx = relative_context()
    ctx[field] = value
    assert evaluate(ledger, ctx=ctx, unit="index")["status"] == "UNKNOWN"


@pytest.mark.parametrize("field,value", [("relative_scale", [0, 1000]), ("query_batch", "another-query-batch")])
def test_relative_scores_from_different_query_scales_cannot_mix(ledger, field, value):
    first, _ = add(ledger, rows((10, 10), unit="index"))
    last, _ = add(ledger, [rows(unit="index")[-1]])
    a, b = first["import"]["import_id"], last["import"]["import_id"]
    changed = relative_context()
    changed[field] = value
    series = build_series(ledger, import_ids=[a, b], topic="coffee", comparison_contexts={a: relative_context(), b: changed})
    assert evaluate_trend(series, as_of=AS_OF)["status"] == "INCOMPARABLE"


def test_rate_requires_denominator_and_preserves_zero(ledger):
    ctx = context(metric="manual_rate", metric_kind="RATE", normalization_id="per-exposure")
    assert evaluate(ledger, (0, 0, 1), ctx=ctx, unit="rate", denominator="exposures")["status"] == "GROWING"
    missing = evaluate(ledger, ctx=ctx, unit="rate")
    assert missing["status"] == "UNKNOWN"
    assert "MISSING_RATE_DENOMINATOR" in missing["reason_codes"]


def test_absent_comparison_metadata_is_unknown_instead_of_invented(ledger):
    imported, _ = add(ledger)
    series = build_series(ledger, import_ids=[imported["import"]["import_id"]], topic="coffee")
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["status"] == "UNKNOWN"
    assert "MISSING_COMPARISON_CONTEXT" in result["reason_codes"]


def test_reimported_raw_bytes_and_duplicate_timestamps_never_inflate_history(ledger):
    first, _ = add(ledger)
    duplicate, _ = add(ledger, bom=True)
    ids = [first["import"]["import_id"], duplicate["import"]["import_id"]]
    series = build_series(ledger, import_ids=ids + ids, topic="coffee", comparison_contexts={i: context() for i in ids})
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["observation_count"] == 6
    assert result["independent_timestamp_count"] == 3
    assert len(result["unused_signal_ids"]) == 3
    one_time = rows()
    for item in one_time:
        item["observed_at"] = AS_OF
        item["value"] = 10
    imported, _ = add(ledger, one_time)
    i = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context()})
    assert evaluate_trend(series, as_of=AS_OF)["status"] == "INSUFFICIENT_HISTORY"


def test_corrections_preserve_conflicting_observations_and_lineage(ledger):
    original, _ = add(ledger)
    corrected_rows = rows()
    corrected_rows[-1]["value"] = 5
    corrected, _ = add(ledger, corrected_rows, correction_of=original["import"]["import_id"])
    ids = [original["import"]["import_id"], corrected["import"]["import_id"]]
    series = build_series(ledger, import_ids=ids, topic="coffee", comparison_contexts={i: context() for i in ids})
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["status"] == "CONTRADICTORY"
    assert "CONFLICTING_SAME_TIMESTAMP" in result["reason_codes"]
    assert any(r["lineage"]["relationship"] == "CORRECTION" for r in series["imports"])
    assert len(result["counterevidence_signal_ids"]) == 2


def interval_rows(hours=24):
    result = rows()
    for item in result:
        end = datetime.fromisoformat(item["observed_at"])
        item["observation_window"] = [(end - timedelta(hours=hours)).isoformat(), end.isoformat()]
    return result


@pytest.mark.parametrize("hours,expected,count", [(24, "GROWING", 3), (48, "INSUFFICIENT_HISTORY", 2)])
def test_window_independence_and_comparability(ledger, hours, expected, count):
    imported, _ = add(ledger, interval_rows(hours))
    i = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context(window_kind="INTERVAL")})
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["status"] == expected
    assert result["independent_timestamp_count"] == count


def test_different_window_lengths_are_incomparable(ledger):
    items = interval_rows()
    items[-1]["observation_window"][0] = "2026-10-27T00:00:00Z"
    imported, _ = add(ledger, items)
    i = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context(window_kind="INTERVAL")})
    assert evaluate_trend(series, as_of=AS_OF)["status"] == "INCOMPARABLE"


def test_utc_equivalent_timestamps_are_one_observation(ledger):
    items = rows((10, 10, 10))
    for item, instant in zip(items, ["2026-10-29T00:00:00Z", "2026-10-29T07:00:00+07:00", "2026-10-28T19:00:00-05:00"]):
        item["observed_at"] = instant
    imported, _ = add(ledger, items)
    i = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context()})
    assert evaluate_trend(series, as_of=AS_OF)["independent_timestamp_count"] == 1


@pytest.mark.parametrize("clock", ["2026-10-29T12:00:00", "not-a-clock", None, datetime(2026, 10, 29)])
def test_clock_must_be_explicit_and_timezone_aware(ledger, clock):
    series, _, _ = series_for(ledger)
    with pytest.raises(SignalValidationError, match="INVALID_TREND_CLOCK"):
        evaluate_trend(series, as_of=clock)


def test_future_collection_prevents_historical_lookahead(ledger):
    series, _, _ = series_for(ledger)
    result = evaluate_trend(series, as_of="2026-10-28T00:00:00Z")
    assert result["status"] == "UNKNOWN"
    assert "FUTURE_EVIDENCE" in result["reason_codes"]


@pytest.mark.parametrize("settings", [
    {"min_observations": 2}, {"baseline_points": 1}, {"relative_delta": -1},
    {"relative_delta": float("inf")}, {"absolute_delta": float("nan")},
    {"max_age_seconds": -1}, {"version": "unregistered-rule.v99"},
])
def test_invalid_rule_cannot_downgrade_minimum_or_hide_nonfinite_values(ledger, settings):
    series, _, _ = series_for(ledger)
    with pytest.raises(SignalValidationError, match="INVALID_TREND_RULE"):
        evaluate_trend(series, as_of=AS_OF, rule=TrendRule(**settings))


def test_configurable_baseline_and_deltas_are_recorded(ledger):
    series, _, _ = series_for(ledger)
    result = evaluate_trend(series, as_of=AS_OF, rule=TrendRule(absolute_delta=15, baseline_points=2))
    assert result["status"] == "STABLE"
    assert result["comparison"]["threshold"] == 15
    assert result["rule"]["absolute_delta"] == 15
    stricter = evaluate_trend(series, as_of=AS_OF, rule=TrendRule(min_observations=4))
    assert stricter["status"] == "INSUFFICIENT_HISTORY"


def test_serialized_series_integrity_and_determinism(ledger):
    series, imported, raw = series_for(ledger)
    before = deepcopy(series)
    first = evaluate_trend(series, as_of=AS_OF)
    second = evaluate_trend(json.loads(canonical_bytes(series)), as_of="2026-10-29T19:00:00+07:00")
    assert first == second
    assert series == before
    assert series["imports"][0] == imported["import"]
    assert series["imports"][0]["raw_sha256"] == sha256_bytes(raw)
    assert all(p["signal"]["rights_status"] == "UNKNOWN" for p in series["points"])
    tampered = deepcopy(series)
    tampered["points"][0]["signal"]["value"] = 999
    with pytest.raises(SignalValidationError, match="TREND_SERIES_ID_MISMATCH"):
        validate_series(tampered)
    tampered["series_id"] = "series-" + digest({k: v for k, v in tampered.items() if k != "series_id"})
    with pytest.raises(SignalValidationError, match="SERIES_SIGNAL_ID_MISMATCH"):
        validate_series(tampered)


def test_raw_tamper_fails_through_actual_m01_replay(ledger):
    imported, _ = add(ledger)
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TRIGGER raw_evidence_no_update")
        db.execute("UPDATE raw_evidence SET payload=zeroblob(size_bytes)")
    i = imported["import"]["import_id"]
    with pytest.raises(SignalLedgerError, match="RAW_EVIDENCE_TAMPERED"):
        build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context()})


def test_declared_hash_mismatch_remains_unknown_without_rewriting_m01(ledger):
    imported, _ = add(ledger, sha256="0" * 64)
    i = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context()})
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["status"] == "UNKNOWN"
    assert "DECLARED_RAW_HASH_MISMATCH" in result["reason_codes"]
    assert series["imports"][0]["source_snapshot"]["sha256"] == "0" * 64


def test_offline_read_only_and_no_unverified_query_shortcuts(ledger, monkeypatch):
    imported, raw = add(ledger, bom=True)
    before = Path(ledger.path).read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected external effect or unverified shortcut")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(ledger, "query_signals", forbidden)
    monkeypatch.setattr(ledger, "list_imports", forbidden)
    i = imported["import"]["import_id"]
    supplied = {i: context()}
    original = deepcopy(supplied)
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts=supplied)
    assert evaluate_trend(series, as_of=AS_OF)["status"] == "GROWING"
    assert Path(ledger.path).read_bytes() == before
    assert supplied == original
    with sqlite3.connect(ledger.path) as db:
        assert bytes(db.execute("SELECT payload FROM raw_evidence").fetchone()[0]) == raw


def test_inputs_are_bounded_and_contexts_bound_to_actual_imports(ledger):
    with pytest.raises(SignalValidationError, match="TOO_MANY_TREND_IMPORTS"):
        build_series(ledger, import_ids=("a10-" + "0" * 64 for _ in range(1000)), topic="coffee")
    for invalid in (None, "a10-" + "0" * 64):
        with pytest.raises(SignalValidationError, match="IMPORT_IDS_ARRAY_REQUIRED"):
            build_series(ledger, import_ids=invalid, topic="coffee")
    with pytest.raises(SignalValidationError, match="UNBOUND_COMPARISON_CONTEXT"):
        build_series(ledger, import_ids=[], topic="coffee", comparison_contexts={"unrelated": context()})


def test_large_numbers_never_create_nonfinite_hypotheses(ledger):
    result = evaluate(ledger, (10 ** 400, 10 ** 400, 2 * 10 ** 400))
    assert result["status"] == "UNKNOWN"
    assert "DERIVED_NUMBER_OUT_OF_RANGE" in result["reason_codes"]
    assert result["direction"] is None
    canonical_bytes(result)


def test_historical_windows_do_not_become_fresh_by_recent_collection(ledger):
    items = interval_rows()
    for item in items:
        item["observation_window"] = [(datetime.fromisoformat(t) - timedelta(days=30)).isoformat()
                                      for t in item["observation_window"]]
    imported, _ = add(ledger, items)
    i = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context(window_kind="INTERVAL")})
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["status"] == "STALE"
    assert len(result["counterevidence_signal_ids"]) == 3


def test_relative_context_cannot_relabel_absolute_search_count_unit(ledger):
    result = evaluate(ledger, ctx=relative_context(), unit="search_count", source_type="GOOGLE_TRENDS")
    assert result["status"] == "UNKNOWN"
    assert "RELATIVE_INDEX_UNIT_MISMATCH" in result["reason_codes"]


@pytest.mark.parametrize("field,value", [("metric_kind", "RATE"), ("window_kind", "UNKNOWN")])
def test_different_metric_and_window_interpretations_remain_separate(ledger, field, value):
    first, _ = add(ledger, rows((10, 10)))
    last, _ = add(ledger, [rows()[-1]])
    a, b = first["import"]["import_id"], last["import"]["import_id"]
    series = build_series(ledger, import_ids=[a, b], topic="coffee", comparison_contexts={a: context(), b: context(**{field: value})})
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["status"] == "INCOMPARABLE"
    assert result["direction"] is None


def test_hypothesis_schema_rejects_direction_when_any_blocker_is_present(ledger):
    result = evaluate(ledger)
    result["conditions"] = ["STALE"]
    with pytest.raises(SignalValidationError, match="INVALID_TRENDHYPOTHESIS"):
        validate_contract("TrendHypothesis", result)
    result["status"] = "STALE"
    result["direction"] = None
    validate_contract("TrendHypothesis", result)


def test_median_baseline_is_explicit_and_robust_to_one_prior_outlier(ledger):
    series, _, _ = series_for(ledger, (10, 100, 10, 20))
    result = evaluate_trend(series, as_of="2026-10-30T12:00:00Z")
    assert result["comparison"]["baseline_median"] == 10
    assert result["status"] == "CONTRADICTORY"
    # The outlier does not manufacture a calibrated confidence or causal claim.
    assert result["direction"] is None


@pytest.mark.parametrize("field,value", [("baseline_id", " "), ("relative_scale", [100, 0]), ("evidence_refs", [])])
def test_malformed_comparison_metadata_is_rejected(ledger, field, value):
    imported, _ = add(ledger)
    i = imported["import"]["import_id"]
    with pytest.raises(SignalValidationError):
        build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context(**{field: value})})


def test_mismatched_window_declaration_is_unknown(ledger):
    imported, _ = add(ledger, interval_rows())
    i = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context()})
    result = evaluate_trend(series, as_of=AS_OF)
    assert result["status"] == "UNKNOWN"
    assert "WINDOW_KIND_MISMATCH" in result["reason_codes"]


def test_overlap_exclusion_does_not_hide_fresh_measurement(ledger):
    imported, _ = add(ledger, interval_rows(hours=72))
    i = imported["import"]["import_id"]
    series = build_series(ledger, import_ids=[i], topic="coffee", comparison_contexts={i: context(window_kind="INTERVAL")})
    result = evaluate_trend(series, as_of="2026-11-04T12:00:00Z")
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["independent_timestamp_count"] == 1
    assert "STALE" not in result["conditions"]
    assert "OVERLAPPING_OR_REPLAYED_OBSERVATION" in result["reason_codes"]
