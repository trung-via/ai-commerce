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


def test_r4_remote_bridge_uses_configured_source_and_never_schedules(tmp_path, monkeypatch):
    from test_ef02_m0r_fault_injection import _r4_bridge_fixture
    from ai_commerce.remote.worker import LocalBatchWorker
    f = _r4_bridge_fixture(tmp_path, monkeypatch)
    worker = object.__new__(LocalBatchWorker)
    worker.github, worker.batch, worker.issue_number, worker.issuer_login = f.args[0], f.args[1], 643, "trung-via"
    def forbidden(*a, **kw): pytest.fail("source bridge scheduled native execution")
    for method in ("execute_action", "_acquire_owner", "reconcile_action"):
        monkeypatch.setattr(worker, method, forbidden)
    with f.ledger:
        report = worker.witness_bridge(f.args[2], f.request(), enrollment=f.enrollment,
            context=f.context, ssh_keygen=f.verifier, ledger=f.ledger, source_contract_fixture=True)
        assert report["status"] == "SOURCE_ONLY_HOLD_NATIVE_SIGNER_REQUIRED", report
        assert report["execution_allowed"] is report["replay_authorized"] is report["publication_authorized"] is False


def test_r4_requested_acceptance_ids_are_registered_with_owner_and_consumers():
    from ai_commerce.engineering.minimum_test import approved_groups
    requested = {"EF01.execution", "EF01.repair", "EF01.source-integrity", "EF01.provider-boundary",
        "EF02.action-and-fanin", "EF02.scope-review", "MT01.selection-and-evidence",
        "EF01P.git-and-host-budget", "EF02.recovery"}
    groups = approved_groups()
    assert requested <= {ac for group in groups for ac in group.acs}
    assert {g.name for g in groups if requested & set(g.acs)} == {
        "ef01", "repair", "integrity", "provider", "ef02", "ef02-scope", "mt01", "ef01p", "ef02-recovery"}
    for path in ("src/ai_commerce/remote/transport.py", "src/ai_commerce/remote/worker.py",
                 "src/ai_commerce/hostops_v3/journal.py", "src/ai_commerce/hostops_v3/security.py",
                 "src/ai_commerce/hostops_v3/broker.py", "src/ai_commerce/hostops_v3/worker.py"):
        assert any(path in g.paths for g in groups)


@pytest.mark.parametrize("field", ["action_id", "attempt_id"])
def test_workowner_scheduler_rejects_duplicate_identity_before_dispatch(field):
    from ai_commerce.remote.scheduler import run_schedule
    first, second = action("a"), action("b")
    second[field] = first[field]
    calls = []
    with pytest.raises(ValueError, match="DUPLICATE_DISPATCH_IDENTITY"):
        run_schedule({"batch_id": "duplicate", "source_sha": "a" * 40,
                      "actions": [first, second]}, lambda a: calls.append(a))
    assert calls == []


def test_workowner_scheduler_mismatched_worker_identity_blocks_dependent():
    from ai_commerce.remote.scheduler import run_schedule
    first, second = action("a"), action("b", ["a"])
    calls = []
    def work(a):
        calls.append(a["action_id"])
        return {"status": "CANDIDATE_READY", "action_id": "foreign"}
    result = run_schedule({"batch_id": "mismatch", "source_sha": "a" * 40,
                           "actions": [first, second]}, work)
    assert result["succeeded"] == 0 and calls == ["a"]
    assert result["actions"][1]["status"] == "BLOCKED_DEPENDENCY"

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



def test_preexecutor_acceptance_ignores_prose_mentions_and_rejects_second_declaration():
    from ai_commerce.remote.validation import require_preexecutor_acceptance
    a = action("a")
    a["instruction"] = (
        'Prose mentions MT01_ACCEPTANCE_JSON: but is not a declaration.\n'
        'MT01_ACCEPTANCE_JSON:["EF02.action-and-fanin"]'
    )
    assert require_preexecutor_acceptance(a)["ids"] == ["EF02.action-and-fanin"]
    assert validate(batch([a]))["batch_id"] == "sample-001"
    a["instruction"] += '\nMT01_ACCEPTANCE_JSON:["EF02.recovery"]'
    with pytest.raises(RemoteRequestError, match="INTAKE_AC_MISSING_OR_AMBIGUOUS"):
        validate(batch([a]))


@pytest.mark.parametrize("text,expected", [
    ("Only source instructions; no machine AC", "INTAKE_AC_MISSING_OR_AMBIGUOUS"),
    ("MT01_ACCEPTANCE_JSON: not-json", "INTAKE_AC_MALFORMED"),
    ("MT01_ACCEPTANCE_JSON: []", "INTAKE_AC_MALFORMED"),
    ('MT01_ACCEPTANCE_JSON:["EF02.recovery","EF02.recovery"]', "INTAKE_AC_MALFORMED"),
    ('MT01_ACCEPTANCE_JSON:["MT01.minimum-test-execution"]', "INTAKE_AC_UNREGISTERED"),
])
def test_preexecutor_acceptance_rejects_malformed_or_foreign_ids_before_provider(text, expected):
    from ai_commerce.remote.validation import require_preexecutor_acceptance
    a = action("a")
    a["instruction"] = text
    with pytest.raises(RemoteRequestError, match=expected):
        require_preexecutor_acceptance(a)
    if text.lstrip().startswith("MT01_ACCEPTANCE_JSON:"):
        with pytest.raises(RemoteRequestError, match=expected):
            validate(batch([a]))



def test_preexecutor_source_closure_adds_registered_downstream_recovery():
    from ai_commerce.remote.validation import require_preexecutor_acceptance
    a = action("a")
    a["allowed_paths"] = ["tests/test_ef02.py", "tests/test_ef02_m0r_fault_injection.py"]
    a["instruction"] = 'Fix only registered source.\nMT01_ACCEPTANCE_JSON:["EF02.action-and-fanin"]'
    original = fingerprint(a)
    planned = require_preexecutor_acceptance(a, source_closure=True)
    assert set(("EF02.action-and-fanin", "EF02.recovery")) <= set(planned["ids"])
    assert "EF02.recovery" in planned["owner_derived_ids"]
    assert "tests/test_ef02_m0r_fault_injection.py" in planned["consumer_map"]["EF02.recovery"]["tests"]
    assert fingerprint(a) == original  # issuer payload is never rewritten


@pytest.mark.parametrize("path", [
    "tests/test_ef02_m0r_fixture.py",
    "src/ai_commerce/remote/unregistered_feature.py",
])
def test_preexecutor_source_closure_rejects_unmapped_before_provider(path):
    from ai_commerce.remote.validation import require_preexecutor_acceptance
    a = action("a")
    a["allowed_paths"] = [path]
    a["instruction"] = 'MT01_ACCEPTANCE_JSON:["EF02.recovery"]'
    with pytest.raises(RemoteRequestError, match="INTAKE_SOURCE_OWNER_UNMAPPED"):
        require_preexecutor_acceptance(a, source_closure=True)


