"""Offline provider fixtures; these are not live Antigravity permission evidence."""
import copy
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_commerce.engineering import runner
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.engineering import adapters


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git loose-object diagnostic")
def test_native_git_loose_object_rmtree_winerror5(tmp_path, record_property):
    """Real Git/NTFS discriminator; no provider, locks, ACL or Defender inference."""
    from test_ef01 import _repo
    tree = tmp_path / "owned-git-repro"
    tree.mkdir()
    repo = _repo(tree)
    child = subprocess.Popen(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = child.communicate(timeout=10)
    assert child.returncode == 0 and len(stdout.strip()) == 40
    with pytest.raises(PermissionError) as raised:
        shutil.rmtree(tree)
    failure = raised.value
    target = Path(failure.filename)
    assert tree in target.parents and ".git" in target.parts and "objects" in target.parts
    info = target.lstat()
    diagnostic = {"winerror": failure.winerror, "readonly": bool(info.st_file_attributes & 1),
                  "reparse": bool(info.st_file_attributes & 0x400), "mode": info.st_mode,
                  "links": info.st_nlink, "file_identity": [info.st_dev, info.st_ino],
                  "worker": os.environ.get("PYTEST_XDIST_WORKER"), "pid": os.getpid(),
                  "git_child_pid": child.pid, "git_child_exit": child.returncode,
                  "classification": "READONLY_GIT_LOOSE_OBJECT_REPRODUCED"}
    assert diagnostic["winerror"] == 5 and diagnostic["readonly"] and not diagnostic["reparse"]
    record_property("native_scratch_discriminator", json.dumps(diagnostic, sort_keys=True))
    print(json.dumps(diagnostic, sort_keys=True))
    identity, parent_identity = tree.stat(), tmp_path.stat()
    result = adapters.agy_remove_owned_tree(tree, tmp_path,
        (identity.st_dev, identity.st_ino), (parent_identity.st_dev, parent_identity.st_ino))
    assert result["status"] == "CLEAN" and result["readonly_files_cleared"] >= 1
    assert not tree.exists()


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


def run_fixture(tmp_path, monkeypatch, events, *, exit_code=0, verify_code=0, lifecycle_result=None):
    from test_ef01 import _fixture_agy_process
    def process_fixture(command, *, lifecycle, **kwargs):
        result = _fixture_agy_process(command, lifecycle=lifecycle, **kwargs)
        lifecycle.update(lifecycle_result or {})
        return result
    monkeypatch.setattr(runner, "_run_agy_owned", process_fixture)
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
            return subprocess.CompletedProcess(argv, verify_code,
                "MT01_XDIST_WORKERS_COLLECTED=12\n" + ("1 failed\n" if verify_code else "1 passed\n"), "")
        return original_run(argv, **kwargs)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="fixture", attempt_id="fixture-attempt", instruction="create changed.txt",
        selection=ExecutorSelection("antigravity", "fixture-model", "high"),
        verify_argv=[sys.executable, "-m", "pytest", "tests", "-q"],
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


def owned_cleanup(path):
    info, parent = path.lstat(), path.parent.lstat()
    return adapters.agy_remove_owned_tree(path, path.parent,
        (info.st_dev, info.st_ino), (parent.st_dev, parent.st_ino))


@pytest.mark.skipif(os.name != "nt", reason="native Windows sharing semantics")
@pytest.mark.parametrize("release", [True, False])
def test_native_held_open_file_cleanup_is_bounded_and_does_not_change_acl(tmp_path, release):
    """CreateFile without FILE_SHARE_DELETE discriminates an actual open handle."""
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
    tree = tmp_path / "owned"
    tree.mkdir()
    target = tree / "held.txt"
    target.write_bytes(b"locked")
    handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    timer = None
    if release:
        timer = threading.Timer(0.12, lambda: kernel.CloseHandle(handle))
        timer.start()
    try:
        result = owned_cleanup(tree)
        assert result["attempts"] > 1 and result["readonly_files_cleared"] == 0
        assert result["diagnostics"][0]["winerror"] == 32
        assert result["status"] == ("CLEAN" if release else "HOLD")
        if not release:
            assert result["attempts"] == 5 and target.read_bytes() == b"locked"
    finally:
        if timer:
            timer.join(timeout=2)
        else:
            kernel.CloseHandle(handle)
        if tree.exists():
            assert owned_cleanup(tree)["status"] == "CLEAN"


@pytest.mark.skipif(os.name != "nt", reason="fault injection around native Windows handles")
@pytest.mark.parametrize("permanent", [False, True])
def test_mock_transient_scanner_style_vs_permanent_access_denied(tmp_path, monkeypatch, permanent):
    """WinError5 injection fixture; this is explicitly NOT Defender evidence."""
    tree = tmp_path / "owned"
    tree.mkdir()
    (tree / "credential-do-not-log.txt").write_bytes(b"opaque")
    original = adapters._AgyCleanupHandle.delete
    failures = []
    def denied(handle, info, report):
        if permanent or len(failures) < 2:
            failures.append(1)
            error = PermissionError(13, "SECRET path must not escape into diagnostics")
            error.winerror = 5
            raise error
        return original(handle, info, report)
    with monkeypatch.context() as patch:
        patch.setattr(adapters._AgyCleanupHandle, "delete", denied)
        result = owned_cleanup(tree)
    assert result["attempts"] == (5 if permanent else 3)
    assert result["status"] == ("HOLD" if permanent else "CLEAN")
    assert all(d["classification"] == "ACCESS_DENIED_UNATTRIBUTED" for d in result["diagnostics"])
    assert "SECRET" not in json.dumps(result) and "credential" not in json.dumps(result)
    if permanent:
        assert tree.exists() and owned_cleanup(tree)["status"] == "CLEAN"


def test_multiple_scratch_roots_do_not_cross_delete_or_reuse_identity(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    first, second = [], []
    with runner._agy_temp_tree(repo, "agy_scratch-", cleanup_receipts=second) as two:
        (two / "sentinel").write_bytes(b"second")
        with runner._agy_temp_tree(repo, "agy_scratch-", cleanup_receipts=first) as one:
            (one / "sentinel").write_bytes(b"first")
        assert not one.exists() and (two / "sentinel").read_bytes() == b"second"
    assert not two.exists() and first[0]["status"] == second[0]["status"] == "CLEAN"
    assert first[0]["root_token_sha256"] != second[0]["root_token_sha256"]


def test_nested_reparse_cleanup_never_touches_external_readonly_file(tmp_path):
    from test_ef01 import _directory_alias
    import stat
    tree, outside = tmp_path / "owned", tmp_path / "outside"
    (tree / "nested").mkdir(parents=True)
    outside.mkdir()
    target = outside / "sentinel"
    target.write_bytes(b"keep")
    if os.name == "nt":
        os.chmod(target, stat.S_IREAD)  # exact fixture file, not an arbitrary parent
    link = tree / "nested/escape"
    try:
        _directory_alias(outside, link)
    except PermissionError:
        pytest.skip("native directory reparse creation denied; no bypass")
    before = target.lstat()
    result = owned_cleanup(tree)
    assert target.read_bytes() == b"keep"
    assert getattr(before, "st_file_attributes", 0) == getattr(target.lstat(), "st_file_attributes", 0)
    assert result["status"] in ("CLEAN", "HOLD")
    if result["status"] == "CLEAN":
        assert result["reparse_links_removed"] == 1
    else:
        # A filesystem that cannot attest/delete the link handle must retain
        # it and HOLD, never traverse its target as a cleanup workaround.
        assert adapters.is_link(link)


def test_root_reparse_replacement_is_preserved_and_never_followed(tmp_path, monkeypatch):
    from test_ef01 import _directory_alias
    repo, outside = tmp_path / "source", tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"keep")
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    try:
        with pytest.raises(runner._AgyScratchCleanupError):
            with runner._agy_temp_tree(repo, "agy_scratch-") as root:
                root.rename(tmp_path / "original-root")
                _directory_alias(outside, root)
        assert root.exists() and (outside / "sentinel").read_bytes() == b"keep"
    finally:
        if 'root' in locals() and adapters.is_link(root):
            if root.is_symlink():
                root.unlink()
            else:
                root.rmdir()


@pytest.mark.parametrize("component", ["..", "file:stream"])
def test_cleanup_path_indirection_is_rejected_before_deletion(tmp_path, component):
    tree = tmp_path / "owned"
    tree.mkdir()
    (tree / "sentinel").write_bytes(b"keep")
    info, parent = tree.stat(), tmp_path.stat()
    result = adapters.agy_remove_owned_tree(tree / component, tmp_path,
        (info.st_dev, info.st_ino), (parent.st_dev, parent.st_ino))
    assert result["status"] == "HOLD" and (tree / "sentinel").read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job Object proof")
@pytest.mark.parametrize("mode", ["normal", "lingering", "abrupt", "timeout"])
def test_native_job_proves_children_empty_or_holds_before_verification(tmp_path, mode):
    lifecycle = {}
    code = "import subprocess,sys,os,time,json; "
    if mode in ("lingering", "abrupt", "timeout"):
        code += ("subprocess.Popen([sys.executable,'-I','-S','-c','import time; time.sleep(30)'],"
                 "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); ")
    code += "print(json.dumps({'status':'SUCCESS'}),flush=True); "
    if mode == "abrupt":
        code += "os._exit(137)"
    elif mode == "timeout":
        code += "time.sleep(30)"
    else:
        # Pending-task prose alone must not override native process accounting.
        code += "print('root agent idle; waiting for 1 background task(s) to finish',file=sys.stderr)"
    args = dict(lifecycle=lifecycle, cwd=tmp_path, env=os.environ.copy(), input=None,
                timeout=0.3 if mode == "timeout" else 10)
    if mode == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            runner._run_agy_owned([sys.executable, "-I", "-S", "-c", code], **args)
    else:
        result = runner._run_agy_owned([sys.executable, "-I", "-S", "-c", code], **args)
        assert result.returncode == (137 if mode == "abrupt" else 0)
    assert lifecycle["authority"] == "WINDOWS_JOB_OBJECT" and lifecycle["containment_proven"]
    assert lifecycle["tree_empty"] is True
    if mode == "normal":
        assert lifecycle["status"] == "RECONCILED" and not lifecycle["forced_termination"]
        assert lifecycle["after_grace"]["active"] == lifecycle["after_grace"]["pid_count"] == 0
    else:
        assert lifecycle["status"] == "HOLD" and lifecycle["forced_termination"]
        assert lifecycle["after_reconcile"]["active"] == lifecycle["after_reconcile"]["pid_count"] == 0


def test_tracking_unavailable_never_launches_provider(tmp_path, monkeypatch):
    def unavailable():
        raise PermissionError(13, "SECRET unavailable Windows job API")
    monkeypatch.setattr(runner, "_WindowsAgyJob", unavailable)
    lifecycle = {}
    marker = tmp_path / "provider-ran"
    with pytest.raises(runner._AgyProcessUnproven, match="AGY_PROCESS_TRACKING_UNAVAILABLE"):
        runner._run_agy_owned([sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
            lifecycle=lifecycle, cwd=tmp_path, env=os.environ.copy(), input=None, timeout=3)
    assert not marker.exists() and lifecycle["status"] == "HOLD"
    assert "SECRET" not in json.dumps(lifecycle)


@pytest.mark.parametrize("tree_empty", [False, True])
def test_fake_success_cannot_replace_process_proof(tmp_path, monkeypatch, tree_empty):
    result, calls = run_fixture(tmp_path, monkeypatch, [{"status": "SUCCESS"}],
        lifecycle_result={"status": "HOLD", "tree_empty": tree_empty,
                          "reason": "AGY_PROCESS_RECONCILIATION_UNPROVEN"})
    assert calls == ["provider"] and result["verify_exit_code"] is None
    assert result["status"] == "UNPROVEN" and result["replay_authorized"] is False
    assert "quarantine_manifest" in result["evidence_files"]
    assert result["scratch_teardown"]["status"] == ("CLEAN" if tree_empty else "HOLD")


@pytest.mark.parametrize("verify_code", [0, 1])
def test_cleanup_hold_preserves_separate_build_result_and_never_replays(tmp_path, monkeypatch, verify_code):
    original = runner.agy_remove_owned_tree
    def unproven(*args, **kwargs):
        result = original(*args, **kwargs)
        # Simulated cleanup uncertainty, independently labeled as a fixture.
        result.update(status="HOLD", reason="AGY_SCRATCH_CLEANUP_UNPROVEN", fixture=True)
        return result
    monkeypatch.setattr(runner, "agy_remove_owned_tree", unproven)
    result, calls = run_fixture(tmp_path, monkeypatch, [{"status": "SUCCESS"}], verify_code=verify_code)
    assert calls == ["provider", "verify"] and result["verify_completed"]
    assert result["verify_exit_code"] == verify_code and result["scratch_teardown"]["status"] == "HOLD"
    assert result["source_build_status"] == ("VERIFICATION_FAILED" if verify_code else "BUILD_VERIFIED_FOR_SCOPE")
    assert result["status"] == ("VERIFICATION_FAILED" if verify_code else "UNPROVEN")
    assert not result["invocations"][0]["repair_eligible"] and result["invocation_count"] == 1
    assert "quarantine_manifest" in result["evidence_files"]


def test_clean_teardown_does_not_bless_subsequent_source_change(tmp_path, monkeypatch):
    original = runner.agy_remove_owned_tree
    def late_change(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / "workspace/changed.txt").write_bytes(b"late mutation")
        return result
    monkeypatch.setattr(runner, "agy_remove_owned_tree", late_change)
    result, calls = run_fixture(tmp_path, monkeypatch, [{"status": "SUCCESS"}])
    assert calls == ["provider", "verify"] and result["verify_exit_code"] == 0
    assert result["status"] == "SOURCE_DRIFT" and result["error_code"] == "AGY_SOURCE_CHANGED_AFTER_TEARDOWN"
    assert result["scratch_teardown"]["status"] == "CLEAN" and "quarantine_manifest" in result["evidence_files"]


@pytest.mark.skipif(os.name != "nt", reason="native Git, Job Object and readonly teardown integration")
@pytest.mark.parametrize("lingering", [False, True])
def test_native_agy_fixture_end_to_end_readonly_git_and_lingering_writer(tmp_path, monkeypatch, lingering):
    from test_ef01 import _repo
    repo = _repo(tmp_path)
    marker = tmp_path / "verifier-ran"
    # The real Git fixture was created above under the inherited guard. Copy
    # its exact readonly object bytes/attributes into disposable provider TEMP;
    # do not bypass a Git/MSYS hook environment failure in a provider child.
    code = ("import os,sys,subprocess,json,shutil; from pathlib import Path; "
            "shutil.copytree(Path.cwd(),Path(os.environ['TEMP'])/'fixture'); "
            "Path('changed.txt').write_bytes(b'verified'); ")
    if lingering:
        child = "import time; from pathlib import Path; time.sleep(30); Path('changed.txt').write_bytes(b'late')"
        code += (f"subprocess.Popen([sys.executable,'-I','-S','-c',{child!r}],"
                 "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); ")
    code += "print(json.dumps({'status':'SUCCESS'}))"
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: ([sys.executable, "-c", code], None))
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="scratch-native", attempt_id="fresh-rootfix-native",
        selection=ExecutorSelection("antigravity", "gemini-4-argon-medium", "medium"),
        instruction="fixture source edit only", allowed_prefixes=["changed.txt"], max_repair_rounds=0,
        verify_argv=[sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_bytes(b'verified')"])
    assert result["process_lifecycle"]["authority"] == "WINDOWS_JOB_OBJECT"
    assert result["process_lifecycle"]["tree_empty"]
    assert result["scratch_teardown"]["status"] == "CLEAN"
    assert any(r["readonly_files_cleared"] for r in result["scratch_teardown"]["roots"])
    assert marker.exists() is (not lingering)
    assert result["status"] == ("UNPROVEN" if lingering else "BUILD_VERIFIED_FOR_SCOPE")
    assert result["model"] == "gemini-4-argon-medium" and result["reasoning_effort"] == "medium"
    if lingering:
        assert result["error_code"] == "AGY_DESCENDANTS_STILL_ACTIVE"
        assert result["verify_exit_code"] is None and "quarantine_manifest" in result["evidence_files"]
    else:
        after_verify = result["evidence_sha256"]["source_after_verify"]
        assert after_verify == result["evidence_sha256"]["source_after_teardown"]
        assert result["verify_exit_code"] == 0


def _measured_tree(tmp_path, files=12, depth=2):
    tree = tmp_path / "measured"
    leaf = tree
    for level in range(depth):
        leaf = leaf / f"d{level}"
    leaf.mkdir(parents=True)
    for index in range(files):
        (leaf / f"f{index}.txt").write_bytes(b"x")
    identity, parent = tree.stat(), tmp_path.stat()
    return tree, (identity.st_dev, identity.st_ino), (parent.st_dev, parent.st_ino)


def test_cleanup_reports_sanitized_measurements(tmp_path):
    tree, identity, parent = _measured_tree(tmp_path)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    measured = result["measurements"]
    assert result["status"] == "CLEAN" and not tree.exists()
    assert measured["entries_visited"] == 12 + 2 + 1
    assert measured["deleted_entries"] == 15 and measured["max_depth"] == 3
    assert 0 <= measured["elapsed_seconds"] <= measured["budget_seconds"]
    assert measured["entry_limit"] == adapters.AGY_CLEANUP_MAX_ENTRIES
    assert measured["depth_limit"] == adapters.AGY_CLEANUP_MAX_DEPTH
    assert str(tmp_path) not in json.dumps(result)


def test_cleanup_zero_budget_holds_immediately(tmp_path):
    tree, identity, parent = _measured_tree(tmp_path)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent, timeout_seconds=0)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_SCRATCH_CLEANUP_TIME_EXCEEDED"
    assert result["measurements"]["budget_seconds"] == 0.0
    assert tree.exists()


@pytest.mark.parametrize("invalid_timeout", [
    float("nan"),
    float("inf"),
    float("-inf"),
    True,
    False,
    -1.0,
    -0.001,
    adapters.AGY_CLEANUP_MAX_SECONDS + 0.1,
    10**9,
    10**400,  # Regression: math.isnan(huge_int) formerly raised OverflowError.
    -(10**400),
    10**1000,
    "30",
    None,
    [],
])
def test_cleanup_rejects_nonfinite_and_invalid_timeout(tmp_path, invalid_timeout):
    tree, identity, parent = _measured_tree(tmp_path)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent, timeout_seconds=invalid_timeout)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_SCRATCH_CLEANUP_TIMEOUT_INVALID"
    assert result["attempts"] == 0 and result["diagnostics"] == []
    assert tree.exists()


