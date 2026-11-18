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

# Full owner-issued #957 r2 body: preserve quotations, negative requirements,
# historical failures and registered post-model tests in the regression.
# https://github.com/trung-via/ai-commerce/issues/957
ISSUE_957_R2 = json.loads(r'''{
  "kind": "AI_COMMERCE_REMOTE_ACTION_BATCH",
  "version": 1,
  "batch_id": "ef02_mt01_trusted_diag_955_db7dc81e_20261115_r2",
  "source_sha": "db7dc81e7547cc89a1e0cc420e27d4d4d94ac274",
  "actor": "HUMAN",
  "authorization_ref": "Human ChatGPT 2026-11-15 approved Engineering/MT01 #955; 2026-11-15 'kiểm tra, fix #957' authorizes r2 corrective scope; SOURCE_ONLY, no Windows WRITE/provider/unsafe retry.",
  "max_parallel": 1,
  "actions": [
    {
      "action_id": "EF02_MT01_TRUSTED_SERIAL_DIAG_POLICY_955_01_R2",
      "attempt_id": "ef02_mt01_diag_policy_955_db7dc81e_codex_xhigh_20261115_r2",
      "executor": "codex",
      "model": "gpt-6.1-sol",
      "effort": "xhigh",
      "priority": 95,
      "instruction": "MT01_ACCEPTANCE_JSON:[\"EF02.action-and-fanin\"]\nMT01_IMPACT_WITNESSES_JSON:{\"EF02.action-and-fanin\":[\"src/ai_commerce/remote/validation.py\",\"src/ai_commerce/remote/worker.py\"]}\n\nOWNER-APPROVED EF02/MT01 INFRA ROOT REPAIR — Human ChatGPT 2026-11-15 \"ok đồng ý đề xuất\", approving expansion beyond #952 test-file scope after issue #955; #957 r1 pre-executor rejection was IMPACT_CLOSURE_UNPROVEN, and this r2 is a new fingerprinted admission-safe scope (no coding-model invocation occurred for r1). This is a NEW non-WP source-only Coding Engineering action on pinned main db7dc81e7547cc89a1e0cc420e27d4d4d94ac274. Make code changes only. IMPORTANT: EF01 focus brief forbids the model directly invoking pytest; DO NOT run pytest, xdist, shell test suites or custom Git-test harness as executor. Trusted EF01/MT01 independently runs registered Windows tests AFTER you return.\n\nCONFIRMED INCIDENT: #955 run 43682564342, artifact 14149136075, Codex gpt-6.1-sol XHigh invoked and returned code 0 but NO_CODE_CHANGE; agent correctly held because issue required executor-run pytest -n0, while src/ai_commerce/engineering/runner.py focused brief forbids ALL direct executor pytest. EF02 validate still PASS, wasting invocation (provider-reported 39699 input/812 output tokens) without diagnostic. Original #952 candidate 4035a20a3a9b8e8bffefca2603829035808e97cb (ONE file test; 236 passed, 3 environment-symlink skips at -n12) remains Brain HOLD due missing native serial/root diagnosis, not to be replayed/merged now. #948 EXECUTION_UNKNOWN is forensic and never replayed.\n\nOBJECTIVE A — PRE-EXECUTOR CAPABILITY ADMISSION: Extend the authenticated EF02 v1 semantic validator to reject clearly incompatible SOURCE_BUILD requests demanding the coding executor itself run pytest/xdist, before any model pre-spawn checkpoint or provider invocation. Add deterministic negative and ordinary-positive cases: e.g. #955 explicit \"executor must run pytest -n0\" rejected with bounded code EXECUTOR_PYTEST_FORBIDDEN_PRESPAWN; descriptions mentioning pytest for normal post-model EF01 verification and quoted negative instructions still allowed. Prefer a typed capability contract over broad keyword filtering; do not disable the focused executor no-pytest rule or Git guards. If semantic freeform cannot be classified safely, use an explicit typed request whose absence fails closed for imperative diagnostic jobs; document the residual bypass risk and test it.\n\nOBJECTIVE B — TRUSTED WINDOWS B0.3 SERIAL-vs-XDIST DIAGNOSTIC, NO CODING MODEL: Implement one narrow source-registered, Human-owner-only, no-model execution mode reachable through the EXISTING authenticated EF02 Issue ingress and existing Windows self-host run step; do not add/edit .github paths (current allowed_paths rejects .github). An optional backward-compatible action field / finite enum e.g. diagnostic_mode=WINDOWS_B03_SERIAL_XDIST_V1 can be added to contracts/RemoteActionBatch.v1.schema.json, validated in remote/validation.py and implemented in remote/worker.py. Fixed code-defined test selection ONLY: three exact tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender nodes [output-foreign],[output-partial],[journal-rollback]; run once trusted serial (no -n, workers=0) and once -n12 on Windows under comparable load. If the original 12-worker contention requires full B0.3 -n12, label extra run as different load and preserve existing 3 pre-existing symlink permission skips separately. DO NOT interpolate Issue-supplied text, filenames, shell, argv, credentials, env, parameters, or test selectors into commands; all commands are repository-coded finite tuples. Use existing isolated per-job Python and registered pytest infrastructure. Strict source SHA/worktree/actor binding; authorized owner HUMAN only, no arbitrary BRAIN dispatch. No model spawn, no provider checkpoint, no candidate branch when diagnostic_only; evidence state DIAGNOSTIC_COMPLETE/DIAGNOSTIC_FAILED distinct from SOURCE_VERIFIED/PR PASS. Capture real Windows serial-vs-xdist elapsed, selected/collected/executed counts, JUnit SHA256/refs, worker_count observed and redacted child process status; never fabricate a JUnit or use exit 0 as proof. Fail closed on missing pytest/FFmpeg, timeout, missing JUnit, zero selected, unexpected test nodeids, stale source, or runner memory limits. Do not silently alter regular EF02 source-build or its -n12 profile.\n\nCRITICAL SCOPE/POLICY: .github/** is explicitly RESTRICTED by remote/validation.safe_path (not an obstacle to bypass). Keep all .github/workflows, .github/ef02/verify-profiles.json and src/ai_commerce/engineering/runner.py READ ONLY. Do NOT weaken safe_path, remove .github restriction or add dynamic arbitrary shell capability. Do NOT change source B0.3 worker tests or runtime. If immutable .github registry change is truly necessary, return a precise HOLD/dependency instead of silently implementing a partial unsafe profile. Prefer finite, strongly typed diagnostic registered in source-owned code and hardcoded guarded operation, compatible with existing registered profile as a separate diagnostic evidence tier.\n\nALLOWED TRACKED PATHS ONLY: contracts/RemoteActionBatch.v1.schema.json; src/ai_commerce/remote/validation.py; src/ai_commerce/remote/worker.py; tests/test_ef02.py; tests/test_minimum_test.py. src/ai_commerce/engineering/verification.py is READ ONLY: pinned owner-consumer closure check for #957 r1 proved that adding it as an editable path makes admission hold. If its modification is unavoidable, HOLD and report the exact owner contract dependency; do not silently widen this action. No new paths; if additional semantic owner paths required, HOLD and report exact dependency. Existing work_items/EF02-MULTI-ACTION-001.json is historical and must not be rewritten.\n\nPOSITIVE AC: (1) #955 contradictory request and impersonated non-owner diagnostic are denied BEFORE provider invocation with deterministic sanitized reason; (2) ordinary source-build instructions and valid v1 schema/EF02 actions still work; (3) typed owner-only fixed diagnostic runner invokes no coding executor, no Git push/commit, no host write or provider, but actually invokes a known finite Windows pytest serial and -n12 pair only once, captures JUnit/worker proof and returns honest result; (4) unknown mode, arbitrary nodeid, forged source, dependency/fan-in, missing binary, fake JUnit and malicious payload all HOLD; (5) Windows EF02 owner tests and affected MT01 consumer tests execute with normal 12 workers after source change; (6) scope unchanged for WakeHub/BQ/PULSE, prior #952 candidate and #948 unknown preserved.\n\nNEGATIVE AC: no weakening focused executor pytest prohibition, GIT Write Guard, Git SHA/claim, .github restricted path, model auth or protected test policy; no blanket timeout increase, skip/xfail, arbitrary shell, new provider/spend/publish/HostOps, Chrome or native Windows WRITE grant; no auto-merge without exact PR CI + independent Brain PASS. No treating code-only simulated tests as genuine Windows diagnostic; actual three native cases must run only AFTER merged source has registered the trusted mode. At most 2 evidence-led Verify-Fix rounds, deliver CANDIDATE_BRANCH after legitimate MT01.\n\nREVIEW HANDOFF: after source candidate pass, Brain opens PR automatically under 2026-11-15 Human standing decision. After trusted CI/Brain PASS/merge, dispatch a separate exact bounded diagnostic Issue with the new mode; only use its genuine native evidence for subsequent B0.3 RCA and eventual #952 promotion. Do NOT produce such diagnostic effects from this coding-executor attempt.",
      "allowed_paths": [
        "contracts/RemoteActionBatch.v1.schema.json",
        "src/ai_commerce/remote/validation.py",
        "src/ai_commerce/remote/worker.py",
        "tests/test_ef02.py",
        "tests/test_minimum_test.py"
      ],
      "verification_profile": "ef02-tests",
      "verification_intent": "AUTO",
      "depends_on": [],
      "delivery": "CANDIDATE_BRANCH",
      "max_repair_rounds": 2
    }
  ]
}''')


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


