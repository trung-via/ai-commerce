"""Opt-in V2 creative requests and proposals; V1 APIs and identities stay intact."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, exact_fields, positive_int, read_json, require_text, text_tuple
from .briefs import brief_digest, intake_brief
from .decisions import AIInferenceBinding, ActorDecision, CreativeModelSelection
from .intent import ActorRef, CreativeIntent
from .models import (
    EDITABLE_FIELDS, EDITABLE_PACKAGE_FIELDS, ConceptChange, ConceptDraft, ConceptRecord, ConceptSelection,
    ProductionLineage, ProductionPackageParent, ProductionPackageSpec, ProviderObservation, concept_identity,
    require_digest, require_package_id,
)
from .reasoner import reframe_concept, revise_concept

if TYPE_CHECKING:
    from .live import InvocationAuthorization, LiveCreativeReasoner
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
class CLIExecutionObservation:
    """Local process evidence, never supplied by the generated creative JSON.

    Injected process runners are always TEST_ONLY_MOCK. Digests bind evidence
    within the trusted operator/storage boundary; they are not signatures.
    """

    kind: Literal["AUTHENTICATED_CLI_PROBE", "TEST_ONLY_MOCK"]
    executable_sha256: str
    version: str
    argv_sha256: str
    probe_sha256: str
    request_digest: str
    configuration_digest: str
    stdin_sha256: str
    schema_sha256: str
    effect: Literal["PROCESS_INVOKED", "UNKNOWN"]
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None
    session_sha256: str | None = None
    observed_model: str = "UNKNOWN"
    observed_effort: str = "UNKNOWN"
    cli_thread_id: str | None = None
    authorization_ref: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("AUTHENTICATED_CLI_PROBE", "TEST_ONLY_MOCK"):
            raise CreativeValidationError("invalid CLI evidence kind")
        if self.effect not in ("PROCESS_INVOKED", "UNKNOWN"):
            raise CreativeValidationError("invalid CLI effect")
        for name in ("executable_sha256", "argv_sha256", "probe_sha256", "request_digest",
                     "configuration_digest", "stdin_sha256", "schema_sha256"):
            require_digest(getattr(self, name))
        for name in ("stdout_sha256", "stderr_sha256", "session_sha256"):
            if getattr(self, name) is not None:
                require_digest(getattr(self, name))
        for name in ("version", "observed_model", "observed_effort"):
            require_text(getattr(self, name), name)
        for name in ("cli_thread_id", "authorization_ref"):
            if getattr(self, name) is not None:
                require_text(getattr(self, name), name)

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> CLIExecutionObservation:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class CreativeEvidenceSource:
    """Caller-verified external source bytes, not model-generated citations.

    verification_ref is the trusted caller's verification record. Hash checking
    proves byte integrity within that boundary, not the truth of source claims.
    No research is performed by this module.
    """

    source_ref: str
    content: str
    content_sha256: str
    verification_ref: str

    def __post_init__(self) -> None:
        for name in ("source_ref", "content", "verification_ref"):
            require_text(getattr(self, name), name)
        require_digest(self.content_sha256)
        if (self.verification_ref == "UNKNOWN"
                or sha256_bytes(self.content.encode("utf-8")) != self.content_sha256):
            raise CreativeValidationError("external evidence needs verified exact source bytes")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> CreativeEvidenceSource:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class CreativeCriticEvidence:
    selection: ConceptSelection
    receipt_digest: str
    request_digest: str
    critique: str

    def __post_init__(self) -> None:
        if not isinstance(self.selection, ConceptSelection):
            raise CreativeValidationError("critic evidence needs a concept pin")
        ConceptSelection.from_dict(self.selection.to_dict())
        require_digest(self.receipt_digest)
        require_digest(self.request_digest)
        require_text(self.critique, "critic evidence")

    def to_dict(self) -> dict[str, Any]:
        return {"selection": self.selection.to_dict(), "receipt_digest": self.receipt_digest,
                "request_digest": self.request_digest, "critique": self.critique}

    @classmethod
    def from_dict(cls, data: object) -> CreativeCriticEvidence:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["selection"] = ConceptSelection.from_dict(value["selection"])
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CreativeCycleContext:
    """R2-only detached context; every referenced inference is resolved on disk."""

    cycle_digest: str
    step: str
    evidence: tuple[CreativeEvidenceSource, ...] = ()
    candidates: tuple[ConceptRecord, ...] = ()
    critics: tuple[CreativeCriticEvidence, ...] = ()
    selector_receipt: str | None = None

    def __post_init__(self) -> None:
        require_digest(self.cycle_digest)
        require_text(self.step, "cycle step")
        for name, kind in (("evidence", CreativeEvidenceSource), ("candidates", ConceptRecord),
                           ("critics", CreativeCriticEvidence)):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or any(not isinstance(v, kind) for v in values):
                raise CreativeValidationError("expected typed cycle context")
            object.__setattr__(self, name, tuple(kind.from_dict(v.to_dict()) for v in values))
        for values in ((e.source_ref for e in self.evidence), (c.digest for c in self.candidates),
                       (c.receipt_digest for c in self.critics)):
            values = tuple(values)
            if len(set(values)) != len(values):
                raise CreativeValidationError("duplicate cycle evidence or candidates")
        if self.selector_receipt is not None:
            require_digest(self.selector_receipt)

    @property
    def research_status(self) -> str:
        return "VERIFIED_SOURCE_BYTES" if self.evidence else "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {"cycle_digest": self.cycle_digest, "step": self.step,
                "research_status": self.research_status,
                "evidence": [e.to_dict() for e in self.evidence],
                "candidates": [c.to_dict() for c in self.candidates],
                "critics": [c.to_dict() for c in self.critics], "selector_receipt": self.selector_receipt}

    @classmethod
    def from_dict(cls, data: object) -> CreativeCycleContext:
        value = exact_fields(data, {f.name for f in fields(cls)} | {"research_status"})
        status = value.pop("research_status")
        for name, kind in (("evidence", CreativeEvidenceSource), ("candidates", ConceptRecord),
                           ("critics", CreativeCriticEvidence)):
            if not isinstance(value[name], list):
                raise CreativeValidationError("expected cycle context array")
            value[name] = tuple(kind.from_dict(v) for v in value[name])
        result = cls(**value)
        if status != result.research_status:
            raise CreativeValidationError("research status disagrees with source evidence")
        return result


def _package_rights_status(spec: ProductionPackageSpec) -> Literal["UNKNOWN", "EVIDENCE_BOUND"]:
    if spec.attributions and all(a.rights_status == "EVIDENCE_BOUND" for a in spec.attributions):
        return "EVIDENCE_BOUND"
    return "UNKNOWN"


def _validate_spec_evidence(
    spec: ProductionPackageSpec, brief: dict[str, Any],
    evidence: tuple[CreativeEvidenceSource, ...], evidence_refs: tuple[str, ...],
) -> None:
    brief_refs = set(brief.get("source_refs", []))
    evidence_by_ref = {e.source_ref: e for e in evidence}
    if not set(evidence_refs) <= evidence_by_ref.keys():
        raise CreativeValidationError("model fabricated external evidence citations")
    for attr in spec.attributions:
        if attr.source_ref not in brief_refs and attr.source_ref not in evidence_by_ref:
            raise CreativeValidationError("fabricated attribution source_ref not in source brief or evidence")
        if attr.rights_status == "EVIDENCE_BOUND":
            source = evidence_by_ref.get(attr.source_ref)
            if (source is None or source.content_sha256 != attr.evidence_sha256
                    or source.verification_ref != attr.verification_ref):
                raise CreativeValidationError("rights claim must bind to exact supplied evidence bytes")
            if attr.source_ref not in evidence_refs:
                raise CreativeValidationError("evidence-bound rights attribution must appear in evidence_refs")


def package_identity(
    brief_id: str, source_brief_digest: str, selected_concept: ConceptSelection,
    lineage: ProductionLineage, spec: ProductionPackageSpec,
    observation: ProviderObservation, reasoning_receipt_digest: str,
) -> str:
    identity = {
        "brief_id": brief_id, "source_brief_digest": source_brief_digest,
        "selected_concept": selected_concept.to_dict(), "lineage": lineage.to_dict(),
        "spec": spec.to_dict(), "observation": observation.to_dict(),
        "reasoning_receipt_digest": reasoning_receipt_digest,
    }
    return "package:" + sha256_bytes(canonical_bytes(identity))


@dataclass(frozen=True, slots=True)
class A03CreativeProductionPackage:
    """Editable, strictly typed R3 production package pinned to a verified R2 cycle.

    Creative output assertions remain GENERATED_CLAIM/HYPOTHESIS and UNVERIFIED.
    Rights remain UNKNOWN unless bound to exact caller-supplied evidence bytes.
    Edits create new immutable revisions retaining exact parent digests.
    """

    package_id: str
    revision: int
    brief_id: str
    source_brief_digest: str
    selected_concept: ConceptSelection
    lineage: ProductionLineage
    spec: ProductionPackageSpec
    observation: ProviderObservation
    usage: UsageObservation
    configuration: ModelConfiguration | None = None
    execution: CLIExecutionObservation | None = None
    provider_request_id: str | None = None
    reasoning_receipt_digest: str | None = None
    evidence_refs: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    parent: ProductionPackageParent | None = None
    changed_sections: tuple[str, ...] = ()
    edit_reason: str | None = None
    edit_actor: ActorRef | None = None
    claim_authority: Literal["GENERATED_CLAIM", "HYPOTHESIS"] = "GENERATED_CLAIM"
    factual_status: Literal["UNVERIFIED"] = "UNVERIFIED"
    rights_status: Literal["UNKNOWN", "EVIDENCE_BOUND"] = "UNKNOWN"
    schema_version: str = "A03CreativeProductionPackage.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "A03CreativeProductionPackage.v1":
            raise CreativeValidationError("unsupported production package schema")
        if self.factual_status != "UNVERIFIED":
            raise CreativeValidationError("production package factual_status must remain UNVERIFIED")
        if self.claim_authority not in ("GENERATED_CLAIM", "HYPOTHESIS"):
            raise CreativeValidationError("production package claim_authority must be GENERATED_CLAIM or HYPOTHESIS")
        if self.rights_status not in ("UNKNOWN", "EVIDENCE_BOUND"):
            raise CreativeValidationError("production package rights_status must be UNKNOWN or EVIDENCE_BOUND")
        require_package_id(self.package_id)
        positive_int(self.revision, "revision")
        require_text(self.brief_id, "brief_id")
        require_digest(self.source_brief_digest)
        if not isinstance(self.selected_concept, ConceptSelection):
            raise CreativeValidationError("expected typed selected_concept")
        ConceptSelection.from_dict(self.selected_concept.to_dict())
        if (self.selected_concept.brief_id != self.brief_id
                or self.selected_concept.source_brief_digest != self.source_brief_digest):
            raise CreativeValidationError("selected_concept does not match package source brief")
        if not isinstance(self.lineage, ProductionLineage):
            raise CreativeValidationError("expected typed ProductionLineage")
        ProductionLineage.from_dict(self.lineage.to_dict())
        for pin in self.lineage.candidate_pins:
            if pin.brief_id != self.brief_id or pin.source_brief_digest != self.source_brief_digest:
                raise CreativeValidationError("production lineage candidate rebound brief")
        if not isinstance(self.spec, ProductionPackageSpec):
            raise CreativeValidationError("expected typed ProductionPackageSpec")
        ProductionPackageSpec.from_dict(self.spec.to_dict())
        if not isinstance(self.observation, ProviderObservation) or not isinstance(self.usage, UsageObservation):
            raise CreativeValidationError("expected typed observation and usage")
        ProviderObservation.from_dict(self.observation.to_dict())
        UsageObservation.from_dict(self.usage.to_dict())
        if self.configuration is not None:
            if not isinstance(self.configuration, ModelConfiguration):
                raise CreativeValidationError("expected typed ModelConfiguration")
            ModelConfiguration.from_dict(self.configuration.to_dict())
        if self.execution is not None:
            if not isinstance(self.execution, CLIExecutionObservation):
                raise CreativeValidationError("expected typed CLIExecutionObservation")
            CLIExecutionObservation.from_dict(self.execution.to_dict())
        if self.provider_request_id is not None:
            require_text(self.provider_request_id, "provider_request_id")
        if self.reasoning_receipt_digest is not None:
            require_digest(self.reasoning_receipt_digest)
        refs = text_tuple(self.evidence_refs, "evidence_refs")
        if len(set(refs)) != len(refs):
            raise CreativeValidationError("duplicate package evidence_refs")
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "hypotheses", text_tuple(self.hypotheses, "hypotheses"))
        if self.rights_status != _package_rights_status(self.spec):
            raise CreativeValidationError("package rights_status disagrees with attribution evidence")
        for attr in self.spec.attributions:
            if attr.rights_status == "EVIDENCE_BOUND" and attr.source_ref not in self.evidence_refs:
                raise CreativeValidationError("evidence-bound attribution missing from package evidence_refs")
        if not isinstance(self.edit_actor, ActorRef):
            raise CreativeValidationError("expected typed edit_actor")
        ActorRef.from_dict(self.edit_actor.to_dict())
        sections = text_tuple(self.changed_sections, "changed_sections")
        if len(set(sections)) != len(sections) or not set(sections) <= EDITABLE_PACKAGE_FIELDS:
            raise CreativeValidationError("invalid changed_sections")
        if list(sections) != sorted(sections):
            raise CreativeValidationError("changed_sections must be sorted")
        object.__setattr__(self, "changed_sections", sections)
        if self.revision == 1:
            if self.parent is not None or self.changed_sections or self.edit_reason is not None:
                raise CreativeValidationError("initial production package cannot have revision lineage")
            if self.reasoning_receipt_digest is None:
                raise CreativeValidationError("initial production package requires an actual model receipt")
            if self.edit_actor != ActorRef("AI", "reasoning:" + self.reasoning_receipt_digest):
                raise CreativeValidationError("initial production package actor must identify its inference receipt")
            expected_id = package_identity(
                self.brief_id, self.source_brief_digest, self.selected_concept,
                self.lineage, self.spec, self.observation, self.reasoning_receipt_digest,
            )
            if self.package_id != expected_id:
                raise CreativeValidationError("package_id does not match initial content/provenance")
        else:
            if not isinstance(self.parent, ProductionPackageParent):
                raise CreativeValidationError("production package revision needs a typed parent")
            ProductionPackageParent.from_dict(self.parent.to_dict())
            if self.parent.package_id != self.package_id or self.parent.revision != self.revision - 1:
                raise CreativeValidationError("invalid production package parent revision")
            if not self.changed_sections:
                raise CreativeValidationError("production package revision needs changed_sections")
            require_text(self.edit_reason, "edit_reason")
            if self.edit_actor.kind == "AI":
                if (self.reasoning_receipt_digest is None
                        or self.edit_actor.ref != "reasoning:" + self.reasoning_receipt_digest):
                    raise CreativeValidationError("AI package revision must identify its inference receipt")
            elif self.edit_actor.kind in ("HUMAN", "SYSTEM"):
                if self.reasoning_receipt_digest is not None:
                    raise CreativeValidationError("non-AI package edit cannot claim a new inference receipt")
            else:
                raise CreativeValidationError("package revision actor cannot be UNKNOWN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "revision": self.revision,
            "brief_id": self.brief_id,
            "source_brief_digest": self.source_brief_digest,
            "selected_concept": self.selected_concept.to_dict(),
            "lineage": self.lineage.to_dict(),
            "spec": self.spec.to_dict(),
            "observation": self.observation.to_dict(),
            "usage": self.usage.to_dict(),
            "configuration": self.configuration.to_dict() if self.configuration else None,
            "execution": self.execution.to_dict() if self.execution else None,
            "provider_request_id": self.provider_request_id,
            "reasoning_receipt_digest": self.reasoning_receipt_digest,
            "evidence_refs": list(self.evidence_refs),
            "hypotheses": list(self.hypotheses),
            "parent": self.parent.to_dict() if self.parent else None,
            "changed_sections": list(self.changed_sections),
            "edit_reason": self.edit_reason,
            "edit_actor": self.edit_actor.to_dict(),
            "claim_authority": self.claim_authority,
            "factual_status": self.factual_status,
            "rights_status": self.rights_status,
        }

    def to_json(self) -> str:
        return canonical_bytes(self.to_dict()).decode("utf-8")

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> A03CreativeProductionPackage:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["selected_concept"] = ConceptSelection.from_dict(value["selected_concept"])
        value["lineage"] = ProductionLineage.from_dict(value["lineage"])
        value["spec"] = ProductionPackageSpec.from_dict(value["spec"])
        value["observation"] = ProviderObservation.from_dict(value["observation"])
        value["usage"] = UsageObservation.from_dict(value["usage"])
        if value["configuration"] is not None:
            value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
        if value["execution"] is not None:
            value["execution"] = CLIExecutionObservation.from_dict(value["execution"])
        if value["parent"] is not None:
            value["parent"] = ProductionPackageParent.from_dict(value["parent"])
        value["edit_actor"] = ActorRef.from_dict(value["edit_actor"])
        for name in ("evidence_refs", "hypotheses", "changed_sections"):
            if not isinstance(value[name], list):
                raise CreativeValidationError(f"expected {name} array")
        return cls(**value)

    @classmethod
    def from_json(cls, text: str) -> A03CreativeProductionPackage:
        return cls.from_dict(read_json(text))


def _spec_field_to_wire(name: str, value: Any) -> Any:
    if name in ("narrative_intention", "visual_style", "lighting_direction"):
        return value
    if name in ("physics_invariants", "temporal_invariants", "creative_freedoms"):
        return list(value) if isinstance(value, (tuple, list)) else value
    if name == "audio":
        return value.to_dict() if hasattr(value, "to_dict") else value
    if isinstance(value, (tuple, list)):
        return [item.to_dict() if hasattr(item, "to_dict") else item for item in value]
    return value


def revise_production_package(
    parent: A03CreativeProductionPackage, changes: Mapping[str, Any], *,
    edit_reason: str, actor: ActorRef,
) -> A03CreativeProductionPackage:
    """Create a new immutable editable package revision without new model inference."""
    if not isinstance(parent, A03CreativeProductionPackage):
        raise CreativeValidationError("expected A03CreativeProductionPackage")
    parent = A03CreativeProductionPackage.from_dict(parent.to_dict())
    if not isinstance(changes, Mapping) or not changes:
        raise CreativeValidationError("production package revision requires non-empty changes")
    if any(not isinstance(k, str) or k not in EDITABLE_PACKAGE_FIELDS for k in changes):
        raise CreativeValidationError("only editable production spec sections may change")
    if not isinstance(actor, ActorRef):
        raise CreativeValidationError("expected typed ActorRef for package edit")
    actor = ActorRef.from_dict(actor.to_dict())
    if actor.kind not in ("HUMAN", "SYSTEM"):
        raise CreativeValidationError("direct package edit requires HUMAN or SYSTEM actor; AI edits need inference")
    merged = parent.spec.to_dict()
    for name, value in changes.items():
        merged[name] = _spec_field_to_wire(name, value)
    new_spec = ProductionPackageSpec.from_dict(merged)
    changed = tuple(sorted(name for name in EDITABLE_PACKAGE_FIELDS
                           if getattr(parent.spec, name) != getattr(new_spec, name)))
    if not changed:
        raise CreativeValidationError("production package edit must be material")
    return A03CreativeProductionPackage(
        package_id=parent.package_id,
        revision=parent.revision + 1,
        brief_id=parent.brief_id,
        source_brief_digest=parent.source_brief_digest,
        selected_concept=parent.selected_concept,
        lineage=parent.lineage,
        spec=new_spec,
        observation=parent.observation,
        usage=parent.usage,
        configuration=parent.configuration,
        execution=parent.execution,
        provider_request_id=parent.provider_request_id,
        reasoning_receipt_digest=None,
        evidence_refs=parent.evidence_refs,
        hypotheses=parent.hypotheses,
        parent=ProductionPackageParent(parent.package_id, parent.revision, parent.digest),
        changed_sections=changed,
        edit_reason=edit_reason,
        edit_actor=actor,
        claim_authority=parent.claim_authority,
        factual_status="UNVERIFIED",
        rights_status=_package_rights_status(new_spec),
    )


@dataclass(frozen=True, slots=True)
class CreativeProductionContext:
    """R3-only detached context pinned to a verified R2 cycle and optional parent package."""

    production_job_digest: str
    lineage: ProductionLineage
    evidence: tuple[CreativeEvidenceSource, ...] = ()
    parent_package: A03CreativeProductionPackage | None = None

    def __post_init__(self) -> None:
        require_digest(self.production_job_digest)
        if not isinstance(self.lineage, ProductionLineage):
            raise CreativeValidationError("expected typed ProductionLineage")
        ProductionLineage.from_dict(self.lineage.to_dict())
        if not isinstance(self.evidence, (tuple, list)) or any(
            not isinstance(e, CreativeEvidenceSource) for e in self.evidence
        ):
            raise CreativeValidationError("expected typed production evidence")
        items = tuple(CreativeEvidenceSource.from_dict(e.to_dict()) for e in self.evidence)
        refs = tuple(e.source_ref for e in items)
        if len(set(refs)) != len(refs):
            raise CreativeValidationError("duplicate production evidence source_ref")
        object.__setattr__(self, "evidence", items)
        if self.parent_package is not None:
            if not isinstance(self.parent_package, A03CreativeProductionPackage):
                raise CreativeValidationError("expected typed parent_package")
            A03CreativeProductionPackage.from_dict(self.parent_package.to_dict())

    @property
    def research_status(self) -> str:
        return "VERIFIED_SOURCE_BYTES" if self.evidence else "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "production_job_digest": self.production_job_digest,
            "research_status": self.research_status,
            "lineage": self.lineage.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "parent_package": self.parent_package.to_dict() if self.parent_package else None,
        }

    @classmethod
    def from_dict(cls, data: object) -> CreativeProductionContext:
        value = exact_fields(data, {f.name for f in fields(cls)} | {"research_status"})
        status = value.pop("research_status")
        value["lineage"] = ProductionLineage.from_dict(value["lineage"])
        if not isinstance(value["evidence"], list):
            raise CreativeValidationError("expected production evidence array")
        value["evidence"] = tuple(CreativeEvidenceSource.from_dict(e) for e in value["evidence"])
        if value["parent_package"] is not None:
            value["parent_package"] = A03CreativeProductionPackage.from_dict(value["parent_package"])
        result = cls(**value)
        if status != result.research_status:
            raise CreativeValidationError("research status disagrees with source evidence")
        return result


@dataclass(frozen=True, slots=True)
class ReasoningRequest:
    """Canonical detached brief plus optional intent and exact source revision."""

    brief_json: str
    operation: Literal[
        "EXPLORE", "REFRAME", "DEVELOP", "CRITIQUE", "SELECT", "PRODUCE_PACKAGE", "REVISE_PACKAGE"
    ] = "EXPLORE"
    mode: Literal["diverge", "controlled_variants"] = "diverge"
    count: int | None = None
    intent: CreativeIntent | None = None
    source: ConceptRecord | None = None
    instruction: str | None = None
    lens: str | None = None
    schema_version: str = "a03.reasoning-request.v2"
    cycle: CreativeCycleContext | None = None
    production: CreativeProductionContext | None = None

    def __post_init__(self) -> None:
        valid = intake_brief(read_json(self.brief_json))
        object.__setattr__(self, "brief_json", canonical_bytes(valid).decode("utf-8"))
        r2 = self.schema_version == "a03.reasoning-request.v3"
        r3 = self.schema_version == "a03.reasoning-request.v4"
        if self.schema_version not in (
            "a03.reasoning-request.v2", "a03.reasoning-request.v3", "a03.reasoning-request.v4"
        ):
            raise CreativeValidationError("unsupported reasoning request schema")
        if self.operation not in (
            "EXPLORE", "REFRAME", "DEVELOP", "CRITIQUE", "SELECT", "PRODUCE_PACKAGE", "REVISE_PACKAGE"
        ):
            raise CreativeValidationError("invalid creative operation")
        if r3:
            if self.cycle is not None or not isinstance(self.production, CreativeProductionContext):
                raise CreativeValidationError("R3 requires typed production context only")
            CreativeProductionContext.from_dict(self.production.to_dict())
            if self.operation not in ("PRODUCE_PACKAGE", "REVISE_PACKAGE"):
                raise CreativeValidationError("unsupported R3 operation")
            if self.mode != "diverge" or self.count is not None or self.lens is not None:
                raise CreativeValidationError("R3 package request cannot set count, lens or controlled_variants")
            if not {e.source_ref for e in self.production.evidence} <= set(valid.get("source_refs", [])):
                raise CreativeValidationError("external evidence must belong to the exact source brief")
            digest = brief_digest(valid)
            for pin in self.production.lineage.candidate_pins:
                if pin.brief_id != valid["brief_id"] or pin.source_brief_digest != digest:
                    raise CreativeValidationError("production lineage candidate rebound brief")
            if self.production.lineage.intent_digest is not None:
                if (not isinstance(self.intent, CreativeIntent)
                        or self.intent.digest != self.production.lineage.intent_digest):
                    raise CreativeValidationError("production request intent mismatch with R2 lineage")
            elif self.intent is not None:
                raise CreativeValidationError("production request introduced unpinned intent")
            if self.operation == "PRODUCE_PACKAGE" and self.production.parent_package is not None:
                raise CreativeValidationError("PRODUCE_PACKAGE cannot carry a parent package")
            if self.operation == "REVISE_PACKAGE":
                parent = self.production.parent_package
                if parent is None or not isinstance(self.source, ConceptRecord):
                    raise CreativeValidationError("REVISE_PACKAGE requires parent_package and exact source concept")
                if (parent.brief_id != valid["brief_id"] or parent.source_brief_digest != digest
                        or parent.selected_concept != _output_pin(self.source)
                        or parent.lineage != self.production.lineage):
                    raise CreativeValidationError("parent package does not match production source lineage")
        elif self.production is not None or self.operation in ("PRODUCE_PACKAGE", "REVISE_PACKAGE"):
            raise CreativeValidationError("production context and package operations require R3")
        if r2:
            if not isinstance(self.cycle, CreativeCycleContext):
                raise CreativeValidationError("R2 requires typed cycle context")
            CreativeCycleContext.from_dict(self.cycle.to_dict())
            if not {e.source_ref for e in self.cycle.evidence} <= set(valid.get("source_refs", [])):
                raise CreativeValidationError("external evidence must belong to the exact source brief")
            for candidate in self.cycle.candidates:
                if candidate.brief_id != valid["brief_id"] or candidate.source_brief_digest != brief_digest(valid):
                    raise CreativeValidationError("cycle candidate rebound the brief")
            if self.operation == "SELECT":
                pins = tuple(_output_pin(c) for c in self.cycle.candidates)
                if (len(pins) < 2 or tuple(c.selection for c in self.cycle.critics) != pins
                        or self.cycle.selector_receipt is not None):
                    raise CreativeValidationError("SELECT requires independently critiqued exact candidates")
            elif self.cycle.critics:
                raise CreativeValidationError("critic evidence belongs only to SELECT")
            if self.operation == "EXPLORE":
                if (self.mode != "diverge" or type(self.count) is not int or not 2 <= self.count <= 3
                        or self.cycle.candidates or self.cycle.selector_receipt is not None):
                    raise CreativeValidationError("R2 exploration requires two or three divergent concepts")
            elif self.operation == "CRITIQUE":
                if self.cycle.candidates != (self.source,) or self.cycle.selector_receipt is not None:
                    raise CreativeValidationError("independent critique needs its exact concept only")
            elif self.operation == "DEVELOP":
                if self.cycle.candidates != (self.source,) or self.cycle.selector_receipt is None:
                    raise CreativeValidationError("development requires an exact selector receipt")
            elif self.operation != "SELECT":
                raise CreativeValidationError("unsupported R2 operation")
        elif self.cycle is not None or self.operation == "SELECT":
            raise CreativeValidationError("cycle context and SELECT require R2")
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
        if self.operation in ("EXPLORE", "SELECT"):
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
            **({"cycle": self.cycle.to_dict()} if self.cycle is not None else {}),
            **({"production": self.production.to_dict()} if self.production is not None else {}),
        }

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ReasoningRequest:
        value = exact_fields(data, {"schema_version", "brief", "operation", "mode", "count",
                                    "intent", "source", "instruction", "lens"}, {"cycle", "production"})
        if (value["schema_version"] == "a03.reasoning-request.v3") != ("cycle" in value):
            raise CreativeValidationError("cycle field requires R2 request schema")
        if (value["schema_version"] == "a03.reasoning-request.v4") != ("production" in value):
            raise CreativeValidationError("production field requires R3 request schema")
        if "cycle" in value:
            value["cycle"] = CreativeCycleContext.from_dict(value["cycle"])
        if "production" in value:
            value["production"] = CreativeProductionContext.from_dict(value["production"])
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
    execution: CLIExecutionObservation | None = None
    selection: CreativeModelSelection | None = None
    production_spec: ProductionPackageSpec | None = None
    evidence_refs: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    response_json: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, ReasoningRequest):
            raise CreativeValidationError("expected typed reasoning request")
        ReasoningRequest.from_dict(self.request.to_dict())
        r2 = self.request.cycle is not None
        r3 = self.request.production is not None
        if self.schema_version != _result_schema(self.request):
            raise CreativeValidationError("unsupported reasoning result schema")
        if not r2 and self.selection is not None:
            raise CreativeValidationError("selection belongs only to R2 request")
        if not r3 and self.production_spec is not None:
            raise CreativeValidationError("production_spec belongs only to R3 request")
        if not (r2 or r3) and (self.evidence_refs or self.hypotheses or self.response_json is not None):
            raise CreativeValidationError("R2/R3 result fields require R2/R3 request")
        for name in ("evidence_refs", "hypotheses"):
            object.__setattr__(self, name, text_tuple(getattr(self, name), name))
        if r2 or r3:
            supplied = self.request.cycle.evidence if r2 else self.request.production.evidence
            if not set(self.evidence_refs) <= {e.source_ref for e in supplied}:
                raise CreativeValidationError("model fabricated external evidence citations")
            if self.response_json is not None:
                # Retain the exact output string and independently check that
                # normalized outputs/metadata are what the adapter observed.
                from .live import (CodexCliCreativeClient, StructuredModelRequest, StructuredModelResponse,
                                   _CLIStopped, _cli_request, parse_creative_output)
                raw = read_json(self.response_json)
                rejected = self.state == "ERROR" and self.error_code == "INVALID_MODEL_RESPONSE"
                response = None if rejected else StructuredModelResponse.from_dict(raw)
                if response is not None and (response.observation != self.observation or response.usage != self.usage
                        or response.request_id != self.provider_request_id or response.execution != self.execution):
                    raise CreativeValidationError("model response metadata rebound")
                if self.state == "OK":
                    if response.state != "OK":
                        raise CreativeValidationError("successful result needs observed successful response")
                    parsed = parse_creative_output(self.request, response.output_json)
                    if any(getattr(self, name) != val for name, val in parsed.items()):
                        raise CreativeValidationError("result differs from immutable model output")
                    if response.output_envelope_json is not None:
                        envelope = exact_fields(read_json(response.output_envelope_json), {"request_sha256", "output"})
                        model_request = StructuredModelRequest(self.configuration, self.request)
                        if envelope["request_sha256"] != sha256_bytes(canonical_bytes(model_request.to_dict())):
                            raise CreativeValidationError("native output envelope rebound request")
                        output = envelope["output"]
                        if self.request.operation == "DEVELOP":
                            output["edits"] = {k: v for k, v in exact_fields(output["edits"], set(EDITABLE_FIELDS)).items()
                                               if v is not None}
                        if output != read_json(response.output_json):
                            raise CreativeValidationError("normalized result lost native model output")
                    if self.execution is not None:
                        import base64
                        if any(getattr(response, n) is None for n in
                               ("output_envelope_json", "cli_stdin_json", "cli_schema_json", "cli_stdout_base64")):
                            raise CreativeValidationError("R2/R3 CLI success needs exact transport bytes")
                        try:
                            thread, output, usage = CodexCliCreativeClient._events(base64.b64decode(response.cli_stdout_base64))
                        except (_CLIStopped, ValueError, UnicodeError) as exc:
                            raise CreativeValidationError("invalid saved native output stream") from exc
                        if (thread != self.execution.cli_thread_id or output != response.output_envelope_json
                                or usage != self.usage):
                            raise CreativeValidationError("native output/usage/session evidence mismatch")
                        _, schema, packet = _cli_request(StructuredModelRequest(self.configuration, self.request))
                        if (response.cli_stdin_json.encode("utf-8") != packet
                                or response.cli_schema_json.encode("utf-8") != canonical_bytes(schema)):
                            raise CreativeValidationError("native stdin/schema rebound its model request")
            elif self.state == "OK":
                raise CreativeValidationError("R2/R3 success requires immutable observed model response")
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
        if self.execution is not None:
            if not isinstance(self.execution, CLIExecutionObservation):
                raise CreativeValidationError("expected typed CLI evidence")
            CLIExecutionObservation.from_dict(self.execution.to_dict())
            if (self.execution.request_digest != self.request.digest or self.configuration is None
                    or self.execution.configuration_digest != sha256_bytes(canonical_bytes(self.configuration.to_dict()))):
                raise CreativeValidationError("CLI evidence request/configuration mismatch")
            if self.state == "OK":
                expected_mode = "MODEL" if self.execution.kind == "AUTHENTICATED_CLI_PROBE" else "TEST_ONLY_MOCK"
                if (self.execution.effect != "PROCESS_INVOKED" or self.observation.mode != expected_mode
                        or self.execution.session_sha256 is None or self.execution.stdout_sha256 is None
                        or self.execution.cli_thread_id is None or self.execution.authorization_ref is None
                        or self.execution.observed_model != self.configuration.model
                        or self.execution.observed_effort != self.configuration.effort):
                    raise CreativeValidationError("unbound CLI completion")
                if expected_mode == "MODEL" and self.observation.model != self.execution.observed_model:
                    raise CreativeValidationError("CLI observation identity mismatch")
        for name, expected in (("drafts", ConceptDraft), ("edits", ConceptChange)):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)) or any(not isinstance(v, expected) for v in value):
                raise CreativeValidationError("expected typed creative output")
            object.__setattr__(self, name, tuple(expected.from_dict(v.to_dict()) for v in value))
        if self.provider_request_id is not None:
            require_text(self.provider_request_id, "provider_request_id")
        if self.state != "OK":
            if (self.drafts or self.edits or self.critique is not None or self.selection is not None
                    or self.production_spec is not None or self.evidence_refs or self.hypotheses):
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
        if op in ("PRODUCE_PACKAGE", "REVISE_PACKAGE"):
            if (self.drafts or self.edits or self.critique is not None or self.selection is not None
                    or not isinstance(self.production_spec, ProductionPackageSpec)):
                raise CreativeValidationError("R3 package operation requires typed production_spec only")
            ProductionPackageSpec.from_dict(self.production_spec.to_dict())
            _validate_spec_evidence(
                self.production_spec, self.request.brief,
                self.request.production.evidence, self.evidence_refs,
            )
            return
        if self.production_spec is not None:
            raise CreativeValidationError("production_spec belongs only to R3 package operations")
        if op == "SELECT":
            if self.drafts or self.edits or self.critique is not None or not isinstance(self.selection, CreativeModelSelection):
                raise CreativeValidationError("SELECT requires typed selection output only")
            CreativeModelSelection.from_dict(self.selection.to_dict())
            if (self.selection.critique_receipts != tuple(c.receipt_digest for c in self.request.cycle.critics)
                    or (self.selection.selection is not None and self.selection.selection not in
                        tuple(_output_pin(c) for c in self.request.cycle.candidates))):
                raise CreativeValidationError("model selection does not bind candidates and critic evidence")
            return
        if self.selection is not None:
            raise CreativeValidationError("model selection belongs only to SELECT")
        if op == "EXPLORE":
            if not self.drafts or self.edits or self.critique is not None:
                raise CreativeValidationError("exploration needs concepts only")
            if self.request.count is not None and len(self.drafts) != self.request.count:
                raise CreativeValidationError("result count disagrees with explicit request")
            exact, families, mechanisms = set(), set(), set()
            known_refs = set(self.request.brief.get("source_refs", []))
            if r2:
                known_refs = {e.source_ref for e in self.request.cycle.evidence}
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
            **({"execution": self.execution.to_dict()} if self.execution is not None else {}),
            **({"selection": self.selection.to_dict() if self.selection else None,
                "evidence_refs": list(self.evidence_refs), "hypotheses": list(self.hypotheses),
                "response_json": self.response_json} if self.request.cycle is not None else {}),
            **({"production_spec": self.production_spec.to_dict() if self.production_spec else None,
                "evidence_refs": list(self.evidence_refs), "hypotheses": list(self.hypotheses),
                "response_json": self.response_json} if self.request.production is not None else {}),
        }

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ReasoningResult:
        r2_fields = {"selection", "evidence_refs", "hypotheses", "response_json"}
        r3_fields = {"production_spec", "evidence_refs", "hypotheses", "response_json"}
        optional = {"execution"} | r2_fields | r3_fields
        value = exact_fields(data, {f.name for f in fields(cls)} - optional, optional)
        value["request"] = ReasoningRequest.from_dict(value["request"])
        if value["request"].cycle is not None:
            if not r2_fields <= value.keys() or "production_spec" in value:
                raise CreativeValidationError("missing or unexpected R2 result fields")
            if value["selection"] is not None:
                value["selection"] = CreativeModelSelection.from_dict(value["selection"])
        elif value["request"].production is not None:
            if not r3_fields <= value.keys() or "selection" in value:
                raise CreativeValidationError("missing or unexpected R3 result fields")
            if value["production_spec"] is not None:
                value["production_spec"] = ProductionPackageSpec.from_dict(value["production_spec"])
        elif (r2_fields | r3_fields) & value.keys():
            raise CreativeValidationError("unexpected R2/R3 fields in historical result")
        for name, expected in (("drafts", ConceptDraft), ("edits", ConceptChange)):
            if not isinstance(value[name], list):
                raise CreativeValidationError("expected creative output array")
            value[name] = tuple(expected.from_dict(v) for v in value[name])
        value["observation"] = ProviderObservation.from_dict(value["observation"])
        value["usage"] = UsageObservation.from_dict(value["usage"])
        if value["configuration"] is not None:
            value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
        if value.get("execution") is not None:
            value["execution"] = CLIExecutionObservation.from_dict(value["execution"])
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
                               schema_version=_result_schema(valid),
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
                               schema_version=_result_schema(valid),
                               message="Creative reasoner returned an invalid result")
    except Exception:
        return ReasoningResult(valid, "ERROR", error_code="REASONER_ERROR",
                               schema_version=_result_schema(valid),
                               message="Creative reasoner failed; no retry performed")


def _result_schema(request: ReasoningRequest) -> str:
    if request.production is not None:
        return "a03.reasoning-result.v4"
    return "a03.reasoning-result.v3" if request.cycle is not None else "a03.reasoning-result.v2"


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
            if (self.request.operation in ("CRITIQUE", "SELECT", "PRODUCE_PACKAGE", "REVISE_PACKAGE")
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
    cli_bound = (result.execution is not None and result.execution.effect == "PROCESS_INVOKED"
                 and result.execution.cli_thread_id is not None and result.execution.session_sha256 is not None)
    if (observed.mode == "UNKNOWN" or observed.provider == "UNKNOWN"
            or (result.provider_request_id is None and not cli_bound)):
        return "UNBOUND_PROVIDER"
    if observed.mode == "TEST_ONLY_MOCK":
        return None if observed.model == "UNKNOWN" else "INVALID_MOCK_IDENTITY"
    if observed.model == "UNKNOWN":
        return "UNBOUND_PROVIDER"
    if (observed.provider, observed.model) != (config.provider, config.model):
        return "PROVIDER_IDENTITY_MISMATCH"
    if config.effort is not None and (result.execution is None or result.execution.observed_effort != config.effort):
        return "CONFIGURATION_MISMATCH"
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
                       reasoner: CreativeReasonerV2 | None = None, *,
                       authorization: InvocationAuthorization | None = None) -> A03CreativeStageResult:
    """Invoke once, commit native artifacts, then return their verified receipt.

    Only an explicit trusted runner-side authorization reaches the live adapter.
    It cannot come from the serialized job or be borrowed from the adapter's own
    grant. Other injected reasoners remain trusted caller code.
    Existing A10 -> A03 intake/store remains INTAKE_COMPLETE, not model invocation.

    Completed attempts are idempotent. A reserved attempt without a terminal
    receipt fails closed, with no automatic replay after uncertain side effects.
    Storage is a caller-owned trusted directory; hashes are not authentication.
    """
    from .live import InvocationAuthorization, LiveCreativeReasoner
    from .sidecars import CreativeSidecarStore

    if not isinstance(job, A03CreativeJob):
        raise CreativeValidationError("expected A03CreativeJob")
    if authorization is not None:
        if not isinstance(authorization, InvocationAuthorization):
            raise CreativeValidationError("expected runner-side InvocationAuthorization")
        authorization = InvocationAuthorization(authorization.allow_network, authorization.allow_spend,
                                                authorization.authorization_ref)
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
                                 schema_version=_result_schema(job.request),
                                 message="Injected adapter does not match requested job configuration")
    elif isinstance(reasoner, LiveCreativeReasoner):
        # Snapshot the explicit runner grant before any client callback.
        reasoner = LiveCreativeReasoner(reasoner.config, reasoner.client,
                                       authorization=authorization or InvocationAuthorization())
        result = reason_v2(job.request, reasoner)
    else:
        result = reason_v2(job.request, reasoner)
    sidecars.save_result(result)
    if job.request.cycle is not None or job.request.production is not None:
        # Before materialization: conflicts/crashes must retain observed output.
        sidecars.save_observed_step(job, result)
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


@dataclass(frozen=True, slots=True)
class A03CreativeCycleJob:
    """Bounded R2 workflow on one immutable ContentBrief/CreativeIntent.

    The seed is configuration and lineage, not an invocation or an AI choice.
    Each internal inference gets a distinct native A03 job/attempt claim.
    """

    seed: A03CreativeJob
    evidence: tuple[CreativeEvidenceSource, ...] = ()
    max_revisions: int = 1
    schema_version: str = "A03CreativeCycleJob.v2"

    def __post_init__(self) -> None:
        if self.schema_version != "A03CreativeCycleJob.v2" or not isinstance(self.seed, A03CreativeJob):
            raise CreativeValidationError("expected typed R2 creative cycle seed")
        A03CreativeJob.from_dict(self.seed.to_dict())
        request = self.seed.request
        if (request.operation != "EXPLORE" or request.mode != "diverge" or request.cycle is not None
                or type(request.count) is not int or not 2 <= request.count <= 3
                or self.seed.selection_index is not None):
            raise CreativeValidationError("cycle seed requires two or three divergent concepts, no system selection")
        if type(self.max_revisions) is not int or not 0 <= self.max_revisions <= 2:
            raise CreativeValidationError("cycle revision budget must be between zero and two")
        if not isinstance(self.evidence, (tuple, list)) or any(not isinstance(e, CreativeEvidenceSource) for e in self.evidence):
            raise CreativeValidationError("expected typed external evidence")
        object.__setattr__(self, "evidence", tuple(CreativeEvidenceSource.from_dict(e.to_dict()) for e in self.evidence))
        refs = tuple(e.source_ref for e in self.evidence)
        if len(set(refs)) != len(refs) or not set(refs) <= set(request.brief.get("source_refs", [])):
            raise CreativeValidationError("cycle evidence must bind distinct original brief source refs")

    @classmethod
    def create(cls, brief: Any, *, intent: CreativeIntent | None = None, count: int = 3,
               evidence: tuple[CreativeEvidenceSource, ...] = (), max_revisions: int = 1,
               **context: Any) -> A03CreativeCycleJob:
        request = ReasoningRequest.create(brief, intent=intent, count=count)
        return cls(A03CreativeJob.create(request, **context), evidence, max_revisions)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "seed": self.seed.to_dict(),
                "evidence": [e.to_dict() for e in self.evidence], "max_revisions": self.max_revisions}

    @classmethod
    def from_dict(cls, data: object) -> A03CreativeCycleJob:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["seed"] = A03CreativeJob.from_dict(value["seed"])
        if not isinstance(value["evidence"], list):
            raise CreativeValidationError("expected evidence array")
        value["evidence"] = tuple(CreativeEvidenceSource.from_dict(e) for e in value["evidence"])
        return cls(**value)

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))


def _cycle_stage_job(cycle: A03CreativeCycleJob, request: ReasoningRequest) -> A03CreativeJob:
    seed = cycle.seed
    return A03CreativeJob.create(request, journey_id=seed.journey_id, cycle_id=seed.cycle_id,
        job_id=seed.job_id + ":r2:" + request.cycle.step, attempt=seed.attempt,
        correlation_id=seed.correlation_id, provenance=seed.provenance,
        configuration=seed.configuration, purpose=seed.purpose)


def _cycle_request(job: A03CreativeCycleJob, step: str, operation: str, *,
                   candidates: tuple[ConceptRecord, ...] = (),
                   critics: tuple[CreativeCriticEvidence, ...] = (),
                   selector: str | None = None, instruction: str | None = None) -> ReasoningRequest:
    return ReasoningRequest.create(job.seed.request.brief, operation=operation,
        intent=job.seed.request.intent, count=job.seed.request.count if operation == "EXPLORE" else None,
        source=candidates[0] if operation in ("CRITIQUE", "DEVELOP") else None,
        instruction=job.seed.request.instruction if operation == "EXPLORE" else instruction,
        schema_version="a03.reasoning-request.v3",
        cycle=CreativeCycleContext(job.digest, step, job.evidence, candidates, critics, selector))


class _CycleHalt(Exception):
    pass


def _cycle_step_failure(stage: A03CreativeStageResult, previous: list[A03CreativeStageResult],
                        authorization: InvocationAuthorization) -> str | None:
    if stage.status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK"):
        return stage.failure or "UNKNOWN_INFERENCE"
    result, config = stage.reasoning, stage.job.configuration
    if result.observation.mode == "MODEL":
        e = result.execution
        if (e is None or e.kind != "AUTHENTICATED_CLI_PROBE" or e.effect != "PROCESS_INVOKED"
                or e.session_sha256 is None or e.stdout_sha256 is None or e.cli_thread_id is None
                or e.authorization_ref != authorization.authorization_ref or config.effort in (None, "UNKNOWN")
                or (e.observed_model, e.observed_effort) != (config.model, config.effort)):
            return "UNVERIFIED_MODEL_EXECUTION"
        response = read_json(result.response_json)
        if any(response.get(n) is None for n in ("output_envelope_json", "cli_stdin_json", "cli_schema_json", "cli_stdout_base64")):
            return "MISSING_NATIVE_OUTPUT"
    for prior in previous:
        other = prior.reasoning
        if other.observation != result.observation or (other.execution is None) != (result.execution is None):
            return "MIXED_INFERENCE_IDENTITY"
        if (other.digest == result.digest or other.request.digest == result.request.digest
                or (result.provider_request_id is not None and other.provider_request_id == result.provider_request_id)):
            return "REPLAYED_INFERENCE"
        if result.execution is not None:
            if any(getattr(result.execution, name) is not None and
                   getattr(result.execution, name) == getattr(other.execution, name)
                   for name in ("cli_thread_id", "session_sha256", "stdout_sha256")):
                return "REPLAYED_INFERENCE"
    return None


def _creative_cycle_flow(job: A03CreativeCycleJob, authorization: InvocationAuthorization):
    """Pure state machine shared by execution and strict receipt validation."""
    if job.seed.configuration is None:
        return "HOLD", "NO_MODEL_CONFIGURED", None, None
    if job.seed.purpose == "PRODUCTION":
        if not (authorization.allow_network and authorization.allow_spend and authorization.authorization_ref):
            return "HOLD", "UNAUTHORIZED", None, None
        if job.seed.configuration.effort in (None, "UNKNOWN"):
            return "HOLD", "UNVERIFIED_MODEL_CONFIGURATION", None, None
    previous: list[A03CreativeStageResult] = []

    def perform(request):
        expected = _cycle_stage_job(job, request)
        stage = yield expected
        if not isinstance(stage, A03CreativeStageResult) or stage.job != expected:
            raise CreativeValidationError("cycle stage order/request/attempt mismatch")
        failure = _cycle_step_failure(stage, previous, authorization)
        previous.append(stage)
        if failure is not None:
            raise _CycleHalt(failure)
        return stage

    try:
        explored = yield from perform(_cycle_request(job, "explore", "EXPLORE"))
        candidates = explored.reasoning.concepts()
        critic_by_digest: dict[str, CreativeCriticEvidence] = {}
        for revision in range(job.max_revisions + 1):
            for index, candidate in enumerate(candidates):
                if candidate.digest not in critic_by_digest:
                    stage = yield from perform(_cycle_request(job, f"critic:{revision}:{index}", "CRITIQUE",
                        candidates=(candidate,), instruction="Independently critique this immutable concept. "
                        "Separate creative hypotheses from cited external source bytes; absent research is UNKNOWN."))
                    critic_by_digest[candidate.digest] = CreativeCriticEvidence(_output_pin(candidate),
                        stage.reasoning.digest, stage.reasoning.request.digest, stage.reasoning.critique)
            critics = tuple(critic_by_digest[c.digest] for c in candidates)
            selector = yield from perform(_cycle_request(job, f"select:{revision}", "SELECT",
                candidates=candidates, critics=critics,
                instruction="Return a typed SELECT, REVISE or STOP bound to all supplied independent critic receipts. "
                "Choose only an exact supplied candidate pin. Scores and prose alone cannot select."))
            choice = selector.reasoning.selection
            if choice.action == "STOP":
                return "STOPPED", "AI_STOP", None, None
            if choice.action == "REVISE" and revision == job.max_revisions:
                return "STOPPED", "REVISION_LIMIT", None, None
            chosen = next(c for c in candidates if _output_pin(c) == choice.selection)
            final = choice.action == "SELECT"
            developed = yield from perform(_cycle_request(job,
                "develop" if final else f"revise:{revision}", "DEVELOP", candidates=(chosen,),
                selector=selector.reasoning.digest, instruction=("Develop the selected concept, preserving the exact brief, "
                "including optional product/CTA and silence. Creative claims remain UNVERIFIED."
                if final else choice.revision_instruction)))
            child = developed.reasoning.proposed_revision()
            if final:
                decision = inference_decision(selector.reasoning, choice.selection, kind="AI_RECOMMENDED")
                return developed.status, None, decision, _output_pin(child)
            candidates = tuple(child if c.digest == chosen.digest else c for c in candidates)
    except _CycleHalt as exc:
        return "HOLD", str(exc), None, None


@dataclass(frozen=True, slots=True)
class A03CreativeCycleResult:
    """R2 receipt; final output is a native production-consumable selection.

    Every intermediate request, raw model output, observed execution and source
    pin is retained. A HOLD can retain the observed but uncommitted last step.
    """

    job: A03CreativeCycleJob
    authorization: InvocationAuthorization
    steps: tuple[A03CreativeStageResult, ...]
    status: Literal["HOLD", "STOPPED", "TEST_ONLY_MOCK", "CREATIVE_COMPLETE"]
    failure: str | None = None
    decision: ActorDecision | None = None
    output: ConceptSelection | None = None
    halted_step: A03CreativeJob | None = None
    observed: ReasoningResult | None = None
    schema_version: str = "A03CreativeCycleResult.v2"

    def __post_init__(self) -> None:
        from .live import InvocationAuthorization
        if self.schema_version != "A03CreativeCycleResult.v2" or not isinstance(self.job, A03CreativeCycleJob):
            raise CreativeValidationError("expected typed R2 creative cycle")
        A03CreativeCycleJob.from_dict(self.job.to_dict())
        if not isinstance(self.authorization, InvocationAuthorization):
            raise CreativeValidationError("expected runner authorization snapshot")
        InvocationAuthorization(self.authorization.allow_network, self.authorization.allow_spend,
                                self.authorization.authorization_ref)
        if not isinstance(self.steps, (list, tuple)) or any(not isinstance(s, A03CreativeStageResult) for s in self.steps):
            raise CreativeValidationError("expected typed cycle steps")
        object.__setattr__(self, "steps", tuple(A03CreativeStageResult.from_dict(s.to_dict()) for s in self.steps))
        flow = _creative_cycle_flow(self.job, self.authorization)
        terminal, expected = None, None
        consumed = 0
        try:
            expected = next(flow)
            for stage in self.steps:
                consumed += 1
                expected = flow.send(stage)
        except StopIteration as stop:
            terminal = stop.value
        # A terminal state cannot have unconsumed steps hidden after it.
        if terminal is not None:
            if consumed != len(self.steps):
                raise CreativeValidationError("cycle contains steps after terminal outcome")
            if self.halted_step is not None or self.observed is not None:
                raise CreativeValidationError("terminal cycle cannot claim a pending step")
        else:
            if (self.status != "HOLD" or self.failure not in
                    ("UNRESOLVED_CREATIVE_STEP", "ARTIFACT_CONFLICT", "ADAPTER_MODE_MISMATCH")
                    or self.halted_step != expected):
                raise CreativeValidationError("incomplete cycle must HOLD at its exact next step")
            if self.observed is not None:
                ReasoningResult.from_dict(self.observed.to_dict())
                if self.observed.request != expected.request:
                    raise CreativeValidationError("halted observation rebound its request")
            terminal = ("HOLD", self.failure, None, None)
        if (self.status, self.failure, self.decision, self.output) != terminal:
            raise CreativeValidationError("cycle outcome disagrees with verified workflow evidence")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "job": self.job.to_dict(),
                "authorization": {"allow_network": self.authorization.allow_network,
                    "allow_spend": self.authorization.allow_spend,
                    "authorization_ref": self.authorization.authorization_ref},
                "steps": [s.to_dict() for s in self.steps], "status": self.status, "failure": self.failure,
                "decision": self.decision.to_dict() if self.decision else None,
                "output": self.output.to_dict() if self.output else None,
                "halted_step": self.halted_step.to_dict() if self.halted_step else None,
                "observed": self.observed.to_dict() if self.observed else None}

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> A03CreativeCycleResult:
        from .live import InvocationAuthorization
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["job"] = A03CreativeCycleJob.from_dict(value["job"])
        value["authorization"] = InvocationAuthorization(**exact_fields(value["authorization"],
            {"allow_network", "allow_spend", "authorization_ref"}))
        if not isinstance(value["steps"], list):
            raise CreativeValidationError("expected cycle step array")
        value["steps"] = tuple(A03CreativeStageResult.from_dict(s) for s in value["steps"])
        for name, kind in (("decision", ActorDecision), ("output", ConceptSelection),
                           ("halted_step", A03CreativeJob), ("observed", ReasoningResult)):
            if value[name] is not None:
                value[name] = kind.from_dict(value[name])
        return cls(**value)


def run_creative_cycle(job: A03CreativeCycleJob, store: CreativeStore | CreativeSidecarStore,
                       reasoner: LiveCreativeReasoner | None = None, *,
                       authorization: InvocationAuthorization | None = None) -> A03CreativeCycleResult:
    """Run R2 without Human relays, provider defaults, retries or other effects.

    The existing LiveCreativeReasoner/CLI seam owns every actual inference.
    Completed steps replay from disk. Interrupted claims only reconstruct saved
    observations and HOLD; they never re-invoke a provider.
    """
    from .live import InvocationAuthorization, LiveCreativeReasoner
    from .sidecars import CreativeSidecarStore
    from ._validation import VersionConflict

    if not isinstance(job, A03CreativeCycleJob):
        raise CreativeValidationError("expected A03CreativeCycleJob")
    job = A03CreativeCycleJob.from_dict(job.to_dict())
    grant = InvocationAuthorization() if authorization is None else authorization
    if not isinstance(grant, InvocationAuthorization):
        raise CreativeValidationError("expected explicit runner authorization")
    grant = InvocationAuthorization(grant.allow_network, grant.allow_spend, grant.authorization_ref)
    if reasoner is not None:
        if not isinstance(reasoner, LiveCreativeReasoner):
            raise CreativeValidationError("R2 requires the existing LiveCreativeReasoner seam")
        reasoner = LiveCreativeReasoner(reasoner.config, reasoner.client, authorization=grant)
    sidecars = store if isinstance(store, CreativeSidecarStore) else CreativeSidecarStore(store)
    fresh = sidecars.claim_cycle(job, grant)
    if not fresh and sidecars.has_cycle_result(job):
        return sidecars.load_cycle(job)
    if not fresh:
        # The saved grant only verifies old observations. The recovery path
        # below never invokes a provider, even with this historical snapshot.
        grant = sidecars.cycle_authorization(job)
    flow, steps = _creative_cycle_flow(job, grant), []
    pending, observed, failure = None, None, None
    try:
        pending = next(flow)
        while True:
            if not fresh:
                stage = sidecars.load_stage(pending)
            else:
                if reasoner is not None and reasoner.client is not None:
                    expected_mode = "MODEL" if job.seed.purpose == "PRODUCTION" else "TEST_ONLY_MOCK"
                    if reasoner.client.execution_mode != expected_mode:
                        failure = "ADAPTER_MODE_MISMATCH"
                        break
                stage = run_creative_stage(pending, sidecars, reasoner, authorization=grant)
            steps.append(stage)
            pending = flow.send(stage)
    except StopIteration as stop:
        status, failure, decision, output = stop.value
        result = A03CreativeCycleResult(job, grant, tuple(steps), status, failure, decision, output)
        pending = None
    except (CreativeValidationError, OSError) as exc:
        failure = "ARTIFACT_CONFLICT" if isinstance(exc, VersionConflict) else "UNRESOLVED_CREATIVE_STEP"
    if pending is not None and failure in ("ARTIFACT_CONFLICT", "UNRESOLVED_CREATIVE_STEP", "ADAPTER_MODE_MISMATCH"):
        observed = sidecars.observed_step(pending)
        result = A03CreativeCycleResult(job, grant, tuple(steps), "HOLD", failure,
                                        halted_step=pending, observed=observed)
    sidecars.save_cycle(result)
    return sidecars.load_cycle(job)


@dataclass(frozen=True, slots=True)
class A03CreativeProductionJob:
    """One immutable R3 attempt to produce or AI-revise a production package.

    Always pins an exact verified A03CreativeCycleResult.v2 and its final
    immutable ConceptSelection. Never retries or synthesizes a package locally.
    """

    journey_id: str
    cycle_id: str
    job_id: str
    attempt: int
    correlation_id: str
    provenance: CreativeJobProvenance
    cycle_job_digest: str
    cycle_result_digest: str
    selected_concept: ConceptSelection
    brief_id: str
    source_brief_digest: str
    configuration: ModelConfiguration | None = None
    purpose: Literal["PRODUCTION", "TEST_ONLY_MOCK"] = "PRODUCTION"
    instruction: str | None = None
    parent_package_digest: str | None = None
    edit_reason: str | None = None
    schema_version: str = "A03CreativeProductionJob.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "A03CreativeProductionJob.v1":
            raise CreativeValidationError("unsupported creative production job schema")
        for name in ("journey_id", "cycle_id", "job_id", "correlation_id", "brief_id"):
            require_text(getattr(self, name), name)
        positive_int(self.attempt, "attempt")
        if not isinstance(self.provenance, CreativeJobProvenance):
            raise CreativeValidationError("expected typed production job provenance")
        CreativeJobProvenance.from_dict(self.provenance.to_dict())
        for name in ("cycle_job_digest", "cycle_result_digest", "source_brief_digest"):
            require_digest(getattr(self, name))
        if not isinstance(self.selected_concept, ConceptSelection):
            raise CreativeValidationError("expected typed selected_concept")
        ConceptSelection.from_dict(self.selected_concept.to_dict())
        if (self.selected_concept.brief_id != self.brief_id
                or self.selected_concept.source_brief_digest != self.source_brief_digest):
            raise CreativeValidationError("production job selected_concept/brief mismatch")
        if self.configuration is not None:
            if not isinstance(self.configuration, ModelConfiguration):
                raise CreativeValidationError("expected typed ModelConfiguration")
            ModelConfiguration.from_dict(self.configuration.to_dict())
        if self.purpose not in ("PRODUCTION", "TEST_ONLY_MOCK"):
            raise CreativeValidationError("invalid production job purpose")
        if self.instruction is not None:
            require_text(self.instruction, "instruction")
        if self.parent_package_digest is not None:
            require_digest(self.parent_package_digest)
            require_text(self.edit_reason, "edit_reason")
        elif self.edit_reason is not None:
            raise CreativeValidationError("edit_reason requires parent_package_digest")

    @classmethod
    def from_cycle(
        cls, cycle: A03CreativeCycleResult, *,
        parent_package: A03CreativeProductionPackage | None = None, **context: Any,
    ) -> A03CreativeProductionJob:
        if not isinstance(cycle, A03CreativeCycleResult):
            raise CreativeValidationError("expected A03CreativeCycleResult")
        cycle = A03CreativeCycleResult.from_dict(cycle.to_dict())
        if (cycle.status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK")
                or cycle.output is None or cycle.decision is None):
            raise CreativeValidationError("production job requires a completed verified R2 cycle")
        parent_digest = None
        if parent_package is not None:
            if not isinstance(parent_package, A03CreativeProductionPackage):
                raise CreativeValidationError("expected A03CreativeProductionPackage")
            parent_digest = A03CreativeProductionPackage.from_dict(parent_package.to_dict()).digest
        return cls(
            cycle_job_digest=cycle.job.digest,
            cycle_result_digest=cycle.digest,
            selected_concept=cycle.output,
            brief_id=cycle.job.seed.brief_id,
            source_brief_digest=cycle.job.seed.source_brief_digest,
            parent_package_digest=parent_digest,
            **context,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **{f.name: getattr(self, f.name) for f in fields(self)},
            "provenance": self.provenance.to_dict(),
            "selected_concept": self.selected_concept.to_dict(),
            "configuration": self.configuration.to_dict() if self.configuration else None,
        }

    @classmethod
    def from_dict(cls, data: object) -> A03CreativeProductionJob:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
        value["selected_concept"] = ConceptSelection.from_dict(value["selected_concept"])
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


def _cycle_lineage(cycle: A03CreativeCycleResult) -> ProductionLineage:
    if (cycle.status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK")
            or cycle.output is None or cycle.decision is None or len(cycle.steps) < 4):
        raise CreativeValidationError("verified completed R2 cycle required for production lineage")
    explore_step = cycle.steps[0]
    selector_step = cycle.steps[-2]
    develop_step = cycle.steps[-1]
    if (explore_step.job.request.operation != "EXPLORE"
            or selector_step.job.request.operation != "SELECT"
            or develop_step.job.request.operation != "DEVELOP"):
        raise CreativeValidationError("R2 cycle steps do not match expected explore/select/develop lineage")
    critic_receipts = tuple(s.reasoning.digest for s in cycle.steps if s.job.request.operation == "CRITIQUE")
    candidate_pins = tuple(_output_pin(c) for c in selector_step.job.request.cycle.candidates)
    intent = cycle.job.seed.request.intent
    return ProductionLineage(
        cycle_job_digest=cycle.job.digest,
        cycle_result_digest=cycle.digest,
        cycle_status=cycle.status,
        explore_receipt_digest=explore_step.reasoning.digest,
        candidate_pins=candidate_pins,
        critic_receipts=critic_receipts,
        selector_receipt_digest=selector_step.reasoning.digest,
        develop_receipt_digest=develop_step.reasoning.digest,
        decision_id=cycle.decision.decision_id,
        intent_digest=intent.digest if intent is not None else None,
    )


def _production_request(
    job: A03CreativeProductionJob, cycle: A03CreativeCycleResult,
    selected_record: ConceptRecord, parent: A03CreativeProductionPackage | None = None,
) -> ReasoningRequest:
    lineage = _cycle_lineage(cycle)
    op = "REVISE_PACKAGE" if parent is not None else "PRODUCE_PACKAGE"
    default_instruction = (
        f"Revise the pinned production package ({job.edit_reason}), preserving immutable lineage, "
        "optional product/CTA, optional speech/music/humans, causal S0->action->S1 scenes and UNVERIFIED/UNKNOWN authority."
        if parent is not None else
        "Author a strictly typed A03CreativeProductionPackage spec from the verified R2 concept and brief. "
        "Do not invent evidence links, copyright/brand rights, verified QA or premature media. "
        "Use explicit SILENCE when no speech or audio is intended."
    )
    return ReasoningRequest.create(
        cycle.job.seed.request.brief,
        operation=op,
        intent=cycle.job.seed.request.intent,
        source=selected_record,
        instruction=job.instruction or default_instruction,
        schema_version="a03.reasoning-request.v4",
        production=CreativeProductionContext(job.digest, lineage, cycle.job.evidence, parent),
    )


def _production_stage_job(job: A03CreativeProductionJob, request: ReasoningRequest) -> A03CreativeJob:
    step = "revise" if job.parent_package_digest is not None else "produce"
    return A03CreativeJob.create(
        request, journey_id=job.journey_id, cycle_id=job.cycle_id,
        job_id=job.job_id + ":r3:" + step, attempt=job.attempt,
        correlation_id=job.correlation_id, provenance=job.provenance,
        configuration=job.configuration, purpose=job.purpose,
    )


def _production_step_failure(
    stage: A03CreativeStageResult, cycle: A03CreativeCycleResult,
    authorization: InvocationAuthorization, parent: A03CreativeProductionPackage | None,
) -> str | None:
    if stage.status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK"):
        return stage.failure or "UNKNOWN_INFERENCE"
    result, config = stage.reasoning, stage.job.configuration
    if result.observation.mode == "MODEL":
        e = result.execution
        if (e is None or e.kind != "AUTHENTICATED_CLI_PROBE" or e.effect != "PROCESS_INVOKED"
                or e.session_sha256 is None or e.stdout_sha256 is None or e.cli_thread_id is None
                or e.authorization_ref != authorization.authorization_ref or config.effort in (None, "UNKNOWN")
                or (e.observed_model, e.observed_effort) != (config.model, config.effort)):
            return "UNVERIFIED_MODEL_EXECUTION"
        response = read_json(result.response_json)
        if any(response.get(n) is None for n in
               ("output_envelope_json", "cli_stdin_json", "cli_schema_json", "cli_stdout_base64")):
            return "MISSING_NATIVE_OUTPUT"
    for prior in cycle.steps:
        other = prior.reasoning
        if (other.digest == result.digest or other.request.digest == result.request.digest
                or (result.provider_request_id is not None and other.provider_request_id == result.provider_request_id)):
            return "REPLAYED_INFERENCE"
        if result.execution is not None and other.execution is not None:
            if any(getattr(result.execution, name) is not None
                   and getattr(result.execution, name) == getattr(other.execution, name)
                   for name in ("cli_thread_id", "session_sha256", "stdout_sha256")):
                return "REPLAYED_INFERENCE"
    if parent is not None and result.production_spec == parent.spec:
        return "NO_MATERIAL_CHANGE"
    return None


@dataclass(frozen=True, slots=True)
class A03CreativeProductionResult:
    """R3 receipt; HOLD/STOP never produces a partial production package."""

    job: A03CreativeProductionJob
    authorization: InvocationAuthorization
    status: Literal["HOLD", "STOPPED", "TEST_ONLY_MOCK", "CREATIVE_COMPLETE"]
    failure: str | None = None
    stage: A03CreativeStageResult | None = None
    package: A03CreativeProductionPackage | None = None
    observed: ReasoningResult | None = None
    schema_version: str = "A03CreativeProductionResult.v1"

    def __post_init__(self) -> None:
        from .live import InvocationAuthorization
        if self.schema_version != "A03CreativeProductionResult.v1" or not isinstance(self.job, A03CreativeProductionJob):
            raise CreativeValidationError("expected typed R3 production result")
        A03CreativeProductionJob.from_dict(self.job.to_dict())
        if not isinstance(self.authorization, InvocationAuthorization):
            raise CreativeValidationError("expected runner authorization snapshot")
        InvocationAuthorization(self.authorization.allow_network, self.authorization.allow_spend,
                                self.authorization.authorization_ref)
        if self.status not in ("HOLD", "STOPPED", "TEST_ONLY_MOCK", "CREATIVE_COMPLETE"):
            raise CreativeValidationError("invalid production result status")
        if self.stage is not None:
            if not isinstance(self.stage, A03CreativeStageResult):
                raise CreativeValidationError("expected typed A03CreativeStageResult")
            A03CreativeStageResult.from_dict(self.stage.to_dict())
            ctx = self.stage.job.request.production
            if ctx is None or ctx.production_job_digest != self.job.digest:
                raise CreativeValidationError("production stage receipt rebound its job")
        if self.package is not None:
            if not isinstance(self.package, A03CreativeProductionPackage):
                raise CreativeValidationError("expected typed A03CreativeProductionPackage")
            A03CreativeProductionPackage.from_dict(self.package.to_dict())
        if self.observed is not None:
            if not isinstance(self.observed, ReasoningResult):
                raise CreativeValidationError("expected typed observed ReasoningResult")
            ReasoningResult.from_dict(self.observed.to_dict())
            if (self.observed.request.production is None
                    or self.observed.request.production.production_job_digest != self.job.digest):
                raise CreativeValidationError("observed production reasoning rebound its job")
        if self.status in ("TEST_ONLY_MOCK", "CREATIVE_COMPLETE"):
            if self.failure is not None or self.observed is not None or self.stage is None or self.package is None:
                raise CreativeValidationError("completed production result needs exact stage and package without failure")
            r = self.stage.reasoning
            p = self.package
            ctx = self.stage.job.request.production
            if (self.stage.status != self.status or r.state != "OK" or r.production_spec != p.spec
                    or self.stage.job.configuration != self.job.configuration or self.stage.job.purpose != self.job.purpose
                    or p.brief_id != self.job.brief_id or p.source_brief_digest != self.job.source_brief_digest
                    or p.selected_concept != self.job.selected_concept
                    or p.lineage != ctx.lineage
                    or p.lineage.cycle_job_digest != self.job.cycle_job_digest
                    or p.lineage.cycle_result_digest != self.job.cycle_result_digest
                    or p.reasoning_receipt_digest != r.digest
                    or p.observation != r.observation or p.usage != r.usage
                    or p.configuration != r.configuration or p.configuration != self.job.configuration
                    or p.execution != r.execution or p.provider_request_id != r.provider_request_id
                    or p.evidence_refs != r.evidence_refs or p.hypotheses != r.hypotheses
                    or p.edit_actor != ActorRef("AI", "reasoning:" + r.digest)):
                raise CreativeValidationError("production package disagrees with verified inference receipt")
            if self.job.parent_package_digest is None:
                if (self.stage.job.request.operation != "PRODUCE_PACKAGE"
                        or p.revision != 1 or p.parent is not None):
                    raise CreativeValidationError("initial production result must create revision 1")
            else:
                parent = ctx.parent_package
                if (self.stage.job.request.operation != "REVISE_PACKAGE"
                        or parent is None or parent.digest != self.job.parent_package_digest
                        or p.parent != ProductionPackageParent(parent.package_id, parent.revision, parent.digest)
                        or p.edit_reason != self.job.edit_reason):
                    raise CreativeValidationError("production revision disagrees with pinned parent package")
            if self.status == "CREATIVE_COMPLETE":
                e = p.execution
                if (self.job.purpose != "PRODUCTION" or p.lineage.cycle_status != "CREATIVE_COMPLETE"
                        or p.observation.mode != "MODEL" or e is None or e.kind != "AUTHENTICATED_CLI_PROBE"
                        or e.effect != "PROCESS_INVOKED" or e.authorization_ref != self.authorization.authorization_ref
                        or self.job.configuration is None
                        or (e.observed_model, e.observed_effort) != (self.job.configuration.model, self.job.configuration.effort)):
                    raise CreativeValidationError("CREATIVE_COMPLETE production result requires verified native MODEL execution")
            else:
                if (self.job.purpose != "TEST_ONLY_MOCK" or p.observation.mode != "TEST_ONLY_MOCK"
                        or p.observation.model != "UNKNOWN"):
                    raise CreativeValidationError("TEST_ONLY_MOCK production result requires mock mode and UNKNOWN model")
        else:
            require_text(self.failure, "failure")
            if self.package is not None:
                raise CreativeValidationError("HOLD/STOPPED production result cannot contain a partial package")
            if self.status == "STOPPED" and (self.stage is None or self.stage.reasoning.state != "REFUSED"):
                raise CreativeValidationError("STOPPED production result requires an observed model refusal")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job": self.job.to_dict(),
            "authorization": {
                "allow_network": self.authorization.allow_network,
                "allow_spend": self.authorization.allow_spend,
                "authorization_ref": self.authorization.authorization_ref,
            },
            "status": self.status,
            "failure": self.failure,
            "stage": self.stage.to_dict() if self.stage else None,
            "package": self.package.to_dict() if self.package else None,
            "observed": self.observed.to_dict() if self.observed else None,
        }

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> A03CreativeProductionResult:
        from .live import InvocationAuthorization
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["job"] = A03CreativeProductionJob.from_dict(value["job"])
        value["authorization"] = InvocationAuthorization(**exact_fields(
            value["authorization"], {"allow_network", "allow_spend", "authorization_ref"}
        ))
        if value["stage"] is not None:
            value["stage"] = A03CreativeStageResult.from_dict(value["stage"])
        if value["package"] is not None:
            value["package"] = A03CreativeProductionPackage.from_dict(value["package"])
        if value["observed"] is not None:
            value["observed"] = ReasoningResult.from_dict(value["observed"])
        return cls(**value)


def run_creative_production(
    job: A03CreativeProductionJob, store: CreativeStore | CreativeSidecarStore,
    reasoner: LiveCreativeReasoner | None = None, *,
    authorization: InvocationAuthorization | None = None,
) -> A03CreativeProductionResult:
    """Produce or AI-revise one verified A03CreativeProductionPackage.v1.

    Pins exact verified R2 cycle + final ConceptSelection + source brief.
    Reuses LiveCreativeReasoner/StructuredCreativeClient/CLI seam with one
    distinct source-bound attempt. Never synthesizes from rules or retries.
    """
    from .live import InvocationAuthorization, LiveCreativeReasoner
    from .sidecars import CreativeSidecarStore
    from ._validation import VersionConflict

    if not isinstance(job, A03CreativeProductionJob):
        raise CreativeValidationError("expected A03CreativeProductionJob")
    job = A03CreativeProductionJob.from_dict(job.to_dict())
    grant = InvocationAuthorization() if authorization is None else authorization
    if not isinstance(grant, InvocationAuthorization):
        raise CreativeValidationError("expected explicit runner authorization")
    grant = InvocationAuthorization(grant.allow_network, grant.allow_spend, grant.authorization_ref)
    if reasoner is not None:
        if not isinstance(reasoner, LiveCreativeReasoner):
            raise CreativeValidationError("R3 requires the existing LiveCreativeReasoner seam")
        reasoner = LiveCreativeReasoner(reasoner.config, reasoner.client, authorization=grant)
    sidecars = store if isinstance(store, CreativeSidecarStore) else CreativeSidecarStore(store)
    cycle = sidecars.load_cycle_by_digest(job.cycle_job_digest, job.cycle_result_digest)
    if (cycle.status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK")
            or cycle.output != job.selected_concept
            or cycle.job.seed.brief_id != job.brief_id
            or cycle.job.seed.source_brief_digest != job.source_brief_digest):
        raise CreativeValidationError("production job does not match verified saved R2 cycle output")
    selected_record = sidecars.creative.load_concept(job.selected_concept.concept_id, job.selected_concept.revision)
    if _output_pin(selected_record) != job.selected_concept:
        raise CreativeValidationError("saved concept does not match pinned selection")
    lineage = _cycle_lineage(cycle)
    parent_package: A03CreativeProductionPackage | None = None
    if job.parent_package_digest is not None:
        parent_package = sidecars.load_production_package_by_digest(job.parent_package_digest)
        if (parent_package.brief_id != job.brief_id
                or parent_package.source_brief_digest != job.source_brief_digest
                or parent_package.selected_concept != job.selected_concept
                or parent_package.lineage != lineage):
            raise CreativeValidationError("parent package does not match verified R2 cycle lineage")
    fresh = sidecars.claim_production_job(job, grant)
    if not fresh:
        saved_grant = sidecars.production_authorization(job)
        if authorization is not None and saved_grant != grant:
            raise CreativeValidationError("AUTHORIZATION_MISMATCH: production job permission changed")
        if sidecars.has_production_result(job):
            return sidecars.load_production_result(job)
        # Interrupted claim recovery: never re-invoke a provider.
        request = _production_request(job, cycle, selected_record, parent_package)
        stage_job = _production_stage_job(job, request)
        observed = sidecars.observed_step(stage_job)
        result = A03CreativeProductionResult(
            job, saved_grant, "HOLD", "UNRESOLVED_PRODUCTION_STEP", observed=observed,
        )
        sidecars.save_production_result(result)
        return sidecars.load_production_result(job)

    hold_failure: str | None = None
    if job.configuration is None:
        hold_failure = "NO_MODEL_CONFIGURED"
    elif job.purpose == "PRODUCTION" and not (grant.allow_network and grant.allow_spend and grant.authorization_ref):
        hold_failure = "UNAUTHORIZED"
    elif job.purpose == "PRODUCTION" and job.configuration.effort in (None, "UNKNOWN"):
        hold_failure = "UNVERIFIED_MODEL_CONFIGURATION"
    elif job.purpose == "PRODUCTION" and cycle.status == "TEST_ONLY_MOCK":
        hold_failure = "MOCK_CYCLE_NOT_PRODUCTION"
    elif reasoner is None or reasoner.client is None:
        hold_failure = "NO_MODEL_CONFIGURED"
    elif reasoner.config is not None and reasoner.config != job.configuration:
        hold_failure = "CONFIGURATION_MISMATCH"
    elif reasoner.client.execution_mode != ("MODEL" if job.purpose == "PRODUCTION" else "TEST_ONLY_MOCK"):
        hold_failure = "ADAPTER_MODE_MISMATCH"

    if hold_failure is not None:
        result = A03CreativeProductionResult(job, grant, "HOLD", hold_failure)
        sidecars.save_production_result(result)
        return sidecars.load_production_result(job)

    request = _production_request(job, cycle, selected_record, parent_package)
    stage_job = _production_stage_job(job, request)
    stage = run_creative_stage(stage_job, sidecars, reasoner, authorization=grant)
    if stage.reasoning.state == "REFUSED":
        result = A03CreativeProductionResult(job, grant, "STOPPED", stage.failure or "MODEL_REFUSED", stage=stage)
    else:
        step_failure = _production_step_failure(stage, cycle, grant, parent_package)
        if step_failure is not None:
            result = A03CreativeProductionResult(job, grant, "HOLD", step_failure, stage=stage)
        else:
            r = stage.reasoning
            spec = r.production_spec
            rights_status = _package_rights_status(spec)
            if parent_package is None:
                pkg_id = package_identity(
                    job.brief_id, job.source_brief_digest, job.selected_concept,
                    lineage, spec, r.observation, r.digest,
                )
                package = A03CreativeProductionPackage(
                    package_id=pkg_id, revision=1,
                    brief_id=job.brief_id, source_brief_digest=job.source_brief_digest,
                    selected_concept=job.selected_concept, lineage=lineage, spec=spec,
                    observation=r.observation, usage=r.usage, configuration=r.configuration,
                    execution=r.execution, provider_request_id=r.provider_request_id,
                    reasoning_receipt_digest=r.digest, evidence_refs=r.evidence_refs,
                    hypotheses=r.hypotheses, parent=None, changed_sections=(),
                    edit_reason=None, edit_actor=ActorRef("AI", "reasoning:" + r.digest),
                    claim_authority="GENERATED_CLAIM", factual_status="UNVERIFIED",
                    rights_status=rights_status,
                )
            else:
                changed = tuple(sorted(name for name in EDITABLE_PACKAGE_FIELDS
                                       if getattr(parent_package.spec, name) != getattr(spec, name)))
                package = A03CreativeProductionPackage(
                    package_id=parent_package.package_id, revision=parent_package.revision + 1,
                    brief_id=job.brief_id, source_brief_digest=job.source_brief_digest,
                    selected_concept=job.selected_concept, lineage=lineage, spec=spec,
                    observation=r.observation, usage=r.usage, configuration=r.configuration,
                    execution=r.execution, provider_request_id=r.provider_request_id,
                    reasoning_receipt_digest=r.digest, evidence_refs=r.evidence_refs,
                    hypotheses=r.hypotheses,
                    parent=ProductionPackageParent(
                        parent_package.package_id, parent_package.revision, parent_package.digest,
                    ),
                    changed_sections=changed, edit_reason=job.edit_reason,
                    edit_actor=ActorRef("AI", "reasoning:" + r.digest),
                    claim_authority="GENERATED_CLAIM", factual_status="UNVERIFIED",
                    rights_status=rights_status,
                )
            try:
                saved_pkg = sidecars.save_production_package(package)
            except VersionConflict:
                result = A03CreativeProductionResult(
                    job, grant, "HOLD", "ARTIFACT_CONFLICT", stage=stage, observed=stage.reasoning,
                )
            else:
                result = A03CreativeProductionResult(
                    job, grant, stage.status, None, stage=stage, package=saved_pkg,
                )
    sidecars.save_production_result(result)
    return sidecars.load_production_result(job)
