"""HostOps R2: bounded Human-supervised session, one Windows account.

This is NOT an unattended broker, service, arbitrary command executor or write
authorization. The private Issues-only token is held in process memory until
this interactive terminal session ends; it is NOT isolated against malicious
programs running under the same Windows SID.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from .contract import Denied, canonical, deny
from .security import (NON_BROKER_SIDS, PERSONAL_V4_MODE, PERSONAL_V4_SCHEMA,
                       load_config_v4, windows_identity)
from .transport import (ControlGitHub, personal_remote_prepare,
                        personal_remote_publish, personal_supervised_scan)


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
