"""Strict boundaries for the provider-neutral composition format."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from ai_commerce.core.provenance import canonical_bytes


class CompositionValidationError(ValueError):
    """Malformed composition, reference or revision lineage."""


class CompositionVersionConflict(FileExistsError):
    """An immutable composition revision already has different bytes."""


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompositionValidationError(f"{name}: expected nonblank text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise CompositionValidationError(f"{name}: invalid Unicode") from exc
    return value


def integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise CompositionValidationError(f"{name}: expected integer >= {minimum}")
    return value


def digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise CompositionValidationError("expected lowercase SHA-256 digest")
    return value


def identity(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix + ":"):
        raise CompositionValidationError(f"invalid {prefix} identity")
    digest(value[len(prefix) + 1:])
    return value


def identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise CompositionValidationError(f"invalid {name}")
    return value


def read_json(raw: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise CompositionValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise CompositionValidationError(f"nonfinite JSON number: {value}")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        canonical_bytes(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise CompositionValidationError("expected strict finite UTF-8 JSON") from exc


def record(value: object, required: set[str], optional: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompositionValidationError("expected a JSON object")
    if not required <= value.keys() or value.keys() - required - optional:
        raise CompositionValidationError("missing or unexpected record fields")
    return dict(value)


def typed_tuple(value: object, cls: type, name: str) -> tuple:
    if not isinstance(value, (tuple, list)) or any(not isinstance(v, cls) for v in value):
        raise CompositionValidationError(f"{name}: expected typed {cls.__name__} items")
    return tuple(value)
