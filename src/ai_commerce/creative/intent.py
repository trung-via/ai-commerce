"""Optional, immutable interpretation alongside an unchanged V1 ContentBrief."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Literal

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, exact_fields, read_json, require_text
from .briefs import brief_digest, intake_brief
from .models import ProviderObservation, require_digest


@dataclass(frozen=True, slots=True)
class ActorRef:
    """Caller-observed actor reference; not authentication or chat attribution."""

    kind: Literal["UNKNOWN", "HUMAN", "AI", "SYSTEM"] = "UNKNOWN"
    ref: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("UNKNOWN", "HUMAN", "AI", "SYSTEM"):
            raise CreativeValidationError("invalid actor kind")
        if self.kind == "UNKNOWN":
            if self.ref is not None:
                raise CreativeValidationError("unknown actor cannot assert a reference")
        else:
            require_text(self.ref, "actor.ref")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: object) -> ActorRef:
        return cls(**exact_fields(data, {"kind", "ref"}))


@dataclass(frozen=True, slots=True)
class IntentField:
    """SOURCE quotes are traceable input, never independently verified facts."""

    name: str
    value: str | None = None
    origin: Literal["SOURCE", "INFERRED", "UNKNOWN"] = "UNKNOWN"
    source_quote: str | None = None

    def __post_init__(self) -> None:
        require_text(self.name, "field.name")
        if self.origin not in ("SOURCE", "INFERRED", "UNKNOWN"):
            raise CreativeValidationError("invalid intent field origin")
        if self.origin == "UNKNOWN":
            if self.value is not None or self.source_quote is not None:
                raise CreativeValidationError("unknown intent field cannot assert a value")
        else:
            require_text(self.value, "field.value")
            if self.origin == "SOURCE":
                require_text(self.source_quote, "field.source_quote")
            elif self.source_quote is not None:
                raise CreativeValidationError("inferred hypotheses cannot claim source quotes")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> IntentField:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class CreativeIntent:
    brief_id: str
    source_brief_digest: str
    raw_text: str
    fields: tuple[IntentField, ...] = ()
    actor: ActorRef = ActorRef()
    observation: ProviderObservation = ProviderObservation()
    parent_intent_digest: str | None = None
    schema_version: str = "a03.intent.v1"

    def __post_init__(self) -> None:
        require_text(self.brief_id, "brief_id")
        require_digest(self.source_brief_digest)
        require_text(self.raw_text, "raw_text")
        if self.schema_version != "a03.intent.v1":
            raise CreativeValidationError("unsupported intent schema")
        if self.parent_intent_digest is not None:
            require_digest(self.parent_intent_digest)
        if not isinstance(self.actor, ActorRef) or not isinstance(self.observation, ProviderObservation):
            raise CreativeValidationError("expected typed intent metadata")
        ActorRef.from_dict(self.actor.to_dict())
        ProviderObservation.from_dict(self.observation.to_dict())
        if not isinstance(self.fields, (tuple, list)) or any(not isinstance(f, IntentField) for f in self.fields):
            raise CreativeValidationError("expected typed intent fields")
        object.__setattr__(self, "fields", tuple(IntentField.from_dict(f.to_dict()) for f in self.fields))
        if len({f.name for f in self.fields}) != len(self.fields):
            raise CreativeValidationError("duplicate intent field")
        for item in self.fields:
            if item.origin == "SOURCE" and item.source_quote not in self.raw_text:
                raise CreativeValidationError("source quote must occur verbatim in raw_text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "brief_id": self.brief_id,
            "source_brief_digest": self.source_brief_digest, "raw_text": self.raw_text,
            "fields": [f.to_dict() for f in self.fields], "actor": self.actor.to_dict(),
            "observation": self.observation.to_dict(), "parent_intent_digest": self.parent_intent_digest,
        }

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    def to_json(self) -> str:
        return canonical_bytes(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, data: object) -> CreativeIntent:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if not isinstance(value["fields"], list):
            raise CreativeValidationError("expected intent fields array")
        value["fields"] = tuple(IntentField.from_dict(f) for f in value["fields"])
        value["actor"] = ActorRef.from_dict(value["actor"])
        value["observation"] = ProviderObservation.from_dict(value["observation"])
        return cls(**value)

    @classmethod
    def from_json(cls, text: str) -> CreativeIntent:
        return cls.from_dict(read_json(text))


def capture_intent(brief: Any, *, raw_text: str | None = None, **metadata: Any) -> CreativeIntent:
    """Capture supplied interpretation only; no automatic semantic extraction.

    String inputs retain exact text, including JSON formatting. Mapping inputs
    use prompt/objective unless the caller supplies the original text.
    """
    valid = intake_brief(brief)
    if raw_text is None:
        raw_text = brief if isinstance(brief, str) else valid.get("prompt") or valid["objective"]
    return CreativeIntent(valid["brief_id"], brief_digest(valid), raw_text, **metadata)
