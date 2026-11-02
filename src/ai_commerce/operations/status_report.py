"""A15 O07: read-only stage and cost summary with honest commercial unknowns."""
from __future__ import annotations
from ai_commerce.operations.cost_ledger import CostLedger, CostEvidenceError
from ai_commerce.operations.manifest import AssetJobManifest
from ai_commerce.operations.retry_guard import RetryGuard


def media_job_status(manifest: AssetJobManifest, ledger: CostLedger,
                     entry_ids: tuple[str, ...], *,
                     guard: RetryGuard | None = None,
                     intent_id: str | None = None,
                     last_attempt: int = 0) -> dict:
    """Projection only. Does not poll, charge, publish, retry or fetch artifacts."""
    if not isinstance(manifest, AssetJobManifest):
        raise ValueError("INVALID_MEDIA_MANIFEST")
    for entry_id in entry_ids:
        entry = ledger.get(entry_id)
        if entry.job_id != manifest.job_id or entry.attempt != manifest.attempt:
            raise CostEvidenceError("COST_ENTRY_JOB_ATTEMPT_MISMATCH")
    if guard is None and intent_id is not None or guard is not None and intent_id is None:
        raise ValueError("RETRY_INTENT_BINDING_REQUIRED")
    retry = (guard.decision(intent_id, last_attempt=last_attempt)
             if guard is not None else {
                 "decision": "RECONCILE_REQUIRED",
                 "reason": "NO_INDEPENDENT_RETRY_PROOF",
                 "provider_called": False, "retry_dispatched": False,
             })
    return {
        "schema": "A15_OPERATIONS_STATUS_V1",
        "job_id": manifest.job_id,
        "attempt": manifest.attempt,
        "stage": manifest.stage,
        "asset_job_state": manifest.state,
        "asset_retry_safety": manifest.retry_safety,
        "cost_observations": ledger.summarize(entry_ids),
        "retry_decision": retry,
        "publication_status": manifest.result["publication_status"],
        "link_status": "UNKNOWN",
        "click_status": "UNKNOWN",
        "order_status": "UNKNOWN",
        "settlement_status": "UNKNOWN",
        "provider_called": False,
        "money_moved": False,
    }
