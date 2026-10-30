"""A12 Product CLI: offline snapshot intake, registry journal and advisory pack export."""
from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path

from .claims import ProductClaimEvidence
from .core import ProductError, parse_json, require
from .offers import ProductOfferObservation
from .pack import (
    ProductEvidencePack, build_evidence_pack, export_pack_from_registry,
    read_evidence_pack, write_evidence_pack,
)
from .reader import ProductListingSnapshot, read_snapshot
from .registry import ProductEvidenceRegistry
from .rights import ProductAssetRights


def add_parser(commands) -> None:
    parser = commands.add_parser("product", help="A12 offline product evidence foundation and advisory pack")
    operations = parser.add_subparsers(dest="product_operation", required=True)

    # snapshot intake
    snap = operations.add_parser("snapshot", help="Import human-supplied listing snapshot with exact SHA-256")
    snap.add_argument("--file", type=Path, required=True, help="Local CSV or JSON file")
    snap.add_argument("--format", choices=("json", "csv"), required=True)
    snap.add_argument("--source-ref", required=True)
    snap.add_argument("--captured-at", required=True)
    snap.add_argument("--source-kind", choices=("HUMAN_SUPPLIED", "PREEXPORTED_API"), default="HUMAN_SUPPLIED")
    snap.add_argument("--declared-verification", default=None)
    snap.add_argument("--synthetic", action="store_true")
    snap.add_argument("--output", type=Path, help="Optional output JSON file")

    # init-db
    init = operations.add_parser("init-db", help="Initialize an empty append-only evidence registry")
    init.add_argument("--db", type=Path, required=True)
    init.add_argument("--root", type=Path, help="Root directory boundary (defaults to db parent)")

    # append
    app = operations.add_parser("append", help="Append validated snapshot, claim, offer or rights to registry")
    app.add_argument("--db", type=Path, required=True)
    app.add_argument("--root", type=Path)
    app.add_argument("--file", type=Path, required=True, help="Contract JSON record")
    app.add_argument("--raw", type=Path, required=True, help="Raw evidence bytes")
    app.add_argument("--snapshot", type=Path, help="Snapshot JSON for claim/offer/rights")
    app.add_argument("--snapshot-raw", type=Path, help="Snapshot raw bytes if different from --raw")
    app.add_argument("--proof", type=Path, help="Reviewer proof raw file for verified claims")
    app.add_argument("--license", type=Path, help="License instrument raw file for rights review")
    app.add_argument("--purpose", help="License review purpose (COMMERCIAL_USE, etc.)")
    app.add_argument("--stream-key", required=True)
    app.add_argument("--recorded-at", required=True)
    app.add_argument("--operation", choices=("OBSERVATION", "REVISION", "CORRECTION", "COUNTEREVIDENCE"),
                     default="OBSERVATION")
    app.add_argument("--parent-event-id", default=None)
    app.add_argument("--actor-ref", default=None)
    app.add_argument("--reason-ref", default=None)

    # inspect
    insp = operations.add_parser("inspect", help="Inspect registry events and revisions")
    insp.add_argument("--db", type=Path, required=True)
    insp.add_argument("--root", type=Path)
    insp.add_argument("--stream-key", default=None)
    insp.add_argument("--event-id", default=None)

    # integrity
    integ = operations.add_parser("integrity", help="Verify hash chain integrity of the evidence registry")
    integ.add_argument("--db", type=Path, required=True)
    integ.add_argument("--root", type=Path)

    # export
    exp = operations.add_parser("export", help="Export an immutable ProductEvidencePack from registry or snapshot")
    exp.add_argument("--db", type=Path, help="Path to evidence registry SQLite database")
    exp.add_argument("--root", type=Path)
    exp.add_argument("--listing-id", help="Target listing ID")
    exp.add_argument("--variant-id", help="Target variant ID")
    exp.add_argument("--as-of", help="RFC3339 evaluation timestamp for offer freshness")
    exp.add_argument("--max-age-seconds", type=int, default=30 * 24 * 3600)
    exp.add_argument("--output", type=Path, help="Destination JSON file")

    # verify
    ver = operations.add_parser("verify", help="Strictly verify and load an exported ProductEvidencePack")
    ver.add_argument("--file", type=Path, required=True)
    ver.add_argument("--expected-id", default=None)


