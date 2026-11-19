"""Explicit, operator-owned Chrome CDP acquisition for AI Commerce EF02-D5.

Standalone preflight/enrollment/launch. Never reads chats, copies profiles, sends Doorbells,
activates a worker, or imports the AIOS-Renew runtime.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from os.path import normpath
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}\Z")
_MARKERS = ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")
_PROBE_SCRIPT = r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$ErrorActionPreference = 'Stop'
$portNumber = [int]$env:AI_COMMERCE_ACQ_PROBE_PORT
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Where-Object { $_.LocalPort -eq $portNumber } |
    Select-Object LocalAddress, LocalPort, OwningProcess)
$processes = @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction Stop |
    ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction Stop
        [pscustomobject]@{ ProcessId=$_.ProcessId; ExecutablePath=$_.ExecutablePath;
            CommandLine=$_.CommandLine; SessionId=$_.SessionId; OwnerSid=$owner.Sid;
            CreationDate=$_.CreationDate.ToUniversalTime().ToString('o') }
    })
@{listeners=$listeners; processes=$processes} | ConvertTo-Json -Depth 5 -Compress
"""


class AcquisitionBlocked(ValueError):
    """Only short, non-sensitive diagnostic codes are exposed."""


@dataclass(frozen=True)
class _ChromeTarget:
    executable: Path
    user_data_dir: Path
    profile_directory: str
    endpoint: str
    port: int

    def command(self) -> list[str]:
        return [str(self.executable),
                f"--user-data-dir={self.user_data_dir}",
                f"--profile-directory={self.profile_directory}",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={self.port}",
                "--no-first-run", "--no-default-browser-check"]


@dataclass(frozen=True)
class ChromeEnvironment(_ChromeTarget):
    allow_launch: bool
    config_path: Path


def _no_links(path: Path) -> bool:
    """Do not follow a link in any existing ancestor of a trusted path."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
            if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                return False
        except FileNotFoundError:
            continue
        except OSError:
            return False
    return True


def _inside(path: Path, parent: Path) -> bool:
    return path != parent and parent in path.parents


def load_environment(config_path: Path, checkout: Path, *, local_app_data: Path) -> ChromeEnvironment:
    """Require an existing, dedicated AI Commerce profile and private external config.

    This never provisions a profile, attempts authentication or reads cookies.
    """
    try:
        if (not all(p.is_absolute() for p in (config_path, checkout, local_app_data))
                or not config_path.is_file() or not checkout.is_dir()
                or not _no_links(config_path) or not _no_links(checkout)):
            raise AcquisitionBlocked("CONFIG_PATH_INVALID")
        checkout = checkout.resolve(strict=True)
        if _inside(config_path.resolve(strict=True), checkout):
            raise AcquisitionBlocked("CONFIG_IN_REPOSITORY")
        owned_config_root = (local_app_data.resolve(strict=True) / "AI-Commerce").resolve()
        if not _inside(config_path.resolve(strict=True), owned_config_root):
            raise AcquisitionBlocked("CONFIG_NOT_OPERATOR_OWNED")
        if config_path.stat().st_size > 8192:
            raise AcquisitionBlocked("CONFIG_INVALID")
        raw = config_path.read_text(encoding="utf-8-sig")
        def unique(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise AcquisitionBlocked("CONFIG_DUPLICATE_KEY")
                obj[key] = value
            return obj
        value = json.loads(raw, object_pairs_hook=unique)
        return _environment_from_value(value, config_path, checkout, local_app_data=local_app_data)
    except AcquisitionBlocked:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise AcquisitionBlocked("CONFIG_INVALID") from None


def _environment_from_value(value, config_path: Path, checkout: Path, *,
                            local_app_data: Path) -> ChromeEnvironment:
    """Shared closed schema/path validation; this helper never creates a config.

    load_environment additionally requires an existing private config. An
    enrollment assessment can validate an in-memory candidate with these same
    profile rules without weakening that existing-file requirement.
    """
    try:
        if (not isinstance(value, dict) or set(value) != {
                "version", "executable", "user_data_dir", "profile_directory",
                "cdp_endpoint", "allow_launch", "exclusive_user_data"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["allow_launch"]) is not bool
                or value["exclusive_user_data"] is not True
                or type(value["profile_directory"]) is not str
                or not _PROFILE.fullmatch(value["profile_directory"])):
            raise AcquisitionBlocked("CONFIG_INVALID")
        if type(value["executable"]) is not str or type(value["user_data_dir"]) is not str:
            raise AcquisitionBlocked("CONFIG_INVALID")
        executable, directory = Path(value["executable"]), Path(value["user_data_dir"])
        if (not executable.is_absolute() or not executable.is_file()
                or executable.name.lower() != "chrome.exe" or not _no_links(executable)
                or not directory.is_absolute() or not directory.is_dir()
                or not _no_links(directory)):
            raise AcquisitionBlocked("CHROME_PATH_INVALID")
        executable, directory = executable.resolve(strict=True), directory.resolve(strict=True)
        owned_base = (local_app_data.resolve(strict=True) / "AI-Commerce").resolve()
        # Never operate on Google default User Data, AIOS-Renew or a sibling project.
        if (not _inside(directory, owned_base) or _inside(directory, checkout)
                or directory == executable or directory in executable.parents):
            raise AcquisitionBlocked("PROFILE_NOT_DEDICATED")
        profile = directory / value["profile_directory"]
        if not profile.is_dir() or not _no_links(profile) or profile.resolve() != profile:
            raise AcquisitionBlocked("PROFILE_UNAVAILABLE")
        endpoint = value["cdp_endpoint"]
        if type(endpoint) is not str:
            raise AcquisitionBlocked("CDP_ENDPOINT_INVALID")
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "http" or parsed.netloc != f"127.0.0.1:{parsed.port}"
                or parsed.port is None or parsed.port < 1024
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise AcquisitionBlocked("CDP_ENDPOINT_INVALID")
        if _inside(config_path.resolve(), directory):
            raise AcquisitionBlocked("CONFIG_IN_CHROME_PROFILE")
        return ChromeEnvironment(executable, directory, value["profile_directory"],
                                 endpoint, parsed.port, value["allow_launch"], config_path.resolve())
    except AcquisitionBlocked:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise AcquisitionBlocked("CONFIG_INVALID") from None


def _switch(args: list[str], name: str) -> str | None:
    values = [arg[len(name) + 1:] for arg in args if arg.startswith(name + "=")]
    return values[0] if len(values) == 1 else None


def _commandline_argv(commandline: str) -> list[str]:
    """Use Windows CommandLineToArgvW rather than shell or POSIX shlex."""
    if os.name != "nt" or not isinstance(commandline, str) or not commandline:
        raise AcquisitionBlocked("PROCESS_IDENTITY_UNKNOWN")
    import ctypes
    from ctypes import wintypes
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    argc = ctypes.c_int()
    shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    ptr = shell32.CommandLineToArgvW(commandline, ctypes.byref(argc))
    if not ptr or argc.value <= 0 or argc.value > 256:
        raise AcquisitionBlocked("PROCESS_IDENTITY_UNKNOWN")
    try:
        return [ptr[i] for i in range(argc.value)]
    finally:
        kernel32.LocalFree(ptr)


def _identity_matches(env: _ChromeTarget, exe: str, argv: list[str]) -> bool:
    try:
        return (Path(exe).resolve(strict=True) == env.executable
                and Path(argv[0]).resolve(strict=True) == env.executable
                and Path(_switch(argv, "--user-data-dir") or "").resolve(strict=True) == env.user_data_dir
                and _switch(argv, "--profile-directory") == env.profile_directory
                and _switch(argv, "--remote-debugging-address") == "127.0.0.1"
                and _switch(argv, "--remote-debugging-port") == str(env.port)
                and not any(arg.startswith("--type=") for arg in argv))
    except (OSError, ValueError, IndexError):
        return False


# Never inherit provider tokens into a browser or diagnostic subprocess.
_CHILD_ENV_KEYS = frozenset({
    "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATH",
    "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
    "PROCESSOR_ARCHITECTURE",
})


def _child_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if key.upper() in _CHILD_ENV_KEYS}


def _powershell() -> Path:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    exe = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not exe.is_file():
        raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
    return exe


def _verify_google_chrome(env: _ChromeTarget) -> None:
    """Verify an authenticode-signed Google Chrome executable, not any .exe."""
    if env.executable.name.lower() != "chrome.exe":
        raise AcquisitionBlocked("CHROME_EXECUTABLE_UNTRUSTED")
    child_env = _child_environment()
    child_env["AI_COMMERCE_ACQ_EXE"] = str(env.executable)
    script = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
        "$s=Get-AuthenticodeSignature -LiteralPath $env:AI_COMMERCE_ACQ_EXE; "
        "if($s.Status -ne 'Valid' -or !$s.SignerCertificate -or "
        "$s.SignerCertificate.Subject -notmatch '(^|, )CN=Google (LLC|Inc)(,|$)') "
        "{exit 1}; Write-Output 'SIGNED_GOOGLE_CHROME'"
    )
    try:
        result = subprocess.run(
            [str(_powershell()), "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=8, check=False, env=child_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or result.stdout.strip() != b"SIGNED_GOOGLE_CHROME":
            raise AcquisitionBlocked("CHROME_EXECUTABLE_UNTRUSTED")
    except AcquisitionBlocked:
        raise
    except (OSError, subprocess.TimeoutExpired):
        raise AcquisitionBlocked("CHROME_EXECUTABLE_UNTRUSTED") from None


def _require_operator_control(env: ChromeEnvironment) -> None:
    """Apply the existing exact-SID, owner and ACL guard to private inputs."""
    from .local_completion_handoff import HandoffBlocked, _private_identity
    try:
        for path in (env.config_path, env.user_data_dir, env.user_data_dir / env.profile_directory):
            _private_identity(path)
    except (HandoffBlocked, OSError, ValueError):
        raise AcquisitionBlocked("OPERATOR_OWNERSHIP_UNPROVEN") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _probe_cdp(env: _ChromeTarget) -> None:
    """Bounded read-only HTTP protocol test, never a tab/CDP websocket attach."""
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        request = Request(env.endpoint.rstrip("/") + "/json/version",
                          headers={"Accept": "application/json"})
        with opener.open(request, timeout=2) as reply:
            if reply.status != 200:
                raise AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")
            body = reply.read(8193)
        if len(body) > 8192:
            raise AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")
        browser = payload.get("Browser")
        protocol = payload.get("Protocol-Version")
        ws = payload.get("webSocketDebuggerUrl")
        if (not isinstance(browser, str) or
                not (browser.startswith("Chrome/") or browser.startswith("HeadlessChrome/"))
                or not isinstance(protocol, str) or
                re.fullmatch("[0-9]+[.][0-9]+", protocol) is None
                or not isinstance(ws, str)):
            raise AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")
        ws_url = urlsplit(ws)
        if (ws_url.scheme != "ws" or ws_url.hostname not in {"localhost", "127.0.0.1"}
                or ws_url.port != env.port or ws_url.username or ws_url.password
                or not ws_url.path.startswith("/devtools/browser/")
                or not ws_url.path[len("/devtools/browser/"):]
                or ws_url.query or ws_url.fragment):
            raise AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")
    except AcquisitionBlocked:
        raise
    except Exception:
        raise AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE") from None


def _query_windows(port: int) -> dict:
    """Bounded, read-only Windows process and TCP inventory; never logs raw data."""
    powershell = _powershell()
    child_env = _child_environment()
    child_env["AI_COMMERCE_ACQ_PROBE_PORT"] = str(port)
    try:
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                                 _PROBE_SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=8, check=False, env=child_env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or len(result.stdout) > 262144:
            raise AcquisitionBlocked("WINDOWS_PROBE_UNAVAILABLE")
        value = json.loads(result.stdout.decode("utf-8-sig"))
        if not isinstance(value, dict) or set(value) != {"listeners", "processes"}:
            raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
        return value
    except AcquisitionBlocked:
        raise
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
        raise AcquisitionBlocked("WINDOWS_PROBE_UNAVAILABLE") from None


def _rows(value) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
    return value


def evaluate(env: ChromeEnvironment, inventory: dict, *, parse=_commandline_argv,
             session_id: int, expected_sid: str | None = None) -> tuple[str, int | None]:
    """Return ABSENT or OWNED only with nonambiguous loopback/process evidence."""
    try:
        listeners = _rows(inventory["listeners"])
        processes = _rows(inventory["processes"])
        if len(listeners) > 1 or len(processes) > 256:
            raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        owners = {p["ProcessId"]: p for p in processes if type(p.get("ProcessId")) is int}
        claimed = []
        for proc in processes:
            commandline = proc.get("CommandLine")
            if not isinstance(commandline, str) or not commandline:
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            argv = parse(commandline)
            if any(arg.startswith("--type=") for arg in argv):
                continue
            profile_args = [arg for arg in argv if arg.lower().startswith("--user-data-dir")]
            if profile_args and (len(profile_args) != 1 or
                                 not profile_args[0].startswith("--user-data-dir=")):
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            path_arg = _switch(argv, "--user-data-dir")
            if path_arg:
                try:
                    if Path(path_arg).resolve(strict=True) == env.user_data_dir:
                        claimed.append(proc)
                except OSError:
                    raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN") from None
        if not listeners:
            if claimed:
                raise AcquisitionBlocked("PROFILE_ALREADY_ACTIVE_NO_CDP")
            return "ABSENT", None
        row = listeners[0]
        if row.get("LocalAddress") != "127.0.0.1" or type(row.get("OwningProcess")) is not int:
            raise AcquisitionBlocked("CDP_NOT_LOOPBACK_OWNED")
        proc = owners.get(row["OwningProcess"])
        if (proc is None or type(proc.get("SessionId")) is not int
                or proc["SessionId"] != session_id or not isinstance(proc.get("ExecutablePath"), str)
                or not _identity_matches(env, proc["ExecutablePath"], parse(proc["CommandLine"]))
                or len(claimed) != 1 or claimed[0] is not proc
                or (expected_sid is not None and (proc.get("OwnerSid") != expected_sid
                    or not isinstance(proc.get("CreationDate"), str) or not proc["CreationDate"]))):
            raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        return "OWNED", proc["ProcessId"]
    except AcquisitionBlocked:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise AcquisitionBlocked("WINDOWS_PROBE_INVALID") from None


def _interactive_session() -> int:
    if os.name != "nt":
        raise AcquisitionBlocked("WINDOWS_INTERACTIVE_REQUIRED")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentProcessId.restype = wintypes.DWORD
    kernel.ProcessIdToSessionId.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(), ctypes.byref(session)) or not session.value:
        raise AcquisitionBlocked("WINDOWS_INTERACTIVE_REQUIRED")
    # A session ID alone does not imply a usable interactive input desktop.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    desktop = user32.OpenInputDesktop(0, False, 0x0001)
    if not desktop:
        raise AcquisitionBlocked("WINDOWS_INTERACTIVE_REQUIRED")
    user32.CloseDesktop(desktop)
    # TokenElevation=20; do not launch a browser with an elevated admin token.
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.OpenProcessToken.restype = wintypes.BOOL
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.GetTokenInformation.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise AcquisitionBlocked("WINDOWS_TOKEN_UNAVAILABLE")
    try:
        elevated = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not adv.GetTokenInformation(token, 20, ctypes.byref(elevated),
                                        ctypes.sizeof(elevated), ctypes.byref(returned)):
            raise AcquisitionBlocked("WINDOWS_TOKEN_UNAVAILABLE")
        if elevated.value != 0:
            raise AcquisitionBlocked("ELEVATED_CHROME_DISALLOWED")
    finally:
        kernel.CloseHandle(token)
    return session.value


def run(env: ChromeEnvironment, *, launch: bool, opt_in: bool) -> dict:
    if os.name != "nt":
        raise AcquisitionBlocked("WINDOWS_INTERACTIVE_REQUIRED")
    session = _interactive_session()
    _require_operator_control(env)
    _verify_google_chrome(env)
    state, pid = evaluate(env, _query_windows(env.port), session_id=session)
    if state == "OWNED":
        _probe_cdp(env)
        return {"status": "READY_ATTACH", "cdp_verified": True,
                "browser_accessed": True, "launched": False}
    if not launch:
        return {"status": "NOT_RUNNING", "cdp_verified": False,
                "browser_accessed": False, "launched": False}
    if not opt_in or not env.allow_launch:
        raise AcquisitionBlocked("LAUNCH_NOT_AUTHORIZED")
    if any((env.user_data_dir / name).exists() or (env.user_data_dir / name).is_symlink()
           for name in _MARKERS):
        raise AcquisitionBlocked("PROFILE_LOCKED")
    child_env = _child_environment()
    try:
        child = subprocess.Popen(env.command(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True, env=child_env)
    except OSError:
        raise AcquisitionBlocked("LAUNCH_UNCERTAIN") from None
    # A single bounded observation, never a second Chrome launch.
    for _ in range(20):
        time.sleep(0.5)
        try:
            state, owner_pid = evaluate(env, _query_windows(env.port), session_id=session)
        except AcquisitionBlocked as exc:
            if str(exc) in ("PROFILE_ALREADY_ACTIVE_NO_CDP", "BROWSER_OWNERSHIP_UNPROVEN"):
                continue
            raise
        if state == "OWNED":
            if owner_pid != child.pid:
                raise AcquisitionBlocked("LAUNCH_OWNER_MISMATCH")
            _probe_cdp(env)
            return {"status": "READY_ATTACH", "cdp_verified": True,
                    "browser_accessed": True, "launched": True}
        if child.poll() is not None:
            break
    raise AcquisitionBlocked("LAUNCH_UNPROVEN")


def verify_playwright_attach(env: ChromeEnvironment) -> None:
    """Old acquisition -> attach contract; no pages, URLs, cookies or messages.

    Stop only the client transport. Browser.close() would affect the owned
    browser and is deliberately never called. The bridge rechecks ownership
    before and after this probe; this is not login or manifest acceptance.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as client:
        browser = client.chromium.connect_over_cdp(env.endpoint, timeout=3000)
        if not browser.is_connected():
            raise AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")


