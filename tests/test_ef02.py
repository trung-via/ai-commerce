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
    RemoteRequestError, validate_batch, verify_registry, safe_path, fingerprint, approved_acceptance_plan
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


def test_component_scope_accepts_nested_paths_not_unrelated_siblings():
    from ai_commerce.engineering.runner import _safe_scope
    a = action("a")
    a["allowed_paths"] = ["src/ai_commerce/engineering", "src/ai_commerce/remote",
                          "tests/test_ef01.py"]
    packet = batch([a])
    assert validate(packet) == packet
    assert _safe_scope(["src/ai_commerce/engineering/new_helper.py",
                        "src/ai_commerce/remote/scheduler.py",
                        "tests/test_ef01.py"], a["allowed_paths"])
    for name in ("src/ai_commerce/engineering_extra/z.py", "tests/test_ef02.py",
                 ".github/workflows/ef02-batch.yml", "src/ai_commerce/unrelated.py"):
        assert not _safe_scope([name], a["allowed_paths"])
    for name in ("../bad", ".git/config", ".local/cache", ".github/workflows/main.yml"):
        with pytest.raises(RemoteRequestError):
            safe_path(name)


def test_fingerprint_deterministic_and_model_change_is_distinct():
    p = batch([action("a")])
    assert fingerprint(p) == fingerprint(copy.deepcopy(p))
    alt = copy.deepcopy(p)
    alt["actions"][0]["model"] = "other-model"
    assert fingerprint(p) != fingerprint(alt)


def test_acceptance_declaration_is_v1_compatible_authorized_and_fingerprinted():
    a = action("a")
    a["instruction"] += '\nMT01_ACCEPTANCE_JSON: ["A12.product-evidence", "EF01.repair"]'
    packet = batch([a])
    assert validate(packet) == packet
    acceptance = approved_acceptance_plan(a)
    assert acceptance["ids"] == ["A12.product-evidence", "EF01.repair"]
    assert not acceptance["broad_required"]
    assert "tests/test_a12_claims.py" in acceptance["consumer_map"]["A12.product-evidence"]["tests"]
    changed = copy.deepcopy(packet)
    changed["actions"][0]["instruction"] += ' '
    assert fingerprint(packet) != fingerprint(changed)
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate(packet, issuer="untrusted-executor")


@pytest.mark.parametrize("declaration", [
    "", "MT01_ACCEPTANCE_JSON: []", "MT01_ACCEPTANCE_JSON: not-json",
    'MT01_ACCEPTANCE_JSON: ["A12.product-evidence"]\nMT01_ACCEPTANCE_JSON: ["EF01.repair"]',
    'MT01_ACCEPTANCE_JSON: ["A12.product-evidence", "A12.product-evidence"]',
    'MT01_ACCEPTANCE_JSON: ["--verify-json", "arbitrary-command"]',
    'MT01_ACCEPTANCE_JSON: {"argv": ["python", "-c", "arbitrary"]}',
    'MT01_ACCEPTANCE_JSON: ["AC-UNKNOWN"]',
])
def test_missing_ambiguous_or_unknown_acceptance_requires_broad_verification(declaration):
    a = action("a")
    a["instruction"] += "\n" + declaration
    acceptance = approved_acceptance_plan(a)
    assert acceptance["broad_required"] and acceptance["unknown_ids"]


@pytest.mark.parametrize("key,value", [("acceptance_ids", ["A12.product-evidence"]),
                                     ("verify_argv", ["python", "-c", "arbitrary"])])
def test_mt01_does_not_relax_v1_additional_properties(key, value):
    from jsonschema import ValidationError
    a = action("a")
    a[key] = value
    with pytest.raises(ValidationError):
        validate(batch([a]))


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
            return {"tree": {"sha": self.commits[path.split("/")[-1]]},
                    "message": "EF02 candidate attempt fixture"}
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
                                       dependencies=[{"candidate_sha": "b"*40, "status": "CANDIDATE_READY"},
                                                     {"candidate_sha": "c"*40, "status": "CANDIDATE_READY"}])
    assert outcome["effective_sha"] == "d"*40
    assert outcome["integration_ref"].startswith("refs/heads/ef02/integrated/")
    assert {e["path"] for e in github.created} == {"src/alpha.py", "src/beta.py"}


def test_dependency_fanin_fails_on_file_collision_without_silent_merge():
    github = FakeIntegrationGitHub(conflict=True)
    with pytest.raises(RemoteTransportError, match="INTEGRATION_CONFLICT"):
        github.integrated_source(source_sha="a"*40, attempt_id="combine-002",
                                 dependencies=[{"candidate_sha": "b"*40, "status": "CANDIDATE_READY"},
                                               {"candidate_sha": "c"*40, "status": "CANDIDATE_READY"}])


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
    assert result["status"] == "UNPROVEN"
    assert result["error_code"] == "LEGACY_REUSE_MT01_EVIDENCE_REQUIRED"
    assert not result["reused_verified"] and not result["executor_invoked"]
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


