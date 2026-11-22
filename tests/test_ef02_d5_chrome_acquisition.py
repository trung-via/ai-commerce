"""D5 Chrome Acquisition: synthetic and read-only checks; never launch Chrome."""
from contextlib import nullcontext
from pathlib import Path
import json
import re
import os
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace

import pytest
from ai_commerce.remote import chrome_acquisition as ca


@pytest.mark.skipif(os.name != "nt", reason="Native Windows paths")
@pytest.mark.parametrize("channel, failure, alongside_stable", [
    ("7", None, False), ("7-preview", None, False), ("7.7", None, False), ("7.12.3", None, False),
    ("7.7", "missing", False), ("7.7", "reparse", False), ("7.7", "foreign-owner", False),
    ("7.7", "unsigned", False), ("7.7", "old-major", False),
    ("7.7", "reparse", True), ("7.7", "foreign-owner", True),
    ("7.7", "unsigned", True), ("7.7", "old-major", True),
])
def test_powershell_resolver_fails_closed_on_machine_install_evidence(
        tmp_path, monkeypatch, channel, failure, alongside_stable):
    root = tmp_path.resolve() / "Program Files"
    installed = root / "PowerShell" / channel / "pwsh.exe"
    installed.parent.mkdir(parents=True)
    if failure != "missing":
        installed.touch()
    stable = root / "PowerShell" / "7" / "pwsh.exe"
    if alongside_stable:
        stable.parent.mkdir()
        stable.touch()
    shadow = tmp_path / "PRIVATE_CALLER_BIN"
    shadow.mkdir()
    (shadow / "pwsh.exe").touch()
    (shadow / "powershell.exe").touch()
    monkeypatch.setenv("PATH", str(shadow))
    monkeypatch.setenv("ProgramFiles", str(shadow))
    monkeypatch.setattr(ca, "_native_program_files", lambda: root)
    no_links = ca._no_links
    if failure == "reparse":
        monkeypatch.setattr(ca, "_no_links", lambda path: path != installed.parent and no_links(path))
    checked = []

    def custody(path):
        checked.append(path)
        if path == installed and failure == "foreign-owner":
            raise ca.AcquisitionBlocked("POWERSHELL_UNAVAILABLE")

    monkeypatch.setattr(ca, "_verify_powershell_custody", custody)
    monkeypatch.setattr(ca, "_microsoft_signed_powershell", lambda path: path != installed or failure != "unsigned")
    monkeypatch.setattr(ca, "_powershell_file_major", lambda path: 5 if path == installed and failure == "old-major" else 7)
    if alongside_stable:
        assert ca._powershell() == stable
    elif failure:
        with pytest.raises(ca.AcquisitionBlocked, match="^POWERSHELL_UNAVAILABLE$"):
            ca._powershell()
    else:
        assert ca._powershell() == installed
    expected_checks = [stable] if alongside_stable else []
    if failure not in {"missing", "reparse"}:
        expected_checks.append(installed)
    assert checked == expected_checks


def fixture(tmp_path):
    home = tmp_path / "local"
    base = home / "AI-Commerce"
    profile_root = base / "Chrome-Doorbell"
    (profile_root / "Default").mkdir(parents=True)
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"stub")
    repo = tmp_path / "checkout"
    repo.mkdir()
    config = base / "chrome.json"
    data = {
        "version": 1,
        "executable": str(executable),
        "user_data_dir": str(profile_root),
        "profile_directory": "Default",
        "cdp_endpoint": "http://127.0.0.1:9222",
        "allow_launch": True,
        "exclusive_user_data": True,
    }
    config.write_text(json.dumps(data), encoding="utf-8")
    return config, repo, home, data


def read(config, repo, home):
    return ca.load_environment(config, repo, local_app_data=home)


def owner(env, *, pid=123, session=1):
    return {"ProcessId": pid, "ExecutablePath": str(env.executable),
            "CommandLine": "trusted browser", "SessionId": session}


def args(env):
    return env.command()


def read_inventory(env, *, pid=123, session=1):
    return {"listeners": [{"LocalAddress": "127.0.0.1", "OwningProcess": pid}],
            "processes": [owner(env, pid=pid, session=session)]}


def test_environment_command_exact_profile_and_no_automation_flag(tmp_path):
    env = read(*fixture(tmp_path)[:3])
    assert env.port == 9222
    assert f"--user-data-dir={env.user_data_dir}" in env.command()
    assert "--profile-directory=Default" in env.command()
    assert "--remote-debugging-address=127.0.0.1" in env.command()
    assert "--enable-automation" not in env.command()
    assert not any("--no-sandbox" in x for x in env.command())
    assert not any("chatgpt.com" in x for x in env.command())


@pytest.mark.parametrize("sid,created,allowed", [
    ("S-1-5-21-1-2-3-1001", "2026-11-05T01:00:00Z", True),
    ("S-1-5-21-1-2-3-1002", "2026-11-05T01:00:00Z", False),
    (None, "2026-11-05T01:00:00Z", False),
    ("S-1-5-21-1-2-3-1001", None, False),
    ("S-1-5-21-1-2-3-1001", "", False),
])
def test_enrolled_d5_owner_requires_sid_and_creation_time(tmp_path, sid, created, allowed):
    env = read(*fixture(tmp_path)[:3])
    snapshot = read_inventory(env)
    snapshot["processes"][0].update(OwnerSid=sid, CreationDate=created)
    def probe():
        return ca.evaluate(env, snapshot, session_id=1, parse=lambda _: args(env),
                           expected_sid="S-1-5-21-1-2-3-1001")
    if allowed:
        assert probe() == ("OWNED", 123)
    else:
        with pytest.raises(ca.AcquisitionBlocked, match="BROWSER_OWNERSHIP_UNPROVEN"):
            probe()


@pytest.mark.parametrize("extra", ["split", "duplicate", "uppercase"])
def test_ambiguous_profile_switch_is_never_absent(tmp_path, extra):
    env = read(*fixture(tmp_path)[:3])
    command = env.command()
    if extra == "split":
        command = [x for x in command if not x.startswith("--user-data-dir=")]
        command += ["--user-data-dir", str(env.user_data_dir)]
    elif extra == "duplicate":
        command += [f"--user-data-dir={env.user_data_dir}"]
    else:
        command = [x.replace("--user-data-dir=", "--USER-DATA-DIR=") for x in command]
    with pytest.raises(ca.AcquisitionBlocked, match="BROWSER_OWNERSHIP_UNPROVEN"):
        ca.evaluate(env, {"listeners": [], "processes": [owner(env)]}, session_id=1, parse=lambda _: command)


def test_old_acquisition_playwright_attach_never_closes_browser_or_reads_pages(monkeypatch, tmp_path):
    import sys
    import types
    env = read(*fixture(tmp_path)[:3])
    events = []
    class Browser:
        def is_connected(self): return True
        def close(self): pytest.fail("browser close forbidden")
        @property
        def contexts(self): pytest.fail("content access forbidden")
    class Chromium:
        def connect_over_cdp(self, endpoint, timeout):
            events.append((endpoint, timeout))
            return Browser()
    class Client:
        chromium = Chromium()
        def __enter__(self): return self
        def __exit__(self, *_): events.append("transport-stopped")
    monkeypatch.setitem(sys.modules, "playwright.sync_api", types.SimpleNamespace(sync_playwright=Client))
    ca.verify_playwright_attach(env)
    assert events == [(env.endpoint, 3000), "transport-stopped"]


@pytest.mark.parametrize("patch,expected", [
    ({"exclusive_user_data": False}, "CONFIG_INVALID"),
    ({"allow_launch": "true"}, "CONFIG_INVALID"),
    ({"profile_directory": "..\\Default"}, "CONFIG_INVALID"),
    ({"cdp_endpoint": "http://0.0.0.0:9222"}, "CDP_ENDPOINT_INVALID"),
    ({"cdp_endpoint": "http://127.0.0.1:9222/json"}, "CDP_ENDPOINT_INVALID"),
    ({"cdp_endpoint": "https://127.0.0.1:9222"}, "CDP_ENDPOINT_INVALID"),
    ({"cdp_endpoint": "http://127.0.0.1:22"}, "CDP_ENDPOINT_INVALID"),
])
def test_invalid_config_fails_closed(tmp_path, patch, expected):
    config, repo, home, data = fixture(tmp_path)
    config.write_text(json.dumps({**data, **patch}))
    with pytest.raises(ca.AcquisitionBlocked, match=expected):
        read(config, repo, home)


def test_reject_cross_project_profile_and_default_chrome(tmp_path):
    config, repo, home, data = fixture(tmp_path)
    outside = tmp_path / "AIOS-Local" / "Profile"
    (outside / "Default").mkdir(parents=True)
    data["user_data_dir"] = str(outside)
    config.write_text(json.dumps(data))
    with pytest.raises(ca.AcquisitionBlocked, match="PROFILE_NOT_DEDICATED"):
        read(config, repo, home)


def test_duplicate_keys_rejected(tmp_path):
    config, repo, home, data = fixture(tmp_path)
    config.write_text(json.dumps(data)[:-1] + ', "allow_launch": true}')
    with pytest.raises(ca.AcquisitionBlocked, match="CONFIG_DUPLICATE_KEY"):
        read(config, repo, home)


def test_config_cannot_live_in_checkout(tmp_path):
    config, repo, home, data = fixture(tmp_path)
    inside = repo / "config.json"
    inside.write_text(json.dumps(data))
    with pytest.raises(ca.AcquisitionBlocked, match="CONFIG_IN_REPOSITORY"):
        read(inside, repo, home)


def test_owner_requires_exact_process_profile_port_and_session(tmp_path):
    env = read(*fixture(tmp_path)[:3])
    snapshot = read_inventory(env)
    assert ca.evaluate(env, snapshot, session_id=1,
                       parse=lambda _: args(env)) == ("OWNED", 123)
    with pytest.raises(ca.AcquisitionBlocked, match="BROWSER_OWNERSHIP_UNPROVEN"):
        ca.evaluate(env, snapshot, session_id=2, parse=lambda _: args(env))
    with pytest.raises(ca.AcquisitionBlocked, match="BROWSER_OWNERSHIP_UNPROVEN"):
        ca.evaluate(env, snapshot, session_id=1,
                    parse=lambda _: args(env) + ["--remote-debugging-port=9223"])


def test_absent_port_and_unclaimed_profile(tmp_path):
    env = read(*fixture(tmp_path)[:3])
    assert ca.evaluate(env, {"listeners": [], "processes": []},
                       session_id=1) == ("ABSENT", None)
    with pytest.raises(ca.AcquisitionBlocked, match="PROFILE_ALREADY_ACTIVE_NO_CDP"):
        ca.evaluate(env, {"listeners": [], "processes": [owner(env)]},
                    session_id=1, parse=lambda _: args(env))


def test_wrong_owner_or_non_loopback_never_attached(tmp_path):
    env = read(*fixture(tmp_path)[:3])
    snap = read_inventory(env)
    snap["listeners"][0]["LocalAddress"] = "0.0.0.0"
    with pytest.raises(ca.AcquisitionBlocked, match="CDP_NOT_LOOPBACK_OWNED"):
        ca.evaluate(env, snap, session_id=1, parse=lambda _: args(env))
    snap["listeners"][0]["LocalAddress"] = "127.0.0.1"
    snap["listeners"][0]["OwningProcess"] = 999
    with pytest.raises(ca.AcquisitionBlocked, match="BROWSER_OWNERSHIP_UNPROVEN"):
        ca.evaluate(env, snap, session_id=1, parse=lambda _: args(env))


def test_ambiguous_listener_and_unknown_process_are_blocked(tmp_path):
    env = read(*fixture(tmp_path)[:3])
    snap = read_inventory(env)
    snap["listeners"].append(snap["listeners"][0].copy())
    with pytest.raises(ca.AcquisitionBlocked, match="BROWSER_OWNERSHIP_UNPROVEN"):
        ca.evaluate(env, snap, session_id=1, parse=lambda _: args(env))
    snap["listeners"] = []
    snap["processes"][0]["CommandLine"] = None
    with pytest.raises(ca.AcquisitionBlocked, match="BROWSER_OWNERSHIP_UNPROVEN"):
        ca.evaluate(env, snap, session_id=1, parse=lambda _: args(env))


def test_no_real_process_or_network_in_synthetic_tests(monkeypatch, tmp_path):
    env = read(*fixture(tmp_path)[:3])
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("Chrome launch forbidden"))
    monkeypatch.setattr(ca, "_query_windows", lambda *_a: pytest.fail("real Windows probe forbidden"))
    assert ca.evaluate(env, {"listeners": [], "processes": []},
                       session_id=1) == ("ABSENT", None)
    if os.name != "nt":
        with pytest.raises(ca.AcquisitionBlocked, match="WINDOWS_INTERACTIVE_REQUIRED"):
            ca.run(env, launch=True, opt_in=True)


# These tests exercise the production command and CDP decision gates without
# attaching to a browser, contacting Google, or starting Windows processes.

def _cdp_payload(env, **overrides):
    return {
        "Browser": "Chrome/156.0.0.0",
        "Protocol-Version": "1.3",
        "webSocketDebuggerUrl": f"ws://127.0.0.1:{env.port}/devtools/browser/fixture"
    } | overrides


class FakeResponse:
    status = 200
    def __init__(self, payload):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, _limit):
        return self.body


@pytest.mark.parametrize("payload,allowed", [
    (None, True),
    ({"Browser": "NotChrome/1.0"}, False),
    ({"Protocol-Version": "unexpected"}, False),
    ({"webSocketDebuggerUrl": "ws://127.0.0.1:9555/devtools/browser/fixture"}, False),
    ({"webSocketDebuggerUrl": "ws://attacker.invalid:9222/devtools/browser/fixture"}, False),
    ({"webSocketDebuggerUrl": "ws://127.0.0.1:9222/other"}, False),
])
def test_cdp_handshake_requires_real_browser_protocol(monkeypatch, tmp_path, payload, allowed):
    env = read(*fixture(tmp_path)[:3])
    captures = []
    class FakeOpener:
        def open(self, request, timeout):
            captures.append((request.full_url, timeout))
            body = _cdp_payload(env, **(payload or {}))
            return FakeResponse(body)
    monkeypatch.setattr(ca, "build_opener", lambda *args: FakeOpener())
    if allowed:
        ca._probe_cdp(env)
    else:
        with pytest.raises(ca.AcquisitionBlocked, match="CDP_PROTOCOL_UNAVAILABLE"):
            ca._probe_cdp(env)
    assert captures == [("http://127.0.0.1:9222/json/version", 2)]


def test_cdp_rejects_timeout_invalid_json_and_oversized_response(monkeypatch, tmp_path):
    env = read(*fixture(tmp_path)[:3])
    class FakeOpener:
        def __init__(self, payload=None, fail=False):
            self.payload, self.fail = payload, fail
        def open(self, request, timeout):
            if self.fail:
                raise TimeoutError("simulated")
            return FakeResponse(self.payload)
    for payload, fail in [(b"not json", False), (b"x" * 8193, False), (None, True)]:
        monkeypatch.setattr(ca, "build_opener", lambda *a, _p=payload, _f=fail: FakeOpener(_p, _f))
        with pytest.raises(ca.AcquisitionBlocked, match="CDP_PROTOCOL_UNAVAILABLE"):
            ca._probe_cdp(env)


def _fake_windows_run(monkeypatch, *, states, outcome=None):
    import types
    monkeypatch.setattr(ca, "os", types.SimpleNamespace(name="nt", environ=dict(os.environ)))
    monkeypatch.setattr(ca, "_interactive_session", lambda: 5)
    monkeypatch.setattr(ca, "_require_operator_control", lambda env: None)
    monkeypatch.setattr(ca, "_verify_google_chrome", lambda env: None)
    monkeypatch.setattr(ca, "_query_windows", lambda port: {})
    monkeypatch.setattr(ca.time, "sleep", lambda seconds: None)
    handshake = []
    monkeypatch.setattr(ca, "_probe_cdp", lambda env: handshake.append(env.port))
    states = iter(states)
    monkeypatch.setattr(ca, "evaluate", lambda env, data, session_id: next(states))
    spawns = []
    class Child:
        pid = 500
        def poll(self): return outcome
    def fake_spawn(command, **kwargs):
        spawns.append((command, kwargs))
        return Child()
    monkeypatch.setattr(ca.subprocess, "Popen", fake_spawn)
    return spawns, handshake


def test_launch_one_process_and_real_cdp_verification_gate(monkeypatch, tmp_path):
    env = read(*fixture(tmp_path)[:3])
    spawns, handshake = _fake_windows_run(monkeypatch, states=[
        ("ABSENT", None), ("OWNED", 500)])
    result = ca.run(env, launch=True, opt_in=True)
    assert result["status"] == "READY_ATTACH" and result["cdp_verified"]
    assert result["launched"] is True
    assert handshake == [9222] and len(spawns) == 1
    command, options = spawns[0]
    assert command == env.command()
    assert options["env"] == ca._child_environment()
    assert "GITHUB_TOKEN" not in options["env"]


def test_launch_unknown_owner_does_not_retry(monkeypatch, tmp_path):
    env = read(*fixture(tmp_path)[:3])
    spawns, _ = _fake_windows_run(monkeypatch, states=[
        ("ABSENT", None), ("OWNED", 501)])
    with pytest.raises(ca.AcquisitionBlocked, match="LAUNCH_OWNER_MISMATCH"):
        ca.run(env, launch=True, opt_in=True)
    assert len(spawns) == 1


def test_probe_owned_requires_handshake(monkeypatch, tmp_path):
    env = read(*fixture(tmp_path)[:3])
    spawns, handshake = _fake_windows_run(monkeypatch, states=[("OWNED", 123)])
    assert ca.run(env, launch=False, opt_in=False)["cdp_verified"] is True
    assert handshake == [9222] and not spawns


def test_launch_not_authorized_has_no_spawn(monkeypatch, tmp_path):
    env = read(*fixture(tmp_path)[:3])
    spawns, _ = _fake_windows_run(monkeypatch, states=[("ABSENT", None)])
    with pytest.raises(ca.AcquisitionBlocked, match="LAUNCH_NOT_AUTHORIZED"):
        ca.run(env, launch=True, opt_in=False)
    assert not spawns


def test_child_environment_is_allowlist_not_blacklist(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "private")
    monkeypatch.setenv("AI_COMMERCE_OTHER_SECRET", "private")
    monkeypatch.setenv("AIOS_TOKEN", "private")
    child = ca._child_environment()
    assert "GITHUB_TOKEN" not in child
    assert "AI_COMMERCE_OTHER_SECRET" not in child
    assert "AIOS_TOKEN" not in child


def test_google_signature_fails_closed(monkeypatch, tmp_path):
    import types
    env = read(*fixture(tmp_path)[:3])
    monkeypatch.setattr(ca, "_powershell", lambda: tmp_path / "pwsh.exe")
    monkeypatch.setattr(ca.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout=b""))
    with pytest.raises(ca.AcquisitionBlocked, match="CHROME_EXECUTABLE_UNTRUSTED"):
        ca._verify_google_chrome(env)


def test_private_control_checks_config_and_profile(monkeypatch, tmp_path):
    from ai_commerce.remote import local_completion_handoff
    env = read(*fixture(tmp_path)[:3])
    seen = []
    monkeypatch.setattr(local_completion_handoff, "_private_identity", seen.append)
    ca._require_operator_control(env)
    assert seen == [env.config_path, env.user_data_dir,
                    env.user_data_dir / env.profile_directory]


def test_actual_windows_command_line_quoting_when_available():
    if os.name != "nt":
        pytest.skip("Win32 parsing only available on Windows")
    command = '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" "--user-data-dir=C:\\Users\\My User\\Chrome" --profile-directory=Default'
    argv = ca._commandline_argv(command)
    assert argv[0].endswith("chrome.exe")
    assert argv[1] == r"--user-data-dir=C:\Users\My User\Chrome"
    assert argv[2] == "--profile-directory=Default"


def test_attended_d5_cdp_wrapper_fixed_user_only_modes_and_no_send():
    script = (Path(__file__).resolve().parents[1] /
              "scripts" / "AI-Commerce-D5-Attended-CDP.ps1").read_text(encoding="utf-8")
    assert "ValidateSet('Inspect', 'RepairConfig', 'StartCDP', 'PrepareChat')" in script
    assert "LOCAL_OPERATOR_ONLY" in script
    assert "INTERACTIVE_SESSION_REQUIRED" in script
    assert "USE_NON_ELEVATED_POWERSHELL" in script
    assert "CHECKOUT_REPOSITORY_UNVERIFIED" in script
    assert "CHECKOUT_TRACKED_CHANGES" in script
    assert "EXISTING_PRIVATE_CONFIG_REQUIRED" in script
    assert "PRIVATE_CONFIG_AMBIGUOUS_EXPLICIT_PATH_REQUIRED" in script
    assert "START-CDP-9333" in script
    assert "CONFIG_NOT_EXACT_9333" in script
    assert "http://127.0.0.1:9333" in script
    assert "PREPARE-EXACT-CHAT" in script
    assert "ai_commerce.remote.chrome_acquisition" in script
    assert "ai_commerce.remote.wake_readiness_flow" in script
    assert "--opt-in-open-chat" in script
    assert "--opt-in" in script
    assert "Get-Command python.exe" in script
    assert "PYTHONPATH" in script
    assert "D5_WAKE_SENT=False" in script
    assert "READY_FOR_PREPARE_NO_SEND" in script
    assert "wake-readiness.json" in script
    assert "Status" not in script.split("ValidateSet(", 1)[1].split(")", 1)[0]
    for forbidden in (
        "Invoke-Expression", "iex ", "Start-Process", "Stop-Process",
        "Stop-ScheduledTask", "Register-ScheduledTask", "New-ScheduledTask",
        "Remove-Item -Recurse", "Set-Content", "Out-File",
        "send-once", "local_wake_entry send", "deliver_wake",
        "chrome.exe --remote-debugging-port",
    ):
        assert forbidden not in script


def test_attended_d5_repair_is_explicit_local_fixed_port_write_only():
    """No public Issue payload can choose config or invoke a private write."""
    script = (Path(__file__).resolve().parents[1] /
              "scripts" / "AI-Commerce-D5-Attended-CDP.ps1").read_text(encoding="utf-8")
    repair_start = script.index("if ($Mode -eq 'RepairConfig')")
    repair_end = script.index("if ($Mode -eq 'PrepareChat')", repair_start)
    repair = script[repair_start:repair_end]
    assert "REPAIR_FIXED_CANDIDATES_ONLY" in repair
    for known in (
        r"Chrome-Acquisition\config.json", "chrome.json", "chrome-acquisition.json",
    ):
        assert known in repair
    for guard in (
        "REPAIR_KNOWN_CONFIG_ABSENT", "REPAIR_KNOWN_CONFIG_AMBIGUOUS",
        "REPAIR_PREFLIGHT_UNVERIFIED", "REPAIR_BROWSER_STATE_NOT_IDLE",
        "REPAIR_CONFIG_SCHEMA_CHANGED", "REPAIR_CONFIG_CHANGED_RECHECK",
        "REPAIR_STAGED_ACL_UNVERIFIED", "REPAIR_STAGING_UNVERIFIED",
        "REPAIR_POST_WRITE_UNKNOWN_RECONCILE",
    ):
        assert guard in repair
    assert "$preflightExit = $LASTEXITCODE" in repair
    assert "Get-Repair-Preflight-Code -Lines $preflight" in repair
    assert "Stop-Bounded ('REPAIR_PREFLIGHT_' + $cause)" in repair
    assert repair.index("Stop-Bounded ('REPAIR_PREFLIGHT_' + $cause)") < repair.index(
        "Read-Host 'Type REPAIR-CONFIG-9333")
    assert "REPAIR_REPARSE_DENIED" in repair
    assert "REPAIR_REPARSE_CHECK_UNAVAILABLE" in repair
    assert "[IO.FileAttributes]::ReparsePoint" in repair
    assert "REPAIR-CONFIG-9333" in repair
    assert repair.index("REPAIR-CONFIG-9333") < repair.index("[IO.File]::Replace(")
    assert repair.index("Get-Acl -LiteralPath $ChromeConfig") < repair.index("WriteAllText(")
    assert repair.index("Set-Acl -LiteralPath $stage") < repair.index("WriteAllText(")
    assert "Get-FileHash -LiteralPath $ChromeConfig -Algorithm SHA256" in repair
    assert "[IO.File]::Replace($stage, $ChromeConfig, $backup, $false)" in repair
    assert "CONFIG_9333_UPDATED_NO_LAUNCH" in repair
    assert "CONFIG_9333_ALREADY" in repair
    assert "D5_WAKE_SENT=False" in repair
    assert "CHROME_ACQUISITION_BLOCKED" not in repair  # collapse private errors
    for forbidden in ("GITHUB_EVENT_PATH", "github.rest", "local_wake_entry",
                      "wake_adapter", "subprocess.Popen", "LaunchChrome",
                      "Invoke-Expression", "iex ", "Move-Item -Force"):
        assert forbidden not in repair


def test_repair_preflight_diagnostic_contract_has_exact_source_owned_code_catalog():
    script = (Path(__file__).resolve().parents[1] /
              "scripts" / "AI-Commerce-D5-Attended-CDP.ps1").read_text(encoding="utf-8")
    acquisition = (Path(__file__).resolve().parents[1] /
                   "src" / "ai_commerce" / "remote" / "chrome_acquisition.py").read_text(encoding="utf-8")
    helper = script.split("function Get-Repair-Preflight-Code", 1)[1].split(
        "if ($env:GITHUB_ACTIONS", 1)[0]
    literal_enum = helper.split("$allowed = @(", 1)[1].split("\n    )", 1)[0]
    allowed = set(re.findall(r"'([A-Z0-9_]{3,90})'", literal_enum))
    defined = set(re.findall(r'AcquisitionBlocked\("([A-Z0-9_]+)"\)', acquisition))
    assert allowed == defined and len(allowed) >= 25
    assert "CHROME_ACQUISITION_BLOCKED: ([A-Z0-9_]{1,90})$" in helper
    assert "$seen.Count -ne 1" in helper
    assert "Write-Output $value" not in helper
    assert "Write-Host $value" not in helper
    assert "Write-Error $value" not in helper
    assert "Invoke-Expression" not in helper
    assert "Out-File" not in helper
    assert "D5_WAKE_SENT=False" in script
    assert "[IO.File]::Replace(" in script


