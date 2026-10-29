"""EF02-D D2: local trusted-origin wake adapter, provenance verification, and idempotency store."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import http.client
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from .completion_event import CompletionEventError, validate_completion_event
from .conversation_binding import (
    ResolvedBinding, _CHAT, _ROUTE, _endpoint,
)
from .trusted_origin import LocalCallerAttestation, LocalOriginRegistrar, OriginAuthority, OriginError
from ._private_state import PrivateJsonState, PrivateStateError

_MAX_STORE_BYTES = 131072
_EVENT_ID = re.compile(r"ef02-completion-v1:[0-9a-f]{64}\Z")
_OUTCOME_CODES = frozenset({
    "IN_FLIGHT_OR_CRASHED", "DELIVERY_UNCERTAIN_HELD", "PLATFORM_WRITE_UNSUPPORTED",
    "CDP_UNAVAILABLE", "CDP_PROBE_DISABLED", "CHAT_URL_INVALID", "CDP_ENDPOINT_INVALID",
    "CDP_TARGETS_INVALID", "EXACT_CHAT_TAB_NOT_FOUND", "AMBIGUOUS_CHAT_TARGETS",
    "SESSION_UNAUTHENTICATED_OR_CAPTCHA", "ATTACH_FAILED", "ADAPTER_FAILED",
    "ADAPTER_RECEIPT_INVALID", "RECEIPT_VERIFICATION_UNSUPPORTED", "DELIVERY_UNSUPPORTED",
})


class ArtifactAuthority(Protocol):
    """Independently authenticate the artifact and these exact decoded contents.

    Configured only by trusted local application code, never by event/Issue data.
    Must verify GitHub repository/run/attempt/artifact identity and authenticate
    the final batch_result and event bytes (including candidate SHA/status).
    Matching JSONs, public digests and caller attestation flags are insufficient.
    D2 supplies no production authority; the default is fail closed.
    """
    def verify_artifact(self, provenance: GitHubArtifactProvenance, *,
                        event: Mapping, batch_result: Mapping) -> bool: ...


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
        return "GitHubArtifactProvenance(<private, unverified hints>)"


def verify_provenance(
    event: Mapping,
    batch_result: Mapping,
    provenance: GitHubArtifactProvenance | None,
    *,
    authority: ArtifactAuthority | None = None,
) -> None:
    """Verify completion_event against matching FINAL batch_result and trusted GitHub artifact provenance.

    Evidence-bearing verifier takes a separately trusted GitHub run/artifact context;
    never accepts just two mutually matching caller-controlled JSON blobs as proof.
    """
    if (type(provenance) is not GitHubArtifactProvenance or authority is None or
            not isinstance(event, Mapping)):
        raise ProvenanceError("PROVENANCE_UNVERIFIED")
    try:
        event, batch_result = copy.deepcopy(event), copy.deepcopy(batch_result)
    except Exception:
        raise ProvenanceError("PROVENANCE_UNVERIFIED") from None
    if (provenance.attestation_verified is not True or
            type(provenance.run_id) is not int or type(provenance.run_attempt) is not int or
            not isinstance(provenance.artifact_name, str) or not _ROUTE.fullmatch(provenance.artifact_name)):
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
        event = validate_completion_event(event)
    except CompletionEventError:
        raise ProvenanceError("EVENT_INVALID") from None

    # Validate final batch_result
    if not isinstance(batch_result, Mapping):
        raise ProvenanceError("BATCH_RESULT_INVALID")
    if (batch_result.get("kind") != "AI_COMMERCE_REMOTE_BATCH_RESULT" or
            type(batch_result.get("version")) is not int or batch_result["version"] != 1):
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
    if (type(result_run_id) not in (str, int) or
            str(result_run_id) != str(event["run_id"])):
        raise ProvenanceError("RUN_ID_MISMATCH")
    # D1's final report has no run_attempt field. The authority MUST bind the
    # exact run attempt to artifact contents; an optional explicit field must
    # also match, with no coercion of bool/float values.
    if "github_workflow_run_attempt" in batch_result and (
            type(batch_result["github_workflow_run_attempt"]) is not int or
            batch_result["github_workflow_run_attempt"] != event["run_attempt"]):
        raise ProvenanceError("RUN_ATTEMPT_MISMATCH")
    if (type(batch_result.get("github_issue_number")) is not int or
            batch_result["github_issue_number"] != event.get("issue_number")):
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
    if (type(batch_result.get("total")) is not int or type(batch_result.get("succeeded")) is not int or
            batch_result["total"] != len(event_actions) or batch_result["succeeded"] != expected_succeeded):
        raise ProvenanceError("BATCH_RESULT_AGGREGATE_MISMATCH")
    expected_status = "COMPLETE" if expected_succeeded == len(event_actions) else "PARTIAL_OR_FAILED"
    if batch_result.get("status") != expected_status:
        raise ProvenanceError("BATCH_RESULT_STATUS_MISMATCH")

    try:
        verified = authority.verify_artifact(provenance, event=copy.deepcopy(event),
                                             batch_result=copy.deepcopy(dict(batch_result)))
    except Exception:
        raise ProvenanceError("PROVENANCE_UNVERIFIED") from None
    if verified is not True:
        raise ProvenanceError("PROVENANCE_UNVERIFIED")


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
        return "WakeReceipt(<private; caller-supplied outcomes are not proof>)"


class DurableIdempotencyStore:
    """Atomic write-ahead claim keyed by (event_id, route_id, generation).

    Every existing state holds the claim, including UNKNOWN and UNSUPPORTED.
    No independently verified receipt capability exists, so CONFIRMED is never
    minted or trusted on replay. Witness payloads are never stored.
    """

    def __init__(self, store_path: Path, repository_root: Path):
        self.store_path = Path(store_path)
        self.repository_root = Path(repository_root)
        try:
            self._state = PrivateJsonState(self.store_path, self.repository_root, label="IDEMPOTENCY_STORE")
        except PrivateStateError as exc:
            raise OriginError(str(exc)) from None

    @contextmanager
    def _transaction(self):
        try:
            with self._state.locked() as transaction:
                yield transaction
        except PrivateStateError as exc:
            raise OriginError(str(exc)) from None

    def _key(self, event_id: str, route_id: str, generation: int) -> str:
        if (not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id) or
                not isinstance(route_id, str) or not _ROUTE.fullmatch(route_id) or
                type(generation) is not int or generation < 1):
            raise OriginError("IDEMPOTENCY_KEY_INVALID")
        return f"{event_id}::{route_id}::{generation}"

    def read_store(self) -> dict:
        with self._transaction() as transaction:
            return self._read_store(transaction)

    def _read_store(self, transaction):
        data = transaction.read({"version": 1, "records": {}})
        if (not isinstance(data, dict) or set(data) != {"version", "records"} or
                type(data["version"]) is not int or data["version"] != 1 or
                not isinstance(data["records"], dict)):
            raise OriginError("IDEMPOTENCY_STORE_INVALID")
        fields = {"event_id", "route_id", "generation", "delivery_status", "chat_delivery_proven",
                  "brain_continuation_proven", "timestamp", "witness_evidence", "error_code"}
        for key, record in data["records"].items():
            if (not isinstance(record, dict) or set(record) != fields or
                    self._key(record["event_id"], record["route_id"], record["generation"]) != key or
                    record["delivery_status"] not in ("UNKNOWN", "UNSUPPORTED", "CONFIRMED") or
                    type(record["chat_delivery_proven"]) is not bool or
                    record["brain_continuation_proven"] is not False or
                    type(record["timestamp"]) not in (int, float) or
                    not 0 < record["timestamp"] < 1e20 or
                    (record["error_code"] is not None and
                     (not isinstance(record["error_code"], str) or record["error_code"] not in _OUTCOME_CODES))):
                raise OriginError("IDEMPOTENCY_STORE_INVALID")
            # A legacy/caller-written CONFIRMED record is an uncertain claim,
            # never authority to return successful delivery or resend it.
            if record["delivery_status"] == "CONFIRMED" or record["chat_delivery_proven"]:
                record["delivery_status"] = "UNKNOWN"
                record["error_code"] = "RECEIPT_VERIFICATION_UNSUPPORTED"
            record["chat_delivery_proven"] = False
            record["witness_evidence"] = None
        return data

    def write_store(self, data: dict) -> None:
        """Bulk replacement could erase crash claims; deliberately unsupported."""
        raise OriginError("IDEMPOTENCY_STORE_REPLACEMENT_FORBIDDEN")

    def get_record(self, event_id: str, route_id: str, generation: int) -> dict | None:
        store = self.read_store()
        return store["records"].get(self._key(event_id, route_id, generation))

    def record_intent(self, event_id: str, route_id: str, generation: int) -> bool:
        """Return True to exactly one writer, only after durable UNKNOWN."""
        key = self._key(event_id, route_id, generation)
        with self._transaction() as transaction:
            store = self._read_store(transaction)
            if key in store["records"]:
                return False
            store["records"][key] = self._record(event_id, route_id, generation, "UNKNOWN",
                                               "IN_FLIGHT_OR_CRASHED")
            transaction.write(store)
            return True

    @staticmethod
    def _record(event_id, route_id, generation, status, error_code):
        return {
            "event_id": event_id,
            "route_id": route_id,
            "generation": generation,
            "delivery_status": status,
            "chat_delivery_proven": False,
            "brain_continuation_proven": False,
            "timestamp": time.time(),
            "witness_evidence": None,
            "error_code": error_code,
        }

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
        key = self._key(event_id, route_id, generation)
        if delivery_status not in ("UNKNOWN", "UNSUPPORTED") or chat_delivery_proven is not False:
            delivery_status, error_code = "UNKNOWN", "RECEIPT_VERIFICATION_UNSUPPORTED"
        if not isinstance(error_code, str) or error_code not in _OUTCOME_CODES:
            error_code = "DELIVERY_UNSUPPORTED" if delivery_status == "UNSUPPORTED" else "ADAPTER_FAILED"
        with self._transaction() as transaction:
            store = self._read_store(transaction)
            existing = store["records"].get(key)
            if existing is None:
                raise OriginError("IDEMPOTENCY_INTENT_REQUIRED")
            if existing["delivery_status"] == "UNKNOWN":
                store["records"][key] = self._record(event_id, route_id, generation, delivery_status, error_code)
                transaction.write(store)


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
    """Exact-target CDP probe only. No supported ChatGPT write/receipt API."""

    def __init__(self, cdp_client: CdpClient | None = None, *, probe_enabled: bool = False):
        self.cdp_client = cdp_client
        self.probe_enabled = probe_enabled is True

    def _query_live_targets(self, cdp_endpoint: str) -> list[dict[str, Any]]:
        if not _endpoint(cdp_endpoint):
            return []
        parts = urlsplit(cdp_endpoint)
        # Direct loopback HTTP only: no proxy environment, redirects or DNS
        # resolution of localhost to an unexpected remote address.
        host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
        connection = http.client.HTTPConnection(host, parts.port, timeout=2)
        try:
            connection.request("GET", "/json/list")
            response = connection.getresponse()
            if response.status != 200:
                return []
            raw = response.read(_MAX_STORE_BYTES + 1)
            if len(raw) > _MAX_STORE_BYTES:
                return []
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError, UnicodeError, http.client.HTTPException, RecursionError):
            return []
        finally:
            connection.close()

    def send_wake(self, *, binding: ResolvedBinding, message: str, event_id: str) -> WakeReceipt:
        def unsupported(code):
            return WakeReceipt(event_id=event_id, route_id=binding.route_id,
                               generation=binding.generation, delivery_status="UNSUPPORTED",
                               error_code=code)

        if not isinstance(binding.chat_url, str) or not _CHAT.fullmatch(binding.chat_url):
            return unsupported("CHAT_URL_INVALID")
        if not _endpoint(binding.cdp_endpoint):
            return unsupported("CDP_ENDPOINT_INVALID")

        client = self.cdp_client
        if client is None:
            if not self.probe_enabled:
                return unsupported("CDP_PROBE_DISABLED")
            targets = self._query_live_targets(binding.cdp_endpoint)
            if not targets:
                return unsupported("CDP_UNAVAILABLE")
        else:
            try:
                targets = client.get_targets()
            except Exception:
                return unsupported("CDP_UNAVAILABLE")

        if (not isinstance(targets, list) or len(targets) > 128 or any(
                not isinstance(t, dict) or not isinstance(t.get("id"), str) or
                not _ROUTE.fullmatch(t["id"]) or not isinstance(t.get("url"), str) or
                not isinstance(t.get("type"), str) for t in targets)):
            return unsupported("CDP_TARGETS_INVALID")
        if len({t["id"] for t in targets}) != len(targets):
            return unsupported("CDP_TARGETS_INVALID")
        matching = [t for t in targets if t["type"] == "page" and
                    t["url"].rstrip("/") == binding.chat_url]
        if not matching:
            return unsupported("EXACT_CHAT_TAB_NOT_FOUND")
        if len(matching) != 1:
            return unsupported("AMBIGUOUS_CHAT_TARGETS")
        if client is None:
            return unsupported("PLATFORM_WRITE_UNSUPPORTED")

        try:
            session = client.attach_to_target(matching[0]["id"])
        except Exception:
            return unsupported("ATTACH_FAILED")
        try:
            try:
                if session.is_authenticated() is not True:
                    return unsupported("SESSION_UNAUTHENTICATED_OR_CAPTCHA")
            except Exception:
                return unsupported("SESSION_UNAUTHENTICATED_OR_CAPTCHA")
            # Client flags, click success and witness dictionaries cannot prove
            # an independently witnessed exact-chat turn. Do not post at all.
            return unsupported("PLATFORM_WRITE_UNSUPPORTED")
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
    origin_authority: OriginAuthority | None = None,
    artifact_authority: ArtifactAuthority | None = None,
) -> WakeReceipt:
    """Local pipeline; authorities must come from a trusted composition root.

    No production write/receipt verifier is supplied. Even a caller-created
    CONFIRMED WakeReceipt is held UNKNOWN and can never authorize a resend.
    """
    event_id, route_id, generation = "UNKNOWN", "UNKNOWN", 0

    def outcome(code, status="UNSUPPORTED", *, replayed=False):
        return WakeReceipt(event_id=event_id, route_id=route_id, generation=generation,
                           delivery_status=status, error_code=code, replayed=replayed)

    try:
        event = validate_completion_event(copy.deepcopy(event))
        batch_result = copy.deepcopy(batch_result)
    except (CompletionEventError, TypeError, ValueError, RecursionError):
        return outcome("EVENT_INVALID")
    event_id = event["event_id"]
    if (type(origin_attestation) is not LocalCallerAttestation or
            not LocalCallerAttestation.is_trusted(
                origin_attestation, authority=origin_authority,
                operation="deliver_wake", context={"event": event})):
        return outcome("UNVERIFIED_ORIGIN")
    try:
        verify_provenance(event, batch_result, provenance, authority=artifact_authority)
    except ProvenanceError:
        return outcome("PROVENANCE_UNVERIFIED")

    try:
        registrar = LocalOriginRegistrar(registry_path, repository_root, authority=origin_authority)
        store = DurableIdempotencyStore(idempotency_path, repository_root)
        registry_paths = {registrar.registry_path, registrar.registry_path.with_name(
            registrar.registry_path.name + ".lock")}
        store_paths = {store.store_path, store.store_path.with_name(store.store_path.name + ".lock")}
        if registry_paths & store_paths:
            raise OriginError("STATE_PATH_CONFLICT")
        # Prevent consent/generation changes between origin authorization and
        # the adapter call. No browser operation occurs unless all checks pass.
        with registrar.binding_for_delivery(event, origin_attestation) as binding:
            route_id, generation = binding.route_id, binding.generation
            if not store.record_intent(event_id, route_id, generation):
                existing = store.get_record(event_id, route_id, generation)
                if existing and existing["delivery_status"] == "UNSUPPORTED":
                    return outcome(existing["error_code"] or "DELIVERY_UNSUPPORTED", replayed=True)
                return outcome("DELIVERY_UNCERTAIN_HELD", "UNKNOWN", replayed=True)

            active_adapter = adapter if adapter is not None else LocalCdpWakeAdapter()
            summary_message = (
                f"[AI Commerce] EF02 batch {event['batch_id']}: {event['batch_status']} "
                f"(run {event['run_id']}, attempt {event['run_attempt']}, event {event_id})"
            )
            try:
                receipt = active_adapter.send_wake(
                    binding=binding, message=summary_message, event_id=event_id)
            except Exception:
                receipt = outcome("ADAPTER_FAILED", "UNKNOWN")
            if (type(receipt) is not WakeReceipt or receipt.event_id != event_id or
                    receipt.route_id != route_id or type(receipt.generation) is not int or
                    receipt.generation != generation or receipt.brain_continuation_proven is not False):
                receipt = outcome("ADAPTER_RECEIPT_INVALID", "UNKNOWN")
            elif (receipt.delivery_status not in ("UNKNOWN", "UNSUPPORTED") or
                  receipt.chat_delivery_proven is not False):
                receipt = outcome("RECEIPT_VERIFICATION_UNSUPPORTED", "UNKNOWN")
            else:
                code = receipt.error_code
                if not isinstance(code, str) or code not in _OUTCOME_CODES:
                    code = "DELIVERY_UNSUPPORTED" if receipt.delivery_status == "UNSUPPORTED" else "ADAPTER_FAILED"
                receipt = outcome(code, receipt.delivery_status)
            # Only allowlisted diagnostics persist. Discard all raw witness
            # payloads (cookies, URLs, tokens, transcript text and fake flags).
            store.record_outcome(event_id, route_id, generation,
                                 delivery_status=receipt.delivery_status,
                                 chat_delivery_proven=False, error_code=receipt.error_code)
            return receipt
    except OriginError as exc:
        # All OriginError messages above are fixed codes, never transport text.
        return outcome(str(exc), "UNKNOWN" if generation else "UNSUPPORTED")
