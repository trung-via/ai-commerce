"""A12-owned local append-only SQLite evidence journal, independent of A11.

Only validated A12 snapshots, claims, offers and rights enter this journal.
Exact UTF-8 JSON/CSV evidence is retained by hash, including old corrections,
counterevidence and raw unmapped fields. Binary media and arbitrary report/PDF
attachments are excluded: keep them outside this metadata journal. A12-02's
in-memory report API still accepts its existing bytes; archiving requires
non-personal JSON evidence. Every persisted field/raw object is privacy-scanned
without redaction. Aliases, public URLs without credentials/query/fragment,
typed product identifiers and hashes are narrow exceptions; the scanner cannot
establish that arbitrary prose is non-personal. Callers must supply clean data.

Transactions serialize writers and rollback the entire append/replay batch.
Events/blobs have no UPDATE/DELETE/ALTER path; checked schema, insert guards,
lineage, canonical payloads and hash chaining detect local damage. Hashes are
not authentication, and a privileged complete rewrite/truncation cannot be
excluded without a separately retained checkpoint. This is a local trust
boundary, not an OS sandbox against hostile filesystem replacement. Explicit
timestamps and stream aliases make event identity independent of wall clock.
No network, provider, A11 import, publishing or consumer mutation occurs.
"""
from __future__ import annotations

import re
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import sha256_bytes

from .claims import ProductClaimEvidence
from .core import ProductError, canonical_bytes, digest, parse_json, parse_timestamp, require
from .files import _inspect, _path
from .offers import ProductOfferObservation
from .reader import ProductListingSnapshot
from .rights import ProductAssetRights
from .rights_schemas import ALIAS
from .schemas import DATE, MAX_DOCUMENT_BYTES, MAX_RAW_BYTES, SHA, identifier, nullable, obj
from .storage_privacy import check_document, check_raw

REGISTRY_VERSION = "a12-evidence-registry.v1"
MAX_EVENTS = 2048
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_ENTRY_BYTES = MAX_DOCUMENT_BYTES
MAX_BATCH_EVENTS = 64
MAX_BATCH_BYTES = 32 * 1024 * 1024
_APP_ID = 0x41313233
_ZERO = "0" * 64
_KINDS = ("ProductListingSnapshot", "ProductClaimEvidence", "ProductOfferObservation", "ProductAssetRights")
_IDS = ("snapshot_id", "claim_id", "offer_id", "rights_id")
_TYPES = (ProductListingSnapshot, ProductClaimEvidence, ProductOfferObservation, ProductAssetRights)
_EVENT = obj({
    "schema_version": {"const": "ProductEvidenceEvent.v1"},
    "registry_version": {"const": REGISTRY_VERSION}, "event_id": identifier("a12-event-"),
    "stream_key": ALIAS, "revision": {"type": "integer", "minimum": 1, "maximum": MAX_EVENTS},
    "parent_event_id": nullable(identifier("a12-event-")),
    "operation": {"enum": ["OBSERVATION", "REVISION", "CORRECTION", "COUNTEREVIDENCE"]},
    "actor_ref": nullable(ALIAS), "reason_ref": nullable(ALIAS), "recorded_at": DATE,
    "evidence": obj({"kind": {"enum": list(_KINDS)}, "record": {"type": "object"},
                     "snapshot": {"type": ["object", "null"]}}),
    "blob_refs": {"type": "array", "minItems": 1, "maxItems": 5, "uniqueItems": True, "items": SHA},
})
_EVENT_VALIDATOR = Draft202012Validator(_EVENT)
_DDL = [f"""CREATE TABLE a12_raw(
    sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<={MAX_RAW_BYTES}),
    payload BLOB NOT NULL CHECK(typeof(payload)='blob' AND length(payload)=size_bytes))""",
    f"""CREATE TABLE a12_events(
    seq INTEGER PRIMARY KEY CHECK(seq>0 AND seq<={MAX_EVENTS}),
    event_id TEXT UNIQUE NOT NULL,
    stream_key TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>0 AND revision<={MAX_EVENTS}),
    parent_event_id TEXT REFERENCES a12_events(event_id),
    record_id TEXT NOT NULL,
    payload BLOB NOT NULL CHECK(typeof(payload)='blob' AND length(payload)>0 AND length(payload)<={MAX_ENTRY_BYTES}),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    previous_hash TEXT NOT NULL CHECK(length(previous_hash)=64),
    event_hash TEXT UNIQUE NOT NULL CHECK(length(event_hash)=64),
    UNIQUE(stream_key,revision))"""]
