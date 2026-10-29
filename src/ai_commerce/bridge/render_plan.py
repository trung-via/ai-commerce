"""Bridge mapping from C03 CreativeComposition to C06 RenderPlan."""

from __future__ import annotations

from typing import Any

from ai_commerce.composition.models import CreativeComposition
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.render.models import Canvas as RenderCanvas, RenderItem, RenderPlan, SourceMediaRef


def composition_to_render_plan(
    composition: CreativeComposition,
    *,
    silence: bool = False,
    composition_ref: str | None = None,
) -> RenderPlan:
    """Map a validated CreativeComposition to a C06 provider-neutral RenderPlan."""
    if not isinstance(composition, CreativeComposition):
        raise ValueError("Expected CreativeComposition")
    if composition.unresolved_intent:
        raise ValueError("ASSET_REQUIRED: composition has unresolved intent items")

    # Map Canvas
    c = composition.canvas
    fps_val: int
    if isinstance(c.fps, int):
        fps_val = c.fps
    elif hasattr(c.fps, "denominator"):
        if c.fps.numerator % c.fps.denominator == 0:
            fps_val = c.fps.numerator // c.fps.denominator
        else:
            fps_val = int(round(c.fps.numerator / c.fps.denominator))
    else:
        fps_val = int(c.fps)

    render_canvas = RenderCanvas(c.width, c.height, fps_val)

    # Collect visual track items
    if not composition.tracks:
        raise ValueError("ASSET_REQUIRED: supply at least one validated still")

    visual_tracks = [t for t in composition.tracks if t.kind in ("visual", "image", "default")]
    if not visual_tracks:
        visual_tracks = list(composition.tracks)

    track = visual_tracks[0]
    if not track.items:
        raise ValueError("ASSET_REQUIRED: supply at least one validated still")

    media_by_id = {m.artifact_id: m for m in composition.media}
    render_items = []
    expected_start = 0

    for item in track.items:
        if item.kind != "image":
            raise ValueError(f"Unsupported item kind for local render: {item.kind!r}; only 'image' is supported")
        if item.start_frame != expected_start:
            raise ValueError(
                f"Still items must be ordered and contiguous from frame zero; expected {expected_start}, got {item.start_frame}"
            )
        if item.asset_id not in media_by_id:
            raise ValueError(f"Referenced media {item.asset_id!r} not found in composition media")

        ref = media_by_id[item.asset_id]
        if ref.manifest_ref is not None:
            source = SourceMediaRef(canonical_bytes(ref.artifact), ref.manifest_ref, ref.manifest_record_sha256)
        else:
            source = SourceMediaRef.from_artifact(ref.artifact)

        render_items.append(RenderItem(source, item.start_frame, item.duration_frames))
        expected_start += item.duration_frames

    comp_ref = composition_ref or f"compositions/{composition.composition_id[12:]}/r{composition.revision}.json"
    comp_sha = composition.digest
    comp_rev = composition.revision

    silence_flag = silence or getattr(composition.settings, "silence", False)

    return RenderPlan(
        canvas=render_canvas,
        items=tuple(render_items),
        silence=silence_flag,
        composition_ref=comp_ref,
        composition_sha256=comp_sha,
        composition_revision=comp_rev,
    )
