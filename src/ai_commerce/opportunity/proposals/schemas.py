"""Self-contained M04 contract; no remote references or shared schema changes."""
from __future__ import annotations

from copy import deepcopy

from ..audience.schemas import INSIGHT_ID, RAW_REF, REVISION, STATUSES
from ..core.schemas import (
    IMPORT_ID, LINEAGE, RIGHTS_STATUSES, SHA, SIGNAL_ID, SOURCE_STATUS,
    WINDOW, nullable_text, obj, text,
)
from ..trends.schemas import BLOCKERS, DIRECTIONS, RULE, identity

MAX_SERIES = 16
MAX_INSIGHTS = 16
MAX_SIGNALS = 10000
MAX_RECORD_BYTES = 8 * 1024 * 1024
DATE = {"type": "string", "format": "date-time", "maxLength": 64}
FRESHNESS = {"enum": ["FRESH", "STALE", "FUTURE", "UNKNOWN"]}
REF = {"type": "string", "pattern": (
    "^(?:sha256:[a-f0-9]{64}|(?:series|hyp|sig|a10|aud)-[a-f0-9]{64}"
    "(?:/statements/[0-9]{1,2})?)$"
)}


def array(items: dict, limit: int, *, unique: bool = False) -> dict:
    result = {"type": "array", "maxItems": limit, "items": items}
    if unique:
        result["uniqueItems"] = True
    return result


