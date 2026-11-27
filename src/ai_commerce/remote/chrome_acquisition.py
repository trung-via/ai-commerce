"""Explicit, operator-owned Chrome CDP acquisition for AI Commerce EF02-D5.

Standalone preflight/enrollment/launch. Never reads chats, copies profiles, sends Doorbells,
activates a worker, or imports the AIOS-Renew runtime.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum, auto
import hashlib
import json
import os
from os.path import normpath
from pathlib import Path
import re
from secrets import token_hex
import socket
import stat
from struct import Struct
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Callable, Literal
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}\Z")
_MARKERS = ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")
_LEGACY_MARKER_HOLDS = frozenset({
    "LEGACY_MARKER_ACCESS_UNPROVEN", "LEGACY_MARKER_ACL_UNPROVEN",
    "LEGACY_MARKER_PROCESS_UNPROVEN", "LEGACY_MARKER_HOLDER_UNPROVEN",
})
_WINDOWS_PROBE_FAILURE_CODES = frozenset({
    "WINDOWS_PROBE_TCP_FAILED", "WINDOWS_PROBE_CIM_FAILED", "WINDOWS_PROBE_OWNER_FAILED",
    "WINDOWS_PROBE_TIMEOUT",
})
_ATTENDED_CLI_INVENTORY_HOLDS = _WINDOWS_PROBE_FAILURE_CODES | {"CHROME_OBSERVATION_CHANGED"}


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


class _SupervisorConfigCheck(Enum):
    UNAVAILABLE = auto()
    USER_ENV_READ = auto()
    USER_ENV_VALUE = auto()
    OVERRIDE_PATH = auto()
    OVERRIDE_MISSING = auto()
    DISCOVERY_PATH = auto()
    RUNTIME_AMBIGUOUS = auto()
    LEGACY_REGISTRY_DISCOVERY = auto()
    LEGACY_REGISTRY_EMPTY = auto()
    LEGACY_REGISTRY_AMBIGUOUS = auto()
    CONFIG_ROOT_CUSTODY = auto()
    CONFIG_PARENT_CUSTODY = auto()
    CONFIG_FILE_CUSTODY = auto()
    PROFILE_CUSTODY = auto()
    CONFIG_PRIVATE_PATH = auto()
    CONFIG_ANCESTOR_HANDLE = auto()
    CONFIG_FILE_READ = auto()
    CONFIG_JSON = auto()
    CONFIG_SCHEMA = auto()
    CONFIG_PATH = auto()
    WRAPPER_SCHEMA = auto()
    WRAPPER_REFERENCE = auto()
    RUNTIME_SCHEMA_OR_PATH = auto()
    RUNTIME_OWNER = auto()
    RUNTIME_REFERENCES = auto()
    RUNTIME_CHROME_REFERENCE = auto()
    CHROME_SIGNATURE = auto()
    CONFIG_PIN_CHANGED = auto()


_supervisor_config_failure: ContextVar[Callable[[_SupervisorConfigCheck], None] | None] = ContextVar(
    "supervisor_config_failure", default=None,
)


@contextmanager
def _supervisor_config_check(check: _SupervisorConfigCheck):
    """Observe a failing check without changing the underlying exception."""
    report = _supervisor_config_failure.get()
    try:
        yield
    except Exception:
        if report is not None:
            report(check)
        raise


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
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_PATH):
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
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_FILE_READ):
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
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_JSON):
            value = json.loads(raw, object_pairs_hook=unique)
        return _environment_from_value(value, config_path, checkout, local_app_data=local_app_data)
    except AcquisitionBlocked:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise AcquisitionBlocked("CONFIG_INVALID") from None


_CHROME_CONFIG_FIELDS = frozenset({
    "version", "executable", "user_data_dir", "profile_directory", "cdp_endpoint",
    "allow_launch", "exclusive_user_data",
})


def _environment_from_value(value, config_path: Path, checkout: Path, *,
                            local_app_data: Path) -> ChromeEnvironment:
    """Shared closed schema/path validation; this helper never creates a config.

    load_environment additionally requires an existing private config. An
    enrollment assessment can validate an in-memory candidate with these same
    profile rules without weakening that existing-file requirement.
    """
    try:
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_SCHEMA):
            if (not isinstance(value, dict) or value.keys() != _CHROME_CONFIG_FIELDS
                    or type(value["version"]) is not int or value["version"] != 1
                    or type(value["allow_launch"]) is not bool
                    or value["exclusive_user_data"] is not True
                    or type(value["profile_directory"]) is not str
                    or not _PROFILE.fullmatch(value["profile_directory"])):
                raise AcquisitionBlocked("CONFIG_INVALID")
            if type(value["executable"]) is not str or type(value["user_data_dir"]) is not str:
                raise AcquisitionBlocked("CONFIG_INVALID")
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_PATH):
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
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_SCHEMA):
            endpoint = value["cdp_endpoint"]
            if type(endpoint) is not str:
                raise AcquisitionBlocked("CDP_ENDPOINT_INVALID")
            parsed = urlsplit(endpoint)
            if (parsed.scheme != "http" or parsed.netloc != f"127.0.0.1:{parsed.port}"
                    or parsed.port is None or parsed.port < 1024
                    or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
                raise AcquisitionBlocked("CDP_ENDPOINT_INVALID")
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_PATH):
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


def _native_program_files() -> Path:
    import winreg

    # Read the native machine view even when the Python caller runs under WOW64.
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion",
                       access=winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY) as key:
        value, kind = winreg.QueryValueEx(key, "ProgramFilesDir")
    if kind != winreg.REG_SZ:
        raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
    return Path(value)


_WINDOWS_INSTALL_OWNERS = frozenset({
    "S-1-5-18", "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # TrustedInstaller
})


def _verify_powershell_custody(exe: Path) -> None:
    import ctypes as c
    from ctypes import wintypes as w

    ptr = c.c_void_p
    adv = c.WinDLL("advapi32", winmode=0x800)
    kernel = c.WinDLL("kernel32", winmode=0x800)
    adv.GetNamedSecurityInfoW.argtypes = (w.LPWSTR, w.DWORD, w.DWORD, c.POINTER(ptr),
                                         ptr, c.POINTER(ptr), ptr, c.POINTER(ptr))
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.GetAce.argtypes = (ptr, w.DWORD, c.POINTER(ptr))
    adv.GetAce.restype = w.BOOL
    adv.ConvertSidToStringSidW.argtypes = (ptr, c.POINTER(w.LPWSTR))
    adv.ConvertSidToStringSidW.restype = w.BOOL
    kernel.LocalFree.argtypes = (ptr,)
    kernel.LocalFree.restype = ptr
    kernel.GetDriveTypeW.argtypes = (w.LPCWSTR,)
    kernel.GetDriveTypeW.restype = w.UINT

    def trusted(sid) -> bool:
        value = w.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid, c.byref(value)):
            return False
        try:
            return value.value in _WINDOWS_INSTALL_OWNERS
        finally:
            kernel.LocalFree(value)

    if kernel.GetDriveTypeW(exe.anchor) != 3:  # DRIVE_FIXED
        raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
    for item in (*reversed(exe.parents), exe):
        owner, acl, descriptor = ptr(), ptr(), ptr()
        if adv.GetNamedSecurityInfoW(str(item), 1, 5, c.byref(owner), None,
                                     c.byref(acl), None, c.byref(descriptor)):
            raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
        try:
            if not owner or not acl or not trusted(owner):
                raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
            # The volume root can permit creating unrelated entries. It must
            # still prevent deleting or replacing its protected children.
            forbidden = 0x500D0040 if item == Path(exe.anchor) else 0x500D0156
            for index in range(c.c_ushort.from_address(acl.value + 4).value):
                ace = ptr()
                if not adv.GetAce(acl, index, c.byref(ace)):
                    raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
                kind, flags = (c.c_ubyte * 2).from_address(ace.value)
                if flags & 8 or kind == 1:  # Inherit-only or access denied.
                    continue
                if kind != 0 or (w.DWORD.from_address(ace.value + 4).value & forbidden
                                 and not trusted(ace.value + 8)):
                    raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
        finally:
            kernel.LocalFree(descriptor)


def _microsoft_signed_powershell(exe: Path) -> bool:
    import ctypes as c
    from ctypes import wintypes as w
    from uuid import UUID

    ptr = c.c_void_p

    class TrustFile(c.Structure):
        _fields_ = [("size", w.DWORD), ("path", w.LPCWSTR), ("file", w.HANDLE), ("subject", ptr)]

    class TrustData(c.Structure):
        _fields_ = [("size", w.DWORD), ("policy_data", ptr), ("sip_data", ptr),
                    ("ui", w.DWORD), ("revocation", w.DWORD), ("choice", w.DWORD),
                    ("file", c.POINTER(TrustFile)), ("action", w.DWORD), ("state", w.HANDLE),
                    ("url", w.LPCWSTR), ("flags", w.DWORD), ("context", w.DWORD),
                    ("signature_settings", ptr)]

    class ProviderCertificate(c.Structure):
        _fields_ = [("size", w.DWORD), ("certificate", ptr)]  # CRYPT_PROVIDER_CERT prefix

    trust = c.WinDLL("wintrust", winmode=0x800)
    crypt = c.WinDLL("crypt32", winmode=0x800)
    trust.WinVerifyTrust.argtypes = (w.HWND, ptr, c.POINTER(TrustData))
    trust.WinVerifyTrust.restype = w.LONG
    trust.WTHelperProvDataFromStateData.argtypes = (w.HANDLE,)
    trust.WTHelperProvDataFromStateData.restype = ptr
    trust.WTHelperGetProvSignerFromChain.argtypes = (ptr, w.DWORD, w.BOOL, w.DWORD)
    trust.WTHelperGetProvSignerFromChain.restype = ptr
    trust.WTHelperGetProvCertFromChain.argtypes = (ptr, w.DWORD)
    trust.WTHelperGetProvCertFromChain.restype = c.POINTER(ProviderCertificate)
    crypt.CertGetNameStringW.argtypes = (ptr, w.DWORD, w.DWORD, ptr, w.LPWSTR, w.DWORD)
    crypt.CertGetNameStringW.restype = w.DWORD

    action = (c.c_ubyte * 16).from_buffer_copy(UUID("00AAC56B-CD44-11D0-8CC2-00C04FC295EE").bytes_le)
    file = TrustFile(size=c.sizeof(TrustFile), path=str(exe))
    data = TrustData(size=c.sizeof(TrustData), ui=2, choice=1, file=c.pointer(file),
                     action=1, flags=0x1000)  # WTD_UI_NONE, WTD_CHOICE_FILE, VERIFY, cache only
    try:
        if trust.WinVerifyTrust(None, c.byref(action), c.byref(data)) != 0 or not data.state:
            return False
        provider = trust.WTHelperProvDataFromStateData(data.state)
        signer = provider and trust.WTHelperGetProvSignerFromChain(provider, 0, False, 0)
        cert = signer and trust.WTHelperGetProvCertFromChain(signer, 0)
        if not cert or not cert.contents.certificate:
            return False
        publisher = c.create_unicode_buffer(256)
        length = crypt.CertGetNameStringW(cert.contents.certificate, 4, 0, None, publisher, len(publisher))
        return 1 < length <= len(publisher) and publisher.value == "Microsoft Corporation"
    finally:
        data.action = 2  # WTD_STATEACTION_CLOSE
        trust.WinVerifyTrust(None, c.byref(action), c.byref(data))


def _powershell_file_major(exe: Path) -> int:
    import ctypes as c
    from ctypes import wintypes as w

    version = c.WinDLL("version", winmode=0x800)
    version.GetFileVersionInfoSizeExW.argtypes = (w.DWORD, w.LPCWSTR, c.POINTER(w.DWORD))
    version.GetFileVersionInfoSizeExW.restype = w.DWORD
    version.GetFileVersionInfoExW.argtypes = (w.DWORD, w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p)
    version.GetFileVersionInfoExW.restype = w.BOOL
    version.VerQueryValueW.argtypes = (c.c_void_p, w.LPCWSTR, c.POINTER(c.c_void_p), c.POINTER(w.UINT))
    version.VerQueryValueW.restype = w.BOOL
    # Read the fixed version from the signed file's language-neutral resource.
    unused = w.DWORD()
    size = version.GetFileVersionInfoSizeExW(2, str(exe), c.byref(unused))
    if not 0 < size <= 65536:
        return 0
    data = c.create_string_buffer(size)
    value, length = c.c_void_p(), w.UINT()
    if (not version.GetFileVersionInfoExW(2, str(exe), 0, size, data)
            or not version.VerQueryValueW(data, "\\", c.byref(value), c.byref(length))
            or not value or length.value != 52):  # sizeof(VS_FIXEDFILEINFO)
        return 0
    signature, _, file_version_ms = (w.DWORD * 3).from_address(value.value)
    if signature != 0xFEEF04BD:
        return 0

    # A Microsoft signature alone does not identify the executable as pwsh.
    if (not version.VerQueryValueW(data, r"\VarFileInfo\Translation", c.byref(value), c.byref(length))
            or not value or not 0 < length.value <= 64 or length.value % 4):
        return 0
    translations = (w.WORD * (length.value // 2)).from_address(value.value)

    def field(prefix: str, name: str) -> str:
        result, chars = c.c_void_p(), w.UINT()
        if (version.VerQueryValueW(data, prefix + name, c.byref(result), c.byref(chars))
                and result and 1 < chars.value <= 64):
            return c.wstring_at(result, chars.value - 1)
        return ""

    for language, code_page in zip(translations[::2], translations[1::2]):
        prefix = f"\\StringFileInfo\\{language:04x}{code_page:04x}\\"
        if (field(prefix, "ProductName") in {"PowerShell", "PowerShell Core"}
                and field(prefix, "OriginalFilename").casefold() in {"pwsh.exe", "pwsh.dll"}):
            return file_version_ms >> 16
    return 0


def _trusted_powershells() -> tuple[Path, ...]:
    """Discover and attest PowerShell in the native Program Files install layout."""
    try:
        if os.name != "nt":
            raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
        root = _native_program_files()
        installs = root / "PowerShell"
        if (not root.is_absolute() or not re.fullmatch(r"[A-Za-z]:", root.drive)
                or not _no_links(installs)):
            raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
        candidates = []
        for slot in sorted(installs.iterdir()):
            if not re.fullmatch(r"7(?:\.[0-9]+){0,2}(?:-preview)?", slot.name, re.IGNORECASE):
                continue
            if not _no_links(slot):
                continue
            exe = slot / "pwsh.exe"
            try:
                if not _no_links(exe) or not exe.is_file() or exe.resolve(strict=True) != exe:
                    continue
                _verify_powershell_custody(exe)
                if _microsoft_signed_powershell(exe) and _powershell_file_major(exe) >= 7:
                    candidates.append(exe)
            except (OSError, ValueError):
                # Each install must prove its own custody before joining the trusted set.
                continue
        if not candidates:
            raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
        return tuple(candidates)
    except (AttributeError, ImportError, OSError, ValueError):
        raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE") from None


_read_only_powershell_owner_action: ContextVar[Callable[[Path], Path] | None] = ContextVar(
    "read_only_powershell_owner_action", default=None,
)


@contextmanager
def _prefer_installed_powershell():
    """Select a verified installed PowerShell without consulting the clean 9444 task."""
    token = _read_only_powershell_owner_action.set(lambda bootstrap: bootstrap)
    try:
        yield
    finally:
        _read_only_powershell_owner_action.reset(token)


def _powershell(*, owner_action: Callable[[Path], Path] | None = None) -> Path:
    """Resolve one verified installation, using the relevant owner action if needed."""
    candidates = _trusted_powershells()
    if len(candidates) == 1:
        return candidates[0]
    try:
        selector = owner_action or _read_only_powershell_owner_action.get() or _clean_task_powershell
        # Only the fixed, read-only task probe uses the bootstrap image.
        action = selector(candidates[0])
        return candidates[candidates.index(action)]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE") from None


_POWERSHELL_TASK_READ_PREAMBLE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules')
"""

_PROBE_SCRIPT = _POWERSHELL_TASK_READ_PREAMBLE + r"""
try {
    $rows = @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -OperationTimeoutSec 3 `
        -Property ProcessId, ParentProcessId, ExecutablePath, CommandLine, SessionId, CreationDate)
} catch { exit 20 }
try {
    $processes = @(foreach ($row in $rows) {
        [pscustomobject]@{
            ProcessId = $row.ProcessId; ParentProcessId = $row.ParentProcessId; ExecutablePath = $row.ExecutablePath
            CommandLine = $row.CommandLine; SessionId = $row.SessionId
            CreationDate = if ($null -ne $row.CreationDate) { $row.CreationDate.ToUniversalTime().ToString('o') }
        }
    })
    ConvertTo-Json -InputObject @{processes = $processes} -Depth 3 -Compress
} catch { exit 21 }
"""


def _verify_google_chrome(env: _ChromeTarget, *,
                          owner_action: Callable[[Path], Path] | None = None) -> None:
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
            [str(_powershell(owner_action=owner_action) if owner_action else _powershell()),
             "-NoProfile", "-NonInteractive", "-Command", script],
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
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_FILE_CUSTODY):
            _private_identity(env.config_path)
        with _supervisor_config_check(_SupervisorConfigCheck.PROFILE_CUSTODY):
            for path in (env.user_data_dir, env.user_data_dir / env.profile_directory):
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


# MIB_TCP{,6}ROW_OWNER_PID, with remote fields skipped.
_TCP4_ROW = Struct("<I4sI8xI")
_TCP6_ROW = Struct("<16sII24xII")
_TCP_COUNT = Struct("<I")


def _tcp_listener_rows(buffer, family: int, port: int) -> list[dict]:
    row = _TCP4_ROW if family == socket.AF_INET else _TCP6_ROW
    if len(buffer) < _TCP_COUNT.size:
        raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
    count, = _TCP_COUNT.unpack_from(buffer)
    size = _TCP_COUNT.size + count * row.size
    if size > len(buffer):
        raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
    listeners = []
    for fields in row.iter_unpack(memoryview(buffer)[_TCP_COUNT.size:size]):
        if family == socket.AF_INET:
            state, address, network_port, pid = fields
            scope = 0
        else:
            address, scope, network_port, state, pid = fields
        if state != 2:  # MIB_TCP_STATE_LISTEN
            raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
        if socket.ntohs(network_port & 0xffff) == port:
            local_address = socket.inet_ntop(family, address)
            if scope:
                local_address += f"%{scope}"
            listeners.append({"LocalAddress": local_address, "LocalPort": port, "OwningProcess": pid})
    return listeners


def _windows_tcp_listeners(port: int) -> list[dict]:
    import ctypes as c
    from ctypes import wintypes as w

    try:
        table = c.WinDLL("iphlpapi", winmode=0x800).GetExtendedTcpTable
        table.argtypes = (c.c_void_p, c.POINTER(w.ULONG), w.BOOL, w.ULONG, c.c_int, w.ULONG)
        table.restype = w.DWORD
        listeners = []
        for family in (socket.AF_INET, socket.AF_INET6):
            size = w.ULONG()
            buffer = None
            for _ in range(3):  # Size discovery and bounded reallocation under TCP churn.
                status = table(buffer, c.byref(size), False, family, 3, 0)  # TCP_TABLE_OWNER_PID_LISTENER
                if status == 0 and buffer is not None and _TCP_COUNT.size <= size.value <= len(buffer):
                    listeners.extend(_tcp_listener_rows(memoryview(buffer)[:size.value], family, port))
                    break
                if status != 122 or not _TCP_COUNT.size <= size.value <= 1024 * 1024:
                    raise AcquisitionBlocked("WINDOWS_PROBE_TCP_FAILED")
                buffer = c.create_string_buffer(size.value)
            else:
                raise AcquisitionBlocked("WINDOWS_PROBE_TCP_FAILED")
        return sorted(listeners, key=lambda row: (row["LocalAddress"], row["OwningProcess"]))
    except (AttributeError, OSError):
        raise AcquisitionBlocked("WINDOWS_PROBE_TCP_FAILED") from None


def _windows_process_owners(processes: list[dict]) -> None:
    """Read local tokens on pinned process handles, without per-process CIM calls."""
    if not processes:
        return
    import ctypes as c
    from ctypes import wintypes as w

    try:
        kernel = c.WinDLL("kernel32", winmode=0x800)
        adv = c.WinDLL("advapi32", winmode=0x800)
        for dll, name, restype, args in (
            (kernel, "OpenProcess", w.HANDLE, (w.DWORD, w.BOOL, w.DWORD)),
            (kernel, "CloseHandle", w.BOOL, (w.HANDLE,)),
            (kernel, "GetProcessTimes", w.BOOL, (w.HANDLE, *([c.POINTER(w.FILETIME)] * 4))),
            (kernel, "WaitForSingleObject", w.DWORD, (w.HANDLE, w.DWORD)),
            (kernel, "LocalFree", c.c_void_p, (c.c_void_p,)),
            (adv, "OpenProcessToken", w.BOOL, (w.HANDLE, w.DWORD, c.POINTER(w.HANDLE))),
            (adv, "GetTokenInformation", w.BOOL, (w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD))),
            (adv, "ConvertSidToStringSidW", w.BOOL, (c.c_void_p, c.POINTER(w.LPWSTR))),
        ):
            function = getattr(dll, name)
            function.restype, function.argtypes = restype, args
        for row in processes:
            process = kernel.OpenProcess(0x101000, False, row["ProcessId"])
            if not process:
                raise AcquisitionBlocked("WINDOWS_PROBE_OWNER_FAILED")
            token = w.HANDLE()
            sid = w.LPWSTR()
            try:
                times = [w.FILETIME() for _ in range(4)]
                if not kernel.GetProcessTimes(process, *(c.byref(item) for item in times)):
                    raise AcquisitionBlocked("WINDOWS_PROBE_OWNER_FAILED")
                ticks = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
                # CIM timestamps have microsecond precision.
                if ticks // 10 != _creation_ticks(row["CreationDate"]) // 10:
                    raise AcquisitionBlocked("CHROME_OBSERVATION_CHANGED")
                user = (c.c_void_p * 16)()  # TOKEN_USER header and maximum SID.
                needed = w.DWORD()
                if (not adv.OpenProcessToken(process, 8, c.byref(token))
                        or not adv.GetTokenInformation(token, 1, user, c.sizeof(user), c.byref(needed))
                        or not adv.ConvertSidToStringSidW(user[0], c.byref(sid)) or not sid.value):
                    raise AcquisitionBlocked("WINDOWS_PROBE_OWNER_FAILED")
                if kernel.WaitForSingleObject(process, 0) != 258:  # WAIT_TIMEOUT: still live.
                    raise AcquisitionBlocked("CHROME_OBSERVATION_CHANGED")
                row["OwnerSid"] = sid.value
            finally:
                if sid:
                    kernel.LocalFree(sid)
                if token:
                    kernel.CloseHandle(token)
                kernel.CloseHandle(process)
    except (AttributeError, OSError):
        raise AcquisitionBlocked("WINDOWS_PROBE_OWNER_FAILED") from None


