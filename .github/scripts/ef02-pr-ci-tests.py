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
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
FULL = ("tests",)
SHA = re.compile(r"[0-9a-f]{40}\Z")
# Existing registry-control test is intentionally outside approved MT-01
# groups. Changes to it must trigger full verification.
KNOWN_UNMAPPED = frozenset({"tests/test_ef02_mt01_registry.py"})
REGISTRY = ".github/ef02/verify-profiles.json"
MAPPED_GITHUB_CONTROLS = frozenset({".github/ef02/approved-candidates.v1.json"})
MT01_REPORT = "docs/engineering/MT01_COMPONENT_AWARE_IMPLEMENTATION_REPORT.md"
REVIEWED_REGISTRY_REGRESSIONS = ("tests/test_ef02_mt01_registry.py", "tests/test_minimum_test.py")
HOSTED_WORKERS = (0, 2, 4)


def additive_registry_tests(before: object, after: object, groups: tuple) -> tuple[str, ...] | None:
    """Only newly added, exact approved xdist-12 group commands are narrow-safe."""
    if not isinstance(before, dict) or not before or not isinstance(after, dict):
        return None
    if not set(before).issubset(after) or any(after[key] != value for key, value in before.items()):
        return None
    added = set(after) - set(before)
    if not added:
        return None
    reviewed = {
        tuple(["python", "-m", "pytest", *group.tests, "-q", "-n", "12"]): tuple(group.tests)
        for group in groups
    }
    selected = set(REVIEWED_REGISTRY_REGRESSIONS)
    for name in sorted(added):
        argv = after[name]
        if (not isinstance(name, str) or not name.startswith("mt01-") or
                not isinstance(argv, list) or not all(isinstance(v, str) for v in argv)):
            return None
        tests = reviewed.get(tuple(argv))
        if not tests:
            return None
        selected.update(tests)
    return tuple(sorted(selected))


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
        if any(not (root / test).is_file() for test in extra_tests):
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
            if all_tests - registered_tests - KNOWN_UNMAPPED:
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


def main() -> int:
    files, mode, reasons, groups = choose(
        ROOT, event=os.environ.get("MT01_CI_EVENT", ""),
        base=os.environ.get("MT01_CI_BASE_SHA", ""),
    )
    print("MT01_PR_CI_MODE=" + mode, flush=True)
    print("MT01_PR_CI_REASON=" + ",".join(reasons), flush=True)
    print("MT01_PR_CI_GROUPS=" + ",".join(groups), flush=True)
    print("MT01_PR_CI_SELECTED_FILES=" + str(len(files)), flush=True)
    raw_workers = os.environ.get("MT01_CI_XDIST_WORKERS", "0")
    if raw_workers not in {"0", "2", "4"}:
        raise ValueError("MT01_CI_UNAPPROVED_XDIST_WORKERS")
    workers = int(raw_workers)
    argv = pytest_command(files, workers)
    print("MT01_PR_CI_XDIST_WORKERS=" + str(workers), flush=True)
    print("MT01_PR_CI_PYTEST_COMMAND=python -m pytest " + " ".join(argv[3:]), flush=True)
    return subprocess.call(argv, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
