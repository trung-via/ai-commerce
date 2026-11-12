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
    # Reviewed exact operator-attended D5 script; no generic .ps1 admission.
    "scripts/AI-Commerce-D5-Attended-CDP.ps1": {
        "tests/test_ef02_d5_chrome_acquisition.py", "tests/test_minimum_test.py",
        "tests/test_mt01_v21.py", "tests/test_pr_merge_gate.py"},
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
    # A candidate cannot omit baseline consumer-owner tests merely because
    # the legacy consumer was referenced but not modified in this diff.
    for candidate in candidate_groups:
        if not any(p.startswith("work_items/mt01-components/")
                   for p in candidate.contracts):
            continue
        for consumer in candidate.consumers:
            for old in base_groups:
                if consumer in old.paths:
                    required.update(old.tests)
                    required_group_names.add(old.name)
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


def independently_reviewed_manifest_groups(
    *, repo: Path, changed_paths: set[str], base_paths: set[str],
    base_groups: tuple,
) -> tuple:
    """An independent stdlib baseline check, not candidate planner testimony."""
    reviewed = []
    old_names = {g.name for g in base_groups}
    old_owned = {p for g in base_groups for p in _owned(g)}
    requested = [p for p in changed_paths if p.startswith("work_items/mt01-components/")]
    if len(requested) > 8:
        raise ValueError("ONBOARD_TOO_MANY_MANIFESTS")
    for path in sorted(requested):
        if not path.endswith(".json") or path in base_paths:
            raise ValueError("ONBOARD_MANIFEST_NOT_NEW")
        data = json.loads((repo / path).read_text(encoding="utf-8"))
        required = {"schema_version", "component", "sources", "tests",
                    "acs", "consumers", "contracts"}
        if type(data) is not dict or set(data) != required:
            raise ValueError("ONBOARD_MANIFEST_UNTRUSTED_FIELDS")
        name = data["component"]
        if (type(name) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", name)
                or path != f"work_items/mt01-components/{name}.json"
                or name in old_names
                or data["schema_version"] != "MT01ComponentOwner.v1"):
            raise ValueError("ONBOARD_MANIFEST_ID_OR_VERSION")
        names = {}
        for key in ("sources", "tests", "acs", "consumers", "contracts"):
            values = data[key]
            if (type(values) is not list or len(values) > 40
                    or (key in {"sources", "tests", "acs"} and not values)
                    or any(type(v) is not str or not v or len(v) > 240 for v in values)
                    or len(values) != len(set(values))):
                raise ValueError("ONBOARD_MANIFEST_INVALID_" + key)
            names[key] = tuple(values)
        sources, tests, acs = (names[k] for k in ("sources", "tests", "acs"))
        consumers, contracts = names["consumers"], names["contracts"]
        if (any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", a) for a in acs)
                or any(not p.startswith("src/ai_commerce/") or not p.endswith(".py")
                       or p.startswith(("src/ai_commerce/engineering/", "src/ai_commerce/remote/"))
                       for p in sources)
                or any(not re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py", p) for p in tests)
                or any(not p.startswith("contracts/") or not p.endswith(".schema.json") for p in contracts)):
            raise ValueError("ONBOARD_MANIFEST_INVALID_PATHS")
        additions = set(sources) | set(tests)
        if (additions & (base_paths | old_owned)
                or not additions <= changed_paths
                or any(p not in old_owned and p not in sources for p in consumers)
                or any(p not in old_owned and p not in changed_paths for p in contracts)):
            raise ValueError("ONBOARD_MANIFEST_OWNER_OR_CONSUMER_UNPROVEN")
        for source in (*sources, *tests, *contracts):
            file = repo / source
            if not file.is_file() or file.is_symlink() or repo.resolve() not in file.resolve().parents:
                raise ValueError("ONBOARD_MANIFEST_FILE_MISSING_OR_LINKED")
        if not (repo / path).is_file() or (repo / path).is_symlink():
            raise ValueError("ONBOARD_MANIFEST_LINKED")
        reviewed.append(types.SimpleNamespace(
            name=name, tests=tests, paths=sources, acs=acs, consumers=consumers,
            contracts=(*contracts, path)))
        old_names.add(name)
        old_owned.update((*sources, *tests, *contracts, path))
    return tuple(reviewed)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    ).stdout



def _independent_baseline_manifest_dependency_order(paths, repo, base_sha):
    """Independently check Git-owned component edges before owner admission.

    No candidate planner import or candidate-owned dependency hints.
    Cycles, duplicate source ownership, malformed data fail closed.
    """
    docs = {}
    owners = {}
    for path in paths:
        raw = _git(repo, "show", f"{base_sha}:{path}")
        if len(raw) > 32768:
            raise ValueError("BASELINE_OWNER_MANIFEST_UNAVAILABLE")
        try:
            item = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("BASELINE_OWNER_MANIFEST_INVALID") from exc
        if (type(item) is not dict or type(item.get("sources")) is not list
                or type(item.get("consumers")) is not list
                or any(type(v) is not str for v in item["sources"])
                or any(type(v) is not str for v in item["consumers"])):
            raise ValueError("BASELINE_OWNER_DEPENDENCY_SHAPE")
        docs[path] = item
        for source in item["sources"]:
            if source in owners:
                raise ValueError("BASELINE_OWNER_DUPLICATE_SOURCE")
            owners[source] = path
    pending = set(paths)
    resolved = []
    while pending:
        ready = sorted(
            p for p in pending
            if not any(
                owners.get(consumer) in pending and owners.get(consumer) != p
                for consumer in docs[p]["consumers"]
            )
        )
        if not ready:
            raise ValueError("BASELINE_OWNER_DEPENDENCY_CYCLE")
        next_path = ready[0]
        pending.remove(next_path)
        resolved.append(next_path)
    return tuple(resolved)


