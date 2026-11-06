"""Closed D5 lifecycle bridge; disabled until independently enrolled.

The private control plane supplies a verified enrollment and authenticates requests.
An Actions worker must never own its key. Same-SID processes are not a vault.
Native effects use only the enrolled interactive desktop and a signed release.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import ctypes
from ctypes import wintypes as w
import sys
import tempfile
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import base64
import secrets
from typing import Callable

from ai_commerce.hostops_v3.auth import child_environment as signature_environment
from ai_commerce.remote import chrome_acquisition as ca

OPS = (
    "STATUS", "MIGRATION_PREFLIGHT", "RETIRE_NEW_AUTOSTART",
    "STOP_OWNED_D5_CHROME", "INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER",
    "START_OR_REUSE_D5_CHROME", "VERIFY_CDP_9333_AND_PLAYWRIGHT",
    "INSTALL_SINGLE_LOGON_AUTOSTART", "REMOVE_D5_AUTOSTART",
    "RECOVER_BOUNDED", "ROLLBACK",
)
READS = frozenset({"STATUS", "MIGRATION_PREFLIGHT", "VERIFY_CDP_9333_AND_PLAYWRIGHT"})
TASK = "AI-Commerce-D5-Operator"
RUNNER_TASKS = ("AI-Commerce-EF02-Interactive", "AI-Commerce-EF02-02-Interactive",
                "AI-Commerce-HostOps-01-Interactive")
STRATEGY = "AUDITED_ACQUISITION_PLAYWRIGHT"
RELEASE_FILES = (
    "src/ai_commerce/hostops_v3/d5_control.py",
    "src/ai_commerce/remote/chrome_acquisition.py",
    "scripts/AI-Commerce-D5-Operator-Bridge.ps1",
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")


class D5Blocked(ValueError):
    """A source-owned public code, never an exception containing private data."""


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise D5Blocked("INPUT_INVALID")
            result[key] = value
        return result
    try:
        if len(data) > 32768:
            raise ValueError()
        value = json.loads(data, object_pairs_hook=unique)
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise D5Blocked("INPUT_INVALID") from None


@dataclass(frozen=True)
class Enrollment:
    """Trusted local policy, NEVER constructed from an Issue or request fields.

    Private deployment verifies the signed policy on EVERY invocation. The
    verifier is dependency injected by the trusted host, never by the caller.
    """
    source_sha: str
    epoch: int
    sid: str
    expires: int
    release_root: Path
    files: dict[str, str]
    environment: ca.ChromeEnvironment
    state_root: Path
    key: bytes
    legacy_tasks: tuple[dict, ...] = ()
    runner_tasks: tuple[str, ...] = ()
    enabled: bool = False
    revoked: bool = False
    private_control_plane: bool = False
    public_runner_exposed: bool = True

    def validate(self, now: int) -> None:
        if (not self.enabled or self.revoked or not self.private_control_plane
                or self.public_runner_exposed or os.environ.get("GITHUB_ACTIONS") == "true"):
            raise D5Blocked("PRIVATE_ENROLLMENT_REQUIRED")
        if (not re.fullmatch(r"[0-9a-f]{40}", self.source_sha)
                or type(self.epoch) is not int or self.epoch < 1
                or not re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", self.sid)
                or type(self.expires) is not int or self.expires <= now
                or len(self.key) != 32 or self.environment.port != 9333
                or self.environment.endpoint.rstrip("/") != "http://127.0.0.1:9333"):
            raise D5Blocked("ENROLLMENT_INVALID_OR_STALE")
        if set(self.runner_tasks) != set(RUNNER_TASKS) or len(self.runner_tasks) != 3:
            raise D5Blocked("RUNNER_PRESERVATION_UNPROVEN")
        if len(self.legacy_tasks) > 16:
            raise D5Blocked("MIGRATION_POLICY_INVALID")
        names = set()
        for task in self.legacy_tasks:
            if (type(task) is not dict or set(task) != {"name", "xml_sha256", "launcher", "sha256"}
                    or not re.fullmatch(r"AI-Commerce-D5-[A-Za-z0-9-]{1,64}", task["name"])
                    or task["name"] in (*self.runner_tasks, TASK) or task["name"] in names
                    or not _HEX.fullmatch(task["xml_sha256"]) or not _HEX.fullmatch(task["sha256"])):
                raise D5Blocked("MIGRATION_POLICY_INVALID")
            names.add(task["name"])
            launcher = Path(task["launcher"])
            legacy_root = self.state_root.parent / "D5-Autostart"
            if (not launcher.is_absolute() or launcher.suffix.lower() != ".ps1"
                    or launcher.parent != legacy_root or not ca._no_links(launcher)
                    or self.environment.user_data_dir == legacy_root
                    or self.environment.user_data_dir in launcher.parents):
                raise D5Blocked("MIGRATION_POLICY_INVALID")


def private_path(path: Path, *, directory: bool = False) -> Path:
    """Existing native exact-SID/ACL guard, including all reparse ancestors."""
    from ai_commerce.remote.local_completion_handoff import _private_identity
    try:
        if (not path.is_absolute() or not ca._no_links(path)
                or (not path.is_dir() if directory else not path.is_file())):
            raise ValueError()
        _private_identity(path)
        return path.resolve(strict=True)
    except Exception:
        raise D5Blocked("PRIVATE_STORAGE_UNPROVEN") from None


def load_enrollment(path: Path, *, authority, now: int) -> Enrollment:
    """Only the concrete, protected-anchor verifier can load native policy."""
    try:
        if type(authority) is not SignedPolicy:
            raise D5Blocked("ENROLLMENT_UNVERIFIED")
        private_path(path)
        value = authority.verify(path, now)
        fields = {"source_sha", "epoch", "sid", "expires", "release_root", "files",
                  "chrome_config", "local_app_data", "state_root", "key_file", "legacy_tasks",
                  "runner_tasks", "enabled", "revoked", "private_control_plane", "public_runner_exposed"}
        if type(value) is not dict or set(value) != fields:
            raise ValueError()
        for flag in ("enabled", "revoked", "private_control_plane", "public_runner_exposed"):
            if type(value[flag]) is not bool:
                raise ValueError()
        root = private_path(Path(value["release_root"]), directory=True)
        state = private_path(Path(value["state_root"]), directory=True)
        key = private_path(Path(value["key_file"])).read_bytes()
        if root == state or root in state.parents or state in root.parents:
            raise ValueError()
        environment = ca.load_environment(Path(value["chrome_config"]), root,
                                         local_app_data=Path(value["local_app_data"]))
        policy = Enrollment(value["source_sha"], value["epoch"], value["sid"], value["expires"],
                            root, value["files"], environment, state, key,
                            tuple(value["legacy_tasks"]), tuple(value["runner_tasks"]),
                            value["enabled"], value["revoked"], value["private_control_plane"],
                            value["public_runner_exposed"])
        policy.validate(now)
        verify_release(policy)
        return policy
    except D5Blocked:
        raise
    except Exception:
        raise D5Blocked("ENROLLMENT_UNVERIFIED") from None


def verify_release(policy: Enrollment) -> None:
    """The signed policy pins the release closure, not a mutable main checkout."""
    try:
        if not set(RELEASE_FILES) <= set(policy.files) or len(policy.files) > 256:
            raise ValueError()
        root = policy.release_root.resolve(strict=True)
        for name, expected in policy.files.items():
            if (not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                    or any(p in ("", ".", "..") for p in name.split("/"))
                    or not _HEX.fullmatch(expected)):
                raise ValueError()
            target = root / name
            if (not ca._no_links(target) or root not in target.resolve(strict=True).parents
                    or digest(target.read_bytes()) != expected):
                raise ValueError()
        # Every Python dependency loaded in this package must be pinned too.
        for name, module in tuple(sys.modules.items()):
            if name == "ai_commerce" or name.startswith("ai_commerce."):
                file = getattr(module, "__file__", None)
                if file:
                    relative = Path(file).resolve().relative_to(root).as_posix()
                    if policy.files.get(relative) != digest(Path(file).read_bytes()):
                        raise ValueError()
    except Exception:
        raise D5Blocked("SOURCE_DRIFT") from None


def sign_request(policy: Enrollment, op: str, request_id: str, now: int, *, dry_run=False) -> dict:
    """Private control-plane helper. Never exposed to an Actions runner."""
    value = {"op": op, "request_id": request_id, "issued": now, "expires": now + 60,
             "source_sha": policy.source_sha, "epoch": policy.epoch, "dry_run": dry_run}
    return {**value, "mac": hmac.new(policy.key, canonical(value), "sha256").hexdigest()}


class Journal:
    """SQLite intent before effect, durable UNKNOWN on crash; no retry queue."""
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, op TEXT, state TEXT, "
                       "receipt TEXT, at INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS health (id INTEGER PRIMARY KEY CHECK(id=1), "
                       "pid INTEGER, created TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS ingress_nonces (nonce TEXT PRIMARY KEY, "
                       "request_id TEXT UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS enrollment_pin (id INTEGER PRIMARY KEY CHECK(id=1), "
                       "enrollment_id TEXT, grant_sha256 TEXT)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=1, isolation_level="IMMEDIATE")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, request: dict, now: int) -> None:
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if "nonce" in request:
                    if db.execute("SELECT 1 FROM ingress_nonces WHERE nonce=? OR request_id=?",
                                  (request["nonce"], request["request_id"])).fetchone():
                        raise D5Blocked("REQUEST_REPLAY")
                if db.execute("SELECT 1 FROM requests WHERE id=?", (request["request_id"],)).fetchone():
                    raise D5Blocked("REQUEST_REPLAY")
                if db.execute("SELECT 1 FROM requests WHERE state IN ('INTENT','UNKNOWN')").fetchone():
                    raise D5Blocked("UNKNOWN_EFFECT_HOLD")
                if db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 2048:
                    raise D5Blocked("JOURNAL_ROTATION_REQUIRED")
                if request["op"] == "RECOVER_BOUNDED" and db.execute(
                        "SELECT 1 FROM requests WHERE op='RECOVER_BOUNDED' AND at>?", (now - 300,)).fetchone():
                    raise D5Blocked("RECOVERY_RATE_LIMIT")
                db.execute("INSERT INTO requests VALUES (?,?, 'INTENT',NULL,?)",
                           (request["request_id"], request["op"], now))
                if "nonce" in request:
                    db.execute("INSERT INTO ingress_nonces VALUES (?,?)",
                               (request["nonce"], request["request_id"]))
        except sqlite3.Error:
            raise D5Blocked("JOURNAL_UNAVAILABLE") from None

    def complete(self, request_id: str, receipt: dict, *, unknown=False) -> None:
        with self.connect() as db:
            db.execute("UPDATE requests SET state=?,receipt=? WHERE id=? AND state='INTENT'",
                       ("UNKNOWN" if unknown else "DONE", canonical(receipt).decode(), request_id))

    def previous(self) -> tuple[int, str] | None:
        with self.connect() as db:
            return db.execute("SELECT pid,created FROM health WHERE id=1").fetchone()

    def healthy(self, identity: tuple[int, str]) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO health VALUES (1,?,?)", identity)

    def pin_enrollment(self, enrollment_id: str, grant_sha256: str) -> None:
        # A different grant/epoch needs attended reconciliation and a NEW journal.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pin = db.execute("SELECT enrollment_id,grant_sha256 FROM enrollment_pin WHERE id=1").fetchone()
            if pin and pin != (enrollment_id, grant_sha256):
                raise D5Blocked("ENROLLMENT_REUSE_DENIED")
            db.execute("INSERT OR IGNORE INTO enrollment_pin VALUES (1,?,?)", (enrollment_id, grant_sha256))


def receipt(op: str, code: str, *, effect=False, registered=False, started=False,
            bound=False, attached=False, stable=False) -> dict:
    return {"operation": op, "code": code, "effect_observed": effect,
            "registered": registered, "started": started, "listener_bound": bound,
            "playwright_attached": attached, "stable": stable,
            "wake_sent": False, "login_verified": False, "manifest_verified": False}


class Bridge:
    def __init__(self, enrollment: Callable[[], Enrollment], native, journal: Journal,
                 *, clock=time.time, sleep=time.sleep):
        self.enrollment, self.native, self.journal = enrollment, native, journal
        self.clock, self.sleep = clock, sleep

    def logon(self) -> dict:
        """Called only by the private receiver after native logon attestation."""
        policy = self.enrollment()
        return self.invoke(sign_request(policy, "START_OR_REUSE_D5_CHROME",
                                        secrets.token_hex(16), int(self.clock())))

    def invoke(self, request: dict, *, ingress_nonce: str | None = None) -> dict:
        # Reload signed policy/revocation on every invocation, including logon.
        policy = self.enrollment()
        now = int(self.clock())
        policy.validate(now)
        fields = {"op", "request_id", "issued", "expires", "source_sha", "epoch", "dry_run", "mac"}
        if (type(request) is not dict or set(request) != fields
                or request.get("op") not in OPS or not isinstance(request.get("request_id"), str)
                or not _ID.fullmatch(request["request_id"]) or type(request["dry_run"]) is not bool
                or type(request["issued"]) is not int or type(request["expires"]) is not int
                or not now - 60 <= request["issued"] <= now
                or not now < request["expires"] <= request["issued"] + 60
                or request["source_sha"] != policy.source_sha or type(request["epoch"]) is not int
                or request["epoch"] != policy.epoch or not isinstance(request["mac"], str)
                or not _HEX.fullmatch(request["mac"])):
            raise D5Blocked("REQUEST_INVALID_OR_STALE")
        unsigned = {k: v for k, v in request.items() if k != "mac"}
        if not hmac.compare_digest(request["mac"], hmac.new(policy.key, canonical(unsigned), "sha256").hexdigest()):
            raise D5Blocked("AUTHENTICATION_FAILED")
        if ingress_nonce is not None:
            if type(ingress_nonce) is not str or not _ID.fullmatch(ingress_nonce):
                raise D5Blocked("INPUT_INVALID")
            request = {**request, "nonce": ingress_nonce}
        self.native.preflight(policy)
        self.journal.reserve(request, now)
        self.native.request_id = request["request_id"]
        self.effect_returned = False
        op = request["op"]
        try:
            result = self.execute(policy, op, request["dry_run"])
        except D5Blocked as exc:
            # Failures after an effect may have an unknown outcome; adapter
            # raises UNKNOWN_EFFECT rather than translating it into a safe retry.
            code = str(exc) if str(exc) in PUBLIC_CODES else "NATIVE_FAILURE_HOLD"
            if self.effect_returned:
                code = "UNKNOWN_EFFECT_HOLD"
            result = receipt(op, code)
            result.update(request_id=request["request_id"], source_sha=policy.source_sha, epoch=policy.epoch)
            self.journal.complete(request["request_id"], result, unknown=code == "UNKNOWN_EFFECT_HOLD")
            return result
        except Exception:
            result = receipt(op, "UNKNOWN_EFFECT_HOLD")
            result.update(request_id=request["request_id"], source_sha=policy.source_sha, epoch=policy.epoch)
            self.journal.complete(request["request_id"], result, unknown=True)
            return result
        result.update(request_id=request["request_id"], source_sha=policy.source_sha, epoch=policy.epoch)
        self.journal.complete(request["request_id"], result)
        return result

    def observe(self, policy):
        snap = self.native.observe(policy)
        # native.observe includes signed exe, exact argv, owner SID/session and
        # creation time; only the root process can own the loopback port.
        if snap["state"] not in {"ABSENT", "OWNED", "PROFILE_ALREADY_ACTIVE_NO_CDP"}:
            raise D5Blocked("OWNERSHIP_UNPROVEN")
        if snap["state"] != "ABSENT" and (type(snap.get("pid")) is not int
                or not isinstance(snap.get("created"), str) or not snap["created"]):
            raise D5Blocked("OWNERSHIP_UNPROVEN")
        return snap

    def perform(self, policy, op, snapshot):
        value = self.native.effect(policy, op, snapshot)
        self.effect_returned = True
        return value

    def verify(self, policy, op, first=None, *, launched=False):
        first = first or self.observe(policy)
        if first["state"] == "PROFILE_ALREADY_ACTIVE_NO_CDP":
            return receipt(op, "PROFILE_ALREADY_ACTIVE_NO_CDP")
        if first["state"] == "ABSENT":
            return receipt(op, "ROOT_PROCESS_LOST" if self.journal.previous() else "NOT_RUNNING")
        identity = (first["pid"], first["created"])
        previous = self.journal.previous()
        self.native.attach(policy)
        self.sleep(0.5)
        later = self.observe(policy)
        if later["state"] == "ABSENT":
            return receipt(op, "ROOT_PROCESS_LOST", effect=launched, started=launched, bound=True, attached=True)
        if later["state"] == "PROFILE_ALREADY_ACTIVE_NO_CDP":
            return receipt(op, "CDP_LISTENER_LOST", effect=launched, started=launched, bound=True, attached=True)
        if (later["pid"], later["created"]) != identity:
            return receipt(op, "BROWSER_RESTARTED", effect=launched, started=launched, bound=True, attached=True)
        self.native.attach(policy)
        self.journal.healthy(identity)
        code = "BROWSER_RESTARTED" if previous and previous != identity and not launched else "READY_ATTACH"
        return receipt(op, code, effect=launched, started=launched, bound=True, attached=True, stable=True)

    def execute(self, policy, op, dry_run):
        self.native.inventory(policy)  # verify all 3 runner tasks + exact D5 metadata
        snapshot = self.observe(policy)
        if snapshot["state"] == "PROFILE_ALREADY_ACTIVE_NO_CDP" and op != "STOP_OWNED_D5_CHROME":
            return receipt(op, "PROFILE_ALREADY_ACTIVE_NO_CDP")
        if dry_run:
            return receipt(op, "DRY_RUN")
        if op == "MIGRATION_PREFLIGHT":
            return receipt(op, "MIGRATION_READY")
        if op in {"STATUS", "VERIFY_CDP_9333_AND_PLAYWRIGHT"}:
            self.native.no_conflicts(policy)
            return self.verify(policy, op, snapshot)
        if op == "STOP_OWNED_D5_CHROME":
            if snapshot["state"] == "ABSENT":
                return receipt(op, "ALREADY_STOPPED")
            self.perform(policy, op, snapshot)
            if self.observe(policy)["state"] != "ABSENT":
                raise D5Blocked("UNKNOWN_EFFECT_HOLD")
            return receipt(op, "STOPPED", effect=True)
        if op in {"START_OR_REUSE_D5_CHROME", "RECOVER_BOUNDED"}:
            if snapshot["state"] == "OWNED":
                return self.verify(policy, op, snapshot)
            self.native.no_conflicts(policy)
            if not policy.environment.allow_launch:
                raise D5Blocked("LAUNCH_NOT_AUTHORIZED")
            launched_identity = self.perform(policy, "START_OR_REUSE_D5_CHROME", snapshot)
            # Tolerate only this launch's short stabilization. Never launch twice.
            for _ in range(20):
                self.sleep(0.5)
                snapshot = self.observe(policy)
                if snapshot["state"] == "OWNED":
                    if (snapshot["pid"], snapshot["created"]) != launched_identity:
                        raise D5Blocked("UNKNOWN_EFFECT_HOLD")
                    return self.verify(policy, op, snapshot, launched=True)
            raise D5Blocked("UNKNOWN_EFFECT_HOLD")
        if snapshot["state"] != "ABSENT":
            raise D5Blocked("BROWSER_MUST_BE_STOPPED")
        if op in {"INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER", "INSTALL_SINGLE_LOGON_AUTOSTART"}:
            self.native.no_conflicts(policy)
        self.perform(policy, op, snapshot)
        self.native.inventory(policy)
        return receipt(op, {
            "RETIRE_NEW_AUTOSTART": "RETIRED", "INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER": "LAUNCHER_INSTALLED",
            "INSTALL_SINGLE_LOGON_AUTOSTART": "REGISTERED", "REMOVE_D5_AUTOSTART": "AUTOSTART_REMOVED",
            "ROLLBACK": "ROLLED_BACK_DISABLED",
        }[op], effect=True, registered=op == "INSTALL_SINGLE_LOGON_AUTOSTART")


PUBLIC_CODES = frozenset({
    "UNKNOWN_EFFECT_HOLD", "OWNERSHIP_UNPROVEN", "PROFILE_ALREADY_ACTIVE_NO_CDP",
    "BROWSER_MUST_BE_STOPPED", "CONFLICTING_D5_TASK", "TASK_OWNERSHIP_UNPROVEN",
    "RUNNER_PRESERVATION_UNPROVEN", "PROFILE_LOCKED", "PLAYWRIGHT_UNAVAILABLE",
    "CDP_PROTOCOL_UNAVAILABLE", "LAUNCHER_UNVERIFIED", "SOURCE_DRIFT",
    "UNSUPPORTED_SESSION", "NATIVE_FAILURE_HOLD", "BACKUP_UNVERIFIED",
    "LAUNCH_NOT_AUTHORIZED", "BLOCKED_NOT_DEPLOYED", "LOGON_TRIGGER_DELIVERED",
})


class WindowsNative:
    """Installed private adapter. Never instantiated by the current HostOps CLI.

    effect() communicates via stdin to a fixed reviewed PS entrypoint, never via
    a caller-supplied command, PID, path, environment or PowerShell expression.
    """
    def preflight(self, policy):
        try:
            self._preflight(policy)
        except D5Blocked:
            raise
        except Exception:
            raise D5Blocked("NATIVE_FAILURE_HOLD") from None

    def _preflight(self, policy):
        verify_release(policy)
        private_path(policy.state_root, directory=True)
        fixed_root = Path(os.environ.get("LOCALAPPDATA", "")) / "AI-Commerce" / "D5-Operator"
        if (policy.state_root.resolve() != fixed_root.resolve()
                or private_path(fixed_root / "control.key").read_bytes() != policy.key):
            raise D5Blocked("PRIVATE_STORAGE_UNPROVEN")
        ca._require_operator_control(policy.environment)
        ca._verify_google_chrome(policy.environment)
        try:
            session = ca._interactive_session()
        except ca.AcquisitionBlocked:
            raise D5Blocked("UNSUPPORTED_SESSION") from None
        from ai_commerce.remote.local_completion_handoff import _windows_process_sid
        if _windows_process_sid() != policy.sid or session <= 0:
            raise D5Blocked("UNSUPPORTED_SESSION")

    def observe(self, policy):
        env = policy.environment
        inventory = ca._query_windows(9333)
        try:
            state, pid = ca.evaluate(env, inventory, session_id=ca._interactive_session(),
                                     expected_sid=policy.sid)
        except ca.AcquisitionBlocked as exc:
            if str(exc) != "PROFILE_ALREADY_ACTIVE_NO_CDP":
                raise D5Blocked("OWNERSHIP_UNPROVEN") from None
            # Still prove identity before permitting any interpretation of a claim.
            try:
                claims = []
                for proc in ca._rows(inventory["processes"]):
                    argv = ca._commandline_argv(proc.get("CommandLine", ""))
                    if any(arg.startswith("--type=") for arg in argv):
                        continue
                    directory = ca._switch(argv, "--user-data-dir")
                    if directory and Path(directory).resolve(strict=True) == env.user_data_dir:
                        claims.append((proc, argv))
                if len(claims) != 1:
                    raise ValueError()
                proc, argv = claims[0]
                if not ca._identity_matches(env, proc.get("ExecutablePath", ""), argv):
                    raise ValueError()
            except Exception:
                raise D5Blocked("OWNERSHIP_UNPROVEN") from None
            if (proc.get("OwnerSid") != policy.sid or proc.get("SessionId") != ca._interactive_session()
                    or not proc.get("CreationDate")):
                raise D5Blocked("OWNERSHIP_UNPROVEN")
            return {"state": "PROFILE_ALREADY_ACTIVE_NO_CDP", "pid": proc["ProcessId"],
                    "created": proc["CreationDate"]}
        if state == "ABSENT":
            return {"state": state}
        proc = next(p for p in ca._rows(inventory["processes"]) if p["ProcessId"] == pid)
        return {"state": state, "pid": pid, "created": proc["CreationDate"]}

    def attach(self, policy):
        try:
            ca._probe_cdp(policy.environment)
            ca.verify_playwright_attach(policy.environment)
        except Exception:
            raise D5Blocked("PLAYWRIGHT_UNAVAILABLE") from None

    def call(self, policy, op, snapshot=None):
        self.preflight(policy)  # source/revocation checked by bridge; identity again at boundary
        payload = {"op": op, "request_id": self.request_id, "sid": policy.sid, "source_sha": policy.source_sha,
                   "files": policy.files, "root": str(policy.release_root),
                   "state": str(policy.state_root), "config": str(policy.environment.config_path),
                   "legacy_tasks": policy.legacy_tasks, "runner_tasks": policy.runner_tasks,
                   "snapshot": snapshot, "nonce": secrets.token_hex(16), "expires": int(time.time()) + 30}
        payload["chrome"] = {"executable": str(policy.environment.executable),
                             "user_data_dir": str(policy.environment.user_data_dir),
                             "profile_directory": policy.environment.profile_directory}
        body = canonical(payload)
        envelope = {"body": base64.b64encode(body).decode(),
                    "mac": hmac.new(policy.key, body, "sha256").hexdigest()}
        try:
            response = subprocess.run(
                [str(ca._powershell()), "-NoProfile", "-NonInteractive", "-File",
                 str(policy.release_root / RELEASE_FILES[2])], input=canonical(envelope),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45,
                env=ca._child_environment(), check=False)
            value = decode(response.stdout)
            if set(value) not in ({"code"}, {"code", "pid", "created"}) or value["code"] not in {"OK", *PUBLIC_CODES}:
                raise ValueError()
            if response.returncode or value["code"] != "OK":
                raise D5Blocked(value["code"] if value["code"] in PUBLIC_CODES else "UNKNOWN_EFFECT_HOLD")
            self.verify_native_receipt(policy, payload, value)
            if op == "START_OR_REUSE_D5_CHROME":
                if type(value.get("pid")) is not int or not isinstance(value.get("created"), str):
                    raise ValueError()
                return value["pid"], value["created"]
        except D5Blocked:
            raise
        except Exception:
            raise D5Blocked("UNKNOWN_EFFECT_HOLD") from None

    def inventory(self, policy):
        self.call(policy, "MIGRATION_PREFLIGHT")

    def no_conflicts(self, policy):
        self.call(policy, "CHECK_NO_CONFLICTS")

    def effect(self, policy, op, snapshot):
        return self.call(policy, op, snapshot)

    def verify_native_receipt(self, policy, payload, result):
        """Independent readback, not stdout success, completes the native gate."""
        try:
            path = private_path(policy.state_root / (payload["nonce"] + ".receipt.json"))
            with path.open("rb") as stream:
                audit = decode(stream.read(32769))
            if (set(audit) != {"request_id", "operation", "source_sha", "code", "native", "sid", "session",
                              "runner_before", "runner_after", "process_before", "process_result", "registered", "observed_utc"}
                    or audit["request_id"] != payload["request_id"] or audit["operation"] != payload["op"]
                    or audit["source_sha"] != policy.source_sha or audit["code"] != "OK" or audit["native"] is not True
                    or audit["sid"] != policy.sid or type(audit["session"]) is not int
                    or audit["session"] != ca._interactive_session() or audit["process_result"] != result
                    or audit["process_before"] != payload["snapshot"]
                    or type(audit["runner_before"]) is not dict or set(audit["runner_before"]) != set(RUNNER_TASKS)
                    or audit["runner_after"] != audit["runner_before"]
                    or any(type(v) is not str or not _HEX.fullmatch(v) for v in audit["runner_before"].values())):
                raise ValueError()
        except Exception:
            raise D5Blocked("UNKNOWN_EFFECT_HOLD") from None


def private_bridge(path: Path, *, authority) -> Bridge:
    """Private deployment factory; no callback or unsigned enable route."""
    def enrolled():
        return load_enrollment(path, authority=authority, now=int(time.time()))
    policy = enrolled()
    WindowsNative().preflight(policy)
    journal = policy.state_root / "journal.sqlite3"
    if journal.exists():
        private_path(journal)
    if not ca._no_links(journal):
        raise D5Blocked("PRIVATE_STORAGE_UNPROVEN")
    return Bridge(enrolled, WindowsNative(), Journal(journal))


D5_PIPE_NAME = "AI-Commerce-D5-Operator-v1"
D5_LOGON_TRIGGER_PAYLOAD = b"D5_LOGON_START_V1\n"
SUCCESS_CODES = frozenset({
    "OK", "DRY_RUN", "NOT_RUNNING", "ALREADY_STOPPED", "MIGRATION_READY", "RETIRED",
    "STOPPED", "LAUNCHER_INSTALLED", "READY_ATTACH", "REGISTERED", "AUTOSTART_REMOVED",
    "ROLLED_BACK_DISABLED", "LOGON_TRIGGER_DELIVERED",
})
BOUNDARY_CODES = frozenset({
    "INPUT_INVALID", "AUTHENTICATION_FAILED", "ENROLLMENT_UNVERIFIED",
    "PRIVATE_ENROLLMENT_REQUIRED", "ENROLLMENT_INVALID_OR_STALE", "REQUEST_INVALID_OR_STALE",
    "REQUEST_REPLAY", "UNKNOWN_EFFECT_HOLD", "UNTRUSTED_PEER_ATTESTATION", "PIPE_UNAVAILABLE",
    "JOURNAL_UNAVAILABLE", "JOURNAL_ROTATION_REQUIRED", "RECOVERY_RATE_LIMIT",
    "ENROLLMENT_REUSE_DENIED", "TRUST_ANCHOR_UNPROVEN", "REVOKED_OR_STALE",
    "SIGNATURE_INVALID", "CUSTODY_UNPROVEN", "PRIVATE_STORAGE_UNPROVEN",
    "INVALID_PUBLIC_RECEIPT", "ROOT_PROCESS_LOST", "CDP_LISTENER_LOST", "BROWSER_RESTARTED",
})
PUBLIC_RECEIPT_FIELDS = frozenset({
    "operation", "code", "status", "effect_observed", "registered", "started", "listener_bound",
    "playwright_attached", "stable", "wake_sent", "login_verified", "manifest_verified",
    "request_id", "source_sha", "epoch", "bridge_receipt",
})


def sanitize_receipt(result: dict) -> dict:
    """Validate VALUES as well as names; strings/nested data cannot leak via an enum."""
    invalid = {"status": "BLOCKED", "code": "INVALID_PUBLIC_RECEIPT", "effect_observed": False}
    if type(result) is not dict:
        return invalid
    out = {}
    for key in PUBLIC_RECEIPT_FIELDS & result.keys():
        value = result[key]
        if key == "operation":
            valid = type(value) is str and value in (*OPS, "LOGON_TRIGGER", "UNKNOWN")
        elif key == "code":
            valid = type(value) is str and value in SUCCESS_CODES | PUBLIC_CODES | BOUNDARY_CODES
        elif key == "status":
            valid = type(value) is str and value in {"BLOCKED", "OBSERVED"}
        elif key in {"request_id", "source_sha"}:
            valid = type(value) is str and bool(re.fullmatch(
                r"[0-9a-f]{32}" if key == "request_id" else r"[0-9a-f]{40}", value))
        elif key == "epoch":
            valid = type(value) is int and 1 <= value <= 2147483647
        elif key == "bridge_receipt":
            valid = type(value) is dict and "bridge_receipt" not in value
            if valid:
                value = sanitize_receipt(value)
                valid = value.get("code") != "INVALID_PUBLIC_RECEIPT"
        else:
            valid = type(value) is bool
            if key in {"wake_sent", "login_verified", "manifest_verified"}:
                valid = value is False
        if not valid:
            return invalid
        out[key] = value
    if "code" not in out:
        return invalid
    out.setdefault("status", "OBSERVED" if out["code"] in SUCCESS_CODES else "BLOCKED")
    return out


def blocked(code: str, op="UNKNOWN") -> dict:
    return sanitize_receipt({"status": "BLOCKED", "code": code,
                             "operation": op if type(op) is str and op in (*OPS, "LOGON_TRIGGER") else "UNKNOWN",
                             "effect_observed": False})


def create_blocked_not_deployed_evidence(*, op="UNKNOWN") -> dict:
    # The enrollment recipe lives in reviewed docs, never in a public free-text field.
    return blocked("BLOCKED_NOT_DEPLOYED", op)


class D5Receiver:
    """In-process core. Only the OS pipe adapter below can attest a logon.

    The bridge factory is a native/test adapter, not an authentication callback.
    Production constructs it only after loading a concrete SignedPolicy.
    """
    def __init__(self, bridge_or_factory=None, *, authority=None, clock=time.time):
        self._bridge_or_factory = bridge_or_factory
        self.authority, self.clock = authority, clock

    def get_bridge(self) -> Bridge:
        value = self._bridge_or_factory
        if callable(value):
            value = value()
        if not isinstance(value, Bridge):
            raise D5Blocked("BLOCKED_NOT_DEPLOYED")
        return value

    def handle_logon_trigger(self, trigger_bytes: bytes, client_attestation=None, *, _peer=None) -> dict:
        if trigger_bytes != D5_LOGON_TRIGGER_PAYLOAD:
            raise D5Blocked("INPUT_INVALID")
        # Wire/caller dictionaries are NEVER evidence, including well-shaped ones.
        if client_attestation is not None or type(_peer) is not TrustedPeer:
            raise D5Blocked("UNTRUSTED_PEER_ATTESTATION")
        try:
            bridge = self.get_bridge()
            policy = bridge.enrollment()
            policy.validate(int(self.clock()))
            _peer.consume(trigger_bytes, policy.sid)
            result = bridge.logon()
            return sanitize_receipt({"status": "OBSERVED", "code": "LOGON_TRIGGER_DELIVERED",
                "operation": "LOGON_TRIGGER", "effect_observed": result.get("effect_observed", False),
                "bridge_receipt": result})
        except D5Blocked as exc:
            if str(exc) in {"PRIVATE_ENROLLMENT_REQUIRED", "BLOCKED_NOT_DEPLOYED"}:
                return create_blocked_not_deployed_evidence(op="LOGON_TRIGGER")
            raise

    def handle_request(self, request: dict) -> dict:
        # No external HMAC route. Every wire request must have an off-host signature.
        return D5IngressBoundary(self, authority=self.authority, clock=self.clock).evaluate_ingress_request(request)


class D5IngressBoundary:
    """Concrete protected anchor + OpenSSH verification, never a boolean callback."""
    def __init__(self, receiver=None, *, authority=None, clock=time.time):
        self.receiver, self.authority, self.clock = receiver, authority, clock

    def evaluate_ingress_request(self, request_payload: dict) -> dict:
        op = "UNKNOWN"
        try:
            body = request_shape(request_payload)
            op = body["op"]
            if type(self.authority) is not SignedPolicy or self.receiver is None:
                return create_blocked_not_deployed_evidence(op=op)
            grant = self.authority.authorize(request_payload, int(self.clock()))
            bridge = self.receiver.get_bridge()
            policy = bridge.enrollment()  # Reload signed revocation again immediately before intent.
            if (policy.source_sha != body["source_sha"] or policy.epoch != body["epoch"]
                    or digest(canonical(self.authority.native_policy(grant))) != self.authority.native_digest):
                raise D5Blocked("SOURCE_DRIFT")
            bridge.journal.pin_enrollment(grant["enrollment_id"], digest(canonical(grant)))
            internal = sign_request(policy, body["op"], body["request_id"], body["issued"],
                                    dry_run=body["dry_run"])
            # Preserve the externally signed deadline (never extend it at the receiver).
            internal["expires"] = body["expires"]
            unsigned = {k: v for k, v in internal.items() if k != "mac"}
            internal["mac"] = hmac.new(policy.key, canonical(unsigned), "sha256").hexdigest()
            return sanitize_receipt(bridge.invoke(internal, ingress_nonce=body["nonce"]))
        except D5Blocked as exc:
            code = str(exc)
            if code in {"TRUST_ANCHOR_UNPROVEN", "PRIVATE_STORAGE_UNPROVEN", "CUSTODY_UNPROVEN", "PRIVATE_ENROLLMENT_REQUIRED"}:
                code = "BLOCKED_NOT_DEPLOYED"
            return blocked(code, op)
        except Exception:
            return blocked("UNKNOWN_EFFECT_HOLD", op)


# Concrete authenticated transport and native pipe adapter share this registered owner.


REQUEST_SCHEMA = "ai-commerce.d5.request.v1"
GRANT_SCHEMA = "ai-commerce.d5.enrollment.v1"
LEASE_SCHEMA = "ai-commerce.d5.custody-lease.v1"
_KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]{68}={0,2}\Z")
_IDENTITY = re.compile(r"d5-[a-z0-9-]{1,48}\Z")
_TRUSTED_OWNERS = {"S-1-5-18", "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"}  # TrustedInstaller


def fixed_state() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "AI-Commerce" / "D5-Operator"


def fixed_anchor() -> Path:
    return Path(os.environ.get("ProgramFiles", "")) / "AI-Commerce" / "D5-Trust" / "anchor.json"


def _sid_text(pointer) -> str:
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    value = w.LPWSTR()
    if not adv.ConvertSidToStringSidW(pointer, ctypes.byref(value)):
        raise D5Blocked("TRUST_ANCHOR_UNPROVEN")
    try:
        return value.value
    finally:
        kernel.LocalFree(value)


def protected_path(path: Path) -> Path:
    """Admin/SYSTEM-owned, no untrusted writable ACEs, no reparse ancestors.

    Same-SID operator ACLs are deliberately insufficient for a trust anchor.
    This does not prove execution isolation; the independent authority must
    additionally attest custody and runtime isolation on every fresh lease.
    """
    try:
        if os.name != "nt" or not path.is_absolute() or not ca._no_links(path):
            raise ValueError()
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, w.DWORD, w.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        adv.GetNamedSecurityInfoW.restype = w.DWORD
        adv.GetAce.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        adv.GetAce.restype = w.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        # The file and installation directories must all resist replacement.
        for index, item in enumerate((path, *path.parents)):
            owner, acl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
            if adv.GetNamedSecurityInfoW(str(item), 1, 5, ctypes.byref(owner), None,
                    ctypes.byref(acl), None, ctypes.byref(sd)):
                raise ValueError()
            try:
                if not acl.value or _sid_text(owner) not in _TRUSTED_OWNERS:
                    raise ValueError()
                count = ctypes.c_ushort.from_address(acl.value + 4).value
                if count > 128:
                    raise ValueError()
                for index in range(count):
                    ace = ctypes.c_void_p()
                    if not adv.GetAce(acl, index, ctypes.byref(ace)):
                        raise ValueError()
                    kind = ctypes.c_ubyte.from_address(ace.value).value
                    flags = ctypes.c_ubyte.from_address(ace.value + 1).value
                    if flags & 8 or kind == 1:  # inherit-only or deny ACE
                        continue
                    if kind != 0:  # No opaque conditional/object allow ACE assumptions.
                        raise ValueError()
                    mask = w.DWORD.from_address(ace.value + 4).value
                    # Write data/add children, delete/replace, WRITE_DAC/OWNER and generic write/all.
                    forbidden = 0x500D0156 if index < 2 else 0x500D0040
                    if mask & forbidden and _sid_text(ace.value + 8) not in _TRUSTED_OWNERS:
                        raise ValueError()
            finally:
                kernel.LocalFree(sd)
        return path.resolve(strict=True)
    except Exception:
        raise D5Blocked("TRUST_ANCHOR_UNPROVEN") from None


def _public_key(key: str) -> str:
    if type(key) is not str or not _KEY.fullmatch(key):
        raise D5Blocked("SIGNATURE_INVALID")
    try:
        wire = base64.b64decode(key.split()[1], validate=True)
        if len(wire) != 51 or wire[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
            raise ValueError()
    except ValueError:
        raise D5Blocked("SIGNATURE_INVALID") from None
    return key


def verify(envelope: dict, *, public_key: str, identity: str, namespace: str, scratch: Path) -> dict:
    """Concrete OpenSSH verifier; no injectable boolean authentication hook."""
    try:
        if (type(envelope) is not dict or set(envelope) != {"body", "signature"}
                or type(envelope["body"]) is not dict or type(envelope["signature"]) is not str
                or not _IDENTITY.fullmatch(identity)):
            raise ValueError()
        key = _public_key(public_key)
        signature = base64.b64decode(envelope["signature"], validate=True)
        # Windows OpenSSH can emit CRLF armor. Accept either exact line ending;
        # keep the signed body and signature bytes unchanged for real verification.
        headers = (b"-----BEGIN SSH SIGNATURE-----\n", b"-----BEGIN SSH SIGNATURE-----\r\n")
        if not 32 <= len(signature) <= 4096 or not signature.startswith(headers):
            raise ValueError()
        executable = protected_path(Path(os.environ.get("SystemRoot", "")) / "System32" / "OpenSSH" / "ssh-keygen.exe")
        # PUBLIC signature and allowed-signers only; temp is explicitly bounded.
        private_path(scratch, directory=True)
        with tempfile.TemporaryDirectory(prefix="d5-public-verify-", dir=scratch) as folder:
            allowed, sig = Path(folder) / "allowed", Path(folder) / "signature"
            allowed.write_text(f'{identity} namespaces="{namespace}" {key}\n', encoding="ascii")
            sig.write_bytes(signature)
            result = subprocess.run([str(executable), "-Y", "verify", "-f", str(allowed),
                "-I", identity, "-n", namespace, "-s", str(sig)], input=canonical(envelope["body"]),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                # Windows OpenSSH needs PROGRAMDATA even for offline verification.
                # Use the existing minimal signature allowlist, not the browser's.
                env=signature_environment(), check=False)
        if result.returncode:
            raise ValueError()
        return envelope["body"]
    except Exception:
        raise D5Blocked("SIGNATURE_INVALID") from None


def request_shape(envelope: dict) -> dict:
    if type(envelope) is not dict or set(envelope) != {"body", "signature"}:
        raise D5Blocked("INPUT_INVALID")
    body = envelope["body"]
    fields = {"schema", "op", "request_id", "nonce", "issued", "expires", "source_sha",
              "epoch", "enrollment_id", "issue_number", "dry_run"}
    if type(body) is not dict or set(body) != fields:
        raise D5Blocked("INPUT_INVALID")
    if (body["schema"] != REQUEST_SCHEMA or type(body["op"]) is not str or body["op"] not in OPS
            or type(body["dry_run"]) is not bool
            or any(type(body[k]) is not str or not _ID.fullmatch(body[k])
                   for k in ("request_id", "nonce", "enrollment_id"))
            or type(body["source_sha"]) is not str or not re.fullmatch(r"[0-9a-f]{40}", body["source_sha"])
            or any(type(body[k]) is not int for k in ("epoch", "issued", "expires", "issue_number"))
            or not 1 <= body["epoch"] <= 2147483647 or not 1 <= body["issue_number"] <= 2147483647
            or type(envelope["signature"]) is not str or len(envelope["signature"]) > 6000):
        raise D5Blocked("INPUT_INVALID")
    return body


class SignedPolicy:
    """Concrete root-signed one-time grant plus <=60s revocation/custody lease.

    The authority is outside both public repositories and all three runner
    SIDs. A signed audit digest must attest a real independent execution
    boundary too; same-SID ACLs alone cannot satisfy this statement.
    """
    def __init__(self):
        self.state = fixed_state()
        self.path = self.state / "enrollment.json"
        self.native_digest = None

    @staticmethod
    def native_policy(grant):
        return grant["native"]

    def _grant(self, now: int) -> dict:
        anchor = decode(protected_path(fixed_anchor()).read_bytes())
        if (set(anchor) != {"schema", "identity", "public_key"}
                or anchor["schema"] != "ai-commerce.d5.root.v1"):
            raise D5Blocked("TRUST_ANCHOR_UNPROVEN")
        public = _public_key(anchor["public_key"])
        private_path(self.state, directory=True)
        grant = verify(decode(private_path(self.path).read_bytes()), public_key=public,
            identity=anchor["identity"], namespace="ai-commerce-d5-enrollment-v1", scratch=self.state)
        fields = {"schema", "enrollment_id", "issued", "expires", "issuer", "issuer_public_key", "custody", "native"}
        if (set(grant) != fields or grant["schema"] != GRANT_SCHEMA
                or type(grant["enrollment_id"]) is not str or not _ID.fullmatch(grant["enrollment_id"])
                or type(grant["issued"]) is not int or type(grant["expires"]) is not int
                or not grant["issued"] <= now < grant["expires"] <= grant["issued"] + 86400
                or type(grant["issuer"]) is not str or not _IDENTITY.fullmatch(grant["issuer"])
                or type(grant["native"]) is not dict):
            raise D5Blocked("ENROLLMENT_UNVERIFIED")
        _public_key(grant["issuer_public_key"])
        custody = grant["custody"]
        if (type(custody) is not dict or set(custody) != {"signer", "operator", "audit_sha256"}
                or custody["signer"] != "OFF_HOST_PRIVATE"
                or custody["operator"] != "INDEPENDENT_EXECUTION_BOUNDARY"
                or type(custody["audit_sha256"]) is not str or not _HEX.fullmatch(custody["audit_sha256"])):
            raise D5Blocked("CUSTODY_UNPROVEN")
        lease = verify(decode(private_path(self.state / "custody-lease.json").read_bytes()),
            public_key=public, identity=anchor["identity"], namespace="ai-commerce-d5-custody-v1", scratch=self.state)
        fields = {"schema", "enrollment_id", "source_sha", "epoch", "grant_sha256", "serial", "issued", "expires",
                  "revoked", "signer_exposed", "runtime_exposed"}
        if (set(lease) != fields or lease["schema"] != LEASE_SCHEMA
                or lease["enrollment_id"] != grant["enrollment_id"]
                or lease["source_sha"] != grant["native"].get("source_sha")
                or type(lease["epoch"]) is not int or lease["epoch"] != grant["native"].get("epoch")
                or lease["grant_sha256"] != digest(canonical(grant))
                or type(lease["serial"]) is not int or not 1 <= lease["serial"] <= 9223372036854775807
                or type(lease["issued"]) is not int or type(lease["expires"]) is not int
                or not now - 60 <= lease["issued"] <= now < lease["expires"] <= lease["issued"] + 60
                or any(type(lease[k]) is not bool for k in ("revoked", "signer_exposed", "runtime_exposed"))):
            raise D5Blocked("REVOKED_OR_STALE")
        denied = any(lease[k] for k in ("revoked", "signer_exposed", "runtime_exposed"))
        native = grant["native"]
        if (native.get("public_runner_exposed") is not False or native.get("private_control_plane") is not True
                or native.get("enabled") is not True or native.get("revoked") is not False
                or native.get("state_root") != str(self.state)):
            raise D5Blocked("CUSTODY_UNPROVEN")
        journal = self.state / "authority.sqlite3"
        if journal.exists():
            private_path(journal)
        if not ca._no_links(journal):
            raise D5Blocked("PRIVATE_STORAGE_UNPROVEN")
        with sqlite3.connect(journal, timeout=1) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS pin (id INTEGER PRIMARY KEY CHECK(id=1), grant TEXT, serial INTEGER, denied INTEGER)")
            db.execute("BEGIN IMMEDIATE")
            pin = db.execute("SELECT grant,serial,denied FROM pin WHERE id=1").fetchone()
            grant_hash = digest(canonical(grant))
            if pin and (pin[0] != grant_hash or lease["serial"] < pin[1] or pin[2]):
                raise D5Blocked("ENROLLMENT_REUSE_DENIED")
            db.execute("INSERT OR REPLACE INTO pin VALUES (1,?,?,?)", (grant_hash, lease["serial"], int(denied)))
        if denied:
            raise D5Blocked("REVOKED_OR_STALE")
        return grant

    def verify(self, path: Path, now: int) -> dict:
        if path != self.path:
            raise D5Blocked("ENROLLMENT_UNVERIFIED")
        grant = self._grant(now)
        self.native_digest = digest(canonical(grant["native"]))
        return grant["native"]

    def authorize(self, request: dict, now: int) -> dict:
        body = request_shape(request)
        grant = self._grant(now)
        if (body["source_sha"] != grant["native"].get("source_sha")
                or body["epoch"] != grant["native"].get("epoch")
                or body["enrollment_id"] != grant["enrollment_id"]
                or not now - 60 <= body["issued"] <= now < body["expires"] <= body["issued"] + 60):
            raise D5Blocked("REQUEST_INVALID_OR_STALE")
        verify(request, public_key=grant["issuer_public_key"], identity=grant["issuer"],
               namespace="ai-commerce-d5-request-v1", scratch=self.state)
        return grant


def prepare_event(event: dict) -> dict:
    """GitHub is a carrier only. It cannot enroll or authorize an effect."""
    try:
        issue, repo = event["issue"], event["repository"]
        if (repo["full_name"] != "trung-via/ai-commerce" or repo["owner"]["login"] != "trung-via"
                or issue["user"]["login"] != "trung-via" or "pull_request" in issue
                or not issue["title"].startswith("[HOSTOPS D5] ")):
            raise ValueError()
        if event.get("action") == "created" and "comment" in event:
            if event["comment"]["user"]["login"] != "trung-via":
                raise ValueError()
            raw = event["comment"]["body"]
        elif event.get("action") == "opened" and "comment" not in event:
            raw = issue["body"]
        else:
            raise ValueError()
        request = decode(raw.encode("utf-8"))
        if request_shape(request)["issue_number"] != issue["number"]:
            raise ValueError()
        if os.environ.get("GITHUB_ACTIONS") == "true" and request["body"]["source_sha"] != os.environ.get("GITHUB_SHA"):
            raise ValueError()
        return request
    except Exception:
        raise D5Blocked("INPUT_INVALID") from None


def _read(path: Path, limit=32768) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise D5Blocked("INPUT_INVALID")
    return raw


def transport_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-event", type=Path)
    mode.add_argument("--request", type=Path)
    mode.add_argument("--native-preflight", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.native_preflight:
        try:
            native_preflight(decode(sys.stdin.buffer.read(32769)))
            print('{"code":"OK"}')
            return 0
        except Exception:
            print('{"code":"BLOCKED_NOT_DEPLOYED"}')
            return 2
    if args.output is None:
        parser.error("--output is required for transport modes")
    try:
        output = args.output.open("xb")  # Reserve evidence BEFORE any delivery.
    except OSError:
        return 2
    try:
        if args.prepare_event:
            value = prepare_event(decode(_read(args.prepare_event)))
        else:
            request = decode(_read(args.request))
            request_shape(request)
            # The operator independently reloads policy and verifies signatures.
            # No private key, local HMAC, or public-runner credential is read here.
            value = sanitize_receipt(decode(pipe_exchange(canonical(request))))
            body = request["body"]
            if value.get("status") == "OBSERVED" and any(value.get(k) != body[k]
                    for k in ("request_id", "source_sha", "epoch")):
                value = blocked("INVALID_PUBLIC_RECEIPT", body["op"])
        output.write(canonical(value))
        output.flush()
        os.fsync(output.fileno())
        return 0 if args.prepare_event or value.get("status") == "OBSERVED" else 2
    except Exception as exc:
        code = str(exc) if isinstance(exc, D5Blocked) else "PIPE_UNAVAILABLE"
        try:
            if output.tell() == 0:
                output.write(canonical(blocked(code)))
        except OSError:
            pass
        return 2
    finally:
        output.close()


def native_preflight(payload: dict) -> None:
    """Fixed protected-runtime gate, before a native MAC can authorize anything.

    The MAC is internal integrity only. A shared-SID key NEVER substitutes for
    a fresh, independently signed runtime/custody proof.
    """
    authority = SignedPolicy()
    policy = load_enrollment(authority.path, authority=authority, now=int(time.time()))
    WindowsNative().preflight(policy)
    expected = {"sid": policy.sid, "source_sha": policy.source_sha, "files": policy.files,
        "root": str(policy.release_root), "state": str(policy.state_root),
        "config": str(policy.environment.config_path), "runner_tasks": list(policy.runner_tasks),
        "legacy_tasks": list(policy.legacy_tasks), "chrome": {
            "executable": str(policy.environment.executable), "user_data_dir": str(policy.environment.user_data_dir),
            "profile_directory": policy.environment.profile_directory}}
    if (set(payload) != set(expected) | {"op", "request_id", "snapshot", "nonce", "expires"}
            or any(payload.get(k) != v for k, v in expected.items())
            or type(payload["op"]) is not str or payload["op"] not in (*OPS, "CHECK_NO_CONFLICTS")
            or any(type(payload[k]) is not str or not _ID.fullmatch(payload[k]) for k in ("nonce", "request_id"))
            or type(payload["expires"]) is not int or not int(time.time()) < payload["expires"] <= int(time.time()) + 30):
        raise D5Blocked("BLOCKED_NOT_DEPLOYED")
    journal = private_path(policy.state_root / "journal.sqlite3")
    # Read-only: the native gate cannot create an intent or authorize a retry.
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        intent = db.execute("SELECT op,state FROM requests WHERE id=?", (payload["request_id"],)).fetchone()
    if (not intent or intent[1] != "INTENT" or (payload["op"] not in {"MIGRATION_PREFLIGHT", "CHECK_NO_CONFLICTS", intent[0]}
            and (intent[0], payload["op"]) != ("RECOVER_BOUNDED", "START_OR_REUSE_D5_CHROME"))):
        raise D5Blocked("UNKNOWN_EFFECT_HOLD")




_SEAL = object()
_PIPE = "\\\\.\\pipe\\" + D5_PIPE_NAME


class TrustedPeer:
    """Single-frame, single-use capability minted after kernel token checks."""
    def __init__(self, seal, frame, sid, session, *, expires):
        if seal is not _SEAL:
            raise D5Blocked("UNTRUSTED_PEER_ATTESTATION")
        self._seal, self._digest = seal, digest(frame)
        self._sid, self._session, self._expires = sid, session, expires
        self._used = False

    def consume(self, frame, sid):
        if (self._used or self._seal is not _SEAL or self._digest != digest(frame)
                or self._sid != sid or self._session <= 0 or time.monotonic() > self._expires):
            raise D5Blocked("UNTRUSTED_PEER_ATTESTATION")
        self._used = True


def _same_operator(current, peer, kernel_session):
    # These tuples come exclusively from GetTokenInformation, never JSON.
    if (type(current) is not tuple or type(peer) is not tuple or len(current) != 3 or len(peer) != 3
            or type(current[1]) is not int or current[1] <= 0 or current[2] is not False
            or peer != current or type(peer[1]) is not int or peer[2] is not False
            or type(kernel_session) is not int or kernel_session != current[1]):
        raise D5Blocked("UNSUPPORTED_SESSION")


class Win32:
    """All HANDLE signatures are explicit (including 64-bit Windows)."""
    def __init__(self):
        if os.name != "nt":
            raise D5Blocked("UNSUPPORTED_SESSION")
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.t = ctypes.WinDLL("wtsapi32", use_last_error=True)
        signatures = [
            (self.k, "CreateNamedPipeW", w.HANDLE, [w.LPCWSTR,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,ctypes.c_void_p]),
            (self.k, "CreateFileW", w.HANDLE, [w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]),
            (self.k, "WaitNamedPipeW", w.BOOL, [w.LPCWSTR,w.DWORD]),
            (self.k, "SetNamedPipeHandleState", w.BOOL, [w.HANDLE,ctypes.POINTER(w.DWORD),ctypes.c_void_p,ctypes.c_void_p]),
            (self.k, "CreateEventW", w.HANDLE, [ctypes.c_void_p,w.BOOL,w.BOOL,w.LPCWSTR]),
            (self.k, "CloseHandle", w.BOOL, [w.HANDLE]),
            (self.k, "LocalFree", w.HANDLE, [ctypes.c_void_p]),
            (self.k, "ConnectNamedPipe", w.BOOL, [w.HANDLE,ctypes.c_void_p]),
            (self.k, "DisconnectNamedPipe", w.BOOL, [w.HANDLE]),
            (self.k, "ReadFile", w.BOOL, [w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]),
            (self.k, "WriteFile", w.BOOL, [w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]),
            (self.k, "WaitForSingleObject", w.DWORD, [w.HANDLE,w.DWORD]),
            (self.k, "GetOverlappedResult", w.BOOL, [w.HANDLE,ctypes.c_void_p,ctypes.POINTER(w.DWORD),w.BOOL]),
            (self.k, "CancelIoEx", w.BOOL, [w.HANDLE,ctypes.c_void_p]),
            (self.k, "GetNamedPipeClientProcessId", w.BOOL, [w.HANDLE,ctypes.POINTER(w.DWORD)]),
            (self.k, "GetNamedPipeClientSessionId", w.BOOL, [w.HANDLE,ctypes.POINTER(w.DWORD)]),
            (self.k, "GetNamedPipeServerProcessId", w.BOOL, [w.HANDLE,ctypes.POINTER(w.DWORD)]),
            (self.k, "GetCurrentProcess", w.HANDLE, []),
            (self.k, "GetCurrentThread", w.HANDLE, []),
            (self.k, "ProcessIdToSessionId", w.BOOL, [w.DWORD,ctypes.POINTER(w.DWORD)]),
            (self.k, "OpenProcess", w.HANDLE, [w.DWORD,w.BOOL,w.DWORD]),
            (self.k, "QueryFullProcessImageNameW", w.BOOL, [w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]),
            (self.k, "GetExitCodeProcess", w.BOOL, [w.HANDLE,ctypes.POINTER(w.DWORD)]),
            (self.a, "OpenProcessToken", w.BOOL, [w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]),
            (self.a, "OpenThreadToken", w.BOOL, [w.HANDLE,w.DWORD,w.BOOL,ctypes.POINTER(w.HANDLE)]),
            (self.a, "GetTokenInformation", w.BOOL, [w.HANDLE,w.DWORD,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]),
            (self.a, "ImpersonateNamedPipeClient", w.BOOL, [w.HANDLE]),
            (self.a, "RevertToSelf", w.BOOL, []),
            (self.a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL,
             [w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]),
            (self.u, "OpenInputDesktop", w.HANDLE, [w.DWORD,w.BOOL,w.DWORD]),
            (self.u, "CloseDesktop", w.BOOL, [w.HANDLE]),
            (self.u, "GetUserObjectInformationW", w.BOOL, [w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]),
            (self.u, "GetUserObjectSecurity", w.BOOL, [w.HANDLE,ctypes.POINTER(w.DWORD),ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]),
            (self.a, "GetSecurityDescriptorOwner", w.BOOL, [ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(w.BOOL)]),
            (self.u, "GetThreadDesktop", w.HANDLE, [w.DWORD]),
            (self.k, "CreateToolhelp32Snapshot", w.HANDLE, [w.DWORD,w.DWORD]),
            (self.k, "Thread32First", w.BOOL, [w.HANDLE,ctypes.c_void_p]),
            (self.k, "Thread32Next", w.BOOL, [w.HANDLE,ctypes.c_void_p]),
            (self.t, "WTSQuerySessionInformationW", w.BOOL, [w.HANDLE,w.DWORD,ctypes.c_int,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(w.DWORD)]),
            (self.t, "WTSFreeMemory", None, [ctypes.c_void_p]),
        ]
        for lib, name, result, args in signatures:
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args

    def token(self, handle):
        def info(kind):
            buffer, size = ctypes.create_string_buffer(4096), w.DWORD()
            if not self.a.GetTokenInformation(handle, kind, buffer, len(buffer), ctypes.byref(size)):
                raise D5Blocked("UNSUPPORTED_SESSION")
            return buffer
        user = info(1)
        sid = _sid_text(ctypes.c_void_p.from_buffer(user))
        return sid, w.DWORD.from_buffer(info(12)).value, w.DWORD.from_buffer(info(20)).value != 0

    def process_token(self, process):
        token = w.HANDLE()
        if not self.a.OpenProcessToken(process, 8, ctypes.byref(token)):
            raise D5Blocked("UNSUPPORTED_SESSION")
        try:
            return self.token(token)
        finally:
            self.k.CloseHandle(token)

    def desktop(self, desktop, sid):
        name, returned = ctypes.create_unicode_buffer(256), w.DWORD()
        if (not desktop or not self.u.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(returned))
                or name.value != "Default"):
            raise D5Blocked("UNSUPPORTED_SESSION")
        security, count, owner, defaulted = w.DWORD(1), w.DWORD(), ctypes.c_void_p(), w.BOOL()
        buffer = ctypes.create_string_buffer(8192)
        if (not self.u.GetUserObjectSecurity(desktop, ctypes.byref(security), buffer, len(buffer), ctypes.byref(count))
                or not self.a.GetSecurityDescriptorOwner(buffer, ctypes.byref(owner), ctypes.byref(defaulted))
                or _sid_text(owner) != sid):
            raise D5Blocked("UNSUPPORTED_SESSION")

    def peer_desktop(self, pid, sid):
        class Thread(ctypes.Structure):
            _fields_ = [("size",w.DWORD),("usage",w.DWORD),("tid",w.DWORD),("pid",w.DWORD),
                        ("base",w.LONG),("delta",w.LONG),("flags",w.DWORD)]
        snap = self.k.CreateToolhelp32Snapshot(4, 0)
        if snap in (None, ctypes.c_void_p(-1).value):
            raise D5Blocked("UNSUPPORTED_SESSION")
        found = False
        try:
            row = Thread()
            row.size = ctypes.sizeof(row)
            more = self.k.Thread32First(snap, ctypes.byref(row))
            for _ in range(65536):
                if not more:
                    break
                if row.pid == pid:
                    found = True
                    self.desktop(self.u.GetThreadDesktop(row.tid), sid)
                more = self.k.Thread32Next(snap, ctypes.byref(row))
            else:
                raise D5Blocked("UNSUPPORTED_SESSION")
            if not found:
                raise D5Blocked("UNSUPPORTED_SESSION")
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise D5Blocked("UNSUPPORTED_SESSION")
        finally:
            self.k.CloseHandle(snap)

    def identity(self, pipe, *, server=False, logon=False):
        """Read the connected kernel peer, never a PID or token in the message."""
        current = self.process_token(self.k.GetCurrentProcess())
        sid, session, elevated = current
        if elevated or session <= 0 or ca._interactive_session() != session:
            raise D5Blocked("UNSUPPORTED_SESSION")
        state, size = ctypes.c_void_p(), w.DWORD()
        if not self.t.WTSQuerySessionInformationW(None, session, 8, ctypes.byref(state), ctypes.byref(size)):
            raise D5Blocked("UNSUPPORTED_SESSION")
        try:
            if size.value != 4 or w.DWORD.from_address(state.value).value != 0:  # WTSActive
                raise D5Blocked("UNSUPPORTED_SESSION")
        finally:
            self.t.WTSFreeMemory(state)
        desktop = self.u.OpenInputDesktop(0, False, 0x20001)  # READ_CONTROL | DESKTOP_READOBJECTS
        try:
            self.desktop(desktop, sid)
        finally:
            if desktop:
                self.u.CloseDesktop(desktop)
        pid, peer_session = w.DWORD(), w.DWORD()
        fn = self.k.GetNamedPipeServerProcessId if server else self.k.GetNamedPipeClientProcessId
        if (not fn(pipe, ctypes.byref(pid)) or not self.k.ProcessIdToSessionId(pid, ctypes.byref(peer_session))
                or peer_session.value != session):
            raise D5Blocked("UNSUPPORTED_SESSION")
        if not server:
            actual = w.DWORD()
            if not self.k.GetNamedPipeClientSessionId(pipe, ctypes.byref(actual)) or actual.value != session:
                raise D5Blocked("UNSUPPORTED_SESSION")
        process = self.k.OpenProcess(0x1000, False, pid)
        if not process:
            raise D5Blocked("UNSUPPORTED_SESSION")
        try:
            _same_operator(current, self.process_token(process), peer_session.value)
            if server:
                image, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
                expected = Path(os.environ.get("ProgramFiles", "")) / "AI-Commerce" / "D5-Runtime" / "python.exe"
                if (not self.k.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(length))
                        or Path(image.value).resolve() != protected_path(expected)):
                    raise D5Blocked("TRUST_ANCHOR_UNPROVEN")
            elif logon:
                self.logon_launcher(pid.value)
            self.peer_desktop(pid.value, sid)
            if not server:
                if not self.a.ImpersonateNamedPipeClient(pipe):
                    raise D5Blocked("UNSUPPORTED_SESSION")
                token = w.HANDLE()
                try:
                    if not self.a.OpenThreadToken(self.k.GetCurrentThread(), 8, True, ctypes.byref(token)):
                        raise D5Blocked("UNSUPPORTED_SESSION")
                    if self.token(token) != current:
                        raise D5Blocked("UNSUPPORTED_SESSION")
                finally:
                    if token:
                        self.k.CloseHandle(token)
                    if not self.a.RevertToSelf():
                        # Never continue in an unexpected security context.
                        raise D5Blocked("UNSUPPORTED_SESSION")
            code = w.DWORD()
            if not self.k.GetExitCodeProcess(process, ctypes.byref(code)) or code.value != 259:
                raise D5Blocked("UNSUPPORTED_SESSION")
        finally:
            self.k.CloseHandle(process)
        return sid, session

    def logon_launcher(self, pid):
        """Kernel-selected peer must be the exact source-pinned logon action.

        Query only this OS peer using fixed read-only source, never a supplied
        PID or command. Process handle remains pinned throughout this check.
        """
        try:
            authority = SignedPolicy()
            policy = load_enrollment(authority.path, authority=authority, now=int(time.time()))
            launcher = private_path(policy.state_root / "launcher.ps1")
            if digest(launcher.read_bytes()) != policy.files[RELEASE_FILES[2]]:
                raise ValueError()
            executable = protected_path(ca._powershell())
            child = ca._child_environment()
            child["AI_COMMERCE_D5_PEER_PID"] = str(pid)
            script = ("$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$env:AI_COMMERCE_D5_PEER_PID);"
                      "if($null -eq $p){exit 2};"
                      "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
                      "@{image=$p.ExecutablePath;argv=$p.CommandLine}|ConvertTo-Json -Compress")
            result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=child, timeout=5, check=False)
            if result.returncode or len(result.stdout) > 8192:
                raise ValueError()
            proc = decode(result.stdout)
            if set(proc) != {"image", "argv"} or Path(proc["image"]).resolve() != executable:
                raise ValueError()
            argv = ca._commandline_argv(proc["argv"])
            expected = ["-noprofile", "-noninteractive", "-file", str(launcher).casefold(), "-logon"]
            if len(argv) != 6 or Path(argv[0]).resolve() != executable or [a.casefold() for a in argv[1:]] != expected:
                raise ValueError()
        except Exception:
            raise D5Blocked("UNTRUSTED_PEER_ATTESTATION") from None

    def io(self, handle, action, data=None, *, timeout=2000, maximum=32768):
        class Overlapped(ctypes.Structure):
            _fields_ = [("internal",ctypes.c_size_t),("high",ctypes.c_size_t),
                        ("offset",w.DWORD),("offset_high",w.DWORD),("event",w.HANDLE)]
        ov = Overlapped()
        ov.event = self.k.CreateEventW(None, True, False, None)
        if not ov.event:
            raise D5Blocked("PIPE_UNAVAILABLE")
        count = w.DWORD()
        buffer = ctypes.create_string_buffer(data) if data is not None else ctypes.create_string_buffer(maximum)
        try:
            if action == "connect":
                ok = self.k.ConnectNamedPipe(handle, ctypes.byref(ov))
            elif action == "read":
                ok = self.k.ReadFile(handle, buffer, maximum, ctypes.byref(count), ctypes.byref(ov))
            else:
                ok = self.k.WriteFile(handle, buffer, len(data), ctypes.byref(count), ctypes.byref(ov))
            if not ok:
                error = ctypes.get_last_error()
                if action == "connect" and error == 535:  # Already connected.
                    return b""
                if error != 997:
                    raise D5Blocked("PIPE_UNAVAILABLE")
                if self.k.WaitForSingleObject(ov.event, timeout) != 0:
                    raise D5Blocked("PIPE_WAIT_TIMEOUT" if action == "connect" else "PIPE_UNAVAILABLE")
                if not self.k.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(count), False):
                    raise D5Blocked("PIPE_UNAVAILABLE")
            if action == "write" and count.value != len(data):
                raise D5Blocked("UNKNOWN_EFFECT_HOLD")
            return buffer.raw[:count.value]
        finally:
            # Drain canceled I/O before releasing the OVERLAPPED/buffer memory.
            self.k.CancelIoEx(handle, ctypes.byref(ov))
            self.k.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(count), True)
            self.k.CloseHandle(ov.event)

    def create(self, sid):
        class Security(ctypes.Structure):
            _fields_ = [("length",w.DWORD),("descriptor",ctypes.c_void_p),("inherit",w.BOOL)]
        sd = ctypes.c_void_p()
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"D:P(A;;GA;;;SY)(A;;GRGW;;;{sid})", 1, ctypes.byref(sd), None):
            raise D5Blocked("PIPE_UNAVAILABLE")
        try:
            attrs = Security(ctypes.sizeof(Security), sd, False)
            handle = self.k.CreateNamedPipeW(_PIPE, 3 | 0x40000000 | 0x80000,
                0xE, 1, 8192, 32768, 2000, ctypes.byref(attrs))
            if handle in (None, ctypes.c_void_p(-1).value):
                raise D5Blocked("PIPE_UNAVAILABLE")
            return handle
        finally:
            self.k.LocalFree(sd)


def attest_peer(api: Win32, pipe, frame: bytes) -> TrustedPeer:
    sid, session = api.identity(pipe, logon=frame == D5_LOGON_TRIGGER_PAYLOAD)
    return TrustedPeer(_SEAL, frame, sid, session, expires=time.monotonic() + 5)


def dispatch(receiver: D5Receiver, frame: bytes, peer: TrustedPeer) -> dict:
    if frame == D5_LOGON_TRIGGER_PAYLOAD:
        return receiver.handle_logon_trigger(frame, _peer=peer)
    # Signed requests still require a genuine local operator session, including
    # when relayed by a public runner. The peer SID is never the signature.
    bridge = receiver.get_bridge()
    peer.consume(frame, bridge.enrollment().sid)
    return receiver.handle_request(decode(frame))


def serve_one(receiver, api, pipe):
    """One bounded message, one response, zero retries after a broken pipe."""
    frame = api.io(pipe, "read", maximum=32768)
    try:
        peer = attest_peer(api, pipe, frame)
        result = dispatch(receiver, frame, peer)
    except D5Blocked as exc:
        result = blocked(str(exc))
    except Exception:
        result = blocked("UNKNOWN_EFFECT_HOLD")
    # A lost response never rolls back or repeats the durable bridge intent.
    api.io(pipe, "write", canonical(sanitize_receipt(result)))
    return result


def pipe_exchange(frame: bytes) -> bytes:
    if not 1 <= len(frame) <= 32768:
        raise D5Blocked("INPUT_INVALID")
    api = Win32()
    if not api.k.WaitNamedPipeW(_PIPE, 2000):
        raise D5Blocked("BLOCKED_NOT_DEPLOYED")
    # SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION, overlapped duplex.
    handle = api.k.CreateFileW(_PIPE, 0xC0000000, 0, None, 3, 0x40000000 | 0x100000 | 0x20000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise D5Blocked("PIPE_UNAVAILABLE")
    try:
        mode = w.DWORD(2)
        if not api.k.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            raise D5Blocked("PIPE_UNAVAILABLE")
        api.identity(handle, server=True)
        api.io(handle, "write", frame)
        return api.io(handle, "read", maximum=8192, timeout=60000)
    finally:
        api.k.CloseHandle(handle)


def receiver_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise D5Blocked("BLOCKED_NOT_DEPLOYED")
        authority = SignedPolicy()
        bridge = private_bridge(authority.path, authority=authority)
        receiver = D5Receiver(bridge, authority=authority)
        api = Win32()
        while True:
            policy = bridge.enrollment()  # Fail closed when lease expires/revokes.
            sid, session, elevated = api.process_token(api.k.GetCurrentProcess())
            if sid != policy.sid or session <= 0 or elevated:
                raise D5Blocked("UNSUPPORTED_SESSION")
            pipe = api.create(sid)
            try:
                try:
                    api.io(pipe, "connect", timeout=10000)
                except D5Blocked as exc:
                    if str(exc) == "PIPE_WAIT_TIMEOUT":
                        continue
                    raise
                serve_one(receiver, api, pipe)
            finally:
                api.k.DisconnectNamedPipe(pipe)
                api.k.CloseHandle(pipe)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, D5Blocked) else "UNKNOWN_EFFECT_HOLD"
        if code in {"TRUST_ANCHOR_UNPROVEN", "PRIVATE_STORAGE_UNPROVEN", "CUSTODY_UNPROVEN", "PRIVATE_ENROLLMENT_REQUIRED"}:
            code = "BLOCKED_NOT_DEPLOYED"
        print(canonical(blocked(code)).decode())
        return 2



def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--serve" in args:
        return receiver_main(args)
    return transport_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
