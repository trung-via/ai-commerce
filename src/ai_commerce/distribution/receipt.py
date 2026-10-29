"""A08 D04: append-only local publication intent and user-supplied receipt ledger.

The journal never contacts providers or verifies social account access. A recorded
published/public state represents attached evidence, NOT a live provider check.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from importlib.resources import files
from jsonschema import Draft202012Validator
from typing import Any
from urllib.parse import urlsplit

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.operations.manifest import strict_json

from .assisted import DistributionError, inspect_package

_ALLOWED_EVENTS = {
    "SUBMISSION_REPORTED", "OUTCOME_UNKNOWN", "REJECTION_REPORTED",
    "PUBLICATION_RECEIPT", "VISIBILITY_EVIDENCE", "REMOVAL_REPORTED",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise DistributionError(reason)


def _load_evidence(path: str | Path | None) -> tuple[bytes | None, str | None]:
    if path is None:
        return None, None
    source = Path(path)
    _require(source.is_file() and not source.is_symlink(), "EVIDENCE_FILE_INVALID")
    _require(source.stat().st_size <= 5 * 1024 * 1024, "EVIDENCE_FILE_TOO_LARGE")
    raw = source.read_bytes()
    _require(bool(raw), "EVIDENCE_FILE_EMPTY")
    return raw, hashlib.sha256(raw).hexdigest()


def _url(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    target = urlsplit(value)
    return target.scheme == "https" and bool(target.hostname) and not target.username and not target.password


class PublicationJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("""
            CREATE TABLE IF NOT EXISTS publications(
                publication_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                package_sha256 TEXT NOT NULL, platform TEXT NOT NULL,
                surface TEXT NOT NULL, account_ref TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS publication_events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                publication_id TEXT NOT NULL REFERENCES publications(publication_id),
                event_digest TEXT UNIQUE NOT NULL,
                event_json BLOB NOT NULL,
                evidence_blob BLOB,
                evidence_sha256 TEXT
            )
        """)
        db.commit()
        return db

    def create(self, package_dir: str | Path, *, idempotency_key: str) -> dict[str, Any]:
        pkg = inspect_package(package_dir)
        _require(isinstance(idempotency_key, str) and 1 <= len(idempotency_key) <= 200,
                 "INVALID_IDEMPOTENCY_KEY")
        digest = pkg["package_sha256"]
        ident = "pub-" + sha256_bytes(canonical_bytes(
            {"idempotency_key": idempotency_key, "package_sha256": digest}))[:24]
        target = canonical_bytes(pkg["account_ref"]).decode("utf-8")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT publication_id, package_sha256 FROM publications WHERE idempotency_key=?",
                (idempotency_key,)).fetchone()
            if existing:
                _require(existing == (ident, digest), "PUBLICATION_IDEMPOTENCY_COLLISION")
            else:
                db.execute(
                    "INSERT INTO publications VALUES(?,?,?,?,?,?,?)",
                    (ident, idempotency_key, digest, pkg["platform"], pkg["surface"], target, _now()),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.get(ident)

    def record(self, publication_id: str, event_type: str, *,
               evidence_file: str | Path | None = None, provider_post_id: str | None = None,
               public_url: str | None = None) -> dict[str, Any]:
        _require(event_type in _ALLOWED_EVENTS, "UNKNOWN_PUBLICATION_EVENT")
        raw, digest = _load_evidence(evidence_file)
        _require(provider_post_id is None or
                 (isinstance(provider_post_id, str) and bool(provider_post_id.strip()) and len(provider_post_id) <= 256),
                 "POST_ID_INVALID")
        _require(public_url is None or _url(public_url), "PUBLIC_URL_INVALID")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            current = self._read(db, publication_id)
            _require(current is not None, "PUBLICATION_NOT_FOUND")
            before = current["state"]
            if event_type == "SUBMISSION_REPORTED":
                _require(before in {"MANUAL_ACTION_REQUIRED", "UNKNOWN"}, "SUBMISSION_TRANSITION_INVALID")
                _require(provider_post_id is None and public_url is None, "SUBMISSION_HAS_UNVERIFIED_POST_ID")
            elif event_type == "PUBLICATION_RECEIPT":
                _require(before in {"SUBMITTED", "UNKNOWN", "PRIVATE_ONLY"}, "CONFIRMATION_TRANSITION_INVALID")
                _require(raw is not None and provider_post_id is not None, "PUBLICATION_RECEIPT_REQUIRED")
            elif event_type == "VISIBILITY_EVIDENCE":
                _require(before == "PUBLISHED_CONFIRMED", "PUBLICATION_NOT_CONFIRMED")
                _require(raw is not None and public_url is not None, "VISIBILITY_PROOF_REQUIRED")
                receipt_sha = current["receipt_sha256"]
                _require(receipt_sha is not None and digest != receipt_sha,
                         "VISIBILITY_EVIDENCE_MUST_BE_INDEPENDENT")
            elif event_type == "REMOVAL_REPORTED":
                _require(raw is not None and before in {"PUBLISHED_CONFIRMED", "PRIVATE_ONLY"},
                         "REMOVAL_EVIDENCE_REQUIRED")
            elif event_type in {"REJECTION_REPORTED", "OUTCOME_UNKNOWN"}:
                _require(before in {"MANUAL_ACTION_REQUIRED", "SUBMITTED", "UNKNOWN"},
                         "STATUS_TRANSITION_INVALID")
                _require(provider_post_id is None and public_url is None,
                         "UNVERIFIED_EVENT_HAS_POST_ID")
            event = {
                "schema_version": "PublicationEvent.v1", "publication_id": publication_id,
                "event_type": event_type, "provider_post_id": provider_post_id,
                "public_url": public_url, "evidence_sha256": digest,
                "source": "USER_SUPPLIED_NOT_LIVE_VERIFIED", "recorded_at": _now(),
            }
            schema = strict_json(files("ai_commerce.distribution").joinpath(
                "schemas", "PublicationEvent.v1.schema.json").read_bytes())
            _require(Draft202012Validator(schema).is_valid(event), "PUBLICATION_EVENT_SCHEMA_INVALID")
            event_bytes = canonical_bytes(event)
            event_digest = sha256_bytes(event_bytes)
            db.execute(
                "INSERT INTO publication_events(publication_id,event_digest,event_json,evidence_blob,evidence_sha256)"
                " VALUES(?,?,?,?,?)",
                (publication_id, event_digest, event_bytes, raw, digest),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.get(publication_id)

    @staticmethod
    def _read(db: sqlite3.Connection, publication_id: str) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT publication_id,idempotency_key,package_sha256,platform,surface,account_ref,created_at"
            " FROM publications WHERE publication_id=?", (publication_id,)
        ).fetchone()
        if row is None:
            return None
        identifier, key, digest, platform, surface, target, created = row
        try:
            account = strict_json(target.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise DistributionError("CORRUPT_PUBLICATION_ACCOUNT") from exc
        state, visibility, provider_post_id, public_url = "MANUAL_ACTION_REQUIRED", "UNKNOWN", None, None
        receipt_refs: list[str] = []
        receipt_sha = None
        rows = db.execute(
            "SELECT event_digest,event_json,evidence_blob,evidence_sha256 FROM publication_events"
            " WHERE publication_id=? ORDER BY seq", (publication_id,)
        ).fetchall()
        for event_digest, raw, evidence_blob, evidence_sha in rows:
            _require(sha256_bytes(raw) == event_digest, "CORRUPT_PUBLICATION_EVENT")
            item = strict_json(raw)
            schema = strict_json(files("ai_commerce.distribution").joinpath(
                "schemas", "PublicationEvent.v1.schema.json").read_bytes())
            _require(Draft202012Validator(schema).is_valid(item), "CORRUPT_EVENT_SCHEMA")
            _require(item.get("publication_id") == publication_id and
                     item.get("schema_version") == "PublicationEvent.v1" and
                     item.get("event_type") in _ALLOWED_EVENTS, "CORRUPT_EVENT_PAYLOAD")
            _require((evidence_blob is None and evidence_sha is None and item["evidence_sha256"] is None)
                     or (evidence_blob is not None and evidence_sha is not None
                         and hashlib.sha256(evidence_blob).hexdigest() == evidence_sha
                         and item["evidence_sha256"] == evidence_sha),
                     "CORRUPT_PUBLICATION_EVIDENCE")
            if evidence_sha:
                receipt_refs.append(evidence_sha)
            kind = item["event_type"]
            if kind == "SUBMISSION_REPORTED":
                _require(state in {"MANUAL_ACTION_REQUIRED", "UNKNOWN"}, "CORRUPT_SUBMISSION_TRANSITION")
                state = "SUBMITTED"
            elif kind == "OUTCOME_UNKNOWN":
                state, visibility = "UNKNOWN", "UNKNOWN"
            elif kind == "REJECTION_REPORTED":
                state, visibility = "REJECTED", "UNKNOWN"
            elif kind == "PUBLICATION_RECEIPT":
                _require(state in {"SUBMITTED", "UNKNOWN", "PRIVATE_ONLY"}
                         and bool(item["provider_post_id"]) and evidence_sha is not None,
                         "CORRUPT_RECEIPT_TRANSITION")
                state, visibility = "PUBLISHED_CONFIRMED", "UNKNOWN"
                receipt_sha, provider_post_id = evidence_sha, item["provider_post_id"]
            elif kind == "VISIBILITY_EVIDENCE":
                _require(state == "PUBLISHED_CONFIRMED" and
                         evidence_sha is not None and evidence_sha != receipt_sha
                         and _url(item["public_url"]), "CORRUPT_VISIBILITY_TRANSITION")
                visibility, public_url = "PUBLIC", item["public_url"]
            elif kind == "REMOVAL_REPORTED":
                _require(state in {"PUBLISHED_CONFIRMED", "PRIVATE_ONLY"} and evidence_sha is not None,
                         "CORRUPT_REMOVAL_TRANSITION")
                state, visibility = "REMOVED", "UNKNOWN"
        return {
            "publication_id": identifier, "idempotency_key": key,
            "approved_package_sha256": digest, "platform": platform, "surface": surface,
            "account_ref": account["namespace"] + ":" + account["id"],
            "account_identity": account, "state": state, "visibility": visibility,
            "commerce_link_status": "NOT_APPLICABLE", "provider_post_id": provider_post_id,
            "public_url": public_url, "receipt_refs": receipt_refs,
            "receipt_sha256": receipt_sha,
            "attempt_id": "manual-" + identifier, "created_at": created,
            "last_checked_at": None, "verification": "USER_SUPPLIED_NOT_LIVE_VERIFIED",
            "events": len(rows),
        }

    def get(self, publication_id: str) -> dict[str, Any]:
        db = self._connect()
        try:
            result = self._read(db, publication_id)
            _require(result is not None, "PUBLICATION_NOT_FOUND")
            return result
        finally:
            db.close()
