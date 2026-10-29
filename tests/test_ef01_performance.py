"""Direct Git and selected 12-worker profile contracts; no benchmarks or vendors."""
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from ai_commerce.engineering import runner
from ai_commerce.engineering.git import git_argv, resolve_git_executable
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.engineering.pytest_profile import (
    BENCHMARK_PROVENANCE, HostCapacity, profile_command, worker_decision,
    require_managed_parallelism,
)
from ai_commerce.remote.validation import check_source

GIB = 1024 ** 3


@pytest.fixture
def direct_git(tmp_path):
    path = tmp_path / "Git With Spaces" / "git.exe"
    path.parent.mkdir()
    path.write_bytes(b"test executable placeholder; never launched")
    path.chmod(0o755)
    return str(path.resolve())


def test_direct_git_path_keeps_windows_argv_and_default_path(direct_git, monkeypatch, tmp_path):
    monkeypatch.delenv("AI_COMMERCE_GIT_EXECUTABLE", raising=False)
    assert resolve_git_executable() == "git"
    argv = git_argv(tmp_path / "work tree", "status", "--porcelain=v1", "-z", executable=direct_git)
    assert argv == [direct_git, "-C", str(tmp_path / "work tree"), "status", "--porcelain=v1", "-z"]
    assert subprocess.list2cmdline(argv).startswith('"' + direct_git + '"')
    monkeypatch.setenv("AI_COMMERCE_GIT_EXECUTABLE", direct_git)
    assert git_argv(tmp_path, "rev-parse", "HEAD")[0] == direct_git


@pytest.mark.parametrize("kind", ["missing", "empty", "shell", "wrapper", "directory"])
def test_bad_explicit_git_fails_before_any_provider(tmp_path, monkeypatch, kind):
    configured = str(tmp_path / "missing.exe")
    if kind == "empty":
        configured = ""
    elif kind == "shell":
        configured = 'git -c core.sshCommand="anything"'
    elif kind == "wrapper":
        path = tmp_path / "git.cmd"
        path.write_text("@echo off\n")
        path.chmod(0o755)
        configured = str(path)
    elif kind == "directory":
        configured = str(tmp_path)
    monkeypatch.setattr(runner, "command_for", lambda *_a, **_kw: pytest.fail("unexpected provider setup"))
    with pytest.raises(ValueError, match="GIT_EXECUTABLE"):
        runner.execute_attempt(repo=tmp_path, output_dir=tmp_path / "evidence", work_id="x", attempt_id="y",
                               selection=ExecutorSelection("codex", "gpt-6.1-sol", "max"), instruction="fixture",
                               verify_argv=["python", "-m", "pytest", "tests", "-q"], git_executable=configured)


def test_every_ef01_snapshot_git_operation_uses_direct_executable(direct_git, monkeypatch, tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[3:5] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if argv[3:5] == ["rev-parse", "--absolute-git-dir"]:
            return subprocess.CompletedProcess(argv, 0, str(tmp_path / ".git") + "\n", "")
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=run))
    tree, _ = runner._snapshot(tmp_path, (), 10 ** 20, git_executable=direct_git)
    assert tree["git_head_after"] == "a" * 40
    assert [argv[3] for argv, _ in calls] == ["rev-parse", "rev-parse", "status", "diff", "rev-parse"]
    assert all(argv[0] == direct_git and not kw.get("shell", False) for argv, kw in calls)


def test_real_explicit_git_observation_matches_path_default(tmp_path):
    # Read-only observations of this checkout; no branch/index/HEAD mutation.
    repo = Path(__file__).resolve().parents[1]
    direct = shutil.which("git")
    assert direct
    default = runner._git(repo, "rev-parse", "HEAD")
    explicit = runner._git(repo, "rev-parse", "HEAD", git_executable=direct)
    assert default.returncode == explicit.returncode == 0 and default.stdout == explicit.stdout


