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
        os_name = "windows-latest"
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
            "platform": "Windows",
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
            for os_name in ("windows-latest",):
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
                for i, os_name in enumerate(("windows-latest",))
            ]
            return {"artifacts": artifacts if self.receipt_ok else []}
        if path == f"/repos/{self.repo}/pulls?state=open&per_page=100":
            return [{"number": self.pr, "head": {"sha": self.head}}]
        raise AssertionError(f"Unexpected {method} {path}")


def _approval_event(g, owner=True):
    """Only a pinned COMMENT review wakes gate; PR Conversation is authority."""
    return {
        "action": "submitted",
        "pull_request": {
            "number": FakeGitHub.pr, "state": "open",
            "head": {"sha": FakeGitHub.head},
            "base": {"sha": FakeGitHub.base},
        },
        "review": {
            "body": g.approval_text(FakeGitHub.pr, FakeGitHub.head, FakeGitHub.base),
            "commit_id": FakeGitHub.head,
            "state": "commented",
            "user": {"login": "trung-via" if owner else "attacker"},
            "author_association": "OWNER" if owner else "NONE",
        },
    }


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
    assert gate.candidates(FakeGitHub(gate), FakeGitHub.repo, _approval_event(gate)) == [(219, FakeGitHub.head)]


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
     "WINDOWS_PYTEST_NODE_PROOF_MISSING"),
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
    assert verdict[0]["reason"] == "V21_EXACT_RUN_WINDOWS_ARTIFACT_PROOF_MISSING"
    assert not api.writes


def test_merge_gate_v21_wrong_run_and_missing_artifact_denied(gate):
    correct = [
        {"id": i + 1, "name": f"mt01-v21-{system}-200",
         "expired": False, "size_in_bytes": 1024,
         "workflow_run": {"id": 200, "head_sha": "a" * 40}}
        for i, system in enumerate(("windows-latest",))
    ]
    assert gate.verified_v21_artifacts({"artifacts": correct}, 200, "a" * 40)
    assert not gate.verified_v21_artifacts({"artifacts": []}, 200, "a" * 40)
    bad = [dict(correct[0])]
    bad[0]["workflow_run"] = {"id": 199, "head_sha": "a" * 40}
    assert not gate.verified_v21_artifacts({"artifacts": bad}, 200, "a" * 40)


def test_mt01_junit_class_case_requires_exact_test_module_boundary(gate):
    """Real unittest/pytest classnames must count; prefix-collision forgery must not."""
    import io
    import json
    import hashlib
    import zipfile

    original = FakeGitHub(gate).read_artifact(1000)
    with zipfile.ZipFile(io.BytesIO(original)) as z:
        source = {n: z.read(n) for n in z.namelist()}

    def rewritten(class_name):
        payload = dict(source)
        receipt = json.loads(payload["evidence.json"])
        xml = payload["chunk-0.xml"].replace(
            b'classname="tests.test_minimum_test"',
            ('classname="' + class_name + '"').encode("ascii"))
        assert xml != source["chunk-0.xml"]
        receipt["chunks"][0]["junit_sha256"] = hashlib.sha256(xml).hexdigest()
        receipt["chunks"][0]["observed_cases"] = [class_name + "::test_ok"]
        payload["chunk-0.xml"] = xml
        payload["evidence.json"] = json.dumps(receipt).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            for path, content in payload.items():
                z.writestr(path, content)
        return buffer.getvalue()

    kwargs = dict(expected_head="a" * 40, expected_base="b" * 40,
                  os_name="windows-latest",
                  expected_changed_paths=("src/ai_commerce/product/identity.py",),
                  required_test_files=("tests/test_minimum_test.py",))
    assert gate.validate_receipt_zip(
        rewritten("tests.test_minimum_test.TestRealClass"), **kwargs)
    assert not gate.validate_receipt_zip(
        rewritten("tests.test_minimum_test_evil.TestRealClass"), **kwargs)
    assert not gate.validate_receipt_zip(
        rewritten("tests.test_unrelated.TestRealClass"), **kwargs)


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
    assert "  issue_comment:" not in text
    assert "  pull_request_review:" in text
    assert "    types: [submitted]" in text
    assert "github.event.review.state == 'commented'" in text
    assert "github.event.review.user.login == github.repository_owner" in text
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
    event["review"]["user"]["login"] = "spoofed-reviewer"
    assert gate.candidates(bad, bad.repo, event) == []
    assert bad.writes == []




