"""Stable CLI: validate (structure only), submit, reconcile, report, broker, poll."""
from __future__ import annotations

import argparse
import base64
import getpass
import os
import platform
from pathlib import Path
import subprocess
import sys
import time

from .auth import verify_release, verify_release_v4
from .broker import serve, serve_v4
from .contract import (MAX_WIRE, Denied, canonical, decode, deny, digest,
                       dual_repo_envelope, envelope, public_receipt_v4, receipt, receipt_v4)
from .security import (NON_BROKER_SIDS, PERSONAL_V4_MODE, PERSONAL_V4_SCHEMA, load_config,
                       load_config_v4, windows_identity)
from .transport import (READ_ONLY_V4, ControlGitHub, GitHub, poll_inbox, poll_once,
                        poll_once_v4, personal_remote_prepare, personal_remote_publish,
                        personal_supervised_scan,
                        report, report_v4, submit, submit_v4,
                        validation, validation_v4)

V3_COMMANDS = ("validate", "submit", "reconcile", "report", "broker", "poll-once", "poll")
V4_COMMANDS = ("validate-v4", "submit-v4", "reconcile-v4", "report-v4", "broker-v4", "poll-once-v4",
               "personal-local-inspect", "personal-remote-once",
               "personal-supervised-session", "personal-w1-root-check",
               "personal-w1-attended-once")


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
    }).decode("ascii"), end="")
    return 0


def _personal_remote_once(args):
    """One interactive owner-scoped private Issue read; NOT an unattended agent.

    Token is entered interactively, never in argv, environment, config or disk.
    It is still potentially readable by an already compromised same-user
    process: this mode must not run alongside untrusted public-runner jobs.
    """
    if (args.output or args.token_file or args.request_file or args.receipt_file or
            args.disclosure_output or args.run_id is not None or
            args.session_seconds != 600):
        deny("PERSONAL_REMOTE_OPTIONS_DENIED")
    if (os.name != "nt" or not args.config or
            type(args.issue) is not int or not 1 <= args.issue <= 2147483647):
        deny("PERSONAL_REMOTE_CONTEXT_DENIED")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        deny("INTERACTIVE_CONSENT_REQUIRED")
    config_path = Path(args.config)
    if (not config_path.is_absolute() or str(config_path).startswith("\\\\") or
            config_path.is_symlink() or not config_path.is_file() or
            config_path.stat().st_size > 131072):
        deny("BAD_CONFIG")
    for component in (config_path, *config_path.parents):
        if getattr(component.lstat(), "st_file_attributes", 0) & 0x400:
            deny("BAD_CONFIG")
    config = load_config_v4(config_path)
    if (config["schema"] != PERSONAL_V4_SCHEMA or
            config["account_mode"] != PERSONAL_V4_MODE or
            config["enabled"] is not False):
        deny("PERSONAL_MODE_NOT_ENROLLED")
    sid, elevated = windows_identity()
    if (elevated or sid in NON_BROKER_SIDS or
            config["broker_identity"] != sid or config["transport_identity"] != sid):
        deny("PERSONAL_IDENTITY_MISMATCH")
    if (Path(config["policy_dir"]) / "KILL").exists():
        deny("KILL_SWITCH")
    root = Path(__file__).resolve().parents[3]
    # Pin to the locally checked-out, unmodified exact public source.
    # Git arguments are fixed, with no remote, fetch, checkout or write.
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
                              capture_output=True, text=True, timeout=8, check=False)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                                "--untracked-files=all"], capture_output=True,
                               text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        deny("LOCAL_SOURCE_UNVERIFIED")
    source_sha = head.stdout.strip()
    if (head.returncode != 0 or dirty.returncode != 0 or dirty.stdout.strip() or
            len(source_sha) != 40 or
            any(c not in "0123456789abcdef" for c in source_sha)):
        deny("LOCAL_SOURCE_UNVERIFIED")
    from .transport import ControlGitHub
    # Do not pass the token in argv or environment and do not save to a file.
    try:
        credential = getpass.getpass("Private GitHub Issues token (not shown): ")
    except (EOFError, KeyboardInterrupt):
        deny("TOKEN_UNAVAILABLE")
    if not credential or len(credential) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in credential):
        deny("TOKEN_UNAVAILABLE")
    api = ControlGitHub(credential)
    del credential
    request = personal_remote_prepare(api, args.issue, source_sha)
    text = "ALLOW READ " + str(args.issue) + " " + request["request_id"]
    print("Local presence required. Request operation: local_os_inventory")
    print("Issue #" + str(args.issue) + " / request " + request["request_id"])
    print("Type " + text + " to approve EXACTLY this one read:", flush=True)
    try:
        confirm = input()
    except EOFError:
        deny("INTERACTIVE_CONSENT_REQUIRED")
    if confirm != text:
        deny("INTERACTIVE_CONSENT_REQUIRED")
    architecture = "X64" if platform.machine().casefold() in ("amd64", "x86_64") else "OTHER"
    cpu = os.cpu_count()
    inventory = {
        "architecture": architecture,
        "logical_cpu_count": cpu if type(cpu) is int and 0 < cpu <= 512 else 0,
    }
    try:
        receipt = personal_remote_publish(api, args.issue, request, inventory)
    except Denied as exc:
        if str(exc) == "REMOTE_POST_UNKNOWN":
            print("HOSTOPS_R1_UNKNOWN / CHECK PRIVATE ISSUE; DO NOT RETRY AUTOMATICALLY",
                  file=sys.stderr)
            return 3
        raise
    print("HOSTOPS_R1_RESULT_POSTED")
    print(canonical(receipt).decode("ascii"), end="")
    return 0


