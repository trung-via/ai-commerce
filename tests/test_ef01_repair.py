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
from ai_commerce.remote.transport import WorkOwner, GitHubTransport, RemoteTransportError

BASE = "a" * 40
ROOT = Path(__file__).resolve().parents[1]
VERIFY = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_scope.py"]


class WorkOwnerMemoryTransport(GitHubTransport):
    """Deterministic GitHub storage only; exercises real WorkOwner validation."""
    def __init__(self, source=BASE):
        super().__init__("fixture/repository", "fixture-not-a-credential")
        self.source = source
        self.refs, self.commits, self.requests = {}, {}, []
        self.commits[source] = {"tree": {"sha": "e" * 40}, "parents": [], "message": "source"}
        self.storage_lock = threading.Lock()
        self.issue_body = None
        self.issues = {}
        self.before_write = None

    def api(self, method, path, data=None):
        with self.storage_lock:
            self.requests.append((method, path, data))
            if method == "GET" and (path == "/issues" or path.startswith("/issues?")):
                items = []
                if self.issues:
                    for num in sorted(self.issues):
                        b = self.issues[num]
                        user = {"login": "trung-via"}
                        if isinstance(b, dict):
                            user = b.get("user", user)
                            b = json.dumps(b)
                        items.append({"number": num, "body": b, "user": user})
                elif self.issue_body:
                    b = json.dumps(self.issue_body) if isinstance(self.issue_body, dict) else self.issue_body
                    items.append({"number": 506, "body": b, "user": {"login": "trung-via"}})
                if "?" in path:
                    from urllib.parse import parse_qs, urlparse
                    qs = parse_qs(urlparse(path).query)
                    if "per_page" in qs or "page" in qs:
                        page = int(qs.get("page", [1])[0])
                        per_page = int(qs.get("per_page", [100])[0])
                        start = (page - 1) * per_page
                        return items[start:start + per_page]
                return items
            if method == "GET" and path.startswith("/issues/"):
                num = int(path.rsplit("/", 1)[1])
                body = self.issues.get(num) if self.issues else self.issue_body
                if body is None:
                    raise RemoteTransportError("GITHUB_HTTP_404")
                user = {"login": "trung-via"}
                if isinstance(body, dict):
                    user = body.get("user", user)
                    body = json.dumps(body)
                return {"number": num, "body": body, "user": user}
            if method == "GET" and path.startswith("/git/matching-refs/heads/"):
                prefix = "refs/heads/" + path.split("/heads/", 1)[1]
                return [{"ref": ref, "object": {"sha": sha}} for ref, sha in self.refs.items() if ref.startswith(prefix)]
            if method == "GET" and path.startswith("/git/ref/heads/"):
                ref = "refs/heads/" + path.split("/heads/", 1)[1]
                if ref not in self.refs:
                    raise RemoteTransportError("GITHUB_HTTP_404", http_status=404)
                return {"ref": ref, "object": {"sha": self.refs[ref], "type": "commit"}}
            if method == "GET" and path.startswith("/git/commits/"):
                return json.loads(json.dumps(self.commits[path.rsplit("/", 1)[1]]))
            if method == "POST" and path == "/git/commits":
                sha = hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
                self.commits[sha] = {**data, "tree": {"sha": data["tree"]},
                                     "parents": [{"sha": sha} for sha in data["parents"]]}
                return {"sha": sha}
            if method == "POST" and path in ("/git/blobs", "/git/trees"):
                return {"sha": hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()}
            if method == "POST" and path == "/git/refs":
                if data["ref"] in self.refs:
                    raise RemoteTransportError("GITHUB_HTTP_422", http_status=422,
                                               github_codes=("already_exists",),
                                               validation_fields=("ref",))
                if self.before_write:
                    self.before_write(data)
                self.refs[data["ref"]] = data["sha"]
                return {"ref": data["ref"], "object": {"sha": data["sha"], "type": "commit"}}
            raise AssertionError((method, path))


def memory_owner(action, batch, storage=None):
    storage = storage or WorkOwnerMemoryTransport(batch["source_sha"])
    submitted = {k: v for k, v in action.items() if k != "resolved_dependencies"}
    packet = {**batch, "actions": [submitted]}
    storage.issue_body = packet
    identity = WorkOwner.identity(storage, 42, packet, submitted)
    return WorkOwner.acquire(storage, identity)


