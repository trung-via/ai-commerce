"""Durable intent BEFORE effects, process-wide serialization, no content in journal."""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3

from .contract import canonical, decode, deny


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=0.1, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, receipt BLOB);
            CREATE TABLE IF NOT EXISTS previews (fingerprint TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
        """)

    def close(self):
        self.db.close()

    def begin(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            deny("HOST_BUSY")

    def epoch(self, epoch):
        old = self.db.execute("SELECT epoch FROM settings WHERE id='revocation'").fetchone()
        if old and epoch < old[0]:
            deny("REVOCATION_ROLLBACK")
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('revocation', ?)", (epoch,))

    @staticmethod
    def request_key(request):
        return ("v4:" if request.value["schema"] == "hostops.request.v4" else "") + request.value["request_id"]

    def reserve(self, request, epoch):
        self.begin()
        try:
            self.epoch(epoch)
            row = self.db.execute("SELECT fingerprint,state,receipt FROM requests WHERE id=?",
                                  (self.request_key(request),)).fetchone()
            if row:
                if row[0] != request.fingerprint:
                    deny("REQUEST_ID_CONFLICT")
                return row
            if self.db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 100000:
                deny("JOURNAL_LIMIT")
            self.db.execute("INSERT INTO requests VALUES (?,?,'INTENT',NULL)",
                            (self.request_key(request), request.fingerprint))
            return None
        finally:
            # A semantic conflict must not erase the previous intent.
            self.db.commit()

    @contextmanager
    def execution(self):
        self.begin()
        try:
            yield
        finally:
            self.db.commit()

    def finish(self, request, receipt):
        self.db.execute("UPDATE requests SET state=?,receipt=? WHERE id=? AND fingerprint=?",
                        (receipt["status"], canonical(receipt), self.request_key(request), request.fingerprint))

    def preview(self, fingerprint):
        self.db.execute("INSERT OR IGNORE INTO previews VALUES (?)", (fingerprint,))

    def has_preview(self, fingerprint):
        return bool(self.db.execute("SELECT 1 FROM previews WHERE fingerprint=?", (fingerprint,)).fetchone())

    @staticmethod
    def previous(row):
        return decode(row[2]) if row[2] else None
