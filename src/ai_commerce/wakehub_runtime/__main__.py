"""WakeHub interactive-session entrypoints."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .runner import (
    Result, RuntimeHold, _CODE_ROOT, _resolve_runtime_config, diagnostic, register_local_chat,
    run_native_source_only, run_read_only, run_supervisor,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WakeHub intake for exact EF02 runs and GitHub Issue creations. "
                    "Chrome 9444 readiness and locally opted-in exact-chat preload. "
                    "The supervisor supports owner-authorized local UI delivery to the registered chat.")
    sub = parser.add_subparsers(dest="command", required=True)
    supervisor = sub.add_parser(
        "supervise", help="Run serial ticks for the interactive owner on Windows.",
        description="Observe Chrome 9444 by default. An owner-controlled private runtime config may set "
                    "chrome_lifecycle to demand-start for the verified demand task, or paused to withhold effects. "
                    "Set chat_preload to true to allow dedicated-browser preload after local registration. "
                    "To explicitly opt in to Send, configure private ui_delivery with schema "
                    "WakeHubLocalUIDelivery.v3, the current route_marker, registration_id, marker_digest, "
                    "and a grants list with exact source_key and expires_ms (UTC epoch milliseconds) entries. "
                    "AGY also requires account_id for each grant it uses. "
                    "Standing intake permits an empty grants list and uses account_id from its private scope. "
                    "For GitHub issue creations, add standing_issue with repository_id, creator_id, title_prefix "
                    "(for example '[WAKEHUB PROJECT] '), not_before (UTC ISO timestamp) and account_id. "
                    "For EF02 completions add standing_ef02 with those fields plus workflow_id and batch_prefix "
                    "(for example title_prefix '[EF02] [PROJECT] ' and batch_prefix 'project_'). "
                    "Configure the existing private GitHub read token with Actions and Issues read access. "
                    "Human decision #1406 defaults v3 to transport agy. Set transport to playwright to "
                    "select the direct route. For agy, supply an agy object with the local absolute agy.exe "
                    "path (executable), executable_sha256, the absolute private workspace, and the enrolled "
                    "wakehub-executor SKILL.md digest (skill_sha256). The workspace or owner profile must "
                    "enroll wake-browser with a versioned chrome-devtools-mcp package on "
                    "http://127.0.0.1:9444. The default model is gemini-4-hydrogen-low with low effort. "
                    "The agy config accepts model, effort and timeout_seconds overrides. "
                    "Fallback requires trusted evidence of no invocation and fresh Playwright readiness. "
                    "An uncertain effect is reconciled through a read-only USER-turn observation. Existing "
                    "v2 config permits reconciliation under its original grant. New Sends require v3. "
                    "Local UI delivery ends with an observed USER turn. A Brain receipt and next work require "
                    "their own authenticated evidence.")
    supervisor.add_argument("--max-ticks", type=int,
                            help="Count all ticks, including Chrome health ticks with no eligible source.")
    supervisor.add_argument("--idle-seconds", type=int, default=30,
                            help="Normal Chrome health and source polling interval (default 30).")
    supervisor.add_argument("--max-backoff-seconds", type=int, default=300,
                            help="Maximum retry delay per source and for Chrome demand-start (default 300).")
    supervisor.add_argument("--dry-run", action="store_true", help="Report wiring without acquiring the receiver.")
    supervisor.add_argument("--observe-only", action="store_true",
                            help="Read Chrome health from the owner session using an existing runtime config "
                                 "or the private Chrome health config created by protected deployment. "
                                 "Stops on config drift and disables source admission, preload, demand start and Send.")
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
        "register", help="Enroll the owner's exact chat from a local Windows owner terminal.",
        description="For an initial Chrome-health-only install, prepare a private WakeHubRuntimeR1.v1 draft "
                    "and pass it explicitly with --config. Use the existing Chrome config, the owner-selected "
                    "exact route and its private journal. Set chat_preload explicitly. A registration-only "
                    "config can omit event targets. Open the configured chat "
                    "in the dedicated Chrome profile, prepare a private marker, manually post it as a USER "
                    "turn there, then observe using its registration ID before preparation expires. "
                    "Reuse an existing enrolled journal for that exact route if present. After enrolling "
                    "and configuring standing consent for the verified account, atomically install the completed draft "
                    "at the canonical WakeHub/runtime-config.json. The health-only instance exits on pin change. "
                    "Once it exits, approve a fresh deploy_wakehub_supervisor to use the same task.")
    actions = registration.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare", help="Print a fresh private marker for manual presentation.")
    observe = actions.add_parser("observe", help="Verify and consume that marker in the exact Chat A.")
    observe.add_argument("registration_id")
    for command in (run, supervisor, prepare, observe):
        command.add_argument("--repository-root", type=Path, help="Checkout excluded from private journal storage.")
        command.add_argument("--config", type=Path,
                             help="Existing private WakeHubRuntimeR1.v1 config, or WakeHubChromeHealth.v1 "
                                  "for supervise --observe-only. Defaults to the local process "
                                  "or Windows User setting AI_COMMERCE_WAKEHUB_CONFIG, then the WakeHub directories "
                                  "under the owner's LOCALAPPDATA/AI-Commerce.")
    args = parser.parse_args(argv)
    root = args.repository_root or _CODE_ROOT
    config = args.config
    if args.command == "run" and args.journal is not None and not (args.diagnostic or args.dry_run):
        run.error("--journal is diagnostic-only; R1 uses the private local config")
    if config is None and not (getattr(args, "diagnostic", False) or getattr(args, "dry_run", False)):
        try:
            config, _ = _resolve_runtime_config(root, observe_only=getattr(args, "observe_only", False),
                                                registration_only=args.command == "register")
        except RuntimeHold as exc:
            print(json.dumps(Result("HOLD", str(exc), "SOURCE_ONLY").to_dict(), sort_keys=True))
            return 2
    if args.command == "register":
        result = register_local_chat(config, root,
                                     registration_id=getattr(args, "registration_id", None))
        if not isinstance(result, Result):
            print(json.dumps({"code": "LOCAL_CHAT_MARKER_PREPARED", **asdict(result)}, sort_keys=True))
            return 0
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0 if result.code == "LOCAL_CHAT_REGISTERED_UI_EVIDENCE" else 2
    if args.command == "supervise":
        def report_health(code):
            if sys.stderr is not None:
                print(json.dumps({"chrome_health": code}), file=sys.stderr, flush=True)

        result = run_supervisor(config, root, dry_run=args.dry_run,
                                max_ticks=args.max_ticks, idle_seconds=args.idle_seconds,
                                max_backoff_seconds=args.max_backoff_seconds,
                                on_health=report_health,
                                observe_only=args.observe_only,
                                _deployment=globals().get("_PROTECTED_DEPLOYMENT"))
        if sys.stdout is not None:
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
        run_mode = run_native_source_only if args.native_source_only else run_read_only
        result = run_mode(config, root, dry_run=args.dry_run,
                          target_index=0 if args.target_index is None else args.target_index)
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if (result.status == "ACK" and result.code == "CONSUMER_RECEIPT_RECORDED"
                 and result.evidence_type in ("GITHUB_AUTHENTICATED", "SYNTHETIC_FIXTURE")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