class DirectWorkOwnerBroker(remote.WorkOwnerBroker):
    """Fake only child stdio for in-process EF01 fixtures; real journal/fences."""
    def communicate(self, child, timeout):
        def fence(stage, inv):
            request = {"stage": stage, "round": inv["repair_round"],
                       "attempt_id": inv["attempt_id"], "token": self.token}
            if stage == "ROUND_VERIFIED":
                request["receipt"] = runner.owner_round_receipt(inv)
            self.fence(request)
        child.owner_fence = fence
        return child.communicate(timeout=timeout)


@pytest.mark.parametrize("native,kind,owner", [
    ({"status": "INVALID_REQUEST", "error_code": "ISSUER_NOT_AUTHORIZED_FOR_ACTOR"}, "INTAKE_AUTH", "INTAKE_OWNER"),
    ({"status": "QUOTA_EXHAUSTED", "error_code": "QUOTA_EXHAUSTED"}, "PROVIDER_QUOTA", "PROVIDER_OWNER"),
    ({"status": "EXECUTION_UNKNOWN", "error_code": "EXECUTOR_OUTCOME_UNCONFIRMED"}, "PROVIDER_TRANSPORT", "PROVIDER_OWNER"),
    ({"status": "UNPROVEN", "error_code": "MT01_COVERAGE_UNPROVEN"}, "MT01_SELECTOR", "MT01_OWNER"),
    ({"status": "UNPROVEN", "error_code": "MT01_COVERAGE_UNPROVEN", "scope_valid": False}, "SOURCE_GIT", "SOURCE_OWNER"),
    ({"status": "SOURCE_DRIFT", "error_code": "WORKTREE_CHANGED_BETWEEN_INVOCATIONS"}, "SOURCE_GIT", "SOURCE_OWNER"),
    ({"status": "CANDIDATE_INTEGRITY_FAILED", "error_code": "EF01_VERIFIED_TREE_CHANGED"}, "TRUSTED_PROOF", "TRUSTED_VERIFIER_OWNER"),
    ({"status": "EXECUTION_UNKNOWN", "error_code": "GITHUB_TRANSPORT_UNKNOWN"}, "REMOTE_GITHUB", "REMOTE_GITHUB_OWNER"),
    ({"status": "VERIFICATION_FAILED", "error_code": "VERIFY_PERMISSION_DENIED"}, "TEST_ENVIRONMENT", "RUNNER_ENVIRONMENT_OWNER"),
    ({"status": "VERIFICATION_FAILED", "error_code": "TARGETED_VERIFY_NONZERO_OR_UNAVAILABLE"}, "UNKNOWN", "ENGINEERING_OWNER"),
])
def test_failure_intelligence_uses_native_boundaries_and_scope_precedence(native, kind, owner):
    from ai_commerce.engineering.profile import diagnose_failure
    diagnosis = diagnose_failure(native).payload()
    assert diagnosis["failure_class"] == kind and diagnosis["root_owner"] == owner
    assert diagnosis["disposition"] == "OWNER_ESCALATION"
    assert diagnosis["proves_pass"] is False and diagnosis["replay_authorized"] is False


def test_failure_intelligence_requires_actual_repair_evidence_and_bounds_private_input():
    from ai_commerce.engineering.profile import diagnose_failure
    native = {"error_code": "TARGETED_VERIFY_NONZERO_OR_UNAVAILABLE", "repair_eligible": True,
              "verify_completed": True, "verify_exit_code": 1, "provider_return_determinate": True,
              "diagnostic": "token=private-secret; claimed PASS"}
    assert diagnose_failure(native).payload()["disposition"] == "SCOPED_REPAIR"
    for field in ("repair_eligible", "verify_completed", "provider_return_determinate"):
        assert diagnose_failure({**native, field: False}).payload()["disposition"] == "OWNER_ESCALATION"
    payload = diagnose_failure({**native, "error_code": "token=private-secret"}).payload()
    assert "private-secret" not in json.dumps(payload) and payload["evidence_gaps"]


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
    from test_ef01 import _fixture_agy_process
    # Provider/lifecycle fixtures exercise status logic, separately from native
    # Job Object tests in the registered AGY diagnostics owner.
    monkeypatch.setattr(runner, "_run_agy_owned", _fixture_agy_process)
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
    # This fixture deliberately models Git; real linked-worktree coverage lives
    # in test_ef01.py and uses the unpatched host preflight.
    monkeypatch.setattr(runner, "_agy_source_binding", lambda r, s, *a: {
        "source_root": str(r), "source_sha": s["git_head_after"], "git_dir": s["git_dir"]})
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