def test_repair_preflight_diagnostic_decoder_native_powershell(tmp_path):
    """Execute only the isolated read-only enum filter on actual Windows PS."""
    if os.name != "nt":
        pytest.skip("Windows PowerShell native-only filter verification")
    shell = str(ca._powershell())
    script = (Path(__file__).resolve().parents[1] /
              "scripts" / "AI-Commerce-D5-Attended-CDP.ps1").read_text(encoding="utf-8")
    isolated = "function Get-Repair-Preflight-Code" + script.split(
        "function Get-Repair-Preflight-Code", 1)[1].split("if ($env:GITHUB_ACTIONS", 1)[0]
    synthetic = r"""
$ErrorActionPreference = 'Stop'
$cases = @(
    @{ Rows=@('CHROME_ACQUISITION_BLOCKED: OPERATOR_OWNERSHIP_UNPROVEN'); Expected='OPERATOR_OWNERSHIP_UNPROVEN' },
    @{ Rows=@('python.exe : CHROME_ACQUISITION_BLOCKED: PROFILE_ALREADY_ACTIVE_NO_CDP'); Expected='PROFILE_ALREADY_ACTIVE_NO_CDP' },
    @{ Rows=@('CHROME_ACQUISITION_BLOCKED: WINDOWS_INTERACTIVE_REQUIRED'); Expected='WINDOWS_INTERACTIVE_REQUIRED' },
    @{ Rows=@('CHROME_ACQUISITION_BLOCKED: UNTRUSTED_SECRET_CODE'); Expected='UNVERIFIED' },
    @{ Rows=@('PRIVATE SECRET PATH=foo', 'Traceback (most recent call last)'); Expected='UNVERIFIED' },
    @{ Rows=@('CHROME_ACQUISITION_BLOCKED: CONFIG_INVALID', 'CHROME_ACQUISITION_BLOCKED: PROFILE_LOCKED'); Expected='UNVERIFIED' },
    @{ Rows=@('CHROME_ACQUISITION_BLOCKED: CONFIG_INVALID', 'PRIVATE_SECRET_123'); Expected='CONFIG_INVALID' }
)
foreach ($case in $cases) {
    $actual = Get-Repair-Preflight-Code -Lines $case.Rows
    if ($actual -cne $case.Expected) { throw 'FILTER_MISMATCH' }
}
Write-Output 'D5_REPAIR_PREFLIGHT_ENUM_FILTER_PASS'
"""
    standalone = tmp_path / "d5-enum-filter.ps1"
    standalone.write_text(isolated + "\n" + synthetic, encoding="utf-8")
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(standalone)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=25)
    assert result.returncode == 0, result.stderr[-1300:]
    assert result.stdout.strip() == "D5_REPAIR_PREFLIGHT_ENUM_FILTER_PASS"
    assert "PRIVATE_SECRET_123" not in result.stdout


def test_d5_repair_config_parent_path_is_valid_and_bounded():
    script = (Path(__file__).resolve().parents[1] /
              "scripts" / "AI-Commerce-D5-Attended-CDP.ps1").read_text(encoding="utf-8")
    repair = script.split("if ($Mode -eq 'RepairConfig')", 1)[1].split(
        "if ($Mode -eq 'PrepareChat')", 1)[0]
    assert "$dir = [IO.Path]::GetDirectoryName($ChromeConfig)" in repair
    assert "Split-Path -LiteralPath $ChromeConfig -Parent" not in script
    assert "GetDirectoryName($ChromeConfig)" in repair.split(
        "Read-Host 'Type REPAIR-CONFIG-9333", 1)[1]
    assert repair.index("GetDirectoryName($ChromeConfig)") < repair.index(
        "[IO.File]::Open($stage")
    assert "D5_WAKE_SENT=False" in repair


def test_d5_repair_config_parent_path_native_windows(tmp_path):
    """PowerShell 7 path selection on synthetic input."""
    if os.name != "nt":
        pytest.skip("Windows-only PowerShell path acceptance")
    shell = str(ca._powershell())
    source = (Path(__file__).resolve().parents[1] /
              "scripts" / "AI-Commerce-D5-Attended-CDP.ps1").read_text(encoding="utf-8")
    repair = source.split("if ($Mode -eq 'RepairConfig')", 1)[1].split(
        "if ($Mode -eq 'PrepareChat')", 1)[0]
    assignment = "$dir = [IO.Path]::GetDirectoryName($ChromeConfig)"
    assert assignment in repair
    safe = tmp_path / "Chrome-Acquisition"
    cfg = safe / "config.json"
    test = (
        "$ErrorActionPreference='Stop';"
        "$ChromeConfig=" + "'" + str(cfg).replace("'", "''") + "';"
        + assignment + ";"
        "if($dir -cne [IO.Path]::GetFullPath("
        "'" + str(safe).replace("'", "''") + "')){exit 3};"
        "Write-Output 'D5_PARENT_PATH_PASS'"
    )
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", test],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr[-500:]
    assert result.stdout.strip() == "D5_PARENT_PATH_PASS"


def test_attended_d5_cdp_wrapper_powershell_parser_on_hosted_windows():
    if os.name != "nt":
        pytest.skip("PowerShell AST parser acceptance only on Windows")
    shell = str(ca._powershell())
    source = (Path(__file__).resolve().parents[1] /
              "scripts" / "AI-Commerce-D5-Attended-CDP.ps1")
    # Parse only. Do not invoke the script, process Chrome or read private paths.
    check = (
        "$found=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "'" + str(source).replace("'", "''") +
        "',[ref]$null,[ref]$found) | Out-Null;"
        "if(@($found).Count -gt 0) {$found | Out-String | Write-Host; exit 1}"
    )
    run = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", check],
        capture_output=True, text=True, check=False, timeout=30
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_wh01b_9444_ensure_is_exact_closed_one_shot_no_send():
    """The protected Issue ingress uses the existing verified Chrome acquisition.

    This test is a static policy preflight. Live interactive launch and ownership
    require a separate real Windows host receipt, never a source-only CI PASS.
    """
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
    assert workflow.count("  hostops-d5-9444-ensure:") == 1
    ensure = workflow.split("  hostops-d5-9444-ensure:", 1)[1].split("\n# Personal-mode D5 bootstrap only.", 1)[0]
    assert "[HOSTOPS D5 9444 ENSURE] " in ensure
    assert '{"schema":"ai-commerce.d5.ensure9444.v1","operation":"ENSURE_OWNED_9444","parameters":{}}' in ensure
    assert "github.event_name == 'issues' && github.event.action == 'opened'" in ensure
    assert "github.event.issue.user.login == github.repository_owner" in ensure
    assert "ai-commerce-hostops" in ensure
    assert "cancel-in-progress: false" in ensure
    assert "NO_INTERACTIVE_SESSION" in ensure
    assert "DEDICATED_CONFIG_NOT_ENROLLED" in ensure
    # HostOps must use the same finite registry as native 9444 diagnostics,
    # including the existing D5-Isolated-9444-Trial slot.
    assert "ca._config_9444_registry(local, checkout)" in ensure
    assert "len(registry.present) != 1" in ensure
    assert "ca.load_environment(registry.present[0], checkout, local_app_data=local)" in ensure
    assert "AI_COMMERCE_ENSURE_9444_PRIVATE_CONFIG" not in ensure
    assert "ELEVATED" in ensure
    assert 'env.port != 9444 or env.endpoint != "http://127.0.0.1:9444"' in ensure
    assert "ca.run(env, launch=True, opt_in=True)" in ensure
    assert "CDP_9444_LAUNCHED" in ensure and "CDP_9444_REUSED" in ensure
    assert "wake_sent=$false" in ensure
    assert "effect_state=$effectState" in ensure
    assert "UNKNOWN_EFFECT" in ensure
    assert "NoProfile" not in ensure  # no arbitrary PowerShell dispatch
    assert "--user-data-dir=" not in ensure  # source-owned private config only


@pytest.mark.parametrize("states, expected_started, count", [
    ([("OWNED", 123)], False, 0),
    ([("ABSENT", None), ("OWNED", 500)], True, 1),
])
def test_wh01b_real_port_9444_reuse_or_launch_once(
        monkeypatch, tmp_path, states, expected_started, count):
    """The EXISTING production acquisition implements the fixed 9444 ensure.

    Synthetic Windows inventory: existing owned browser -> reuse with no
    process; independently absent browser -> exactly one launch; no UI/Send.
    """
    config, repo, home, data = fixture(tmp_path)
    data["cdp_endpoint"] = "http://127.0.0.1:9444"
    config.write_text(json.dumps(data), encoding="utf-8")
    env = read(config, repo, home)
    assert env.port == 9444
    spawns, handshake = _fake_windows_run(monkeypatch, states=states)
    receipt = ca.run(env, launch=True, opt_in=True)
    assert receipt == {
        "status": "READY_ATTACH", "cdp_verified": True,
        "browser_accessed": True, "launched": expected_started
    }
    assert handshake == [9444]
    assert len(spawns) == count


def test_wh01b_9444_profile_lock_disallows_second_browser(
        monkeypatch, tmp_path):
    config, repo, home, data = fixture(tmp_path)
    data["cdp_endpoint"] = "http://127.0.0.1:9444"
    config.write_text(json.dumps(data), encoding="utf-8")
    env = read(config, repo, home)
    spawns, handshake = _fake_windows_run(
        monkeypatch, states=[("ABSENT", None)])
    (env.user_data_dir / "SingletonLock").write_text("owned")
    with pytest.raises(ca.AcquisitionBlocked, match="PROFILE_LOCKED"):
        ca.run(env, launch=True, opt_in=True)
    assert not spawns and not handshake


# WH-01B enrollment tests are SOURCE ONLY. All Windows identities, signatures,
# inventories and ACLs below are synthetic; no native custody is established.
_OPERATOR_SID = "S-1-5-21-1-2-3-1001"
_REAL_NATIVE_ENROLLMENT = ca._NativeEnrollment


def enrollment_fixture(monkeypatch, tmp_path):
    import types
    from ai_commerce.remote import local_completion_handoff, local_wake_entry
    config, repo, home, data = fixture(tmp_path)
    config.unlink()
    config = home / "AI-Commerce" / "Chrome-Acquisition" / "config.json"
    data.update(cdp_endpoint="http://127.0.0.1:9444", allow_launch=False)
    env = ca._environment_from_value(data, config, repo, local_app_data=home)
    snapshot = read_inventory(env)
    snapshot["listeners"][0]["LocalPort"] = 9444
    snapshot["processes"][0].update(
        OwnerSid=_OPERATOR_SID, CreationDate="2026-11-14T01:00:00Z",
        CommandLine=json.dumps(env.command()))
    events = []
    monkeypatch.setattr(ca, "os", types.SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(ca, "sys", types.SimpleNamespace(
        stdin=types.SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setattr(ca, "_native_local_app_data", lambda: home)
    monkeypatch.setattr(ca, "_interactive_session", lambda: 1)
    monkeypatch.setattr(local_wake_entry, "_windows_process_sid", lambda: _OPERATOR_SID)
    monkeypatch.setattr(local_completion_handoff, "_private_identity", lambda path: _OPERATOR_SID)
    monkeypatch.setattr(ca, "_commandline_argv", json.loads)
    def query(port):
        assert port == 9444
        events.append("inventory")
        return snapshot
    monkeypatch.setattr(ca, "_query_windows", query)
    monkeypatch.setattr(ca, "_verify_google_chrome", lambda env: events.append("signature"))
    def forbidden(*args, **kwargs):
        pytest.fail("Enrollment must not launch, attach, read HTTP/CDP or run real subprocesses")
    for name in ("run", "_probe_cdp", "verify_playwright_attach", "build_opener"):
        monkeypatch.setattr(ca, name, forbidden)
    monkeypatch.setattr(ca.subprocess, "run", forbidden)
    monkeypatch.setattr(ca.subprocess, "Popen", forbidden)

    class SyntheticNative:
        """Offline transaction double; deliberately establishes no native custody."""
        instances = []
        def __init__(self, env, local, session, sid, identity):
            assert (local, session, sid) == (home, 1, _OPERATOR_SID)
            self.env, self.identity = env, identity
            self.effect_possible = False
            self.operations = []
            self.instances.append(self)
        def __enter__(self):
            self.operations.append("pin")
            if self.env.config_path.parent.exists():
                raise ca._EnrollmentHold("ENROLLMENT_RESERVATION_EXISTS")
            return self
        def validate(self):
            self.operations.append("validate")
        def prove_marker(self, path):
            return False
        def create_and_flush(self, payload):
            assert 1 <= len(payload) <= 8192
            self.operations.append("create")
            self.effect_possible = True
            self.env.config_path.parent.mkdir()
            with self.env.config_path.open("xb") as stream:
                stream.write(payload)
            self.operations.append("flush")
        def readback(self, payload):
            self.operations.append("readback")
            assert self.env.config_path.read_bytes() == payload
        def __exit__(self, *args):
            self.operations.append("close")
    monkeypatch.setattr(ca, "_NativeEnrollment", SyntheticNative)
    return env, repo, home, snapshot, events


def assert_enrollment_hold(result, code, *, assessed=False):
    assert result == {"status": "HOLD", "code": code, "candidate_assessed": assessed,
                      "enrolled": False, "allow_launch": False, "browser_accessed": False,
                      "launched": False, "chat_identity_proven": False, "send_authorized": False}


def test_wh01b_enrollment_assesses_private_candidate_but_never_writes(monkeypatch, tmp_path, capsys):
    env, repo, home, snapshot, events = enrollment_fixture(monkeypatch, tmp_path)
    before = set(home.rglob("*"))
    def no_open(*args, **kwargs):
        pytest.fail("Enrollment assessment must not open or create files")
    monkeypatch.setattr(Path, "open", no_open)
    for _ in range(2):  # Idempotent assessment never enrolls or changes launch policy.
        result = ca.assess_existing_9444(repo)
        assert_enrollment_hold(result, "NATIVE_ATOMIC_ENROLLMENT_REQUIRED", assessed=True)
    assert events == ["inventory", "signature", "inventory", "signature"] * 2
    assert not env.config_path.exists() and set(home.rglob("*")) == before
    assert capsys.readouterr().out == ""
    public = json.dumps(result)
    assert str(home) not in public and _OPERATOR_SID not in public
    assert "CreationDate" not in public and "ProcessId" not in public


def test_wh01b_enrollment_candidate_reuses_ensure_closed_schema(monkeypatch, tmp_path):
    env, repo, home, snapshot, _ = enrollment_fixture(monkeypatch, tmp_path)
    values = []
    original = ca._environment_from_value
    def validate(value, *args, **kwargs):
        values.append(value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(ca, "_environment_from_value", validate)
    assert_enrollment_hold(ca.assess_existing_9444(repo), "NATIVE_ATOMIC_ENROLLMENT_REQUIRED", assessed=True)
    expected = {"version": 1, "executable": str(env.executable),
                "user_data_dir": str(env.user_data_dir), "profile_directory": "Default",
                "cdp_endpoint": "http://127.0.0.1:9444", "exclusive_user_data": True,
                "allow_launch": False}
    assert values == [expected, expected]
    assert env.config_path == home / "AI-Commerce" / "Chrome-Acquisition" / "config.json"
    # Only a synthetic test writes a config; the production assessment never does.
    env.config_path.parent.mkdir()
    env.config_path.write_text(json.dumps(expected), encoding="utf-8")
    loaded = ca.load_environment(env.config_path, repo, local_app_data=home)
    assert loaded == env and loaded.allow_launch is False
    assert ca.evaluate(loaded, snapshot, session_id=1, expected_sid=_OPERATOR_SID,
                       parse=json.loads) == ("OWNED", 123)
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), "ENROLLMENT_CONFIG_EXISTS")


@pytest.mark.parametrize("opt_in", [False, None, 1, "true"])
def test_wh01b_enrollment_disabled_by_default(monkeypatch, tmp_path, opt_in):
    _, repo, _, _, events = enrollment_fixture(monkeypatch, tmp_path)
    assert_enrollment_hold(ca.enroll_existing_9444(repo), "ENROLLMENT_OPT_IN_REQUIRED")
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=opt_in), "ENROLLMENT_OPT_IN_REQUIRED")
    assert events == []


@pytest.mark.parametrize("gate", ["non_windows", "non_interactive", "session_zero", "elevated",
                                  "wrong_token", "github_actions", "github_event", "github_run"])
def test_wh01b_enrollment_requires_independent_local_operator(monkeypatch, tmp_path, gate):
    from ai_commerce.remote import local_wake_entry
    _, repo, _, _, events = enrollment_fixture(monkeypatch, tmp_path)
    expected = "WINDOWS_INTERACTIVE_REQUIRED"
    if gate == "non_windows":
        ca.os.name = "posix"
    elif gate == "non_interactive":
        ca.sys.stdin.isatty = lambda: False
    elif gate == "session_zero":
        monkeypatch.setattr(ca, "_interactive_session", lambda: 0)
        expected = "ENROLLMENT_IDENTITY_UNPROVEN"
    elif gate == "elevated":
        def elevated():
            raise ca.AcquisitionBlocked("ELEVATED_CHROME_DISALLOWED")
        monkeypatch.setattr(ca, "_interactive_session", elevated)
        expected = "ELEVATED_CHROME_DISALLOWED"
    elif gate == "wrong_token":
        monkeypatch.setattr(local_wake_entry, "_windows_process_sid", lambda: "uid:1000")
        expected = "ENROLLMENT_IDENTITY_UNPROVEN"
    else:
        key = {"github_actions": "GITHUB_ACTIONS", "github_event": "GITHUB_EVENT_PATH",
               "github_run": "GITHUB_RUN_ID"}[gate]
        ca.os.environ[key] = "private-runner-value"
        expected = "LOCAL_OPERATOR_ONLY"
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), expected)
    assert not events


@pytest.mark.parametrize("case,code", [
    ("off", "FIRST_SUPERVISED_ENROLLMENT_REQUIRED"),
    ("no_chrome", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("other_process", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("duplicate_listener", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("not_loopback", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("listener_9333", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("listener_9222", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("command_9333", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("command_9222", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("duplicate_port", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("split_port", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("duplicate_profile_switch", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("duplicate_pid", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("another_9444_profile", "ENROLLMENT_CANDIDATE_UNPROVEN"),
    ("duplicate_profile", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("wrong_sid", "ENROLLMENT_IDENTITY_UNPROVEN"),
    ("wrong_session", "ENROLLMENT_IDENTITY_UNPROVEN"),
    ("missing_creation", "ENROLLMENT_IDENTITY_UNPROVEN"),
    ("forged_executable", "BROWSER_OWNERSHIP_UNPROVEN"),
])
def test_wh01b_enrollment_rejects_ambiguous_or_foreign_browser(monkeypatch, tmp_path, case, code):
    env, repo, _, snapshot, events = enrollment_fixture(monkeypatch, tmp_path)
    proc, row = snapshot["processes"][0], snapshot["listeners"][0]
    command = env.command()
    if case == "off":
        snapshot.update(listeners=[], processes=[])
    elif case == "no_chrome":
        snapshot["processes"] = []
    elif case == "other_process":
        row["OwningProcess"] = 999
    elif case == "duplicate_listener":
        snapshot["listeners"].append(dict(row))
    elif case == "not_loopback":
        row["LocalAddress"] = "0.0.0.0"
    elif case.startswith("listener_"):
        row["LocalPort"] = int(case.split("_")[1])
    elif case.startswith("command_"):
        command = [a.replace("--remote-debugging-port=9444", "--remote-debugging-port=" +
                             case.split("_")[1]) for a in command]
    elif case == "duplicate_port":
        command += ["--remote-debugging-port=9444"]
    elif case == "split_port":
        command += ["--remote-debugging-port", "9444"]
    elif case == "duplicate_profile_switch":
        command += ["--profile-directory=Default"]
    elif case == "duplicate_pid":
        snapshot["processes"].append(dict(proc))
    elif case in {"another_9444_profile", "duplicate_profile"}:
        other = command.copy()
        if case == "another_9444_profile":
            other = [a.replace(str(env.user_data_dir), str(env.user_data_dir.parent / "Other")) for a in other]
        else:
            other = [a.replace("--remote-debugging-port=9444", "--remote-debugging-port=9333") for a in other]
        snapshot["processes"].append({**proc, "ProcessId": 456, "CommandLine": json.dumps(other)})
    elif case == "wrong_sid":
        proc["OwnerSid"] = "S-1-5-21-1-2-3-1002"
    elif case == "wrong_session":
        proc["SessionId"] = 2
    elif case == "missing_creation":
        proc["CreationDate"] = None
    elif case == "forged_executable":
        fake = tmp_path / "forged" / "chrome.exe"
        fake.parent.mkdir()
        fake.write_bytes(b"forged")
        proc["ExecutablePath"] = str(fake)
    proc["CommandLine"] = json.dumps(command)
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), code)
    assert events == ["inventory"]
    assert not ca._NativeEnrollment.instances and not env.config_path.parent.exists()


@pytest.mark.parametrize("relative", [
    "Chrome-Acquisition/config.json", "D5-Isolated-9444-Trial/config.json",
    "WakeHub/config.json", "WakeHub-9444/config.json", "chrome.json", "chrome-acquisition.json",
])
def test_wh01b_enrollment_refuses_any_existing_candidate(monkeypatch, tmp_path, relative):
    _, repo, home, _, events = enrollment_fixture(monkeypatch, tmp_path)
    existing = home / "AI-Commerce" / relative
    existing.parent.mkdir(exist_ok=True)
    existing.write_bytes(b"existing private content")
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), "ENROLLMENT_CONFIG_EXISTS")
    assert existing.read_bytes() == b"existing private content" and events == []


def test_wh01b_enrollment_refuses_old_config_duplicates(monkeypatch, tmp_path):
    _, repo, home, _, events = enrollment_fixture(monkeypatch, tmp_path)
    for name in ("chrome.json", "chrome-acquisition.json"):
        (home / "AI-Commerce" / name).write_bytes(b"old config")
    assert_enrollment_hold(ca.assess_existing_9444(repo), "ENROLLMENT_CONFIG_EXISTS")
    assert not events


@pytest.mark.parametrize("location", ["config", "private_root", "profile", "executable"])
@pytest.mark.parametrize("link", ["symlink", "junction"])
def test_wh01b_enrollment_refuses_symlink_or_junction_paths(monkeypatch, tmp_path, location, link):
    import stat
    import types
    env, repo, home, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    target = {"config": env.config_path, "private_root": home / "AI-Commerce",
              "profile": env.user_data_dir, "executable": env.executable}[location]
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == target:
            return types.SimpleNamespace(st_file_attributes=0x400,
                st_mode=stat.S_IFLNK if link == "symlink" else stat.S_IFDIR)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    result = ca.assess_existing_9444(repo)
    assert result["candidate_assessed"] is False and result["enrolled"] is False
    assert result["code"] in {"ENROLLMENT_PATH_UNSAFE", "CHROME_PATH_INVALID"}


def test_wh01b_enrollment_does_not_select_another_chrome_port(monkeypatch, tmp_path):
    env, repo, home, snapshot, _ = enrollment_fixture(monkeypatch, tmp_path)
    other_directory = home / "Google" / "Chrome" / "User Data"
    other_directory.mkdir(parents=True)
    other_command = [a.replace(str(env.user_data_dir), str(other_directory)).replace(
        "--remote-debugging-port=9444", "--remote-debugging-port=9222") for a in env.command()]
    snapshot["processes"].append({**snapshot["processes"][0], "ProcessId": 456,
                                  "CommandLine": json.dumps(other_command)})
    assert_enrollment_hold(ca.assess_existing_9444(repo), "NATIVE_ATOMIC_ENROLLMENT_REQUIRED", assessed=True)


def test_wh01b_enrollment_environment_cannot_supply_operator_or_private_root(monkeypatch, tmp_path):
    _, repo, _, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    ca.os.environ.update(LOCALAPPDATA=str(tmp_path / "foreign"), USERNAME="forged-owner",
                         AI_COMMERCE_OPERATOR_SID="S-1-5-21-1-2-3-1002")
    assert_enrollment_hold(ca.assess_existing_9444(repo), "NATIVE_ATOMIC_ENROLLMENT_REQUIRED", assessed=True)


@pytest.mark.parametrize("project", ["Google/Chrome/User Data", "Other-Project/Profile"])
def test_wh01b_enrollment_refuses_default_google_and_foreign_project(monkeypatch, tmp_path, project):
    env, repo, home, snapshot, _ = enrollment_fixture(monkeypatch, tmp_path)
    directory = home / project
    (directory / "Default").mkdir(parents=True)
    snapshot["processes"][0]["CommandLine"] = json.dumps(
        [a.replace(str(env.user_data_dir), str(directory)) for a in env.command()])
    assert_enrollment_hold(ca.assess_existing_9444(repo), "PROFILE_NOT_DEDICATED")


@pytest.mark.parametrize("guard", ["unsigned", "inaccessible", "unsafe_acl", "profile_lock", "unknown"])
def test_wh01b_enrollment_stops_on_signature_custody_or_unknown(monkeypatch, tmp_path, guard):
    from ai_commerce.remote import local_completion_handoff
    env, repo, _, _, events = enrollment_fixture(monkeypatch, tmp_path)
    expected = "CHROME_EXECUTABLE_UNTRUSTED"
    if guard in {"unsigned", "inaccessible"}:
        def unsigned(env):
            raise ca.AcquisitionBlocked("CHROME_EXECUTABLE_UNTRUSTED")
        monkeypatch.setattr(ca, "_verify_google_chrome", unsigned)
    elif guard == "unsafe_acl":
        def acl(path):
            raise local_completion_handoff.HandoffBlocked("PRIVATE_SID_OR_PATH_MUST_NOT_ESCAPE")
        monkeypatch.setattr(local_completion_handoff, "_private_identity", acl)
        expected = "OPERATOR_OWNERSHIP_UNPROVEN"
    elif guard == "profile_lock":
        (env.user_data_dir / "SingletonLock").write_bytes(b"unproven owner")
        expected = "ENROLLMENT_PROFILE_LOCK_UNPROVEN"
    else:
        def unknown(port):
            raise RuntimeError("PRIVATE_PATH_SESSION_SID")
        monkeypatch.setattr(ca, "_query_windows", unknown)
        expected = "ENROLLMENT_ASSESSMENT_UNKNOWN"
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), expected)
    assert len(events) <= 1  # No retry of an unavailable or unknown observation.


@pytest.mark.parametrize("change", ["pid_reuse", "new_pid", "stale_listener", "new_config", "dropped_permissions", "token"])
@pytest.mark.parametrize("enroll", [False, True])
def test_wh01b_enrollment_rechecks_without_replay(monkeypatch, tmp_path, change, enroll):
    from copy import deepcopy
    from ai_commerce.remote import local_completion_handoff, local_wake_entry
    env, repo, _, snapshot, events = enrollment_fixture(monkeypatch, tmp_path)
    current = deepcopy(snapshot)
    expected = "ENROLLMENT_OBSERVATION_CHANGED"
    if change == "pid_reuse":
        current["processes"][0]["CreationDate"] = "2026-11-14T02:00:00Z"
    elif change == "new_pid":
        current["processes"][0]["ProcessId"] = 789
        current["listeners"][0]["OwningProcess"] = 789
    elif change == "stale_listener":
        current["processes"] = []
        expected = "ENROLLMENT_CANDIDATE_UNPROVEN"
    def query(port):
        assert port == 9444
        events.append("inventory")
        return snapshot if events.count("inventory") == 1 else current
    monkeypatch.setattr(ca, "_query_windows", query)
    def signature(env):
        events.append("signature")
        if change == "new_config":
            env.config_path.parent.mkdir()
            env.config_path.write_bytes(b"concurrent private config")
        elif change == "dropped_permissions":
            def dropped(path):
                raise local_completion_handoff.HandoffBlocked("PERMISSIONS_DROPPED")
            monkeypatch.setattr(local_completion_handoff, "_private_identity", dropped)
        elif change == "token":
            monkeypatch.setattr(local_wake_entry, "_windows_process_sid", lambda: "S-1-5-21-1-2-3-1002")
    monkeypatch.setattr(ca, "_verify_google_chrome", signature)
    if change == "new_config":
        expected = "ENROLLMENT_CONFIG_EXISTS"
    elif change == "dropped_permissions":
        expected = "OPERATOR_OWNERSHIP_UNPROVEN"
    result = ca.enroll_existing_9444(repo, opt_in=True) if enroll else ca.assess_existing_9444(repo)
    assert_enrollment_hold(result, expected)
    assert events.count("inventory") <= 2
    if change == "new_config":
        assert env.config_path.read_bytes() == b"concurrent private config"


def test_wh01b_enrollment_sanitizes_untrusted_diagnostic(monkeypatch, tmp_path, capsys):
    _, repo, _, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    def unknown(port):
        raise ca.AcquisitionBlocked("PRIVATE_PATH_SESSION_SID")
    monkeypatch.setattr(ca, "_query_windows", unknown)
    assert_enrollment_hold(ca.assess_existing_9444(repo), "ENROLLMENT_ASSESSMENT_UNKNOWN")
    assert capsys.readouterr().out == ""


def test_wh01b_enrollment_cli_no_input_selected_config_and_no_launch(monkeypatch, tmp_path, capsys):
    env, repo, _, _, events = enrollment_fixture(monkeypatch, tmp_path)
    assert ca.main(["enroll-existing-9444", "--checkout", str(repo)]) == 2
    assert_enrollment_hold(json.loads(capsys.readouterr().out), "ENROLLMENT_OPT_IN_REQUIRED")
    assert not events
    assert ca.main(["assess-existing-9444", "--checkout", str(repo)]) == 2
    assert_enrollment_hold(json.loads(capsys.readouterr().out), "NATIVE_ATOMIC_ENROLLMENT_REQUIRED", assessed=True)
    events.clear()
    with pytest.raises(SystemExit) as error:
        ca.main(["enroll-existing-9444", "--checkout", str(repo), "--config", str(env.config_path), "--opt-in"])
    assert error.value.code == 2 and events == []


def assert_enrollment_unknown(result):
    expected = ca._enrollment_hold("ENROLLMENT_ASSESSMENT_UNKNOWN", assessed=True)
    expected.update(status="UNKNOWN_EFFECT", code="UNKNOWN_EFFECT")
    assert result == expected


def test_wh01b_atomic_enrollment_source_only_positive(monkeypatch, tmp_path, capsys):
    env, repo, home, _, events = enrollment_fixture(monkeypatch, tmp_path)
    result = ca.enroll_existing_9444(repo, opt_in=True)
    expected = ca._enrollment_hold("NATIVE_ATOMIC_ENROLLMENT_REQUIRED", assessed=True)
    expected.update(status="ENROLLED", code="CONFIG_VERIFIED_CREATED", enrolled=True)
    assert result == expected
    assert events == ["inventory", "signature"] * 3
    transaction, = ca._NativeEnrollment.instances
    assert transaction.operations == ["pin", "validate", "create", "flush", "readback",
                                       "validate", "readback", "close"]
    payload = json.loads(env.config_path.read_bytes())
    assert payload == {"version": 1, "executable": str(env.executable),
        "user_data_dir": str(env.user_data_dir), "profile_directory": "Default",
        "cdp_endpoint": "http://127.0.0.1:9444", "exclusive_user_data": True, "allow_launch": False}
    assert ca.load_environment(env.config_path, repo, local_app_data=home) == env
    public = json.dumps(result)
    for private in (str(home), str(env.executable), _OPERATOR_SID, "123", "webSocket", "account"):
        assert private not in public
    assert capsys.readouterr().out == ""
    before = env.config_path.read_bytes()
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), "ENROLLMENT_CONFIG_EXISTS")
    assert len(ca._NativeEnrollment.instances) == 1 and env.config_path.read_bytes() == before


def test_wh01b_atomic_enrollment_cli_success_is_explicit(monkeypatch, tmp_path, capsys):
    _, repo, _, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    assert ca.main(["enroll-existing-9444", "--checkout", str(repo), "--opt-in"]) == 0
    assert json.loads(capsys.readouterr().out)["code"] == "CONFIG_VERIFIED_CREATED"


def test_wh01b_atomic_enrollment_unavailable_native_api_holds(monkeypatch, tmp_path):
    env, repo, _, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    def unavailable(*args):
        raise ca._EnrollmentHold("NATIVE_HANDLE_RELATIVE_UNAVAILABLE")
    monkeypatch.setattr(ca, "_NativeEnrollment", unavailable)
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), "NATIVE_HANDLE_RELATIVE_UNAVAILABLE")
    assert not env.config_path.parent.exists()


@pytest.mark.parametrize("marker", ca._MARKERS)
@pytest.mark.parametrize("holder", ["same", "none", "foreign", "shared", "stale"])
def test_wh01b_atomic_enrollment_requires_positive_live_marker_holder(monkeypatch, tmp_path, marker, holder):
    env, repo, _, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    lock = env.user_data_dir / marker
    lock.write_bytes(b"opaque; never parsed for PID")
    observations = []
    def prove(self, path):
        observations.append(path)
        return holder == "same"  # Native RM/token/creation-time seam, not marker contents.
    monkeypatch.setattr(ca._NativeEnrollment, "prove_marker", prove)
    result = ca.enroll_existing_9444(repo, opt_in=True)
    if holder == "same":
        assert result["code"] == "CONFIG_VERIFIED_CREATED" and observations == [lock] * 3
    else:
        assert_enrollment_hold(result, "ENROLLMENT_PROFILE_LOCK_UNPROVEN")
        assert observations == [lock] and not env.config_path.parent.exists()
    assert lock.read_bytes() == b"opaque; never parsed for PID"


@pytest.mark.parametrize("stage", ["reservation", "write", "flush", "readback", "acl", "close", "interrupted"])
def test_wh01b_atomic_enrollment_uncertain_storage_never_retries_or_cleans(monkeypatch, tmp_path, stage):
    env, repo, _, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    native = ca._NativeEnrollment
    create = native.create_and_flush
    readback = native.readback
    def fail(self, payload):
        if stage == "reservation":
            self.effect_possible = True
            env.config_path.parent.mkdir()
            raise OSError("private reservation error")
        create(self, payload)
        if stage in {"write", "flush"}:
            raise OSError("private storage error")
        if stage == "interrupted":
            raise KeyboardInterrupt
    def fail_readback(self, payload):
        if stage in {"readback", "acl"}:
            raise OSError("private readback/ACL error")
        readback(self, payload)
    monkeypatch.setattr(native, "create_and_flush", fail)
    monkeypatch.setattr(native, "readback", fail_readback)
    if stage == "close":
        def close(self, *args):
            raise OSError("private close error")
        monkeypatch.setattr(native, "__exit__", close)
    assert_enrollment_unknown(ca.enroll_existing_9444(repo, opt_in=True))
    assert env.config_path.parent.is_dir()
    before = env.config_path.read_bytes() if env.config_path.exists() else None
    again = ca.enroll_existing_9444(repo, opt_in=True)
    assert again["status"] == "HOLD" and again["code"] in {
        "ENROLLMENT_CONFIG_EXISTS", "ENROLLMENT_RESERVATION_EXISTS"}
    assert before == (env.config_path.read_bytes() if env.config_path.exists() else None)
    assert sum(tx.operations.count("create") for tx in native.instances) <= 1


@pytest.mark.parametrize("change", ["pid_reuse", "pid", "listener", "sid", "session", "command", "token", "signature"])
def test_wh01b_atomic_enrollment_post_write_divergence_is_unknown(monkeypatch, tmp_path, change):
    from copy import deepcopy
    from ai_commerce.remote import local_wake_entry
    env, repo, _, snapshot, events = enrollment_fixture(monkeypatch, tmp_path)
    original = ca._NativeEnrollment.create_and_flush
    def mutate(self, payload):
        original(self, payload)
        other = deepcopy(snapshot)
        proc = other["processes"][0]
        if change == "pid_reuse":
            proc["CreationDate"] = "2026-11-14T02:00:00Z"
        elif change == "pid":
            proc["ProcessId"] = other["listeners"][0]["OwningProcess"] = 456
        elif change == "listener":
            other["listeners"] = []
        elif change == "sid":
            proc["OwnerSid"] = "S-1-5-21-1-2-3-1002"
        elif change == "session":
            proc["SessionId"] = 2
        elif change == "command":
            proc["CommandLine"] = json.dumps(env.command() + ["--no-sandbox"])
        elif change == "token":
            monkeypatch.setattr(local_wake_entry, "_windows_process_sid", lambda: "S-1-5-21-1-2-3-1002")
        elif change == "signature":
            def unsigned(env):
                raise ca.AcquisitionBlocked("CHROME_EXECUTABLE_UNTRUSTED")
            monkeypatch.setattr(ca, "_verify_google_chrome", unsigned)
        def query(port):
            assert port == 9444
            events.append("inventory")
            return other
        monkeypatch.setattr(ca, "_query_windows", query)
    monkeypatch.setattr(ca._NativeEnrollment, "create_and_flush", mutate)
    assert_enrollment_unknown(ca.enroll_existing_9444(repo, opt_in=True))
    assert env.config_path.is_file()
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), "ENROLLMENT_CONFIG_EXISTS")


@pytest.mark.parametrize("switch", ["--no-sandbox", "--remote-allow-origins=*", "https://chatgpt.com/",
                                   "--remote-debugging-pipe", "--profile-directory=Other"])
def test_wh01b_atomic_enrollment_disallows_unregistered_switches(monkeypatch, tmp_path, switch):
    env, repo, _, snapshot, _ = enrollment_fixture(monkeypatch, tmp_path)
    snapshot["processes"][0]["CommandLine"] = json.dumps(env.command() + [switch])
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), "ENROLLMENT_CANDIDATE_UNPROVEN")
    assert not ca._NativeEnrollment.instances and not env.config_path.parent.exists()


