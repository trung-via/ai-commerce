"""Stable CLI: validate (structure only), submit, reconcile, report, broker, poll."""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import sys
import time

from .auth import verify_release
from .broker import serve
from .contract import MAX_WIRE, Denied, canonical, decode, deny, digest, envelope, receipt
from .security import load_config, windows_identity
from .transport import GitHub, poll_inbox, poll_once, report, submit, validation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "submit", "reconcile", "report", "broker", "poll-once", "poll"))
    parser.add_argument("--request-file")
    parser.add_argument("--config")
    parser.add_argument("--output")
    parser.add_argument("--disclosure-output")
    parser.add_argument("--receipt-file")
    parser.add_argument("--token-file")
    parser.add_argument("--issue", type=int)
    parser.add_argument("--run-id", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            output = report({"receipt": decode(Path(args.receipt_file).read_bytes())}).encode("ascii")
        elif args.command == "validate":
            with Path(args.request_file).open("rb") as f:
                output = canonical(validation(f.read(MAX_WIRE + 1)))
        else:
            config = load_config(args.config)
            if args.command == "broker":
                serve(args.config)
                return 0
            # The transport process must also execute installed pinned code.
            release, _ = verify_release(config)
            identity = windows_identity()[0] if os.name == "nt" else str(os.geteuid())
            if identity != config["transport_identity"]:
                deny("TRANSPORT_IDENTITY_MISMATCH")
            if args.command in ("submit", "reconcile"):
                with Path(args.request_file).open("rb") as f:
                    raw = f.read(MAX_WIRE + 1)
                _, request = envelope(raw)
                if request.value["operation"] == "disclose" and not args.disclosure_output:
                    deny("DISCLOSURE_OUTPUT_REQUIRED")
                response = submit(config, raw, reconcile=args.command == "reconcile")
                if "disclosure_b64" in response:
                    if request.value["operation"] != "disclose":
                        deny("UNSOLICITED_DISCLOSURE")
                    content = base64.b64decode(response["disclosure_b64"], validate=True)
                    if digest(content) != response["receipt"]["result"]["chunk_sha256"]:
                        deny("DISCLOSURE_HASH_MISMATCH")
                    with Path(args.disclosure_output).open("xb") as f:
                        f.write(content)
                output = canonical(response["receipt"])
            else:
                api = GitHub(Path(args.token_file).read_text(encoding="utf-8").strip())
                if args.command == "poll-once":
                    output = canonical(poll_once(api, config, release, args.issue, args.run_id))
                else:
                    while not (Path(config["policy_dir"]) / "KILL").exists():
                        try:
                            poll_inbox(api, config, release)
                        except (Denied, OSError):
                            pass  # No raw API errors, tokens or request bodies on stdout.
                        time.sleep(15)
                    return 0
        if not args.output:
            deny("OUTPUT_REQUIRED")
        Path(args.output).write_bytes(output)
        print("HOSTOPS_V3_RESULT_FILE")
        return 0
    except (Denied, OSError, ValueError, TypeError) as exc:
        # Static diagnostic only. Never echo paths, request contents or exception text.
        if args.output:
            unavailable = isinstance(exc, OSError) or str(exc) in ("BROKER_UNAVAILABLE", "GITHUB_UNAVAILABLE")
            Path(args.output).write_bytes(canonical(receipt(None, "UNAVAILABLE" if unavailable else "BLOCKED", "CLI_BLOCKED")))
        print("HOSTOPS_V3_BLOCKED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
