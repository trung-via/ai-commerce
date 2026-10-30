"""Bounded EF01 repair and EF02 aggregation: local Python fakes, never vendors."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_commerce.engineering import runner
from ai_commerce.engineering.cli import build_parser
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.remote import worker as remote
from ai_commerce.remote.scheduler import run_schedule
from ai_commerce.remote.validation import validate_batch

BASE = "a" * 40
ROOT = Path(__file__).resolve().parents[1]
VERIFY = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_scope.py"]


def _files(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(repo).parts
            and "__pycache__" not in p.parts}


def _snapshot_fake(repo, baseline, state):
    """Model Git observations; file bytes and provider/verifier children are real."""
    current = _files(repo)
    changed = sorted(p for p in set(current) | set(baseline) if current.get(p) != baseline.get(p))
    hashes = {p: hashlib.sha256(current[p]).hexdigest() if p in current else None for p in changed}
    diff = json.dumps(hashes, sort_keys=True).encode()
    return {
        "git_head_after": state.get("head", BASE), "git_dir": state.get("git_dir", str(repo / ".git")),
        "changed_files": changed, "candidate_file_sha256": hashes,
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "scope_valid": runner._safe_scope(changed, state["scope"]),
    }, diff


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    repo = tmp_path / "workspace"
    repo.mkdir()
    (repo / "test_scope.py").write_text(
        "from pathlib import Path\ndef test_value():\n"
        "    assert Path('impl.py').read_text() == 'good'\n", encoding="utf-8")
    baseline = _files(repo)
    state = {"scope": ["impl.py", "kept.txt"]}
    monkeypatch.setattr(runner, "_snapshot", lambda r, _scope, _d: _snapshot_fake(r, baseline, state))
    return SimpleNamespace(repo=repo, root=tmp_path, state=state, monkeypatch=monkeypatch)


def _provider(fixture, mode="pass-after-repair", executor="codex"):
    script = fixture.root / "fake_executor.py"
    calls = fixture.root / "calls.jsonl"
    script.write_text("""import json,sys
from pathlib import Path
calls = Path(sys.argv[1])
previous = calls.read_text().splitlines() if calls.exists() else []
index = len(previous)
prompt = sys.stdin.read()
with calls.open('a', encoding='utf-8') as f:
    f.write(json.dumps({'index':index,'profile':sys.argv[3:6],
                        'cwd':str(Path.cwd()),'prompt':prompt}) + '\\n')
mode = sys.argv[2]
if mode != 'no-change':
    Path('impl.py').write_text('good' if ((index and mode in ('pass-after-repair', 'scope-fix')) or mode == 'scope-pass') else 'bad-' + str(index))
    if index == 0:
        Path('kept.txt').write_text('initial cumulative change')
if mode in ('scope', 'scope-pass') or (mode == 'scope-fix' and index == 0):
    Path('forbidden.txt').write_text('out of scope')
elif mode == 'scope-fix' and Path('forbidden.txt').exists():
    Path('forbidden.txt').unlink()
if mode == 'nonzero':
    print('provider error',file=sys.stderr)
    sys.exit(7)
if mode == 'permission':
    print(json.dumps({'status':'SUCCESS','denied_actions':[{'action':'command'}]}))
elif mode in ('capacity','quota','unavailable','unknown'):
    messages = {'capacity':'Selected model is at capacity','quota':'quota exceeded',
                'unavailable':'model unavailable','unknown':'outcome unclear'}
    print(json.dumps({'type':'turn.failed','error':{'message':messages[mode]}}))
else:
    print(json.dumps({'type':'turn.completed','usage':{
        'input_tokens':10,'cached_input_tokens':2,'output_tokens':3}}))