def test_wh01b_atomic_enrollment_native_prewrite_guard_has_no_effect(monkeypatch, tmp_path):
    env, repo, _, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    def validate(self):
        raise ca._EnrollmentHold("ENROLLMENT_NATIVE_CUSTODY_UNPROVEN")
    monkeypatch.setattr(ca._NativeEnrollment, "validate", validate)
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), "ENROLLMENT_NATIVE_CUSTODY_UNPROVEN")
    assert not env.config_path.parent.exists()


def test_wh01b_atomic_enrollment_reservation_race_is_create_new(monkeypatch, tmp_path):
    env, repo, _, _, _ = enrollment_fixture(monkeypatch, tmp_path)
    def race(self, payload):
        env.config_path.parent.mkdir()
        env.config_path.write_bytes(b"other writer")
        raise ca._EnrollmentHold("ENROLLMENT_CONFIG_EXISTS")
    monkeypatch.setattr(ca._NativeEnrollment, "create_and_flush", race)
    assert_enrollment_hold(ca.enroll_existing_9444(repo, opt_in=True), "ENROLLMENT_CONFIG_EXISTS")
    assert env.config_path.read_bytes() == b"other writer"


def test_wh01b_native_timestamp_preserves_100ns_and_offset():
    assert ca._creation_ticks("1601-01-01T00:00:00.0000001Z") == 1
    assert ca._creation_ticks("2026-11-14T01:00:00.1234567Z") == ca._creation_ticks("2026-11-14T08:00:00.1234567+07:00")
    with pytest.raises(ca._EnrollmentHold, match="ENROLLMENT_IDENTITY_UNPROVEN"):
        ca._creation_ticks("unproven timestamp")


def offline_native(monkeypatch):
    """Bind real ctypes structures exclusively to offline function doubles."""
    import ctypes as c
    class Function:
        def __init__(self): self.handler = lambda *args: 1
        def __call__(self, *args):
            return self.handler(*args)
    class Library:
        def __getattr__(self, name):
            value = Function()
            setattr(self, name, value)
            return value
    monkeypatch.setattr(c, "WinDLL", lambda *a, **k: Library(), raising=False)
    return _REAL_NATIVE_ENROLLMENT(None, None, 1, _OPERATOR_SID, None)


def test_wh01b_native_create_uses_relative_no_reparse_protected_create_new(monkeypatch):
    """Inspect the real syscall boundary with fake DLLs; no Windows effects in CI."""
    tx = offline_native(monkeypatch)
    c = tx.c
    calls = []
    def create(*args):
        attrs = args[2]._obj
        calls.append((attrs.RootDirectory, attrs.SecurityDescriptor, attrs.Attributes,
                      attrs.ObjectName.contents.Length, args[6:9]))
        args[0]._obj.value = 101
        args[3]._obj.Information = 2
        return 0
    tx.n.NtCreateFile.handler = create
    tx.descriptor = c.c_void_p(321)
    tx._info = lambda *args: (1, 0, 2, 0, 0)
    handle = tx._relative(99, "config.json", directory=False, create=True, write=True)
    assert handle.value == 101 and tx.effect_possible is True
    assert calls == [(99, 321, 0x1040, len("config.json") * 2, (1, 2, 0x200060))]


@pytest.mark.parametrize("kind", ["reparse", "hardlink", "wrong_type", "non_disk", "unavailable"])
def test_wh01b_native_handle_rejects_unsafe_file_identity(monkeypatch, kind):
    tx = offline_native(monkeypatch)
    def info(handle, pointer):
        value = pointer._obj
        value.Attributes = 0x400 if kind == "reparse" else 0x10 if kind == "wrong_type" else 0
        value.Links = 2 if kind == "hardlink" else 1
        return kind != "unavailable"
    tx.k.GetFileInformationByHandle.handler = info
    tx.k.GetFileType.handler = lambda handle: 2 if kind == "non_disk" else 1
    with pytest.raises(ca._EnrollmentHold, match="ENROLLMENT_NATIVE_CUSTODY_UNPROVEN"):
        tx._info(99, False)


@pytest.mark.parametrize("kind", ["valid", "null_dacl", "foreign_owner", "inherited", "unprotected", "mask"])
def test_wh01b_native_private_acl_requires_exact_protected_owner(monkeypatch, kind):
    tx = offline_native(monkeypatch)
    c = tx.c
    def security(handle, object_type, requested, owner, group, dacl, sacl, descriptor):
        owner._obj.value, dacl._obj.value, descriptor._obj.value = 11, None if kind == "null_dacl" else 12, 13
        return 0
    def control(sd, flags, revision):
        flags._obj.value = 0 if kind == "unprotected" else 0x1000
        return 1
    def acl_info(acl, info, size, kind):
        info[0] = 1
        return 1
    header = bytes([0, 0x10 if kind == "inherited" else 0, 20, 0])
    ace = c.create_string_buffer(header + (0x120089 if kind == "mask" else 0x1F01FF).to_bytes(4, "little") + b"s" * 12)
    def get_ace(acl, index, pointer):
        pointer._obj.value = c.addressof(ace)
        return 1
    def sid(pointer):
        if pointer.value == 11 and kind == "foreign_owner":
            return "S-1-5-21-1-2-3-1002"
        return _OPERATOR_SID
    tx.a.GetSecurityInfo.handler = security
    tx.a.GetSecurityDescriptorControl.handler = control
    tx.a.GetAclInformation.handler = acl_info
    tx.a.GetAce.handler = get_ace
    tx.a.GetLengthSid.handler = lambda pointer: 12
    tx._sid_text = sid
    if kind == "valid":
        tx._acl(99, exact=True)
    else:
        with pytest.raises(ValueError):
            tx._acl(99, exact=True)


@pytest.mark.parametrize("kind", ["valid", "foreign_pid", "pid_reuse", "session", "shared", "none", "unavailable"])
def test_wh01b_native_marker_restart_manager_is_exact_process_proof(monkeypatch, kind):
    tx = offline_native(monkeypatch)
    tx.identity = (123, None, None, 1, _OPERATOR_SID, "2026-11-14T01:00:00Z")
    tx.process_ticks = ca._creation_ticks(tx.identity[5])
    tx._pin_path = lambda *args, **kwargs: None
    tx._process = lambda: None
    def start(session, flags, key):
        session._obj.value = 7
        return 0
    def listing(session, needed, count, rows, reasons):
        count._obj.value = 0 if kind == "none" else 2 if kind == "shared" else 1
        rows[0].process.pid = 456 if kind == "foreign_pid" else 123
        ticks = ca._creation_ticks(tx.identity[5]) + (1 if kind == "pid_reuse" else 0)
        rows[0].process.start.dwHighDateTime = ticks >> 32
        rows[0].process.start.dwLowDateTime = ticks & 0xFFFFFFFF
        rows[0].session = 2 if kind == "session" else 1
        return 5 if kind == "unavailable" else 0
    tx.rm.RmStartSession.handler = start
    tx.rm.RmRegisterResources.handler = lambda *args: 0
    tx.rm.RmGetList.handler = listing
    assert tx.prove_marker(Path("synthetic-marker")) is (kind == "valid")


@pytest.mark.parametrize("kind", ["valid", "short_write", "write_error", "flush_error"])
def test_wh01b_native_write_flush_is_bounded_and_private_at_create(monkeypatch, kind):
    tx = offline_native(monkeypatch)
    payload = b'{"private":"synthetic"}\n'
    tx.root = 9
    tx.absent = lambda: None
    tx._info = lambda *args: (1, 0, 2, 0, 0)
    tx._acl = lambda handle, exact: None
    def descriptor(text, version, result, size):
        assert text == f"O:{_OPERATOR_SID}D:P(A;;FA;;;{_OPERATOR_SID})"
        result._obj.value = 77
        return 1
    def relative(parent, name, *, directory, create, write=False):
        assert create is True and tx.descriptor.value == 77
        assert (parent, name, directory, write) in {
            (9, "Chrome-Acquisition", True, False), (10, "config.json", False, True)}
        tx.effect_possible = True
        return 10 if directory else 11
    def write(handle, data, size, count, overlapped):
        assert handle == 11 and data == payload and size == len(payload)
        count._obj.value = len(payload) - (1 if kind == "short_write" else 0)
        return kind != "write_error"
    tx._relative = relative
    tx.a.ConvertStringSecurityDescriptorToSecurityDescriptorW.handler = descriptor
    tx.k.WriteFile.handler = write
    tx.k.FlushFileBuffers.handler = lambda handle: kind != "flush_error"
    if kind == "valid":
        tx.create_and_flush(payload)
    else:
        with pytest.raises(ca._EnrollmentHold):
            tx.create_and_flush(payload)
    assert tx.effect_possible


@pytest.mark.parametrize("kind", ["valid", "bytes", "file_id", "size", "read_error", "other_config"])
def test_wh01b_native_readback_independently_matches_exact_file(monkeypatch, kind):
    tx = offline_native(monkeypatch)
    payload = b"synthetic private bytes"
    tx.root, tx.directory, tx.file_identity = 9, 10, (1, 0, 2)
    tx._acl = lambda handle, exact: None
    tx._relative = lambda parent, name, **kwargs: 11 if name == "config.json" else 12 if kind == "other_config" else None
    tx._info = lambda *args: (1, 0, 3 if kind == "file_id" else 2, 0, len(payload) + (1 if kind == "size" else 0))
    def read(handle, buffer, size, count, overlapped):
        assert handle == 11 and size == 8193
        tx.c.memmove(buffer, b"x" * len(payload) if kind == "bytes" else payload, len(payload))
        count._obj.value = len(payload)
        return kind != "read_error"
    tx.k.ReadFile.handler = read
    if kind == "valid":
        tx.readback(payload)
    else:
        with pytest.raises(ca._EnrollmentHold):
            tx.readback(payload)


@pytest.mark.parametrize("kind", ["valid", "sid", "session", "elevated", "noninteractive", "unauthenticated", "deny_only"])
def test_wh01b_native_token_requires_authenticated_non_elevated_exact_owner(monkeypatch, kind):
    tx = offline_native(monkeypatch)
    c, w = tx.c, tx.w
    class Group(c.Structure):
        _fields_ = [("sid", c.c_void_p), ("flags", w.DWORD)]
    class Groups(c.Structure):
        _fields_ = [("count", w.DWORD), ("groups", Group * 2)]
    user = Group(100, 0)
    groups = Groups(2, (Group * 2)(
        Group(999 if kind == "noninteractive" else 4, 0x10 if kind == "deny_only" else 4),
        Group(999 if kind == "unauthenticated" else 11, 4)))
    values = {1: user, 2: groups, 12: w.DWORD(2 if kind == "session" else 1),
              20: w.DWORD(1 if kind == "elevated" else 0)}
    def open_token(process, access, handle):
        assert access == 8
        handle._obj.value = 99
        return 1
    def information(token, code, buffer, size, needed):
        value = values[code]
        needed._obj.value = c.sizeof(value)
        if buffer is None:
            return 0
        c.memmove(buffer, c.byref(value), c.sizeof(value))
        return 1
    tx.a.OpenProcessToken.handler = open_token
    tx.a.GetTokenInformation.handler = information
    tx._sid_text = lambda pointer: ("S-1-5-21-1-2-3-1002" if kind == "sid" else _OPERATOR_SID) if pointer == 100 else f"S-1-5-{pointer}"
    if kind == "valid":
        tx._token(33)
    else:
        with pytest.raises(ca._EnrollmentHold):
            tx._token(33)


@pytest.mark.parametrize("kind", ["valid", "exited", "pid_reuse", "image", "impersonation", "unknown_thread"])
def test_wh01b_native_process_handle_pins_live_immutable_identity(monkeypatch, tmp_path, kind):
    import types
    tx = offline_native(monkeypatch)
    c = tx.c
    last_error = [0]
    monkeypatch.setattr(c, "set_last_error", lambda value: last_error.__setitem__(0, value), raising=False)
    monkeypatch.setattr(c, "get_last_error", lambda: last_error[0], raising=False)
    tx.process = 33
    tx.env = types.SimpleNamespace(executable=tmp_path / "chrome.exe")
    tx.identity = (123, str(tx.env.executable), None, 1, _OPERATOR_SID, "2026-11-14T01:00:00Z")
    tx.process_ticks = ca._creation_ticks(tx.identity[5])
    checked = []
    tx._token = checked.append
    def thread_token(thread, access, as_self, handle):
        handle._obj.value = 44
        last_error[0] = 5 if kind == "unknown_thread" else 1008
        return kind == "impersonation"
    def times(process, created, exited, kernel, user):
        ticks = ca._creation_ticks(tx.identity[5]) + (1 if kind == "pid_reuse" else 0)
        created._obj.dwHighDateTime, created._obj.dwLowDateTime = ticks >> 32, ticks & 0xFFFFFFFF
        return 1
    def image(process, flags, value, size):
        value.value = str(tmp_path / "other.exe" if kind == "image" else tx.env.executable)
        return 1
    tx.a.OpenThreadToken.handler = thread_token
    tx.k.WaitForSingleObject.handler = lambda *args: 0 if kind == "exited" else 258
    tx.k.GetProcessTimes.handler = times
    tx.k.QueryFullProcessImageNameW.handler = image
    if kind == "valid":
        tx._process()
        assert checked == [33, 1]
    else:
        with pytest.raises(ca._EnrollmentHold):
            tx._process()
        assert not checked


# N3-A is SOURCE ONLY. These fixtures do not establish native logon readiness,
# issue launch grants, start browsers, install tasks or read any Chat content.
def logon_fixture(monkeypatch, tmp_path, *, enrolled=True, allow_launch=False, running=False):
    env, repo, home, snapshot, events = enrollment_fixture(monkeypatch, tmp_path)
    env = ca.replace(env, allow_launch=allow_launch)
    if enrolled:
        env.config_path.parent.mkdir()
        env.config_path.write_text(json.dumps({
            "version": 1, "executable": str(env.executable),
            "user_data_dir": str(env.user_data_dir), "profile_directory": env.profile_directory,
            "cdp_endpoint": env.endpoint, "exclusive_user_data": True,
            "allow_launch": allow_launch,
        }), encoding="utf-8")
    if not running:
        snapshot.update(listeners=[], processes=[])
    return env, repo, home, snapshot, events


def assert_logon_hold(result, code):
    assert result == {
        "status": "UNKNOWN_EFFECT" if code == "UNKNOWN_EFFECT" else "HOLD",
        "code": code, "scope": "SOURCE_ONLY_ADVISORY",
        "native_adapter": "BLOCKED_NOT_DEPLOYED", "launch_authorized": False,
        "launched": False, "browser_accessed": False,
        "chat_identity_proven": False, "send_authorized": False,
    }


@pytest.mark.parametrize("enrolled,allow_launch,running,code", [
    (False, False, False, "FIRST_SUPERVISED_ENROLLMENT_REQUIRED"),
    (False, False, True, "UNTRUSTED_OWNER"),
    (True, False, False, "LAUNCH_GRANT_REQUIRED"),
    (True, True, False, "BLOCKED_NOT_DEPLOYED"),
    (True, False, True, "REUSE_ONLY"),
    (True, True, True, "REUSE_ONLY"),
])
def test_n3a_logon_decisions_are_advisory_without_mutation(
        monkeypatch, tmp_path, capsys, enrolled, allow_launch, running, code):
    env, repo, home, _, events = logon_fixture(monkeypatch, tmp_path,
        enrolled=enrolled, allow_launch=allow_launch, running=running)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    for _ in range(2):  # Repeated logon never repairs, enrolls, starts or attaches.
        assert_logon_hold(ca.assess_logon_9444(repo), code)
    assert events.count("inventory") == 4
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    assert not ca._NativeEnrollment.instances
    if enrolled:
        assert json.loads(env.config_path.read_text())["allow_launch"] is allow_launch
    else:
        assert not env.config_path.parent.exists()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("gate", ["non_windows", "noninteractive_runner", "session_zero",
    "missing_token", "wrong_token", "elevated", "github_actions", "github_event", "github_run"])
def test_n3a_logon_requires_current_interactive_owner(monkeypatch, tmp_path, gate):
    from ai_commerce.remote import local_wake_entry
    _, repo, _, _, events = logon_fixture(monkeypatch, tmp_path)
    if gate == "non_windows":
        ca.os.name = "posix"
    elif gate == "noninteractive_runner":
        ca.sys.stdin.isatty = lambda: False
    elif gate == "session_zero":
        monkeypatch.setattr(ca, "_interactive_session", lambda: 0)
    elif gate == "wrong_token":
        monkeypatch.setattr(local_wake_entry, "_windows_process_sid", lambda: "S-1-5-18")
    elif gate in {"missing_token", "elevated"}:
        def unavailable():
            raise ca.AcquisitionBlocked("WINDOWS_TOKEN_UNAVAILABLE" if gate == "missing_token"
                                        else "ELEVATED_CHROME_DISALLOWED")
        monkeypatch.setattr(ca, "_interactive_session", unavailable)
    else:
        ca.os.environ[{"github_actions": "GITHUB_ACTIONS", "github_event": "GITHUB_EVENT_PATH",
                       "github_run": "GITHUB_RUN_ID"}[gate]] = "private-runner-value"
    assert_logon_hold(ca.assess_logon_9444(repo), "WAIT_INTERACTIVE_OWNER")
    assert events == []


@pytest.mark.parametrize("case", ["partial", "duplicate_config", "legacy_only", "directory",
    "duplicate_key", "invalid_json", "wrong_port", "unsafe_acl", "foreign_config_sid",
    "config_reparse", "profile_reparse", "executable_reparse"])
