"""BQ-06b: independent, read-only A09 freshness recheck for pinned A11 learning.

This is a separately authored local-source consumer. It cannot authenticate a
provider, copy a historic EF02 candidate, change learning/adoption, or retry a
WorkOwner. A09 and A11 are distinct databases: native pinned filesystem locks
and two exact owner reads limit races, but are not a distributed transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import (
    LearningAdvisory, LearningRegistry, SnapshotPin, export_advisory, read_snapshot,
)
from ai_commerce.learning.core import digest
from ai_commerce.creative_quality import behavioral_learning_bridge as bq06a

SCHEMA = "BQ06bFreshEvidenceReview.v1"
MAX_HISTORY = 512
PAGE_SIZE = 100
MAX_OBSERVATIONS = 512
MAX_RECORD_BYTES = 64 * 1024
HOLD = "INSUFFICIENT_EVIDENCE_HOLD"
CURRENT = "LOCAL_A09_RAW_REPLAY"
REASONS = frozenset({
    "WINDOWS_REQUIRED", "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID",
    "A11_SELECTED_HISTORY_REQUIRED", "A11_SOURCE_REPLAY_UNPROVEN",
    "A11_A09_OBSERVATION_REF_MISMATCH", "A09_LINEAGE_STALE_OR_CORRECTED",
    "HISTORY_UNPROVEN", "CROSS_OWNER_ATOMICITY_UNPROVEN",
    "COHORT_OR_DENOMINATOR_UNPROVEN", "A17_PENDING_ADVISORY_REQUIRED",
})


class _Hold(ValueError):
    def __init__(self, reason: str):
        if reason not in REASONS:
            raise ValueError("UNREGISTERED_HOLD_CODE")
        self.reason = reason
        super().__init__(reason)


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise _Hold(reason)


@dataclass(frozen=True, slots=True)
class BQ06bFreshEvidenceReview:
    """Canonical bytes are identity only, never a signature/attestation."""

    raw: bytes

    def __post_init__(self):
        if type(self.raw) is not bytes or not 0 < len(self.raw) < MAX_RECORD_BYTES:
            raise ValueError("REVIEW_SIZE_INVALID")
        value = json.loads(self.raw)
        if canonical_bytes(value) != self.raw or type(value) is not dict:
            raise ValueError("REVIEW_NOT_CANONICAL")
        record = {k: v for k, v in value.items() if k != "record_sha256"}
        if value.get("record_sha256") != sha256(canonical_bytes(record)).hexdigest():
            raise ValueError("REVIEW_DIGEST_MISMATCH")
        if set(value) != {
            "schema_version", "status", "reason_codes", "a09_import_sha256",
            "a11_export_sha256", "selected_observation_ref_set_sha256",
            "selected_observation_count", "history_count", "source_kind",
            "verification_scope", "provider_attested", "causal_lift", "roi",
            "brain_decision", "human_adoption", "writes_performed", "record_sha256",
        }:
            raise ValueError("REVIEW_UNKNOWN_FIELDS")
        if value["schema_version"] != SCHEMA or value["provider_attested"] is not False:
            raise ValueError("REVIEW_AUTHORITY_INVALID")
        if value["writes_performed"] is not False or value["brain_decision"] is not None:
            raise ValueError("REVIEW_AUTHORITY_INVALID")
        if value["human_adoption"] is not None or value["causal_lift"] is not None or value["roi"] is not None:
            raise ValueError("REVIEW_AUTHORITY_INVALID")
        if value["status"] not in (CURRENT, HOLD):
            raise ValueError("REVIEW_STATUS_INVALID")
        if value["reason_codes"] and (value["status"] != HOLD
                                     or sorted(set(value["reason_codes"])) != value["reason_codes"]
                                     or not set(value["reason_codes"]) <= REASONS):
            raise ValueError("REVIEW_HOLD_INVALID")
        if (not value["reason_codes"]) != (value["status"] == CURRENT):
            raise ValueError("REVIEW_HOLD_INVALID")

    def to_dict(self) -> dict:
        return json.loads(self.raw)

    @property
    def digest(self) -> str:
        return sha256(self.raw).hexdigest()


def _seal(*, reason: str | None = None, import_id: str | None = None,
          export_id: str | None = None, refs: list[dict] | None = None,
          history_count: int = 0, source_kind: str | None = None) -> BQ06bFreshEvidenceReview:
    _require(reason is None or reason in REASONS, "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
    body = {
        "schema_version": SCHEMA,
        "status": CURRENT if reason is None else HOLD,
        "reason_codes": [] if reason is None else [reason],
        "a09_import_sha256": None if import_id is None else sha256(import_id.encode()).hexdigest(),
        "a11_export_sha256": None if export_id is None else sha256(export_id.encode()).hexdigest(),
        "selected_observation_ref_set_sha256": None if refs is None else digest(refs),
        "selected_observation_count": 0 if refs is None else len(refs),
        "history_count": history_count,
        "source_kind": source_kind,
        "verification_scope": "LOCAL_A09_RAW_AND_A11_ARCHIVE_ONLY" if reason is None else "NOT_PROVEN",
        "provider_attested": False, "causal_lift": None, "roi": None,
        "brain_decision": None, "human_adoption": None, "writes_performed": False,
    }
    raw = canonical_bytes({**body, "record_sha256": sha256(canonical_bytes(body)).hexdigest()})
    return BQ06bFreshEvidenceReview(raw)


def _complete_native_history(ledger: AnalyticsLedger, plan: dict, selected: str) -> list[dict]:
    """Recheck the complete A09 same-scope append-only history, no top-100 PASS."""
    rows: list[dict] = []
    seen = set()
    offset = 0
    for _ in range((MAX_HISTORY + PAGE_SIZE - 1) // PAGE_SIZE + 1):
        page = ledger.history(plan=plan, limit=PAGE_SIZE, offset=offset)
        _require(type(page) is dict and set(page) == {"imports", "count", "next_offset"},
                 "HISTORY_UNPROVEN")
        imports = page["imports"]
        _require(type(imports) is list and page["count"] == len(imports)
                 and 0 < len(imports) <= PAGE_SIZE, "HISTORY_UNPROVEN")
        for record in imports:
            _require(type(record) is dict and type(record.get("import_id")) is str,
                     "HISTORY_UNPROVEN")
            ident = record["import_id"]
            lineage = record.get("lineage")
            _require(ident not in seen and type(lineage) is dict
                     and set(lineage) == {"relationship", "parent_import_id"}
                     and lineage["relationship"] in ("ORIGINAL", "CORRECTION", "REPROCESS"),
                     "HISTORY_UNPROVEN")
            parent = lineage["parent_import_id"]
            _require((lineage["relationship"] == "ORIGINAL" and parent is None)
                     or (lineage["relationship"] != "ORIGINAL"
                         and type(parent) is str and parent in seen),
                     "HISTORY_UNPROVEN")
            seen.add(ident)
            rows.append(record)
        _require(len(rows) <= MAX_HISTORY, "HISTORY_UNPROVEN")
        cursor = page["next_offset"]
        if cursor is None:
            _require(selected in seen, "HISTORY_UNPROVEN")
            return rows
        _require(type(cursor) is int and cursor == offset + len(imports)
                 and cursor > offset and len(imports) == PAGE_SIZE
                 and len(rows) < MAX_HISTORY, "HISTORY_UNPROVEN")
        offset = cursor
    raise _Hold("HISTORY_UNPROVEN")


def _superseded(records: list[dict], pinned_import_id: str) -> bool:
    """Genuine directed parent ancestry; unrelated roots never invalidate."""
    ancestry: dict[str, str | None] = {}
    for rec in records:
        lineage = rec["lineage"]
        parent = lineage["parent_import_id"]
        ident = rec["import_id"]
        if parent is not None:
            cursor = parent
            for _ in range(len(records)):
                if cursor == pinned_import_id:
                    return True
                cursor = ancestry.get(cursor)
                if cursor is None:
                    break
            else:
                raise _Hold("HISTORY_UNPROVEN")
        ancestry[ident] = parent
    _require(pinned_import_id in ancestry, "HISTORY_UNPROVEN")
    return False


def _a11_current_refs(advisory: LearningAdvisory, source_arguments: dict) -> list[dict]:
    value = advisory.to_dict()
    events = [event for event in value["entries"] if event["entry"]["revision"]["learning_key"]
              == source_arguments["learning_key"]]
    _require(events and events[-1]["entry"]["revision"]["revision"]
             == source_arguments["expected_revision"], "A11_SELECTED_HISTORY_REQUIRED")
    selected = events[-1]["entry"]
    facts = selected["observation_sources"]
    _require(type(facts) is list and 0 < len(facts) <= MAX_OBSERVATIONS,
             "A11_SELECTED_HISTORY_REQUIRED")
    refs = [fact["observation_ref"] for fact in facts]
    _require(all(type(ref) is dict for ref in refs), "A11_SELECTED_HISTORY_REQUIRED")
    evidence_refs = [ev["observation_ref"] for ev in selected["evidence"]]
    _require(len(set(digest(ref) for ref in refs)) == len(refs)
             and evidence_refs and set(digest(ref) for ref in evidence_refs) <= set(digest(ref) for ref in refs),
             "A11_A09_OBSERVATION_REF_MISMATCH")
    _require(all(fact["metric"] == source_arguments["expected_metric"]
                 and fact["context"] == source_arguments["expected_context"] for fact in facts),
             "A11_A09_OBSERVATION_REF_MISMATCH")
    return refs


def _native_recheck(ledger_path: str, pin: SnapshotPin, source_arguments: dict,
                    previous_raw: bytes) -> tuple[str, str, list[dict], int, str]:
    # Native owner lock rejects symlink/junction/hardlink/reparse/ADS path aliases.
    # Both databases and their ancestor directories are frozen during the
    # cross-owner pinned reads. This is not a distributed SQLite transaction.
    source = source_arguments.get("registry_path")
    archive = source_arguments.get("archive_path")
    _require((type(source) is str) != (type(archive) is str), "A11_SELECTED_HISTORY_REQUIRED")
    with bq06a._frozen(files=[ledger_path, source or archive]):
        bq06a._no_sqlite_sidecars(ledger_path)
        if source is not None:
            bq06a._no_sqlite_sidecars(source)
        old = bq06a.recheck_review(previous_raw, **source_arguments)
        _require(old.to_dict()["status"] == "EVIDENCE_REVIEW_FOR_BRAIN"
                 and old.to_dict()["a17"] is not None
                 and old.to_dict()["a17"]["adoption"] == "PENDING",
                 "A17_PENDING_ADVISORY_REQUIRED")
        if source is not None:
            advisory = export_advisory(LearningRegistry(source))
        else:
            advisory = LearningAdvisory.from_bytes(
                Path(archive).read_bytes(), expected_id=source_arguments["expected_export_id"])
        _require(advisory.export_id == source_arguments["expected_export_id"],
                 "A11_SOURCE_REPLAY_UNPROVEN")
        refs = _a11_current_refs(advisory, source_arguments)
        snap = read_snapshot(ledger_path, pin=pin)
        record = snap.to_dict()["import"]
        _require(record["source_kind"] in ("SYNTHETIC_FIXTURE", "USER_AUTHORED_MANUAL",
                                          "USER_PROVIDED_PLATFORM_EXPORT"),
                 "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
        _require(all(ref["import_id"] == pin.import_id for ref in refs),
                 "A11_A09_OBSERVATION_REF_MISMATCH")
        native_refs = [snap.observation_ref(ob["observation_id"])
                       for ob in snap.to_dict()["observations"]]
        native_ref_digests = {digest(r) for r in native_refs}
        # The selected A11 evidence set may be a strict subset of the full
        # authentic A09 import, but each selected ref must match in full.
        _require(len(native_ref_digests) == len(native_refs)
                 and all(digest(r) in native_ref_digests for r in refs),
                 "A11_A09_OBSERVATION_REF_MISMATCH")
        for ob in snap.to_dict()["observations"]:
            _require((ob["status"] == "AVAILABLE") == (ob["value"] is not None),
                     "COHORT_OR_DENOMINATOR_UNPROVEN")
            _require(ob["metric"] == source_arguments["expected_metric"]
                     and all(ob[k] == source_arguments["expected_context"][k]
                             for k in ("platform", "surface",
                                       "cohort", "measurement_window", "timezone"))
                     and source_arguments["expected_context"]["account_ref"] == {
                         "namespace": "internal_alias",
                         "id": "a11-account-" + digest(ob["account_ref"])},
                     "COHORT_OR_DENOMINATOR_UNPROVEN")
        ledger = AnalyticsLedger(ledger_path)
        first = _complete_native_history(ledger, record["plan"], pin.import_id)
        stale = _superseded(first, pin.import_id)
        # Independent replay of the entire history and snapshot under same
        # frozen file handles detects races/incomplete cross-owner observations.
        second = _complete_native_history(ledger, record["plan"], pin.import_id)
        _require(first == second and snap.to_bytes() == read_snapshot(ledger_path, pin=pin).to_bytes(),
                 "CROSS_OWNER_ATOMICITY_UNPROVEN")
        bq06a._no_sqlite_sidecars(ledger_path)
        if source is not None:
            bq06a._no_sqlite_sidecars(source)
        _require(not stale, "A09_LINEAGE_STALE_OR_CORRECTED")
        return pin.import_id, advisory.export_id, refs, len(first), record["source_kind"]


def review_fresh_evidence(*, ledger_path: str, pin: SnapshotPin,
                          bq06a_raw: bytes, source_arguments: dict) -> BQ06bFreshEvidenceReview:
    """A09 fresh local recheck against independently replayed A11/BQ06a pins.

    "Current" is LOCAL provenance only. All missing evidence returns typed HOLD.
    Caller must hold independent A11/BQ06a pins; this is not a trust-root.
    """
    if os.name != "nt":
        return _seal(reason="WINDOWS_REQUIRED")
    try:
        _require(type(ledger_path) is str and type(pin) is SnapshotPin,
                 "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
        _require(type(bq06a_raw) is bytes and type(source_arguments) is dict,
                 "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
        imported, advisory, refs, count, kind = _native_recheck(
            ledger_path, pin, source_arguments, bq06a_raw)
        return _seal(import_id=imported, export_id=advisory, refs=refs,
                     history_count=count, source_kind=kind)
    except _Hold as exc:
        return _seal(reason=exc.reason)
    except Exception:
        # Native owner exceptions, OS errors and private source payloads do
        # not escape to an untrusted public reviewer.
        return _seal(reason="SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
