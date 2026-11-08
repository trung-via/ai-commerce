"""OS identity/ACL admission. No chmod, ACL mutation, UAC or privilege acquisition."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import time

from .contract import CONTROL_REPOSITORY, SOURCE_REPOSITORY, decode, deny, digest, ident, sha, shape, text


def admit_work_owner_ledger_path(path, *, private_root=None):
    """Fail before SQLite opens any native pathname.

    Python's SQLite VFS opens the database and its journal/WAL/SHM by name.
    A leaf lstat, SID match or directory ACL check cannot bind those opens to
    inspected handles. No reviewed native VFS/custody policy exists here.
    This gate deliberately performs NO open, mkdir, ACL change or link walk.
    Even an apparently owned new path must HOLD until that gap is closed.
    """
    deny("SOURCE_ONLY_HOLD_LEDGER_CUSTODY_UNPROVEN")


def prefer_readable_pwsh_path():
    """Resolve pwsh only when explicitly called; never modify process PATH.

    A WindowsApps alias may execute PLAN-only tests even when it cannot be
    byte-read. V4 broker admission separately requires a pinned readable binary.
    """
    fallback = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if os.name != "nt":
        return fallback
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / "pwsh.exe"
        try:
            if candidate.is_file():
                with candidate.open("rb") as handle:
                    if handle.read(4):
                        return str(candidate)
        except OSError:
            continue
    return fallback


def windows_identity(process=None):
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    adv.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(process or kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        deny("TOKEN_UNAVAILABLE")
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    adv.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    try:
        length = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        buf = ctypes.create_string_buffer(length.value)
        if not adv.GetTokenInformation(token, 1, buf, length, ctypes.byref(length)):
            deny("TOKEN_UNAVAILABLE")
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        string = wintypes.LPWSTR()
        adv.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(string)):
            deny("TOKEN_UNAVAILABLE")
        try:
            result = string.value
        finally:
            kernel.LocalFree.argtypes = (ctypes.c_void_p,)
            kernel.LocalFree(string)
        elevation = wintypes.DWORD()
        if not adv.GetTokenInformation(token, 20, ctypes.byref(elevation), 4, ctypes.byref(length)):
            deny("TOKEN_UNAVAILABLE")
        return result, bool(elevation.value)
    finally:
        kernel.CloseHandle(token)


def windows_acl(path, trusted, *, private_readers=None):
    """Reject NULL DACL, writable foreign owners and foreign write allow ACEs.

    ACLs with unknown allow-ACE formats fail closed. This is conservative and may
    require the Human installer to simplify ACLs using their existing authority.
    """
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p))
    owner, acl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    error = adv.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                    ctypes.byref(acl), None, ctypes.byref(descriptor))
    if error:
        deny("ACL_UNAVAILABLE")
    def sid_string(sid):
        out = wintypes.LPWSTR()
        adv.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(out)):
            deny("ACL_UNAVAILABLE")
        try:
            return out.value
        finally:
            kernel.LocalFree(out)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    try:
        if not acl.value or sid_string(owner) not in trusted:
            deny("ACL_UNSAFE")
        # ACL header: revision/pad/size/ace-count/pad.
        count = ctypes.c_ushort.from_address(acl.value + 4).value
        adv.GetAce.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
        for index in range(count):
            ace = ctypes.c_void_p()
            if not adv.GetAce(acl, index, ctypes.byref(ace)):
                deny("ACL_UNAVAILABLE")
            kind, flags = (ctypes.c_ubyte * 2).from_address(ace.value)
            if flags & 8:  # INHERIT_ONLY_ACE does not grant this object access.
                continue
            if kind == 1:  # ACCESS_DENIED_ACE; ignoring denial only makes admission stricter.
                continue
            if kind != 0:
                deny("ACL_COMPLEX_UNSUPPORTED")
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            sid = sid_string(ace.value + 8)
            if mask & 0x500D0156 and sid not in trusted:  # generic write/all, data/append/delete/ACL/owner
                deny("ACL_UNSAFE")
            if private_readers is not None and mask & 0xA0020089 and sid not in private_readers:
                deny("ACL_SECRET_EXPOSED")
    finally:
        kernel.LocalFree(descriptor)


CONFIG_KEYS = ("schema enabled host_id policy_dir state_dir transport_state_dir release_dir broker_identity transport_identity "
               "untrusted_identities trusted_owners endpoint ssh_keygen ssh_keygen_sha256 pwsh pwsh_sha256")


def load_config(path):
    config = decode(Path(path).read_bytes())
    shape(config, CONFIG_KEYS)
    if config["schema"] != "hostops.config.v3" or type(config["enabled"]) is not bool:
        deny("BAD_CONFIG")
    from .contract import ident, sha, text
    ident(config["host_id"])
    for key in ("policy_dir", "state_dir", "transport_state_dir", "release_dir", "ssh_keygen", "pwsh"):
        text(config[key])
        if not Path(config[key]).is_absolute():
            deny("BAD_CONFIG")
    text(config["endpoint"])
    for key in ("ssh_keygen_sha256", "pwsh_sha256"):
        sha(config[key])
    for key in ("untrusted_identities", "trusted_owners"):
        if type(config[key]) is not list or not config[key] or len(config[key]) > 32:
            deny("BAD_CONFIG")
        for item in config[key]:
            text(item, 256)
    if (config["broker_identity"] == config["transport_identity"] or
            config["broker_identity"] in config["untrusted_identities"] or
            config["transport_identity"] in config["untrusted_identities"] or
            set(config["trusted_owners"]) & set(config["untrusted_identities"])):
        deny("IDENTITIES_NOT_ISOLATED")
    return config


def _bounded_install_paths(root, *, max_entries=1024, max_depth=12,
                           max_seconds=2.0):
    """Enumerate approved tree with hard node/depth/time bounds; never follow links.

    Results include the root. Unknown permissions, reparse points, and limit
    overruns fail closed rather than trusting partial installed-state evidence.
    """
    deadline = time.monotonic() + max_seconds
    pending = [(Path(root), 0)]
    scheduled = 1
    result = []
    while pending:
        if time.monotonic() >= deadline:
            deny("INSTALL_SCAN_LIMIT")
        path, depth = pending.pop()
        try:
            info = path.lstat()
        except OSError:
            deny("INSTALL_SCAN_UNAVAILABLE")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            deny("INSTALL_REPARSE_DENIED")
        result.append(path)
        if not stat.S_ISDIR(info.st_mode):
            continue
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if (time.monotonic() >= deadline or depth >= max_depth
                            or scheduled >= max_entries):
                        deny("INSTALL_SCAN_LIMIT")
                    pending.append((Path(entry.path), depth + 1))
                    scheduled += 1
        except OSError:
            deny("INSTALL_SCAN_UNAVAILABLE")
    return result


def admit(config, config_path):
    if not config["enabled"]:
        deny("KILL_SWITCH")
    broker, transport = config["broker_identity"], config["transport_identity"]
    if os.name == "nt":
        identity, elevated = windows_identity()
        if elevated or identity in ("S-1-5-18", "S-1-5-19", "S-1-5-20"):
            deny("ELEVATION_DENIED")
    else:
        identity = str(os.geteuid())
        if os.geteuid() == 0:
            deny("ELEVATION_DENIED")
    if identity != broker:
        deny("BROKER_IDENTITY_MISMATCH")
    trusted = set(config["trusted_owners"]) | {broker}
    targets = [Path(config_path), Path(config["policy_dir"]), Path(config["release_dir"]), Path(config["state_dir"])]
    # Entire installed code/policy tree, not just a label or marker file.
    for root in (Path(config["policy_dir"]), Path(config["release_dir"]), Path(config["state_dir"])):
        # Includes each root; fixed traversal budget prevents unbounded preflight.
        targets.extend(_bounded_install_paths(root))
    for p in targets:
        s = p.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
            deny("INSTALL_REPARSE_DENIED")
        if os.name == "nt":
            private = trusted if p == Path(config["state_dir"]) or Path(config["state_dir"]) in p.parents else None
            windows_acl(p, trusted, private_readers=private)
        elif str(s.st_uid) not in trusted or s.st_mode & 0o022:
            deny("ACL_UNSAFE")
        elif (p == Path(config["state_dir"]) or Path(config["state_dir"]) in p.parents) and s.st_mode & 0o077:
            deny("ACL_SECRET_EXPOSED")
    key = Path(config["policy_dir"]) / "ipc.key"
    if os.name == "nt":
        windows_acl(key, trusted, private_readers=trusted | {transport})
    elif key.stat().st_mode & 0o007:
        deny("ACL_SECRET_EXPOSED")


CONFIG_KEYS_V4 = CONFIG_KEYS + " source_repository control_repository"
# A distinct, disabled-only personal-use contract. Do NOT reinterpret legacy V4
# identity isolation or let an OS-user-only contract enable an unattended broker.
CONFIG_KEYS_V4_PERSONAL = CONFIG_KEYS_V4 + " account_mode"
PERSONAL_V4_SCHEMA = "hostops.config.v4.personal"
PERSONAL_V4_MODE = "PERSONAL_SINGLE_USER"
PERSONAL_SID_PLACEHOLDER = "REPLACE_WITH_CURRENT_WINDOWS_SID"
NON_BROKER_SIDS = frozenset({"S-1-5-18", "S-1-5-19", "S-1-5-20", "S-1-5-32-544", "0"})


def _validate_v4_config_dict(config):
    personal = isinstance(config, dict) and config.get("schema") == PERSONAL_V4_SCHEMA
    shape(config, CONFIG_KEYS_V4_PERSONAL if personal else CONFIG_KEYS_V4)
    if (config["schema"] not in ("hostops.config.v4", PERSONAL_V4_SCHEMA)
            or type(config["enabled"]) is not bool):
        deny("BAD_CONFIG")
    if personal:
        if config["account_mode"] != PERSONAL_V4_MODE:
            deny("BAD_CONFIG")
        # One Windows token cannot isolate transport credentials from a broker
        # (or from an untrusted public-repo self-host runner under that token).
        # Explicit local signed enrollment is NOT yet implemented: no runtime.
        if config["enabled"]:
            deny("PERSONAL_MODE_NOT_ENROLLED")
    if (config["source_repository"] != SOURCE_REPOSITORY or
            config["control_repository"] != CONTROL_REPOSITORY):
        deny("REPOSITORY_MISMATCH")
    ident(config["host_id"])
    text(config["broker_identity"], 256)
    text(config["transport_identity"], 256)
    for key in ("policy_dir", "state_dir", "transport_state_dir", "release_dir", "ssh_keygen", "pwsh"):
        text(config[key])
        value = config[key]
        # Offline disabled Windows template must parse on POSIX source CI.
        # An enabled V4 broker still requires a native absolute local path.
        native_absolute = Path(value).is_absolute()
        offline_windows_absolute = (config["enabled"] is False and
                                    PureWindowsPath(value).is_absolute())
        if value.startswith(("\\\\", "//")) or not (native_absolute or offline_windows_absolute):
            deny("BAD_CONFIG")
    text(config["endpoint"])
    if personal:
        # Personal PLAN may only name a Windows-local V4 pipe namespace.
        prefix = r"\\.\pipe\AICommerceHostOpsV4-Personal-"
        endpoint = config["endpoint"]
        suffix = endpoint[len(prefix):] if endpoint.startswith(prefix) else ""
        if not (1 <= len(suffix) <= 64 and all(
                ch.isascii() and (ch.isalnum() or ch in "-_") for ch in suffix)):
            deny("NONLOCAL_ENDPOINT")
    for key in ("ssh_keygen_sha256", "pwsh_sha256"):
        sha(config[key])
    for key in ("untrusted_identities", "trusted_owners"):
        if type(config[key]) is not list or not config[key] or len(config[key]) > 32:
            deny("BAD_CONFIG")
        for item in config[key]:
            text(item, 256)
    if (config["broker_identity"] in config["untrusted_identities"] or
            config["transport_identity"] in config["untrusted_identities"] or
            config["broker_identity"] in config["trusted_owners"] or
            config["transport_identity"] in config["trusted_owners"] or
            set(config["trusted_owners"]) & set(config["untrusted_identities"])):
        deny("IDENTITIES_NOT_ISOLATED")
    if personal:
        same = config["broker_identity"] == config["transport_identity"]
        sid = config["broker_identity"]
        import re
        sid_valid = (sid == PERSONAL_SID_PLACEHOLDER or
                     re.fullmatch(r"S-1-[0-9]+(?:-[0-9]+){2,}", sid) is not None)
        if not same or not sid_valid or sid in NON_BROKER_SIDS:
            deny("PERSONAL_IDENTITY_INVALID")
    elif config["broker_identity"] == config["transport_identity"]:
        deny("IDENTITIES_NOT_ISOLATED")
    return config


def load_config_v4(path):
    """Strict V4 configuration loader; rejects v3 schemas and mismatched repos."""
    return _validate_v4_config_dict(decode(Path(path).read_bytes()))


def admit_v4(config, config_path):
    """Strict V4 admission; enforces disabled/KILL gates, non-admin SIDs, and ACLs."""
    _validate_v4_config_dict(config)
    if config["schema"] == PERSONAL_V4_SCHEMA:
        deny("PERSONAL_MODE_NOT_ENROLLED")
    if not config["enabled"] or (Path(config["policy_dir"]) / "KILL").exists():
        deny("KILL_SWITCH")
    if "0" * 64 in (config["ssh_keygen_sha256"], config["pwsh_sha256"]):
        deny("VERIFIER_UNVERIFIED")
    for exe_key, sha_key, err_code in (
        ("ssh_keygen", "ssh_keygen_sha256", "VERIFIER_UNVERIFIED"),
        ("pwsh", "pwsh_sha256", "POWERSHELL_UNVERIFIED"),
    ):
        exe = Path(config[exe_key])
        if not exe.is_absolute() or not exe.is_file() or digest(exe.read_bytes()) != config[sha_key]:
            deny(err_code)
    broker, transport = config["broker_identity"], config["transport_identity"]
    if broker in NON_BROKER_SIDS or transport in NON_BROKER_SIDS:
        deny("ELEVATION_DENIED")
    if (broker.startswith("REPLACE_WITH_") or transport.startswith("REPLACE_WITH_") or
            any(str(item).startswith("REPLACE_WITH_") for item in config["untrusted_identities"])):
        deny("IDENTITIES_NOT_ISOLATED")
    admit(config, config_path)
    transport_root = Path(config["transport_state_dir"])
    if transport_root.exists() or transport_root.is_symlink():
        _bounded_install_paths(transport_root, max_entries=256, max_depth=6,
                               max_seconds=1.0)