@pytest.mark.parametrize("field,value", [
    ("allowed_paths", "src/ai_commerce/feature_a"),
    ("depends_on", "b"),
    ("resources", "fixed-resource"),
])
def test_source_schema_preserves_unique_item_leaf_errors(field, value):
    from jsonschema import ValidationError
    from ai_commerce.remote.validation import validate_batch_content

    packet = batch([action("a"), action("b")])
    packet["actions"][0][field] = [value, value]
    for check in (validate, lambda data: validate_batch_content(
            data, schema=SCHEMA, registry=verify_registry(REGISTRY))):
        with pytest.raises(ValidationError) as error:
            check(packet)
        assert error.value.validator == "uniqueItems"
        assert list(error.value.absolute_path) == ["actions", 0, field]


def test_source_duplicate_paths_preflight_keeps_redacted_error_code(monkeypatch):
    from ai_commerce.remote.authoring_preflight import preflight_batch

    def forbidden(*args, **kwargs):
        pytest.fail("Offline schema rejection must not spawn a child")

    monkeypatch.setattr(subprocess, "run", forbidden)
    packet = batch([action("a")])
    packet["actions"][0].update(
        allowed_paths=["src/ai_commerce/feature_a"] * 2,
        instruction="SECRET_REQUEST_INSTRUCTION")
    result = preflight_batch(packet, schema=SCHEMA, registry=verify_registry(REGISTRY))
    assert result["status"] == "PREFLIGHT_REJECTED"
    assert result["error_code"] == "DUPLICATE_ALLOWED_PATHS"
    assert result["failure_provenance"]["phase"] == "REQUEST_VALIDATION"
    assert result["failure_provenance"]["source_sha"] == packet["source_sha"]
    assert result["failure_provenance"]["attempt_id"] == "a-001"
    assert result["dispatch_performed"] is False
    assert "SECRET_REQUEST_INSTRUCTION" not in json.dumps(result)


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
    from ai_commerce.engineering.runner import DEFAULT_ACTION_TIMEOUT_SECONDS, DEFAULT_VERIFY_TIMEOUT_SECONDS

    # AUTO uses the Human-authorized 90m test cap with finite full action and watchdog.
    auto_verify, auto_action, auto_child = _temporary_verification_budget(human_full_suite_authorized=False)
    assert (auto_verify, auto_action, auto_child) == (5400, 12600, 12900)
    assert auto_verify == DEFAULT_VERIFY_TIMEOUT_SECONDS
    assert auto_action == DEFAULT_ACTION_TIMEOUT_SECONDS
    assert auto_verify < auto_action < auto_child < 360 * 60
    # An already-authorized HUMAN_FULL profile receives 180m of pytest inside
    # a finite 300m action and a 305m EF02 child-process watchdog.
    verify, action, child = _temporary_verification_budget(human_full_suite_authorized=True)
    assert (verify, action, child) == (10800, 18000, 18300)
    assert verify < action < child < 360 * 60


# Exact read-only #1006 request; regressions below replay it offline only.
ISSUE_1006 = json.loads(r'''{
  "kind": "AI_COMMERCE_REMOTE_ACTION_BATCH",
  "version": 1,
  "batch_id": "production_p0_atomic_mt01_owner_dcc76de_20261117",
  "source_sha": "dcc76de4b17871762f4e5de063f0f66251d5c118",
  "actor": "HUMAN",
  "authorization_ref": "Trung approved autonomous Production Pipeline Orchestrator architecture under #993/#994 and continued implementation on 2026-11-17; bounded P0 MT01 owner onboarding.",
  "max_parallel": 1,
  "actions": [
    {
      "action_id": "PRODUCTION_P0_ATOMIC_ORCHESTRATOR_OWNER_CONTRACTS",
      "attempt_id": "production_p0_atomic_codex_xhigh_dcc76de_v1",
      "executor": "codex",
      "model": "gpt-6.1-sol",
      "effort": "xhigh",
      "priority": 95,
      "instruction": "P0 approved #994/#995: atomic MT01 V2.2 onboarding of a shared ProductionPipelineOrchestrator owner from exact main@dcc76de4b17871762f4e5de063f0f66251d5c118; only allowed files, no protected .github modification. MT01_ACCEPTANCE_JSON: [\"PRODUCTION.orchestration-p0\"]. Read docs/engineering/MT01_V22_ATOMIC_ONBOARDING_REPORT.md and work_items/mt01-components/b0-2.json and bq-01a.json as read-only examples of valid owner manifests. Add new manifest work_items/mt01-components/production-orchestration.json with valid MT01ComponentOwner.v1 schema; component id production-orchestration, sources exactly new three Python paths, owned tests tests/test_production_orchestration.py, AC PRODUCTION.orchestration-p0 and real existing downstream consumer groups A10 M05 bridge briefs and A03 creative briefs per actual repo selectors (do not invent test filenames). New src/ai_commerce/production_orchestration/{__init__,contracts,orchestrator}.py implements typed strictly versioned StageSpec, ProductionStageCompleted, ProductionStageDispatched, ProductionStageReceipt data contracts and validations for schema version, source/hash provenance, event/route, consumer binding, duplicate idempotence, malformed/missing receipts and NOT_DISPATCHED when no genuine downstream execution. P0 is CONTRACT+OWNER ONLY; no fake durable outbox, no PULSE stage state machine, no claim of live A03 dispatch, no external effects. Tests exercise happy and negative cases; actual atomic onboarding profile is mt01-component-onboarding, 12 workers only when self-host evidence. Preserve existing src policies; if registration fails, report exact blockers in EF02 receipt instead of security bypass. Deliver isolated CANDIDATE_BRANCH and exact tests with real CLI model invocation provenance; no source main mutations, no Human approval gate internal.",
      "allowed_paths": [
        "work_items/mt01-components/production-orchestration.json",
        "src/ai_commerce/production_orchestration/__init__.py",
        "src/ai_commerce/production_orchestration/contracts.py",
        "src/ai_commerce/production_orchestration/orchestrator.py",
        "tests/test_production_orchestration.py"
      ],
      "verification_profile": "mt01-component-onboarding",
      "verification_intent": "AUTO",
      "depends_on": [],
      "delivery": "CANDIDATE_BRANCH",
      "max_repair_rounds": 2
    }
  ]
}''')


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


