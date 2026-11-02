"""AI Commerce automated merge gate for a pinned, owner-backed Brain PASS.

This runs from protected main, not candidate-controlled PR code. A canonical
AI Commerce Brain PASS decision must have been emitted to GitHub through the
owner's authenticated connector after actual review; no generic CI success
or free-form 'PASS' text authorizes the merge.
The GitHub identity authenticates the emitter, not the underlying model:
model provenance is an explicit limitation of owner-backed review comments.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# This module is loaded by filename during auto-merge unit tests as well as CI.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mt01_v21_proof import validate_receipt_zip

# Brain PASS auto-merge workflow checks out protected main. Load only its reviewed registry source:
# no package __init__, pip dependencies or PR-controlled Python imports.
import importlib.util

def approved_groups():
    trusted_path = (Path(__file__).resolve().parents[2] /
                    "src/ai_commerce/engineering/minimum_test.py")
    spec = importlib.util.spec_from_file_location("_mt01_main_trusted_registry", trusted_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("TRUSTED_MAIN_REGISTRY_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.approved_groups()

SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
APPROVAL = "AI_COMMERCE_BRAIN_REVIEW_PASS_V1"
REVOKE = "AI_COMMERCE_BRAIN_REVIEW_HOLD_V1"
CI_WORKFLOW = ".github/workflows/ef02-tests.yml"
PYTEST_STEP = "MT-01 PR CI selective tests (full fallback)"
NODE_STEP = "Run node --test tests/ef02-watch.test.cjs tests/ef02-reconcile.test.cjs"
RECEIPT_STEP = "MT-01 V2.1 upload verification receipt"
ORACLE_STEP = "MT-01 baseline closure oracle"

# Conservative independently authored reviewed control closure. This table is
# separate from impact_v21.select_impact and from the candidate receipt. The
# default-branch merge-queue script uses GitHub's PR changed-file endpoint.
# Any unreviewed control or rename fails closed instead of accepting a claim.
REVIEWED_CONTROL_TESTS = {
    ".github/ef02/verify-profiles.json": (
        "tests/test_minimum_test.py", "tests/test_ef02_mt01_registry.py"),
    ".github/scripts/ef02-pr-ci-tests.py": (
        "tests/test_minimum_test.py", "tests/test_ef02_mt01_registry.py"),
    ".github/scripts/mt01_v21_proof.py": (
        "tests/test_pr_merge_gate.py", "tests/test_mt01_v21.py"),
    ".github/scripts/mt01_v21_oracle.py": ("tests/test_mt01_v21.py",),
    ".github/scripts/pr-merge-gate.py": (
        "tests/test_pr_merge_gate.py", "tests/test_minimum_test.py"),
    ".github/workflows/brain-pass-auto-merge.yml": ("tests/test_pr_merge_gate.py",),
    # Retired workflow must remain recognized for this one deletion diff.
    ".github/workflows/human-merge-queue.yml": ("tests/test_pr_merge_gate.py",),
    ".github/workflows/ef02-batch.yml": (
        "tests/test_ef02.py", "tests/test_minimum_test.py"),
    ".github/workflows/ef02-tests.yml": (
        "tests/test_minimum_test.py", "tests/test_pr_merge_gate.py"),
    "src/ai_commerce/engineering/impact_v21.py": (
        "tests/test_mt01_v21.py", "tests/test_minimum_test.py"),
    "src/ai_commerce/engineering/minimum_test.py": (
        "tests/test_minimum_test.py", "tests/test_ef02.py",
        "tests/test_ef02_mt01_registry.py"),
    "src/ai_commerce/remote/validation.py": (
        "tests/test_minimum_test.py", "tests/test_ef02.py"),
    "tests/test_mt01_v21.py": ("tests/test_mt01_v21.py",),
    "tests/test_pr_merge_gate.py": ("tests/test_pr_merge_gate.py",),
    # The merge gate also requires the real Node step PASS on both OS.
    "tests/ef02-watch.test.cjs": (),
    "tests/ef02-reconcile.test.cjs": (),
    ".github/scripts/ef02-watch.cjs": (),
    ".github/scripts/ef02-reconcile.cjs": (),
}


def reviewed_coverage_for_changed_files(files: object):
    """Return authoritative changed paths + minimal reviewed control witness.

    This is a conservative control-file policy, not yet a global trusted
    consumer-graph oracle for arbitrary new product components.
    """
    if not isinstance(files, list) or not files or len(files) >= 100:
        return None
    changed = set()
    required = set()
    for entry in files:
        if not isinstance(entry, dict):
            return None
        name, status = entry.get("filename"), entry.get("status")
        if (not isinstance(name, str) or not name or name.startswith("/") or
            "\\" in name or ".." in name.split("/") or
            status not in ("added", "modified", "removed", "changed")):
            return None
        changed.add(name)
        if name in REVIEWED_CONTROL_TESTS:
            required.update(REVIEWED_CONTROL_TESTS[name])
        elif name.startswith(".github/"):
            return None
        elif name.startswith("tests/test_") and name.endswith(".py"):
            required.add(name)
    if len(changed) != len(files):
        return None
    # The merge gate's main-tree owner map is independent of candidate plans.
    # Existing components inherit full reviewed owner/consumer obligations.
    groups = approved_groups()
    for path in changed:
        owning = [g for g in groups if path in
                  (set(g.paths) | set(g.tests) | set(g.consumers) | set(g.contracts))]
        for group in owning:
            required.update(group.tests)
        if (not owning and path not in REVIEWED_CONTROL_TESTS and
                (path.startswith(("src/ai_commerce/", "contracts/", "scripts/")) or
                 path.endswith((".py", ".cjs", ".ps1", ".sh", ".ts", ".tsx", ".js")))):
            return None
    return tuple(sorted(changed)), tuple(sorted(required))




@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str
    pr: int | None = None
    head: str = ""
    ci_run: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason,
                "pr": self.pr, "head_sha": self.head, "ci_run": self.ci_run}


def approval_text(pr: int, head: str, base: str, *, revoke: bool = False) -> str:
    if type(pr) is not int or pr <= 0 or not SHA.fullmatch(head) or not SHA.fullmatch(base):
        raise ValueError("INVALID_APPROVAL_BINDING")
    directive = REVOKE if revoke else APPROVAL
    return f"{directive}\npr={pr}\nhead_sha={head}\nbase_sha={base}"


def parse_approval(text: object) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    lines = text.strip().splitlines()
    if len(lines) != 4 or lines[0] not in (APPROVAL, REVOKE):
        return None
    try:
        pr = int(lines[1].removeprefix("pr="))
        head = lines[2].removeprefix("head_sha=")
        base = lines[3].removeprefix("base_sha=")
        if text.strip() != approval_text(pr, head, base, revoke=lines[0] == REVOKE):
            return None
    except ValueError:
        return None
    return {"pr": pr, "head": head, "base": base, "revoke": lines[0] == REVOKE}


def owner_directive(comments: list, owner: str) -> dict | None:
    """Latest owner-backed Brain decision wins. No old Human token accepted."""
    directives = []
    for item in comments:
        if (not isinstance(item, dict) or not isinstance(item.get("user"), dict) or
                item["user"].get("login") != owner):
            continue
        if item.get("author_association") != "OWNER":
            continue
        parsed = parse_approval(item.get("body"))
        if parsed and type(item.get("id")) is int:
            directives.append((item["id"], parsed))
    return max(directives, key=lambda pair: pair[0])[1] if directives else None


def latest_exact_ci(runs: list, head: str) -> dict | None:
    valid = [r for r in runs if isinstance(r, dict) and
             r.get("event") == "pull_request" and r.get("head_sha") == head and
             str(r.get("path", "")).split("@")[0] == CI_WORKFLOW and
             type(r.get("id")) is int]
    return max(valid, key=lambda r: (str(r.get("created_at") or ""), r["id"])) if valid else None


def verified_jobs(jobs: list) -> bool:
    """A successful wrapper or skipped pytest is not proof of verified code."""
    if not isinstance(jobs, list):
        return False
    needed = {"tests (ubuntu-latest)", "tests (windows-latest)"}
    observed = set()
    for job in jobs:
        if not isinstance(job, dict) or job.get("name") not in needed:
            continue
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        steps_by_name = {s.get("name"): s.get("conclusion") for s in steps
                         if isinstance(s, dict) and isinstance(s.get("name"), str)}
        if (steps_by_name.get(PYTEST_STEP) == "success" and
                steps_by_name.get(NODE_STEP) == "success" and
                steps_by_name.get(RECEIPT_STEP) == "success" and
                steps_by_name.get(ORACLE_STEP) == "success"):
            observed.add(job["name"])
    return observed == needed


def verified_v21_artifacts(value: object, run_id: int, expected_head: str) -> bool:
    """Ensure both operating systems uploaded authentic GitHub run metadata.

    The GitHub metadata is externally attestable; candidate-supplied receipt
    bytes require a separate independent coverage oracle before final rollout.
    """
    if not isinstance(value, dict) or not isinstance(value.get("artifacts"), list):
        return False
    artifacts = value["artifacts"]
    if len(artifacts) >= 100 or any(not isinstance(a, dict) for a in artifacts):
        return False
    for os_name in ("ubuntu-latest", "windows-latest"):
        matches = [a for a in artifacts if a.get("name") == f"mt01-v21-{os_name}-{run_id}"]
        if len(matches) != 1:
            return False
        record = matches[0]
        provenance = record.get("workflow_run")
        if (type(record.get("id")) is not int or record["id"] < 1 or
                record.get("expired") is not False or
                type(record.get("size_in_bytes")) is not int or
                record["size_in_bytes"] <= 100 or not isinstance(provenance, dict) or
                provenance.get("id") != run_id or provenance.get("head_sha") != expected_head):
            return False
    return True


def verify_one(api: Callable[[str], Any], repository: str, number: int,
               *, event_head: str | None = None) -> Verdict:
    """Read-only preflight. All missing or stale evidence holds the merge."""
    owner = repository.partition("/")[0]
    base = f"/repos/{repository}"
    pr = api(f"{base}/pulls/{number}")
    if not isinstance(pr, dict) or pr.get("state") != "open" or pr.get("merged") or pr.get("draft"):
        return Verdict("WAIT_REVIEW", "PR_NOT_OPEN_AND_READY", number)
    if not isinstance(pr.get("number"), int) or pr["number"] != number:
        return Verdict("HOLD", "PR_ID_MISMATCH", number)
    head = pr.get("head", {}).get("sha")
    base_sha = pr.get("base", {}).get("sha")
    if (not isinstance(head, str) or not SHA.fullmatch(head) or
            not isinstance(base_sha, str) or not SHA.fullmatch(base_sha) or
            pr.get("base", {}).get("ref") != "main"):
        return Verdict("HOLD", "PR_HEAD_OR_BASE_INVALID", number)
    if event_head and event_head != head:
        return Verdict("HOLD", "EVENT_HEAD_MISMATCH", number, head)

    # Only a canonical Brain PASS issued by authenticated owner GitHub access
    # binds this exact PR, HEAD and current base; prose/CI alone never passes.
    comments = api(f"{base}/issues/{number}/comments?per_page=100")
    if not isinstance(comments, list) or len(comments) == 100:
        return Verdict("HOLD", "BRAIN_DECISION_COMMENTS_UNPROVEN", number, head)
    approval = owner_directive(comments, owner)
    if not approval or approval["revoke"]:
        return Verdict("HOLD", "NO_ACTIVE_BRAIN_PASS", number, head)
    if (approval["pr"], approval["head"], approval["base"]) != (number, head, base_sha):
        return Verdict("HOLD", "BRAIN_PASS_BOUND_TO_DIFFERENT_REVISION", number, head)

    live = api(f"{base}/branches/main")
    if live.get("commit", {}).get("sha") != base_sha:
        return Verdict("WAIT_REBASE", "BASE_MAIN_CHANGED", number, head)
    if pr.get("mergeable") is not True:
        return Verdict("WAIT_MERGEABILITY", "PR_NOT_MERGEABLE", number, head)

    q = urllib.parse.urlencode({"event": "pull_request", "head_sha": head, "per_page": 100})
    obj = api(f"{base}/actions/runs?{q}")
    if not isinstance(obj, dict) or not isinstance(obj.get("workflow_runs"), list) or len(obj["workflow_runs"]) == 100:
        return Verdict("HOLD", "CI_RUN_LIST_UNPROVEN", number, head)
    latest = latest_exact_ci(obj["workflow_runs"], head)
    if latest is None:
        return Verdict("WAIT_CI", "NO_EXACT_HEAD_PR_CI", number, head)
    if latest.get("status") != "completed":
        return Verdict("WAIT_CI", "EXACT_HEAD_CI_STILL_RUNNING", number, head, latest["id"])
    if latest.get("conclusion") != "success":
        return Verdict("WAIT_FIX", "EXACT_HEAD_CI_NOT_SUCCESS", number, head, latest["id"])
    jobs = api(f"{base}/actions/runs/{latest['id']}/jobs?per_page=100")
    if not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list) or len(jobs["jobs"]) == 100:
        return Verdict("HOLD", "CI_JOBS_UNPROVEN", number, head, latest["id"])
    if not verified_jobs(jobs["jobs"]):
        return Verdict("WAIT_EVIDENCE", "BOTH_OS_PYTEST_NODE_PROOF_MISSING", number, head, latest["id"])
    artifact_proof = api(f"{base}/actions/runs/{latest['id']}/artifacts?per_page=100")
    if not verified_v21_artifacts(artifact_proof, latest["id"], head):
        return Verdict("WAIT_EVIDENCE", "V21_EXACT_RUN_OS_ARTIFACT_PROOF_MISSING", number, head, latest["id"])
    # The GitHub diff is an external, exact-PR source independent of the
    # candidate-created impact plan and its recorded changed_paths.
    changed_files = api(f"{base}/pulls/{number}/files?per_page=100")
    reviewed = reviewed_coverage_for_changed_files(changed_files)
    if reviewed is None:
        return Verdict("WAIT_EVIDENCE", "V21_REVIEWED_CHANGED_PATH_ORACLE_MISSING", number, head, latest["id"])
    actual_changed, required_tests = reviewed
    if not callable(getattr(api, "read_artifact", None)):
        return Verdict("WAIT_EVIDENCE", "V21_ARTIFACT_CONTENT_READER_MISSING", number, head, latest["id"])
    for os_name in ("ubuntu-latest", "windows-latest"):
        record = next(a for a in artifact_proof["artifacts"]
                      if a["name"] == f"mt01-v21-{os_name}-{latest['id']}")
        try:
            data = api.read_artifact(record["id"])
        except (ValueError, TypeError, OSError, TimeoutError, urllib.error.URLError):
            return Verdict("WAIT_EVIDENCE", "V21_ARTIFACT_BYTES_UNAVAILABLE", number, head, latest["id"])
        if not validate_receipt_zip(data, expected_head=head, expected_base=base_sha,
                                    os_name=os_name,
                                    expected_changed_paths=actual_changed,
                                    required_test_files=required_tests):
            return Verdict("WAIT_EVIDENCE", "V21_CONTENT_PROOF_INVALID", number, head, latest["id"])
    return Verdict("READY_FOR_MERGE", "EXACT_HEAD_BOTH_OS_CI_VERIFIED", number, head, latest["id"])


class GitHubAPI:
    def __init__(self, repository: str, token: str):
        if not REPO.fullmatch(repository) or not token:
            raise ValueError("MERGE_GATE_REPOSITORY_OR_TOKEN_MISSING")
        self.repository = repository
        self.token = token

    def read_artifact(self, artifact_id: int) -> bytes:
        """Download the exact GitHub-hosted ZIP through a bounded signed redirect."""
        if type(artifact_id) is not int or artifact_id < 1:
            raise ValueError("INVALID_ARTIFACT_ID")
        path = f"/repos/{self.repository}/actions/artifacts/{artifact_id}/zip"
        req = urllib.request.Request(
            "https://api.github.com" + path,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(req, timeout=18) as response:
            content = response.read(24 * 1024 * 1024 + 1)
        if len(content) > 24 * 1024 * 1024:
            raise ValueError("ARTIFACT_TOO_LARGE")
        return content

    def __call__(self, path: str, method: str = "GET", body: dict | None = None) -> Any:
        if not path.startswith(f"/repos/{self.repository}/"):
            raise ValueError("CROSS_REPOSITORY_API_DENIED")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            "https://api.github.com" + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.load(response)


def candidates(api: Callable, repo: str, event: dict) -> list[tuple[int, str | None]]:
    """Only a canonical Brain PASS/HOLD comment or matching PR CI completion."""
    if "comment" in event and "issue" in event:
        c = event["comment"]
        issue = event["issue"]
        if (not issue.get("pull_request") or not isinstance(c.get("user"), dict) or
                c["user"].get("login") != repo.split("/")[0]
                or c.get("author_association") != "OWNER" or parse_approval(c.get("body")) is None):
            return []
        number = issue.get("number")
        directive = parse_approval(c["body"])
        return [(number, None)] if type(number) is int and directive["pr"] == number else []
    run = event.get("workflow_run")
    if not isinstance(run, dict) or run.get("name") != "EF02 source tests" or run.get("event") != "pull_request":
        return []
    if run.get("status") != "completed":
        return []
    head = run.get("head_sha")
    if not isinstance(head, str) or not SHA.fullmatch(head):
        return []
    prs = api(f"/repos/{repo}/pulls?state=open&per_page=100")
    if not isinstance(prs, list) or len(prs) == 100:
        return []
    return [(p["number"], head) for p in prs if isinstance(p, dict) and
            type(p.get("number")) is int and p.get("head", {}).get("sha") == head]


def process(api: GitHubAPI, repository: str, event: dict) -> list[dict]:
    results: list[dict] = []
    for number, event_head in candidates(api, repository, event):
        try:
            decision = verify_one(api, repository, number, event_head=event_head)
            outcome = decision.as_dict()
            if decision.status == "READY_FOR_MERGE":
                # Recheck source SHA and main before write. The same Brain PASS
                # persists through WAIT_CI -> PASS only for unchanged HEAD/base.
                fresh = api(f"/repos/{repository}/pulls/{number}")
                current_main = api(f"/repos/{repository}/branches/main")
                if (fresh.get("head", {}).get("sha") != decision.head or
                    fresh.get("base", {}).get("sha") != current_main.get("commit", {}).get("sha") or
                    fresh.get("mergeable") is not True or fresh.get("draft") or
                    fresh.get("state") != "open"):
                    outcome["status"] = "WAIT_RECHECK"
                    outcome["reason"] = "PR_CHANGED_DURING_MERGE"
                    results.append(outcome)
                    continue
                # The GitHub merge endpoint's SHA compare prevents PR HEAD races.
                # A second worker is idempotently refused after the first merge.
                result = api(f"/repos/{repository}/pulls/{number}/merge", method="PUT",
                             body={"sha": decision.head, "merge_method": "merge"})
                outcome["status"] = "MERGED" if result.get("merged") is True else "HOLD"
                outcome["reason"] = "GITHUB_MERGED_EXACT_BRAIN_PASSED_HEAD" if result.get("merged") is True else "GITHUB_MERGE_DENIED"
                outcome["merge_sha"] = result.get("sha") if result.get("merged") else None
            results.append(outcome)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError,
                TypeError, KeyError, AttributeError) as exc:
            # Fail closed; expose class only, not provider response/token.
            results.append({"status": "HOLD", "reason": "GITHUB_PROOF_OR_MERGE_ERROR_" + type(exc).__name__,
                            "pr": number})
    return results


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    try:
        api = GitHubAPI(repo, token)
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        event = json.loads(event_path.read_text(encoding="utf-8"))
        decisions = process(api, repo, event)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "HOLD", "reason": "MERGE_GATE_INPUT_UNPROVEN_" + type(exc).__name__}))
        return 1
    for decision in decisions:
        print("AI_COMMERCE_MERGE_GATE=" + json.dumps(decision, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
