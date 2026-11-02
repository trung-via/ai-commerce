"""Read-only A15 offline status CLI: no network, retry or provider operations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence
from ai_commerce.operations.cost_ledger import CostLedger
from ai_commerce.operations.registry import ArtifactRegistry
from ai_commerce.operations.runner import LocalMediaRunner
from ai_commerce.operations.status_report import media_job_status


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.operations.a15_cli")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--cost-entry-id", action="append", default=[])
    args = parser.parse_args(argv)
    # Do not create a new empty registry while performing an inspection.
    if not args.registry.is_dir():
        parser.error("Registry does not exist")
    registry = ArtifactRegistry(args.registry)
    manifest = LocalMediaRunner(registry).load(args.job_id, args.attempt)
    result = media_job_status(manifest, CostLedger(registry),
                              tuple(args.cost_entry_id))
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
