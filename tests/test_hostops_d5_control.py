"""Offline component tests: no desktop, scheduler, Chrome, profile or live IPC."""


from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import pytest
import base64
import uuid

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
        d5.load_enrollment(path, authority=reject, now=NOW)
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


def test_no_external_hmac_or_callback_authentication_route(rig):
    policy, native, bridge, _ = rig
    receiver = d5.D5Receiver(bridge, clock=lambda: NOW)
    assert receiver.handle_request(d5.sign_request(policy, "STATUS", "1"*32, NOW))["code"] == "INPUT_INVALID"
    with pytest.raises(TypeError):
        d5.D5IngressBoundary(receiver, verify_off_host_signature=lambda _: True, off_host_custody_proven=True)
    assert native.effects == []


@pytest.mark.parametrize("field,value", [
    ("code", "PRIVATE_PATH"), ("operation", "PRIVATE_COOKIE"), ("request_id", "PRIVATE_VALUE"),
    ("epoch", True), ("effect_observed", "PRIVATE_VALUE"), ("bridge_receipt", "PRIVATE_VALUE"),
    ("wake_sent", True),
])
def test_sanitizer_checks_values_not_only_field_names(field, value):
    result = d5.sanitize_receipt({"code": "READY_ATTACH", "operation": "STATUS", field: value})
    assert result["code"] == "INVALID_PUBLIC_RECEIPT"
    assert "PRIVATE" not in json.dumps(result)


def test_sanitizer_drops_unknown_fields_and_nested_private_fields():
    value = d5.sanitize_receipt({"code": "LOGON_TRIGGER_DELIVERED", "operation": "LOGON_TRIGGER",
        "bridge_receipt": {"code": "READY_ATTACH", "private_path": "DO_NOT_PUBLISH"},
        "raw_cookie": "DO_NOT_PUBLISH"})
    assert "DO_NOT_PUBLISH" not in json.dumps(value)
    assert value["bridge_receipt"]["code"] == "READY_ATTACH"






receiver = transport = d5


class KernelPipe:
    def __init__(self, sid, *, fail=None):
        self.sid, self.fail, self.writes = sid, fail, []

    def identity(self, pipe, *, logon=False):
        if self.fail == "identity":
            raise d5.D5Blocked("UNSUPPORTED_SESSION")
        return self.sid, 1

    def io(self, pipe, action, data=None, **kwargs):
        if self.fail == action:
            raise d5.D5Blocked("PIPE_UNAVAILABLE")
        if action == "read":
            return d5.D5_LOGON_TRIGGER_PAYLOAD
        self.writes.append(data)


@pytest.mark.parametrize("attestation", [None, {}, {
    "sid": "S-1-5-21-111-222-333-1001", "session_id": 1,
    "elevated": False, "desktop_available": True,
}])
def test_missing_or_forged_attestation_cannot_launch(rig, attestation):
    _, native, bridge, _ = rig
    core = d5.D5Receiver(bridge, clock=lambda: NOW)
    with pytest.raises(d5.D5Blocked, match="UNTRUSTED_PEER_ATTESTATION"):
        core.handle_logon_trigger(d5.D5_LOGON_TRIGGER_PAYLOAD, client_attestation=attestation)
    assert native.effects == []


def test_attested_at_logon_delivery_then_reuse_one_browser(rig):
    policy, native, bridge, _ = rig
    core, api = d5.D5Receiver(bridge, clock=lambda: NOW), KernelPipe(policy.sid)
    for _ in range(2):
        result = receiver.serve_one(core, api, "SYNTHETIC_HANDLE")
        assert result["code"] == "LOGON_TRIGGER_DELIVERED"
        assert result["bridge_receipt"]["code"] == "READY_ATTACH"
    assert native.effects == ["START_OR_REUSE_D5_CHROME"]
    assert native.attaches == 4
    assert set(native.tasks) == set(d5.RUNNER_TASKS)
    assert native.profile == b"private cookies preserved"
    assert "sid" not in json.loads(api.writes[0])


@pytest.mark.parametrize("current,peer,session", [
    (("sid", 1, False), ("other", 1, False), 1),
    (("sid", 1, False), ("sid", 2, False), 1),
    (("sid", 1, False), ("sid", 1, True), 1),
    (("sid", 1, True), ("sid", 1, True), 1),
    (("sid", 0, False), ("sid", 0, False), 0),
    (("sid", 1, False), ("sid", 1, False), 2),
    (("sid", 1, False), ("sid", True, False), 1),
])
def test_kernel_sid_session_elevation_mismatch_is_closed(current, peer, session):
    with pytest.raises(d5.D5Blocked, match="UNSUPPORTED_SESSION"):
        receiver._same_operator(current, peer, session)


