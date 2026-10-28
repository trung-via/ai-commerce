"""Validate portable A02 contracts (JSON Schema 2020-12)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMAS = frozenset({"ContentBrief", "EvidenceRef", "SourceSnapshot", "MediaArtifact", "JobResult"})


# JSON Schema's ``format`` keyword is only asserted when a matching checker
# is installed. jsonschema's built-in date-time checker depends on an optional
# RFC 3339 validator, so a Windows installation may silently skip it.
# Register an explicit application-owned check to keep validation consistent.
_DATE_TIME_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"[Tt](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


def _valid_datetime(value: object) -> bool:
    """Validate timezone-aware RFC3339-style timestamps without optional extras."""
    if not isinstance(value, str):
        # The JSON Schema's ``type`` keyword checks non-string inputs.
        return True
    if not _DATE_TIME_RE.fullmatch(value):
        return False
    normalized = value[:10] + "T" + value[11:]
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _format_checker() -> FormatChecker:
    checker = FormatChecker()
    checker.checks("date-time")(_valid_datetime)
    return checker


class ContractValidationError(ValueError):
    """A supplied record does not meet an A02 contract."""


def load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMAS:
        raise ContractValidationError(f"UNKNOWN_CONTRACT: {name}")
    resource = files("ai_commerce.contracts").joinpath("schemas", f"{name}.v0.1.schema.json")
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def validate_record(name: str, record: Any) -> dict[str, Any]:
    """Return validated mapping; report validation path, not fabricated PASS."""
    if not isinstance(record, dict):
        raise ContractValidationError(f"INVALID_{name}: expected JSON object")
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema))
        for schema in (load_schema(item) for item in sorted(SCHEMAS))
    )
    validator = Draft202012Validator(load_schema(name), registry=registry, format_checker=_format_checker())
    errors = sorted(validator.iter_errors(record), key=lambda e: (list(map(str, e.path)), e.message))
    if errors:
        first = errors[0]
        pointer = "/".join(map(str, first.absolute_path)) or "$"
        raise ContractValidationError(f"INVALID_{name} at {pointer}: {first.message}")
    return record
