"""A11-03: bounded, offline, append-only learning revisions.

``append(hypothesis=..., evidence=[...], record=..., revision=...,
snapshots=[...], reports=[...])`` accepts the sealed A11-01 contracts. Callers
supply explicit revision timestamps, previous revision refs and non-personal
actor/creative aliases. Use ``hypothesis_context(snapshot)`` for the hypothesis
context: account IDs are replaced by deterministic internal aliases. Source
post IDs, URLs, matching data, raw rows and complete snapshots are never stored.
Free text must be non-personal; obvious contact/URL data is rejected, but this
is not a general purpose PII classifier.

Each event contains the complete advisory record and the minimum source facts
needed to interpret its evidence. Reports are validated A11-02 identities, not
recalculated or upgraded to provider verification. Archived facts and local
hash chains detect local damage, not a malicious rewrite of the entire database
or an externally verified observation. Reopen reads do not claim a fresh A09
raw replay. No A09 database connection is opened by this module.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from ai_commerce.analytics.files import local_path
from ai_commerce.analytics.model import AnalyticsError, parse_json, validate_timezone
from ai_commerce.analytics.schemas import CONTEXT, VERIFICATION
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from .core import LearningError, bounded_bytes, digest, require, seal_contract, validate_contract
from .reader import ObservationSnapshot
from .schemas import READER_VERSION, schema

REGISTRY_VERSION = "a11-registry.v1"
MAX_ENTRY_BYTES = 512 * 1024
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 4096
MAX_SNAPSHOTS = 16
MAX_REPORTS = 16
_APP_ID = 0x41313133
_ZERO = "0" * 64
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_CONTACT = re.compile(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\w)\+?(?:\d[ ()-]*){9,14}(?!\w)", re.I)
_CONTEXT_KEYS = tuple(CONTEXT)
_TABLE = """CREATE TABLE a11_events(
    seq INTEGER PRIMARY KEY CHECK(seq>0),
    revision_id TEXT UNIQUE NOT NULL,
    learning_key TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>0 AND revision<=1000000),
    previous_revision_id TEXT REFERENCES a11_events(revision_id),
    payload BLOB NOT NULL CHECK(typeof(payload)='blob' AND length(payload)>0 AND length(payload)<=524288),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    previous_event_hash TEXT NOT NULL CHECK(length(previous_event_hash)=64),
    event_hash TEXT UNIQUE NOT NULL CHECK(length(event_hash)=64),
    UNIQUE(learning_key,revision))"""
_DDL = [_TABLE]
for _operation in ("UPDATE", "DELETE"):
    _DDL.append(f"CREATE TRIGGER a11_no_{_operation.lower()} BEFORE {_operation} ON a11_events "
                "BEGIN SELECT RAISE(ABORT,'A11_APPEND_ONLY'); END")
_DDL.append("""CREATE TRIGGER a11_insert_order BEFORE INSERT ON a11_events BEGIN
    SELECT CASE WHEN NEW.seq != COALESCE((SELECT MAX(seq) FROM a11_events),0)+1
        OR EXISTS(SELECT 1 FROM a11_events WHERE revision_id=NEW.revision_id OR event_hash=NEW.event_hash)
        OR NEW.previous_event_hash != COALESCE((SELECT event_hash FROM a11_events ORDER BY seq DESC LIMIT 1),
            '0000000000000000000000000000000000000000000000000000000000000000')
        OR NEW.revision != COALESCE((SELECT MAX(revision) FROM a11_events WHERE learning_key=NEW.learning_key),0)+1
        OR NEW.previous_revision_id IS NOT (SELECT revision_id FROM a11_events
            WHERE learning_key=NEW.learning_key ORDER BY revision DESC LIMIT 1)
    THEN RAISE(ABORT,'A11_INVALID_ORDER') END;