def trusted_baseline_manifest_groups(*, repo: Path, base_sha: str,
                                     base_groups: tuple) -> tuple:
    """Independent stdlib review of persisted owners from *base Git blobs*.

    No candidate Python import, executable manifest or arbitrary test argv.
    The new component's first PR is authenticated by the merge gate; later
    PRs must preserve the committed manifest verbatim.
    """
    if not SHA.fullmatch(base_sha):
        raise ValueError("BASELINE_OWNER_BAD_SHA")
    base_paths = {
        p.decode("utf-8") for p in
        _git(repo, "ls-tree", "-r", "--name-only", "-z", base_sha).split(bytes([0])) if p
    }
    paths = sorted(p for p in base_paths if p.startswith("work_items/mt01-components/"))
    if len(paths) > 256:
        raise ValueError("BASELINE_OWNER_LIMIT")
    paths = _independent_baseline_manifest_dependency_order(paths, repo, base_sha)
    accepted = []
    names = {g.name for g in base_groups}
    owned = {p for g in base_groups for p in _owned(g)}
    for path in paths:
        if not re.fullmatch(r"work_items/mt01-components/[a-z][a-z0-9_-]{1,63}\.json", path):
            raise ValueError("BASELINE_OWNER_BAD_MANIFEST_PATH")
        raw = _git(repo, "show", f"{base_sha}:{path}")
        target = repo / path
        if (len(raw) > 32768 or not target.is_file() or target.is_symlink()):
            raise ValueError("BASELINE_OWNER_MANIFEST_UNAVAILABLE")
        actual = target.read_bytes()
        if actual != raw and actual.replace(bytes([13, 10]), bytes([10])) != raw:
            raise ValueError("BASELINE_OWNER_MANIFEST_CHANGED")
        data = json.loads(raw.decode("utf-8-sig"))
        keys = {"schema_version", "component", "sources", "tests",
                "acs", "consumers", "contracts"}
        if type(data) is not dict or set(data) != keys:
            raise ValueError("BASELINE_OWNER_BAD_SHAPE")
        name = data["component"]
        if (type(name) is not str or path != f"work_items/mt01-components/{name}.json"
                or name in names or data["schema_version"] != "MT01ComponentOwner.v1"):
            raise ValueError("BASELINE_OWNER_CONFLICT")
        items = {}
        for key in ("sources", "tests", "acs", "consumers", "contracts"):
            value = data[key]
            if (type(value) is not list or len(value) > 40
                    or (key in ("sources", "tests", "acs") and not value)
                    or len(value) != len(set(value))
                    or any(type(v) is not str or not v or len(v) > 240 for v in value)):
                raise ValueError("BASELINE_OWNER_INVALID_" + key)
            items[key] = tuple(value)
        src, tests, acs = (items[k] for k in ("sources", "tests", "acs"))
        consumers, contracts = items["consumers"], items["contracts"]
        if (any(not p.startswith("src/ai_commerce/") or not p.endswith(".py")
                or p.startswith(("src/ai_commerce/remote/", "src/ai_commerce/engineering/"))
                or ".." in p.split("/") for p in src)
                or any(not re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py", p) for p in tests)
                or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", a) for a in acs)
                or any(not p.startswith("contracts/") or not p.endswith(".schema.json")
                       or ".." in p.split("/") for p in contracts)
                or any(not p.startswith("src/ai_commerce/") or not p.endswith(".py")
                       or ".." in p.split("/") for p in consumers)):
            raise ValueError("BASELINE_OWNER_UNSAFE_PATHS")
        fresh = set(src) | set(tests)
        if (fresh & owned or not (fresh | set(contracts) | {path}) <= base_paths
                or any(p not in owned and p not in fresh for p in consumers)):
            raise ValueError("BASELINE_OWNER_CLOSURE_MISSING")
        for p in fresh | set(contracts):
            target = repo / p
            if not target.is_file() or target.is_symlink() or repo.resolve() not in target.resolve().parents:
                raise ValueError("BASELINE_OWNER_TEST_OR_SOURCE_MISSING")
        item = types.SimpleNamespace(
            name=name, paths=src, tests=tests, acs=acs,
            consumers=consumers, contracts=(*contracts, path))
        names.add(name)
        owned.update((*src, *tests, *contracts, path))
        accepted.append(item)
    return tuple(accepted)


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
        groups_base = (*baseline_groups(base_text),
                       *trusted_baseline_manifest_groups(
                           repo=repo, base_sha=base,
                           base_groups=baseline_groups(base_text)))
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
        base_paths = set(_git(repo, "ls-tree", "-r", "--name-only", base).decode("utf-8").splitlines())
        groups_from_manifest = independently_reviewed_manifest_groups(
            repo=repo, changed_paths=changed_paths,
            base_paths=base_paths, base_groups=groups_base)
        good, reasons = check_selection(
            plan, base_groups=groups_base,
            candidate_groups=(*groups_candidate, *groups_from_manifest),
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
