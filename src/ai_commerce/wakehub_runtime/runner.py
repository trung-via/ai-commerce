"""Bounded R1 composition with one private SQLite event/attempt journal.

Adapters are trusted application inputs, never loaded from event payloads. The
native path has no effect authority. Synthetic ACK proves only offline wiring.
RESERVED is durable before calling the sender; an interrupted reservation is
UNKNOWN_EFFECT forever here. Recovery never replays a send or a handoff.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from typing import Callable, Literal, Protocol

from ai_commerce.production_orchestration.contracts import (
    CONSUMER_ROUTES, OrchestrationError, ProductionStageCompleted, ProductionStageReceipt,
    Provenance, RouteAuthorization, StageSpec, bytes_digest, digest,
)
from ai_commerce.production_orchestration.orchestrator import ProductionPipelineOrchestrator
from ai_commerce.remote import chrome_acquisition
from ai_commerce.remote._private_state import (
    PrivateStateError, _Transaction, _linked, _regular, _windows_handle,
)
from ai_commerce.remote.cdp_doorbell import LocalChromeDoorbellAdapter
from ai_commerce.remote.conversation_binding import (
    BindingError, ResolvedBinding, _CHAT, _ROUTE, _decode_unique, resolve_binding,
)
from ai_commerce.remote.local_completion_handoff import HandoffBlocked, _private_identity
from ai_commerce.remote.local_wake_trigger import GitHubExactRunReader, PinnedRun, TriggerBlocked
from ai_commerce.remote.github_artifact_authority import GitHubRestArtifactAuthority
from ai_commerce.remote.github_read_retry import GitHubReadBlocked, GitHubReadBudget
from ai_commerce.remote.local_run_registration import (
    RegistrationBlocked, RegistrationIntent, _record_valid,
)
from ai_commerce.remote.signed_origin_consent import SignedOriginConsentAuthority
from ai_commerce.remote.trusted_origin import LocalCallerAttestation
from ai_commerce.remote.wake_adapter import (
    ArtifactAuthority, GitHubArtifactProvenance, ProvenanceError, WakeReceipt, verify_provenance,
)

Evidence = Literal["GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE"]
Status = Literal["PENDING", "RESERVED", "UI_TURN_OBSERVED", "BRAIN_CORRELATED", "ACK",
                 "BLOCKED_EXTERNAL", "FAILED", "UNKNOWN_EFFECT", "HOLD"]
_CODE_ROOT = Path(__file__).resolve().parents[3]
_NEXT = {
    "PENDING": {"RESERVED", "BLOCKED_EXTERNAL", "FAILED"},
    "RESERVED": {"UI_TURN_OBSERVED", "UNKNOWN_EFFECT", "BLOCKED_EXTERNAL"},
    "UI_TURN_OBSERVED": {"BRAIN_CORRELATED", "BLOCKED_EXTERNAL", "UNKNOWN_EFFECT"},
    "BRAIN_CORRELATED": {"ACK", "BLOCKED_EXTERNAL", "UNKNOWN_EFFECT"},
}


class RuntimeHold(ValueError):
    """Only fixed diagnostic codes; no private routes or remote payloads."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeHold(code)


def _safe_code(error: Exception, fallback: str) -> str:
    code = str(error)
    return code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) else fallback


@dataclass(frozen=True, repr=False)
class Registration:
    """One immutable Chat A, source revision, route and marker generation."""
    binding: ResolvedBinding
    source_sha: str
    evidence_type: Evidence

    def __post_init__(self) -> None:
        b = self.binding
        _require(type(b) is ResolvedBinding and type(b.route_id) is str
                 and _ROUTE.fullmatch(b.route_id) is not None
                 and type(b.generation) is int and b.generation > 0
                 and type(b.chat_url) is str and _CHAT.fullmatch(b.chat_url) is not None
                 and b.cdp_endpoint == "http://127.0.0.1:9444", "REGISTRATION_INVALID")
        _require(type(self.source_sha) is str and re.fullmatch(r"[0-9a-f]{40}", self.source_sha)
                 is not None, "SOURCE_INVALID")
        _require(self.evidence_type in ("GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE"), "EVIDENCE_INVALID")

    @property
    def marker(self) -> str:
        # A correlation commitment, not a grant or native origin attestation.
        return "wakehub-r1:" + digest(asdict(self))


