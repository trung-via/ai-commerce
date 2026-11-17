"""P0 detached validation and bounded, durable SOURCE_ONLY A10 -> A03 dispatch.

P1 uses a caller-owned SQLite outbox, not a scheduler or provider. An invocation
reservation without a durable receiver witness is never replayed, even after
lease expiry. P0 wire receipts still require independent trusted receipt pins.
"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
from pathlib import Path
import sqlite3
import time
from typing import Literal

from ai_commerce.creative.briefs import brief_digest, intake_brief
from ai_commerce.creative import briefs as creative_briefs
from ai_commerce.creative.store import CreativeStore
from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import canonical_bytes as artifact_bytes
from ai_commerce.opportunity.bridge.briefs import validate_bridge
from ai_commerce.opportunity.core.models import validate_opportunity_decision
from ai_commerce.opportunity.signals.files import local_path, read_local
from ai_commerce.opportunity.signals.files import ledger_path

from .contracts import (
    CONSUMER_ROUTES, LocalAuthorization, LocalHandoff, OrchestrationError,
    ProductionStageCompleted, ProductionStageDispatched, ProductionStageReceipt,
    Provenance, RouteAuthorization, StageSpec, bytes_digest, canonical_bytes, digest, require,
)


@dataclass(frozen=True, kw_only=True)
class DispatchResult:
    status: Literal["NOT_DISPATCHED", "RECEIPT_RECORDED", "PENDING", "RUNNING",
                    "SUCCESS", "FAILED", "UNKNOWN", "RETRY_SAFE"]
    accepted_consumer: str | None = None
    event: ProductionStageDispatched | None = None
    completed: ProductionStageCompleted | None = None
    reason: str | None = None


class ProductionPipelineOrchestrator:
    """P0 bookkeeping plus an explicitly granted P1 local function adapter.

    ``authorized_specs`` is an independent, trusted caller input containing
    (authorization_ref, exact StageSpec SHA-256) pairs. A wire authorization
    label alone grants nothing, including permission to execute a consumer.
    ``verified_receipts`` contains (consumer_id, receipt SHA-256) pairs from an
    independently authenticated receipt source. P0 supplies no such verifier;
    an untrusted wire receipt can never grant its own acknowledgement.

    P1: supply ``state_dir``, the caller's checkout ``source_sha`` and a scoped
    ``LocalAuthorization``; pass this owner as ``production=`` to A10's existing
    ``run_automation`` callable. Read ``last_result`` for downstream outcome, or
    recreate the owner with the current grant and call ``resume_local``. Producer
    state and this journal must be trusted local directories; hashes alone do
    not authenticate an attacker-writable filesystem. No grants are recovered
    from disk: every delivery/reconciliation requires current caller authority.
    """

    def __init__(self, *, authorized_specs: Iterable[tuple[str, str]] = (),
                 verified_receipts: Iterable[tuple[str, str]] = (),
                 state_dir: str | Path | None = None, source_sha: str | None = None,
                 local_authorization: LocalAuthorization | None = None,
                 max_attempts: int = 2, lease_seconds: int = 60) -> None:
        pins = tuple(authorized_specs)
        require(all(type(pin) is tuple and len(pin) == 2
                    and all(type(part) is str for part in pin) for pin in pins), "AUTHORIZATION_PINS")
        self._authorized_specs = frozenset(pins)
        receipts = tuple(verified_receipts)
        require(all(type(pin) is tuple and len(pin) == 2
                    and all(type(part) is str for part in pin) for pin in receipts), "RECEIPT_PINS")
        self._verified_receipts = frozenset(receipts)
        self._completed: dict[str, ProductionStageCompleted] = {}
        self._receipts: dict[str, ProductionStageDispatched] = {}
        self._receipt_refs: dict[str, str] = {}
        self._execution_refs: dict[str, str] = {}
        require(local_authorization is None or type(local_authorization) is LocalAuthorization,
                "LOCAL_AUTHORIZATION_REQUIRED")
        require(type(max_attempts) is int and 1 <= max_attempts <= 3, "RETRY_BOUND")
        require(type(lease_seconds) is int and 1 <= lease_seconds <= 300, "LEASE_BOUND")
        self._local_authorization = local_authorization
        self._source_sha = source_sha
        self._state_dir = local_path(state_dir) if state_dir is not None else None
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds
        self.last_result: DispatchResult | None = None

    def record_completed(self, completed: ProductionStageCompleted, *, source_bytes: bytes,
                         input_value: dict, output: dict) -> DispatchResult:
        """Check bytes and real consumer contract compatibility; never dispatch."""
        require(type(completed) is ProductionStageCompleted, "COMPLETED_REQUIRED")
        # Revalidate detached wire data at the public boundary.
        completed = ProductionStageCompleted.from_dict(completed.to_dict())
        spec = completed.spec
        require((spec.authorization.authorization_ref, spec.sha256) in self._authorized_specs,
                "ROUTE_NOT_AUTHORIZED")
        require(bytes_digest(source_bytes) == spec.source_sha256, "SOURCE_HASH_MISMATCH")
        require(type(input_value) is dict and digest(input_value) == spec.input_sha256, "INPUT_HASH_MISMATCH")
        require(type(output) is dict and digest(output) == spec.output_sha256, "OUTPUT_HASH_MISMATCH")
        # A03 validation returns a detached ContentBrief. This compatibility
        # check is not an execution receipt for either registered consumer.
        try:
            brief = intake_brief(output)
            require(brief_digest(brief) == spec.output_sha256, "OUTPUT_HASH_MISMATCH")
            if spec.route_id == "a10-m05-content-brief":
                bridge = validate_bridge(brief.get("opportunity_bridge"))
                require(bridge["mode"] == "OPPORTUNITY" and bridge["proposal"] == input_value,
                        "M05_INPUT_BINDING")
            else:
                require(intake_brief(input_value) == brief, "A03_INPUT_BINDING")
        except OrchestrationError:
            raise
        except (ValueError, TypeError, KeyError):
            raise OrchestrationError("CONSUMER_CONTRACT_INVALID") from None
        key = spec.idempotency_key
        previous = self._completed.get(key)
        require(previous is None or previous == completed, "IDEMPOTENCY_CONFLICT")
        self._completed[key] = completed
        return self.dispatch(key)

    def dispatch(self, idempotency_key: str) -> DispatchResult:
        """Report receipt state. This method has no execution capability."""
        require(type(idempotency_key) is str and idempotency_key in self._completed, "COMPLETED_NOT_FOUND")
        event = self._receipts.get(idempotency_key)
        if event is None:
            return DispatchResult(status="NOT_DISPATCHED")
        return DispatchResult(status="RECEIPT_RECORDED", accepted_consumer=event.spec.consumer_id, event=event)

    def record_receipt(self, receipt: ProductionStageReceipt) -> DispatchResult:
        """Observe an actual caller-supplied receipt; never mint one from output."""
        require(type(receipt) is ProductionStageReceipt, "RECEIPT_REQUIRED")
        receipt = ProductionStageReceipt.from_dict(receipt.to_dict())
        spec = receipt.spec
        key = spec.idempotency_key
        completed = self._completed.get(key)
        require(completed is not None, "COMPLETED_NOT_FOUND")
        require(completed.spec == spec and receipt.completed_sha256 == completed.sha256,
                "RECEIPT_COMPLETION_MISMATCH")
        previous = self._receipts.get(key)
        require(previous is None or previous.receipt == receipt, "IDEMPOTENCY_CONFLICT")
        for refs, ref in ((self._receipt_refs, receipt.receipt_ref),
                          (self._execution_refs, receipt.execution_ref)):
            require(ref not in refs or refs[ref] == key, "RECEIPT_REPLAY")
        require((receipt.consumer_id, receipt.sha256) in self._verified_receipts, "RECEIPT_NOT_VERIFIED")
        event = ProductionStageDispatched(spec=spec, completed_sha256=completed.sha256,
                                          receipt=receipt, receipt_sha256=receipt.sha256)
        self._receipts[key] = event
        self._receipt_refs[receipt.receipt_ref] = key
        self._execution_refs[receipt.execution_ref] = key
        return self.dispatch(key)

    def revoke_local_authorization(self) -> None:
        """Revocation also prevents receipt reconciliation and duplicate delivery."""
        self._local_authorization = None

    def _grant(self) -> LocalAuthorization:
        grant = self._local_authorization
        require(type(grant) is LocalAuthorization, "LOCAL_AUTHORIZATION_REQUIRED")
        require(self._source_sha == grant.source_sha, "SOURCE_SHA_MISMATCH")
        require(self._state_dir is not None, "STATE_DIR_REQUIRED")
        return grant

    @contextmanager
    def _database(self):
        require(self._state_dir is not None, "STATE_DIR_REQUIRED")
        path = ledger_path(self._state_dir / "production.sqlite", create=True)
        try:
            db = sqlite3.connect(path, timeout=5)
        except sqlite3.Error:
            raise OrchestrationError("JOURNAL_UNAVAILABLE") from None
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            empty = not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
            require(version == 1 or version == 0 and empty, "JOURNAL_SCHEMA_VERSION")
            db.execute("PRAGMA user_version=1")
            db.execute("""CREATE TABLE IF NOT EXISTS outbox (
                key TEXT PRIMARY KEY, slot TEXT UNIQUE NOT NULL, completed TEXT NOT NULL,
                state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                lease_until REAL, witness TEXT, dispatched TEXT, reason TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS journal (
                seq INTEGER PRIMARY KEY, key TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL)""")
            yield db
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise OrchestrationError("JOURNAL_INVALID") from None
        except OrchestrationError:
            db.rollback()
            raise
        except (ValueError, TypeError, KeyError, RecursionError):
            db.rollback()
            raise OrchestrationError("JOURNAL_RECORD_INVALID") from None
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _journal(db, key: str, kind: str, body: dict) -> None:
        db.execute("INSERT INTO journal(key,kind,body) VALUES (?,?,?)",
                   (key, kind, canonical_bytes(body).decode("utf-8")))

    @staticmethod
    def _read_json(path: Path, limit: int = 4 * 1024 * 1024) -> tuple[dict, bytes]:
        raw = read_local(path, limit=limit)
        value = json.loads(raw)
        require(type(value) is dict and raw == artifact_bytes(value) + b"\n", "ARTIFACT_BYTES_INVALID")
        return value, raw

    def _producer(self, result: dict, result_ref: Path, brief_ref: Path) -> tuple[dict, bytes, bytes]:
        """Resolve genuine A10 journal refs, not a detached completion assertion."""
        grant = self._grant()
        require(type(result) is dict and result.get("schema_version") == "A10AutomationResult.v1",
                "PRODUCER_SCHEMA")
        require((result.get("status"), result.get("reason"), result.get("next_step"), result.get("next_state")) ==
                ("COMPLETE", "A03_BRIEF_CREATED", "READY_FOR_A03_DISPATCH", "READY_FOR_A03_DISPATCH"),
                "PRODUCER_NOT_COMPLETE")
        require(result.get("source_sha") == grant.source_sha and result.get("job_id") == grant.job_id,
                "PRODUCER_SOURCE_MISMATCH")
        require(result.get("event_id") == "a10event-" + bytes_digest(artifact_bytes(
            {k: v for k, v in result.items() if k != "event_id"})), "PRODUCER_EVENT_HASH")
        result_ref, brief_ref = local_path(result_ref), local_path(brief_ref)
        require(result_ref.name == result["event_id"] + ".json"
                and result_ref.parent == brief_ref.parent and brief_ref.name == "brief.json"
                and result_ref.parent.name == grant.job_id, "PRODUCER_REFERENCE_MISMATCH")
        saved, source = self._read_json(result_ref)
        require(saved == result, "PRODUCER_RESULT_MISMATCH")
        brief, raw = self._read_json(brief_ref, limit=131072)
        require(result.get("brief_ref") == str(brief_ref)
                and result.get("brief_bytes_sha256") == bytes_digest(raw), "OUTPUT_BYTES_MISMATCH")
        require(result.get("brief") == brief and result.get("brief_sha256") == digest(brief),
                "OUTPUT_HASH_MISMATCH")
        validate_record("ContentBrief", brief)
        binding, _ = self._read_json(result_ref.parent / "job.json")
        require(binding.get("schema_version") == "A10AutomationBinding.v1"
                and binding.get("job_id") == grant.job_id
                and binding.get("manifest_sha256") == result.get("manifest_sha256")
                and binding["manifest"]["source_sha"] == grant.source_sha
                and bytes_digest(artifact_bytes(binding["manifest"])) == result["manifest_sha256"]
                and binding["manifest"].get("decision_mode", "autonomous") == "autonomous"
                and binding.get("mode") in ("offline", "execute"), "PRODUCER_BINDING_MISMATCH")
        decision, _ = self._read_json(result_ref.parent / "decision.json")
        require(decision == result.get("decision") and decision.get("outcome") == "SELECT"
                and result.get("decision_sha256") == bytes_digest(artifact_bytes(decision)), "PRODUCER_DECISION_MISMATCH")
        validate_opportunity_decision(decision, proposals=result["proposals"], job_id=grant.job_id,
            manifest_sha256=result["manifest_sha256"], source_sha=grant.source_sha,
            assessment=result.get("assessment"), assessment_task=result.get("ai_task"))
        bridge = validate_bridge(brief.get("opportunity_bridge"))
        automation = brief.get("opportunity_automation", {})
        require(type(automation) is dict, "M05_INPUT_BINDING")
        expected_automation = {"schema_version": "A10AutomationDecisionReceipt.v1", "job_id": grant.job_id,
            "manifest_sha256": result["manifest_sha256"], "source_sha": grant.source_sha, "decision": decision,
            "decision_sha256": result["decision_sha256"], "assessment_receipt_id": decision["assessment_receipt_id"],
            "next_state": "READY_FOR_A03_DISPATCH", "dispatch_status": "NOT_ATTEMPTED"}
        expected_automation = {"receipt_id": "a10briefreceipt-" + bytes_digest(artifact_bytes(expected_automation)),
                               **expected_automation}
        require(bridge["mode"] == "OPPORTUNITY"
                and bridge["proposal_sha256"] == decision["selected_proposal_sha256"]
                and automation.get("decision") == decision
                and automation.get("source_sha") == grant.source_sha
                and automation.get("job_id") == grant.job_id
                and automation == expected_automation, "M05_INPUT_BINDING")
        assessment = result.get("assessment")
        require(assessment is None or type(assessment) is dict and assessment.get("status") == "OK",
                "PROVIDER_NOT_COMPLETE")
        return brief, raw, source

    def complete_a10(self, result: dict, *, result_ref: str | Path, brief_ref: str | Path,
                     dispatch: bool = True) -> DispatchResult:
        """Called by the real producer after its immutable success is committed.

        Event transport cannot insert executable records through record_completed
        or record_receipt. Only this validated producer seam creates a P1 outbox.
        ``dispatch=False`` supports persist-before-delivery transport/recovery.
        """
        grant = self._grant()
        require(type(dispatch) is bool, "DISPATCH_FLAG")
        try:
            brief, raw, source = self._producer(result, Path(result_ref), Path(brief_ref))
        except OrchestrationError:
            raise
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            raise OrchestrationError("PRODUCER_CONTRACT_INVALID") from None
        handoff = LocalHandoff(journey_id=grant.journey_id, cycle_id=grant.cycle_id, job_id=grant.job_id,
            source_sha=grant.source_sha, producer_event_id=result["event_id"],
            producer_result_ref=str(local_path(result_ref)), producer_result_sha256=bytes_digest(source),
            brief_ref=str(local_path(brief_ref)), brief_bytes_sha256=bytes_digest(raw))
        consumer, path = CONSUMER_ROUTES["a03-creative-brief"]
        key = digest({"handoff": handoff.to_dict(), "consumer": consumer, "output": digest(brief)})
        spec = StageSpec(stage_id="A10.M05", route_id="a03-creative-brief", consumer_id=consumer,
            consumer_path=path, source_sha256=bytes_digest(source),
            input_sha256=digest({"producer_event_id": result["event_id"], "result_sha256": bytes_digest(source)}),
            output_sha256=digest(brief), idempotency_key=key, local_handoff=handoff,
            provenance=Provenance(actor_ref="ai_commerce.opportunity.bridge.cli:run_automation",
                source_ref=handoff.producer_result_ref, source_sha256=bytes_digest(source), evidence_type="UNKNOWN"),
            authorization=RouteAuthorization(route_id="a03-creative-brief", consumer_id=consumer, consumer_path=path,
                authorization_ref=grant.authorization_ref, scope="SOURCE_ONLY_LOCAL"))
        completed = ProductionStageCompleted(spec=spec, completion_ref=handoff.producer_result_ref,
            source_sha256=spec.source_sha256, input_sha256=spec.input_sha256, output_sha256=spec.output_sha256)
        slot = digest({k: v for k, v in grant.to_dict().items() if k != "authorization_ref"})
        with self._database() as db:
            previous = db.execute("SELECT * FROM outbox WHERE slot=? OR key=?", (slot, key)).fetchone()
            if previous:
                require(previous["key"] == key and json.loads(previous["completed"]) == completed.to_dict(),
                        "IDEMPOTENCY_CONFLICT")
            else:
                db.execute("INSERT INTO outbox(key,slot,completed,state) VALUES (?,?,?,'PENDING')",
                           (key, slot, canonical_bytes(completed.to_dict()).decode("utf-8")))
                self._journal(db, key, completed.schema_version, completed.to_dict())
        self.last_result = self.dispatch_local(key) if dispatch else DispatchResult(status="PENDING", completed=completed)
        return self.last_result

    def _authorize_completed(self, completed: ProductionStageCompleted) -> None:
        grant = self._grant()
        spec, handoff = completed.spec, completed.spec.local_handoff
        require(handoff is not None and (handoff.journey_id, handoff.cycle_id, handoff.job_id, handoff.source_sha,
                handoff.route_id) == (grant.journey_id, grant.cycle_id, grant.job_id, grant.source_sha, grant.route_id)
                and spec.authorization.authorization_ref == grant.authorization_ref
                and spec.authorization.scope == "SOURCE_ONLY_LOCAL", "ROUTE_NOT_AUTHORIZED")
        require(spec.stage_id == "A10.M05" and spec.route_id == "a03-creative-brief"
                and (spec.consumer_id, spec.consumer_path) == CONSUMER_ROUTES["a03-creative-brief"], "LOCAL_ROUTE_MISMATCH")
        require(spec.source_sha256 == handoff.producer_result_sha256
                and completed.completion_ref == handoff.producer_result_ref
                and spec.provenance.source_ref == handoff.producer_result_ref
                and spec.provenance.actor_ref == "ai_commerce.opportunity.bridge.cli:run_automation"
                and spec.input_sha256 == digest({"producer_event_id": handoff.producer_event_id,
                                                "result_sha256": handoff.producer_result_sha256})
                and spec.idempotency_key == digest({"handoff": handoff.to_dict(), "consumer": spec.consumer_id,
                                                    "output": spec.output_sha256}), "LOCAL_COMPLETION_BINDING")

    def _verified_input(self, completed: ProductionStageCompleted) -> bytes:
        self._authorize_completed(completed)
        spec, handoff = completed.spec, completed.spec.local_handoff
        result, source = self._read_json(Path(handoff.producer_result_ref))
        require(bytes_digest(source) == handoff.producer_result_sha256 == spec.source_sha256,
                "SOURCE_HASH_MISMATCH")
        brief, raw, _ = self._producer(result, Path(handoff.producer_result_ref), Path(handoff.brief_ref))
        require(bytes_digest(raw) == handoff.brief_bytes_sha256 and digest(brief) == spec.output_sha256,
                "OUTPUT_HASH_MISMATCH")
        require(result["event_id"] == handoff.producer_event_id, "PRODUCER_EVENT_HASH")
        return raw

    def _invoke_a03(self, completed: ProductionStageCompleted, raw: bytes) -> dict:
        """The only executable adapter: exact bytes -> existing intake -> store."""
        spec = completed.spec
        require(type(raw) is bytes and bytes_digest(raw) == spec.local_handoff.brief_bytes_sha256,
                "RECEIVER_INPUT_MISMATCH")
        actual = creative_briefs.intake_brief(raw.decode("utf-8"))
        require(digest(actual) == spec.output_sha256, "RECEIVER_OUTPUT_MISMATCH")
        store = CreativeStore(local_path(self._state_dir / "creative"))
        local_path(store.root / "briefs" / (spec.output_sha256 + ".json"))
        stored = store.save_brief(actual)
        require(stored == spec.output_sha256 and digest(store.load_brief(stored)) == stored,
                "RECEIVER_OUTPUT_MISMATCH")
        artifact = local_path(store.root / "briefs" / (stored + ".json"))
        require(bytes_digest(read_local(artifact)) == stored, "RECEIVER_OUTPUT_MISMATCH")
        return {"schema_version": "A03LocalInvocation.v1", "completed_sha256": completed.sha256,
                "consumer_id": spec.consumer_id, "execution_ref": "local:a03:" + spec.idempotency_key,
                "input_ref": spec.local_handoff.brief_ref, "input_bytes_sha256": bytes_digest(raw),
                "output_sha256": stored, "artifact_ref": str(artifact),
                "source_sha": self._source_sha, "outcome": "SUCCESS"}

    def _finish_local(self, db, row, completed: ProductionStageCompleted) -> DispatchResult:
        """Reconcile only a committed native witness, never a supplied receipt."""
        self._authorize_completed(completed)
        self._verified_input(completed)
        witness = json.loads(row["witness"])
        spec = completed.spec
        artifact = str(local_path(self._state_dir / "creative" / "briefs" / (spec.output_sha256 + ".json")))
        expected = {"schema_version": "A03LocalInvocation.v1", "completed_sha256": completed.sha256,
            "consumer_id": spec.consumer_id, "execution_ref": "local:a03:" + spec.idempotency_key,
            "input_ref": spec.local_handoff.brief_ref, "input_bytes_sha256": spec.local_handoff.brief_bytes_sha256,
            "output_sha256": spec.output_sha256, "artifact_ref": artifact,
            "source_sha": self._source_sha, "outcome": "SUCCESS"}
        require(witness == expected and bytes_digest(read_local(artifact)) == spec.output_sha256,
                "INVOCATION_WITNESS_MISMATCH")
        receipt = ProductionStageReceipt(spec=spec, completed_sha256=completed.sha256,
            output_sha256=spec.output_sha256, consumer_id=spec.consumer_id,
            receipt_ref="local:receipt:" + spec.idempotency_key, execution_ref=witness["execution_ref"],
            provenance=replace(spec.provenance, actor_ref=spec.consumer_id), consumer_executed=True,
            outcome="SUCCESS", artifact_ref=artifact)
        event = ProductionStageDispatched(spec=spec, completed_sha256=completed.sha256,
            receipt=receipt, receipt_sha256=receipt.sha256, dispatch_ref=witness["execution_ref"])
        if row["dispatched"] is None:
            db.execute("UPDATE outbox SET state='SUCCESS',dispatched=?,reason=NULL WHERE key=?",
                       (canonical_bytes(event.to_dict()).decode("utf-8"), spec.idempotency_key))
            self._journal(db, spec.idempotency_key, receipt.schema_version, receipt.to_dict())
        else:
            require(ProductionStageDispatched.from_dict(json.loads(row["dispatched"])) == event,
                    "DISPATCH_RECEIPT_BINDING")
        return DispatchResult(status="SUCCESS", accepted_consumer=spec.consumer_id, event=event, completed=completed)

    def dispatch_local(self, key: str) -> DispatchResult:
        """At-least-once delivery; a stale uncertain lease is never reclaimed."""
        self._grant()
        with self._database() as db:
            row = db.execute("SELECT * FROM outbox WHERE key=?", (key,)).fetchone()
            require(row is not None, "COMPLETED_NOT_FOUND")
            completed = ProductionStageCompleted.from_dict(json.loads(row["completed"]))
            require(row["key"] == completed.spec.idempotency_key, "LOCAL_COMPLETION_BINDING")
            require(type(row["attempts"]) is int and 0 <= row["attempts"] <= 3, "RETRY_BOUND")
            self._authorize_completed(completed)
            if row["witness"] is not None:
                return self._finish_local(db, row, completed)
            if row["state"] == "RUNNING":
                state = "UNKNOWN" if row["lease_until"] <= time.time() else "RUNNING"
                if state == "UNKNOWN":
                    db.execute("UPDATE outbox SET state='UNKNOWN',reason='UNRESOLVED_INVOCATION' WHERE key=?", (key,))
                    self._journal(db, key, "UNKNOWN", {"reason": "UNRESOLVED_INVOCATION"})
                return DispatchResult(status=state, completed=completed, reason="UNRESOLVED_INVOCATION")
            if row["state"] in ("UNKNOWN", "FAILED"):
                return DispatchResult(status=row["state"], completed=completed, reason=row["reason"])
            require(row["state"] in ("PENDING", "RETRY_SAFE"), "JOURNAL_STATE_INVALID")
            attempts = row["attempts"] + 1
            try:
                raw = self._verified_input(completed)
            except (ValueError, TypeError, KeyError, OSError, RecursionError) as exc:
                # Missing local files may be restored safely: no invocation has
                # been reserved. Integrity/contract failures are terminal.
                safe = isinstance(exc, OSError) or getattr(exc, "code", None) == "FILE_IO_ERROR"
                state = "RETRY_SAFE" if safe and attempts < self._max_attempts else "FAILED"
                reason = "INPUT_UNAVAILABLE" if safe else "INPUT_REJECTED"
                db.execute("UPDATE outbox SET state=?,attempts=?,reason=? WHERE key=?", (state, attempts, reason, key))
                self._journal(db, key, state, {"reason": reason, "attempt": attempts})
                return DispatchResult(status=state, completed=completed, reason=reason)
            db.execute("UPDATE outbox SET state='RUNNING',attempts=?,lease_until=? WHERE key=?",
                       (attempts, time.time() + self._lease_seconds, key))
            dispatched = ProductionStageDispatched(spec=completed.spec, completed_sha256=completed.sha256,
                receipt=None, receipt_sha256=None, phase="DISPATCHED", dispatch_ref="local:a03:" + key)
            self._journal(db, key, dispatched.schema_version, dispatched.to_dict())
            self._journal(db, key, "ProductionDispatchAttempt.v1", {
                "attempt": attempts, "execution_ref": "local:a03:" + key,
                "completed_sha256": completed.sha256, "status": "RUNNING"})
        # Reservation is committed before entering the actual local function.
        try:
            self._authorize_completed(completed)
            witness = self._invoke_a03(completed, raw)
        except (ValueError, TypeError, KeyError):
            with self._database() as db:
                db.execute("UPDATE outbox SET state='FAILED',reason='RECEIVER_REJECTED' WHERE key=?", (key,))
                self._journal(db, key, "FAILED", {"reason": "RECEIVER_REJECTED"})
            return DispatchResult(status="FAILED", completed=completed, reason="RECEIVER_REJECTED")
        except Exception:
            # An I/O/unexpected failure after reservation may have happened
            # after intake/store. Do not infer failure-before-effect or retry.
            with self._database() as db:
                db.execute("UPDATE outbox SET state='UNKNOWN',reason='RECEIVER_UNRESOLVED' WHERE key=?", (key,))
                self._journal(db, key, "UNKNOWN", {"reason": "RECEIVER_UNRESOLVED"})
            return DispatchResult(status="UNKNOWN", completed=completed, reason="RECEIVER_UNRESOLVED")
        with self._database() as db:
            db.execute("UPDATE outbox SET witness=? WHERE key=?",
                       (canonical_bytes(witness).decode("utf-8"), key))
            self._journal(db, key, "A03LocalInvocation.v1", witness)
        # Separate commit makes crash-after-invocation / before-receipt resumable.
        with self._database() as db:
            row = db.execute("SELECT * FROM outbox WHERE key=?", (key,)).fetchone()
            return self._finish_local(db, row, completed)

    def resume_local(self) -> tuple[DispatchResult, ...]:
        """Resume this caller's bounded journey/cycle; never run another grant."""
        grant = self._grant()
        slot = digest({k: v for k, v in grant.to_dict().items() if k != "authorization_ref"})
        with self._database() as db:
            keys = [row[0] for row in db.execute("SELECT key FROM outbox WHERE slot=?", (slot,))]
        return tuple(self.dispatch_local(key) for key in keys)