""", encoding="utf-8")
    profiles = []

    def command(selection, repo, prompt, **kwargs):
        profiles.append((selection, repo, kwargs))
        return [sys.executable, str(script), str(calls), mode,
                selection.executor, selection.model, selection.effort], prompt

    fixture.monkeypatch.setattr(runner, "command_for", command)
    selection = ExecutorSelection(executor, "selected-model", "high")
    return selection, calls, profiles


def _run(fixture, mode="pass-after-repair", executor="codex", **kwargs):
    selection, calls, profiles = _provider(fixture, mode, executor)
    result = runner.execute_attempt(
        repo=fixture.repo, output_dir=fixture.root / "evidence", work_id="approved-work",
        attempt_id="immutable-attempt", selection=selection, instruction="Make the scoped change",
        verify_argv=kwargs.pop("verify_argv", VERIFY), allowed_prefixes=fixture.state["scope"], **kwargs)
    observations = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    return result, observations, profiles


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_fail_repair_pass_same_profile_worktree_and_immutable_evidence(fixture, executor):
    sandbox = "mxc" if executor == "codex" else None
    result, calls, profiles = _run(fixture, executor=executor, codex_windows_sandbox=sandbox)
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["verify_exit_code"] == 0
    assert result["repair_rounds_used"] == 1 and result["invocation_count"] == 2
    assert result["source_base_sha"] == result["git_head_after"] == BASE
    assert result["changed_files"] == ["impl.py", "kept.txt"]
    assert (fixture.repo / "kept.txt").read_text() == "initial cumulative change"
    assert len(calls) == 2 and len({c["cwd"] for c in calls}) == 1
    assert all(c["profile"] == [executor, "selected-model", "high"] for c in calls)
    assert all(p[2]["codex_windows_sandbox"] == sandbox for p in profiles)
    rounds = result["invocations"]
    assert [r["verify_exit_code"] for r in rounds] == [1, 0]
    assert len({r["invocation_id"] for r in rounds}) == 2
    assert len({r["evidence_dir"] for r in rounds}) == 2
    evidence = fixture.root / "evidence"
    original_files = {}
    for inv in rounds:
        persisted = json.loads((evidence / inv["result_file"]).read_text())
        assert inv == persisted
        assert inv["started_at"] <= inv["executor_started_at"] <= inv["executor_finished_at"]
        assert inv["verify_started_at"] <= inv["verify_finished_at"] <= inv["finished_at"]
        assert inv["source_base_sha"] == BASE and inv["executor_exit_code"] == 0
        for key, relative in inv["evidence_files"].items():
            data = (evidence / relative).read_bytes()
            assert hashlib.sha256(data).hexdigest() == inv["evidence_sha256"][key]
            original_files[relative] = data
    feedback = json.loads((evidence / rounds[1]["evidence_files"]["repair_feedback"]).read_text())
    previous = feedback["previous_invocations"][0]
    assert previous["verify_exit_code"] == 1 and "test_value" in previous["verify_failure_details"]
    assert previous["candidate_file_sha256"]["impl.py"] != result["candidate_file_sha256"]["impl.py"]
    assert feedback["allowed_prefixes"] == fixture.state["scope"]
    assert "fresh CLI invocation" in calls[1]["prompt"]
    assert previous["evidence_files"]["verify_stderr"] in calls[1]["prompt"]
    assert (evidence / "executor.stdout.log").read_bytes() == original_files[rounds[0]["evidence_files"]["executor_stdout"]]
    assert len(list(evidence.rglob("engineering_result.json"))) == 1
    assert result["usage"] == {"input_tokens": 20, "cached_input_tokens": 4, "output_tokens": 6}
    assert result["cost"] is None and result["cost_status"] == "UNKNOWN"
    with pytest.raises(FileExistsError, match="EVIDENCE_EXISTS"):
        runner.execute_attempt(
            repo=fixture.repo, output_dir=evidence, work_id="approved-work",
            attempt_id="immutable-attempt", selection=ExecutorSelection(executor, "selected-model", "high"),
            instruction="no replay", verify_argv=VERIFY)
    assert all((evidence / name).read_bytes() == data for name, data in original_files.items())


@pytest.mark.parametrize("rounds,expected_calls,reason", [
    (0, 1, "REPAIRS_DISABLED"), (1, 2, "REPAIR_ROUNDS_EXHAUSTED"), (2, 3, "REPAIR_ROUNDS_EXHAUSTED"),
])
def test_exhaustion_and_disable_preserve_all_failures(fixture, rounds, expected_calls, reason):
    result, calls, _ = _run(fixture, mode="always-fail", max_repair_rounds=rounds)
    assert result["status"] == "VERIFICATION_FAILED"
    assert result["stop_reason"] == reason and len(calls) == expected_calls
    assert len(result["invocations"]) == expected_calls
    assert all(i["verify_exit_code"] == 1 and "test_value" in i["verify_failure_details"]
               for i in result["invocations"])
    if rounds == 2:
        feedback_path = result["invocations"][2]["evidence_files"]["repair_feedback"]
        feedback = json.loads((fixture.root / "evidence" / feedback_path).read_text())
        assert len(feedback["previous_invocations"]) == 2


@pytest.mark.parametrize("mode,status", [
    ("nonzero", "EXECUTOR_FAILED"), ("permission", "EXECUTOR_PERMISSION_DENIED"),
    ("capacity", "MODEL_AT_CAPACITY"), ("quota", "QUOTA_EXHAUSTED"),
    ("unavailable", "EXECUTOR_UNAVAILABLE"), ("unknown", "EXECUTION_UNKNOWN"),
    ("no-change", "NO_CODE_CHANGE"),
])
def test_provider_prohibitions_never_verify_or_reinvoke(fixture, mode, status):
    result, calls, _ = _run(fixture, mode=mode, executor="antigravity")
    assert len(calls) == 1 and result["status"] == status
    assert result["verify_exit_code"] is None and not result["invocations"][0]["repair_eligible"]


@pytest.mark.parametrize("kind,status", [
    ("timeout", "EXECUTION_UNKNOWN"), ("missing", "VERIFICATION_FAILED"),
    ("permission", "VERIFICATION_FAILED"), ("interrupted", "EXECUTION_UNKNOWN"),
    ("internal", "EXECUTION_UNKNOWN"), ("usage", "VERIFICATION_FAILED"),
    ("no-tests", "VERIFICATION_FAILED"), ("signal", "EXECUTION_UNKNOWN"),
    ("ambiguous", "VERIFICATION_FAILED"), ("missing-module", "VERIFICATION_FAILED"),
    ("stdout-permission", "VERIFICATION_FAILED"),
])
def test_verify_prohibitions_never_invoke_second_provider(fixture, kind, status):
    actual_run = subprocess.run

    def verify(argv, **kwargs):
        if argv != VERIFY:
            return actual_run(argv, **kwargs)
        if kind == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"partial stdout", stderr=b"partial stderr")
        if kind == "missing":
            raise FileNotFoundError("pytest missing")
        if kind == "permission":
            raise PermissionError("verification permission denied")
        code = {"interrupted": 2, "internal": 3, "usage": 4, "no-tests": 5, "signal": -9}.get(kind, 1)
        err = "No module named pytest" if kind == "missing-module" else ""
        out = "FAILED test_scope.py::test_value PermissionError: denied" if kind == "stdout-permission" else "no confirmed failure details"
        return subprocess.CompletedProcess(argv, code, out, err)

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=verify, TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess))
    result, calls, _ = _run(fixture)
    assert len(calls) == 1 and result["status"] == status
    assert not result["invocations"][0]["repair_eligible"]
    if kind == "timeout":
        files = result["invocations"][0]["evidence_files"]
        assert (fixture.root / "evidence" / files["verify_stdout"]).read_bytes() == b"partial stdout"
        assert (fixture.root / "evidence" / files["verify_stderr"]).read_bytes() == b"partial stderr"


@pytest.mark.parametrize("kind,status", [
    ("head", "GIT_UNEXPECTED_COMMIT"), ("identity", "SOURCE_DRIFT"),
    ("scope", "SOURCE_DRIFT"), ("scoped-source", "SOURCE_DRIFT"), ("undo", "NO_CODE_CHANGE"),
])
def test_verifier_mutation_or_source_drift_never_repairs_or_passes(fixture, kind, status):
    actual_run = subprocess.run

    def verify(argv, **kwargs):
        if argv != VERIFY:
            return actual_run(argv, **kwargs)
        if kind == "head":
            fixture.state["head"] = "b" * 40
        elif kind == "identity":
            fixture.state["git_dir"] = "different-worktree"
        elif kind == "scope":
            (fixture.repo / "forbidden.txt").write_text("mutation")
        elif kind == "scoped-source":
            (fixture.repo / "impl.py").write_text("unverified mutation")
        else:
            (fixture.repo / "impl.py").unlink()
            (fixture.repo / "kept.txt").unlink()
        return subprocess.CompletedProcess(argv, 1, "FAILED test_scope.py::test_value", "")

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=verify, TimeoutExpired=subprocess.TimeoutExpired))
    result, calls, _ = _run(fixture)
    assert result["status"] == status and len(calls) == 1


def test_scope_verify_first_pass_keeps_evidence_without_candidate_permission(fixture):
    result, calls, _ = _run(fixture, mode="scope-pass", max_repair_rounds=1)
    assert len(calls) == 1
    assert result["status"] == "VERIFIED_SCOPE_REVIEW_REQUIRED"
    assert result["error_code"] == "OUT_OF_SCOPE_FILES_REQUIRE_RECONCILIATION"
    assert result["verify_exit_code"] == 0
    assert result["scope_valid"] is False
    assert result["scope_review_required"] is True
    assert result["out_of_scope_files"] == ["forbidden.txt"]
    assert result["repair_rounds_used"] == 0


def test_scope_verify_first_fail_then_repair_to_scoped_pass(fixture):
    result, calls, _ = _run(fixture, mode="scope-fix", max_repair_rounds=2)
    assert len(calls) == 2
    assert [x["verify_exit_code"] for x in result["invocations"]] == [1, 0]
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["scope_valid"] is True
    assert result["out_of_scope_files"] == []
    assert result["repair_rounds_used"] == 1


def test_scope_verify_first_failed_tests_never_pass(fixture):
    result, calls, _ = _run(fixture, mode="scope", max_repair_rounds=1)
    assert len(calls) == 2
    assert result["status"] == "VERIFICATION_FAILED"
    assert result["scope_valid"] is False


def test_scope_strict_mode_still_rejects_before_pytest(fixture):
    result, calls, _ = _run(fixture, mode="scope-pass", verify_first_scope=False)
    assert len(calls) == 1
    assert result["status"] == "SCOPE_VIOLATION"
    assert result["verify_exit_code"] is None


@pytest.mark.parametrize("protected", [
    ".github/workflows/ef02-batch.yml",
    "src/ai_commerce/engineering/.env",
    "src/ai_commerce/engineering/.env.production",
    "src/ai_commerce/engineering/secrets/key.json",
    "src/ai_commerce/remote/credentials/auth.json",
    "src/ai_commerce/remote/.github/config.yml",
    "src/ai_commerce/remote/.local/runner.json",
    "src/ai_commerce/remote/.git/config",
])
def test_protected_changed_path_stops_before_verification(fixture, protected):
    # Simulate the path as permitted by a component-level allowlist, proving
    # the protected-path check has precedence over scope and pytest.
    original = runner._snapshot

    def blocked(repo, prefixes, deadline):
        snapshot, diff = original(repo, prefixes, deadline)
        if snapshot["changed_files"]:
            snapshot = dict(snapshot, changed_files=[*snapshot["changed_files"],
                            protected], scope_valid=True)
        return snapshot, diff

    fixture.monkeypatch.setattr(runner, "_snapshot", blocked)
    result, calls, _ = _run(fixture, mode="scope-pass")
    assert len(calls) == 1
    assert result["status"] == "SCOPE_VIOLATION"
    assert result["error_code"] == "PROTECTED_CHANGED_PATH"
    assert result["verify_exit_code"] is None


def test_protected_path_matching_does_not_block_ordinary_component_files():
    assert not runner._protected_change([
        "src/ai_commerce/engineering/feature.py",
        "tests/test_environment.py",
        "src/ai_commerce/remote/secrets_provider.py",
    ])
    for path in ("src/a/.env", "src/a/secrets/token", "src/a/credentials/key",
                 "src/a/.github/job.yml", "src/a/.local/data", "src/a/.git/config"):
        assert runner._protected_change([path])


def test_non_pytest_nonzero_is_not_repairable(fixture):
    result, calls, _ = _run(fixture, verify_argv=[sys.executable, "-c", "print('1 failed');raise SystemExit(1)"])
    assert result["status"] == "VERIFICATION_FAILED" and len(calls) == 1


def test_no_change_repair_cannot_claim_flaky_success(fixture):
    selection, calls, _ = _provider(fixture, mode="always-fail")
    actual_run = subprocess.run

    def execute(argv, **kwargs):
        if argv == VERIFY:
            return subprocess.CompletedProcess(argv, 1, "FAILED test_scope.py::test_value", "")
        if calls.exists():
            # Model returns normally without modifying its previous candidate.
            return subprocess.CompletedProcess(argv, 0, "", "")
        return actual_run(argv, **kwargs)

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result = runner.execute_attempt(
        repo=fixture.repo, output_dir=fixture.root / "evidence", work_id="w", attempt_id="a",
        selection=selection, instruction="change", verify_argv=VERIFY, allowed_prefixes=fixture.state["scope"])
    assert result["invocation_count"] == 2
    assert result["status"] == "NO_CODE_CHANGE" and result["error_code"] == "REPAIR_NO_CODE_CHANGE"


@pytest.mark.parametrize("fault", ["timeout", "interruption", "signal", "shell-interruption"])
def test_provider_ambiguity_preserves_boundary_and_never_reverifies(fixture, fault):
    attempts = []
    selection, _calls, _ = _provider(fixture)

    def execute(argv, **kwargs):
        attempts.append(argv)
        if fault == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"out", stderr=b"err")
        if fault == "interruption":
            raise SystemExit("parent lost")
        return subprocess.CompletedProcess(argv, 130 if fault == "shell-interruption" else -15, "", "")

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    args = dict(repo=fixture.repo, output_dir=fixture.root / "evidence", work_id="w", attempt_id="a",
                selection=selection, instruction="change", verify_argv=VERIFY)
    if fault == "interruption":
        with pytest.raises(SystemExit, match="parent lost"):
            runner.execute_attempt(**args)
        result = json.loads((fixture.root / "evidence/engineering_result.json").read_text())
    else:
        result = runner.execute_attempt(**args)
    assert result["status"] == "EXECUTION_UNKNOWN" and len(attempts) == 1
    assert result["verify_exit_code"] is None and result["replay_authorized"] is False
    boundary = json.loads((fixture.root / "evidence/provider_boundary.json").read_text())
    assert boundary["status"] == "EXECUTION_UNKNOWN" and boundary["replay_authorized"] is False


def test_round_evidence_publication_failure_stops_before_repair(fixture):
    original = runner._write

    def write(path, data):
        if path.name == "invocation_result.json":
            raise OSError("evidence device failed")
        return original(path, data)

    fixture.monkeypatch.setattr(runner, "_write", write)
    result, calls, _ = _run(fixture)
    assert len(calls) == 1 and result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EVIDENCE_WRITE_FAILED"
    assert (fixture.root / "evidence/engineering_result.json").exists()


def test_shared_action_deadline_caps_provider_and_verify_without_second_call(fixture):
    clock = SimpleNamespace(now=0.0)
    fixture.monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock.now))
    actual_run = subprocess.run
    timeouts = []

    def execute(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        if argv == VERIFY:
            clock.now += 6
            return subprocess.CompletedProcess(argv, 1, "FAILED test_scope.py::test_value", "")
        result = actual_run(argv, **kwargs)
        clock.now += 4
        return result

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result, calls, _ = _run(fixture, timeout_seconds=7, verify_timeout_seconds=8, action_timeout_seconds=10)
    assert timeouts == [7, 6] and len(calls) == 1
    assert result["status"] == "EXECUTION_UNKNOWN" and result["error_code"] == "ACTION_TIMEOUT"


@pytest.mark.parametrize("value", [-1, 6, True, 1.0, "2"])
def test_invalid_repair_bound_rejected_before_execution(fixture, value):
    with pytest.raises(ValueError, match="max_repair_rounds"):
        _run(fixture, max_repair_rounds=value)
    assert not (fixture.root / "calls.jsonl").exists()


def _action(name, depends=(), **extra):
    return dict(action_id=name, attempt_id=name + "-001", executor="codex",
                model="selected-model", effort="high", instruction="change " + name,
                allowed_paths=["impl.py", "kept.txt"], verification_profile="scope",
                depends_on=list(depends), **extra)


def test_optional_contract_bounds_fingerprint_and_cli_compatibility():
    from ai_commerce.remote.validation import fingerprint
    from jsonschema.exceptions import ValidationError
    packet = dict(kind="AI_COMMERCE_REMOTE_ACTION_BATCH", version=1, batch_id="repair",
                  source_sha=BASE, actor="HUMAN", authorization_ref="existing-human-approval",
                  actions=[_action("a")])
    kwargs = dict(schema=ROOT / "contracts/RemoteActionBatch.v1.schema.json",
                  registry={"scope": VERIFY}, issuer_login="owner", owner_login="owner")
    assert validate_batch(packet, **kwargs) is packet
    original = fingerprint(packet)
    for value in (0, 2, 5):
        packet["actions"][0]["max_repair_rounds"] = value
        assert validate_batch(packet, **kwargs) is packet
        assert fingerprint(packet) != original
    for value in (-1, 6, True, 1.5, "2"):
        packet["actions"][0]["max_repair_rounds"] = value
        with pytest.raises(ValidationError):
            validate_batch(packet, **kwargs)
    packet["actions"][0]["max_repair_rounds"] = 1.0
    with pytest.raises(ValueError, match="INVALID_MAX_REPAIR_ROUNDS"):
        validate_batch(packet, **kwargs)
    argv = ["--repo", ".", "--work-id", "w", "--attempt-id", "a", "--executor", "codex",
            "--model", "m", "--effort", "high", "--instruction-file", "p",
            "--verify-json", "v", "--out-dir", "o"]
    parser = build_parser()
    assert parser.parse_args(argv).max_repair_rounds == 2
    assert parser.parse_args(argv + ["--max-repair-rounds", "0"]).max_repair_rounds == 0
    assert parser.parse_args(argv).action_timeout_seconds == 7200
    with pytest.raises(SystemExit):
        parser.parse_args(argv + ["--max-repair-rounds", "6"])


# Integration below uses real independent Git worktrees, local Python executors,
# and an in-memory GitHub transport. It creates no refs in the source repository.
def _git_repo(tmp_path, real_git=True):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_scope.py").write_text(
        "from pathlib import Path\ndef test_value():\n"
        "    assert Path('impl.py').read_text() == 'good'\n", encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.local/\n")
    # This fixture must be independent of pytest configuration above tmp_path.
    # The observation-only Git model deliberately sees every fixture file;
    # disable pytest's writable cache rather than hide a real source mutation.
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    if not real_git:
        return repo, BASE
    for args in (["init", "-q"], ["config", "user.email", "local@example.invalid"],
                 ["config", "user.name", "repair fixture"], ["add", "."], ["commit", "-qm", "initial"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    return repo, head


def _worker_fixture(tmp_path, monkeypatch, fail=(), publication_error=False, checkpoint_error=False, real_git=True):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    repo, head = _git_repo(tmp_path, real_git)
    monkeypatch.setattr(remote, 'load_approved_candidates', lambda *_a: {'version': 1, 'candidates': {}})
    worktree_run = subprocess.run
    if not real_git:
        import shutil
        baseline = _files(repo)

        def observed_snapshot(workspace, prefixes, _deadline):
            return _snapshot_fake(workspace, baseline, {"scope": prefixes})

        def worktree_run(argv, **kwargs):
            assert argv[:4] == ["git", "-C", str(repo), "worktree"] and argv[4] == "add"
            shutil.copytree(repo, Path(argv[6]))
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(remote, "check_source", lambda *_a: None)
        monkeypatch.setattr(remote, "_snapshot", observed_snapshot)
        monkeypatch.setattr(runner, "_snapshot", observed_snapshot)
        # Fake worktree add copies fixture files without creating a .git worktree.
        # Stub only the new checkout preflight here, not in real_git tests.
        monkeypatch.setattr(remote, "checkout_diagnostics", lambda *_a, **_kw: {
            "clean": True, "dirty_entries": [], "dirty_entry_count": 0,
            "index_refresh_exit": None, "metadata_only_reconciled": False,
        })
    lock = threading.Lock()
    calls, children, claims, candidates, resolved = [], [], [], [], {}

    class Github:
        def claim(self, **kwargs):
            with lock:
                if kwargs["attempt_id"] in claims:
                    return "DUPLICATE_CLAIM_NO_REEXECUTION"
                claims.append(kwargs["attempt_id"])
                return "CLAIMED"

        def integrated_source(self, **kwargs):
            resolved[kwargs["attempt_id"]] = kwargs["dependencies"]
            return {"effective_sha": head, "integration_ref": None}

        def pre_spawn_checkpoint(self, **kwargs):
            if checkpoint_error:
                raise OSError("claim checkpoint uncertain")
            return "mock/pre-spawn"

        def spawned_checkpoint(self, **kwargs):
            return "mock/spawned"

        def candidate(self, **kwargs):
            candidates.append(kwargs)
            assert kwargs["source_sha"] == head
            assert runner._candidate_hashes(kwargs["workspace"], kwargs["paths"]) == kwargs["expected_hashes"]
            if publication_error:
                raise OSError("candidate publication uncertain")
            return {"candidate_sha": hashlib.sha256(kwargs["attempt_id"].encode()).hexdigest()[:40],
                    "candidate_ref": "mock/candidate/" + kwargs["attempt_id"]}

    local_rounds = {}

    def command(selection, repo, prompt, **kwargs):
        with lock:
            # The first call validates adapter arguments before evidence creation.
            index = local_rounds.get(repo, -1)
            local_rounds[repo] = index + 1
            if index >= 0:
                calls.append((repo, selection, kwargs, prompt))
        aid = next(a for a in ("a", "b", "c") if ("change " + a) in prompt)
        content = "good" if index > 0 and aid not in fail else "bad-" + str(index)
        code = "from pathlib import Path; Path('impl.py').write_text(" + repr(content) + ");"
        if index == 0:
            code += "Path('kept.txt').write_text('preserve');"
        code += "print('{\\\"type\\\":\\\"turn.completed\\\"}')"
        return [sys.executable, "-c", code], None

    monkeypatch.setattr(runner, "command_for", command)

    class Child:
        pid = 12345
        returncode = None

        def __init__(self, argv, **kwargs):
            self.argv = argv
            children.append(self)

        def communicate(self, timeout=None):
            args = build_parser().parse_args(self.argv[3:])
            result = runner.execute_attempt(
                repo=args.repo, output_dir=args.out_dir, work_id=args.work_id,
                attempt_id=args.attempt_id, selection=ExecutorSelection(args.executor, args.model, args.effort),
                instruction=args.instruction_file.read_text(), verify_argv=json.loads(args.verify_json.read_text()),
                allowed_prefixes=args.allow_path, max_repair_rounds=args.max_repair_rounds,
                action_timeout_seconds=args.action_timeout_seconds, codex_windows_sandbox=args.codex_windows_sandbox,
                minimum_test=args.minimum_test, registered_only=args.registered_only,
                aggregate_sources=args.aggregate_source, host_slots=args.host_slots,
                registered_profiles=json.loads(args.registered_profiles_json.read_text())
                if args.registered_profiles_json else None)
            self.returncode = 0 if result["status"] == "BUILD_VERIFIED_FOR_SCOPE" else 1
            return "", ""

    monkeypatch.setattr(remote, "subprocess", SimpleNamespace(
        run=worktree_run, Popen=Child, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired))
    import tempfile
    monkeypatch.setattr(remote, "tempfile", SimpleNamespace(
        mkdtemp=lambda prefix: tempfile.mkdtemp(prefix=prefix, dir=tmp_path)))
    batch = dict(batch_id="repair-integration", source_sha=head, max_parallel=2, actions=[])
    worker = remote.LocalBatchWorker(checkout=repo, batch=batch,
                                    profiles={"scope": VERIFY, "project-tests": ["python", "-m", "pytest", "tests", "-q"]},
                                    github=Github(), root=tmp_path / "action-evidence")
    return SimpleNamespace(worker=worker, batch=batch, calls=calls, children=children,
                           claims=claims, candidates=candidates, resolved=resolved, head=head)


@pytest.fixture(params=[False, True], ids=["observations", "git"])
def real_git(request):
    return request.param


def test_ef02_single_claim_two_invocations_one_candidate_and_mapping(tmp_path, monkeypatch, real_git):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=real_git)
    action = _action("a")
    result = context.worker.execute_action(action)
    assert result["status"] == "CANDIDATE_READY"
    assert context.claims == ["a-001"] and len(context.children) == len(context.candidates) == 1
    assert len(context.calls) == 2
    assert result["repair_rounds_used"] == 1 and result["invocation_count"] == 2
    assert [i["verify_exit_code"] for i in result["ef01_invocations"]] == [1, 0]
    assert result["source_sha"] == result["effective_source_sha"] == context.head
    assert len({c[0] for c in context.calls}) == 1
    assert all(c[1] == ExecutorSelection("codex", "selected-model", "high") for c in context.calls)
    expected_sandbox = "mxc" if os.name == "nt" else None
    assert all(c[2]["codex_windows_sandbox"] == expected_sandbox for c in context.calls)
    assert len(list(context.worker.root.rglob("action_result.json"))) == 1
    assert len(list(context.worker.root.rglob("engineering_result.json"))) == 1
    assert context.worker.execute_action(action)["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert len(context.calls) == 2 and len(context.candidates) == 1


@pytest.mark.parametrize("rounds", [0, 1])
def test_ef02_exhaustion_never_publishes_candidate(tmp_path, monkeypatch, rounds, real_git):
    context = _worker_fixture(tmp_path, monkeypatch, fail=("a",), real_git=real_git)
    result = context.worker.execute_action(_action("a", max_repair_rounds=rounds))
    assert result["status"] == result["ef01_status"] == "VERIFICATION_FAILED"
    assert result["verify_exit_code"] == 1 and len(context.calls) == rounds + 1
    assert len(context.claims) == len(context.children) == 1 and not context.candidates
    argv = context.children[0].argv
    assert argv[argv.index("--max-repair-rounds") + 1] == str(rounds)


@pytest.mark.parametrize("error", ["checkpoint", "publication"])
def test_ef02_remote_uncertainty_or_publication_failure_never_replays(tmp_path, monkeypatch, error, real_git):
    context = _worker_fixture(tmp_path, monkeypatch, publication_error=error == "publication",
                              checkpoint_error=error == "checkpoint", real_git=real_git)
    action = _action("a")
    result = context.worker.execute_action(action)
    assert result["status"] == ("PRESPAWN_CHECKPOINT_UNCONFIRMED" if error == "checkpoint"
                                else "RESULT_PUBLICATION_FAILED")
    count = len(context.calls)
    assert count == (0 if error == "checkpoint" else 2)
    assert result["candidate_sha"] is None
    assert context.worker.execute_action(action)["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert len(context.calls) == count


def test_independent_siblings_repair_in_distinct_worktrees_and_block_only_failed_dag(tmp_path, monkeypatch, real_git):
    context = _worker_fixture(tmp_path, monkeypatch, fail=("a",), real_git=real_git)
    context.batch["actions"] = [_action("a", max_repair_rounds=1), _action("b"), _action("c", ("a", "b"))]
    result = run_schedule(context.batch, context.worker.execute_action)
    actions = {a["action_id"]: a for a in result["actions"]}
    assert result["status"] == "PARTIAL_OR_FAILED" and result["scheduler_max_active"] == 2
    assert actions["a"]["status"] == "VERIFICATION_FAILED", {
        "status": actions["a"]["status"], "error_code": actions["a"].get("error_code"),
        "diagnostic": actions["a"].get("diagnostic")}
    assert actions["b"]["status"] == "CANDIDATE_READY"
    assert actions["c"]["status"] == "BLOCKED_DEPENDENCY"
    assert actions["a"]["worktree"] != actions["b"]["worktree"]
    assert len(context.claims) == 2 and len(context.children) == 2 and len(context.candidates) == 1
    assert len(context.calls) == 4


def test_repaired_sibling_candidates_unblock_dag_with_pinned_dependency_results(tmp_path, monkeypatch, real_git):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=real_git)
    context.batch["actions"] = [_action("a"), _action("b"), _action("c", ("a", "b"))]
    result = run_schedule(context.batch, context.worker.execute_action)
    assert result["status"] == "COMPLETE" and len(context.candidates) == 3
    assert len({a["worktree"] for a in result["actions"]}) == 3
    pinned = context.resolved["c-001"]
    assert {p["candidate_sha"] for p in pinned} == {
        a["candidate_sha"] for a in result["actions"] if a["action_id"] in ("a", "b")}
    combined = next(a for a in result["actions"] if a["action_id"] == "c")
    assert combined["aggregate_verification"]["state"] == "VERIFIED_COMBINED_SCOPE"
    assert combined["aggregate_verification"]["sibling_pass_reusable"] is False
    assert all(g["actual_exit_code"] == 0 and g["state"] != "REUSE"
               for g in combined["ef01_invocations"][-1]["minimum_test"]["groups"])
    assert result["aggregate_verification"]["state"] == "UNPROVEN"

def test_live_event_mirror_timeout_includes_blocked_stdin(tmp_path):
    # Large repair feedback must not postpone starting the invocation timeout.
    live = tmp_path / "blocked-input.jsonl"
    with pytest.raises(subprocess.TimeoutExpired):
        runner._run_codex_with_live_event_mirror(
            [sys.executable, "-c", "import time;time.sleep(0.7)"],
            repo=tmp_path, stdin_text="x" * (1024 * 1024),
            timeout_seconds=0.1, live_path=live)

def test_source_drift_between_rounds_stops_before_second_provider(fixture):
    original = runner._write

    def write(path, data):
        original(path, data)
        if path.name == "invocation_result.json" and path.parent.name == "000":
            (fixture.repo / "impl.py").write_text("unexpected intervening edit")

    fixture.monkeypatch.setattr(runner, "_write", write)
    result, calls, _ = _run(fixture)
    assert len(calls) == result["invocation_count"] == 1
    assert result["status"] == "SOURCE_DRIFT"
    assert result["error_code"] == "WORKTREE_CHANGED_BETWEEN_INVOCATIONS"
    assert result["invocations"][1]["executor_invoked"] is False
    assert "provider_boundary" not in result["invocations"][1]["evidence_files"]


def test_ef02_failure_classifier_uses_last_invocation_evidence(tmp_path):
    (tmp_path / "executor.stderr.log").write_text("quota exceeded")
    last = tmp_path / "invocations/001"
    last.mkdir(parents=True)
    (last / "executor.stderr.log").write_text("Selected model is at capacity")
    report = {"status": "EXECUTOR_FAILED",
              "evidence_files": {"executor_stderr": "invocations/001/executor.stderr.log"}}
    assert remote._classify_failed_executor(report, tmp_path) == "MODEL_AT_CAPACITY"


@pytest.mark.parametrize("drift", ["head", "files", "hashes", "diff", "scope"])
def test_ef02_rechecks_final_tree_before_any_candidate_write(tmp_path, monkeypatch, drift):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    observed = remote._snapshot

    def snapshot(*args):
        tree, patch = observed(*args)
        key, value = {
            "head": ("git_head_after", "b" * 40),
            "files": ("changed_files", ["impl.py"]),
            "hashes": ("candidate_file_sha256", {"impl.py": "f" * 64, "kept.txt": "f" * 64}),
            "diff": ("tracked_diff_sha256", "f" * 64),
            "scope": ("scope_valid", False),
        }[drift]
        tree[key] = value
        return tree, patch

    monkeypatch.setattr(remote, "_snapshot", snapshot)
    result = context.worker.execute_action(_action("a"))
    assert result["status"] == "CANDIDATE_INTEGRITY_FAILED"
    assert result["error_code"] == "EF01_VERIFIED_TREE_CHANGED"
    assert len(context.calls) == 2 and not context.candidates and len(context.claims) == 1

def test_large_diagnostics_use_artifact_references_in_bounded_repair_prompt(fixture):
    actual_run = subprocess.run
    failure = "FAILED test_scope.py::test_value\n" + "traceback detail " * 5000

    def execute(argv, **kwargs):
        if argv == VERIFY:
            return subprocess.CompletedProcess(argv, 1, failure, "")
        return actual_run(argv, **kwargs)

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result, calls, _ = _run(fixture, mode="always-fail", executor="antigravity", max_repair_rounds=1)
    assert len(calls) == 2 and len(calls[1]["prompt"]) < 16000
    evidence = fixture.root / "evidence"
    first = result["invocations"][0]
    assert (evidence / first["evidence_files"]["verify_stdout"]).read_text() == failure
    assert first["evidence_files"]["verify_stdout"] in calls[1]["prompt"]
    feedback = result["invocations"][1]["evidence_files"]["repair_feedback"]
    assert feedback in calls[1]["prompt"]


@pytest.mark.parametrize("executor,payload", [
    ("codex", ""),
    ("codex", '{"type":"turn.started"}'),
    ("codex", '{"type":"turn.completed"}\n{"type":"turn.started"}'),
    ("codex", '{"type":"turn.completed"}\n{"type":'),
    ("antigravity", '{"response":"outcome unclear"}'),
])
@pytest.mark.parametrize("verify_code", [0, 1])
def test_ambiguous_zero_exit_cannot_authorize_repair(fixture, executor, payload, verify_code):
    actual_run = subprocess.run

    def execute(argv, **kwargs):
        result = actual_run(argv, **kwargs)
        if argv != VERIFY:
            return subprocess.CompletedProcess(argv, result.returncode, payload, result.stderr)
        return subprocess.CompletedProcess(argv, verify_code, result.stdout, result.stderr)

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result, calls, _ = _run(fixture, executor=executor)
    assert len(calls) == result["invocation_count"] == 1
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EXECUTOR_OUTCOME_UNCONFIRMED"
    assert result["invocations"][0]["repair_eligible"] is False


@pytest.mark.parametrize("signature", [
    "subprocess.CalledProcessError: Command ['git', 'init'] returned non-zero exit status 3221225794.",
    "git startup exit 0xC0000142",
    "git returned -1073741502",
    "OSError: [WinError 1920] The file cannot be accessed by the system",
    "E   subprocess.TimeoutExpired: Command ['tool'] timed out after 1 seconds",
])
def test_nested_host_failure_is_not_a_code_repair_trigger(fixture, signature):
    actual_run = subprocess.run

    def execute(argv, **kwargs):
        if argv == VERIFY:
            return subprocess.CompletedProcess(
                argv, 1, "FAILED test_scope.py::test_value\n" + signature, "")
        return actual_run(argv, **kwargs)

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result, calls, _ = _run(fixture)
    assert len(calls) == result["invocation_count"] == 1
    assert result["status"] in ("VERIFICATION_FAILED", "EXECUTION_UNKNOWN")
    assert result["error_code"] == "VERIFY_INFRASTRUCTURE_ERROR"
    assert result["invocations"][0]["repair_eligible"] is False


def test_pretty_antigravity_completion_can_repair_with_unknown_usage(fixture):
    actual_run = subprocess.run

    def execute(argv, **kwargs):
        result = actual_run(argv, **kwargs)
        if argv != VERIFY:
            return subprocess.CompletedProcess(
                argv, result.returncode,
                json.dumps({"status": "SUCCESS", "response": "completed"}, indent=2),
                result.stderr)
        return result

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result, calls, _ = _run(fixture, executor="antigravity")
    assert len(calls) == 2 and result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["usage"] is None and result["usage_status"] == "UNKNOWN"


def test_antigravity_argv_budget_records_complete_original_prompt(fixture):
    from ai_commerce.engineering.adapters import command_for
    fixture.monkeypatch.setattr(runner, "command_for", command_for)
    commands = []
    original_prompt = 'approved instructions "quoted" \\ ' * 3000 + "\U0001f680" * 500

    def execute(argv, **kwargs):
        if argv == VERIFY:
            return subprocess.CompletedProcess(argv, 0, "1 passed", "")
        commands.append(argv)
        (fixture.repo / "impl.py").write_text("good")
        return subprocess.CompletedProcess(argv, 0, '{"status":"SUCCESS"}', "")

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result = runner.execute_attempt(
        repo=fixture.repo, output_dir=fixture.root / "evidence", work_id="w", attempt_id="a",
        selection=ExecutorSelection("antigravity", "selected-model", "high"),
        instruction=original_prompt, verify_argv=VERIFY, allowed_prefixes=fixture.state["scope"])
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE" and len(commands) == 1
    # Include Windows quoting expansion and UTF-16 surrogate pairs in the budget.
    units = len(subprocess.list2cmdline(commands[0]).encode("utf-16-le")) // 2 + 1
    assert units <= 8000
    evidence = fixture.root / "evidence"
    inv = result["invocations"][0]
    original = evidence / inv["evidence_files"]["instruction"]
    assert original.read_text(encoding="utf-8") == original_prompt
    transport = evidence / inv["evidence_files"]["transport_instruction"]
    assert transport.read_text(encoding="utf-8") == commands[0][commands[0].index("--print") + 1]
    # AGY receives only the disposable input mirror, not the authoritative log root.
    mirror = evidence / "invocations" / "000" / "agy_input" / "instruction.txt"
    assert mirror.read_bytes() == original.read_bytes()
    assert str(mirror) in transport.read_text(encoding="utf-8")
    assert str(original) not in transport.read_text(encoding="utf-8")
    assert inv["evidence_sha256"]["instruction"] == hashlib.sha256(original_prompt.encode()).hexdigest()


@pytest.mark.parametrize("pretty", [False, True])
def test_antigravity_denial_is_preserved_across_json_framing(fixture, pretty):
    actual_run = subprocess.run
    payload = json.dumps({"status": "SUCCESS", "denied_actions": [{"action": "command"}]},
                         indent=2 if pretty else None)
    if not pretty:
        payload += "\ntruncated trailing response"

    def execute(argv, **kwargs):
        assert argv != VERIFY, "Permission denial must stop before verification"
        result = actual_run(argv, **kwargs)
        return subprocess.CompletedProcess(argv, result.returncode, payload, result.stderr)

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result, calls, _ = _run(fixture, executor="antigravity")
    assert len(calls) == 1 and result["status"] == "EXECUTOR_PERMISSION_DENIED"
    assert result["denied_actions"] == ["command"]


def test_explicit_provider_failure_survives_truncated_trailing_output(fixture):
    actual_run = subprocess.run

    def execute(argv, **kwargs):
        assert argv != VERIFY
        result = actual_run(argv, **kwargs)
        return subprocess.CompletedProcess(argv, result.returncode,
            '{"type":"turn.failed","error":{"message":"outcome unclear"}}\n{"type":', "")

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(
        run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result, calls, _ = _run(fixture)
    assert len(calls) == 1 and result["status"] == "EXECUTION_UNKNOWN"
    assert result["verify_exit_code"] is None


def test_pretty_antigravity_usage_is_observed_without_inventing_cost():
    payload = json.dumps({"status": "SUCCESS", "usage": {
        "input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3}}, indent=2)
    assert runner._observe_usage(payload.encode(), "antigravity") == {
        "input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3}