def test_n3a_logon_refuses_unsafe_or_incomplete_config(monkeypatch, tmp_path, case):
    from ai_commerce.remote import local_completion_handoff
    env, repo, home, _, events = logon_fixture(monkeypatch, tmp_path)
    config = env.config_path
    if case in {"partial", "legacy_only", "directory"}:
        config.unlink()
        if case == "legacy_only":
            config.parent.rmdir()
        elif case == "directory":
            config.mkdir()
    if case in {"legacy_only", "duplicate_config"}:
        (home / "AI-Commerce" / "chrome.json").write_text("{}")
    elif case == "duplicate_key":
        config.write_text(config.read_text()[:-1] + ', "allow_launch": true}')
    elif case == "invalid_json":
        config.write_text("private invalid bytes")
    elif case == "wrong_port":
        config.write_text(config.read_text().replace(":9444", ":9333"))
    elif case == "unsafe_acl":
        def unsafe(path):
            raise local_completion_handoff.HandoffBlocked("private path/SID/token")
        monkeypatch.setattr(local_completion_handoff, "_private_identity", unsafe)
    elif case == "foreign_config_sid":
        monkeypatch.setattr(local_completion_handoff, "_private_identity",
            lambda path: "S-1-5-21-1-2-3-1002" if path == config else _OPERATOR_SID)
    elif case.endswith("_reparse"):
        unsafe_path = {"config_reparse": config,
                       "profile_reparse": env.user_data_dir / env.profile_directory,
                       "executable_reparse": env.executable}[case]
        original = ca._no_links
        monkeypatch.setattr(ca, "_no_links", lambda path: path != unsafe_path and original(path))
    assert_logon_hold(ca.assess_logon_9444(repo), "CONFIG_UNTRUSTED")
    assert events == []


@pytest.mark.parametrize("case", ["wrong_port", "foreign_pid", "duplicate_pid", "foreign_sid",
    "wrong_session", "foreign_executable", "foreign_argv", "foreign_profile", "foreign_address",
    "duplicate_listener", "unsigned", "missing_creation", "extra_switch"])
def test_n3a_logon_never_reuses_foreign_or_ambiguous_listener(monkeypatch, tmp_path, case):
    env, repo, _, snapshot, _ = logon_fixture(monkeypatch, tmp_path, running=True)
    proc, row = snapshot["processes"][0], snapshot["listeners"][0]
    command = env.command()
    if case == "wrong_port":
        row["LocalPort"] = 9333
    elif case == "foreign_pid":
        row["OwningProcess"] = 999
    elif case == "duplicate_pid":
        snapshot["processes"].append(dict(proc))
    elif case == "foreign_sid":
        proc["OwnerSid"] = "S-1-5-21-1-2-3-1002"
    elif case == "wrong_session":
        proc["SessionId"] = 2
    elif case == "foreign_executable":
        proc["ExecutablePath"] = str(tmp_path / "foreign.exe")
    elif case == "foreign_argv":
        command[0] = str(tmp_path / "foreign.exe")
    elif case == "foreign_profile":
        command = [a.replace("--profile-directory=Default", "--profile-directory=Other") for a in command]
    elif case == "foreign_address":
        row["LocalAddress"] = "0.0.0.0"
    elif case == "duplicate_listener":
        snapshot["listeners"].append(dict(row))
    elif case == "missing_creation":
        proc["CreationDate"] = None
    elif case == "extra_switch":
        command.append("--unregistered-switch")
    else:
        def unsigned(env):
            raise ca.AcquisitionBlocked("CHROME_EXECUTABLE_UNTRUSTED")
        monkeypatch.setattr(ca, "_verify_google_chrome", unsigned)
    proc["CommandLine"] = json.dumps(command)
    assert_logon_hold(ca.assess_logon_9444(repo), "UNTRUSTED_OWNER")


@pytest.mark.parametrize("marker", ca._MARKERS)
@pytest.mark.parametrize("symlink", [False, True])
def test_n3a_logon_holds_profile_markers_without_removing_them(monkeypatch, tmp_path, marker, symlink):
    env, repo, _, _, _ = logon_fixture(monkeypatch, tmp_path)
    path = env.user_data_dir / marker
    if symlink:
        # Synthetic dangling link lstat evidence; no Windows link privilege needed.
        original = Path.lstat
        def lstat(current, *args, **kwargs):
            if current == path:
                return env.user_data_dir.stat()
            return original(current, *args, **kwargs)
        monkeypatch.setattr(Path, "lstat", lstat)
    else:
        path.write_bytes(b"private marker; do not interpret or delete")
    assert_logon_hold(ca.assess_logon_9444(repo), "PROFILE_LOCK_HOLD")
    if not symlink:
        assert path.read_bytes() == b"private marker; do not interpret or delete"


def test_n3a_9333_only_is_not_a_9444_enrollment(monkeypatch, tmp_path):
    env, repo, _, snapshot, _ = logon_fixture(monkeypatch, tmp_path, enrolled=False, running=True)
    snapshot["listeners"] = []  # The 9444 query has no listener; leave 9333 alone.
    snapshot["processes"][0]["CommandLine"] = json.dumps([
        a.replace("--remote-debugging-port=9444", "--remote-debugging-port=9333") for a in env.command()])
    assert_logon_hold(ca.assess_logon_9444(repo), "FIRST_SUPERVISED_ENROLLMENT_REQUIRED")
    assert not env.config_path.parent.exists()


def test_n3a_exact_9444_off_without_config_never_invents_private_inputs(monkeypatch, tmp_path):
    _, repo, home, _, _ = logon_fixture(monkeypatch, tmp_path, enrolled=False)
    def forbidden(*args, **kwargs):
        pytest.fail("OFF without enrollment must not invent executable/profile candidates")
    for name in ("_environment_from_value", "_existing_9444_candidate", "_enrollment_location"):
        monkeypatch.setattr(ca, name, forbidden)
    before = set(home.rglob("*"))
    assert_logon_hold(ca.assess_logon_9444(repo), "FIRST_SUPERVISED_ENROLLMENT_REQUIRED")
    assert set(home.rglob("*")) == before


@pytest.mark.parametrize("case", ["timeout", "exception", "partial", "unparseable",
                                   "concurrent_9444", "active_profile"])
def test_n3a_absence_requires_independent_complete_evidence(monkeypatch, tmp_path, case):
    env, repo, _, snapshot, events = logon_fixture(monkeypatch, tmp_path)
    expected = "ABSENCE_UNPROVEN"
    if case in {"timeout", "exception"}:
        def unavailable(port):
            events.append("failed inventory")
            raise TimeoutError("private arguments/WS/token") if case == "timeout" else RuntimeError("private")
        monkeypatch.setattr(ca, "_query_windows", unavailable)
    elif case == "partial":
        snapshot.pop("processes")
    else:
        command = env.command()
        if case == "active_profile":
            command = [a.replace("--remote-debugging-port=9444", "--remote-debugging-port=9333") for a in command]
            expected = "PROFILE_LOCK_HOLD"
        else:
            expected = "UNTRUSTED_OWNER"
        snapshot["processes"] = [{**owner(env), "CommandLine": None if case == "unparseable"
                                  else json.dumps(command)}]
    assert_logon_hold(ca.assess_logon_9444(repo), expected)
    assert len(events) <= 2  # One inventory and, where applicable, one signature; no retry.


@pytest.mark.parametrize("change", ["session", "sid", "config", "concurrent_start", "pid_reuse", "timeout"])
def test_n3a_logon_drift_is_unknown_with_no_second_start(monkeypatch, tmp_path, change):
    import copy
    from ai_commerce.remote import local_wake_entry
    env, repo, _, snapshot, _ = logon_fixture(monkeypatch, tmp_path, running=change == "pid_reuse")
    original = ca._logon_observation
    observations = []
    def observe(checkout):
        observations.append("read")
        if len(observations) == 2:
            if change == "session":
                monkeypatch.setattr(ca, "_interactive_session", lambda: 2)
            elif change == "sid":
                monkeypatch.setattr(local_wake_entry, "_windows_process_sid", lambda: "S-1-5-21-1-2-3-1002")
            elif change == "config":
                env.config_path.write_text(env.config_path.read_text().replace('"allow_launch": false', '"allow_launch": true'))
            elif change == "timeout":
                raise TimeoutError("private WS/token/arguments")
            elif change == "pid_reuse":
                snapshot["processes"][0]["CreationDate"] = "2026-11-14T02:00:00Z"
            else:
                snapshot.update(listeners=[{"LocalAddress": "127.0.0.1", "LocalPort": 9444, "OwningProcess": 123}],
                    processes=[{**owner(env), "OwnerSid": _OPERATOR_SID,
                        "CreationDate": "2026-11-14T01:00:00Z", "CommandLine": json.dumps(env.command())}])
        return original(checkout)
    monkeypatch.setattr(ca, "_logon_observation", observe)
    before = copy.deepcopy(snapshot)
    assert_logon_hold(ca.assess_logon_9444(repo), "UNKNOWN_EFFECT")
    assert observations == ["read", "read"]
    if change in {"session", "sid", "config", "timeout"}:
        assert snapshot == before


@pytest.mark.parametrize("grant", [None, False, True, {},
    {"operation": "deliver_wake", "human_consent": True},
    {"operation": "start_chrome", "signature": "synthetic", "valid_until": 9999999999999},
    {"operation": "start_chrome", "revoked": True},
    {"operation": "start_chrome", "valid_until": 0},
    {"status": "START_ONCE_READY", "allow_launch": True},
    {"status": "UNKNOWN_EFFECT"},
])
def test_n3a_private_effect_seam_never_accepts_unverified_grants(monkeypatch, tmp_path, grant):
    def forbidden(*args, **kwargs):
        pytest.fail("Undeployed effect seam must return before all reads and effects")
    for name in ("assess_logon_9444", "_enrollment_operator", "_query_windows", "run",
                 "_probe_cdp", "verify_playwright_attach"):
        monkeypatch.setattr(ca, name, forbidden)
    monkeypatch.setattr(ca.subprocess, "Popen", forbidden)
    monkeypatch.setattr(ca.subprocess, "run", forbidden)
    class UntrustedGrant:
        def __getattribute__(self, name):
            pytest.fail("Grant callbacks/attributes are not a native authority")
    for supplied in (grant, UntrustedGrant()):
        for request_id in ("same-logon", "same-logon", "new-logon"):
            assert_logon_hold(ca._start_or_reuse_9444(tmp_path, request_id=request_id,
                                                   grant=supplied), "BLOCKED_NOT_DEPLOYED")


