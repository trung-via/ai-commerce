"""Transactional, content-addressed, append-only SQLite signal evidence.

Raw BLOBs, import records and normalized signals commit together in an immediate
transaction. Exact replays are scoped by the full snapshot, mapping, parser dialect
and explicit correction lineage. Cryptographic hashes detect tamper; this local
ledger is an offline audit journal, not live external provider attestation.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ..core.models import (
    OpportunityError,
    SignalLedgerError,
    digest,
    parse_json,
    require,
    scope,
)
from .files import MAX_FILE_BYTES, MAX_ROW_BYTES, MAX_SNAPSHOT_BYTES, ledger_path, read_local
from .importer import PreparedSignalImport, prepare_bytes

_APP_ID = 0x41313031  # ASCII 'A101'
_VERSION = 1
_MAX_RECORD = MAX_SNAPSHOT_BYTES + 64 * 1024
_MAX_SIGNAL = MAX_ROW_BYTES

_DDL = (
    """CREATE TABLE raw_evidence(
        sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
        size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=8388608),
        payload BLOB NOT NULL CHECK(length(payload)=size_bytes))""",
    """CREATE TABLE imports(
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        import_id TEXT UNIQUE NOT NULL,
        raw_sha256 TEXT NOT NULL REFERENCES raw_evidence(sha256),
        context_sha256 TEXT NOT NULL,
        scope_sha256 TEXT NOT NULL,
        parent_import_id TEXT REFERENCES imports(import_id),
        relationship TEXT NOT NULL,
        payload BLOB NOT NULL,
        payload_sha256 TEXT NOT NULL,
        recorded_at TEXT NOT NULL)""",
    """CREATE TABLE signals(
        import_id TEXT NOT NULL REFERENCES imports(import_id),
        ordinal INTEGER NOT NULL,
        signal_id TEXT UNIQUE NOT NULL,
        payload BLOB NOT NULL,
        payload_sha256 TEXT NOT NULL,
        PRIMARY KEY(import_id, ordinal))""",
    "CREATE INDEX imports_scope ON imports(scope_sha256, seq)",
    "CREATE INDEX imports_source_context ON imports(raw_sha256, context_sha256)",
    "CREATE INDEX imports_lineage ON imports(parent_import_id, seq)",
    "CREATE INDEX signals_import_ordinal ON signals(import_id, ordinal)",
)


def _check(ok: bool, code: str) -> None:
    if not ok:
        raise SignalLedgerError(code)


class SignalLedger:
    def __init__(self, path: Path | str):
        self.path = path

    @contextmanager
    def _connect(self, *, write: bool = False):
        path = ledger_path(self.path, create=write)
        db = None
        try:
            db = sqlite3.connect(
                str(path) if write else path.as_uri() + "?mode=ro",
                uri=not write,
                timeout=5,
                isolation_level=None,
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")  # Snapshot consistency for raw + signals
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if write and app_id == 0 and version == 0:
                _check(not db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone(), "LEDGER_FORMAT_MISMATCH")
                for sql in _DDL:
                    db.execute(sql)
                for table in ("raw_evidence", "imports", "signals"):
                    for operation in ("UPDATE", "DELETE"):
                        db.execute(
                            f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                            "BEGIN SELECT RAISE(ABORT, 'A10_APPEND_ONLY'); END"
                        )
                db.execute(f"PRAGMA application_id={_APP_ID}")
                db.execute(f"PRAGMA user_version={_VERSION}")
            else:
                _check(app_id == _APP_ID and version == _VERSION, "LEDGER_FORMAT_MISMATCH")
            yield db
            db.commit()
        except sqlite3.Error as exc:
            raise SignalLedgerError("LEDGER_DATABASE_ERROR") from exc
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    @staticmethod
    def _raw(db, sha: str) -> bytes:
        row = db.execute(
            "SELECT size_bytes, length(payload), payload FROM raw_evidence WHERE sha256=?",
            (sha,),
        ).fetchone()
        _check(row is not None, "RAW_EVIDENCE_NOT_FOUND")
        _check(0 < row[0] <= MAX_FILE_BYTES and row[0] == row[1], "RAW_EVIDENCE_SIZE_MISMATCH")
        payload = bytes(row[2])
        _check(sha256_bytes(payload) == sha, "RAW_EVIDENCE_TAMPERED")
        return payload

    @staticmethod
    def _record(db, import_id: str) -> tuple[sqlite3.Row, dict[str, Any]]:
        row = db.execute(
            "SELECT seq, import_id, raw_sha256, context_sha256, scope_sha256, parent_import_id, "
            "relationship, payload, payload_sha256, recorded_at FROM imports WHERE import_id=?",
            (import_id,),
        ).fetchone()
        _check(row is not None, "IMPORT_NOT_FOUND")
        payload = bytes(row["payload"])
        _check(len(payload) <= _MAX_RECORD, "IMPORT_PAYLOAD_TOO_LARGE")
        _check(sha256_bytes(payload) == row["payload_sha256"], "IMPORT_PAYLOAD_TAMPERED")
        try:
            record = json.loads(payload.decode("utf-8"))
            _check(
                record["import_id"] == import_id
                and all(record[k] == row[k] for k in ("raw_sha256", "context_sha256", "scope_sha256"))
                and record["lineage"] == {"relationship": row["relationship"], "parent_import_id": row["parent_import_id"]},
                "IMPORT_INDEX_MISMATCH",
            )
            context = {
                "snapshot_context": {k: v for k, v in record["source_snapshot"].items() if k not in ("sha256", "captured_at")},
                "mapping": record["mapping"],
                "format": record["format"],
                "dialect": record["dialect"],
            }
            _check(
                record["context_sha256"] == digest(context)
                and record["scope_sha256"] == digest(scope(record["source_snapshot"]))
                and record["raw_ref"] == "sha256:" + record["raw_sha256"]
                and import_id == "a10-" + sha256_bytes(
                    canonical_bytes({
                        "raw_sha256": record["raw_sha256"],
                        "context_sha256": record["context_sha256"],
                        "lineage": record["lineage"],
                    })
                ),
                "IMPORT_IDENTITY_MISMATCH",
            )
            return row, record
        except OpportunityError:
            raise
        except (ValueError, UnicodeError) as exc:
            raise SignalLedgerError("IMPORT_RECORD_CORRUPT") from exc

    @staticmethod
    def _lineage(child: dict[str, Any], parent: dict[str, Any]) -> None:
        require(child["scope_sha256"] == parent["scope_sha256"], "LINEAGE_SCOPE_MISMATCH")
        if child["lineage"]["relationship"] == "CORRECTION":
            require(child["context_sha256"] == parent["context_sha256"], "CORRECTION_CONTEXT_MISMATCH")
            require(child["raw_sha256"] != parent["raw_sha256"], "CORRECTION_SOURCE_UNCHANGED")
        else:
            require(child["raw_sha256"] == parent["raw_sha256"], "REPROCESS_SOURCE_MISMATCH")
            require(child["context_sha256"] != parent["context_sha256"], "REPROCESS_CONTEXT_UNCHANGED")

    def _get(self, db, import_id: str) -> dict[str, Any]:
        row, record = self._record(db, import_id)
        raw = self._raw(db, record["raw_sha256"])
        lineage = record["lineage"]
        try:
            prepared = prepare_bytes(
                raw,
                source_snapshot=record["source_snapshot"],
                profile=record["mapping"],
                file_format=record["format"],
                dialect=record["dialect"],
                correction_of=lineage["parent_import_id"] if lineage["relationship"] == "CORRECTION" else None,
                reprocess_of=lineage["parent_import_id"] if lineage["relationship"] == "REPROCESS" else None,
            )
            expected = {**prepared.record, "status": "IMPORTED", "recorded_at": record["recorded_at"]}
            _check(record == expected, "IMPORT_REPLAY_MISMATCH")
            if lineage["parent_import_id"]:
                parent_row, parent = self._record(db, lineage["parent_import_id"])
                _check(parent_row["seq"] < row["seq"], "LINEAGE_ORDER_CORRUPT")
                self._lineage(record, parent)
        except OpportunityError as exc:
            if isinstance(exc, SignalLedgerError):
                raise
            raise SignalLedgerError("IMPORT_REPLAY_INVALID") from exc
        
        stored = db.execute(
            "SELECT ordinal, signal_id, "
            "CASE WHEN length(payload)<=? THEN payload ELSE NULL END AS payload, "
            "payload_sha256 FROM signals WHERE import_id=? ORDER BY ordinal LIMIT ?",
            (_MAX_SIGNAL, import_id, record["signal_count"] + 1),
        ).fetchall()
        _check(len(stored) == len(prepared.signals), "SIGNAL_COUNT_MISMATCH")
        for ordinal, (actual, signal) in enumerate(zip(stored, prepared.signals), 1):
            _check(
                actual["ordinal"] == ordinal
                and actual["signal_id"] == signal["signal_id"]
                and bytes(actual["payload"]) == canonical_bytes(signal)
                and actual["payload_sha256"] == digest(signal),
                "SIGNAL_INTEGRITY_MISMATCH",
            )
        return {"import": record, "signals": list(prepared.signals)}

    def _store(self, prepared: PreparedSignalImport) -> dict[str, Any]:
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
                db.execute(
                    "INSERT INTO raw_evidence VALUES(?,?,?)",
                    (record["raw_sha256"], record["raw_size_bytes"], prepared.raw),
                )
            db.execute(
                "INSERT INTO imports(import_id, raw_sha256, context_sha256, scope_sha256, parent_import_id, "
                "relationship, payload, payload_sha256, recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    record["raw_sha256"],
                    record["context_sha256"],
                    record["scope_sha256"],
                    record["lineage"]["parent_import_id"],
                    record["lineage"]["relationship"],
                    canonical_bytes(record),
                    digest(record),
                    record["recorded_at"],
                ),
            )
            for signal in prepared.signals:
                db.execute(
                    "INSERT INTO signals(import_id, ordinal, signal_id, payload, payload_sha256) VALUES(?,?,?,?,?)",
                    (
                        import_id,
                        signal["ordinal"],
                        signal["signal_id"],
                        canonical_bytes(signal),
                        digest(signal),
                    ),
                )
            return {"import": record, "signals": list(prepared.signals), "duplicate": False}

    def import_file(
        self,
        path: Path | str,
        *,
        source_snapshot: dict[str, Any],
        profile: str | dict[str, Any] | None = None,
        file_format: str = "csv",
        dialect: str | None = None,
        correction_of: str | None = None,
        reprocess_of: str | None = None,
    ) -> dict[str, Any]:
        raw = read_local(path, limit=MAX_FILE_BYTES)
        prepared = prepare_bytes(
            raw,
            source_snapshot=source_snapshot,
            profile=profile,
            file_format=file_format,
            dialect=dialect,
            correction_of=correction_of,
            reprocess_of=reprocess_of,
        )
        return self._store(prepared)

    def import_bytes(
        self,
        raw: bytes,
        *,
        source_snapshot: dict[str, Any],
        profile: str | dict[str, Any] | None = None,
        file_format: str = "csv",
        dialect: str | None = None,
        correction_of: str | None = None,
        reprocess_of: str | None = None,
    ) -> dict[str, Any]:
        prepared = prepare_bytes(
            raw,
            source_snapshot=source_snapshot,
            profile=profile,
            file_format=file_format,
            dialect=dialect,
            correction_of=correction_of,
            reprocess_of=reprocess_of,
        )
        return self._store(prepared)

    def get(self, import_id: str) -> dict[str, Any]:
        with self._connect(write=False) as db:
            return self._get(db, import_id)

    def inspect(self, import_id: str) -> dict[str, Any]:
        with self._connect(write=False) as db:
            result = self._get(db, import_id)
            record = result["import"]
            return {
                "verified": True,
                "import_id": record["import_id"],
                "raw_sha256": record["raw_sha256"],
                "signal_count": record["signal_count"],
                "context_sha256": record["context_sha256"],
                "scope_sha256": record["scope_sha256"],
                "lineage": record["lineage"],
                "recorded_at": record["recorded_at"],
            }

    def list_imports(
        self,
        *,
        source_id: str | None = None,
        locale: str | None = None,
        geo: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect(write=False) as db:
            rows = db.execute(
                "SELECT payload FROM imports ORDER BY seq DESC LIMIT ?",
                (min(max(limit, 1), 1000),),
            ).fetchall()
            results = []
            for row in rows:
                item = json.loads(bytes(row["payload"]).decode("utf-8"))
                snap = item.get("source_snapshot", {})
                if source_id and snap.get("source_id") != source_id:
                    continue
                if locale and snap.get("locale") != locale:
                    continue
                if geo and snap.get("geo") != geo:
                    continue
                results.append(item)
            return results

    def query_signals(
        self,
        *,
        topic: str | None = None,
        locale: str | None = None,
        geo: str | None = None,
        source_type: str | None = None,
        unit: str | None = None,
        query_context: str | None = None,
        status: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query normalized signals across imports for consumption by M02/M03."""
        with self._connect(write=False) as db:
            rows = db.execute(
                "SELECT payload FROM signals ORDER BY import_id, ordinal LIMIT ?",
                (min(max(limit, 1), 10000),),
            ).fetchall()
            results = []
            for row in rows:
                sig = json.loads(bytes(row["payload"]).decode("utf-8"))
                if topic is not None and sig.get("topic") != topic:
                    continue
                if locale is not None and sig.get("locale") != locale:
                    continue
                if geo is not None and sig.get("geo") != geo:
                    continue
                if source_type is not None and sig.get("source_type") != source_type:
                    continue
                if unit is not None and sig.get("unit") != unit:
                    continue
                if query_context is not None and sig.get("query_context") != query_context:
                    continue
                if status is not None and sig.get("status") != status:
                    continue
                results.append(sig)
            return results
