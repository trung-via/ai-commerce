"""Opt-in V2 creative requests and proposals; V1 APIs and identities stay intact."""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import monotonic_ns
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import (
    CreativeValidationError, VersionConflict, exact_fields, positive_int, read_json, require_text, text_tuple,
)
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
    from .live import BenchmarkResearchReceipt, BenchmarkResearchRequest, InvocationAuthorization, LiveCreativeReasoner
    from .sidecars import CreativeSidecarStore
    from .store import CreativeStore


_PARSE_LOCAL = threading.local()
_PARSE_SCOPE_MAX = 65536


class _ParseScopeState:
    __slots__ = ("verified", "briefs")

    def __init__(self) -> None:
        self.verified: dict[int, Any] = {}
        self.briefs: set[str] = set()


@contextmanager
def _parse_scope() -> Iterator[_ParseScopeState]:
    """Bounded single-operation parse scope avoiding exponential dataclass re-parsing.

    Never persists across calls. Retains strong references for exact-type
    immutable records validated during this single operation so object IDs
    cannot be recycled. Any unverified or outside-scope object is fully
    revalidated via ``from_dict(obj.to_dict())``.
    """
    existing = getattr(_PARSE_LOCAL, "scope", None)
    if existing is not None:
        yield existing
        return
    scope = _ParseScopeState()
    _PARSE_LOCAL.scope = scope
    try:
        yield scope
    finally:
        _PARSE_LOCAL.scope = None


def _mark_verified(obj: Any, expected_cls: type[Any] | None = None) -> Any:
    if expected_cls is not None and type(obj) is not expected_cls:
        return obj
    scope = getattr(_PARSE_LOCAL, "scope", None)
    if scope is not None and len(scope.verified) < _PARSE_SCOPE_MAX:
        scope.verified[id(obj)] = obj
    return obj


def _is_json_native(value: Any) -> bool:
    t = type(value)
    if t in (str, int, float, bool) or value is None:
        return True
    if t is list:
        return all(_is_json_native(item) for item in value)
    if t is dict:
        return all(type(k) is str and _is_json_native(v) for k, v in value.items())
    return False


def _revalidate(obj: Any, cls: type[Any]) -> Any:
    if type(obj) is cls:
        scope = getattr(_PARSE_LOCAL, "scope", None)
        if scope is not None and scope.verified.get(id(obj)) is obj:
            return obj
    return _mark_verified(cls.from_dict(obj.to_dict()), cls)


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
        return _mark_verified(cls(**exact_fields(data, {"provider", "model"}, {"effort"})), cls)


@dataclass(frozen=True, slots=True)
class UsageObservation:
    """Only adapter-reported measurements; missing values are never zero."""

    status: Literal["UNKNOWN", "OBSERVED"] = "UNKNOWN"
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_amount: float | None = None
    currency: str | None = None
    cost_source_ref: str | None = None

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
            if self.cost_source_ref is not None:
                require_text(self.cost_source_ref, "usage.cost_source_ref")
        elif self.currency is not None or self.cost_source_ref is not None:
            raise CreativeValidationError("currency and cost source require an observed cost")
        measured = any(v is not None for v in (self.input_tokens, self.output_tokens, self.cost_amount))
        if (self.status == "UNKNOWN" and measured) or (self.status == "OBSERVED" and not measured):
            raise CreativeValidationError("usage status disagrees with measurements")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_amount": self.cost_amount, "currency": self.currency,
                **({"cost_source_ref": self.cost_source_ref} if self.cost_source_ref is not None else {})}

    @classmethod
    def from_dict(cls, data: object) -> UsageObservation:
        optional = {"cost_source_ref"}
        value = exact_fields(data, {f.name for f in fields(cls)} - optional, optional)
        if "cost_source_ref" in value:
            require_text(value["cost_source_ref"], "usage.cost_source_ref")
        return _mark_verified(cls(**value), cls)


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
        return _mark_verified(cls(**exact_fields(data, {f.name for f in fields(cls)})), cls)


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
        return _mark_verified(cls(**exact_fields(data, {f.name for f in fields(cls)})), cls)


@dataclass(frozen=True, slots=True)
class CreativeCriticEvidence:
    selection: ConceptSelection
    receipt_digest: str
    request_digest: str
    critique: str

    def __post_init__(self) -> None:
        if not isinstance(self.selection, ConceptSelection):
            raise CreativeValidationError("critic evidence needs a concept pin")
        _revalidate(self.selection, ConceptSelection)
        require_digest(self.receipt_digest)
        require_digest(self.request_digest)
        require_text(self.critique, "critic evidence")
        _mark_verified(self, CreativeCriticEvidence)

    def to_dict(self) -> dict[str, Any]:
        return {"selection": self.selection.to_dict(), "receipt_digest": self.receipt_digest,
                "request_digest": self.request_digest, "critique": self.critique}

    @classmethod
    def from_dict(cls, data: object) -> CreativeCriticEvidence:
        with _parse_scope():
            value = exact_fields(data, {f.name for f in fields(cls)})
            value["selection"] = _mark_verified(ConceptSelection.from_dict(value["selection"]), ConceptSelection)
            return _mark_verified(cls(**value), cls)


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
        with _parse_scope():
            require_digest(self.cycle_digest)
            require_text(self.step, "cycle step")
            for name, kind in (("evidence", CreativeEvidenceSource), ("candidates", ConceptRecord),
                               ("critics", CreativeCriticEvidence)):
                values = getattr(self, name)
                if not isinstance(values, (tuple, list)) or any(not isinstance(v, kind) for v in values):
                    raise CreativeValidationError("expected typed cycle context")
                object.__setattr__(self, name, tuple(_revalidate(v, kind) for v in values))
            for values in ((e.source_ref for e in self.evidence), (c.digest for c in self.candidates),
                           (c.receipt_digest for c in self.critics)):
                values = tuple(values)
                if len(set(values)) != len(values):
                    raise CreativeValidationError("duplicate cycle evidence or candidates")
            if self.selector_receipt is not None:
                require_digest(self.selector_receipt)
            _mark_verified(self, CreativeCycleContext)

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
        with _parse_scope():
            value = exact_fields(data, {f.name for f in fields(cls)} | {"research_status"})
            status = value.pop("research_status")
            for name, kind in (("evidence", CreativeEvidenceSource), ("candidates", ConceptRecord),
                               ("critics", CreativeCriticEvidence)):
                if not isinstance(value[name], list):
                    raise CreativeValidationError("expected cycle context array")
                value[name] = tuple(_mark_verified(kind.from_dict(v), kind) for v in value[name])
            result = cls(**value)
            if status != result.research_status:
                raise CreativeValidationError("research status disagrees with source evidence")
            return _mark_verified(result, cls)


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
        with _parse_scope():
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
            _revalidate(self.selected_concept, ConceptSelection)
            if (self.selected_concept.brief_id != self.brief_id
                    or self.selected_concept.source_brief_digest != self.source_brief_digest):
                raise CreativeValidationError("selected_concept does not match package source brief")
            if not isinstance(self.lineage, ProductionLineage):
                raise CreativeValidationError("expected typed ProductionLineage")
            _revalidate(self.lineage, ProductionLineage)
            for pin in self.lineage.candidate_pins:
                if pin.brief_id != self.brief_id or pin.source_brief_digest != self.source_brief_digest:
                    raise CreativeValidationError("production lineage candidate rebound brief")
            if not isinstance(self.spec, ProductionPackageSpec):
                raise CreativeValidationError("expected typed ProductionPackageSpec")
            _revalidate(self.spec, ProductionPackageSpec)
            if not isinstance(self.observation, ProviderObservation) or not isinstance(self.usage, UsageObservation):
                raise CreativeValidationError("expected typed observation and usage")
            _revalidate(self.observation, ProviderObservation)
            _revalidate(self.usage, UsageObservation)
            if self.configuration is not None:
                if not isinstance(self.configuration, ModelConfiguration):
                    raise CreativeValidationError("expected typed ModelConfiguration")
                _revalidate(self.configuration, ModelConfiguration)
            if self.execution is not None:
                if not isinstance(self.execution, CLIExecutionObservation):
                    raise CreativeValidationError("expected typed CLIExecutionObservation")
                _revalidate(self.execution, CLIExecutionObservation)
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
            _revalidate(self.edit_actor, ActorRef)
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
                _revalidate(self.parent, ProductionPackageParent)
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
            _mark_verified(self, A03CreativeProductionPackage)

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
        with _parse_scope():
            value = exact_fields(data, {f.name for f in fields(cls)})
            value["selected_concept"] = _mark_verified(ConceptSelection.from_dict(value["selected_concept"]), ConceptSelection)
            value["lineage"] = _mark_verified(ProductionLineage.from_dict(value["lineage"]), ProductionLineage)
            value["spec"] = _mark_verified(ProductionPackageSpec.from_dict(value["spec"]), ProductionPackageSpec)
            value["observation"] = _mark_verified(ProviderObservation.from_dict(value["observation"]), ProviderObservation)
            value["usage"] = UsageObservation.from_dict(value["usage"])
            if value["configuration"] is not None:
                value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
            if value["execution"] is not None:
                value["execution"] = CLIExecutionObservation.from_dict(value["execution"])
            if value["parent"] is not None:
                value["parent"] = _mark_verified(ProductionPackageParent.from_dict(value["parent"]), ProductionPackageParent)
            value["edit_actor"] = _mark_verified(ActorRef.from_dict(value["edit_actor"]), ActorRef)
            for name in ("evidence_refs", "hypotheses", "changed_sections"):
                if not isinstance(value[name], list):
                    raise CreativeValidationError(f"expected {name} array")
            return _mark_verified(cls(**value), cls)

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
        with _parse_scope():
            require_digest(self.production_job_digest)
            if not isinstance(self.lineage, ProductionLineage):
                raise CreativeValidationError("expected typed ProductionLineage")
            _revalidate(self.lineage, ProductionLineage)
            if not isinstance(self.evidence, (tuple, list)) or any(
                not isinstance(e, CreativeEvidenceSource) for e in self.evidence
            ):
                raise CreativeValidationError("expected typed production evidence")
            items = tuple(_revalidate(e, CreativeEvidenceSource) for e in self.evidence)
            refs = tuple(e.source_ref for e in items)
            if len(set(refs)) != len(refs):
                raise CreativeValidationError("duplicate production evidence source_ref")
            object.__setattr__(self, "evidence", items)
            if self.parent_package is not None:
                if not isinstance(self.parent_package, A03CreativeProductionPackage):
                    raise CreativeValidationError("expected typed parent_package")
                _revalidate(self.parent_package, A03CreativeProductionPackage)
            _mark_verified(self, CreativeProductionContext)

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
        with _parse_scope():
            value = exact_fields(data, {f.name for f in fields(cls)} | {"research_status"})
            status = value.pop("research_status")
            value["lineage"] = _mark_verified(ProductionLineage.from_dict(value["lineage"]), ProductionLineage)
            if not isinstance(value["evidence"], list):
                raise CreativeValidationError("expected production evidence array")
            value["evidence"] = tuple(CreativeEvidenceSource.from_dict(e) for e in value["evidence"])
            if value["parent_package"] is not None:
                value["parent_package"] = A03CreativeProductionPackage.from_dict(value["parent_package"])
            result = cls(**value)
            if status != result.research_status:
                raise CreativeValidationError("research status disagrees with source evidence")
            return _mark_verified(result, cls)


@dataclass(frozen=True, slots=True)
class BenchmarkRoleContext:
    """Provider input pinned to a trial attempt, with independently collected evidence."""

    plan_digest: str
    sample_id: str
    attempt: int
    research_receipt_digest: str | None = None
    evidence: tuple[CreativeEvidenceSource, ...] = ()
    generator_receipt_digest: str | None = None
    critic: CreativeCriticEvidence | None = None

    def __post_init__(self) -> None:
        require_digest(self.plan_digest)
        require_text(self.sample_id, "sample_id")
        positive_int(self.attempt, "benchmark role attempt")
        if self.research_receipt_digest is not None:
            require_digest(self.research_receipt_digest)
        if self.generator_receipt_digest is not None:
            require_digest(self.generator_receipt_digest)
        if bool(self.evidence) != (self.research_receipt_digest is not None):
            raise CreativeValidationError("benchmark evidence requires its research receipt")
        if not isinstance(self.evidence, tuple) or any(not isinstance(item, CreativeEvidenceSource)
                                                       for item in self.evidence):
            raise CreativeValidationError("expected typed benchmark research evidence")
        if self.critic is not None and not isinstance(self.critic, CreativeCriticEvidence):
            raise CreativeValidationError("expected typed benchmark critic evidence")

    def to_dict(self) -> dict[str, Any]:
        return {"plan_digest": self.plan_digest, "sample_id": self.sample_id, "attempt": self.attempt,
                "research_receipt_digest": self.research_receipt_digest,
                "evidence": [item.to_dict() for item in self.evidence],
                "generator_receipt_digest": self.generator_receipt_digest,
                "critic": self.critic.to_dict() if self.critic else None}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkRoleContext:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if not isinstance(value["evidence"], list):
            raise CreativeValidationError("expected benchmark evidence array")
        value["evidence"] = tuple(CreativeEvidenceSource.from_dict(item) for item in value["evidence"])
        if value["critic"] is not None:
            value["critic"] = CreativeCriticEvidence.from_dict(value["critic"])
        return cls(**value)


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
    benchmark: BenchmarkRoleContext | None = None

    def __post_init__(self) -> None:
        with _parse_scope() as scope:
            if type(self.brief_json) is str and self.brief_json in scope.briefs:
                valid = read_json(self.brief_json)
                digest = sha256_bytes(self.brief_json.encode("utf-8"))
            else:
                valid = intake_brief(read_json(self.brief_json))
                brief_canonical = canonical_bytes(valid)
                brief_str = brief_canonical.decode("utf-8")
                object.__setattr__(self, "brief_json", brief_str)
                digest = sha256_bytes(brief_canonical)
                if len(scope.briefs) < _PARSE_SCOPE_MAX:
                    scope.briefs.add(brief_str)
            r2 = self.schema_version == "a03.reasoning-request.v3"
            r3 = self.schema_version == "a03.reasoning-request.v4"
            if self.schema_version not in (
                "a03.reasoning-request.v2", "a03.reasoning-request.v3", "a03.reasoning-request.v4",
                "a03.reasoning-request.v5",
            ):
                raise CreativeValidationError("unsupported reasoning request schema")
            if self.operation not in (
                "EXPLORE", "REFRAME", "DEVELOP", "CRITIQUE", "SELECT", "PRODUCE_PACKAGE", "REVISE_PACKAGE"
            ):
                raise CreativeValidationError("invalid creative operation")
            if r3:
                if self.cycle is not None or not isinstance(self.production, CreativeProductionContext):
                    raise CreativeValidationError("R3 requires typed production context only")
                _revalidate(self.production, CreativeProductionContext)
                if self.operation not in ("PRODUCE_PACKAGE", "REVISE_PACKAGE"):
                    raise CreativeValidationError("unsupported R3 operation")
                if self.mode != "diverge" or self.count is not None or self.lens is not None:
                    raise CreativeValidationError("R3 package request cannot set count, lens or controlled_variants")
                if not {e.source_ref for e in self.production.evidence} <= set(valid.get("source_refs", [])):
                    raise CreativeValidationError("external evidence must belong to the exact source brief")
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
                _revalidate(self.cycle, CreativeCycleContext)
                if not {e.source_ref for e in self.cycle.evidence} <= set(valid.get("source_refs", [])):
                    raise CreativeValidationError("external evidence must belong to the exact source brief")
                for candidate in self.cycle.candidates:
                    if candidate.brief_id != valid["brief_id"] or candidate.source_brief_digest != digest:
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
            for item, expected in ((self.intent, CreativeIntent), (self.source, ConceptRecord)):
                if item is not None:
                    if not isinstance(item, expected):
                        raise CreativeValidationError("invalid typed creative request context")
                    _revalidate(item, expected)
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
            if self.schema_version == "a03.reasoning-request.v5":
                context = self.benchmark
                if (not isinstance(context, BenchmarkRoleContext)
                        or self.operation not in ("EXPLORE", "CRITIQUE", "DEVELOP")):
                    raise CreativeValidationError("expected typed benchmark role request")
                if (context.generator_receipt_digest is None) != (self.operation == "EXPLORE"):
                    raise CreativeValidationError("benchmark critique and development require the generator receipt")
                if self.operation == "DEVELOP":
                    if (context.critic is None or context.critic.selection != _output_pin(self.source)
                            or context.research_receipt_digest is None):
                        raise CreativeValidationError("bounded development requires research and exact independent critique")
                elif context.critic is not None:
                    raise CreativeValidationError("critic evidence belongs to benchmark development")
            elif self.benchmark is not None:
                raise CreativeValidationError("benchmark context requires v5 request")
            _mark_verified(self, ReasoningRequest)

    @classmethod
    def create(cls, brief: Any, **options: Any) -> ReasoningRequest:
        return cls(canonical_bytes(intake_brief(brief)).decode("utf-8"), **options)

    @property
    def brief(self) -> dict[str, Any]:
        scope = getattr(_PARSE_LOCAL, "scope", None)
        if scope is not None and type(self.brief_json) is str and self.brief_json in scope.briefs:
            return read_json(self.brief_json)
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
            **({"benchmark": self.benchmark.to_dict()} if self.benchmark is not None else {}),
        }

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ReasoningRequest:
        with _parse_scope() as scope:
            value = exact_fields(data, {"schema_version", "brief", "operation", "mode", "count",
                                        "intent", "source", "instruction", "lens"}, {"cycle", "production", "benchmark"})
            if (value["schema_version"] == "a03.reasoning-request.v3") != ("cycle" in value):
                raise CreativeValidationError("cycle field requires R2 request schema")
            if (value["schema_version"] == "a03.reasoning-request.v4") != ("production" in value):
                raise CreativeValidationError("production field requires R3 request schema")
            if "cycle" in value:
                value["cycle"] = CreativeCycleContext.from_dict(value["cycle"])
            if "production" in value:
                value["production"] = CreativeProductionContext.from_dict(value["production"])
            if (value["schema_version"] == "a03.reasoning-request.v5") != ("benchmark" in value):
                raise CreativeValidationError("benchmark field requires v5 request")
            if "benchmark" in value:
                value["benchmark"] = BenchmarkRoleContext.from_dict(value["benchmark"])
            raw_brief = value.pop("brief")
            brief_str = None
            if type(raw_brief) is dict and _is_json_native(raw_brief):
                try:
                    candidate_str = canonical_bytes(raw_brief).decode("utf-8")
                except (TypeError, ValueError):
                    candidate_str = None
                if candidate_str is not None and candidate_str in scope.briefs:
                    brief_str = candidate_str
            if brief_str is None:
                brief_str = canonical_bytes(intake_brief(raw_brief)).decode("utf-8")
                if len(scope.briefs) < _PARSE_SCOPE_MAX:
                    scope.briefs.add(brief_str)
            value["brief_json"] = brief_str
            if value["intent"] is not None:
                value["intent"] = _mark_verified(CreativeIntent.from_dict(value["intent"]), CreativeIntent)
            if value["source"] is not None:
                value["source"] = _mark_verified(ConceptRecord.from_dict(value["source"]), ConceptRecord)
            return _mark_verified(cls(**value), cls)


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
        with _parse_scope():
            if not isinstance(self.request, ReasoningRequest):
                raise CreativeValidationError("expected typed reasoning request")
            _revalidate(self.request, ReasoningRequest)
            r2 = self.request.cycle is not None
            r3 = self.request.production is not None
            benchmark = self.request.benchmark
            if self.schema_version != _result_schema(self.request):
                raise CreativeValidationError("unsupported reasoning result schema")
            if not r2 and self.selection is not None:
                raise CreativeValidationError("selection belongs only to R2 request")
            if not r3 and self.production_spec is not None:
                raise CreativeValidationError("production_spec belongs only to R3 request")
            if not (r2 or r3) and (self.evidence_refs or self.hypotheses
                                   or (benchmark is None and self.response_json is not None)):
                raise CreativeValidationError("R2/R3 result fields require R2/R3 request")
            for name in ("evidence_refs", "hypotheses"):
                object.__setattr__(self, name, text_tuple(getattr(self, name), name))
            if r2 or r3 or benchmark is not None:
                context = self.request.cycle or self.request.production or benchmark
                supplied = context.evidence
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
                    if benchmark is not None and self.state == "REFUSED" and response.state != "REFUSED":
                        raise CreativeValidationError("reasoning state differs from observed response")
                    if self.state == "OK":
                        if response.state != "OK":
                            raise CreativeValidationError("successful result needs observed successful response")
                        parsed = parse_creative_output(self.request, response.output_json)
                        if any(getattr(self, name) != val for name, val in parsed.items()):
                            raise CreativeValidationError("result differs from immutable model output")
                        model_request = None
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
                            _, schema, packet = _cli_request(model_request)
                            if (response.cli_stdin_json.encode("utf-8") != packet
                                    or response.cli_schema_json.encode("utf-8") != canonical_bytes(schema)):
                                raise CreativeValidationError("native stdin/schema rebound its model request")
                elif self.state == "OK" or (benchmark is not None and self.state == "REFUSED"):
                    raise CreativeValidationError("reasoning outcome requires immutable observed model response")
            if self.state not in ("OK", "NO_MODEL_CONFIGURED", "UNAUTHORIZED", "REFUSED", "ERROR"):
                raise CreativeValidationError("invalid reasoning state")
            if not isinstance(self.observation, ProviderObservation) or not isinstance(self.usage, UsageObservation):
                raise CreativeValidationError("expected typed result metadata")
            _revalidate(self.observation, ProviderObservation)
            if self.configuration is not None:
                if not isinstance(self.configuration, ModelConfiguration):
                    raise CreativeValidationError("expected typed model configuration")
                _revalidate(self.configuration, ModelConfiguration)
            _revalidate(self.usage, UsageObservation)
            if self.execution is not None:
                if not isinstance(self.execution, CLIExecutionObservation):
                    raise CreativeValidationError("expected typed CLI evidence")
                _revalidate(self.execution, CLIExecutionObservation)
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
                object.__setattr__(self, name, tuple(_revalidate(v, expected) for v in value))
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
                _mark_verified(self, ReasoningResult)
                return
            if self.error_code is not None or self.message is not None:
                raise CreativeValidationError("successful output cannot assert an error")
            op = self.request.operation
            if op in ("PRODUCE_PACKAGE", "REVISE_PACKAGE"):
                if (self.drafts or self.edits or self.critique is not None or self.selection is not None
                        or not isinstance(self.production_spec, ProductionPackageSpec)):
                    raise CreativeValidationError("R3 package operation requires typed production_spec only")
                _revalidate(self.production_spec, ProductionPackageSpec)
                _validate_spec_evidence(
                    self.production_spec, self.request.brief,
                    self.request.production.evidence, self.evidence_refs,
                )
                _mark_verified(self, ReasoningResult)
                return
            if self.production_spec is not None:
                raise CreativeValidationError("production_spec belongs only to R3 package operations")
            if op == "SELECT":
                if self.drafts or self.edits or self.critique is not None or not isinstance(self.selection, CreativeModelSelection):
                    raise CreativeValidationError("SELECT requires typed selection output only")
                _revalidate(self.selection, CreativeModelSelection)
                if (self.selection.critique_receipts != tuple(c.receipt_digest for c in self.request.cycle.critics)
                        or (self.selection.selection is not None and self.selection.selection not in
                            tuple(_output_pin(c) for c in self.request.cycle.candidates))):
                    raise CreativeValidationError("model selection does not bind candidates and critic evidence")
                _mark_verified(self, ReasoningResult)
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
                elif benchmark is not None and benchmark.research_receipt_digest is not None:
                    known_refs &= {e.source_ref for e in benchmark.evidence}
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
            _mark_verified(self, ReasoningResult)

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
            **({"response_json": self.response_json} if self.request.benchmark is not None else {}),
        }

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, data: object) -> ReasoningResult:
        with _parse_scope():
            r2_fields = {"selection", "evidence_refs", "hypotheses", "response_json"}
            r3_fields = {"production_spec", "evidence_refs", "hypotheses", "response_json"}
            optional = {"execution"} | r2_fields | r3_fields
            value = exact_fields(data, {f.name for f in fields(cls)} - optional, optional)
            value["request"] = ReasoningRequest.from_dict(value["request"])
            if value["request"].cycle is not None:
                if not r2_fields <= value.keys() or "production_spec" in value:
                    raise CreativeValidationError("missing or unexpected R2 result fields")
                if value["selection"] is not None:
                    value["selection"] = _mark_verified(
                        CreativeModelSelection.from_dict(value["selection"]), CreativeModelSelection
                    )
            elif value["request"].production is not None:
                if not r3_fields <= value.keys() or "selection" in value:
                    raise CreativeValidationError("missing or unexpected R3 result fields")
                if value["production_spec"] is not None:
                    value["production_spec"] = _mark_verified(
                        ProductionPackageSpec.from_dict(value["production_spec"]), ProductionPackageSpec
                    )
            elif value["request"].benchmark is not None:
                if (value.keys() & (r2_fields | r3_fields)) != {"response_json"}:
                    raise CreativeValidationError("missing or unexpected benchmark result fields")
            elif (r2_fields | r3_fields) & value.keys():
                raise CreativeValidationError("unexpected R2/R3 fields in historical result")
            for name, expected in (("drafts", ConceptDraft), ("edits", ConceptChange)):
                if not isinstance(value[name], list):
                    raise CreativeValidationError("expected creative output array")
                value[name] = tuple(_mark_verified(expected.from_dict(v), expected) for v in value[name])
            value["observation"] = _mark_verified(ProviderObservation.from_dict(value["observation"]), ProviderObservation)
            value["usage"] = UsageObservation.from_dict(value["usage"])
            if value["configuration"] is not None:
                value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
            if value.get("execution") is not None:
                value["execution"] = CLIExecutionObservation.from_dict(value["execution"])
            return _mark_verified(cls(**value), cls)

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
    if request.benchmark is not None:
        return "a03.reasoning-result.v5"
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
        return _mark_verified(cls(**exact_fields(data, {f.name for f in fields(cls)})), cls)


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
        with _parse_scope():
            if self.schema_version != "A03CreativeJob.v1":
                raise CreativeValidationError("unsupported creative job schema")
            for name in ("journey_id", "cycle_id", "job_id", "correlation_id", "brief_id"):
                require_text(getattr(self, name), name)
            positive_int(self.attempt, "attempt")
            if not isinstance(self.provenance, CreativeJobProvenance) or not isinstance(self.request, ReasoningRequest):
                raise CreativeValidationError("expected typed creative job context")
            _revalidate(self.provenance, CreativeJobProvenance)
            _revalidate(self.request, ReasoningRequest)
            require_digest(self.source_brief_digest)
            require_digest(self.request_digest)
            req_brief = self.request.brief
            if (self.brief_id != req_brief["brief_id"]
                    or self.source_brief_digest != brief_digest(req_brief)
                    or self.request_digest != self.request.digest):
                raise CreativeValidationError("creative job request/source hash mismatch")
            if self.configuration is not None:
                if not isinstance(self.configuration, ModelConfiguration):
                    raise CreativeValidationError("expected requested model configuration")
                _revalidate(self.configuration, ModelConfiguration)
            if self.purpose not in ("PRODUCTION", "TEST_ONLY_MOCK"):
                raise CreativeValidationError("invalid creative job purpose")
            if self.request.benchmark is not None:
                context = self.request.benchmark
                if (self.journey_id, self.cycle_id, self.job_id, self.attempt, self.correlation_id) != (
                    f"benchmark:{context.plan_digest}", context.sample_id, _role_for_operation(self.request.operation),
                    context.attempt, f"{context.sample_id}:{self.request.operation}:{context.attempt}",
                ):
                    raise CreativeValidationError("benchmark job rebound role attempt identity")
            if self.selection_index is not None:
                if type(self.selection_index) is not int or self.selection_index < 0:
                    raise CreativeValidationError("selection_index must be a nonnegative integer or null")
                if (self.request.operation in ("CRITIQUE", "SELECT", "PRODUCE_PACKAGE", "REVISE_PACKAGE")
                        or (self.request.operation != "EXPLORE" and self.selection_index != 0)
                        or (self.request.count is not None and self.selection_index >= self.request.count)):
                    raise CreativeValidationError("selection_index outside requested output")
            _mark_verified(self, A03CreativeJob)

    @classmethod
    def create(cls, request: ReasoningRequest, **context: Any) -> A03CreativeJob:
        if not isinstance(request, ReasoningRequest):
            raise CreativeValidationError("expected ReasoningRequest")
        req_brief = request.brief
        return cls(request=request, brief_id=req_brief["brief_id"],
                   source_brief_digest=brief_digest(req_brief), request_digest=request.digest, **context)

    def to_dict(self) -> dict[str, Any]:
        return {**{f.name: getattr(self, f.name) for f in fields(self)},
                "request": self.request.to_dict(), "provenance": self.provenance.to_dict(),
                "configuration": self.configuration.to_dict() if self.configuration else None}

    @classmethod
    def from_dict(cls, data: object) -> A03CreativeJob:
        with _parse_scope():
            value = exact_fields(data, {f.name for f in fields(cls)})
            value["request"] = ReasoningRequest.from_dict(value["request"])
            value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
            if value["configuration"] is not None:
                value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
            return _mark_verified(cls(**value), cls)

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
        if result.request.benchmark is not None:
            # Preserve the trial's proposal in its receipt without committing
            # an alternate revision to the shared concept's production lineage.
            return ()
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
        with _parse_scope():
            if self.schema_version != "A03CreativeStageResult.v1":
                raise CreativeValidationError("unsupported creative stage schema")
            if not isinstance(self.job, A03CreativeJob) or not isinstance(self.reasoning, ReasoningResult):
                raise CreativeValidationError("expected typed creative stage context")
            _revalidate(self.job, A03CreativeJob)
            _revalidate(self.reasoning, ReasoningResult)
            if self.reasoning.request != self.job.request:
                raise CreativeValidationError("stage receipt rebound creative job request")
            if (self.status, self.failure) != _stage_outcome(self.job, self.reasoning):
                raise CreativeValidationError("stage status/failure disagrees with inference evidence")
            if not isinstance(self.outputs, (list, tuple)) or any(not isinstance(s, ConceptSelection) for s in self.outputs):
                raise CreativeValidationError("expected typed creative output references")
            object.__setattr__(self, "outputs", tuple(_revalidate(s, ConceptSelection) for s in self.outputs))
            successful = self.status in ("TEST_ONLY_MOCK", "CREATIVE_COMPLETE")
            expected = tuple(_output_pin(c) for c in _stage_concepts(self.reasoning)) if successful else ()
            if self.outputs != expected:
                raise CreativeValidationError("stage output references do not match reasoning output")
            selected = self.job.selection_index
            expected_decision = (inference_decision(self.reasoning, expected[selected])
                                 if successful and selected is not None else None)
            if self.decision != expected_decision:
                raise CreativeValidationError("stage selection does not match reasoning output")
            _mark_verified(self, A03CreativeStageResult)

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
        with _parse_scope():
            value = exact_fields(data, {f.name for f in fields(cls)})
            value["job"] = A03CreativeJob.from_dict(value["job"])
            value["reasoning"] = ReasoningResult.from_dict(value["reasoning"])
            if not isinstance(value["outputs"], list):
                raise CreativeValidationError("expected output reference array")
            value["outputs"] = tuple(_mark_verified(ConceptSelection.from_dict(s), ConceptSelection) for s in value["outputs"])
            if value["decision"] is not None:
                value["decision"] = _mark_verified(ActorDecision.from_dict(value["decision"]), ActorDecision)
            return _mark_verified(cls(**value), cls)


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
    return _run_creative_stage(job, store, reasoner, authorization=authorization)


