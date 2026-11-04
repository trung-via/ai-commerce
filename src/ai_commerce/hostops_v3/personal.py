"""Personal HostOps v3: bounded reads, new-file writes and attended git-main fast-forward.

ChatGPT-approved READ sends only public committed source, safe git metadata or
file hashes to public Issues. A Human GitHub comment authorizes each WRITE.
No arbitrary shell, secret file access, force update, delete or provider effect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
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
        if (comment.get("user", {}).get("login") != OWNER or
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
        if operation in ("inventory", "runner_runtime_inventory", "cdp_9333_status"):
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
        if operation == "git_sync_main":
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


def perform(request):
    op = request["operation"]
    params = request["parameters"]
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
        outcome = perform(request)
        result = {
            **outcome,
            "issue_number": request["issue_number"],
            "approval_channel": ("CHATGPT_TO_AI_COMMERCE_ISSUE" if args.mode == "read"
                                 else "OWNER_GITHUB_COMMENT"),
            "remote_shell_enabled": False,
            "source_sha": os.environ.get("GITHUB_SHA", ""),
        }
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