@pytest.mark.parametrize("tamper", (
    "issue_comment", "non_pr_comment", "missing_comment_body",
    "extra_comment_line", "reviewer_spoof", "wrong_commit", "wrong_head",
    "wrong_base", "stale_pr", "wrong_pr", "revoked", "review_approved",
    "edited_review", "missing_review_commit", "wrong_review_state",
))
def test_brain_wake_requires_exact_owner_comment_review(gate, tamper):
    from copy import deepcopy
    event = deepcopy(_approval_event(gate))
    if tamper == "issue_comment":
        event = {
            "action": "created", "issue": {"number": 219, "pull_request": {}},
            "comment": {"body": gate.approval_text(219, "a"*40, "b"*40),
                        "user": {"login": "trung-via"}, "author_association": "OWNER"}
        }
    elif tamper == "non_pr_comment":
        event = {"action": "created", "issue": {"number": 281},
                 "comment": {"body": "EF02_EXECUTION_SUMMARY_V1"}}
    elif tamper == "missing_comment_body":
        event["review"]["body"] = ""
    elif tamper == "extra_comment_line":
        event["review"]["body"] += "\\nnot-canonical"
    elif tamper == "reviewer_spoof":
        event["review"]["user"]["login"] = "attacker"
    elif tamper == "wrong_commit":
        event["review"]["commit_id"] = "d"*40
    elif tamper == "wrong_head":
        event["pull_request"]["head"]["sha"] = "d"*40
    elif tamper == "wrong_base":
        event["pull_request"]["base"]["sha"] = "d"*40
    elif tamper == "stale_pr":
        event["pull_request"]["state"] = "closed"
    elif tamper == "wrong_pr":
        event["pull_request"]["number"] = 220
    elif tamper == "revoked":
        event["review"]["body"] = gate.approval_text(219, "a"*40, "b"*40, revoke=True)
    elif tamper == "review_approved":
        event["review"]["state"] = "approved"
    elif tamper == "edited_review":
        event["action"] = "edited"
    elif tamper == "missing_review_commit":
        event["review"].pop("commit_id")
    elif tamper == "wrong_review_state":
        event["review"]["state"] = "dismissed"
    fake = FakeGitHub(gate, ci="completed")
    assert gate.candidates(fake, fake.repo, event) == []
    assert gate.process(fake, fake.repo, event) == []
    assert not fake.writes


def test_review_wake_without_prior_conversation_pass_cannot_merge(gate):
    fake = FakeGitHub(gate, ci="completed", approval=False)
    outcome = gate.process(fake, fake.repo, _approval_event(gate))
    assert outcome[0]["status"] == "HOLD"
    assert outcome[0]["reason"] == "NO_ACTIVE_BRAIN_PASS"
    assert not fake.writes

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



def test_new_component_onboarding_end_to_end_complete_windows_junit_can_merge(gate, monkeypatch):
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
            os_name = {1000: "windows-latest"}[artifact_id]
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
                "platform": "Windows",
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
    # Match the current independently reviewed MT-01 consumer closure, not
    # the historical single-line tuple. Only the new fixture source may change.
    consumers = ('(eng + "runner.py", rem + "worker.py", "scripts/HostOps-V4-InstallPlan.ps1",\n'
                 '                   "config/hostops-v3/config-v4.example.json"))')
    old_mt01 = '(eng + "minimum_test.py", eng + "verification.py"),\n                  ' + consumers
    new_mt01 = '(eng + "minimum_test.py", eng + "verification.py", eng + "gate_mt01_probe.py"),\n                  ' + consumers
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

def test_noise_cleanup_and_issue_router_are_independently_covered(gate):
    """Keep trusted merge-gate witnesses independent of candidate MT01 map."""
    expected = {
        ".github/workflows/issue-ingress.yml": {
            "tests/test_ef02.py", "tests/test_hostops_personal.py",
            "tests/test_mt01_v21.py",
        },
        ".github/workflows/actions-skipped-cleanup.yml": {
            "tests/test_pr_merge_gate.py", "tests/test_mt01_v21.py",
        },
        ".github/workflows/hostops-readonly.yml": {
            "tests/test_hostops_request.py", "tests/test_mt01_v21.py",
        },
        ".github/workflows/hostops-v2.yml": {
            "tests/test_hostops_v2.py", "tests/test_hostops_ef02_202_recovery.py",
            "tests/test_mt01_v21.py",
        },
    }
    # Historical mappings remain reviewed solely for removal and rollback.
    # None of these legacy workflow YAML files is present in the release.
    for retired in ("hostops-readonly.yml", "hostops-v2.yml",
                    "hostops-v3.yml", "hostops-ci.yml", "hostops-v2-ci.yml"):
        path = ".github/workflows/" + retired
        assert path in gate.REVIEWED_CONTROL_TESTS
        assert not (ROOT / path).exists()
    for path, required_tests in expected.items():
        assert set(gate.REVIEWED_CONTROL_TESTS[path]) == required_tests
        paths, witness = gate.reviewed_coverage_for_changed_files([
            {"filename": path, "status": "modified"}
        ])
        assert paths == (path,)
        assert required_tests <= set(witness)
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": ".github/workflows/unsafe-not-reviewed.yml", "status": "added"}
    ]) is None


