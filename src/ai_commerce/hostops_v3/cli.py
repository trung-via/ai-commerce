"""Stable CLI: validate (structure only), submit, reconcile, report, broker, poll."""
from __future__ import annotations

import argparse
import base64
import os
import platform
from pathlib import Path
import sys
import time

from .auth import verify_release, verify_release_v4
from .broker import serve, serve_v4
from .contract import (MAX_WIRE, Denied, canonical, decode, deny, digest,
                       dual_repo_envelope, envelope, public_receipt_v4, receipt, receipt_v4)
from .security import (NON_BROKER_SIDS, PERSONAL_V4_MODE, PERSONAL_V4_SCHEMA, load_config,
                       load_config_v4, windows_identity)
from .transport import (READ_ONLY_V4, ControlGitHub, GitHub, poll_inbox, poll_once,
                        poll_once_v4, report, report_v4, submit, submit_v4,
                        validation, validation_v4)

V3_COMMANDS = ("validate", "submit", "reconcile", "report", "broker", "poll-once", "poll")
V4_COMMANDS = ("validate-v4", "submit-v4", "reconcile-v4", "report-v4", "broker-v4", "poll-once-v4",
               "personal-local-inspect")


def _personal_local_inspect(args):
    """Operator-attended, fixed local metadata read. NOT a remote HostOps broker.

    No token, network API, arbitrary command, file write, or unattended
    activation is available through this command. Console consent is not a
    security boundary against a malicious *same-user* process.
    """
    if (args.request_file or args.receipt_file or args.token_file or args.output or
            args.disclosure_output or args.issue is not None or args.run_id is not None):
        deny("PERSONAL_LOCAL_OPTIONS_DENIED")
    if os.name != "nt":
        deny("WINDOWS_REQUIRED")
    if not args.config:
        deny("BAD_CONFIG")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        deny("INTERACTIVE_CONSENT_REQUIRED")
    path = Path(args.config)
    # Local absolute file only. Disallow UNC, symlink, oversized files and
    # device/relative paths; never let local inspection read a network config.
    if (not path.is_absolute() or str(path).startswith("\\\\") or path.is_symlink()
            or not path.is_file() or path.stat().st_size > 131072):
        deny("BAD_CONFIG")
    if os.name == "nt":
        # Reject NTFS junctions/reparse points in ANY ancestor of the supplied
        # config, not only a symlink at the final path.
        for component in (path, *path.parents):
            if getattr(component.lstat(), "st_file_attributes", 0) & 0x400:
                deny("BAD_CONFIG")
    config = load_config_v4(path)
    if (config["schema"] != PERSONAL_V4_SCHEMA or
            config["account_mode"] != PERSONAL_V4_MODE or
            config["enabled"] is not False):
        deny("PERSONAL_MODE_NOT_ENROLLED")
    sid, elevated = windows_identity()
    if (elevated or sid in NON_BROKER_SIDS or
            config["broker_identity"] != sid or
            config["transport_identity"] != sid):
        deny("PERSONAL_IDENTITY_MISMATCH")
    if (Path(config["policy_dir"]) / "KILL").exists():
        deny("KILL_SWITCH")
    print("LOCAL_ONLY: fixed OS metadata; no GitHub API, user file reads, broker or services.")
    print("Type LOCAL READ ONLY to inspect this Windows process:", flush=True)
    try:
        confirm = input()
    except EOFError:
        deny("INTERACTIVE_CONSENT_REQUIRED")
    if confirm != "LOCAL READ ONLY":
        deny("INTERACTIVE_CONSENT_REQUIRED")
    arch = platform.machine().casefold()
    architecture = "X64" if arch in ("amd64", "x86_64") else "OTHER"
    cpu = os.cpu_count()
    cpu_count = cpu if type(cpu) is int and 0 < cpu <= 512 else 0
    # Fixed fields only: no host name, user name, SID, process lists, paths,
    # secret status, token values, environment, device identifiers or logs.
    print("HOSTOPS_PERSONAL_LOCAL_READ")
    print(canonical({
        "schema": "hostops.personal.local_inventory.v1",
        "account_mode": PERSONAL_V4_MODE,
        "operation": "local_os_inventory",
        "windows": True,
        "current_sid_matched": True,
        "non_elevated": True,
        "console_confirmation_received": True,
        "architecture": architecture,
        "logical_cpu_count": cpu_count,
        "github_contacted": False,
        "broker_started": False,
        "private_keys_used": False,
        "operation_file_write_requested": False,
        "ready_to_enable": False,
    }).decode("ascii"))
    return 0


