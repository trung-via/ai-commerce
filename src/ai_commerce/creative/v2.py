"""Opt-in V2 creative requests and proposals; V1 APIs and identities stay intact."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, exact_fields, positive_int, read_json, require_text
from .briefs import brief_digest, intake_brief
from .decisions import AIInferenceBinding, ActorDecision
from .intent import ActorRef, CreativeIntent
from .models import (
    EDITABLE_FIELDS, ConceptChange, ConceptDraft, ConceptRecord, ConceptSelection,
    ProviderObservation, concept_identity, require_digest,
)
from .reasoner import reframe_concept, revise_concept

if TYPE_CHECKING:
    from .sidecars import CreativeSidecarStore
    from .store import CreativeStore


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    """Requested target, not evidence of which model actually answered."""

    provider: str
    model: str
    effort: str | None = None

    def __post_init__(self) -> None:
        require_text(self.provider, "configured provider")
        require_text(self.model, "configured model")
        if self.effort is not None:
            require_text(self.effort, "requested effort")

    def to_dict(self) -> dict[str, str]:
        # Preserve historical V2 bytes when no effort was requested.
        return {"provider": self.provider, "model": self.model,
                **({"effort": self.effort} if self.effort is not None else {})}

    @classmethod
    def from_dict(cls, data: object) -> ModelConfiguration:
        return cls(**exact_fields(data, {"provider", "model"}, {"effort"}))


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
            value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
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


@dataclass(frozen=True, slots=True)
class CreativeJobProvenance:
    """Caller-supplied lineage, never authentication or provider proof."""

    actor_ref: str
    source_ref: str
    source_sha256: str
    evidence_type: Literal["HUMAN_SUPPLIED", "SYNTHETIC_FIXTURE", "UNKNOWN"] = "UNKNOWN"

    def __post_init__(self) -> None:
        require_text(self.actor_ref, "provenance.actor_ref")
        require_text(self.source_ref, "provenance.source_ref")
        require_digest(self.source_sha256)
        if self.evidence_type not in ("HUMAN_SUPPLIED", "SYNTHETIC_FIXTURE", "UNKNOWN"):
            raise CreativeValidationError("invalid creative provenance")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> CreativeJobProvenance:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class A03CreativeJob:
    """One explicit attempt at a V2 task, carrying ContentBrief.v0.1 unchanged.

    Configuration is REQUESTED identity/effort, not observed model evidence.
    A new attempt requires a new attempt number; the owner never retries itself.
    """

    journey_id: str
    cycle_id: str
    job_id: str
    attempt: int
    correlation_id: str
    provenance: CreativeJobProvenance
    request: ReasoningRequest
    brief_id: str
    source_brief_digest: str
    request_digest: str
    configuration: ModelConfiguration | None = None
    purpose: Literal["PRODUCTION", "TEST_ONLY_MOCK"] = "PRODUCTION"
    selection_index: int | None = None
    schema_version: str = "A03CreativeJob.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "A03CreativeJob.v1":
            raise CreativeValidationError("unsupported creative job schema")
        for name in ("journey_id", "cycle_id", "job_id", "correlation_id", "brief_id"):
            require_text(getattr(self, name), name)
        positive_int(self.attempt, "attempt")
        if not isinstance(self.provenance, CreativeJobProvenance) or not isinstance(self.request, ReasoningRequest):
            raise CreativeValidationError("expected typed creative job context")
        CreativeJobProvenance.from_dict(self.provenance.to_dict())
        ReasoningRequest.from_dict(self.request.to_dict())
        require_digest(self.source_brief_digest)
        require_digest(self.request_digest)
        if (self.brief_id != self.request.brief["brief_id"]
                or self.source_brief_digest != brief_digest(self.request.brief)
                or self.request_digest != self.request.digest):
            raise CreativeValidationError("creative job request/source hash mismatch")
        if self.configuration is not None:
            if not isinstance(self.configuration, ModelConfiguration):
                raise CreativeValidationError("expected requested model configuration")
            ModelConfiguration.from_dict(self.configuration.to_dict())
        if self.purpose not in ("PRODUCTION", "TEST_ONLY_MOCK"):
            raise CreativeValidationError("invalid creative job purpose")
        if self.selection_index is not None:
            if type(self.selection_index) is not int or self.selection_index < 0:
                raise CreativeValidationError("selection_index must be a nonnegative integer or null")
            if (self.request.operation == "CRITIQUE"
                    or (self.request.operation != "EXPLORE" and self.selection_index != 0)
                    or (self.request.count is not None and self.selection_index >= self.request.count)):
                raise CreativeValidationError("selection_index outside requested output")

    @classmethod
    def create(cls, request: ReasoningRequest, **context: Any) -> A03CreativeJob:
        if not isinstance(request, ReasoningRequest):
            raise CreativeValidationError("expected ReasoningRequest")
        return cls(request=request, brief_id=request.brief["brief_id"],
                   source_brief_digest=brief_digest(request.brief), request_digest=request.digest, **context)

    def to_dict(self) -> dict[str, Any]:
        return {**{f.name: getattr(self, f.name) for f in fields(self)},
                "request": self.request.to_dict(), "provenance": self.provenance.to_dict(),
                "configuration": self.configuration.to_dict() if self.configuration else None}

    @classmethod
    def from_dict(cls, data: object) -> A03CreativeJob:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["request"] = ReasoningRequest.from_dict(value["request"])
        value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
        if value["configuration"] is not None:
            value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
        return cls(**value)

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @property
    def slot(self) -> str:
        return sha256_bytes(canonical_bytes({n: getattr(self, n) for n in
                                           ("journey_id", "cycle_id", "job_id", "attempt")}))


def inference_failure(result: ReasoningResult) -> str | None:
    """Whether a receipt binds an observed inference to its requested target.

    Adapter assertions need a trusted injected adapter/storage boundary. Hashes
    bind content; they do not authenticate a provider or make mocks into models.
    """
    if result.state != "OK":
        return result.error_code
    config, observed = result.configuration, result.observation
    if config is None or "UNKNOWN" in (config.provider, config.model):
        return "NO_MODEL_CONFIGURED"
    if observed.mode == "UNKNOWN" or observed.provider == "UNKNOWN" or result.provider_request_id is None:
        return "UNBOUND_PROVIDER"
    if observed.mode == "TEST_ONLY_MOCK":
        return None if observed.model == "UNKNOWN" else "INVALID_MOCK_IDENTITY"
    if observed.model == "UNKNOWN":
        return "UNBOUND_PROVIDER"
    if (observed.provider, observed.model) != (config.provider, config.model):
        return "PROVIDER_IDENTITY_MISMATCH"
    return None


def _stage_outcome(job: A03CreativeJob, result: ReasoningResult) -> tuple[str, str | None]:
    failure = inference_failure(result)
    if result.state == "NO_MODEL_CONFIGURED":
        return "INTAKE_COMPLETE", failure
    if failure is None and result.configuration != job.configuration:
        failure = "CONFIGURATION_MISMATCH"
    if failure is None and result.observation.mode == "TEST_ONLY_MOCK" and job.purpose != "TEST_ONLY_MOCK":
        failure = "MOCK_NOT_PRODUCTION"
    if failure is None and result.observation.mode == "MODEL" and job.purpose == "TEST_ONLY_MOCK":
        failure = "EXECUTION_MODE_MISMATCH"
    if failure is None and job.selection_index is not None and job.selection_index >= len(_stage_concepts(result)):
        failure = "UNKNOWN_CONCEPT_SELECTION"
    if failure is not None:
        return "FAILED", failure
    return ("TEST_ONLY_MOCK" if result.observation.mode == "TEST_ONLY_MOCK" else "CREATIVE_COMPLETE"), None


def _stage_concepts(result: ReasoningResult) -> tuple[ConceptRecord, ...]:
    if result.request.operation == "EXPLORE":
        return result.concepts()
    if result.request.operation in ("REFRAME", "DEVELOP"):
        return (result.proposed_revision(),)
    return ()


def _output_pin(concept: ConceptRecord) -> ConceptSelection:
    return ConceptSelection(concept.concept_id, concept.revision, concept.digest,
                            concept.brief_id, concept.source_brief_digest)


def inference_decision(result: ReasoningResult, selection: ConceptSelection, *,
                       kind: str = "AUTO_PINNED", actor_ref: str = "a03:run_creative_stage") -> ActorDecision:
    """Construct a candidate; saving/loading it still verifies the actual receipt."""
    evidence = "reasoning:" + result.digest
    return ActorDecision(selection.selection_id, kind,
                         ActorRef("AI", evidence) if kind == "AI_RECOMMENDED" else ActorRef("SYSTEM", actor_ref),
                         evidence, schema_version="a03.actor-decision.v2",
                         inference=AIInferenceBinding(result.digest, result.request.digest,
                             brief_digest(result.request.brief), selection.concept_digest, result.observation.mode))


@dataclass(frozen=True, slots=True)
class A03CreativeStageResult:
    """Verifiable creative receipt; intake and mock success are never model completion.

    The nested reasoning receipt retains exact observed identity, usage and
    provider request ID. Output pins reference immutable V1 concept revisions.
    """

    job: A03CreativeJob
    reasoning: ReasoningResult
    status: Literal["INTAKE_COMPLETE", "FAILED", "TEST_ONLY_MOCK", "CREATIVE_COMPLETE"]
    failure: str | None = None
    outputs: tuple[ConceptSelection, ...] = ()
    decision: ActorDecision | None = None
    schema_version: str = "A03CreativeStageResult.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "A03CreativeStageResult.v1":
            raise CreativeValidationError("unsupported creative stage schema")
        if not isinstance(self.job, A03CreativeJob) or not isinstance(self.reasoning, ReasoningResult):
            raise CreativeValidationError("expected typed creative stage context")
        A03CreativeJob.from_dict(self.job.to_dict())
        ReasoningResult.from_dict(self.reasoning.to_dict())
        if self.reasoning.request != self.job.request:
            raise CreativeValidationError("stage receipt rebound creative job request")
        if (self.status, self.failure) != _stage_outcome(self.job, self.reasoning):
            raise CreativeValidationError("stage status/failure disagrees with inference evidence")
        if not isinstance(self.outputs, (list, tuple)) or any(not isinstance(s, ConceptSelection) for s in self.outputs):
            raise CreativeValidationError("expected typed creative output references")
        object.__setattr__(self, "outputs", tuple(ConceptSelection.from_dict(s.to_dict()) for s in self.outputs))
        successful = self.status in ("TEST_ONLY_MOCK", "CREATIVE_COMPLETE")
        expected = tuple(_output_pin(c) for c in _stage_concepts(self.reasoning)) if successful else ()
        if self.outputs != expected:
            raise CreativeValidationError("stage output references do not match reasoning output")
        selected = self.job.selection_index
        expected_decision = (inference_decision(self.reasoning, expected[selected])
                             if successful and selected is not None else None)
        if self.decision != expected_decision:
            raise CreativeValidationError("stage selection does not match reasoning output")

    @property
    def reasoning_ref(self) -> str:
        return "reasoning:" + self.reasoning.digest

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "job": self.job.to_dict(),
                "reasoning": self.reasoning.to_dict(), "status": self.status, "failure": self.failure,
                "outputs": [s.to_dict() for s in self.outputs],
                "decision": self.decision.to_dict() if self.decision else None}

    @classmethod
    def from_dict(cls, data: object) -> A03CreativeStageResult:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["job"] = A03CreativeJob.from_dict(value["job"])
        value["reasoning"] = ReasoningResult.from_dict(value["reasoning"])
        if not isinstance(value["outputs"], list):
            raise CreativeValidationError("expected output reference array")
        value["outputs"] = tuple(ConceptSelection.from_dict(s) for s in value["outputs"])
        if value["decision"] is not None:
            value["decision"] = ActorDecision.from_dict(value["decision"])
        return cls(**value)


def run_creative_stage(job: A03CreativeJob, store: CreativeStore | CreativeSidecarStore,
                       reasoner: CreativeReasonerV2 | None = None) -> A03CreativeStageResult:
    """Invoke once, commit native artifacts, then return their verified receipt.

    R0 supplies no transport and never authorizes network/spend. An injected
    LiveCreativeReasoner is snapshotted with offline authorization; other injected
    reasoners are trusted caller code. R1 can extend that existing adapter's
    authorization boundary without adding a connector or changing these wire
    contracts. R2 may orchestrate explicit attempts outside this bounded owner.
    Existing A10 -> A03 intake/store remains INTAKE_COMPLETE, not model invocation.

    Completed attempts are idempotent. A reserved attempt without a terminal
    receipt fails closed, with no automatic replay after uncertain side effects.
    Storage is a caller-owned trusted directory; hashes are not authentication.
    """
    from .live import InvocationAuthorization, LiveCreativeReasoner
    from .sidecars import CreativeSidecarStore

    if not isinstance(job, A03CreativeJob):
        raise CreativeValidationError("expected A03CreativeJob")
    job = A03CreativeJob.from_dict(job.to_dict())
    sidecars = store if isinstance(store, CreativeSidecarStore) else CreativeSidecarStore(store)
    sidecars.creative.save_brief(job.request.brief)
    sidecars.validate_request(job.request)
    if not sidecars.claim_job(job):
        return sidecars.load_stage(job)
    if job.configuration is None:
        result = reason_v2(job.request)
    elif (isinstance(reasoner, LiveCreativeReasoner) and reasoner.config is not None
          and reasoner.config != job.configuration):
        result = ReasoningResult(job.request, "ERROR", error_code="CONFIGURATION_MISMATCH",
                                 message="Injected adapter does not match requested job configuration")
    elif isinstance(reasoner, LiveCreativeReasoner):
        # Capture the existing adapter before any client callback. R0 cannot
        # adopt an authorization supplied for a different, live call path.
        reasoner = LiveCreativeReasoner(reasoner.config, reasoner.client, authorization=InvocationAuthorization())
        result = reason_v2(job.request, reasoner)
    else:
        result = reason_v2(job.request, reasoner)
    sidecars.save_result(result)
    status, failure = _stage_outcome(job, result)
    outputs, decision = (), None
    if status in ("TEST_ONLY_MOCK", "CREATIVE_COMPLETE"):
        committed = tuple(sidecars.creative.save_concept(c) for c in _stage_concepts(result))
        outputs = tuple(sidecars.creative.select(c.concept_id, c.revision) for c in committed)
        if job.selection_index is not None:
            decision = inference_decision(result, outputs[job.selection_index])
            sidecars.save_decision(decision)
    stage = A03CreativeStageResult(job, result, status, failure, outputs, decision)
    sidecars.save_stage(stage)
    return sidecars.load_stage(job)
