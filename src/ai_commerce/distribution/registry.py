"""Small append-only SQLite registry with exact tuple isolation and hash verification."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .model import CapabilityError, canonical, digest, identity, timestamp, validate_snapshot


class CapabilityRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL UNIQUE,
                stream_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS lookup_snapshot ON snapshots(stream_key, seq)")
        db.commit()
        return db

    @staticmethod
    def _decode(row: tuple[str, str] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        raw, expected = row
        try:
            data = json.loads(raw)
            validate_snapshot(data)
        except (ValueError, TypeError, KeyError) as exc:
            raise CapabilityError("CORRUPT_SNAPSHOT_RECORD") from exc
        if digest(data) != expected:
            raise CapabilityError("SNAPSHOT_INTEGRITY_MISMATCH")
        return data

    def import_snapshot(self, record: dict[str, Any]) -> str:
        snapshot = validate_snapshot(record)
        key, encoded, sha = identity(snapshot), canonical(snapshot).decode("utf-8"), digest(snapshot)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute("SELECT payload_sha256 FROM snapshots WHERE snapshot_id=?", (snapshot["snapshot_id"],)).fetchone()
            if exists is not None:
                if exists[0] == sha:
                    return sha
                raise CapabilityError("SNAPSHOT_ID_COLLISION")
            current_row = db.execute(
                "SELECT payload, payload_sha256 FROM snapshots WHERE stream_key=? ORDER BY seq DESC LIMIT 1", (key,)
            ).fetchone()
            current = self._decode(current_row)
            previous = snapshot.get("supersedes_snapshot_id")
            if current is None and previous is not None:
                raise CapabilityError("SUPERSESSION_TARGET_MISSING")
            if current is not None:
                if previous != current["snapshot_id"]:
                    raise CapabilityError("SUPERSESSION_REQUIRED_FOR_CURRENT_HEAD")
                if timestamp(snapshot["observed_at"]) < timestamp(current["observed_at"]):
                    raise CapabilityError("STALE_OBSERVATION_CANNOT_SUPERSEDE")
                if timestamp(snapshot["recorded_at"]) < timestamp(current["recorded_at"]):
                    raise CapabilityError("STALE_RECORDING_CANNOT_SUPERSEDE")
            db.execute(
                "INSERT INTO snapshots(snapshot_id,stream_key,payload,payload_sha256) VALUES(?,?,?,?)",
                (snapshot["snapshot_id"], key, encoded, sha),
            )
        return sha

    def latest(self, request: dict[str, Any]) -> dict[str, Any] | None:
        from .model import validate_request
        validate_request(request)
        with self._connect() as db:
            return self._decode(db.execute(
                "SELECT payload,payload_sha256 FROM snapshots WHERE stream_key=? ORDER BY seq DESC LIMIT 1",
                (identity(request),),
            ).fetchone())

    def history(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        from .model import validate_request
        validate_request(request)
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload,payload_sha256 FROM snapshots WHERE stream_key=? ORDER BY seq",
                (identity(request),),
            ).fetchall()
        return [self._decode(row) for row in rows]
