"""Bounded JSON, safe timestamps and component-owned contract validation."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from functools import lru_cache

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from ai_commerce.core.provenance import canonical_bytes as _canonical_bytes, sha256_bytes

from .schemas import (CONTRACT_IDS, MAX_DEPTH, MAX_DOCUMENT_BYTES, MAX_FIELD_CHARS,
                      MAX_NODES, TIMESTAMP_PATTERN, schema)


class ProductError(ValueError):
    """Stable reason code and optional logical row; never echoes source data."""

    def __init__(self, code: str, *, row: int | None = None):
        self.code, self.row = code, row
        super().__init__(code + (f":row={row}" if row is not None else ""))


def require(ok: bool, code: str, *, row: int | None = None) -> None:
    if not ok:
        raise ProductError(code, row=row)


def canonical_bytes(value) -> bytes:
    """Reject cycles, excess complexity, nonfinite numbers and non-JSON types."""
    pending = [(value, 0)]
    nodes = size = 0
    try:
        while pending:
            current, depth = pending.pop()
            nodes += 1
            require(depth <= MAX_DEPTH and nodes <= MAX_NODES, "DOCUMENT_COMPLEXITY_LIMIT")
            if type(current) is dict:
                require(len(current) <= 256 and all(type(key) is str for key in current),
                        "INVALID_JSON_OBJECT")
                pending.extend((item, depth + 1) for pair in current.items() for item in pair)
            elif type(current) is list:
                require(len(current) <= 10000, "DOCUMENT_COMPLEXITY_LIMIT")
                pending.extend((item, depth + 1) for item in current)
            elif type(current) is str:
                require(len(current) <= MAX_FIELD_CHARS, "DOCUMENT_TEXT_LIMIT")
                size += len(current.encode("utf-8"))
            elif type(current) is float:
                require(math.isfinite(current), "JSON_NONFINITE")
                size += 32
            elif type(current) is int:
                require(current.bit_length() <= 256, "JSON_NUMBER_LIMIT")
                size += 80
            elif current is None or type(current) is bool:
                size += 5
            else:
                raise ProductError("INVALID_JSON_VALUE")
            require(size <= MAX_DOCUMENT_BYTES, "DOCUMENT_TOO_LARGE")
        result = _canonical_bytes(value)
        require(len(result) <= MAX_DOCUMENT_BYTES, "DOCUMENT_TOO_LARGE")
        return result
    except ProductError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ProductError("INVALID_JSON_VALUE") from exc


def digest(value) -> str:
    return sha256_bytes(canonical_bytes(value))


def parse_json(raw: bytes, *, limit: int = MAX_DOCUMENT_BYTES):
    require(type(raw) is bytes and 0 < len(raw) <= limit, "DOCUMENT_EMPTY_OR_TOO_LARGE")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise ProductError("JSON_NONFINITE")

    def integer(value):
        require(len(value.lstrip("-")) <= 77, "JSON_NUMBER_LIMIT")
        return int(value)

    def floating(value):
        require(len(value) <= 128, "JSON_NUMBER_LIMIT")
        result = float(value)
        require(math.isfinite(result), "JSON_NONFINITE")
        mantissa = value.lower().split("e", 1)[0]
        require(result != 0 or not any(char in "123456789" for char in mantissa),
                "JSON_NUMBER_UNDERFLOW")
        return result

    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
                           parse_constant=nonfinite, parse_int=integer, parse_float=floating)
        canonical_bytes(value)
        return value
    except ProductError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ProductError("JSON_MALFORMED") from exc


_DATE = re.compile(TIMESTAMP_PATTERN)


def parse_timestamp(value: str) -> datetime:
    """Timezone-aware RFC3339, microsecond precision; unknown -00:00 rejected.

    Bounds also exclude instants whose UTC conversion overflows datetime.
    Caller supplies capture time; no wall-clock defaults affect reproducibility.
    """
    require(type(value) is str and _DATE.fullmatch(value) is not None
            and not value.endswith("-00:00"), "INVALID_TIMESTAMP")
    try:
        normalized = value[:10] + "T" + value[11:]
        if normalized.endswith(("Z", "z")):
            normalized = normalized[:-1] + "+00:00"
        result = datetime.fromisoformat(normalized)
        require(result.utcoffset() is not None, "INVALID_TIMESTAMP")
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ProductError("INVALID_TIMESTAMP") from exc


def _valid_timestamp(value):
    if type(value) is not str:
        return True
    try:
        parse_timestamp(value)
        return True
    except ProductError:
        return False


@lru_cache(maxsize=8)
def validator(name: str) -> Draft202012Validator:
    definition = schema(name)
    Draft202012Validator.check_schema(definition)
    checker = FormatChecker()
    checker.checks("date-time")(_valid_timestamp)
    # Empty registry + local references: validation never resolves remote URLs.
    return Draft202012Validator(definition, registry=Registry(), format_checker=checker)


def content_id(name: str, value: dict) -> str:
    require(type(name) is str and name in CONTRACT_IDS, "UNKNOWN_PRODUCT_CONTRACT")
    require(type(value) is dict, "JSON_OBJECT_REQUIRED")
    field, prefix = CONTRACT_IDS[name]
    return prefix + digest({key: item for key, item in value.items() if key != field})


def validate_contract(name: str, value: dict, *, raw: bytes | None = None,
                      snapshot=None, proof_raw: bytes | None = None,
                      license_raw: dict[str, bytes] | None = None) -> dict:
    """Validate content IDs and semantics, returning a detached JSON value.

    A snapshot requires raw evidence; a valid hash of forged normalized content
    alone cannot establish local provenance. No external attestation is implied.
    """
    require(type(name) is str and name in CONTRACT_IDS, "UNKNOWN_PRODUCT_CONTRACT")
    require(type(value) is dict, "JSON_OBJECT_REQUIRED")
    encoded = canonical_bytes(value)
    require(validator(name).is_valid(value), "INVALID_" + name.upper())
    field, _ = CONTRACT_IDS[name]
    require(value[field] == content_id(name, value), "CONTENT_ID_MISMATCH")
    if name == "ProductListingSnapshot":
        from .reader import _verify_snapshot
        _verify_snapshot(value, raw)
    elif name == "ProductClaimEvidence":
        from .claims import _verify_claim
        _verify_claim(value, raw=raw, snapshot=snapshot, proof_raw=proof_raw)
    elif name == "ProductOfferObservation":
        from .offers import _verify_offer
        _verify_offer(value, raw=raw, snapshot=snapshot)
    elif name == "ProductAssetRights":
        from .rights import _verify_rights
        _verify_rights(value, raw=raw, snapshot=snapshot, license_raw=license_raw)
    else:
        from .identity import _verify_identity, _verify_variant
        (_verify_identity if name == "ProductIdentity" else _verify_variant)(value)
    return parse_json(encoded)


def seal_contract(name: str, body: dict, *, raw: bytes | None = None,
                  snapshot=None, proof_raw: bytes | None = None,
                  license_raw: dict[str, bytes] | None = None) -> dict:
    require(type(name) is str and name in CONTRACT_IDS, "UNKNOWN_PRODUCT_CONTRACT")
    require(type(body) is dict and CONTRACT_IDS[name][0] not in body, "INVALID_CONTRACT_BODY")
    return validate_contract(name, {**body, CONTRACT_IDS[name][0]: content_id(name, body)},
                             raw=raw, snapshot=snapshot, proof_raw=proof_raw, license_raw=license_raw)


def parse_contract(name: str, encoded: bytes, *, raw: bytes | None = None,
                   snapshot=None, proof_raw: bytes | None = None,
                   license_raw: dict[str, bytes] | None = None) -> dict:
    return validate_contract(name, parse_json(encoded), raw=raw, snapshot=snapshot,
                             proof_raw=proof_raw, license_raw=license_raw)
