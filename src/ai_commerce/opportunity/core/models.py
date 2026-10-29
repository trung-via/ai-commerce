"""A10 contract validation, exact identities and bounded error vocabulary."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator

from ai_commerce.contracts.schema import _format_checker
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from .schemas import DEFINITIONS, schema


class OpportunityError(ValueError):
    """Bounded reason and optional logical source row; never echoes input bytes."""

    def __init__(self, code: str, *, row: int | None = None):
        self.code = code
        self.row = row
        super().__init__(code + (f":row={row}" if row is not None else ""))


class SignalLedgerError(OpportunityError):
    """Storage, replay or tamper failure, separate from invalid manual input."""


class SignalValidationError(OpportunityError):
    """Validation failure of signal inputs, schemas or constraints."""


def require(ok: bool, code: str, *, row: int | None = None) -> None:
    if not ok:
        raise SignalValidationError(code, row=row)


def digest(record: dict) -> str:
    return sha256_bytes(canonical_bytes(record))


def parse_json(raw: bytes) -> dict:
    """Strictly parse JSON: reject duplicate keys, nonfinite floats, and malformed UTF-8."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise SignalValidationError("JSON_NONFINITE")

    try:
        obj = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=unique,
            parse_constant=nonfinite,
        )
        require(isinstance(obj, dict), "JSON_OBJECT_REQUIRED")
        canonical_bytes(obj)  # Reject overflowed floats and invalid Unicode
        return obj
    except OpportunityError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SignalValidationError("JSON_MALFORMED") from exc


@lru_cache(maxsize=4)
def _validator(name: str) -> Draft202012Validator:
    require(name in DEFINITIONS, "UNKNOWN_A10_CONTRACT")
    value = schema(name)
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value, format_checker=_format_checker())


def validate_contract(name: str, value: dict, *, row: int | None = None) -> dict:
    try:
        require(_validator(name).is_valid(value), "INVALID_" + name.upper(), row=row)
        canonical_bytes(value)
        return value
    except OpportunityError:
        raise
    except (ValueError, RecursionError, UnicodeError, TypeError, OverflowError) as exc:
        raise SignalValidationError("INVALID_CONTRACT_VALUE", row=row) from exc


def validate_timezone(value: str, *, row: int | None = None) -> None:
    require(isinstance(value, str) and bool(value.strip()), "INVALID_TIMEZONE", row=row)
    if value == "UTC":
        return
    match = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", value)
    if match:
        _, hours, minutes = match.groups()
        h, m = int(hours), int(minutes)
        require(m < 60 and (h < 14 or (h == 14 and m == 0)), "INVALID_TIMEZONE", row=row)
        return
    require(".." not in value and not value.startswith(("/", "\\")), "INVALID_TIMEZONE", row=row)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SignalValidationError("INVALID_TIMEZONE", row=row) from exc


def validate_window(window: list[str] | None, *, row: int | None = None) -> None:
    if window is None:
        return
    require(isinstance(window, list) and len(window) == 2, "INVALID_WINDOW", row=row)
    start_str, end_str = window
    require(isinstance(start_str, str) and isinstance(end_str, str), "INVALID_WINDOW", row=row)
    try:
        # Check datetime parsing
        t_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        t_end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        require(t_start.utcoffset() is not None and t_end.utcoffset() is not None, "INVALID_WINDOW", row=row)
        require(t_start <= t_end, "INVALID_WINDOW", row=row)
    except (ValueError, TypeError) as exc:
        raise SignalValidationError("INVALID_WINDOW", row=row) from exc


def scope(snapshot: dict) -> dict:
    """Return deterministic scope representation for lineage consistency."""
    return {
        "source_id": snapshot["source_id"],
        "geo": snapshot.get("geo"),
        "locale": snapshot.get("locale"),
    }


def validate_source_snapshot(snapshot: dict) -> dict:
    validate_contract("SignalSourceSnapshot", snapshot)
    if snapshot.get("default_timezone"):
        validate_timezone(snapshot["default_timezone"])
    return snapshot


def validate_signal(signal: dict, *, row: int | None = None) -> dict:
    validate_contract("TrendSignal", signal, row=row)
    validate_timezone(signal["timezone"], row=row)
    if signal.get("observation_window"):
        validate_window(signal["observation_window"], row=row)
    return signal


def validate_signal_import(record: dict) -> dict:
    return validate_contract("SignalImport", record)
