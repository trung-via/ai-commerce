"""Self-contained AudienceInsight v1 contract; all schema references are local."""
from __future__ import annotations

from copy import deepcopy

from ..core.schemas import (
    IMPORT_ID, LINEAGE, RIGHTS_STATUSES, SHA, SIGNAL_ID, SOURCE_STATUS,
    nullable_text, obj, text,
)

INSIGHT_ID = {"type": "string", "pattern": "^aud-[a-f0-9]{64}$"}
RAW_REF = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"}
KINDS = ["JOB_TO_BE_DONE", "FRICTION", "CONTENT_CONTEXT", "ALTERNATIVE", "COUNTEREXAMPLE"]
STATUSES = ["OBSERVED_USER_SUPPLIED", "INFERRED", "UNKNOWN"]
DATA_KINDS = ["USER_SUPPLIED_QUALITATIVE", "SYNTHETIC_FIXTURE"]

EVIDENCE_REF = obj({
    "import_id": IMPORT_ID,
    "signal_id": SIGNAL_ID,
    "raw_sha256": SHA,
    "evidence_ref": text(1024),
})
REFS = {"type": "array", "maxItems": 32, "uniqueItems": True, "items": EVIDENCE_REF}
NOTE_STATEMENT = obj({
    "kind": {"enum": KINDS},
    "status": {"enum": STATUSES},
    "text": text(4096),
    "uncertainty": text(2048),
    "evidence_refs": REFS,
}, required=["kind", "status", "text"])

NOTES = obj({
    "schema_version": {"const": "AudienceNotes.v1"},
    "data_kind": {"enum": DATA_KINDS},
    "source_note": text(4096),
    "noted_at": {"type": ["string", "null"], "format": "date-time", "maxLength": 64},
    "audience_segment": nullable_text(1024),
    "statements": {"type": "array", "maxItems": 100, "items": NOTE_STATEMENT},
}, required=["schema_version", "data_kind", "source_note", "statements"])

STATEMENT = obj({
    **NOTE_STATEMENT["properties"],
    "note_ref": RAW_REF,
    "evidence_status": {"enum": ["NOTE_ONLY", "LOCAL_INTEGRITY_CHECKED"]},
})
STATEMENT["allOf"] = [{
    "if": {"properties": {"evidence_status": {"const": "NOTE_ONLY"}}},
    "then": {"properties": {"evidence_refs": {"maxItems": 0}}},
    "else": {"properties": {"evidence_refs": {"minItems": 1}}},
}]
PROOF = obj({
    "reference": EVIDENCE_REF,
    "context_sha256": SHA,
    "scope_sha256": SHA,
    "lineage": LINEAGE,
    "source_status": {"const": SOURCE_STATUS},
    "source_rights_status": {"enum": RIGHTS_STATUSES},
    "signal_rights_status": {"enum": RIGHTS_STATUSES},
    "signal_status": {"enum": ["AVAILABLE", "NOT_AVAILABLE"]},
    "value": {"type": ["number", "null"]},
    "observed_at": {"type": "string", "format": "date-time", "maxLength": 64},
})
PROOF["allOf"] = [{
    "if": {"properties": {"signal_status": {"const": "NOT_AVAILABLE"}}},
    "then": {"properties": {"value": {"type": "null"}}},
    "else": {"properties": {"value": {"type": "number"}}},
}]
REVISION = obj({
    "relationship": {"enum": ["ORIGINAL", "REVISION"]},
    "parent_insight_id": {"oneOf": [INSIGHT_ID, {"type": "null"}]},
    "number": {"type": "integer", "minimum": 1, "maximum": 128},
})
REVISION["allOf"] = [{
    "if": {"properties": {"relationship": {"const": "ORIGINAL"}}},
    "then": {"properties": {"parent_insight_id": {"type": "null"}, "number": {"const": 1}}},
    "else": {"properties": {"parent_insight_id": INSIGHT_ID, "number": {"minimum": 2}}},
}]
INSIGHT = obj({
    "schema_version": {"const": "AudienceInsight.v1"},
    "insight_id": INSIGHT_ID,
    "source_sha256": SHA,
    "source_ref": RAW_REF,
    "source_status": {"const": SOURCE_STATUS},
    "rights_status": {"const": "UNKNOWN"},
    "data_kind": {"enum": DATA_KINDS},
    "source_note": text(4096),
    "noted_at": NOTES["properties"]["noted_at"],
    "audience_segment": nullable_text(1024),
    "statements": {"type": "array", "maxItems": 100, "items": STATEMENT},
    "signal_evidence": {"type": "array", "maxItems": 3200, "uniqueItems": True, "items": PROOF},
    "lineage": REVISION,
})


def schema() -> dict:
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://ai-commerce.local/schema/AudienceInsight.v1",
        "title": "AudienceInsight.v1",
        "description": "Offline qualitative notes and hypotheses. Local integrity is not provider verification.",
        **INSIGHT,
    })
