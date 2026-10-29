"""python -m ai_commerce.opportunity.audience: offline JSON in/out, no shared CLI edits."""
from __future__ import annotations

import argparse
import json

from ..core.models import OpportunityError
from ..signals.files import read_local
from ..signals.ledger import SignalLedger
from .ledger import AudienceLedger
from .models import MAX_NOTES_BYTES, prepare_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optional offline qualitative audience insights")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "append", "inspect", "history"):
        command = sub.add_parser(name)
        command.add_argument("--signals", help="Existing M01 SQLite journal, opened read-only")
        if name in ("validate", "append"):
            command.add_argument("--file", required=True, help="Local AudienceNotes.v1 JSON")
        if name != "validate":
            command.add_argument("--ledger", required=True, help="Independent audience SQLite journal")
        if name == "append":
            command.add_argument("--revision-of")
        if name in ("inspect", "history"):
            command.add_argument("--insight-id", required=True)
    args = parser.parse_args(argv)
    signals = SignalLedger(args.signals) if args.signals else None
    try:
        if args.command == "validate":
            result = prepare_bytes(read_local(args.file, limit=MAX_NOTES_BYTES), signal_ledger=signals).record
        else:
            ledger = AudienceLedger(args.ledger)
            if args.command == "append":
                result = ledger.append_file(args.file, signal_ledger=signals, revision_of=args.revision_of)
            elif args.command == "inspect":
                result = ledger.get(args.insight_id, signal_ledger=signals)
            else:
                result = ledger.history(args.insight_id, signal_ledger=signals)
    except OpportunityError as exc:
        print(json.dumps({"error": exc.code}))
        return 2
    # ASCII escaping keeps Unicode portable even on legacy Windows console encodings.
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
