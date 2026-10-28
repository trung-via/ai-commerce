"""A05 C03 contracts, actual parent compatibility, and honest asset resolution."""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Barrier

import pytest

from ai_commerce.composition import (
    BriefSnapshot, Canvas, CompositionParent, CompositionStore,
    CompositionValidationError, CompositionVersionConflict, CreativeComposition,
    FrameRate, MediaReference, RegistryMediaResolver, RenderSettings,
    ResolvedMedia, SceneItem, Track, compile_composition, compose_images,
    create_composition, direct_image, edit_composition, pinned_selection,
)
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import (
    CreativeStore, CreativeValidationError, IntentField, MockCreativeReasoner,
    brief_digest, capture_intent, generate_concepts, intake_brief, revise_concept,
)
from ai_commerce.media import MediaRequest
from ai_commerce.operations import ArtifactRegistry, LocalMediaRunner
from ai_commerce.operations.errors import ArtifactMissingError

TEXT = "M\u01b0a S\u00e0i G\u00f2n \U0001f327\ufe0f e\u0301"
CANVAS = Canvas(721, 479, FrameRate(30000, 1001))


def declaration(sha="a" * 64, **changes):
    artifact = {
        "artifact_id": "artifact:" + sha, "kind": "image", "mime_type": "image/png",
        "sha256": sha, "size_bytes": 123, "path": "objects/" + sha,
        "source_snapshot_ids": ["snapshot:" + sha], "rights_status": "UNKNOWN",
        "provider": "caller-declared", "generation_performed": False,
    }
    artifact.update(changes)
    return MediaReference.from_artifact(artifact)


def png(red):
    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes((0, red, 30, 60))))
            + chunk(b"IEND", b""))


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    root = tmp_path_factory.mktemp("composition-assets")
    registry = ArtifactRegistry(root / "custody")
    runner = LocalMediaRunner(registry)
    manifests = []
    for index, red in enumerate((10, 100, 200)):
        source = root / f"source-{index}.png"
        source.write_bytes(png(red))
        manifests.append(runner.run(MediaRequest("import", source, "image/png")))
    return registry.root, tuple(manifests)


@pytest.fixture
def selected(tmp_path):
    brief = intake_brief(TEXT)
    concept = generate_concepts(brief, MockCreativeReasoner())[0]
    store = CreativeStore(tmp_path / "creative")
    store.save_brief(brief)
    store.save_concept(concept)
    selection = store.select(concept.concept_id, 1)
    return store, concept, selection, store.load_selected(selection.selection_id)


def test_prompt_only_is_preserved_unresolved_without_invoking_a_resolver(tmp_path):
    class Forbidden:
        def resolve(self, reference):
            pytest.fail("unresolved intent must not resolve or generate media")

    composition = direct_image("  {a freeform idea, not JSON} \n" + TEXT, canvas=CANVAS)
    assert composition.tracks == composition.media == ()
    assert composition.duration_frames == 0 and composition.unresolved_intent
    result = compile_composition(composition, Forbidden())
    assert result.state == result.diagnostics[0].code == "ASSET_REQUIRED"
    assert result.plan is None
    store = CompositionStore(tmp_path / "store")
    assert store.save(composition) == store.load(composition.composition_id, 1)
    data = composition.to_dict()
    assert data["selection"] is data["source_brief"] is data["source_intent"] is None
    assert not {"storyboard", "sku", "cta", "model", "score"} & data.keys()
    assert composition.canvas.width == 721 and composition.canvas.height == 479


def test_stable_canonical_identity_and_digest_have_a_fixed_golden():
    composition = create_composition("rain", canvas=Canvas(640, 360, 24))
    assert composition.composition_id == "composition:e1159053d5b72ba226357957c80b1c681681ff91ba65662f8b5b7ee858cc8c98"
    assert composition.digest == "3a129513f627077f19afc7e9d7b1f5dec10104990c81d32cebda0c496bcfac5a"
    reordered = dict(reversed(list(composition.to_dict().items())))
    assert CreativeComposition.from_dict(reordered) == composition
    assert CreativeComposition.from_json(json.dumps(reordered, indent=2)).digest == composition.digest
    assert create_composition("rain!", canvas=Canvas(640, 360, 24)).composition_id != composition.composition_id
    assert FrameRate(60000, 2002) == FrameRate(30000, 1001)


