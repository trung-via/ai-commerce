"""Closed D5 lifecycle bridge. No CLI, network listener, or enrollment by default.

The private control plane supplies a verified enrollment and authenticates requests.
An Actions worker must never own its key. Same-SID processes are not a vault.
Native effects use only the enrolled interactive desktop and a signed release.
"""
from __future__ import annotations

from dataclasses import dataclass
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


def load_enrollment(path: Path, *, verify_signed: Callable[[dict], dict], now: int) -> Enrollment:
    """verify_signed verifies signed policy + revocation via PRIVATE trust anchor.

    No built-in identity verifier, default anchor, shared-runner key or unsigned
    enable flag exists. Protect the verifier/service config in a separate review.
    """
    try:
        private_path(path)
        value = verify_signed(decode(path.read_bytes()))
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
        import sys
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

    def connect(self):
        db = sqlite3.connect(self.path, timeout=1, isolation_level="IMMEDIATE")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, request: dict, now: int) -> None:
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
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

    def invoke(self, request: dict) -> dict:
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
            claims = [p for p in ca._rows(inventory["processes"])
                      if ca._identity_matches(env, p.get("ExecutablePath", ""),
                                              ca._commandline_argv(p.get("CommandLine", "")))]
            if len(claims) != 1:
                raise D5Blocked("OWNERSHIP_UNPROVEN")
            proc = claims[0]
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


def private_bridge(path: Path, *, verify_signed: Callable[[dict], dict]) -> Bridge:
    """Private deployment factory. There is intentionally no unsigned CLI route."""
    def enrolled():
        return load_enrollment(path, verify_signed=verify_signed, now=int(time.time()))
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
PUBLIC_RECEIPT_FIELDS = frozenset({
    "operation", "code", "status", "effect_observed", "registered",
    "started", "listener_bound", "playwright_attached", "stable",
    "wake_sent", "login_verified", "manifest_verified",
    "request_id", "source_sha", "epoch", "attended_enrollment_required",
    "minimal_attended_enrollment", "reason", "recorded_at", "bridge_receipt",
})


def sanitize_receipt(result: dict) -> dict:
    """Return strictly allowlisted public receipt fields without internal paths or data."""
    if not isinstance(result, dict):
        return {"code": "INPUT_INVALID", "status": "BLOCKED"}
    sanitized = {k: result[k] for k in PUBLIC_RECEIPT_FIELDS if k in result}
    if "bridge_receipt" in sanitized and isinstance(sanitized["bridge_receipt"], dict):
        sanitized["bridge_receipt"] = sanitize_receipt(sanitized["bridge_receipt"])
    return sanitized


def create_blocked_not_deployed_evidence(*, op: str = "UNKNOWN", state_root: Path | None = None) -> dict:
    """Produces explicit BLOCKED_NOT_DEPLOYED evidence with minimal attended enrollment spec.

    Contains NO private keys, HMAC keys, paths or secrets.
    """
    return {
        "status": "BLOCKED",
        "code": "BLOCKED_NOT_DEPLOYED",
        "operation": op if op in OPS else "UNKNOWN",
        "effect_observed": False,
        "reason": "OFF_HOST_SIGNER_UNAVAILABLE_OR_UNPROVEN_CUSTODY",
        "attended_enrollment_required": True,
        "minimal_attended_enrollment": {
            "required_fields": [
                "source_sha", "epoch", "sid", "expires", "release_root",
                "files", "chrome_config", "local_app_data", "state_root",
                "key_file", "legacy_tasks", "runner_tasks", "enabled",
                "revoked", "private_control_plane", "public_runner_exposed",
            ],
            "runner_tasks": list(RUNNER_TASKS),
            "strategy": STRATEGY,
            "port": 9333,
            "public_runners_present": 3,
            "shared_sid_isolation_claimed": False,
        },
    }