def test_preexecutor_source_closure_does_not_accept_unrelated_registered_ac():
    from ai_commerce.remote.validation import require_preexecutor_acceptance
    a = action("a")
    a["allowed_paths"] = ["tests/test_ef02_m0r_fault_injection.py"]
    a["instruction"] = 'MT01_ACCEPTANCE_JSON:["A12.product-evidence"]'
    with pytest.raises(RemoteRequestError, match="INTAKE_AC_NOT_OWNING_ALLOWED_PATH"):
        require_preexecutor_acceptance(a, source_closure=True)


def test_direct_real_worker_ac_gate_precedes_workowner_operations(tmp_path, monkeypatch):
    from ai_commerce.remote import worker as remote_worker
    monkeypatch.setattr(remote_worker, "check_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(remote_worker, "load_approved_candidates",
                        lambda *_a: {"version": 1, "candidates": {}})
    class ForbiddenClaim:
        def claim(self, **_kw):
            pytest.fail("invalid AC must not reach GitHub claim")
    worker = remote_worker.LocalBatchWorker(
        checkout=tmp_path, batch=batch([action("a")]),
        profiles={"project-tests": ["python", "-m", "pytest", "tests", "-q"]},
        github=ForbiddenClaim(), root=tmp_path / "evidence")
    assert worker._dispatch_strict_ac is True
    out = worker.execute_action(worker.batch["actions"][0])
    assert out["status"] == "INVALID_ACTION"
    assert out["error_code"] == "INTAKE_AC_MISSING_OR_AMBIGUOUS"
    assert out["executor_invoked"] is False
    assert out["candidate_sha"] is None



def test_reconcile_readonly_accepts_legacy_malformed_ac_without_dispatch():
    # Read-only reconciliation must still inspect old immutable bad WorkItems;
    # executable modes continue to reject the same source-bound request.
    a = action("a")
    a["instruction"] = 'MT01_ACCEPTANCE_JSON: not-json'
    p = batch([a])
    with pytest.raises(RemoteRequestError, match="INTAKE_AC_MALFORMED"):
        validate(p)
    assert validate_batch(p, schema=SCHEMA, registry=verify_registry(REGISTRY),
                          issuer_login="trung-via", owner_login="trung-via",
                          strict_ac=False) == p


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

    store("tracked_diff", "tracked.patch", b"diff --git a/impl.py b/impl.py\\n")
    store("source_after_verify", "source.after.json", b'{"clean": true}')
    store("verify_stdout", "verify.stdout.log", b"1 passed\\n")
    store("provider_return_observed", "provider-return.json", b'{"observed": true}')
    passed = b"1 passed in 0.01s\\nMT01_XDIST_WORKERS_COLLECTED=12\\n"
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


def _build_authentic_16_group_report(tmp_path, *, group_12_profile="mt01-mt01-v21-tests"):
    from ai_commerce.engineering.minimum_test import canonical, digest, file_hashes
    workspace = tmp_path / "workspace"
    evidence_dir = tmp_path / "evidence"
    workspace.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (workspace / "impl.py").write_text("content", encoding="utf-8")
    current = file_hashes(workspace)
    source_sha = "a" * 40

    profiles = {
        "mt01-mt01-v21-tests": ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"],
        "mt01-component-onboarding": ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"],
    }
    for i in range(16):
        if i != 12:
            profiles[f"mt01-group-{i}-tests"] = ["python", "-m", "pytest", f"tests/test_{i}.py", "-q", "-n", "12"]

    diff_data = b"diff --git a/impl.py b/impl.py\n"
    (evidence_dir / "git.tracked.diff.patch").write_bytes(diff_data)
    (evidence_dir / "source.after_verify.json").write_bytes(b'{"changed_files": ["impl.py"]}')
    (evidence_dir / "verify.stdout.log").write_bytes(b"16 passed\n")
    (evidence_dir / "provider_return_observed.json").write_bytes(b'{"status": "SUCCESS"}')

    refs = {
        "tracked_diff": "git.tracked.diff.patch",
        "source_after_verify": "source.after_verify.json",
        "verify_stdout": "verify.stdout.log",
        "provider_return_observed": "provider_return_observed.json",
    }
    sha_map = {k: hashlib.sha256((evidence_dir / v).read_bytes()).hexdigest() for k, v in refs.items()}

    groups = []
    for i in range(16):
        gname = "mt01-v21" if i == 12 else f"group-{i}"
        pname = group_12_profile if i == 12 else f"mt01-group-{i}-tests"
        argv = ["python", "-m", "pytest", "tests/test_mt01_v21.py" if i == 12 else f"tests/test_{i}.py", "-q", "-n", "12"]
        binding = {
            "base_sha": source_sha,
            "aggregate_sources": [],
            "workspace": str(workspace.resolve()),
            "candidate_files": {"impl.py": current.get("impl.py")},
            "argv": argv,
            "registered_profile": pname,
            "environment_proven": True,
        }
        stdout_data = b"12 passed in 1.0s\nMT01_XDIST_WORKERS_COLLECTED=12\n"
        (evidence_dir / f"mt01_{i}_stdout.log").write_bytes(stdout_data)
        refs[f"mt01_{i}_stdout"] = f"mt01_{i}_stdout.log"
        sha_map[f"mt01_{i}_stdout"] = hashlib.sha256(stdout_data).hexdigest()

        art_map = {"stdout": {"path": f"mt01_{i}_stdout.log", "sha256": hashlib.sha256(stdout_data).hexdigest()}}
        payload = canonical({"binding": binding, "exit_code": 0, "nonempty": True, "artifacts": art_map})
        p_sha = digest(payload.encode())
        (evidence_dir / f"mt01_{i}_evidence.json").write_text(payload, encoding="utf-8")
        refs[f"mt01_{i}_evidence"] = f"mt01_{i}_evidence.json"
        sha_map[f"mt01_{i}_evidence"] = hashlib.sha256(payload.encode()).hexdigest()

        groups.append({"group": gname, "binding": binding, "evidence_sha256": p_sha})

    report = {
        "verify_completed": True,
        "provider_return_determinate": True,
        "evidence_files": refs,
        "evidence_sha256": sha_map,
        "tracked_diff_sha256": sha_map["tracked_diff"],
        "minimum_test_enabled": True,
        "minimum_test": {"groups": groups},
        "changed_files": ["impl.py"],
    }
    return report, evidence_dir, workspace, source_sha, profiles


