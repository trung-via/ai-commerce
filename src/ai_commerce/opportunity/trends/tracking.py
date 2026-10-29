"""Offline comparable series and transparent, advisory direction hypotheses.

Only SignalLedger.get() supplies observations. Supplemental metric/normalization
metadata is explicitly caller supplied, bound to an import, and never fills in or
changes an M01 observation. No wall clock, network, database writes or providers.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from itertools import islice
from statistics import median
from typing import Iterable, Mapping

from ai_commerce.opportunity.core.models import digest, require, validate_signal
from ai_commerce.opportunity.signals import SignalLedger

from .schemas import BLOCKERS, MAX_IMPORTS, MAX_POINTS, validate_contract

LIMITATIONS = [
    "User-supplied observations and comparison metadata; no live provider verification or rights grant.",
    "Direction is a descriptive hypothesis, not validated demand, viral probability or causal lift.",
    "Relative indices retain their query batch and scale; they are not absolute search counts.",
    "Independence means distinct timestamps with non-overlapping supplied windows; external statistical independence is unknown.",
]


def _instant(value: str | datetime) -> datetime:
    if isinstance(value, str):
        # Use the application's RFC3339 checker, including on Windows.
        from ai_commerce.contracts.schema import _valid_datetime
        require(_valid_datetime(value), "INVALID_TREND_CLOCK")
        value = datetime.fromisoformat(value.replace("t", "T").replace("z", "Z").replace("Z", "+00:00"))
    require(isinstance(value, datetime) and value.utcoffset() is not None, "INVALID_TREND_CLOCK")
    return value.astimezone(timezone.utc)


def _microseconds(delta: timedelta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _category(signal_type: str) -> str:
    return signal_type if signal_type in ("TOPIC", "CREATIVE_FORMAT") else "UNKNOWN"


def _cohort(signal: dict, record: dict, context: dict | None) -> tuple[dict, list[str]]:
    unknown = []
    ctx = context or {}
    kind = ctx.get("metric_kind", "UNKNOWN")
    window = signal.get("observation_window")
    observed = _instant(signal["observed_at"])
    offsets = [_microseconds(_instant(t) - observed) for t in window] if window else None
    if not context:
        unknown.append("MISSING_COMPARISON_CONTEXT")
    if _category(signal["signal_type"]) == "UNKNOWN":
        unknown.append("UNKNOWN_CATEGORY")
    if kind == "UNKNOWN":
        unknown.append("UNKNOWN_METRIC_KIND")
    for field in ("baseline_id", "normalization_id"):
        if not ctx.get(field):
            unknown.append("MISSING_" + field.upper())
    if kind == "RATE" and not signal.get("denominator"):
        unknown.append("MISSING_RATE_DENOMINATOR")
    if kind == "RELATIVE_INDEX":
        if signal["unit"].casefold() in ("count", "absolute_count", "search_count", "searches"):
            unknown.append("RELATIVE_INDEX_UNIT_MISMATCH")
        if ctx.get("relative_scale") is None or not ctx.get("query_batch"):
            unknown.append("MISSING_RELATIVE_NORMALIZATION")
        elif signal["value"] is not None and not ctx["relative_scale"][0] <= signal["value"] <= ctx["relative_scale"][1]:
            unknown.append("VALUE_OUTSIDE_RELATIVE_SCALE")
    elif signal["unit"].casefold() == "index" or signal["source_type"] == "GOOGLE_TRENDS" or ctx.get("relative_scale") is not None:
        unknown.append("RELATIVE_INDEX_IS_NOT_ABSOLUTE_COUNT_OR_RATE")
    if signal["value"] is not None and signal["value"] < 0:
        unknown.append("NEGATIVE_COUNT_RATE_OR_INDEX")
    if ctx.get("window_kind", "UNKNOWN") == "UNKNOWN":
        unknown.append("UNKNOWN_WINDOW_KIND")
    elif (ctx["window_kind"] == "POINT") != (window is None):
        unknown.append("WINDOW_KIND_MISMATCH")
    if offsets is not None and offsets[0] >= offsets[1]:
        unknown.append("EMPTY_OBSERVATION_WINDOW")
    if record["source_snapshot"]["sha256"] != record["raw_sha256"]:
        unknown.append("DECLARED_RAW_HASH_MISMATCH")
    key = {
        "topic": signal["topic"], "category": _category(signal["signal_type"]), "signal_type": signal["signal_type"],
        "source_id": record["source_snapshot"]["source_id"], "source_kind": record["source_snapshot"]["source_kind"],
        "source_type": signal["source_type"], "scope_sha256": record["scope_sha256"],
        "mapping_sha256": digest({k: record[k] for k in ("mapping", "format", "dialect")}),
        "metric": ctx.get("metric"), "metric_kind": kind, "unit": signal["unit"],
        "denominator": signal.get("denominator"), "query_context": signal["query_context"],
        "baseline_id": ctx.get("baseline_id"), "normalization_id": ctx.get("normalization_id"),
        "query_batch": ctx.get("query_batch"), "relative_scale": ctx.get("relative_scale"),
        "geo": signal["geo"], "locale": signal["locale"], "timezone": signal["timezone"],
        "window_kind": ctx.get("window_kind", "UNKNOWN"), "window_offsets_microseconds": offsets,
    }
    return key, sorted(set(unknown))


def _context(value: dict) -> dict:
    value = deepcopy(value)
    validate_contract("TrendComparisonContext", value)
    require(all(not isinstance(v, str) or bool(v.strip()) for v in value.values()), "EMPTY_COMPARISON_FIELD")
    scale = value["relative_scale"]
    require(scale is None or scale[0] < scale[1], "INVALID_RELATIVE_SCALE")
    return value


def build_series(
    ledger: SignalLedger, *, import_ids: Iterable[str], topic: str, signal_type: str = "TOPIC",
    comparison_contexts: Mapping[str, dict] | None = None,
) -> dict:
    """Replay explicit imports; never use M01's unverified query/list shortcuts.

    comparison_contexts is keyed by import ID. A caller must explicitly describe
    the selected metric, baseline, scale/query batch and window interpretation.
    Different cohorts remain visible and cannot produce one arithmetic direction.
    """
    require(isinstance(topic, str) and 0 < len(topic.strip()) <= 256, "INVALID_TREND_TOPIC")
    require(isinstance(signal_type, str) and 0 < len(signal_type.strip()) <= 64, "INVALID_TREND_SIGNAL_TYPE")
    require(isinstance(import_ids, Iterable) and not isinstance(import_ids, (str, bytes)), "IMPORT_IDS_ARRAY_REQUIRED")
    selected = list(islice(iter(import_ids), MAX_IMPORTS + 1))
    require(len(selected) <= MAX_IMPORTS, "TOO_MANY_TREND_IMPORTS")
    from ai_commerce.opportunity.signals.importer import IMPORT_ID_RE
    require(all(isinstance(i, str) and IMPORT_ID_RE.fullmatch(i) for i in selected), "INVALID_TREND_IMPORT_ID")
    selected = sorted(set(selected))
    contexts = {} if comparison_contexts is None else comparison_contexts
    require(isinstance(contexts, Mapping) and set(contexts).issubset(selected), "UNBOUND_COMPARISON_CONTEXT")
    contexts = {i: _context(c) for i, c in contexts.items()}
    imports, points, excluded, cohorts = [], [], [], {}
    total = 0
    for import_id in selected:
        replay = ledger.get(import_id)  # verifies exact raw bytes, parser, lineage and signal IDs
        record = deepcopy(replay["import"])
        imports.append(record)
        total += len(replay["signals"])
        require(total <= MAX_POINTS, "TOO_MANY_TREND_POINTS")
        for original in replay["signals"]:
            signal = deepcopy(original)
            if signal["topic"] != topic or signal["signal_type"] != signal_type:
                excluded.append(signal["signal_id"])
                continue
            context = deepcopy(contexts.get(import_id))
            key, unknown = _cohort(signal, record, context)
            cohort_id = "cohort-" + digest(key)
            cohort = cohorts.setdefault(cohort_id, {"cohort_id": cohort_id, "key": key, "signal_ids": []})
            cohort["signal_ids"].append(signal["signal_id"])
            points.append({"signal": signal, "comparison_context": context,
                           "comparison_sha256": digest(context) if context is not None else None,
                           "unknown_reasons": unknown})
    points.sort(key=lambda p: (_instant(p["signal"]["observed_at"]), p["signal"]["signal_id"]))
    for cohort in cohorts.values():
        cohort["signal_ids"].sort()
    status = "INCOMPARABLE" if len(cohorts) > 1 else (
        "UNKNOWN" if not points or any(p["unknown_reasons"] for p in points) else "COMPARABLE")
    result = {
        "schema_version": "TrendSeries.v1", "topic": topic, "signal_type": signal_type,
        "category": _category(signal_type), "comparison_status": status,
        "replay_status": "M01_REPLAY_VERIFIED_AT_BUILD", "source_status": imports[0]["source_status"] if imports else "USER_SUPPLIED_NOT_LIVE_VERIFIED",
        "imports": imports, "points": points, "cohorts": [cohorts[k] for k in sorted(cohorts)],
        "excluded_signal_ids": sorted(excluded), "limitations": list(LIMITATIONS),
    }
    result["series_id"] = "series-" + digest(result)
    return validate_series(result)


def validate_series(value: dict) -> dict:
    validate_contract("TrendSeries", value)
    require(value["series_id"] == "series-" + digest({k: v for k, v in value.items() if k != "series_id"}), "TREND_SERIES_ID_MISMATCH")
    imports = {r["import_id"]: r for r in value["imports"]}
    require(len(imports) == len(value["imports"]), "DUPLICATE_SERIES_IMPORT")
    expected = {}
    point_ids = set()
    for point in value["points"]:
        signal, context = point["signal"], point["comparison_context"]
        validate_signal(signal)
        require(signal["topic"] == value["topic"] and signal["signal_type"] == value["signal_type"], "SERIES_SELECTION_MISMATCH")
        require(signal["import_id"] in imports and signal["signal_id"] not in point_ids, "INVALID_SERIES_SIGNAL_REFERENCE")
        point_ids.add(signal["signal_id"])
        require(signal["signal_id"] == "sig-" + digest({
            "import_id": signal["import_id"], "ordinal": signal["ordinal"],
            "signal": {k: v for k, v in signal.items() if k != "signal_id"},
        }), "SERIES_SIGNAL_ID_MISMATCH")
        require(point["comparison_sha256"] == (digest(context) if context is not None else None), "COMPARISON_HASH_MISMATCH")
        if context is not None:
            _context(context)
        key, unknown = _cohort(signal, imports[signal["import_id"]], context)
        require(point["unknown_reasons"] == unknown, "UNKNOWN_REASONS_MISMATCH")
        cohort_id = "cohort-" + digest(key)
        cohort = expected.setdefault(cohort_id, {"cohort_id": cohort_id, "key": key, "signal_ids": []})
        cohort["signal_ids"].append(signal["signal_id"])
    for cohort in expected.values():
        cohort["signal_ids"].sort()
    require(value["cohorts"] == [expected[k] for k in sorted(expected)], "COHORT_IDENTITY_MISMATCH")
    expected_status = "INCOMPARABLE" if len(expected) > 1 else (
        "UNKNOWN" if not point_ids or any(p["unknown_reasons"] for p in value["points"]) else "COMPARABLE")
    require(value["comparison_status"] == expected_status and value["category"] == _category(value["signal_type"]), "SERIES_STATUS_MISMATCH")
    return value


@dataclass(frozen=True)
class TrendRule:
    """Median of prior independent values, latest delta, strict threshold >.

    baseline_points=None uses all prior values; otherwise the latest N prior
    independent observations. Any material adjacent reversals block direction.
    """
    version: str = "median-baseline-delta.v1"
    min_observations: int = 3
    baseline_points: int | None = None
    relative_delta: float = 0.20
    absolute_delta: float = 0.0
    max_age_seconds: int = 7 * 86400

    def record(self) -> dict:
        value = asdict(self)
        from .schemas import RULE
        from jsonschema import Draft202012Validator
        require(Draft202012Validator(RULE).is_valid(value), "INVALID_TREND_RULE")
        # Reject NaN/Infinity, including Python's permissive JSON-schema numbers.
        try:
            digest(value)
        except (ValueError, TypeError, OverflowError):
            require(False, "INVALID_TREND_RULE")
        return value


def evaluate_trend(series: dict, *, as_of: str | datetime, rule: TrendRule | None = None) -> dict:
    """Evaluate a saved series at a mandatory explicit, timezone-aware clock.

    All blockers are preserved in conditions; status uses BLOCKERS precedence.
    No arithmetic combines cohorts, and no direction survives a blocker.
    """
    validate_series(series)
    clock = _instant(as_of)
    policy = (rule or TrendRule()).record()
    conditions, reasons, counter, unused = set(), set(), set(), set()
    if series["comparison_status"] == "INCOMPARABLE":
        conditions.add("INCOMPARABLE")
        reasons.add("MULTIPLE_NORMALIZATION_COHORTS")
    signals = [p["signal"] for p in series["points"]]
    if "INCOMPARABLE" in conditions:
        counter.update(s["signal_id"] for s in signals)
    unavailable = {s["signal_id"] for s in signals if s["value"] is None}
    counter.update(unavailable)
    for point in series["points"]:
        if point["unknown_reasons"]:
            conditions.add("UNKNOWN")
            reasons.update(point["unknown_reasons"])
            counter.add(point["signal"]["signal_id"])
    if signals and len(unavailable) == len(signals):
        conditions.add("UNKNOWN")
        reasons.add("NO_AVAILABLE_VALUES")
    by_time = {}
    for signal in signals:
        end = _instant(signal["observation_window"][1]) if signal.get("observation_window") else _instant(signal["observed_at"])
        if max(_instant(signal["observed_at"]), _instant(signal["collected_at"]), end) > clock:
            conditions.add("UNKNOWN")
            reasons.add("FUTURE_EVIDENCE")
            counter.add(signal["signal_id"])
            continue
        if signal["value"] is not None and "INCOMPARABLE" not in conditions:
            by_time.setdefault(_instant(signal["observed_at"]), []).append(signal)
    independent, last_end, raw_rows = [], None, set()
    for when, group in sorted(by_time.items()):
        group.sort(key=lambda s: s["signal_id"])
        if len({Decimal(str(s["value"])) for s in group}) > 1:
            conditions.add("CONTRADICTORY")
            reasons.add("CONFLICTING_SAME_TIMESTAMP")
            counter.update(s["signal_id"] for s in group)
            continue
        signal = group[0]
        # Repeated imports at one timestamp are one observation; never a sum.
        unused.update(s["signal_id"] for s in group[1:])
        window = signal.get("observation_window")
        start, end = (_instant(window[0]), _instant(window[1])) if window else (when, when)
        record = next(r for r in series["imports"] if r["import_id"] == signal["import_id"])
        raw_row = (record["raw_sha256"], signal["ordinal"])
        if (last_end is not None and start < last_end) or raw_row in raw_rows:
            unused.add(signal["signal_id"])
            reasons.add("OVERLAPPING_OR_REPLAYED_OBSERVATION")
            continue
        independent.append(signal)
        raw_rows.add(raw_row)
        last_end = end
    needed = max(policy["min_observations"], (policy["baseline_points"] or 2) + 1)
    if len(independent) < needed:
        conditions.add("INSUFFICIENT_HISTORY")
        reasons.add("TOO_FEW_INDEPENDENT_TIMESTAMPS")
    def measurement_time(signal):
        window = signal.get("observation_window")
        return _instant(window[1] if window else signal["observed_at"])

    # Freshness describes the latest available measurement, including overlapping
    # samples excluded only from the independent history count.
    available = [s for group in by_time.values() for s in group]
    freshest = max(available, key=measurement_time) if available else None
    if freshest and (clock - measurement_time(freshest)).total_seconds() > policy["max_age_seconds"]:
        conditions.add("STALE")
        reasons.add("LATEST_AVAILABLE_OBSERVATION_STALE")
        counter.update(s["signal_id"] for s in available
                       if (clock - measurement_time(s)).total_seconds() > policy["max_age_seconds"])
    comparison = dict.fromkeys(("baseline_median", "latest_value", "delta", "relative_change", "threshold"))
    support, direction = set(), None
    if len(independent) >= needed:
        baseline = independent[:-1]
        if policy["baseline_points"] is not None:
            baseline = baseline[-policy["baseline_points"]:]
        with localcontext() as arithmetic:
            arithmetic.prec = 400  # deterministic finite arithmetic for accepted JSON numbers
            baseline_value = median([Decimal(str(s["value"])) for s in baseline])
            latest = Decimal(str(independent[-1]["value"]))
            relative, absolute = Decimal(str(policy["relative_delta"])), Decimal(str(policy["absolute_delta"]))
            threshold = max(absolute, abs(baseline_value) * relative)
            delta = latest - baseline_value
            direction = "GROWING" if delta > threshold else "DROPPING" if delta < -threshold else "STABLE"
            changes = set()
            for left, right in zip(independent, independent[1:]):
                before, after = Decimal(str(left["value"])), Decimal(str(right["value"]))
                step = after - before
                step_threshold = max(absolute, abs(before) * relative)
                if abs(step) > step_threshold:
                    sign = 1 if step > 0 else -1
                    changes.add(sign)
                    if (direction == "GROWING" and sign < 0) or (direction == "DROPPING" and sign > 0) or direction == "STABLE":
                        counter.update((left["signal_id"], right["signal_id"]))
            if len(changes) > 1:
                conditions.add("CONTRADICTORY")
                reasons.add("MATERIAL_DIRECTION_REVERSALS")
            numbers = [baseline_value, latest, delta, delta / abs(baseline_value) if baseline_value else None, threshold]
            # An overflowed output is UNKNOWN, never an Infinity/NaN metric.
            import math
            converted = [float(n) if n is not None else None for n in numbers]
            if all(n is None or math.isfinite(n) for n in converted):
                comparison = dict(zip(comparison, converted))
            else:
                conditions.add("UNKNOWN")
                reasons.add("DERIVED_NUMBER_OUT_OF_RANGE")
            if not baseline_value:
                reasons.add("ZERO_BASELINE_RELATIVE_CHANGE_UNDEFINED")
        support.update(s["signal_id"] for s in baseline + independent[-1:])
    status = next((c for c in BLOCKERS if c in conditions), direction or "INSUFFICIENT_HISTORY")
    result = {
        "schema_version": "TrendHypothesis.v1", "series_id": series["series_id"],
        "as_of": clock.isoformat().replace("+00:00", "Z"), "status": status,
        "conditions": [c for c in BLOCKERS if c in conditions], "direction": None if conditions else direction,
        "epistemic_status": "INFERRED", "source_status": series["source_status"], "rule": policy,
        "observation_count": len(signals), "independent_timestamp_count": len(independent),
        "supporting_signal_ids": sorted(support), "counterevidence_signal_ids": sorted(counter),
        "unavailable_signal_ids": sorted(unavailable), "unused_signal_ids": sorted(unused),
        "reason_codes": sorted(reasons), "comparison": comparison, "limitations": list(LIMITATIONS),
    }
    result["hypothesis_id"] = "hyp-" + digest(result)
    return validate_contract("TrendHypothesis", result)
