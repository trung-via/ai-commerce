"""Personal HostOps v3 with a separate, privately enrolled execution broker.

Legacy READ reports public committed source, safe git metadata or file hashes.
A Human GitHub comment authorizes each fixed WRITE. The separate exec route
requires native owner enrollment and an authenticated protected workflow job.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import namedtuple
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from os.path import normpath
from pathlib import Path, PureWindowsPath
import platform
import re
import secrets
import shutil
import sqlite3
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
from threading import Lock
import time
from typing import Callable, Literal, Mapping, TypedDict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, HTTPSHandler, build_opener

REPO = "trung-via/ai-commerce"
OWNER = "trung-via"
SCHEMA = "ai-commerce.personal-hostops.v1"
READ_TITLE = "[HOSTOPS READ] "
WRITE_TITLE = "[HOSTOPS WRITE] "
ROOTS = {
    "workspace": Path("C:/TOOL/ai-commerce"),
    "scratch": Path("C:/TOOL/AI-Commerce-HostOps-Approved"),
    "workspace_notes": Path("C:/TOOL/ai-commerce/.local"),
}
PUBLIC_PREFIXES = ("docs/", "src/ai_commerce/", "tests/", "work_items/", ".github/workflows/")
PUBLIC_EXTENSIONS = {".md", ".py", ".json", ".yml", ".yaml", ".toml", ".cjs", ".txt"}
MAX_READ = 8192
MAX_WRITE = 2048
_CLEAN_AUTOSTART = "install_clean_9444_autostart"
_CLEAN_DEMAND_MIGRATION = "migrate_clean_9444_demand_task"
_CLEAN_TASK_RESET = "reset_clean_9444_tasks"
_CLEAN_TASK_WRITES = (_CLEAN_AUTOSTART, _CLEAN_DEMAND_MIGRATION, _CLEAN_TASK_RESET)
_SUPERVISOR_DEPLOY = "deploy_wakehub_supervisor"
_SUPERVISOR_RECOVER_HEALTH_ONLY = "recover_wakehub_supervisor_health_only"
_SUPERVISOR_ENROLL_CHAT = "enroll_wakehub_chat"
_SUPERVISOR_PREPARE_CHAT = "prepare_wakehub_chat_enrollment"
_SUPERVISOR_CHAT_WRITES = (_SUPERVISOR_PREPARE_CHAT, _SUPERVISOR_ENROLL_CHAT)
_SUPERVISOR_CONFIG_PROBE = "wakehub_supervisor_config_probe"
_SUPERVISOR_TASK_PROBE = "wakehub_supervisor_task_probe"
_CLEAN_RUNTIME_PROBE = "clean_9444_runtime_probe"
_CLEAN_1270_FORENSIC_READ = "clean_9444_1270_forensic_read"
_A10_READINESS = "a10_codex_native_readiness"
_A10_READINESS_SCHEMA = "A10CodexHostReadiness.v1"
_ACQUISITION_SOURCE = Path("src/ai_commerce/remote/chrome_acquisition.py")

# Source-owned PUBLIC vocabulary. Private config is locally validated by the
# existing acquisition contract and reduced to fixed enums; path/name/JSON
# contents never leave the runner. Only three known fixed config candidates and
# the fixed D5 manifest name are considered. Manifest-provided config paths
# and private browser state remain outside this operation.
_STANDING_CATEGORY_CODES = {
    "chrome_profile": ("UNVERIFIED", "DEDICATED_DIR_PRESENT", "UNAVAILABLE"),
    "chrome_config": ("UNAVAILABLE", "KNOWN_CANDIDATES_ABSENT", "KNOWN_CANDIDATES_AMBIGUOUS",
                      "KNOWN_CONFIG_NOT_VERIFIED", "KNOWN_CONFIG_EXACT_9333",
                      "KNOWN_CONFIG_OTHER_PORT"),
    "chrome_markers": ("UNVERIFIED", "LOCK_MARKER_PRESENT",
                       "LOCK_MARKERS_ABSENT", "UNAVAILABLE"),
    "cdp_9333_listener": ("ABSENT", "EXACT_LOOPBACK", "UNSAFE_BINDING", "UNAVAILABLE"),
    "chrome_process": ("MATCHED", "UNVERIFIED", "UNAVAILABLE"),
    "chrome_session": ("MATCHED", "UNVERIFIED", "UNAVAILABLE"),
    "cdp_protocol": ("RESPONDING", "UNVERIFIED", "UNAVAILABLE"),
    "ef02_01_task": ("RUNNING", "READY", "DISABLED", "QUEUED", "NOT_REGISTERED", "UNAVAILABLE"),
    "ef02_01_listener": ("PRESENT", "ABSENT", "UNAVAILABLE"),
    "ef02_02_task": ("RUNNING", "READY", "DISABLED", "QUEUED", "NOT_REGISTERED", "UNAVAILABLE"),
    "ef02_02_listener": ("PRESENT", "ABSENT", "UNAVAILABLE"),
    "hostops_01_task": ("RUNNING", "READY", "DISABLED", "QUEUED", "NOT_REGISTERED", "UNAVAILABLE"),
    "hostops_01_listener": ("PRESENT", "ABSENT", "UNAVAILABLE"),
    "repo_git": ("CLEAN", "TRACKED_CHANGES", "UNAVAILABLE"),
    "python_runtime": ("SUPPORTED", "UNSUPPORTED"),
    "d5_metadata": ("UNAVAILABLE", "MANIFEST_PRESENT", "MANIFEST_ABSENT"),
}
STANDING_READ_CATEGORIES = tuple(_STANDING_CATEGORY_CODES)
STANDING_READ_CODES = frozenset(code for codes in _STANDING_CATEGORY_CODES.values() for code in codes)

# Mirror the reviewed protected Issue ingress allowlist, including its separate
# runner and standing-READ sanitizers. The workflow independently checks all
# sixteen closed-code categories and the entire sanitized receipt before posting.
PERSONAL_PUBLIC_FIELDS = frozenset((
    "status code operation issue_number root_id relative_path bytes sha256 windows architecture "
    "logical_cpu_count no_shell_command_executed overwritten remote_shell_enabled source_sha "
    "approval_channel content_utf8 git_head_sha branch tracked_changes prefix paths "
    "total_tracked_in_scope truncated git_before_sha git_after_sha target_sha fast_forward_only "
    "sync_result all_listener_count unmatched_listener_count arbitrary_shell_enabled cdp_port "
    "listener_count loopback_count unexpected_binding_count chrome_owned_loopback_count "
    "same_session_chrome_count devtools_protocol_ok infrastructure_ready exact_chat_verified "
    "wake_sent runners diagnostics"
).split())

_WAKEHUB_ENROLLMENT_PUBLIC_FIELDS = frozenset((
    "status code operation issue_number source_sha approval_comment_id approval_channel "
    "remote_shell_enabled arbitrary_shell_enabled exact_chat_verified wake_sent"
).split())
_WAKEHUB_ENROLLMENT_HOST_HOLDS = frozenset({
    "PROTECTED_HOSTOPS_ONLY", "SOURCE_CUSTODY_UNVERIFIED", "WINDOWS_OWNER_CONTEXT_UNVERIFIED",
    "SUPERVISOR_ACTIVE_UNVERIFIED", "WAKEHUB_ENROLLMENT_BUSY",
    "WAKEHUB_ENROLLMENT_APPROVAL_UNVERIFIED", "WAKEHUB_ENROLLMENT_APPROVAL_EXPIRED",
})


class Denied(Exception):
    pass


def deny(code: str):
    raise Denied(code)


def _object_unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            deny("DUPLICATE_JSON_KEY")
        obj[key] = value
    return obj


def _dict(value):
    if type(value) is not dict:
        deny("INVALID_REQUEST")
    return value


def _relative_path(value, *, write=False):
    if type(value) is not str or not value or len(value) > 240:
        deny("PATH_DENIED")
    if value.startswith("/") or "\\" in value or ":" in value or "\x00" in value:
        deny("PATH_DENIED")
    segments = value.split("/")
    if write and len(segments) != 1:
        deny("WRITE_FLAT_NAME_ONLY")
    for i, segment in enumerate(segments):
        if (segment in (".", "..") or not segment or
                (segment != ".github" or i != 0 or write) and not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_. -]{0,90}", segment) or
                segment[-1] in (".", " ") or
                re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?",
                             segment, re.IGNORECASE)):
            deny("PATH_DENIED")
    if write and (not segments[0].startswith("hostops-personal-") or
                  Path(segments[0]).suffix.lower() not in (".txt", ".md", ".json")):
        deny("WRITE_EXTENSION_DENIED")
    return segments


def parse_event(event, mode):
    event = _dict(event)
    repo = _dict(event.get("repository"))
    if (repo.get("full_name") != REPO or repo.get("private") is not False or
            _dict(repo.get("owner")).get("login") != OWNER):
        deny("EXPECTED_PUBLIC_AI_COMMERCE_REPOSITORY")
    issue = _dict(event.get("issue"))
    user = _dict(issue.get("user"))
    number = issue.get("number")
    if (user.get("login") != OWNER or type(number) is not int or number < 1 or
            issue.get("state") != "open" or "pull_request" in issue):
        deny("ISSUE_OWNER_OR_STATE_DENIED")
    sender = _dict(event.get("sender"))
    if sender.get("login") != OWNER:
        deny("NON_OWNER_EVENT")
    title = issue.get("title")
    if type(title) is not str:
        deny("INVALID_ISSUE_TITLE")
    if mode == "read":
        if event.get("action") != "opened" or not title.startswith(READ_TITLE):
            deny("READ_EVENT_DENIED")
    elif mode == "write":
        if event.get("action") != "created" or not title.startswith(WRITE_TITLE):
            deny("WRITE_EVENT_DENIED")
        comment = _dict(event.get("comment"))
        comment_user = comment.get("user")
        if (type(comment_user) is not dict or comment_user.get("login") != OWNER or
                comment.get("body") != "APPROVE WRITE #" + str(number)):
            deny("EXACT_WRITE_APPROVAL_REQUIRED")
    else:
        deny("INVALID_MODE")
    raw = issue.get("body")
    if type(raw) is not str or not 0 < len(raw.encode("utf-8")) <= 12000:
        deny("INVALID_ISSUE_BODY")
    try:
        request = json.loads(raw, object_pairs_hook=_object_unique)
    except (ValueError, UnicodeError, RecursionError):
        deny("INVALID_JSON")
    request = _dict(request)
    if set(request) != {"schema", "operation", "parameters"} or request["schema"] != SCHEMA:
        deny("INVALID_SCHEMA")
    params = _dict(request["parameters"])
    operation = request["operation"]
    if mode == "read":
        if operation in (_A10_READINESS, _CLEAN_1270_FORENSIC_READ, _SUPERVISOR_CONFIG_PROBE, _SUPERVISOR_TASK_PROBE):
            if params:
                deny("INVALID_PARAMETERS")
            if number > 2**53 - 1:
                deny("ISSUE_OWNER_OR_STATE_DENIED")
        elif operation in ("inventory", "runner_runtime_inventory", "cdp_9333_status", "cdp_9444_topology",
                         "cdp_9444_config_registry", "standing_read_diagnostics", _CLEAN_RUNTIME_PROBE):
            # Runtime inventory has fixed, compiled-in three-runner targets.
            # The public Issue cannot choose a task, executable or command.
            if params:
                deny("INVALID_PARAMETERS")
        elif operation == "read_text":
            if set(params) != {"root_id", "relative_path"} or params.get("root_id") != "workspace":
                deny("READ_SCOPE_DENIED")
            rel = params.get("relative_path")
            _relative_path(rel)
            if not (rel == "README.md" or rel.startswith(PUBLIC_PREFIXES)) or Path(rel).suffix.lower() not in PUBLIC_EXTENSIONS:
                deny("PUBLIC_READ_SCOPE_DENIED")
        elif operation == "git_snapshot":
            if params:
                deny("INVALID_PARAMETERS")
        elif operation == "tracked_paths":
            if set(params) != {"prefix"} or type(params.get("prefix")) is not str:
                deny("TRACKED_PREFIX_DENIED")
            prefix = params["prefix"]
            _relative_path(prefix.rstrip("/"))
            if (not prefix.endswith("/") or
                    not any(prefix.startswith(allowed) for allowed in PUBLIC_PREFIXES)):
                deny("TRACKED_PREFIX_DENIED")
        elif operation == "file_digest":
            if set(params) != {"root_id", "relative_path"} or params.get("root_id") not in ("scratch", "workspace_notes"):
                deny("DIGEST_SCOPE_DENIED")
            _relative_path(params.get("relative_path"), write=True)
        else:
            deny("READ_OPERATION_DENIED")
    else:
        if operation in _SUPERVISOR_CHAT_WRITES:
            if ((operation == _SUPERVISOR_PREPARE_CHAT and params)
                    or (operation == _SUPERVISOR_ENROLL_CHAT and (
                        params.keys() != {"selection_id"} or type(params["selection_id"]) is not str
                        or re.fullmatch(r"[a-f0-9]{64}", params["selection_id"]) is None))):
                deny("INVALID_PARAMETERS")
            if number > 2**53 - 1:
                deny("ISSUE_OWNER_OR_STATE_DENIED")
        elif operation in (*_CLEAN_TASK_WRITES, _SUPERVISOR_DEPLOY, _SUPERVISOR_RECOVER_HEALTH_ONLY):
            if params:
                deny("INVALID_PARAMETERS")
            if operation in (_SUPERVISOR_DEPLOY, _SUPERVISOR_RECOVER_HEALTH_ONLY) and number > 2**53 - 1:
                deny("ISSUE_OWNER_OR_STATE_DENIED")
        elif operation == "git_sync_main":
            if set(params) != {"expected_local_sha", "target_sha"} or any(
                type(params.get(key)) is not str or
                not re.fullmatch(r"[0-9a-f]{40}", params[key])
                for key in ("expected_local_sha", "target_sha")
            ):
                deny("GIT_SYNC_PIN_REQUIRED")
        elif operation == "create_text":
            if (set(params) != {"root_id", "relative_path", "content_utf8"} or
                    params.get("root_id") not in ("scratch", "workspace_notes")):
                deny("WRITE_SCOPE_DENIED")
            _relative_path(params["relative_path"], write=True)
            content = params["content_utf8"]
            if type(content) is not str or "\x00" in content:
                deny("WRITE_CONTENT_DENIED")
            size = len(content.encode("utf-8"))
            if not 0 < size <= MAX_WRITE:
                deny("WRITE_SIZE_DENIED")
        else:
            deny("WRITE_SCOPE_DENIED")
    return {"issue_number": number, "operation": operation, "parameters": params}


def _is_reparse(path: Path):
    try:
        flags = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(flags & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)) or path.is_symlink()


def _get_path(root_id, relative, *, write=False):
    segments = _relative_path(relative, write=write)
    root = ROOTS[root_id]
    if not root.is_dir():
        deny("ROOT_NOT_ENROLLED")
    # Reject junctions/reparse points in the path components. Same-account
    # processes are NOT a separate Windows security boundary.
    if any(_is_reparse(part) for part in (root, *root.parents)):
        deny("REPARSE_PATH_DENIED")
    current = root
    for segment in segments[:-1]:
        current = current / segment
        if not current.is_dir() or _is_reparse(current):
            deny("PARENT_PATH_DENIED")
    target = current / segments[-1]
    if _is_reparse(target) and (target.exists() or target.is_symlink()):
        deny("REPARSE_PATH_DENIED")
    if target.parent.resolve() != root.resolve() and write:
        deny("WRITE_FLAT_NAME_ONLY")
    return target


def _git(*args):
    root = ROOTS["workspace"]
    if not root.is_dir():
        deny("ROOT_NOT_ENROLLED")
    # Avoid git refreshing/writing index and do not run arbitrary issue commands.
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            timeout=12, check=False, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        deny("GIT_READ_UNAVAILABLE")
    if result.returncode:
        deny("GIT_READ_FAILED")
    return result.stdout


TRUSTED_ORIGIN_URLS = frozenset((
    "https://github.com/trung-via/ai-commerce.git",
    "https://github.com/trung-via/ai-commerce",
    "git@github.com:trung-via/ai-commerce.git",
    "ssh://git@github.com/trung-via/ai-commerce.git",
))


def _git_write(*args):
    """Fixed argv only, no issue-supplied shell, Git hooks disabled for merge."""
    root = ROOTS["workspace"]
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "-c", "core.hooksPath=NUL", *args],
            capture_output=True, text=True, timeout=90, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
        )
    except (OSError, subprocess.TimeoutExpired):
        deny("GIT_SYNC_UNKNOWN_CHECK_HEAD_NO_RETRY")
    if result.returncode != 0:
        deny("GIT_SYNC_FAILED_CHECK_HEAD_NO_RETRY")
    return result.stdout


def _sync_main(params):
    """One attended fast-forward, pinned to observed local and remote commits."""
    root = ROOTS["workspace"]
    if not root.is_dir() or any(_is_reparse(p) for p in (root, *root.parents)):
        deny("GIT_SYNC_ROOT_DENIED")
    top = _git("rev-parse", "--show-toplevel").strip()
    if Path(top).resolve() != root.resolve():
        deny("GIT_SYNC_ROOT_MISMATCH")
    if _git("symbolic-ref", "--quiet", "--short", "HEAD").strip() != "main":
        deny("GIT_SYNC_NOT_MAIN")
    origin = _git("config", "--get", "remote.origin.url").strip()
    if origin not in TRUSTED_ORIGIN_URLS:
        deny("GIT_SYNC_ORIGIN_DENIED")
    before = _git("rev-parse", "HEAD").strip()
    if before != params["expected_local_sha"]:
        deny("GIT_SYNC_LOCAL_SHA_DRIFT")
    if _git("status", "--porcelain=v1", "--untracked-files=normal").strip():
        deny("GIT_SYNC_DIRTY_WORKTREE")
    # Fetch changes only remote-tracking refs, not tracked workspace files.
    _git_write("fetch", "--no-tags", "origin",
               "refs/heads/main:refs/remotes/origin/main")
    if (_git("symbolic-ref", "--quiet", "--short", "HEAD").strip() != "main" or
            _git("rev-parse", "HEAD").strip() != before):
        deny("GIT_SYNC_WORKTREE_DRIFT")
    if _git("status", "--porcelain=v1", "--untracked-files=normal").strip():
        deny("GIT_SYNC_DIRTY_AFTER_FETCH")
    remote = _git("rev-parse", "refs/remotes/origin/main").strip()
    if remote != params["target_sha"]:
        deny("GIT_SYNC_TARGET_MOVED")
    try:
        _git("merge-base", "--is-ancestor", before, remote)
    except Denied:
        deny("GIT_SYNC_NON_FAST_FORWARD")
    if before != remote:
        _git_write("merge", "--ff-only", "--no-edit",
                   "refs/remotes/origin/main")
    after = _git("rev-parse", "HEAD").strip()
    if after != remote:
        deny("GIT_SYNC_UNKNOWN_CHECK_HEAD_NO_RETRY")
    if _git("status", "--porcelain=v1", "--untracked-files=normal").strip():
        deny("GIT_SYNC_POST_MERGE_DIRTY_CHECK_HEAD")
    return {
        "status": "OBSERVED", "operation": "git_sync_main",
        "git_before_sha": before, "git_after_sha": after,
        "target_sha": remote, "fast_forward_only": True,
        "sync_result": "ALREADY_AT_TARGET" if before == remote else "FAST_FORWARDED",
        "branch": "main",
    }


_CLEAN_PUBLIC_CODES = frozenset({
    "PROTECTED_HOSTOPS_ONLY", "WINDOWS_INTERACTIVE_REQUIRED", "WINDOWS_TOKEN_UNAVAILABLE",
    "ELEVATED_CHROME_DISALLOWED", "ENROLLMENT_IDENTITY_UNPROVEN",
    "WINDOWS_OWNER_CONTEXT_UNVERIFIED", "PROFILE_UNAVAILABLE", "PROFILE_LOCKED", "PROFILE_IN_USE",
    "PROFILE_ALREADY_ACTIVE_NO_CDP", "FOREIGN_PORT_OWNER", "BROWSER_OWNERSHIP_UNPROVEN",
    "CHROME_PATH_INVALID", "CHROME_EXECUTABLE_UNTRUSTED", "CDP_PROTOCOL_UNAVAILABLE",
    "SUPERVISOR_ACTIVE", "AUTOSTART_CONFLICT", "AUTOSTART_RUNTIME_UNAVAILABLE",
    "LOCAL_MAIN_SYNC_REQUIRED", "SOURCE_WORKFLOW_PATH_MISMATCH", "SOURCE_PATH_UNTRUSTED",
    "SOURCE_INDEX_UNVERIFIED", "SOURCE_UNTRACKED_FILE", "SOURCE_BYTES_MISMATCH",
    "WINDOWS_PROBE_INVALID", "WINDOWS_PROBE_UNAVAILABLE",
    "UNKNOWN_EFFECT", "CDP_VERIFIED", "INSTALLED_BUT_NOT_RUNNING",
})

_CLEAN_MIGRATION_CODES = _CLEAN_PUBLIC_CODES | {
    "AUTOSTART_NOT_ENROLLED", "DEMAND_MIGRATED", "DEMAND_ALREADY_READY",
    "TASK_MIGRATION_AMBIGUOUS", "TASK_MIGRATION_NOT_READY", "TASK_MIGRATION_REPLAY",
}
_CLEAN_RESET_OBSERVED = {"TASK_RESET_VERIFIED", "TASK_RESET_ALREADY_READY"}
_CLEAN_RESET_CODES = _CLEAN_PUBLIC_CODES | _CLEAN_RESET_OBSERVED | {
    "TASK_RESET_D5_UNVERIFIED", "TASK_RESET_NOT_READY", "TASK_RESET_DRIFT",
}

_CHECKOUT_EGG_INFO_FILES = frozenset({
    "PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt",
    "requires.txt", "top_level.txt", "not-zip-safe", "zip-safe",
})

_CLEAN_SOURCE_TEXT_EXTENSIONS = frozenset({
    ".py", ".json", ".md", ".txt", ".patch", ".yml", ".yaml", ".toml",
})


def _normalize_crlf_text(raw: bytes) -> bytes | None:
    if b"\x00" in raw:
        return None
    normalized = raw.replace(b"\r\n", b"\n")
    if b"\r" in normalized:
        return None
    try:
        normalized.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return normalized


def _clean_source_bytes_match(path: Path, worktree_bytes: bytes, pinned_bytes: bytes) -> bool:
    if worktree_bytes == pinned_bytes:
        return True
    if path.suffix.lower() not in _CLEAN_SOURCE_TEXT_EXTENSIONS:
        return False
    worktree_norm = _normalize_crlf_text(worktree_bytes)
    if worktree_norm is None:
        return False
    pinned_norm = _normalize_crlf_text(pinned_bytes)
    if pinned_norm is None or worktree_norm != pinned_norm:
        return False
    return worktree_bytes in (worktree_norm, worktree_norm.replace(b"\n", b"\r\n"))


def _clean_autostart_receipt(code, *, operation=_CLEAN_AUTOSTART):
    migration = operation == _CLEAN_DEMAND_MIGRATION
    if operation == _CLEAN_TASK_RESET:
        codes = _CLEAN_RESET_CODES
        observed = _CLEAN_RESET_OBSERVED
    elif migration:
        codes = _CLEAN_MIGRATION_CODES
        observed = {"DEMAND_MIGRATED", "DEMAND_ALREADY_READY"}
    else:
        codes = _CLEAN_PUBLIC_CODES
        observed = {"CDP_VERIFIED", "INSTALLED_BUT_NOT_RUNNING"}
    if type(code) is not str or code not in codes:
        code = "AUTOSTART_RUNTIME_UNAVAILABLE"
    result = {
        "status": "OBSERVED" if code in observed else "HOLD",
        "code": code, "operation": operation, "cdp_port": 9444,
        "exact_chat_verified": False,
        "wake_sent": False, "arbitrary_shell_enabled": False,
    }
    if operation == _CLEAN_AUTOSTART:
        result["devtools_protocol_ok"] = code == "CDP_VERIFIED"
    if not result.keys() <= PERSONAL_PUBLIC_FIELDS:
        deny("PUBLIC_TRANSPORT_GAP")
    return result


def _require_protected_clean_runner(event_name: str, *, reconcile: bool = False):
    # main() has already validated the exact authenticated event. This narrows
    # the non-TTY bridge to the protected main workflow's dedicated runner.
    expected = {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": event_name,
        "GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_OWNER": OWNER,
        "GITHUB_ACTOR": OWNER, "GITHUB_TRIGGERING_ACTOR": OWNER,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/issue-ingress.yml@refs/heads/main",
        "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_NAME": "AI-COMMERCE-HOSTOPS-01",
    }
    if (os.name != "nt" or any(os.environ.get(key) != value for key, value in expected.items())
            or not re.fullmatch(r"[1-9][0-9]*" if reconcile else "1", os.environ.get("GITHUB_RUN_ATTEMPT", ""))
            or any(key.startswith("AI_COMMERCE_GIT_GUARD_") for key in os.environ)):
        deny("PROTECTED_HOSTOPS_ONLY")


def _protected_clean_workflow_sha(*, write=False, allow_blank_workflow_sha=False) -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    workflow_sha = os.environ.get("GITHUB_WORKFLOW_SHA", "")
    # Issue ingress pins checkout to github.sha and blanks its custom workflow
    # SHA outside the A10 READ. Clean WRITE and the protected 9444 READs
    # additionally check local main and source bytes against that checkout
    # before any task access; A10 READ still requires an explicit workflow SHA.
    if (not re.fullmatch(r"[a-f0-9]{40}", sha) or
            (workflow_sha != sha and not ((write or allow_blank_workflow_sha) and workflow_sha == ""))):
        deny("PROTECTED_HOSTOPS_ONLY")
    return sha


def _persistent_clean_source(acquisition, sha, *, pin_runtime=False, runtime_commitment=None):
    """Verify public checkout custody via Git pin, clean tree, bytes and non-reparse paths.

    Unlike private Chrome profile/handoff state, the public repository checkout
    does not require a bespoke Windows DACL/owner check.
    """
    root = ROOTS["workspace"]
    raw_workflow = os.environ.get("GITHUB_WORKSPACE", "")
    if type(raw_workflow) is not str or not raw_workflow:
        deny("SOURCE_WORKFLOW_PATH_MISMATCH")
    workflow = Path(raw_workflow)
    source = root / _ACQUISITION_SOURCE
    try:
        if (not root.is_absolute() or not workflow.is_absolute()
                or Path(normpath(Path(__file__).absolute())) != Path(normpath(workflow / "src/ai_commerce/hostops_v3/personal.py"))
                or Path(normpath(Path(acquisition.__file__).absolute())) != Path(normpath(workflow / _ACQUISITION_SOURCE))):
            deny("SOURCE_WORKFLOW_PATH_MISMATCH")
    except (OSError, ValueError, TypeError, AttributeError):
        deny("SOURCE_WORKFLOW_PATH_MISMATCH")
    for path in (root, root / ".git", root / "src"):
        if not path.is_dir() or not acquisition._no_links(path):
            deny("SOURCE_PATH_UNTRUSTED")
    try:
        top = Path(_git("rev-parse", "--show-toplevel").strip())
        if not top.is_absolute() or not acquisition._no_links(top) or top.resolve(strict=True) != root.resolve(strict=True):
            deny("LOCAL_MAIN_SYNC_REQUIRED")
        if (_git("rev-parse", "--abbrev-ref", "HEAD").strip() != "main"
                or _git("config", "--get", "remote.origin.url").strip() not in TRUSTED_ORIGIN_URLS
                or _git("rev-parse", "HEAD", "refs/remotes/origin/main").splitlines() != [sha, sha]):
            deny("LOCAL_MAIN_SYNC_REQUIRED")
        # The supervisor loads source from this isolated src tree. Unrelated
        # untracked operator data cannot affect its source custody.
        status = (("--untracked-files=all", "--", "src") if pin_runtime else ("--untracked-files=all",))
        status_lines = [line for line in _git("status", "--porcelain=v1", *status).splitlines()
                        if line.strip()]
    except (OSError, ValueError, Denied):
        deny("LOCAL_MAIN_SYNC_REQUIRED")
    if status_lines:
        if all(line.startswith("?? ") for line in status_lines):
            deny("SOURCE_UNTRACKED_FILE")
        deny("LOCAL_MAIN_SYNC_REQUIRED")
    # Compare source bytes with the protected SHA checkout, permitting only
    # uniform LF/CRLF conversion on reviewed UTF-8 text extensions. This also
    # catches index flags and filters that could hide worktree edits.
    try:
        raw_entries = _git("ls-files", "-v", "-z", "--", "src")
    except (OSError, ValueError, Denied):
        deny("LOCAL_MAIN_SYNC_REQUIRED")
    entries = raw_entries.rstrip("\0").split("\0")
    if not entries or len(entries) > 4096 or any(not item.startswith("H src/") for item in entries):
        deny("SOURCE_INDEX_UNVERIFIED")
    tracked = {root / item[2:] for item in entries}
    if source not in tracked:
        deny("SOURCE_INDEX_UNVERIFIED")

    def walk_error(_error):
        deny("SOURCE_PATH_UNTRUSTED")

    src_root = root / "src"
    egg_info = src_root / "ai_commerce.egg-info"
    runtime_files = []
    eol_tags = {}
    try:
        for folder, dirs, files in os.walk(src_root, onerror=walk_error):
            parent = Path(folder)
            for name in (*dirs, *files):
                path = parent / name
                if _is_reparse(path):
                    deny("SOURCE_PATH_UNTRUSTED")
                info = path.lstat()
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    deny("SOURCE_PATH_UNTRUSTED")
            if parent == egg_info:
                if dirs or any(name not in _CHECKOUT_EGG_INFO_FILES for name in files):
                    deny("SOURCE_UNTRACKED_FILE")
                continue
            dirs[:] = [name for name in dirs if name != "__pycache__"]
            for name in files:
                path = parent / name
                if path not in tracked:
                    deny("SOURCE_UNTRACKED_FILE")
                pinned = workflow / path.relative_to(root)
                if not acquisition._no_links(pinned):
                    deny("SOURCE_PATH_UNTRUSTED")
                if not pinned.is_file():
                    deny("SOURCE_BYTES_MISMATCH")
                raw = path.read_bytes()
                if not _clean_source_bytes_match(path, raw, pinned.read_bytes()):
                    deny("SOURCE_BYTES_MISMATCH")
                if pin_runtime and path.suffix == ".py" and src_root / "ai_commerce" in path.parents:
                    relative = path.relative_to(src_root).as_posix()
                    runtime_files.append([relative, hashlib.sha256(raw).hexdigest()])
                    if runtime_commitment is not None:
                        eol_tags[relative] = _supervisor_eol_tag(raw)
                tracked.remove(path)
    except Denied:
        raise
    except (OSError, ValueError):
        deny("SOURCE_PATH_UNTRUSTED")
    if tracked:
        deny("SOURCE_UNTRACKED_FILE")
    if pin_runtime:
        if runtime_commitment is not None:
            packed = sum(tag << (2 * index) for index, (_, tag) in enumerate(sorted(eol_tags.items())))
            layout = packed.to_bytes((len(eol_tags) + 3) // 4, "little")
            runtime_commitment["source_eols"] = base64.urlsafe_b64encode(layout).decode("ascii").rstrip("=")
        return hashlib.sha256(json.dumps(sorted(runtime_files), separators=(",", ":")).encode("ascii")).hexdigest()
    return source


def _supervisor_eol_tag(raw):
    """Encode retain-blob, uniform LF, or uniform CRLF in two bits per path."""
    normalized = _normalize_crlf_text(raw)
    if normalized is None or b"\n" not in normalized:
        return 0
    if raw == normalized:
        return 1
    return 2 if raw == normalized.replace(b"\n", b"\r\n") else 0


def _decode_supervisor_eols(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{2,1366}", value) is None:
        return None
    try:
        packed = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError:
        return None
    if base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=") != value:
        return None
    return packed


_CLEAN_RUNTIME_PENDING_PREFIXES = (
    "AUTOSTART_PENDING", "LAUNCH_PENDING", "BOTH_PENDING", "MARKER_UNVERIFIED",
)
_CLEAN_RUNTIME_TARGET_FAIL_OUTCOMES = (
    "PROFILE_UNAVAILABLE", "TARGET_UNAVAILABLE",
)
_CLEAN_RUNTIME_TASK_CODES = (
    "DEMAND_READY", "DEMAND_DISABLED", "DEMAND_NOT_READY", "DEMAND_RUNTIME_BAD",
    "LEGACY_PT1M_READY", "LEGACY_PT1M_DISABLED", "LEGACY_PT1M_NOT_READY",
    "TASK_ABSENT", "TASK_MULTIPLE", "TASK_FOREIGN", "TASK_CONFLICT",
    "TASK_CONFLICT_OWNER", "TASK_CONFLICT_ACL", "TASK_SD_UNVERIFIED", "TASK_CONFLICT_PRINCIPAL",
    "TASK_CONFLICT_ACTION", "TASK_CONFLICT_SETTINGS", "TASK_CONFLICT_TRIGGERS",
    "TASK_CONFLICT_DEMAND_PATH", "TASK_CONFLICT_DEMAND_PWSH_PATH",
    "TASK_CONFLICT_DEMAND_WORKDIR", "TASK_CONFLICT_DEMAND_ARGS", "TASK_CONFLICT_DEMAND_PWSH",
    "TASK_CONFLICT_PT1M_TRIG_TYPE", "TASK_CONFLICT_PT1M_TRIG_OWNER",
    "TASK_CONFLICT_PT1M_INTERVAL", "TASK_CONFLICT_PT1M_TRIG_CFG",
    "TASK_CONFLICT_PT1M_PATH", "TASK_CONFLICT_PT1M_ARGS", "TASK_CONFLICT_PT1M_WORKDIR",
    "TASK_UNAVAILABLE",
)
_CLEAN_RUNTIME_JOURNAL_CODES = (
    "JOURNAL_FRESH", "JOURNAL_COMPLETE", "JOURNAL_MISMATCH",
    "ROLLBACK_ONLY", "COMPLETE_ONLY", "JOURNAL_UNVERIFIED",
)
_CLEAN_RUNTIME_D5_CODES = (
    "D5_ENABLED", "D5_DISABLED", "D5_ABSENT", "D5_UNVERIFIED",
    "D5_UNVERIFIED_MULTIPLE", "D5_UNVERIFIED_MOVED", "D5_UNVERIFIED_STATE",
    "D5_UNVERIFIED_BASE", "D5_UNVERIFIED_ACL", "D5_UNVERIFIED_PRINCIPAL",
    "D5_UNVERIFIED_TRIGGER", "D5_UNVERIFIED_ACTION", "D5_UNVERIFIED_EXE", "D5_UNVERIFIED_EXE_PATH",
    "D5_UNVERIFIED_CUSTODY", "D5_UNVERIFIED_SCRIPT",
)
_CLEAN_RUNTIME_EXCEPTION_CODES = frozenset({
    "PROBE_EXCEPTION_IMPORT", "PROBE_EXCEPTION_PERMISSION", "PROBE_EXCEPTION_TIMEOUT",
    "PROBE_EXCEPTION_OS", "PROBE_EXCEPTION_DATA", "PROBE_EXCEPTION_RUNTIME",
    "PROBE_EXCEPTION_OTHER",
})
_CLEAN_RUNTIME_RECONCILED_CODES = frozenset(
    f"{task}_{journal}_{d5}"
    for task in _CLEAN_RUNTIME_TASK_CODES
    for journal in _CLEAN_RUNTIME_JOURNAL_CODES
    for d5 in _CLEAN_RUNTIME_D5_CODES
)
_CLEAN_RUNTIME_PENDING_RECONCILED_CODES = frozenset(
    f"{prefix}_{reconciled}"
    for prefix in _CLEAN_RUNTIME_PENDING_PREFIXES
    for reconciled in _CLEAN_RUNTIME_RECONCILED_CODES
)
# Present-state read-only native attestation only (never historical mutation proof):
# D5_ENABLED and D5_DISABLED reflect independently attested present D5 task
# definitions; D5_ABSENT and all D5_UNVERIFIED subcauses always HOLD with explicit visibility.
_CLEAN_RUNTIME_PROBE_OBSERVED_CODES = frozenset({
    *(f"DEMAND_READY_{journal}_{d5}"
      for journal in ("JOURNAL_FRESH", "JOURNAL_COMPLETE")
      for d5 in ("D5_ENABLED", "D5_DISABLED")),
    *(f"LEGACY_PT1M_READY_JOURNAL_FRESH_{d5}"
      for d5 in ("D5_ENABLED", "D5_DISABLED")),
})
_CLEAN_RUNTIME_PROBE_GUARD_CODES = frozenset({
    "WINDOWS_OWNER_CONTEXT_UNVERIFIED", "SOURCE_CUSTODY_UNVERIFIED",
    "PROFILE_UNAVAILABLE", "TARGET_READ_UNVERIFIED",
    "PENDING_STATE_CHANGED", "UNKNOWN",
    *(f"{prefix}_{outcome}"
      for prefix in _CLEAN_RUNTIME_PENDING_PREFIXES
      for outcome in _CLEAN_RUNTIME_TARGET_FAIL_OUTCOMES),
    *_CLEAN_RUNTIME_EXCEPTION_CODES,
})
_CLEAN_RUNTIME_PROBE_CODES = _CLEAN_RUNTIME_PROBE_GUARD_CODES | {
    *_CLEAN_RUNTIME_RECONCILED_CODES,
    *_CLEAN_RUNTIME_PENDING_RECONCILED_CODES,
}
_CLEAN_RECONCILE_TASK_MAP = {
    "DEMAND_READY": "DEMAND_READY",
    "DEMAND_DISABLED": "DEMAND_DISABLED",
    "DEMAND_NOT_READY": "DEMAND_NOT_READY",
    "DEMAND_RUNTIME_BAD": "DEMAND_RUNTIME_BAD",
    "LEGACY_PT1M_READY": "LEGACY_PT1M_READY",
    "LEGACY_PT1M_DISABLED": "LEGACY_PT1M_DISABLED",
    "LEGACY_PT1M_NOT_READY": "LEGACY_PT1M_NOT_READY",
    "ABSENT": "TASK_ABSENT",
    "MULTIPLE_CANDIDATES": "TASK_MULTIPLE",
    "FOREIGN_CANDIDATE": "TASK_FOREIGN",
    "CONFLICT": "TASK_CONFLICT",
    "CONFLICT_BASE_OWNER": "TASK_CONFLICT_OWNER",
    "CONFLICT_BASE_ACL": "TASK_CONFLICT_ACL",
    "SD_UNVERIFIED": "TASK_SD_UNVERIFIED",
    "CONFLICT_BASE_PRINCIPAL": "TASK_CONFLICT_PRINCIPAL",
    "CONFLICT_BASE_ACTION": "TASK_CONFLICT_ACTION",
    "CONFLICT_BASE_SETTINGS": "TASK_CONFLICT_SETTINGS",
    "CONFLICT_TRIGGER_COUNT": "TASK_CONFLICT_TRIGGERS",
    "CONFLICT_DEMAND_PATH": "TASK_CONFLICT_DEMAND_PATH",
    "CONFLICT_DEMAND_PWSH_PATH": "TASK_CONFLICT_DEMAND_PWSH_PATH",
    "CONFLICT_DEMAND_WORKDIR": "TASK_CONFLICT_DEMAND_WORKDIR",
    "CONFLICT_DEMAND_ARGS": "TASK_CONFLICT_DEMAND_ARGS",
    "CONFLICT_DEMAND_PWSH": "TASK_CONFLICT_DEMAND_PWSH",
    "CONFLICT_LEGACY_TRIGGER_TYPE": "TASK_CONFLICT_PT1M_TRIG_TYPE",
    "CONFLICT_LEGACY_TRIGGER_OWNER": "TASK_CONFLICT_PT1M_TRIG_OWNER",
    "CONFLICT_LEGACY_TRIGGER_PT1M": "TASK_CONFLICT_PT1M_INTERVAL",
    "CONFLICT_LEGACY_TRIGGER_CFG": "TASK_CONFLICT_PT1M_TRIG_CFG",
    "CONFLICT_LEGACY_PATH": "TASK_CONFLICT_PT1M_PATH",
    "CONFLICT_LEGACY_ARGS": "TASK_CONFLICT_PT1M_ARGS",
    "CONFLICT_LEGACY_WORKDIR": "TASK_CONFLICT_PT1M_WORKDIR",
    "UNAVAILABLE": "TASK_UNAVAILABLE",
}
_CLEAN_RECONCILE_JOURNAL_MAP = {
    "FRESH": "JOURNAL_FRESH",
    "COMPLETE": "JOURNAL_COMPLETE",
    "MISMATCH": "JOURNAL_MISMATCH",
    "ROLLBACK_ONLY": "ROLLBACK_ONLY",
    "COMPLETE_ONLY": "COMPLETE_ONLY",
    "UNVERIFIED": "JOURNAL_UNVERIFIED",
}
_CLEAN_RECONCILE_D5_MAP = {
    "ENABLED_ATTESTED": "D5_ENABLED",
    "DISABLED_ATTESTED": "D5_DISABLED",
    "ABSENT": "D5_ABSENT",
    "UNVERIFIED": "D5_UNVERIFIED",
    "UNVERIFIED_MULTIPLE": "D5_UNVERIFIED_MULTIPLE",
    "UNVERIFIED_MOVED": "D5_UNVERIFIED_MOVED",
    "UNVERIFIED_STATE": "D5_UNVERIFIED_STATE",
    "UNVERIFIED_BASE": "D5_UNVERIFIED_BASE",
    "UNVERIFIED_ACL": "D5_UNVERIFIED_ACL",
    "UNVERIFIED_PRINCIPAL": "D5_UNVERIFIED_PRINCIPAL",
    "UNVERIFIED_TRIGGER": "D5_UNVERIFIED_TRIGGER",
    "UNVERIFIED_ACTION": "D5_UNVERIFIED_ACTION",
    "UNVERIFIED_EXE": "D5_UNVERIFIED_EXE",
    "UNVERIFIED_EXE_PATH": "D5_UNVERIFIED_EXE_PATH",
    "UNVERIFIED_CUSTODY": "D5_UNVERIFIED_CUSTODY",
    "UNVERIFIED_SCRIPT": "D5_UNVERIFIED_SCRIPT",
}
_CLEAN_1270_BACKUP_CODES = frozenset({"NO_TRUSTED_1270_CUSTODY", "JOURNAL_ABSENT", "VALID_BUT_UNBOUND"})
_CLEAN_1270_OBSERVATION_CODES = frozenset({
    "PENDING_CLEAR", "PENDING_STATE_CHANGED", "PROCESS_IDENTITY_UNVERIFIED",
    *_CLEAN_RUNTIME_PENDING_PREFIXES,
})


def _clean_1270_public_code(task, journal, d5, backup, pending):
    # The protected Issue transport already carries a scalar code. Each
    # fixed-position component comes from its own closed vocabulary.
    return "+".join(("UNKNOWN_EFFECT", backup, task, d5, journal, pending))


def _clean_1270_unavailable(pending):
    return _clean_1270_public_code(
        "TASK_UNAVAILABLE", "JOURNAL_UNVERIFIED", "D5_UNVERIFIED", "NO_TRUSTED_1270_CUSTODY", pending,
    )


def _clean_1270_public_status(code):
    if type(code) is not str:
        return None
    if code in _CLEAN_RUNTIME_PROBE_GUARD_CODES:
        return "HOLD"
    parts = code.split("+")
    if len(parts) != 6:
        return None
    effect, backup, task, d5, journal, pending = parts
    if (effect != "UNKNOWN_EFFECT" or backup not in _CLEAN_1270_BACKUP_CODES
            or task not in _CLEAN_RUNTIME_TASK_CODES or d5 not in _CLEAN_RUNTIME_D5_CODES
            or journal not in _CLEAN_RUNTIME_JOURNAL_CODES or pending not in _CLEAN_1270_OBSERVATION_CODES):
        return None
    if pending == "PENDING_CLEAR" and f"{task}_{journal}_{d5}" in _CLEAN_RUNTIME_PROBE_OBSERVED_CODES:
        return "OBSERVED"
    return "HOLD"


def _clean_probe_exception_code(exc: BaseException) -> str:
    if isinstance(exc, ImportError):
        return "PROBE_EXCEPTION_IMPORT"
    if isinstance(exc, PermissionError):
        return "PROBE_EXCEPTION_PERMISSION"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "PROBE_EXCEPTION_TIMEOUT"
    if isinstance(exc, OSError):
        return "PROBE_EXCEPTION_OS"
    if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
        return "PROBE_EXCEPTION_DATA"
    if isinstance(exc, RuntimeError):
        return "PROBE_EXCEPTION_RUNTIME"
    return "PROBE_EXCEPTION_OTHER"


def _observe_clean_runtime(sha: str, *, forensic: bool = False) -> str:
    from ai_commerce.remote import chrome_acquisition as acquisition
    from ai_commerce.remote.local_wake_entry import LocalWakeConfigError

    def read_owner():
        try:
            return acquisition._native_operator()
        except (acquisition.AcquisitionBlocked, acquisition._EnrollmentHold, LocalWakeConfigError, OSError):
            return None

    def read_marker(reader):
        try:
            value = reader()
        except (acquisition.AcquisitionBlocked, acquisition._EnrollmentHold, acquisition._Clean9444Hold, OSError):
            return None
        return value if type(value) is bool else None

    def classify_markers(autostart, launch):
        if autostart is None or launch is None:
            return "MARKER_UNVERIFIED"
        if autostart and launch:
            return "BOTH_PENDING"
        if autostart:
            return "AUTOSTART_PENDING"
        if launch:
            return "LAUNCH_PENDING"
        return None

    def sample_markers():
        return (
            read_marker(lambda: acquisition._clean_pending(acquisition._CLEAN_TASK_PENDING)),
            read_marker(acquisition._clean_launch_pending),
        )

    def read_process():
        try:
            state, identity = acquisition._clean_inventory(target, candidates, owner[1], owner[2])
        except (acquisition.AcquisitionBlocked, acquisition._EnrollmentHold, acquisition._Clean9444Hold, OSError):
            return None
        if state in ("ABSENT", "OWNED"):
            return state, identity
        return None

    owner = read_owner()
    if owner is None:
        return "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
    try:
        _persistent_clean_source(acquisition, sha)
    except Denied:
        return "SOURCE_CUSTODY_UNVERIFIED"

    if read_owner() != owner:
        return "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
    # Independently observe both markers without mutating registry state, then
    # inspect the fixed WakeHub 9444 task, migration journal and D5 read-only.
    before = sample_markers()
    marker_state = classify_markers(*before)
    try:
        target, candidates = acquisition._clean_target(owner)
    except acquisition._Clean9444Hold as exc:
        if str(exc) == "PROFILE_UNAVAILABLE":
            return "PROFILE_UNAVAILABLE" if marker_state is None else f"{marker_state}_PROFILE_UNAVAILABLE"
        return "TARGET_READ_UNVERIFIED" if marker_state is None else f"{marker_state}_TARGET_UNAVAILABLE"
    except (acquisition.AcquisitionBlocked, acquisition._EnrollmentHold, OSError):
        return "TARGET_READ_UNVERIFIED" if marker_state is None else f"{marker_state}_TARGET_UNAVAILABLE"

    process_before = read_process() if forensic and marker_state is None else None
    read_error = False
    reconciled = None
    try:
        if forensic:
            reconciled = acquisition._clean_reconcile_read(owner, target, candidates, forensic=True)
        else:
            reconciled = acquisition._clean_reconcile_read(owner, target, candidates)
    except (acquisition.AcquisitionBlocked, acquisition._EnrollmentHold, acquisition._Clean9444Hold, OSError):
        read_error = True
    process_stable = process_before is not None and read_process() == process_before
    if read_owner() != owner or (not read_error and reconciled == {"error": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"}):
        return "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
    after = sample_markers()
    if forensic and after != before:
        return _clean_1270_unavailable("PENDING_STATE_CHANGED")
    after_state = classify_markers(*after)
    if after_state != marker_state:
        return "PENDING_STATE_CHANGED"
    pending = marker_state or "PENDING_CLEAR"
    if read_error:
        if forensic:
            return _clean_1270_unavailable(pending)
        base = "TASK_UNAVAILABLE_JOURNAL_UNVERIFIED_D5_UNVERIFIED"
        return base if marker_state is None else f"{marker_state}_{base}"
    fields = {"task", "journal", "d5", "backup"} if forensic else {"task", "journal", "d5"}
    if (type(reconciled) is not dict or reconciled.keys() != fields
            or any(type(value) is not str for value in reconciled.values())):
        if forensic:
            return _clean_1270_unavailable(pending)
        if marker_state is None:
            return "UNKNOWN"
        return f"{marker_state}_TASK_UNAVAILABLE_JOURNAL_UNVERIFIED_D5_UNVERIFIED"
    task_code = _CLEAN_RECONCILE_TASK_MAP.get(reconciled["task"])
    journal_code = _CLEAN_RECONCILE_JOURNAL_MAP.get(reconciled["journal"])
    d5_code = _CLEAN_RECONCILE_D5_MAP.get(reconciled["d5"])
    if task_code is None or journal_code is None or d5_code is None:
        if forensic:
            return _clean_1270_unavailable(pending)
        if marker_state is None:
            return "UNKNOWN"
        return f"{marker_state}_TASK_UNAVAILABLE_JOURNAL_UNVERIFIED_D5_UNVERIFIED"
    if journal_code == "JOURNAL_COMPLETE" and not task_code.startswith("DEMAND_"):
        journal_code = "JOURNAL_MISMATCH"
    if forensic:
        backup = reconciled["backup"]
        if backup not in _CLEAN_1270_BACKUP_CODES:
            return _clean_1270_unavailable(pending)
        if pending == "PENDING_CLEAR" and not process_stable:
            pending = "PROCESS_IDENTITY_UNVERIFIED"
        return _clean_1270_public_code(task_code, journal_code, d5_code, backup, pending)
    reconciled_code = f"{task_code}_{journal_code}_{d5_code}"
    return reconciled_code if marker_state is None else f"{marker_state}_{reconciled_code}"


def _probe_protected_clean_runtime(event=None, *, forensic=False):
    """Project only the fixed observation from an authenticated READ workflow."""
    operation = _CLEAN_1270_FORENSIC_READ if forensic else _CLEAN_RUNTIME_PROBE
    result = {
        "status": "HOLD", "code": "UNKNOWN", "operation": operation,
        "cdp_port": 9444, "exact_chat_verified": False, "wake_sent": False,
        "arbitrary_shell_enabled": False, "remote_shell_enabled": False,
    }
    if forensic:
        if event is None:
            deny("PROTECTED_HOSTOPS_ONLY")
        request = parse_event(event, "read")
        if request["operation"] != operation:
            deny("READ_OPERATION_DENIED")
        result["issue_number"] = request["issue_number"]
        result["approval_channel"] = "CHATGPT_TO_AI_COMMERCE_ISSUE"
    if not (result.keys() | {"source_sha"}) <= PERSONAL_PUBLIC_FIELDS:
        deny("PUBLIC_TRANSPORT_GAP")
    try:
        try:
            _require_protected_clean_runner("issues")
            if not forensic and event is not None and parse_event(event, "read")["operation"] != operation:
                deny("PROTECTED_HOSTOPS_ONLY")
        except Denied:
            return {**result, "code": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"}
        try:
            sha = _protected_clean_workflow_sha(allow_blank_workflow_sha=True)
        except Denied:
            return {**result, "code": "SOURCE_CUSTODY_UNVERIFIED"}
        result["source_sha"] = sha
        if forensic:
            observed = _observe_clean_runtime(sha, forensic=True)
            status = _clean_1270_public_status(observed)
            if status is not None:
                result.update(status=status, code=observed)
            return result
        observed = _observe_clean_runtime(sha)
        code = observed if type(observed) is str and observed in _CLEAN_RUNTIME_PROBE_CODES else "UNKNOWN"
        result["code"] = code
        if code in _CLEAN_RUNTIME_PROBE_OBSERVED_CODES:
            result["status"] = "OBSERVED"
    except Exception as exc:
        # A failed probe can never project native output or exception text.
        code = _clean_probe_exception_code(exc)
        result["code"] = code if code in _CLEAN_RUNTIME_PROBE_CODES else "UNKNOWN"
    return result


_SUPERVISOR_OBSERVED = frozenset({
    "SUPERVISOR_STARTED_HEALTH_OBSERVED", "SUPERVISOR_REUSED_HEALTH_OBSERVED",
    "SUPERVISOR_CHROME_HEALTH_ONLY_OBSERVED",
})
_SUPERVISOR_DESKTOP_HOLDS = frozenset({
    "WINDOWS_INTERACTIVE_REQUIRED", "CHROME_INTERACTIVE_SESSION_CONFLICT",
})
_SUPERVISOR_CHROME_HOLDS = frozenset({
    "CDP_PROTOCOL_UNAVAILABLE", "PROFILE_LOCK_HOLD", "ENROLLMENT_PROFILE_LOCK_UNPROVEN",
    "PROFILE_ALREADY_ACTIVE_NO_CDP", "WINDOWS_PROBE_INVALID", "CHROME_OBSERVATION_HOLD",
})
_SUPERVISOR_CHROME_PROBE_HOLDS = _SUPERVISOR_CHROME_HOLDS | _SUPERVISOR_DESKTOP_HOLDS | {
    "ACTIVE_CONFIG_UNVERIFIED", "LOCAL_CONFIG_CHANGED", "LOCAL_CONFIG_AMBIGUOUS", "LOCAL_CONFIG_INVALID",
    "LOCAL_CONFIG_REQUIRED",
    "CONFIG_UNTRUSTED", "CHROME_ENROLLMENT_DRIFT", "OPERATOR_OWNERSHIP_UNPROVEN",
    "WINDOWS_OWNER_CONTEXT_UNVERIFIED", "ENROLLMENT_IDENTITY_UNPROVEN",
    "CHROME_OWNER_UNPROVEN", "CDP_OWNER_MISMATCH", "CDP_NOT_LOOPBACK_OWNED", "FOREIGN_PORT_OWNER",
    "BROWSER_OWNERSHIP_UNPROVEN", "PROCESS_IDENTITY_UNKNOWN", "CHROME_EXECUTABLE_UNTRUSTED",
    "WINDOWS_PROBE_UNAVAILABLE", "POWERSHELL_UNAVAILABLE", "CHROME_OBSERVATION_CHANGED",
    "ACTIVE_CHROME_OWNED_REQUIRED",
}
_SUPERVISOR_HOLDS = frozenset({
    "PROTECTED_HOSTOPS_ONLY", "SOURCE_CUSTODY_UNVERIFIED", "WINDOWS_OWNER_CONTEXT_UNVERIFIED",
    "LOCAL_CONFIG_REQUIRED", "LOCAL_CONFIG_INVALID", "LOCAL_CONFIG_OBSERVE_ONLY_REQUIRED",
    "LOCAL_CONFIG_STALE", "LOCAL_CONFIG_AMBIGUOUS", "LOCAL_CONFIG_USER_ENV_UNAVAILABLE",
    "LOCAL_CONFIG_CHANGED", "SUPERVISOR_RUNTIME_UNAVAILABLE", "SUPERVISOR_ACTIVE_UNVERIFIED",
    "SUPERVISOR_START_UNKNOWN", "SUPERVISOR_TASK_CONFLICT", "SUPERVISOR_START_NOT_ATTEMPTED",
}) | _SUPERVISOR_DESKTOP_HOLDS | _SUPERVISOR_CHROME_HOLDS
_SUPERVISOR_RECOVERY_OBSERVED = frozenset({"SUPERVISOR_CHROME_HEALTH_ONLY_OBSERVED"})
_SUPERVISOR_RECOVERY_HOLDS = _SUPERVISOR_HOLDS | frozenset({
    "RECOVERY_PENDING_ABSENT", "RECOVERY_PENDING_GUID_REQUIRED", "RECOVERY_PENDING_MALFORMED",
    "RECOVERY_PENDING_OWNER_MISMATCH", "RECOVERY_PRIOR_SOURCE_UNPROVEN",
    "RECOVERY_HEALTH_ONLY_REQUIRED", "RECOVERY_PRIOR_HEALTH_ONLY_UNPROVEN",
    "RECOVERY_CONFLICTING_RUN", "RECOVERY_CONFLICTING_HEALTH",
    "RECOVERY_EVIDENCE_UNAVAILABLE", "RECOVERY_STATE_CHANGED",
    "RECOVERY_ARCHIVE_CONFLICT", "RECOVERY_ARCHIVE_FAILED",
    "RECOVERY_TASK_MIGRATION_FAILED",
})

_SUPERVISOR_TASK_MISMATCH_CODES = frozenset({
    "TASK_MISSING", "TASK_DISABLED", "TASK_PATH_MISMATCH",
    "TASK_SD_UNVERIFIED", "TASK_OWNER_MISMATCH",
    "TASK_DACL_SEMANTIC_MISMATCH", "TASK_DACL_REPR_MISMATCH",
    "TASK_DEFINITION_UNVERIFIED",
    "TASK_PRINCIPAL_USER_MISMATCH", "TASK_PRINCIPAL_GROUP_MISMATCH",
    "TASK_PRINCIPAL_ID_MISMATCH", "TASK_PRINCIPAL_LOGON_MISMATCH",
    "TASK_PRINCIPAL_RUNLEVEL_MISMATCH", "TASK_ACTIONS_CONTEXT_MISMATCH",
    "TASK_TRIGGERS_PRESENT",
    "TASK_ACTION_COUNT_MISMATCH", "TASK_ACTION_TYPE_MISMATCH",
    "TASK_ACTION_PATH_MISMATCH", "TASK_ACTION_ARGS_MISMATCH", "TASK_ACTION_CWD_MISMATCH",
    "TASK_SETTING_ALLOW_DEMAND_START", "TASK_SETTING_MULTIPLE_INSTANCES",
    "TASK_SETTING_DISALLOW_ON_BATTERIES", "TASK_SETTING_STOP_ON_BATTERIES",
    "TASK_SETTING_EXEC_TIME_LIMIT", "TASK_SETTING_ALLOW_HARD_TERMINATE",
    "TASK_SETTING_RESTART_COUNT", "TASK_SETTING_RESTART_INTERVAL",
    "TASK_SETTING_RUN_ONLY_IF_IDLE", "TASK_SETTING_RUN_ONLY_IF_NETWORK",
    "TASK_SETTING_START_WHEN_AVAILABLE", "TASK_SETTING_WAKE_TO_RUN",
    "TASK_SETTING_DELETE_EXPIRED", "TASK_SETTING_HIDDEN", "TASK_SETTING_COMPATIBILITY",
})
_SUPERVISOR_TASK_PROBE_OBSERVED = frozenset({"TASK_READY", "TASK_INSTANCE_PRESENT"})
_SUPERVISOR_TASK_BASE_CODES = _SUPERVISOR_TASK_MISMATCH_CODES | _SUPERVISOR_TASK_PROBE_OBSERVED | {
    "TASK_BUSY", "TASK_PRIOR_BOOTSTRAP_READY", "TASK_PRIOR_BOOTSTRAP_BUSY",
}
_SUPERVISOR_TASK_PROBE_CODES = _SUPERVISOR_TASK_BASE_CODES | {
    "PENDING_INTENT_CHANGED", "TASK_PROBE_TIMEOUT",
    *(f"PENDING_INTENT_PRESENT_{code}" for code in _SUPERVISOR_TASK_BASE_CODES),
    *(f"PENDING_INTENT_UNKNOWN_{code}" for code in _SUPERVISOR_TASK_BASE_CODES),
}
_SUPERVISOR_PENDING_VERDICTS = frozenset({
    "ACTION_START_FAILED",
    "TASK_EXITED_NO_HEALTH",
    "RUNEX_RETURNED_EXIT_UNPROVEN",
    "STARTUP_HEALTH_MISSING",
    "ACTIVE_HEALTH_UNRESOLVED",
    "ACTIVE_UNRESOLVED",
    "UNKNOWN_PENDING_MALFORMED",
    "UNKNOWN_OWNER_DRIFT",
    "UNKNOWN_TASK_DRIFT",
    "UNKNOWN_NO_GUID_WEAK_EVIDENCE",
    "UNKNOWN_NO_GUID_LOG_UNAVAILABLE",
    "UNKNOWN_NO_GUID_UNCORRELATED",
    "UNKNOWN_EVIDENCE_UNAVAILABLE",
})
_SUPERVISOR_PENDING_TASK_CODES = _SUPERVISOR_TASK_BASE_CODES | {
    "TASK_INSTANCE_MATCH", "TASK_INSTANCE_OTHER",
}
_SUPERVISOR_PENDING_SHAPE_CODES = frozenset({
    "PEND_WITH_GUID_SAME_SRC", "PEND_WITH_GUID_PRIOR_SRC", "PEND_WITH_GUID_SESSION_DRIFT",
    "PEND_NO_GUID_SAME_SRC", "PEND_NO_GUID_PRIOR_SRC", "PEND_NO_GUID_SESSION_DRIFT",
    "PEND_OWNER_DRIFT", "PEND_MALFORMED",
})
_SUPERVISOR_PENDING_MUTEX_CODES = frozenset({"MUTEX_ACTIVE", "MUTEX_INACTIVE", "MUTEX_UNKNOWN"})
_SUPERVISOR_PENDING_HEALTH_CODES = frozenset({
    "HEALTH_ABSENT", "HEALTH_MATCH_LIVE", "HEALTH_MATCH_STALE", "HEALTH_MATCH_PROCESS_UNVERIFIED",
    "HEALTH_FOREIGN", "HEALTH_UNCORRELATED", "HEALTH_MALFORMED", "HEALTH_UNREADABLE",
})
_SUPERVISOR_PENDING_RUN_CODES = frozenset({
    "RUN_ABSENT", "RUN_MATCH", "RUN_PRIOR", "RUN_UNCORRELATED", "RUN_MALFORMED", "RUN_UNREADABLE",
})
_SUPERVISOR_UPGRADE_PROBE_CODES = frozenset({
    "UPGRADE_ABSENT", "UPGRADE_UNREADABLE", "UPGRADE_MALFORMED", "UPGRADE_MATCH_RUN", "UPGRADE_OTHER_RUN",
})
_SUPERVISOR_SOURCE_PROBE_STAGES = frozenset({
    "ACTIVE_SOURCE_PINS_INVALID", "ACTIVE_SOURCE_CURRENT_DIGEST_MISMATCH", "ACTIVE_SOURCE_DIGEST_MISMATCH",
    "ACTIVE_SOURCE_BATCH_OBJECT_MISSING", "ACTIVE_SOURCE_BATCH_TRUNCATED",
    "ACTIVE_SOURCE_RUNNER_MISSING", "ACTIVE_SOURCE_LEGACY_GUARD_MISSING", "ACTIVE_SOURCE_TICK_FENCE_MISSING",
    *(f"ACTIVE_SOURCE_{phase}_{outcome}"
      for phase in ("PRIOR_OBJECT", "ANCESTRY", "TREE", "BATCH")
      for outcome in ("FAILED", "UNAVAILABLE", "TIMEOUT", "INVALID")),
})
_SUPERVISOR_ACTIVE_PROBE_STAGES = (
    _SUPERVISOR_PENDING_RUN_CODES | _SUPERVISOR_PENDING_HEALTH_CODES | _SUPERVISOR_UPGRADE_PROBE_CODES
    | _SUPERVISOR_CHROME_PROBE_HOLDS | _SUPERVISOR_SOURCE_PROBE_STAGES | {
        "ACTIVE_TASK_UNVERIFIED", "ACTIVE_TASK_INSTANCE_MISMATCH", "ACTIVE_RUN_OWNER_DRIFT",
        "ACTIVE_RUN_SESSION_DRIFT", "ACTIVE_CONFIG_PATH_MISMATCH",
        "ACTIVE_CONFIG_DIGEST_MISMATCH", "ACTIVE_RUN_SOURCE_UNVERIFIED", "ACTIVE_HEALTH_CHANGED",
        "ACTIVE_HEALTH_MODE_UNVERIFIED", "ACTIVE_ENROLLMENT_UNVERIFIED", "ACTIVE_CHROME_UNVERIFIED",
        "ACTIVE_TASK_PROCESS_UNVERIFIED", "ACTIVE_TASK_PROBE_TIMEOUT", "ACTIVE_TASK_PROCESS_TIMEOUT",
        "ACTIVE_READ_STATE_CHANGED", "ACTIVE_READ_CHECKS_COMPLETE", "ACTIVE_STARTUP_UNVERIFIED",
        "ACTIVE_LEGACY_RETIREMENT_ELIGIBLE",
    }
)
_SUPERVISOR_PENDING_HISTORY_CODES = frozenset({
    "HIST_NA", "HIST_NEVER_RAN", "HIST_RUNNING", "HIST_QUEUED",
    "HIST_EXIT_0", "HIST_EXIT_1", "HIST_EXIT_2", "HIST_EXIT_OTHER",
    "HIST_TERMINATED", "HIST_ERR_FILE_NOT_FOUND", "HIST_ERR_ACCESS_DENIED",
    "HIST_ERR_DIR_INVALID", "HIST_ERR_CANCELLED", "HIST_ERR_SYSTEM", "HIST_UNAVAILABLE",
})
_SUPERVISOR_PENDING_LOG_CODES = frozenset({
    "LOG_NA", "LOG_EMPTY", "LOG_NO_MATCH", "LOG_UNCORRELATED_NO_GUID",
    "LOG_DENIED", "LOG_DISABLED", "LOG_UNAVAILABLE",
    "LOG_EXIT_0", "LOG_EXIT_1", "LOG_EXIT_2", "LOG_EXIT_OTHER", "LOG_ERR_SYSTEM",
    "LOG_ACTION_START_FAILED", "LOG_TERMINATED", "LOG_COMPLETED", "LOG_STARTED_NO_EXIT",
})
_SUPERVISOR_STARTUP_RECEIPT_SCHEMA = "WakeHubSupervisorStartupReceipt.v1"
_SUPERVISOR_STARTUP_STAGE_REASONS = {
    "ARGV_NONCE": frozenset({"ARGV_COUNT_INVALID", "NONCE_UNSUBSTITUTED", "NONCE_FORMAT_INVALID"}),
    "PENDING_READ": frozenset({
        "PENDING_ABSENT", "PENDING_UNREADABLE", "PENDING_KIND_OR_SIZE_INVALID",
        "PENDING_MALFORMED", "PENDING_NONCE_MISMATCH",
    }),
    "SOURCE_DIGEST": frozenset({
        "SOURCE_READ_ERROR", "SOURCE_DIGEST_MISMATCH", "SOURCE_MODULE_PRELOADED",
    }),
    "CLI_ENTRY": frozenset({"IMPORT_FAILED", "CLI_EXIT_NONZERO", "CLI_EXCEPTION"}),
    "OWNER_CONFIG": frozenset({
        "WINDOWS_INTERACTIVE_REQUIRED", "ELEVATED_CHROME_DISALLOWED", "LOCAL_OPERATOR_ONLY",
        "WINDOWS_OWNER_CONTEXT_UNVERIFIED", "OWNER_UNVERIFIED",
        "LOCAL_CONFIG_CHANGED", "LOCAL_CONFIG_REQUIRED", "LOCAL_CONFIG_STALE",
        "LOCAL_CONFIG_AMBIGUOUS", "LOCAL_CONFIG_OBSERVE_ONLY_REQUIRED", "LOCAL_CONFIG_INVALID",
    }),
    "FIRST_HEALTH": frozenset({
        "SUPERVISOR_ACTIVE", "SLOT_UNAVAILABLE",
        "WINDOWS_INTERACTIVE_REQUIRED", "CHROME_INTERACTIVE_SESSION_CONFLICT",
        "WINDOWS_OWNER_CONTEXT_UNVERIFIED", "LOCAL_OPERATOR_ONLY", "ELEVATED_CHROME_DISALLOWED",
        "WINDOWS_TOKEN_UNAVAILABLE", "ENROLLMENT_IDENTITY_UNPROVEN",
        "LOCAL_CONFIG_CHANGED", "LOCAL_CONFIG_REQUIRED", "LOCAL_CONFIG_STALE",
        "LOCAL_CONFIG_AMBIGUOUS", "LOCAL_CONFIG_OBSERVE_ONLY_REQUIRED", "LOCAL_CONFIG_INVALID",
        "LOCAL_AUTHORITY_MISSING", "LOCAL_AUTHORITY_INVALID", "LOCAL_AUTHORITY_UNPROVEN",
        "LOCAL_AUTHORITY_DRIFT", "CDP_OWNER_MISMATCH", "SUPERVISOR_STOPPED",
        "CHROME_OWNER_UNPROVEN", "CHROME_ENROLLMENT_DRIFT", "BROWSER_OWNERSHIP_UNPROVEN",
        "CDP_NOT_LOOPBACK_OWNED", "FOREIGN_PORT_OWNER", "PROCESS_IDENTITY_UNKNOWN",
        "OPERATOR_OWNERSHIP_UNPROVEN", "POWERSHELL_UNAVAILABLE", "CHROME_EXECUTABLE_UNTRUSTED",
        "CDP_PROTOCOL_UNAVAILABLE", "PROFILE_LOCK_HOLD", "ENROLLMENT_PROFILE_LOCK_UNPROVEN",
        "PROFILE_ALREADY_ACTIVE_NO_CDP", "CHROME_OBSERVATION_HOLD",
        "WINDOWS_PROBE_UNAVAILABLE", "WINDOWS_PROBE_INVALID",
        "CHROME_OBSERVATION_CHANGED", "CHROME_LAUNCH_RECONCILE_REQUIRED",
        "JOURNAL_OWNER_DRIFT", "JOURNAL_BUSY", "JOURNAL_CORRUPT", "JOURNAL_INVALID",
        "JOURNAL_SCHEMA_INCOMPATIBLE", "REGISTRATION_DRIFT",
        "HEALTH_PUBLISH_FAILED", "FIRST_HEALTH_HOLD", "HEALTH_PUBLISHED",
    }),
}
_SUPERVISOR_STARTUP_RECEIPT_CODES = frozenset({
    "STARTUP_ABSENT", "STARTUP_UNREADABLE", "STARTUP_MALFORMED", "STARTUP_UNCORRELATED", "STARTUP_FOREIGN",
    *(
        f"STARTUP_MATCH_{stage}_{reason}"
        for stage, reasons in _SUPERVISOR_STARTUP_STAGE_REASONS.items()
        for reason in reasons
    ),
    *(
        f"STARTUP_UNCORRELATED_{stage}_{reason}"
        for stage, reasons in _SUPERVISOR_STARTUP_STAGE_REASONS.items()
        for reason in reasons
    ),
})

# Pure comparator functions shared verbatim by protected task probe and deploy.
_SUPERVISOR_TASK_COMPARATOR_FUNCTIONS = r"""
function Test-SupervisorInt($value, [int]$want) {
    if ($null -eq $value -or $value -is [bool] -or $value -is [string] -or
        $value -is [float] -or $value -is [double] -or $value -is [decimal]) {
        return $false
    }
    try { return [int]$value -eq $want } catch { return $false }
}

function Test-SupervisorCompatibility($value) {
    if ($null -eq $value -or $value -is [bool] -or $value -is [string] -or
        $value -is [float] -or $value -is [double] -or $value -is [decimal]) {
        return $false
    }
    try { return [int]$value -ge 2 } catch { return $false }
}

function Test-SupervisorZeroDuration($value) {
    if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) { return $false }
    if ($value.Trim() -cne $value) { return $false }
    try {
        return [Xml.XmlConvert]::ToTimeSpan($value) -eq [TimeSpan]::Zero
    } catch {
        return $false
    }
}

function Test-SupervisorEmptySetting($value) {
    if ($null -eq $value) { return $true }
    return $value -is [string] -and $value.Length -eq 0
}

function Get-SupervisorDaclStatus($sddlText, $expectedSid) {
    if ($sddlText -isnot [string] -or [string]::IsNullOrEmpty($sddlText)) { return 'TASK_SD_UNVERIFIED' }
    try {
        $acl = [Security.AccessControl.RawSecurityDescriptor]::new($sddlText)
    } catch {
        return 'TASK_SD_UNVERIFIED'
    }
    if ($null -eq $acl.Owner -or [string]::IsNullOrEmpty($acl.Owner.Value)) {
        return 'TASK_SD_UNVERIFIED'
    }
    if ($acl.Owner.Value -cne $expectedSid) {
        return 'TASK_OWNER_MISMATCH'
    }
    if ($null -eq $acl.DiscretionaryAcl -or $acl.DiscretionaryAcl.Count -eq 0) {
        return 'TASK_DACL_SEMANTIC_MISMATCH'
    }
    $protected = ($acl.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -ne 0
    if (!$protected) {
        return 'TASK_DACL_SEMANTIC_MISMATCH'
    }
    $allowedTrustees = @('S-1-5-18', 'S-1-5-32-544', $expectedSid)
    $seen = @{}
    $reprMismatch = $false
    foreach ($ace in $acl.DiscretionaryAcl) {
        if (($ace.AceFlags -band [Security.AccessControl.AceFlags]::Inherited) -ne 0) {
            return 'TASK_DACL_SEMANTIC_MISMATCH'
        }
        if ($ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed) {
            return 'TASK_DACL_SEMANTIC_MISMATCH'
        }
        if ($ace -isnot [Security.AccessControl.KnownAce]) {
            return 'TASK_DACL_SEMANTIC_MISMATCH'
        }
        $trustee = if ($null -ne $ace.SecurityIdentifier) { $ace.SecurityIdentifier.Value } else { '' }
        if ($trustee -cnotin $allowedTrustees) {
            return 'TASK_DACL_SEMANTIC_MISMATCH'
        }
        $mask = [int]$ace.AccessMask
        # Task Scheduler maps GENERIC_ALL (0x10000000) to FILE_ALL_ACCESS (0x001F01FF) on persisted tasks.
        if ($mask -ne 0x10000000 -and $mask -ne 0x001F01FF -and $mask -ne 0x101F01FF) {
            return 'TASK_DACL_SEMANTIC_MISMATCH'
        }
        if ($ace -isnot [Security.AccessControl.CommonAce] -or
            $ace.AceFlags -ne [Security.AccessControl.AceFlags]::None -or
            $mask -eq 0x101F01FF -or
            $seen.ContainsKey($trustee)) {
            $reprMismatch = $true
        }
        $seen[$trustee] = $true
    }
    foreach ($required in $allowedTrustees) {
        if (!$seen.ContainsKey($required)) {
            return 'TASK_DACL_SEMANTIC_MISMATCH'
        }
    }
    if ($reprMismatch) {
        return 'TASK_DACL_REPR_MISMATCH'
    }
    return 'OK'
}

function Get-SupervisorRegistrationStatus($registered, $expected, $expectedAction, $name, $sid) {
    if ($registered.Enabled -isnot [bool] -or !$registered.Enabled) { return 'TASK_DISABLED' }
    if ($registered.Path -isnot [string] -or $registered.Path -cne ('\' + $name)) { return 'TASK_PATH_MISMATCH' }
    try {
        $sdText = $registered.GetSecurityDescriptor(5)
    } catch {
        return 'TASK_SD_UNVERIFIED'
    }
    $daclStatus = Get-SupervisorDaclStatus $sdText $sid
    if ($daclStatus -cne 'OK') { return $daclStatus }
    $definition = $registered.Definition
    if (!$definition -or !$definition.Principal -or !$definition.Triggers -or
        !$definition.Actions -or !$definition.Settings) {
        return 'TASK_DEFINITION_UNVERIFIED'
    }
    $principal = $definition.Principal
    $principalSid = $principal.UserId
    if ($principalSid -isnot [string] -or $principalSid -cne $sid) {
        if ($principalSid -isnot [string] -or [string]::IsNullOrEmpty($principalSid)) {
            return 'TASK_PRINCIPAL_USER_MISMATCH'
        }
        try {
            $principalSid = [Security.Principal.NTAccount]::new($principalSid).Translate(
                [Security.Principal.SecurityIdentifier]).Value
        } catch {
            return 'TASK_PRINCIPAL_USER_MISMATCH'
        }
    }
    if ($principalSid -cne $sid) { return 'TASK_PRINCIPAL_USER_MISMATCH' }
    if ($principal.GroupId) { return 'TASK_PRINCIPAL_GROUP_MISMATCH' }
    if ($principal.Id -isnot [string] -or $principal.Id -cne $expected.Principal.Id) {
        return 'TASK_PRINCIPAL_ID_MISMATCH'
    }
    if (!(Test-SupervisorInt $principal.LogonType 3)) { return 'TASK_PRINCIPAL_LOGON_MISMATCH' }
    if (!(Test-SupervisorInt $principal.RunLevel 0)) { return 'TASK_PRINCIPAL_RUNLEVEL_MISMATCH' }
    if ($definition.Actions.Context -isnot [string] -or $definition.Actions.Context -cne $expected.Actions.Context) {
        return 'TASK_ACTIONS_CONTEXT_MISMATCH'
    }
    if (!(Test-SupervisorInt $definition.Triggers.Count 0)) { return 'TASK_TRIGGERS_PRESENT' }
    if (!(Test-SupervisorInt $definition.Actions.Count 1)) { return 'TASK_ACTION_COUNT_MISMATCH' }
    $actual = $definition.Actions.Item(1)
    if (!$actual -or !(Test-SupervisorInt $actual.Type 0)) { return 'TASK_ACTION_TYPE_MISMATCH' }
    if ($actual.Path -isnot [string] -or [string]::IsNullOrEmpty($actual.Path) -or
        $actual.Path -ine $expectedAction.Path) {
        return 'TASK_ACTION_PATH_MISMATCH'
    }
    if ($actual.Arguments -isnot [string] -or $actual.Arguments -cne $expectedAction.Arguments) {
        return 'TASK_ACTION_ARGS_MISMATCH'
    }
    if ($actual.WorkingDirectory -isnot [string] -or [string]::IsNullOrEmpty($actual.WorkingDirectory) -or
        $actual.WorkingDirectory -ine $expectedAction.WorkingDirectory) {
        return 'TASK_ACTION_CWD_MISMATCH'
    }
    $settings = $definition.Settings
    if ($settings.AllowDemandStart -isnot [bool] -or !$settings.AllowDemandStart) {
        return 'TASK_SETTING_ALLOW_DEMAND_START'
    }
    if (!(Test-SupervisorInt $settings.MultipleInstances 2)) {
        return 'TASK_SETTING_MULTIPLE_INSTANCES'
    }
    if ($settings.DisallowStartIfOnBatteries -isnot [bool] -or $settings.DisallowStartIfOnBatteries) {
        return 'TASK_SETTING_DISALLOW_ON_BATTERIES'
    }
    if ($settings.StopIfGoingOnBatteries -isnot [bool] -or $settings.StopIfGoingOnBatteries) {
        return 'TASK_SETTING_STOP_ON_BATTERIES'
    }
    if (!(Test-SupervisorZeroDuration $settings.ExecutionTimeLimit)) {
        return 'TASK_SETTING_EXEC_TIME_LIMIT'
    }
    if ($settings.AllowHardTerminate -isnot [bool] -or !$settings.AllowHardTerminate) {
        return 'TASK_SETTING_ALLOW_HARD_TERMINATE'
    }
    if (!(Test-SupervisorInt $settings.RestartCount 0)) {
        return 'TASK_SETTING_RESTART_COUNT'
    }
    if (!(Test-SupervisorEmptySetting $settings.RestartInterval)) {
        return 'TASK_SETTING_RESTART_INTERVAL'
    }
    if ($settings.RunOnlyIfIdle -isnot [bool] -or $settings.RunOnlyIfIdle) {
        return 'TASK_SETTING_RUN_ONLY_IF_IDLE'
    }
    if ($settings.RunOnlyIfNetworkAvailable -isnot [bool] -or $settings.RunOnlyIfNetworkAvailable) {
        return 'TASK_SETTING_RUN_ONLY_IF_NETWORK'
    }
    if ($settings.StartWhenAvailable -isnot [bool] -or $settings.StartWhenAvailable) {
        return 'TASK_SETTING_START_WHEN_AVAILABLE'
    }
    if ($settings.WakeToRun -isnot [bool] -or $settings.WakeToRun) {
        return 'TASK_SETTING_WAKE_TO_RUN'
    }
    if (!(Test-SupervisorEmptySetting $settings.DeleteExpiredTaskAfter)) {
        return 'TASK_SETTING_DELETE_EXPIRED'
    }
    if ($settings.Hidden -isnot [bool] -or $settings.Hidden) {
        return 'TASK_SETTING_HIDDEN'
    }
    if (!(Test-SupervisorCompatibility $settings.Compatibility)) {
        return 'TASK_SETTING_COMPATIBILITY'
    }
    return 'OK'
}
"""

_SUPERVISOR_TASK_SETUP_SCRIPT = r"""
$sid = $env:AI_COMMERCE_SUPERVISOR_SID
if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne $sid) { throw 'Owner changed' }
$name = 'AI-Commerce-WakeHub-Supervisor'
$sddl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')'
$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder = $service.GetFolder('\')
$expected = $service.NewTask(0)
$expected.Principal.Id = 'WakeHubSupervisorOwner'
$expected.Principal.UserId = $sid
$expected.Principal.LogonType = 3 # TASK_LOGON_INTERACTIVE_TOKEN
$expected.Principal.RunLevel = 0 # TASK_RUNLEVEL_LUA
$expected.Settings.MultipleInstances = 2 # TASK_INSTANCES_IGNORE_NEW
$expected.Settings.AllowDemandStart = $true
$expected.Settings.DisallowStartIfOnBatteries = $false
$expected.Settings.StopIfGoingOnBatteries = $false
$expected.Settings.ExecutionTimeLimit = 'PT0S'
$expected.Actions.Context = $expected.Principal.Id
$action = $expected.Actions.Create(0)
$action.Path = $env:AI_COMMERCE_SUPERVISOR_IMAGE
$action.Arguments = $env:AI_COMMERCE_SUPERVISOR_ARGUMENTS
$action.WorkingDirectory = $env:AI_COMMERCE_SUPERVISOR_ROOT

function Read-Registration {
    try { $folder.GetTask($name) }
    catch {
        if ($_.Exception.GetBaseException().HResult -ne -2147024894) { throw }
    }
}
"""

_SUPERVISOR_PENDING_DIAGNOSTIC_FUNCTIONS = r"""
function Get-SupervisorHistoryCode($lastRunTime, $lastTaskResult) {
    if ($null -eq $lastTaskResult -or $lastTaskResult -is [bool] -or $lastTaskResult -is [string] -or
        $lastTaskResult -is [float] -or $lastTaskResult -is [double] -or $lastTaskResult -is [decimal]) {
        return 'HIST_UNAVAILABLE'
    }
    try {
        $u = [BitConverter]::ToUInt32([BitConverter]::GetBytes([int]$lastTaskResult), 0)
    } catch {
        return 'HIST_UNAVAILABLE'
    }
    if ($u -eq 0x00041303) { return 'HIST_NEVER_RAN' }
    if ($null -eq $lastRunTime -or $lastRunTime -isnot [DateTime]) { return 'HIST_UNAVAILABLE' }
    if ($lastRunTime.Year -lt 2000) {
        if ($u -eq 0) { return 'HIST_NEVER_RAN' }
        return 'HIST_UNAVAILABLE'
    }
    if ($u -eq 0x00000000) { return 'HIST_EXIT_0' }
    if ($u -eq 0x00000001) { return 'HIST_EXIT_1' }
    if ($u -eq 0x00000002) { return 'HIST_EXIT_2' }
    if ($u -eq 0x00041301) { return 'HIST_RUNNING' }
    if ($u -eq 0x00041306) { return 'HIST_TERMINATED' }
    if ($u -eq 0x00041325) { return 'HIST_QUEUED' }
    if ($u -eq 2147942402) { return 'HIST_ERR_FILE_NOT_FOUND' } # 0x80070002
    if ($u -eq 2147942405) { return 'HIST_ERR_ACCESS_DENIED' } # 0x80070005
    if ($u -eq 2147942667) { return 'HIST_ERR_DIR_INVALID' } # 0x8007010B
    if ($u -eq 2147943623 -or $u -eq 3221225786) { return 'HIST_ERR_CANCELLED' } # 0x800704C7 / 0xC000013A
    if ($u -ge 3 -and $u -le 0xFFFF) { return 'HIST_EXIT_OTHER' }
    return 'HIST_ERR_SYSTEM'
}

function Convert-SupervisorEventResult($raw) {
    if ($null -eq $raw) { return $null }
    if ($raw -is [int] -or $raw -is [uint32] -or $raw -is [long]) {
        try {
            $signed = [int64]$raw
            if ($signed -lt -2147483648 -or $signed -gt 4294967295) { return $null }
            return [uint32]($signed -band 0xFFFFFFFF)
        } catch {
            return $null
        }
    }
    if ($raw -is [string]) {
        $text = $raw.Trim()
        if ($text -cmatch '^0[xX][0-9a-fA-F]{1,8}$') {
            try { return [Convert]::ToUInt32($text.Substring(2), 16) } catch { return $null }
        }
        if ($text -cmatch '^-?[0-9]{1,10}$') {
            try {
                $signed = [int64]$text
                if ($signed -lt -2147483648 -or $signed -gt 4294967295) { return $null }
                return [uint32]($signed -band 0xFFFFFFFF)
            } catch {
                return $null
            }
        }
    }
    return $null
}

function Get-SupervisorOperationalLogCode($events, $taskPath, $pendingGuid) {
    $hasGuid = ($pendingGuid -is [string] -and $pendingGuid -cmatch '^[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12}$')
    $targetGuid = if ($hasGuid) { [Guid]::Parse($pendingGuid) } else { $null }
    $sawTaskEvent = $false
    $actionCompleted = $null
    $sawActionStartFailed = $false
    $sawTerminated = $false
    $sawCompleted = $false
    $sawStarted = $false
    foreach ($ev in @($events)) {
        if ($null -eq $ev) { continue }
        if ([string]$ev.TaskName -cne $taskPath) { continue }
        $sawTaskEvent = $true
        if (!$hasGuid) { continue }
        $matched = $false
        foreach ($candidate in @($ev.InstanceId, $ev.ActivityId)) {
            if ($candidate -is [Guid]) {
                if ($candidate -eq $targetGuid) { $matched = $true; break }
            } elseif ($candidate -is [string] -and ![string]::IsNullOrWhiteSpace($candidate)) {
                try {
                    if ([Guid]::Parse($candidate) -eq $targetGuid) { $matched = $true; break }
                } catch {}
            }
        }
        if (!$matched) { continue }
        $id = try { [int]$ev.Id } catch { -1 }
        if ($id -eq 201) {
            $code = Convert-SupervisorEventResult $ev.ResultCode
            if ($null -ne $code -and $null -eq $actionCompleted) { $actionCompleted = $code }
        } elseif ($id -in @(103, 202, 203)) {
            $sawActionStartFailed = $true
        } elseif ($id -eq 111) {
            $sawTerminated = $true
        } elseif ($id -eq 102) {
            $sawCompleted = $true
        } elseif ($id -in @(100, 110, 129, 200)) {
            $sawStarted = $true
        }
    }
    if (!$hasGuid) {
        if ($sawTaskEvent) { return 'LOG_UNCORRELATED_NO_GUID' }
        return 'LOG_EMPTY'
    }
    if ($null -ne $actionCompleted) {
        if ($actionCompleted -eq 0) { return 'LOG_EXIT_0' }
        if ($actionCompleted -eq 1) { return 'LOG_EXIT_1' }
        if ($actionCompleted -eq 2) { return 'LOG_EXIT_2' }
        if ($actionCompleted -ge 3 -and $actionCompleted -le 0xFFFF) { return 'LOG_EXIT_OTHER' }
        return 'LOG_ERR_SYSTEM'
    }
    if ($sawActionStartFailed) { return 'LOG_ACTION_START_FAILED' }
    if ($sawTerminated) { return 'LOG_TERMINATED' }
    if ($sawCompleted) { return 'LOG_COMPLETED' }
    if ($sawStarted) { return 'LOG_STARTED_NO_EXIT' }
    if (!$sawTaskEvent) { return 'LOG_EMPTY' }
    return 'LOG_NO_MATCH'
}

function Read-SupervisorOperationalLog($taskPath, $pendingGuid) {
    $logName = 'Microsoft-Windows-TaskScheduler/Operational'
    $cfgEnabled = $null
    $cfg = $null
    try {
        $cfg = [System.Diagnostics.Eventing.Reader.EventLogConfiguration]::new($logName)
        $cfgEnabled = [bool]$cfg.IsEnabled
    } catch {
        $cfgEnabled = $null
    } finally {
        if ($null -ne $cfg) { $cfg.Dispose() }
    }
    if ($cfgEnabled -eq $false) { return 'LOG_DISABLED' }
    $xpath = "*[System[(EventID=100 or EventID=101 or EventID=102 or EventID=103 or EventID=110 or EventID=111 or EventID=129 or EventID=200 or EventID=201 or EventID=202 or EventID=203)] and EventData[Data[@Name='TaskName']='" + $taskPath + "']]"
    $reader = $null
    try {
        $query = [System.Diagnostics.Eventing.Reader.EventLogQuery]::new(
            $logName, [System.Diagnostics.Eventing.Reader.PathType]::LogName, $xpath
        )
        $query.ReverseDirection = $true
        $reader = [System.Diagnostics.Eventing.Reader.EventLogReader]::new($query)
    } catch [System.UnauthorizedAccessException] {
        return 'LOG_DENIED'
    } catch {
        return 'LOG_UNAVAILABLE'
    }
    $rows = [System.Collections.Generic.List[object]]::new()
    try {
        while ($rows.Count -lt 50) {
            $record = $reader.ReadEvent()
            if ($null -eq $record) { break }
            try {
                $xml = [System.Xml.XmlDocument]::new()
                $xml.XmlResolver = $null
                $xml.LoadXml($record.ToXml())
                $ns = [System.Xml.XmlNamespaceManager]::new($xml.NameTable)
                $ns.AddNamespace('e', 'http://schemas.microsoft.com/win/2004/08/events/event')
                $taskNode = $xml.SelectSingleNode('/e:Event/e:EventData/e:Data[@Name="TaskName"]', $ns)
                $instNode = $xml.SelectSingleNode('/e:Event/e:EventData/e:Data[@Name="InstanceId"]', $ns)
                $resNode = $xml.SelectSingleNode('/e:Event/e:EventData/e:Data[@Name="ResultCode"]', $ns)
                $rows.Add([pscustomobject]@{
                    Id = $record.Id
                    ActivityId = $record.ActivityId
                    TaskName = if ($null -ne $taskNode) { $taskNode.InnerText } else { '' }
                    InstanceId = if ($null -ne $instNode) { $instNode.InnerText } else { '' }
                    ResultCode = if ($null -ne $resNode) { $resNode.InnerText } else { $null }
                })
            } finally {
                $record.Dispose()
            }
        }
    } catch [System.UnauthorizedAccessException] {
        return 'LOG_DENIED'
    } catch {
        return 'LOG_UNAVAILABLE'
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
    }
    if ($rows.Count -eq 0 -and $cfgEnabled -ne $true) { return 'LOG_UNAVAILABLE' }
    return (Get-SupervisorOperationalLogCode $rows $taskPath $pendingGuid)
}
"""

_SUPERVISOR_TASK_PROBE_SCRIPT = (
    _SUPERVISOR_TASK_COMPARATOR_FUNCTIONS
    + _SUPERVISOR_TASK_SETUP_SCRIPT
    + _SUPERVISOR_PENDING_DIAGNOSTIC_FUNCTIONS
    + r"""
$mode = $env:AI_COMMERCE_SUPERVISOR_MODE
if ($mode -cne 'probe' -and $mode -cne 'probe_pending') { throw 'Read-only probe mode required' }
$task = Read-Registration
if (!$task) {
    if ($mode -ceq 'probe_pending') { 'TASK_MISSING+HIST_NA+LOG_NA' } else { 'TASK_MISSING' }
    exit
}
$status = Get-SupervisorRegistrationStatus $task $expected $action $name $sid
$priorMatch = $false
if ($status -ceq 'TASK_ACTION_ARGS_MISMATCH' -and ![string]::IsNullOrEmpty($env:AI_COMMERCE_SUPERVISOR_PRIOR_ARGUMENTS)) {
    $priorAction = [pscustomobject]@{
        Path = $action.Path
        Arguments = $env:AI_COMMERCE_SUPERVISOR_PRIOR_ARGUMENTS
        WorkingDirectory = $action.WorkingDirectory
    }
    if ((Get-SupervisorRegistrationStatus $task $expected $priorAction $name $sid) -ceq 'OK') {
        $priorMatch = $true
        $status = 'OK'
    }
}
if ($status -cne 'OK') {
    if ($mode -ceq 'probe_pending') { $status + '+HIST_NA+LOG_NA' } else { $status }
    exit
}
$instances = $task.GetInstances(0)
if ($null -eq $instances) { throw 'Instances unavailable' }
$pendingGuid = if ($mode -ceq 'probe_pending') { [string]$env:AI_COMMERCE_SUPERVISOR_PENDING_GUID } else { '' }
$hasPendingGuid = ($pendingGuid -cmatch '^[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12}$')
if ($priorMatch) {
    if ($task.State -eq 3 -and $instances.Count -eq 0) {
        $taskCode = 'TASK_PRIOR_BOOTSTRAP_READY'
    } else {
        $taskCode = 'TASK_PRIOR_BOOTSTRAP_BUSY'
    }
} elseif ($task.State -eq 3 -and $instances.Count -eq 0) {
    $taskCode = 'TASK_READY'
} elseif ($instances.Count -eq 1 -and $instances.Item(1).State -eq 4) {
    $instance = $instances.Item(1)
    if (!$instance -or $instance.Path -cne ('\' + $name) -or !$instance.InstanceGuid) {
        throw 'Instance unavailable'
    }
    $parsedInstance = [Guid]::Parse($instance.InstanceGuid)
    if ($mode -ceq 'probe_pending' -and $hasPendingGuid) {
        $taskCode = if ($parsedInstance -eq [Guid]::Parse($pendingGuid)) {
            'TASK_INSTANCE_MATCH'
        } else {
            'TASK_INSTANCE_OTHER'
        }
    } else {
        $taskCode = 'TASK_INSTANCE_PRESENT'
    }
} else {
    $taskCode = 'TASK_BUSY'
}
if ($mode -ceq 'probe') {
    $taskCode; exit
}
$histCode = 'HIST_UNAVAILABLE'
try {
    $histCode = Get-SupervisorHistoryCode $task.LastRunTime $task.LastTaskResult
} catch {
    $histCode = 'HIST_UNAVAILABLE'
}
$logCode = Read-SupervisorOperationalLog ('\' + $name) $pendingGuid
($taskCode + '+' + $histCode + '+' + $logCode)
"""
)

_SUPERVISOR_TASK_SCRIPT = _SUPERVISOR_TASK_COMPARATOR_FUNCTIONS + _SUPERVISOR_TASK_SETUP_SCRIPT + r"""
$task = Read-Registration
$mode = $env:AI_COMMERCE_SUPERVISOR_MODE
if ($mode -ceq 'inspect_recovery' -or $mode -ceq 'migrate') {
    if (!$task) { 'MISSING'; exit }
    $priorArgs = $env:AI_COMMERCE_SUPERVISOR_PRIOR_ARGUMENTS
    $priorAction = if (![string]::IsNullOrEmpty($priorArgs)) {
        [pscustomobject]@{
            Path = $action.Path
            Arguments = $priorArgs
            WorkingDirectory = $action.WorkingDirectory
        }
    } else { $null }
    $status = Get-SupervisorRegistrationStatus $task $expected $action $name $sid
    $isPrior = $false
    if ($status -ceq 'TASK_ACTION_ARGS_MISMATCH' -and $null -ne $priorAction) {
        if ((Get-SupervisorRegistrationStatus $task $expected $priorAction $name $sid) -ceq 'OK') {
            $isPrior = $true
        }
    }
    if ($mode -ceq 'inspect_recovery') {
        if ($status -cne 'OK' -and !$isPrior) { 'CONFLICT'; exit }
        $instances = $task.GetInstances(0)
        if ($null -eq $instances) { throw 'Instances unavailable' }
        if ($task.State -eq 3 -and $instances.Count -eq 0) {
            if ($isPrior) { 'PRIOR_READY' } else { 'READY' }
            exit
        }
        'BUSY'; exit
    }
    # Explicit in-place migration ONLY from exact known prior registered definition while idle.
    if (!$isPrior) { 'CONFLICT'; exit }
    $instances = $task.GetInstances(0)
    if ($null -eq $instances) { throw 'Instances unavailable' }
    if ($task.State -ne 3 -or $instances.Count -ne 0) { 'BUSY'; exit }
    # TASK_UPDATE (4) | TASK_DONT_ADD_PRINCIPAL_ACE (16) | TASK_IGNORE_REGISTRATION_TRIGGERS (32) = 52.
    $null = $folder.RegisterTaskDefinition($name, $expected, 52, $sid, $null, 3, $sddl)
    $updated = Read-Registration
    if (!$updated) { 'CONFLICT'; exit }
    if ((Get-SupervisorRegistrationStatus $updated $expected $action $name $sid) -cne 'OK') { 'CONFLICT'; exit }
    $postInstances = $updated.GetInstances(0)
    if ($null -eq $postInstances -or $updated.State -ne 3 -or $postInstances.Count -ne 0) { 'BUSY'; exit }
    'MIGRATED'; exit
}
if (!$task -and $mode -ceq 'install') {
    # TASK_CREATE | TASK_DONT_ADD_PRINCIPAL_ACE | TASK_IGNORE_REGISTRATION_TRIGGERS.
    try { $null = $folder.RegisterTaskDefinition($name, $expected, 50, $sid, $null, 3, $sddl) }
    catch {
        if ($_.Exception.GetBaseException().HResult -ne -2147024713) { throw }
    }
    $task = Read-Registration
}
if (!$task) { 'MISSING'; exit }
if ((Get-SupervisorRegistrationStatus $task $expected $action $name $sid) -cne 'OK') { 'CONFLICT'; exit }
$instances = $task.GetInstances(0)
if ($null -eq $instances) { throw 'Instances unavailable' }
if ($mode -ceq 'start') {
    if ($task.State -ne 3 -or $instances.Count -ne 0) { 'NOT_ATTEMPTED'; exit }
    $nonce = $env:AI_COMMERCE_SUPERVISOR_NONCE
    $session = [int]$env:AI_COMMERCE_SUPERVISOR_SESSION
    if ($nonce -cnotmatch '^[a-f0-9]{32}$' -or $session -le 0) { throw 'Invalid start request' }
    # TASK_RUN_USE_SESSION_ID | TASK_RUN_USER_SID. The only substitution is a nonce.
    $instance = $task.RunEx($nonce, 12, $session, $sid)
} elseif ($task.State -eq 3 -and $instances.Count -eq 0) {
    'READY'; exit
} elseif ($instances.Count -eq 1 -and $instances.Item(1).State -eq 4) {
    $instance = $instances.Item(1)
} else {
    'BUSY'; exit
}
if (!$instance -or $instance.Path -cne ('\' + $name) -or !$instance.InstanceGuid) {
    throw 'Instance unavailable'
}
$guid = [Guid]::Parse($instance.InstanceGuid).ToString('D')
if ($mode -ceq 'prove_upgrade') {
    if ($guid -cne $env:AI_COMMERCE_SUPERVISOR_PENDING_GUID) { 'CONFLICT'; exit }
    $processId = [uint32]$env:AI_COMMERCE_SUPERVISOR_PROCESS_ID
    $process = @(Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -OperationTimeoutSec 3)
    if ($process.Count -ne 1 -or !$process[0].CommandLine) { 'CONFLICT'; exit }
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($process[0].CommandLine))
    exit
}
$guid
"""

# Exact known prior registered bootstrap definition (for verified in-place migration only).
_SUPERVISOR_BOOTSTRAP_PRIOR = """\
import hashlib, importlib.machinery, json, re, runpy, sys, winreg
from pathlib import Path

root, dependencies, nonce = sys.argv[1:]
if not re.fullmatch(r'[a-f0-9]{32}', nonce):
    raise SystemExit(2)
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\\AI-Commerce\\WakeHubSupervisor') as key:
    raw, kind = winreg.QueryValueEx(key, 'StartPending')
if kind != winreg.REG_SZ or not 0 < len(raw) <= 2048:
    raise SystemExit(2)
intent = json.loads(raw)
if intent['nonce'] != nonce:
    raise SystemExit(2)
source = Path(root) / 'src'
files = {path.relative_to(source).as_posix(): path.read_bytes()
         for path in (source / 'ai_commerce').rglob('*.py')}
manifest = [[name, hashlib.sha256(raw).hexdigest()] for name, raw in sorted(files.items())]
observed = hashlib.sha256(json.dumps(manifest, separators=(',', ':')).encode('ascii')).hexdigest()
if (observed != intent['source_digest']
        or any(name == 'ai_commerce' or name.startswith('ai_commerce.') for name in sys.modules)):
    raise SystemExit(2)
loader = importlib.machinery.SourceFileLoader
default_code = loader.get_code
def get_code(self, fullname):
    if fullname == 'ai_commerce' or fullname.startswith('ai_commerce.'):
        name = Path(self.path).relative_to(source).as_posix()
        return self.source_to_code(files[name], self.path)
    return default_code(self, fullname)
loader.get_code = get_code
sys.path[:0] = [str(source), dependencies]
sys.argv = ['wakehub_runtime', 'supervise', '--observe-only', '--config', intent['config'], '--repository-root', root]
runpy.run_module('ai_commerce.wakehub_runtime', run_name='__main__', alter_sys=True,
                 init_globals={'_PROTECTED_DEPLOYMENT': intent})
"""

# Launch the canonical CLI using the exact source bytes read and hashed here.
# Records one bounded private StartupReceipt across startup boundaries without
# leaking raw paths/args/secrets and correlates only after nonce/pin verification.
_SUPERVISOR_BOOTSTRAP = """\
import hashlib, importlib.machinery, json, os, re, runpy, sys, time, winreg
from pathlib import Path

_KEY = r'Software\\AI-Commerce\\WakeHubSupervisor'
_PINS = ('source_sha', 'source_digest', 'config_digest', 'desktop', 'session', 'nonce')
def _rec(stage, reason, pins=None):
    row = {'schema': 'WakeHubSupervisorStartupReceipt.v1', 'stage': stage, 'reason': reason,
           'correlated': pins is not None, 'pid': os.getpid(), 'observed_ns': time.monotonic_ns()}
    for k in _PINS:
        row[k] = pins[k] if pins is not None else None
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _KEY, access=winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, 'StartupReceipt', 0, winreg.REG_SZ, json.dumps(row, separators=(',', ':')))
            winreg.FlushKey(key)
    except Exception:
        pass
def _fail(stage, reason, pins=None):
    _rec(stage, reason, pins)
    raise SystemExit(2)

if len(sys.argv) != 4:
    _fail('ARGV_NONCE', 'ARGV_COUNT_INVALID')
root, dependencies, nonce = sys.argv[1:]
if nonce == '$(Arg0)':
    _fail('ARGV_NONCE', 'NONCE_UNSUBSTITUTED')
if not re.fullmatch(r'[a-f0-9]{32}', nonce):
    _fail('ARGV_NONCE', 'NONCE_FORMAT_INVALID')
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as key:
        raw, kind = winreg.QueryValueEx(key, 'StartPending')
except FileNotFoundError:
    _fail('PENDING_READ', 'PENDING_ABSENT')
except OSError:
    _fail('PENDING_READ', 'PENDING_UNREADABLE')
if kind != winreg.REG_SZ or type(raw) is not str or not 0 < len(raw) <= 2048:
    _fail('PENDING_READ', 'PENDING_KIND_OR_SIZE_INVALID')
try:
    intent = json.loads(raw)
except Exception:
    _fail('PENDING_READ', 'PENDING_MALFORMED')
if (type(intent) is not dict
        or not {'source_sha', 'source_digest', 'config_digest', 'desktop', 'session', 'sid', 'config', 'nonce'} <= intent.keys()
        or type(intent['source_sha']) is not str or not re.fullmatch(r'[a-f0-9]{40}', intent['source_sha'])
        or any(type(intent[k]) is not str or not re.fullmatch(r'[a-f0-9]{64}', intent[k]) for k in ('source_digest', 'config_digest', 'desktop'))
        or type(intent['session']) is not int or intent['session'] <= 0
        or type(intent['sid']) is not str or type(intent['config']) is not str
        or type(intent['nonce']) is not str or not re.fullmatch(r'[a-f0-9]{32}', intent['nonce'])):
    _fail('PENDING_READ', 'PENDING_MALFORMED')
if intent['nonce'] != nonce:
    _fail('PENDING_READ', 'PENDING_NONCE_MISMATCH')
pins = {k: intent[k] for k in _PINS}
source = Path(root) / 'src'
try:
    files = {path.relative_to(source).as_posix(): path.read_bytes()
             for path in (source / 'ai_commerce').rglob('*.py')}
except OSError:
    _fail('SOURCE_DIGEST', 'SOURCE_READ_ERROR', pins)
manifest = [[name, hashlib.sha256(raw).hexdigest()] for name, raw in sorted(files.items())]
observed = hashlib.sha256(json.dumps(manifest, separators=(',', ':')).encode('ascii')).hexdigest()
if observed != intent['source_digest']:
    _fail('SOURCE_DIGEST', 'SOURCE_DIGEST_MISMATCH', pins)
if any(name == 'ai_commerce' or name.startswith('ai_commerce.') for name in sys.modules):
    _fail('SOURCE_DIGEST', 'SOURCE_MODULE_PRELOADED', pins)
loader = importlib.machinery.SourceFileLoader
default_code = loader.get_code
def get_code(self, fullname):
    if fullname == 'ai_commerce' or fullname.startswith('ai_commerce.'):
        name = Path(self.path).relative_to(source).as_posix()
        return self.source_to_code(files[name], self.path)
    return default_code(self, fullname)
loader.get_code = get_code
sys.path[:0] = [str(source), dependencies]
sys.argv = ['wakehub_runtime', 'supervise', '--observe-only', '--config', intent['config'], '--repository-root', root]
reported = [False]
def _report_stage(stage, reason):
    reported[0] = True
    _rec(stage, reason, pins)
try:
    runpy.run_module('ai_commerce.wakehub_runtime', run_name='__main__', alter_sys=True,
                     init_globals={'_PROTECTED_DEPLOYMENT': intent, '_PROTECTED_STARTUP_RECEIPT': _report_stage})
except ImportError:
    _fail('CLI_ENTRY', 'IMPORT_FAILED', pins)
except SystemExit as exc:
    if exc.code not in (None, 0) and not reported[0]:
        _rec('CLI_ENTRY', 'CLI_EXIT_NONZERO', pins)
    raise
except Exception:
    if not reported[0]:
        _rec('CLI_ENTRY', 'CLI_EXCEPTION', pins)
    raise SystemExit(2)
"""

_SUPERVISOR_INSTANCE = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")


def _run_supervisor_task_script(
    acquisition, script, executable, arguments, root, owner, *,
    mode="inspect", nonce="", pending_guid="", prior_arguments="", process_id=0,
):
    # Select a verified native PowerShell without consulting a Chrome task.
    shell = acquisition._powershell(owner_action=lambda bootstrap: bootstrap)
    env = acquisition._child_environment()
    env.update(
        AI_COMMERCE_SUPERVISOR_SID=owner[2], AI_COMMERCE_SUPERVISOR_SESSION=str(owner[1]),
        AI_COMMERCE_SUPERVISOR_IMAGE=str(executable), AI_COMMERCE_SUPERVISOR_ARGUMENTS=arguments,
        AI_COMMERCE_SUPERVISOR_ROOT=str(root), AI_COMMERCE_SUPERVISOR_MODE=mode,
        AI_COMMERCE_SUPERVISOR_NONCE=nonce,
    )
    if mode in ("probe_pending", "prove_upgrade"):
        env["AI_COMMERCE_SUPERVISOR_PENDING_GUID"] = pending_guid
    if mode == "prove_upgrade":
        env["AI_COMMERCE_SUPERVISOR_PROCESS_ID"] = str(process_id)
    if prior_arguments:
        env["AI_COMMERCE_SUPERVISOR_PRIOR_ARGUMENTS"] = prior_arguments
    result = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command",
         acquisition._POWERSHELL_TASK_READ_PREAMBLE + script],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=True, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stdout.strip().decode("ascii")


def _supervisor_upgrade_task_process(acquisition, executable, arguments, root, owner, intent, pid):
    # The native comparator must still see the exact registration and sole GUID.
    # Bind the action process to the nonce that this RunEx instance received.
    raw = _run_supervisor_task_script(
        acquisition, _SUPERVISOR_TASK_SCRIPT, executable, arguments, root, owner,
        mode="prove_upgrade", pending_guid=intent["instance"], process_id=pid,
    )
    if raw in {"MISSING", "READY", "BUSY", "CONFLICT"} or not 0 < len(raw) <= 131072:
        return False
    running = acquisition._commandline_argv(base64.b64decode(raw, validate=True).decode("utf-8"))
    expected = acquisition._commandline_argv(
        subprocess.list2cmdline([str(executable)]) + " " + arguments.removesuffix("$(Arg0)") + intent["nonce"],
    )
    return Path(running[0]) == executable and running[1:] == expected[1:]


def _supervisor_task(
    acquisition, executable, arguments, root, owner, *, mode="inspect", nonce="", prior_arguments="",
):
    code = _run_supervisor_task_script(
        acquisition, _SUPERVISOR_TASK_SCRIPT, executable, arguments, root, owner,
        mode=mode, nonce=nonce, prior_arguments=prior_arguments,
    )
    allowed = {"MISSING", "CONFLICT", "READY", "BUSY", "NOT_ATTEMPTED"}
    if mode == "inspect_recovery":
        allowed = {"MISSING", "CONFLICT", "READY", "PRIOR_READY", "BUSY"}
    elif mode == "migrate":
        allowed = {"MISSING", "CONFLICT", "BUSY", "MIGRATED"}
    if code not in allowed and (mode in ("inspect_recovery", "migrate") or not _SUPERVISOR_INSTANCE.fullmatch(code)):
        deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
    return code


def _supervisor_task_probe(acquisition, executable, arguments, root, owner, *, prior_arguments=""):
    code = _run_supervisor_task_script(
        acquisition, _SUPERVISOR_TASK_PROBE_SCRIPT, executable, arguments, root, owner,
        mode="probe", nonce="", prior_arguments=prior_arguments,
    )
    if code not in _SUPERVISOR_TASK_BASE_CODES:
        deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
    return code


def _supervisor_task_probe_pending(
    acquisition, executable, arguments, root, owner, *, pending_guid="", prior_arguments="",
):
    if pending_guid and not _SUPERVISOR_INSTANCE.fullmatch(pending_guid):
        deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
    raw = _run_supervisor_task_script(
        acquisition, _SUPERVISOR_TASK_PROBE_SCRIPT, executable, arguments, root, owner,
        mode="probe_pending", nonce="", pending_guid=pending_guid, prior_arguments=prior_arguments,
    )
    parts = raw.split("+")
    if (len(parts) != 3 or parts[0] not in _SUPERVISOR_PENDING_TASK_CODES
            or parts[1] not in _SUPERVISOR_PENDING_HISTORY_CODES
            or parts[2] not in _SUPERVISOR_PENDING_LOG_CODES):
        deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
    return parts[0], parts[1], parts[2]


def _parse_supervisor_intent(raw, *, require_instance=False):
    if type(raw) is not str or not 0 < len(raw) <= 2048:
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_object_unique)
    except (Denied, ValueError, TypeError, RecursionError):
        return None
    base = {"source_sha", "source_digest", "config_digest", "desktop", "session", "sid", "config", "nonce"}
    if type(value) is not dict:
        return None
    if require_instance:
        base.add("instance")
    if not base <= value.keys() <= base | {"instance", "source_eols"}:
        return None
    if (type(value["source_sha"]) is not str or re.fullmatch(r"[a-f0-9]{40}", value["source_sha"]) is None
            or any(type(value[key]) is not str or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None
                   for key in ("source_digest", "config_digest", "desktop"))
            or type(value["session"]) is not int or value["session"] <= 0
            or type(value["sid"]) is not str or re.fullmatch(r"S-1-(?:\d+-){1,14}\d+", value["sid"]) is None
            or type(value["config"]) is not str or not 0 < len(value["config"]) <= 2048
            or any(ch in value["config"] for ch in "\x00\r\n")
            or type(value["nonce"]) is not str or re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) is None):
        return None
    if "source_eols" in value and _decode_supervisor_eols(value["source_eols"]) is None:
        return None
    if "instance" in value and (type(value["instance"]) is not str
                                or _SUPERVISOR_INSTANCE.fullmatch(value["instance"]) is None):
        return None
    return value


def _classify_pending_intent(raw, *, owner, desktop, sha, source):
    intent = _parse_supervisor_intent(raw, require_instance=False)
    if intent is None:
        return "PEND_MALFORMED", None
    if intent["sid"] != owner[2]:
        return "PEND_OWNER_DRIFT", intent
    prefix = "PEND_WITH_GUID_" if "instance" in intent else "PEND_NO_GUID_"
    if intent["session"] != owner[1] or intent["desktop"] != desktop:
        return prefix + "SESSION_DRIFT", intent
    if intent["source_sha"] == sha and intent["source_digest"] == source:
        return prefix + "SAME_SRC", intent
    return prefix + "PRIOR_SRC", intent


def _probe_supervisor_mutex(runtime):
    try:
        active = runtime._supervisor_active()
    except Exception:
        return "MUTEX_UNKNOWN"
    if type(active) is not bool:
        return "MUTEX_UNKNOWN"
    return "MUTEX_ACTIVE" if active else "MUTEX_INACTIVE"


def _probe_supervisor_health_state(runtime, intent, executable, owner, *, mutex_active):
    try:
        raw = runtime._supervisor_deployment_value("Health")
    except Exception:
        return "HEALTH_UNREADABLE"
    if raw is None:
        return "HEALTH_ABSENT"
    if type(raw) is not str or not 0 < len(raw) <= 2048:
        return "HEALTH_MALFORMED"
    try:
        value = json.loads(raw, object_pairs_hook=_object_unique)
    except (Denied, ValueError, TypeError, RecursionError):
        return "HEALTH_MALFORMED"
    required = {
        "source_sha", "source_digest", "config_digest", "desktop", "session", "nonce",
        "pid", "created", "observed_ns", "health",
    }
    if (type(value) is not dict or not required <= value.keys() <= required | {"mode"}
            or ("mode" in value and value["mode"] not in (runtime._CHROME_HEALTH_ONLY, runtime._OPERATIONAL_TICK_V1))
            or type(value["source_sha"]) is not str or re.fullmatch(r"[a-f0-9]{40}", value["source_sha"]) is None
            or any(type(value[key]) is not str or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None
                   for key in ("source_digest", "config_digest", "desktop"))
            or type(value["session"]) is not int or value["session"] <= 0
            or type(value["nonce"]) is not str or re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) is None
            or type(value["pid"]) is not int or not 0 < value["pid"] < 2**32
            or type(value["created"]) is not int or value["created"] <= 0
            or type(value["observed_ns"]) is not int
            or type(value["health"]) is not str
            or value["health"] not in (
                runtime._OBSERVED_CHROME_HEALTH | _SUPERVISOR_DESKTOP_HOLDS
                | (_SUPERVISOR_CHROME_HOLDS if value.get("mode") == runtime._CHROME_HEALTH_ONLY else frozenset()))):
        return "HEALTH_MALFORMED"
    if intent is None:
        return "HEALTH_UNCORRELATED"
    pin_keys = ("source_sha", "source_digest", "config_digest", "desktop", "session", "nonce")
    if any(value[key] != intent[key] for key in pin_keys):
        return "HEALTH_FOREIGN"
    try:
        created = runtime._supervisor_process(value["pid"], executable, owner)
        process_ok = type(created) is int and created == value["created"]
    except Exception:
        process_ok = False
    if not process_ok:
        return "HEALTH_MATCH_PROCESS_UNVERIFIED"
    try:
        age = runtime.monotonic_ns() - value["observed_ns"]
        fresh = type(age) is int and 0 <= age <= runtime._SUPERVISOR_HEALTH_MAX_AGE_NS
    except Exception:
        fresh = False
    if fresh and mutex_active:
        return "HEALTH_MATCH_LIVE"
    return "HEALTH_MATCH_STALE"


def _probe_supervisor_run_state(runtime, intent):
    try:
        raw = runtime._supervisor_deployment_value("Run")
    except Exception:
        return "RUN_UNREADABLE"
    if raw is None:
        return "RUN_ABSENT"
    parsed = _parse_supervisor_intent(raw, require_instance=True)
    if parsed is None:
        return "RUN_MALFORMED"
    if intent is None:
        return "RUN_UNCORRELATED"
    if (parsed["nonce"] == intent["nonce"]
            and ("instance" not in intent or parsed["instance"] == intent["instance"])
            and parsed.get("source_eols") == intent.get("source_eols")
            and all(parsed[key] == intent[key]
                    for key in ("source_sha", "source_digest", "config_digest", "desktop", "session", "sid", "config"))):
        return "RUN_MATCH"
    return "RUN_PRIOR"


def _probe_supervisor_startup_receipt(runtime, intent, *, pid=None):
    """Read and correlate the bounded private StartupReceipt without leaking raw data.

    Only receipts with verified matching nonce/source/config/desktop/session pins
    correlate to `intent`. Untrusted or pre-nonce receipts stay UNCORRELATED and
    are never assigned as proven correlation to an original attempt.
    """
    try:
        raw = runtime._supervisor_deployment_value("StartupReceipt")
    except Exception:
        return "STARTUP_UNREADABLE"
    if raw is None:
        return "STARTUP_ABSENT"
    if type(raw) is not str or not 0 < len(raw) <= 2048:
        return "STARTUP_MALFORMED"
    try:
        value = json.loads(raw, object_pairs_hook=_object_unique)
    except (Denied, ValueError, TypeError, RecursionError):
        return "STARTUP_MALFORMED"
    pin_keys = ("source_sha", "source_digest", "config_digest", "desktop", "session", "nonce")
    required = {"schema", "stage", "reason", "correlated", "pid", "observed_ns", *pin_keys}
    if type(value) is not dict or value.keys() != required:
        return "STARTUP_MALFORMED"
    stage = value["stage"]
    reason = value["reason"]
    correlated = value["correlated"]
    if (value["schema"] != _SUPERVISOR_STARTUP_RECEIPT_SCHEMA
            or type(stage) is not str or stage not in _SUPERVISOR_STARTUP_STAGE_REASONS
            or type(reason) is not str or reason not in _SUPERVISOR_STARTUP_STAGE_REASONS[stage]
            or type(correlated) is not bool
            or type(value["pid"]) is not int or not 0 < value["pid"] < 2**32
            or type(value["observed_ns"]) is not int):
        return "STARTUP_MALFORMED"
    if not correlated:
        if any(value[k] is not None for k in pin_keys):
            return "STARTUP_MALFORMED"
        return f"STARTUP_UNCORRELATED_{stage}_{reason}"
    if (type(value["source_sha"]) is not str or re.fullmatch(r"[a-f0-9]{40}", value["source_sha"]) is None
            or any(type(value[k]) is not str or re.fullmatch(r"[a-f0-9]{64}", value[k]) is None
                   for k in ("source_digest", "config_digest", "desktop"))
            or type(value["session"]) is not int or value["session"] <= 0
            or type(value["nonce"]) is not str or re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) is None):
        return "STARTUP_MALFORMED"
    if intent is None:
        return "STARTUP_UNCORRELATED"
    if any(value[k] != intent[k] for k in pin_keys) or (pid is not None and value["pid"] != pid):
        return "STARTUP_FOREIGN"
    return f"STARTUP_MATCH_{stage}_{reason}"


def _classify_pending_supervisor_diagnosis(
    task_code, pending_shape, mutex_state, health_state, run_state, history_code, log_code,
):
    if pending_shape == "PEND_MALFORMED":
        return "UNKNOWN_PENDING_MALFORMED"
    if pending_shape == "PEND_OWNER_DRIFT":
        return "UNKNOWN_OWNER_DRIFT"
    if task_code in _SUPERVISOR_TASK_MISMATCH_CODES:
        return "UNKNOWN_TASK_DRIFT"
    if mutex_state == "MUTEX_UNKNOWN" or health_state == "HEALTH_UNREADABLE" or run_state == "RUN_UNREADABLE":
        return "UNKNOWN_EVIDENCE_UNAVAILABLE"
    if health_state == "HEALTH_MATCH_LIVE":
        if task_code in ("TASK_INSTANCE_MATCH", "TASK_INSTANCE_PRESENT"):
            return "ACTIVE_HEALTH_UNRESOLVED"
        return "ACTIVE_UNRESOLVED"
    if task_code in ("TASK_INSTANCE_MATCH", "TASK_INSTANCE_PRESENT"):
        return "STARTUP_HEALTH_MISSING"
    if task_code in ("TASK_INSTANCE_OTHER", "TASK_BUSY", "TASK_PRIOR_BOOTSTRAP_BUSY"):
        return "ACTIVE_UNRESOLVED"
    # Task is TASK_READY or TASK_PRIOR_BOOTSTRAP_READY (idle, zero instances).
    if mutex_state == "MUTEX_ACTIVE" or health_state == "HEALTH_MATCH_STALE" or run_state == "RUN_MATCH":
        return "ACTIVE_UNRESOLVED"
    if health_state in ("HEALTH_MALFORMED", "HEALTH_MATCH_PROCESS_UNVERIFIED") or run_state == "RUN_MALFORMED":
        return "UNKNOWN_EVIDENCE_UNAVAILABLE"
    if pending_shape.startswith("PEND_WITH_GUID_"):
        if log_code == "LOG_ACTION_START_FAILED":
            return "ACTION_START_FAILED"
        if log_code in ("LOG_EXIT_0", "LOG_EXIT_1", "LOG_EXIT_2", "LOG_EXIT_OTHER",
                        "LOG_ERR_SYSTEM", "LOG_TERMINATED", "LOG_COMPLETED"):
            return "TASK_EXITED_NO_HEALTH"
        return "RUNEX_RETURNED_EXIT_UNPROVEN"
    # Valid pending intent with NO recorded instance GUID.
    if history_code == "HIST_NEVER_RAN":
        if log_code == "LOG_EMPTY":
            return "UNKNOWN_NO_GUID_WEAK_EVIDENCE"
        if log_code in ("LOG_DENIED", "LOG_DISABLED", "LOG_UNAVAILABLE"):
            return "UNKNOWN_NO_GUID_LOG_UNAVAILABLE"
        return "UNKNOWN_NO_GUID_UNCORRELATED"
    if history_code == "HIST_UNAVAILABLE":
        return "UNKNOWN_EVIDENCE_UNAVAILABLE"
    return "UNKNOWN_NO_GUID_UNCORRELATED"


def _supervisor_pending_public_code(
    task_code, pending_shape, mutex_state, health_state, run_state, history_code, log_code,
    *, startup_state="STARTUP_ABSENT",
):
    verdict = _classify_pending_supervisor_diagnosis(
        task_code, pending_shape, mutex_state, health_state, run_state, history_code, log_code,
    )
    parts = [
        "PENDING_INTENT_PRESENT", verdict, task_code, pending_shape,
        mutex_state, health_state, run_state, history_code, log_code,
    ]
    if startup_state != "STARTUP_ABSENT":
        parts.append(startup_state)
    return "+".join(parts)


def _is_valid_supervisor_task_probe_code(code):
    if type(code) is not str:
        return False
    if code in _SUPERVISOR_TASK_PROBE_CODES:
        return True
    parts = code.split("+")
    if len(parts) == 6:
        task, stage, run, mutex, health, upgrade = parts
        return (task in _SUPERVISOR_TASK_BASE_CODES and stage in _SUPERVISOR_ACTIVE_PROBE_STAGES
                and run in _SUPERVISOR_PENDING_RUN_CODES and mutex in _SUPERVISOR_PENDING_MUTEX_CODES
                and health in _SUPERVISOR_PENDING_HEALTH_CODES and upgrade in _SUPERVISOR_UPGRADE_PROBE_CODES)
    if len(parts) not in (9, 10):
        return False
    prefix, verdict, task_code, pending_shape, mutex_state, health_state, run_state, history_code, log_code = parts[:9]
    if len(parts) == 10 and (parts[9] == "STARTUP_ABSENT" or parts[9] not in _SUPERVISOR_STARTUP_RECEIPT_CODES):
        return False
    return (
        prefix == "PENDING_INTENT_PRESENT"
        and verdict in _SUPERVISOR_PENDING_VERDICTS
        and task_code in _SUPERVISOR_PENDING_TASK_CODES
        and pending_shape in _SUPERVISOR_PENDING_SHAPE_CODES
        and mutex_state in _SUPERVISOR_PENDING_MUTEX_CODES
        and health_state in _SUPERVISOR_PENDING_HEALTH_CODES
        and run_state in _SUPERVISOR_PENDING_RUN_CODES
        and history_code in _SUPERVISOR_PENDING_HISTORY_CODES
        and log_code in _SUPERVISOR_PENDING_LOG_CODES
    )


def _supervisor_receipt(code, issue_number, sha=None, *, operation=_SUPERVISOR_DEPLOY):
    if operation == _SUPERVISOR_RECOVER_HEALTH_ONLY:
        observed = _SUPERVISOR_RECOVERY_OBSERVED
        allowed = observed | _SUPERVISOR_RECOVERY_HOLDS
    else:
        observed = _SUPERVISOR_OBSERVED
        allowed = observed | _SUPERVISOR_HOLDS | _SUPERVISOR_CHROME_PROBE_HOLDS
    if type(code) is not str or code not in allowed:
        code = "SUPERVISOR_RUNTIME_UNAVAILABLE"
    if type(sha) is not str or re.fullmatch(r"[a-f0-9]{40}", sha) is None:
        code = "PROTECTED_HOSTOPS_ONLY"
        sha = None
    result = {
        "status": "OBSERVED" if code in observed else "HOLD", "code": code,
        "operation": operation, "issue_number": issue_number,
        "approval_channel": "OWNER_GITHUB_COMMENT", "remote_shell_enabled": False,
        "arbitrary_shell_enabled": False, "exact_chat_verified": False, "wake_sent": False,
    }
    if sha is not None:
        result["source_sha"] = sha
    if not result.keys() <= PERSONAL_PUBLIC_FIELDS:
        deny("PUBLIC_TRANSPORT_GAP")
    return result


def _read_supervisor_health(runtime, expected, executable, owner):
    raw = runtime._supervisor_deployment_value("Health")
    if raw is None or not runtime._supervisor_active():
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_object_unique)
        required = expected.keys() | {"pid", "created", "observed_ns", "health"}
        if (type(value) is not dict or not required <= value.keys() <= required | {"mode"}
                or ("mode" in value and value["mode"] not in (runtime._CHROME_HEALTH_ONLY, runtime._OPERATIONAL_TICK_V1))
                or any(value[key] != pin for key, pin in expected.items())
                or type(value["session"]) is not int
                or type(value["pid"]) is not int or not 0 < value["pid"] < 2**32
                or type(value["created"]) is not int or value["created"] <= 0
                or type(value["observed_ns"]) is not int
                or not 0 <= runtime.monotonic_ns() - value["observed_ns"] <= runtime._SUPERVISOR_HEALTH_MAX_AGE_NS
                or value["health"] not in (
                    runtime._OBSERVED_CHROME_HEALTH | _SUPERVISOR_DESKTOP_HOLDS
                    | (_SUPERVISOR_CHROME_HOLDS if value.get("mode") == runtime._CHROME_HEALTH_ONLY else frozenset()))):
            return None
        if (runtime._supervisor_process(value["pid"], executable, owner) == value["created"]
                and runtime._supervisor_active() and runtime._supervisor_deployment_value("Health") == raw):
            return value
    except (Denied, OSError, ValueError, TypeError, RecursionError, runtime.RuntimeHold):
        pass
    return None


def _supervisor_source(runtime, acquisition, sha, *, runtime_commitment=None):
    workflow_runtime = Path(os.environ["GITHUB_WORKSPACE"]) / "src/ai_commerce/wakehub_runtime/runner.py"
    if Path(runtime.__file__).absolute() != workflow_runtime:
        deny("SOURCE_CUSTODY_UNVERIFIED")
    return _persistent_clean_source(acquisition, sha, pin_runtime=True, runtime_commitment=runtime_commitment)


def _supervisor_interpreter(acquisition, root):
    # Use the installed GUI-subsystem interpreter rather than a venv redirector.
    executable = Path(sys._base_executable).with_name("pythonw.exe")
    dependencies = Path(sys.prefix) / "Lib" / "site-packages"
    if (not executable.is_absolute() or not executable.is_file() or not acquisition._no_links(executable)
            or not dependencies.is_absolute() or not dependencies.is_dir() or not acquisition._no_links(dependencies)
            or any(re.search(r'[%\r\n\x00]|\$\(', str(path)) for path in (root, executable, dependencies))):
        deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
    return executable, dependencies


def _supervisor_expected_registration_for(acquisition, root, bootstrap_source):
    executable, dependencies = _supervisor_interpreter(acquisition, root)
    bootstrap = base64.b64encode(bootstrap_source.encode("ascii")).decode("ascii")
    arguments = subprocess.list2cmdline([
        "-I", "-B", "-S", "-c", f"import base64;exec(base64.b64decode('{bootstrap}'))",
        str(root), str(dependencies), "$(Arg0)",
    ])
    return executable, arguments


def _supervisor_expected_registration(acquisition, root):
    return _supervisor_expected_registration_for(acquisition, root, _SUPERVISOR_BOOTSTRAP)


def _supervisor_prior_registration(acquisition, root):
    return _supervisor_expected_registration_for(acquisition, root, _SUPERVISOR_BOOTSTRAP_PRIOR)


def _git_bytes(root, args, *, stdin_bytes=None):
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(root), *args], input=stdin_bytes, capture_output=True,
            timeout=15, check=False, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except subprocess.TimeoutExpired:
        deny("GIT_READ_TIMEOUT")
    except OSError:
        deny("GIT_READ_UNAVAILABLE")
    if result.returncode:
        deny("GIT_READ_FAILED")
    return result.stdout


# Narrowly scoped reviewed compatibility/migration witness for exact immutable
# prior deployed commits whose legacy 9444 health-only mode was independently
# reviewed (#1476/#1479/#1485/#1489 for a74e90f3... and #1492/#1493/#1494 for
# c8d77c15...). Does NOT assert an independently reconstructed runtime source
# digest or completed historical execution; retains prior_source_digest verbatim
# as historical evidence and leaves execution HISTORICAL_UNKNOWN.
_SUPERVISOR_REVIEWED_PRIOR_HEALTH_ONLY_SHAS = frozenset({
    "a74e90f3799938e9c564d6c0fcc5e3bd3583a7b7",
    "c8d77c15cf8e937ea395c677ccd0ac8e20a2a1e5",
})
_SUPERVISOR_PRIOR_SOURCE_PROVENANCES = frozenset({
    "CURRENT_SOURCE_EXACT",
    "GIT_HISTORY_REHASHED",
    "REVIEWED_PRIOR_SHA_COMPATIBILITY",
})
_SUPERVISOR_LEGACY_RETIREMENT = "LEGACY_RETIREMENT_GUARDS_VERIFIED"


def _prior_legacy_retirement_guards(runner_bytes):
    # These are the reviewed protected dispatch and native observation paths.
    # Runtimes with an enrollment mailbox must also share the deploy fence.
    runner = runner_bytes.replace(b"\r\n", b"\n")

    def function(name):
        return runner.partition(b"\ndef " + name + b"(")[2].split(b"\n\n\n", 1)[0]

    launch = function(b"run_supervisor")
    observe = function(b"_observe_only_supervisor")
    if (b"observe_only = not isinstance(chrome, _SupervisorChrome)" not in launch
            or b"if observe_only:\n        return _observe_only_supervisor(" not in launch
            or b"legacy_health_only=True" not in function(b"_observe_only_config")
            or b"_deployment_health(deployment, health_only=True)" not in observe
            or b"state, provenance, pending = sender.lifecycle_snapshot()" not in observe
            or b"result = _registered_result(observe)" not in observe):
        return False
    if b"_receive_enrollment" not in runner:
        return True
    return b"\n        with _supervisor_slot(_SUPERVISOR_DEPLOY_MUTEX):\n" in function(b"_receive_enrollment")


def _verify_prior_supervisor_source(root, prior_sha, prior_digest, current_sha, current_digest, *,
                                    require_rehashed=False, require_tick=False, report_failure=False,
                                    source_eols=None, allow_legacy_retirement=False):
    """Verify Git provenance. Legacy retirement still needs independent live custody."""
    def unverified(code):
        return code if report_failure else None

    require_rehashed = require_rehashed or require_tick or allow_legacy_retirement
    packed = _decode_supervisor_eols(source_eols) if source_eols is not None else None
    if (type(prior_sha) is not str or re.fullmatch(r"[a-f0-9]{40}", prior_sha) is None
            or type(prior_digest) is not str or re.fullmatch(r"[a-f0-9]{64}", prior_digest) is None
            or type(current_sha) is not str or re.fullmatch(r"[a-f0-9]{40}", current_sha) is None
            or type(current_digest) is not str or re.fullmatch(r"[a-f0-9]{64}", current_digest) is None
            or (source_eols is not None and packed is None)):
        return unverified("ACTIVE_SOURCE_PINS_INVALID")
    if prior_sha == current_sha:
        if prior_digest != current_digest:
            return unverified("ACTIVE_SOURCE_CURRENT_DIGEST_MISMATCH")
        return None if require_rehashed else "CURRENT_SOURCE_EXACT"
    if not require_rehashed and packed is None and prior_sha in _SUPERVISOR_REVIEWED_PRIOR_HEALTH_ONLY_SHAS:
        return "REVIEWED_PRIOR_SHA_COMPATIBILITY"
    phase = "PRIOR_OBJECT"
    try:
        if _git_bytes(root, ("rev-parse", "--verify", f"{prior_sha}^{{commit}}")).decode("ascii").strip() != prior_sha:
            return unverified("ACTIVE_SOURCE_PRIOR_OBJECT_INVALID")
        phase = "ANCESTRY"
        _git_bytes(root, ("merge-base", "--is-ancestor", prior_sha, current_sha))
        phase = "TREE"
        raw_tree = _git_bytes(root, ("ls-tree", "-r", "-z", prior_sha, "--", "src/ai_commerce"))
        entries = [item for item in raw_tree.split(b"\0") if item]
        names = []
        blob_shas = []
        for entry in entries:
            meta, rel = entry.split(b"\t", 1)
            rel_str = rel.decode("utf-8")
            if not rel_str.startswith("src/ai_commerce/") or not rel_str.endswith(".py"):
                continue
            parts = meta.split()
            if (len(parts) != 3 or parts[1] != b"blob" or re.fullmatch(rb"[a-f0-9]{40}", parts[2]) is None
                    or (require_rehashed and parts[0] not in (b"100644", b"100755"))):
                return unverified("ACTIVE_SOURCE_TREE_INVALID")
            names.append(rel_str[len("src/"):])
            blob_shas.append(parts[2].decode("ascii"))
        if not names or len(names) > 4096 or len(set(names)) != len(names):
            return unverified("ACTIVE_SOURCE_TREE_INVALID")
        eol_bits = 0
        if packed is not None:
            eol_bits = int.from_bytes(packed, "little")
            if len(packed) != (len(names) + 3) // 4 or eol_bits >> (2 * len(names)):
                return unverified("ACTIVE_SOURCE_PINS_INVALID")
        eol_indexes = {name: index for index, name in enumerate(sorted(names))}
        phase = "BATCH"
        batch = _git_bytes(root, ("cat-file", "--batch"), stdin_bytes=("\n".join(blob_shas) + "\n").encode("ascii"))
        pos = 0
        raw_manifest = []
        crlf_manifest = []
        runner_bytes = None
        for name, oid in zip(names, blob_shas):
            nl = batch.find(b"\n", pos)
            if nl < 0:
                return unverified("ACTIVE_SOURCE_BATCH_TRUNCATED")
            header = batch[pos:nl].decode("ascii").split()
            if len(header) == 2 and header[1] == "missing":
                return unverified("ACTIVE_SOURCE_BATCH_OBJECT_MISSING")
            if len(header) != 3 or header[:2] != [oid, "blob"]:
                return unverified("ACTIVE_SOURCE_BATCH_INVALID")
            size = int(header[2])
            if size < 0:
                return unverified("ACTIVE_SOURCE_BATCH_INVALID")
            pos = nl + 1
            if pos + size >= len(batch):
                return unverified("ACTIVE_SOURCE_BATCH_TRUNCATED")
            blob = batch[pos:pos + size]
            if len(blob) != size or batch[pos + size:pos + size + 1] != b"\n":
                return unverified("ACTIVE_SOURCE_BATCH_INVALID")
            pos += size + 1
            if name == "ai_commerce/wakehub_runtime/runner.py":
                runner_bytes = blob
            if packed is None:
                crlf_blob = blob.replace(b"\n", b"\r\n") if b"\r" not in blob else blob
                crlf_manifest.append([name, hashlib.sha256(crlf_blob).hexdigest()])
            else:
                tag = (eol_bits >> (2 * eol_indexes[name])) & 3
                if tag:
                    normalized = _normalize_crlf_text(blob)
                    if tag == 3 or normalized is None or b"\n" not in normalized:
                        return unverified("ACTIVE_SOURCE_PINS_INVALID")
                    blob = normalized if tag == 1 else normalized.replace(b"\n", b"\r\n")
            raw_manifest.append([name, hashlib.sha256(blob).hexdigest()])
        if pos != len(batch):
            return unverified("ACTIVE_SOURCE_BATCH_INVALID")
        if runner_bytes is None:
            return unverified("ACTIVE_SOURCE_RUNNER_MISSING")
        raw_digest = hashlib.sha256(json.dumps(sorted(raw_manifest), separators=(",", ":")).encode("ascii")).hexdigest()
        crlf_digest = hashlib.sha256(json.dumps(sorted(crlf_manifest), separators=(",", ":")).encode("ascii")).hexdigest()
        rehashed = prior_digest == raw_digest or (packed is None and prior_digest == crlf_digest)
        retire_legacy = allow_legacy_retirement and not require_tick and packed is None
        if not rehashed and not retire_legacy:
            return unverified("ACTIVE_SOURCE_DIGEST_MISMATCH")
        # Confirm the prior commit itself enforced legacy health-only mode for legacy 9444 configs.
        if (b"legacy_health_only=True" not in runner_bytes
                or b"observe_only = not isinstance(chrome, _SupervisorChrome)" not in runner_bytes
                or (require_rehashed and b"_deployment_health(deployment, health_only=True)" not in runner_bytes)
                or (not rehashed and not _prior_legacy_retirement_guards(runner_bytes))):
            return unverified("ACTIVE_SOURCE_LEGACY_GUARD_MISSING")
        if require_tick and (b'_OPERATIONAL_TICK_V1 = "OPERATIONAL_TICK_V1"' not in runner_bytes
                             or b"_protected_operational_tick(run," not in runner_bytes
                             or b"stack.enter_context(_supervisor_slot(_SUPERVISOR_TICK_MUTEX))" not in runner_bytes):
            return unverified("ACTIVE_SOURCE_TICK_FENCE_MISSING")
        return "GIT_HISTORY_REHASHED" if rehashed else _SUPERVISOR_LEGACY_RETIREMENT
    except Denied as exc:
        outcome = {"GIT_READ_UNAVAILABLE": "UNAVAILABLE", "GIT_READ_TIMEOUT": "TIMEOUT"}.get(str(exc), "FAILED")
        return unverified(f"ACTIVE_SOURCE_{phase}_{outcome}")
    except (OSError, ValueError):
        return unverified(f"ACTIVE_SOURCE_{phase}_INVALID")


def _supervisor_upgrade_source(runtime, root, prior, sha, source, mode, *, allow_legacy=True, report_failure=False):
    options = {"require_rehashed": True}
    if mode == runtime._OPERATIONAL_TICK_V1:
        options["require_tick"] = True
    elif mode == runtime._CHROME_HEALTH_ONLY and allow_legacy:
        options["allow_legacy_retirement"] = True
    if "source_eols" in prior:
        options["source_eols"] = prior["source_eols"]
    if report_failure:
        options["report_failure"] = True
    return _verify_prior_supervisor_source(root, prior["source_sha"], prior["source_digest"], sha, source, **options)


def _legacy_retirement_startup_matches(runtime, prior, pid):
    # Receipt writes were best effort. A present receipt must corroborate this
    # launch, with its initial health allowed to differ from the current one.
    startup = _probe_supervisor_startup_receipt(runtime, prior, pid=pid)
    prefix = "STARTUP_MATCH_FIRST_HEALTH_"
    return startup == "STARTUP_ABSENT" or (
        startup.startswith(prefix) and startup.removeprefix(prefix) in (
            {"HEALTH_PUBLISHED"} | _SUPERVISOR_DESKTOP_HOLDS | _SUPERVISOR_CHROME_HOLDS
            | runtime._LEGACY_BROWSER_OBSERVATION_HOLDS))


def _probe_protected_supervisor_read(event, operation, inspect):
    request = parse_event(event, "read")
    if request["operation"] != operation:
        deny("READ_OPERATION_DENIED")
    result = {
        "status": "HOLD", "code": "PROTECTED_HOSTOPS_ONLY", "operation": operation,
        "issue_number": request["issue_number"], "approval_channel": "CHATGPT_TO_AI_COMMERCE_ISSUE",
        "remote_shell_enabled": False,
    }
    if not (result.keys() | {"source_sha"}) <= PERSONAL_PUBLIC_FIELDS:
        deny("PUBLIC_TRANSPORT_GAP")

    bytecode = sys.dont_write_bytecode
    try:
        _require_protected_clean_runner("issues")
        result["source_sha"] = _protected_clean_workflow_sha(allow_blank_workflow_sha=True)
        result["code"] = "SUPERVISOR_RUNTIME_UNAVAILABLE"
        sys.dont_write_bytecode = True
        from ai_commerce.remote import chrome_acquisition as acquisition
        from ai_commerce.wakehub_runtime import runner as runtime

        root = ROOTS["workspace"]
        sha = result["source_sha"]

        def read_owner():
            result["code"] = "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
            try:
                return runtime._supervisor_owner_session()
            except runtime.RuntimeHold as exc:
                code = str(exc)
                if code in _SUPERVISOR_DESKTOP_HOLDS:
                    result["code"] = code
                raise

        result["code"] = "SOURCE_CUSTODY_UNVERIFIED"
        source = _supervisor_source(runtime, acquisition, sha)
        owner, desktop = read_owner()
        result["code"] = "SUPERVISOR_RUNTIME_UNAVAILABLE"
        executable, arguments = _supervisor_expected_registration(acquisition, root)

        def recheck_identity():
            result["code"] = "SOURCE_CUSTODY_UNVERIFIED"
            if _supervisor_source(runtime, acquisition, sha) != source:
                deny(result["code"])
            if read_owner() != (owner, desktop):
                deny(result["code"])

        inspect(result, runtime, acquisition, root, owner, executable, arguments, recheck_identity, (sha, source, desktop))
    except Exception:
        # The active check's fixed code is the entire public diagnostic.
        pass
    finally:
        sys.dont_write_bytecode = bytecode
    return result


def _probe_protected_supervisor_config(event):
    def inspect(result, runtime, _acquisition, root, owner, _executable, _arguments, recheck_identity, _identity):
        def resolve_config():
            result["code"] = "SUPERVISOR_RUNTIME_UNAVAILABLE"
            config, code = runtime._probe_supervisor_config(root, owner)
            allowed = runtime._CONFIG_PROBE_FAILURES if config is None else runtime._CONFIG_PROBE_VERIFIED
            if type(code) is not str or code not in allowed:
                deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
            if config is None:
                result["code"] = code
                deny(code)
            return config, code

        config, code = resolve_config()
        recheck_identity()
        current, _ = resolve_config()
        if current != config:
            result["code"] = "LOCAL_CONFIG_CHANGED"
        else:
            result.update(status="OBSERVED", code=code)

    return _probe_protected_supervisor_read(event, _SUPERVISOR_CONFIG_PROBE, inspect)


def _probe_supervisor_active_run(runtime, acquisition, root, owner, executable, arguments, identity, task_code):
    """Report the first failed read-side check and the current record correlations."""
    sha, source, desktop = identity
    unreadable = object()

    def read(name):
        try:
            return runtime._supervisor_deployment_value(name)
        except Exception:
            return unreadable

    raw_run, raw_upgrade = read("Run"), read("UpgradePending")
    run = _parse_supervisor_intent(raw_run, require_instance=True)
    owner_drift = None
    if raw_run is unreadable:
        run_state = "RUN_UNREADABLE"
    elif raw_run is None:
        run_state = "RUN_ABSENT"
    elif run is None:
        run_state = "RUN_MALFORMED"
    else:
        run_state = "RUN_UNCORRELATED"
        if run["sid"] != owner[2]:
            owner_drift = "ACTIVE_RUN_OWNER_DRIFT"
        elif (run["session"], run["desktop"]) != (owner[1], desktop):
            owner_drift = "ACTIVE_RUN_SESSION_DRIFT"

    if raw_upgrade is unreadable:
        upgrade_state = "UPGRADE_UNREADABLE"
    elif raw_upgrade is None:
        upgrade_state = "UPGRADE_ABSENT"
    else:
        try:
            upgrade = _parse_supervisor_upgrade(runtime, raw_upgrade)
            upgrade_state = "UPGRADE_MATCH_RUN" if _parse_supervisor_intent(
                upgrade["run"], require_instance=True,
            ) == run else "UPGRADE_OTHER_RUN"
        except (Denied, ValueError, TypeError, RecursionError):
            upgrade_state = "UPGRADE_MALFORMED"

    mutex_state = _probe_supervisor_mutex(runtime)
    health_state = _probe_supervisor_health_state(
        runtime, run if owner_drift is None else None, executable, owner,
        mutex_active=(mutex_state == "MUTEX_ACTIVE"),
    )

    def first_stage():
        nonlocal run_state
        if upgrade_state in {"UPGRADE_UNREADABLE", "UPGRADE_MALFORMED"}:
            return upgrade_state
        if task_code != "TASK_INSTANCE_PRESENT":
            if task_code == "TASK_READY" and raw_upgrade is not None:
                return upgrade_state
            return "ACTIVE_TASK_UNVERIFIED"
        if run is None:
            return run_state
        try:
            instance = _supervisor_task(acquisition, executable, arguments, root, owner)
        except subprocess.TimeoutExpired:
            return "ACTIVE_TASK_PROBE_TIMEOUT"
        except Exception:
            return "ACTIVE_TASK_UNVERIFIED"
        if not _SUPERVISOR_INSTANCE.fullmatch(instance):
            return "ACTIVE_READ_STATE_CHANGED"
        if instance != run["instance"]:
            return "ACTIVE_TASK_INSTANCE_MISMATCH"
        run_state = "RUN_MATCH"
        if owner_drift is not None:
            return owner_drift
        try:
            config, config_pin = runtime._resolve_runtime_config(root, owner=owner, observe_only=True, protected=True)
        except Exception:
            return "ACTIVE_CONFIG_UNVERIFIED"
        if run["config"] != str(config):
            return "ACTIVE_CONFIG_PATH_MISMATCH"
        if run["config_digest"] != config_pin:
            return "ACTIVE_CONFIG_DIGEST_MISMATCH"
        if raw_upgrade is not None:
            return upgrade_state
        if health_state != "HEALTH_MATCH_LIVE":
            return health_state
        pins = {key: run[key] for key in (*runtime._ENROLLMENT_PINS, "config_digest")}
        try:
            health = _read_supervisor_health(runtime, pins, executable, owner)
        except Exception:
            return "HEALTH_UNREADABLE"
        if health is None:
            return "ACTIVE_HEALTH_CHANGED"
        legacy_hold = _supervisor_upgrade_from_legacy_hold(runtime, health, run["source_sha"], sha)
        if health["health"] not in runtime._OBSERVED_CHROME_HEALTH and not legacy_hold:
            return health["health"]
        if run["source_sha"] == sha:
            if run["source_digest"] != source:
                return "ACTIVE_SOURCE_CURRENT_DIGEST_MISMATCH"
            return "ACTIVE_READ_CHECKS_COMPLETE"
        mode = health.get("mode")
        if mode not in (runtime._CHROME_HEALTH_ONLY, runtime._OPERATIONAL_TICK_V1):
            return "ACTIVE_HEALTH_MODE_UNVERIFIED"
        prior_source = _supervisor_upgrade_source(runtime, root, run, sha, source, mode, report_failure=True)
        legacy_retirement = mode == runtime._CHROME_HEALTH_ONLY and prior_source == _SUPERVISOR_LEGACY_RETIREMENT
        if prior_source != "GIT_HISTORY_REHASHED" and not legacy_retirement:
            return prior_source if prior_source in _SUPERVISOR_SOURCE_PROBE_STAGES else "ACTIVE_RUN_SOURCE_UNVERIFIED"
        if legacy_retirement and not _legacy_retirement_startup_matches(runtime, run, health["pid"]):
            return "ACTIVE_STARTUP_UNVERIFIED"
        try:
            if _supervisor_upgrade_enrollment_pending(runtime, run, health["pid"]):
                return "ACTIVE_ENROLLMENT_UNVERIFIED"
        except Exception:
            return "ACTIVE_ENROLLMENT_UNVERIFIED"
        try:
            options = {"expected_health": "CHROME_OWNED"} if legacy_hold or legacy_retirement else {}
            _supervisor_upgrade_chrome_pin(runtime, acquisition, root, owner, config, config_pin, mode, **options)
        except Exception as exc:
            if mode == runtime._CHROME_HEALTH_ONLY:
                return _supervisor_upgrade_chrome_hold(runtime, acquisition, exc)
            return "ACTIVE_CHROME_UNVERIFIED"
        try:
            if not _supervisor_upgrade_task_process(acquisition, executable, arguments, root, owner, run, health["pid"]):
                return "ACTIVE_TASK_PROCESS_UNVERIFIED"
        except subprocess.TimeoutExpired:
            return "ACTIVE_TASK_PROCESS_TIMEOUT"
        except Exception:
            return "ACTIVE_TASK_PROCESS_UNVERIFIED"
        try:
            latest = _read_supervisor_health(runtime, pins, executable, owner)
        except Exception:
            return "HEALTH_UNREADABLE"
        if not _supervisor_upgrade_health_matches(health, latest, legacy_hold=legacy_hold):
            return "ACTIVE_HEALTH_CHANGED"
        return "ACTIVE_LEGACY_RETIREMENT_ELIGIBLE" if legacy_retirement else "ACTIVE_READ_CHECKS_COMPLETE"

    stage = first_stage()
    if (read("Run"), read("UpgradePending")) != (raw_run, raw_upgrade):
        stage = "ACTIVE_READ_STATE_CHANGED"
    elif (task_code != "TASK_INSTANCE_PRESENT" and raw_run is None and raw_upgrade is None
          and health_state == "HEALTH_ABSENT" and mutex_state == "MUTEX_INACTIVE"):
        # Keep the scalar result when no exact instance or deployment evidence is present.
        return task_code
    return "+".join((task_code, stage, run_state, mutex_state, health_state, upgrade_state))


def _probe_protected_supervisor_task(event):
    def inspect(result, runtime, acquisition, root, owner, executable, arguments, recheck_identity, identity):
        sha, source, desktop = identity

        def read_pending():
            try:
                value = runtime._supervisor_deployment_value("StartPending")
            except Exception:
                return ("UNKNOWN", None)
            if value is None:
                return ("ABSENT", None)
            return ("PRESENT", value) if type(value) is str else ("UNKNOWN", None)

        result["code"] = "SUPERVISOR_RUNTIME_UNAVAILABLE"
        _, prior_arguments = _supervisor_prior_registration(acquisition, root)
        pending_before = read_pending()
        if pending_before[0] != "PRESENT":
            try:
                task_code = _supervisor_task_probe(
                    acquisition, executable, arguments, root, owner, prior_arguments=prior_arguments,
                )
            except subprocess.TimeoutExpired:
                result["code"] = "TASK_PROBE_TIMEOUT"
                return
            code = task_code
            if pending_before[0] == "ABSENT":
                code = _probe_supervisor_active_run(runtime, acquisition, root, owner, executable, arguments,
                                                    identity, task_code)
            recheck_identity()
            result["code"] = "SUPERVISOR_RUNTIME_UNAVAILABLE"
            if (_supervisor_expected_registration(acquisition, root) != (executable, arguments)
                    or _supervisor_prior_registration(acquisition, root) != (executable, prior_arguments)):
                deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
            pending_after = read_pending()
            if pending_after != pending_before:
                result["code"] = "PENDING_INTENT_CHANGED"
                return
            if pending_before[0] == "ABSENT":
                status = "OBSERVED" if task_code in _SUPERVISOR_TASK_PROBE_OBSERVED else "HOLD"
            else:
                code = "PENDING_INTENT_UNKNOWN_" + task_code
                status = "HOLD"
            if not _is_valid_supervisor_task_probe_code(code):
                deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
            result.update(status=status, code=code)
            return

        pending_shape, intent = _classify_pending_intent(
            pending_before[1], owner=owner, desktop=desktop, sha=sha, source=source,
        )
        correlatable = intent if pending_shape not in ("PEND_MALFORMED", "PEND_OWNER_DRIFT") else None
        pending_guid = (
            correlatable["instance"]
            if correlatable is not None and pending_shape.startswith("PEND_WITH_GUID_")
            else ""
        )
        task_code, history_code, log_code = _supervisor_task_probe_pending(
            acquisition, executable, arguments, root, owner,
            pending_guid=pending_guid, prior_arguments=prior_arguments,
        )
        if correlatable is None:
            log_code = "LOG_NA"
        mutex_state = _probe_supervisor_mutex(runtime)
        health_state = _probe_supervisor_health_state(
            runtime, correlatable, executable, owner, mutex_active=(mutex_state == "MUTEX_ACTIVE"),
        )
        run_state = _probe_supervisor_run_state(runtime, correlatable)
        startup_state = _probe_supervisor_startup_receipt(runtime, correlatable)
        recheck_identity()
        result["code"] = "SUPERVISOR_RUNTIME_UNAVAILABLE"
        if (_supervisor_expected_registration(acquisition, root) != (executable, arguments)
                or _supervisor_prior_registration(acquisition, root) != (executable, prior_arguments)):
            deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
        pending_after = read_pending()
        if pending_after != pending_before:
            result["code"] = "PENDING_INTENT_CHANGED"
            return
        code = _supervisor_pending_public_code(
            task_code, pending_shape, mutex_state, health_state, run_state, history_code, log_code,
            startup_state=startup_state,
        )
        if not _is_valid_supervisor_task_probe_code(code):
            deny("SUPERVISOR_RUNTIME_UNAVAILABLE")
        result.update(status="HOLD", code=code)

    return _probe_protected_supervisor_read(event, _SUPERVISOR_TASK_PROBE, inspect)


def _supervisor_upgrade_from_legacy_hold(runtime, health, prior_sha, current_sha):
    return (prior_sha != current_sha and health.get("mode") == runtime._CHROME_HEALTH_ONLY
            and health["health"] in _SUPERVISOR_CHROME_HOLDS)


def _supervisor_upgrade_health_matches(health, latest, *, legacy_hold):
    if latest is None or any(latest.get(key) != health.get(key) for key in ("pid", "created", "mode")):
        return False
    # With independent owned Chrome proof, the legacy HOLD only corroborates process liveness.
    return latest["health"] == health["health"] or (legacy_hold and latest["health"] in _SUPERVISOR_CHROME_HOLDS)


def _supervisor_upgrade_chrome_hold(runtime, acquisition, error, *, fallback="CHROME_OWNER_UNPROVEN"):
    if isinstance(error, (Denied, runtime.RuntimeHold, acquisition.AcquisitionBlocked,
                          acquisition._EnrollmentHold, acquisition._Lifecycle9444Hold)) and len(error.args) == 1:
        code = error.args[0]
        if type(code) is str and code in _SUPERVISOR_CHROME_PROBE_HOLDS:
            return code
    return fallback


def _supervisor_upgrade_chrome_pin(runtime, acquisition, root, owner, config, config_pin, mode, *, expected_health=None):
    def read_config():
        try:
            return runtime._observe_only_config(config, root, owner, lambda: False, protected=True)
        except Exception as exc:
            deny(_supervisor_upgrade_chrome_hold(runtime, acquisition, exc, fallback="ACTIVE_CONFIG_UNVERIFIED"))

    chrome, pin = read_config()
    if pin != config_pin:
        deny("LOCAL_CONFIG_CHANGED")
    if mode == runtime._OPERATIONAL_TICK_V1 and isinstance(chrome, runtime._SupervisorChrome):
        with acquisition._prefer_installed_powershell():
            state, provenance, pending = chrome.sender.lifecycle_snapshot(hostops=True)
        if pending or provenance[:3] != owner or state not in ("OWNED", "ABSENT"):
            deny("SUPERVISOR_ACTIVE_UNVERIFIED")
        journal = chrome.runtime.journal
        before = journal.path.stat()
        origin = journal.enrolled_origin(chrome.runtime.registration, owner[2], for_upgrade=True)
        after = journal.path.stat()
        identity = (after.st_dev, after.st_ino)
        if origin is None or not after.st_ino or identity != (before.st_dev, before.st_ino):
            deny("SUPERVISOR_ACTIVE_UNVERIFIED")
        listeners, processes = provenance[4]
        browser_identity = [[list(row) for row in listeners], [list(row) for row in processes]]
        return runtime.digest([state, browser_identity, provenance[5], origin, list(identity)])
    if (mode != runtime._CHROME_HEALTH_ONLY or not isinstance(chrome, runtime.NativeChrome9444)
            or not chrome.legacy_health_only):
        deny("CHROME_ENROLLMENT_DRIFT")
    state, provenance, pending = chrome.lifecycle_snapshot(hostops=True)
    if provenance[:3] != owner:
        deny("WINDOWS_OWNER_CONTEXT_UNVERIFIED")
    if pending or state not in ("OWNED", "ABSENT"):
        deny("CHROME_OWNER_UNPROVEN")
    if expected_health is not None and expected_health != f"CHROME_{state}":
        deny("ACTIVE_CHROME_OWNED_REQUIRED" if expected_health == "CHROME_OWNED" else "CHROME_OBSERVATION_CHANGED")
    _, latest_pin = read_config()
    if latest_pin != pin:
        deny("LOCAL_CONFIG_CHANGED")
    identity = provenance[4]
    # Preserve the persisted R2 digest so interrupted upgrades retain their browser pin.
    return hashlib.sha256(json.dumps((state, identity), separators=(",", ":")).encode("utf-8")).hexdigest()


def _parse_supervisor_upgrade(runtime, raw):
    value = json.loads(raw, object_pairs_hook=_object_unique)
    base = {"issue", "comment", "start_comment", "run", "pid", "created", "target", "chrome"}
    if (type(value) is not dict
            or not base <= value.keys() <= base | {"target_sha", "mode", "nonce", "prior_source_provenance"}
            or any(type(value[key]) is not int or value[key] <= 0
                   for key in ("issue", "comment", "start_comment", "pid", "created"))
            or any(value[key] > 2**53 - 1 for key in ("issue", "comment", "start_comment"))
            or value["start_comment"] < value["comment"]
            or value["pid"] >= 2**32
            or any(type(value[key]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value[key])
                   for key in ("target", "chrome"))
            or ("target_sha" in value and (type(value["target_sha"]) is not str
                                           or not re.fullmatch(r"[a-f0-9]{40}", value["target_sha"])))
            or ("mode" in value and value["mode"] not in (runtime._CHROME_HEALTH_ONLY, runtime._OPERATIONAL_TICK_V1))
            or ("nonce" in value and (type(value["nonce"]) is not str
                                      or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"])))
            or ("prior_source_provenance" in value and (
                value["prior_source_provenance"] != _SUPERVISOR_LEGACY_RETIREMENT
                or value.get("mode") != runtime._CHROME_HEALTH_ONLY or "target_sha" not in value))):
        deny("SUPERVISOR_ACTIVE_UNVERIFIED")
    prior = _parse_supervisor_intent(value["run"], require_instance=True)
    if prior is None or ("prior_source_provenance" in value and "source_eols" in prior):
        deny("SUPERVISOR_ACTIVE_UNVERIFIED")
    return value


def _supervisor_upgrade_enrollment_pending(runtime, prior, pid):
    raw = runtime._supervisor_deployment_value(runtime._ENROLLMENT_INTENT)
    receipt_raw = runtime._supervisor_deployment_value(runtime._ENROLLMENT_RECEIPT)
    if raw is None:
        return receipt_raw is not None
    intent = runtime._parse_enrollment_intent(raw, prior)
    if intent is None:
        # A terminal receipt from a retired nonce remains available to its original caller.
        previous = json.loads(raw, object_pairs_hook=_object_unique)
        intent = runtime._parse_enrollment_intent(raw, previous)
        row = json.loads(receipt_raw, object_pairs_hook=_object_unique) if receipt_raw else {}
        pid = row.get("pid")
        if type(pid) is not int or not 0 < pid < 2**32:
            return True
    receipt = runtime._enrollment_receipt(receipt_raw, intent, pid)
    return receipt is None or receipt["code"] == runtime._ENROLLMENT_UNKNOWN


def _confirmed_supervisor_health(runtime, expected, executable, owner, task, recheck, intent, instance):
    pins = {**expected, "nonce": intent["nonce"]}
    proof = _read_supervisor_health(runtime, pins, executable, owner)
    if proof is None or task() != instance:
        return None
    if proof["health"] not in _SUPERVISOR_DESKTOP_HOLDS:
        recheck()
    latest = _read_supervisor_health(runtime, pins, executable, owner)
    if (latest is not None and all(latest.get(key) == proof.get(key) for key in ("pid", "created", "health", "mode"))
            and task() == instance):
        return latest
    return None


def _supervisor_start_pins(runtime, pins, commitment):
    start_pins = {**pins, **commitment}
    if len(json.dumps({**start_pins, "nonce": "0" * 32, "instance": "0" * 36})) > runtime._SUPERVISOR_INTENT_LIMIT:
        deny("SOURCE_CUSTODY_UNVERIFIED")
    return start_pins


def _complete_supervisor_start(runtime, intent, instance, pending, *, upgrade=None):
    read, store = runtime._supervisor_deployment_value, runtime._store_supervisor_deployment_value
    if read("StartPending") != pending or read("UpgradePending") != upgrade:
        return False
    run = {**intent, "instance": instance}
    raw_run = read("Run")
    run_matches = _parse_supervisor_intent(raw_run, require_instance=True) == run
    if upgrade is not None and not run_matches and (raw_run is not None or pending is None):
        return False
    if pending is not None:
        if not run_matches:
            store("Run", json.dumps(run))
        if _parse_supervisor_intent(read("Run"), require_instance=True) != run:
            return False
        store("StartPending", None)
    # Run is durable and carries the same nonce. Keep the upgrade pins until
    # StartPending is retired so either interruption can be revalidated.
    if upgrade is not None:
        store("UpgradePending", None)
    return True


def _deploy_protected_supervisor(event):
    request = parse_event(event, "write")
    if request["operation"] != _SUPERVISOR_DEPLOY:
        deny("WRITE_SCOPE_DENIED")
    issue_number = request["issue_number"]
    sha = None
    failure = "PROTECTED_HOSTOPS_ONLY"
    _supervisor_receipt(failure, issue_number)  # Check projection before native access.
    try:
        _require_protected_clean_runner("issue_comment", reconcile=True)
        sha = _protected_clean_workflow_sha(write=True)
        failure = "SUPERVISOR_RUNTIME_UNAVAILABLE"
        from ai_commerce.remote import chrome_acquisition as acquisition
        from ai_commerce.wakehub_runtime import runner as runtime

        approval = event["comment"].get("id")
        approval_known = runtime._github_id(approval) and approval <= 2**53 - 1
        upgrade_key = "UpgradePending"
        upgrade = None
        prior = None
        failure = "SOURCE_CUSTODY_UNVERIFIED"
        commitment = {}
        source = _supervisor_source(runtime, acquisition, sha, runtime_commitment=commitment)
        rerun = os.environ["GITHUB_RUN_ATTEMPT"] != "1"
        if (rerun and runtime._supervisor_deployment_value("StartPending") is None
                and runtime._supervisor_deployment_value("Run") is None
                and runtime._supervisor_deployment_value(upgrade_key) is None):
            return _supervisor_receipt("PROTECTED_HOSTOPS_ONLY", issue_number)
        root = ROOTS["workspace"]

        def read_owner():
            nonlocal failure
            try:
                return runtime._supervisor_owner_session()
            except runtime.RuntimeHold as exc:
                code = str(exc)
                if code in _SUPERVISOR_DESKTOP_HOLDS:
                    failure = code
                raise

        failure = "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
        owner, desktop = read_owner()
        failure = "SUPERVISOR_RUNTIME_UNAVAILABLE"
        executable, arguments = _supervisor_expected_registration(acquisition, root)

        def resolve_config():
            return runtime._resolve_runtime_config(root, owner=owner, observe_only=True, protected=True)

        def recheck(*, check_source=False):
            nonlocal failure
            if check_source:
                failure = "SOURCE_CUSTODY_UNVERIFIED"
                if _persistent_clean_source(acquisition, sha, pin_runtime=True) != source:
                    deny(failure)
            failure = "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
            if read_owner() != (owner, desktop):
                deny(failure)
            failure = "LOCAL_CONFIG_CHANGED"
            if resolve_config() != (config, config_pin):
                deny(failure)

        def task(**kwargs):
            return _supervisor_task(acquisition, executable, arguments, root, owner, **kwargs)

        def chrome_pin(mode, **options):
            return _supervisor_upgrade_chrome_pin(runtime, acquisition, root, owner, config, config_pin, mode, **options)

        def remember_upgrade():
            nonlocal raw_upgrade
            raw_upgrade = json.dumps(upgrade, separators=(",", ":"))
            if len(raw_upgrade) > 4096:
                deny(failure)
            runtime._store_supervisor_deployment_value(upgrade_key, raw_upgrade)
            if runtime._supervisor_deployment_value(upgrade_key) != raw_upgrade:
                deny(failure)

        def enrollment_pending(prior, pid):
            return _supervisor_upgrade_enrollment_pending(runtime, prior, pid)

        def reserve_stop(raw_run):
            nonlocal failure, upgrade
            failure = "SUPERVISOR_ACTIVE_UNVERIFIED"
            if rerun or not approval_known or prior["source_sha"] == sha or not prior_owner_matches():
                deny(failure)
            pins = {key: prior[key] for key in (*runtime._ENROLLMENT_PINS, "config_digest")}
            health = _read_supervisor_health(runtime, pins, executable, owner)
            if health is None:
                deny(failure)
            legacy_hold = _supervisor_upgrade_from_legacy_hold(runtime, health, prior["source_sha"], sha)
            if health["health"] not in runtime._OBSERVED_CHROME_HEALTH and not legacy_hold:
                deny(failure)
            mode = health.get("mode")
            if mode not in (runtime._CHROME_HEALTH_ONLY, runtime._OPERATIONAL_TICK_V1):
                # Older operational runtimes have no shared tick fence.
                deny(failure)
            operational = mode == runtime._OPERATIONAL_TICK_V1
            provenance = _supervisor_upgrade_source(runtime, root, prior, sha, source, mode)
            legacy_retirement = not operational and provenance == _SUPERVISOR_LEGACY_RETIREMENT
            if provenance != "GIT_HISTORY_REHASHED" and not legacy_retirement:
                deny(failure)
            pid, created = health["pid"], health["created"]
            if legacy_retirement and not _legacy_retirement_startup_matches(runtime, prior, pid):
                deny(failure)

            def observe_browser():
                nonlocal failure
                if not legacy_hold and not legacy_retirement:
                    return chrome_pin(mode)
                try:
                    return chrome_pin(mode, expected_health="CHROME_OWNED")
                except Exception as exc:
                    failure = _supervisor_upgrade_chrome_hold(runtime, acquisition, exc)
                    raise

            def checkpoint():
                nonlocal failure, upgrade
                recheck(check_source=True)
                failure = "SUPERVISOR_ACTIVE_UNVERIFIED"
                if (runtime._supervisor_deployment_value("Run") != raw_run
                        or runtime._supervisor_deployment_value("StartPending") is not None
                        or runtime._supervisor_deployment_value(upgrade_key) is not None
                        or enrollment_pending(prior, pid)
                        or (legacy_retirement and not _legacy_retirement_startup_matches(runtime, prior, pid))):
                    deny(failure)
                if observe_browser() != browser:
                    if legacy_hold or legacy_retirement:
                        failure = "CHROME_OBSERVATION_CHANGED"
                    deny(failure)
                if not _supervisor_upgrade_task_process(acquisition, executable, arguments, root, owner, prior, pid):
                    deny(failure)
                latest = _read_supervisor_health(runtime, pins, executable, owner)
                if not _supervisor_upgrade_health_matches(health, latest, legacy_hold=legacy_hold):
                    deny(failure)
                upgrade = {"issue": issue_number, "comment": approval, "start_comment": approval,
                           "run": raw_run, "pid": pid, "created": created, "mode": mode,
                           "target": target, "target_sha": sha, "chrome": browser}
                if legacy_retirement:
                    upgrade["prior_source_provenance"] = provenance
                remember_upgrade()

            with ExitStack() as fence:
                # Legacy health-only enrollment already holds the deploy mutex.
                # Operational ticks try both slots without waiting for HostOps.
                if operational:
                    deadline = time.monotonic() + 3
                    while True:
                        try:
                            fence.enter_context(runtime._supervisor_slot(runtime._SUPERVISOR_TICK_MUTEX))
                            break
                        except runtime.RuntimeHold as exc:
                            if str(exc) != "SUPERVISOR_ACTIVE" or time.monotonic() >= deadline:
                                raise
                            time.sleep(0.25)
                if enrollment_pending(prior, pid):
                    deny(failure)
                browser = observe_browser()
                try:
                    runtime._stop_verified_supervisor(pid, created, executable, owner, checkpoint)
                except (OSError, runtime.RuntimeHold):
                    if upgrade is None or runtime._supervisor_deployment_value(upgrade_key) != raw_upgrade:
                        raise
                # A lost stop reply has the same readback as a successful one.
                deadline = time.monotonic() + 10
                while not upgrade_idle():
                    if time.monotonic() >= deadline:
                        deny(failure)
                    time.sleep(0.25)

        def upgrade_idle():
            return (task() == "READY" and not runtime._supervisor_active()
                    and runtime._supervisor_process_exited(upgrade["pid"], upgrade["created"]))

        def prior_owner_matches():
            return all(prior[key] == intent_pins[key] for key in ("sid", "desktop", "session", "config", "config_digest"))

        def upgrade_mode():
            return upgrade.get("mode", runtime._CHROME_HEALTH_ONLY)

        def retire_stopped_run():
            nonlocal failure
            recheck(check_source=True)
            failure = "SUPERVISOR_ACTIVE_UNVERIFIED"
            if (not prior_owner_matches() or runtime._supervisor_deployment_value(upgrade_key) != raw_upgrade
                    or runtime._supervisor_deployment_value("StartPending") is not None
                    or not upgrade_idle() or enrollment_pending(prior, upgrade["pid"])):
                deny(failure)
            legacy_retirement = upgrade.get("prior_source_provenance") == _SUPERVISOR_LEGACY_RETIREMENT
            retarget = upgrade["target"] != target or upgrade.get("target_sha", sha) != sha
            if retarget:
                # A committed start remains bound to its original target. With
                # no launch reservation, only a proven descendant may take over.
                previous_target = upgrade.get("target_sha")
                if previous_target is None or previous_target == sha:
                    deny(failure)
                _git_bytes(root, ("merge-base", "--is-ancestor", previous_target, sha))
            if retarget or (legacy_retirement and not reserved_here):
                provenance = _supervisor_upgrade_source(
                    runtime, root, prior, sha, source, upgrade_mode(), allow_legacy=legacy_retirement,
                )
                if (not upgrade_idle() or (provenance != "GIT_HISTORY_REHASHED"
                        and not (legacy_retirement and provenance == _SUPERVISOR_LEGACY_RETIREMENT))):
                    deny(failure)
            options = {"expected_health": "CHROME_OWNED"} if legacy_retirement else {}
            if chrome_pin(upgrade_mode(), **options) != upgrade["chrome"]:
                deny(failure)
            run = runtime._supervisor_deployment_value("Run")
            stale_health = runtime._supervisor_deployment_value("Health")
            if run not in (None, upgrade["run"]):
                deny(failure)
            if stale_health is not None:
                row = json.loads(stale_health, object_pairs_hook=_object_unique)
                pins = {key: prior[key] for key in (*runtime._ENROLLMENT_PINS, "config_digest")}
                if (type(row) is not dict or any(row.get(key) != value for key, value in pins.items())
                        or row.get("mode") != upgrade_mode()
                        or (row.get("pid"), row.get("created")) != (upgrade["pid"], upgrade["created"])):
                    deny(failure)
                runtime._store_supervisor_deployment_value("Health", None)
            if run is not None:
                # The original Run is durably retained in UpgradePending until readback.
                runtime._store_supervisor_deployment_value("Run", None)
            upgrade.update(start_comment=approval, target=target, target_sha=sha)

        def recheck_completion():
            nonlocal failure
            prior_failure = failure
            recheck(check_source=upgrade is not None)
            failure = prior_failure

        def finish(intent, instance, pending):
            if runtime._supervisor_deployment_value("StartPending") != pending:
                return "SUPERVISOR_START_UNKNOWN"
            if upgrade is not None and (
                upgrade["target"] != target or upgrade.get("target_sha") != sha
                or upgrade.get("nonce") != intent["nonce"] or intent["nonce"] == prior["nonce"]
                or instance == prior["instance"] or not prior_owner_matches()
                or runtime._supervisor_deployment_value(upgrade_key) != raw_upgrade
                or not runtime._supervisor_process_exited(upgrade["pid"], upgrade["created"])
                or enrollment_pending(prior, upgrade["pid"])
            ):
                return "SUPERVISOR_START_UNKNOWN"
            proven = _confirmed_supervisor_health(
                runtime, expected, executable, owner, task, recheck_completion, intent, instance,
            )
            if proven is None:
                return None
            if proven["health"] in _SUPERVISOR_DESKTOP_HOLDS:
                return proven["health"]
            legacy_upgrade = upgrade is not None and upgrade_mode() == runtime._CHROME_HEALTH_ONLY
            options = {"expected_health": proven["health"]} if legacy_upgrade else {}
            if upgrade is not None and (
                proven.get("mode") != upgrade_mode() or proven["health"] not in runtime._OBSERVED_CHROME_HEALTH
                or (proven["pid"], proven["created"]) == (upgrade["pid"], upgrade["created"])
                or chrome_pin(upgrade_mode(), **options) != upgrade["chrome"]
                or not _supervisor_upgrade_task_process(acquisition, executable, arguments, root, owner,
                                                        {**intent, "instance": instance}, proven["pid"])
                or not 0 <= runtime.monotonic_ns() - proven["observed_ns"] <= runtime._SUPERVISOR_HEALTH_MAX_AGE_NS
            ):
                return "SUPERVISOR_START_UNKNOWN"
            if legacy_upgrade:
                latest = _read_supervisor_health(runtime, {**expected, "nonce": intent["nonce"]}, executable, owner)
                if latest is None or any(latest.get(key) != proven.get(key) for key in ("pid", "created", "mode", "health")):
                    return "SUPERVISOR_START_UNKNOWN"
            if pending is not None or upgrade is not None:
                if not _complete_supervisor_start(runtime, intent, instance, pending, upgrade=raw_upgrade):
                    return "SUPERVISOR_START_UNKNOWN"
            if proven["health"] in _SUPERVISOR_CHROME_HOLDS:
                return proven["health"]
            if proven.get("mode") == runtime._CHROME_HEALTH_ONLY:
                return "SUPERVISOR_CHROME_HEALTH_ONLY_OBSERVED"
            if pending is not None:
                return "SUPERVISOR_STARTED_HEALTH_OBSERVED"
            return "SUPERVISOR_REUSED_HEALTH_OBSERVED"

        with runtime._supervisor_slot(runtime._SUPERVISOR_DEPLOY_MUTEX):
            pending = runtime._supervisor_deployment_value("StartPending")
            try:
                config, config_pin = resolve_config()
            except (runtime.RuntimeHold, Denied) as exc:
                failure = "SUPERVISOR_START_UNKNOWN" if pending is not None else str(exc)
                raise
            expected = {"source_sha": sha, "source_digest": source, "config_digest": config_pin,
                        "desktop": desktop, "session": owner[1]}
            intent_pins = {**expected, "sid": owner[2], "config": str(config)}
            failure = "SOURCE_CUSTODY_UNVERIFIED"
            start_pins = _supervisor_start_pins(runtime, intent_pins, commitment)
            failure = "SUPERVISOR_RUNTIME_UNAVAILABLE"
            target = hashlib.sha256(json.dumps(intent_pins, sort_keys=True).encode("utf-8")).hexdigest()
            raw_upgrade = runtime._supervisor_deployment_value(upgrade_key)
            if pending is not None:
                failure = "SUPERVISOR_START_UNKNOWN"
            elif raw_upgrade is not None:
                failure = "SUPERVISOR_ACTIVE_UNVERIFIED"
            if raw_upgrade is not None:
                upgrade = _parse_supervisor_upgrade(runtime, raw_upgrade)
                prior = _parse_supervisor_intent(upgrade["run"], require_instance=True)
            reserved_here = False
            state = task()
            if pending is not None or _SUPERVISOR_INSTANCE.fullmatch(state):
                failure = "SUPERVISOR_START_UNKNOWN" if pending is not None else "SUPERVISOR_ACTIVE_UNVERIFIED"
                record = pending if pending is not None else runtime._supervisor_deployment_value("Run")
                if record is None or not _SUPERVISOR_INSTANCE.fullmatch(state):
                    deny(failure)
                intent = _parse_supervisor_intent(record, require_instance=pending is None)
                if intent is None or intent.get("instance", state) != state:
                    deny(failure)
                if all(intent[key] == pin for key, pin in intent_pins.items()):
                    return _supervisor_receipt(finish(intent, state, pending) or failure, issue_number, sha)
                if pending is not None or upgrade is not None:
                    deny(failure)
                prior = intent
                reserve_stop(record)
                reserved_here = True
                state = "READY"  # reserve_stop independently observed the idle task.
            if state == "CONFLICT":
                return _supervisor_receipt("SUPERVISOR_TASK_CONFLICT", issue_number, sha)
            if state == "BUSY" or runtime._supervisor_active():
                return _supervisor_receipt("SUPERVISOR_ACTIVE_UNVERIFIED", issue_number, sha)
            if upgrade is not None:
                failure = "SUPERVISOR_ACTIVE_UNVERIFIED"
                if state != "READY":
                    deny(failure)
                if not reserved_here and (rerun or not approval_known
                                          or approval <= max(upgrade["comment"], upgrade["start_comment"])):
                    code = "SUPERVISOR_START_NOT_ATTEMPTED" if upgrade_idle() else failure
                    return _supervisor_receipt(code, issue_number, sha)
                retire_stopped_run()
            elif rerun:
                # Only a fresh dispatch can reserve a start or install the task.
                if runtime._supervisor_deployment_value("Run") is None:
                    return _supervisor_receipt("PROTECTED_HOSTOPS_ONLY", issue_number)
                return _supervisor_receipt("SUPERVISOR_START_NOT_ATTEMPTED", issue_number, sha)
            else:
                recheck()
                failure = "SUPERVISOR_RUNTIME_UNAVAILABLE"
                if state == "MISSING":
                    state = task(mode="install")
                if state == "CONFLICT":
                    return _supervisor_receipt("SUPERVISOR_TASK_CONFLICT", issue_number, sha)
                if state != "READY" or runtime._supervisor_active():
                    return _supervisor_receipt("SUPERVISOR_ACTIVE_UNVERIFIED", issue_number, sha)
                recheck(check_source=True)

            failure = "SUPERVISOR_START_UNKNOWN"
            nonce = secrets.token_hex(16)
            if upgrade is not None:
                while nonce in (prior["nonce"], upgrade.get("nonce")):
                    nonce = secrets.token_hex(16)
                upgrade["nonce"] = nonce
                remember_upgrade()
            intent = {**start_pins, "nonce": nonce}
            pending = json.dumps(intent)
            if runtime._supervisor_deployment_value("StartupReceipt") is not None:
                runtime._store_supervisor_deployment_value("StartupReceipt", None)
            runtime._store_supervisor_deployment_value("StartPending", pending)
            if runtime._supervisor_deployment_value("StartPending") != pending:
                deny(failure)
            instance = task(mode="start", nonce=intent["nonce"])
            if instance in {"NOT_ATTEMPTED", "MISSING", "CONFLICT"}:
                runtime._store_supervisor_deployment_value("StartPending", None)
                code = "SUPERVISOR_TASK_CONFLICT" if instance == "CONFLICT" else "SUPERVISOR_START_NOT_ATTEMPTED"
                return _supervisor_receipt(code, issue_number, sha)
            if not _SUPERVISOR_INSTANCE.fullmatch(instance):
                deny(failure)
            intent["instance"] = instance
            pending = json.dumps(intent)
            runtime._store_supervisor_deployment_value("StartPending", pending)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                code = finish(intent, instance, pending)
                if code is not None:
                    return _supervisor_receipt(code, issue_number, sha)
                time.sleep(0.25)
    except Exception:
        # Exception text can include private config, paths and native diagnostics.
        pass
    return _supervisor_receipt(failure, issue_number, sha)


def _check_wakehub_enrollment_public_receipt(result, *, operation, number, sha, comment_id, runtime):
    code = result.get("code")
    prepared = operation == _SUPERVISOR_PREPARE_CHAT and code == runtime._ENROLLMENT_PREPARED
    verified = (operation == _SUPERVISOR_ENROLL_CHAT and type(code) is str
                and code in runtime._ENROLLMENT_VERIFIED)
    holds = ((_WAKEHUB_ENROLLMENT_HOST_HOLDS | runtime._ENROLLMENT_OUTCOMES)
             - {runtime._ENROLLMENT_PREPARED} - runtime._ENROLLMENT_VERIFIED)
    fields = _WAKEHUB_ENROLLMENT_PUBLIC_FIELDS | ({"selection_id"} if prepared else set())
    if (operation not in _SUPERVISOR_CHAT_WRITES or result.keys() != fields
            or not (prepared or verified or type(code) is str and code in holds)
            or result["status"] != ("OBSERVED" if prepared or verified else "HOLD")
            or result["operation"] != operation
            or type(result["issue_number"]) is not int or result["issue_number"] != number
            or not 0 < number <= 2**53 - 1
            or type(result["approval_comment_id"]) is not int or result["approval_comment_id"] != comment_id
            or not 0 < comment_id <= 2**53 - 1
            or type(sha) is not str or re.fullmatch(r"[a-f0-9]{40}", sha) is None or result["source_sha"] != sha
            or result["approval_channel"] != "OWNER_GITHUB_COMMENT"
            or result["exact_chat_verified"] is not verified
            or any(result[key] is not False for key in ("remote_shell_enabled", "arbitrary_shell_enabled", "wake_sent"))
            or prepared and not runtime._chrome_digest(result["selection_id"])):
        deny("PUBLIC_TRANSPORT_GAP")


def _enroll_protected_supervisor_chat(event):
    request = parse_event(event, "write")
    operation = request["operation"]
    if operation not in _SUPERVISOR_CHAT_WRITES:
        deny("WRITE_SCOPE_DENIED")
    number = request["issue_number"]
    result = {"status": "HOLD", "code": "PROTECTED_HOSTOPS_ONLY", "operation": operation,
              "issue_number": number, "approval_channel": "OWNER_GITHUB_COMMENT", "remote_shell_enabled": False,
              "arbitrary_shell_enabled": False, "exact_chat_verified": False, "wake_sent": False}
    try:
        _require_protected_clean_runner("issue_comment", reconcile=True)
        sha = _protected_clean_workflow_sha()
        # The workflow must opt this exact WRITE into its bound receipt sanitizer.
        if os.environ.get("HOSTOPS_WAKEHUB_ENROLLMENT_DISPATCH") != "WakeHubChatEnrollment.v1":
            return result
        from ai_commerce.remote import chrome_acquisition as acquisition
        from ai_commerce.wakehub_runtime import runner as runtime
    except Exception:
        return result

    failure = "WAKEHUB_ENROLLMENT_APPROVAL_UNVERIFIED"
    comment = event["comment"]
    comment_id = comment.get("id")
    if not runtime._github_id(comment_id) or comment_id > 2**53 - 1:
        return dict(result, code=failure)
    result.update(source_sha=sha, approval_comment_id=comment_id)
    outcome = None
    try:
        if comment.get("created_at") != comment.get("updated_at"):
            deny(failure)
        issued = int(runtime._timestamp(comment.get("created_at"))) * 1000
        approval = {"schema": runtime._ENROLLMENT_SCHEMA, "issue_number": number, "comment_id": comment_id,
                    "selection_id": request["parameters"].get("selection_id"), "issued_ms": issued,
                    "expires_ms": issued + runtime._ENROLLMENT_LEASE_MS}
        if not runtime._enrollment_intent_fresh(approval):
            failure = "WAKEHUB_ENROLLMENT_APPROVAL_EXPIRED"
            deny(failure)
        failure = "SOURCE_CUSTODY_UNVERIFIED"
        source = _supervisor_source(runtime, acquisition, sha)
        root = ROOTS["workspace"]
        failure = "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
        owner, desktop = runtime._supervisor_owner_session()
        failure = "SUPERVISOR_ACTIVE_UNVERIFIED"
        executable, arguments = _supervisor_expected_registration(acquisition, root)

        def current_instance():
            return _supervisor_task(acquisition, executable, arguments, root, owner)

        with runtime._supervisor_slot(runtime._SUPERVISOR_DEPLOY_MUTEX):
            run = _parse_supervisor_intent(runtime._supervisor_deployment_value("Run"), require_instance=True)
            if (run is None or runtime._supervisor_deployment_value("StartPending") is not None
                    or runtime._supervisor_deployment_value("UpgradePending") is not None
                    or (run["source_sha"], run["source_digest"], run["desktop"], run["session"], run["sid"])
                    != (sha, source, desktop, owner[1], owner[2]) or current_instance() != run["instance"]):
                deny(failure)
            pins = {key: run[key] for key in (*runtime._ENROLLMENT_PINS, "config_digest")}
            health = _read_supervisor_health(runtime, pins, executable, owner)
            if health is None or runtime._supervisor_owner_session() != (owner, desktop):
                deny(failure)
            intent = {**approval, **{key: run[key] for key in runtime._ENROLLMENT_PINS}}
            current = runtime._parse_enrollment_intent(runtime._supervisor_deployment_value(runtime._ENROLLMENT_INTENT), run)
            previous = runtime._enrollment_receipt(runtime._supervisor_deployment_value(runtime._ENROLLMENT_RECEIPT),
                                                   current, health["pid"]) if current is not None else None
            if current != intent:
                previous_code = previous["code"] if previous else None
                if (current is not None and ((runtime._enrollment_intent_fresh(current)
                        and previous_code in (None, runtime._ENROLLMENT_UNKNOWN))
                        or (previous_code == runtime._ENROLLMENT_UNKNOWN
                            and current["selection_id"] != intent["selection_id"]))):
                    failure = "WAKEHUB_ENROLLMENT_BUSY"
                    deny(failure)
                if os.environ["GITHUB_RUN_ATTEMPT"] != "1":
                    failure = "PROTECTED_HOSTOPS_ONLY"
                    deny(failure)
                failure = runtime._ENROLLMENT_UNKNOWN
                runtime._store_supervisor_deployment_value(runtime._ENROLLMENT_INTENT, json.dumps(intent))
        failure = runtime._ENROLLMENT_UNKNOWN
        deadline = time.monotonic() + 75
        while True:
            receipt = runtime._enrollment_receipt(runtime._supervisor_deployment_value(runtime._ENROLLMENT_RECEIPT),
                                                 intent, health["pid"])
            if receipt is not None and receipt["code"] != runtime._ENROLLMENT_UNKNOWN:
                current_health = _read_supervisor_health(runtime, pins, executable, owner)
                if (current_health is None or current_health["pid"] != health["pid"]
                        or current_health["created"] != health["created"] or current_instance() != run["instance"]
                        or runtime._supervisor_owner_session() != (owner, desktop)):
                    deny(failure)
                code = receipt["code"]
                verified = code in runtime._ENROLLMENT_VERIFIED
                if code == runtime._ENROLLMENT_PREPARED:
                    outcome = dict(result, status="OBSERVED", code=code, selection_id=receipt["selection_id"])
                else:
                    outcome = dict(result, status="OBSERVED" if verified else "HOLD", code=code,
                                   exact_chat_verified=verified)
                break
            if time.monotonic() >= deadline or not runtime._enrollment_intent_fresh(intent):
                break
            time.sleep(0.25)
    except Exception:
        # All public reasons come from fixed stages or the closed native receipt.
        pass
    public = outcome if outcome is not None else dict(result, code=failure)
    _check_wakehub_enrollment_public_receipt(public, operation=operation, number=number, sha=sha,
                                            comment_id=comment_id, runtime=runtime)
    return public


def _recover_protected_supervisor_health_only(event):
    """Explicitly retire one unresolved prior legacy health-only pending intent and start one fresh attempt.

    Requires its own owner-authenticated WRITE grant (attempt 1 only), verified
    prior pending identity with GUID, proven legacy health-only mode (exact
    current/rehashed prior source or reviewed compatibility witness for exact
    immutable prior commit a74e90f3... plus exact config path/digest match),
    repeated idle/zero-instance task verification, inactive supervisor mutex,
    absent Run/Health, and durable archival of the verbatim original pending
    bytes as HISTORICAL_UNKNOWN with honest source provenance before any mutation.
    """
    request = parse_event(event, "write")
    if request["operation"] != _SUPERVISOR_RECOVER_HEALTH_ONLY:
        deny("WRITE_SCOPE_DENIED")
    issue_number = request["issue_number"]
    sha = None
    failure = "PROTECTED_HOSTOPS_ONLY"

    def receipt(code, source_sha=None):
        return _supervisor_receipt(
            code, issue_number, sha if source_sha is None else source_sha,
            operation=_SUPERVISOR_RECOVER_HEALTH_ONLY,
        )

    receipt(failure, None)
    try:
        _require_protected_clean_runner("issue_comment", reconcile=False)
        sha = _protected_clean_workflow_sha(write=True)
        failure = "SUPERVISOR_RUNTIME_UNAVAILABLE"
        from ai_commerce.remote import chrome_acquisition as acquisition
        from ai_commerce.wakehub_runtime import runner as runtime

        failure = "SOURCE_CUSTODY_UNVERIFIED"
        commitment = {}
        source = _supervisor_source(runtime, acquisition, sha, runtime_commitment=commitment)
        root = ROOTS["workspace"]

        def read_owner():
            nonlocal failure
            try:
                return runtime._supervisor_owner_session()
            except runtime.RuntimeHold as exc:
                code = str(exc)
                if code in _SUPERVISOR_DESKTOP_HOLDS:
                    failure = code
                raise

        failure = "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
        owner, desktop = read_owner()
        failure = "SUPERVISOR_RUNTIME_UNAVAILABLE"
        executable, arguments = _supervisor_expected_registration(acquisition, root)
        prior_executable, prior_arguments = _supervisor_prior_registration(acquisition, root)
        if prior_executable != executable:
            deny("SUPERVISOR_RUNTIME_UNAVAILABLE")

        def resolve_health_only_config():
            cfg, pin = runtime._resolve_runtime_config(root, owner=owner, observe_only=True, protected=True)
            chrome, observed_pin = runtime._observe_only_config(cfg, root, owner, lambda: False, protected=True)
            if (not isinstance(chrome, runtime.NativeChrome9444)
                    or not getattr(chrome, "legacy_health_only", False)
                    or observed_pin != pin):
                deny("RECOVERY_HEALTH_ONLY_REQUIRED")
            return cfg, pin

        def recheck(*, check_source=False):
            nonlocal failure
            if check_source:
                failure = "SOURCE_CUSTODY_UNVERIFIED"
                if _persistent_clean_source(acquisition, sha, pin_runtime=True) != source:
                    deny(failure)
            failure = "WINDOWS_OWNER_CONTEXT_UNVERIFIED"
            if read_owner() != (owner, desktop):
                deny(failure)
            failure = "LOCAL_CONFIG_CHANGED"
            if resolve_health_only_config() != (config, config_pin):
                deny(failure)

        def task(**kwargs):
            return _supervisor_task(acquisition, executable, arguments, root, owner, **kwargs)

        def recheck_completion():
            nonlocal failure
            prior_failure = failure
            recheck()
            failure = prior_failure

        def finish(intent, instance, pending):
            if runtime._supervisor_deployment_value("StartPending") != pending:
                return "SUPERVISOR_START_UNKNOWN"
            proven = _confirmed_supervisor_health(
                runtime, expected, executable, owner, task, recheck_completion, intent, instance,
            )
            if proven is None or proven.get("mode") != runtime._CHROME_HEALTH_ONLY:
                return None
            if proven["health"] in _SUPERVISOR_DESKTOP_HOLDS:
                return proven["health"]
            if not _complete_supervisor_start(runtime, intent, instance, pending):
                return "SUPERVISOR_START_UNKNOWN"
            if proven["health"] in _SUPERVISOR_CHROME_HOLDS:
                return proven["health"]
            return "SUPERVISOR_CHROME_HEALTH_ONLY_OBSERVED"

        with runtime._supervisor_slot(runtime._SUPERVISOR_DEPLOY_MUTEX):
            failure = "RECOVERY_EVIDENCE_UNAVAILABLE"
            raw_pending = runtime._supervisor_deployment_value("StartPending")
            if runtime._supervisor_deployment_value("UpgradePending") is not None:
                # The deploy operation owns this transaction, even when the
                # legacy pending bytes have the same config or source pins.
                return receipt("SUPERVISOR_START_UNKNOWN" if raw_pending is not None
                               else "SUPERVISOR_ACTIVE_UNVERIFIED")
            if raw_pending is None:
                return receipt("RECOVERY_PENDING_ABSENT")
            prior_intent = _parse_supervisor_intent(raw_pending, require_instance=True)
            if prior_intent is None:
                if _parse_supervisor_intent(raw_pending, require_instance=False) is not None:
                    return receipt("RECOVERY_PENDING_GUID_REQUIRED")
                return receipt("RECOVERY_PENDING_MALFORMED")
            if prior_intent["sid"] != owner[2]:
                return receipt("RECOVERY_PENDING_OWNER_MISMATCH")

            try:
                config, config_pin = resolve_health_only_config()
            except (runtime.RuntimeHold, Denied) as exc:
                failure = str(exc)
                raise

            if prior_intent["config"] != str(config) or prior_intent["config_digest"] != config_pin:
                return receipt("RECOVERY_PRIOR_HEALTH_ONLY_UNPROVEN")
            history_options = ({"source_eols": prior_intent["source_eols"]} if "source_eols" in prior_intent else {})
            prior_source_provenance = _verify_prior_supervisor_source(
                root, prior_intent["source_sha"], prior_intent["source_digest"], sha, source, **history_options,
            )
            if prior_source_provenance not in _SUPERVISOR_PRIOR_SOURCE_PROVENANCES:
                return receipt("RECOVERY_PRIOR_SOURCE_UNPROVEN")
            prior_digest_rehashed = prior_source_provenance != "REVIEWED_PRIOR_SHA_COMPATIBILITY"
            expected = {"source_sha": sha, "source_digest": source, "config_digest": config_pin,
                        "desktop": desktop, "session": owner[1]}
            failure = "SOURCE_CUSTODY_UNVERIFIED"
            intent_pins = _supervisor_start_pins(runtime, {**expected, "sid": owner[2], "config": str(config)}, commitment)
            failure = "RECOVERY_EVIDENCE_UNAVAILABLE"

            def check_quiescent():
                mutex_state = _probe_supervisor_mutex(runtime)
                if mutex_state == "MUTEX_ACTIVE":
                    return "SUPERVISOR_ACTIVE_UNVERIFIED"
                if mutex_state != "MUTEX_INACTIVE":
                    return "RECOVERY_EVIDENCE_UNAVAILABLE"
                if _probe_supervisor_run_state(runtime, prior_intent) != "RUN_ABSENT":
                    return "RECOVERY_CONFLICTING_RUN"
                if _probe_supervisor_health_state(
                    runtime, prior_intent, executable, owner, mutex_active=False,
                ) != "HEALTH_ABSENT":
                    return "RECOVERY_CONFLICTING_HEALTH"
                startup_state = _probe_supervisor_startup_receipt(runtime, prior_intent)
                if startup_state == "STARTUP_UNREADABLE":
                    return "RECOVERY_EVIDENCE_UNAVAILABLE"
                if startup_state == "STARTUP_MATCH_FIRST_HEALTH_HEALTH_PUBLISHED":
                    return "RECOVERY_CONFLICTING_HEALTH"
                return None

            quiescent_hold = check_quiescent()
            if quiescent_hold is not None:
                return receipt(quiescent_hold)

            failure = "SUPERVISOR_RUNTIME_UNAVAILABLE"
            state_1 = task(mode="inspect_recovery", prior_arguments=prior_arguments)
            if state_1 == "BUSY":
                return receipt("SUPERVISOR_ACTIVE_UNVERIFIED")
            if state_1 not in ("READY", "PRIOR_READY"):
                return receipt("SUPERVISOR_TASK_CONFLICT")

            # Independent second READY/zero-instance + identity + quiescence observation.
            recheck(check_source=True)
            failure = "RECOVERY_STATE_CHANGED"
            if runtime._supervisor_deployment_value("StartPending") != raw_pending:
                return receipt("RECOVERY_STATE_CHANGED")
            quiescent_hold = check_quiescent()
            if quiescent_hold is not None:
                return receipt(quiescent_hold)
            failure = "SUPERVISOR_RUNTIME_UNAVAILABLE"
            state_2 = task(mode="inspect_recovery", prior_arguments=prior_arguments)
            if state_2 == "BUSY":
                return receipt("SUPERVISOR_ACTIVE_UNVERIFIED")
            if state_2 != state_1:
                return receipt("RECOVERY_STATE_CHANGED")

            # Durably archive original pending bytes as HISTORICAL_UNKNOWN before ANY mutation.
            pending_sha = hashlib.sha256(raw_pending.encode("utf-8")).hexdigest()
            archive_key = f"RetiredPending_{prior_intent['nonce']}"
            archive_payload = {
                "schema": "WakeHubSupervisorRetiredPending.v1",
                "classification": "HISTORICAL_UNKNOWN",
                "retired_by_issue": issue_number,
                "retired_source_sha": sha,
                "prior_nonce": prior_intent["nonce"],
                "prior_instance": prior_intent["instance"],
                "prior_source_sha": prior_intent["source_sha"],
                "prior_source_digest": prior_intent["source_digest"],
                "prior_source_provenance": prior_source_provenance,
                "prior_source_digest_rehashed": prior_digest_rehashed,
                "prior_config_digest": prior_intent["config_digest"],
                "prior_mode": "LEGACY_CHROME_HEALTH_ONLY",
                "pending_sha256": pending_sha,
                "pending_raw": raw_pending,
            }
            archive_raw = json.dumps(archive_payload, separators=(",", ":"))
            if len(archive_raw) > 4096:
                return receipt("RECOVERY_ARCHIVE_FAILED")

            def verify_archive(stored):
                if type(stored) is not str:
                    return False
                try:
                    parsed = json.loads(stored, object_pairs_hook=_object_unique)
                except (Denied, ValueError, TypeError, RecursionError):
                    return False
                return (
                    type(parsed) is dict
                    and parsed.get("schema") == "WakeHubSupervisorRetiredPending.v1"
                    and parsed.get("classification") == "HISTORICAL_UNKNOWN"
                    and parsed.get("prior_nonce") == prior_intent["nonce"]
                    and parsed.get("prior_instance") == prior_intent["instance"]
                    and parsed.get("prior_source_sha") == prior_intent["source_sha"]
                    and parsed.get("prior_source_digest") == prior_intent["source_digest"]
                    and parsed.get("prior_source_provenance") == prior_source_provenance
                    and parsed.get("prior_source_digest_rehashed") is prior_digest_rehashed
                    and parsed.get("pending_sha256") == pending_sha
                    and parsed.get("pending_raw") == raw_pending
                )

            failure = "RECOVERY_ARCHIVE_FAILED"
            existing_archive = runtime._supervisor_deployment_value(archive_key)
            if existing_archive is not None:
                if not verify_archive(existing_archive):
                    return receipt("RECOVERY_ARCHIVE_CONFLICT")
            else:
                runtime._store_supervisor_deployment_value(archive_key, archive_raw)
            if not verify_archive(runtime._supervisor_deployment_value(archive_key)):
                return receipt("RECOVERY_ARCHIVE_FAILED")

            # Explicitly migrate ONLY the exact known prior registered task in place while idle.
            if state_1 == "PRIOR_READY":
                failure = "RECOVERY_TASK_MIGRATION_FAILED"
                migrated = task(mode="migrate", prior_arguments=prior_arguments)
                if migrated == "CONFLICT":
                    return receipt("SUPERVISOR_TASK_CONFLICT")
                if migrated == "BUSY":
                    return receipt("SUPERVISOR_ACTIVE_UNVERIFIED")
                if migrated != "MIGRATED":
                    return receipt("RECOVERY_TASK_MIGRATION_FAILED")

            # Final pre-start verification before replacing StartPending and invoking RunEx once.
            recheck(check_source=True)
            failure = "RECOVERY_STATE_CHANGED"
            if (runtime._supervisor_deployment_value("StartPending") != raw_pending
                    or not verify_archive(runtime._supervisor_deployment_value(archive_key))):
                return receipt("RECOVERY_STATE_CHANGED")
            quiescent_hold = check_quiescent()
            if quiescent_hold is not None:
                return receipt(quiescent_hold)

            failure = "SUPERVISOR_START_UNKNOWN"
            new_nonce = secrets.token_hex(16)
            while new_nonce == prior_intent["nonce"]:
                new_nonce = secrets.token_hex(16)
            new_intent = {**intent_pins, "nonce": new_nonce}
            new_pending = json.dumps(new_intent)
            if runtime._supervisor_deployment_value("StartupReceipt") is not None:
                runtime._store_supervisor_deployment_value("StartupReceipt", None)
            runtime._store_supervisor_deployment_value("StartPending", new_pending)
            instance = task(mode="start", nonce=new_nonce)
            if instance in {"NOT_ATTEMPTED", "MISSING", "CONFLICT"}:
                if runtime._supervisor_deployment_value("StartPending") == new_pending:
                    runtime._store_supervisor_deployment_value("StartPending", None)
                return receipt("SUPERVISOR_TASK_CONFLICT" if instance == "CONFLICT" else "SUPERVISOR_START_NOT_ATTEMPTED")
            if not _SUPERVISOR_INSTANCE.fullmatch(instance) or instance == prior_intent["instance"]:
                deny(failure)
            new_intent["instance"] = instance
            new_pending = json.dumps(new_intent)
            runtime._store_supervisor_deployment_value("StartPending", new_pending)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                code = finish(new_intent, instance, new_pending)
                if code is not None:
                    return receipt(code)
                startup = _probe_supervisor_startup_receipt(runtime, new_intent)
                if (startup.startswith("STARTUP_MATCH_")
                        and startup != "STARTUP_MATCH_FIRST_HEALTH_HEALTH_PUBLISHED"
                        and task() == "READY"
                        and not runtime._supervisor_active()):
                    return receipt("SUPERVISOR_START_UNKNOWN")
                time.sleep(0.25)
    except Exception:
        # Exception text can include private config, paths and native diagnostics.
        pass
    return receipt(failure)


def _install_protected_clean_autostart(event):
    # Check the public contract before any native call, even on a HOLD.
    _clean_autostart_receipt("PROTECTED_HOSTOPS_ONLY")
    reserved = False
    try:
        _require_protected_clean_runner("issue_comment")
        sha = _protected_clean_workflow_sha(write=True)
        if parse_event(event, "write")["operation"] != _CLEAN_AUTOSTART:
            deny("PROTECTED_HOSTOPS_ONLY")
        from ai_commerce.remote import chrome_acquisition as acquisition
        owner = acquisition._native_operator()
        _, session, sid = owner
        pending = acquisition._CLEAN_TASK_PENDING
        _persistent_clean_source(acquisition, sha)
        target, candidates = acquisition._clean_target(owner)

        def inspect_target(*, enrolling=False):
            if acquisition._native_operator() != owner:
                deny("WINDOWS_OWNER_CONTEXT_UNVERIFIED")
            if acquisition._clean_target(owner) != (target, candidates):
                deny("CHROME_PATH_INVALID")
            state, identity = acquisition._clean_inventory(target, candidates, session, sid)
            if state == "STARTING":
                deny("PROFILE_ALREADY_ACTIVE_NO_CDP")
            if enrolling and state != "ABSENT":
                deny("PROFILE_IN_USE")
            if acquisition._clean_launch_pending():
                deny("UNKNOWN_EFFECT")
            if state == "ABSENT":
                acquisition._clean_require_unlocked(target)
            acquisition._verify_google_chrome(target if identity is None else
                                              replace(target, executable=identity[1]))

        if acquisition._clean_pending(pending):
            deny("UNKNOWN_EFFECT")
        inspect_target()
        task = acquisition._clean_task(owner, target, candidates, install=False)
        if task not in {"ALREADY_ENROLLED", "AUTOSTART_NOT_ENROLLED"}:
            return _clean_autostart_receipt(task)
        if task == "AUTOSTART_NOT_ENROLLED":
            with acquisition._clean_supervisor_slot():
                if acquisition._clean_pending(pending):
                    deny("UNKNOWN_EFFECT")
                inspect_target(enrolling=True)
                reserved = True
                acquisition._clean_record_pending(pending, True)
                created = acquisition._clean_task(owner, target, candidates, install=True)
                if created not in {"ENROLLED", "ALREADY_ENROLLED"}:
                    if created in {"AUTOSTART_CONFLICT", "WINDOWS_OWNER_CONTEXT_UNVERIFIED",
                                   "AUTOSTART_RUNTIME_UNAVAILABLE"}:
                        acquisition._clean_record_pending(pending, False)
                        return _clean_autostart_receipt(created)
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                if acquisition._clean_task(owner, target, candidates, install=False) != "ALREADY_ENROLLED":
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                if acquisition._native_operator() != owner:
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                acquisition._clean_record_pending(pending, False)
                return _clean_autostart_receipt("INSTALLED_BUT_NOT_RUNNING")
        state, identity = acquisition._clean_inventory(target, candidates, session, sid)
        if acquisition._clean_launch_pending():
            return _clean_autostart_receipt("UNKNOWN_EFFECT")
        if state == "OWNED":
            observed = replace(target, executable=identity[1])
            acquisition._verify_google_chrome(observed)
            acquisition._probe_cdp(observed)
            if (acquisition._clean_inventory(target, candidates, session, sid) != (state, identity)
                    or acquisition._native_operator() != owner):
                return _clean_autostart_receipt("UNKNOWN_EFFECT")
            return _clean_autostart_receipt("CDP_VERIFIED")
        return _clean_autostart_receipt("INSTALLED_BUT_NOT_RUNNING" if state == "ABSENT" else "UNKNOWN_EFFECT")
    except (ValueError, Denied) as exc:
        return _clean_autostart_receipt("UNKNOWN_EFFECT" if reserved else str(exc))
    except Exception:
        return _clean_autostart_receipt("UNKNOWN_EFFECT" if reserved else "AUTOSTART_RUNTIME_UNAVAILABLE")


def _change_protected_clean_task(event, *, operation):
    reset = operation == _CLEAN_TASK_RESET
    codes = _CLEAN_RESET_CODES if reset else _CLEAN_MIGRATION_CODES

    def receipt(code):
        return _clean_autostart_receipt(code, operation=operation)

    receipt("PROTECTED_HOSTOPS_ONLY")
    dispatched = False
    try:
        _require_protected_clean_runner("issue_comment")
        sha = _protected_clean_workflow_sha(write=True)
        if parse_event(event, "write")["operation"] != operation:
            deny("PROTECTED_HOSTOPS_ONLY")
        from ai_commerce.remote import chrome_acquisition as acquisition
        owner = acquisition._native_operator()
        _persistent_clean_source(acquisition, sha)
        with acquisition._clean_supervisor_slot():
            target, candidates = acquisition._clean_target(owner)
            if acquisition._native_operator() != owner:
                deny("WINDOWS_OWNER_CONTEXT_UNVERIFIED")
            dispatched = True
            if reset:
                code = acquisition._clean_task(owner, target, candidates, install=False, reset=True)
            else:
                code = acquisition._clean_task(owner, target, candidates, install=False, migrate=True)
            if acquisition._native_operator() != owner or code not in codes:
                return receipt("UNKNOWN_EFFECT")
            return receipt(code)
    except (ValueError, Denied) as exc:
        return receipt("UNKNOWN_EFFECT" if dispatched else str(exc))
    except Exception:
        return receipt("UNKNOWN_EFFECT" if dispatched else "AUTOSTART_RUNTIME_UNAVAILABLE")


def _run_hostops_powershell_read(script: str, timeout: int, unavailable: str):
    from ai_commerce.remote import chrome_acquisition as acquisition

    try:
        return subprocess.run(
            [str(acquisition._powershell()), "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [Text.UTF8Encoding]::new();\n" + script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, env=acquisition._child_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (acquisition.AcquisitionBlocked, OSError, subprocess.TimeoutExpired):
        raise Denied(unavailable) from None


def _runner_runtime_inventory():
    """Read-only Windows Task Scheduler/CIM snapshot; never publish process commands.

    The PowerShell source is a fixed literal with three fixed AI Commerce roots.
    No Issue field, shell fragment, credential, full path or process command line
    is interpolated into the subprocess or returned in a public Issue.
    """
    if os.name != "nt":
        deny("RUNNER_RUNTIME_WINDOWS_ONLY")
    script = r"""
$ErrorActionPreference = 'Stop'
$specs = @(
  @{ name='AI-COMMERCE-EF02-01'; root='C:\TOOL\ai-commerce-runner'; task='AI-Commerce-EF02-Interactive'; launcher='EF02-Hidden-Runner.vbs' },
  @{ name='AI-COMMERCE-EF02-02'; root='C:\TOOL\ai-commerce-runner-ef02-02'; task='AI-Commerce-EF02-02-Interactive'; launcher='Runner-Hidden.vbs' },
  @{ name='AI-COMMERCE-HOSTOPS-01'; root='C:\TOOL\ai-commerce-runner-hostops-01'; task='AI-Commerce-HostOps-01-Interactive'; launcher='Runner-Hidden.vbs' }
)
$listeners = @(Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe'" -ErrorAction Stop)
$launchers = @(Get-CimInstance Win32_Process -Filter "Name='wscript.exe'" -ErrorAction Stop)
$found = 0
$records = @(
  foreach ($spec in $specs) {
    $task = Get-ScheduledTask -TaskName $spec.task -ErrorAction SilentlyContinue
    $expected = Join-Path $spec.root 'bin\Runner.Listener.exe'
    $matched = @($listeners | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -ieq $expected) })
    $found += $matched.Count
    $launcher = Join-Path $spec.root $spec.launcher
    $active = @($launchers | Where-Object { $_.CommandLine -and ($_.CommandLine -like ('*' + $launcher + '*')) })
    $taskState = if ($null -eq $task) { 'NOT_REGISTERED' } else { [string]$task.State }
    $taskInteractive = ($null -ne $task -and [string]$task.Principal.LogonType -eq 'Interactive')
    $taskHidden = ($null -ne $task -and @($task.Actions).Count -eq 1 -and
        [string]$task.Actions[0].Execute -ilike '*\wscript.exe' -and
        [string]$task.Actions[0].Arguments -like ('*' + $spec.launcher + '*'))
    [pscustomobject]@{
      name = $spec.name
      task_state = $taskState
      task_interactive = [bool]$taskInteractive
      hidden_launcher_configured = [bool]$taskHidden
      hidden_launcher_process_count = [int]$active.Count
      listener_process_count = [int]$matched.Count
      listener_session_ids = @($matched | ForEach-Object { [int]$_.SessionId } | Sort-Object -Unique)
    }
  }
)
[pscustomobject]@{
  all_listener_count = [int]$listeners.Count
  unmatched_listener_count = [int]($listeners.Count - $found)
  runners = $records
} | ConvertTo-Json -Compress -Depth 5
"""
    result = _run_hostops_powershell_read(script, 25, "RUNNER_RUNTIME_READ_UNAVAILABLE")
    if result.returncode != 0 or len(result.stdout) > 16384:
        deny("RUNNER_RUNTIME_READ_FAILED")
    try:
        data = json.loads(result.stdout.lstrip("\ufeff"))
    except (ValueError, UnicodeError):
        deny("RUNNER_RUNTIME_INVALID_JSON")
    if type(data) is not dict or set(data) != {
        "all_listener_count", "unmatched_listener_count", "runners"
    }:
        deny("RUNNER_RUNTIME_INVALID_SHAPE")
    records = data["runners"]
    expected = ("AI-COMMERCE-EF02-01", "AI-COMMERCE-EF02-02",
                "AI-COMMERCE-HOSTOPS-01")
    if type(records) is not list or len(records) != 3 or [r.get("name") for r in records] != list(expected):
        deny("RUNNER_RUNTIME_TARGET_MISMATCH")
    if (type(data["all_listener_count"]) is not int or
            type(data["unmatched_listener_count"]) is not int or
            not 0 <= data["unmatched_listener_count"] <= data["all_listener_count"] <= 1000):
        deny("RUNNER_RUNTIME_COUNT_INVALID")
    for row in records:
        if type(row) is not dict or set(row) != {
            "name", "task_state", "task_interactive", "hidden_launcher_configured",
            "hidden_launcher_process_count", "listener_process_count", "listener_session_ids"
        }:
            deny("RUNNER_RUNTIME_ROW_INVALID")
        if (row["task_state"] not in ("Running", "Ready", "Disabled", "Queued",
                                     "NOT_REGISTERED") or
                any(type(row[key]) is not bool for key in
                    ("task_interactive", "hidden_launcher_configured")) or
                any(type(row[key]) is not int or not 0 <= row[key] <= 10 for key in
                    ("hidden_launcher_process_count", "listener_process_count")) or
                type(row["listener_session_ids"]) is not list or
                any(type(sid) is not int or not 0 <= sid < 100000 for sid in
                    row["listener_session_ids"])):
            deny("RUNNER_RUNTIME_ROW_VALUE_INVALID")
    if sum(row["listener_process_count"] for row in records) != (
            data["all_listener_count"] - data["unmatched_listener_count"]):
        deny("RUNNER_RUNTIME_COUNT_MISMATCH")
    return {
        "status": "OBSERVED", "operation": "runner_runtime_inventory",
        "windows": True, "all_listener_count": data["all_listener_count"],
        "unmatched_listener_count": data["unmatched_listener_count"],
        "runners": records,
        "arbitrary_shell_enabled": False,
    }


class _CdpNoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _cdp_9333_status():
    """Fixed READ-ONLY Chrome 9333 probe, no navigation, tab content or Send.

    Native information is reduced to bounded counters/booleans before leaving
    the Windows process; command lines, profile paths, PIDs and URLs are never
    exported into a public GitHub Issue. This DOES NOT validate a D2 route,
    a logged-in exact ChatGPT tab, a grant, or any Wake authorization.
    """
    if os.name != "nt":
        deny("CDP_DIAGNOSTIC_WINDOWS_ONLY")
    script = r"""
$ErrorActionPreference = 'Stop'
$port = 9333
$items = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Where-Object { [int]$_.LocalPort -eq $port })
$session = [int](Get-Process -Id $PID -ErrorAction Stop).SessionId
$loopback = 0
$other = 0
$chromeOwned = 0
$sameSession = 0
foreach ($item in $items) {
    if ([string]$item.LocalAddress -cne '127.0.0.1') {
        $other++
        continue
    }
    $loopback++
    $owner = Get-CimInstance Win32_Process -Filter (
        'ProcessId=' + [int]$item.OwningProcess) -ErrorAction SilentlyContinue
    if ($null -ne $owner -and [string]$owner.Name -ieq 'chrome.exe') {
        $chromeOwned++
        if ([int]$owner.SessionId -eq $session -and $session -gt 0) {
            $sameSession++
        }
    }
}
@{
  listener_count = [int]$items.Count
  loopback_count = [int]$loopback
  unexpected_binding_count = [int]$other
  chrome_owned_loopback_count = [int]$chromeOwned
  same_session_chrome_count = [int]$sameSession
} | ConvertTo-Json -Compress
"""
    native = _run_hostops_powershell_read(script, 18, "CDP_NATIVE_READ_UNAVAILABLE")
    if native.returncode != 0 or len(native.stdout) > 4096:
        deny("CDP_NATIVE_READ_FAILED")
    try:
        record = json.loads(native.stdout.lstrip("\ufeff"))
    except (UnicodeError, ValueError):
        deny("CDP_NATIVE_INVALID_JSON")
    names = (
        "listener_count", "loopback_count", "unexpected_binding_count",
        "chrome_owned_loopback_count", "same_session_chrome_count"
    )
    if type(record) is not dict or set(record) != set(names):
        deny("CDP_NATIVE_INVALID_SHAPE")
    if any(type(record[key]) is not int or not 0 <= record[key] <= 16 for key in names):
        deny("CDP_NATIVE_INVALID_COUNTS")
    if (record["loopback_count"] + record["unexpected_binding_count"] !=
            record["listener_count"] or
            record["same_session_chrome_count"] >
            record["chrome_owned_loopback_count"] or
            record["chrome_owned_loopback_count"] >
            record["loopback_count"]):
        deny("CDP_NATIVE_COUNT_MISMATCH")
    protocol_ok = False
    if (record["listener_count"] == 1 and
            record["loopback_count"] == 1 and
            record["chrome_owned_loopback_count"] == 1 and
            record["same_session_chrome_count"] == 1):
        # Safe fixed localhost GET. No redirect, no proxy, no cookies, no tabs.
        request = Request(
            "http://127.0.0.1:9333/json/version",
            headers={"Accept": "application/json", "User-Agent": "AI-Commerce-CDP-ReadOnly"},
            method="GET",
        )
        try:
            opener = build_opener(ProxyHandler({}), _CdpNoRedirect())
            with opener.open(request, timeout=2) as response:
                raw = response.read(16385)
                if response.status == 200 and 0 < len(raw) <= 16384:
                    version = json.loads(raw.decode("utf-8"))
                    protocol_ok = (type(version) is dict and
                                   isinstance(version.get("Browser"), str) and
                                   version["Browser"].startswith("Chrome/") and
                                   isinstance(version.get("Protocol-Version"), str))
        except (OSError, ValueError, UnicodeError, TimeoutError):
            protocol_ok = False
    ready = (record["listener_count"] == 1 and
             record["loopback_count"] == 1 and
             record["chrome_owned_loopback_count"] == 1 and
             record["same_session_chrome_count"] == 1 and protocol_ok)
    return {
        "status": "OBSERVED", "operation": "cdp_9333_status", "cdp_port": 9333,
        **record, "devtools_protocol_ok": bool(protocol_ok),
        "infrastructure_ready": bool(ready),
        "exact_chat_verified": False, "wake_sent": False,
        "arbitrary_shell_enabled": False,
    }


def _cdp_9444_private_context(acquisition, argv=()):
    """Read the fixed clean target or attest an independent legacy config.

    No Issue/environment identity, manifest-selected path, enrollment or launch.
    Failure to attest is UNKNOWN, never evidence that the listener is absent.
    """
    from ai_commerce.remote.local_wake_entry import _windows_process_sid

    local = acquisition._native_local_app_data()
    session, sid = acquisition._interactive_session(), _windows_process_sid()
    if (type(session) is not int or session <= 0 or type(sid) is not str
            or not re.fullmatch(r"S-1-(?:\d+-){1,14}\d+", sid)
            or sid in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}):
        deny("OWNER_SESSION_UNATTESTED")
    root = local / "AI-Commerce"
    directory = acquisition._clean_switch(argv, "--user-data-dir")
    clean = root / acquisition._CLEAN_9444_PROFILE
    if directory is not None and Path(normpath(directory)) == clean:
        return acquisition._clean_target((local, session, sid)), session, sid

    from ai_commerce.remote.local_completion_handoff import _private_identity

    if (not local.is_absolute() or _fixed_private_metadata_kind(root, directory=True) != "PRESENT"
            or _private_identity(root) != sid):
        deny("OWNER_SESSION_UNATTESTED")
    candidates = (root / "Chrome-Acquisition" / "config.json",
                  root / "chrome.json", root / "chrome-acquisition.json")
    states = [_fixed_private_metadata_kind(path) for path in candidates]
    if "UNAVAILABLE" in states or states.count("PRESENT") != 1:
        deny("OWNER_SESSION_UNATTESTED")
    config = candidates[states.index("PRESENT")]
    # Check custody BEFORE reading even the bounded private config bytes.
    for path in (config.parent, config):
        if _private_identity(path) != sid:
            deny("OWNER_SESSION_UNATTESTED")
    env = acquisition.load_environment(config, Path(__file__).resolve().parents[3], local_app_data=local)
    if env.port != 9444 or env.endpoint.rstrip("/") != "http://127.0.0.1:9444":
        deny("OWNER_SESSION_UNATTESTED")
    for path in (env.user_data_dir, *env.user_data_dir.parents, env.user_data_dir / env.profile_directory):
        if (path == root or root in path.parents) and _private_identity(path) != sid:
            deny("OWNER_SESSION_UNATTESTED")
    return env, session, sid


def _cdp_9444_owned_target(acquisition, context, listeners, owners, pid):
    target, session, sid = context
    inventory = {"listeners": listeners, "processes": [row for row, _ in owners.values()]}
    if isinstance(target, tuple):
        clean, candidates = target
        try:
            state, identity = acquisition._clean_inventory(clean, candidates, session, sid, inventory=inventory)
        except acquisition._Clean9444Hold:
            raise acquisition.AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN") from None
        if state == "OWNED" and identity[0] == pid:
            return replace(clean, executable=identity[1])
    else:
        # The legacy config continues to require both explicit switches.
        argv = owners[pid][1]
        if (acquisition._clean_switch(argv, "--profile-directory") is not None
                and acquisition._clean_switch(argv, "--remote-debugging-address") is not None
                and acquisition.evaluate(target, inventory, session_id=session, expected_sid=sid,
                                         parse=acquisition._commandline_argv) == ("OWNED", pid)):
            return target
    return None


def _cdp_9444_inventory(acquisition, value):
    """Validate the native snapshot without converting missing data to zero."""
    if (type(value) is not dict or set(value) != {"listeners", "processes"}
            or type(value["listeners"]) is not list or len(value["listeners"]) > 16
            or type(value["processes"]) is not list or len(value["processes"]) > 256):
        raise ValueError("INVALID_TOPOLOGY")
    listeners, processes = value["listeners"], value["processes"]
    for row in listeners:
        if (type(row) is not dict or set(row) != {"LocalAddress", "LocalPort", "OwningProcess"}
                or type(row["LocalAddress"]) is not str or not 0 < len(row["LocalAddress"]) <= 64
                or type(row["LocalPort"]) is not int or row["LocalPort"] != 9444
                or type(row["OwningProcess"]) is not int or row["OwningProcess"] <= 0):
            raise ValueError("INVALID_TOPOLOGY")
    owners, claimants = {}, []
    for proc in processes:
        if (type(proc) is not dict or set(proc) != {
                "ProcessId", "ExecutablePath", "CommandLine", "SessionId", "OwnerSid", "CreationDate"}
                or type(proc["ProcessId"]) is not int or proc["ProcessId"] <= 0
                or proc["ProcessId"] in owners
                or type(proc["SessionId"]) is not int or proc["SessionId"] < 0
                or any(type(proc[key]) is not str or not 0 < len(proc[key]) <= limit for key, limit in (
                    ("ExecutablePath", 32768), ("CommandLine", 32768), ("OwnerSid", 192), ("CreationDate", 80)))):
            raise ValueError("INVALID_TOPOLOGY")
        if (re.fullmatch(r"S-1-(?:\d+-){1,14}\d+", proc["OwnerSid"]) is None
                or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?(?:Z|[+-]\d\d:\d\d)",
                                proc["CreationDate"]) is None):
            raise ValueError("INVALID_TOPOLOGY")
        datetime.fromisoformat(proc["CreationDate"].replace("Z", "+00:00"))
        argv = acquisition._commandline_argv(proc["CommandLine"])
        if (type(argv) is not list or not 0 < len(argv) <= 256
                or any(type(arg) is not str for arg in argv)):
            raise ValueError("INVALID_TOPOLOGY")
        owners[proc["ProcessId"]] = (proc, argv)
        if any(arg.lower() == "--type" or arg.lower().startswith("--type=") for arg in argv):
            continue
        ports = [arg for arg in argv if arg.lower() == "--remote-debugging-port"
                 or arg.lower().startswith("--remote-debugging-port=")]
        if ports:
            if (len(ports) != 1 or not ports[0].startswith("--remote-debugging-port=")
                    or re.fullmatch(r"[0-9]{1,5}", ports[0].split("=", 1)[1]) is None
                    or not 0 < int(ports[0].split("=", 1)[1]) <= 65535):
                raise ValueError("INVALID_TOPOLOGY")
            if int(ports[0].split("=", 1)[1]) == 9444:
                claimants.append(proc["ProcessId"])
    return listeners, owners, claimants


def _cdp_9444_topology():
    """Fixed read-only topology; public output contains counts and closed codes.

    Signed Chrome, profile custody, native SID and interactive session
    must independently agree before the strict version GET. This proves neither
    enrollment nor Chat A, and never attaches a websocket or sends anything.
    """
    fields = {"status", "code", "operation", "cdp_port", "listener_count", "loopback_count",
              "unexpected_binding_count", "chrome_owned_loopback_count", "same_session_chrome_count",
              "devtools_protocol_ok", "infrastructure_ready", "exact_chat_verified", "wake_sent",
              "arbitrary_shell_enabled"}
    if not fields <= PERSONAL_PUBLIC_FIELDS:
        deny("PUBLIC_TRANSPORT_GAP")
    if os.name != "nt":
        deny("CDP_TOPOLOGY_WINDOWS_ONLY")
    unknown = {
        "status": "UNKNOWN", "code": "NATIVE_IMPORT_FAILED", "operation": "cdp_9444_topology",
        "cdp_port": 9444, "listener_count": None, "loopback_count": None,
        "unexpected_binding_count": None, "chrome_owned_loopback_count": None,
        "same_session_chrome_count": None, "devtools_protocol_ok": None,
        "infrastructure_ready": False, "exact_chat_verified": False,
        "wake_sent": False, "arbitrary_shell_enabled": False,
    }
    # Diagnose only the observer stage, never an exception's type or contents.
    # Each failure retains unproven counts; a failed recheck cannot reuse stale
    # counts from the first snapshot as a current Windows observation.
    try:
        from ai_commerce.remote import chrome_acquisition as acquisition
    except Exception:
        return unknown
    try:
        native = acquisition._query_windows(9444)
    except Exception:
        return {**unknown, "code": "NATIVE_QUERY_FAILED"}
    try:
        listeners, owners, claimants = _cdp_9444_inventory(acquisition, native)
    except Exception:
        return {**unknown, "code": "NATIVE_NORMALIZATION_FAILED"}
    result = unknown.copy()
    result.update(listener_count=len(listeners),
                  loopback_count=sum(row["LocalAddress"] == "127.0.0.1" for row in listeners),
                  unexpected_binding_count=sum(row["LocalAddress"] != "127.0.0.1" for row in listeners))
    if not listeners:
        result.update(status="OBSERVED", code="CLAIMANT_WITHOUT_LISTENER" if claimants else "NO_LISTENER",
                      chrome_owned_loopback_count=0, same_session_chrome_count=0)
        return result
    if len(listeners) != 1 or result["unexpected_binding_count"]:
        result.update(status="OBSERVED", code="UNTRUSTED_BIND")
        return result
    pid = listeners[0]["OwningProcess"]
    if pid not in owners or claimants != [pid]:
        result.update(status="OBSERVED", code="UNTRUSTED_OWNER")
        return result
    proc, argv = owners[pid]
    for name in ("--user-data-dir", "--profile-directory", "--remote-debugging-address",
                 "--remote-debugging-port"):
        switches = [arg for arg in argv if arg.lower() == name or arg.lower().startswith(name + "=")]
        optional = name in ("--profile-directory", "--remote-debugging-address")
        if (not switches and not optional
                or switches and (len(switches) != 1 or not switches[0].startswith(name + "="))):
            result.update(status="OBSERVED", code="UNTRUSTED_OWNER")
            return result
    try:
        context = _cdp_9444_private_context(acquisition, argv)
    except Exception:
        result["code"] = "OWNER_SESSION_UNATTESTED"
        return result
    try:
        env = _cdp_9444_owned_target(acquisition, context, listeners, owners, pid)
        if env is None:
            raise acquisition.AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        acquisition._verify_google_chrome(env)
    except acquisition.AcquisitionBlocked:
        result.update(status="OBSERVED", code="UNTRUSTED_OWNER")
        return result
    except Exception:
        result["code"] = "OWNER_SESSION_UNATTESTED"
        return result
    # One independent re-observation, never a retry or launch on drift/failure.
    try:
        native = acquisition._query_windows(9444)
    except Exception:
        return {**unknown, "code": "NATIVE_RECHECK_QUERY_FAILED"}
    try:
        current = _cdp_9444_inventory(acquisition, native)
    except Exception:
        return {**unknown, "code": "NATIVE_RECHECK_NORMALIZATION_FAILED"}
    try:
        # A new same-profile process need not claim 9444; compare all processes
        # before evaluating ownership so known drift stays TOPOLOGY_CHANGED.
        if (current[0] != listeners or current[2] != claimants
                or current[1] != owners
                or _cdp_9444_private_context(acquisition, argv) != context):
            result["code"] = "TOPOLOGY_CHANGED"
            return result
        if _cdp_9444_owned_target(acquisition, context, current[0], current[1], pid) != env:
            result["code"] = "TOPOLOGY_CHANGED"
            return result
    except Exception:
        return {**unknown, "code": "NATIVE_RECHECK_FAILED"}
    result.update(chrome_owned_loopback_count=1, same_session_chrome_count=1)
    try:
        acquisition._probe_cdp(env)
    except Exception:
        # The strict helper deliberately hides HTTP/timeout/JSON details.
        result.update(code="CDP_PROTOCOL_FAILED", devtools_protocol_ok=False)
        return result
    result.update(status="OBSERVED", code="CDP_PROTOCOL_HEALTHY", devtools_protocol_ok=True,
                  infrastructure_ready=True)
    return result


_CONFIG_9444_SLOT_IDS = ("SLOT_01", "SLOT_02", "SLOT_03", "SLOT_04", "SLOT_05", "SLOT_06")
_CONFIG_9444_SLOT_STATES = ("ABSENT", "PRESENT_FILE", "PRESENT_NONFILE", "UNAVAILABLE_OR_REPARSE")


def _config_registry_path_state(path: Path, *, directory: bool = False) -> str:
    """Inspect one component after its parents have passed the metadata check."""
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        return "ABSENT" if getattr(exc, "winerror", None) in (None, 2, 3) else "UNAVAILABLE_OR_REPARSE"
    except (OSError, ValueError):
        return "UNAVAILABLE_OR_REPARSE"
    if (stat.S_ISLNK(info.st_mode) or
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        return "UNAVAILABLE_OR_REPARSE"
    if directory and not stat.S_ISDIR(info.st_mode):
        return "UNAVAILABLE_OR_REPARSE"
    return "PRESENT_FILE" if stat.S_ISREG(info.st_mode) else "PRESENT_NONFILE"


def _cdp_9444_config_registry():
    """Report fixed-slot metadata from the current Windows token's known folder."""
    unavailable = "UNAVAILABLE_OR_REPARSE"

    def receipt(code, states=(unavailable,) * len(_CONFIG_9444_SLOT_IDS)):
        return {
            "status": "OBSERVED" if code == "METADATA_ONLY" else "UNKNOWN",
            "code": code,
            "operation": "cdp_9444_config_registry",
            "cdp_port": 9444,
            "slots": [{"slot_id": slot, "status": state}
                      for slot, state in zip(_CONFIG_9444_SLOT_IDS, states)],
            "counts": {state: states.count(state) for state in _CONFIG_9444_SLOT_STATES},
            "no_shell_command_executed": True,
            "browser_attached": False,
            "chrome_launched": False,
            "config_written": False,
            "enrollment_performed": False,
            "send_performed": False,
            "grant_issued": False,
        }

    if os.name != "nt":
        return receipt("OWNER_KNOWN_FOLDER_UNAVAILABLE")
    try:
        from ai_commerce.remote import chrome_acquisition as acquisition
        relatives = acquisition._CONFIG_9444_RELATIVE_PATHS
        if len(relatives) != len(_CONFIG_9444_SLOT_IDS):
            return receipt("REGISTRY_SOURCE_UNAVAILABLE")
        candidates = tuple(tuple(_relative_path(relative)) for relative in relatives)
    except (ImportError, AttributeError, Denied):
        return receipt("REGISTRY_SOURCE_UNAVAILABLE")
    if (len({relative.lower() for relative in relatives}) != len(candidates)
            or any(len(parts) > 2 for parts in candidates)):
        return receipt("REGISTRY_SOURCE_UNAVAILABLE")

    try:
        local = acquisition._native_local_app_data()
    except (OSError, ValueError, AttributeError, ImportError):
        return receipt("OWNER_KNOWN_FOLDER_UNAVAILABLE")
    if (not isinstance(local, PureWindowsPath) or not local.is_absolute()
            or not re.fullmatch(r"[A-Za-z]:", local.drive)
            or len(str(local)) > 4096 or len(local.parts) > 32 or ".." in local.parts):
        return receipt("OWNER_KNOWN_FOLDER_UNAVAILABLE")
    for part in (*reversed(local.parents), local):
        if _config_registry_path_state(part, directory=True) != "PRESENT_NONFILE":
            return receipt("OWNER_KNOWN_FOLDER_UNAVAILABLE")

    root = local / "AI-Commerce"
    root_state = _config_registry_path_state(root, directory=True)
    states = []
    for parts in candidates:
        state = root_state
        if state == "PRESENT_NONFILE" and len(parts) == 2:
            state = _config_registry_path_state(root / parts[0], directory=True)
        if state == "PRESENT_NONFILE":
            state = _config_registry_path_state(root.joinpath(*parts))
        states.append(state)
    return receipt("CONFIG_CUSTODY_UNAVAILABLE" if unavailable in states else "METADATA_ONLY", states)


def _validate_standing_snapshot(snapshot):
    """Return only a fixed, typed public schema; reject raw/extra/duplicate data."""
    if (type(snapshot) is not dict or set(snapshot) != {
            "status", "operation", "windows", "diagnostics",
            "arbitrary_shell_enabled", "exact_chat_verified", "wake_sent"} or
            type(snapshot["status"]) is not str or snapshot["status"] != "OBSERVED" or
            type(snapshot["operation"]) is not str or
            snapshot["operation"] != "standing_read_diagnostics" or
            snapshot["windows"] is not True or
            any(snapshot[key] is not False for key in (
                "arbitrary_shell_enabled", "exact_chat_verified", "wake_sent"))):
        deny("INVALID_PUBLIC_DIAGNOSTIC_RECEIPT")
    rows = snapshot["diagnostics"]
    if type(rows) is not list or len(rows) != len(STANDING_READ_CATEGORIES):
        deny("INVALID_PUBLIC_DIAGNOSTIC_RECEIPT")
    clean = []
    for category, row in zip(STANDING_READ_CATEGORIES, rows):
        if (type(row) is not dict or set(row) != {"category", "code"} or
                type(row["category"]) is not str or row["category"] != category or
                type(row["code"]) is not str or row["code"] not in _STANDING_CATEGORY_CODES[category]):
            deny("INVALID_PUBLIC_DIAGNOSTIC_RECEIPT")
        clean.append({"category": category, "code": row["code"]})
    result = {**snapshot, "diagnostics": clean}
    if len(json.dumps(result, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()) > MAX_READ:
        deny("INVALID_PUBLIC_DIAGNOSTIC_RECEIPT")
    return result


def _fixed_private_metadata_kind(path: Path, *, directory: bool = False) -> str:
    """Stat only one fixed path and its ancestors, never follow reparse points.

    No file content, directory listing, arbitrary user path or name is returned.
    Missing and unreadable/unsafe paths are different. These checks are advisory
    metadata, not ownership, signature, active session or D5 readiness proof.
    """
    try:
        for item in (path, *path.parents):
            info = item.lstat()
            if (stat.S_ISLNK(info.st_mode) or
                    getattr(info, "st_file_attributes", 0) &
                    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                return "UNAVAILABLE"
            if item == path and not (stat.S_ISDIR(info.st_mode) if directory
                                     else stat.S_ISREG(info.st_mode)):
                return "UNAVAILABLE"
        return "PRESENT"
    except FileNotFoundError:
        return "ABSENT"
    except (OSError, ValueError, TypeError):
        return "UNAVAILABLE"


def _private_d5_metadata_codes():
    """Bounded local-only metadata preflight; emits four fixed public enums.

    Reuse the real Chrome acquisition *validator* for one of three literal
    config locations. It may read that single <=8KiB private config locally,
    but returns no values, file names, paths, exceptions or raw content.
    No profile contents, Chrome tabs, tokens, D5 ledger or external calls.
    A manifest-selected alternative config is deliberately NOT followed.
    """
    codes = {
        "chrome_profile": "UNVERIFIED",
        "chrome_config": "UNAVAILABLE",
        "chrome_markers": "UNVERIFIED",
        "d5_metadata": "UNAVAILABLE",
    }
    raw_local = os.environ.get("LOCALAPPDATA")
    if type(raw_local) is not str or not raw_local:
        return codes
    local = Path(raw_local)
    if not local.is_absolute():
        return codes
    private_root = local / "AI-Commerce"
    root_kind = _fixed_private_metadata_kind(private_root, directory=True)
    if root_kind == "ABSENT":
        codes["chrome_config"] = "KNOWN_CANDIDATES_ABSENT"
        codes["d5_metadata"] = "MANIFEST_ABSENT"
        return codes
    if root_kind != "PRESENT":
        return codes
    manifest = _fixed_private_metadata_kind(private_root / "wake-readiness.json")
    codes["d5_metadata"] = (
        "MANIFEST_PRESENT" if manifest == "PRESENT" else
        "MANIFEST_ABSENT" if manifest == "ABSENT" else "UNAVAILABLE")
    candidates = (
        private_root / "Chrome-Acquisition" / "config.json",
        private_root / "chrome.json",
        private_root / "chrome-acquisition.json",
    )
    checks = tuple(_fixed_private_metadata_kind(candidate) for candidate in candidates)
    if "UNAVAILABLE" in checks:
        return codes
    found = [candidate for candidate, state in zip(candidates, checks)
             if state == "PRESENT"]
    if not found:
        codes["chrome_config"] = "KNOWN_CANDIDATES_ABSENT"
        return codes
    if len(found) != 1:
        codes["chrome_config"] = "KNOWN_CANDIDATES_AMBIGUOUS"
        return codes
    try:
        from ai_commerce.remote import chrome_acquisition
        checkout = Path(__file__).resolve().parents[3]
        env = chrome_acquisition.load_environment(
            found[0], checkout, local_app_data=local)
        # A valid acquisition contract is not proof of a signed Chrome
        # executable or an authenticated exact ChatGPT tab.
        if (_fixed_private_metadata_kind(env.user_data_dir, directory=True) != "PRESENT"
                or _fixed_private_metadata_kind(
                    env.user_data_dir / env.profile_directory, directory=True) != "PRESENT"):
            codes["chrome_config"] = "KNOWN_CONFIG_NOT_VERIFIED"
            return codes
        codes["chrome_config"] = (
            "KNOWN_CONFIG_EXACT_9333" if env.port == 9333
            else "KNOWN_CONFIG_OTHER_PORT")
        codes["chrome_profile"] = "DEDICATED_DIR_PRESENT"
        marker_states = tuple(_fixed_private_metadata_kind(env.user_data_dir / name)
                              for name in chrome_acquisition._MARKERS)
        codes["chrome_markers"] = (
            "UNAVAILABLE" if "UNAVAILABLE" in marker_states else
            "LOCK_MARKER_PRESENT" if "PRESENT" in marker_states else
            "LOCK_MARKERS_ABSENT")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, ImportError):
        codes["chrome_config"] = "KNOWN_CONFIG_NOT_VERIFIED"
    return codes


def _standing_read_snapshot():
    """Read fixed operational probes; only public enum codes leave runner.

    A single locally verified, bounded private Chrome config may be parsed
    strictly via the existing acquisition validator. Its bytes, profile paths,
    marker names, private D5 manifest and all failures remain local. Never
    infer browser login, an exact ChatGPT chat, Chrome signature or Wake.
    """
    if os.name != "nt":
        deny("STANDING_READ_WINDOWS_ONLY")
    codes = {category: allowed[0] if len(allowed) == 1 else "UNAVAILABLE"
             for category, allowed in _STANDING_CATEGORY_CODES.items()}
    try:
        private_codes = _private_d5_metadata_codes()
        for key in ("chrome_profile", "chrome_config", "chrome_markers", "d5_metadata"):
            value = private_codes[key]
            if value in _STANDING_CATEGORY_CODES[key]:
                codes[key] = value
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        pass
    try:
        cdp = _cdp_9333_status()
    except (Denied, OSError, ValueError, KeyError, TypeError):
        pass
    else:
        codes["cdp_9333_listener"] = (
            "UNSAFE_BINDING" if cdp["unexpected_binding_count"] else
            "ABSENT" if cdp["listener_count"] == 0 else
            "EXACT_LOOPBACK" if cdp["listener_count"] == cdp["loopback_count"] == 1 else "UNSAFE_BINDING")
        codes["chrome_process"] = "MATCHED" if cdp["chrome_owned_loopback_count"] == 1 else "UNVERIFIED"
        codes["chrome_session"] = "MATCHED" if cdp["same_session_chrome_count"] == 1 else "UNVERIFIED"
        codes["cdp_protocol"] = "RESPONDING" if cdp["devtools_protocol_ok"] is True else "UNVERIFIED"
    try:
        runners = _runner_runtime_inventory()
    except (Denied, OSError, ValueError, KeyError, TypeError):
        pass
    else:
        for name, row in zip(("ef02_01", "ef02_02", "hostops_01"), runners["runners"]):
            codes[name + "_task"] = row["task_state"].upper()
            codes[name + "_listener"] = "PRESENT" if row["listener_process_count"] else "ABSENT"
    try:
        head = _git("rev-parse", "HEAD").strip()
        if re.fullmatch(r"[a-f0-9]{40}", head):
            changed = _git("status", "--porcelain=v1", "--untracked-files=no")
            codes["repo_git"] = "TRACKED_CHANGES" if changed.strip() else "CLEAN"
    except (Denied, OSError, ValueError, TypeError):
        pass
    version = tuple(int(part) for part in platform.python_version_tuple())
    codes["python_runtime"] = "SUPPORTED" if version >= (3, 11, 0) else "UNSUPPORTED"
    return _validate_standing_snapshot({
        "status": "OBSERVED", "operation": "standing_read_diagnostics", "windows": True,
        "diagnostics": [{"category": category, "code": codes[category]}
                        for category in STANDING_READ_CATEGORIES],
        "arbitrary_shell_enabled": False, "exact_chat_verified": False, "wake_sent": False,
    })


# LOCAL PRIVATE foundation only. Deliberately absent from parse_event, perform,
# main, PERSONAL_PUBLIC_FIELDS and the sixteen standing READ categories.
_HEALTH_SCHEMA = "ExecutorHealthLocalReceipt.v1"
_HEALTH_TIMEOUT = 5
_HEALTH_MAX_AGE = 3600
_HEALTH_LIMIT = 8192
_HEALTH_VERSION = r"\d{1,4}\.\d{1,4}\.\d{1,4}(?:-(?:alpha|beta|rc)\.\d{1,4})?"
_HEALTH_PROBES = {
    "codex_version": ("codex.exe", ("--version",)),
    "codex_login": ("codex.exe", ("login", "status")),
    "antigravity_version": ("agy.exe", ("--version",)),
}


class ExecutorDefaults(TypedDict):
    state: Literal["VALIDATED", "INVALID", "UNKNOWN"]
    preferred: dict[str, str] | None
    fallback: dict[str, str] | None
    recovery_probe_cooldown_seconds: int | None


class EF01HealthObservation(TypedDict):
    state: Literal["UNKNOWN", "UNTRUSTED", "INVALID", "FRESH", "STALE"]
    outcome: Literal["UNKNOWN", "QUOTA_EXHAUSTED", "DETERMINATE_RETURN"]
    observed_at: str | None


class ExecutorHealthRow(TypedDict):
    executor: Literal["codex", "antigravity"]
    executable: Literal["PRESENT", "ABSENT", "UNAVAILABLE", "UNSUPPORTED_LAUNCHER", "UNKNOWN"]
    version_state: Literal["OBSERVED", "UNKNOWN", "TIMEOUT", "UNAVAILABLE", "MALFORMED"]
    version: str | None
    authentication: Literal["LOGIN_REPORTED", "NOT_LOGGED_IN", "UNKNOWN"]
    login_probe: Literal["OBSERVED", "UNKNOWN", "TIMEOUT", "UNAVAILABLE", "MALFORMED", "NOT_SUPPORTED"]
    provider_identity: Literal["UNKNOWN"]
    account: Literal["UNKNOWN"]
    quota: Literal["UNKNOWN"]
    remaining: None
    reset_at: None
    last_ef01: EF01HealthObservation


class ExecutorHealthLocalReceipt(TypedDict):
    schema_version: Literal["ExecutorHealthLocalReceipt.v1"]
    visibility: Literal["LOCAL_PRIVATE"]
    capability: Literal["SOURCE_ONLY"]
    observed_at: str
    windows: bool
    context_identity: Literal["UNKNOWN"]
    defaults: ExecutorDefaults
    executors: list[ExecutorHealthRow]


def _health_parse_time(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise ValueError("HEALTH_TIME_INVALID")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _executor_defaults() -> ExecutorDefaults:
    """Read one bounded public policy, never mutable .local preference state.

    Share the existing ExecutorSelection validation; report configuration only.
    No resolve_model, observe_result, cooldown scheduling, fallback or save.
    """
    from ai_commerce.engineering.model_defaults import POLICY_PATH
    from ai_commerce.engineering.profile import ExecutorSelection
    invalid = {"state": "INVALID", "preferred": None, "fallback": None,
               "recovery_probe_cooldown_seconds": None}
    path = Path(__file__).absolute().parents[3] / POLICY_PATH
    if _fixed_private_metadata_kind(path) != "PRESENT":
        return invalid
    try:
        with path.open("rb") as stream:
            raw = stream.read(_HEALTH_LIMIT + 1)
        if not 0 < len(raw) <= _HEALTH_LIMIT:
            return invalid
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_unique)
        if (type(obj) is not dict or set(obj) != {
                "version", "hard_default", "preferred", "fallback", "recovery_probe_cooldown_seconds"}
                or type(obj["version"]) is not int or obj["version"] != 1
                or obj["hard_default"] is not False):
            return invalid
        result = {"state": "VALIDATED"}
        for field, executor in (("preferred", "codex"), ("fallback", "antigravity")):
            selection = obj[field]
            if type(selection) is not dict or set(selection) != {"executor", "model", "effort"}:
                return invalid
            ExecutorSelection(**selection)
            if selection["executor"] != executor:
                return invalid
            result[field] = selection
        seconds = obj["recovery_probe_cooldown_seconds"]
        if type(seconds) is not int or not 60 <= seconds <= 604800:
            return invalid
        return {**result, "recovery_probe_cooldown_seconds": seconds}
    except (Denied, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return invalid


def _health_executable(executor):
    """Fixed PATH names only; never search homes or execute shell launchers."""
    name = {"codex": "codex", "antigravity": "agy"}[executor]
    try:
        executable = shutil.which(name + ".exe")
        if executable is None:
            wrapper = shutil.which(name + ".cmd")
            return ("UNSUPPORTED_LAUNCHER" if wrapper else "ABSENT"), None
        path = Path(executable)
        if (not path.is_absolute() or path.name.lower() != name + ".exe"
                or _fixed_private_metadata_kind(path) != "PRESENT"):
            return "UNAVAILABLE", None
        return "PRESENT", str(path)
    except (OSError, ValueError, TypeError):
        return "UNAVAILABLE", None


def _health_probe(probe, executable):
    """Three fixed argv only. Raw stdout/stderr/exception text never escape.

    The size check bounds accepted responses; subprocess capture is memory-only
    and time-bounded, not a hostile-process output sandbox. No files are written.
    """
    if probe not in _HEALTH_PROBES:
        deny("EXECUTOR_HEALTH_PROBE_DENIED")
    name, args = _HEALTH_PROBES[probe]
    if (type(executable) is not str or not Path(executable).is_absolute()
            or Path(executable).name.lower() != name):
        deny("EXECUTOR_HEALTH_PROBE_DENIED")
    try:
        result = subprocess.run(
            [executable, *args], shell=False, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=_HEALTH_TIMEOUT, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", None
    except OSError:
        return "UNAVAILABLE", None
    # Only exact known responses are projected; never redact-and-forward logs.
    if (type(result.returncode) is not int
            or type(result.stdout) is not bytes or type(result.stderr) is not bytes
            or len(result.stdout) + len(result.stderr) > _HEALTH_LIMIT):
        return "MALFORMED", None
    try:
        stdout, stderr = (part.decode("utf-8").strip() for part in (result.stdout, result.stderr))
    except UnicodeError:
        return "MALFORMED", None
    if stdout and stderr:
        return "MALFORMED", None
    text = stdout or stderr
    if probe == "codex_login":
        if result.returncode == 0 and text in (
                "Logged in using ChatGPT", "Logged in using an API key"):
            return "OBSERVED", "LOGIN_REPORTED"
        if result.returncode == 1 and text == "Not logged in":
            return "OBSERVED", "NOT_LOGGED_IN"
    elif result.returncode == 0:
        prefix = r"codex-cli " if probe == "codex_version" else r"(?:(?:agy|Antigravity CLI) )?"
        match = re.fullmatch(prefix + "(" + _HEALTH_VERSION + ")", text)
        if match:
            return "OBSERVED", match.group(1)
    return "MALFORMED", None


def _health_ef01(report, verifier, now) -> dict[str, EF01HealthObservation]:
    """Optional caller-supplied independent trust boundary; NOT a trust verifier.

    No report discovery or .local scan. A JSON flag, digest alone, agent prose or
    model-defaults-state file cannot establish trust. With no independently
    authenticated EF01 verifier, all supplied reports remain UNTRUSTED.
    """
    unknown = {"state": "UNKNOWN", "outcome": "UNKNOWN", "observed_at": None}
    def both(value):
        return {executor: dict(value) for executor in ("codex", "antigravity")}

    if report is None:
        return both(unknown)
    if type(report) is not bytes or not 0 < len(report) <= 131072:
        return both({**unknown, "state": "INVALID"})
    if verifier is None:
        return both({**unknown, "state": "UNTRUSTED"})
    try:
        if verifier(report) is not True:
            return both({**unknown, "state": "UNTRUSTED"})
        obj = json.loads(report.decode("utf-8"), object_pairs_hook=_object_unique)
        if type(obj) is not dict:
            raise ValueError()
        from ai_commerce.engineering.profile import ExecutorSelection
        ExecutorSelection(obj["executor"], obj["model"], obj["reasoning_effort"])
        executor = obj["executor"]
        calls = obj["invocations"]
        if type(calls) is not list or not calls or type(calls[-1]) is not dict:
            raise ValueError()
        call = calls[-1]
        if call.get("executor_invoked") is not True or type(call.get("executor_exit_code")) is not int:
            raise ValueError()
        # Native EF01 timestamps are ISO-8601 with an explicit UTC offset.
        observed = datetime.fromisoformat(call["executor_finished_at"].replace("Z", "+00:00"))
        started = datetime.fromisoformat(obj["started_at"].replace("Z", "+00:00"))
        if (observed.tzinfo is None or observed.utcoffset() is None
                or started.tzinfo is None or started.utcoffset() is None or started > observed):
            raise ValueError()
        observed = observed.astimezone(timezone.utc)
        age = (now - observed).total_seconds()
        if age < 0:
            raise ValueError()
        quota = obj.get("status") == call.get("status") == "QUOTA_EXHAUSTED"
        if quota:
            outcome = "QUOTA_EXHAUSTED"
        elif (obj.get("status") == call.get("status")
                and obj.get("status") in ("BUILD_VERIFIED_FOR_SCOPE", "VERIFICATION_FAILED", "UNPROVEN")
                and call["executor_exit_code"] == 0
                and call.get("provider_return_determinate") is True):
            outcome = "DETERMINATE_RETURN"
        else:
            return both(unknown)
        return {**both(unknown), executor: {
            "state": "FRESH" if age <= _HEALTH_MAX_AGE else "STALE", "outcome": outcome,
            "observed_at": observed.replace(microsecond=0).isoformat().replace("+00:00", "Z")}}
    except Exception:
        # An independent verifier can fail with private evidence; suppress its
        # text too. Never retry the provider or downgrade to self-attestation.
        return both({**unknown, "state": "INVALID"})


def validate_executor_health_local_receipt(receipt) -> ExecutorHealthLocalReceipt:
    """Strict versioned local contract; this does NOT approve public transport."""
    def require(condition):
        if not condition:
            deny("INVALID_EXECUTOR_HEALTH_LOCAL_RECEIPT")

    require(type(receipt) is dict and set(receipt) == set(ExecutorHealthLocalReceipt.__annotations__))
    require(receipt["schema_version"] == _HEALTH_SCHEMA and receipt["visibility"] == "LOCAL_PRIVATE"
            and receipt["capability"] == "SOURCE_ONLY" and type(receipt["windows"]) is bool
            and receipt["context_identity"] == "UNKNOWN")
    try:
        now = _health_parse_time(receipt["observed_at"])
    except ValueError:
        deny("INVALID_EXECUTOR_HEALTH_LOCAL_RECEIPT")
    defaults = receipt["defaults"]
    require(type(defaults) is dict and set(defaults) == set(ExecutorDefaults.__annotations__))
    require(defaults["state"] in ("VALIDATED", "INVALID", "UNKNOWN"))
    if defaults["state"] == "VALIDATED":
        from ai_commerce.engineering.profile import ExecutorSelection
        for field, executor in (("preferred", "codex"), ("fallback", "antigravity")):
            choice = defaults[field]
            require(type(choice) is dict and set(choice) == {"executor", "model", "effort"})
            try:
                ExecutorSelection(**choice)
            except (ValueError, TypeError):
                deny("INVALID_EXECUTOR_HEALTH_LOCAL_RECEIPT")
            require(choice["executor"] == executor)
        require(type(defaults["recovery_probe_cooldown_seconds"]) is int
                and 60 <= defaults["recovery_probe_cooldown_seconds"] <= 604800)
    else:
        require(all(defaults[key] is None for key in ("preferred", "fallback", "recovery_probe_cooldown_seconds")))
    rows = receipt["executors"]
    require(type(rows) is list and len(rows) == 2)
    for row, executor in zip(rows, ("codex", "antigravity")):
        require(type(row) is dict and set(row) == set(ExecutorHealthRow.__annotations__))
        require(row["executor"] == executor and row["provider_identity"] == row["account"] == row["quota"] == "UNKNOWN"
                and row["remaining"] is None and row["reset_at"] is None)
        require(row["executable"] in ("PRESENT", "ABSENT", "UNAVAILABLE", "UNSUPPORTED_LAUNCHER", "UNKNOWN"))
        require(row["version_state"] in ("OBSERVED", "UNKNOWN", "TIMEOUT", "UNAVAILABLE", "MALFORMED"))
        if row["version_state"] == "OBSERVED":
            require(row["executable"] == "PRESENT" and type(row["version"]) is str
                    and re.fullmatch(_HEALTH_VERSION, row["version"]) is not None)
        else:
            require(row["version"] is None)
        require(row["authentication"] in ("LOGIN_REPORTED", "NOT_LOGGED_IN", "UNKNOWN"))
        require(row["login_probe"] in ("OBSERVED", "UNKNOWN", "TIMEOUT", "UNAVAILABLE", "MALFORMED", "NOT_SUPPORTED"))
        require((row["login_probe"] == "OBSERVED") == (row["authentication"] != "UNKNOWN"))
        if row["login_probe"] == "OBSERVED":
            require(row["executable"] == "PRESENT" and row["version_state"] == "OBSERVED")
        if executor == "antigravity":
            require(row["login_probe"] == "NOT_SUPPORTED" and row["authentication"] == "UNKNOWN")
        else:
            require(row["login_probe"] != "NOT_SUPPORTED")
        if row["executable"] != "PRESENT":
            require(row["version_state"] == "UNKNOWN" and row["authentication"] == "UNKNOWN"
                    and row["login_probe"] == ("UNKNOWN" if executor == "codex" else "NOT_SUPPORTED"))
        if not receipt["windows"]:
            require(row["executable"] == "UNKNOWN" and defaults["state"] == "UNKNOWN")
        last = row["last_ef01"]
        require(type(last) is dict and set(last) == set(EF01HealthObservation.__annotations__))
        require(last["state"] in ("UNKNOWN", "UNTRUSTED", "INVALID", "FRESH", "STALE"))
        if last["state"] in ("FRESH", "STALE"):
            require(last["outcome"] in ("QUOTA_EXHAUSTED", "DETERMINATE_RETURN"))
            try:
                age = (now - _health_parse_time(last["observed_at"])).total_seconds()
            except ValueError:
                deny("INVALID_EXECUTOR_HEALTH_LOCAL_RECEIPT")
            require(age >= 0 and (age <= _HEALTH_MAX_AGE) == (last["state"] == "FRESH"))
        else:
            require(last["outcome"] == "UNKNOWN" and last["observed_at"] is None)
    raw = json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    require(len(raw.encode("ascii")) <= _HEALTH_LIMIT)
    return json.loads(raw)


def collect_executor_health_local(*, now: datetime | None = None, ef01_report: bytes | None = None,
                                  verify_ef01: Callable[[bytes], bool] | None = None) -> ExecutorHealthLocalReceipt:
    """Local observation only, no operation/transport wiring or durable writes.

    No provider/model invocation, auth mutation, profile/home scan, scheduling,
    installation or account identification. Fixed CLI metadata reads run only
    on Windows. Test stubs establish source behavior, never native capability.
    """
    timestamp = now if now is not None else datetime.now(timezone.utc)
    if (type(timestamp) is not datetime or timestamp.tzinfo is None
            or timestamp.utcoffset() is None):
        deny("EXECUTOR_HEALTH_TIME_INVALID")
    timestamp = timestamp.astimezone(timezone.utc).replace(microsecond=0)
    windows = platform.system() == "Windows"
    ef01 = _health_ef01(ef01_report, verify_ef01, timestamp)
    rows = []
    for executor in ("codex", "antigravity"):
        row = {"executor": executor, "executable": "UNKNOWN", "version_state": "UNKNOWN", "version": None,
               "authentication": "UNKNOWN", "login_probe": "UNKNOWN" if executor == "codex" else "NOT_SUPPORTED",
               "provider_identity": "UNKNOWN", "account": "UNKNOWN", "quota": "UNKNOWN", "remaining": None, "reset_at": None,
               "last_ef01": ef01[executor]}
        if windows:
            row["executable"], executable = _health_executable(executor)
            if executable is not None:
                row["version_state"], row["version"] = _health_probe(executor + "_version", executable)
                if executor == "codex" and row["version_state"] == "OBSERVED":
                    row["login_probe"], auth = _health_probe("codex_login", executable)
                    if auth is not None:
                        row["authentication"] = auth
        rows.append(row)
    return validate_executor_health_local_receipt({
        "schema_version": _HEALTH_SCHEMA, "visibility": "LOCAL_PRIVATE", "capability": "SOURCE_ONLY",
        "observed_at": timestamp.isoformat().replace("+00:00", "Z"), "windows": windows,
        "context_identity": "UNKNOWN", "executors": rows,
        "defaults": _executor_defaults() if windows else {"state": "UNKNOWN", "preferred": None,
            "fallback": None, "recovery_probe_cooldown_seconds": None},
    })


# Private source contract only. No public operation, CLI entrypoint, transport,
# account verifier or durable replay store is installed by this implementation.
_PRIVATE_HEALTH_TTL = 60
_PRIVATE_HEALTH_REPLAY_LIMIT = 4096
_PRIVATE_HEALTH_PROVIDERS = ["codex", "antigravity"]
_PRIVATE_HEALTH_FIELDS = ["preferred", "fallback", "executors"]
_PRIVATE_HEALTH_POLICY = {
    "preferred": ({"executor": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"},
                  "CODEX_GPT_6_1_SOL_XHIGH"),
    "fallback": ({"executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"},
                 "ANTIGRAVITY_GEMINI_3_8_FLASH_HIGH"),
}


class ExecutorHealthPrivateRequest(TypedDict):
    schema_version: Literal["ExecutorHealthPrivateRequest.v1"]
    operation: Literal["executor_health_snapshot"]
    visibility: Literal["LOCAL_PRIVATE"]
    origin_id: str
    chat_id: str
    nonce: str
    sequence: int
    issued_at: str
    ttl_seconds: int
    max_age_seconds: int
    providers: list[Literal["codex", "antigravity"]]
    requested_fields: list[Literal["preferred", "fallback", "executors"]]


class ExecutorHealthPrivateRow(ExecutorHealthRow):
    percentage: None


class ExecutorHealthPrivateSnapshot(TypedDict):
    observed_at: str
    windows: bool
    context_identity: Literal["UNKNOWN"]
    preferred: Literal["CODEX_GPT_6_1_SOL_XHIGH", "UNKNOWN"]
    fallback: Literal["ANTIGRAVITY_GEMINI_3_8_FLASH_HIGH", "UNKNOWN"]
    executors: list[ExecutorHealthPrivateRow]


class ExecutorHealthPrivateResponse(TypedDict):
    schema_version: Literal["ExecutorHealthPrivateResponse.v1"]
    visibility: Literal["LOCAL_PRIVATE"]
    capability: Literal["SOURCE_ONLY_NOT_DEPLOYED"]
    request_sha256: str
    responded_at: str
    valid_until: str | None
    max_age_seconds: int
    status: Literal["OBSERVED", "UNAVAILABLE", "UNKNOWN"]
    code: Literal["LOCAL_OBSERVATION_ONLY", "TRUST_UNAVAILABLE", "AUTHORIZATION_UNPROVEN",
                  "BUSY", "REPLAY_CAPACITY", "SOURCE_UNAVAILABLE", "SNAPSHOT_NOT_READY"]
    freshness: Literal["FRESH", "NOT_READY"]
    snapshot: ExecutorHealthPrivateSnapshot | None


def _private_health_utc(value):
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        deny("PRIVATE_HEALTH_TIME_INVALID")
    return value.astimezone(timezone.utc)


def _private_health_stamp(value):
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_executor_health_private_request(raw: bytes) -> ExecutorHealthPrivateRequest:
    """Closed shape only; parsing never authenticates or performs local reads."""
    if type(raw) is not bytes or not 0 < len(raw) <= 4096:
        deny("PRIVATE_HEALTH_REQUEST_INVALID")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_unique)
    except (ValueError, UnicodeError, RecursionError):
        deny("PRIVATE_HEALTH_REQUEST_INVALID")
    if (type(value) is not dict or set(value) != set(ExecutorHealthPrivateRequest.__annotations__)
            or value["schema_version"] != "ExecutorHealthPrivateRequest.v1"
            or value["operation"] != "executor_health_snapshot"
            or value["visibility"] != "LOCAL_PRIVATE"):
        deny("PRIVATE_HEALTH_REQUEST_INVALID")
    for field, size in (("origin_id", 64), ("chat_id", 64), ("nonce", 32)):
        if type(value[field]) is not str or not re.fullmatch("[0-9a-f]{" + str(size) + "}", value[field]):
            deny("PRIVATE_HEALTH_REQUEST_INVALID")
    for field, high in (("sequence", 2**53 - 1), ("ttl_seconds", _PRIVATE_HEALTH_TTL),
                        ("max_age_seconds", _PRIVATE_HEALTH_TTL)):
        if type(value[field]) is not int or not 1 <= value[field] <= high:
            deny("PRIVATE_HEALTH_REQUEST_INVALID")
    for field, expected in (("providers", _PRIVATE_HEALTH_PROVIDERS),
                            ("requested_fields", _PRIVATE_HEALTH_FIELDS)):
        if type(value[field]) is not list or value[field] != expected:
            deny("PRIVATE_HEALTH_REQUEST_INVALID")
    try:
        _health_parse_time(value["issued_at"])
    except ValueError:
        deny("PRIVATE_HEALTH_REQUEST_INVALID")
    return value


def validate_executor_health_private_response(value) -> ExecutorHealthPrivateResponse:
    """Validate the entire private projection, never redact arbitrary fields."""
    def require(condition):
        if not condition:
            deny("PRIVATE_HEALTH_RESPONSE_INVALID")

    require(type(value) is dict and set(value) == set(ExecutorHealthPrivateResponse.__annotations__))
    require(value["schema_version"] == "ExecutorHealthPrivateResponse.v1"
            and value["visibility"] == "LOCAL_PRIVATE" and value["capability"] == "SOURCE_ONLY_NOT_DEPLOYED")
    require(type(value["request_sha256"]) is str and re.fullmatch("[0-9a-f]{64}", value["request_sha256"]) is not None)
    require(type(value["max_age_seconds"]) is int and 1 <= value["max_age_seconds"] <= _PRIVATE_HEALTH_TTL)
    try:
        responded = _health_parse_time(value["responded_at"])
        if value["status"] == "OBSERVED":
            require(value["code"] == "LOCAL_OBSERVATION_ONLY" and value["freshness"] == "FRESH")
            snapshot = value["snapshot"]
            require(type(snapshot) is dict and set(snapshot) == set(ExecutorHealthPrivateSnapshot.__annotations__))
            require(snapshot["context_identity"] == "UNKNOWN")
            for field, (_, code) in _PRIVATE_HEALTH_POLICY.items():
                require(snapshot[field] in (code, "UNKNOWN"))
            rows = snapshot["executors"]
            require(type(rows) is list and len(rows) == 2)
            for row in rows:
                require(type(row) is dict and set(row) == set(ExecutorHealthRow.__annotations__) | {"percentage"}
                        and row["percentage"] is None)
            # Reuse the existing strict row validator; policy is exposed only
            # through the two closed enums above, never raw model selectors.
            local = validate_executor_health_local_receipt({
                "schema_version": _HEALTH_SCHEMA, "visibility": "LOCAL_PRIVATE", "capability": "SOURCE_ONLY",
                "observed_at": snapshot["observed_at"], "windows": snapshot["windows"],
                "context_identity": snapshot["context_identity"],
                "defaults": {"state": "UNKNOWN", "preferred": None, "fallback": None,
                             "recovery_probe_cooldown_seconds": None},
                "executors": [{k: v for k, v in row.items() if k != "percentage"} for row in rows],
            })
            require(local["windows"] is True)
            observed = _health_parse_time(snapshot["observed_at"])
            until = _health_parse_time(value["valid_until"])
            require(0 <= (responded - observed).total_seconds() < value["max_age_seconds"]
                    and responded < until <= observed + timedelta(seconds=value["max_age_seconds"]))
        else:
            require((value["status"] == "UNAVAILABLE" and value["code"] in (
                "TRUST_UNAVAILABLE", "AUTHORIZATION_UNPROVEN", "BUSY", "REPLAY_CAPACITY", "SOURCE_UNAVAILABLE"))
                or (value["status"] == "UNKNOWN" and value["code"] == "SNAPSHOT_NOT_READY"))
            require(value["freshness"] == "NOT_READY" and value["snapshot"] is None and value["valid_until"] is None)
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        require(len(raw) <= 16384)
        return json.loads(raw)
    except (Denied, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        deny("PRIVATE_HEALTH_RESPONSE_INVALID")


class ExecutorHealthPrivateBridge:
    """Transport-agnostic source adapter with in-memory, fail-closed admission.

    Trusted local code pins the exact origin/current chat and supplies an
    independent authorization verifier over canonical request bytes. A request
    flag cannot establish trust. One instance must cover an entire trust session;
    restart-safe replay/signatures and installed transport remain future work.
    """

    def __init__(self, *, origin_id: str | None = None, chat_id: str | None = None,
                 authorize: Callable[[bytes], bool] | None = None,
                 clock: Callable[[], datetime] | None = None):
        for value in (origin_id, chat_id):
            if value is not None and (type(value) is not str or not re.fullmatch("[0-9a-f]{64}", value)):
                deny("PRIVATE_HEALTH_ORIGIN_INVALID")
        self._origin_id, self._chat_id, self._authorize = origin_id, chat_id, authorize
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self._lock = Lock()
        self._nonces: set[str] = set()
        self._sequence = 0
        self._issued_at: datetime | None = None
        self._last_now: datetime | None = None

    def read(self, raw: bytes) -> ExecutorHealthPrivateResponse:
        request = parse_executor_health_private_request(raw)
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        now = _private_health_utc(self._clock())
        issued = _health_parse_time(request["issued_at"])
        try:
            expires = issued + timedelta(seconds=request["ttl_seconds"])
        except OverflowError:
            deny("PRIVATE_HEALTH_REQUEST_TIME_DENIED")
        if not issued <= now < expires:
            deny("PRIVATE_HEALTH_REQUEST_TIME_DENIED")

        def response(status, code, *, at=now, snapshot=None, until=None):
            return validate_executor_health_private_response({
                "schema_version": "ExecutorHealthPrivateResponse.v1", "visibility": "LOCAL_PRIVATE",
                "capability": "SOURCE_ONLY_NOT_DEPLOYED", "request_sha256": hashlib.sha256(canonical).hexdigest(),
                "responded_at": _private_health_stamp(at), "valid_until": _private_health_stamp(until) if until else None,
                "max_age_seconds": request["max_age_seconds"], "status": status, "code": code,
                "freshness": "FRESH" if status == "OBSERVED" else "NOT_READY", "snapshot": snapshot,
            })

        if self._origin_id is None or self._chat_id is None or self._authorize is None:
            return response("UNAVAILABLE", "TRUST_UNAVAILABLE")
        if request["origin_id"] != self._origin_id or request["chat_id"] != self._chat_id:
            deny("PRIVATE_HEALTH_ORIGIN_DENIED")
        if not self._lock.acquire(blocking=False):
            return response("UNAVAILABLE", "BUSY")
        try:
            if (request["nonce"] in self._nonces or request["sequence"] <= self._sequence
                    or (self._issued_at is not None and issued < self._issued_at)
                    or (self._last_now is not None and now < self._last_now)):
                deny("PRIVATE_HEALTH_REPLAY_DENIED")
            if len(self._nonces) >= _PRIVATE_HEALTH_REPLAY_LIMIT:
                return response("UNAVAILABLE", "REPLAY_CAPACITY")
            try:
                authorized = self._authorize(canonical) is True
            except Exception:
                authorized = False
            if not authorized:
                return response("UNAVAILABLE", "AUTHORIZATION_UNPROVEN")
            # Authorization may take time. Recheck expiry/clock BEFORE reads.
            admitted = _private_health_utc(self._clock())
            if not now <= admitted < expires:
                deny("PRIVATE_HEALTH_REQUEST_TIME_DENIED")
            # Consume before the collector, including failures. No retries,
            # nonce eviction, background tasks or extra diagnostic probes.
            self._nonces.add(request["nonce"])
            self._sequence, self._issued_at, self._last_now = request["sequence"], issued, admitted
            try:
                local = validate_executor_health_local_receipt(collect_executor_health_local(now=admitted))
            except Exception:
                return response("UNAVAILABLE", "SOURCE_UNAVAILABLE", at=admitted)
            completed = _private_health_utc(self._clock())
            self._last_now = max(admitted, completed)
            observed = _health_parse_time(local["observed_at"])
            if completed < admitted or observed != admitted.replace(microsecond=0):
                return response("UNKNOWN", "SNAPSHOT_NOT_READY", at=completed)
            try:
                until = min(expires, observed + timedelta(seconds=request["max_age_seconds"]))
            except OverflowError:
                return response("UNKNOWN", "SNAPSHOT_NOT_READY", at=completed)
            if not completed < until:
                return response("UNKNOWN", "SNAPSHOT_NOT_READY", at=completed)
            if not local["windows"]:
                return response("UNAVAILABLE", "SOURCE_UNAVAILABLE", at=completed)
            snapshot = {key: local[key] for key in ("observed_at", "windows", "context_identity")}
            for field, (selection, code) in _PRIVATE_HEALTH_POLICY.items():
                snapshot[field] = (code if local["defaults"]["state"] == "VALIDATED"
                                   and local["defaults"][field] == selection else "UNKNOWN")
            snapshot["executors"] = [{**row, "percentage": None} for row in local["executors"]]
            return response("OBSERVED", "LOCAL_OBSERVATION_ONLY", at=completed, snapshot=snapshot, until=until)
        finally:
            self._lock.release()


# ---------------------------------------------------------------------------
# HOSTOPS_EXEC_V1: Source-only generic PowerShell execution contract & one-time
# enrollment backend. DISABLED by default and NOT wired into legacy READ/WRITE
# parse_event/perform/main or .github/workflows/issue-ingress.yml.
#
# Authority model (explicitly two-tier; no naive shell string denylist):
# - Tier 1 (LOCAL_USER_TOKEN): ordinary non-elevated interactive Windows user
#   PowerShell after explicit one-time owner enrollment.
# - Tier 2 (EXTERNAL_SENSITIVE_GOVERNANCE): irreversible/provider/financial/
#   credential/privilege effects remain governed outside this broker.
# A PowerShell text denylist CANNOT enforce Tier 1 vs Tier 2 because arbitrary
# PowerShell trivially bypasses substring filters (encoding, aliases, reflection,
# .NET, splatting). Real boundaries are the non-elevated Windows OS token,
# authenticated origin, interactive owner session, and private channel isolation.
# ---------------------------------------------------------------------------
HOSTOPS_EXEC_V1_SCHEMA = "HOSTOPS_EXEC_V1"
HOSTOPS_EXEC_V1_ENROLLMENT_SCHEMA = "HOSTOPS_EXEC_V1_ENROLLMENT"
HOSTOPS_EXEC_V1_RECEIPT_SCHEMA = "HOSTOPS_EXEC_V1_RECEIPT"
HOSTOPS_EXEC_V1_TITLE = "[HOSTOPS EXEC] "
HOSTOPS_EXEC_V1_RUNNER_NAME = "AI-COMMERCE-HOSTOPS-01"
HOSTOPS_EXEC_V1_WORKFLOW_REF = REPO + "/.github/workflows/issue-ingress.yml@refs/heads/main"
HOSTOPS_EXEC_V1_ENABLED_BY_DEFAULT = False
HOSTOPS_EXEC_V1_DENYLIST_IS_SECURITY_BOUNDARY = False
HOSTOPS_EXEC_V1_AUTHORITY_TIER_LOCAL = "LOCAL_USER_TOKEN"
HOSTOPS_EXEC_V1_AUTHORITY_TIER_EXTERNAL = "EXTERNAL_SENSITIVE_GOVERNANCE"
HOSTOPS_EXEC_V1_INTENDED_SESSION = "INTERACTIVE_USER_NON_ELEVATED"
HOSTOPS_EXEC_V1_PAYLOAD_MODES = frozenset({"PRIVATE_DIGEST", "PRIVATE_INLINE_UTF8"})
HOSTOPS_EXEC_V1_MAX_EVENT_BYTES = 16384
HOSTOPS_EXEC_V1_MAX_SCRIPT_BYTES = 8192
HOSTOPS_EXEC_V1_MIN_TIMEOUT_SECONDS = 1
HOSTOPS_EXEC_V1_MAX_TIMEOUT_SECONDS = 120
HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES = 65536
HOSTOPS_EXEC_V1_JOURNAL_MAX_RECORDS = 2048
HOSTOPS_EXEC_V1_ENROLLMENT_FILENAME = "hostops-exec-v1-enrollment.json"
HOSTOPS_EXEC_V1_JOURNAL_FILENAME = "hostops-exec-v1-journal.sqlite3"
HOSTOPS_EXEC_V1_PAYLOAD_DIRNAME = "payloads"
_EXEC_V1_KEY_FILENAME = "owner.key"
_EXEC_V1_REVOKED_FILENAME = "revoked"
_EXEC_V1_STATE_DIRNAME = "AI-Commerce-HostOps-Exec-V1"
_EXEC_V1_RUNNER_ROOT = Path("C:/TOOL/ai-commerce-runner-hostops-01")
_EXEC_V1_SOURCE_PATH = Path("src/ai_commerce/hostops_v3/personal.py")
_EXEC_V1_SOURCE_LIMIT = 512 * 1024
_EXEC_V1_REVOKED_JOURNAL_VERSION = -1
_EXEC_V1_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
_EXEC_V1_WORKFLOW_ENVIRONMENT = "hostops-exec-v1"

HOSTOPS_EXEC_V1_STATUSES = frozenset({
    "PLANNED", "DISABLED", "ENROLLED", "REVOKED", "STAGED", "EXECUTED", "REJECTED", "UNKNOWN_EFFECT",
})
HOSTOPS_EXEC_V1_CODES = frozenset({
    "READY_TO_EXECUTE",
    "BLOCKED_NOT_ENROLLED",
    "ENROLLMENT_DISABLED",
    "ENROLLMENT_REVOKED",
    "ENROLLMENT_INVALID",
    "ENROLLMENT_RECORDED",
    "ALREADY_ENROLLED_NO_OVERWRITE",
    "ATTENDED_OWNER_REQUIRED",
    "PRIVATE_STATE_UNSAFE",
    "PAYLOAD_STAGED",
    "PAYLOAD_ALREADY_STAGED",
    "SOURCE_REVISION_MISMATCH",
    "SOURCE_ROTATION_RECORDED",
    "SOURCE_ALREADY_APPROVED",
    "RECEIPT_OUTPUT_UNAVAILABLE",
    "PROTECTED_WORKFLOW_DISPATCH_DISABLED",
    "EVENT_OWNER_OR_REPO_DENIED",
    "EVENT_SHAPE_OR_STATE_DENIED",
    "PUBLIC_ISSUE_RAW_SCRIPT_DENIED",
    "REQUEST_INVALID",
    "PAYLOAD_OVERSIZED_OR_INVALID",
    "PAYLOAD_DIGEST_MISMATCH",
    "REQUEST_DIGEST_MISMATCH",
    "AUTHORITY_TIER_DENIED",
    "AUTHENTICATION_UNPROVEN",
    "PRIVATE_PAYLOAD_UNAVAILABLE",
    "DUPLICATE_JOB_ID",
    "REPLAY_DENIED",
    "JOURNAL_CAPACITY_EXCEEDED",
    "JOURNAL_UNAVAILABLE",
    "ENROLLMENT_PIN_MISMATCH",
    "WINDOWS_ONLY",
    "SOURCE_CI_EXECUTION_FORBIDDEN",
    "WRONG_RUNNER_FORBIDDEN",
    "WRONG_SESSION_OR_ELEVATED",
    "OWNER_SID_MISMATCH",
    "REPARSE_PATH_DENIED",
    "PROTECTED_HOSTOPS_ONLY",
    "CANCELLED_BEFORE_EXECUTION",
    "EXECUTION_COMPLETED",
    "EXECUTION_NONZERO_EXIT",
    "EXECUTION_CANCELLED_OR_TIMED_OUT_UNKNOWN_EFFECT",
    "READBACK_UNVERIFIED_UNKNOWN_EFFECT",
    "EXECUTION_ERROR_UNKNOWN_EFFECT",
    "UNKNOWN_EFFECT_HOLD",
})
HOSTOPS_EXEC_V1_PUBLIC_RECEIPT_FIELDS = frozenset({
    "schema", "status", "code", "job_id", "request_digest", "script_sha256",
    "authority_tier", "payload_mode", "enrolled", "executed", "cancelled",
    "readback_verified", "exit_code", "stdout_sha256", "stderr_sha256",
    "output_bytes", "output_truncated", "denylist_security_claim",
    "public_script_echoed", "public_output_echoed", "elevated_token",
    "source_sha", "issue_number", "recorded_at",
})
_EXEC_V1_REQUEST_FIELDS = frozenset({
    "schema", "job_id", "payload_mode", "script_sha256", "script_utf8",
    "timeout_seconds", "intended_owner", "intended_session", "authority_tier",
    "nonce", "request_digest",
})
_EXEC_V1_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}\Z")
_EXEC_V1_HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
_EXEC_V1_HEX40_RE = re.compile(r"[0-9a-f]{40}\Z")
_EXEC_V1_NONCE_RE = re.compile(r"[0-9a-f]{32,64}\Z")
_EXEC_V1_USER_SID_RE = re.compile(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+\Z")


def _exec_v1_canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _exec_v1_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exec_v1_stamp(now: datetime | None = None) -> str:
    value = now if now is not None else datetime.now(timezone.utc)
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        deny("REQUEST_INVALID")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _exec_v1_no_reparse(path: Path) -> bool:
    if not path.is_absolute():
        return False
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            return False
    return True


def build_hostops_exec_v1_request(
    *,
    job_id: str,
    nonce: str,
    timeout_seconds: int = 30,
    script_utf8: str | None = None,
    script_sha256: str | None = None,
    payload_mode: str = "PRIVATE_DIGEST",
    intended_owner: str = OWNER,
    intended_session: str = HOSTOPS_EXEC_V1_INTENDED_SESSION,
    authority_tier: str = HOSTOPS_EXEC_V1_AUTHORITY_TIER_LOCAL,
) -> dict:
    """Build a canonical digest-bound HOSTOPS_EXEC_V1 request object."""
    if payload_mode == "PRIVATE_INLINE_UTF8":
        if type(script_utf8) is not str:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        digest = _exec_v1_sha256(script_utf8.encode("utf-8")) if script_sha256 is None else script_sha256
    elif payload_mode == "PRIVATE_DIGEST":
        if script_utf8 is not None or type(script_sha256) is not str:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        digest = script_sha256
    else:
        deny("REQUEST_INVALID")
    unsigned = {
        "schema": HOSTOPS_EXEC_V1_SCHEMA,
        "job_id": job_id,
        "payload_mode": payload_mode,
        "script_sha256": digest,
        "script_utf8": script_utf8,
        "timeout_seconds": timeout_seconds,
        "intended_owner": intended_owner,
        "intended_session": intended_session,
        "authority_tier": authority_tier,
        "nonce": nonce,
    }
    request = {**unsigned, "request_digest": _exec_v1_sha256(_exec_v1_canonical(unsigned))}
    return parse_hostops_exec_v1_request(request, public_issue=(payload_mode == "PRIVATE_DIGEST"))


def parse_hostops_exec_v1_request(raw, *, public_issue: bool = False) -> dict:
    """Validate bounded HOSTOPS_EXEC_V1 request shape and digests; never executes."""
    if type(raw) is bytes:
        if not 0 < len(raw) <= HOSTOPS_EXEC_V1_MAX_EVENT_BYTES:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        try:
            obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_unique)
        except (ValueError, UnicodeError, RecursionError):
            deny("REQUEST_INVALID")
    elif type(raw) is str:
        try:
            encoded = raw.encode("utf-8")
        except UnicodeError:
            deny("REQUEST_INVALID")
        if not 0 < len(encoded) <= HOSTOPS_EXEC_V1_MAX_EVENT_BYTES:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        try:
            obj = json.loads(raw, object_pairs_hook=_object_unique)
        except (ValueError, UnicodeError, RecursionError):
            deny("REQUEST_INVALID")
    elif type(raw) is dict:
        try:
            encoded = _exec_v1_canonical(raw)
        except (ValueError, TypeError, OverflowError, RecursionError):
            deny("REQUEST_INVALID")
        if not 0 < len(encoded) <= HOSTOPS_EXEC_V1_MAX_EVENT_BYTES:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        obj = json.loads(encoded.decode("ascii"), object_pairs_hook=_object_unique)
    else:
        deny("REQUEST_INVALID")

    if type(obj) is not dict or set(obj) != _EXEC_V1_REQUEST_FIELDS:
        deny("REQUEST_INVALID")
    if obj["schema"] != HOSTOPS_EXEC_V1_SCHEMA:
        deny("REQUEST_INVALID")
    if type(obj["job_id"]) is not str or _EXEC_V1_JOB_ID_RE.fullmatch(obj["job_id"]) is None:
        deny("REQUEST_INVALID")
    if type(obj["nonce"]) is not str or _EXEC_V1_NONCE_RE.fullmatch(obj["nonce"]) is None:
        deny("REQUEST_INVALID")
    if type(obj["script_sha256"]) is not str or _EXEC_V1_HEX64_RE.fullmatch(obj["script_sha256"]) is None:
        deny("REQUEST_INVALID")
    if type(obj["request_digest"]) is not str or _EXEC_V1_HEX64_RE.fullmatch(obj["request_digest"]) is None:
        deny("REQUEST_INVALID")
    if (type(obj["timeout_seconds"]) is not int
            or not HOSTOPS_EXEC_V1_MIN_TIMEOUT_SECONDS <= obj["timeout_seconds"] <= HOSTOPS_EXEC_V1_MAX_TIMEOUT_SECONDS):
        deny("REQUEST_INVALID")
    if obj["intended_owner"] != OWNER:
        deny("EVENT_OWNER_OR_REPO_DENIED")
    if obj["intended_session"] != HOSTOPS_EXEC_V1_INTENDED_SESSION:
        deny("WRONG_SESSION_OR_ELEVATED")
    if obj["authority_tier"] != HOSTOPS_EXEC_V1_AUTHORITY_TIER_LOCAL:
        deny("AUTHORITY_TIER_DENIED")
    mode = obj["payload_mode"]
    if type(mode) is not str or mode not in HOSTOPS_EXEC_V1_PAYLOAD_MODES:
        deny("REQUEST_INVALID")
    script = obj["script_utf8"]
    if public_issue and (mode != "PRIVATE_DIGEST" or script is not None):
        deny("PUBLIC_ISSUE_RAW_SCRIPT_DENIED")
    if mode == "PRIVATE_DIGEST":
        if script is not None:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
    else:
        if type(script) is not str or "\x00" in script:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        try:
            script_bytes = script.encode("utf-8")
        except UnicodeError:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        if not 0 < len(script_bytes) <= HOSTOPS_EXEC_V1_MAX_SCRIPT_BYTES:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        if not hmac.compare_digest(_exec_v1_sha256(script_bytes), obj["script_sha256"]):
            deny("PAYLOAD_DIGEST_MISMATCH")
    unsigned = {k: v for k, v in obj.items() if k != "request_digest"}
    if not hmac.compare_digest(obj["request_digest"], _exec_v1_sha256(_exec_v1_canonical(unsigned))):
        deny("REQUEST_DIGEST_MISMATCH")
    return obj


def parse_hostops_exec_v1_event(event) -> dict:
    """Validate owner-authored Issue event for a future protected HOSTOPS_EXEC_V1 workflow.

    Public repository Issues may NEVER carry raw PowerShell script text; they must
    use payload_mode='PRIVATE_DIGEST' bound to an enrolled local private payload.
    """
    if type(event) is not dict:
        deny("EVENT_SHAPE_OR_STATE_DENIED")
    repo = event.get("repository")
    if (type(repo) is not dict or repo.get("full_name") != REPO
            or type(repo.get("private")) is not bool
            or type(repo.get("owner")) is not dict
            or repo["owner"].get("login") != OWNER):
        deny("EVENT_OWNER_OR_REPO_DENIED")
    sender = event.get("sender")
    if type(sender) is not dict or sender.get("login") != OWNER:
        deny("EVENT_OWNER_OR_REPO_DENIED")
    issue = event.get("issue")
    if type(issue) is not dict or "pull_request" in issue:
        deny("EVENT_SHAPE_OR_STATE_DENIED")
    issue_user = issue.get("user")
    number = issue.get("number")
    title = issue.get("title")
    if type(issue_user) is not dict or issue_user.get("login") != OWNER:
        deny("EVENT_OWNER_OR_REPO_DENIED")
    if (event.get("action") != "opened" or issue.get("state") != "open"
            or type(number) is not int or number < 1
            or type(title) is not str or not title.startswith(HOSTOPS_EXEC_V1_TITLE)):
        deny("EVENT_SHAPE_OR_STATE_DENIED")
    body = issue.get("body")
    if type(body) is not str:
        deny("EVENT_SHAPE_OR_STATE_DENIED")
    public_issue = repo["private"] is False
    request = parse_hostops_exec_v1_request(body, public_issue=public_issue)
    return {"issue_number": number, "public_issue": public_issue, "request": request}


def validate_hostops_exec_v1_receipt(receipt) -> dict:
    """Strict public-safe receipt validator; forbids raw scripts, output or paths."""
    def require(condition):
        if not condition:
            deny("REQUEST_INVALID")

    require(type(receipt) is dict and set(receipt) == HOSTOPS_EXEC_V1_PUBLIC_RECEIPT_FIELDS)
    require(receipt["schema"] == HOSTOPS_EXEC_V1_RECEIPT_SCHEMA)
    require(type(receipt["status"]) is str and receipt["status"] in HOSTOPS_EXEC_V1_STATUSES)
    require(type(receipt["code"]) is str and receipt["code"] in HOSTOPS_EXEC_V1_CODES)
    require(receipt["authority_tier"] == HOSTOPS_EXEC_V1_AUTHORITY_TIER_LOCAL)
    require(receipt["denylist_security_claim"] is False)
    require(receipt["public_script_echoed"] is False)
    require(receipt["public_output_echoed"] is False)
    require(receipt["elevated_token"] is False)
    for field in ("enrolled", "executed", "cancelled", "readback_verified", "output_truncated"):
        require(type(receipt[field]) is bool)
    require(type(receipt["output_bytes"]) is int
            and 0 <= receipt["output_bytes"] <= HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES)
    if receipt["job_id"] is not None:
        require(type(receipt["job_id"]) is str and _EXEC_V1_JOB_ID_RE.fullmatch(receipt["job_id"]) is not None)
    for field in ("request_digest", "script_sha256"):
        if receipt[field] is not None:
            require(type(receipt[field]) is str and _EXEC_V1_HEX64_RE.fullmatch(receipt[field]) is not None)
    if receipt["payload_mode"] is not None:
        require(type(receipt["payload_mode"]) is str and receipt["payload_mode"] in HOSTOPS_EXEC_V1_PAYLOAD_MODES)
    if receipt["source_sha"] is not None:
        require(type(receipt["source_sha"]) is str and _EXEC_V1_HEX40_RE.fullmatch(receipt["source_sha"]) is not None)
    if receipt["issue_number"] is not None:
        require(type(receipt["issue_number"]) is int and receipt["issue_number"] >= 1)
    try:
        _health_parse_time(receipt["recorded_at"])
    except ValueError:
        deny("REQUEST_INVALID")
    if receipt["status"] == "EXECUTED":
        require(receipt["executed"] is True and receipt["enrolled"] is True
                and receipt["readback_verified"] is True and receipt["cancelled"] is False
                and type(receipt["exit_code"]) is int
                and type(receipt["stdout_sha256"]) is str and _EXEC_V1_HEX64_RE.fullmatch(receipt["stdout_sha256"]) is not None
                and type(receipt["stderr_sha256"]) is str and _EXEC_V1_HEX64_RE.fullmatch(receipt["stderr_sha256"]) is not None)
    else:
        require(receipt["executed"] is False and receipt["exit_code"] is None
                and receipt["stdout_sha256"] is None and receipt["stderr_sha256"] is None)
    encoded = _exec_v1_canonical(receipt)
    require(len(encoded) <= 4096)
    return json.loads(encoded.decode("ascii"))


HostOpsExecV1RuntimeContext = namedtuple(
    "HostOpsExecV1RuntimeContext",
    ("os_name", "runner_name", "session_id", "elevated", "owner_sid", "env"),
)

HostOpsExecV1ProcessResult = namedtuple(
    "HostOpsExecV1ProcessResult",
    ("exit_code", "stdout", "stderr", "output_truncated"),
    defaults=(False,),
)

_HostOpsExecV1EnrollmentBase = namedtuple(
    "_HostOpsExecV1EnrollmentBase",
    (
        "enrollment_id", "owner_login", "owner_sid", "runner_name",
        "repository", "source_sha", "state_root", "journal_path",
        "payload_root", "auth_key_sha256", "enrolled_at",
        "enabled", "revoked", "require_private_payload",
    ),
    defaults=(HOSTOPS_EXEC_V1_ENABLED_BY_DEFAULT, False, True),
)


class HostOpsExecV1Enrollment(_HostOpsExecV1EnrollmentBase):
    __slots__ = ()

    def validate(self) -> None:
        if (type(self.enrollment_id) is not str or _EXEC_V1_JOB_ID_RE.fullmatch(self.enrollment_id) is None
                or self.owner_login != OWNER
                or type(self.owner_sid) is not str or _EXEC_V1_USER_SID_RE.fullmatch(self.owner_sid) is None
                or self.runner_name != HOSTOPS_EXEC_V1_RUNNER_NAME
                or self.repository != REPO
                or type(self.source_sha) is not str or _EXEC_V1_HEX40_RE.fullmatch(self.source_sha) is None
                or type(self.auth_key_sha256) is not str or _EXEC_V1_HEX64_RE.fullmatch(self.auth_key_sha256) is None
                or type(self.enabled) is not bool or type(self.revoked) is not bool
                or type(self.require_private_payload) is not bool):
            deny("ENROLLMENT_INVALID")
        try:
            _health_parse_time(self.enrolled_at)
        except ValueError:
            deny("ENROLLMENT_INVALID")
        for path in (self.state_root, self.journal_path, self.payload_root):
            if not isinstance(path, Path) or not _exec_v1_no_reparse(path):
                deny("REPARSE_PATH_DENIED")
        root_norm = Path(normpath(self.state_root))
        if (root_norm not in Path(normpath(self.journal_path)).parents
                or root_norm not in Path(normpath(self.payload_root)).parents):
            deny("ENROLLMENT_INVALID")
        if self.revoked:
            deny("ENROLLMENT_REVOKED")
        if not self.enabled:
            deny("ENROLLMENT_DISABLED")


def _exec_v1_source_ci(env: Mapping[str, str]) -> bool:
    return (any(key.upper().startswith("AI_COMMERCE_GIT_GUARD_") for key in env)
            or "PYTEST_CURRENT_TEST" in env or "pytest" in sys.modules
            or (env.get("CI", "").lower() == "true" and env.get("GITHUB_ACTIONS") != "true")
            or env.get("RUNNER_NAME", "").upper().startswith("AI-COMMERCE-EF02"))


def _verify_hostops_exec_v1_runtime(
    ctx: HostOpsExecV1RuntimeContext,
    *,
    expected_owner_sid: str | None = None,
    require_protected_workflow: bool = False,
    forbid_github_actions: bool = False,
) -> None:
    if not isinstance(ctx, HostOpsExecV1RuntimeContext):
        deny("PROTECTED_HOSTOPS_ONLY")
    if ctx.os_name != "nt":
        deny("WINDOWS_ONLY")
    if (any(key.upper().startswith("AI_COMMERCE_GIT_GUARD_") for key in ctx.env)
            or "PYTEST_CURRENT_TEST" in ctx.env):
        deny("SOURCE_CI_EXECUTION_FORBIDDEN")
    if ctx.runner_name != HOSTOPS_EXEC_V1_RUNNER_NAME:
        deny("WRONG_RUNNER_FORBIDDEN")
    in_actions = ctx.env.get("GITHUB_ACTIONS") == "true"
    if forbid_github_actions and "GITHUB_ACTIONS" in ctx.env:
        deny("SOURCE_CI_EXECUTION_FORBIDDEN")
    if in_actions:
        expected = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "issues",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_REPOSITORY_OWNER": OWNER,
            "GITHUB_ACTOR": OWNER,
            "GITHUB_TRIGGERING_ACTOR": OWNER,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_REF": HOSTOPS_EXEC_V1_WORKFLOW_REF,
            "RUNNER_ENVIRONMENT": "self-hosted",
            "RUNNER_NAME": HOSTOPS_EXEC_V1_RUNNER_NAME,
        }
        sha = ctx.env.get("GITHUB_SHA", "")
        if (any(ctx.env.get(k) != v for k, v in expected.items())
                or _EXEC_V1_HEX40_RE.fullmatch(sha) is None
                or ctx.env.get("GITHUB_WORKFLOW_SHA") != sha):
            deny("PROTECTED_HOSTOPS_ONLY")
        if ctx.env.get("HOSTOPS_EXEC_V1_PROTECTED_DISPATCH") != "ENABLED":
            deny("PROTECTED_WORKFLOW_DISPATCH_DISABLED")
    elif require_protected_workflow:
        deny("PROTECTED_HOSTOPS_ONLY")
    if type(ctx.session_id) is not int or ctx.session_id <= 0 or ctx.elevated is not False:
        deny("WRONG_SESSION_OR_ELEVATED")
    if type(ctx.owner_sid) is not str or _EXEC_V1_USER_SID_RE.fullmatch(ctx.owner_sid) is None:
        deny("OWNER_SID_MISMATCH")
    if expected_owner_sid is not None and ctx.owner_sid != expected_owner_sid:
        deny("OWNER_SID_MISMATCH")


def sign_hostops_exec_v1_request(request: dict, key: bytes) -> str:
    """Private owner-side HMAC-SHA256 signer over canonical validated request bytes."""
    if type(key) is not bytes or len(key) < 32:
        deny("AUTHENTICATION_UNPROVEN")
    validated = parse_hostops_exec_v1_request(request, public_issue=False)
    return hmac.new(key, _exec_v1_canonical(validated), "sha256").hexdigest()


def make_hostops_exec_v1_hmac_authenticator(key: bytes) -> Callable[[bytes, str | None, HostOpsExecV1Enrollment], bool]:
    """Return a fail-closed HMAC authenticator bound to enrollment.auth_key_sha256."""
    if type(key) is not bytes or len(key) < 32:
        deny("AUTHENTICATION_UNPROVEN")
    key_digest = _exec_v1_sha256(key)

    def verify(canonical_request: bytes, auth_token: str | None, enrollment: HostOpsExecV1Enrollment) -> bool:
        if (type(canonical_request) is not bytes or type(auth_token) is not str
                or _EXEC_V1_HEX64_RE.fullmatch(auth_token) is None
                or not hmac.compare_digest(key_digest, enrollment.auth_key_sha256)):
            return False
        expected = hmac.new(key, canonical_request, "sha256").hexdigest()
        return hmac.compare_digest(auth_token, expected)

    return verify


def enroll_hostops_exec_v1(
    *,
    enrollment_id: str,
    source_sha: str,
    state_root: Path,
    auth_key: bytes,
    runtime: HostOpsExecV1RuntimeContext,
    require_private_payload: bool = True,
    now: datetime | None = None,
) -> tuple[HostOpsExecV1Enrollment, dict]:
    """One-time native interactive owner enrollment. Refuses CI, EF02, Session 0 and elevation."""
    _verify_hostops_exec_v1_runtime(runtime, forbid_github_actions=True)
    if type(auth_key) is not bytes or len(auth_key) < 32:
        deny("ENROLLMENT_INVALID")
    if not isinstance(state_root, Path) or not _exec_v1_no_reparse(state_root):
        deny("REPARSE_PATH_DENIED")
    stamped = _exec_v1_stamp(now)
    journal_path = state_root / HOSTOPS_EXEC_V1_JOURNAL_FILENAME
    payload_root = state_root / HOSTOPS_EXEC_V1_PAYLOAD_DIRNAME
    enrollment = HostOpsExecV1Enrollment(
        enrollment_id=enrollment_id,
        owner_login=OWNER,
        owner_sid=runtime.owner_sid,
        runner_name=HOSTOPS_EXEC_V1_RUNNER_NAME,
        repository=REPO,
        source_sha=source_sha,
        state_root=state_root,
        journal_path=journal_path,
        payload_root=payload_root,
        auth_key_sha256=_exec_v1_sha256(auth_key),
        enrolled_at=stamped,
        enabled=True,
        revoked=False,
        require_private_payload=require_private_payload,
    )
    enrollment.validate()
    state_root.mkdir(parents=True, exist_ok=True)
    payload_root.mkdir(parents=True, exist_ok=True)
    if not _exec_v1_no_reparse(state_root) or not _exec_v1_no_reparse(payload_root):
        deny("REPARSE_PATH_DENIED")
    record = {
        "schema": HOSTOPS_EXEC_V1_ENROLLMENT_SCHEMA,
        "enrollment_id": enrollment.enrollment_id,
        "owner_login": enrollment.owner_login,
        "owner_sid": enrollment.owner_sid,
        "runner_name": enrollment.runner_name,
        "repository": enrollment.repository,
        "source_sha": enrollment.source_sha,
        "state_root": str(enrollment.state_root),
        "journal_path": str(enrollment.journal_path),
        "payload_root": str(enrollment.payload_root),
        "auth_key_sha256": enrollment.auth_key_sha256,
        "enrolled_at": enrollment.enrolled_at,
        "enabled": enrollment.enabled,
        "revoked": enrollment.revoked,
        "require_private_payload": enrollment.require_private_payload,
    }
    enrollment_file = state_root / HOSTOPS_EXEC_V1_ENROLLMENT_FILENAME
    if not _exec_v1_no_reparse(enrollment_file):
        deny("REPARSE_PATH_DENIED")
    raw = _exec_v1_canonical(record) + b"\n"
    try:
        with enrollment_file.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        deny("ALREADY_ENROLLED_NO_OVERWRITE")
    except OSError:
        deny("ENROLLMENT_INVALID")
    if load_hostops_exec_v1_enrollment(enrollment_file) != enrollment:
        deny("ENROLLMENT_INVALID")
    receipt = validate_hostops_exec_v1_receipt({
        "schema": HOSTOPS_EXEC_V1_RECEIPT_SCHEMA,
        "status": "ENROLLED",
        "code": "ENROLLMENT_RECORDED",
        "job_id": None,
        "request_digest": None,
        "script_sha256": None,
        "authority_tier": HOSTOPS_EXEC_V1_AUTHORITY_TIER_LOCAL,
        "payload_mode": None,
        "enrolled": True,
        "executed": False,
        "cancelled": False,
        "readback_verified": True,
        "exit_code": None,
        "stdout_sha256": None,
        "stderr_sha256": None,
        "output_bytes": 0,
        "output_truncated": False,
        "denylist_security_claim": False,
        "public_script_echoed": False,
        "public_output_echoed": False,
        "elevated_token": False,
        "source_sha": enrollment.source_sha,
        "issue_number": None,
        "recorded_at": stamped,
    })
    return enrollment, receipt


def load_hostops_exec_v1_enrollment(path: Path) -> HostOpsExecV1Enrollment | None:
    """Load an operator-owned enrollment file; returns None when absent."""
    if not isinstance(path, Path) or not _exec_v1_no_reparse(path):
        deny("REPARSE_PATH_DENIED")
    if not path.exists():
        return None
    if not path.is_file():
        deny("ENROLLMENT_INVALID")
    try:
        with path.open("rb") as stream:
            raw = stream.read(8193)
        if not 0 < len(raw) <= 8192:
            deny("ENROLLMENT_INVALID")
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_unique)
    except (OSError, ValueError, UnicodeError, RecursionError):
        deny("ENROLLMENT_INVALID")
    expected_keys = {
        "schema", "enrollment_id", "owner_login", "owner_sid", "runner_name",
        "repository", "source_sha", "state_root", "journal_path", "payload_root",
        "auth_key_sha256", "enrolled_at", "enabled", "revoked", "require_private_payload",
    }
    if type(obj) is not dict or set(obj) != expected_keys or obj["schema"] != HOSTOPS_EXEC_V1_ENROLLMENT_SCHEMA:
        deny("ENROLLMENT_INVALID")
    if any(type(obj[k]) is not str for k in ("state_root", "journal_path", "payload_root")):
        deny("ENROLLMENT_INVALID")
    return HostOpsExecV1Enrollment(
        enrollment_id=obj["enrollment_id"],
        owner_login=obj["owner_login"],
        owner_sid=obj["owner_sid"],
        runner_name=obj["runner_name"],
        repository=obj["repository"],
        source_sha=obj["source_sha"],
        state_root=Path(obj["state_root"]),
        journal_path=Path(obj["journal_path"]),
        payload_root=Path(obj["payload_root"]),
        auth_key_sha256=obj["auth_key_sha256"],
        enrolled_at=obj["enrolled_at"],
        enabled=obj["enabled"],
        revoked=obj["revoked"],
        require_private_payload=obj["require_private_payload"],
    )


class HostOpsExecV1Journal:
    """Durable operator-owned SQLite journal; intent before effect, no plaintext secrets."""

    def __init__(
        self, path: Path, *, existing: bool = False,
        native_source: tuple[bytes, tuple[str, str] | None] | None = None,
    ):
        if not isinstance(path, Path) or not _exec_v1_no_reparse(path):
            deny("REPARSE_PATH_DENIED")
        self.path = path
        self._existing = existing
        self._native_source = native_source
        if existing:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not _exec_v1_no_reparse(self.path):
                deny("REPARSE_PATH_DENIED")
            with self._session() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS enrollment_pin ("
                    "id INTEGER PRIMARY KEY CHECK(id=1), "
                    "enrollment_id TEXT NOT NULL, owner_sid TEXT NOT NULL, source_sha TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS jobs ("
                    "job_id TEXT PRIMARY KEY, "
                    "nonce TEXT UNIQUE NOT NULL, "
                    "request_digest TEXT UNIQUE NOT NULL, "
                    "script_sha256 TEXT NOT NULL, "
                    "state TEXT NOT NULL, "
                    "status TEXT NOT NULL, "
                    "code TEXT NOT NULL, "
                    "exit_code INTEGER, "
                    "stdout_sha256 TEXT, "
                    "stderr_sha256 TEXT, "
                    "output_bytes INTEGER NOT NULL, "
                    "output_truncated INTEGER NOT NULL, "
                    "cancelled INTEGER NOT NULL, "
                    "readback_verified INTEGER NOT NULL, "
                    "created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL)"
                )
        except Denied:
            raise
        except (OSError, sqlite3.Error):
            deny("JOURNAL_UNAVAILABLE")

    @contextmanager
    def _session(self):
        location = self.path.as_uri() + "?mode=rw" if self._existing else self.path
        db = sqlite3.connect(location, uri=self._existing, timeout=1, isolation_level="IMMEDIATE")
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def check_ready(self, request: dict, enrollment: HostOpsExecV1Enrollment) -> None:
        """Read-only admission check for plan()."""
        try:
            with self._session() as db:
                db.execute("BEGIN")
                pin = db.execute(
                    "SELECT enrollment_id, owner_sid, source_sha FROM enrollment_pin WHERE id=1"
                ).fetchone()
                if (self._existing or pin is not None) and pin != self._pin(enrollment):
                    deny("ENROLLMENT_PIN_MISMATCH")
                self._check_source_snapshot(db, enrollment)
                if db.execute("SELECT 1 FROM jobs WHERE state IN ('INTENT','UNKNOWN_EFFECT')").fetchone():
                    deny("UNKNOWN_EFFECT_HOLD")
                if db.execute("SELECT 1 FROM jobs WHERE job_id=?", (request["job_id"],)).fetchone():
                    deny("DUPLICATE_JOB_ID")
                if db.execute(
                    "SELECT 1 FROM jobs WHERE nonce=? OR request_digest=?",
                    (request["nonce"], request["request_digest"]),
                ).fetchone():
                    deny("REPLAY_DENIED")
                if db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= HOSTOPS_EXEC_V1_JOURNAL_MAX_RECORDS:
                    deny("JOURNAL_CAPACITY_EXCEEDED")
        except Denied:
            raise
        except sqlite3.Error:
            deny("JOURNAL_UNAVAILABLE")

    def reserve(self, request: dict, enrollment: HostOpsExecV1Enrollment, timestamp: str) -> None:
        """Atomically record durable INTENT before any PowerShell effect."""
        try:
            with self._session() as db:
                db.execute("BEGIN IMMEDIATE")
                pin = db.execute(
                    "SELECT enrollment_id, owner_sid, source_sha FROM enrollment_pin WHERE id=1"
                ).fetchone()
                if pin is None:
                    if self._existing:
                        deny("ENROLLMENT_PIN_MISMATCH")
                    db.execute(
                        "INSERT INTO enrollment_pin VALUES (1,?,?,?)",
                        self._pin(enrollment),
                    )
                elif pin != self._pin(enrollment):
                    deny("ENROLLMENT_PIN_MISMATCH")
                self._check_source_snapshot(db, enrollment)
                if db.execute("SELECT 1 FROM jobs WHERE state IN ('INTENT','UNKNOWN_EFFECT')").fetchone():
                    deny("UNKNOWN_EFFECT_HOLD")
                if db.execute("SELECT 1 FROM jobs WHERE job_id=?", (request["job_id"],)).fetchone():
                    deny("DUPLICATE_JOB_ID")
                if db.execute(
                    "SELECT 1 FROM jobs WHERE nonce=? OR request_digest=?",
                    (request["nonce"], request["request_digest"]),
                ).fetchone():
                    deny("REPLAY_DENIED")
                if db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= HOSTOPS_EXEC_V1_JOURNAL_MAX_RECORDS:
                    deny("JOURNAL_CAPACITY_EXCEEDED")
                db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        request["job_id"], request["nonce"], request["request_digest"],
                        request["script_sha256"], "INTENT", "PLANNED", "READY_TO_EXECUTE",
                        None, None, None, 0, 0, 0, 0, timestamp, timestamp,
                    ),
                )
        except Denied:
            raise
        except sqlite3.Error:
            deny("JOURNAL_UNAVAILABLE")

    @staticmethod
    def _pin(enrollment):
        return enrollment.enrollment_id, enrollment.owner_sid, enrollment.source_sha

    @staticmethod
    def _source_version(db) -> int:
        # Zero is legacy state. A positive version seals the number of approval rows.
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == _EXEC_V1_REVOKED_JOURNAL_VERSION:
            deny("ENROLLMENT_REVOKED")
        if not 0 <= version <= HOSTOPS_EXEC_V1_JOURNAL_MAX_RECORDS:
            deny("JOURNAL_UNAVAILABLE")
        return version

    @classmethod
    def _approval_mac(cls, enrollment, key, seq, sha, digest, previous, stamped):
        record = ["hostops-exec-v1-source-approval", *cls._pin(enrollment),
                  enrollment.auth_key_sha256, enrollment.enrolled_at, seq, sha, digest, previous, stamped]
        return hmac.new(key, _exec_v1_canonical(record), "sha256").hexdigest()

    def _source_history(self, db, enrollment, key) -> tuple[int, tuple[str, str] | None, str]:
        _exec_v1_require_not_revoked(enrollment.state_root)
        version = self._source_version(db)
        if type(key) is not bytes or len(key) != 32 or not hmac.compare_digest(
            _exec_v1_sha256(key), enrollment.auth_key_sha256,
        ):
            deny("AUTHENTICATION_UNPROVEN")
        if db.execute("SELECT enrollment_id, owner_sid, source_sha FROM enrollment_pin").fetchall() != [self._pin(enrollment)]:
            deny("ENROLLMENT_PIN_MISMATCH")
        table = db.execute("SELECT type FROM sqlite_master WHERE name='source_approvals'").fetchone()
        if table is None and version == 0:
            return 0, None, ""
        if table != ("table",) or version == 0:
            deny("JOURNAL_UNAVAILABLE")
        rows = db.execute(
            "SELECT seq, source_sha, source_sha256, approved_at, mac FROM source_approvals ORDER BY seq LIMIT ?",
            (version + 1,),
        ).fetchall()
        if len(rows) != version:
            deny("JOURNAL_UNAVAILABLE")
        previous = ""
        for seq, (stored_seq, sha, digest, stamped, mac) in enumerate(rows, 1):
            if (stored_seq != seq or type(sha) is not str or not _EXEC_V1_HEX40_RE.fullmatch(sha)
                    or type(digest) is not str or not _EXEC_V1_HEX64_RE.fullmatch(digest)
                    or type(stamped) is not str or type(mac) is not str or not _EXEC_V1_HEX64_RE.fullmatch(mac)
                    or not hmac.compare_digest(mac, self._approval_mac(enrollment, key, seq, sha, digest, previous, stamped))):
                deny("JOURNAL_UNAVAILABLE")
            previous = mac
        return version, (sha, digest), previous

    def source_approval(self, enrollment: HostOpsExecV1Enrollment, key: bytes) -> tuple[str, str] | None:
        try:
            with self._session() as db:
                db.execute("BEGIN")
                return self._source_history(db, enrollment, key)[1]
        except sqlite3.Error:
            deny("JOURNAL_UNAVAILABLE")

    def _check_source_snapshot(self, db, enrollment) -> None:
        if self._native_source is None:
            self._source_version(db)
            return
        key, approved = self._native_source
        if self._source_history(db, enrollment, key)[1] != approved:
            deny("SOURCE_REVISION_MISMATCH")

    def verify_source(self, sha: str, enrollment: HostOpsExecV1Enrollment) -> None:
        approved = self._native_source[1] if self._native_source else None
        _exec_v1_verify_source(sha, enrollment_sha=enrollment.source_sha, approved_digest=approved[1] if approved else None)

    def approved_source_sha(self, enrollment: HostOpsExecV1Enrollment) -> str:
        approved = self._native_source[1] if self._native_source else None
        return approved[0] if approved else enrollment.source_sha

    def _append_source_approval(self, db, enrollment, key, version, sha, digest, previous) -> None:
        seq = version + 1
        if seq > HOSTOPS_EXEC_V1_JOURNAL_MAX_RECORDS:
            deny("JOURNAL_CAPACITY_EXCEEDED")
        if version == 0:
            db.execute(
                "CREATE TABLE source_approvals (seq INTEGER PRIMARY KEY, source_sha TEXT NOT NULL, "
                "source_sha256 TEXT NOT NULL, approved_at TEXT NOT NULL, mac TEXT NOT NULL)"
            )
        stamped = _exec_v1_stamp()
        mac = self._approval_mac(enrollment, key, seq, sha, digest, previous, stamped)
        _exec_v1_require_not_revoked(enrollment.state_root)
        db.execute("INSERT INTO source_approvals VALUES (?,?,?,?,?)", (seq, sha, digest, stamped, mac))
        db.execute(f"PRAGMA user_version={seq}")

    def rotate_source(self, enrollment: HostOpsExecV1Enrollment, key: bytes, sha: str) -> str:
        """Append an attended approval while holding the same lock as INTENT."""
        try:
            with self._session() as db:
                db.execute("BEGIN IMMEDIATE")
                version, approved, previous = self._source_history(db, enrollment, key)
                if (db.execute("PRAGMA journal_mode").fetchone() not in (("delete",), ("truncate",), ("persist",), ("wal",))
                        or db.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",)):
                    deny("JOURNAL_UNAVAILABLE")
                if db.execute("SELECT 1 FROM jobs WHERE state IN ('INTENT','UNKNOWN_EFFECT')").fetchone():
                    deny("UNKNOWN_EFFECT_HOLD")
                digest = _exec_v1_verify_source(sha)
                if approved is not None and hmac.compare_digest(digest, approved[1]):
                    result = "SOURCE_ALREADY_APPROVED"
                    expected = approved
                else:
                    # The attended owner can migrate a legacy pin even if its commit is absent.
                    self._append_source_approval(db, enrollment, key, version, sha, digest, previous)
                    result = "SOURCE_ROTATION_RECORDED"
                    expected = (sha, digest)
            if self.source_approval(enrollment, key) != expected:
                deny("JOURNAL_UNAVAILABLE")
            return result
        except sqlite3.Error:
            deny("JOURNAL_UNAVAILABLE")

    def latch_revocation(self) -> None:
        """Keep the revocation authoritative if the compatibility marker goes missing."""
        try:
            with self._session() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(f"PRAGMA user_version={_EXEC_V1_REVOKED_JOURNAL_VERSION}")
            with self._session() as db:
                if db.execute("PRAGMA user_version").fetchone() != (_EXEC_V1_REVOKED_JOURNAL_VERSION,):
                    deny("JOURNAL_UNAVAILABLE")
        except sqlite3.Error:
            deny("JOURNAL_UNAVAILABLE")

    def pin_new_enrollment(self, enrollment: HostOpsExecV1Enrollment, key: bytes, digest: str) -> None:
        """Commit the original pin and its first authenticated approval together."""
        try:
            with self._session() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO enrollment_pin VALUES (1,?,?,?)", self._pin(enrollment))
                version, _, previous = self._source_history(db, enrollment, key)
                if version != 0:
                    deny("JOURNAL_UNAVAILABLE")
                self._append_source_approval(db, enrollment, key, version, enrollment.source_sha, digest, previous)
        except sqlite3.Error:
            deny("JOURNAL_UNAVAILABLE")

    def finalize(self, job_id: str, receipt: dict) -> None:
        """Transition an INTENT record to its terminal durable state."""
        state = "UNKNOWN_EFFECT" if receipt["status"] == "UNKNOWN_EFFECT" else receipt["status"]
        try:
            with self._session() as db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    "UPDATE jobs SET state=?, status=?, code=?, exit_code=?, "
                    "stdout_sha256=?, stderr_sha256=?, output_bytes=?, output_truncated=?, "
                    "cancelled=?, readback_verified=?, updated_at=? "
                    "WHERE job_id=? AND state='INTENT'",
                    (
                        state, receipt["status"], receipt["code"], receipt["exit_code"],
                        receipt["stdout_sha256"], receipt["stderr_sha256"],
                        receipt["output_bytes"], int(receipt["output_truncated"]),
                        int(receipt["cancelled"]), int(receipt["readback_verified"]),
                        receipt["recorded_at"], job_id,
                    ),
                )
                if cursor.rowcount != 1:
                    deny("JOURNAL_UNAVAILABLE")
        except Denied:
            raise
        except sqlite3.Error:
            deny("JOURNAL_UNAVAILABLE")

    def record(self, job_id: str) -> dict | None:
        try:
            with self._session() as db:
                row = db.execute(
                    "SELECT job_id, nonce, request_digest, script_sha256, state, status, code, "
                    "exit_code, stdout_sha256, stderr_sha256, output_bytes, output_truncated, "
                    "cancelled, readback_verified, created_at, updated_at FROM jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
        except sqlite3.Error:
            deny("JOURNAL_UNAVAILABLE")
        if row is None:
            return None
        keys = (
            "job_id", "nonce", "request_digest", "script_sha256", "state", "status", "code",
            "exit_code", "stdout_sha256", "stderr_sha256", "output_bytes", "output_truncated",
            "cancelled", "readback_verified", "created_at", "updated_at",
        )
        return dict(zip(keys, row))


def _default_hostops_exec_v1_payload_resolver(
    script_sha256: str, enrollment: HostOpsExecV1Enrollment
) -> bytes | None:
    """Load an immutable local private PowerShell payload matching script_sha256."""
    if _EXEC_V1_HEX64_RE.fullmatch(script_sha256) is None:
        deny("REQUEST_INVALID")
    if not _exec_v1_no_reparse(enrollment.payload_root) or not enrollment.payload_root.is_dir():
        return None
    candidate = enrollment.payload_root / (script_sha256 + ".ps1")
    if not _exec_v1_no_reparse(candidate) or not candidate.is_file():
        return None
    try:
        with candidate.open("rb") as stream:
            raw = stream.read(HOSTOPS_EXEC_V1_MAX_SCRIPT_BYTES + 1)
    except OSError:
        return None
    if not 0 < len(raw) <= HOSTOPS_EXEC_V1_MAX_SCRIPT_BYTES:
        deny("PAYLOAD_OVERSIZED_OR_INVALID")
    return raw


class _ExecV1OutputCapture(asyncio.SubprocessProtocol):
    """Drain both streams with the receipt's stdout-first retention policy."""

    def __init__(self, limit: int):
        self.limit = limit
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.truncated = False
        self.pipe_failed = False
        self.closed = asyncio.Event()

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        available = self.limit - len(self.stdout)
        if fd == 1:
            retained = min(len(data), available)
            stderr_limit = available - retained
            if len(self.stderr) > stderr_limit:
                del self.stderr[stderr_limit:]
                self.truncated = True
            self.stdout.extend(memoryview(data)[:retained])
        else:
            retained = min(len(data), available - len(self.stderr))
            self.stderr.extend(memoryview(data)[:retained])
        self.truncated |= retained < len(data)

    def pipe_connection_lost(self, fd: int, exc: Exception | None) -> None:
        self.pipe_failed |= exc is not None

    def connection_lost(self, exc: Exception | None) -> None:
        self.pipe_failed |= exc is not None
        self.closed.set()


@contextmanager
def _exec_v1_windows_job():
    """Own process-tree cleanup under the existing runner token."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class BasicAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, dword, boolean = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    for name, arguments, result in (
        ("CreateJobObjectW", (ctypes.c_void_p, wintypes.LPCWSTR), handle),
        ("SetInformationJobObject", (handle, ctypes.c_int, ctypes.c_void_p, dword), boolean),
        ("QueryInformationJobObject", (handle, ctypes.c_int, ctypes.c_void_p, dword, ctypes.c_void_p), boolean),
        ("OpenProcess", (dword, boolean, dword), handle),
        ("AssignProcessToJobObject", (handle, handle), boolean),
        ("CloseHandle", (handle,), boolean),
    ):
        function = getattr(kernel, name)
        function.argtypes = arguments
        function.restype = result

    def checked(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    # Unnamed and non-inheritable. Do not grant breakaway for child processes.
    job = checked(kernel.CreateJobObjectW(None, None))
    try:
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        checked(kernel.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits),  # JobObjectExtendedLimitInformation
        ))

        def attach(pid):
            process = checked(kernel.OpenProcess(0x0101, False, pid))  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
            try:
                checked(kernel.AssignProcessToJobObject(job, process))
            finally:
                checked(kernel.CloseHandle(process))

        def has_live_processes():
            accounting = BasicAccounting()
            checked(kernel.QueryInformationJobObject(
                job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None,  # JobObjectBasicAccountingInformation
            ))
            return accounting.ActiveProcesses != 0

        yield attach, has_live_processes
    finally:
        checked(kernel.CloseHandle(job))


async def _capture_hostops_exec_v1(
    argv: tuple[str, ...], script: bytes, timeout_seconds: int, max_output_bytes: int,
    *, child_env: Mapping[str, str] | None = None,
) -> HostOpsExecV1ProcessResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    capture = _ExecV1OutputCapture(max_output_bytes)
    transport = None
    stdin = None
    try:
        with _exec_v1_windows_job() as (attach, has_live_processes):
            async with asyncio.timeout_at(deadline):
                # Proactor I/O drains both output pipes concurrently with the bounded script write.
                transport, _ = await loop.subprocess_exec(
                    lambda: capture, *argv,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    env=child_env,
                )
                stdin = transport.get_pipe_transport(0)
                # Attach before giving PowerShell any user script to run.
                attach(transport.get_pid())
                if loop.time() >= deadline:
                    raise TimeoutError
                stdin.write(script)
                stdin.close()
                await capture.closed.wait()
                if capture.pipe_failed:
                    raise OSError("HOSTOPS_EXEC_PIPE_FAILED")
                exit_code = transport.get_returncode()
                transport.close()
                transport = None
                # An exited parent and closed pipes can still leave a detached child.
                while has_live_processes():
                    await asyncio.sleep(0.01)
                if loop.time() >= deadline:
                    raise TimeoutError
                return HostOpsExecV1ProcessResult(
                    exit_code, bytes(capture.stdout), bytes(capture.stderr), capture.truncated,
                )
    except TimeoutError:
        raise subprocess.TimeoutExpired(argv, timeout_seconds) from None
    finally:
        # The job has closed before pipe teardown, so inherited handles cannot keep
        # this wait alive. Abort the stdin write instead of flushing during cancellation.
        if transport is not None:
            if stdin is not None and not capture.closed.is_set():
                stdin.abort()
            transport.close()
            if not capture.closed.is_set():
                # Cleanup is best effort after a failure and keeps its original classification.
                with suppress(TimeoutError):
                    await asyncio.wait_for(capture.closed.wait(), timeout=2)


def _default_hostops_exec_v1_runner(
    script_utf8: str, timeout_seconds: int, max_output_bytes: int
) -> HostOpsExecV1ProcessResult:
    """Default local PowerShell 7 runner; hard-blocked in CI/pytest/EF02."""
    if (os.name != "nt" or _exec_v1_source_ci(os.environ)
            or os.environ.get("RUNNER_NAME") != HOSTOPS_EXEC_V1_RUNNER_NAME):
        deny("SOURCE_CI_EXECUTION_FORBIDDEN")
    if (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("HOSTOPS_EXEC_V1_PROTECTED_DISPATCH") != "ENABLED"):
        deny("PROTECTED_WORKFLOW_DISPATCH_DISABLED")
    _verify_hostops_exec_v1_runtime(_default_hostops_exec_v1_runtime(), require_protected_workflow=True)
    from ai_commerce.remote import chrome_acquisition as acquisition

    try:
        executable = acquisition._powershell()
    except acquisition.AcquisitionBlocked:
        raise Denied("PROTECTED_HOSTOPS_ONLY") from None
    # The child receives the exact UTF-8 bytes and no Actions credentials in its environment.
    command = ("[Console]::InputEncoding = [Text.UTF8Encoding]::new($false, $true); "
               "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
               "& ([ScriptBlock]::Create([Console]::In.ReadToEnd())); if (-not $?) { exit 1 }")
    argv = (str(executable), "-NoProfile", "-NonInteractive", "-Command", command)
    child_env = {key: os.environ[key] for key in (
        "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
        "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "PATH", "PATHEXT", "PSModulePath",
    ) if key in os.environ}
    with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
        return runner.run(_capture_hostops_exec_v1(
            argv, script_utf8.encode("utf-8"), timeout_seconds, max_output_bytes, child_env=child_env,
        ))


def _exec_v1_winfn(dll, name, arguments, result):
    function = getattr(dll, name)
    function.argtypes = arguments
    function.restype = result
    return function


def _exec_v1_interactive_token() -> tuple[int, bool, str, str]:
    """Read process/token facts and independently resolve the active WTS user's SID."""
    import ctypes as c
    from ctypes import wintypes as w
    from ai_commerce.hostops_v3.security import windows_identity

    kernel = c.WinDLL("kernel32", use_last_error=True)
    adv = c.WinDLL("advapi32", use_last_error=True)
    wts = c.WinDLL("wtsapi32", use_last_error=True)
    shell = c.WinDLL("shell32", use_last_error=True)
    user = c.WinDLL("user32", use_last_error=True)
    ptr = c.c_void_p
    current_process = _exec_v1_winfn(kernel, "GetCurrentProcess", (), w.HANDLE)
    current_thread = _exec_v1_winfn(kernel, "GetCurrentThread", (), w.HANDLE)
    thread_token = _exec_v1_winfn(adv, "OpenThreadToken", (w.HANDLE, w.DWORD, w.BOOL, c.POINTER(w.HANDLE)), w.BOOL)
    close = _exec_v1_winfn(kernel, "CloseHandle", (w.HANDLE,), w.BOOL)
    token = w.HANDLE()
    if thread_token(current_thread(), 8, True, c.byref(token)):
        close(token)
        deny("WRONG_SESSION_OR_ELEVATED")
    if c.get_last_error() != 1008:  # ERROR_NO_TOKEN
        deny("WRONG_SESSION_OR_ELEVATED")
    open_token = _exec_v1_winfn(adv, "OpenProcessToken", (w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)), w.BOOL)
    token_info = _exec_v1_winfn(adv, "GetTokenInformation", (w.HANDLE, c.c_int, ptr, w.DWORD, c.POINTER(w.DWORD)), w.BOOL)
    if not open_token(current_process(), 8, c.byref(token)):
        deny("WRONG_SESSION_OR_ELEVATED")
    try:
        session, elevation_type, size = w.DWORD(), w.DWORD(), w.DWORD()
        if (not token_info(token, 12, c.byref(session), 4, c.byref(size)) or not session.value
                or not token_info(token, 18, c.byref(elevation_type), 4, c.byref(size))):
            deny("WRONG_SESSION_OR_ELEVATED")
    finally:
        close(token)
    owner_sid, elevated = windows_identity()
    is_admin = _exec_v1_winfn(shell, "IsUserAnAdmin", (), w.BOOL)
    if elevated or elevation_type.value != 1 or is_admin():  # Refuse filtered admin tokens too.
        deny("WRONG_SESSION_OR_ELEVATED")
    process_id = _exec_v1_winfn(kernel, "GetCurrentProcessId", (), w.DWORD)
    process_session = _exec_v1_winfn(kernel, "ProcessIdToSessionId", (w.DWORD, c.POINTER(w.DWORD)), w.BOOL)
    actual = w.DWORD()
    if not process_session(process_id(), c.byref(actual)) or actual.value != session.value:
        deny("WRONG_SESSION_OR_ELEVATED")
    open_desktop = _exec_v1_winfn(user, "OpenInputDesktop", (w.DWORD, w.BOOL, w.DWORD), w.HANDLE)
    close_desktop = _exec_v1_winfn(user, "CloseDesktop", (w.HANDLE,), w.BOOL)
    desktop = open_desktop(0, False, 1)
    if not desktop:
        deny("WRONG_SESSION_OR_ELEVATED")
    close_desktop(desktop)

    query = _exec_v1_winfn(wts, "WTSQuerySessionInformationW", (w.HANDLE, w.DWORD, c.c_int, c.POINTER(ptr), c.POINTER(w.DWORD)), w.BOOL)
    free = _exec_v1_winfn(wts, "WTSFreeMemory", (ptr,), None)

    def session_info(field, *, text=False):
        value, length = ptr(), w.DWORD()
        if not query(None, session, field, c.byref(value), c.byref(length)):
            deny("WRONG_SESSION_OR_ELEVATED")
        try:
            if text and value and 2 <= length.value <= 2048 and length.value % 2 == 0:
                return c.wstring_at(value, length.value // 2).rstrip("\x00")
            if not text and value and length.value == 4:
                return w.DWORD.from_address(value.value).value
            deny("WRONG_SESSION_OR_ELEVATED")
        finally:
            free(value)

    if session_info(8) != 0:  # WTSActive
        deny("WRONG_SESSION_OR_ELEVATED")
    username, domain = session_info(5, text=True), session_info(7, text=True)
    if not username or not domain:
        deny("OWNER_SID_MISMATCH")
    lookup = _exec_v1_winfn(adv, "LookupAccountNameW", (w.LPCWSTR, w.LPCWSTR, ptr, c.POINTER(w.DWORD),
                             w.LPWSTR, c.POINTER(w.DWORD), c.POINTER(w.DWORD)), w.BOOL)
    sid, referenced_domain = c.create_string_buffer(68), c.create_unicode_buffer(256)
    sid_length, domain_length, sid_use = w.DWORD(len(sid)), w.DWORD(len(referenced_domain)), w.DWORD()
    if (not lookup(None, f"{domain}\\{username}", sid, c.byref(sid_length), referenced_domain,
                   c.byref(domain_length), c.byref(sid_use)) or sid_use.value != 1):
        deny("OWNER_SID_MISMATCH")
    convert = _exec_v1_winfn(adv, "ConvertSidToStringSidW", (ptr, c.POINTER(w.LPWSTR)), w.BOOL)
    local_free = _exec_v1_winfn(kernel, "LocalFree", (ptr,), ptr)
    sid_text = w.LPWSTR()
    if not convert(sid, c.byref(sid_text)):
        deny("OWNER_SID_MISMATCH")
    try:
        return session.value, elevated, owner_sid, sid_text.value
    finally:
        local_free(c.cast(sid_text, ptr))


def _exec_v1_runner_lineage(session_id: int, owner_sid: str) -> str:
    """Identify the dedicated Listener/Worker by their live Windows process images."""
    import ctypes as c
    from ctypes import wintypes as w
    from ai_commerce.hostops_v3.security import windows_acl, windows_identity

    class ProcessEntry(c.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD), ("heap", c.c_size_t),
                    ("module", w.DWORD), ("threads", w.DWORD), ("parent", w.DWORD),
                    ("priority", w.LONG), ("flags", w.DWORD), ("image", w.WCHAR * 260)]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    snapshot = _exec_v1_winfn(kernel, "CreateToolhelp32Snapshot", (w.DWORD, w.DWORD), w.HANDLE)
    first = _exec_v1_winfn(kernel, "Process32FirstW", (w.HANDLE, c.POINTER(ProcessEntry)), w.BOOL)
    next_entry = _exec_v1_winfn(kernel, "Process32NextW", (w.HANDLE, c.POINTER(ProcessEntry)), w.BOOL)
    close = _exec_v1_winfn(kernel, "CloseHandle", (w.HANDLE,), w.BOOL)
    handle = snapshot(2, 0)
    if handle == c.c_void_p(-1).value:
        deny("WRONG_RUNNER_FORBIDDEN")
    entries = {}
    try:
        entry = ProcessEntry(size=c.sizeof(ProcessEntry))
        if not first(handle, c.byref(entry)):
            deny("WRONG_RUNNER_FORBIDDEN")
        while True:
            entries[entry.pid] = (entry.parent, entry.image.lower())
            if not next_entry(handle, c.byref(entry)):
                if c.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                    deny("WRONG_RUNNER_FORBIDDEN")
                break
    finally:
        close(handle)

    open_process = _exec_v1_winfn(kernel, "OpenProcess", (w.DWORD, w.BOOL, w.DWORD), w.HANDLE)
    process_session = _exec_v1_winfn(kernel, "ProcessIdToSessionId", (w.DWORD, c.POINTER(w.DWORD)), w.BOOL)
    image_name = _exec_v1_winfn(kernel, "QueryFullProcessImageNameW", (w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)), w.BOOL)
    get_times = _exec_v1_winfn(kernel, "GetProcessTimes", (w.HANDLE, *(c.POINTER(w.FILETIME),) * 4), w.BOOL)

    pid, child_created, worker = os.getpid(), None, False
    for _ in range(32):
        parent, image = entries.get(pid, (0, ""))
        if not image:
            return "OTHER" if worker else ""
        runner_image = image in ("runner.worker.exe", "runner.listener.exe")
        process = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return "OTHER" if worker or runner_image else ""
        try:
            created, exited, kernel_time, user_time = (w.FILETIME() for _ in range(4))
            if not get_times(process, *(c.byref(value) for value in (created, exited, kernel_time, user_time))):
                deny("WRONG_RUNNER_FORBIDDEN")
            created_time = (created.dwHighDateTime << 32) | created.dwLowDateTime
            if child_created is not None and created_time > child_created:
                return "OTHER" if worker or runner_image else ""  # The parent PID was reused.
            child_created = created_time
            if runner_image:
                buf = c.create_unicode_buffer(32768)
                length, session = w.DWORD(len(buf)), w.DWORD()
                if (not image_name(process, 0, buf, c.byref(length))
                        or not process_session(pid, c.byref(session)) or session.value != session_id):
                    return "OTHER"
                expected = _EXEC_V1_RUNNER_ROOT / "bin" / image
                if (Path(buf.value) != expected or not _exec_v1_no_reparse(expected)
                        or windows_identity(process) != (owner_sid, False)):
                    return "OTHER"
                for path in (expected, expected.parent, _EXEC_V1_RUNNER_ROOT, _EXEC_V1_RUNNER_ROOT.parent):
                    windows_acl(path, {owner_sid, "S-1-5-18", "S-1-5-32-544"})
                if image == "runner.listener.exe":
                    return HOSTOPS_EXEC_V1_RUNNER_NAME if worker else "OTHER"
                worker = True
        finally:
            close(process)
        pid = parent
    deny("WRONG_RUNNER_FORBIDDEN")


def _default_hostops_exec_v1_runtime(env: Mapping[str, str] | None = None) -> HostOpsExecV1RuntimeContext:
    current_env = os.environ if env is None else env
    if _exec_v1_source_ci(current_env):
        deny("SOURCE_CI_EXECUTION_FORBIDDEN")
    if os.name != "nt":
        deny("WINDOWS_ONLY")
    if sys.version_info < (3, 14):
        deny("PROTECTED_HOSTOPS_ONLY")
    try:
        session, elevated, owner_sid, interactive_sid = _exec_v1_interactive_token()
        if type(session) is not int or session <= 0 or elevated is not False:
            deny("WRONG_SESSION_OR_ELEVATED")
        if (type(owner_sid) is not str or _EXEC_V1_USER_SID_RE.fullmatch(owner_sid) is None
                or owner_sid != interactive_sid):
            deny("OWNER_SID_MISMATCH")
        lineage = _exec_v1_runner_lineage(session, owner_sid)
    except Denied:
        raise
    except Exception:
        deny("PROTECTED_HOSTOPS_ONLY")
    if current_env.get("GITHUB_ACTIONS") != "true":
        if lineage:
            deny("SOURCE_CI_EXECUTION_FORBIDDEN")
        # The local operator has no runner parent. The enrollment is for this fixed runner.
        lineage = HOSTOPS_EXEC_V1_RUNNER_NAME
    return HostOpsExecV1RuntimeContext(
        os.name, lineage, session, elevated, owner_sid, current_env,
    )


class HostOpsExecV1Broker:
    """Fail-closed generic PowerShell execution broker, disabled until enrolled.

    Once enrolled by the owner on the interactive non-elevated Windows HOSTOPS
    runner, owner-authenticated requests may run varied PowerShell commands
    without modifying source per command or requiring repeated GitHub approval
    comments. Public receipts never echo raw scripts, output, paths, or traces.
    """

    def __init__(
        self,
        *,
        enrollment: HostOpsExecV1Enrollment | None = None,
        journal: HostOpsExecV1Journal | None = None,
        runtime_inspector: Callable[[], HostOpsExecV1RuntimeContext] | None = None,
        authenticator: Callable[[bytes, str | None, HostOpsExecV1Enrollment], bool] | None = None,
        payload_resolver: Callable[[str, HostOpsExecV1Enrollment], bytes | None] | None = None,
        runner: Callable[[str, int, int], HostOpsExecV1ProcessResult] | None = None,
        readback_verifier: Callable[[HostOpsExecV1ProcessResult], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
        require_protected_workflow: bool = False,
    ):
        self._enrollment = enrollment
        self._journal = journal
        self._runtime_inspector = runtime_inspector if runtime_inspector is not None else _default_hostops_exec_v1_runtime
        self._authenticator = authenticator
        self._payload_resolver = (
            payload_resolver if payload_resolver is not None else _default_hostops_exec_v1_payload_resolver
        )
        self._runner = runner if runner is not None else _default_hostops_exec_v1_runner
        self._readback_verifier = readback_verifier
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self._require_protected_workflow = require_protected_workflow
        self._lock = Lock()

    def _receipt(
        self,
        *,
        status: str,
        code: str,
        request: dict | None = None,
        enrolled: bool = False,
        executed: bool = False,
        cancelled: bool = False,
        readback_verified: bool = False,
        exit_code: int | None = None,
        stdout_sha256: str | None = None,
        stderr_sha256: str | None = None,
        output_bytes: int = 0,
        output_truncated: bool = False,
        source_sha: str | None = None,
        issue_number: int | None = None,
    ) -> dict:
        return validate_hostops_exec_v1_receipt({
            "schema": HOSTOPS_EXEC_V1_RECEIPT_SCHEMA,
            "status": status,
            "code": code,
            "job_id": request["job_id"] if request else None,
            "request_digest": request["request_digest"] if request else None,
            "script_sha256": request["script_sha256"] if request else None,
            "authority_tier": HOSTOPS_EXEC_V1_AUTHORITY_TIER_LOCAL,
            "payload_mode": request["payload_mode"] if request else None,
            "enrolled": enrolled,
            "executed": executed,
            "cancelled": cancelled,
            "readback_verified": readback_verified,
            "exit_code": exit_code,
            "stdout_sha256": stdout_sha256,
            "stderr_sha256": stderr_sha256,
            "output_bytes": output_bytes,
            "output_truncated": output_truncated,
            "denylist_security_claim": False,
            "public_script_echoed": False,
            "public_output_echoed": False,
            "elevated_token": False,
            "source_sha": source_sha,
            "issue_number": issue_number,
            "recorded_at": _exec_v1_stamp(self._clock()),
        })

    def _admit(
        self,
        raw_request,
        *,
        public_issue: bool = False,
        issue_number: int | None = None,
        auth_token: str | None = None,
    ) -> tuple[dict, HostOpsExecV1Enrollment, HostOpsExecV1Journal, str]:
        request = parse_hostops_exec_v1_request(raw_request, public_issue=public_issue)
        if self._enrollment is None:
            deny("BLOCKED_NOT_ENROLLED")
        enrollment = self._enrollment
        enrollment.validate()
        ctx = self._runtime_inspector()
        _verify_hostops_exec_v1_runtime(
            ctx,
            expected_owner_sid=enrollment.owner_sid,
            require_protected_workflow=self._require_protected_workflow,
        )
        if self._authenticator is None:
            deny("AUTHENTICATION_UNPROVEN")
        try:
            authed = self._authenticator(_exec_v1_canonical(request), auth_token, enrollment) is True
        except Exception:
            authed = False
        if not authed:
            deny("AUTHENTICATION_UNPROVEN")
        if self._require_protected_workflow:
            if self._journal is None:
                _exec_v1_verify_source(ctx.env["GITHUB_SHA"], enrollment_sha=enrollment.source_sha)
            else:
                self._journal.verify_source(ctx.env["GITHUB_SHA"], enrollment)
        if public_issue and request["payload_mode"] != "PRIVATE_DIGEST":
            deny("PUBLIC_ISSUE_RAW_SCRIPT_DENIED")
        if request["payload_mode"] == "PRIVATE_DIGEST":
            payload_bytes = self._payload_resolver(request["script_sha256"], enrollment)
            if payload_bytes is None:
                deny("PRIVATE_PAYLOAD_UNAVAILABLE")
            if type(payload_bytes) is not bytes or not 0 < len(payload_bytes) <= HOSTOPS_EXEC_V1_MAX_SCRIPT_BYTES:
                deny("PAYLOAD_OVERSIZED_OR_INVALID")
            if b"\x00" in payload_bytes:
                deny("PAYLOAD_OVERSIZED_OR_INVALID")
            if not hmac.compare_digest(_exec_v1_sha256(payload_bytes), request["script_sha256"]):
                deny("PAYLOAD_DIGEST_MISMATCH")
            try:
                script_utf8 = payload_bytes.decode("utf-8")
            except UnicodeError:
                deny("PAYLOAD_OVERSIZED_OR_INVALID")
        else:
            if enrollment.require_private_payload and public_issue:
                deny("PUBLIC_ISSUE_RAW_SCRIPT_DENIED")
            script_utf8 = request["script_utf8"]
        journal = self._journal if self._journal is not None else HostOpsExecV1Journal(enrollment.journal_path)
        return request, enrollment, journal, script_utf8

    def _error_receipt(
        self,
        code: str,
        *,
        request: dict | None = None,
        enrollment: HostOpsExecV1Enrollment | None = None,
        issue_number: int | None = None,
    ) -> dict:
        safe_code = code if code in HOSTOPS_EXEC_V1_CODES else "REQUEST_INVALID"
        if safe_code in ("BLOCKED_NOT_ENROLLED", "ENROLLMENT_DISABLED", "ENROLLMENT_REVOKED",
                         "PROTECTED_WORKFLOW_DISPATCH_DISABLED"):
            status = "DISABLED"
        elif safe_code == "UNKNOWN_EFFECT_HOLD":
            status = "UNKNOWN_EFFECT"
        else:
            status = "REJECTED"
        enrolled = (
            enrollment is not None
            and enrollment.enabled is True
            and enrollment.revoked is False
            and safe_code not in ("BLOCKED_NOT_ENROLLED", "ENROLLMENT_DISABLED", "ENROLLMENT_REVOKED", "ENROLLMENT_INVALID")
        )
        source_sha = self._approved_source_sha(enrollment) if (enrollment and type(enrollment.source_sha) is str
                                                               and _EXEC_V1_HEX40_RE.fullmatch(enrollment.source_sha)) else None
        return self._receipt(
            status=status,
            code=safe_code,
            request=request,
            enrolled=enrolled,
            source_sha=source_sha,
            issue_number=issue_number,
        )

    def _approved_source_sha(self, enrollment: HostOpsExecV1Enrollment) -> str:
        if self._journal is not None:
            return self._journal.approved_source_sha(enrollment)
        return enrollment.source_sha

    def plan(
        self,
        raw_request,
        *,
        public_issue: bool = False,
        issue_number: int | None = None,
        auth_token: str | None = None,
    ) -> dict:
        """Validate enrollment, auth, payload binding and journal without side effects."""
        parsed = None
        try:
            parsed = parse_hostops_exec_v1_request(raw_request, public_issue=public_issue)
            request, enrollment, journal, _ = self._admit(
                parsed, public_issue=public_issue, issue_number=issue_number, auth_token=auth_token
            )
            journal.check_ready(request, enrollment)
            return self._receipt(
                status="PLANNED",
                code="READY_TO_EXECUTE",
                request=request,
                enrolled=True,
                source_sha=self._approved_source_sha(enrollment),
                issue_number=issue_number,
            )
        except Denied as exc:
            return self._error_receipt(
                str(exc), request=parsed, enrollment=self._enrollment, issue_number=issue_number
            )

    def execute(
        self,
        raw_request,
        *,
        public_issue: bool = False,
        issue_number: int | None = None,
        auth_token: str | None = None,
        cancel_before_spawn: bool = False,
    ) -> dict:
        """Reserve durable intent and execute one bounded PowerShell job."""
        parsed = None
        with self._lock:
            try:
                parsed = parse_hostops_exec_v1_request(raw_request, public_issue=public_issue)
                request, enrollment, journal, script_utf8 = self._admit(
                    parsed, public_issue=public_issue, issue_number=issue_number, auth_token=auth_token
                )
                if cancel_before_spawn:
                    journal.check_ready(request, enrollment)
                    return self._receipt(
                        status="REJECTED",
                        code="CANCELLED_BEFORE_EXECUTION",
                        request=request,
                        enrolled=True,
                        cancelled=True,
                        readback_verified=False,
                        source_sha=self._approved_source_sha(enrollment),
                        issue_number=issue_number,
                    )
                reserved_at = _exec_v1_stamp(self._clock())
                journal.reserve(request, enrollment, reserved_at)
            except Denied as exc:
                return self._error_receipt(
                    str(exc), request=parsed, enrollment=self._enrollment, issue_number=issue_number
                )

            # From this point forward, durable INTENT exists in the journal.
            # Any timeout, post-spawn cancellation, exception, or readback failure
            # must land in UNKNOWN_EFFECT and NEVER auto-replay.
            try:
                result = self._runner(script_utf8, request["timeout_seconds"], HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES)
            except (subprocess.TimeoutExpired, InterruptedError, KeyboardInterrupt):
                receipt = self._receipt(
                    status="UNKNOWN_EFFECT",
                    code="EXECUTION_CANCELLED_OR_TIMED_OUT_UNKNOWN_EFFECT",
                    request=request,
                    enrolled=True,
                    cancelled=True,
                    readback_verified=False,
                    source_sha=self._approved_source_sha(enrollment),
                    issue_number=issue_number,
                )
                try:
                    journal.finalize(request["job_id"], receipt)
                except Denied:
                    pass
                return receipt
            except Exception:
                receipt = self._receipt(
                    status="UNKNOWN_EFFECT",
                    code="EXECUTION_ERROR_UNKNOWN_EFFECT",
                    request=request,
                    enrolled=True,
                    cancelled=False,
                    readback_verified=False,
                    source_sha=self._approved_source_sha(enrollment),
                    issue_number=issue_number,
                )
                try:
                    journal.finalize(request["job_id"], receipt)
                except Denied:
                    pass
                return receipt

            try:
                if (not isinstance(result, HostOpsExecV1ProcessResult)
                        or type(result.exit_code) is not int
                        or type(result.stdout) is not bytes
                        or type(result.stderr) is not bytes
                        or type(result.output_truncated) is not bool):
                    raise ValueError("INVALID_PROCESS_RESULT")
                if self._readback_verifier is not None and self._readback_verifier(result) is not True:
                    raise ValueError("READBACK_FAILED")
                total_bytes = len(result.stdout) + len(result.stderr)
                truncated = result.output_truncated or total_bytes > HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES
                bounded_stdout = result.stdout[:HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES]
                remaining = max(0, HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES - len(bounded_stdout))
                bounded_stderr = result.stderr[:remaining]
                receipt = self._receipt(
                    status="EXECUTED",
                    code="EXECUTION_COMPLETED" if result.exit_code == 0 else "EXECUTION_NONZERO_EXIT",
                    request=request,
                    enrolled=True,
                    executed=True,
                    cancelled=False,
                    readback_verified=True,
                    exit_code=result.exit_code,
                    stdout_sha256=_exec_v1_sha256(bounded_stdout),
                    stderr_sha256=_exec_v1_sha256(bounded_stderr),
                    output_bytes=len(bounded_stdout) + len(bounded_stderr),
                    output_truncated=truncated,
                    source_sha=self._approved_source_sha(enrollment),
                    issue_number=issue_number,
                )
                journal.finalize(request["job_id"], receipt)
                return receipt
            except Exception:
                receipt = self._receipt(
                    status="UNKNOWN_EFFECT",
                    code="READBACK_UNVERIFIED_UNKNOWN_EFFECT",
                    request=request,
                    enrolled=True,
                    cancelled=False,
                    readback_verified=False,
                    source_sha=self._approved_source_sha(enrollment),
                    issue_number=issue_number,
                )
                try:
                    journal.finalize(request["job_id"], receipt)
                except Denied:
                    pass
                return receipt

    def handle_event(
        self,
        event,
        *,
        action: Literal["plan", "execute"] = "plan",
        auth_token: str | None = None,
        cancel_before_spawn: bool = False,
    ) -> dict:
        """Process a GitHub Issue event for a future protected workflow PR."""
        try:
            parsed = parse_hostops_exec_v1_event(event)
        except Denied as exc:
            return self._error_receipt(str(exc), enrollment=self._enrollment)
        if action == "plan":
            return self.plan(
                parsed["request"],
                public_issue=parsed["public_issue"],
                issue_number=parsed["issue_number"],
                auth_token=auth_token,
            )
        if action == "execute":
            return self.execute(
                parsed["request"],
                public_issue=parsed["public_issue"],
                issue_number=parsed["issue_number"],
                auth_token=auth_token,
                cancel_before_spawn=cancel_before_spawn,
            )
        return self._error_receipt(
            "REQUEST_INVALID",
            request=parsed["request"],
            enrollment=self._enrollment,
            issue_number=parsed["issue_number"],
        )


def _exec_v1_https_read(url: str, limit: int, *, bearer: str | None = None) -> bytes:
    """Use Windows TLS trust and a direct connection with no credential redirects."""
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    certificates = [ssl.DER_cert_to_PEM_cert(cert) for cert, encoding, trust in ssl.enum_certificates("ROOT")
                    if encoding == "x509_asn" and (trust is True or "1.3.6.1.5.5.7.3.1" in trust)]
    tls.load_verify_locations(cadata="".join(certificates))
    headers = {"User-Agent": "AI-Commerce-HostOps-Exec-V1"}
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    with build_opener(ProxyHandler({}), HTTPSHandler(context=tls), _CdpNoRedirect()).open(
            Request(url, headers=headers), timeout=5) as response:
        raw = response.read(limit + 1)
    if not 0 < len(raw) <= limit:
        deny("PROTECTED_HOSTOPS_ONLY")
    return raw


def _exec_v1_b64url(value: str) -> bytes:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        deny("PROTECTED_HOSTOPS_ONLY")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _exec_v1_verify_rs256(data: bytes, signature: bytes, key: dict) -> None:
    """Verify the issuer's RSA key with Windows CNG, using PKCS#1 SHA-256."""
    import ctypes as c
    from ctypes import wintypes as w

    class Padding(c.Structure):
        _fields_ = [("algorithm", w.LPCWSTR)]

    exponent = _exec_v1_b64url(key["e"])
    modulus = _exec_v1_b64url(key["n"])
    if (exponent != b"\x01\x00\x01" or len(modulus) not in (256, 384, 512)
            or not modulus[0] & 0x80 or len(signature) != len(modulus)):
        deny("PROTECTED_HOSTOPS_ONLY")
    blob = struct.pack("<6I", 0x31415352, len(modulus) * 8, len(exponent), len(modulus), 0, 0)
    blob += exponent + modulus
    crypto = c.WinDLL("bcrypt", use_last_error=True)
    ptr, status = c.c_void_p, w.LONG
    open_provider = _exec_v1_winfn(crypto, "BCryptOpenAlgorithmProvider", (c.POINTER(ptr), w.LPCWSTR, w.LPCWSTR, w.ULONG), status)
    close_provider = _exec_v1_winfn(crypto, "BCryptCloseAlgorithmProvider", (ptr, w.ULONG), status)
    import_key = _exec_v1_winfn(crypto, "BCryptImportKeyPair", (ptr, ptr, w.LPCWSTR, c.POINTER(ptr), ptr, w.ULONG, w.ULONG), status)
    destroy_key = _exec_v1_winfn(crypto, "BCryptDestroyKey", (ptr,), status)
    verify = _exec_v1_winfn(crypto, "BCryptVerifySignature", (ptr, ptr, ptr, w.ULONG, ptr, w.ULONG, w.ULONG), status)
    provider, imported = ptr(), ptr()
    if open_provider(c.byref(provider), "RSA", None, 0):
        deny("PROTECTED_HOSTOPS_ONLY")
    try:
        if import_key(provider, None, "RSAPUBLICBLOB", c.byref(imported), blob, len(blob), 0):
            deny("PROTECTED_HOSTOPS_ONLY")
        try:
            digest = hashlib.sha256(data).digest()
            if verify(imported, c.byref(Padding("SHA256")), digest, len(digest), signature, len(signature), 2):
                deny("PROTECTED_HOSTOPS_ONLY")
        finally:
            destroy_key(imported)
    finally:
        close_provider(provider, 0)


def _exec_v1_check_oidc(env: Mapping[str, str], request_digest: str, *, action: str) -> None:
    """Require fresh GitHub-signed claims for the protected job and selected request."""
    endpoint = urlsplit(env.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""))
    bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    if (endpoint.scheme != "https" or re.fullmatch(r"pipelines[a-z0-9]+\.actions\.githubusercontent\.com", endpoint.netloc) is None
            or re.fullmatch(r"/[A-Za-z0-9/_-]+/idtoken", endpoint.path) is None or endpoint.fragment
            or re.fullmatch(r"[A-Za-z0-9._~-]{20,8192}", bearer) is None
            or re.fullmatch(r"[1-9][0-9]{0,19}", run_id) is None):
        deny("PROTECTED_HOSTOPS_ONLY")
    query = parse_qsl(endpoint.query, keep_blank_values=True, max_num_fields=16)
    if any(key.lower() == "audience" for key, _ in query):
        deny("PROTECTED_HOSTOPS_ONLY")
    audience = f"ai-commerce:hostops-exec-v1:{action}:{request_digest}:{secrets.token_hex(16)}"
    url = urlunsplit(endpoint._replace(query=urlencode([*query, ("audience", audience)])))
    envelope = json.loads(_exec_v1_https_read(url, 16384, bearer=bearer), object_pairs_hook=_object_unique)
    header_part, claims_part, sig_part = envelope["value"].split(".")
    header = json.loads(_exec_v1_b64url(header_part), object_pairs_hook=_object_unique)
    if header.get("alg") != "RS256" or type(header.get("kid")) is not str or "crit" in header:
        deny("PROTECTED_HOSTOPS_ONLY")
    keys = json.loads(_exec_v1_https_read(_EXEC_V1_OIDC_ISSUER + "/.well-known/jwks", 32768),
                      object_pairs_hook=_object_unique)["keys"]
    matching = [key for key in keys if key.get("kid") == header["kid"] and key.get("kty") == "RSA"
                and key.get("alg", "RS256") == "RS256" and key.get("use", "sig") == "sig"]
    if len(matching) != 1:
        deny("PROTECTED_HOSTOPS_ONLY")
    _exec_v1_verify_rs256(f"{header_part}.{claims_part}".encode("ascii"), _exec_v1_b64url(sig_part), matching[0])
    claims = json.loads(_exec_v1_b64url(claims_part), object_pairs_hook=_object_unique)
    expected = {
        "iss": _EXEC_V1_OIDC_ISSUER, "aud": audience,
        "environment": _EXEC_V1_WORKFLOW_ENVIRONMENT,
        "repository": REPO, "repository_owner": OWNER, "repository_visibility": "public",
        "actor": OWNER, "event_name": "issues", "ref": "refs/heads/main", "ref_type": "branch",
        "workflow_ref": HOSTOPS_EXEC_V1_WORKFLOW_REF, "workflow_sha": env["GITHUB_SHA"],
        "sha": env["GITHUB_SHA"], "runner_environment": "self-hosted", "run_attempt": "1", "run_id": run_id,
    }
    now = int(time.time())
    if (any(claims.get(name) != value for name, value in expected.items())
            or (claims.get("ref_protected") is not True and claims.get("ref_protected") != "true")
            or any(type(claims.get(name)) is not int for name in ("iat", "nbf", "exp"))
            or not now - 120 <= claims["iat"] <= now + 30
            or claims["nbf"] > now + 30 or claims["exp"] <= now
            or not 0 < claims["exp"] - claims["iat"] <= 600):
        deny("PROTECTED_HOSTOPS_ONLY")


def _exec_v1_git_object(git_dir: Path, kind: str, ref: str, limit: int) -> tuple[str, bytes]:
    """Read and rehash raw Git objects, with replacement and lazy fetching disabled."""
    command = ["git", "--no-replace-objects", "--no-lazy-fetch", f"--git-dir={git_dir}", "cat-file"]
    try:
        size_result = subprocess.run(
            [*command, "-s", ref], cwd=git_dir.parent, stdin=subprocess.DEVNULL,
            capture_output=True, check=True, timeout=5,
        )
        size_raw = size_result.stdout.strip()
        if not re.fullmatch(rb"[1-9][0-9]{0,8}", size_raw) or int(size_raw) > limit:
            deny("SOURCE_REVISION_MISMATCH")
        result = subprocess.run(
            [*command, kind, ref], cwd=git_dir.parent, stdin=subprocess.DEVNULL,
            capture_output=True, check=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        deny("SOURCE_REVISION_MISMATCH")
    raw = result.stdout
    if len(raw) != int(size_raw):
        deny("SOURCE_REVISION_MISMATCH")
    oid = hashlib.sha1(f"{kind} {len(raw)}\0".encode("ascii") + raw).hexdigest()
    if ref != "HEAD" and oid != ref:
        deny("SOURCE_REVISION_MISMATCH")
    return oid, raw


def _exec_v1_source_checkout() -> tuple[Path, Path]:
    source = Path(__file__).absolute()
    try:
        workspace = source.parents[len(_EXEC_V1_SOURCE_PATH.parts) - 1]
        git_dir = workspace / ".git"
        if (source != workspace / _EXEC_V1_SOURCE_PATH or not _exec_v1_no_reparse(source)
                or not _exec_v1_no_reparse(git_dir / "objects") or not (git_dir / "objects").is_dir()):
            deny("SOURCE_REVISION_MISMATCH")
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            deny("SOURCE_REVISION_MISMATCH")
    except (IndexError, OSError):
        deny("SOURCE_REVISION_MISMATCH")
    return source, git_dir


def _exec_v1_tree_entry(tree: bytes, name: str, modes: tuple[bytes, ...]) -> str:
    target = name.encode("ascii")
    offset = 0
    matches = []
    while offset < len(tree):
        end = tree.find(b"\0", offset)
        if end < 0 or end + 21 > len(tree):
            deny("SOURCE_REVISION_MISMATCH")
        mode, _, entry_name = tree[offset:end].partition(b" ")
        if entry_name == target:
            if mode not in modes:
                deny("SOURCE_REVISION_MISMATCH")
            matches.append(tree[end + 1:end + 21].hex())
        offset = end + 21
    if len(matches) != 1:
        deny("SOURCE_REVISION_MISMATCH")
    return matches[0]


def _exec_v1_pinned_source(git_dir: Path, sha: str | None, *, head: bool = False) -> tuple[str, bytes]:
    if sha is not None and (type(sha) is not str or not _EXEC_V1_HEX40_RE.fullmatch(sha)):
        deny("SOURCE_REVISION_MISMATCH")
    if not head and sha is None:
        deny("SOURCE_REVISION_MISMATCH")
    ref = "HEAD" if head else sha
    commit_oid, commit = _exec_v1_git_object(git_dir, "commit", ref, _EXEC_V1_SOURCE_LIMIT)
    if sha is not None and commit_oid != sha:
        deny("SOURCE_REVISION_MISMATCH")
    tree_line = re.fullmatch(rb"tree ([a-f0-9]{40})", commit.split(b"\n", 1)[0])
    if tree_line is None:
        deny("SOURCE_REVISION_MISMATCH")
    tree_oid = tree_line[1].decode("ascii")
    for directory in _EXEC_V1_SOURCE_PATH.parts[:-1]:
        _, tree = _exec_v1_git_object(git_dir, "tree", tree_oid, _EXEC_V1_SOURCE_LIMIT)
        tree_oid = _exec_v1_tree_entry(tree, directory, (b"40000",))
    _, tree = _exec_v1_git_object(git_dir, "tree", tree_oid, _EXEC_V1_SOURCE_LIMIT)
    blob_oid = _exec_v1_tree_entry(tree, _EXEC_V1_SOURCE_PATH.name, (b"100644", b"100755"))
    return commit_oid, _exec_v1_git_object(git_dir, "blob", blob_oid, _EXEC_V1_SOURCE_LIMIT)[1]


def _exec_v1_legacy_source_digest(enrollment_sha: str, current_sha: str, current_digest: str) -> str:
    if enrollment_sha == current_sha:
        return current_digest
    _, git_dir = _exec_v1_source_checkout()
    return _exec_v1_sha256(_exec_v1_pinned_source(git_dir, enrollment_sha)[1])


def _exec_v1_verify_source(
    sha: str | None, *, enrollment_sha: str | None = None, approved_digest: str | None = None,
) -> str:
    source, git_dir = _exec_v1_source_checkout()
    try:
        with source.open("rb") as stream:
            current = stream.read(_EXEC_V1_SOURCE_LIMIT + 1)
    except OSError:
        deny("SOURCE_REVISION_MISMATCH")
    if len(current) > _EXEC_V1_SOURCE_LIMIT:
        deny("SOURCE_REVISION_MISMATCH")
    current_sha, pinned = _exec_v1_pinned_source(git_dir, sha, head=True)
    if not _clean_source_bytes_match(source, current, pinned):
        deny("SOURCE_REVISION_MISMATCH")
    digest = _exec_v1_sha256(pinned)
    # CRLF checkout conversion is allowed only above, never for the committed fingerprint.
    if approved_digest is None and enrollment_sha is not None:
        approved_digest = _exec_v1_legacy_source_digest(enrollment_sha, current_sha, digest)
    if approved_digest is not None and not hmac.compare_digest(approved_digest, digest):
        deny("SOURCE_REVISION_MISMATCH")
    return digest


def _exec_v1_native_state_root(owner_sid: str) -> Path:
    import ctypes as c
    from ctypes import wintypes as w
    from uuid import UUID
    from ai_commerce.hostops_v3.security import windows_acl

    shell = c.WinDLL("shell32", use_last_error=True)
    ole = c.WinDLL("ole32", use_last_error=True)
    known_folder = _exec_v1_winfn(shell, "SHGetKnownFolderPath", (c.c_void_p, w.DWORD, w.HANDLE,
                                                                    c.POINTER(w.LPWSTR)), w.LONG)
    free = _exec_v1_winfn(ole, "CoTaskMemFree", (c.c_void_p,), None)
    guid = (c.c_ubyte * 16).from_buffer_copy(UUID("F1B32785-6FBA-4FCF-9D55-7B8E7F157091").bytes_le)
    directory = w.LPWSTR()
    if known_folder(guid, 0, None, c.byref(directory)):
        deny("PRIVATE_STATE_UNSAFE")
    try:
        parent = Path(directory.value or "")
    finally:
        free(c.cast(directory, c.c_void_p))
    if not parent.is_dir() or not _exec_v1_no_reparse(parent) or parent.drive.startswith("\\"):
        deny("PRIVATE_STATE_UNSAFE")
    windows_acl(parent, {owner_sid, "S-1-5-18", "S-1-5-32-544"})
    return parent / _EXEC_V1_STATE_DIRNAME


def _exec_v1_private_path(path: Path, owner_sid: str, *, directory: bool = False) -> None:
    from ai_commerce.remote.local_completion_handoff import _check_windows_acl, _windows_acl

    if not _exec_v1_no_reparse(path):
        deny("REPARSE_PATH_DENIED")
    try:
        info = path.lstat()
        if directory:
            if not stat.S_ISDIR(info.st_mode):
                deny("PRIVATE_STATE_UNSAFE")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            deny("PRIVATE_STATE_UNSAFE")
        _check_windows_acl(*_windows_acl(path), owner_sid)
    except Denied:
        raise
    except Exception:
        deny("PRIVATE_STATE_UNSAFE")


def _exec_v1_write_private(stream, path: Path, raw: bytes, owner_sid: str) -> None:
    _exec_v1_private_path(path, owner_sid)
    stream.write(raw)
    stream.flush()
    os.fsync(stream.fileno())
    stream.seek(0)
    if stream.read(len(raw) + 1) != raw:
        deny("PRIVATE_STATE_UNSAFE")


def _exec_v1_require_not_revoked(root: Path) -> None:
    marker = root / _EXEC_V1_REVOKED_FILENAME
    if not _exec_v1_no_reparse(marker) or marker.exists():
        deny("ENROLLMENT_REVOKED")


def _exec_v1_private_journal(path: Path, owner_sid: str) -> None:
    _exec_v1_private_path(path, owner_sid)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() or not _exec_v1_no_reparse(sidecar):
            _exec_v1_private_path(sidecar, owner_sid)


def _exec_v1_enroll_native(root: Path, ctx: HostOpsExecV1RuntimeContext, sha: str) -> dict:
    _verify_hostops_exec_v1_runtime(ctx, forbid_github_actions=True)
    if type(sha) is not str or not _EXEC_V1_HEX40_RE.fullmatch(sha):
        deny("SOURCE_REVISION_MISMATCH")
    if not _exec_v1_no_reparse(root):
        deny("REPARSE_PATH_DENIED")
    digest = _exec_v1_verify_source(sha)
    try:
        root.mkdir(mode=0o700)  # Windows 3.14 applies a user/administrators DACL at creation.
    except FileExistsError:
        deny("ALREADY_ENROLLED_NO_OVERWRITE")
    _exec_v1_private_path(root, ctx.owner_sid, directory=True)
    payload_root = root / HOSTOPS_EXEC_V1_PAYLOAD_DIRNAME
    payload_root.mkdir(mode=0o700)
    _exec_v1_private_path(payload_root, ctx.owner_sid, directory=True)
    key = secrets.token_bytes(32)
    key_path = root / _EXEC_V1_KEY_FILENAME
    with key_path.open("x+b") as stream:
        _exec_v1_write_private(stream, key_path, key, ctx.owner_sid)
    enrollment, receipt = enroll_hostops_exec_v1(
        enrollment_id=secrets.token_hex(16), source_sha=sha, state_root=root, auth_key=key, runtime=ctx,
    )
    journal = HostOpsExecV1Journal(enrollment.journal_path)
    journal.pin_new_enrollment(enrollment, key, digest)
    stored_enrollment, stored_key = _exec_v1_load_native(root, ctx)
    if stored_enrollment != enrollment:
        deny("ENROLLMENT_PIN_MISMATCH")
    journal = HostOpsExecV1Journal(enrollment.journal_path, existing=True)
    if journal.source_approval(stored_enrollment, stored_key) != (sha, digest):
        deny("JOURNAL_UNAVAILABLE")
    return receipt


def _exec_v1_load_native(
    root: Path, ctx: HostOpsExecV1RuntimeContext,
) -> tuple[HostOpsExecV1Enrollment, bytes]:
    if not _exec_v1_no_reparse(root):
        deny("REPARSE_PATH_DENIED")
    if not root.exists():
        deny("BLOCKED_NOT_ENROLLED")
    owner = ctx.owner_sid
    _exec_v1_private_path(root, owner, directory=True)
    _exec_v1_require_not_revoked(root)
    record = root / HOSTOPS_EXEC_V1_ENROLLMENT_FILENAME
    _exec_v1_private_path(record, owner)
    enrollment = load_hostops_exec_v1_enrollment(record)
    if enrollment is None:
        deny("ENROLLMENT_INVALID")
    enrollment.validate()
    if (enrollment.state_root != root or enrollment.journal_path != root / HOSTOPS_EXEC_V1_JOURNAL_FILENAME
            or enrollment.payload_root != root / HOSTOPS_EXEC_V1_PAYLOAD_DIRNAME or not enrollment.require_private_payload):
        deny("ENROLLMENT_INVALID")
    if enrollment.owner_sid != owner:
        deny("OWNER_SID_MISMATCH")
    _exec_v1_private_path(enrollment.payload_root, owner, directory=True)
    _exec_v1_private_journal(enrollment.journal_path, owner)
    key_path = root / _EXEC_V1_KEY_FILENAME
    _exec_v1_private_path(key_path, owner)
    with key_path.open("rb") as stream:
        key = stream.read(33)
    if len(key) != 32 or not hmac.compare_digest(_exec_v1_sha256(key), enrollment.auth_key_sha256):
        deny("AUTHENTICATION_UNPROVEN")
    return enrollment, key


def _exec_v1_stage_native(enrollment: HostOpsExecV1Enrollment, stream) -> str:
    raw = stream.read(HOSTOPS_EXEC_V1_MAX_SCRIPT_BYTES + 1)
    if not 0 < len(raw) <= HOSTOPS_EXEC_V1_MAX_SCRIPT_BYTES or b"\x00" in raw:
        deny("PAYLOAD_OVERSIZED_OR_INVALID")
    try:
        raw.decode("utf-8")
    except UnicodeError:
        deny("PAYLOAD_OVERSIZED_OR_INVALID")
    digest = _exec_v1_sha256(raw)
    destination = enrollment.payload_root / (digest + ".ps1")
    if not _exec_v1_no_reparse(destination):
        deny("REPARSE_PATH_DENIED")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=enrollment.payload_root, delete=False) as output:
            temp = Path(output.name)
            _exec_v1_write_private(output, temp, raw, enrollment.owner_sid)
        # A hard link publishes all bytes at once and fails if the digest already exists.
        os.link(temp, destination)
    except FileExistsError:
        deny("PAYLOAD_ALREADY_STAGED")
    finally:
        if temp is not None:
            temp.unlink()
    _exec_v1_private_path(destination, enrollment.owner_sid)
    return digest


def _exec_v1_runner_temp() -> Path:
    return _EXEC_V1_RUNNER_ROOT / "_work/_temp"


def _exec_v1_event_path(value: str, ctx: HostOpsExecV1RuntimeContext) -> Path:
    from ai_commerce.hostops_v3.security import windows_acl

    event_path = Path(value)
    expected = _exec_v1_runner_temp() / "_github_workflow/event.json"
    workspace = _EXEC_V1_RUNNER_ROOT / "_work/ai-commerce/ai-commerce"
    if (event_path != expected or Path(ctx.env.get("GITHUB_EVENT_PATH", "")) != expected
            or Path(ctx.env.get("GITHUB_WORKSPACE", "")) != workspace
            or Path(__file__).absolute() != workspace / _EXEC_V1_SOURCE_PATH
            or not _exec_v1_no_reparse(event_path)):
        deny("PROTECTED_HOSTOPS_ONLY")
    if not event_path.is_file() or event_path.stat().st_nlink != 1:
        deny("EVENT_SHAPE_OR_STATE_DENIED")
    for path in (event_path, event_path.parent, event_path.parent.parent):
        windows_acl(path, {ctx.owner_sid, "S-1-5-18", "S-1-5-32-544"})
    return event_path


def _exec_v1_read_event(event_path: Path) -> dict:
    try:
        with event_path.open("rb") as stream:
            raw = stream.read(HOSTOPS_EXEC_V1_MAX_EVENT_BYTES + 1)
        if not 0 < len(raw) <= HOSTOPS_EXEC_V1_MAX_EVENT_BYTES:
            deny("PAYLOAD_OVERSIZED_OR_INVALID")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object_unique)
    except (OSError, ValueError, UnicodeError, RecursionError):
        deny("EVENT_SHAPE_OR_STATE_DENIED")


def _exec_v1_protected_dispatch(args, ctx: HostOpsExecV1RuntimeContext) -> dict:
    _verify_hostops_exec_v1_runtime(ctx, require_protected_workflow=True)
    event_path = _exec_v1_event_path(args.event or ctx.env.get("GITHUB_EVENT_PATH", ""), ctx)
    event = _exec_v1_read_event(event_path)
    parsed = parse_hostops_exec_v1_event(event)
    if not parsed["public_issue"] or parsed["request"]["payload_mode"] != "PRIVATE_DIGEST":
        deny("PUBLIC_ISSUE_RAW_SCRIPT_DENIED")
    request = parsed["request"]
    _exec_v1_check_oidc(ctx.env, request["request_digest"], action=args.action)
    root = _exec_v1_native_state_root(ctx.owner_sid)
    enrollment, key = _exec_v1_load_native(root, ctx)
    journal = HostOpsExecV1Journal(enrollment.journal_path, existing=True)
    approval = journal.source_approval(enrollment, key)

    def resolve(digest, policy):
        path = policy.payload_root / (digest + ".ps1")
        if path.exists() or not _exec_v1_no_reparse(path):
            _exec_v1_private_path(path, policy.owner_sid)
        return _default_hostops_exec_v1_payload_resolver(digest, policy)

    def run(script, timeout, limit):
        # Recheck the current policy just before the reserved job starts.
        current, _ = _exec_v1_load_native(root, ctx)
        if current != enrollment or journal.source_approval(current, key) != approval:
            deny("ENROLLMENT_PIN_MISMATCH")
        return _default_hostops_exec_v1_runner(script, timeout, limit)

    # The GitHub event and its proven job authorize minting the internal broker MAC.
    # The owner key, MAC and script never travel through GitHub.
    token = sign_hostops_exec_v1_request(request, key)
    broker = HostOpsExecV1Broker(
        enrollment=enrollment, journal=HostOpsExecV1Journal(
            enrollment.journal_path, existing=True, native_source=(key, approval),
        ),
        authenticator=make_hostops_exec_v1_hmac_authenticator(key),
        runtime_inspector=lambda: ctx, payload_resolver=resolve, runner=run, require_protected_workflow=True,
    )
    receipt = broker.handle_event(event, action=args.action, auth_token=token)
    # These statuses follow the broker's checkout and source approval checks.
    # Publish the verified run commit, even when approval came from an older commit.
    source_verified = receipt["status"] in ("PLANNED", "EXECUTED", "UNKNOWN_EFFECT")
    return {**receipt, "source_sha": ctx.env["GITHUB_SHA"] if source_verified else None}


def _exec_v1_attended_action(args, ctx: HostOpsExecV1RuntimeContext) -> dict:
    _verify_hostops_exec_v1_runtime(ctx, forbid_github_actions=True)
    if not sys.stdout.isatty() or sys.stdin.isatty() != (args.action != "stage"):
        deny("ATTENDED_OWNER_REQUIRED")
    root = _exec_v1_native_state_root(ctx.owner_sid)
    if args.action == "enroll":
        if root.exists():
            deny("ALREADY_ENROLLED_NO_OVERWRITE")
        return _exec_v1_enroll_native(root, ctx, args.source_sha)
    if args.action == "revoke":
        if not root.exists() and _exec_v1_no_reparse(root):
            deny("BLOCKED_NOT_ENROLLED")
        _exec_v1_private_path(root, ctx.owner_sid, directory=True)
        marker = root / _EXEC_V1_REVOKED_FILENAME
        if not _exec_v1_no_reparse(marker):
            deny("REPARSE_PATH_DENIED")
        try:
            journal_path = root / HOSTOPS_EXEC_V1_JOURNAL_FILENAME
            if journal_path.exists() or not _exec_v1_no_reparse(journal_path):
                _exec_v1_private_journal(journal_path, ctx.owner_sid)
                HostOpsExecV1Journal(journal_path, existing=True).latch_revocation()
        finally:
            try:
                with marker.open("x+b") as stream:
                    _exec_v1_write_private(stream, marker, b"revoked\n", ctx.owner_sid)
            except FileExistsError:
                _exec_v1_private_path(marker, ctx.owner_sid)
        return HostOpsExecV1Broker()._receipt(status="REVOKED", code="ENROLLMENT_REVOKED", readback_verified=True)
    enrollment, key = _exec_v1_load_native(root, ctx)
    journal = HostOpsExecV1Journal(enrollment.journal_path, existing=True)
    if args.action == "rotate-source":
        code = journal.rotate_source(enrollment, key, args.source_sha)
        return HostOpsExecV1Broker()._receipt(
            status="ENROLLED", code=code, enrolled=True, source_sha=args.source_sha, readback_verified=True,
        )
    approval = journal.source_approval(enrollment, key)
    _exec_v1_verify_source(None, enrollment_sha=enrollment.source_sha, approved_digest=approval[1] if approval else None)
    digest = _exec_v1_stage_native(enrollment, sys.stdin.buffer)
    receipt = HostOpsExecV1Broker()._receipt(status="STAGED", code="PAYLOAD_STAGED", enrolled=True,
                                             readback_verified=True)
    return validate_hostops_exec_v1_receipt({**receipt, "script_sha256": digest, "payload_mode": "PRIVATE_DIGEST"})


class _ExecV1ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        deny("REQUEST_INVALID")


def _exec_v1_receipt_file(output: Path, raw: bytes) -> None:
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw)
        os.replace(temp, output)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def hostops_exec_v1_cli(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    broker: HostOpsExecV1Broker | None = None,
) -> int:
    """Owner-only rotate-source requires the checked-out commit and authenticated enrollment."""
    parser = _ExecV1ArgumentParser(prog="hostops-exec-v1", add_help=False, allow_abbrev=False)
    parser.add_argument("--action", choices=("plan", "execute", "enroll", "rotate-source", "stage", "revoke"),
                        default="plan")
    parser.add_argument("--event")
    parser.add_argument("--source-sha")
    parser.add_argument("--output")
    current_env = os.environ if env is None else env
    fallback = HostOpsExecV1Broker()
    output = None
    try:
        args = parser.parse_args(argv)
        local = args.action in ("enroll", "rotate-source", "stage", "revoke")
        if ((args.source_sha is not None) != (args.action in ("enroll", "rotate-source"))
                or local and (args.event is not None or args.output is not None or broker is not None)):
            deny("REQUEST_INVALID")
        if broker is None and (_exec_v1_source_ci(current_env) or local and "GITHUB_ACTIONS" in current_env):
            deny("SOURCE_CI_EXECUTION_FORBIDDEN")
        if args.output is not None:
            candidate = Path(args.output).absolute()
            if ((broker is None and (candidate.parent != _exec_v1_runner_temp()
                                      or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.json", candidate.name)))
                    or not _exec_v1_no_reparse(candidate) or not candidate.parent.is_dir()
                    or candidate.exists() and not candidate.is_file()):
                deny("RECEIPT_OUTPUT_UNAVAILABLE")
            output = candidate
        if broker is not None:
            event = _exec_v1_read_event(Path(args.event or current_env.get("GITHUB_EVENT_PATH", "")))
            receipt = broker.handle_event(event, action=args.action,
                                          auth_token=current_env.get("HOSTOPS_EXEC_V1_AUTH_TOKEN"))
        else:
            ctx = _default_hostops_exec_v1_runtime(current_env)
            receipt = _exec_v1_attended_action(args, ctx) if local else _exec_v1_protected_dispatch(args, ctx)
        receipt = validate_hostops_exec_v1_receipt(receipt)
    except Denied as exc:
        receipt = fallback._error_receipt(str(exc))
    except (Exception, KeyboardInterrupt):
        receipt = fallback._error_receipt("PROTECTED_HOSTOPS_ONLY")
    raw = _exec_v1_canonical(receipt) + b"\n"
    try:
        if output is None:
            sys.stdout.write(raw.decode("ascii"))
        else:
            _exec_v1_receipt_file(output, raw)
    except (OSError, ValueError, KeyboardInterrupt):
        if output is not None:
            with suppress(OSError, ValueError):
                sys.stdout.write(raw.decode("ascii"))
        return 2
    if receipt["status"] in ("PLANNED", "ENROLLED", "REVOKED", "STAGED", "EXECUTED"):
        return 0
    return 2


_A10ReadinessState = Literal["SOURCE_PRESENT", "HOST_UNVERIFIED", "HOST_UNSUPPORTED", "HOST_READINESS_VERIFIED"]
# Historical comparison for the public receipt's version field.
_A10_VERSION_DIAGNOSTIC_BASELINE = "0.164.0"
_A10_CHECK_CODES = {
    "binary": ("OBSERVED", "NOT_FOUND_IN_SCOPE", "AMBIGUOUS", "UNSAFE"),
    "version": ("MATCHES_SOURCE_VERSION", "DIFFERS_FROM_SOURCE_VERSION"),
    "root_help": ("USAGE_OBSERVED",),
    "exec_help": ("USAGE_OBSERVED",),
    "root_flags": ("DECLARED", "NOT_DECLARED"),
    "exec_flags": ("DECLARED", "NOT_DECLARED"),
    "config_override_flags": ("DECLARED", "NOT_DECLARED"),
    "feature_disable_flag": ("DECLARED", "NOT_DECLARED"),
    "catalog": ("DEFAULT_CACHE_PRESENT", "DEFAULT_CACHE_ABSENT"),
    "system_config": ("CONFLICTING_POLICY_PRESENT", "UNSAFE"),
    "account_config": ("MISSING_PRIVATE_CONFIG", "CONFLICTING_PRIVATE_CONFIG", "UNSAFE"),
    "disabled_features": ("NOT_DECLARED", "UNSAFE"),
    "read_only_policy": ("NOT_DECLARED", "UNSAFE"),
}
_A10_HELP_OPTION = re.compile(r"(?m)^ {2,8}(?:-[A-Za-z], )?(--[a-z][a-z0-9-]*)(?=[ \t=,]|$)")
_A10_CONFIG_FLAGS = frozenset({"--config", "--ignore-user-config", "--strict-config"})
_A10_PROBE_ARGS = (("--version",), ("--help",), ("exec", "--help"))
_A10_PROBE_TIMEOUT = 5
_A10_PROBE_MAX_BYTES = 32768
_A10_PROBE_WARNING = re.compile(rb"\bWARN\b|^warning:", re.IGNORECASE)
_A10_SYSTEM_CONFIG_RELATIVE = (
    PureWindowsPath("OpenAI/Codex/config.toml"),
    PureWindowsPath("OpenAI/Codex/managed_config.toml"),
    PureWindowsPath("OpenAI/Codex/requirements.toml"),
)


def _a10_readiness_receipt(issue_number, source_sha=None, *, observations=None):
    """Public observation checksum only. A host-verification grant is separate."""
    if type(issue_number) is not int or not 0 < issue_number <= 2**53 - 1:
        deny("ISSUE_OWNER_OR_STATE_DENIED")
    if source_sha is not None or observations is not None:
        if type(source_sha) is not str or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            deny("PROTECTED_HOSTOPS_ONLY")
    observed = observations if type(observations) is dict else {}
    checks = dict.fromkeys(_A10_CHECK_CODES, "UNKNOWN")
    for field, allowed in _A10_CHECK_CODES.items():
        value = observed.get(field)
        if type(value) is str and value in allowed:
            checks[field] = value
    binary_sha = observed.get("observed_binary_sha256")
    if (checks["binary"] != "OBSERVED" or type(binary_sha) is not str
            or re.fullmatch(r"[0-9a-f]{64}", binary_sha) is None):
        binary_sha = None
        if checks["binary"] == "OBSERVED":
            checks["binary"] = "UNKNOWN"
    if checks["binary"] != "OBSERVED":
        for field in ("version", "root_help", "exec_help", "root_flags", "exec_flags",
                      "config_override_flags", "feature_disable_flag"):
            checks[field] = "UNKNOWN"
    if checks["root_help"] != "USAGE_OBSERVED":
        checks["root_flags"] = "UNKNOWN"
    if checks["exec_help"] != "USAGE_OBSERVED":
        for field in ("exec_flags", "config_override_flags", "feature_disable_flag"):
            checks[field] = "UNKNOWN"
    if checks["disabled_features"] == "NOT_DECLARED" and checks["feature_disable_flag"] != "NOT_DECLARED":
        checks["disabled_features"] = "UNKNOWN"
    if (checks["read_only_policy"] == "NOT_DECLARED"
            and checks["exec_flags"] != "NOT_DECLARED"
            and checks["config_override_flags"] != "NOT_DECLARED"):
        checks["read_only_policy"] = "UNKNOWN"
    if checks["disabled_features"] == "UNSAFE" and checks["binary"] != "UNSAFE":
        checks["disabled_features"] = "UNKNOWN"
    if checks["read_only_policy"] == "UNSAFE" and checks["binary"] != "UNSAFE":
        checks["read_only_policy"] = "UNKNOWN"

    state: _A10ReadinessState
    if observations is None:
        state = "SOURCE_PRESENT"
    else:
        # Help declarations cannot establish effective config, feature disable
        # behavior or sandbox enforcement. HOST_READINESS_VERIFIED is reserved.
        state = "HOST_UNVERIFIED"
    readiness = {
        "schema_version": _A10_READINESS_SCHEMA, "state": state, **checks,
        "observed_binary_sha256": binary_sha, "native_inference_executed": False,
    }
    # The future protected sanitizer must validate this exact envelope and
    # closed nested vocabulary independently, before any generic projection.
    result = {
        "status": "BLOCKED" if state == "SOURCE_PRESENT" else "HOLD",
        "code": "A10_PROTECTED_WORKFLOW_REQUIRED" if state == "SOURCE_PRESENT" else state,
        "operation": _A10_READINESS, "a10_readiness": readiness, "issue_number": issue_number,
        "source_sha": source_sha, "approval_channel": "CHATGPT_TO_AI_COMMERCE_ISSUE",
        "remote_shell_enabled": False,
    }
    digest = None
    if observations is not None:
        # Canonical sorted-key JSON binds the observed binary bytes by SHA-256
        # to this public projection, Issue and source. No private bytes are hashed.
        canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("ascii")
        digest = hashlib.sha256(canonical).hexdigest()
    readiness["public_observation_sha256"] = digest
    return result


def _a10_local_path(raw):
    """Recognize ordinary drive-letter paths without accessing the filesystem."""
    if (type(raw) is not str or len(raw) > 512
            or re.fullmatch(r'[a-zA-Z]:[\\/][^<>:"|?*\x00-\x1f]*', raw) is None):
        return None
    parts = re.split(r"[\\/]", raw[3:].rstrip("\\/"))
    if any(part.endswith((".", " ")) or re.fullmatch(
        r"(?i:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])", part.split(".")[0].rstrip()
    ) for part in parts):
        return None
    return PureWindowsPath(raw)


def _a10_install_candidates(env):
    # One native filename per bounded PATH entry, plus fixed layouts from the
    # npm distribution. Never use cwd, PATHEXT, or recursive search.
    search = env.get("PATH", "")
    if len(search) > 16384:
        search = search[:16384].rpartition(";")[0]
    candidates = {}
    for entry in search.split(";", 64)[:64]:
        if len(entry) >= 2 and entry[0] == entry[-1] == '"':
            entry = entry[1:-1]
        path = _a10_local_path(entry)
        if path is not None:
            candidates[path / "codex.exe"] = None
    app_data = _a10_local_path(env.get("APPDATA"))
    if app_data is not None:
        packages = app_data / "npm/node_modules/@openai"
        for arch, target in (("x64", "x86_64"), ("arm64", "aarch64")):
            platform_package = f"codex-win32-{arch}"
            for package in (packages / "codex", packages / platform_package,
                            packages / "codex/node_modules/@openai" / platform_package):
                for layout in ("codex", "bin"):
                    candidates[package / "vendor" / f"{target}-pc-windows-msvc" / layout / "codex.exe"] = None
    return tuple(candidates)


@contextmanager
def _a10_pin_local_path(path):
    """Hold the directory chain for a cache attribute check."""
    import ctypes
    from ctypes import wintypes

    class FileInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME), ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD), ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD), ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD),
        ]

    if path is None or _a10_local_path(str(path)) != path:
        raise OSError("A10_PATH_UNSAFE")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    drive_type = kernel.GetDriveTypeW
    drive_type.argtypes, drive_type.restype = (wintypes.LPCWSTR,), wintypes.UINT
    if drive_type(path.anchor) != 3:  # DRIVE_FIXED
        raise OSError("A10_PATH_UNSAFE")
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = (wintypes.HANDLE,), wintypes.BOOL
    handle_info = kernel.GetFileInformationByHandle
    handle_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(FileInfo))
    handle_info.restype = wintypes.BOOL
    with ExitStack() as handles:
        for item in reversed((path, *path.parents)):
            is_directory = item != path
            if is_directory:
                # Attribute-only opens do not enforce a Windows share lock.
                access = 0x81  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
            else:
                access = 0x80  # FILE_READ_ATTRIBUTES
            share = 1  # FILE_SHARE_READ only. Deny replacement and reparse mutation.
            # OPEN_EXISTING, OPEN_REPARSE_POINT | BACKUP_SEMANTICS.
            handle = create(str(item), access, share, None, 3, 0x02200000, None)
            if handle in (None, ctypes.c_void_p(-1).value):
                if ctypes.get_last_error() in (2, 3):
                    raise FileNotFoundError("A10_PATH_ABSENT")
                raise OSError("A10_PATH_UNSAFE")
            handles.callback(close, handle)
            info = FileInfo()
            if (not handle_info(handle, ctypes.pointer(info))
                    or info.attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DEVICE)
                    or bool(info.attributes & stat.FILE_ATTRIBUTE_DIRECTORY) != is_directory
                    or (not is_directory and info.links != 1)):
                raise OSError("A10_PATH_UNSAFE")
        yield Path(path)


def _a10_find_executable(candidates):
    from ai_commerce.opportunity.core.models import AI_MAX_BYTES, _native_binary_digest

    kind = "NOT_FOUND_IN_SCOPE"
    for candidate in candidates:
        try:
            executable = Path(candidate).resolve(strict=True)
            # Follow ordinary filesystem aliases to native Codex. A link to
            # some other filename is outside the scope of this observation.
            if executable.name.lower() != "codex.exe" or _a10_local_path(str(executable)) is None:
                kind = "UNSAFE"
                continue
            info = executable.stat()
            if (stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 512 * AI_MAX_BYTES
                    and _a10_is_native_executable(executable)):
                return "OBSERVED", executable, _native_binary_digest(executable)
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError, ValueError):
            # Includes symlink loops on older Python and the streaming hash limit.
            pass
        kind = "UNSAFE"
    return kind, None, None


def _a10_is_native_executable(path):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    binary_type = kernel.GetBinaryTypeW
    binary_type.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    binary_type.restype = wintypes.BOOL
    kind = wintypes.DWORD()
    # Win32 metadata classification only. SCS_32BIT_BINARY / SCS_64BIT_BINARY.
    return bool(binary_type(str(path), ctypes.byref(kind))) and kind.value in (0, 6)


def _a10_default_catalog_state(env=None):
    # Probe attributes of this one conventional cache on the runner account.
    # Effective CODEX_HOME, cache bytes, config and auth are all out of scope.
    current_env = os.environ if env is None else env
    profile = _a10_local_path(current_env.get("USERPROFILE"))
    if profile is not None:
        try:
            with _a10_pin_local_path(profile / ".codex/models_cache.json"):
                return "DEFAULT_CACHE_PRESENT"
        except FileNotFoundError:
            return "DEFAULT_CACHE_ABSENT"
        except OSError:
            pass
    return "UNKNOWN"


def _a10_probe_raw_text(probe, args):
    from ai_commerce.opportunity.core.models import OpportunityError

    try:
        result = probe(args)
        if (type(result.returncode) is int and result.returncode == 0
                and type(result.stderr) is bytes and len(result.stderr) <= _A10_PROBE_MAX_BYTES
                and type(result.stdout) is bytes and 0 < len(result.stdout) <= _A10_PROBE_MAX_BYTES):
            # Codex can emit warnings even for successful metadata commands.
            # Categorize only stdout and never include stderr in the observation.
            if result.stderr and (not result.stderr.strip() or any(
                not _A10_PROBE_WARNING.search(line) for line in result.stderr.splitlines() if line.strip()
            )):
                return b"", ""
            text = result.stdout.decode("utf-8").replace("\r\n", "\n")
            if not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", text):
                return result.stdout, text
    except (OpportunityError, OSError, subprocess.SubprocessError, UnicodeError, AttributeError):
        pass
    return b"", ""


def _a10_probe_text(probe, args):
    return _a10_probe_raw_text(probe, args)[1]


def _a10_help_options(text, *, exec_help):
    command = r"codex(?:\.exe)?" + (" exec" if exec_help else "")
    intro, options_header, options = text.partition("\nOptions:\n")
    if options_header and re.search(rf"(?m)^Usage: {command} \[OPTIONS\](?: [^\n]*)?$", intro):
        declarations = set(_A10_HELP_OPTION.findall(re.split(r"\n(?=\S)", options, maxsplit=1)[0]))
        if "--help" in declarations:
            return declarations
    return None


def _a10_observe_cli(probe, observations):
    from ai_commerce.opportunity.core import models

    version = _a10_probe_text(probe, ("--version",)).strip()
    if not re.fullmatch("codex-cli " + _HEALTH_VERSION, version, flags=re.ASCII):
        return
    observations["version"] = (
        "MATCHES_SOURCE_VERSION" if version == "codex-cli " + _A10_VERSION_DIAGNOSTIC_BASELINE
        else "DIFFERS_FROM_SOURCE_VERSION"
    )
    root_options = _a10_help_options(_a10_probe_text(probe, ("--help",)), exec_help=False)
    if root_options is not None:
        observations["root_help"] = "USAGE_OBSERVED"
        observations["root_flags"] = "DECLARED" if {"--config", "--disable"} <= root_options else "NOT_DECLARED"
    exec_options = _a10_help_options(_a10_probe_text(probe, ("exec", "--help")), exec_help=True)
    if exec_options is not None:
        selector = models.ai_provider_profile("codex-cli")
        # The source argv places every one of these flags after exec. A
        # declaration on root help alone gives no evidence for that placement.
        argv = models.codex_research_argv("codex.exe", model=selector["model"], effort=selector["effort"])
        required = {arg for arg in argv[2:] if arg.startswith("--")}
        observations["exec_help"] = "USAGE_OBSERVED"
        observations["exec_flags"] = "DECLARED" if required <= exec_options else "NOT_DECLARED"
        observations["config_override_flags"] = "DECLARED" if _A10_CONFIG_FLAGS <= exec_options else "NOT_DECLARED"
        observations["feature_disable_flag"] = "DECLARED" if "--disable" in exec_options else "NOT_DECLARED"


def _a10_verify_binary_unchanged(executable, observed_sha):
    from ai_commerce.opportunity.core.models import _native_binary_digest

    try:
        return (executable.resolve(strict=True) == executable
                and _a10_is_native_executable(executable)
                and _native_binary_digest(executable) == observed_sha)
    except (OSError, RuntimeError, ValueError):
        return False


@contextmanager
def _a10_isolated_cli(executable, observed_sha):
    from ai_commerce.opportunity.core.models import _bounded_native_codex

    with tempfile.TemporaryDirectory(prefix="a10-readiness-") as directory:
        def probe(args):
            if args not in _A10_PROBE_ARGS:
                deny("A10_PROBE_DENIED")
            # Recheck the resolved native file before every metadata process.
            if not _a10_verify_binary_unchanged(executable, observed_sha):
                deny("A10_PROBE_BINARY_CHANGED")
            return _bounded_native_codex(
                [str(executable), *args], input=b"", cwd=directory, codex_home=directory,
                shell=False, timeout=_A10_PROBE_TIMEOUT, max_bytes=_A10_PROBE_MAX_BYTES,
            )

        yield probe


def _a10_system_config_probe(env=None):
    """Metadata-only negative check for known Windows system Codex policy files.

    Never reads file bytes or emits paths. Absence of these known paths does not
    exhaustively prove absence of registry/MDM/version-specific policy, so clean
    absence remains UNKNOWN.
    """
    current_env = os.environ if env is None else env
    program_data = _a10_local_path(current_env.get("ProgramData"))
    if program_data is None:
        return {"status": "UNKNOWN", "basis": "NOT_CHECKED", "known_paths_checked": 0}
    checked = 0
    for rel in _A10_SYSTEM_CONFIG_RELATIVE:
        checked += 1
        try:
            with _a10_pin_local_path(program_data / rel):
                return {
                    "status": "CONFLICTING_POLICY_PRESENT",
                    "basis": "KNOWN_SYSTEM_POLICY_FILE_PRESENT",
                    "known_paths_checked": checked,
                }
        except FileNotFoundError:
            continue
        except OSError:
            return {
                "status": "UNSAFE",
                "basis": "SYSTEM_PATH_UNSAFE",
                "known_paths_checked": checked,
            }
    return {
        "status": "UNKNOWN",
        "basis": "KNOWN_SYSTEM_PATHS_ABSENT_NOT_EXHAUSTIVE",
        "known_paths_checked": checked,
    }


def _a10_collect_r7_internal_evidence(source_sha, *, as_of=None, env=None, max_age_seconds=3600):
    """Testable, non-emitting R7a internal effective-config evidence collector.

    Never emitted directly to GitHub before protected R7b sanitizer review.
    Retains dynamic stable semver acceptance, bounds system-config negative
    checks without reading secrets, rechecks binary digest after probing, and
    keeps every unprovable effective-config layer explicitly UNKNOWN.
    """
    from ai_commerce.opportunity.core import models

    if type(source_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        deny("PROTECTED_HOSTOPS_ONLY")
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if as_of is None else as_of
    current_env = os.environ if env is None else env
    system_config = _a10_system_config_probe(current_env)
    account_config = {"status": "UNKNOWN", "basis": "REMOTE_ACCOUNT_POLICY_UNPROVABLE_OFFLINE"}
    cli = dict.fromkeys(
        ("root_help", "root_flags", "exec_help", "exec_flags", "config_override_flags", "feature_disable_flag"),
        "UNKNOWN",
    )
    kind, executable, observed_sha = _a10_find_executable(_a10_install_candidates(current_env))
    binary = {
        "state": kind,
        "sha256": observed_sha if kind == "OBSERVED" else None,
        "version_status": "UNKNOWN",
        "version": None,
        "help_sha256": None,
    }
    if executable is not None:
        binary_drifted = False
        try:
            with _a10_isolated_cli(executable, observed_sha) as probe:
                version_line = _a10_probe_text(probe, ("--version",)).strip()
                if version_line.startswith("codex-cli "):
                    candidate_ver = version_line[len("codex-cli "):]
                    if (len(candidate_ver) <= 64
                            and re.fullmatch(models._NATIVE_CODEX_STABLE_VERSION, candidate_ver, flags=re.ASCII)):
                        binary["version_status"] = "STABLE_SEMVER"
                        binary["version"] = candidate_ver
                    elif "-" in candidate_ver and re.fullmatch(_HEALTH_VERSION, candidate_ver, flags=re.ASCII):
                        binary["version_status"] = "PRERELEASE_UNSUPPORTED"
                if binary["version_status"] != "UNKNOWN":
                    root_options = _a10_help_options(_a10_probe_text(probe, ("--help",)), exec_help=False)
                    if root_options is not None:
                        cli["root_help"] = "USAGE_OBSERVED"
                        cli["root_flags"] = "DECLARED" if {"--config", "--disable"} <= root_options else "NOT_DECLARED"
                    exec_raw, exec_text = _a10_probe_raw_text(probe, ("exec", "--help"))
                    exec_options = _a10_help_options(exec_text, exec_help=True)
                    if exec_options is not None:
                        required = set(models.native_codex_policy_contract()["required_exec_flags"])
                        binary["help_sha256"] = hashlib.sha256(exec_raw).hexdigest()
                        cli["exec_help"] = "USAGE_OBSERVED"
                        cli["exec_flags"] = "DECLARED" if required <= exec_options else "NOT_DECLARED"
                        cli["config_override_flags"] = "DECLARED" if _A10_CONFIG_FLAGS <= exec_options else "NOT_DECLARED"
                        cli["feature_disable_flag"] = "DECLARED" if "--disable" in exec_options else "NOT_DECLARED"
        except Denied as exc:
            if str(exc) == "A10_PROBE_BINARY_CHANGED":
                binary_drifted = True
        except (OSError, ValueError):
            pass
        if binary_drifted or not _a10_verify_binary_unchanged(executable, observed_sha):
            binary = {
                "state": "UNSAFE", "sha256": None,
                "version_status": "UNKNOWN", "version": None, "help_sha256": None,
            }
            cli = dict.fromkeys(cli, "UNKNOWN")
    return models.make_codex_effective_config_evidence(
        source_sha=source_sha, observed_at=observed_at, max_age_seconds=max_age_seconds,
        binary=binary, cli_declarations=cli, system_config=system_config, account_config=account_config,
        windows_boundary=(os.name == "nt"),
    )


def _a10_project_public_observations(
    evidence,
    *,
    catalog="UNKNOWN",
    source_sha=None,
    as_of=None,
    binary_sha256=None,
    version=None,
    help_sha256=None,
    policy_sha256=None,
    host_verification_sha256=None,
):
    """Validate R7a internal evidence and project negative-only public observations."""
    from ai_commerce.opportunity.core import models

    binding_requested = any(
        value is not None
        for value in (source_sha, as_of, binary_sha256, version, help_sha256, policy_sha256, host_verification_sha256)
    )
    if binding_requested:
        if type(source_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
            raise models.SignalValidationError("CODEX_EVIDENCE_SOURCE_MISMATCH")
        verify_as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if as_of is None else as_of
        validated = models.verify_codex_effective_config_binding(
            evidence,
            source_sha=source_sha,
            as_of=verify_as_of,
            binary_sha256=binary_sha256,
            version=version,
            help_sha256=help_sha256,
            policy_sha256=policy_sha256,
            host_verification_sha256=host_verification_sha256,
        )
    else:
        validated = models.validate_codex_effective_config_evidence(evidence)
        expected_policy = models.native_codex_policy_contract()["policy_sha256"]
        if validated["policy_sha256"] != expected_policy:
            raise models.SignalValidationError("CODEX_EVIDENCE_POLICY_MISMATCH")

    binary = validated["binary"]
    cli = validated["cli_declarations"]
    layers = validated["layers"]
    observations = {}
    if binary["state"] in _A10_CHECK_CODES["binary"]:
        observations["binary"] = binary["state"]

    sys_layer = layers["system_config"]
    if (sys_layer["status"] == "CONFLICTING_POLICY_PRESENT"
            and sys_layer["basis"] == "KNOWN_SYSTEM_POLICY_FILE_PRESENT"
            and sys_layer["known_paths_checked"] >= 1):
        observations["system_config"] = "CONFLICTING_POLICY_PRESENT"
    elif (sys_layer["status"] == "UNSAFE"
            and sys_layer["basis"] == "SYSTEM_PATH_UNSAFE"
            and sys_layer["known_paths_checked"] >= 1):
        observations["system_config"] = "UNSAFE"

    acct_layer = layers["account_config"]
    if acct_layer in (
        {"status": "MISSING_PRIVATE_CONFIG", "basis": "DEDICATED_HOME_AUTH_ABSENT"},
        {"status": "CONFLICTING_PRIVATE_CONFIG", "basis": "DEDICATED_HOME_FORBIDDEN_CONFIG_PRESENT"},
        {"status": "UNSAFE", "basis": "DEDICATED_HOME_UNSAFE"},
    ):
        observations["account_config"] = acct_layer["status"]

    disabled_layer = layers["disabled_features"]
    if (disabled_layer == {"status": "NOT_DECLARED", "basis": "DISABLE_FLAG_NOT_DECLARED"}
            and binary["state"] == "OBSERVED"
            and cli["exec_help"] == "USAGE_OBSERVED"
            and cli["feature_disable_flag"] == "NOT_DECLARED"):
        observations["disabled_features"] = "NOT_DECLARED"
    elif (disabled_layer == {"status": "UNSAFE", "basis": "BINARY_OR_PROBE_UNSAFE"}
            and binary["state"] == "UNSAFE"):
        observations["disabled_features"] = "UNSAFE"

    ro_layer = layers["read_only_policy"]
    if (ro_layer == {"status": "NOT_DECLARED", "basis": "SANDBOX_OR_CONFIG_FLAGS_NOT_DECLARED"}
            and binary["state"] == "OBSERVED"
            and cli["exec_help"] == "USAGE_OBSERVED"
            and (cli["exec_flags"] == "NOT_DECLARED" or cli["config_override_flags"] == "NOT_DECLARED")):
        observations["read_only_policy"] = "NOT_DECLARED"
    elif (ro_layer == {"status": "UNSAFE", "basis": "BINARY_OR_PROBE_UNSAFE"}
            and binary["state"] == "UNSAFE"):
        observations["read_only_policy"] = "UNSAFE"

    if binary["state"] != "OBSERVED":
        return observations

    observations["observed_binary_sha256"] = binary["sha256"]
    observations["catalog"] = catalog if catalog in _A10_CHECK_CODES["catalog"] else "UNKNOWN"
    if binary["version_status"] == "STABLE_SEMVER":
        observations["version"] = (
            "MATCHES_SOURCE_VERSION" if binary["version"] == _A10_VERSION_DIAGNOSTIC_BASELINE
            else "DIFFERS_FROM_SOURCE_VERSION"
        )
    elif binary["version_status"] == "PRERELEASE_UNSUPPORTED":
        observations["version"] = "DIFFERS_FROM_SOURCE_VERSION"
    for field, value in cli.items():
        if value != "UNKNOWN":
            observations[field] = value
    return observations


def _a10_collect_protected_observations(source_sha, *, as_of=None, env=None):
    """Collect R7a evidence once, verify binding, and project negative-only public fields."""
    from ai_commerce.opportunity.core import models

    try:
        kwargs = {}
        if as_of is not None:
            kwargs["as_of"] = as_of
        if env is not None:
            kwargs["env"] = env
        evidence = _a10_collect_r7_internal_evidence(source_sha, **kwargs)
        verify_as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if as_of is None else as_of
        policy_sha = models.native_codex_policy_contract()["policy_sha256"]
        validated = models.verify_codex_effective_config_binding(
            evidence, source_sha=source_sha, as_of=verify_as_of, policy_sha256=policy_sha,
        )
        binary = validated["binary"]
        catalog = _a10_default_catalog_state(env) if binary["state"] == "OBSERVED" else "UNKNOWN"
        return _a10_project_public_observations(
            validated,
            catalog=catalog,
            source_sha=source_sha,
            as_of=verify_as_of,
            binary_sha256=binary["sha256"] if binary["state"] == "OBSERVED" else None,
            version=binary["version"] if binary["version_status"] == "STABLE_SEMVER" else None,
            help_sha256=binary["help_sha256"],
            policy_sha256=policy_sha,
            host_verification_sha256=validated["evidence_sha256"],
        )
    except Exception:
        # Downgrade invalid, stale, drifted, or unprovable evidence to UNKNOWN/HOLD.
        return {}


def _a10_collect_readiness():
    try:
        kind, executable, observed_sha = _a10_find_executable(_a10_install_candidates(os.environ))
    except ImportError:
        return {}
    observations = {"binary": kind}
    if executable is None:
        return observations
    observations["observed_binary_sha256"] = observed_sha
    observations["catalog"] = _a10_default_catalog_state()
    try:
        with _a10_isolated_cli(executable, observed_sha) as probe:
            _a10_observe_cli(probe, observations)
    except (Denied, OSError, ValueError):
        pass  # Keep observations collected before a binary or temp failure.
    return observations


def _protected_a10_readiness(issue_number):
    try:
        _require_protected_clean_runner("issues")
        sha = _protected_clean_workflow_sha()
    except Denied:
        return _a10_readiness_receipt(issue_number)
    if ("PYTEST_CURRENT_TEST" in os.environ
            or os.environ.get("HOSTOPS_A10_READINESS_DISPATCH") != _A10_READINESS_SCHEMA):
        return _a10_readiness_receipt(issue_number, sha)
    return _a10_readiness_receipt(
        issue_number, sha, observations=_a10_collect_protected_observations(sha),
    )


def perform(request):
    op = request["operation"]
    params = request["parameters"]
    if op == _A10_READINESS:
        if type(params) is not dict or params:
            deny("INVALID_PARAMETERS")
        return _a10_readiness_receipt(request.get("issue_number"))
    if op in _CLEAN_TASK_WRITES:
        if type(params) is not dict or params:
            deny("INVALID_PARAMETERS")
        return _clean_autostart_receipt("PROTECTED_HOSTOPS_ONLY", operation=op)
    if op in _SUPERVISOR_CHAT_WRITES:
        deny("PROTECTED_HOSTOPS_ONLY")
    if op in (_SUPERVISOR_DEPLOY, _SUPERVISOR_RECOVER_HEALTH_ONLY, _SUPERVISOR_CONFIG_PROBE, _SUPERVISOR_TASK_PROBE):
        if type(params) is not dict or params:
            deny("INVALID_PARAMETERS")
        deny("PROTECTED_HOSTOPS_ONLY")
    if op == "cdp_9444_config_registry":
        if type(params) is not dict or params:
            deny("INVALID_PARAMETERS")
        return _cdp_9444_config_registry()
    if op == "cdp_9444_topology":
        if type(params) is not dict or params:
            deny("INVALID_PARAMETERS")
        return _cdp_9444_topology()
    if op == "standing_read_diagnostics":
        if type(params) is not dict or params:
            deny("INVALID_PARAMETERS")
        # Fail before collecting anything: the current protected public
        # workflow would silently discard diagnostics. Never launder them via
        # content_utf8 or claim an empty OBSERVED receipt is successful READ.
        if "diagnostics" not in PERSONAL_PUBLIC_FIELDS:
            deny("PUBLIC_TRANSPORT_GAP")
        snapshot = _validate_standing_snapshot(_standing_read_snapshot())
        if set(snapshot) - PERSONAL_PUBLIC_FIELDS:
            deny("PUBLIC_TRANSPORT_GAP")
        return snapshot
    if op == "git_sync_main":
        return _sync_main(params)
    if op == "runner_runtime_inventory":
        return _runner_runtime_inventory()
    if op == "cdp_9333_status":
        return _cdp_9333_status()
    if op == "inventory":
        return {
            "status": "OBSERVED", "operation": op,
            "windows": platform.system() == "Windows",
            "architecture": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
            "no_shell_command_executed": True,
        }
    if op == "git_snapshot":
        head = _git("rev-parse", "HEAD").strip()
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
        if not re.fullmatch(r"[a-f0-9]{40}", head):
            deny("GIT_HEAD_INVALID")
        changed = _git("status", "--porcelain=v1", "--untracked-files=no")
        return {
            "status": "OBSERVED", "operation": op, "git_head_sha": head,
            "branch": branch[:120], "tracked_changes": len(changed.splitlines()),
        }
    if op == "tracked_paths":
        prefix = params["prefix"]
        paths = _git("ls-files", "--", prefix).splitlines()
        # Paths originate from public git-tracked repository only.
        return {
            "status": "OBSERVED", "operation": op, "prefix": prefix,
            "paths": paths[:50], "total_tracked_in_scope": len(paths),
            "truncated": len(paths) > 50,
        }
    if op == "file_digest":
        path = _get_path(params["root_id"], params["relative_path"], write=True)
        if not path.is_file() or path.stat().st_size > MAX_READ:
            deny("DIGEST_FILE_MISSING_OR_TOO_LARGE")
        raw = path.read_bytes()
        if len(raw) > MAX_READ:
            deny("DIGEST_FILE_TOO_LARGE")
        return {
            "status": "OBSERVED", "operation": op, "root_id": params["root_id"],
            "relative_path": params["relative_path"], "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if op == "read_text":
        path = _get_path(params["root_id"], params["relative_path"])
        rel = params["relative_path"]
        _git("ls-files", "--error-unmatch", "--", rel)
        # A tracked path can contain uncommitted secrets: never post those bytes publicly.
        _git("diff", "--quiet", "HEAD", "--", rel)
        if not path.is_file():
            deny("READ_FILE_NOT_FOUND")
        if path.stat().st_size > MAX_READ:
            deny("READ_FILE_TOO_LARGE")
        try:
            raw = path.read_bytes()
            data = raw.decode("utf-8")
        except (OSError, UnicodeError):
            deny("READ_FILE_UNAVAILABLE")
        if len(raw) > MAX_READ:
            deny("READ_FILE_TOO_LARGE")
        return {
            "status": "OBSERVED", "operation": op,
            "root_id": params["root_id"], "relative_path": params["relative_path"],
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "content_utf8": data,
        }
    if op == "create_text":
        raw = params["content_utf8"].encode("utf-8")
        path = _get_path(params["root_id"], params["relative_path"], write=True)
        # Exclusive create is the replay guard. If a previous run crashed after
        # creation, a second attempt must HOLD instead of overwriting.
        try:
            with path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            deny("TARGET_ALREADY_EXISTS_NO_RETRY")
        except OSError:
            deny("WRITE_FAILED_CHECK_DISK_BEFORE_RETRY")
        try:
            observed = path.read_bytes()
        except OSError:
            deny("WRITE_READBACK_UNKNOWN_NO_RETRY")
        if observed != raw:
            deny("WRITE_READBACK_MISMATCH_NO_RETRY")
        return {
            "status": "OBSERVED", "operation": op,
            "root_id": params["root_id"], "relative_path": params["relative_path"],
            "bytes": len(observed), "sha256": hashlib.sha256(observed).hexdigest(),
            "overwritten": False,
        }
    deny("OPERATION_DENIED")


def main():
    if sys.argv[1:2] == ["--hostops-exec-v1"]:
        return hostops_exec_v1_cli(sys.argv[2:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("read", "write"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = {"status": "BLOCKED", "code": "UNKNOWN", "remote_shell_enabled": False}
    try:
        if os.name != "nt":
            deny("WINDOWS_ONLY")
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
            event = json.load(stream)
        request = parse_event(event, args.mode)
        # The runtime probe emits its own closed receipt with a validated SHA.
        if request["operation"] == _A10_READINESS:
            result = _protected_a10_readiness(request["issue_number"])
        elif request["operation"] == _CLEAN_RUNTIME_PROBE:
            result = _probe_protected_clean_runtime(event)
        elif request["operation"] == _CLEAN_1270_FORENSIC_READ:
            result = _probe_protected_clean_runtime(event, forensic=True)
        elif request["operation"] == _SUPERVISOR_DEPLOY:
            result = _deploy_protected_supervisor(event)
        elif request["operation"] == _SUPERVISOR_RECOVER_HEALTH_ONLY:
            result = _recover_protected_supervisor_health_only(event)
        elif request["operation"] in _SUPERVISOR_CHAT_WRITES:
            result = _enroll_protected_supervisor_chat(event)
        elif request["operation"] == _SUPERVISOR_CONFIG_PROBE:
            result = _probe_protected_supervisor_config(event)
        elif request["operation"] == _SUPERVISOR_TASK_PROBE:
            result = _probe_protected_supervisor_task(event)
        else:
            if request["operation"] == _CLEAN_AUTOSTART:
                outcome = _install_protected_clean_autostart(event)
            elif request["operation"] in (_CLEAN_DEMAND_MIGRATION, _CLEAN_TASK_RESET):
                outcome = _change_protected_clean_task(event, operation=request["operation"])
            else:
                outcome = perform(request)
            result = {
                **outcome,
                "issue_number": request["issue_number"],
                "approval_channel": ("CHATGPT_TO_AI_COMMERCE_ISSUE" if args.mode == "read"
                                     else "OWNER_GITHUB_COMMENT"),
                "remote_shell_enabled": False,
                "source_sha": os.environ.get("GITHUB_SHA", ""),
            }
            if (request["operation"] in _CLEAN_TASK_WRITES
                    and not re.fullmatch(r"[a-f0-9]{40}", result["source_sha"])):
                del result["source_sha"]
    except Denied as exc:
        result["code"] = str(exc)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        result["code"] = "RUN_ERROR_CHECK_STATE_NO_RETRY"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n",
                      encoding="utf-8")
    return 0 if result["status"] == "OBSERVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