def test_verified_ef01_proof_accepts_authentic_16_group_report(tmp_path):
    from ai_commerce.remote.worker import _verified_ef01_proof
    report, ev_dir, ws, source_sha, profiles = _build_authentic_16_group_report(tmp_path)
    diagnostics = []
    assert _verified_ef01_proof(
        report, root=ev_dir, workspace=ws, source_sha=source_sha,
        sources=[], profiles=profiles, allowed_paths=["impl.py"], diagnostics=diagnostics
    ) is True
    assert diagnostics == []


def test_verified_ef01_proof_rejects_group_12_collision_without_manifest(tmp_path):
    from ai_commerce.remote.worker import _verified_ef01_proof
    # Collision: group 12 with registered_profile="mt01-component-onboarding" without valid onboarding manifest
    report, ev_dir, ws, source_sha, profiles = _build_authentic_16_group_report(
        tmp_path, group_12_profile="mt01-component-onboarding")
    diagnostics = []
    assert _verified_ef01_proof(
        report, root=ev_dir, workspace=ws, source_sha=source_sha,
        sources=[], profiles=profiles, allowed_paths=["impl.py"], diagnostics=diagnostics
    ) is False
    assert diagnostics == ["MT01_GROUP_12_REGISTERED_BINDING_REJECTED"]


@pytest.mark.parametrize("mutation,expected_diag", [
    ("fake_profile", "MT01_GROUP_0_REGISTERED_BINDING_REJECTED"),
    ("tampered_argv", "MT01_GROUP_0_REGISTERED_BINDING_REJECTED"),
    ("tampered_stdout", "RECEIPT_ARTIFACT_HASH_MISMATCH"),
    ("tampered_diff", "RECEIPT_TRACKED_DIFF_HASH_MISMATCH"),
    ("source_sha_mismatch", "MT01_GROUP_0_SOURCE_OR_HASH_MISMATCH"),
    ("uncovered_changed_file", "MT01_CHANGED_FILES_UNCOVERED"),
    ("missing_provenance", "RECEIPT_PROVENANCE_INCOMPLETE"),
])
def test_verified_ef01_proof_mutation_negatives(tmp_path, mutation, expected_diag):
    from ai_commerce.remote.worker import _verified_ef01_proof
    report, ev_dir, ws, source_sha, profiles = _build_authentic_16_group_report(tmp_path)

    if mutation == "fake_profile":
        report["minimum_test"]["groups"][0]["binding"]["registered_profile"] = "mt01-fake-injection"
    elif mutation == "tampered_argv":
        report["minimum_test"]["groups"][0]["binding"]["argv"] = ["python", "-m", "pytest", "tests/injected.py"]
    elif mutation == "tampered_stdout":
        (ev_dir / "verify.stdout.log").write_bytes(b"tampered stdout")
    elif mutation == "tampered_diff":
        report["tracked_diff_sha256"] = "0" * 64
    elif mutation == "source_sha_mismatch":
        report["minimum_test"]["groups"][0]["binding"]["base_sha"] = "0" * 40
    elif mutation == "uncovered_changed_file":
        report["changed_files"].append("uncovered.py")
    elif mutation == "missing_provenance":
        report["verify_completed"] = False

    diagnostics = []
    result = _verified_ef01_proof(
        report, root=ev_dir, workspace=ws, source_sha=source_sha,
        sources=[], profiles=profiles, allowed_paths=["impl.py"], diagnostics=diagnostics
    )
    assert result is False
    assert expected_diag in diagnostics


@pytest.mark.parametrize("kind", ["http", "timeout"])
def test_publication_diagnostic_survives_worker_batch_artifact_and_safe_issue_summary(
        tmp_path, monkeypatch, capsys, kind):
    """Exercise the real result consumers; all provider/GitHub effects are fakes."""
    from types import SimpleNamespace
    from ai_commerce.remote import __main__ as cli
    from ai_commerce.remote import transport
    from test_ef01_repair import _action, _worker_fixture
    from test_ef02_candidate_integrity import _publication_fixture, _PRIVATE

    wire = _publication_fixture(tmp_path, monkeypatch, fault=("POST", "/git/refs"),
        body=json.dumps({"message": _PRIVATE, "errors": [{"code": "invalid", "field": "ref"}]}).encode(),
        kind=kind)
    with pytest.raises(RemoteTransportError) as failure:
        wire.publish()
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    attempts = []
    def reject_publication(**kwargs):
        attempts.append(kwargs["attempt_id"])
        raise failure.value
    monkeypatch.setattr(context.worker.github, "candidate", reject_publication)
    request = _action("a")
    report = run_schedule({**context.batch, "actions": [request]}, context.worker.execute_action)
    result = report["actions"][0]
    assert result["status"] == "RESULT_PUBLICATION_FAILED" and report["succeeded"] == 0
    assert result["error_code"] == "RemoteTransportError" and result["candidate_sha"] is result["candidate_ref"] is None
    safe = result["diagnostic"]
    assert safe == str(failure.value) and "CANDIDATE_REF_CREATE" in safe
    assert ("UNKNOWN_EFFECT_RECONCILE" if kind == "timeout" else
            "TERMINAL_VALIDATION_OR_POLICY_REJECTION") in safe
    saved = json.loads(next(context.worker.root.rglob("action_result.json")).read_text())
    assert saved["diagnostic"] == safe and _PRIVATE not in json.dumps(saved)

    comments = []
    monkeypatch.setattr(cli, "GitHubTransport", lambda *_: SimpleNamespace(comment=lambda n, body: comments.append((n, body))))
    monkeypatch.setattr(cli, "LocalBatchWorker", lambda **_: context.worker)
    monkeypatch.setattr(cli, "run_schedule", lambda *_a, **_kw: report)
    monkeypatch.setattr(cli, "check_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "verify_registry", lambda _: context.worker.profiles)
    monkeypatch.setattr(cli, "load_approved_candidates", lambda _: {"version": 1, "candidates": {}})
    # Isolate post-admission publication uncertainty; issuer AC negatives
    # are separately covered by the real strict pre-executor acceptance gate.
    monkeypatch.setattr(cli, "require_preexecutor_acceptance",
                        lambda _action, **_kw: {"ids": ["EF02.action-and-fanin"]})
    monkeypatch.setattr(cli, "start_worker_signal", lambda **_: None)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    packet = {"kind": "AI_COMMERCE_REMOTE_ACTION_BATCH", "version": 1, "batch_id": context.batch["batch_id"],
              "source_sha": context.head, "actor": "HUMAN", "authorization_ref": "fixture-grant", "actions": [request]}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(packet))
    assert cli.main(["run", "--request-file", str(path), "--checkout", str(ROOT),
                     "--issuer-login", "owner", "--owner-login", "owner", "--issue-number", "512"]) == 1
    persisted = json.loads((context.worker.root / "batch_result.json").read_text())
    assert persisted["actions"][0]["diagnostic"] == safe
    assert len(comments) == 1 and "RESULT_PUBLICATION_FAILED" in comments[0][1]
    # The existing public summary points to the per-attempt artifact. Inline
    # stage diagnostics would require the __main__.py owner, outside this patch.
    assert "Full per-attempt diagnostics are in this workflow's artifact." in comments[0][1]
    assert _PRIVATE not in comments[0][1] + json.dumps(persisted) + capsys.readouterr().out
    assert attempts == [request["attempt_id"]] and len(context.calls) == 2
    before = len(context.calls)
    assert context.worker.execute_action(request)["status"] == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert len(context.calls) == before and len(attempts) == 1


