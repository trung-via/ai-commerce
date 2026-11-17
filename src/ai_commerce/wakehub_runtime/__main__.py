"""One bounded interactive-session iteration; no installation or external writes."""
import argparse
import json
from pathlib import Path

from .runner import Result, RuntimeHold, diagnostic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Source-only replacement WakeHub R0. No Chrome launch, UI Send, "
                    "login, scheduler, HostOps or GitHub writes.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run one bounded iteration; native execution currently HOLDs.")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Report wiring without reading events or creating state; exits HOLD (2).")
    mode.add_argument("--diagnostic", action="store_true",
                      help="Run labeled offline synthetic adapters and real journal; no host proof.")
    run.add_argument("--journal", type=Path,
                     help="Diagnostic-only private file in an existing operator-owned directory, outside checkout.")
    run.add_argument("--repository-root", type=Path, help="Checkout excluded from private journal storage.")
    args = parser.parse_args(argv)
    if args.diagnostic:
        if args.journal is None or args.repository_root is None:
            run.error("--diagnostic requires --journal and --repository-root")
        try:
            result = diagnostic(args.journal, args.repository_root)
        except RuntimeHold as exc:
            result = Result("HOLD", str(exc), "SYNTHETIC_FIXTURE")
        except (OSError, ValueError):
            result = Result("HOLD", "DIAGNOSTIC_JOURNAL_UNAVAILABLE", "SYNTHETIC_FIXTURE")
    else:
        result = Result("HOLD" if args.dry_run else "BLOCKED_EXTERNAL",
                        "DRY_RUN_NO_EFFECTS" if args.dry_run else "UNSUPPORTED_NATIVE_OWNER",
                        "SOURCE_ONLY")
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if result.status == "ACK" and result.evidence_type == "SYNTHETIC_FIXTURE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
