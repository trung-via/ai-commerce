"""Portable A09 v1 schemas. Published JSON mirrors are checked by the tests.

Keeping the runtime schemas in Python makes installed wheels self-contained
without changing the project's packaging configuration. All references are local.
"""
from __future__ import annotations

from copy import deepcopy


def text(limit: int = 256) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": limit}


def nullable_text(limit: int = 256) -> dict:
    return {**text(limit), "type": ["string", "null"]}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties) if required is None else required}


VERIFICATION = "USER_SUPPLIED_NOT_LIVE_VERIFIED"
PLATFORMS = ["TIKTOK", "YOUTUBE", "FACEBOOK", "INSTAGRAM"]
SURFACES = ["VIDEO", "PROFILE_VIDEO", "SHORT", "PAGE_REEL", "REEL"]
ROW_FIELDS = ("provider_post_id", "public_url", "metric_key", "provider_name",
              "definition_version", "status", "value")
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
IMPORT_ID = {"type": "string", "pattern": "^a09-[a-f0-9]{64}$"}
KEY = {**text(128), "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$"}
WINDOW = {"type": "array", "minItems": 2, "maxItems": 2,
          "items": {"type": "string", "format": "date-time", "maxLength": 64},
          "description": "Explicit instants: start inclusive, end exclusive; never a default period."}
ACCOUNT = obj({"namespace": {"enum": ["internal_alias", "tiktok_open_id", "youtube_channel_id",
                                      "facebook_page_id", "instagram_user_id"]}, "id": text()})
CONTEXT = {"platform": {"enum": PLATFORMS}, "surface": {"enum": SURFACES},
           "account_ref": ACCOUNT, "measurement_window": WINDOW, "timezone": text(128),
           "cohort": nullable_text()}
TARGET_RULES = [
    {"if": {"properties": {"platform": {"const": platform}}},
     "then": {"properties": {"account_ref": {"properties": {"namespace": {"enum": [namespace, "internal_alias"]}}},
                             "surface": {"enum": surfaces}}}}
    for platform, namespace, surfaces in (
        ("TIKTOK", "tiktok_open_id", ["VIDEO", "PROFILE_VIDEO"]),
        ("YOUTUBE", "youtube_channel_id", ["VIDEO", "SHORT"]),
        ("FACEBOOK", "facebook_page_id", ["PAGE_REEL"]),
        ("INSTAGRAM", "instagram_user_id", ["REEL"]),
    )
]
MAPPING = obj({
    "profile_id": KEY, "mapping_version": text(64),
    "adapter_version": {"const": "manual-analytics.v1"},
    "platform": {"enum": PLATFORMS},
    "native_export_layout_verified": {"const": False},
    "columns": obj({name: text(128) for name in ROW_FIELDS}),
})
LINEAGE = obj({"relationship": {"enum": ["ORIGINAL", "CORRECTION", "REPROCESS"]},
               "parent_import_id": {"oneOf": [IMPORT_ID, {"type": "null"}]}})
LINEAGE["allOf"] = [{
    "if": {"properties": {"relationship": {"const": "ORIGINAL"}}},
    "then": {"properties": {"parent_import_id": {"type": "null"}}},
    "else": {"properties": {"parent_import_id": IMPORT_ID}},
}]

DEFINITION = obj({
    "schema_version": {"const": "MetricDefinition.v1"},
    "metric_key": KEY, "provider_name": text(), "definition_version": text(64),
    "platform": {"enum": PLATFORMS}, "unit": text(64), "definition": text(2048),
    "denominator": nullable_text(512), "cohort": nullable_text(),
    "comparison_policy": {"const": "NO_CROSS_PROVIDER_EQUIVALENCE"},
    "source_status": {"const": VERIFICATION},
})

# Retain every draft v0.2 field name, including its two-element window and
# attribution/economics metadata. v1 deliberately requires A08's typed identity.
PLAN = obj({
    "schema_version": {"const": "MeasurementPlan.v1"},
    "plan_id": text(), "campaign_ref": text(), "primary_metric": KEY,
    "denominator": nullable_text(512), "metric_definition_version": text(64),
    **CONTEXT,
    "source_mode": {"enum": ["MANUAL_IMPORT", "OFFICIAL_API", "AUTHORIZED_EXPORT",
                              "LICENSED_FEED", "NOT_AVAILABLE"]},
    "attribution_tier": {"enum": ["A", "B", "C", "D", "NOT_AVAILABLE"]},
    "missingness_rule": text(1024),
    "settlement_lag_days": {"type": ["number", "null"], "minimum": 0, "maximum": 1e6},
    "cost_scope": nullable_text(512),
    "review_at": {"type": ["string", "null"], "format": "date-time", "maxLength": 64},
    "evidence_refs": {"type": "array", "maxItems": 100, "items": text(1024)},
    "source_status": {"const": VERIFICATION},
    "metric_definitions": {"type": "array", "minItems": 1, "maxItems": 100,
                           "items": {"$ref": "#/$defs/MetricDefinition"}},
})
PLAN["required"] = [name for name in PLAN["properties"] if name not in {
    "settlement_lag_days", "cost_scope", "review_at", "evidence_refs"}]
