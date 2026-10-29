"""Independent, append-only audience journal; M01 is only opened for reading."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ..core.models import OpportunityError, parse_json
from ..signals.files import ledger_path, read_local
from ..signals.ledger import SignalLedger
from .models import (
    MAX_NOTES_BYTES, MAX_RECORD_BYTES, AudienceError, PreparedAudienceInsight,
    check, prepare_bytes, validate_insight,
)

_APP_ID = 0x41313033  # A103; never share the M01 or A09 database.
_DDL = (
    """CREATE TABLE audience_raw(
        sha256 TEXT PRIMARY KEY,
        size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=262144),
        payload BLOB NOT NULL CHECK(length(payload)=size_bytes))""",
    """CREATE TABLE audience_insights(
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        insight_id TEXT UNIQUE NOT NULL,
        raw_sha256 TEXT NOT NULL REFERENCES audience_raw(sha256),
        parent_id TEXT REFERENCES audience_insights(insight_id),
        revision INTEGER NOT NULL,
        payload BLOB NOT NULL,
        payload_sha256 TEXT NOT NULL)""",
    "CREATE INDEX audience_parent ON audience_insights(parent_id, seq)",
)


class AudienceLedger:
    def __init__(self, path: Path | str):
        self.path = path

    @contextmanager
    def _connect(self, *, write: bool = False):
        db = None
        try:
            path = ledger_path(self.path, create=write)
            db = sqlite3.connect(str(path) if write else path.as_uri() + "?mode=ro",
                                 uri=not write, isolation_level=None, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            if not write:
                db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if write and app_id == 0 and version == 0:
                check(not db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone(), "AUDIENCE_LEDGER_FORMAT_MISMATCH")
                for sql in _DDL:
                    db.execute(sql)
                for table in ("audience_raw", "audience_insights"):
                    for operation in ("UPDATE", "DELETE"):
                        db.execute(
                            f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                            "BEGIN SELECT RAISE(ABORT, 'A10_AUDIENCE_APPEND_ONLY'); END"
                        )
                db.execute(f"PRAGMA application_id={_APP_ID}")
                db.execute("PRAGMA user_version=1")
            else:
                check(app_id == _APP_ID and version == 1, "AUDIENCE_LEDGER_FORMAT_MISMATCH")
            yield db
            db.commit()
        except AudienceError:
            raise
        except (sqlite3.Error, OpportunityError, OSError) as exc:
            raise AudienceError("AUDIENCE_LEDGER_IO_ERROR") from exc
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    @staticmethod
    def _get(db, insight_id: str, signal_ledger: SignalLedger | None) -> PreparedAudienceInsight:
        check(isinstance(insight_id, str) and re.fullmatch(r"aud-[a-f0-9]{64}", insight_id) is not None,
              "INVALID_INSIGHT_ID")
        chain, seen = [], set()
        current = insight_id
        while current is not None:
            check(current not in seen and len(chain) < 128, "AUDIENCE_LINEAGE_INVALID")
            seen.add(current)
            row = db.execute(
                "SELECT i.seq, i.insight_id, i.raw_sha256, i.parent_id, i.revision, i.payload_sha256, "
                "CASE WHEN length(i.payload)<=? THEN i.payload END AS record, r.size_bytes, "
                "CASE WHEN length(r.payload)<=? THEN r.payload END AS raw "
                "FROM audience_insights i JOIN audience_raw r ON r.sha256=i.raw_sha256 "
                "WHERE i.insight_id=?", (MAX_RECORD_BYTES, MAX_NOTES_BYTES, current),
            ).fetchone()
            check(row is not None, "AUDIENCE_INSIGHT_NOT_FOUND")
            check(row["record"] is not None and row["raw"] is not None, "AUDIENCE_PAYLOAD_TOO_LARGE")
            payload, raw = bytes(row["record"]), bytes(row["raw"])
            check(0 < len(raw) == row["size_bytes"] and sha256_bytes(raw) == row["raw_sha256"],
                  "AUDIENCE_RAW_TAMPERED")
            check(sha256_bytes(payload) == row["payload_sha256"], "AUDIENCE_RECORD_TAMPERED")
            try:
                record = validate_insight(parse_json(payload))
            except OpportunityError as exc:
                raise AudienceError("AUDIENCE_RECORD_INVALID") from exc
            check(record["insight_id"] == current
                  and record["source_sha256"] == row["raw_sha256"]
                  and record["lineage"]["parent_insight_id"] == row["parent_id"]
                  and record["lineage"]["number"] == row["revision"], "AUDIENCE_INDEX_MISMATCH")
            if chain:
                check(row["seq"] < chain[-1][2], "AUDIENCE_LINEAGE_INVALID")
            chain.append((raw, record, row["seq"]))
            current = row["parent_id"]
        parent = None
        for raw, record, _ in reversed(chain):
            expected = prepare_bytes(raw, signal_ledger=signal_ledger, parent=parent)
            check(expected.record == record, "AUDIENCE_REPLAY_MISMATCH")
            parent = record
        return PreparedAudienceInsight(chain[0][0], chain[0][1])

    def append_bytes(self, raw: bytes, *, signal_ledger: SignalLedger | None = None,
                     revision_of: str | None = None) -> dict:
        parent = self.get(revision_of, signal_ledger=signal_ledger) if revision_of is not None else None
        prepared = prepare_bytes(raw, signal_ledger=signal_ledger, parent=parent)
        record = prepared.record
        with self._connect(write=True) as db:
            if db.execute("SELECT 1 FROM audience_insights WHERE insight_id=?", (record["insight_id"],)).fetchone():
                existing = self._get(db, record["insight_id"], signal_ledger)
                check(existing == prepared, "AUDIENCE_REPLAY_MISMATCH")
                return {"insight": existing.record, "duplicate": True}
            if parent is not None:
                check(self._get(db, parent["insight_id"], signal_ledger).record == parent, "AUDIENCE_PARENT_CHANGED")
            existing = db.execute("SELECT payload FROM audience_raw WHERE sha256=?", (record["source_sha256"],)).fetchone()
            if existing is None:
                db.execute("INSERT INTO audience_raw VALUES(?,?,?)", (record["source_sha256"], len(raw), raw))
            else:
                check(bytes(existing[0]) == raw, "AUDIENCE_RAW_TAMPERED")
            payload = canonical_bytes(record)
            db.execute(
                "INSERT INTO audience_insights(insight_id,raw_sha256,parent_id,revision,payload,payload_sha256) "
                "VALUES(?,?,?,?,?,?)",
                (record["insight_id"], record["source_sha256"], record["lineage"]["parent_insight_id"],
                 record["lineage"]["number"], payload, sha256_bytes(payload)),
            )
        return {"insight": record, "duplicate": False}

    def append_file(self, path: Path | str, **kwargs) -> dict:
        return self.append_bytes(read_local(path, limit=MAX_NOTES_BYTES), **kwargs)

    def get(self, insight_id: str, *, signal_ledger: SignalLedger | None = None) -> dict:
        with self._connect() as db:
            return self._get(db, insight_id, signal_ledger).record

    def raw_bytes(self, insight_id: str, *, signal_ledger: SignalLedger | None = None) -> bytes:
        with self._connect() as db:
            return self._get(db, insight_id, signal_ledger).raw

    def history(self, insight_id: str, *, signal_ledger: SignalLedger | None = None) -> list[dict]:
        """Return the exact ancestry, oldest first; branches never replace each other."""
        with self._connect() as db:
            result = []
            current = insight_id
            while current is not None:
                record = self._get(db, current, signal_ledger).record
                result.append(record)
                current = record["lineage"]["parent_insight_id"]
            return list(reversed(result))