def test_real_http_claim_collision_keeps_legacy_error_and_duplicate_semantics(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from ai_commerce.remote import transport
    calls = []
    fingerprint_value = "f" * 64
    message = "EF02 CLAIM\n\nfingerprint=" + fingerprint_value + "\nattempt_id=claim-001"
    def open_request(request, timeout):
        method, url = request.get_method(), request.full_url
        calls.append((method, url))
        if method == "POST" and url.endswith("/git/refs"):
            raise HTTPError(url, 422, "private reason", {}, BytesIO(b'{"message":"private body"}'))
        if method == "POST":
            response = {"sha": "b" * 40}
        elif "/git/ref/" in url:
            response = {"object": {"sha": "b" * 40}}
        else:
            response = {"tree": {"sha": "c" * 40}, "message": message}
        return BytesIO(json.dumps(response).encode())
    monkeypatch.setattr(transport, "urlopen", open_request)
    github = GitHubTransport("owner/repo", "private token")
    assert github.claim(source_sha="a" * 40, attempt_id="claim-001", fingerprint=fingerprint_value) == "DUPLICATE_CLAIM_NO_REEXECUTION"
    assert [method for method, _ in calls] == ["GET", "POST", "POST", "GET", "GET"]


def test_http_422_body_read_timeout_retains_status_without_plaintext_or_replay(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from ai_commerce.remote import transport
    bodies = []
    class Unreadable(BytesIO):
        def read(self, size=-1):
            assert size == 8193
            raise TimeoutError("private token in response reader")
    def open_request(request, timeout):
        body = Unreadable()
        bodies.append(body)
        raise HTTPError(request.full_url, 422, "private reason", {}, body)
    monkeypatch.setattr(transport, "urlopen", open_request)
    with pytest.raises(RemoteTransportError) as failure:
        GitHubTransport("owner/repo", "private token")._publication_api(transport.PublicationStage.BLOB_CREATE, {})
    payload = failure.value.publication.payload()
    assert payload["http_status"] == 422 and payload["github_codes"] == payload["validation_fields"] == []
    assert payload["disposition"] == "TERMINAL_VALIDATION_OR_POLICY_REJECTION"
    assert len(bodies) == 1 and bodies[0].closed and "private" not in str(failure.value)


def test_candidate_ref_get_verification_failure_survives_worker_batch_artifact(tmp_path, monkeypatch, capsys):
    """Exercise worker batch reporting when POST /git/refs succeeds but GET ref fails."""
    from types import SimpleNamespace
    from io import BytesIO
    from urllib.error import HTTPError
    from ai_commerce.remote import __main__ as cli
    from ai_commerce.remote import transport
    from test_ef01_repair import _action, _worker_fixture
    from test_ef02_candidate_integrity import _publication_fixture, _PRIVATE

    fixture = _publication_fixture(tmp_path, monkeypatch)
    orig_open = transport.urlopen
    def open_request(request, timeout):
        if request.get_method() == "GET" and "/git/ref/" in request.full_url:
            raise HTTPError(request.full_url, 404, "Not Found", {}, BytesIO(b'{"message":"' + _PRIVATE.encode() + b'"}'))
        return orig_open(request, timeout)
    monkeypatch.setattr(transport, "urlopen", open_request)

    with pytest.raises(RemoteTransportError) as failure:
        fixture.publish()

    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    monkeypatch.setattr(context.worker.github, "candidate", lambda **_: (_ for _ in ()).throw(failure.value))
    request = _action("a")
    report = run_schedule({**context.batch, "actions": [request]}, context.worker.execute_action)
    result = report["actions"][0]
    assert result["status"] == "RESULT_PUBLICATION_FAILED" and report["succeeded"] == 0
    assert result["error_code"] == "RemoteTransportError" and result["candidate_sha"] is result["candidate_ref"] is None
    safe = result["diagnostic"]
    assert "CANDIDATE_REF_CREATE" in safe and "UNKNOWN_EFFECT_RECONCILE" in safe
    assert _PRIVATE not in safe


def test_workowner_candidate_publication_enforces_owner_fence_and_ref_readback(tmp_path, monkeypatch):
    """Integration consumer check: WorkOwner fence + main's _publication_api ref readback."""
    from ai_commerce.remote import transport
    from ai_commerce.remote.transport import CandidateIntegrityError, RemoteTransportError
    from test_ef02_m0r_fault_injection import _publishable_workowner
    storage, owner, arguments = _publishable_workowner(tmp_path, monkeypatch)

    # Wrong owner transport rejected before any publication API call
    other_storage = transport.GitHubTransport("other/repo", "token")
    bad_owner = transport.WorkOwner(other_storage, owner.identity, "f" * 64)
    with pytest.raises(CandidateIntegrityError, match="WORK_OWNER_PUBLICATION_FENCE_REQUIRED"):
        storage.candidate(**{**arguments, "owner": bad_owner})

    # Valid WorkOwner publishes via _publication_api and performs GET readback verification
    storage.requests.clear()
    res = storage.candidate(**arguments)
    assert res["candidate_ref"] in storage.refs
    assert storage.requests[-2][0] == "POST" and storage.requests[-2][1] == "/git/refs"
    assert storage.requests[-1][0] == "GET" and storage.requests[-1][1].startswith("/git/ref/heads/ef02/candidate/")


def test_ef02_710_headless_issue_preflight_has_owner_only_no_model_gate():
    # The native read may only be initiated by an owner-created exact-title
    # Issue; no Issue body is ever interpreted as a command, path or model prompt.
    src = (Path(__file__).resolve().parents[1] /
           ".github/workflows/ef02-antigravity-headless-preflight.yml").read_text(encoding="utf-8")
    for fixed in (
        "github.event_name == 'issues'",
        "github.event.action == 'opened'",
        "github.repository == 'trung-via/ai-commerce'",
        "github.actor == github.repository_owner",
        "github.event.issue.user.login == github.repository_owner",
        "github.event.issue.title == '[EF02 HEADLESS READ] #710 G3'",
        "runs-on: [self-hosted, Windows, ai-commerce-ef02]",
        "ref: main",
        "persist-credentials: false",
        "EF02-Antigravity-Headless-Preflight.ps1",
        "AGY_G3_STATIC_PREFLIGHT=PASS_NO_MODEL",
        "EFFECTIVE_WRITE_PERMISSION=UNPROVEN_NO_MODEL",
        "MODEL_AUTH_QUOTA=UNPROVEN_NO_MODEL",
    ):
        assert fixed in src
    assert "issues: write" not in src
    g3_read = src.split("  g3b-write:", 1)[0]
    assert "agy --print" not in g3_read
    assert "AGY_G3B_RESULT" not in g3_read
    assert "issue.body" not in src
    assert "Write-Output $resultLines" not in src



def test_ef02_710_g3b_owner_one_shot_bounded_model_and_disposable_scope():
    src = (Path(__file__).resolve().parents[1] /
           ".github/workflows/ef02-antigravity-headless-preflight.yml").read_text(encoding="utf-8")
    read, after_g3b = src.split("  g3b-write:", 1)
    model = after_g3b.split("\n  g4-forensic:", 1)[0]
    assert "\n  g4-forensic:" in after_g3b
    assert "wp01-native-recovery-restart" not in model
    assert "AGY_G3_STATIC_PREFLIGHT=PASS_NO_MODEL" in read
    for contract in (
        "github.event_name == 'issues'",
        "github.event.action == 'opened'",
        "github.actor == github.repository_owner",
        "github.event.issue.user.login == github.repository_owner",
        "github.event.issue.title == '[EF02 HEADLESS WRITE] #710 G3B'",
        "github.run_attempt == 1",
        "runs-on: [self-hosted, Windows, ai-commerce-ef02]",
        "timeout-minutes: 4",
        "ref: main",
        "persist-credentials: false",
        'shutil.which("agy")',
        'os.environ["RUNNER_TEMP"]',
        "tempfile.mkdtemp",
        "subprocess.CREATE_NEW_PROCESS_GROUP",
        "wait(timeout=90)",
        '"taskkill", "/PID", str(proc.pid), "/T", "/F"',
        '"--model", "gemini-3.8-flash"',
        '"--effort", "high"',
        '"--mode", "accept-edits"',
        '"--output-format", "json"',
        'data.rstrip(b"\\r\\n")',
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL",
        "AGY_G3B_OUTSIDE_WRITES=UNPROVEN",
        "root_original.st_dev, root_original.st_ino",
        "stat.FILE_ATTRIBUTE_REPARSE_POINT",
        "group: epwo-710-g3b-owner-probe",
        'names != ["proof.txt"]',
        'AGY_G3B_RESULT=',
        'AGY_G3B_EFFECTIVE_WRITE=',
    ):
        assert contract in model
    assert "issues: write" not in src
    assert "issue.body" not in src
    assert "github.event.issue.body" not in src
    assert "--dangerously-skip-permissions" not in src
    assert "WORKOWNER" not in model


def test_ef02_710_g3b_inline_python_script_syntax_and_scope_tokens():
    import ast
    import textwrap
    src = (Path(__file__).resolve().parents[1] /
           ".github/workflows/ef02-antigravity-headless-preflight.yml").read_text(encoding="utf-8")
    g3b = src.split("  g3b-write:", 1)[1]
    script = g3b.split("$probe = @'\n", 1)[1].split("\n          '@", 1)[0]
    parsed = ast.parse(textwrap.dedent(script))
    assert any(isinstance(node, ast.Call) for node in ast.walk(parsed))
    assert "stdout=subprocess.PIPE" not in script
    assert "stderr=subprocess.PIPE" not in script
    assert "AGY_G3B_OUTSIDE_WRITES=UNPROVEN" in script
    assert "stream.read(129)" in script
    assert "handle.st_dev, handle.st_ino" in script
    assert "itertools.islice(root.iterdir(), 2)" in script
    assert "target.read_bytes()" not in script
    assert "sorted(p.name for p in root.iterdir())" not in script

def _epwo_710_g4_script():
    import textwrap
    src = (Path(__file__).resolve().parents[1] /
           ".github/workflows/ef02-antigravity-headless-preflight.yml").read_text(encoding="utf-8")
    _, tail = src.split("  g4-forensic:", 1)
    job = tail.split("  wh03b-745-forensic:", 1)[0]
    script = job.split("$probe = @'\n", 1)[1].split("\n          '@", 1)[0]
    return src, job, textwrap.dedent(script)


def test_epwo_710_g4_owner_exact_one_shot_readonly_contract():
    import ast
    src, job, script = _epwo_710_g4_script()
    for element in (
        "github.event_name == 'issues'",
        "github.event.action == 'opened'",
        "github.actor == github.repository_owner",
        "github.event.issue.user.login == github.repository_owner",
        "github.event.issue.title == '[EPWO 710 G4 FORENSIC READ] #710'",
        "github.run_attempt == 1",
        "runs-on: [self-hosted, Windows, ai-commerce-ef02]",
        "persist-credentials: false",
        "contents: read",
        "EPWO_710_G4_",
        "HOLD_UNKNOWN_EFFECT",
    ):
        assert element in job
    ast.parse(script)
    assert "--print" not in job
    assert "issues: write" not in src
    assert "github.event.issue.body" not in job
    assert "write_text(" not in script
    assert "subprocess." not in script
    assert "GITHUB_TOKEN" not in job


def test_epwo_710_g4_historical_local_receipt_stays_unverified(tmp_path):
    import json
    import types
    _, _, code = _epwo_710_g4_script()
    scope = {"__name__": "epwo_710_g4_tests"}
    exec(compile(code, "EPWO710_G4_REVIEWED_INLINE", "exec"), scope)
    root = tmp_path / "batch" / "action"
    scope["EVIDENCE_ROOT"] = root
    result = scope["observe"]()
    assert result["root"] == "BATCH_ABSENT"
    assert result["status"] == "HOLD_UNKNOWN_EFFECT"
    assert result["replay_authorized"] == "FALSE"
    root.parent.mkdir()
    assert scope["observe"]()["root"] == "ACTION_ABSENT"
    ef01 = root / "ef01"
    ef01.mkdir(parents=True)
    valid = {"attempt_id": scope["ATTEMPT"], "stage": "EXECUTOR_SUBPROCESS_RETURNED"}
    (ef01 / "provider_return_observed.json").write_text(json.dumps(valid), encoding="utf-8")
    result = scope["observe"]()
    assert result["root"] == "ACTION_PRESENT"
    assert result["return"] == "PRESENT_UNVERIFIED"
    assert result["provider_outcome_proven"] == "FALSE"
    assert result["status"] == "HOLD_UNKNOWN_EFFECT"
    valid["attempt_id"] = "unrelated-attempt"
    (ef01 / "provider_return_observed.json").write_text(json.dumps(valid), encoding="utf-8")
    assert scope["observe"]()["return"] == "UNSAFE"
    (ef01 / "provider_return_observed.json").write_text("{broken", encoding="utf-8")
    assert scope["observe"]()["return"] == "READ_UNKNOWN"
    (ef01 / "provider_return_observed.json").unlink()
    assert scope["observe"]()["return"] == "MISSING"


def test_epwo_710_g4_never_trusts_synthetic_terminal(tmp_path):
    import json
    _, _, code = _epwo_710_g4_script()
    scope = {"__name__": "epwo_710_g4_tests"}
    exec(compile(code, "EPWO710_G4_REVIEWED_INLINE", "exec"), scope)
    root = tmp_path / "batch" / "action"
    (root / "ef01").mkdir(parents=True)
    scope["EVIDENCE_ROOT"] = root
    terminal = root / "ef01" / "engineering_result.json"
    terminal.write_text(json.dumps({"attempt_id": scope["ATTEMPT"],
                                    "work_id": scope["BATCH"] + ":" + scope["ACTION"]}), encoding="utf-8")
    report = scope["observe"]()
    assert report["terminal"] == "PRESENT_UNVERIFIED"
    assert report["provider_outcome_proven"] == "FALSE"
    terminal.write_text(json.dumps({"attempt_id": scope["ATTEMPT"],"work_id": "wrong"}), encoding="utf-8")
    assert scope["observe"]()["terminal"] == "UNSAFE"


# WH03B #745 fixed native Windows forensic read; script never adjudicates replay.
def _wh03b_745_forensic_script():
    import textwrap
    src = (Path(__file__).resolve().parents[1] /
           ".github/workflows/ef02-antigravity-headless-preflight.yml").read_text(encoding="utf-8")
    job = src.split("  wh03b-745-forensic:", 1)[1]
    script = job.split("$probe = @'\n", 1)[1].split("\n          '@", 1)[0]
    return src, job, textwrap.dedent(script)


def test_wh03b_745_forensic_exact_owner_native_read_only_contract():
    import ast
    src, job, script = _wh03b_745_forensic_script()
    for required in (
        "github.event_name == 'issues'",
        "github.event.action == 'opened'",
        "github.actor == github.repository_owner",
        "github.event.issue.user.login == github.repository_owner",
        "github.event.issue.title == '[WH03B #745 NATIVE FORENSIC READ] exact attempt only'",
        "github.run_attempt == 1",
        "runs-on: [self-hosted, Windows, ai-commerce-ef02]",
        "group: wh03b-745-native-forensic-read",
        "timeout-minutes: 5",
        "contents: read",
        "ref: main",
        "persist-credentials: false",
        "Get-CimInstance Win32_Process -Filter 'ProcessId=25736'",
        "2026-11-09T19:21:00Z",
        "2026-11-09T19:43:12Z",
        "WH03B_745_PID=",
        "HOLD_UNKNOWN_EFFECT",
    ):
        assert required in job
    ast.parse(script)
    assert "issue.body" not in src
    assert "github.event.issue.body" not in job
    assert "issues: write" not in src
    assert "GITHUB_TOKEN" not in job
    assert "subprocess" not in script
    assert "write_text(" not in script
    assert "write_bytes(" not in script
    assert "taskkill" not in job
    assert "agy --print" not in job
    assert "START-Process" not in job
    assert "Chrome" not in job
    for pin in (
        'ISSUE = 745',
        'SOURCE = "5e8503b09f5100a38dfb5ae5e08e3020b9b05790"',
        'ATTEMPT = "wh03b_native_adapter_agy38flashhigh_5e8503b0_20261110_r2b"',
        'JOURNAL_TIP = "29527952859fb8421709345c34fb476a38a81707"',
        '"replay_authorized": "FALSE"',
        '"provider_effect_proven": "FALSE"',
        '"native_identity_proven": "FALSE"',
    ):
        assert pin in script


def test_wh03b_745_forensic_fails_closed_for_wrong_attempt_and_missing_evidence(tmp_path):
    import json
    _, _, code = _wh03b_745_forensic_script()
    scope = {"__name__": "wh03b_745_forensic_test"}
    exec(compile(code, "WH03B_745_NATIVE_READ_REVIEWED_INLINE", "exec"), scope)
    root = tmp_path / "batch" / "action"
    scope["EVIDENCE_ROOT"] = root
    assert scope["_evidence"]()[0] == "BATCH_ABSENT_HERE"
    root.parent.mkdir()
    assert scope["_evidence"]()[0] == "ACTION_ABSENT_HERE"
    ef01 = root / "ef01"
    ef01.mkdir(parents=True)
    rel, stage = scope["EXPECTED"][1]
    target = ef01 / "provider_return_observed.json"
    expected = {"attempt_id": scope["ATTEMPT"], "stage": stage}
    target.write_text(json.dumps(expected), encoding="utf-8")
    result = scope["observe"]()
    assert result["root"] == "ACTION_PRESENT_UNVERIFIED"
    assert result["return"] == "PRESENT_UNVERIFIED"
    assert result["status"] == "HOLD_UNKNOWN_EFFECT"
    assert result["provider_effect_proven"] == "FALSE"
    assert result["replay_authorized"] == "FALSE"
    target.write_text(json.dumps({**expected, "attempt_id": "other"}), encoding="utf-8")
    assert scope["_file_state"](rel, stage) == "MISMATCH"
    target.write_text(json.dumps({**expected, "stage": "OTHER"}), encoding="utf-8")
    assert scope["_file_state"](rel, stage) == "MISMATCH"
    target.write_text("{invalid", encoding="utf-8")
    assert scope["_file_state"](rel, stage) == "READ_UNKNOWN"
    target.unlink()
    assert scope["_file_state"](rel, stage) == "MISSING"


def test_wh03b_745_forensic_never_accepts_fake_terminal(tmp_path):
    import json
    _, _, code = _wh03b_745_forensic_script()
    scope = {"__name__": "wh03b_745_forensic_test"}
    exec(compile(code, "WH03B_745_NATIVE_READ_REVIEWED_INLINE", "exec"), scope)
    root = tmp_path / "batch" / "action"
    (root / "ef01").mkdir(parents=True)
    scope["EVIDENCE_ROOT"] = root
    rel, _ = scope["EXPECTED"][2]
    terminal = root / rel
    terminal.write_text(json.dumps({"attempt_id":scope["ATTEMPT"],
                                     "work_id":scope["BATCH"] + ":" + scope["ACTION"]}),
                        encoding="utf-8")
    assert scope["observe"]()["terminal"] == "PRESENT_UNVERIFIED"
    assert scope["observe"]()["replay_authorized"] == "FALSE"
    terminal.write_text(json.dumps({"attempt_id":scope["ATTEMPT"],"work_id":"bad"}),encoding="utf-8")
    assert scope["_file_state"](rel,None) == "MISMATCH"



def test_epwo_656_worker_recovery_projection_never_acquires_or_dispatches(monkeypatch):
    from test_ef02_m0r_fault_injection import _readonly_audit_fixture
    from ai_commerce.remote.worker import LocalBatchWorker
    from ai_commerce.remote.transport import WorkOwner
    storage, packet, action, owner = _readonly_audit_fixture(unknown=True)
    worker = object.__new__(LocalBatchWorker)
    worker.github = storage
    worker.batch = packet
    worker.issue_number = owner.identity["current_issue"]
    worker.issuer_login = "trung-via"
    def forbidden(*args, **kwargs):
        pytest.fail("recovery review attempted effectful WorkOwner/EF02 operation")
    for name in ("execute_action", "_acquire_owner", "reconcile_action"):
        monkeypatch.setattr(worker, name, forbidden)
    monkeypatch.setattr(WorkOwner, "acquire", forbidden)
    monkeypatch.setattr(WorkOwner, "append", forbidden)
    original_refs = dict(storage.refs)
    storage.requests.clear()
    result = worker.independent_reconciliation_action(action)
    assert result["classification"] == "UNRESOLVED_PROVIDER_BOUNDARY"
    assert result["status"] == "HOLD_RECONCILIATION_REQUIRED"
    assert result["authority"]["journal_write_authorized"] is False
    assert result["authority"]["provider_dispatch_authorized"] is False
    assert storage.refs == original_refs
    assert all(method == "GET" for method, *_ in storage.requests)

def _wp01_native_child_probe_source():
    """Extract only the independently gated WP01 job, not adjacent jobs."""
    import textwrap
    source = (Path(__file__).resolve().parents[1] /
              ".github/workflows/ef02-antigravity-headless-preflight.yml").read_text(encoding="utf-8")
    job = source.split("  wp01-native-child-crash:", 1)[1]
    script = job.split("$probe = @'\n", 1)[1].split("\n          '@", 1)[0]
    return source, job, textwrap.dedent(script)


def test_wp01_native_child_crash_exact_owner_gate_one_shot_and_no_provider():
    """A fresh Issue only authorizes the disposable child, never old effect replay."""
    import ast
    source, job, script = _wp01_native_child_probe_source()
    for required in (
        "github.event_name == 'issues'",
        "github.event.action == 'opened'",
        "github.repository == 'trung-via/ai-commerce'",
        "github.actor == github.repository_owner",
        "github.event.issue.user.login == github.repository_owner",
        "github.event.issue.title == '[EPWO WP01 NATIVE CHILD CRASH] #466'",
        "github.run_attempt == 1",
        "runs-on: [self-hosted, Windows, ai-commerce-ef02]",
        "timeout-minutes: 4",
        "group: epwo-wp01-native-child-crash-one-shot",
        "permissions:\n      contents: read",
        "persist-credentials: false",
        "ref: main",
        "EPWO_WP01_NATIVE_REPLAY_AUTHORIZED=FALSE",
        "EPWO_WP01_NATIVE_PROVIDER=NOT_INVOKED",
        "PASS_CHILD_KILLED_INTENT_ONLY_UNKNOWN",
    ):
        assert required in job, required
    ast.parse(script)
    assert "issue.body" not in job
    assert "github.event.issue.body" not in job
    assert "issues: write" not in job
    assert "GITHUB_TOKEN" not in job
    assert "--model" not in job
    assert "agy " not in job
    assert "codex " not in job
    assert "taskkill" not in job.lower()
    assert "subprocess.Popen" in script
    assert "child.kill()" in script
    assert 'subprocess.CREATE_NEW_PROCESS_GROUP' in script
    assert 'tempfile.TemporaryDirectory(prefix="epwo-wp01-native-", dir=temp)' in script
    assert 'print("EPWO_WP01_NATIVE_HISTORICAL_710=NOT_TOUCHED")' in script
    assert "WORKOWNER" not in script
    assert "write_text(" not in script
    assert "github.api" not in script


def test_wp01_native_child_crash_deny_non_windows_before_any_process_spawn(monkeypatch):
    """Trusted code inspection checks that its first scope gate is Windows."""
    import ast
    _, _, script = _wp01_native_child_probe_source()
    module = ast.parse(script)
    observe = next(n for n in module.body
                   if isinstance(n, ast.FunctionDef) and n.name == "observe")
    first = observe.body[0]
    assert isinstance(first, ast.If)
    assert isinstance(first.test, ast.Compare)
    assert "os.name" in ast.unparse(first.test)
    assert isinstance(first.body[0], ast.Return)
    assert isinstance(first.body[0].value, ast.Constant)
    assert first.body[0].value.value == "HOLD_WINDOWS_UNPROVEN"


def _wp01_native_recovery_restart_source():
    """Extract only the source-owned one-shot restart observer job."""
    import textwrap
    source = (Path(__file__).resolve().parents[1] /
              ".github/workflows/ef02-antigravity-headless-preflight.yml").read_text(encoding="utf-8")
    job = source.split("  wp01-native-recovery-restart:", 1)[1]
    script = job.split("$probe = @'\n", 1)[1].split("\n          '@", 1)[0]
    return job, textwrap.dedent(script)


def test_wp01_recovery_restart_one_shot_owner_scope_and_ast():
    import ast
    job, script = _wp01_native_recovery_restart_source()
    for required in (
        "github.event_name == 'issues'",
        "github.event.action == 'opened'",
        "github.repository == 'trung-via/ai-commerce'",
        "github.actor == github.repository_owner",
        "github.event.issue.user.login == github.repository_owner",
        "github.event.issue.title == '[EPWO WP01 NATIVE RESTART RECOVERY] #466'",
        "github.run_attempt == 1",
        "runs-on: [self-hosted, Windows, ai-commerce-ef02]",
        "timeout-minutes: 4",
        "group: epwo-wp01-native-recovery-restart-once",
        "permissions:\n      contents: read",
        "persist-credentials: false",
        "ref: main",
        "EPWO_WP01_RESTART_RUNNER_REBOOT=NOT_TESTED",
        "EPWO_WP01_RESTART_WORKOWNER_710=NOT_TOUCHED",
        "EPWO_WP01_RESTART_REPLAY_AUTHORIZED=FALSE",
    ):
        assert required in job, required
    assert "github.event.issue.body" not in job
    assert "issues: write" not in job
    assert "GITHUB_TOKEN" not in job
    assert "taskkill" not in job.lower()
    assert "--model" not in job
    assert "codex " not in job
    assert "agy " not in job
    ast.parse(script)
    for token in ("os.fsync(fd)", "os.fsync(ready_fd)", "durable.ready", "FSYNC_DONE", "child.kill()", "child.wait(timeout=8)",
                  "subprocess.run(", "HOLD_UNKNOWN_NO_REPLAY",
                  "PASS_FRESH_READER_HOLD_UNKNOWN", "HOLD_READER_UNVERIFIED",
                  'tempfile.TemporaryDirectory(prefix="epwo-wp01-restart-",dir=temp)'):
        assert token in script, token
    assert script.count("subprocess.Popen(") == 1
    assert script.count("subprocess.run(") == 1


def test_wp01_recovery_restart_reader_is_read_only_and_no_relaunch():
    import ast
    _, script = _wp01_native_recovery_restart_source()
    tree = ast.parse(script)
    observe = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "observe")
    assert isinstance(observe.body[0], ast.If)
    assert "os.name" in ast.unparse(observe.body[0].test)
    assert isinstance(observe.body[0].body[0], ast.Return)
    assert observe.body[0].body[0].value.value == "HOLD_WINDOWS_UNPROVEN"
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and
                      any(isinstance(t, ast.Name) and t.id == "READER" for t in n.targets))
    assert isinstance(assignment.value, (ast.Constant, ast.BinOp))
    # Reconstruct the fixed reader literal from an inert AST, never invoke worker.
    reader_text = ast.literal_eval(assignment.value)
    reader_tree = ast.parse(reader_text)
    assert "HOLD_UNKNOWN_NO_REPLAY" in reader_text
    assert "FSYNC_DONE" in reader_text
    assert "durable.ready" in reader_text
    assert "HOLD_READER_UNVERIFIED" in reader_text
    assert "terminal.exists()" in reader_text
    forbidden = ("subprocess", "Popen(", "os.system", "unlink(", "write_bytes(", "write_text(", "open(")
    assert all(term not in reader_text for term in forbidden)
    assert not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                   n.name == "execute_action" for n in reader_tree.body)


