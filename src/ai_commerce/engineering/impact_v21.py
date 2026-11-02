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
    ".github/workflows/ef02-tests.yml": (
        "tests/test_minimum_test.py", "tests/test_pr_merge_gate.py",
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