def test_remote_source_git_path_is_explicit_and_credentials_are_untouched(tmp_path, monkeypatch, direct_git):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "commit\n", "")
    monkeypatch.setattr("ai_commerce.remote.validation.subprocess.run", run)
    check_source(tmp_path, "a" * 40, git_executable=direct_git)
    argv, kwargs = calls[0]
    assert argv == [direct_git, "-C", str(tmp_path), "cat-file", "-t", "a" * 40]
    assert "env" not in kwargs and not kwargs.get("shell", False)


@pytest.mark.parametrize("cpus,ram,slots", [(26, 40, 2), (16, 16, 2), (8, 8, 2),
                                         (2, 3, 2), (14, 14, 1), (26, 12, 2), (None, None, 2)])
def test_every_action_gets_twelve_without_cpu_ram_or_second_action_reduction(cpus, ram, slots):
    result = worker_decision(capacity=HostCapacity(cpus, ram * GIB if ram is not None else None), host_slots=slots,
                             xdist_available=True, isolation_approved=True, windows=True)
    assert result["intended_workers"] == result["workers"] == result["workers_requested"] == 12
    assert result["workers_actual"] is None and result["degradation_reason"] is None
    assert result["benchmark_provenance"] == BENCHMARK_PROVENANCE == "HUMAN_REPORTED_PRIOR_BENCHMARK"
    assert result["prior_compared_workers"] == [2, 4, 8, 12, 16]
    assert result["observed_workers"] is None


@pytest.mark.parametrize("override,reason", [
    ({"xdist_available": False}, "XDIST_UNAVAILABLE"),
    ({"registered_only": True}, "COMPANION_REGISTRATION"),
])
def test_missing_xdist_or_serial_registration_is_truthfully_recorded(override, reason):
    args = dict(profile="windows-12", capacity=HostCapacity(64, 64 * GIB), host_slots=2,
                xdist_available=True, isolation_approved=True, windows=True)
    result = worker_decision(**(args | override))
    assert result["workers"] == 0 and reason in result["reason"]
    assert profile_command(["python", "-m", "pytest", "tests", "-q"], decision=result, temp_dir=Path("temp")) == [
        "python", "-m", "pytest", "tests", "-q"]


def test_fixture_approval_and_host_probe_are_not_new_capacity_gates():
    result = worker_decision(capacity=HostCapacity(None, None), host_slots=2,
                             xdist_available=True, windows=False, isolation_approved=False)
    assert result["workers"] == 12
    with pytest.raises(ValueError, match="FIXTURE_FAILURE_STOP"):
        worker_decision(capacity=HostCapacity(32, 32 * GIB), xdist_available=True, flaky=True)


def test_windows_xdist_command_is_isolated_and_never_changes_model_or_ef02_slots(tmp_path):
    selected = worker_decision(profile="windows-12", capacity=HostCapacity(32, 64 * GIB), host_slots=2,
                               xdist_available=True, isolation_approved=True, windows=True)
    argv = ["python", "-m", "pytest", "tests/test_minimum_test.py", "-q"]
    temp = tmp_path / "action a" / "group b"
    command = profile_command(argv, decision=selected, temp_dir=temp)
    assert command == [*argv, "-n", "12", "--dist", "loadfile", "--basetemp", str(temp)]
    assert selected["host_slots"] == 2
    assert "--executor" not in command and "--model" not in command and "--effort" not in command
    with pytest.raises(ValueError, match="PLAIN_PYTHON_PYTEST"):
        profile_command([*argv, "-n", "24"], decision=selected, temp_dir=temp)


