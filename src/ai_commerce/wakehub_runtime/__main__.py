"""One bounded interactive-session iteration; no installation or external writes."""
import argparse
import json
import os
from pathlib import Path

from .runner import Result, RuntimeHold, _CODE_ROOT, diagnostic, run_native_source_only, run_read_only


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Source-only WakeHub R2 ingress and R3A dependency gates for Chrome 9444. "
                    "No Chrome launch, tab preload, UI Send, "
                    "login, scheduler, HostOps or GitHub writes.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Verify one registered event and read-only readiness; exits HOLD.")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Report wiring without reading events or creating state; exits HOLD (2).")
    mode.add_argument("--diagnostic", action="store_true",
                      help="Run labeled offline synthetic adapters and real journal; no host proof.")
    run.add_argument("--native-source-only", action="store_true",
                     help="Check R3A exact deliver_wake grant and dependency gates; no UI Send. "
                          "Default remains R2 read-only; missing enrolled authority yields HOLD_DEPENDENCY.")
    run.add_argument("--journal", type=Path,
                     help="Diagnostic-only private file in an existing operator-owned directory, outside checkout.")
    run.add_argument("--repository-root", type=Path, help="Checkout excluded from private journal storage.")
    run.add_argument("--config", type=Path,
                     help="Existing private WakeHubRuntimeR1.v1 local config; never an Issue payload.")
    args = parser.parse_args(argv)
    if args.diagnostic:
        if args.native_source_only:
            run.error("--native-source-only cannot be combined with --diagnostic")
        if args.config is not None:
            run.error("--config cannot be combined with --diagnostic")
        if args.journal is None or args.repository_root is None:
            run.error("--diagnostic requires --journal and --repository-root")
        try:
            result = diagnostic(args.journal, args.repository_root)
        except RuntimeHold as exc:
            result = Result("HOLD", str(exc), "SYNTHETIC_FIXTURE")
        except (OSError, ValueError):
            result = Result("HOLD", "DIAGNOSTIC_JOURNAL_UNAVAILABLE", "SYNTHETIC_FIXTURE")
    else:
        if args.journal is not None and not args.dry_run:
            run.error("--journal is diagnostic-only; R1 uses the private local config")
        configured = os.environ.get("AI_COMMERCE_WAKEHUB_CONFIG")
        config = args.config if args.config is not None else Path(configured) if configured else None
        run_mode = run_native_source_only if args.native_source_only else run_read_only
        result = run_mode(config, args.repository_root or _CODE_ROOT, dry_run=args.dry_run)
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if (result.status == "ACK" and result.code == "CONSUMER_RECEIPT_RECORDED"
                 and result.evidence_type in ("GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
