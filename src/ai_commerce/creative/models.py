"""Validated immutable concept records; no field can claim verified facts."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any, Literal

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import (
    CreativeValidationError, exact_fields, positive_int, read_json, require_text, text_tuple,
)

EDITABLE_FIELDS = frozenset({"title", "premise", "visual_direction", "family"})
_DIGEST = re.compile(r"[0-9a-f]{64}")


def require_digest(value: object) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise CreativeValidationError("expected lowercase SHA-256 digest")
    return value


def require_concept_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("concept:"):
        raise CreativeValidationError("invalid concept_id")
    require_digest(value[8:])
    return value


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    """Adapter-reported identity, never inferred from credentials or chat context."""

    provider: str = "UNKNOWN"
    model: str = "UNKNOWN"
    mode: Literal["UNKNOWN", "MODEL", "TEST_ONLY_MOCK"] = "UNKNOWN"

    def __post_init__(self) -> None:
        require_text(self.provider, "provider")
        require_text(self.model, "model")
        if self.mode not in ("UNKNOWN", "MODEL", "TEST_ONLY_MOCK"):
            raise CreativeValidationError("invalid provider mode")
        if self.provider == "deterministic-test-only" and (
            self.mode != "TEST_ONLY_MOCK" or self.model != "UNKNOWN"
        ):
            raise CreativeValidationError("test-only mock cannot be relabeled as a model")

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model, "mode": self.mode}

    @classmethod
    def from_dict(cls, data: object) -> ProviderObservation:
        return cls(**exact_fields(data, {"provider", "model", "mode"}))


@dataclass(frozen=True, slots=True)
class ConceptDraft:
    family: str
    title: str
    premise: str
    visual_direction: str
    source_refs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in EDITABLE_FIELDS:
            require_text(getattr(self, name), name)
        object.__setattr__(self, "source_refs", text_tuple(self.source_refs, "source_refs"))
        object.__setattr__(self, "assumptions", text_tuple(self.assumptions, "assumptions"))

    def to_dict(self) -> dict[str, Any]:
        return {
            **{name: getattr(self, name) for name in sorted(EDITABLE_FIELDS)},
            "source_refs": list(self.source_refs), "assumptions": list(self.assumptions),
        }

    @classmethod
    def from_dict(cls, data: object) -> ConceptDraft:
        return cls(**exact_fields(data, set(EDITABLE_FIELDS), {"source_refs", "assumptions"}))


def concept_identity(brief_id: str, digest: str, draft: ConceptDraft, observation: ProviderObservation) -> str:
    identity = {
        "brief_id": brief_id, "source_brief_digest": digest,
        "draft": draft.to_dict(), "observation": observation.to_dict(),
    }
    return "concept:" + sha256_bytes(canonical_bytes(identity))


@dataclass(frozen=True, slots=True)
class ParentRevision:
    concept_id: str
    revision: int
    digest: str

    def __post_init__(self) -> None:
        require_concept_id(self.concept_id)
        positive_int(self.revision, "parent.revision")
        require_digest(self.digest)

    def to_dict(self) -> dict[str, Any]:
        return {"concept_id": self.concept_id, "revision": self.revision, "digest": self.digest}

    @classmethod
    def from_dict(cls, data: object) -> ParentRevision:
        return cls(**exact_fields(data, {"concept_id", "revision", "digest"}))


@dataclass(frozen=True, slots=True)
class ConceptChange:
    field: str
    before: str
    after: str

    def __post_init__(self) -> None:
        if not isinstance(self.field, str) or self.field not in EDITABLE_FIELDS:
            raise CreativeValidationError("only creative text fields may change")
        require_text(self.before, "change.before")
        require_text(self.after, "change.after")
        if self.before == self.after:
            raise CreativeValidationError("change must be material")

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "before": self.before, "after": self.after}

    @classmethod
    def from_dict(cls, data: object) -> ConceptChange:
        return cls(**exact_fields(data, {"field", "before", "after"}))


@dataclass(frozen=True, slots=True)
class ConceptRecord:
    brief_id: str
    source_brief_digest: str
    concept_id: str
    revision: int
    family: str
    title: str
    premise: str
    visual_direction: str
    source_refs: tuple[str, ...]
    observation: ProviderObservation
    assumptions: tuple[str, ...] = ()
    parent: ParentRevision | None = None
    changes: tuple[ConceptChange, ...] = ()
    operation: Literal["GENERATE", "REVISE", "REFRAME"] = "GENERATE"
    lens: str | None = None
    factual_status: Literal["UNVERIFIED"] = "UNVERIFIED"
    rights_status: Literal["UNKNOWN"] = "UNKNOWN"
    schema_version: str = "a03.concept.v1"

    def __post_init__(self) -> None:
        require_text(self.brief_id, "brief_id")
        require_digest(self.source_brief_digest)
        require_concept_id(self.concept_id)
        positive_int(self.revision, "revision")
        if not isinstance(self.observation, ProviderObservation):
            raise CreativeValidationError("expected ProviderObservation")
        draft = ConceptDraft(
            self.family, self.title, self.premise, self.visual_direction,
            self.source_refs, self.assumptions,
        )
        object.__setattr__(self, "source_refs", draft.source_refs)
        object.__setattr__(self, "assumptions", draft.assumptions)
        if self.schema_version != "a03.concept.v1" or self.factual_status != "UNVERIFIED" or self.rights_status != "UNKNOWN":
            raise CreativeValidationError("unsupported schema or unsupported factual/rights certainty")
        if not isinstance(self.changes, (list, tuple)) or any(not isinstance(c, ConceptChange) for c in self.changes):
            raise CreativeValidationError("expected typed changes")
        object.__setattr__(self, "changes", tuple(self.changes))
        if self.revision == 1:
            if self.parent is not None or self.changes or self.operation != "GENERATE" or self.lens is not None:
                raise CreativeValidationError("initial concept cannot have revision lineage")
            if self.concept_id != concept_identity(self.brief_id, self.source_brief_digest, draft, self.observation):
                raise CreativeValidationError("concept_id does not match initial content/provenance")
        else:
            if not isinstance(self.parent, ParentRevision):
                raise CreativeValidationError("revision needs a typed parent")
            if self.parent.concept_id != self.concept_id or self.parent.revision != self.revision - 1:
                raise CreativeValidationError("invalid parent revision")
            if self.operation not in ("REVISE", "REFRAME") or not self.changes:
                raise CreativeValidationError("revision needs an operation and changes")
            if len({c.field for c in self.changes}) != len(self.changes):
                raise CreativeValidationError("duplicate change field")
            if any(getattr(self, c.field) != c.after for c in self.changes):
                raise CreativeValidationError("change.after disagrees with concept")
            if self.operation == "REFRAME":
                require_text(self.lens, "lens")
            elif self.lens is not None:
                raise CreativeValidationError("lens belongs to a separate REFRAME operation")

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result["source_refs"] = list(self.source_refs)
        result["assumptions"] = list(self.assumptions)
        result["observation"] = self.observation.to_dict()
        result["parent"] = self.parent.to_dict() if self.parent else None
        result["changes"] = [change.to_dict() for change in self.changes]
        return result

    def to_json(self) -> str:
        return canonical_bytes(self.to_dict()).decode("utf-8")

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ConceptRecord:
        value = exact_fields(data, {field.name for field in fields(cls)})
        value["observation"] = ProviderObservation.from_dict(value["observation"])
        if value["parent"] is not None:
            value["parent"] = ParentRevision.from_dict(value["parent"])
        if not isinstance(value["changes"], list):
            raise CreativeValidationError("expected changes array")
        value["changes"] = tuple(ConceptChange.from_dict(item) for item in value["changes"])
        return cls(**value)

    @classmethod
    def from_json(cls, text: str) -> ConceptRecord:
        return cls.from_dict(read_json(text))


@dataclass(frozen=True, slots=True)
class ConceptSelection:
    """An immutable choice of an exact concept revision, never a mutable pointer."""

    concept_id: str
    revision: int
    concept_digest: str
    brief_id: str
    source_brief_digest: str
    schema_version: str = "a03.selection.v1"

    def __post_init__(self) -> None:
        require_concept_id(self.concept_id)
        positive_int(self.revision, "revision")
        require_digest(self.concept_digest)
        require_digest(self.source_brief_digest)
        require_text(self.brief_id, "brief_id")
        if self.schema_version != "a03.selection.v1":
            raise CreativeValidationError("unsupported selection schema")

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @property
    def selection_id(self) -> str:
        return "selection:" + sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ConceptSelection:
        return cls(**exact_fields(data, {field.name for field in fields(cls)}))
