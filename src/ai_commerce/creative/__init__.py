"""A03 product creative logic, independent of engineering executor workflows."""

from ._validation import CreativeValidationError, NoReasonerConfigured, VersionConflict
from .briefs import brief_digest, intake_brief, revise_brief
from .models import ConceptChange, ConceptDraft, ConceptRecord, ConceptSelection, ParentRevision, ProviderObservation
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
from .decisions import ActorDecision, HumanSelectionEvent
from .v2 import CreativeReasonerV2, ReasoningRequest, ReasoningResult, UsageObservation, reason_v2
from .live import (
    InvocationAuthorization, LiveCreativeReasoner, ModelConfiguration,
    StructuredCreativeClient, StructuredModelRequest, StructuredModelResponse,
)
from .sidecars import CreativeSidecarStore

__all__ += [
    "ActorDecision", "ActorRef", "CreativeIntent", "CreativeReasonerV2", "CreativeSidecarStore",
    "HumanSelectionEvent", "IntentField", "InvocationAuthorization", "LiveCreativeReasoner",
    "ModelConfiguration", "ReasoningRequest", "ReasoningResult", "StructuredCreativeClient",
    "StructuredModelRequest", "StructuredModelResponse", "UsageObservation", "capture_intent", "reason_v2",
]