def test_remote_pre_spawn_ref_is_create_only_and_chained_to_exact_claim():
    fp = "f" * 64
    source = "a" * 40
    claim_sha = "b" * 40
    tree_sha = "c" * 40

    class CheckpointTransport(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce", "fixture-token")
            self.writes = []
        def api(self, method, path, data=None):
            if method == "GET" and path.startswith("/git/ref/heads/ef02/claims/"):
                return {"object": {"sha": claim_sha}}
            if method == "GET" and path == "/git/commits/" + claim_sha:
                return {"message": "EF02 CLAIM\n\nfingerprint=" + fp +
                        "\nattempt_id=checkpoint-001",
                        "tree": {"sha": tree_sha}, "parents": [{"sha": source}]}
            if method == "POST" and path == "/git/commits":
                self.writes.append((path, data))
                return {"sha": "d" * 40}
            if method == "POST" and path == "/git/refs":
                self.writes.append((path, data))
                return {}
            raise AssertionError("unexpected operation: " + method + " " + path)

    t = CheckpointTransport()
    ref = t.pre_spawn_checkpoint(source_sha=source,
                                 attempt_id="checkpoint-001", fingerprint=fp)
    assert ref == "refs/heads/ef02/pre-spawn/" + t.attempt_key("checkpoint-001")
    assert len(t.writes) == 2
    commit = t.writes[0][1]
    assert commit["tree"] == tree_sha
    assert commit["parents"] == [claim_sha]
    assert commit["message"] == "EF02 PRESPAWN\n\nfingerprint=" + fp + "\nattempt_id=checkpoint-001"
    assert t.writes[1][1] == {"ref": ref, "sha": "d" * 40}


def test_remote_pre_spawn_ref_rejects_wrong_claim_parent_before_any_write():
    class BadClaim(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce", "fixture-token")
        def api(self, method, path, data=None):
            assert method == "GET", "must not create any commit/ref"
            if "/git/ref/" in path:
                return {"object": {"sha": "b" * 40}}
            return {"message": "EF02 CLAIM\n\nfingerprint=" + "f" * 64 +
                    "\nattempt_id=bad-claim",
                    "tree": {"sha": "c" * 40},
                    "parents": [{"sha": "d" * 40}]}
    with pytest.raises(RemoteTransportError, match="PRESPAWN_CLAIM_IDENTITY_MISMATCH"):
        BadClaim().pre_spawn_checkpoint(source_sha="a" * 40,
                                        attempt_id="bad-claim", fingerprint="f" * 64)


def test_spawned_remote_checkpoint_links_pre_spawn_and_pid():
    source = "a" * 40
    fp = "f" * 64
    attempt = "spawned-pid-001"
    claim_sha = "b" * 40
    pre_sha = "c" * 40
    tree_sha = "d" * 40

    class StageTransport(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce", "fixture")
            self.writes = []
        def api(self, method, path, data=None):
            if method == "GET" and "/git/ref/heads/ef02/claims/" in path:
                return {"object": {"sha": claim_sha}}
            if method == "GET" and "/git/ref/heads/ef02/pre-spawn/" in path:
                return {"object": {"sha": pre_sha}}
            if method == "GET" and path == "/git/commits/" + claim_sha:
                return {"message": "EF02 CLAIM\n\nfingerprint=" + fp +
                        "\nattempt_id=" + attempt, "tree": {"sha": tree_sha},
                        "parents": [{"sha": source}]}
            if method == "GET" and path == "/git/commits/" + pre_sha:
                return {"message": "EF02 PRESPAWN\n\nfingerprint=" + fp +
                        "\nattempt_id=" + attempt, "tree": {"sha": tree_sha},
                        "parents": [{"sha": claim_sha}]}
            self.writes.append((method, path, data))
            if method == "POST" and path == "/git/commits":
                return {"sha": "e" * 40}
            if method == "POST" and path == "/git/refs":
                return {"ref": data["ref"]}
            raise AssertionError((method, path, data))

    github = StageTransport()
    ref = github.spawned_checkpoint(source_sha=source, attempt_id=attempt,
                                    fingerprint=fp, child_pid=4312)
    assert ref == "refs/heads/ef02/spawned/" + github.attempt_key(attempt)
    assert len(github.writes) == 2
    assert github.writes[0][2] == {
        "message": "EF02 SPAWNED\n\nfingerprint=" + fp +
                   "\nattempt_id=" + attempt + "\nchild_pid=4312",
        "tree": tree_sha, "parents": [pre_sha]}
    assert github.writes[1][2] == {"ref": ref, "sha": "e" * 40}


def test_spawned_checkpoint_rejects_untrusted_parent_without_writes():
    class Fake(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce", "fixture")
            self.writes = 0
        def api(self, method, path, data=None):
            if method == "POST":
                self.writes += 1
                raise AssertionError("unexpected write")
            if "/git/ref/heads/ef02/claims/" in path:
                return {"object": {"sha": "b" * 40}}
            if path == "/git/commits/" + "b" * 40:
                return {"message": "EF02 CLAIM\n\nfingerprint=" + "f" * 64 +
                        "\nattempt_id=incorrect",
                        "parents": [{"sha": "a" * 40}], "tree": {"sha": "c" * 40}}
            raise AssertionError(path)
    fake = Fake()
    with pytest.raises(RemoteTransportError, match="SPAWNED_CLAIM_IDENTITY_MISMATCH"):
        fake.spawned_checkpoint(source_sha="a" * 40, attempt_id="expected",
                                fingerprint="f" * 64, child_pid=2345)
    assert fake.writes == 0
    with pytest.raises(RemoteTransportError, match="SPAWNED_PID_INVALID"):
        fake.spawned_checkpoint(source_sha="a" * 40, attempt_id="expected",
                                fingerprint="f" * 64, child_pid=0)


def test_ef02_batch_ingress_is_not_a_legacy_m0r_probe():
    """Keep Issue ingress via trusted router and manual dispatch, without M0-R probe."""
    workflow = (ROOT / ".github" / "workflows" / "ef02-batch.yml").read_text(encoding="utf-8")
    router = (ROOT / ".github" / "workflows" / "issue-ingress.yml").read_text(encoding="utf-8")
    assert "types: [opened, labeled]" in router
    assert "startsWith(github.event.issue.title, '[EF02]')" in router
    assert "uses: ./.github/workflows/ef02-batch.yml" in router
    assert "workflow_call:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "startsWith(github.event.issue.title, '[EF02]')" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "needs.validate.result == 'success'" in workflow
    assert "m0r_pre_executor_cancel" not in workflow
    assert "M0-R pre-executor cancellation probe" not in workflow



def test_authenticated_human_full_is_distinct_from_default_profile(monkeypatch):
    from ai_commerce.remote.validation import approved_verification_plan
    registry = verify_registry(REGISTRY)
    packet = batch([action("a")])
    packet["actions"][0]["verification_intent"] = "HUMAN_FULL"
    assert validate(packet) == packet
    assert fingerprint(packet) != fingerprint(batch([action("a")]))
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    human = approved_verification_plan(packet["actions"][0], registry)
    assert human["human_full_suite_authorized"] is True
    assert human["argv"] == registry["project-tests"]
    assert human["reason"] == "HUMAN_AUTHENTICATED_FULL_SUITE"
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate(packet, issuer="untrusted")
    brain = copy.deepcopy(packet)
    brain["actor"] = "BRAIN"
    with pytest.raises(RemoteRequestError, match="HUMAN_FULL_REQUIRES_OWNER"):
        validate(brain, issuer="brain-bot", brain=("brain-bot",))
    narrow = copy.deepcopy(packet)
    narrow["actions"][0]["verification_profile"] = "ef02-tests"
    with pytest.raises(RemoteRequestError, match="HUMAN_FULL_REQUIRES_OWNER"):
        validate(narrow)
    default_plan = approved_verification_plan(action("a"), registry)
    assert default_plan["human_full_suite_authorized"] is False
    assert default_plan["coverage"] == "V21_REQUIRES_POST_EXECUTOR_IMPACT_PROOF"
    serial = action("a")
    serial["verification_profile"] = "project-tests-serial"
    assert approved_verification_plan(serial, registry)["human_full_suite_authorized"] is False


def test_ef02_independent_receipt_accepts_correct_profile_and_rejects_alias(tmp_path):
    """Exact artifact hashes + registered profile identity; not a mocked PASS."""
    from ai_commerce.remote.worker import _verified_receipt_evidence
    from ai_commerce.engineering.minimum_test import Evidence, file_hashes
    workspace = tmp_path / "worktree"
    root = tmp_path / "evidence"
    workspace.mkdir()
    root.mkdir()
    (workspace / "impl.py").write_text("verified", encoding="utf-8")
    source = "a" * 40
    command = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {
        "mt01-component-onboarding": command,
        "mt01-mt01-v21-tests": command,
        "unrelated-alias": ["python", "-m", "pytest", "tests/test_ef02.py", "-q", "-n", "12"],
    }
    binding = {
        "base_sha": source, "aggregate_sources": [],
        "workspace": str(workspace.resolve()),
        "candidate_files": {"impl.py": file_hashes(workspace)["impl.py"]},
        "registered_profile": "mt01-mt01-v21-tests",
        "argv": command, "environment_proven": True,
    }
    refs = {}
    hashes = {}

    def store(key, filename, data):
        (root / filename).write_bytes(data)
        refs[key] = filename
        hashes[key] = hashlib.sha256(data).hexdigest()

    store("tracked_diff", "tracked.patch", b"diff --git a/impl.py b/impl.py\n")
    store("source_after_verify", "source.after.json", b'{"clean": true}')
    store("verify_stdout", "verify.stdout.log", b"1 passed\n")
    store("provider_return_observed", "provider-return.json", b'{"observed": true}')
    passed = b"1 passed in 0.01s\nMT01_XDIST_WORKERS_COLLECTED=12\n"
    store("mt01_0_stdout", "mt01.stdout.log", passed)
    proof = Evidence.from_success(binding, exit_code=0, stdout=passed,
                                  artifacts={"stdout": refs["mt01_0_stdout"]},
                                  evidence_root=root)
    store("mt01_0_evidence", "mt01.evidence.json", proof.payload.encode("utf-8"))
    report = {
        "verify_completed": True, "provider_return_determinate": True,
        "evidence_files": refs, "evidence_sha256": hashes,
        "tracked_diff_sha256": hashes["tracked_diff"],
        "minimum_test_enabled": True,
        "changed_files": ["impl.py"],
        "minimum_test": {"groups": [
            {"group": "mt01-v21", "binding": binding,
             "evidence_sha256": proof.sha256},
        ]},
    }
    args = dict(root=root, workspace=workspace, source_sha=source,
                sources=[], profiles=profiles, allowed_paths=["impl.py"])
    assert _verified_receipt_evidence(report, **args) is True
    import copy
    for forged_name in ("mt01-component-onboarding", "unrelated-alias", "unknown-profile"):
        bad = copy.deepcopy(report)
        bad["minimum_test"]["groups"][0]["binding"]["registered_profile"] = forged_name
        diagnostics = []
        assert _verified_receipt_evidence(bad, **args, diagnostics=diagnostics) is False
        assert diagnostics == ["MT01_GROUP_0_REGISTERED_BINDING_REJECTED"]
    tampered = copy.deepcopy(report)
    tampered["evidence_sha256"]["tracked_diff"] = "0" * 64
    assert _verified_receipt_evidence(tampered, **args) is False
    for bad_stdout in (
        b"1 passed in 0.01s\n",
        b"1 passed in 0.01s\nMT01_XDIST_WORKERS_COLLECTED=4\n",
        b"1 passed in 0.01s\\nMT01_XDIST_WORKERS_COLLECTED=12\\n",
    ):
        bad_xdist = copy.deepcopy(report)
        (root / "mt01.stdout.log").write_bytes(bad_stdout)
        bad_xdist["evidence_sha256"]["mt01_0_stdout"] = hashlib.sha256(bad_stdout).hexdigest()
        bad_proof = Evidence.from_success(
            binding, exit_code=0, stdout=bad_stdout,
            artifacts={"stdout": refs["mt01_0_stdout"]}, evidence_root=root)
        (root / "mt01.evidence.json").write_bytes(bad_proof.payload.encode("utf-8"))
        bad_xdist["evidence_sha256"]["mt01_0_evidence"] = bad_proof.sha256
        bad_xdist["minimum_test"]["groups"][0]["evidence_sha256"] = bad_proof.sha256
        diagnostics = []
        assert _verified_receipt_evidence(bad_xdist, **args, diagnostics=diagnostics) is False
        assert diagnostics == ["MT01_GROUP_0_XDIST_UNPROVEN"]

# Minimal immutable retirement security regression; no WorkOwner runtime.
import pytest

from ai_commerce.remote.validation import (
    RemoteRequestError, reject_retired_wp_identifiers,
)
from ai_commerce.remote.worker import LocalBatchWorker


@pytest.mark.parametrize("key,value", [
    ("batch_id", "EPWO-REPLAY"),
    ("action_id", "WP01-a"),
    ("attempt_id", "WP06-a"),
    ("action_id", "wP03-mixed"),
])
def test_retired_machine_identifier_denied(key, value):
    batch = {"batch_id": "fresh-nonwp", "actions": [{
        "action_id": "safe", "attempt_id": "safe-attempt",
    }]}
    if key == "batch_id":
        batch["batch_id"] = value
    else:
        batch["actions"][0][key] = value
    with pytest.raises(RemoteRequestError, match="RETIRED_WP_NOT_DISPATCHABLE"):
        reject_retired_wp_identifiers(batch)


def test_unrelated_nonwp_prose_is_not_a_program_identifier():
    reject_retired_wp_identifiers({
        "batch_id": "safe-batch",
        "description": "Historical EPWO audit must remain read only",
        "actions": [{"action_id": "fresh", "attempt_id": "fresh-1"}],
    })


def test_direct_worker_no_claim_no_provider_on_retired_id(tmp_path):
    worker = LocalBatchWorker.__new__(LocalBatchWorker)
    worker.root = tmp_path
    worker.batch = {"batch_id": "safe-batch", "source_sha": "a" * 40}
    action = {
        "action_id": "WP01-replay", "attempt_id": "old-attempt",
        "executor": "codex", "model": "gpt-6.1-sol", "effort": "xhigh",
        "depends_on": [],
    }
    outcome = worker.execute_action(action)
    assert outcome["status"] == "INVALID_ACTION"
    assert outcome["error_code"] == "RETIRED_WP_NOT_DISPATCHABLE"
    assert outcome["executor_invoked"] is False

def test_new_worktree_path_is_short_stable_and_unique(tmp_path):
    """Windows EF02 must never derive a tool cwd from a long action_id."""
    from ai_commerce.remote.worker import _new_worktree_path
    import subprocess
    checkout = tmp_path / "runner" / "checkout"
    checkout.mkdir(parents=True)
    first = _new_worktree_path(checkout)
    second = _new_worktree_path(checkout)
    assert first != second
    assert first.parent == checkout.parent / "_ef02_worktrees"
    assert not first.exists() and not second.exists()
    assert first.name.startswith("wt-")
    # A real Git worktree at this location remains available to rev-parse.
    subprocess.run(["git", "-C", str(checkout), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "fixture@example.invalid"], check=True)
    (checkout / "README.md").write_text("base\\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(checkout), "worktree", "add", "--detach", str(first), "HEAD"],
                   check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(first), "rev-parse", "HEAD"],
                          check=True, text=True, capture_output=True).stdout.strip()
    assert len(head) == 40
    assert (first / ".git").is_file()


def test_agy_stream_p0b_opt_in_is_owner_only_and_does_not_affect_regular_ef02():
    """Only an owner-created opened P0-B Issue can enable opt-in profiling.

    This checks the protected workflow's exact source contract, not a claimed
    native/provider execution. Regular and workflow_dispatch batches stay off.
    """
    workflow = (ROOT / ".github/workflows/ef02-batch.yml").read_text(encoding="utf-8")
    marker = "AI_COMMERCE_EF01_AGY_STREAM_PROFILE:"
    assert workflow.count(marker) == 1
    execute = workflow.split("- name: Execute bounded concurrent EF01 jobs", 1)[1]
    assert marker in execute
    expression = next(line.strip() for line in execute.splitlines()
                      if line.strip().startswith(marker))
    assert "github.event_name == 'issues'" in expression
    assert "github.event.action == 'opened'" in expression
    assert "github.repository == 'trung-via/ai-commerce'" in expression
    assert "github.actor == github.repository_owner" in expression
    assert "github.event.issue.user.login == github.repository_owner" in expression
    assert "startsWith(github.event.issue.title, '[EF02][P0B-AGY-STREAM-SMOKE] ')" in expression
    assert "&& '1' || '0' }}" in expression
    assert "vars.EF02_AGY_STREAM_PROFILE" not in workflow
    assert "dangerously-skip-permissions" not in workflow


def test_ef02_verified_receipt_accepts_committed_manifest_and_rejects_xdist_or_hash_tamper(tmp_path):
    """EF02 independently verifies committed manifest bindings and -n 12 evidence."""
    import copy
    import subprocess
    import sys
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    from ai_commerce.engineering.minimum_test import (
        Evidence, TestGroup, digest, file_hashes,
    )
    from ai_commerce.remote.worker import _verified_receipt_evidence

    workspace = tmp_path / "worktree"
    ev_root = tmp_path / "evidence"
    workspace.mkdir()
    ev_root.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=workspace, check=True,
            capture_output=True, text=True).stdout.strip()

    def write(rel, content):
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    git("init", "-q")
    git("config", "user.name", "MT01")
    git("config", "user.email", "mt01@example.invalid")
    git("config", "core.autocrlf", "false")
    src = "src/ai_commerce/alpha/mod.py"
    test_file = "tests/test_alpha.py"
    write(src, "def val():\n    return 1\n")
    write(test_file, "def test_alpha():\n    assert True\n")
    manifest = {
        "schema_version": "MT01ComponentOwner.v1", "component": "alpha",
        "sources": [src], "tests": [test_file], "acs": ["ALPHA.offline"],
        "consumers": [], "contracts": [],
    }
    write("work_items/mt01-components/alpha.json", json.dumps(manifest) + "\n")
    git("add", ".")
    git("commit", "-qm", "baseline")
    base_sha = git("rev-parse", "HEAD")
    base_files = file_hashes(workspace)

    # Candidate edits only the owned source (manifest remains unchanged baseline).
    write(src, "def val():\n    return 2\n")
    profiles = {
        "project-tests": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
        "mt01-component-onboarding": [
            "python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"],
    }
    plan = registered_action_plan(
        repo=workspace, base_sha=base_sha, base_files=base_files,
        candidate_files=file_hashes(workspace), changed_paths=[src],
        required_acs=("ALPHA.offline",), fallback_argv=profiles["project-tests"],
        groups=(), registered_profiles=profiles, allowed_paths=[src])
    assert plan["selected_groups"] == ["alpha"]
    binding = copy.deepcopy(plan["groups"][0]["binding"])
    # Anchor python provenance deterministically for offline receipt creation.
    binding["executable_sha256"] = digest(Path(sys.executable).read_bytes())
    binding["environment_proven"] = True

    refs, hashes = {}, {}

    def store(key, filename, data):
        (ev_root / filename).write_bytes(data)
        refs[key] = filename
        hashes[key] = hashlib.sha256(data).hexdigest()

    store("tracked_diff", "tracked.patch", b"diff --git a/x b/x\n")
    store("source_after_verify", "source.after.json", b'{"clean": true}')
    store("verify_stdout", "verify.stdout.log", b"1 passed\n")
    store("provider_return_observed", "provider-return.json", b'{"observed": true}')
    passed = b"1 passed in 0.01s\nMT01_XDIST_WORKERS_COLLECTED=12\n"
    store("mt01_0_stdout", "mt01.stdout.log", passed)
    proof = Evidence.from_success(
        binding, exit_code=0, stdout=passed,
        artifacts={"stdout": refs["mt01_0_stdout"]}, evidence_root=ev_root)
    store("mt01_0_evidence", "mt01.evidence.json", proof.payload.encode("utf-8"))

    report = {
        "verify_completed": True, "provider_return_determinate": True,
        "evidence_files": refs, "evidence_sha256": hashes,
        "tracked_diff_sha256": hashes["tracked_diff"],
        "minimum_test_enabled": True, "changed_files": [src],
        "minimum_test": {"groups": [
            {"group": "alpha", "binding": binding, "evidence_sha256": proof.sha256},
        ]},
    }
    args = dict(root=ev_root, workspace=workspace, source_sha=base_sha,
                sources=[], profiles=profiles, allowed_paths=[src])
    assert _verified_receipt_evidence(report, **args) is True

    # Missing -n 12 worker collection marker -> rejected.
    bad_xdist = copy.deepcopy(report)
    bad_stdout = b"1 passed in 0.01s\nMT01_XDIST_WORKERS_COLLECTED=4\n"
    (ev_root / "mt01.stdout.log").write_bytes(bad_stdout)
    bad_xdist["evidence_sha256"]["mt01_0_stdout"] = hashlib.sha256(bad_stdout).hexdigest()
    bad_proof = Evidence.from_success(
        binding, exit_code=0, stdout=bad_stdout,
        artifacts={"stdout": refs["mt01_0_stdout"]}, evidence_root=ev_root)
    (ev_root / "mt01.evidence.json").write_bytes(bad_proof.payload.encode("utf-8"))
    bad_xdist["evidence_sha256"]["mt01_0_evidence"] = bad_proof.sha256
    bad_xdist["minimum_test"]["groups"][0]["evidence_sha256"] = bad_proof.sha256
    diagnostics = []
    assert _verified_receipt_evidence(bad_xdist, **args, diagnostics=diagnostics) is False
    assert diagnostics == ["MT01_GROUP_0_XDIST_UNPROVEN"]

    # Out-of-scope allowed_paths -> rejected.
    diagnostics = []
    assert _verified_receipt_evidence(
        bad_xdist, **{**args, "allowed_paths": ["src/ai_commerce/other"]},
        diagnostics=diagnostics) is False
    assert diagnostics == ["MT01_GROUP_0_REGISTERED_BINDING_REJECTED"]


def test_temporary_human_full_verification_budget_is_bounded_and_opt_in():
    from ai_commerce.remote.worker import _temporary_verification_budget
    from ai_commerce.engineering.runner import DEFAULT_ACTION_TIMEOUT_SECONDS

    # Normal AUTO/owner-first actions are NOT granted the long-test budget.
    assert _temporary_verification_budget(human_full_suite_authorized=False) == (
        None, DEFAULT_ACTION_TIMEOUT_SECONDS, 7500,
    )
    # An already-authorized HUMAN_FULL profile receives 180m of pytest inside
    # a finite 300m action and a 305m EF02 child-process watchdog.
    verify, action, child = _temporary_verification_budget(human_full_suite_authorized=True)
    assert (verify, action, child) == (10800, 18000, 18300)
    assert verify < action < child < 360 * 60


AC_MARKER = "MT01_ACCEPTANCE_JSON:"
PERSONAL_SOURCE = "src/ai_commerce/hostops_v3/personal.py"
PERSONAL_AC = "HOSTOPS.PERSONAL.chat-approved-public-read-owner-comment-new-scratch-write"
WAKE_SOURCE = "src/ai_commerce/remote/completion_event.py"
VALIDATION_SOURCE = "src/ai_commerce/remote/validation.py"


def scoped_action(name, paths, ids, depends=()):
    item = action(name, depends)
    item["allowed_paths"] = paths
    item["instruction"] = (AC_MARKER + json.dumps(ids) + "\n" if ids is not None else "") + "PRIVATE_INSTRUCTIONS"
    item["verification_profile"] = "mt01-mt01-tests"
    return item


def make_acceptance_repo(tmp_path, monkeypatch, *, contents=None, modes=None):
    """Offline Git object transport, with real reviewed groups/manifest parser.

    No Git init/commit, test invocation or supplied owner map. All static groups
    and current committed manifest shapes remain intact; only source imports
    are minimal fixture bytes. The production snapshot reader verifies hashes.
    """
    from ai_commerce.engineering.minimum_test import approved_groups

    files = {p: b"# pinned fixture\r\n" for g in approved_groups() for p in
             (*g.paths, *g.tests, *g.consumers, *g.contracts)}
    for manifest in sorted((ROOT / "work_items/mt01-components").glob("*.json")):
        raw = manifest.read_bytes()
        data = json.loads(raw)
        files[manifest.relative_to(ROOT).as_posix()] = raw
        for key in ("sources", "tests", "consumers", "contracts"):
            for path in data[key]:
                files.setdefault(path, b"# pinned fixture\r\n")
    for path in ("contracts/RemoteActionBatch.v1.schema.json", ".github/ef02/verify-profiles.json",
                 ".github/ef02/approved-candidates.v1.json"):
        files[path] = (ROOT / path).read_bytes()
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[path])
    files.update(contents or {})
    (tmp_path / ".git").mkdir()
    modes = modes or {}
    objects, entries = {}, b""
    for path, raw in sorted(files.items()):
        oid = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest().encode("ascii")
        objects[oid] = raw
        entries += (modes.get(path, b"100644") + b" blob " + oid + b" "
                    + str(len(raw)).encode("ascii") + b"\t" + path.encode("utf-8") + b"\0")
    calls = []

    def read_git(argv, **kwargs):
        args = argv[3:] if argv[1:2] == ["-C"] else argv[1:]
        calls.append(args)
        if args == ["rev-parse", "HEAD"]:
            output = "a" * 40 + "\n"
        elif args == ["cat-file", "-t", "a" * 40]:
            output = "commit\n"
        elif args == ["ls-tree", "-r", "-l", "-z", "a" * 40]:
            output = entries
        elif args == ["ls-tree", "-r", "--name-only", "-z", "a" * 40]:
            output = b"\0".join(p.encode("utf-8") for p in sorted(files)) + b"\0"
        elif args[:1] == ["show"] and args[1].startswith("a" * 40 + ":"):
            output = files[args[1].split(":", 1)[1]]
        elif args == ["cat-file", "--batch"]:
            output = b"".join(oid + b" blob " + str(len(objects[oid])).encode("ascii")
                              + b"\n" + objects[oid] + b"\n" for oid in kwargs["input"].splitlines())
        else:
            pytest.fail("Unexpected Git effect: " + repr(args))
        return subprocess.CompletedProcess(argv, 0, output, "" if isinstance(output, str) else b"")

    monkeypatch.setattr(subprocess, "run", read_git)
    return files, calls


def malformed_scope_action(problem):
    item = scoped_action("b", [VALIDATION_SOURCE], ["EF02.action-and-fanin"])
    if problem == "missing":
        item["instruction"] = "PRIVATE_INSTRUCTIONS"
    elif problem == "malformed":
        item["instruction"] = AC_MARKER + "{bad JSON}\nPRIVATE_INSTRUCTIONS"
    elif problem == "duplicate-ids":
        item["instruction"] = AC_MARKER + '["EF02.action-and-fanin", "EF02.action-and-fanin"]\nPRIVATE_INSTRUCTIONS'
    elif problem == "historical-duplicate":
        # #913: correct HEAD declaration, then the same prefix quoted in prose.
        item["instruction"] += '\nPRIOR EVIDENCE: the historical action declared `' + AC_MARKER + '["EF02.action-and-fanin"]` in its task body.'
    elif problem == "unknown":
        item["instruction"] = AC_MARKER + '["EF02.action-and-fani"]\nPRIVATE_INSTRUCTIONS'
    elif problem == "owner":
        item["allowed_paths"] = [PERSONAL_SOURCE, "tests/test_hostops_personal.py"]
        item["instruction"] = AC_MARKER + '["EF02.wake-boundaries"]\nPRIVATE_INSTRUCTIONS'
    return item


@pytest.mark.parametrize("command", ["validate", "run"])
@pytest.mark.parametrize("problem", ["missing", "malformed", "duplicate-ids", "historical-duplicate", "unknown", "owner"])
@pytest.mark.parametrize("full", [False, True])
def test_pre_executor_rejects_whole_batch_before_any_effect(tmp_path, monkeypatch, capsys, command, problem, full):
    from ai_commerce.remote import __main__ as cli, worker
    from ai_commerce.engineering import adapters

    _, calls = make_acceptance_repo(tmp_path, monkeypatch)
    good = scoped_action("a", [WAKE_SOURCE], ["EF02.wake-boundaries"])
    bad = malformed_scope_action(problem)
    if full:
        bad.update(verification_intent="HUMAN_FULL", verification_profile="project-tests")
    packet = batch([good, bad])
    original = copy.deepcopy(packet)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    effects = []

    def forbidden(*args, **kwargs):
        effects.append(True)
        pytest.fail("No transport, claim, worktree, worker, spawn, model or schedule permitted")

    for name in ("GitHubTransport", "LocalBatchWorker", "run_schedule"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(GitHubTransport, "claim", forbidden)
    monkeypatch.setattr(worker, "_new_worktree_path", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(adapters, "command_for", forbidden)
    assert cli.main([command, "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 2
    captured = capsys.readouterr()
    code = "AC_SCOPE_MISMATCH_PRE_EXECUTOR" if problem == "owner" else "AC_DECLARATION_UNPROVEN_PRE_EXECUTOR"
    assert captured.err.strip() == "EF02_REQUEST_REJECTED: " + code
    assert "PRIVATE_INSTRUCTIONS" not in captured.out + captured.err
    assert effects == [] and packet == original
    assert before == {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    assert calls[0] == ["cat-file", "-t", "a" * 40]
    assert calls[1] == ["ls-tree", "-r", "-l", "-z", "a" * 40]
    if problem == "historical-duplicate":
        assert approved_acceptance_plan(bad)["broad_required"] is True


@pytest.mark.parametrize("path,ac,owner", [
    (PERSONAL_SOURCE, PERSONAL_AC, "hostops-personal"),
    (WAKE_SOURCE, "EF02.wake-boundaries", "ef02-wake"),
    ("src/ai_commerce/wakehub/native_runtime.py", "WAKEHUB.G2.native-source-custody", "wakehub-g2-native"),
    ("src/ai_commerce/engineering/__main__.py", "EF01.execution", "ef01"),
    ("src/ai_commerce/production_runtime/state_machine.py", "B0.1.pa2-independent-runtime-contract-and-roadmap", "b0-1"),
    (VALIDATION_SOURCE, "EF02.action-and-fanin", "ef02"),
])
def test_pre_executor_valid_real_owner_declarations(tmp_path, monkeypatch, path, ac, owner):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_acceptance_repo(tmp_path, monkeypatch)
    item = scoped_action("a", [path], [ac])
    packet = batch([item])
    original = copy.deepcopy(packet)
    result = validate_acceptance_scope(packet, repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_COMPATIBLE"
    assert owner in result["actions"][0]["selected_groups"]
    assert result["next_gate"] == "POST_EDIT_MT01_REQUIRED"
    assert approved_acceptance_plan(item)["ids"] == [ac]
    assert fingerprint(packet) == fingerprint(original)


def test_pre_executor_import_consumer_and_transitive_dag_fanin(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    product = "src/ai_commerce/product/identity.py"
    attribution = "src/ai_commerce/affiliate_attribution/evidence.py"
    make_acceptance_repo(tmp_path, monkeypatch, contents={
        attribution: b"from ai_commerce.product import identity\n"})
    packet = batch([scoped_action("a", [product], ["A14.L04.offline-attribution-evidence"])])
    assert validate_acceptance_scope(packet, repo=tmp_path)["status"] == "PRE_EXECUTOR_COMPATIBLE"
    packet = batch([
        scoped_action("a", [WAKE_SOURCE], ["EF02.wake-boundaries"]),
        scoped_action("b", [PERSONAL_SOURCE], [PERSONAL_AC], depends=("a",)),
        scoped_action("c", [VALIDATION_SOURCE], ["EF02.wake-boundaries", PERSONAL_AC], depends=("b",)),
    ])
    assert validate_acceptance_scope(packet, repo=tmp_path)["status"] == "PRE_EXECUTOR_COMPATIBLE"
    packet["actions"][2]["depends_on"] = []
    with pytest.raises(RemoteRequestError, match="^AC_SCOPE_MISMATCH_PRE_EXECUTOR$"):
        validate_acceptance_scope(packet, repo=tmp_path)


@pytest.mark.parametrize("path", ["src/ai_commerce/new_component.py", "src/ai_commerce/hostops_v3",
    "work_items/mt01-components/new-owner.json", "work_items/mt01-components/hostops-personal.json"])
def test_pre_executor_new_owner_or_directory_is_hold_even_with_invalid_declaration(tmp_path, monkeypatch, path):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_acceptance_repo(tmp_path, monkeypatch)
    result = validate_acceptance_scope(batch([scoped_action("a", [path], None)]), repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["next_gate"] == "HOLD_NO_EXECUTOR"
    assert result["actions"][0]["reason"] == "ALLOWED_SCOPE_NOT_EXACT_ESTABLISHED_FILES"


@pytest.mark.parametrize("problem", ["dynamic", "unknown-consumer", "unowned", "forged-owner",
    "symlink", "bytes", "header", "trailing", "empty", "unavailable"])
def test_pre_executor_uncertain_graph_or_pinned_bytes_always_hold(tmp_path, monkeypatch, problem):
    from ai_commerce.remote.validation import validate_acceptance_scope
    contents, source, modes = {}, PERSONAL_SOURCE, {}
    if problem == "dynamic":
        contents[source] = b"import importlib\nimportlib.import_module(name)\n"
    elif problem == "unknown-consumer":
        contents["src/ai_commerce/unowned.py"] = b"from ai_commerce.hostops_v3 import personal\n"
    elif problem == "unowned":
        source = "src/ai_commerce/unowned.py"
        contents[source] = b"# no reviewed owner\n"
    elif problem == "forged-owner":
        path = "work_items/mt01-components/hostops-personal.json"
        manifest = json.loads((ROOT / path).read_bytes())
        manifest["acs"] = ["EF02.wake-boundaries"]
        contents[path] = json.dumps(manifest).encode()
    elif problem == "symlink":
        modes[source] = b"120000"
    make_acceptance_repo(tmp_path, monkeypatch, contents=contents, modes=modes)
    original_run = subprocess.run

    def read(argv, **kwargs):
        result = original_run(argv, **kwargs)
        if argv[-2:] == ["cat-file", "--batch"]:
            if problem == "unavailable":
                return subprocess.CompletedProcess(argv, 1, b"", b"PRIVATE_INSTRUCTIONS")
            output = result.stdout
            if problem == "bytes":
                header, raw = output.split(b"\n", 1)
                output = header + b"\n" + bytes([raw[0] ^ 1]) + raw[1:]
            elif problem == "header":
                output = output.replace(b" blob ", b" tree ", 1)
            elif problem == "trailing":
                output += b"unrequested bytes\n"
            elif problem == "empty":
                output = b""
            return subprocess.CompletedProcess(argv, 0, output, b"")
        return result

    monkeypatch.setattr(subprocess, "run", read)
    result = validate_acceptance_scope(batch([scoped_action("a", [source], [PERSONAL_AC])]), repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["next_gate"] == "HOLD_NO_EXECUTOR"
    assert "PRIVATE_INSTRUCTIONS" not in json.dumps(result)


def test_pre_executor_ignores_forged_checkout_imports_and_owner_manifest(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_acceptance_repo(tmp_path, monkeypatch)
    for path, raw in {
        WAKE_SOURCE: b"from ai_commerce.hostops_v3 import personal\n",
        "work_items/mt01-components/hostops-personal.json": b'{"acs": ["EF02.wake-boundaries"]}',
    }.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    with pytest.raises(RemoteRequestError, match="^AC_SCOPE_MISMATCH_PRE_EXECUTOR$"):
        validate_acceptance_scope(batch([scoped_action("a", [PERSONAL_SOURCE], ["EF02.wake-boundaries"])]), repo=tmp_path)


@pytest.mark.parametrize("command", ["validate", "run"])
def test_cli_holds_unknown_scope_without_transport_or_worker(tmp_path, monkeypatch, capsys, command):
    from ai_commerce.remote import __main__ as cli
    make_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([scoped_action("a", ["src/ai_commerce/new_component.py"], ["NEW.offline"])])
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    def forbidden(*args, **kwargs):
        pytest.fail("HOLD must never create a transport or worker")
    monkeypatch.setattr(cli, "GitHubTransport", forbidden)
    monkeypatch.setattr(cli, "LocalBatchWorker", forbidden)
    assert cli.main([command, "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "EF02_REQUEST_HELD"
    assert result["acceptance_scope"]["next_gate"] == "HOLD_NO_EXECUTOR"


def test_cli_keeps_issuer_reuse_and_exact_source_gates_ahead_of_scope(tmp_path, monkeypatch, capsys):
    from ai_commerce.remote import __main__ as cli
    _, calls = make_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([scoped_action("a", [VALIDATION_SOURCE], ["EF02.action-and-fanin"])])
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    argv = ["validate", "--request-file", str(request), "--checkout", str(tmp_path),
            "--issuer-login", "owner", "--owner-login", "owner"]
    attacker = list(argv)
    attacker[attacker.index("--issuer-login") + 1] = "attacker"
    assert cli.main(attacker) == 2
    assert capsys.readouterr().err.strip() == "EF02_REQUEST_REJECTED: ISSUER_NOT_AUTHORIZED_FOR_ACTOR"
    assert calls == []
    packet["actions"][0]["reuse_candidate"] = {"candidate_sha": "b" * 40, "verified_run_id": 123}
    request.write_text(json.dumps(packet), encoding="utf-8")
    assert cli.main(argv) == 2
    assert "REUSE_" in capsys.readouterr().err and calls == []
    del packet["actions"][0]["reuse_candidate"]
    packet["source_sha"] = "b" * 40
    request.write_text(json.dumps(packet), encoding="utf-8")
    def unavailable(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "PRIVATE_INSTRUCTIONS")
    monkeypatch.setattr(subprocess, "run", unavailable)
    assert cli.main(argv) == 2
    assert capsys.readouterr().err.strip() == "EF02_REQUEST_HELD: SOURCE_COMMIT_UNAVAILABLE"
    assert len(calls) == 1 and calls[0][-3:] == ["cat-file", "-t", "b" * 40]


def test_cli_valid_declaration_preserves_fingerprint_and_post_edit_requirement(tmp_path, monkeypatch, capsys):
    from ai_commerce.remote import __main__ as cli
    make_acceptance_repo(tmp_path, monkeypatch)
    item = scoped_action("a", [VALIDATION_SOURCE], ["EF02.action-and-fanin"])
    packet = batch([item])
    original_hash = fingerprint(packet)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    assert cli.main(["validate", "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "VALIDATED"
    assert result["acceptance_scope"]["status"] == "PRE_EXECUTOR_COMPATIBLE"
    assert result["acceptance_scope"]["next_gate"] == "POST_EDIT_MT01_REQUIRED"
    assert approved_acceptance_plan(item)["ids"] == ["EF02.action-and-fanin"]
    assert approved_acceptance_plan(item)["unknown_ids"] == []
    assert fingerprint(json.loads(request.read_text(encoding="utf-8"))) == original_hash


def test_definite_bad_action_rejects_even_when_an_independent_scope_is_undetermined(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([
        scoped_action("a", ["src/ai_commerce/new_component.py"], ["NEW.offline"]),
        malformed_scope_action("historical-duplicate"),
    ])
    with pytest.raises(RemoteRequestError, match="^AC_DECLARATION_UNPROVEN_PRE_EXECUTOR$"):
        validate_acceptance_scope(packet, repo=tmp_path)


WITNESS_MARKER = "MT01_IMPACT_WITNESSES_JSON:"
WAKE_AC = "EF02.wake-boundaries"
G2_AC = "WAKEHUB.G2.native-source-custody"
G3_AC = "WAKEHUB.G3.signed-desktop-nonce-source-generation"
CHROME_SOURCE = "src/ai_commerce/remote/chrome_acquisition.py"
G2_SOURCE = "src/ai_commerce/wakehub/native_runtime.py"
G3_SOURCE = "src/ai_commerce/wakehub/g3_enrollment.py"
# #938's failure shape: seven optional files, three disjoint required owners,
# then a successful executor that changes only four G2/G3 files.
WAKEHUB_SCOPE = [CHROME_SOURCE, "tests/test_ef02_d5_chrome_acquisition.py",
                 G2_SOURCE, "tests/test_wakehub_g2_native.py", G3_SOURCE,
                 "src/ai_commerce/wakehub/g3_desktop_observer.py",
                 "tests/test_wakehub_g3_native_enrollment.py"]
WAKEHUB_WITNESSES = {WAKE_AC: [CHROME_SOURCE], G2_AC: [G2_SOURCE], G3_AC: [G3_SOURCE]}


def with_impact_witnesses(item, witnesses):
    item["instruction"] += "\n" + WITNESS_MARKER + json.dumps(
        witnesses, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return item


def wakehub_scope_action(*, witnessed=False):
    item = scoped_action("a", list(WAKEHUB_SCOPE), [WAKE_AC, G2_AC, G3_AC])
    return with_impact_witnesses(item, WAKEHUB_WITNESSES) if witnessed else item


def make_current_acceptance_repo(tmp_path, monkeypatch):
    """Pinned, hash-verified transport with CURRENT source/import/test bytes.

    Uses the real owner loader and selector, including manifest dependencies;
    no Git repository is created and no tests or provider children are run.
    """
    contents = {path.relative_to(ROOT).as_posix(): path.read_bytes()
                for branch in (ROOT / "src/ai_commerce", ROOT / "tests")
                for path in branch.rglob("*.py")}
    return make_acceptance_repo(tmp_path, monkeypatch, contents=contents)


@pytest.mark.parametrize("command", ["validate", "run"])
@pytest.mark.parametrize("full", [False, True])
def test_938_optional_owner_union_requires_witness_before_any_executor(
        tmp_path, monkeypatch, capsys, command, full):
    from ai_commerce.remote import __main__ as cli
    from ai_commerce.remote.validation import _pinned_source_tree
    from ai_commerce.engineering.impact_v21 import load_baseline_component_owner_records, select_impact
    from ai_commerce.engineering.minimum_test import approved_groups

    make_acceptance_repo(tmp_path, monkeypatch)
    item = wakehub_scope_action()
    if full:
        item.update(verification_intent="HUMAN_FULL", verification_profile="project-tests")
    packet = batch([item])
    root = _pinned_source_tree(tmp_path, packet["source_sha"], git_executable=None)
    groups = approved_groups()
    groups = (*groups, *(r["group"] for r in load_baseline_component_owner_records(
        root=root, base_sha=packet["source_sha"], existing_groups=groups)))
    union = select_impact(root=root, changed_paths=item["allowed_paths"], groups=groups)
    # This is the old potential-coverage test, which is insufficient.
    assert union.runnable and {"ef02-wake", "wakehub-g2-native", "wakehub-g3-native-enrollment"} <= set(union.selected_groups)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("Ambiguous AC scope must stop before any model/transport/schedule")

    for name in ("GitHubTransport", "LocalBatchWorker", "run_schedule"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert cli.main([command, "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == "EF02_REQUEST_REJECTED: AC_IMPACT_WITNESS_REQUIRED_PRE_EXECUTOR"
    assert "PRIVATE_INSTRUCTIONS" not in captured.out + captured.err


def test_938_exact_witness_is_fingerprinted_contract_not_coverage(tmp_path, monkeypatch, capsys):
    from ai_commerce.remote import __main__ as cli
    from ai_commerce.remote.validation import validate_acceptance_scope

    make_current_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([wakehub_scope_action(witnessed=True)])
    original = copy.deepcopy(packet)
    with pytest.raises(RemoteRequestError, match="^ISSUER_NOT_AUTHORIZED_FOR_ACTOR$"):
        validate(packet, issuer="untrusted-executor", owner="owner")
    assert validate(packet, issuer="owner", owner="owner") == packet
    result = validate_acceptance_scope(packet, repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_COMPATIBLE"  # Existing host dispatch wire contract.
    assert result["coverage_status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
    assert result["actions"][0]["status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
    assert result["next_gate"] == "POST_EDIT_MT01_REQUIRED"
    assert result["actions"][0]["impact_contract"] == {
        "source_sha": packet["source_sha"], "request_fingerprint": fingerprint(packet),
        "witnesses": WAKEHUB_WITNESSES, "coverage_proven": False}
    assert packet == original
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    assert cli.main(["validate", "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 0
    assert json.loads(capsys.readouterr().out)["acceptance_scope"] == result
    # Neither an executor's invented declaration nor a different pin can reuse
    # the issuer's dispatch fingerprint. No output file is an authority source.
    for field in ("instruction", "allowed_paths", "source_sha"):
        forged = copy.deepcopy(packet)
        if field == "source_sha":
            forged[field] = "b" * 40
        elif field == "instruction":
            forged["actions"][0][field] = forged["actions"][0][field].replace(CHROME_SOURCE, G2_SOURCE)
        else:
            forged["actions"][0][field].remove(CHROME_SOURCE)
        assert fingerprint(forged) != fingerprint(packet)


@pytest.mark.parametrize("problem,code", [
    ("wrong-owner", "OWNER_MISMATCH"), ("disjoint-known-owner", "OWNER_MISMATCH"),
    ("outside-allowlist", "OUTSIDE_SCOPE"), ("missing-ac", "AC_MISMATCH"),
    ("extra-known-ac", "AC_MISMATCH"), ("unknown-ac", "UNKNOWN_AC"),
    ("malformed", "MALFORMED"), ("empty", "MALFORMED"), ("empty-paths", "MALFORMED"),
    ("non-string-path", "MALFORMED"), ("duplicate-key", "MALFORMED"),
    ("duplicate-path", "MALFORMED"), ("too-many-paths", "MALFORMED"),
    ("too-many-acs", "MALFORMED"), ("too-many-total-paths", "MALFORMED"),
    ("path-too-long", "MALFORMED"),
    ("oversized", "MALFORMED"), ("non-json-constant", "MALFORMED"),
    ("duplicate-marker", "DUPLICATE"), ("quoted-marker", "MALFORMED"),
    ("noncanonical", "NONCANONICAL"), ("unsorted-paths", "MALFORMED"),
    ("manifest", "PATH_UNESTABLISHED"), ("new-file", "PATH_UNESTABLISHED"),
    ("directory", "PATH_UNESTABLISHED"), ("unsafe-path", "PATH_INVALID"),
])
def test_impact_witness_rejects_untrusted_declarations_with_bounded_codes(
        tmp_path, monkeypatch, problem, code):
    from ai_commerce.remote.validation import validate_acceptance_scope

    make_acceptance_repo(tmp_path, monkeypatch)
    item = wakehub_scope_action()
    witnesses = copy.deepcopy(WAKEHUB_WITNESSES)
    raw, prefix = None, WITNESS_MARKER
    if problem == "wrong-owner":
        witnesses[WAKE_AC] = [G3_SOURCE]
    elif problem == "disjoint-known-owner":
        item["allowed_paths"].append(PERSONAL_SOURCE)
        witnesses[G2_AC] = [PERSONAL_SOURCE]
    elif problem == "outside-allowlist":
        witnesses[WAKE_AC] = [WAKE_SOURCE]
    elif problem == "missing-ac":
        del witnesses[WAKE_AC]
    elif problem == "extra-known-ac":
        witnesses[PERSONAL_AC] = [PERSONAL_SOURCE]
    elif problem == "unknown-ac":
        witnesses["PRIVATE_UNKNOWN_AC"] = [CHROME_SOURCE]
    elif problem == "malformed":
        raw = '{"PRIVATE_SECRET":bad JSON}'
    elif problem == "empty":
        raw = "{}"
    elif problem == "empty-paths":
        witnesses[WAKE_AC] = []
    elif problem == "non-string-path":
        witnesses[WAKE_AC] = [123]
    elif problem == "duplicate-key":
        raw = '{"EF02.wake-boundaries":[],"EF02.wake-boundaries":[]}'
    elif problem == "duplicate-path":
        witnesses[WAKE_AC] *= 2
    elif problem == "too-many-paths":
        witnesses[WAKE_AC] = [f"src/PRIVATE_{i:02d}.py" for i in range(33)]
    elif problem == "too-many-acs":
        witnesses = {f"PRIVATE_AC_{i}": [CHROME_SOURCE] for i in range(129)}
    elif problem == "too-many-total-paths":
        witnesses = {f"PRIVATE_AC_{i}": [f"src/PRIVATE_{n:02d}.py" for n in range(32)] for i in range(9)}
    elif problem == "path-too-long":
        witnesses[WAKE_AC] = ["s" * 513]
    elif problem == "oversized":
        raw = " " * 32769
    elif problem == "non-json-constant":
        raw = '{"EF02.wake-boundaries":NaN}'
    elif problem == "duplicate-marker":
        with_impact_witnesses(item, witnesses)
    elif problem == "quoted-marker":
        prefix = "Historical declaration: " + prefix
    elif problem == "noncanonical":
        raw = json.dumps(witnesses, sort_keys=True)
    elif problem == "unsorted-paths":
        witnesses[WAKE_AC] = sorted([CHROME_SOURCE, WAKE_SOURCE], reverse=True)
    elif problem in ("manifest", "new-file", "directory", "unsafe-path"):
        path = {"manifest": "work_items/mt01-components/wakehub-g2-native.json",
                "new-file": "src/ai_commerce/wakehub/PRIVATE_new.py",
                "directory": "src/ai_commerce/wakehub",
                "unsafe-path": "../PRIVATE_SECRET"}[problem]
        if problem != "unsafe-path":
            item["allowed_paths"].append(path)
        witnesses[WAKE_AC] = [path]
    if raw is None:
        raw = json.dumps(witnesses, sort_keys=True, separators=(",", ":"))
    item["instruction"] += "\n" + prefix + raw
    original = copy.deepcopy(item)
    with pytest.raises(RemoteRequestError) as rejected:
        validate_acceptance_scope(batch([item]), repo=tmp_path)
    assert str(rejected.value) == f"AC_IMPACT_WITNESS_{code}_PRE_EXECUTOR"
    assert "PRIVATE" not in str(rejected.value) and item == original


def test_impact_witness_cannot_borrow_a_hidden_dependency_owner(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_acceptance_repo(tmp_path, monkeypatch)
    predecessor = scoped_action("a", [CHROME_SOURCE], [WAKE_AC])
    child = with_impact_witnesses(scoped_action("b", [G2_SOURCE], [WAKE_AC, G2_AC], depends=("a",)),
                                  {WAKE_AC: [CHROME_SOURCE], G2_AC: [G2_SOURCE]})
    with pytest.raises(RemoteRequestError, match="^AC_IMPACT_WITNESS_OUTSIDE_SCOPE_PRE_EXECUTOR$"):
        validate_acceptance_scope(batch([child, predecessor]), repo=tmp_path)


def test_impact_witness_uses_pinned_import_closure_not_forged_checkout(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    product = "src/ai_commerce/product/identity.py"
    attribution = "src/ai_commerce/affiliate_attribution/evidence.py"
    ac = "A14.L04.offline-attribution-evidence"
    make_acceptance_repo(tmp_path, monkeypatch, contents={
        attribution: b"from ai_commerce.product import identity\n"})
    item = with_impact_witnesses(scoped_action("a", [product], [ac]), {ac: [product]})
    result = validate_acceptance_scope(batch([item]), repo=tmp_path)
    assert result["actions"][0]["status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
    # A known but unrelated owner cannot masquerade as this dependency.
    target = tmp_path / attribution
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("from ai_commerce.hostops_v3 import personal\n", encoding="utf-8")
    item = with_impact_witnesses(scoped_action("a", [PERSONAL_SOURCE, attribution], [ac]), {ac: [PERSONAL_SOURCE]})
    with pytest.raises(RemoteRequestError, match="^AC_IMPACT_WITNESS_OWNER_MISMATCH_PRE_EXECUTOR$"):
        validate_acceptance_scope(batch([item]), repo=tmp_path)


def test_impact_witness_cannot_survive_changed_pinned_bytes(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([wakehub_scope_action(witnessed=True)])
    assert validate_acceptance_scope(packet, repo=tmp_path)["coverage_status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
    original_run = subprocess.run

    def changed_blob(argv, **kwargs):
        result = original_run(argv, **kwargs)
        if argv[-2:] == ["cat-file", "--batch"]:
            header, contents = result.stdout.split(b"\n", 1)
            # Same repo, pin and blob IDs cannot authenticate different bytes.
            result.stdout = header + b"\n" + bytes([contents[0] ^ 1]) + contents[1:]
        return result

    monkeypatch.setattr(subprocess, "run", changed_blob)
    held = validate_acceptance_scope(packet, repo=tmp_path)
    assert held["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert held["actions"][0]["reason"] == "PINNED_BASELINE_UNPROVEN"
    assert held["next_gate"] == "HOLD_NO_EXECUTOR"


def test_ordinary_two_file_single_owner_needs_no_impact_marker(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_acceptance_repo(tmp_path, monkeypatch)
    item = scoped_action("a", [VALIDATION_SOURCE, "tests/test_ef02.py"], ["EF02.action-and-fanin"])
    result = validate_acceptance_scope(batch([item]), repo=tmp_path)
    assert result["status"] == result["actions"][0]["status"] == "PRE_EXECUTOR_COMPATIBLE"
    assert "impact_contract" not in result["actions"][0]
    # Multiple ACs on the same owner are equally unambiguous.
    item = scoped_action("a", [G2_SOURCE, "tests/test_wakehub_g2_native.py"],
                         [G2_AC, "WAKEHUB.G2.native-durable-first-wins"])
    assert validate_acceptance_scope(batch([item]), repo=tmp_path)["coverage_status"] == "PRE_EXECUTOR_COMPATIBLE"


def test_single_ac_optional_scope_still_needs_witness_and_cross_owner_action_is_possible(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_acceptance_repo(tmp_path, monkeypatch)
    item = scoped_action("a", [CHROME_SOURCE, G2_SOURCE], [WAKE_AC])
    with pytest.raises(RemoteRequestError, match="^AC_IMPACT_WITNESS_REQUIRED_PRE_EXECUTOR$"):
        validate_acceptance_scope(batch([item]), repo=tmp_path)
    item = with_impact_witnesses(scoped_action("a", [CHROME_SOURCE, G2_SOURCE], [WAKE_AC, G2_AC]),
                                 {WAKE_AC: [CHROME_SOURCE], G2_AC: [G2_SOURCE]})
    result = validate_acceptance_scope(batch([item]), repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_COMPATIBLE"
    assert result["coverage_status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"


def test_938_post_change_missing_wake_owner_remains_mt01_coverage_unproven(tmp_path, monkeypatch):
    from ai_commerce.engineering import verification
    from ai_commerce.engineering.minimum_test import approved_groups
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    from ai_commerce.remote.validation import validate_acceptance_scope

    files, _ = make_current_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([wakehub_scope_action(witnessed=True)])
    assert validate_acceptance_scope(packet, repo=tmp_path)["coverage_status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
    for path, raw in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    base = {p: hashlib.sha256(raw).hexdigest() for p, raw in files.items()}
    changed = [G2_SOURCE, "tests/test_wakehub_g2_native.py", G3_SOURCE,
               "tests/test_wakehub_g3_native_enrollment.py"]
    candidate = dict(base)
    for path in changed:
        raw = files[path] + b"\nSCOPED_FIXTURE_VALUE = 1\n"
        (tmp_path / path).write_bytes(raw)
        candidate[path] = hashlib.sha256(raw).hexdigest()
    registry = verify_registry(REGISTRY)
    argv = registry["ef02-tests"]
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    # The simulated diff has real fixture bytes. The owner loader, selector,
    # AC check, registered-binding check and no-execution refusal path are real.

    def forbidden(*args, **kwargs):
        pytest.fail("Missing mandatory owner must never launch pytest")

    monkeypatch.setattr(verification, "_run_pytest_with_junit", forbidden)
    saved = {}
    response, plan = verification.execute_plan(
        repo=tmp_path, output_dir=tmp_path, base_sha=packet["source_sha"], base_files=base,
        changed_paths=changed, required_acs=tuple(approved_acceptance_plan(packet["actions"][0])["ids"]),
        fallback_argv=argv, prior={}, save=lambda key, name, raw: saved.update({key: raw}),
        evidence_files={}, deadline=time.monotonic() + 60, timeout_seconds=60,
        registered_only=True, groups=approved_groups(), registered_profiles=registry,
        allowed_paths=tuple(WAKEHUB_SCOPE))
    # The trusted runner maps this exact verifier code to MT01_COVERAGE_UNPROVEN.
    assert response.returncode == 5 and not response.stdout
    assert "ef02-wake" not in plan["selected_groups"]
    assert {"wakehub-g2-native", "wakehub-g3-native-enrollment"} <= set(plan["selected_groups"])
    assert "AC_REQUIRED_GROUP_NOT_SELECTED:" + WAKE_AC in plan["reason_codes"]
    assert all(g["state"] == "UNPROVEN" and not g["execute"] for g in plan["groups"])
    assert all(g["actual_argv"] is None and not any(g["tests_executed"].values()) for g in plan["groups"])
    assert plan["narrow_execution_observed"] is False and "mt01_results" in saved

    # Adding the required owner to a simulated ACTUAL diff restores exact
    # owner/consumer selection, only making real verification eligible to run.
    changed.append(CHROME_SOURCE)
    raw = files[CHROME_SOURCE] + b"\nSCOPED_FIXTURE_VALUE = 1\n"
    (tmp_path / CHROME_SOURCE).write_bytes(raw)
    candidate[CHROME_SOURCE] = hashlib.sha256(raw).hexdigest()
    complete = registered_action_plan(
        repo=tmp_path, base_sha=packet["source_sha"], base_files=base, candidate_files=candidate,
        changed_paths=changed, required_acs=[WAKE_AC, G2_AC, G3_AC], fallback_argv=argv,
        groups=approved_groups(), registered_profiles=registry, allowed_paths=WAKEHUB_SCOPE)
    assert not complete["impact_v21"]["missing_mapping"]
    assert {"ef02-wake", "wakehub-g2-native", "wakehub-g3-native-enrollment"} <= set(complete["selected_groups"])
    assert all(g["state"] == "EXECUTE" and "result" not in g for g in complete["groups"])
    assert verification.registered_binding_problems(complete, registry) == []
    for entry in complete["groups"]:
        mapping, binding = complete["consumer_map"][entry["group"]], entry["binding"]
        assert set((*mapping["paths"], *mapping["tests"], *mapping["consumers"], *mapping["contracts"])) <= binding["candidate_files"].keys()
        assert all(binding["candidate_files"][p] == candidate[p] for p in binding["candidate_files"])