class _EnrollmentHold(ValueError):
    """Assessment diagnostics are separate from the legacy acquisition CLI."""


_ENROLLMENT_CODES = frozenset({
    "ENROLLMENT_OPT_IN_REQUIRED", "LOCAL_OPERATOR_ONLY", "WINDOWS_INTERACTIVE_REQUIRED",
    "WINDOWS_TOKEN_UNAVAILABLE", "ELEVATED_CHROME_DISALLOWED",
    "ENROLLMENT_IDENTITY_UNPROVEN", "ENROLLMENT_PRIVATE_ROOT_UNAVAILABLE",
    "ENROLLMENT_PATH_UNSAFE", "ENROLLMENT_CONFIG_EXISTS", "CONFIG_PATH_INVALID",
    "CONFIG_IN_REPOSITORY", "CONFIG_INVALID", "CHROME_PATH_INVALID",
    "PROFILE_NOT_DEDICATED", "PROFILE_UNAVAILABLE", "CONFIG_IN_CHROME_PROFILE",
    "FIRST_SUPERVISED_ENROLLMENT_REQUIRED", "ENROLLMENT_CANDIDATE_UNPROVEN",
    "BROWSER_OWNERSHIP_UNPROVEN", "CDP_NOT_LOOPBACK_OWNED", "WINDOWS_PROBE_INVALID",
    "PROCESS_IDENTITY_UNKNOWN", "OPERATOR_OWNERSHIP_UNPROVEN",
    "CHROME_EXECUTABLE_UNTRUSTED", "WINDOWS_PROBE_UNAVAILABLE", "POWERSHELL_UNAVAILABLE",
    "ENROLLMENT_PROFILE_LOCK_UNPROVEN", "ENROLLMENT_OBSERVATION_CHANGED",
    "NATIVE_ATOMIC_ENROLLMENT_REQUIRED", "ENROLLMENT_ASSESSMENT_UNKNOWN",
    "NATIVE_HANDLE_RELATIVE_UNAVAILABLE", "ENROLLMENT_NATIVE_CUSTODY_UNPROVEN",
    "ENROLLMENT_RESERVATION_EXISTS",
})


def _enrollment_hold(code: str, *, assessed: bool = False) -> dict:
    # No private candidate, paths, PID, session, SID or subprocess output escapes.
    return {"status": "HOLD", "code": code if code in _ENROLLMENT_CODES else
            "ENROLLMENT_ASSESSMENT_UNKNOWN", "candidate_assessed": assessed,
            "enrolled": False, "allow_launch": False, "browser_accessed": False,
            "launched": False, "chat_identity_proven": False, "send_authorized": False}


def _native_known_folder(folder_id: str) -> Path:
    import ctypes
    from ctypes import wintypes
    from uuid import UUID
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                          wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    folder = (ctypes.c_ubyte * 16).from_buffer_copy(
        UUID(folder_id).bytes_le)
    value = ctypes.c_wchar_p()
    if shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(value)) != 0:
        raise _EnrollmentHold("ENROLLMENT_IDENTITY_UNPROVEN")
    try:
        if not value.value:
            raise _EnrollmentHold("ENROLLMENT_IDENTITY_UNPROVEN")
        return Path(value.value)
    finally:
        ole.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))


def _native_local_app_data() -> Path:
    """Use the current token's Windows known folder, not an input-selected path."""
    return _native_known_folder("F1B32785-6FBA-4FCF-9D55-7B8E7F157091")


def _enrollment_operator(*, attended: bool = True) -> tuple[Path, int, str]:
    # Nothing at this entrypoint can turn an Issue/runner request into enrollment.
    if os.name != "nt":
        raise _EnrollmentHold("WINDOWS_INTERACTIVE_REQUIRED")
    if any(os.environ.get(key) for key in ("GITHUB_ACTIONS", "GITHUB_EVENT_PATH", "GITHUB_RUN_ID")):
        raise _EnrollmentHold("LOCAL_OPERATOR_ONLY")
    if attended and (sys.stdin is None or not sys.stdin.isatty()):
        raise _EnrollmentHold("WINDOWS_INTERACTIVE_REQUIRED")
    return _native_operator()


def _native_operator() -> tuple[Path, int, str]:
    """Native token and desktop attestation only. Callers supply effect authority."""
    session = _interactive_session()
    from .local_wake_entry import _windows_process_sid
    sid = _windows_process_sid()
    if (type(session) is not int or session <= 0 or not isinstance(sid, str)
            or not re.fullmatch(r"S-1-(?:\d+-){1,14}\d+", sid)
            or sid in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}):
        raise _EnrollmentHold("ENROLLMENT_IDENTITY_UNPROVEN")
    return _native_local_app_data(), session, sid


# HostOps may reuse this exact finite registry in a separately scoped change.
# Canonical enrollment takes precedence for diagnostics only: any second entry
# still HOLDs. Never recursively discover, migrate or trust a legacy launcher.
_CONFIG_9444_RELATIVE_PATHS = (
    "Chrome-Acquisition/config.json",
    "D5-Isolated-9444-Trial/config.json",
    "WakeHub/config.json",
    "WakeHub-9444/config.json",
    "chrome.json",
    "chrome-acquisition.json",
)


@dataclass(frozen=True, slots=True, repr=False)
class _Config9444Registry:
    canonical: Path
    present: tuple[Path, ...]

    @property
    def selected(self) -> Path | None:
        return self.present[0] if self.present else None


def _config_9444_registry(local: Path, checkout: Path) -> _Config9444Registry:
    """Bounded read-only path recognition, including invalid files/reservations."""
    if (not local.is_absolute() or not local.is_dir() or not checkout.is_absolute()
            or not checkout.is_dir() or not _no_links(local) or not _no_links(checkout)):
        raise _EnrollmentHold("CONFIG_PATH_INVALID")
    root = local / "AI-Commerce"
    candidates = tuple(root / name for name in _CONFIG_9444_RELATIVE_PATHS)
    if not all(_no_links(path) for path in candidates):
        raise _EnrollmentHold("ENROLLMENT_PATH_UNSAFE")
    if any(_inside(path.resolve(), checkout.resolve(strict=True)) for path in candidates):
        raise _EnrollmentHold("CONFIG_IN_REPOSITORY")
    present = []
    for path in candidates:
        try:
            path.lstat()  # Includes directories and dangling links; never overwrite.
        except FileNotFoundError:
            continue
        present.append(path)
    return _Config9444Registry(candidates[0], tuple(present))


def _enrollment_location(local: Path, checkout: Path) -> Path:
    """One fixed future destination; refuse all six recognized existing configs."""
    registry = _config_9444_registry(local, checkout)
    if not (local / "AI-Commerce").is_dir():
        raise _EnrollmentHold("ENROLLMENT_PRIVATE_ROOT_UNAVAILABLE")
    if registry.present:
        raise _EnrollmentHold("ENROLLMENT_CONFIG_EXISTS")
    return registry.canonical


def _existing_9444_candidate(inventory: dict, *, local: Path, checkout: Path,
                             config: Path, session: int, sid: str) -> tuple[ChromeEnvironment, tuple]:
    listeners, processes = _rows(inventory["listeners"]), _rows(inventory["processes"])
    if not listeners:
        # OFF is not evidence of a previous executable/profile/owner enrollment.
        raise _EnrollmentHold("FIRST_SUPERVISED_ENROLLMENT_REQUIRED")
    if len(listeners) != 1 or len(processes) > 256:
        raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
    row = listeners[0]
    pid = row.get("OwningProcess")
    pids = [p.get("ProcessId") for p in processes]
    if (row.get("LocalAddress") != "127.0.0.1" or type(row.get("LocalPort")) is not int
            or row["LocalPort"] != 9444 or type(pid) is not int or pid <= 0
            or any(type(p) is not int or p <= 0 for p in pids) or len(set(pids)) != len(pids)):
        raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
    owners = [p for p in processes if p["ProcessId"] == pid]
    if len(owners) != 1:
        raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
    proc = owners[0]
    created = proc.get("CreationDate")
    if (proc.get("OwnerSid") != sid or type(proc.get("SessionId")) is not int
            or proc["SessionId"] != session or not isinstance(created, str)
            or not 15 <= len(created) <= 80):
        raise _EnrollmentHold("ENROLLMENT_IDENTITY_UNPROVEN")
    if _creation_ticks(created) <= 0:
        raise _EnrollmentHold("ENROLLMENT_IDENTITY_UNPROVEN")
    argv = _commandline_argv(proc.get("CommandLine"))
    for name in ("--user-data-dir", "--profile-directory", "--remote-debugging-address",
                 "--remote-debugging-port"):
        switches = [a for a in argv if a.lower() == name or a.lower().startswith(name + "=")]
        if len(switches) != 1 or not switches[0].startswith(name + "="):
            raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
    if (_switch(argv, "--remote-debugging-port") != "9444"
            or _switch(argv, "--remote-debugging-address") != "127.0.0.1"
            or any(a.lower() == "--type" or a.lower().startswith("--type=") for a in argv)):
        raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
    port_claimants = []
    for process in processes:
        process_argv = _commandline_argv(process.get("CommandLine"))
        if any(a.lower().startswith("--type=") for a in process_argv):
            continue
        ports = [a for a in process_argv if a.lower() == "--remote-debugging-port"
                 or a.lower().startswith("--remote-debugging-port=")]
        if ports and (len(ports) != 1 or not ports[0].startswith("--remote-debugging-port=")):
            raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
        port = _switch(process_argv, "--remote-debugging-port")
        if ports and (port is None or re.fullmatch(r"[0-9]{1,5}", port) is None):
            raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
        if port is not None and int(port) == 9444:
            port_claimants.append(process["ProcessId"])
    if port_claimants != [pid]:
        raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
    value = {"version": 1, "executable": proc.get("ExecutablePath"),
             "user_data_dir": _switch(argv, "--user-data-dir"),
             "profile_directory": _switch(argv, "--profile-directory"),
             "cdp_endpoint": "http://127.0.0.1:9444", "exclusive_user_data": True,
             "allow_launch": False}
    env = _environment_from_value(value, config, checkout.resolve(strict=True), local_app_data=local)
    if sorted(argv[1:]) != sorted(env.command()[1:]):
        raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
    # Reuse ENSURE's exact process/profile/session/SID rules, with no absent launch.
    state, owned_pid = evaluate(env, inventory, session_id=session, expected_sid=sid,
                                parse=_commandline_argv)
    if state != "OWNED" or owned_pid != pid:
        raise _EnrollmentHold("ENROLLMENT_CANDIDATE_UNPROVEN")
    identity = tuple(proc.get(k) for k in (
        "ProcessId", "ExecutablePath", "CommandLine", "SessionId", "OwnerSid", "CreationDate"))
    return env, identity


