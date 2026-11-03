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
    ".github/scripts/mt01_v21_oracle.py": ("tests/test_mt01_v21.py",),
    ".github/scripts/pr-merge-gate.py": (
        "tests/test_pr_merge_gate.py", "tests/test_minimum_test.py",
    ),
    ".github/workflows/human-merge-queue.yml": ("tests/test_pr_merge_gate.py",),
    ".github/workflows/brain-pass-auto-merge.yml": ("tests/test_pr_merge_gate.py",),
    # HostOps v3 hosted-only signed queue; never authorize unreviewed CI paths.
    ".github/workflows/hostops-v3.yml": (
        "tests/test_hostops_v3_integration.py", "tests/test_pr_merge_gate.py",
        "tests/test_mt01_v21.py",
    ),
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
# Reviewed Node sources have mandatory GitHub-hosted `node --test` evidence.
# No pytest test file is invented; unrelated JavaScript remains HOLD.
REVIEWED_NODE_CONTROL_FILES = frozenset(["tests/ef02-watch.test.cjs","tests/ef02-reconcile.test.cjs",".github/scripts/ef02-watch.cjs",".github/scripts/ef02-reconcile.cjs"])

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
    # A new component's declared legacy consumers need their owner tests
    # even when the consumer source itself is unchanged in this PR. Do not
    # confuse "not edited" with "not affected by a new interface".
    for group in groups:
        if not any(p.startswith("work_items/mt01-components/") for p in group.contracts):
            continue
        if not set(changed) & (set(group.paths) | set(group.tests) | set(group.contracts)):
            continue
        for old in groups:
            if old is group or not (set(group.consumers) & set(old.paths)):
                continue
            selected_groups.add(old.name)
            selected_tests.update(old.tests)
            related.update((*old.paths, *old.tests, *old.consumers, *old.contracts))
            reasons.add("ONBOARD_CONSUMER_OWNER_CLOSURE")
    for path in changed:
        if path in extra_controls:
            selected_tests.update(extra_controls[path])
            reasons.add("REVIEWED_CONTROL_CLOSURE")
            related.add(path)
        if path in COLLECTION_FILES or Path(path).name in {"conftest.py", ".coveragerc"} \
                or Path(path).name.startswith(("requirements", "constraints")) \
                or path.endswith((".lock", ".pth")):
            missing.add(path + ":SHARED_COLLECTION_OR_ENVIRONMENT")
        if path in REVIEWED_NODE_CONTROL_FILES:
            reasons.add("REVIEWED_NODE_TEST_STEP_REQUIRED")
            related.add(path)
        elif path.startswith(".github/") and path not in extra_controls:
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
        if all(p in REVIEWED_NODE_CONTROL_FILES or p.startswith(("docs/", ".github/ISSUE_TEMPLATE/"))
               or p.endswith(".md") for p in changed):
            reason = "REVIEWED_NODE_TEST_STEP_REQUIRED" if any(
                p in REVIEWED_NODE_CONTROL_FILES for p in changed) else "REVIEWED_DOCUMENT_ONLY"
            return decision("NO_PYTEST_APPLICABLE", reasons=(reason,),
                            warnings=warnings, affected=affected)
        return decision("HOLD_MAPPING", reasons=("NO_RELEVANT_TEST_PROOF",),
                        missing=tuple(p + ":UNMAPPED" for p in changed), warnings=warnings, affected=affected)
    if all(t.startswith("tests/") or t.startswith("src/ai_commerce/") for t in selected_tests):
        return decision("AFFECTED" if len(selected_groups) > 1 else "TARGETED",
                        selected_tests, selected_groups, reasons or ("SCOPED_CONSUMER_CLOSURE",),
                        warnings=warnings, affected=affected)
    return decision("HOLD_MAPPING", reasons=("UNSUPPORTED_TEST_PATH",),
                    missing=tuple(sorted(selected_tests)), warnings=warnings, affected=affected)




