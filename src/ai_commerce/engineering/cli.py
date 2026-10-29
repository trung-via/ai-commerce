"""Manual EF01 work-item CLI with bounded same-profile scoped pytest repairs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .profile import ExecutorSelection, SelectionError
from .runner import execute_attempt, DEFAULT_MAX_REPAIR_ROUNDS, MAX_REPAIR_ROUNDS, DEFAULT_ACTION_TIMEOUT_SECONDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ai_commerce.engineering", description="EF01 independent manual engineering CLI")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--executor", required=True, choices=["codex", "antigravity"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--instruction-file", required=True, type=Path)
    parser.add_argument("--verify-json", required=True, type=Path,
                        help="JSON list of targeted command argv (no shell)")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="New ignored .local/ subdir or a directory outside repository")
    parser.add_argument("--allow-path", action="append", default=[],
                        help="Allow this file or directory path (repeatable; repository-relative)")
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--verify-timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-repair-rounds", type=int, choices=range(MAX_REPAIR_ROUNDS + 1),
                        default=DEFAULT_MAX_REPAIR_ROUNDS,
                        help="Same-profile repairs after determinate pytest failure (0 disables; default: 2)")
    parser.add_argument("--action-timeout-seconds", type=int, default=DEFAULT_ACTION_TIMEOUT_SECONDS,
                        help="Total EF01 budget shared by all invocations and verifies (default: 7200)")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--antigravity-bin", default="agy")
    parser.add_argument("--git-bin", default=None, help="Direct Git executable; default: git on PATH")
    parser.add_argument("--minimum-test", action="store_true", help="Use the deterministic MT-01 AC/dependency map")
    parser.add_argument("--acceptance", action="append", default=[], help="Applicable AC ID in the trusted MT-01 map")
    parser.add_argument("--registered-only", action="store_true", help="EF02: preserve exact registered verification argv")
    parser.add_argument("--registered-profiles-json", type=Path, default=None,
                        help="EF02 trusted registry snapshot for exact registered test groups")
    parser.add_argument("--aggregate-source", action="append", default=[], help="EF02: invalidate sibling proof at fan-in")
    parser.add_argument("--pytest-profile", choices=["windows-12"], default="windows-12",
                        help="Twelve workers per eligible action (Human default)")
    parser.add_argument("--host-slots", type=int, default=1,
                        help="Observable concurrent action slots; never divides pytest workers")
    parser.add_argument("--codex-windows-sandbox", choices=["mxc"], default=None,
                        help="Per-invocation MXC override for Codex on EF02 Windows Service only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        selection = ExecutorSelection(args.executor, args.model, args.effort)
        prompt = args.instruction_file.read_text(encoding="utf-8-sig")
        verify = json.loads(args.verify_json.read_text(encoding="utf-8-sig"))
        if not isinstance(verify, list) or not all(isinstance(x, str) and x for x in verify):
            raise ValueError("verify-json must contain an argv array of non-empty strings")
        result = execute_attempt(repo=args.repo, output_dir=args.out_dir,
                                 work_id=args.work_id, attempt_id=args.attempt_id,
                                 selection=selection, instruction=prompt, verify_argv=verify,
                                 allowed_prefixes=args.allow_path,
                                 timeout_seconds=args.timeout_seconds,
                                 verify_timeout_seconds=args.verify_timeout_seconds,
                                 max_repair_rounds=args.max_repair_rounds,
                                 action_timeout_seconds=args.action_timeout_seconds,
                                 codex_bin=args.codex_bin, antigravity_bin=args.antigravity_bin,
                                 codex_windows_sandbox=args.codex_windows_sandbox,
                                 git_executable=args.git_bin, minimum_test=args.minimum_test,
                                 acceptance_ids=args.acceptance, registered_only=args.registered_only,
                                 aggregate_sources=args.aggregate_source, pytest_profile=args.pytest_profile,
                                 host_slots=args.host_slots,
                                 registered_profiles=json.loads(args.registered_profiles_json.read_text(encoding="utf-8-sig"))
                                 if args.registered_profiles_json else None)
    except (SelectionError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"EF01 INPUT/ENV ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"work_id": result["work_id"], "attempt_id": result["attempt_id"],
                      "executor": result["executor"], "model": result["model"],
                      "effort": result["reasoning_effort"], "status": result["status"],
                      "error_code": result["error_code"]}, sort_keys=True))
    return 0 if result["status"] == "BUILD_VERIFIED_FOR_SCOPE" else 1