def _run_creative_stage(
    job: A03CreativeJob, store: CreativeStore | CreativeSidecarStore, reasoner: CreativeReasonerV2 | None,
    *, authorization: InvocationAuthorization | None,
    record_timing: Callable[[A03CreativeJob, BenchmarkAttemptTiming], None] | None = None,
) -> A03CreativeStageResult:
    from .live import InvocationAuthorization, LiveCreativeReasoner
    from .sidecars import CreativeSidecarStore

    if not isinstance(job, A03CreativeJob):
        raise CreativeValidationError("expected A03CreativeJob")
    if job.request.benchmark is not None and record_timing is None:
        raise CreativeValidationError("benchmark job requires trial admission and timing")
    if authorization is not None:
        if not isinstance(authorization, InvocationAuthorization):
            raise CreativeValidationError("expected runner-side InvocationAuthorization")
        authorization = InvocationAuthorization(authorization.allow_network, authorization.allow_spend,
                                                authorization.authorization_ref)
    if record_timing is not None and (
        authorization is None or authorization.authorization_ref is None
        or authorization.allow_network or authorization.allow_spend or job.purpose != "TEST_ONLY_MOCK"
    ):
        raise CreativeValidationError("benchmark timing requires an explicit offline fixture grant")
    job = A03CreativeJob.from_dict(job.to_dict())
    sidecars = store if isinstance(store, CreativeSidecarStore) else CreativeSidecarStore(store)
    sidecars.creative.save_brief(job.request.brief)
    sidecars.validate_request(job.request)
    if not sidecars.claim_job(job):
        return sidecars.load_stage(job)
    if record_timing is not None:
        started_at = datetime.now(timezone.utc).isoformat()
        started_ns = monotonic_ns()
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
    if record_timing is not None:
        wall_ms = (monotonic_ns() - started_ns + 999_999) // 1_000_000
        completed_at = datetime.now(timezone.utc).isoformat()
    sidecars.save_result(result)
    if job.request.cycle is not None or job.request.production is not None or record_timing is not None:
        # Before materialization: conflicts/crashes must retain observed output.
        sidecars.save_observed_step(job, result)
    if record_timing is not None:
        record_timing(job, BenchmarkAttemptTiming(
            result.digest, authorization.authorization_ref, started_at, completed_at, wall_ms,
        ))
    return _commit_creative_stage(job, sidecars, result)


def _commit_creative_stage(job: A03CreativeJob, sidecars: CreativeSidecarStore,
                           result: ReasoningResult) -> A03CreativeStageResult:
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
        with _parse_scope():
            if self.schema_version != "A03CreativeCycleJob.v2" or not isinstance(self.seed, A03CreativeJob):
                raise CreativeValidationError("expected typed R2 creative cycle seed")
            _revalidate(self.seed, A03CreativeJob)
            request = self.seed.request
            if (request.operation != "EXPLORE" or request.mode != "diverge" or request.cycle is not None
                    or type(request.count) is not int or not 2 <= request.count <= 3
                    or self.seed.selection_index is not None):
                raise CreativeValidationError("cycle seed requires two or three divergent concepts, no system selection")
            if type(self.max_revisions) is not int or not 0 <= self.max_revisions <= 2:
                raise CreativeValidationError("cycle revision budget must be between zero and two")
            if not isinstance(self.evidence, (tuple, list)) or any(not isinstance(e, CreativeEvidenceSource) for e in self.evidence):
                raise CreativeValidationError("expected typed external evidence")
            object.__setattr__(self, "evidence", tuple(_revalidate(e, CreativeEvidenceSource) for e in self.evidence))
            refs = tuple(e.source_ref for e in self.evidence)
            if len(set(refs)) != len(refs) or not set(refs) <= set(request.brief.get("source_refs", [])):
                raise CreativeValidationError("cycle evidence must bind distinct original brief source refs")
            _mark_verified(self, A03CreativeCycleJob)

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
        with _parse_scope():
            value = exact_fields(data, {f.name for f in fields(cls)})
            value["seed"] = A03CreativeJob.from_dict(value["seed"])
            if not isinstance(value["evidence"], list):
                raise CreativeValidationError("expected evidence array")
            value["evidence"] = tuple(CreativeEvidenceSource.from_dict(e) for e in value["evidence"])
            return _mark_verified(cls(**value), cls)

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
        with _parse_scope():
            if self.schema_version != "A03CreativeCycleResult.v2" or not isinstance(self.job, A03CreativeCycleJob):
                raise CreativeValidationError("expected typed R2 creative cycle")
            _revalidate(self.job, A03CreativeCycleJob)
            if not isinstance(self.authorization, InvocationAuthorization):
                raise CreativeValidationError("expected runner authorization snapshot")
            InvocationAuthorization(self.authorization.allow_network, self.authorization.allow_spend,
                                    self.authorization.authorization_ref)
            if not isinstance(self.steps, (list, tuple)) or any(not isinstance(s, A03CreativeStageResult) for s in self.steps):
                raise CreativeValidationError("expected typed cycle steps")
            object.__setattr__(self, "steps", tuple(_revalidate(s, A03CreativeStageResult) for s in self.steps))
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
                    _revalidate(self.observed, ReasoningResult)
                    if self.observed.request != expected.request:
                        raise CreativeValidationError("halted observation rebound its request")
                terminal = ("HOLD", self.failure, None, None)
            if (self.status, self.failure, self.decision, self.output) != terminal:
                raise CreativeValidationError("cycle outcome disagrees with verified workflow evidence")
            _mark_verified(self, A03CreativeCycleResult)

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
        with _parse_scope():
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
                    value[name] = _mark_verified(kind.from_dict(value[name]), kind)
            return _mark_verified(cls(**value), cls)


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
        with _parse_scope():
            if self.schema_version != "A03CreativeProductionJob.v1":
                raise CreativeValidationError("unsupported creative production job schema")
            for name in ("journey_id", "cycle_id", "job_id", "correlation_id", "brief_id"):
                require_text(getattr(self, name), name)
            positive_int(self.attempt, "attempt")
            if not isinstance(self.provenance, CreativeJobProvenance):
                raise CreativeValidationError("expected typed production job provenance")
            _revalidate(self.provenance, CreativeJobProvenance)
            for name in ("cycle_job_digest", "cycle_result_digest", "source_brief_digest"):
                require_digest(getattr(self, name))
            if not isinstance(self.selected_concept, ConceptSelection):
                raise CreativeValidationError("expected typed selected_concept")
            _revalidate(self.selected_concept, ConceptSelection)
            if (self.selected_concept.brief_id != self.brief_id
                    or self.selected_concept.source_brief_digest != self.source_brief_digest):
                raise CreativeValidationError("production job selected_concept/brief mismatch")
            if self.configuration is not None:
                if not isinstance(self.configuration, ModelConfiguration):
                    raise CreativeValidationError("expected typed ModelConfiguration")
                _revalidate(self.configuration, ModelConfiguration)
            if self.purpose not in ("PRODUCTION", "TEST_ONLY_MOCK"):
                raise CreativeValidationError("invalid production job purpose")
            if self.instruction is not None:
                require_text(self.instruction, "instruction")
            if self.parent_package_digest is not None:
                require_digest(self.parent_package_digest)
                require_text(self.edit_reason, "edit_reason")
            elif self.edit_reason is not None:
                raise CreativeValidationError("edit_reason requires parent_package_digest")
            _mark_verified(self, A03CreativeProductionJob)

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
        with _parse_scope():
            value = exact_fields(data, {f.name for f in fields(cls)})
            value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
            value["selected_concept"] = _mark_verified(
                ConceptSelection.from_dict(value["selected_concept"]), ConceptSelection
            )
            if value["configuration"] is not None:
                value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
            return _mark_verified(cls(**value), cls)

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
        with _parse_scope():
            if self.schema_version != "A03CreativeProductionResult.v1" or not isinstance(self.job, A03CreativeProductionJob):
                raise CreativeValidationError("expected typed R3 production result")
            _revalidate(self.job, A03CreativeProductionJob)
            if not isinstance(self.authorization, InvocationAuthorization):
                raise CreativeValidationError("expected runner authorization snapshot")
            InvocationAuthorization(self.authorization.allow_network, self.authorization.allow_spend,
                                    self.authorization.authorization_ref)
            if self.status not in ("HOLD", "STOPPED", "TEST_ONLY_MOCK", "CREATIVE_COMPLETE"):
                raise CreativeValidationError("invalid production result status")
            job_digest = self.job.digest
            if self.stage is not None:
                if not isinstance(self.stage, A03CreativeStageResult):
                    raise CreativeValidationError("expected typed A03CreativeStageResult")
                _revalidate(self.stage, A03CreativeStageResult)
                ctx = self.stage.job.request.production
                if ctx is None or ctx.production_job_digest != job_digest:
                    raise CreativeValidationError("production stage receipt rebound its job")
            if self.package is not None:
                if not isinstance(self.package, A03CreativeProductionPackage):
                    raise CreativeValidationError("expected typed A03CreativeProductionPackage")
                _revalidate(self.package, A03CreativeProductionPackage)
            if self.observed is not None:
                if not isinstance(self.observed, ReasoningResult):
                    raise CreativeValidationError("expected typed observed ReasoningResult")
                _revalidate(self.observed, ReasoningResult)
                if (self.observed.request.production is None
                        or self.observed.request.production.production_job_digest != job_digest):
                    raise CreativeValidationError("observed production reasoning rebound its job")
            if self.status in ("TEST_ONLY_MOCK", "CREATIVE_COMPLETE"):
                if self.failure is not None or self.observed is not None or self.stage is None or self.package is None:
                    raise CreativeValidationError("completed production result needs exact stage and package without failure")
                r = self.stage.reasoning
                p = self.package
                ctx = self.stage.job.request.production
                r_digest = r.digest
                if (self.stage.status != self.status or r.state != "OK" or r.production_spec != p.spec
                        or self.stage.job.configuration != self.job.configuration or self.stage.job.purpose != self.job.purpose
                        or p.brief_id != self.job.brief_id or p.source_brief_digest != self.job.source_brief_digest
                        or p.selected_concept != self.job.selected_concept
                        or p.lineage != ctx.lineage
                        or p.lineage.cycle_job_digest != self.job.cycle_job_digest
                        or p.lineage.cycle_result_digest != self.job.cycle_result_digest
                        or p.reasoning_receipt_digest != r_digest
                        or p.observation != r.observation or p.usage != r.usage
                        or p.configuration != r.configuration or p.configuration != self.job.configuration
                        or p.execution != r.execution or p.provider_request_id != r.provider_request_id
                        or p.evidence_refs != r.evidence_refs or p.hypotheses != r.hypotheses
                        or p.edit_actor != ActorRef("AI", "reasoning:" + r_digest)):
                    raise CreativeValidationError("production package disagrees with verified inference receipt")
                if self.job.parent_package_digest is None:
                    if (self.stage.job.request.operation != "PRODUCE_PACKAGE"
                            or p.revision != 1 or p.parent is not None):
                        raise CreativeValidationError("initial production result must create revision 1")
                else:
                    parent = ctx.parent_package
                    parent_digest = parent.digest if parent is not None else None
                    if (self.stage.job.request.operation != "REVISE_PACKAGE"
                            or parent is None or parent_digest != self.job.parent_package_digest
                            or p.parent != ProductionPackageParent(parent.package_id, parent.revision, parent_digest)
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
            _mark_verified(self, A03CreativeProductionResult)

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
        with _parse_scope():
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
            return _mark_verified(cls(**value), cls)


def run_creative_production(
    job: A03CreativeProductionJob, store: CreativeStore | CreativeSidecarStore,
    reasoner: LiveCreativeReasoner | None = None, *,
    authorization: InvocationAuthorization | None = None,
    production: Any = None,
) -> A03CreativeProductionResult:
    """Produce or AI-revise one verified A03CreativeProductionPackage.v1.

    Pins exact verified R2 cycle + final ConceptSelection + source brief.
    Reuses LiveCreativeReasoner/StructuredCreativeClient/CLI seam with one
    distinct source-bound attempt. Never synthesizes from rules or retries.
    Optional ``production`` accepts only a trusted ProductionPipelineOrchestrator
    and triggers bounded local A03 -> A04 handoff solely for verified
    CREATIVE_COMPLETE results; replaying a saved result resumes delivery
    without reinference.
    """
    from .live import InvocationAuthorization, LiveCreativeReasoner
    from .sidecars import CreativeSidecarStore
    from ._validation import VersionConflict

    if production is not None:
        from ai_commerce.production_orchestration import ProductionPipelineOrchestrator
        if type(production) is not ProductionPipelineOrchestrator:
            raise CreativeValidationError("expected ProductionPipelineOrchestrator for production handoff")
        production.last_result = None
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
            loaded = sidecars.load_production_result(job)
            if production is not None and loaded.status == "CREATIVE_COMPLETE" and loaded.package is not None:
                production.complete_a03(loaded, store=sidecars)
            return loaded
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
    loaded = sidecars.load_production_result(job)
    if production is not None and loaded.status == "CREATIVE_COMPLETE" and loaded.package is not None:
        production.complete_a03(loaded, store=sidecars)
    return loaded


# ---------------------------------------------------------------------------
# Opt-in R5a benchmarking & evidence-led comparison foundation (SOURCE_ONLY).
# Zero real provider/media/spend/account/rights/HostOps/Wake/Send effects.
# ---------------------------------------------------------------------------

_SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

BENCHMARK_STRATEGIES: tuple[Literal["S1", "S2", "S3"], ...] = ("S1", "S2", "S3")
BENCHMARK_STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "S1": "one generator",
    "S2": "generator + independent critic",
    "S3": "source-research + generator + critic + bounded developer",
}
BENCHMARK_BRIEF_CLASSES: tuple[Literal["VN_PRODUCT", "VN_NON_PRODUCT_FICTION", "SILENT_NO_CTA"], ...] = (
    "VN_PRODUCT",
    "VN_NON_PRODUCT_FICTION",
    "SILENT_NO_CTA",
)
BENCHMARK_ROLES: tuple[str, ...] = (
    "RESEARCH", "GENERATOR", "CRITIC", "SELECTOR", "DEVELOPER", "PACKAGE_PRODUCER",
)
_ROLE_TO_OPS: dict[str, tuple[str, ...]] = {
    "RESEARCH": ("RESEARCH",),
    "GENERATOR": ("EXPLORE",),
    "CRITIC": ("CRITIQUE",),
    "SELECTOR": ("SELECT",),
    "DEVELOPER": ("DEVELOP",),
    "PACKAGE_PRODUCER": ("PRODUCE_PACKAGE", "REVISE_PACKAGE"),
}
BENCHMARK_RUBRIC_VERSION = "a03.benchmark-rubric.v1"
NSM_V1_VERSION = "NSM_v1"
NSM_V1_WEIGHTS: dict[str, float] = {
    "quality": 0.55,
    "token_efficiency": 0.20,
    "wall_time_efficiency": 0.15,
    "human_time_efficiency": 0.10,
}
NSM_V1_RUBRIC_WEIGHTS: dict[str, float] = {
    "brief_adherence": 0.25,
    "originality_non_duplication": 0.20,
    "factual_evidence_accuracy": 0.15,
    "script_scene_s0_action_s1_consistency": 0.20,
    "continuity_camera_audio_silence_rights_qa_readiness": 0.20,
}
NSM_V1_REFERENCES: dict[str, int] = {
    "total_tokens": 20000,
    "wall_time_ms": 60000,
    "human_active_time_ms": 60000,
}


def _require_source_sha(value: object, field: str = "source_sha") -> str:
    if type(value) is not str or not _SOURCE_SHA_RE.fullmatch(value):
        raise CreativeValidationError(f"{field}: expected 40- or 64-char lowercase hex git SHA")
    return value


