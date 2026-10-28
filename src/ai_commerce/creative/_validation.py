"""Small strict JSON boundary shared by A03's public APIs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ai_commerce.core.provenance import canonical_bytes


class CreativeValidationError(ValueError):
    """Invalid creative input or serialized record."""


class VersionConflict(FileExistsError):
    """A stored identity/revision already contains different content."""


class NoReasonerConfigured(RuntimeError):
    """No product creative reasoner was explicitly supplied."""


def require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CreativeValidationError(f"{field}: expected nonblank text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise CreativeValidationError(f"{field}: invalid Unicode") from exc
    return value


def positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise CreativeValidationError(f"{field}: expected a positive integer")
    return value


def text_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise CreativeValidationError(f"{field}: expected a list/tuple of text")
    return tuple(require_text(item, field) for item in value)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise CreativeValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise CreativeValidationError(f"nonfinite JSON number: {value}")


def read_json(text: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise CreativeValidationError("INVALID_JSON: expected strict JSON") from exc


def json_object(value: object) -> dict[str, Any]:
    """Detach caller data and reject values that cannot be losslessly JSON encoded."""
    if not isinstance(value, Mapping):
        raise CreativeValidationError("expected a JSON object")
    def check(item: Any) -> None:
        if item is None or type(item) in (str, int, float, bool):
            return
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise CreativeValidationError("JSON object keys must be strings")
            for nested in item.values():
                check(nested)
        elif isinstance(item, list):
            for nested in item:
                check(nested)
        else:
            raise CreativeValidationError("expected JSON values (no tuples or custom objects)")
    try:
        data = dict(value)
        check(data)
        return read_json(canonical_bytes(data).decode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise CreativeValidationError("expected finite UTF-8 JSON data") from exc


def exact_fields(data: object, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    result = json_object(data)
    if not required <= result.keys() or result.keys() - required - (optional or set()):
        raise CreativeValidationError("missing or unexpected record fields")
    return result
