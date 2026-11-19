"""Validated immutable concept records; no field can claim verified facts."""

from __future__ import annotations

import math
import re
import unicodedata
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


# Opt-in R3 production package value objects; V1 ConceptRecord/ConceptSelection bytes stay intact.
EDITABLE_PACKAGE_FIELDS = frozenset({
    "narrative_intention", "visual_style", "lighting_direction", "identities",
    "physics_invariants", "temporal_invariants", "creative_freedoms", "scenes",
    "script", "transitions", "audio", "media_targets", "qa_expectations", "attributions",
})


def require_package_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("package:"):
        raise CreativeValidationError("invalid package_id")
    require_digest(value[8:])
    return value


def _require_decision_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("decision:"):
        raise CreativeValidationError("invalid decision_id")
    require_digest(value[9:])
    return value


def _positive_number(value: object, field: str) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise CreativeValidationError(f"{field}: expected a positive finite number")
    return value


def _optional_text(value: object, field: str) -> str | None:
    return None if value is None else require_text(value, field)


def _distinct_texts(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    items = text_tuple(value, field)
    if required and not items:
        raise CreativeValidationError(f"{field}: expected at least one item")
    if len(set(items)) != len(items):
        raise CreativeValidationError(f"{field}: duplicate values")
    return items


@dataclass(frozen=True, slots=True)
class CameraSpec:
    framing: str
    lens: str
    movement: str

    def __post_init__(self) -> None:
        for name in ("framing", "lens", "movement"):
            require_text(getattr(self, name), f"camera.{name}")

    def to_dict(self) -> dict[str, str]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> CameraSpec:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class IdentityContinuity:
    identity_id: str
    kind: Literal["CHARACTER", "OBJECT", "ENVIRONMENT", "ABSTRACT"]
    description: str
    continuity_invariants: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text(self.identity_id, "identity.identity_id")
        if self.kind not in ("CHARACTER", "OBJECT", "ENVIRONMENT", "ABSTRACT"):
            raise CreativeValidationError("invalid identity continuity kind")
        require_text(self.description, "identity.description")
        object.__setattr__(self, "continuity_invariants",
                           _distinct_texts(self.continuity_invariants, "identity.continuity_invariants", required=True))

    def to_dict(self) -> dict[str, Any]:
        return {"identity_id": self.identity_id, "kind": self.kind, "description": self.description,
                "continuity_invariants": list(self.continuity_invariants)}

    @classmethod
    def from_dict(cls, data: object) -> IdentityContinuity:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if not isinstance(value["continuity_invariants"], list):
            raise CreativeValidationError("expected continuity_invariants array")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ProductionScene:
    scene_id: str
    order: int
    duration_seconds: int | float
    state_s0: str
    action: str
    state_s1: str
    camera: CameraSpec
    lighting: str
    visual_style: str
    speech_mode: Literal["SPOKEN", "SILENCE"]
    identity_refs: tuple[str, ...] = ()
    sound_cue: str | None = None

    def __post_init__(self) -> None:
        require_text(self.scene_id, "scene.scene_id")
        positive_int(self.order, "scene.order")
        _positive_number(self.duration_seconds, "scene.duration_seconds")
        for name in ("state_s0", "action", "state_s1", "lighting", "visual_style"):
            require_text(getattr(self, name), f"scene.{name}")
        if (unicodedata.normalize("NFKC", self.state_s0).strip().casefold()
                == unicodedata.normalize("NFKC", self.state_s1).strip().casefold()):
            raise CreativeValidationError("causal scene requires distinct state_s0 and state_s1")
        if not isinstance(self.camera, CameraSpec):
            raise CreativeValidationError("expected typed CameraSpec")
        CameraSpec.from_dict(self.camera.to_dict())
        if self.speech_mode not in ("SPOKEN", "SILENCE"):
            raise CreativeValidationError("scene.speech_mode must be SPOKEN or SILENCE")
        object.__setattr__(self, "identity_refs", _distinct_texts(self.identity_refs, "scene.identity_refs"))
        _optional_text(self.sound_cue, "scene.sound_cue")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id, "order": self.order, "duration_seconds": self.duration_seconds,
            "state_s0": self.state_s0, "action": self.action, "state_s1": self.state_s1,
            "camera": self.camera.to_dict(), "lighting": self.lighting, "visual_style": self.visual_style,
            "speech_mode": self.speech_mode, "identity_refs": list(self.identity_refs),
            "sound_cue": self.sound_cue,
        }

    @classmethod
    def from_dict(cls, data: object) -> ProductionScene:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["camera"] = CameraSpec.from_dict(value["camera"])
        if not isinstance(value["identity_refs"], list):
            raise CreativeValidationError("expected scene identity_refs array")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ScriptSegment:
    segment_id: str
    scene_id: str
    speech_mode: Literal["SPOKEN", "SILENCE"]
    spoken_text: str | None = None
    language: str | None = None
    delivery_notes: str | None = None

    def __post_init__(self) -> None:
        require_text(self.segment_id, "script.segment_id")
        require_text(self.scene_id, "script.scene_id")
        if self.speech_mode not in ("SPOKEN", "SILENCE"):
            raise CreativeValidationError("script.speech_mode must be SPOKEN or SILENCE")
        if self.speech_mode == "SILENCE":
            if self.spoken_text is not None or self.language is not None:
                raise CreativeValidationError("explicit SILENCE segment cannot carry spoken text or language")
        else:
            require_text(self.spoken_text, "script.spoken_text")
            require_text(self.language, "script.language")
        _optional_text(self.delivery_notes, "script.delivery_notes")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> ScriptSegment:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class SceneTransition:
    from_scene_id: str
    to_scene_id: str
    kind: str
    notes: str | None = None

    def __post_init__(self) -> None:
        require_text(self.from_scene_id, "transition.from_scene_id")
        require_text(self.to_scene_id, "transition.to_scene_id")
        if self.from_scene_id == self.to_scene_id:
            raise CreativeValidationError("scene transition cannot target the same scene")
        require_text(self.kind, "transition.kind")
        _optional_text(self.notes, "transition.notes")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> SceneTransition:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class AudioPlan:
    mode: Literal["SILENCE", "SOUND_DESIGN", "VOICEOVER", "MUSIC", "MIXED"]
    sound_design: str | None = None
    voiceover: str | None = None
    music: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("SILENCE", "SOUND_DESIGN", "VOICEOVER", "MUSIC", "MIXED"):
            raise CreativeValidationError("audio.mode must be SILENCE, SOUND_DESIGN, VOICEOVER, MUSIC or MIXED")
        for name in ("sound_design", "voiceover", "music"):
            _optional_text(getattr(self, name), f"audio.{name}")
        if self.mode == "SILENCE":
            if any(getattr(self, name) is not None for name in ("sound_design", "voiceover", "music")):
                raise CreativeValidationError("explicit SILENCE audio plan cannot include sound, VO or music")
        elif self.mode == "SOUND_DESIGN" and self.sound_design is None:
            raise CreativeValidationError("SOUND_DESIGN audio plan requires sound_design")
        elif self.mode == "VOICEOVER" and self.voiceover is None:
            raise CreativeValidationError("VOICEOVER audio plan requires voiceover")
        elif self.mode == "MUSIC" and self.music is None:
            raise CreativeValidationError("MUSIC audio plan requires music")
        elif self.mode == "MIXED" and not any(
            getattr(self, name) is not None for name in ("sound_design", "voiceover", "music")
        ):
            raise CreativeValidationError("MIXED audio plan requires at least one audio element")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> AudioPlan:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class MediaOutputTarget:
    target_id: str
    media_kind: Literal["image", "video", "audio", "storyboard_frame"]
    aspect_ratio: str
    format_hint: str
    scene_refs: tuple[str, ...]
    delivery_note: str | None = None
    generation_status: Literal["NOT_GENERATED"] = "NOT_GENERATED"

    def __post_init__(self) -> None:
        require_text(self.target_id, "media_target.target_id")
        if self.media_kind not in ("image", "video", "audio", "storyboard_frame"):
            raise CreativeValidationError("invalid media_target.media_kind")
        require_text(self.aspect_ratio, "media_target.aspect_ratio")
        require_text(self.format_hint, "media_target.format_hint")
        object.__setattr__(self, "scene_refs", _distinct_texts(self.scene_refs, "media_target.scene_refs", required=True))
        _optional_text(self.delivery_note, "media_target.delivery_note")
        if self.generation_status != "NOT_GENERATED":
            raise CreativeValidationError("production package cannot claim premature media generation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id, "media_kind": self.media_kind, "aspect_ratio": self.aspect_ratio,
            "format_hint": self.format_hint, "scene_refs": list(self.scene_refs),
            "delivery_note": self.delivery_note, "generation_status": self.generation_status,
        }

    @classmethod
    def from_dict(cls, data: object) -> MediaOutputTarget:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if not isinstance(value["scene_refs"], list):
            raise CreativeValidationError("expected media_target.scene_refs array")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class QAExpectation:
    check_id: str
    target: str
    criterion: str
    verification_method: str
    claim_authority: Literal["GENERATED_CLAIM", "HYPOTHESIS"] = "GENERATED_CLAIM"
    verification_status: Literal["UNVERIFIED"] = "UNVERIFIED"

    def __post_init__(self) -> None:
        for name in ("check_id", "target", "criterion", "verification_method"):
            require_text(getattr(self, name), f"qa.{name}")
        if self.claim_authority not in ("GENERATED_CLAIM", "HYPOTHESIS"):
            raise CreativeValidationError("QA expectation must remain GENERATED_CLAIM or HYPOTHESIS")
        if self.verification_status != "UNVERIFIED":
            raise CreativeValidationError("production package cannot fabricate verified QA outcomes")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> QAExpectation:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class RightsAttribution:
    source_ref: str
    attribution: str
    rights_status: Literal["UNKNOWN", "EVIDENCE_BOUND"] = "UNKNOWN"
    evidence_sha256: str | None = None
    verification_ref: str | None = None

    def __post_init__(self) -> None:
        require_text(self.source_ref, "attribution.source_ref")
        require_text(self.attribution, "attribution.attribution")
        if self.rights_status not in ("UNKNOWN", "EVIDENCE_BOUND"):
            raise CreativeValidationError("rights_status must remain UNKNOWN unless bound to supplied evidence")
        if self.rights_status == "UNKNOWN":
            if self.evidence_sha256 is not None or self.verification_ref is not None:
                raise CreativeValidationError("UNKNOWN rights cannot attach evidence hashes or verification refs")
        else:
            require_digest(self.evidence_sha256)
            require_text(self.verification_ref, "attribution.verification_ref")
            if self.verification_ref == "UNKNOWN":
                raise CreativeValidationError("EVIDENCE_BOUND rights need an explicit verification_ref")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> RightsAttribution:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class ProductionPackageSpec:
    """Editable, strictly typed production spec; zero premature media or verified facts."""

    narrative_intention: str
    visual_style: str
    lighting_direction: str
    identities: tuple[IdentityContinuity, ...]
    physics_invariants: tuple[str, ...]
    temporal_invariants: tuple[str, ...]
    creative_freedoms: tuple[str, ...]
    scenes: tuple[ProductionScene, ...]
    script: tuple[ScriptSegment, ...]
    transitions: tuple[SceneTransition, ...]
    audio: AudioPlan
    media_targets: tuple[MediaOutputTarget, ...]
    qa_expectations: tuple[QAExpectation, ...]
    attributions: tuple[RightsAttribution, ...] = ()

    def __post_init__(self) -> None:
        for name in ("narrative_intention", "visual_style", "lighting_direction"):
            require_text(getattr(self, name), f"spec.{name}")
        for name in ("physics_invariants", "temporal_invariants", "creative_freedoms"):
            object.__setattr__(self, name, _distinct_texts(getattr(self, name), f"spec.{name}"))
        typed_lists = (
            ("identities", IdentityContinuity), ("scenes", ProductionScene),
            ("script", ScriptSegment), ("transitions", SceneTransition),
            ("media_targets", MediaOutputTarget), ("qa_expectations", QAExpectation),
            ("attributions", RightsAttribution),
        )
        for name, kind in typed_lists:
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or any(not isinstance(v, kind) for v in values):
                raise CreativeValidationError(f"spec.{name}: expected typed sequence")
            object.__setattr__(self, name, tuple(kind.from_dict(v.to_dict()) for v in values))
        if not isinstance(self.audio, AudioPlan):
            raise CreativeValidationError("spec.audio: expected AudioPlan")
        AudioPlan.from_dict(self.audio.to_dict())
        if not self.scenes:
            raise CreativeValidationError("production spec requires at least one scene")
        if not self.script:
            raise CreativeValidationError("production spec requires at least one script segment")
        if not self.media_targets:
            raise CreativeValidationError("production spec requires at least one media output target")
        if not self.qa_expectations:
            raise CreativeValidationError("production spec requires at least one QA expectation")
        identity_ids = [item.identity_id for item in self.identities]
        if len(set(identity_ids)) != len(identity_ids):
            raise CreativeValidationError("duplicate identity continuity ID")
        known_identities = set(identity_ids)
        scene_by_id: dict[str, ProductionScene] = {}
        for index, scene in enumerate(self.scenes, start=1):
            if scene.order != index:
                raise CreativeValidationError("scenes must be strictly ordered 1..N")
            if scene.scene_id in scene_by_id:
                raise CreativeValidationError("duplicate scene_id")
            if not set(scene.identity_refs) <= known_identities:
                raise CreativeValidationError("inconsistent scene identity reference")
            if self.audio.mode == "SILENCE" and (scene.speech_mode != "SILENCE" or scene.sound_cue is not None):
                raise CreativeValidationError("explicit SILENCE audio conflicts with scene speech or sound cue")
            scene_by_id[scene.scene_id] = scene
        if len(self.transitions) != len(self.scenes) - 1:
            raise CreativeValidationError("transitions must connect every consecutive scene pair")
        for index, trans in enumerate(self.transitions):
            if (trans.from_scene_id != self.scenes[index].scene_id
                    or trans.to_scene_id != self.scenes[index + 1].scene_id):
                raise CreativeValidationError("inconsistent scene transition order or target")
        segment_ids = [seg.segment_id for seg in self.script]
        if len(set(segment_ids)) != len(segment_ids):
            raise CreativeValidationError("duplicate script segment_id")
        segments_by_scene: dict[str, list[ScriptSegment]] = {sid: [] for sid in scene_by_id}
        last_order = 0
        for seg in self.script:
            if seg.scene_id not in scene_by_id:
                raise CreativeValidationError("script segment references unknown scene_id")
            order = scene_by_id[seg.scene_id].order
            if order < last_order:
                raise CreativeValidationError("script segments must follow scene order")
            last_order = order
            if self.audio.mode == "SILENCE" and seg.speech_mode != "SILENCE":
                raise CreativeValidationError("explicit SILENCE audio conflicts with spoken script segment")
            segments_by_scene[seg.scene_id].append(seg)
        for scene in self.scenes:
            segs = segments_by_scene[scene.scene_id]
            if not segs:
                raise CreativeValidationError("every scene requires at least one explicit script or SILENCE segment")
            if scene.speech_mode == "SILENCE" and any(s.speech_mode != "SILENCE" for s in segs):
                raise CreativeValidationError("inconsistent scene speech state: SILENCE scene has spoken segment")
            if scene.speech_mode == "SPOKEN" and not any(s.speech_mode == "SPOKEN" for s in segs):
                raise CreativeValidationError("inconsistent scene speech state: SPOKEN scene lacks spoken segment")
        for items, attr, label in (
            (self.media_targets, "target_id", "media target"),
            (self.qa_expectations, "check_id", "QA expectation"),
            (self.attributions, "source_ref", "attribution source_ref"),
        ):
            keys = [getattr(item, attr) for item in items]
            if len(set(keys)) != len(keys):
                raise CreativeValidationError(f"duplicate {label}")
        for target in self.media_targets:
            if not set(target.scene_refs) <= scene_by_id.keys():
                raise CreativeValidationError("media target references unknown scene_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "narrative_intention": self.narrative_intention,
            "visual_style": self.visual_style,
            "lighting_direction": self.lighting_direction,
            "identities": [i.to_dict() for i in self.identities],
            "physics_invariants": list(self.physics_invariants),
            "temporal_invariants": list(self.temporal_invariants),
            "creative_freedoms": list(self.creative_freedoms),
            "scenes": [s.to_dict() for s in self.scenes],
            "script": [s.to_dict() for s in self.script],
            "transitions": [t.to_dict() for t in self.transitions],
            "audio": self.audio.to_dict(),
            "media_targets": [m.to_dict() for m in self.media_targets],
            "qa_expectations": [q.to_dict() for q in self.qa_expectations],
            "attributions": [a.to_dict() for a in self.attributions],
        }

    @classmethod
    def from_dict(cls, data: object) -> ProductionPackageSpec:
        value = exact_fields(data, {f.name for f in fields(cls)})
        for name in ("physics_invariants", "temporal_invariants", "creative_freedoms"):
            if not isinstance(value[name], list):
                raise CreativeValidationError(f"expected {name} array")
        for name, kind in (
            ("identities", IdentityContinuity), ("scenes", ProductionScene),
            ("script", ScriptSegment), ("transitions", SceneTransition),
            ("media_targets", MediaOutputTarget), ("qa_expectations", QAExpectation),
            ("attributions", RightsAttribution),
        ):
            if not isinstance(value[name], list):
                raise CreativeValidationError(f"expected {name} array")
            value[name] = tuple(kind.from_dict(item) for item in value[name])
        value["audio"] = AudioPlan.from_dict(value["audio"])
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ProductionPackageParent:
    package_id: str
    revision: int
    digest: str

    def __post_init__(self) -> None:
        require_package_id(self.package_id)
        positive_int(self.revision, "parent.revision")
        require_digest(self.digest)

    def to_dict(self) -> dict[str, Any]:
        return {"package_id": self.package_id, "revision": self.revision, "digest": self.digest}

    @classmethod
    def from_dict(cls, data: object) -> ProductionPackageParent:
        return cls(**exact_fields(data, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class ProductionLineage:
    """Exact R2 cycle lineage pinned into an A03CreativeProductionPackage.v1."""

    cycle_job_digest: str
    cycle_result_digest: str
    cycle_status: Literal["CREATIVE_COMPLETE", "TEST_ONLY_MOCK"]
    explore_receipt_digest: str
    candidate_pins: tuple[ConceptSelection, ...]
    critic_receipts: tuple[str, ...]
    selector_receipt_digest: str
    develop_receipt_digest: str
    decision_id: str
    intent_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("cycle_job_digest", "cycle_result_digest", "explore_receipt_digest",
                     "selector_receipt_digest", "develop_receipt_digest"):
            require_digest(getattr(self, name))
        if self.cycle_status not in ("CREATIVE_COMPLETE", "TEST_ONLY_MOCK"):
            raise CreativeValidationError("production lineage requires a completed R2 cycle status")
        if not isinstance(self.candidate_pins, (tuple, list)) or any(
            not isinstance(p, ConceptSelection) for p in self.candidate_pins
        ):
            raise CreativeValidationError("expected typed candidate pins")
        pins = tuple(ConceptSelection.from_dict(p.to_dict()) for p in self.candidate_pins)
        if len(pins) < 2 or len({p.selection_id for p in pins}) != len(pins):
            raise CreativeValidationError("production lineage requires at least two distinct candidate pins")
        object.__setattr__(self, "candidate_pins", pins)
        critics = _distinct_texts(self.critic_receipts, "lineage.critic_receipts", required=True)
        for ref in critics:
            require_digest(ref)
        object.__setattr__(self, "critic_receipts", critics)
        _require_decision_id(self.decision_id)
        if self.intent_digest is not None:
            require_digest(self.intent_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_job_digest": self.cycle_job_digest,
            "cycle_result_digest": self.cycle_result_digest,
            "cycle_status": self.cycle_status,
            "explore_receipt_digest": self.explore_receipt_digest,
            "candidate_pins": [p.to_dict() for p in self.candidate_pins],
            "critic_receipts": list(self.critic_receipts),
            "selector_receipt_digest": self.selector_receipt_digest,
            "develop_receipt_digest": self.develop_receipt_digest,
            "decision_id": self.decision_id,
            "intent_digest": self.intent_digest,
        }

    @classmethod
    def from_dict(cls, data: object) -> ProductionLineage:
        value = exact_fields(data, {f.name for f in fields(cls)})
        if not isinstance(value["candidate_pins"], list) or not isinstance(value["critic_receipts"], list):
            raise CreativeValidationError("expected lineage arrays")
        value["candidate_pins"] = tuple(ConceptSelection.from_dict(p) for p in value["candidate_pins"])
        return cls(**value)