MAX_SESSION_SECONDS = 1800
MAX_READS_PER_SESSION = 5
POLL_SECONDS = 15


def _clean_source_sha(root: Path) -> str:
    """Read source HEAD and worktree status; never mutate checkout."""
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=8, check=False)
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain",
             "--untracked-files=all"],
            capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        deny("LOCAL_SOURCE_UNVERIFIED")
    sha = head.stdout.strip()
    if (head.returncode != 0 or status.returncode != 0 or
            status.stdout.strip() or len(sha) != 40 or
            any(c not in "0123456789abcdef" for c in sha)):
        deny("LOCAL_SOURCE_UNVERIFIED")
    return sha


def _context(args):
    if (args.output or args.token_file or args.request_file or
            args.receipt_file or args.disclosure_output or
            args.run_id is not None or args.issue is not None):
        deny("PERSONAL_SESSION_OPTIONS_DENIED")
    if (type(args.session_seconds) is not int or
            not 30 <= args.session_seconds <= MAX_SESSION_SECONDS):
        deny("PERSONAL_SESSION_DURATION_DENIED")
    if (os.name != "nt" or not args.config or
            not (sys.stdin.isatty() and sys.stdout.isatty())):
        deny("INTERACTIVE_CONSENT_REQUIRED")
    path = Path(args.config)
    if (not path.is_absolute() or str(path).startswith("\\\\") or
            path.is_symlink() or not path.is_file() or
            path.stat().st_size > 131072):
        deny("BAD_CONFIG")
    for part in (path, *path.parents):
        if getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
            deny("BAD_CONFIG")
    cfg = load_config_v4(path)
    if (cfg["schema"] != PERSONAL_V4_SCHEMA or
            cfg["account_mode"] != PERSONAL_V4_MODE or
            cfg["enabled"] is not False):
        deny("PERSONAL_MODE_NOT_ENROLLED")
    sid, elevated = windows_identity()
    if (elevated or sid in NON_BROKER_SIDS or
            cfg["broker_identity"] != sid or cfg["transport_identity"] != sid):
        deny("PERSONAL_IDENTITY_MISMATCH")
    kill = Path(cfg["policy_dir"]) / "KILL"
    if kill.exists():
        deny("KILL_SWITCH")
    root = Path(__file__).resolve().parents[3]
    return root, kill, _clean_source_sha(root)


