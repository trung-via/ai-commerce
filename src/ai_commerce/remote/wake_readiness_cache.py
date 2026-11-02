"""Change-driven private re-attestation for the existing verified D5 Chrome root.

A cached record is never sufficient by itself: each reuse checks the same
live PID *and* process creation timestamp, loopback-only TCP owner, exact
executable and /json/version protocol. Any change requires the original
full chrome_acquisition guard. Unlike a TTL, an unchanged live process does
not cause unnecessary full rechecks. Cache cannot authorize Send or Wake.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

from . import chrome_acquisition as ca
from ._private_state import PrivateJsonState
from .local_completion_handoff import _private_identity

_IDENTITY_PS = r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$ErrorActionPreference='Stop'
$port=[int]$env:AI_COMMERCE_ACQ_PROBE_PORT
$listener=@(Get-NetTCPConnection -State Listen -ErrorAction Stop |
  Where-Object { $_.LocalPort -eq $port })
if($listener.Count -ne 1) { Write-Output '{}'; exit 0 }
$entry=$listener[0]
$proc=Get-CimInstance -ClassName Win32_Process -Filter (
  'ProcessId = ' + [int]$entry.OwningProcess) -ErrorAction Stop
if($null -eq $proc) { Write-Output '{}'; exit 0 }
@{
  address=[string]$entry.LocalAddress
  pid=[int]$entry.OwningProcess
  creation=$proc.CreationDate.ToUniversalTime().ToString('o')
  executable=[string]$proc.ExecutablePath
  session=[int]$proc.SessionId
} | ConvertTo-Json -Compress -Depth 2
"""


def _live_port_identity(port: int) -> dict | None:
    """One Windows PID/port query, not the all-Chrome CIM enumeration."""
    env = ca._child_environment()
    env["AI_COMMERCE_ACQ_PROBE_PORT"] = str(port)
    try:
        completed = subprocess.run(
            [str(ca._powershell()), "-NoProfile", "-NonInteractive",
             "-Command", _IDENTITY_PS],
            capture_output=True, timeout=8, check=False, env=env)
        if completed.returncode or len(completed.stdout) > 4096:
            return None
        value = json.loads(completed.stdout.decode("utf-8-sig"))
        if (type(value) is not dict or set(value) !=
                {"address", "pid", "creation", "executable", "session"}):
            return None
        if (value["address"] != "127.0.0.1" or
                type(value["pid"]) is not int or value["pid"] <= 0 or
                type(value["session"]) is not int or
                type(value["creation"]) is not str or
                not 15 <= len(value["creation"]) <= 80 or
                type(value["executable"]) is not str):
            return None
        return value
    except (OSError, ValueError, TypeError, UnicodeError, subprocess.TimeoutExpired):
        return None


class ChromeAttestationCache:
    """Private advisory cache. Never permits a new launch on cache failure."""

    def __init__(self, *, path: Path, checkout: Path, env: ca.ChromeEnvironment,
                 identity_reader=None, protocol_probe=None):
        self.env = env
        self.identity_reader = identity_reader or (
            lambda: _live_port_identity(env.port))
        self.protocol_probe = protocol_probe or (lambda: ca._probe_cdp(env))
        self.state = PrivateJsonState(
            path, checkout, label="D5_CHROME_CACHE", max_bytes=2048)
        self._private_owner()

    def _private_owner(self):
        principal = _private_identity(self.state.path.parent)
        for path in (self.state.path,
                     self.state.path.with_name(self.state.path.name + ".lock")):
            if path.exists() and _private_identity(path) != principal:
                raise ValueError("CACHE_OWNER_MISMATCH")
        return principal

    def _fingerprint(self) -> str:
        # The configured Chrome program/profile/port and all Chrome flags are
        # included in the private config. Never store private file contents.
        raw = self.env.config_path.read_bytes()
        if len(raw) > 8192:
            raise ValueError("CACHE_CONFIG_TOO_LARGE")
        return hashlib.sha256(raw).hexdigest()

    def _read(self) -> dict:
        owner = self._private_owner()
        with self.state.locked(
                new_file_owner_sid=owner if os.name == "nt" else None) as tx:
            return tx.read({})

    def _write(self, record: dict):
        owner = self._private_owner()
        with self.state.locked(
                new_file_owner_sid=owner if os.name == "nt" else None) as tx:
            tx.write(record)

    def _valid_live(self, identity) -> bool:
        if not isinstance(identity, dict):
            return False
        try:
            # Compare a PID and its creation time: PID alone can be reused.
            return (identity.get("address") == "127.0.0.1" and
                    type(identity.get("pid")) is int and
                    type(identity.get("session")) is int and
                    type(identity.get("creation")) is str and
                    Path(identity["executable"]).resolve(strict=True) ==
                    self.env.executable and
                    identity["session"] == ca._interactive_session())
        except (OSError, ValueError, TypeError, KeyError, ca.AcquisitionBlocked):
            return False

    def reuse(self) -> dict | None:
        """Return trusted existing-browser attestation only on exact live match."""
        try:
            record = self._read()
            if (type(record) is not dict or set(record) !=
                    {"v", "fingerprint", "pid", "creation", "session"} or
                    record["v"] != 1 or
                    record["fingerprint"] != self._fingerprint()):
                return None
            identity = self.identity_reader()
            if (not self._valid_live(identity) or
                    identity["pid"] != record["pid"] or
                    identity["creation"] != record["creation"] or
                    identity["session"] != record["session"]):
                return None
            self.protocol_probe()
            return {"status": "READY_ATTACH", "cdp_verified": True,
                    "browser_accessed": True, "launched": False,
                    "verification": "LIVE_PID_CREATION_AND_CDP_REUSED"}
        except Exception:
            return None

    def remember_verified(self) -> None:
        """Called only AFTER the original full acquisition returned READY_ATTACH."""
        try:
            identity = self.identity_reader()
            if not self._valid_live(identity):
                return
            self.protocol_probe()
            self._write({
                "v": 1, "fingerprint": self._fingerprint(),
                "pid": identity["pid"], "creation": identity["creation"],
                "session": identity["session"],
            })
        except Exception:
            # Readiness does not depend on an optional cache succeeding.
            return

    def acquire(self, *, opt_in_launch: bool) -> dict:
        # Fast path: no Authenticode rerun, no every-Chrome enumeration, no
        # launch. Protocol and live identity still tested on every reuse.
        reused = self.reuse()
        if reused is not None:
            return reused
        result = ca.run(self.env, launch=opt_in_launch, opt_in=opt_in_launch)
        if (result.get("status") == "READY_ATTACH" and
                result.get("cdp_verified") is True):
            self.remember_verified()
        return result