@pytest.mark.parametrize("raw", [TEXT, " \n e\u0301 \u00e9 \U0001f98b\n", "\u591c\u306e\u8857", "\u062d\u0643\u0627\u064a\u0629"])
def test_unicode_roundtrip_retains_every_original_codepoint_and_source_intent(tmp_path, raw):
    brief = {**intake_brief(raw), "custom": {"palette": ["\u9752", "\u7d2b"]}}
    intent = capture_intent(brief, raw_text=raw, fields=(
        IntentField("source", raw, "SOURCE", raw),
        IntentField("hypothesis", "maybe", "INFERRED"), IntentField("unknown"),
    ))
    composition = create_composition(intent, canvas=CANVAS, source_brief=brief)
    original_digest = composition.digest
    brief["custom"]["palette"].clear()
    detached = composition.to_dict()
    detached["source_brief"]["custom"]["palette"].clear()
    detached["source_intent"]["fields"].clear()
    store = CompositionStore(tmp_path)
    reopened = store.load(store.save(composition).composition_id, 1)
    assert reopened.original_intent.encode("utf-8") == raw.encode("utf-8")
    assert reopened.source_intent == intent
    assert reopened.source_intent.observation.model == "UNKNOWN"
    assert reopened.source_intent.actor.kind == "UNKNOWN"
    assert reopened.source_brief.to_dict()["custom"]["palette"] == ["\u9752", "\u7d2b"]
    assert reopened.digest == original_digest
    with pytest.raises(FrozenInstanceError):
        reopened.canvas.width = 1
    with pytest.raises(FrozenInstanceError):
        reopened.original_intent = "changed"


@pytest.mark.parametrize("form", ["typed", "v1-json", "consumer-selection", "consumer-payload"])
def test_old_v1_selection_inputs_keep_literal_historical_ids(selected, form):
    store, concept, selection, payload = selected
    inputs = {"typed": selection, "v1-json": selection.to_dict(),
              "consumer-selection": payload["selection"], "consumer-payload": payload}
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*.json")}
    composition = create_composition(TEXT, canvas=CANVAS, selection=inputs[form])
    assert selection.selection_id == "selection:ac7eb5e385210355e8acf01e08ed05251467b011a1bff03c86da5a442ca16b9f"
    assert concept.digest == "5ac3559ec4f3f57ce4808070d69d00dd4be7db934b53bf4fbe5c01ddd7f90f7b"
    assert composition.selection == selection
    assert composition.selection.schema_version == "a03.selection.v1"
    assert CreativeComposition.from_json(composition.to_json()).selection.selection_id == selection.selection_id
    assert compile_composition(composition).state == "ASSET_REQUIRED"
    revised = revise_concept(concept, {"title": "another view"})
    store.save_concept(revised)
    assert composition.selection.revision == 1
    assert composition.selection.concept_digest == concept.digest
    assert all((store.root / path).read_bytes() == data for path, data in before.items())
    assert not (store.root / "sidecars").exists()


@pytest.mark.parametrize("damage", ["selection-id", "concept", "brief", "pin-digest", "extra"])
def test_inconsistent_legacy_selection_payloads_are_rejected(selected, damage):
    _, _, _, payload = selected
    if damage == "selection-id":
        payload["selection"]["selection_id"] = "selection:" + "0" * 64
    elif damage == "concept":
        payload["concept"]["title"] += "tampered"
    elif damage == "brief":
        payload["brief"]["objective"] += "tampered"
    elif damage == "pin-digest":
        payload["selection"]["concept_digest"] = "0" * 64
    else:
        payload["unexpected"] = True
    with pytest.raises((CompositionValidationError, CreativeValidationError)):
        create_composition(TEXT, canvas=CANVAS, selection=payload)


def test_source_brief_and_a03_intent_must_match_the_pin(selected):
    _, _, selection, payload = selected
    with pytest.raises(CompositionValidationError, match="brief"):
        create_composition(TEXT, canvas=CANVAS, selection=selection, source_brief=intake_brief("other"))
    with pytest.raises(CompositionValidationError, match="brief"):
        create_composition(capture_intent("other"), canvas=CANVAS, selection=selection)
    explicit = {**payload["brief"], "different": True}
    with pytest.raises(CompositionValidationError, match="explicit brief"):
        create_composition(TEXT, canvas=CANVAS, selection=payload, source_brief=explicit)


