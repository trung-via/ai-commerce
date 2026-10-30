"""Explicit, operator-owned Chrome CDP acquisition for AI Commerce EF02-D5.

Standalone preflight/launch. Never reads chats, copies profiles, sends Doorbells,
activates a worker, or imports the AIOS-Renew runtime.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
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
    Select-Object LocalAddress, OwningProcess)
$processes = @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction Stop |
    Select-Object ProcessId, ExecutablePath, CommandLine, SessionId)
@{listeners=$listeners; processes=$processes} | ConvertTo-Json -Depth 5 -Compress
"""


class AcquisitionBlocked(ValueError):
    """Only short, non-sensitive diagnostic codes are exposed."""


@dataclass(frozen=True)
class ChromeEnvironment:
    executable: Path
    user_data_dir: Path
    profile_directory: str
    endpoint: str
    port: int
    allow_launch: bool
    config_path: Path

    def command(self) -> list[str]:
        return [str(self.executable),
                f"--user-data-dir={self.user_data_dir}",
                f"--profile-directory={self.profile_directory}",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={self.port}",
                "--no-first-run", "--no-default-browser-check"]


def _no_links(path: Path) -> bool:
    """Do not follow a link in any existing ancestor of a trusted path."""
    return not any(part.is_symlink() for part in (path, *path.parents))


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


def _identity_matches(env: ChromeEnvironment, exe: str, argv: list[str]) -> bool:
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


def _verify_google_chrome(env: ChromeEnvironment) -> None:
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
            timeout=8, check=False, env=child_env)
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


def _probe_cdp(env: ChromeEnvironment) -> None:
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
                                timeout=8, check=False, env=child_env)
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
             session_id: int) -> tuple[str, int | None]:
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
                or len(claimed) != 1 or claimed[0] is not proc):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.remote.chrome_acquisition")
    parser.add_argument("command", choices=("probe", "launch"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--opt-in", action="store_true")
    args = parser.parse_args(argv)
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
