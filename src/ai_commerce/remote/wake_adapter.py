"""EF02-D D2: local trusted-origin wake adapter, provenance verification, and idempotency store."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .completion_event import CompletionEventError, validate_completion_event
from .conversation_binding import (
    BindingError, ResolvedBinding, _CHAT, _decode_unique,
    _endpoint, _outside_repo, resolve_binding,
)
from .trusted_origin import LocalCallerAttestation, LocalOriginRegistrar, OriginError

_MAX_STORE_BYTES = 131072


class ProvenanceError(ValueError):
    """Artifact, run, or batch result provenance verification failure."""


class WakeDeliveryError(RuntimeError):
    """Wake adapter transport or receipt failure."""


@dataclass(frozen=True, repr=False)
class GitHubArtifactProvenance:
    """Separately trusted GitHub workflow run and artifact attestation context.

    repr=False prevents leaking internal paths or tokens into logs.
    """
    repository: str
    run_id: int
    run_attempt: int
    artifact_name: str
    workflow_run_status: str
    artifact_digest: str | None = None
    attestation_verified: bool = False

    def __repr__(self) -> str:
        return (
            f"GitHubArtifactProvenance(repo={self.repository!r}, run_id={self.run_id!r}, "
            f"run_attempt={self.run_attempt!r}, status={self.workflow_run_status!r}, "
            f"verified={self.attestation_verified!r})"
        )


def verify_provenance(
    event: Mapping,
    batch_result: Mapping,
    provenance: GitHubArtifactProvenance | None,
) -> None:
    """Verify completion_event against matching FINAL batch_result and trusted GitHub artifact provenance.

    Evidence-bearing verifier takes a separately trusted GitHub run/artifact context;
    never accepts just two mutually matching caller-controlled JSON blobs as proof.
    """
    if provenance is None or not isinstance(provenance, GitHubArtifactProvenance):
        raise ProvenanceError("PROVENANCE_UNVERIFIED")
    if not provenance.attestation_verified:
        raise ProvenanceError("PROVENANCE_UNVERIFIED")
    if provenance.workflow_run_status not in ("completed", "success"):
        raise ProvenanceError("PROVENANCE_UNVERIFIED")
    if provenance.repository != event.get("repository"):
        raise ProvenanceError("PROVENANCE_UNVERIFIED")
    if provenance.run_id != event.get("run_id"):
        raise ProvenanceError("PROVENANCE_UNVERIFIED")
    if provenance.run_attempt != event.get("run_attempt"):
        raise ProvenanceError("PROVENANCE_UNVERIFIED")

    # Re-validate completion event identity and shape
    try:
        validated_event = validate_completion_event(event)
    except CompletionEventError as exc:
        raise ProvenanceError(f"EVENT_INVALID: {exc}") from exc

    # Validate final batch_result
    if not isinstance(batch_result, Mapping):
        raise ProvenanceError("BATCH_RESULT_INVALID")
    if batch_result.get("kind") != "AI_COMMERCE_REMOTE_BATCH_RESULT" or batch_result.get("version") != 1:
        raise ProvenanceError("BATCH_RESULT_KIND_INVALID")
    if batch_result.get("batch_id") != event.get("batch_id"):
        raise ProvenanceError("BATCH_RESULT_MISMATCH")
    if batch_result.get("source_sha") != event.get("source_sha"):
        raise ProvenanceError("BATCH_RESULT_MISMATCH")
    if batch_result.get("status") != event.get("batch_status"):
        raise ProvenanceError("BATCH_RESULT_MISMATCH")
    if batch_result.get("status") not in ("COMPLETE", "PARTIAL_OR_FAILED"):
        raise ProvenanceError("BATCH_RESULT_NOT_FINAL")

    if batch_result.get("wake_event_status") != "RECORDED_UNBOUND":
        raise ProvenanceError("WAKE_EVENT_STATUS_INVALID")
    if batch_result.get("wake_event_id") != event.get("event_id"):
        raise ProvenanceError("WAKE_EVENT_ID_MISMATCH")

    result_run_id = batch_result.get("github_workflow_run_id")
    if result_run_id is not None:
        try:
            if int(result_run_id) != event.get("run_id"):
                raise ProvenanceError("RUN_ID_MISMATCH")
        except (ValueError, TypeError):
            raise ProvenanceError("RUN_ID_MISMATCH")
    if batch_result.get("github_issue_number") != event.get("issue_number"):
        raise ProvenanceError("ISSUE_NUMBER_MISMATCH")

    result_actions = batch_result.get("actions")
    event_actions = event.get("actions")
    if not isinstance(result_actions, list) or len(result_actions) != len(event_actions):
        raise ProvenanceError("ACTIONS_MISMATCH")
    for ra, ea in zip(result_actions, event_actions):
        if not isinstance(ra, Mapping):
            raise ProvenanceError("ACTIONS_MISMATCH")
        if ra.get("action_id") != ea.get("action_id"):
            raise ProvenanceError("ACTIONS_MISMATCH")
        if ra.get("attempt_id") != ea.get("attempt_id"):
            raise ProvenanceError("ACTIONS_MISMATCH")
        if ra.get("status") != ea.get("status"):
            raise ProvenanceError("ACTIONS_MISMATCH")
        if ea.get("candidate_sha") is not None:
            if ra.get("candidate_sha") != ea.get("candidate_sha"):
                raise ProvenanceError("ACTIONS_MISMATCH")
        elif ra.get("candidate_sha") is not None:
            raise ProvenanceError("ACTIONS_MISMATCH")

    expected_succeeded = sum(a.get("status") == "CANDIDATE_READY" for a in event_actions)
    if batch_result.get("total") != len(event_actions) or batch_result.get("succeeded") != expected_succeeded:
        raise ProvenanceError("BATCH_RESULT_AGGREGATE_MISMATCH")
    expected_status = "COMPLETE" if expected_succeeded == len(event_actions) else "PARTIAL_OR_FAILED"
    if batch_result.get("status") != expected_status:
        raise ProvenanceError("BATCH_RESULT_STATUS_MISMATCH")


@dataclass(frozen=True, repr=False)
class WakeReceipt:
    """Outcome of an exact conversation wake delivery attempt.

    repr=False prevents leaking private chat URLs, CDP endpoints, or tokens.
    """
    event_id: str
    route_id: str
    generation: int
    delivery_status: str  # NOT_ATTEMPTED, UNSUPPORTED, UNKNOWN, CONFIRMED
    chat_delivery_proven: bool = False
    brain_continuation_proven: bool = False  # Always False (NOT_PROVEN)
    witness_evidence: dict | None = None
    error_code: str | None = None
    replayed: bool = False

    def __repr__(self) -> str:
        return (
            f"WakeReceipt(event_id={self.event_id!r}, route_id={self.route_id!r}, "
            f"generation={self.generation!r}, status={self.delivery_status!r}, "
            f"delivery_proven={self.chat_delivery_proven!r}, "
            f"brain_continuation_proven={self.brain_continuation_proven!r}, "
            f"error_code={self.error_code!r})"
        )


class DurableIdempotencyStore:
    """Local, private crash-resistant store keyed by (event_id, route_id, generation)."""

    def __init__(self, store_path: Path, repository_root: Path):
        self.store_path = Path(store_path)
        self.repository_root = Path(repository_root)
        if not self.store_path.is_absolute() or not _outside_repo(self.store_path, self.repository_root):
            raise OriginError("IDEMPOTENCY_STORE_LOCATION_INVALID")

    def _key(self, event_id: str, route_id: str, generation: int) -> str:
        return f"{event_id}::{route_id}::{generation}"

    def read_store(self) -> dict:
        if not self.store_path.exists():
            return {"version": 1, "records": {}}
        try:
            if self.store_path.stat().st_size > _MAX_STORE_BYTES:
                raise OriginError("IDEMPOTENCY_STORE_TOO_LARGE")
            raw = self.store_path.read_text(encoding="utf-8")
            data = _decode_unique(raw)
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("records"), dict):
                raise OriginError("IDEMPOTENCY_STORE_INVALID")
            return data
        except (OSError, UnicodeError, json.JSONDecodeError, BindingError) as exc:
            raise OriginError("IDEMPOTENCY_STORE_UNAVAILABLE") from exc

    def write_store(self, data: dict) -> None:
        if not _outside_repo(self.store_path, self.repository_root):
            raise OriginError("IDEMPOTENCY_STORE_LOCATION_INVALID")
        raw = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
        if len(raw.encode("utf-8")) > _MAX_STORE_BYTES:
            raise OriginError("IDEMPOTENCY_STORE_TOO_LARGE")
        temporary = self.store_path.with_suffix(self.store_path.suffix + ".part")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(self.store_path)

    def get_record(self, event_id: str, route_id: str, generation: int) -> dict | None:
        store = self.read_store()
        return store["records"].get(self._key(event_id, route_id, generation))

    def record_intent(self, event_id: str, route_id: str, generation: int) -> None:
        """Write-ahead transition to UNKNOWN before dispatching send."""
        store = self.read_store()
        key = self._key(event_id, route_id, generation)
        store["records"][key] = {
            "event_id": event_id,
            "route_id": route_id,
            "generation": generation,
            "delivery_status": "UNKNOWN",
            "chat_delivery_proven": False,
            "brain_continuation_proven": False,
            "timestamp": time.time(),
            "witness_evidence": None,
            "error_code": "IN_FLIGHT_OR_CRASHED",
        }
        self.write_store(store)

    def record_outcome(
        self,
        event_id: str,
        route_id: str,
        generation: int,
        *,
        delivery_status: str,
        chat_delivery_proven: bool,
        witness_evidence: dict | None = None,
        error_code: str | None = None,
    ) -> None:
        store = self.read_store()
        key = self._key(event_id, route_id, generation)
        store["records"][key] = {
            "event_id": event_id,
            "route_id": route_id,
            "generation": generation,
            "delivery_status": delivery_status,
            "chat_delivery_proven": chat_delivery_proven,
            "brain_continuation_proven": False,  # Always False
            "timestamp": time.time(),
            "witness_evidence": witness_evidence,
            "error_code": error_code,
        }
        self.write_store(store)


class CdpSession(Protocol):
    def post_chat_message(self, text: str) -> bool: ...
    def witness_exact_receipt(self, event_id: str, timeout: float = 3.0) -> dict | None: ...
    def is_authenticated(self) -> bool: ...
    def close(self) -> None: ...


class CdpClient(Protocol):
    def get_targets(self) -> list[dict[str, Any]]: ...
    def attach_to_target(self, target_id: str) -> CdpSession: ...


class WakeAdapter(Protocol):
    def send_wake(self, *, binding: ResolvedBinding, message: str, event_id: str) -> WakeReceipt: ...


class LocalCdpWakeAdapter:
    """Local CDP wake adapter strictly enforcing exact-chat tab isolation and verified receipts."""

    def __init__(self, cdp_client: CdpClient | None = None):
        self.cdp_client = cdp_client

    def _query_live_targets(self, cdp_endpoint: str) -> list[dict[str, Any]]:
        url = cdp_endpoint.rstrip("/") + "/json/list"
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Commerce-Local-Wake/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list):
                    return data
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        return []

    def send_wake(self, *, binding: ResolvedBinding, message: str, event_id: str) -> WakeReceipt:
        matched = _CHAT.fullmatch(binding.chat_url)
        if not matched:
            return WakeReceipt(
                event_id=event_id,
                route_id=binding.route_id,
                generation=binding.generation,
                delivery_status="UNSUPPORTED",
                chat_delivery_proven=False,
                brain_continuation_proven=False,
                error_code="CHAT_URL_INVALID",
            )

        if self.cdp_client is None:
            # Attempt live probe of endpoint
            targets = self._query_live_targets(binding.cdp_endpoint)
            if not targets:
                return WakeReceipt(
                    event_id=event_id,
                    route_id=binding.route_id,
                    generation=binding.generation,
                    delivery_status="UNSUPPORTED",
                    chat_delivery_proven=False,
                    brain_continuation_proven=False,
                    error_code="CDP_UNAVAILABLE",
                )
            client = None
        else:
            client = self.cdp_client
            try:
                targets = client.get_targets()
            except Exception as exc:
                return WakeReceipt(
                    event_id=event_id,
                    route_id=binding.route_id,
                    generation=binding.generation,
                    delivery_status="UNSUPPORTED",
                    chat_delivery_proven=False,
                    brain_continuation_proven=False,
                    error_code=f"CDP_UNAVAILABLE: {exc}",
                )

        # Strict target matching: exact chat URL only, no default or active tab
        exact_url = binding.chat_url.rstrip("/")
        matching = []
        for t in targets:
            if t.get("type") == "page":
                t_url = str(t.get("url", "")).rstrip("/")
                if t_url == exact_url:
                    matching.append(t)

        if len(matching) == 0:
            return WakeReceipt(
                event_id=event_id,
                route_id=binding.route_id,
                generation=binding.generation,
                delivery_status="UNSUPPORTED",
                chat_delivery_proven=False,
                brain_continuation_proven=False,
                error_code="EXACT_CHAT_TAB_NOT_FOUND",
            )
        if len(matching) > 1:
            return WakeReceipt(
                event_id=event_id,
                route_id=binding.route_id,
                generation=binding.generation,
                delivery_status="UNSUPPORTED",
                chat_delivery_proven=False,
                brain_continuation_proven=False,
                error_code="AMBIGUOUS_CHAT_TARGETS",
            )

        target = matching[0]
        if client is None:
            # Without an active CDP client implementation, we fail closed
            return WakeReceipt(
                event_id=event_id,
                route_id=binding.route_id,
                generation=binding.generation,
                delivery_status="UNSUPPORTED",
                chat_delivery_proven=False,
                brain_continuation_proven=False,
                error_code="PLATFORM_WRITE_UNSUPPORTED",
            )

        try:
            session = client.attach_to_target(target["id"])
        except Exception as exc:
            return WakeReceipt(
                event_id=event_id,
                route_id=binding.route_id,
                generation=binding.generation,
                delivery_status="UNSUPPORTED",
                chat_delivery_proven=False,
                brain_continuation_proven=False,
                error_code=f"ATTACH_FAILED: {exc}",
            )

        try:
            if not session.is_authenticated():
                return WakeReceipt(
                    event_id=event_id,
                    route_id=binding.route_id,
                    generation=binding.generation,
                    delivery_status="UNSUPPORTED",
                    chat_delivery_proven=False,
                    brain_continuation_proven=False,
                    error_code="SESSION_UNAUTHENTICATED_OR_CAPTCHA",
                )

            sent = session.post_chat_message(message)
            if not sent:
                return WakeReceipt(
                    event_id=event_id,
                    route_id=binding.route_id,
                    generation=binding.generation,
                    delivery_status="UNSUPPORTED",
                    chat_delivery_proven=False,
                    brain_continuation_proven=False,
                    error_code="PLATFORM_WRITE_UNSUPPORTED",
                )

            # Witness exact receipt in DOM
            witness = session.witness_exact_receipt(event_id, timeout=3.0)
            if witness and witness.get("receipt_verified") is True and witness.get("exact_chat_turn_id"):
                return WakeReceipt(
                    event_id=event_id,
                    route_id=binding.route_id,
                    generation=binding.generation,
                    delivery_status="CONFIRMED",
                    chat_delivery_proven=True,
                    brain_continuation_proven=False,  # Native Brain continuation is NOT_PROVEN
                    witness_evidence=witness,
                    error_code=None,
                )
            else:
                # Dispatched but receipt not independently witnessed
                return WakeReceipt(
                    event_id=event_id,
                    route_id=binding.route_id,
                    generation=binding.generation,
                    delivery_status="UNKNOWN",
                    chat_delivery_proven=False,
                    brain_continuation_proven=False,
                    witness_evidence=witness,
                    error_code="RECEIPT_NOT_WITNESSED",
                )
        finally:
            try:
                session.close()
            except Exception:
                pass


def deliver_exact_chat_wake(
    *,
    event: Mapping,
    batch_result: Mapping,
    provenance: GitHubArtifactProvenance | None,
    origin_attestation: LocalCallerAttestation | None,
    registry_path: Path,
    idempotency_path: Path,
    repository_root: Path,
    adapter: WakeAdapter | None = None,
) -> WakeReceipt:
    """Execute the trusted local wake pipeline with full provenance and idempotency checks."""
    event_id = str(event.get("event_id") or "UNKNOWN")

    # 1. Local origin authority attestation
    if origin_attestation is None or not origin_attestation.is_trusted():
        return WakeReceipt(
            event_id=event_id,
            route_id="UNKNOWN",
            generation=0,
            delivery_status="UNSUPPORTED",
            chat_delivery_proven=False,
            brain_continuation_proven=False,
            error_code="UNVERIFIED_ORIGIN",
        )

    # 2. Provenance verification
    try:
        verify_provenance(event, batch_result, provenance)
    except ProvenanceError as exc:
        return WakeReceipt(
            event_id=event_id,
            route_id="UNKNOWN",
            generation=0,
            delivery_status="UNSUPPORTED",
            chat_delivery_proven=False,
            brain_continuation_proven=False,
            error_code=f"PROVENANCE_UNVERIFIED: {exc}",
        )

    # 3. Resolve route from local origin registrar
    registrar = LocalOriginRegistrar(registry_path, repository_root)
    try:
        route_id = registrar.resolve_claim_route(
            repository=str(event["repository"]),
            batch_id=str(event["batch_id"]),
            source_sha=str(event["source_sha"]),
            issue_number=int(event["issue_number"]),
            attestation=origin_attestation,
        )
    except OriginError as exc:
        return WakeReceipt(
            event_id=event_id,
            route_id="UNKNOWN",
            generation=0,
            delivery_status="UNSUPPORTED",
            chat_delivery_proven=False,
            brain_continuation_proven=False,
            error_code=str(exc),
        )

    # 4. Resolve binding
    try:
        binding = resolve_binding(
            dict(event),
            route_id=route_id,
            registry_path=registry_path,
            repository_root=repository_root,
        )
    except BindingError as exc:
        return WakeReceipt(
            event_id=event_id,
            route_id=route_id,
            generation=0,
            delivery_status="UNSUPPORTED",
            chat_delivery_proven=False,
            brain_continuation_proven=False,
            error_code=str(exc),
        )

    # 5. Check idempotency store
    store = DurableIdempotencyStore(idempotency_path, repository_root)
    existing = store.get_record(event["event_id"], binding.route_id, binding.generation)
    if existing is not None:
        status = existing.get("delivery_status")
        if status == "CONFIRMED":
            return WakeReceipt(
                event_id=event["event_id"],
                route_id=binding.route_id,
                generation=binding.generation,
                delivery_status="CONFIRMED",
                chat_delivery_proven=True,
                brain_continuation_proven=False,
                witness_evidence=existing.get("witness_evidence"),
                replayed=True,
            )
        elif status == "UNKNOWN":
            # An earlier send crashed or was uncertain: HOLD send, do not duplicate
            return WakeReceipt(
                event_id=event["event_id"],
                route_id=binding.route_id,
                generation=binding.generation,
                delivery_status="UNKNOWN",
                chat_delivery_proven=False,
                brain_continuation_proven=False,
                witness_evidence=existing.get("witness_evidence"),
                error_code="DELIVERY_UNCERTAIN_HELD",
            )

    # 6. Record write-ahead intent
    store.record_intent(event["event_id"], binding.route_id, binding.generation)

    # 7. Dispatch wake via adapter
    active_adapter = adapter if adapter is not None else LocalCdpWakeAdapter()
    summary_message = (
        f"[AI Commerce] EF02 batch {event['batch_id']}: {event['batch_status']} "
        f"(run {event['run_id']}, attempt {event['run_attempt']}, event {event['event_id']})"
    )

    receipt = active_adapter.send_wake(
        binding=binding,
        message=summary_message,
        event_id=event["event_id"],
    )

    # 8. Record outcome
    store.record_outcome(
        event["event_id"],
        binding.route_id,
        binding.generation,
        delivery_status=receipt.delivery_status,
        chat_delivery_proven=receipt.chat_delivery_proven,
        witness_evidence=receipt.witness_evidence,
        error_code=receipt.error_code,
    )

    return receipt
