"""Explicit authoring helpers. No model, synthetic asset or implicit duration."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from ai_commerce.creative import CreativeIntent

from ._validation import CompositionValidationError, integer, text, typed_tuple
from .models import (
    Canvas, CompositionParent, CreativeComposition, RenderSettings, SceneItem,
    Track, composition_identity,
)
from .sources import BriefSnapshot, MediaReference, pinned_selection


def create_composition(
    intent: str | CreativeIntent, *, canvas: Canvas, tracks: Sequence[Track] = (),
    media: Sequence[MediaReference] = (), duration_frames: int | None = None,
    settings: RenderSettings = RenderSettings(), selection: object = None,
    source_brief: object = None,
) -> CreativeComposition:
    """Create an unsaved initial revision, including unresolved prompt-only input."""
    if not isinstance(canvas, Canvas) or not isinstance(settings, RenderSettings):
        raise CompositionValidationError("expected typed canvas and settings")
    source_intent = intent if isinstance(intent, CreativeIntent) else None
    raw_text = text(source_intent.raw_text if source_intent is not None else intent, "original_intent")
    pin, selected_brief = pinned_selection(selection) if selection is not None else (None, None)
    brief = (source_brief if isinstance(source_brief, BriefSnapshot)
             else BriefSnapshot.from_dict(source_brief) if source_brief is not None else selected_brief)
    if selected_brief is not None and brief != selected_brief:
        raise CompositionValidationError("explicit brief differs from selection payload")
    valid_tracks = typed_tuple(tracks, Track, "tracks")
    valid_media = typed_tuple(media, MediaReference, "media")
    if duration_frames is None:
        duration_frames = max((i.end_frame for t in valid_tracks for i in t.items), default=0)
    # Compute the seed from the identical serialized content checked by the model.
    data = {
        "schema_version": "CreativeComposition.v1",
        "original_intent": raw_text, "intent": raw_text, "canvas": canvas.to_dict(),
        "duration_frames": duration_frames, "tracks": [t.to_dict() for t in valid_tracks],
        "media": [m.to_dict() for m in valid_media], "settings": settings.to_dict(),
        "selection": pin.to_dict() if pin else None, "source_brief": brief.to_dict() if brief else None,
        "source_intent": source_intent.to_dict() if source_intent else None,
    }
    return CreativeComposition(
        composition_id=composition_identity(data), original_intent=raw_text, intent=raw_text,
        canvas=canvas, duration_frames=duration_frames, tracks=valid_tracks, media=valid_media,
        settings=settings, selection=pin, source_brief=brief, source_intent=source_intent,
    )


def compose_images(intent: str | CreativeIntent, scenes: Sequence[tuple[MediaReference, int]], *,
                   canvas: Canvas, **metadata: Any) -> CreativeComposition:
    """Lay supplied images end-to-end; scene count and frame lengths are caller choices."""
    if not isinstance(scenes, (tuple, list)):
        raise CompositionValidationError("expected ordered image/frame pairs")
    media: dict[str, MediaReference] = {}
    items, cursor = [], 0
    for index, pair in enumerate(scenes, 1):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise CompositionValidationError("expected image/frame pair")
        reference, frames = pair
        if not isinstance(reference, MediaReference) or reference.artifact["kind"] != "image":
            raise CompositionValidationError("expected image MediaReference")
        integer(frames, "scene duration_frames", 1)
        if reference.artifact_id in media and media[reference.artifact_id] != reference:
            raise CompositionValidationError("conflicting metadata for shared image")
        media[reference.artifact_id] = reference
        items.append(SceneItem(f"scene-{index}", "image", cursor, frames, reference.artifact_id))
        cursor += frames
    tracks = (Track("visual", tuple(items)),) if items else ()
    return create_composition(intent, canvas=canvas, tracks=tracks, media=tuple(media.values()), **metadata)


def direct_image(intent: str | CreativeIntent, image: MediaReference | None = None, *,
                 canvas: Canvas, duration_frames: int | None = None, **metadata: Any) -> CreativeComposition:
    """No image means unresolved intent. An image requires an explicit frame length."""
    if image is None:
        return create_composition(intent, canvas=canvas, duration_frames=duration_frames, **metadata)
    integer(duration_frames, "duration_frames", 1)
    return compose_images(intent, ((image, duration_frames),), canvas=canvas, **metadata)


EDITABLE_FIELDS = frozenset({"intent", "canvas", "duration_frames", "tracks", "media", "settings"})
SOURCE_FIELDS = ("original_intent", "selection", "source_brief", "source_intent", "schema_version")


def edit_composition(composition: CreativeComposition, **changes: Any) -> CreativeComposition:
    """Return an unsaved child. Original source evidence and pins never change."""
    if not isinstance(composition, CreativeComposition):
        raise CompositionValidationError("expected CreativeComposition")
    valid = CreativeComposition.from_dict(composition.to_dict())
    if not changes or changes.keys() - EDITABLE_FIELDS:
        raise CompositionValidationError("edits must target composition content, not identity or original sources")
    if all(getattr(valid, name) == value for name, value in changes.items()):
        raise CompositionValidationError("revision must change content")
    return replace(valid, **changes, revision=valid.revision + 1,
                   parent=CompositionParent(valid.composition_id, valid.revision, valid.digest))
