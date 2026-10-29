"""Opt-in V2 creative requests and proposals; V1 APIs and identities stay intact."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, fields
from typing import Any, Literal, Protocol

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, exact_fields, positive_int, read_json, require_text
from .briefs import brief_digest, intake_brief
from .intent import CreativeIntent
from .models import (
    EDITABLE_FIELDS, ConceptChange, ConceptDraft, ConceptRecord, ProviderObservation, concept_identity,
)
from .reasoner import reframe_concept, revise_concept


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    """Requested target, not evidence of which model actually answered."""

    provider: str
    model: str

    def __post_init__(self) -> None:
        require_text(self.provider, "configured provider")
        require_text(self.model, "configured model")

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}


@dataclass(frozen=True, slots=True)
class UsageObservation:
    """Only adapter-reported measurements; missing values are never zero."""

    status: Literal["UNKNOWN", "OBSERVED"] = "UNKNOWN"
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_amount: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("UNKNOWN", "OBSERVED"):
            raise CreativeValidationError("invalid usage status")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise CreativeValidationError("usage tokens must be nonnegative integers or null")
        if self.cost_amount is not None:
            if type(self.cost_amount) not in (int, float) or not math.isfinite(self.cost_amount) or self.cost_amount < 0:
                raise CreativeValidationError("invalid observed cost")
            require_text(self.currency, "usage.currency")
        elif self.currency is not None:
            raise CreativeValidationError("currency needs an observed cost")
        measured = any(v is not None for v in (self.input_tokens, self.output_tokens, self.cost_amount))
        if (self.status == "UNKNOWN" and measured) or (self.status == "OBSERVED" and not measured):
            raise CreativeValidationError("usage status disagrees with measurements")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> UsageObservation:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class ReasoningRequest:
    """Canonical detached brief plus optional intent and exact source revision."""

    brief_json: str
    operation: Literal["EXPLORE", "REFRAME", "DEVELOP", "CRITIQUE"] = "EXPLORE"
    mode: Literal["diverge", "controlled_variants"] = "diverge"
    count: int | None = None
    intent: CreativeIntent | None = None
    source: ConceptRecord | None = None
    instruction: str | None = None
    lens: str | None = None
    schema_version: str = "a03.reasoning-request.v2"

    def __post_init__(self) -> None:
        valid = intake_brief(read_json(self.brief_json))
        object.__setattr__(self, "brief_json", canonical_bytes(valid).decode("utf-8"))
        if self.schema_version != "a03.reasoning-request.v2":
            raise CreativeValidationError("unsupported reasoning request schema")
        if self.operation not in ("EXPLORE", "REFRAME", "DEVELOP", "CRITIQUE"):
            raise CreativeValidationError("invalid creative operation")
        if self.mode not in ("diverge", "controlled_variants"):
            raise CreativeValidationError("invalid exploration mode")
        if self.count is not None:
            positive_int(self.count, "count")
            if self.operation != "EXPLORE":
                raise CreativeValidationError("count belongs to exploration")
        if self.instruction is not None:
            require_text(self.instruction, "instruction")
        digest = brief_digest(valid)
        for item, expected in ((self.intent, CreativeIntent), (self.source, ConceptRecord)):
            if item is not None:
                if not isinstance(item, expected):
                    raise CreativeValidationError("invalid typed creative request context")
                expected.from_dict(item.to_dict())
                if item.brief_id != valid["brief_id"] or item.source_brief_digest != digest:
                    raise CreativeValidationError("request source brief revision mismatch")
        if self.operation == "EXPLORE":
            if self.source is not None:
                raise CreativeValidationError("exploration has no parent concept; use develop or reframe")
        elif self.source is None:
            raise CreativeValidationError("creative operation requires an exact source concept")
        if self.operation == "REFRAME":
            require_text(self.lens, "lens")
        elif self.lens is not None:
            raise CreativeValidationError("lens belongs to reframe")

    @classmethod
    def create(cls, brief: Any, **options: Any) -> ReasoningRequest:
        return cls(canonical_bytes(intake_brief(brief)).decode("utf-8"), **options)

    @property
    def brief(self) -> dict[str, Any]:
        return intake_brief(read_json(self.brief_json))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "brief": self.brief,
            "operation": self.operation, "mode": self.mode, "count": self.count,
            "intent": self.intent.to_dict() if self.intent else None,
            "source": self.source.to_dict() if self.source else None,
            "instruction": self.instruction, "lens": self.lens,
        }

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ReasoningRequest:
        value = exact_fields(data, {"schema_version", "brief", "operation", "mode", "count",
                                    "intent", "source", "instruction", "lens"})
        value["brief_json"] = canonical_bytes(intake_brief(value.pop("brief"))).decode("utf-8")
        if value["intent"] is not None:
            value["intent"] = CreativeIntent.from_dict(value["intent"])
        if value["source"] is not None:
            value["source"] = ConceptRecord.from_dict(value["source"])
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ReasoningResult:
    request: ReasoningRequest
    state: Literal["OK", "NO_MODEL_CONFIGURED", "UNAUTHORIZED", "REFUSED", "ERROR"]
    drafts: tuple[ConceptDraft, ...] = ()
    edits: tuple[ConceptChange, ...] = ()
    critique: str | None = None
    observation: ProviderObservation = ProviderObservation()
    usage: UsageObservation = UsageObservation()
    configuration: ModelConfiguration | None = None
    provider_request_id: str | None = None
    error_code: str | None = None
    message: str | None = None
    schema_version: str = "a03.reasoning-result.v2"

    def __post_init__(self) -> None:
        if not isinstance(self.request, ReasoningRequest):
            raise CreativeValidationError("expected typed reasoning request")
        ReasoningRequest.from_dict(self.request.to_dict())
        if self.schema_version != "a03.reasoning-result.v2":
            raise CreativeValidationError("unsupported reasoning result schema")
        if self.state not in ("OK", "NO_MODEL_CONFIGURED", "UNAUTHORIZED", "REFUSED", "ERROR"):
            raise CreativeValidationError("invalid reasoning state")
        if not isinstance(self.observation, ProviderObservation) or not isinstance(self.usage, UsageObservation):
            raise CreativeValidationError("expected typed result metadata")
        ProviderObservation.from_dict(self.observation.to_dict())
        if self.configuration is not None:
            if not isinstance(self.configuration, ModelConfiguration):
                raise CreativeValidationError("expected typed model configuration")
            ModelConfiguration(**self.configuration.to_dict())
        UsageObservation.from_dict(self.usage.to_dict())
        for name, expected in (("drafts", ConceptDraft), ("edits", ConceptChange)):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)) or any(not isinstance(v, expected) for v in value):
                raise CreativeValidationError("expected typed creative output")
            object.__setattr__(self, name, tuple(expected.from_dict(v.to_dict()) for v in value))
        if self.provider_request_id is not None:
            require_text(self.provider_request_id, "provider_request_id")
        if self.state != "OK":
            if self.drafts or self.edits or self.critique is not None:
                raise CreativeValidationError("non-success cannot contain creative output")
            require_text(self.error_code, "error_code")
            require_text(self.message, "message")
            if self.state in ("NO_MODEL_CONFIGURED", "UNAUTHORIZED") and (
                self.observation != ProviderObservation() or self.usage != UsageObservation()
                or self.provider_request_id is not None
            ):
                raise CreativeValidationError("uncalled provider cannot claim observations")
            return
        if self.error_code is not None or self.message is not None:
            raise CreativeValidationError("successful output cannot assert an error")
        op = self.request.operation
        if op == "EXPLORE":
            if not self.drafts or self.edits or self.critique is not None:
                raise CreativeValidationError("exploration needs concepts only")
            if self.request.count is not None and len(self.drafts) != self.request.count:
                raise CreativeValidationError("result count disagrees with explicit request")
            exact, families, mechanisms = set(), set(), set()
            known_refs = set(self.request.brief.get("source_refs", []))
            for draft in self.drafts:
                if not set(draft.source_refs) <= known_refs:
                    raise CreativeValidationError("source_refs must come from the source brief")
                # Metadata differences cannot disguise exactly repeated creative output.
                content = tuple(getattr(draft, name) for name in sorted(EDITABLE_FIELDS))
                if content in exact:
                    raise CreativeValidationError("repeated exact creative output")
                exact.add(content)
                family = unicodedata.normalize("NFKC", draft.family).strip().casefold()
                mechanism = tuple(unicodedata.normalize("NFKC", s).strip().casefold()
                                  for s in (draft.premise, draft.visual_direction))
                if self.request.mode == "diverge" and (family in families or mechanism in mechanisms):
                    raise CreativeValidationError("diverge requires distinct families and mechanisms")
                families.add(family)
                mechanisms.add(mechanism)
        elif op in ("REFRAME", "DEVELOP"):
            if self.drafts or not self.edits or self.critique is not None:
                raise CreativeValidationError("reframe/develop returns proposed edits only")
            if len({e.field for e in self.edits}) != len(self.edits):
                raise CreativeValidationError("duplicate proposed edit")
            if any(e.before != getattr(self.request.source, e.field) for e in self.edits):
                raise CreativeValidationError("proposed edit does not match exact source revision")
        else:
            if self.drafts or self.edits:
                raise CreativeValidationError("critique cannot change a concept or create a gate")
            require_text(self.critique, "critique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "request": self.request.to_dict(), "state": self.state,
            "drafts": [d.to_dict() for d in self.drafts], "edits": [e.to_dict() for e in self.edits],
            "critique": self.critique, "observation": self.observation.to_dict(), "usage": self.usage.to_dict(),
            "configuration": self.configuration.to_dict() if self.configuration else None,
            "provider_request_id": self.provider_request_id, "error_code": self.error_code, "message": self.message,
        }

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ReasoningResult:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["request"] = ReasoningRequest.from_dict(value["request"])
        for name, expected in (("drafts", ConceptDraft), ("edits", ConceptChange)):
            if not isinstance(value[name], list):
                raise CreativeValidationError("expected creative output array")
            value[name] = tuple(expected.from_dict(v) for v in value[name])
        value["observation"] = ProviderObservation.from_dict(value["observation"])
        value["usage"] = UsageObservation.from_dict(value["usage"])
        if value["configuration"] is not None:
            value["configuration"] = ModelConfiguration(**exact_fields(value["configuration"], {"provider", "model"}))
        return cls(**value)

    def concepts(self) -> tuple[ConceptRecord, ...]:
        """Materialize unsaved V1-compatible records, with actual output metadata."""
        if self.state != "OK" or self.request.operation != "EXPLORE":
            raise CreativeValidationError("only successful exploration has concepts")
        brief = self.request.brief
        digest = brief_digest(brief)
        return tuple(ConceptRecord(
            brief_id=brief["brief_id"], source_brief_digest=digest,
            concept_id=concept_identity(brief["brief_id"], digest, draft, self.observation),
            revision=1, family=draft.family, title=draft.title, premise=draft.premise,
            visual_direction=draft.visual_direction, source_refs=draft.source_refs,
            assumptions=draft.assumptions, observation=self.observation,
        ) for draft in self.drafts)

    def proposed_revision(self) -> ConceptRecord:
        """Unsaved child; original generation observation remains unchanged.

        The separate result retains this editing operation's observation/usage.
        A deliberate CreativeSidecarStore.save_revision commits the proposal.
        """
        if self.state != "OK" or self.request.operation not in ("REFRAME", "DEVELOP"):
            raise CreativeValidationError("result has no revision proposal")
        changes = {e.field: e.after for e in self.edits}
        if self.request.operation == "REFRAME":
            return reframe_concept(self.request.source, lens=self.request.lens, changes=changes)
        return revise_concept(self.request.source, changes)


class CreativeReasonerV2(Protocol):
    def reason(self, request: ReasoningRequest) -> ReasoningResult: ...


def reason_v2(request: ReasoningRequest, reasoner: CreativeReasonerV2 | None = None) -> ReasoningResult:
    """Opt-in execution, without a default provider or automatic retry."""
    if not isinstance(request, ReasoningRequest):
        raise CreativeValidationError("expected ReasoningRequest")
    valid = ReasoningRequest.from_dict(request.to_dict())
    if reasoner is None:
        return ReasoningResult(valid, "NO_MODEL_CONFIGURED", error_code="NO_MODEL_CONFIGURED",
                               message="Explicitly configure a V2 creative reasoner")
    try:
        result = reasoner.reason(valid)
        if not isinstance(result, ReasoningResult):
            raise CreativeValidationError("reasoner must return a typed result")
        result = ReasoningResult.from_dict(result.to_dict())
        if result.request != valid:
            raise CreativeValidationError("reasoner rebound the request or source revision")
        return result
    except CreativeValidationError:
        return ReasoningResult(valid, "ERROR", error_code="INVALID_REASONER_RESULT",
                               message="Creative reasoner returned an invalid result")
    except Exception:
        return ReasoningResult(valid, "ERROR", error_code="REASONER_ERROR",
                               message="Creative reasoner failed; no retry performed")
