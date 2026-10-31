"""Self-contained, offline MediaReleaseInspection.v1 contract."""

from __future__ import annotations

from copy import deepcopy


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$", "minLength": 64, "maxLength": 64}
TIME = {"type": "string", "pattern": "^-?[0-9]+(?:/[1-9][0-9]*)?$", "maxLength": 80}
POSITIVE = {"type": "integer", "minimum": 1}
CODES = {"type": "array", "items": {"type": "string", "pattern": "^[A-Z0-9_]+$"},
         "uniqueItems": True, "maxItems": 8}
SEGMENT = _object({"stream_index": {"type": "integer", "minimum": 0},
                   "start_seconds": TIME, "end_seconds": TIME, "duration_seconds": TIME})
STREAM = _object({
    "index": {"type": "integer", "minimum": 0}, "kind": {"enum": ["video", "audio"]},
    "codec": {"type": "string", "minLength": 1, "maxLength": 80},
    "width": _nullable(POSITIVE), "height": _nullable(POSITIVE),
    "fps": _nullable(TIME), "duration_seconds": _nullable(TIME),
    "decoded_frames": _nullable(POSITIVE), "sample_rate": _nullable(POSITIVE),
    "channels": _nullable(POSITIVE),
})
FACTS = _object({"container": {"const": "mp4"},
                 "format_names": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 16},
                 "duration_seconds": TIME,
                 "streams": {"type": "array", "items": STREAM, "minItems": 1, "maxItems": 16}})


def _diagnostic(kind):
    params = (_object({"minimum_duration_seconds": {"const": "1/10"},
                       "pixel_threshold": {"const": "1/10"}, "picture_black_ratio": {"const": "49/50"}})
              if kind == "black" else
              _object({"minimum_duration_seconds": {"const": "1/10"}, "noise_db": {"const": -50}}))
    return _object({"status": {"enum": ["NOT_REQUESTED", "NOT_APPLICABLE", "OBSERVED", "UNKNOWN"]},
                    "parameters": params, "segments": {"type": "array", "items": SEGMENT, "maxItems": 4096},
                    "reason_codes": CODES})


SOURCE = {"oneOf": [
    _object({"kind": {"const": "LOCAL_EXPECTED_HASH"}, "custody_verification": {"const": "NOT_PERFORMED"}}),
    _object({"kind": {"const": "A06_E01_MANIFEST_HASH_BOUND"},
             "manifest_ref": {"type": "string", "minLength": 1, "maxLength": 512},
             "manifest_sha256": SHA, "record_sha256": SHA,
             "run_id": {"type": "string", "pattern": "^e01-[0-9a-f]{32}$"},
             "custody_verification": {"const": "NOT_PERFORMED"}}),
]}
ASSET = _object({
    "asset_ref": {"type": "string", "minLength": 1, "maxLength": 512},
    "expected_sha256": SHA, "mime_type": {"type": "string", "maxLength": 128},
    "observed_sha256": _nullable(SHA), "size_bytes": _nullable({"type": "integer", "minimum": 0}),
    "status": {"enum": ["PASS", "FAIL", "UNKNOWN"]}, "reason_codes": CODES,
    "full_decode": {"type": "boolean"}, "facts": _nullable(FACTS),
    "diagnostics": _object({"black": _diagnostic("black"), "silence": _diagnostic("silence")}),
    "source": SOURCE,
})


def media_inspection_schema() -> dict:
    """A detached schema; no external $ref or registry/network resolution."""
    result = _object({
        "schema_version": {"const": "MediaReleaseInspection.v1"},
        "inspector_version": {"const": "a13-c08/1.0"},
        "scope": {"const": "COMMERCIAL_PLACEMENT_TECHNICAL_INSPECTION_ONLY"},
        "status": {"enum": ["PASS", "FAIL", "UNKNOWN"]},
        "limits": _object({
            "max_asset_bytes": {"type": "integer", "minimum": 1, "maximum": 1024 ** 3},
            "max_total_bytes": {"type": "integer", "minimum": 1, "maximum": 4 * 1024 ** 3},
            "max_assets": {"type": "integer", "minimum": 1, "maximum": 64},
            "tool_timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 60},
            "max_tool_output_bytes": {"type": "integer", "minimum": 1024, "maximum": 1024 ** 2},
        }),
        "diagnostics_requested": _object({"black": {"type": "boolean"}, "silence": {"type": "boolean"}}),
        "tools": _object({"ffprobe": _nullable({"type": "string", "minLength": 1, "maxLength": 1024}),
                          "ffmpeg": _nullable({"type": "string", "minLength": 1, "maxLength": 1024})}),
        "assets": {"type": "array", "items": ASSET, "minItems": 1, "maxItems": 64},
        "rights_status": {"const": "UNKNOWN"}, "account_eligibility": {"const": "UNKNOWN"},
        "link_activation": {"const": "UNKNOWN"}, "publication_status": {"const": "NOT_ATTEMPTED"},
        "record_sha256": SHA,
    })
    result.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
                   "$id": "urn:ai-commerce:MediaReleaseInspection:v1", "title": "MediaReleaseInspection.v1"})
    return deepcopy(result)