def test_single_actual_image_compiles_to_provider_neutral_data(imported):
    root, manifests = imported
    reference = MediaReference.from_manifest(manifests[0])
    composition = direct_image(TEXT, reference, canvas=CANVAS, duration_frames=37)
    assert compile_composition(composition).state == "ASSET_REQUIRED"
    result = compile_composition(composition, RegistryMediaResolver(ArtifactRegistry(root)))
    assert result.state == "READY" and not result.diagnostics
    plan = result.plan.to_dict()
    assert plan["schema_version"] == "CompositionPlan.v1"
    assert plan["composition_digest"] == composition.digest
    assert plan["duration_frames"] == 37
    assert plan["canvas"] == {"width": 721, "height": 479, "fps": {"numerator": 30000, "denominator": 1001}}
    assert plan["sources"][0]["reference"]["artifact"] == manifests[0].result["artifact"]
    assert Path(plan["sources"][0]["source_ref"]).read_bytes() == png(10)
    assert not {"command", "argv", "filter", "ffmpeg", "encoder"} & plan.keys()
    assert result.to_dict()["plan"] == plan
    assert reference.artifact["generation_performed"] is False
    assert reference.artifact["source_generation"] == reference.artifact["rights_status"] == "UNKNOWN"


def test_multiple_ordered_images_have_exact_timing_without_drift(imported):
    root, manifests = imported
    references = [MediaReference.from_manifest(m) for m in manifests]
    scenes = list(zip(references, (7, 11, 3)))
    composition = compose_images(TEXT, scenes, canvas=CANVAS)
    scenes.clear()
    assert [(i.start_frame, i.duration_frames) for i in composition.tracks[0].items] == [(0, 7), (7, 11), (18, 3)]
    assert [i.asset_id for i in composition.tracks[0].items] == [r.artifact_id for r in references]
    assert composition.duration_frames == 21
    assert compile_composition(composition, RegistryMediaResolver(ArtifactRegistry(root))).state == "READY"
    repeated = compose_images(TEXT, [(references[0], 1), (references[0], 2)], canvas=CANVAS)
    assert len(repeated.media) == 1 and len(repeated.tracks[0].items) == 2
    assert compose_images(TEXT, [], canvas=CANVAS).unresolved_intent


def test_manual_multiple_tracks_gaps_and_video_trim_are_data_not_engine_claims():
    image = declaration()
    video = declaration("b" * 64, kind="video", mime_type="video/mp4")
    background = Track("background", (SceneItem("a", "image", 5, 10, image.artifact_id),))
    foreground = Track("foreground", (SceneItem("b", "video", 0, 20, video.artifact_id, source_start_frame=3),))
    tracks = [background, Track("empty"), foreground]
    composition = create_composition(TEXT, canvas=CANVAS, tracks=tracks, media=[image, video],
                                     duration_frames=22, settings=RenderSettings("cover", "#234567"))
    tracks.clear()
    assert composition.duration_frames == 22 and len(composition.tracks) == 3
    assert composition.tracks[-1].items[0].source_start_frame == 3
    assert CreativeComposition.from_json(composition.to_json()) == composition
    assert compile_composition(composition).plan is None  # Declaration alone is never resolved.


def test_explicit_intent_item_blocks_partial_success(imported):
    root, manifests = imported
    image = MediaReference.from_manifest(manifests[0])
    tracks = (Track("visual", (
        SceneItem("ready", "image", 0, 10, image.artifact_id),
        SceneItem("missing", "intent", 10, 20, intent="a new visual idea"),
    )),)
    composition = create_composition(TEXT, canvas=CANVAS, tracks=tracks, media=(image,))
    result = compile_composition(composition, RegistryMediaResolver(ArtifactRegistry(root)))
    assert result.state == "ASSET_REQUIRED" and result.plan is None
    assert result.diagnostics[0].item_id == "missing"
    assert result.diagnostics[0].code == "ASSET_REQUIRED"


