"""Private, fixed Windows READ inventory and a minimized public receipt.

This is not an Issue handler or a native operator enrollment mechanism. A future
enrolled desktop operator must independently authenticate the current user,
unlocked interactive desktop, source, dedicated profile and observation digest.
No implementation of that authority is installed here. Injecting a Python object
is a testing seam, not a security boundary against a malicious local caller.

The fixed collector reads only the three registered task identities, their
Runner.Listener processes and Chrome/CDP 9444. It never probes HTTP, attaches a
browser, starts a task or sends anything. Process presence does not prove GitHub
connectivity. The existing Supervisor is a compatibility consumer; it does not
currently call this module. Only PublicInventory.to_json() is a public receipt;
private models (including their digest) must not be published.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import ntpath
import os
import re
import subprocess
import time
from typing import Protocol

from ai_commerce.engineering.three_runner_topology import (
    WakeHubPreflight, assess_wakehub_preflight, planned_topology,
)


SOURCE_EPOCH = "81cdb3ae95c4af695c9b021bab2010a6d9ee81c1"
TASKS = tuple(spec.task for spec in planned_topology())
_RUNNER_EXES = tuple(spec.root + r"\bin\Runner.Listener.exe"
                     for spec in planned_topology())
_TASK_STATES = frozenset({
    "RUNNING", "READY", "QUEUED", "DISABLED", "NOT_REGISTERED", "UNAVAILABLE",
})
_LISTENER_STATES = frozenset({"PRESENT", "ABSENT", "UNAVAILABLE"})
_CHROME_STATES = frozenset({
    "OWNED_CDP_9444", "ABSENT_INDEPENDENTLY_VERIFIED", "UNVERIFIED", "PROFILE_CONFLICT",
})
_FRESH_SECONDS = 30
_MAX_BYTES = 262144
_MAX_PROCESSES = 64


def _invalid() -> ValueError:
    # Never include private payloads, OS error strings or field values.
    return ValueError("WAKEHUB_PRIVATE_INPUT_INVALID")


def _text(value: object, limit: int = 1024) -> bool:
    return (type(value) is str and 0 < len(value) <= limit
            and not any(ord(c) < 32 for c in value))


def _integer(value: object, minimum: int = 1) -> bool:
    return type(value) is int and minimum <= value <= 2**32 - 1


def _timestamp(value: object) -> bool:
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 < value < 2**53)


def _path(value: object) -> bool:
    return (_text(value) and re.match(r"^[A-Za-z]:\\", value) is not None
            and ntpath.normpath(value) == value
            and not any(p in value for p in ("/", "..", "*", "?")))


def _sid(value: object) -> bool:
    return _text(value, 184) and re.fullmatch(r"S-1-(?:[0-9]+-)+[0-9]+", value) is not None


def _same_path(left: str, right: str) -> bool:
    return ntpath.normcase(left) == ntpath.normcase(right)


@dataclass(frozen=True, slots=True, repr=False)
class ProcessIdentity:
    pid: int
    created_at: float
    executable: str
    user_sid: str
    session_id: int

    def __post_init__(self):
        if not (_integer(self.pid) and _timestamp(self.created_at)
                and _path(self.executable) and _sid(self.user_sid)
                and _integer(self.session_id, 0)):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class ChromeProcess:
    identity: ProcessIdentity
    ports: tuple[int, ...]
    addresses: tuple[str, ...]
    profiles: tuple[str, ...]

    def __post_init__(self):
        if type(self.identity) is not ProcessIdentity:
            raise _invalid()
        for values in (self.ports, self.addresses, self.profiles):
            if type(values) is not tuple or len(values) > 4:
                raise _invalid()
        if (any(not _integer(p) or p > 65535 for p in self.ports)
                or any(not _text(a, 64) for a in self.addresses)
                or any(not _path(p) for p in self.profiles)):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class SocketListener:
    address: str
    port: int
    pid: int

    def __post_init__(self):
        if not (_text(self.address, 64) and _integer(self.port)
                and self.port <= 65535 and _integer(self.pid)):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class PrivateSnapshot:
    source_epoch: str
    observed_at: float
    user_name: str
    user_sid: str
    session_id: int
    task_states: tuple[str, str, str]
    runner_processes: tuple[tuple[ProcessIdentity, ...], ...]
    chrome_processes: tuple[ChromeProcess, ...]
    initial_processes: tuple[ProcessIdentity, ...]
    confirmed_processes: tuple[ProcessIdentity, ...]
    listeners: tuple[SocketListener, ...]
    complete: bool

    def __post_init__(self):
        if (not _text(self.source_epoch, 64) or not _timestamp(self.observed_at)
                or not _text(self.user_name, 256) or not _sid(self.user_sid)
                or not _integer(self.session_id, 0) or type(self.complete) is not bool
                or type(self.task_states) is not tuple or len(self.task_states) != 3
                or any(type(s) is not str or s not in _TASK_STATES for s in self.task_states)
                or type(self.runner_processes) is not tuple or len(self.runner_processes) != 3):
            raise _invalid()
        for group in self.runner_processes:
            if (type(group) is not tuple or len(group) > _MAX_PROCESSES
                    or any(type(p) is not ProcessIdentity for p in group)):
                raise _invalid()
        for group, kind in ((self.chrome_processes, ChromeProcess),
                            (self.initial_processes, ProcessIdentity),
                            (self.confirmed_processes, ProcessIdentity),
                            (self.listeners, SocketListener)):
            if (type(group) is not tuple or len(group) > _MAX_PROCESSES
                    or any(type(p) is not kind for p in group)):
                raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class PrivateEnrollment:
    """Returned only by a separate, independently trusted private authority.

    Profile must be a dedicated owner-enrolled WakeHub profile. Its spelling
    cannot prove dedication. Authority must authenticate that claim externally.
    interactive_session_proven means the exact owner's usable unlocked desktop.
    Human pause and unknown effect come from that authority, never OS absence.
    """
    source_epoch: str
    user_name: str
    user_sid: str
    session_id: int
    chrome_executable: str
    dedicated_profile: str
    issued_at: float
    expires_at: float
    read_allowed: bool
    muted: bool
    interactive_session_proven: bool
    trusted_human_pause: bool
    unknown_effect: bool

    def __post_init__(self):
        if (not _text(self.source_epoch, 64) or not _text(self.user_name, 256)
                or not _sid(self.user_sid) or not _integer(self.session_id)
                or not _path(self.chrome_executable)
                or ntpath.basename(self.chrome_executable).casefold() != "chrome.exe"
                or not _path(self.dedicated_profile)
                or not _timestamp(self.issued_at) or not _timestamp(self.expires_at)
                or not 0 < self.expires_at - self.issued_at <= _FRESH_SECONDS
                or any(type(v) is not bool for v in (
                    self.read_allowed, self.muted, self.interactive_session_proven,
                    self.trusted_human_pause, self.unknown_effect))):
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class PrivateAttestation:
    """Independent proof bound to BOTH private snapshot and current enrollment.

    An operator must not implement verification by blindly echoing these hashes.
    It must independently check identities, creation times, epoch and freshness.
    """
    snapshot_sha256: str
    enrollment_sha256: str
    verified_at: float

    def __post_init__(self):
        if (any(type(v) is not str or re.fullmatch(r"[0-9a-f]{64}", v) is None
                for v in (self.snapshot_sha256, self.enrollment_sha256))
                or not _timestamp(self.verified_at)):
            raise _invalid()


class PrivateReadAuthority(Protocol):
    def authorize_read(self) -> PrivateEnrollment | None: ...

    def verify_read(self, snapshot: PrivateSnapshot,
                    enrollment: PrivateEnrollment) -> PrivateAttestation | None: ...


class InventoryBackend(Protocol):
    def read_fixed_inventory(self) -> PrivateSnapshot: ...


def _private_digest(value: PrivateSnapshot | PrivateEnrollment) -> str:
    return hashlib.sha256(json.dumps(asdict(value), sort_keys=True,
        separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicInventory:
    task_states: tuple[str, str, str]
    runner_listeners: tuple[str, str, str]
    chrome_ownership: str
    lifecycle: str
    inventory_status: str
    supervisor: WakeHubPreflight

    def to_json(self) -> str:
        # A field allowlist, not private asdict() with redactions. Recheck at
        # serialization so a forged PublicInventory cannot disclose free text.
        if (type(self.task_states) is not tuple or len(self.task_states) != 3
                or any(type(v) is not str or v not in _TASK_STATES for v in self.task_states)
                or type(self.runner_listeners) is not tuple or len(self.runner_listeners) != 3
                or any(type(v) is not str or v not in _LISTENER_STATES for v in self.runner_listeners)
                or type(self.chrome_ownership) is not str or self.chrome_ownership not in _CHROME_STATES
                or type(self.lifecycle) is not str
                or self.lifecycle not in ("EXPECTED_MANUAL_STOP", "OBSERVE_ONLY")
                or type(self.inventory_status) is not str
                or self.inventory_status not in ("VERIFIED_READ", "HOLD_UNVERIFIED", "HOLD_RECONCILE")
                or type(self.supervisor) is not WakeHubPreflight):
            raise _invalid()
        # Reconstruct from sanitized inputs; never serialize caller-supplied
        # Supervisor strings or booleans as a public grant.
        s = self.supervisor
        allowed_codes = {
            "HOLD_UNKNOWN_EFFECT", "HOLD_SOURCE_DRIFT", "HOLD_EVENT_RECEIVER_UNAVAILABLE",
            "PAUSED_BY_HUMAN", "WAIT_INTERACTIVE_SESSION", "HOLD_CHROME_OWNERSHIP",
            "WAIT_PRIVATE_CHROME_START_GRANT", "READY_DEGRADED_ENGINEERING", "READY_FOR_ORIGIN_PREPARE",
        }
        if type(s.code) is not str or s.code not in allowed_codes:
            raise _invalid()
        return json.dumps({
            "schema_version": "WakeHubNativeInventoryPublic.v1", "cdp_port": 9444,
            "task_states": self.task_states, "runner_listeners": self.runner_listeners,
            "chrome_ownership": self.chrome_ownership, "lifecycle": self.lifecycle,
            "inventory_status": self.inventory_status, "supervisor_code": s.code,
            "receiver_expectation": "ONE_ONLINE_RECEIVER_REQUIRED",
            "runner_evidence": "LOCAL_PROCESS_ONLY_NOT_GITHUB_CONNECTIVITY",
            "start_permitted": False, "wake_send_authorized": False, "effect_attempted": False,
        }, sort_keys=True, separators=(",", ":"))


def _public(*, task_states=("UNAVAILABLE",) * 3,
            runners=("UNAVAILABLE",) * 3, chrome="UNVERIFIED", verified=False,
            source_current=False, interactive=False, pause=False,
            unknown=False) -> PublicInventory:
    supervisor = assess_wakehub_preflight(
        hostops_task=task_states[2], hostops_listener=runners[2],
        engineering_listeners=runners[:2], interactive_session_proven=interactive,
        chrome_ownership=chrome, source_current=source_current,
        unknown_effect=unknown, trusted_human_pause=pause,
    )
    return PublicInventory(task_states, runners, chrome,
        "EXPECTED_MANUAL_STOP" if pause else "OBSERVE_ONLY",
        "HOLD_RECONCILE" if unknown else "VERIFIED_READ" if verified else "HOLD_UNVERIFIED",
        supervisor)


def _eligible(enrollment: PrivateEnrollment, now: float) -> bool:
    return (enrollment.source_epoch == SOURCE_EPOCH and enrollment.read_allowed
            and not enrollment.muted and enrollment.interactive_session_proven
            and enrollment.issued_at <= now <= enrollment.expires_at
            and now - enrollment.issued_at <= _FRESH_SECONDS)


def normalize_private_inventory(
    snapshot: PrivateSnapshot, enrollment: PrivateEnrollment | None,
    attestation: PrivateAttestation | None, *, now: float,
) -> PublicInventory:
    """Pure normalization. No OS, browser, file, task, network or Issue calls."""
    if type(snapshot) is not PrivateSnapshot or not _timestamp(now):
        raise _invalid()
    if enrollment is not None and type(enrollment) is not PrivateEnrollment:
        raise _invalid()
    if attestation is not None and type(attestation) is not PrivateAttestation:
        raise _invalid()
    if enrollment is None:
        return _public()
    pause, unknown = enrollment.trusted_human_pause, enrollment.unknown_effect
    if not _eligible(enrollment, now) or attestation is None:
        return _public(pause=pause, unknown=unknown)
    if (attestation.snapshot_sha256 != _private_digest(snapshot)
            or attestation.enrollment_sha256 != _private_digest(enrollment)
            or not snapshot.observed_at <= attestation.verified_at <= now
            or now - snapshot.observed_at > _FRESH_SECONDS
            or snapshot.observed_at < enrollment.issued_at
            or snapshot.source_epoch != SOURCE_EPOCH or not snapshot.complete
            or snapshot.user_name != enrollment.user_name
            or snapshot.user_sid != enrollment.user_sid
            or snapshot.session_id != enrollment.session_id
            or len({p.pid for p in snapshot.initial_processes}) != len(snapshot.initial_processes)
            or set(snapshot.initial_processes) != set(snapshot.confirmed_processes)
            or len(snapshot.initial_processes) != len(snapshot.confirmed_processes)):
        return _public(pause=pause, unknown=unknown)

    def identity_ok(p: ProcessIdentity) -> bool:
        return (p.user_sid == enrollment.user_sid and p.session_id == enrollment.session_id
                and p.created_at <= snapshot.observed_at
                and snapshot.initial_processes.count(p) == 1
                and snapshot.confirmed_processes.count(p) == 1
                and sum(c.pid == p.pid for c in snapshot.confirmed_processes) == 1)

    runners = tuple(
        "ABSENT" if not group and not any(_same_path(p.executable, expected)
            for p in snapshot.confirmed_processes) else "PRESENT" if len(group) == 1
        and identity_ok(group[0]) and _same_path(group[0].executable, expected)
        else "UNAVAILABLE"
        for group, expected in zip(snapshot.runner_processes, _RUNNER_EXES)
    )
    chrome = "UNVERIFIED"
    candidates = snapshot.chrome_processes
    # Collision is visible even if a second process is foreign or has bad flags.
    profile_owners = [c for c in candidates
        if any(_same_path(p, enrollment.dedicated_profile) for p in c.profiles)]
    if len(profile_owners) > 1 or any(len(c.profiles) > 1 for c in profile_owners):
        chrome = "PROFILE_CONFLICT"
    elif (not candidates and not snapshot.listeners
          and not any(ntpath.basename(p.executable).casefold() == "chrome.exe"
                      for p in snapshot.confirmed_processes)):
        # Absence needs a complete, independently attested process/socket read.
        chrome = "ABSENT_INDEPENDENTLY_VERIFIED"
    elif len(profile_owners) == 1 and len(snapshot.listeners) == 1:
        c, listener = profile_owners[0], snapshot.listeners[0]
        if (identity_ok(c.identity)
                and sum(p.identity.pid == c.identity.pid for p in candidates) == 1
                and _same_path(c.identity.executable, enrollment.chrome_executable)
                and c.ports == (9444,) and c.addresses == ("127.0.0.1",)
                and listener.port == 9444 and listener.address == "127.0.0.1"
                and listener.pid == c.identity.pid):
            chrome = "OWNED_CDP_9444"
    return _public(task_states=snapshot.task_states, runners=runners, chrome=chrome,
        verified=chrome not in ("UNVERIFIED", "PROFILE_CONFLICT")
                 and "UNAVAILABLE" not in runners,
        source_current=True, interactive=True, pause=pause, unknown=unknown)


# This command has no interpolated selectors, Issue text, environment-owned
# paths, network requests or write/start operations. CommandLine is ephemeral
# private transport only and is discarded by _decode_native, never a receipt.
_WINDOWS_READ_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$owner = [Security.Principal.WindowsIdentity]::GetCurrent()
$session = (Get-Process -Id $PID).SessionId
$tasks = @('AI-Commerce-EF02-Interactive','AI-Commerce-EF02-02-Interactive','AI-Commerce-HostOps-01-Interactive')
$registered = @(Get-CimInstance -Namespace 'root/Microsoft/Windows/TaskScheduler' -ClassName MSFT_ScheduledTask -Filter "TaskPath = '\\' AND (TaskName = 'AI-Commerce-EF02-Interactive' OR TaskName = 'AI-Commerce-EF02-02-Interactive' OR TaskName = 'AI-Commerce-HostOps-01-Interactive')")
$taskStates = @(foreach ($name in $tasks) {
    $found = @($registered | Where-Object { $_.TaskName -ceq $name })
    if ($found.Count -gt 1) { throw 'TASK_NOT_UNIQUE' }
    $states = @('UNAVAILABLE','DISABLED','QUEUED','READY','RUNNING')
    $state = if ($found.Count -eq 0) { 'NOT_REGISTERED' } elseif ([int]$found[0].State -ge 0 -and [int]$found[0].State -le 4) { $states[[int]$found[0].State] } else { 'UNAVAILABLE' }
    $state
})
function Read-FixedProcesses {
    param([bool]$WithArguments)
    $found = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'chrome.exe' OR Name = 'Runner.Listener.exe'" | Select-Object -First 65)
    if ($found.Count -gt 64) { throw 'PROCESS_LIMIT' }
    @(foreach ($p in $found) {
        $sid = Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid
        if ($sid.ReturnValue -ne 0 -or [string]::IsNullOrEmpty($p.ExecutablePath)) { throw 'PROCESS_IDENTITY_UNAVAILABLE' }
        $item = @{
            pid = [long]$p.ProcessId
            created_at = ([DateTimeOffset]$p.CreationDate).ToUnixTimeMilliseconds() / 1000.0
            executable = [string]$p.ExecutablePath
            user_sid = [string]$sid.Sid
            session_id = [long]$p.SessionId
        }
        if ($WithArguments) { $item.command_line = [string]$p.CommandLine }
        $item
    })
}
$processes = @(Read-FixedProcesses $true)
$listeners = @(Get-CimInstance -Namespace 'root/StandardCimv2' -ClassName MSFT_NetTCPConnection -Filter 'LocalPort = 9444 AND State = 2' | Select-Object -First 65 | ForEach-Object {
    @{address=[string]$_.LocalAddress; port=[long]$_.LocalPort; pid=[long]$_.OwningProcess}
})
if ($listeners.Count -gt 64) { throw 'LISTENER_LIMIT' }
$confirmed = @(Read-FixedProcesses $false)
@{
    schema_version = 'WakeHubNativeReadPrivate.v1'
    source_epoch = '81cdb3ae95c4af695c9b021bab2010a6d9ee81c1'
    observed_at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    user_name = $owner.Name; user_sid = $owner.User.Value; session_id = [long]$session
    task_states = $taskStates; processes = $processes; confirmed_processes = $confirmed
    listeners = $listeners; complete = $true
} | ConvertTo-Json -Depth 7 -Compress
"""
_WINDOWS_READ_COMMAND = (
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_READ_SCRIPT,
)