@dataclass(frozen=True)
class Event:
    """Detached selectors returned only by a trusted, provenance-checking source."""
    event_id: str
    source_sha: str
    attempt: int
    payload_sha256: str
    evidence_type: Evidence

    def __post_init__(self) -> None:
        _require(type(self.event_id) is str and re.fullmatch(r"ef02-completion-v1:[0-9a-f]{64}",
                                                          self.event_id) is not None, "EVENT_INVALID")
        _require(type(self.source_sha) is str and re.fullmatch(r"[0-9a-f]{40}", self.source_sha)
                 is not None and type(self.attempt) is int and self.attempt > 0, "EVENT_INVALID")
        _require(type(self.payload_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", self.payload_sha256)
                 is not None and self.evidence_type in ("GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE"),
                 "EVENT_INVALID")

    def key(self, registration: Registration) -> str:
        return digest({"event_id": self.event_id, "source_sha": self.source_sha,
                       "route_generation": registration.binding.generation, "attempt": self.attempt})


class EventSource(Protocol):
    def next_event(self) -> Event | None: ...


class GitHubEventSource:
    """Compose the existing exact-run reader and independent D5 authentication.

    Reader results are candidates, never authority. No polling loop, new queue,
    GitHub writes, or tokens stored in the journal. Callers inject authenticated
    existing implementations at a trusted local composition root.
    """
    def __init__(self, reader: GitHubExactRunReader, target: PinnedRun,
                 authority: ArtifactAuthority, *, expected_event_id: str | None = None):
        self.reader, self.target, self.authority = reader, target, authority
        self.expected_event_id = expected_event_id
        self.verified_event: dict | None = None

    def next_event(self) -> Event | None:
        self.verified_event = None
        candidate = self.reader.candidate(self.target)
        if candidate is None:
            return None
        event, report = deepcopy(candidate)
        provenance = GitHubArtifactProvenance(
            repository=event["repository"], run_id=self.target.run_id,
            run_attempt=self.target.run_attempt,
            artifact_name=f"ef02-results-{self.target.run_id}-{self.target.run_attempt}",
            workflow_run_status="completed", attestation_verified=True)
        verify_provenance(event, report, provenance, authority=self.authority)
        _require(event["source_sha"] == self.target.source_sha
                 and event["issue_number"] == self.target.issue_number
                 and event["batch_id"] == self.target.batch_id
                 and event["run_id"] == self.target.run_id
                 and event["run_attempt"] == self.target.run_attempt, "SOURCE_TARGET_DRIFT")
        _require(self.expected_event_id is None or event["event_id"] == self.expected_event_id,
                 "REGISTERED_EVENT_DRIFT")
        self.verified_event = deepcopy(event)
        return Event(event["event_id"], event["source_sha"], event["run_attempt"],
                     digest(event), "GITHUB_AUTHENTICATED")


@dataclass(frozen=True, repr=False)
class Observation:
    receipt: WakeReceipt
    chat_url: str
    marker: str
    user_turn_ref: str


class Sender(Protocol):
    evidence_type: Evidence

    def preflight(self, registration: Registration) -> str | None: ...
    def send(self, event: Event, registration: Registration) -> Observation: ...


class NativeChrome9444:
    """Future native seam; R1 never starts Chrome, attaches CDP or sends."""
    evidence_type = "GITHUB_AUTHENTICATED"

    def __init__(self, repository_root: Path):
        self.repository_root = repository_root
        self.adapter = LocalChromeDoorbellAdapter(allow_submit=False)

    def preflight(self, registration: Registration) -> str:
        return "UNSUPPORTED_NATIVE_OWNER"

    def send(self, event: Event, registration: Registration) -> Observation:
        raise RuntimeHold("UNSUPPORTED_NATIVE_OWNER")


@dataclass(frozen=True, repr=False)
class ProductionHandoff:
    completed: ProductionStageCompleted
    source_bytes: bytes
    input_json: str
    output_json: str
    receipt: ProductionStageReceipt


@dataclass(frozen=True, repr=False)
class BrainCorrelation:
    event_key: str
    marker: str
    user_turn_ref: str
    correlation_ref: str
    evidence_type: Evidence
    handoff: ProductionHandoff


class BrainAdapter(Protocol):
    """Independently authenticate Brain correlation AND consumer-specific receipt.

    A UI user turn, assistant text, or callback flag is insufficient. The caller
    supplies a scoped P0 orchestrator with independently verified receipt pins;
    the runner never constructs a global production orchestrator or real pins.
    """
    def correlate(self, event: Event, registration: Registration,
                  observation: Observation) -> BrainCorrelation | None: ...


@dataclass(frozen=True)
class Result:
    status: Status
    code: str
    evidence_type: str
    replayed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _private_path(path: Path, repository_root: Path) -> None:
    """No checkout, links, reparse points, hard links, UNC or alternate streams."""
    _require(path.is_absolute() and repository_root.is_absolute()
             and ".." not in path.parts and not path.drive.startswith("\\\\")
             and ":" not in path.name and not path.name.endswith((".", " ")),
             "JOURNAL_LOCATION_INVALID")
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            _require(not _linked(part.lstat()), "JOURNAL_LOCATION_INVALID")
    for root in (repository_root.resolve(), _CODE_ROOT):
        _require(path.resolve() != root and root not in path.resolve().parents,
                 "JOURNAL_LOCATION_INVALID")
    _require(path.parent.is_dir() and (not path.exists() or _regular(path.lstat())),
             "JOURNAL_LOCATION_INVALID")


@contextmanager
def _private_access(path: Path, repository_root: Path):
    """Pin ancestors while SQLite opens its own handles; never write JSON state."""
    handles = []
    try:
        _private_path(path, repository_root)
        if os.name == "nt":
            for part in reversed(path.parents):
                handles.append(_windows_handle(part, 0, directory=True))
            directory_fd = None
        else:
            directory_fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            handles.append(directory_fd)
            for part in path.parent.parts[1:]:
                directory_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=directory_fd)
                handles.append(directory_fd)
        owner = _private_identity(path.parent)
        if path.exists():
            _require(_private_identity(path) == owner, "JOURNAL_OWNER_DRIFT")
        # Reuse only D5's safe handle opener, never its JSON read/write path.
        yield owner, _Transaction(SimpleNamespace(path=path), directory_fd,
                                  new_file_owner_sid=owner if os.name == "nt" else None)
    finally:
        for handle in reversed(handles):
            if os.name == "nt":
                handle[1](handle[0])
            else:
                os.close(handle)


