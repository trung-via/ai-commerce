"""Deterministic, caller-authored qualitative insights with exact M01 references."""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache

from jsonschema import Draft202012Validator

from ai_commerce.contracts.schema import _format_checker
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ..core.models import OpportunityError, digest, parse_json
from ..core.schemas import SOURCE_STATUS
from ..signals.ledger import SignalLedger
from .schemas import NOTES, schema

MAX_NOTES_BYTES = 256 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024


class AudienceError(OpportunityError):
    """Bounded error codes never echo caller notes, contact details, or paths."""


def check(ok: bool, code: str) -> None:
    if not ok:
        raise AudienceError(code)


@lru_cache(maxsize=2)
def _validator(notes: bool = False) -> Draft202012Validator:
    value = NOTES if notes else schema()
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value, format_checker=_format_checker())


def _privacy(value: str) -> None:
    # Scan a normalized copy only: accepted source bytes/text are never rewritten.
    scanned = unicodedata.normalize("NFKC", value)
    scanned = "".join(c for c in scanned if unicodedata.category(c) != "Cf")
    contact = (
        re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", scanned)
        or re.search(r"(?<!\w)@[\w.]{2,}", scanned)
        or re.search(r"(?<!\w)(?:\+?\d[\s().-]*){7,}(?!\w)", scanned)
        or re.search(r"(?i)\b(?:mailto|tel):|https?://", scanned)
    )
    check(not contact, "QUALITATIVE_CONTACT_OR_IDENTIFIER_NOT_ALLOWED")
    check(not any(ord(c) < 32 and c not in "\n\r\t" for c in value), "NOTE_CONTROL_CHARACTER")
    check(bool(value.strip()), "NOTE_TEXT_EMPTY")


def load_notes(raw: bytes) -> dict:
    check(isinstance(raw, bytes) and 0 < len(raw) <= MAX_NOTES_BYTES, "NOTES_EMPTY_OR_TOO_LARGE")
    try:
        notes = parse_json(raw)
    except OpportunityError as exc:
        raise AudienceError("NOTES_INVALID_JSON") from exc
    check(_validator(True).is_valid(notes), "INVALID_AUDIENCE_NOTES")
    for value in (notes["source_note"], notes.get("audience_segment")):
        if value is not None:
            _privacy(value)
    for statement in notes["statements"]:
        _privacy(statement["text"])
        _privacy(statement.get("uncertainty", "UNKNOWN"))
        for ref in statement.get("evidence_refs", []):
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", ref["evidence_ref"]):
                _privacy(ref["evidence_ref"])
    return notes


def _resolve(ref: dict, ledger: SignalLedger | None, cache: dict) -> dict:
    check(ledger is not None, "M01_EVIDENCE_REQUIRED")
    import_id = ref["import_id"]
    if import_id not in cache:
        try:
            cache[import_id] = ledger.get(import_id)
        except (OpportunityError, OSError, ValueError, TypeError, KeyError) as exc:
            raise AudienceError("M01_EVIDENCE_UNAVAILABLE_OR_INVALID") from exc
    imported = cache[import_id]
    record = imported["import"]
    check(record["source_snapshot"]["sha256"] == record["raw_sha256"], "M01_SOURCE_HASH_MISMATCH")
    check(ref["raw_sha256"] == record["raw_sha256"], "M01_RAW_HASH_MISMATCH")
    signal = next((s for s in imported["signals"] if s["signal_id"] == ref["signal_id"]), None)
    check(signal is not None, "M01_SIGNAL_NOT_FOUND")
    check(ref["evidence_ref"] in [record["raw_ref"], *signal.get("evidence_refs", [])], "M01_REF_NOT_BOUND")
    return {
        "reference": deepcopy(ref),
        "context_sha256": record["context_sha256"],
        "scope_sha256": record["scope_sha256"],
        "lineage": deepcopy(record["lineage"]),
        "source_status": record["source_status"],
        "source_rights_status": record["source_snapshot"]["rights_status"],
        "signal_rights_status": signal["rights_status"],
        "signal_status": signal["status"],
        "value": signal["value"],
        "observed_at": signal["observed_at"],
    }


def signal_evidence_ref(ledger: SignalLedger, import_id: str, signal_id: str) -> dict:
    """Build an exact reference using M01's existing sha256 raw-ref convention."""
    try:
        record = ledger.get(import_id)["import"]
    except (OpportunityError, OSError, ValueError, TypeError, KeyError) as exc:
        raise AudienceError("M01_EVIDENCE_UNAVAILABLE_OR_INVALID") from exc
    ref = {"import_id": import_id, "signal_id": signal_id,
           "raw_sha256": record["raw_sha256"], "evidence_ref": record["raw_ref"]}
    _resolve(ref, ledger, {})
    return ref


def validate_insight(record: dict) -> dict:
    check(_validator().is_valid(record), "INVALID_AUDIENCE_INSIGHT")
    try:
        body = {k: v for k, v in record.items() if k != "insight_id"}
        check(record["insight_id"] == "aud-" + digest(body), "INSIGHT_IDENTITY_MISMATCH")
        check(len(canonical_bytes(record)) <= MAX_RECORD_BYTES, "INSIGHT_TOO_LARGE")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AudienceError):
            raise
        raise AudienceError("INVALID_AUDIENCE_INSIGHT") from exc
    return record


@dataclass(frozen=True)
class PreparedAudienceInsight:
    raw: bytes
    record: dict


def prepare_bytes(raw: bytes, *, signal_ledger: SignalLedger | None = None,
                  parent: dict | None = None) -> PreparedAudienceInsight:
    """Preview only: no writes, timestamps, generated claims, or external calls.

    AudienceLedger resolves a revision's parent from its own immutable journal.
    A supplied pure-preview parent must itself have a valid content identity.
    """
    notes = load_notes(raw)
    raw_sha = sha256_bytes(raw)
    if parent is not None:
        validate_insight(parent)
        check(parent["source_sha256"] != raw_sha, "REVISION_SOURCE_UNCHANGED")
    raw_ref = "sha256:" + raw_sha
    proofs, cache, statements = {}, {}, []
    for original in notes["statements"]:
        refs = original.get("evidence_refs", [])
        for ref in refs:
            key = canonical_bytes(ref)
            if key not in proofs:
                proofs[key] = _resolve(ref, signal_ledger, cache)
        statements.append({
            **deepcopy(original),
            "uncertainty": original.get("uncertainty", "UNKNOWN"),
            "evidence_refs": deepcopy(refs),
            "note_ref": raw_ref,
            "evidence_status": "LOCAL_INTEGRITY_CHECKED" if refs else "NOTE_ONLY",
        })
    body = {
        "schema_version": "AudienceInsight.v1",
        "source_sha256": raw_sha,
        "source_ref": raw_ref,
        "source_status": SOURCE_STATUS,
        "rights_status": "UNKNOWN",
        "data_kind": notes["data_kind"],
        "source_note": notes["source_note"],
        "noted_at": notes.get("noted_at"),
        "audience_segment": notes.get("audience_segment"),
        "statements": statements,
        "signal_evidence": [proofs[k] for k in sorted(proofs)],
        "lineage": {
            "relationship": "REVISION" if parent is not None else "ORIGINAL",
            "parent_insight_id": parent["insight_id"] if parent is not None else None,
            "number": parent["lineage"]["number"] + 1 if parent is not None else 1,
        },
    }
    record = {"insight_id": "aud-" + digest(body), **body}
    validate_insight(record)
    return PreparedAudienceInsight(raw, record)
