"""Offline provider fixtures; these are not live Antigravity permission evidence."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_commerce.engineering import runner
from ai_commerce.engineering.profile import ExecutorSelection


def stream(*events):
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode()


def test_known_target_is_classified_without_disclosing_it(tmp_path):
    repo = tmp_path / "repo"
    private = "credential-do-not-print"
    data = stream({"status": "SUCCESS", "denied_actions": [
        {"action": "read_file", "arguments": {"path": str(repo / "src" / private)}},
        {"action": "read_file", "path": str(tmp_path / private)},
        {"action": "read_file", "parameters": {"file_path": str(tmp_path / "input" / private)}},
        {"action": "read_file"},
    ]})
    diagnostic = runner._headless_denial_diagnostics(
        data, "antigravity", repo=repo, agy_input_dir=tmp_path / "input")
    assert [d["target_status"] for d in diagnostic] == [
        "DENIED_TARGET_KNOWN", "DENIED_TARGET_KNOWN", "DENIED_TARGET_KNOWN",
        "DENIED_TARGET_UNKNOWN"]
    assert [d["target_scope"] for d in diagnostic] == ["WORKTREE", "EXTERNAL", "AGY_INPUT", "UNKNOWN"]
    assert private not in json.dumps(diagnostic)
    assert str(tmp_path) not in json.dumps(diagnostic)


def test_no_inference_from_text_or_nested_tool_payload_and_bounded_output(tmp_path):
    nested = stream({"type": "item.completed", "item": {"result": {
        "denied_actions": [{"action": "read_file", "path": "secret"}]}}})
    assert runner._headless_denied_actions(nested, "antigravity") == []
    data = stream({"denied_actions": [{"action": "read_file",
        "message": "permission denied reading C:/private/token"}] * 100})
    result = runner._headless_denial_diagnostics(data, "antigravity", repo=tmp_path)
    assert 0 < len(result) <= 32
    assert all(d["target_status"] == "DENIED_TARGET_UNKNOWN" for d in result)
    assert "private" not in json.dumps(result)
    assert runner._headless_denied_actions(data, "codex") == []


def test_lexical_target_scope_never_confuses_sibling_or_relative_paths(tmp_path):
    repo = tmp_path / "repo"
    data = stream({"denied_actions": [
        {"action": "read_file", "path": str(tmp_path / "repo-other" / "file.py")},
        {"action": "read_file", "path": "src/file.py"},
        {"action": "read_file", "path": str(repo / ".." / "private")}]})
    assert [d["target_scope"] for d in runner._headless_denial_diagnostics(
        data, "antigravity", repo=repo)] == ["EXTERNAL", "RELATIVE", "EXTERNAL"]


@pytest.mark.parametrize("denied", [[{}], ["read_file"], [{"action": "secret\nTOKEN=value"}]])
def test_any_explicit_denial_fails_closed_and_never_echoes_unknown_tool(denied):
    assert runner._headless_denied_actions(stream({"denied_actions": denied}), "antigravity") == ["UNKNOWN_TOOL"]


@pytest.mark.parametrize("denied", ["read_file", {"action": "read_file"}])
def test_nonempty_malformed_denial_container_still_fails_closed(denied):
    assert runner._headless_denied_actions(stream({"status": "SUCCESS", "denied_actions": denied}),
        "antigravity") == ["UNKNOWN_TOOL"]


def test_private_structured_target_is_distinct_from_ordinary_source(tmp_path):
    result = runner._headless_denial_diagnostics(stream({"denied_actions": [
        {"action": "read_file", "path": str(tmp_path / ".local" / "authoritative.json")}
    ]}), "antigravity", repo=tmp_path)
    assert result[0]["target_scope"] == "PRIVATE_PATH"
    assert "authoritative.json" not in json.dumps(result)


def test_registered_roots_under_private_ancestors_are_still_exact_roots(tmp_path):
    repo = tmp_path / ".codex" / "worktree"
    agy_input = tmp_path / ".local" / "attempt" / "agy_input"
    data = stream({"denied_actions": [
        {"action": "read_file", "path": str(repo / "src" / "file.py")},
        {"action": "read_file", "path": str(agy_input / "instruction.txt")},
        {"action": "read_file", "path": str(repo / ".local" / "private.json")} ]})
    assert [d["target_scope"] for d in runner._headless_denial_diagnostics(
        data, "antigravity", repo=repo, agy_input_dir=agy_input)] == ["WORKTREE", "AGY_INPUT", "PRIVATE_PATH"]


def run_fixture(tmp_path, monkeypatch, events, *, exit_code=0, verify_code=0):
    repo = tmp_path / "workspace"
    repo.mkdir()
    calls = []
    def snapshot(*args, **kwargs):
        changed = (repo / "changed.txt").exists()
        hashes = {"changed.txt": hashlib.sha256((repo / "changed.txt").read_bytes()).hexdigest()} if changed else {}
        return {"git_head_after": "a" * 40, "git_dir": str(repo / ".git"),
                "changed_files": list(hashes), "candidate_file_sha256": hashes,
                "tracked_diff_sha256": hashlib.sha256(json.dumps(hashes).encode()).hexdigest(),
                "scope_valid": True}, b"fixture diff"
    monkeypatch.setattr(runner, "_snapshot", snapshot)
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: (["fixture-provider"], None))
    original_run = subprocess.run
    def execute(argv, **kwargs):
        if argv[0] == "fixture-provider":
            calls.append("provider")
            (repo / "changed.txt").write_text("candidate", encoding="utf-8")
            return subprocess.CompletedProcess(argv, exit_code, stream(*events).decode(), "")
        if list(argv[1:3]) == ["-m", "pytest"]:
            calls.append("verify")
            return subprocess.CompletedProcess(argv, verify_code, "1 failed\n" if verify_code else "1 passed\n", "")
        return original_run(argv, **kwargs)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="fixture", attempt_id="fixture-attempt", instruction="create changed.txt",
        selection=ExecutorSelection("antigravity", "fixture-model", "high"),
        verify_argv=[sys.executable, "-m", "pytest", "tests", "-q", "-n", "12"],
        allowed_prefixes=["changed.txt"], max_repair_rounds=2)
    return result, calls


@pytest.mark.parametrize("exit_code", [0, 1])
def test_success_or_nonzero_with_denial_never_verifies_or_replays(tmp_path, monkeypatch, exit_code):
    result, calls = run_fixture(tmp_path, monkeypatch, [{"status": "SUCCESS",
        "denied_actions": [{"action": "read_file"}], "error": {"message": "quota exceeded"}}],
        exit_code=exit_code)
    assert result["status"] == "EXECUTOR_PERMISSION_DENIED"
    assert result["error_code"] == "HEADLESS_TOOL_PERMISSION_DENIED"
    assert result["verify_exit_code"] is None and calls == ["provider"]
    assert result["replay_authorized"] is False
    assert result["failure_provenance"]["phase"] == "HEADLESS_TOOL_DENIED"
    assert result["failure_provenance"]["tool"] == "read_file"
    assert result["denied_action_diagnostics"][0]["target_status"] == "DENIED_TARGET_UNKNOWN"
    assert result["git_delivery"] == "LOCAL_DIFF_ONLY_NO_COMMIT_NO_PUSH"
    assert "candidate_ref" not in result


@pytest.mark.parametrize("message,phase", [("quota exceeded", "PROVIDER_QUOTA"),
    ("Selected model is at capacity", "PROVIDER_CAPACITY"), ("unrecognized failure", "UNKNOWN_EFFECTS")])
def test_nonzero_provider_failure_keeps_specific_phase(tmp_path, monkeypatch, message, phase):
    result, calls = run_fixture(tmp_path, monkeypatch, [{"type": "turn.failed",
        "error": {"message": message}}], exit_code=1)
    assert calls == ["provider"] and result["verify_exit_code"] is None
    assert result["failure_provenance"]["phase"] == phase
    assert result["replay_authorized"] is False


def test_provider_internal_pytest_is_not_registered_verification(tmp_path, monkeypatch):
    result, calls = run_fixture(tmp_path, monkeypatch, [
        {"type": "item.completed", "item": {"type": "command_execution",
            "command": "python -m pytest tests -q", "exit_code": 1,
            "aggregated_output": "SECRET traceback"}},
        {"type": "turn.failed", "error": {"message": "quota exceeded"}}], exit_code=1)
    assert calls == ["provider"] and result["verify_exit_code"] is None
    assert result["provider_test_observations"] == [{"phase": "AGENT_INTERNAL_PYTEST",
        "tool": "pytest", "status": "FAILED", "exit_code": 1,
        "authority": "PROVIDER_REPORTED_NOT_EF01_VERIFY"}]
    assert "SECRET" not in json.dumps(result["provider_test_observations"])
    assert result["failure_provenance"]["phase"] == "PROVIDER_QUOTA"


def test_provider_comment_cannot_claim_internal_pytest_execution():
    assert runner._provider_test_observations(stream({"type": "item.completed", "item": {
        "type": "agent_message", "text": "I ran python -m pytest: 1 failed"}})) == []


def test_agy_repair_uses_mirrored_feedback_without_requiring_external_logs(tmp_path, monkeypatch):
    from test_ef01_repair import fixture, _run
    # Reuse the existing no-Git fixture and real local Python subprocesses.
    f = fixture.__wrapped__(tmp_path, monkeypatch)
    result, calls, _ = _run(f, executor="antigravity")
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    prompt = calls[1]["prompt"]
    assert "External archived artifacts are OPTIONAL" in prompt
    assert "Read the full feedback, source snapshot" not in prompt
    assert "agy_input" in prompt and "repair_feedback.json" in prompt


def test_fingerprint_excludes_attempt_and_raw_diagnostics_but_binds_precondition():
    invocation = {"status": "EXECUTOR_PERMISSION_DENIED", "error_code": "HEADLESS_TOOL_PERMISSION_DENIED",
        "executor": "antigravity", "model": "fixture", "reasoning_effort": "high",
        "source_base_sha": "a" * 40, "attempt_id": "attempt-1", "denied_actions": ["read_file"],
        "candidate_file_sha256": {"file.py": "b" * 64}, "verify_argv": ["python", "-m", "pytest"],
        "diagnostic": "SECRET C:/private/token", "instruction_sha256": "c" * 64}
    first = runner._failure_provenance(invocation, precondition_instruction_sha256="d" * 64)
    other = copy.deepcopy(invocation)
    other.update(attempt_id="attempt-2", diagnostic="another secret", instruction_sha256="e" * 64)
    second = runner._failure_provenance(other, precondition_instruction_sha256="d" * 64)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["attempt_id"] != second["attempt_id"]
    assert "SECRET" not in json.dumps(first) and "private" not in json.dumps(first)
    other["candidate_file_sha256"]["file.py"] = "f" * 64
    assert first["fingerprint"] != runner._failure_provenance(other,
        precondition_instruction_sha256="d" * 64)["fingerprint"]
    other["attempt_id"] = "TOKEN=secret\nC:/private/key"
    assert runner._failure_provenance(other, precondition_instruction_sha256="d" * 64)["attempt_id"] == "UNPROVEN"


def test_unchanged_failure_precondition_blocks_next_model_call(tmp_path, monkeypatch):
    # Drive the existing repair loop with unchanged, determinate verifier evidence.
    from test_ef01_repair import _snapshot_fake, _files
    repo = tmp_path / "workspace"
    repo.mkdir()
    baseline = _files(repo)
    monkeypatch.setattr(runner, "_snapshot", lambda r, s, d: _snapshot_fake(r, baseline, {"scope": ["impl.py"]}))
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: (["fixture-provider"], None))
    calls = []
    def failed_round(**kwargs):
        inv = kwargs["invocation"]
        calls.append(inv["repair_round"])
        inv.update(status="VERIFICATION_FAILED", error_code="TARGETED_VERIFY_NONZERO_OR_UNAVAILABLE",
            executor_invoked=True, executor_exit_code=0, verify_exit_code=1, verify_completed=True,
            verification_origin="REGISTERED_EF01", repair_eligible=True,
            candidate_file_sha256={"impl.py": "b" * 64},
            final_snapshot=kwargs["expected"])
        for key in ("verify_stdout", "verify_stderr", "tracked_diff", "source_after_verify"):
            inv["evidence_files"][key] = "fixture-" + key
            inv["evidence_sha256"][key] = "c" * 64
    monkeypatch.setattr(runner, "_run_invocation", failed_round)
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="fixture", attempt_id="unchanged", selection=ExecutorSelection("codex", "fixture", "high"),
        instruction="scoped change", allowed_prefixes=["impl.py"],
        verify_argv=[sys.executable, "-m", "pytest", "tests", "-q", "-n", "12"])
    assert calls == [0, 1]
    assert result["stop_reason"] == "UNCHANGED_FAILURE_PRECONDITION"
    assert result["failure_provenance"]["phase"] == "EF01_REGISTERED_VERIFY"

@pytest.mark.parametrize("exit_code", [128, 130, -9])
def test_interrupted_provider_exit_preserves_unknown_effects_despite_quota(tmp_path, monkeypatch, exit_code):
    result, calls = run_fixture(tmp_path, monkeypatch,
        [{"type": "turn.failed", "error": {"message": "quota exceeded"}}],
        exit_code=exit_code)
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EXECUTOR_INTERRUPTED"
    assert result["failure_provenance"]["phase"] == "UNKNOWN_EFFECTS"
    assert result["verify_exit_code"] is None
    assert result["replay_authorized"] is False
    assert calls == ["provider"]

def test_short_agy_input_directory_is_not_nested_under_evidence_root(tmp_path):
    """Regression for #837: deep evidence paths caused WinError 267."""
    import shutil
    import tempfile
    from ai_commerce.engineering.runner import _new_agy_input_dir
    deep = tmp_path / ("batch-" + "b" * 80) / ("action-" + "a" * 80) / "ef01" / "invocations" / "000"
    mirror = _new_agy_input_dir()
    try:
        assert mirror.is_dir()
        assert mirror.name.startswith("agy-ef01-")
        assert deep not in mirror.parents
        assert mirror.parent == Path(tempfile.gettempdir()).resolve()
        assert len(str(mirror).encode("utf-16-le")) // 2 <= 180 or sys.platform != "win32"
        # This must actually work as a Windows subprocess working directory:
        # #837's 248-unit cwd failed BEFORE shell/tool launch (WinError 267).
        probe = subprocess.run([sys.executable, "-c", "import os; print(os.getcwd())"],
                               cwd=mirror, capture_output=True, text=True, timeout=10)
        assert probe.returncode == 0
        assert Path(probe.stdout.strip()).resolve() == mirror
    finally:
        shutil.rmtree(mirror)