def validate_component_onboarding_manifest(
    data: object, *, manifest_path: str, changed_paths: Sequence[str],
    base_paths: Sequence[str], existing_groups: Sequence,
    allowed_paths: Sequence[str], root: Path,
) -> dict:
    """V2.2 foundation: DATA-ONLY new-owner admission, not test execution authority.

    Every newly owned source/test and its manifest must be added inside the
    signed EF02 allowed_paths. An existing consumer can be referenced but
    NEVER re-owned. No argv, import, glob, env or executable value is accepted.
    CI/merge must independently replay these checks against an immutable base.
    """
    import hashlib
    import json
    import re

    fields = {"schema_version", "component", "sources", "tests", "acs",
              "consumers", "contracts"}
    if type(data) is not dict or set(data) != fields:
        raise ValueError("ONBOARD_MANIFEST_SHAPE")
    if data["schema_version"] != "MT01ComponentOwner.v1":
        raise ValueError("ONBOARD_SCHEMA_VERSION")
    name = data["component"]
    if type(name) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", name):
        raise ValueError("ONBOARD_COMPONENT_ID")
    if manifest_path != f"work_items/mt01-components/{name}.json":
        raise ValueError("ONBOARD_MANIFEST_LOCATION")
    existing_names = {g.name for g in existing_groups}
    if name in existing_names:
        raise ValueError("ONBOARD_COMPONENT_ALREADY_OWNED")
    changed, base, allowed = map(set, (changed_paths, base_paths, allowed_paths))
    def in_scope(path: str) -> bool:
        # Match the already-authorized EF01 directory-prefix semantics, not
        # an Issue-supplied glob. Every path must also pass its family guard.
        return any(path == prefix or path.startswith(prefix.rstrip("/") + "/")
                   for prefix in allowed)
    if manifest_path not in changed or manifest_path in base or not in_scope(manifest_path):
        raise ValueError("ONBOARD_MANIFEST_NOT_NEW_AND_AUTHORIZED")
    if len(changed) != len(set(changed_paths)) or len(allowed) != len(allowed_paths):
        raise ValueError("ONBOARD_DUPLICATE_PATH")
    if not all(in_scope(path) for path in changed):
        raise ValueError("ONBOARD_CHANGED_OUT_OF_SCOPE")

    items = {}
    for key in ("sources", "tests", "acs", "consumers", "contracts"):
        values = data[key]
        minimum = 1 if key in {"sources", "tests", "acs"} else 0
        if (type(values) is not list or not minimum <= len(values) <= 40
                or any(type(v) is not str or not v or len(v) > 240 for v in values)
                or len(values) != len(set(values))):
            raise ValueError("ONBOARD_INVALID_" + key.upper())
        items[key] = tuple(values)
    sources, tests, acs = (items[key] for key in ("sources", "tests", "acs"))
    consumers, contracts = items["consumers"], items["contracts"]
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", ac) for ac in acs):
        raise ValueError("ONBOARD_INVALID_AC")
    for path in (*sources, *tests, *consumers, *contracts):
        if not _valid(path):
            raise ValueError("ONBOARD_UNSAFE_PATH")
    if any(not (p.startswith("src/ai_commerce/") and p.endswith(".py")
                and not p.startswith(("src/ai_commerce/engineering/",
                                      "src/ai_commerce/remote/"))) for p in sources):
        raise ValueError("ONBOARD_UNSAFE_SOURCE_FAMILY")
    if any(not re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py", p) for p in tests):
        raise ValueError("ONBOARD_UNSAFE_TEST_FAMILY")
    if any(not p.startswith("contracts/") or not p.endswith(".schema.json")
           for p in contracts):
        raise ValueError("ONBOARD_UNSAFE_CONTRACT_FAMILY")
    if any(not p.startswith("src/ai_commerce/") or not p.endswith(".py")
           for p in consumers):
        raise ValueError("ONBOARD_UNSAFE_CONSUMER_FAMILY")
    existing_owned = set()
    existing_tests = set()
    for group in existing_groups:
        existing_owned.update((*group.paths, *group.tests, *group.consumers, *group.contracts))
        existing_tests.update(group.tests)
    fresh = set(sources) | set(tests)
    if fresh & (existing_owned | base) or not fresh <= changed or not all(in_scope(p) for p in fresh):
        raise ValueError("ONBOARD_REOWN_OR_NOT_ADDED")
    if set(tests) & existing_tests:
        raise ValueError("ONBOARD_EXISTING_TEST")
    if any(p not in existing_owned and p not in sources for p in consumers):
        raise ValueError("ONBOARD_CONSUMER_UNPROVEN")
    if any(p not in existing_owned and (p not in changed or p in base or not in_scope(p))
           for p in contracts):
        raise ValueError("ONBOARD_CONTRACT_UNPROVEN")
    root = root.resolve()
    for path in (manifest_path, *fresh, *contracts):
        target = root / path
        if (not target.is_file() or target.is_symlink()
                or root not in target.resolve().parents):
            raise ValueError("ONBOARD_FILE_MISSING_OR_LINK")
        ancestor = target.parent
        while ancestor != root:
            if ancestor.is_symlink():
                raise ValueError("ONBOARD_UNSAFE_PARENT_LINK")
            ancestor = ancestor.parent
    try:
        payload = (root / manifest_path).read_bytes()
        observed = json.loads(payload.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("ONBOARD_MANIFEST_BYTES_UNPROVEN") from exc
    if observed != data:
        raise ValueError("ONBOARD_MANIFEST_BYTES_MISMATCH")
    return {
        "name": name, "manifest_path": manifest_path,
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        "paths": sources, "tests": tests, "acs": acs,
        "consumers": consumers, "contracts": contracts,
        "status": "VALIDATED_DATA_ONLY_NOT_VERIFICATION_PASS",
    }



def verified_onboarding_binding(
    *, workspace: Path, binding: dict, changed_paths: Sequence[str],
    allowed_paths: Sequence[str], base_sha: str, registered_profiles: dict,
    expected_group: str | None = None,
) -> bool:
    """Independently rebuild the one candidate-generated test argv in trusted code.

    The EF02 worker invokes this after EF01 exits, not on self-reported PASS
    alone. All existing group commands remain byte-identical to the registry.
    """
    import json
    import subprocess
    from .minimum_test import TestGroup, approved_groups

    try:
        if (registered_profiles.get("mt01-component-onboarding") is None
                or binding.get("registered_profile") != "mt01-component-onboarding"
                or binding.get("onboarding_data_only") is not True
                or binding.get("base_sha") != base_sha):
            return False
        path = binding["onboarding_manifest_path"]
        if (not isinstance(path, str)
                or not path.startswith("work_items/mt01-components/")
                or not path.endswith(".json")):
            return False
        original = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", base_sha],
            cwd=workspace, capture_output=True, check=False, timeout=30)
        if original.returncode:
            return False
        base_paths = [x.decode("utf-8") for x in original.stdout.split(bytes([0])) if x]
        old_groups = approved_groups()
        baseline_records = load_baseline_component_owner_records(
            root=workspace, base_sha=base_sha, existing_groups=old_groups)
        baseline_by_path = {item["manifest_path"]: item for item in baseline_records}
        if path in baseline_by_path:
            if path in changed_paths:
                return False
            record = baseline_by_path[path]
            group = record["group"]
            digest = record["manifest_sha256"]
        else:
            candidate = json.loads((workspace / path).read_text(encoding="utf-8"))
            admission = validate_component_onboarding_manifest(
                candidate, manifest_path=path,
                changed_paths=changed_paths, base_paths=base_paths,
                existing_groups=(*old_groups, *(item["group"] for item in baseline_records)),
                allowed_paths=allowed_paths, root=workspace)
            digest = admission["manifest_sha256"]
            group = TestGroup(
                admission["name"], admission["tests"], admission["acs"], admission["paths"],
                admission["consumers"], (*admission["contracts"], path))
        if binding["onboarding_manifest_sha256"] != digest:
            return False
        if expected_group is not None and expected_group != group.name:
            return False
        baseline_groups = tuple(item["group"] for item in baseline_records)
        groups = (*old_groups, *baseline_groups)
        if path not in baseline_by_path:
            groups = (*groups, group)
        plan = select_impact(
            root=workspace, changed_paths=changed_paths, groups=groups,
            base_sha=base_sha)
        if not plan.runnable or group.name not in plan.selected_groups:
            return False
        if binding.get("mapping") != group.mapping():
            return False
        if binding.get("selected_impact_tests") != list(plan.selected_tests):
            return False
        return binding.get("argv") == [
            "python", "-m", "pytest", *group.tests, "-q", "-n", "12"]
    except (OSError, ValueError, TypeError, UnicodeError, KeyError,
            subprocess.TimeoutExpired):
        return False


def candidate_onboarding_groups(
    *, root: Path, changed_paths: Sequence[str], base_paths: Sequence[str],
    existing_groups: Sequence, allowed_paths: Sequence[str],
) -> tuple:
    """Construct CI's finite new-owner map from changed manifest data, not code.

    GitHub merge gate independently revalidates source blobs and obligations;
    this helper does not approve a merge or any on-host EF02 action.
    """
    import json
    from .minimum_test import TestGroup

    paths = sorted(p for p in changed_paths if
                   p.startswith("work_items/mt01-components/") and p.endswith(".json"))
    if len(paths) > 8:
        raise ValueError("ONBOARD_TOO_MANY_MANIFESTS")
    results = []
    groups = tuple(existing_groups)
    for path in paths:
        item = json.loads((root / path).read_text(encoding="utf-8"))
        checked = validate_component_onboarding_manifest(
            item, manifest_path=path, changed_paths=changed_paths,
            base_paths=base_paths, existing_groups=groups,
            allowed_paths=allowed_paths, root=root)
        group = TestGroup(
            checked["name"], checked["tests"], checked["acs"], checked["paths"],
            checked["consumers"], (*checked["contracts"], path))
        groups = (*groups, group)
        results.append(group)
    return tuple(results)


def load_baseline_component_owner_records(
    *, root: Path, base_sha: str, existing_groups: Sequence,
) -> tuple[dict, ...]:
    """Load persisted reviewed owners ONLY from immutable Git base, never
    newly supplied candidate manifest declarations.

    A component's first PR must pass protected manifest admission. In later
    PRs its committed owner remains available for scoped tests without a
    second preregistration PR or mutable Python registry modification.
    """
    import json
    import re
    import subprocess
    from .minimum_test import TestGroup

    root = root.resolve()
    if not (root / ".git").exists():
        return ()
    if type(base_sha) is not str or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("ONBOARD_BASE_SHA_UNPROVEN")
    def git(*argv: str) -> bytes:
        try:
            process = subprocess.run(["git", *argv], cwd=root, timeout=30,
                                     check=False, capture_output=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("ONBOARD_BASE_TREE_UNAVAILABLE") from exc
        if process.returncode:
            raise ValueError("ONBOARD_BASE_TREE_UNAVAILABLE")
        return process.stdout
    base_paths = {p.decode("utf-8") for p in
                  git("ls-tree", "-r", "--name-only", "-z", base_sha).split(bytes([0])) if p}
    manifests = sorted(p for p in base_paths
                       if p.startswith("work_items/mt01-components/"))
    if len(manifests) > 256:
        raise ValueError("ONBOARD_BASELINE_OWNER_LIMIT")
    accepted = []
    groups = tuple(existing_groups)
    for path in manifests:
        if not path.endswith(".json"):
            raise ValueError("ONBOARD_BASELINE_UNKNOWN_OWNER_FILE")
        raw = git("show", f"{base_sha}:{path}")
        if len(raw) > 32768:
            raise ValueError("ONBOARD_BASELINE_MANIFEST_TOO_LARGE")
        target = root / path
        if not target.is_file() or target.is_symlink():
            raise ValueError("ONBOARD_BASELINE_MANIFEST_MISSING")
        actual = target.read_bytes()
        if raw != actual and actual.replace(bytes([13, 10]), bytes([10])) != raw:
            raise ValueError("ONBOARD_BASELINE_MANIFEST_DRIFT")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("ONBOARD_BASELINE_MANIFEST_INVALID") from exc
        expected_keys = {"schema_version", "component", "sources", "tests",
                         "acs", "consumers", "contracts"}
        if (type(data) is not dict or set(data) != expected_keys
                or any(type(data[k]) is not list
                       for k in ("sources", "tests", "acs", "consumers", "contracts"))):
            raise ValueError("ONBOARD_BASELINE_MANIFEST_SHAPE")
        owned = (path, *data["sources"], *data["tests"], *data["contracts"])
        if not set(owned) <= base_paths:
            raise ValueError("ONBOARD_BASELINE_OWNER_FILES_NOT_COMMITTED")
        # Reuse the same strict data parser on a synthetic *baseline*
        # registration review. No candidate-changed path is introduced.
        checked = validate_component_onboarding_manifest(
            data, manifest_path=path, changed_paths=owned,
            base_paths=tuple(p for g in groups for p in
                             (*g.paths, *g.tests, *g.consumers, *g.contracts)),
            existing_groups=groups, allowed_paths=owned, root=root)
        group = TestGroup(
            checked["name"], checked["tests"], checked["acs"],
            checked["paths"], checked["consumers"],
            (*checked["contracts"], path))
        accepted.append({"group": group, "manifest_path": path,
                         "manifest_sha256": checked["manifest_sha256"]})
        groups = (*groups, group)
    return tuple(accepted)


def registered_action_plan(
    *, repo: Path, base_sha: str, base_files, candidate_files,
    changed_paths: Sequence[str], required_acs: Sequence[str], fallback_argv,
    groups: Sequence, registered_profiles: dict | None, aggregate_sources=(), env=None,
    allowed_paths: Sequence[str] = (),
) -> dict:
    """Translate V2 selected impact to the existing immutable EF01/EF02 evidence contract.

    No new Issue-controlled argv and no broad implicit fallback. Invalid mapping
    produces an UNPROVEN group, causing the trusted runner to stop with a
    verifiable nonzero status, not a silently successful verification.
    """
    import shutil
    import sys
    from .minimum_test import TestGroup, canonical, digest
    import json

    # A fixed ingress profile is a permission to ATTEMPT onboarding, never an
    # authority to use candidate-supplied shell/pytest argv. Load one manifest
    # as data AFTER Codex edits; verify it against the immutable base and the
    # Human-approved allowed_paths. The trusted planner constructs test argv.
    profiles = registered_profiles or {}
    onboarding = (
        profiles.get("mt01-component-onboarding") == list(fallback_argv)
        and bool(allowed_paths)
    )
    admission, onboarding_error = None, None
    persisted_records = (
        load_baseline_component_owner_records(
            root=repo, base_sha=base_sha, existing_groups=groups)
        if onboarding and (repo / ".git").exists() else ())
    persisted_by_name = {item["group"].name: item for item in persisted_records}
    if persisted_records:
        groups = (*groups, *(item["group"] for item in persisted_records))
    if onboarding:
        candidates = sorted(p for p in changed_paths
                            if p.startswith("work_items/mt01-components/") and p.endswith(".json"))
        if len(candidates) != 1:
            if candidates or not persisted_records:
                onboarding_error = "ONBOARD_EXACTLY_ONE_NEW_MANIFEST_REQUIRED"
        else:
            try:
                data = json.loads((repo / candidates[0]).read_text(encoding="utf-8"))
                admission = validate_component_onboarding_manifest(
                    data, manifest_path=candidates[0], changed_paths=changed_paths,
                    base_paths=tuple(base_files), existing_groups=groups,
                    allowed_paths=allowed_paths, root=repo)
            except (ValueError, OSError, UnicodeError, TypeError) as exc:
                onboarding_error = "ONBOARD_INVALID_MANIFEST:" + str(exc)[:128]
        if admission is not None:
            groups = (*groups, TestGroup(
                admission["name"], admission["tests"], admission["acs"],
                admission["paths"], admission["consumers"],
                (*admission["contracts"], admission["manifest_path"])))
    impact = select_impact(
        root=repo, changed_paths=changed_paths, groups=groups,
        base_sha=base_sha,
    )
    by_name = {g.name: g for g in groups}
    entries = []
    problems = list(impact.missing_mapping)
    if onboarding_error:
        problems.append(onboarding_error)
    if admission is not None and aggregate_sources:
        # A candidate manifest can prove one new owner, but it cannot
        # silently inherit sibling verification from a different tree.
        problems.append("ONBOARD_AGGREGATE_NEEDS_INDEPENDENT_FANIN_PROOF")
    known_acs = {ac for group in groups for ac in group.acs}
    # A globally known AC is NOT verified unless its owning group is selected.
    # Do not expand silently: an unrelated action requirement needs a new
    # explicit impact plan or a precise HOLD_COVERAGE witness.
    selected_acs = {ac for name in impact.selected_groups for ac in by_name[name].acs}
    required = set(required_acs)
    unknown_acs = sorted(required - known_acs)
    unselected_acs = sorted((required & known_acs) - selected_acs)
    if unknown_acs:
        problems.extend("AC_UNMAPPED:" + ac for ac in unknown_acs)
    if unselected_acs:
        problems.extend("AC_REQUIRED_GROUP_NOT_SELECTED:" + ac for ac in unselected_acs)
    select_parallel = list(fallback_argv[-2:]) == ["-n", "12"]
    for name in impact.selected_groups:
        group = by_name[name]
        expected = ["python", "-m", "pytest", *group.tests, "-q"]
        if select_parallel:
            expected.extend(["-n", "12"])
        new_owner = bool((admission is not None and name == admission["name"])
                         or (onboarding and name in persisted_by_name))
        verified_owner = (admission if admission is not None and name == admission["name"]
                          else persisted_by_name.get(name))
        matched = ("mt01-component-onboarding" if new_owner else
                   next((key for key, value in sorted(profiles.items()) if value == expected), None))
        if matched is None:
            problems.append(name + ":REGISTERED_PROFILE_REQUIRED")
        # The only dynamic command is generated from a VALIDATED bounded
        # manifest; all original components still use exact registry argv.
        argv = expected if new_owner else list(profiles[matched]) if matched is not None else list(fallback_argv)
        executable = shutil.which(argv[0]) if argv else None
        paths = (set(group.tests) | set(group.paths) | set(group.consumers) |
                 set(group.contracts) | (set(changed_paths) & set(impact.affected_paths)))
        if new_owner:
            # Nonexecutable review documents and work-item records are still
            # changed candidate bytes: include them in immutable evidence so
            # EF02's all-changed-files coverage check cannot be bypassed.
            reviewed_auxiliary = {p for p in changed_paths
                if any(p == prefix or p.startswith(prefix.rstrip("/") + "/")
                       for prefix in allowed_paths)
                and ((p.startswith("docs/engineering/") and p.endswith(".md"))
                     or (p.startswith("work_items/") and p.endswith(".json")))}
            paths.update(reviewed_auxiliary)
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
            **({"onboarding_manifest_path": verified_owner["manifest_path"],
                "onboarding_manifest_sha256": verified_owner["manifest_sha256"],
                "onboarding_data_only": True} if new_owner else {}),
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
    if problems:
        # No partial green subset when a mandatory AC or profile is unproven.
        # The whole action is HOLD, not a sequence of successful groups plus
        # a late warning. Avoid running expensive incomplete pytest groups.
        for entry in entries:
            entry["state"] = "UNPROVEN"
            entry["execute"] = False
            entry["explanation"] = sorted(set(entry["explanation"]) |
                                          {"MT01_V21_COVERAGE_HOLD"})
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
    impact_receipt = impact.as_dict()
    if unselected_acs:
        impact_receipt["mode"] = "HOLD_COVERAGE"
        impact_receipt["missing_mapping"] = sorted(
            set(impact_receipt["missing_mapping"]) |
            {"AC_REQUIRED_GROUP_NOT_SELECTED:" + ac for ac in unselected_acs})
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
        "impact_v21": impact_receipt,
    }