def run(args: Namespace) -> int:
    try:
        op = args.product_operation
        if op == "snapshot":
            root = args.file.parent.resolve()
            snapshot = read_snapshot(
                args.file,
                root=root,
                file_format=args.format,
                source_ref=args.source_ref,
                captured_at=args.captured_at,
                source_kind=args.source_kind,
                declared_verification=args.declared_verification,
                synthetic=args.synthetic,
            )
            result = snapshot.to_dict()
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_bytes(snapshot.to_bytes())
        elif op == "init-db":
            root = (args.root or args.db.parent).resolve()
            registry = ProductEvidenceRegistry(args.db, root=root)
            registry.initialize()
            result = {"status": "INITIALIZED", "db": str(args.db.resolve())}
        elif op == "append":
            root = (args.root or args.db.parent).resolve()
            registry = ProductEvidenceRegistry(args.db, root=root)
            raw = args.raw.read_bytes()
            data = parse_json(args.file.read_bytes())
            schema_ver = data.get("schema_version")
            if schema_ver == "ProductListingSnapshot.v1":
                record = ProductListingSnapshot(data, raw=raw)
            else:
                require(args.snapshot is not None, "SNAPSHOT_PATH_REQUIRED")
                s_data = parse_json(args.snapshot.read_bytes())
                s_raw = args.snapshot_raw.read_bytes() if args.snapshot_raw else raw
                snap = ProductListingSnapshot(s_data, raw=s_raw)
                if schema_ver == "ProductClaimEvidence.v1":
                    proof_raw = args.proof.read_bytes() if args.proof else None
                    record = ProductClaimEvidence(data, raw=raw, snapshot=snap, proof_raw=proof_raw)
                elif schema_ver == "ProductOfferObservation.v1":
                    record = ProductOfferObservation(data, raw=raw, snapshot=snap)
                elif schema_ver == "ProductAssetRights.v1":
                    licenses = {args.purpose: args.license.read_bytes()} if (args.license and args.purpose) else None
                    record = ProductAssetRights(data, raw=raw, snapshot=snap, license_raw=licenses)
                else:
                    raise ProductError("UNKNOWN_PRODUCT_CONTRACT")
            appended = registry.append(
                record,
                stream_key=args.stream_key,
                recorded_at=args.recorded_at,
                parent_event_id=args.parent_event_id,
                operation=args.operation,
                actor_ref=args.actor_ref,
                reason_ref=args.reason_ref,
            )
            result = {
                "event_id": appended["event"].event_id,
                "duplicate": appended["duplicate"],
                "event": appended["event"].to_dict(),
            }
        elif op == "inspect":
            root = (args.root or args.db.parent).resolve()
            registry = ProductEvidenceRegistry(args.db, root=root)
            if args.event_id:
                events = [registry.get(args.event_id)]
            elif args.stream_key:
                events = list(registry.history(args.stream_key))
            else:
                events = list(registry.events())
            result = {"events": [e.to_dict() for e in events]}
        elif op == "integrity":
            root = (args.root or args.db.parent).resolve()
            registry = ProductEvidenceRegistry(args.db, root=root)
            result = registry.integrity_check()
        elif op == "export":
            require(args.db is not None, "DB_PATH_REQUIRED")
            root = (args.root or args.db.parent).resolve()
            registry = ProductEvidenceRegistry(args.db, root=root)
            pack = export_pack_from_registry(
                registry,
                listing_id=args.listing_id,
                variant_id=args.variant_id,
                as_of=args.as_of,
                max_age_seconds=args.max_age_seconds,
            )
            if args.output:
                write_evidence_pack(args.output, pack, overwrite=True)
            result = pack.to_dict()
        elif op == "verify":
            pack = read_evidence_pack(args.file, expected_id=args.expected_id)
            result = pack.to_dict()
        else:
            raise ProductError("UNKNOWN_PRODUCT_OPERATION")

        print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except ProductError as exc:
        err = {"error": exc.code}
        if exc.row is not None:
            err["row"] = exc.row
        print(json.dumps(err, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 70
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        print(json.dumps({"error": "PRODUCT_INPUT_OR_IO_ERROR"}), file=sys.stderr)
        return 70
