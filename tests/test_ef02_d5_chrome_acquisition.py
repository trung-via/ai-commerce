"""D5 Chrome Acquisition: synthetic and read-only checks; never launch Chrome."""
from pathlib import Path
import json
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