for _table in ("a12_raw", "a12_events"):
    for _op in ("UPDATE", "DELETE"):
        _DDL.append(f"CREATE TRIGGER {_table}_no_{_op.lower()} BEFORE {_op} ON {_table} "
                    "BEGIN SELECT RAISE(ABORT,'A12_APPEND_ONLY'); END")
    _DDL.append(f"CREATE TRIGGER {_table}_controlled_insert BEFORE INSERT ON {_table} "
                "WHEN a12_append_allowed()!=1 BEGIN SELECT RAISE(ABORT,'A12_CONTROLLED_APPEND_ONLY'); END")
_DDL.append(f"""CREATE TRIGGER a12_event_order BEFORE INSERT ON a12_events BEGIN
    SELECT CASE WHEN NEW.seq != COALESCE((SELECT MAX(seq) FROM a12_events),0)+1
      OR NEW.previous_hash != COALESCE((SELECT event_hash FROM a12_events ORDER BY seq DESC LIMIT 1),'{_ZERO}')
      OR NEW.revision != COALESCE((SELECT MAX(revision) FROM a12_events WHERE stream_key=NEW.stream_key),0)+1
      OR NEW.parent_event_id IS NOT (SELECT event_id FROM a12_events
                                    WHERE stream_key=NEW.stream_key ORDER BY revision DESC LIMIT 1)
    THEN RAISE(ABORT,'A12_INVALID_APPEND_ORDER') END;
END""")