@pytest.mark.parametrize("mode,expected,exit_code", [
    ("valid", "HOLD_UNKNOWN_NO_REPLAY", 0),
    ("missing_intent", "HOLD_READER_UNVERIFIED", 2),
    ("tampered_intent", "HOLD_READER_UNVERIFIED", 2),
    ("missing_ready", "HOLD_READER_UNVERIFIED", 2),
    ("tampered_ready", "HOLD_READER_UNVERIFIED", 2),
    ("conflicting_terminal", "HOLD_READER_UNVERIFIED", 2),
])
def test_wp01_recovery_restart_reader_negative_cases(tmp_path, mode, expected, exit_code):
    """Exercise the actual fresh reader in disposable pytest scratch only."""
    import ast
    _, script = _wp01_native_recovery_restart_source()
    module = ast.parse(script)
    assignment = next(n for n in module.body if isinstance(n, ast.Assign) and
                      any(isinstance(t, ast.Name) and t.id == "READER" for t in n.targets))
    reader_code = ast.literal_eval(assignment.value)
    owned = tmp_path / "epwo-restart-reader"
    owned.mkdir()
    if mode != "missing_intent":
        (owned / "intent.marker").write_bytes(
            b"DIFFERENT" if mode == "tampered_intent" else b"INTENT_ONLY"
        )
    if mode != "missing_ready":
        (owned / "durable.ready").write_bytes(
            b"WRONG" if mode == "tampered_ready" else b"FSYNC_DONE"
        )
    if mode == "conflicting_terminal":
        (owned / "terminal.marker").write_bytes(b"UNVERIFIED")
    result = subprocess.run(
        [sys.executable, "-B", "-c", reader_code, str(owned)],
        cwd=str(owned), capture_output=True, text=True, timeout=8, check=False,
    )
    assert result.returncode == exit_code
    assert result.stdout.strip() == expected
    assert result.stderr == ""
    assert not list(owned.glob("*.new"))