@pytest.mark.parametrize("source", ["argv", "environment", "pyproject", "pytest.ini", "tox.ini", "setup.cfg"])
def test_unmanaged_workers_cannot_override_twelve_per_action(tmp_path, monkeypatch, source):
    argv = ["python", "-m", "pytest", "tests", "-q"]
    if source == "argv":
        argv += ["-n24"]
    elif source == "environment":
        monkeypatch.setenv("PYTEST_ADDOPTS", "-n 24")
    elif source == "pyproject":
        (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-n 24"\n')
    else:
        section = "tool:pytest" if source == "setup.cfg" else "pytest"
        (tmp_path / source).write_text(f"[{section}]\naddopts = -n 24\n")
    with pytest.raises(ValueError, match="UNMANAGED_WORKERS"):
        require_managed_parallelism(tmp_path, argv)


@pytest.mark.parametrize("source", ["argv", "environment", "pyproject", "pytest.ini"])
def test_mt01_config_cannot_silently_deselect_applicable_tests(tmp_path, monkeypatch, source):
    argv = ["python", "-m", "pytest", "tests", "-q"]
    if source == "argv":
        argv += ["--deselect=tests/test_scope.py::test_required_ac"]
    elif source == "environment":
        monkeypatch.setenv("PYTEST_ADDOPTS", "-k only_one_test")
    elif source == "pyproject":
        (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-m only_one_marker"\n')
    else:
        (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --ignore=tests/test_required_ac.py\n")
    with pytest.raises(ValueError, match="FILTERED_SCOPE_UNPROVEN"):
        require_managed_parallelism(tmp_path, argv, coverage_required=True)


def test_unknown_pytest_configuration_cannot_bypass_scope_or_capacity(tmp_path):
    (tmp_path / "pytest.toml").write_text('[pytest]\naddopts = "-n 24"\n')
    with pytest.raises(ValueError, match="CONFIG_SCOPE_UNPROVEN"):
        require_managed_parallelism(tmp_path, ["python", "-m", "pytest", "tests", "-q"], coverage_required=True)


@pytest.mark.parametrize("flag", ["--lf", "--stepwise"])
def test_mt01_implicit_pytest_cache_cannot_narrow_ac_scope(tmp_path, flag):
    with pytest.raises(ValueError, match="FILTERED_SCOPE_UNPROVEN"):
        require_managed_parallelism(tmp_path, ["python", "-m", "pytest", "tests", "-q", flag], coverage_required=True)


def test_unavailable_host_probes_keep_human_twelve_each_decision(monkeypatch):
    import ctypes
    from ai_commerce.engineering import pytest_profile as profile
    def unavailable(*_a):
        raise OSError("capacity unavailable")
    monkeypatch.setattr(profile, "os", SimpleNamespace(cpu_count=unavailable))
    monkeypatch.setattr(profile, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=unavailable)), raising=False)
    capacity = profile.host_capacity()
    assert capacity == HostCapacity(None, None)
    selected = worker_decision(profile="windows-12", capacity=capacity, windows=True,
                               isolation_approved=True, xdist_available=True, host_slots=2)
    assert selected["workers"] == 12 and selected["reason"] == "HUMAN_DEFAULT_12_EACH_ACTION"


def test_ef02_direct_git_admin_fetch_credentials_do_not_enter_child(tmp_path, monkeypatch, direct_git):
    from test_ef02_m0r_fault_injection import _worker, _action, BASE
    from ai_commerce.remote.worker import _safe_child_env
    calls = []
    class Github:
        def claim(self, **_kw):
            return "CLAIMED"
        def integrated_source(self, **_kw):
            return {"effective_sha": "b" * 40, "integration_ref": "refs/heads/ef02/integrated/fake"}
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0 if "fetch" in argv else 1, "", "fixture worktree failure")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-credential-not-a-real-secret")
    monkeypatch.setattr("ai_commerce.remote.worker.subprocess.run", run)
    worker = _worker(tmp_path, Github())
    worker.git_executable = direct_git
    outcome = worker.execute_action(_action())
    assert outcome["status"] == "SOURCE_WORKTREE_FAILED"
    assert [argv[0] for argv, _ in calls] == [direct_git, direct_git]
    assert calls[0][1]["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert "GIT_CONFIG_COUNT" not in _safe_child_env(tmp_path)
    assert "GITHUB_TOKEN" not in _safe_child_env(tmp_path)


def test_two_registered_actions_each_launch_twelve_with_default_max_parallel(tmp_path, monkeypatch):
    """Fake subprocesses prove policy and overlap, never claim live OS workers."""
    import threading
    import time
    from ai_commerce.engineering import verification
    from ai_commerce.engineering.minimum_test import file_hashes
    from ai_commerce.remote.scheduler import run_schedule
    from ai_commerce.remote.validation import approved_verification_plan
    barrier = threading.Barrier(2)
    calls = []
    parallel = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    def run(argv, **kwargs):
        assert argv == parallel and not kwargs.get("shell", False)
        started = time.monotonic()
        barrier.wait(timeout=10)
        calls.append((list(argv), started, time.monotonic()))
        return subprocess.CompletedProcess(argv, 0, "created: 12/12 workers\n12 workers [1 item]\n1 passed\n", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired))
    def work(action):
        repo = tmp_path / action["action_id"]
        (repo / "tests").mkdir(parents=True)
        (repo / "tests/test_scope.py").write_text("def test_value():\n    assert True\n")
        output = tmp_path / (action["action_id"] + "-evidence")
        output.mkdir()
        files = {}
        def save(key, name, data):
            (output / name).write_bytes(data)
            files[key] = name
        approved = approved_verification_plan(action, {"project-tests": parallel})
        selected = worker_decision(capacity=HostCapacity(None, None), host_slots=2,
                                   xdist_available=True, registered_only=True, registered_argv=approved["argv"])
        result, plan = verification.execute_plan(
            repo=repo, output_dir=output, base_sha="a" * 40, base_files=file_hashes(repo),
            changed_paths=["tests/test_scope.py"], required_acs=(), fallback_argv=approved["argv"], prior={},
            save=save, evidence_files=files, deadline=time.monotonic() + 20, timeout_seconds=15,
            registered_only=True,
            command_transform=lambda argv, group: (profile_command(argv, decision=selected, temp_dir=output / group), selected))
        assert result.returncode == 0
        assert plan["groups"][0]["workers_actual"] == 12
        return {"status": "CANDIDATE_READY", "workers_requested": selected["workers_requested"]}
    batch = {"batch_id": "two-actions", "source_sha": "a" * 40, "actions": [
        {"action_id": name, "attempt_id": name, "depends_on": [], "verification_profile": "project-tests"}
        for name in ("a", "b")]}
    assert "max_parallel" not in batch  # Exercise the unchanged default: two.
    result = run_schedule(batch, work)
    assert result["status"] == "COMPLETE" and result["scheduler_max_active"] == 2
    assert [a["workers_requested"] for a in result["actions"]] == [12, 12]
    assert len(calls) == 2 and max(c[1] for c in calls) <= min(c[2] for c in calls)