def _windows_argv(command_line: str) -> tuple[str, ...]:
    """Windows' own parser; no shell evaluation or execution of arguments."""
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    parsed = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not parsed:
        raise _invalid()
    try:
        if not 1 <= count.value <= 256:
            raise _invalid()
        return tuple(parsed[i] for i in range(count.value))
    finally:
        kernel.LocalFree(ctypes.cast(parsed, wintypes.HLOCAL))


def _fields(data: object, fields: set[str]) -> dict:
    if type(data) is not dict or set(data) != fields:
        raise _invalid()
    return data


def _items(data: object) -> list:
    if type(data) is not list or len(data) > _MAX_PROCESSES:
        raise _invalid()
    return data


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise _invalid()
        obj[key] = value
    return obj


def _decode_native(raw: bytes) -> PrivateSnapshot:
    if type(raw) is not bytes or len(raw) > _MAX_BYTES:
        raise _invalid()
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        _fields(data, {"schema_version", "source_epoch", "observed_at", "user_name", "user_sid",
            "session_id", "task_states", "processes", "confirmed_processes", "listeners", "complete"})
        if data["schema_version"] != "WakeHubNativeReadPrivate.v1":
            raise _invalid()
        identity_fields = {"pid", "created_at", "executable", "user_sid", "session_id"}
        confirmed = tuple(ProcessIdentity(**_fields(p, identity_fields))
                          for p in _items(data["confirmed_processes"]))
        runners: list[list[ProcessIdentity]] = [[], [], []]
        chrome = []
        initial = []
        for raw_process in _items(data["processes"]):
            p = _fields(raw_process, identity_fields | {"command_line"})
            identity = ProcessIdentity(**{k: p[k] for k in identity_fields})
            initial.append(identity)
            if not _text(p["command_line"], 32768):
                raise _invalid()
            for index, expected in enumerate(_RUNNER_EXES):
                if _same_path(identity.executable, expected):
                    runners[index].append(identity)
            if ntpath.basename(identity.executable).casefold() != "chrome.exe":
                continue
            argv = _windows_argv(p["command_line"])
            if not argv or not _same_path(argv[0], identity.executable):
                raise _invalid()
            if any(a == "--type" or a.startswith("--type=") for a in argv[1:]):
                continue  # Chrome children are not independent browser owners.
            def flags(name):
                # Only unambiguous --name=value syntax proves a facet.
                if name in argv:
                    raise _invalid()
                return tuple(a[len(name)+1:] for a in argv[1:] if a.startswith(name + "="))
            ports = flags("--remote-debugging-port")
            if any(not re.fullmatch(r"[0-9]{1,5}", port) for port in ports):
                raise _invalid()
            chrome.append(ChromeProcess(identity, tuple(int(p) for p in ports),
                flags("--remote-debugging-address"), flags("--user-data-dir")))
        listeners = tuple(SocketListener(**_fields(p, {"address", "port", "pid"}))
                          for p in _items(data["listeners"]))
        return PrivateSnapshot(data["source_epoch"], data["observed_at"], data["user_name"],
            data["user_sid"], data["session_id"], tuple(_items(data["task_states"])),
            tuple(tuple(p) for p in runners), tuple(chrome), tuple(initial), confirmed,
            listeners, data["complete"])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise _invalid() from None


