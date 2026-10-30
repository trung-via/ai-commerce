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
