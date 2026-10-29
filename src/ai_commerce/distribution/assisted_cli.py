"""A08 CLI routing. No posting, OAuth or external validation."""
from __future__ import annotations

import json
import sqlite3
import sys
from argparse import Namespace

from ai_commerce.e2e import E01Error
from .assisted import DistributionError, export_package, inspect_package
from .model import CapabilityError
from .receipt import PublicationJournal


def run(args: Namespace) -> int:
    try:
        op = args.distribution_operation
        if op == "export":
            result = export_package(
                args.source, args.output, platform=args.platform, surface=args.surface,
                account=args.account, caption=args.caption, title=args.title,
                hashtags=args.hashtag, market=args.market, capability_db=args.capability_db)
        elif op == "inspect":
            result = inspect_package(args.package)
        elif op == "handoff":
            result = PublicationJournal(args.db).create(
                args.package, idempotency_key=args.idempotency_key)
        elif op == "event":
            result = PublicationJournal(args.db).record(
                args.publication_id, args.type, evidence_file=args.evidence,
                provider_post_id=args.post_id, public_url=args.url)
        elif op == "status":
            result = PublicationJournal(args.db).get(args.publication_id)
        else:
            raise DistributionError("UNKNOWN_DISTRIBUTION_COMMAND")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (DistributionError, CapabilityError, E01Error, sqlite3.DatabaseError,
            ValueError, OSError, UnicodeError) as exc:
        print("ERROR " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2
