"""Production snapshots and explicitly authorized SOURCE_ONLY local P1/R4 handoff."""

from .contracts import (
    A03PackageHandoff, CONSUMER_ROUTES, LOCAL_ROUTES, LocalAuthorization, LocalHandoff,
    OrchestrationError, ProductionStageCompleted,
    ProductionStageDispatched, ProductionStageReceipt, Provenance,
    ROUTE_OUTPUT_CONTRACTS, RouteAuthorization, StageSpec, bytes_digest, canonical_bytes, digest,
)
from .orchestrator import DispatchResult, ProductionPipelineOrchestrator

__all__ = [
    "A03PackageHandoff", "CONSUMER_ROUTES", "LOCAL_ROUTES", "LocalAuthorization", "LocalHandoff",
    "DispatchResult", "OrchestrationError",
    "ProductionPipelineOrchestrator", "ProductionStageCompleted",
    "ProductionStageDispatched", "ProductionStageReceipt", "Provenance",
    "ROUTE_OUTPUT_CONTRACTS", "RouteAuthorization", "StageSpec",
    "bytes_digest", "canonical_bytes", "digest",
]