def test_absent_input_desktop_denied_without_real_windows_call():
    class User:
        def GetUserObjectInformationW(self, *args):
            raise AssertionError("absent desktop must short circuit")
    api = object.__new__(receiver.Win32)
    api.u = User()
    with pytest.raises(d5.D5Blocked, match="UNSUPPORTED_SESSION"):
        api.desktop(None, "sid")


def test_wrong_peer_sid_and_kernel_failure_have_no_effect(rig):
    policy, native, bridge, _ = rig
    core = d5.D5Receiver(bridge, clock=lambda: NOW)
    for api in (KernelPipe("other-sid"), KernelPipe(policy.sid, fail="identity")):
        result = receiver.serve_one(core, api, "SYNTHETIC_HANDLE")
        assert result["status"] == "BLOCKED"
    assert native.effects == []


def test_capability_is_frame_bound_single_use_and_cannot_be_supplied_as_dict(rig):
    policy, native, bridge, _ = rig
    core, api = d5.D5Receiver(bridge, clock=lambda: NOW), KernelPipe(policy.sid)
    with pytest.raises(d5.D5Blocked, match="UNTRUSTED_PEER_ATTESTATION"):
        receiver.TrustedPeer(object(), d5.D5_LOGON_TRIGGER_PAYLOAD, policy.sid, 1, expires=999999999)
    peer = receiver.attest_peer(api, "SYNTHETIC_HANDLE", d5.D5_LOGON_TRIGGER_PAYLOAD)
    assert core.handle_logon_trigger(d5.D5_LOGON_TRIGGER_PAYLOAD, _peer=peer)["code"] == "LOGON_TRIGGER_DELIVERED"
    with pytest.raises(d5.D5Blocked, match="UNTRUSTED_PEER_ATTESTATION"):
        core.handle_logon_trigger(d5.D5_LOGON_TRIGGER_PAYLOAD, _peer=peer)
    other = receiver.attest_peer(api, "SYNTHETIC_HANDLE", b"OTHER_FRAME")
    with pytest.raises(d5.D5Blocked, match="UNTRUSTED_PEER_ATTESTATION"):
        core.handle_logon_trigger(d5.D5_LOGON_TRIGGER_PAYLOAD, _peer=other)
    assert native.effects == ["START_OR_REUSE_D5_CHROME"]


def test_broken_pipe_never_repeats_an_effect(rig):
    policy, native, bridge, _ = rig
    core = d5.D5Receiver(bridge, clock=lambda: NOW)
    with pytest.raises(d5.D5Blocked, match="PIPE_UNAVAILABLE"):
        receiver.serve_one(core, KernelPipe(policy.sid, fail="read"), "SYNTHETIC_HANDLE")
    assert not native.effects
    with pytest.raises(d5.D5Blocked, match="PIPE_UNAVAILABLE"):
        receiver.serve_one(core, KernelPipe(policy.sid, fail="write"), "SYNTHETIC_HANDLE")
    assert native.snapshot == OWNED
    receiver.serve_one(core, KernelPipe(policy.sid), "SYNTHETIC_HANDLE")
    assert native.effects == ["START_OR_REUSE_D5_CHROME"]


def test_unknown_native_effect_blocks_later_logon_after_restart(rig, monkeypatch):
    policy, native, bridge, _ = rig
    def uncertain(*args):
        native.effects.append("ONE_ATTEMPT")
        raise TimeoutError("PRIVATE_VALUE")
    monkeypatch.setattr(native, "effect", uncertain)
    core, api = d5.D5Receiver(bridge, clock=lambda: NOW), KernelPipe(policy.sid)
    result = receiver.serve_one(core, api, "SYNTHETIC_HANDLE")
    assert result["bridge_receipt"]["code"] == "UNKNOWN_EFFECT_HOLD"
    reopened = d5.Bridge(bridge.enrollment, native, d5.Journal(bridge.journal.path), clock=lambda: NOW)
    result = receiver.serve_one(d5.D5Receiver(reopened, clock=lambda: NOW), api, "SYNTHETIC_HANDLE")
    assert result["code"] == "UNKNOWN_EFFECT_HOLD"
    assert native.effects == ["ONE_ATTEMPT"]
    assert "PRIVATE_VALUE" not in json.dumps(result)