def test_all_active_ci_workflows_are_windows_only():
    workflows = sorted((ROOT / '.github' / 'workflows').glob('*.yml'))
    assert workflows
    for path in workflows:
        source = path.read_text(encoding='utf-8')
        assert 'ubuntu-latest' not in source, path
        assert 'shell: bash' not in source, path
        assert 'matrix.os' not in source, path



def test_repository_wide_windows_only_audit():
    """Audit ALL tracked text files, not just the CI workflow directory.

    Historical audit records remain intact. Only live Windows CI/merge
    contracts may gate the PR; archived Ubuntu evidence is informational.
    """
    import json
    import re
    import subprocess

    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    tracked = [p.decode("utf-8") for p in output.split(b"\x00") if p]
    assert len(tracked) >= 200, "INCOMPLETE_TRACKED_TREE"

    active_workflows = 0
    scanned_text = 0
    skipped_binary = 0
    blocking = []
    code_references = []
    historical_references = []
    marker = re.compile(
        r"ubuntu(?:[-_ ]latest)?|\blinux\b|both[_ -]?os|dual[_ -]?os|"
        r"\bapt-get\b|shell\s*:\s*bash|two[-_ ]os", re.IGNORECASE
    )
    forbidden_workflow = re.compile(
        r"ubuntu-latest|macos-latest|linux-latest|two[-_ ]os|"
        r"shell\s*:\s*bash|\bapt-get\b|runs-on:\s*(?:ubuntu|linux|macos)|"
        r"\$\{\{\s*matrix\.os\s*\}\}", re.IGNORECASE
    )
    forbidden_control = re.compile(
        r"tests\s*\(\s*ubuntu-latest\s*\)|"
        r"EXACT_HEAD_BOTH_OS|NO_BOTH_OS|BOTH_OS|two[-_ ]os",
        re.IGNORECASE
    )
    binary_extensions = {
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".svgz",
        ".docx", ".pdf", ".zip", ".7z", ".gz", ".mp4", ".mp3",
        ".woff", ".woff2", ".ttf", ".otf", ".exe", ".dll",
    }

    for name in tracked:
        file = ROOT / name
        if not file.is_file():
            continue
        if file.suffix.lower() in binary_extensions:
            skipped_binary += 1
            continue
        data = file.read_bytes()
        if b"\x00" in data[:4096]:
            skipped_binary += 1
            continue
        try:
            contents = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped_binary += 1
            continue
        scanned_text += 1
        workflow = name.startswith(".github/workflows/") and name.endswith((".yml", ".yaml"))
        operational = (
            name.startswith((".github/scripts/", "src/", "scripts/", "config/"))
            or name in {"BOOTSTRAP_GITHUB.ps1", "pyproject.toml"}
        )
        if workflow:
            active_workflows += 1
        runner = ""
        for i, line in enumerate(contents.splitlines(), 1):
            text = line.strip()
            if workflow and "runs-on:" in line:
                runner = text
            if workflow and forbidden_workflow.search(line):
                blocking.append(f"{name}:{i}:{text[:160]}")
            elif operational and forbidden_control.search(line):
                blocking.append(f"{name}:{i}:{text[:160]}")
            if workflow and runner == "runs-on: windows-latest" and re.search(
                r"(?:^|\s)-n\s+12(?:\s|$)", line
            ):
                blocking.append(f"{name}:{i}:HOSTED_12_WORKERS")
            if marker.search(line):
                label = f"{name}:{i}:{text[:160]}"
                if name.startswith(("docs/", "work_items/", "examples/", "contracts/")) or name == "README.md":
                    historical_references.append(label)
                elif not workflow:
                    code_references.append(label)

    report = {
        "schema": "AI_COMMERCE_WINDOWS_ONLY_REPO_AUDIT_V1",
        "tracked_files": len(tracked),
        "scanned_text_files": scanned_text,
        "skipped_binary_files": skipped_binary,
        "active_workflow_files": active_workflows,
        "blocking_count": len(blocking),
        "blocking": blocking[:50],
        "code_reference_count": len(code_references),
        "code_reference_samples": code_references[:40],
        "historical_reference_count": len(historical_references),
        "historical_reference_samples": historical_references[:30],
    }
    print("WINDOWS_ONLY_REPO_AUDIT_V1=" + json.dumps(report, ensure_ascii=False))
    # Retiring obsolete workflows must not fail a hardcoded file-count floor.
    # Require the actual reviewed routing/CI contracts instead.
    essential = {
        ".github/workflows/ef02-tests.yml",
        ".github/workflows/ef02-batch.yml",
        ".github/workflows/issue-ingress.yml",
        ".github/workflows/brain-pass-auto-merge.yml",
        ".github/workflows/actions-skipped-cleanup.yml",
    }
    existing = {p for p in tracked if p.startswith(".github/workflows/")
                and p.endswith((".yml", ".yaml")) and (ROOT / p).is_file()}
    missing = sorted(essential - existing)
    assert not missing, "ESSENTIAL_WORKFLOW_MISSING: " + repr(missing)
    assert active_workflows >= len(essential), "ACTIVE_WORKFLOW_INVENTORY_INCOMPLETE"
    assert not blocking, "WINDOWS_ONLY_ACTIVE_DEPENDENCY_REMAINS: " + repr(blocking[:20])

