"""MT-01 V2.1 baseline comparison oracle.

This is a distinct read-only verifier of selected file closure against an
immutable Git base. Until deployed from a protected base ref, it is not a
fully independent trust boundary against malicious workflow self-edit.
"""
from __future__ import annotations

import argparse
import os
import json
from pathlib import Path
import re
import subprocess
import sys
import types

SHA = re.compile(r"[0-9a-f]{40}\Z")
BASE_MAP = "src/ai_commerce/engineering/minimum_test.py"
# These controls are checked here, separately from impact_v21.CONTROL_TESTS.
CONTROL_TESTS = {
    ".github/scripts/pr-merge-gate.py": {"tests/test_pr_merge_gate.py"},
    ".github/scripts/mt01_v21_proof.py": {"tests/test_pr_merge_gate.py"},
    ".github/scripts/mt01_v21_oracle.py": {"tests/test_mt01_v21.py"},
    ".github/scripts/ef02-pr-ci-tests.py": {"tests/test_minimum_test.py"},
    ".github/workflows/ef02-tests.yml": {"tests/test_minimum_test.py", "tests/test_pr_merge_gate.py"},
    ".github/workflows/ef02-batch.yml": {"tests/test_ef02.py"},
    "src/ai_commerce/engineering/impact_v21.py": {"tests/test_mt01_v21.py"},
    "src/ai_commerce/engineering/minimum_test.py": {"tests/test_minimum_test.py"},
    "src/ai_commerce/remote/validation.py": {"tests/test_ef02.py"},
    ".github/ef02/verify-profiles.json": {"tests/test_ef02_mt01_registry.py"},
    # These files require the independently mandatory Node job, not pytest.
    "tests/ef02-watch.test.cjs": set(),
    "tests/ef02-reconcile.test.cjs": set(),
    ".github/scripts/ef02-watch.cjs": set(),
    ".github/scripts/ef02-reconcile.cjs": set(),
}


def baseline_groups(base_source: str) -> tuple:
    """Materialize only reviewed base-tree group definitions, not candidate code."""
    name = "mt01_v21_reviewed_git_baseline"
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(base_source, BASE_MAP, "exec"), module.__dict__)
        return module.approved_groups()
    finally:
        sys.modules.pop(name, None)


def _owned(group) -> set[str]:
    return set(group.paths) | set(group.tests) | set(group.consumers) | set(group.contracts)


def check_selection(plan: dict, *, base_groups: tuple, candidate_groups: tuple,
                    changed_paths: set[str], present_paths: set[str]) -> tuple[bool, tuple[str, ...]]:
    """Independent minimum test-file inclusion; unknown or deletion => HOLD."""
    if not isinstance(plan, dict) or plan.get("version") != 2 or not changed_paths:
        return False, ("PLAN_OR_DIFF_INVALID",)
    selected = plan.get("selected_tests")
    selected_groups = plan.get("selected_groups")
    if (not isinstance(selected, list) or any(not isinstance(t, str) for t in selected) or
        len(selected) != len(set(selected)) or not isinstance(selected_groups, list)):
        return False, ("SELECTION_SHAPE_INVALID",)
    if set(plan.get("changed_paths", [])) != changed_paths:
        return False, ("PLAN_DIFF_MISMATCH",)
    groups = {}
    conflicts = []
    for g in (*base_groups, *candidate_groups):
        if g.name in groups:
            old = groups[g.name]
            # Both source and candidate group obligations must hold.
            if old is not g:
                groups[g.name] = (old[0] | set(g.tests), old[1] | _owned(g))
        else:
            groups[g.name] = (set(g.tests), _owned(g))
    required = set()
    required_group_names = set()
    for name, (test_paths, coverage) in groups.items():
        if changed_paths & coverage:
            required.update(test_paths)
            required_group_names.add(name)
    for p in changed_paths:
        required.update(CONTROL_TESTS.get(p, ()))
        if p.startswith("tests/test_") and p.endswith(".py"):
            required.add(p)
    missing = sorted(required - set(selected))
    missing_groups = sorted(required_group_names - set(selected_groups))
    missing_paths = sorted(required - present_paths)
    reasons = (["REQUIRED_TEST_OMITTED:" + p for p in missing] +
               ["REQUIRED_GROUP_OMITTED:" + p for p in missing_groups] +
               ["REQUIRED_TEST_FILE_NOT_PRESENT:" + p for p in missing_paths])
    return not reasons, tuple(reasons)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    ).stdout


