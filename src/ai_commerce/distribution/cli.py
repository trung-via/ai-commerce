"""A07 CLI: explicit local disk operations only; never contacts a social provider."""
from __future__ import annotations

import json
import sqlite3
import sys
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from ai_commerce.operations.manifest import strict_json

from .evaluator import evaluate
from .model import CapabilityError, validate_request, validate_snapshot
from .registry import CapabilityRegistry

_EXIT = {"ALLOW": 0, "UNKNOWN": 10, "NEEDS_RECHECK": 20, "DENY": 30}


def _read(path: Path) -> dict:
    return strict_json(path.read_bytes().removeprefix(b"\xef\xbb\xbf"))


def run(args: Namespace) -> int:
    try:
        if args.channel_operation == "validate":
            snapshot = validate_snapshot(_read(args.file))
            result = {"valid": True, "snapshot_id": snapshot["snapshot_id"]}
            status = 0
        elif args.channel_operation == "import":
            record = _read(args.file)
            sha = CapabilityRegistry(args.db).import_snapshot(record)
            result = {"snapshot_id": record["snapshot_id"], "payload_sha256": sha, "imported": True}
            status = 0
        elif args.channel_operation == "evaluate":
            req = validate_request(_read(args.request))
            when = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else None
            result = evaluate(req, CapabilityRegistry(args.db), at=when)
            status = _EXIT[result["decision"]]
        elif args.channel_operation == "history":
            req = validate_request(_read(args.request))
            rows = CapabilityRegistry(args.db).history(req)
            result = {"count": len(rows), "snapshots": rows}
            status = 0
        else:
            raise CapabilityError("UNKNOWN_CHANNEL_OPERATION")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return status
    except (CapabilityError, ValueError, OSError, UnicodeError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 40
    except sqlite3.DatabaseError as exc:
        print(f"ERROR REGISTRY_DB: {exc}", file=sys.stderr)
        return 50