def test_d5_attended_cdp_script_has_exact_protected_main_review_owner(gate):
    """Review only the one Human-attended no-Send script, not arbitrary .ps1."""
    exact = "scripts/AI-Commerce-D5-Attended-CDP.ps1"
    required = {
        "tests/test_ef02_d5_chrome_acquisition.py",
        "tests/test_minimum_test.py", "tests/test_mt01_v21.py",
        "tests/test_pr_merge_gate.py",
    }
    assert set(gate.REVIEWED_CONTROL_TESTS[exact]) == required
    reviewed = gate.reviewed_coverage_for_changed_files(
        [{"filename": exact, "status": "added"}])
    assert reviewed is not None
    paths, witness = reviewed
    assert paths == (exact,)
    assert required <= set(witness)
    # An unrelated operator shell script cannot piggyback on this contract.
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": "scripts/AI-Commerce-D5-Arbitrary-Execute.ps1", "status": "added"}
    ]) is None
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": exact, "status": "added"},
        {"filename": "scripts/AI-Commerce-D5-Arbitrary-Execute.ps1", "status": "added"},
    ]) is None


def test_d5_operator_bridge_script_has_independent_protected_main_review_owner(gate):
    """Source-only D5 adapter gets exact, narrow independent review coverage."""
    exact = "scripts/AI-Commerce-D5-Operator-Bridge.ps1"
    required = {
        "tests/test_hostops_d5_control.py",
        "tests/test_ef02_d5_chrome_acquisition.py",
        "tests/test_hostops_personal.py",
        "tests/test_hostops_v3.py",
    }
    assert set(gate.REVIEWED_CONTROL_TESTS[exact]) == required
    reviewed = gate.reviewed_coverage_for_changed_files([
        {"filename": exact, "status": "added"},
    ])
    assert reviewed is not None
    paths, tests = reviewed
    assert paths == (exact,)
    assert required <= set(tests)
    # No wildcard privilege for other scripts, chrome actions or Wake sends.
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": "scripts/AI-Commerce-D5-Unreviewed-Launch.ps1", "status": "added"},
    ]) is None
    assert gate.reviewed_coverage_for_changed_files([
        {"filename": exact, "status": "added"},
        {"filename": "scripts/AI-Commerce-D5-Unreviewed-Launch.ps1", "status": "added"},
    ]) is None