def _require_iso_timestamp(value: object, field: str) -> datetime:
    text = require_text(value, field)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CreativeValidationError(f"{field}: expected timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CreativeValidationError(f"{field}: timestamp must include timezone offset or Z")
    return parsed


def _nonneg_int_or_none(value: object, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise CreativeValidationError(f"{field}: expected nonnegative integer or null")
    return value


def _nonneg_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise CreativeValidationError(f"{field}: expected nonnegative integer")
    return value


def _require_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise CreativeValidationError(f"{field}: expected boolean")
    return value


def _unit_score(value: object, field: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise CreativeValidationError(f"{field}: expected finite score in [0.0, 1.0]")
    return float(value)


def _distinct_digests(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise CreativeValidationError(f"{field}: expected list/tuple of SHA-256 digests")
    items = tuple(require_digest(v) for v in value)
    if len(set(items)) != len(items):
        raise CreativeValidationError(f"{field}: duplicate digests")
    return items


def _distinct_texts(value: object, field: str) -> tuple[str, ...]:
    items = text_tuple(value, field)
    if len(set(items)) != len(items):
        raise CreativeValidationError(f"{field}: duplicate values")
    return items


def _benchmark_case_slot(cohort_id: str, case_id: str) -> str:
    return sha256_bytes(canonical_bytes({"cohort_id": cohort_id, "case_id": case_id}))


def _benchmark_observation_slot(
    cohort_id: str, case_id: str, strategy: str, sample_id: str, attempt: int,
) -> str:
    return sha256_bytes(canonical_bytes({
        "cohort_id": cohort_id,
        "case_id": case_id,
        "strategy": strategy,
        "sample_id": sample_id,
        "attempt": attempt,
    }))


def _benchmark_comparison_slot(cohort_id: str, comparison_id: str) -> str:
    return sha256_bytes(canonical_bytes({"cohort_id": cohort_id, "comparison_id": comparison_id}))


@dataclass(frozen=True, slots=True)
class A03BenchmarkCase:
    """Pinned cohort benchmark case; preserves ContentBrief.v0.1 unchanged."""

    case_id: str
    cohort_id: str
    source_sha: str
    brief_id: str
    source_brief_digest: str
    brief_class: Literal["VN_PRODUCT", "VN_NON_PRODUCT_FICTION", "SILENT_NO_CTA"]
    factual_evidence_applicable: bool
    provenance: CreativeJobProvenance
    created_at: str
    language: str = "vi"
    expected_evidence_refs: tuple[str, ...] = ()
    rubric_version: str = BENCHMARK_RUBRIC_VERSION
    schema_version: str = "A03BenchmarkCase.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "A03BenchmarkCase.v1":
            raise CreativeValidationError("unsupported benchmark case schema")
        if self.rubric_version != BENCHMARK_RUBRIC_VERSION:
            raise CreativeValidationError("unsupported benchmark rubric version")
        for name in ("case_id", "cohort_id", "brief_id", "language"):
            require_text(getattr(self, name), name)
        _require_source_sha(self.source_sha, "source_sha")
        require_digest(self.source_brief_digest)
        if self.brief_class not in BENCHMARK_BRIEF_CLASSES:
            raise CreativeValidationError("unsupported benchmark brief_class")
        _require_bool(self.factual_evidence_applicable, "factual_evidence_applicable")
        if not isinstance(self.provenance, CreativeJobProvenance):
            raise CreativeValidationError("expected typed benchmark case provenance")
        CreativeJobProvenance.from_dict(self.provenance.to_dict())
        _require_iso_timestamp(self.created_at, "created_at")
        object.__setattr__(self, "expected_evidence_refs",
                           _distinct_texts(self.expected_evidence_refs, "expected_evidence_refs"))

    @classmethod
    def create(
        cls,
        brief: Any,
        *,
        case_id: str,
        cohort_id: str,
        source_sha: str,
        brief_class: Literal["VN_PRODUCT", "VN_NON_PRODUCT_FICTION", "SILENT_NO_CTA"],
        factual_evidence_applicable: bool,
        provenance: CreativeJobProvenance,
        created_at: str,
        language: str = "vi",
        expected_evidence_refs: tuple[str, ...] = (),
        rubric_version: str = BENCHMARK_RUBRIC_VERSION,
    ) -> A03BenchmarkCase:
        valid = intake_brief(brief)
        refs = tuple(expected_evidence_refs)
        if not set(refs) <= set(valid.get("source_refs", [])):
            raise CreativeValidationError("expected_evidence_refs must belong to source brief")
        if brief_class == "VN_NON_PRODUCT_FICTION" and valid.get("product_ref") is not None:
            raise CreativeValidationError("VN_NON_PRODUCT_FICTION brief cannot require a product_ref")
        if brief_class == "SILENT_NO_CTA" and valid.get("cta") is not None:
            raise CreativeValidationError("SILENT_NO_CTA brief cannot carry a non-null cta")
        return cls(
            case_id=case_id,
            cohort_id=cohort_id,
            source_sha=source_sha,
            brief_id=valid["brief_id"],
            source_brief_digest=brief_digest(valid),
            brief_class=brief_class,
            factual_evidence_applicable=factual_evidence_applicable,
            provenance=provenance,
            created_at=created_at,
            language=language,
            expected_evidence_refs=refs,
            rubric_version=rubric_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "cohort_id": self.cohort_id,
            "rubric_version": self.rubric_version,
            "source_sha": self.source_sha,
            "brief_id": self.brief_id,
            "source_brief_digest": self.source_brief_digest,
            "brief_class": self.brief_class,
            "factual_evidence_applicable": self.factual_evidence_applicable,
            "language": self.language,
            "expected_evidence_refs": list(self.expected_evidence_refs),
            "provenance": self.provenance.to_dict(),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return canonical_bytes(self.to_dict()).decode("utf-8")

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @property
    def slot(self) -> str:
        return _benchmark_case_slot(self.cohort_id, self.case_id)

    @classmethod
    def from_dict(cls, data: object) -> A03BenchmarkCase:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
        if not isinstance(value["expected_evidence_refs"], list):
            raise CreativeValidationError("expected expected_evidence_refs array")
        return cls(**value)

    @classmethod
    def from_json(cls, text: str) -> A03BenchmarkCase:
        return cls.from_dict(read_json(text))


@dataclass(frozen=True, slots=True)
class BenchmarkRoleTrace:
    """One actually observed workflow role step or verified external research input."""

    role: Literal["RESEARCH", "GENERATOR", "CRITIC", "SELECTOR", "DEVELOPER", "PACKAGE_PRODUCER"]
    step_name: str
    operation: Literal["RESEARCH", "EXPLORE", "CRITIQUE", "SELECT", "DEVELOP", "PRODUCE_PACKAGE", "REVISE_PACKAGE"]
    execution_status: Literal["OK", "FAILED", "HOLD", "REFUSED", "UNKNOWN", "VERIFIED_SOURCE_BYTES"]
    job_slot: str | None = None
    job_digest: str | None = None
    stage_digest: str | None = None
    request_digest: str | None = None
    receipt_digest: str | None = None
    provider_request_id: str | None = None
    requested: ModelConfiguration | None = None
    observed: ProviderObservation = ProviderObservation()
    observed_effort: str = "UNKNOWN"
    usage: UsageObservation = UsageObservation()
    wall_time_ms: int | None = None
    retry_count: int | None = None
    is_retry: bool = False
    evidence_refs: tuple[str, ...] = ()
    evidence_digests: tuple[str, ...] = ()
    started_at: str | None = None
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if self.role not in BENCHMARK_ROLES:
            raise CreativeValidationError("invalid benchmark role")
        require_text(self.step_name, "step_name")
        if self.operation not in _ROLE_TO_OPS[self.role]:
            raise CreativeValidationError("benchmark role disagrees with operation")
        if self.execution_status not in ("OK", "FAILED", "HOLD", "REFUSED", "UNKNOWN", "VERIFIED_SOURCE_BYTES"):
            raise CreativeValidationError("invalid benchmark role execution_status")
        _require_bool(self.is_retry, "is_retry")
        if not isinstance(self.observed, ProviderObservation) or not isinstance(self.usage, UsageObservation):
            raise CreativeValidationError("expected typed observed provider and usage")
        ProviderObservation.from_dict(self.observed.to_dict())
        UsageObservation.from_dict(self.usage.to_dict())
        if self.requested is not None:
            if not isinstance(self.requested, ModelConfiguration):
                raise CreativeValidationError("expected typed requested ModelConfiguration")
            ModelConfiguration.from_dict(self.requested.to_dict())
        require_text(self.observed_effort, "observed_effort")
        if self.observed.mode == "TEST_ONLY_MOCK" and self.observed.model != "UNKNOWN":
            raise CreativeValidationError("TEST_ONLY_MOCK trace cannot claim a named model")
        _nonneg_int_or_none(self.wall_time_ms, "wall_time_ms")
        _nonneg_int_or_none(self.retry_count, "retry_count")
        if self.provider_request_id is not None:
            require_text(self.provider_request_id, "provider_request_id")
        object.__setattr__(self, "evidence_refs", _distinct_texts(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "evidence_digests", _distinct_digests(self.evidence_digests, "evidence_digests"))
        if self.started_at is not None and self.completed_at is not None:
            if _require_iso_timestamp(self.completed_at, "completed_at") < _require_iso_timestamp(self.started_at, "started_at"):
                raise CreativeValidationError("role trace completed_at precedes started_at")
        elif self.started_at is not None:
            _require_iso_timestamp(self.started_at, "started_at")
        elif self.completed_at is not None:
            _require_iso_timestamp(self.completed_at, "completed_at")
        for name in ("job_slot", "job_digest", "stage_digest", "request_digest", "receipt_digest"):
            val = getattr(self, name)
            if val is not None:
                require_digest(val)
        if self.role == "RESEARCH":
            if self.execution_status != "VERIFIED_SOURCE_BYTES":
                raise CreativeValidationError("RESEARCH trace requires VERIFIED_SOURCE_BYTES")
            if not self.evidence_refs or len(self.evidence_refs) != len(self.evidence_digests):
                raise CreativeValidationError("RESEARCH trace requires matched verified evidence refs and digests")
            if self.is_retry:
                raise CreativeValidationError("caller-supplied RESEARCH evidence cannot be marked as a retry")
        else:
            if self.execution_status == "VERIFIED_SOURCE_BYTES":
                raise CreativeValidationError("only RESEARCH trace may use VERIFIED_SOURCE_BYTES")
            for name in ("job_slot", "job_digest", "stage_digest", "request_digest", "receipt_digest"):
                if getattr(self, name) is None:
                    raise CreativeValidationError(f"model role trace requires exact {name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "step_name": self.step_name,
            "operation": self.operation,
            "execution_status": self.execution_status,
            "job_slot": self.job_slot,
            "job_digest": self.job_digest,
            "stage_digest": self.stage_digest,
            "request_digest": self.request_digest,
            "receipt_digest": self.receipt_digest,
            "provider_request_id": self.provider_request_id,
            "requested": self.requested.to_dict() if self.requested else None,
            "observed": self.observed.to_dict(),
            "observed_effort": self.observed_effort,
            "usage": self.usage.to_dict(),
            "wall_time_ms": self.wall_time_ms,
            "retry_count": self.retry_count,
            "is_retry": self.is_retry,
            "evidence_refs": list(self.evidence_refs),
            "evidence_digests": list(self.evidence_digests),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkRoleTrace:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if value["requested"] is not None:
            value["requested"] = ModelConfiguration.from_dict(value["requested"])
        value["observed"] = ProviderObservation.from_dict(value["observed"])
        value["usage"] = UsageObservation.from_dict(value["usage"])
        for name in ("evidence_refs", "evidence_digests"):
            if not isinstance(value[name], list):
                raise CreativeValidationError(f"expected {name} array")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class BenchmarkRoleTokenSummary:
    """Observed per-role token and retry accounting; UNKNOWN never becomes 0."""

    role: Literal["RESEARCH", "GENERATOR", "CRITIC", "SELECTOR", "DEVELOPER", "PACKAGE_PRODUCER"]
    invocation_count: int
    input_tokens_status: Literal["OBSERVED", "UNKNOWN"]
    input_tokens: int | None
    output_tokens_status: Literal["OBSERVED", "UNKNOWN"]
    output_tokens: int | None
    retries_status: Literal["OBSERVED", "UNKNOWN"]
    retries_observed: int | None

    def __post_init__(self) -> None:
        if self.role not in BENCHMARK_ROLES:
            raise CreativeValidationError("invalid role token summary role")
        positive_int(self.invocation_count, "invocation_count")
        for status_name, val_name in (
            ("input_tokens_status", "input_tokens"),
            ("output_tokens_status", "output_tokens"),
            ("retries_status", "retries_observed"),
        ):
            status = getattr(self, status_name)
            val = getattr(self, val_name)
            if status not in ("OBSERVED", "UNKNOWN"):
                raise CreativeValidationError(f"invalid {status_name}")
            _nonneg_int_or_none(val, val_name)
            if (status == "UNKNOWN") != (val is None):
                raise CreativeValidationError(f"{status_name} disagrees with {val_name}")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkRoleTokenSummary:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class BenchmarkTelemetry:
    """Cumulative observed telemetry across linked invocations; UNKNOWN stays UNKNOWN."""

    input_tokens_status: Literal["OBSERVED", "UNKNOWN"] = "UNKNOWN"
    total_input_tokens: int | None = None
    output_tokens_status: Literal["OBSERVED", "UNKNOWN"] = "UNKNOWN"
    total_output_tokens: int | None = None
    cost_status: Literal["OBSERVED", "UNKNOWN"] = "UNKNOWN"
    total_cost_amount: float | None = None
    currency: str | None = None
    invocation_wall_time_ms: int | None = None
    queue_wait_ms: int | None = None
    retry_wall_time_ms: int | None = None
    wall_time_status: Literal["OBSERVED", "UNKNOWN"] = "UNKNOWN"
    total_wall_time_ms: int | None = None
    human_active_time_status: Literal["OBSERVED", "UNKNOWN"] = "UNKNOWN"
    human_active_time_ms: int | None = None
    retries_status: Literal["OBSERVED", "UNKNOWN"] = "UNKNOWN"
    total_retries: int | None = None
    role_breakdown: tuple[BenchmarkRoleTokenSummary, ...] = ()

    def __post_init__(self) -> None:
        for status_name, val_name in (
            ("input_tokens_status", "total_input_tokens"),
            ("output_tokens_status", "total_output_tokens"),
            ("wall_time_status", "total_wall_time_ms"),
            ("human_active_time_status", "human_active_time_ms"),
            ("retries_status", "total_retries"),
        ):
            status = getattr(self, status_name)
            val = getattr(self, val_name)
            if status not in ("OBSERVED", "UNKNOWN"):
                raise CreativeValidationError(f"invalid {status_name}")
            _nonneg_int_or_none(val, val_name)
            if (status == "UNKNOWN") != (val is None):
                raise CreativeValidationError(f"{status_name} disagrees with {val_name}")
        for name in ("invocation_wall_time_ms", "queue_wait_ms", "retry_wall_time_ms"):
            _nonneg_int_or_none(getattr(self, name), name)
        if self.wall_time_status == "OBSERVED":
            if (self.invocation_wall_time_ms is None or self.queue_wait_ms is None
                    or self.retry_wall_time_ms is None):
                raise CreativeValidationError("OBSERVED total_wall_time_ms requires observed invocation/queue/retry times")
            if self.total_wall_time_ms != self.invocation_wall_time_ms + self.queue_wait_ms + self.retry_wall_time_ms:
                raise CreativeValidationError("total_wall_time_ms disagrees with invocation + queue + retry times")
        if self.cost_status not in ("OBSERVED", "UNKNOWN"):
            raise CreativeValidationError("invalid cost_status")
        if self.cost_status == "UNKNOWN":
            if self.total_cost_amount is not None or self.currency is not None:
                raise CreativeValidationError("UNKNOWN cost cannot carry amount or currency")
        else:
            if (type(self.total_cost_amount) not in (int, float)
                    or not math.isfinite(self.total_cost_amount) or self.total_cost_amount < 0):
                raise CreativeValidationError("invalid total_cost_amount")
            object.__setattr__(self, "total_cost_amount", float(self.total_cost_amount))
            require_text(self.currency, "currency")
        if not isinstance(self.role_breakdown, (tuple, list)) or any(
            not isinstance(item, BenchmarkRoleTokenSummary) for item in self.role_breakdown
        ):
            raise CreativeValidationError("expected typed role_breakdown")
        items = tuple(BenchmarkRoleTokenSummary.from_dict(item.to_dict()) for item in self.role_breakdown)
        if len({item.role for item in items}) != len(items):
            raise CreativeValidationError("duplicate role in role_breakdown")
        object.__setattr__(self, "role_breakdown", items)

    def to_dict(self) -> dict[str, Any]:
        return {
            **{f.name: getattr(self, f.name) for f in fields(self)},
            "role_breakdown": [item.to_dict() for item in self.role_breakdown],
        }

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkTelemetry:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if not isinstance(value["role_breakdown"], list):
            raise CreativeValidationError("expected role_breakdown array")
        value["role_breakdown"] = tuple(BenchmarkRoleTokenSummary.from_dict(item) for item in value["role_breakdown"])
        return cls(**value)


def aggregate_benchmark_telemetry(
    role_traces: tuple[BenchmarkRoleTrace, ...],
    *,
    queue_wait_ms: int | None = None,
    retry_wall_time_ms: int | None = None,
    human_active_time_ms: int | None = None,
) -> BenchmarkTelemetry:
    """Compute exact cumulative telemetry from linked traces; UNKNOWN never becomes 0."""
    _nonneg_int_or_none(queue_wait_ms, "queue_wait_ms")
    _nonneg_int_or_none(retry_wall_time_ms, "retry_wall_time_ms")
    _nonneg_int_or_none(human_active_time_ms, "human_active_time_ms")

    model_traces = tuple(t for t in role_traces if t.role != "RESEARCH" or t.receipt_digest is not None)
    by_role: dict[str, list[BenchmarkRoleTrace]] = {}
    for trace in role_traces:
        by_role.setdefault(trace.role, []).append(trace)

    breakdown: list[BenchmarkRoleTokenSummary] = []
    for role in BENCHMARK_ROLES:
        traces = by_role.get(role)
        if not traces:
            continue
        inv_traces = [t for t in traces if t.role != "RESEARCH" or t.receipt_digest is not None]
        if inv_traces and all(t.usage.status == "OBSERVED" and t.usage.input_tokens is not None for t in inv_traces):
            in_status, in_total = "OBSERVED", sum(t.usage.input_tokens for t in inv_traces)
        else:
            in_status, in_total = "UNKNOWN", None
        if inv_traces and all(t.usage.status == "OBSERVED" and t.usage.output_tokens is not None for t in inv_traces):
            out_status, out_total = "OBSERVED", sum(t.usage.output_tokens for t in inv_traces)
        else:
            out_status, out_total = "UNKNOWN", None
        explicit_retries = [t for t in traces if t.is_retry]
        if all(t.retry_count is not None for t in traces):
            r_status = "OBSERVED"
            r_total = sum(t.retry_count for t in traces) + len(explicit_retries)
        elif explicit_retries:
            r_status, r_total = "OBSERVED", len(explicit_retries)
        else:
            r_status, r_total = "UNKNOWN", None
        breakdown.append(BenchmarkRoleTokenSummary(
            role=role,
            invocation_count=len(traces),
            input_tokens_status=in_status,
            input_tokens=in_total,
            output_tokens_status=out_status,
            output_tokens=out_total,
            retries_status=r_status,
            retries_observed=r_total,
        ))

    if model_traces and all(t.usage.status == "OBSERVED" and t.usage.input_tokens is not None for t in model_traces):
        total_in_status, total_in = "OBSERVED", sum(t.usage.input_tokens for t in model_traces)
    else:
        total_in_status, total_in = "UNKNOWN", None

    if model_traces and all(t.usage.status == "OBSERVED" and t.usage.output_tokens is not None for t in model_traces):
        total_out_status, total_out = "OBSERVED", sum(t.usage.output_tokens for t in model_traces)
    else:
        total_out_status, total_out = "UNKNOWN", None

    if (model_traces
            and all(t.usage.status == "OBSERVED" and t.usage.cost_amount is not None for t in model_traces)
            and len({t.usage.currency for t in model_traces}) == 1):
        cost_status = "OBSERVED"
        total_cost = round(sum(float(t.usage.cost_amount) for t in model_traces), 8)
        currency = model_traces[0].usage.currency
    else:
        cost_status, total_cost, currency = "UNKNOWN", None, None

    if role_traces and all(t.wall_time_ms is not None for t in role_traces):
        inv_wall = sum(t.wall_time_ms for t in role_traces)
    else:
        inv_wall = None

    if inv_wall is not None and queue_wait_ms is not None and retry_wall_time_ms is not None:
        wall_status, total_wall = "OBSERVED", inv_wall + queue_wait_ms + retry_wall_time_ms
    else:
        wall_status, total_wall = "UNKNOWN", None

    if human_active_time_ms is not None:
        human_status = "OBSERVED"
    else:
        human_status = "UNKNOWN"

    if role_traces and all(item.retries_status == "OBSERVED" for item in breakdown):
        retries_status = "OBSERVED"
        total_retries = sum(item.retries_observed for item in breakdown)
    else:
        retries_status, total_retries = "UNKNOWN", None

    return BenchmarkTelemetry(
        input_tokens_status=total_in_status,
        total_input_tokens=total_in,
        output_tokens_status=total_out_status,
        total_output_tokens=total_out,
        cost_status=cost_status,
        total_cost_amount=total_cost,
        currency=currency,
        invocation_wall_time_ms=inv_wall,
        queue_wait_ms=queue_wait_ms,
        retry_wall_time_ms=retry_wall_time_ms,
        wall_time_status=wall_status,
        total_wall_time_ms=total_wall,
        human_active_time_status=human_status,
        human_active_time_ms=human_active_time_ms,
        retries_status=retries_status,
        total_retries=total_retries,
        role_breakdown=tuple(breakdown),
    )


@dataclass(frozen=True, slots=True)
class BenchmarkStructuralCheck:
    """Deterministic schema/contract check only; NEVER asserts semantic PASS."""

    status: Literal["STRUCTURAL_VALID", "STRUCTURAL_INVALID"]
    brief_binding_valid: bool
    non_duplication_valid: bool
    evidence_citation_valid: bool
    scene_s0_action_s1_valid: bool | None = None
    continuity_camera_audio_silence_valid: bool | None = None
    rights_qa_handoff_valid: bool | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in ("STRUCTURAL_VALID", "STRUCTURAL_INVALID"):
            raise CreativeValidationError("structural check status must be STRUCTURAL_VALID or STRUCTURAL_INVALID")
        for name in ("brief_binding_valid", "non_duplication_valid", "evidence_citation_valid"):
            _require_bool(getattr(self, name), name)
        for name in ("scene_s0_action_s1_valid", "continuity_camera_audio_silence_valid", "rights_qa_handoff_valid"):
            val = getattr(self, name)
            if val is not None:
                _require_bool(val, name)
        object.__setattr__(self, "notes", text_tuple(self.notes, "notes"))
        checks = [
            self.brief_binding_valid,
            self.non_duplication_valid,
            self.evidence_citation_valid,
        ]
        for opt in (self.scene_s0_action_s1_valid, self.continuity_camera_audio_silence_valid,
                    self.rights_qa_handoff_valid):
            if opt is not None:
                checks.append(opt)
        expected = "STRUCTURAL_VALID" if all(checks) else "STRUCTURAL_INVALID"
        if self.status != expected:
            raise CreativeValidationError("structural check status disagrees with deterministic check flags")

    def to_dict(self) -> dict[str, Any]:
        return {**{f.name: getattr(self, f.name) for f in fields(self)}, "notes": list(self.notes)}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkStructuralCheck:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if not isinstance(value["notes"], list):
            raise CreativeValidationError("expected structural check notes array")
        return cls(**value)


def evaluate_benchmark_structure(
    case: A03BenchmarkCase,
    brief: Mapping[str, Any],
    *,
    stages: tuple[A03CreativeStageResult, ...] = (),
    package: A03CreativeProductionPackage | None = None,
) -> BenchmarkStructuralCheck:
    """Run deterministic contract checks; reports STRUCTURAL_VALID or STRUCTURAL_INVALID only."""
    notes: list[str] = []
    valid_brief = intake_brief(brief)
    b_digest = brief_digest(valid_brief)
    brief_ok = (valid_brief["brief_id"] == case.brief_id and b_digest == case.source_brief_digest)
    for st in stages:
        if brief_digest(st.job.request.brief) != b_digest or st.job.brief_id != case.brief_id:
            brief_ok = False
            notes.append("stage brief mismatch")
    if package is not None and (package.brief_id != case.brief_id or package.source_brief_digest != b_digest):
        brief_ok = False
        notes.append("package brief mismatch")

    non_dup_ok = True
    for st in stages:
        if st.reasoning.state == "OK" and st.reasoning.request.operation == "EXPLORE":
            drafts = st.reasoning.drafts
            if len(drafts) > 1:
                families = {unicodedata.normalize("NFKC", d.family).strip().casefold() for d in drafts}
                mechanisms = {
                    (unicodedata.normalize("NFKC", d.premise).strip().casefold(),
                     unicodedata.normalize("NFKC", d.visual_direction).strip().casefold())
                    for d in drafts
                }
                if len(families) != len(drafts) or len(mechanisms) != len(drafts):
                    non_dup_ok = False
                    notes.append("duplicate draft family or mechanism")

    evidence_ok = True
    brief_refs = set(valid_brief.get("source_refs", []))
    for st in stages:
        supplied_refs = set(brief_refs)
        if st.job.request.cycle is not None:
            supplied_refs = {e.source_ref for e in st.job.request.cycle.evidence}
        elif st.job.request.production is not None:
            supplied_refs = {e.source_ref for e in st.job.request.production.evidence}
        elif st.job.request.benchmark is not None and st.job.request.benchmark.research_receipt_digest is not None:
            supplied_refs &= {e.source_ref for e in st.job.request.benchmark.evidence}
        if not set(st.reasoning.evidence_refs) <= supplied_refs:
            evidence_ok = False
            notes.append("unverified stage evidence_refs")
        for draft in st.reasoning.drafts:
            if not set(draft.source_refs) <= supplied_refs:
                evidence_ok = False
                notes.append("unverified draft source_refs")

    scene_ok: bool | None = None
    continuity_ok: bool | None = None
    rights_qa_ok: bool | None = None
    if package is not None:
        spec = package.spec
        scene_ok = bool(spec.scenes) and all(
            unicodedata.normalize("NFKC", s.state_s0).strip().casefold()
            != unicodedata.normalize("NFKC", s.state_s1).strip().casefold()
            for s in spec.scenes
        )
        silence_ok = True
        if case.brief_class == "SILENT_NO_CTA":
            if spec.audio.mode != "SILENCE" or any(s.speech_mode != "SILENCE" for s in spec.scenes):
                silence_ok = False
                notes.append("SILENT_NO_CTA package must preserve SILENCE audio/speech mode")
        continuity_ok = silence_ok and bool(spec.media_targets) and bool(spec.script)
        rights_qa_ok = (
            package.factual_status == "UNVERIFIED"
            and package.rights_status in ("UNKNOWN", "EVIDENCE_BOUND")
            and all(t.generation_status == "NOT_GENERATED" for t in spec.media_targets)
            and all(q.verification_status == "UNVERIFIED" for q in spec.qa_expectations)
        )
        if not rights_qa_ok:
            notes.append("package handoff readiness failed rights/QA/media boundary check")

    all_ok = all(x for x in (brief_ok, non_dup_ok, evidence_ok, scene_ok, continuity_ok, rights_qa_ok) if x is not None)
    return BenchmarkStructuralCheck(
        status="STRUCTURAL_VALID" if all_ok else "STRUCTURAL_INVALID",
        brief_binding_valid=brief_ok,
        non_duplication_valid=non_dup_ok,
        evidence_citation_valid=evidence_ok,
        scene_s0_action_s1_valid=scene_ok,
        continuity_camera_audio_silence_valid=continuity_ok,
        rights_qa_handoff_valid=rights_qa_ok,
        notes=tuple(notes),
    )


@dataclass(frozen=True, slots=True)
class BenchmarkRubricScores:
    """Comparable semantic rubric scores in [0.0, 1.0]."""

    brief_adherence: float
    originality_non_duplication: float
    factual_evidence_accuracy: float | None
    script_scene_s0_action_s1_consistency: float
    continuity_camera_audio_silence_rights_qa_readiness: float
    rubric_version: str = BENCHMARK_RUBRIC_VERSION

    def __post_init__(self) -> None:
        if self.rubric_version != BENCHMARK_RUBRIC_VERSION:
            raise CreativeValidationError("unsupported benchmark rubric version")
        for name in (
            "brief_adherence",
            "originality_non_duplication",
            "script_scene_s0_action_s1_consistency",
            "continuity_camera_audio_silence_rights_qa_readiness",
        ):
            object.__setattr__(self, name, _unit_score(getattr(self, name), name))
        if self.factual_evidence_accuracy is not None:
            object.__setattr__(
                self, "factual_evidence_accuracy",
                _unit_score(self.factual_evidence_accuracy, "factual_evidence_accuracy"),
            )

    def weighted_quality(self) -> float:
        total_weight = 0.0
        weighted_sum = 0.0
        for dim, weight in NSM_V1_RUBRIC_WEIGHTS.items():
            val = getattr(self, dim)
            if val is not None:
                weighted_sum += val * weight
                total_weight += weight
        return weighted_sum / total_weight

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkRubricScores:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class BenchmarkJudgeReceipt:
    """Independently observed semantic judge receipt; self-reported quality is rejected."""

    judge_receipt_id: str
    judge_kind: Literal["HUMAN_REVIEWER", "INDEPENDENT_MODEL_JUDGE", "TEST_ONLY_MOCK"]
    judge_actor_ref: str
    case_digest: str
    source_brief_digest: str
    evaluated_artifact_digest: str
    scores: BenchmarkRubricScores
    verdict: Literal["PASS", "FAIL", "HOLD"]
    rationale: str
    provenance: CreativeJobProvenance
    observed_at: str
    evidence_refs: tuple[str, ...] = ()
    judge_provider: ProviderObservation | None = None
    judge_execution_ref: str | None = None
    rubric_version: str = BENCHMARK_RUBRIC_VERSION
    schema_version: str = "A03BenchmarkJudgeReceipt.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "A03BenchmarkJudgeReceipt.v1":
            raise CreativeValidationError("unsupported benchmark judge receipt schema")
        if self.rubric_version != BENCHMARK_RUBRIC_VERSION:
            raise CreativeValidationError("unsupported benchmark judge rubric version")
        for name in ("judge_receipt_id", "judge_actor_ref", "rationale"):
            require_text(getattr(self, name), name)
        if self.judge_actor_ref.strip().casefold().startswith("self"):
            raise CreativeValidationError("self-reported model quality is forbidden as judge evidence")
        if self.judge_kind not in ("HUMAN_REVIEWER", "INDEPENDENT_MODEL_JUDGE", "TEST_ONLY_MOCK"):
            raise CreativeValidationError("invalid judge_kind")
        if self.verdict not in ("PASS", "FAIL", "HOLD"):
            raise CreativeValidationError("invalid judge verdict")
        for name in ("case_digest", "source_brief_digest", "evaluated_artifact_digest"):
            require_digest(getattr(self, name))
        if not isinstance(self.scores, BenchmarkRubricScores):
            raise CreativeValidationError("expected typed BenchmarkRubricScores")
        BenchmarkRubricScores.from_dict(self.scores.to_dict())
        if self.scores.rubric_version != self.rubric_version:
            raise CreativeValidationError("judge scores rubric_version mismatch")
        if not isinstance(self.provenance, CreativeJobProvenance):
            raise CreativeValidationError("expected typed judge provenance")
        CreativeJobProvenance.from_dict(self.provenance.to_dict())
        _require_iso_timestamp(self.observed_at, "observed_at")
        object.__setattr__(self, "evidence_refs", _distinct_texts(self.evidence_refs, "evidence_refs"))
        if self.judge_provider is not None:
            if not isinstance(self.judge_provider, ProviderObservation):
                raise CreativeValidationError("expected typed judge_provider")
            ProviderObservation.from_dict(self.judge_provider.to_dict())
        if self.judge_execution_ref is not None:
            require_text(self.judge_execution_ref, "judge_execution_ref")
        if self.judge_kind == "INDEPENDENT_MODEL_JUDGE":
            if (self.judge_provider is None or self.judge_provider.mode != "MODEL"
                    or self.judge_provider.provider == "UNKNOWN" or self.judge_provider.model == "UNKNOWN"
                    or self.judge_execution_ref is None):
                raise CreativeValidationError("INDEPENDENT_MODEL_JUDGE requires verified MODEL provider and execution_ref")
            if self.provenance.evidence_type == "SYNTHETIC_FIXTURE":
                raise CreativeValidationError("synthetic fixture cannot masquerade as INDEPENDENT_MODEL_JUDGE")
        elif self.judge_kind == "HUMAN_REVIEWER":
            if self.provenance.evidence_type == "SYNTHETIC_FIXTURE":
                raise CreativeValidationError("synthetic fixture cannot masquerade as HUMAN_REVIEWER")
        elif self.judge_kind == "TEST_ONLY_MOCK":
            if self.judge_provider is not None and (
                self.judge_provider.mode != "TEST_ONLY_MOCK" or self.judge_provider.model != "UNKNOWN"
            ):
                raise CreativeValidationError("TEST_ONLY_MOCK judge cannot claim MODEL provider")

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "judge_receipt_id": self.judge_receipt_id,
            "judge_kind": self.judge_kind,
            "judge_actor_ref": self.judge_actor_ref,
            "case_digest": self.case_digest,
            "source_brief_digest": self.source_brief_digest,
            "evaluated_artifact_digest": self.evaluated_artifact_digest,
            "rubric_version": self.rubric_version,
            "scores": self.scores.to_dict(),
            "verdict": self.verdict,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "judge_provider": self.judge_provider.to_dict() if self.judge_provider else None,
            "judge_execution_ref": self.judge_execution_ref,
            "provenance": self.provenance.to_dict(),
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkJudgeReceipt:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["scores"] = BenchmarkRubricScores.from_dict(value["scores"])
        value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
        if value["judge_provider"] is not None:
            value["judge_provider"] = ProviderObservation.from_dict(value["judge_provider"])
        if not isinstance(value["evidence_refs"], list):
            raise CreativeValidationError("expected judge evidence_refs array")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class BenchmarkConsumerEvidence:
    """Separates A04 source-handoff acceptance from media quality and commercial utility."""

    source_handoff_status: Literal["NOT_AVAILABLE", "SOURCE_HANDOFF_VERIFIED"] = "NOT_AVAILABLE"
    a04_receipt: Any | None = None
    a04_receipt_digest: str | None = None
    a04_receipt_ref: str | None = None
    media_quality_status: Literal["NOT_ATTEMPTED", "UNMEASURED", "OBSERVED_PASS", "OBSERVED_FAIL"] = "NOT_ATTEMPTED"
    media_quality_ref: str | None = None
    commercial_utility_status: Literal[
        "NOT_ATTEMPTED", "UNMEASURED", "OBSERVED_USEFUL", "OBSERVED_NOT_USEFUL"
    ] = "NOT_ATTEMPTED"
    commercial_utility_ref: str | None = None

    def __post_init__(self) -> None:
        from ai_commerce.media.generation import A04CreativeHandoffReceipt

        if self.source_handoff_status not in ("NOT_AVAILABLE", "SOURCE_HANDOFF_VERIFIED"):
            raise CreativeValidationError("invalid source_handoff_status")
        if self.media_quality_status not in ("NOT_ATTEMPTED", "UNMEASURED", "OBSERVED_PASS", "OBSERVED_FAIL"):
            raise CreativeValidationError("invalid media_quality_status")
        if self.commercial_utility_status not in (
            "NOT_ATTEMPTED", "UNMEASURED", "OBSERVED_USEFUL", "OBSERVED_NOT_USEFUL"
        ):
            raise CreativeValidationError("invalid commercial_utility_status")
        if self.source_handoff_status == "SOURCE_HANDOFF_VERIFIED":
            if not isinstance(self.a04_receipt, A04CreativeHandoffReceipt):
                raise CreativeValidationError("SOURCE_HANDOFF_VERIFIED requires typed A04CreativeHandoffReceipt")
            try:
                checked = A04CreativeHandoffReceipt.from_dict(self.a04_receipt.to_dict())
            except ValueError as exc:
                raise CreativeValidationError("forged or invalid A04CreativeHandoffReceipt") from exc
            require_digest(self.a04_receipt_digest)
            if self.a04_receipt_digest != checked.digest:
                raise CreativeValidationError("a04_receipt_digest disagrees with A04CreativeHandoffReceipt")
            if self.a04_receipt_ref is not None:
                require_text(self.a04_receipt_ref, "a04_receipt_ref")
        else:
            if (self.a04_receipt is not None or self.a04_receipt_digest is not None
                    or self.a04_receipt_ref is not None):
                raise CreativeValidationError("NOT_AVAILABLE handoff cannot carry A04 receipt fields")
        handoff_ids = set()
        if self.a04_receipt is not None:
            handoff_ids.update({self.a04_receipt.receipt_id, self.a04_receipt.digest})
        if self.media_quality_status in ("OBSERVED_PASS", "OBSERVED_FAIL"):
            ref = require_text(self.media_quality_ref, "media_quality_ref")
            if ref in handoff_ids:
                raise CreativeValidationError("source-handoff acceptance cannot be reused as media quality evidence")
        elif self.media_quality_ref is not None:
            raise CreativeValidationError("unobserved media quality cannot carry media_quality_ref")
        if self.commercial_utility_status in ("OBSERVED_USEFUL", "OBSERVED_NOT_USEFUL"):
            ref = require_text(self.commercial_utility_ref, "commercial_utility_ref")
            if ref in handoff_ids:
                raise CreativeValidationError("source-handoff acceptance cannot be reused as commercial utility evidence")
        elif self.commercial_utility_ref is not None:
            raise CreativeValidationError("unobserved commercial utility cannot carry commercial_utility_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_handoff_status": self.source_handoff_status,
            "a04_receipt": self.a04_receipt.to_dict() if self.a04_receipt is not None else None,
            "a04_receipt_digest": self.a04_receipt_digest,
            "a04_receipt_ref": self.a04_receipt_ref,
            "media_quality_status": self.media_quality_status,
            "media_quality_ref": self.media_quality_ref,
            "commercial_utility_status": self.commercial_utility_status,
            "commercial_utility_ref": self.commercial_utility_ref,
        }

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkConsumerEvidence:
        from ai_commerce.media.generation import A04CreativeHandoffReceipt

        value = exact_fields(data, {f.name for f in fields(cls)})
        if value["a04_receipt"] is not None:
            try:
                value["a04_receipt"] = A04CreativeHandoffReceipt.from_dict(value["a04_receipt"])
            except ValueError as exc:
                raise CreativeValidationError("forged or invalid A04CreativeHandoffReceipt") from exc
        return cls(**value)


def calculate_nsm_v1_score(
    scores: BenchmarkRubricScores,
    *,
    total_tokens: int,
    wall_time_ms: int,
    human_active_time_ms: int,
) -> dict[str, Any]:
    """Pure, deterministic, versioned NSM_v1 normalization and weighted score formula."""
    if not isinstance(scores, BenchmarkRubricScores):
        raise CreativeValidationError("expected BenchmarkRubricScores")
    scores = BenchmarkRubricScores.from_dict(scores.to_dict())
    _nonneg_int(total_tokens, "total_tokens")
    _nonneg_int(wall_time_ms, "wall_time_ms")
    _nonneg_int(human_active_time_ms, "human_active_time_ms")
    q = round(scores.weighted_quality(), 6)
    t = round(1.0 / (1.0 + (total_tokens / NSM_V1_REFERENCES["total_tokens"])), 6)
    w = round(1.0 / (1.0 + (wall_time_ms / NSM_V1_REFERENCES["wall_time_ms"])), 6)
    h = round(1.0 / (1.0 + (human_active_time_ms / NSM_V1_REFERENCES["human_active_time_ms"])), 6)
    total = round(
        NSM_V1_WEIGHTS["quality"] * q
        + NSM_V1_WEIGHTS["token_efficiency"] * t
        + NSM_V1_WEIGHTS["wall_time_efficiency"] * w
        + NSM_V1_WEIGHTS["human_time_efficiency"] * h,
        6,
    )
    return {
        "version": NSM_V1_VERSION,
        "quality_component": q,
        "token_efficiency_component": t,
        "wall_time_efficiency_component": w,
        "human_time_efficiency_component": h,
        "score": total,
    }


@dataclass(frozen=True, slots=True)
class BenchmarkNSMScore:
    """NSM_v1 score emitted ONLY with complete trusted data and verified useful consumer outcome."""

    version: str = NSM_V1_VERSION
    status: Literal["SCORED", "INCOMPLETE", "NOT_REAL_BENCHMARK"] = "INCOMPLETE"
    score: float | None = None
    quality_component: float | None = None
    token_efficiency_component: float | None = None
    wall_time_efficiency_component: float | None = None
    human_time_efficiency_component: float | None = None
    missing_coverage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.version != NSM_V1_VERSION:
            raise CreativeValidationError("unsupported NSM version")
        if self.status not in ("SCORED", "INCOMPLETE", "NOT_REAL_BENCHMARK"):
            raise CreativeValidationError("invalid NSM status")
        object.__setattr__(self, "missing_coverage", _distinct_texts(self.missing_coverage, "missing_coverage"))
        comp_names = (
            "score",
            "quality_component",
            "token_efficiency_component",
            "wall_time_efficiency_component",
            "human_time_efficiency_component",
        )
        if self.status == "SCORED":
            if self.missing_coverage:
                raise CreativeValidationError("SCORED NSM cannot have missing_coverage")
            for name in comp_names:
                object.__setattr__(self, name, _unit_score(getattr(self, name), name))
            expected = round(
                NSM_V1_WEIGHTS["quality"] * self.quality_component
                + NSM_V1_WEIGHTS["token_efficiency"] * self.token_efficiency_component
                + NSM_V1_WEIGHTS["wall_time_efficiency"] * self.wall_time_efficiency_component
                + NSM_V1_WEIGHTS["human_time_efficiency"] * self.human_time_efficiency_component,
                6,
            )
            if abs(self.score - expected) > 1e-6:
                raise CreativeValidationError("NSM_v1 score disagrees with versioned weights (possible inflation)")
        else:
            if not self.missing_coverage:
                raise CreativeValidationError("non-SCORED NSM must list missing_coverage")
            if any(getattr(self, name) is not None for name in comp_names):
                raise CreativeValidationError("non-SCORED NSM cannot emit numeric scores")

    def to_dict(self) -> dict[str, Any]:
        return {**{f.name: getattr(self, f.name) for f in fields(self)},
                "missing_coverage": list(self.missing_coverage)}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkNSMScore:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if not isinstance(value["missing_coverage"], list):
            raise CreativeValidationError("expected missing_coverage array")
        return cls(**value)


def _derive_semantic_status(
    *,
    judge_receipt: BenchmarkJudgeReceipt | None,
    structural_check: BenchmarkStructuralCheck,
    execution_mode: str,
    provenance: CreativeJobProvenance,
) -> Literal["UNMEASURED", "TEST_ONLY_MOCK", "MEASURED_PASS", "MEASURED_FAIL", "HOLD"]:
    if judge_receipt is None:
        return "UNMEASURED"
    if judge_receipt.judge_kind == "TEST_ONLY_MOCK":
        return "TEST_ONLY_MOCK"
    if judge_receipt.verdict == "HOLD":
        return "HOLD"
    if judge_receipt.verdict == "FAIL":
        return "MEASURED_FAIL"
    # PASS requires non-mock execution, non-synthetic provenance, and STRUCTURAL_VALID.
    if execution_mode != "MODEL" or provenance.evidence_type != "HUMAN_SUPPLIED":
        return "TEST_ONLY_MOCK" if execution_mode == "TEST_ONLY_MOCK" else "HOLD"
    if structural_check.status != "STRUCTURAL_VALID":
        return "HOLD"
    return "MEASURED_PASS"


def _evaluate_observation_nsm(
    *,
    status: str,
    execution_mode: str,
    provenance: CreativeJobProvenance,
    structural_check: BenchmarkStructuralCheck,
    semantic_status: str,
    judge_receipt: BenchmarkJudgeReceipt | None,
    telemetry: BenchmarkTelemetry,
    consumer_evidence: BenchmarkConsumerEvidence,
    hold_reasons: tuple[str, ...],
    errors: tuple[str, ...],
) -> BenchmarkNSMScore:
    missing: list[str] = []
    if status in ("HOLD", "FAILED", "INCOMPLETE") or hold_reasons or errors:
        missing.append("WORKFLOW_NOT_COMPLETE")
    if execution_mode != "MODEL" or status != "REAL_MODEL_OBSERVED":
        missing.append("REAL_MODEL_EXECUTION")
    if provenance.evidence_type != "HUMAN_SUPPLIED":
        missing.append("NON_SYNTHETIC_PROVENANCE")
    if structural_check.status != "STRUCTURAL_VALID":
        missing.append("STRUCTURAL_VALID")
    if (judge_receipt is None or judge_receipt.judge_kind == "TEST_ONLY_MOCK"
            or semantic_status not in ("MEASURED_PASS", "MEASURED_FAIL")):
        missing.append("INDEPENDENT_NON_MOCK_JUDGE")
    if telemetry.input_tokens_status != "OBSERVED":
        missing.append("INPUT_TOKENS")
    if telemetry.output_tokens_status != "OBSERVED":
        missing.append("OUTPUT_TOKENS")
    if telemetry.wall_time_status != "OBSERVED":
        missing.append("WALL_TIME")
    if telemetry.human_active_time_status != "OBSERVED":
        missing.append("HUMAN_ACTIVE_TIME")
    if consumer_evidence.source_handoff_status != "SOURCE_HANDOFF_VERIFIED":
        missing.append("A04_SOURCE_HANDOFF")
    if consumer_evidence.commercial_utility_status != "OBSERVED_USEFUL":
        missing.append("VERIFIED_USEFUL_CONSUMER_OUTCOME")

    if ("REAL_MODEL_EXECUTION" in missing or "NON_SYNTHETIC_PROVENANCE" in missing
            or (judge_receipt is not None and judge_receipt.judge_kind == "TEST_ONLY_MOCK")):
        return BenchmarkNSMScore(
            version=NSM_V1_VERSION,
            status="NOT_REAL_BENCHMARK",
            missing_coverage=tuple(missing),
        )
    if missing:
        return BenchmarkNSMScore(
            version=NSM_V1_VERSION,
            status="INCOMPLETE",
            missing_coverage=tuple(missing),
        )
    computed = calculate_nsm_v1_score(
        judge_receipt.scores,
        total_tokens=telemetry.total_input_tokens + telemetry.total_output_tokens,
        wall_time_ms=telemetry.total_wall_time_ms,
        human_active_time_ms=telemetry.human_active_time_ms,
    )
    return BenchmarkNSMScore(
        version=NSM_V1_VERSION,
        status="SCORED",
        score=computed["score"],
        quality_component=computed["quality_component"],
        token_efficiency_component=computed["token_efficiency_component"],
        wall_time_efficiency_component=computed["wall_time_efficiency_component"],
        human_time_efficiency_component=computed["human_time_efficiency_component"],
        missing_coverage=(),
    )


def _strategy_hold_reasons(
    *,
    strategy: str,
    role_traces: tuple[BenchmarkRoleTrace, ...],
    cycle_result_digest: str | None,
    research_status: str,
    evidence_refs: tuple[str, ...],
    include_retried_success: bool = False,
) -> tuple[str, ...]:
    reasons: list[str] = []
    roles = {t.role for t in role_traces if not t.is_retry or include_retried_success}
    ok_roles = {t.role for t in role_traces if (not t.is_retry or include_retried_success)
                and t.execution_status in ("OK", "VERIFIED_SOURCE_BYTES")}
    if "GENERATOR" not in ok_roles:
        reasons.append("MISSING_GENERATOR")
    if strategy == "S1":
        if cycle_result_digest is not None or roles & {"CRITIC", "SELECTOR", "DEVELOPER"}:
            reasons.append("STRATEGY_WORKFLOW_MISMATCH")
    elif strategy == "S2":
        if "CRITIC" not in ok_roles:
            reasons.append("MISSING_INDEPENDENT_CRITIC")
        if cycle_result_digest is not None or "DEVELOPER" in roles:
            reasons.append("STRATEGY_WORKFLOW_MISMATCH")
    elif strategy == "S3":
        if research_status != "VERIFIED_SOURCE_BYTES" or not evidence_refs or "RESEARCH" not in ok_roles:
            reasons.append("MISSING_RESEARCH_FOR_S3")
        if "CRITIC" not in ok_roles:
            reasons.append("MISSING_INDEPENDENT_CRITIC")
        if "DEVELOPER" not in ok_roles:
            reasons.append("MISSING_BOUNDED_DEVELOPER")
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class A03BenchmarkObservation:
    """Strict, immutable sidecar observation of one actually executed A03 workflow."""

    sample_id: str
    attempt: int
    cohort_id: str
    case_id: str
    case_digest: str
    source_sha: str
    brief_id: str
    source_brief_digest: str
    brief_class: Literal["VN_PRODUCT", "VN_NON_PRODUCT_FICTION", "SILENT_NO_CTA"]
    strategy: Literal["S1", "S2", "S3"]
    requested_configuration: ModelConfiguration | None
    observed_provider: ProviderObservation
    observed_effort: str
    job_digests: tuple[str, ...]
    stage_result_digests: tuple[str, ...]
    consumer_evidence: BenchmarkConsumerEvidence
    provenance: CreativeJobProvenance
    research_status: Literal["VERIFIED_SOURCE_BYTES", "UNKNOWN"]
    role_traces: tuple[BenchmarkRoleTrace, ...]
    structural_check: BenchmarkStructuralCheck
    semantic_status: Literal["UNMEASURED", "TEST_ONLY_MOCK", "MEASURED_PASS", "MEASURED_FAIL", "HOLD"]
    telemetry: BenchmarkTelemetry
    nsm: BenchmarkNSMScore
    started_at: str
    completed_at: str
    execution_mode: Literal["TEST_ONLY_MOCK", "MODEL", "UNKNOWN"]
    status: Literal["TEST_ONLY_MOCK", "REAL_MODEL_OBSERVED", "HOLD", "FAILED"]
    benchmark_validity: Literal["NOT_REAL_BENCHMARK", "ELIGIBLE_REAL_BENCHMARK", "HOLD"]
    rubric_version: str = BENCHMARK_RUBRIC_VERSION
    cycle_job_digest: str | None = None
    cycle_result_digest: str | None = None
    production_job_digest: str | None = None
    production_result_digest: str | None = None
    package_id: str | None = None
    package_revision: int | None = None
    package_digest: str | None = None
    explore_receipt_digest: str | None = None
    critic_receipt_digests: tuple[str, ...] = ()
    selector_receipt_digest: str | None = None
    develop_receipt_digest: str | None = None
    package_receipt_digest: str | None = None
    judge_receipt: BenchmarkJudgeReceipt | None = None
    evidence_refs: tuple[str, ...] = ()
    evidence_digests: tuple[str, ...] = ()
    hold_reasons: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    side_effects: Literal["NONE"] = "NONE"
    schema_version: str = "A03BenchmarkObservation.v1"
    experiment_plan_digest: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version == "A03BenchmarkObservation.v2":
            require_digest(self.experiment_plan_digest)
            if (self.status != "TEST_ONLY_MOCK" or self.judge_receipt is not None
                    or self.consumer_evidence != BenchmarkConsumerEvidence()):
                raise CreativeValidationError("source trial cannot claim a judge or consumer outcome")
        elif self.schema_version != "A03BenchmarkObservation.v1" or self.experiment_plan_digest is not None:
            raise CreativeValidationError("unsupported benchmark observation schema")
        if self.rubric_version != BENCHMARK_RUBRIC_VERSION:
            raise CreativeValidationError("unsupported benchmark rubric version")
        if self.side_effects != "NONE":
            raise CreativeValidationError("R5a benchmark observation forbids external/auth/spend side effects")
        for name in ("sample_id", "cohort_id", "case_id", "brief_id", "observed_effort"):
            require_text(getattr(self, name), name)
        positive_int(self.attempt, "attempt")
        _require_source_sha(self.source_sha, "source_sha")
        for name in ("case_digest", "source_brief_digest"):
            require_digest(getattr(self, name))
        if self.brief_class not in BENCHMARK_BRIEF_CLASSES:
            raise CreativeValidationError("invalid brief_class")
        if self.strategy not in BENCHMARK_STRATEGIES:
            raise CreativeValidationError("invalid benchmark strategy")
        if self.research_status not in ("VERIFIED_SOURCE_BYTES", "UNKNOWN"):
            raise CreativeValidationError("invalid research_status")
        if self.execution_mode not in ("TEST_ONLY_MOCK", "MODEL", "UNKNOWN"):
            raise CreativeValidationError("invalid execution_mode")
        if self.status not in ("TEST_ONLY_MOCK", "REAL_MODEL_OBSERVED", "HOLD", "FAILED"):
            raise CreativeValidationError("invalid observation status")
        if self.benchmark_validity not in ("NOT_REAL_BENCHMARK", "ELIGIBLE_REAL_BENCHMARK", "HOLD"):
            raise CreativeValidationError("invalid benchmark_validity")
        if _require_iso_timestamp(self.completed_at, "completed_at") < _require_iso_timestamp(self.started_at, "started_at"):
            raise CreativeValidationError("observation completed_at precedes started_at")
        if self.requested_configuration is not None:
            if not isinstance(self.requested_configuration, ModelConfiguration):
                raise CreativeValidationError("expected typed requested_configuration")
            ModelConfiguration.from_dict(self.requested_configuration.to_dict())
        if not isinstance(self.observed_provider, ProviderObservation):
            raise CreativeValidationError("expected typed observed_provider")
        ProviderObservation.from_dict(self.observed_provider.to_dict())
        if not isinstance(self.provenance, CreativeJobProvenance):
            raise CreativeValidationError("expected typed observation provenance")
        CreativeJobProvenance.from_dict(self.provenance.to_dict())
        if not isinstance(self.consumer_evidence, BenchmarkConsumerEvidence):
            raise CreativeValidationError("expected typed BenchmarkConsumerEvidence")
        BenchmarkConsumerEvidence.from_dict(self.consumer_evidence.to_dict())
        if not isinstance(self.structural_check, BenchmarkStructuralCheck):
            raise CreativeValidationError("expected typed BenchmarkStructuralCheck")
        BenchmarkStructuralCheck.from_dict(self.structural_check.to_dict())
        if not isinstance(self.telemetry, BenchmarkTelemetry):
            raise CreativeValidationError("expected typed BenchmarkTelemetry")
        BenchmarkTelemetry.from_dict(self.telemetry.to_dict())
        if not isinstance(self.nsm, BenchmarkNSMScore):
            raise CreativeValidationError("expected typed BenchmarkNSMScore")
        BenchmarkNSMScore.from_dict(self.nsm.to_dict())

        object.__setattr__(self, "job_digests", _distinct_digests(self.job_digests, "job_digests"))
        object.__setattr__(self, "stage_result_digests", _distinct_digests(self.stage_result_digests, "stage_result_digests"))
        object.__setattr__(self, "critic_receipt_digests", _distinct_digests(self.critic_receipt_digests, "critic_receipt_digests"))
        object.__setattr__(self, "evidence_refs", _distinct_texts(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "evidence_digests", _distinct_digests(self.evidence_digests, "evidence_digests"))
        object.__setattr__(self, "hold_reasons", _distinct_texts(self.hold_reasons, "hold_reasons"))
        object.__setattr__(self, "errors", _distinct_texts(self.errors, "errors"))

        if self.research_status == "VERIFIED_SOURCE_BYTES":
            if not self.evidence_refs or len(self.evidence_refs) != len(self.evidence_digests):
                raise CreativeValidationError("VERIFIED_SOURCE_BYTES requires matched evidence refs and digests")
        elif self.evidence_refs or self.evidence_digests:
            raise CreativeValidationError("UNKNOWN research_status cannot assert verified external evidence")

        for name in (
            "cycle_job_digest", "cycle_result_digest", "production_job_digest", "production_result_digest",
            "package_digest", "explore_receipt_digest", "selector_receipt_digest",
            "develop_receipt_digest", "package_receipt_digest",
        ):
            val = getattr(self, name)
            if val is not None:
                require_digest(val)
        if (self.cycle_job_digest is None) != (self.cycle_result_digest is None):
            raise CreativeValidationError("cycle_job_digest and cycle_result_digest must be paired")
        if (self.production_job_digest is None) != (self.production_result_digest is None):
            raise CreativeValidationError("production_job_digest and production_result_digest must be paired")
        pkg_fields = (self.package_id, self.package_revision, self.package_digest)
        if any(v is not None for v in pkg_fields):
            if any(v is None for v in pkg_fields):
                raise CreativeValidationError("package_id, package_revision and package_digest must be paired")
            require_package_id(self.package_id)
            positive_int(self.package_revision, "package_revision")
            if self.production_result_digest is None:
                raise CreativeValidationError("package requires pinned production_result_digest")

        if not isinstance(self.role_traces, (tuple, list)) or not self.role_traces or any(
            not isinstance(t, BenchmarkRoleTrace) for t in self.role_traces
        ):
            raise CreativeValidationError("observation requires non-empty typed role_traces")
        traces = tuple(BenchmarkRoleTrace.from_dict(t.to_dict()) for t in self.role_traces)
        object.__setattr__(self, "role_traces", traces)

        # Replay prevention within the same observation.
        seen_receipts: set[str] = set()
        seen_requests: set[str] = set()
        seen_slots: set[str] = set()
        seen_stages: set[str] = set()
        seen_provider_ids: set[str] = set()
        for t in traces:
            if t.role != "RESEARCH":
                if (t.receipt_digest in seen_receipts or t.job_slot in seen_slots
                        or t.stage_digest in seen_stages
                        or (not t.is_retry and t.request_digest in seen_requests)):
                    raise CreativeValidationError("replayed receipt/request/slot within benchmark observation")
                seen_receipts.add(t.receipt_digest)
                seen_slots.add(t.job_slot)
                seen_stages.add(t.stage_digest)
                if not t.is_retry:
                    seen_requests.add(t.request_digest)
                if t.provider_request_id is not None:
                    if t.provider_request_id in seen_provider_ids:
                        raise CreativeValidationError("replayed provider_request_id within benchmark observation")
                    seen_provider_ids.add(t.provider_request_id)

        # Verify role trace receipts match pinned digests.
        model_traces = tuple(t for t in traces if t.role != "RESEARCH")
        if tuple(t.job_digest for t in model_traces) != self.job_digests:
            raise CreativeValidationError("job_digests disagrees with role_traces")
        if tuple(t.stage_digest for t in model_traces) != self.stage_result_digests:
            raise CreativeValidationError("stage_result_digests disagrees with role_traces")
        terminal_traces = (tuple(t for t in traces if t.execution_status in ("OK", "VERIFIED_SOURCE_BYTES"))
                           if self.experiment_plan_digest is not None else tuple(t for t in traces if not t.is_retry))
        gen_receipts = tuple(t.receipt_digest for t in terminal_traces if t.role == "GENERATOR")
        critic_receipts = tuple(t.receipt_digest for t in terminal_traces if t.role == "CRITIC")
        sel_receipts = tuple(t.receipt_digest for t in terminal_traces if t.role == "SELECTOR")
        dev_receipts = tuple(t.receipt_digest for t in terminal_traces if t.role == "DEVELOPER")
        if self.explore_receipt_digest != (gen_receipts[0] if gen_receipts else None):
            raise CreativeValidationError("explore_receipt_digest disagrees with role_traces")
        if self.critic_receipt_digests != critic_receipts:
            raise CreativeValidationError("critic_receipt_digests disagrees with role_traces")
        if self.selector_receipt_digest != (sel_receipts[-1] if sel_receipts else None):
            raise CreativeValidationError("selector_receipt_digest disagrees with role_traces")
        if self.develop_receipt_digest != (dev_receipts[-1] if dev_receipts else None):
            raise CreativeValidationError("develop_receipt_digest disagrees with role_traces")
        if self.package_digest is None and self.package_receipt_digest is not None:
            raise CreativeValidationError("package_receipt_digest requires a pinned package")

        # Verify exact telemetry aggregation (no fabricated/tampered sums).
        expected_telemetry = aggregate_benchmark_telemetry(
            traces,
            queue_wait_ms=self.telemetry.queue_wait_ms,
            retry_wall_time_ms=self.telemetry.retry_wall_time_ms,
            human_active_time_ms=self.telemetry.human_active_time_ms,
        )
        if self.telemetry != expected_telemetry:
            raise CreativeValidationError("observation telemetry disagrees with linked role_traces")

        # Verify judge receipt independence & binding.
        if self.judge_receipt is not None:
            if not isinstance(self.judge_receipt, BenchmarkJudgeReceipt):
                raise CreativeValidationError("expected typed BenchmarkJudgeReceipt")
            jr = BenchmarkJudgeReceipt.from_dict(self.judge_receipt.to_dict())
            if (jr.case_digest != self.case_digest
                    or jr.source_brief_digest != self.source_brief_digest
                    or jr.rubric_version != self.rubric_version):
                raise CreativeValidationError("judge receipt rebound case, brief or rubric")
            expected_artifact = self.primary_artifact_digest
            if jr.evaluated_artifact_digest != expected_artifact:
                raise CreativeValidationError("judge receipt does not match evaluated artifact digest")
            if jr.judge_actor_ref in seen_receipts or jr.judge_actor_ref in seen_provider_ids:
                raise CreativeValidationError("judge cannot reuse workflow actor/receipt identity")
            if jr.judge_execution_ref is not None and (
                jr.judge_execution_ref in seen_receipts or jr.judge_execution_ref in seen_provider_ids
            ):
                raise CreativeValidationError("judge execution_ref cannot replay a workflow invocation")
            if not set(jr.evidence_refs) <= set(self.evidence_refs):
                raise CreativeValidationError("judge receipt cited unverified evidence_refs")

        expected_semantic = _derive_semantic_status(
            judge_receipt=self.judge_receipt,
            structural_check=self.structural_check,
            execution_mode=self.execution_mode,
            provenance=self.provenance,
        )
        if self.semantic_status != expected_semantic:
            raise CreativeValidationError("semantic_status disagrees with verified judge/structural evidence")

        # Verify A04 consumer receipt correlation.
        if self.consumer_evidence.source_handoff_status == "SOURCE_HANDOFF_VERIFIED":
            if self.execution_mode != "MODEL" or self.status == "TEST_ONLY_MOCK":
                raise CreativeValidationError("TEST_ONLY_MOCK/UNKNOWN observation cannot claim verified A04 handoff")
            a04 = self.consumer_evidence.a04_receipt
            if (self.package_digest is None or self.production_result_digest is None
                    or a04.source_sha != self.source_sha
                    or a04.brief_id != self.brief_id
                    or a04.source_brief_digest != self.source_brief_digest
                    or a04.a03_result_digest != self.production_result_digest
                    or a04.package_id != self.package_id
                    or a04.revision != self.package_revision
                    or a04.package_digest != self.package_digest
                    or a04.cycle_job_digest != self.cycle_job_digest
                    or a04.cycle_result_digest != self.cycle_result_digest
                    or a04.reasoning_receipt_digest != self.package_receipt_digest):
                raise CreativeValidationError("A04 handoff receipt disagrees with benchmark observation artifacts")

        # Mock-vs-real and strategy completion gates.
        non_retry_model = tuple(t for t in terminal_traces if t.role != "RESEARCH")
        strategy_holds = _strategy_hold_reasons(
            strategy=self.strategy,
            role_traces=traces,
            cycle_result_digest=self.cycle_result_digest,
            research_status=self.research_status,
            evidence_refs=self.evidence_refs,
            include_retried_success=self.experiment_plan_digest is not None,
        )
        if self.status in ("TEST_ONLY_MOCK", "REAL_MODEL_OBSERVED"):
            if self.hold_reasons or self.errors or strategy_holds:
                raise CreativeValidationError("completed benchmark observation cannot have hold_reasons, errors or missing strategy roles")
            if not non_retry_model or any(t.execution_status != "OK" for t in non_retry_model):
                raise CreativeValidationError("completed benchmark observation cannot have failed or UNKNOWN workflow traces")
        if self.status == "REAL_MODEL_OBSERVED":
            if (self.execution_mode != "MODEL" or self.observed_provider.mode != "MODEL"
                    or self.observed_provider.provider == "UNKNOWN"
                    or self.observed_provider.model == "UNKNOWN"
                    or self.requested_configuration is None
                    or (self.observed_provider.provider, self.observed_provider.model)
                    != (self.requested_configuration.provider, self.requested_configuration.model)
                    or self.requested_configuration.effort in (None, "UNKNOWN")
                    or self.observed_effort != self.requested_configuration.effort
                    or any(t.observed.mode != "MODEL" or t.observed.model == "UNKNOWN" for t in non_retry_model)):
                raise CreativeValidationError("REAL_MODEL_OBSERVED requires verified MODEL identity and effort across traces")
        elif self.status == "TEST_ONLY_MOCK":
            if (self.execution_mode != "TEST_ONLY_MOCK" or self.observed_provider.mode != "TEST_ONLY_MOCK"
                    or self.observed_provider.model != "UNKNOWN"
                    or any(t.observed.mode != "TEST_ONLY_MOCK" or t.observed.model != "UNKNOWN"
                           for t in non_retry_model)):
                raise CreativeValidationError("TEST_ONLY_MOCK observation requires mock mode and UNKNOWN model")
        else:
            if not self.hold_reasons and not self.errors:
                raise CreativeValidationError("HOLD/FAILED observation must record hold_reasons or errors")

        # Benchmark validity cannot promote mock/synthetic/HOLD to real benchmark.
        if self.status in ("HOLD", "FAILED"):
            expected_validity = "HOLD"
        elif self.status == "TEST_ONLY_MOCK" or self.provenance.evidence_type != "HUMAN_SUPPLIED":
            expected_validity = "NOT_REAL_BENCHMARK"
        else:
            expected_validity = "ELIGIBLE_REAL_BENCHMARK"
        if self.benchmark_validity != expected_validity:
            raise CreativeValidationError("benchmark_validity disagrees with execution mode and provenance")

        expected_nsm = _evaluate_observation_nsm(
            status=self.status,
            execution_mode=self.execution_mode,
            provenance=self.provenance,
            structural_check=self.structural_check,
            semantic_status=self.semantic_status,
            judge_receipt=self.judge_receipt,
            telemetry=self.telemetry,
            consumer_evidence=self.consumer_evidence,
            hold_reasons=self.hold_reasons,
            errors=self.errors,
        )
        if self.nsm != expected_nsm:
            raise CreativeValidationError("observation NSM_v1 score disagrees with verified trust/telemetry gates")

    @property
    def primary_artifact_digest(self) -> str:
        if self.package_digest is not None:
            return self.package_digest
        if self.cycle_result_digest is not None:
            return self.cycle_result_digest
        if self.stage_result_digests:
            return self.stage_result_digests[-1]
        return self.case_digest

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @property
    def slot(self) -> str:
        return _benchmark_observation_slot(
            self.cohort_id, self.case_id, self.strategy, self.sample_id, self.attempt,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sample_id": self.sample_id,
            "attempt": self.attempt,
            "cohort_id": self.cohort_id,
            "case_id": self.case_id,
            "case_digest": self.case_digest,
            "rubric_version": self.rubric_version,
            "source_sha": self.source_sha,
            "brief_id": self.brief_id,
            "source_brief_digest": self.source_brief_digest,
            "brief_class": self.brief_class,
            "strategy": self.strategy,
            "requested_configuration": self.requested_configuration.to_dict() if self.requested_configuration else None,
            "observed_provider": self.observed_provider.to_dict(),
            "observed_effort": self.observed_effort,
            "job_digests": list(self.job_digests),
            "stage_result_digests": list(self.stage_result_digests),
            "cycle_job_digest": self.cycle_job_digest,
            "cycle_result_digest": self.cycle_result_digest,
            "production_job_digest": self.production_job_digest,
            "production_result_digest": self.production_result_digest,
            "package_id": self.package_id,
            "package_revision": self.package_revision,
            "package_digest": self.package_digest,
            "explore_receipt_digest": self.explore_receipt_digest,
            "critic_receipt_digests": list(self.critic_receipt_digests),
            "selector_receipt_digest": self.selector_receipt_digest,
            "develop_receipt_digest": self.develop_receipt_digest,
            "package_receipt_digest": self.package_receipt_digest,
            "consumer_evidence": self.consumer_evidence.to_dict(),
            "judge_receipt": self.judge_receipt.to_dict() if self.judge_receipt else None,
            "provenance": self.provenance.to_dict(),
            "research_status": self.research_status,
            "evidence_refs": list(self.evidence_refs),
            "evidence_digests": list(self.evidence_digests),
            "role_traces": [t.to_dict() for t in self.role_traces],
            "structural_check": self.structural_check.to_dict(),
            "semantic_status": self.semantic_status,
            "telemetry": self.telemetry.to_dict(),
            "nsm": self.nsm.to_dict(),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "execution_mode": self.execution_mode,
            "status": self.status,
            "hold_reasons": list(self.hold_reasons),
            "errors": list(self.errors),
            "benchmark_validity": self.benchmark_validity,
            "side_effects": self.side_effects,
            **({"experiment_plan_digest": self.experiment_plan_digest}
               if self.experiment_plan_digest is not None else {}),
        }

    def to_json(self) -> str:
        return canonical_bytes(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, data: object) -> A03BenchmarkObservation:
        optional = {"experiment_plan_digest"}
        value = exact_fields(data, {f.name for f in fields(cls)} - optional, optional)
        if (value["schema_version"] == "A03BenchmarkObservation.v2") != ("experiment_plan_digest" in value):
            raise CreativeValidationError("experiment plan pin requires benchmark observation v2")
        if value["requested_configuration"] is not None:
            value["requested_configuration"] = ModelConfiguration.from_dict(value["requested_configuration"])
        value["observed_provider"] = ProviderObservation.from_dict(value["observed_provider"])
        value["consumer_evidence"] = BenchmarkConsumerEvidence.from_dict(value["consumer_evidence"])
        if value["judge_receipt"] is not None:
            value["judge_receipt"] = BenchmarkJudgeReceipt.from_dict(value["judge_receipt"])
        value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
        if not isinstance(value["role_traces"], list):
            raise CreativeValidationError("expected role_traces array")
        value["role_traces"] = tuple(BenchmarkRoleTrace.from_dict(t) for t in value["role_traces"])
        value["structural_check"] = BenchmarkStructuralCheck.from_dict(value["structural_check"])
        value["telemetry"] = BenchmarkTelemetry.from_dict(value["telemetry"])
        value["nsm"] = BenchmarkNSMScore.from_dict(value["nsm"])
        for name in ("job_digests", "stage_result_digests", "critic_receipt_digests",
                     "evidence_refs", "evidence_digests", "hold_reasons", "errors"):
            if not isinstance(value[name], list):
                raise CreativeValidationError(f"expected {name} array")
        return cls(**value)

    @classmethod
    def from_json(cls, text: str) -> A03BenchmarkObservation:
        return cls.from_dict(read_json(text))


@dataclass(frozen=True, slots=True)
class BenchmarkStrategySummary:
    """Paired summary for one strategy label within a cohort comparison."""

    strategy: Literal["S1", "S2", "S3"]
    observation_digests: tuple[str, ...]
    observed_case_ids: tuple[str, ...]
    paired_case_ids: tuple[str, ...]
    missing_case_ids: tuple[str, ...]
    real_model_count: int
    mock_count: int
    hold_or_failed_count: int
    structural_valid_count: int
    measured_semantic_pass_count: int
    a04_handoff_verified_count: int
    useful_consumer_outcome_count: int
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_wall_time_ms: int | None = None
    total_human_active_time_ms: int | None = None
    mean_nsm_v1: float | None = None
    missing_coverage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.strategy not in BENCHMARK_STRATEGIES:
            raise CreativeValidationError("invalid strategy in summary")
        object.__setattr__(self, "observation_digests", _distinct_digests(self.observation_digests, "observation_digests"))
        for name in ("observed_case_ids", "paired_case_ids", "missing_case_ids", "missing_coverage"):
            object.__setattr__(self, name, _distinct_texts(getattr(self, name), name))
        for name in (
            "real_model_count", "mock_count", "hold_or_failed_count", "structural_valid_count",
            "measured_semantic_pass_count", "a04_handoff_verified_count", "useful_consumer_outcome_count",
        ):
            _nonneg_int(getattr(self, name), name)
        for name in ("total_input_tokens", "total_output_tokens", "total_wall_time_ms", "total_human_active_time_ms"):
            _nonneg_int_or_none(getattr(self, name), name)
        if self.mean_nsm_v1 is not None:
            object.__setattr__(self, "mean_nsm_v1", _unit_score(self.mean_nsm_v1, "mean_nsm_v1"))
            if not self.paired_case_ids or self.missing_coverage:
                raise CreativeValidationError("mean_nsm_v1 requires complete paired coverage without missing_coverage")

    def to_dict(self) -> dict[str, Any]:
        return {
            **{f.name: getattr(self, f.name) for f in fields(self)},
            "observation_digests": list(self.observation_digests),
            "observed_case_ids": list(self.observed_case_ids),
            "paired_case_ids": list(self.paired_case_ids),
            "missing_case_ids": list(self.missing_case_ids),
            "missing_coverage": list(self.missing_coverage),
        }

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkStrategySummary:
        value = exact_fields(data, {f.name for f in fields(cls)})
        for name in ("observation_digests", "observed_case_ids", "paired_case_ids",
                     "missing_case_ids", "missing_coverage"):
            if not isinstance(value[name], list):
                raise CreativeValidationError(f"expected {name} array")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class A03BenchmarkComparison:
    """Paired cohort comparison across strategy labels; never fabricates a WIN/PASS."""

    comparison_id: str
    cohort_id: str
    source_sha: str
    case_digests: tuple[str, ...]
    case_ids: tuple[str, ...]
    compared_strategies: tuple[Literal["S1", "S2", "S3"], ...]
    observation_digests: tuple[str, ...]
    paired_case_ids: tuple[str, ...]
    missing_cases_by_strategy: dict[str, tuple[str, ...]]
    strategy_summaries: tuple[BenchmarkStrategySummary, ...]
    nsm_weights: dict[str, float]
    outcome: Literal["NOT_REAL_BENCHMARK", "INSUFFICIENT_EVIDENCE", "INCOMPLETE", "HOLD", "COMPARED"]
    ranking_status: Literal["UNRANKED", "PAIRED_COMPARED"]
    provenance: CreativeJobProvenance
    created_at: str
    winner_strategy: Literal["S1", "S2", "S3"] | None = None
    hold_reasons: tuple[str, ...] = ()
    missing_coverage: tuple[str, ...] = ()
    rubric_version: str = BENCHMARK_RUBRIC_VERSION
    nsm_version: str = NSM_V1_VERSION
    side_effects: Literal["NONE"] = "NONE"
    schema_version: str = "A03BenchmarkComparison.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "A03BenchmarkComparison.v1":
            raise CreativeValidationError("unsupported benchmark comparison schema")
        if self.rubric_version != BENCHMARK_RUBRIC_VERSION:
            raise CreativeValidationError("unsupported benchmark comparison rubric version")
        if self.nsm_version != NSM_V1_VERSION:
            raise CreativeValidationError("unsupported benchmark comparison NSM version")
        if self.side_effects != "NONE":
            raise CreativeValidationError("benchmark comparison forbids external/auth/spend side effects")
        for name in ("comparison_id", "cohort_id"):
            require_text(getattr(self, name), name)
        _require_source_sha(self.source_sha, "source_sha")
        _require_iso_timestamp(self.created_at, "created_at")
        if not isinstance(self.provenance, CreativeJobProvenance):
            raise CreativeValidationError("expected typed comparison provenance")
        CreativeJobProvenance.from_dict(self.provenance.to_dict())
        object.__setattr__(self, "case_digests", _distinct_digests(self.case_digests, "case_digests"))
        object.__setattr__(self, "case_ids", _distinct_texts(self.case_ids, "case_ids"))
        if not self.case_ids or len(self.case_ids) != len(self.case_digests):
            raise CreativeValidationError("comparison requires matched non-empty cohort cases")
        strategies = _distinct_texts(self.compared_strategies, "compared_strategies")
        if len(strategies) < 2 or not set(strategies) <= set(BENCHMARK_STRATEGIES):
            raise CreativeValidationError("comparison requires at least two distinct valid strategy labels")
        object.__setattr__(self, "compared_strategies", strategies)
        object.__setattr__(self, "observation_digests", _distinct_digests(self.observation_digests, "observation_digests"))
        object.__setattr__(self, "paired_case_ids", _distinct_texts(self.paired_case_ids, "paired_case_ids"))
        if not set(self.paired_case_ids) <= set(self.case_ids):
            raise CreativeValidationError("paired_case_ids must be subset of cohort case_ids")
        if not isinstance(self.missing_cases_by_strategy, Mapping) or set(self.missing_cases_by_strategy) != set(strategies):
            raise CreativeValidationError("missing_cases_by_strategy must cover exact compared_strategies")
        norm_missing = {
            k: _distinct_texts(v, f"missing_cases_by_strategy[{k}]")
            for k, v in self.missing_cases_by_strategy.items()
        }
        object.__setattr__(self, "missing_cases_by_strategy", norm_missing)
        if not isinstance(self.strategy_summaries, (tuple, list)) or any(
            not isinstance(s, BenchmarkStrategySummary) for s in self.strategy_summaries
        ):
            raise CreativeValidationError("expected typed strategy_summaries")
        summaries = tuple(BenchmarkStrategySummary.from_dict(s.to_dict()) for s in self.strategy_summaries)
        if tuple(s.strategy for s in summaries) != strategies:
            raise CreativeValidationError("strategy_summaries must match compared_strategies order")
        object.__setattr__(self, "strategy_summaries", summaries)
        if not isinstance(self.nsm_weights, Mapping) or dict(self.nsm_weights) != NSM_V1_WEIGHTS:
            raise CreativeValidationError("nsm_weights must pin exact NSM_v1 weights")
        object.__setattr__(self, "nsm_weights", dict(NSM_V1_WEIGHTS))
        if self.outcome not in ("NOT_REAL_BENCHMARK", "INSUFFICIENT_EVIDENCE", "INCOMPLETE", "HOLD", "COMPARED"):
            raise CreativeValidationError("invalid comparison outcome")
        if self.ranking_status not in ("UNRANKED", "PAIRED_COMPARED"):
            raise CreativeValidationError("invalid ranking_status")
        object.__setattr__(self, "hold_reasons", _distinct_texts(self.hold_reasons, "hold_reasons"))
        object.__setattr__(self, "missing_coverage", _distinct_texts(self.missing_coverage, "missing_coverage"))
        if self.winner_strategy is not None and self.winner_strategy not in strategies:
            raise CreativeValidationError("winner_strategy must belong to compared_strategies")

        if self.outcome == "COMPARED":
            if (self.ranking_status != "PAIRED_COMPARED" or not self.paired_case_ids
                    or self.hold_reasons or self.missing_coverage
                    or self.provenance.evidence_type != "HUMAN_SUPPLIED"
                    or any(s.mean_nsm_v1 is None or s.mock_count > 0 or s.hold_or_failed_count > 0
                           for s in summaries)):
                raise CreativeValidationError("COMPARED outcome requires complete real paired NSM_v1 scores")
            best = max(s.mean_nsm_v1 for s in summaries)
            leaders = [s.strategy for s in summaries if abs(s.mean_nsm_v1 - best) <= 1e-9]
            expected_winner = leaders[0] if len(leaders) == 1 else None
            if self.winner_strategy != expected_winner:
                raise CreativeValidationError("winner_strategy disagrees with paired NSM_v1 scores")
        else:
            if self.ranking_status != "UNRANKED" or self.winner_strategy is not None:
                raise CreativeValidationError("non-COMPARED comparison must stay UNRANKED with no winner")
            if self.outcome == "HOLD" and not self.hold_reasons:
                raise CreativeValidationError("HOLD comparison requires hold_reasons")

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @property
    def slot(self) -> str:
        return _benchmark_comparison_slot(self.cohort_id, self.comparison_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "comparison_id": self.comparison_id,
            "cohort_id": self.cohort_id,
            "rubric_version": self.rubric_version,
            "source_sha": self.source_sha,
            "case_digests": list(self.case_digests),
            "case_ids": list(self.case_ids),
            "compared_strategies": list(self.compared_strategies),
            "observation_digests": list(self.observation_digests),
            "paired_case_ids": list(self.paired_case_ids),
            "missing_cases_by_strategy": {k: list(v) for k, v in self.missing_cases_by_strategy.items()},
            "strategy_summaries": [s.to_dict() for s in self.strategy_summaries],
            "nsm_version": self.nsm_version,
            "nsm_weights": dict(self.nsm_weights),
            "outcome": self.outcome,
            "ranking_status": self.ranking_status,
            "winner_strategy": self.winner_strategy,
            "hold_reasons": list(self.hold_reasons),
            "missing_coverage": list(self.missing_coverage),
            "provenance": self.provenance.to_dict(),
            "created_at": self.created_at,
            "side_effects": self.side_effects,
        }

    def to_json(self) -> str:
        return canonical_bytes(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, data: object) -> A03BenchmarkComparison:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
        if not isinstance(value["strategy_summaries"], list):
            raise CreativeValidationError("expected strategy_summaries array")
        value["strategy_summaries"] = tuple(BenchmarkStrategySummary.from_dict(s) for s in value["strategy_summaries"])
        if not isinstance(value["missing_cases_by_strategy"], dict):
            raise CreativeValidationError("expected missing_cases_by_strategy object")
        value["missing_cases_by_strategy"] = {
            k: tuple(v) if isinstance(v, list) else (_ for _ in ()).throw(
                CreativeValidationError("expected missing cases array")
            )
            for k, v in value["missing_cases_by_strategy"].items()
        }
        for name in ("case_digests", "case_ids", "compared_strategies", "observation_digests",
                     "paired_case_ids", "hold_reasons", "missing_coverage"):
            if not isinstance(value[name], list):
                raise CreativeValidationError(f"expected {name} array")
        return cls(**value)

    @classmethod
    def from_json(cls, text: str) -> A03BenchmarkComparison:
        return cls.from_dict(read_json(text))


def _role_for_operation(operation: str) -> Literal[
    "GENERATOR", "CRITIC", "SELECTOR", "DEVELOPER", "PACKAGE_PRODUCER"
]:
    mapping = {
        "EXPLORE": "GENERATOR",
        "CRITIQUE": "CRITIC",
        "SELECT": "SELECTOR",
        "DEVELOP": "DEVELOPER",
        "PRODUCE_PACKAGE": "PACKAGE_PRODUCER",
        "REVISE_PACKAGE": "PACKAGE_PRODUCER",
    }
    if operation not in mapping:
        raise CreativeValidationError(f"unsupported benchmark stage operation: {operation}")
    return mapping[operation]


def _stage_to_trace(
    stage: A03CreativeStageResult,
    *,
    step_name: str,
    is_retry: bool = False,
    step_timings_ms: Mapping[str, int] | None = None,
    step_retries: Mapping[str, int] | None = None,
) -> BenchmarkRoleTrace:
    r = stage.reasoning
    op = r.request.operation
    role = _role_for_operation(op)
    if stage.status in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK") and r.state == "OK":
        exec_status = "OK"
    elif r.state == "REFUSED":
        exec_status = "REFUSED"
    elif r.observation.mode == "UNKNOWN" or (r.execution is not None and r.execution.effect == "UNKNOWN"):
        exec_status = "UNKNOWN"
    else:
        exec_status = "FAILED"
    obs_effort = r.execution.observed_effort if r.execution is not None else "UNKNOWN"
    wall_ms = None
    if step_timings_ms is not None:
        if r.digest in step_timings_ms:
            wall_ms = step_timings_ms[r.digest]
        elif step_name in step_timings_ms:
            wall_ms = step_timings_ms[step_name]
    retries = None
    if step_retries is not None:
        if r.digest in step_retries:
            retries = step_retries[r.digest]
        elif step_name in step_retries:
            retries = step_retries[step_name]
    return BenchmarkRoleTrace(
        role=role,
        step_name=step_name,
        operation=op,
        execution_status=exec_status,
        job_slot=stage.job.slot,
        job_digest=stage.job.digest,
        stage_digest=stage.digest,
        request_digest=r.request.digest,
        receipt_digest=r.digest,
        provider_request_id=r.provider_request_id,
        requested=r.configuration if r.configuration is not None else stage.job.configuration,
        observed=r.observation,
        observed_effort=obs_effort,
        usage=r.usage,
        wall_time_ms=wall_ms,
        retry_count=retries,
        is_retry=is_retry,
        evidence_refs=r.evidence_refs,
    )


def observe_benchmark_run(
    case: A03BenchmarkCase,
    store: CreativeStore | CreativeSidecarStore,
    *,
    strategy: Literal["S1", "S2", "S3"],
    sample_id: str,
    attempt: int,
    provenance: CreativeJobProvenance,
    started_at: str,
    completed_at: str,
    stages: tuple[A03CreativeStageResult, ...] = (),
    retry_stages: tuple[A03CreativeStageResult, ...] = (),
    cycle: A03CreativeCycleResult | None = None,
    production_result: A03CreativeProductionResult | None = None,
    package: A03CreativeProductionPackage | None = None,
    a04_receipt: Any | None = None,
    a04_receipt_path: str | Path | None = None,
    judge_receipt: BenchmarkJudgeReceipt | None = None,
    media_quality_status: Literal["NOT_ATTEMPTED", "UNMEASURED", "OBSERVED_PASS", "OBSERVED_FAIL"] = "NOT_ATTEMPTED",
    media_quality_ref: str | None = None,
    commercial_utility_status: Literal[
        "NOT_ATTEMPTED", "UNMEASURED", "OBSERVED_USEFUL", "OBSERVED_NOT_USEFUL"
    ] = "NOT_ATTEMPTED",
    commercial_utility_ref: str | None = None,
    step_timings_ms: Mapping[str, int] | None = None,
    step_retries: Mapping[str, int] | None = None,
    queue_wait_ms: int | None = None,
    retry_wall_time_ms: int | None = None,
    human_active_time_ms: int | None = None,
) -> A03BenchmarkObservation:
    """Record an immutable A03BenchmarkObservation.v1 from actually persisted A03/R4 artifacts.

    Never invokes any provider, network, spend, or orchestrator. Re-verifies every
    referenced artifact on disk via CreativeSidecarStore.
    """
    from .sidecars import CreativeSidecarStore
    from ai_commerce.media.generation import A04CreativeHandoffReceipt, load_a04_handoff_receipt

    if not isinstance(case, A03BenchmarkCase):
        raise CreativeValidationError("expected A03BenchmarkCase")
    sidecars = store if isinstance(store, CreativeSidecarStore) else CreativeSidecarStore(store)
    saved_case = sidecars.load_benchmark_case_by_digest(case.digest)
    if saved_case != case:
        raise CreativeValidationError("benchmark case disagrees with stored cohort record")
    brief = sidecars.creative.load_brief(saved_case.source_brief_digest)

    hold_reasons: list[str] = []
    errors: list[str] = []
    traces: list[BenchmarkRoleTrace] = []
    all_stages: list[A03CreativeStageResult] = []
    evidence_sources: tuple[CreativeEvidenceSource, ...] = ()

    cycle_job_digest: str | None = None
    cycle_result_digest: str | None = None
    prod_job_digest: str | None = None
    prod_result_digest: str | None = None
    pkg_id: str | None = None
    pkg_rev: int | None = None
    pkg_digest: str | None = None
    explore_digest: str | None = None
    critic_digests: list[str] = []
    selector_digest: str | None = None
    develop_digest: str | None = None
    package_receipt_digest: str | None = None
    verified_pkg: A03CreativeProductionPackage | None = None

    if cycle is not None:
        if stages:
            raise CreativeValidationError("supply either standalone stages or a verified R2 cycle, not both")
        if not isinstance(cycle, A03CreativeCycleResult):
            raise CreativeValidationError("expected A03CreativeCycleResult")
        saved_cycle = sidecars.load_cycle_by_digest(cycle.job.digest, cycle.digest)
        if saved_cycle != cycle:
            raise CreativeValidationError("cycle disagrees with stored R2 receipt")
        if (saved_cycle.job.seed.brief_id != saved_case.brief_id
                or saved_cycle.job.seed.source_brief_digest != saved_case.source_brief_digest):
            raise CreativeValidationError("cycle brief disagrees with pinned benchmark case")
        cycle_job_digest = saved_cycle.job.digest
        cycle_result_digest = saved_cycle.digest
        evidence_sources = saved_cycle.job.evidence
        if evidence_sources:
            res_wall = step_timings_ms.get("research") if step_timings_ms is not None else None
            res_ret = step_retries.get("research") if step_retries is not None else None
            traces.append(BenchmarkRoleTrace(
                role="RESEARCH",
                step_name="research",
                operation="RESEARCH",
                execution_status="VERIFIED_SOURCE_BYTES",
                wall_time_ms=res_wall,
                retry_count=res_ret,
                evidence_refs=tuple(e.source_ref for e in evidence_sources),
                evidence_digests=tuple(e.content_sha256 for e in evidence_sources),
            ))
        if saved_cycle.status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK"):
            hold_reasons.append(f"CYCLE_{saved_cycle.status}")
            if saved_cycle.failure:
                errors.append(saved_cycle.failure)
        for st in saved_cycle.steps:
            step_name = st.job.request.cycle.step if st.job.request.cycle else st.job.job_id
            all_stages.append(st)
            traces.append(_stage_to_trace(
                st, step_name=step_name, step_timings_ms=step_timings_ms, step_retries=step_retries,
            ))
            op = st.job.request.operation
            if op == "EXPLORE" and explore_digest is None:
                explore_digest = st.reasoning.digest
            elif op == "CRITIQUE":
                critic_digests.append(st.reasoning.digest)
            elif op == "SELECT":
                selector_digest = st.reasoning.digest
            elif op == "DEVELOP":
                develop_digest = st.reasoning.digest
    else:
        if not stages:
            raise CreativeValidationError("benchmark observation requires persisted stage or cycle evidence")
        explored_concept_ids: set[tuple[str, int, str]] = set()
        for idx, st in enumerate(stages):
            if not isinstance(st, A03CreativeStageResult):
                raise CreativeValidationError("expected A03CreativeStageResult")
            saved_st = sidecars.load_stage(st.job)
            if saved_st != st:
                raise CreativeValidationError("stage disagrees with stored receipt")
            if (saved_st.job.brief_id != saved_case.brief_id
                    or saved_st.job.source_brief_digest != saved_case.source_brief_digest):
                raise CreativeValidationError("stage brief disagrees with pinned benchmark case")
            all_stages.append(saved_st)
            step_name = f"{saved_st.job.request.operation.lower()}:{idx}"
            traces.append(_stage_to_trace(
                saved_st, step_name=step_name, step_timings_ms=step_timings_ms, step_retries=step_retries,
            ))
            op = saved_st.job.request.operation
            if op == "EXPLORE":
                if explore_digest is None:
                    explore_digest = saved_st.reasoning.digest
                if saved_st.reasoning.state == "OK":
                    for c in saved_st.reasoning.concepts():
                        explored_concept_ids.add((c.concept_id, c.revision, c.digest))
            elif op == "CRITIQUE":
                src = saved_st.job.request.source
                if src is None or (src.concept_id, src.revision, src.digest) not in explored_concept_ids:
                    hold_reasons.append("CRITIC_NOT_BOUND_TO_GENERATOR_OUTPUT")
                critic_digests.append(saved_st.reasoning.digest)
            elif op == "SELECT":
                selector_digest = saved_st.reasoning.digest
            elif op == "DEVELOP":
                develop_digest = saved_st.reasoning.digest

    # Link any observed prior retry stages (verified on disk).
    for r_idx, r_st in enumerate(retry_stages):
        if not isinstance(r_st, A03CreativeStageResult):
            raise CreativeValidationError("expected A03CreativeStageResult in retry_stages")
        saved_r = sidecars.load_stage(r_st.job)
        if saved_r != r_st:
            raise CreativeValidationError("retry stage disagrees with stored receipt")
        if (saved_r.job.brief_id != saved_case.brief_id
                or saved_r.job.source_brief_digest != saved_case.source_brief_digest):
            raise CreativeValidationError("retry stage brief disagrees with pinned benchmark case")
        all_stages.append(saved_r)
        traces.append(_stage_to_trace(
            saved_r,
            step_name=f"retry:{saved_r.job.request.operation.lower()}:{r_idx}",
            is_retry=True,
            step_timings_ms=step_timings_ms,
            step_retries=step_retries,
        ))

    if production_result is not None:
        if not isinstance(production_result, A03CreativeProductionResult):
            raise CreativeValidationError("expected A03CreativeProductionResult")
        saved_prod = sidecars.load_production_result_by_digest(production_result.digest)
        if saved_prod != production_result:
            raise CreativeValidationError("production result disagrees with stored receipt")
        if (saved_prod.job.brief_id != saved_case.brief_id
                or saved_prod.job.source_brief_digest != saved_case.source_brief_digest):
            raise CreativeValidationError("production result brief disagrees with pinned benchmark case")
        if cycle_result_digest is not None and (
            saved_prod.job.cycle_job_digest != cycle_job_digest
            or saved_prod.job.cycle_result_digest != cycle_result_digest
        ):
            raise CreativeValidationError("production result disagrees with pinned R2 cycle")
        prod_job_digest = saved_prod.job.digest
        prod_result_digest = saved_prod.digest
        if saved_prod.stage is not None:
            all_stages.append(saved_prod.stage)
            traces.append(_stage_to_trace(
                saved_prod.stage,
                step_name="produce_package" if saved_prod.job.parent_package_digest is None else "revise_package",
                step_timings_ms=step_timings_ms,
                step_retries=step_retries,
            ))
        if saved_prod.status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK") or saved_prod.package is None:
            hold_reasons.append(f"PRODUCTION_{saved_prod.status}")
            if saved_prod.failure:
                errors.append(saved_prod.failure)
        else:
            target_pkg = saved_prod.package if package is None else package
            if not isinstance(target_pkg, A03CreativeProductionPackage):
                raise CreativeValidationError("expected A03CreativeProductionPackage")
            origin, verified_pkg, _ = sidecars.verify_production_package_lineage(
                target_pkg.package_id, target_pkg.revision, origin_result_digest=saved_prod.digest,
            )
            if origin != saved_prod or verified_pkg != target_pkg:
                raise CreativeValidationError("production package lineage verification failed")
            pkg_id = verified_pkg.package_id
            pkg_rev = verified_pkg.revision
            pkg_digest = verified_pkg.digest
            package_receipt_digest = verified_pkg.reasoning_receipt_digest
    elif package is not None:
        raise CreativeValidationError("package requires pinned production_result")

    # Correlate optional R4 A04CreativeHandoffReceipt strictly.
    resolved_a04: Any | None = None
    resolved_a04_ref: str | None = None
    if a04_receipt_path is not None:
        try:
            loaded_a04 = load_a04_handoff_receipt(a04_receipt_path)
        except ValueError as exc:
            raise CreativeValidationError(f"invalid persisted A04 handoff receipt: {exc}") from exc
        if a04_receipt is not None and loaded_a04 != a04_receipt:
            raise CreativeValidationError("supplied a04_receipt disagrees with persisted file bytes")
        resolved_a04 = loaded_a04
        resolved_a04_ref = str(a04_receipt_path)
    elif a04_receipt is not None:
        if not isinstance(a04_receipt, A04CreativeHandoffReceipt):
            raise CreativeValidationError("expected A04CreativeHandoffReceipt")
        try:
            resolved_a04 = A04CreativeHandoffReceipt.from_dict(a04_receipt.to_dict())
        except ValueError as exc:
            raise CreativeValidationError(f"invalid A04 handoff receipt: {exc}") from exc

    consumer_evidence = BenchmarkConsumerEvidence(
        source_handoff_status="SOURCE_HANDOFF_VERIFIED" if resolved_a04 is not None else "NOT_AVAILABLE",
        a04_receipt=resolved_a04,
        a04_receipt_digest=resolved_a04.digest if resolved_a04 is not None else None,
        a04_receipt_ref=resolved_a04_ref,
        media_quality_status=media_quality_status,
        media_quality_ref=media_quality_ref,
        commercial_utility_status=commercial_utility_status,
        commercial_utility_ref=commercial_utility_ref,
    )

    traces_tuple = tuple(traces)
    model_traces = tuple(t for t in traces_tuple if t.role != "RESEARCH")
    non_retry_model = tuple(t for t in model_traces if not t.is_retry)
    if not non_retry_model:
        raise CreativeValidationError("benchmark observation requires at least one non-retry model trace")

    # Determine requested vs observed identity and detect failed/UNKNOWN/mixed effects.
    requested_configs = [t.requested for t in non_retry_model if t.requested is not None]
    requested_config = requested_configs[0] if requested_configs else None
    if any(t.requested != requested_config for t in non_retry_model):
        hold_reasons.append("MIXED_REQUESTED_CONFIGURATION")

    observed_providers = [t.observed for t in non_retry_model]
    observed_provider = observed_providers[0]
    if any(p != observed_provider for p in observed_providers):
        hold_reasons.append("MIXED_OBSERVED_PROVIDER")

    observed_efforts = {t.observed_effort for t in non_retry_model}
    observed_effort = next(iter(observed_efforts)) if len(observed_efforts) == 1 else "UNKNOWN"

    modes = {t.observed.mode for t in non_retry_model}
    if modes == {"MODEL"}:
        execution_mode: Literal["TEST_ONLY_MOCK", "MODEL", "UNKNOWN"] = "MODEL"
    elif modes == {"TEST_ONLY_MOCK"}:
        execution_mode = "TEST_ONLY_MOCK"
    else:
        execution_mode = "UNKNOWN"
        hold_reasons.append("UNKNOWN_OR_MIXED_EXECUTION_MODE")

    for st in all_stages:
        if any(t.stage_digest == st.digest and t.is_retry for t in model_traces):
            continue
        if st.status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK") or st.reasoning.state != "OK":
            hold_reasons.append("PROVIDER_EFFECT_FAILED_OR_UNKNOWN")
            if st.failure:
                errors.append(st.failure)
            elif st.reasoning.error_code:
                errors.append(st.reasoning.error_code)
        elif st.reasoning.observation.mode == "MODEL":
            e = st.reasoning.execution
            cfg = st.job.configuration
            if (e is None or e.kind != "AUTHENTICATED_CLI_PROBE" or e.effect != "PROCESS_INVOKED"
                    or cfg is None or cfg.effort in (None, "UNKNOWN")
                    or (e.observed_model, e.observed_effort) != (cfg.model, cfg.effort)):
                hold_reasons.append("UNVERIFIED_MODEL_EXECUTION")

    research_status: Literal["VERIFIED_SOURCE_BYTES", "UNKNOWN"] = (
        "VERIFIED_SOURCE_BYTES" if evidence_sources else "UNKNOWN"
    )
    evidence_refs = tuple(e.source_ref for e in evidence_sources)
    evidence_digests = tuple(e.content_sha256 for e in evidence_sources)

    for reason in _strategy_hold_reasons(
        strategy=strategy,
        role_traces=traces_tuple,
        cycle_result_digest=cycle_result_digest,
        research_status=research_status,
        evidence_refs=evidence_refs,
    ):
        hold_reasons.append(reason)

    if judge_receipt is not None and saved_case.factual_evidence_applicable:
        if judge_receipt.scores.factual_evidence_accuracy is None:
            raise CreativeValidationError("factual_evidence_accuracy score is required for this benchmark case")

    dedup_holds = tuple(dict.fromkeys(hold_reasons))
    dedup_errors = tuple(dict.fromkeys(errors))

    if dedup_holds or dedup_errors:
        obs_status: Literal["TEST_ONLY_MOCK", "REAL_MODEL_OBSERVED", "HOLD", "FAILED"] = "HOLD"
        validity: Literal["NOT_REAL_BENCHMARK", "ELIGIBLE_REAL_BENCHMARK", "HOLD"] = "HOLD"
    elif execution_mode == "TEST_ONLY_MOCK":
        obs_status = "TEST_ONLY_MOCK"
        validity = "NOT_REAL_BENCHMARK"
    else:
        obs_status = "REAL_MODEL_OBSERVED"
        validity = "ELIGIBLE_REAL_BENCHMARK" if provenance.evidence_type == "HUMAN_SUPPLIED" else "NOT_REAL_BENCHMARK"

    structural_check = evaluate_benchmark_structure(
        saved_case, brief, stages=tuple(all_stages), package=verified_pkg,
    )
    semantic_status = _derive_semantic_status(
        judge_receipt=judge_receipt,
        structural_check=structural_check,
        execution_mode=execution_mode,
        provenance=provenance,
    )
    telemetry = aggregate_benchmark_telemetry(
        traces_tuple,
        queue_wait_ms=queue_wait_ms,
        retry_wall_time_ms=retry_wall_time_ms,
        human_active_time_ms=human_active_time_ms,
    )
    nsm = _evaluate_observation_nsm(
        status=obs_status,
        execution_mode=execution_mode,
        provenance=provenance,
        structural_check=structural_check,
        semantic_status=semantic_status,
        judge_receipt=judge_receipt,
        telemetry=telemetry,
        consumer_evidence=consumer_evidence,
        hold_reasons=dedup_holds,
        errors=dedup_errors,
    )

    obs = A03BenchmarkObservation(
        sample_id=sample_id,
        attempt=attempt,
        cohort_id=saved_case.cohort_id,
        case_id=saved_case.case_id,
        case_digest=saved_case.digest,
        rubric_version=saved_case.rubric_version,
        source_sha=saved_case.source_sha,
        brief_id=saved_case.brief_id,
        source_brief_digest=saved_case.source_brief_digest,
        brief_class=saved_case.brief_class,
        strategy=strategy,
        requested_configuration=requested_config,
        observed_provider=observed_provider,
        observed_effort=observed_effort,
        job_digests=tuple(t.job_digest for t in model_traces),
        stage_result_digests=tuple(t.stage_digest for t in model_traces),
        cycle_job_digest=cycle_job_digest,
        cycle_result_digest=cycle_result_digest,
        production_job_digest=prod_job_digest,
        production_result_digest=prod_result_digest,
        package_id=pkg_id,
        package_revision=pkg_rev,
        package_digest=pkg_digest,
        explore_receipt_digest=explore_digest,
        critic_receipt_digests=tuple(critic_digests),
        selector_receipt_digest=selector_digest,
        develop_receipt_digest=develop_digest,
        package_receipt_digest=package_receipt_digest,
        consumer_evidence=consumer_evidence,
        judge_receipt=judge_receipt,
        provenance=provenance,
        research_status=research_status,
        evidence_refs=evidence_refs,
        evidence_digests=evidence_digests,
        role_traces=traces_tuple,
        structural_check=structural_check,
        semantic_status=semantic_status,
        telemetry=telemetry,
        nsm=nsm,
        started_at=started_at,
        completed_at=completed_at,
        execution_mode=execution_mode,
        status=obs_status,
        hold_reasons=dedup_holds,
        errors=dedup_errors,
        benchmark_validity=validity,
    )
    sidecars.save_benchmark_observation(obs)
    return sidecars.load_benchmark_observation_by_digest(obs.digest)


def _build_benchmark_comparison_payload(
    *,
    comparison_id: str,
    cases: tuple[A03BenchmarkCase, ...],
    observations: tuple[A03BenchmarkObservation, ...],
    strategies: tuple[Literal["S1", "S2", "S3"], ...],
    provenance: CreativeJobProvenance,
    created_at: str,
) -> A03BenchmarkComparison:
    if not cases:
        raise CreativeValidationError("benchmark comparison requires at least one declared cohort case")
    strategies_norm = _distinct_texts(strategies, "strategies")
    if len(strategies_norm) < 2 or not set(strategies_norm) <= set(BENCHMARK_STRATEGIES):
        raise CreativeValidationError("comparison requires at least two distinct strategy labels")

    cohort_id = cases[0].cohort_id
    rubric_version = cases[0].rubric_version
    source_sha = cases[0].source_sha
    hold_reasons: list[str] = []

    if any(c.cohort_id != cohort_id for c in cases):
        hold_reasons.append("NONCOMPARABLE_COHORT")
    if any(c.rubric_version != rubric_version for c in cases):
        hold_reasons.append("NONCOMPARABLE_RUBRIC")
    if any(c.source_sha != source_sha for c in cases):
        hold_reasons.append("STALE_SOURCE_SHA")

    case_by_id: dict[str, A03BenchmarkCase] = {}
    for c in cases:
        if c.case_id in case_by_id:
            raise CreativeValidationError("duplicate case_id in declared cohort")
        case_by_id[c.case_id] = c

    seen_obs_digests: set[str] = set()
    receipt_owner: dict[str, str] = {}
    job_owner: dict[str, str] = {}
    cycle_owner: dict[str, str] = {}
    provider_id_owner: dict[str, str] = {}

    for obs in observations:
        if obs.digest in seen_obs_digests:
            raise CreativeValidationError("duplicate observation in comparison")
        seen_obs_digests.add(obs.digest)
        if obs.cohort_id != cohort_id:
            hold_reasons.append("NONCOMPARABLE_COHORT")
        if obs.rubric_version != rubric_version:
            hold_reasons.append("NONCOMPARABLE_RUBRIC")
        if obs.source_sha != source_sha:
            hold_reasons.append("STALE_SOURCE_SHA")
        declared = case_by_id.get(obs.case_id)
        if declared is None or declared.digest != obs.case_digest or declared.source_brief_digest != obs.source_brief_digest:
            hold_reasons.append("UNDECLARED_COHORT_CASE")
        # Cross-strategy replay detection: distinct strategies must come from genuinely separate workflows.
        for t in obs.role_traces:
            if t.receipt_digest is not None:
                prev = receipt_owner.get(t.receipt_digest)
                if prev is not None and prev != obs.strategy:
                    hold_reasons.append("REPLAYED_WORKFLOW_ACROSS_STRATEGIES")
                receipt_owner[t.receipt_digest] = obs.strategy
            if t.job_digest is not None:
                prev = job_owner.get(t.job_digest)
                if prev is not None and prev != obs.strategy:
                    hold_reasons.append("REPLAYED_WORKFLOW_ACROSS_STRATEGIES")
                job_owner[t.job_digest] = obs.strategy
            if t.provider_request_id is not None:
                prev = provider_id_owner.get(t.provider_request_id)
                if prev is not None and prev != obs.strategy:
                    hold_reasons.append("REPLAYED_WORKFLOW_ACROSS_STRATEGIES")
                provider_id_owner[t.provider_request_id] = obs.strategy
        if obs.cycle_result_digest is not None:
            prev = cycle_owner.get(obs.cycle_result_digest)
            if prev is not None and prev != obs.strategy:
                hold_reasons.append("REPLAYED_WORKFLOW_ACROSS_STRATEGIES")
            cycle_owner[obs.cycle_result_digest] = obs.strategy

    sorted_case_ids = tuple(sorted(case_by_id))
    sorted_case_digests = tuple(case_by_id[cid].digest for cid in sorted_case_ids)

    valid_by_strategy: dict[str, dict[str, list[A03BenchmarkObservation]]] = {s: {} for s in strategies_norm}
    all_by_strategy: dict[str, list[A03BenchmarkObservation]] = {s: [] for s in strategies_norm}
    for obs in observations:
        if obs.strategy not in all_by_strategy:
            continue
        all_by_strategy[obs.strategy].append(obs)
        if (obs.case_id in case_by_id
                and obs.case_digest == case_by_id[obs.case_id].digest
                and obs.cohort_id == cohort_id
                and obs.rubric_version == rubric_version
                and obs.source_sha == source_sha
                and obs.status in ("TEST_ONLY_MOCK", "REAL_MODEL_OBSERVED")):
            valid_by_strategy[obs.strategy].setdefault(obs.case_id, []).append(obs)

    if not hold_reasons:
        paired_ids = tuple(
            cid for cid in sorted_case_ids
            if all(cid in valid_by_strategy[s] for s in strategies_norm)
        )
    else:
        paired_ids = ()

    missing_by_strategy = {
        s: tuple(cid for cid in sorted_case_ids if cid not in valid_by_strategy[s])
        for s in strategies_norm
    }

    summaries: list[BenchmarkStrategySummary] = []
    global_missing_coverage: list[str] = []
    any_mock_or_synthetic = provenance.evidence_type != "HUMAN_SUPPLIED" or any(
        c.provenance.evidence_type != "HUMAN_SUPPLIED" for c in cases
    )
    for obs in observations:
        if (obs.status == "TEST_ONLY_MOCK" or obs.execution_mode == "TEST_ONLY_MOCK"
                or obs.benchmark_validity == "NOT_REAL_BENCHMARK"
                or obs.provenance.evidence_type != "HUMAN_SUPPLIED"):
            any_mock_or_synthetic = True

    for s in strategies_norm:
        s_all = all_by_strategy[s]
        paired_obs = [obs for cid in paired_ids for obs in valid_by_strategy[s].get(cid, ())]
        s_missing: list[str] = []
        if not paired_obs:
            s_missing.append("NO_PAIRED_CASES")
        for obs in paired_obs:
            for item in obs.nsm.missing_coverage:
                if item not in s_missing:
                    s_missing.append(item)
        if paired_obs and all(o.telemetry.input_tokens_status == "OBSERVED" for o in paired_obs):
            sum_in = sum(o.telemetry.total_input_tokens for o in paired_obs)
        else:
            sum_in = None
        if paired_obs and all(o.telemetry.output_tokens_status == "OBSERVED" for o in paired_obs):
            sum_out = sum(o.telemetry.total_output_tokens for o in paired_obs)
        else:
            sum_out = None
        if paired_obs and all(o.telemetry.wall_time_status == "OBSERVED" for o in paired_obs):
            sum_wall = sum(o.telemetry.total_wall_time_ms for o in paired_obs)
        else:
            sum_wall = None
        if paired_obs and all(o.telemetry.human_active_time_status == "OBSERVED" for o in paired_obs):
            sum_human = sum(o.telemetry.human_active_time_ms for o in paired_obs)
        else:
            sum_human = None
        if (paired_obs and not s_missing and not any_mock_or_synthetic
                and all(o.nsm.status == "SCORED" and o.nsm.score is not None for o in paired_obs)):
            mean_nsm = round(sum(o.nsm.score for o in paired_obs) / len(paired_obs), 6)
        else:
            mean_nsm = None
        for item in s_missing:
            if item not in global_missing_coverage:
                global_missing_coverage.append(item)
        summaries.append(BenchmarkStrategySummary(
            strategy=s,
            observation_digests=tuple(o.digest for o in paired_obs),
            observed_case_ids=tuple(sorted({o.case_id for o in s_all if o.case_id in case_by_id})),
            paired_case_ids=paired_ids,
            missing_case_ids=missing_by_strategy[s],
            real_model_count=sum(1 for o in s_all if o.status == "REAL_MODEL_OBSERVED"),
            mock_count=sum(1 for o in s_all if o.status == "TEST_ONLY_MOCK"),
            hold_or_failed_count=sum(1 for o in s_all if o.status in ("HOLD", "FAILED")),
            structural_valid_count=sum(1 for o in paired_obs if o.structural_check.status == "STRUCTURAL_VALID"),
            measured_semantic_pass_count=sum(1 for o in paired_obs if o.semantic_status == "MEASURED_PASS"),
            a04_handoff_verified_count=sum(
                1 for o in paired_obs if o.consumer_evidence.source_handoff_status == "SOURCE_HANDOFF_VERIFIED"
            ),
            useful_consumer_outcome_count=sum(
                1 for o in paired_obs if o.consumer_evidence.commercial_utility_status == "OBSERVED_USEFUL"
            ),
            total_input_tokens=sum_in,
            total_output_tokens=sum_out,
            total_wall_time_ms=sum_wall,
            total_human_active_time_ms=sum_human,
            mean_nsm_v1=mean_nsm,
            missing_coverage=tuple(s_missing),
        ))

    dedup_holds = tuple(dict.fromkeys(hold_reasons))
    dedup_missing = tuple(dict.fromkeys(global_missing_coverage))

    if dedup_holds:
        outcome: Literal["NOT_REAL_BENCHMARK", "INSUFFICIENT_EVIDENCE", "INCOMPLETE", "HOLD", "COMPARED"] = "HOLD"
        ranking_status: Literal["UNRANKED", "PAIRED_COMPARED"] = "UNRANKED"
        winner: Literal["S1", "S2", "S3"] | None = None
    elif not paired_ids:
        outcome = "INSUFFICIENT_EVIDENCE"
        ranking_status = "UNRANKED"
        winner = None
    elif any_mock_or_synthetic:
        outcome = "NOT_REAL_BENCHMARK"
        ranking_status = "UNRANKED"
        winner = None
    elif dedup_missing or any(s.mean_nsm_v1 is None for s in summaries):
        outcome = "INCOMPLETE"
        ranking_status = "UNRANKED"
        winner = None
    else:
        outcome = "COMPARED"
        ranking_status = "PAIRED_COMPARED"
        best = max(s.mean_nsm_v1 for s in summaries)
        leaders = [s.strategy for s in summaries if abs(s.mean_nsm_v1 - best) <= 1e-9]
        winner = leaders[0] if len(leaders) == 1 else None

    return A03BenchmarkComparison(
        comparison_id=comparison_id,
        cohort_id=cohort_id,
        rubric_version=rubric_version,
        source_sha=source_sha,
        case_digests=sorted_case_digests,
        case_ids=sorted_case_ids,
        compared_strategies=strategies_norm,
        observation_digests=tuple(o.digest for o in observations),
        paired_case_ids=paired_ids,
        missing_cases_by_strategy=missing_by_strategy,
        strategy_summaries=tuple(summaries),
        nsm_version=NSM_V1_VERSION,
        nsm_weights=dict(NSM_V1_WEIGHTS),
        outcome=outcome,
        ranking_status=ranking_status,
        winner_strategy=winner,
        hold_reasons=dedup_holds,
        missing_coverage=dedup_missing,
        provenance=provenance,
        created_at=created_at,
    )


def compare_benchmark_observations(
    *,
    comparison_id: str,
    cases: tuple[A03BenchmarkCase, ...],
    observations: tuple[A03BenchmarkObservation, ...],
    store: CreativeStore | CreativeSidecarStore,
    provenance: CreativeJobProvenance,
    created_at: str,
    strategies: tuple[Literal["S1", "S2", "S3"], ...] = BENCHMARK_STRATEGIES,
) -> A03BenchmarkComparison:
    """Compare persisted observations across strategy labels on a fixed cohort.

    Re-verifies every case and observation from CreativeSidecarStore. Returns
    NOT_REAL_BENCHMARK / INSUFFICIENT_EVIDENCE / INCOMPLETE / HOLD instead of
    fabricating a winner without complete trusted real samples.
    """
    from .sidecars import CreativeSidecarStore

    sidecars = store if isinstance(store, CreativeSidecarStore) else CreativeSidecarStore(store)
    loaded_cases = tuple(sidecars.load_benchmark_case_by_digest(c.digest) for c in cases)
    if loaded_cases != tuple(cases):
        raise CreativeValidationError("comparison cases disagree with stored cohort records")
    loaded_obs = tuple(sidecars.load_benchmark_observation_by_digest(o.digest) for o in observations)
    if loaded_obs != tuple(observations):
        raise CreativeValidationError("comparison observations disagree with stored observation records")
    comp = _build_benchmark_comparison_payload(
        comparison_id=comparison_id,
        cases=loaded_cases,
        observations=loaded_obs,
        strategies=strategies,
        provenance=provenance,
        created_at=created_at,
    )
    sidecars.save_benchmark_comparison(comp)
    return sidecars.load_benchmark_comparison_by_digest(comp.digest)


# R5b-1 uses the existing V2 stage claim and R5a records.
_TRIAL_ROLE_INSTRUCTIONS = {
    "EXPLORE": "Generate two distinct concepts for the exact brief. Preserve its product, CTA and audio constraints.",
    "CRITIQUE": "Independently assess the exact generated concept against the brief and any retrieved evidence.",
    "DEVELOP": "Propose bounded creative text edits for the exact concept using the independent critique and "
               "retrieved source bytes. Preserve the brief's product, CTA and audio constraints.",
}
_EXPERIMENT_OPERATIONS = {
    "S1": ("EXPLORE",),
    "S2": ("EXPLORE", "CRITIQUE"),
    "S3": ("RESEARCH", "EXPLORE", "CRITIQUE", "DEVELOP"),
}


@dataclass(frozen=True, slots=True)
class BenchmarkTrialBudget:
    """Per-sample ceilings for scheduling and checking observed attempts."""

    max_attempts: int
    max_invocation_wall_time_ms: int
    max_tokens: int
    max_cost_amount: float
    currency: str

    def __post_init__(self) -> None:
        for name in ("max_attempts", "max_invocation_wall_time_ms", "max_tokens"):
            positive_int(getattr(self, name), name)
        if self.max_attempts > 16:
            raise CreativeValidationError("at most 16 attempts per experiment sample")
        if (type(self.max_cost_amount) not in (float, int)
                or not 0 < self.max_cost_amount <= 1_000_000):
            raise CreativeValidationError("expected positive finite experimental cost ceiling")
        require_text(self.currency, "budget currency")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkTrialBudget:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class BenchmarkTrialSample:
    sample_id: str
    case_digest: str
    strategy: Literal["S1", "S2", "S3"]
    repetition: int

    @property
    def operations(self) -> tuple[str, ...]:
        return _EXPERIMENT_OPERATIONS[self.strategy]


@dataclass(frozen=True, slots=True)
class BenchmarkResearchTarget:
    """Reviewed source identity and expected raw bytes for one case."""

    case_digest: str
    source_ref: str
    content_sha256: str
    origin_ref: str

    def __post_init__(self) -> None:
        require_digest(self.case_digest)
        require_digest(self.content_sha256)
        require_text(self.source_ref, "research source_ref")
        require_text(self.origin_ref, "research origin_ref")
        if "UNKNOWN" in (self.source_ref, self.origin_ref):
            raise CreativeValidationError("research source requires a known reference and origin")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkResearchTarget:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class A03BenchmarkExperimentPlan:
    plan_id: str
    cohort_id: str
    source_sha: str
    case_digests: tuple[str, ...]
    repetitions: int
    configuration: ModelConfiguration
    budget: BenchmarkTrialBudget
    purpose: Literal["TEST_ONLY_MOCK", "PRODUCTION"]
    provenance: CreativeJobProvenance
    created_at: str
    rubric_version: str = BENCHMARK_RUBRIC_VERSION
    schema_version: str = "A03BenchmarkExperimentPlan.v1"
    role_configurations: tuple[ModelConfiguration, ...] = ()
    research_targets: tuple[BenchmarkResearchTarget, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version not in ("A03BenchmarkExperimentPlan.v1", "A03BenchmarkExperimentPlan.v2"):
            raise CreativeValidationError("unsupported benchmark experiment schema")
        require_text(self.plan_id, "plan_id")
        require_text(self.cohort_id, "cohort_id")
        _require_source_sha(self.source_sha)
        _require_iso_timestamp(self.created_at, "created_at")
        if self.rubric_version != BENCHMARK_RUBRIC_VERSION:
            raise CreativeValidationError("unsupported benchmark rubric version")
        cases = _distinct_digests(self.case_digests, "case_digests")
        if len(cases) != len(BENCHMARK_BRIEF_CLASSES):
            raise CreativeValidationError("experiment requires one case per benchmark brief class")
        object.__setattr__(self, "case_digests", cases)
        positive_int(self.repetitions, "repetitions")
        if self.repetitions > 16:
            raise CreativeValidationError("at most 16 repetitions per case and strategy")
        if self.purpose not in ("TEST_ONLY_MOCK", "PRODUCTION"):
            raise CreativeValidationError("invalid experiment purpose")
        for item, expected in ((self.configuration, ModelConfiguration), (self.budget, BenchmarkTrialBudget),
                               (self.provenance, CreativeJobProvenance)):
            if not isinstance(item, expected):
                raise CreativeValidationError("invalid typed experiment plan context")
            expected.from_dict(item.to_dict())
        if "UNKNOWN" in (self.configuration.model, self.configuration.provider):
            raise CreativeValidationError("experiment requires an explicit requested configuration")
        if self.schema_version == "A03BenchmarkExperimentPlan.v1":
            if self.role_configurations or self.research_targets:
                raise CreativeValidationError("role configurations and research targets require plan v2")
            return
        if (not isinstance(self.role_configurations, tuple) or len(self.role_configurations) != 3
                or any(not isinstance(config, ModelConfiguration) or "UNKNOWN" in (config.provider, config.model)
                       for config in self.role_configurations)
                or self.configuration != self.role_configurations[0]):
            raise CreativeValidationError("plan v2 requires generator, critic and developer configurations")
        if (not isinstance(self.research_targets, tuple)
                or any(not isinstance(target, BenchmarkResearchTarget) or target.case_digest not in cases
                       for target in self.research_targets)):
            raise CreativeValidationError("expected plan-bound research targets")
        for case_digest in cases:
            targets = self.targets_for(case_digest)
            if (not 1 <= len(targets) <= 8
                    or len({target.source_ref for target in targets}) != len(targets)
                    or len({target.content_sha256 for target in targets}) != len(targets)):
                raise CreativeValidationError("case requires distinct research refs and bytes")

    def targets_for(self, case_digest: str) -> tuple[BenchmarkResearchTarget, ...]:
        return tuple(target for target in self.research_targets if target.case_digest == case_digest)

    def configuration_for(self, operation: str) -> ModelConfiguration:
        if not self.role_configurations:
            return self.configuration
        return self.role_configurations[tuple(_TRIAL_ROLE_INSTRUCTIONS).index(operation)]

    @classmethod
    def create(cls, cases: tuple[A03BenchmarkCase, ...], **options: Any) -> A03BenchmarkExperimentPlan:
        if not isinstance(cases, (tuple, list)) or any(not isinstance(case, A03BenchmarkCase) for case in cases):
            raise CreativeValidationError("expected typed benchmark cases")
        by_class = {case.brief_class: case for case in cases}
        if len(cases) != len(BENCHMARK_BRIEF_CLASSES) or set(by_class) != set(BENCHMARK_BRIEF_CLASSES):
            raise CreativeValidationError("experiment requires one case per benchmark brief class")
        ordered = tuple(by_class[name] for name in BENCHMARK_BRIEF_CLASSES)
        plan = cls(cohort_id=ordered[0].cohort_id, source_sha=ordered[0].source_sha,
                   case_digests=tuple(case.digest for case in ordered), **options)
        plan.validate_cases(ordered)
        return plan

    def validate_cases(self, cases: tuple[A03BenchmarkCase, ...]) -> None:
        if (tuple(case.digest for case in cases) != self.case_digests
                or tuple(case.brief_class for case in cases) != BENCHMARK_BRIEF_CLASSES
                or len({case.case_id for case in cases}) != len(cases)
                or len({case.source_brief_digest for case in cases}) != len(cases)
                or any((case.cohort_id, case.source_sha, case.rubric_version, case.language)
                       != (self.cohort_id, self.source_sha, self.rubric_version, "vi") for case in cases)):
            raise CreativeValidationError("experiment case, brief, cohort, source or rubric mismatch")
        if self.research_targets and any(
            not set(case.expected_evidence_refs) <= {target.source_ref for target in self.targets_for(case.digest)}
            for case in cases
        ):
            raise CreativeValidationError("research targets omit the case's expected source refs")

    @property
    def samples(self) -> tuple[BenchmarkTrialSample, ...]:
        digest = self.digest
        return tuple(
            BenchmarkTrialSample(f"{digest}:{case_index}:{repetition}:{strategy}", case, strategy, repetition)
            for case_index, case in enumerate(self.case_digests, start=1)
            for repetition in range(1, self.repetitions + 1)
            for strategy in BENCHMARK_STRATEGIES
        )

    def paired_samples(self, observed_sample_ids: tuple[str, ...]) -> tuple[BenchmarkTrialSample, ...]:
        """Keep matching repetitions across all strategies and equal counts across cases."""
        observed = set(_distinct_texts(observed_sample_ids, "observed_sample_ids"))
        samples = self.samples
        if observed - {sample.sample_id for sample in samples}:
            raise CreativeValidationError("unplanned experiment sample")
        paired: dict[str, list[BenchmarkTrialSample]] = {case: [] for case in self.case_digests}
        width = len(BENCHMARK_STRATEGIES)
        for offset in range(0, len(samples), width):
            group = samples[offset:offset + width]
            if all(sample.sample_id in observed for sample in group):
                paired[group[0].case_digest].extend(group)
        count = min(map(len, paired.values()))
        return tuple(sample for group in paired.values() for sample in group[:count])

    def to_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "cohort_id": self.cohort_id, "source_sha": self.source_sha,
                "case_digests": list(self.case_digests), "repetitions": self.repetitions,
                "configuration": self.configuration.to_dict(), "budget": self.budget.to_dict(),
                "purpose": self.purpose, "provenance": self.provenance.to_dict(), "created_at": self.created_at,
                "rubric_version": self.rubric_version, "schema_version": self.schema_version,
                **({"role_configurations": [config.to_dict() for config in self.role_configurations],
                    "research_targets": [target.to_dict() for target in self.research_targets]}
                   if self.schema_version == "A03BenchmarkExperimentPlan.v2" else {})}

    def to_json(self) -> str:
        return canonical_bytes(self.to_dict()).decode("utf-8")

    @property
    def digest(self) -> str:
        return sha256_bytes(self.to_json().encode("utf-8"))

    @property
    def slot(self) -> str:
        return sha256_bytes(canonical_bytes({"cohort_id": self.cohort_id, "plan_id": self.plan_id}))

    @classmethod
    def from_dict(cls, data: object) -> A03BenchmarkExperimentPlan:
        role_fields = {"role_configurations", "research_targets"}
        value = exact_fields(data, {f.name for f in fields(cls)} - role_fields, role_fields)
        if not isinstance(value["case_digests"], list):
            raise CreativeValidationError("expected case_digests array")
        value["configuration"] = ModelConfiguration.from_dict(value["configuration"])
        value["budget"] = BenchmarkTrialBudget.from_dict(value["budget"])
        value["provenance"] = CreativeJobProvenance.from_dict(value["provenance"])
        extended = value["schema_version"] == "A03BenchmarkExperimentPlan.v2"
        if (value.keys() & role_fields) != (role_fields if extended else set()):
            raise CreativeValidationError("unexpected experiment role configuration fields")
        if extended:
            if any(not isinstance(value[name], list) for name in role_fields):
                raise CreativeValidationError("expected experiment role and research arrays")
            value["role_configurations"] = tuple(ModelConfiguration.from_dict(config)
                                                 for config in value["role_configurations"])
            value["research_targets"] = tuple(BenchmarkResearchTarget.from_dict(target)
                                              for target in value["research_targets"])
        return cls(**value)

    @classmethod
    def from_json(cls, text: str) -> A03BenchmarkExperimentPlan:
        return cls.from_dict(read_json(text))


@dataclass(frozen=True, slots=True)
class BenchmarkTrialGrant:
    """Runner-side grant for one offline fixture sample."""

    plan_digest: str
    sample_id: str
    authorization_ref: str

    def __post_init__(self) -> None:
        require_digest(self.plan_digest)
        require_text(self.sample_id, "sample_id")
        require_text(self.authorization_ref, "authorization_ref")


@dataclass(frozen=True, slots=True)
class BenchmarkLiveTrialGrant:
    """Runner authorization input for one live sample, separate from fixture grants."""

    plan_digest: str
    sample_id: str
    provider: str
    account_ref: str
    authorization_ref: str
    not_before: str
    expires_at: str
    budget: BenchmarkTrialBudget
    scope: Literal["A03_BENCHMARK_REASONING"] = "A03_BENCHMARK_REASONING"
    schema_version: str = "A03BenchmarkLiveTrialGrant.v1"

    def __post_init__(self) -> None:
        require_digest(self.plan_digest)
        for name in ("sample_id", "provider", "account_ref", "authorization_ref"):
            require_text(getattr(self, name), name)
        start = _require_iso_timestamp(self.not_before, "not_before")
        end = _require_iso_timestamp(self.expires_at, "expires_at")
        if end <= start or "UNKNOWN" in (self.account_ref, self.provider):
            raise CreativeValidationError("invalid live grant window or identity")
        if (self.schema_version != "A03BenchmarkLiveTrialGrant.v1" or self.scope != "A03_BENCHMARK_REASONING"
                or not isinstance(self.budget, BenchmarkTrialBudget)):
            raise CreativeValidationError("invalid live benchmark grant scope or budget")

    def to_dict(self) -> dict[str, Any]:
        return {**{f.name: getattr(self, f.name) for f in fields(self)}, "budget": self.budget.to_dict()}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkLiveTrialGrant:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["budget"] = BenchmarkTrialBudget.from_dict(value["budget"])
        return cls(**value)

    def permits(self, plan: A03BenchmarkExperimentPlan, sample: BenchmarkTrialSample, now: datetime) -> bool:
        budget, limit = self.budget, plan.budget
        return (plan.purpose == "PRODUCTION" and (self.plan_digest, self.sample_id) == (plan.digest, sample.sample_id)
                and self.provider == plan.configuration.provider
                and all(plan.configuration_for(op).provider == self.provider
                        for op in sample.operations if op != "RESEARCH")
                and _require_iso_timestamp(self.not_before, "not_before") <= now
                < _require_iso_timestamp(self.expires_at, "expires_at")
                and budget.currency == limit.currency and budget.max_attempts <= limit.max_attempts
                and budget.max_invocation_wall_time_ms <= limit.max_invocation_wall_time_ms
                and budget.max_tokens <= limit.max_tokens and budget.max_cost_amount <= limit.max_cost_amount)


@dataclass(frozen=True, slots=True)
class BenchmarkAttemptTiming:
    receipt_digest: str
    authorization_ref: str
    started_at: str
    completed_at: str
    wall_time_ms: int
    schema_version: str = "A03BenchmarkAttemptTiming.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "A03BenchmarkAttemptTiming.v1":
            raise CreativeValidationError("unsupported benchmark attempt timing schema")
        require_digest(self.receipt_digest)
        require_text(self.authorization_ref, "authorization_ref")
        start = _require_iso_timestamp(self.started_at, "started_at")
        end = _require_iso_timestamp(self.completed_at, "completed_at")
        if end < start:
            raise CreativeValidationError("benchmark attempt clock moved backwards")
        _nonneg_int(self.wall_time_ms, "wall_time_ms")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> BenchmarkAttemptTiming:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class BenchmarkTrialAttempt:
    stage: A03CreativeStageResult
    timing: BenchmarkAttemptTiming | None

    @property
    def trace(self) -> BenchmarkRoleTrace:
        job = self.stage.job
        trace = _stage_to_trace(
            self.stage, step_name=job.job_id, is_retry=job.attempt > 1,
            step_timings_ms={job.job_id: self.timing.wall_time_ms} if self.timing else None,
            step_retries={job.job_id: 0},
        )
        return replace(
            trace, started_at=self.timing.started_at if self.timing else None,
            completed_at=self.timing.completed_at if self.timing else None,
            execution_status="UNKNOWN" if self.stage.reasoning.error_code == "UNKNOWN_EFFECT"
            else trace.execution_status,
        )


@dataclass(frozen=True, slots=True)
class BenchmarkTrialResult:
    sample: BenchmarkTrialSample
    status: Literal[
        "READY", "RETRY_REQUIRED", "INCOMPLETE", "HOLD_AUTHORIZATION", "HOLD_DEPENDENCY",
        "HOLD_BUDGET", "HOLD_UNKNOWN_EFFECT", "HOLD_RECEIPT", "HOLD_SOURCE",
    ]
    reason: str
    attempts: tuple[BenchmarkTrialAttempt, ...] = ()
    next_job: A03CreativeJob | None = None
    observation: A03BenchmarkObservation | None = None
    ranking_status: Literal["UNRANKED"] = "UNRANKED"
    next_research: BenchmarkResearchRequest | None = None
    research: BenchmarkResearchReceipt | None = None

    @property
    def telemetry(self) -> BenchmarkTelemetry:
        traces = tuple(attempt.trace for attempt in self.attempts)
        if self.research is not None and self.research.error_code is None:
            traces = (self.research.trace,) + traces
        return aggregate_benchmark_telemetry(traces)


def _trial_budget_hold(budget: BenchmarkTrialBudget, attempts: list[BenchmarkTrialAttempt], *,
                       scheduling: bool, research: BenchmarkResearchReceipt | None = None,
                       require_cost_source: bool = False) -> str | None:
    tokens = 0
    wall_ms = 0 if research is None else research.wall_time_ms
    cost = Decimal(0)
    for attempt in attempts:
        usage = attempt.stage.reasoning.usage
        if (attempt.timing is None or usage.input_tokens is None or usage.output_tokens is None
                or usage.cost_amount is None or usage.currency != budget.currency
                or (require_cost_source and usage.cost_source_ref in (None, "UNKNOWN"))):
            return "UNOBSERVED_OR_INCOMPARABLE_BUDGET_USAGE"
        tokens += usage.input_tokens + usage.output_tokens
        wall_ms += attempt.timing.wall_time_ms
        cost += Decimal(str(usage.cost_amount))
    limits = ((len(attempts) + int(research is not None), budget.max_attempts), (tokens, budget.max_tokens),
              (wall_ms, budget.max_invocation_wall_time_ms), (cost, Decimal(str(budget.max_cost_amount))))
    if any(used > limit or (scheduling and used == limit) for used, limit in limits):
        return "EXPERIMENT_BUDGET_EXHAUSTED"
    return None


def _benchmark_research_request(plan: A03BenchmarkExperimentPlan, sample: BenchmarkTrialSample,
                                case: A03BenchmarkCase) -> BenchmarkResearchRequest:
    from .live import BenchmarkResearchRequest

    return BenchmarkResearchRequest(plan.digest, sample.sample_id, case.digest, case.source_brief_digest,
                                    plan.targets_for(case.digest), plan.budget.currency)


def _role_trial_observation(
    plan: A03BenchmarkExperimentPlan, sample: BenchmarkTrialSample, case: A03BenchmarkCase, brief: dict[str, Any],
    attempts: list[BenchmarkTrialAttempt], research: BenchmarkResearchReceipt | None,
) -> A03BenchmarkObservation:
    model_traces = tuple(replace(attempt.trace, step_name=f"{attempt.stage.job.job_id}:{attempt.stage.job.attempt}")
                         for attempt in attempts)
    successful = {trace.role: trace.receipt_digest for trace in model_traces if trace.execution_status == "OK"}
    research_trace = research.trace if research is not None else None
    traces = (research_trace,) + model_traces if research_trace is not None else model_traces
    consumer, telemetry = BenchmarkConsumerEvidence(), aggregate_benchmark_telemetry(traces)
    structural = evaluate_benchmark_structure(case, brief, stages=tuple(attempt.stage for attempt in attempts))
    nsm = _evaluate_observation_nsm(
        status="TEST_ONLY_MOCK", execution_mode="TEST_ONLY_MOCK", provenance=plan.provenance,
        structural_check=structural, semantic_status="UNMEASURED", judge_receipt=None,
        telemetry=telemetry, consumer_evidence=consumer, hold_reasons=(), errors=(),
    )
    return A03BenchmarkObservation(
        sample_id=sample.sample_id, attempt=1, cohort_id=case.cohort_id, case_id=case.case_id,
        case_digest=case.digest, source_sha=case.source_sha, brief_id=case.brief_id,
        source_brief_digest=case.source_brief_digest, brief_class=case.brief_class, strategy=sample.strategy,
        requested_configuration=plan.configuration, observed_provider=model_traces[0].observed,
        observed_effort=model_traces[0].observed_effort,
        job_digests=tuple(trace.job_digest for trace in model_traces),
        stage_result_digests=tuple(trace.stage_digest for trace in model_traces),
        explore_receipt_digest=successful["GENERATOR"],
        critic_receipt_digests=(successful["CRITIC"],) if "CRITIC" in successful else (),
        develop_receipt_digest=successful.get("DEVELOPER"), consumer_evidence=consumer,
        provenance=plan.provenance, research_status="VERIFIED_SOURCE_BYTES" if research_trace else "UNKNOWN",
        evidence_refs=research_trace.evidence_refs if research_trace else (),
        evidence_digests=research_trace.evidence_digests if research_trace else (),
        role_traces=traces, structural_check=structural, semantic_status="UNMEASURED", telemetry=telemetry, nsm=nsm,
        started_at=traces[0].started_at, completed_at=traces[-1].completed_at,
        execution_mode="TEST_ONLY_MOCK", status="TEST_ONLY_MOCK", benchmark_validity="NOT_REAL_BENCHMARK",
        schema_version="A03BenchmarkObservation.v2", experiment_plan_digest=plan.digest,
    )


def _reconcile_benchmark_sample(
    plan: A03BenchmarkExperimentPlan, sample: BenchmarkTrialSample, sidecars: CreativeSidecarStore,
    *, persist_observation: bool = True,
) -> BenchmarkTrialResult:
    if plan.purpose == "PRODUCTION":
        return BenchmarkTrialResult(sample, "HOLD_DEPENDENCY", "NO_CERTIFIED_LIVE_TRIAL_TRANSPORT")
    extended = bool(plan.role_configurations)
    if sample.strategy == "S3" and not extended:
        return BenchmarkTrialResult(sample, "HOLD_DEPENDENCY", "S3_REQUIRES_PLAN_V2_SOURCE_TARGETS")
    case = sidecars.load_benchmark_case_by_digest(sample.case_digest)
    brief = sidecars.creative.load_brief(case.source_brief_digest)
    attempts: list[BenchmarkTrialAttempt] = []
    successful: list[A03CreativeStageResult] = []
    prior: list[A03CreativeStageResult] = []
    receipts: set[str] = set()
    provider_ids: set[str] = set()
    research = None
    last_completed_at = None
    authorization_ref = None

    def result(status: str, reason: str, **options: Any) -> BenchmarkTrialResult:
        return BenchmarkTrialResult(sample, status, reason, tuple(attempts), research=research, **options)

    try:
        for operation in sample.operations:
            if operation == "RESEARCH":
                request = _benchmark_research_request(plan, sample, case)
                claimed, research = sidecars._load_benchmark_research(request)
                if not claimed:
                    return result("READY", "RESEARCH_READY", next_research=request)
                if research is None:
                    return result("HOLD_UNKNOWN_EFFECT", "RESERVED_RESEARCH_WITHOUT_TERMINAL_RECEIPT")
                if research.error_code is not None:
                    return result("HOLD_RECEIPT", research.error_code)
                provider_ids.add(research.invocation_id)
                receipts.add(research.digest)
                last_completed_at, authorization_ref = research.completed_at, research.authorization_ref
                continue

            generator = None if operation == "EXPLORE" else successful[0].reasoning
            source = generator.concepts()[0] if generator is not None else None
            configuration = plan.configuration_for(operation)
            critic = None
            if operation == "DEVELOP":
                critique = successful[1].reasoning
                critic = CreativeCriticEvidence(_output_pin(source), critique.digest, critique.request.digest,
                                                critique.critique)
            for number in range(1, plan.budget.max_attempts + 2):
                options = {}
                if extended:
                    options = {
                        "schema_version": "a03.reasoning-request.v5", "instruction": _TRIAL_ROLE_INSTRUCTIONS[operation],
                        "benchmark": BenchmarkRoleContext(
                            plan.digest, sample.sample_id, number,
                            research_receipt_digest=research.digest if research is not None else None,
                            evidence=research.sources if research is not None else (),
                            generator_receipt_digest=generator.digest if generator is not None else None,
                            critic=critic,
                        ),
                    }
                request = ReasoningRequest.create(brief, operation=operation, source=source,
                                                  count=2 if operation == "EXPLORE" else None, **options)
                job = A03CreativeJob.create(
                    request, journey_id=f"benchmark:{plan.digest}", cycle_id=sample.sample_id,
                    job_id=_role_for_operation(operation), attempt=number,
                    correlation_id=f"{sample.sample_id}:{operation}:{number}", provenance=plan.provenance,
                    configuration=configuration, purpose="TEST_ONLY_MOCK",
                )
                claimed, attempt = sidecars._load_experiment_attempt(job)
                if not claimed:
                    hold = _trial_budget_hold(plan.budget, attempts, scheduling=True, research=research)
                    if hold:
                        return result("HOLD_BUDGET", hold)
                    if number > 1:
                        return result("RETRY_REQUIRED", "EXPLICIT_RETRY_REQUIRED", next_job=job)
                    return result("READY", "ROLE_READY", next_job=job)
                if attempt is None:
                    return result("HOLD_UNKNOWN_EFFECT", "RESERVED_ATTEMPT_WITHOUT_TERMINAL_RECEIPT")
                attempts.append(attempt)
                stage = attempt.stage
                observed = stage.reasoning
                if (observed.provider_request_id is None or observed.observation.mode == "UNKNOWN"
                        or observed.error_code == "UNKNOWN_EFFECT"
                        or (observed.execution is not None and observed.execution.effect == "UNKNOWN")):
                    return result("HOLD_UNKNOWN_EFFECT", "UNRESOLVED_PROVIDER_EFFECT")
                if observed.digest in receipts or observed.provider_request_id in provider_ids:
                    return result("HOLD_RECEIPT", "REPLAYED_PROVIDER_RECEIPT")
                receipts.add(observed.digest)
                provider_ids.add(observed.provider_request_id)
                if (observed.configuration != configuration or observed.observation.mode != "TEST_ONLY_MOCK"
                        or observed.observation.model != "UNKNOWN"
                        or (not extended and (
                            observed.observation.provider != "deterministic-test-only"
                            or (configuration.effort is not None and attempt.trace.observed_effort != configuration.effort)
                        ))):
                    return result("HOLD_RECEIPT", "REQUESTED_AND_OBSERVED_CONFIGURATION_MISMATCH")
                hold = _trial_budget_hold(plan.budget, attempts, scheduling=False, research=research,
                                          require_cost_source=extended)
                if hold:
                    return result("HOLD_BUDGET", hold)
                if extended:
                    timing = attempt.timing
                    if ((authorization_ref is not None and timing.authorization_ref != authorization_ref)
                            or (last_completed_at is not None and _require_iso_timestamp(timing.started_at, "started_at")
                                < _require_iso_timestamp(last_completed_at, "completed_at"))):
                        return result("HOLD_RECEIPT", "ROLE_TIMING_OR_AUTHORIZATION_MISMATCH")
                    last_completed_at, authorization_ref = timing.completed_at, timing.authorization_ref
                if stage.status == "TEST_ONLY_MOCK":
                    successful.append(stage)
                    break
                if observed.state != "REFUSED" or observed.error_code != "MODEL_REFUSED":
                    return result("HOLD_RECEIPT", stage.failure or "ROLE_FAILED")
                prior.append(stage)

        if extended:
            observation = _role_trial_observation(plan, sample, case, brief, attempts, research)
            if persist_observation:
                sidecars._write_benchmark_observation(observation)
            return result("INCOMPLETE", "NOT_REAL_BENCHMARK", observation=observation)

        observation = observe_benchmark_run(
            case, sidecars, strategy=sample.strategy, sample_id=sample.sample_id, attempt=1,
            provenance=plan.provenance, started_at=attempts[0].timing.started_at,
            completed_at=attempts[-1].timing.completed_at, stages=tuple(successful), retry_stages=tuple(prior),
            step_timings_ms={a.stage.reasoning.digest: a.timing.wall_time_ms for a in attempts},
            # The stage owner invokes once per slot. Additional calls have their own receipts.
            step_retries={a.stage.reasoning.digest: 0 for a in attempts},
        )
        return result("INCOMPLETE", "NOT_REAL_BENCHMARK", observation=observation)
    except (CreativeValidationError, VersionConflict, FileNotFoundError) as exc:
        return result("HOLD_RECEIPT", str(exc))


def advance_benchmark_trial(
    plan: A03BenchmarkExperimentPlan, store: CreativeSidecarStore, *, source_sha: str, sample_id: str,
    grant: BenchmarkTrialGrant | None = None, reasoner: CreativeReasonerV2 | None = None,
    attempt_id: str | None = None, live_grant: BenchmarkLiveTrialGrant | None = None,
) -> BenchmarkTrialResult:
    """Reconcile and execute only the explicitly selected next job slot, if any."""
    from .live import InvocationAuthorization, LiveCreativeReasoner, LocalResearchFixtureProvider

    if store.load_benchmark_experiment_plan(plan.digest) != plan:
        raise CreativeValidationError("experiment plan disagrees with stored bytes")
    sample = next((item for item in plan.samples if item.sample_id == sample_id), None)
    if sample is None:
        raise CreativeValidationError("unplanned experiment sample")
    if attempt_id is not None:
        require_digest(attempt_id)
    if source_sha != plan.source_sha:
        return BenchmarkTrialResult(sample, "HOLD_SOURCE", "EXPERIMENT_SOURCE_MISMATCH")
    current = _reconcile_benchmark_sample(plan, sample, store)
    if plan.purpose == "PRODUCTION" and live_grant is not None:
        if (type(live_grant) is not BenchmarkLiveTrialGrant
                or not BenchmarkLiveTrialGrant.from_dict(live_grant.to_dict()).permits(
                    plan, sample, datetime.now(timezone.utc))):
            return replace(current, status="HOLD_AUTHORIZATION", reason="LIVE_GRANT_SCOPE_OR_WINDOW_MISMATCH")
        if type(reasoner) is not LiveCreativeReasoner:
            return current
        if reasoner.config != plan.configuration:
            return replace(current, reason="BENCHMARK_TRANSPORT_CONFIGURATION_MISMATCH")
        if sample.strategy == "S3":
            return replace(current, reason="NO_VERIFIED_EXTERNAL_RESEARCH_PROVIDER")
        status, reason = reasoner.benchmark_live_hold(live_grant)
        return replace(current, status=status, reason=reason)
    if current.next_job is None and current.next_research is None:
        return current
    if (type(grant) is not BenchmarkTrialGrant
            or (grant.plan_digest, grant.sample_id) != (plan.digest, sample_id)):
        return replace(current, status="HOLD_AUTHORIZATION", reason="BOUND_OFFLINE_GRANT_REQUIRED",
                       next_job=None, next_research=None)
    if current.next_research is not None:
        request = current.next_research
        if reasoner is None:
            return current
        provider = reasoner.research_provider if type(reasoner) is LiveCreativeReasoner else None
        if type(provider) is not LocalResearchFixtureProvider:
            return replace(current, status="HOLD_DEPENDENCY", reason="UNSUPPORTED_PROVIDER", next_research=None)
        if attempt_id != request.digest:
            return current
        try:
            if store._claim_benchmark_research(request, grant.authorization_ref, provider.custody_ref):
                receipt = LiveCreativeReasoner(research_provider=provider).research(request, grant.authorization_ref)
                store._save_benchmark_research(receipt)
        except (CreativeValidationError, VersionConflict, FileNotFoundError):
            observed = _reconcile_benchmark_sample(plan, sample, store)
            if observed.next_research is not None:
                return replace(observed, status="HOLD_RECEIPT", reason="RESEARCH_COULD_NOT_BE_PERSISTED",
                               next_research=None)
            return observed
        return _reconcile_benchmark_sample(plan, sample, store)
    if reasoner is None or attempt_id != current.next_job.slot:
        return current
    if plan.role_configurations and (current.research is not None or current.attempts):
        previous_ref = (current.attempts[-1].timing.authorization_ref if current.attempts
                        else current.research.authorization_ref)
        if previous_ref != grant.authorization_ref:
            return replace(current, status="HOLD_AUTHORIZATION", reason="OFFLINE_GRANT_REBOUND", next_job=None)
    client = reasoner.client if type(reasoner) is LiveCreativeReasoner else None
    capabilities = getattr(client, "capabilities", None)
    configuration = current.next_job.configuration
    if (getattr(client, "execution_mode", None) != "TEST_ONLY_MOCK"
            or reasoner.config != configuration
            or getattr(capabilities, "network_required", None) is not False
            or getattr(capabilities, "external_spend_possible", None) is not False):
        return replace(current, status="HOLD_DEPENDENCY", reason="CONFIGURED_OFFLINE_MOCK_CLIENT_REQUIRED", next_job=None)
    try:
        _run_creative_stage(
            current.next_job, store, LiveCreativeReasoner(configuration, client),
            authorization=InvocationAuthorization(authorization_ref=grant.authorization_ref),
            record_timing=store._save_experiment_attempt_timing,
        )
    except (CreativeValidationError, VersionConflict, FileNotFoundError):
        # An existing claim may have been created by a concurrent caller. Only
        # the immutable receipts decide whether it is safe to advance.
        observed = _reconcile_benchmark_sample(plan, sample, store)
        if observed.next_job == current.next_job:
            return replace(observed, status="HOLD_RECEIPT", reason="ATTEMPT_COULD_NOT_BE_PERSISTED", next_job=None)
        return observed
    return _reconcile_benchmark_sample(plan, sample, store)


@dataclass(frozen=True, slots=True)
class BenchmarkExperimentComparison:
    status: Literal["HOLD", "INCOMPLETE", "NOT_REAL_BENCHMARK"]
    reasons: tuple[str, ...]
    missing_sample_ids: tuple[str, ...]
    comparison: A03BenchmarkComparison | None = None
    ranking_status: Literal["UNRANKED"] = "UNRANKED"


def compare_benchmark_trials(
    plan: A03BenchmarkExperimentPlan, store: CreativeSidecarStore, *, source_sha: str, created_at: str,
) -> BenchmarkExperimentComparison:
    """Compare only plan-bound, equally weighted and receipt-verified repetitions."""
    if store.load_benchmark_experiment_plan(plan.digest) != plan:
        raise CreativeValidationError("experiment plan disagrees with stored bytes")
    _require_iso_timestamp(created_at, "created_at")
    cases = tuple(store.load_benchmark_case_by_digest(digest) for digest in plan.case_digests)
    samples = plan.samples
    if source_sha != plan.source_sha:
        return BenchmarkExperimentComparison("HOLD", ("EXPERIMENT_SOURCE_MISMATCH",),
                                             tuple(sample.sample_id for sample in samples))
    observations: dict[str, A03BenchmarkObservation] = {}
    reasons: set[str] = set()
    used: set[tuple[str, str]] = set()
    replayed = False
    invalid = False
    for sample in samples:
        trial = _reconcile_benchmark_sample(plan, sample, store)
        if trial.observation is not None:
            observations[sample.sample_id] = trial.observation
        else:
            reasons.add(trial.status)
            invalid |= trial.status in ("HOLD_RECEIPT", "HOLD_BUDGET", "HOLD_UNKNOWN_EFFECT")
        identities = []
        if trial.research is not None:
            identities.extend((("receipt", trial.research.digest), ("provider", trial.research.invocation_id)))
        for attempt in trial.attempts:
            observed = attempt.stage.reasoning
            identities.extend((("receipt", observed.digest), ("provider", observed.provider_request_id)))
            if observed.execution is not None:
                execution = observed.execution
                identities.extend((("process", execution.cli_thread_id), ("session", execution.session_sha256)))
        for kind, identity in identities:
            if identity is not None:
                replayed |= (kind, identity) in used
                used.add((kind, identity))
    missing = tuple(sample.sample_id for sample in samples if sample.sample_id not in observations)
    if replayed or invalid:
        if replayed:
            reasons.add("REPLAYED_EXPERIMENT_RECEIPT")
        return BenchmarkExperimentComparison("HOLD", tuple(sorted(reasons)), missing)
    paired = plan.paired_samples(tuple(observations))
    comparison = _build_benchmark_comparison_payload(
        comparison_id=f"experiment:{plan.digest}", cases=cases,
        observations=tuple(observations[sample.sample_id] for sample in paired),
        strategies=BENCHMARK_STRATEGIES, provenance=plan.provenance, created_at=created_at,
    )
    status = "NOT_REAL_BENCHMARK" if observations else "INCOMPLETE"
    return BenchmarkExperimentComparison(status, tuple(sorted(reasons)), missing, comparison)