def run_session(args):
    """One terminal-held session: per-request consent, bounded scan and time."""
    root, kill, pinned = _context(args)
    print("R2 SUPERVISED READ ONLY: no file writes, broker, or unattended tasks.")
    print("Type START R2 READ ONLY to begin a bounded session:", flush=True)
    try:
        approval = input()
    except EOFError:
        deny("INTERACTIVE_CONSENT_REQUIRED")
    if approval != "START R2 READ ONLY":
        deny("INTERACTIVE_CONSENT_REQUIRED")
    try:
        credential = getpass.getpass("Private GitHub Issues token (not shown): ")
    except (EOFError, KeyboardInterrupt):
        deny("TOKEN_UNAVAILABLE")
    if (not credential or len(credential) > 512 or
            any(ord(c) < 33 or ord(c) > 126 for c in credential)):
        deny("TOKEN_UNAVAILABLE")
    api = ControlGitHub(credential)
    del credential

    deadline = time.monotonic() + args.session_seconds
    scanned = set()
    completed = 0
    try:
        while time.monotonic() < deadline:
            if kill.exists():
                deny("KILL_SWITCH")
            if _clean_source_sha(root) != pinned:
                deny("LOCAL_SOURCE_CHANGED")
            for issue_num in personal_supervised_scan(api):
                if issue_num in scanned:
                    continue
                try:
                    request = personal_remote_prepare(api, issue_num, pinned)
                except Denied as exc:
                    if str(exc) in ("REMOTE_REQUEST_EXPIRED",
                                    "REMOTE_RESULT_ALREADY_PRESENT"):
                        scanned.add(issue_num)
                        continue
                    raise
                confirm_text = ("ALLOW READ " + str(issue_num) + " " +
                                request["request_id"])
                print("Issue #" + str(issue_num) + " — local_os_inventory only.")
                print("Type " + confirm_text + " (other input cancels):",
                      flush=True)
                try:
                    confirmation = input()
                except EOFError:
                    deny("INTERACTIVE_CONSENT_REQUIRED")
                if confirmation != confirm_text:
                    deny("INTERACTIVE_CONSENT_REQUIRED")
                if time.monotonic() >= deadline or kill.exists():
                    deny("SESSION_EXPIRED_OR_KILLED")
                if _clean_source_sha(root) != pinned:
                    deny("LOCAL_SOURCE_CHANGED")
                # Recheck after interactive input: Issue may have been edited
                # or the short-lived request may have expired while typing.
                fresh = personal_remote_prepare(api, issue_num, pinned)
                if canonical(fresh) != canonical(request):
                    deny("REMOTE_REQUEST_CHANGED")
                arch = ("X64" if platform.machine().casefold() in
                        ("amd64", "x86_64") else "OTHER")
                cpu = os.cpu_count()
                inventory = {
                    "architecture": arch,
                    "logical_cpu_count": (
                        cpu if type(cpu) is int and 0 < cpu <= 512 else 0)}
                try:
                    result = personal_remote_publish(
                        api, issue_num, fresh, inventory)
                except Denied as exc:
                    if str(exc) == "REMOTE_POST_UNKNOWN":
                        print("HOSTOPS_R2_UNKNOWN / CHECK PRIVATE ISSUE, DO NOT RETRY",
                              file=sys.stderr)
                        return 3
                    raise
                scanned.add(issue_num)
                completed += 1
                print("HOSTOPS_R2_RESULT_POSTED issue=" + str(issue_num) +
                      " request=" + result["request_id"])
                if completed >= MAX_READS_PER_SESSION:
                    print("HOSTOPS_R2_SESSION_LIMIT")
                    return 0
                if time.monotonic() >= deadline:
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(POLL_SECONDS,
                           max(0.0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        print("HOSTOPS_R2_SESSION_STOPPED")
        return 0
    print("HOSTOPS_R2_SESSION_ENDED completed=" + str(completed))
    return 0



# W1 supervised bootstrap; fixed test path/content, no remote polling or token.
# This command is deliberately NOT a credential-isolated unattended service.
W1_PROOF_ROOT = Path(r"C:\TOOL\AI-Commerce-HostOps-Approved")
W1_PROOF_STATE = Path(r"C:\TOOL\AI-Commerce-HostOps-State")
W1_PROOF_NAME = "w1-native-proof.txt"
W1_PROOF_TEXT = "AI_COMMERCE_W1_NATIVE_PROOF_V1"


def _w1_attended_context(args):
    """Operator-only check, with no host modifications."""
    from . import filesystem as fs

    if (os.name != "nt" or not sys.stdin.isatty() or not sys.stdout.isatty() or
            args.output or args.token_file or args.config or args.receipt_file or
            args.disclosure_output or args.run_id is not None or
            not args.source_root):
        deny("W1_ATTENDED_CONSOLE_REQUIRED")
    current_sid, elevated = windows_identity()
    if not current_sid or elevated or current_sid in NON_BROKER_SIDS:
        deny("W1_ATTENDED_IDENTITY_DENIED")
    source = Path(args.source_root)
    if not source.is_absolute() or source.is_symlink():
        deny("LOCAL_SOURCE_UNVERIFIED")
    sha = _clean_source_sha(source)
    identity = fs.w1_windows_root_identity(W1_PROOF_ROOT)
    return sha, identity


def _w1_attended_command(args):
    """Strict local one-shot only; no persistent credentials or GitHub dispatch.

    W1 approval must be signed by an independently controlled Human signer.
    Console consent and same-SID hash pins DO NOT prove unattended isolation.
    """
    from . import filesystem as fs
    from .contract import decode, canonical
    from .journal import OfflineW1IntentLedger

    source_sha, root_identity = _w1_attended_context(args)
    if args.command == "personal-w1-root-check":
        if args.request_file or args.issue is not None:
            deny("W1_ATTENDED_OPTIONS_DENIED")
        print("HOSTOPS_W1_ROOT_CANDIDATE_NOT_ATTESTED")
        print(canonical({
            "schema": "hostops.personal.w1_local_preflight.v1",
            "source_sha": source_sha,
            "root_id": "human-scratch-v1",
            "root_identity": root_identity,
            "source_only": True,
            "remote_write_enabled": False,
            "independent_custody_proven": False,
        }).decode("ascii").strip(), flush=True)
        return 0

    if (args.command != "personal-w1-attended-once" or args.issue is None or
            not all((args.request_file, args.allowed_signers, args.signers_sha256,
                     args.ssh_keygen, args.verifier_sha256, args.root_volume_serial,
                     args.root_file_index))):
        deny("W1_ATTENDED_OPTIONS_DENIED")
    from . import auth
    expected_identity = {"volume_serial_hex": args.root_volume_serial,
                         "file_index_hex": args.root_file_index}
    if root_identity != expected_identity:
        deny("W1_NATIVE_ROOT_MISMATCH")
    if (not W1_PROOF_STATE.is_dir() or W1_PROOF_STATE.is_symlink() or
            (W1_PROOF_STATE / "KILL").exists()):
        deny("W1_ATTENDED_STATE_OR_KILL")
    request_path = Path(args.request_file)
    if (not request_path.is_absolute() or request_path.is_symlink() or
            not request_path.is_file() or request_path.stat().st_size > 16384):
        deny("W1_ATTENDED_REQUEST_DENIED")
    proposal = decode(request_path.read_bytes(), limit=16384)
    params = proposal.get("parameters")
    if (type(params) is not dict or params.get("root_id") != "human-scratch-v1" or
            params.get("filename") != W1_PROOF_NAME or
            params.get("content_utf8") != W1_PROOF_TEXT or
            params.get("content_sha256") != digest(W1_PROOF_TEXT.encode("ascii")) or
            params.get("content_bytes") != len(W1_PROOF_TEXT)):
        deny("W1_ATTENDED_EXACT_PROOF_ONLY")
    verify = {
        "issue_number": args.issue,
        "expected_source_sha": source_sha,
        "allowed_signers": Path(args.allowed_signers),
        "expected_signers_sha256": args.signers_sha256,
        "ssh_keygen": Path(args.ssh_keygen),
        "expected_verifier_sha256": args.verifier_sha256,
    }
    # Verify BEFORE any irreversible intent. A signed Issue number is not
    # an independently authenticated private GitHub Issue origin.
    auth.personal_w1_verify_offline(proposal, **verify)
    if os.path.lexists(W1_PROOF_ROOT / W1_PROOF_NAME):
        deny("W1_NATIVE_ALREADY_EXISTS")
    expected_phrase = "CREATE W1 " + str(args.issue) + " " + proposal["request_id"]
    print("W1 ATTENDED TEST: " + W1_PROOF_NAME)
    print("SHA256 " + params["content_sha256"])
    print("Source " + source_sha)
    print("Issue #" + str(args.issue) + " — externally verify the private Issue first.")
    print("Type exactly " + expected_phrase + " to approve one file create:", flush=True)
    try:
        consent = input()
    except (EOFError, KeyboardInterrupt):
        deny("W1_ATTENDED_CONSENT_DENIED")
    if consent != expected_phrase:
        deny("W1_ATTENDED_CONSENT_DENIED")
    # Recheck after human interaction: TTL, source, KILL and root identity.
    if ((W1_PROOF_STATE / "KILL").exists() or
            _clean_source_sha(Path(args.source_root)) != source_sha or
            fs.w1_windows_root_identity(W1_PROOF_ROOT) != expected_identity):
        deny("W1_ATTENDED_STATE_CHANGED")
    auth.personal_w1_verify_offline(proposal, **verify)
    with OfflineW1IntentLedger(W1_PROOF_STATE / "w1-intent.sqlite3") as ledger:
        staged = ledger.record_verified_proposal(proposal, **verify)
        if staged["status"] != "INTENT_STAGED_NO_EFFECT":
            deny("W1_ATTENDED_REPLAY_UNKNOWN")
        result = fs.w1_windows_native_create_fixture(
            W1_PROOF_ROOT, expected_identity, W1_PROOF_NAME, W1_PROOF_TEXT)
    print("HOSTOPS_W1_LOCAL_ATTENDED_RESULT", flush=True)
    print(canonical({
        "schema": "hostops.personal.w1_local_attended_result.v1",
        "status": "LOCAL_NATIVE_FIXTURE_VERIFIED_NOT_REMOTE",
        "request_id": staged["request_id"],
        "issue_number": args.issue,
        "source_sha": source_sha,
        "sha256": result["content_sha256"],
        "bytes": result["content_bytes"],
        "human_signature_verified": True,
        "independent_host_receipt": False,
        "remote_write_enabled": False,
        "unattended_credentials_proven": False,
    }).decode("ascii").strip(), flush=True)
    return 0

def _run_v4_cli(args):
    if args.command in ("personal-w1-root-check", "personal-w1-attended-once"):
        return _w1_attended_command(args)
    if args.command == "personal-remote-once":
        return _personal_remote_once(args)
    if args.command == "personal-supervised-session":
        return run_session(args)
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
    parser.add_argument("--session-seconds", type=int, default=600)
    parser.add_argument("--source-root")
    parser.add_argument("--allowed-signers")
    parser.add_argument("--signers-sha256")
    parser.add_argument("--ssh-keygen")
    parser.add_argument("--verifier-sha256")
    parser.add_argument("--root-volume-serial")
    parser.add_argument("--root-file-index")
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
        if args.output and args.command not in ("personal-local-inspect", "personal-remote-once", "personal-supervised-session",
                                                "personal-w1-root-check", "personal-w1-attended-once"):
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
