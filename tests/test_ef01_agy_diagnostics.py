"""Offline provider fixtures; these are not live Antigravity permission evidence."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_commerce.engineering import runner
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.engineering import adapters


def input_fixture(tmp_path, feedback=True):
    repo, mirror = tmp_path / "source", tmp_path / "input"
    repo.mkdir()
    mirror.mkdir()
    expected = {"instruction.txt": 'Sửa mã nguồn "Việt" \\ 🚀'.encode()}
    if feedback:
        expected["repair_feedback.json"] = b'{"trusted_host_feedback":true}\n'
    for name, data in expected.items():
        (mirror / name).write_bytes(data)
    baseline = adapters.agy_input_manifest(repo, mirror, expected)
    assert baseline["status"] == "INTACT", baseline
    return repo, mirror, expected, baseline


def test_benign_sidecar_keeps_contract_hash_and_is_never_read(tmp_path, monkeypatch):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    (mirror / "pytest-session.log").write_bytes(b"not an instruction")
    # The old guard rejects exactly this reproduction before comparing bytes.
    with pytest.raises(ValueError, match="AGY_EVIDENCE_DIR_NOT_PREPARED"):
        adapters.agy_input_files(repo, mirror)
    original = adapters.agy_read_regular
    def read_contract_only(path, limit):
        assert path.name in expected
        return original(path, limit)
    monkeypatch.setattr(adapters, "agy_read_regular", read_contract_only)
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "INTACT"
    contract = next(f for f in result["files"] if f["name"] == "instruction.txt")
    assert contract["sha256"] == hashlib.sha256(expected["instruction.txt"]).hexdigest()
    sidecar = next(f for f in result["files"] if f["role"] == "AUXILIARY")
    assert sidecar["sha256"] is None and sidecar["hash_status"] == "AUXILIARY_CONTENT_NOT_READ"


@pytest.mark.parametrize("name", ["instruction.txt", "repair_feedback.json"])
@pytest.mark.parametrize("change,reason", [("modify", "MODIFIED"), ("delete", "DELETED"), ("replace", "REPLACED")])
def test_contract_tampering_has_exact_reason(tmp_path, name, change, reason):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    target = mirror / name
    if change == "modify":
        target.write_bytes(b"tampered")
    elif change == "delete":
        target.unlink()
    else:
        # Keep the old inode alive to make identity replacement deterministic.
        target.rename(tmp_path / "old-contract")
        target.write_bytes(expected[name])
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_INPUT_CONTRACT_" + reason


@pytest.mark.parametrize("name", ["settings.json", "AGENTS.md", "metadata.json", "pytest.ini",
                                  "pytest-session.exe", "pytest-session.py", "scratch-session.cmd",
                                  "cache-agent.log.json", "repair_feedback.json"])
def test_unknown_config_and_executable_names_hold(tmp_path, name):
    repo, mirror, expected, baseline = input_fixture(tmp_path, feedback=False)
    (mirror / name).write_bytes(b"opaque")
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD"
    assert result["reason"] in ("AGY_INPUT_UNEXPECTED_AUXILIARY", "AGY_INPUT_EXECUTABLE")


@pytest.mark.parametrize("target", ["instruction.txt", "pytest-session.log"])
def test_hardlinks_hold_without_opening_external_bytes(tmp_path, target):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    outside = tmp_path / "external"
    outside.write_bytes(b"private outside")
    (mirror / target).unlink(missing_ok=True)
    try:
        os.link(outside, mirror / target)
    except PermissionError:
        pytest.skip("native hardlink creation denied; no bypass")
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_HARDLINK"


def test_reported_hardlink_metadata_holds_even_when_native_creation_is_unavailable(tmp_path, monkeypatch):
    """Simulated stat metadata, explicitly not native hardlink proof."""
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    original = Path.lstat
    target = mirror / "instruction.txt"
    def linked_stat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size,
                                   st_file_attributes=getattr(info, "st_file_attributes", 0), st_nlink=2)
        return info
    monkeypatch.setattr(Path, "lstat", linked_stat)
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_HARDLINK"


@pytest.mark.parametrize("target", ["instruction.txt", "pytest-session.log"])
def test_reparse_entries_hold_and_cleanup_never_follows_target(tmp_path, target):
    repo, _, _, _ = input_fixture(tmp_path)
    from test_ef01 import _directory_alias
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"keep")
    with runner._agy_input_mirror(repo, "task", None) as mirror:
        expected = adapters.agy_input_files(repo, mirror)
        baseline = adapters.agy_input_manifest(repo, mirror, expected)
        (mirror / target).unlink(missing_ok=True)
        try:
            _directory_alias(outside, mirror / target)
        except PermissionError:
            pytest.skip("native reparse creation denied; no bypass")
        result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
        assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_NON_REGULAR"
    assert (outside / "sentinel.txt").read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt", reason="native NTFS ADS check")
@pytest.mark.parametrize("target", ["instruction.txt", "pytest-session.log", "."])
def test_ntfs_alternate_streams_hold(tmp_path, target):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    if target == "pytest-session.log":
        (mirror / target).write_bytes(b"opaque")
    stream_path = str(mirror if target == "." else mirror / target) + ":hidden"
    with open(stream_path, "wb") as stream_file:
        stream_file.write(b"hidden payload")
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_NAMED_STREAM"


@pytest.mark.parametrize("kind", ["directory", "count", "size", "root-swap", "traversal"])
def test_input_bounds_and_root_replacement_hold(tmp_path, kind):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    if kind == "directory":
        (mirror / "pytest-dir.log").mkdir()
    elif kind == "count":
        for index in range(17):
            (mirror / f"pytest-{index}.log").write_bytes(b"")
    elif kind == "size":
        with (mirror / "cache-large.tmp").open("wb") as file:
            file.truncate(1024 * 1024 + 1)
    elif kind == "root-swap":
        mirror.rename(tmp_path / "old-input")
        mirror.mkdir()
        for name, data in expected.items():
            (mirror / name).write_bytes(data)
    else:
        mirror = mirror / ".." / "input"
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD"


def test_contract_race_is_observed_before_read(tmp_path, monkeypatch):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    original = adapters.os.open
    def racing_open(path, flags, *args, **kwargs):
        if Path(path).name == "instruction.txt":
            (mirror / "instruction.txt").write_bytes(b"raced")
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(adapters.os, "open", racing_open)
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_RACE"


def test_cleanup_refuses_to_delete_replacement_directory(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    with pytest.raises(ValueError, match="AGY_INPUT_CLEANUP_UNSAFE"):
        with runner._agy_input_mirror(repo, "task", None) as mirror:
            mirror.rename(tmp_path / "original-input")
            mirror.mkdir()
            (mirror / "unrelated.txt").write_bytes(b"keep")
    assert (mirror / "unrelated.txt").read_bytes() == b"keep"
    assert (tmp_path / "original-input/instruction.txt").read_bytes() == b"task"


def test_scratch_is_a_distinct_explicit_root_and_preserves_model(tmp_path):
    repo, mirror, _, _ = input_fixture(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    selection = ExecutorSelection("antigravity", "gemini-3.8-flash", "high")
    argv, stdin = adapters.command_for(selection, repo, "task", antigravity_evidence_dir=mirror,
                                       antigravity_scratch_dir=scratch)
    assert stdin is None
    assert [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--add-dir"] == list(map(str, (repo, mirror, scratch)))
    assert argv[argv.index("--model") + 1] == selection.model
    assert argv[argv.index("--effort") + 1] == "high"
    for alias in (repo, mirror, mirror / ".." / "scratch"):
        with pytest.raises(ValueError):
            adapters.command_for(selection, repo, "task", antigravity_evidence_dir=mirror,
                                 antigravity_scratch_dir=alias)


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
    monkeypatch.setattr(runner, "_agy_source_binding", lambda r, s, *a: {
        "source_root": str(r), "source_sha": s["git_head_after"], "git_dir": s["git_dir"]})
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


@pytest.mark.parametrize("quote", ["'", "\u2018", "\u2019", "\u201b", "\u02bc", "\uff07"])
def test_usage_limit_only_in_failed_provider_fields(quote):
    message = f"You{quote}ve hit your usage limit. Try again later."
    failure = stream({"type": "error", "message": message},
                     {"type": "turn.failed", "error": {"message": message}})
    assert runner._provider_problem(failure) == ("QUOTA_EXHAUSTED", "QUOTA_EXHAUSTED")
    for kind in ("command_execution", "tool_result", "agent_message"):
        success = stream({"type": "item.completed", "item": {
            "type": kind, "status": "completed", "exit_code": 0,
            "aggregated_output": message, "error": {"message": message}}},
            {"type": "turn.completed"})
        assert runner._provider_problem(success) is None


@pytest.mark.parametrize("quote", ["'", "\u2019"])
def test_codex_usage_limit_stops_same_attempt_without_verify(tmp_path, monkeypatch, quote):
    from test_ef01_repair import fixture, _provider, VERIFY
    f = fixture.__wrapped__(tmp_path, monkeypatch)
    selection, _, _ = _provider(f)
    calls = []
    raw = stream({"type": "error", "message": f"You{quote}ve hit your usage limit"},
                 {"type": "turn.failed", "error": {"message": f"You{quote}ve hit your usage limit"}})
    actual_run = subprocess.run
    def fail(argv, **kwargs):
        if argv[0] == "git":
            return actual_run(argv, **kwargs)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, raw.decode(), "")
    monkeypatch.setattr(runner.subprocess, "run", fail)
    result = runner.execute_attempt(repo=f.repo, output_dir=f.root / "evidence",
        work_id="quota", attempt_id="fresh-quota", selection=selection,
        instruction="scoped source change", verify_argv=VERIFY, max_repair_rounds=2)
    assert result["status"] == result["error_code"] == "QUOTA_EXHAUSTED"
    assert len(calls) == result["invocation_count"] == 1
    assert result["verify_exit_code"] is None and result["repair_rounds_used"] == 0
    assert result["replay_authorized"] is False
    assert (f.root / "evidence/executor.stdout.log").read_bytes() == raw
    assert (f.root / "evidence/source.before.json").is_file()
    assert (f.root / "evidence/source.after_executor.json").is_file()


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
