"""A11-02 L03 descriptive performance reporting and comparison.

Source-backed performance reports and pairwise comparisons with strict
metric definition, provider, denominator, unit, cohort, timezone, and
measurement window compatibility.

Enforces explicit typed states:
  AVAILABLE (including numeric zero)
  NOT_AVAILABLE (null data / missing)
  INSUFFICIENT_DATA (undefined arithmetic / sample shortfall)
  NOT_COMPARABLE (incompatible contexts or policies)

No invented lift, no causal attribution, and no cross-provider auto-matching.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from ai_commerce.analytics.model import (
    digest as a09_digest,
    metric_identity,
    parse_json,
    validate_target,
    validate_timezone,
)
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.contracts.schema import _valid_datetime
from ai_commerce.core.provenance import canonical_bytes

from .core import (
    LearningError,
    content_id,
    digest,
    parse_contract,
    require,
    seal_contract,
    validate_contract,
)
from .reader import ObservationSnapshot
from .schemas import EVIDENCE_STATES, MAX_OBSERVATIONS

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

INCOMPATIBLE_CODES = {
    "SAME_REPORT",
    "CROSS_PLATFORM_INCOMPATIBLE",
    "SURFACE_MISMATCH",
    "ACCOUNT_MISMATCH",
    "METRIC_KEY_MISMATCH",
    "PROVIDER_MISMATCH",
    "METRIC_VERSION_MISMATCH",
    "UNIT_MISMATCH",
    "DENOMINATOR_MISMATCH",
    "COHORT_MISMATCH",
    "TIMEZONE_MISMATCH",
    "WINDOW_DURATION_MISMATCH",
    "INVERTED_WINDOW_ORDER",
    "STALE_DATA",
}


def _parse_instant(value: str) -> datetime:
    require(type(value) is str and _valid_datetime(value), "INVALID_DATETIME_FORMAT")
    dt = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    require(dt.utcoffset() is not None, "TIMEZONE_OFFSET_REQUIRED")
    return dt


def _windows_overlap(w1: tuple[datetime, datetime], w2: tuple[datetime, datetime]) -> bool:
    return max(w1[0], w2[0]) < min(w1[1], w2[1])


def _check_key(code: str) -> None:
    require(type(code) is str and bool(_KEY_PATTERN.fullmatch(code)), "INVALID_KEY_CODE")


@dataclass(frozen=True, slots=True, init=False)
class PerformanceReport:
    """Immutable, validated descriptive performance report."""

    _raw: bytes

    def __init__(self, value: dict):
        require(type(value) is dict, "JSON_OBJECT_REQUIRED")
        if "report_id" not in value:
            value = seal_contract("PerformanceReport", value)
        checked = validate_contract("PerformanceReport", value)
        object.__setattr__(self, "_raw", canonical_bytes(checked))

    @classmethod
    def from_bytes(cls, raw: bytes) -> PerformanceReport:
        checked = parse_contract("PerformanceReport", raw)
        result = object.__new__(cls)
        object.__setattr__(result, "_raw", canonical_bytes(checked))
        return result

    @classmethod
    def from_dict(cls, value: dict) -> PerformanceReport:
        return cls(value)

    @classmethod
    def build(cls, *args, **kwargs) -> PerformanceReport:
        return build_performance_report(*args, **kwargs)

    def to_dict(self) -> dict:
        return parse_json(self._raw)

    def to_bytes(self) -> bytes:
        return self._raw

    @property
    def report_id(self) -> str:
        return self.to_dict()["report_id"]

    @property
    def snapshot_refs(self) -> list[dict]:
        return self.to_dict()["snapshot_refs"]

    @property
    def evidence_refs(self) -> list[dict]:
        return self.to_dict()["evidence_refs"]

    @property
    def platform(self) -> str:
        return self.to_dict()["platform"]

    @property
    def surface(self) -> str:
        return self.to_dict()["surface"]

    @property
    def account_ref(self) -> dict:
        return self.to_dict()["account_ref"]

    @property
    def measurement_window(self) -> list[str]:
        return self.to_dict()["measurement_window"]

    @property
    def timezone(self) -> str:
        return self.to_dict()["timezone"]

    @property
    def cohort(self) -> str | None:
        return self.to_dict()["cohort"]

    @property
    def metric(self) -> dict:
        return self.to_dict()["metric"]

    @property
    def measurement(self) -> dict:
        return self.to_dict()["measurement"]

    @property
    def measurement_status(self) -> str:
        return self.to_dict()["measurement"]["status"]

    @property
    def measurement_value(self) -> float | int | None:
        return self.to_dict()["measurement"]["value"]

    @property
    def available_count(self) -> int:
        return self.to_dict()["available_count"]

    @property
    def not_available_count(self) -> int:
        return self.to_dict()["not_available_count"]

    @property
    def confounders(self) -> list[dict]:
        return self.to_dict()["confounders"]

    @property
    def caveats(self) -> list[str]:
        return self.to_dict()["caveats"]

    @property
    def as_of(self) -> str | None:
        return self.to_dict().get("as_of")

    def report_ref(self) -> dict:
        return {"report_id": self.report_id}


@dataclass(frozen=True, slots=True, init=False)
class ComparisonResult:
    """Immutable, validated descriptive pairwise comparison."""

    _raw: bytes

    def __init__(self, value: dict):
        require(type(value) is dict, "JSON_OBJECT_REQUIRED")
        if "comparison_id" not in value:
            value = seal_contract("ComparisonResult", value)
        checked = validate_contract("ComparisonResult", value)
        object.__setattr__(self, "_raw", canonical_bytes(checked))

    @classmethod
    def from_bytes(cls, raw: bytes) -> ComparisonResult:
        checked = parse_contract("ComparisonResult", raw)
        result = object.__new__(cls)
        object.__setattr__(result, "_raw", canonical_bytes(checked))
        return result

    @classmethod
    def from_dict(cls, value: dict) -> ComparisonResult:
        return cls(value)

    @classmethod
    def build(cls, *args, **kwargs) -> ComparisonResult:
        return compare_reports(*args, **kwargs)

    def to_dict(self) -> dict:
        return parse_json(self._raw)

    def to_bytes(self) -> bytes:
        return self._raw

    @property
    def comparison_id(self) -> str:
        return self.to_dict()["comparison_id"]

    @property
    def status(self) -> str:
        return self.to_dict()["status"]

    @property
    def left_report_ref(self) -> dict:
        return self.to_dict()["left_report_ref"]

    @property
    def right_report_ref(self) -> dict:
        return self.to_dict()["right_report_ref"]

    @property
    def absolute_change(self) -> dict:
        return self.to_dict()["absolute_change"]

    @property
    def relative_change(self) -> dict:
        return self.to_dict()["relative_change"]

    @property
    def comparison_policy(self) -> str:
        return self.to_dict()["comparison_policy"]

    @property
    def inference(self) -> str:
        return self.to_dict()["inference"]

    @property
    def reason_codes(self) -> list[str]:
        return self.to_dict()["reason_codes"]

    @property
    def confounders(self) -> list[dict]:
        return self.to_dict()["confounders"]

    @property
    def caveats(self) -> list[str]:
        return self.to_dict()["caveats"]

    @property
    def is_comparable(self) -> bool:
        return self.status != "NOT_COMPARABLE"

    @property
    def is_available(self) -> bool:
        return self.status == "AVAILABLE"

    @property
    def absolute_value(self) -> float | int | None:
        return self.to_dict()["absolute_change"]["value"]

    @property
    def relative_value(self) -> float | int | None:
        return self.to_dict()["relative_change"]["value"]

    def comparison_ref(self) -> dict:
        return {"comparison_id": self.comparison_id}


def build_performance_report(
    snapshots: ObservationSnapshot | dict | Sequence[ObservationSnapshot | dict],
    *,
    metric_key: str | None = None,
    metric: dict | None = None,
    aggregation: str = "SUM",
    denominator_value: float | int | None = None,
    confounders: Sequence[dict] | None = None,
    caveats: Sequence[str] | None = None,
    as_of: str | None = None,
    stale_after_days: int | None = None,
) -> PerformanceReport:
    """Build a descriptive PerformanceReport from pinned snapshot observations.

    Strictly preserves source identity, metric definitions, window, cohort,
    and typed availability states. Never invents lift or upgrades trust.
    """
    if isinstance(snapshots, (ObservationSnapshot, dict)):
        raw_list = [snapshots]
    elif isinstance(snapshots, (list, tuple)):
        raw_list = list(snapshots)
    else:
        raise LearningError("SNAPSHOT_REQUIRED")

    require(len(raw_list) >= 1, "SNAPSHOT_REQUIRED")

    snapshot_objects: list[ObservationSnapshot] = []
    for s in raw_list:
        if isinstance(s, ObservationSnapshot):
            snapshot_objects.append(s)
        elif isinstance(s, dict):
            snapshot_objects.append(ObservationSnapshot(s))
        else:
            raise LearningError("INVALID_SNAPSHOT")

    first_dict = snapshot_objects[0].to_dict()
    first_plan = first_dict["import"]["plan"]

    platform = first_plan["platform"]
    surface = first_plan["surface"]
    account_ref = first_plan["account_ref"]
    measurement_window = first_plan["measurement_window"]
    timezone_str = first_plan["timezone"]
    cohort = first_plan["cohort"]

    # Verify context consistency across snapshots
    for snap in snapshot_objects[1:]:
        plan = snap.to_dict()["import"]["plan"]
        require(plan["platform"] == platform, "SNAPSHOT_CONTEXT_MISMATCH")
        require(plan["surface"] == surface, "SNAPSHOT_CONTEXT_MISMATCH")
        require(plan["account_ref"] == account_ref, "SNAPSHOT_CONTEXT_MISMATCH")
        require(plan["measurement_window"] == measurement_window, "SNAPSHOT_CONTEXT_MISMATCH")
        require(plan["timezone"] == timezone_str, "SNAPSHOT_CONTEXT_MISMATCH")
        require(plan["cohort"] == cohort, "SNAPSHOT_CONTEXT_MISMATCH")

    # Collect unique snapshot references
    snapshot_refs = []
    seen_snaps = set()
    for snap in snapshot_objects:
        sref = snap.snapshot_ref()
        if sref["snapshot_id"] not in seen_snaps:
            seen_snaps.add(sref["snapshot_id"])
            snapshot_refs.append(sref)

    # Determine target metric definition
    if metric is not None:
        require(type(metric) is dict and metric.get("schema_version") == "MetricDefinition.v1", "INVALID_METRIC_DEFINITION")
        require(metric["platform"] == platform, "METRIC_PLATFORM_MISMATCH")
        require(metric["cohort"] == cohort, "METRIC_COHORT_MISMATCH")
        target_metric = metric
    elif metric_key is not None:
        matched = [m for m in first_plan["metric_definitions"] if m["metric_key"] == metric_key]
        require(len(matched) > 0, "METRIC_NOT_FOUND_IN_SNAPSHOT")
        require(len(matched) == 1, "AMBIGUOUS_METRIC_DEFINITION")
        target_metric = matched[0]
    else:
        primary = first_plan.get("primary_metric")
        p_ver = first_plan.get("metric_definition_version")
        matched = [m for m in first_plan["metric_definitions"]
                   if m["metric_key"] == primary and (p_ver is None or m["definition_version"] == p_ver)]
        require(len(matched) == 1, "PRIMARY_METRIC_AMBIGUOUS_OR_MISSING")
        target_metric = matched[0]

    target_id = metric_identity(target_metric)

    # Collect matching observations and evidence references
    matching_obs = []
    evidence_refs = []
    seen_obs_ids = set()

    for snap in snapshot_objects:
        s_dict = snap.to_dict()
        for obs in s_dict["observations"]:
            if metric_identity(obs["metric"]) == target_id:
                oid = obs["observation_id"]
                require(oid not in seen_obs_ids, "DUPLICATE_OBSERVATION_ID")
                seen_obs_ids.add(oid)
                matching_obs.append(obs)
                evidence_refs.append(snap.observation_ref(oid))

    require(len(evidence_refs) >= 1, "NO_OBSERVATIONS_FOR_METRIC")

    # Evaluate counts and values
    available_values: list[float | int] = []
    not_available_count = 0

    for obs in matching_obs:
        st = obs["status"]
        if st == "AVAILABLE":
            v = obs["value"]
            require(v is not None, "INVALID_OBSERVATION_VALUE")
            require(isinstance(v, (int, float)) and math.isfinite(v), "JSON_NONFINITE")
            available_values.append(v)
        elif st == "NOT_AVAILABLE":
            require(obs["value"] is None, "INVALID_OBSERVATION_VALUE")
            not_available_count += 1
        else:
            raise LearningError("INVALID_OBSERVATION_STATUS")

    available_count = len(available_values)

    if available_count == 0:
        measurement = {
            "status": "NOT_AVAILABLE",
            "value": None,
            "unit": target_metric["unit"],
            "numerator": None,
            "denominator": None,
            "reason": "ALL_OBSERVATIONS_NOT_AVAILABLE",
        }
    else:
        if aggregation == "SUM":
            computed_num = sum(available_values)
        elif aggregation == "MEAN":
            computed_num = sum(available_values) / available_count
        elif aggregation == "COUNT":
            computed_num = float(available_count)
        else:
            raise LearningError("UNKNOWN_AGGREGATION")

        require(math.isfinite(computed_num), "JSON_NONFINITE")

        # Resolve denominator
        if target_metric.get("denominator") is not None:
            if denominator_value is not None:
                denom = denominator_value
            else:
                denom_key = target_metric["denominator"]
                denom_values = []
                denom_definitions = set()
                for snap in snapshot_objects:
                    for obs in snap.to_dict()["observations"]:
                        if obs["metric"]["metric_key"] == denom_key:
                            denom_definitions.add(digest(obs["metric"]))
                            if obs["status"] == "AVAILABLE":
                                denom_values.append(obs["value"])
                require(len(denom_definitions) <= 1, "AMBIGUOUS_DENOMINATOR_DEFINITION")
                denom = sum(denom_values) if denom_values else None

            if denom is not None:
                require(isinstance(denom, (int, float)) and math.isfinite(denom), "JSON_NONFINITE")
                if denom == 0:
                    measurement = {
                        "status": "INSUFFICIENT_DATA",
                        "value": None,
                        "unit": target_metric["unit"],
                        "numerator": computed_num,
                        "denominator": 0,
                        "reason": "Zero denominator",
                    }
                else:
                    ratio_val = computed_num / denom
                    require(math.isfinite(ratio_val), "JSON_NONFINITE")
                    measurement = {
                        "status": "AVAILABLE",
                        "value": ratio_val,
                        "unit": target_metric["unit"],
                        "numerator": computed_num,
                        "denominator": denom,
                        "reason": None,
                    }
            else:
                measurement = {
                    "status": "INSUFFICIENT_DATA",
                    "value": None,
                    "unit": target_metric["unit"],
                    "numerator": computed_num,
                    "denominator": None,
                    "reason": "Declared denominator quantity is not available",
                }
        else:
            if denominator_value is not None:
                require(isinstance(denominator_value, (int, float)) and math.isfinite(denominator_value), "JSON_NONFINITE")
                if denominator_value == 0:
                    measurement = {
                        "status": "INSUFFICIENT_DATA",
                        "value": None,
                        "unit": target_metric["unit"],
                        "numerator": computed_num,
                        "denominator": 0,
                        "reason": "Zero denominator",
                    }
                else:
                    val = computed_num / denominator_value
                    require(math.isfinite(val), "JSON_NONFINITE")
                    measurement = {
                        "status": "AVAILABLE",
                        "value": val,
                        "unit": target_metric["unit"],
                        "numerator": computed_num,
                        "denominator": denominator_value,
                        "reason": None,
                    }
            else:
                measurement = {
                    "status": "AVAILABLE",
                    "value": computed_num,
                    "unit": target_metric["unit"],
                    "numerator": computed_num,
                    "denominator": None,
                    "reason": None,
                }

    report_caveats: list[str] = ["Descriptive observation only; no causal inference or lift claimed."]
    if not_available_count > 0:
        report_caveats.append(f"{not_available_count} of {len(matching_obs)} observation(s) NOT_AVAILABLE")
    if caveats:
        for cav in caveats:
            if cav not in report_caveats:
                report_caveats.append(cav)

    report_confounders: list[dict] = list(confounders or [])

    # Check as_of and staleness
    if as_of is not None:
        as_of_dt = _parse_instant(as_of)
        start_dt = _parse_instant(measurement_window[0])
        end_dt = _parse_instant(measurement_window[1])
        require(as_of_dt >= start_dt, "AS_OF_BEFORE_WINDOW_START")
        if stale_after_days is not None:
            age_days = (as_of_dt - end_dt).total_seconds() / 86400
            if age_days > stale_after_days:
                report_caveats.append(f"STALE_DATA: Observation window ended {math.floor(age_days)} days before as_of")
                report_confounders.append({
                    "code": "STALE_DATA",
                    "description": f"Observation window ended {math.floor(age_days)} days before as_of",
                    "status": "OBSERVED",
                    "evidence_refs": evidence_refs[:10],
                })

    body = {
        "schema_version": "PerformanceReport.v1",
        "source_status": VERIFICATION,
        "snapshot_refs": snapshot_refs,
        "platform": platform,
        "surface": surface,
        "account_ref": account_ref,
        "measurement_window": measurement_window,
        "timezone": timezone_str,
        "cohort": cohort,
        "metric": target_metric,
        "evidence_refs": evidence_refs,
        "measurement": measurement,
        "available_count": available_count,
        "not_available_count": not_available_count,
        "confounders": report_confounders,
        "caveats": report_caveats,
        "inference": "DESCRIPTIVE_ONLY",
    }
    if as_of is not None:
        body["as_of"] = as_of

    return PerformanceReport(body)


def compare_reports(
    left: PerformanceReport | dict,
    right: PerformanceReport | dict,
    *,
    confounders: Sequence[dict] | None = None,
    caveats: Sequence[str] | None = None,
    allow_overlapping_windows: bool = False,
    allow_cross_surface: bool = False,
    allow_cross_account: bool = False,
    max_staleness_days: int | None = None,
    as_of: str | None = None,
) -> ComparisonResult:
    """Compare two PerformanceReports with strict comparability checks.

    Rejects cross-provider, window overlap (by default), unit/denominator mismatch,
    and unbacked causal inferences.
    """
    l_dict = left.to_dict() if isinstance(left, PerformanceReport) else validate_contract("PerformanceReport", left)
    r_dict = right.to_dict() if isinstance(right, PerformanceReport) else validate_contract("PerformanceReport", right)

    left_report_ref = {"report_id": l_dict["report_id"]}
    right_report_ref = {"report_id": r_dict["report_id"]}

    reason_codes: list[str] = []
    comp_caveats: list[str] = ["Descriptive comparison only; no causal lift or external equivalence claimed."]
    comp_confounders: list[dict] = []

    # Merge inherited confounders and caveats
    for c in (l_dict.get("confounders", []) + r_dict.get("confounders", [])):
        if c not in comp_confounders:
            comp_confounders.append(c)
    for cav in (l_dict.get("caveats", []) + r_dict.get("caveats", [])):
        if cav not in comp_caveats:
            comp_caveats.append(cav)
    if confounders:
        for c in confounders:
            if c not in comp_confounders:
                comp_confounders.append(c)
    if caveats:
        for cav in caveats:
            if cav not in comp_caveats:
                comp_caveats.append(cav)

    # Comparability checks
    if l_dict["report_id"] == r_dict["report_id"]:
        reason_codes.append("SAME_REPORT")
        comp_caveats.append("Cannot compare report to itself.")

    if l_dict["platform"] != r_dict["platform"]:
        reason_codes.append("CROSS_PLATFORM_INCOMPATIBLE")
        comp_caveats.append(f"Cross-provider equivalence between {l_dict['platform']} and {r_dict['platform']} is prohibited.")

    if l_dict["surface"] != r_dict["surface"] and not allow_cross_surface:
        reason_codes.append("SURFACE_MISMATCH")
        comp_caveats.append(f"Surface mismatch: {l_dict['surface']} vs {r_dict['surface']}.")

    if l_dict["account_ref"] != r_dict["account_ref"] and not allow_cross_account:
        reason_codes.append("ACCOUNT_MISMATCH")
        comp_caveats.append("Account reference mismatch between baseline and comparison.")

    l_m, r_m = l_dict["metric"], r_dict["metric"]
    if l_m["metric_key"] != r_m["metric_key"]:
        reason_codes.append("METRIC_KEY_MISMATCH")
    if l_m["provider_name"] != r_m["provider_name"]:
        reason_codes.append("PROVIDER_MISMATCH")
    if l_m["definition_version"] != r_m["definition_version"]:
        reason_codes.append("METRIC_VERSION_MISMATCH")
    if l_m["unit"] != r_m["unit"]:
        reason_codes.append("UNIT_MISMATCH")
    if l_m["denominator"] != r_m["denominator"]:
        reason_codes.append("DENOMINATOR_MISMATCH")

    if l_dict["cohort"] != r_dict["cohort"]:
        reason_codes.append("COHORT_MISMATCH")
        comp_caveats.append(f"Cohort mismatch: {l_dict['cohort']} vs {r_dict['cohort']}.")

    if l_dict["timezone"] != r_dict["timezone"]:
        reason_codes.append("TIMEZONE_MISMATCH")
        comp_caveats.append(f"Timezone mismatch: {l_dict['timezone']} vs {r_dict['timezone']}.")

    start_l = _parse_instant(l_dict["measurement_window"][0])
    end_l = _parse_instant(l_dict["measurement_window"][1])
    start_r = _parse_instant(r_dict["measurement_window"][0])
    end_r = _parse_instant(r_dict["measurement_window"][1])

    is_overlapping = _windows_overlap((start_l, end_l), (start_r, end_r))
    if is_overlapping:
        reason_codes.append("OVERLAPPING_WINDOWS")
        comp_confounders.append({
            "code": "OVERLAPPING_WINDOWS",
            "description": "Measurement windows overlap between baseline and comparison periods.",
            "status": "OBSERVED",
            "evidence_refs": [],
        })
        comp_caveats.append("Measurement windows overlap; comparison periods are not disjoint.")

    dur_l = (end_l - start_l).total_seconds()
    dur_r = (end_r - start_r).total_seconds()
    if dur_l != dur_r:
        reason_codes.append("WINDOW_DURATION_MISMATCH")
        comp_caveats.append(f"Measurement window durations differ: {dur_l}s vs {dur_r}s.")

    if start_l > start_r:
        reason_codes.append("INVERTED_WINDOW_ORDER")
        comp_caveats.append("Baseline window starts after comparison window.")

    if as_of is not None:
        as_of_dt = _parse_instant(as_of)
        if max_staleness_days is not None:
            age_l = (as_of_dt - end_l).total_seconds() / 86400
            age_r = (as_of_dt - end_r).total_seconds() / 86400
            if age_l > max_staleness_days or age_r > max_staleness_days:
                reason_codes.append("STALE_DATA")
                comp_confounders.append({
                    "code": "STALE_DATA",
                    "description": f"Comparison data is stale relative to as_of ({max(age_l, age_r):.1f} days old).",
                    "status": "OBSERVED",
                    "evidence_refs": [],
                })
                comp_caveats.append("Report data is stale relative to as_of timestamp.")

    incompatible_reasons = set(INCOMPATIBLE_CODES)
    if not allow_overlapping_windows:
        incompatible_reasons.add("OVERLAPPING_WINDOWS")

    is_not_comparable = any(rc in incompatible_reasons for rc in reason_codes)

    if is_not_comparable:
        status = "NOT_COMPARABLE"
        reason_str = "NOT_COMPARABLE: " + "; ".join(reason_codes)
        absolute_change = {
            "status": "NOT_COMPARABLE",
            "value": None,
            "unit": l_dict["metric"]["unit"],
            "numerator": None,
            "denominator": None,
            "reason": reason_str,
        }
        relative_change = {
            "status": "NOT_COMPARABLE",
            "value": None,
            "unit": "ratio",
            "numerator": None,
            "denominator": None,
            "reason": reason_str,
        }
    else:
        l_meas = l_dict["measurement"]
        r_meas = r_dict["measurement"]

        if l_meas["status"] == "NOT_AVAILABLE" or r_meas["status"] == "NOT_AVAILABLE":
            status = "NOT_AVAILABLE"
            reason_codes.append("MEASUREMENT_NOT_AVAILABLE")
            missing = ("baseline and comparison" if l_meas["status"] == r_meas["status"]
                       else "baseline" if l_meas["status"] == "NOT_AVAILABLE" else "comparison")
            reason_str = f"Measurement NOT_AVAILABLE in {missing} report"
            absolute_change = {
                "status": "NOT_AVAILABLE",
                "value": None,
                "unit": l_dict["metric"]["unit"],
                "numerator": None,
                "denominator": None,
                "reason": reason_str,
            }
            relative_change = {
                "status": "NOT_AVAILABLE",
                "value": None,
                "unit": "ratio",
                "numerator": None,
                "denominator": None,
                "reason": reason_str,
            }
        elif l_meas["status"] == "INSUFFICIENT_DATA" or r_meas["status"] == "INSUFFICIENT_DATA":
            status = "INSUFFICIENT_DATA"
            reason_codes.append("MEASUREMENT_INSUFFICIENT_DATA")
            insuf = ("baseline and comparison" if l_meas["status"] == r_meas["status"]
                     else "baseline" if l_meas["status"] == "INSUFFICIENT_DATA" else "comparison")
            reason_str = f"Measurement INSUFFICIENT_DATA in {insuf} report"
            absolute_change = {
                "status": "INSUFFICIENT_DATA",
                "value": None,
                "unit": l_dict["metric"]["unit"],
                "numerator": None,
                "denominator": None,
                "reason": reason_str,
            }
            relative_change = {
                "status": "INSUFFICIENT_DATA",
                "value": None,
                "unit": "ratio",
                "numerator": None,
                "denominator": None,
                "reason": reason_str,
            }
        else:
            # Both are AVAILABLE
            l_val = l_meas["value"]
            r_val = r_meas["value"]
            require(isinstance(l_val, (int, float)) and math.isfinite(l_val), "JSON_NONFINITE")
            require(isinstance(r_val, (int, float)) and math.isfinite(r_val), "JSON_NONFINITE")

            diff = r_val - l_val
            require(math.isfinite(diff), "JSON_NONFINITE")

            absolute_change = {
                "status": "AVAILABLE",
                "value": diff,
                "unit": l_dict["metric"]["unit"],
                "numerator": diff,
                "denominator": None,
                "reason": None,
            }

            if l_val == 0:
                relative_change = {
                    "status": "INSUFFICIENT_DATA",
                    "value": None,
                    "unit": "ratio",
                    "numerator": diff,
                    "denominator": 0,
                    "reason": "Zero denominator",
                }
                reason_codes.append("ZERO_DENOMINATOR")
                comp_caveats.append("Relative change is INSUFFICIENT_DATA because baseline measurement is zero.")
            else:
                rel = diff / abs(l_val)
                require(math.isfinite(rel), "JSON_NONFINITE")
                relative_change = {
                    "status": "AVAILABLE",
                    "value": rel,
                    "unit": "ratio",
                    "numerator": diff,
                    "denominator": l_val,
                    "reason": None,
                }
            status = "AVAILABLE"

    # Ensure all reason_codes match KEY format
    for code in reason_codes:
        _check_key(code)

    comp_body = {
        "schema_version": "ComparisonResult.v1",
        "source_status": VERIFICATION,
        "left_report_ref": left_report_ref,
        "right_report_ref": right_report_ref,
        "status": status,
        "absolute_change": absolute_change,
        "relative_change": relative_change,
        "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE",
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "confounders": comp_confounders,
        "caveats": comp_caveats,
        "inference": "DESCRIPTIVE_ONLY",
    }

    return ComparisonResult(comp_body)


def validate_performance_report(value: dict) -> dict:
    return validate_contract("PerformanceReport", value)


def validate_comparison_result(value: dict) -> dict:
    return validate_contract("ComparisonResult", value)


def parse_performance_report(raw: bytes) -> PerformanceReport:
    return PerformanceReport.from_bytes(raw)


def parse_comparison_result(raw: bytes) -> ComparisonResult:
    return ComparisonResult.from_bytes(raw)


__all__ = [
    "ComparisonResult",
    "PerformanceReport",
    "build_performance_report",
    "compare_reports",
    "parse_comparison_result",
    "parse_performance_report",
    "validate_comparison_result",
    "validate_performance_report",
]
