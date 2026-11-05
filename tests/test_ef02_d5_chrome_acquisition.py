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