def test_registered_parallel_profile_missing_xdist_stops_and_serial_contract_is_exact(tmp_path):
    serial = ["python", "-m", "pytest", "tests", "-q"]
    with pytest.raises(ValueError, match="XDIST_UNAVAILABLE.*STOP"):
        worker_decision(capacity=HostCapacity(None, None), registered_only=True,
                        registered_argv=[*serial, "-n", "12"], xdist_available=False)
    decision = worker_decision(capacity=HostCapacity(None, None), registered_only=True,
                               registered_argv=serial, xdist_available=True)
    assert decision["workers_requested"] == 12 and decision["workers"] == 0
    assert decision["degradation_reason"] is None
    assert profile_command(serial, decision=decision, temp_dir=tmp_path) == serial


@pytest.mark.parametrize("suffix", [["-n", "auto"], ["-n", "2"], ["-n", "12", "-n", "2"], ["--numprocesses=12"]])
def test_registered_worker_policy_rejects_automatic_or_reduced_counts(tmp_path, suffix):
    with pytest.raises(ValueError, match="REQUIRES_12_EACH"):
        require_managed_parallelism(tmp_path, ["python", "-m", "pytest", "tests", "-q", *suffix],
                                    registered_only=True)


def test_ef01_repair_every_verify_argv_keeps_twelve(tmp_path, monkeypatch):
    import json
    from test_ef01_repair import _files, _provider, _snapshot_fake
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_scope.py").write_text("def test_value():\n    assert True\n")
    baseline = _files(repo)
    context = SimpleNamespace(repo=repo, root=tmp_path, monkeypatch=monkeypatch,
                              state={"scope": ["impl.py", "kept.txt"]})
    monkeypatch.setattr(runner, "_snapshot", lambda r, _p, _d: _snapshot_fake(r, baseline, context.state))
    monkeypatch.setattr(runner, "xdist_available", lambda: True)
    monkeypatch.setenv("AI_COMMERCE_EF01_CODEX_STREAM_EVENTS", "0")
    real_run, calls = subprocess.run, []
    def run(argv, **kwargs):
        if argv[1:3] != ["-m", "pytest"]:
            return real_run(argv, **kwargs)
        calls.append(list(argv))
        assert argv[argv.index("-n") + 1] == "12"
        return subprocess.CompletedProcess(argv, 1 if len(calls) == 1 else 0,
            "created: 12/12 workers\n12 workers [1 item]\n" + (
                "FAILED test_scope.py::test_value\n1 failed\n" if len(calls) == 1 else "1 passed\n"), "")
    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    selection, provider_calls, _ = _provider(context)
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="repair12", attempt_id="one",
                                    selection=selection, instruction="fixture", host_slots=2,
                                    verify_argv=["python", "-m", "pytest", "test_scope.py", "-q"],
                                    allowed_prefixes=context.state["scope"])
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE" and len(calls) == 2
    assert len(provider_calls.read_text().splitlines()) == 2 and result["repair_rounds_used"] == 1
    assert all(inv["executor"] == selection.executor and inv["model"] == selection.model and
               inv["reasoning_effort"] == selection.effort and inv["pytest_profile"]["workers"] == 12
               for inv in result["invocations"])
    assert result["pytest_profile"]["workers_actual"] == 12
    assert result["pytest_profile"]["execution_status"] == "12_WORKERS_EXECUTED"