def test_unused_media_does_not_invent_a_timeline_or_block_used_sources(imported):
    root, manifests = imported
    image = MediaReference.from_manifest(manifests[0])
    composition = direct_image(TEXT, image, canvas=CANVAS, duration_frames=5)
    composition = edit_composition(composition, media=(image, declaration()))
    result = compile_composition(composition, RegistryMediaResolver(ArtifactRegistry(root)))
    assert result.state == "READY" and len(result.plan.sources) == 1
    assert compile_composition(create_composition(TEXT, canvas=CANVAS, media=(image,))).state == "ASSET_REQUIRED"


def test_actual_v1_a04_handoff_and_mock_labels_roundtrip_without_mutating_parents(tmp_path, selected):
    _, _, selection, _ = selected
    registry = ArtifactRegistry(tmp_path / "media")
    manifest = LocalMediaRunner(registry).run(MediaRequest("mock"))
    handoff = {"handoff_version": 1, "selection_id": selection.selection_id, "job_id": manifest.job_id,
               "attempt": manifest.attempt, "manifest_record_sha256": manifest.to_dict()["record_sha256"]}
    ref = "handoffs/" + sha256_bytes(canonical_bytes(handoff)) + ".json"
    registry.put_immutable(ref, canonical_bytes(handoff))
    before = {p.relative_to(registry.root): p.read_bytes() for p in registry.root.rglob("*") if p.is_file()}
    media = MediaReference.from_manifest(manifest.to_dict(), handoff=handoff, handoff_ref=ref)
    composition = direct_image(TEXT, media, canvas=CANVAS, duration_frames=12, selection=selection)
    assert MediaReference.from_dict(media.to_dict()) == media
    assert media.selection_id == selection.selection_id and media.handoff_ref == ref
    result = compile_composition(composition, RegistryMediaResolver(registry))
    assert result.state == "READY"
    artifact = result.plan.sources[0].reference.artifact
    assert artifact["origin"] == "MOCK" and artifact["source_generation"] == "SYNTHETIC_FIXTURE"
    assert artifact["generation_performed"] is False and artifact["rights_status"] == "UNKNOWN"
    assert before == {p.relative_to(registry.root): p.read_bytes() for p in registry.root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("damage", ["handoff-hash", "manifest-hash", "version", "attempt-bool", "job"])
def test_malformed_a04_handoff_is_rejected(imported, selected, damage):
    _, manifests = imported
    _, _, selection, _ = selected
    manifest = manifests[0]
    handoff = {"handoff_version": 1, "selection_id": selection.selection_id, "job_id": manifest.job_id,
               "attempt": manifest.attempt, "manifest_record_sha256": manifest.to_dict()["record_sha256"]}
    if damage == "manifest-hash":
        handoff["manifest_record_sha256"] = "0" * 64
    elif damage == "version":
        handoff["handoff_version"] = True
    elif damage == "attempt-bool":
        handoff["attempt"] = True
    elif damage == "job":
        handoff["job_id"] = "other"
    ref = "handoffs/" + ("0" * 64 if damage == "handoff-hash" else sha256_bytes(canonical_bytes(handoff))) + ".json"
    with pytest.raises(CompositionValidationError):
        MediaReference.from_manifest(manifest, handoff=handoff, handoff_ref=ref)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "manifest", "request-binding"])
def test_unrealizable_source_never_compiles_as_ready(imported, tmp_path, damage):
    source_root, manifests = imported
    root = tmp_path / "copy"
    shutil.copytree(source_root, root)
    manifest = manifests[0]
    reference = MediaReference.from_manifest(manifest)
    if damage in ("missing", "corrupt"):
        path = root / reference.artifact["path"]
        path.unlink() if damage == "missing" else path.write_bytes(b"corrupted")
    elif damage == "manifest":
        path = root / reference.manifest_ref
        data = json.loads(path.read_bytes())
        data["record_sha256"] = "0" * 64
        path.write_bytes(canonical_bytes(data))
    else:
        path = root / f"jobs/{manifest.job_id}/request.json"
        path.write_bytes(b"{}")
    result = compile_composition(direct_image(TEXT, reference, canvas=CANVAS, duration_frames=1),
                                 RegistryMediaResolver(ArtifactRegistry(root)))
    assert result.state == "SOURCE_ERROR" and result.plan is None
    assert result.diagnostics[0].code == "SOURCE_RESOLUTION_FAILED"
    assert result.diagnostics[0].asset_id == reference.artifact_id