_TABLES = {
    "identity": "CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "owner TEXT NOT NULL, registration TEXT NOT NULL)",
    "events": "CREATE TABLE events (event_key TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, "
              "source_sha TEXT NOT NULL, attempt INTEGER NOT NULL, generation INTEGER NOT NULL, "
              "record TEXT NOT NULL)",
}
_APP_ID = 0x57485231


def _validate_records(data: dict, registration: Registration) -> None:
    _require(type(data["events"]) is dict and len(data["events"]) <= 128, "JOURNAL_INVALID")
    seen = set()
    for key, record in data["events"].items():
        _require(type(record) is dict and set(record) == {"event", "status", "code", "history", "evidence"}
                 and type(record["event"]) is dict, "JOURNAL_INVALID")
        try:
            saved = Event(**record["event"])
        except (TypeError, RuntimeHold):
            raise RuntimeHold("JOURNAL_INVALID") from None
        _require(saved.key(registration) == key and saved.event_id not in seen
                 and type(record["history"]) is list and 1 <= len(record["history"]) <= 6
                 and record["history"][0] == "PENDING" and record["status"] == record["history"][-1]
                 and type(record["code"]) is str
                 and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", record["code"]) is not None
                 and type(record["evidence"]) is dict, "JOURNAL_INVALID")
        seen.add(saved.event_id)
        _require(all(b in _NEXT.get(a, set()) for a, b in
                     zip(record["history"], record["history"][1:])), "JOURNAL_INVALID")
        proof = record["evidence"]
        if "UI_TURN_OBSERVED" in record["history"]:
            _require(proof.get("marker") == registration.marker
                     and type(proof.get("user_turn_ref")) is str and bool(proof["user_turn_ref"]),
                     "JOURNAL_PROOF_MISSING")
        if "BRAIN_CORRELATED" in record["history"]:
            _require(all(type(proof.get(k)) is str and bool(proof[k]) for k in
                         ("correlation_ref", "consumer_id", "receipt_sha256", "completed_sha256")),
                     "JOURNAL_PROOF_MISSING")
        if record["status"] == "ACK":
            _require(type(proof.get("consumer_receipt")) is dict
                     and type(proof.get("completed")) is dict, "JOURNAL_PROOF_MISSING")
            try:
                receipt = ProductionStageReceipt.from_dict(proof["consumer_receipt"])
                completed = ProductionStageCompleted.from_dict(proof["completed"])
            except (OrchestrationError, TypeError, KeyError, ValueError):
                raise RuntimeHold("JOURNAL_PROOF_INVALID") from None
            _require(record["code"] == "CONSUMER_RECEIPT_RECORDED"
                     and receipt.sha256 == proof["receipt_sha256"]
                     and completed.sha256 == proof["completed_sha256"]
                     and receipt.completed_sha256 == completed.sha256
                     and receipt.spec == completed.spec
                     and receipt.consumer_id == proof["consumer_id"]
                     and completed.spec.idempotency_key == key
                     and completed.spec.provenance.source_ref == "wakehub-event:" + key
                     and completed.source_sha256 == bytes_digest(
                         json.dumps(asdict(saved), sort_keys=True).encode("utf-8")),
                     "JOURNAL_PROOF_INVALID")


class _SQLiteTransaction:
    def __init__(self, connection, journal, registration, owner):
        self.connection, self.journal = connection, journal
        self.registration, self.owner = registration, owner

    def write(self, data: dict) -> None:
        _validate_records(data, self.registration)
        self.journal._owner(self.owner)
        db = self.connection
        try:
            db.execute("BEGIN IMMEDIATE")
            for key, record in data["events"].items():
                event = record["event"]
                db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?) "
                           "ON CONFLICT(event_key) DO UPDATE SET record=excluded.record",
                           (key, event["event_id"], event["source_sha"], event["attempt"],
                            self.registration.binding.generation, json.dumps(record, sort_keys=True)))
            # Includes the rollback sidecar created by SQLite, before commit.
            self.journal._owner(self.owner)
            db.commit()  # FULL/EXTRA synchronous; no effect may precede this commit.
        except BaseException:
            db.rollback()
            raise


