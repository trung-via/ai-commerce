"""A09 CLI: explicit manual files and exact, account/window-scoped local history."""
from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

from .importer import DELIMITERS, load_mapping, load_plan, validate_file
from .ledger import AnalyticsLedger
from .model import AnalyticsError, LedgerError, require
from .profiles import PROFILE_IDS
from .schemas import SOURCE


def add_parser(commands) -> None:
    analytics = commands.add_parser("analytics", help="A09 offline manual analytics and raw evidence")
    operations = analytics.add_subparsers(dest="analytics_operation", required=True)
    for name in ("validate", "import"):
        parser = operations.add_parser(name, help="Validate the entire input offline" if name == "validate"
                                       else "Atomically append raw evidence and normalized observations")
        parser.add_argument("--plan", type=Path, required=True, help="MeasurementPlan.v1 JSON; explicit window/timezone")
        mapping = parser.add_mutually_exclusive_group(required=True)
        mapping.add_argument("--profile", choices=PROFILE_IDS, help="Application manual format; no verified native export layout")
        mapping.add_argument("--mapping", type=Path, help="Explicit versioned column mapping JSON")
        parser.add_argument("--dialect", choices=tuple(DELIMITERS), help="CSV delimiter; comma if omitted; never guessed")
        parser.add_argument("--source-kind", choices=SOURCE["properties"]["source_kind"]["enum"],
                            default=None, help="User declaration only; never provider attestation")
        if name == "validate":
            parser.add_argument("--file", type=Path, required=True)
            parser.add_argument("--format", choices=("csv", "json"), required=True)
        else:
            source = parser.add_mutually_exclusive_group(required=True)
            source.add_argument("--file", type=Path, help="Local UTF-8 CSV/BOM or canonical JSON")
            source.add_argument("--reprocess-of", help="Replay original ledger bytes with a revised mapping/plan")
            parser.add_argument("--format", choices=("csv", "json"), help="Required with --file; reprocess retains original format")
            parser.add_argument("--correction-of", help="Prior import ID in the same exact context; original stays immutable")
            parser.add_argument("--db", type=Path, required=True)
    inspect = operations.add_parser("inspect", help="Reopen raw evidence, verify hashes and replay observations")
    inspect.add_argument("--db", type=Path, required=True)
    inspect.add_argument("--import-id", required=True)
    history = operations.add_parser("history", help="Verified import history for the plan's exact account/window scope")
    history.add_argument("--db", type=Path, required=True)
    history.add_argument("--plan", type=Path, required=True)
    history.add_argument("--limit", type=int, default=20, help="1..100 records")
    history.add_argument("--offset", type=int, default=0)


def run(args: Namespace) -> int:
    try:
        operation = args.analytics_operation
        if operation == "inspect":
            result = AnalyticsLedger(args.db).inspect(args.import_id)
        elif operation == "history":
            result = AnalyticsLedger(args.db).history(plan=load_plan(args.plan), limit=args.limit, offset=args.offset)
        else:
            plan = load_plan(args.plan)
            profile = args.profile if args.profile else load_mapping(args.mapping)
            if operation == "import" and args.reprocess_of:
                require(not args.correction_of and args.format is None and args.source_kind is None,
                        "REPROCESS_RETAINS_SOURCE_CONTEXT")
                result = AnalyticsLedger(args.db).reprocess(args.reprocess_of, plan=plan, profile=profile,
                                                           dialect=args.dialect)
            else:
                require(args.format is not None, "FORMAT_REQUIRED")
                kwargs = {"plan": plan, "profile": profile, "file_format": args.format,
                          "dialect": args.dialect, "source_kind": args.source_kind or "USER_AUTHORED_MANUAL"}
                if operation == "validate":
                    prepared = validate_file(args.file, **kwargs)
                    result = {"import": prepared.record, "observations": list(prepared.observations)}
                else:
                    result = AnalyticsLedger(args.db).import_file(args.file, correction_of=args.correction_of, **kwargs)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except LedgerError as exc:
        print(json.dumps({"error": exc.code}), file=sys.stderr)
        return 50
    except AnalyticsError as exc:
        print(json.dumps({"error": exc.code, "row": exc.row}), file=sys.stderr)
        return 40
    except (OSError, ValueError, UnicodeError, RecursionError):
        print(json.dumps({"error": "ANALYTICS_INPUT_OR_IO_ERROR"}), file=sys.stderr)
        return 40
