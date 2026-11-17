"""Bounded R0 composition with a single private durable event/attempt journal.

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
from typing import Callable, Literal, Protocol

from ai_commerce.production_orchestration.contracts import (
    CONSUMER_ROUTES, OrchestrationError, ProductionStageCompleted, ProductionStageReceipt,
    Provenance, RouteAuthorization, StageSpec, bytes_digest, digest,
)
from ai_commerce.production_orchestration.orchestrator import ProductionPipelineOrchestrator
from ai_commerce.remote import chrome_acquisition
from ai_commerce.remote._private_state import PrivateJsonState, PrivateStateError
from ai_commerce.remote.cdp_doorbell import LocalChromeDoorbellAdapter
from ai_commerce.remote.conversation_binding import ResolvedBinding, _CHAT, _ROUTE
from ai_commerce.remote.local_completion_handoff import HandoffBlocked, _private_identity
from ai_commerce.remote.local_wake_trigger import GitHubExactRunReader, PinnedRun, TriggerBlocked
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
        return "wakehub-r0:" + digest(asdict(self))


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
                 authority: ArtifactAuthority):
        self.reader, self.target, self.authority = reader, target, authority

    def next_event(self) -> Event | None:
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
                 and event["batch_id"] == self.target.batch_id, "SOURCE_TARGET_DRIFT")
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
    """One owner/adapter; deliberately incapable of launching or sending in R0."""
    evidence_type = "GITHUB_AUTHENTICATED"

    def __init__(self, repository_root: Path):
        self.repository_root = repository_root
        self.adapter = LocalChromeDoorbellAdapter(allow_submit=False)

    def preflight(self, registration: Registration) -> str:
        # Existing private N3-A boundary returns BLOCKED_NOT_DEPLOYED without
        # subprocesses, browser launch or accepting caller-supplied authority.
        chrome_acquisition._start_or_reuse_9444(
            self.repository_root, request_id=registration.marker)
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


class Journal:
    """One bounded D5 private state file, OS lock and atomic fsync/replace.

    Directory custody is provisioned outside this runner. Neither missing nor
    corrupt state is silently repaired. Holding this lock across one iteration
    serializes competing callers and protects reservation through receipt.
    """
    def __init__(self, path: Path, repository_root: Path):
        PrivateJsonState(path, _CODE_ROOT, label="WAKEHUB_R0")
        self.state = PrivateJsonState(path, repository_root, label="WAKEHUB_R0")
        _require(not self.state.path.name.endswith((".lock", ".part")), "JOURNAL_LOCATION_INVALID")

    def _owner(self) -> str:
        owner = _private_identity(self.state.path.parent)
        for path in (self.state.path, self.state.path.with_name(self.state.path.name + ".lock")):
            if path.exists() or path.is_symlink():
                _require(_private_identity(path) == owner, "JOURNAL_OWNER_DRIFT")
        return owner

    @contextmanager
    def locked(self, registration: Registration):
        owner = self._owner()
        with self.state.locked(wait_seconds=0, new_file_owner_sid=owner if os.name == "nt" else None) as txn:
            _require(self._owner() == owner, "JOURNAL_OWNER_DRIFT")
            data = txn.read({})
            if data == {} and not self.state.path.exists():
                data = {"schema": "WakeHubRuntimeJournal.v1", "registration": asdict(registration),
                        "owner": owner, "events": {}}
                txn.write(data)
            _require(type(data) is dict and set(data) == {"schema", "registration", "owner", "events"}
                     and data["schema"] == "WakeHubRuntimeJournal.v1" and data["owner"] == owner
                     and type(data["events"]) is dict and len(data["events"]) <= 128, "JOURNAL_INVALID")
            _require(data["registration"] == asdict(registration), "REGISTRATION_DRIFT")
            for key, record in data["events"].items():
                _require(type(record) is dict and set(record) == {"event", "status", "code", "history", "evidence"}
                         and type(record["event"]) is dict, "JOURNAL_INVALID")
                saved = Event(**record["event"])
                _require(saved.key(registration) == key and type(record["history"]) is list
                         and 1 <= len(record["history"]) <= 6
                         and record["history"][0] == "PENDING" and record["status"] == record["history"][-1]
                         and type(record["code"]) is str and type(record["evidence"]) is dict,
                         "JOURNAL_INVALID")
                _require(all(b in _NEXT.get(a, set()) for a, b in
                             zip(record["history"], record["history"][1:])), "JOURNAL_INVALID")
            yield txn, data
            _require(self._owner() == owner, "JOURNAL_OWNER_DRIFT")


class Runner:
    """Dependency-injected single-process runner; run_once consumes at most one event."""
    def __init__(self, *, source: EventSource, registration: Registration, journal: Journal,
                 sender: Sender, brain: BrainAdapter, orchestrator: ProductionPipelineOrchestrator,
                 grant_verifier: Callable[[Event, Registration], bool]):
        _require(type(orchestrator) is ProductionPipelineOrchestrator, "PRODUCTION_ORCHESTRATOR_REQUIRED")
        self.source, self.registration, self.journal = source, registration, journal
        self.sender, self.brain, self.orchestrator = sender, brain, orchestrator
        self.grant_verifier = grant_verifier

    def run_once(self) -> Result:
        evidence = self.registration.evidence_type
        try:
            event = self.source.next_event()
            if event is None:
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
                # R0 has exactly one native owner and it cannot send. A generic
                # injected sender or a readiness boolean cannot bypass that seam.
                if evidence == "GITHUB_AUTHENTICATED" and type(self.sender) is not NativeChrome9444:
                    return self._finish(txn, data, record, "BLOCKED_EXTERNAL", "UNSUPPORTED_NATIVE_OWNER")
                try:
                    granted = self.grant_verifier(event, self.registration) is True
                    code = self.sender.preflight(self.registration) if granted else "MISSING_GRANT"
                    _require(code is None or (type(code) is str and
                             re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) is not None),
                             "PREFLIGHT_RESULT_INVALID")
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
                    return self._finish(txn, data, record, "ACK", "CONSUMER_RECEIPT_RECORDED")
                except (RuntimeHold, OrchestrationError) as exc:
                    return self._finish(txn, data, record, "UNKNOWN_EFFECT",
                                        _safe_code(exc, "ATTEMPT_UNPROVEN_NO_REPLAY"))
                except Exception:
                    return self._finish(txn, data, record, "UNKNOWN_EFFECT", "ATTEMPT_UNPROVEN_NO_REPLAY")
        except RuntimeHold as exc:
            return Result("HOLD", str(exc), evidence)
        except (ProvenanceError, TriggerBlocked, PrivateStateError, HandoffBlocked) as exc:
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

    def _finish(self, txn, data: dict, record: dict, status: Status, code: str) -> Result:
        self._move(txn, data, record, status, code)
        return Result(status, code, self.registration.evidence_type)


def synthetic_handoff(event: Event, registration: Registration) -> ProductionHandoff:
    """Actually call the local A03 consumer, label every resulting claim synthetic."""
    from ai_commerce.creative.briefs import intake_brief

    _require(event.evidence_type == registration.evidence_type == "SYNTHETIC_FIXTURE", "SYNTHETIC_ONLY")
    source = json.dumps(asdict(event), sort_keys=True).encode("utf-8")
    input_value = intake_brief("Synthetic WakeHub R0 diagnostic: a quiet cloud.")
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
