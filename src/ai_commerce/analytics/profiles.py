"""Provider metadata for explicitly user-authored long-form analytics rows.

These are application formats, NOT claims about native provider CSV exports.
Metric definitions and provider labels always come from the supplied plan.
"""
from .model import require, validate_mapping
from .schemas import PLATFORMS, ROW_FIELDS

PROFILE_IDS = tuple(platform.lower() + ".manual.v1" for platform in PLATFORMS)


def mapping_profile(profile_id: str) -> dict:
    require(profile_id in PROFILE_IDS, "UNKNOWN_MAPPING_PROFILE")
    return validate_mapping({
        "profile_id": profile_id, "mapping_version": "1",
        "adapter_version": "manual-analytics.v1", "platform": profile_id.split(".")[0].upper(),
        "native_export_layout_verified": False, "columns": {name: name for name in ROW_FIELDS},
    })