@pytest.fixture(scope="session")
def signing_keys(tmp_path_factory):
    folder = tmp_path_factory.mktemp("d5-ephemeral-crypto")
    assert ROOT not in folder.resolve().parents
    executable = Path(os.environ["SystemRoot"]) / "System32" / "OpenSSH" / "ssh-keygen.exe"
    assert executable.is_file(), "Windows OpenSSH verifier required; no crypto bypass"
    keys = {}
    for name in ("authority", "issuer", "rogue"):
        path = folder / name
        result = subprocess.run([str(executable), "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        assert result.returncode == 0, "ephemeral Ed25519 fixture generation failed"
        keys[name] = path
    return executable, keys


def signed(signing_keys, name, body, namespace, folder):
    executable, keys = signing_keys
    message = folder / (uuid.uuid4().hex + ".message")
    message.write_bytes(d5.canonical(body))
    result = subprocess.run([str(executable), "-Y", "sign", "-f", str(keys[name]), "-n", namespace, str(message)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    assert result.returncode == 0, "ephemeral signing failed"
    return {"body": body, "signature": base64.b64encode(Path(str(message) + ".sig").read_bytes()).decode()}


def public_key(keys, name):
    return " ".join(Path(str(keys[name]) + ".pub").read_text().split()[:2])


@pytest.fixture
def enrolled(rig, tmp_path, monkeypatch, signing_keys):
    policy, native, bridge, holder = rig
    state = tmp_path / "operator-state"
    state.mkdir()
    policy = replace(policy, state_root=state)
    holder[0] = policy
    executable, keys = signing_keys
    anchor = tmp_path / "synthetic-protected-anchor.json"
    anchor.write_bytes(d5.canonical({"schema": "ai-commerce.d5.root.v1", "identity": "d5-authority",
                                    "public_key": public_key(keys, "authority")}))
    monkeypatch.setattr(transport, "fixed_state", lambda: state)
    monkeypatch.setattr(transport, "fixed_anchor", lambda: anchor)
    # Synthetic filesystem ACL seam ONLY, never signature verification.
    def synthetic_protected(path):
        assert path in (anchor, executable)
        return path
    monkeypatch.setattr(transport, "protected_path", synthetic_protected)
    monkeypatch.setattr(d5, "private_path", lambda path, **kwargs: path)
    native_policy = {
        "source_sha": policy.source_sha, "epoch": policy.epoch, "sid": policy.sid, "expires": policy.expires,
        "release_root": str(policy.release_root), "files": policy.files, "chrome_config": str(policy.environment.config_path),
        "local_app_data": str(tmp_path), "state_root": str(state), "key_file": str(state / "control.key"),
        "legacy_tasks": [], "runner_tasks": list(d5.RUNNER_TASKS), "enabled": True, "revoked": False,
        "private_control_plane": True, "public_runner_exposed": False,
    }
    grant = {"schema": transport.GRANT_SCHEMA, "enrollment_id": "a"*32,
        "issued": NOW-1, "expires": NOW+1000, "issuer": "d5-issuer", "issuer_public_key": public_key(keys, "issuer"),
        "custody": {"signer": "OFF_HOST_PRIVATE", "operator": "INDEPENDENT_EXECUTION_BOUNDARY", "audit_sha256": "b"*64},
        "native": native_policy}
    lease = {"schema": transport.LEASE_SCHEMA, "enrollment_id": grant["enrollment_id"],
        "source_sha": policy.source_sha, "epoch": policy.epoch, "grant_sha256": d5.digest(d5.canonical(grant)),
        "serial": 1, "issued": NOW-1, "expires": NOW+59,
        "revoked": False, "signer_exposed": False, "runtime_exposed": False}
    def save_grant(value=grant, signer="authority"):
        (state / "enrollment.json").write_bytes(d5.canonical(signed(signing_keys, signer, value,
            "ai-commerce-d5-enrollment-v1", tmp_path)))
    def save_lease(value=lease, signer="authority"):
        (state / "custody-lease.json").write_bytes(d5.canonical(signed(signing_keys, signer, value,
            "ai-commerce-d5-custody-v1", tmp_path)))
    save_grant()
    save_lease()
    authority = transport.SignedPolicy()
    def current():
        authority.verify(authority.path, NOW)
        return holder[0]
    bridge.enrollment = current
    core = d5.D5Receiver(bridge, authority=authority, clock=lambda: NOW)
    def request(op="STATUS", number=1, patch=None, signer="issuer", namespace="ai-commerce-d5-request-v1"):
        body = {"schema": transport.REQUEST_SCHEMA, "op": op, "request_id": f"{number:032x}",
            "nonce": f"{number+1000:032x}", "issued": NOW, "expires": NOW+30,
            "source_sha": policy.source_sha, "epoch": policy.epoch, "enrollment_id": grant["enrollment_id"],
            "issue_number": 446, "dry_run": False}
        body.update(patch or {})
        return signed(signing_keys, signer, body, namespace, tmp_path)
    return core, native, bridge, request, grant, lease, save_grant, save_lease, authority


def test_authenticated_synthetic_lifecycle_preserves_all_runners_and_profile(enrolled):
    core, native, bridge, request, *_ = enrolled
    lifecycle = [
        ("STATUS", "NOT_RUNNING"), ("MIGRATION_PREFLIGHT", "MIGRATION_READY"),
        ("RETIRE_NEW_AUTOSTART", "RETIRED"), ("INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER", "LAUNCHER_INSTALLED"),
        ("INSTALL_SINGLE_LOGON_AUTOSTART", "REGISTERED"), ("START_OR_REUSE_D5_CHROME", "READY_ATTACH"),
        ("START_OR_REUSE_D5_CHROME", "READY_ATTACH"), ("VERIFY_CDP_9333_AND_PLAYWRIGHT", "READY_ATTACH"),
        ("STOP_OWNED_D5_CHROME", "STOPPED"), ("RECOVER_BOUNDED", "READY_ATTACH"),
        ("STOP_OWNED_D5_CHROME", "STOPPED"), ("REMOVE_D5_AUTOSTART", "AUTOSTART_REMOVED"),
        ("ROLLBACK", "ROLLED_BACK_DISABLED"),
    ]
    for number, (op, code) in enumerate(lifecycle, 1):
        result = core.handle_request(request(op, number))
        assert result["code"] == code, (op, result)
        assert result["request_id"] == f"{number:032x}"
        assert result["wake_sent"] is False
    api = KernelPipe(bridge.enrollment().sid)
    for _ in range(2):
        result = receiver.serve_one(core, api, "SYNTHETIC_HANDLE")
        assert result["bridge_receipt"]["code"] == "READY_ATTACH"
    assert native.effects.count("START_OR_REUSE_D5_CHROME") == 3
    assert native.profile == b"private cookies preserved" and set(native.tasks) == set(d5.RUNNER_TASKS)


@pytest.mark.parametrize("op", d5.OPS)
def test_authenticated_dry_run_catalog_has_no_effect(enrolled, op):
    core, native, _, request, *_ = enrolled
    assert core.handle_request(request(op, patch={"dry_run": True}))["code"] == "DRY_RUN"
    assert not native.effects and not native.attaches


@pytest.mark.parametrize("patch", [
    {"expires": NOW}, {"issued": NOW+1}, {"expires": NOW+61}, {"source_sha": "f"*40},
    {"epoch": 2}, {"enrollment_id": "f"*32}, {"epoch": True}, {"op": "SHELL"},
    {"command": "PRIVATE"}, {"script": "PRIVATE"}, {"path": "PRIVATE"},
    {"pid": 22}, {"env": {}}, {"dry_run": 1}, {"nonce": "wrong"},
])
def test_signed_but_invalid_schema_or_stale_envelope_cannot_mutate(enrolled, patch):
    core, native, _, request, *_ = enrolled
    result = core.handle_request(request("START_OR_REUSE_D5_CHROME", patch=patch))
    assert result["status"] == "BLOCKED" and not native.effects
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["LF", "CRLF"])
@pytest.mark.parametrize("damage", [None, "body", "header"], ids=["valid", "tampered-body", "invalid-header"])
def test_real_crypto_verifies_openssh_armor_line_endings(enrolled, newline, damage):
    core, native, _, request, *_ = enrolled
    value = request()
    armor = base64.b64decode(value["signature"], validate=True)
    armor = armor.replace(b"\r\n", b"\n").replace(b"\n", newline)
    assert armor.startswith(b"-----BEGIN SSH SIGNATURE-----" + newline)
    if damage == "body":
        value["body"]["dry_run"] = True
    elif damage == "header":
        armor = armor.replace(b"-----BEGIN SSH SIGNATURE-----" + newline,
                              b"-----BEGIN SSH SIGNATURE----- \n", 1)
    value["signature"] = base64.b64encode(armor).decode("ascii")
    result = core.handle_request(value)
    assert result["code"] == ("NOT_RUNNING" if damage is None else "SIGNATURE_INVALID"), result
    assert native.effects == [] and native.attaches == 0


def test_real_verifier_keeps_programdata_without_runner_credentials(enrolled, monkeypatch):
    core, native, _, request, *_ = enrolled
    value = request()
    expected = {key.upper(): value for key, value in os.environ.items()
                if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PROGRAMDATA"}}
    assert expected.get("PROGRAMDATA"), "Windows OpenSSH initialization needs PROGRAMDATA"
    for key in ("GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "SSH_AUTH_SOCK", "PYTHONPATH"):
        monkeypatch.setenv(key, "SYNTHETIC_RUNNER_CREDENTIAL_DO_NOT_INHERIT")
    run = subprocess.run
    verified = []

    def inspect_environment(argv, **kwargs):
        assert argv[1:3] == ["-Y", "verify"]
        actual = {key.upper(): value for key, value in kwargs["env"].items()}
        assert actual == expected, "Offline verifier must retain only its Windows initialization allowlist"
        verified.append(True)
        return run(argv, **kwargs)  # Real OpenSSH; no signature/return-code stub.

    monkeypatch.setattr(subprocess, "run", inspect_environment)
    result = core.handle_request(value)
    assert result["code"] == "NOT_RUNNING", result
    assert verified, "The request must pass the real OpenSSH verifier"
    assert native.effects == [] and native.attaches == 0


@pytest.mark.parametrize("mode", ["unsigned", "rogue", "wrong-namespace", "tampered"])
def test_real_crypto_rejects_unsigned_forged_issuer_and_tamper(enrolled, mode):
    core, native, _, request, *_ = enrolled
    value = request("START_OR_REUSE_D5_CHROME", signer="rogue" if mode == "rogue" else "issuer",
                    namespace="wrong-namespace" if mode == "wrong-namespace" else "ai-commerce-d5-request-v1")
    if mode == "unsigned":
        value["signature"] = ""
    elif mode == "tampered":
        value["body"]["dry_run"] = True
    assert core.handle_request(value)["code"] == "SIGNATURE_INVALID"
    assert native.effects == []


def test_request_id_and_nonce_replay_survive_restart(enrolled):
    core, native, bridge, request, *rest = enrolled
    assert core.handle_request(request())["code"] == "NOT_RUNNING"
    reopened = d5.Bridge(bridge.enrollment, native, d5.Journal(bridge.journal.path), clock=lambda: NOW)
    restarted = d5.D5Receiver(reopened, authority=rest[-1], clock=lambda: NOW)
    assert restarted.handle_request(request())["code"] == "REQUEST_REPLAY"
    assert restarted.handle_request(request(number=2, patch={"nonce": f"{1001:032x}"}))["code"] == "REQUEST_REPLAY"


@pytest.mark.parametrize("patch", [{"revoked": True}, {"signer_exposed": True}, {"runtime_exposed": True},
                                  {"expires": NOW}, {"issued": NOW-61}, {"serial": 0}])
def test_signed_revocation_or_exposed_custody_stops_effects(enrolled, patch):
    core, native, _, request, _, lease, _, save, _ = enrolled
    save({**lease, **patch})
    assert core.handle_request(request("START_OR_REUSE_D5_CHROME"))["code"] == "REVOKED_OR_STALE"
    assert native.effects == []


def test_lease_rollback_and_reenrollment_do_not_clear_one_time_pin(enrolled):
    core, native, _, request, grant, lease, save_grant, save_lease, _ = enrolled
    save_lease({**lease, "serial": 2})
    assert core.handle_request(request())["code"] == "NOT_RUNNING"
    save_lease(lease)
    assert core.handle_request(request(number=2))["code"] == "ENROLLMENT_REUSE_DENIED"
    changed = {**grant, "enrollment_id": "f"*32}
    save_grant(changed)
    save_lease({**lease, "serial": 3, "enrollment_id": changed["enrollment_id"], "grant_sha256": d5.digest(d5.canonical(changed))})
    assert core.handle_request(request(number=2))["code"] == "ENROLLMENT_REUSE_DENIED"
    assert native.effects == []


def test_untrusted_authority_and_absent_custody_are_not_deployment(enrolled):
    core, native, _, request, grant, _, save_grant, _, _ = enrolled
    save_grant(grant, signer="rogue")
    assert core.handle_request(request())["code"] == "SIGNATURE_INVALID"
    save_grant({**grant, "custody": {**grant["custody"], "signer": "SAME_SID_RUNNER"}})
    assert core.handle_request(request())["code"] == "BLOCKED_NOT_DEPLOYED"
    assert native.effects == []


def test_no_private_signer_no_authority_is_blocked(enrolled):
    _, native, _, request, *_ = enrolled
    assert d5.D5IngressBoundary().evaluate_ingress_request(request())["code"] == "BLOCKED_NOT_DEPLOYED"
    assert native.effects == []


def test_signed_stop_can_close_only_identity_verified_claim(enrolled):
    core, native, _, request, *_ = enrolled
    native.snapshot = CLAIMED.copy()
    assert core.handle_request(request("START_OR_REUSE_D5_CHROME"))["code"] == "PROFILE_ALREADY_ACTIVE_NO_CDP"
    assert native.effects == []
    assert core.handle_request(request("STOP_OWNED_D5_CHROME", 2))["code"] == "STOPPED"
    assert native.effects == ["STOP_OWNED_D5_CHROME"]


def test_authenticated_unknown_and_broken_response_never_retry(enrolled, monkeypatch):
    core, native, bridge, request, *_ = enrolled
    def uncertain(*args):
        native.effects.append("ONE_ATTEMPT")
        raise TimeoutError("PRIVATE_COMMAND_LINE")
    monkeypatch.setattr(native, "effect", uncertain)
    assert core.handle_request(request("START_OR_REUSE_D5_CHROME"))["code"] == "UNKNOWN_EFFECT_HOLD"
    assert core.handle_request(request("START_OR_REUSE_D5_CHROME", 2))["code"] == "UNKNOWN_EFFECT_HOLD"
    assert native.effects == ["ONE_ATTEMPT"]
    with sqlite3.connect(bridge.journal.path) as db:
        assert db.execute("SELECT state FROM requests").fetchall() == [("UNKNOWN",)]


def test_runnable_event_preparation_transport_cli_and_sanitized_receipt(enrolled, tmp_path, monkeypatch):
    core, native, bridge, request, *_ = enrolled
    event = {"action": "created", "repository": {"full_name": "trung-via/ai-commerce", "owner": {"login": "trung-via"}},
        "issue": {"number": 446, "user": {"login": "trung-via"}, "title": "[HOSTOPS D5] signed carrier"},
        "comment": {"user": {"login": "trung-via"}, "body": json.dumps(request("START_OR_REUSE_D5_CHROME"))}}
    source, prepared, output = tmp_path / "event.json", tmp_path / "request.json", tmp_path / "receipt.json"
    source.write_bytes(d5.canonical(event))
    assert transport.main(["--prepare-event", str(source), "--output", str(prepared)]) == 0
    def exchange(frame):
        peer = receiver.attest_peer(KernelPipe(bridge.enrollment().sid), "SYNTHETIC_HANDLE", frame)
        return d5.canonical(receiver.dispatch(core, frame, peer))
    monkeypatch.setattr(receiver, "pipe_exchange", exchange)
    assert transport.main(["--request", str(prepared), "--output", str(output)]) == 0
    result = d5.decode(output.read_bytes())
    assert result["code"] == "READY_ATTACH" and result["stable"]
    assert native.effects == ["START_OR_REUSE_D5_CHROME"] and native.attaches == 2
    assert "private" not in output.read_text().lower() and "sid" not in result
    # No overwrite of immutable receipt and no repeat native effect.
    old = output.read_bytes()
    assert transport.main(["--request", str(prepared), "--output", str(output)]) == 2
    assert output.read_bytes() == old and native.effects == ["START_OR_REUSE_D5_CHROME"]


def test_event_carrier_does_not_replace_signature_or_issue_binding(enrolled):
    _, _, _, request, *_ = enrolled
    event = {"action": "opened", "repository": {"full_name": "trung-via/ai-commerce", "owner": {"login": "trung-via"}},
        "issue": {"number": 447, "user": {"login": "trung-via"}, "title": "[HOSTOPS D5] carrier", "body": json.dumps(request())}}
    with pytest.raises(d5.D5Blocked, match="INPUT_INVALID"):
        transport.prepare_event(event)


def test_revocation_is_durable_and_old_good_lease_cannot_reactivate(enrolled):
    core, native, _, request, _, lease, _, save_lease, _ = enrolled
    assert core.handle_request(request())["code"] == "NOT_RUNNING"
    save_lease({**lease, "serial": 2, "revoked": True})
    assert core.handle_request(request(number=2))["code"] == "REVOKED_OR_STALE"
    save_lease(lease)
    assert core.handle_request(request("START_OR_REUSE_D5_CHROME", 3))["code"] == "ENROLLMENT_REUSE_DENIED"
    save_lease({**lease, "serial": 3})
    assert core.handle_request(request("START_OR_REUSE_D5_CHROME", 4))["code"] == "ENROLLMENT_REUSE_DENIED"
    assert native.effects == []


@pytest.mark.parametrize("wrong_name", [True, False])
def test_wrong_desktop_name_or_owner_is_denied_synthetically(monkeypatch, wrong_name):
    class User:
        def GetUserObjectInformationW(self, handle, kind, name, *args):
            name.value = "OtherDesktop" if wrong_name else "Default"
            return True
        def GetUserObjectSecurity(self, *args):
            return True
    class Adv:
        def GetSecurityDescriptorOwner(self, *args):
            return True
    api = object.__new__(receiver.Win32)
    api.u, api.a = User(), Adv()
    monkeypatch.setattr(d5, "_sid_text", lambda _: "other-sid")
    with pytest.raises(d5.D5Blocked, match="UNSUPPORTED_SESSION"):
        api.desktop(123, "operator-sid")


def test_no_private_custody_native_gate_cannot_be_bypassed_by_local_mac(monkeypatch, capsys):
    import io
    import types
    def absent():
        raise d5.D5Blocked("CUSTODY_UNPROVEN")
    monkeypatch.setattr(d5, "SignedPolicy", absent)
    monkeypatch.setattr(d5.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(b'{"mac":"forged"}')))
    assert d5.main(["--native-preflight"]) == 2
    assert capsys.readouterr().out.strip() == '{"code":"BLOCKED_NOT_DEPLOYED"}'


def test_existing_output_refuses_delivery_before_a_different_request(enrolled, tmp_path, monkeypatch):
    _, native, _, request, *_ = enrolled
    source, output = tmp_path / "request.json", tmp_path / "immutable-receipt.json"
    source.write_bytes(d5.canonical(request("START_OR_REUSE_D5_CHROME")))
    output.write_bytes(b"IMMUTABLE")
    def forbidden(*args):
        raise AssertionError("existing output must prevent transport delivery")
    monkeypatch.setattr(d5, "pipe_exchange", forbidden)
    assert d5.main(["--request", str(source), "--output", str(output)]) == 2
    assert output.read_bytes() == b"IMMUTABLE" and native.effects == []


@pytest.mark.parametrize("damage", [None, "missing", "runner-drift", "wrong-source", "wrong-session"])
def test_native_stdout_requires_independent_correlated_receipt(rig, monkeypatch, damage):
    policy, _, _, _ = rig
    policy.state_root.mkdir()
    monkeypatch.setattr(d5, "private_path", lambda path, **kwargs: path)
    monkeypatch.setattr(ca, "_interactive_session", lambda: 1)
    payload = {"nonce": "a"*32, "request_id": "b"*32, "op": "MIGRATION_PREFLIGHT", "snapshot": None}
    audit = {"request_id": payload["request_id"], "operation": payload["op"], "source_sha": policy.source_sha,
        "code": "OK", "native": True, "sid": policy.sid, "session": 1,
        "runner_before": dict.fromkeys(d5.RUNNER_TASKS, "d"*64), "runner_after": dict.fromkeys(d5.RUNNER_TASKS, "d"*64),
        "process_before": None, "process_result": {"code": "OK"}, "registered": False, "observed_utc": "2026-11-06T00:00:00Z"}
    if damage == "runner-drift":
        audit["runner_after"][d5.RUNNER_TASKS[0]] = "e"*64
    if damage == "wrong-source":
        audit["source_sha"] = "f"*40
    if damage == "wrong-session":
        audit["session"] = 2
    if damage != "missing":
        (policy.state_root / (payload["nonce"] + ".receipt.json")).write_bytes(d5.canonical(audit))
    if damage:
        with pytest.raises(d5.D5Blocked, match="UNKNOWN_EFFECT_HOLD"):
            d5.WindowsNative().verify_native_receipt(policy, payload, {"code": "OK"})
    else:
        d5.WindowsNative().verify_native_receipt(policy, payload, {"code": "OK"})


def test_same_sid_operator_owned_root_is_not_a_trust_anchor(tmp_path, monkeypatch):
    import ctypes
    import types
    def get_security(*args):
        ctypes.cast(args[3], ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(1)
        ctypes.cast(args[5], ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(1)
        return 0
    adv = types.SimpleNamespace(GetNamedSecurityInfoW=get_security, GetAce=lambda *args: True)
    kernel = types.SimpleNamespace(LocalFree=lambda *args: 0)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: adv if name == "advapi32" else kernel)
    monkeypatch.setattr(d5, "_sid_text", lambda _: "S-1-5-21-111-222-333-1001")
    with pytest.raises(d5.D5Blocked, match="TRUST_ANCHOR_UNPROVEN"):
        d5.protected_path(tmp_path / "operator-owned-anchor.json")


@pytest.mark.parametrize("ambiguous", [False, True])
def test_no_cdp_stop_identity_counts_every_profile_claim_not_only_matching_argv(rig, monkeypatch, ambiguous):
    policy, native, _, _ = rig
    policy.environment.user_data_dir.mkdir()
    exact = {"ProcessId": 101, "ExecutablePath": "exact", "CommandLine": "exact",
             "OwnerSid": policy.sid, "SessionId": 1, "CreationDate": OWNED["created"]}
    # A second profile claimant with wrong flags must make graceful stop HOLD.
    rows = [exact, {**exact, "ProcessId": 102, "ExecutablePath": "wrong", "CommandLine": "wrong"}] if ambiguous else [exact]
    monkeypatch.setattr(ca, "_query_windows", lambda _: {"processes": rows, "listeners": []})
    def active(*args, **kwargs):
        raise ca.AcquisitionBlocked("PROFILE_ALREADY_ACTIVE_NO_CDP")
    monkeypatch.setattr(ca, "evaluate", active)
    monkeypatch.setattr(ca, "_interactive_session", lambda: 1)
    monkeypatch.setattr(ca, "_commandline_argv", lambda text: [text, "--user-data-dir=" + str(policy.environment.user_data_dir)])
    monkeypatch.setattr(ca, "_identity_matches", lambda env, exe, argv: exe == "exact")
    if ambiguous:
        with pytest.raises(d5.D5Blocked, match="OWNERSHIP_UNPROVEN"):
            d5.WindowsNative().observe(policy)
    else:
        assert d5.WindowsNative().observe(policy) == CLAIMED
    assert native.effects == []


@pytest.mark.parametrize("damage", [None, "wrong-command", "wrong-script", "source-drift"])
def test_kernel_logon_peer_must_run_exact_pinned_launcher(rig, monkeypatch, damage):
    import types
    policy, native, _, _ = rig
    policy.state_root.mkdir()
    launcher = policy.state_root / "launcher.ps1"
    launcher.write_bytes(b"SYNTHETIC_REVIEWED_LAUNCHER")
    policy = replace(policy, files={d5.RELEASE_FILES[2]: d5.digest(launcher.read_bytes())})
    if damage == "source-drift":
        launcher.write_bytes(b"UNVERIFIED_LAUNCHER")
    exe = policy.state_root / "powershell.exe"
    monkeypatch.setattr(d5, "SignedPolicy", lambda: types.SimpleNamespace(path=policy.state_root / "enrollment.json"))
    monkeypatch.setattr(d5, "load_enrollment", lambda *args, **kwargs: policy)
    monkeypatch.setattr(d5, "private_path", lambda path, **kwargs: path)
    monkeypatch.setattr(d5, "protected_path", lambda path: path)
    monkeypatch.setattr(ca, "_powershell", lambda: exe)
    argv = [str(exe), "-NoProfile", "-NonInteractive", "-File", str(launcher), "-Logon"]
    if damage == "wrong-command":
        argv[3] = "-Command"
    if damage == "wrong-script":
        argv[4] = str(policy.state_root / "arbitrary.ps1")
    monkeypatch.setattr(ca, "_commandline_argv", lambda _: argv)
    calls = []
    def probe(args, **kwargs):
        calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0, stdout=d5.canonical({"image": str(exe), "argv": "SYNTHETIC_OS_REPLY"}))
    monkeypatch.setattr(subprocess, "run", probe)
    api = object.__new__(d5.Win32)
    if damage:
        with pytest.raises(d5.D5Blocked, match="UNTRUSTED_PEER_ATTESTATION"):
            api.logon_launcher(321)
    else:
        api.logon_launcher(321)
        assert calls[0][1]["env"]["AI_COMMERCE_D5_PEER_PID"] == "321"
        assert calls[0][1]["timeout"] == 5
    assert native.effects == []
