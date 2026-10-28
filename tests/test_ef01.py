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
    assert o["status"] == "SCOPE_VIOLATION" and o["verify_exit_code"] is None


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
    assert o["status"] == "SCOPE_VIOLATION"
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
