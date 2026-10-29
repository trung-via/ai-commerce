"""Git Write Guard: real Git subprocess coverage, independent of an LLM CLI."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_commerce.engineering.git_write_guard import (
    GIT_GUARD_INSTRUCTION,
    git_write_guard_env,
)
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.engineering.runner import execute_attempt


def _git(repo: Path, *args: str, env=None):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False, capture_output=True, text=True, env=env,
    )


def _repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    assert _git(repo, "config", "user.name", "Guard Test").returncode == 0
    assert _git(repo, "config", "user.email", "guard@example.invalid").returncode == 0
    (repo / "data.txt").write_text("original\n", encoding="utf-8")
    assert _git(repo, "add", "data.txt").returncode == 0
    assert _git(repo, "commit", "-qm", "base").returncode == 0
    return repo


@pytest.mark.parametrize("args", [
    ("commit", "-m", "bad"),
    ("commit", "--no-verify", "-m", "bad"),
    ("checkout", "-b", "bad-branch"),
    ("reset", "--hard", "HEAD"),
])
def test_executor_hooks_block_ref_writes_without_changing_head(tmp_path, args):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "data.txt").write_text("new\n", encoding="utf-8")
    assert _git(repo, "add", "data.txt").returncode == 0
    env = git_write_guard_env(tmp_path / "evidence")
    assert _git(repo, "config", "--get", "core.hooksPath", env=env).returncode == 0
    assert _git(repo, "status", "--porcelain", env=env).returncode == 0
    result = _git(repo, *args, env=env)
    assert result.returncode != 0
    assert "AI_COMMERCE_GIT_WRITE_BLOCKED" in (result.stderr + result.stdout)
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == base
    # reset --hard may clean the tree; give parent Git its own new edit.
    # Parent/publisher Git is not subject to executor-only hooks.
    (repo / "parent-only.txt").write_text("publisher change\n", encoding="utf-8")
    assert _git(repo, "add", "parent-only.txt").returncode == 0
    assert _git(repo, "commit", "-qm", "allowed-parent").returncode == 0
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != base


def test_guard_preserves_existing_git_env_config(tmp_path):
    parent = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.quotepath",
        "GIT_CONFIG_VALUE_0": "false",
    }
    child = git_write_guard_env(tmp_path / "evidence", parent_env=parent)
    assert child["GIT_CONFIG_COUNT"] == "2"
    assert child["GIT_CONFIG_KEY_0"] == "core.quotepath"
    assert child["GIT_CONFIG_KEY_1"] == "core.hooksPath"
    assert Path(child["GIT_CONFIG_VALUE_1"]).is_dir()
    assert "GIT_CONFIG_KEY_1" not in parent


@pytest.mark.parametrize("bad", ["-1", "NaN", "65"])
def test_invalid_parent_git_config_fails_before_executor(tmp_path, bad):
    with pytest.raises(RuntimeError, match="GIT_WRITE_GUARD_ENV_INVALID"):
        git_write_guard_env(tmp_path / "evidence", parent_env={"GIT_CONFIG_COUNT": bad})


def _fake_cli(tmp_path: Path):
    script = tmp_path / "fake_provider.py"
    script.write_text(
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "text = sys.stdin.read() if 'exec' in sys.argv else sys.argv[sys.argv.index('--print') + 1]\n"
        "assert 'GIT WRITE GUARD' in text\n"
        "Path('changed.txt').write_text('written\\n', encoding='utf-8')\n"
        "subprocess.run(['git', 'add', 'changed.txt'], check=True)\n"
        "attempt = subprocess.run(['git', 'commit', '--no-verify', '-m', 'blocked'], capture_output=True, text=True)\n"
        "assert attempt.returncode != 0\n"
        "assert 'AI_COMMERCE_GIT_WRITE_BLOCKED' in attempt.stderr\n"
        "print(json.dumps({'type':'turn.completed','status':'SUCCESS'}))\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = tmp_path / "fake_provider.cmd"
        wrapper.write_text(
            '@echo off\r\n"' + sys.executable + '" "' + str(script) + '" %*\r\n'
            + "exit /b %errorlevel%\r\n",
            encoding="utf-8",
        )
        return str(wrapper)
    wrapper = tmp_path / "fake_provider"
    wrapper.write_text("#!" + sys.executable + "\n"
                       + script.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    return str(wrapper)


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_both_executors_get_guard_and_can_still_edit_code(tmp_path, executor):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    fake = _fake_cli(tmp_path)
    result = execute_attempt(
        repo=repo,
        output_dir=tmp_path / "evidence",
        work_id="EF01-GIT-GUARD",
        attempt_id="guard-test",
        selection=ExecutorSelection(executor, "model-x", "high"),
        instruction="Write changed.txt without changing HEAD.",
        verify_argv=[sys.executable, "-c",
                     "from pathlib import Path; assert Path('changed.txt').read_text() == 'written\\n'"],
        allowed_prefixes=["changed.txt"],
        **{("codex_bin" if executor == "codex" else "antigravity_bin"): fake},
    )
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["git_write_guard"] == "EXECUTOR_SCOPED_HOOKS_PLUS_POST_HEAD_CHECK"
    assert result["git_head_after"] == base
    assert result["changed_files"] == ["changed.txt"]
    assert (
        tmp_path / "evidence" / "invocations" / "000" / "instruction.txt"
    ).read_text(encoding="utf-8") == "Write changed.txt without changing HEAD."
