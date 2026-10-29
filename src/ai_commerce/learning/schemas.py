"""A11 v1 contracts, with local references only and no A02 loader changes.

Runtime definitions make installed wheels self-contained. The published JSON
mirrors are checked against these definitions. A09 definitions are reused intact.
"""
from __future__ import annotations

from copy import deepcopy

from ai_commerce.analytics.schemas import (
    CONTEXT, DEFINITIONS as A09_DEFINITIONS, IMPORT_ID, KEY, SHA, TARGET_RULES,
    VERIFICATION, nullable_text, obj, text,
)

READER_VERSION = "a11-reader.v1"
MAX_OBSERVATIONS = 10000
EVIDENCE_STATES = ("AVAILABLE", "NOT_AVAILABLE", "INSUFFICIENT_DATA", "NOT_COMPARABLE")
CONTRACT_IDS = {
    "ObservationSnapshot": ("snapshot_id", "a11-snapshot-"),
    "PerformanceReport": ("report_id", "a11-report-"),
    "ComparisonResult": ("comparison_id", "a11-comparison-"),
    "LearningHypothesis": ("hypothesis_id", "a11-hypothesis-"),
    "LearningEvidence": ("evidence_id", "a11-evidence-"),
    "LearningRecord": ("record_id", "a11-learning-"),
    "LearningRevision": ("revision_id", "a11-revision-"),
}


def identity(prefix: str) -> dict:
    return {"type": "string", "pattern": "^" + prefix + "[a-f0-9]{64}$"}


def ref(name: str) -> dict:
    return {"$ref": "#/$defs/" + name}


def array(item: dict, *, maximum: int = 100, minimum: int = 0, unique: bool = False) -> dict:
    value = {"type": "array", "minItems": minimum, "maxItems": maximum, "items": item}
    if unique:
        value["uniqueItems"] = True
    return value


def optional(value: dict) -> dict:
    return {"oneOf": [value, {"type": "null"}]}


OBSERVATION_ID = identity("obs-")
SNAPSHOT_REF = obj({
    "snapshot_id": identity("a11-snapshot-"), "import_id": IMPORT_ID,
    "context_sha256": SHA, "raw_sha256": SHA,
})
OBSERVATION_REF = obj({
    **SNAPSHOT_REF["properties"], "observation_id": OBSERVATION_ID,
    "observation_sha256": SHA, "metric_definition_sha256": SHA,
})
PIN = obj({
    "import_id": IMPORT_ID, "context_sha256": SHA, "raw_sha256": SHA,
    "observation_ids": array(OBSERVATION_ID, maximum=MAX_OBSERVATIONS, minimum=1, unique=True),
})
REPORT_REF = obj({"report_id": identity("a11-report-")})
HYPOTHESIS_REF = obj({"hypothesis_id": identity("a11-hypothesis-")})
EVIDENCE_REF = obj({"evidence_id": identity("a11-evidence-")})
RECORD_REF = obj({"record_id": identity("a11-learning-")})
REVISION_REF = obj({"revision_id": identity("a11-revision-"),
                    "revision": {"type": "integer", "minimum": 1, "maximum": 1000000}})
CONFOUNDER = obj({
    "code": KEY, "description": text(2048),
    "status": {"enum": ["DECLARED", "OBSERVED", "UNKNOWN"]},
    "evidence_refs": array(ref("ObservationReference")),
})
CONFOUNDERS = array(ref("Confounder"))
CAVEATS = array(text(2048))
NUMBER = {"type": ["number", "null"], "minimum": -1e100, "maximum": 1e100}
MEASUREMENT = obj({
    "status": {"enum": list(EVIDENCE_STATES)}, "value": NUMBER,
    "unit": text(64), "numerator": NUMBER, "denominator": NUMBER,
    "reason": nullable_text(2048),
})
MEASUREMENT["description"] = (
    "AVAILABLE includes numeric zero. Other states require null, never imputed zero. "
    "Numerator/denominator are explicit quantities, null when absent or inapplicable."
)
MEASUREMENT["allOf"] = [{
    "if": {"properties": {"status": {"const": "AVAILABLE"}}},
    "then": {"properties": {"value": {"type": "number"}}},
    "else": {"properties": {"value": {"type": "null"}, "reason": text(2048)}},
}]


def contract(name: str, properties: dict, *, optional_fields: tuple[str, ...] = ()) -> dict:
    field, prefix = CONTRACT_IDS[name]
    properties = {"schema_version": {"const": name + ".v1"}, field: identity(prefix),
                  "source_status": {"const": VERIFICATION}, **properties}
    return obj(properties, [key for key in properties if key not in optional_fields])


