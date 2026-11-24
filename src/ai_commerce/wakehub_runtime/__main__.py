"""WakeHub interactive-session entrypoints."""
import argparse
import json
import os
from pathlib import Path
import sys

from .runner import (
    Result, RuntimeHold, _CODE_ROOT, diagnostic, run_native_source_only, run_read_only, run_supervisor,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Source-only WakeHub R2 ingress and R3B1 typed dependency gates for Chrome 9444. "
                    "No tab preload, UI Send, "
                    "login, scheduler, HostOps or GitHub writes.")
    sub = parser.add_subparsers(dest="command", required=True)
    supervisor = sub.add_parser(
        "supervise", help="Run serial ticks for the interactive owner on Windows.",
        description="Observe Chrome 9444 by default. An owner-controlled private runtime config may set "
                    "chrome_lifecycle to demand-start for the verified demand task, or paused to withhold starts.")
    supervisor.add_argument("--max-ticks", type=int, help="Stop after this many ticks. Omit to run until stopped.")
    supervisor.add_argument("--idle-seconds", type=int, default=30, help="Minimum delay between ticks (default 30).")
    supervisor.add_argument("--max-backoff-seconds", type=int, default=300,
                            help="Maximum delay after consecutive holds (default 300).")
    supervisor.add_argument("--dry-run", action="store_true", help="Report wiring without acquiring the receiver.")
    run = sub.add_parser("run", help="Verify one registered event and read-only readiness; exits HOLD.")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Report wiring without reading events or creating state; exits HOLD (2).")
    mode.add_argument("--diagnostic", action="store_true",
                      help="Run labeled offline synthetic adapters and real journal; no host proof.")
    run.add_argument("--native-source-only", action="store_true",
                     help="Check signed resolve/deliver grants and R3B1 authenticated origin, "
                          "USER-turn and Brain dependency gates; no UI Send. "
                          "Default remains R2 read-only; missing enrolled authority yields HOLD_DEPENDENCY.")
    run.add_argument("--journal", type=Path,
                     help="Diagnostic-only private file in an existing operator-owned directory, outside checkout.")
    for command in (run, supervisor):
        command.add_argument("--repository-root", type=Path, help="Checkout excluded from private journal storage.")
        command.add_argument("--config", type=Path,
                             help="Existing private WakeHubRuntimeR1.v1 local config; never an Issue payload.")
    args = parser.parse_args(argv)
    if args.command == "supervise":
        configured = os.environ.get("AI_COMMERCE_WAKEHUB_CONFIG")
        config = args.config if args.config is not None else Path(configured) if configured else None
        result = run_supervisor(config, args.repository_root or _CODE_ROOT, dry_run=args.dry_run,
                                max_ticks=args.max_ticks, idle_seconds=args.idle_seconds,
                                max_backoff_seconds=args.max_backoff_seconds,
                                on_health=lambda code: print(json.dumps({"chrome_health": code}),
                                                             file=sys.stderr, flush=True))
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0 if result.code == "SUPERVISOR_STOPPED" else 2
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
