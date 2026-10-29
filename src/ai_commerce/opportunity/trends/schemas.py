"""Self-contained M02 contracts; JSON mirrors live in this component only."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache

from jsonschema import Draft202012Validator
from referencing import Registry

from ai_commerce.contracts.schema import _format_checker
from ai_commerce.opportunity.core.models import require
from ai_commerce.opportunity.core.schemas import (
    LINEAGE, SHA, SIGNAL_ID, SIGNAL_IMPORT, SIGNAL_SOURCE_SNAPSHOT,
    SOURCE_STATUS, TREND_SIGNAL, nullable_text, obj, text,
)
from ai_commerce.core.provenance import canonical_bytes

MAX_IMPORTS = 100
MAX_POINTS = 10000
CATEGORIES = ["TOPIC", "CREATIVE_FORMAT", "UNKNOWN"]
BLOCKERS = ["INCOMPARABLE", "CONTRADICTORY", "UNKNOWN", "STALE", "INSUFFICIENT_HISTORY"]
DIRECTIONS = ["GROWING", "DROPPING", "STABLE"]
DATE = {"type": "string", "format": "date-time", "maxLength": 64}
NULL_NUMBER = {"type": ["number", "null"]}


def array(items: dict, maximum: int = MAX_POINTS) -> dict:
    return {"type": "array", "maxItems": maximum, "items": items}


def identity(prefix: str) -> dict:
    return {"type": "string", "pattern": "^" + prefix + "-[a-f0-9]{64}$"}


COMPARISON_CONTEXT = obj({
    "schema_version": {"const": "TrendComparisonContext.v1"},
    "metric": text(128),
    "metric_kind": {"enum": ["COUNT", "RATE", "RELATIVE_INDEX", "UNKNOWN"]},
    "baseline_id": nullable_text(256),
    "normalization_id": nullable_text(256),
    "query_batch": nullable_text(256),
    "relative_scale": {"oneOf": [
        {"type": "null"},
        {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
    ]},
    "window_kind": {"enum": ["POINT", "INTERVAL", "UNKNOWN"]},
    "evidence_refs": {**array(text(1024), 100), "minItems": 1},
    "source_status": {"const": SOURCE_STATUS},
})

COHORT_KEY = obj({
    "topic": text(256), "category": {"enum": CATEGORIES}, "signal_type": text(64),
    "source_id": text(128), "source_kind": text(128), "source_type": text(64),
    "scope_sha256": SHA, "mapping_sha256": SHA,
    "metric": nullable_text(128), "metric_kind": {"enum": ["COUNT", "RATE", "RELATIVE_INDEX", "UNKNOWN"]},
    "unit": text(64), "denominator": nullable_text(256), "query_context": text(256),
    "baseline_id": nullable_text(256), "normalization_id": nullable_text(256),
    "query_batch": nullable_text(256), "relative_scale": deepcopy(COMPARISON_CONTEXT["properties"]["relative_scale"]),
    "geo": text(64), "locale": text(64), "timezone": text(128),
    "window_kind": {"enum": ["POINT", "INTERVAL", "UNKNOWN"]},
    "window_offsets_microseconds": {"oneOf": [
        {"type": "null"},
        {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer"}},
    ]},
})

TREND_SERIES = obj({
    "schema_version": {"const": "TrendSeries.v1"}, "series_id": identity("series"),
    "topic": text(256), "signal_type": text(64), "category": {"enum": CATEGORIES},
    "comparison_status": {"enum": ["COMPARABLE", "INCOMPARABLE", "UNKNOWN"]},
    "replay_status": {"const": "M01_REPLAY_VERIFIED_AT_BUILD"},
    "source_status": {"const": SOURCE_STATUS},
    "imports": array(deepcopy(SIGNAL_IMPORT), MAX_IMPORTS),
    "points": array(obj({
        "signal": deepcopy(TREND_SIGNAL),
        "comparison_context": {"oneOf": [COMPARISON_CONTEXT, {"type": "null"}]},
        "comparison_sha256": {"oneOf": [SHA, {"type": "null"}]},
        "unknown_reasons": array(text(128), 30),
    })),
    "cohorts": array(obj({
        "cohort_id": identity("cohort"), "key": COHORT_KEY, "signal_ids": array(SIGNAL_ID),
    })),
    "excluded_signal_ids": array(SIGNAL_ID),
    "limitations": array(text(1024), 30),
})

RULE = obj({
    "version": {"const": "median-baseline-delta.v1"},
    "min_observations": {"type": "integer", "minimum": 3, "maximum": MAX_POINTS},
    "baseline_points": {"type": ["integer", "null"], "minimum": 2, "maximum": MAX_POINTS - 1},
    "relative_delta": {"type": "number", "minimum": 0},
    "absolute_delta": {"type": "number", "minimum": 0},
    "max_age_seconds": {"type": "integer", "minimum": 0},
})
TREND_HYPOTHESIS = obj({
    "schema_version": {"const": "TrendHypothesis.v1"}, "hypothesis_id": identity("hyp"),
    "series_id": identity("series"), "as_of": DATE,
    "status": {"enum": BLOCKERS + DIRECTIONS}, "conditions": array({"enum": BLOCKERS}, len(BLOCKERS)),
    "direction": {"enum": DIRECTIONS + [None]},
    "epistemic_status": {"const": "INFERRED"}, "source_status": {"const": SOURCE_STATUS},
    "rule": RULE,
    "observation_count": {"type": "integer", "minimum": 0, "maximum": MAX_POINTS},
    "independent_timestamp_count": {"type": "integer", "minimum": 0, "maximum": MAX_POINTS},
    "supporting_signal_ids": array(SIGNAL_ID), "counterevidence_signal_ids": array(SIGNAL_ID),
    "unavailable_signal_ids": array(SIGNAL_ID), "unused_signal_ids": array(SIGNAL_ID),
    "reason_codes": array(text(128), 50),
    "comparison": obj({
        "baseline_median": NULL_NUMBER, "latest_value": NULL_NUMBER,
        "delta": NULL_NUMBER, "relative_change": NULL_NUMBER, "threshold": NULL_NUMBER,
    }),
    "limitations": array(text(1024), 30),
})
TREND_HYPOTHESIS["properties"]["conditions"]["uniqueItems"] = True
TREND_HYPOTHESIS["allOf"] = [
    {
        "if": {"properties": {"conditions": {"minItems": 1}}},
        "then": {"properties": {"direction": {"type": "null"}, "status": {"enum": BLOCKERS}}},
        "else": {"properties": {"direction": {"enum": DIRECTIONS}, "status": {"enum": DIRECTIONS}}},
    },
    *({"if": {"properties": {"status": {"const": direction}}},
       "then": {"properties": {"direction": {"const": direction}}}} for direction in DIRECTIONS),
    *({"if": {"properties": {"status": {"const": blocker}}},
       "then": {"properties": {"conditions": {"contains": {"const": blocker}}}}} for blocker in BLOCKERS),
]

DEFINITIONS = {"TrendComparisonContext": COMPARISON_CONTEXT, "TrendSeries": TREND_SERIES,
               "TrendHypothesis": TREND_HYPOTHESIS}


def schema(name: str) -> dict:
    require(name in DEFINITIONS, "UNKNOWN_M02_CONTRACT")
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://ai-commerce.local/schema/{name}.v1",
        "title": name + ".v1", **DEFINITIONS[name],
        "$defs": {"SignalSourceSnapshot": SIGNAL_SOURCE_SNAPSHOT, "SignalLineage": LINEAGE},
    })


@lru_cache(maxsize=3)
def _validator(name: str) -> Draft202012Validator:
    definition = schema(name)
    Draft202012Validator.check_schema(definition)
    return Draft202012Validator(definition, registry=Registry(), format_checker=_format_checker())


def validate_contract(name: str, value: dict) -> dict:
    require(_validator(name).is_valid(value), "INVALID_" + name.upper())
    try:
        canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        require(False, "INVALID_M02_CANONICAL_VALUE")
    return value
