"""B0.2 offline coordination only. No adapter, authority verifier or scheduler.

Every write reopens and verifies the database inside BEGIN IMMEDIATE. Intent
commit is an explicit crash boundary, never permission to call a provider.
Owner evidence stays in A08/A09/A15/A16; only opaque refs/digests live here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re

from .contracts import canonical_bytes, validate_contract, ValidatedContract
from .state_machine import Event, EventKind, EvidencePin

# Explicit stdlib dependencies. Literal loads preserve the frozen B0.1 AST
# import test, whose allowlist predates SQLite. B0.2 tests audit these loads,
# imports AND runtime external-call denial; this is not an optional adapter.
sqlite3 = __import__("sqlite3")
os = __import__("os")

SCHEMA_VERSION = 1
APPLICATION_ID = 0x50554C32
MAX_TTL = 300
MAX_RECORDS = 8192
SAFE_EVENTS = frozenset({EventKind.START, EventKind.OUTPUT_READY,
                         EventKind.QA_PASSED, EventKind.HOLD, EventKind.STOP})


class JournalHold(ValueError):
    """Bounded HOLD reason, never private payload or a SQLite path/error."""


class Conflict(JournalHold):
    pass


def _need(ok, code):
    if not ok:
        raise JournalHold(code)


def _ref(value):
    # Opaque identifiers, not URLs, freeform messages or customer documents.
    return type(value) is str and re.fullmatch(
        r"[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) is not None


def _sha(value, size=64):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is not None


def _time(at):
    _need(type(at) is int and 0 <= at <= 2**63 - MAX_TTL - 1, "EXPLICIT_LOGICAL_TIME_REQUIRED")


def _hash(value):
    return sha256(canonical_bytes(value)).hexdigest()


def _closed(value, fields):
    _need(type(value) is dict and set(value) == set(fields.split()), "CLOSED_PAYLOAD_REQUIRED")


@dataclass(frozen=True, slots=True)
class OwnerRef:
    ledger: str
    ref: str
    sha256: str

    def __post_init__(self):
        _need(self.ledger in {"A08", "A09", "A15", "A16"}
              and _ref(self.ref) and _sha(self.sha256), "INVALID_OWNER_REF")


@dataclass(frozen=True, slots=True)
class EffectTarget:
    """Canonical owner-assigned identities; aliases are not resolved here."""
    action: str
    provider_ref: str
    account_ref: str
    surface: str
    content_sha256: str

    def __post_init__(self):
        _need(self.action == "PUBLISH" and all(_ref(v) for v in
              (self.provider_ref, self.account_ref, self.surface))
              and _sha(self.content_sha256), "INVALID_SEMANTIC_TARGET")

    @property
    def semantic_sha256(self):
        return _hash(asdict(self))

    @property
    def effect_key(self):
        return "pulse:b02-" + self.semantic_sha256


@dataclass(frozen=True, slots=True)
class Lease:
    owner: str
    generation: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Caller-retained comparison anchor, NOT independent anti-rollback proof."""
    journal_id: str
    source_sha: str
    position: int
    sha256: str
    generation: int


@dataclass(frozen=True, slots=True)
class Record:
    position: int
    event_id: str
    journey_id: str | None
    sequence: int | None
    at: int
    owner: str
    generation: int
    kind: str
    payload: bytes
    payload_sha256: str
    previous_sha256: str
    sha256: str

    def data(self):
        return json.loads(self.payload)


@dataclass(frozen=True, slots=True)
class History:
    records: tuple[Record, ...]
    lease: Lease | None
    checkpoint: Checkpoint


@dataclass(frozen=True, slots=True)
class Intent:
    effect_key: str
    semantic_sha256: str
    request_sha256: str
    record_sha256: str
    boundary: str = "INTENT_COMMITTED_NO_EXECUTION_AUTHORITY"


