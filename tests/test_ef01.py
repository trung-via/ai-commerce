"""EF01 deterministic real subprocess smoke, git attribution, and negative cases."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_commerce.engineering.adapters import command_for
from ai_commerce.engineering.profile import ExecutorSelection, SelectionError
from ai_commerce.engineering.runner import execute_attempt, _observe_usage


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "local@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "EF test"], check=True)
    (repo / "README.md").write_text("start\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def _fake_binary(tmp_path, name, code):
    """Create a test-only CLI runnable through subprocess on Windows and POSIX.

    Windows CreateProcess cannot execute a shebang-only script (WinError 193).
    A .cmd shim launches the exact test Python interpreter; POSIX uses the
    original executable shebang script. This never affects production adapters.
    """
    if os.name == "nt":
        script = tmp_path / (name + ".py")
        script.write_text(code, encoding="utf-8")
        launcher = tmp_path / (name + ".cmd")
        launcher.write_bytes(
            ("@echo off\r\n"
             + f'"{sys.executable}" "{script}"\r\n'
             + "exit /b %errorlevel%\r\n").encode("utf-8")
        )
        return str(launcher)
    p = tmp_path / name
    p.write_text("#!" + sys.executable + "\n" + code, encoding="utf-8")
    p.chmod(0o755)
    return str(p)


def test_selection_mandatory_and_no_inferred_model():
    with pytest.raises(SelectionError):
        ExecutorSelection("", "model", "high")
    with pytest.raises(SelectionError):
        ExecutorSelection("codex", "", "high")
    with pytest.raises(SelectionError):
        ExecutorSelection("antigravity", "arg with space", "high")
    assert ExecutorSelection("codex", "gpt-6.1", "max").effort == "max"


def test_commands_exact_and_no_automatic_fallback(tmp_path):
    c, stdin = command_for(ExecutorSelection("codex", "gpt-6.1", "max"), tmp_path, "change this")
    assert c[:2] == ["codex", "exec"] and c[c.index("-m") + 1] == "gpt-6.1"
    assert 'model_reasoning_effort="max"' in c and stdin == "change this"
    a, stdin = command_for(ExecutorSelection("antigravity", "gemini-4-argon", "high"), tmp_path, "change this")
    assert a[0] == "agy" and "--print" in a and a[a.index("--effort") + 1] == "high" and stdin is None


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_both_executor_subprocesses_and_normalized_result(tmp_path, executor):
    repo = _repo(tmp_path)
    binary = _fake_binary(tmp_path, "mock-" + executor, """import json