def test_injected_resolver_cannot_rebind_an_asset_and_is_not_retried():
    class Rebinding:
        calls = 0

        def resolve(self, reference):
            self.calls += 1
            return ResolvedMedia(declaration("b" * 64), "fixture://different")

    resolver = Rebinding()
    result = compile_composition(direct_image(TEXT, declaration(), canvas=CANVAS, duration_frames=1), resolver)
    assert result.state == "SOURCE_ERROR" and result.plan is None
    assert resolver.calls == 1


def test_declarations_of_new_formats_do_not_bypass_actual_parent_capabilities(tmp_path):
    content = b"explicit synthetic unsupported-format bytes"
    reference = declaration(sha256_bytes(content), mime_type="image/jpeg", size_bytes=len(content))
    registry = ArtifactRegistry(tmp_path)
    registry.put_immutable(reference.artifact["path"], content)
    result = compile_composition(direct_image(TEXT, reference, canvas=CANVAS, duration_frames=1),
                                 RegistryMediaResolver(registry))
    assert result.state == "SOURCE_ERROR" and result.plan is None
    assert "Unsupported MIME" in result.diagnostics[0].message


def test_save_edit_reopen_and_conflicts_leave_old_revisions_intact(tmp_path):
    original = create_composition(TEXT, canvas=CANVAS)
    store = CompositionStore(tmp_path)
    store.save(original)
    original_bytes = store.registry.read(store.revision_ref(original.composition_id, 1))
    proposed = edit_composition(original, intent="A different current instruction")
    assert not store.registry.exists(store.revision_ref(original.composition_id, 2))
    assert proposed.parent == CompositionParent(original.composition_id, 1, original.digest)
    child = store.save(proposed)
    assert child.composition_id == original.composition_id and child.revision == 2
    assert child.original_intent == TEXT and child.intent != original.intent
    assert child.digest != original.digest
    assert store.save(child) == child
    assert CompositionStore(tmp_path).load(original.composition_id, 2) == child
    with pytest.raises(CompositionVersionConflict):
        store.save(edit_composition(original, intent="a competing child"))
    assert store.load(original.composition_id, 2) == child
    assert store.registry.read(store.revision_ref(original.composition_id, 1)) == original_bytes
    third = store.edit(child.composition_id, 2, canvas=Canvas(1000, 1000, 60))
    assert third.revision == 3 and third.parent.digest == child.digest


def test_store_checks_missing_parent_wrong_parent_digest_and_source_rebinding(tmp_path):
    original = create_composition(TEXT, canvas=CANVAS)
    child = edit_composition(original, intent="changed")
    store = CompositionStore(tmp_path)
    with pytest.raises(ArtifactMissingError):
        store.save(child)
    store.save(original)
    with pytest.raises(CompositionValidationError, match="parent digest"):
        store.save(replace(child, parent=replace(child.parent, digest="0" * 64)))
    with pytest.raises(CompositionValidationError, match="original source"):
        store.save(replace(child, original_intent="rewritten history"))
    assert not store.registry.exists(store.revision_ref(original.composition_id, 2))


def test_conflicting_concurrent_revision_writers_have_one_winner(tmp_path):
    store = CompositionStore(tmp_path)
    original = store.save(create_composition(TEXT, canvas=CANVAS))
    children = [edit_composition(original, intent=f"choice {i}") for i in range(2)]
    barrier = Barrier(2)

    def save(child):
        barrier.wait(timeout=10)
        try:
            return store.save(child)
        except CompositionVersionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, children))
    winners = [v for v in outcomes if v is not None]
    assert len(winners) == 1
    assert store.load(original.composition_id, 2) == winners[0]