def _check_enrollment_custody(env: ChromeEnvironment, local: Path, sid: str, *,
                              marker_proof=None) -> None:
    from .local_completion_handoff import HandoffBlocked, _private_identity
    root = (local / "AI-Commerce").resolve(strict=True)
    # No config exists. Check the existing private root in its place, without
    # pretending this proves custody of a future file or authorizes its creation.
    _require_operator_control(replace(env, config_path=root))
    paths = {root, env.user_data_dir, env.user_data_dir / env.profile_directory,
             *env.user_data_dir.parents}
    paths = {p for p in paths if p == root or _inside(p, root)}
    if env.config_path.parent.exists():
        paths.add(env.config_path.parent)
    try:
        unsafe = any(not _no_links(p) or _private_identity(p) != sid for p in paths)
    except (HandoffBlocked, OSError, ValueError):
        raise _EnrollmentHold("OPERATOR_OWNERSHIP_UNPROVEN") from None
    if unsafe:
        raise _EnrollmentHold("OPERATOR_OWNERSHIP_UNPROVEN")
    for name in _MARKERS:
        try:
            (env.user_data_dir / name).lstat()
        except FileNotFoundError:
            continue
        # Ownership of the directory or a PID written in a marker is insufficient.
        # Only the native transaction may independently prove the live holder.
        if marker_proof is None or marker_proof(env.user_data_dir / name) is not True:
            raise _EnrollmentHold("ENROLLMENT_PROFILE_LOCK_UNPROVEN")
    _verify_google_chrome(env)


