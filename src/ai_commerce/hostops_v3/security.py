"""OS identity/ACL admission. No chmod, ACL mutation, UAC or privilege acquisition."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import stat

from .contract import CONTROL_REPOSITORY, SOURCE_REPOSITORY, decode, deny, digest, ident, sha, shape, text


def prefer_readable_pwsh_path():
    """Prefer a byte-readable pwsh binary over an unreadable WindowsApps AppExecLink stub."""
    if os.name != "nt":
        return shutil.which("pwsh")
    current = shutil.which("pwsh")
    if current:
        try:
            with Path(current).open("rb") as handle:
                handle.read(4)
            return current
        except OSError:
            pass
    entries = os.environ.get("PATH", "").split(os.pathsep)
    readable_dirs = []
    other_dirs = []
    for entry in entries:
        candidate = Path(entry) / "pwsh.exe" if entry else None
        is_readable = False
        if candidate is not None:
            try:
                if candidate.is_file():
                    with candidate.open("rb") as handle:
                        is_readable = bool(handle.read(4))
            except OSError:
                is_readable = False
        if is_readable:
            readable_dirs.append(entry)
        else:
            other_dirs.append(entry)
    if readable_dirs:
        os.environ["PATH"] = os.pathsep.join([*readable_dirs, *other_dirs])
    return shutil.which("pwsh")


prefer_readable_pwsh_path()


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
        targets.extend(root.rglob("*"))
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
NON_BROKER_SIDS = frozenset({"S-1-5-18", "S-1-5-19", "S-1-5-20", "S-1-5-32-544", "0"})


def _validate_v4_config_dict(config):
    shape(config, CONFIG_KEYS_V4)
    if config["schema"] != "hostops.config.v4" or type(config["enabled"]) is not bool:
        deny("BAD_CONFIG")
    if (config["source_repository"] != SOURCE_REPOSITORY or
            config["control_repository"] != CONTROL_REPOSITORY):
        deny("REPOSITORY_MISMATCH")
    ident(config["host_id"])
    text(config["broker_identity"], 256)
    text(config["transport_identity"], 256)
    for key in ("policy_dir", "state_dir", "transport_state_dir", "release_dir", "ssh_keygen", "pwsh"):
        text(config[key])
        if not Path(config[key]).is_absolute() or str(config[key]).startswith("\\\\"):
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
            config["broker_identity"] in config["trusted_owners"] or
            config["transport_identity"] in config["trusted_owners"] or
            set(config["trusted_owners"]) & set(config["untrusted_identities"])):
        deny("IDENTITIES_NOT_ISOLATED")
    return config


def load_config_v4(path):
    """Strict V4 configuration loader; rejects v3 schemas and mismatched repos."""
    return _validate_v4_config_dict(decode(Path(path).read_bytes()))


def admit_v4(config, config_path):
    """Strict V4 admission; enforces disabled/KILL gates, non-admin SIDs, and ACLs."""
    _validate_v4_config_dict(config)
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
    if transport_root.exists():
        for p in (transport_root, *transport_root.rglob("*")):
            s = p.lstat()
            if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
                deny("INSTALL_REPARSE_DENIED")