def test_cleanup_valid_custom_budget(tmp_path):
    tree, identity, parent = _measured_tree(tmp_path)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent, timeout_seconds=15.0)
    assert result["status"] == "CLEAN" and not tree.exists()
    assert result["measurements"]["budget_seconds"] == 15.0


def test_cleanup_entry_and_depth_limits_are_separate_reasons(tmp_path, monkeypatch):
    tree, identity, parent = _measured_tree(tmp_path)
    monkeypatch.setattr(adapters, "AGY_CLEANUP_MAX_ENTRIES", 5)
    entries = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    assert entries["status"] == "HOLD"
    assert entries["reason"] == "AGY_SCRATCH_CLEANUP_ENTRY_LIMIT_EXCEEDED"
    assert tree.exists()
    monkeypatch.setattr(adapters, "AGY_CLEANUP_MAX_ENTRIES", 100_000)
    monkeypatch.setattr(adapters, "AGY_CLEANUP_MAX_DEPTH", 1)
    depth = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    assert depth["status"] == "HOLD"
    assert depth["reason"] == "AGY_SCRATCH_CLEANUP_DEPTH_LIMIT_EXCEEDED"
    assert tree.exists()


def test_cleanup_residual_present_holds(tmp_path, monkeypatch):
    tree, identity, parent = _measured_tree(tmp_path)
    if os.name == "nt":
        monkeypatch.setattr(adapters._AgyCleanupHandle, "delete", lambda self, info, report: None)
    else:
        monkeypatch.setattr(Path, "rmdir", lambda self: None)
        monkeypatch.setattr(Path, "unlink", lambda self: None)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_SCRATCH_RESIDUAL_PRESENT"
    assert tree.exists()


