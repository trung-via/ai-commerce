"""Actor decisions are separate from V1's immutable revision pin."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Literal

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, exact_fields, require_text, text_tuple
from .intent import ActorRef
from .models import ConceptSelection, require_digest


@dataclass(frozen=True, slots=True)
class CreativeModelSelection:
    """R2 model output, never inferred from scores or critique prose.

    The request and sidecar verifier resolve these exact critic receipts and
    candidate pins. Text is creative reasoning, not verified external evidence.
    """

    action: Literal["SELECT", "REVISE", "STOP"]
    selection: ConceptSelection | None
    critique_receipts: tuple[str, ...]
    rationale: str
    revision_instruction: str | None = None

    def __post_init__(self) -> None:
        if self.action not in ("SELECT", "REVISE", "STOP"):
            raise CreativeValidationError("invalid model selection action")
        require_text(self.rationale, "selection rationale")
        refs = text_tuple(self.critique_receipts, "critique receipts")
        for ref in refs:
            require_digest(ref)
        if not refs or len(set(refs)) != len(refs):
            raise CreativeValidationError("selection requires distinct critic receipts")
        object.__setattr__(self, "critique_receipts", refs)
        if self.action == "STOP":
            if self.selection is not None:
                raise CreativeValidationError("STOP cannot select a concept")
        elif not isinstance(self.selection, ConceptSelection):
            raise CreativeValidationError("selection requires an immutable concept pin")
        else:
            ConceptSelection.from_dict(self.selection.to_dict())
        if self.action == "REVISE":
            require_text(self.revision_instruction, "revision instruction")
        elif self.revision_instruction is not None:
            raise CreativeValidationError("revision instruction belongs to REVISE")

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "selection": self.selection.to_dict() if self.selection else None,
                "critique_receipts": list(self.critique_receipts), "rationale": self.rationale,
                "revision_instruction": self.revision_instruction}

    @classmethod
    def from_dict(cls, data: object) -> CreativeModelSelection:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if value["selection"] is not None:
            value["selection"] = ConceptSelection.from_dict(value["selection"])
        return cls(**value)


def require_selection_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("selection:"):
        raise CreativeValidationError("invalid selection_id")
    require_digest(value[10:])
    return value


@dataclass(frozen=True, slots=True)
class HumanSelectionEvent:
    """Explicit caller-supplied event evidence, not an authentication service.

    The caller must obtain this from an actual Human action. A CLI pin or model
    recommendation cannot manufacture it. References are retained for audit.
    """

    event_ref: str
    actor_ref: str
    selection_id: str
    occurred_at: str

    def __post_init__(self) -> None:
        require_text(self.event_ref, "event_ref")
        require_text(self.actor_ref, "actor_ref")
        require_selection_id(self.selection_id)
        require_text(self.occurred_at, "occurred_at")
        try:
            stamp = datetime.fromisoformat(self.occurred_at)
        except ValueError as exc:
            raise CreativeValidationError("invalid selection event timestamp") from exc
        if stamp.utcoffset() is None:
            raise CreativeValidationError("selection event timestamp needs timezone")

    def to_dict(self) -> dict[str, str]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> HumanSelectionEvent:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class AIInferenceBinding:
    """Exact saved inference/output linkage, not actor authentication.

    TEST_ONLY_MOCK bindings are fixture evidence and never live model proof.
    CreativeSidecarStore verifies every field against saved inference bytes.
    """

    receipt_digest: str
    request_digest: str
    source_brief_digest: str
    concept_digest: str
    mode: Literal["MODEL", "TEST_ONLY_MOCK"]

    def __post_init__(self) -> None:
        for name in ("receipt_digest", "request_digest", "source_brief_digest", "concept_digest"):
            require_digest(getattr(self, name))
        if self.mode not in ("MODEL", "TEST_ONLY_MOCK"):
            raise CreativeValidationError("inference binding needs an observed execution mode")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> AIInferenceBinding:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class ActorDecision:
    """V1 remains decodable history; only verified V2 inference is AI evidence."""

    selection_id: str
    kind: Literal["AUTO_PINNED", "AI_RECOMMENDED", "HUMAN_SELECTED"]
    actor: ActorRef
    evidence_ref: str
    human_event: HumanSelectionEvent | None = None
    schema_version: str = "a03.actor-decision.v1"
    inference: AIInferenceBinding | None = None

    def __post_init__(self) -> None:
        require_selection_id(self.selection_id)
        require_text(self.evidence_ref, "evidence_ref")
        if self.schema_version not in ("a03.actor-decision.v1", "a03.actor-decision.v2"):
            raise CreativeValidationError("unsupported decision schema")
        expected_actor = {"AUTO_PINNED": "SYSTEM", "AI_RECOMMENDED": "AI", "HUMAN_SELECTED": "HUMAN"}
        if not isinstance(self.kind, str) or self.kind not in expected_actor:
            raise CreativeValidationError("invalid actor decision")
        if not isinstance(self.actor, ActorRef):
            raise CreativeValidationError("expected ActorRef")
        ActorRef.from_dict(self.actor.to_dict())
        if self.actor.kind != expected_actor[self.kind]:
            raise CreativeValidationError("decision kind disagrees with actual actor kind")
        if self.kind == "HUMAN_SELECTED":
            if not isinstance(self.human_event, HumanSelectionEvent):
                raise CreativeValidationError("HUMAN_SELECTED requires an actual Human selection event")
            HumanSelectionEvent.from_dict(self.human_event.to_dict())
            if (self.human_event.actor_ref != self.actor.ref
                    or self.human_event.selection_id != self.selection_id
                    or self.human_event.event_ref != self.evidence_ref):
                raise CreativeValidationError("Human event does not match decision")
        elif self.human_event is not None:
            raise CreativeValidationError("non-Human decision cannot claim a Human event")
        if self.schema_version == "a03.actor-decision.v1" or self.kind == "HUMAN_SELECTED":
            if self.inference is not None:
                raise CreativeValidationError("historical/Human decision cannot claim inference binding")
        else:
            if not isinstance(self.inference, AIInferenceBinding):
                raise CreativeValidationError("AI decision requires typed inference binding")
            AIInferenceBinding.from_dict(self.inference.to_dict())
            if self.evidence_ref != "reasoning:" + self.inference.receipt_digest:
                raise CreativeValidationError("AI evidence reference must identify its inference receipt")
            if self.kind == "AI_RECOMMENDED" and self.actor.ref != self.evidence_ref:
                raise CreativeValidationError("AI actor reference must identify its inference, not assert authentication")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "selection_id": self.selection_id,
            "kind": self.kind, "actor": self.actor.to_dict(), "evidence_ref": self.evidence_ref,
            "human_event": self.human_event.to_dict() if self.human_event else None,
            **({"inference": self.inference.to_dict() if self.inference else None}
               if self.schema_version == "a03.actor-decision.v2" else {}),
        }

    @property
    def decision_id(self) -> str:
        return "decision:" + sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ActorDecision:
        value = exact_fields(data, {f.name for f in fields(cls)} - {"inference"}, {"inference"})
        if value["schema_version"] == "a03.actor-decision.v2" and "inference" not in value:
            raise CreativeValidationError("missing inference binding")
        if value["schema_version"] == "a03.actor-decision.v1" and "inference" in value:
            raise CreativeValidationError("unexpected inference field in historical decision")
        value["actor"] = ActorRef.from_dict(value["actor"])
        if value["human_event"] is not None:
            value["human_event"] = HumanSelectionEvent.from_dict(value["human_event"])
        if value.get("inference") is not None:
            value["inference"] = AIInferenceBinding.from_dict(value["inference"])
        return cls(**value)
