"""Independent project CLI. No automatic provider accounts or remote executor."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ai_commerce.bridge import (
    BridgeValidationError, composition_to_render_plan,
)
from ai_commerce.composition import (
    CompositionStore, CompositionValidationError, create_composition, direct_image,
)
from ai_commerce.composition.models import Canvas as CompositionCanvas, FrameRate
from ai_commerce.composition.sources import MediaReference
from ai_commerce.contracts.schema import SCHEMAS, ContractValidationError, validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import (
    CreativeStore, MockCreativeReasoner, generate_concepts, intake_brief, reframe_concept,
)
from ai_commerce.media import MediaProviderError, MediaRequest
from ai_commerce.media.files import hash_file
from ai_commerce.operations import (
    ArtifactRegistry, AssetJobManifest, LocalMediaRunner, MediaOperationError,
)
from ai_commerce.operations.manifest import strict_json
from ai_commerce.providers.base import ProviderError
from ai_commerce.providers.mock_run import run_mock_job
from ai_commerce.render import (
    FFmpegRenderer, RenderError, RenderItem, RenderPlan, SourceMediaRef,
)
from ai_commerce.render.models import Canvas as RenderCanvas

from .core.identity import ProjectIdentity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-commerce", description="AI Commerce independent project CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hello", help="Check local Python CLI entrypoint")
    about = commands.add_parser("about", help="Show project identity and capability status")
    about.add_argument("--json", action="store_true", dest="as_json")
    validate = commands.add_parser("validate", help="Validate an A02 product JSON contract")
    validate.add_argument("--schema", choices=sorted(SCHEMAS), required=True)
    validate.add_argument("--file", type=Path, required=True)
    mock = commands.add_parser("mock-generate", help="Generate local mock TEXT artifact and receipt (no API)")
    mock.add_argument("--brief", type=Path, required=True)
    mock.add_argument("--out-dir", type=Path, required=True)

    create = commands.add_parser("concept-create", help="Save TEST_ONLY_MOCK concepts and pinned selections")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Freeform creative brief")
    source.add_argument("--brief", type=Path, help="UTF-8 prose or ContentBrief JSON file")
    create.add_argument("--store", type=Path, required=True)
    create.add_argument("--mock", action="store_true", required=True,
                        help="Explicitly use deterministic test templates; no model inference")
    create.add_argument("--count", type=int, default=1)

    for name, help_text in (
        ("concept-inspect", "Load a pinned brief/concept/selection"),
        ("concept-reframe", "Save explicit creative edits as a new pinned revision"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--selection-id", required=True)
        if name == "concept-reframe":
            command.add_argument("--lens", required=True)
            command.add_argument("--changes", type=Path, required=True,
                                 help="UTF-8 JSON object of creative text edits")

    asset = commands.add_parser("asset-run", help="Associate a selection with a local import/mock asset job")
    asset.add_argument("--store", type=Path, required=True)
    asset.add_argument("--selection-id", required=True)
    asset.add_argument("--out-dir", type=Path, required=True)
    asset.add_argument("--mode", choices=("import", "mock"), required=True)
    asset.add_argument("--source", type=Path, help="Local media file for import mode")
    asset.add_argument("--mime-type", choices=("image/png", "video/x-msvideo"))

    inspect = commands.add_parser("asset-inspect", help="Verify a saved handoff, manifest and actual asset bytes")
    inspect.add_argument("--store", type=Path, required=True)
    inspect.add_argument("--out-dir", type=Path, required=True)
    inspect.add_argument("--handoff", required=True, help="Relative handoffs/<sha256>.json reference")

    # A05 Composition & Render CLI commands
    comp_create = commands.add_parser("composition-create", help="Create and store an immutable CreativeComposition revision")
    comp_source = comp_create.add_mutually_exclusive_group(required=True)
    comp_source.add_argument("--text", help="Freeform creative intent text")
    comp_source.add_argument("--brief", type=Path, help="Brief text or ContentBrief JSON file")
    comp_source.add_argument("--from-concept", dest="selection_id", help="Selection ID from CreativeStore")
    comp_create.add_argument("--store", type=Path, help="CreativeStore path (required if using --from-concept)")
    comp_create.add_argument("--out-dir", type=Path, required=True, help="Artifact / Composition registry root")
    comp_create.add_argument("--width", type=int, default=64)
    comp_create.add_argument("--height", type=int, default=96)
    comp_create.add_argument("--fps", type=int, default=24)
    comp_create.add_argument("--duration-frames", type=int, default=24)
    comp_create.add_argument("--source", type=Path, help="Direct media file to attach")
    comp_create.add_argument("--mime-type", choices=("image/png", "image/jpeg", "image/webp"), default="image/png")
    comp_create.add_argument("--handoff", help="Relative handoffs/<sha256>.json reference")

    comp_inspect = commands.add_parser("composition-inspect", help="Load and inspect a stored CreativeComposition revision")
    comp_inspect.add_argument("--out-dir", type=Path, required=True, help="Composition store root")
    comp_inspect.add_argument("--composition-id", required=True, help="composition:<sha256> ID")
    comp_inspect.add_argument("--revision", type=int, default=1, help="Revision number (default: 1)")

    r_run = commands.add_parser("render-run", help="Render a composition or source still to playable H.264 MP4")
    r_run.add_argument("--out-dir", type=Path, required=True, help="Artifact / render registry root")
    r_target = r_run.add_mutually_exclusive_group(required=True)
    r_target.add_argument("--composition-id", help="Stored composition ID")
    r_target.add_argument("--source", type=Path, help="Direct image source file to render")
    r_run.add_argument("--revision", type=int, default=1, help="Composition revision (default: 1)")
    r_run.add_argument("--job-id", help="Optional render job ID")
    r_run.add_argument("--silence", action="store_true", help="Add 48 kHz stereo AAC silent audio stream")
    r_run.add_argument("--mime-type", choices=("image/png", "image/jpeg", "image/webp"), default="image/png")
    r_run.add_argument("--width", type=int, default=64)
    r_run.add_argument("--height", type=int, default=96)
    r_run.add_argument("--fps", type=int, default=24)
    r_run.add_argument("--duration-frames", type=int, default=24)

    r_inspect = commands.add_parser("render-inspect", help="Inspect and verify a completed render job and its MP4 output")
    r_inspect.add_argument("--out-dir", type=Path, required=True, help="Artifact / render registry root")
    r_inspect.add_argument("--job-id", required=True, help="Render job ID")


    # A06: one consumer-facing offline creative run; no implicit AI provider.
    e01 = commands.add_parser("creative-e2e", help="Offline creative video and evidence")
    e01_commands = e01.add_subparsers(dest="e01_command", required=True)
    e01_run = e01_commands.add_parser("run", help="Create an MP4 from prompt + local still image(s)")
    e01_run.add_argument("--prompt", required=True)
    e01_run.add_argument("--asset", type=Path, action="append", required=True)
    e01_run.add_argument("--output", type=Path, required=True)
    e01_run.add_argument("--width", type=int, default=64)
    e01_run.add_argument("--height", type=int, default=96)
    e01_run.add_argument("--fps", type=int, default=24)
    e01_run.add_argument("--frames", type=int, default=24, help="Frames per still image")
    e01_run.add_argument("--silence", action="store_true", help="Include AAC silent audio")
    for name in ("inspect", "verify"):
        sub = e01_commands.add_parser(name, help="Read saved E01 package" if name == "inspect" else
                                      "Reopen and verify E01 provenance and decoded MP4")
        sub.add_argument("--run", type=Path, required=True)
        if name == "verify":
            sub.add_argument("--full-decode", action="store_true",
                             help="Explicit flag; A05 verifier always full-decodes")

    return parser


def _handoff_record(selection_id: str, manifest: AssetJobManifest) -> dict:
    """Associate existing identities without changing A03/A04 provenance formats."""
    return {
        "handoff_version": 1,
        "selection_id": selection_id,
        "job_id": manifest.job_id,
        "attempt": manifest.attempt,
        "manifest_record_sha256": manifest.to_dict()["record_sha256"],
    }


def _inspect_asset(store: CreativeStore, runner: LocalMediaRunner, ref: str) -> dict:
    record = strict_json(runner.registry.read(ref))
    if ref != "handoffs/" + sha256_bytes(canonical_bytes(record)) + ".json":
        raise ValueError("HANDOFF_IDENTITY_MISMATCH")
    fields = {"handoff_version", "selection_id", "job_id", "attempt", "manifest_record_sha256"}
    if set(record) != fields or type(record["handoff_version"]) is not int or record["handoff_version"] != 1:
        raise ValueError("INVALID_HANDOFF_FIELDS_OR_VERSION")
    creative = store.load_selected(record["selection_id"])
    manifest = runner.load(record["job_id"], record["attempt"])
    if record != _handoff_record(creative["selection"]["selection_id"], manifest):
        raise ValueError("HANDOFF_MANIFEST_MISMATCH")
    return {
        "handoff_ref": ref, "handoff": record, "creative": creative,
        "asset_manifest_ref": runner.manifest_ref(manifest.job_id, manifest.attempt),
        "asset_manifest": manifest.to_dict(),
    }


def _creative_command(args: argparse.Namespace) -> dict:
    store = CreativeStore(args.store)
    if args.command == "concept-create":
        brief = intake_brief(args.brief.read_text(encoding="utf-8-sig") if args.brief else args.text)
        concepts = generate_concepts(brief, MockCreativeReasoner(), count=args.count)
        store.save_brief(brief)
        selections = []
        for concept in concepts:
            store.save_concept(concept)
            selection = store.select(concept.concept_id, concept.revision)
            selections.append(store.load_selected(selection.selection_id))
        return {"concepts": selections}
    selection = store.load_selection(args.selection_id)
    if args.command == "concept-reframe":
        concept = store.load_concept(selection.concept_id, selection.revision)
        edits = strict_json(args.changes.read_bytes().removeprefix(b"\xef\xbb\xbf"))
        reframed = reframe_concept(concept, lens=args.lens, changes=edits)
        store.save_concept(reframed)
        selection = store.select(reframed.concept_id, reframed.revision)
    return store.load_selected(selection.selection_id)


def _asset_command(args: argparse.Namespace) -> dict:
    store = CreativeStore(args.store)
    # Validate creative custody before starting a media job or creating its root.
    if args.command == "asset-run":
        selected = store.load_selected(args.selection_id)
    runner = LocalMediaRunner(ArtifactRegistry(args.out_dir))
    if args.command == "asset-inspect":
        return _inspect_asset(store, runner, args.handoff)
    manifest = runner.run(MediaRequest(mode=args.mode, source_path=args.source, mime_type=args.mime_type))
    record = _handoff_record(selected["selection"]["selection_id"], manifest)
    content = canonical_bytes(record)
    ref = "handoffs/" + sha256_bytes(content) + ".json"
    runner.registry.put_immutable(ref, content)
    return _inspect_asset(store, runner, ref)


def _composition_command(args: argparse.Namespace) -> dict:
    store = CompositionStore(args.out_dir)
    if args.command == "composition-inspect":
        comp = store.load(args.composition_id, args.revision)
        ref = store.revision_ref(comp.composition_id, comp.revision)
        return {
            "composition_ref": ref,
            "composition_id": comp.composition_id,
            "revision": comp.revision,
            "composition_digest": comp.digest,
            "unresolved_intent": comp.unresolved_intent,
            "composition": comp.to_dict(),
        }

    # composition-create
    selection_payload = None
    if getattr(args, "selection_id", None):
        if not args.store:
            raise ValueError("--store is required when --from-concept is specified")
        cstore = CreativeStore(args.store)
        selection_payload = cstore.load_selected(args.selection_id)
        brief = selection_payload.get("brief", {})
        intent_text = brief.get("prompt") or brief.get("raw_text") or brief.get("text") or "composition"
    elif getattr(args, "brief", None):
        intent_text = args.brief.read_text(encoding="utf-8-sig")
    else:
        intent_text = args.text

    canvas = CompositionCanvas(args.width, args.height, FrameRate(args.fps, 1))

    if getattr(args, "source", None):
        runner = LocalMediaRunner(store.registry)
        manifest = runner.run(MediaRequest(mode="import", source_path=args.source, mime_type=args.mime_type))
        media_ref = MediaReference.from_manifest(manifest)
        comp = direct_image(intent_text, media_ref, canvas=canvas, duration_frames=args.duration_frames, selection=selection_payload)
    elif getattr(args, "handoff", None):
        raw_handoff = strict_json(store.registry.read(args.handoff))
        runner = LocalMediaRunner(store.registry)
        manifest = runner.load(raw_handoff["job_id"], raw_handoff["attempt"])
        media_ref = MediaReference.from_manifest(manifest, handoff=raw_handoff, handoff_ref=args.handoff)
        comp = direct_image(intent_text, media_ref, canvas=canvas, duration_frames=args.duration_frames, selection=selection_payload)
    else:
        comp = create_composition(intent_text, canvas=canvas, duration_frames=args.duration_frames, selection=selection_payload)

    saved = store.save(comp)
    ref = store.revision_ref(saved.composition_id, saved.revision)
    return {
        "composition_ref": ref,
        "composition_id": saved.composition_id,
        "revision": saved.revision,
        "composition_digest": saved.digest,
        "unresolved_intent": saved.unresolved_intent,
        "composition": saved.to_dict(),
    }


def _render_command(args: argparse.Namespace) -> dict:
    registry = ArtifactRegistry(args.out_dir)
    renderer = FFmpegRenderer(registry)
    if args.command == "render-inspect":
        manifest = renderer.load(args.job_id)
        if manifest.output is not None:
            movie_path = registry.safe_path(manifest.output["ref"])
            sha, size = hash_file(movie_path)
            if (sha, size) != (manifest.output["sha256"], manifest.output["size_bytes"]):
                raise ValueError("Rendered MP4 output file bytes corrupted or modified")
        return {
            "job_id": manifest.job_id,
            "manifest_ref": FFmpegRenderer.manifest_ref(manifest.job_id),
            "state": manifest.state,
            "output": manifest.output,
            "thumbnail": manifest.thumbnail,
            "render_manifest": manifest.to_dict(),
        }

    # render-run
    if getattr(args, "composition_id", None):
        comp_store = CompositionStore(args.out_dir)
        comp = comp_store.load(args.composition_id, args.revision)
        ref = comp_store.revision_ref(comp.composition_id, comp.revision)
        plan = composition_to_render_plan(comp, silence=args.silence, composition_ref=ref)
    elif getattr(args, "source", None):
        runner = LocalMediaRunner(registry)
        manifest = runner.run(MediaRequest(mode="import", source_path=args.source, mime_type=args.mime_type))
        source_ref = SourceMediaRef(
            canonical_bytes(manifest.result["artifact"]),
            runner.manifest_ref(manifest.job_id, manifest.attempt),
            manifest.to_dict()["record_sha256"],
        )
        canvas = RenderCanvas(args.width, args.height, args.fps)
        plan = RenderPlan(
            canvas=canvas,
            items=(RenderItem(source_ref, 0, args.duration_frames),),
            silence=args.silence,
        )
    else:
        raise ValueError("Either --composition-id or --source is required")

    result = renderer.render(plan, job_id=args.job_id)
    return {
        "job_id": result.job_id,
        "manifest_ref": FFmpegRenderer.manifest_ref(result.job_id),
        "state": result.state,
        "output": result.output,
        "thumbnail": result.thumbnail,
        "render_manifest": result.to_dict(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "hello":
        print("AI Commerce foundation ready (A01).")
        return 0
    if args.command == "about":
        info = ProjectIdentity()
        if args.as_json:
            print(json.dumps(info.to_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(f"{info.project} v{info.version} — independent foundation")
            print(f"Engineering Flow: {info.engineering_flow}")
            print(f"Product Runtime: {info.product_runtime}")
        return 0
    try:
        if args.command == "creative-e2e":
            from ai_commerce.e2e import inspect as e01_inspect, run as e01_run, verify as e01_verify
            if args.e01_command == "run":
                result = e01_run(args.prompt, args.asset, args.output,
                                 width=args.width, height=args.height, fps=args.fps,
                                 frames=args.frames, silence=args.silence)
            elif args.e01_command == "inspect":
                result = e01_inspect(args.run)
            else:
                result = e01_verify(args.run)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result["state"] == "DELIVERED" else 1
        if args.command in {"concept-create", "concept-inspect", "concept-reframe"}:
            print(json.dumps(_creative_command(args), sort_keys=True))
            return 0
        if args.command in {"asset-run", "asset-inspect"}:
            output = _asset_command(args)
            print(json.dumps(output, sort_keys=True))
            return 0 if output["asset_manifest"]["state"] == "SUCCEEDED" else 1
        if args.command in {"composition-create", "composition-inspect"}:
            output = _composition_command(args)
            print(json.dumps(output, sort_keys=True))
            return 0
        if args.command in {"render-run", "render-inspect"}:
            output = _render_command(args)
            print(json.dumps(output, sort_keys=True))
            return 0 if output["state"] == "SUCCEEDED" else 1
        if args.command == "validate":
            record = json.loads(args.file.read_text(encoding="utf-8"))
            validate_record(args.schema, record)
            print(f"VALID {args.schema}: {args.file}")
            return 0
        if args.command == "mock-generate":
            result = run_mock_job(args.brief, args.out_dir)
            print(json.dumps({"job_id": result["job_id"], "status": result["status"],
                              "artifact": result["artifact"], "error_code": result["error_code"]},
                             ensure_ascii=False, sort_keys=True))
            return 0 if result["status"] == "SUCCEEDED" else 1
    except (ContractValidationError, ValueError, OSError, UnicodeError,
            ProviderError, MediaProviderError, MediaOperationError,
            RenderError, CompositionValidationError, BridgeValidationError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 2
