"""CreativeComposition.v1: immutable data, independent of any render engine."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any, Literal

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import ConceptSelection, CreativeIntent

from ._validation import (
    CompositionValidationError, digest, identifier, identity, integer, read_json,
    record, text, typed_tuple,
)
from .sources import BriefSnapshot, MediaReference


class JsonModel:
    __slots__ = ()

    def to_dict(self) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            if isinstance(value, tuple):
                return [encode(v) for v in value]
            return value.to_dict() if hasattr(value, "to_dict") else value
        return {f.name: encode(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, value: object):
        return cls(**record(value, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class FrameRate(JsonModel):
    """Exact frames/second; 30000/1001 needs no floating-point rounding."""

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        integer(self.numerator, "fps.numerator", 1)
        integer(self.denominator, "fps.denominator", 1)
        from math import gcd
        divisor = gcd(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", self.numerator // divisor)
        object.__setattr__(self, "denominator", self.denominator // divisor)


@dataclass(frozen=True, slots=True)
class Canvas(JsonModel):
    width: int
    height: int
    fps: FrameRate | int

    def __post_init__(self) -> None:
        integer(self.width, "canvas.width", 1)
        integer(self.height, "canvas.height", 1)
        fps = FrameRate(self.fps) if type(self.fps) is int else self.fps
        if not isinstance(fps, FrameRate):
            raise CompositionValidationError("expected integer fps or FrameRate")
        object.__setattr__(self, "fps", fps)

    @classmethod
    def from_dict(cls, value: object) -> Canvas:
        data = record(value, {"width", "height", "fps"})
        data["fps"] = FrameRate.from_dict(data["fps"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class RenderSettings(JsonModel):
    """Provider-neutral layout; no codec, executable or filter syntax."""

    fit: Literal["contain", "cover", "stretch"] = "contain"
    background: str = "#000000"

    def __post_init__(self) -> None:
        if self.fit not in ("contain", "cover", "stretch"):
            raise CompositionValidationError("unsupported fit")
        if not isinstance(self.background, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", self.background):
            raise CompositionValidationError("background must be an RGB hex color")


@dataclass(frozen=True, slots=True)
class SceneItem(JsonModel):
    """Half-open interval [start_frame, start_frame + duration_frames).

    Intent items explicitly mark missing creative media. No implicit image
    generation, text overlay, audio, transitions or source-frame conversion.
    """

    item_id: str
    kind: Literal["image", "video", "intent"]
    start_frame: int
    duration_frames: int
    asset_id: str | None = None
    intent: str | None = None
    source_start_frame: int = 0

    def __post_init__(self) -> None:
        identifier(self.item_id, "item_id")
        integer(self.start_frame, "start_frame")
        integer(self.duration_frames, "duration_frames", 1)
        integer(self.source_start_frame, "source_start_frame")
        if self.kind not in ("image", "video", "intent"):
            raise CompositionValidationError("unsupported item kind")
        if self.intent is not None:
            text(self.intent, "item.intent")
        if self.kind == "intent":
            text(self.intent, "unresolved item intent")
            if self.asset_id is not None:
                raise CompositionValidationError("intent item cannot claim a realized asset")
        else:
            identity(self.asset_id, "artifact")
        if self.kind != "video" and self.source_start_frame != 0:
            raise CompositionValidationError("only video items may trim source frames")

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.duration_frames


@dataclass(frozen=True, slots=True)
class Track(JsonModel):
    """Tracks are composited in tuple order, bottom to top.

    Same-track items are chronological and nonoverlapping. Put simultaneous
    visual items on separate tracks. Empty tracks and timeline gaps are valid.
    """

    track_id: str
    items: tuple[SceneItem, ...] = ()
    kind: Literal["visual"] = "visual"

    def __post_init__(self) -> None:
        identifier(self.track_id, "track_id")
        if self.kind != "visual":
            raise CompositionValidationError("only visual tracks are implemented")
        items = typed_tuple(self.items, SceneItem, "items")
        object.__setattr__(self, "items", items)
        if any(b.start_frame < a.end_frame for a, b in zip(items, items[1:])):
            raise CompositionValidationError("track items must be ordered and nonoverlapping")

    @classmethod
    def from_dict(cls, value: object) -> Track:
        data = record(value, {"track_id", "items", "kind"})
        if not isinstance(data["items"], list):
            raise CompositionValidationError("expected items array")
        data["items"] = tuple(SceneItem.from_dict(v) for v in data["items"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CompositionParent(JsonModel):
    composition_id: str
    revision: int
    digest: str

    def __post_init__(self) -> None:
        identity(self.composition_id, "composition")
        integer(self.revision, "parent.revision", 1)
        digest(self.digest)


def composition_identity(data: dict[str, Any]) -> str:
    content = {k: v for k, v in data.items() if k not in {"composition_id", "revision", "parent"}}
    return "composition:" + sha256_bytes(canonical_bytes(content))


@dataclass(frozen=True, slots=True)
class CreativeComposition(JsonModel):
    composition_id: str
    original_intent: str
    intent: str
    canvas: Canvas
    duration_frames: int = 0
    tracks: tuple[Track, ...] = ()
    media: tuple[MediaReference, ...] = ()
    settings: RenderSettings = RenderSettings()
    selection: ConceptSelection | None = None
    source_brief: BriefSnapshot | None = None
    source_intent: CreativeIntent | None = None
    revision: int = 1
    parent: CompositionParent | None = None
    schema_version: str = "CreativeComposition.v1"

    def __post_init__(self) -> None:
        identity(self.composition_id, "composition")
        text(self.original_intent, "original_intent")
        text(self.intent, "intent")
        integer(self.revision, "revision", 1)
        integer(self.duration_frames, "duration_frames")
        if self.schema_version != "CreativeComposition.v1":
            raise CompositionValidationError("unsupported composition schema")
        if not isinstance(self.canvas, Canvas) or not isinstance(self.settings, RenderSettings):
            raise CompositionValidationError("expected typed canvas and settings")
        object.__setattr__(self, "tracks", typed_tuple(self.tracks, Track, "tracks"))
        object.__setattr__(self, "media", typed_tuple(self.media, MediaReference, "media"))
        if self.selection is not None:
            if not isinstance(self.selection, ConceptSelection):
                raise CompositionValidationError("expected typed pinned selection")
            ConceptSelection.from_dict(self.selection.to_dict())
        if self.source_brief is not None and not isinstance(self.source_brief, BriefSnapshot):
            raise CompositionValidationError("expected BriefSnapshot")
        if self.source_intent is not None:
            if not isinstance(self.source_intent, CreativeIntent):
                raise CompositionValidationError("expected CreativeIntent")
            CreativeIntent.from_dict(self.source_intent.to_dict())
            if self.source_intent.raw_text != self.original_intent:
                raise CompositionValidationError("original intent differs from A03 raw text")
        if self.source_brief is not None:
            brief = self.source_brief.to_dict()
            for source in (self.selection, self.source_intent):
                if source is not None and (
                    source.brief_id != brief["brief_id"] or source.source_brief_digest != self.source_brief.digest
                ):
                    raise CompositionValidationError("source brief identity/digest mismatch")
        if self.selection is not None and self.source_intent is not None and (
            self.selection.brief_id != self.source_intent.brief_id
            or self.selection.source_brief_digest != self.source_intent.source_brief_digest
        ):
            raise CompositionValidationError("intent and selection reference different briefs")
        if len({t.track_id for t in self.tracks}) != len(self.tracks):
            raise CompositionValidationError("duplicate track_id")
        library = {m.artifact_id: m for m in self.media}
        if len(library) != len(self.media):
            raise CompositionValidationError("duplicate artifact_id")
        items = [item for track in self.tracks for item in track.items]
        if len({i.item_id for i in items}) != len(items):
            raise CompositionValidationError("duplicate item_id")
        for item in items:
            if item.end_frame > self.duration_frames:
                raise CompositionValidationError("item timing exceeds composition duration")
            if item.kind != "intent":
                if item.asset_id not in library:
                    raise CompositionValidationError("dangling item media reference")
                if library[item.asset_id].artifact["kind"] != item.kind:
                    raise CompositionValidationError("item kind does not match referenced media")
        if self.revision == 1:
            if self.parent is not None or self.intent != self.original_intent:
                raise CompositionValidationError("initial revision cannot have parent or altered original intent")
            if self.composition_id != composition_identity(self.to_dict()):
                raise CompositionValidationError("initial composition identity mismatch")
        elif (not isinstance(self.parent, CompositionParent)
              or self.parent.composition_id != self.composition_id
              or self.parent.revision != self.revision - 1):
            raise CompositionValidationError("revision needs its exact preceding parent")

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @property
    def unresolved_intent(self) -> bool:
        items = [item for track in self.tracks for item in track.items]
        return not items or any(item.kind == "intent" for item in items)

    def to_json(self) -> str:
        return canonical_bytes(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> CreativeComposition:
        data = record(value, {f.name for f in fields(cls)})
        data["canvas"] = Canvas.from_dict(data["canvas"])
        data["settings"] = RenderSettings.from_dict(data["settings"])
        for name, kind in (("tracks", Track), ("media", MediaReference)):
            if not isinstance(data[name], list):
                raise CompositionValidationError(f"expected {name} array")
            data[name] = tuple(kind.from_dict(v) for v in data[name])
        if data["parent"] is not None:
            data["parent"] = CompositionParent.from_dict(data["parent"])
        if data["selection"] is not None:
            data["selection"] = ConceptSelection.from_dict(data["selection"])
        if data["source_brief"] is not None:
            data["source_brief"] = BriefSnapshot.from_dict(data["source_brief"])
        if data["source_intent"] is not None:
            data["source_intent"] = CreativeIntent.from_dict(data["source_intent"])
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str | bytes) -> CreativeComposition:
        return cls.from_dict(read_json(raw))
