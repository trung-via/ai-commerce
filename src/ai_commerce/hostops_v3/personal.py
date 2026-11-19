"""Personal HostOps v3: bounded reads and individually approved fixed writes.

ChatGPT-approved READ sends only public committed source, safe git metadata or
file hashes to public Issues. A Human GitHub comment authorizes each WRITE.
No arbitrary shell, secret file access, force update, delete or provider effect.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from os.path import normpath
from pathlib import Path, PureWindowsPath
import platform
import re
import shutil
import stat
import subprocess
import sys
from threading import Lock
import time
from typing import Callable, Literal, TypedDict
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

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
_CLEAN_TASK_PENDING = "AutostartPending"
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
        if operation in ("inventory", "runner_runtime_inventory", "cdp_9333_status", "cdp_9444_topology",
                         "cdp_9444_config_registry", "standing_read_diagnostics"):
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
        if operation == _CLEAN_AUTOSTART:
            if params:
                deny("INVALID_PARAMETERS")
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
    "OPERATOR_OWNERSHIP_UNPROVEN", "PROFILE_UNAVAILABLE", "PROFILE_LOCKED",
    "PROFILE_ALREADY_ACTIVE_NO_CDP", "FOREIGN_PORT_OWNER", "BROWSER_OWNERSHIP_UNPROVEN",
    "CHROME_PATH_INVALID", "CHROME_EXECUTABLE_UNTRUSTED", "CDP_PROTOCOL_UNAVAILABLE",
    "SUPERVISOR_ACTIVE", "AUTOSTART_CONFLICT", "AUTOSTART_RUNTIME_UNAVAILABLE",
    "LOCAL_MAIN_SYNC_REQUIRED", "SOURCE_WORKFLOW_PATH_MISMATCH", "SOURCE_PATH_UNTRUSTED",
    "SOURCE_ACL_UNTRUSTED", "SOURCE_INDEX_UNVERIFIED", "SOURCE_UNTRACKED_FILE",
    "SOURCE_BYTES_MISMATCH", "WINDOWS_PROBE_INVALID", "WINDOWS_PROBE_UNAVAILABLE",
    "UNKNOWN_EFFECT", "CDP_VERIFIED", "INSTALLED_BUT_NOT_RUNNING",
})

# Public Git working-tree ACL guard: unlike private handoff/Chrome profile
# state, non-secret committed source under C:\TOOL\ai-commerce may grant
# read/execute to Users or Authenticated Users, provided only the operator,
# SYSTEM or local Administrators own or can modify it.
_CHECKOUT_READ_ONLY_MASK = (
    0x00000001  # FILE_READ_DATA / FILE_LIST_DIRECTORY
    | 0x00000008  # FILE_READ_EA
    | 0x00000020  # FILE_EXECUTE / FILE_TRAVERSE
    | 0x00000080  # FILE_READ_ATTRIBUTES
    | 0x00020000  # READ_CONTROL
    | 0x00100000  # SYNCHRONIZE
    | 0x20000000  # GENERIC_EXECUTE
    | 0x80000000  # GENERIC_READ
)
_INHERIT_ONLY_ACE = 0x08
_CHECKOUT_PUBLIC_READERS = frozenset({"S-1-5-32-545", "S-1-5-11"})
_CHECKOUT_EGG_INFO_FILES = frozenset({
    "PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt",
    "requires.txt", "top_level.txt", "not-zip-safe", "zip-safe",
})


def _clean_autostart_receipt(code):
    if type(code) is not str or code not in _CLEAN_PUBLIC_CODES:
        code = "AUTOSTART_RUNTIME_UNAVAILABLE"
    result = {
        "status": "OBSERVED" if code in {"CDP_VERIFIED", "INSTALLED_BUT_NOT_RUNNING"} else "HOLD",
        "code": code, "operation": _CLEAN_AUTOSTART, "cdp_port": 9444,
        "devtools_protocol_ok": code == "CDP_VERIFIED", "exact_chat_verified": False,
        "wake_sent": False, "arbitrary_shell_enabled": False,
    }
    if not result.keys() <= PERSONAL_PUBLIC_FIELDS:
        deny("PUBLIC_TRANSPORT_GAP")
    return result


def _protected_clean_autostart_source() -> str:
    # main() has already validated the exact authenticated event. This narrows
    # the non-TTY bridge to the protected main workflow's dedicated runner.
    expected = {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "issue_comment",
        "GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_OWNER": OWNER,
        "GITHUB_ACTOR": OWNER, "GITHUB_TRIGGERING_ACTOR": OWNER,
        "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/issue-ingress.yml@refs/heads/main",
        "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_NAME": "AI-COMMERCE-HOSTOPS-01",
    }
    sha = os.environ.get("GITHUB_SHA", "")
    if (os.name != "nt" or any(os.environ.get(key) != value for key, value in expected.items())
            or any(key.startswith("AI_COMMERCE_GIT_GUARD_") for key in os.environ)
            or not re.fullmatch(r"[a-f0-9]{40}", sha)
            or os.environ.get("GITHUB_WORKFLOW_SHA") != sha):
        deny("PROTECTED_HOSTOPS_ONLY")
    return sha


def _windows_checkout_acl(path: Path):
    """Read native Windows owner SID and (kind, flags, mask, sid) DACL tuples."""
    import ctypes
    from ctypes import wintypes as w

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, w.DWORD, w.DWORD,
                                          ctypes.POINTER(ptr), ptr, ctypes.POINTER(ptr),
                                          ptr, ctypes.POINTER(ptr)]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    adv.GetAclInformation.argtypes = [ptr, ptr, w.DWORD, ctypes.c_int]
    adv.GetAclInformation.restype = w.BOOL
    adv.GetAce.argtypes = [ptr, w.DWORD, ctypes.POINTER(ptr)]
    adv.GetAce.restype = w.BOOL
    adv.IsValidSid.argtypes = [ptr]
    adv.IsValidSid.restype = w.BOOL
    adv.GetLengthSid.argtypes = [ptr]
    adv.GetLengthSid.restype = w.DWORD
    kernel.LocalFree.argtypes = [ptr]
    kernel.LocalFree.restype = ptr

    def sid_text(sid):
        text = w.LPWSTR()
        if not sid or not adv.IsValidSid(sid) or not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            deny("SOURCE_ACL_UNTRUSTED")
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ptr))

    owner, dacl, descriptor = ptr(), ptr(), ptr()
    if adv.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                 ctypes.byref(dacl), None, ctypes.byref(descriptor)):
        deny("SOURCE_ACL_UNTRUSTED")
    try:
        if not dacl:
            deny("SOURCE_ACL_UNTRUSTED")
        info = (w.DWORD * 3)()
        if not adv.GetAclInformation(dacl, info, ctypes.sizeof(info), 2) or not 1 <= info[0] <= 128:
            deny("SOURCE_ACL_UNTRUSTED")
        aces = []
        for index in range(info[0]):
            ace = ptr()
            if not adv.GetAce(dacl, index, ctypes.byref(ace)):
                deny("SOURCE_ACL_UNTRUSTED")
            header = ctypes.string_at(ace, 8)
            ace_type, ace_flags = header[0], header[1]
            size = int.from_bytes(header[2:4], "little")
            mask = int.from_bytes(header[4:8], "little")
            if ace_type not in (0, 1) or size < 16:
                deny("SOURCE_ACL_UNTRUSTED")
            sid = ptr(ace.value + 8)
            if not adv.IsValidSid(sid) or adv.GetLengthSid(sid) > size - 8:
                deny("SOURCE_ACL_UNTRUSTED")
            aces.append(("allow" if ace_type == 0 else "deny", ace_flags, mask, sid_text(sid)))
        return sid_text(owner), tuple(aces)
    finally:
        kernel.LocalFree(descriptor)


def _check_public_checkout_acl(owner, aces, principal):
    """Allow ordinary read-only checkout ACLs while refusing untrusted writers."""
    trusted_owners = {principal, "S-1-5-18", "S-1-5-32-544"}
    trusted_writers = trusted_owners | {"S-1-3-4"}
    if owner not in trusted_owners or not aces or len(aces) > 128:
        deny("SOURCE_ACL_UNTRUSTED")
    operator_allowed = False
    for entry in aces:
        if type(entry) is not tuple or len(entry) != 4:
            deny("SOURCE_ACL_UNTRUSTED")
        kind, flags, mask, sid = entry
        if (kind not in {"allow", "deny"} or type(flags) is not int
                or type(mask) is not int or type(sid) is not str):
            deny("SOURCE_ACL_UNTRUSTED")
        if kind != "allow":
            continue
        inherit_only = bool(flags & _INHERIT_ONLY_ACE)
        if sid in trusted_writers:
            if (not inherit_only and (sid == principal or (sid == "S-1-3-4" and owner == principal))
                    and (mask & 0x90000001)):
                operator_allowed = True
            continue
        if sid == "S-1-3-0" and inherit_only:
            continue
        if sid in _CHECKOUT_PUBLIC_READERS and (mask & ~_CHECKOUT_READ_ONLY_MASK) == 0:
            if not inherit_only and (mask & 0x80000001):
                operator_allowed = True
            continue
        deny("SOURCE_ACL_UNTRUSTED")
    if not operator_allowed:
        deny("SOURCE_ACL_UNTRUSTED")


def _verify_checkout_acl(path: Path, sid: str):
    """Verify non-link checkout custody without requiring private-only read ACL."""
    try:
        info = path.lstat()
    except OSError:
        deny("SOURCE_PATH_UNTRUSTED")
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
        deny("SOURCE_PATH_UNTRUSTED")
    if os.name != "nt":
        deny("SOURCE_ACL_UNTRUSTED")
    try:
        owner, aces = _windows_checkout_acl(path)
    except Denied:
        raise
    except (OSError, ValueError):
        deny("SOURCE_ACL_UNTRUSTED")
    _check_public_checkout_acl(owner, aces, sid)


def _persistent_clean_source(acquisition, sha, sid):
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
        _verify_checkout_acl(path, sid)
    try:
        top = Path(_git("rev-parse", "--show-toplevel").strip())
        if not top.is_absolute() or not acquisition._no_links(top) or top.resolve(strict=True) != root.resolve(strict=True):
            deny("LOCAL_MAIN_SYNC_REQUIRED")
        if (_git("rev-parse", "--abbrev-ref", "HEAD").strip() != "main"
                or _git("config", "--get", "remote.origin.url").strip() not in TRUSTED_ORIGIN_URLS
                or _git("rev-parse", "HEAD", "refs/remotes/origin/main").splitlines() != [sha, sha]):
            deny("LOCAL_MAIN_SYNC_REQUIRED")
        status_lines = [line for line in _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
                        if line.strip()]
    except (OSError, ValueError, Denied):
        deny("LOCAL_MAIN_SYNC_REQUIRED")
    if status_lines:
        if all(line.startswith("?? ") for line in status_lines):
            deny("SOURCE_UNTRACKED_FILE")
        deny("LOCAL_MAIN_SYNC_REQUIRED")
    # Compare source bytes with the protected SHA checkout. This also
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
    try:
        for folder, dirs, files in os.walk(src_root, onerror=walk_error):
            parent = Path(folder)
            for name in (*dirs, *files):
                path = parent / name
                if _is_reparse(path):
                    deny("SOURCE_PATH_UNTRUSTED")
                _verify_checkout_acl(path, sid)
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
                if not pinned.is_file() or path.read_bytes() != pinned.read_bytes():
                    deny("SOURCE_BYTES_MISMATCH")
                tracked.remove(path)
    except Denied:
        raise
    except (OSError, ValueError):
        deny("SOURCE_PATH_UNTRUSTED")
    if tracked:
        deny("SOURCE_UNTRACKED_FILE")
    return source


def _install_protected_clean_autostart(event):
    # Check the public contract before any native call, even on a HOLD.
    _clean_autostart_receipt("PROTECTED_HOSTOPS_ONLY")
    reserved = False
    try:
        sha = _protected_clean_autostart_source()
        if parse_event(event, "write")["operation"] != _CLEAN_AUTOSTART:
            deny("PROTECTED_HOSTOPS_ONLY")
        from ai_commerce.remote import chrome_acquisition as acquisition
        owner = acquisition._native_operator()
        local, session, sid = owner
        source = _persistent_clean_source(acquisition, sha, sid)
        runtime = acquisition._managed_clean_runtime(local, sid, source)

        def inspect_target():
            if acquisition._native_operator() != owner:
                deny("OPERATOR_OWNERSHIP_UNPROVEN")
            target, candidates = acquisition._clean_target(local, sid)
            state, identity = acquisition._clean_inventory(target, candidates, session, sid)
            if state == "STARTING":
                deny("PROFILE_ALREADY_ACTIVE_NO_CDP")
            if acquisition._clean_launch_pending():
                deny("UNKNOWN_EFFECT")
            if state == "ABSENT":
                acquisition._clean_require_unlocked(target)
            acquisition._verify_google_chrome(target if identity is None else
                                              replace(target, executable=identity[1]))

        if acquisition._clean_pending(_CLEAN_TASK_PENDING):
            deny("UNKNOWN_EFFECT")
        inspect_target()
        task = acquisition._clean_task(sid, install=False, runtime=runtime)
        if task not in {"ALREADY_ENROLLED", "AUTOSTART_NOT_ENROLLED"}:
            return _clean_autostart_receipt(task)
        created = None
        if task == "AUTOSTART_NOT_ENROLLED":
            with acquisition._clean_supervisor_slot():
                if acquisition._clean_pending(_CLEAN_TASK_PENDING):
                    deny("UNKNOWN_EFFECT")
                inspect_target()
                reserved = True
                acquisition._clean_record_pending(_CLEAN_TASK_PENDING, True)
                created = acquisition._clean_task(sid, install=True, runtime=runtime)
                if created not in {"ENROLLED", "ALREADY_ENROLLED"}:
                    if created == "AUTOSTART_CONFLICT":
                        acquisition._clean_record_pending(_CLEAN_TASK_PENDING, False)
                        return _clean_autostart_receipt(created)
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                if acquisition._clean_task(sid, install=False, runtime=runtime) != "ALREADY_ENROLLED":
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                inspect_target()
        # Release the shared slot before Task Scheduler starts the same monitor.
        # The monitor must independently acquire it and use LaunchPending.
        started = False
        if reserved:
            if created == "ENROLLED":
                if acquisition._native_operator() != owner:
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                start = acquisition._clean_task(sid, install=False, runtime=runtime, start_session=session)
                if start not in {"START_REQUESTED", "START_NOT_ATTEMPTED"}:
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                started = start == "START_REQUESTED"
            acquisition._clean_record_pending(_CLEAN_TASK_PENDING, False)
            reserved = False
        target, candidates = acquisition._clean_target(local, sid)
        deadline = time.monotonic() + (20 if started else 0)
        while True:
            state, identity = acquisition._clean_inventory(target, candidates, session, sid)
            if state == "OWNED":
                if acquisition._clean_launch_pending():
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                observed = replace(target, executable=identity[1])
                acquisition._verify_google_chrome(observed)
                acquisition._probe_cdp(observed)
                if (acquisition._clean_inventory(target, candidates, session, sid) != (state, identity)
                        or acquisition._native_operator() != owner):
                    return _clean_autostart_receipt("UNKNOWN_EFFECT")
                return _clean_autostart_receipt("CDP_VERIFIED")
            if time.monotonic() >= deadline:
                return _clean_autostart_receipt("UNKNOWN_EFFECT" if state != "ABSENT"
                                               or acquisition._clean_launch_pending()
                                               else "INSTALLED_BUT_NOT_RUNNING")
            time.sleep(0.5)
    except (ValueError, Denied) as exc:
        return _clean_autostart_receipt("UNKNOWN_EFFECT" if reserved else str(exc))
    except Exception:
        return _clean_autostart_receipt("UNKNOWN_EFFECT" if reserved else "AUTOSTART_RUNTIME_UNAVAILABLE")


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
    try:
        # No user-provided tokens, paths, script, arguments or shell=True.
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=25, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        deny("RUNNER_RUNTIME_READ_UNAVAILABLE")
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
    try:
        native = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=18, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        deny("CDP_NATIVE_READ_UNAVAILABLE")
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
    """Attest the fixed clean profile or an independent legacy config.

    No Issue/environment identity, manifest-selected path, enrollment or launch.
    Failure to attest is UNKNOWN, never evidence that the listener is absent.
    """
    from ai_commerce.remote.local_completion_handoff import _private_identity
    from ai_commerce.remote.local_wake_entry import _windows_process_sid

    local = acquisition._native_local_app_data()
    session, sid = acquisition._interactive_session(), _windows_process_sid()
    if (type(session) is not int or session <= 0 or type(sid) is not str
            or not re.fullmatch(r"S-1-(?:\d+-){1,14}\d+", sid)
            or sid in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}):
        deny("OWNER_SESSION_UNATTESTED")
    root = local / "AI-Commerce"
    if (not local.is_absolute() or _fixed_private_metadata_kind(root, directory=True) != "PRESENT"
            or _private_identity(root) != sid):
        deny("OWNER_SESSION_UNATTESTED")
    directory = acquisition._clean_switch(argv, "--user-data-dir")
    clean = root / acquisition._CLEAN_9444_PROFILE
    if directory is not None and Path(normpath(directory)) == clean:
        return acquisition._clean_target(local, sid), session, sid
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


def perform(request):
    op = request["operation"]
    params = request["parameters"]
    if op == _CLEAN_AUTOSTART:
        if type(params) is not dict or params:
            deny("INVALID_PARAMETERS")
        return _clean_autostart_receipt("PROTECTED_HOSTOPS_ONLY")
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
        # The protected Issue ingress independently validates and reconstructs
        # the closed six-slot public receipt before posting any result.
        outcome = (_install_protected_clean_autostart(event) if request["operation"] == _CLEAN_AUTOSTART
                   else perform(request))
        result = {
            **outcome,
            "issue_number": request["issue_number"],
            "approval_channel": ("CHATGPT_TO_AI_COMMERCE_ISSUE" if args.mode == "read"
                                 else "OWNER_GITHUB_COMMENT"),
            "remote_shell_enabled": False,
            "source_sha": os.environ.get("GITHUB_SHA", ""),
        }
        if request["operation"] == _CLEAN_AUTOSTART and not re.fullmatch(r"[a-f0-9]{40}", result["source_sha"]):
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