@pytest.mark.skipif(os.name != "nt", reason="kernel32 DLL is Windows-only")
def test_agy_kernel_thread_safety(monkeypatch):
    monkeypatch.setattr(adapters, "_AGY_KERNEL", None)
    results = []
    def worker():
        results.append(adapters._agy_kernel())
    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 16
    first = results[0]
    assert all(r is first for r in results)
    assert first is adapters._AGY_KERNEL


@pytest.mark.skipif(os.name != "nt", reason="native Windows performance and Git loose objects")
def test_native_medium_large_scratch_scope_proof(tmp_path):
    """Host-owned targeted fixture with >=1,000 ordinary scratch leaves,

    moderate nested tree, multiple read-only Git loose-object-like files,
    and observed elapsed/entries/depth/final root ABSENT.
    """
    tree = tmp_path / "large_scratch"
    tree.mkdir()
    total_files = 0
    readonly_count = 0
    for d_idx in range(10):
        branch = tree / f"dir_{d_idx:02d}" / "sub"
        branch.mkdir(parents=True)
        for f_idx in range(100):
            file_path = branch / f"leaf_{f_idx:03d}.tmp"
            file_path.write_bytes(b"disposable payload")
            total_files += 1
            if f_idx < 2:  # 2 per dir * 10 dirs = 20 read-only files
                os.chmod(file_path, stat.S_IREAD)
                readonly_count += 1
    assert total_files == 1000
    assert readonly_count == 20

    identity = tree.stat()
    parent_identity = tmp_path.stat()
    result = adapters.agy_remove_owned_tree(
        tree, tmp_path,
        (identity.st_dev, identity.st_ino),
        (parent_identity.st_dev, parent_identity.st_ino)
    )

    assert result["status"] == "CLEAN"
    assert result["reason"] is None
    assert not tree.exists()
    assert result["readonly_files_cleared"] == 20
    measurements = result["measurements"]
    assert measurements["entries_visited"] == 1021
    assert measurements["deleted_entries"] == 1021
    assert measurements["max_depth"] == 3
    assert measurements["elapsed_seconds"] >= 0.0
    assert measurements["elapsed_seconds"] <= measurements["budget_seconds"]
    assert str(tmp_path) not in json.dumps(result)

