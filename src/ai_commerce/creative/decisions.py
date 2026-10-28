"""Actor decisions are separate from V1's immutable revision pin."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Literal

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, exact_fields, require_text
from .intent import ActorRef
from .models import require_digest


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
class ActorDecision:
    selection_id: str
    kind: Literal["AUTO_PINNED", "AI_RECOMMENDED", "HUMAN_SELECTED"]
    actor: ActorRef
    evidence_ref: str
    human_event: HumanSelectionEvent | None = None
    schema_version: str = "a03.actor-decision.v1"

    def __post_init__(self) -> None:
        require_selection_id(self.selection_id)
        require_text(self.evidence_ref, "evidence_ref")
        if self.schema_version != "a03.actor-decision.v1":
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "selection_id": self.selection_id,
            "kind": self.kind, "actor": self.actor.to_dict(), "evidence_ref": self.evidence_ref,
            "human_event": self.human_event.to_dict() if self.human_event else None,
        }

    @property
    def decision_id(self) -> str:
        return "decision:" + sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ActorDecision:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["actor"] = ActorRef.from_dict(value["actor"])
        if value["human_event"] is not None:
            value["human_event"] = HumanSelectionEvent.from_dict(value["human_event"])
        return cls(**value)