class Journal:
    """One SQLite database, private custody and one OS lock across an iteration.

    Individual state transitions commit durably before crossing an effect seam.
    JSON is refused, not migrated implicitly. Existing empty/corrupt databases
    are never initialized. Missing databases are enrolled only under the lock.
    """
    def __init__(self, path: Path, repository_root: Path):
        self.path, self.repository_root = Path(path), Path(repository_root)
        _private_path(self.path, self.repository_root)
        _require(not self.path.name.endswith((".lock", ".part", "-journal", "-wal", "-shm")),
                 "JOURNAL_LOCATION_INVALID")

    def _owner(self, expected: str) -> None:
        _require(_private_identity(self.path.parent) == expected, "JOURNAL_OWNER_DRIFT")
        for suffix in ("", ".lock", "-journal", "-wal", "-shm"):
            path = self.path.with_name(self.path.name + suffix)
            _private_path(path, self.repository_root)
            if path.exists():
                _require(_private_identity(path) == expected, "JOURNAL_OWNER_DRIFT")
                _require(suffix not in ("-wal", "-shm"), "JOURNAL_SCHEMA_INCOMPATIBLE")

    @contextmanager
    def locked(self, registration: Registration):
        db, lock_fd, unlock = None, None, None
        try:
            with _private_access(self.path, self.repository_root) as (owner, files):
                self._owner(owner)
                lock_existed = self.path.with_name(self.path.name + ".lock").exists()
                lock_fd = files.open(self.path.name + ".lock", os.O_RDWR | os.O_CREAT)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                        unlock = lambda: (os.lseek(lock_fd, 0, os.SEEK_SET),
                                          msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1))
                    else:
                        import fcntl
                        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        unlock = lambda: fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    raise RuntimeHold("JOURNAL_BUSY") from None
                self._owner(owner)
                new = not self.path.exists()
                if new:
                    _require(not lock_existed
                             and not self.path.with_name(self.path.name + "-journal").exists(),
                             "JOURNAL_PROOF_MISSING")
                    fd = files.open(self.path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                    os.close(fd)
                else:
                    fd = files.open(self.path.name, os.O_RDONLY)
                    with os.fdopen(fd, "rb") as stream:
                        header = stream.read(16)
                    _require(not header.lstrip().startswith((b"{", b"[")), "JOURNAL_MIGRATION_REQUIRED")
                    _require(header == b"SQLite format 3\x00", "JOURNAL_CORRUPT")
                self._owner(owner)
                db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0,
                                     isolation_level=None)
                db.execute("PRAGMA trusted_schema=OFF")
                _require(db.execute("PRAGMA quick_check").fetchall() == [("ok",)], "JOURNAL_CORRUPT")
                if not new:
                    _require(db.execute("PRAGMA user_version").fetchone() == (2,)
                             and db.execute("PRAGMA application_id").fetchone() == (_APP_ID,)
                             and db.execute("SELECT type, name, sql FROM sqlite_master "
                                            "WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                             == [("table", name, sql) for name, sql in sorted(_TABLES.items())],
                             "JOURNAL_SCHEMA_INCOMPATIBLE")
                    rows = db.execute("SELECT owner, registration FROM identity WHERE singleton=1").fetchall()
                    _require(len(rows) == 1 and rows[0][0] == owner, "JOURNAL_OWNER_DRIFT")
                    _require(_decode_unique(rows[0][1]) == asdict(registration), "REGISTRATION_DRIFT")
                # Provision the persistent rollback sidecar with the same owner.
                fd = files.open(self.path.name + "-journal", os.O_RDWR | os.O_CREAT)
                os.close(fd)
                _require(db.execute("PRAGMA journal_mode=PERSIST").fetchone() == ("persist",),
                         "JOURNAL_SCHEMA_INCOMPATIBLE")
                db.execute("PRAGMA synchronous=EXTRA")
                if new:
                    db.execute("BEGIN IMMEDIATE")
                    for sql in _TABLES.values():
                        db.execute(sql)
                    db.execute("INSERT INTO identity VALUES (1, ?, ?)",
                               (owner, json.dumps(asdict(registration), sort_keys=True)))
                    db.execute("PRAGMA user_version=2")
                    db.execute(f"PRAGMA application_id={_APP_ID}")
                    self._owner(owner)
                    db.commit()
                    if os.name != "nt":
                        os.fsync(files.directory_fd)
                data = {"events": {}}
                for key, event_id, sha, attempt, generation, raw in db.execute("SELECT * FROM events"):
                    record = _decode_unique(raw)
                    _require(type(record) is dict and type(record.get("event")) is dict, "JOURNAL_INVALID")
                    event = record["event"]
                    _require((event_id, sha, attempt, generation) ==
                             (event.get("event_id"), event.get("source_sha"), event.get("attempt"),
                              registration.binding.generation), "JOURNAL_INVALID")
                    data["events"][key] = record
                _validate_records(data, registration)
                txn = _SQLiteTransaction(db, self, registration, owner)
                # Recover every interrupted attempt, even when ingress selects another event.
                interrupted = [r for r in data["events"].values() if r["status"] in
                               ("RESERVED", "UI_TURN_OBSERVED", "BRAIN_CORRELATED")]
                for record in interrupted:
                    record.update(status="UNKNOWN_EFFECT", code="INTERRUPTED_NO_REPLAY",
                                  history=[*record["history"], "UNKNOWN_EFFECT"])
                if interrupted:
                    txn.write(data)
                yield txn, data
                self._owner(owner)
        except sqlite3.DatabaseError as exc:
            code = "JOURNAL_BUSY" if isinstance(exc, sqlite3.OperationalError) and \
                getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) \
                else "JOURNAL_CORRUPT"
            raise RuntimeHold(code) from None
        finally:
            if db is not None:
                db.close()
            if unlock is not None:
                unlock()
            if lock_fd is not None:
                os.close(lock_fd)


