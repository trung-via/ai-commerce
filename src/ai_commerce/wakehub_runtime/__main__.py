"""WakeHub interactive-session entrypoints."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from .runner import (
    Result, RuntimeHold, _CODE_ROOT, diagnostic, probe_local_ui_account, register_local_chat,
    run_native_source_only, run_read_only, run_supervisor,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WakeHub intake for exact EF02 runs and GitHub Issue creations. "
                    "Chrome 9444 readiness and locally opted-in exact-chat preload. "
                    "Scoped UI Send is available only through the supervisor with enrolled local evidence.")
    sub = parser.add_subparsers(dest="command", required=True)
    supervisor = sub.add_parser(
        "supervise", help="Run serial ticks for the interactive owner on Windows.",
        description="Observe Chrome 9444 by default. An owner-controlled private runtime config may set "
                    "chrome_lifecycle to demand-start for the verified demand task, or paused to withhold effects. "
                    "Set chat_preload to true to allow dedicated-browser preload after local registration. "
                    "Use register account for the current local UI scope before opting in to exact source grants. "
                    "Without private ui_delivery consent, Send is disabled.")
    supervisor.add_argument("--max-ticks", type=int,
                            help="Count all ticks, including Chrome health ticks with no eligible source.")
    supervisor.add_argument("--idle-seconds", type=int, default=30,
                            help="Normal Chrome health and source polling interval (default 30).")
    supervisor.add_argument("--max-backoff-seconds", type=int, default=300,
                            help="Maximum retry delay per source and for Chrome demand-start (default 300).")
    supervisor.add_argument("--dry-run", action="store_true", help="Report wiring without acquiring the receiver.")
    run = sub.add_parser("run", help="Verify one registered event and read-only readiness; exits HOLD.")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Report wiring without reading events or creating state; exits HOLD (2).")
    mode.add_argument("--diagnostic", action="store_true",
                      help="Run labeled offline synthetic adapters and real journal; no host proof.")
    run.add_argument("--native-source-only", action="store_true",
                     help="For EF02, check signed resolve/deliver grants and R3B1 authenticated origin, "
                          "USER-turn and Brain dependency gates; no UI Send. "
                          "Default remains R2 read-only; missing enrolled authority yields HOLD_DEPENDENCY.")
    run.add_argument("--journal", type=Path,
                     help="Diagnostic-only private file in an existing operator-owned directory, outside checkout.")
    run.add_argument("--target-index", type=int,
                     help="Select a zero-based entry in the private registered_targets list (default 0).")
    registration = sub.add_parser(
        "register", help="Enroll exact Chat A from a local Windows owner terminal.",
        description="Set chat_preload to false or true in the private config. Open the configured Chat A "
                    "in the dedicated Chrome profile, prepare a private marker, manually post it as a USER "
                    "turn there, then observe using its registration ID before it expires.")
    actions = registration.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare", help="Print a fresh private marker for manual presentation.")
    observe = actions.add_parser("observe", help="Verify and consume that marker in the exact Chat A.")
    observe.add_argument("registration_id")
    account = actions.add_parser(
        "account", help="Read the enrolled Chat A account scope and print private commitments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="FLAT07 read-only probe for the owner in the enrolled Windows desktop and Chrome profile. "
                    "After register observe succeeds, keep the exact Chat A tab and manually open its nonmodal "
                    "Account or Profile menu. Run this command with the menu visible and the unobstructed "
                    "composer focused. "
                    "The reader requires a single visible 'Signed in as: <address>' text row in the app menu "
                    "controlled by its named Account or Profile button. Live support for that surface is unverified. "
                    "Repeat with the menu closed to verify evidence becomes unavailable. Reopen it to verify the "
                    "same commitment. If the menu is missing, ambiguous, or cannot stay visible with the composer "
                    "unobstructed, keep delivery on HOLD.",
        epilog="Keep the output in the owner terminal. Confirm the visible account is the one you intend to "
               "authorize, then copy "
               "the five commitment fields into private ui_delivery with schema WakeHubLocalUIDelivery.v1. "
               "session_sha256 is null when visible session identity is unavailable. Add grants only for "
               "the exact source events you authorize. Use their source_key from the registered source route "
               "result and an expires_ms UTC epoch deadline. This probe creates no grant and stages no turn.")
    for command in (run, supervisor, prepare, observe, account):
        command.add_argument("--repository-root", type=Path, help="Checkout excluded from private journal storage.")
        command.add_argument("--config", type=Path,
                             help="Existing private WakeHubRuntimeR1.v1 local config; never an Issue payload.")
    args = parser.parse_args(argv)
    if args.command == "register":
        configured = os.environ.get("AI_COMMERCE_WAKEHUB_CONFIG")
        config = args.config if args.config is not None else Path(configured) if configured else None
        if args.action == "account":
            result = probe_local_ui_account(config, args.repository_root or _CODE_ROOT)
            print(json.dumps(result.to_dict(), sort_keys=True))
            return 0 if result.code == "LOCAL_UI_ACCOUNT_SCOPE_OBSERVED" else 2
        result = register_local_chat(config, args.repository_root or _CODE_ROOT,
                                     registration_id=getattr(args, "registration_id", None))
        if not isinstance(result, Result):
            print(json.dumps({"code": "LOCAL_CHAT_MARKER_PREPARED", **asdict(result)}, sort_keys=True))
            return 0
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0 if result.code == "LOCAL_CHAT_REGISTERED_UI_EVIDENCE" else 2
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
        if args.target_index is not None:
            run.error("--target-index cannot be combined with --diagnostic")
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
        result = run_mode(config, args.repository_root or _CODE_ROOT, dry_run=args.dry_run,
                          target_index=0 if args.target_index is None else args.target_index)
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if (result.status == "ACK" and result.code == "CONSUMER_RECEIPT_RECORDED"
                 and result.evidence_type in ("GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
