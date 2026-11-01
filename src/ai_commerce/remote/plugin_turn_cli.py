"""Operator CLI for D5 *separate-turn* plugin staging. NO ChatGPT Send.

Default mode is a read-only ledger preview; type-only is a separately
consented single browser input against an already-enrolled exact route.
No route enrollment, GitHub API calls, Wake or external message delivery.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets

from .plugin_selection_probe_cli import (
    LocalProbeConfigError, load_registered_probe_route,
)
from .plugin_turn_handoff import TurnHandoffError, preview_next_turn
from .plugin_turn_store import TurnSessionStore
from .plugin_turn_stage_only import ExplicitTurnChipStager


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="D5 explicit plugin-turn LEDGER PREVIEW or ONE CHIP STAGING; never Send.")
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path,
                        help="Operator-preprovisioned private file path OUTSIDE repository")
    parser.add_argument("--mode", choices=("preview", "create-ledger", "type-only"),
                        default="preview")
    parser.add_argument("--registry", type=Path,
                        help="Existing consented D5 route registry; required only for type-only")
    parser.add_argument("--route-id",
                        help="Existing exact route ID; required only for type-only")
    parser.add_argument("--issue-number", type=int,
                        help="Used only when creating a new ledger")
    parser.add_argument("--source-sha",
                        help="40-char source SHA; used only when creating a new ledger")
    args = parser.parse_args(argv)
    if (args.mode == "type-only" and
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
