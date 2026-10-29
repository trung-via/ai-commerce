"""Bounded A11 validation and deterministic immutable reference identities.

Schema validation is structural. Snapshot validation additionally checks A09
identities, definitions and provenance; report calculations and registry lineage
resolution belong to A11-02/03, not to this foundation.
"""
from __future__ import annotations

import math
from functools import lru_cache

from jsonschema import Draft202012Validator
from referencing import Registry

from ai_commerce.analytics.model import AnalyticsError, parse_json
from ai_commerce.contracts.schema import _format_checker
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from .schemas import CONTRACT_IDS, schema

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 2000000


class LearningError(ValueError):
    """A bounded code; never includes user source bytes or paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(ok: bool, code: str) -> None:
    if not ok:
        raise LearningError(code)


def bounded_bytes(value: dict) -> bytes:
    """Bound depth, nodes and text before serialization or schema evaluation."""
    pending = [(value, 0)]
    nodes = size = 0
    try:
        while pending:
            current, depth = pending.pop()
            nodes += 1
            require(depth <= MAX_JSON_DEPTH and nodes <= MAX_JSON_NODES, "DOCUMENT_COMPLEXITY_LIMIT")
            if type(current) is dict:
                require(len(current) <= 256, "DOCUMENT_COMPLEXITY_LIMIT")
                require(all(type(key) is str for key in current), "INVALID_JSON_VALUE")
                pending.extend((item, depth + 1) for pair in current.items() for item in pair)
            elif type(current) is list:
                require(len(current) <= 10000, "DOCUMENT_COMPLEXITY_LIMIT")
                pending.extend((item, depth + 1) for item in current)
            elif type(current) is str:
                require(len(current) <= 4096, "DOCUMENT_TEXT_LIMIT")
                size += len(current.encode("utf-8"))
            elif type(current) is float:
                require(math.isfinite(current), "JSON_NONFINITE")
                size += 32
            elif type(current) is int:
                require(current.bit_length() <= 1024, "INVALID_JSON_VALUE")
                size += 320
            elif current is None or type(current) is bool:
                size += 5
            else:
                raise LearningError("INVALID_JSON_VALUE")
            require(size <= MAX_DOCUMENT_BYTES, "DOCUMENT_TOO_LARGE")
        raw = canonical_bytes(value)
        require(len(raw) <= MAX_DOCUMENT_BYTES, "DOCUMENT_TOO_LARGE")
        return raw
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        if isinstance(exc, LearningError):
            raise
        raise LearningError("INVALID_JSON_VALUE") from exc


def digest(value: dict) -> str:
    return sha256_bytes(bounded_bytes(value))


def _identity(name: str) -> tuple[str, str]:
    require(type(name) is str and name in CONTRACT_IDS, "UNKNOWN_LEARNING_CONTRACT")
    return CONTRACT_IDS[name]


def content_id(name: str, value: dict) -> str:
    """Identity covers every field except the contract's own content ID."""
    field, prefix = _identity(name)
    require(type(value) is dict, "JSON_OBJECT_REQUIRED")
    return prefix + digest({key: item for key, item in value.items() if key != field})


@lru_cache(maxsize=7)
def _validator(name: str) -> Draft202012Validator:
    definition = schema(name)
    Draft202012Validator.check_schema(definition)
    return Draft202012Validator(definition, registry=Registry(), format_checker=_format_checker())


def validate_contract(name: str, value: dict) -> dict:
    """Return a detached validated value; identity checks do not attest truth.

    For reports/learning this verifies shape and local content identity only.
    Resolving evidence references and calculations requires their owning runtime.
    """
    field, _ = _identity(name)
    require(type(value) is dict, "JSON_OBJECT_REQUIRED")
    raw = bounded_bytes(value)
    try:
        require(_validator(name).is_valid(value), "INVALID_" + name.upper())
        require(value[field] == content_id(name, value), "CONTENT_ID_MISMATCH")
        if name == "ObservationSnapshot":
            from .reader import _validate_snapshot
            _validate_snapshot(value)
        return parse_json(raw)
    except LearningError:
        raise
    except AnalyticsError as exc:
        raise LearningError("A09_VALIDATION_FAILED") from exc
    except (ValueError, TypeError, UnicodeError, KeyError, OverflowError, RecursionError) as exc:
        raise LearningError("INVALID_CONTRACT_VALUE") from exc


def seal_contract(name: str, body: dict) -> dict:
    """Add a deterministic ID to an explicitly versioned body, then validate.

    No persistence, reports, inference or registry operations are performed.
    """
    field, _ = _identity(name)
    require(type(body) is dict and field not in body, "CONTENT_ID_ALREADY_PRESENT_OR_INVALID_BODY")
    return validate_contract(name, {**body, field: content_id(name, body)})


def parse_contract(name: str, raw: bytes) -> dict:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_DOCUMENT_BYTES, "DOCUMENT_EMPTY_OR_TOO_LARGE")
    try:
        return validate_contract(name, parse_json(raw))
    except AnalyticsError as exc:
        raise LearningError("INVALID_CONTRACT_JSON") from exc