class D5Receiver:
    """Closed D5-only private operator receiver.

    Serves the at-logon named-pipe trigger and authenticated D5 requests.
    Enforces strict enrollment checks, source SHA pinning, replay/TTL guards,
    and durable native receipts. Default disabled when off-host custody
    or private enrollment is unproven.
    """
    def __init__(self, bridge_or_factory: Callable[[], Bridge] | Bridge | None = None,
                 *, enrollment_path: Path | None = None,
                 verify_signed: Callable[[dict], dict] | None = None,
                 clock: Callable[[], float] = time.time):
        self._bridge_or_factory = bridge_or_factory
        self.enrollment_path = enrollment_path
        self.verify_signed = verify_signed
        self.clock = clock

    def get_bridge(self) -> Bridge:
        if callable(self._bridge_or_factory):
            return self._bridge_or_factory()
        if isinstance(self._bridge_or_factory, Bridge):
            return self._bridge_or_factory
        if self.enrollment_path and self.verify_signed:
            return private_bridge(self.enrollment_path, verify_signed=self.verify_signed)
        raise D5Blocked("BLOCKED_NOT_DEPLOYED")

    def handle_logon_trigger(self, trigger_bytes: bytes, client_attestation: dict | None = None) -> dict:
        """Process incoming trigger on named pipe AI-Commerce-D5-Operator-v1.

        Authenticates client interactive session/SID/elevation, verifies exact
        trigger payload (<=64 bytes), reloads enrollment and executes Bridge.logon().
        """
        if len(trigger_bytes) > 64 or trigger_bytes.strip() != b"D5_LOGON_START_V1":
            raise D5Blocked("INPUT_INVALID")

        if client_attestation is not None:
            if (not isinstance(client_attestation, dict)
                    or client_attestation.get("elevated") is True
                    or client_attestation.get("session_id", 0) <= 0
                    or not client_attestation.get("desktop_available")):
                raise D5Blocked("UNSUPPORTED_SESSION")

        try:
            bridge = self.get_bridge()
            policy = bridge.enrollment()
            now = int(self.clock())
            policy.validate(now)
            if client_attestation is not None:
                if client_attestation.get("sid") != policy.sid:
                    raise D5Blocked("UNSUPPORTED_SESSION")
            result = bridge.logon()
            return {
                "status": "OBSERVED",
                "code": "LOGON_TRIGGER_DELIVERED",
                "operation": "LOGON_TRIGGER",
                "effect_observed": result.get("effect_observed", False),
                "bridge_receipt": sanitize_receipt(result),
                "recorded_at": now,
            }
        except D5Blocked as exc:
            if str(exc) in ("PRIVATE_ENROLLMENT_REQUIRED", "BLOCKED_NOT_DEPLOYED"):
                return {
                    "status": "BLOCKED",
                    "code": "BLOCKED_NOT_DEPLOYED",
                    "operation": "LOGON_TRIGGER",
                    "reason": str(exc),
                }
            raise

    def handle_request(self, request: dict) -> dict:
        """Handle incoming authenticated D5 request with full replay/TTL/SHA guards."""
        try:
            bridge = self.get_bridge()
        except D5Blocked as exc:
            op = request.get("op", "UNKNOWN") if isinstance(request, dict) else "UNKNOWN"
            if op not in OPS:
                op = "UNKNOWN"
            return {
                "operation": op,
                "code": "BLOCKED_NOT_DEPLOYED",
                "status": "BLOCKED",
                "effect_observed": False,
                "reason": str(exc),
            }

        return sanitize_receipt(bridge.invoke(request))


class D5IngressBoundary:
    """Fail-closed ingress boundary from ChatGPT/GitHub to trusted private operator.

    Validates fixed D5 operation enums, rejects any arbitrary command/script/path/PID,
    refuses to treat public GitHub issues or same-SID runner credentials as signatures,
    and requires verified off-host signatures.
    """
    def __init__(self, receiver: D5Receiver | None = None, *,
                 verify_off_host_signature: Callable[[dict], bool] | None = None,
                 off_host_custody_proven: bool = False):
        self.receiver = receiver
        self.verify_off_host_signature = verify_off_host_signature
        self.off_host_custody_proven = off_host_custody_proven

    def evaluate_ingress_request(self, request_payload: dict) -> dict:
        """Evaluate raw ingress request from ChatGPT / GitHub.

        Enforces closed parameterless schema:
        Only op, request_id, issued, expires, source_sha, epoch, dry_run, mac/signature.
        Rejects arbitrary commands, scripts, paths, PIDs, environment variables.
        """
        if not isinstance(request_payload, dict):
            return {
                "status": "BLOCKED",
                "code": "INPUT_INVALID",
                "operation": "UNKNOWN",
            }

        allowed_keys = {"op", "request_id", "issued", "expires", "source_sha", "epoch", "dry_run", "mac", "signature"}
        if not set(request_payload).issubset(allowed_keys):
            return {
                "status": "BLOCKED",
                "code": "INPUT_INVALID",
                "operation": request_payload.get("op", "UNKNOWN") if request_payload.get("op") in OPS else "UNKNOWN",
            }

        op = request_payload.get("op")
        if op not in OPS:
            return {
                "status": "BLOCKED",
                "code": "INPUT_INVALID",
                "operation": "UNKNOWN",
            }

        # Refuse to treat public GitHub issues or runner-owned keys as private signatures
        if not self.off_host_custody_proven or self.verify_off_host_signature is None:
            return create_blocked_not_deployed_evidence(op=op)

        if not self.verify_off_host_signature(request_payload):
            return {
                "status": "BLOCKED",
                "code": "AUTHENTICATION_FAILED",
                "operation": op,
            }

        if self.receiver is None:
            return create_blocked_not_deployed_evidence(op=op)

        return self.receiver.handle_request(request_payload)