def test_wp02_native_supervisor_owner_gate_and_no_provider():
    src = (Path(__file__).resolve().parents[1] /
           ".github/workflows/ef02-antigravity-headless-preflight.yml").read_text(encoding="utf-8")
    job = src.split("  wp02-native-supervisor-restart:", 1)[1]
    required = (
        "github.event_name == 'issues'",
        "github.event.action == 'opened'",
        "github.repository == 'trung-via/ai-commerce'",
        "github.actor == github.repository_owner",
        "github.event.issue.user.login == github.repository_owner",
        "github.event.issue.title == '[EPWO WP02 NATIVE SUPERVISOR RESTART] #466'",
        "github.run_attempt == 1",
        "runs-on: [self-hosted, Windows, ai-commerce-ef02]",
        "timeout-minutes: 4",
        "permissions:\n      contents: read",
        "ref: main",
        "persist-credentials: false",
        "test_wp02_disposable_supervisor_crash_new_supervisor_readonly",
        "EPWO_WP02_NATIVE_EFFECT=SIMULATED_JOURNAL_ONLY",
        "EPWO_WP02_NATIVE_WORKOWNER_710=NOT_TOUCHED",
        "EPWO_WP02_NATIVE_REPLAY_AUTHORIZED=FALSE",
    )
    assert all(token in job for token in required)
    forbidden = ("issues: write", "github.event.issue.body", "GITHUB_TOKEN",
                 "taskkill", "Restart-Service", "--model", "agy --print", "codex --")
    assert all(token not in job for token in forbidden)