def _query_windows(port: int, *, owner_action: Callable[[Path], Path] | None = None) -> dict:
    """Bounded, read-only Windows process and TCP inventory with no raw diagnostics."""
    powershell = _powershell(owner_action=owner_action) if owner_action else _powershell()
    listeners = _windows_tcp_listeners(port)
    try:
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                                 _PROBE_SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=8, check=False, env=_child_environment(),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise AcquisitionBlocked("WINDOWS_PROBE_TIMEOUT") from None
    except OSError:
        raise AcquisitionBlocked("WINDOWS_PROBE_UNAVAILABLE") from None
    if result.returncode:
        raise AcquisitionBlocked({20: "WINDOWS_PROBE_CIM_FAILED", 21: "WINDOWS_PROBE_INVALID"}.get(
            result.returncode, "WINDOWS_PROBE_UNAVAILABLE"))
    if len(result.stdout) > 262144:
        raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
    try:
        value = json.loads(result.stdout.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        raise AcquisitionBlocked("WINDOWS_PROBE_INVALID") from None
    if (type(value) is not dict or value.keys() != {"processes"}
            or type(value["processes"]) is not list or len(value["processes"]) > 256):
        raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
    processes = _rows(value["processes"])
    pids = set()
    for row in processes:
        pid = row.get("ProcessId")
        if (type(pid) is not int or not 0 < pid < 2**32 or pid in pids
                or type(row.get("ParentProcessId")) is not int or not 0 <= row["ParentProcessId"] < 2**32
                or type(row.get("SessionId")) is not int or not 0 <= row["SessionId"] < 2**32
                or not isinstance(row.get("ExecutablePath"), str) or not row["ExecutablePath"]
                or not isinstance(row.get("CommandLine"), str) or not 1 <= len(row["CommandLine"]) <= 32768
                or not isinstance(row.get("CreationDate"), str)):
            raise AcquisitionBlocked("PROCESS_IDENTITY_UNKNOWN")
        try:
            if _creation_ticks(row["CreationDate"]) <= 0:
                raise AcquisitionBlocked("PROCESS_IDENTITY_UNKNOWN")
        except _EnrollmentHold:
            raise AcquisitionBlocked("PROCESS_IDENTITY_UNKNOWN") from None
        pids.add(pid)
    _windows_process_owners(processes)
    if _windows_tcp_listeners(port) != listeners:
        raise AcquisitionBlocked("CHROME_OBSERVATION_CHANGED")
    return {"listeners": listeners, "processes": processes}


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


def _interactive_session(*, protocol_only: bool = False) -> int:
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
    if not protocol_only:
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


def _enrollment_operator(*, attended: bool = True,
                         protocol_only: bool = False) -> tuple[Path, int, str]:
    # Nothing at this entrypoint can turn an Issue/runner request into enrollment.
    if os.name != "nt":
        raise _EnrollmentHold("WINDOWS_INTERACTIVE_REQUIRED")
    if any(os.environ.get(key) for key in ("GITHUB_ACTIONS", "GITHUB_EVENT_PATH", "GITHUB_RUN_ID")):
        raise _EnrollmentHold("LOCAL_OPERATOR_ONLY")
    if attended and (sys.stdin is None or not sys.stdin.isatty()):
        raise _EnrollmentHold("WINDOWS_INTERACTIVE_REQUIRED")
    if not attended and protocol_only:
        return _native_operator(protocol_only=True)
    return _native_operator()


def _native_operator(*, protocol_only: bool = False) -> tuple[Path, int, str]:
    """Bind the real process token and session. Callers supply effect authority."""
    session = _interactive_session(protocol_only=True) if protocol_only else _interactive_session()
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
                              marker_proof=None, existing_config: bool = False) -> None:
    from .local_completion_handoff import HandoffBlocked, _private_identity
    root = (local / "AI-Commerce").resolve(strict=True)
    paths = {env.user_data_dir, env.user_data_dir / env.profile_directory}
    if not existing_config:
        _require_operator_control(replace(env, config_path=root))
        paths.update(p for p in env.user_data_dir.parents if _inside(p, root))
        paths.add(root)
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

    def _acl(self, handle, *, exact=False, inherit=False):
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
                if exact and (header[0:2] != bytes((0, 3 if inherit else 0))
                              or int.from_bytes(header[4:8], "little") != 0x1F01FF):
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

    def _relative(self, parent, name, *, directory, create=False, write=False, optional=False,
                  readback=False, repair_acl=False, marker_observation=False):
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
        share = 3 if readback else 1
        if marker_observation:
            # Metadata access can coexist with an exclusive data handle. It
            # does not prevent deletion, so the observer must recheck the name.
            access, share = 0x120080, 3  # READ_CONTROL | SYNCHRONIZE | FILE_READ_ATTRIBUTES
        options = 0x20 | 0x200000 | (1 if directory else 0x40)
        if repair_acl:
            # SetSecurityInfo does not propagate ACEs from MAXIMUM_ALLOWED handles.
            access = 0x02000000
            options &= ~0x20
        previous = self.effect_possible
        if create:
            self.effect_possible = True  # Set BEFORE crossing the mutation boundary.
        status = self.n.NtCreateFile(c.byref(handle), access, c.byref(attrs), c.byref(io), None,
            0x80, share, 2 if create else 1,
            options, None, 0)
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

    def _pin_path(self, path, *, directory=True, private=False, marker_observation=False):
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
            options = {"marker_observation": True} if marker_observation else {}
            handle = self._relative(handle, name, directory=directory if last else True, **options)
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

    def _process(self, *, require_interactive_caller=True):
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
        if require_interactive_caller:
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
        self._pin_path(path, directory=False, private=True)
        return self._marker_holder_matches(path)

    def observe_marker(self, path):
        """Rule out competing data holders for an already identified live listener."""
        failure = "LEGACY_MARKER_PROCESS_UNPROVEN"
        try:
            self.pinned = []
            self.process = self._keep(self.k.OpenProcess(0x101000, False, self.identity[0]))
            if not self.process:
                self._blocked()
            # Protected HostOps can read under the owner token outside the desktop
            # session. The target must still have the exact interactive token.
            self._process(require_interactive_caller=False)
            failure = "LEGACY_MARKER_ACCESS_UNPROVEN"
            handle = self._pin_path(path, directory=False, marker_observation=True)
            identity = self._info(handle, False)[:3]
            failure = "LEGACY_MARKER_ACL_UNPROVEN"
            self._acl(handle)
            failure = "LEGACY_MARKER_HOLDER_UNPROVEN"
            holders = self._marker_process_ids(handle)
            failure = "LEGACY_MARKER_ACCESS_UNPROVEN"
            latest = self._pin_path(path, directory=False, marker_observation=True)
            if self._info(latest, False)[:3] != identity:
                self._blocked()
            failure = "LEGACY_MARKER_ACL_UNPROVEN"
            self._acl(latest)
            failure = "LEGACY_MARKER_PROCESS_UNPROVEN"
            self._process(require_interactive_caller=False)
            # The observer may appear because of its metadata handles. A child
            # PID alone does not establish that the child holds this for Chrome.
            return not holders.difference((os.getpid(), self.identity[0]))
        except (_EnrollmentHold, OSError, AttributeError):
            raise _EnrollmentHold(failure) from None
        finally:
            self.__exit__()

    def _marker_process_ids(self, handle):
        # FileProcessIdsUsingFileInformation queries the exact open file
        # without a registry-backed Restart Manager session or a second path lookup.
        c, w = self.c, self.w

        class HolderList(c.Structure):
            _fields_ = [("count", w.ULONG), ("pids", c.c_size_t * 64)]

        query = self.n.NtQueryInformationFile
        query.restype = c.c_long
        query.argtypes = [w.HANDLE, c.POINTER(self.IoStatus), c.c_void_p, w.ULONG, c.c_int]
        io, rows = self.IoStatus(), HolderList()
        status = query(handle, c.byref(io), c.byref(rows), c.sizeof(rows), 47)
        if (status != 0 or rows.count > len(rows.pids)
                or io.Information != HolderList.pids.offset + rows.count * c.sizeof(c.c_size_t)):
            self._blocked()
        pids = set(rows.pids[:rows.count])
        if len(pids) != rows.count or any(not 0 < pid <= 0xFFFFFFFF for pid in pids):
            self._blocked()
        return pids

    def _marker_holder_matches(self, path):
        c, w = self.c, self.w
        session, key = w.DWORD(), c.create_unicode_buffer(33)
        if self.rm.RmStartSession(c.byref(session), 0, key):
            return False
        try:
            files = (w.LPCWSTR * 1)(str(path))
            if self.rm.RmRegisterResources(session, 1, files, 0, None, 0, None):
                return False
            needed, count, reasons = w.UINT(), w.UINT(2), w.DWORD()
            rows = (self.RmInfo * 2)()
            if self.rm.RmGetList(session, c.byref(needed), c.byref(count), rows, c.byref(reasons)):
                return False
            if count.value != 1:
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


class _WakeHubEnrollmentACL(_NativeEnrollment):
    """Handle-relative custody for the fixed WakeHub enrollment state."""

    def __enter__(self):
        return self

    def _owner(self, handle):
        c = self.c
        owner, sd = c.c_void_p(), c.c_void_p()
        if self.a.GetSecurityInfo(handle, 1, 1, c.byref(owner), None, None, None, c.byref(sd)):
            self._blocked()
        try:
            if self._sid_text(owner) != self.sid:
                raise _EnrollmentHold("WAKEHUB_ENROLLMENT_FOREIGN_OWNER")
        finally:
            self.k.LocalFree(sd)

    def _repair(self, handle, *, directory):
        try:
            self._acl(handle, exact=True, inherit=directory)
            return
        except _EnrollmentHold:
            self._owner(handle)
        c, w = self.c, self.w
        ptr = c.c_void_p
        sd, dacl, present, defaulted = ptr(), ptr(), w.BOOL(), w.BOOL()
        get_dacl = self.a.GetSecurityDescriptorDacl
        get_dacl.argtypes = [ptr, c.POINTER(w.BOOL), c.POINTER(ptr), c.POINTER(w.BOOL)]
        get_dacl.restype = w.BOOL
        set_security = self.a.SetSecurityInfo
        set_security.argtypes = [w.HANDLE, w.DWORD, w.DWORD, ptr, ptr, ptr, ptr]
        set_security.restype = w.DWORD
        inheritance = "OICI" if directory else ""
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;{inheritance};FA;;;{self.sid})", 1, c.byref(sd), None,
        ):
            self._blocked()
        try:
            if (not get_dacl(sd, c.byref(present), c.byref(dacl), c.byref(defaulted))
                    or not present or not dacl
                    or set_security(handle, 1, 0x80000004, None, None, dacl, None)):
                self._blocked()
            self._acl(handle, exact=True, inherit=directory)
        finally:
            self.k.LocalFree(sd)

    def _create(self, parent, name, *, directory):
        c = self.c
        inheritance = "OICI" if directory else ""
        descriptor = c.c_void_p()
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"O:{self.sid}D:P(A;{inheritance};FA;;;{self.sid})", 1, c.byref(descriptor), None):
            self._blocked()
        self.descriptor = descriptor
        try:
            handle = self._relative(parent, name, directory=directory, create=True, write=not directory)
            self._acl(handle, exact=True, inherit=directory)
            return handle
        finally:
            self.k.LocalFree(descriptor)
            self.descriptor = None

    def prepare(self, payload=None):
        self.pinned = []
        root = self._pin_path(self.local / "AI-Commerce")
        parent = self._relative(root, "WakeHub", directory=True, optional=True)
        if parent is None:
            parent = self._create(root, "WakeHub", directory=True)
        directory = self._relative(parent, "Enrollment", directory=True, repair_acl=True, optional=True)
        if directory is None:
            directory = self._create(parent, "Enrollment", directory=True)
        files = [self._relative(directory, name, directory=False, repair_acl=True, optional=True)
                 for name in ("runtime-config.json", "enrollment.sqlite3",
                              "enrollment.sqlite3.lock", "enrollment.sqlite3-journal")]
        handles = [(directory, True), *((handle, False) for handle in files if handle is not None)]
        for handle, _ in handles:
            self._owner(handle)
        for handle, is_directory in handles:
            self._repair(handle, directory=is_directory)
        if payload is None:
            return
        if any(handle is not None for handle in files) or not 1 <= len(payload) <= 8192:
            self._blocked()
        temp_name = f"runtime-config.{token_hex(16)}.part"
        writer = self._create(directory, temp_name, directory=False)
        path = self.local / "AI-Commerce" / "WakeHub" / "Enrollment"
        c, w = self.c, self.w
        written = w.DWORD()
        try:
            try:
                if (not self.k.WriteFile(writer, payload, len(payload), c.byref(written), None)
                        or written.value != len(payload) or not self.k.FlushFileBuffers(writer)):
                    self._blocked()
                writer_id = self._info(writer, False)[:3]
            finally:
                if not self.k.CloseHandle(writer):
                    self._blocked()
                self.handles.pop()
            # The native handles pin the directory and ancestors. On Windows
            # rename fails if the fixed name already exists.
            os.rename(path / temp_name, path / "runtime-config.json")
        finally:
            try:
                (path / temp_name).unlink()
            except FileNotFoundError:
                pass
        reader = self._relative(directory, "runtime-config.json", directory=False)
        self._acl(reader, exact=True)
        info = self._info(reader, False)
        buffer, count = c.create_string_buffer(8193), w.DWORD()
        if (info[:3] != writer_id or info[3:] != (0, len(payload))
                or not self.k.ReadFile(reader, buffer, len(buffer), c.byref(count), None)
                or count.value != len(payload) or buffer.raw[:count.value] != payload):
            self._blocked()


def _prepare_wakehub_enrollment_acl(owner: tuple[Path, int, str], *, payload: bytes | None = None) -> None:
    """Prepare owner-owned enrollment state and optionally create the first config."""
    local, session, sid = owner
    try:
        with _WakeHubEnrollmentACL(None, local, session, sid, None) as custody:
            custody.prepare(payload)
    except (OSError, ValueError) as exc:
        if str(exc) == "WAKEHUB_ENROLLMENT_FOREIGN_OWNER":
            raise
        raise _EnrollmentHold("WAKEHUB_ENROLLMENT_ACL_UNPROVEN") from None


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
    """Validate existing private inputs for the strict D5 lifecycle."""
    root = local / "AI-Commerce"
    from .local_completion_handoff import _private_identity
    for path, check in ((root, _SupervisorConfigCheck.CONFIG_ROOT_CUSTODY),
                        (config.parent, _SupervisorConfigCheck.CONFIG_PARENT_CUSTODY),
                        (config, _SupervisorConfigCheck.CONFIG_FILE_CUSTODY)):
        with _supervisor_config_check(check):
            if not _no_links(path) or _private_identity(path) != sid:
                raise _EnrollmentHold("CONFIG_UNTRUSTED")
    env = load_environment(config, checkout, local_app_data=local)
    with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_SCHEMA):
        if env.port != 9444 or env.endpoint != "http://127.0.0.1:9444":
            raise _EnrollmentHold("CONFIG_UNTRUSTED")
    return env


def _read_pinned_legacy_json(config: Path, local: Path, checkout: Path, sid: str, *,
                             require_private_parent: bool = False) -> object:
    """Read a bounded JSON file under pinned, non-reparse ancestors.

    Always prove exact file custody. Operational adoption also requires a
    private immediate parent, including when the config is directly in the root.
    """
    from . import _private_state
    from .local_completion_handoff import _private_identity

    real_os = _private_state.os
    with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_PATH):
        if (not all(p.is_absolute() for p in (config, local, checkout))
                or ".." in config.parts or config.drive.startswith("\\\\")
                or ":" in config.name or config.name.endswith((".", " "))
                or not config.is_file() or not checkout.is_dir() or not local.is_dir()
                or not _no_links(config) or not _no_links(checkout) or not _no_links(local)):
            raise AcquisitionBlocked("CONFIG_PATH_INVALID")
        try:
            info = config.lstat()
            if not _private_state._regular(info):
                raise AcquisitionBlocked("CONFIG_PATH_INVALID")
            resolved_checkout = checkout.resolve(strict=True)
            resolved_config = config.resolve(strict=True)
            owned_config_root = (local.resolve(strict=True) / "AI-Commerce").resolve()
        except AcquisitionBlocked:
            raise
        except OSError:
            raise AcquisitionBlocked("CONFIG_PATH_INVALID") from None
        if _inside(resolved_config, resolved_checkout):
            raise AcquisitionBlocked("CONFIG_IN_REPOSITORY")
        if not _inside(resolved_config, owned_config_root):
            raise AcquisitionBlocked("CONFIG_NOT_OPERATOR_OWNED")

    def check_custody(fd=None):
        if require_private_parent:
            with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_PARENT_CUSTODY):
                if not _no_links(config.parent) or _private_identity(config.parent) != sid:
                    raise _EnrollmentHold("CONFIG_UNTRUSTED")
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_FILE_CUSTODY):
            if (not _no_links(config) or _private_identity(config) != sid
                    or (fd is not None and real_os.name != "nt"
                        and not real_os.path.samestat(real_os.fstat(fd), config.lstat()))):
                raise _EnrollmentHold("CONFIG_UNTRUSTED")

    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise AcquisitionBlocked("CONFIG_DUPLICATE_KEY")
            obj[key] = value
        return obj

    handles = []
    try:
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_ANCESTOR_HANDLE):
            try:
                if real_os.name == "nt":
                    for part in reversed(config.parents):
                        handles.append(_private_state._windows_handle(part, 0, directory=True))
                    directory_fd = None
                else:
                    directory_fd = real_os.open(
                        config.anchor, real_os.O_RDONLY | real_os.O_DIRECTORY | real_os.O_NOFOLLOW)
                    handles.append(directory_fd)
                    for part in config.parent.parts[1:]:
                        directory_fd = real_os.open(
                            part, real_os.O_RDONLY | real_os.O_DIRECTORY | real_os.O_NOFOLLOW,
                            dir_fd=directory_fd)
                        handles.append(directory_fd)
            except (OSError, _private_state.PrivateStateError):
                if require_private_parent:
                    raise _EnrollmentHold("CHROME_CONFIG_ANCESTOR_PIN_UNPROVEN") from None
                raise
        check_custody()
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_FILE_READ):
            try:
                txn = _private_state._Transaction(SimpleNamespace(path=config), directory_fd)
                fd = txn.open(config.name, real_os.O_RDONLY)
            except (OSError, _private_state.PrivateStateError):
                raise AcquisitionBlocked("CONFIG_INVALID") from None
        with real_os.fdopen(fd, "rb") as stream:
            with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_FILE_READ):
                try:
                    raw = stream.read(8193)
                except OSError:
                    raise AcquisitionBlocked("CONFIG_INVALID") from None
                if len(raw) > 8192:
                    raise AcquisitionBlocked("CONFIG_INVALID")
            with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_JSON):
                try:
                    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
                except AcquisitionBlocked:
                    raise
                except (ValueError, UnicodeError, RecursionError):
                    raise AcquisitionBlocked("CONFIG_INVALID") from None
            check_custody(fd)
            return value
    finally:
        for handle in reversed(handles):
            if real_os.name == "nt":
                handle[1](handle[0])
            else:
                real_os.close(handle)


def _read_supervised_9444_config(config: Path, local: Path, checkout: Path,
                                 sid: str) -> tuple[ChromeEnvironment, dict]:
    """Read the existing Chrome config for supervised local registration and delivery."""
    value = _read_pinned_legacy_json(config, local, checkout, sid, require_private_parent=True)
    env = _environment_from_value(value, config, checkout, local_app_data=local)
    with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_SCHEMA):
        if env.port != 9444 or env.endpoint != "http://127.0.0.1:9444":
            raise _EnrollmentHold("CONFIG_UNTRUSTED")
    return env, value


def _validate_legacy_health_9444_value(value, config: Path, local: Path, checkout: Path,
                                       sid: str) -> ChromeEnvironment:
    """Validate legacy 9444 schema plus strict actual file and profile custody."""
    env = _environment_from_value(value, config, checkout, local_app_data=local)
    with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_SCHEMA):
        if env.port != 9444 or env.endpoint != "http://127.0.0.1:9444":
            raise _EnrollmentHold("CONFIG_UNTRUSTED")
    _require_operator_control(env)
    from .local_completion_handoff import HandoffBlocked, _private_identity
    try:
        with _supervisor_config_check(_SupervisorConfigCheck.CONFIG_FILE_CUSTODY):
            if not _no_links(env.config_path) or _private_identity(env.config_path) != sid:
                raise _EnrollmentHold("CONFIG_UNTRUSTED")
        with _supervisor_config_check(_SupervisorConfigCheck.PROFILE_CUSTODY):
            for path in (env.user_data_dir, env.user_data_dir / env.profile_directory):
                if not _no_links(path) or _private_identity(path) != sid:
                    raise AcquisitionBlocked("OPERATOR_OWNERSHIP_UNPROVEN")
    except (_EnrollmentHold, AcquisitionBlocked):
        raise
    except (HandoffBlocked, OSError, ValueError):
        raise AcquisitionBlocked("OPERATOR_OWNERSHIP_UNPROVEN") from None
    return env


def _read_legacy_health_9444_config(config: Path, local: Path, checkout: Path,
                                    sid: str) -> tuple[ChromeEnvironment, dict]:
    """One bounded pinned read and custody validation for legacy 9444 health."""
    value = _read_pinned_legacy_json(config, local, checkout, sid)
    env = _validate_legacy_health_9444_value(value, config, local, checkout, sid)
    return env, value


def _supervisor_9444_config(local: Path, checkout: Path,
                            sid: str) -> tuple[ChromeEnvironment, dict] | None:
    """Adopt a sole registered legacy 9444 Chrome config for health reads only."""
    with _supervisor_config_check(_SupervisorConfigCheck.LEGACY_REGISTRY_DISCOVERY):
        registry = _config_9444_registry(local, checkout)
    if not registry.present:
        return None
    with _supervisor_config_check(_SupervisorConfigCheck.LEGACY_REGISTRY_AMBIGUOUS):
        if len(registry.present) != 1:
            raise _EnrollmentHold("LOCAL_CONFIG_AMBIGUOUS")
    return _read_legacy_health_9444_config(registry.present[0], local, checkout, sid)


def _supervisor_9444_environment(local: Path, checkout: Path, sid: str) -> ChromeEnvironment | None:
    """Adopt a sole registered Chrome config for health reads without changing it."""
    selected = _supervisor_9444_config(local, checkout, sid)
    return None if selected is None else selected[0]


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


