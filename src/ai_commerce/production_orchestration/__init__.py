"""Production snapshots and explicitly authorized SOURCE_ONLY local P1 handoff."""

from .contracts import (
    CONSUMER_ROUTES, LOCAL_ROUTES, LocalAuthorization, LocalHandoff,
    OrchestrationError, ProductionStageCompleted,
    ProductionStageDispatched, ProductionStageReceipt, Provenance,
    RouteAuthorization, StageSpec, bytes_digest, canonical_bytes, digest,
)
from .orchestrator import DispatchResult, ProductionPipelineOrchestrator

__all__ = [
    "CONSUMER_ROUTES", "LOCAL_ROUTES", "LocalAuthorization", "LocalHandoff",
    "DispatchResult", "OrchestrationError",
    "ProductionPipelineOrchestrator", "ProductionStageCompleted",
    "ProductionStageDispatched", "ProductionStageReceipt", "Provenance",
    "RouteAuthorization", "StageSpec", "bytes_digest", "canonical_bytes", "digest",
]