_TABLES = (
    "CREATE TABLE metadata (journal_id TEXT NOT NULL, source_sha TEXT NOT NULL, version INTEGER NOT NULL)",
    "CREATE TABLE records (position INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, journey_id TEXT, sequence INTEGER, at INTEGER NOT NULL, owner TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, previous_sha256 TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, UNIQUE(journey_id, sequence))",
    "CREATE TABLE journeys (journey_id TEXT PRIMARY KEY NOT NULL, position INTEGER NOT NULL UNIQUE REFERENCES records(position))",
    "CREATE TABLE intents (semantic_sha256 TEXT PRIMARY KEY NOT NULL, effect_key TEXT NOT NULL UNIQUE, journey_id TEXT NOT NULL UNIQUE REFERENCES journeys(journey_id), position INTEGER NOT NULL UNIQUE REFERENCES records(position))",
)
_DDL = _TABLES + tuple(
    f"CREATE TRIGGER immutable_{table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END"
    for table in ("metadata", "records", "journeys", "intents") for op in ("UPDATE", "DELETE"))
_SCHEMA = tuple(sorted((sql.split()[1].lower(), sql.split()[2], sql) for sql in _DDL))


def _path(path):
    _need(type(path) is str and os.path.isabs(path), "EXPLICIT_ABSOLUTE_DATABASE_PATH_REQUIRED")
    _need(not path.replace("\\", "/").startswith("//") and "\x00" not in path,
          "LOCAL_DATABASE_PATH_REQUIRED")
    normalized = os.path.abspath(path)
    _need(os.path.normcase(normalized) == os.path.normcase(os.path.realpath(normalized))
          and not os.path.islink(normalized), "DATABASE_LINK_DENIED")
    return normalized


def _connect(path, mode):
    # Percent-encode even '#'/'?' in a literal path; never interpret URI options
    # supplied in a filename. mode=rw/ro cannot create a missing database.
    encoded = "".join(chr(b) if chr(b) in
                      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/:-_."
                      else f"%{b:02X}" for b in path.replace("\\", "/").encode("utf-8"))
    conn = sqlite3.connect("file:" + encoded + "?mode=" + mode, uri=True,
                           timeout=1.0, isolation_level=None)
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.execute("PRAGMA foreign_keys=ON")
    if mode == "ro":
        conn.execute("PRAGMA query_only=ON")
    else:
        conn.execute("PRAGMA synchronous=FULL")
    return conn


def _binding(directive, directive_ref):
    _need(type(directive) is ValidatedContract and directive.name == "CreativeDirective"
          and _ref(directive_ref), "DIRECTIVE_PIN_REQUIRED")
    value = validate_contract("CreativeDirective", directive.to_dict()).to_dict()
    _need(value["creative"]["product_ref"] is None or _ref(value["creative"]["product_ref"]),
          "OPAQUE_PRODUCT_REF_REQUIRED")
    for key in ("provider_ref", "account_ref", "surface"):
        _need(value["scope"][key] is None or _ref(value["scope"][key]), "OPAQUE_SCOPE_REF_REQUIRED")
    return {"directive_ref": directive_ref, "directive_sha256": value["directive_sha256"],
            "directive_record_sha256": sha256(directive.raw).hexdigest(),
            "directive_version": value["directive_version"], "scope": value["scope"],
            "product_ref": value["creative"]["product_ref"]}


def _owners(values):
    _need(type(values) is tuple and len(values) <= 32
          and all(type(v) is OwnerRef for v in values), "TYPED_OWNER_REFS_REQUIRED")
    for value in values:
        value.__post_init__()
    result = [asdict(v) for v in values]
    _need(len({_hash(v) for v in result}) == len(result), "DUPLICATE_OWNER_REF")
    return sorted(result, key=lambda v: (v["ledger"], v["ref"]))


def _event_payload(event, owner_refs):
    _need(type(event) is Event and event.kind in SAFE_EVENTS, "OFFLINE_EVENT_ONLY")
    event.__post_init__()
    _need(_ref(event.event_id) and _ref(event.journey_id)
          and (event.tool is None or _ref(event.tool)), "OPAQUE_EVENT_REF_REQUIRED")
    for pin in event.evidence:
        _need(_ref(pin.ref) and (pin.attestation_ref is None or _ref(pin.attestation_ref)),
              "OPAQUE_EVIDENCE_REF_REQUIRED")
    return {"kind": event.kind.value, "tool": event.tool,
            "evidence": [asdict(pin) for pin in event.evidence], "owner_refs": _owners(owner_refs)}