def resolve_base(repo: Path, requested: str) -> str:
    """Use trusted GitHub push context for postmerge, never candidate testimony."""
    if requested:
        return requested
    if (os.environ.get("GITHUB_EVENT_NAME") == "push" and
            os.environ.get("GITHUB_REF") == "refs/heads/main"):
        return _git(repo, "rev-parse", "HEAD^").decode().strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base", default="")
    parser.add_argument("--plan", type=Path, required=True)
    opts = parser.parse_args()
    try:
        repo = opts.repo.resolve()
        base = resolve_base(repo, opts.base)
        if not SHA.fullmatch(base):
            print("MT01_ORACLE_STATUS=HOLD_PROOF_INVALID_BASE", flush=True)
            return 3
        base_text = _git(repo, "show", f"{base}:{BASE_MAP}").decode("utf-8")
        groups_base = baseline_groups(base_text)
        # The candidate registry may add new groups; it cannot remove the
        # baseline's test obligations for a changed path.
        sys.path.insert(0, str(repo / "src"))
        from ai_commerce.engineering.minimum_test import approved_groups
        groups_candidate = approved_groups()
        plan = json.loads(opts.plan.read_text(encoding="utf-8"))
        head = _git(repo, "rev-parse", "HEAD").decode().strip()
        if head != plan.get("candidate_sha") or plan.get("base_sha") != base:
            print("MT01_ORACLE_STATUS=HOLD_PROOF_SHA_MISMATCH", flush=True)
            return 3
        changed = _git(repo, "diff", "--name-only", "-z", base, head)
        changed_paths = {n.decode("utf-8") for n in changed.split(b"\0") if n}
        # Capture rename/deletion sources from name-status to avoid silently
        # losing the base owner's obligation.
        status = _git(repo, "diff", "--name-status", "-z", "-M", base, head)
        fields = [s.decode("utf-8") for s in status.split(b"\0") if s]
        cursor = 0
        while cursor < len(fields):
            typ = fields[cursor]
            cursor += 1
            if typ.startswith(("R", "C")):
                if cursor + 1 >= len(fields):
                    raise ValueError("DIFF_RENAME_MALFORMED")
                changed_paths.add(fields[cursor])
                changed_paths.add(fields[cursor + 1])
                cursor += 2
            else:
                if cursor >= len(fields):
                    raise ValueError("DIFF_PATH_MALFORMED")
                changed_paths.add(fields[cursor])
                cursor += 1
        paths = {
            p.relative_to(repo).as_posix()
            for branch in ("src", "tests")
            if (repo / branch).exists()
            for p in (repo / branch).rglob("*.py") if p.is_file()
        }
        # Plan's path list must reflect all rename source+dest paths.
        good, reasons = check_selection(
            plan, base_groups=groups_base, candidate_groups=groups_candidate,
            changed_paths=changed_paths, present_paths=paths)
        print("MT01_ORACLE_STATUS=" + ("PASS_BASELINE_CLOSURE" if good else "HOLD_COVERAGE"), flush=True)
        for reason in reasons:
            print("MT01_ORACLE_REASON=" + reason, flush=True)
        return 0 if good else 3
    except (OSError, ValueError, TypeError, UnicodeError, KeyError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print("MT01_ORACLE_STATUS=HOLD_PROOF_" + type(exc).__name__, flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