class _AttemptScopedGitHub(FakeGitHub):
    """GitHub's generic jobs snapshot is stale; exact-attempt jobs are final."""

    def __init__(self, g, *, nodes_ok=True, bad_identity=False,
                 same_attempt_duplicate=False, no_current_artifact=False):
        super().__init__(g, ci="completed")
        self.nodes_ok = nodes_ok
        self.bad_identity = bad_identity
        self.same_attempt_duplicate = same_attempt_duplicate
        self.no_current_artifact = no_current_artifact
        self.read_ids = []

    def read_artifact(self, artifact_id):
        self.read_ids.append(artifact_id)
        return super().read_artifact(artifact_id)

    def __call__(self, path, method="GET", body=None):
        root = f"/repos/{self.repo}/actions/runs/200"
        if path.startswith(f"/repos/{self.repo}/actions/runs?"):
            data = super().__call__(path, method, body)
            data["workflow_runs"][0]["run_attempt"] = 2
            return data
        if path == root + "/attempts/2":
            return {
                "id": 999 if self.bad_identity else 200,
                "run_attempt": 2, "head_sha": self.head,
                "status": "completed", "conclusion": "success",
                "run_started_at": "2026-11-02T01:00:00Z",
                "updated_at": "2026-11-02T01:05:00Z",
            }
        if path == root + "/attempts/2/jobs?per_page=100":
            jobs = super().__call__(root + "/jobs?per_page=100", method, body)
            if not self.nodes_ok:
                jobs["jobs"][0]["steps"][-1]["conclusion"] = "skipped"
            return jobs
        if path == root + "/jobs?per_page=100":
            # Intentionally unreliable REST snapshot, as in real PR #651.
            jobs = super().__call__(path, method, body)
            jobs["jobs"][0]["steps"][-1]["conclusion"] = None
            return jobs
        if path == root + "/artifacts?per_page=100":
            old = {"id": 1000, "name": "mt01-v21-windows-latest-200",
                   "expired": False, "size_in_bytes": 1024,
                   "created_at": "2026-11-02T00:30:00Z",
                   "workflow_run": {"id": 200, "head_sha": self.head}}
            current = {"id": 1001, "name": "mt01-v21-windows-latest-200",
                       "expired": False, "size_in_bytes": 1024,
                       "created_at": "2026-11-02T01:03:00Z",
                       "workflow_run": {"id": 200, "head_sha": self.head}}
            if self.same_attempt_duplicate:
                duplicate = dict(current, id=1002, created_at="2026-11-02T01:04:00Z")
                return {"artifacts": [old, current, duplicate]}
            return {"artifacts": [old] if self.no_current_artifact else [old, current]}
        return super().__call__(path, method, body)


def test_merge_gate_uses_exact_attempt_jobs_and_artifact_after_rerun(gate):
    api = _AttemptScopedGitHub(gate)
    verdict = gate.verify_one(api, api.repo, api.pr)
    assert (verdict.status, verdict.reason) == (
        "READY_FOR_MERGE", "EXACT_HEAD_WINDOWS_CI_VERIFIED")
    # A prior attempt's identically named ZIP cannot stand in for this attempt.
    assert api.read_ids == [1001]


def test_merge_gate_latest_attempt_skipped_node_remains_blocked(gate):
    api = _AttemptScopedGitHub(gate, nodes_ok=False)
    verdict = gate.verify_one(api, api.repo, api.pr)
    assert (verdict.status, verdict.reason) == (
        "WAIT_EVIDENCE", "WINDOWS_PYTEST_NODE_PROOF_MISSING")
    assert api.read_ids == []


def test_merge_gate_latest_attempt_identity_mismatch_remains_blocked(gate):
    api = _AttemptScopedGitHub(gate, bad_identity=True)
    verdict = gate.verify_one(api, api.repo, api.pr)
    assert (verdict.status, verdict.reason) == (
        "WAIT_EVIDENCE", "CI_ATTEMPT_IDENTITY_UNPROVEN")
    assert api.read_ids == []


def test_merge_gate_duplicate_or_missing_same_attempt_artifact_remains_blocked(gate):
    for options in ({"same_attempt_duplicate": True}, {"no_current_artifact": True}):
        api = _AttemptScopedGitHub(gate, **options)
        verdict = gate.verify_one(api, api.repo, api.pr)
        assert (verdict.status, verdict.reason) == (
            "WAIT_EVIDENCE", "V21_EXACT_RUN_WINDOWS_ARTIFACT_PROOF_MISSING")
        assert api.read_ids == []

def test_710_g3_no_model_workflow_has_exact_protected_merge_review(gate):
    # Main review oracle is separate from candidate MT-01 impact mapping.
    path = ".github/workflows/ef02-antigravity-headless-preflight.yml"
    required = {"tests/test_ef02.py", "tests/test_ef02_scope_main_integration.py",
                "tests/test_mt01_v21.py"}
    assert set(gate.REVIEWED_CONTROL_TESTS[path]) == required
    reviewed = gate.reviewed_coverage_for_changed_files(
        [{"filename": path, "status": "modified"}])
    assert reviewed is not None
    changed, witness = reviewed
    assert changed == (path,)
    assert required <= set(witness)
    assert gate.reviewed_coverage_for_changed_files(
        [{"filename": ".github/workflows/ef02-headless-unsafe.yml",
          "status": "added"}]) is None


