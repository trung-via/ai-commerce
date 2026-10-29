"""Versioned, redacted JSON advisory history for Human/Brain/Creative readers.

Reload validates archived contracts, references, revisions and the local chain.
It never imports events, edits creative concepts or upgrades provider trust.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from ai_commerce.analytics.files import local_path, read_local
from ai_commerce.analytics.model import AnalyticsError, parse_json
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.core.provenance import canonical_bytes

from .core import MAX_DOCUMENT_BYTES, LearningError, bounded_bytes, digest, require
from .registry import MAX_EVENTS, LearningEntry, LearningRegistry, validate_history

EXPORT_VERSION = "a11-export.v1"
_LIMITS = [
    "Human consultation only; no automatic creative edit, gate or publishing action.",
    "User supplied or synthetic observations; no live provider verification or causal lift.",
    "Archived redacted facts and local hashes only; no fresh A09 raw replay or external attestation.",
    "A separately retained export ID is required to detect replacement or suffix truncation.",
]


def advisory_schema() -> dict:
    """Self-contained envelope schema; registry validation resolves inner contracts."""
    properties = {
        "schema_version": {"const": "LearningAdvisory.v1"},
        "export_version": {"const": EXPORT_VERSION},
        "export_id": {"type": "string", "pattern": "^a11-advisory-[a-f0-9]{64}$"},
        "source_status": {"const": VERIFICATION},
        "inference": {"const": "ADVISORY_ONLY"},
        "consumers": {"const": ["HUMAN", "BRAIN", "CREATIVE"]},
        "verification_scope": {"const": "ARCHIVED_LOCAL_HASH_CHAIN_ONLY"},
        "limitations": {"const": list(_LIMITS)},
        "event_count": {"type": "integer", "minimum": 0, "maximum": MAX_EVENTS},
        "head_event_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "entries": {"type": "array", "maxItems": MAX_EVENTS, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["sequence", "event_hash", "entry"], "properties": {
                "sequence": {"type": "integer", "minimum": 1, "maximum": MAX_EVENTS},
                "event_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                "entry": {"type": "object"},
            },
        }},
    }
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "LearningAdvisory.v1", "type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


_VALIDATOR = Draft202012Validator(advisory_schema())


@dataclass(frozen=True, slots=True, init=False)
class LearningAdvisory:
    """Immutable complete registry history. Dictionary reads are detached."""

    _raw: bytes

    def __init__(self, value: dict, *, expected_id: str | None = None):
        raw = bounded_bytes(value)
        checked = parse_json(raw)
        require(_VALIDATOR.is_valid(checked), "INVALID_LEARNING_ADVISORY")
        require(checked["export_id"] == "a11-advisory-" + digest({
            key: item for key, item in checked.items() if key != "export_id"}), "ADVISORY_ID_MISMATCH")
        require(expected_id is None or checked["export_id"] == expected_id, "ADVISORY_PIN_MISMATCH")
        entries = validate_history([LearningEntry(canonical_bytes(item["entry"]), item["sequence"],
                                                  item["event_hash"]) for item in checked["entries"]])
        require(checked["event_count"] == len(entries)
                and checked["head_event_hash"] == (entries[-1].event_hash if entries else "0" * 64),
                "ADVISORY_HEAD_MISMATCH")
        object.__setattr__(self, "_raw", raw)

    @classmethod
    def from_bytes(cls, raw: bytes, *, expected_id: str | None = None) -> LearningAdvisory:
        require(type(raw) is bytes and 0 < len(raw) <= MAX_DOCUMENT_BYTES, "DOCUMENT_EMPTY_OR_TOO_LARGE")
        try:
            return cls(parse_json(raw), expected_id=expected_id)
        except AnalyticsError as exc:
            raise LearningError("INVALID_ADVISORY_JSON") from exc

    def to_bytes(self) -> bytes:
        return self._raw

    def to_dict(self) -> dict:
        return parse_json(self._raw)

    @property
    def export_id(self) -> str:
        return self.to_dict()["export_id"]


def export_advisory(registry: LearningRegistry) -> LearningAdvisory:
    """Read one consistent transaction, retaining all revisions and counterevidence."""
    require(type(registry) is LearningRegistry, "REGISTRY_REQUIRED")
    entries = registry.inspect()
    body = {
        "schema_version": "LearningAdvisory.v1", "export_version": EXPORT_VERSION,
        "source_status": VERIFICATION, "inference": "ADVISORY_ONLY",
        "consumers": ["HUMAN", "BRAIN", "CREATIVE"],
        "verification_scope": "ARCHIVED_LOCAL_HASH_CHAIN_ONLY", "limitations": list(_LIMITS),
        "event_count": len(entries), "head_event_hash": entries[-1].event_hash if entries else "0" * 64,
        "entries": [{"sequence": item.sequence, "event_hash": item.event_hash, "entry": item.to_dict()}
                    for item in entries],
    }
    return LearningAdvisory({**body, "export_id": "a11-advisory-" + digest(body)})


def read_document(path, *, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    """Use A09's bounded regular-file reader without changing A09 behavior."""
    try:
        return read_local(path, limit=limit)
    except AnalyticsError as exc:
        raise LearningError("LEARNING_INPUT_FILE_ERROR") from exc


def write_advisory(path, advisory: LearningAdvisory) -> None:
    """Create one explicit local file exclusively; never overwrite existing data."""
    require(type(advisory) is LearningAdvisory, "ADVISORY_REQUIRED")
    destination, fd, created = None, None, False
    try:
        destination = local_path(path)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created = True
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(advisory.to_bytes())
    except (AnalyticsError, OSError) as exc:
        if fd is not None:
            os.close(fd)
        if created and destination is not None:
            destination.unlink(missing_ok=True)
        raise LearningError("ADVISORY_OUTPUT_FILE_ERROR") from exc
