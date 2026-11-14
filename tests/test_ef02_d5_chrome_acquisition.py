"""D5 Chrome Acquisition: synthetic and read-only checks; never launch Chrome."""
from pathlib import Path
import json
import re
import os
import subprocess

import pytest
from ai_commerce.remote import chrome_acquisition as ca


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
    monkeypatch.setattr(ca, "_powershell", lambda: tmp_path / "powershell.exe")
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
    from shutil import which
    shell = which("powershell.exe")
    assert shell, "Native Windows PowerShell 5.1 is required"
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
    """PowerShell 5.1: actual path selection on synthetic input, no private IO."""
    if os.name != "nt":
        pytest.skip("Windows-only PowerShell path acceptance")
    from shutil import which
    shell = which("powershell.exe")
    assert shell
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
    from shutil import which
    shell = which("powershell.exe")
    assert shell, "Windows PowerShell 5.1 is required"
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
        result = ca.enroll_existing_9444(repo, opt_in=True)
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


@pytest.mark.parametrize("relative", ["Chrome-Acquisition/config.json", "chrome.json", "chrome-acquisition.json"])
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
def test_wh01b_enrollment_rechecks_without_replay(monkeypatch, tmp_path, change):
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
    assert_enrollment_hold(ca.assess_existing_9444(repo), expected)
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
