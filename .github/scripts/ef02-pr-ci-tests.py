"""EF02 PR CI: minimum sufficient MT-01 tests; fail closed to full suite.

Reuse approved MT-01 consumer/contract/import closure. Non-PR runs, incomplete
mapping, unknown changes and selector failures run the historical full suite.
No cache, user-supplied shell commands, tokens or pytest worker overrides.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
FULL = ("tests",)
SHA = re.compile(r"[0-9a-f]{40}\Z")
# Existing registry-control test is intentionally outside approved MT-01
# groups. Changes to it must trigger full verification.
KNOWN_UNMAPPED = frozenset({"tests/test_ef02_mt01_registry.py"})
REGISTRY = ".github/ef02/verify-profiles.json"
MT01_ENGINE = "src/ai_commerce/engineering/minimum_test.py"
MAPPED_GITHUB_CONTROLS = frozenset()  # Candidate/approval controls always require broad CI.
MT01_REPORT = "docs/engineering/MT01_COMPONENT_AWARE_IMPLEMENTATION_REPORT.md"
REVIEWED_REGISTRY_REGRESSIONS = ("tests/test_ef02_mt01_registry.py", "tests/test_minimum_test.py")
HOSTED_WORKERS = (0, 2, 4)


def additive_registry_tests(before: object, after: object, groups: tuple) -> tuple[str, ...] | None:
    """Prove additive reviewed profile entries OR test-only extensions.

    For existing profiles, the old tests must retain order and the candidate
    command must exactly match the CURRENT reviewed MT-01 group. Any altered
    required argv, deletions, unknown names or unreviewed tests fail closed.
    This does not authorize narrowing a PR that edits the MT-01 selector.
    """
    if not isinstance(before, dict) or not before or not isinstance(after, dict):
        return None
    if not set(before).issubset(after) or before == after:
        return None
    reviewed = {
        f"mt01-{group.name}-tests": (
            ["python", "-m", "pytest", *group.tests, "-q", "-n", "12"],
            tuple(group.tests),
        )
        for group in groups
    }
    selected = set(REVIEWED_REGISTRY_REGRESSIONS)
    changed = False
    for name in sorted(after):
        new_command = after[name]
        if name in before and before[name] == new_command:
            continue
        if not isinstance(name, str) or not isinstance(new_command, list):
            return None
        approved = reviewed.get(name)
        if approved is None or new_command != approved[0]:
            return None
        reviewed_tests = approved[1]
        if (not reviewed_tests or len(set(reviewed_tests)) != len(reviewed_tests)
                or any(not t.startswith("tests/test_") or not t.endswith(".py")
                       or ".." in t.split("/") for t in reviewed_tests)):
            return None
        if name in before:
            old = before[name]
            prefix = ["python", "-m", "pytest"]
            suffix = ["-q", "-n", "12"]
            if (not isinstance(old, list) or len(old) <= len(prefix) + len(suffix)
                    or old[:3] != prefix or old[-3:] != suffix):
                return None
            old_tests = old[3:-3]
            if (not all(isinstance(t, str) and t.startswith("tests/test_")
                        and t.endswith(".py") for t in old_tests)
                    or len(set(old_tests)) != len(old_tests)
                    or len(old_tests) >= len(reviewed_tests)):
                return None
            # A subsequence permits insertion of new reviewed tests without
            # moving/removing any previous test or changing its relative order.
            cursor = iter(reviewed_tests)
            if not all(any(candidate == t for candidate in cursor)
                       for t in old_tests):
                return None
        selected.update(reviewed_tests)
        changed = True
    return tuple(sorted(selected)) if changed else None


def registry_extension_tests(root: Path, base: str) -> tuple[str, ...] | None:
    """Read exact base/candidate bytes via local Direct Git, without API round trips."""
    from ai_commerce.engineering import minimum_test as mt
    old = subprocess.check_output(
        ["git", "-C", str(root), "show", f"{base}:{REGISTRY}"],
        stderr=subprocess.DEVNULL, timeout=20,
    )
    before = json.loads(old)
    after = json.loads((root / REGISTRY).read_text(encoding="utf-8"))
    return additive_registry_tests(before, after, mt.approved_groups())


def pytest_command(files: tuple[str, ...], workers: int) -> tuple[str, ...]:
    """Hosted CI only. The self-hosted EF02 -n12 profiles are unchanged."""
    if type(workers) is not int or workers not in HOSTED_WORKERS:
        raise ValueError("UNAPPROVED_HOSTED_WORKER_COUNT")
    return (sys.executable, "-m", "pytest", *files, "-q",
            *(("-n", str(workers), "--dist", "loadfile") if workers else ()))


def changed_paths(root: Path, base: str) -> tuple[set[str], bool]:
    """Get both source and destination paths on rename, including deletions."""
    if not SHA.fullmatch(base):
        raise ValueError("BASE_SHA_INVALID")
    raw = subprocess.check_output(
        ["git", "-C", str(root), "diff", "--name-status", "-z", "--find-renames", base, "HEAD"],
        stderr=subprocess.DEVNULL, timeout=20,
    )
    parts = raw.decode("utf-8", errors="strict").split("\0")
    if parts[-1] != "":
        raise ValueError("DIFF_INVALID")
    parts.pop()
    result = set()
    renamed = False
    i = 0
    while i < len(parts):
        status = parts[i]
        i += 1
        if not re.fullmatch(r"[ACDMRT][0-9]*", status):
            raise ValueError("DIFF_STATUS_UNKNOWN")
        count = 2 if status.startswith(("R", "C")) else 1
        if i + count > len(parts):
            raise ValueError("DIFF_TRUNCATED")
        entries = parts[i:i + count]
        i += count
        for entry in entries:
            if (not entry or "\\" in entry or entry.startswith("/") or
                    any(part in (".", "..") for part in entry.split("/"))):
                raise ValueError("DIFF_PATH_UNSAFE")
            result.add(entry)
        renamed |= count == 2
    if not result:
        raise ValueError("DIFF_EMPTY")
    return result, renamed


def choose(root: Path, *, event: str, base: str, paths: set[str] | None = None,
           selection_fn: Callable | None = None,
           registry_tests_fn: Callable | None = None) -> tuple[tuple[str, ...], str, tuple[str, ...], tuple[str, ...]]:
    """Select actual approved MT-01 test files, or full scope on any uncertainty."""
    if event != "pull_request":
        return FULL, "FULL_PROJECT_REQUIRED", ("PUSH_OR_NON_PR_EVENT",), ()
    if not SHA.fullmatch(base):
        return FULL, "FULL_PROJECT_REQUIRED", ("MISSING_OR_INVALID_BASE_SHA",), ()
    try:
        if paths is None:
            paths, renamed = changed_paths(root, base)
            if renamed:
                return FULL, "FULL_PROJECT_REQUIRED", ("RENAMED_PATH_REQUIRES_FULL",), ()
        if not paths:
            return FULL, "FULL_PROJECT_REQUIRED", ("EMPTY_DIFF",), ()
        if MT01_ENGINE in paths:
            return FULL, "FULL_PROJECT_REQUIRED", ("MT01_ENGINE_CHANGED",), ()
        if any((p.startswith(".github/") and p not in MAPPED_GITHUB_CONTROLS | {REGISTRY})
               or p in ("pyproject.toml", "setup.py") for p in paths):
            return FULL, "FULL_PROJECT_REQUIRED", ("CI_OR_SHARED_CONFIG_CHANGED",), ()
        extra_tests: set[str] = set()
        if REGISTRY in paths:
            try:
                verified = (registry_tests_fn or registry_extension_tests)(root, base)
            except Exception:
                verified = None
            if verified is None:
                return FULL, "FULL_PROJECT_REQUIRED", ("REGISTRY_CHANGE_NOT_PROVEN_ADDITIVE",), ()
            extra_tests.update(verified)
        if MT01_REPORT in paths:
            extra_tests.add("tests/test_minimum_test.py")
        if (len(extra_tests) > 100 or any(
            not test.startswith("tests/test_") or not test.endswith(".py")
            or ".." in test.split("/") or not (root / test).is_file()
            for test in extra_tests
        )):
            return FULL, "FULL_PROJECT_REQUIRED", ("REVIEWED_CONTROL_TEST_MISSING",), ()
        mapped_paths = paths - {REGISTRY, MT01_REPORT}
        if not mapped_paths and extra_tests:
            return (tuple(sorted(extra_tests)), "SELECTED_AFFECTED_INTEGRATION",
                    ("REVIEWED_CONTROL_REGRESSIONS",), ("mt01-controls",))
        if selection_fn is None:
            sys.path.insert(0, str(root / "src"))
            from ai_commerce.engineering import minimum_test as mt
            candidate_files = mt.file_hashes(root)
            groups = mt.approved_groups()
            registered_tests = {t for g in groups for t in g.tests}
            all_tests = {p for p in candidate_files
                         if p.startswith("tests/test_") and p.endswith(".py")}
            if (all_tests - registered_tests - KNOWN_UNMAPPED) & mapped_paths:
                return FULL, "FULL_PROJECT_REQUIRED", ("NEW_UNMAPPED_TEST_DEFINITION",), ()
            plan = mt.select_plan(
                repo=root, base_sha=base, base_files=candidate_files,
                candidate_files=candidate_files, changed_paths=sorted(mapped_paths),
                required_acs=(), fallback_argv=("python", "-m", "pytest", "tests", "-q"),
                groups=groups, registered_only=False, env={},
            )
        else:
            plan = selection_fn(root=root, base=base, paths=mapped_paths)
        if plan.get("fallback") or plan.get("reason_codes"):
            return FULL, "FULL_PROJECT_REQUIRED", tuple(plan.get("reason_codes") or
                                                         ("UNKNOWN_SELECTION",)), ()
        if not plan.get("groups") or any(
            entry.get("state") != "EXECUTE" or not entry.get("execute")
            for entry in plan["groups"]
        ):
            return FULL, "FULL_PROJECT_REQUIRED", ("SELECTOR_NOT_EXECUTABLE",), ()
        groups = tuple(sorted({entry["group"] for entry in plan["groups"]} |
                              ({"mt01-controls"} if extra_tests else set())))
        selected = tuple(sorted({path for entry in plan["groups"]
                                 for path in entry["binding"]["mapping"]["tests"]} | extra_tests))
        if (not selected or len(selected) > 100 or any(
            not (p.startswith("tests/") or p.startswith("src/ai_commerce/"))
            or not p.endswith(".py") or not (root / p).is_file()
            for p in selected
        )):
            return FULL, "FULL_PROJECT_REQUIRED", ("SELECTED_TEST_PATH_UNSAFE_OR_MISSING",), ()
        if any(p.startswith("tests/test_") and p.endswith(".py") and p not in selected
               for p in paths):
            return FULL, "FULL_PROJECT_REQUIRED", ("MODIFIED_TEST_NOT_SELECTED",), ()
        return selected, "SELECTED_AFFECTED_INTEGRATION", ("MT01_PROVEN_CONSUMER_CLOSURE",), groups
    except Exception:
        # A selector failure must never silently reduce required PR test scope.
        return FULL, "FULL_PROJECT_REQUIRED", ("SELECTOR_ERROR_FAILS_TO_FULL",), ()



# Only exact, independently successful matrix PR runs can be reused on a
# byte-identical merge tree. Network/permission/lineage ambiguity fails to full.
WORKFLOW_FILE = ".github/workflows/ef02-tests.yml"


def postmerge_reuse(*, repo: Path, event: str, branch: str, current_sha: str,
                    repository: str, token: str, git_fn=None, api_fn=None) -> tuple[bool, str, str]:
    if event != "push" or branch != "refs/heads/main":
        return False, "NON_MAIN_PUSH_OR_PR", ""
    if not SHA.fullmatch(current_sha) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        return False, "INVALID_REPOSITORY_OR_SHA", ""
    if not token:
        return False, "MISSING_GITHUB_ACTION_TOKEN", ""
    def git(*argv):
        return subprocess.check_output(["git", "-C", str(repo), *argv],
                                       stderr=subprocess.DEVNULL, timeout=15).decode().strip()
    git = git_fn or git

    def request(url):
        req = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + token,
                          "Accept": "application/vnd.github+json",
                          "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(req, timeout=12) as res:
            return json.load(res)
    request = api_fn or request

    try:
        if git("rev-parse", "HEAD") != current_sha:
            return False, "CHECKOUT_SHA_DRIFT", ""
        parents = git("rev-list", "--parents", "-n", "1", current_sha).split()
        if len(parents) != 3 or parents[0] != current_sha:
            return False, "NOT_EXACT_TWO_PARENT_MERGE", ""
        tested_head = parents[2]
        if not SHA.fullmatch(tested_head):
            return False, "PR_PARENT_SHA_INVALID", ""
        if git("rev-parse", current_sha + "^{tree}") != git("rev-parse", tested_head + "^{tree}"):
            return False, "MERGE_TREE_DIFFERS_FROM_TESTED_PR", ""
        origin = "https://api.github.com/repos/" + repository
        query = urllib.parse.urlencode({"head_sha": tested_head,
                                       "event": "pull_request", "per_page": 100})
        runs = request(origin + "/actions/runs?" + query).get("workflow_runs")
        if not isinstance(runs, list):
            return False, "PR_RUN_EVIDENCE_MISSING", ""
        for run in runs:
            if (run.get("event") != "pull_request" or
                run.get("head_sha") != tested_head or
                run.get("status") != "completed" or
                run.get("conclusion") != "success" or
                str(run.get("path", "")).split("@")[0] != WORKFLOW_FILE):
                continue
            run_id = run.get("id")
            if type(run_id) is not int or run_id < 1:
                continue
            jobs = request(origin + f"/actions/runs/{run_id}/jobs?per_page=100").get("jobs")
            if not isinstance(jobs, list):
                continue
            matrix = {"tests (ubuntu-latest)": False, "tests (windows-latest)": False}
            for job in jobs:
                name = job.get("name")
                steps = job.get("steps")
                if not isinstance(steps, list):
                    continue
                # A green wrapper is not proof that Python/Node tests ran:
                # require the two actual reviewed test steps to have passed.
                test_steps = {s.get("name"): s.get("conclusion") for s in steps
                              if isinstance(s, dict)}
                pytest_pass = test_steps.get("MT-01 PR CI selective tests (full fallback)") == "success"
                node_pass = any("node --test tests/ef02-watch.test.cjs" in name and result == "success"
                                for name, result in test_steps.items() if isinstance(name, str))
                if (name in matrix and job.get("status") == "completed" and
                        job.get("conclusion") == "success" and pytest_pass and node_pass):
                    matrix[name] = True
            if all(matrix.values()):
                return True, "EXACT_TREE_AND_BOTH_OS_PR_CI_VERIFIED", str(run_id)
        return False, "NO_BOTH_OS_SUCCESSFUL_EXACT_HEAD_PR_RUN", ""
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            TimeoutError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Never print API tokens or response bodies in CI.
        return False, "PROOF_UNAVAILABLE_" + type(exc).__name__, ""


def postmerge_preflight() -> int:
    reuse, reason, run_id = postmerge_reuse(
        repo=ROOT, event=os.getenv("MT01_CI_EVENT", ""),
        branch=os.getenv("GITHUB_REF", ""), current_sha=os.getenv("GITHUB_SHA", ""),
        repository=os.getenv("GITHUB_REPOSITORY", ""), token=os.getenv("GITHUB_TOKEN", ""))
    print("MT01_POSTMERGE_REUSE=" + str(reuse).lower(), flush=True)
    print("MT01_POSTMERGE_PROOF_REASON=" + reason, flush=True)
    print("MT01_POSTMERGE_PR_RUN=" + run_id, flush=True)
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write("skip_pytest=" + str(reuse).lower() + "\n")
    # Fail-closed: no proof -> ordinary full on push. The preflight itself
    # never disables an existing required job or makes a model/provider call.
    return 0


def main_v1() -> int:
    files, mode, reasons, groups = choose(
        ROOT, event=os.environ.get("MT01_CI_EVENT", ""),
        base=os.environ.get("MT01_CI_BASE_SHA", ""),
    )
    print("MT01_PR_CI_MODE=" + mode, flush=True)
    print("MT01_PR_CI_REASON=" + ",".join(reasons), flush=True)
    print("MT01_PR_CI_GROUPS=" + ",".join(groups), flush=True)
    print("MT01_PR_CI_SELECTED_FILES=" + str(len(files)), flush=True)
    print("MT01_PR_CI_SELECTED_TARGETS=" + json.dumps(files), flush=True)
    print("MT01_PR_CI_SELECTED_TARGET_TYPE=" + ("FULL_TEST_DIRECTORY" if files == FULL else "REVIEWED_TEST_FILES"), flush=True)
    raw_workers = os.environ.get("MT01_CI_XDIST_WORKERS", "0")
    if raw_workers not in {"0", "2", "4"}:
        raise ValueError("MT01_CI_UNAPPROVED_XDIST_WORKERS")
    workers = int(raw_workers)
    argv = pytest_command(files, workers)
    print("MT01_PR_CI_XDIST_WORKERS=" + str(workers), flush=True)
    print("MT01_PR_CI_PYTEST_COMMAND=python -m pytest " + " ".join(argv[3:]), flush=True)
    return subprocess.call(argv, cwd=ROOT)



def main() -> int:
    """MT-01 V2.1 CI entrypoint: affected tests or explicit HOLD, never auto full."""
    from ai_commerce.engineering.impact_v21 import select_impact
    from ai_commerce.engineering.minimum_test import approved_groups
    event = os.getenv("MT01_CI_EVENT", "")
    base = os.getenv("MT01_CI_BASE_SHA", "")
    if event == "push" and not base:
        try:
            base = subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD^"],
                stderr=subprocess.DEVNULL, timeout=20).decode().strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            base = ""
    if event not in ("push", "pull_request") or not SHA.fullmatch(base):
        print("MT01_V21_STATUS=HOLD_PROOF", flush=True)
        print("MT01_V21_REASON=BASE_OR_EVENT_UNVERIFIED", flush=True)
        return 3
    try:
        changed, _ = changed_paths(ROOT, base)
        base_tests = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", "-z", base],
            stderr=subprocess.DEVNULL, timeout=20).decode("utf-8").split("\0")
        candidate = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], timeout=20).decode().strip()
        plan = select_impact(
            root=ROOT, changed_paths=sorted(changed), groups=approved_groups(),
            base_sha=base, candidate_sha=candidate,
            previous_tests=[p for p in base_tests if p.startswith("tests/test_") and p.endswith(".py")],
        )
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as exc:
        print("MT01_V21_STATUS=HOLD_PROOF", flush=True)
        print("MT01_V21_REASON=PLANNER_EVIDENCE_" + type(exc).__name__, flush=True)
        return 3
    print("MT01_V21_STATUS=" + plan.mode, flush=True)
    print("MT01_V21_REASON=" + ",".join(plan.reason_codes), flush=True)
    print("MT01_V21_GROUPS=" + ",".join(plan.selected_groups), flush=True)
    print("MT01_V21_TEST_PATHS=" + json.dumps(plan.selected_tests), flush=True)
    print("MT01_V21_MISSING_MAPPING=" + json.dumps(plan.missing_mapping), flush=True)
    print("MT01_V21_INVENTORY_WARNINGS=" + json.dumps(plan.inventory_warnings), flush=True)
    print("MT01_V21_PLAN=" + json.dumps(plan.as_dict(), sort_keys=True), flush=True)
    raw_workers = os.getenv("MT01_CI_XDIST_WORKERS", "0")
    if raw_workers not in {"0", "2", "4"}:
        print("MT01_V21_REASON=UNAPPROVED_HOSTED_XDIST_WORKERS", flush=True)
        return 3
    if not plan.runnable:
        return 3
    if plan.mode == "NO_PYTEST_APPLICABLE":
        # The workflow still runs its trusted Node/static steps.
        return 0
    files = list(plan.selected_tests)
    if not files:
        return 3
    workers = int(raw_workers)
    # No arbitrary shell supplied by an Issue, and no 100-file full fallback.
    # Small commands keep Windows command-line lengths bounded.
    for start in range(0, len(files), 40):
        targets = tuple(files[start:start + 40])
        argv = pytest_command(targets, workers)
        print("MT01_V21_PYTEST_COMMAND=python -m pytest " + " ".join(argv[3:]), flush=True)
        code = subprocess.call(argv, cwd=ROOT)
        if code != 0:
            return code
    return 0

if __name__ == "__main__":
    raise SystemExit(postmerge_preflight() if sys.argv[1:] == ["--reuse-preflight"] else main())
