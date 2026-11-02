"""A15 O07: read-only, evidence-bound cost/side-effect status projections."""
from __future__ import annotations

from ai_commerce.operations.cost_ledger import CostLedger, CostEvidenceError
from ai_commerce.operations.generation import GenerationJobRunner
from ai_commerce.operations.manifest import AssetJobManifest
from ai_commerce.operations.retry_guard import RetryGuard
from ai_commerce.operations.runner import LocalMediaRunner


def _guarded_decision(guard: RetryGuard | None, intent_id: str | None, last_attempt: int,
                      request_digest: str) -> dict:
    if (guard is None) != (intent_id is None):
        raise ValueError("RETRY_INTENT_BINDING_REQUIRED")
    if guard is None:
        return {"decision": "RECONCILE_REQUIRED",
                "reason": "NO_INDEPENDENT_RETRY_PROOF",
                "additional_provider_calls": False,
                "retry_dispatched": False}
    intent = guard.read_intent(intent_id)
    if intent["request_digest"] != request_digest:
        raise CostEvidenceError("RETRY_INTENT_DIGEST_MISMATCH")
    return guard.decision(intent_id, last_attempt=last_attempt)


def _costs(ledger: CostLedger, entry_ids: tuple[str, ...], *,
           job_id: str, attempt: int, intent_digest: str, provider: str) -> dict:
    for entry_id in entry_ids:
        entry = ledger.get(entry_id)
        if (entry.job_id != job_id or entry.attempt != attempt
                or entry.intent_digest != intent_digest or entry.provider != provider):
            raise CostEvidenceError("COST_ENTRY_SOURCE_IDENTITY_MISMATCH")
    return ledger.summarize(entry_ids)


def media_job_status(manifest: AssetJobManifest, ledger: CostLedger,
                     entry_ids: tuple[str, ...], *,
                     guard: RetryGuard | None = None,
                     intent_id: str | None = None,
                     last_attempt: int = 0) -> dict:
    """Validate persisted A04 job and output custody before any projection."""
    if not isinstance(manifest, AssetJobManifest):
        raise ValueError("INVALID_MEDIA_MANIFEST")
    actual = LocalMediaRunner(ledger.registry).load(manifest.job_id, manifest.attempt)
    if actual.to_dict() != manifest.to_dict():
        raise CostEvidenceError("MEDIA_MANIFEST_NOT_PERSISTED")
    request_digest = actual.request_digest
    provider = actual.request["provider"]["name"]
    evidence = _costs(ledger, entry_ids, job_id=actual.job_id, attempt=actual.attempt,
                      intent_digest=request_digest, provider=provider)
    retry = _guarded_decision(guard, intent_id, last_attempt, request_digest)
    return {
        "schema": "A15_OPERATIONS_STATUS_V1", "source_kind": "A04_MEDIA_V1",
        "job_id": actual.job_id, "attempt": actual.attempt,
        "stage": actual.stage, "asset_job_state": actual.state,
        "asset_retry_safety": actual.retry_safety,
        "cost_observations": evidence, "retry_decision": retry,
        "publication_status": actual.result["publication_status"],
        "link_status": "UNKNOWN", "click_status": "UNKNOWN",
        "order_status": "UNKNOWN", "settlement_status": "UNKNOWN",
        "historical_provider_side_effects": (
            "NONE" if actual.state == "DRY_RUN" else "UNKNOWN"),
        "historical_provider_charge_verified": False,
        "additional_provider_calls": False,
        "money_movement_verified": False,
    }


def generation_job_status(runner: GenerationJobRunner, job_id: str,
                          ledger: CostLedger, entry_ids: tuple[str, ...] = ()) -> dict:
    """Read an A04 V2 fixture journal; no submit/observe/fetch/reconcile calls."""
    if not isinstance(runner, GenerationJobRunner) or runner.registry.root != ledger.registry.root:
        raise CostEvidenceError("GENERATION_LEDGER_STORAGE_MISMATCH")
    request, _ = runner._request(job_id)  # existing checked, immutable intent record
    status = runner.load(job_id)          # checked replay of stored observations only
    if status.job_id != job_id or status.request_digest != request.digest:
        raise CostEvidenceError("GENERATION_RECEIPT_IDENTITY_MISMATCH")
    evidence = _costs(ledger, entry_ids, job_id=job_id, attempt=1,
                      intent_digest=request.digest, provider=request.provider)
    return {
        "schema": "A15_OPERATIONS_STATUS_V1",
        "source_kind": "A04_GENERATION_V2_SYNTHETIC",
        "job_id": job_id, "request_digest": request.digest,
        "generation_state": status.state, "generation_retry_safety": status.retry_safety,
        "side_effects": status.side_effects, "cost_status": status.cost.status,
        "cost_amount_untrusted_fixture": status.cost.amount,
        "cost_observations": evidence,
        "publication_status": "NOT_ATTEMPTED",
        "link_status": "UNKNOWN", "click_status": "UNKNOWN",
        "order_status": "UNKNOWN", "settlement_status": "UNKNOWN",
        "historical_provider_charge_verified": False,
        "additional_provider_calls": False, "money_movement_verified": False,
    }