CREATIVE_FIELDS = {
    "objective": text(8192), "audience": nullable_text(1024),
    "product_ref": nullable_text(1024), "cta": nullable_text(4096),
    "media_hint": nullable_text(1024),
}
OVERRIDES = obj(deepcopy(CREATIVE_FIELDS), required=[])
MANUAL = obj({"text": text(8192), "text_sha256": SHA})
SIGNAL_EVIDENCE = obj({
    "import_id": IMPORT_ID, "signal_id": SIGNAL_ID, "raw_sha256": SHA, "raw_ref": RAW_REF,
    "import_sha256": SHA, "signal_sha256": SHA,
    "context_sha256": SHA, "scope_sha256": SHA, "lineage": LINEAGE,
    "source_status": {"const": SOURCE_STATUS},
    "source_rights_status": {"enum": RIGHTS_STATUSES},
    "signal_rights_status": {"enum": RIGHTS_STATUSES},
    "signal_status": {"enum": ["AVAILABLE", "NOT_AVAILABLE"]},
    "value": {"type": ["number", "null"]},
    "unit": text(64), "denominator": nullable_text(256),
    "locale": text(64), "geo": text(64),
    "observed_at": DATE, "collected_at": DATE,
    "observation_window": {"oneOf": [WINDOW, {"type": "null"}]},
    "freshness": FRESHNESS,
})
SIGNAL_EVIDENCE["allOf"] = [{
    "if": {"properties": {"signal_status": {"const": "NOT_AVAILABLE"}}},
    "then": {"properties": {"value": {"type": "null"}}},
    "else": {"properties": {"value": {"type": "number"}}},
}]
STATEMENT = obj({
    "index": {"type": "integer", "minimum": 0, "maximum": 99},
    "kind": {"enum": ["JOB_TO_BE_DONE", "FRICTION", "CONTENT_CONTEXT", "ALTERNATIVE", "COUNTEREXAMPLE"]},
    "status": {"enum": STATUSES}, "text": text(4096), "uncertainty": text(2048),
    "note_ref": RAW_REF, "evidence_status": {"enum": ["NOTE_ONLY", "LOCAL_INTEGRITY_CHECKED"]},
    "signal_ids": array(SIGNAL_ID, 32, unique=True),
    "matched_signal_ids": array(SIGNAL_ID, 32, unique=True),
    "relation_to_trend": {"enum": ["MATCHED_SIGNAL", "UNLINKED_CONTEXT", "NO_TREND"]},
})
AUDIENCE_TRACE = obj({
    "insight_id": INSIGHT_ID, "record_sha256": SHA, "source_sha256": SHA, "source_ref": RAW_REF,
    "data_kind": {"enum": ["USER_SUPPLIED_QUALITATIVE", "SYNTHETIC_FIXTURE"]},
    "source_status": {"const": SOURCE_STATUS}, "rights_status": {"const": "UNKNOWN"},
    "lineage": REVISION, "noted_at": {**DATE, "type": ["string", "null"]},
    "audience_segment": nullable_text(1024), "freshness": FRESHNESS,
    "statements": array(STATEMENT, 100),
})
TREND_TRACE = obj({
    "series_id": identity("series"), "series_sha256": SHA,
    "hypothesis_id": identity("hyp"), "hypothesis_sha256": SHA,
    "category": {"enum": ["TOPIC", "CREATIVE_FORMAT", "UNKNOWN"]},
    "comparison_status": {"enum": ["COMPARABLE", "INCOMPARABLE", "UNKNOWN"]},
    "status": {"enum": BLOCKERS + DIRECTIONS}, "direction": {"enum": DIRECTIONS + [None]},
    "conditions": array({"enum": BLOCKERS}, len(BLOCKERS), unique=True), "rule": RULE,
    "observation_count": {"type": "integer", "minimum": 0, "maximum": MAX_SIGNALS},
    "independent_timestamp_count": {"type": "integer", "minimum": 0, "maximum": MAX_SIGNALS},
    "supporting_signal_ids": array(SIGNAL_ID, MAX_SIGNALS, unique=True),
    "counterevidence_signal_ids": array(SIGNAL_ID, MAX_SIGNALS, unique=True),
    "unavailable_signal_ids": array(SIGNAL_ID, MAX_SIGNALS, unique=True),
    "unused_signal_ids": array(SIGNAL_ID, MAX_SIGNALS, unique=True),
    "reason_codes": array(text(128), 50),
    "comparison": obj({k: {"type": ["number", "null"]} for k in (
        "baseline_median", "latest_value", "delta", "relative_change", "threshold")}),
})
REASON = obj({
    "code": text(128),
    "polarity": {"enum": ["SUPPORT", "COUNTEREVIDENCE", "CONTEXT", "LIMITATION", "ALTERNATIVE"]},
    "epistemic_status": {"enum": STATUSES}, "text": text(4096),
    "source_refs": array(REF, MAX_SIGNALS + 2, unique=True),
})
PROPOSAL = obj({
    "schema_version": {"const": "OpportunityProposal.v1"}, "proposal_id": identity("opp"),
    "kind": {"enum": ["TREND_CONTENT", "AUDIENCE_CONTENT", "MANUAL_CONTENT"]},
    "status": {"const": "HYPOTHESIS"}, "epistemic_status": {"const": "INFERRED"},
    "source_status": {"const": SOURCE_STATUS}, "rights_status": {"const": "UNKNOWN"},
    "suggestion_only": {"const": True}, "publication_status": {"const": "NOT_ATTEMPTED"},
    "as_of": DATE, "freshness_max_age_seconds": {"type": "integer", "minimum": 0},
    "topic": nullable_text(256), "creative": obj(CREATIVE_FIELDS), "creative_overrides": OVERRIDES,
    "manual_input": {"oneOf": [MANUAL, {"type": "null"}]},
    "trend": {"oneOf": [TREND_TRACE, {"type": "null"}]},
    "audience_insights": array(AUDIENCE_TRACE, MAX_INSIGHTS),
    "signal_evidence": array(SIGNAL_EVIDENCE, MAX_SIGNALS),
    "reasons": array(REASON, MAX_INSIGHTS * 102 + 10),
    "alternatives": array(text(4096), MAX_INSIGHTS * 100 + 2),
    "source_refs": array(REF, MAX_SIGNALS * 2 + MAX_INSIGHTS * 102 + 2, unique=True),
    "limitations": array(text(1024), 100),
})
PROPOSAL["allOf"] = [
    {"if": {"properties": {"kind": {"const": "MANUAL_CONTENT"}}},
     "then": {"properties": {"manual_input": MANUAL, "trend": {"type": "null"},
                               "audience_insights": {"maxItems": 0}, "signal_evidence": {"maxItems": 0}}},
     "else": {"properties": {"manual_input": {"type": "null"}}}},
    {"if": {"properties": {"kind": {"const": "TREND_CONTENT"}}},
     "then": {"properties": {"trend": TREND_TRACE, "topic": text(256), "signal_evidence": {"minItems": 1}}}},
    {"if": {"properties": {"kind": {"const": "AUDIENCE_CONTENT"}}},
     "then": {"properties": {"trend": {"type": "null"}, "audience_insights": {"minItems": 1}}}},
]


def schema() -> dict:
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://ai-commerce.local/schema/OpportunityProposal.v1",
        "title": "OpportunityProposal.v1",
        "description": "Offline creative hypotheses. Integrity hashes grant no provider rights or action authority.",
        **PROPOSAL,
    })