@pytest.mark.parametrize("damage", ["bytes", "digest", "parent-digest", "path-identity", "partial"])
def test_store_detects_tamper_even_when_envelope_is_rehashed(tmp_path, damage):
    store = CompositionStore(tmp_path)
    parent = store.save(create_composition(TEXT, canvas=CANVAS))
    child = store.save(edit_composition(parent, intent="child"))
    ref = store.revision_ref(child.composition_id, 2)
    data = json.loads(store.registry.read(ref))
    if damage == "bytes":
        data["composition"]["intent"] = "tampered"
    elif damage == "digest":
        data["digest"] = "0" * 64
    elif damage == "parent-digest":
        data["composition"]["parent"]["digest"] = "0" * 64
        data["digest"] = sha256_bytes(canonical_bytes(data["composition"]))
    elif damage == "path-identity":
        data["composition"]["revision"] = 3
        data["composition"]["parent"]["revision"] = 2
        data["digest"] = sha256_bytes(canonical_bytes(data["composition"]))
    path = store.registry.safe_path(ref)
    path.write_bytes(b'{"composition":' if damage == "partial" else canonical_bytes(data))
    damaged_bytes = path.read_bytes()
    with pytest.raises(CompositionValidationError):
        store.load(child.composition_id, 2)
    with pytest.raises(CompositionVersionConflict):
        store.save(child)
    assert path.read_bytes() == damaged_bytes


@pytest.mark.parametrize("changes", [{}, {"intent": TEXT}, {"revision": 9}, {"original_intent": "other"},
                                     {"selection": None}, {"parent": None}])
def test_edits_cannot_be_noops_or_rewrite_original_lineage(changes):
    with pytest.raises(CompositionValidationError):
        edit_composition(create_composition(TEXT, canvas=CANVAS), **changes)


@pytest.mark.parametrize("kwargs", [
    {"start_frame": -1}, {"start_frame": True}, {"start_frame": 0.0},
    {"duration_frames": 0}, {"duration_frames": -1}, {"duration_frames": 1.5},
    {"duration_frames": True}, {"source_start_frame": -1}, {"source_start_frame": 2},
    {"asset_id": "../escape"}, {"asset_id": "artifact:" + "G" * 64},
    {"kind": "text"}, {"kind": "audio"}, {"intent": "\ud800"},
])
def test_item_timing_and_references_are_strict(kwargs):
    args = {"item_id": "i", "kind": "image", "start_frame": 0, "duration_frames": 1,
            "asset_id": "artifact:" + "a" * 64}
    args.update(kwargs)
    with pytest.raises(CompositionValidationError):
        SceneItem(**args)


@pytest.mark.parametrize("canvas", [
    (0, 720, 24), (1280, -1, 24), (True, 720, 24), (1280.0, 720, 24),
    (1280, 720, 0), (1280, 720, True), (1280, 720, 29.97),
])
def test_canvas_requires_positive_integer_dimensions_and_exact_fps(canvas):
    with pytest.raises(CompositionValidationError):
        Canvas(*canvas)


@pytest.mark.parametrize("rate", [(0, 1), (24, 0), (-24, 1), (True, 1), (24, 1.0)])
def test_invalid_rational_fps_is_rejected(rate):
    with pytest.raises(CompositionValidationError):
        FrameRate(*rate)


@pytest.mark.parametrize("damage", ["order", "overlap", "duplicate-item", "duplicate-track",
                                   "dangling", "duplicate-media", "kind", "duration", "track-kind"])
def test_invalid_manual_timeline_fails_before_storage(damage):
    image = declaration()
    first = SceneItem("a", "image", 0, 10, image.artifact_id)
    last = SceneItem("b", "image", 10, 10, image.artifact_id)
    tracks, media, duration = [Track("v", (first, last))], [image], 20
    with pytest.raises(CompositionValidationError):
        if damage == "order":
            tracks = [Track("v", (last, first))]
        elif damage == "overlap":
            tracks = [Track("v", (first, replace(last, start_frame=9)))]
        elif damage == "duplicate-item":
            tracks = [Track("v", (first, replace(last, item_id="a")))]
        elif damage == "duplicate-track":
            tracks += [Track("v")]
        elif damage == "dangling":
            media = []
        elif damage == "duplicate-media":
            media += [image]
        elif damage == "kind":
            media = [declaration(kind="video", mime_type="video/mp4")]
        elif damage == "duration":
            duration = 19
        else:
            tracks = [Track("audio", kind="audio")]
        create_composition(TEXT, canvas=CANVAS, tracks=tracks, media=media, duration_frames=duration)