def _creation_ticks(value: str) -> int:
    """CIM's ISO timestamp -> exact Windows FILETIME, including its seventh digit."""
    match = re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[.](\d{1,7}))?(?:Z|[+-]\d\d:\d\d)", value)
    if match is None:
        raise _EnrollmentHold("ENROLLMENT_IDENTITY_UNPROVEN")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        delta = date.astimezone(timezone.utc) - datetime(1601, 1, 1, tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        raise _EnrollmentHold("ENROLLMENT_IDENTITY_UNPROVEN") from None
    fraction = (match[1] or "").ljust(7, "0")
    return (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10 + int(fraction[6])


class _NativeEnrollment:
    """One native transaction. No path-based opens, inherited creation ACL or retry.

    NtCreateFile FILE_CREATE is CREATE_NEW with RootDirectory custody. Every
    component is opened with OBJ_DONT_REPARSE and pinned without delete sharing.
    A fresh protected directory is the durable one-time reservation. An existing
    directory is never adopted, including an empty remnant of an uncertain call.
    This backend is never invoked by the read-only assessment or legacy run().
    """

    def __init__(self, env, local, session, sid, identity):
        self.env, self.local, self.session, self.sid, self.identity = env, local, session, sid, identity
        self.effect_possible = False
        self.handles = []
        self.descriptor = None
        try:
            self._bind()
        except Exception:
            raise _EnrollmentHold("NATIVE_HANDLE_RELATIVE_UNAVAILABLE") from None

    def _bind(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.k = c.WinDLL("kernel32", use_last_error=True)
        self.a = c.WinDLL("advapi32", use_last_error=True)
        self.n = c.WinDLL("ntdll", use_last_error=True)
        self.rm = c.WinDLL("rstrtmgr", use_last_error=True)
        p = c.c_void_p

        class Unicode(c.Structure):
            _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", w.LPWSTR)]
        class Attributes(c.Structure):
            _fields_ = [("Length", w.ULONG), ("RootDirectory", w.HANDLE),
                        ("ObjectName", c.POINTER(Unicode)), ("Attributes", w.ULONG),
                        ("SecurityDescriptor", p), ("SecurityQualityOfService", p)]
        class IoStatus(c.Structure):
            _fields_ = [("Status", p), ("Information", c.c_size_t)]
        class FileInfo(c.Structure):
            _fields_ = [("Attributes", w.DWORD), ("Creation", w.FILETIME),
                        ("Access", w.FILETIME), ("Write", w.FILETIME),
                        ("Volume", w.DWORD), ("SizeHigh", w.DWORD), ("SizeLow", w.DWORD),
                        ("Links", w.DWORD), ("IndexHigh", w.DWORD), ("IndexLow", w.DWORD)]
        class UniqueProcess(c.Structure):
            _fields_ = [("pid", w.DWORD), ("start", w.FILETIME)]
        class RmInfo(c.Structure):
            _fields_ = [("process", UniqueProcess), ("app", w.WCHAR * 256),
                        ("service", w.WCHAR * 64), ("kind", c.c_int),
                        ("status", w.ULONG), ("session", w.DWORD), ("restartable", w.BOOL)]
        self.Unicode, self.Attributes, self.IoStatus = Unicode, Attributes, IoStatus
        self.FileInfo, self.RmInfo = FileInfo, RmInfo
        bindings = [
            (self.n, "NtCreateFile", c.c_long, [c.POINTER(w.HANDLE), w.DWORD, c.POINTER(Attributes),
                c.POINTER(IoStatus), p, w.ULONG, w.ULONG, w.ULONG, w.ULONG, p, w.ULONG]),
            (self.k, "CreateFileW", w.HANDLE, [w.LPCWSTR, w.DWORD, w.DWORD, p, w.DWORD, w.DWORD, w.HANDLE]),
            (self.k, "GetFileInformationByHandle", w.BOOL, [w.HANDLE, c.POINTER(FileInfo)]),
            (self.k, "GetFileType", w.DWORD, [w.HANDLE]),
            (self.k, "GetDriveTypeW", w.UINT, [w.LPCWSTR]),
            (self.k, "CloseHandle", w.BOOL, [w.HANDLE]),
            (self.k, "LocalFree", p, [p]),
            (self.k, "WriteFile", w.BOOL, [w.HANDLE, p, w.DWORD, c.POINTER(w.DWORD), p]),
            (self.k, "ReadFile", w.BOOL, [w.HANDLE, p, w.DWORD, c.POINTER(w.DWORD), p]),
            (self.k, "FlushFileBuffers", w.BOOL, [w.HANDLE]),
            (self.k, "OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
            (self.k, "GetCurrentProcess", w.HANDLE, []),
            (self.k, "GetCurrentThread", w.HANDLE, []),
            (self.k, "WaitForSingleObject", w.DWORD, [w.HANDLE, w.DWORD]),
            (self.k, "GetProcessTimes", w.BOOL, [w.HANDLE, p, p, p, p]),
            (self.k, "QueryFullProcessImageNameW", w.BOOL, [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]),
            (self.a, "OpenProcessToken", w.BOOL, [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]),
            (self.a, "OpenThreadToken", w.BOOL, [w.HANDLE, w.DWORD, w.BOOL, c.POINTER(w.HANDLE)]),
            (self.a, "GetTokenInformation", w.BOOL, [w.HANDLE, c.c_int, p, w.DWORD, c.POINTER(w.DWORD)]),
            (self.a, "ConvertSidToStringSidW", w.BOOL, [p, c.POINTER(w.LPWSTR)]),
            (self.a, "IsValidSid", w.BOOL, [p]),
            (self.a, "GetLengthSid", w.DWORD, [p]),
            (self.a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL,
                [w.LPCWSTR, w.DWORD, c.POINTER(p), p]),
            (self.a, "GetSecurityInfo", w.DWORD, [w.HANDLE, c.c_int, w.DWORD,
                c.POINTER(p), p, c.POINTER(p), p, c.POINTER(p)]),
            (self.a, "GetSecurityDescriptorControl", w.BOOL, [p, c.POINTER(w.WORD), c.POINTER(w.DWORD)]),
            (self.a, "GetAclInformation", w.BOOL, [p, p, w.DWORD, c.c_int]),
            (self.a, "GetAce", w.BOOL, [p, w.DWORD, c.POINTER(p)]),
            (self.rm, "RmStartSession", w.DWORD, [c.POINTER(w.DWORD), w.DWORD, w.LPWSTR]),
            (self.rm, "RmRegisterResources", w.DWORD, [w.DWORD, w.UINT, c.POINTER(w.LPCWSTR),
                w.UINT, p, w.UINT, p]),
            (self.rm, "RmGetList", w.DWORD, [w.DWORD, c.POINTER(w.UINT), c.POINTER(w.UINT),
                c.POINTER(RmInfo), c.POINTER(w.DWORD)]),
            (self.rm, "RmEndSession", w.DWORD, [w.DWORD]),
        ]
        for library, name, result, args in bindings:
            function = getattr(library, name)
            function.restype, function.argtypes = result, args

    def _blocked(self):
        raise _EnrollmentHold("ENROLLMENT_NATIVE_CUSTODY_UNPROVEN")

    def _keep(self, handle):
        self.handles.append(handle)
        return handle

    def _sid_text(self, pointer):
        c, w = self.c, self.w
        text = w.LPWSTR()
        if not pointer or not self.a.IsValidSid(pointer) or not self.a.ConvertSidToStringSidW(pointer, c.byref(text)):
            self._blocked()
        try:
            return text.value
        finally:
            self.k.LocalFree(c.cast(text, c.c_void_p))

    def _acl(self, handle, *, exact=False):
        from .local_completion_handoff import HandoffBlocked, _check_windows_acl
        c, w = self.c, self.w
        owner, dacl, sd = c.c_void_p(), c.c_void_p(), c.c_void_p()
        if self.a.GetSecurityInfo(handle, 1, 5, c.byref(owner), None, c.byref(dacl), None, c.byref(sd)):
            self._blocked()
        try:
            control, revision = w.WORD(), w.DWORD()
            info = (w.DWORD * 3)()
            if (not dacl or not self.a.GetSecurityDescriptorControl(sd, c.byref(control), c.byref(revision))
                    or not self.a.GetAclInformation(dacl, info, c.sizeof(info), 2)
                    or not 1 <= info[0] <= 128):
                self._blocked()
            aces = []
            for index in range(info[0]):
                ace = c.c_void_p()
                if not self.a.GetAce(dacl, index, c.byref(ace)):
                    self._blocked()
                header = c.string_at(ace, 8)
                size = int.from_bytes(header[2:4], "little")
                sid = c.c_void_p(ace.value + 8)
                if (header[0] not in (0, 1) or size < 16 or not self.a.IsValidSid(sid)
                        or self.a.GetLengthSid(sid) > size - 8):
                    self._blocked()
                aces.append(("allow" if header[0] == 0 else "deny", self._sid_text(sid)))
                if exact and (header[0:2] != b"\x00\x00" or int.from_bytes(header[4:8], "little") != 0x1F01FF):
                    self._blocked()
            try:
                _check_windows_acl(self._sid_text(owner), aces, self.sid)
            except HandoffBlocked:
                self._blocked()
            if exact and (not control.value & 0x1000 or aces != [("allow", self.sid)]):
                self._blocked()
        finally:
            self.k.LocalFree(sd)

    def _info(self, handle, directory):
        info = self.FileInfo()
        if (self.k.GetFileType(handle) != 1 or not self.k.GetFileInformationByHandle(handle, self.c.byref(info))
                or info.Attributes & 0x400 or bool(info.Attributes & 0x10) != directory
                or (not directory and info.Links != 1)):
            self._blocked()
        return (info.Volume, info.IndexHigh, info.IndexLow, info.SizeHigh, info.SizeLow)

    def _relative(self, parent, name, *, directory, create=False, write=False, optional=False, readback=False):
        c, w = self.c, self.w
        if not name or name in {".", ".."} or any(ch in name for ch in "\\/:\x00"):
            self._blocked()
        buffer = c.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        if length > 65532:
            self._blocked()
        string = self.Unicode(length, length + 2, c.cast(buffer, w.LPWSTR))
        attrs = self.Attributes(c.sizeof(self.Attributes), parent, c.pointer(string),
                                0x40 | 0x1000, self.descriptor if create else None, None)
        io, handle = self.IoStatus(), w.HANDLE()
        access = 0x120089 | (0x116 if write else 0)  # read/control/synchronize; optional write
        previous = self.effect_possible
        if create:
            self.effect_possible = True  # Set BEFORE crossing the mutation boundary.
        status = self.n.NtCreateFile(c.byref(handle), access, c.byref(attrs), c.byref(io), None,
            0x80, 3 if readback else 1, 2 if create else 1,
            0x20 | 0x200000 | (1 if directory else 0x40), None, 0)
        unsigned = status & 0xFFFFFFFF
        if unsigned in {0xC0000034, 0xC000003A} and optional and not create:
            return None
        if unsigned == 0xC0000035 and create:
            self.effect_possible = previous  # FILE_CREATE collision has no effect.
            raise _EnrollmentHold("ENROLLMENT_RESERVATION_EXISTS" if directory else "ENROLLMENT_CONFIG_EXISTS")
        if status < 0:
            # Native failure is definitive; an exception during the syscall is not.
            self.effect_possible = previous
            self._blocked()
        self._keep(handle)
        if create and io.Information != 2:  # FILE_CREATED only
            self._blocked()
        self._info(handle, directory)
        return handle

    def _pin_path(self, path, *, directory=True, private=False):
        # Only a native local drive: no UNC/device paths or reparse mount points.
        if (not re.fullmatch(r"[A-Za-z]:\\", path.anchor)
                or self.k.GetDriveTypeW(path.anchor) != 3
                or any(p in {".", ".."} for p in path.parts)):
            self._blocked()
        root = self.k.CreateFileW(path.anchor, 0x120089, 1, None, 3, 0x02200000, None)
        if root in (None, self.c.c_void_p(-1).value):
            self._blocked()
        handle = self._keep(root)
        self._info(handle, True)
        for index, name in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            handle = self._relative(handle, name, directory=directory if last else True)
        if private:
            self._acl(handle)
        self.pinned.append((path, handle, directory, self._info(handle, directory), private))
        return handle

    def _token(self, process):
        c, w = self.c, self.w
        token = w.HANDLE()
        if not self.a.OpenProcessToken(process, 8, c.byref(token)):
            self._blocked()
        self._keep(token)
        def data(kind):
            needed = w.DWORD()
            self.a.GetTokenInformation(token, kind, None, 0, c.byref(needed))
            if not 4 <= needed.value <= 65536:
                self._blocked()
            value = c.create_string_buffer(needed.value)
            if not self.a.GetTokenInformation(token, kind, value, needed, c.byref(needed)):
                self._blocked()
            return value
        user = data(1)
        if (self._sid_text(c.cast(user, c.POINTER(c.c_void_p))[0]) != self.sid
                or c.cast(data(12), c.POINTER(w.DWORD))[0] != self.session
                or c.cast(data(20), c.POINTER(w.DWORD))[0] != 0):
            self._blocked()
        # Enabled INTERACTIVE token group is positive logon evidence, in addition
        # to the input desktop/session checks. Deny-only groups cannot qualify.
        class Group(c.Structure):
            _fields_ = [("sid", c.c_void_p), ("flags", w.DWORD)]
        class Groups(c.Structure):
            _fields_ = [("count", w.DWORD), ("first", Group)]
        groups = data(2)
        count = c.cast(groups, c.POINTER(w.DWORD))[0]
        offset = Groups.first.offset
        if not 1 <= count <= 2048 or offset + count * c.sizeof(Group) > c.sizeof(groups):
            self._blocked()
        found = set()
        for index in range(count):
            group = Group.from_buffer(groups, offset + index * c.sizeof(Group))
            if group.flags & 4 and not group.flags & 0x10:
                found.add(self._sid_text(group.sid))
        if not {"S-1-5-4", "S-1-5-11"} <= found:
            self._blocked()

    def _process(self):
        c, w = self.c, self.w
        # Never create under a thread identity different from the process token
        # used by the SID, known-folder and interactive-session observations.
        thread_token = w.HANDLE()
        c.set_last_error(0)
        if self.a.OpenThreadToken(self.k.GetCurrentThread(), 8, True, c.byref(thread_token)):
            self._keep(thread_token)
            self._blocked()
        if c.get_last_error() != 1008:  # ERROR_NO_TOKEN, positive no-impersonation evidence
            self._blocked()
        if self.k.WaitForSingleObject(self.process, 0) != 258:
            self._blocked()
        times = [w.FILETIME() for _ in range(4)]
        image, size = c.create_unicode_buffer(32768), w.DWORD(32768)
        if (not self.k.GetProcessTimes(self.process, *(c.byref(t) for t in times))
                or not self.k.QueryFullProcessImageNameW(self.process, 0, image, c.byref(size))
                or Path(image.value) != self.env.executable):
            self._blocked()
        ticks = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
        # CIM datetime has microsecond precision. Pin the full native 100ns value
        # once and require its exact equality on every subsequent observation.
        if (ticks // 10 != _creation_ticks(self.identity[5]) // 10
                or (hasattr(self, "process_ticks") and ticks != self.process_ticks)):
            self._blocked()
        self.process_ticks = ticks
        self._token(self.process)
        self._token(self.k.GetCurrentProcess())

    def __enter__(self):
        try:
            self.pinned = []
            self.process = self._keep(self.k.OpenProcess(0x101000, False, self.identity[0]))
            if not self.process:
                self._blocked()
            self._process()
            self._pin_path(self.local)
            self.root = self._pin_path(self.local / "AI-Commerce", private=True)
            # Verify every dedicated-profile ancestor by handle, not only the leaf.
            for path in reversed((self.env.user_data_dir, *self.env.user_data_dir.parents)):
                if _inside(path, self.local / "AI-Commerce"):
                    self._pin_path(path, private=True)
            self._pin_path(self.env.user_data_dir / self.env.profile_directory, private=True)
            self._pin_path(self.env.executable, directory=False)
            self.absent()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def absent(self):
        for name in ("chrome.json", "chrome-acquisition.json"):
            if self._relative(self.root, name, directory=False, optional=True) is not None:
                raise _EnrollmentHold("ENROLLMENT_CONFIG_EXISTS")
        # Do not adopt any prior reservation, including one with no config yet.
        if self._relative(self.root, "Chrome-Acquisition", directory=True, optional=True) is not None:
            raise _EnrollmentHold("ENROLLMENT_RESERVATION_EXISTS")

    def validate(self):
        self._process()
        for path, handle, directory, identity, private in self.pinned:
            if self._info(handle, directory)[:3] != identity[:3]:
                self._blocked()
            if private:
                self._acl(handle)

    def prove_marker(self, path):
        """Restart Manager must name exactly the pinned browser as live holder.

        Never interpret lock contents, remove a marker or accept an owner ACL as
        process proof. Unsupported, stale, shared or inaccessible markers HOLD.
        """
        c, w = self.c, self.w
        self._pin_path(path, directory=False, private=True)
        session, key = w.DWORD(), c.create_unicode_buffer(33)
        if self.rm.RmStartSession(c.byref(session), 0, key):
            return False
        try:
            files = (w.LPCWSTR * 1)(str(path))
            if self.rm.RmRegisterResources(session, 1, files, 0, None, 0, None):
                return False
            needed, count, reasons = w.UINT(), w.UINT(2), w.DWORD()
            rows = (self.RmInfo * 2)()
            if self.rm.RmGetList(session, c.byref(needed), c.byref(count), rows, c.byref(reasons)) or count.value != 1:
                return False
            proc = rows[0].process
            self._process()
            return (proc.pid == self.identity[0] and rows[0].session == self.session
                    and (proc.start.dwHighDateTime << 32 | proc.start.dwLowDateTime) == self.process_ticks)
        finally:
            self.rm.RmEndSession(session)

    def create_and_flush(self, payload):
        c, w = self.c, self.w
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= 8192:
            self._blocked()
        self.absent()
        sd = c.c_void_p()
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"O:{self.sid}D:P(A;;FA;;;{self.sid})", 1, c.byref(sd), None):
            self._blocked()
        self.descriptor = sd
        self.directory = self._relative(self.root, "Chrome-Acquisition", directory=True, create=True)
        self._acl(self.directory, exact=True)
        self.file = self._relative(self.directory, "config.json", directory=False, create=True, write=True)
        self.file_identity = self._info(self.file, False)[:3]
        self._acl(self.file, exact=True)
        written = w.DWORD()
        if (not self.k.WriteFile(self.file, payload, len(payload), c.byref(written), None)
                or written.value != len(payload) or not self.k.FlushFileBuffers(self.file)):
            self._blocked()

    def readback(self, payload):
        c, w = self.c, self.w
        # A new handle independently reads the fixed name while the writer denies
        # all other writers/deletion. Verify both handle IDs and owner-only DACL.
        handle = self._relative(self.directory, "config.json", directory=False, readback=True)
        info = self._info(handle, False)
        self._acl(self.directory, exact=True)
        self._acl(handle, exact=True)
        if info[:3] != self.file_identity or info[3:] != (0, len(payload)):
            self._blocked()
        buffer, count = c.create_string_buffer(8193), w.DWORD()
        if (not self.k.ReadFile(handle, buffer, 8193, c.byref(count), None)
                or count.value != len(payload) or buffer.raw[:count.value] != payload):
            self._blocked()
        for name in ("chrome.json", "chrome-acquisition.json"):
            if self._relative(self.root, name, directory=False, optional=True) is not None:
                self._blocked()

    def __exit__(self, *unused):
        failed = False
        for handle in reversed(self.handles):
            if handle and not self.k.CloseHandle(handle):
                failed = True
        self.handles.clear()
        if self.descriptor:
            self.k.LocalFree(self.descriptor)
            self.descriptor = None
        if failed:
            self._blocked()


def assess_existing_9444(checkout: Path) -> dict:
    """Local read-only assessment, never account/chat proof or write permission.

    Path-based ACL checks cannot securely create a new Windows config under
    atomic, handle-based custody. This read-only entrypoint produces only a
    private in-memory candidate and HOLD for explicit local native enrollment.
    No HTTP/CDP attach, prompt, file creation, Chrome start or retry occurs here.
    """
    try:
        local, session, sid = _enrollment_operator()
        config = _enrollment_location(local, checkout)
        env, identity = _existing_9444_candidate(_query_windows(9444), local=local,
            checkout=checkout, config=config, session=session, sid=sid)
        _check_enrollment_custody(env, local, sid)
        # Independently re-observe PID creation time, ownership, listener, token,
        # paths and permissions once. A changed observation is a HOLD, no replay.
        if _enrollment_operator() != (local, session, sid):
            raise _EnrollmentHold("ENROLLMENT_OBSERVATION_CHANGED")
        if _enrollment_location(local, checkout) != config:
            raise _EnrollmentHold("ENROLLMENT_OBSERVATION_CHANGED")
        current, current_identity = _existing_9444_candidate(_query_windows(9444), local=local,
            checkout=checkout, config=config, session=session, sid=sid)
        if current != env or current_identity != identity:
            raise _EnrollmentHold("ENROLLMENT_OBSERVATION_CHANGED")
        _check_enrollment_custody(current, local, sid)
        return _enrollment_hold("NATIVE_ATOMIC_ENROLLMENT_REQUIRED", assessed=True)
    except (_EnrollmentHold, AcquisitionBlocked) as exc:
        return _enrollment_hold(str(exc))
    except Exception:
        return _enrollment_hold("ENROLLMENT_ASSESSMENT_UNKNOWN")


def enroll_existing_9444(checkout: Path, *, opt_in: bool = False) -> dict:
    """Explicit one-time local native enrollment; never launch, HTTP or attach.

    Source tests cannot establish native effects. A successful local receipt
    proves only private config creation for the independently owned browser.
    No launch, account, chat, logon autostart or delivery authority is granted.
    """
    if opt_in is not True:
        return _enrollment_hold("ENROLLMENT_OPT_IN_REQUIRED")
    native = None
    try:
        local, session, sid = _enrollment_operator()
        config = _enrollment_location(local, checkout)
        env, identity = _existing_9444_candidate(_query_windows(9444), local=local,
            checkout=checkout, config=config, session=session, sid=sid)
        native = _NativeEnrollment(env, local, session, sid, identity)
        with native:
            _check_enrollment_custody(env, local, sid, marker_proof=native.prove_marker)
            if (_enrollment_operator() != (local, session, sid)
                    or _enrollment_location(local, checkout) != config):
                raise _EnrollmentHold("ENROLLMENT_OBSERVATION_CHANGED")
            current, current_identity = _existing_9444_candidate(_query_windows(9444), local=local,
                checkout=checkout, config=config, session=session, sid=sid)
            if current != env or current_identity != identity:
                raise _EnrollmentHold("ENROLLMENT_OBSERVATION_CHANGED")
            _check_enrollment_custody(current, local, sid, marker_proof=native.prove_marker)
            native.validate()
            value = {"version": 1, "executable": str(env.executable),
                     "user_data_dir": str(env.user_data_dir), "profile_directory": env.profile_directory,
                     "cdp_endpoint": "http://127.0.0.1:9444", "exclusive_user_data": True,
                     "allow_launch": False}
            if _environment_from_value(value, config, checkout, local_app_data=local) != env:
                raise _EnrollmentHold("ENROLLMENT_OBSERVATION_CHANGED")
            payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            native.create_and_flush(payload)
            native.readback(payload)
            # Independent post-write token, process, listener, command, signature,
            # marker-holder and custody observations; any failure is UNKNOWN.
            if _enrollment_operator() != (local, session, sid):
                raise _EnrollmentHold("ENROLLMENT_OBSERVATION_CHANGED")
            current, current_identity = _existing_9444_candidate(_query_windows(9444), local=local,
                checkout=checkout, config=config, session=session, sid=sid)
            if current != env or current_identity != identity:
                raise _EnrollmentHold("ENROLLMENT_OBSERVATION_CHANGED")
            _check_enrollment_custody(current, local, sid, marker_proof=native.prove_marker)
            native.validate()
            native.readback(payload)
        result = _enrollment_hold("NATIVE_ATOMIC_ENROLLMENT_REQUIRED", assessed=True)
        result.update(status="ENROLLED", code="CONFIG_VERIFIED_CREATED", enrolled=True)
        return result
    except BaseException as exc:
        if native is not None and native.effect_possible:
            result = _enrollment_hold("ENROLLMENT_ASSESSMENT_UNKNOWN", assessed=True)
            result.update(status="UNKNOWN_EFFECT", code="UNKNOWN_EFFECT")
            return result
        if not isinstance(exc, Exception):
            raise
        return _enrollment_hold(str(exc) if isinstance(exc, (_EnrollmentHold, AcquisitionBlocked))
                                else "ENROLLMENT_ASSESSMENT_UNKNOWN")


_LOGON_CODES = frozenset({
    "FIRST_SUPERVISED_ENROLLMENT_REQUIRED", "LAUNCH_GRANT_REQUIRED",
    "WAIT_INTERACTIVE_OWNER", "UNTRUSTED_OWNER", "PROFILE_LOCK_HOLD",
    "CONFIG_UNTRUSTED", "ABSENCE_UNPROVEN", "REUSE_ONLY",
    "BLOCKED_NOT_DEPLOYED", "UNKNOWN_EFFECT",
})


def _logon_result(code: str) -> dict:
    # Fixed diagnostics only: never return config, inventory, grant or request bytes.
    code = code if code in _LOGON_CODES else "ABSENCE_UNPROVEN"
    return {"status": "UNKNOWN_EFFECT" if code == "UNKNOWN_EFFECT" else "HOLD",
            "code": code, "scope": "SOURCE_ONLY_ADVISORY",
            "native_adapter": "BLOCKED_NOT_DEPLOYED", "launch_authorized": False,
            "launched": False, "browser_accessed": False,
            "chat_identity_proven": False, "send_authorized": False}


def _read_9444_config(config: Path, local: Path, checkout: Path, sid: str) -> ChromeEnvironment:
    """Validate existing private inputs without enrolling or changing launch policy."""
    root = local / "AI-Commerce"
    from .local_completion_handoff import _private_identity
    if any(not _no_links(path) or _private_identity(path) != sid
           for path in (root, config.parent, config)):
        raise _EnrollmentHold("CONFIG_UNTRUSTED")
    env = load_environment(config, checkout, local_app_data=local)
    if env.port != 9444 or env.endpoint != "http://127.0.0.1:9444":
        raise _EnrollmentHold("CONFIG_UNTRUSTED")
    return env


def _logon_environment(local: Path, checkout: Path, sid: str) -> ChromeEnvironment | None:
    """Read only canonical enrollment using the shared six-name registry.

    Legacy candidates require attended reconciliation. Keep the established
    public CONFIG_UNTRUSTED advisory for them and duplicate/partial enrollment.
    Never rewrite N2's allow_launch=False to enable startup.
    """
    registry = _config_9444_registry(local, checkout)
    config = registry.canonical
    if registry.present and registry.present != (config,):
        raise _EnrollmentHold("CONFIG_UNTRUSTED")
    try:
        config.lstat()
    except FileNotFoundError:
        # An incomplete native reservation cannot be adopted or repaired here.
        if config.parent.exists():
            raise _EnrollmentHold("CONFIG_UNTRUSTED")
        return None
    return _read_9444_config(config, local, checkout, sid)


def _logon_inventory(inventory: dict) -> tuple[list[dict], tuple]:
    """Pin only exact 9444 observations; a failed/partial query is never ABSENT."""
    if type(inventory) is not dict or set(inventory) != {"listeners", "processes"}:
        raise _EnrollmentHold("ABSENCE_UNPROVEN")
    listeners, processes = _rows(inventory["listeners"]), _rows(inventory["processes"])
    pids = [p.get("ProcessId") for p in processes]
    if (len(listeners) > 1 or len(processes) > 256
            or any(type(pid) is not int or pid <= 0 for pid in pids)
            or len(set(pids)) != len(pids)):
        raise _EnrollmentHold("UNTRUSTED_OWNER")
    for row in listeners:
        if (row.get("LocalAddress") != "127.0.0.1" or type(row.get("LocalPort")) is not int
                or row["LocalPort"] != 9444 or type(row.get("OwningProcess")) is not int
                or row["OwningProcess"] not in pids):
            raise _EnrollmentHold("UNTRUSTED_OWNER")
    claimants = []
    for proc in processes:
        try:
            argv = _commandline_argv(proc.get("CommandLine"))
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 256
                    or any(not isinstance(arg, str) for arg in argv)):
                raise ValueError
        except Exception:
            raise _EnrollmentHold("UNTRUSTED_OWNER") from None
        if any(a.lower().startswith("--type=") for a in argv):
            continue
        ports = [a for a in argv if a.lower() == "--remote-debugging-port"
                 or a.lower().startswith("--remote-debugging-port=")]
        port = _switch(argv, "--remote-debugging-port")
        if ports and (len(ports) != 1 or not ports[0].startswith("--remote-debugging-port=")
                      or port is None or re.fullmatch(r"[0-9]{1,5}", port) is None):
            raise _EnrollmentHold("UNTRUSTED_OWNER")
        if port is not None and int(port) == 9444:
            claimants.append(proc["ProcessId"])
    if claimants != [row["OwningProcess"] for row in listeners]:
        raise _EnrollmentHold("UNTRUSTED_OWNER")
    # Copy immutable fields before a second independent query; never publish them.
    identity = tuple(tuple(p.get(k) for k in (
        "ProcessId", "ExecutablePath", "CommandLine", "SessionId", "OwnerSid", "CreationDate"))
        for p in sorted(processes, key=lambda p: p["ProcessId"]))
    return listeners, (tuple((r["LocalAddress"], r["LocalPort"], r["OwningProcess"])
                             for r in listeners), identity)


def _logon_observation(checkout: Path) -> tuple[str, tuple]:
    try:
        local, session, sid = _enrollment_operator()
    except Exception:
        raise _EnrollmentHold("WAIT_INTERACTIVE_OWNER") from None
    try:
        env = _logon_environment(local, checkout, sid)
    except Exception:
        raise _EnrollmentHold("CONFIG_UNTRUSTED") from None
    try:
        inventory = _query_windows(9444)
    except Exception:
        raise _EnrollmentHold("ABSENCE_UNPROVEN") from None
    listeners, identity = _logon_inventory(inventory)
    pinned = ((local, session, sid), env, identity)
    if env is None:
        return ("UNTRUSTED_OWNER" if listeners else "FIRST_SUPERVISED_ENROLLMENT_REQUIRED"), pinned
    try:
        # Same exact SID/ACL, signature, dedicated profile and marker guards as N2.
        _require_operator_control(env)
        _check_enrollment_custody(env, local, sid)
    except _EnrollmentHold as exc:
        raise _EnrollmentHold("PROFILE_LOCK_HOLD" if str(exc) ==
                              "ENROLLMENT_PROFILE_LOCK_UNPROVEN" else "UNTRUSTED_OWNER") from None
    except AcquisitionBlocked:
        raise _EnrollmentHold("UNTRUSTED_OWNER") from None
    if listeners:
        current, _ = _existing_9444_candidate(inventory, local=local, checkout=checkout,
            config=env.config_path, session=session, sid=sid)
        if current != replace(env, allow_launch=False):
            raise _EnrollmentHold("UNTRUSTED_OWNER")
        return "REUSE_ONLY", pinned
    state, _ = evaluate(env, inventory, session_id=session, expected_sid=sid,
                        parse=_commandline_argv)
    if state != "ABSENT":
        raise _EnrollmentHold("ABSENCE_UNPROVEN")
    # A launch-enabled config is also not a private, effect-specific grant.
    return ("BLOCKED_NOT_DEPLOYED" if env.allow_launch else "LAUNCH_GRANT_REQUIRED"), pinned


@dataclass(frozen=True, slots=True, repr=False)
class _Logon9444Assessment:
    """Private N3-A observations, never desktop authority or an effect grant.

    Retain the exact config and immutable owner/process observations for G2's
    source contract. Only _logon_result(code) may leave private custody.
    """
    code: str
    observation: tuple | None = None


def _assess_logon_9444_private(checkout: Path) -> _Logon9444Assessment:
    """Two fresh reads using the same guards as the public N3-A advisory."""
    observed = False
    try:
        before = _logon_observation(checkout)
        observed = True
        if _logon_observation(checkout) != before:
            return _Logon9444Assessment("UNKNOWN_EFFECT")
        return _Logon9444Assessment(before[0], before[1])
    except (_EnrollmentHold, AcquisitionBlocked) as exc:
        code = str(exc)
        if observed:
            code = "UNKNOWN_EFFECT"
        elif code == "PROFILE_ALREADY_ACTIVE_NO_CDP":
            code = "PROFILE_LOCK_HOLD"
        elif code in _ENROLLMENT_CODES and code not in _LOGON_CODES:
            code = "UNTRUSTED_OWNER"
        return _Logon9444Assessment(_logon_result(code)["code"])
    except Exception:
        return _Logon9444Assessment("UNKNOWN_EFFECT" if observed else "ABSENCE_UNPROVEN")


def assess_logon_9444(checkout: Path) -> dict:
    """Source-only logon/startup decision, with two fresh independent reads.

    No HTTP success, SID, config, Issue, runner or opt-in grants launch authority.
    REUSE_ONLY describes an enrolled owned listener, not protocol/account proof.
    START_ONCE_READY is unreachable: SignedOriginConsentAuthority authorizes only
    route/Wake operations, and the native origin interfaces enroll no Chrome
    launch owner. A future N3-B needs a separately authenticated one-shot grant,
    interactive owner, pinned ABSENT observation and durable replay custody.
    This policy never calls run(), attaches, creates config or mutates a profile.
    """
    return _logon_result(_assess_logon_9444_private(checkout).code)


class _Lifecycle9444Hold(ValueError):
    """Private lifecycle diagnostics, separate from the legacy probe/launch CLI."""


_Lifecycle9444State = Literal[
    "OFF_CLEAN_ENROLLED", "RUNNING_HEALTHY", "RUNNING_CDP_FAULT",
    "FOREIGN_PORT_OWNER", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN",
    "CONFIG_NOT_ENROLLED", "LEGACY_CANDIDATE", "INVENTORY_UNAVAILABLE", "UNKNOWN_EFFECT",
]


@dataclass(frozen=True, slots=True, repr=False)
class _Lifecycle9444Assessment:
    """Private source-only evidence, always HOLD; never a native W1 capability.

    Missing Windows owner/ACL/signature/lock evidence is a typed dependency.
    Observations stay private and are discarded on drift. No public API or
    fixed HostOps row schema is extended by this downstream integration seam.
    """
    state: _Lifecycle9444State
    code: str
    dependency: str | None = None
    observation: tuple | None = None
    status: Literal["HOLD"] = "HOLD"
    native_adapter: Literal["BLOCKED_NOT_DEPLOYED"] = "BLOCKED_NOT_DEPLOYED"


def _lifecycle_inventory(env: ChromeEnvironment, inventory: dict, *,
                         session: int, sid: str) -> tuple[str, tuple]:
    """Separate process presence from listener health; never infer OFF from HTTP."""
    if (type(inventory) is not dict or set(inventory) != {"listeners", "processes"}
            or any(type(inventory[k]) not in (list, dict) for k in inventory)):
        raise _Lifecycle9444Hold("WINDOWS_PROBE_INVALID")
    listeners, processes = _rows(inventory["listeners"]), _rows(inventory["processes"])
    pids = [p.get("ProcessId") for p in processes]
    if (len(processes) > 256 or any(type(pid) is not int or pid <= 0 for pid in pids)
            or len(set(pids)) != len(pids) or len(listeners) > 1):
        raise _Lifecycle9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
    for row in listeners:
        if type(row.get("LocalPort")) is not int or row["LocalPort"] != 9444:
            raise _Lifecycle9444Hold("WINDOWS_PROBE_INVALID")
        if (row.get("LocalAddress") != "127.0.0.1"
                or type(row.get("OwningProcess")) is not int or row["OwningProcess"] <= 0
                or row["OwningProcess"] not in pids):
            raise _Lifecycle9444Hold("FOREIGN_PORT_OWNER")
    profile_processes, port_claimants, profile_children = [], [], False
    for proc in processes:
        command = proc.get("CommandLine")
        owner_sid, created = proc.get("OwnerSid"), proc.get("CreationDate")
        if (not isinstance(command, str) or not 1 <= len(command) <= 32768
                or not isinstance(proc.get("ExecutablePath"), str)
                or type(proc.get("SessionId")) is not int or proc["SessionId"] < 0
                or not isinstance(owner_sid, str)
                or re.fullmatch(r"S-1-(?:\d+-){1,14}\d+", owner_sid) is None
                or not isinstance(created, str) or not 15 <= len(created) <= 80):
            raise _Lifecycle9444Hold("PROCESS_IDENTITY_UNKNOWN")
        try:
            if _creation_ticks(created) <= 0:
                raise ValueError
            argv = _commandline_argv(command)
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 256
                    or any(not isinstance(arg, str) or not arg for arg in argv)):
                raise ValueError
            for name in ("--user-data-dir", "--profile-directory", "--remote-debugging-address",
                         "--remote-debugging-port", "--type"):
                switches = [a for a in argv if a.lower() == name or a.lower().startswith(name + "=")]
                if switches and (len(switches) != 1 or not switches[0].startswith(name + "=")
                                 or not switches[0][len(name) + 1:]):
                    raise ValueError
            directory = _switch(argv, "--user-data-dir")
            child = _switch(argv, "--type") is not None
            # Chrome children may omit their inherited profile switch. They do
            # not constitute a second browser, but cannot prove clean absence.
            if directory is None and child:
                same_profile = None
            elif directory is None or not Path(directory).is_absolute():
                raise ValueError
            else:
                same_profile = Path(directory).resolve(strict=True) == env.user_data_dir
            port = _switch(argv, "--remote-debugging-port")
            if port is not None and (re.fullmatch(r"[0-9]{1,5}", port) is None
                                     or not 1 <= int(port) <= 65535):
                raise ValueError
        except Exception:
            raise _Lifecycle9444Hold("PROCESS_IDENTITY_UNKNOWN") from None
        if child:
            profile_children |= same_profile is not False
            continue
        if same_profile:
            profile_processes.append((proc, argv))
        if port is not None and int(port) == 9444:
            port_claimants.append(proc["ProcessId"])
    identity = (tuple((r["LocalAddress"], r["LocalPort"], r["OwningProcess"]) for r in listeners),
                tuple(tuple(p.get(k) for k in (
                    "ProcessId", "ExecutablePath", "CommandLine", "SessionId", "OwnerSid", "CreationDate"))
                    for p in sorted(processes, key=lambda p: p["ProcessId"])))
    if not profile_processes:
        if listeners:
            raise _Lifecycle9444Hold("FOREIGN_PORT_OWNER")
        if port_claimants or profile_children:
            raise _Lifecycle9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
        return "ABSENT", identity
    if len(profile_processes) != 1:
        raise _Lifecycle9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
    proc, argv = profile_processes[0]
    if (proc["OwnerSid"] != sid or proc["SessionId"] != session
            or not _identity_matches(env, proc["ExecutablePath"], argv)
            or sorted(argv[1:]) != sorted(env.command()[1:])
            or port_claimants != [proc["ProcessId"]]):
        raise _Lifecycle9444Hold("PROCESS_IDENTITY_UNKNOWN")
    if listeners and listeners[0]["OwningProcess"] != proc["ProcessId"]:
        raise _Lifecycle9444Hold("FOREIGN_PORT_OWNER")
    return ("OWNED" if listeners else "NO_LISTENER"), identity


def _lifecycle_9444_observation(checkout: Path) -> _Lifecycle9444Assessment:
    try:
        local, session, sid = _enrollment_operator()
    except Exception:
        return _Lifecycle9444Assessment("INVENTORY_UNAVAILABLE", "WAIT_INTERACTIVE_OWNER",
                                       "WINDOWS_INTERACTIVE_INVENTORY_REQUIRED")
    try:
        registry = _config_9444_registry(local, checkout)
        if len(registry.present) > 1:
            return _Lifecycle9444Assessment("CONFIG_NOT_ENROLLED", "CONFIG_AMBIGUOUS",
                                           "ATTENDED_CONFIG_RECONCILIATION_REQUIRED")
        if registry.selected is None:
            code = "CONFIG_UNTRUSTED" if registry.canonical.parent.exists() else "CONFIG_MISSING"
            return _Lifecycle9444Assessment("CONFIG_NOT_ENROLLED", code,
                                           "FIRST_SUPERVISED_ENROLLMENT_REQUIRED")
        env = _read_9444_config(registry.selected, local, checkout, sid)
        legacy = registry.selected != registry.canonical
        if legacy and not env.allow_launch:
            raise _EnrollmentHold("CONFIG_UNTRUSTED")
    except Exception:
        return _Lifecycle9444Assessment("CONFIG_NOT_ENROLLED", "CONFIG_UNTRUSTED",
                                       "WINDOWS_CONFIG_CUSTODY_REQUIRED")
    try:
        _require_operator_control(env)
        # Existing enrollment checks markers at the user-data root. Also refuse
        # markers inside the selected profile; never read/delete/adopt a lock.
        for name in _MARKERS:
            try:
                (env.user_data_dir / env.profile_directory / name).lstat()
            except FileNotFoundError:
                continue
            raise _EnrollmentHold("ENROLLMENT_PROFILE_LOCK_UNPROVEN")
        _check_enrollment_custody(env, local, sid)
    except _EnrollmentHold as exc:
        marker = str(exc) == "ENROLLMENT_PROFILE_LOCK_UNPROVEN"
        return _Lifecycle9444Assessment("PROFILE_LOCK_OR_IDENTITY_UNPROVEN",
            "PROFILE_LOCK_HOLD" if marker else "OPERATOR_OWNERSHIP_UNPROVEN",
            "WINDOWS_PROFILE_LOCK_HOLDER_REQUIRED" if marker else "WINDOWS_CUSTODY_REQUIRED")
    except Exception:
        return _Lifecycle9444Assessment("PROFILE_LOCK_OR_IDENTITY_UNPROVEN", "IDENTITY_UNPROVEN",
                                       "WINDOWS_OWNER_ACL_SIGNATURE_REQUIRED")
    pinned = ((local, session, sid), registry, env)
    if legacy:
        # Even a valid signed, private prior config is not canonical enrollment.
        return _Lifecycle9444Assessment("LEGACY_CANDIDATE", "ATTENDED_RECONCILIATION_REQUIRED",
                                       "ATTENDED_LEGACY_RECONCILIATION_REQUIRED", pinned)
    try:
        inventory = _query_windows(9444)
    except Exception:
        return _Lifecycle9444Assessment("INVENTORY_UNAVAILABLE", "WINDOWS_PROBE_UNAVAILABLE",
                                       "WINDOWS_PROCESS_PORT_INVENTORY_REQUIRED")
    try:
        state, identity = _lifecycle_inventory(env, inventory, session=session, sid=sid)
    except (AcquisitionBlocked, _Lifecycle9444Hold) as exc:
        if str(exc) == "FOREIGN_PORT_OWNER":
            return _Lifecycle9444Assessment("FOREIGN_PORT_OWNER", "FOREIGN_PORT_OWNER",
                                           "ATTENDED_PORT_RECONCILIATION_REQUIRED")
        if str(exc) == "WINDOWS_PROBE_INVALID":
            return _Lifecycle9444Assessment("INVENTORY_UNAVAILABLE", "WINDOWS_PROBE_INVALID",
                                           "WINDOWS_PROCESS_PORT_INVENTORY_REQUIRED")
        return _Lifecycle9444Assessment("PROFILE_LOCK_OR_IDENTITY_UNPROVEN", "PROCESS_IDENTITY_UNKNOWN",
                                       "WINDOWS_PROCESS_IDENTITY_REQUIRED")
    pinned += (identity,)
    if state == "ABSENT":
        return _Lifecycle9444Assessment("OFF_CLEAN_ENROLLED", "PROCESS_AND_LOCK_ABSENT",
                                       observation=pinned)
    if state == "NO_LISTENER":
        return _Lifecycle9444Assessment("RUNNING_CDP_FAULT", "CDP_LISTENER_MISSING",
                                       observation=pinned)
    try:
        _probe_cdp(env)  # Only after exact unique owned loopback/process proof.
    except Exception:
        return _Lifecycle9444Assessment("RUNNING_CDP_FAULT", "CDP_PROTOCOL_UNAVAILABLE",
                                       observation=pinned)
    return _Lifecycle9444Assessment("RUNNING_HEALTHY", "CDP_VERIFIED", observation=pinned)


def _assess_lifecycle_9444(checkout: Path) -> _Lifecycle9444Assessment:
    """Two bounded read-only observations, no launch/config write/attach/Send.

    A no-listener HTTP result is never identity or OFF evidence. CI and callers
    without trustworthy interactive Windows inventory HOLD. Future signed W1
    integration must independently verify custody and grant/replay authority.
    The existing public logon advisory and its consumers retain their schema.
    """
    observed = False
    try:
        before = _lifecycle_9444_observation(checkout)
        if before.observation is None:
            return before
        observed = True
        if _lifecycle_9444_observation(checkout) == before:
            return before
    except Exception:
        if not observed:
            return _Lifecycle9444Assessment("INVENTORY_UNAVAILABLE", "EVIDENCE_UNAVAILABLE",
                                           "WINDOWS_EVIDENCE_REQUIRED")
    return _Lifecycle9444Assessment("UNKNOWN_EFFECT", "OBSERVATION_CHANGED",
                                   "ATTENDED_RECONCILIATION_REQUIRED")


def _start_or_reuse_9444(checkout: Path, *, request_id: str, grant: object = None) -> dict:
    """Private N3-A seam, deliberately absent from CLI/public remote routes.

    No authenticated native launch/replay owner is deployed. Neither request IDs
    nor caller-supplied grant objects may supply that owner (or callbacks). Do
    not inspect/consume them, flip allow_launch, or invoke run(launch=True).
    Repeated logon/requests, revoked/expired/synthetic grants and uncertain
    observations therefore cannot trigger even a first start, much less retry.
    G2 WindowsPrivateRuntime.preflight_9444 binds signed consent and the exact
    private N3-A observations into a source contract, never an effect capability.
    Call assess_logon_9444 separately for advisory readiness. With no native
    backend this boundary returns before even launching diagnostic subprocesses.
    A future effect implementation requires separate local approval/enrollment.
    """
    return _logon_result("BLOCKED_NOT_DEPLOYED")


class _Clean9444Hold(ValueError):
    """Diagnostics returned by the clean 9444 JSON commands."""


_CLEAN_9444_PROFILE = "WakeHub-9444-Clean"
_CLEAN_9444_REGISTRY = r"Software\AI-Commerce\WakeHub-9444-Clean"
_CLEAN_9444_SLOT = r"Global\AI-Commerce-WakeHub-9444-Clean-v1"
_CLEAN_9444_PORT = 9444
_PROGRAM_FILES_FOLDERS = ("905E63B6-C1BF-494E-B29C-65B732D3D21A",
                          "7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E")
_CLEAN_9444_CODES = frozenset({
    "LOCAL_OPERATOR_ONLY", "WINDOWS_INTERACTIVE_REQUIRED", "WINDOWS_TOKEN_UNAVAILABLE",
    "ELEVATED_CHROME_DISALLOWED", "ENROLLMENT_IDENTITY_UNPROVEN", "OPT_IN_REQUIRED",
    "OPERATOR_OWNERSHIP_UNPROVEN",
    "PROFILE_UNAVAILABLE", "PROFILE_LOCKED", "PROFILE_ALREADY_ACTIVE_NO_CDP",
    "CHROME_PATH_INVALID", "CHROME_EXECUTABLE_UNTRUSTED", "SUPERVISOR_ACTIVE",
    "WINDOWS_PROBE_INVALID", "WINDOWS_PROBE_UNAVAILABLE", "FOREIGN_PORT_OWNER",
    "BROWSER_OWNERSHIP_UNPROVEN", "CDP_PROTOCOL_UNAVAILABLE", "UNKNOWN_EFFECT",
    "AUTOSTART_CONFLICT", "AUTOSTART_NOT_ENROLLED", "AUTOSTART_RUNTIME_UNAVAILABLE",
})


def _clean_hold(code: str) -> dict:
    return {"status": "HOLD", "code": code if code in _CLEAN_9444_CODES else
            "WINDOWS_PROBE_UNAVAILABLE"}


def _clean_operator() -> tuple[Path, int, str]:
    if os.environ.get("CI") or any(key.startswith("AI_COMMERCE_GIT_GUARD_") for key in os.environ):
        raise _Clean9444Hold("LOCAL_OPERATOR_ONLY")
    return _enrollment_operator()


def _clean_target(local: Path, sid: str) -> tuple[_ChromeTarget, tuple[Path, ...]]:
    from .local_completion_handoff import HandoffBlocked, _private_identity

    root = local / "AI-Commerce"
    directory = root / _CLEAN_9444_PROFILE
    for path in (root, directory, directory / "Default"):
        if not path.is_absolute() or not _no_links(path) or not path.is_dir():
            raise _Clean9444Hold("PROFILE_UNAVAILABLE")
        try:
            if _private_identity(path) != sid:
                raise _Clean9444Hold("OPERATOR_OWNERSHIP_UNPROVEN")
        except HandoffBlocked:
            raise _Clean9444Hold("OPERATOR_OWNERSHIP_UNPROVEN") from None

    roots = (*(_native_known_folder(folder) for folder in _PROGRAM_FILES_FOLDERS), local)
    candidates = []
    for parent in roots:
        path = parent / "Google" / "Chrome" / "Application" / "chrome.exe"
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        if not path.is_absolute() or not _no_links(path) or not path.is_file():
            raise _Clean9444Hold("CHROME_PATH_INVALID")
        candidates.append(path.resolve(strict=True))
    if not candidates:
        raise _Clean9444Hold("CHROME_PATH_INVALID")
    target = _ChromeTarget(candidates[0], directory.resolve(strict=True), "Default",
                           "http://127.0.0.1:9444", _CLEAN_9444_PORT)
    return target, tuple(candidates)


def _clean_switch(argv: list[str], name: str) -> str | None:
    matches = [arg for arg in argv[1:] if arg.lower().split("=", 1)[0] == name]
    if not matches:
        return None
    if len(matches) != 1 or not matches[0].startswith(name + "=") or matches[0] == name + "=":
        raise _Clean9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
    return matches[0][len(name) + 1:]


def _clean_inventory(target: _ChromeTarget, candidates: tuple[Path, ...],
                     session: int, sid: str, *, inventory: dict | None = None) -> tuple[str, tuple | None]:
    """Inspect only 9444 and claims on the fixed profile. Helpers are not browsers."""
    if inventory is None:
        inventory = _query_windows(_CLEAN_9444_PORT)
    if (type(inventory) is not dict or set(inventory) != {"listeners", "processes"}
            or any(type(rows) not in (list, dict) for rows in inventory.values())):
        raise _Clean9444Hold("WINDOWS_PROBE_INVALID")
    listeners, processes = _rows(inventory["listeners"]), _rows(inventory["processes"])
    pids = [p.get("ProcessId") for p in processes]
    if (len(listeners) > 1 or len(pids) > 256
            or any(type(pid) is not int or pid <= 0 for pid in pids) or len(set(pids)) != len(pids)):
        raise _Clean9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
    listener_pid = None
    if listeners:
        listener = listeners[0]
        listener_pid = listener.get("OwningProcess")
        if (listener.get("LocalPort") != _CLEAN_9444_PORT
                or listener.get("LocalAddress") != "127.0.0.1"
                or type(listener_pid) is not int or listener_pid not in pids):
            raise _Clean9444Hold("FOREIGN_PORT_OWNER")

    browsers, helpers = [], False
    for process in processes:
        pid, command = process["ProcessId"], process.get("CommandLine")
        argv = _commandline_argv(command)
        directory = _clean_switch(argv, "--user-data-dir")
        if directory is not None and not Path(directory).is_absolute():
            raise _Clean9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
        # Never resolve another Chrome profile or follow a remote path from argv.
        same_profile = directory is not None and Path(normpath(directory)) == target.user_data_dir
        child = _clean_switch(argv, "--type") is not None
        port = _clean_switch(argv, "--remote-debugging-port")
        owns_port = (port is not None and re.fullmatch(r"[0-9]{1,5}", port) is not None
                     and int(port) == target.port)
        if not (same_profile or pid == listener_pid or (owns_port and not child)):
            continue
        if not same_profile:
            raise _Clean9444Hold("FOREIGN_PORT_OWNER")
        exe, created = process.get("ExecutablePath"), process.get("CreationDate")
        if (process.get("OwnerSid") != sid or type(process.get("SessionId")) is not int
                or process["SessionId"] != session or not isinstance(exe, str)
                or not Path(exe).is_absolute() or Path(exe) not in candidates
                or not isinstance(created, str) or _creation_ticks(created) <= 0):
            raise _Clean9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
        if child:
            if pid == listener_pid:
                raise _Clean9444Hold("FOREIGN_PORT_OWNER")
            helpers = True
            continue
        # A manual start may omit Chrome's loopback and Default switches.
        # The listener must still be IPv4 loopback on the fixed owned directory.
        if (not owns_port or port != str(target.port)
                or Path(argv[0]) not in (Path(exe), Path("chrome.exe"))
                or _clean_switch(argv, "--profile-directory") not in (None, "Default")
                or _clean_switch(argv, "--remote-debugging-address") not in (None, "127.0.0.1")):
            raise _Clean9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
        browsers.append((pid, Path(exe), created, command))
    if len(browsers) > 1:
        raise _Clean9444Hold("BROWSER_OWNERSHIP_UNPROVEN")
    if not browsers:
        if helpers:
            raise _Clean9444Hold("PROFILE_ALREADY_ACTIVE_NO_CDP")
        return "ABSENT", None
    identity = browsers[0]
    if listener_pid is None:
        return "STARTING", identity
    if listener_pid != identity[0]:
        raise _Clean9444Hold("FOREIGN_PORT_OWNER")
    return "OWNED", identity


def _clean_require_unlocked(target: _ChromeTarget) -> None:
    for directory in (target.user_data_dir, target.user_data_dir / target.profile_directory):
        for name in _MARKERS:
            try:
                (directory / name).lstat()
            except FileNotFoundError:
                continue
            raise _Clean9444Hold("PROFILE_LOCKED")


@contextmanager
def _clean_supervisor_slot():
    """One native slot across processes and Windows sessions for the only port."""
    import ctypes
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexExW.argtypes = [ctypes.c_void_p, w.LPCWSTR, w.DWORD, w.DWORD]
    kernel.CreateMutexExW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexExW(None, _CLEAN_9444_SLOT, 0, 0x100000)
    if not handle:
        raise _Clean9444Hold("SUPERVISOR_ACTIVE")
    try:
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            raise _Clean9444Hold("SUPERVISOR_ACTIVE")
        yield
    finally:
        kernel.CloseHandle(handle)


def _clean_launch_pending() -> bool:
    return _clean_pending("LaunchPending")


def _clean_pending(name: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLEAN_9444_REGISTRY) as key:
            winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return False
    return True


def _clean_record_pending(name: str, pending: bool) -> None:
    import winreg

    if pending:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _CLEAN_9444_REGISTRY,
                                0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
            winreg.FlushKey(key)
    else:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLEAN_9444_REGISTRY,
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
            winreg.FlushKey(key)


# Both the attended command and the task run this one bounded action. Its only
# persistent browser state is LaunchPending under the existing private HKCU key.
_CLEAN_9444_LAUNCHER = r"""
$ErrorActionPreference = 'Stop'
$env:PSModulePath = Join-Path $PSHOME 'Modules'
function Get-OwnerSid($process) {
    (Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -OperationTimeoutSec 2).Sid
}

function Assert-OwnerSession {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $session = [Diagnostics.Process]::GetCurrentProcess().SessionId
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($identity.User.Value -cne $sid -or $session -le 0 -or
        ![Environment]::UserInteractive -or
        $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'WINDOWS_INTERACTIVE_REQUIRED'
    }
    $sessions = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -OperationTimeoutSec 2 |
        Where-Object { (Get-OwnerSid $_) -ceq $sid } |
        Select-Object -ExpandProperty SessionId -Unique)
    if ($sessions.Count -ne 1 -or $sessions[0] -ne $session) { throw 'WINDOWS_INTERACTIVE_REQUIRED' }
    return $session
}

function Assert-PlainPath($path) {
    while ($path) {
        $part = Get-Item -LiteralPath $path -Force
        if ($part.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'PROFILE_UNAVAILABLE' }
        $path = [IO.Path]::GetDirectoryName($path)
    }
}

function Assert-PrivateProfile {
    $local = [Environment]::GetFolderPath('LocalApplicationData')
    if (!$local -or $profileDir -ine (Join-Path $local 'AI-Commerce\WakeHub-9444-Clean')) {
        throw 'PROFILE_UNAVAILABLE'
    }
    foreach ($path in @((Split-Path -Parent $profileDir), $profileDir, (Join-Path $profileDir 'Default'))) {
        if (!(Test-Path -LiteralPath $path -PathType Container)) { throw 'PROFILE_UNAVAILABLE' }
        Assert-PlainPath $path
        $acl = Get-Acl -LiteralPath $path
        $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
        if ($descriptor.Owner.Value -cne $sid -or !$descriptor.DiscretionaryAcl -or
            $descriptor.DiscretionaryAcl.Count -gt 128) { throw 'OPERATOR_OWNERSHIP_UNPROVEN' }
        $ownerAllowed = $false
        foreach ($ace in $descriptor.DiscretionaryAcl) {
            if ($ace.AceType -eq 'AccessDenied') { continue }
            $principal = $ace.SecurityIdentifier.Value
            if ($ace.AceType -ne 'AccessAllowed' -or
                $principal -cnotin @($sid, 'S-1-5-18', 'S-1-5-32-544', 'S-1-3-4')) {
                throw 'OPERATOR_OWNERSHIP_UNPROVEN'
            }
            if ($principal -ceq $sid) { $ownerAllowed = $true }
        }
        if (!$ownerAllowed) { throw 'OPERATOR_OWNERSHIP_UNPROVEN' }
    }
}

function Assert-UnlockedProfile {
    foreach ($folder in @($profileDir, (Join-Path $profileDir 'Default'))) {
        foreach ($marker in $lockMarkers) {
            if (Get-ChildItem -LiteralPath $folder -Force -Filter $marker) { throw 'PROFILE_LOCKED' }
        }
    }
}

function Assert-SignedChrome($path) {
    Assert-PlainPath $path
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid' -or !$signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch '(^|, )CN=Google (LLC|Inc)(,|$)') {
        throw 'CHROME_EXECUTABLE_UNTRUSTED'
    }
}

function Read-Switch($arguments, $name) {
    $match = @($arguments | Where-Object { ($_ -split '=', 2)[0] -ieq $name })
    if (!$match.Count) { return $null }
    if ($match.Count -ne 1 -or !$match[0].StartsWith($name + '=') -or
        $match[0].Length -eq ($name.Length + 1)) { throw 'BROWSER_OWNERSHIP_UNPROVEN' }
    $match[0].Substring($name.Length + 1)
}

function Read-Chrome {
    $listeners = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -eq 9444 })
    if ($listeners.Count -gt 1) { throw 'BROWSER_OWNERSHIP_UNPROVEN' }
    $listener = $listeners | Select-Object -First 1
    if ($listener -and $listener.LocalAddress -cne '127.0.0.1') { throw 'FOREIGN_PORT_OWNER' }
    $browser = $null
    $helper = $false
    foreach ($process in (Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -OperationTimeoutSec 2)) {
        $line = $process.CommandLine
        # Accept the normal Windows Chrome quoting forms and hold on unparseable arguments.
        if (!$line -or $line -match '\\"|""' -or
            $line -notmatch '^\s*(?:(?>(?:[^\s"]+|"[^"]*")+)\s*)+$') {
            throw 'BROWSER_OWNERSHIP_UNPROVEN'
        }
        $arguments = @([regex]::Matches($line, '(?:[^\s"]+|"[^"]*")+') |
            ForEach-Object { $_.Value.Replace([string][char]34, '') })
        $switches = $arguments | Select-Object -Skip 1
        $directory = Read-Switch $switches '--user-data-dir'
        if ($directory -and $directory -notmatch '^[A-Za-z]:[\\/]') { throw 'BROWSER_OWNERSHIP_UNPROVEN' }
        $sameProfile = $directory -and [IO.Path]::GetFullPath($directory).TrimEnd([char]92) -ieq $profileDir
        $child = $null -ne (Read-Switch $switches '--type')
        $port = Read-Switch $switches '--remote-debugging-port'
        $holdsPort = $listener -and $process.ProcessId -eq $listener.OwningProcess
        if (!$sameProfile -and !$holdsPort -and ($child -or $port -notmatch '^0*9444$')) { continue }
        if (!$sameProfile) { throw 'FOREIGN_PORT_OWNER' }
        if ($process.SessionId -ne $session -or (Get-OwnerSid $process) -cne $sid -or
            $process.ExecutablePath -notin $executables -or !$process.CreationDate) {
            throw 'BROWSER_OWNERSHIP_UNPROVEN'
        }
        if ($child) {
            if ($holdsPort) { throw 'FOREIGN_PORT_OWNER' }
            $helper = $true
            continue
        }
        if ($browser -or $port -cne '9444' -or
            $arguments[0] -notin @($process.ExecutablePath, 'chrome.exe') -or
            (Read-Switch $switches '--profile-directory') -cnotin @($null, 'Default') -or
            (Read-Switch $switches '--remote-debugging-address') -cnotin @($null, '127.0.0.1')) {
            throw 'BROWSER_OWNERSHIP_UNPROVEN'
        }
        $browser = $process
    }
    if ($listener -and (!$browser -or $browser.ProcessId -ne $listener.OwningProcess)) {
        throw 'FOREIGN_PORT_OWNER'
    }
    if (!$browser) {
        if ($helper) { throw 'PROFILE_ALREADY_ACTIVE_NO_CDP' }
        return
    }
    [pscustomobject]@{ Pid = $browser.ProcessId; Path = $browser.ExecutablePath; Listening = [bool]$listener
        Identity = @($browser.ProcessId, $browser.CreationDate.Ticks, $browser.ExecutablePath,
            $browser.CommandLine) -join '|' }
}

function Read-DevToolsVersion($milliseconds) {
    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromMilliseconds($milliseconds)
        $client.MaxResponseContentBufferSize = 8192
        $reply = $client.GetAsync('http://127.0.0.1:9444/json/version').GetAwaiter().GetResult()
        if ([int]$reply.StatusCode -ne 200) { throw 'CDP_PROTOCOL_UNAVAILABLE' }
        $reply.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    } catch { throw 'CDP_PROTOCOL_UNAVAILABLE' }
    finally { $client.Dispose() }
}

function Confirm-DevTools($browser, $milliseconds) {
    $version = Read-DevToolsVersion $milliseconds
    if ($version.Browser -isnot [string] -or $version.Browser -cnotmatch '^(Headless)?Chrome/' -or
        $version.'Protocol-Version' -isnot [string] -or $version.'Protocol-Version' -notmatch '^[0-9]+\.[0-9]+$' -or
        $version.webSocketDebuggerUrl -isnot [string] -or
        $version.webSocketDebuggerUrl -cnotmatch '^ws://(localhost|127\.0\.0\.1):9444/devtools/browser/[^?#/\s]+$') {
        throw 'CDP_PROTOCOL_UNAVAILABLE'
    }
    $current = Read-Chrome
    if (!$current.Listening -or $current.Identity -cne $browser.Identity) { throw 'UNKNOWN_EFFECT' }
}

function Start-CleanChrome {
    $info = [Diagnostics.ProcessStartInfo]::new($executables[0], $chromeArguments)
    $info.UseShellExecute = $false
    foreach ($name in @($info.EnvironmentVariables.Keys)) {
        if ($name -notin $chromeEnvironment) { $info.EnvironmentVariables.Remove($name) }
    }
    [Diagnostics.Process]::Start($info)
}

function Open-LauncherSlot {
    $created = $false
    $mutex = [Threading.Mutex]::new($false, $mutexName, [ref]$created)
    if (!$created) { $mutex.Dispose(); throw 'SUPERVISOR_ACTIVE' }
    return $mutex
}

function Open-LaunchState($create) {
    if ($create) { return [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($registryPath) }
    return [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($registryPath, $true)
}

function Test-PortListening {
    [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq 9444 } | Select-Object -First 1
}

$slot = $null
$registry = $null
$pending = $false
try {
    $session = Assert-OwnerSession
    $slot = Open-LauncherSlot
    Assert-PrivateProfile
    $registry = Open-LaunchState
    $pending = $registry -and 'LaunchPending' -in $registry.GetValueNames()
    $browser = Read-Chrome
    if ($browser.Listening) {
        Assert-SignedChrome $browser.Path
        Confirm-DevTools $browser 2000
        if ($pending) { $registry.DeleteValue('LaunchPending'); $registry.Flush() }
        return 'REUSE'
    }
    if ($pending) { throw 'UNKNOWN_EFFECT' }
    if ($browser) { throw 'PROFILE_ALREADY_ACTIVE_NO_CDP' }
    Assert-UnlockedProfile
    Assert-SignedChrome $executables[0]
    if (!$registry) { $registry = Open-LaunchState $true }
    $pending = $true
    $registry.SetValue('LaunchPending', 1, [Microsoft.Win32.RegistryValueKind]::DWord)
    $registry.Flush()
    if ((Read-Chrome) -or (Assert-OwnerSession) -ne $session) { throw 'UNKNOWN_EFFECT' }
    Assert-UnlockedProfile
    $elapsed = [Diagnostics.Stopwatch]::StartNew()
    $chrome = Start-CleanChrome
    while (!$chrome.HasExited -and $elapsed.ElapsedMilliseconds -lt 15000) {
        if (Test-PortListening) {
            $browser = Read-Chrome
            $remaining = 15000 - $elapsed.ElapsedMilliseconds
            if ($browser.Listening -and $browser.Pid -eq $chrome.Id -and $remaining -gt 0) {
                Confirm-DevTools $browser ([math]::Min(2000, $remaining))
                if (!$chrome.HasExited -and $elapsed.ElapsedMilliseconds -lt 15000) {
                    $registry.DeleteValue('LaunchPending'); $registry.Flush()
                    return 'STARTED'
                }
            }
            break
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'UNKNOWN_EFFECT'
} catch {
    if ($pending) { 'UNKNOWN_EFFECT' }
    elseif ($_.Exception.Message -cin $holdCodes) { $_.Exception.Message }
    else { 'WINDOWS_PROBE_UNAVAILABLE' }
} finally {
    if ($registry) { $registry.Dispose() }
    if ($slot) { $slot.Dispose() }
}
"""


def _clean_launcher(owner: tuple[Path, int, str], target: _ChromeTarget,
                    candidates: tuple[Path, ...]) -> str:
    def literal(value: str) -> str:
        # Task Scheduler may expand percent-delimited text in exec arguments.
        if re.search(r"[%\r\n\x00\u2018\u2019\u201a\u201b]", value):
            raise _Clean9444Hold("AUTOSTART_RUNTIME_UNAVAILABLE")
        return "'" + value.replace("'", "''") + "'"

    values = {
        "sid": literal(owner[2]),
        "profileDir": literal(str(target.user_data_dir)),
        "executables": "@(" + ", ".join(literal(str(path)) for path in candidates) + ")",
        "chromeArguments": literal(subprocess.list2cmdline(target.command()[1:])),
        "chromeEnvironment": "@(" + ", ".join(map(literal, sorted(_CHILD_ENV_KEYS))) + ")",
        "lockMarkers": "@(" + ", ".join(map(literal, _MARKERS)) + ")",
        "registryPath": literal(_CLEAN_9444_REGISTRY),
        "mutexName": literal(_CLEAN_9444_SLOT),
        "holdCodes": "@(" + ", ".join(map(literal, sorted(_CLEAN_9444_CODES))) + ")",
    }
    bindings = "\n".join(f"${name} = {value}" for name, value in values.items())
    return "& {\n" + bindings + _CLEAN_9444_LAUNCHER + "\n}"


_CLEAN_POWERSHELL_FLAGS = ("-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command")


def ensure_clean_9444(*, opt_in: bool = False) -> dict:
    """Run the same fixed launcher once from the attended owner session."""
    if opt_in is not True:
        return _clean_hold("OPT_IN_REQUIRED")
    try:
        owner = _clean_operator()
        target, candidates = _clean_target(owner[0], owner[2])
        result = subprocess.run(
            [str(_powershell()), *_CLEAN_POWERSHELL_FLAGS, _clean_launcher(owner, target, candidates)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45, check=False,
            env=_child_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        code = result.stdout.strip().decode("ascii")
        if not result.returncode and code in {"STARTED", "REUSE"}:
            return {"status": code, "code": "CDP_VERIFIED"}
        return _clean_hold(code if not result.returncode else "UNKNOWN_EFFECT")
    except (AcquisitionBlocked, _EnrollmentHold, _Clean9444Hold) as exc:
        return _clean_hold(str(exc))
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return _clean_hold("UNKNOWN_EFFECT")


_CLEAN_9444_TASK_INSPECTION = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($sid -cne $env:AI_COMMERCE_TASK_SID) { throw 'Owner changed' }
$sddl = 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')'
$name = 'AI-Commerce-WakeHub-9444-Clean-' + $sid
$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder = $service.GetFolder('\')
$expected = $service.NewTask(0)
$expected.Principal.Id = 'WakeHubOwner'
$expected.Principal.UserId = $sid
$expected.Principal.LogonType = 3 # TASK_LOGON_INTERACTIVE_TOKEN
$expected.Principal.RunLevel = 0 # TASK_RUNLEVEL_LUA
$expected.Settings.MultipleInstances = 2 # TASK_INSTANCES_IGNORE_NEW
$expected.Settings.AllowDemandStart = $true
$expected.Settings.DisallowStartIfOnBatteries = $false
$expected.Settings.StopIfGoingOnBatteries = $false
$expected.Settings.ExecutionTimeLimit = 'PT45S'
$trigger = $expected.Triggers.Create(9) # TASK_TRIGGER_LOGON
$trigger.UserId = $sid
$trigger.Repetition.Interval = 'PT1M'
$expected.Actions.Context = $expected.Principal.Id
$action = $expected.Actions.Create(0) # TASK_ACTION_EXEC
$action.Path = $env:AI_COMMERCE_TASK_POWERSHELL
$action.Arguments = $env:AI_COMMERCE_TASK_ARGUMENTS
$action.WorkingDirectory = $env:AI_COMMERCE_TASK_DIRECTORY

function Read-Registration {
    try { $folder.GetTask($name) }
    catch {
        if ($_.Exception.GetBaseException().HResult -ne -2147024894) { throw }
    }
}

function Test-Owner($value) {
    if ($value -ceq $sid) { return $true }
    if ([string]::IsNullOrEmpty($value)) { return $false }
    try {
        $account = [Security.Principal.NTAccount]::new($value)
        return $account.Translate([Security.Principal.SecurityIdentifier]).Value -ceq $sid
    } catch { return $false }
}

function Test-Registration($registered) {
    if (!$registered -or !$registered.Enabled) { return $false }
    $acl = [Security.AccessControl.RawSecurityDescriptor]::new($registered.GetSecurityDescriptor(4))
    $want = [Security.AccessControl.RawSecurityDescriptor]::new($sddl)
    if ($acl.GetSddlForm('Access') -cne $want.GetSddlForm('Access')) { return $false }
    $definition = $registered.Definition
    $principal = $definition.Principal
    if ($principal.Id -cne $expected.Principal.Id -or !(Test-Owner $principal.UserId) -or
        $principal.LogonType -ne 3 -or
        $principal.RunLevel -ne 0 -or $principal.GroupId -or
        $definition.Triggers.Count -ne 1 -or $definition.Actions.Count -ne 1) { return $false }
    $t = $definition.Triggers.Item(1)
    $a = $definition.Actions.Item(1)
    if ($t.Type -ne 9 -or !$t.Enabled -or !(Test-Owner $t.UserId) -or $t.Delay -cne $trigger.Delay -or
        $t.StartBoundary -cne $trigger.StartBoundary -or $t.EndBoundary -cne $trigger.EndBoundary -or
        $t.Repetition.Interval -cne $trigger.Repetition.Interval -or
        $t.Repetition.Duration -cne $trigger.Repetition.Duration -or
        $t.Repetition.StopAtDurationEnd -ne $trigger.Repetition.StopAtDurationEnd -or
        $t.ExecutionTimeLimit -cne $trigger.ExecutionTimeLimit -or
        $a.Type -ne 0 -or $a.Path -cne $action.Path -or
        $a.Arguments -cne $action.Arguments -or $a.WorkingDirectory -cne $action.WorkingDirectory -or
        $definition.Actions.Context -cne $expected.Actions.Context) {
        return $false
    }
    foreach ($key in @('AllowDemandStart', 'AllowHardTerminate', 'DisallowStartIfOnBatteries',
        'Enabled', 'ExecutionTimeLimit', 'Hidden', 'MultipleInstances', 'RestartCount', 'RestartInterval',
        'RunOnlyIfIdle', 'RunOnlyIfNetworkAvailable', 'StartWhenAvailable',
        'StopIfGoingOnBatteries', 'WakeToRun', 'DeleteExpiredTaskAfter')) {
        if ($definition.Settings.$key -cne $expected.Settings.$key) { return $false }
    }
    return $true
}

$registered = Read-Registration
"""


_CLEAN_9444_TASK_READ_SCRIPT = _CLEAN_9444_TASK_INSPECTION + r"""
if (!$registered) { 'AUTOSTART_NOT_ENROLLED' }
elseif (Test-Registration $registered) { 'ALREADY_ENROLLED' }
else { 'AUTOSTART_CONFLICT' }
"""


_CLEAN_9444_TASK_SCRIPT = _CLEAN_9444_TASK_INSPECTION + r"""
if ($env:AI_COMMERCE_TASK_START_SESSION) {
    $session = [int]$env:AI_COMMERCE_TASK_START_SESSION
    if ($session -le 0 -or $session -ne [Diagnostics.Process]::GetCurrentProcess().SessionId -or
        !(Test-Registration $registered)) { 'AUTOSTART_CONFLICT'; exit }
    if ($registered.State -ne 3) { 'START_NOT_ATTEMPTED'; exit } # TASK_STATE_READY
    # TASK_RUN_USE_SESSION_ID | TASK_RUN_USER_SID, no substituted arguments.
    $instance = $registered.RunEx($null, 12, $session, $sid)
    if (!$instance -or !$instance.InstanceGuid) { 'UNKNOWN_EFFECT'; exit }
    'START_REQUESTED'
    exit
}
if ($registered) {
    if (Test-Registration $registered) { 'ALREADY_ENROLLED' } else { 'AUTOSTART_CONFLICT' }
    exit
}
if ($env:AI_COMMERCE_TASK_INSTALL -cne '1') { 'AUTOSTART_NOT_ENROLLED'; exit }
# TASK_CREATE | TASK_IGNORE_REGISTRATION_TRIGGERS. A racing registration is never updated.
try { $null = $folder.RegisterTaskDefinition($name, $expected, 34, $sid, $null, 3, $sddl) }
catch {
    if ($_.Exception.GetBaseException().HResult -ne -2147024713) { throw }
    if (Test-Registration (Read-Registration)) { 'ALREADY_ENROLLED' } else { 'AUTOSTART_CONFLICT' }
    exit
}
if (Test-Registration (Read-Registration)) { 'ENROLLED' } else { 'UNKNOWN_EFFECT' }
"""


def _clean_task(owner: tuple[Path, int, str], target: _ChromeTarget,
                candidates: tuple[Path, ...], *, install: bool, start: bool = False) -> str:
    """Register or inspect the same fixed, interactive PowerShell task."""
    _, session, sid = owner
    if start and install:
        raise _Clean9444Hold("AUTOSTART_RUNTIME_UNAVAILABLE")
    powershell = _powershell()
    launcher = _clean_launcher(owner, target, candidates)
    child_env = _child_environment()
    child_env.update(AI_COMMERCE_TASK_SID=sid, AI_COMMERCE_TASK_POWERSHELL=str(powershell),
                     AI_COMMERCE_TASK_DIRECTORY=str(powershell.parent),
                     AI_COMMERCE_TASK_ARGUMENTS=subprocess.list2cmdline([*_CLEAN_POWERSHELL_FLAGS, launcher]),
                     AI_COMMERCE_TASK_START_SESSION=str(session) if start else "",
                     AI_COMMERCE_TASK_INSTALL="1" if install else "0")
    script = _CLEAN_9444_TASK_SCRIPT if install or start else _CLEAN_9444_TASK_READ_SCRIPT
    try:
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                                 script], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=15, check=False, env=child_env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        code = result.stdout.strip().decode("ascii")
        expected = {"AUTOSTART_CONFLICT"}
        if start:
            expected.update(("START_REQUESTED", "START_NOT_ATTEMPTED"))
        else:
            expected.update(("ALREADY_ENROLLED", "AUTOSTART_NOT_ENROLLED"))
            if install:
                expected.add("ENROLLED")
        if not result.returncode and code in expected:
            return code
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    return "UNKNOWN_EFFECT" if install or start else "AUTOSTART_RUNTIME_UNAVAILABLE"


def install_clean_9444_autostart(*, opt_in: bool = False) -> dict:
    """Enroll the fixed task from an attended, non-elevated owner session."""
    if opt_in is not True:
        return _clean_hold("OPT_IN_REQUIRED")
    try:
        owner = _clean_operator()
        target, candidates = _clean_target(owner[0], owner[2])
        _verify_google_chrome(target)
        code = _clean_task(owner, target, candidates, install=True)
        if code in {"ENROLLED", "ALREADY_ENROLLED"}:
            return {"status": "ENROLLED", "code": code}
        return _clean_hold(code)
    except (AcquisitionBlocked, _EnrollmentHold, _Clean9444Hold) as exc:
        return _clean_hold(str(exc))
    except Exception:
        return _clean_hold("AUTOSTART_RUNTIME_UNAVAILABLE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.remote.chrome_acquisition",
        epilog="Attended native operations require explicit local --opt-in.", allow_abbrev=False)
    parser.add_argument("command", choices=("probe", "launch", "assess-existing-9444",
                                          "enroll-existing-9444", "ensure-clean-9444",
                                          "install-clean-9444-autostart"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--opt-in", action="store_true")
    args = parser.parse_args(argv)
    if args.command in {"ensure-clean-9444", "install-clean-9444-autostart"}:
        if args.config is not None or args.checkout is not None:
            parser.error("clean 9444 uses only fixed local paths")
        if args.command == "install-clean-9444-autostart":
            result = install_clean_9444_autostart(opt_in=args.opt_in)
        else:
            result = ensure_clean_9444(opt_in=args.opt_in)
        print(json.dumps(result, sort_keys=True))
        return 2 if result["status"] == "HOLD" else 0
    if args.checkout is None:
        parser.error("--checkout is required")
    if args.command in {"assess-existing-9444", "enroll-existing-9444"}:
        if args.config is not None:
            parser.error("enrollment uses only the fixed private config location")
        result = (assess_existing_9444(args.checkout) if args.command == "assess-existing-9444"
                  else enroll_existing_9444(args.checkout, opt_in=args.opt_in))
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "ENROLLED" else 2
    if args.config is None:
        parser.error("--config is required for probe/launch")
    try:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise AcquisitionBlocked("LOCAL_APP_DATA_UNAVAILABLE")
        env = load_environment(args.config, args.checkout, local_app_data=Path(local))
        result = run(env, launch=args.command == "launch", opt_in=args.opt_in)
        print(json.dumps(result, sort_keys=True))
        return 0
    except AcquisitionBlocked as exc:
        print("CHROME_ACQUISITION_BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
