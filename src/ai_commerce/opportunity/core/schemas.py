"""Portable A10 v1 schemas. Published JSON mirrors are checked by tests.

Keeping the runtime schemas in Python makes installed packages self-contained
without changing the project packaging configuration. All references are local.
"""
from __future__ import annotations

from copy import deepcopy


def text(limit: int = 256) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": limit}


def nullable_text(limit: int = 256) -> dict:
    return {**text(limit), "type": ["string", "null"]}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


RIGHTS_STATUSES = [
    "UNKNOWN",
    "USER_DECLARED_PERMITTED",
    "USER_DECLARED_RESTRICTED",
    "NOT_APPLICABLE",
]
SOURCE_STATUS = "USER_SUPPLIED_NOT_LIVE_VERIFIED"
SIGNAL_STATUSES = ["AVAILABLE", "NOT_AVAILABLE"]

SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
IMPORT_ID = {"type": "string", "pattern": "^a10-[a-f0-9]{64}$"}
SIGNAL_ID = {"type": "string", "pattern": "^sig-[a-f0-9]{64}$"}
KEY = {**text(128), "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$"}
WINDOW = {
    "type": "array",
    "minItems": 2,
    "maxItems": 2,
    "items": {"type": "string", "format": "date-time", "maxLength": 64},
    "description": "Explicit instants: start inclusive, end exclusive; never a default period.",
}

SIGNAL_SOURCE_SNAPSHOT = obj(
    {
        "schema_version": {"const": "SignalSourceSnapshot.v1"},
        "source_id": KEY,
        "source_kind": text(128),
        "source_ref": text(1024),
        "captured_at": {"type": "string", "format": "date-time", "maxLength": 64},
        "collected_at": {"type": ["string", "null"], "format": "date-time", "maxLength": 64},
        "sha256": SHA,
        "rights_status": {"enum": RIGHTS_STATUSES},
        "verification_status": {"const": SOURCE_STATUS},
        "jurisdiction": nullable_text(64),
        "geo": nullable_text(64),
        "locale": nullable_text(64),
        "purpose": nullable_text(512),
        "terms_url": nullable_text(2048),
        "notes": nullable_text(2048),
    },
    required=[
        "schema_version",
        "source_id",
        "source_kind",
        "source_ref",
        "captured_at",
        "sha256",
        "rights_status",
        "verification_status",
    ],
)

TREND_SIGNAL = obj(
    {
        "schema_version": {"const": "TrendSignal.v1"},
        "signal_id": SIGNAL_ID,
        "import_id": IMPORT_ID,
        "ordinal": {"type": "integer", "minimum": 1, "maximum": 10000},
        "topic": text(256),
        "signal_type": text(64),
        "source_type": text(64),
        "locale": text(64),
        "geo": text(64),
        "query_context": text(256),
        "observed_at": {"type": "string", "format": "date-time", "maxLength": 64},
        "observation_window": {"oneOf": [WINDOW, {"type": "null"}]},
        "collected_at": {"type": "string", "format": "date-time", "maxLength": 64},
        "timezone": text(128),
        "unit": text(64),
        "denominator": nullable_text(256),
        "status": {"enum": SIGNAL_STATUSES},
        "value": {"type": ["number", "null"]},
        "source_status": {"const": SOURCE_STATUS},
        "rights_status": {"enum": RIGHTS_STATUSES},
        "evidence_refs": {"type": "array", "maxItems": 100, "items": text(1024)},
    },
    required=[
        "schema_version",
        "signal_id",
        "import_id",
        "ordinal",
        "topic",
        "signal_type",
        "source_type",
        "locale",
        "geo",
        "query_context",
        "observed_at",
        "collected_at",
        "timezone",
        "unit",
        "status",
        "value",
        "source_status",
        "rights_status",
    ],
)
TREND_SIGNAL["allOf"] = [
    {
        "if": {"properties": {"status": {"const": "NOT_AVAILABLE"}}},
        "then": {"properties": {"value": {"type": "null"}}},
        "else": {"properties": {"value": {"type": "number"}}},
    }
]

LINEAGE = obj(
    {
        "relationship": {"enum": ["ORIGINAL", "CORRECTION", "REPROCESS"]},
        "parent_import_id": {"oneOf": [IMPORT_ID, {"type": "null"}]},
    }
)
LINEAGE["allOf"] = [
    {
        "if": {"properties": {"relationship": {"const": "ORIGINAL"}}},
        "then": {"properties": {"parent_import_id": {"type": "null"}}},
        "else": {"properties": {"parent_import_id": IMPORT_ID}},
    }
]

MAPPING = obj(
    {
        "profile_id": KEY,
        "mapping_version": text(64),
        "adapter_version": {"const": "manual-signal.v1"},
        "native_export_layout_verified": {"const": False},
        "columns": {"type": "object", "additionalProperties": text(128)},
    }
)

SIGNAL_IMPORT = obj(
    {
        "schema_version": {"const": "SignalImport.v1"},
        "import_id": IMPORT_ID,
        "status": {"enum": ["VALIDATED", "IMPORTED"]},
        "raw_sha256": SHA,
        "raw_ref": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
        "raw_size_bytes": {"type": "integer", "minimum": 1, "maximum": 8 * 1024 * 1024},
        "format": {"enum": ["csv", "json"]},
        "dialect": {"enum": ["comma", "semicolon", "tab", None]},
        "source_snapshot": {"$ref": "#/$defs/SignalSourceSnapshot"},
        "mapping": {"oneOf": [MAPPING, {"type": "null"}]},
        "context_sha256": SHA,
        "scope_sha256": SHA,
        "lineage": {"$ref": "#/$defs/SignalLineage"},
        "signal_count": {"type": "integer", "minimum": 1, "maximum": 10000},
        "source_status": {"const": SOURCE_STATUS},
        "recorded_at": {"type": "string", "format": "date-time", "maxLength": 64},
    },
    required=[
        "schema_version",
        "import_id",
        "status",
        "raw_sha256",
        "raw_ref",
        "raw_size_bytes",
        "format",
        "source_snapshot",
        "context_sha256",
        "scope_sha256",
        "lineage",
        "signal_count",
        "source_status",
        "recorded_at",
    ],
)
SIGNAL_IMPORT["allOf"] = [
    {
        "if": {"properties": {"format": {"const": "json"}}},
        "then": {"properties": {"dialect": {"type": "null"}}},
        "else": {"properties": {"dialect": {"enum": ["comma", "semicolon", "tab"]}}},
    }
]

DEFINITIONS = {
    "SignalSourceSnapshot": SIGNAL_SOURCE_SNAPSHOT,
    "TrendSignal": TREND_SIGNAL,
    "SignalImport": SIGNAL_IMPORT,
}


def schema(name: str) -> dict:
    """Return an isolated schema with draft 2020-12 declaration; no remote resolution."""
    defs: dict[str, dict] = {}
    if name == "SignalImport":
        defs = {
            "SignalSourceSnapshot": deepcopy(SIGNAL_SOURCE_SNAPSHOT),
            "SignalLineage": deepcopy(LINEAGE),
            "Mapping": deepcopy(MAPPING),
        }
    return deepcopy(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://ai-commerce.local/schema/{name}.v1",
            "title": f"{name}.v1",
            "description": f"A10 offline {name} contract. User-supplied; never live-provider verification.",
            **DEFINITIONS[name],
            "$defs": defs,
        }
    )
