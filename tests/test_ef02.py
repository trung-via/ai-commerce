"""EF02 scope tests: validation, real parallel child processes, dependencies and claim races."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_commerce.remote.validation import (
    RemoteRequestError, validate_batch, verify_registry, safe_path, fingerprint
)
from ai_commerce.remote.scheduler import run_schedule
from ai_commerce.remote.transport import GitHubTransport, RemoteTransportError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "RemoteActionBatch.v1.schema.json"
REGISTRY = ROOT / ".github" / "ef02" / "verify-profiles.json"


def action(name, depends=(), resources=()):
    return {
        "action_id": name, "attempt_id": name + "-001",
        "executor": "codex" if name != "b" else "antigravity",
        "model": "gpt-6.1-sol" if name != "b" else "gemini-3.8-flash",
        "effort": "max" if name != "b" else "medium",
        "instruction": "Make one scoped source change",
        "allowed_paths": ["src/ai_commerce/feature_" + name],
        "verification_profile": "project-tests", "depends_on": list(depends),
        "resources": list(resources)
    }


def batch(actions):
    return {
        "kind": "AI_COMMERCE_REMOTE_ACTION_BATCH", "version": 1,
        "batch_id": "sample-001", "source_sha": "a" * 40,
        "actor": "HUMAN", "authorization_ref": "user-directive-001",
        "max_parallel": 2, "actions": actions,
    }


def validate(packet, issuer="trung-via", owner="trung-via", brain=()):
    return validate_batch(packet, schema=SCHEMA, registry=verify_registry(REGISTRY),
                          issuer_login=issuer, owner_login=owner, brain_issuers=brain)


def test_schema_and_authentication_are_not_just_actor_strings():
    assert validate(batch([action("a")]))["batch_id"] == "sample-001"
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate(batch([action("a")]), issuer="someone-else")
    p = batch([action("a")])
    p["actor"] = "BRAIN"
    assert validate(p, issuer="ai-commerce-bot", brain=("ai-commerce-bot",))
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate(p, issuer="trung-via")


def test_reject_duplicate_cycle_unknown_dependency_and_bad_path():
    cases = [
        [action("a"), action("a")],
        [action("a", depends=("b",)), action("b", depends=("a",))],
        [action("a", depends=("missing",))],
    ]
    for acts in cases:
        with pytest.raises(RemoteRequestError):
            validate(batch(acts))
    for value in ("../secret", "src\\secret", "/etc/passwd", ".github/workflows/run.yml",
                  "C:/Windows/System32", "./src"):
        with pytest.raises(RemoteRequestError):
            safe_path(value)
    p = batch([action("a")])
    p["actions"][0]["verification_profile"] = "shell"
    with pytest.raises(RemoteRequestError, match="VERIFICATION_PROFILE_UNREGISTERED"):
        validate(p)


def test_fingerprint_deterministic_and_model_change_is_distinct():
    p = batch([action("a")])
    assert fingerprint(p) == fingerprint(copy.deepcopy(p))
    alt = copy.deepcopy(p)
    alt["actions"][0]["model"] = "other-model"
    assert fingerprint(p) != fingerprint(alt)


def test_two_real_child_processes_overlap_in_one_batch():
    running = batch([action("a"), action("b")])
    def work(a):
        start = time.monotonic()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.8)"])
        child.wait(timeout=20)
        return {"status": "CANDIDATE_READY", "process_id": child.pid,
                "child_started": start, "child_ended": time.monotonic()}
    result = run_schedule(running, work, max_workers=2)
    assert result["status"] == "COMPLETE"
    assert result["scheduler_max_active"] == 2
    one, two = result["actions"]
    assert one["process_id"] != two["process_id"]
    assert max(one["child_started"], two["child_started"]) < min(one["child_ended"], two["child_ended"])


def test_five_actions_dag_wait_and_aggregate():
    p = batch([action("a"), action("b"), action("c", ("a", "b")),
               action("d", ("c",)), action("e")])
    completed = []
    def work(a):
        completed.append(a["action_id"])
        return {"status": "CANDIDATE_READY"}
    out = run_schedule(p, work, max_workers=2)
    assert out["total"] == 5 and out["succeeded"] == 5
    assert completed.index("c") > completed.index("a")
    assert completed.index("c") > completed.index("b")
    assert completed.index("d") > completed.index("c")


def test_failure_blocks_dependent_but_not_independent_sibling():
    p = batch([action("a"), action("b", ("a",)), action("c")])
    out = run_schedule(p, lambda a: {"status": "VERIFICATION_FAILED"} if a["action_id"] == "a"
                       else {"status": "CANDIDATE_READY"})
    states = {a["action_id"]: a["status"] for a in out["actions"]}
    assert states == {"a": "VERIFICATION_FAILED", "b": "BLOCKED_DEPENDENCY",
                      "c": "CANDIDATE_READY"}
    assert out["status"] == "PARTIAL_OR_FAILED"


def test_named_shared_resource_never_overlaps():
    p = batch([action("a", resources=("gpu",)), action("b", resources=("gpu",))])
    out = run_schedule(p, lambda a: (time.sleep(0.12), {"status": "CANDIDATE_READY"})[1],
                       max_workers=2)
    assert out["status"] == "COMPLETE"
    assert out["scheduler_max_active"] == 1


class FakeGitHub(GitHubTransport):
    def __init__(self):
        super().__init__("trung-via/ai-commerce", "fake")
        self.refs = {}
        self.commits = {}
        self.lock = threading.Lock()

    def api(self, method, path, data=None):
        with self.lock:
            if method == "GET" and path.startswith("/git/commits/"):
                sha = path.rsplit("/", 1)[1]
                return self.commits.get(sha, {"tree": {"sha": "b" * 40}})
            if method == "POST" and path == "/git/commits":
                sha = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:40]
                self.commits[sha] = {"message": data["message"], "tree": {"sha": data["tree"]}}
                return {"sha": sha}
            if method == "POST" and path == "/git/refs":
                ref = data["ref"]
                if ref in self.refs:
                    raise RemoteTransportError("GITHUB_HTTP_422")
                self.refs[ref] = data["sha"]
                return {"ref": ref}
            if method == "GET" and path.startswith("/git/ref/"):
                ref = path[len("/git/ref/"):]
                return {"object": {"sha": self.refs["refs/" + ref]}}
        raise AssertionError((method, path))


def test_ten_simultaneous_claims_only_one_can_start_executor():
    github = FakeGitHub()
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: github.claim(source_sha="a"*40,
                         attempt_id="abc-001", fingerprint="1"*64), range(10)))
    assert results.count("CLAIMED") == 1
    assert results.count("DUPLICATE_CLAIM_NO_REEXECUTION") == 9
    assert github.claim(source_sha="a"*40, attempt_id="abc-001",
                        fingerprint="2"*64) == "ATTEMPT_ID_CONFLICT"


class FakeIntegrationGitHub(GitHubTransport):
    """API-level fake for disjoint/fan-in blob tree composition."""

    def __init__(self, conflict=False):
        super().__init__("trung-via/ai-commerce", "fake")
        self.created = []
        base = [{"path": "src/existing.py", "mode": "100644", "type": "blob", "sha": "1"*40}]
        fa = [{"path": "src/existing.py", "mode": "100644", "type": "blob", "sha": "1"*40},
              {"path": "src/alpha.py", "mode": "100644", "type": "blob", "sha": "2"*40}]
        fb = [{"path": "src/existing.py", "mode": "100644", "type": "blob", "sha": "1"*40},
              {"path": "src/beta.py", "mode": "100644", "type": "blob", "sha": "3"*40}]
        if conflict:
            fb[-1]["path"] = "src/alpha.py"
            fb[-1]["sha"] = "4"*40
        self.trees = {"base-tree": base, "alpha-tree": fa, "beta-tree": fb}
        self.commits = {"a"*40: "base-tree", "b"*40: "alpha-tree",
                        "c"*40: "beta-tree"}

    def api(self, method, path, data=None):
        if method == "GET" and path.startswith("/git/commits/"):
            return {"tree": {"sha": self.commits[path.split("/")[-1]]}}
        if method == "GET" and path.startswith("/git/trees/"):
            key = path.split("/")[-1].split("?")[0]
            return {"tree": self.trees[key], "truncated": False}
        if method == "POST" and path == "/git/trees":
            self.created = data["tree"]
            return {"sha": "integrated-tree"}
        if method == "POST" and path == "/git/commits":
            assert data["parents"] == ["a"*40]
            return {"sha": "d"*40}
        if method == "POST" and path == "/git/refs":
            assert data["ref"].startswith("refs/heads/ef02/integrated/")
            return {"ref": data["ref"]}
        raise AssertionError((method, path, data))


def test_dependency_fanin_composes_distinct_candidate_file_trees():
    github = FakeIntegrationGitHub()
    outcome = github.integrated_source(source_sha="a"*40, attempt_id="combine-001",
                                       dependencies=[{"candidate_sha": "b"*40},
                                                     {"candidate_sha": "c"*40}])
    assert outcome["effective_sha"] == "d"*40
    assert outcome["integration_ref"].startswith("refs/heads/ef02/integrated/")
    assert {e["path"] for e in github.created} == {"src/alpha.py", "src/beta.py"}


def test_dependency_fanin_fails_on_file_collision_without_silent_merge():
    github = FakeIntegrationGitHub(conflict=True)
    with pytest.raises(RemoteTransportError, match="INTEGRATION_CONFLICT"):
        github.integrated_source(source_sha="a"*40, attempt_id="combine-002",
                                 dependencies=[{"candidate_sha": "b"*40},
                                               {"candidate_sha": "c"*40}])


def test_scheduler_passes_pinned_predecessor_results_to_worker():
    p = batch([action("a"), action("b", ("a",))])
    seen = {}
    def work(a):
        seen[a["action_id"]] = a.get("resolved_dependencies", [])
        return {"status": "CANDIDATE_READY", "candidate_sha": a["action_id"] * 40}
    assert run_schedule(p, work)["status"] == "COMPLETE"
    assert seen["a"] == []
    assert len(seen["b"]) == 1
    assert seen["b"][0]["candidate_sha"] == "a" * 40


def test_remote_schema_limits_parallel_request_not_fixed_slot_count():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["max_parallel"]["maximum"] == 128
    assert schema["properties"]["max_parallel"]["default"] == 2

def test_ef02_mxc_only_for_windows_codex_service():
    from ai_commerce.remote.worker import _remote_codex_sandbox_args
    assert _remote_codex_sandbox_args("codex", windows=True) == ["--codex-windows-sandbox", "mxc"]
    assert _remote_codex_sandbox_args("codex", windows=False) == []
    assert _remote_codex_sandbox_args("antigravity", windows=True) == []
    assert _remote_codex_sandbox_args("antigravity", windows=False) == []

def test_codex_capacity_failure_reports_distinct_code(tmp_path):
    from ai_commerce.remote.worker import _classify_failed_executor
    evidence = tmp_path / "ef01"
    evidence.mkdir()
    (evidence / "executor.stdout.log").write_text(
        '{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}',
        encoding="utf-8")
    report = {"status": "EXECUTOR_FAILED", "error_code": "EXECUTOR_NONZERO"}
    assert _classify_failed_executor(report, evidence) == "MODEL_AT_CAPACITY"


def test_capacity_classifier_does_not_confuse_quota_and_unsupported(tmp_path):
    from ai_commerce.remote.worker import _classify_failed_executor
    evidence = tmp_path / "ef01"
    evidence.mkdir()
    log = evidence / "executor.stderr.log"
    report = {"status": "EXECUTOR_FAILED"}
    log.write_text("429 too many requests", encoding="utf-8")
    assert _classify_failed_executor(report, evidence) == "QUOTA_EXHAUSTED"
    log.write_text("model is not supported", encoding="utf-8")
    assert _classify_failed_executor(report, evidence) == "MODEL_UNSUPPORTED"
    log.write_text("command crashed for an unrelated reason", encoding="utf-8")
    assert _classify_failed_executor(report, evidence) == "EXECUTOR_FAILED"

def test_approved_reuse_needs_exact_pinned_profile_and_source():
    from ai_commerce.remote.reuse import approve_reuse
    entry = dict(action_id="a", attempt_id="old-1", executor="codex", model="m",
                 effort="max", depends_on=[], allowed_paths=["tests/one.py"],
                 verification_profile="project-tests", source_sha="a"*40,
                 candidate_sha="b"*40, parent_sha="a"*40, tree_sha="c"*40,
                 verified_run_id=5, verification_status="BUILD_VERIFIED_FOR_SCOPE",
                 verify_exit_code=0, scope_valid=True)
    approved = {"version": 1, "approved_source_sha": "a"*40, "candidates": {"old-1": entry}}
    action = {k: entry[k] for k in ("action_id","attempt_id","executor","model","effort",
                                   "depends_on","allowed_paths","verification_profile")}
    action["reuse_candidate"] = {"candidate_sha": "b"*40, "verified_run_id": 5}
    assert approve_reuse({"source_sha": "a"*40}, action, approved) == entry
    tampered = copy.deepcopy(action)
    tampered["effort"] = "medium"
    with pytest.raises(RemoteRequestError, match="REUSE_PROFILE_OR_SCOPE_MISMATCH"):
        approve_reuse({"source_sha": "a"*40}, tampered, approved)
    tampered = copy.deepcopy(action)
    tampered["reuse_candidate"]["candidate_sha"] = "d"*40
    with pytest.raises(RemoteRequestError, match="REUSE_CANDIDATE_NOT_APPROVED"):
        approve_reuse({"source_sha": "a"*40}, tampered, approved)


def test_reuse_candidate_readonly_path_no_claim_no_model(tmp_path):
    from ai_commerce.remote.worker import LocalBatchWorker
    class StubGithub:
        calls = 0
        def verify_reused_candidate(self, entry):
            self.calls += 1
        def claim(self, **kwargs):
            raise AssertionError("must not claim or invoke provider")
    worker = object.__new__(LocalBatchWorker)
    worker.root = tmp_path
    worker.checkout = tmp_path
    worker.github = StubGithub()
    entry = dict(action_id="a", attempt_id="old-1", executor="codex", model="m",
                 effort="max", depends_on=[], allowed_paths=["tests/one.py"],
                 verification_profile="project-tests", source_sha="a"*40,
                 candidate_sha="b"*40, candidate_ref="refs/heads/ef02/candidate/key",
                 parent_sha="a"*40, tree_sha="c"*40,
                 verified_run_id=5, verification_status="BUILD_VERIFIED_FOR_SCOPE",
                 verify_exit_code=0, scope_valid=True)
    worker.approved_reuse = {"version": 1, "approved_source_sha": "a"*40,
                             "candidates": {"old-1": entry}}
    worker.batch = {"source_sha":"a"*40,"batch_id":"reuse-smoke"}
    act = {k: entry[k] for k in ("action_id","attempt_id","executor","model","effort",
                                "depends_on","allowed_paths","verification_profile")}
    act.update(instruction="No provider calls", reuse_candidate={"candidate_sha":"b"*40, "verified_run_id":5})
    result = worker.execute_action(act)
    assert result["status"] == "CANDIDATE_READY"
    assert result["reused_verified"] and not result["executor_invoked"]
    assert worker.github.calls == 1


def test_git_ref_reuse_verification_detects_mismatch():
    class FakeReuseTransport(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce","fake")
        def api(self, method, path, data=None):
            if "/candidate/" in path:
                return {"object":{"sha":"f"*40}}
            raise AssertionError("bad ref should fail before other reads")
    github=FakeReuseTransport()
    attempt="old-1"
    entry={"attempt_id":attempt,"candidate_sha":"b"*40,
           "candidate_ref":"refs/heads/ef02/candidate/"+github.attempt_key(attempt)}
    with pytest.raises(RemoteTransportError,match="REUSE_REF_SHA_CHANGED"):
        github.verify_reused_candidate(entry)