class WindowsReadBackend:
    """Private backend. Use only through collect_private_inventory's gates."""

    def __init__(self, authority: PrivateReadAuthority | None = None):
        self._authority = authority

    def read_fixed_inventory(self) -> PrivateSnapshot:
        if os.name != "nt":
            raise ValueError("WAKEHUB_WINDOWS_REQUIRED")
        if os.environ.get("GITHUB_ACTIONS", "").casefold() == "true" or self._authority is None:
            raise ValueError("WAKEHUB_PRIVATE_AUTHORITY_REQUIRED")
        permit = self._authority.authorize_read()
        if (type(permit) is not PrivateEnrollment or not _eligible(permit, time.time())
                or permit.unknown_effect):
            raise ValueError("WAKEHUB_PRIVATE_AUTHORITY_REQUIRED")
        result = subprocess.run(_WINDOWS_READ_COMMAND, shell=False,
            capture_output=True, timeout=10, check=False, creationflags=0x08000000)
        if result.returncode != 0:
            # stderr may contain paths, command lines and account information.
            raise ValueError("WAKEHUB_NATIVE_READ_UNAVAILABLE")
        return _decode_native(result.stdout)


def collect_private_inventory(
    authority: PrivateReadAuthority | None = None, *,
    backend: InventoryBackend | None = None,
) -> PublicInventory:
    """Private-callable only; no default enrollment and no external dispatch.

    Actual collection is gated off on non-Windows, unattended CI, denied/muted
    or unverified desktops, expired enrollment and unresolved prior effects.
    Neither OS absence nor another event releases trusted_human_pause.
    """
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS", "").casefold() == "true" or authority is None:
        return _public()
    enrollment = None
    try:
        enrollment = authority.authorize_read()
        now = time.time()
        if type(enrollment) is not PrivateEnrollment:
            return _public()
        if enrollment.unknown_effect or not _eligible(enrollment, now):
            return _public(pause=enrollment.trusted_human_pause, unknown=enrollment.unknown_effect)
        snapshot = (backend if backend is not None else WindowsReadBackend(authority)).read_fixed_inventory()
        attestation = authority.verify_read(snapshot, enrollment)
        return normalize_private_inventory(snapshot, enrollment, attestation, now=time.time())
    except Exception:
        # Private error text is never a public receipt or a retry instruction.
        return _public(pause=bool(type(enrollment) is PrivateEnrollment and enrollment.trusted_human_pause),
            unknown=bool(type(enrollment) is PrivateEnrollment and enrollment.unknown_effect))
