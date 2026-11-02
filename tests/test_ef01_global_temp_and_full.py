"""Regression for EF01 provider temp isolation and MT-01 Human FULL gating."""
from __future__ import annotations

from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from ai_commerce.engineering import pytest_temp_guard, verification
from ai_commerce.engineering.minimum_test import TestGroup, file_hashes, select_plan

FULL = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]


def test_provider_pytest_redirects_even_explicit_relative_basetemp(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setenv("AI_COMMERCE_EF01_WORKTREE", str(repo))
    monkeypatch.setenv("AI_COMMERCE_EF01_PYTEST_BASETEMP_ROOT", str(external))
    config = SimpleNamespace(option=SimpleNamespace(basetemp=".local/fixture/policy"))
    pytest_temp_guard.pytest_configure(config)
    target = Path(config.option.basetemp).resolve()
    assert target.is_dir() and target.parent == external.resolve()
    assert repo.resolve() not in target.parents


def test_provider_pytest_refuses_temp_root_under_source(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    scratch = repo / ".local" / "fixtures"
    scratch.mkdir(parents=True)
    monkeypatch.setenv("AI_COMMERCE_EF01_WORKTREE", str(repo))
    monkeypatch.setenv("AI_COMMERCE_EF01_PYTEST_BASETEMP_ROOT", str(scratch))
    config = SimpleNamespace(option=SimpleNamespace(basetemp=None))
    with pytest.raises(pytest.UsageError, match="EF01_TEST_TEMP_INSIDE_SOURCE"):
        pytest_temp_guard.pytest_configure(config)


def _source(tmp_path, *, script):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "widget.py").write_text("VALUE = 1\n")
    (repo / "tests" / "test_widget.py").write_text("def test_widget(): assert True\n")
    changed = ["src/widget.py"]
    if script:
        (repo / "scripts").mkdir()
        (repo / "scripts" / "check.ps1").write_text("Write-Output 'ok'\n")
        changed.append("scripts/check.ps1")
    return repo, changed


def _plan(repo, changed, groups, acceptance):
    files = file_hashes(repo)
    return select_plan(
        repo=repo, base_sha="a"*40, base_files=files,
        candidate_files=files, changed_paths=changed,
        required_acs=acceptance, fallback_argv=FULL,
        groups=groups, registered_only=True,
        registered_profiles={"project-tests": FULL},
        env={"test": "isolated"}, human_full_suite_authorized=True,
    )


def test_human_full_registered_command_executes_but_cannot_claim_unmapped_script(tmp_path, monkeypatch):
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    repo, changed = _source(tmp_path, script=True)
    plan = _plan(repo, changed, groups=(), acceptance=("NEW.AC",))
    assert plan["version"] == 1
    assert plan["groups"][0]["state"] == "EXECUTE"
    assert plan["groups"][0]["binding"]["argv"] == FULL
    assert "AC_UNMAPPED:NEW.AC" in plan["post_test_coverage_gaps"]
    assert "NON_PYTHON_DIRECT_VERIFICATION_REQUIRED:scripts/check.ps1" in plan["post_test_coverage_gaps"]


def test_human_full_known_python_source_has_no_extra_coverage_hold(tmp_path):
    repo, changed = _source(tmp_path, script=False)
    groups = (TestGroup("widget", ("tests/test_widget.py",), ("WIDGET.AC",), ("src/widget.py",)),)
    plan = _plan(repo, changed, groups, ("WIDGET.AC",))
    assert plan["groups"][0]["execute"] is True
    assert plan["post_test_coverage_gaps"] == []


def test_full_pytest_executes_then_reports_unproven_without_false_pass(tmp_path, monkeypatch):
    repo, changed = _source(tmp_path, script=True)
    outdir = tmp_path / "evidence"
    outdir.mkdir()
    monkeypatch.setattr(verification, "require_managed_parallelism", lambda *args, **kwargs: None)
    observed = []
    def fake_run(argv, **kwargs):
        observed.append(argv)
        return subprocess.CompletedProcess(argv, 0, "created: 12/12 workers\n2 passed in 0.01s\n", "")
    monkeypatch.setattr(verification.subprocess, "run", fake_run)
    refs = {}
    def save(key, name, data):
        (outdir / name).write_bytes(data)
        refs[key] = name
    result, plan = verification.execute_plan(
        repo=repo, output_dir=outdir, base_sha="a"*40,
        base_files=file_hashes(repo), changed_paths=changed,
        required_acs=("NEW.AC",), fallback_argv=FULL, prior={},
        save=save, evidence_files=refs,
        deadline=time.monotonic()+60, timeout_seconds=30,
        registered_only=True, groups=(),
        registered_profiles={"project-tests": FULL},
        human_full_suite_authorized=True,
    )
    assert observed == [FULL]
    assert result.returncode == 5
    assert plan["groups"][0]["result"] == "PASS"
    assert plan["groups"][0]["tests_executed"]["passed"] == 2
    assert plan["groups"][0]["workers_actual"] == 12
    assert plan["final_coverage_status"] == "UNPROVEN_AFTER_REGISTERED_FULL"


def test_full_profile_cannot_be_unregistered_or_narrow(tmp_path):
    repo, changed = _source(tmp_path, script=False)
    files = file_hashes(repo)
    with pytest.raises(ValueError, match="UNTRUSTED_OR_UNREGISTERED"):
        select_plan(
            repo=repo, base_sha="a"*40, base_files=files,
            candidate_files=files, changed_paths=changed, required_acs=(),
            fallback_argv=["python", "-m", "pytest", "tests/test_widget.py", "-q", "-n", "12"],
            groups=(), registered_only=True,
            registered_profiles={"project-tests": FULL},
            human_full_suite_authorized=True,
        )