def test_n3a_public_diagnostics_sanitize_private_values(monkeypatch, tmp_path, capsys):
    env, repo, home, _, _ = logon_fixture(monkeypatch, tmp_path)
    def unknown(port):
        raise RuntimeError(f"{home} {_OPERATOR_SID} PID=123 --private ws://private token=secret")
    monkeypatch.setattr(ca, "_query_windows", unknown)
    results = [ca.assess_logon_9444(repo),
               ca._start_or_reuse_9444(repo, request_id="private request", grant={"token": "secret"})]
    for result in results:
        public = json.dumps(result)
        assert all(value not in public for value in (str(home), str(env.executable), _OPERATOR_SID,
                   "PID=", "--private", "ws://", "secret", "private request"))
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("command", ["assess-logon-9444", "start-or-reuse-9444"])
def test_n3a_policy_and_private_adapter_are_not_cli_operations(monkeypatch, tmp_path, command, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Public CLI must not dispatch logon policy/effects")
    monkeypatch.setattr(ca, "assess_logon_9444", forbidden)
    monkeypatch.setattr(ca, "_start_or_reuse_9444", forbidden)
    with pytest.raises(SystemExit) as exc:
        ca.main([command, "--checkout", str(tmp_path), "--opt-in"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("enrolled,allow_launch,running,code", [
    (False, False, False, "FIRST_SUPERVISED_ENROLLMENT_REQUIRED"),
    (True, False, False, "LAUNCH_GRANT_REQUIRED"),
    (True, True, False, "BLOCKED_NOT_DEPLOYED"),
    (True, False, True, "REUSE_ONLY"),
])
def test_n3b_private_assessment_retains_exact_n3a_inputs_without_authority(
        monkeypatch, tmp_path, enrolled, allow_launch, running, code):
    env, repo, home, snapshot, events = logon_fixture(monkeypatch, tmp_path,
        enrolled=enrolled, allow_launch=allow_launch, running=running)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = ca._assess_logon_9444_private(repo)
    assert type(result) is ca._Logon9444Assessment
    assert result.code == code
    (local, session, sid), current, identity = result.observation
    assert (local, session, sid) == (home, 1, _OPERATOR_SID)
    assert current == (env if enrolled else None)
    assert identity == ca._logon_inventory(snapshot)[1]
    assert events.count("inventory") == 2
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    assert not ca._NativeEnrollment.instances
    assert_logon_hold(ca._logon_result(result.code), code)
    for secret in (str(env.config_path), str(env.executable), _OPERATOR_SID, "CreationDate"):
        assert secret not in repr(result)
        assert secret not in json.dumps(ca._logon_result(result.code))


def test_n3b_changed_private_assessment_discards_all_target_inputs(monkeypatch, tmp_path):
    env, repo, _, _, events = logon_fixture(monkeypatch, tmp_path, allow_launch=True)
    original = ca._logon_observation
    calls = []
    def observe(checkout):
        calls.append("read")
        if len(calls) == 2:
            env.config_path.write_text(env.config_path.read_text().replace('"allow_launch": true',
                                                                          '"allow_launch": false'))
        return original(checkout)
    monkeypatch.setattr(ca, "_logon_observation", observe)
    result = ca._assess_logon_9444_private(repo)
    assert result.code == "UNKNOWN_EFFECT"
    assert result.observation is None
    assert calls == ["read", "read"]
    assert events.count("inventory") == 2
    assert not ca._NativeEnrollment.instances


@pytest.mark.parametrize("command", ["preflight-9444", "preflight_9444", "native-9444"])
def test_n3b_contract_is_not_a_public_cli_mode(tmp_path, command, capsys):
    with pytest.raises(SystemExit) as exc:
        ca.main([command, "--checkout", str(tmp_path), "--opt-in"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


# WH01B L1A: offline lifecycle evidence only. These tests neither execute a
# Windows inventory nor establish native custody, launch or deployment proof.
_PRIOR_9444_CONFIGS = (
    "Chrome-Acquisition/config.json", "D5-Isolated-9444-Trial/config.json",
    "WakeHub/config.json", "WakeHub-9444/config.json", "chrome.json", "chrome-acquisition.json",
)


@pytest.mark.parametrize("case,code", [
    ("wrong_port", "WINDOWS_PROBE_INVALID"),
    ("duplicate_listener", "BROWSER_OWNERSHIP_UNPROVEN"),
    ("unknown_owner", "PROCESS_IDENTITY_UNKNOWN"),
    ("foreign_pid", "FOREIGN_PORT_OWNER"),
])
def test_l1a_inventory_diagnostics_stay_out_of_legacy_cli(monkeypatch, tmp_path, case, code):
    env, _, _, snapshot, _ = logon_fixture(monkeypatch, tmp_path, running=True)
    if case == "wrong_port":
        snapshot["listeners"][0]["LocalPort"] = 9333
    elif case == "duplicate_listener":
        snapshot["listeners"].append(dict(snapshot["listeners"][0]))
    elif case == "unknown_owner":
        snapshot["processes"][0]["OwnerSid"] = None
    else:
        snapshot["listeners"][0]["OwningProcess"] = 999
    with pytest.raises(ca._Lifecycle9444Hold, match=f"^{code}$") as exc:
        ca._lifecycle_inventory(env, snapshot, session=1, sid=_OPERATOR_SID)
    assert not isinstance(exc.value, ca.AcquisitionBlocked)


@pytest.mark.parametrize("relative", _PRIOR_9444_CONFIGS)
def test_l1a_each_finite_config_is_recognized_without_adoption(monkeypatch, tmp_path, relative):
    env, repo, home, _, _ = logon_fixture(monkeypatch, tmp_path, allow_launch=True)
    target = home / "AI-Commerce" / relative
    if target != env.config_path:
        target.parent.mkdir(exist_ok=True)
        env.config_path.rename(target)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    registry = ca._config_9444_registry(home, repo)
    assert registry.selected == target and registry.present == (target,)
    result = ca._assess_lifecycle_9444(repo)
    assert result.state == ("OFF_CLEAN_ENROLLED" if target == registry.canonical else "LEGACY_CANDIDATE")
    assert result.status == "HOLD" and result.native_adapter == "BLOCKED_NOT_DEPLOYED"
    if target != registry.canonical:
        assert result.dependency == "ATTENDED_LEGACY_RECONCILIATION_REQUIRED"
        assert_logon_hold(ca.assess_logon_9444(repo), "CONFIG_UNTRUSTED")
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    assert json.loads(target.read_text())["allow_launch"] is True
    assert not ca._NativeEnrollment.instances


@pytest.mark.parametrize("relative", _PRIOR_9444_CONFIGS[1:])
def test_l1a_canonical_precedence_never_accepts_duplicate_config(monkeypatch, tmp_path, relative):
    env, repo, home, _, events = logon_fixture(monkeypatch, tmp_path)
    duplicate = home / "AI-Commerce" / relative
    duplicate.parent.mkdir(exist_ok=True)
    duplicate.write_bytes(env.config_path.read_bytes())  # Identical bytes are still ambiguous.
    registry = ca._config_9444_registry(home, repo)
    assert registry.selected == env.config_path and len(registry.present) == 2
    result = ca._assess_lifecycle_9444(repo)
    assert (result.state, result.code) == ("CONFIG_NOT_ENROLLED", "CONFIG_AMBIGUOUS")
    assert result.observation is None
    assert_logon_hold(ca.assess_logon_9444(repo), "CONFIG_UNTRUSTED")
    assert_enrollment_hold(ca.assess_existing_9444(repo), "ENROLLMENT_CONFIG_EXISTS")
    assert events == []


def test_l1a_registry_is_finite_and_legacy_duplicates_hold(monkeypatch, tmp_path):
    _, repo, home, _, events = logon_fixture(monkeypatch, tmp_path, enrolled=False)
    root = home / "AI-Commerce"
    unrelated = root / "arbitrary" / "deep" / "config.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("unregistered content")
    assert ca._config_9444_registry(home, repo).present == ()
    assert ca._assess_lifecycle_9444(repo).state == "CONFIG_NOT_ENROLLED"
    for relative in ("WakeHub/config.json", "D5-Isolated-9444-Trial/config.json"):
        path = root / relative
        path.parent.mkdir()
        path.write_text("untrusted prior config")
    assert ca._assess_lifecycle_9444(repo).code == "CONFIG_AMBIGUOUS"
    assert events == []


@pytest.mark.parametrize("running,listener,cdp_ok,state,code", [
    (False, False, False, "OFF_CLEAN_ENROLLED", "PROCESS_AND_LOCK_ABSENT"),
    (True, False, False, "RUNNING_CDP_FAULT", "CDP_LISTENER_MISSING"),
    (True, True, False, "RUNNING_CDP_FAULT", "CDP_PROTOCOL_UNAVAILABLE"),
    (True, True, True, "RUNNING_HEALTHY", "CDP_VERIFIED"),
])
def test_l1a_off_fault_and_healthy_require_independent_evidence(
        monkeypatch, tmp_path, running, listener, cdp_ok, state, code):
    env, repo, home, snapshot, events = logon_fixture(monkeypatch, tmp_path, running=running)
    if not listener:
        snapshot["listeners"] = []
    def cdp(current):
        assert current == env and listener and running
        events.append("cdp")
        if not cdp_ok:
            raise ca.AcquisitionBlocked("CDP_PROTOCOL_UNAVAILABLE")
    monkeypatch.setattr(ca, "_probe_cdp", cdp)
    before_paths = set(home.rglob("*"))
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    for _ in range(2):
        result = ca._assess_lifecycle_9444(repo)
        assert (result.state, result.code, result.status) == (state, code, "HOLD")
        assert result.native_adapter == "BLOCKED_NOT_DEPLOYED"
    assert events.count("inventory") == 4
    assert events.count("cdp") == (4 if listener else 0)
    assert set(home.rglob("*")) == before_paths
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    assert json.loads(env.config_path.read_text())["allow_launch"] is False
    assert not ca._NativeEnrollment.instances


@pytest.mark.parametrize("profile_switch", [False, True])
@pytest.mark.parametrize("browser", [False, True])
def test_l1a_children_do_not_hide_active_profile_or_duplicate_browser(
        monkeypatch, tmp_path, profile_switch, browser):
    env, repo, _, snapshot, events = logon_fixture(monkeypatch, tmp_path, running=browser)
    command = [str(env.executable), "--type=renderer"]
    if profile_switch:
        command.append(f"--user-data-dir={env.user_data_dir}")
    snapshot["processes"].append({**owner(env, pid=456), "OwnerSid": _OPERATOR_SID,
        "CreationDate": "2026-11-14T01:00:00Z", "CommandLine": json.dumps(command)})
    monkeypatch.setattr(ca, "_probe_cdp", lambda current: events.append("cdp"))
    result = ca._assess_lifecycle_9444(repo)
    assert result.state == ("RUNNING_HEALTHY" if browser else "PROFILE_LOCK_OR_IDENTITY_UNPROVEN")
    assert events.count("cdp") == (2 if browser else 0)


@pytest.mark.parametrize("marker", ca._MARKERS)
@pytest.mark.parametrize("location", ["root", "profile"])
def test_l1a_lock_at_either_profile_location_requires_native_holder(
        monkeypatch, tmp_path, marker, location):
    env, repo, _, _, events = logon_fixture(monkeypatch, tmp_path)
    directory = env.user_data_dir if location == "root" else env.user_data_dir / env.profile_directory
    path = directory / marker
    path.write_bytes(b"private lock; never remove or parse")
    result = ca._assess_lifecycle_9444(repo)
    assert (result.state, result.code) == ("PROFILE_LOCK_OR_IDENTITY_UNPROVEN", "PROFILE_LOCK_HOLD")
    assert result.dependency == "WINDOWS_PROFILE_LOCK_HOLDER_REQUIRED"
    assert path.read_bytes() == b"private lock; never remove or parse"
    assert "inventory" not in events


@pytest.mark.parametrize("case,state", [
    ("foreign_pid", "FOREIGN_PORT_OWNER"), ("foreign_address", "FOREIGN_PORT_OWNER"),
    ("foreign_profile", "FOREIGN_PORT_OWNER"), ("wrong_port", "INVENTORY_UNAVAILABLE"),
    ("unknown_sid", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"),
    ("unknown_session", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"),
    ("wrong_session", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"),
    ("unknown_creation", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"),
    ("duplicate_pid", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"),
    ("duplicate_listener", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"),
    ("same_profile_9333", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"),
    ("unparseable", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"),
])
def test_l1a_foreign_unknown_or_9333_evidence_never_proves_health_or_off(
        monkeypatch, tmp_path, case, state):
    env, repo, home, snapshot, events = logon_fixture(monkeypatch, tmp_path, running=True)
    proc, row = snapshot["processes"][0], snapshot["listeners"][0]
    if case == "foreign_pid":
        row["OwningProcess"] = 999
    elif case == "foreign_address":
        row["LocalAddress"] = "0.0.0.0"
    elif case == "foreign_profile":
        other = home / "AI-Commerce" / "Other-Profile"
        other.mkdir()
        proc["CommandLine"] = json.dumps([a.replace(str(env.user_data_dir), str(other)) for a in env.command()])
    elif case == "wrong_port":
        row["LocalPort"] = 9333
    elif case in {"unknown_sid", "unknown_session", "unknown_creation"}:
        proc[{"unknown_sid": "OwnerSid", "unknown_session": "SessionId",
              "unknown_creation": "CreationDate"}[case]] = None
    elif case == "wrong_session":
        proc["SessionId"] = 2
    elif case == "duplicate_pid":
        snapshot["processes"].append(dict(proc))
    elif case == "duplicate_listener":
        snapshot["listeners"].append(dict(row))
    elif case == "same_profile_9333":
        snapshot["listeners"] = []
        proc["CommandLine"] = json.dumps([
            a.replace("--remote-debugging-port=9444", "--remote-debugging-port=9333") for a in env.command()])
    else:
        proc["CommandLine"] = "unparseable private arguments"
    result = ca._assess_lifecycle_9444(repo)
    assert result.state == state and result.status == "HOLD"
    assert result.observation is None and result.dependency
    assert events.count("inventory") == 1  # No CDP access or retry on unknown ownership.


@pytest.mark.parametrize("case", ["timeout", "partial", "null_rows", "non_windows", "ci"])
def test_l1a_unavailable_inventory_never_proves_off(monkeypatch, tmp_path, case):
    _, repo, _, snapshot, events = logon_fixture(monkeypatch, tmp_path)
    if case == "timeout":
        def unavailable(port):
            raise TimeoutError("private arguments/token")
        monkeypatch.setattr(ca, "_query_windows", unavailable)
    elif case == "partial":
        snapshot.pop("processes")
    elif case == "null_rows":
        snapshot.update(processes=None, listeners=None)
    elif case == "non_windows":
        ca.os.name = "posix"
    else:
        ca.os.environ["GITHUB_ACTIONS"] = "true"
    result = ca._assess_lifecycle_9444(repo)
    assert result.state == "INVENTORY_UNAVAILABLE" and result.dependency
    assert result.status == "HOLD" and result.observation is None
    assert events.count("inventory") <= 1


@pytest.mark.parametrize("relative", _PRIOR_9444_CONFIGS)
@pytest.mark.parametrize("link", ["symlink", "reparse"])
def test_l1a_every_registry_path_refuses_links(monkeypatch, tmp_path, relative, link):
    import stat
    import types
    _, repo, home, _, events = logon_fixture(monkeypatch, tmp_path, enrolled=False)
    target = home / "AI-Commerce" / relative
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == target:
            return types.SimpleNamespace(st_file_attributes=0x400,
                st_mode=stat.S_IFLNK if link == "symlink" else stat.S_IFREG)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    assert ca._assess_lifecycle_9444(repo).state == "CONFIG_NOT_ENROLLED"
    assert_logon_hold(ca.assess_logon_9444(repo), "CONFIG_UNTRUSTED")
    assert_enrollment_hold(ca.assess_existing_9444(repo), "ENROLLMENT_PATH_UNSAFE")
    assert events == []


@pytest.mark.parametrize("case", ["missing", "bad_json", "wrong_port", "unsigned", "owner_acl", "lock"])
def test_l1a_invalid_config_or_unproven_custody_is_typed_hold(monkeypatch, tmp_path, case):
    from ai_commerce.remote import local_completion_handoff
    env, repo, home, _, events = logon_fixture(monkeypatch, tmp_path)
    if case == "missing":
        env.config_path.unlink()
    elif case == "bad_json":
        env.config_path.write_text("invalid private config")
    elif case == "wrong_port":
        env.config_path.write_text(env.config_path.read_text().replace(":9444", ":9333"))
    elif case == "unsigned":
        def unsigned(current):
            raise ca.AcquisitionBlocked("CHROME_EXECUTABLE_UNTRUSTED")
        monkeypatch.setattr(ca, "_verify_google_chrome", unsigned)
    elif case == "owner_acl":
        def unproven(path):
            raise local_completion_handoff.HandoffBlocked("private identity/ACL")
        monkeypatch.setattr(local_completion_handoff, "_private_identity", unproven)
    else:
        (env.user_data_dir / "SingletonLock").write_bytes(b"do not remove")
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = ca._assess_lifecycle_9444(repo)
    assert result.state in {"CONFIG_NOT_ENROLLED", "PROFILE_LOCK_OR_IDENTITY_UNPROVEN"}
    assert result.status == "HOLD" and result.dependency and result.observation is None
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    assert "inventory" not in events
    assert str(home) not in repr(result) and _OPERATOR_SID not in repr(result)


@pytest.mark.parametrize("change", ["process", "lock", "config", "inventory_failure", "cdp"])
def test_l1a_changed_lifecycle_evidence_discards_private_observation(monkeypatch, tmp_path, change):
    env, repo, _, snapshot, _ = logon_fixture(monkeypatch, tmp_path, running=change == "cdp")
    monkeypatch.setattr(ca, "_probe_cdp", lambda current: None)
    original = ca._lifecycle_9444_observation
    calls = []
    def observe(checkout):
        calls.append("read")
        if len(calls) == 2:
            if change == "process":
                snapshot["processes"] = [{**owner(env), "OwnerSid": _OPERATOR_SID,
                    "CreationDate": "2026-11-14T01:00:00Z", "CommandLine": json.dumps(env.command())}]
            elif change == "lock":
                (env.user_data_dir / "SingletonLock").write_bytes(b"new lock")
            elif change == "config":
                env.config_path.write_text(env.config_path.read_text().replace('"allow_launch": false',
                                                                              '"allow_launch": true'))
            else:
                def unavailable(*args):
                    raise TimeoutError("private evidence")
                monkeypatch.setattr(ca, "_probe_cdp" if change == "cdp" else "_query_windows", unavailable)
        return original(checkout)
    monkeypatch.setattr(ca, "_lifecycle_9444_observation", observe)
    result = ca._assess_lifecycle_9444(repo)
    assert (result.state, result.code, result.status) == ("UNKNOWN_EFFECT", "OBSERVATION_CHANGED", "HOLD")
    assert result.observation is None and calls == ["read", "read"]
    assert not ca._NativeEnrollment.instances


@pytest.fixture
def clean_9444(monkeypatch, tmp_path):
    from ai_commerce.remote import local_completion_handoff, local_wake_entry

    local = tmp_path / "Local App Data"
    directory = local / "AI-Commerce" / "WakeHub-9444-Clean"
    (directory / "Default").mkdir(parents=True)
    files = tmp_path / "Program Files"
    exe = files / "Google" / "Chrome" / "Application" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected native effect")

    monkeypatch.setattr(ca, "os", SimpleNamespace(name="nt", environ={"GITHUB_TOKEN": "private"},
                                                scandir=os.scandir))
    monkeypatch.setattr(ca.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(ca, "_interactive_session", lambda: 7)
    monkeypatch.setattr(ca, "_native_local_app_data", lambda: local)
    monkeypatch.setattr(ca, "_native_known_folder", lambda folder: files)
    monkeypatch.setattr(local_wake_entry, "_windows_process_sid", lambda: _OPERATOR_SID)
    monkeypatch.setattr(local_completion_handoff, "_private_identity", forbidden)
    monkeypatch.setattr(ca, "_clean_supervisor_slot", nullcontext)
    monkeypatch.setattr(ca, "_clean_pending", lambda name: False)
    monkeypatch.setattr(ca, "_clean_inventory", lambda *args: ("ABSENT", None))
    monkeypatch.setattr(ca, "_verify_google_chrome", lambda target: None)
    monkeypatch.setattr(ca.subprocess, "run", forbidden)
    monkeypatch.setattr(ca.subprocess, "Popen", forbidden)
    monkeypatch.setattr(local_wake_entry, "send_one", forbidden)
    return ca._clean_operator()


@pytest.mark.parametrize("operation", [ca.ensure_clean_9444, ca.install_clean_9444_autostart])
def test_clean_9444_native_gates_precede_process_and_task_access(clean_9444, monkeypatch, operation):
    assert operation()["code"] == "OPT_IN_REQUIRED"
    assert operation(opt_in=1)["code"] == "OPT_IN_REQUIRED"
    monkeypatch.setattr(ca, "_interactive_session", lambda: 0)
    assert operation(opt_in=True)["code"] == "ENROLLMENT_IDENTITY_UNPROVEN"

    def elevated():
        raise ca.AcquisitionBlocked("ELEVATED_CHROME_DISALLOWED")

    monkeypatch.setattr(ca, "_interactive_session", elevated)
    assert operation(opt_in=True)["code"] == "ELEVATED_CHROME_DISALLOWED"
    ca.os.environ["CI"] = "true"
    assert operation(opt_in=True)["code"] == "LOCAL_OPERATOR_ONLY"


@pytest.mark.parametrize("case", ["missing", "file", "inaccessible", "junction"])
def test_clean_9444_profile_holds_precede_native_effects(clean_9444, monkeypatch, case):
    root = clean_9444[0] / "AI-Commerce"
    profile = root / ca._CLEAN_9444_PROFILE / "Default"
    if case in {"missing", "file"}:
        profile.rmdir()
        if case == "file":
            profile.touch()
    elif case == "inaccessible":
        def scandir(path):
            if path == profile:
                raise PermissionError("private directory")
            return os.scandir(path)

        monkeypatch.setattr(ca.os, "scandir", scandir)
    else:
        lstat = type(profile).lstat

        def junction(path):
            assert root not in path.parents
            if path == root:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return lstat(path)

        monkeypatch.setattr(type(profile), "lstat", junction)

    for operation in (ca.ensure_clean_9444, ca.install_clean_9444_autostart):
        assert operation(opt_in=True) == {"status": "HOLD", "code": "PROFILE_UNAVAILABLE"}


@pytest.mark.parametrize("code", ["UNKNOWN_EFFECT", "FOREIGN_PORT_OWNER", "PROFILE_IN_USE", "PROFILE_LOCKED"])
def test_clean_9444_install_holds_before_registration(clean_9444, monkeypatch, code):
    def foreign(*args):
        raise ca._Clean9444Hold("FOREIGN_PORT_OWNER")

    if code == "UNKNOWN_EFFECT":
        monkeypatch.setattr(ca, "_clean_pending", lambda name: name == "LaunchPending")
    elif code == "FOREIGN_PORT_OWNER":
        monkeypatch.setattr(ca, "_clean_inventory", foreign)
    elif code == "PROFILE_IN_USE":
        monkeypatch.setattr(ca, "_clean_inventory", lambda *args: ("OWNED", None))
    else:
        local = clean_9444[0]
        (local / "AI-Commerce" / ca._CLEAN_9444_PROFILE / "Default" / "lockfile").touch()
    assert ca.install_clean_9444_autostart(opt_in=True) == {"status": "HOLD", "code": code}


def test_clean_9444_task_is_triggerless_and_uses_a_no_console_launcher(clean_9444, monkeypatch):
    owner = clean_9444
    local = owner[0]
    target, candidates = ca._clean_target(owner)
    powershell = local / "PowerShell" / "7" / "pwsh.exe"
    monkeypatch.setattr(ca, "_powershell", lambda: powershell)
    calls = []
    replies = iter(("ENROLLED", "ALREADY_ENROLLED", "STARTED", "AUTOSTART_NOT_ENROLLED",
                    "AUTOSTART_CONFLICT", "private diagnostic"))

    def run(args, **kwargs):
        calls.append((args, kwargs["env"]))
        assert "GITHUB_TOKEN" not in kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=next(replies).encode("ascii"))

    monkeypatch.setattr(subprocess, "run", run)
    assert ca.install_clean_9444_autostart(opt_in=True) == {"status": "ENROLLED", "code": "ENROLLED"}
    assert ca._clean_task(owner, target, candidates, install=False) == "ALREADY_ENROLLED"
    assert ca.ensure_clean_9444(opt_in=True) == {"status": "STARTED", "code": "CDP_VERIFIED"}
    for code in ("AUTOSTART_NOT_ENROLLED", "AUTOSTART_CONFLICT", "AUTOSTART_RUNTIME_UNAVAILABLE"):
        assert ca._clean_task(owner, target, candidates, install=False) == code

    (install_args, install_env), (read_args, read_env), (launcher_args, _) = calls[:3]
    assert launcher_args[:6] == [str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command"]
    for env in (install_env, read_env):
        assert env["AI_COMMERCE_TASK_POWERSHELL"] == str(powershell)
        assert env["AI_COMMERCE_TASK_SESSION"] == "7"
        assert env["AI_COMMERCE_TASK_HISTORICAL_WORKDIR"] == ""
        arguments = env["AI_COMMERCE_TASK_ARGUMENTS"]
        assert arguments.startswith("-I -B -S -c ")
        assert "creationflags=subprocess.CREATE_NO_WINDOW" in arguments
        if os.name == "nt":
            assert repr(launcher_args) in ca._commandline_argv("pythonw.exe " + arguments)[5]
        assert Path(env["AI_COMMERCE_TASK_GUI"]).name == "pythonw.exe"
    install_script = install_args[-1]
    assert "Triggers.Create" not in install_script and "PT1M" not in install_script
    assert "$registered.Definition.Triggers.Count -ne 0" in install_script
    assert "$expected.Settings.MultipleInstances = 2" in install_script
    assert "$expected.Principal.LogonType = 3" in install_script
    assert "$expected, 34, $sid, $null, 3, $sddl" in install_script
    assert install_script.count(".RegisterTaskDefinition(") == 1
    assert ".RegisterTaskDefinition(" not in read_args[-1] and ".RunEx(" not in read_args[-1]
    assert "Get-Acl" not in launcher_args[-1]
    assert "Stop-Process" not in launcher_args[-1] and "/json/list" not in launcher_args[-1]
    assert "Test-PowerShell77" not in ca._CLEAN_9444_TASK_MIGRATION


@pytest.mark.parametrize("action", ["versioned-legacy", "versioned-demand", "unverified", "owner-mismatch"])
def test_clean_9444_mixed_install_uses_attested_action(clean_9444, monkeypatch, action):
    owner = clean_9444
    target, candidates = ca._clean_target(owner)
    stable = owner[0] / "PowerShell" / "7" / "pwsh.exe"
    versioned = stable.parent.parent / "7.7" / "pwsh.exe"
    shadow = owner[0] / "PRIVATE_UNVERIFIED_IMAGE" / "pwsh.exe"
    monkeypatch.setattr(ca, "_native_operator", lambda: owner)
    monkeypatch.setattr(ca, "_trusted_powershells", lambda: (stable, versioned))
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            assert args[0] == str(stable)
            assert "Test-LegacyRegistration $registered" in args[-1]
            assert "GetSecurityDescriptor(7)" in args[-1]
            assert ".RegisterTaskDefinition(" not in args[-1] and ".RunEx(" not in args[-1]
            specs = json.loads(kwargs["env"]["AI_COMMERCE_TASK_TRUSTED_POWERSHELLS"])
            assert [s["path"] for s in specs] == [str(stable), str(versioned)]
            assert all(len(s["demand_sha256"]) == 64 for s in specs)
            if action == "owner-mismatch":
                return SimpleNamespace(returncode=2, stdout=b"PRIVATE_OWNER_DETAIL")
            path = versioned if action.startswith("versioned-") else shadow
            return SimpleNamespace(returncode=0, stdout=(str(path) + "\r\n").encode("utf-8"))
        return SimpleNamespace(returncode=0, stdout=b"ALREADY_ENROLLED")

    monkeypatch.setattr(subprocess, "run", run)
    if not action.startswith("versioned-"):
        with pytest.raises(ca.AcquisitionBlocked, match="^POWERSHELL_UNAVAILABLE$"):
            ca._clean_task(owner, target, candidates, install=False)
        assert len(calls) == 1
        return

    assert ca._clean_task(owner, target, candidates, install=False) == "ALREADY_ENROLLED"
    (probe, probe_kwargs), (read, read_kwargs) = calls
    assert read[0] == str(versioned)
    assert read_kwargs["env"]["AI_COMMERCE_TASK_POWERSHELL"] == str(versioned)
    assert Path(read_kwargs["env"]["AI_COMMERCE_TASK_GUI"]).name == "pythonw.exe"
    assert repr(str(versioned)) in read_kwargs["env"]["AI_COMMERCE_TASK_ARGUMENTS"]
    assert ".RegisterTaskDefinition(" not in read[-1] and ".RunEx(" not in read[-1]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows historical workdir custody")
def test_historical_legacy_workdir_fails_closed(monkeypatch):
    resolved = ca._historical_legacy_workdir()
    assert resolved is not None and resolved.is_dir()
    assert resolved.parts[-3:] == ("System32", "WindowsPowerShell", "v1.0")
    monkeypatch.setattr(ca, "_no_links", lambda _path: False)
    assert ca._historical_legacy_workdir() is None
    monkeypatch.undo()
    monkeypatch.setattr(ca, "_verify_powershell_custody",
                        lambda _path: (_ for _ in ()).throw(ca.AcquisitionBlocked("POWERSHELL_UNAVAILABLE")))
    assert ca._historical_legacy_workdir() is None


def test_clean_9444_migrate_passes_script_via_utf8_stdin_with_historical_workdir(clean_9444, monkeypatch):
    owner = clean_9444
    local = owner[0]
    target, candidates = ca._clean_target(owner)
    powershell = local / "PowerShell" / "7.7" / "pwsh.exe"
    historical = local / "Windows" / "System32" / "WindowsPowerShell" / "v1.0"
    monkeypatch.setattr(ca, "_trusted_powershells", lambda: (powershell,))
    monkeypatch.setattr(ca, "_powershell", lambda: powershell)
    monkeypatch.setattr(ca, "_historical_legacy_workdir", lambda: historical)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"DEMAND_MIGRATED")

    monkeypatch.setattr(subprocess, "run", run)
    assert ca._clean_task(owner, target, candidates, install=False, migrate=True) == "DEMAND_MIGRATED"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == str(powershell)
    assert len(subprocess.list2cmdline(args)) < 512
    assert kwargs["input"] == ca._CLEAN_9444_TASK_MIGRATION.encode("utf-8")
    assert kwargs["env"]["AI_COMMERCE_TASK_HISTORICAL_WORKDIR"] == str(historical)
    assert kwargs["env"]["AI_COMMERCE_TASK_LEGACY_ARGUMENTS"] != ""
    specs = json.loads(kwargs["env"]["AI_COMMERCE_TASK_TRUSTED_POWERSHELLS"])
    assert [s["path"] for s in specs] == [str(powershell)]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows PowerShell 7 attestation")
def test_clean_9444_task_image_and_trusted_powershell_native(tmp_path):
    real_pwsh = ca._powershell()
    real_dir = real_pwsh.parent
    historical_dir = ca._historical_legacy_workdir()
    assert historical_dir is not None and historical_dir.is_dir()
    fake_77 = tmp_path / "PowerShell" / "7.7" / "pwsh.exe"
    fake_77.parent.mkdir(parents=True)
    fake_77.write_bytes(b"unsigned placeholder")
    ps51 = historical_dir / "powershell.exe"
    cmd_exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
    # Verify real Test-TrustedPowerShell + Test-HistoricalLegacyWorkDir + _CLEAN_9444_TASK_IMAGE_SCRIPT.
    funcs = ca._CLEAN_9444_TASK_IMAGE_SCRIPT.split("\nfunction Read-Registration", 1)[1]
    seam = "\n$registered = Read-Registration"
    import hashlib
    demand_args = "demand-args-real-pwsh"
    demand_sha = hashlib.sha256(demand_args.encode("utf-8")).hexdigest()
    specs = json.dumps([
        {"path": str(real_pwsh), "dir": str(real_dir), "demand_sha256": demand_sha},
        {"path": str(fake_77), "dir": str(fake_77.parent), "demand_sha256": "0" * 64},
    ])
    fixture = r"""
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sddl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')'
$action = [pscustomobject]@{
    Type = 0; Path = 'C:\Owner\pythonw.exe'; Arguments = 'demand-args-real-pwsh'; WorkingDirectory = 'C:\Owner'
}
$expected = [pscustomobject]@{
    Principal = [pscustomobject]@{ Id = 'WakeHubOwner'; UserId = $sid; LogonType = 3; RunLevel = 0 }
    Settings = [pscustomobject]@{ AllowDemandStart = $true; RestartCount = 0 }
    Actions = [pscustomobject]@{ Context = 'WakeHubOwner'; Entry = $action }
    Triggers = [pscustomobject]@{ Count = 0 }
}
$trustedPowerShellSpecs = @($env:AI_COMMERCE_TASK_TRUSTED_POWERSHELLS | ConvertFrom-Json)
$trustedPowerShellPaths = @($trustedPowerShellSpecs | ForEach-Object { [string]$_.path })
$trustedPowerShellDirs = @($trustedPowerShellSpecs | ForEach-Object { [string]$_.dir })
$installOwnerSids = @(
    'S-1-5-18', 'S-1-5-32-544',
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
)
$name = 'AI-Commerce-WakeHub-9444-Clean-' + $sid
function New-FakeTask($demand, $path, $dir, $argsText) {
    $t = [pscustomobject]@{ Type = 9; Enabled = $true; UserId = $sid
        Repetition = [pscustomobject]@{ Interval = 'PT1M' } }
    $a = [pscustomobject]@{ Type = 0; Path = $path; Arguments = $argsText; WorkingDirectory = $dir }
    $actions = [pscustomobject]@{ Count = 1; Context = 'WakeHubOwner'; Entry = $a }
    $actions | Add-Member ScriptMethod Item { $this.Entry }
    $triggers = [pscustomobject]@{ Count = [int](!$demand); Entry = $t }
    $triggers | Add-Member ScriptMethod Item { $this.Entry }
    $task = [pscustomobject]@{ Enabled = $true; State = 3; Instances = 0; Xml = 'xml'; Sddl = $sddl
        Definition = [pscustomobject]@{ Principal = $expected.Principal.PSObject.Copy()
            Settings = $expected.Settings.PSObject.Copy(); Actions = $actions; Triggers = $triggers } }
    $task | Add-Member ScriptMethod GetSecurityDescriptor { $this.Sddl }
    return $task
}
$folder = [pscustomobject]@{}
$folder | Add-Member ScriptMethod GetTask { param($n) $script:current }
"""
    body = funcs.replace(seam, "\nfunction Test-DemandRuntime { return $true }\n" + seam, 1)
    cases = [
        ("legacy_real", False, str(real_pwsh), str(real_dir), "legacy-args", str(historical_dir), str(real_pwsh)),
        ("legacy_mixed_dir", False, str(real_pwsh), str(fake_77.parent), "legacy-args", str(historical_dir), str(real_pwsh)),
        ("legacy_historical_winps_dir", False, str(real_pwsh), str(historical_dir), "legacy-args", str(historical_dir), str(real_pwsh)),
        ("legacy_historical_winps_slash", False, str(real_pwsh), str(historical_dir) + "\\", "legacy-args", str(historical_dir), str(real_pwsh)),
        ("legacy_historical_no_env", False, str(real_pwsh), str(historical_dir), "legacy-args", "", None),
        ("legacy_historical_traversal", False, str(real_pwsh), str(historical_dir) + r"\..\v1.0", "legacy-args", str(historical_dir), None),
        ("legacy_historical_injection", False, str(real_pwsh), str(historical_dir) + "; whoami", "legacy-args", str(historical_dir), None),
        ("demand_real", True, r"C:\Owner\pythonw.exe", r"C:\Owner", demand_args, str(historical_dir), str(real_pwsh)),
        ("legacy_unsigned", False, str(fake_77), str(fake_77.parent), "legacy-args", str(historical_dir), None),
        ("legacy_ps51", False, str(ps51), str(historical_dir), "legacy-args", str(historical_dir), None),
        ("legacy_cmd", False, str(cmd_exe), str(cmd_exe.parent), "legacy-args", str(historical_dir), None),
        ("legacy_bad_dir", False, str(real_pwsh), str(tmp_path), "legacy-args", str(historical_dir), None),
        ("demand_bad_args", True, r"C:\Owner\pythonw.exe", r"C:\Owner", "tampered", str(historical_dir), None),
    ]
    for label, demand, path, workdir, args_text, hist_env, expected_out in cases:
        env = ca._child_environment()
        env.update(
            AI_COMMERCE_TASK_TRUSTED_POWERSHELLS=specs,
            AI_COMMERCE_TASK_LEGACY_ARGUMENTS="legacy-args",
            AI_COMMERCE_TASK_HISTORICAL_WORKDIR=hist_env,
            TEST_CASE_PATH=path,
            TEST_CASE_WORKDIR=workdir,
            TEST_CASE_ARGS=args_text,
        )
        script = (
            fixture
            + f"$script:current = New-FakeTask ${str(demand).lower()} $env:TEST_CASE_PATH $env:TEST_CASE_WORKDIR $env:TEST_CASE_ARGS\n"
            + "function Read-Registration" + body
        )
        res = subprocess.run([str(real_pwsh), *ca._CLEAN_POWERSHELL_FLAGS, script],
                             capture_output=True, text=True, encoding="utf-8", env=env, timeout=15)
        if expected_out is None:
            assert res.returncode == 2, f"{label}: expected exit 2, got {res.returncode} / {res.stdout!r}"
        else:
            assert res.returncode == 0 and not res.stderr, f"{label}: {res.stderr}"
            assert res.stdout.strip() == expected_out


_CLEAN_TASK_MIGRATION_FIXTURE = r"""
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sddl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')'
$action = [pscustomobject]@{
    Type = 0; Path = 'C:\Owner\pythonw.exe'; Arguments = 'demand'; WorkingDirectory = 'C:\Owner'
}
$expected = [pscustomobject]@{
    Principal = [pscustomobject]@{ Id = 'WakeHubOwner'; UserId = $sid; LogonType = 3; RunLevel = 0 }
    Settings = [pscustomobject]@{ AllowDemandStart = $true; RestartCount = 0 }
    Actions = [pscustomobject]@{ Context = 'WakeHubOwner'; Entry = $action }
    Triggers = [pscustomobject]@{ Count = 0 }
}
$localRoot = [Environment]::GetFolderPath('LocalApplicationData')
$aiRoot = Join-Path $localRoot 'AI-Commerce'
$cleanProfile = Join-Path $aiRoot 'WakeHub-9444-Clean'
$d5Profile = Join-Path $aiRoot 'D5-Chrome-9333'
$chromePath = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$historicalWorkDir = $env:TEST_HISTORICAL_WORKDIR
$env:AI_COMMERCE_TASK_PROFILE_DIR = $cleanProfile
$env:AI_COMMERCE_TASK_CHROME_EXECUTABLES = (@($chromePath) | ConvertTo-Json -Compress)
$env:AI_COMMERCE_TASK_LEGACY_ARGUMENTS = 'legacy'
$trustedPowerShellSpecs = @($env:AI_COMMERCE_TASK_TRUSTED_POWERSHELLS | ConvertFrom-Json)
$trustedPowerShellPaths = @($trustedPowerShellSpecs | ForEach-Object { [string]$_.path })
$trustedPowerShellDirs = @($trustedPowerShellSpecs | ForEach-Object { [string]$_.dir })
$installOwnerSids = @(
    'S-1-5-18', 'S-1-5-32-544',
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
)
$name = 'AI-Commerce-WakeHub-9444-Clean-' + $sid
$script:d5ScriptPath = $env:TEST_D5_SCRIPT_PATH
$script:d5RootDir = $env:TEST_D5_ROOT_DIR
$script:d5GetTaskCalls = 0
function New-FakeTask($demand, $legacyPath = $trustedPowerShellPaths[0], $legacyDir = $trustedPowerShellDirs[0]) {
    $t = [pscustomobject]@{ Type = 9; Enabled = $true; UserId = $sid
        Repetition = [pscustomobject]@{ Interval = 'PT1M' } }
    $a = if ($demand) { $action.PSObject.Copy() } else {
        [pscustomobject]@{ Type = 0; Path = $legacyPath; Arguments = 'legacy'
            WorkingDirectory = $legacyDir }
    }
    $actions = [pscustomobject]@{ Count = 1; Context = 'WakeHubOwner'; Entry = $a }
    $actions | Add-Member ScriptMethod Item { $this.Entry }
    $triggers = [pscustomobject]@{ Count = [int](!$demand); Entry = $t }
    $triggers | Add-Member ScriptMethod Item { $this.Entry }
    $task = [pscustomobject]@{ Name = $name; Enabled = $true; State = 3; Instances = 0; Xml = 'prior xml'
        Sddl = $sddl; Definition = [pscustomobject]@{ Principal = $expected.Principal.PSObject.Copy()
            Settings = $expected.Settings.PSObject.Copy(); Actions = $actions; Triggers = $triggers } }
    $task | Add-Member ScriptMethod GetSecurityDescriptor { $this.Sddl }
    $task | Add-Member ScriptMethod GetInstances { [pscustomobject]@{ Count = $this.Instances } }
    return $task
}
function New-FakeD5Task($enabled = $true, $state = 3, $port = '9333', $dir = $d5Profile,
                        $exePath = $chromePath, $argsOverride = $null, $workDir = $null,
                        $interval = $null, $d5Sddl = $sddl, $userId = $sid, $instances = 0) {
    $t = [pscustomobject]@{ Type = 9; Enabled = $true; UserId = $userId
        Repetition = [pscustomobject]@{ Interval = $interval; Duration = $null } }
    $argsText = if ($null -ne $argsOverride) { $argsOverride } else {
        '--remote-debugging-port=' + $port + ' --remote-debugging-address=127.0.0.1 ' +
        '--user-data-dir="' + $dir + '" --profile-directory=Default --no-first-run'
    }
    $a = [pscustomobject]@{ Type = 0; Path = $exePath; Arguments = $argsText; WorkingDirectory = $workDir }
    $actions = [pscustomobject]@{ Count = 1; Context = 'D5Owner'; Entry = $a }
    $actions | Add-Member ScriptMethod Item { $this.Entry }
    $triggers = [pscustomobject]@{ Count = 1; Entry = $t }
    $triggers | Add-Member ScriptMethod Item { $this.Entry }
    $principal = [pscustomobject]@{ Id = 'D5Owner'; UserId = $userId; LogonType = 3; RunLevel = 0; GroupId = $null }
    $task = [pscustomobject]@{ Name = 'AI-Commerce-D5-CDP-AtLogon'; Enabled = $enabled; State = $state
        Instances = $instances; Xml = '<Task><RegistrationInfo><URI>\AI-Commerce-D5-CDP-AtLogon</URI></RegistrationInfo></Task>'
        Sddl = $d5Sddl; Definition = [pscustomobject]@{ Principal = $principal
            Settings = [pscustomobject]@{}; Actions = $actions; Triggers = $triggers } }
    $task | Add-Member ScriptMethod GetSecurityDescriptor { $this.Sddl }
    $task | Add-Member ScriptMethod GetInstances { [pscustomobject]@{ Count = $this.Instances } }
    return $task
}
$script:current = New-FakeTask ($case -eq 'already')
$script:d5 = $null
$script:writes = 0
$script:d5Writes = 0
$script:values = @{}
$pwshFileArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script:d5ScriptPath + '"'
switch ($case) {
    'mixed_7_77_same_dir' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $trustedPowerShellDirs[1]
    }
    'mixed_7_77_cross_dir' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $trustedPowerShellDirs[0]
    }
    'legacy_77_historical_workdir' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $historicalWorkDir
    }
    'legacy_77_historical_workdir_slash' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] ($historicalWorkDir + '\')
    }
    'legacy_historical_no_proof' {
        $env:AI_COMMERCE_TASK_HISTORICAL_WORKDIR = ''
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $historicalWorkDir
    }
    'legacy_historical_bad_custody' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $historicalWorkDir
    }
    'legacy_historical_traversal' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] ($historicalWorkDir + '\..\v1.0')
    }
    'legacy_historical_injection' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] ($historicalWorkDir + '; calc.exe')
    }
    'legacy_historical_wrong_owner' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $historicalWorkDir
        $script:current.Sddl = $sddl.Replace('O:' + $sid, 'O:SY')
    }
    'legacy_historical_unsigned_pwsh' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $historicalWorkDir
    }
    'legacy_historical_changed_state' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $historicalWorkDir
        $script:current.State = 4
    }
    'legacy_51' {
        $script:current = New-FakeTask $false ($historicalWorkDir + '\powershell.exe') $historicalWorkDir
    }
    'pwsh_unverified' {
        $script:current = New-FakeTask $false 'C:\Caller\pwsh.exe' 'C:\Caller'
    }
    'pwsh_unsigned' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $trustedPowerShellDirs[1]
    }
    'pwsh_wrong_workdir' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[0] 'C:\Windows\Temp'
    }
    'd5_disabled' {
        $script:d5 = New-FakeD5Task -enabled $false -state 1
    }
    'd5_disabled_active' {
        $script:d5 = New-FakeD5Task -enabled $false -state 1 -instances 1
    }
    'd5_enabled_9333_ready' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3
    }
    'd5_enabled_9333_running' {
        $script:d5 = New-FakeD5Task -enabled $true -state 4 -instances 1
    }
    'd5_enabled_quoted_chrome' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath ('"' + $chromePath + '"')
    }
    'd5_enabled_standard_acl' {
        $stdAcl = 'O:SYD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')(A;;FR;;;AU)'
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -d5Sddl $stdAcl
    }
    'd5_pwsh_file_start_process_77' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[1] `
            -argsOverride $pwshFileArgs -workDir $historicalWorkDir
    }
    'd5_pwsh_file_direct_call_real' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride $pwshFileArgs
    }
    'd5_pwsh_file_process_start' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride ('-NoProfile -NonInteractive -File "' + $script:d5ScriptPath + '"')
    }
    'd5_pwsh_command_inline_chrome' {
        $inlineArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -Command "Start-Process -FilePath ''' + $chromePath +
            ''' -ArgumentList @(''--remote-debugging-port=9333'',''--remote-debugging-address=127.0.0.1'',''--user-data-dir=' +
            $d5Profile + ''',''--profile-directory=Default'')"'
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] -argsOverride $inlineArgs
    }
    'd5_pwsh_command_invokes_file' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride ('-NoProfile -NonInteractive -WindowStyle Hidden -Command "& ''' + $script:d5ScriptPath + '''"')
    }
    'd5_pwsh_and_9444_historical_workdir' {
        $script:current = New-FakeTask $false $trustedPowerShellPaths[1] $historicalWorkDir
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[1] `
            -argsOverride $pwshFileArgs -workDir $historicalWorkDir
    }
    'd5_pwsh_unqualified_path' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath 'pwsh.exe' -argsOverride $pwshFileArgs
    }
    'd5_pwsh_foreign_binary' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath 'C:\Caller\pwsh.exe' -argsOverride $pwshFileArgs
    }
    'd5_pwsh_unsigned' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[1] -argsOverride $pwshFileArgs
    }
    'd5_pwsh_winps51' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath ($historicalWorkDir + '\powershell.exe') -argsOverride $pwshFileArgs
    }
    'd5_pwsh_encoded_cmd' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride '-NoProfile -EncodedCommand UwB0AGEAcgB0AC0AUAByAG8AYwBlAHMAcwA='
    }
    'd5_pwsh_extra_args' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride ($pwshFileArgs + ' -Extra 1')
    }
    'd5_pwsh_malformed_args' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride ('-NoProfile -File "' + $script:d5ScriptPath)
    }
    'd5_pwsh_missing_script' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride ('-NoProfile -File "' + $script:d5ScriptPath + '.missing.ps1"')
    }
    'd5_pwsh_bad_workdir' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride $pwshFileArgs -workDir 'C:\Windows\Temp'
    }
    { $_ -in @(
        'd5_pwsh_script_port_9444', 'd5_pwsh_script_wrong_port', 'd5_pwsh_script_clean_profile',
        'd5_pwsh_script_outside_ai_root', 'd5_pwsh_script_9444_contamination',
        'd5_pwsh_script_path_9444', 'd5_pwsh_script_arbitrary_cmd',
        'd5_pwsh_script_subexpr_injection', 'd5_pwsh_script_unsigned_chrome',
        'd5_pwsh_script_writable_acl', 'd5_pwsh_script_writable_parent',
        'd5_pwsh_script_tampered_sig', 'd5_pwsh_script_reparse',
        'd5_pwsh_script_race_before_write', 'd5_pwsh_script_mutated_during_write'
    ) } {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] `
            -argsOverride $pwshFileArgs
    }
    'd5_ambiguous_9444' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -port '9444'
    }
    'd5_ambiguous_pt1m' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -interval 'PT1M'
    }
    'd5_ambiguous_same_profile' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -dir $cleanProfile
    }
    'd5_ambiguous_reparse' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3
    }
    'd5_ambiguous_script' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0]
    }
    'd5_ambiguous_writable_acl' {
        $badAcl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')(A;;GW;;;AU)'
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -d5Sddl $badAcl
    }
    'd5_ambiguous_foreign_owner' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -userId 'S-1-5-21-9-9-9-1002'
    }
    'd5_mutated_during_write' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3
    }
    'wrong_action' { $script:current.Definition.Actions.Entry.Arguments = 'private wrong action' }
    'wrong_owner' { $script:current.Sddl = $sddl.Replace('O:' + $sid, 'O:SY') }
    'wrong_principal' { $script:current.Definition.Principal.UserId = '' }
    'running' { $script:current.State = 4 }
    'queued' { $script:current.Instances = 1 }
    'unresolved' { $script:values.DemandMigrationRollback = 'saved' }
    'replay' {
        $script:values.DemandMigrationRollback = 'saved'
        $script:values.DemandMigrationComplete = 1
    }
}
$registry = [pscustomobject]@{}
$registry | Add-Member ScriptMethod GetValueNames { $script:values.Keys }
$registry | Add-Member ScriptMethod GetValue { param($name) $script:values[$name] }
$registry | Add-Member ScriptMethod SetValue { param($name, $value) $script:values[$name] = $value }
$registry | Add-Member ScriptMethod Flush {}
$registry | Add-Member ScriptMethod Dispose {}
$folder = [pscustomobject]@{}
$folder | Add-Member ScriptMethod GetTask {
    param($taskName)
    if ($taskName -ceq 'AI-Commerce-D5-CDP-AtLogon') {
        $script:d5GetTaskCalls++
        if ($case -eq 'd5_pwsh_script_race_before_write' -and $script:d5GetTaskCalls -eq 2) {
            [IO.File]::AppendAllText($script:d5ScriptPath, "`n# raced before write`n")
        }
        return $script:d5
    }
    if ($taskName -cne $name) { throw 'Unexpected target' }
    return $script:current
}
$folder | Add-Member ScriptMethod RegisterTaskDefinition {
    param($taskName, $definition, $flags, $user, $password, $logon, $security)
    if ($taskName -ceq 'AI-Commerce-D5-CDP-AtLogon') { $script:d5Writes++; throw 'Never mutate D5' }
    if ($taskName -cne $name -or $flags -ne 52 -or $user -cne $sid -or $logon -ne 3 -or
        $security -cne $sddl -or !$script:values.DemandMigrationRollback -or
        $definition.Triggers.Count -ne 0 -or $definition.Actions.Entry.Arguments -cne 'demand') {
        throw 'Unsafe migration'
    }
    $script:writes++
    $script:current = New-FakeTask $true
    if ($case -eq 'readback_fault') { $script:current.State = 4 }
    if ($case -eq 'd5_mutated_during_write') { $script:d5.Xml = '<Task>changed</Task>' }
    if ($case -eq 'd5_pwsh_script_mutated_during_write') {
        [IO.File]::AppendAllText($script:d5ScriptPath, "`n# mutated during write`n")
    }
}
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell task definition")
@pytest.mark.parametrize("case, code, writes, complete", [
    ("legacy", "DEMAND_MIGRATED", 1, True),
    ("mixed_7_77_same_dir", "DEMAND_MIGRATED", 1, True),
    ("mixed_7_77_cross_dir", "DEMAND_MIGRATED", 1, True),
    ("legacy_77_historical_workdir", "DEMAND_MIGRATED", 1, True),
    ("legacy_77_historical_workdir_slash", "DEMAND_MIGRATED", 1, True),
    ("legacy_historical_no_proof", "AUTOSTART_CONFLICT", 0, False),
    ("legacy_historical_bad_custody", "AUTOSTART_CONFLICT", 0, False),
    ("legacy_historical_traversal", "AUTOSTART_CONFLICT", 0, False),
    ("legacy_historical_injection", "AUTOSTART_CONFLICT", 0, False),
    ("legacy_historical_wrong_owner", "AUTOSTART_CONFLICT", 0, False),
    ("legacy_historical_unsigned_pwsh", "AUTOSTART_CONFLICT", 0, False),
    ("legacy_historical_changed_state", "TASK_MIGRATION_NOT_READY", 0, False),
    ("legacy_51", "AUTOSTART_CONFLICT", 0, False),
    ("pwsh_unverified", "AUTOSTART_CONFLICT", 0, False),
    ("pwsh_unsigned", "AUTOSTART_CONFLICT", 0, False),
    ("pwsh_wrong_workdir", "AUTOSTART_CONFLICT", 0, False),
    ("d5_disabled", "DEMAND_MIGRATED", 1, True),
    ("d5_disabled_active", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_enabled_9333_ready", "DEMAND_MIGRATED", 1, True),
    ("d5_enabled_9333_running", "DEMAND_MIGRATED", 1, True),
    ("d5_enabled_quoted_chrome", "DEMAND_MIGRATED", 1, True),
    ("d5_enabled_standard_acl", "DEMAND_MIGRATED", 1, True),
    ("d5_pwsh_file_start_process_77", "DEMAND_MIGRATED", 1, True),
    ("d5_pwsh_file_direct_call_real", "DEMAND_MIGRATED", 1, True),
    ("d5_pwsh_file_process_start", "DEMAND_MIGRATED", 1, True),
    ("d5_pwsh_command_inline_chrome", "DEMAND_MIGRATED", 1, True),
    ("d5_pwsh_command_invokes_file", "DEMAND_MIGRATED", 1, True),
    ("d5_pwsh_and_9444_historical_workdir", "DEMAND_MIGRATED", 1, True),
    ("d5_pwsh_unqualified_path", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_foreign_binary", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_unsigned", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_winps51", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_encoded_cmd", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_extra_args", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_malformed_args", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_missing_script", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_bad_workdir", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_port_9444", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_wrong_port", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_clean_profile", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_outside_ai_root", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_9444_contamination", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_path_9444", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_arbitrary_cmd", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_subexpr_injection", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_unsigned_chrome", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_writable_acl", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_writable_parent", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_tampered_sig", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_reparse", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_race_before_write", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_pwsh_script_mutated_during_write", "UNKNOWN_EFFECT", 1, False),
    ("d5_ambiguous_9444", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_ambiguous_pt1m", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_ambiguous_same_profile", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_ambiguous_reparse", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_ambiguous_script", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_ambiguous_writable_acl", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_ambiguous_foreign_owner", "TASK_MIGRATION_AMBIGUOUS", 0, False),
    ("d5_mutated_during_write", "UNKNOWN_EFFECT", 1, False),
    ("already", "DEMAND_ALREADY_READY", 0, False),
    ("wrong_action", "AUTOSTART_CONFLICT", 0, False),
    ("wrong_owner", "AUTOSTART_CONFLICT", 0, False),
    ("wrong_principal", "AUTOSTART_CONFLICT", 0, False),
    ("running", "TASK_MIGRATION_NOT_READY", 0, False),
    ("queued", "TASK_MIGRATION_NOT_READY", 0, False),
    ("unresolved", "UNKNOWN_EFFECT", 0, False),
    ("replay", "TASK_MIGRATION_REPLAY", 0, True),
    ("readback_fault", "UNKNOWN_EFFECT", 1, False),
])
def test_clean_9444_migration_offline(case, code, writes, complete):
    real_pwsh = ca._powershell()
    historical_dir = ca._historical_legacy_workdir()
    assert historical_dir is not None and historical_dir.is_dir()
    alt_slot = "7" if real_pwsh.parent.name.casefold() == "7.7" else "7.7"
    alt_pwsh = real_pwsh.parent.parent / alt_slot / "pwsh.exe"
    specs = json.dumps([
        {"path": str(real_pwsh), "dir": str(real_pwsh.parent), "demand_sha256": "0" * 64},
        {"path": str(alt_pwsh), "dir": str(alt_pwsh.parent), "demand_sha256": "1" * 64},
    ])
    # Allocate outside pytest's tmp_path (whose directory name includes
    # test_clean_9444_migration_offline) so Get-AttestedD5Script's fail-closed
    # 9444 path-contamination check is exercised accurately for every case.
    raw_root = None
    for _ in range(8):
        candidate = tempfile.mkdtemp(prefix="d5_launcher_")
        resolved_candidate = str(Path(candidate).resolve())
        if not re.search(r"(?<![0-9])9444(?![0-9])", resolved_candidate):
            raw_root = candidate
            break
        shutil.rmtree(candidate, ignore_errors=True)
    assert raw_root is not None, "Unable to allocate clean D5 launcher temp root"
    d5_root = Path(raw_root).resolve() / "d5-launcher"
    d5_dir = d5_root / "scripts"
    script_name = "start-d5-9444.ps1" if case == "d5_pwsh_script_path_9444" else "start-d5-cdp.ps1"
    d5_script = d5_dir / script_name
    script_bodies = {
        "d5_pwsh_file_direct_call_real": (
            "$ErrorActionPreference = 'Stop'\n"
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\D5-Chrome-9333'\n"
            "& $chrome '--remote-debugging-port=9333' '--remote-debugging-address=127.0.0.1' "
            "\"--user-data-dir=$profileDir\" '--profile-directory=Default' '--no-first-run'\n"
        ),
        "d5_pwsh_file_process_start": (
            "$ErrorActionPreference = 'Stop'\n"
            "$local = [Environment]::GetFolderPath('LocalApplicationData')\n"
            "$profileDir = [IO.Path]::Combine($local, 'AI-Commerce', 'D5-Chrome-9333')\n"
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "[void][Diagnostics.Process]::Start($chrome, "
            "\"--remote-debugging-port=9333 --remote-debugging-address=127.0.0.1 "
            "--user-data-dir=`\"$profileDir`\" --profile-directory=Default\")\n"
        ),
        "d5_pwsh_script_port_9444": (
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\D5-Chrome-9333'\n"
            "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9444', "
            "\"--user-data-dir=$profileDir\")\n"
        ),
        "d5_pwsh_script_wrong_port": (
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\D5-Chrome-9333'\n"
            "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9222', "
            "\"--user-data-dir=$profileDir\")\n"
        ),
        "d5_pwsh_script_clean_profile": (
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\WakeHub-9444-Clean'\n"
            "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333', "
            "\"--user-data-dir=$profileDir\")\n"
        ),
        "d5_pwsh_script_outside_ai_root": (
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333', "
            "'--user-data-dir=C:\\Windows\\Temp\\OtherProfile')\n"
        ),
        "d5_pwsh_script_9444_contamination": (
            "# also mentions 9444\n"
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\D5-Chrome-9333'\n"
            "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333', "
            "\"--user-data-dir=$profileDir\")\n"
        ),
        "d5_pwsh_script_arbitrary_cmd": (
            "Invoke-Expression 'Write-Host hi'\n"
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\D5-Chrome-9333'\n"
            "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333', "
            "\"--user-data-dir=$profileDir\")\n"
        ),
        "d5_pwsh_script_subexpr_injection": (
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
            "$profileDir = Join-Path $env:LOCALAPPDATA \"AI-Commerce\\D5-$(whoami)\"\n"
            "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333', "
            "\"--user-data-dir=$profileDir\")\n"
        ),
        "d5_pwsh_script_unsigned_chrome": (
            "$chrome = 'C:\\Caller\\chrome.exe'\n"
            "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\D5-Chrome-9333'\n"
            "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333', "
            "\"--user-data-dir=$profileDir\")\n"
        ),
    }
    default_d5_script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
        "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\D5-Chrome-9333'\n"
        "$existing = Get-NetTCPConnection -LocalPort 9333 -State Listen -ErrorAction SilentlyContinue\n"
        "if ($existing) { exit 0 }\n"
        "$chromeArgs = @(\n"
        "    '--remote-debugging-port=9333',\n"
        "    '--remote-debugging-address=127.0.0.1',\n"
        "    \"--user-data-dir=$profileDir\",\n"
        "    '--profile-directory=Default',\n"
        "    '--no-first-run'\n"
        ")\n"
        "Start-Process -FilePath $chrome -ArgumentList $chromeArgs\n"
    )
    try:
        d5_dir.mkdir(parents=True)
        d5_script.write_text(script_bodies.get(case, default_d5_script), encoding="utf-8")

        functions = ca._CLEAN_9444_TASK_MIGRATION.split("\nfunction Read-Registration", 1)[1]
        seam = "\n$registered = Read-Registration"
        migration_entry = "\n$key = $null"
        # Keep real Test-TrustedPowerShell, Test-HistoricalLegacyWorkDir, AST parser, file hashing,
        # and NTFS ACL checks; only simulate synthetic non-installed 7.7 / Chrome / reparse / signature
        # OS states unavailable in hosted CI, and isolate d5_root from runner temp ancestors.
        acl_setup = r"""
$ownerRule = [Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'Allow')
$sysRule = [Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-5-18'), 'FullControl', 'Allow')
$admRule = [Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'), 'FullControl', 'Allow')
function Set-ProtectedAcl($targetPath, $isFile, $addUsersWrite = $false) {
    $sec = if ($isFile) { [Security.AccessControl.FileSecurity]::new() }
           else { [Security.AccessControl.DirectorySecurity]::new() }
    $sec.SetAccessRuleProtection($true, $false)
    $sec.AddAccessRule($ownerRule)
    $sec.AddAccessRule($sysRule)
    $sec.AddAccessRule($admRule)
    if ($addUsersWrite) {
        $badRule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'), 'Write', 'Allow')
        $sec.AddAccessRule($badRule)
    }
    if ($isFile) { [IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($targetPath), $sec) }
    else { [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($targetPath), $sec) }
}
Set-ProtectedAcl $script:d5RootDir $false $false
Set-ProtectedAcl ([IO.Path]::GetDirectoryName($script:d5ScriptPath)) $false ($case -eq 'd5_pwsh_script_writable_parent')
Set-ProtectedAcl $script:d5ScriptPath $true ($case -eq 'd5_pwsh_script_writable_acl')
"""
        overrides = r"""
function Test-DemandRuntime { return $true }
function Open-MigrationState($write) { return $registry }
$realSigned = (Get-Item function:Test-SignedExecutable).ScriptBlock
function Test-SignedExecutable($path, $publisher) {
    if ($path -ieq $chromePath -and $publisher -match 'Google') { return $true }
    if ($path -ieq $trustedPowerShellPaths[1] -and $publisher -match 'Microsoft') {
        return $case -notin @('pwsh_unsigned', 'legacy_historical_unsigned_pwsh', 'd5_pwsh_unsigned')
    }
    return & $realSigned $path $publisher
}
$realTrusted = (Get-Item function:Test-TrustedPowerShell).ScriptBlock
function Test-TrustedPowerShell($path) {
    if ($path -ieq $trustedPowerShellPaths[1]) {
        return ($case -notin @('pwsh_unsigned', 'legacy_historical_unsigned_pwsh', 'd5_pwsh_unsigned')) -and
            ($path -in $trustedPowerShellPaths)
    }
    return & $realTrusted $path
}
$realCustody = (Get-Item function:Get-AttestedPathCustodySddl).ScriptBlock
function Get-AttestedPathCustodySddl($path, $allowedOwners, $expectFile = $false) {
    if ($case -eq 'legacy_historical_bad_custody' -and $path -ieq $historicalWorkDir) { return $null }
    if ($case -eq 'd5_pwsh_script_reparse' -and $path -ieq $script:d5ScriptPath) { return $null }
    $full = [IO.Path]::GetFullPath($path).TrimEnd([char]92)
    $rootPrefix = [IO.Path]::GetFullPath($script:d5RootDir).TrimEnd([char]92)
    if ($full -ieq $rootPrefix -or $full.StartsWith($rootPrefix + '\', [StringComparison]::OrdinalIgnoreCase)) {
        # Run real NTFS owner/ACL/reparse checks from the isolated test root down to the script.
        $parts = [Collections.Generic.Stack[string]]::new()
        for ($cur = $full; $cur; $cur = [IO.Path]::GetDirectoryName($cur)) {
            $parts.Push($cur)
            if ($cur -ieq $rootPrefix) { break }
        }
        $sections = [Security.AccessControl.AccessControlSections]::Owner -bor
            [Security.AccessControl.AccessControlSections]::Access
        $aceOwners = @($allowedOwners) + @('S-1-3-4')
        $leafSddl = $null
        foreach ($part in $parts) {
            $entry = Get-Item -LiteralPath $part -Force -ErrorAction Stop
            if (!$entry -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $null }
            $isLeaf = $part -ieq $full
            $aclObj = if ($isLeaf -and $expectFile) {
                [IO.FileSystemAclExtensions]::GetAccessControl([IO.FileInfo]$entry, $sections)
            } else {
                [IO.FileSystemAclExtensions]::GetAccessControl([IO.DirectoryInfo]$entry, $sections)
            }
            $partSddl = $aclObj.GetSecurityDescriptorSddlForm('Owner, Access')
            $sd = [Security.AccessControl.RawSecurityDescriptor]::new($partSddl)
            if (!$sd.Owner -or $sd.Owner.Value -cnotin $allowedOwners -or !$sd.DiscretionaryAcl) { return $null }
            foreach ($ace in $sd.DiscretionaryAcl) {
                if ($ace -isnot [Security.AccessControl.CommonAce]) { return $null }
                if ($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly) { continue }
                if ($ace.AceType -eq [Security.AccessControl.AceType]::AccessDenied) { continue }
                if ($ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed) { return $null }
                if (($ace.AccessMask -band 0x500D0156) -and $ace.SecurityIdentifier.Value -cnotin $aceOwners) {
                    return $null
                }
            }
            if ($isLeaf) { $leafSddl = $partSddl }
        }
        return $leafSddl
    }
    return & $realCustody $path $allowedOwners $expectFile
}
"""
        if case == "d5_pwsh_script_tampered_sig":
            overrides += r"""
function Get-AuthenticodeSignature($LiteralPath) {
    if ($LiteralPath -ieq $script:d5ScriptPath) {
        return [pscustomobject]@{ Status = 'HashMismatch'; SignerCertificate = $null }
    }
    return Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $LiteralPath
}
"""
        functions = functions.replace(seam, overrides + seam, 1)
        if case == "d5_ambiguous_reparse":
            functions = functions.replace(
                migration_entry,
                "\nfunction Test-PlainPathOrAncestors($path) { return $false }" + migration_entry,
                1,
            )
        env = ca._child_environment()
        env.update(
            AI_COMMERCE_TASK_TRUSTED_POWERSHELLS=specs,
            AI_COMMERCE_TASK_HISTORICAL_WORKDIR=str(historical_dir),
            TEST_HISTORICAL_WORKDIR=str(historical_dir),
            TEST_D5_SCRIPT_PATH=str(d5_script),
            TEST_D5_ROOT_DIR=str(d5_root),
        )
        harness_script = (
            f"$case = '{case}'\n" + _CLEAN_TASK_MIGRATION_FIXTURE + acl_setup
            + "\n$result = & {\nfunction Read-Registration" + functions.replace("; exit", "; return")
            + "\n}\n"
            + "@{ code = $result; writes = $script:writes; d5Writes = $script:d5Writes;"
              " triggersAfter = $script:current.Definition.Triggers.Count;"
              " complete = [bool]$script:values.DemandMigrationComplete;"
              " rollback = [bool]$script:values.DemandMigrationRollback } | ConvertTo-Json -Compress\n"
        )
        stdin_runner = (
            "[Console]::InputEncoding=[Text.UTF8Encoding]::new(); "
            "& ([ScriptBlock]::Create([Console]::In.ReadToEnd()))"
        )
        result = subprocess.run(
            [str(real_pwsh), *ca._CLEAN_POWERSHELL_FLAGS, stdin_runner],
            input=harness_script, capture_output=True, text=True, encoding="utf-8", env=env, timeout=15)
    finally:
        shutil.rmtree(raw_root, ignore_errors=True)
    assert result.returncode == 0 and not result.stderr, result.stderr
    observed = json.loads(result.stdout)
    assert observed == {
        "code": code, "writes": writes, "d5Writes": 0,
        "triggersAfter": 0 if writes or case == "already" else 1,
        "complete": complete,
        "rollback": bool(writes) or case in {"unresolved", "replay"},
    }


def test_clean_9444_reconcile_read_is_strictly_readonly_and_sanitized(clean_9444, monkeypatch):
    for forbidden in (
        ".RegisterTaskDefinition(", ".RunEx(", ".CreateSubKey(",
        ".SetValue(", ".DeleteValue(", ".DeleteTask(", "Open-MigrationState $true",
    ):
        assert forbidden not in ca._CLEAN_9444_FORENSIC_READ_SCRIPT
    journal_reader = ca._CLEAN_9444_FORENSIC_READ_SCRIPT.split("function Get-ReconcileJournalState", 1)[1]
    journal_reader = journal_reader.split("function Get-ReconcileRelevantTasks", 1)[0]
    assert "Open-MigrationState $false" in journal_reader
    for forbidden in ("Get-Content", "ReadAll", "OpenRead", "Get-ChildItem"):
        assert forbidden not in journal_reader

    owner = clean_9444
    local = owner[0]
    target, candidates = ca._clean_target(owner)
    pwsh_7 = local / "PowerShell" / "7" / "pwsh.exe"
    pwsh_77 = local / "PowerShell" / "7.7" / "pwsh.exe"
    historical = local / "Windows" / "System32" / "WindowsPowerShell" / "v1.0"
    monkeypatch.setattr(ca, "_trusted_powershells", lambda: (pwsh_7, pwsh_77))
    # Simulate absent/conflicting task where _powershell() fails task image resolution;
    # the read-only reconciler must still run using trusted[0] and full trusted specs.
    monkeypatch.setattr(
        ca, "_powershell",
        lambda: (_ for _ in ()).throw(ca.AcquisitionBlocked("POWERSHELL_UNAVAILABLE")),
    )
    monkeypatch.setattr(ca, "_historical_legacy_workdir", lambda: historical)
    monkeypatch.setitem(ca.os.environ, "AI_COMMERCE_TASK_REGISTRY", "PRIVATE_UNTRUSTED_REGISTRY")

    replies = iter((
        SimpleNamespace(returncode=0, stdout=b'{"task":"DEMAND_READY","journal":"COMPLETE","d5":"ENABLED_ATTESTED"}\r\n'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"DEMAND_READY","journal":"MISMATCH","d5":"ENABLED_ATTESTED"}\n'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"CONFLICT_LEGACY_ARGS","journal":"FRESH","d5":"UNVERIFIED_EXE"}\n'),
        SimpleNamespace(returncode=0, stdout=b"WINDOWS_OWNER_CONTEXT_UNVERIFIED\r\n"),
        SimpleNamespace(returncode=0, stdout=b'{"error":"WINDOWS_OWNER_CONTEXT_UNVERIFIED"}\n'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"PRIVATE_LEAK","journal":"FRESH","d5":"ABSENT"}'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"DEMAND_READY","journal":"FRESH","d5":"ABSENT","extra":"secret"}'),
        SimpleNamespace(returncode=1, stdout=b'{"task":"DEMAND_READY","journal":"FRESH","d5":"ABSENT"}'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"DEMAND_READY","journal":"COMPLETE","d5":"ENABLED_ATTESTED","backup":"VALID_BUT_UNBOUND"}'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"ABSENT","journal":"FRESH","d5":"ABSENT","backup":"JOURNAL_ABSENT"}'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"ABSENT","journal":"UNVERIFIED","d5":"ABSENT","backup":"C:/PRIVATE/backup.xml"}'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"ABSENT","journal":"FRESH","d5":"ABSENT","backup":"JOURNAL_ABSENT","backup":"VALID_BUT_UNBOUND"}'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"ABSENT","journal":"FRESH","d5":"ABSENT","backup":[]}'),
        SimpleNamespace(returncode=0, stdout=b'{"task":"ABSENT","journal":"FRESH","d5":"ABSENT"}'),
    ))
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        assert "GITHUB_TOKEN" not in kwargs["env"]
        return next(replies)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ca._clean_reconcile_read(owner, target, candidates) == {
        "task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED",
    }
    assert ca._clean_reconcile_read(owner, target, candidates) == {
        "task": "DEMAND_READY", "journal": "MISMATCH", "d5": "ENABLED_ATTESTED",
    }
    assert ca._clean_reconcile_read(owner, target, candidates) == {
        "task": "CONFLICT_LEGACY_ARGS", "journal": "FRESH", "d5": "UNVERIFIED_EXE",
    }
    assert ca._clean_reconcile_read(owner, target, candidates) == {"error": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"}
    assert ca._clean_reconcile_read(owner, target, candidates) == {"error": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"}
    fallback = {"task": "UNAVAILABLE", "journal": "UNVERIFIED", "d5": "UNVERIFIED"}
    for _ in range(3):
        assert ca._clean_reconcile_read(owner, target, candidates) == fallback
    assert ca._clean_reconcile_read(owner, target, candidates, forensic=True) == {
        "task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED", "backup": "VALID_BUT_UNBOUND",
    }
    assert ca._clean_reconcile_read(owner, target, candidates, forensic=True) == {
        "task": "ABSENT", "journal": "FRESH", "d5": "ABSENT", "backup": "JOURNAL_ABSENT",
    }
    for _ in range(4):
        assert ca._clean_reconcile_read(owner, target, candidates, forensic=True) == {
            **fallback, "backup": "NO_TRUSTED_1270_CUSTODY",
        }

    args, kwargs = calls[0]
    assert args[0] == str(pwsh_7)
    assert len(subprocess.list2cmdline(args)) < 512
    assert kwargs["input"] == ca._CLEAN_9444_TASK_RECONCILE_SCRIPT.encode("utf-8")
    _, forensic_kwargs = calls[-1]
    assert forensic_kwargs["input"] == ca._CLEAN_9444_FORENSIC_READ_SCRIPT.encode("utf-8")
    assert forensic_kwargs["env"]["AI_COMMERCE_TASK_REGISTRY"] == ca._CLEAN_9444_REGISTRY
    assert kwargs["env"]["AI_COMMERCE_TASK_HISTORICAL_WORKDIR"] == str(historical)
    assert kwargs["env"]["AI_COMMERCE_TASK_LEGACY_ARGUMENTS"] != ""
    specs = json.loads(kwargs["env"]["AI_COMMERCE_TASK_TRUSTED_POWERSHELLS"])
    assert [s["path"] for s in specs] == [str(pwsh_7), str(pwsh_77)]
    assert all(len(s["demand_sha256"]) == 64 for s in specs)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell post-UNKNOWN reconciler")
@pytest.mark.parametrize("case, expected_task, expected_journal, expected_d5", [
    ("demand_ready_complete_d5_pwsh7_enabled", "DEMAND_READY", "COMPLETE", "ENABLED_ATTESTED"),
    ("demand_ready_77_complete_d5_disabled", "DEMAND_READY", "COMPLETE", "DISABLED_ATTESTED"),
    ("demand_ready_fresh_d5_absent", "DEMAND_READY", "FRESH", "ABSENT"),
    ("demand_ready_rollback_only_partial", "DEMAND_READY", "ROLLBACK_ONLY", "ENABLED_ATTESTED"),
    ("demand_ready_complete_only", "DEMAND_READY", "COMPLETE_ONLY", "ENABLED_ATTESTED"),
    ("demand_ready_corrupt_journal", "DEMAND_READY", "UNVERIFIED", "ENABLED_ATTESTED"),
    ("forensic_invalid_complete_valid_rollback", "DEMAND_READY", "UNVERIFIED", "ENABLED_ATTESTED"),
    ("forensic_standalone_xml_in_journal", "DEMAND_READY", "UNVERIFIED", "ENABLED_ATTESTED"),
    ("rollback_valid_json_invalid_xml_and_sddl", "DEMAND_READY", "UNVERIFIED", "ENABLED_ATTESTED"),
    ("rollback_json_extra_key", "DEMAND_READY", "UNVERIFIED", "ENABLED_ATTESTED"),
    ("rollback_json_duplicate_key", "DEMAND_READY", "UNVERIFIED", "ENABLED_ATTESTED"),
    ("rollback_xml_dtd_xxe", "DEMAND_READY", "UNVERIFIED", "ENABLED_ATTESTED"),
    ("rollback_wrong_sddl_owner", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_wrong_sddl_acl", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_foreign_task_uri", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_wrong_sid", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_wrong_action_args", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_wrong_action_pwsh", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_wrong_action_workdir", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_duplicate_action", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_wrong_trigger_pt5m", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("rollback_wrong_trigger_type", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("fresh_journal_unexpected_value", "DEMAND_READY", "MISMATCH", "ENABLED_ATTESTED"),
    ("legacy_pt1m_fresh_d5_pwsh7", "LEGACY_PT1M_READY", "FRESH", "ENABLED_ATTESTED"),
    ("legacy_pt1m_rollback_only_partial", "LEGACY_PT1M_READY", "ROLLBACK_ONLY", "ENABLED_ATTESTED"),
    ("legacy_pt1m_complete_mismatch", "LEGACY_PT1M_READY", "COMPLETE", "ENABLED_ATTESTED"),
    ("demand_disabled", "DEMAND_DISABLED", "COMPLETE", "ENABLED_ATTESTED"),
    ("demand_not_ready_running", "DEMAND_NOT_READY", "COMPLETE", "ENABLED_ATTESTED"),
    ("demand_runtime_unsigned", "DEMAND_RUNTIME_BAD", "COMPLETE", "ENABLED_ATTESTED"),
    ("legacy_disabled", "LEGACY_PT1M_DISABLED", "FRESH", "ENABLED_ATTESTED"),
    ("legacy_not_ready_queued", "LEGACY_PT1M_NOT_READY", "FRESH", "ENABLED_ATTESTED"),
    ("task_absent", "ABSENT", "FRESH", "ENABLED_ATTESTED"),
    ("multiple_wakehub_candidates", "MULTIPLE_CANDIDATES", "COMPLETE", "ENABLED_ATTESTED"),
    ("rogue_aicommerce_9444_candidate", "MULTIPLE_CANDIDATES", "COMPLETE", "ENABLED_ATTESTED"),
    ("benign_ef02_task_with_9444_timestamp", "DEMAND_READY", "COMPLETE", "ENABLED_ATTESTED"),
    ("malicious_trigger_two_triggers", "CONFLICT_TRIGGER_COUNT", "FRESH", "ENABLED_ATTESTED"),
    ("malicious_trigger_pt5m", "CONFLICT_LEGACY_TRIGGER_PT1M", "FRESH", "ENABLED_ATTESTED"),
    ("malicious_trigger_daily", "CONFLICT_LEGACY_TRIGGER_TYPE", "FRESH", "ENABLED_ATTESTED"),
    ("wrong_owner_sddl", "CONFLICT_BASE_OWNER", "FRESH", "ENABLED_ATTESTED"),
    ("wrong_acl_sddl", "CONFLICT_BASE_ACL", "FRESH", "ENABLED_ATTESTED"),
    ("wrong_principal_runlevel", "CONFLICT_BASE_PRINCIPAL", "FRESH", "ENABLED_ATTESTED"),
    ("wrong_action_count", "CONFLICT_BASE_ACTION", "FRESH", "ENABLED_ATTESTED"),
    ("wrong_settings_limit", "CONFLICT_BASE_SETTINGS", "FRESH", "ENABLED_ATTESTED"),
    ("foreign_moved_wakehub_task", "FOREIGN_CANDIDATE", "FRESH", "ENABLED_ATTESTED"),
    ("demand_pwsh77_direct_path", "CONFLICT_DEMAND_PWSH_PATH", "FRESH", "ENABLED_ATTESTED"),
    ("demand_untrusted_path", "CONFLICT_DEMAND_PATH", "FRESH", "ENABLED_ATTESTED"),
    ("demand_workdir_mismatch", "CONFLICT_DEMAND_WORKDIR", "FRESH", "ENABLED_ATTESTED"),
    ("demand_args_mismatch", "CONFLICT_DEMAND_ARGS", "FRESH", "ENABLED_ATTESTED"),
    ("demand_pwsh_untrusted", "CONFLICT_DEMAND_PWSH", "FRESH", "ENABLED_ATTESTED"),
    ("legacy_wrong_trigger_owner", "CONFLICT_LEGACY_TRIGGER_OWNER", "FRESH", "ENABLED_ATTESTED"),
    ("legacy_trigger_delay", "CONFLICT_LEGACY_TRIGGER_CFG", "FRESH", "ENABLED_ATTESTED"),
    ("legacy_untrusted_exe", "CONFLICT_LEGACY_PATH", "FRESH", "ENABLED_ATTESTED"),
    ("legacy_pwsh77_args_mismatch", "CONFLICT_LEGACY_ARGS", "FRESH", "ENABLED_ATTESTED"),
    ("legacy_pwsh77_workdir_mismatch", "CONFLICT_LEGACY_WORKDIR", "FRESH", "ENABLED_ATTESTED"),
    ("d5_pwsh_unverified_unsigned", "DEMAND_READY", "COMPLETE", "UNVERIFIED_EXE"),
    ("d5_untrusted_exe_powershell51", "DEMAND_READY", "COMPLETE", "UNVERIFIED_EXE"),
    ("d5_pwsh_script_9444_contamination", "DEMAND_READY", "COMPLETE", "UNVERIFIED_SCRIPT"),
    ("d5_pwsh_script_malicious_ast", "DEMAND_READY", "COMPLETE", "UNVERIFIED_SCRIPT"),
    ("d5_pwsh_script_bad_custody", "DEMAND_READY", "COMPLETE", "UNVERIFIED_CUSTODY"),
    ("d5_pwsh_bad_flags", "DEMAND_READY", "COMPLETE", "UNVERIFIED_ACTION"),
    ("d5_disabled_contaminated_acl", "DEMAND_READY", "COMPLETE", "UNVERIFIED_ACL"),
    ("d5_wrong_principal", "DEMAND_READY", "COMPLETE", "UNVERIFIED_PRINCIPAL"),
    ("d5_wrong_trigger_pt1m", "DEMAND_READY", "COMPLETE", "UNVERIFIED_TRIGGER"),
    ("d5_disabled_active_instance", "DEMAND_READY", "COMPLETE", "UNVERIFIED_STATE"),
    ("d5_multiple_candidates", "DEMAND_READY", "COMPLETE", "UNVERIFIED_MULTIPLE"),
    ("d5_moved_subfolder", "DEMAND_READY", "COMPLETE", "UNVERIFIED_MOVED"),
    ("d5_xml_contaminated", "DEMAND_READY", "COMPLETE", "UNVERIFIED_BASE"),
])
def test_clean_9444_reconcile_script_offline(case, expected_task, expected_journal, expected_d5):
    import hashlib
    real_pwsh = ca._powershell()
    historical_dir = ca._historical_legacy_workdir()
    assert historical_dir is not None and historical_dir.is_dir()
    alt_slot = "7" if real_pwsh.parent.name.casefold() == "7.7" else "7.7"
    alt_pwsh = real_pwsh.parent.parent / alt_slot / "pwsh.exe"
    demand_args_0 = "demand-args-0"
    demand_args_1 = "demand-args-1"
    specs = json.dumps([
        {
            "path": str(real_pwsh),
            "dir": str(real_pwsh.parent),
            "demand_sha256": hashlib.sha256(demand_args_0.encode("utf-8")).hexdigest(),
        },
        {
            "path": str(alt_pwsh),
            "dir": str(alt_pwsh.parent),
            "demand_sha256": hashlib.sha256(demand_args_1.encode("utf-8")).hexdigest(),
        },
    ])
    raw_root = None
    for _ in range(8):
        candidate = tempfile.mkdtemp(prefix="d5_reconcile_")
        resolved_candidate = str(Path(candidate).resolve())
        if not re.search(r"(?<![0-9])9444(?![0-9])", resolved_candidate):
            raw_root = candidate
            break
        shutil.rmtree(candidate, ignore_errors=True)
    assert raw_root is not None
    d5_root = Path(raw_root).resolve() / "d5-launcher"
    d5_dir = d5_root / "scripts"
    d5_script = d5_dir / "start-d5-cdp.ps1"
    good_script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'\n"
        "$profileDir = Join-Path $env:LOCALAPPDATA 'AI-Commerce\\D5-Chrome-9333'\n"
        "$existing = Get-NetTCPConnection -LocalPort 9333 -State Listen -ErrorAction SilentlyContinue\n"
        "if ($existing) { exit 0 }\n"
        "Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333',"
        "'--remote-debugging-address=127.0.0.1',\"--user-data-dir=$profileDir\",'--profile-directory=Default')\n"
    )
    bad_script = "# mentions 9444\n" + good_script
    malicious_script = "Invoke-WebRequest 'https://example.invalid'\n" + good_script
    try:
        d5_dir.mkdir(parents=True)
        script_body = (
            bad_script if case == "d5_pwsh_script_9444_contamination"
            else malicious_script if case == "d5_pwsh_script_malicious_ast"
            else good_script
        )
        d5_script.write_text(script_body, encoding="utf-8")
        functions = ca._CLEAN_9444_TASK_RECONCILE_SCRIPT.split("\nfunction Read-Registration", 1)[1]
        seam = "\n$registered = Read-Registration"
        fixture = r"""
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$session = [Diagnostics.Process]::GetCurrentProcess().SessionId
$sddl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')'
$action = [pscustomobject]@{
    Type = 0; Path = 'C:\Owner\pythonw.exe'; Arguments = 'demand-args-0'; WorkingDirectory = 'C:\Owner'
}
$expected = [pscustomobject]@{
    Principal = [pscustomobject]@{ Id = 'WakeHubOwner'; UserId = $sid; LogonType = 3; RunLevel = 0; GroupId = $null }
    Settings = [pscustomobject]@{
        AllowDemandStart = $true; AllowHardTerminate = $true; DisallowStartIfOnBatteries = $false
        Enabled = $true; ExecutionTimeLimit = 'PT45S'; Hidden = $false; MultipleInstances = 2
        RestartCount = 0; RestartInterval = $null; RunOnlyIfIdle = $false
        RunOnlyIfNetworkAvailable = $false; StartWhenAvailable = $false
        StopIfGoingOnBatteries = $false; WakeToRun = $false; DeleteExpiredTaskAfter = $null
    }
    Actions = [pscustomobject]@{ Context = 'WakeHubOwner'; Entry = $action }
    Triggers = [pscustomobject]@{ Count = 0 }
}
$localRoot = [Environment]::GetFolderPath('LocalApplicationData')
$aiRoot = Join-Path $localRoot 'AI-Commerce'
$cleanProfile = Join-Path $aiRoot 'WakeHub-9444-Clean'
$d5Profile = Join-Path $aiRoot 'D5-Chrome-9333'
$chromePath = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$historicalWorkDir = $env:TEST_HISTORICAL_WORKDIR
$env:AI_COMMERCE_TASK_PROFILE_DIR = $cleanProfile
$env:AI_COMMERCE_TASK_CHROME_EXECUTABLES = (@($chromePath) | ConvertTo-Json -Compress)
$env:AI_COMMERCE_TASK_LEGACY_ARGUMENTS = 'legacy'
$trustedPowerShellSpecs = @($env:AI_COMMERCE_TASK_TRUSTED_POWERSHELLS | ConvertFrom-Json)
$trustedPowerShellPaths = @($trustedPowerShellSpecs | ForEach-Object { [string]$_.path })
$trustedPowerShellDirs = @($trustedPowerShellSpecs | ForEach-Object { [string]$_.dir })
$installOwnerSids = @(
    'S-1-5-18', 'S-1-5-32-544',
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
)
$name = 'AI-Commerce-WakeHub-9444-Clean-' + $sid
$script:d5ScriptPath = $env:TEST_D5_SCRIPT_PATH
$script:d5RootDir = $env:TEST_D5_ROOT_DIR
$script:writes = 0
$script:regWrites = 0
$script:runCalls = 0

$ownerRule = [Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'Allow')
$sysRule = [Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-5-18'), 'FullControl', 'Allow')
$admRule = [Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'), 'FullControl', 'Allow')
function Set-ProtectedAcl($targetPath, $isFile) {
    $sec = if ($isFile) { [Security.AccessControl.FileSecurity]::new() }
           else { [Security.AccessControl.DirectorySecurity]::new() }
    $sec.SetAccessRuleProtection($true, $false)
    $sec.AddAccessRule($ownerRule)
    $sec.AddAccessRule($sysRule)
    $sec.AddAccessRule($admRule)
    if ($isFile) { [IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($targetPath), $sec) }
    else { [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($targetPath), $sec) }
}
Set-ProtectedAcl $script:d5RootDir $false
Set-ProtectedAcl ([IO.Path]::GetDirectoryName($script:d5ScriptPath)) $false
Set-ProtectedAcl $script:d5ScriptPath $true

function New-LegacyRollbackXml(
    $uri = ('\' + $name),
    $ownerSid = $sid,
    $cmdPath = $trustedPowerShellPaths[0],
    $cmdArgs = 'legacy',
    $workDir = $trustedPowerShellDirs[0],
    $interval = 'PT1M',
    $triggerTag = 'LogonTrigger',
    $extraAction = ''
) {
    $esc = { param($v) [Security.SecurityElement]::Escape([string]$v) }
    $triggerXml = if ($triggerTag -ceq 'LogonTrigger') {
        "<LogonTrigger><Repetition><Interval>$(& $esc $interval)</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><ExecutionTimeLimit>PT72H</ExecutionTimeLimit><Enabled>true</Enabled><UserId>$(& $esc $ownerSid)</UserId></LogonTrigger>"
    } else {
        "<$triggerTag><Repetition><Interval>$(& $esc $interval)</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><Enabled>true</Enabled><StartBoundary>2026-11-21T00:00:00</StartBoundary></$triggerTag>"
    }
    return '<?xml version="1.0" encoding="UTF-16"?>' +
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">' +
        "<RegistrationInfo><Date>2026-11-20T10:00:00</Date><URI>$(& $esc $uri)</URI></RegistrationInfo>" +
        "<Triggers>$triggerXml</Triggers>" +
        "<Principals><Principal id=`"WakeHubOwner`"><UserId>$(& $esc $ownerSid)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>" +
        '<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>false</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun><ExecutionTimeLimit>PT45S</ExecutionTimeLimit><Priority>7</Priority></Settings>' +
        "<Actions Context=`"WakeHubOwner`"><Exec><Command>$(& $esc $cmdPath)</Command><Arguments>$(& $esc $cmdArgs)</Arguments><WorkingDirectory>$(& $esc $workDir)</WorkingDirectory></Exec>$extraAction</Actions>" +
        '</Task>'
}

function New-FakeWakeTask($demand, $argsText = 'demand-args-0', $enabled = $true, $state = 3, $instances = 0,
                          $legacyPath = $trustedPowerShellPaths[0], $legacyDir = $trustedPowerShellDirs[0],
                          $trigCount = $null, $trigType = 9, $interval = 'PT1M', $taskName = $name) {
    $t = [pscustomobject]@{ Type = $trigType; Enabled = $true; UserId = $sid
        Repetition = [pscustomobject]@{ Interval = $interval } }
    $a = if ($demand) {
        [pscustomobject]@{ Type = 0; Path = $action.Path; Arguments = $argsText; WorkingDirectory = $action.WorkingDirectory }
    } else {
        [pscustomobject]@{ Type = 0; Path = $legacyPath; Arguments = 'legacy'; WorkingDirectory = $legacyDir }
    }
    $actions = [pscustomobject]@{ Count = 1; Context = 'WakeHubOwner'; Entry = $a }
    $actions | Add-Member ScriptMethod Item { $this.Entry }
    $tc = if ($null -ne $trigCount) { $trigCount } else { [int](!$demand) }
    $triggers = [pscustomobject]@{ Count = $tc; Entry = $t }
    $triggers | Add-Member ScriptMethod Item { $this.Entry }
    $settings = $expected.Settings.PSObject.Copy()
    $settings.Enabled = $enabled
    $task = [pscustomobject]@{ Name = $taskName; Enabled = $enabled; State = $state; Instances = $instances
        Xml = '<Task><URI>\' + $taskName + '</URI></Task>'; Sddl = $sddl
        Definition = [pscustomobject]@{ Principal = $expected.Principal.PSObject.Copy()
            Settings = $settings; Actions = $actions; Triggers = $triggers } }
    $task | Add-Member ScriptMethod GetSecurityDescriptor { $this.Sddl }
    $task | Add-Member ScriptMethod GetInstances { [pscustomobject]@{ Count = $this.Instances } }
    $task | Add-Member ScriptMethod RunEx { $script:runCalls++; throw 'Never run task' }
    return $task
}

function New-FakeD5Task($enabled = $true, $state = 3, $exePath = $chromePath, $argsOverride = $null,
                        $workDir = $null, $d5Sddl = $sddl, $instances = 0, $taskName = 'AI-Commerce-D5-CDP-AtLogon') {
    $t = [pscustomobject]@{ Type = 9; Enabled = $enabled; UserId = $sid
        Repetition = [pscustomobject]@{ Interval = $null; Duration = $null } }
    $argsText = if ($null -ne $argsOverride) { $argsOverride } else {
        '--remote-debugging-port=9333 --remote-debugging-address=127.0.0.1 ' +
        '--user-data-dir="' + $d5Profile + '" --profile-directory=Default --no-first-run'
    }
    $a = [pscustomobject]@{ Type = 0; Path = $exePath; Arguments = $argsText; WorkingDirectory = $workDir }
    $actions = [pscustomobject]@{ Count = 1; Context = 'D5Owner'; Entry = $a }
    $actions | Add-Member ScriptMethod Item { $this.Entry }
    $triggers = [pscustomobject]@{ Count = 1; Entry = $t }
    $triggers | Add-Member ScriptMethod Item { $this.Entry }
    $principal = [pscustomobject]@{ Id = 'D5Owner'; UserId = $sid; LogonType = 3; RunLevel = 0; GroupId = $null }
    $task = [pscustomobject]@{ Name = $taskName; Enabled = $enabled; State = $state; Instances = $instances
        Xml = '<Task><RegistrationInfo><URI>\' + $taskName + '</URI></RegistrationInfo></Task>'
        Sddl = $d5Sddl; Definition = [pscustomobject]@{ Principal = $principal
            Settings = [pscustomobject]@{}; Actions = $actions; Triggers = $triggers } }
    $task | Add-Member ScriptMethod GetSecurityDescriptor { $this.Sddl }
    $task | Add-Member ScriptMethod GetInstances { [pscustomobject]@{ Count = $this.Instances } }
    return $task
}

$pwshFileArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script:d5ScriptPath + '"'
$validLegacyXml = New-LegacyRollbackXml
$validRollback = (@{ Xml = $validLegacyXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
$script:current = New-FakeWakeTask $true 'demand-args-0'
$script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] -argsOverride $pwshFileArgs
$script:extraTasks = @()
$script:subFolderTasks = @()
$script:values = @{ DemandMigrationRollback = $validRollback; DemandMigrationComplete = 1 }

switch ($case) {
    'demand_ready_77_complete_d5_disabled' {
        $script:current = New-FakeWakeTask $true 'demand-args-1'
        $script:d5 = New-FakeD5Task -enabled $false -state 1
        $legacy77Xml = New-LegacyRollbackXml -cmdPath $trustedPowerShellPaths[1] -workDir $historicalWorkDir
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $legacy77Xml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'demand_ready_fresh_d5_absent' {
        $script:values = @{}
        $script:d5 = $null
    }
    'demand_ready_rollback_only_partial' {
        $script:values = @{ DemandMigrationRollback = $validRollback }
    }
    'demand_ready_complete_only' {
        $script:values = @{ DemandMigrationComplete = 1 }
    }
    'demand_ready_corrupt_journal' {
        $script:values = @{ DemandMigrationRollback = 'not-json'; DemandMigrationComplete = 1 }
    }
    'forensic_invalid_complete_valid_rollback' {
        $script:values = @{ DemandMigrationRollback = $validRollback; DemandMigrationComplete = '1' }
    }
    'forensic_standalone_xml_in_journal' {
        $script:values = @{ DemandMigrationRollback = $validLegacyXml; DemandMigrationComplete = 1 }
    }
    'rollback_valid_json_invalid_xml_and_sddl' {
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = 'invalid-task-xml'; SecurityDescriptor = 'invalid-security-descriptor' } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_json_extra_key' {
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $validLegacyXml; SecurityDescriptor = $sddl; Extra = 'x' } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_json_duplicate_key' {
        $escapedXml = ($validLegacyXml | ConvertTo-Json -Compress)
        $escapedAcl = ($sddl | ConvertTo-Json -Compress)
        $script:values = @{
            DemandMigrationRollback = '{"Xml":"bad","Xml":' + $escapedXml + ',"SecurityDescriptor":' + $escapedAcl + '}'
            DemandMigrationComplete = 1
        }
    }
    'rollback_xml_dtd_xxe' {
        $dtdXml = '<?xml version="1.0"?><!DOCTYPE task [<!ENTITY xxe "bad">]>' + ($validLegacyXml -replace '^<\?xml[^?]*\?>', '')
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $dtdXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_wrong_sddl_owner' {
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $validLegacyXml; SecurityDescriptor = $sddl.Replace('O:' + $sid, 'O:SY') } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_wrong_sddl_acl' {
        $badAcl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')(A;;GW;;;AU)'
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $validLegacyXml; SecurityDescriptor = $badAcl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_foreign_task_uri' {
        $foreignXml = New-LegacyRollbackXml -uri '\AI-Commerce-D5-CDP-AtLogon'
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $foreignXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_wrong_sid' {
        $wrongSidXml = New-LegacyRollbackXml -ownerSid 'S-1-5-21-999-999-999-1001'
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $wrongSidXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_wrong_action_args' {
        $wrongArgsXml = New-LegacyRollbackXml -cmdArgs 'other-args'
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $wrongArgsXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_wrong_action_pwsh' {
        $wrongCmdXml = New-LegacyRollbackXml -cmdPath 'C:\Windows\System32\cmd.exe'
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $wrongCmdXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_wrong_action_workdir' {
        $wrongDirXml = New-LegacyRollbackXml -workDir 'C:\Temp'
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $wrongDirXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_duplicate_action' {
        $dupExec = '<Exec><Command>C:\Windows\System32\cmd.exe</Command><Arguments>/c echo hi</Arguments><WorkingDirectory>C:\Windows</WorkingDirectory></Exec>'
        $dupXml = New-LegacyRollbackXml -extraAction $dupExec
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $dupXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_wrong_trigger_pt5m' {
        $pt5mXml = New-LegacyRollbackXml -interval 'PT5M'
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $pt5mXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'rollback_wrong_trigger_type' {
        $timeTrigXml = New-LegacyRollbackXml -triggerTag 'TimeTrigger'
        $script:values = @{
            DemandMigrationRollback = (@{ Xml = $timeTrigXml; SecurityDescriptor = $sddl } | ConvertTo-Json -Compress)
            DemandMigrationComplete = 1
        }
    }
    'fresh_journal_unexpected_value' {
        $script:values = @{ UnexpectedJournalValue = 1 }
    }
    'legacy_pt1m_fresh_d5_pwsh7' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[1] $historicalWorkDir
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[1] -argsOverride $pwshFileArgs -workDir $historicalWorkDir
        $script:values = @{}
    }
    'legacy_pt1m_rollback_only_partial' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[1] $historicalWorkDir
        $script:values = @{ DemandMigrationRollback = $validRollback }
    }
    'legacy_pt1m_complete_mismatch' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0]
    }
    'demand_disabled' {
        $script:current = New-FakeWakeTask $true 'demand-args-0' $false 1 0
    }
    'demand_not_ready_running' {
        $script:current = New-FakeWakeTask $true 'demand-args-0' $true 4 1
    }
    'demand_runtime_unsigned' { }
    'legacy_disabled' {
        $script:current = New-FakeWakeTask $false '' $false 1 0 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0]
        $script:values = @{}
    }
    'legacy_not_ready_queued' {
        $script:current = New-FakeWakeTask $false '' $true 3 1 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0]
        $script:values = @{}
    }
    'task_absent' {
        $script:current = $null
        $script:values = @{}
    }
    'multiple_wakehub_candidates' {
        $script:extraTasks = @(New-FakeWakeTask $true 'demand-args-0' $true 3 0 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0] $null 9 'PT1M' 'AI-Commerce-WakeHub-9444-Clean-S-1-5-21-999')
    }
    'rogue_aicommerce_9444_candidate' {
        $rogue = [pscustomobject]@{
            Name = 'AI-Commerce-Rogue'
            Xml = '<Task><Actions><Exec><Arguments>--remote-debugging-port=9444</Arguments></Exec></Actions></Task>'
            Definition = [pscustomobject]@{ Actions = @([pscustomobject]@{ Type = 0; Arguments = '--remote-debugging-port=9444' }) }
        }
        $script:extraTasks = @($rogue)
    }
    'benign_ef02_task_with_9444_timestamp' {
        $ef02 = [pscustomobject]@{
            Name = 'AI-Commerce-EF02-Interactive'
            Xml = '<Task><RegistrationInfo><Date>2026-11-21T10:00:00.9444</Date></RegistrationInfo></Task>'
            Definition = [pscustomobject]@{ Actions = @([pscustomobject]@{ Type = 0; Arguments = 'C:\TOOL\ai-commerce-runner\EF02-Hidden-Runner.vbs' }) }
        }
        $script:extraTasks = @($ef02)
    }
    'malicious_trigger_two_triggers' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0] 2
        $script:values = @{}
    }
    'malicious_trigger_pt5m' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0] 1 9 'PT5M'
        $script:values = @{}
    }
    'malicious_trigger_daily' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0] 1 2 'PT1M'
        $script:values = @{}
    }
    'wrong_owner_sddl' {
        $script:current = New-FakeWakeTask $true 'demand-args-0'
        $script:current.Sddl = $sddl.Replace('O:' + $sid, 'O:SY')
        $script:values = @{}
    }
    'wrong_acl_sddl' {
        $script:current = New-FakeWakeTask $true 'demand-args-0'
        $script:current.Sddl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')(A;;GW;;;AU)'
        $script:values = @{}
    }
    'wrong_principal_runlevel' {
        $script:current = New-FakeWakeTask $true 'demand-args-0'
        $script:current.Definition.Principal.RunLevel = 1
        $script:values = @{}
    }
    'wrong_action_count' {
        $script:current = New-FakeWakeTask $true 'demand-args-0'
        $script:current.Definition.Actions.Count = 2
        $script:values = @{}
    }
    'wrong_settings_limit' {
        $script:current = New-FakeWakeTask $true 'demand-args-0'
        $script:current.Definition.Settings.ExecutionTimeLimit = 'PT72H'
        $script:values = @{}
    }
    'foreign_moved_wakehub_task' {
        $script:current = $null
        $script:subFolderTasks = @(New-FakeWakeTask $true 'demand-args-0')
        $script:values = @{}
    }
    'demand_pwsh77_direct_path' {
        $script:current = New-FakeWakeTask $true 'demand-args-0'
        $script:current.Definition.Actions.Entry.Path = $trustedPowerShellPaths[1]
        $script:values = @{}
    }
    'demand_untrusted_path' {
        $script:current = New-FakeWakeTask $true 'demand-args-0'
        $script:current.Definition.Actions.Entry.Path = 'C:\Untrusted\pythonw.exe'
        $script:values = @{}
    }
    'demand_workdir_mismatch' {
        $script:current = New-FakeWakeTask $true 'demand-args-0'
        $script:current.Definition.Actions.Entry.WorkingDirectory = 'C:\Temp'
        $script:values = @{}
    }
    'demand_args_mismatch' {
        $script:current = New-FakeWakeTask $true 'modified-demand-args'
        $script:values = @{}
    }
    'demand_pwsh_untrusted' {
        $script:current = New-FakeWakeTask $true 'demand-args-1'
        $script:values = @{}
    }
    'legacy_wrong_trigger_owner' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0]
        $script:current.Definition.Triggers.Entry.UserId = 'S-1-5-21-999-999-999-1001'
        $script:values = @{}
    }
    'legacy_trigger_delay' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[0] $trustedPowerShellDirs[0]
        $script:current.Definition.Triggers.Entry | Add-Member NoteProperty Delay 'PT10S'
        $script:values = @{}
    }
    'legacy_untrusted_exe' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' $historicalWorkDir
        $script:values = @{}
    }
    'legacy_pwsh77_args_mismatch' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[1] $trustedPowerShellDirs[1]
        $script:current.Definition.Actions.Entry.Arguments = '-NoProfile -NonInteractive -Command "drifted"'
        $script:values = @{}
    }
    'legacy_pwsh77_workdir_mismatch' {
        $script:current = New-FakeWakeTask $false '' $true 3 0 $trustedPowerShellPaths[1] 'C:\Temp'
        $script:values = @{}
    }
    'd5_pwsh_unverified_unsigned' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[1] -argsOverride $pwshFileArgs
    }
    'd5_untrusted_exe_powershell51' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -argsOverride $pwshFileArgs
    }
    'd5_pwsh_script_bad_custody' {
        $sec = [IO.FileSystemAclExtensions]::GetAccessControl([IO.FileInfo]::new($script:d5ScriptPath))
        $sec.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new('S-1-1-0'), 'Modify', 'Allow'))
        [IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($script:d5ScriptPath), $sec)
    }
    'd5_pwsh_bad_flags' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] -argsOverride ('-UnknownFlag -File "' + $script:d5ScriptPath + '"')
    }
    'd5_disabled_contaminated_acl' {
        $badAcl = 'O:' + $sid + 'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;' + $sid + ')(A;;GW;;;AU)'
        $script:d5 = New-FakeD5Task -enabled $false -state 1 -d5Sddl $badAcl
    }
    'd5_wrong_principal' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] -argsOverride $pwshFileArgs
        $script:d5.Definition.Principal.RunLevel = 1
    }
    'd5_wrong_trigger_pt1m' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] -argsOverride $pwshFileArgs
        $script:d5.Definition.Triggers.Entry.Repetition.Interval = 'PT1M'
    }
    'd5_disabled_active_instance' {
        $script:d5 = New-FakeD5Task -enabled $false -state 1 -instances 1
    }
    'd5_multiple_candidates' {
        $script:extraTasks = @(New-FakeD5Task -enabled $true -state 3 -taskName 'AI-Commerce-D5-CDP-Backup')
    }
    'd5_moved_subfolder' {
        $script:d5 = $null
        $script:subFolderTasks = @(New-FakeD5Task -enabled $true -state 3)
    }
    'd5_xml_contaminated' {
        $script:d5 = New-FakeD5Task -enabled $true -state 3 -exePath $trustedPowerShellPaths[0] -argsOverride $pwshFileArgs
        $script:d5.Xml = '<Task><Description>WakeHub-9444-Clean</Description></Task>'
    }
}

