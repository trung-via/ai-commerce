"""Offline component tests: no desktop, scheduler, Chrome, profile or live IPC."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from ai_commerce.hostops_v3 import d5_control as d5
from ai_commerce.remote import chrome_acquisition as ca

ROOT = Path(__file__).resolve().parents[1]
BASE = "28e0ec8ee1a29d0f2cd563f1b8a092028c407fb6"
NOW = 10000
ABSENT = {"state": "ABSENT"}
OWNED = {"state": "OWNED", "pid": 101, "created": "2026-11-05T00:00:00Z"}
CLAIMED = {**OWNED, "state": "PROFILE_ALREADY_ACTIVE_NO_CDP"}


class SimulatedNative:
    """Stateful native contract, independent of real Windows entrypoints."""
    def __init__(self):
        self.snapshot = ABSENT.copy()
        self.tasks = dict.fromkeys(d5.RUNNER_TASKS, "runner-unchanged")
        self.profile = b"private cookies preserved"
        self.legacy = False
        self.launcher = False
        self.effects = []
        self.attaches = 0
        self.denial = None
        self.after_attach = None

    def preflight(self, policy):
        if self.denial:
            raise d5.D5Blocked(self.denial)

    def inventory(self, policy):
        if any(self.tasks.get(n) != "runner-unchanged" for n in d5.RUNNER_TASKS):
            raise d5.D5Blocked("RUNNER_PRESERVATION_UNPROVEN")
        if set(self.tasks) - {*d5.RUNNER_TASKS, d5.TASK}:
            raise d5.D5Blocked("TASK_OWNERSHIP_UNPROVEN")

    def observe(self, policy):
        return self.snapshot.copy()

    def no_conflicts(self, policy):
        if self.legacy:
            raise d5.D5Blocked("CONFLICTING_D5_TASK")

    def attach(self, policy):
        self.attaches += 1
        if self.after_attach:
            self.snapshot = self.after_attach.copy()

    def effect(self, policy, op, snapshot):
        self.effects.append(op)
        if op == "RETIRE_NEW_AUTOSTART":
            self.legacy = False
        elif op == "INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER":
            self.launcher = True
        elif op == "INSTALL_SINGLE_LOGON_AUTOSTART":
            if not self.launcher:
                raise d5.D5Blocked("LAUNCHER_UNVERIFIED")
            self.tasks[d5.TASK] = "one-interactive-logon-limited"
        elif op == "START_OR_REUSE_D5_CHROME":
            self.snapshot = OWNED.copy()
            return OWNED["pid"], OWNED["created"]
        elif op == "STOP_OWNED_D5_CHROME":
            self.snapshot = ABSENT.copy()
        elif op in {"REMOVE_D5_AUTOSTART", "ROLLBACK"}:
            self.tasks.pop(d5.TASK, None)
            if op == "ROLLBACK":
                self.launcher = False


@pytest.fixture
def rig(tmp_path, monkeypatch):
    # Every synthetic file is under pytest's external absolute --basetemp.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert ROOT not in tmp_path.resolve().parents
    env = ca.ChromeEnvironment(tmp_path / "chrome.exe", tmp_path / "dedicated", "Default",
                               "http://127.0.0.1:9333", 9333, True, tmp_path / "chrome.json")
    policy = d5.Enrollment(BASE, 1, "S-1-5-21-111-222-333-1001", NOW + 1000,
                           tmp_path / "release", {}, env, tmp_path / "state", b"K" * 32,
                           runner_tasks=d5.RUNNER_TASKS, enabled=True,
                           private_control_plane=True, public_runner_exposed=False)
    native = SimulatedNative()
    journal = d5.Journal(tmp_path / "journal.sqlite3")
    holder = [policy]
    bridge = d5.Bridge(lambda: holder[0], native, journal, clock=lambda: NOW, sleep=lambda _: None)
    return policy, native, bridge, holder


def invoke(rig, op, number=1, **kwargs):
    policy, _, bridge, _ = rig
    return bridge.invoke(d5.sign_request(policy, op, f"{number:032x}", NOW, **kwargs))


def test_closed_enrolled_lifecycle_and_logon_need_zero_routine_human_powershell(rig):
    _, native, bridge, _ = rig
    native.legacy = True
    for i, (op, code) in enumerate([
        ("STATUS", "CONFLICTING_D5_TASK"), ("MIGRATION_PREFLIGHT", "MIGRATION_READY"),
        ("RETIRE_NEW_AUTOSTART", "RETIRED"),
        ("INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER", "LAUNCHER_INSTALLED"),
        ("INSTALL_SINGLE_LOGON_AUTOSTART", "REGISTERED"),
        ("INSTALL_SINGLE_LOGON_AUTOSTART", "REGISTERED"),
        ("START_OR_REUSE_D5_CHROME", "READY_ATTACH"),
        ("START_OR_REUSE_D5_CHROME", "READY_ATTACH"),
        ("VERIFY_CDP_9333_AND_PLAYWRIGHT", "READY_ATTACH"),
        ("STOP_OWNED_D5_CHROME", "STOPPED"),
        ("RECOVER_BOUNDED", "READY_ATTACH"),
        ("STOP_OWNED_D5_CHROME", "STOPPED"),
        ("REMOVE_D5_AUTOSTART", "AUTOSTART_REMOVED"),
        ("ROLLBACK", "ROLLED_BACK_DISABLED"),
    ], 1):
        result = invoke(rig, op, i)
        assert result["code"] == code
        assert result["wake_sent"] is False
        assert not result["login_verified"] and not result["manifest_verified"]
    assert native.effects.count("START_OR_REUSE_D5_CHROME") == 2
    assert set(native.tasks) == set(d5.RUNNER_TASKS)
    assert native.profile == b"private cookies preserved"
    assert bridge.logon()["code"] == "READY_ATTACH"
    assert bridge.logon()["code"] == "READY_ATTACH"
    assert native.effects.count("START_OR_REUSE_D5_CHROME") == 3


@pytest.mark.parametrize("op", d5.OPS)
def test_dry_run_has_no_effect_attach_or_task_change(rig, op):
    _, native, _, _ = rig
    before = native.tasks.copy()
    assert invoke(rig, op, dry_run=True)["code"] == "DRY_RUN"
    assert native.effects == [] and native.attaches == 0 and native.tasks == before


@pytest.mark.parametrize("patch,code", [
    ({"enabled": False}, "PRIVATE_ENROLLMENT_REQUIRED"),
    ({"revoked": True}, "PRIVATE_ENROLLMENT_REQUIRED"),
    ({"public_runner_exposed": True}, "PRIVATE_ENROLLMENT_REQUIRED"),
    ({"private_control_plane": False}, "PRIVATE_ENROLLMENT_REQUIRED"),
    ({"expires": NOW}, "ENROLLMENT_INVALID_OR_STALE"),
    ({"runner_tasks": d5.RUNNER_TASKS[:2]}, "RUNNER_PRESERVATION_UNPROVEN"),
    ({"runner_tasks": (*d5.RUNNER_TASKS[:2], d5.TASK)}, "RUNNER_PRESERVATION_UNPROVEN"),
])
def test_enrollment_revocation_and_private_custody_fail_closed(rig, patch, code):
    policy, native, _, holder = rig
    holder[0] = replace(policy, **patch)
    with pytest.raises(d5.D5Blocked, match=code):
        invoke(rig, "STATUS")
    assert native.effects == []


def test_default_policy_disabled_and_actions_never_private_control_plane(rig, monkeypatch):
    policy, _, _, _ = rig
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(d5.D5Blocked, match="PRIVATE_ENROLLMENT_REQUIRED"):
        invoke(rig, "STATUS")
    assert d5.Enrollment.__dataclass_fields__["enabled"].default is False
    assert d5.Enrollment.__dataclass_fields__["public_runner_exposed"].default is True


@pytest.mark.parametrize("patch", [
    {"op": "powershell"}, {"op": "Wake"}, {"op": "SEND"}, {"path": "PRIVATE"},
    {"pid": 7}, {"command": "whoami"}, {"epoch": 0}, {"epoch": True},
    {"source_sha": "0" * 40}, {"issued": NOW-61}, {"issued": NOW+1},
    {"expires": NOW}, {"expires": NOW+61}, {"dry_run": 1}, {"request_id": "../x"},
])
def test_unfixed_inputs_source_drift_stale_approval_and_expiry_rejected(rig, patch):
    policy, native, bridge, _ = rig
    request = d5.sign_request(policy, "STATUS", "1" * 32, NOW)
    request.update(patch)
    with pytest.raises(d5.D5Blocked, match="REQUEST_INVALID_OR_STALE"):
        bridge.invoke(request)
    assert native.effects == []


def test_authentication_and_replay_are_not_idempotent_reexecution(rig):
    policy, native, bridge, _ = rig
    request = d5.sign_request(policy, "STATUS", "1" * 32, NOW)
    with pytest.raises(d5.D5Blocked, match="AUTHENTICATION_FAILED"):
        bridge.invoke({**request, "mac": "0"*64})
    assert bridge.invoke(request)["code"] == "NOT_RUNNING"
    with pytest.raises(d5.D5Blocked, match="REQUEST_REPLAY"):
        bridge.invoke(request)
    assert native.effects == []


@pytest.mark.parametrize("denial", ["UNSUPPORTED_SESSION", "SOURCE_DRIFT", "PRIVATE_STORAGE_UNPROVEN"])
def test_elevated_wrong_sid_missing_desktop_or_unpinned_source_never_effects(rig, denial):
    _, native, _, _ = rig
    native.denial = denial
    with pytest.raises(d5.D5Blocked, match=denial):
        invoke(rig, "START_OR_REUSE_D5_CHROME")
    assert native.effects == []


@pytest.mark.parametrize("op", [op for op in d5.OPS if op not in d5.READS and op != "STOP_OWNED_D5_CHROME"])
def test_active_profile_without_listener_holds_every_mutation(rig, op):
    _, native, _, _ = rig
    native.snapshot = CLAIMED.copy()
    assert invoke(rig, op)["code"] == "PROFILE_ALREADY_ACTIVE_NO_CDP"
    assert native.effects == [] and native.profile == b"private cookies preserved"


def test_stop_owned_d5_chrome_under_profile_already_active_no_cdp(rig):
    _, native, _, _ = rig
    native.snapshot = CLAIMED.copy()
    # Dry-run returns DRY_RUN without effects
    assert invoke(rig, "STOP_OWNED_D5_CHROME", 1, dry_run=True)["code"] == "DRY_RUN"
    assert native.effects == []
    # Graceful stop succeeds when process exits and observe confirms ABSENT
    result = invoke(rig, "STOP_OWNED_D5_CHROME", 2)
    assert result["code"] == "STOPPED"
    assert result["effect_observed"] is True
    assert native.effects == ["STOP_OWNED_D5_CHROME"]
    assert native.snapshot["state"] == "ABSENT"


def test_stop_owned_d5_chrome_unverified_exit_raises_unknown_effect(rig, monkeypatch):
    _, native, _, _ = rig
    native.snapshot = CLAIMED.copy()
    def effect_leaving_process(policy, op, snapshot):
        native.effects.append(op)
        # Snapshot remains CLAIMED (not ABSENT)
        return None
    monkeypatch.setattr(native, "effect", effect_leaving_process)
    result = invoke(rig, "STOP_OWNED_D5_CHROME")
    assert result["code"] == "UNKNOWN_EFFECT_HOLD"


@pytest.mark.parametrize("after,code", [(CLAIMED, "CDP_LISTENER_LOST"), (ABSENT, "ROOT_PROCESS_LOST"),
                                           ({**OWNED, "pid": 102}, "BROWSER_RESTARTED")])
def test_ready_attach_then_listener_loss_root_loss_or_restart_are_distinct(rig, after, code):
    _, native, _, _ = rig
    native.snapshot = OWNED.copy()
    assert invoke(rig, "STATUS")["code"] == "READY_ATTACH"
    native.after_attach = after
    result = invoke(rig, "STATUS", 2)
    assert result["code"] == code and result["playwright_attached"] and not result["stable"]
    assert native.effects == []


def test_recovery_is_rate_limited_and_conflicts_never_relaunch(rig):
    _, native, _, _ = rig
    native.legacy = True
    assert invoke(rig, "RECOVER_BOUNDED")["code"] == "CONFLICTING_D5_TASK"
    native.legacy = False
    with pytest.raises(d5.D5Blocked, match="RECOVERY_RATE_LIMIT"):
        invoke(rig, "RECOVER_BOUNDED", 2)
    assert native.effects == []


def test_bounded_stabilization_never_spawns_twice(rig, monkeypatch):
    _, native, _, _ = rig
    native.legacy = False
    original = native.effect
    def starting(policy, op, snapshot):
        result = original(policy, op, snapshot)
        native.snapshot = CLAIMED.copy()
        return result
    monkeypatch.setattr(native, "effect", starting)
    reads = []
    def observe(policy):
        reads.append(1)
        if len(reads) > 3:
            return OWNED.copy()
        return native.snapshot.copy()
    monkeypatch.setattr(native, "observe", observe)
    assert invoke(rig, "START_OR_REUSE_D5_CHROME")["code"] == "READY_ATTACH"
    assert native.effects == ["START_OR_REUSE_D5_CHROME"]


def test_post_effect_probe_failure_is_unknown_and_cannot_be_retried(rig, monkeypatch):
    _, native, _, _ = rig
    native.legacy = False
    def attach(_):
        raise d5.D5Blocked("PLAYWRIGHT_UNAVAILABLE")
    monkeypatch.setattr(native, "attach", attach)
    assert invoke(rig, "START_OR_REUSE_D5_CHROME")["code"] == "UNKNOWN_EFFECT_HOLD"
    with pytest.raises(d5.D5Blocked, match="UNKNOWN_EFFECT_HOLD"):
        invoke(rig, "START_OR_REUSE_D5_CHROME", 2)
    assert native.effects == ["START_OR_REUSE_D5_CHROME"]


def test_config_launch_optout_is_not_overridden_by_enrollment(rig):
    policy, native, _, holder = rig
    holder[0] = replace(policy, environment=replace(policy.environment, allow_launch=False))
    native.legacy = False
    assert invoke(rig, "START_OR_REUSE_D5_CHROME")["code"] == "LAUNCH_NOT_AUTHORIZED"
    assert native.effects == []


@pytest.mark.parametrize("mode", ["crash", "unknown", "unrelated-pid", "never-stabilizes"])
def test_unknown_or_crash_survives_restart_and_never_retries(rig, monkeypatch, mode):
    policy, native, bridge, _ = rig
    native.legacy = False
    def effect(*_):
        native.effects.append("one-attempt")
        if mode == "crash":
            raise KeyboardInterrupt()
        if mode == "unknown":
            raise TimeoutError("PRIVATE_SECRET_PATH")
        if mode == "unrelated-pid":
            native.snapshot = {**OWNED, "pid": 999}
        return OWNED["pid"], OWNED["created"]
    monkeypatch.setattr(native, "effect", effect)
    if mode == "crash":
        with pytest.raises(KeyboardInterrupt):
            invoke(rig, "START_OR_REUSE_D5_CHROME")
    else:
        result = invoke(rig, "START_OR_REUSE_D5_CHROME")
        assert result["code"] == "UNKNOWN_EFFECT_HOLD" and "PRIVATE" not in json.dumps(result)
    reopened = d5.Bridge(lambda: policy, native, d5.Journal(bridge.journal.path), clock=lambda: NOW)
    with pytest.raises(d5.D5Blocked, match="UNKNOWN_EFFECT_HOLD"):
        reopened.invoke(d5.sign_request(policy, "START_OR_REUSE_D5_CHROME", "2"*32, NOW))
    assert native.effects == ["one-attempt"]


@pytest.mark.parametrize("damage", ["missing-runner", "unknown-task", "running-browser"])
def test_bad_tasks_and_owned_running_browser_preserve_all_resources(rig, damage):
    _, native, _, _ = rig
    if damage == "missing-runner":
        native.tasks.pop(d5.RUNNER_TASKS[0])
    elif damage == "unknown-task":
        native.tasks["AI-Commerce-D5-Unenrolled"] = "unknown"
    else:
        native.snapshot = OWNED.copy()
    result = invoke(rig, "RETIRE_NEW_AUTOSTART")
    assert result["code"] in {"RUNNER_PRESERVATION_UNPROVEN", "TASK_OWNERSHIP_UNPROVEN", "BROWSER_MUST_BE_STOPPED"}
    assert native.effects == [] and native.profile == b"private cookies preserved"


def test_journal_bounded_and_intents_durable_before_effect(rig):
    _, native, bridge, _ = rig
    assert invoke(rig, "STATUS")["code"] == "NOT_RUNNING"
    with sqlite3.connect(bridge.journal.path) as db:
        assert db.execute("SELECT state FROM requests").fetchone() == ("DONE",)
        db.executemany("INSERT INTO requests VALUES (?, 'STATUS','DONE',NULL,?)",
                       [(f"{i+100:032x}", NOW) for i in range(2047)])
    with pytest.raises(d5.D5Blocked, match="JOURNAL_ROTATION_REQUIRED"):
        invoke(rig, "STATUS", 2)
    assert native.effects == []


def test_duplicate_json_no_unsigned_default_verifier_and_no_path_leaks(rig, tmp_path, monkeypatch):
    with pytest.raises(d5.D5Blocked, match="INPUT_INVALID"):
        d5.decode(b'{"op":"STATUS","op":"SEND"}')
    monkeypatch.setattr(d5, "private_path", lambda path, **_: path)
    path = tmp_path / "enrollment.json"
    path.write_text('{"signed":true}')
    def reject(value):
        raise ValueError("PRIVATE_OPERATOR_KEY")
    with pytest.raises(d5.D5Blocked, match="^ENROLLMENT_UNVERIFIED$"):
        d5.load_enrollment(path, verify_signed=reject, now=NOW)
    with pytest.raises(TypeError):
        d5.load_enrollment(path, now=NOW)


def test_release_hashes_pin_current_executing_package_and_detect_drift(rig):
    policy, _, _, _ = rig
    import sys
    paths = list(d5.RELEASE_FILES)
    for name, module in tuple(sys.modules.items()):
        if name == "ai_commerce" or name.startswith("ai_commerce."):
            file = getattr(module, "__file__", None)
            if file:
                paths.append(Path(file).resolve().relative_to(ROOT).as_posix())
    files = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    pinned = replace(policy, release_root=ROOT, files=files)
    d5.verify_release(pinned)
    with pytest.raises(d5.D5Blocked, match="SOURCE_DRIFT"):
        d5.verify_release(replace(pinned, files={**files, d5.RELEASE_FILES[0]: "0"*64}))
    with pytest.raises(d5.D5Blocked, match="SOURCE_DRIFT"):
        d5.verify_release(replace(pinned, files={**files, "../escape.py": "0"*64}))


@pytest.mark.parametrize("session", [0, "elevated", "no-desktop", "wrong-sid"])
def test_actual_native_preflight_session_gates_offline(rig, monkeypatch, session):
    policy, _, _, _ = rig
    monkeypatch.setattr(d5, "verify_release", lambda _: None)
    monkeypatch.setattr(d5, "private_path", lambda path, **_: path)
    # preflight checks fixed state/key BEFORE desktop access: no real input read.
    local = policy.state_root.parent / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    fixed = local / "AI-Commerce" / "D5-Operator"
    fixed.mkdir(parents=True)
    (fixed / "control.key").write_bytes(policy.key)
    policy = replace(policy, state_root=fixed)
    monkeypatch.setattr(ca, "_require_operator_control", lambda _: None)
    monkeypatch.setattr(ca, "_verify_google_chrome", lambda _: None)
    def interactive():
        if session in {"elevated", "no-desktop"}:
            raise ca.AcquisitionBlocked("WINDOWS_INTERACTIVE_REQUIRED")
        return 0 if session == 0 else 1
    monkeypatch.setattr(ca, "_interactive_session", interactive)
    from ai_commerce.remote import local_completion_handoff as handoff
    monkeypatch.setattr(handoff, "_windows_process_sid", lambda: "wrong" if session == "wrong-sid" else policy.sid)
    with pytest.raises(d5.D5Blocked, match="UNSUPPORTED_SESSION"):
        d5.WindowsNative().preflight(policy)


def test_native_adapter_no_raw_stdout_stderr_or_private_values(rig, monkeypatch):
    policy, _, _, _ = rig
    native = d5.WindowsNative()
    native.request_id = "1"*32
    monkeypatch.setattr(native, "preflight", lambda _: None)
    monkeypatch.setattr(ca, "_powershell", lambda: Path("powershell.exe"))
    class Reply:
        stdout = b'{"code":"PRIVATE_SECRET_PATH"}'
        returncode = 1
    calls = []
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return Reply()
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(d5.D5Blocked, match="UNKNOWN_EFFECT_HOLD"):
        native.call(policy, "MIGRATION_PREFLIGHT")
    _, kwargs = calls[0]
    assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["timeout"] == 45
    assert "PRIVATE_SECRET_PATH" not in str(kwargs)


def test_powershell_native_adapter_parses_without_invoking_windows_effects():
    if os.name != "nt":
        pytest.skip("native Windows PowerShell AST validation")
    script = ROOT / "scripts/AI-Commerce-D5-Operator-Bridge.ps1"
    command = "$e=$null;[System.Management.Automation.Language.Parser]::ParseFile('" + str(script).replace("'", "''") + "',[ref]$null,[ref]$e)|Out-Null;if(@($e).Count){$e|Out-String|Write-Output;exit 1}"
    result = subprocess.run([str(ca._powershell()), "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mt01_owner_manifest_has_real_legacy_consumers_and_eight_path_scope():
    manifest = json.loads((ROOT / "work_items/mt01-components/hostops-d5-control.json").read_text())
    assert manifest["sources"] == ["src/ai_commerce/hostops_v3/d5_control.py"]
    assert "src/ai_commerce/remote/chrome_acquisition.py" in manifest["consumers"]
    assert "src/ai_commerce/hostops_v3/contract.py" in manifest["consumers"]
    work = json.loads((ROOT / "work_items/HOSTOPS_D5_OPERATOR_BRIDGE_001.json").read_text())
    assert len(work["allowed_paths"]) == 8 and work["source_sha"] == BASE
    assert work["native_acceptance"] == "PENDING_GENUINE_HOSTOPS_DEPLOYMENT"


def test_d5_receiver_handle_request_lifecycle(rig):
    policy, native, bridge, _ = rig
    receiver = d5.D5Receiver(bridge, clock=lambda: NOW)
    request = d5.sign_request(policy, "STATUS", "1" * 32, NOW)
    res = receiver.handle_request(request)
    assert res["code"] == "NOT_RUNNING"
    assert res["operation"] == "STATUS"
    assert "private" not in json.dumps(res).lower()


def test_d5_receiver_handle_logon_trigger(rig):
    policy, native, bridge, _ = rig
    receiver = d5.D5Receiver(bridge, clock=lambda: NOW)
    attestation = {
        "sid": policy.sid,
        "session_id": 1,
        "elevated": False,
        "desktop_available": True,
    }
    result = receiver.handle_logon_trigger(b"D5_LOGON_START_V1\n", client_attestation=attestation)
    assert result["status"] == "OBSERVED"
    assert result["code"] == "LOGON_TRIGGER_DELIVERED"
    assert result["operation"] == "LOGON_TRIGGER"
    assert result["bridge_receipt"]["code"] == "READY_ATTACH"
    assert native.effects.count("START_OR_REUSE_D5_CHROME") == 1


def test_d5_receiver_logon_trigger_rejected_inputs(rig):
    policy, native, bridge, _ = rig
    receiver = d5.D5Receiver(bridge, clock=lambda: NOW)
    # Reject wrong payload
    with pytest.raises(d5.D5Blocked, match="INPUT_INVALID"):
        receiver.handle_logon_trigger(b"WRONG_TRIGGER\n")
    # Reject oversized payload
    with pytest.raises(d5.D5Blocked, match="INPUT_INVALID"):
        receiver.handle_logon_trigger(b"A" * 65)
    # Reject elevated client
    with pytest.raises(d5.D5Blocked, match="UNSUPPORTED_SESSION"):
        receiver.handle_logon_trigger(b"D5_LOGON_START_V1\n", client_attestation={
            "sid": policy.sid, "session_id": 1, "elevated": True, "desktop_available": True,
        })
    # Reject wrong SID client
    with pytest.raises(d5.D5Blocked, match="UNSUPPORTED_SESSION"):
        receiver.handle_logon_trigger(b"D5_LOGON_START_V1\n", client_attestation={
            "sid": "S-1-5-21-999", "session_id": 1, "elevated": False, "desktop_available": True,
        })
    # Reject session 0 (non-interactive)
    with pytest.raises(d5.D5Blocked, match="UNSUPPORTED_SESSION"):
        receiver.handle_logon_trigger(b"D5_LOGON_START_V1\n", client_attestation={
            "sid": policy.sid, "session_id": 0, "elevated": False, "desktop_available": True,
        })


def test_d5_receiver_not_deployed_fail_closed():
    receiver = d5.D5Receiver(None)
    result = receiver.handle_request({"op": "STATUS"})
    assert result["status"] == "BLOCKED"
    assert result["code"] == "BLOCKED_NOT_DEPLOYED"
    logon_res = receiver.handle_logon_trigger(b"D5_LOGON_START_V1\n")
    assert logon_res["status"] == "BLOCKED"
    assert logon_res["code"] == "BLOCKED_NOT_DEPLOYED"


def test_d5_receiver_repeated_logon_delivery_does_not_duplicate_browser(rig):
    policy, native, bridge, _ = rig
    receiver = d5.D5Receiver(bridge, clock=lambda: NOW)
    attestation = {
        "sid": policy.sid,
        "session_id": 1,
        "elevated": False,
        "desktop_available": True,
    }
    r1 = receiver.handle_logon_trigger(b"D5_LOGON_START_V1\n", client_attestation=attestation)
    assert r1["code"] == "LOGON_TRIGGER_DELIVERED"
    assert r1["bridge_receipt"]["code"] == "READY_ATTACH"
    # Second logon delivery reuses existing owned process
    r2 = receiver.handle_logon_trigger(b"D5_LOGON_START_V1\n", client_attestation=attestation)
    assert r2["code"] == "LOGON_TRIGGER_DELIVERED"
    assert r2["bridge_receipt"]["code"] == "READY_ATTACH"
    assert native.effects.count("START_OR_REUSE_D5_CHROME") == 1


def test_ingress_boundary_rejects_arbitrary_commands_scripts_and_paths():
    boundary = d5.D5IngressBoundary()
    # Reject arbitrary command
    res1 = boundary.evaluate_ingress_request({"op": "STATUS", "command": "cmd.exe"})
    assert res1["status"] == "BLOCKED"
    assert res1["code"] == "INPUT_INVALID"
    # Reject arbitrary script
    res2 = boundary.evaluate_ingress_request({"op": "STATUS", "script": "exploit.ps1"})
    assert res2["status"] == "BLOCKED"
    assert res2["code"] == "INPUT_INVALID"
    # Reject arbitrary PID
    res3 = boundary.evaluate_ingress_request({"op": "STATUS", "pid": 1234})
    assert res3["status"] == "BLOCKED"
    assert res3["code"] == "INPUT_INVALID"
    # Reject unknown op
    res4 = boundary.evaluate_ingress_request({"op": "EXECUTE_SHELL"})
    assert res4["status"] == "BLOCKED"
    assert res4["code"] == "INPUT_INVALID"


def test_ingress_boundary_off_host_custody_absent_returns_blocked_not_deployed():
    boundary = d5.D5IngressBoundary(off_host_custody_proven=False)
    res = boundary.evaluate_ingress_request({
        "op": "STATUS", "request_id": "1" * 32, "issued": NOW, "expires": NOW + 30,
        "source_sha": "a" * 40, "epoch": 1, "dry_run": False, "mac": "0" * 64,
    })
    assert res["status"] == "BLOCKED"
    assert res["code"] == "BLOCKED_NOT_DEPLOYED"
    assert res["attended_enrollment_required"] is True
    assert "minimal_attended_enrollment" in res


def test_ingress_boundary_valid_off_host_signed_request_dispatches(rig):
    policy, native, bridge, _ = rig
    receiver = d5.D5Receiver(bridge, clock=lambda: NOW)
    boundary = d5.D5IngressBoundary(receiver, verify_off_host_signature=lambda req: True,
                                    off_host_custody_proven=True)
    request = d5.sign_request(policy, "STATUS", "2" * 32, NOW)
    res = boundary.evaluate_ingress_request(request)
    assert res["code"] == "NOT_RUNNING"
    assert res["operation"] == "STATUS"


def test_create_blocked_not_deployed_evidence_contains_no_secrets():
    evidence = d5.create_blocked_not_deployed_evidence(op="STATUS")
    raw = json.dumps(evidence)
    assert evidence["status"] == "BLOCKED"
    assert evidence["code"] == "BLOCKED_NOT_DEPLOYED"
    assert evidence["attended_enrollment_required"] is True
    assert "key" not in evidence
    assert "control.key" not in raw
    assert "secret" not in raw.lower()


def test_sanitize_receipt_preserves_allowlist_only():
    raw_receipt = {
        "operation": "STATUS",
        "code": "READY_ATTACH",
        "status": "OBSERVED",
        "effect_observed": False,
        "internal_path": "C:\\Secret\\Path",
        "raw_cookie": "secret_cookie",
        "registered": True,
    }
    sanitized = d5.sanitize_receipt(raw_receipt)
    assert "internal_path" not in sanitized
    assert "raw_cookie" not in sanitized
    assert sanitized["operation"] == "STATUS"
    assert sanitized["code"] == "READY_ATTACH"
    assert sanitized["registered"] is True
