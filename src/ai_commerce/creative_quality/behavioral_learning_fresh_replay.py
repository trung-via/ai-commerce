"""BQ-06b: independently pinned, read-only A09/A11 freshness review.

Reauthored CI-first from main. A local replay is not provider verification,
causal attribution, a Brain decision, or authority to touch PULSE/A17.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import os

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import (
    SnapshotPin, read_snapshot, LearningAdvisory, LearningRegistry, export_advisory,
)
from ai_commerce.creative_quality import behavioral_learning_bridge as bq06a


SCHEMA = "BQ06bFreshEvidenceReview.v1"
MAX_HISTORY = 512
PAGE_LIMIT = 100
REASONS = frozenset({
    "WINDOWS_REQUIRED", "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID",
    "CROSS_OWNER_ATOMICITY_UNPROVEN", "HISTORY_UNPROVEN",
    "A09_LINEAGE_STALE_OR_CORRECTED", "A11_SELECTED_HISTORY_REQUIRED",
    "A11_OBSERVATION_REFS_MISMATCH", "A11_MIXED_IMPORTS_UNSUPPORTED",
    "A11_ARCHIVED_REVIEW_HOLD", "SOURCE_STATUS_NOT_PROVIDER_ATTESTED",
})


class FreshEvidenceHold(ValueError):
    """Only finite public codes; never leak native paths or secrets."""


@dataclass(frozen=True)
class BQ06bFreshEvidenceReview:
    raw: bytes

    def to_dict(self):
        return json.loads(self.raw.decode("utf-8"))

    @property
    def digest(self):
        return sha256(self.raw).hexdigest()


def _need(condition, code):
    if not condition:
        raise FreshEvidenceHold(code)


def _seal(missing=(), *, snapshot=None, source_count=0, review=None, provenance=None):
    reasons = sorted(set(missing))
    body = {
        "schema_version": SCHEMA,
        "status": "INSUFFICIENT_EVIDENCE_HOLD" if reasons else "EVIDENCE_REVIEW_FOR_BRAIN",
        "a09": None if snapshot is None else {
            "snapshot_id": snapshot.snapshot_id,
            "import_id_sha256": sha256(snapshot.import_id.encode()).hexdigest(),
            "verification_scope": "LOCAL_A09_RAW_REPLAY",
            "source_status": "LOCAL_SUPPLIED_OR_SYNTHETIC_ONLY",
            "matched_selected_a11_refs": source_count,
        },
        "source_state_counts": None if snapshot is None else {
            "available_zero": sum(
                item["status"] == "AVAILABLE" and type(item["value"]) in (int, float)
                and item["value"] == 0 for item in snapshot.to_dict()["observations"]
            ),
            "not_available": sum(
                item["status"] == "NOT_AVAILABLE" and item["value"] is None
                for item in snapshot.to_dict()["observations"]
            ),
        },
        "a11_review_sha256": None if review is None else sha256(review.raw).hexdigest(),
        "provenance": provenance,
        "missing_requirements": reasons,
        "questions": [
            "What independent owner evidence could disconfirm the retained hypothesis?",
            "Are metric definition, denominator, cohort, window and timezone comparable?",
            "Does any authorized provider attestation actually exist? (not claimed here)",
        ],
        "limits": {
            "authority": "NONE", "provider_attested": False,
            "causal_lift": None, "roi": None, "live_pulse": False,
            "verification": "LOCAL_HASH_AND_A09_REPLAY_ONLY",
            "hash_use": "IDENTITY_ONLY_NOT_SIGNATURE",
            "brain_decision": "NOT_MADE", "write_attempted": False,
        },
    }
    data = canonical_bytes(body)
    return BQ06bFreshEvidenceReview(canonical_bytes({
        **body, "record_sha256": sha256(data).hexdigest(),
    }))


def _history(ledger, selected):
    """Full bounded native same-scope history, including transitive descendants."""
    _need(type(selected) is dict and type(selected.get("plan")) is dict,
          "HISTORY_UNPROVEN")
    plan, selected_id = selected["plan"], selected["import_id"]
    offset, seen, records = 0, set(), []
    for _ in range(1 + MAX_HISTORY // PAGE_LIMIT):
        page = ledger.history(plan=plan, limit=PAGE_LIMIT, offset=offset)
        _need(type(page) is dict and set(page) == {"imports", "count", "next_offset"},
              "HISTORY_UNPROVEN")
        items, count, nxt = page["imports"], page["count"], page["next_offset"]
        _need(type(items) is list and type(count) is int and count == len(items)
              and 0 <= count <= PAGE_LIMIT and len(records) + count <= MAX_HISTORY,
              "HISTORY_UNPROVEN")
        for record in items:
            _need(type(record) is dict, "HISTORY_UNPROVEN")
            ident, lineage = record.get("import_id"), record.get("lineage")
            _need(type(ident) is str and ident not in seen and type(lineage) is dict
                  and set(lineage) == {"relationship", "parent_import_id"},
                  "HISTORY_UNPROVEN")
            relation, parent = lineage["relationship"], lineage["parent_import_id"]
            _need(relation in ("ROOT", "CORRECTION", "REPROCESS"), "HISTORY_UNPROVEN")
            if relation == "ROOT":
                _need(parent is None, "HISTORY_UNPROVEN")
            else:
                _need(type(parent) is str and parent in seen, "HISTORY_UNPROVEN")
            _need(record.get("scope_sha256") == selected.get("scope_sha256"),
                  "HISTORY_UNPROVEN")
            seen.add(ident)
            records.append(record)
        if nxt is None:
            break
        _need(type(nxt) is int and nxt == offset + PAGE_LIMIT and count == PAGE_LIMIT
              and len(records) < MAX_HISTORY, "HISTORY_UNPROVEN")
        offset = nxt
    else:
        raise FreshEvidenceHold("HISTORY_UNPROVEN")
    same = [x for x in records if x["import_id"] == selected_id]
    _need(len(same) == 1 and same[0] == selected, "HISTORY_UNPROVEN")
    descendants = {selected_id}
    for item in records:
        lineage = item["lineage"]
        if lineage["parent_import_id"] in descendants:
            descendants.add(item["import_id"])
    _need(descendants == {selected_id}, "A09_LINEAGE_STALE_OR_CORRECTED")
    return records


def _advisory(source_arguments):
    path = source_arguments.get("registry_path")
    archive = source_arguments.get("archive_path")
    _need((path is None) != (archive is None), "A11_SELECTED_HISTORY_REQUIRED")
    if path is not None:
        return export_advisory(LearningRegistry(path))
    return LearningAdvisory.from_bytes(
        Path(archive).read_bytes(),
        expected_id=source_arguments["expected_export_id"],
    )


def _selected_refs(advisory, source_arguments):
    raw = advisory.to_dict()
    key, rev = source_arguments["learning_key"], source_arguments["expected_revision"]
    events = [item["entry"] for item in raw["entries"]
              if item["entry"]["revision"]["learning_key"] == key]
    _need(events and events[-1]["revision"]["revision"] == rev,
          "A11_SELECTED_HISTORY_REQUIRED")
    latest = events[-1]
    facts = latest["observation_sources"]
    evidence = latest["evidence"]
    _need(facts and evidence and len(facts) <= 512 and len(evidence) <= 512,
          "A11_SELECTED_HISTORY_REQUIRED")
    refs = [fact["observation_ref"] for fact in facts]
    _need(len({canonical_bytes(ref) for ref in refs}) == len(refs),
          "A11_OBSERVATION_REFS_MISMATCH")
    fact_set = {canonical_bytes(ref) for ref in refs}
    _need(all(canonical_bytes(event["observation_ref"]) in fact_set
              for event in evidence), "A11_OBSERVATION_REFS_MISMATCH")
    return refs


def review_fresh(*, a09_path, snapshot_pin, bq06a_raw, source_arguments):
    """One scoped, fail-closed, read-only receiver replay.

    A09 and A11 bytes/Windows native locks cover both native owner reads.
    Do not infer cross-DB transaction atomicity from separate SQLite readers.
    """
    if os.name != "nt":
        return _seal(["WINDOWS_REQUIRED"])
    try:
        _need(type(a09_path) is str and type(snapshot_pin) is SnapshotPin
              and type(bq06a_raw) is bytes and type(source_arguments) is dict,
              "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
        _need(source_arguments.get("a09_current") is None
              and source_arguments.get("effect", "REVIEW_ONLY") == "REVIEW_ONLY",
              "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
        a11_file = source_arguments.get("registry_path") or source_arguments.get("archive_path")
        _need(type(a11_file) is str, "A11_SELECTED_HISTORY_REQUIRED")
        a17 = source_arguments.get("a17")
        _need(a17 is None or (type(a17) is dict
              and type(a17.get("registry_root")) is str),
              "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
        paths = [a09_path, a11_file]
        trees = [] if a17 is None else [a17["registry_root"]]
        with bq06a._frozen(files=paths, trees=trees):
            for path in paths:
                if path.endswith(".sqlite") or path.endswith(".db"):
                    bq06a._no_sqlite_sidecars(path)
            archived = bq06a.recheck_review(bq06a_raw, **source_arguments)
            snapshot = read_snapshot(a09_path, pin=snapshot_pin)
            ledger = AnalyticsLedger(a09_path)
            selected = ledger.inspect(snapshot.import_id)["import"]
            _history(ledger, selected)
            advisory = _advisory(source_arguments)
            _need(advisory.export_id == source_arguments["expected_export_id"],
                  "A11_SELECTED_HISTORY_REQUIRED")
            refs = _selected_refs(advisory, source_arguments)
            full = {canonical_bytes(snapshot.observation_ref(value["observation_id"]))
                    for value in snapshot.to_dict()["observations"]}
            _need(all(ref.get("import_id") == snapshot.import_id for ref in refs),
                  "A11_MIXED_IMPORTS_UNSUPPORTED")
            _need(all(canonical_bytes(ref) in full for ref in refs),
                  "A11_OBSERVATION_REFS_MISMATCH")
            archived_value = archived.to_dict()
            _need(archived_value["status"] == "EVIDENCE_REVIEW_FOR_BRAIN",
                  "A11_ARCHIVED_REVIEW_HOLD")
            source_kinds = {item["source"]["source_kind"]
                            for item in snapshot.to_dict()["observations"]}
            _need(source_kinds <= {"SYNTHETIC_FIXTURE", "USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT"},
                  "SOURCE_STATUS_NOT_PROVIDER_ATTESTED")
            return _seal(snapshot=snapshot, source_count=len(refs), review=archived,
                         provenance="LOCAL_SUPPLIED_OR_SYNTHETIC_ONLY")
    except FreshEvidenceHold as exc:
        return _seal([str(exc)])
    except Exception:
        return _seal(["CROSS_OWNER_ATOMICITY_UNPROVEN"])


def recheck_fresh(raw, **arguments):
    """Receiver must re-run real native owners, never trust sidecar hash alone."""
    candidate = BQ06bFreshEvidenceReview(raw)
    current = review_fresh(**arguments)
    if candidate.raw != current.raw:
        raise FreshEvidenceHold("SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
    return current