$registry = [pscustomobject]@{}
$registry | Add-Member ScriptMethod GetValueNames { $script:values.Keys }
$registry | Add-Member ScriptMethod GetValue { param($k) $script:values[$k] }
$registry | Add-Member ScriptMethod SetValue { $script:regWrites++; throw 'Never write registry' }
$registry | Add-Member ScriptMethod DeleteValue { $script:regWrites++; throw 'Never delete registry' }
$registry | Add-Member ScriptMethod Dispose {}

$folder = [pscustomobject]@{}
$folder | Add-Member ScriptMethod GetTask {
    param($taskName)
    if ($taskName -ceq 'AI-Commerce-D5-CDP-AtLogon') { return $script:d5 }
    if ($taskName -ceq $name) { return $script:current }
    return $null
}
$folder | Add-Member ScriptMethod GetTasks {
    param($flags)
    $list = @()
    if ($script:current) { $list += $script:current }
    if ($script:d5) { $list += $script:d5 }
    $list += $script:extraTasks
    return ,$list
}
$subFolder = [pscustomobject]@{ Name = 'AI-Commerce' }
$subFolder | Add-Member ScriptMethod GetTasks { param($flags) return ,$script:subFolderTasks }
$folder | Add-Member ScriptMethod GetFolders {
    param($flags)
    if ($script:subFolderTasks.Count) { return ,@($subFolder) }
    return ,@()
}
$folder | Add-Member ScriptMethod RegisterTaskDefinition { $script:writes++; throw 'Never mutate task' }
"""
        overrides = r"""