def test_git_source_unavailable_emits_safe_specific_cause(tmp_path, monkeypatch):
    """Provider failures and worktree loss must remain distinguishable."""
    import time
    from ai_commerce.engineering import runner
    secret = "SECRET-TOKEN-WITH-PRIVATE-PATH"
    def failing_git(repo, *args, **kwargs):
        return subprocess.CompletedProcess(["git"], 128, "", secret)
    monkeypatch.setattr(runner, "_git", failing_git)
    with pytest.raises(RuntimeError, match="GIT_SOURCE_UNAVAILABLE:GIT_ENTRY_MISSING") as error:
        runner._snapshot(tmp_path, [], time.monotonic() + 10)
    assert secret not in str(error.value)

def test_503_observation_survives_lost_git_snapshot_without_provider_replay(tmp_path, monkeypatch):
    """#837: a post-call Git failure must not erase the provider 503 forensics."""
    from ai_commerce.engineering import runner
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    head = "a" * 40
    baseline = {
        "git_head_after": head, "git_dir": str(repo / ".git"),
        "changed_files": [], "candidate_file_sha256": {}, "tracked_diff_sha256": "0" * 64,
        "scope_valid": True,
    }
    counter = {"snapshot": 0, "provider": 0}
    def snap(*args, **kwargs):
        counter["snapshot"] += 1
        if counter["snapshot"] >= 3:
            raise RuntimeError("GIT_SOURCE_UNAVAILABLE:WORKTREE_MISSING")
        return dict(baseline), b""
    monkeypatch.setattr(runner, "_snapshot", snap)
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: (["fixture-provider"], None))
    original_run = subprocess.run
    def run(argv, **kwargs):
        if argv[0] == "fixture-provider":
            counter["provider"] += 1
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "status": "ERROR", "error": "No capacity available for model gemini-3.8-flash-high",
            }), "")
        return original_run(argv, **kwargs)
    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner.execute_attempt(
        repo=repo, output_dir=tmp_path / "evidence", work_id="ef01-837",
        attempt_id="distinct-one-shot", selection=ExecutorSelection("antigravity", "model", "high"),
        instruction="offline fixture", verify_argv=[sys.executable, "-m", "pytest", "tests/test_ef01.py", "-q"],
        allowed_prefixes=["changed.txt"], max_repair_rounds=0,
    )
    invocation = result["invocations"][0]
    assert counter["provider"] == 1
    assert invocation["provider_failure_observed"] == "MODEL_AT_CAPACITY"
    assert invocation["status"] == "EXECUTION_UNKNOWN"
    assert invocation["error_code"] == "RUNNER_ERROR"
    assert invocation["verify_exit_code"] is None
    assert invocation["replay_authorized"] is False
