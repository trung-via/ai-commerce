"""MT-01 V2.1 deterministic, bounded test-impact planner.

This planner does not execute tests, grant candidate scope, or attest a PASS.
Callers must use a trusted runner, verify actual collection/execution and
retain Human approval / immutable candidate provenance.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


CONTROL_TESTS = {
    ".github/scripts/ef02-pr-ci-tests.py": (
        "tests/test_minimum_test.py", "tests/test_ef02_mt01_registry.py",
    ),
    ".github/workflows/ef02-batch.yml": ("tests/test_ef02.py", "tests/test_minimum_test.py"),
    ".github/workflows/ef02-tests.yml": (
        "tests/test_minimum_test.py", "tests/test_pr_merge_gate.py",
    ),
    ".github/scripts/mt01_v21_proof.py": (
        "tests/test_pr_merge_gate.py", "tests/test_mt01_v21.py",
    ),
    ".github/scripts/pr-merge-gate.py": (
        "tests/test_pr_merge_gate.py", "tests/test_minimum_test.py",
    ),
    ".github/workflows/human-merge-queue.yml": ("tests/test_pr_merge_gate.py",),
    ".github/ef02/verify-profiles.json": (
        "tests/test_minimum_test.py", "tests/test_ef02_mt01_registry.py",
    ),
    "src/ai_commerce/engineering/minimum_test.py": (
        "tests/test_minimum_test.py", "tests/test_ef02.py",
        "tests/test_ef02_mt01_registry.py",
    ),
    "src/ai_commerce/engineering/impact_v21.py": (
        "tests/test_mt01_v21.py", "tests/test_minimum_test.py",
    ),
    "src/ai_commerce/remote/validation.py": (
        "tests/test_minimum_test.py", "tests/test_ef02.py",
    ),
}
COLLECTION_FILES = {
    "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "setup.py",
}
LEGACY_UNMAPPED = frozenset({"tests/test_ef02_mt01_registry.py"})


@dataclass(frozen=True)
class ImpactPlan:
    version: int
    mode: str
    changed_paths: tuple[str, ...]
    selected_groups: tuple[str, ...]
    selected_tests: tuple[str, ...]
    reason_codes: tuple[str, ...]
    missing_mapping: tuple[str, ...]
    inventory_warnings: tuple[str, ...]
    affected_paths: tuple[str, ...]
    base_sha: str
    candidate_sha: str

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def runnable(self) -> bool:
        return self.mode in {"TARGETED", "AFFECTED", "BOUNDED_BROAD", "NO_PYTEST_APPLICABLE"}


def _valid(path: str) -> bool:
    return (
        isinstance(path, str) and path
        and "\\" not in path and "\x00" not in path
        and not path.startswith("/") and ":" not in path
        and all(p not in ("", ".", "..") for p in path.split("/"))
    )


def _python_paths(root: Path) -> tuple[str, ...]:
    return tuple(sorted(
        p.relative_to(root).as_posix()
        for branch in ("src/ai_commerce", "tests")
        if (root / branch).is_dir()
        for p in (root / branch).rglob("*.py")
        if p.is_file()
    ))


def _reverse_import_graph(root: Path, paths: Iterable[str]) -> tuple[dict[str, set[str]], set[str]]:
    """Static local-import edges; unresolved dynamic imports are tracked per file."""
    paths = tuple(paths)
    lookup = {}
    for path in paths:
        if path.startswith("src/"):
            module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
            lookup[module.removesuffix(".__init__")] = path
        elif path.startswith("tests/"):
            lookup[Path(path).stem] = path
    reverse: dict[str, set[str]] = {}
    uncertain: set[str] = set()
    for path in paths:
        try:
            contents = (root / path).read_text(encoding="utf-8-sig")
            tree = ast.parse(contents)
        except (OSError, UnicodeError, SyntaxError):
            uncertain.add(path)
            continue
        module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        package = module.removesuffix(".__init__") if path.endswith("/__init__.py") else module.rpartition(".")[0]
        aliases = {"__import__"}
        aliases.update(
            a.asname or a.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module in ("importlib", "builtins")
            for a in node.names if a.name in ("import_module", "__import__")
        )
        deps = set()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    parts = package.split(".")
                    prefix = ".".join(parts[:len(parts) - node.level + 1] + ([prefix] if prefix else []))
                names = [prefix] + [prefix + "." + a.name for a in node.names]
            elif isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id in aliases
                or isinstance(node.func, ast.Attribute) and node.func.attr in ("import_module", "__import__")
            ):
                if (node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                        and not node.args[0].value.startswith(".")):
                    names = [node.args[0].value]
                else:
                    uncertain.add(path)
            for name in names:
                while name:
                    if name in lookup:
                        deps.add(lookup[name])
                    name = name.rpartition(".")[0]
        for dep in deps:
            reverse.setdefault(dep, set()).add(path)
    return reverse, uncertain


def _consumers(paths: Iterable[str], reverse: dict[str, set[str]]) -> set[str]:
    result = set(paths)
    pending = list(result)
    while pending:
        for child in reverse.get(pending.pop(), ()):
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def select_impact(
    *, root: Path, changed_paths: Sequence[str], groups: Sequence,
    base_sha: str = "", candidate_sha: str = "", previous_tests: Iterable[str] = (),
    additional_control_tests: dict[str, Sequence[str]] | None = None,
) -> ImpactPlan:
    """Select only impacted groups/tests; unknown scope holds, never expands to full.

    previous_tests are immutable base-tree test paths, if known. Unrelated
    orphan files in a dirty worktree are independently reported, not gates on
    an unrelated component. New changed tests cannot be silently skipped.
    """
    changed = tuple(sorted(set(changed_paths)))
    def decision(mode, tests=(), selected=(), reasons=(), missing=(), warnings=(), affected=()):
        return ImpactPlan(
            2, mode, changed, tuple(sorted(set(selected))), tuple(sorted(set(tests))),
            tuple(sorted(set(reasons))), tuple(sorted(set(missing))),
            tuple(sorted(set(warnings))), tuple(sorted(set(affected))), base_sha, candidate_sha,
        )
    if not changed or any(not _valid(p) for p in changed):
        return decision("HOLD_PROOF", reasons=("EMPTY_OR_UNSAFE_DIFF",))
    root = root.resolve()
    current_tests = {p.relative_to(root).as_posix() for p in (root / "tests").glob("test_*.py")} \
        if (root / "tests").is_dir() else set()
    python = _python_paths(root)
    reverse, uncertain = _reverse_import_graph(root, python)
    affected = _consumers(changed, reverse)
    group_tests = {t for g in groups for t in g.tests}
    registered = set(group_tests) | LEGACY_UNMAPPED
    extra_controls = dict(CONTROL_TESTS)
    if additional_control_tests:
        extra_controls.update(additional_control_tests)
    selected_tests: set[str] = set()
    selected_groups: set[str] = set()
    missing: set[str] = set()
    reasons: set[str] = set()
    related: set[str] = set()
    for group in groups:
        coverage = set(group.paths) | set(group.tests) | set(group.consumers) | set(group.contracts)
        if affected & coverage or changed and set(changed) & coverage:
            selected_groups.add(group.name)
            selected_tests.update(group.tests)
            related.update(coverage)
    for path in changed:
        if path in extra_controls:
            selected_tests.update(extra_controls[path])
            reasons.add("REVIEWED_CONTROL_CLOSURE")
            related.add(path)
        if path in COLLECTION_FILES or Path(path).name in {"conftest.py", ".coveragerc"} \
                or Path(path).name.startswith(("requirements", "constraints")) \
                or path.endswith((".lock", ".pth")):
            missing.add(path + ":SHARED_COLLECTION_OR_ENVIRONMENT")
        if path.startswith(".github/") and path not in extra_controls:
            missing.add(path + ":UNREVIEWED_CI_CONTROL")
        if path.startswith("tests/test_") and path.endswith(".py"):
            if path not in registered:
                missing.add(path + ":TEST_REGISTRATION_REQUIRED")
                if (root / path).is_file():
                    selected_tests.add(path)
            elif path in group_tests:
                selected_tests.add(path)
        elif path.startswith("src/ai_commerce/") and path.endswith(".py") \
                and path not in related and path not in extra_controls:
            missing.add(path + ":SOURCE_OWNERSHIP_REQUIRED")
        elif path.startswith("contracts/") and path not in related:
            missing.add(path + ":CONTRACT_CONSUMER_REQUIRED")
    pending = current_tests - registered
    changed_test_paths = {p for p in changed if p.startswith("tests/test_")}
    warnings = {
        p + ":UNRELATED_UNMAPPED_TEST" for p in pending
        if p not in changed_test_paths
    }
    previous = set(previous_tests)
    for path in changed_test_paths & previous:
        if path not in registered:
            missing.add(path + ":EXISTING_TEST_OWNER_REQUIRED")
    # Unrelated import uncertainty is never a repository-global veto.
    involved = uncertain & (set(changed) | affected | related)
    if involved:
        missing.update(p + ":UNRESOLVED_IMPORT" for p in involved)
    for path in sorted(selected_tests):
        p = root / path
        if not p.is_file() or p.is_symlink():
            missing.add(path + ":REQUIRED_TEST_MISSING_OR_UNSAFE")
    if missing:
        mode = "HOLD_COVERAGE" if any(s.endswith((":UNRESOLVED_IMPORT", ":SHARED_COLLECTION_OR_ENVIRONMENT"))
                                          for s in missing) else "HOLD_MAPPING"
        return decision(mode, selected_tests, selected_groups, reasons, missing, warnings, affected)
    if not selected_tests:
        if all(p.startswith(("docs/", ".github/ISSUE_TEMPLATE/")) or p.endswith(".md") for p in changed):
            return decision("NO_PYTEST_APPLICABLE", reasons=("REVIEWED_DOCUMENT_ONLY",), warnings=warnings, affected=affected)
        return decision("HOLD_MAPPING", reasons=("NO_RELEVANT_TEST_PROOF",),
                        missing=tuple(p + ":UNMAPPED" for p in changed), warnings=warnings, affected=affected)
    if all(t.startswith("tests/") or t.startswith("src/ai_commerce/") for t in selected_tests):
        return decision("AFFECTED" if len(selected_groups) > 1 else "TARGETED",
                        selected_tests, selected_groups, reasons or ("SCOPED_CONSUMER_CLOSURE",),
                        warnings=warnings, affected=affected)
    return decision("HOLD_MAPPING", reasons=("UNSUPPORTED_TEST_PATH",),
                    missing=tuple(sorted(selected_tests)), warnings=warnings, affected=affected)


def registered_action_plan(
    *, repo: Path, base_sha: str, base_files, candidate_files,
    changed_paths: Sequence[str], required_acs: Sequence[str], fallback_argv,
    groups: Sequence, registered_profiles: dict | None, aggregate_sources=(), env=None,
) -> dict:
    """Translate V2 selected impact to the existing immutable EF01/EF02 evidence contract.

    No new Issue-controlled argv and no broad implicit fallback. Invalid mapping
    produces an UNPROVEN group, causing the trusted runner to stop with a
    verifiable nonzero status, not a silently successful verification.
    """
    import shutil
    import sys
    from .minimum_test import canonical, digest

    impact = select_impact(
        root=repo, changed_paths=changed_paths, groups=groups,
        base_sha=base_sha,
    )
    by_name = {g.name: g for g in groups}
    profiles = registered_profiles or {}
    entries = []
    problems = list(impact.missing_mapping)
    known_acs = {ac for group in groups for ac in group.acs}
    unknown_acs = sorted(set(required_acs) - known_acs)
    if unknown_acs:
        problems.extend("AC_UNMAPPED:" + ac for ac in unknown_acs)
    select_parallel = list(fallback_argv[-2:]) == ["-n", "12"]
    for name in impact.selected_groups:
        group = by_name[name]
        expected = ["python", "-m", "pytest", *group.tests, "-q"]
        if select_parallel:
            expected.extend(["-n", "12"])
        matched = next((key for key, value in sorted(profiles.items()) if value == expected), None)
        if matched is None:
            problems.append(name + ":REGISTERED_PROFILE_REQUIRED")
        # Closed, registered fallback argv only; never synthesize unknown commands.
        argv = list(profiles[matched]) if matched is not None else list(fallback_argv)
        executable = shutil.which(argv[0]) if argv else None
        paths = (set(group.tests) | set(group.paths) | set(group.consumers) |
                 set(group.contracts) | (set(changed_paths) & set(impact.affected_paths)))
        if aggregate_sources:
            # Current composed-source hashes are required by aggregate receipt verification.
            paths.update(candidate_files)
        files = sorted(paths)
        binding = {
            "version": 2, "base_sha": base_sha,
            "aggregate_sources": list(aggregate_sources),
            "workspace": str(repo.resolve()),
            "base_files": {p: base_files.get(p) for p in files},
            "candidate_files": {p: candidate_files.get(p) for p in files},
            "argv": argv, "mapping": group.mapping(), "environment": env or {},
            "executable_sha256": digest(Path(executable).read_bytes()) if executable else None,
            "environment_proven": bool(executable and Path(executable).resolve() == Path(sys.executable).resolve()),
            "registered_profile": matched,
            "selected_impact_tests": list(impact.selected_tests),
        }
        executable_scope = bool(impact.runnable and matched)
        entries.append({
            "group": name, "state": "EXECUTE" if executable_scope else "UNPROVEN",
            "execute": executable_scope, "acs": list(group.acs),
            "binding": __import__("json").loads(canonical(binding)),
            "selected_profile": matched,
            "test_files_count": len(group.tests),
            "explanation": ["MT01_V21_SCOPED_ACTUAL_CHANGED_PATHS"] if executable_scope else
                           ["MT01_V21_HOLD_MISSING_VERIFICATION_PROOF"],
        })
    if not entries or problems:
        # One explicit refusal record at the end; no all-tests substitution.
        entries.append({
            "group": "v21-coverage-hold", "state": "UNPROVEN", "execute": False,
            "acs": [], "selected_profile": None, "test_files_count": 0,
            "explanation": list(problems or ["NO_SCOPED_VERIFICATION_GROUP"]),
            "binding": {
                "version": 2, "base_sha": base_sha, "aggregate_sources": list(aggregate_sources),
                "workspace": str(repo.resolve()), "base_files": {}, "candidate_files": {},
                "argv": list(fallback_argv), "mapping": {
                    "tests": [], "acs": [], "paths": [], "consumers": [], "contracts": []},
                "environment": env or {}, "executable_sha256": None,
                "environment_proven": False, "registered_profile": None,
                "selected_impact_tests": list(impact.selected_tests),
            },
        })
    return {
        "kind": "AI_COMMERCE_MT01_PLAN", "version": 2,
        "required_acs": sorted(set(required_acs)),
        "changed_paths": sorted(set(changed_paths)),
        "affected_paths": list(impact.affected_paths),
        "unknown_acs": unknown_acs,
        "unknown_tests": [p for p in impact.missing_mapping if "TEST" in p],
        "unknown_consumers": [p for p in impact.missing_mapping if "SOURCE" in p or "IMPORT" in p],
        "reason_codes": list(impact.reason_codes) + list(problems),
        "unsupported_paths": [],
        "consumer_map": {g.name: g.mapping() for g in groups if g.name in impact.selected_groups},
        "selected_groups": list(impact.selected_groups),
        "selected_profiles": [e["selected_profile"] for e in entries if e["selected_profile"]],
        "fallback_profile": None, "fallback_argv": list(fallback_argv),
        "narrow_execution_observed": False, "fallback": False,
        "aggregate_sources": list(aggregate_sources), "groups": entries,
        "impact_v21": impact.as_dict(),
    }