function Test-DemandRuntime { return $case -ne 'demand_runtime_unsigned' }
function Open-MigrationState($write) {
    if ($write) { $script:regWrites++; throw 'Must open read-only' }
    return $registry
}
$realSigned = (Get-Item function:Test-SignedExecutable).ScriptBlock
function Test-SignedExecutable($path, $publisher) {
    if ($path -ieq $chromePath -and $publisher -match 'Google') { return $true }
    if ($path -ieq $trustedPowerShellPaths[1] -and $publisher -match 'Microsoft') {
        return $case -notin @('d5_pwsh_unverified_unsigned', 'demand_pwsh_untrusted')
    }
    return & $realSigned $path $publisher
}
$realTrusted = (Get-Item function:Test-TrustedPowerShell).ScriptBlock
function Test-TrustedPowerShell($path) {
    if ($path -ieq $trustedPowerShellPaths[1]) {
        return ($case -notin @('d5_pwsh_unverified_unsigned', 'demand_pwsh_untrusted')) -and ($path -in $trustedPowerShellPaths)
    }
    return & $realTrusted $path
}
$realCustody = (Get-Item function:Get-AttestedPathCustodySddl).ScriptBlock
function Get-AttestedPathCustodySddl($path, $allowedOwners, $expectFile = $false) {
    $full = [IO.Path]::GetFullPath($path).TrimEnd([char]92)
    $rootPrefix = [IO.Path]::GetFullPath($script:d5RootDir).TrimEnd([char]92)
    if ($full -ieq $rootPrefix -or $full.StartsWith($rootPrefix + '\', [StringComparison]::OrdinalIgnoreCase)) {
        $parts = [Collections.Generic.Stack[string]]::new()
        for ($cur = $full; $cur; $cur = [IO.Path]::GetDirectoryName($cur)) {
            $parts.Push($cur)
            if ($cur -ieq $rootPrefix) { break }
        }
        $sections = [Security.AccessControl.AccessControlSections]::Owner -bor
            [Security.AccessControl.AccessControlSections]::Access
        $aceOwners = @($allowedOwners) + @('S-1-3-4')
        $leafSddl = $null
        foreach ($part in $parts) {
            $entry = Get-Item -LiteralPath $part -Force -ErrorAction Stop
            if (!$entry -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $null }
            $isLeaf = $part -ieq $full
            $aclObj = if ($isLeaf -and $expectFile) {
                [IO.FileSystemAclExtensions]::GetAccessControl([IO.FileInfo]$entry, $sections)
            } else {
                [IO.FileSystemAclExtensions]::GetAccessControl([IO.DirectoryInfo]$entry, $sections)
            }
            $partSddl = $aclObj.GetSecurityDescriptorSddlForm('Owner, Access')
            $sd = [Security.AccessControl.RawSecurityDescriptor]::new($partSddl)
            if (!$sd.Owner -or $sd.Owner.Value -cnotin $allowedOwners -or !$sd.DiscretionaryAcl) { return $null }
            foreach ($ace in $sd.DiscretionaryAcl) {
                if ($ace -isnot [Security.AccessControl.CommonAce]) { return $null }
                if ($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly) { continue }
                if ($ace.AceType -eq [Security.AccessControl.AceType]::AccessDenied) { continue }
                if ($ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed) { return $null }
                if (($ace.AccessMask -band 0x500D0156) -and $ace.SecurityIdentifier.Value -cnotin $aceOwners) {
                    return $null
                }
            }
            if ($isLeaf) { $leafSddl = $partSddl }
        }
        return $leafSddl
    }
    return & $realCustody $path $allowedOwners $expectFile
}
"""
        functions = functions.replace(seam, overrides + seam, 1)
        env = ca._child_environment()
        env.update(
            AI_COMMERCE_TASK_TRUSTED_POWERSHELLS=specs,
            AI_COMMERCE_TASK_HISTORICAL_WORKDIR=str(historical_dir),
            TEST_HISTORICAL_WORKDIR=str(historical_dir),
            TEST_D5_SCRIPT_PATH=str(d5_script),
            TEST_D5_ROOT_DIR=str(d5_root),
        )
        forensic_backups = {
            "demand_ready_complete_d5_pwsh7_enabled": "VALID_BUT_UNBOUND",
            "demand_ready_rollback_only_partial": "VALID_BUT_UNBOUND",
            "demand_ready_fresh_d5_absent": "JOURNAL_ABSENT",
            "demand_ready_complete_only": "NO_TRUSTED_1270_CUSTODY",
            "forensic_invalid_complete_valid_rollback": "VALID_BUT_UNBOUND",
            "forensic_standalone_xml_in_journal": "NO_TRUSTED_1270_CUSTODY",
            "rollback_wrong_sddl_owner": "NO_TRUSTED_1270_CUSTODY",
            "rollback_wrong_sddl_acl": "NO_TRUSTED_1270_CUSTODY",
            "rollback_xml_dtd_xxe": "NO_TRUSTED_1270_CUSTODY",
            "multiple_wakehub_candidates": "VALID_BUT_UNBOUND",
            "foreign_moved_wakehub_task": "JOURNAL_ABSENT",
            "d5_pwsh_unverified_unsigned": "VALID_BUT_UNBOUND",
        }
        forensic_prefix = ""
        if case in forensic_backups:
            forensic_prefix = ca._CLEAN_9444_FORENSIC_READ_SCRIPT.removesuffix(ca._CLEAN_9444_TASK_RECONCILE_SCRIPT)
        harness_script = (
            forensic_prefix + f"$case = '{case}'\n" + fixture
            + "\n$outJson = & {\nfunction Read-Registration" + functions.replace("; exit", "; return")
            + "\n}\n"
            + "@{ out = ($outJson | ConvertFrom-Json); writes = $script:writes; regWrites = $script:regWrites;"
              " runCalls = $script:runCalls } | ConvertTo-Json -Compress -Depth 5\n"
        )
        stdin_runner = (
            "[Console]::InputEncoding=[Text.UTF8Encoding]::new(); "
            "& ([ScriptBlock]::Create([Console]::In.ReadToEnd()))"
        )
        result = subprocess.run(
            [str(real_pwsh), *ca._CLEAN_POWERSHELL_FLAGS, stdin_runner],
            input=harness_script, capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
        )
    finally:
        shutil.rmtree(raw_root, ignore_errors=True)
    assert result.returncode == 0 and not result.stderr, result.stderr
    observed = json.loads(result.stdout)
    expected = {"task": expected_task, "journal": expected_journal, "d5": expected_d5}
    if case in forensic_backups:
        expected["backup"] = forensic_backups[case]
    assert observed == {
        "out": expected,
        "writes": 0, "regWrites": 0, "runCalls": 0,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell launcher")
def test_clean_9444_launcher_profile_check_offline():
    checks = ca._CLEAN_9444_LAUNCHER.split("\nfunction Get-OwnerSid", 1)[0]
    command = "if ($PSVersionTable.PSVersion.Major -lt 7) { exit 7 }\n" + checks + r"""
$local = [Environment]::GetFolderPath('LocalApplicationData')
$root = Join-Path $local 'AI-Commerce'
$profileDir = Join-Path $root 'WakeHub-9444-Clean'
$defaultDir = Join-Path $profileDir 'Default'
function Get-Item($LiteralPath, [switch]$Force) {
    $script:pathsRead += $LiteralPath
    if ($case -eq 'junction' -and $LiteralPath -eq $root) {
        return [pscustomobject]@{ Attributes = [IO.FileAttributes]::ReparsePoint }
    }
    if ($LiteralPath -eq $defaultDir) {
        if ($case -eq 'missing') { throw 'private path' }
        if ($case -eq 'file') { return [IO.FileInfo]::new($LiteralPath) }
    }
    [IO.DirectoryInfo]::new($PSHOME)
}
function Get-ChildItem($LiteralPath, [switch]$Force) {
    if ($case -eq 'inaccessible' -and $LiteralPath -eq $defaultDir) { throw 'private denial' }
}
function Get-Acl { throw 'profile must use ordinary access' }
foreach ($case in @('accessible', 'missing', 'file', 'inaccessible', 'junction')) {
    $script:pathsRead = @()
    try { Assert-CleanProfile; 'ACCESSIBLE' }
    catch { $_.Exception.Message }
    if ($case -eq 'junction' -and $script:pathsRead[-1] -ne $root) { throw 'traversed junction' }
}
"""
    result = subprocess.run([str(ca._powershell()), *ca._CLEAN_POWERSHELL_FLAGS, command],
                            capture_output=True, text=True, encoding="ascii", timeout=15)
    assert result.returncode == 0 and not result.stderr
    assert result.stdout.splitlines() == ["ACCESSIBLE", *["PROFILE_UNAVAILABLE"] * 4]


_CLEAN_9444_OFFLINE_WINDOWS = r"""
$script:started = 0
$script:probed = 0
$script:pending = $case -eq 'pending'
$script:active = $case -in @('reuse', 'bad_cdp', 'wrong_session', 'wrong_owner',
    'wrong_profile', 'wrong_binary', 'duplicate', 'claimed', 'mixed')
$stub = [pscustomobject]@{}
$stub | Add-Member ScriptMethod Dispose {}
$stub | Add-Member ScriptMethod GetValueNames { if ($script:pending) { 'LaunchPending' } }
$stub | Add-Member ScriptMethod SetValue { $script:pending = $true }
$stub | Add-Member ScriptMethod DeleteValue { $script:pending = $false }
$stub | Add-Member ScriptMethod Flush {}
function Open-LauncherSlot {
    if ($case -eq 'busy') { throw 'SUPERVISOR_ACTIVE' }
    $stub
}
function Open-LaunchState($create) { $stub }
function Assert-OwnerSession {
    if ($case -eq 'no_desktop') { throw 'WINDOWS_INTERACTIVE_REQUIRED' }
    7
}
function Assert-CleanProfile {
    if ($case -eq 'profile_unavailable') { throw 'PROFILE_UNAVAILABLE' }
}
function Assert-UnlockedProfile {
    if ($case -eq 'locked') { throw 'PROFILE_LOCKED' }
}
function Assert-SignedChrome($path) {
    if ($case -eq 'unsigned') { throw 'CHROME_EXECUTABLE_UNTRUSTED' }
}
function Get-OwnerSid($process) {
    if ($case -eq 'wrong_owner') { 'S-1-5-21-9-9-9-1001' } else { $sid }
}
function Test-PortListening { $script:active }
function Get-NetTCPConnection($State) {
    if ($script:active -and $case -ne 'claimed') {
        [pscustomobject]@{ LocalPort = 9444; LocalAddress = '127.0.0.1'; OwningProcess = 500 }
    }
    if ($case -eq 'foreign') {
        [pscustomobject]@{ LocalPort = 9444; LocalAddress = '127.0.0.1'; OwningProcess = 999 }
    }
    if ($case -eq 'mixed') {
        [pscustomobject]@{ LocalPort = 9444; LocalAddress = '::1'; OwningProcess = 500 }
    }
}
function Get-CimInstance($ClassName, $Filter, $OperationTimeoutSec) {
    if (!$script:active) { return }
    $command = [string][char]34 + $executables[0] + [char]34 + ' ' + $chromeArguments
    if ($case -eq 'reuse') {
        $command = 'chrome.exe --user-data-dir=' + [char]34 + $profileDir + [char]34 + ' --remote-debugging-port=9444'
    }
    if ($case -eq 'wrong_profile') { $command = $command.Replace('--profile-directory=Default', '--profile-directory=Other') }
    $ownerSession = if ($case -eq 'wrong_session') { 8 } else { 7 }
    $path = if ($case -eq 'wrong_binary') { 'C:\Unowned\chrome.exe' } else { $executables[0] }
    [pscustomobject]@{ ProcessId = 500; ExecutablePath = $path; SessionId = $ownerSession
        CreationDate = [DateTime]'2026-11-18T01:00:00Z'; CommandLine = $command }
    if ($case -eq 'reuse') {
        [pscustomobject]@{ ProcessId = 501; ExecutablePath = $executables[0]; SessionId = 7
            CreationDate = [DateTime]'2026-11-18T01:00:00Z'; CommandLine = $command + ' --type=renderer' }
    }
    if ($case -eq 'duplicate') {
        [pscustomobject]@{ ProcessId = 502; ExecutablePath = $executables[0]; SessionId = 7
            CreationDate = [DateTime]'2026-11-18T01:00:00Z'; CommandLine = $command }
    }
}
function Start-CleanChrome {
    if (!$script:pending) { throw 'fixture: launched before durable reservation' }
    $script:started++
    if ($case -eq 'failed_spawn') { throw 'private native detail' }
    $script:active = $true
    $pidSeen = if ($case -eq 'different_pid') { 501 } else { 500 }
    [pscustomobject]@{ Id = $pidSeen; HasExited = $false }
}
function Read-DevToolsVersion($milliseconds) {
    $script:probed++
    $hostName = if ($case -in @('bad_cdp', 'bad_start_cdp')) { 'example.invalid' } else { '127.0.0.1' }
    [pscustomobject]@{ Browser = 'Chrome/123'; 'Protocol-Version' = '1.3'
        webSocketDebuggerUrl = 'ws://' + $hostName + ':9444/devtools/browser/test' }
}
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell launcher")
@pytest.mark.parametrize("case, code, starts, probes, pending", [
    ("start", "STARTED", 1, 1, False),
    ("reuse", "REUSE", 0, 1, False),
    ("foreign", "FOREIGN_PORT_OWNER", 0, 0, False),
    ("wrong_session", "BROWSER_OWNERSHIP_UNPROVEN", 0, 0, False),
    ("wrong_owner", "BROWSER_OWNERSHIP_UNPROVEN", 0, 0, False),
    ("wrong_profile", "BROWSER_OWNERSHIP_UNPROVEN", 0, 0, False),
    ("wrong_binary", "BROWSER_OWNERSHIP_UNPROVEN", 0, 0, False),
    ("mixed", "BROWSER_OWNERSHIP_UNPROVEN", 0, 0, False),
    ("duplicate", "BROWSER_OWNERSHIP_UNPROVEN", 0, 0, False),
    ("claimed", "PROFILE_ALREADY_ACTIVE_NO_CDP", 0, 0, False),
    ("locked", "PROFILE_LOCKED", 0, 0, False),
    ("profile_unavailable", "PROFILE_UNAVAILABLE", 0, 0, False),
    ("unsigned", "CHROME_EXECUTABLE_UNTRUSTED", 0, 0, False),
    ("no_desktop", "WINDOWS_INTERACTIVE_REQUIRED", 0, 0, False),
    ("busy", "SUPERVISOR_ACTIVE", 0, 0, False),
    ("bad_cdp", "CDP_PROTOCOL_UNAVAILABLE", 0, 1, False),
    ("pending", "UNKNOWN_EFFECT", 0, 0, True),
    ("failed_spawn", "UNKNOWN_EFFECT", 1, 0, True),
    ("bad_start_cdp", "UNKNOWN_EFFECT", 1, 1, True),
    ("different_pid", "UNKNOWN_EFFECT", 1, 0, True),
])
def test_clean_9444_inline_launcher_offline(tmp_path, case, code, starts, probes, pending):
    local = tmp_path / "Owner's Local App Data"
    owner = (local, 7, _OPERATOR_SID)
    target = ca._ChromeTarget(
        tmp_path / "Program Files" / "chrome.exe", local / "AI-Commerce" / ca._CLEAN_9444_PROFILE,
        "Default", "http://127.0.0.1:9444", 9444)
    launcher = ca._clean_launcher(owner, target, (target.executable,))
    native_boundary = "\n$slot = $null"
    assert launcher.count(native_boundary) == 1
    launcher = launcher.replace(native_boundary, _CLEAN_9444_OFFLINE_WINDOWS + native_boundary)
    command = (
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new()\n"
        f"$case = '{case}'\n"
        f"$result = {launcher}\n"
        "@{ code = $result; starts = $script:started; probes = $script:probed;"
        " pending = $script:pending } | ConvertTo-Json -Compress\n"
    )
    result = subprocess.run([str(ca._powershell()), *ca._CLEAN_POWERSHELL_FLAGS, command],
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == {"code": code, "starts": starts, "probes": probes, "pending": pending}


def test_clean_9444_cli_refuses_caller_selected_native_inputs(clean_9444, capsys, monkeypatch):
    assert ca.main(["ensure-clean-9444"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "OPT_IN_REQUIRED"
    monkeypatch.setitem(ca.os.environ, "CI", "true")
    assert ca.main(["ensure-clean-9444", "--opt-in"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "HOLD", "code": "LOCAL_OPERATOR_ONLY"}
    assert output.err == ""
    for option in ("--config", "--checkout", "--port", "--executable", "--url", "--command"):
        with pytest.raises(SystemExit) as exit_info:
            ca.main(["install-clean-9444-autostart", "--opt-in", option, "untrusted"])
        assert exit_info.value.code == 2


def test_clean_9444_import_is_inert(monkeypatch):
    import runpy
    import sys

    def unexpected(*args, **kwargs):
        pytest.fail("Import triggered a native effect")

    monkeypatch.setitem(sys.modules, "winreg", None)
    monkeypatch.setattr(ca.subprocess, "Popen", unexpected)
    monkeypatch.setattr(ca.subprocess, "run", unexpected)
    loaded = runpy.run_path(ca.__file__)
    assert callable(loaded["install_clean_9444_autostart"])