_ADOPTABLE_CHROME_FLAGS = frozenset({
    "--no-first-run", "--no-default-browser-check",
    "--disable-background-mode", "--start-maximized",
})
_CDP_IDENTITY_SWITCHES = frozenset({
    "--user-data-dir", "--profile-directory", "--remote-debugging-address", "--remote-debugging-port",
})


def _observed_chrome_switch(args: list[str], name: str) -> str | None:
    aliases = (name, name[1:], "/" + name[2:])
    matches = [arg for arg in args if arg.partition("=")[0].lower() in aliases]
    if not matches:
        return None
    prefix = name + "="
    if len(matches) != 1 or not matches[0].startswith(prefix) or not matches[0][len(prefix):].strip():
        raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
    return matches[0][len(prefix):]


def _adoptable_chrome_args(argv: list[str]) -> bool:
    # Allow only configured identity switches and specific startup/window flags.
    # Unknown flags can change profile, network, extension, or CDP behavior.
    names = [arg.partition("=")[0] for arg in argv[1:]]
    return (len(set(names)) == len(names)
            and all(name in _CDP_IDENTITY_SWITCHES or arg in _ADOPTABLE_CHROME_FLAGS
                    for name, arg in zip(names, argv[1:])))


def _validate_observed_chrome(env: ChromeEnvironment, proc: dict, argv0: str, *,
                              session: int, sid: str) -> None:
    exe, created = proc.get("ExecutablePath"), proc.get("CreationDate")
    try:
        if (proc.get("OwnerSid") == sid and type(proc.get("SessionId")) is int
                and proc["SessionId"] == session and isinstance(exe, str)
                and Path(exe).is_absolute() and Path(exe).resolve(strict=True) == env.executable
                and isinstance(created, str) and _creation_ticks(created) > 0):
            image = Path(argv0)
            if (image.resolve(strict=True) == env.executable if image.is_absolute()
                    else image == Path(env.executable.name)):
                return
    except (OSError, ValueError, RuntimeError):
        pass
    raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")


def _observe_9444_inventory(env: ChromeEnvironment, inventory: dict, *,
                            session: int, sid: str, for_adoption: bool = True) -> tuple[str, tuple]:
    """Observe the configured 9444 browser and competing claims in one inventory.

    Only the target contributes to the owned identity. ABSENT here is a health
    observation and callers still need the strict lifecycle proof for a start.
    """
    if (type(inventory) is not dict or set(inventory) != {"listeners", "processes"}
            or any(type(inventory[k]) not in (list, dict) for k in inventory)):
        raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
    listeners = _rows(inventory["listeners"])
    processes = _rows(inventory["processes"])
    pids = [p.get("ProcessId") for p in processes]
    if (len(listeners) > 1 or len(processes) > 256
            or any(type(pid) is not int or pid <= 0 for pid in pids)
            or len(set(pids)) != len(pids)):
        raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
    listener_pid = None
    if listeners:
        row = listeners[0]
        if type(row.get("LocalPort")) is not int or row["LocalPort"] != env.port:
            raise AcquisitionBlocked("WINDOWS_PROBE_INVALID")
        listener_pid = row.get("OwningProcess")
        if row.get("LocalAddress") != "127.0.0.1" or type(listener_pid) is not int:
            raise AcquisitionBlocked("CDP_NOT_LOOPBACK_OWNED")
        if listener_pid not in pids:
            raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
    owned = None
    children = {}
    inherited = []
    profile_active = port_claimed = False
    for proc in processes:
        command = proc.get("CommandLine")
        if not isinstance(command, str) or not 1 <= len(command) <= 32768:
            raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        try:
            argv = _commandline_argv(command)
        except (ValueError, OSError):
            raise AcquisitionBlocked("PROCESS_IDENTITY_UNKNOWN") from None
        if (type(argv) is not list or not 1 <= len(argv) <= 256
                or any(not isinstance(a, str) or not a for a in argv)):
            raise AcquisitionBlocked("PROCESS_IDENTITY_UNKNOWN")
        args = argv[1:]
        if "--" in args:
            # Chromium treats everything after the separator as positional.
            args = args[:args.index("--")]
        child = _observed_chrome_switch(args, "--type") is not None
        directory = _observed_chrome_switch(args, "--user-data-dir")
        port = _observed_chrome_switch(args, "--remote-debugging-port")
        claims_port = False
        if port is not None:
            if re.fullmatch(r"[0-9]{1,5}", port) is None:
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            port_number = int(port)
            if port_number > 65535 or (port_number == 0 and not child):
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            claims_port = port_number == env.port
        claims_profile = False
        if directory is not None:
            path = Path(directory)
            if not path.is_absolute() or path.anchor.startswith("\\\\"):
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            try:
                claims_profile = path.resolve(strict=True) == env.user_data_dir
            except (OSError, ValueError, RuntimeError):
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN") from None
        if listener_pid is None:
            profile_active |= claims_profile
            port_claimed |= claims_port
            continue
        if child:
            children[proc["ProcessId"]] = (proc, argv[0], args, directory, claims_profile, port, claims_port)
        if proc["ProcessId"] == listener_pid:
            _observed_chrome_switch(args, "--profile-directory")
            _observed_chrome_switch(args, "--remote-debugging-address")
            if (child or not claims_profile or not claims_port
                    or (for_adoption and not _adoptable_chrome_args(argv))
                    or not Path(argv[0]).is_absolute()):
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            _validate_observed_chrome(env, proc, argv[0], session=session, sid=sid)
            if not _identity_matches(env, proc["ExecutablePath"], [argv[0], *args]):
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            owned = proc
        elif (claims_port and not (child and claims_profile)) or (claims_profile and not child):
            raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        elif claims_profile:
            profile = _observed_chrome_switch(args, "--profile-directory")
            address = _observed_chrome_switch(args, "--remote-debugging-address")
            if (profile not in (None, env.profile_directory) or address not in (None, "127.0.0.1")
                    or (port is not None and not claims_port)):
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            if claims_port:
                inherited.append(proc["ProcessId"])
            else:
                _validate_observed_chrome(env, proc, argv[0], session=session, sid=sid)
    if listener_pid is None:
        if profile_active:
            raise AcquisitionBlocked("PROFILE_ALREADY_ACTIVE_NO_CDP")
        if port_claimed:
            raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        return "ABSENT", ((), ())
    if owned is None:
        raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
    # CIM parent IDs can outlive a process. Require a same-owner Chrome chain
    # with strictly increasing creation times, ending at this listener.
    linked = {listener_pid: _creation_ticks(owned["CreationDate"])}
    for pid in inherited:
        chain = {}
        while pid not in linked:
            if pid in chain or pid not in children:
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            proc, image, args, directory, same_profile, port, same_port = children[pid]
            _validate_observed_chrome(env, proc, image, session=session, sid=sid)
            if ((directory is not None and not same_profile) or (port is not None and not same_port)
                    or _observed_chrome_switch(args, "--profile-directory") not in (None, env.profile_directory)
                    or _observed_chrome_switch(args, "--remote-debugging-address") not in (None, "127.0.0.1")):
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            chain[pid] = _creation_ticks(proc["CreationDate"])
            pid = proc.get("ParentProcessId")
            if type(pid) is not int or pid <= 0:
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        parent_created = linked[pid]
        for child_pid, child_created in reversed(chain.items()):
            if child_created <= parent_created:
                raise AcquisitionBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            linked[child_pid] = parent_created = child_created
    listener_tuple = (("127.0.0.1", env.port, listener_pid),)
    proc_tuple = (tuple(owned.get(k) for k in (
        "ProcessId", "ExecutablePath", "CommandLine", "SessionId", "OwnerSid", "CreationDate")),)
    return "OWNED", (listener_tuple, proc_tuple)


def _legacy_health_inventory(env: ChromeEnvironment, inventory: dict, *,
                             session: int, sid: str) -> tuple[str, tuple]:
    """Observe health without granting operational use of the startup command."""
    return _observe_9444_inventory(env, inventory, session=session, sid=sid, for_adoption=False)


def _observe_legacy_profile_markers(env, local, session, sid, state, identity):
    for directory, hold in (
        (env.user_data_dir, "ENROLLMENT_PROFILE_LOCK_UNPROVEN"),
        (env.user_data_dir / env.profile_directory, "PROFILE_LOCK_HOLD"),
    ):
        for name in _MARKERS:
            marker = directory / name
            try:
                marker.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise _EnrollmentHold("LEGACY_MARKER_ACCESS_UNPROVEN" if state == "OWNED" else hold) from None
            if state == "OWNED":
                try:
                    native = _NativeEnrollment(env, local, session, sid, identity[1][0])
                    if native.observe_marker(marker) is True:
                        continue
                except (_EnrollmentHold, OSError) as exc:
                    if isinstance(exc, _EnrollmentHold) and str(exc) in _LEGACY_MARKER_HOLDS:
                        raise
                    raise _EnrollmentHold("LEGACY_MARKER_ACCESS_UNPROVEN") from None
            raise _EnrollmentHold(hold)


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
_CLEAN_TASK_PENDING = "AutostartPending"
_PROGRAM_FILES_FOLDERS = ("905E63B6-C1BF-494E-B29C-65B732D3D21A",
                          "7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E")
_CLEAN_9444_CODES = {
    "LOCAL_OPERATOR_ONLY", "WINDOWS_INTERACTIVE_REQUIRED", "WINDOWS_TOKEN_UNAVAILABLE",
    "ELEVATED_CHROME_DISALLOWED", "ENROLLMENT_IDENTITY_UNPROVEN", "OPT_IN_REQUIRED",
    "WINDOWS_OWNER_CONTEXT_UNVERIFIED", "PROFILE_UNAVAILABLE",
    "PROFILE_LOCKED", "PROFILE_IN_USE", "PROFILE_ALREADY_ACTIVE_NO_CDP",
    "CHROME_PATH_INVALID", "CHROME_EXECUTABLE_UNTRUSTED", "SUPERVISOR_ACTIVE",
    "WINDOWS_PROBE_INVALID", "WINDOWS_PROBE_UNAVAILABLE", "FOREIGN_PORT_OWNER",
    "BROWSER_OWNERSHIP_UNPROVEN", "CDP_PROTOCOL_UNAVAILABLE", "UNKNOWN_EFFECT",
    "AUTOSTART_CONFLICT", "AUTOSTART_NOT_ENROLLED", "AUTOSTART_RUNTIME_UNAVAILABLE",
}


def _clean_hold(code: str) -> dict:
    return {"status": "HOLD", "code": code if code in _CLEAN_9444_CODES else
            "WINDOWS_PROBE_UNAVAILABLE"}


def _clean_operator() -> tuple[Path, int, str]:
    if os.environ.get("CI") or any(key.startswith("AI_COMMERCE_GIT_GUARD_") for key in os.environ):
        raise _Clean9444Hold("LOCAL_OPERATOR_ONLY")
    return _enrollment_operator()


