"""Contract tests for owner-backed Brain PASS -> protected auto-merge.

Mocks require no GitHub credentials or paid executor. Brain PASS is a pinned
decision distinct from ordinary Human approval. CI failure, changed HEAD/base,
HOLD and missing proof must never merge.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def gate():
    path = ROOT / ".github" / "scripts" / "pr-merge-gate.py"
    spec = importlib.util.spec_from_file_location("ai_commerce_merge_gate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # Unit mock has one synthetic owner; production loads reviewed main code.
    from ai_commerce.engineering.minimum_test import TestGroup
    module.approved_groups = lambda: (
        TestGroup("fixture", ("tests/test_minimum_test.py",), ("fixture.ac",),
                  ("src/ai_commerce/product/identity.py",)),)
    # The fixture intentionally tests a synthetic owner graph. Once main adds
    # committed MT01 component manifests, the production loader correctly
    # expects their real owners; they must not contaminate this synthetic fake.
    # Production protected_owner_groups() remains unchanged and is tested
    # independently with a real owner registry.
    module.protected_owner_groups = lambda: tuple(module.approved_groups())
    return module


class FakeGitHub:
    repo = "trung-via/ai-commerce"
    pr = 219
    head = "a" * 40
    base = "b" * 40

    def __init__(self, g, *, ci="in_progress", changed_head=False,
                 changed_base=False, jobs_ok=True, approval=True,
                 revoked=False, latest_failed=False, receipt_ok=True,
                 tampered_artifact=False, forged_collected_case=False,
                 extra_changed_path=None):
        self.g = g
        self.ci = ci
        self.changed_head = changed_head
        self.changed_base = changed_base
        self.jobs_ok = jobs_ok
        self.approval = approval
        self.revoked = revoked
        self.latest_failed = latest_failed
        self.receipt_ok = receipt_ok
        self.tampered_artifact = tampered_artifact
        self.forged_collected_case = forged_collected_case
        self.extra_changed_path = extra_changed_path
        self.writes = []

    def read_artifact(self, artifact_id):
        import io
        import hashlib
        import json
        import zipfile
        if artifact_id not in (1000, 1001):
            raise ValueError("UNKNOWN_TEST_ARTIFACT")
        os_name = "ubuntu-latest" if artifact_id == 1000 else "windows-latest"
        plan = {
            "version": 2, "base_sha": self.base, "candidate_sha": "c" * 40,
            "mode": "TARGETED", "changed_paths": ["src/ai_commerce/product/identity.py"],
            "selected_tests": ["tests/test_minimum_test.py"],
        }
        plan_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
        xml = b'<testsuites><testsuite><testcase classname="tests.test_minimum_test" name="test_ok"/></testsuite></testsuites>'
        if self.forged_collected_case:
            xml = b'<testsuites><testsuite><testcase classname="tests.test_unrelated" name="test_ok"/></testsuite></testsuites>'
        receipt = {
            "schema": "MT01_VERIFICATION_EVIDENCE_V2", "source_sha": "c" * 40,
            "base_sha": self.base, "pr_head_sha": self.head,
            "platform": "Linux" if os_name == "ubuntu-latest" else "Windows",
            "selection_mode": "TARGETED",
            "selected_test_files": ["tests/test_minimum_test.py"],
            "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "selector_sha256": "a" * 64, "registry_sha256": "b" * 64,
            "chunks": [{
                "test_files": ["tests/test_minimum_test.py"], "exit_code": 0,
                "junit_sha256": hashlib.sha256(xml).hexdigest(),
                "testcases": 1, "counts": {"passed": 1, "skipped": 0, "failed": 0, "errors": 0},
                "observed_cases": [
                    "tests.test_unrelated::test_ok" if self.forged_collected_case
                    else "tests.test_minimum_test::test_ok"],
            }],
            "collected_count": 1, "executed_count": 1, "passed_count": 1,
            "skipped_count": 0, "failed_count": 0, "error_count": 0,
        }
        if getattr(self, "tampered_artifact", False):
            receipt["executed_count"] = 2
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("plan.json", json.dumps(plan))
            archive.writestr("evidence.json", json.dumps(receipt))
            archive.writestr("chunk-0.xml", xml)
        return buffer.getvalue()

    def __call__(self, path, method="GET", body=None):
        assert path.startswith("/repos/" + self.repo + "/")
        if method == "PUT":
            assert path == f"/repos/{self.repo}/pulls/{self.pr}/merge"
            self.writes.append(body)
            return {"merged": True, "sha": "c" * 40}
        if path == f"/repos/{self.repo}/pulls/{self.pr}":
            return {
                "number": self.pr, "state": "open", "merged": False,
                "draft": False, "mergeable": True,
                "head": {"sha": self.head if not self.changed_head else "d" * 40},
                "base": {"sha": self.base if not self.changed_base else "e" * 40,
                         "ref": "main"},
            }
        if path == f"/repos/{self.repo}/pulls/{self.pr}/files?per_page=100":
            paths = [{"filename": "src/ai_commerce/product/identity.py",
                      "status": "modified"}]
            if self.extra_changed_path:
                paths.append({"filename": self.extra_changed_path,
                              "status": "modified"})
            return paths
        if path == f"/repos/{self.repo}/branches/main":
            return {"commit": {"sha": self.base}}
        if path == f"/repos/{self.repo}/issues/{self.pr}/comments?per_page=100":
            if not self.approval:
                return []
            text = self.g.approval_text(self.pr, self.head, self.base, revoke=self.revoked)
            return [{"id": 123, "user": {"login": "trung-via"},
                     "author_association": "OWNER", "body": text}]
        if path.startswith(f"/repos/{self.repo}/actions/runs?"):
            if self.latest_failed:
                return {"workflow_runs": [
                    {"id": 100, "event": "pull_request", "status": "completed",
                     "conclusion": "success", "head_sha": self.head,
                     "created_at": "2026-11-02T00:00:00Z", "path": self.g.CI_WORKFLOW},
                    {"id": 200, "event": "pull_request", "status": "completed",
                     "conclusion": "failure", "head_sha": self.head,
                     "created_at": "2026-11-02T01:00:00Z", "path": self.g.CI_WORKFLOW},
                ]}
            return {"workflow_runs": [
                {"id": 200, "event": "pull_request", "status": self.ci,
                 "conclusion": "success" if self.ci == "completed" else None,
                 "head_sha": self.head, "created_at": "2026-11-02T01:00:00Z",
                 "path": self.g.CI_WORKFLOW}
            ]}
        if path == f"/repos/{self.repo}/actions/runs/200/jobs?per_page=100":
            jobs = []
            for os_name in ("ubuntu-latest", "windows-latest"):
                steps = [{"name": self.g.PYTEST_STEP, "conclusion": "success"},
                         {"name": self.g.RECEIPT_STEP, "conclusion": "success"},
                         {"name": self.g.ORACLE_STEP, "conclusion": "success"},
                         {"name": self.g.NODE_STEP,
                          "conclusion": "success" if self.jobs_ok else "skipped"}]
                jobs.append({"name": f"tests ({os_name})", "status": "completed",
                             "conclusion": "success", "steps": steps})
            return {"jobs": jobs}
        if path == f"/repos/{self.repo}/actions/runs/200/artifacts?per_page=100":
            artifacts = [
                {"id": 1000 + i, "name": f"mt01-v21-{os_name}-200",
                 "expired": False, "size_in_bytes": 1024,
                 "workflow_run": {"id": 200, "head_sha": self.head}}
                for i, os_name in enumerate(("ubuntu-latest", "windows-latest"))
            ]
            return {"artifacts": artifacts if self.receipt_ok else artifacts[:1]}
        if path == f"/repos/{self.repo}/pulls?state=open&per_page=100":
            return [{"number": self.pr, "head": {"sha": self.head}}]
        raise AssertionError(f"Unexpected {method} {path}")


def _approval_event(g, owner=True):
    return {"issue": {"number": FakeGitHub.pr, "pull_request": {"url": "repo"}},
            "comment": {"body": g.approval_text(FakeGitHub.pr, FakeGitHub.head,
                                                 FakeGitHub.base),
                        "user": {"login": "trung-via" if owner else "attacker"},
                        "author_association": "OWNER" if owner else "NONE"}}


def _ci_event(g, conclusion="success", head=None):
    return {"workflow_run": {"name": "EF02 source tests",
                             "event": "pull_request", "status": "completed",
                             "conclusion": conclusion,
                             "head_sha": head or FakeGitHub.head}}


def test_merge_gate_canonical_approval_and_owner_authentication(gate):
    text = gate.approval_text(219, "a" * 40, "b" * 40)
    assert gate.parse_approval(text) == {
        "pr": 219, "head": "a" * 40, "base": "b" * 40, "revoke": False}
    assert gate.parse_approval(text + "\nexecute dangerous code") is None
    assert gate.parse_approval("AI_COMMERCE_BRAIN_REVIEW_PASS_V1\npr=219") is None
    assert gate.candidates(FakeGitHub(gate), FakeGitHub.repo, _approval_event(gate, False)) == []
    assert gate.candidates(FakeGitHub(gate), FakeGitHub.repo, _approval_event(gate)) == [(219, None)]


def test_merge_queue_retains_same_approval_until_ci_finishes(gate):
    api = FakeGitHub(gate, ci="in_progress")
    first = gate.process(api, api.repo, _approval_event(gate))
    assert first[0]["status"] == "WAIT_CI"
    assert first[0]["reason"] == "EXACT_HEAD_CI_STILL_RUNNING"
    assert not api.writes

    # No fresh comment, no new Human prompt. The normal workflow_run completion
    # automatically resumes on the exact same approval and original SHA.
    api.ci = "completed"
    second = gate.process(api, api.repo, _ci_event(gate))
    assert second[0]["status"] == "MERGED"
    assert second[0]["ci_run"] == 200
    assert api.writes == [{"sha": FakeGitHub.head, "merge_method": "merge"}]


@pytest.mark.parametrize("kwargs,status,reason", [
    ({"jobs_ok": False, "ci": "completed"}, "WAIT_EVIDENCE",
     "BOTH_OS_PYTEST_NODE_PROOF_MISSING"),
    ({"approval": False, "ci": "completed"}, "HOLD", "NO_ACTIVE_BRAIN_PASS"),
    ({"revoked": True, "ci": "completed"}, "HOLD", "NO_ACTIVE_BRAIN_PASS"),
    ({"changed_head": True, "ci": "completed"}, "HOLD",
     "BRAIN_PASS_BOUND_TO_DIFFERENT_REVISION"),
    ({"changed_base": True, "ci": "completed"}, "HOLD",
     "BRAIN_PASS_BOUND_TO_DIFFERENT_REVISION"),
    ({"latest_failed": True, "ci": "completed"}, "WAIT_FIX",
     "EXACT_HEAD_CI_NOT_SUCCESS"),
])
def test_merge_gate_fail_closed_and_never_invent_approval(gate, kwargs, status, reason):
    api = FakeGitHub(gate, **kwargs)
    event = _ci_event(gate)
    # For changed HEAD the prior CI event also has old SHA: event-head check
    # must refuse before even consulting stale approval.
    result = gate.process(api, api.repo, event)
    assert result[0]["status"] == status
    if "changed_head" not in kwargs:
        assert result[0]["reason"] == reason
    assert api.writes == []


def test_failed_ci_waits_for_fix_not_human_merge_resubmission(gate):
    api = FakeGitHub(gate, ci="completed", latest_failed=True)
    assert gate.process(api, api.repo, _ci_event(gate, conclusion="failure"))[0]["status"] == "WAIT_FIX"
    assert api.writes == []
    # Rerun of unchanged exact HEAD -> PASS automatically resumes.
    api.latest_failed = False
    assert gate.process(api, api.repo, _ci_event(gate))[0]["status"] == "MERGED"
    assert len(api.writes) == 1


def test_explicit_revoke_cancels_pending_merge(gate):
    api = FakeGitHub(gate, ci="completed", revoked=True)
    assert gate.process(api, api.repo, _ci_event(gate))[0]["status"] == "HOLD"
    assert not api.writes


def test_merge_gate_is_not_triggered_by_unrelated_workflow(gate):
    api = FakeGitHub(gate, ci="completed")
    assert gate.process(api, api.repo, {"workflow_run": {
        "name": "Unrelated", "event": "pull_request",
        "status": "completed", "conclusion": "success",
        "head_sha": api.head}}) == []
    assert not api.writes


def test_merge_gate_v21_missing_os_artifact_holds(gate):
    api = FakeGitHub(gate, ci="completed", receipt_ok=False)
    verdict = gate.process(api, api.repo, _ci_event(gate))
    assert verdict[0]["status"] == "WAIT_EVIDENCE"
    assert verdict[0]["reason"] == "V21_EXACT_RUN_OS_ARTIFACT_PROOF_MISSING"
    assert not api.writes


def test_merge_gate_v21_wrong_run_and_missing_artifact_denied(gate):
    correct = [
        {"id": i + 1, "name": f"mt01-v21-{system}-200",
         "expired": False, "size_in_bytes": 1024,
         "workflow_run": {"id": 200, "head_sha": "a" * 40}}
        for i, system in enumerate(("ubuntu-latest", "windows-latest"))
    ]
    assert gate.verified_v21_artifacts({"artifacts": correct}, 200, "a" * 40)
    assert not gate.verified_v21_artifacts({"artifacts": correct[:1]}, 200, "a" * 40)
    bad = [dict(correct[0]), dict(correct[1])]
    bad[1]["workflow_run"] = {"id": 199, "head_sha": "a" * 40}
    assert not gate.verified_v21_artifacts({"artifacts": bad}, 200, "a" * 40)


def test_merge_gate_v21_rejects_uploaded_forged_receipt_bytes(gate):
    api = FakeGitHub(gate, ci="completed", tampered_artifact=True)
    verdict = gate.process(api, api.repo, _ci_event(gate))
    assert verdict[0]["status"] == "WAIT_EVIDENCE"
    assert verdict[0]["reason"] == "V21_CONTENT_PROOF_INVALID"
    assert not api.writes


def test_merge_gate_v21_rejects_corrupt_zip_payload(gate):
    api = FakeGitHub(gate, ci="completed")
    api.read_artifact = lambda artifact_id: b"corrupt"
    verdict = gate.process(api, api.repo, _ci_event(gate))
    assert verdict[0]["status"] == "WAIT_EVIDENCE"
    assert verdict[0]["reason"] == "V21_CONTENT_PROOF_INVALID"
    assert not api.writes


def test_v21_gate_rejects_self_consistent_fake_junit_omitting_selected_file(gate):
    api = FakeGitHub(gate, ci="completed", forged_collected_case=True)
    verdict = gate.process(api, api.repo, _ci_event(gate))
    assert verdict[0]["status"] == "WAIT_EVIDENCE"
    assert verdict[0]["reason"] == "V21_CONTENT_PROOF_INVALID"
    assert not api.writes


def test_v21_gate_rejects_candidate_plan_that_hides_real_github_diff(gate):
    api = FakeGitHub(gate, ci="completed",
                     extra_changed_path=".github/scripts/pr-merge-gate.py")
    verdict = gate.process(api, api.repo, _ci_event(gate))
    assert verdict[0]["status"] == "WAIT_EVIDENCE"
    assert verdict[0]["reason"] == "V21_CONTENT_PROOF_INVALID"
    assert not api.writes


def test_v21_independent_control_closure_rejects_unknown_github_controls(gate):
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": ".github/workflows/unreviewed.yml", "status": "added"}
    ]) is None
    paths, required = gate.reviewed_coverage_for_changed_files([
        {"filename": ".github/scripts/mt01_v21_proof.py", "status": "modified"},
        {"filename": "tests/test_pr_merge_gate.py", "status": "modified"},
    ])
    assert "tests/test_mt01_v21.py" in required
    assert "tests/test_pr_merge_gate.py" in required
    assert len(paths) == 2



def test_v21_gate_allows_known_node_controls_but_blocks_unknown_control(gate):
    known = [
        {"filename": ".github/scripts/ef02-watch.cjs", "status": "modified"},
        {"filename": "tests/ef02-watch.test.cjs", "status": "modified"},
    ]
    reviewed = gate.reviewed_coverage_for_changed_files(known)
    assert reviewed is not None
    assert reviewed[0] == tuple(sorted(e["filename"] for e in known))
    assert reviewed[1] == ()
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": ".github/scripts/unregistered.cjs", "status": "modified"},
    ]) is None



def test_generic_trusted_main_component_ownership_prevents_partial_receipt(gate, monkeypatch):
    from ai_commerce.engineering.minimum_test import approved_groups as real_groups
    monkeypatch.setattr(gate, "approved_groups", real_groups)
    changed = [{"filename": "src/ai_commerce/product/identity.py", "status": "modified"}]
    result = gate.reviewed_coverage_for_changed_files(changed)
    assert result is not None
    assert {"tests/test_a12_identity.py", "tests/test_a12_claims.py"} <= set(result[1])
    unknown = [{"filename": "src/ai_commerce/product/new_unreviewed_agent.py", "status": "added"}]
    assert gate.reviewed_coverage_for_changed_files(unknown) is None
    assert "tests/test_ef02.py" in gate.reviewed_coverage_for_changed_files([
        {"filename": "src/ai_commerce/remote/worker.py", "status": "modified"}
    ])[1]



def test_merge_gate_trusted_registry_loads_without_importing_app_package(gate):
    # Default merge workflow installs only setup-python, not project deps.
    # Load the explicitly reviewed main file, not ai_commerce.__init__.
    import importlib.util
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / ".github/scripts/pr-merge-gate.py").read_text()
    assert 'spec_from_file_location("_mt01_main_trusted_registry"' in src
    assert "from ai_commerce.engineering.minimum_test import approved_groups" not in src



def test_brain_pass_protocol_rejects_old_human_approval_and_freeform_pass(gate):
    assert gate.parse_approval(
        "AI_COMMERCE_HUMAN_MERGE_APPROVAL_V1\npr=219\nhead_sha=" +
        "a" * 40 + "\nbase_sha=" + "b" * 40
    ) is None
    assert gate.parse_approval("Brain review PASS") is None
    assert gate.parse_approval(gate.approval_text(219, "a" * 40, "b" * 40)
                               + "\nmerge_now=true") is None


def test_brain_pass_gate_workflow_replaces_old_queue():
    new = ROOT / ".github" / "workflows" / "brain-pass-auto-merge.yml"
    old = ROOT / ".github" / "workflows" / "human-merge-queue.yml"
    assert new.is_file()
    assert not old.exists()
    text = new.read_text(encoding="utf-8")
    assert "AI Commerce Brain PASS auto-merge" in text
    assert "issue_comment:" in text
    assert "workflow_run:" in text
    assert "ref: main" in text
    # The merge endpoint requires repository Contents(write), not only PR(write).
    assert "  contents: write" in text.splitlines()
    assert "  actions: read" in text.splitlines()
    assert "  issues: read" in text.splitlines()
    assert "  pull-requests: write" in text.splitlines()
    assert "  contents: read" not in text.splitlines()
    assert "persist-credentials: false" in text
    assert "python .github/scripts/pr-merge-gate.py" in text


def test_brain_pass_exact_identity_and_revision(gate):
    g = FakeGitHub(gate, ci="completed")
    event = _approval_event(gate)
    assert gate.process(g, g.repo, event)[0]["status"] == "MERGED"
    assert len(g.writes) == 1
    bad = FakeGitHub(gate, ci="completed")
    event["comment"]["user"]["login"] = "spoofed-reviewer"
    assert gate.candidates(bad, bad.repo, event) == []
    assert bad.writes == []



# New-component admission: protected main may inspect a pinned candidate's
# strictly additive declaration as DATA, but cannot execute candidate Python.
def _onboarding_fixture():
    import pathlib
    main = (ROOT / "src/ai_commerce/engineering/minimum_test.py").read_text(encoding="utf-8")
    insert = '''        TestGroup("a99", ("tests/test_a99_operations.py",),
                  ("A99.offline-operations",),
                  sources("operations", "new_component_fixture"),
                  consumers=("src/ai_commerce/operations/runner.py",),
                  contracts=("docs/engineering/A99_SYNTHETIC.md",)),
'''
    anchor = '        TestGroup("a05",'
    assert main.count(anchor) == 1
    candidate = main.replace(anchor, insert + anchor, 1)
    files = [
        {"filename": "src/ai_commerce/engineering/minimum_test.py", "status": "modified"},
        {"filename": "src/ai_commerce/operations/new_component_fixture.py", "status": "added"},
        {"filename": "tests/test_a99_operations.py", "status": "added"},
        {"filename": "docs/engineering/A99_SYNTHETIC.md", "status": "added"},
    ]
    return main, candidate, files


def test_new_component_generic_additive_owner_accepted_without_executing_candidate(gate, monkeypatch):
    from ai_commerce.engineering.minimum_test import approved_groups as trusted_groups
    main, candidate, changed = _onboarding_fixture()
    admitted = gate.review_new_component_registry(main, candidate, changed, trusted_groups())
    assert len(admitted) == 1
    assert admitted[0]["name"] == "a99"
    assert admitted[0]["paths"] == ("src/ai_commerce/operations/new_component_fixture.py",)
    monkeypatch.setattr(gate, "approved_groups", trusted_groups)
    assert gate.reviewed_coverage_for_changed_files(changed) is None  # main-only remains closed
    reviewed = gate.reviewed_coverage_for_changed_files(changed, onboarded_groups=admitted)
    assert reviewed is not None
    assert "tests/test_a99_operations.py" in reviewed[1]
    assert "tests/test_minimum_test.py" in reviewed[1]
    # Unowned second source cannot hide behind a legitimate owner declaration.
    assert gate.reviewed_coverage_for_changed_files(
        changed + [{"filename": "src/ai_commerce/operations/malicious.py", "status": "added"}],
        onboarded_groups=admitted) is None


def test_new_component_admission_rejects_changes_to_existing_owner_or_code(gate):
    from ai_commerce.engineering.minimum_test import approved_groups
    main, candidate, files = _onboarding_fixture()
    invalid = candidate.replace('TestGroup("a04"', 'TestGroup("a04-rewritten"', 1)
    with pytest.raises(ValueError, match="EXISTING_TRUSTED_REGISTRY_MUTATED|EXISTING_OWNER_ORDER_CHANGED"):
        gate.review_new_component_registry(main, invalid, files, approved_groups())
    injected = candidate.replace("return (", "import os\n    os.system('do-not-run')\n    return (", 1)
    with pytest.raises((ValueError, SyntaxError)):
        gate.review_new_component_registry(main, injected, files, approved_groups())


@pytest.mark.parametrize("broken", [
    "not_added_test",
    "not_added_source",
    "steal_base_test",
    "stolen_source",
    "malicious_group_expr",
    "no_registry_change",
])
def test_new_component_admission_fails_closed_negative_cases(gate, broken):
    from ai_commerce.engineering.minimum_test import approved_groups
    main, candidate, files = _onboarding_fixture()
    if broken == "not_added_test":
        files[2]["status"] = "modified"
    elif broken == "not_added_source":
        files[1]["status"] = "modified"
    elif broken == "steal_base_test":
        candidate = candidate.replace('"tests/test_a99_operations.py"',
                                      '"tests/test_a04_media_operations.py"', 1)
        files[2]["filename"] = "tests/test_a04_media_operations.py"
    elif broken == "stolen_source":
        candidate = candidate.replace('sources("operations", "new_component_fixture")',
                                      'sources("operations", "runner")', 1)
        files[1]["filename"] = "src/ai_commerce/operations/runner.py"
    elif broken == "malicious_group_expr":
        candidate = candidate.replace('sources("operations", "new_component_fixture")',
                                      '__import__("os").system("forbidden")', 1)
    else:
        files[0]["status"] = "removed"
    with pytest.raises(ValueError):
        gate.review_new_component_registry(main, candidate, files, approved_groups())


def test_candidate_registry_blob_identity_and_sha_must_match(gate):
    import base64
    import hashlib
    main, candidate, files = _onboarding_fixture()
    raw = candidate.encode()
    good_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    base = "/repos/trung-via/ai-commerce"
    suffix = base + "/contents/src/ai_commerce/engineering/minimum_test.py?ref=" + "a" * 40
    def api(path):
        assert path == suffix
        return {"type": "file", "encoding": "base64",
                "content": base64.b64encode(raw).decode(), "sha": good_sha}
    assert gate._read_candidate_registry(api, base, "a" * 40) == candidate
    def invalid(path):
        return {**api(path), "sha": "0" * 40}
    with pytest.raises(ValueError, match="BLOB_INTEGRITY"):
        gate._read_candidate_registry(invalid, base, "a" * 40)


def test_candidate_new_component_not_admitted_without_brain_review_or_all_required_junit(gate, monkeypatch):
    import base64
    import hashlib
    from ai_commerce.engineering.minimum_test import approved_groups
    main, candidate, changed = _onboarding_fixture()
    monkeypatch.setattr(gate, "approved_groups", approved_groups)
    raw = candidate.encode()
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    class NewComponentAPI(FakeGitHub):
        def __call__(self, path, method="GET", body=None):
            if path == (f"/repos/{self.repo}/contents/"
                        f"src/ai_commerce/engineering/minimum_test.py?ref={self.head}"):
                return {"type": "file", "encoding": "base64",
                        "content": base64.b64encode(raw).decode(), "sha": sha}
            if path == f"/repos/{self.repo}/pulls/{self.pr}/files?per_page=100":
                return changed
            return super().__call__(path, method, body)
    api = NewComponentAPI(gate, ci="completed")
    # The main owner never imports candidate; onboarding is demonstrably
    # admitted, but existing self-consistent receipt omits required A99 cases.
    assert gate.onboarded_groups_for_pr(api, f"/repos/{api.repo}", api.head, changed)
    verdict = gate.process(api, api.repo, _ci_event(gate))
    assert verdict[0]["status"] == "WAIT_EVIDENCE"
    assert verdict[0]["reason"] == "V21_CONTENT_PROOF_INVALID"
    assert not api.writes
    no_brain = NewComponentAPI(gate, ci="completed", approval=False)
    assert gate.process(no_brain, no_brain.repo, _ci_event(gate))[0]["reason"] == "NO_ACTIVE_BRAIN_PASS"
    assert not no_brain.writes



def test_new_component_onboarding_end_to_end_complete_both_os_junit_can_merge(gate, monkeypatch):
    """Synthetic only: assert base-trusted gate admits new code after ALL proofs.

    This exercises the full decision path, not just a helper-level happy path.
    Real GitHub artifacts and owner-backed comments remain separately required.
    """
    import base64
    import hashlib
    import io
    import json
    import zipfile
    from ai_commerce.engineering.minimum_test import approved_groups

    base_source, candidate, changed = _onboarding_fixture()
    monkeypatch.setattr(gate, "approved_groups", approved_groups)
    entries = gate.review_new_component_registry(
        base_source, candidate, changed, approved_groups())
    checked = gate.reviewed_coverage_for_changed_files(changed, onboarded_groups=entries)
    assert checked is not None
    changed_paths, required = checked
    assert "tests/test_a99_operations.py" in required
    assert "tests/test_minimum_test.py" in required
    raw = candidate.encode()
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

    class CompleteNewComponentAPI(FakeGitHub):
        def __call__(self, path, method="GET", body=None):
            if path == (f"/repos/{self.repo}/contents/"
                        f"src/ai_commerce/engineering/minimum_test.py?ref={self.head}"):
                return {"type": "file", "encoding": "base64",
                        "content": base64.b64encode(raw).decode(), "sha": sha}
            if path == f"/repos/{self.repo}/pulls/{self.pr}/files?per_page=100":
                return changed
            return super().__call__(path, method, body)

        def read_artifact(self, artifact_id):
            os_name = {1000: "ubuntu-latest", 1001: "windows-latest"}[artifact_id]
            plan = {
                "version": 2, "base_sha": self.base,
                "candidate_sha": "c" * 40, "mode": "TARGETED",
                "changed_paths": list(changed_paths), "selected_tests": list(required),
            }
            plan_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
            cases = [(path.removesuffix(".py").replace("/", ".") + "::test_new_component",
                      path.removesuffix(".py").replace("/", "."))
                     for path in required]
            xml = ("<testsuites><testsuite>" + "".join(
                f'<testcase classname="{cls}" name="test_new_component"/>'
                for _, cls in cases) + "</testsuite></testsuites>").encode()
            receipt = {
                "schema": "MT01_VERIFICATION_EVIDENCE_V2",
                "source_sha": "c" * 40, "base_sha": self.base,
                "pr_head_sha": self.head,
                "platform": "Linux" if os_name == "ubuntu-latest" else "Windows",
                "selection_mode": "TARGETED", "selected_test_files": list(required),
                "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
                "selector_sha256": "a" * 64, "registry_sha256": "b" * 64,
                "chunks": [{
                    "test_files": list(required), "exit_code": 0,
                    "junit_sha256": hashlib.sha256(xml).hexdigest(),
                    "testcases": len(cases),
                    "counts": {"passed": len(cases), "skipped": 0,
                               "failed": 0, "errors": 0},
                    "observed_cases": [name for name, _ in cases],
                }],
                "collected_count": len(cases), "executed_count": len(cases),
                "passed_count": len(cases), "skipped_count": 0,
                "failed_count": 0, "error_count": 0,
            }
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as output:
                output.writestr("plan.json", json.dumps(plan))
                output.writestr("evidence.json", json.dumps(receipt))
                output.writestr("chunk-0.xml", xml)
            return data.getvalue()

    api = CompleteNewComponentAPI(gate, ci="completed")
    verdict = gate.process(api, api.repo, _ci_event(gate))
    assert verdict[0]["status"] == "MERGED"
    assert len(api.writes) == 1



def test_artifact_download_uses_signed_storage_redirect_without_token_leak(gate, monkeypatch):
    import io
    import urllib.error
    import urllib.request

    signer = gate.GitHubAPI("trung-via/ai-commerce", "secret-token")
    download = "https://pipelines.actions.githubusercontent.com/download/signed?sig=abc"
    api_requests, signed_requests = [], []

    class ControlledOpener:
        def open(self, req, timeout):
            api_requests.append(req)
            raise urllib.error.HTTPError(req.full_url, 302, "Found",
                                         {"Location": download}, io.BytesIO(b""))

    class ZIPResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, length):
            return b"PK-valid-offline-fixture"

    def open_signed(req, timeout):
        signed_requests.append(req)
        return ZIPResponse()

    monkeypatch.setattr(gate.urllib.request, "build_opener", lambda *args: ControlledOpener())
    monkeypatch.setattr(gate.urllib.request, "urlopen", open_signed)
    assert signer.read_artifact(99) == b"PK-valid-offline-fixture"
    assert api_requests[0].headers["Authorization"] == "Bearer secret-token"
    assert len(signed_requests) == 1
    assert signed_requests[0].full_url == download
    assert not any(k.lower() == "authorization" for k in signed_requests[0].headers)


@pytest.mark.parametrize("redirect", [
    "http://pipelines.actions.githubusercontent.com/artifact",
    "https://evil.example.com/artifact",
    "https://pipelines.actions.githubusercontent.com.evil.example.com/x",
    "https://user:pass@pipelines.actions.githubusercontent.com/x",
    "https://pipelines.actions.githubusercontent.com/x#fragment",
])
def test_artifact_redirect_rejects_untrusted_location(gate, monkeypatch, redirect):
    import io
    import urllib.error
    client = gate.GitHubAPI("trung-via/ai-commerce", "token")

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 302, "Found",
                                         {"Location": redirect}, io.BytesIO(b""))
    monkeypatch.setattr(gate.urllib.request, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(
        gate.urllib.request, "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("NOT_ALLOWED")))
    with pytest.raises(ValueError, match="ARTIFACT_REDIRECT_HOST_INVALID"):
        client.read_artifact(1)


def test_hostops_v3_queue_workflow_is_explicitly_trusted_gate_coverage(gate):
    """New workflow changes need exact signed queue and merge-gate tests."""
    path = ".github/workflows/hostops-v3.yml"
    reviewed = gate.reviewed_coverage_for_changed_files([
        {"filename": path, "status": "added"},
    ])
    assert reviewed is not None
    changed, required = reviewed
    assert changed == (path,)
    assert set(required) == {
        "tests/test_hostops_v3_integration.py", "tests/test_pr_merge_gate.py",
    }
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": ".github/workflows/unreviewed-hostops.yml", "status": "added"},
    ]) is None


def _existing_owner_addition_fixture():
    """Two real shared-owner files; no candidate module is executed."""
    from ai_commerce.engineering.minimum_test import approved_groups
    main = (ROOT / "src/ai_commerce/engineering/minimum_test.py").read_text(encoding="utf-8")
    old_ef01 = 'eng + "model_defaults.py"),'
    new_ef01 = 'eng + "model_defaults.py", eng + "gate_fixture.py"),'
    old_repair = '(eng + "runner.py",), (rem + "worker.py", rem + "scheduler.py")),'
    new_repair = '(eng + "runner.py", eng + "gate_fixture.py"), (rem + "worker.py", rem + "scheduler.py")),'
    old_mt01 = '(eng + "minimum_test.py", eng + "verification.py"), (eng + "runner.py", rem + "worker.py"))'
    new_mt01 = '(eng + "minimum_test.py", eng + "verification.py", eng + "gate_mt01_probe.py"), (eng + "runner.py", rem + "worker.py"))'
    candidate = main
    for old, new in ((old_ef01, new_ef01), (old_repair, new_repair), (old_mt01, new_mt01)):
        assert candidate.count(old) == 1
        candidate = candidate.replace(old, new, 1)
    files = [
        {"filename": "src/ai_commerce/engineering/minimum_test.py", "status": "modified"},
        {"filename": "src/ai_commerce/engineering/gate_fixture.py", "status": "added"},
        {"filename": "src/ai_commerce/engineering/gate_mt01_probe.py", "status": "added"},
        {"filename": "tests/test_pr_merge_gate.py", "status": "modified"},
    ]
    return main, candidate, files, approved_groups()


def test_existing_owner_additive_source_is_admitted_without_candidate_import(gate):
    main, candidate, files, groups = _existing_owner_addition_fixture()
    entries = gate.review_existing_owner_source_additions(main, candidate, files, groups)
    assert {e["name"] for e in entries} == {"ef01", "repair", "mt01"}
    assert gate.reviewed_coverage_for_changed_files(files) is None
    paths, required = gate.reviewed_coverage_for_changed_files(files, onboarded_groups=entries)
    assert set(paths) == {e["filename"] for e in files}
    # One new source owned by two groups must retain BOTH independent tests.
    assert {"tests/test_ef01.py", "tests/test_ef01_repair.py",
            "tests/test_minimum_test.py", "tests/test_pr_merge_gate.py"} <= set(required)
    assert "tests/test_hostops_v3.py" not in required


@pytest.mark.parametrize("corruption", [
    "removed_source", "modified_source", "replaced_old_owner_test",
    "modified_old_ac", "renamed_old_owner", "new_keyword",
    "unsafe_source_expr", "unknown_source", "unrelated_approved_groups_logic",
])
def test_existing_owner_extension_rejects_nonadditive_or_unreviewed(gate, corruption):
    main, candidate, files, groups = _existing_owner_addition_fixture()
    if corruption == "removed_source":
        files[1]["status"] = "removed"
    elif corruption == "modified_source":
        files[1]["status"] = "modified"
    elif corruption == "replaced_old_owner_test":
        candidate = candidate.replace('TestGroup("repair", ("tests/test_ef01_repair.py",)',
                                      'TestGroup("repair", ("tests/test_ef01.py",)', 1)
    elif corruption == "modified_old_ac":
        candidate = candidate.replace('("EF01.repair",)', '("EF01.repair_modified",)', 1)
    elif corruption == "renamed_old_owner":
        candidate = candidate.replace('TestGroup("repair"', 'TestGroup("repair2"', 1)
    elif corruption == "new_keyword":
        candidate = candidate.replace('eng + "gate_fixture.py"', 'eng + "gate_fixture.py", contracts=("secrets/test.key",)', 1)
    elif corruption == "unsafe_source_expr":
        candidate = candidate.replace('eng + "gate_fixture.py"', '__import__("os").system("unsafe")', 1)
    elif corruption == "unknown_source":
        candidate = candidate.replace('eng + "gate_fixture.py"', 'eng + "unregistered_extra.py"', 1)
    else:
        candidate = candidate.replace('    return (\n', '    print("candidate may not alter registry")\n    return (\n', 1)
    with pytest.raises((ValueError, SyntaxError)):
        gate.review_existing_owner_source_additions(main, candidate, files, groups)


def test_existing_owner_extension_does_not_approve_unrelated_unknown_changes(gate):
    main, candidate, files, groups = _existing_owner_addition_fixture()
    entries = gate.review_existing_owner_source_additions(main, candidate, files, groups)
    extra = {"filename": "src/ai_commerce/engineering/unknown.py", "status": "added"}
    assert gate.reviewed_coverage_for_changed_files(
        files + [extra], onboarded_groups=entries) is None
    assert gate.reviewed_coverage_for_changed_files(
        files + [{"filename": ".github/workflows/bypass.yml", "status": "added"}],
        onboarded_groups=entries) is None


def test_hostops_v4_install_plan_paths_have_independent_trusted_gate(gate):
    """One exact PLAN script/config is reviewed; arbitrary installers stay blocked."""
    reviewed = gate.reviewed_coverage_for_changed_files([
        {"filename": "scripts/HostOps-V4-InstallPlan.ps1", "status": "added"},
        {"filename": "config/hostops-v3/config-v4.example.json", "status": "added"},
    ])
    assert reviewed is not None
    changed, needed = reviewed
    assert set(changed) == {
        "scripts/HostOps-V4-InstallPlan.ps1",
        "config/hostops-v3/config-v4.example.json",
    }
    assert set(needed) == {
        "tests/test_hostops_v3_integration.py", "tests/test_minimum_test.py",
    }
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": "scripts/HostOps-V4-Install-Apply.ps1", "status": "added"},
    ]) is None
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": ".github/workflows/unreviewed-hostops-installer.yml", "status": "added"},
    ]) is None


def test_v22_manifest_owner_admitted_from_exact_github_blob(gate):
    import base64
    import hashlib
    import json

    payload = {
        "schema_version": "MT01ComponentOwner.v1",
        "component": "fresh",
        "sources": ["src/ai_commerce/fresh/service.py"],
        "tests": ["tests/test_fresh_service.py"],
        "acs": ["FRESH.offline"],
        "consumers": ["src/ai_commerce/product/identity.py"],
        "contracts": ["contracts/Fresh.v1.schema.json"],
    }
    manifest_path = "work_items/mt01-components/fresh.json"
    changed = [
        {"filename": path, "status": "added"} for path in (
            manifest_path, *payload["sources"], *payload["tests"], *payload["contracts"])]
    def read(path, method="GET", body=None):
        assert path.endswith(manifest_path + "?ref=" + "a" * 40)
        raw = json.dumps(payload).encode("utf-8")
        sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()
        return {"type": "file", "encoding": "base64",
                "content": base64.b64encode(raw).decode("ascii"), "sha": sha}
    reviewed = gate.reviewed_manifest_groups_for_pr(
        read, "/repos/trung-via/ai-commerce", "a" * 40, changed)
    assert reviewed[0]["tests"] == ("tests/test_fresh_service.py",)
    closure = gate.reviewed_coverage_for_changed_files(
        changed, onboarded_groups=reviewed)
    assert closure is not None
    assert closure[1] == ("tests/test_fresh_service.py", "tests/test_minimum_test.py")


def test_v22_merge_gate_rejects_manifest_shell_and_owner_theft(gate):
    import base64
    import hashlib
    import json
    manifest_path = "work_items/mt01-components/fresh.json"
    payload = {
        "schema_version": "MT01ComponentOwner.v1",
        "component": "fresh",
        "sources": ["src/ai_commerce/fresh/service.py"],
        "tests": ["tests/test_fresh_service.py"],
        "acs": ["FRESH.offline"],
        "consumers": [],
        "contracts": [],
    }
    changed = [{"filename": manifest_path, "status": "added"},
               {"filename": "src/ai_commerce/fresh/service.py", "status": "added"},
               {"filename": "tests/test_fresh_service.py", "status": "added"}]
    def call(data, records=changed):
        def read(path, method="GET", body=None):
            raw = json.dumps(data).encode("utf-8")
            sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()
            return {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(raw).decode(), "sha": sha}
        return gate.reviewed_manifest_groups_for_pr(
            read, "/repos/trung-via/ai-commerce", "a" * 40, records)
    assert call(payload)[0]["name"] == "fresh"
    with pytest.raises(ValueError, match="UNTRUSTED_FIELDS"):
        call({**payload, "argv": ["python", "-m", "pytest", "tests"]})
    with pytest.raises(ValueError, match="COVERAGE_INCOMPLETE"):
        call({**payload, "sources": ["src/ai_commerce/product/identity.py"]})
    with pytest.raises(ValueError, match="MANIFEST_ONBOARDING_CHANGE_UNREVIEWED"):
        call(payload, [dict(changed[0], status="modified"), *changed[1:]])
    with pytest.raises(ValueError, match="BAD_TEST"):
        call({**payload, "tests": ["tests/../test_fresh_service.py"]})
