"""Resolve an editable composition to data for a later render adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.operations import ArtifactRegistry, LocalMediaRunner

from ._validation import CompositionValidationError, read_json, text, typed_tuple
from .models import CreativeComposition, JsonModel
from .sources import MediaReference


@dataclass(frozen=True, slots=True)
class ResolvedMedia(JsonModel):
    """Resolver-attested bytes identity and location; never a generated asset."""

    reference: MediaReference
    source_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, MediaReference):
            raise CompositionValidationError("expected MediaReference")
        text(self.source_ref, "resolved source_ref")
        if any(ord(c) < 32 for c in self.source_ref):
            raise CompositionValidationError("invalid resolved source_ref")


class MediaResolver(Protocol):
    def resolve(self, reference: MediaReference) -> ResolvedMedia:
        """Verify the exact asset bytes/provenance, or raise; no generation."""
        ...


class RegistryMediaResolver:
    """Adapter for the actual A04 registry available in this checkout.

    It preserves that registry's format/capability limits. JPEG/WebP/MP4 require
    an upgraded resolver; declaring their MIME alone cannot make compile pass.
    """

    def __init__(self, registry: ArtifactRegistry) -> None:
        self.registry = registry

    def resolve(self, reference: MediaReference) -> ResolvedMedia:
        if reference.manifest_ref is not None:
            parts = reference.manifest_ref.split("/")
            manifest = LocalMediaRunner(self.registry).load(parts[1], int(parts[2][8:-14]))
            if (manifest.to_dict()["record_sha256"] != reference.manifest_record_sha256
                    or manifest.result["artifact"] != reference.artifact or manifest.state != "SUCCEEDED"):
                raise CompositionValidationError("A04 manifest binding mismatch")
        if reference.handoff_ref is not None:
            handoff = read_json(self.registry.read(reference.handoff_ref))
            expected = "handoffs/" + sha256_bytes(canonical_bytes(handoff)) + ".json"
            if reference.handoff_ref != expected or handoff != reference._handoff():
                raise CompositionValidationError("A04 handoff binding mismatch")
        if reference.manifest_ref is None:
            self.registry.verify(reference.artifact)
        return ResolvedMedia(reference, str(self.registry.safe_path(reference.artifact["path"])))


@dataclass(frozen=True, slots=True)
class CompositionPlan:
    """Provider-neutral, resolved data. READY does not mean a video was rendered."""

    composition: CreativeComposition
    sources: tuple[ResolvedMedia, ...]
    schema_version: str = "CompositionPlan.v1"

    def __post_init__(self) -> None:
        if not isinstance(self.composition, CreativeComposition) or self.composition.unresolved_intent:
            raise CompositionValidationError("plan requires a resolved visual timeline")
        if self.schema_version != "CompositionPlan.v1":
            raise CompositionValidationError("unsupported plan version")
        object.__setattr__(self, "sources", typed_tuple(self.sources, ResolvedMedia, "sources"))
        used = {i.asset_id for t in self.composition.tracks for i in t.items}
        if {s.reference.artifact_id for s in self.sources} != used or len(self.sources) != len(used):
            raise CompositionValidationError("plan sources must resolve each used asset exactly once")
        library = {m.artifact_id: m for m in self.composition.media}
        if any(library[s.reference.artifact_id] != s.reference for s in self.sources):
            raise CompositionValidationError("resolved media differs from composition")

    def to_dict(self) -> dict[str, Any]:
        composition = self.composition
        return {
            "schema_version": self.schema_version, "composition_id": composition.composition_id,
            "revision": composition.revision, "composition_digest": composition.digest,
            "canvas": composition.canvas.to_dict(), "duration_frames": composition.duration_frames,
            "settings": composition.settings.to_dict(), "tracks": [t.to_dict() for t in composition.tracks],
            "sources": [s.to_dict() for s in self.sources],
        }


@dataclass(frozen=True, slots=True)
class CompilationDiagnostic(JsonModel):
    code: str
    message: str
    asset_id: str | None = None
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class CompileResult(JsonModel):
    state: Literal["READY", "ASSET_REQUIRED", "SOURCE_ERROR"]
    plan: CompositionPlan | None = None
    diagnostics: tuple[CompilationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", typed_tuple(self.diagnostics, CompilationDiagnostic, "diagnostics"))
        if self.state == "READY":
            if not isinstance(self.plan, CompositionPlan) or self.diagnostics:
                raise CompositionValidationError("READY requires a plan without errors")
        elif self.state in ("ASSET_REQUIRED", "SOURCE_ERROR"):
            if self.plan is not None or not self.diagnostics:
                raise CompositionValidationError("unsuccessful compile cannot contain a plan")
        else:
            raise CompositionValidationError("unsupported compilation state")


def _exception_detail(error: Exception) -> str:
    """Retain dependency/custody causes that A04 deliberately wraps."""
    parts, seen = [], set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__
    return "; caused by ".join(parts)


def compile_composition(composition: CreativeComposition, resolver: MediaResolver | None = None) -> CompileResult:
    """Resolve sources only; never call a model, create media or execute a renderer."""
    if not isinstance(composition, CreativeComposition):
        raise CompositionValidationError("expected CreativeComposition")
    valid = CreativeComposition.from_dict(composition.to_dict())
    items = [item for track in valid.tracks for item in track.items]
    missing = tuple(CompilationDiagnostic(
        "ASSET_REQUIRED", "Supply a realized asset for this intent item", item_id=item.item_id,
    ) for item in items if item.kind == "intent")
    if valid.unresolved_intent:
        return CompileResult("ASSET_REQUIRED", diagnostics=missing or (
            CompilationDiagnostic("ASSET_REQUIRED", "Original intent has no visual timeline with realized assets"),
        ))
    if resolver is None:
        return CompileResult("ASSET_REQUIRED", diagnostics=(
            CompilationDiagnostic("RESOLVER_REQUIRED", "Configure a media resolver to verify actual source bytes"),
        ))
    used = {item.asset_id for item in items}
    sources = []
    for reference in valid.media:
        if reference.artifact_id not in used:
            continue
        try:
            resolved = resolver.resolve(reference)
            if not isinstance(resolved, ResolvedMedia) or resolved.reference != reference:
                raise CompositionValidationError("resolver rebound the source asset")
            sources.append(resolved)
        except Exception as exc:
            return CompileResult("SOURCE_ERROR", diagnostics=(CompilationDiagnostic(
                "SOURCE_RESOLUTION_FAILED", _exception_detail(exc), reference.artifact_id,
            ),))
    return CompileResult("READY", CompositionPlan(valid, tuple(sources)))