PLAN["allOf"] = deepcopy(TARGET_RULES)

ROW = obj({"provider_post_id": nullable_text(), "public_url": nullable_text(2048),
           "metric_key": KEY, "provider_name": text(), "definition_version": text(64),
           "status": {"enum": ["AVAILABLE", "NOT_AVAILABLE"]},
           "value": {"type": ["number", "null"], "minimum": -1e100, "maximum": 1e100}})
ROW["allOf"] = [{
    "if": {"properties": {"status": {"const": "AVAILABLE"}}},
    "then": {"properties": {"value": {"type": "number"}}},
    "else": {"properties": {"value": {"type": "null"}}},
}]
SOURCE = obj({
    "raw_sha256": SHA, "source_row": {"type": "integer", "minimum": 1, "maximum": 10001},
    "source_row_semantics": {"enum": ["CSV_LOGICAL_RECORD_INCLUDING_HEADER", "JSON_ROWS_ONE_BASED"]},
    "profile_id": KEY, "mapping_version": text(64),
    "adapter_version": {"const": "manual-analytics.v1"},
    "source_kind": {"enum": ["USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT", "SYNTHETIC_FIXTURE"]},
    "source_status": {"const": VERIFICATION},
})
OBSERVATION = obj({
    "schema_version": {"const": "PerformanceObservation.v1"},
    "observation_id": {"type": "string", "pattern": "^obs-[a-f0-9]{64}$"},
    "import_id": IMPORT_ID, **CONTEXT,
    "provider_post_id": nullable_text(), "public_url": nullable_text(2048),
    "metric": {"$ref": "#/$defs/MetricDefinition"},
    "status": ROW["properties"]["status"], "value": ROW["properties"]["value"],
    "source": SOURCE, "source_status": {"const": VERIFICATION},
})
OBSERVATION["allOf"] = deepcopy(ROW["allOf"] + TARGET_RULES)

IMPORT = obj({
    "schema_version": {"const": "AnalyticsImport.v1"}, "import_id": IMPORT_ID,
    "status": {"enum": ["VALIDATED", "IMPORTED"]},
    "raw_sha256": SHA, "raw_ref": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
    "raw_size_bytes": {"type": "integer", "minimum": 1, "maximum": 8 * 1024 * 1024},
    "format": {"enum": ["csv", "json"]},
    "dialect": {"enum": ["comma", "semicolon", "tab", None]},
    "plan": {"$ref": "#/$defs/MeasurementPlan"}, "mapping": MAPPING,
    "source_kind": SOURCE["properties"]["source_kind"],
    "source_status": {"const": VERIFICATION},
    "context_sha256": SHA, "scope_sha256": SHA, "lineage": LINEAGE,
    "observation_count": {"type": "integer", "minimum": 1, "maximum": 10000},
})
IMPORT["allOf"] = [{
    "if": {"properties": {"format": {"const": "json"}}},
    "then": {"properties": {"dialect": {"type": "null"}}},
    "else": {"properties": {"dialect": {"enum": ["comma", "semicolon", "tab"]}}},
}]

DEFINITIONS = {"MetricDefinition": DEFINITION, "MeasurementPlan": PLAN,
               "PerformanceObservation": OBSERVATION, "AnalyticsImport": IMPORT}


def schema(name: str) -> dict:
    """Return an isolated schema; no remote resolution or schema file lookup."""
    dependencies = {"MetricDefinition": {}, "MeasurementPlan": {"MetricDefinition": DEFINITION},
                    "PerformanceObservation": {"MetricDefinition": DEFINITION},
                    "AnalyticsImport": {"MeasurementPlan": PLAN, "MetricDefinition": DEFINITION}}
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://ai-commerce.local/schema/{name}.v1",
        "title": f"{name}.v1",
        "description": "A09 offline manual analytics. User-supplied; never live-provider verification.",
        **DEFINITIONS[name], "$defs": dependencies[name],
    })