END""")


def _schema_manifest(db) -> tuple:
    return tuple(tuple(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' ORDER BY name"))


def _expected_schema() -> tuple:
    with sqlite3.connect(":memory:") as db:
        for sql in _DDL:
            db.execute(sql)
        return _schema_manifest(db)


_EXPECTED_SCHEMA = _expected_schema()
_OBSERVATION_SCHEMA = schema("ObservationSnapshot")
_SOURCE_PROPERTIES = {
    "observation_ref": schema("LearningEvidence")["$defs"]["ObservationReference"],
    "context": {"type": "object", "additionalProperties": False,
                "properties": CONTEXT, "required": list(CONTEXT)},
    "metric": {"$ref": "#/$defs/MetricDefinition"},
    "status": {"enum": ["AVAILABLE", "NOT_AVAILABLE"]},
    "value": {"type": ["number", "null"], "minimum": -1e100, "maximum": 1e100},
    "source": _OBSERVATION_SCHEMA["$defs"]["PerformanceObservation"]["properties"]["source"],
    "reader_version": {"const": READER_VERSION},
    "snapshot_schema_version": {"const": "ObservationSnapshot.v1"},
    "import_schema_version": {"const": "AnalyticsImport.v1"},
    "observation_schema_version": {"const": "PerformanceObservation.v1"},
}
# Reuse the actual A09 provenance definition rather than a parallel contract.
_SOURCE_VALIDATOR = Draft202012Validator({
    "type": "object", "additionalProperties": False, "properties": _SOURCE_PROPERTIES,
    "required": list(_SOURCE_PROPERTIES),
    "$defs": {**_OBSERVATION_SCHEMA["$defs"], **schema("LearningEvidence")["$defs"]},
})


def _key(value, code="INVALID_LEARNING_KEY") -> None:
    require(type(value) is str and _KEY.fullmatch(value) is not None, code)


def _text(value: str) -> None:
    require(bool(value.strip()), "EMPTY_LEARNING_TEXT")
    require(not _CONTACT.search(value) and not any(ord(c) < 32 and c not in "\n\t" for c in value),
            "PERSONAL_OR_CONTACT_DATA_NOT_ALLOWED")


def _privacy(value: dict) -> None:
    """Check caller prose, not timestamps, numeric measurements or hash IDs."""
    for key, item in value.items():
        if key in {"statement", "expected_outcome", "uncertainty", "observed_outcome", "reason",
                   "description", "definition", "provider_name", "unit", "cohort", "mapping_version",
                   "profile_id", "definition_version"}:
            if item is not None:
                _text(item)
        elif key in {"limitations", "caveats"}:
            for text in item:
                _text(text)
        elif isinstance(item, dict):
            _privacy(item)
        elif isinstance(item, list):
            for child in item:
                if isinstance(child, dict):
                    _privacy(child)


def _context(value: dict) -> dict:
    context = {key: value[key] for key in _CONTEXT_KEYS}
    context["account_ref"] = {"namespace": "internal_alias", "id": "a11-account-" + digest(value["account_ref"])}
    return context


def hypothesis_context(snapshot: ObservationSnapshot) -> dict:
    """Return the exact source context, replacing the account with a hash alias."""
    require(type(snapshot) is ObservationSnapshot, "SNAPSHOT_REQUIRED")
    checked = ObservationSnapshot.from_bytes(snapshot.to_bytes()).to_dict()
    return _context(checked["import"]["plan"])


def _alias_context(context: dict) -> None:
    account = context["account_ref"]
    require(account["namespace"] == "internal_alias" and type(account["id"]) is str
            and re.fullmatch(r"a11-account-[a-f0-9]{64}", account["id"]) is not None,
            "HASHED_ACCOUNT_CONTEXT_REQUIRED")
    try:
        validate_timezone(context["timezone"])
        start, end = (_instant(value) for value in context["measurement_window"])
        require(start < end, "INVALID_MEASUREMENT_WINDOW")
    except AnalyticsError as exc:
        raise LearningError("INVALID_LEARNING_TIMEZONE") from exc


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.upper().replace("Z", "+00:00"))


def _refs(value: dict):
    """Every observation ref, including confounders, must resolve exactly."""
    if "observation_id" in value:
        yield value
    else:
        for item in value.values():
            if isinstance(item, dict):
                yield from _refs(item)
            elif isinstance(item, list):
                for child in item:
                    if isinstance(child, dict):
                        yield from _refs(child)


def _sources(snapshots, reports) -> tuple[dict, list[dict]]:
    require(type(snapshots) in (list, tuple) and 1 <= len(snapshots) <= MAX_SNAPSHOTS,
            "SNAPSHOT_LIMIT")
    require(type(reports) in (list, tuple) and len(reports) <= MAX_REPORTS, "REPORT_LIMIT")
    facts, snapshot_refs, total = {}, {}, 0
    for snapshot in snapshots:
        require(type(snapshot) is ObservationSnapshot, "SNAPSHOT_REQUIRED")
        total += len(snapshot.to_bytes())
        require(total <= MAX_DATABASE_BYTES, "SOURCE_INPUT_LIMIT")
        checked = ObservationSnapshot.from_bytes(snapshot.to_bytes())
        value = checked.to_dict()
        sref = checked.snapshot_ref()
        require(sref["snapshot_id"] not in snapshot_refs, "DUPLICATE_SOURCE_SNAPSHOT")
        snapshot_refs[sref["snapshot_id"]] = sref
        for obs in value["observations"]:
            ref = {**sref, "observation_id": obs["observation_id"],
                   "observation_sha256": digest(obs), "metric_definition_sha256": digest(obs["metric"])}
            facts[digest(ref)] = {
                "observation_ref": ref, "context": _context(obs), "metric": obs["metric"],
                "status": obs["status"], "value": obs["value"], "source": obs["source"],
                "reader_version": value["reader_version"],
                "snapshot_schema_version": value["schema_version"],
                "import_schema_version": value["import"]["schema_version"],
                "observation_schema_version": obs["schema_version"],
            }
    report_sources = []
    seen = set()
    for report in reports:
        # No runtime dependency on reporting: it supplies an existing sealed contract.
        if type(report) is not dict:
            from .reporting import PerformanceReport
            require(type(report) is PerformanceReport, "REPORT_REQUIRED")
            report = report.to_dict()
        report = validate_contract("PerformanceReport", report)
        require(report["report_id"] not in seen, "DUPLICATE_REPORT")
        seen.add(report["report_id"])
        require(all(snapshot_refs.get(ref["snapshot_id"]) == ref for ref in report["snapshot_refs"]),
                "UNRESOLVED_REPORT_SNAPSHOT")
        body = {key: value for key, value in report.items() if key != "report_id"}
        body["account_ref"] = _context(report)["account_ref"]
        report_sources.append({"report_ref": {"report_id": report["report_id"]}, "body": body})
    return facts, sorted(report_sources, key=lambda item: item["report_ref"]["report_id"])


def _report_arithmetic(body: dict, facts: list[dict]) -> None:
    """Check the existing report API's SUM/MEAN/COUNT against archived facts.

    v1 has no aggregation field; accept its three supported calculations, never
    an arbitrary resealed numerator. A supplied denominator is a declaration,
    not independently verified exposure. Source-derived denominators are retained
    by the report; reopening cannot claim a new raw-source calculation.
    """
    measurement = body["measurement"]
    values = [fact["value"] for fact in facts if fact["status"] == "AVAILABLE"]
    if not values:
        require(measurement["status"] == "NOT_AVAILABLE"
                and all(measurement[key] is None for key in ("value", "numerator", "denominator")),
                "REPORT_ARITHMETIC_MISMATCH")
        return
    numerator, denominator = measurement["numerator"], measurement["denominator"]
    require(numerator in (sum(values), sum(values) / len(values), float(len(values))),
            "REPORT_ARITHMETIC_MISMATCH")
    if denominator == 0 or (denominator is None and body["metric"]["denominator"] is not None):
        require(measurement["status"] == "INSUFFICIENT_DATA" and measurement["value"] is None,
                "REPORT_ARITHMETIC_MISMATCH")
    else:
        expected = numerator if denominator is None else numerator / denominator
        require(measurement["status"] == "AVAILABLE" and measurement["value"] == expected
                and measurement["reason"] is None, "REPORT_ARITHMETIC_MISMATCH")


def _validate_bundle(bundle: dict) -> None:
    require(set(bundle) == {"schema_version", "registry_version", "verification_scope", "hypothesis",
                           "evidence", "record", "revision", "observation_sources", "report_sources"},
            "INVALID_REGISTRY_ENTRY")
    require(bundle["schema_version"] == "LearningRegistryEntry.v1" and bundle["registry_version"] == REGISTRY_VERSION
            and bundle["verification_scope"] == "LOCAL_REFERENCES_AND_HASHES_ONLY", "REGISTRY_VERSION_MISMATCH")
    hypothesis = validate_contract("LearningHypothesis", bundle["hypothesis"])
    record = validate_contract("LearningRecord", bundle["record"])
    revision = validate_contract("LearningRevision", bundle["revision"])
    require(type(bundle["evidence"]) is list and len(bundle["evidence"]) <= 200, "EVIDENCE_LIMIT")
    evidence = [validate_contract("LearningEvidence", value) for value in bundle["evidence"]]
    require(record["hypothesis_ref"] == {"hypothesis_id": hypothesis["hypothesis_id"]}, "HYPOTHESIS_REF_MISMATCH")
    require(revision["learning_key"] == record["learning_key"]
            and revision["record_ref"] == {"record_id": record["record_id"]}, "REVISION_RECORD_MISMATCH")
    _alias_context(hypothesis)
    _key(revision["actor_ref"], "INTERNAL_ACTOR_ALIAS_REQUIRED")
    _text(revision["actor_ref"])
    _key(hypothesis["creative_ref"]["creative_id"], "INTERNAL_CREATIVE_ALIAS_REQUIRED")
    _text(hypothesis["creative_ref"]["creative_id"])
    _text(hypothesis["creative_ref"]["version"])
    _privacy(hypothesis)
    _privacy(record)
    _privacy(revision)
    require(type(bundle["observation_sources"]) is list and len(bundle["observation_sources"]) <= 1000,
            "OBSERVATION_SOURCE_LIMIT")
    facts = {}
    for fact in bundle["observation_sources"]:
        require(type(fact) is dict and _SOURCE_VALIDATOR.is_valid(fact), "INVALID_OBSERVATION_SOURCE")
        _alias_context(fact["context"])
        _privacy(fact)
        ref = fact["observation_ref"]
        require(ref["raw_sha256"] == fact["source"]["raw_sha256"]
                and ref["metric_definition_sha256"] == digest(fact["metric"]), "SOURCE_IDENTITY_MISMATCH")
        require(fact["metric"]["platform"] == fact["context"]["platform"]
                and fact["metric"]["cohort"] == fact["context"]["cohort"], "SOURCE_METRIC_CONTEXT_MISMATCH")
        require((fact["status"] == "NOT_AVAILABLE" and fact["value"] is None)
                or (fact["status"] == "AVAILABLE" and type(fact["value"]) in (int, float)),
                "SOURCE_ZERO_MISSING_MISMATCH")
        require(digest(ref) not in facts, "DUPLICATE_OBSERVATION_SOURCE")
        facts[digest(ref)] = fact
    require(type(bundle["report_sources"]) is list and len(bundle["report_sources"]) <= MAX_REPORTS, "REPORT_LIMIT")
    reports = {}
    for proof in bundle["report_sources"]:
        require(type(proof) is dict and set(proof) == {"report_ref", "body"}, "INVALID_REPORT_SOURCE")
        ref = proof["report_ref"]
        require(type(ref) is dict and set(ref) == {"report_id"} and type(ref["report_id"]) is str
                and re.fullmatch(r"a11-report-[a-f0-9]{64}", ref["report_id"]) is not None, "INVALID_REPORT_REF")
        body = seal_contract("PerformanceReport", proof["body"])
        _alias_context(body)
        _privacy(body)
        require(ref["report_id"] not in reports, "DUPLICATE_REPORT")
        reports[ref["report_id"]] = body
        report_facts = []
        for oref in body["evidence_refs"]:
            require(digest(oref) in facts, "UNRESOLVED_REPORT_OBSERVATION")
            fact = facts[digest(oref)]
            require(fact["context"] == {key: body[key] for key in _CONTEXT_KEYS}
                    and fact["metric"] == body["metric"], "REPORT_SOURCE_CONTEXT_MISMATCH")
            report_facts.append(fact)
        expected_srefs = [{key: fact["observation_ref"][key] for key in
                           ("snapshot_id", "import_id", "context_sha256", "raw_sha256")} for fact in report_facts]
        require({digest(ref) for ref in expected_srefs} == {digest(ref) for ref in body["snapshot_refs"]},
                "REPORT_SNAPSHOT_MISMATCH")
        require(body["available_count"] == sum(f["status"] == "AVAILABLE" for f in report_facts)
                and body["not_available_count"] == sum(f["status"] == "NOT_AVAILABLE" for f in report_facts),
                "REPORT_COUNTS_MISMATCH")
        require(body["measurement"]["unit"] == body["metric"]["unit"], "REPORT_UNIT_MISMATCH")
        if not body["available_count"]:
            require(body["measurement"]["status"] == "NOT_AVAILABLE", "REPORT_MISSINGNESS_MISMATCH")
        _report_arithmetic(body, report_facts)
    used_reports, seen_observations = set(), set()
    primary = {ref["evidence_id"] for ref in record["evidence_refs"]}
    counters = {ref["evidence_id"] for ref in record["counterevidence_refs"]}
    require(not primary.intersection(counters), "OVERLAPPING_EVIDENCE_ROLES")
    require(len({e["evidence_id"] for e in evidence}) == len(evidence)
            and primary | counters == {e["evidence_id"] for e in evidence}, "EVIDENCE_SET_MISMATCH")
    for item in evidence:
        _privacy(item)
        require(item["hypothesis_ref"] == record["hypothesis_ref"], "EVIDENCE_HYPOTHESIS_MISMATCH")
        ref = item["observation_ref"]
        require(digest(ref) in facts, "UNRESOLVED_OBSERVATION_REF")
        fact = facts[digest(ref)]
        require(ref["observation_id"] not in seen_observations, "DUPLICATE_EVIDENCE_OBSERVATION")
        seen_observations.add(ref["observation_id"])
        state = fact["status"]
        report_ref = item.get("report_ref")
        if report_ref is not None:
            report_id = report_ref["report_id"]
            require(report_id in reports and ref in reports[report_id]["evidence_refs"], "UNRESOLVED_EVIDENCE_REPORT")
            used_reports.add(report_id)
            if state == "AVAILABLE":
                state = reports[report_id]["measurement"]["status"]
        if fact["context"] != {key: hypothesis[key] for key in _CONTEXT_KEYS}:
            state = "NOT_COMPARABLE"
        require(item["evidence_state"] == state, "EVIDENCE_STATE_MISMATCH")
        require(state == "AVAILABLE" or item["role"] == "INCONCLUSIVE", "UNAVAILABLE_EVIDENCE_CANNOT_SUPPORT")
        require((item["role"] == "COUNTEREVIDENCE") == (item["evidence_id"] in counters), "EVIDENCE_ROLE_MISMATCH")
    require(used_reports == set(reports), "UNUSED_REPORT_SOURCE")
    all_refs = list(_refs({key: bundle[key] for key in ("hypothesis", "evidence", "record", "report_sources")}))
    require({digest(ref) for ref in all_refs} == set(facts), "UNRESOLVED_OR_UNUSED_SOURCE")
    supporting = sum(item["role"] == "SUPPORTING" for item in evidence)
    negative = sum(item["role"] == "COUNTEREVIDENCE" for item in evidence)
    inconclusive = sum(item["role"] == "INCONCLUSIVE" for item in evidence)
    status = record["status"]
    require(status != "PROPOSED" or not evidence, "PROPOSED_WITH_EVIDENCE")
    require(status != "SUPPORTED" or (supporting > 0 and not negative and not inconclusive), "UNSUPPORTED_STATUS")
    require(status != "CONTRADICTED" or (negative > 0 and not supporting and not inconclusive), "UNSUPPORTED_STATUS")
    require(status != "MIXED" or (supporting > 0 and negative > 0), "UNSUPPORTED_STATUS")
    require((revision["operation"] == "RETRACT") == (status == "RETIRED"), "RETRACTION_STATUS_MISMATCH")


def _prepare(*, hypothesis, evidence, record, revision, snapshots, reports) -> bytes:
    require(type(evidence) in (list, tuple) and len(evidence) <= 200, "EVIDENCE_LIMIT")
    # Bound and detach caller-controlled contracts before any source processing.
    inputs = {"hypothesis": hypothesis, "evidence": list(evidence), "record": record, "revision": revision}
    require(len(bounded_bytes(inputs)) <= MAX_ENTRY_BYTES, "REGISTRY_ENTRY_TOO_LARGE")
    inputs = parse_json(canonical_bytes(inputs))
    # Validate before sorting/indexing potentially malformed references.
    for name, key in (("LearningHypothesis", "hypothesis"), ("LearningRecord", "record"), ("LearningRevision", "revision")):
        validate_contract(name, inputs[key])
    inputs["evidence"] = sorted((validate_contract("LearningEvidence", item) for item in inputs["evidence"]),
                                key=lambda item: item["evidence_id"])
    facts, report_sources = _sources(snapshots, reports)
    references = list(_refs({**inputs, "report_sources": report_sources}))
    require(all(digest(ref) in facts for ref in references), "UNRESOLVED_OBSERVATION_REF")
    used = {digest(ref): facts[digest(ref)] for ref in references}
    bundle = {"schema_version": "LearningRegistryEntry.v1", "registry_version": REGISTRY_VERSION,
              "verification_scope": "LOCAL_REFERENCES_AND_HASHES_ONLY", **inputs,
              "observation_sources": [used[key] for key in sorted(used)], "report_sources": report_sources}
    raw = bounded_bytes(bundle)
    require(len(raw) <= MAX_ENTRY_BYTES, "REGISTRY_ENTRY_TOO_LARGE")
    _validate_bundle(bundle)
    return raw


def _lineage(current: dict, previous: dict | None) -> None:
    revision, record = current["revision"], current["record"]
    if previous is None:
        require(revision["revision"] == 1 and revision["operation"] == "CREATE"
                and revision["previous_revision_ref"] is None, "INVALID_INITIAL_REVISION")
        return
    prev = previous["revision"]
    require(revision["revision"] == prev["revision"] + 1
            and revision["previous_revision_ref"] == {"revision_id": prev["revision_id"], "revision": prev["revision"]},
            "REVISION_CONFLICT")
    require(previous["record"]["status"] != "RETIRED", "RETIRED_LEARNING_IS_TERMINAL")
    require(_instant(revision["recorded_at"]) >= _instant(prev["recorded_at"]), "REVISION_TIME_REVERSED")
    if revision["operation"] in {"UPDATE", "RETRACT"}:
        require(record["hypothesis_ref"] == previous["record"]["hypothesis_ref"], "HYPOTHESIS_CHANGE_REQUIRES_CORRECTION")
        for key in ("evidence_refs", "counterevidence_refs", "confounders"):
            require(all(item in record[key] for item in previous["record"][key]), "EVIDENCE_REMOVAL_REQUIRES_CORRECTION")


def _event_hash(seq, revision_id, key, revision, previous_revision_id, payload_sha256, previous_event_hash) -> str:
    return digest({"registry_version": REGISTRY_VERSION, "seq": seq, "revision_id": revision_id,
                   "learning_key": key, "revision": revision, "previous_revision_id": previous_revision_id,
                   "payload_sha256": payload_sha256, "previous_event_hash": previous_event_hash})


@dataclass(frozen=True, slots=True)
class LearningEntry:
    """Immutable bytes and local chain identity; dictionary reads are detached."""

    _raw: bytes
    sequence: int
    event_hash: str

    def to_bytes(self) -> bytes:
        return self._raw

    def to_dict(self) -> dict:
        return parse_json(self._raw)

    @property
    def revision_id(self) -> str:
        return self.to_dict()["revision"]["revision_id"]


def validate_history(entries) -> tuple[LearningEntry, ...]:
    """Validate a complete archived chain, without reopening A09 or a database.

    A caller-held export ID is needed to detect replacement or suffix truncation.
    Local hashes alone are not an external attestation of the observations.
    """
    require(type(entries) in (list, tuple) and len(entries) <= MAX_EVENTS, "REGISTRY_CAPACITY_EXCEEDED")
    previous_hash, latest, total = _ZERO, {}, 0
    for sequence, entry in enumerate(entries, 1):
        require(type(entry) is LearningEntry and type(entry.sequence) is int
                and entry.sequence == sequence, "REGISTRY_CHAIN_MISMATCH")
        raw = entry.to_bytes()
        require(type(raw) is bytes and 0 < len(raw) <= MAX_ENTRY_BYTES, "REGISTRY_ENTRY_TOO_LARGE")
        total += len(raw)
        require(total <= MAX_DATABASE_BYTES, "REGISTRY_CAPACITY_EXCEEDED")
        bundle = parse_json(raw)
        require(bounded_bytes(bundle) == raw, "REGISTRY_NONCANONICAL_PAYLOAD")
        _validate_bundle(bundle)
        revision = bundle["revision"]
        previous = latest.get(revision["learning_key"])
        _lineage(bundle, previous)
        previous_id = None if previous is None else previous["revision"]["revision_id"]
        require(entry.event_hash == _event_hash(sequence, revision["revision_id"], revision["learning_key"],
                revision["revision"], previous_id, sha256_bytes(raw), previous_hash), "REGISTRY_CHAIN_MISMATCH")
        previous_hash = entry.event_hash
        latest[revision["learning_key"]] = bundle
    return tuple(entries)


class LearningRegistry:
    """Independent A11 database. Reads never create files or change schemas.

    Writes serialize with BEGIN IMMEDIATE, validate the entire bounded existing
    history, then append one atomic event. Exact old retries return their original
    event even after newer revisions; different content never reuses an identity.
    A stale writer must supply a newly reviewed revision, not retry blindly.
    """

    def __init__(self, path: str | Path):
        self.path = path

    def _path(self, *, write: bool) -> Path:
        require(type(self.path) is str or isinstance(self.path, Path), "INVALID_REGISTRY_PATH")
        require(0 < len(str(self.path)) <= 2048, "INVALID_REGISTRY_PATH")
        try:
            path = local_path(self.path)
            require(path.parent.is_dir(), "REGISTRY_PARENT_NOT_FOUND")
            if path.exists():
                info = path.stat()
                require(path.is_file() and info.st_nlink == 1 and info.st_size <= MAX_DATABASE_BYTES,
                        "REGISTRY_FILE_UNSAFE_OR_TOO_LARGE")
            else:
                require(write, "REGISTRY_NOT_FOUND")
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = local_path(str(path) + suffix)
                if sidecar.exists():
                    info = sidecar.stat()
                    require(sidecar.is_file() and info.st_nlink == 1 and info.st_size <= MAX_DATABASE_BYTES,
                            "REGISTRY_SIDECAR_UNSAFE")
            return path
        except AnalyticsError as exc:
            raise LearningError("REGISTRY_UNSAFE_PATH") from exc
        except OSError as exc:
            raise LearningError("REGISTRY_FILE_ERROR") from exc

    @contextmanager
    def _connect(self, *, write: bool = False):
        path, db = self._path(write=write), None
        try:
            db = sqlite3.connect(path.as_uri() + ("?mode=rwc" if write else "?mode=ro"),
                                 uri=True, timeout=5, isolation_level=None)
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            if not write:
                db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            app_id, version = db.execute("PRAGMA application_id").fetchone()[0], db.execute("PRAGMA user_version").fetchone()[0]
            if write and app_id == 0 and version == 0:
                require(not db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone(), "REGISTRY_FORMAT_MISMATCH")
                for sql in _DDL:
                    db.execute(sql)
                db.execute(f"PRAGMA application_id={_APP_ID}")
                db.execute("PRAGMA user_version=1")
            else:
                require(app_id == _APP_ID and version == 1, "REGISTRY_FORMAT_MISMATCH")
            require(_schema_manifest(db) == _EXPECTED_SCHEMA, "REGISTRY_SCHEMA_MISMATCH")
            require(db.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok", "REGISTRY_DATABASE_CORRUPT")
            yield db
            db.commit()
        except LearningError:
            raise
        except (sqlite3.Error, AnalyticsError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            raise LearningError("REGISTRY_DATABASE_OR_INTEGRITY_ERROR") from exc
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    @staticmethod
    def _read(db) -> list[LearningEntry]:
        count, size = db.execute("SELECT count(*),COALESCE(sum(length(payload)),0) FROM a11_events").fetchone()
        require(count <= MAX_EVENTS and size <= MAX_DATABASE_BYTES, "REGISTRY_CAPACITY_EXCEEDED")
        rows = db.execute("SELECT seq,revision_id,learning_key,revision,previous_revision_id,"
                          "CASE WHEN length(payload)<=? THEN payload ELSE NULL END,payload_sha256,"
                          "previous_event_hash,event_hash FROM a11_events ORDER BY seq LIMIT ?",
                          (MAX_ENTRY_BYTES, MAX_EVENTS + 1))
        entries, latest, previous_hash = [], {}, _ZERO
        for row in rows:
            seq, rid, key, number, previous_id, raw, payload_hash, previous_event_hash, event_hash = row
            require(type(raw) is bytes and 0 < len(raw) <= MAX_ENTRY_BYTES
                    and sha256_bytes(raw) == payload_hash, "REGISTRY_PAYLOAD_HASH_MISMATCH")
            require(seq == len(entries) + 1 and previous_event_hash == previous_hash
                    and event_hash == _event_hash(seq, rid, key, number, previous_id, payload_hash, previous_event_hash),
                    "REGISTRY_CHAIN_MISMATCH")
            bundle = parse_json(raw)
            require(bounded_bytes(bundle) == raw, "REGISTRY_NONCANONICAL_PAYLOAD")
            _validate_bundle(bundle)
            revision = bundle["revision"]
            require((rid, key, number, previous_id) == (
                revision["revision_id"], revision["learning_key"], revision["revision"],
                None if revision["previous_revision_ref"] is None else revision["previous_revision_ref"]["revision_id"]),
                "REGISTRY_INDEX_MISMATCH")
            _lineage(bundle, latest.get(key))
            latest[key] = bundle
            entries.append(LearningEntry(raw, seq, event_hash))
            previous_hash = event_hash
        return entries

    def append(self, *, hypothesis: dict, evidence, record: dict, revision: dict,
               snapshots, reports=()) -> LearningEntry:
        raw = _prepare(hypothesis=hypothesis, evidence=evidence, record=record, revision=revision,
                       snapshots=snapshots, reports=reports)
        bundle = parse_json(raw)
        revision = bundle["revision"]
        key, rid = revision["learning_key"], revision["revision_id"]
        with self._connect(write=True) as db:
            entries = self._read(db)
            previous = None
            for entry in entries:
                if entry.revision_id == rid:
                    require(entry.to_bytes() == raw, "REGISTRY_REPLAY_MISMATCH")
                    return entry
                value = entry.to_dict()
                if value["revision"]["learning_key"] == key:
                    previous = value
            _lineage(bundle, previous)
            require(len(entries) < MAX_EVENTS, "REGISTRY_CAPACITY_EXCEEDED")
            require(sum(len(e.to_bytes()) for e in entries) + len(raw) <= MAX_DATABASE_BYTES,
                    "REGISTRY_CAPACITY_EXCEEDED")
            seq, payload_hash = len(entries) + 1, sha256_bytes(raw)
            previous_event_hash = entries[-1].event_hash if entries else _ZERO
            previous_id = None if previous is None else previous["revision"]["revision_id"]
            event_hash = _event_hash(seq, rid, key, revision["revision"], previous_id, payload_hash, previous_event_hash)
            db.execute("INSERT INTO a11_events VALUES(?,?,?,?,?,?,?,?,?)",
                       (seq, rid, key, revision["revision"], previous_id, raw, payload_hash, previous_event_hash, event_hash))
            # Verify the committed representation while still able to roll back.
            return self._read(db)[-1]

    def inspect(self) -> tuple[LearningEntry, ...]:
        """Verify and return a consistent bounded snapshot of every revision."""
        with self._connect() as db:
            return tuple(self._read(db))

    def history(self, learning_key: str) -> tuple[LearningEntry, ...]:
        _key(learning_key)
        return tuple(entry for entry in self.inspect() if entry.to_dict()["revision"]["learning_key"] == learning_key)

    def get(self, learning_key: str, *, revision: int | None = None) -> LearningEntry:
        require(revision is None or (type(revision) is int and 1 <= revision <= 1000000), "INVALID_REVISION_NUMBER")
        entries = self.history(learning_key)
        matches = entries if revision is None else tuple(entry for entry in entries
                                                        if entry.to_dict()["revision"]["revision"] == revision)
        require(bool(matches), "LEARNING_REVISION_NOT_FOUND")
        return matches[-1]