@pytest.mark.parametrize("changes", [
    {"sha256": "x" * 64}, {"artifact_id": "artifact:" + "b" * 64},
    {"path": "../escape"}, {"path": "objects/" + "a" * 64 + ":ads"},
    {"path": "objects\\" + "a" * 64}, {"mime_type": "text/plain"},
    {"mime_type": []}, {"kind": "video"}, {"size_bytes": True}, {"size_bytes": 0},
    {"size_bytes": 10.0}, {"source_snapshot_ids": "snapshot"}, {"rights_status": "VERIFIED"},
])
def test_malformed_media_declarations_are_rejected(changes):
    with pytest.raises(CompositionValidationError):
        declaration(**changes)


@pytest.mark.parametrize("changes", [
    {"manifest_ref": "../escape", "manifest_record_sha256": "0" * 64},
    {"manifest_ref": "jobs/a/attempt-0.manifest.json", "manifest_record_sha256": "0" * 64},
    {"manifest_ref": "jobs/a/attempt-1.manifest.json"},
    {"manifest_record_sha256": "0" * 64}, {"handoff_ref": "handoffs/" + "a" * 64 + ".json"},
    {"selection_id": "selection:" + "0" * 64},
])
def test_malformed_manifest_and_handoff_refs_are_rejected(changes):
    data = declaration().to_dict()
    data.update(changes)
    with pytest.raises(CompositionValidationError):
        MediaReference.from_dict(data)


@pytest.mark.parametrize("raw", [
    '{"schema_version":1,"schema_version":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}',
    "[]", "null", '{"broken":', b"\xff", '{"text":"\\ud800"}',
])
def test_strict_json_rejects_duplicates_nonfinite_and_invalid_unicode(raw):
    with pytest.raises(CompositionValidationError):
        CreativeComposition.from_json(raw)


@pytest.mark.parametrize("field,value", [
    ("schema_version", "CreativeComposition.v2"), ("revision", True),
    ("tracks", {}), ("media", {}), ("duration_frames", False),
    ("original_intent", "other"), ("composition_id", "composition:" + "0" * 64),
])
def test_malformed_serialized_composition_is_not_accepted(field, value):
    data = create_composition(TEXT, canvas=CANVAS).to_dict()
    data[field] = value
    with pytest.raises(CompositionValidationError):
        CreativeComposition.from_dict(data)


@pytest.mark.parametrize("identifier", ["../escape", "composition:../escape", "composition:" + "A" * 64, "x"])
def test_storage_identity_paths_are_not_caller_controlled(tmp_path, identifier):
    store = CompositionStore(tmp_path)
    with pytest.raises(CompositionValidationError):
        store.load(identifier, 1)
    assert not list(tmp_path.iterdir())


def test_direct_image_requires_explicit_duration_and_an_image():
    with pytest.raises(CompositionValidationError):
        direct_image(TEXT, declaration(), canvas=CANVAS)
    with pytest.raises(CompositionValidationError):
        direct_image(TEXT, declaration(kind="video", mime_type="video/mp4"), canvas=CANVAS, duration_frames=1)
    with pytest.raises(CompositionValidationError):
        SceneItem("intent", "intent", 0, 1, declaration().artifact_id, "not realized")


@pytest.mark.parametrize("selection_id", [None, True, "selection:../escape", "selection:" + "A" * 64])
def test_optional_consumer_selection_id_is_validated_when_present(selected, selection_id):
    _, _, _, payload = selected
    payload["selection"]["selection_id"] = selection_id
    with pytest.raises(CompositionValidationError):
        pinned_selection(payload["selection"])


@pytest.mark.parametrize("kwargs", [
    {"canvas": {"width": 1, "height": 1, "fps": 24}},
    {"settings": {"fit": "contain"}}, {"intent": None}, {"intent": "\ud800"},
])
def test_authoring_rejects_untyped_and_invalid_original_inputs(kwargs):
    args = {"intent": TEXT, "canvas": CANVAS}
    args.update(kwargs)
    with pytest.raises(CompositionValidationError):
        create_composition(**args)