class Runner:
    """Dependency-injected single-process runner; run_once consumes at most one event."""
    def __init__(self, *, source: EventSource, registration: Registration, journal: Journal,
                 sender: Sender, brain: BrainAdapter, orchestrator: ProductionPipelineOrchestrator,
                 grant_verifier: Callable[[Event, Registration], bool], read_only: bool = False):
        _require(type(orchestrator) is ProductionPipelineOrchestrator, "PRODUCTION_ORCHESTRATOR_REQUIRED")
        self.source, self.registration, self.journal = source, registration, journal
        self.sender, self.brain, self.orchestrator = sender, brain, orchestrator
        self.grant_verifier = grant_verifier
        self.read_only = read_only

    def run_once(self) -> Result:
        evidence = self.registration.evidence_type
        try:
            event = self.source.next_event()
            if event is None:
                if self.journal.path.exists():
                    # Recover enrolled attempts even when GitHub has no new candidate.
                    with self.journal.locked(self.registration):
                        pass
                return Result("HOLD", "NO_EVENT", evidence)
            _require(type(event) is Event, "EVENT_REQUIRED")
            with self.journal.locked(self.registration) as (txn, data):
                key = event.key(self.registration)
                records = data["events"]
                if key in records:
                    record = records[key]
                    _require(record["event"] == asdict(event), "EVENT_IDENTITY_DRIFT")
                    if record["status"] in ("RESERVED", "UI_TURN_OBSERVED", "BRAIN_CORRELATED"):
                        self._move(txn, data, record, "UNKNOWN_EFFECT", "INTERRUPTED_NO_REPLAY")
                    if record["status"] != "PENDING":
                        if self.read_only and record["status"] == "ACK":
                            return Result("HOLD", "NATIVE_RECEIPT_UNPROVEN", evidence, replayed=True)
                        return Result(record["status"], record["code"], evidence, replayed=True)
                else:
                    _require(not any(r["event"]["event_id"] == event.event_id for r in records.values()),
                             "EVENT_IDENTITY_DRIFT")
                    _require(len(records) < 128, "JOURNAL_FULL")
                    record = {"event": asdict(event), "status": "PENDING", "code": "EVENT_PENDING",
                              "history": ["PENDING"], "evidence": {}}
                    records[key] = record
                    txn.write(data)
                if event.source_sha != self.registration.source_sha:
                    return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "SOURCE_DRIFT")
                if event.evidence_type != evidence or self.sender.evidence_type != evidence:
                    return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "EVIDENCE_MODE_MISMATCH")
                # R1 has exactly one native owner and it cannot send. A generic
                # injected sender or a readiness boolean cannot bypass that seam.
                if evidence == "GITHUB_AUTHENTICATED" and type(self.sender) is not NativeChrome9444:
                    return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "UNSUPPORTED_NATIVE_OWNER")
                try:
                    granted = self.grant_verifier(event, self.registration) is True
                    if self.read_only:
                        _require(evidence == "GITHUB_AUTHENTICATED"
                                 and type(self.source) is GitHubEventSource
                                 and type(self.sender) is NativeChrome9444, "READ_ONLY_COMPOSITION_INVALID")
                        if granted:
                            # Durable PENDING is safe for a future separately authorized R2/R3.
                            return Result("HOLD", "SOURCE_READY_NATIVE_UNKNOWN", evidence)
                    code = self.sender.preflight(self.registration) if granted else "MISSING_GRANT"
                    _require(code is None or (type(code) is str and
                             re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) is not None),
                             "PREFLIGHT_RESULT_INVALID")
                except (RuntimeHold, BindingError, HandoffBlocked) as exc:
                    return Result("HOLD", _safe_code(exc, "PREFLIGHT_UNPROVEN"), evidence)
                except Exception:
                    code = "PREFLIGHT_UNPROVEN"
                if code is not None:
                    return self._finish(txn, data, record, "BLOCKED_EXTERNAL", code)
                self._move(txn, data, record, "RESERVED", "SEND_RESERVED")
                try:
                    observation = self.sender.send(event, self.registration)
                    b = self.registration.binding
                    _require(type(observation) is Observation and type(observation.receipt) is WakeReceipt,
                             "UI_RECEIPT_INVALID")
                    receipt = observation.receipt
                    _require((receipt.event_id, receipt.route_id, receipt.generation) ==
                             (event.event_id, b.route_id, b.generation)
                             and observation.chat_url == b.chat_url
                             and observation.marker == self.registration.marker
                             and type(observation.user_turn_ref) is str and bool(observation.user_turn_ref)
                             and receipt.delivery_status == "UNKNOWN"
                             and receipt.error_code == "UI_USER_TURN_OBSERVED"
                             and not receipt.brain_continuation_proven, "UI_OBSERVATION_UNPROVEN")
                    self._move(txn, data, record, "UI_TURN_OBSERVED", "UI_USER_TURN_OBSERVED",
                               proof={"user_turn_ref": observation.user_turn_ref,
                                      "marker": observation.marker})
                    correlation = self.brain.correlate(event, self.registration, observation)
                    if correlation is None:
                        return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "BRAIN_RECEIPT_UNAVAILABLE")
                    _require(type(correlation) is BrainCorrelation
                             and (correlation.event_key, correlation.marker, correlation.user_turn_ref,
                                  correlation.evidence_type) ==
                             (key, self.registration.marker, observation.user_turn_ref, evidence)
                             and type(correlation.correlation_ref) is str and bool(correlation.correlation_ref),
                             "BRAIN_CORRELATION_MISMATCH")
                    handoff = correlation.handoff
                    _require(type(handoff) is ProductionHandoff
                             and type(handoff.completed) is ProductionStageCompleted
                             and type(handoff.receipt) is ProductionStageReceipt, "CONSUMER_RECEIPT_REQUIRED")
                    expected_kind = "SYNTHETIC_FIXTURE" if evidence == "SYNTHETIC_FIXTURE" else "HUMAN_SUPPLIED"
                    _require(handoff.receipt.provenance.evidence_type == expected_kind
                             and handoff.completed.spec.idempotency_key == key
                             and handoff.completed.spec.provenance.source_ref == "wakehub-event:" + key
                             and handoff.source_bytes == json.dumps(asdict(event), sort_keys=True).encode("utf-8"),
                             "HANDOFF_EVENT_MISMATCH")
                    self._move(txn, data, record, "BRAIN_CORRELATED", "BRAIN_RECEIPT_CORRELATED",
                               proof={"correlation_ref": correlation.correlation_ref,
                                      "consumer_id": handoff.receipt.consumer_id,
                                      "receipt_sha256": handoff.receipt.sha256,
                                      "completed_sha256": handoff.completed.sha256})
                    self.orchestrator.record_completed(
                        handoff.completed, source_bytes=handoff.source_bytes,
                        input_value=json.loads(handoff.input_json), output=json.loads(handoff.output_json))
                    dispatched = self.orchestrator.record_receipt(handoff.receipt)
                    _require(dispatched.status == "RECEIPT_RECORDED"
                             and dispatched.accepted_consumer == handoff.receipt.consumer_id
                             and dispatched.event is not None
                             and dispatched.event.receipt == handoff.receipt,
                             "CONSUMER_RECEIPT_NOT_RECORDED")
                    return self._finish(txn, data, record, "ACK", "CONSUMER_RECEIPT_RECORDED",
                                        proof={"consumer_receipt": handoff.receipt.to_dict(),
                                               "completed": handoff.completed.to_dict()})
                except (RuntimeHold, OrchestrationError) as exc:
                    return self._finish(txn, data, record, "UNKNOWN_EFFECT",
                                        _safe_code(exc, "ATTEMPT_UNPROVEN_NO_REPLAY"))
                except Exception:
                    return self._finish(txn, data, record, "UNKNOWN_EFFECT", "ATTEMPT_UNPROVEN_NO_REPLAY")
        except RuntimeHold as exc:
            return Result("HOLD", str(exc), evidence)
        except (ProvenanceError, TriggerBlocked, PrivateStateError, HandoffBlocked,
                BindingError, RegistrationBlocked, GitHubReadBlocked) as exc:
            return Result("HOLD", _safe_code(exc, "SOURCE_OR_JOURNAL_UNPROVEN"), evidence)
        except Exception:
            return Result("HOLD", "SOURCE_OR_JOURNAL_UNPROVEN", evidence)

    @staticmethod
    def _move(txn, data: dict, record: dict, status: Status, code: str,
              *, proof: dict | None = None) -> None:
        _require(status in _NEXT.get(record["status"], set()), "INVALID_TRANSITION")
        record.update(status=status, code=code, history=[*record["history"], status])
        if proof is not None:
            record["evidence"].update(proof)
        txn.write(data)

    def _finish(self, txn, data: dict, record: dict, status: Status, code: str,
                *, proof: dict | None = None) -> Result:
        self._move(txn, data, record, status, code, proof=proof)
        return Result(status, code, self.registration.evidence_type)


