"""Configurable column mapping profiles for human-supplied trend CSVs.

These are application mappings, NOT claims about native provider CSV exports.
Provider exports are user-supplied declarations; layout is never provider-verified.
"""
from __future__ import annotations

from copy import deepcopy

from ..core.models import require

SIGNAL_ROW_FIELDS = (
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
    "denominator",
    "status",
    "value",
    "rights_status",
)

REQUIRED_ROW_FIELDS = ("topic", "observed_at", "status", "value")

DEFAULT_COLUMNS = {name: name for name in SIGNAL_ROW_FIELDS}

GOOGLE_TRENDS_COLUMNS = {
    "topic": "Query",
    "signal_type": "Type",
    "source_type": "Source",
    "locale": "Locale",
    "geo": "Geo",
    "query_context": "Category",
    "observed_at": "Time",
    "collected_at": "Collected",
    "timezone": "Timezone",
    "unit": "Unit",
    "denominator": "Denominator",
    "status": "Status",
    "value": "Interest",
    "rights_status": "Rights",
}

BUILTIN_PROFILES = {
    "default.trend.v1": DEFAULT_COLUMNS,
    "google_trends.manual.v1": GOOGLE_TRENDS_COLUMNS,
}


def validate_mapping(mapping: dict) -> dict:
    require(isinstance(mapping, dict), "INVALID_MAPPING")
    require(isinstance(mapping.get("profile_id"), str) and bool(mapping["profile_id"].strip()), "INVALID_MAPPING_PROFILE_ID")
    require(mapping.get("mapping_version") in ("1", 1, "v1"), "INVALID_MAPPING_VERSION")
    require(mapping.get("adapter_version") == "manual-signal.v1", "INVALID_ADAPTER_VERSION")
    require(mapping.get("native_export_layout_verified") is False, "NATIVE_EXPORT_UNVERIFIED_REQUIRED")
    columns = mapping.get("columns")
    require(isinstance(columns, dict) and bool(columns), "INVALID_MAPPING_COLUMNS")
    for req in REQUIRED_ROW_FIELDS:
        require(req in columns and isinstance(columns[req], str) and bool(columns[req].strip()), f"MAPPING_MISSING_REQUIRED_FIELD_{req.upper()}")
    header_names = list(columns.values())
    require(len(header_names) == len(set(header_names)), "AMBIGUOUS_CSV_HEADER_MAPPING")
    for field, header in columns.items():
        require(isinstance(field, str) and bool(field.strip()), "INVALID_MAPPING_FIELD_NAME")
        require(isinstance(header, str) and bool(header.strip()), "INVALID_MAPPING_HEADER_NAME")
    return mapping


def mapping_profile(profile_id: str | dict) -> dict:
    if isinstance(profile_id, dict):
        return validate_mapping(profile_id)
    require(profile_id in BUILTIN_PROFILES, "UNKNOWN_MAPPING_PROFILE")
    return validate_mapping({
        "profile_id": profile_id,
        "mapping_version": "1",
        "adapter_version": "manual-signal.v1",
        "native_export_layout_verified": False,
        "columns": deepcopy(BUILTIN_PROFILES[profile_id]),
    })