from pathlib import Path
Path('changed.txt').write_text('ok\\n', encoding='utf-8')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':2,'output_tokens':3}}))
""")
    kwargs = {"codex_bin" if executor == "codex" else "antigravity_bin": binary}
    out = execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="EF01-test",
                          attempt_id="test-1", selection=ExecutorSelection(executor, "model-x", "high"),
                          instruction="create changed.txt", verify_argv=[sys.executable, "-c", "from pathlib import Path; assert Path('changed.txt').read_text() == 'ok\\n'"],
                          allowed_prefixes=["changed.txt"], **kwargs)
    assert out["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert out["changed_files"] == ["changed.txt"]
    assert out["source_base_sha"] == out["git_head_after"]
    assert out["candidate_file_sha256"]["changed.txt"]
    assert out["tracked_diff_sha256"] is not None
    assert out["git_delivery"] == "LOCAL_DIFF_ONLY_NO_COMMIT_NO_PUSH"
    assert (tmp_path / "evidence" / "engineering_result.json").is_file()
    assert set(out) == set(json.loads((tmp_path / "evidence" / "engineering_result.json").read_text()))
    boundary = json.loads((tmp_path / "evidence" / "provider_boundary.json").read_text())
    returned = json.loads((tmp_path / "evidence" / "provider_return_observed.json").read_text())
    assert boundary["stage"] == "BEFORE_EXECUTOR_SUBPROCESS"
    assert boundary["status"] == "EXECUTION_UNKNOWN"
    assert boundary["provider_invocation_proven"] is False
    assert boundary["replay_authorized"] is False
    assert returned["stage"] == "EXECUTOR_SUBPROCESS_RETURNED"
    assert returned["executor_process_return_observed"] is True
    assert returned["executor_exit_code"] == 0
    assert returned["provider_effects_reconciled"] is False

    # Numeric-only first-party phase timing; no extra model or provider call.
    invocation = out["invocations"][0]
    for phase in (
        "source_preflight_seconds", "prelaunch_seconds", "executor_wall_seconds",
        "post_executor_snapshot_seconds", "git_filter_seconds",
        "verify_wall_seconds", "post_verify_snapshot_seconds",
    ):
        assert isinstance(invocation[phase], (int, float))
        assert invocation[phase] >= 0
    assert invocation["prelaunch_seconds"] >= invocation["source_preflight_seconds"]
    assert invocation["git_guard_cleanup"] == "RETAINED_UNTIL_QUIESCENT"


@pytest.mark.parametrize("post_timeout_snapshot_fails", [False, True])
def test_agy_timeout_preserves_boundary_and_readonly_reconciliation(
        tmp_path, monkeypatch, post_timeout_snapshot_fails):
    """A hung provider cannot erase the original timeout or authorize its replay."""
    import time
    from ai_commerce.engineering import runner as runner_module

    repo = _repo(tmp_path)
    monkeypatch.setenv("AI_COMMERCE_EF01_AGY_STREAM_PROFILE", "1")

    def blocked_agy(command, *, repo, timeout_seconds, env, profile):
        (repo / "partial.txt").write_text("untrusted\\n", encoding="utf-8")
        time.sleep(timeout_seconds + 0.15)
        raise subprocess.TimeoutExpired(command, timeout_seconds)

    monkeypatch.setattr(runner_module, "run_agy_stream_profile", blocked_agy)
    original_snapshot = runner_module._snapshot
    calls = [0]

    def read_only_snapshot(*args, **kwargs):
        calls[0] += 1
        if post_timeout_snapshot_fails and calls[0] >= 3:
            raise RuntimeError("SYNTHETIC_POST_TIMEOUT_SNAPSHOT_FAILED")
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(runner_module, "_snapshot", read_only_snapshot)
    evidence = tmp_path / "timeout-evidence"
    result = execute_attempt(
        repo=repo, output_dir=evidence, work_id="AGY-timeout-fixture",
        attempt_id="single-invocation", selection=ExecutorSelection(
            "antigravity", "synthetic-model", "high"),
        instruction="No live provider in this fixture",
        verify_argv=[sys.executable, "-c", "assert False"],
        allowed_prefixes=["partial.txt"], timeout_seconds=1,
        action_timeout_seconds=1, max_repair_rounds=0)
    invocation = result["invocations"][0]
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EXECUTOR_TIMEOUT"
    assert invocation["error_code"] == "EXECUTOR_TIMEOUT"
    assert invocation["replay_authorized"] is False
    assert invocation["verify_completed"] is False
    assert result["repair_rounds_used"] == 0
    assert (evidence / "provider_timeout.json").is_file()
    artifact = evidence / "invocations" / "000" / "timeout.snap.json"
    if post_timeout_snapshot_fails:
        assert invocation["post_timeout_snapshot_status"] == "UNVERIFIED"
        assert invocation["post_timeout_snapshot_error_type"] == "RuntimeError"
        assert not artifact.exists()
    else:
        assert invocation["post_timeout_snapshot_status"] == "OBSERVED_UNTRUSTED"
        observed = json.loads(artifact.read_text(encoding="utf-8"))
        assert observed["quiescence_proven"] is False
        assert observed["changed_files"] == ["partial.txt"]
        assert "untrusted" not in json.dumps(observed)
        assert result["candidate_file_sha256"] == {}


def test_missing_binary_returns_non_pass_and_no_verification(tmp_path):
    repo = _repo(tmp_path)
    o = execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="w", attempt_id="a",
                        selection=ExecutorSelection("codex", "gpt-6", "max"), instruction="something",
                        verify_argv=[sys.executable, "-c", "assert False"], codex_bin="no-such-ef01-binary-9182")
    assert o["status"] == "EXECUTOR_UNAVAILABLE" and o["verify_exit_code"] is None


def test_scope_violation_cannot_pass(tmp_path):
    repo = _repo(tmp_path)
    binary = _fake_binary(tmp_path, "other", "from pathlib import Path\nPath('wrong.txt').write_text('x')\n")
    o = execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="w", attempt_id="a",
                        selection=ExecutorSelection("antigravity", "gemini-x", "medium"), instruction="x",
                        verify_argv=[sys.executable, "-c", "print('success')"],
                        allowed_prefixes=["src"], antigravity_bin=binary)
    # Scope drift does not block pytest; an unconfirmed provider still cannot PASS.
    assert o["status"] == "EXECUTION_UNKNOWN" and o["verify_exit_code"] == 0
    assert o["scope_valid"] is False and o["out_of_scope_files"] == ["wrong.txt"]
    assert o["scope_review_required"] is False


def test_dirty_worktree_rejected(tmp_path):
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("dirty\n")
    with pytest.raises(ValueError, match="WORKTREE_DIRTY"):
        execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="w", attempt_id="a",
                        selection=ExecutorSelection("codex", "m", "high"), instruction="x",
                        verify_argv=[sys.executable, "-c", "pass"])


def test_untrusted_partial_usage_not_fabricated():
    assert _observe_usage(b'{"type":"turn.completed","usage":{"input_tokens":10}}', "codex") is None
    assert _observe_usage(b'not json', "antigravity") is None


def test_cli_help_and_missing_parameters(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    cmd = [sys.executable, "-m", "ai_commerce.engineering"]
    ok = subprocess.run(cmd + ["--help"], env=env, capture_output=True, text=True)
    bad = subprocess.run(cmd + ["--model", "x"], env=env, capture_output=True, text=True)
    assert ok.returncode == 0 and "--executor" in ok.stdout
    assert bad.returncode != 0


def test_cli_full_path_negative_missing_binary(tmp_path):
    repo = _repo(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Implement one file")
    verify = tmp_path / "verify.json"
    verify.write_text(json.dumps([sys.executable, "-c", "pass"]))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    cmd = [sys.executable, "-m", "ai_commerce.engineering", "--repo", str(repo),
           "--work-id", "EF01-s", "--attempt-id", "s-1", "--executor", "codex",
           "--model", "gpt-6", "--effort", "max", "--instruction-file", str(prompt),
           "--verify-json", str(verify), "--out-dir", str(tmp_path / "out"),
           "--codex-bin", "definitely-missing-binary-EF"]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert p.returncode == 1
    assert json.loads(p.stdout)["status"] == "EXECUTOR_UNAVAILABLE"


def test_verify_out_of_scope_mutation_is_failure(tmp_path):
    repo = _repo(tmp_path)
    binary = _fake_binary(tmp_path, "write-good", "from pathlib import Path\nPath('src.txt').write_text('content')\n")
    o = execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="w", attempt_id="a",
                        selection=ExecutorSelection("codex", "m", "max"), instruction="x",
                        verify_argv=[sys.executable, "-c", "from pathlib import Path; Path('bad.txt').write_text('oops')"],
                        allowed_prefixes=["src.txt"], codex_bin=binary)
    assert o["status"] == "SOURCE_DRIFT"
    assert o["verify_exit_code"] == 0
    assert "bad.txt" in o["changed_files"]


def test_cli_accepts_windows_utf8_bom_verify_json(tmp_path):
    repo = _repo(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_bytes(b"\xef\xbb\xbfDo a code change")
    verify = tmp_path / "verify.json"
    verify.write_bytes(b"\xef\xbb\xbf" + json.dumps([sys.executable, "-c", "pass"]).encode())
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    cmd = [sys.executable, "-m", "ai_commerce.engineering", "--repo", str(repo),
           "--work-id", "EF01-s", "--attempt-id", "s-1", "--executor", "codex",
           "--model", "gpt-6", "--effort", "max", "--instruction-file", str(prompt),
           "--verify-json", str(verify), "--out-dir", str(tmp_path / "out"),
           "--codex-bin", "definitely-missing-binary-EF"]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert p.returncode == 1
    assert json.loads(p.stdout)["status"] == "EXECUTOR_UNAVAILABLE"

def test_codex_windows_service_mxc_override_and_unchanged_antigravity(tmp_path):
    codex = ExecutorSelection("codex", "gpt-6.1-sol", "max")
    plain, stdin = command_for(codex, tmp_path, "code change")
    mxc, other_stdin = command_for(codex, tmp_path, "code change", codex_windows_sandbox="mxc")
    assert plain[:2] == ["codex", "exec"]
    assert mxc[:4] == ["codex", "-c", "windows.sandbox=mxc", "exec"]
    assert mxc[4:] == plain[2:]
    assert "--sandbox" in mxc and mxc[mxc.index("--sandbox") + 1] == "workspace-write"
    assert '-c' in mxc and 'model_reasoning_effort="max"' in mxc
    assert stdin == other_stdin == "code change"

    antigravity = ExecutorSelection("antigravity", "gemini-3.8-flash", "medium")
    before, no_stdin = command_for(antigravity, tmp_path, "change")
    after, after_stdin = command_for(antigravity, tmp_path, "change")
    assert before == after and before[0] == "agy" and no_stdin is after_stdin is None
    assert "--mode" in before and before[before.index("--mode") + 1] == "accept-edits"
    assert "windows.sandbox=mxc" not in before
    with pytest.raises(ValueError, match="CODEX_SANDBOX_WITH_NON_CODEX_EXECUTOR"):
        command_for(antigravity, tmp_path, "change", codex_windows_sandbox="mxc")
    with pytest.raises(ValueError, match="UNSUPPORTED_CODEX_WINDOWS_SANDBOX"):
        command_for(codex, tmp_path, "change", codex_windows_sandbox="none")

def test_agy_headless_soft_denial_is_explicit_nonpass(tmp_path):
    repo = _repo(tmp_path)
    mock = _fake_binary(tmp_path, "agy-denied", """import json,sys