@pytest.mark.parametrize("deny_round", [0, 1])
def test_workowner_fence_before_initial_provider_and_every_internal_repair(fixture, deny_round):
    events = []
    def fence(stage, inv):
        events.append((stage, inv["repair_round"]))
        if stage == "PROVIDER_INTENT" and inv["repair_round"] == deny_round:
            raise RuntimeError("WORK_OWNER_STALE_FENCE")
    result, calls, _ = _run(fixture, owner_fence=fence)
    assert result["status"] == "EXECUTION_UNKNOWN" and not result["replay_authorized"]
    assert len(calls) == deny_round
    assert events[-1] == ("PROVIDER_INTENT", deny_round)
    assert result["invocations"][-1]["executor_invoked"] is False


def test_workowner_broker_duplicate_request_and_parent_loss_fail_closed():
    from ai_commerce.remote.worker import WorkOwnerBroker
    owner = memory_owner({"action_id": "a", "attempt_id": "a-1"},
                         {"batch_id": "b", "source_sha": BASE})
    owner.append("CLAIMED")
    owner.append("SPAWN_INTENT")
    broker = WorkOwnerBroker(owner)
    # Standalone children do not inherit pytest's worktree-local sys.path.
    environment = {**remote._safe_child_env(ROOT), **broker.environment()}
    script = """import os, sys
from pathlib import Path
from ai_commerce.engineering import runner
assert Path(runner.__file__).resolve() == Path(sys.argv[1]).resolve()
fence = runner.owner_fence_client(os.environ)
assert not any(k.startswith('AI_COMMERCE_EF02_OWNER_') for k in os.environ)
inv = {'repair_round': 0, 'attempt_id': 'a-1'}
fence('PROVIDER_INTENT', inv)
try:
    fence('PROVIDER_INTENT', inv)
except RuntimeError:
    print('DUPLICATE_DENIED')
else:
    raise AssertionError('duplicate admitted')
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(Path(runner.__file__).resolve())],
                             env=environment,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8")
    try:
        output, errors = broker.communicate(child, timeout=20)
        assert child.returncode == 0, errors
        assert output.strip() == "DUPLICATE_DENIED" and broker.token not in output
    finally:
        broker.close()
    with pytest.raises(RemoteTransportError):
        owner.append("PROVIDER_INTENT", round=1)


def test_workowner_parent_eof_denies_provider_without_any_dispatch(monkeypatch):
    import io
    environment = {"AI_COMMERCE_EF02_OWNER_REQUIRED": "1",
                   "AI_COMMERCE_EF02_OWNER_ENDPOINT": "stdio-v1",
                   "AI_COMMERCE_EF02_OWNER_TOKEN": "f" * 64}
    fence = runner.owner_fence_client(environment)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with pytest.raises(RuntimeError, match="UNCONFIRMED"):
        fence("PROVIDER_INTENT", {"repair_round": 0, "attempt_id": "a"})


@pytest.mark.parametrize("field", ["attempt_id", "source_base_sha", "status"])
def test_workowner_mismatched_round_receipt_cannot_publish(tmp_path, monkeypatch, field):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    original = remote.subprocess.Popen
    class Tampered(original):
        def communicate(self, timeout=None):
            result = super().communicate(timeout)
            args = build_parser().parse_args(self.argv[3:])
            path = args.out_dir / "engineering_result.json"
            report = json.loads(path.read_text())
            report["invocations"][0][field] = "mismatched"
            path.write_text(json.dumps(report))
            return result
    monkeypatch.setattr(remote.subprocess, "Popen", Tampered)
    result = context.worker.execute_action(_action("a"))
    assert result["status"] == "CANDIDATE_INTEGRITY_FAILED"
    assert result["error_code"] == "WORK_PROVIDER_RECEIPT_MISMATCH"
    assert not context.candidates and len(context.calls) == 2


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
    assert feedback["hypothesis_trail"][0]["diagnosis"]["failure_class"] == "CODE_ASSERTION"
    assert result["hypothesis_trail"][0]["next_experiment"] == "TARGETED_PYTEST_ON_CHANGED_SOURCE"
    assert len(result["hypothesis_trail"]) <= 4
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
@pytest.mark.parametrize("minimum_test", [False, True])
def test_protected_changed_path_stops_before_verification(fixture, protected, minimum_test):
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
    result, calls, _ = _run(fixture, mode="scope-pass", minimum_test=minimum_test)
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
    git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_") and k != "AI_COMMERCE_GIT_GUARD_PATHS"}
    for args in (["init", "-q"], ["config", "user.email", "local@example.invalid"],
                 ["config", "user.name", "repair fixture"], ["add", "."], ["commit", "-qm", "initial"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_env)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True, env=git_env).stdout.strip()
    return repo, head


def _worker_fixture(tmp_path, monkeypatch, fail=(), publication_error=False, checkpoint_error=False, real_git=True,
                    drift=None, mt01_v21=False):
    monkeypatch.setattr(remote, "WorkOwnerBroker", DirectWorkOwnerBroker)
    if not mt01_v21:
        monkeypatch.delenv("MT01_V21_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MT01_V21_ENABLED", "1")
    for key in list(os.environ):
        if key.startswith("GIT_CONFIG_") or key == "AI_COMMERCE_GIT_GUARD_PATHS":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    import shutil
    shutil_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd: sys.executable if cmd in ("python", "py") else shutil_which(cmd))
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
        monkeypatch.setattr(runner, "_agy_source_binding", lambda r, s, *a: {
            "source_root": str(r), "source_sha": s["git_head_after"], "git_dir": s["git_dir"]})
        # Fake worktree add copies fixture files without creating a .git worktree.
        # Stub only the new checkout preflight here, not in real_git tests.
        monkeypatch.setattr(remote, "checkout_diagnostics", lambda *_a, **_kw: {
            "clean": True, "dirty_entries": [], "dirty_entry_count": 0,
            "index_refresh_exit": None, "metadata_only_reconciled": False,
            "git_head_after": BASE,
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
        if drift:
            code += "p=Path(" + repr(drift) + ");p.parent.mkdir(parents=True,exist_ok=True);p.write_text('ordinary');"
        code += "print('{\\\"type\\\":\\\"turn.completed\\\"}')"
        return [sys.executable, "-c", code], None

    monkeypatch.setattr(runner, "command_for", command)

    class Child:
        pid = 12345
        returncode = None

        def __init__(self, argv, **kwargs):
            self.argv = argv
            self.owner_environment = {k: v for k, v in kwargs["env"].items()
                                      if k.startswith("AI_COMMERCE_EF02_OWNER_")}
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
                acceptance_ids=args.acceptance,
                aggregate_sources=args.aggregate_source, host_slots=args.host_slots,
                registered_profiles=json.loads(args.registered_profiles_json.read_text())
                if args.registered_profiles_json else None,
                owner_fence=self.owner_fence)
            self.returncode = 0 if result["status"] in remote.VERIFIED_STATUSES else 1
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
    storage = WorkOwnerMemoryTransport(head)
    worker._acquire_owner = lambda action: memory_owner(action, batch, storage)
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


@pytest.mark.parametrize("declared", [True, False])
def test_ef02_acceptance_reaches_selector_through_actual_child_cli(tmp_path, monkeypatch, declared):
    from ai_commerce.engineering import minimum_test as mt
    from ai_commerce.remote import validation
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    groups = (mt.TestGroup("scope", ("tests/test_scope.py",), ("FIXTURE.acceptance",), ("impl.py", "kept.txt")),)
    monkeypatch.setattr(mt, "approved_groups", lambda: groups)
    monkeypatch.setattr(validation, "approved_groups", lambda: groups)
    context.worker.profiles["component-scope"] = ["python", "-m", "pytest", "tests/test_scope.py", "-q"]
    a = _action("a")
    if declared:
        a["instruction"] += '\nMT01_ACCEPTANCE_JSON: ["FIXTURE.acceptance"]'
    result = context.worker.execute_action(a)
    assert result["status"] == "CANDIDATE_READY"
    args = build_parser().parse_args(context.children[0].argv[3:])
    expected = ["FIXTURE.acceptance"] if declared else ["MT01.ACCEPTANCE_UNDECLARED"]
    assert args.acceptance == result["mt01_acceptance"]["ids"] == expected
    assert set(expected) <= set(result["minimum_test"]["required_acs"])
    assert result["minimum_test"]["fallback"] is not declared
    entry = result["minimum_test"]["groups"][0]
    profile = "component-scope" if declared else "project-tests"
    assert entry["actual_argv"] == context.worker.profiles[profile]
    assert entry["selected_profile"] == profile
    saved = json.loads(next(context.worker.root.rglob("action_result.json")).read_text())
    assert saved["minimum_test"] == result["minimum_test"]
    assert len(context.claims) == len(context.children) == 1 and len(context.calls) == 2


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
        # Inject only after execution: the earlier source preflight must stay
        # clean so this test still exercises independent final-tree rejection.
        if not context.calls:
            return tree, patch
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


@pytest.mark.parametrize("field,value,code", [
    ("git_head_after", "b" * 40, "SOURCE_PREFLIGHT_MISMATCH"),
    ("clean", False, "SOURCE_CHECKOUT_INCONSISTENT")])
def test_ef02_source_preflight_mismatch_stops_before_provider(tmp_path, monkeypatch, field, value, code):
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    observed = remote.checkout_diagnostics
    def mismatched(*args, **kwargs):
        details = observed(*args, **kwargs)
        details[field] = value
        return details
    monkeypatch.setattr(remote, "checkout_diagnostics", mismatched)
    result = context.worker.execute_action(_action("a"))
    assert result["status"] == "SOURCE_WORKTREE_FAILED"
    assert result["error_code"] == code
    assert result["failure_diagnosis"]["disposition"] == "OWNER_ESCALATION"
    if field == "git_head_after":
        assert result["source_preflight"]["proves_pass"] is False
    assert not context.calls and not context.candidates

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
    "E   OSError: [WinError 1314] A required privilege is not held by the client",
    "E   PermissionError: [WinError 5] Access is denied",
    "fatal: in 'preparing' phase, update aborted by the reference-transaction hook",
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
    mirrors = []
    original_prompt = 'approved instructions "quoted" \\ ' * 3000 + "\U0001f680" * 500

    def execute(argv, **kwargs):
        if argv == VERIFY:
            return subprocess.CompletedProcess(argv, 0, "1 passed", "")
        commands.append(argv)
        mirror = Path(argv[argv.index("--add-dir", argv.index("--add-dir") + 1) + 1])
        assert (mirror / "instruction.txt").read_text(encoding="utf-8") == original_prompt
        mirrors.append(mirror)
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
    mirror = mirrors[0]
    assert not mirror.exists()
    assert json.dumps(str(mirror / "instruction.txt"), ensure_ascii=False) in transport.read_text(encoding="utf-8")
    assert str(original) not in transport.read_text(encoding="utf-8")
    wire = transport.read_text(encoding="utf-8")
    assert "CODE WRITE ROOT" in wire and "READ ONLY INPUT" in wire
    assert json.dumps(str(fixture.repo), ensure_ascii=False) in wire
    assert hashlib.sha256(original_prompt.encode()).hexdigest() in wire
    assert inv["evidence_sha256"]["instruction"] == hashlib.sha256(original_prompt.encode()).hexdigest()


def test_agy_every_repair_round_cleans_its_own_mirror(fixture):
    result, calls, profiles = _run(fixture, executor="antigravity", max_repair_rounds=2)
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE" and len(calls) == 2
    mirrors = [p[2]["antigravity_evidence_dir"] for p in profiles if p[2].get("antigravity_evidence_dir")]
    assert len(mirrors) == len(set(mirrors)) == 2
    assert all(not p.exists() for p in mirrors)
    assert all(not (p / ".git").exists() for root in mirrors for p in root.parents)
    assert (fixture.root / "evidence/invocations/001/repair_feedback.json").is_file()


@pytest.mark.parametrize("fault", ["timeout", "error", "interruption", "mutated-input"])
def test_agy_mirror_cleanup_after_failed_provider(fixture, fault):
    from ai_commerce.engineering.adapters import command_for
    fixture.monkeypatch.setattr(runner, "command_for", command_for)
    mirrors = []
    actual_run = subprocess.run
    def execute(argv, **kwargs):
        if argv[0] != "agy":
            return actual_run(argv, **kwargs)
        mirrors.append(Path(argv[argv.index("--add-dir", argv.index("--add-dir") + 1) + 1]))
        assert (mirrors[-1] / "instruction.txt").is_file()
        if fault == "timeout":
            raise subprocess.TimeoutExpired(argv, 1, output=b'{"status":"STARTED"}')
        if fault == "error":
            raise RuntimeError("fixture provider failed")
        if fault == "interruption":
            raise KeyboardInterrupt()
        (mirrors[-1] / "instruction.txt").write_text("untrusted replacement")
        (fixture.repo / "impl.py").write_text("good")
        return subprocess.CompletedProcess(argv, 0, '{"status":"SUCCESS"}', "")
    fixture.monkeypatch.setattr(runner.subprocess, "run", execute)
    args = dict(repo=fixture.repo, output_dir=fixture.root / "evidence", work_id="w", attempt_id="a",
                selection=ExecutorSelection("antigravity", "fixture", "high"), instruction="scoped change",
                verify_argv=VERIFY, allowed_prefixes=fixture.state["scope"])
    if fault == "interruption":
        with pytest.raises(KeyboardInterrupt):
            runner.execute_attempt(**args)
        result = json.loads((fixture.root / "evidence/engineering_result.json").read_text())
    else:
        result = runner.execute_attempt(**args)
    assert len(mirrors) == 1 and not mirrors[0].exists()
    assert result["verify_exit_code"] is None and result["replay_authorized"] is False
    assert result["status"] == ("SOURCE_DRIFT" if fault == "mutated-input" else "EXECUTION_UNKNOWN")
    if fault == "mutated-input":
        assert result["error_code"] == "AGY_INPUT_INTEGRITY_FAILED"
        assert result["input_integrity_reason"] == "AGY_INPUT_CONTRACT_MODIFIED"


def _boundary_attempt(fixture, fault=None, *, repair=False, long_prompt=False):
    """Real TEMP/source bytes with simulated provider/verifier, never native proof."""
    from ai_commerce.engineering.adapters import command_for
    fixture.monkeypatch.setattr(runner, "command_for", command_for)
    providers, verifies, roots_seen = [], [], []
    prompt = ('Sửa đúng phạm vi "tiếng Việt" \\ 🚀\n' * 2000 if long_prompt else "approved source edit")

    def execute(argv, **kwargs):
        if argv == VERIFY:
            verifies.append(argv)
            if fault == "late-input":
                (roots_seen[-1][1] / "instruction.txt").write_bytes(b"late background mutation")
            if fault == "late-source":
                (fixture.repo / "impl.py").write_bytes(b"late source mutation")
            if fault == "late-aux-modify":
                (roots_seen[-1][1] / "pytest-session.log").write_bytes(b"late sidecar write")
            if fault == "late-aux-delete":
                (roots_seen[-1][1] / "pytest-session.log").unlink()
            if fault == "late-aux-add":
                (roots_seen[-1][1] / "scratch-late.tmp").write_bytes(b"late new sidecar")
            code = int(repair and len(verifies) == 1)
            return subprocess.CompletedProcess(argv, code, "1 failed\n" if code else "1 passed\n", "")
        assert argv[0] == "agy"
        providers.append(argv)
        roots = [Path(argv[i + 1]) for i, arg in enumerate(argv[:-1]) if arg == "--add-dir"]
        roots_seen.append(roots)
        source, mirror, scratch = roots
        assert source == fixture.repo and source != mirror != scratch
        assert kwargs["cwd"] == source and kwargs["input"] is None
        assert all(kwargs["env"][key] == str(scratch) for key in ("TEMP", "TMP", "TMPDIR"))
        assert argv[argv.index("--model") + 1] == "gemini-3.8-flash"
        assert argv[argv.index("--effort") + 1] == "high"
        assert runner._windows_command_units(argv) <= 8000
        instruction = (mirror / "instruction.txt").read_bytes()
        if len(providers) == 1:
            assert instruction == prompt.encode()
        else:
            assert prompt.encode() in instruction and (mirror / "repair_feedback.json").is_file()
        (mirror / "pytest-session.log").write_bytes(b"benign opaque sidecar")
        (scratch / "generated-cache.bin").write_bytes(b"\x00\xff")
        (source / "impl.py").write_bytes(b"bad\r\n" if repair and len(providers) == 1 else b"good\r\n")
        if fault in ("modify", "delete", "feedback") and (fault != "feedback" or len(providers) == 2):
            target = mirror / ("repair_feedback.json" if fault == "feedback" else "instruction.txt")
            if fault == "delete":
                target.unlink()
            else:
                target.write_bytes(b"tampered")
        if fault == "unsafe":
            (mirror / "metadata.json").write_bytes(b"{}")
        if fault == "scope":
            (source / "outside.py").write_bytes(b"outside allowlist")
        if fault == "head":
            fixture.state["head"] = "b" * 40
        if fault == "gitdir":
            fixture.state["git_dir"] = "other-checkout"
        payload = {"status": "UNKNOWN" if fault == "unknown" else "SUCCESS"}
        if fault == "denied":
            payload["denied_actions"] = [{"action": "write_file"}]
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    fixture.monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=execute, TimeoutExpired=subprocess.TimeoutExpired))
    result = runner.execute_attempt(repo=fixture.repo, output_dir=fixture.root / "boundary-evidence",
        work_id="approved", attempt_id="new-boundary", instruction=prompt,
        selection=ExecutorSelection("antigravity", "gemini-3.8-flash", "high"),
        verify_argv=VERIFY, allowed_prefixes=fixture.state["scope"], verify_first_scope=False,
        max_repair_rounds=1)
    assert all(not root.exists() for roots in roots_seen for root in roots[1:])
    return result, providers, verifies


def test_535_sidecar_and_real_add_bytes_reach_independent_verifier(fixture):
    result, providers, verifies = _boundary_attempt(fixture)
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert len(providers) == len(verifies) == 1
    assert result["candidate_file_sha256"]["impl.py"] == hashlib.sha256(b"good\r\n").hexdigest()
    evidence = fixture.root / "boundary-evidence"
    manifest = json.loads((evidence / result["evidence_files"]["agy_input_after_executor"]).read_text())
    assert manifest["status"] == "INTACT"
    assert next(f for f in manifest["files"] if f["name"] == "instruction.txt")["sha256"] == result["instruction_sha256"]


@pytest.mark.parametrize("fault,reason", [("modify", "AGY_INPUT_CONTRACT_MODIFIED"),
    ("delete", "AGY_INPUT_CONTRACT_DELETED"), ("unsafe", "AGY_INPUT_UNEXPECTED_AUXILIARY"),
    ("late-input", "AGY_INPUT_CONTRACT_MODIFIED"), ("late-aux-modify", "AGY_INPUT_LATE_WRITE"),
    ("late-aux-delete", "AGY_INPUT_LATE_WRITE"), ("late-aux-add", "AGY_INPUT_LATE_WRITE")])
def test_input_failure_is_non_candidate_and_quarantines_exact_add_bytes(fixture, fault, reason):
    result, providers, verifies = _boundary_attempt(fixture, fault)
    assert result["error_code"] == "AGY_INPUT_INTEGRITY_FAILED"
    assert result["input_integrity_reason"] == reason and result["status"] == "SOURCE_DRIFT"
    assert len(providers) == 1 and len(verifies) == int(fault.startswith("late-"))
    assert not result["invocations"][0]["repair_eligible"] and result["replay_authorized"] is False
    evidence = fixture.root / "boundary-evidence"
    manifest = json.loads((evidence / result["evidence_files"]["quarantine_manifest"]).read_text())
    assert manifest["candidate"] is False and manifest["replay_authorized"] is False
    record = next(f for f in manifest["files"] if f["path"] == "impl.py")
    copied = evidence / "invocations/000" / record["evidence_name"]
    assert copied.read_bytes() == b"good\r\n"
    assert record["sha256"] == hashlib.sha256(copied.read_bytes()).hexdigest()
    assert record["status"] == "QUARANTINED" and record["mode"]
    if fault.startswith("late-aux-"):
        diagnostic = json.loads((evidence / result["evidence_files"]["agy_input_after_verify"]).read_text())
        delta = {f["change"] for f in diagnostic["files"] if f["role"] == "AUXILIARY"}
        assert {"late-aux-modify": "MODIFIED", "late-aux-delete": "DELETED", "late-aux-add": "ADDED"}[fault] in delta


def test_cleanup_failure_preserves_native_input_reason_and_quarantine(fixture):
    from contextlib import contextmanager
    original = runner._agy_temp_tree
    @contextmanager
    def failed_cleanup(repo, prefix, **kwargs):
        with original(repo, prefix, **kwargs) as path:
            yield path
        if prefix == "agy_input-":
            raise ValueError("AGY_INPUT_CLEANUP_UNSAFE")
    fixture.monkeypatch.setattr(runner, "_agy_temp_tree", failed_cleanup)
    result, _, _ = _boundary_attempt(fixture, "modify")
    assert result["error_code"] == "AGY_INPUT_INTEGRITY_FAILED"
    assert result["input_integrity_reason"] == "AGY_INPUT_CONTRACT_MODIFIED"
    assert result["invocations"][0]["runtime_cleanup_error"] == "AGY_INPUT_CLEANUP_UNSAFE"
    assert "quarantine_manifest" in result["evidence_files"]


@pytest.mark.parametrize("fault,error", [("scope", "CHANGES_OUTSIDE_ALLOWED_SCOPE"),
    ("head", "GIT_HEAD_CHANGED"), ("gitdir", "GIT_WORKTREE_IDENTITY_CHANGED"),
    ("denied", "HEADLESS_TOOL_PERMISSION_DENIED"), ("unknown", "EXECUTOR_OUTCOME_UNCONFIRMED"),
    ("late-source", "VERIFY_CHANGED_SOURCE")])
def test_sidecar_acceptance_never_weakens_other_fail_closed_boundaries(fixture, fault, error):
    result, providers, _ = _boundary_attempt(fixture, fault)
    assert result["error_code"] == error
    assert result["status"] != "BUILD_VERIFIED_FOR_SCOPE" and len(providers) == 1


@pytest.mark.parametrize("feedback_mutation", [False, True])
def test_unicode_long_prompt_first_and_repair_round_contracts(fixture, feedback_mutation):
    result, providers, verifies = _boundary_attempt(fixture, "feedback" if feedback_mutation else None,
                                                   repair=True, long_prompt=True)
    assert len(providers) == 2 and len(verifies) == (1 if feedback_mutation else 2)
    if feedback_mutation:
        assert result["error_code"] == "AGY_INPUT_INTEGRITY_FAILED"
        assert result["input_integrity_reason"] == "AGY_INPUT_CONTRACT_MODIFIED"
    else:
        assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert all("transport_instruction" in inv["evidence_files"] for inv in result["invocations"])


@pytest.mark.parametrize("kind", ["protected", "binary", "oversize", "secret", "hash-race", "hardlink", "out-of-scope"])
def test_quarantine_refuses_unsafe_source_bytes(tmp_path, kind):
    repo = tmp_path / "source"
    repo.mkdir()
    name = ".env" if kind == "protected" else "new.py"
    data = b"\x00binary" if kind == "binary" else b"a" * (128 * 1024 + 1) if kind == "oversize" else (
        b'password = "sensitive-value"' if kind == "secret" else b"safe ordinary text\n")
    (repo / name).write_bytes(data)
    if kind == "hardlink":
        try:
            os.link(repo / name, tmp_path / "external-link")
        except PermissionError:
            pytest.skip("native hardlink creation denied; no bypass")
    snapshot = {"changed_files": [name], "candidate_file_sha256": {name:
        "wrong" if kind == "hash-race" else hashlib.sha256(data).hexdigest()}}
    saved = {}
    runner._quarantine_source(repo, snapshot, ["other.py"] if kind == "out-of-scope" else [name],
                               lambda key, path, raw: saved.update({key: raw}))
    assert set(saved) == {"quarantine_manifest"}
    assert json.loads(saved["quarantine_manifest"])["files"][0]["status"] == "REJECTED"
    assert (repo / name).read_bytes() == data


def test_quarantine_enforces_count_and_total_caps(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    data = b"ordinary text\n" * 4000
    names = [f"new-{i}.py" for i in range(40)]
    for name in names:
        (repo / name).write_bytes(data)
    snapshot = {"changed_files": names, "candidate_file_sha256": {name: hashlib.sha256(data).hexdigest() for name in names}}
    saved = {}
    runner._quarantine_source(repo, snapshot, names, lambda key, path, raw: saved.update({key: raw}))
    manifest = json.loads(saved.pop("quarantine_manifest"))
    assert len(saved) <= 32 and sum(map(len, saved.values())) <= 1024 * 1024
    assert manifest["total_bytes"] == sum(map(len, saved.values()))
    assert any(item["status"] == "REJECTED" for item in manifest["files"])


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


def test_ef02_worker_with_mt01_v21_enabled_and_registered_groups_accepts_candidate(tmp_path, monkeypatch):
    """When MT01_V21_ENABLED=1 and groups are registered, worker passes and produces candidate."""
    from ai_commerce.engineering import minimum_test as mt
    from ai_commerce.remote import validation
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, mt01_v21=True)
    groups = (mt.TestGroup("scope", ("tests/test_scope.py",), ("FIXTURE.acceptance",), ("impl.py", "kept.txt")),)
    monkeypatch.setattr(mt, "approved_groups", lambda: groups)
    monkeypatch.setattr(validation, "approved_groups", lambda: groups)
    context.worker.profiles["mt01-scope-tests"] = ["python", "-m", "pytest", "tests/test_scope.py", "-q"]
    a = _action("a")
    a["instruction"] += '\nMT01_ACCEPTANCE_JSON: ["FIXTURE.acceptance"]'
    result = context.worker.execute_action(a)
    assert result["status"] == "CANDIDATE_READY"


def test_ef02_worker_with_mt01_v21_enabled_and_unregistered_fixture_fails_closed(tmp_path, monkeypatch):
    """When MT01_V21_ENABLED=1 and synthetic fixture has unregistered changes, fail-closed UNPROVEN."""
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False, mt01_v21=True)
    a = _action("a")
    result = context.worker.execute_action(a)
    assert result["status"] == "UNPROVEN"
    assert result.get("error_code") == "MT01_COVERAGE_UNPROVEN"
