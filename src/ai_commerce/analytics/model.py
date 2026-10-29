"""A09 contract validation, exact identities and bounded error vocabulary."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator

from ai_commerce.contracts.schema import _format_checker
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.distribution.model import provider_profiles

from .schemas import CONTEXT, DEFINITIONS, MAPPING, VERIFICATION, schema


class AnalyticsError(ValueError):
    """Bounded reason and optional logical source row; never echoes input bytes."""

    def __init__(self, code: str, *, row: int | None = None):
        self.code = code
        self.row = row
        super().__init__(code + (f":row={row}" if row is not None else ""))


class LedgerError(AnalyticsError):
    """Storage or integrity failure, separate from invalid manual input."""


def require(ok: bool, code: str, *, row: int | None = None) -> None:
    if not ok:
        raise AnalyticsError(code, row=row)


def digest(record: dict) -> str:
    return sha256_bytes(canonical_bytes(record))


def parse_json(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise AnalyticsError("JSON_NONFINITE")

    try:
        obj = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
                         parse_constant=nonfinite)
        require(isinstance(obj, dict), "JSON_OBJECT_REQUIRED")
        canonical_bytes(obj)  # Reject overflowed floats and invalid Unicode.
        return obj
    except AnalyticsError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AnalyticsError("JSON_MALFORMED") from exc


@lru_cache(maxsize=4)
def _validator(name: str) -> Draft202012Validator:
    require(name in DEFINITIONS, "UNKNOWN_ANALYTICS_CONTRACT")
    value = schema(name)
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value, format_checker=_format_checker())


def validate_contract(name: str, value: dict) -> dict:
    try:
        require(_validator(name).is_valid(value), "INVALID_" + name.upper())
        canonical_bytes(value)
        return value
    except AnalyticsError:
        raise
    except (ValueError, RecursionError, UnicodeError, TypeError, OverflowError) as exc:
        raise AnalyticsError("INVALID_CONTRACT_VALUE") from exc


def validate_target(plan: dict) -> None:
    platform = plan["platform"]
    ns = {"TIKTOK": "tiktok_open_id", "YOUTUBE": "youtube_channel_id",
          "FACEBOOK": "facebook_page_id", "INSTAGRAM": "instagram_user_id"}
    require(plan["account_ref"]["namespace"] in {ns[platform], "internal_alias"},
            "ACCOUNT_NAMESPACE_PLATFORM_MISMATCH")
    require(bool(plan["account_ref"]["id"].strip()), "ACCOUNT_ID_EMPTY")
    require(plan["surface"] in provider_profiles()["platforms"][platform]["surfaces"],
            "SURFACE_PLATFORM_MISMATCH")


def validate_timezone(value: str) -> None:
    if value == "UTC":
        return
    match = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", value)
    if match:
        hours, minutes = int(match[2]), int(match[3])
        require(hours <= 14 and minutes < 60 and (hours < 14 or minutes == 0), "INVALID_TIMEZONE")
        timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if match[1] == "+" else -1)))
        return
    require(not value.startswith(("/", "\\")) and ".." not in value.split("/"), "INVALID_TIMEZONE")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AnalyticsError("TIMEZONE_UNKNOWN_OR_DATA_UNAVAILABLE") from exc


def validate_plan(value: dict) -> dict:
    validate_contract("MeasurementPlan", value)
    validate_target(value)
    validate_timezone(value["timezone"])
    start, end = (datetime.fromisoformat(v.upper().replace("Z", "+00:00"))
                  for v in value["measurement_window"])
    require(start < end, "INVALID_MEASUREMENT_WINDOW")
    seen = set()
    primary = []
    for metric in value["metric_definitions"]:
        key = metric_identity(metric)
        require(key not in seen, "AMBIGUOUS_METRIC_DEFINITION")
        seen.add(key)
        require(metric["platform"] == value["platform"], "METRIC_PLATFORM_MISMATCH")
        require(metric["cohort"] == value["cohort"], "METRIC_COHORT_MISMATCH")
        if metric["metric_key"] == value["primary_metric"] and metric["definition_version"] == value["metric_definition_version"]:
            primary.append(metric)
    require(len(primary) == 1, "PRIMARY_METRIC_AMBIGUOUS_OR_MISSING")
    require(primary[0]["denominator"] == value["denominator"], "PRIMARY_DENOMINATOR_MISMATCH")
    return deepcopy(value)


def metric_identity(value: dict) -> tuple[str, str, str]:
    return value["metric_key"], value["provider_name"], value["definition_version"]


def validate_mapping(value: dict) -> dict:
    require(Draft202012Validator(MAPPING).is_valid(value), "INVALID_MAPPING_PROFILE")
    headers = list(value["columns"].values())
    require(len(headers) == len(set(headers)), "AMBIGUOUS_MAPPING_HEADER")
    require(all(v.strip() == v and not any(c in v for c in "\r\n\x00") for v in headers),
            "INVALID_MAPPING_HEADER")
    return deepcopy(value)


def scope(plan: dict) -> dict:
    return {name: deepcopy(plan[name]) for name in CONTEXT}


def checked_url(value: str | None, *, row: int) -> None:
    if value is None:
        return
    try:
        parsed = urlsplit(value)
        ok = (parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
              and not parsed.password and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))
        parsed.port  # Force validation of an invalid port.
    except ValueError:
        ok = False
    require(ok, "INVALID_POST_URL", row=row)