def test_cli_defaults_to_twelve_without_fixture_approval():
    from ai_commerce.engineering.cli import build_parser
    args = build_parser().parse_args([
        "--repo", ".", "--work-id", "x", "--attempt-id", "y", "--executor", "codex", "--model", "test", "--effort", "max",
        "--instruction-file", "instruction.txt", "--verify-json", "verify.json", "--out-dir", ".local/test"])
    assert args.pytest_profile == "windows-12"


def test_registered_minimal_group_uses_its_own_twelve_worker_command(tmp_path, monkeypatch):
    from ai_commerce.engineering import minimum_test as mt, verification
    from test_ef01_repair import _files, _provider, _snapshot_fake
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests/test_scope.py").write_text("def test_value():\n    assert True\n")
    baseline = _files(repo)
    context = SimpleNamespace(repo=repo, root=tmp_path, monkeypatch=monkeypatch,
                              state={"scope": ["impl.py", "kept.txt"]})
    monkeypatch.setattr(runner, "_snapshot", lambda r, _p, _d: _snapshot_fake(r, baseline, context.state))
    monkeypatch.setattr(runner, "xdist_available", lambda: True)
    monkeypatch.setattr(mt, "approved_groups", lambda: (
        mt.TestGroup("scoped", ("tests/test_scope.py",), ("AC",), ("impl.py", "kept.txt")),))
    monkeypatch.setenv("AI_COMMERCE_EF01_CODEX_STREAM_EVENTS", "0")
    scoped = ["python", "-m", "pytest", "tests/test_scope.py", "-q", "-n", "12"]
    calls = []
    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "created: 12/12 workers\n12 workers [1 item]\n1 passed\n", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired))
    selection, provider_calls, _ = _provider(context)
    full = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="registry", attempt_id="one",
        selection=selection, instruction="fixture", verify_argv=full, allowed_prefixes=context.state["scope"],
        minimum_test=True, registered_only=True, registered_profiles={"project-tests": full, "scoped": scoped})
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE" and calls == [scoped]
    assert not result["minimum_test"]["fallback"] and result["pytest_profile"]["workers"] == 12
    assert len(provider_calls.read_text().splitlines()) == 1