def synthetic_handoff(event: Event, registration: Registration) -> ProductionHandoff:
    """Actually call the local A03 consumer, label every resulting claim synthetic."""
    from ai_commerce.creative.briefs import intake_brief

    _require(event.evidence_type == registration.evidence_type == "SYNTHETIC_FIXTURE", "SYNTHETIC_ONLY")
    source = json.dumps(asdict(event), sort_keys=True).encode("utf-8")
    input_value = intake_brief("Synthetic WakeHub R1 diagnostic: a quiet cloud.")
    output = intake_brief(input_value)
    route = "a03-creative-brief"
    consumer, path = CONSUMER_ROUTES[route]
    key = event.key(registration)
    provenance = Provenance(actor_ref="fixture:wakehub-runtime", source_ref="wakehub-event:" + key,
                            source_sha256=bytes_digest(source), evidence_type="SYNTHETIC_FIXTURE")
    spec = StageSpec(stage_id="fixture:wakehub-runtime", route_id=route, consumer_id=consumer,
                     consumer_path=path, source_sha256=bytes_digest(source), input_sha256=digest(input_value),
                     output_sha256=digest(output), idempotency_key=key, provenance=provenance,
                     authorization=RouteAuthorization(route_id=route, consumer_id=consumer,
                         consumer_path=path, authorization_ref="fixture:wakehub-runtime"))
    completed = ProductionStageCompleted(spec=spec, completion_ref="fixture:completed:" + key,
        source_sha256=spec.source_sha256, input_sha256=spec.input_sha256, output_sha256=spec.output_sha256)
    receipt = ProductionStageReceipt(spec=spec, completed_sha256=completed.sha256,
        output_sha256=digest(output), consumer_id=consumer, receipt_ref="fixture:receipt:" + key,
        execution_ref="fixture:execution:" + key, provenance=replace(provenance, actor_ref=consumer),
        consumer_executed=True)
    return ProductionHandoff(completed, source, json.dumps(input_value), json.dumps(output), receipt)


class SyntheticSource:
    def __init__(self, event: Event):
        self.event = event

    def next_event(self) -> Event:
        return self.event  # Deliberately redeliver to exercise durable deduplication.


