"""Contract tests for the Human-approved asynchronous PR merge queue.

Mocks require no GitHub credentials or paid executor. They prove WAIT_CI does not
consume an approval, the same approval resumes automatically on a CI completion
event, and any changed HEAD or missing proof never merges.
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
    return module


class FakeGitHub:
    repo = "trung-via/ai-commerce"
    pr = 219
    head = "a" * 40
    base = "b" * 40

    def __init__(self, g, *, ci="in_progress", changed_head=False,
                 changed_base=False, jobs_ok=True, approval=True,
                 revoked=False, latest_failed=False, receipt_ok=True,
                 tampered_artifact=False):
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
                "observed_cases": ["tests.test_minimum_test::test_ok"],
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
    assert gate.parse_approval("AI_COMMERCE_HUMAN_MERGE_APPROVAL_V1\npr=219") is None
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
    ({"approval": False, "ci": "completed"}, "HOLD", "NO_ACTIVE_OWNER_APPROVAL"),
    ({"revoked": True, "ci": "completed"}, "HOLD", "NO_ACTIVE_OWNER_APPROVAL"),
    ({"changed_head": True, "ci": "completed"}, "HOLD",
     "APPROVAL_BOUND_TO_DIFFERENT_REVISION"),
    ({"changed_base": True, "ci": "completed"}, "HOLD",
     "APPROVAL_BOUND_TO_DIFFERENT_REVISION"),
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
