"""P0 detached validation and bounded, durable SOURCE_ONLY A10 -> A03 and A03 -> A04 dispatch.

P1/R4 use a caller-owned SQLite outbox, not a scheduler or provider. An invocation
reservation without a durable receiver witness is never replayed, even after
lease expiry. P0 wire receipts still require independent trusted receipt pins.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Literal, Protocol

from ai_commerce.a04_pulse_handoff.handoff import (
    A04PulseHandoffPacket,
    A04PulseReceiverAuthorization,
    build_a04_pulse_handoff_packet,
    parse_a04_pulse_handoff_packet,
    validate_a04_pulse_receiver_intake,
)
from ai_commerce.creative.briefs import brief_digest, intake_brief
from ai_commerce.creative import briefs as creative_briefs, verified_reasoning_receipt
from ai_commerce.creative._validation import read_json as read_strict_json
from ai_commerce.creative.sidecars import CreativeSidecarStore
from ai_commerce.creative.store import CreativeStore
from ai_commerce.creative.v2 import A03CreativeProductionPackage, A03CreativeProductionResult
from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import canonical_bytes as artifact_bytes
from ai_commerce.media import generation as media_generation
from ai_commerce.opportunity.bridge.briefs import validate_bridge
from ai_commerce.opportunity.core.models import validate_opportunity_decision
from ai_commerce.opportunity.signals.files import local_path, read_local
from ai_commerce.opportunity.signals.files import ledger_path
from ai_commerce.production_runtime import attended_worker, receipt as runtime_receipt
from ai_commerce.production_runtime.contracts import (
    ContractError,
    draft_authorization as pulse_draft_authorization,
    parse_contract as parse_pulse_contract,
    seal_contract as seal_pulse_contract,
)
from ai_commerce.production_runtime.state_machine import (
    AuthorityKind,
    AuthorityReceipt,
    Event as PulseEvent,
    EventKind as PulseEventKind,
    State as PulseState,
    TransitionError,
    VerificationContext,
    transition as pulse_transition,
)

from .contracts import (
    A03PackageHandoff, A04PulseHandoff, BrainDecisionIntakeResult, CONSUMER_ROUTES,
    LocalAuthorization, LocalBrainDecisionInput, LocalHandoff, OrchestrationError,
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
    receiver_receipt: object | None = None


class PulseBrainBridge(Protocol):
    """Caller-owned transport for one exact-chat review, with a write-ahead send guard."""

    def request_decision(
        self, *, evidence_request: bytes, chat_ref: str, reserve_send: Callable[[], bool],
    ) -> LocalBrainDecisionInput | None: ...


class PulseBrainTurnTransport(Protocol):
    """Opaque exact-chat exchange; only the production owner interprets decisions."""

    def request_turn(
        self, *, prompt: str, header: str, chat_ref: str, reserve_send: Callable[[], bool],
    ) -> tuple[str, bool] | None: ...


class PulseBrainDecisionBridge:
    """Compose a caller-owned chat transport with the existing PULSE contracts.

    Transport implementations stay independent of production types and imports.
    A returned UI turn is local evidence, not proof of native execution or identity.
    """

    def __init__(self, *, transport: PulseBrainTurnTransport):
        self._transport = transport

    def request_decision(
        self, *, evidence_request: bytes, chat_ref: str, reserve_send: Callable[[], bool],
    ) -> LocalBrainDecisionInput | None:
        if type(chat_ref) is not str or not re.fullmatch(r"chat:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", chat_ref):
            return None
        try:
            request = runtime_receipt.parse_brain_review_evidence_request(evidence_request)
            nonce = secrets.token_hex(16)
            request_sha = request["request_sha256"]
            header = f"PULSE_BRAIN_DECISION {nonce} {request_sha}\n"
            decision_fields = {
                "schema_version": "BrainDecision.v1",
                "decision_id": "decision:pulse-" + nonce,
                "journey_id": request["journey_id"],
                "source_sha": request["source_sha"],
                "directive_version": request["directive_version"],
                "directive_sha256": request["directive_sha256"],
                "scope": request["scope"],
                "review_scope": request["review_scope"],
                "channel": request["required_channel"],
                "chat_ref": chat_ref,
                "receipt_ref": request["receipt_ref"],
                "provenance": {
                    "actor": "CHATGPT_BRAIN", "actor_ref": "brain:chatgpt-current",
                    "evidence_type": "HUMAN_SUPPLIED", "source_ref": request["receipt_ref"],
                    "source_sha": request["source_sha"],
                },
                "authorization": pulse_draft_authorization(),
            }
            prompt = (
                "Review this PULSE directive using only the supplied evidence. "
                "Reply with the exact header below followed by one plain JSON object. "
                "Copy the decision fields exactly and add only decision and rationale. "
                "Choose APPROVE_DIRECTIVE, REVISE, HOLD, or STOP. Use HOLD if evidence "
                "is insufficient. Do not use tools or take any other action.\n"
                + header + json.dumps(decision_fields, ensure_ascii=True, sort_keys=True) + "\n"
                + evidence_request.decode("utf-8")
            )
            result = self._transport.request_turn(prompt=prompt, header=header,
                                                  chat_ref=chat_ref, reserve_send=reserve_send)
            if type(result) is not tuple or len(result) != 2:
                return None
            answer, test_only = result
            if (type(test_only) is not bool or type(answer) is not str or
                    len(answer) > 16384 or not answer.startswith(header)):
                return None
            body = read_strict_json(answer[len(header):])
            if (type(body) is not dict or set(body) != {*decision_fields, "decision", "rationale"} or
                    any(body[field] != value for field, value in decision_fields.items())):
                return None
            decision = seal_pulse_contract("BrainDecision", body)
            return LocalBrainDecisionInput(
                decision_bytes=decision.raw,
                origin_kind="TEST_ONLY" if test_only else "LOCAL_CHATGPT_TRANSFER",
                origin_ref=("TEST_ONLY:" if test_only else "") + "pulse-cdp-ui:" + nonce,
                chat_ref=chat_ref, evidence_request_sha256=request_sha,
            )
        except OrchestrationError:
            raise
        except Exception:
            return None


_PULSE_BRAIN_SEND_INTENT = "PulseBrainSendIntent"


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
                 max_attempts: int = 2, lease_seconds: int = 60,
                 pulse_brain_bridge: PulseBrainBridge | None = None) -> None:
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
        self._pulse_brain_bridge = pulse_brain_bridge
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
        if spec.route_id == "a04-media-preparation":
            require(type(input_value) is dict and bytes_digest(artifact_bytes(input_value)) == spec.input_sha256,
                    "INPUT_HASH_MISMATCH")
            require(type(output) is dict and bytes_digest(artifact_bytes(output)) == spec.output_sha256,
                    "OUTPUT_HASH_MISMATCH")
            try:
                pkg = A03CreativeProductionPackage.from_dict(output)
                require(pkg.digest == spec.output_sha256, "OUTPUT_HASH_MISMATCH")
                prod_res = A03CreativeProductionResult.from_dict(input_value)
                require(prod_res.status == "CREATIVE_COMPLETE" and prod_res.package is not None
                        and prod_res.package.package_id == pkg.package_id
                        and prod_res.package.brief_id == pkg.brief_id
                        and prod_res.package.source_brief_digest == pkg.source_brief_digest
                        and prod_res.package.selected_concept == pkg.selected_concept
                        and prod_res.package.lineage == pkg.lineage, "A04_INPUT_BINDING")
            except OrchestrationError:
                raise
            except (ValueError, TypeError, KeyError):
                raise OrchestrationError("CONSUMER_CONTRACT_INVALID") from None
        elif spec.route_id == "a04-to-pulse-offline":
            require(type(input_value) is dict and bytes_digest(artifact_bytes(input_value)) == spec.input_sha256,
                    "INPUT_HASH_MISMATCH")
            try:
                pkt = A04PulseHandoffPacket.from_dict(output)
                require(bytes_digest(pkt.to_canonical_bytes()) == spec.output_sha256, "OUTPUT_HASH_MISMATCH")
                a04_rec = media_generation.A04CreativeHandoffReceipt.from_dict(input_value)
                require(
                    a04_rec.receipt_id == pkt.a04_receipt_id
                    and a04_rec.digest == pkt.a04_receipt_digest == spec.source_sha256
                    and a04_rec.journey_id == pkt.journey_id
                    and a04_rec.cycle_id == pkt.cycle_id
                    and a04_rec.job_id == pkt.job_id
                    and a04_rec.attempt == pkt.attempt
                    and a04_rec.source_sha == pkt.source_sha,
                    "A04_PULSE_INPUT_BINDING",
                )
            except OrchestrationError:
                raise
            except (ValueError, TypeError, KeyError):
                raise OrchestrationError("CONSUMER_CONTRACT_INVALID") from None
        else:
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
    def _database(self, *, read_only: bool = False):
        require(self._state_dir is not None, "STATE_DIR_REQUIRED")
        try:
            path = ledger_path(self._state_dir / "production.sqlite", create=not read_only)
            if read_only:
                db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
            else:
                db = sqlite3.connect(path, timeout=5)
        except ValueError:
            if not read_only:
                raise
            raise OrchestrationError("JOURNAL_UNAVAILABLE") from None
        except sqlite3.Error:
            raise OrchestrationError("JOURNAL_UNAVAILABLE") from None
        db.row_factory = sqlite3.Row
        try:
            if not read_only:
                db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if read_only:
                require(version == 1, "JOURNAL_SCHEMA_VERSION")
            else:
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

    @staticmethod
    def _read_canonical_json(path: Path, limit: int = 4 * 1024 * 1024) -> tuple[dict, bytes]:
        raw = read_local(path, limit=limit)
        value = read_strict_json(raw.decode("utf-8"))
        require(type(value) is dict and raw == artifact_bytes(value), "ARTIFACT_BYTES_INVALID")
        return value, raw

    @staticmethod
    def _base_slot(grant: LocalAuthorization) -> str:
        return digest({k: v for k, v in grant.to_dict().items() if k != "authorization_ref"})

    def complete_a10(self, result: dict, *, result_ref: str | Path, brief_ref: str | Path,
                     dispatch: bool = True) -> DispatchResult:
        """Called by the real producer after its immutable success is committed.

        Event transport cannot insert executable records through record_completed
        or record_receipt. Only this validated producer seam creates a P1 outbox.
        ``dispatch=False`` supports persist-before-delivery transport/recovery.
        """
        grant = self._grant()
        require(grant.route_id == "a10-m05-to-a03", "LOCAL_ROUTE_MISMATCH")
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
        slot = self._base_slot(grant)
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

    def _a03_producer(
        self,
        result: A03CreativeProductionResult | dict,
        *,
        store: CreativeStore | CreativeSidecarStore | str | Path,
        result_ref: str | Path | None = None,
        package: A03CreativeProductionPackage | dict | None = None,
        package_ref: str | Path | None = None,
    ) -> tuple[A03CreativeProductionResult, A03CreativeProductionPackage, Path, Path, bytes, Path, bytes]:
        """Resolve genuine persisted A03 R3 CREATIVE_COMPLETE result and package."""
        grant = self._grant()
        require(grant.route_id == "a03-to-a04", "LOCAL_ROUTE_MISMATCH")
        if isinstance(store, CreativeSidecarStore):
            store_root = local_path(store.creative.root)
        elif isinstance(store, CreativeStore):
            store_root = local_path(store.root)
        elif isinstance(store, (str, Path)):
            store_root = local_path(store)
        else:
            raise OrchestrationError("PRODUCER_STORE_REQUIRED")
        sidecars = CreativeSidecarStore(store_root)
        if isinstance(result, A03CreativeProductionResult):
            prod = A03CreativeProductionResult.from_dict(result.to_dict())
        elif type(result) is dict:
            require(result.get("schema_version") == "A03CreativeProductionResult.v1", "PRODUCER_SCHEMA")
            prod = A03CreativeProductionResult.from_dict(result)
        else:
            raise OrchestrationError("PRODUCER_SCHEMA")
        require(prod.status == "CREATIVE_COMPLETE" and prod.failure is None
                and prod.package is not None and prod.job.purpose == "PRODUCTION"
                and prod.stage is not None and prod.stage.status == "CREATIVE_COMPLETE",
                "PRODUCER_NOT_COMPLETE")
        require((prod.job.journey_id, prod.job.cycle_id, prod.job.job_id) ==
                (grant.journey_id, grant.cycle_id, grant.job_id), "PRODUCER_SOURCE_MISMATCH")
        expected_result_path = local_path(sidecars.production_result_path(prod.digest))
        if result_ref is not None:
            require(local_path(result_ref) == expected_result_path, "PRODUCER_REFERENCE_MISMATCH")
        saved_result, result_raw = self._read_canonical_json(expected_result_path)
        require(bytes_digest(result_raw) == prod.digest and saved_result == prod.to_dict(),
                "PRODUCER_RESULT_MISMATCH")
        if package is None:
            target_pkg = prod.package
        elif isinstance(package, A03CreativeProductionPackage):
            target_pkg = A03CreativeProductionPackage.from_dict(package.to_dict())
        elif type(package) is dict:
            require(package.get("schema_version") == "A03CreativeProductionPackage.v1", "PRODUCER_CONTRACT_INVALID")
            target_pkg = A03CreativeProductionPackage.from_dict(package)
        else:
            raise OrchestrationError("PRODUCER_CONTRACT_INVALID")
        expected_pkg_path = local_path(sidecars.production_package_path(target_pkg.package_id, target_pkg.revision))
        if package_ref is not None:
            require(local_path(package_ref) == expected_pkg_path, "PRODUCER_REFERENCE_MISMATCH")
        saved_pkg, pkg_raw = self._read_canonical_json(expected_pkg_path)
        require(bytes_digest(pkg_raw) == target_pkg.digest and saved_pkg == target_pkg.to_dict(),
                "OUTPUT_BYTES_MISMATCH")
        origin, verified_pkg, _ = sidecars.verify_production_package_lineage(
            target_pkg.package_id, target_pkg.revision, origin_result_digest=prod.digest,
        )
        require(origin == prod and verified_pkg == target_pkg, "PRODUCER_RESULT_MISMATCH")
        require(verified_reasoning_receipt(origin.stage, authorization=origin.authorization) is not None
                and verified_pkg.lineage.cycle_status == "CREATIVE_COMPLETE", "PRODUCER_NOT_COMPLETE")
        cycle = sidecars.load_cycle_by_digest(
            verified_pkg.lineage.cycle_job_digest, verified_pkg.lineage.cycle_result_digest,
        )
        require(cycle.status == "CREATIVE_COMPLETE" and cycle.job.seed.purpose == "PRODUCTION",
                "PRODUCER_NOT_COMPLETE")
        for step in cycle.steps:
            require(verified_reasoning_receipt(step, authorization=cycle.authorization) is not None,
                    "PRODUCER_NOT_COMPLETE")
        require(verified_pkg.factual_status == "UNVERIFIED"
                and verified_pkg.rights_status in ("UNKNOWN", "EVIDENCE_BOUND")
                and all(t.generation_status == "NOT_GENERATED" for t in verified_pkg.spec.media_targets)
                and all(q.verification_status == "UNVERIFIED" for q in verified_pkg.spec.qa_expectations),
                "PRODUCER_CONTRACT_INVALID")
        return origin, verified_pkg, store_root, expected_result_path, result_raw, expected_pkg_path, pkg_raw

    def complete_a03(
        self,
        result: A03CreativeProductionResult | dict,
        *,
        store: CreativeStore | CreativeSidecarStore | str | Path,
        result_ref: str | Path | None = None,
        package: A03CreativeProductionPackage | dict | None = None,
        package_ref: str | Path | None = None,
        dispatch: bool = True,
    ) -> DispatchResult:
        """Persist and dispatch a verified A03 R3 production package to registered A04.

        Only a real saved A03CreativeProductionResult.v1 with status CREATIVE_COMPLETE
        (and optional verified HUMAN/SYSTEM editable revision retaining immutable parent)
        is admitted. Produces SOURCE_HANDOFF_VERIFIED only, never media execution,
        rights grants, QA approval, or PULSE execution.
        """
        grant = self._grant()
        require(grant.route_id == "a03-to-a04", "LOCAL_ROUTE_MISMATCH")
        require(type(dispatch) is bool, "DISPATCH_FLAG")
        try:
            origin, verified_pkg, store_root, result_path, result_raw, pkg_path, pkg_raw = self._a03_producer(
                result, store=store, result_ref=result_ref, package=package, package_ref=package_ref,
            )
        except OrchestrationError:
            raise
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            raise OrchestrationError("PRODUCER_CONTRACT_INVALID") from None
        actor_ref = f"{verified_pkg.edit_actor.kind}:{verified_pkg.edit_actor.ref}"
        handoff = A03PackageHandoff(
            journey_id=grant.journey_id,
            cycle_id=grant.cycle_id,
            job_id=grant.job_id,
            attempt=origin.job.attempt,
            source_sha=grant.source_sha,
            actor_ref=actor_ref,
            creative_store_root=str(store_root),
            a03_result_ref=str(result_path),
            a03_result_digest=bytes_digest(result_raw),
            package_id=verified_pkg.package_id,
            revision=verified_pkg.revision,
            parent_package_digest=verified_pkg.parent.digest if verified_pkg.parent is not None else None,
            package_ref=str(pkg_path),
            package_bytes_sha256=bytes_digest(pkg_raw),
        )
        consumer, path = CONSUMER_ROUTES["a04-media-preparation"]
        key = digest({"handoff": handoff.to_dict(), "consumer": consumer, "output": handoff.package_bytes_sha256})
        input_digest = digest({
            "a03_result_digest": handoff.a03_result_digest,
            "cycle_result_digest": verified_pkg.lineage.cycle_result_digest,
            "package_id": handoff.package_id,
            "revision": handoff.revision,
        })
        spec = StageSpec(
            stage_id="A03.creative",
            route_id="a04-media-preparation",
            consumer_id=consumer,
            consumer_path=path,
            source_sha256=handoff.a03_result_digest,
            input_sha256=input_digest,
            output_sha256=handoff.package_bytes_sha256,
            idempotency_key=key,
            output_contract="A03CreativeProductionPackage.v1",
            package_handoff=handoff,
            provenance=Provenance(
                actor_ref="ai_commerce.creative.v2:run_creative_production",
                source_ref=handoff.a03_result_ref,
                source_sha256=handoff.a03_result_digest,
                evidence_type=origin.job.provenance.evidence_type,
            ),
            authorization=RouteAuthorization(
                route_id="a04-media-preparation",
                consumer_id=consumer,
                consumer_path=path,
                authorization_ref=grant.authorization_ref,
                scope="SOURCE_ONLY_LOCAL",
            ),
        )
        completed = ProductionStageCompleted(
            spec=spec,
            completion_ref=handoff.a03_result_ref,
            source_sha256=spec.source_sha256,
            input_sha256=spec.input_sha256,
            output_sha256=spec.output_sha256,
        )
        slot = f"{self._base_slot(grant)}:{handoff.package_id}:r{handoff.revision}"
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

    @staticmethod
    def _pulse_receiver_authorization(
        grant: LocalAuthorization,
        *,
        attempt: int,
        packet_id: str,
        packet_sha256: str,
        logical_identity: str,
    ) -> A04PulseReceiverAuthorization:
        return A04PulseReceiverAuthorization(
            authorization_ref=grant.authorization_ref,
            caller_id="ai_commerce.production_orchestration.orchestrator:ProductionPipelineOrchestrator",
            journey_id=grant.journey_id,
            cycle_id=grant.cycle_id,
            job_id=grant.job_id,
            attempt=attempt,
            source_sha=grant.source_sha,
            packet_id=packet_id,
            packet_sha256=packet_sha256,
            logical_identity=logical_identity,
            human_attended=False,
        )

    @staticmethod
    def _pulse_mapping(values: tuple[str, ...] | list[str]) -> tuple[tuple[str, ...], dict[str, Path]]:
        require(type(values) in (tuple, list) and len(values) <= 4, "PRODUCER_CONTRACT_INVALID")
        normalized: list[str] = []
        mapping: dict[str, Path] = {}
        for item in values:
            require(type(item) is str and "=" in item, "PRODUCER_CONTRACT_INVALID")
            ref, raw_path = item.split("=", 1)
            require(ref not in mapping and len(ref) > 0, "PRODUCER_CONTRACT_INVALID")
            resolved = local_path(raw_path)
            mapping[ref] = resolved
            normalized.append(f"{ref}={resolved}")
        return tuple(normalized), mapping

    def _a04_pulse_producer(
        self,
        *,
        a04_receipt_ref: str | Path,
        directive_ref: str | Path,
        assets: tuple[str, ...] | list[str],
        asset_root: str | Path,
        rights: tuple[str, ...] | list[str] = (),
        packet: A04PulseHandoffPacket | dict | bytes | None = None,
        packet_ref: str | Path | None = None,
        package_ref: str | Path | None = None,
        a03_result_ref: str | Path | None = None,
        cta_mode: str = "NONE",
        cta_text: str | None = None,
        min_directive_version: int | None = None,
        min_package_revision: int | None = None,
    ) -> tuple[
        A04PulseHandoffPacket,
        bytes,
        Path,
        media_generation.A04CreativeHandoffReceipt,
        Path,
        bytes,
        Path,
        Path,
        tuple[str, ...],
        tuple[str, ...],
        Path | None,
        Path | None,
    ]:
        """Resolve genuine persisted A04 receipt, CreativeDirective, assets, rights, and packet."""
        grant = self._grant()
        require(grant.route_id == "a04-to-pulse-offline", "LOCAL_ROUTE_MISMATCH")
        receipt_path = local_path(a04_receipt_ref)
        receipt_raw = read_local(receipt_path, limit=131072)
        a04_receipt = media_generation.load_a04_handoff_receipt(receipt_path)
        require(
            bytes_digest(receipt_raw) == a04_receipt.digest
            and a04_receipt.status == "SOURCE_HANDOFF_VERIFIED"
            and a04_receipt.source_sha == grant.source_sha
            and (a04_receipt.journey_id, a04_receipt.cycle_id, a04_receipt.job_id)
            == (grant.journey_id, grant.cycle_id, grant.job_id),
            "PRODUCER_SOURCE_MISMATCH",
        )

        dir_path = local_path(directive_ref)
        directive_raw = read_local(dir_path, limit=131072)
        directive_contract = parse_pulse_contract("CreativeDirective", directive_raw)
        require(directive_contract.raw == directive_raw, "OUTPUT_BYTES_MISMATCH")

        resolved_asset_root = local_path(asset_root)
        asset_items, asset_paths = self._pulse_mapping(assets)
        rights_items, rights_paths = self._pulse_mapping(rights)
        require(1 <= len(asset_items) <= 4, "PRODUCER_CONTRACT_INVALID")
        asset_bytes_by_ref = {
            ref: read_local(path, limit=4 * 1024 * 1024) for ref, path in asset_paths.items()
        }
        rights_bytes_by_ref = {
            ref: read_local(path, limit=131072) for ref, path in rights_paths.items()
        }

        pkg_path = local_path(package_ref) if package_ref is not None else None
        package_raw = read_local(pkg_path, limit=4 * 1024 * 1024) if pkg_path is not None else None
        res_path = local_path(a03_result_ref) if a03_result_ref is not None else None
        a03_result_raw = read_local(res_path, limit=4 * 1024 * 1024) if res_path is not None else None

        persisted_packet_path: Path | None = None
        if packet_ref is not None:
            persisted_packet_path = local_path(packet_ref)
            packet_raw = read_local(persisted_packet_path, limit=131072)
            pkt = parse_a04_pulse_handoff_packet(packet_raw)
            if packet is not None:
                if type(packet) is bytes:
                    require(packet == packet_raw, "OUTPUT_BYTES_MISMATCH")
                elif type(packet) is A04PulseHandoffPacket:
                    require(packet == pkt, "OUTPUT_BYTES_MISMATCH")
                elif type(packet) is dict:
                    require(A04PulseHandoffPacket.from_dict(packet) == pkt, "OUTPUT_BYTES_MISMATCH")
                else:
                    raise OrchestrationError("PRODUCER_CONTRACT_INVALID")
        elif packet is not None:
            if type(packet) is bytes:
                pkt = parse_a04_pulse_handoff_packet(packet)
            elif type(packet) is A04PulseHandoffPacket:
                pkt = A04PulseHandoffPacket.from_dict(packet.to_dict())
            elif type(packet) is dict:
                pkt = A04PulseHandoffPacket.from_dict(packet)
            else:
                raise OrchestrationError("PRODUCER_CONTRACT_INVALID")
            packet_raw = pkt.to_canonical_bytes()
        else:
            pkt = build_a04_pulse_handoff_packet(
                a04_receipt=a04_receipt,
                directive=directive_contract,
                cta_mode=cta_mode,
                cta_text=cta_text,
                directive_bytes=directive_raw,
                receipt_bytes=receipt_raw,
                package_bytes=package_raw,
                a03_result_bytes=a03_result_raw,
                asset_bytes_by_ref=asset_bytes_by_ref,
                rights_bytes_by_ref=rights_bytes_by_ref,
                expected_source_sha=grant.source_sha,
                expected_journey_id=grant.journey_id,
                expected_cycle_id=grant.cycle_id,
                min_directive_version=min_directive_version,
                min_package_revision=min_package_revision,
            )
            packet_raw = pkt.to_canonical_bytes()

        receiver_auth = self._pulse_receiver_authorization(
            grant,
            attempt=pkt.attempt,
            packet_id=pkt.packet_id,
            packet_sha256=pkt.packet_sha256,
            logical_identity=pkt.logical_identity,
        )
        validate_a04_pulse_receiver_intake(
            packet_raw,
            a04_receipt=receipt_raw,
            directive=directive_raw,
            authorization=receiver_auth,
            expected_source_sha=grant.source_sha,
            directive_bytes=directive_raw,
            receipt_bytes=receipt_raw,
            package_bytes=package_raw,
            a03_result_bytes=a03_result_raw,
            asset_bytes_by_ref=asset_bytes_by_ref,
            rights_bytes_by_ref=rights_bytes_by_ref,
            expected_journey_id=grant.journey_id,
            expected_cycle_id=grant.cycle_id,
            min_directive_version=min_directive_version,
            min_package_revision=min_package_revision,
        )

        # Pre-check local rights freshness, PNG headers/dimensions, and private custody
        # before creating any packet file or state directory.
        attended_worker._private(str(resolved_asset_root))
        for pin in pkt.assets:
            img_path = attended_worker._local(str(asset_paths[pin.asset_ref]), under=resolved_asset_root)
            require(img_path.suffix.lower() == ".png", "PRODUCER_CONTRACT_INVALID")
            img_raw = asset_bytes_by_ref[pin.asset_ref]
            require(img_raw.startswith(b"\x89PNG\r\n\x1a\n") and len(img_raw) >= 24, "PRODUCER_CONTRACT_INVALID")
            w, h = int.from_bytes(img_raw[16:20]), int.from_bytes(img_raw[20:24])
            require(0 < w <= 1024 and 0 < h <= 1024, "PRODUCER_CONTRACT_INVALID")
            if pin.evidence_type == "HUMAN_SUPPLIED":
                require(pin.rights_ref in rights_paths, "PRODUCER_CONTRACT_INVALID")
                claim_path = attended_worker._local(str(rights_paths[pin.rights_ref]), under=resolved_asset_root)
                claim = attended_worker._json(claim_path)
                require(
                    set(claim) == {"schema_version", "asset_sha256", "source_sha", "scope", "status", "valid_from", "expires_at"}
                    and claim["schema_version"] == "LocalStillRights.v1"
                    and claim["asset_sha256"] == pin.asset_sha256
                    and claim["source_sha"] == grant.source_sha
                    and claim["scope"] == "OFFLINE_LOCAL_RENDER"
                    and claim["status"] == "GRANTED"
                    and type(claim["valid_from"]) is int
                    and type(claim["expires_at"]) is int
                    and 0 <= claim["valid_from"] <= attended_worker._now() < claim["expires_at"],
                    "PRODUCER_CONTRACT_INVALID",
                )

        if persisted_packet_path is None:
            packet_dir = local_path(self._state_dir / "a04-pulse-packets")
            packet_dir.mkdir(parents=True, exist_ok=True)
            persisted_packet_path = local_path(packet_dir / f"{pkt.packet_id}.json")
            runtime_receipt.put_new(persisted_packet_path, packet_raw)

        # Full receiver-contract preflight on the exact persisted files.
        attended_worker._autonomous_inputs(
            authorization=receiver_auth,
            packet_path=str(persisted_packet_path),
            a04_receipt_path=str(receipt_path),
            directive_path=str(dir_path),
            assets=asset_items,
            rights=rights_items,
            asset_root=str(resolved_asset_root),
            source_sha=grant.source_sha,
            package_path=str(pkg_path) if pkg_path is not None else None,
            a03_result_path=str(res_path) if res_path is not None else None,
        )
        return (
            pkt, packet_raw, persisted_packet_path, a04_receipt, receipt_path,
            directive_raw, dir_path, resolved_asset_root, asset_items, rights_items,
            pkg_path, res_path,
        )

    def complete_a04_pulse(
        self,
        *,
        a04_receipt_ref: str | Path,
        directive_ref: str | Path,
        assets: tuple[str, ...] | list[str],
        asset_root: str | Path,
        rights: tuple[str, ...] | list[str] = (),
        packet: A04PulseHandoffPacket | dict | bytes | None = None,
        packet_ref: str | Path | None = None,
        package_ref: str | Path | None = None,
        a03_result_ref: str | Path | None = None,
        cta_mode: str = "NONE",
        cta_text: str | None = None,
        min_directive_version: int | None = None,
        min_package_revision: int | None = None,
        dispatch: bool = True,
    ) -> DispatchResult:
        """Persist and dispatch a verified A04 -> PULSE offline handoff to WAIT_BRAIN."""
        grant = self._grant()
        require(grant.route_id == "a04-to-pulse-offline", "LOCAL_ROUTE_MISMATCH")
        require(type(dispatch) is bool, "DISPATCH_FLAG")
        try:
            (
                pkt, packet_raw, persisted_packet_path, a04_receipt, receipt_path,
                directive_raw, dir_path, resolved_asset_root, asset_items, rights_items,
                pkg_path, res_path,
            ) = self._a04_pulse_producer(
                a04_receipt_ref=a04_receipt_ref,
                directive_ref=directive_ref,
                assets=assets,
                asset_root=asset_root,
                rights=rights,
                packet=packet,
                packet_ref=packet_ref,
                package_ref=package_ref,
                a03_result_ref=a03_result_ref,
                cta_mode=cta_mode,
                cta_text=cta_text,
                min_directive_version=min_directive_version,
                min_package_revision=min_package_revision,
            )
        except OrchestrationError:
            raise
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            raise OrchestrationError("PRODUCER_CONTRACT_INVALID") from None

        handoff = A04PulseHandoff(
            journey_id=grant.journey_id,
            cycle_id=grant.cycle_id,
            job_id=grant.job_id,
            attempt=pkt.attempt,
            source_sha=grant.source_sha,
            packet_id=pkt.packet_id,
            packet_sha256=pkt.packet_sha256,
            logical_identity=pkt.logical_identity,
            packet_ref=str(persisted_packet_path),
            packet_bytes_sha256=bytes_digest(packet_raw),
            a04_receipt_id=a04_receipt.receipt_id,
            a04_receipt_digest=a04_receipt.digest,
            a04_receipt_ref=str(receipt_path),
            directive_version=pkt.directive_version,
            directive_sha256=pkt.directive_sha256,
            directive_bytes_sha256=bytes_digest(directive_raw),
            directive_ref=str(dir_path),
            asset_root=str(resolved_asset_root),
            assets=asset_items,
            rights=rights_items,
            package_id=pkt.package_id,
            package_revision=pkt.package_revision,
            package_ref=str(pkg_path) if pkg_path is not None else None,
            a03_result_ref=str(res_path) if res_path is not None else None,
        )
        consumer, path = CONSUMER_ROUTES["a04-to-pulse-offline"]
        key = digest({"handoff": handoff.to_dict(), "consumer": consumer, "output": handoff.packet_bytes_sha256})
        input_digest = digest({
            "a04_receipt_id": handoff.a04_receipt_id,
            "a04_receipt_digest": handoff.a04_receipt_digest,
            "directive_bytes_sha256": handoff.directive_bytes_sha256,
            "logical_identity": handoff.logical_identity,
        })
        spec = StageSpec(
            stage_id="A04.pulse_handoff",
            route_id="a04-to-pulse-offline",
            consumer_id=consumer,
            consumer_path=path,
            source_sha256=handoff.a04_receipt_digest,
            input_sha256=input_digest,
            output_sha256=handoff.packet_bytes_sha256,
            idempotency_key=key,
            output_contract="A04PulseOfflineHandoffPacket.v1",
            pulse_handoff=handoff,
            provenance=Provenance(
                actor_ref="ai_commerce.a04_pulse_handoff:build_a04_pulse_handoff_packet",
                source_ref=handoff.a04_receipt_ref,
                source_sha256=handoff.a04_receipt_digest,
                evidence_type=pkt.provenance.directive_evidence_type,
            ),
            authorization=RouteAuthorization(
                route_id="a04-to-pulse-offline",
                consumer_id=consumer,
                consumer_path=path,
                authorization_ref=grant.authorization_ref,
                scope="SOURCE_ONLY_LOCAL",
            ),
        )
        completed = ProductionStageCompleted(
            spec=spec,
            completion_ref=handoff.a04_receipt_ref,
            source_sha256=spec.source_sha256,
            input_sha256=spec.input_sha256,
            output_sha256=spec.output_sha256,
        )
        slot = f"{self._base_slot(grant)}:{handoff.logical_identity}"
        with self._database() as db:
            previous = db.execute("SELECT * FROM outbox WHERE slot=? OR key=?", (slot, key)).fetchone()
            if previous:
                require(previous["key"] == key and json.loads(previous["completed"]) == completed.to_dict(),
                        "IDEMPOTENCY_CONFLICT")
            else:
                for existing_row in db.execute("SELECT completed FROM outbox"):
                    existing_spec = json.loads(existing_row["completed"]).get("spec", {})
                    existing_pulse = existing_spec.get("pulse_handoff")
                    if type(existing_pulse) is not dict:
                        continue
                    require(
                        existing_pulse.get("logical_identity") != handoff.logical_identity
                        and existing_pulse.get("packet_id") != handoff.packet_id
                        and existing_pulse.get("a04_receipt_id") != handoff.a04_receipt_id
                        and (
                            existing_pulse.get("journey_id"),
                            existing_pulse.get("cycle_id"),
                            existing_pulse.get("package_id"),
                            existing_pulse.get("package_revision"),
                        ) != (
                            handoff.journey_id,
                            handoff.cycle_id,
                            handoff.package_id,
                            handoff.package_revision,
                        )
                        and (
                            existing_pulse.get("journey_id"),
                            existing_pulse.get("cycle_id"),
                            existing_pulse.get("job_id"),
                            existing_pulse.get("attempt"),
                        ) != (
                            handoff.journey_id,
                            handoff.cycle_id,
                            handoff.job_id,
                            handoff.attempt,
                        ),
                        "DUPLICATE_LOGICAL_IDENTITY",
                    )
                db.execute("INSERT INTO outbox(key,slot,completed,state) VALUES (?,?,?,'PENDING')",
                           (key, slot, canonical_bytes(completed.to_dict()).decode("utf-8")))
                self._journal(db, key, completed.schema_version, completed.to_dict())
        self.last_result = self.dispatch_local(key) if dispatch else DispatchResult(status="PENDING", completed=completed)
        return self.last_result

    def _authorize_completed(self, completed: ProductionStageCompleted) -> None:
        grant = self._grant()
        spec = completed.spec
        if spec.local_handoff is not None:
            handoff = spec.local_handoff
            require((handoff.journey_id, handoff.cycle_id, handoff.job_id, handoff.source_sha,
                    handoff.route_id) == (grant.journey_id, grant.cycle_id, grant.job_id, grant.source_sha, grant.route_id)
                    and spec.authorization.authorization_ref == grant.authorization_ref
                    and spec.authorization.scope == "SOURCE_ONLY_LOCAL", "ROUTE_NOT_AUTHORIZED")
            require(spec.stage_id == "A10.M05" and spec.route_id == "a03-creative-brief"
                    and (spec.consumer_id, spec.consumer_path) == CONSUMER_ROUTES["a03-creative-brief"],
                    "LOCAL_ROUTE_MISMATCH")
            require(spec.source_sha256 == handoff.producer_result_sha256
                    and completed.completion_ref == handoff.producer_result_ref
                    and spec.provenance.source_ref == handoff.producer_result_ref
                    and spec.provenance.actor_ref == "ai_commerce.opportunity.bridge.cli:run_automation"
                    and spec.input_sha256 == digest({"producer_event_id": handoff.producer_event_id,
                                                    "result_sha256": handoff.producer_result_sha256})
                    and spec.idempotency_key == digest({"handoff": handoff.to_dict(), "consumer": spec.consumer_id,
                                                        "output": spec.output_sha256}), "LOCAL_COMPLETION_BINDING")
            return
        if spec.package_handoff is not None:
            handoff = spec.package_handoff
            require((handoff.journey_id, handoff.cycle_id, handoff.job_id, handoff.source_sha,
                    handoff.route_id) == (grant.journey_id, grant.cycle_id, grant.job_id, grant.source_sha, grant.route_id)
                    and spec.authorization.authorization_ref == grant.authorization_ref
                    and spec.authorization.scope == "SOURCE_ONLY_LOCAL", "ROUTE_NOT_AUTHORIZED")
            require(spec.stage_id == "A03.creative" and spec.route_id == "a04-media-preparation"
                    and spec.output_contract == "A03CreativeProductionPackage.v1"
                    and (spec.consumer_id, spec.consumer_path) == CONSUMER_ROUTES["a04-media-preparation"],
                    "LOCAL_ROUTE_MISMATCH")
            require(spec.source_sha256 == handoff.a03_result_digest
                    and completed.completion_ref == handoff.a03_result_ref
                    and spec.provenance.source_ref == handoff.a03_result_ref
                    and spec.provenance.actor_ref == "ai_commerce.creative.v2:run_creative_production"
                    and spec.output_sha256 == handoff.package_bytes_sha256
                    and spec.idempotency_key == digest({"handoff": handoff.to_dict(), "consumer": spec.consumer_id,
                                                        "output": spec.output_sha256}), "LOCAL_COMPLETION_BINDING")
            return
        handoff = spec.pulse_handoff
        require(handoff is not None and (handoff.journey_id, handoff.cycle_id, handoff.job_id, handoff.source_sha,
                handoff.route_id) == (grant.journey_id, grant.cycle_id, grant.job_id, grant.source_sha, grant.route_id)
                and spec.authorization.authorization_ref == grant.authorization_ref
                and spec.authorization.scope == "SOURCE_ONLY_LOCAL", "ROUTE_NOT_AUTHORIZED")
        require(spec.stage_id == "A04.pulse_handoff" and spec.route_id == "a04-to-pulse-offline"
                and spec.output_contract == "A04PulseOfflineHandoffPacket.v1"
                and (spec.consumer_id, spec.consumer_path) == CONSUMER_ROUTES["a04-to-pulse-offline"],
                "LOCAL_ROUTE_MISMATCH")
        require(spec.source_sha256 == handoff.a04_receipt_digest
                and completed.completion_ref == handoff.a04_receipt_ref
                and spec.provenance.source_ref == handoff.a04_receipt_ref
                and spec.provenance.actor_ref == "ai_commerce.a04_pulse_handoff:build_a04_pulse_handoff_packet"
                and spec.output_sha256 == handoff.packet_bytes_sha256
                and spec.input_sha256 == digest({
                    "a04_receipt_id": handoff.a04_receipt_id,
                    "a04_receipt_digest": handoff.a04_receipt_digest,
                    "directive_bytes_sha256": handoff.directive_bytes_sha256,
                    "logical_identity": handoff.logical_identity,
                })
                and spec.idempotency_key == digest({"handoff": handoff.to_dict(), "consumer": spec.consumer_id,
                                                    "output": spec.output_sha256}), "LOCAL_COMPLETION_BINDING")

    def _verified_input(self, completed: ProductionStageCompleted) -> bytes:
        self._authorize_completed(completed)
        spec = completed.spec
        if spec.local_handoff is not None:
            handoff = spec.local_handoff
            result, source = self._read_json(Path(handoff.producer_result_ref))
            require(bytes_digest(source) == handoff.producer_result_sha256 == spec.source_sha256,
                    "SOURCE_HASH_MISMATCH")
            brief, raw, _ = self._producer(result, Path(handoff.producer_result_ref), Path(handoff.brief_ref))
            require(bytes_digest(raw) == handoff.brief_bytes_sha256 and digest(brief) == spec.output_sha256,
                    "OUTPUT_HASH_MISMATCH")
            require(result["event_id"] == handoff.producer_event_id, "PRODUCER_EVENT_HASH")
            return raw
        if spec.package_handoff is not None:
            handoff = spec.package_handoff
            result_dict, result_raw = self._read_canonical_json(Path(handoff.a03_result_ref))
            require(bytes_digest(result_raw) == handoff.a03_result_digest == spec.source_sha256,
                    "SOURCE_HASH_MISMATCH")
            pkg_dict, pkg_raw = self._read_canonical_json(Path(handoff.package_ref))
            require(bytes_digest(pkg_raw) == handoff.package_bytes_sha256 == spec.output_sha256,
                    "OUTPUT_HASH_MISMATCH")
            origin, verified_pkg, _, _, _, _, _ = self._a03_producer(
                result_dict, store=handoff.creative_store_root,
                result_ref=handoff.a03_result_ref, package=pkg_dict, package_ref=handoff.package_ref,
            )
            require(origin.job.attempt == handoff.attempt
                    and f"{verified_pkg.edit_actor.kind}:{verified_pkg.edit_actor.ref}" == handoff.actor_ref
                    and (verified_pkg.parent.digest if verified_pkg.parent is not None else None)
                    == handoff.parent_package_digest
                    and spec.provenance.evidence_type == origin.job.provenance.evidence_type
                    and spec.input_sha256 == digest({
                        "a03_result_digest": handoff.a03_result_digest,
                        "cycle_result_digest": verified_pkg.lineage.cycle_result_digest,
                        "package_id": handoff.package_id,
                        "revision": handoff.revision,
                    }), "LOCAL_COMPLETION_BINDING")
            return pkg_raw
        handoff = spec.pulse_handoff
        grant = self._grant()
        packet_raw = read_local(Path(handoff.packet_ref), limit=131072)
        receipt_raw = read_local(Path(handoff.a04_receipt_ref), limit=131072)
        directive_raw = read_local(Path(handoff.directive_ref), limit=131072)
        if handoff.package_ref is not None:
            read_local(Path(handoff.package_ref), limit=4 * 1024 * 1024)
        if handoff.a03_result_ref is not None:
            read_local(Path(handoff.a03_result_ref), limit=4 * 1024 * 1024)
        for item in (*handoff.assets, *handoff.rights):
            _, raw_path = item.split("=", 1)
            read_local(Path(raw_path), limit=4 * 1024 * 1024)
        require(bytes_digest(packet_raw) == handoff.packet_bytes_sha256 == spec.output_sha256,
                "OUTPUT_HASH_MISMATCH")
        require(bytes_digest(receipt_raw) == handoff.a04_receipt_digest == spec.source_sha256,
                "SOURCE_HASH_MISMATCH")
        require(bytes_digest(directive_raw) == handoff.directive_bytes_sha256,
                "INPUT_HASH_MISMATCH")
        pkt = parse_a04_pulse_handoff_packet(packet_raw)
        require(
            pkt.packet_id == handoff.packet_id
            and pkt.packet_sha256 == handoff.packet_sha256
            and pkt.logical_identity == handoff.logical_identity
            and pkt.a04_receipt_id == handoff.a04_receipt_id
            and pkt.a04_receipt_digest == handoff.a04_receipt_digest
            and pkt.directive_version == handoff.directive_version
            and pkt.directive_sha256 == handoff.directive_sha256
            and pkt.directive_bytes_sha256 == handoff.directive_bytes_sha256
            and pkt.package_id == handoff.package_id
            and pkt.package_revision == handoff.package_revision
            and spec.provenance.evidence_type == pkt.provenance.directive_evidence_type,
            "LOCAL_COMPLETION_BINDING",
        )
        receiver_auth = self._pulse_receiver_authorization(
            grant,
            attempt=handoff.attempt,
            packet_id=handoff.packet_id,
            packet_sha256=handoff.packet_sha256,
            logical_identity=handoff.logical_identity,
        )
        attended_worker._autonomous_inputs(
            authorization=receiver_auth,
            packet_path=handoff.packet_ref,
            a04_receipt_path=handoff.a04_receipt_ref,
            directive_path=handoff.directive_ref,
            assets=handoff.assets,
            rights=handoff.rights,
            asset_root=handoff.asset_root,
            source_sha=handoff.source_sha,
            package_path=handoff.package_ref,
            a03_result_path=handoff.a03_result_ref,
        )
        return packet_raw

    def _invoke_a03(self, completed: ProductionStageCompleted, raw: bytes) -> dict:
        """Executable P1 adapter: exact bytes -> existing A03 intake -> store."""
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

    def _validate_a04_receipt(
        self, completed: ProductionStageCompleted, receipt: object,
    ) -> media_generation.A04CreativeHandoffReceipt:
        spec = completed.spec
        handoff = spec.package_handoff
        require(type(receipt) is media_generation.A04CreativeHandoffReceipt, "RECEIVER_OUTPUT_MISMATCH")
        checked = media_generation.A04CreativeHandoffReceipt.from_dict(receipt.to_dict())
        require(checked == receipt
                and checked.consumer_id == spec.consumer_id
                and checked.status == "SOURCE_HANDOFF_VERIFIED"
                and checked.media_execution == "NOT_ATTEMPTED"
                and checked.factual_status == "UNVERIFIED"
                and checked.semantic_approval == "NOT_ATTEMPTED"
                and checked.qa_status == "UNVERIFIED"
                and checked.pulse_execution == "NOT_ATTEMPTED"
                and checked.rights_status in ("UNKNOWN", "EVIDENCE_BOUND")
                and checked.journey_id == handoff.journey_id
                and checked.cycle_id == handoff.cycle_id
                and checked.job_id == handoff.job_id
                and checked.attempt == handoff.attempt
                and checked.actor_ref == handoff.actor_ref
                and checked.source_sha == handoff.source_sha == self._source_sha
                and checked.a03_result_ref == handoff.a03_result_ref
                and checked.a03_result_digest == handoff.a03_result_digest
                and checked.package_id == handoff.package_id
                and checked.revision == handoff.revision
                and checked.parent_package_digest == handoff.parent_package_digest
                and checked.package_ref == handoff.package_ref
                and checked.package_digest == handoff.package_bytes_sha256 == spec.output_sha256,
                "RECEIVER_OUTPUT_MISMATCH")
        return checked

    def _invoke_a04(self, completed: ProductionStageCompleted, raw: bytes) -> dict:
        """Executable R4 adapter: exact A03 package -> registered A04 source-only intake."""
        spec = completed.spec
        handoff = spec.package_handoff
        require(type(raw) is bytes and bytes_digest(raw) == handoff.package_bytes_sha256,
                "RECEIVER_INPUT_MISMATCH")
        receipt_dir = local_path(self._state_dir / "a04-handoffs")
        receipt = media_generation.accept_creative_production_package(
            creative_store_root=handoff.creative_store_root,
            package_id=handoff.package_id,
            revision=handoff.revision,
            expected_package_digest=handoff.package_bytes_sha256,
            a03_result_ref=handoff.a03_result_ref,
            expected_result_digest=handoff.a03_result_digest,
            source_sha=handoff.source_sha,
            journey_id=handoff.journey_id,
            cycle_id=handoff.cycle_id,
            job_id=handoff.job_id,
            attempt=handoff.attempt,
            actor_ref=handoff.actor_ref,
            receipt_dir=receipt_dir,
        )
        checked = self._validate_a04_receipt(completed, receipt)
        artifact = local_path(media_generation.a04_handoff_receipt_path(receipt_dir, checked.receipt_id))
        require(artifact.parent == receipt_dir
                and bytes_digest(read_local(artifact, limit=131072)) == checked.digest
                and media_generation.load_a04_handoff_receipt(artifact) == checked,
                "RECEIVER_OUTPUT_MISMATCH")
        return {
            "schema_version": "A04LocalInvocation.v1",
            "completed_sha256": completed.sha256,
            "consumer_id": spec.consumer_id,
            "execution_ref": "local:a04:" + spec.idempotency_key,
            "input_ref": handoff.package_ref,
            "input_bytes_sha256": bytes_digest(raw),
            "output_sha256": handoff.package_bytes_sha256,
            "artifact_ref": str(artifact),
            "receiver_receipt_id": checked.receipt_id,
            "receiver_receipt_digest": checked.digest,
            "media_execution": "NOT_ATTEMPTED",
            "handoff_status": "SOURCE_HANDOFF_VERIFIED",
            "source_sha": self._source_sha,
            "outcome": "SUCCESS",
        }

    def _pulse_receiver_dirs(self, key: str) -> tuple[Path, Path]:
        private_dir = local_path(self._state_dir / "pulse-runtime" / key[:32])
        export_dir = local_path(self._state_dir / "pulse-exports" / key[:32])
        return private_dir, export_dir

    def _validate_pulse_receipt_summary(
        self, completed: ProductionStageCompleted, summary: object, auth: A04PulseReceiverAuthorization
    ) -> dict:
        spec = completed.spec
        handoff = spec.pulse_handoff
        require(type(summary) is dict, "RECEIVER_OUTPUT_MISMATCH")
        require(
            summary.get("schema_version") == "A04PulseReceiverReceipt.v1"
            and summary.get("intake_mode") == "AUTONOMOUS_OFFLINE_LOCAL"
            and summary.get("human_attended") is False
            and summary.get("state") == "WAIT_BRAIN"
            and summary.get("code") == "WAIT_BRAIN"
            and summary.get("journey_id") == handoff.journey_id
            and summary.get("source_sha") == handoff.source_sha == self._source_sha
            and summary.get("packet_id") == handoff.packet_id
            and summary.get("packet_sha256") == handoff.packet_sha256
            and summary.get("logical_identity") == handoff.logical_identity
            and summary.get("a04_receipt_id") == handoff.a04_receipt_id
            and summary.get("a04_receipt_digest") == handoff.a04_receipt_digest
            and summary.get("authorization_ref") == auth.authorization_ref
            and summary.get("authorization_sha256") == auth.digest
            and summary.get("journal_sequence") == 4
            and type(summary.get("journal_id")) is str
            and type(summary.get("journal_checkpoint")) is dict
            and type(summary.get("artifact_sha256")) is str
            and type(summary.get("q4_qa_ref")) is str
            and type(summary.get("q4_qa_sha256")) is str
            and type(summary.get("receipt_sha256")) is str
            and type(summary.get("receipt_file_sha256")) is str
            and type(summary.get("evidence_packet_sha256")) is str
            and type(summary.get("evidence_file_sha256")) is str
            and summary.get("brain_decision") is None
            and summary.get("provider_network_calls") == 0
            and summary.get("paid_generation_calls") == 0
            and summary.get("publish_calls") == 0,
            "RECEIVER_OUTPUT_MISMATCH",
        )
        return summary

    def _invoke_pulse(self, completed: ProductionStageCompleted, raw: bytes) -> dict:
        """Executable W2 adapter: exact A04PulseOfflineHandoffPacket -> B0.3/B1.5 offline PULSE receiver."""
        spec = completed.spec
        handoff = spec.pulse_handoff
        grant = self._grant()
        require(type(raw) is bytes and bytes_digest(raw) == handoff.packet_bytes_sha256,
                "RECEIVER_INPUT_MISMATCH")
        private_dir, export_dir = self._pulse_receiver_dirs(spec.idempotency_key)
        private_dir.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)
        receiver_auth = self._pulse_receiver_authorization(
            grant,
            attempt=handoff.attempt,
            packet_id=handoff.packet_id,
            packet_sha256=handoff.packet_sha256,
            logical_identity=handoff.logical_identity,
        )
        common_kwargs = {
            "authorization": receiver_auth,
            "packet_path": handoff.packet_ref,
            "a04_receipt_path": handoff.a04_receipt_ref,
            "directive_path": handoff.directive_ref,
            "assets": handoff.assets,
            "rights": handoff.rights,
            "asset_root": handoff.asset_root,
            "source_sha": handoff.source_sha,
            "package_path": handoff.package_ref,
            "a03_result_path": handoff.a03_result_ref,
        }
        received = attended_worker.receive_a04_pulse_handoff(
            private_dir=str(private_dir),
            export_dir=str(export_dir),
            **common_kwargs,
        )
        checked = self._validate_pulse_receipt_summary(completed, received, receiver_auth)
        readback = attended_worker.inspect_a04_pulse_handoff(
            private_dir=str(private_dir),
            export_dir=str(export_dir),
            **common_kwargs,
        )
        require(readback == checked, "RECEIVER_OUTPUT_MISMATCH")
        receipt_artifact = local_path(export_dir / "ProductJourneyReceipt.v1.json")
        evidence_artifact = local_path(export_dir / "autonomous-offline-evidence.json")
        return {
            "schema_version": "A04PulseLocalInvocation.v1",
            "completed_sha256": completed.sha256,
            "consumer_id": spec.consumer_id,
            "execution_ref": "local:pulse:" + spec.idempotency_key,
            "input_ref": handoff.packet_ref,
            "input_bytes_sha256": bytes_digest(raw),
            "output_sha256": handoff.packet_bytes_sha256,
            "artifact_ref": str(receipt_artifact),
            "evidence_ref": str(evidence_artifact),
            "private_dir": str(private_dir),
            "export_dir": str(export_dir),
            "packet_id": handoff.packet_id,
            "logical_identity": handoff.logical_identity,
            "a04_receipt_id": handoff.a04_receipt_id,
            "journal_id": checked["journal_id"],
            "journal_checkpoint": checked["journal_checkpoint"],
            "media_artifact_sha256": checked["artifact_sha256"],
            "q4_qa_ref": checked["q4_qa_ref"],
            "q4_qa_sha256": checked["q4_qa_sha256"],
            "receiver_receipt_sha256": checked["receipt_sha256"],
            "receiver_receipt_file_sha256": checked["receipt_file_sha256"],
            "receiver_evidence_sha256": checked["evidence_packet_sha256"],
            "receiver_evidence_file_sha256": checked["evidence_file_sha256"],
            "state": "WAIT_BRAIN",
            "intake_mode": "AUTONOMOUS_OFFLINE_LOCAL",
            "human_attended": False,
            "source_sha": self._source_sha,
            "outcome": "SUCCESS",
        }

    def _finish_local(self, db, row, completed: ProductionStageCompleted) -> DispatchResult:
        """Reconcile only a committed native witness, never a supplied receipt."""
        self._authorize_completed(completed)
        self._verified_input(completed)
        witness = json.loads(row["witness"])
        spec = completed.spec
        receiver_receipt = None
        if spec.local_handoff is not None:
            artifact = str(local_path(self._state_dir / "creative" / "briefs" / (spec.output_sha256 + ".json")))
            expected = {"schema_version": "A03LocalInvocation.v1", "completed_sha256": completed.sha256,
                "consumer_id": spec.consumer_id, "execution_ref": "local:a03:" + spec.idempotency_key,
                "input_ref": spec.local_handoff.brief_ref, "input_bytes_sha256": spec.local_handoff.brief_bytes_sha256,
                "output_sha256": spec.output_sha256, "artifact_ref": artifact,
                "source_sha": self._source_sha, "outcome": "SUCCESS"}
            require(witness == expected and bytes_digest(read_local(artifact)) == spec.output_sha256,
                    "INVOCATION_WITNESS_MISMATCH")
        elif spec.package_handoff is not None:
            handoff = spec.package_handoff
            require(type(witness) is dict and type(witness.get("artifact_ref")) is str
                    and type(witness.get("receiver_receipt_id")) is str, "INVOCATION_WITNESS_MISMATCH")
            receipt_dir = local_path(self._state_dir / "a04-handoffs")
            try:
                expected_artifact = local_path(
                    media_generation.a04_handoff_receipt_path(receipt_dir, witness["receiver_receipt_id"])
                )
                artifact_path = local_path(witness["artifact_ref"])
                require(artifact_path == expected_artifact and artifact_path.parent == receipt_dir,
                        "INVOCATION_WITNESS_MISMATCH")
                raw_receipt = read_local(artifact_path, limit=131072)
                loaded_receipt = media_generation.load_a04_handoff_receipt(artifact_path)
                loaded_receipt = self._validate_a04_receipt(completed, loaded_receipt)
                sidecars = CreativeSidecarStore(local_path(handoff.creative_store_root))
                origin, verified_pkg, _ = sidecars.verify_production_package_lineage(
                    handoff.package_id, handoff.revision, origin_result_digest=handoff.a03_result_digest,
                )
                require(loaded_receipt.job_actor_ref == origin.job.provenance.actor_ref
                        and loaded_receipt.brief_id == verified_pkg.brief_id
                        and loaded_receipt.source_brief_digest == verified_pkg.source_brief_digest
                        and loaded_receipt.selection_id == verified_pkg.selected_concept.selection_id
                        and loaded_receipt.concept_id == verified_pkg.selected_concept.concept_id
                        and loaded_receipt.concept_revision == verified_pkg.selected_concept.revision
                        and loaded_receipt.concept_digest == verified_pkg.selected_concept.concept_digest
                        and loaded_receipt.cycle_job_digest == verified_pkg.lineage.cycle_job_digest
                        and loaded_receipt.cycle_result_digest == verified_pkg.lineage.cycle_result_digest
                        and loaded_receipt.reasoning_receipt_digest == verified_pkg.reasoning_receipt_digest
                        and loaded_receipt.audio_mode == verified_pkg.spec.audio.mode
                        and loaded_receipt.scene_count == len(verified_pkg.spec.scenes)
                        and loaded_receipt.media_target_count == len(verified_pkg.spec.media_targets)
                        and loaded_receipt.rights_status == verified_pkg.rights_status,
                        "INVOCATION_WITNESS_MISMATCH")
            except OrchestrationError:
                raise
            except (ValueError, TypeError, KeyError, OSError):
                raise OrchestrationError("INVOCATION_WITNESS_MISMATCH") from None
            artifact = str(artifact_path)
            expected = {
                "schema_version": "A04LocalInvocation.v1",
                "completed_sha256": completed.sha256,
                "consumer_id": spec.consumer_id,
                "execution_ref": "local:a04:" + spec.idempotency_key,
                "input_ref": handoff.package_ref,
                "input_bytes_sha256": handoff.package_bytes_sha256,
                "output_sha256": spec.output_sha256,
                "artifact_ref": artifact,
                "receiver_receipt_id": loaded_receipt.receipt_id,
                "receiver_receipt_digest": loaded_receipt.digest,
                "media_execution": "NOT_ATTEMPTED",
                "handoff_status": "SOURCE_HANDOFF_VERIFIED",
                "source_sha": self._source_sha,
                "outcome": "SUCCESS",
            }
            require(witness == expected and bytes_digest(raw_receipt) == loaded_receipt.digest,
                    "INVOCATION_WITNESS_MISMATCH")
            receiver_receipt = loaded_receipt
        else:
            handoff = spec.pulse_handoff
            grant = self._grant()
            require(handoff is not None and type(witness) is dict, "INVOCATION_WITNESS_MISMATCH")
            private_dir, export_dir = self._pulse_receiver_dirs(spec.idempotency_key)
            expected_receipt_path = local_path(export_dir / "ProductJourneyReceipt.v1.json")
            expected_evidence_path = local_path(export_dir / "autonomous-offline-evidence.json")
            receiver_auth = self._pulse_receiver_authorization(
                grant,
                attempt=handoff.attempt,
                packet_id=handoff.packet_id,
                packet_sha256=handoff.packet_sha256,
                logical_identity=handoff.logical_identity,
            )
            try:
                require(
                    witness.get("private_dir") == str(private_dir)
                    and witness.get("export_dir") == str(export_dir)
                    and witness.get("artifact_ref") == str(expected_receipt_path)
                    and witness.get("evidence_ref") == str(expected_evidence_path),
                    "INVOCATION_WITNESS_MISMATCH",
                )
                readback = attended_worker.inspect_a04_pulse_handoff(
                    private_dir=str(private_dir),
                    export_dir=str(export_dir),
                    authorization=receiver_auth,
                    packet_path=handoff.packet_ref,
                    a04_receipt_path=handoff.a04_receipt_ref,
                    directive_path=handoff.directive_ref,
                    assets=handoff.assets,
                    rights=handoff.rights,
                    asset_root=handoff.asset_root,
                    source_sha=handoff.source_sha,
                    package_path=handoff.package_ref,
                    a03_result_path=handoff.a03_result_ref,
                )
                checked = self._validate_pulse_receipt_summary(completed, readback, receiver_auth)
                receipt_raw = read_local(expected_receipt_path, limit=131072)
                evidence_raw = read_local(expected_evidence_path, limit=131072)
                parsed_receipt = parse_pulse_contract("ProductJourneyReceipt", receipt_raw).to_dict()
                parsed_evidence = runtime_receipt.parse_autonomous_evidence_packet(evidence_raw)
                require(
                    parsed_receipt["state"] == "WAIT_BRAIN"
                    and parsed_receipt["brain_decision"] is None
                    and parsed_receipt["effect"]["status"] == "NONE"
                    and parsed_receipt["network_calls_performed"] is False
                    and parsed_receipt["external_effects_performed"] is False
                    and parsed_receipt["journey_id"] == handoff.journey_id
                    and parsed_receipt["source_sha"] == handoff.source_sha
                    and parsed_receipt["receipt_sha256"] == checked["receipt_sha256"]
                    and bytes_digest(receipt_raw) == checked["receipt_file_sha256"]
                    and parsed_evidence["evidence_sha256"] == checked["evidence_packet_sha256"]
                    and bytes_digest(evidence_raw) == checked["evidence_file_sha256"]
                    and parsed_evidence["human_attended"] is False
                    and parsed_evidence["q4_technical_qa"]["qa_ref"] == checked["q4_qa_ref"]
                    and parsed_evidence["q4_technical_qa"]["qa_sha256"] == checked["q4_qa_sha256"],
                    "INVOCATION_WITNESS_MISMATCH",
                )
            except OrchestrationError:
                raise
            except (ValueError, TypeError, KeyError, OSError):
                raise OrchestrationError("INVOCATION_WITNESS_MISMATCH") from None
            artifact = str(expected_receipt_path)
            expected = {
                "schema_version": "A04PulseLocalInvocation.v1",
                "completed_sha256": completed.sha256,
                "consumer_id": spec.consumer_id,
                "execution_ref": "local:pulse:" + spec.idempotency_key,
                "input_ref": handoff.packet_ref,
                "input_bytes_sha256": handoff.packet_bytes_sha256,
                "output_sha256": spec.output_sha256,
                "artifact_ref": artifact,
                "evidence_ref": str(expected_evidence_path),
                "private_dir": str(private_dir),
                "export_dir": str(export_dir),
                "packet_id": handoff.packet_id,
                "logical_identity": handoff.logical_identity,
                "a04_receipt_id": handoff.a04_receipt_id,
                "journal_id": checked["journal_id"],
                "journal_checkpoint": checked["journal_checkpoint"],
                "media_artifact_sha256": checked["artifact_sha256"],
                "q4_qa_ref": checked["q4_qa_ref"],
                "q4_qa_sha256": checked["q4_qa_sha256"],
                "receiver_receipt_sha256": checked["receipt_sha256"],
                "receiver_receipt_file_sha256": checked["receipt_file_sha256"],
                "receiver_evidence_sha256": checked["evidence_packet_sha256"],
                "receiver_evidence_file_sha256": checked["evidence_file_sha256"],
                "state": "WAIT_BRAIN",
                "intake_mode": "AUTONOMOUS_OFFLINE_LOCAL",
                "human_attended": False,
                "source_sha": self._source_sha,
                "outcome": "SUCCESS",
            }
            require(witness == expected, "INVOCATION_WITNESS_MISMATCH")
            receiver_receipt = checked
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
        return DispatchResult(status="SUCCESS", accepted_consumer=spec.consumer_id, event=event,
                              completed=completed, receiver_receipt=receiver_receipt)

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
            is_a04 = completed.spec.package_handoff is not None
            is_pulse = completed.spec.pulse_handoff is not None
            prefix = "local:pulse:" if is_pulse else ("local:a04:" if is_a04 else "local:a03:")
            execution_ref = prefix + key
            db.execute("UPDATE outbox SET state='RUNNING',attempts=?,lease_until=? WHERE key=?",
                       (attempts, time.time() + self._lease_seconds, key))
            dispatched = ProductionStageDispatched(spec=completed.spec, completed_sha256=completed.sha256,
                receipt=None, receipt_sha256=None, phase="DISPATCHED", dispatch_ref=execution_ref)
            self._journal(db, key, dispatched.schema_version, dispatched.to_dict())
            self._journal(db, key, "ProductionDispatchAttempt.v1", {
                "attempt": attempts, "execution_ref": execution_ref,
                "completed_sha256": completed.sha256, "status": "RUNNING"})
        # Reservation is committed before entering the actual local function.
        try:
            self._authorize_completed(completed)
            if is_pulse:
                witness = self._invoke_pulse(completed, raw)
            elif is_a04:
                witness = self._invoke_a04(completed, raw)
            else:
                witness = self._invoke_a03(completed, raw)
        except Exception:
            # Once the dispatch reservation is committed, even a ValueError or
            # malformed receiver response can occur after the consumer wrote its
            # artifact. Only the pre-invocation validation above can mark a
            # deterministic FAILED; never assume no effect or blindly retry.
            with self._database() as db:
                db.execute("UPDATE outbox SET state='UNKNOWN',reason='RECEIVER_UNRESOLVED' WHERE key=?", (key,))
                self._journal(db, key, "UNKNOWN", {"reason": "RECEIVER_UNRESOLVED"})
            return DispatchResult(status="UNKNOWN", completed=completed, reason="RECEIVER_UNRESOLVED")
        witness_kind = (
            "A04PulseLocalInvocation.v1" if is_pulse
            else ("A04LocalInvocation.v1" if is_a04 else "A03LocalInvocation.v1")
        )
        with self._database() as db:
            db.execute("UPDATE outbox SET witness=? WHERE key=?",
                       (canonical_bytes(witness).decode("utf-8"), key))
            self._journal(db, key, witness_kind, witness)
        # Separate commit makes crash-after-invocation / before-receipt resumable.
        with self._database() as db:
            row = db.execute("SELECT * FROM outbox WHERE key=?", (key,)).fetchone()
            return self._finish_local(db, row, completed)

    def resume_local(self) -> tuple[DispatchResult, ...]:
        """Resume this caller's bounded journey/cycle; never run another grant."""
        grant = self._grant()
        if grant.route_id == "a10-m05-to-a03" and not local_path(self._state_dir / "production.sqlite").exists():
            return ()
        base_slot = self._base_slot(grant)
        with self._database() as db:
            if grant.route_id in ("a03-to-a04", "a04-to-pulse-offline"):
                keys = [row[0] for row in db.execute(
                    "SELECT key FROM outbox WHERE slot LIKE ? ORDER BY rowid", (base_slot + ":%",),
                )]
            else:
                keys = [row[0] for row in db.execute("SELECT key FROM outbox WHERE slot=?", (base_slot,))]
        return tuple(self.dispatch_local(key) for key in keys)

    def _brain_decision_cut(self, name: str) -> None:
        """Named crash-window cut for BrainDecision.v1 intake tests; no fault switches."""

    def _pulse_brain_decision_dir(self, key: str) -> Path:
        return local_path(self._state_dir / "pulse-brain-decisions" / key[:32])

    @staticmethod
    def _seal_brain_accepted_receipt(directive, accepted_snapshot):
        """Seal post-BRAIN_ACCEPTED B0.1 ProductJourneyReceipt.v1 without mutating B0.1 schema."""
        require(
            accepted_snapshot.state == PulseState.READY_FOR_HUMAN_RELEASE
            and accepted_snapshot.brain_decision is not None
            and accepted_snapshot.effect_status == "NONE",
            "BRAIN_ACCEPTED_SNAPSHOT_REQUIRED",
        )
        value = directive.to_dict()
        decision = accepted_snapshot.brain_decision.to_dict()
        fixture = any(p.evidence_type == "SYNTHETIC_FIXTURE" for p in accepted_snapshot.evidence)
        evidence_type = "SYNTHETIC_FIXTURE" if fixture else "HUMAN_SUPPLIED"
        actor_ref = "fixture:a04-pulse-offline" if fixture else "human:a04-pulse-offline"
        sealed = seal_pulse_contract("ProductJourneyReceipt", {
            "schema_version": "ProductJourneyReceipt.v1",
            **{k: value[k] for k in ("journey_id", "source_sha", "directive_version", "directive_sha256", "scope")},
            "state": "READY_FOR_HUMAN_RELEASE",
            "brain_decision": {"ref": decision["decision_id"], "sha256": decision["decision_sha256"]},
            "owner_evidence_refs": [asdict(p) for p in accepted_snapshot.evidence],
            "effect": {
                "status": "NONE",
                "idempotency_key": None,
                "request_sha256": None,
                "receipt": None,
            },
            "network_calls_performed": False,
            "external_effects_performed": False,
            "authorization": pulse_draft_authorization(),
            "provenance": {
                "evidence_type": evidence_type,
                "actor": "FIXTURE" if fixture else "HUMAN",
                "actor_ref": actor_ref,
                "source_ref": "directive:" + value["directive_sha256"],
                "source_sha": value["source_sha"],
            },
        })
        return parse_pulse_contract("ProductJourneyReceipt", sealed.raw)

    def _load_completed_pulse_stage(self, key: str) -> tuple[ProductionStageCompleted, dict, Path, Path]:
        grant = self._grant()
        require(grant.route_id == "a04-to-pulse-offline", "LOCAL_ROUTE_MISMATCH")
        require(type(key) is str and len(key) > 0, "COMPLETED_NOT_FOUND")
        with self._database(read_only=True) as db:
            row = db.execute("SELECT * FROM outbox WHERE key=?", (key,)).fetchone()
            require(row is not None, "COMPLETED_NOT_FOUND")
            completed = ProductionStageCompleted.from_dict(json.loads(row["completed"]))
            require(row["key"] == completed.spec.idempotency_key, "LOCAL_COMPLETION_BINDING")
            self._authorize_completed(completed)
            require(
                row["state"] == "SUCCESS"
                and row["witness"] is not None
                and row["dispatched"] is not None,
                "PULSE_WAIT_BRAIN_NOT_COMMITTED",
            )
            witness = json.loads(row["witness"])
        require(type(witness) is dict and witness.get("schema_version") == "A04PulseLocalInvocation.v1",
                "INVOCATION_WITNESS_MISMATCH")
        private_dir, export_dir = self._pulse_receiver_dirs(key)
        expected_receipt_path = local_path(export_dir / "ProductJourneyReceipt.v1.json")
        expected_evidence_path = local_path(export_dir / "autonomous-offline-evidence.json")
        require(
            witness.get("completed_sha256") == completed.sha256
            and witness.get("private_dir") == str(private_dir)
            and witness.get("export_dir") == str(export_dir)
            and witness.get("artifact_ref") == str(expected_receipt_path)
            and witness.get("evidence_ref") == str(expected_evidence_path),
            "INVOCATION_WITNESS_MISMATCH",
        )
        return completed, witness, private_dir, export_dir

    def _pulse_wait_brain_kwargs(self, completed: ProductionStageCompleted) -> dict:
        grant = self._grant()
        handoff = completed.spec.pulse_handoff
        require(handoff is not None, "LOCAL_ROUTE_MISMATCH")
        receiver_auth = self._pulse_receiver_authorization(
            grant,
            attempt=handoff.attempt,
            packet_id=handoff.packet_id,
            packet_sha256=handoff.packet_sha256,
            logical_identity=handoff.logical_identity,
        )
        return {
            "authorization": receiver_auth,
            "packet_path": handoff.packet_ref,
            "a04_receipt_path": handoff.a04_receipt_ref,
            "directive_path": handoff.directive_ref,
            "assets": handoff.assets,
            "rights": handoff.rights,
            "asset_root": handoff.asset_root,
            "source_sha": handoff.source_sha,
            "package_path": handoff.package_ref,
            "a03_result_path": handoff.a03_result_ref,
        }

    @staticmethod
    def _verify_request_matches_witness(request_data: dict, witness: dict, handoff: A04PulseHandoff) -> None:
        require(
            request_data["journey_id"] == handoff.journey_id
            and request_data["source_sha"] == handoff.source_sha
            and request_data["packet_id"] == handoff.packet_id == witness.get("packet_id")
            and request_data["packet_sha256"] == handoff.packet_sha256
            and request_data["logical_identity"] == handoff.logical_identity == witness.get("logical_identity")
            and request_data["a04_receipt_id"] == handoff.a04_receipt_id == witness.get("a04_receipt_id")
            and request_data["a04_receipt_digest"] == handoff.a04_receipt_digest
            and request_data["receipt_sha256"] == witness.get("receiver_receipt_sha256")
            and request_data["receipt_file_sha256"] == witness.get("receiver_receipt_file_sha256")
            and request_data["autonomous_evidence_sha256"] == witness.get("receiver_evidence_sha256")
            and request_data["autonomous_evidence_file_sha256"] == witness.get("receiver_evidence_file_sha256")
            and request_data["local_artifact"]["artifact_sha256"] == witness.get("media_artifact_sha256")
            and request_data["q4_technical_qa"]["qa_ref"] == witness.get("q4_qa_ref")
            and request_data["q4_technical_qa"]["qa_sha256"] == witness.get("q4_qa_sha256")
            and request_data["journal_checkpoint"] == witness.get("journal_checkpoint")
            and request_data["journal_checkpoint"]["journal_id"] == witness.get("journal_id"),
            "BRAIN_EVIDENCE_WITNESS_MISMATCH",
        )

    def build_pulse_brain_evidence_request(
        self,
        key: str,
        *,
        expected_generation: int | None = None,
    ) -> bytes:
        """Build a bounded, sanitized BrainReviewEvidenceRequest.v1 from a W2 WAIT_BRAIN stage."""
        completed, witness, private_dir, export_dir = self._load_completed_pulse_stage(key)
        common_kwargs = self._pulse_wait_brain_kwargs(completed)
        try:
            request_raw = attended_worker.build_a04_pulse_brain_evidence_request(
                private_dir=str(private_dir),
                export_dir=str(export_dir),
                expected_generation=expected_generation,
                **common_kwargs,
            )
            parsed = runtime_receipt.parse_brain_review_evidence_request(request_raw)
            self._verify_request_matches_witness(parsed, witness, completed.spec.pulse_handoff)
            return request_raw
        except OrchestrationError:
            raise
        except (ValueError, TypeError, KeyError, OSError):
            raise OrchestrationError("BRAIN_EVIDENCE_REQUEST_HOLD") from None

    def review_pulse_brain_decision(
        self, key: str, *, chat_ref: str, at: int | None = None,
        expected_fencing_generation: int | None = None,
    ) -> BrainDecisionIntakeResult:
        """Offer verified WAIT_BRAIN evidence once, then pass the decision to W3a intake."""
        bridge = self._pulse_brain_bridge
        if bridge is None:
            return self._brain_hold("BRAIN_CDP_BRIDGE_UNAVAILABLE")
        if type(chat_ref) is not str or not re.fullmatch(r"chat:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", chat_ref):
            return self._brain_hold("BRAIN_CHAT_MISMATCH")
        if at is not None and (type(at) is not int or not 0 <= at < 2**63 - 1):
            return self._brain_hold("EXPLICIT_TIME_REQUIRED")

        try:
            inspected = self.inspect_pulse_brain_decision(key)
        except OrchestrationError as exc:
            return self._brain_hold(str(exc))
        if inspected.accepted:
            if inspected.chat_ref != chat_ref:
                return self._brain_hold("BRAIN_CHAT_MISMATCH")
            return inspected
        if inspected.status != "NO_DECISION":
            return inspected

        try:
            evidence = self.build_pulse_brain_evidence_request(key)
            request = runtime_receipt.parse_brain_review_evidence_request(evidence)
        except (OrchestrationError, runtime_receipt.ReceiptHold):
            return self._brain_hold("BRAIN_EVIDENCE_REQUEST_HOLD")
        generation = request["journal_checkpoint"]["generation"]
        if expected_fencing_generation is not None and (
            type(expected_fencing_generation) is not int or expected_fencing_generation != generation
        ):
            return self._brain_hold("STALE_FENCING_GENERATION", live_request=request)

        intent = {"request_sha256": request["request_sha256"], "chat_ref": chat_ref}
        intent_raw = canonical_bytes(intent).decode("utf-8")

        def has_intent(db) -> bool:
            rows = db.execute("SELECT body FROM journal WHERE key=? AND kind=?",
                              (key, _PULSE_BRAIN_SEND_INTENT)).fetchall()
            require(not rows or len(rows) == 1 and rows[0]["body"] == intent_raw,
                    "BRAIN_CDP_SEND_INTENT_MISMATCH")
            return bool(rows)

        reserved_here = False

        def reserve_send() -> bool:
            nonlocal reserved_here
            with self._database() as db:
                if has_intent(db) or db.execute(
                    "SELECT 1 FROM journal WHERE key=? AND kind IN "
                    "('BrainDecisionIntakeReserved.v1', 'BrainDecisionAccepted.v1')", (key,),
                ).fetchone():
                    return False
                self._journal(db, key, _PULSE_BRAIN_SEND_INTENT, intent)
            reserved_here = True
            return True

        def unknown() -> BrainDecisionIntakeResult:
            return self._brain_hold("BRAIN_CDP_UNKNOWN_EFFECT", state="UNKNOWN_EFFECT", live_request=request)

        try:
            with self._database(read_only=True) as db:
                if has_intent(db):
                    return unknown()
            decision_input = bridge.request_decision(
                evidence_request=evidence, chat_ref=chat_ref, reserve_send=reserve_send,
            )
            if decision_input is None:
                with self._database(read_only=True) as db:
                    if has_intent(db):
                        return unknown()
                return self._brain_hold("BRAIN_CDP_DECISION_UNAVAILABLE", live_request=request)
        except OrchestrationError as exc:
            return self._brain_hold(str(exc), live_request=request)

        if not reserved_here or type(decision_input) is not LocalBrainDecisionInput:
            return self._brain_hold("BRAIN_CDP_DECISION_UNBOUND", live_request=request)
        if decision_input.chat_ref != chat_ref:
            return self._brain_hold("BRAIN_CHAT_MISMATCH", live_request=request, decision_input=decision_input)
        # The canonical request hash binds the decision to the verified Q4 and WAIT_BRAIN receipt.
        if decision_input.evidence_request_sha256 != request["request_sha256"]:
            return self._brain_hold("BRAIN_EVIDENCE_REQUEST_MISMATCH", live_request=request,
                                    decision_input=decision_input)
        return self.accept_pulse_brain_decision(
            key, decision_input=decision_input, evidence_request=evidence, at=at,
            expected_fencing_generation=generation,
        )

    @staticmethod
    def _validate_brain_journal_records(
        db,
        *,
        key: str,
        logical_identity: str,
        journey_id: str,
        completed_sha256: str,
    ) -> tuple[tuple[int, dict] | None, tuple[int, dict] | None, set[str], set[str]]:
        """Validate integrity of all local BrainDecision intake rows in production.sqlite."""
        rows = db.execute(
            "SELECT seq, key, kind, body FROM journal "
            "WHERE kind IN ('BrainDecisionIntakeReserved.v1', 'BrainDecisionAccepted.v1') "
            "ORDER BY seq"
        ).fetchall()
        reserved_by_seq: dict[int, tuple[str, dict]] = {}
        key_reserved: list[tuple[int, dict]] = []
        key_accepted: list[tuple[int, dict]] = []
        other_event_ids: set[str] = set()
        other_decision_ids: set[str] = set()

        reserved_fields = {
            "schema_version", "completed_sha256", "logical_identity", "journey_id",
            "source_sha", "directive_version", "directive_sha256",
            "wait_receipt_sha256", "wait_receipt_file_sha256",
            "artifact_sha256", "q4_qa_sha256",
            "fencing_generation", "lease_until",
            "decision_id", "decision_sha256", "decision_bytes_sha256", "event_id",
            "chat_ref", "actor_ref", "origin_ref", "origin_kind",
            "identity_authenticated", "live_cdp_proven",
            "evidence_request_sha256", "pulse_journal_checkpoint",
            "accepted_receipt_sha256", "accepted_receipt_file_sha256", "record_sha256",
        }
        accepted_fields = {
            "schema_version", "completed_sha256", "logical_identity", "journey_id",
            "source_sha", "directive_version", "directive_sha256",
            "wait_receipt_sha256", "wait_receipt_file_sha256",
            "artifact_sha256", "q4_qa_sha256",
            "reserved_seq", "reserved_record_sha256", "fencing_generation",
            "decision_id", "decision_sha256", "decision_bytes_sha256", "event_id",
            "chat_ref", "actor_ref", "origin_ref", "origin_kind",
            "identity_authenticated", "live_cdp_proven", "accepted_at",
            "evidence_request_sha256", "pulse_journal_checkpoint",
            "accepted_receipt_sha256", "accepted_receipt_file_sha256", "accepted_receipt_ref",
            "accepted_state", "record_sha256",
        }

        for row in rows:
            seq, row_key, kind, raw_body = row["seq"], row["key"], row["kind"], row["body"]
            try:
                body = json.loads(raw_body)
            except (ValueError, TypeError):
                raise OrchestrationError("BRAIN_JOURNAL_TAMPERED") from None
            require(type(body) is dict and raw_body == canonical_bytes(body).decode("utf-8"),
                    "BRAIN_JOURNAL_TAMPERED")
            expected_fields = reserved_fields if kind == "BrainDecisionIntakeReserved.v1" else accepted_fields
            require(set(body) == expected_fields and body.get("schema_version") == kind,
                    "BRAIN_JOURNAL_TAMPERED")
            body_no_hash = {k: v for k, v in body.items() if k != "record_sha256"}
            require(body.get("record_sha256") == digest(body_no_hash), "BRAIN_JOURNAL_TAMPERED")
            require(
                body["identity_authenticated"] is False
                and body["live_cdp_proven"] is False
                and body["origin_kind"] in {"LOCAL_CHATGPT_TRANSFER", "TEST_ONLY"},
                "BRAIN_JOURNAL_TAMPERED",
            )

            same_logical = (
                body.get("logical_identity") == logical_identity
                or body.get("journey_id") == journey_id
            )
            if row_key != key:
                require(not same_logical, "BRAIN_DECISION_CONFLICT")
                other_event_ids.add(body["event_id"])
                other_decision_ids.add(body["decision_id"])
                if kind == "BrainDecisionIntakeReserved.v1":
                    reserved_by_seq[seq] = (row_key, body)
                else:
                    ref_seq = body["reserved_seq"]
                    require(
                        ref_seq in reserved_by_seq
                        and reserved_by_seq[ref_seq][0] == row_key
                        and reserved_by_seq[ref_seq][1]["record_sha256"] == body["reserved_record_sha256"],
                        "BRAIN_JOURNAL_TAMPERED",
                    )
                continue

            require(
                body["completed_sha256"] == completed_sha256
                and body["logical_identity"] == logical_identity
                and body["journey_id"] == journey_id,
                "BRAIN_JOURNAL_TAMPERED",
            )
            if kind == "BrainDecisionIntakeReserved.v1":
                reserved_by_seq[seq] = (row_key, body)
                key_reserved.append((seq, body))
            else:
                require(body["accepted_state"] == "READY_FOR_HUMAN_RELEASE", "BRAIN_JOURNAL_TAMPERED")
                ref_seq = body["reserved_seq"]
                require(
                    ref_seq in reserved_by_seq
                    and reserved_by_seq[ref_seq][0] == key
                    and reserved_by_seq[ref_seq][1]["record_sha256"] == body["reserved_record_sha256"]
                    and reserved_by_seq[ref_seq][1]["decision_sha256"] == body["decision_sha256"]
                    and reserved_by_seq[ref_seq][1]["decision_bytes_sha256"] == body["decision_bytes_sha256"]
                    and reserved_by_seq[ref_seq][1]["decision_id"] == body["decision_id"]
                    and reserved_by_seq[ref_seq][1]["event_id"] == body["event_id"]
                    and reserved_by_seq[ref_seq][1]["origin_ref"] == body["origin_ref"]
                    and reserved_by_seq[ref_seq][1]["origin_kind"] == body["origin_kind"],
                    "BRAIN_JOURNAL_TAMPERED",
                )
                key_accepted.append((seq, body))

        require(len(key_accepted) <= 1, "BRAIN_JOURNAL_TAMPERED")
        require(len(key_reserved) <= 1, "BRAIN_JOURNAL_TAMPERED")
        return (
            key_reserved[0] if key_reserved else None,
            key_accepted[0] if key_accepted else None,
            other_event_ids,
            other_decision_ids,
        )

    @staticmethod
    def _brain_hold(
        reason: str,
        *,
        status: Literal["NO_DECISION", "HOLD"] = "HOLD",
        state: Literal["WAIT_BRAIN", "UNKNOWN_EFFECT", "HOLD"] = "WAIT_BRAIN",
        live_request: dict | None = None,
        decision_input: LocalBrainDecisionInput | None = None,
    ) -> BrainDecisionIntakeResult:
        origin_kind: Literal["NONE", "LOCAL_CHATGPT_TRANSFER", "TEST_ONLY"] = (
            decision_input.origin_kind if type(decision_input) is LocalBrainDecisionInput else "NONE"
        )
        origin_ref = (
            decision_input.origin_ref if type(decision_input) is LocalBrainDecisionInput else None
        )
        return BrainDecisionIntakeResult(
            status=status,
            state=state,
            reason=reason,
            accepted=False,
            idempotent_replay=False,
            journey_id=live_request["journey_id"] if live_request else None,
            source_sha=live_request["source_sha"] if live_request else None,
            logical_identity=live_request["logical_identity"] if live_request else None,
            directive_sha256=live_request["directive_sha256"] if live_request else None,
            evidence_request_sha256=live_request["request_sha256"] if live_request else None,
            artifact_sha256=live_request["local_artifact"]["artifact_sha256"] if live_request else None,
            q4_qa_sha256=live_request["q4_technical_qa"]["qa_sha256"] if live_request else None,
            origin_ref=origin_ref,
            origin_kind=origin_kind,
            pulse_journal_checkpoint=dict(live_request["journal_checkpoint"]) if live_request else None,
        )

    @staticmethod
    def _brain_accepted_result(
        acc: dict,
        *,
        journal_seq: int,
        reason: str,
        idempotent_replay: bool,
    ) -> BrainDecisionIntakeResult:
        return BrainDecisionIntakeResult(
            status="BRAIN_ACCEPTED",
            state="READY_FOR_HUMAN_RELEASE",
            reason=reason,
            accepted=True,
            idempotent_replay=idempotent_replay,
            journey_id=acc["journey_id"],
            source_sha=acc["source_sha"],
            logical_identity=acc["logical_identity"],
            directive_sha256=acc["directive_sha256"],
            evidence_request_sha256=acc["evidence_request_sha256"],
            artifact_sha256=acc["artifact_sha256"],
            q4_qa_sha256=acc["q4_qa_sha256"],
            decision_id=acc["decision_id"],
            decision_sha256=acc["decision_sha256"],
            decision_bytes_sha256=acc["decision_bytes_sha256"],
            chat_ref=acc["chat_ref"],
            event_id=acc["event_id"],
            origin_ref=acc["origin_ref"],
            origin_kind=acc["origin_kind"],
            accepted_receipt_sha256=acc["accepted_receipt_sha256"],
            accepted_receipt_file_sha256=acc["accepted_receipt_file_sha256"],
            accepted_receipt_ref=acc["accepted_receipt_ref"],
            journal_seq=journal_seq,
            pulse_journal_checkpoint=dict(acc["pulse_journal_checkpoint"]),
        )

    def accept_pulse_brain_decision(
        self,
        key: str,
        *,
        decision_input: LocalBrainDecisionInput | None = None,
        evidence_request: bytes | None = None,
        event_id: str | None = None,
        at: int | None = None,
        expected_fencing_generation: int | None = None,
        reconcile_fencing_generation: int | None = None,
    ) -> BrainDecisionIntakeResult:
        """Bounded local single-owner W3a BrainDecision.v1 intake for a W2 WAIT_BRAIN journey.

        Preserves B0.1 state_machine BRAIN_ACCEPTED transition (reaching
        READY_FOR_HUMAN_RELEASE as a state name only), immutable readback of the
        original W2 ProductJourneyReceipt.v1 and Q4/MP4 hashes, deterministic
        dedup, atomic SQLite journal+receipt persistence, and crash fencing.

        When no local ChatGPT Brain decision has been supplied (`decision_input=None`),
        returns an honest NO_DECISION / WAIT_BRAIN result with zero mutation.
        Labels are informational only and never assert private cryptographic
        verification or live CDP operation.
        """
        grant = self._grant()
        require(grant.route_id == "a04-to-pulse-offline", "LOCAL_ROUTE_MISMATCH")

        try:
            completed, witness, private_dir, export_dir = self._load_completed_pulse_stage(key)
        except OrchestrationError as exc:
            if str(exc) in {"LOCAL_AUTHORIZATION_REQUIRED", "SOURCE_SHA_MISMATCH", "STATE_DIR_REQUIRED",
                            "LOCAL_ROUTE_MISMATCH", "ROUTE_NOT_AUTHORIZED"}:
                raise
            return self._brain_hold("BRAIN_JOURNAL_OR_SOURCE_TAMPERED")

        handoff = completed.spec.pulse_handoff
        common_kwargs = self._pulse_wait_brain_kwargs(completed)
        try:
            directive, recovery, _, _, live_request_raw = attended_worker.load_a04_pulse_wait_brain_snapshot(
                private_dir=str(private_dir),
                export_dir=str(export_dir),
                **common_kwargs,
            )
            live_request = runtime_receipt.parse_brain_review_evidence_request(live_request_raw)
            self._verify_request_matches_witness(live_request, witness, handoff)
        except attended_worker.WorkerHold as exc:
            if str(exc) == "UNKNOWN_EFFECT":
                return self._brain_hold("BRAIN_UNKNOWN_EFFECT", state="UNKNOWN_EFFECT")
            return self._brain_hold("BRAIN_JOURNAL_OR_SOURCE_TAMPERED")
        except (OrchestrationError, runtime_receipt.ReceiptHold, ValueError, TypeError, KeyError, OSError):
            return self._brain_hold("BRAIN_JOURNAL_OR_SOURCE_TAMPERED")

        if evidence_request is not None:
            if type(evidence_request) is not bytes or evidence_request != live_request_raw:
                return self._brain_hold(
                    "BRAIN_EVIDENCE_REQUEST_MISMATCH",
                    live_request=live_request,
                    decision_input=decision_input if type(decision_input) is LocalBrainDecisionInput else None,
                )

        # Verify journal integrity before returning NO_DECISION so tampering is never masked.
        try:
            with self._database(read_only=True) as db:
                pre_reserved, pre_accepted, _, _ = self._validate_brain_journal_records(
                    db,
                    key=key,
                    logical_identity=handoff.logical_identity,
                    journey_id=live_request["journey_id"],
                    completed_sha256=completed.sha256,
                )
        except OrchestrationError as exc:
            return self._brain_hold(
                str(exc),
                live_request=live_request,
                decision_input=decision_input if type(decision_input) is LocalBrainDecisionInput else None,
            )

        if decision_input is None:
            if pre_accepted is not None:
                return self._brain_hold(
                    "BRAIN_DECISION_ALREADY_ACCEPTED",
                    state="HOLD",
                    live_request=live_request,
                )
            if pre_reserved is not None:
                return self._brain_hold("BRAIN_INTERRUPTED_COMMIT_HOLD", live_request=live_request)
            return self._brain_hold(
                "WAIT_BRAIN_NO_DECISION",
                status="NO_DECISION",
                state="WAIT_BRAIN",
                live_request=live_request,
            )

        if type(decision_input) is not LocalBrainDecisionInput:
            return self._brain_hold("BRAIN_DECISION_INPUT_INVALID", live_request=live_request)

        now_at = int(time.time()) if at is None else at
        if type(now_at) is not int or not (0 <= now_at < 2**63 - 1):
            return self._brain_hold("EXPLICIT_TIME_REQUIRED", live_request=live_request, decision_input=decision_input)

        if (
            decision_input.evidence_request_sha256 is not None
            and decision_input.evidence_request_sha256 != live_request["request_sha256"]
        ):
            return self._brain_hold(
                "BRAIN_EVIDENCE_REQUEST_MISMATCH",
                live_request=live_request,
                decision_input=decision_input,
            )

        decision_bytes = decision_input.decision_bytes
        try:
            decision_contract = parse_pulse_contract("BrainDecision", decision_bytes)
        except ContractError:
            return self._brain_hold("BRAIN_DECISION_INVALID", live_request=live_request, decision_input=decision_input)
        if decision_bytes != decision_contract.raw:
            return self._brain_hold(
                "BRAIN_DECISION_BYTES_MISMATCH",
                live_request=live_request,
                decision_input=decision_input,
            )
        decision_bytes_sha256 = bytes_digest(decision_bytes)

        decision = decision_contract.to_dict()
        ckpt = live_request["journal_checkpoint"]
        if decision["schema_version"] != "BrainDecision.v1":
            return self._brain_hold("BRAIN_VERSION_MISMATCH", live_request=live_request, decision_input=decision_input)
        if decision["source_sha"] != live_request["source_sha"] or decision["source_sha"] != grant.source_sha:
            return self._brain_hold("BRAIN_SOURCE_MISMATCH", live_request=live_request, decision_input=decision_input)
        if decision["journey_id"] != live_request["journey_id"] or decision["journey_id"] != grant.journey_id:
            return self._brain_hold("BRAIN_JOURNEY_MISMATCH", live_request=live_request, decision_input=decision_input)
        if decision["directive_version"] != live_request["directive_version"]:
            return self._brain_hold("BRAIN_VERSION_MISMATCH", live_request=live_request, decision_input=decision_input)
        if decision["directive_sha256"] != live_request["directive_sha256"]:
            return self._brain_hold(
                "BRAIN_DIRECTIVE_MISMATCH",
                live_request=live_request,
                decision_input=decision_input,
            )
        if decision["scope"] != live_request["scope"] or decision["review_scope"] != "DIRECTIVE_ONLY":
            return self._brain_hold("BRAIN_SCOPE_MISMATCH", live_request=live_request, decision_input=decision_input)
        if decision["channel"] != "CURRENT_CHATGPT_CHAT":
            return self._brain_hold("BRAIN_CHANNEL_MISMATCH", live_request=live_request, decision_input=decision_input)
        if decision["receipt_ref"] != live_request["receipt_ref"]:
            return self._brain_hold("BRAIN_RECEIPT_MISMATCH", live_request=live_request, decision_input=decision_input)
        if decision_input.chat_ref is not None and decision_input.chat_ref != decision["chat_ref"]:
            return self._brain_hold("BRAIN_CHAT_MISMATCH", live_request=live_request, decision_input=decision_input)
        if (
            decision["provenance"]["actor"] != "CHATGPT_BRAIN"
            or decision["provenance"]["evidence_type"] != "HUMAN_SUPPLIED"
        ):
            return self._brain_hold("BRAIN_ACTOR_MISMATCH", live_request=live_request, decision_input=decision_input)
        if decision["decision"] != "APPROVE_DIRECTIVE":
            return self._brain_hold(
                "BRAIN_DECISION_NOT_APPROVED",
                live_request=live_request,
                decision_input=decision_input,
            )

        resolved_event_id = (
            "event:brain-accepted-" + decision["decision_sha256"][:32]
            if event_id is None else event_id
        )
        if (
            type(resolved_event_id) is not str
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", resolved_event_id) is None
        ):
            return self._brain_hold("INVALID_EVENT_ID", live_request=live_request, decision_input=decision_input)

        # Drive the unchanged frozen B0.1 pure reducer with an ephemeral local-owner
        # adapter input before any persistence. This never claims private verifier auth.
        try:
            local_bridge_receipt = AuthorityReceipt(
                kind=AuthorityKind.BRAIN,
                journey_id=live_request["journey_id"],
                source_sha=live_request["source_sha"],
                directive_sha256=live_request["directive_sha256"],
                scope_sha256=live_request["scope_sha256"],
                record_sha256=decision["decision_sha256"],
                actor_ref=decision["provenance"]["actor_ref"],
                channel="CURRENT_CHATGPT_CHAT",
                actions=("APPROVE_DIRECTIVE",),
                verifier_ref="local-owner:single-owner-bridge",
                valid_from=now_at,
                expires_at=now_at + 1,
                evidence_type="HUMAN_SUPPLIED",
                verified=True,
            )
            local_bridge_ctx = VerificationContext(
                receipts=(local_bridge_receipt,),
                current_chat_ref=decision["chat_ref"],
            )
            pulse_event = PulseEvent(
                resolved_event_id,
                PulseEventKind.BRAIN_ACCEPTED,
                live_request["journey_id"],
                live_request["source_sha"],
                record=decision_contract,
            )
            accepted_snapshot = pulse_transition(
                recovery.snapshot,
                pulse_event,
                at=now_at,
                verification=local_bridge_ctx,
            )
            accepted_receipt = self._seal_brain_accepted_receipt(directive, accepted_snapshot)
        except (TransitionError, ContractError, OrchestrationError):
            return self._brain_hold(
                "BRAIN_TRANSITION_REJECTED",
                live_request=live_request,
                decision_input=decision_input,
            )

        accepted_receipt_dict = accepted_receipt.to_dict()
        accepted_receipt_sha256 = accepted_receipt_dict["receipt_sha256"]
        accepted_receipt_file_sha256 = bytes_digest(accepted_receipt.raw)

        decision_dir = self._pulse_brain_decision_dir(key)
        decision_artifact_path = local_path(decision_dir / "BrainDecision.v1.json")
        accepted_receipt_path = local_path(decision_dir / "ProductJourneyReceipt.v1.json")
        current_fence_gen = ckpt["generation"]

        # Transaction 1: fencing check, logical idempotency, conflict detection, or reservation.
        reconciling_existing = False
        reserved_seq: int
        reserved_record_sha256: str
        reserved_fence_gen: int
        try:
            with self._database() as db:
                reserved_pair, accepted_pair, other_event_ids, other_decision_ids = (
                    self._validate_brain_journal_records(
                        db,
                        key=key,
                        logical_identity=handoff.logical_identity,
                        journey_id=live_request["journey_id"],
                        completed_sha256=completed.sha256,
                    )
                )
                if resolved_event_id in other_event_ids or decision["decision_id"] in other_decision_ids:
                    return self._brain_hold(
                        "DUPLICATE_BRAIN_EVENT_ID",
                        live_request=live_request,
                        decision_input=decision_input,
                    )

                if accepted_pair is not None:
                    acc_seq, acc = accepted_pair
                    same_decision = (
                        acc["decision_id"] == decision["decision_id"]
                        and acc["decision_sha256"] == decision["decision_sha256"]
                        and acc["decision_bytes_sha256"] == decision_bytes_sha256
                        and acc["event_id"] == resolved_event_id
                        and acc["evidence_request_sha256"] == live_request["request_sha256"]
                        and acc["chat_ref"] == decision["chat_ref"]
                        and acc["actor_ref"] == decision["provenance"]["actor_ref"]
                        and acc["origin_ref"] == decision_input.origin_ref
                        and acc["origin_kind"] == decision_input.origin_kind
                        and acc["accepted_receipt_sha256"] == accepted_receipt_sha256
                        and acc["accepted_receipt_file_sha256"] == accepted_receipt_file_sha256
                    )
                    if not same_decision:
                        return self._brain_hold(
                            "BRAIN_DECISION_CONFLICT",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    if (
                        expected_fencing_generation is not None
                        and (type(expected_fencing_generation) is not int
                             or expected_fencing_generation != acc["fencing_generation"])
                    ):
                        return self._brain_hold(
                            "STALE_FENCING_GENERATION",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    if (
                        not decision_artifact_path.is_file()
                        or not accepted_receipt_path.is_file()
                        or read_local(decision_artifact_path, limit=131072) != decision_bytes
                        or read_local(accepted_receipt_path, limit=131072) != accepted_receipt.raw
                    ):
                        return self._brain_hold(
                            "BRAIN_RECEIPT_TAMPERED",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    return self._brain_accepted_result(
                        acc,
                        journal_seq=acc_seq,
                        reason="BRAIN_ACCEPTED_IDEMPOTENT_REPLAY",
                        idempotent_replay=True,
                    )

                if reserved_pair is not None:
                    res_seq, res = reserved_pair
                    if res["lease_until"] > time.time():
                        return self._brain_hold(
                            "BRAIN_DECISION_INTAKE_IN_PROGRESS",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    if reconcile_fencing_generation is None:
                        return self._brain_hold(
                            "BRAIN_INTERRUPTED_COMMIT_HOLD",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    if (
                        type(reconcile_fencing_generation) is not int
                        or reconcile_fencing_generation != res["fencing_generation"]
                    ):
                        return self._brain_hold(
                            "STALE_FENCING_GENERATION",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    same_reserved = (
                        res["decision_id"] == decision["decision_id"]
                        and res["decision_sha256"] == decision["decision_sha256"]
                        and res["decision_bytes_sha256"] == decision_bytes_sha256
                        and res["event_id"] == resolved_event_id
                        and res["evidence_request_sha256"] == live_request["request_sha256"]
                        and res["chat_ref"] == decision["chat_ref"]
                        and res["actor_ref"] == decision["provenance"]["actor_ref"]
                        and res["origin_ref"] == decision_input.origin_ref
                        and res["origin_kind"] == decision_input.origin_kind
                        and res["accepted_receipt_sha256"] == accepted_receipt_sha256
                        and res["accepted_receipt_file_sha256"] == accepted_receipt_file_sha256
                    )
                    if not same_reserved:
                        return self._brain_hold(
                            "BRAIN_DECISION_CONFLICT",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    if (
                        not decision_artifact_path.is_file()
                        or not accepted_receipt_path.is_file()
                        or read_local(decision_artifact_path, limit=131072) != decision_bytes
                        or read_local(accepted_receipt_path, limit=131072) != accepted_receipt.raw
                    ):
                        return self._brain_hold(
                            "BRAIN_INTERRUPTED_COMMIT_HOLD",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    reconciling_existing = True
                    reserved_seq = res_seq
                    reserved_record_sha256 = res["record_sha256"]
                    reserved_fence_gen = res["fencing_generation"]
                else:
                    if (
                        expected_fencing_generation is not None
                        and (type(expected_fencing_generation) is not int
                             or expected_fencing_generation != current_fence_gen)
                    ):
                        return self._brain_hold(
                            "STALE_FENCING_GENERATION",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    if reconcile_fencing_generation is not None:
                        return self._brain_hold(
                            "STALE_FENCING_GENERATION",
                            live_request=live_request,
                            decision_input=decision_input,
                        )
                    reserved_body = {
                        "schema_version": "BrainDecisionIntakeReserved.v1",
                        "completed_sha256": completed.sha256,
                        "logical_identity": handoff.logical_identity,
                        "journey_id": live_request["journey_id"],
                        "source_sha": live_request["source_sha"],
                        "directive_version": live_request["directive_version"],
                        "directive_sha256": live_request["directive_sha256"],
                        "wait_receipt_sha256": live_request["receipt_sha256"],
                        "wait_receipt_file_sha256": live_request["receipt_file_sha256"],
                        "artifact_sha256": live_request["local_artifact"]["artifact_sha256"],
                        "q4_qa_sha256": live_request["q4_technical_qa"]["qa_sha256"],
                        "fencing_generation": current_fence_gen,
                        "lease_until": time.time() + self._lease_seconds,
                        "decision_id": decision["decision_id"],
                        "decision_sha256": decision["decision_sha256"],
                        "decision_bytes_sha256": decision_bytes_sha256,
                        "event_id": resolved_event_id,
                        "chat_ref": decision["chat_ref"],
                        "actor_ref": decision["provenance"]["actor_ref"],
                        "origin_ref": decision_input.origin_ref,
                        "origin_kind": decision_input.origin_kind,
                        "identity_authenticated": False,
                        "live_cdp_proven": False,
                        "evidence_request_sha256": live_request["request_sha256"],
                        "pulse_journal_checkpoint": ckpt,
                        "accepted_receipt_sha256": accepted_receipt_sha256,
                        "accepted_receipt_file_sha256": accepted_receipt_file_sha256,
                    }
                    reserved_record_sha256 = digest(reserved_body)
                    reserved_full = {**reserved_body, "record_sha256": reserved_record_sha256}
                    self._journal(db, key, "BrainDecisionIntakeReserved.v1", reserved_full)
                    reserved_seq = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                    reserved_fence_gen = current_fence_gen
        except OrchestrationError as exc:
            return self._brain_hold(str(exc), live_request=live_request, decision_input=decision_input)

        if not reconciling_existing:
            self._brain_decision_cut("after_reserve")
            decision_dir.mkdir(parents=True, exist_ok=True)
            runtime_receipt.put_new(decision_artifact_path, decision_bytes)
            runtime_receipt.put_new(accepted_receipt_path, accepted_receipt.raw)
            self._brain_decision_cut("after_receipt_write")

        # Transaction 2: commit exact BrainDecisionAccepted.v1 record after receipt verification.
        accepted_body = {
            "schema_version": "BrainDecisionAccepted.v1",
            "completed_sha256": completed.sha256,
            "logical_identity": handoff.logical_identity,
            "journey_id": live_request["journey_id"],
            "source_sha": live_request["source_sha"],
            "directive_version": live_request["directive_version"],
            "directive_sha256": live_request["directive_sha256"],
            "wait_receipt_sha256": live_request["receipt_sha256"],
            "wait_receipt_file_sha256": live_request["receipt_file_sha256"],
            "artifact_sha256": live_request["local_artifact"]["artifact_sha256"],
            "q4_qa_sha256": live_request["q4_technical_qa"]["qa_sha256"],
            "reserved_seq": reserved_seq,
            "reserved_record_sha256": reserved_record_sha256,
            "fencing_generation": reserved_fence_gen,
            "decision_id": decision["decision_id"],
            "decision_sha256": decision["decision_sha256"],
            "decision_bytes_sha256": decision_bytes_sha256,
            "event_id": resolved_event_id,
            "chat_ref": decision["chat_ref"],
            "actor_ref": decision["provenance"]["actor_ref"],
            "origin_ref": decision_input.origin_ref,
            "origin_kind": decision_input.origin_kind,
            "identity_authenticated": False,
            "live_cdp_proven": False,
            "accepted_at": now_at,
            "evidence_request_sha256": live_request["request_sha256"],
            "pulse_journal_checkpoint": ckpt,
            "accepted_receipt_sha256": accepted_receipt_sha256,
            "accepted_receipt_file_sha256": accepted_receipt_file_sha256,
            "accepted_receipt_ref": str(accepted_receipt_path),
            "accepted_state": "READY_FOR_HUMAN_RELEASE",
        }
        accepted_record_sha256 = digest(accepted_body)
        accepted_full = {**accepted_body, "record_sha256": accepted_record_sha256}

        with self._database() as db:
            res_pair, acc_pair, _, _ = self._validate_brain_journal_records(
                db,
                key=key,
                logical_identity=handoff.logical_identity,
                journey_id=live_request["journey_id"],
                completed_sha256=completed.sha256,
            )
            require(
                acc_pair is None
                and res_pair is not None
                and res_pair[0] == reserved_seq
                and res_pair[1]["record_sha256"] == reserved_record_sha256,
                "BRAIN_JOURNAL_TAMPERED",
            )
            require(
                read_local(decision_artifact_path, limit=131072) == decision_bytes
                and read_local(accepted_receipt_path, limit=131072) == accepted_receipt.raw,
                "BRAIN_RECEIPT_TAMPERED",
            )
            self._journal(db, key, "BrainDecisionAccepted.v1", accepted_full)
            committed_seq = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        self._brain_decision_cut("after_commit")

        # Mandatory reopen verification on a fresh SQLite connection + disk readback.
        with self._database() as db:
            _, reopened_acc_pair, _, _ = self._validate_brain_journal_records(
                db,
                key=key,
                logical_identity=handoff.logical_identity,
                journey_id=live_request["journey_id"],
                completed_sha256=completed.sha256,
            )
            require(
                reopened_acc_pair is not None
                and reopened_acc_pair[0] == committed_seq
                and reopened_acc_pair[1] == accepted_full,
                "BRAIN_JOURNAL_TAMPERED",
            )
        reopened_decision = parse_pulse_contract("BrainDecision", read_local(decision_artifact_path, limit=131072))
        reopened_receipt = parse_pulse_contract("ProductJourneyReceipt", read_local(accepted_receipt_path, limit=131072))
        require(
            reopened_decision.raw == decision_bytes
            and reopened_receipt.raw == accepted_receipt.raw
            and reopened_receipt.to_dict()["state"] == "READY_FOR_HUMAN_RELEASE"
            and reopened_receipt.to_dict()["brain_decision"] == {
                "ref": decision["decision_id"],
                "sha256": decision["decision_sha256"],
            },
            "BRAIN_RECEIPT_TAMPERED",
        )
        # Confirm original W2 WAIT_BRAIN journal and export remain intact and uncorrupted.
        attended_worker.inspect_a04_pulse_handoff(
            private_dir=str(private_dir),
            export_dir=str(export_dir),
            **common_kwargs,
        )
        return self._brain_accepted_result(
            accepted_full,
            journal_seq=committed_seq,
            reason="BRAIN_ACCEPTED",
            idempotent_replay=False,
        )

    def inspect_pulse_brain_decision(self, key: str) -> BrainDecisionIntakeResult:
        """Read-only reopen check of persisted W3a local BrainDecision.v1 intake state."""
        grant = self._grant()
        require(grant.route_id == "a04-to-pulse-offline", "LOCAL_ROUTE_MISMATCH")
        completed, witness, private_dir, export_dir = self._load_completed_pulse_stage(key)
        handoff = completed.spec.pulse_handoff
        common_kwargs = self._pulse_wait_brain_kwargs(completed)
        try:
            _, _, _, _, live_request_raw = attended_worker.load_a04_pulse_wait_brain_snapshot(
                private_dir=str(private_dir),
                export_dir=str(export_dir),
                **common_kwargs,
            )
            live_request = runtime_receipt.parse_brain_review_evidence_request(live_request_raw)
            self._verify_request_matches_witness(live_request, witness, handoff)
        except attended_worker.WorkerHold as exc:
            if str(exc) == "UNKNOWN_EFFECT":
                return self._brain_hold("BRAIN_UNKNOWN_EFFECT", state="UNKNOWN_EFFECT")
            return self._brain_hold("BRAIN_JOURNAL_OR_SOURCE_TAMPERED")
        except (OrchestrationError, runtime_receipt.ReceiptHold, ValueError, TypeError, KeyError, OSError):
            return self._brain_hold("BRAIN_JOURNAL_OR_SOURCE_TAMPERED")

        try:
            with self._database(read_only=True) as db:
                reserved_pair, accepted_pair, _, _ = self._validate_brain_journal_records(
                    db,
                    key=key,
                    logical_identity=handoff.logical_identity,
                    journey_id=live_request["journey_id"],
                    completed_sha256=completed.sha256,
                )
        except OrchestrationError as exc:
            return self._brain_hold(str(exc), live_request=live_request)

        if accepted_pair is not None:
            acc_seq, acc = accepted_pair
            decision_dir = self._pulse_brain_decision_dir(key)
            decision_artifact_path = local_path(decision_dir / "BrainDecision.v1.json")
            accepted_receipt_path = local_path(decision_dir / "ProductJourneyReceipt.v1.json")
            if (
                not decision_artifact_path.is_file()
                or not accepted_receipt_path.is_file()
                or bytes_digest(read_local(decision_artifact_path, limit=131072)) != acc["decision_bytes_sha256"]
                or bytes_digest(read_local(accepted_receipt_path, limit=131072)) != acc["accepted_receipt_file_sha256"]
            ):
                return self._brain_hold("BRAIN_RECEIPT_TAMPERED", live_request=live_request)
            return self._brain_accepted_result(
                acc,
                journal_seq=acc_seq,
                reason="BRAIN_ACCEPTED_PERSISTED",
                idempotent_replay=True,
            )
        if reserved_pair is not None:
            return self._brain_hold("BRAIN_INTERRUPTED_COMMIT_HOLD", live_request=live_request)
        return self._brain_hold(
            "WAIT_BRAIN_NO_DECISION",
            status="NO_DECISION",
            state="WAIT_BRAIN",
            live_request=live_request,
        )
