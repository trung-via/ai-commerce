"""Independent project CLI. No automatic provider accounts or remote executor."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ai_commerce.contracts.schema import SCHEMAS, ContractValidationError, validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import (
    CreativeStore, MockCreativeReasoner, generate_concepts, intake_brief, reframe_concept,
)
from ai_commerce.media import MediaProviderError, MediaRequest
from ai_commerce.operations import (
    ArtifactRegistry, AssetJobManifest, LocalMediaRunner, MediaOperationError,
)
from ai_commerce.operations.manifest import strict_json
from ai_commerce.providers.base import ProviderError
from ai_commerce.providers.mock_run import run_mock_job

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
        if args.command in {"concept-create", "concept-inspect", "concept-reframe"}:
            print(json.dumps(_creative_command(args), sort_keys=True))
            return 0
        if args.command in {"asset-run", "asset-inspect"}:
            output = _asset_command(args)
            print(json.dumps(output, sort_keys=True))
            return 0 if output["asset_manifest"]["state"] == "SUCCEEDED" else 1
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
            ProviderError, MediaProviderError, MediaOperationError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 2
