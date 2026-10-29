"""Transactional, content-addressed, append-only SQLite analytics evidence.

Raw BLOBs, import records and observations commit together. Exact replays are
scoped by the full plan, mapping, parser context and explicit correction lineage.
Hashes detect modification; this local database is not a provider attestation or
an externally anchored, tamper-proof audit system.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from copy import deepcopy

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes, timestamp_utc

from .files import MAX_FILE_BYTES, MAX_PLAN_BYTES, ledger_path
from .importer import PreparedImport, prepare_bytes, validate_file
from .model import (AnalyticsError, LedgerError, digest, parse_json, require, scope,
                    validate_contract, validate_plan)

_APP_ID = 0x41303931
_VERSION = 1
_IMPORT_ID = re.compile(r"a09-[a-f0-9]{64}")
_SHA = re.compile(r"[a-f0-9]{64}")
_MAX_RECORD = MAX_PLAN_BYTES + 64 * 1024
_MAX_OBSERVATION = 32 * 1024
_DDL = (
    """CREATE TABLE raw_evidence(
        sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
        size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=8388608),
        payload BLOB NOT NULL CHECK(length(payload)=size_bytes))""",
    """CREATE TABLE imports(
        seq INTEGER PRIMARY KEY AUTOINCREMENT, import_id TEXT UNIQUE NOT NULL,
        raw_sha256 TEXT NOT NULL REFERENCES raw_evidence(sha256),
        context_sha256 TEXT NOT NULL, scope_sha256 TEXT NOT NULL,
        parent_import_id TEXT REFERENCES imports(import_id), relationship TEXT NOT NULL,
        payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL, recorded_at TEXT NOT NULL)""",
    """CREATE TABLE observations(
        import_id TEXT NOT NULL REFERENCES imports(import_id), ordinal INTEGER NOT NULL,
        observation_id TEXT UNIQUE NOT NULL, payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL,
        PRIMARY KEY(import_id,ordinal))""",
    "CREATE INDEX imports_scope ON imports(scope_sha256,seq)",
    "CREATE INDEX imports_source_context ON imports(raw_sha256,context_sha256)",
    "CREATE INDEX imports_lineage ON imports(parent_import_id,seq)",
)


def _check(ok: bool, code: str) -> None:
    if not ok:
        raise LedgerError(code)


class AnalyticsLedger:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def _connect(self, *, write: bool = False):
        path = ledger_path(self.path, create=write)
        db = None
        try:
            db = sqlite3.connect(str(path) if write else path.as_uri() + "?mode=ro",
                                 uri=not write, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")  # One consistent snapshot for raw + normalized evidence.
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if write and app_id == 0 and version == 0:
                _check(not db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone(), "LEDGER_FORMAT_MISMATCH")
                for sql in _DDL:
                    db.execute(sql)
                for table in ("raw_evidence", "imports", "observations"):
                    for operation in ("UPDATE", "DELETE"):
                        db.execute(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                                   "BEGIN SELECT RAISE(ABORT,'A09_APPEND_ONLY'); END")
                db.execute(f"PRAGMA application_id={_APP_ID}")
                db.execute(f"PRAGMA user_version={_VERSION}")
            else:
                _check(app_id == _APP_ID and version == _VERSION, "LEDGER_FORMAT_MISMATCH")
            yield db
            db.commit()
        except sqlite3.Error as exc:
            raise LedgerError("LEDGER_DATABASE_ERROR") from exc
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    @staticmethod
    def _raw(db, sha: str) -> bytes:
        row = db.execute("SELECT size_bytes,length(payload) FROM raw_evidence WHERE sha256=?", (sha,)).fetchone()
        _check(row is not None, "RAW_EVIDENCE_NOT_FOUND")
        _check(0 < row[0] <= MAX_FILE_BYTES and row[0] == row[1], "RAW_EVIDENCE_SIZE_MISMATCH")
        raw = db.execute("SELECT payload FROM raw_evidence WHERE sha256=?", (sha,)).fetchone()[0]
        _check(isinstance(raw, bytes) and sha256_bytes(raw) == sha, "RAW_EVIDENCE_HASH_MISMATCH")
        return raw

    @staticmethod
    def _record(db, import_id: str) -> tuple[sqlite3.Row, dict]:
        require(isinstance(import_id, str) and _IMPORT_ID.fullmatch(import_id) is not None, "INVALID_IMPORT_ID")
        row = db.execute(
            "SELECT seq,import_id,raw_sha256,context_sha256,scope_sha256,parent_import_id,relationship,"
            "CASE WHEN length(payload)<=? THEN payload ELSE NULL END AS payload,payload_sha256 "
            "FROM imports WHERE import_id=?", (_MAX_RECORD, import_id)).fetchone()
        require(row is not None, "IMPORT_NOT_FOUND")
        raw = row["payload"]
        _check(isinstance(raw, bytes) and sha256_bytes(raw) == row["payload_sha256"], "IMPORT_RECORD_HASH_MISMATCH")
        try:
            record = parse_json(raw)
            validate_contract("AnalyticsImport", record)
            _check(record["status"] == "IMPORTED", "IMPORT_STATUS_CORRUPT")
            _check(record["import_id"] == import_id
                   and all(record[k] == row[k] for k in ("raw_sha256", "context_sha256", "scope_sha256"))
                   and record["lineage"] == {"relationship": row["relationship"], "parent_import_id": row["parent_import_id"]},
                   "IMPORT_INDEX_MISMATCH")
            context = {key: record[key] for key in ("plan", "mapping", "format", "dialect", "source_kind")}
            _check(record["context_sha256"] == digest(context)
                   and record["scope_sha256"] == digest(scope(record["plan"]))
                   and record["raw_ref"] == "sha256:" + record["raw_sha256"]
                   and import_id == "a09-" + digest({"raw_sha256": record["raw_sha256"],
                                                    "context_sha256": record["context_sha256"],
                                                    "lineage": record["lineage"]}), "IMPORT_IDENTITY_MISMATCH")
            return row, record
        except LedgerError:
            raise
        except AnalyticsError as exc:
            raise LedgerError("IMPORT_RECORD_CORRUPT") from exc

    @staticmethod
    def _lineage(child: dict, parent: dict) -> None:
        require(child["scope_sha256"] == parent["scope_sha256"], "LINEAGE_SCOPE_MISMATCH")
        if child["lineage"]["relationship"] == "CORRECTION":
            require(child["context_sha256"] == parent["context_sha256"], "CORRECTION_CONTEXT_MISMATCH")
            require(child["raw_sha256"] != parent["raw_sha256"], "CORRECTION_SOURCE_UNCHANGED")
        else:
            require(child["raw_sha256"] == parent["raw_sha256"], "REPROCESS_SOURCE_MISMATCH")
            require(child["context_sha256"] != parent["context_sha256"], "REPROCESS_CONTEXT_UNCHANGED")

    def _get(self, db, import_id: str) -> dict:
        row, record = self._record(db, import_id)
        raw = self._raw(db, record["raw_sha256"])
        lineage = record["lineage"]
        try:
            prepared = prepare_bytes(
                raw, plan=record["plan"], profile=record["mapping"], file_format=record["format"],
                dialect=record["dialect"], source_kind=record["source_kind"],
                correction_of=lineage["parent_import_id"] if lineage["relationship"] == "CORRECTION" else None,
                reprocess_of=lineage["parent_import_id"] if lineage["relationship"] == "REPROCESS" else None)
            expected = {**prepared.record, "status": "IMPORTED"}
            _check(record == expected, "IMPORT_REPLAY_MISMATCH")
            if lineage["parent_import_id"]:
                parent_row, parent = self._record(db, lineage["parent_import_id"])
                _check(parent_row["seq"] < row["seq"], "LINEAGE_ORDER_CORRUPT")
                self._lineage(record, parent)
        except LedgerError:
            raise
        except AnalyticsError as exc:
            raise LedgerError("IMPORT_REPLAY_INVALID") from exc
        stored = db.execute(
            "SELECT ordinal,observation_id,CASE WHEN length(payload)<=? THEN payload ELSE NULL END AS payload,"
            "payload_sha256 FROM observations WHERE import_id=? ORDER BY ordinal LIMIT ?",
            (_MAX_OBSERVATION, import_id, record["observation_count"] + 1)).fetchall()
        _check(len(stored) == len(prepared.observations), "OBSERVATION_COUNT_MISMATCH")
        for ordinal, (actual, observation) in enumerate(zip(stored, prepared.observations), 1):
            _check(actual["ordinal"] == ordinal and actual["observation_id"] == observation["observation_id"]
                   and actual["payload"] == canonical_bytes(observation)
                   and actual["payload_sha256"] == digest(observation), "OBSERVATION_INTEGRITY_MISMATCH")
        return {"import": record, "observations": list(prepared.observations)}

    def _store(self, prepared: PreparedImport) -> dict:
        record = {**deepcopy(prepared.record), "status": "IMPORTED"}
        import_id = record["import_id"]
        with self._connect(write=True) as db:
            if db.execute("SELECT 1 FROM imports WHERE import_id=?", (import_id,)).fetchone():
                return {**self._get(db, import_id), "duplicate": True}
            parent_id = record["lineage"]["parent_import_id"]
            if parent_id:
                parent = self._get(db, parent_id)["import"]
                self._lineage(record, parent)
            if db.execute("SELECT 1 FROM raw_evidence WHERE sha256=?", (record["raw_sha256"],)).fetchone():
                _check(self._raw(db, record["raw_sha256"]) == prepared.raw, "RAW_EVIDENCE_HASH_MISMATCH")
            else:
                db.execute("INSERT INTO raw_evidence VALUES(?,?,?)",
                           (record["raw_sha256"], len(prepared.raw), prepared.raw))
            db.execute(
                "INSERT INTO imports(import_id,raw_sha256,context_sha256,scope_sha256,parent_import_id,relationship,"
                "payload,payload_sha256,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (import_id, record["raw_sha256"], record["context_sha256"], record["scope_sha256"],
                 parent_id, record["lineage"]["relationship"], canonical_bytes(record), digest(record), timestamp_utc()))
            for ordinal, observation in enumerate(prepared.observations, 1):
                db.execute("INSERT INTO observations VALUES(?,?,?,?,?)",
                           (import_id, ordinal, observation["observation_id"],
                            canonical_bytes(observation), digest(observation)))
            return {"import": record, "observations": list(prepared.observations), "duplicate": False}

    def import_file(self, path, **kwargs) -> dict:
        return self._store(validate_file(path, **kwargs))

    def reprocess(self, import_id: str, *, plan: dict, profile: str | dict,
                  dialect: str | None = None) -> dict:
        # Reopen the saved raw source, rather than trusting a caller's edited file.
        with self._connect() as db:
            parent = self._get(db, import_id)["import"]
            raw = self._raw(db, parent["raw_sha256"])
        prepared = prepare_bytes(raw, plan=plan, profile=profile, file_format=parent["format"],
                                 dialect=parent["dialect"] if dialect is None else dialect,
                                 source_kind=parent["source_kind"], reprocess_of=import_id)
        return self._store(prepared)

    def inspect(self, import_id: str) -> dict:
        with self._connect() as db:
            return self._get(db, import_id)

    def raw_bytes(self, sha256: str) -> bytes:
        require(isinstance(sha256, str) and _SHA.fullmatch(sha256) is not None, "INVALID_RAW_SHA256")
        with self._connect() as db:
            return self._raw(db, sha256)

    def history(self, *, plan: dict, limit: int = 20, offset: int = 0) -> dict:
        plan = validate_plan(plan)
        require(type(limit) is int and 1 <= limit <= 100 and type(offset) is int
                and 0 <= offset <= 1000000, "INVALID_HISTORY_PAGE")
        with self._connect() as db:
            ids = db.execute("SELECT import_id FROM imports WHERE scope_sha256=? ORDER BY seq LIMIT ? OFFSET ?",
                             (digest(scope(plan)), limit + 1, offset)).fetchall()
            records = [self._get(db, row[0])["import"] for row in ids[:limit]]
        return {"imports": records, "count": len(records),
                "next_offset": offset + limit if len(ids) > limit else None}
