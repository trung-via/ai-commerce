"""Independent project CLI. No automatic provider accounts or remote executor."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ai_commerce.contracts.schema import SCHEMAS, ContractValidationError, validate_record
from ai_commerce.providers.mock_run import run_mock_job

from .core.identity import ProjectIdentity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-commerce", description="AI Commerce independent project CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hello", help="Check local Python CLI entrypoint")
    about = commands.add_parser("about", help="Show project identity and capability status")
    about.add_argument("--json", action="store_true", dest="as_json")
    validate = commands.add_parser("validate", help="Validate an A02 product JSON contract")
    validate.add_argument("--schema", choices=sorted(SCHEMAS), required=True)
    validate.add_argument("--file", type=Path, required=True)
    mock = commands.add_parser("mock-generate", help="Generate local mock TEXT artifact and receipt (no API)")
    mock.add_argument("--brief", type=Path, required=True)
    mock.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "hello":
        print("AI Commerce foundation ready (A01).")
        return 0
    if args.command == "about":
        info = ProjectIdentity()
        if args.as_json:
            print(json.dumps(info.to_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(f"{info.project} v{info.version} — independent foundation")
            print(f"Engineering Flow: {info.engineering_flow}")
            print(f"Product Runtime: {info.product_runtime}")
        return 0
    try:
        if args.command == "validate":
            record = json.loads(args.file.read_text(encoding="utf-8"))
            validate_record(args.schema, record)
            print(f"VALID {args.schema}: {args.file}")
            return 0
        if args.command == "mock-generate":
            result = run_mock_job(args.brief, args.out_dir)
            print(json.dumps({"job_id": result["job_id"], "status": result["status"],
                              "artifact": result["artifact"], "error_code": result["error_code"]},
                             ensure_ascii=False, sort_keys=True))
            return 0 if result["status"] == "SUCCEEDED" else 1
    except (ContractValidationError, ValueError, OSError, UnicodeError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 2