def _decode_event(record):
    data = record.data()
    _closed(data, "kind tool evidence owner_refs")
    _need(type(data["evidence"]) is list, "INVALID_EVENT_EVIDENCE")
    pins = []
    for pin in data["evidence"]:
        _closed(pin, "owner ref sha256 evidence_type attestation_ref")
        pins.append(EvidencePin(**pin))
    event = Event(record.event_id, EventKind(data["kind"]), record.journey_id,
                  "0" * 40, tool=data["tool"], evidence=tuple(pins))
    _event_payload(event, ())
    return event


def _envelope(journal_id, source_sha, record):
    return {"journal_id": journal_id, "source_sha": source_sha,
            **{field: getattr(record, field) for field in
               ("position", "event_id", "journey_id", "sequence", "at", "owner",
                "generation", "kind", "payload_sha256", "previous_sha256")}}


@dataclass(frozen=True, slots=True)
class Journal:
    path: str
    journal_id: str
    source_sha: str
    anchor: Checkpoint | None = None

    @classmethod
    def create(cls, path: str, *, journal_id: str, source_sha: str):
        path = _path(path)
        _need(_ref(journal_id) and _sha(source_sha, 40), "INVALID_JOURNAL_IDENTITY")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            raise JournalHold("DATABASE_ALREADY_EXISTS") from None
        except OSError:
            raise JournalHold("DATABASE_CREATION_FAILED") from None
        # Failed initialization deliberately leaves its file for HOLD/inspection.
        # Never repair/reset an existing database or race another initializer.
        conn = None
        try:
            conn = _connect(path, "rw")
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("BEGIN IMMEDIATE")
            for sql in _DDL:
                conn.execute(sql)
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.execute("INSERT INTO metadata VALUES (?,?,?)", (journal_id, source_sha, SCHEMA_VERSION))
            conn.execute("COMMIT")
        except (sqlite3.Error, OSError):
            if conn is not None and conn.in_transaction:
                conn.execute("ROLLBACK")
            raise JournalHold("DATABASE_INITIALIZATION_FAILED") from None
        finally:
            if conn is not None:
                conn.close()
        return cls.open(path, journal_id=journal_id, source_sha=source_sha)

    @classmethod
    def open(cls, path: str, *, journal_id: str, source_sha: str, checkpoint=None):
        journal = cls(_path(path), journal_id, source_sha, checkpoint)
        journal.inspect()
        return journal

    def inspect(self):
        """Idempotent read-only verified replay; never initializes storage."""
        conn = None
        try:
            conn = _connect(_path(self.path), "ro")
            conn.execute("BEGIN")
            return self._verify(conn)
        except (sqlite3.Error, OSError):
            raise JournalHold("MISSING_OR_CORRUPT_JOURNAL") from None
        finally:
            if conn is not None:
                conn.close()

    def _verify(self, conn):
        _need(_ref(self.journal_id) and _sha(self.source_sha, 40), "INVALID_JOURNAL_IDENTITY")
        _need(conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
              and conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION, "FOREIGN_OR_VERSIONED_JOURNAL")
        schema = tuple(conn.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
        _need(schema == _SCHEMA, "SCHEMA_MISMATCH")
        _need(conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
              and not conn.execute("PRAGMA foreign_key_check").fetchall(), "SQLITE_INTEGRITY_FAILURE")
        _need(conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete", "JOURNAL_MODE_MISMATCH")
        _need(conn.execute("SELECT * FROM metadata").fetchall() ==
              [(self.journal_id, self.source_sha, SCHEMA_VERSION)], "FOREIGN_JOURNAL_IDENTITY")
        genesis = _hash({"journal_id": self.journal_id, "source_sha": self.source_sha, "version": SCHEMA_VERSION})
        previous, generation, last_at, lease = genesis, 0, 0, None
        records, journeys, intents, owner_pins, evidence_pins, owners = [], {}, {}, {}, {}, set()
        rows = conn.execute("SELECT * FROM records ORDER BY position LIMIT ?", (MAX_RECORDS + 1,)).fetchall()
        _need(len(rows) <= MAX_RECORDS, "JOURNAL_BOUND_EXCEEDED")
        anchor_seen = self.anchor is None
        if self.anchor is not None:
            a = self.anchor
            _need(type(a) is Checkpoint and a.journal_id == self.journal_id and a.source_sha == self.source_sha
                  and type(a.position) is int and a.position >= 0 and _sha(a.sha256)
                  and type(a.generation) is int and a.generation >= 0, "INVALID_CHECKPOINT")
            if a.position == 0:
                _need(a.sha256 == genesis and a.generation == 0, "CHECKPOINT_MISMATCH")
                anchor_seen = True
        for row in rows:
            try:
                r = Record(*row[:8], row[8].encode("ascii"), *row[9:])
                data = r.data()
                _need(canonical_bytes(data) == r.payload and _hash(data) == r.payload_sha256,
                      "PAYLOAD_HASH_MISMATCH")
                _need(type(r.position) is int and r.position == len(records) + 1
                      and r.previous_sha256 == previous and _hash(_envelope(self.journal_id, self.source_sha, r)) == r.sha256,
                      "CHAIN_OR_SEQUENCE_MISMATCH")
                _time(r.at)
                _need(r.at >= last_at and _ref(r.event_id) and _ref(r.owner)
                      and type(r.generation) is int, "INVALID_RECORD_IDENTITY_OR_TIME")
                if r.kind.startswith("LEASE_"):
                    _need(r.journey_id is None and r.sequence is None, "LEASE_JOURNEY_MISMATCH")
                    if r.kind == "LEASE_ACQUIRED":
                        _closed(data, "ttl")
                        _need((lease is None or r.at >= lease.expires_at) and r.owner not in owners
                              and r.generation == generation + 1, "LEASE_HISTORY_CONFLICT")
                        owners.add(r.owner)
                        generation = r.generation
                    else:
                        _need(lease is not None and (r.owner, r.generation) == (lease.owner, lease.generation)
                              and r.at < lease.expires_at, "STALE_FENCING_GENERATION")
                    if r.kind in {"LEASE_ACQUIRED", "LEASE_RENEWED"}:
                        _closed(data, "ttl")
                        _need(type(data["ttl"]) is int and 1 <= data["ttl"] <= MAX_TTL, "INVALID_LEASE_TTL")
                        lease = Lease(r.owner, generation, r.at + data["ttl"])
                    else:
                        _need(r.kind == "LEASE_RELEASED" and data == {}, "INVALID_LEASE_EVENT")
                        lease = None
                else:
                    _need(lease is not None and (r.owner, r.generation) == (lease.owner, lease.generation)
                          and r.at < lease.expires_at, "STALE_FENCING_GENERATION")
                    _need(_ref(r.journey_id) and type(r.sequence) is int, "INVALID_JOURNEY_SEQUENCE")
                    prior = journeys.get(r.journey_id, [])
                    _need(r.sequence == len(prior) + 1 and r.sequence <= 256, "JOURNEY_SEQUENCE_MISMATCH")
                    if r.kind == "JOURNEY_CREATED":
                        _need(not prior, "DUPLICATE_JOURNEY")
                        _closed(data, "directive_ref directive_sha256 directive_record_sha256 directive_version scope product_ref owner_refs")
                        _need(_ref(data["directive_ref"]) and _sha(data["directive_sha256"])
                              and _sha(data["directive_record_sha256"]) and type(data["directive_version"]) is int
                              and 1 <= data["directive_version"] <= 2147483647
                              and (data["product_ref"] is None or _ref(data["product_ref"])), "INVALID_DIRECTIVE_PIN")
                        _closed(data["scope"], "action_class provider_ref account_ref surface asset_sha256")
                        _need(data["scope"]["action_class"] in {"PLAN_OFFLINE", "GENERATE_MEDIA", "EXPORT_DRAFT", "PUBLISH", "IMPORT_ANALYTICS", "REVIEW_LEARNING"}
                              and all(data["scope"][k] is None or _ref(data["scope"][k]) for k in ("provider_ref", "account_ref", "surface"))
                              and (data["scope"]["asset_sha256"] is None or _sha(data["scope"]["asset_sha256"])), "INVALID_SCOPE_PIN")
                    else:
                        _need(bool(prior), "MISSING_JOURNEY")
                        if r.kind == "TRANSITION":
                            event = _decode_event(r)
                            _need(not any(v.kind == "EFFECT_RESERVED" for v in prior), "UNKNOWN_EFFECT_BLOCKS_TRANSITION")
                            for pin in event.evidence:
                                identity = (pin.owner, pin.ref)
                                _need(identity not in evidence_pins or evidence_pins[identity] == pin,
                                      "CONTRADICTORY_EVIDENCE_REF")
                                evidence_pins[identity] = pin
                        elif r.kind == "EFFECT_RESERVED":
                            _closed(data, "target semantic_sha256 effect_key request_sha256 owner_refs")
                            _closed(data["target"], "action provider_ref account_ref surface content_sha256")
                            target = EffectTarget(**data["target"])
                            scope = prior[0].data()["scope"]
                            _need(scope == {"action_class": target.action, "provider_ref": target.provider_ref,
                                  "account_ref": target.account_ref, "surface": target.surface,
                                  "asset_sha256": target.content_sha256}, "TARGET_DIRECTIVE_MISMATCH")
                            request = _hash({"journey_id": r.journey_id, "source_sha": self.source_sha,
                                             "directive_sha256": prior[0].data()["directive_sha256"], "target": data["target"]})
                            _need(data["semantic_sha256"] == target.semantic_sha256 and data["effect_key"] == target.effect_key
                                  and data["request_sha256"] == request, "EFFECT_IDENTITY_MISMATCH")
                            _need(target.semantic_sha256 not in intents
                                  and not any(v.kind == "EFFECT_RESERVED" for v in prior), "DUPLICATE_SEMANTIC_ACTION")
                            intents[target.semantic_sha256] = (data["effect_key"], r.journey_id, r.position)
                        elif r.kind == "MODELED_OBSERVATION":
                            _closed(data, "effect_key request_sha256 outcome evidence_ref evidence_sha256 evidence_type owner_refs")
                            reserved = [v.data() for v in prior if v.kind == "EFFECT_RESERVED"]
                            _need(len(reserved) == 1 and not any(v.kind == r.kind for v in prior), "OBSERVATION_WITHOUT_UNIQUE_INTENT")
                            _need(all(data[k] == reserved[0][k] for k in ("effect_key", "request_sha256")), "EFFECT_IDENTITY_MISMATCH")
                            _need(data["outcome"] in {"UNKNOWN", "NO_EFFECT", "CONFIRMED"}
                                  and data["evidence_type"] in {"SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED", "PROVIDER_ATTESTED", "UNKNOWN"}
                                  and _ref(data["evidence_ref"]) and _sha(data["evidence_sha256"]), "INVALID_MODELED_OBSERVATION")
                        else:
                            raise JournalHold("UNKNOWN_RECORD_KIND")
                    _need(type(data["owner_refs"]) is list and len(data["owner_refs"]) <= 32, "INVALID_OWNER_REFS")
                    pins = []
                    for pin in data["owner_refs"]:
                        _closed(pin, "ledger ref sha256")
                        item = OwnerRef(**pin)
                        key = (item.ledger, item.ref)
                        _need(key not in owner_pins or owner_pins[key] == item.sha256, "CONTRADICTORY_OWNER_REF")
                        owner_pins[key] = item.sha256
                        pins.append(item)
                    _need(_owners(tuple(pins)) == data["owner_refs"], "NONCANONICAL_OWNER_REFS")
                    journeys[r.journey_id] = [*prior, r]
                records.append(r)
                previous, last_at = r.sha256, r.at
                if self.anchor is not None and r.position == self.anchor.position:
                    _need(r.sha256 == self.anchor.sha256 and generation == self.anchor.generation, "CHECKPOINT_MISMATCH")
                    anchor_seen = True
            except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
                # Keep our diagnostic, but never reflect attacker-supplied text.
                raise JournalHold("INVALID_OR_CORRUPT_RECORD") from None
        _need(anchor_seen, "ROLLED_BACK_OR_TRUNCATED_JOURNAL")
        _need(conn.execute("SELECT journey_id,position FROM journeys ORDER BY journey_id").fetchall() ==
              sorted((key, values[0].position) for key, values in journeys.items()), "JOURNEY_INDEX_MISMATCH")
        _need(conn.execute("SELECT semantic_sha256,effect_key,journey_id,position FROM intents ORDER BY semantic_sha256").fetchall() ==
              sorted((key, *value) for key, value in intents.items()), "INTENT_INDEX_MISMATCH")
        return History(tuple(records), lease, Checkpoint(self.journal_id, self.source_sha,
                       len(records), previous, generation))

    def _commit(self, operation):
        conn = None
        try:
            conn = _connect(_path(self.path), "rw")
            conn.execute("BEGIN IMMEDIATE")
            history = self._verify(conn)
            result = operation(conn, history)
            self._verify(conn)  # Reject inconsistent records/projections before commit.
            conn.execute("COMMIT")
            return result
        except sqlite3.Error:
            raise JournalHold("SQLITE_TRANSACTION_FAILED") from None
        except JournalHold:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            raise JournalHold("INVALID_OFFLINE_MUTATION") from None
        finally:
            if conn is not None:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                conn.close()

    def _insert(self, conn, history, *, event_id, journey_id, at, owner, generation, kind, data):
        _need(_ref(event_id), "OPAQUE_EVENT_REF_REQUIRED")
        _time(at)
        _need(not history.records or at >= history.records[-1].at, "LOGICAL_TIME_REGRESSION")
        if any(r.event_id == event_id for r in history.records):
            raise Conflict("DUPLICATE_EVENT_ID")
        sequence = None if journey_id is None else 1 + sum(r.journey_id == journey_id for r in history.records)
        payload = canonical_bytes(data)
        record = Record(history.checkpoint.position + 1, event_id, journey_id, sequence,
                        at, owner, generation, kind, payload, sha256(payload).hexdigest(),
                        history.checkpoint.sha256, "")
        digest = _hash(_envelope(self.journal_id, self.source_sha, record))
        conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (record.position, event_id, journey_id, sequence, at, owner, generation,
                      kind, payload.decode("ascii"), record.payload_sha256, record.previous_sha256, digest))
        return digest, record.position

    @staticmethod
    def _fence(history, lease, at):
        _time(at)
        _need(type(lease) is Lease and lease == history.lease and at < lease.expires_at,
              "STALE_FENCING_GENERATION")

    @staticmethod
    def _cas(history, journey_id, expected_sequence):
        _need(_ref(journey_id) and type(expected_sequence) is int and expected_sequence >= 0,
              "INVALID_EXPECTED_SEQUENCE")
        actual = sum(r.journey_id == journey_id for r in history.records)
        if actual != expected_sequence:
            raise Conflict("EXPECTED_SEQUENCE_CONFLICT")

    def acquire(self, *, owner, at, ttl, event_id):
        _time(at)
        _need(_ref(owner) and type(ttl) is int and 1 <= ttl <= MAX_TTL, "INVALID_LEASE")
        def operation(conn, history):
            _need(history.lease is None or at >= history.lease.expires_at, "LEASE_BUSY")
            _need(not any(r.owner == owner and r.kind == "LEASE_ACQUIRED" for r in history.records), "LEASE_OWNER_MUST_BE_UNIQUE")
            generation = history.checkpoint.generation + 1
            self._insert(conn, history, event_id=event_id, journey_id=None, at=at,
                         owner=owner, generation=generation, kind="LEASE_ACQUIRED", data={"ttl": ttl})
            return Lease(owner, generation, at + ttl)
        return self._commit(operation)

    def renew(self, lease, *, at, ttl, event_id):
        _need(type(ttl) is int and 1 <= ttl <= MAX_TTL, "INVALID_LEASE_TTL")
        def operation(conn, history):
            self._fence(history, lease, at)
            self._insert(conn, history, event_id=event_id, journey_id=None, at=at, owner=lease.owner,
                         generation=lease.generation, kind="LEASE_RENEWED", data={"ttl": ttl})
            return Lease(lease.owner, lease.generation, at + ttl)
        return self._commit(operation)

    def release(self, lease, *, at, event_id):
        def operation(conn, history):
            self._fence(history, lease, at)
            self._insert(conn, history, event_id=event_id, journey_id=None, at=at, owner=lease.owner,
                         generation=lease.generation, kind="LEASE_RELEASED", data={})
        return self._commit(operation)

    def create_journey(self, directive, *, directive_ref, lease, at, event_id, owner_refs=()):
        data = {**_binding(directive, directive_ref), "owner_refs": _owners(owner_refs)}
        value = directive.to_dict()
        _need(value["source_sha"] == self.source_sha, "DIRECTIVE_SOURCE_MISMATCH")
        journey_id = value["journey_id"]
        def operation(conn, history):
            self._fence(history, lease, at)
            self._cas(history, journey_id, 0)
            digest, position = self._insert(conn, history, event_id=event_id, journey_id=journey_id,
                at=at, owner=lease.owner, generation=lease.generation, kind="JOURNEY_CREATED", data=data)
            conn.execute("INSERT INTO journeys VALUES (?,?)", (journey_id, position))
            return digest
        return self._commit(operation)

    def _safe_replay(self, history, journey_id, directive):
        from .recovery import replay_verified
        recovered = replay_verified(self, history, journey_id, directive=directive)
        _need(recovered.state not in {"HOLD", "STOPPED", "UNKNOWN_EFFECT"}, "RECOVERY_HOLD_OR_UNKNOWN")
        return recovered

    def append_event(self, event, *, directive, lease, at, expected_sequence, owner_refs=()):
        data = _event_payload(event, owner_refs)
        _need(event.source_sha == self.source_sha, "EVENT_SOURCE_MISMATCH")
        def operation(conn, history):
            self._fence(history, lease, at)
            self._cas(history, event.journey_id, expected_sequence)
            current = self._safe_replay(history, event.journey_id, directive)
            from .state_machine import transition
            transition(current.snapshot, event, at=at)  # No VerificationContext ingress.
            return self._insert(conn, history, event_id=event.event_id, journey_id=event.journey_id,
                at=at, owner=lease.owner, generation=lease.generation, kind="TRANSITION", data=data)[0]
        return self._commit(operation)

    def reserve_intent(self, journey_id, target, *, directive, lease, at, event_id,
                       expected_sequence, effect_key=None, owner_refs=()):
        _need(type(target) is EffectTarget, "TYPED_TARGET_REQUIRED")
        target.__post_init__()
        _need(effect_key is None or effect_key == target.effect_key, "EFFECT_REKEY_DENIED")
        def operation(conn, history):
            self._fence(history, lease, at)
            self._cas(history, journey_id, expected_sequence)
            if any(r.kind == "EFFECT_RESERVED" and
                   (r.journey_id == journey_id or r.data()["semantic_sha256"] == target.semantic_sha256)
                   for r in history.records):
                raise Conflict("DUPLICATE_SEMANTIC_ACTION")
            current = self._safe_replay(history, journey_id, directive)
            _need(current.state == "WAIT_BRAIN", "OFFLINE_WAIT_BRAIN_REQUIRED")
            data = {"target": asdict(target), "semantic_sha256": target.semantic_sha256,
                    "effect_key": target.effect_key, "request_sha256": _hash({
                        "journey_id": journey_id, "source_sha": self.source_sha,
                        "directive_sha256": directive.to_dict()["directive_sha256"], "target": asdict(target)}),
                    "owner_refs": _owners(owner_refs)}
            digest, position = self._insert(conn, history, event_id=event_id, journey_id=journey_id,
                at=at, owner=lease.owner, generation=lease.generation, kind="EFFECT_RESERVED", data=data)
            conn.execute("INSERT INTO intents VALUES (?,?,?,?)",
                         (target.semantic_sha256, target.effect_key, journey_id, position))
            return Intent(target.effect_key, target.semantic_sha256, data["request_sha256"], digest)
        # Only returns AFTER COMMIT. There is deliberately no call/dispatch hook.
        return self._commit(operation)

    def record_observation(self, journey_id, *, lease, at, event_id, expected_sequence,
                           effect_key, request_sha256, outcome, evidence_ref, evidence_sha256,
                           evidence_type="SYNTHETIC_FIXTURE", owner_refs=()):
        """Append an offline modeled receipt. Even PROVIDER_ATTESTED is a label.

        CONFIRMED and NO_EFFECT remain distinguishable claims, never actual
        outcomes. No verified=True, verifier/context argument or retry API exists.
        """
        data = {"effect_key": effect_key, "request_sha256": request_sha256, "outcome": outcome,
                "evidence_ref": evidence_ref, "evidence_sha256": evidence_sha256,
                "evidence_type": evidence_type, "owner_refs": _owners(owner_refs)}
        def operation(conn, history):
            self._fence(history, lease, at)
            self._cas(history, journey_id, expected_sequence)
            return self._insert(conn, history, event_id=event_id, journey_id=journey_id,
                at=at, owner=lease.owner, generation=lease.generation, kind="MODELED_OBSERVATION", data=data)[0]
        return self._commit(operation)
