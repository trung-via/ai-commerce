"""Operator CLI for explicit D5 turns, with gated one-shot Send.

Default PREVIEW and TYPE-ONLY remain non-delivering. SEND-ONCE is disabled
unless an explicit CLI flag and new per-ledger Human token are supplied.
No route enrollment, Wake, EF02 or automatic retries. Send is NEVER invoked
on module import or during ordinary preview/staging.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import subprocess
import re

from .plugin_selection_probe_cli import (
    LocalProbeConfigError, load_registered_probe_route,
)
from .plugin_turn_handoff import TurnHandoffError, preview_next_turn
from .plugin_turn_store import TurnSessionStore
from .plugin_turn_stage_only import ExplicitTurnChipStager
from .plugin_turn_send_once import ExplicitTurnOneShotSender


def _emit(code: str, **fields) -> None:
    # Only bounded, authored status fields. Never log a chat URL or CDP port.
    print(json.dumps({"code": code, **fields}, sort_keys=True))


def _consent(token: str) -> bool:
    print("One local exact-chat D5 staging attempt. NO Send, NO Wake.")
    print("The existing ChatGPT composer must be EMPTY.")
    print("After typing, visually inspect and MANUALLY clear all unsent chips/draft.")
    try:
        return input(f"Type exactly {token} [other=abort]: ") == token
    except (EOFError, KeyboardInterrupt):
        return False


def _fresh_main_sha() -> str:
    """Freshly read main using local gh credentials without logging tokens."""
    try:
        run = subprocess.run(
            ["gh", "api", "repos/trung-via/ai-commerce/branches/main",
             "--jq", ".commit.sha"],
            capture_output=True, text=True, timeout=15, check=False)
        sha = run.stdout.strip()
        if run.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise ValueError
        return sha
    except Exception:
        raise TurnHandoffError("LIVE_SOURCE_UNAVAILABLE") from None


def _send_consent(token: str) -> bool:
    print("D5 ONE-SHOT SEND: exact enrolled chat and one message, NO RETRY.")
    print("Only proceed if structured plugin chip AND exact preview prompt are prepared.")
    print("Private ledger will enter SUBMISSION_UNKNOWN BEFORE the Send click.")
    try:
        return input(f"Type exactly {token} [other=abort]: ") == token
    except (EOFError, KeyboardInterrupt):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="D5 explicit plugin-turn LEDGER PREVIEW or ONE CHIP STAGING; never Send.")
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path,
                        help="Operator-preprovisioned private file path OUTSIDE repository")
    parser.add_argument("--mode", choices=("preview", "create-ledger", "type-only", "send-once"),
                        default="preview")
    parser.add_argument("--enable-send-once", action="store_true",
                        help="Opt-in; also requires per-turn exact Human token")
    parser.add_argument("--registry", type=Path,
                        help="Existing consented D5 route registry; required only for type-only")
    parser.add_argument("--route-id",
                        help="Existing exact route ID; required only for type-only")
    parser.add_argument("--issue-number", type=int,
                        help="Used only when creating a new ledger")
    parser.add_argument("--source-sha",
                        help="40-char source SHA; used only when creating a new ledger")
    args = parser.parse_args(argv)
    if args.mode == "send-once" and not args.enable_send_once:
        _emit("SEND_DISABLED", sent=False)
        return 2
    if (args.mode in ("type-only", "send-once") and
            (args.registry is None or args.route_id is None)):
        _emit("ROUTE_ARGS_REQUIRED")
        return 2
    if (args.mode == "create-ledger" and
            (args.issue_number is None or args.source_sha is None)):
        _emit("LEDGER_INIT_ARGS_REQUIRED")
        return 2
    try:
        ledger = TurnSessionStore(
            path=args.state_file, repository_root=args.checkout.resolve(strict=True))
        if args.mode == "create-ledger":
            if not _consent("CREATE-D5-PRIVATE-LEDGER"):
                _emit("USER_DECLINED", sent=False)
                return 2
            created = ledger.create(
                handoff_id="d5-turn-" + secrets.token_hex(16),
                issue_number=args.issue_number, source_sha=args.source_sha)
            _emit("LEDGER_CREATED", phase=created["phase"],
                  turn_index=created["turn_index"], sent=False)
            return 0
        session = ledger.read()
        preview = preview_next_turn(session)
        if args.mode == "preview":
            _emit("PREVIEW_ONLY", role=preview["role"],
                  turn_index=preview["turn_index"],
                  host_selection_proven=False, sent=False)
            return 0
        # Resolve route/consent BEFORE asking for the one-shot typing approval.
        binding = load_registered_probe_route(
            registry_path=args.registry, checkout=args.checkout,
            route_id=args.route_id)
        if args.mode == "send-once":
            if _fresh_main_sha() != session["source_sha"]:
                _emit("SOURCE_SHA_CHANGED", sent=False)
                return 2
            # A per-ledger and per-turn token, separate from creating a ledger.
            token = (f"SEND-ONCE-D5-{preview['role']}-{preview['turn_index']}-"
                     f"{session['handoff_id'][-8:]}")
            if not _send_consent(token):
                _emit("USER_DECLINED", phase="NOT_STARTED", sent=False)
                return 2
            if ledger.read() != session:
                _emit("TURN_STATE_CHANGED", phase="NOT_STARTED", sent=False)
                return 2
            fresh_binding = load_registered_probe_route(
                registry_path=args.registry, checkout=args.checkout,
                route_id=args.route_id)
            if fresh_binding != binding:
                _emit("ROUTE_STATE_CHANGED", phase="NOT_STARTED", sent=False)
                return 2
            if _fresh_main_sha() != session["source_sha"]:
                _emit("SOURCE_SHA_CHANGED", phase="NOT_STARTED", sent=False)
                return 2
            result = ExplicitTurnOneShotSender(enabled=True).send_once(
                store=ledger, expected_state=session, binding=binding,
                current_main_sha=_fresh_main_sha,
                current_binding=lambda: load_registered_probe_route(
                    registry_path=args.registry, checkout=args.checkout,
                    route_id=args.route_id),
                operator_approved=True)
            _emit(result.code, phase=result.phase, role=preview["role"],
                  turn_index=preview["turn_index"], sent=result.sent,
                  host_selection_proven=False)
            # Do not let shell auto-retry on unknown / ambiguous outcome.
            return 2
        token = f"TYPE-ONLY-D5-{preview['role']}-{preview['turn_index']}"
        if not _consent(token):
            _emit("USER_DECLINED", phase="NOT_STARTED", sent=False)
            return 2
        # Consent may take minutes. Another process can advance the ledger
        # while the operator types. Refuse a stale role or UNKNOWN handoff.
        # This bounded re-read is NOT a cross-process CDP staging lease.
        fresh = ledger.read()
        if fresh != session:
            _emit("TURN_STATE_CHANGED", phase="NOT_STARTED", sent=False)
            return 2
        # Route consent or exact-chat binding may also change during the
        # operator prompt. Re-read the private registry and fail closed.
        # This is a preflight recheck, not an OS-enforced routing lease.
        fresh_binding = load_registered_probe_route(
            registry_path=args.registry, checkout=args.checkout,
            route_id=args.route_id)
        if fresh_binding != binding:
            _emit("ROUTE_STATE_CHANGED", phase="NOT_STARTED", sent=False)
            return 2
        result = ExplicitTurnChipStager(enabled=True).stage(
            fresh_binding, fresh, operator_approved=True)
        _emit(result.code, phase=result.phase, role=preview["role"],
              turn_index=preview["turn_index"], brain=result.brain,
              engineering=result.engineering,
              host_selection_proven=False, sent=False)
        return 0 if result.code == "INPUT_STAGED_NO_SEND" else 2
    except (TurnHandoffError, LocalProbeConfigError) as exc:
        _emit(str(exc), sent=False)
        return 2
    except Exception:
        # Hide filesystem paths, private registry values, Chrome errors.
        _emit("D5_LOCAL_UNAVAILABLE", sent=False)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