def _manifest(db) -> tuple:
    return tuple(tuple(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' ORDER BY name"))


@lru_cache(maxsize=1)
def _expected_manifest() -> tuple:
    with sqlite3.connect(":memory:") as db:
        for sql in _DDL:
            db.execute(sql)
        return _manifest(db)


def _key(value, code="INVALID_REGISTRY_STREAM") -> None:
    require(type(value) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,127}", value) is not None, code)


def _event_id(body: dict) -> str:
    return "a12-event-" + digest({key: value for key, value in body.items() if key != "event_id"})


def _chain_hash(seq: int, previous_hash: str, payload_hash: str) -> str:
    return digest({"registry_version": REGISTRY_VERSION, "seq": seq,
                   "previous_hash": previous_hash, "payload_sha256": payload_hash})


def _record_id(evidence: dict) -> str:
    return evidence["record"][_IDS[_KINDS.index(evidence["kind"])]]


def _unpack(evidence: dict, blobs: dict[str, bytes]):
    """Revalidate every record from raw bytes, never just its content hash."""
    record, kind = evidence["record"], evidence["kind"]
    used = set()

    def raw(ref):
        require(type(ref) is str and ref in blobs, "REGISTRY_RAW_MISSING")
        value = blobs[ref]
        require(type(value) is bytes and 0 < len(value) <= MAX_RAW_BYTES, "REGISTRY_RAW_SIZE_INVALID")
        require(sha256_bytes(value) == ref, "REGISTRY_RAW_HASH_MISMATCH")
        used.add(ref)
        return value

    if kind == "ProductListingSnapshot":
        require(evidence["snapshot"] is None, "REGISTRY_SNAPSHOT_MISMATCH")
        source = raw(record["source"]["raw_sha256"])
        result = ProductListingSnapshot(record, raw=source)
        check_raw(source, file_format=record["source"]["format"])
    else:
        value = evidence["snapshot"]
        require(type(value) is dict, "REGISTRY_SNAPSHOT_REQUIRED")
        snapshot_raw = raw(value["source"]["raw_sha256"])
        snapshot = ProductListingSnapshot(value, raw=snapshot_raw)
        check_raw(snapshot_raw, file_format=value["source"]["format"])
        source = raw(record["source"]["raw_sha256"])
        check_raw(source)
        if kind == "ProductClaimEvidence":
            proof = record["reviewer_proof"]
            proof_raw = None if proof is None else raw(proof["evidence_sha256"])
            if proof_raw is not None:
                check_raw(proof_raw)
            result = ProductClaimEvidence(record, raw=source, snapshot=snapshot, proof_raw=proof_raw)
        elif kind == "ProductOfferObservation":
            result = ProductOfferObservation(record, raw=source, snapshot=snapshot)
        else:
            licenses = {item["purpose"]: raw(item["review"]["evidence_sha256"])
                        for item in record["permissions"] if item["review"] is not None}
            result = ProductAssetRights(record, raw=source, snapshot=snapshot, license_raw=licenses)
    require(used == set(blobs), "REGISTRY_UNREFERENCED_RAW")
    check_document(evidence)
    return result


def _bundle(record) -> tuple[dict, dict[str, bytes]]:
    require(type(record) in _TYPES, "INVALID_REGISTRY_RECORD")
    kind = _KINDS[_TYPES.index(type(record))]
    blobs = {}

    def add(raw):
        ref = sha256_bytes(raw)
        require(ref not in blobs or blobs[ref] == raw, "REGISTRY_RAW_COLLISION")
        blobs[ref] = raw

    add(record.raw_bytes())
    snapshot = None
    if type(record) is not ProductListingSnapshot:
        snapshot = record._snapshot.to_dict()
        add(record._snapshot.raw_bytes())
    if type(record) is ProductClaimEvidence and record.proof_bytes() is not None:
        add(record.proof_bytes())
    if type(record) is ProductAssetRights:
        for proof in record.license_bytes().values():
            add(proof)
    evidence = {"kind": kind, "record": record.to_dict(), "snapshot": snapshot}
    require(_unpack(evidence, blobs).to_bytes() == record.to_bytes(), "REGISTRY_REPLAY_MISMATCH")
    return evidence, blobs


@dataclass(frozen=True, slots=True, init=False)
class EvidenceEvent:
    """Portable immutable replay unit with exact raw bytes, detached accessors."""

    _encoded: bytes = field(repr=False)
    _blobs: tuple[tuple[str, bytes], ...] = field(repr=False)

    def __init__(self, value: dict, *, raw_blobs: dict[str, bytes]):
        encoded = canonical_bytes(value)
        require(len(encoded) <= MAX_ENTRY_BYTES and _EVENT_VALIDATOR.is_valid(value),
                "INVALID_REGISTRY_EVENT")
        require(type(raw_blobs) is dict and len(raw_blobs) <= 5, "INVALID_REGISTRY_RAW_MAP")
        require(value["blob_refs"] == sorted(raw_blobs), "REGISTRY_RAW_REFERENCES_MISMATCH")
        require(value["event_id"] == _event_id(value), "REGISTRY_EVENT_ID_MISMATCH")
        initial = value["parent_event_id"] is None
        require((value["operation"] == "OBSERVATION" and value["revision"] == 1
                 and value["actor_ref"] is None and value["reason_ref"] is None) if initial
                else (value["operation"] != "OBSERVATION" and value["revision"] > 1
                      and value["actor_ref"] is not None and value["reason_ref"] is not None),
                "REGISTRY_LINEAGE_INVALID")
        try:
            _unpack(value["evidence"], raw_blobs)
        except (KeyError, TypeError, IndexError):
            raise ProductError("INVALID_REGISTRY_EVIDENCE") from None
        record = value["evidence"]["record"]
        latest_time = [parse_timestamp(record["source"]["captured_at"])]
        if value["evidence"]["snapshot"] is not None:
            latest_time.append(parse_timestamp(value["evidence"]["snapshot"]["source"]["captured_at"]))
        if value["evidence"]["kind"] == "ProductClaimEvidence" and record["reviewer_proof"] is not None:
            latest_time.append(parse_timestamp(record["reviewer_proof"]["reviewed_at"]))
        if value["evidence"]["kind"] == "ProductAssetRights":
            latest_time.extend(parse_timestamp(item["review"]["reviewed_at"])
                               for item in record["permissions"] if item["review"] is not None)
        require(parse_timestamp(value["recorded_at"]) >= max(latest_time), "REGISTRY_EVENT_BEFORE_EVIDENCE")
        check_document(value)
        object.__setattr__(self, "_encoded", encoded)
        object.__setattr__(self, "_blobs", tuple(sorted(raw_blobs.items())))

    @classmethod
    def from_bytes(cls, encoded: bytes, *, raw_blobs: dict[str, bytes]) -> EvidenceEvent:
        return cls(parse_json(encoded, limit=MAX_ENTRY_BYTES), raw_blobs=raw_blobs)

    def to_dict(self) -> dict:
        return parse_json(self._encoded)

    def to_bytes(self) -> bytes:
        return self._encoded

    def raw_blobs(self) -> dict[str, bytes]:
        return dict(self._blobs)

    def evidence(self):
        return _unpack(self.to_dict()["evidence"], self.raw_blobs())

    @property
    def event_id(self) -> str:
        return self.to_dict()["event_id"]


def _subject_key(evidence: dict) -> bytes:
    """A revision cannot silently change merchant, variant or evidence purpose."""
    record, kind = evidence["record"], evidence["kind"]
    if kind == "ProductListingSnapshot":
        key = [kind, record["source"]["source_ref"], record["source"]["source_kind"],
               record["source"]["synthetic"],
               sorted([item["identity"]["listing_id"], item["variant"]["variant_id"] or ""]
                      for item in record["listings"])]
    else:
        subject = record["subject"]
        key = [kind, subject["listing_id"], subject["variant_id"], subject["synthetic"]]
        if kind == "ProductClaimEvidence":
            key.extend((record["claim"]["category"], record["claim"]["claim_key"]))
        elif kind == "ProductAssetRights":
            key.extend((record["asset"]["asset_ref"], record["asset"]["content_sha256"]))
    return canonical_bytes(key)


def _lineage(value: dict, heads: dict) -> None:
    parent = heads.get(value["stream_key"])
    require(value["parent_event_id"] == (parent["event_id"] if parent else None)
            and value["revision"] == (parent["revision"] + 1 if parent else 1),
            "REGISTRY_LINEAGE_CONFLICT")
    if parent is not None:
        require(parse_timestamp(value["recorded_at"]) >= parse_timestamp(parent["recorded_at"]),
                "REGISTRY_REVISION_BEFORE_PARENT")
        require(_subject_key(value["evidence"]) == _subject_key(parent["evidence"]),
                "REGISTRY_REVISION_SUBJECT_MISMATCH")
        require(_record_id(value["evidence"]) != _record_id(parent["evidence"]),
                "REGISTRY_REVISION_UNCHANGED")
    if value["operation"] == "COUNTEREVIDENCE":
        evidence = value["evidence"]
        negative = ((evidence["kind"] == "ProductClaimEvidence"
                     and evidence["record"]["claim"]["role"] == "COUNTEREVIDENCE")
                    or (evidence["kind"] == "ProductAssetRights"
                        and any(item["status"] == "RESTRICTED" for item in evidence["record"]["permissions"])))
        require(negative, "REGISTRY_COUNTEREVIDENCE_REQUIRED")


class ProductEvidenceRegistry:
    """Bounded local journal. Construction does not touch the filesystem.

    ``append`` creates one event; ``replay`` atomically imports up to 64 exact
    exported events. Both return duplicate flags for exact idempotent replay.
    Call ``initialize`` for an empty database. Reads open SQLite read-only and
    verify a consistent snapshot; none silently creates a database.
    """

    def __init__(self, path: str | Path, *, root: str | Path, timeout_seconds: float = 5):
        require(type(timeout_seconds) in (int, float) and 0 <= timeout_seconds <= 5,
                "INVALID_REGISTRY_TIMEOUT")
        self.path, self.root, self.timeout = path, root, timeout_seconds

    def _local_path(self, *, write: bool) -> Path:
        root = _path(self.root).absolute()
        path = _path(self.path)
        path = path.absolute() if path.is_absolute() else root / path
        require(path.is_relative_to(root), "PATH_OUTSIDE_ROOT")
        require(path.suffix in (".sqlite", ".sqlite3", ".db"), "REGISTRY_PATH_SUFFIX_INVALID")
        require(stat.S_ISDIR(_inspect(root).st_mode) and stat.S_ISDIR(_inspect(path.parent).st_mode),
                "REGISTRY_ROOT_NOT_DIRECTORY")
        for target in (path, *(Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
            try:
                info = _inspect(target)
            except FileNotFoundError:
                # SQLite deletes a journal at commit. Its disappearance between
                # another writer's preflight checks is normal, not corruption.
                # Inspect parents again; only a missing leaf is permitted.
                _inspect(target.parent)
                require(write or target != path, "REGISTRY_NOT_FOUND")
                continue
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "REGISTRY_FILE_NOT_PRIVATE_REGULAR")
            require(info.st_size <= MAX_DATABASE_BYTES, "REGISTRY_DATABASE_TOO_LARGE")
        return path

    @contextmanager
    def _connect(self, *, write=False):
        db = None
        try:
            path = self._local_path(write=write)
            db = sqlite3.connect(path.as_uri() + ("?mode=rwc" if write else "?mode=ro"),
                                 uri=True, isolation_level=None, timeout=self.timeout)
            db.row_factory = sqlite3.Row
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_ENTRY_BYTES + MAX_RAW_BYTES)
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA trusted_schema=OFF")
            require(db.execute("PRAGMA journal_mode").fetchone()[0] == "delete", "REGISTRY_JOURNAL_FORMAT_MISMATCH")
            if not write:
                db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            app, version = db.execute("PRAGMA application_id").fetchone()[0], db.execute("PRAGMA user_version").fetchone()[0]
            if write and app == 0 and version == 0:
                require(not db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone(), "REGISTRY_FORMAT_MISMATCH")
                for sql in _DDL:
                    db.execute(sql)
                db.execute(f"PRAGMA application_id={_APP_ID}")
                db.execute("PRAGMA user_version=1")
            else:
                require(app == _APP_ID and version == 1, "REGISTRY_FORMAT_MISMATCH")
            require(_manifest(db) == _expected_manifest(), "REGISTRY_SCHEMA_MISMATCH")
            # trusted_schema=ON is needed only for our checked insert-guard UDF
            # in triggers; the schema was compared with the exact local DDL.
            db.create_function("a12_append_allowed", 0, lambda: int(write))
            db.execute("PRAGMA trusted_schema=ON")
            if write:
                pages = MAX_DATABASE_BYTES // db.execute("PRAGMA page_size").fetchone()[0]
                db.execute(f"PRAGMA max_page_count={pages}")

            def authorize(action, arg1, arg2, database, trigger):
                blocked = (sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_ALTER_TABLE,
                           sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_CREATE_TRIGGER,
                           sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE,
                           sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_VIEW,
                           sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH)
                denied = action in blocked or (action == sqlite3.SQLITE_PRAGMA and arg2 is not None)
                return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK

            db.set_authorizer(authorize)
            yield db
            require(db.execute("PRAGMA page_count").fetchone()[0]
                    * db.execute("PRAGMA page_size").fetchone()[0] <= MAX_DATABASE_BYTES,
                    "REGISTRY_DATABASE_TOO_LARGE")
            db.commit()
        except ProductError:
            raise
        except sqlite3.Error as exc:
            base_code = getattr(exc, "sqlite_errorcode", 0) & 255
            code = "REGISTRY_BUSY" if base_code in (
                sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "REGISTRY_IO_ERROR"
            raise ProductError(code) from None
        except (OSError, ValueError):
            raise ProductError("REGISTRY_IO_ERROR") from None
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    @staticmethod
    def _load(db) -> tuple[list[EvidenceEvent], dict, str]:
        require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok", "REGISTRY_SQLITE_INTEGRITY_FAILED")
        count = db.execute("SELECT COUNT(*) FROM a12_events").fetchone()[0]
        require(count <= MAX_EVENTS, "REGISTRY_EVENT_LIMIT")
        raw_count, total = db.execute("SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM a12_raw").fetchone()
        require(raw_count <= MAX_EVENTS * 5 and total <= MAX_DATABASE_BYTES, "REGISTRY_RAW_LIMIT")
        blobs = {}
        for row in db.execute("SELECT sha256,size_bytes,CASE WHEN length(payload)<=? THEN payload END AS raw FROM a12_raw",
                              (MAX_RAW_BYTES,)):
            raw = row["raw"]
            require(type(raw) is bytes and 0 < len(raw) == row["size_bytes"] <= MAX_RAW_BYTES,
                    "REGISTRY_RAW_SIZE_INVALID")
            require(sha256_bytes(raw) == row["sha256"], "REGISTRY_RAW_HASH_MISMATCH")
            blobs[row["sha256"]] = raw
        result, heads, referenced, records = [], {}, set(), {}
        previous = _ZERO
        for row in db.execute("SELECT seq,event_id,stream_key,revision,parent_event_id,record_id,payload_sha256,"
                              "previous_hash,event_hash,CASE WHEN length(payload)<=? THEN payload END AS record "
                              "FROM a12_events ORDER BY seq", (MAX_ENTRY_BYTES,)):
            encoded = row["record"]
            require(type(encoded) is bytes and 0 < len(encoded) <= MAX_ENTRY_BYTES, "REGISTRY_EVENT_SIZE_INVALID")
            payload_hash = sha256_bytes(encoded)
            require(row["payload_sha256"] == payload_hash, "REGISTRY_PAYLOAD_HASH_MISMATCH")
            value = parse_json(encoded, limit=MAX_ENTRY_BYTES)
            require(_EVENT_VALIDATOR.is_valid(value), "INVALID_REGISTRY_EVENT")
            require(all(ref in blobs for ref in value["blob_refs"]), "REGISTRY_RAW_MISSING")
            event = EvidenceEvent(value, raw_blobs={ref: blobs[ref] for ref in value["blob_refs"]})
            require(event.to_bytes() == encoded, "REGISTRY_NONCANONICAL_EVENT")
            require(row["seq"] == len(result) + 1 and row["previous_hash"] == previous
                    and row["event_hash"] == _chain_hash(row["seq"], previous, payload_hash),
                    "REGISTRY_CHAIN_MISMATCH")
            require(all(value[key] == row[key] for key in
                        ("event_id", "stream_key", "revision", "parent_event_id"))
                    and row["record_id"] == _record_id(value["evidence"]), "REGISTRY_INDEX_MISMATCH")
            _lineage(value, heads)
            identity = _record_id(value["evidence"])
            record_bytes = canonical_bytes(value["evidence"]["record"])
            require(identity not in records or records[identity] == record_bytes, "REGISTRY_RECORD_COLLISION")
            records[identity] = record_bytes
            referenced.update(value["blob_refs"])
            heads[value["stream_key"]] = value
            previous = row["event_hash"]
            result.append(event)
        require(referenced == set(blobs), "REGISTRY_UNREFERENCED_RAW")
        return result, heads, previous

    @staticmethod
    def _insert(db, event: EvidenceEvent, events: list[EvidenceEvent], heads: dict, previous: str):
        value = event.to_dict()
        for prior in events:
            if prior.event_id == event.event_id:
                require(prior.to_bytes() == event.to_bytes() and prior.raw_blobs() == event.raw_blobs(),
                        "REGISTRY_EVENT_COLLISION")
                return True, previous
        require(len(events) < MAX_EVENTS, "REGISTRY_EVENT_LIMIT")
        _lineage(value, heads)
        for ref, raw in event.raw_blobs().items():
            found = db.execute("SELECT payload FROM a12_raw WHERE sha256=?", (ref,)).fetchone()
            if found is None:
                db.execute("INSERT INTO a12_raw(sha256,size_bytes,payload) VALUES(?,?,?)", (ref, len(raw), raw))
            else:
                require(found[0] == raw, "REGISTRY_RAW_COLLISION")
        payload = event.to_bytes()
        payload_hash = sha256_bytes(payload)
        seq = len(events) + 1
        event_hash = _chain_hash(seq, previous, payload_hash)
        db.execute("INSERT INTO a12_events(seq,event_id,stream_key,revision,parent_event_id,record_id,payload,"
                   "payload_sha256,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (seq, event.event_id, value["stream_key"], value["revision"], value["parent_event_id"],
                    _record_id(value["evidence"]), payload, payload_hash, previous, event_hash))
        events.append(event)
        heads[value["stream_key"]] = value
        return False, event_hash

    def initialize(self) -> None:
        with self._connect(write=True) as db:
            self._load(db)

    def append(self, record, *, stream_key: str, recorded_at: str,
               parent_event_id: str | None = None, operation: str = "OBSERVATION",
               actor_ref: str | None = None, reason_ref: str | None = None) -> dict:
        _key(stream_key)
        evidence, blobs = _bundle(record)
        with self._connect(write=True) as db:
            events, heads, previous = self._load(db)
            parent = None
            if parent_event_id is not None:
                require(type(parent_event_id) is str and re.fullmatch(r"a12-event-[a-f0-9]{64}", parent_event_id) is not None,
                        "INVALID_REGISTRY_EVENT_ID")
                parent = next((event.to_dict() for event in events if event.event_id == parent_event_id), None)
                require(parent is not None, "REGISTRY_PARENT_NOT_FOUND")
            body = {"schema_version": "ProductEvidenceEvent.v1", "registry_version": REGISTRY_VERSION,
                    "stream_key": stream_key, "revision": 1 if parent is None else parent["revision"] + 1,
                    "parent_event_id": parent_event_id, "operation": operation, "actor_ref": actor_ref,
                    "reason_ref": reason_ref, "recorded_at": recorded_at,
                    "evidence": evidence, "blob_refs": sorted(blobs)}
            event = EvidenceEvent({**body, "event_id": _event_id(body)}, raw_blobs=blobs)
            duplicate, _ = self._insert(db, event, events, heads, previous)
        return {"event": event, "duplicate": duplicate}

    def replay(self, events: list[EvidenceEvent] | tuple[EvidenceEvent, ...]) -> list[dict]:
        require(type(events) in (list, tuple) and len(events) <= MAX_BATCH_EVENTS, "REGISTRY_BATCH_LIMIT")
        with self._connect(write=True) as db:
            existing, heads, previous = self._load(db)
            results, size = [], 0
            for item in events:
                require(type(item) is EvidenceEvent, "INVALID_REGISTRY_EVENT")
                size += len(item.to_bytes()) + sum(len(raw) for raw in item.raw_blobs().values())
                require(size <= MAX_BATCH_BYTES, "REGISTRY_BATCH_SIZE_LIMIT")
                checked = EvidenceEvent.from_bytes(item.to_bytes(), raw_blobs=item.raw_blobs())
                duplicate, previous = self._insert(db, checked, existing, heads, previous)
                results.append({"event": checked, "duplicate": duplicate})
        return results

    def events(self) -> tuple[EvidenceEvent, ...]:
        with self._connect() as db:
            return tuple(self._load(db)[0])

    def get(self, event_id: str) -> EvidenceEvent:
        require(type(event_id) is str and re.fullmatch(r"a12-event-[a-f0-9]{64}", event_id) is not None,
                "INVALID_REGISTRY_EVENT_ID")
        result = next((item for item in self.events() if item.event_id == event_id), None)
        require(result is not None, "REGISTRY_EVENT_NOT_FOUND")
        return result

    def history(self, stream_key: str) -> tuple[EvidenceEvent, ...]:
        _key(stream_key)
        return tuple(item for item in self.events() if item.to_dict()["stream_key"] == stream_key)

    def integrity_check(self) -> dict:
        with self._connect() as db:
            events, heads, last_hash = self._load(db)
            return {"schema_version": "ProductEvidenceRegistryIntegrity.v1", "registry_version": REGISTRY_VERSION,
                    "event_count": len(events), "stream_count": len(heads), "head_hash": last_hash,
                    "integrity": "LOCAL_HASH_AND_REPLAY_VERIFIED", "external_attestation": "NOT_ESTABLISHED"}
