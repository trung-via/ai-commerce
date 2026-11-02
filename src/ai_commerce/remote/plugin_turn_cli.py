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
from .plugin_turn_evidence import GitHubTurnEvidenceAuthority


def _emit(code: str, **fields) -> None:
    # Only bounded, authored status fields. Never log a chat URL or CDP port.
    print(json.dumps({"code": code, **fields}, sort_keys=True))


def _consent(token: str, *, preserve_draft: bool = False) -> bool:
    print("One local exact-chat D5 staging attempt. NO Send, NO Wake.")
    print("The existing ChatGPT composer must be EMPTY.")
    if preserve_draft:
        print("After preparing the exact chip/prompt, inspect before a separately approved Send.")
        print("Do not clear the draft unless aborting; no Send occurs in prepare-turn.")
    else:
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


def _consent_observation(token: str) -> bool:
    try:
        return input(f"Confirm real UI reply; type exactly {token} [other=abort]: ") == token
    except (EOFError, KeyboardInterrupt):
        return False


def _gh_token() -> str:
    # One bounded read-only credential lookup; no token appears in output.
    try:
        run = subprocess.run(
            ["gh", "auth", "token"], capture_output=True,
            text=True, timeout=15, check=False)
        token = run.stdout.strip()
        if (run.returncode != 0 or not 1 <= len(token) <= 4096 or
                any(c in token for c in "\r\n\x00")):
            raise ValueError
        return token
    except Exception:
        raise TurnHandoffError("GITHUB_AUTH_UNAVAILABLE") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="D5 preview, chip staging, or separately approved guarded one-shot Send.")
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path,
                        help="Operator-preprovisioned private file path OUTSIDE repository")
    parser.add_argument("--mode", choices=("preview", "create-ledger", "type-only", "prepare-turn", "send-once", "observe-reply"),
                        default="preview")
    parser.add_argument("--comment-ref", help="Exact GitHub Issue comment URL for a reconciled UI response")
    parser.add_argument("--expected-author-id", type=int, help="Operator-pinned trusted GitHub account numeric ID")
    parser.add_argument("--enable-send-once", action="store_true",
                        help="Opt-in; also requires per-turn exact Human token")
    parser.add_argument("--registry", type=Path,
                        help="Consented D5 route registry; required for type-only or send-once")
    parser.add_argument("--route-id",
                        help="Existing exact route ID; required for type-only or send-once")
    parser.add_argument("--issue-number", type=int,
                        help="Used only when creating a new ledger")
    parser.add_argument("--source-sha",
                        help="40-char source SHA; used only when creating a new ledger")
    args = parser.parse_args(argv)
    if args.mode == "send-once" and not args.enable_send_once:
        _emit("SEND_DISABLED", sent=False)
        return 2
    if (args.mode in ("type-only", "prepare-turn", "send-once") and
            (args.registry is None or args.route_id is None)):
        _emit("ROUTE_ARGS_REQUIRED")
        return 2
    if (args.mode == "observe-reply" and
            (not args.comment_ref or args.expected_author_id is None or args.expected_author_id < 1)):
        _emit("OBSERVATION_ARGS_REQUIRED")
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
        if args.mode == "observe-reply":
            if session["phase"] != "SUBMISSION_UNKNOWN":
                _emit("TURN_RECONCILIATION_BLOCKED", sent=False)
                return 2
            if _fresh_main_sha() != session["source_sha"]:
                _emit("SOURCE_SHA_CHANGED", sent=False)
                return 2
            token = (f"OBSERVE-UI-D5-{session['turn_index']}-"
                     f"{session['handoff_id'][-8:]}")
            print("Verify the real ChatGPT UI reply yourself; a GitHub comment is NOT a server/plugin receipt.")
            if not _consent_observation(token):
                _emit("USER_DECLINED", sent=False)
                return 2
            if ledger.read() != session or _fresh_main_sha() != session["source_sha"]:
                _emit("TURN_STATE_CHANGED", sent=False)
                return 2
            authority = GitHubTurnEvidenceAuthority(
                token_provider=_gh_token, expected_author_id=args.expected_author_id)
            updated = ledger.observe_verified_reply(
                issue_comment_ref=args.comment_ref,
                authority=authority, operator_verified=True)
            _emit("UI_REPLY_RECONCILED_ONLY", phase=updated["phase"],
                  turn_index=updated["turn_index"], sent=False,
                  host_selection_proven=False)
            return 0
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
        token = (f"PREPARE-D5-{preview['role']}-{preview['turn_index']}"
                 if args.mode == "prepare-turn" else
                 f"TYPE-ONLY-D5-{preview['role']}-{preview['turn_index']}")
        if not _consent(token, preserve_draft=args.mode == "prepare-turn"):
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
            fresh_binding, fresh, operator_approved=True,
            **({"include_prompt": True} if args.mode == "prepare-turn" else {}))
        _emit(result.code, phase=result.phase, role=preview["role"],
              turn_index=preview["turn_index"], brain=result.brain,
              engineering=result.engineering,
              host_selection_proven=False, sent=False)
        return 0 if result.code in ("INPUT_STAGED_NO_SEND", "TURN_PREPARED_NO_SEND") else 2
    except (TurnHandoffError, LocalProbeConfigError) as exc:
        _emit(str(exc), sent=False)
        return 2
    except Exception:
        # Hide filesystem paths, private registry values, Chrome errors.
        _emit("D5_LOCAL_UNAVAILABLE", sent=False)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