print(json.dumps({"status":"SUCCESS","response":"","denied_actions":[{"action":"read_file","display_name":"ReadFile"}]}))
print('jetski: headless read_file permission was auto-denied', file=sys.stderr)
""")
    result = execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="EF02-AGY", attempt_id="agy-denied-01",
        selection=ExecutorSelection("antigravity","gemini-3.8-flash","medium"),
        instruction="create one file", verify_argv=[sys.executable,"-c","assert False"],
        antigravity_bin=mock, allowed_prefixes=["tests/"])
    assert result["status"] == "EXECUTOR_PERMISSION_DENIED"
    assert result["error_code"] == "HEADLESS_TOOL_PERMISSION_DENIED"
    assert result["denied_actions"] == ["read_file"]
    assert result["verify_exit_code"] is None
    assert result["changed_files"] == []
    assert result["executor_exit_code"] == 0

def test_agy_headless_denied_actions_are_not_ignored_even_after_write(tmp_path):
    repo = _repo(tmp_path)
    mock = _fake_binary(tmp_path, "agy-partial", """import json
from pathlib import Path
Path('one.txt').write_text('partial')
print(json.dumps({"status":"SUCCESS","denied_actions":[{"action":"command"}]}))
""")
    result = execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="EF02-AGY", attempt_id="agy-partial-01",
        selection=ExecutorSelection("antigravity","gemini-3.8-flash","medium"),
        instruction="create one file", verify_argv=[sys.executable,"-c","assert False"],
        antigravity_bin=mock, allowed_prefixes=["one.txt"])
    assert result["status"] == "EXECUTOR_PERMISSION_DENIED"
    assert result["denied_actions"] == ["command"]
    assert result["changed_files"] == ["one.txt"]


def test_real_mock_executor_timeout_is_unknown_and_no_verification(tmp_path):
    """A real OS child timeout is not proof that provider side effects failed."""
    repo = _repo(tmp_path)
    binary = _fake_binary(tmp_path, "mock-sleeping-cli",
                          "import time\ntime.sleep(3)\n")
    outdir = tmp_path / "timeout-evidence"
    result = execute_attempt(
        repo=repo, output_dir=outdir, work_id="M0R-timeout",
        attempt_id="timeout-001",
        selection=ExecutorSelection("codex", "mock-model-no-network", "high"),
        instruction="fixture only", verify_argv=[sys.executable, "-c", "assert False"],
        timeout_seconds=1, codex_bin=binary)
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EXECUTOR_TIMEOUT"
    assert result["replay_authorized"] is False
    assert result["executor_invoked"] is True
    assert result["verify_exit_code"] is None
    assert result["usage_status"] == "UNKNOWN"
    assert result["cost_status"] == "UNKNOWN"
    assert not (outdir / "provider_return_observed.json").exists()
    assert not (outdir / "verify.stdout.log").exists()
    boundary = json.loads((outdir / "provider_boundary.json").read_text())
    timeout = json.loads((outdir / "provider_timeout.json").read_text())
    assert boundary["stage"] == "BEFORE_EXECUTOR_SUBPROCESS"
    assert boundary["replay_authorized"] is False
    assert timeout["stage"] == "EXECUTOR_TIMEOUT_OBSERVED"
    assert timeout["provider_effects_reconciled"] is False
    assert json.loads((outdir / "engineering_result.json").read_text())["status"] == "EXECUTION_UNKNOWN"


def test_parent_lost_around_provider_boundary_remains_unknown(tmp_path, monkeypatch):
    """Simulate abrupt parent loss; no actual provider or runner crash."""
    from ai_commerce.engineering import runner as ef01_runner

    repo = _repo(tmp_path)
    actual_run = subprocess.run
    def interrupted_run(argv, **kwargs):
        if isinstance(argv, list) and argv and argv[0] == "never-run-codex":
            raise SystemExit("SIMULATED_PARENT_DISAPPEARED_AFTER_BOUNDARY")
        return actual_run(argv, **kwargs)
    monkeypatch.setattr(ef01_runner.subprocess, "run", interrupted_run)
    outdir = tmp_path / "interrupted"
    with pytest.raises(SystemExit, match="SIMULATED_PARENT_DISAPPEARED_AFTER_BOUNDARY"):
        execute_attempt(
            repo=repo, output_dir=outdir, work_id="M0R-interruption",
            attempt_id="interruption-001",
            selection=ExecutorSelection("codex", "fixture-no-model", "max"),
            instruction="do not run", verify_argv=[sys.executable, "-c", "assert False"],
            codex_bin="never-run-codex")
    boundary = json.loads((outdir / "provider_boundary.json").read_text())
    report = json.loads((outdir / "engineering_result.json").read_text())
    assert boundary["status"] == "EXECUTION_UNKNOWN"
    assert boundary["provider_invocation_proven"] is False
    assert boundary["replay_authorized"] is False
    assert report["status"] == "EXECUTION_UNKNOWN"
    assert report["verify_exit_code"] is None
    assert not (outdir / "provider_return_observed.json").exists()



def test_codex_live_event_mirror_streams_before_process_exit(tmp_path):
    """Real Python child, no provider: JSONL is visible while subprocess lives."""
    import threading
    import time
    from ai_commerce.engineering.runner import _run_codex_with_live_event_mirror
    logfile = tmp_path / "codex.events.live.jsonl"
    fake = ("import json,time; print(json.dumps("
            "{'type':'item.started','item':{'type':'command_execution',"
            "'command':'python -V'}}), flush=True); time.sleep(0.8)")
    done = {}
    def execute():
        done["result"] = _run_codex_with_live_event_mirror(
            [sys.executable, "-c", fake], repo=tmp_path, stdin_text=None,
            timeout_seconds=5, live_path=logfile)
    t = threading.Thread(target=execute)
    t.start()
    try:
        deadline = time.monotonic() + 3
        while not logfile.is_file() or "item.started" not in logfile.read_text():
            assert time.monotonic() < deadline, "LIVE_EVENT_NOT_VISIBLE_BEFORE_EXIT"
            time.sleep(0.03)
        assert t.is_alive(), "EVENT_ONLY_VISIBLE_AFTER_EXIT"
    finally:
        t.join(timeout=4)
    assert done["result"].returncode == 0
    assert "command_execution" in done["result"].stdout
    assert logfile.read_text() == done["result"].stdout


def test_codex_live_event_mirror_timeout_cannot_become_pass(tmp_path):
    from ai_commerce.engineering.runner import _run_codex_with_live_event_mirror
    live = tmp_path / "partial.jsonl"
    with pytest.raises(subprocess.TimeoutExpired):
        _run_codex_with_live_event_mirror(
            [sys.executable, "-c",
             "import time; print('{\"type\":\"turn.started\"}', flush=True); time.sleep(4)"],
            repo=tmp_path, stdin_text=None, timeout_seconds=1,
            live_path=live)
    assert "turn.started" in live.read_text()


def test_agy_headless_evidence_scope_registers_only_prepared_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence = tmp_path / "attempt-evidence" / "invocations" / "000"
    evidence.mkdir(parents=True)
    (evidence / "instruction.txt").write_text("trusted task", encoding="utf-8")
    selection = ExecutorSelection("antigravity", "gemini-3.8-flash", "high")

    argv, stdin = command_for(selection, repo, "do scoped task",
                              antigravity_evidence_dir=evidence)
    roots = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--add-dir"]
    assert stdin is None
    assert roots == [str(evidence.resolve()), str(repo)]
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" not in argv
    assert "--yolo" not in argv

    with pytest.raises(ValueError, match="AGY_EVIDENCE_DIR_NOT_PREPARED"):
        command_for(selection, repo, "task", antigravity_evidence_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        command_for(selection, repo, "task", antigravity_evidence_dir=tmp_path / "missing")
    with pytest.raises(ValueError, match="AGY_EVIDENCE_WITH_NON_AGY_EXECUTOR"):
        command_for(ExecutorSelection("codex", "gpt-6", "max"), repo, "task",
                    antigravity_evidence_dir=evidence)


def test_agy_ef01_passes_attempt_evidence_scope_to_adapter(tmp_path, monkeypatch):
    import ai_commerce.engineering.runner as runner_module

    repo = _repo(tmp_path)
    binary = _fake_binary(tmp_path, "agy-scope-fake", """import json
