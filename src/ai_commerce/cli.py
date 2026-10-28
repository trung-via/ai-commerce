"""Minimal local CLI. No network, credentials, AI executor or publish actions."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .core.identity import ProjectIdentity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-commerce", description="AI Commerce independent project CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hello", help="Check local Python CLI entrypoint")
    about = commands.add_parser("about", help="Show project identity and capability status")
    about.add_argument("--json", action="store_true", dest="as_json")
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
    return 2
