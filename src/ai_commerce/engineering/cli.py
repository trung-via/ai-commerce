"""Manual EF01 work-item CLI; no scheduling, automatic executor or retry."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .profile import ExecutorSelection, SelectionError
from .runner import execute_attempt


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
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--antigravity-bin", default="agy")
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
                                 codex_bin=args.codex_bin, antigravity_bin=args.antigravity_bin)
    except (SelectionError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"EF01 INPUT/ENV ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"work_id": result["work_id"], "attempt_id": result["attempt_id"],
                      "executor": result["executor"], "model": result["model"],
                      "effort": result["reasoning_effort"], "status": result["status"],
                      "error_code": result["error_code"]}, sort_keys=True))
    return 0 if result["status"] == "BUILD_VERIFIED_FOR_SCOPE" else 1
