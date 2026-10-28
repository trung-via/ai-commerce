"""Validate portable A02 contracts (JSON Schema 2020-12)."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMAS = frozenset({"ContentBrief", "EvidenceRef", "SourceSnapshot", "MediaArtifact", "JobResult"})


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
    validator = Draft202012Validator(load_schema(name), registry=registry, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(record), key=lambda e: (list(map(str, e.path)), e.message))
    if errors:
        first = errors[0]
        pointer = "/".join(map(str, first.absolute_path)) or "$"
        raise ContractValidationError(f"INVALID_{name} at {pointer}: {first.message}")
    return record