def _run_v4_cli(args):
    if args.command == "personal-local-inspect":
        return _personal_local_inspect(args)
    if args.command == "report-v4":
        output = report_v4({"receipt": decode(Path(args.receipt_file).read_bytes())}).encode("ascii")
    elif args.command == "validate-v4":
        with Path(args.request_file).open("rb") as f:
            output = canonical(validation_v4(f.read(MAX_WIRE + 1)))
    else:
        config = load_config_v4(args.config)
        if config["schema"] == PERSONAL_V4_SCHEMA:
            deny("PERSONAL_MODE_NOT_ENROLLED")
        if not config["enabled"] or (Path(config["policy_dir"]) / "KILL").exists():
            deny("KILL_SWITCH")
        if args.command == "broker-v4":
            serve_v4(args.config, once=True)
            return 0
        release, _ = verify_release_v4(config)
        if os.name == "nt":
            identity, elevated = windows_identity()
            if elevated or identity in NON_BROKER_SIDS:
                deny("ELEVATION_DENIED")
        else:
            if os.geteuid() == 0:
                deny("ELEVATION_DENIED")
            identity = str(os.geteuid())
        if identity != config["transport_identity"] or identity == config["broker_identity"]:
            deny("TRANSPORT_IDENTITY_MISMATCH")
        if args.command in ("submit-v4", "reconcile-v4"):
            if args.disclosure_output:
                deny("OPERATION_NOT_ENABLED")
            with Path(args.request_file).open("rb") as f:
                raw = f.read(MAX_WIRE + 1)
            _, request = dual_repo_envelope(raw)
            if request.value["operation"] not in READ_ONLY_V4:
                deny("OPERATION_NOT_ENABLED")
            response = submit_v4(config, raw, reconcile=args.command == "reconcile-v4")
            if type(response) is not dict or set(response) != {"receipt"}:
                deny("UNSOLICITED_DISCLOSURE")
            output = canonical(public_receipt_v4(response["receipt"]))
        else:
            api = ControlGitHub(Path(args.token_file).read_text(encoding="utf-8").strip())
            output = canonical(poll_once_v4(api, config, release, args.issue, args.run_id))
    if not args.output:
        deny("OUTPUT_REQUIRED")
    Path(args.output).write_bytes(output)
    print("HOSTOPS_V4_RESULT_FILE")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=(*V3_COMMANDS, *V4_COMMANDS))
    parser.add_argument("--request-file")
    parser.add_argument("--config")
    parser.add_argument("--output")
    parser.add_argument("--disclosure-output")
    parser.add_argument("--receipt-file")
    parser.add_argument("--token-file")
    parser.add_argument("--issue", type=int)
    parser.add_argument("--run-id", type=int)
    args = parser.parse_args(argv)
    is_v4 = args.command in V4_COMMANDS
    try:
        if is_v4:
            return _run_v4_cli(args)
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
        if args.output and args.command != "personal-local-inspect":
            unavailable = isinstance(exc, OSError) or str(exc) in ("BROKER_UNAVAILABLE", "GITHUB_UNAVAILABLE")
            maker = receipt_v4 if is_v4 else receipt
            Path(args.output).write_bytes(canonical(maker(None, "UNAVAILABLE" if unavailable else "BLOCKED", "CLI_BLOCKED")))
        print("HOSTOPS_V4_BLOCKED" if is_v4 else "HOSTOPS_V3_BLOCKED", file=sys.stderr)
        return 2


def main_v4(argv=None):
    """Explicit V4 CLI entrypoint; rejects v3 commands."""
    parser = argparse.ArgumentParser(description="HostOps V4 staged read-only CLI")
    parser.add_argument("command", choices=V4_COMMANDS)
    parsed, _ = parser.parse_known_args(argv)
    return main(argv)



if __name__ == "__main__":
    raise SystemExit(main())