class SyntheticSender:
    """No browser access. Optional supplied acquisition snapshot exercises D5 evaluation."""
    evidence_type = "SYNTHETIC_FIXTURE"

    def __init__(self, environment=None, inventory=None):
        self.environment, self.inventory = environment, inventory
        self.calls = 0

    def preflight(self, registration: Registration) -> str | None:
        if self.environment is not None:
            _require(self.environment.port == 9444 and self.environment.endpoint ==
                     registration.binding.cdp_endpoint, "CDP_OWNER_MISMATCH")
            try:
                state, _ = chrome_acquisition.evaluate(
                    self.environment, self.inventory, parse=json.loads, session_id=7)
            except chrome_acquisition.AcquisitionBlocked as exc:
                return str(exc)
            if state != "OWNED":
                return "PROFILE_ABSENT"
        return None

    def send(self, event: Event, registration: Registration) -> Observation:
        self.calls += 1
        b = registration.binding
        return Observation(WakeReceipt(event_id=event.event_id, route_id=b.route_id,
            generation=b.generation, delivery_status="UNKNOWN", error_code="UI_USER_TURN_OBSERVED"),
            b.chat_url, registration.marker, "fixture:user-turn:" + event.key(registration))


class SyntheticBrain:
    def __init__(self, handoff: ProductionHandoff):
        self.handoff = handoff

    def correlate(self, event: Event, registration: Registration,
                  observation: Observation) -> BrainCorrelation:
        return BrainCorrelation(event.key(registration), registration.marker, observation.user_turn_ref,
                                "fixture:brain-correlation", "SYNTHETIC_FIXTURE", self.handoff)


