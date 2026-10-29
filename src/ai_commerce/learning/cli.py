"""A11 offline commands. Only explicit append writes the independent registry."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_commerce.analytics.model import AnalyticsError, parse_json

from .core import LearningError, require
from .export import LearningAdvisory, export_advisory, read_document, write_advisory
from .reader import ObservationSnapshot, SnapshotPin, read_snapshot
from .registry import MAX_ENTRY_BYTES, MAX_REPORTS, MAX_SNAPSHOTS, LearningRegistry
from .reporting import PerformanceReport, build_performance_report, compare_reports


def add_parser(commands) -> None:
    parser = commands.add_parser("learning", help="A11 offline descriptive reports and advisory learning")
    operations = parser.add_subparsers(dest="learning_operation", required=True)
    snapshot = operations.add_parser("snapshot", help="Read an existing A09 import with an exact caller pin")
    snapshot.add_argument("--db", type=Path, required=True)
    snapshot.add_argument("--pin", type=Path, required=True, help="SnapshotPin JSON, including ordered observation IDs")
    report = operations.add_parser("report", help="Describe explicit immutable snapshots")
    report.add_argument("--snapshot", type=Path, action="append", required=True)
    report.add_argument("--metric-key")
    report.add_argument("--aggregation", choices=("SUM", "MEAN", "COUNT"), default="SUM")
    report.add_argument("--denominator-value", type=float)
    report.add_argument("--as-of")
    report.add_argument("--stale-after-days", type=int)
    compare = operations.add_parser("compare", help="Strict descriptive comparison; no causal claim")
    compare.add_argument("--left", type=Path, required=True)
    compare.add_argument("--right", type=Path, required=True)
    append = operations.add_parser("append", help="Append an explicitly authored learning revision to A11")
    append.add_argument("--db", type=Path, required=True)
    append.add_argument("--file", type=Path, required=True, help="Sealed hypothesis, evidence, record and revision JSON")
    append.add_argument("--snapshot", type=Path, action="append", required=True)
    append.add_argument("--report", type=Path, action="append", default=[])
    inspect = operations.add_parser("inspect", help="Read verified history; never creates a database")
    inspect.add_argument("--db", type=Path, required=True)
    inspect.add_argument("--learning-key")
    inspect.add_argument("--revision", type=int)
    export = operations.add_parser("export", help="Export complete redacted advisory history for consultation")
    export.add_argument("--db", type=Path, required=True)
    export.add_argument("--output", type=Path, help="Create a new JSON file; existing files are never replaced")
    reload = operations.add_parser("reload", help="Validate archived advisory JSON without importing or acting")
    reload.add_argument("--file", type=Path, required=True)
    reload.add_argument("--expected-id", help="Separately retained a11-advisory hash identity")


def _snapshots(paths) -> list[ObservationSnapshot]:
    require(1 <= len(paths) <= MAX_SNAPSHOTS, "SNAPSHOT_LIMIT")
    return [ObservationSnapshot.from_bytes(read_document(path)) for path in paths]


def run(args) -> int:
    try:
        operation = args.learning_operation
        if operation == "snapshot":
            value = parse_json(read_document(args.pin, limit=256 * 1024))
            require(type(value) is dict and set(value) == {"import_id", "context_sha256", "raw_sha256", "observation_ids"}
                    and type(value["observation_ids"]) is list, "INVALID_SNAPSHOT_PIN")
            pin = SnapshotPin(value["import_id"], value["context_sha256"], value["raw_sha256"],
                              tuple(value["observation_ids"]))
            result = read_snapshot(args.db, pin=pin).to_dict()
        elif operation == "report":
            require(args.stale_after_days is None or (args.as_of is not None and args.stale_after_days >= 0),
                    "INVALID_STALENESS_POLICY")
            result = build_performance_report(_snapshots(args.snapshot), metric_key=args.metric_key,
                aggregation=args.aggregation, denominator_value=args.denominator_value,
                as_of=args.as_of, stale_after_days=args.stale_after_days).to_dict()
        elif operation == "compare":
            result = compare_reports(PerformanceReport.from_bytes(read_document(args.left)),
                                     PerformanceReport.from_bytes(read_document(args.right))).to_dict()
        elif operation == "append":
            value = parse_json(read_document(args.file, limit=MAX_ENTRY_BYTES))
            require(type(value) is dict and set(value) == {"hypothesis", "evidence", "record", "revision"},
                    "INVALID_LEARNING_APPEND_PACKET")
            require(len(args.report) <= MAX_REPORTS, "REPORT_LIMIT")
            result = LearningRegistry(args.db).append(**value, snapshots=_snapshots(args.snapshot),
                reports=[PerformanceReport.from_bytes(read_document(path)) for path in args.report]).to_dict()
        elif operation == "inspect":
            require(args.revision is None or args.learning_key is not None, "LEARNING_KEY_REQUIRED")
            registry = LearningRegistry(args.db)
            if args.revision is not None:
                entries = (registry.get(args.learning_key, revision=args.revision),)
            elif args.learning_key is not None:
                entries = registry.history(args.learning_key)
            else:
                entries = registry.inspect()
            result = {"entries": [{"sequence": e.sequence, "event_hash": e.event_hash, "entry": e.to_dict()}
                                  for e in entries]}
        elif operation == "export":
            advisory = export_advisory(LearningRegistry(args.db))
            if args.output is not None:
                write_advisory(args.output, advisory)
            result = advisory.to_dict()
        else:
            require(operation == "reload", "UNKNOWN_LEARNING_OPERATION")
            result = LearningAdvisory.from_bytes(read_document(args.file), expected_id=args.expected_id).to_dict()
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except (LearningError, AnalyticsError) as exc:
        print(json.dumps({"error": exc.code}), file=sys.stderr)
        return 60
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        print(json.dumps({"error": "LEARNING_INPUT_OR_IO_ERROR"}), file=sys.stderr)
        return 60