from pathlib import Path
Path('changed.txt').write_text('scoped\\n', encoding='utf-8')
print(json.dumps({'status': 'SUCCESS', 'denied_actions': []}))
""")
    real = runner_module.command_for
    seen = []

    def capture(selection, path, instruction, **kwargs):
        mirror = kwargs.get("antigravity_evidence_dir")
        seen.append((selection.executor, mirror, mirror.is_dir() if mirror else None,
                     (mirror / "instruction.txt").is_file() if mirror else None,
                     (mirror / "provider_boundary.json").exists() if mirror else None))
        return real(selection, path, instruction, **kwargs)

    monkeypatch.setattr(runner_module, "command_for", capture)
    outdir = tmp_path / "evidence"
    result = execute_attempt(
        repo=repo, output_dir=outdir, work_id="agy-scope-smoke",
        attempt_id="new-attempt", selection=ExecutorSelection("antigravity", "gemini-test", "high"),
        instruction="create changed.txt", verify_argv=[sys.executable, "-c", "assert True"],
        allowed_prefixes=["changed.txt"], antigravity_bin=binary,
    )
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert (outdir / "invocations" / "000" / "instruction.txt").is_file()
    assert seen[-1][0] == "antigravity"
    assert seen[-1][2:] == (True, True, False)
    assert outdir not in seen[-1][1].parents
    assert not seen[-1][1].exists()  # disposable mirror removed after CLI return
    assert result["invocations"][0]["agy_input_cleanup"] == "CLEANED"
    assert result["denied_actions"] == []


# Human-selected project soft default: no unapproved within-attempt switch.
from datetime import datetime, timedelta, timezone

from ai_commerce.engineering.model_defaults import resolve_model, observe_result, STATE_PATH


def _soft_default_repo(tmp_path):
    p = tmp_path / "config" / "engineering" / "model-defaults.v1.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "version": 1, "hard_default": False,
        "preferred": {"executor": "codex", "model": "gpt-6.1-sol", "effort": "max"},
        "fallback": {"executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"},
        "recovery_probe_cooldown_seconds": 3600,
    }), encoding="utf-8")
    return tmp_path


def _soft_default_report(when, *, status, completed=False, attempt="a"):
    return {
        "executor": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "max",
        "started_at": when.isoformat(), "attempt_id": attempt, "status": status,
        "invocations": [{"executor_invoked": True, "executor_exit_code": 1 if not completed else 0,
                         "status": status, "provider_return_determinate": completed}],
    }


def test_project_soft_default_and_manual_override(tmp_path):
    repo = _soft_default_repo(tmp_path)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    decision = resolve_model(repo, now=now)
    assert decision.as_dict() == {
        "executor": "codex", "model": "gpt-6.1-sol", "effort": "max",
        "selection_source": "PROJECT_SOFT_DEFAULT", "quota_mode": "PREFERRED",
        "retry_after": None,
    }
    other = ExecutorSelection("antigravity", "human-other-model", "medium")
    human = resolve_model(repo, override=other, now=now)
    assert human.selection == other and human.source == "HUMAN_EXPLICIT"
    assert not (repo / STATE_PATH).exists()


def test_quota_fallback_then_recovery_check_then_verified_restoration(tmp_path):
    repo = _soft_default_repo(tmp_path)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert observe_result(repo, _soft_default_report(now, status="QUOTA_EXHAUSTED"),
                          now=now) == "QUOTA_FALLBACK_RECORDED"
    fallback = resolve_model(repo, now=now + timedelta(minutes=2))
    assert (fallback.selection.executor, fallback.selection.model, fallback.selection.effort) == (
        "antigravity", "gemini-3.8-flash", "high")
    assert fallback.source == "PROJECT_QUOTA_FALLBACK"
    due = resolve_model(repo, now=now + timedelta(hours=1, minutes=1))
    assert due.source == "PROJECT_CODEX_RECOVERY_CHECK"
    assert due.selection == ExecutorSelection("codex", "gpt-6.1-sol", "max")
    # A test failure *after* a confirmed Codex completion still proves quota is available.
    later = now + timedelta(hours=1, minutes=2)
    assert observe_result(repo, _soft_default_report(
        later, status="VERIFICATION_FAILED", completed=True, attempt="recovered"),
        now=later) == "CODEX_PREFERENCE_RESTORED"
    assert resolve_model(repo, now=later).source == "PROJECT_SOFT_DEFAULT"


def test_recovery_probe_still_quota_switches_future_action_only(tmp_path):
    repo = _soft_default_repo(tmp_path)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    observe_result(repo, _soft_default_report(now, status="QUOTA_EXHAUSTED"), now=now)
    later = now + timedelta(hours=2)
    assert resolve_model(repo, now=later).source == "PROJECT_CODEX_RECOVERY_CHECK"
    observe_result(repo, _soft_default_report(
        later, status="QUOTA_EXHAUSTED", attempt="again"), now=later)
    assert resolve_model(repo, now=later + timedelta(minutes=1)).selection.executor == "antigravity"


@pytest.mark.parametrize("problem", [
    "EXECUTION_UNKNOWN", "MODEL_AT_CAPACITY", "EXECUTOR_PERMISSION_DENIED",
    "MODEL_UNSUPPORTED", "EXECUTOR_FAILED",
])
def test_unverified_or_unrelated_errors_do_not_infer_quota(tmp_path, problem):
    repo = _soft_default_repo(tmp_path)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert observe_result(repo, _soft_default_report(now, status=problem),
                          now=now) == "IGNORED_UNPROVEN"
    assert resolve_model(repo, now=now).source == "PROJECT_SOFT_DEFAULT"


def test_missing_provider_process_evidence_does_not_flip(tmp_path):
    repo = _soft_default_repo(tmp_path)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    report = _soft_default_report(now, status="QUOTA_EXHAUSTED")
    report["invocations"][0]["executor_exit_code"] = None
    assert observe_result(repo, report, now=now) == "IGNORED_UNPROVEN"
    assert resolve_model(repo, now=now).selection.executor == "codex"


def test_stale_older_success_does_not_cancel_newer_quota(tmp_path):
    repo = _soft_default_repo(tmp_path)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    observe_result(repo, _soft_default_report(
        now + timedelta(minutes=10), status="QUOTA_EXHAUSTED"), now=now + timedelta(minutes=10))
    result = observe_result(repo, _soft_default_report(
        now, status="BUILD_VERIFIED_FOR_SCOPE", completed=True), now=now + timedelta(minutes=11))
    assert result == "IGNORED_STALE_ATTEMPT"
    assert resolve_model(repo, now=now + timedelta(minutes=11)).selection.executor == "antigravity"


def test_explicit_nonpreferred_human_model_does_not_change_quota_state(tmp_path):
    repo = _soft_default_repo(tmp_path)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    result = _soft_default_report(now, status="QUOTA_EXHAUSTED")
    result["model"] = "human-chosen-codex-model"
    assert observe_result(repo, result, now=now) == "IGNORED_OTHER_SELECTION"
    assert resolve_model(repo, now=now).selection.executor == "codex"


def test_corrupted_policy_state_fails_closed(tmp_path):
    repo = _soft_default_repo(tmp_path)
    state = repo / STATE_PATH
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"mode":"BAD","version":1}', encoding="utf-8")
    with pytest.raises(SelectionError, match="MODEL_DEFAULTS_STATE_INVALID"):
        resolve_model(repo)
    explicit = ExecutorSelection("antigravity", "human", "high")
    assert resolve_model(repo, override=explicit).selection == explicit


def test_project_default_resolver_cli_does_not_call_executor(tmp_path):
    repo = _soft_default_repo(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    p = subprocess.run([sys.executable, "-m", "ai_commerce.engineering.model_defaults",
                        "--repo", str(repo)], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    obj = json.loads(p.stdout)
    assert obj["executor"] == "codex" and obj["effort"] == "max"


def test_ef01_cli_prefers_soft_default_but_human_override_wins(tmp_path, monkeypatch):
    from ai_commerce.engineering import cli as efcli
    repo = _soft_default_repo(tmp_path)
    instruction = tmp_path / "instruction.txt"
    verify = tmp_path / "verify.json"
    instruction.write_text("Safe bounded test", encoding="utf-8")
    verify.write_text(json.dumps(["python", "-m", "pytest", "tests/test_ef01.py", "-q"]))
    captured = []

    def fake_execute(**kw):
        captured.append((kw["selection"], kw["selection_source"]))
        return {"work_id": "x", "attempt_id": "a", "executor": kw["selection"].executor,
                "model": kw["selection"].model, "reasoning_effort": kw["selection"].effort,
                "status": "VERIFICATION_FAILED", "error_code": "TEST", "invocations": []}

    monkeypatch.setattr(efcli, "execute_attempt", fake_execute)
    monkeypatch.setattr(efcli, "observe_result", lambda *_: "IGNORED_UNPROVEN")
    args = ["--repo", str(repo), "--work-id", "x", "--attempt-id", "a",
            "--instruction-file", str(instruction), "--verify-json", str(verify),
            "--out-dir", str(tmp_path / "out")]
    assert efcli.main(args) == 1
    assert captured[-1] == (ExecutorSelection("codex", "gpt-6.1-sol", "max"),
                            "PROJECT_SOFT_DEFAULT")
    assert efcli.main(args + ["--executor", "antigravity", "--model", "other",
                              "--effort", "high"]) == 1
    assert captured[-1] == (ExecutorSelection("antigravity", "other", "high"),
                            "HUMAN_EXPLICIT")
    assert efcli.main(args + ["--model", "partial"]) == 2


def test_new_crlf_txt_normalized_before_actual_verify(tmp_path):
    """#202: EF01 seals hashes only after Git-clean-stable text is produced."""
    import hashlib

    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "core.autocrlf", "true"], check=True)
    (repo / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitattributes"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "text rules"], check=True)
    fake = _fake_binary(tmp_path, "write-crlf-202", (
        "from pathlib import Path\n"
        "Path('consumer-tests.txt').write_bytes(bytes.fromhex('636f6e73756d65720d0a'))\n"
        "print('{\"type\":\"turn.completed\"}')\n"
    ))
    result = execute_attempt(
        repo=repo, output_dir=tmp_path / "evidence", work_id="EF02-202-regression",
        attempt_id="new-immutable-id", selection=ExecutorSelection("codex", "model", "high"),
        instruction="write text", allowed_prefixes=["consumer-tests.txt"], codex_bin=fake,
        verify_argv=[sys.executable, "-c", (
            "from pathlib import Path; "
            "assert Path('consumer-tests.txt').read_bytes() == bytes.fromhex('636f6e73756d65720a')"
        )],
    )
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["verify_exit_code"] == 0
    normalized = (repo / "consumer-tests.txt").read_bytes()
    assert normalized == b"consumer\n"
    assert result["candidate_file_sha256"]["consumer-tests.txt"] == hashlib.sha256(normalized).hexdigest()
    assert "git_filter_preverify" in result["evidence_files"]
    assert (tmp_path / "evidence" / "git_filter.preverify.json").is_file()
