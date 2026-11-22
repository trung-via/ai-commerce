"""A03 product creative logic, independent of engineering executor workflows."""

from ._validation import CreativeValidationError, NoReasonerConfigured, VersionConflict
from .briefs import brief_digest, intake_brief, revise_brief
from .models import (
    AudioPlan, CameraSpec, ConceptChange, ConceptDraft, ConceptRecord, ConceptSelection,
    EDITABLE_PACKAGE_FIELDS, IdentityContinuity, MediaOutputTarget, ParentRevision,
    ProductionLineage, ProductionPackageParent, ProductionPackageSpec, ProductionScene,
    ProviderObservation, QAExpectation, RightsAttribution, SceneTransition, ScriptSegment,
)
from .reasoner import CreativeReasoner, MockCreativeReasoner, generate_concepts, reframe_concept, revise_concept
from .store import CreativeStore

__all__ = [
    "ConceptChange", "ConceptDraft", "ConceptRecord", "ConceptSelection",
    "CreativeReasoner", "CreativeStore", "CreativeValidationError",
    "MockCreativeReasoner", "NoReasonerConfigured", "ParentRevision", "ProviderObservation",
    "VersionConflict", "brief_digest", "generate_concepts", "intake_brief",
    "reframe_concept", "revise_brief", "revise_concept",
]

# V1.1 is opt-in; importing these symbols creates no provider or storage.
from .intent import ActorRef, CreativeIntent, IntentField, capture_intent
from .decisions import AIInferenceBinding, ActorDecision, CreativeModelSelection, HumanSelectionEvent
from .v2 import (
    A03CreativeJob, A03CreativeStageResult, CLIExecutionObservation, CreativeJobProvenance, CreativeReasonerV2,
    ReasoningRequest, ReasoningResult, UsageObservation, reason_v2, run_creative_stage,
    A03CreativeCycleJob, A03CreativeCycleResult, CreativeCycleContext,
    CreativeCriticEvidence, CreativeEvidenceSource, run_creative_cycle,
    A03CreativeProductionJob, A03CreativeProductionPackage, A03CreativeProductionResult,
    CreativeProductionContext, package_identity, revise_production_package, run_creative_production,
    A03BenchmarkCase, A03BenchmarkComparison, A03BenchmarkObservation,
    BENCHMARK_BRIEF_CLASSES, BENCHMARK_ROLES, BENCHMARK_RUBRIC_VERSION,
    BENCHMARK_STRATEGIES, BENCHMARK_STRATEGY_DESCRIPTIONS,
    BenchmarkConsumerEvidence, BenchmarkJudgeReceipt, BenchmarkNSMScore,
    BenchmarkRoleTokenSummary, BenchmarkRoleTrace, BenchmarkRubricScores,
    BenchmarkStrategySummary, BenchmarkStructuralCheck, BenchmarkTelemetry,
    NSM_V1_REFERENCES, NSM_V1_RUBRIC_WEIGHTS, NSM_V1_VERSION, NSM_V1_WEIGHTS,
    aggregate_benchmark_telemetry, calculate_nsm_v1_score, compare_benchmark_observations,
    evaluate_benchmark_structure, observe_benchmark_run,
)
from .live import (
    AIReasoningTask, AntigravityCliCreativeClient, AntigravityCliPreflight,
    AntigravityCliTerminalReceipt, AntigravityCliTurn,
    CodexCliCapability, CodexCliCreativeClient, InvocationAuthorization,
    LiveCreativeReasoner, ModelConfiguration, ReasoningProviderRoute, StructuredCreativeClient,
    StructuredModelRequest, StructuredModelResponse, assess_brain_cdp_turn, codex_creative_argv,
    select_reasoning_provider, verified_model_transport, verified_reasoning_receipt,
)
from .sidecars import CreativeSidecarStore

__all__ += [
    "A03CreativeJob", "A03CreativeStageResult", "AIInferenceBinding", "CreativeJobProvenance",
    "ActorDecision", "ActorRef", "CreativeIntent", "CreativeReasonerV2", "CreativeSidecarStore",
    "HumanSelectionEvent", "IntentField", "InvocationAuthorization", "LiveCreativeReasoner",
    "ModelConfiguration", "ReasoningRequest", "ReasoningResult", "StructuredCreativeClient",
    "StructuredModelRequest", "StructuredModelResponse", "UsageObservation", "capture_intent", "reason_v2",
    "run_creative_stage",
    "CLIExecutionObservation", "CodexCliCapability", "CodexCliCreativeClient", "codex_creative_argv",
    "AntigravityCliCreativeClient", "AntigravityCliPreflight", "AntigravityCliTerminalReceipt", "AntigravityCliTurn",
    "AIReasoningTask", "ReasoningProviderRoute", "assess_brain_cdp_turn",
    "select_reasoning_provider", "verified_model_transport", "verified_reasoning_receipt",
    "A03CreativeCycleJob", "A03CreativeCycleResult", "CreativeCycleContext",
    "CreativeCriticEvidence", "CreativeEvidenceSource", "CreativeModelSelection", "run_creative_cycle",
    "A03CreativeProductionJob", "A03CreativeProductionPackage", "A03CreativeProductionResult",
    "AudioPlan", "CameraSpec", "CreativeProductionContext", "EDITABLE_PACKAGE_FIELDS",
    "IdentityContinuity", "MediaOutputTarget", "ProductionLineage", "ProductionPackageParent",
    "ProductionPackageSpec", "ProductionScene", "QAExpectation", "RightsAttribution",
    "SceneTransition", "ScriptSegment", "package_identity", "revise_production_package",
    "run_creative_production",
    "A03BenchmarkCase", "A03BenchmarkComparison", "A03BenchmarkObservation",
    "BENCHMARK_BRIEF_CLASSES", "BENCHMARK_ROLES", "BENCHMARK_RUBRIC_VERSION",
    "BENCHMARK_STRATEGIES", "BENCHMARK_STRATEGY_DESCRIPTIONS",
    "BenchmarkConsumerEvidence", "BenchmarkJudgeReceipt", "BenchmarkNSMScore",
    "BenchmarkRoleTokenSummary", "BenchmarkRoleTrace", "BenchmarkRubricScores",
    "BenchmarkStrategySummary", "BenchmarkStructuralCheck", "BenchmarkTelemetry",
    "NSM_V1_REFERENCES", "NSM_V1_RUBRIC_WEIGHTS", "NSM_V1_VERSION", "NSM_V1_WEIGHTS",
    "aggregate_benchmark_telemetry", "calculate_nsm_v1_score", "compare_benchmark_observations",
    "evaluate_benchmark_structure", "observe_benchmark_run",
]