def _clean_target(owner: tuple[Path, int, str]) -> tuple[_ChromeTarget, tuple[Path, ...]]:
    local = owner[0]
    directory = local / "AI-Commerce" / _CLEAN_9444_PROFILE
    profile = directory / "Default"
    if not local.is_absolute():
        raise _Clean9444Hold("PROFILE_UNAVAILABLE")
    try:
        # Check parents first so a junction is rejected before probing its children.
        for path in (*reversed(profile.parents), profile):
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise _Clean9444Hold("PROFILE_UNAVAILABLE")
        for path in (directory, profile):
            with os.scandir(path) as entries:
                next(entries, None)
    except OSError:
        raise _Clean9444Hold("PROFILE_UNAVAILABLE") from None

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
    target = _ChromeTarget(candidates[0], directory, "Default",
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

function Assert-PlainPath($path, $directory = $false) {
    $parts = [Collections.Generic.Stack[string]]::new()
    for ($partPath = $path; $partPath; $partPath = [IO.Path]::GetDirectoryName($partPath)) {
        $parts.Push($partPath)
    }
    foreach ($partPath in $parts) {
        $part = Get-Item -LiteralPath $partPath -Force
        if (!$part -or ($part.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            (($directory -or $partPath -ine $path) -and $part -isnot [IO.DirectoryInfo])) {
            throw 'PROFILE_UNAVAILABLE'
        }
    }
}

function Assert-CleanProfile {
    try {
        $local = [Environment]::GetFolderPath('LocalApplicationData')
        if (!$local -or $profileDir -ine (Join-Path $local 'AI-Commerce\WakeHub-9444-Clean')) {
            throw 'PROFILE_UNAVAILABLE'
        }
        $defaultDir = Join-Path $profileDir 'Default'
        Assert-PlainPath $defaultDir $true
        foreach ($folder in @($profileDir, $defaultDir)) {
            $null = Get-ChildItem -LiteralPath $folder -Force | Select-Object -First 1
        }
    } catch { throw 'PROFILE_UNAVAILABLE' }
}

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
    Assert-CleanProfile
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
    Assert-CleanProfile
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


def _historical_legacy_workdir() -> Path | None:
    """Independently verify the native WindowsPowerShell v1.0 directory if safe."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            access=winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, kind = winreg.QueryValueEx(key, "SystemRoot")
        if kind != winreg.REG_SZ or not isinstance(value, str):
            return None
        root = Path(value)
        if not root.is_absolute() or not re.fullmatch(r"[A-Za-z]:", root.drive):
            return None
        workdir = root / "System32" / "WindowsPowerShell" / "v1.0"
        if not _no_links(workdir) or not workdir.is_dir() or workdir.resolve(strict=True) != workdir:
            return None
        _verify_powershell_custody(workdir)
        return workdir
    except (AttributeError, ImportError, OSError, ValueError, AcquisitionBlocked):
        return None


def ensure_clean_9444(*, opt_in: bool = False) -> dict:
    """Acquire Chrome once for an explicitly opted-in interactive owner."""
    if opt_in is not True:
        return _clean_hold("OPT_IN_REQUIRED")
    try:
        owner = _clean_operator()
        target, candidates = _clean_target(owner)
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


def _clean_demand_action(powershell: Path, launcher: str) -> tuple[Path, str]:
    # A GUI-subsystem entry point prevents a console before PowerShell starts.
    gui = Path(sys._base_executable).with_name("pythonw.exe")
    if not gui.is_absolute() or re.search(r"[%\r\n\x00]", str(gui)):
        raise _Clean9444Hold("AUTOSTART_RUNTIME_UNAVAILABLE")
    command = [str(powershell), *_CLEAN_POWERSHELL_FLAGS, launcher]
    script = (
        "import os, subprocess\n"
        f"env = {{k: v for k, v in os.environ.items() if k.upper() in {sorted(_CHILD_ENV_KEYS)!r}}}\n"
        f"subprocess.run({command!r}, env=env, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW,"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    )
    return gui, subprocess.list2cmdline(["-I", "-B", "-S", "-c", script])


_CLEAN_9444_TASK_INSPECTION = _POWERSHELL_TASK_READ_PREAMBLE + r"""
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$session = [Diagnostics.Process]::GetCurrentProcess().SessionId
if ($sid -cne $env:AI_COMMERCE_TASK_SID -or $session -le 0 -or
    $session -ne [int]$env:AI_COMMERCE_TASK_SESSION) { 'WINDOWS_OWNER_CONTEXT_UNVERIFIED'; exit }
$sddl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')'
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
$expected.Actions.Context = $expected.Principal.Id
$action = $expected.Actions.Create(0) # TASK_ACTION_EXEC
$action.Path = $env:AI_COMMERCE_TASK_GUI
$action.Arguments = $env:AI_COMMERCE_TASK_ARGUMENTS
$action.WorkingDirectory = [IO.Path]::GetDirectoryName($action.Path)
$trustedPowerShellSpecs = if ($env:AI_COMMERCE_TASK_TRUSTED_POWERSHELLS) {
    @($env:AI_COMMERCE_TASK_TRUSTED_POWERSHELLS | ConvertFrom-Json)
} else { @() }
$trustedPowerShellPaths = @($trustedPowerShellSpecs | ForEach-Object { [string]$_.path })
$trustedPowerShellDirs = @($trustedPowerShellSpecs | ForEach-Object { [string]$_.dir })
$installOwnerSids = @(
    'S-1-5-18', 'S-1-5-32-544',
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
)

function Read-Registration($taskName = $name) {
    try { $folder.GetTask($taskName) }
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

function Test-RegistrationBase($registered) {
    if (!$registered -or !$registered.Enabled) { return $false }
    $acl = [Security.AccessControl.RawSecurityDescriptor]::new($registered.GetSecurityDescriptor(7))
    $want = [Security.AccessControl.RawSecurityDescriptor]::new($sddl)
    if (!$acl.Owner -or $acl.Owner.Value -cne $sid -or
        $acl.GetSddlForm('Access') -cne $want.GetSddlForm('Access')) { return $false }
    $definition = $registered.Definition
    $principal = $definition.Principal
    if ($principal.Id -cne $expected.Principal.Id -or !(Test-Owner $principal.UserId) -or
        $principal.LogonType -ne 3 -or
        $principal.RunLevel -ne 0 -or $principal.GroupId -or $definition.Actions.Count -ne 1 -or
        $definition.Actions.Item(1).Type -ne 0 -or
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

function Test-SignedExecutable($path, $publisher) {
    try {
        if (!$path -or $path -notmatch '^[A-Za-z]:[\\/]') { return $false }
        $full = [IO.Path]::GetFullPath($path)
        if ($full -ine $path) { return $false }
        for ($part = $full; $part; $part = [IO.Path]::GetDirectoryName($part)) {
            $entry = Get-Item -LiteralPath $part -Force
            if (!$entry -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
            if ($part -ieq $full) {
                if ($entry -isnot [IO.FileInfo]) { return $false }
            } elseif ($entry -isnot [IO.DirectoryInfo]) {
                return $false
            }
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $full
        return $signature.Status -eq 'Valid' -and $null -ne $signature.SignerCertificate -and
            $signature.SignerCertificate.Subject -match $publisher
    } catch { return $false }
}

function Get-AttestedPathCustodySddl($path, $allowedOwners, $expectFile = $false) {
    try {
        if (!$path -or $path -isnot [string] -or $path -cne $path.Trim() -or
            $path -notmatch '^[A-Za-z]:\\' -or $path -match '[%"\r\n\t\x00]' -or
            $path -match '(^|[\\/])\.\.([\\/]|$)') { return $null }
        $full = [IO.Path]::GetFullPath($path)
        $driveRoot = [IO.Path]::GetPathRoot($full)
        if (!$driveRoot -or $driveRoot.Length -ne 3) { return $null }
        if ($expectFile) {
            if ($full -ine $path) { return $null }
        } else {
            $trimmedFull = if ($full.Length -gt 3) { $full.TrimEnd([char]92) } else { $full }
            $trimmedPath = if ($path.Length -gt 3) { $path.TrimEnd([char]92) } else { $path }
            if ($trimmedFull -ine $trimmedPath) { return $null }
            $full = $trimmedFull
        }
        if ([IO.DriveInfo]::new($driveRoot.Substring(0, 1)).DriveType -ne [IO.DriveType]::Fixed) {
            return $null
        }
        $parts = [Collections.Generic.Stack[string]]::new()
        for ($cur = $full; $cur; $cur = [IO.Path]::GetDirectoryName($cur)) { $parts.Push($cur) }
        $sections = [Security.AccessControl.AccessControlSections]::Owner -bor
            [Security.AccessControl.AccessControlSections]::Access
        $aceOwners = @($allowedOwners) + @('S-1-3-4')
        $leafSddl = $null
        foreach ($part in $parts) {
            $entry = Get-Item -LiteralPath $part -Force -ErrorAction Stop
            if (!$entry -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $null }
            $isLeaf = $part -ieq $full
            if ($isLeaf -and $expectFile) {
                if ($entry -isnot [IO.FileInfo]) { return $null }
                $aclObj = [IO.FileSystemAclExtensions]::GetAccessControl([IO.FileInfo]$entry, $sections)
            } else {
                if ($entry -isnot [IO.DirectoryInfo]) { return $null }
                $aclObj = [IO.FileSystemAclExtensions]::GetAccessControl([IO.DirectoryInfo]$entry, $sections)
            }
            $partSddl = $aclObj.GetSecurityDescriptorSddlForm('Owner, Access')
            $sd = [Security.AccessControl.RawSecurityDescriptor]::new($partSddl)
            if (!$sd.Owner -or $sd.Owner.Value -cnotin $allowedOwners -or !$sd.DiscretionaryAcl) {
                return $null
            }
            $forbidden = if ($part -ieq $driveRoot) { 0x500D0040 } else { 0x500D0156 }
            foreach ($ace in $sd.DiscretionaryAcl) {
                if ($ace -isnot [Security.AccessControl.CommonAce]) { return $null }
                if ($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly) { continue }
                if ($ace.AceType -eq [Security.AccessControl.AceType]::AccessDenied) { continue }
                if ($ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed) { return $null }
                if (($ace.AccessMask -band $forbidden) -and $ace.SecurityIdentifier.Value -cnotin $aceOwners) {
                    return $null
                }
            }
            if ($isLeaf) { $leafSddl = $partSddl }
        }
        return $leafSddl
    } catch { return $null }
}

function Test-PathCustody($path, $allowedOwners, $expectFile = $false) {
    return $null -ne (Get-AttestedPathCustodySddl $path $allowedOwners $expectFile)
}

function Test-HistoricalLegacyWorkDir($dir) {
    try {
        if (!$dir -or !$env:AI_COMMERCE_TASK_HISTORICAL_WORKDIR -or
            $dir -cne $dir.Trim() -or $dir -match '[%"\r\n\t\x00;\|&<>`]' -or
            $dir -notmatch '^[A-Za-z]:\\' -or $dir -match '(^|[\\/])\.\.([\\/]|$)') {
            return $false
        }
        $sysDir = [Environment]::SystemDirectory
        if (!$sysDir) { return $false }
        $expectedHist = [IO.Path]::GetFullPath((Join-Path $sysDir 'WindowsPowerShell\v1.0')).TrimEnd([char]92)
        $envHist = [IO.Path]::GetFullPath($env:AI_COMMERCE_TASK_HISTORICAL_WORKDIR).TrimEnd([char]92)
        if ($envHist -ine $expectedHist) { return $false }
        $rawTrimmed = $dir.TrimEnd([char]92)
        if ($rawTrimmed -ine $expectedHist -or
            [IO.Path]::GetFullPath($dir).TrimEnd([char]92) -ine $expectedHist) {
            return $false
        }
        return Test-PathCustody $expectedHist $installOwnerSids $false
    } catch { return $false }
}

function Test-DemandRuntime {
    Test-SignedExecutable $action.Path '(^|, )CN=Python Software Foundation(,|$)'
}

function Test-TrustedPowerShell($path) {
    if (!$path -or $path -notin $trustedPowerShellPaths) { return $false }
    if (!(Test-SignedExecutable $path '(^|, )CN=Microsoft Corporation(,|$)')) { return $false }
    try {
        $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($path)
        return $version.FileMajorPart -ge 7 -and
            $version.ProductName -cin @('PowerShell', 'PowerShell Core') -and
            $version.OriginalFilename -iin @('pwsh.exe', 'pwsh.dll')
    } catch { return $false }
}

function Test-Registration($registered) {
    if (!(Test-RegistrationBase $registered) -or $registered.Definition.Triggers.Count -ne 0) {
        return $false
    }
    $a = $registered.Definition.Actions.Item(1)
    return $a.Path -ieq $action.Path -and $a.Arguments -ceq $action.Arguments -and
        $a.WorkingDirectory -ieq $action.WorkingDirectory
}

function Test-LegacyRegistration($registered) {
    if (!(Test-RegistrationBase $registered) -or $registered.Definition.Triggers.Count -ne 1) {
        return $false
    }
    $t = $registered.Definition.Triggers.Item(1)
    if ($t.Type -ne 9 -or !$t.Enabled -or !(Test-Owner $t.UserId) -or $t.Delay -or
        $t.StartBoundary -or $t.EndBoundary -or $t.Repetition.Duration -or
        $t.Repetition.StopAtDurationEnd -or [string]$t.ExecutionTimeLimit -cnotin @('', 'PT72H')) {
        return $false
    }
    # Classify the installed legacy definition without constructing a trigger.
    try {
        if ([Xml.XmlConvert]::ToTimeSpan($t.Repetition.Interval) -ne [TimeSpan]::FromMinutes(1)) {
            return $false
        }
    } catch { return $false }
    $a = $registered.Definition.Actions.Item(1)
    if (!$env:AI_COMMERCE_TASK_LEGACY_ARGUMENTS -or
        $a.Arguments -cne $env:AI_COMMERCE_TASK_LEGACY_ARGUMENTS) { return $false }
    if (!$a.Path -or !$a.WorkingDirectory) { return $false }
    $validWorkDir = ($a.WorkingDirectory -in $trustedPowerShellDirs) -or
        (Test-HistoricalLegacyWorkDir $a.WorkingDirectory)
    if (!$validWorkDir) { return $false }
    return Test-TrustedPowerShell $a.Path
}

function Test-Ready($registered) {
    # TASK_STATE_READY, with no queued/running instance.
    return $registered.State -eq 3 -and $registered.GetInstances(0).Count -eq 0
}

function Get-MigrationState($key) {
    if (!$key) { return 'FRESH' }
    $names = $key.GetValueNames()
    if ('DemandMigrationRollback' -in $names) {
        if ('DemandMigrationComplete' -in $names -and
            $key.GetValue('DemandMigrationComplete') -eq 1 -and
            $key.GetValue('DemandMigrationRollback')) { return 'COMPLETE' }
        return 'UNRESOLVED'
    }
    if ('DemandMigrationComplete' -in $names) { return 'UNRESOLVED' }
    return 'FRESH'
}

function Open-MigrationState($write) {
    [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($env:AI_COMMERCE_TASK_REGISTRY, $write)
}

$registered = Read-Registration
"""


_CLEAN_9444_TASK_IMAGE_SCRIPT = _CLEAN_9444_TASK_INSPECTION + r"""
if (!$registered -or !$trustedPowerShellSpecs.Count) { exit 2 }
if ($registered.Definition.Triggers.Count -eq 0) {
    if (!(Test-RegistrationBase $registered) -or !(Test-DemandRuntime)) { exit 2 }
    $a = $registered.Definition.Actions.Item(1)
    if ($a.Path -ine $action.Path -or $a.WorkingDirectory -ine $action.WorkingDirectory -or !$a.Arguments) {
        exit 2
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $argHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$a.Arguments))).Replace('-', '')
    } finally { $sha.Dispose() }
    $matched = @($trustedPowerShellSpecs | Where-Object { [string]$_.demand_sha256 -ieq $argHash })
    if ($matched.Count -ne 1 -or !(Test-TrustedPowerShell ([string]$matched[0].path))) { exit 2 }
    [string]$matched[0].path
    exit
}
if (Test-LegacyRegistration $registered) {
    $image = $registered.Definition.Actions.Item(1).Path
    $matched = @($trustedPowerShellSpecs | Where-Object { [string]$_.path -ieq $image })
    if ($matched.Count -ne 1) { exit 2 }
    [string]$matched[0].path
    exit
}
exit 2
"""


_CLEAN_9444_TASK_READ_SCRIPT = _CLEAN_9444_TASK_INSPECTION + r"""
$key = Open-MigrationState $false
try {
    if ((Get-MigrationState $key) -eq 'UNRESOLVED') { 'UNKNOWN_EFFECT'; exit }
} finally {
    if ($key) { $key.Dispose() }
}
if (!$registered) { 'AUTOSTART_NOT_ENROLLED' }
elseif (!(Test-Registration $registered)) { 'AUTOSTART_CONFLICT' }
elseif (!(Test-DemandRuntime)) { 'AUTOSTART_RUNTIME_UNAVAILABLE' }
else { 'ALREADY_ENROLLED' }
"""


_CLEAN_9444_TASK_SCRIPT = _CLEAN_9444_TASK_INSPECTION + r"""
if (!(Test-DemandRuntime)) { 'AUTOSTART_RUNTIME_UNAVAILABLE'; exit }
if ($env:AI_COMMERCE_TASK_START_SESSION) {
    $session = [int]$env:AI_COMMERCE_TASK_START_SESSION
    if ($session -le 0 -or $session -ne [Diagnostics.Process]::GetCurrentProcess().SessionId -or
        !(Test-Registration $registered)) { 'AUTOSTART_CONFLICT'; exit }
    if (!(Test-Ready $registered)) { 'START_NOT_ATTEMPTED'; exit }
    $key = Open-MigrationState $false
    try {
        if ((Get-MigrationState $key) -eq 'UNRESOLVED') { 'START_NOT_ATTEMPTED'; exit }
    } finally {
        if ($key) { $key.Dispose() }
    }
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


_CLEAN_9444_D5_ATTESTATION = r"""
$chromeExecutables = if ($env:AI_COMMERCE_TASK_CHROME_EXECUTABLES) {
    @($env:AI_COMMERCE_TASK_CHROME_EXECUTABLES | ConvertFrom-Json)
} else { @() }

function Read-TaskSwitch($arguments, $flag) {
    $m = @($arguments | Where-Object { ($_ -split '=', 2)[0] -ieq $flag })
    if (!$m.Count) { return $null }
    if ($m.Count -ne 1 -or !$m[0].StartsWith($flag + '=') -or $m[0].Length -eq ($flag.Length + 1)) {
        throw 'TASK_MIGRATION_AMBIGUOUS'
    }
    $m[0].Substring($flag.Length + 1)
}

function Split-TaskArguments($line) {
    if (!$line -or $line -isnot [string] -or $line -match '[\r\n\x00%]' -or
        $line -match '\\"|""' -or $line -notmatch '^\s*(?:(?>(?:[^\s"]+|"[^"]*")+)\s*)+$') { return $null }
    $parts = @([regex]::Matches($line, '(?:[^\s"]+|"[^"]*")+') | ForEach-Object { $_.Value.Replace([string][char]34, '') })
    if (!$parts.Count) { return $null }
    return ,$parts
}

function Test-PlainPathOrAncestors($path) {
    try {
        $full = [IO.Path]::GetFullPath($path)
        $parts = [Collections.Generic.Stack[string]]::new()
        for ($cur = $full; $cur; $cur = [IO.Path]::GetDirectoryName($cur)) { $parts.Push($cur) }
        $seenMissing = $false
        foreach ($part in $parts) {
            $entry = $null
            try { $entry = Get-Item -LiteralPath $part -Force -ErrorAction Stop }
            catch [Management.Automation.ItemNotFoundException] { $seenMissing = $true; continue }
            if ($seenMissing -or !$entry -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                $entry -isnot [IO.DirectoryInfo]) { return $false }
        }
        return $true
    } catch { return $false }
}

function Test-D5Contamination($text) {
    if (!$text) { return $false }
    return $text -match '(?i)WakeHub-9444-Clean' -or $text -match '(?<![0-9])9444(?![0-9])' -or
        $text.IndexOf($name, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-AstKind($node) {
    if ($null -eq $node -or $node.GetType().Namespace -cne 'System.Management.Automation.Language') { return '' }
    return $node.GetType().Name
}

function Test-AttestedChromeLaunch($chromeExe, $chromeArgs, $aiRoot, $cleanProfile) {
    if (!$chromeExe -or $chromeExe -notin $chromeExecutables -or
        !(Test-SignedExecutable $chromeExe '(^|, )CN=Google (LLC|Inc)(,|$)')) { return $false }
    if (!$chromeArgs -or !$chromeArgs.Count) { return $false }
    foreach ($arg in $chromeArgs) {
        if ($arg -isnot [string] -or !$arg -or $arg -match '(?i)WakeHub-9444-Clean' -or
            $arg -match '(?:[:=]|^)9444(?:\D|$)' -or
            $arg.IndexOf($name, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $false }
    }
    $d5Port = Read-TaskSwitch $chromeArgs '--remote-debugging-port'
    $d5Addr = Read-TaskSwitch $chromeArgs '--remote-debugging-address'
    $d5Dir = Read-TaskSwitch $chromeArgs '--user-data-dir'
    $d5Prof = Read-TaskSwitch $chromeArgs '--profile-directory'
    $d5Type = Read-TaskSwitch $chromeArgs '--type'
    if ($d5Port -cne '9333' -or $d5Addr -cnotin @($null, '127.0.0.1') -or $null -ne $d5Type -or
        !$d5Dir -or $d5Dir -notmatch '^[A-Za-z]:[\\/]' -or $d5Dir -match '(^|[\\/])\.\.([\\/]|$)') { return $false }
    if ($null -ne $d5Prof -and $d5Prof -cnotmatch '^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$') { return $false }
    $d5Full = [IO.Path]::GetFullPath($d5Dir).TrimEnd([char]92)
    if (!$d5Full.StartsWith($aiRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $d5Full -ieq $cleanProfile -or
        $d5Full.StartsWith($cleanProfile + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $cleanProfile.StartsWith($d5Full + '\', [StringComparison]::OrdinalIgnoreCase) -or
        !(Test-PlainPathOrAncestors $d5Full)) { return $false }
    return $true
}

function Resolve-D5StaticValue($expr, $vars, $scriptDir) {
    $k = Get-AstKind $expr
    switch ($k) {
        'StringConstantExpressionAst' { return [string]$expr.Value }
        'ConstantExpressionAst' {
            if ($expr.Value -is [int] -or $expr.Value -is [long] -or $expr.Value -is [bool]) { return [string]$expr.Value }
            throw 'TASK_MIGRATION_AMBIGUOUS'
        }
        'VariableExpressionAst' {
            if ($expr.Splatted) { throw 'TASK_MIGRATION_AMBIGUOUS' }
            $vp = $expr.VariablePath.UserPath
            if ($vp -ieq 'env:LOCALAPPDATA') { $v = [Environment]::GetFolderPath('LocalApplicationData'); if (!$v) { throw 'TASK_MIGRATION_AMBIGUOUS' }; return $v }
            if ($vp -ieq 'env:ProgramFiles') { $v = [Environment]::GetFolderPath('ProgramFiles'); if (!$v) { throw 'TASK_MIGRATION_AMBIGUOUS' }; return $v }
            if ($vp -ieq 'env:ProgramFiles(x86)') { $v = [Environment]::GetFolderPath('ProgramFilesX86'); if (!$v) { throw 'TASK_MIGRATION_AMBIGUOUS' }; return $v }
            if ($vp -ieq 'PSScriptRoot' -and $scriptDir) { return $scriptDir }
            if ($vars.ContainsKey($vp)) { return $vars[$vp] }
            throw 'TASK_MIGRATION_AMBIGUOUS'
        }
        'ExpandableStringExpressionAst' {
            if ($expr.StringConstantType -notin @('DoubleQuoted', 'BareWord')) { throw 'TASK_MIGRATION_AMBIGUOUS' }
            $raw = $expr.Extent.Text; $base = $expr.Extent.StartOffset
            $sIdx = if ($expr.StringConstantType -eq 'DoubleQuoted') { 1 } else { 0 }
            $eIdx = if ($expr.StringConstantType -eq 'DoubleQuoted') { $raw.Length - 1 } else { $raw.Length }
            $cur = $sIdx; $sb = [Text.StringBuilder]::new()
            foreach ($n in @($expr.NestedExpressions | Sort-Object { $_.Extent.StartOffset })) {
                if ((Get-AstKind $n) -cne 'VariableExpressionAst') { throw 'TASK_MIGRATION_AMBIGUOUS' }
                $ns = $n.Extent.StartOffset - $base; $ne = $n.Extent.EndOffset - $base
                if ($ns -lt $cur -or $ne -gt $eIdx) { throw 'TASK_MIGRATION_AMBIGUOUS' }
                $chunk = $raw.Substring($cur, $ns - $cur)
                if ($chunk -match '`[^"]') { throw 'TASK_MIGRATION_AMBIGUOUS' }
                $null = $sb.Append($chunk.Replace('`"', '"'))
                $rv = Resolve-D5StaticValue $n $vars $scriptDir
                if ($rv -isnot [string]) { throw 'TASK_MIGRATION_AMBIGUOUS' }
                $null = $sb.Append($rv); $cur = $ne
            }
            $tail = $raw.Substring($cur, $eIdx - $cur)
            if ($tail -match '`[^"]') { throw 'TASK_MIGRATION_AMBIGUOUS' }
            $null = $sb.Append($tail.Replace('`"', '"'))
            return $sb.ToString()
        }
        'ParenExpressionAst' {
            $in = $expr.Pipeline
            if ((Get-AstKind $in) -ceq 'PipelineAst' -and $in.PipelineElements.Count -eq 1) {
                $pe = $in.PipelineElements[0]; $pk = Get-AstKind $pe
                if ($pk -ceq 'CommandExpressionAst') { return Resolve-D5StaticValue $pe.Expression $vars $scriptDir }
                if ($pk -ceq 'CommandAst') { return Resolve-D5StaticCommandValue $pe $vars $scriptDir }
            }
            throw 'TASK_MIGRATION_AMBIGUOUS'
        }
        'ArrayLiteralAst' {
            $items = [Collections.Generic.List[string]]::new()
            foreach ($el in $expr.Elements) {
                $val = Resolve-D5StaticValue $el $vars $scriptDir
                if ($val -is [string]) { $items.Add($val) }
                elseif ($val -is [Array]) { foreach ($x in $val) { if ($x -isnot [string]) { throw 'TASK_MIGRATION_AMBIGUOUS' }; $items.Add($x) } }
                else { throw 'TASK_MIGRATION_AMBIGUOUS' }
            }
            return ,([string[]]$items.ToArray())
        }
        'ArrayExpressionAst' {
            $items = [Collections.Generic.List[string]]::new()
            foreach ($st in $expr.SubExpression.Statements) {
                if ((Get-AstKind $st) -cne 'PipelineAst' -or $st.PipelineElements.Count -ne 1 -or
                    (Get-AstKind $st.PipelineElements[0]) -cne 'CommandExpressionAst') { throw 'TASK_MIGRATION_AMBIGUOUS' }
                $val = Resolve-D5StaticValue $st.PipelineElements[0].Expression $vars $scriptDir
                if ($val -is [string]) { $items.Add($val) }
                elseif ($val -is [Array]) { foreach ($x in $val) { if ($x -isnot [string]) { throw 'TASK_MIGRATION_AMBIGUOUS' }; $items.Add($x) } }
                else { throw 'TASK_MIGRATION_AMBIGUOUS' }
            }
            return ,([string[]]$items.ToArray())
        }
        'BinaryExpressionAst' {
            if ([string]$expr.Operator -cne 'Plus') { throw 'TASK_MIGRATION_AMBIGUOUS' }
            $l = Resolve-D5StaticValue $expr.Left $vars $scriptDir
            $r = Resolve-D5StaticValue $expr.Right $vars $scriptDir
            if ($l -is [string] -and $r -is [string]) { return $l + $r }
            if ($l -is [Array] -and $r -is [Array]) { return ,([string[]]($l + $r)) }
            if ($l -is [Array] -and $r -is [string]) { return ,([string[]]($l + @($r))) }
            throw 'TASK_MIGRATION_AMBIGUOUS'
        }
        'InvokeMemberExpressionAst' {
            if ($expr.Static -and (Get-AstKind $expr.Expression) -ceq 'TypeExpressionAst' -and
                (Get-AstKind $expr.Member) -ceq 'StringConstantExpressionAst') {
                $tn = $expr.Expression.TypeName.FullName; $mn = $expr.Member.Value
                if ($tn -iin @('Environment', 'System.Environment') -and $mn -ieq 'GetFolderPath' -and $expr.Arguments.Count -eq 1) {
                    $fa = Resolve-D5StaticValue $expr.Arguments[0] $vars $scriptDir
                    if ($fa -cin @('LocalApplicationData', 'ProgramFiles', 'ProgramFilesX86')) {
                        $v = [Environment]::GetFolderPath($fa); if (!$v) { throw 'TASK_MIGRATION_AMBIGUOUS' }; return $v
                    }
                }
                if ($tn -iin @('IO.Path', 'System.IO.Path') -and $mn -ieq 'Combine' -and
                    $expr.Arguments.Count -ge 2 -and $expr.Arguments.Count -le 6) {
                    $segs = @()
                    foreach ($arg in $expr.Arguments) {
                        $s = Resolve-D5StaticValue $arg $vars $scriptDir
                        if ($s -isnot [string] -or !$s) { throw 'TASK_MIGRATION_AMBIGUOUS' }
                        $segs += $s
                    }
                    return [IO.Path]::Combine([string[]]$segs)
                }
            }
            throw 'TASK_MIGRATION_AMBIGUOUS'
        }
        default { throw 'TASK_MIGRATION_AMBIGUOUS' }
    }
}

function Resolve-D5StaticCommandValue($cmdAst, $vars, $scriptDir) {
    if ($cmdAst.Redirections.Count -gt 0 -or [string]$cmdAst.InvocationOperator -cne 'Unknown' -or
        $cmdAst.GetCommandName() -ine 'Join-Path') { throw 'TASK_MIGRATION_AMBIGUOUS' }
    $segs = [Collections.Generic.List[string]]::new()
    $els = $cmdAst.CommandElements
    for ($i = 1; $i -lt $els.Count; $i++) {
        $el = $els[$i]
        if ((Get-AstKind $el) -ceq 'CommandParameterAst') {
            if ($el.ParameterName -notin @('Path', 'ChildPath', 'AdditionalChildPath')) { throw 'TASK_MIGRATION_AMBIGUOUS' }
            $va = if ($null -ne $el.Argument) { $el.Argument } else { $i++; if ($i -ge $els.Count) { throw 'TASK_MIGRATION_AMBIGUOUS' }; $els[$i] }
            $val = Resolve-D5StaticValue $va $vars $scriptDir
            if ($val -is [string]) { $segs.Add($val) }
            elseif ($val -is [Array]) { foreach ($x in $val) { if ($x -isnot [string]) { throw 'TASK_MIGRATION_AMBIGUOUS' }; $segs.Add($x) } }
            else { throw 'TASK_MIGRATION_AMBIGUOUS' }
            continue
        }
        $val = Resolve-D5StaticValue $el $vars $scriptDir
        if ($val -is [string]) { $segs.Add($val) } else { throw 'TASK_MIGRATION_AMBIGUOUS' }
    }
    if ($segs.Count -lt 2) { throw 'TASK_MIGRATION_AMBIGUOUS' }
    return [IO.Path]::Combine([string[]]$segs.ToArray())
}

function Test-D5ReadOnlyGuardCommand($cmdAst, $vars, $scriptDir) {
    if ($cmdAst.Redirections.Count -gt 0 -or [string]$cmdAst.InvocationOperator -cne 'Unknown') { return $false }
    $cn = $cmdAst.GetCommandName(); $els = $cmdAst.CommandElements
    if ($cn -ieq 'Test-Path') {
        $saw = $false
        for ($i = 1; $i -lt $els.Count; $i++) {
            $el = $els[$i]
            if ((Get-AstKind $el) -ceq 'CommandParameterAst') {
                if ($el.ParameterName -notin @('Path', 'LiteralPath', 'PathType', 'ErrorAction')) { return $false }
                $va = if ($null -ne $el.Argument) { $el.Argument } else { $i++; if ($i -ge $els.Count) { return $false }; $els[$i] }
                $v = Resolve-D5StaticValue $va $vars $scriptDir
                if ($v -isnot [string]) { return $false }
                if ($el.ParameterName -iin @('Path', 'LiteralPath')) {
                    if (Test-D5Contamination $v) { return $false }
                    $saw = $true
                }
                continue
            }
            $v = Resolve-D5StaticValue $el $vars $scriptDir
            if ($v -isnot [string] -or (Test-D5Contamination $v)) { return $false }
            $saw = $true
        }
        return $saw
    }
    if ($cn -ieq 'Get-NetTCPConnection') {
        $portOk = $false
        for ($i = 1; $i -lt $els.Count; $i++) {
            $el = $els[$i]
            if ((Get-AstKind $el) -cne 'CommandParameterAst') { return $false }
            $va = if ($null -ne $el.Argument) { $el.Argument } else { $i++; if ($i -ge $els.Count) { return $false }; $els[$i] }
            $v = Resolve-D5StaticValue $va $vars $scriptDir
            if ($el.ParameterName -ieq 'LocalPort') { if ($v -cne '9333') { return $false }; $portOk = $true }
            elseif ($el.ParameterName -ieq 'State') { if ($v -ine 'Listen') { return $false } }
            elseif ($el.ParameterName -ieq 'LocalAddress') { if ($v -cne '127.0.0.1') { return $false } }
            elseif ($el.ParameterName -ieq 'ErrorAction') { if ($v -notin @('SilentlyContinue', 'Ignore', 'Stop')) { return $false } }
            else { return $false }
        }
        return $portOk
    }
    return $false
}

function Test-D5GuardCondition($condPipe, $vars, $guardVars, $scriptDir) {
    if ((Get-AstKind $condPipe) -cne 'PipelineAst' -or $condPipe.PipelineElements.Count -ne 1) { return $false }
    $el = $condPipe.PipelineElements[0]; $ek = Get-AstKind $el
    if ($ek -ceq 'CommandAst') { return Test-D5ReadOnlyGuardCommand $el $vars $scriptDir }
    if ($ek -cne 'CommandExpressionAst') { return $false }
    $expr = $el.Expression
    if ((Get-AstKind $expr) -ceq 'UnaryExpressionAst' -and [string]$expr.TokenKind -cin @('Exclaim', 'Not')) { $expr = $expr.Child }
    $xk = Get-AstKind $expr
    if ($xk -ceq 'ParenExpressionAst') { return Test-D5GuardCondition $expr.Pipeline $vars $guardVars $scriptDir }
    if ($xk -ceq 'VariableExpressionAst' -and !$expr.Splatted) { return $guardVars.ContainsKey($expr.VariablePath.UserPath) }
    if ($xk -ceq 'BinaryExpressionAst' -and [string]$expr.Operator -cin @('Ine', 'Cne')) {
        if ((Get-AstKind $expr.Left) -ceq 'VariableExpressionAst' -and (Get-AstKind $expr.Right) -ceq 'VariableExpressionAst' -and
            !$expr.Left.Splatted -and !$expr.Right.Splatted) {
            $ln = $expr.Left.VariablePath.UserPath; $rn = $expr.Right.VariablePath.UserPath
            return ($ln -ieq 'null' -and $guardVars.ContainsKey($rn)) -or ($rn -ieq 'null' -and $guardVars.ContainsKey($ln))
        }
    }
    return $false
}

function Resolve-D5ChromeArgs($rawArgVal) {
    if ($rawArgVal -is [string]) { return Split-TaskArguments $rawArgVal }
    if ($rawArgVal -is [Array] -and $rawArgVal.Count -gt 0) {
        $out = [Collections.Generic.List[string]]::new()
        foreach ($item in $rawArgVal) {
            if ($item -isnot [string] -or !$item -or $item -match '[\r\n\x00%]') { return $null }
            if ($item -match '^(--[A-Za-z0-9-]+)="([^"]*)"$') { $out.Add($Matches[1] + '=' + $Matches[2]) }
            elseif ($item.Length -ge 2 -and $item.StartsWith('"') -and $item.EndsWith('"') -and
                    !$item.Substring(1, $item.Length - 2).Contains('"')) { $out.Add($item.Substring(1, $item.Length - 2)) }
            elseif ($item.Contains('"')) { return $null }
            else { $out.Add($item) }
        }
        return ,([string[]]$out.ToArray())
    }
    return $null
}

function Test-AttestedD5ScriptAst($ast, $aiRoot, $cleanProfile, $scriptDir, $d5FileOwners) {
    if (!$ast -or $ast.BeginBlock -or $ast.ProcessBlock -or $ast.DynamicParamBlock -or !$ast.EndBlock) { return $false }
    $vars = @{}; $guardVars = @{}
    if ($ast.ParamBlock) {
        foreach ($pAst in $ast.ParamBlock.Parameters) {
            if (!$pAst.DefaultValue) { return $false }
            foreach ($attr in $pAst.Attributes) {
                if ((Get-AstKind $attr) -cne 'TypeConstraintAst' -or
                    $attr.TypeName.FullName -notin @('string', 'System.String', 'int', 'System.Int32')) { return $false }
            }
            $vars[$pAst.Name.VariablePath.UserPath] = Resolve-D5StaticValue $pAst.DefaultValue $vars $scriptDir
        }
    }
    $stmts = $ast.EndBlock.Statements
    if (!$stmts -or $stmts.Count -lt 1 -or $stmts.Count -gt 32) { return $false }
    $launchCount = 0
    foreach ($stmt in $stmts) {
        $sk = Get-AstKind $stmt
        if ($sk -ceq 'AssignmentStatementAst') {
            if ([string]$stmt.Operator -cne 'Equals' -or (Get-AstKind $stmt.Left) -cne 'VariableExpressionAst' -or
                $stmt.Left.Splatted) { return $false }
            $lhs = $stmt.Left.VariablePath.UserPath; $rhs = $stmt.Right; $rk = Get-AstKind $rhs
            if ($lhs -ieq 'ErrorActionPreference') {
                if ($rk -cne 'CommandExpressionAst') { return $false }
                $pref = Resolve-D5StaticValue $rhs.Expression $vars $scriptDir
                if ($pref -iin @('Stop', 'SilentlyContinue', 'Continue')) { continue }
                return $false
            }
            if ($lhs -ieq 'null') {
                if ($rk -ceq 'PipelineAst' -and $rhs.PipelineElements.Count -eq 1) { $stmt = $rhs; $sk = $rk }
                elseif ($rk -cin @('CommandExpressionAst', 'CommandAst')) { $stmt = $rhs; $sk = $rk }
                else { return $false }
            } else {
                if ($lhs -match ':' -or $lhs -iin @('PSScriptRoot', 'PSCommandPath', 'args')) { return $false }
                if ($rk -ceq 'CommandExpressionAst') { $vars[$lhs] = Resolve-D5StaticValue $rhs.Expression $vars $scriptDir; continue }
                if ($rk -ceq 'PipelineAst' -and $rhs.PipelineElements.Count -eq 1) {
                    $pe = $rhs.PipelineElements[0]; $pk = Get-AstKind $pe
                    if ($pk -ceq 'CommandAst') {
                        if ($pe.GetCommandName() -ieq 'Join-Path') { $vars[$lhs] = Resolve-D5StaticCommandValue $pe $vars $scriptDir; continue }
                        if (Test-D5ReadOnlyGuardCommand $pe $vars $scriptDir) { $guardVars[$lhs] = $true; continue }
                    }
                    if ($pk -ceq 'CommandExpressionAst') { $vars[$lhs] = Resolve-D5StaticValue $pe.Expression $vars $scriptDir; continue }
                }
                return $false
            }
        }
        if ($sk -ceq 'IfStatementAst') {
            if ($stmt.ElseClause -or $stmt.Clauses.Count -ne 1) { return $false }
            if (!(Test-D5GuardCondition $stmt.Clauses[0].Item1 $vars $guardVars $scriptDir)) { return $false }
            $body = $stmt.Clauses[0].Item2
            if (!$body.Statements -or $body.Statements.Count -ne 1) { return $false }
            $ex = $body.Statements[0]; $exk = Get-AstKind $ex
            if ($exk -ceq 'ExitStatementAst') {
                if ($ex.Pipeline) {
                    if ((Get-AstKind $ex.Pipeline) -cne 'PipelineAst' -or $ex.Pipeline.PipelineElements.Count -ne 1 -or
                        (Get-AstKind $ex.Pipeline.PipelineElements[0]) -cne 'CommandExpressionAst') { return $false }
                    if ((Resolve-D5StaticValue $ex.Pipeline.PipelineElements[0].Expression $vars $scriptDir) -notmatch '^[0-9]{1,3}$') { return $false }
                }
                continue
            }
            if ($exk -ceq 'ReturnStatementAst' -and !$ex.Pipeline) { continue }
            return $false
        }
        $element = if ($sk -ceq 'PipelineAst') {
            if ($stmt.Background -or $stmt.PipelineElements.Count -ne 1) { return $false }
            $stmt.PipelineElements[0]
        } elseif ($sk -cin @('CommandAst', 'CommandExpressionAst')) { $stmt } else { return $false }
        $ek = Get-AstKind $element
        if ($ek -ceq 'CommandExpressionAst') {
            $expr = $element.Expression
            if ((Get-AstKind $expr) -ceq 'ConvertExpressionAst' -and $expr.Type.TypeName.FullName -iin @('void', 'System.Void')) { $expr = $expr.Child }
            if ((Get-AstKind $expr) -ceq 'ParenExpressionAst' -and (Get-AstKind $expr.Pipeline) -ceq 'PipelineAst' -and
                $expr.Pipeline.PipelineElements.Count -eq 1 -and
                (Get-AstKind $expr.Pipeline.PipelineElements[0]) -ceq 'CommandExpressionAst') { $expr = $expr.Pipeline.PipelineElements[0].Expression }
            if ((Get-AstKind $expr) -ceq 'InvokeMemberExpressionAst' -and $expr.Static -and
                (Get-AstKind $expr.Expression) -ceq 'TypeExpressionAst' -and
                $expr.Expression.TypeName.FullName -iin @('Diagnostics.Process', 'System.Diagnostics.Process') -and
                (Get-AstKind $expr.Member) -ceq 'StringConstantExpressionAst' -and
                $expr.Member.Value -ieq 'Start' -and $expr.Arguments.Count -eq 2) {
                $exe = Resolve-D5StaticValue $expr.Arguments[0] $vars $scriptDir
                $resolvedArgs = Resolve-D5ChromeArgs (Resolve-D5StaticValue $expr.Arguments[1] $vars $scriptDir)
                if (!(Test-AttestedChromeLaunch $exe $resolvedArgs $aiRoot $cleanProfile)) { return $false }
                $launchCount++; continue
            }
            return $false
        }
        if ($ek -cne 'CommandAst' -or $element.Redirections.Count -gt 0) { return $false }
        $els = $element.CommandElements; $op = [string]$element.InvocationOperator
        if ($op -ceq 'Ampersand') {
            if ($els.Count -lt 2) { return $false }
            $exe = Resolve-D5StaticValue $els[0] $vars $scriptDir
            $argItems = [Collections.Generic.List[string]]::new()
            for ($i = 1; $i -lt $els.Count; $i++) {
                if ((Get-AstKind $els[$i]) -ceq 'CommandParameterAst') {
                    $pt = '-' + $els[$i].ParameterName
                    if ($null -ne $els[$i].Argument) {
                        $pv = Resolve-D5StaticValue $els[$i].Argument $vars $scriptDir
                        if ($pv -isnot [string]) { return $false }
                        $pt += ':' + $pv
                    }
                    $argItems.Add($pt)
                } else {
                    $av = Resolve-D5StaticValue $els[$i] $vars $scriptDir
                    if ($av -is [string]) { $argItems.Add($av) }
                    elseif ($av -is [Array]) { foreach ($x in $av) { if ($x -isnot [string]) { return $false }; $argItems.Add($x) } }
                    else { return $false }
                }
            }
            $resolvedArgs = Resolve-D5ChromeArgs ([string[]]$argItems.ToArray())
            if (!(Test-AttestedChromeLaunch $exe $resolvedArgs $aiRoot $cleanProfile)) { return $false }
            $launchCount++; continue
        }
        if ($op -cne 'Unknown' -or $element.GetCommandName() -ine 'Start-Process') { return $false }
        $spExe = $null; $spArgs = $null; $spWork = $null; $posIdx = 0
        for ($i = 1; $i -lt $els.Count; $i++) {
            $el = $els[$i]
            if ((Get-AstKind $el) -ceq 'CommandParameterAst') {
                $pn = $el.ParameterName
                if ($pn -notin @('FilePath', 'ArgumentList', 'WorkingDirectory', 'WindowStyle')) { return $false }
                $va = if ($null -ne $el.Argument) { $el.Argument } else { $i++; if ($i -ge $els.Count) { return $false }; $els[$i] }
                $v = Resolve-D5StaticValue $va $vars $scriptDir
                if ($pn -ieq 'FilePath') { if ($null -ne $spExe -or $v -isnot [string]) { return $false }; $spExe = $v }
                elseif ($pn -ieq 'ArgumentList') { if ($null -ne $spArgs) { return $false }; $spArgs = $v }
                elseif ($pn -ieq 'WorkingDirectory') { if ($null -ne $spWork -or $v -isnot [string]) { return $false }; $spWork = $v }
                elseif ($v -isnot [string] -or $v -notin @('Normal', 'Minimized', 'Maximized', 'Hidden')) { return $false }
                continue
            }
            $v = Resolve-D5StaticValue $el $vars $scriptDir
            if ($posIdx -eq 0 -and $null -eq $spExe -and $v -is [string]) { $spExe = $v; $posIdx++ }
            elseif ($posIdx -eq 1 -and $null -eq $spArgs) { $spArgs = $v; $posIdx++ }
            else { return $false }
        }
        if ($spWork) {
            if ($spWork -notmatch '^[A-Za-z]:\\' -or $spWork -match '(^|[\\/])\.\.([\\/]|$)' -or
                (Test-D5Contamination $spWork) -or !(Test-PathCustody $spWork $d5FileOwners $false)) { return $false }
            $wFull = [IO.Path]::GetFullPath($spWork).TrimEnd([char]92)
            if ($wFull -ieq $cleanProfile -or $wFull.StartsWith($cleanProfile + '\', [StringComparison]::OrdinalIgnoreCase)) { return $false }
        }
        $resolvedArgs = Resolve-D5ChromeArgs $spArgs
        if (!(Test-AttestedChromeLaunch $spExe $resolvedArgs $aiRoot $cleanProfile)) { return $false }
        $launchCount++
    }
    return $launchCount -eq 1
}

function Get-AttestedD5ScriptResult($scriptPath, $aiRoot, $cleanProfile, $d5FileOwners) {
    try {
        if (!$scriptPath -or $scriptPath -isnot [string] -or $scriptPath -notmatch '^[A-Za-z]:\\.*\.ps1$' -or
            $scriptPath -match '[%"\r\n\t\x00]' -or $scriptPath -match '(^|[\\/])\.\.([\\/]|$)') {
            return @{ ok = $false; reason = 'UNVERIFIED_ACTION' }
        }
        if (Test-D5Contamination $scriptPath) {
            return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' }
        }
        $scriptFull = [IO.Path]::GetFullPath($scriptPath)
        if ($scriptFull -ine $scriptPath -or $scriptFull -ieq $cleanProfile -or
            $scriptFull.StartsWith($cleanProfile + '\', [StringComparison]::OrdinalIgnoreCase)) {
            return @{ ok = $false; reason = 'UNVERIFIED_CUSTODY' }
        }
        $scriptSddl = Get-AttestedPathCustodySddl $scriptFull $d5FileOwners $true
        if (!$scriptSddl) { return @{ ok = $false; reason = 'UNVERIFIED_CUSTODY' } }
        $sig = Get-AuthenticodeSignature -LiteralPath $scriptFull
        if (!$sig -or $sig.Status -cnotin @('Valid', 'NotSigned')) {
            return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' }
        }
        $sigTag = if ($sig.Status -ceq 'Valid' -and $sig.SignerCertificate) { 'SIGNED:' + [string]$sig.SignerCertificate.Thumbprint } else { 'UNSIGNED_OWNED' }
        $info = [IO.FileInfo]::new($scriptFull)
        if ($info.Length -lt 1 -or $info.Length -gt 65536) { return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' } }
        $bytes = [IO.File]::ReadAllBytes($scriptFull)
        if ($bytes.Length -lt 1 -or $bytes.Length -gt 65536 -or ([Array]::IndexOf($bytes, [byte]0) -ge 0)) {
            return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' }
        }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $scriptHash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '') }
        finally { $sha.Dispose() }
        $off = if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { 3 } else { 0 }
        $scriptText = ([Text.UTF8Encoding]::new($false, $true)).GetString($bytes, $off, $bytes.Length - $off)
        if (!$scriptText -or (Test-D5Contamination $scriptText)) { return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' } }
        $tokens = $null; $parseErrors = $null
        $ast = [Management.Automation.Language.Parser]::ParseInput($scriptText, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors -and $parseErrors.Count -gt 0) { return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' } }
        if (!(Test-AttestedD5ScriptAst $ast $aiRoot $cleanProfile ([IO.Path]::GetDirectoryName($scriptFull)) $d5FileOwners)) {
            return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' }
        }
        $val = @('PWSH_SCRIPT', $scriptFull.ToLowerInvariant(), $scriptHash, $bytes.Length, $sigTag, $scriptSddl) -join '|'
        return @{ ok = $true; value = $val }
    } catch { return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' } }
}

function Get-AttestedD5Script($scriptPath, $aiRoot, $cleanProfile, $d5FileOwners) {
    $res = Get-AttestedD5ScriptResult $scriptPath $aiRoot $cleanProfile $d5FileOwners
    if ($res.ok) { return $res.value }
    return $null
}

function Get-AttestedD5PowerShellActionResult($pwshPath, $argsLine, $workDir, $aiRoot, $cleanProfile, $d5FileOwners) {
    if (!(Test-TrustedPowerShell $pwshPath)) { return @{ ok = $false; reason = 'UNVERIFIED_EXE' } }
    if ($workDir -and !(Test-PathCustody $workDir $d5FileOwners $false)) {
        return @{ ok = $false; reason = 'UNVERIFIED_CUSTODY' }
    }
    $tokens = Split-TaskArguments $argsLine
    if (!$tokens) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
    $seen = @{}; $mode = $null; $payload = $null
    for ($i = 0; $i -lt $tokens.Count; $i++) {
        $tok = $tokens[$i]
        if ($tok -iin @('-NoProfile', '-nop')) { if ($seen['NoProfile']) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }; $seen['NoProfile'] = $true; continue }
        if ($tok -iin @('-NonInteractive', '-noni')) { if ($seen['NonInteractive']) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }; $seen['NonInteractive'] = $true; continue }
        if ($tok -iin @('-NoLogo', '-nol')) { if ($seen['NoLogo']) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }; $seen['NoLogo'] = $true; continue }
        if ($tok -iin @('-WindowStyle', '-w')) {
            if ($seen['WindowStyle'] -or ($i + 1) -ge $tokens.Count) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
            $i++; if ($tokens[$i] -notin @('Hidden', 'Minimized', 'Normal')) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
            $seen['WindowStyle'] = $true; continue
        }
        if ($tok -imatch '^-(?:WindowStyle|w):(.+)$') {
            if ($seen['WindowStyle'] -or $Matches[1] -notin @('Hidden', 'Minimized', 'Normal')) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
            $seen['WindowStyle'] = $true; continue
        }
        if ($tok -iin @('-ExecutionPolicy', '-ep')) {
            if ($seen['ExecutionPolicy'] -or ($i + 1) -ge $tokens.Count) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
            $i++; if ($tokens[$i] -notin @('Bypass', 'RemoteSigned', 'AllSigned', 'Default', 'Restricted', 'Unrestricted')) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
            $seen['ExecutionPolicy'] = $true; continue
        }
        if ($tok -imatch '^-(?:ExecutionPolicy|ep):(.+)$') {
            if ($seen['ExecutionPolicy'] -or $Matches[1] -notin @('Bypass', 'RemoteSigned', 'AllSigned', 'Default', 'Restricted', 'Unrestricted')) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
            $seen['ExecutionPolicy'] = $true; continue
        }
        if ($tok -iin @('-File', '-f')) { if (($i + 2) -ne $tokens.Count) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }; $mode = 'FILE'; $payload = $tokens[$i + 1]; break }
        if ($tok -imatch '^-(?:File|f):(.+)$') { if (($i + 1) -ne $tokens.Count) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }; $mode = 'FILE'; $payload = $Matches[1]; break }
        if ($tok -iin @('-Command', '-c')) { if (($i + 2) -ne $tokens.Count) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }; $mode = 'COMMAND'; $payload = $tokens[$i + 1]; break }
        return @{ ok = $false; reason = 'UNVERIFIED_ACTION' }
    }
    if (!$mode -or !$payload) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
    if ($mode -ceq 'FILE') {
        $sa = Get-AttestedD5ScriptResult $payload $aiRoot $cleanProfile $d5FileOwners
        if (!$sa.ok) { return $sa }
        return @{ ok = $true; value = ('PWSH|' + $pwshPath.ToLowerInvariant() + '|' + $sa.value) }
    }
    try {
        if (Test-D5Contamination $payload) { return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' } }
        $ct = $null; $ce = $null
        $cmdAst = [Management.Automation.Language.Parser]::ParseInput($payload, [ref]$ct, [ref]$ce)
        if (($ce -and $ce.Count -gt 0) -or !$cmdAst -or !$cmdAst.EndBlock -or !$cmdAst.EndBlock.Statements.Count) {
            return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' }
        }
        if ($cmdAst.EndBlock.Statements.Count -eq 1 -and (Get-AstKind $cmdAst.EndBlock.Statements[0]) -ceq 'PipelineAst' -and
            $cmdAst.EndBlock.Statements[0].PipelineElements.Count -eq 1 -and
            (Get-AstKind $cmdAst.EndBlock.Statements[0].PipelineElements[0]) -ceq 'CommandAst') {
            $c0 = $cmdAst.EndBlock.Statements[0].PipelineElements[0]
            if ([string]$c0.InvocationOperator -ceq 'Ampersand' -and $c0.Redirections.Count -eq 0 -and $c0.CommandElements.Count -eq 1) {
                $target = Resolve-D5StaticValue $c0.CommandElements[0] @{} $null
                if ($target -is [string] -and $target -imatch '\.ps1$') {
                    $sa = Get-AttestedD5ScriptResult $target $aiRoot $cleanProfile $d5FileOwners
                    if (!$sa.ok) { return $sa }
                    return @{ ok = $true; value = ('PWSH|' + $pwshPath.ToLowerInvariant() + '|' + $sa.value) }
                }
            }
        }
        if (!(Test-AttestedD5ScriptAst $cmdAst $aiRoot $cleanProfile $null $d5FileOwners)) {
            return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' }
        }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $ch = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($payload))).Replace('-', '') }
        finally { $sha.Dispose() }
        return @{ ok = $true; value = ('PWSH_CMD|' + $pwshPath.ToLowerInvariant() + '|' + $ch) }
    } catch { return @{ ok = $false; reason = 'UNVERIFIED_SCRIPT' } }
}

function Get-AttestedD5PowerShellAction($pwshPath, $argsLine, $workDir, $aiRoot, $cleanProfile, $d5FileOwners) {
    $res = Get-AttestedD5PowerShellActionResult $pwshPath $argsLine $workDir $aiRoot $cleanProfile $d5FileOwners
    if ($res.ok) { return $res.value }
    return $null
}

function Get-D5AttestationResult($d5, [bool]$allowDisabled = $false, $replacementPowerShell = $null) {
    try {
        if (!$d5 -or (!$allowDisabled -and !$d5.Enabled) -or $d5.Name -cne 'AI-Commerce-D5-CDP-AtLogon') {
            return @{ ok = $false; reason = 'UNVERIFIED_BASE' }
        }
        if (!$d5.Xml -or $d5.Xml.IndexOf($name, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $d5.Xml -match '(?i)WakeHub-9444-Clean') {
            return @{ ok = $false; reason = 'UNVERIFIED_BASE' }
        }
        try {
            $d5Acl = [Security.AccessControl.RawSecurityDescriptor]::new($d5.GetSecurityDescriptor(7))
        } catch {
            return @{ ok = $false; reason = 'UNVERIFIED_ACL' }
        }
        $trustedSids = @($sid, 'S-1-5-18', 'S-1-5-32-544')
        $d5FileOwners = @($sid) + $installOwnerSids
        if (!$d5Acl.Owner -or $d5Acl.Owner.Value -cnotin $trustedSids -or !$d5Acl.DiscretionaryAcl) {
            return @{ ok = $false; reason = 'UNVERIFIED_ACL' }
        }
        foreach ($ace in $d5Acl.DiscretionaryAcl) {
            if ($ace -isnot [Security.AccessControl.CommonAce]) { return @{ ok = $false; reason = 'UNVERIFIED_ACL' } }
            if ($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly) { continue }
            if ($ace.AceType -eq [Security.AccessControl.AceType]::AccessDenied) { continue }
            if ($ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed) { return @{ ok = $false; reason = 'UNVERIFIED_ACL' } }
            if (($ace.AccessMask -band 0x500D0156) -and $ace.SecurityIdentifier.Value -cnotin $trustedSids) {
                return @{ ok = $false; reason = 'UNVERIFIED_ACL' }
            }
        }
        $def = $d5.Definition; $p = $def.Principal
        if (!(Test-Owner $p.UserId) -or $p.LogonType -ne 3 -or $p.RunLevel -ne 0 -or $p.GroupId) {
            return @{ ok = $false; reason = 'UNVERIFIED_PRINCIPAL' }
        }
        if ($def.Actions.Context -and $def.Actions.Context -cne $p.Id) {
            return @{ ok = $false; reason = 'UNVERIFIED_PRINCIPAL' }
        }
        if ($def.Triggers.Count -ne 1) { return @{ ok = $false; reason = 'UNVERIFIED_TRIGGER' } }
        $t = $def.Triggers.Item(1)
        if ($t.Type -ne 9 -or (!$allowDisabled -and !$t.Enabled) -or !(Test-Owner $t.UserId) -or
            ($t.Repetition -and ($t.Repetition.Interval -or $t.Repetition.Duration))) {
            return @{ ok = $false; reason = 'UNVERIFIED_TRIGGER' }
        }
        if ($def.Actions.Count -ne 1) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
        $a = $def.Actions.Item(1)
        if ($a.Type -ne 0 -or !$a.Path -or !$a.Arguments) { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
        $rawPath = [string]$a.Path
        if ($rawPath.Length -ge 2 -and $rawPath.StartsWith('"') -and $rawPath.EndsWith('"')) {
            $rawPath = $rawPath.Substring(1, $rawPath.Length - 2)
        }
        if ($rawPath.Contains('"') -or $rawPath -match '[%\r\n\x00]') { return @{ ok = $false; reason = 'UNVERIFIED_ACTION' } }
        $localRoot = [Environment]::GetFolderPath('LocalApplicationData')
        if (!$localRoot) { return @{ ok = $false; reason = 'UNVERIFIED_BASE' } }
        $aiRoot = [IO.Path]::GetFullPath((Join-Path $localRoot 'AI-Commerce')).TrimEnd([char]92)
        $cleanProfile = [IO.Path]::GetFullPath((Join-Path $aiRoot 'WakeHub-9444-Clean')).TrimEnd([char]92)
        if ($env:AI_COMMERCE_TASK_PROFILE_DIR -and
            [IO.Path]::GetFullPath($env:AI_COMMERCE_TASK_PROFILE_DIR).TrimEnd([char]92) -ine $cleanProfile) {
            return @{ ok = $false; reason = 'UNVERIFIED_BASE' }
        }
        $d5Work = $null
        if ($a.WorkingDirectory) {
            if ($a.WorkingDirectory -notmatch '^[A-Za-z]:[\\/]' -or
                $a.WorkingDirectory -match '(^|[\\/])\.\.([\\/]|$)' -or
                (Test-D5Contamination $a.WorkingDirectory) -or
                !(Test-PlainPathOrAncestors $a.WorkingDirectory)) {
                return @{ ok = $false; reason = 'UNVERIFIED_ACTION' }
            }
            $d5Work = [IO.Path]::GetFullPath($a.WorkingDirectory).TrimEnd([char]92)
            if ($d5Work -ieq $cleanProfile -or
                $d5Work.StartsWith($cleanProfile + '\', [StringComparison]::OrdinalIgnoreCase)) {
                return @{ ok = $false; reason = 'UNVERIFIED_ACTION' }
            }
        }
        if ($rawPath -in $chromeExecutables) {
            if (!(Test-SignedExecutable $rawPath '(^|, )CN=Google (LLC|Inc)(,|$)')) {
                return @{ ok = $false; reason = 'UNVERIFIED_EXE' }
            }
            $d5Args = Split-TaskArguments ([string]$a.Arguments)
            try {
                if (!(Test-AttestedChromeLaunch $rawPath $d5Args $aiRoot $cleanProfile)) {
                    return @{ ok = $false; reason = 'UNVERIFIED_ACTION' }
                }
            } catch {
                return @{ ok = $false; reason = 'UNVERIFIED_ACTION' }
            }
            return @{ ok = $true; value = ('CHROME|' + $rawPath.ToLowerInvariant() + '|' + ($d5Args -join [char]31)) }
        }
        if ($rawPath -in $trustedPowerShellPaths) {
            return Get-AttestedD5PowerShellActionResult $rawPath ([string]$a.Arguments) $d5Work $aiRoot $cleanProfile $d5FileOwners
        }
        if ($replacementPowerShell) {
            # Accept only a former native Program Files PowerShell executable.
            # All existing principal, ACL, trigger, profile and script checks still apply.
            $installRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($replacementPowerShell))
            $pattern = '^' + [regex]::Escape($installRoot) + '\\7(?:\.[0-9]+){0,2}\\pwsh\.exe$'
            $legacyDir = $env:AI_COMMERCE_TASK_HISTORICAL_WORKDIR
            if ($rawPath -imatch $pattern -or
                ($legacyDir -and $rawPath -ieq [IO.Path]::Combine($legacyDir, 'powershell.exe'))) {
                return Get-AttestedD5PowerShellActionResult $replacementPowerShell ([string]$a.Arguments) $d5Work $aiRoot $cleanProfile $d5FileOwners
            }
        }
        return @{ ok = $false; reason = 'UNVERIFIED_EXE_PATH' }
    } catch { return @{ ok = $false; reason = 'UNVERIFIED' } }
}

function Get-D5Attestation($d5, [bool]$allowDisabled = $false) {
    $res = Get-D5AttestationResult $d5 $allowDisabled
    if ($res.ok) { return $res.value }
    return $null
}

function Test-AttestedD5Task($d5) {
    return $null -ne (Get-D5Attestation $d5)
}

function Get-D5Snapshot {
    $d5 = Read-Registration 'AI-Commerce-D5-CDP-AtLogon'
    if (!$d5) { return 'ABSENT' }
    $d5Xml = [string]$d5.Xml
    $d5Acl = [string]$d5.GetSecurityDescriptor(7)
    if (!$d5.Enabled) {
        if ($d5.State -eq 1 -and $d5.GetInstances(0).Count -eq 0) {
            return @('DISABLED', $d5Xml, $d5Acl) -join "`0"
        }
        return $null
    }
    $attestation = Get-D5Attestation $d5
    if ($null -ne $attestation) {
        return @('ENABLED_D5_9333', $d5Xml, $d5Acl, $attestation) -join "`0"
    }
    return $null
}
"""


_CLEAN_9444_TASK_MIGRATION = _CLEAN_9444_TASK_INSPECTION + _CLEAN_9444_D5_ATTESTATION + r"""
$key = $null
$rollback = 'DemandMigrationRollback'
$completed = 'DemandMigrationComplete'
$effectPossible = $false
try {
    $registryPath = $env:AI_COMMERCE_TASK_REGISTRY
    $key = Open-MigrationState $true
    $state = Get-MigrationState $key
    $names = if ($key) { $key.GetValueNames() }
    if ($state -eq 'UNRESOLVED' -or 'AutostartPending' -in $names -or 'LaunchPending' -in $names) {
        'UNKNOWN_EFFECT'; exit
    }
    $d5Before = Get-D5Snapshot
    if ($null -eq $d5Before) { 'TASK_MIGRATION_AMBIGUOUS'; exit }
    if (!$registered) { 'AUTOSTART_NOT_ENROLLED'; exit }
    if (!(Test-Ready $registered)) { 'TASK_MIGRATION_NOT_READY'; exit }
    if (!(Test-DemandRuntime)) { 'AUTOSTART_RUNTIME_UNAVAILABLE'; exit }
    if (Test-Registration $registered) { 'DEMAND_ALREADY_READY'; exit }
    if ($state -eq 'COMPLETE') { 'TASK_MIGRATION_REPLAY'; exit }
    if (!(Test-LegacyRegistration $registered)) { 'AUTOSTART_CONFLICT'; exit }

    $priorXml = $registered.Xml
    $priorAcl = $registered.GetSecurityDescriptor(7)
    if (!$priorXml -or !$priorAcl -or $priorXml.Length -gt 65536 -or $priorAcl.Length -gt 2048) {
        'AUTOSTART_CONFLICT'; exit
    }

    $current = Read-Registration
    $d5Check = Get-D5Snapshot
    if ($null -eq $d5Check -or $d5Check -cne $d5Before -or !$current -or !(Test-Ready $current) -or
        !(Test-LegacyRegistration $current) -or
        $current.Xml -cne $priorXml -or $current.GetSecurityDescriptor(7) -cne $priorAcl) {
        'TASK_MIGRATION_AMBIGUOUS'; exit
    }
    $effectPossible = $true
    if (!$key) { $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($registryPath) }
    $metadata = @{ Xml = $priorXml; SecurityDescriptor = $priorAcl } | ConvertTo-Json -Compress
    $key.SetValue($rollback, $metadata, [Microsoft.Win32.RegistryValueKind]::String)
    $key.Flush()
    $current = Read-Registration
    $d5Check = Get-D5Snapshot
    if ($null -eq $d5Check -or $d5Check -cne $d5Before -or !$current -or !(Test-Ready $current) -or
        $current.Xml -cne $priorXml -or $current.GetSecurityDescriptor(7) -cne $priorAcl) {
        'UNKNOWN_EFFECT'; exit
    }
    # TASK_UPDATE | TASK_DONT_ADD_PRINCIPAL_ACE | TASK_IGNORE_REGISTRATION_TRIGGERS.
    $null = $folder.RegisterTaskDefinition($name, $expected, 52, $sid, $null, 3, $priorAcl)
    $after = Read-Registration
    $d5After = Get-D5Snapshot
    if (!(Test-Registration $after) -or !(Test-Ready $after) -or
        $null -eq $d5After -or $d5After -cne $d5Before -or
        $after.GetSecurityDescriptor(7) -cne $priorAcl) { 'UNKNOWN_EFFECT'; exit }
    $key.SetValue($completed, 1, [Microsoft.Win32.RegistryValueKind]::DWord)
    $key.Flush()
    'DEMAND_MIGRATED'
} catch {
    if ($effectPossible) { 'UNKNOWN_EFFECT' } else { 'AUTOSTART_RUNTIME_UNAVAILABLE' }
} finally {
    if ($key) { $key.Dispose() }
}
"""


_CLEAN_9444_RECONCILE_INSPECTION = _CLEAN_9444_TASK_INSPECTION + _CLEAN_9444_D5_ATTESTATION + r"""
function Get-ReconcileRegistrationBaseStatus($reg, [bool]$allowOwnedAcl = $false) {
    if (!$reg) { return 'CONFLICT' }
    try {
        $acl = [Security.AccessControl.RawSecurityDescriptor]::new($reg.GetSecurityDescriptor(7))
        $want = [Security.AccessControl.RawSecurityDescriptor]::new($sddl)
        if (!$acl.Owner) { return 'SD_UNVERIFIED' }
        if ($acl.Owner.Value -cne $sid) { return 'CONFLICT_BASE_OWNER' }
        if ($null -eq $acl.DiscretionaryAcl) { return 'CONFLICT_BASE_ACL' }
        if ($acl.GetSddlForm('Access') -cne $want.GetSddlForm('Access')) {
            if (!$allowOwnedAcl -or !$acl.DiscretionaryAcl.Count) { return 'CONFLICT_BASE_ACL' }
            foreach ($ace in $acl.DiscretionaryAcl) {
                if ($ace -isnot [Security.AccessControl.CommonAce] -or
                    $ace.SecurityIdentifier.Value -cnotin @($sid, 'S-1-5-18', 'S-1-5-32-544')) {
                    return 'CONFLICT_BASE_ACL'
                }
            }
        }
    } catch {
        return 'SD_UNVERIFIED'
    }
    $definition = $reg.Definition
    if (!$definition -or !$definition.Principal -or !$definition.Actions -or !$definition.Settings) {
        return 'CONFLICT'
    }
    $principal = $definition.Principal
    if ($principal.Id -cne $expected.Principal.Id -or !(Test-Owner $principal.UserId) -or
        $principal.LogonType -ne 3 -or $principal.RunLevel -ne 0 -or $principal.GroupId) {
        return 'CONFLICT_BASE_PRINCIPAL'
    }
    if ($definition.Actions.Count -ne 1 -or $definition.Actions.Item(1).Type -ne 0 -or
        $definition.Actions.Context -cne $expected.Actions.Context) {
        return 'CONFLICT_BASE_ACTION'
    }
    foreach ($key in @('AllowDemandStart', 'AllowHardTerminate', 'DisallowStartIfOnBatteries',
        'ExecutionTimeLimit', 'Hidden', 'MultipleInstances', 'RestartCount', 'RestartInterval',
        'RunOnlyIfIdle', 'RunOnlyIfNetworkAvailable', 'StartWhenAvailable',
        'StopIfGoingOnBatteries', 'WakeToRun', 'DeleteExpiredTaskAfter')) {
        if ($definition.Settings.$key -cne $expected.Settings.$key) { return 'CONFLICT_BASE_SETTINGS' }
    }
    if ($reg.Enabled) {
        if ($definition.Settings.Enabled -cne $expected.Settings.Enabled) { return 'CONFLICT_BASE_SETTINGS' }
    } else {
        if ($null -ne $definition.Settings.Enabled -and $definition.Settings.Enabled -ne $false) {
            return 'CONFLICT_BASE_SETTINGS'
        }
    }
    return 'OK'
}

function Get-ReconcileDemandActionStatus($reg) {
    $a = $reg.Definition.Actions.Item(1)
    if (!$a) { return 'CONFLICT_BASE_ACTION' }
    if (!$a.Path -or $a.Path -ine $action.Path) {
        if ($a.Path -and (Test-TrustedPowerShell $a.Path)) {
            return 'CONFLICT_DEMAND_PWSH_PATH'
        }
        return 'CONFLICT_DEMAND_PATH'
    }
    if ($a.WorkingDirectory -ine $action.WorkingDirectory) {
        return 'CONFLICT_DEMAND_WORKDIR'
    }
    if (!$a.Arguments) {
        return 'CONFLICT_DEMAND_ARGS'
    }
    if (!$trustedPowerShellSpecs.Count) {
        return 'CONFLICT_DEMAND_PWSH'
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $argHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$a.Arguments))).Replace('-', '')
    } finally { $sha.Dispose() }
    $matched = @($trustedPowerShellSpecs | Where-Object { [string]$_.demand_sha256 -ieq $argHash })
    if ($matched.Count -eq 0) {
        return 'CONFLICT_DEMAND_ARGS'
    }
    if ($matched.Count -ne 1 -or !(Test-TrustedPowerShell ([string]$matched[0].path))) {
        return 'CONFLICT_DEMAND_PWSH'
    }
    return 'OK'
}

function Get-ReconcileLegacyStatus($reg) {
    if ($reg.Definition.Triggers.Count -ne 1) { return 'CONFLICT_TRIGGER_COUNT' }
    $t = $reg.Definition.Triggers.Item(1)
    if (!$t -or $t.Type -ne 9) { return 'CONFLICT_LEGACY_TRIGGER_TYPE' }
    if (!(Test-Owner $t.UserId)) { return 'CONFLICT_LEGACY_TRIGGER_OWNER' }
    if (!$t.Repetition -or !$t.Repetition.Interval) { return 'CONFLICT_LEGACY_TRIGGER_PT1M' }
    try {
        if ([Xml.XmlConvert]::ToTimeSpan($t.Repetition.Interval) -ne [TimeSpan]::FromMinutes(1)) {
            return 'CONFLICT_LEGACY_TRIGGER_PT1M'
        }
    } catch { return 'CONFLICT_LEGACY_TRIGGER_PT1M' }
    if (($reg.Enabled -and !$t.Enabled) -or $t.Delay -or
        $t.StartBoundary -or $t.EndBoundary -or $t.Repetition.Duration -or
        $t.Repetition.StopAtDurationEnd -or [string]$t.ExecutionTimeLimit -cnotin @('', 'PT72H')) {
        return 'CONFLICT_LEGACY_TRIGGER_CFG'
    }
    $a = $reg.Definition.Actions.Item(1)
    if (!$a) { return 'CONFLICT_BASE_ACTION' }
    if (!$a.Path -or !(Test-TrustedPowerShell $a.Path)) {
        return 'CONFLICT_LEGACY_PATH'
    }
    if (!$env:AI_COMMERCE_TASK_LEGACY_ARGUMENTS -or
        $a.Arguments -cne $env:AI_COMMERCE_TASK_LEGACY_ARGUMENTS) {
        return 'CONFLICT_LEGACY_ARGS'
    }
    if (!$a.WorkingDirectory) { return 'CONFLICT_LEGACY_WORKDIR' }
    $validWorkDir = ($a.WorkingDirectory -in $trustedPowerShellDirs) -or
        (Test-HistoricalLegacyWorkDir $a.WorkingDirectory)
    if (!$validWorkDir) { return 'CONFLICT_LEGACY_WORKDIR' }
    return 'OK'
}

function Test-ReconcileRollbackSddl($aclText) {
    if ($aclText -isnot [string] -or !$aclText -or $aclText.Length -gt 2048 -or
        $aclText.IndexOf([char]0) -ge 0 -or $aclText -cne $aclText.Trim()) {
        return 'UNVERIFIED'
    }
    try {
        $acl = [Security.AccessControl.RawSecurityDescriptor]::new($aclText)
        $want = [Security.AccessControl.RawSecurityDescriptor]::new($sddl)
    } catch {
        return 'UNVERIFIED'
    }
    if (!$acl.Owner -or $acl.Owner.Value -cne $sid -or !$acl.DiscretionaryAcl -or
        $null -ne $acl.SystemAcl -or $acl.GetSddlForm('Access') -cne $want.GetSddlForm('Access')) {
        return 'MISMATCH'
    }
    return 'VALID'
}

function Get-XmlElementChildren($el) {
    $children = [Collections.Generic.List[Xml.XmlElement]]::new()
    foreach ($node in $el.ChildNodes) {
        if ($node.NodeType -eq [Xml.XmlNodeType]::Element) {
            if ($node.NamespaceURI -cne 'http://schemas.microsoft.com/windows/2004/02/mit/task') {
                return $null
            }
            $children.Add([Xml.XmlElement]$node)
        } elseif ($node.NodeType -in @([Xml.XmlNodeType]::Whitespace, [Xml.XmlNodeType]::SignificantWhitespace, [Xml.XmlNodeType]::Comment)) {
            continue
        } else {
            return $null
        }
    }
    return ,$children
}

function Get-XmlLeafText($el) {
    if ($el.HasAttributes) { return $null }
    foreach ($node in $el.ChildNodes) {
        if ($node.NodeType -notin @([Xml.XmlNodeType]::Text, [Xml.XmlNodeType]::Whitespace, [Xml.XmlNodeType]::SignificantWhitespace)) {
            return $null
        }
    }
    return [string]$el.InnerText
}

function ConvertFrom-XmlStrictBool($text) {
    if ($text -ceq 'true') { return $true }
    if ($text -ceq 'false') { return $false }
    return $null
}

function Test-ReconcileRollbackMetadata($rbVal) {
    if ($rbVal -isnot [string] -or [string]::IsNullOrWhiteSpace($rbVal) -or
        $rbVal.Length -gt 131072 -or $rbVal.IndexOf([char]0) -ge 0) {
        return 'UNVERIFIED'
    }
    $xmlText = $null
    $aclText = $null
    $jsonDoc = $null
    try {
        $jsonOpts = [Text.Json.JsonDocumentOptions]::new()
        $jsonOpts.AllowTrailingCommas = $false
        $jsonOpts.CommentHandling = [Text.Json.JsonCommentHandling]::Disallow
        $jsonOpts.MaxDepth = 4
        $jsonDoc = [Text.Json.JsonDocument]::Parse($rbVal, $jsonOpts)
        if ($jsonDoc.RootElement.ValueKind -ne [Text.Json.JsonValueKind]::Object) {
            return 'UNVERIFIED'
        }
        $seenKeys = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($prop in $jsonDoc.RootElement.EnumerateObject()) {
            if (!$seenKeys.Add($prop.Name)) { return 'UNVERIFIED' }
            if ($prop.Name -cnotin @('Xml', 'SecurityDescriptor')) { return 'UNVERIFIED' }
            if ($prop.Value.ValueKind -ne [Text.Json.JsonValueKind]::String) { return 'UNVERIFIED' }
            if ($prop.Name -ceq 'Xml') { $xmlText = $prop.Value.GetString() }
            else { $aclText = $prop.Value.GetString() }
        }
        if ($seenKeys.Count -ne 2) { return 'UNVERIFIED' }
    } catch {
        return 'UNVERIFIED'
    } finally {
        if ($jsonDoc) { $jsonDoc.Dispose() }
    }
    if ($xmlText -isnot [string] -or !$xmlText -or $xmlText.Length -gt 65536 -or $xmlText.IndexOf([char]0) -ge 0) {
        return 'UNVERIFIED'
    }
    $aclStatus = Test-ReconcileRollbackSddl $aclText
    if ($aclStatus -cne 'VALID') { return $aclStatus }

    $sr = $null
    $reader = $null
    $xmlDoc = $null
    try {
        $xmlSettings = [Xml.XmlReaderSettings]::new()
        $xmlSettings.DtdProcessing = [Xml.DtdProcessing]::Prohibit
        $xmlSettings.XmlResolver = $null
        $xmlSettings.MaxCharactersInDocument = 65536
        $xmlSettings.MaxCharactersFromEntities = 1024
        $xmlSettings.IgnoreComments = $true
        $xmlSettings.IgnoreProcessingInstructions = $false
        $xmlSettings.IgnoreWhitespace = $true
        $sr = [IO.StringReader]::new($xmlText)
        $reader = [Xml.XmlReader]::Create($sr, $xmlSettings)
        $xmlDoc = [Xml.XmlDocument]::new()
        $xmlDoc.XmlResolver = $null
        $xmlDoc.Load($reader)
    } catch {
        return 'UNVERIFIED'
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($sr) { $sr.Dispose() }
    }

    foreach ($topNode in $xmlDoc.ChildNodes) {
        if ($topNode.NodeType -notin @([Xml.XmlNodeType]::XmlDeclaration, [Xml.XmlNodeType]::Element, [Xml.XmlNodeType]::Comment, [Xml.XmlNodeType]::Whitespace)) {
            return 'UNVERIFIED'
        }
    }
    $taskNs = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    $root = $xmlDoc.DocumentElement
    if (!$root -or $root.LocalName -cne 'Task' -or $root.NamespaceURI -cne $taskNs) {
        return 'UNVERIFIED'
    }
    $taskVer = $null
    foreach ($attr in $root.Attributes) {
        if ($attr.Name -ceq 'xmlns') { continue }
        if ($attr.Name -ceq 'version') { $taskVer = $attr.Value; continue }
        return 'UNVERIFIED'
    }
    if ($taskVer -cnotin @('1.2', '1.3', '1.4')) { return 'UNVERIFIED' }

    $rootChildren = Get-XmlElementChildren $root
    if ($null -eq $rootChildren) { return 'UNVERIFIED' }
    $sections = @{}
    foreach ($child in $rootChildren) {
        if ($child.LocalName -cnotin @('RegistrationInfo', 'Triggers', 'Principals', 'Settings', 'Actions')) {
            return 'UNVERIFIED'
        }
        if ($sections.ContainsKey($child.LocalName)) { return 'UNVERIFIED' }
        $sections[$child.LocalName] = $child
    }
    foreach ($req in @('RegistrationInfo', 'Triggers', 'Principals', 'Settings', 'Actions')) {
        if (!$sections.ContainsKey($req)) { return 'MISMATCH' }
    }

    if ($sections['RegistrationInfo'].HasAttributes) { return 'UNVERIFIED' }
    $regChildren = Get-XmlElementChildren $sections['RegistrationInfo']
    if ($null -eq $regChildren) { return 'UNVERIFIED' }
    $regSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $regUri = $null
    foreach ($rc in $regChildren) {
        if ($rc.LocalName -cnotin @('Date', 'Author', 'Version', 'Description', 'Documentation', 'URI', 'SecurityDescriptor', 'Source')) {
            return 'UNVERIFIED'
        }
        if (!$regSeen.Add($rc.LocalName)) { return 'UNVERIFIED' }
        $val = Get-XmlLeafText $rc
        if ($null -eq $val) { return 'UNVERIFIED' }
        if ($rc.LocalName -ceq 'URI') { $regUri = $val }
        elseif ($rc.LocalName -ceq 'SecurityDescriptor') {
            $regSddlStatus = Test-ReconcileRollbackSddl $val
            if ($regSddlStatus -cne 'VALID') { return $regSddlStatus }
        }
    }
    if ($regUri -cne ('\' + $name)) { return 'MISMATCH' }

    if ($sections['Principals'].HasAttributes) { return 'UNVERIFIED' }
    $principalNodes = Get-XmlElementChildren $sections['Principals']
    if ($null -eq $principalNodes) { return 'UNVERIFIED' }
    if ($principalNodes.Count -ne 1 -or $principalNodes[0].LocalName -cne 'Principal') { return 'MISMATCH' }
    $pEl = $principalNodes[0]
    $principalId = $null
    foreach ($attr in $pEl.Attributes) {
        if ($attr.Name -ceq 'id') { $principalId = $attr.Value; continue }
        return 'UNVERIFIED'
    }
    $pChildren = Get-XmlElementChildren $pEl
    if ($null -eq $pChildren) { return 'UNVERIFIED' }
    $pSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $pUserId = $null; $pLogonType = -1; $pRunLevel = 0; $pGroupId = $null
    foreach ($pc in $pChildren) {
        if ($pc.LocalName -cnotin @('UserId', 'LogonType', 'GroupId', 'DisplayName', 'RunLevel')) {
            return 'MISMATCH'
        }
        if (!$pSeen.Add($pc.LocalName)) { return 'UNVERIFIED' }
        $val = Get-XmlLeafText $pc
        if ($null -eq $val) { return 'UNVERIFIED' }
        switch -CaseSensitive ($pc.LocalName) {
            'UserId' { $pUserId = $val }
            'GroupId' { $pGroupId = $val }
            'LogonType' {
                if ($val -ceq 'InteractiveToken') { $pLogonType = 3 }
                else { $pLogonType = -1 }
            }
            'RunLevel' {
                if ($val -ceq 'LeastPrivilege') { $pRunLevel = 0 }
                elseif ($val -ceq 'HighestAvailable') { $pRunLevel = 1 }
                else { return 'UNVERIFIED' }
            }
        }
    }
    $principalObj = [pscustomobject]@{
        Id = $principalId; UserId = $pUserId; LogonType = $pLogonType; RunLevel = $pRunLevel; GroupId = $pGroupId
    }

    if ($sections['Settings'].HasAttributes) { return 'UNVERIFIED' }
    $sChildren = Get-XmlElementChildren $sections['Settings']
    if ($null -eq $sChildren) { return 'UNVERIFIED' }
    $sSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $settingsMap = @{
        AllowDemandStart = $true; AllowHardTerminate = $true; DisallowStartIfOnBatteries = $true
        Enabled = $true; ExecutionTimeLimit = 'PT72H'; Hidden = $false; MultipleInstances = 2
        RestartCount = 0; RestartInterval = $null; RunOnlyIfIdle = $false
        RunOnlyIfNetworkAvailable = $false; StartWhenAvailable = $false
        StopIfGoingOnBatteries = $true; WakeToRun = $false; DeleteExpiredTaskAfter = $null
    }
    $boolSettings = @{
        AllowStartOnDemand = 'AllowDemandStart'; AllowHardTerminate = 'AllowHardTerminate'
        DisallowStartIfOnBatteries = 'DisallowStartIfOnBatteries'; Enabled = 'Enabled'
        Hidden = 'Hidden'; RunOnlyIfIdle = 'RunOnlyIfIdle'
        RunOnlyIfNetworkAvailable = 'RunOnlyIfNetworkAvailable'; StartWhenAvailable = 'StartWhenAvailable'
        StopIfGoingOnBatteries = 'StopIfGoingOnBatteries'; WakeToRun = 'WakeToRun'
    }
    foreach ($sc in $sChildren) {
        if (!$sSeen.Add($sc.LocalName)) { return 'UNVERIFIED' }
        if ($boolSettings.ContainsKey($sc.LocalName)) {
            $val = Get-XmlLeafText $sc
            $b = ConvertFrom-XmlStrictBool $val
            if ($null -eq $b) { return 'UNVERIFIED' }
            $settingsMap[$boolSettings[$sc.LocalName]] = $b
        } elseif ($sc.LocalName -ceq 'MultipleInstancesPolicy') {
            $val = Get-XmlLeafText $sc
            $mip = switch -CaseSensitive ($val) {
                'Parallel' { 0 }; 'Queue' { 1 }; 'IgnoreNew' { 2 }; 'StopExisting' { 3 }; default { -1 }
            }
            if ($mip -lt 0) { return 'UNVERIFIED' }
            $settingsMap['MultipleInstances'] = $mip
        } elseif ($sc.LocalName -ceq 'ExecutionTimeLimit') {
            $val = Get-XmlLeafText $sc
            if ($null -eq $val) { return 'UNVERIFIED' }
            $settingsMap['ExecutionTimeLimit'] = $val
        } elseif ($sc.LocalName -ceq 'DeleteExpiredTaskAfter') {
            $val = Get-XmlLeafText $sc
            if ($null -eq $val) { return 'UNVERIFIED' }
            $settingsMap['DeleteExpiredTaskAfter'] = $val
        } elseif ($sc.LocalName -ceq 'RestartOnFailure') {
            if ($sc.HasAttributes) { return 'UNVERIFIED' }
            $rfChildren = Get-XmlElementChildren $sc
            if ($null -eq $rfChildren) { return 'UNVERIFIED' }
            $rfSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($rfc in $rfChildren) {
                if ($rfc.LocalName -cnotin @('Count', 'Interval') -or !$rfSeen.Add($rfc.LocalName)) { return 'UNVERIFIED' }
                $val = Get-XmlLeafText $rfc
                if ($null -eq $val) { return 'UNVERIFIED' }
                if ($rfc.LocalName -ceq 'Count') {
                    if ($val -notmatch '^[0-9]{1,4}$') { return 'UNVERIFIED' }
                    $settingsMap['RestartCount'] = [int]$val
                } else {
                    $settingsMap['RestartInterval'] = $val
                }
            }
        } elseif ($sc.LocalName -ceq 'IdleSettings') {
            if ($sc.HasAttributes) { return 'UNVERIFIED' }
            $idChildren = Get-XmlElementChildren $sc
            if ($null -eq $idChildren) { return 'UNVERIFIED' }
            $idSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($idc in $idChildren) {
                if ($idc.LocalName -cnotin @('Duration', 'WaitTimeout', 'StopOnIdleEnd', 'RestartOnIdle') -or !$idSeen.Add($idc.LocalName)) {
                    return 'UNVERIFIED'
                }
                if ($null -eq (Get-XmlLeafText $idc)) { return 'UNVERIFIED' }
            }
        } elseif ($sc.LocalName -ceq 'Priority') {
            $val = Get-XmlLeafText $sc
            if ($null -eq $val -or $val -notmatch '^([0-9]|10)$') { return 'UNVERIFIED' }
        } elseif ($sc.LocalName -cin @('DisallowStartOnRemoteAppSession', 'Volatile')) {
            $b = ConvertFrom-XmlStrictBool (Get-XmlLeafText $sc)
            if ($null -eq $b) { return 'UNVERIFIED' }
            if ($b) { return 'MISMATCH' }
        } elseif ($sc.LocalName -ceq 'UseUnifiedSchedulingEngine') {
            if ($null -eq (ConvertFrom-XmlStrictBool (Get-XmlLeafText $sc))) { return 'UNVERIFIED' }
        } elseif ($sc.LocalName -ceq 'Compatibility') {
            $val = Get-XmlLeafText $sc
            if ($val -cnotin @('V1', 'Vista', 'Win7', 'Win8')) { return 'UNVERIFIED' }
        } else {
            return 'UNVERIFIED'
        }
    }
    $settingsObj = [pscustomobject]$settingsMap

    if ($sections['Triggers'].HasAttributes) { return 'UNVERIFIED' }
    $triggerNodes = Get-XmlElementChildren $sections['Triggers']
    if ($null -eq $triggerNodes) { return 'UNVERIFIED' }
    if ($triggerNodes.Count -ne 1 -or $triggerNodes[0].LocalName -cne 'LogonTrigger') { return 'MISMATCH' }
    $tEl = $triggerNodes[0]
    if ($tEl.HasAttributes) { return 'UNVERIFIED' }
    $tChildren = Get-XmlElementChildren $tEl
    if ($null -eq $tChildren) { return 'UNVERIFIED' }
    $tSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $tEnabled = $true; $tUserId = $null; $tDelay = $null; $tStart = $null; $tEnd = $null; $tExecLimit = ''
    $repInterval = $null; $repDuration = $null; $repStop = $false; $hasRep = $false
    foreach ($tc in $tChildren) {
        if (!$tSeen.Add($tc.LocalName)) { return 'UNVERIFIED' }
        switch -CaseSensitive ($tc.LocalName) {
            'Enabled' {
                $b = ConvertFrom-XmlStrictBool (Get-XmlLeafText $tc)
                if ($null -eq $b) { return 'UNVERIFIED' }
                $tEnabled = $b
            }
            'UserId' { $tUserId = Get-XmlLeafText $tc; if ($null -eq $tUserId) { return 'UNVERIFIED' } }
            'Delay' { $tDelay = Get-XmlLeafText $tc; if ($null -eq $tDelay) { return 'UNVERIFIED' } }
            'StartBoundary' { $tStart = Get-XmlLeafText $tc; if ($null -eq $tStart) { return 'UNVERIFIED' } }
            'EndBoundary' { $tEnd = Get-XmlLeafText $tc; if ($null -eq $tEnd) { return 'UNVERIFIED' } }
            'ExecutionTimeLimit' { $tExecLimit = Get-XmlLeafText $tc; if ($null -eq $tExecLimit) { return 'UNVERIFIED' } }
            'Repetition' {
                if ($tc.HasAttributes) { return 'UNVERIFIED' }
                $rChildren = Get-XmlElementChildren $tc
                if ($null -eq $rChildren) { return 'UNVERIFIED' }
                $rSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
                foreach ($rc in $rChildren) {
                    if (!$rSeen.Add($rc.LocalName)) { return 'UNVERIFIED' }
                    switch -CaseSensitive ($rc.LocalName) {
                        'Interval' { $repInterval = Get-XmlLeafText $rc; if ($null -eq $repInterval) { return 'UNVERIFIED' } }
                        'Duration' { $repDuration = Get-XmlLeafText $rc; if ($null -eq $repDuration) { return 'UNVERIFIED' } }
                        'StopAtDurationEnd' {
                            $b = ConvertFrom-XmlStrictBool (Get-XmlLeafText $rc)
                            if ($null -eq $b) { return 'UNVERIFIED' }
                            $repStop = $b
                        }
                        default { return 'UNVERIFIED' }
                    }
                }
                $hasRep = $true
            }
            default { return 'UNVERIFIED' }
        }
    }
    if (!$hasRep -or !$repInterval) { return 'MISMATCH' }
    $triggerObj = [pscustomobject]@{
        Type = 9; Enabled = $tEnabled; UserId = $tUserId; Delay = $tDelay
        StartBoundary = $tStart; EndBoundary = $tEnd; ExecutionTimeLimit = $tExecLimit
        Repetition = [pscustomobject]@{ Interval = $repInterval; Duration = $repDuration; StopAtDurationEnd = $repStop }
    }
    $triggersObj = [pscustomobject]@{ Count = 1; Entry = $triggerObj }
    $triggersObj | Add-Member ScriptMethod Item { param($idx) if ($idx -ne 1) { throw 'Invalid trigger index' }; $this.Entry }

    $aContext = $null
    foreach ($attr in $sections['Actions'].Attributes) {
        if ($attr.Name -ceq 'Context') { $aContext = $attr.Value; continue }
        return 'UNVERIFIED'
    }
    $actionNodes = Get-XmlElementChildren $sections['Actions']
    if ($null -eq $actionNodes) { return 'UNVERIFIED' }
    if ($actionNodes.Count -ne 1 -or $actionNodes[0].LocalName -cne 'Exec') { return 'MISMATCH' }
    $aEl = $actionNodes[0]
    if ($aEl.HasAttributes) { return 'UNVERIFIED' }
    $aChildren = Get-XmlElementChildren $aEl
    if ($null -eq $aChildren) { return 'UNVERIFIED' }
    $aSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $aPath = $null; $aArgs = $null; $aWorkDir = $null
    foreach ($ac in $aChildren) {
        if ($ac.LocalName -cnotin @('Command', 'Arguments', 'WorkingDirectory') -or !$aSeen.Add($ac.LocalName)) {
            return 'UNVERIFIED'
        }
        $val = Get-XmlLeafText $ac
        if ($null -eq $val) { return 'UNVERIFIED' }
        switch -CaseSensitive ($ac.LocalName) {
            'Command' { $aPath = $val }
            'Arguments' { $aArgs = $val }
            'WorkingDirectory' { $aWorkDir = $val }
        }
    }
    $actionObj = [pscustomobject]@{ Type = 0; Path = $aPath; Arguments = $aArgs; WorkingDirectory = $aWorkDir }
    $actionsObj = [pscustomobject]@{ Count = 1; Context = $aContext; Entry = $actionObj }
    $actionsObj | Add-Member ScriptMethod Item { param($idx) if ($idx -ne 1) { throw 'Invalid action index' }; $this.Entry }

    $parsedTask = [pscustomobject]@{
        Enabled = [bool]$settingsObj.Enabled
        Sddl = $aclText
        Definition = [pscustomobject]@{
            Principal = $principalObj
            Settings = $settingsObj
            Actions = $actionsObj
            Triggers = $triggersObj
        }
    }
    $parsedTask | Add-Member ScriptMethod GetSecurityDescriptor { param($flags) $this.Sddl }
    if (!(Test-LegacyRegistration $parsedTask)) { return 'MISMATCH' }

    # On live Windows COM runtime, also verify non-mutating registerable task shape via ITaskDefinition.XmlText.
    $svcVar = Get-Variable -Name service -ErrorAction SilentlyContinue
    if ($svcVar -and $null -ne $svcVar.Value -and $svcVar.Value.PSObject.Methods['NewTask']) {
        try {
            $comDef = $svcVar.Value.NewTask(0)
            $comDef.XmlText = $xmlText
        } catch {
            return 'UNVERIFIED'
        }
        if ([string]$comDef.RegistrationInfo.URI -cne ('\' + $name)) { return 'MISMATCH' }
        $comTask = [pscustomobject]@{
            Enabled = [bool]$comDef.Settings.Enabled
            Sddl = $aclText
            Definition = $comDef
        }
        $comTask | Add-Member ScriptMethod GetSecurityDescriptor { param($flags) $this.Sddl }
        if (!(Test-LegacyRegistration $comTask)) { return 'MISMATCH' }
    }
    return 'VALID'
}

function Get-ReconcileJournalState($backup = $null) {
    $key = $null
    try {
        $key = Open-MigrationState $false
        if (!$key) {
            if ($backup) { $backup.Value = 'JOURNAL_ABSENT' }
            return 'FRESH'
        }
        if ($key.PSObject.Properties['SubKeyCount'] -and $key.SubKeyCount -gt 0) {
            return 'UNVERIFIED'
        }
        $names = @($key.GetValueNames())
        $knownNames = @('AutostartPending', 'LaunchPending', 'DemandMigrationRollback', 'DemandMigrationComplete')
        foreach ($n in $names) {
            if ($n -isnot [string] -or $n -cnotin $knownNames) {
                return 'MISMATCH'
            }
        }
        $hasRollback = 'DemandMigrationRollback' -cin $names
        $hasComplete = 'DemandMigrationComplete' -cin $names
        if (!$hasRollback -and !$hasComplete) {
            if ($backup) { $backup.Value = 'JOURNAL_ABSENT' }
            return 'FRESH'
        }
        if (!$hasRollback -and $hasComplete) { return 'COMPLETE_ONLY' }
        $completeKindValid = $true
        if ($key.PSObject.Methods['GetValueKind']) {
            if ($key.GetValueKind('DemandMigrationRollback') -ne [Microsoft.Win32.RegistryValueKind]::String) {
                return 'UNVERIFIED'
            }
            if ($hasComplete -and $key.GetValueKind('DemandMigrationComplete') -ne [Microsoft.Win32.RegistryValueKind]::DWord) {
                if (!$backup) { return 'UNVERIFIED' }
                $completeKindValid = $false
            }
        }
        $rbVal = $key.GetValue('DemandMigrationRollback')
        $rbStatus = Test-ReconcileRollbackMetadata $rbVal
        # The journal stores XML and an ACL without an attempt identity.
        if ($backup -and $rbStatus -ceq 'VALID') { $backup.Value = 'VALID_BUT_UNBOUND' }
        if (!$completeKindValid) { return 'UNVERIFIED' }
        if ($rbStatus -cne 'VALID') { return $rbStatus }
        if (!$hasComplete) { return 'ROLLBACK_ONLY' }
        $cmVal = $key.GetValue('DemandMigrationComplete')
        if ($cmVal -isnot [int] -or $cmVal -ne 1) {
            return 'UNVERIFIED'
        }
        return 'COMPLETE'
    } catch { return 'UNVERIFIED' }
    finally { if ($key) { $key.Dispose() } }
}

function Get-ReconcileRelevantTasks {
    $all = [Collections.Generic.List[object]]::new()
    foreach ($t in @($folder.GetTasks(1))) {
        if ($t) { $all.Add([pscustomobject]@{ Task = $t; Root = $true }) }
    }
    foreach ($sub in @($folder.GetFolders(0))) {
        if ($sub -and $sub.Name -and ([string]$sub.Name) -match '(?i)(AI-Commerce|WakeHub|D5)') {
            foreach ($t in @($sub.GetTasks(1))) {
                if ($t) { $all.Add([pscustomobject]@{ Task = $t; Root = $false }) }
            }
        }
    }
    return ,$all.ToArray()
}
"""


_CLEAN_9444_TASK_RECONCILE_SCRIPT = _CLEAN_9444_RECONCILE_INSPECTION + r"""
$taskState = 'UNAVAILABLE'
$d5State = 'UNVERIFIED'
$backupState = 'NO_TRUSTED_1270_CUSTODY'
$journalState = if ($readRollbackCustody) {
    Get-ReconcileJournalState ([ref]$backupState)
} else {
    Get-ReconcileJournalState
}

try {
    $allTasks = Get-ReconcileRelevantTasks
    $wakeCandidates = [Collections.Generic.List[object]]::new()
    $d5Candidates = [Collections.Generic.List[object]]::new()
    foreach ($item in $allTasks) {
        $tName = [string]$item.Task.Name
        if ($tName -match '(?i)(WakeHub.*9444|9444.*WakeHub|AI-Commerce-WakeHub)') {
            $wakeCandidates.Add($item)
        } elseif ($tName -match '(?i)(AI-Commerce-D5-CDP|D5-CDP-AtLogon)') {
            $d5Candidates.Add($item)
        } elseif ($tName -like 'AI-Commerce-*') {
            try {
                $otherXml = [string]$item.Task.Xml
                $mentionsClean = $otherXml -match '(?i)WakeHub-9444-Clean' -or
                    $otherXml.IndexOf($name, [StringComparison]::OrdinalIgnoreCase) -ge 0
                $claims9444 = $false
                foreach ($act in @($item.Task.Definition.Actions)) {
                    if ($act -and $act.Type -eq 0 -and
                        ([string]$act.Arguments -match '(?i)(--remote-debugging-port\s*[=:]\s*9444|WakeHub-9444-Clean)')) {
                        $claims9444 = $true
                        break
                    }
                }
                if ($mentionsClean -or $claims9444) {
                    $wakeCandidates.Add($item)
                }
            } catch {
                $wakeCandidates.Add($item)
            }
        }
    }

    if ($wakeCandidates.Count -gt 1 -or ($registered -and $wakeCandidates.Count -ne 1)) {
        $taskState = 'MULTIPLE_CANDIDATES'
    } elseif ($wakeCandidates.Count -eq 1 -and (!$wakeCandidates[0].Root -or $wakeCandidates[0].Task.Name -cne $name)) {
        $taskState = 'FOREIGN_CANDIDATE'
    } elseif (!$registered) {
        $taskState = 'ABSENT'
    } else {
        $baseStatus = Get-ReconcileRegistrationBaseStatus $registered
        if ($baseStatus -cne 'OK') {
            $taskState = $baseStatus
        } elseif ($registered.Definition.Triggers.Count -eq 0) {
            $demandStatus = Get-ReconcileDemandActionStatus $registered
            if ($demandStatus -cne 'OK') {
                $taskState = $demandStatus
            } elseif (!$registered.Enabled) {
                if ($registered.State -eq 1 -and $registered.GetInstances(0).Count -eq 0) {
                    $taskState = 'DEMAND_DISABLED'
                } else {
                    $taskState = 'DEMAND_NOT_READY'
                }
            } elseif (!(Test-DemandRuntime)) {
                $taskState = 'DEMAND_RUNTIME_BAD'
            } elseif (!(Test-Ready $registered)) {
                $taskState = 'DEMAND_NOT_READY'
            } else {
                $taskState = 'DEMAND_READY'
            }
        } elseif ($registered.Definition.Triggers.Count -eq 1) {
            $legacyStatus = Get-ReconcileLegacyStatus $registered
            if ($legacyStatus -cne 'OK') {
                $taskState = $legacyStatus
            } elseif (!$registered.Enabled) {
                if ($registered.State -eq 1 -and $registered.GetInstances(0).Count -eq 0) {
                    $taskState = 'LEGACY_PT1M_DISABLED'
                } else {
                    $taskState = 'LEGACY_PT1M_NOT_READY'
                }
            } elseif (!(Test-Ready $registered)) {
                $taskState = 'LEGACY_PT1M_NOT_READY'
            } else {
                $taskState = 'LEGACY_PT1M_READY'
            }
        } else {
            $taskState = 'CONFLICT_TRIGGER_COUNT'
        }
    }

    $d5Task = Read-Registration 'AI-Commerce-D5-CDP-AtLogon'
    if ($d5Candidates.Count -gt 1 -or ($d5Task -and $d5Candidates.Count -ne 1)) {
        $d5State = 'UNVERIFIED_MULTIPLE'
    } elseif ($d5Candidates.Count -eq 1 -and (!$d5Candidates[0].Root -or $d5Candidates[0].Task.Name -cne 'AI-Commerce-D5-CDP-AtLogon')) {
        $d5State = 'UNVERIFIED_MOVED'
    } elseif (!$d5Task) {
        $d5State = 'ABSENT'
    } elseif ($d5Task.Enabled) {
        $d5Res = Get-D5AttestationResult $d5Task $false
        if ($d5Res.ok) { $d5State = 'ENABLED_ATTESTED' }
        else { $d5State = $d5Res.reason }
    } else {
        if ($d5Task.State -ne 1 -or $d5Task.GetInstances(0).Count -ne 0) {
            $d5State = 'UNVERIFIED_STATE'
        } else {
            $d5Res = Get-D5AttestationResult $d5Task $true
            if ($d5Res.ok) { $d5State = 'DISABLED_ATTESTED' }
            else { $d5State = $d5Res.reason }
        }
    }
} catch {
    $taskState = 'UNAVAILABLE'
    $d5State = 'UNVERIFIED'
}

$afterSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$afterSession = [Diagnostics.Process]::GetCurrentProcess().SessionId
if ($afterSid -cne $sid -or $afterSession -le 0 -or $afterSession -ne $session) {
    @{ error = 'WINDOWS_OWNER_CONTEXT_UNVERIFIED' } | ConvertTo-Json -Compress
    exit
}

$receipt = @{ task = $taskState; journal = $journalState; d5 = $d5State }
if ($readRollbackCustody) { $receipt['backup'] = $backupState }
$receipt | ConvertTo-Json -Compress
"""

_CLEAN_9444_FORENSIC_READ_SCRIPT = "$readRollbackCustody = $true\n" + _CLEAN_9444_TASK_RECONCILE_SCRIPT


_CLEAN_9444_TASK_RESET = _CLEAN_9444_TASK_INSPECTION + r"""
function Test-ResetIdle($task) {
    $idleState = if ($task.Enabled) { 3 } else { 1 }
    if ($task.Enabled -isnot [bool] -or $task.State -ne $idleState) {
        return $false
    }
    $instances = $task.GetInstances(0)
    return $null -ne $instances -and $instances.Count -is [int] -and $instances.Count -eq 0
}

function Get-ResetImage($task) {
    if ($task.Name -cne $name -or $task.Path -cne ('\' + $name)) {
        throw 'AUTOSTART_CONFLICT'
    }
    $xml = [string]$task.Xml
    $acl = [string]$task.GetSecurityDescriptor(7)
    if (!$xml -or $xml.Length -gt 65536 -or !$acl -or $acl.Length -gt 2048) {
        throw 'AUTOSTART_CONFLICT'
    }
    try { $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new($acl) }
    catch { throw 'AUTOSTART_CONFLICT' }
    # The old DACL and action can drift. Verify custody before preserving the image.
    if (!$descriptor.Owner -or $descriptor.Owner.Value -cne $sid -or
        !(Test-Owner $task.Definition.Principal.UserId)) {
        throw 'AUTOSTART_CONFLICT'
    }
    ConvertTo-Json -Compress -InputObject ([ordered]@{ Xml = $xml; SecurityDescriptor = $acl })
}

function Get-ResetSnapshot($task) {
    if ($folder.Path -cne '\') { throw 'AUTOSTART_CONFLICT' }
    $image = if ($task) { Get-ResetImage $task }
    $matched = $false
    $folders = [Collections.Generic.Stack[object]]::new()
    $folders.Push($folder)
    while ($folders.Count) {
        $currentFolder = $folders.Pop()
        foreach ($candidate in @($currentFolder.GetTasks(1))) {
            if (!$candidate) { continue }
            $candidateName = [string]$candidate.Name
            if (!$candidateName) { throw 'AUTOSTART_CONFLICT' }
            if ($candidateName -match '(?i)(WakeHub.*9444|9444.*WakeHub|AI-Commerce-WakeHub)') {
                if ($matched -or !$task -or $currentFolder.Path -cne '\' -or
                    (Get-ResetImage $candidate) -cne $image) { throw 'AUTOSTART_CONFLICT' }
                $matched = $true
            } elseif ($candidateName -like 'AI-Commerce-*' -and $candidateName -ine 'AI-Commerce-D5-CDP-AtLogon') {
                $otherXml = [string]$candidate.Xml
                if (!$otherXml -or $otherXml -match '(?i)(WakeHub-9444-Clean|--remote-debugging-port\s*[=:]\s*9444)') {
                    throw 'AUTOSTART_CONFLICT'
                }
            }
        }
        foreach ($sub in @($currentFolder.GetFolders(0))) {
            if ($sub) { $folders.Push($sub) }
        }
    }
    if ($task -and !$matched) { throw 'AUTOSTART_CONFLICT' }
    return $image
}

function Test-ResetOwnerContext {
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ceq $sid -and
        [Diagnostics.Process]::GetCurrentProcess().SessionId -eq $session
}

function Get-ResetAutostartMarker($state) {
    if (!$state) { return $false }
    $markers = $state.GetValueNames()
    if ('LaunchPending' -in $markers) { throw 'UNKNOWN_EFFECT' }
    if ('AutostartPending' -notin $markers) { return $false }
    if ($state.GetValueKind('AutostartPending') -ne [Microsoft.Win32.RegistryValueKind]::DWord -or
        $state.GetValue('AutostartPending') -ne 1) { throw 'UNKNOWN_EFFECT' }
    return $true
}

function Read-ResetAutostartMarker {
    $state = Open-MigrationState $false
    try { Get-ResetAutostartMarker $state }
    finally { if ($state) { $state.Dispose() } }
}

$key = $null
$prior = $null
$effectPossible = $false
$resetKey = $env:AI_COMMERCE_TASK_REGISTRY + '-TaskReset'
try {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($resetKey, $true)
    if ($key -and ($key.GetValue('Complete') -ne 1 -or !$key.GetValue('Backup') -or
        $key.GetValueKind('Complete') -ne [Microsoft.Win32.RegistryValueKind]::DWord -or
        $key.GetValueKind('Backup') -ne [Microsoft.Win32.RegistryValueKind]::String)) {
        'UNKNOWN_EFFECT'; exit
    }
    if (!(Test-ResetOwnerContext)) { 'WINDOWS_OWNER_CONTEXT_UNVERIFIED'; exit }
    if (!(Test-DemandRuntime) -or !(Test-TrustedPowerShell $env:AI_COMMERCE_TASK_POWERSHELL)) {
        'AUTOSTART_RUNTIME_UNAVAILABLE'; exit
    }
    $hadAutostart = Read-ResetAutostartMarker
    $wakeBefore = Get-ResetSnapshot $registered
    if (!$registered -or !(Test-ResetIdle $registered)) { 'TASK_RESET_NOT_READY'; exit }
    $wakeReady = Test-Registration $registered
    if ($wakeReady -and !$hadAutostart) {
        $current = Read-Registration
        if ((Get-ResetSnapshot $current) -cne $wakeBefore -or !(Test-Registration $current) -or
            !(Test-ResetIdle $current) -or
            (Read-ResetAutostartMarker) -or !(Test-ResetOwnerContext)) {
            'TASK_RESET_DRIFT'; exit
        }
        'TASK_RESET_ALREADY_READY'; exit
    }
    if ($key) { 'TASK_RESET_DRIFT'; exit }

    $effectPossible = $true
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($resetKey)
    if ($key.GetValueNames().Count -ne 0) { 'UNKNOWN_EFFECT'; exit }
    $backup = @{ WakeHub = $wakeBefore; AutostartPending = $hadAutostart } | ConvertTo-Json -Compress
    $key.SetValue('Backup', $backup, [Microsoft.Win32.RegistryValueKind]::String)
    $key.Flush()
    $current = Read-Registration
    if ($key.GetValueKind('Backup') -ne [Microsoft.Win32.RegistryValueKind]::String -or
        $key.GetValue('Backup') -cne $backup -or (Read-ResetAutostartMarker) -ne $hadAutostart -or
        (Get-ResetSnapshot $current) -cne $wakeBefore -or !(Test-ResetIdle $current) -or
        !(Test-ResetOwnerContext)) {
        'UNKNOWN_EFFECT'; exit
    }
    if (!$wakeReady) {
        $folder.DeleteTask($name, 0)
        if ((Read-Registration) -or !(Test-ResetOwnerContext) -or
            (Read-ResetAutostartMarker) -ne $hadAutostart) { 'UNKNOWN_EFFECT'; exit }
        # TASK_CREATE | TASK_IGNORE_REGISTRATION_TRIGGERS.
        $null = $folder.RegisterTaskDefinition($name, $expected, 34, $sid, $null, 3, $sddl)
    }
    $after = Read-Registration
    if (!(Get-ResetSnapshot $after) -or !(Test-Registration $after) -or !(Test-ResetIdle $after) -or
        !(Test-ResetOwnerContext)) { 'UNKNOWN_EFFECT'; exit }

    # Retire the stale install marker only after readback and private backup.
    # The original demand migration journal and Issue #1270 are historical evidence.
    $prior = Open-MigrationState $hadAutostart
    if ((Get-ResetAutostartMarker $prior) -ne $hadAutostart) { 'UNKNOWN_EFFECT'; exit }
    if ($hadAutostart) {
        $prior.DeleteValue('AutostartPending')
        $prior.Flush()
    }
    if ((Read-ResetAutostartMarker) -or !(Test-ResetOwnerContext)) { 'UNKNOWN_EFFECT'; exit }
    $key.SetValue('Complete', 1, [Microsoft.Win32.RegistryValueKind]::DWord)
    $key.Flush()
    if ($wakeReady) { 'TASK_RESET_ALREADY_READY' } else { 'TASK_RESET_VERIFIED' }
} catch {
    if ($effectPossible -or $_.Exception.Message -ceq 'UNKNOWN_EFFECT') { 'UNKNOWN_EFFECT' }
    elseif ($_.Exception.Message -ceq 'AUTOSTART_CONFLICT') { 'AUTOSTART_CONFLICT' }
    else { 'AUTOSTART_RUNTIME_UNAVAILABLE' }
} finally {
    if ($prior) { $prior.Dispose() }
    if ($key) { $key.Dispose() }
}
"""


def _clean_task_environment(owner: tuple[Path, int, str], target: _ChromeTarget,
                            candidates: tuple[Path, ...], powershell: Path, *,
                            trusted: tuple[Path, ...] | None = None,
                            include_legacy: bool = False) -> dict[str, str]:
    _, session, sid = owner
    launcher = _clean_launcher(owner, target, candidates)
    gui, arguments = _clean_demand_action(powershell, launcher)
    historical_workdir = _historical_legacy_workdir() if include_legacy else None
    child_env = _child_environment()
    child_env.update(
        AI_COMMERCE_TASK_SID=sid,
        AI_COMMERCE_TASK_SESSION=str(session),
        AI_COMMERCE_TASK_POWERSHELL=str(powershell),
        AI_COMMERCE_TASK_GUI=str(gui),
        AI_COMMERCE_TASK_ARGUMENTS=arguments,
        AI_COMMERCE_TASK_REGISTRY=_CLEAN_9444_REGISTRY,
        AI_COMMERCE_TASK_PROFILE_DIR=str(target.user_data_dir),
        AI_COMMERCE_TASK_CHROME_EXECUTABLES=json.dumps([str(path) for path in candidates]),
        AI_COMMERCE_TASK_HISTORICAL_WORKDIR=str(historical_workdir) if historical_workdir else "",
        AI_COMMERCE_TASK_LEGACY_ARGUMENTS=(
            subprocess.list2cmdline([*_CLEAN_POWERSHELL_FLAGS, launcher]) if include_legacy else ""
        ),
    )
    if trusted is not None:
        specs = []
        for shell in trusted:
            _, shell_args = _clean_demand_action(shell, launcher)
            specs.append({
                "path": str(shell),
                "dir": str(shell.parent),
                "demand_sha256": hashlib.sha256(shell_args.encode("utf-8")).hexdigest(),
            })
        child_env["AI_COMMERCE_TASK_TRUSTED_POWERSHELLS"] = json.dumps(specs)
    return child_env


def _clean_task_powershell(bootstrap: Path) -> Path:
    """Read the action only after verifying the same registration used to run 9444."""
    owner = _native_operator()
    target, candidates = _clean_target(owner)
    trusted = _trusted_powershells()
    result = subprocess.run(
        [str(bootstrap), "-NoProfile", "-NonInteractive", "-Command", _CLEAN_9444_TASK_IMAGE_SCRIPT],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=False,
        env=_clean_task_environment(owner, target, candidates, bootstrap,
                                    trusted=trusted, include_legacy=True),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or len(result.stdout) > 4096:
        raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
    return Path(result.stdout.decode("utf-8").rstrip("\r\n"))


_CLEAN_RECONCILE_TASK_VALUES = frozenset({
    "DEMAND_READY", "DEMAND_DISABLED", "DEMAND_NOT_READY", "DEMAND_RUNTIME_BAD",
    "LEGACY_PT1M_READY", "LEGACY_PT1M_DISABLED", "LEGACY_PT1M_NOT_READY",
    "ABSENT", "MULTIPLE_CANDIDATES", "FOREIGN_CANDIDATE", "CONFLICT",
    "CONFLICT_BASE_OWNER", "CONFLICT_BASE_ACL", "SD_UNVERIFIED", "CONFLICT_BASE_PRINCIPAL",
    "CONFLICT_BASE_ACTION", "CONFLICT_BASE_SETTINGS", "CONFLICT_TRIGGER_COUNT",
    "CONFLICT_DEMAND_PATH", "CONFLICT_DEMAND_PWSH_PATH", "CONFLICT_DEMAND_WORKDIR",
    "CONFLICT_DEMAND_ARGS", "CONFLICT_DEMAND_PWSH",
    "CONFLICT_LEGACY_TRIGGER_TYPE", "CONFLICT_LEGACY_TRIGGER_OWNER",
    "CONFLICT_LEGACY_TRIGGER_PT1M", "CONFLICT_LEGACY_TRIGGER_CFG",
    "CONFLICT_LEGACY_PATH", "CONFLICT_LEGACY_ARGS", "CONFLICT_LEGACY_WORKDIR",
    "UNAVAILABLE",
})
_CLEAN_RECONCILE_JOURNAL_VALUES = frozenset({
    "FRESH", "COMPLETE", "MISMATCH", "ROLLBACK_ONLY", "COMPLETE_ONLY", "UNVERIFIED",
})
_CLEAN_RECONCILE_BACKUP_VALUES = frozenset({
    "NO_TRUSTED_1270_CUSTODY", "JOURNAL_ABSENT", "VALID_BUT_UNBOUND",
})
_CLEAN_RECONCILE_D5_VALUES = frozenset({
    "ENABLED_ATTESTED", "DISABLED_ATTESTED", "ABSENT", "UNVERIFIED",
    "UNVERIFIED_MULTIPLE", "UNVERIFIED_MOVED", "UNVERIFIED_STATE",
    "UNVERIFIED_BASE", "UNVERIFIED_ACL", "UNVERIFIED_PRINCIPAL",
    "UNVERIFIED_TRIGGER", "UNVERIFIED_ACTION", "UNVERIFIED_EXE", "UNVERIFIED_EXE_PATH",
    "UNVERIFIED_CUSTODY", "UNVERIFIED_SCRIPT",
})


def _clean_reconcile_unique_object(pairs):
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError("DUPLICATE_RECONCILE_KEY")
    return data


def _clean_reconcile_read(owner: tuple[Path, int, str], target: _ChromeTarget,
                          candidates: tuple[Path, ...], *, forensic: bool = False) -> dict[str, str]:
    """Read-only post-UNKNOWN reconciler for WakeHub 9444 task, journal and D5."""
    trusted = _trusted_powershells()
    if len(trusted) == 1:
        powershell = trusted[0]
    else:
        try:
            powershell = _powershell()
            if powershell not in trusted:
                powershell = trusted[0]
        except AcquisitionBlocked:
            powershell = trusted[0]
    child_env = _clean_task_environment(owner, target, candidates, powershell,
                                        trusted=trusted, include_legacy=True)
    stdin_runner = (
        "[Console]::InputEncoding=[Text.UTF8Encoding]::new(); "
        "& ([ScriptBlock]::Create([Console]::In.ReadToEnd()))"
    )
    fallback = {"task": "UNAVAILABLE", "journal": "UNVERIFIED", "d5": "UNVERIFIED"}
    fields = {
        "task": _CLEAN_RECONCILE_TASK_VALUES,
        "journal": _CLEAN_RECONCILE_JOURNAL_VALUES,
        "d5": _CLEAN_RECONCILE_D5_VALUES,
    }
    script = _CLEAN_9444_TASK_RECONCILE_SCRIPT
    if forensic:
        script = _CLEAN_9444_FORENSIC_READ_SCRIPT
        fields["backup"] = _CLEAN_RECONCILE_BACKUP_VALUES
        fallback["backup"] = "NO_TRUSTED_1270_CUSTODY"
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", stdin_runner],
            input=script.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=25, check=False, env=child_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or not 0 < len(result.stdout) <= 512:
            return fallback
        raw = result.stdout.decode("utf-8").strip()
        if raw == "WINDOWS_OWNER_CONTEXT_UNVERIFIED":
            return {"error": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"}
        data = json.loads(raw, object_pairs_hook=_clean_reconcile_unique_object)
        if type(data) is not dict:
            return fallback
        if data == {"error": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"}:
            return {"error": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"}
        if (data.keys() == fields.keys()
                and all(type(data[key]) is str and data[key] in values for key, values in fields.items())):
            return data
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
        pass
    return fallback


def _clean_task(owner: tuple[Path, int, str], target: _ChromeTarget,
                candidates: tuple[Path, ...], *, install: bool, start: bool = False,
                migrate: bool = False, reset: bool = False) -> str:
    """Operate on the fixed owner task using an exact native definition."""
    if sum((install, start, migrate, reset)) > 1:
        raise _Clean9444Hold("AUTOSTART_RUNTIME_UNAVAILABLE")
    if reset:
        trusted = _trusted_powershells()
        # The old registration cannot select the executable for its own reset.
        versioned = [shell for shell in trusted if re.fullmatch(r"7\.7(?:\.[0-9]+)?", shell.parent.name)]
        stable = [shell for shell in trusted if shell.parent.name == "7"]
        selected = versioned or stable or trusted
        if len(selected) != 1:
            raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
        powershell = selected[0]
    elif migrate:
        trusted = _trusted_powershells()
        powershell = _powershell()
        if powershell not in trusted:
            raise AcquisitionBlocked("POWERSHELL_UNAVAILABLE")
    else:
        powershell = _powershell()
        trusted = None
    child_env = _clean_task_environment(owner, target, candidates, powershell,
                                        trusted=trusted, include_legacy=migrate)
    child_env.update(AI_COMMERCE_TASK_START_SESSION=str(owner[1]) if start else "",
                     AI_COMMERCE_TASK_INSTALL="1" if install else "0")
    if reset:
        script = _CLEAN_9444_TASK_RESET
    elif migrate:
        script = _CLEAN_9444_TASK_MIGRATION
    else:
        script = _CLEAN_9444_TASK_SCRIPT if install or start else _CLEAN_9444_TASK_READ_SCRIPT
    try:
        if migrate or reset:
            stdin_runner = (
                "[Console]::InputEncoding=[Text.UTF8Encoding]::new(); "
                "& ([ScriptBlock]::Create([Console]::In.ReadToEnd()))"
            )
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", stdin_runner],
                input=script.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=25, check=False, env=child_env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                                     script], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=15, check=False, env=child_env,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        code = result.stdout.strip().decode("ascii")
        expected = {"AUTOSTART_CONFLICT", "UNKNOWN_EFFECT", "WINDOWS_OWNER_CONTEXT_UNVERIFIED",
                    "AUTOSTART_RUNTIME_UNAVAILABLE"}
        if reset:
            expected.update(("TASK_RESET_VERIFIED", "TASK_RESET_ALREADY_READY", "TASK_RESET_D5_UNVERIFIED",
                             "TASK_RESET_NOT_READY", "TASK_RESET_DRIFT"))
        elif migrate:
            expected.update(("DEMAND_MIGRATED", "DEMAND_ALREADY_READY", "AUTOSTART_NOT_ENROLLED",
                             "TASK_MIGRATION_NOT_READY", "TASK_MIGRATION_AMBIGUOUS",
                             "TASK_MIGRATION_REPLAY"))
        elif start:
            expected.update(("START_REQUESTED", "START_NOT_ATTEMPTED"))
        else:
            expected.update(("ALREADY_ENROLLED", "AUTOSTART_NOT_ENROLLED"))
            if install:
                expected.add("ENROLLED")
        if not result.returncode and code in expected:
            return code
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    return "UNKNOWN_EFFECT" if install or start or migrate or reset else "AUTOSTART_RUNTIME_UNAVAILABLE"


def install_clean_9444_autostart(*, opt_in: bool = False) -> dict:
    """Enroll the fixed task from an attended, non-elevated owner session."""
    if opt_in is not True:
        return _clean_hold("OPT_IN_REQUIRED")
    try:
        owner = _clean_operator()
        with _clean_supervisor_slot():
            target, candidates = _clean_target(owner)
            if _clean_pending(_CLEAN_TASK_PENDING) or _clean_launch_pending():
                return _clean_hold("UNKNOWN_EFFECT")
            state, _ = _clean_inventory(target, candidates, owner[1], owner[2])
            if state != "ABSENT":
                return _clean_hold("PROFILE_IN_USE")
            _clean_require_unlocked(target)
            _verify_google_chrome(target)
            if _clean_operator() != owner:
                return _clean_hold("WINDOWS_OWNER_CONTEXT_UNVERIFIED")
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
        code = str(exc)
        # The attended PowerShell repair decoder uses the legacy probe catalog.
        if code in _ATTENDED_CLI_INVENTORY_HOLDS:
            code = "WINDOWS_PROBE_UNAVAILABLE"
        print("CHROME_ACQUISITION_BLOCKED: " + code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