def _read_owned_json(path: Path, repository_root: Path, *, limit: int = 131072) -> dict:
    """Read existing operator files without creating locks, state or credentials."""
    _require(path.exists(), "LOCAL_AUTHORITY_MISSING")
    with _private_access(path, repository_root) as (owner, files):
        fd = files.open(path.name, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(limit + 1)
        _require(len(raw) <= limit, "LOCAL_AUTHORITY_INVALID")
        value = _decode_unique(raw.decode("utf-8-sig"))
        _require(type(value) is dict and _private_identity(path) == owner,
                 "LOCAL_AUTHORITY_INVALID")
        return value


class _NoNativeBrain:
    def correlate(self, *args):
        raise RuntimeHold("NATIVE_BRAIN_UNAVAILABLE")


def compose_read_only(config_path: Path, repository_root: Path) -> Runner:
    """One real R1 ingress, using existing D5 registered source/config contracts.

    All paths/selectors come from an existing private local operator file. No
    Issue/event may provide credentials, URLs, profile selection or authority.
    A resolve-claim grant is required; it does not authorize Send. P0 has no real
    receipt pins in R1, so neither native Brain nor consumer success is possible.
    """
    config = _read_owned_json(config_path, repository_root)
    _require(set(config) == {"schema", "registration", "marker", "registered_source",
             "registry", "journal", "chrome_config", "grants", "caller_id"}
             and config["schema"] == "WakeHubRuntimeR1.v1", "LOCAL_CONFIG_INVALID")
    raw_registration = config["registration"]
    _require(type(raw_registration) is dict
             and set(raw_registration) == {"binding", "source_sha", "evidence_type"}
             and type(raw_registration["binding"]) is dict, "LOCAL_CONFIG_INVALID")
    registration = Registration(ResolvedBinding(**raw_registration["binding"]),
                                raw_registration["source_sha"], raw_registration["evidence_type"])
    _require(registration.evidence_type == "GITHUB_AUTHENTICATED"
             and config["marker"] == registration.marker, "REGISTRATION_MARKER_DRIFT")
    _require(type(config["caller_id"]) is str and _ROUTE.fullmatch(config["caller_id"]) is not None,
             "LOCAL_CONFIG_INVALID")
    paths = {}
    for name in ("registered_source", "registry", "journal", "chrome_config", "grants"):
        _require(type(config[name]) is str, "LOCAL_CONFIG_INVALID")
        paths[name] = Path(config[name])
        _private_path(paths[name], repository_root)
    _require(len(set(paths.values()) | {config_path}) == 6, "LOCAL_CONFIG_INVALID")
    registered = _read_owned_json(paths["registered_source"], repository_root, limit=8192)
    _require(type(registered.get("intent")) is dict, "REGISTERED_SOURCE_INVALID")
    intent = RegistrationIntent(**registered["intent"])
    _record_valid(registered, intent)
    target = intent.target(registered["run_id"])
    _require(target.source_sha == registration.source_sha, "SOURCE_TARGET_DRIFT")
    registry = _read_owned_json(paths["registry"], repository_root)
    grants = _read_owned_json(paths["grants"], repository_root)
    _require(set(grants) == {"version", "grants"} and type(grants["version"]) is int
             and grants["version"] == 1 and type(grants["grants"]) is list
             and 1 <= len(grants["grants"]) <= 128
             and all(type(g) is dict for g in grants["grants"]), "GRANTS_INVALID")
    chrome_config = _read_owned_json(paths["chrome_config"], repository_root, limit=8192)
    local = os.environ.get("LOCALAPPDATA")
    _require(bool(local), "PROFILE_AUTHORITY_MISSING")
    environment = chrome_acquisition.load_environment(paths["chrome_config"], repository_root,
                                                       local_app_data=Path(local))
    _require(environment.endpoint == registration.binding.cdp_endpoint and environment.port == 9444,
             "CDP_OWNER_MISMATCH")
    # Existing operator-only environment contracts; never read from Issue JSON.
    token = os.environ.get("AI_COMMERCE_GITHUB_READ_TOKEN", "")
    _require(bool(token) and len(token) <= 4096 and not any(c in token for c in "\r\n\0"),
             "GITHUB_CREDENTIAL_INVALID")
    key_hex = os.environ.get("AI_COMMERCE_WAKE_HMAC_KEY_HEX", "")
    _require(re.fullmatch(r"[0-9a-fA-F]{64,8192}", key_hex) is not None and len(key_hex) % 2 == 0,
             "WAKE_CREDENTIAL_INVALID")
    key = bytes.fromhex(key_hex)
    owner = _private_identity(config_path)
    origin = SignedOriginConsentAuthority(grants=grants["grants"], secret_provider=lambda: key,
                                          principal_provider=lambda: _private_identity(config_path))
    caller = LocalCallerAttestation(config["caller_id"], is_local=True, attestation_verified=True)
    budget = GitHubReadBudget(max_reads=16, max_seconds=60, max_attempts=1)
    reader = GitHubExactRunReader(token_provider=lambda: token, read_budget=budget)
    authority = GitHubRestArtifactAuthority(repository=intent.repository, workflow_id=target.workflow_id,
                                             token_provider=lambda: token, read_budget=budget)
    source = GitHubEventSource(reader, target, authority, expected_event_id=registered["event_id"])

    def verify_local_authority(event: Event, current: Registration) -> bool:
        full_event = source.verified_event
        _require(full_event is not None and digest(full_event) == event.payload_sha256,
                 "SOURCE_PROOF_MISSING")
        # Recheck the existing immutable local inputs at the admission boundary.
        for path, snapshot in ((config_path, config), (paths["registered_source"], registered),
                               (paths["registry"], registry), (paths["grants"], grants),
                               (paths["chrome_config"], chrome_config)):
            _require(_read_owned_json(path, repository_root) == snapshot, "LOCAL_AUTHORITY_DRIFT")
        _require(_private_identity(config_path) == owner, "JOURNAL_OWNER_DRIFT")
        binding = resolve_binding(full_event, route_id=current.binding.route_id,
                                  registry_path=paths["registry"], repository_root=repository_root)
        _require(binding == current.binding, "REGISTRATION_DRIFT")
        _require(_read_owned_json(paths["registry"], repository_root) == registry,
                 "LOCAL_AUTHORITY_DRIFT")
        context = {k: full_event[k] for k in ("repository", "batch_id", "source_sha", "issue_number")}
        return origin.authorize_origin(caller, operation="resolve_claim_route",
                                       context={**context, "registry": registry})

    return Runner(source=source, registration=registration,
                  journal=Journal(paths["journal"], repository_root),
                  sender=NativeChrome9444(repository_root), brain=_NoNativeBrain(),
                  orchestrator=ProductionPipelineOrchestrator(), grant_verifier=verify_local_authority,
                  read_only=True)


def run_read_only(config_path: Path | None, repository_root: Path, *, dry_run: bool = False) -> Result:
    if dry_run:
        return Result("HOLD", "DRY_RUN_NO_EFFECTS", "SOURCE_ONLY")
    if config_path is None:
        return Result("HOLD", "LOCAL_AUTHORITY_MISSING", "GITHUB_AUTHENTICATED")
    try:
        return compose_read_only(config_path, repository_root).run_once()
    except (RuntimeHold, BindingError, RegistrationBlocked, TriggerBlocked, GitHubReadBlocked,
            PrivateStateError, HandoffBlocked, chrome_acquisition.AcquisitionBlocked) as exc:
        return Result("HOLD", _safe_code(exc, "LOCAL_AUTHORITY_UNPROVEN"), "GITHUB_AUTHENTICATED")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return Result("HOLD", "LOCAL_AUTHORITY_INVALID", "GITHUB_AUTHENTICATED")


def diagnostic(path: Path, repository_root: Path) -> Result:
    """Offline composition only; no native readiness or real Brain success claim."""
    registration = Registration(ResolvedBinding("fixture-chat-a", 1,
        "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001", "http://127.0.0.1:9444"),
        "a" * 40, "SYNTHETIC_FIXTURE")
    event = Event("ef02-completion-v1:" + "b" * 64, registration.source_sha, 1,
                  "c" * 64, "SYNTHETIC_FIXTURE")
    handoff = synthetic_handoff(event, registration)
    spec, receipt = handoff.completed.spec, handoff.receipt
    # Pins are fixture-only, never promoted to real authenticated authority.
    orchestrator = ProductionPipelineOrchestrator(
        authorized_specs=((spec.authorization.authorization_ref, spec.sha256),),
        verified_receipts=((receipt.consumer_id, receipt.sha256),))
    return Runner(source=SyntheticSource(event), registration=registration,
                  journal=Journal(path, repository_root), sender=SyntheticSender(),
                  brain=SyntheticBrain(handoff), orchestrator=orchestrator,
                  grant_verifier=lambda e, r: e.evidence_type == r.evidence_type == "SYNTHETIC_FIXTURE"
                  ).run_once()