SNAPSHOT = contract("ObservationSnapshot", {
    "reader_version": {"const": READER_VERSION},
    "verification_scope": {"const": "LOCAL_HASH_AND_A09_REPLAY_ONLY"},
    "pin": ref("SnapshotPin"), "import": ref("AnalyticsImport"),
    "observations": array(ref("PerformanceObservation"), maximum=MAX_OBSERVATIONS, minimum=1),
})
SNAPSHOT["properties"]["import"] = {
    "allOf": [ref("AnalyticsImport"), {"properties": {"status": {"const": "IMPORTED"}}}],
}
REPORT = contract("PerformanceReport", {
    "snapshot_refs": array(ref("SnapshotReference"), minimum=1, unique=True),
    **CONTEXT, "metric": ref("MetricDefinition"),
    "evidence_refs": array(ref("ObservationReference"), maximum=MAX_OBSERVATIONS, minimum=1, unique=True),
    "measurement": ref("Measurement"),
    "available_count": {"type": "integer", "minimum": 0, "maximum": MAX_OBSERVATIONS},
    "not_available_count": {"type": "integer", "minimum": 0, "maximum": MAX_OBSERVATIONS},
    "confounders": CONFOUNDERS, "caveats": CAVEATS,
    "inference": {"const": "DESCRIPTIVE_ONLY"},
    "as_of": {"type": ["string", "null"], "format": "date-time", "maxLength": 64},
}, optional_fields=("as_of",))
REPORT["allOf"] = deepcopy(TARGET_RULES)
COMPARISON = contract("ComparisonResult", {
    "left_report_ref": ref("ReportReference"), "right_report_ref": ref("ReportReference"),
    "status": {"enum": list(EVIDENCE_STATES)},
    "absolute_change": ref("Measurement"), "relative_change": ref("Measurement"),
    "comparison_policy": {"const": "NO_CROSS_PROVIDER_EQUIVALENCE"},
    "reason_codes": array(KEY, unique=True), "confounders": CONFOUNDERS, "caveats": CAVEATS,
    "inference": {"const": "DESCRIPTIVE_ONLY"},
})
COMPARISON["allOf"] = [{
    "if": {"properties": {"status": {"enum": list(EVIDENCE_STATES[1:])}}},
    "then": {"properties": {
        key: {"properties": {"value": {"type": "null"}, "status": {"enum": list(EVIDENCE_STATES[1:])}}}
        for key in ("absolute_change", "relative_change")
    }},
}]
HYPOTHESIS = contract("LearningHypothesis", {
    "statement": text(4096), **CONTEXT,
    "creative_ref": obj({"creative_id": text(), "version": text(64), "content_sha256": optional(SHA)}),
    "expected_outcome": text(2048), "uncertainty": text(2048), "confounders": CONFOUNDERS,
    "inference": {"const": "HYPOTHESIS_ONLY"},
})
HYPOTHESIS["allOf"] = deepcopy(TARGET_RULES)
EVIDENCE = contract("LearningEvidence", {
    "hypothesis_ref": ref("HypothesisReference"), "observation_ref": ref("ObservationReference"),
    "report_ref": optional(ref("ReportReference")),
    "role": {"enum": ["SUPPORTING", "COUNTEREVIDENCE", "INCONCLUSIVE"]},
    "evidence_state": {"enum": list(EVIDENCE_STATES)},
    "observed_outcome": text(2048), "confounders": CONFOUNDERS, "limitations": CAVEATS,
    "inference": {"const": "DESCRIPTIVE_ONLY"},
}, optional_fields=("report_ref",))
RECORD = contract("LearningRecord", {
    "learning_key": KEY, "hypothesis_ref": ref("HypothesisReference"),
    "evidence_refs": array(ref("EvidenceReference"), unique=True),
    "counterevidence_refs": array(ref("EvidenceReference"), unique=True),
    "status": {"enum": ["PROPOSED", "SUPPORTED", "CONTRADICTED", "MIXED", "INCONCLUSIVE", "RETIRED"]},
    "uncertainty": text(2048), "confounders": CONFOUNDERS,
    "inference": {"const": "ADVISORY_ONLY"},
})
REVISION = contract("LearningRevision", {
    "learning_key": KEY, "revision": {"type": "integer", "minimum": 1, "maximum": 1000000},
    "previous_revision_ref": optional(ref("RevisionReference")), "record_ref": ref("RecordReference"),
    "operation": {"enum": ["CREATE", "UPDATE", "CORRECTION", "RETRACT"]},
    "reason": text(2048), "actor_ref": text(),
    "recorded_at": {"type": "string", "format": "date-time", "maxLength": 64},
})
REVISION["allOf"] = [{
    "if": {"properties": {"revision": {"const": 1}}},
    "then": {"properties": {"previous_revision_ref": {"type": "null"}, "operation": {"const": "CREATE"}}},
    "else": {"properties": {"previous_revision_ref": ref("RevisionReference"),
                             "operation": {"enum": ["UPDATE", "CORRECTION", "RETRACT"]}}},
}]
DEFINITIONS = {
    "ObservationSnapshot": SNAPSHOT, "PerformanceReport": REPORT, "ComparisonResult": COMPARISON,
    "LearningHypothesis": HYPOTHESIS, "LearningEvidence": EVIDENCE,
    "LearningRecord": RECORD, "LearningRevision": REVISION,
}
_SHARED = {
    **A09_DEFINITIONS, "SnapshotPin": PIN, "SnapshotReference": SNAPSHOT_REF,
    "ObservationReference": OBSERVATION_REF, "ReportReference": REPORT_REF,
    "HypothesisReference": HYPOTHESIS_REF, "EvidenceReference": EVIDENCE_REF,
    "RecordReference": RECORD_REF, "RevisionReference": REVISION_REF,
    "Confounder": CONFOUNDER, "Measurement": MEASUREMENT,
}


def schema(name: str) -> dict:
    """Isolated 2020-12 schema with only its transitive local definitions."""
    root = deepcopy(DEFINITIONS[name])
    dependencies = {}
    pending = [root]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            if "$ref" in value:
                key = value["$ref"].removeprefix("#/$defs/")
                if key not in dependencies:
                    dependencies[key] = deepcopy(_SHARED[key])
                    pending.append(dependencies[key])
            pending.extend(value.values())
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://ai-commerce.local/schema/{name}.v1", "title": name + ".v1",
        "description": (
            "A11 offline advisory evidence. Hashes detect local modification, not external attestation. "
            "All fields required except those omitted from required; nullable fields retain explicit unknowns. "
            "Content identity is the prefixed SHA256 of canonical JSON without its own ID field."
        ),
        **root, "$defs": {key: dependencies[key] for key in sorted(dependencies)},
    }