def test_wp02_live_github_readonly_job_closed_contract():
    import ast
    import textwrap
    path = Path(__file__).resolve().parents[1] / ".github/workflows/ef02-antigravity-headless-preflight.yml"
    job = path.read_text(encoding="utf-8").split("  wp02-live-github-journal-readonly:", 1)[1]
    assert "github.event.issue.title == '[EPWO WP02 REAL GITHUB READONLY] #466'" in job
    for text in ("github.actor == github.repository_owner", "github.run_attempt == 1",
                 "github.event.issue.user.login == github.repository_owner",
                 "runs-on: [self-hosted, Windows, ai-commerce-ef02]",
                 "contents: read", "issues: read", "persist-credentials: false",
                 "WP02_LIVE_JOURNAL_REAL_GITHUB_WRITES=0",
                 "WP02_LIVE_JOURNAL_WORKOWNER_710=NOT_TOUCHED"):
        assert text in job
    assert all(x not in job for x in ("contents: write", "issues: write",
               "github.event.issue.body", "taskkill", "Restart-Service"))
    start = job.index("          $probe = @'\n") + len("          $probe = @'\n")
    end = job.index("\n          '@", start)
    script = textwrap.dedent(job[start:end])
    ast.parse(script)
    for text in ('method!="GET"', 'WorkOwner.identity(gh,643,packet,action[0])',
                 'chain=owner.read()', 'verdict=owner.reconcile()',
                 '"HOLD_UNVERIFIED"', 'replay_authorized', 'ROOT=', 'TIP='):
        assert text in script
    assert job.count("& python -B -c $probe") == 2
    assert "$a -ne $b" in job