def onboarding_packet():
    packet = copy.deepcopy(ISSUE_1006)
    # Preserve #1006 as historical provenance. A merged owner cannot be
    # re-admitted as new: use a new offline-only owner/AC for positive tests.
    packet["source_sha"] = "a" * 40  # Offline immutable fixture only.
    packet["batch_id"] += "_fresh_owner_ingress"
    item = packet["actions"][0]
    item["attempt_id"] += "_fresh_owner_ingress"
    item["allowed_paths"] = [
        "work_items/mt01-components/atb-onboarding-smoke.json",
        "src/ai_commerce/atb_onboarding_smoke/__init__.py",
        "src/ai_commerce/atb_onboarding_smoke/contracts.py",
        "src/ai_commerce/atb_onboarding_smoke/orchestrator.py",
        "tests/test_atb_onboarding_smoke.py",
    ]
    item["instruction"] = (AC_MARKER + '["ATB01.onboarding-smoke"]\n'
                           + "PRIVATE_INSTRUCTIONS: offline new owner contract only")
    return packet


def test_onboarding_1006_corrected_standalone_ac_contracts_without_claiming_proof(
        tmp_path, monkeypatch, capsys):
    from ai_commerce.remote import __main__ as cli
    from ai_commerce.remote.validation import validate_acceptance_scope
    from ai_commerce.engineering import adapters
    from ai_commerce.engineering.impact_v21 import validate_component_onboarding_manifest
    from ai_commerce.engineering.minimum_test import approved_groups

    files, calls = make_acceptance_repo(tmp_path, monkeypatch)
    packet = onboarding_packet()
    item = packet["actions"][0]
    original = copy.deepcopy(packet)
    assert validate(packet) == packet
    def forbidden(*args, **kwargs):
        pytest.fail("Ingress cannot invoke model, tests, transport or candidate worker")
    for name in ("GitHubTransport", "LocalBatchWorker", "run_schedule"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(adapters, "command_for", forbidden)

    result = validate_acceptance_scope(packet, repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_COMPATIBLE"
    assert result["coverage_status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
    assert result["next_gate"] == "POST_EDIT_MT01_REQUIRED"
    assert result["actions"] == [{
        "action_id": item["action_id"], "status": "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN",
        "onboarding_contract": {
            "source_sha": packet["source_sha"], "request_fingerprint": fingerprint(packet),
            "allowed_paths": item["allowed_paths"],
            "acceptance_ids": ["ATB01.onboarding-smoke"], "coverage_proven": False,
        },
    }]
    assert packet == original
    assert not any((tmp_path / p).exists() for p in item["allowed_paths"])
    acceptance = approved_acceptance_plan(item)
    assert acceptance["consumer_map"] == {} and acceptance["broad_required"] is True
    assert acceptance["unknown_ids"] == ["ATB01.onboarding-smoke"]

    # Admission is no substitute for the existing verifier's actual bytes.
    data = {"schema_version": "MT01ComponentOwner.v1", "component": "atb-onboarding-smoke",
            "sources": item["allowed_paths"][1:4], "tests": item["allowed_paths"][4:],
            "acs": acceptance["ids"], "consumers": [VALIDATION_SOURCE], "contracts": []}
    with pytest.raises(ValueError, match="^ONBOARD_FILE_MISSING_OR_LINK$"):
        validate_component_onboarding_manifest(data, manifest_path=item["allowed_paths"][0],
            changed_paths=item["allowed_paths"], base_paths=tuple(files),
            existing_groups=approved_groups(), allowed_paths=item["allowed_paths"], root=tmp_path)

    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    assert cli.main(["validate", "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 0
    assert json.loads(capsys.readouterr().out)["acceptance_scope"] == result
    assert calls and all(args[0] in {"ls-tree", "cat-file", "show"} for args in calls)
    for field in ("instruction", "allowed_paths", "source_sha"):
        changed = copy.deepcopy(packet)
        if field == "source_sha":
            changed[field] = "b" * 40
        elif field == "instruction":
            changed["actions"][0][field] += " "
        else:
            changed["actions"][0][field].pop()
        assert fingerprint(changed) != fingerprint(packet)


@pytest.mark.parametrize("problem", [
    "second-manifest", "unsafe-manifest", "remote", "engineering", "existing-package",
    "existing-source", "existing-test", "existing-manifest", "existing-component",
    "protected", "directory", "wildcard", "nested-source", "two-packages", "no-source", "no-test",
    "duplicate-path", "case-alias", "reserved-name", "control-character", "ordinary-profile",
    "baseline-ac", "mixed-ac", "unknown-extra-ac", "bad-ac", "duplicate-ac", "missing-ac",
    "inline-ac-1006", "quoted-ac", "fenced-ac", "duplicate-marker", "bad-json", "fake-impact",
    "second-action", "dependency", "reuse",
])
def test_onboarding_invalid_or_ambiguous_contract_holds_without_effects(
        tmp_path, monkeypatch, capsys, problem):
    from ai_commerce.remote import __main__ as cli
    from ai_commerce.remote.validation import validate_acceptance_scope
    from ai_commerce.engineering import adapters

    make_acceptance_repo(tmp_path, monkeypatch)
    packet = onboarding_packet()
    item = packet["actions"][0]
    paths = item["allowed_paths"]
    if problem == "second-manifest":
        paths.append("work_items/mt01-components/second-owner.json")
    elif problem == "unsafe-manifest":
        paths[0] = "work_items/mt01-components/../outside.json"
    elif problem in {"remote", "engineering", "existing-package"}:
        package = "hostops_v3" if problem == "existing-package" else problem
        paths[1:4] = [p.replace("atb_onboarding_smoke", package) for p in paths[1:4]]
    elif problem in {"existing-source", "existing-test", "existing-manifest", "protected"}:
        paths.append({"existing-source": VALIDATION_SOURCE, "existing-test": "tests/test_ef02.py",
                      "existing-manifest": "work_items/mt01-components/hostops-personal.json",
                      "protected": ".github/workflows/ef02-batch.yml"}[problem])
    elif problem == "existing-component":
        paths[0] = "work_items/mt01-components/ef02.json"
    elif problem in {"directory", "wildcard", "nested-source", "two-packages"}:
        paths[1] = {"directory": "src/ai_commerce/atb_onboarding_smoke",
                    "wildcard": "src/ai_commerce/atb_onboarding_smoke/*.py",
                    "nested-source": "src/ai_commerce/atb_onboarding_smoke/nested/owner.py",
                    "two-packages": "src/ai_commerce/another_package/owner.py"}[problem]
    elif problem == "no-source":
        del paths[1:4]
    elif problem == "no-test":
        paths.pop()
    elif problem == "duplicate-path":
        paths.append(paths[-1])
    elif problem == "case-alias":
        paths.append("src/ai_commerce/atb_onboarding_smoke/CONTRACTS.py")
    elif problem == "reserved-name":
        paths[1] = "src/ai_commerce/atb_onboarding_smoke/CON.py"
    elif problem == "control-character":
        paths[0] = "work_items/mt01-components/new\nowner.json"
    elif problem == "ordinary-profile":
        item["verification_profile"] = "ef02-tests"
    elif problem in {"baseline-ac", "mixed-ac", "unknown-extra-ac", "bad-ac", "duplicate-ac"}:
        ids = {"baseline-ac": ["EF02.action-and-fanin"],
               "mixed-ac": ["ATB01.onboarding-smoke", "EF02.action-and-fanin"],
               "unknown-extra-ac": ["ATB01.onboarding-smoke", "UNKNOWN.unbound"],
               "bad-ac": ["--arbitrary"],
               "duplicate-ac": ["ATB01.onboarding-smoke"] * 2}[problem]
        item["instruction"] = AC_MARKER + json.dumps(ids)
    elif problem == "missing-ac":
        item["instruction"] = "PRIVATE_INSTRUCTIONS"
    elif problem == "inline-ac-1006":
        item["instruction"] = ISSUE_1006["actions"][0]["instruction"]
    elif problem == "quoted-ac":
        item["instruction"] = "> " + item["instruction"]
    elif problem == "fenced-ac":
        item["instruction"] = "```text\n" + item["instruction"] + "\n```"
    elif problem == "duplicate-marker":
        item["instruction"] += "\n" + AC_MARKER + '["ATB01.onboarding-smoke"]'
    elif problem == "bad-json":
        item["instruction"] = AC_MARKER + '{"acs": []}'
    elif problem == "fake-impact":
        item["instruction"] += '\nMT01_IMPACT_WITNESSES_JSON:{"ATB01.onboarding-smoke":["' + VALIDATION_SOURCE + '"]}'
    elif problem == "second-action":
        packet["actions"].append(scoped_action("b", [VALIDATION_SOURCE], ["EF02.action-and-fanin"]))
    elif problem == "dependency":
        item["depends_on"] = ["missing"]
    elif problem == "reuse":
        item["reuse_candidate"] = {"candidate_sha": "b" * 40, "verified_run_id": 123}
    original = copy.deepcopy(packet)
    result = validate_acceptance_scope(packet, repo=tmp_path)
    assert result["status"] == result["coverage_status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["next_gate"] == "HOLD_NO_EXECUTOR"
    assert result["actions"][0]["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["actions"][0]["reason"].startswith(("ONBOARD_", "ALLOWED_SCOPE_"))
    assert packet == original

    def forbidden(*args, **kwargs):
        pytest.fail("HOLD/REJECT must precede model, verification, claim and transport")
    for name in ("GitHubTransport", "LocalBatchWorker", "run_schedule"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(adapters, "command_for", forbidden)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    assert cli.main(["run", "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_INSTRUCTIONS" not in captured.out + captured.err


@pytest.mark.parametrize("problem", ["baseline-file", "baseline-case-alias", "package-link", "manifest-parent-link"])
def test_onboarding_uses_pinned_inventory_for_freshness_and_parent_safety(tmp_path, monkeypatch, problem):
    from ai_commerce.remote.validation import validate_acceptance_scope
    packet = onboarding_packet()
    item = packet["actions"][0]
    path = {"baseline-file": item["allowed_paths"][-1],
            "baseline-case-alias": item["allowed_paths"][-1].upper(),
            "package-link": "src/ai_commerce/atb_onboarding_smoke",
            "manifest-parent-link": "work_items"}[problem]
    make_acceptance_repo(tmp_path, monkeypatch, contents={path: b"elsewhere"},
                         modes={path: b"120000"} if problem.endswith("link") else {})
    result = validate_acceptance_scope(packet, repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["next_gate"] == "HOLD_NO_EXECUTOR"


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


@pytest.mark.parametrize("policy", [None, "EF01_POST_MODEL_ONLY"])
@pytest.mark.parametrize("instruction", [
    "Make one scoped source change. EF01 runs registered pytest -n 12 after the model returns.",
    "DO NOT run pytest, xdist, shell test suites or custom Git-test harness as executor.",
    "Reject requests demanding the coding executor itself run pytest/xdist.",
    'Quoted example: "executor must run pytest -n0" must reject before claim.',
    "Example: `executor must run pytest -n0` is prohibited; EF01 verifies after return.",
    "The executor must not run pytest. Never run xdist as the coding model.",
    "The historical issue said executor must run pytest -n0. Implement a preflight rejection.",
    "```text\nexecutor must run pytest -n0\n```\nImplement the pre-spawn rejection.",
])
def test_executor_policy_preserves_negative_quoted_and_post_model_instructions(policy, instruction):
    item = action("a")
    item["instruction"] = instruction
    if policy:
        item["executor_test_policy"] = policy
    assert validate(batch([item]))["actions"][0] == item


def test_full_957_r2_wording_is_not_an_executor_test_mandate():
    assert validate(copy.deepcopy(ISSUE_957_R2)) == ISSUE_957_R2
    typed = copy.deepcopy(ISSUE_957_R2)
    typed["actions"][0]["executor_test_policy"] = "EF01_POST_MODEL_ONLY"
    assert validate(typed) == typed


@pytest.mark.parametrize("instruction", [
    "executor must run pytest -n0",
    "The coding executor itself must run pytest -n 0 before returning.",
    "You shall execute python -m pytest tests/test_ef02.py -n 12.",
    "Run pytest -n0 as the coding executor.",
    "Please invoke xdist before returning.",
    "The model must directly run `pytest -n0`.",
])
@pytest.mark.parametrize("policy", [None, "EF01_POST_MODEL_ONLY"])
def test_clear_executor_test_imperatives_are_rejected_even_with_typed_default(instruction, policy):
    item = action("a")
    item["instruction"] = instruction
    if policy:
        item["executor_test_policy"] = policy
    with pytest.raises(RemoteRequestError, match="^EXECUTOR_PYTEST_FORBIDDEN_PRESPAWN$"):
        validate(batch([item]))


@pytest.mark.parametrize("policy", ["EXECUTOR_PYTEST_REQUIRED", "unknown", None, True, []])
def test_executor_test_policy_is_finite_and_required_capability_is_incompatible(policy):
    from jsonschema import ValidationError
    item = action("a")
    item["executor_test_policy"] = policy
    if policy == "EXECUTOR_PYTEST_REQUIRED":
        with pytest.raises(RemoteRequestError, match="^EXECUTOR_PYTEST_FORBIDDEN_PRESPAWN$"):
            validate(batch([item]))
    else:
        with pytest.raises(ValidationError):
            validate(batch([item]))


def _copy_remote_contracts(checkout):
    for relative in ("contracts/RemoteActionBatch.v1.schema.json", ".github/ef02/verify-profiles.json",
                     ".github/ef02/approved-candidates.v1.json"):
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())


@pytest.mark.parametrize("command", ["validate", "run"])
@pytest.mark.parametrize("typed", [False, True])
def test_executor_conflict_cli_stops_before_source_scope_transport_or_claim(tmp_path, monkeypatch, capsys, command, typed):
    from ai_commerce.remote import __main__ as cli
    _copy_remote_contracts(tmp_path)
    item = action("a")
    item["instruction"] = "executor must run pytest -n0 PRIVATE_DATA"
    if typed:
        item["instruction"] = "PRIVATE_DATA"
        item["executor_test_policy"] = "EXECUTOR_PYTEST_REQUIRED"
    request = tmp_path / "request.json"
    request.write_text(json.dumps(batch([item])), encoding="utf-8")
    def forbidden(*args, **kwargs):
        pytest.fail("Incompatible capability must reject before any dispatch effect")
    for name in ("check_source", "validate_acceptance_scope", "GitHubTransport", "LocalBatchWorker", "run_schedule"):
        monkeypatch.setattr(cli, name, forbidden)
    assert cli.main([command, "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 2
    output = capsys.readouterr()
    assert output.err == "EF02_REQUEST_REJECTED: EXECUTOR_PYTEST_FORBIDDEN_PRESPAWN\n"
    assert "PRIVATE_DATA" not in output.err + output.out


@pytest.mark.parametrize("typed", [False, True])
def test_worker_defends_preclaim_policy_for_direct_callers(tmp_path, monkeypatch, typed):
    from types import SimpleNamespace
    from ai_commerce.remote import worker as module
    item = action("a")
    if typed:
        item["executor_test_policy"] = "EXECUTOR_PYTEST_REQUIRED"
    else:
        item["instruction"] = "executor must run pytest -n0"
    def forbidden(*args, **kwargs):
        pytest.fail("No claim, worktree, checkpoint, provider or child allowed")
    worker = module.LocalBatchWorker.__new__(module.LocalBatchWorker)
    worker.root, worker.checkout, worker.batch = tmp_path, tmp_path, batch([item])
    worker.github = SimpleNamespace(claim=forbidden)
    monkeypatch.setattr(module.subprocess, "Popen", forbidden)
    monkeypatch.setattr(module, "_new_worktree_path", forbidden)
    result = worker.execute_action(item)
    assert result["status"] == "INVALID_ACTION" and result["executor_invoked"] is False
    assert result["error_code"] == "EXECUTOR_PYTEST_FORBIDDEN_PRESPAWN"


def diagnostic_packet():
    packet = batch([{"action_id": "diag", "attempt_id": "diag-001", "depends_on": [],
                     "diagnostic_mode": "WINDOWS_B03_SERIAL_XDIST_V1", "delivery": "DIAGNOSTIC_ONLY"}])
    packet["max_parallel"] = 1
    return packet


def test_diagnostic_contract_is_owner_only_and_distinct_from_source_build():
    assert validate(diagnostic_packet()) == diagnostic_packet()
    with pytest.raises(RemoteRequestError, match="^ISSUER_NOT_AUTHORIZED_FOR_ACTOR$"):
        validate(diagnostic_packet(), issuer="impersonator")
    with pytest.raises(RemoteRequestError, match="^ISSUER_NOT_AUTHORIZED_FOR_ACTOR$"):
        validate(diagnostic_packet(), issuer="", owner="")
    packet = diagnostic_packet()
    packet["actor"] = "BRAIN"
    with pytest.raises(RemoteRequestError, match="^DIAGNOSTIC_OWNER_HUMAN_REQUIRED$"):
        validate(packet, issuer="brain", brain=("brain",))


@pytest.mark.parametrize("field,value", [
    ("diagnostic_mode", "UNKNOWN"), ("diagnostic_mode", None),
    ("delivery", "CANDIDATE_BRANCH"), ("nodeids", ["tests/other.py::test_other"]),
    ("argv", ["python", "-c", "unsafe"]), ("env", {"PYTEST_ADDOPTS": "-n0"}),
    ("path", "C:/arbitrary"), ("resources", ["arbitrary"]),
    ("allowed_paths", ["tests/test_b0_3_attended_worker.py"]),
    ("instruction", "run arbitrary test"), ("executor", "codex"),
    ("reuse_candidate", {"candidate_sha": "a" * 40, "verified_run_id": 1}),
    ("depends_on", ["other"]), ("resolved_dependencies", []),
])
def test_diagnostic_rejects_unknown_mode_and_request_command_surfaces(field, value):
    from jsonschema import ValidationError
    packet = diagnostic_packet()
    packet["actions"][0][field] = value
    with pytest.raises(ValidationError):
        validate(packet)


@pytest.mark.parametrize("problem", ["duplicate", "mixed", "parallel", "fanin"])
def test_diagnostic_rejects_multiple_actions_and_fanin(problem):
    packet = diagnostic_packet()
    if problem == "parallel":
        packet["max_parallel"] = 2
    elif problem == "mixed":
        packet["actions"].append(action("source"))
    else:
        other = copy.deepcopy(packet["actions"][0])
        if problem == "fanin":
            other.update(action_id="other", attempt_id="other-001")
        packet["actions"].append(other)
    with pytest.raises(RemoteRequestError, match="^DIAGNOSTIC_SINGLE_ACTION_REQUIRED$"):
        validate(packet)


def _synthetic_diagnostic_artifacts(module, checkout, phase, nonce):
    """SYNTHETIC control fixture; Windows native proof remains NOT_YET_RUN."""
    root = checkout / module.B03_DIAGNOSTIC_ROOT
    directory = root / phase
    nodes = module.B03_DIAGNOSTIC_NODEIDS
    cases = "".join('<testcase classname="tests.test_b0_3_attended_worker" name="' + n.split("::")[1] + '" time="0.01" />' for n in nodes)
    (root / (phase + ".junit.xml")).write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0">' + cases + '</testsuite></testsuites>', encoding="utf-8")
    workers = ["serial"] if phase == "serial" else [f"gw{i}" for i in range(12)]
    for index, worker in enumerate(workers):
        base = {"version": 1, "nonce": nonce, "worker": worker, "pid": 100 + index}
        module._write_json(directory / (worker + ".collection.json"), {**base, "nodeids": list(nodes)})
        module._write_json(directory / (worker + ".session.json"),
                           {**base, "workers_requested": 0, "start": 100.0, "end": 200.0, "exitstatus": 0})
        selected = nodes if phase == "serial" else nodes[index:index + 1]
        if selected:
            events = [{**base, "nodeid": node, "when": when, "outcome": "passed", "at": 150.0, "duration": 0.01}
                      for node in selected for when in ("setup", "call", "teardown")]
            module._write_json(directory / (worker + ".events.json"), {"events": events})
    if phase == "xdist":
        module._write_json(directory / "controller.session.json", {"version": 1, "nonce": nonce,
            "worker": "controller", "pid": 99, "workers_requested": 12, "exitstatus": 0, "start": 90.0, "end": 210.0})
    return b"collecting ... collected 3 items\n" if phase == "serial" else b"created: 12/12 workers\n12 workers [3 items]\n"


def _offline_diagnostic(tmp_path, monkeypatch, *, damage=None, damaged_phase="serial"):
    from ai_commerce.remote import worker as module
    _copy_remote_contracts(tmp_path)
    monkeypatch.setattr(module, "_diagnostic_environment", lambda checkout: {})
    monkeypatch.setattr(module, "_diagnostic_source", lambda *args: None)
    calls = []
    def child(argv, *, checkout, env):
        calls.append(argv)
        phase, nonce = env["AI_COMMERCE_B03_PHASE"], env["AI_COMMERCE_B03_NONCE"]
        raw = _synthetic_diagnostic_artifacts(module, checkout, phase, nonce)
        root = checkout / module.B03_DIAGNOSTIC_ROOT
        if phase == damaged_phase:
            junit = root / (phase + ".junit.xml")
            collection = root / phase / ("serial" if phase == "serial" else "gw0")
            if damage == "missing-junit":
                junit.unlink()
            elif damage == "fake-junit-only":
                for path in (root / phase).iterdir():
                    path.unlink()
            elif damage == "unexpected-node":
                junit.write_bytes(junit.read_bytes().replace(b"output-foreign", b"foreign-extra"))
            elif damage == "skipped-node":
                junit.write_bytes(junit.read_bytes().replace(b'skipped="0"', b'skipped="1"'))
            elif damage == "zero-nodes":
                junit.write_bytes(b'<testsuites><testsuite tests="0" /></testsuites>')
            elif damage == "extra-node":
                junit.write_bytes(junit.read_bytes().replace(b"</testsuite>", b'<testcase name="extra" /></testsuite>'))
            elif damage == "missing-collection":
                collection.with_suffix(".collection.json").unlink()
            elif damage == "malformed-receipt":
                collection.with_suffix(".session.json").write_bytes(b"{")
            elif damage == "wrong-nonce":
                path = collection.with_suffix(".collection.json")
                receipt = json.loads(path.read_text())
                receipt["nonce"] = "0" * 64
                path.write_text(json.dumps(receipt))
            elif damage == "duplicate-stage":
                path = collection.with_suffix(".events.json")
                receipt = json.loads(path.read_text())
                receipt["events"].append(receipt["events"][0])
                path.write_text(json.dumps(receipt))
            elif damage == "worker-mismatch":
                raw = b"created: 11/11 workers\n11 workers [3 items]\n"
            elif damage == "spoofed-workers":
                if phase == "serial":
                    raw += b"created: 12/12 workers\n"
                else:
                    (root / phase / "gw11.collection.json").unlink()
            elif damage == "timeout":
                return 0, raw, "DIAGNOSTIC_TIMEOUT"
            elif damage == "job-unavailable":
                raise module.RemoteRequestError("DIAGNOSTIC_JOB_UNAVAILABLE")
            elif damage == "nonzero-exit":
                return 1, raw, ""
            elif damage == "tamper-prior" and phase == "xdist":
                (root / "serial.output.log").write_bytes(b"tampered")
        return 0, raw + b"SECRET_TOKEN=C:/private/fixture\n", ""
    monkeypatch.setattr(module, "_run_diagnostic_child", child)
    def forbidden(*args, **kwargs):
        pytest.fail("Offline control tests must never spawn or invoke Git/provider")
    monkeypatch.setattr(module.subprocess, "Popen", forbidden)
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    return module, calls


def test_diagnostic_fixed_pair_and_evidence_control_only(tmp_path, monkeypatch):
    module, calls = _offline_diagnostic(tmp_path, monkeypatch)
    report = module.execute_diagnostic_batch(checkout=tmp_path, batch=diagnostic_packet(),
        issuer_login="owner", owner_login="owner", issue_number=957)
    assert report["status"] == "DIAGNOSTIC_COMPLETE" and report["executor_invoked"] is False
    assert report["memory_policy"] == "NO_JOB_MEMORY_LIMIT" and report["memory_limit_bytes"] is None
    assert report["authorization"] == "AUTHENTICATED_OWNER_HUMAN" and report["github_issue_number"] == 957
    assert report["source_sha"] == diagnostic_packet()["source_sha"]
    assert report["request_fingerprint"] == fingerprint(diagnostic_packet())
    assert len(calls) == 2 and all(isinstance(argv, tuple) for argv in calls)
    assert [argv[:3] for argv in calls] == [(sys.executable, "-m", "pytest")] * 2
    assert "-n" not in calls[0] and calls[1][calls[1].index("-n") + 1] == "12"
    assert all(tuple(a for a in argv if a.startswith("tests/")) == module.B03_DIAGNOSTIC_NODEIDS for argv in calls)
    assert [c["workers_observed"] for c in report["cases"]] == [0, 12]
    assert all(c["junit_nodeids"] == list(module.B03_DIAGNOSTIC_NODEIDS) for c in report["cases"])
    assert all(c["elapsed_seconds"] > 0 and c["ended_at"] >= c["started_at"] for c in report["cases"])
    for phase in ("serial", "xdist"):
        root = tmp_path / module.B03_DIAGNOSTIC_ROOT
        receipt = json.loads((root / (phase + ".result.json")).read_text())
        assert receipt["memory_policy"] == "NO_JOB_MEMORY_LIMIT" and receipt["memory_limit_bytes"] is None
        assert receipt["source_sha"] == report["source_sha"]
        assert receipt["request_fingerprint"] == report["request_fingerprint"]
        assert module._verify_diagnostic_receipt(root, receipt)
        assert b"SECRET_TOKEN" not in (root / (phase + ".output.log")).read_bytes()
        for ref, digest in receipt["artifacts"].items():
            assert hashlib.sha256((root / ref).read_bytes()).hexdigest() == digest
    # All subprocesses above were fakes. This test supplies NO Windows native proof.


def test_diagnostic_elapsed_receipt_uses_high_resolution_clock_on_windows(tmp_path, monkeypatch):
    """A coarse Windows monotonic tick must not zero genuine short intervals."""
    module, calls = _offline_diagnostic(tmp_path, monkeypatch)
    # Deliberately simulate a Windows monotonic clock that does not advance
    # during either synthetic child; the high-resolution clock is independent.
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(module.time, "monotonic", lambda: 1234.0)
        report = module.execute_diagnostic_batch(
            checkout=tmp_path, batch=diagnostic_packet(),
            issuer_login="owner", owner_login="owner", issue_number=957)
    assert report["status"] == "DIAGNOSTIC_COMPLETE" and len(calls) == 2
    assert all(case["elapsed_seconds"] > 0 for case in report["cases"])
    root = tmp_path / module.B03_DIAGNOSTIC_ROOT
    for phase in ("serial", "xdist"):
        receipt = json.loads((root / (phase + ".result.json")).read_text())
        assert module._verify_diagnostic_receipt(root, receipt)
        receipt["elapsed_seconds"] = 0.0
        assert module._verify_diagnostic_receipt(root, receipt) is False


@pytest.mark.parametrize("phase", ["serial", "xdist"])
@pytest.mark.parametrize("damage", ["missing-junit", "fake-junit-only", "unexpected-node", "skipped-node",
    "zero-nodes", "extra-node", "missing-collection", "malformed-receipt", "wrong-nonce", "duplicate-stage",
    "worker-mismatch", "spoofed-workers", "timeout", "job-unavailable", "nonzero-exit"])
def test_diagnostic_fails_closed_on_unproven_native_evidence(tmp_path, monkeypatch, phase, damage):
    module, calls = _offline_diagnostic(tmp_path, monkeypatch, damage=damage, damaged_phase=phase)
    report = module.execute_diagnostic_batch(checkout=tmp_path, batch=diagnostic_packet(),
        issuer_login="owner", owner_login="owner", issue_number=957)
    assert report["status"] == "DIAGNOSTIC_FAILED" and report["error_code"]
    assert report["native_proof"] == "WINDOWS_NATIVE_UNPROVEN"
    assert report["executor_invoked"] is False and len(calls) <= 2
    assert "SECRET_TOKEN" not in json.dumps(report)


def test_diagnostic_rechecks_prior_artifact_hashes_after_pair(tmp_path, monkeypatch):
    module, _ = _offline_diagnostic(tmp_path, monkeypatch, damage="tamper-prior", damaged_phase="xdist")
    report = module.execute_diagnostic_batch(checkout=tmp_path, batch=diagnostic_packet(),
        issuer_login="owner", owner_login="owner", issue_number=957)
    assert report["status"] == "DIAGNOSTIC_FAILED"


@pytest.mark.parametrize("mutation", ["artifact", "malformed", "exit", "workers", "clock",
    "missing-memory-policy", "unknown-memory-policy", "legacy-memory-policy", "legacy-memory-cap",
    "missing-memory-limit", "false-memory-limit", "extra-field"])
def test_diagnostic_receipt_cannot_authorize_tampered_or_malformed_evidence(tmp_path, monkeypatch, mutation):
    module, _ = _offline_diagnostic(tmp_path, monkeypatch)
    report = module.execute_diagnostic_batch(checkout=tmp_path, batch=diagnostic_packet(),
        issuer_login="owner", owner_login="owner", issue_number=957)
    assert report["status"] == "DIAGNOSTIC_COMPLETE"
    root = tmp_path / module.B03_DIAGNOSTIC_ROOT
    receipt = json.loads((root / "serial.result.json").read_text())
    if mutation == "artifact":
        (root / "serial.junit.xml").write_bytes(b"tampered")
    elif mutation == "malformed":
        receipt = {"phase": "serial", "status": "DIAGNOSTIC_COMPLETE", "artifacts": receipt["artifacts"]}
    elif mutation == "exit":
        receipt["child_exit_code"] = 1
    elif mutation == "workers":
        receipt["workers_observed"] = 12
    elif mutation == "clock":
        receipt["elapsed_seconds"] = float("nan")
    elif mutation == "missing-memory-policy":
        del receipt["memory_policy"]
    elif mutation == "unknown-memory-policy":
        receipt["memory_policy"] = "ISSUE_SUPPLIED_POLICY"
    elif mutation == "legacy-memory-policy":
        receipt["memory_policy"] = "JOB_MEMORY_LIMIT"
    elif mutation == "legacy-memory-cap":
        receipt["memory_limit_bytes"] = 6 * 1024 ** 3
    elif mutation == "missing-memory-limit":
        del receipt["memory_limit_bytes"]
    elif mutation == "false-memory-limit":
        receipt["memory_limit_bytes"] = False
    else:
        receipt["extra_field"] = True
    assert module._verify_diagnostic_receipt(root, receipt) is False


@pytest.mark.parametrize("problem", ["issuer", "brain", "issue", "reuse"])
def test_diagnostic_direct_route_reauthenticates_before_any_child(tmp_path, monkeypatch, problem):
    module, calls = _offline_diagnostic(tmp_path, monkeypatch)
    packet, issuer, issue_number = diagnostic_packet(), "owner", 957
    if problem == "issuer":
        issuer = "impersonator"
    elif problem == "brain":
        packet["actor"] = "BRAIN"
    elif problem == "issue":
        issue_number = 0
    else:
        (tmp_path / module.B03_DIAGNOSTIC_ROOT).mkdir(parents=True)
    report = module.execute_diagnostic_batch(checkout=tmp_path, batch=packet,
        issuer_login=issuer, owner_login="owner", issue_number=issue_number)
    assert report["status"] == "DIAGNOSTIC_FAILED" and not calls


@pytest.mark.parametrize("problem", ["source", "dirty", "operator"])
def test_diagnostic_checks_exact_head_clean_source_and_loaded_operator(tmp_path, monkeypatch, problem):
    from ai_commerce.remote import worker as module
    monkeypatch.setattr(module, "check_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, ("b" if problem == "source" else "a") * 40 + "\n", ""))
    monkeypatch.setattr(module, "checkout_diagnostics", lambda *args, **kw: {"clean": problem != "dirty"})
    monkeypatch.setattr(module, "__file__", str(tmp_path / ("wrong.py" if problem == "operator" else "src/ai_commerce/remote/worker.py")))
    with pytest.raises(RemoteRequestError, match="^DIAGNOSTIC_(STALE_SOURCE|DIRTY_SOURCE|UNPINNED_OPERATOR)$"):
        module._diagnostic_source(tmp_path, "a" * 40)


@pytest.mark.parametrize("problem", ["windows", "venv", "binary"])
def test_diagnostic_requires_real_windows_job_venv_and_both_media_tools(tmp_path, monkeypatch, problem):
    from types import SimpleNamespace
    from ai_commerce.remote import worker as module
    import os
    venv = tmp_path / "venv"
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix" if problem == "windows" else "nt",
                                                      environ={**os.environ, "VIRTUAL_ENV": str(venv)}))
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32", prefix=str(venv),
        base_prefix=str(tmp_path / "base"),
        executable=str(tmp_path / "wrong.exe" if problem == "venv" else venv / "Scripts/python.exe")))
    monkeypatch.setattr(module, "platform", SimpleNamespace(system=lambda: "Windows"))
    monkeypatch.setattr(module, "shutil", SimpleNamespace(
        which=lambda name: None if problem == "binary" and name == "ffprobe" else name + ".exe"))
    with pytest.raises(RemoteRequestError, match="^DIAGNOSTIC_(WINDOWS_REQUIRED|ISOLATED_PYTHON_REQUIRED|MEDIA_BINARY_MISSING)$"):
        module._diagnostic_environment(tmp_path)


def test_diagnostic_environment_clears_pytest_injection_and_preserves_git_guards(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ai_commerce.remote import worker as module
    import os
    venv = tmp_path / "venv"
    environment = {**os.environ, "VIRTUAL_ENV": str(venv), "PYTEST_ADDOPTS": "--arbitrary",
                   "PYTEST_PLUGINS": "untrusted", "AI_COMMERCE_B03_NONCE": "forged",
                   "GITHUB_TOKEN": "SECRET", "AI_COMMERCE_GIT_GUARD_SENTINEL": "keep",
                   "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "test.guard", "GIT_CONFIG_VALUE_0": "keep"}
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", environ=environment))
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32", prefix=str(venv),
        base_prefix=str(tmp_path / "base"), executable=str(venv / "Scripts/python.exe")))
    monkeypatch.setattr(module, "platform", SimpleNamespace(system=lambda: "Windows"))
    monkeypatch.setattr(module, "shutil", SimpleNamespace(which=lambda name: name + ".exe"))
    env = module._diagnostic_environment(tmp_path)
    assert "PYTEST_ADDOPTS" not in env and "GITHUB_TOKEN" not in env and "AI_COMMERCE_B03_NONCE" not in env
    assert env["PYTEST_PLUGINS"] == "xdist.plugin" and env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    for key in ("AI_COMMERCE_GIT_GUARD_SENTINEL", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        assert env[key] == environment[key]


def test_diagnostic_child_timeout_kills_job_before_returning(tmp_path, monkeypatch):
    """Fake process and Job only; no native B0.3 commands are executed."""
    from types import SimpleNamespace
    from ai_commerce.remote import worker as module
    events, state = [], {"done": False}
    class Child:
        returncode = 1
        def poll(self):
            return 1 if state["done"] else None
        def wait(self, timeout):
            return 1
    def stop():
        events.append("stop")
        state["done"] = True
    job = SimpleNamespace(attach=lambda child: events.append("attach"), stop=stop,
                          close=lambda: events.append("close"))
    monkeypatch.setattr(module, "_windows_diagnostic_job", lambda: job)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: Child())
    clock = iter((0.0, 601.0))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    result = module._run_diagnostic_child(module._B03_COMMANDS[0], checkout=tmp_path, env={})
    assert result == (1, b"", "DIAGNOSTIC_TIMEOUT")
    assert events == ["attach", "stop", "close"]


def _offline_windows_diagnostic_job(monkeypatch, *, failure=None):
    """Exercise the real Job helper with API/process fakes, never native runs."""
    import ctypes
    from types import SimpleNamespace
    from ai_commerce.remote import worker as module
    events, configured = [], []
    state = {"child": None, "assigned": False, "queries": 0}

    class Function:
        def __init__(self, name, callback):
            self.name, self.callback = name, callback
        def __call__(self, *args):
            events.append(self.name)
            return self.callback(*args)

    class Child:
        _handle = 456
        returncode = None
        def poll(self):
            return self.returncode
        def kill(self):
            events.append("child-kill")
            self.returncode = 1
        def wait(self, timeout):
            assert self.returncode is not None and timeout == 10
            events.append("child-wait")
            return self.returncode

    def configure(handle, kind, pointer, size):
        limits = pointer._obj
        assert handle == 123 and kind == 9 and size == ctypes.sizeof(limits)
        configured.append((limits.basic.flags, limits.process_memory, limits.job_memory))
        return failure != "configure"

    def assign(handle, child_handle):
        assert handle == 123 and child_handle.value == Child._handle
        if failure == "attach-exception":
            raise OSError("synthetic assignment exception")
        if failure == "attach":
            return 0
        state["assigned"] = True
        return 1

    def resume(child_handle):
        assert state["assigned"] and child_handle.value == Child._handle
        if failure == "resume":
            return 1
        state["child"].returncode = 0
        return 0

    def terminate_child():
        if state["assigned"] and state["child"].returncode is None:
            state["child"].returncode = 1

    def terminate(handle, code):
        assert handle == 123 and code == 1
        if failure == "terminate":
            return 0
        terminate_child()
        return 1

    def query(handle, kind, pointer, size, returned):
        assert handle == 123 and kind == 1 and size == ctypes.sizeof(pointer._obj) and returned is None
        if failure == "query":
            return 0
        state["queries"] += 1
        # A descendant outlives the direct child until the second observation.
        pointer._obj.active = int(state["assigned"] and state["queries"] == 1)
        return 1

    def close(handle):
        assert handle == 123
        terminate_child()
        return 1

    # No GlobalMemoryStatusEx symbol: even querying minimum RAM fails this fixture.
    kernel = SimpleNamespace(**{name: Function(name, callback) for name, callback in {
        "CreateJobObjectW": lambda *args: 0 if failure == "create" else 123,
        "SetInformationJobObject": configure, "AssignProcessToJobObject": assign,
        "TerminateJobObject": terminate, "QueryInformationJobObject": query, "CloseHandle": close,
    }.items()})
    native = SimpleNamespace(NtResumeProcess=Function("NtResumeProcess", resume))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: {"kernel32": kernel, "ntdll": native}[name],
                        raising=False)

    def spawn(argv, **kwargs):
        assert argv in module._B03_COMMANDS and kwargs["creationflags"] == 0x08000004
        events.append("spawn-suspended")
        state["child"] = Child()
        return state["child"]
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module, "time", SimpleNamespace(time=time.time, perf_counter_ns=time.perf_counter_ns,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: events.append("wait-descendants")))
    return module, events, configured


def test_diagnostic_job_has_no_ram_admission_or_memory_cap_and_retains_containment(tmp_path, monkeypatch):
    module, events, configured = _offline_windows_diagnostic_job(monkeypatch)
    assert module._B03_MEMORY_POLICY == "NO_JOB_MEMORY_LIMIT"
    assert module._B03_TIMEOUT == 600 and module._B03_OUTPUT_LIMIT == 8 * 1024 ** 2
    assert module._run_diagnostic_child(module._B03_COMMANDS[0], checkout=tmp_path, env={}) == (0, b"", "")
    # Only KILL_ON_JOB_CLOSE; neither JOB_MEMORY (0x200) nor PROCESS_MEMORY (0x100).
    assert configured == [(0x2000, 0, 0)]
    assert events == ["CreateJobObjectW", "SetInformationJobObject", "spawn-suspended",
        "AssignProcessToJobObject", "NtResumeProcess", "child-wait", "TerminateJobObject",
        "QueryInformationJobObject", "wait-descendants", "QueryInformationJobObject", "CloseHandle"]


@pytest.mark.parametrize("failure,error_code", [
    ("create", "DIAGNOSTIC_JOB_UNAVAILABLE"), ("configure", "DIAGNOSTIC_JOB_CONFIGURATION_FAILED"),
    ("attach", "DIAGNOSTIC_JOB_ASSIGN_FAILED"), ("attach-exception", "DIAGNOSTIC_EXECUTION_UNPROVEN"),
    ("resume", "DIAGNOSTIC_CHILD_RESUME_FAILED"),
    ("terminate", "DIAGNOSTIC_CHILD_TERMINATION_UNPROVEN"),
    ("query", "DIAGNOSTIC_CHILD_TERMINATION_UNPROVEN"),
])
def test_diagnostic_job_failures_reject_before_spawn_or_native_success(tmp_path, monkeypatch, failure, error_code):
    from ai_commerce.remote import worker as module
    run_child = module._run_diagnostic_child
    _offline_diagnostic(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_run_diagnostic_child", run_child)
    module, events, _ = _offline_windows_diagnostic_job(monkeypatch, failure=failure)
    report = module.execute_diagnostic_batch(checkout=tmp_path, batch=diagnostic_packet(),
        issuer_login="owner", owner_login="owner", issue_number=957)
    assert report["status"] == "DIAGNOSTIC_FAILED" and report["error_code"] == error_code
    assert report["native_proof"] == "WINDOWS_NATIVE_UNPROVEN" and report["succeeded"] == 0
    assert report["executor_invoked"] is False and len(report["cases"]) == 1
    assert report["cases"][0]["status"] == "DIAGNOSTIC_FAILED"
    assert report["memory_policy"] == "NO_JOB_MEMORY_LIMIT" and report["memory_limit_bytes"] is None
    if failure == "create":
        assert events == ["CreateJobObjectW"]
    elif failure == "configure":
        assert events == ["CreateJobObjectW", "SetInformationJobObject", "CloseHandle"]
    else:
        assert events.count("spawn-suspended") == 1 and events.count("CloseHandle") == 1
        if failure in ("attach", "attach-exception"):
            assert "NtResumeProcess" not in events
            assert "child-kill" in events and "child-wait" in events
        if failure in ("terminate", "query"):
            assert "NtResumeProcess" in events  # Even a zero-exit child cannot bypass cleanup proof.


@pytest.mark.parametrize("feedback_fails", [False, True])
def test_diagnostic_cli_routes_before_schedule_and_never_emits_wake(tmp_path, monkeypatch, capsys, feedback_fails):
    from types import SimpleNamespace
    from ai_commerce.remote import __main__ as cli
    module, calls = _offline_diagnostic(tmp_path, monkeypatch)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(diagnostic_packet()), encoding="utf-8")
    monkeypatch.setattr(cli, "check_source", lambda *args, **kwargs: None)
    def forbidden(*args, **kwargs):
        pytest.fail("No source scheduler/worker, acceptance, provider, candidate or wake permitted")
    for name in ("validate_acceptance_scope", "LocalBatchWorker", "run_schedule", "build_completion_event", "start_worker_signal"):
        monkeypatch.setattr(cli, name, forbidden)
    comments = []
    def comment(issue, body):
        comments.append((issue, body))
        if feedback_fails:
            raise RuntimeError("SECRET_FEEDBACK")
    monkeypatch.setattr(cli, "GitHubTransport", lambda *args: SimpleNamespace(comment=comment))
    arguments = ["--request-file", str(request), "--checkout", str(tmp_path),
                 "--issuer-login", "owner", "--owner-login", "owner", "--issue-number", "957"]
    assert cli.main(["validate", *arguments]) == 0
    assert not calls
    assert json.loads(capsys.readouterr().out)["native_proof"] == "NOT_YET_RUN"
    assert cli.main(["run", *arguments]) == (1 if feedback_fails else 0)
    result = json.loads((tmp_path / module.B03_DIAGNOSTIC_ROOT / "batch_result.json").read_text())
    assert result["status"] == "DIAGNOSTIC_COMPLETE" and len(calls) == 2
    assert result["feedback_status"] == ("FEEDBACK_PUBLICATION_FAILED" if feedback_fails else "GITHUB_ISSUE_COMMENT_PUBLISHED")
    assert len(comments) == 1 and "SECRET_FEEDBACK" not in json.dumps(result)
    assert not (tmp_path / module.B03_DIAGNOSTIC_ROOT / "completion_event.json").exists()
