"""AI Commerce automated merge gate for a pinned, owner-backed Brain PASS.

This runs from protected main, not candidate-controlled PR code. A canonical
AI Commerce Brain PASS decision must have been emitted to GitHub through the
owner's authenticated connector after actual review; no generic CI success
or free-form 'PASS' text authorizes the merge.
The GitHub identity authenticates the emitter, not the underlying model:
model provenance is an explicit limitation of owner-backed review comments.
"""
from __future__ import annotations

import ast
import base64
import binascii
import hashlib
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
    # Reviewed hosted queue ONLY: no broker token, checkout or laptop job.
    ".github/workflows/hostops-v3.yml":
        ("tests/test_hostops_v3_integration.py", "tests/test_pr_merge_gate.py"),
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
    # Explicit offline-only HostOps v4 Windows PLAN bootstrap. No service,
    # broker token, signed write, ACL mutation or installer execution path.
    "scripts/HostOps-V4-InstallPlan.ps1": (
        "tests/test_hostops_v3_integration.py", "tests/test_minimum_test.py"),
    "config/hostops-v3/config-v4.example.json": (
        "tests/test_hostops_v3_integration.py", "tests/test_minimum_test.py"),
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


# A newly introduced component cannot exist in main's owner registry yet.
# Promote ONLY a declarative, additive TestGroup from the pinned PR HEAD.
# A candidate is never imported/executed, and cannot amend any existing owner,
# baseline logic, or privileged .github paths.
ONBOARDING_REGISTRY_PATH = "src/ai_commerce/engineering/minimum_test.py"
NEW_COMPONENT_MAX_GROUPS = 8
NEW_COMPONENT_MAX_SOURCE_BYTES = 256 * 1024
SAFE_COMPONENT = re.compile(r"[a-z][a-z0-9_-]{1,63}\Z")
SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SAFE_PACKAGE = re.compile(r"[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*\Z")


def _registrations(source: str):
    tree = ast.parse(source)
    found = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name == "approved_groups"]
    if len(found) != 1 or not isinstance(found[0].body[-1], ast.Return):
        raise ValueError("REGISTRY_FUNCTION_INVALID")
    returned = found[0].body[-1].value
    if not isinstance(returned, ast.Tuple):
        raise ValueError("REGISTRY_STATIC_RETURN_REQUIRED")
    by_name = {}
    for item in returned.elts:
        if (not isinstance(item, ast.Call) or not isinstance(item.func, ast.Name)
                or item.func.id != "TestGroup" or not item.args
                or not isinstance(item.args[0], ast.Constant)
                or not isinstance(item.args[0].value, str)):
            raise ValueError("REGISTRY_GROUP_SHAPE_INVALID")
        name = item.args[0].value
        if name in by_name:
            raise ValueError("DUPLICATE_REGISTRY_OWNER")
        by_name[name] = item
    return tree, returned, by_name


def _static_strings(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.Tuple, ast.List)):
        raise ValueError("NEW_GROUP_REQUIRES_LITERAL_LIST")
    if any(not isinstance(item, ast.Constant) or type(item.value) is not str
           for item in node.elts):
        raise ValueError("NEW_GROUP_NONLITERAL_PATH")
    values = tuple(item.value for item in node.elts)
    if len(values) != len(set(values)) or len(values) > 40:
        raise ValueError("NEW_GROUP_DUPLICATE_OR_OVERSIZED_PATHS")
    return values


def _declared_paths(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, (ast.Tuple, ast.List)):
        return _static_strings(node)
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
        return _declared_paths(node.left) + _declared_paths(node.right)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"sources", "contracts"} and not node.keywords
            and len(node.args) == (2 if node.func.id == "sources" else 1)
            and all(isinstance(x, ast.Constant) and type(x.value) is str for x in node.args)):
        if node.func.id == "sources":
            package, names = (x.value for x in node.args)
            if not SAFE_PACKAGE.fullmatch(package):
                raise ValueError("NEW_GROUP_BAD_PACKAGE")
            prefix, suffix = "src/ai_commerce/" + package + "/", ".py"
        else:
            names = node.args[0].value
            prefix, suffix = "contracts/", ".schema.json"
        values = names.split()
        if not values or len(values) > 40 or any(not SAFE_NAME.fullmatch(x) for x in values):
            raise ValueError("NEW_GROUP_BAD_STATIC_SOURCE_NAMES")
        return tuple(prefix + name + suffix for name in values)
    raise ValueError("NEW_GROUP_UNSUPPORTED_DECLARATION")


def _new_group(node: ast.Call):
    if len(node.args) != 4:
        raise ValueError("NEW_GROUP_INVALID_ARGUMENTS")
    name = node.args[0].value
    if not SAFE_COMPONENT.fullmatch(name):
        raise ValueError("NEW_GROUP_BAD_NAME")
    tests, acs, paths = (_declared_paths(node.args[i]) for i in (1, 2, 3))
    kwargs = {}
    for item in node.keywords:
        if item.arg not in {"consumers", "contracts"} or item.arg in kwargs:
            raise ValueError("NEW_GROUP_UNSUPPORTED_KEYWORD")
        kwargs[item.arg] = _declared_paths(item.value)
    if (not tests or not acs or not paths or len(paths) != len(set(paths))
            or any(not p.startswith("tests/test_") or not p.endswith(".py") for p in tests)
            or any(not p.startswith("src/ai_commerce/") or not p.endswith(".py") for p in paths)):
        raise ValueError("NEW_GROUP_INVALID_TEST_OR_SOURCE")
    for part in (tests, paths, kwargs.get("consumers", ()), kwargs.get("contracts", ())):
        if any(not p or p.startswith("/") or "\\" in p or ".." in p.split("/") for p in part):
            raise ValueError("NEW_GROUP_UNSAFE_PATH")
    return {"name": name, "tests": tests, "acs": acs, "paths": paths,
            "consumers": kwargs.get("consumers", ()),
            "contracts": kwargs.get("contracts", ())}


def review_new_component_registry(main_source: str, candidate_source: str,
                                  changed_files: list, existing_groups: tuple) -> tuple[dict, ...]:
    """Review added owner declarations as data; absolutely no candidate Python exec."""
    if len(candidate_source.encode("utf-8")) > NEW_COMPONENT_MAX_SOURCE_BYTES:
        raise ValueError("NEW_COMPONENT_REGISTRY_TOO_LARGE")
    main_tree, main_return, original = _registrations(main_source)
    candidate_tree, new_return, updated = _registrations(candidate_source)
    old_names = list(original)
    if [name for name in updated if name in original] != old_names:
        raise ValueError("EXISTING_OWNER_ORDER_CHANGED")
    additions = [name for name in updated if name not in original]
    if not additions or len(additions) > NEW_COMPONENT_MAX_GROUPS:
        raise ValueError("NO_BOUNDED_NEW_COMPONENT_OWNERS")
    # Remove ONLY the new owner calls, then AST equality proves no modification
    # to any old registration or to the rest of the protected MT-01 code.
    new_return.elts = [item for item in new_return.elts if item.args[0].value in original]
    if ast.dump(main_tree, include_attributes=False) != ast.dump(candidate_tree, include_attributes=False):
        raise ValueError("EXISTING_TRUSTED_REGISTRY_MUTATED")
    changes = {f["filename"]: f["status"] for f in changed_files}
    if changes.get(ONBOARDING_REGISTRY_PATH) != "modified":
        raise ValueError("CANDIDATE_OWNER_REGISTRY_NOT_CHANGED")
    existing_tests = {path for group in existing_groups for path in group.tests}
    existing_owned = {path for group in existing_groups
                      for path in (*group.paths, *group.tests, *group.consumers, *group.contracts)}
    fresh_tests, fresh_paths = set(), set()
    entries = []
    for name in additions:
        entry = _new_group(updated[name])
        if (set(entry["tests"]) & (existing_tests | fresh_tests)
                or set(entry["paths"]) & (existing_owned | fresh_paths)):
            raise ValueError("NEW_GROUP_STEALS_EXISTING_OWNER")
        if any(changes.get(path) != "added" for path in (*entry["tests"], *entry["paths"])):
            raise ValueError("NEW_GROUP_TEST_OR_SOURCE_NOT_ADDED")
        # Existing consumers must be backed by the reviewed main registry;
        # new consumers/contracts must be explicitly added with the candidate.
        if any(path not in existing_owned and changes.get(path) != "added"
               for path in (*entry["consumers"], *entry["contracts"])):
            raise ValueError("NEW_GROUP_UNPROVEN_CONSUMER_OR_CONTRACT")
        fresh_tests.update(entry["tests"])
        fresh_paths.update(entry["paths"])
        entries.append(entry)
    return tuple(entries)


def review_existing_owner_source_additions(main_source: str, candidate_source: str,
                                           changed_files: list,
                                           existing_groups: tuple) -> tuple[dict, ...]:
    """Admit ONLY appended, newly added engineering Python source in an old owner.

    The candidate is AST-parsed but never imported. All old owner tests, ACs,
    consumers and contracts must remain byte-for-AST unchanged. This is a
    generic owner extension, not a one-off exception for an Issue or path.
    """
    if len(candidate_source.encode("utf-8")) > NEW_COMPONENT_MAX_SOURCE_BYTES:
        raise ValueError("EXISTING_OWNER_REGISTRY_TOO_LARGE")
    original_tree, _, originals = _registrations(main_source)
    candidate_tree, _, updated = _registrations(candidate_source)
    if list(originals) != list(updated):
        raise ValueError("EXISTING_OWNER_IDENTITY_OR_ORDER_CHANGED")
    changes = {f.get("filename"): f.get("status") for f in changed_files
               if isinstance(f, dict)}
    if changes.get(ONBOARDING_REGISTRY_PATH) != "modified":
        raise ValueError("EXISTING_OWNER_REGISTRY_NOT_CHANGED")
    trusted = {g.name: g for g in existing_groups}
    owned = {p for g in existing_groups
             for p in (*g.paths, *g.tests, *g.consumers, *g.contracts)}
    if set(trusted) != set(originals):
        raise ValueError("TRUSTED_OWNER_MAP_MISMATCH")
    entries = []
    additions = set()
    for name, before in originals.items():
        after = updated[name]
        if ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False):
            continue
        if (len(before.args) != len(after.args)
                or len(before.args) < 4
                or [ast.dump(x, include_attributes=False) for x in before.keywords] !=
                   [ast.dump(x, include_attributes=False) for x in after.keywords]
                or any(ast.dump(x, include_attributes=False) !=
                       ast.dump(y, include_attributes=False)
                       for i, (x, y) in enumerate(zip(before.args, after.args)) if i != 3)):
            raise ValueError("EXISTING_OWNER_TEST_AC_OR_CONSUMER_CHANGED")
        old_paths, new_paths = before.args[3], after.args[3]
        if (not isinstance(old_paths, ast.Tuple) or not isinstance(new_paths, ast.Tuple)
                or len(new_paths.elts) <= len(old_paths.elts)
                or any(ast.dump(x, include_attributes=False) != ast.dump(y, include_attributes=False)
                       for x, y in zip(old_paths.elts, new_paths.elts))):
            raise ValueError("EXISTING_OWNER_PATHS_NOT_APPEND_ONLY")
        fresh = []
        for node in new_paths.elts[len(old_paths.elts):]:
            if (not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add)
                    or not isinstance(node.left, ast.Name) or node.left.id != "eng"
                    or not isinstance(node.right, ast.Constant)
                    or type(node.right.value) is not str
                    or not SAFE_NAME.fullmatch(node.right.value.removesuffix(".py"))
                    or not node.right.value.endswith(".py")):
                raise ValueError("EXISTING_OWNER_NEW_SOURCE_NOT_STATIC_ENGINEERING_PYTHON")
            path = "src/ai_commerce/engineering/" + node.right.value
            if path in owned or changes.get(path) != "added":
                raise ValueError("EXISTING_OWNER_SOURCE_NOT_NEWLY_ADDED")
            fresh.append(path)
            additions.add(path)
        if len(fresh) != len(set(fresh)) or not fresh:
            raise ValueError("EXISTING_OWNER_DUPLICATE_NEW_SOURCE")
        entries.append({"name": name, "paths": tuple(fresh),
                        "tests": tuple(trusted[name].tests), "contracts": ()})
        # Undo only trusted additive owner-path edits; every other statement
        # in approved_groups must remain exactly the same AST.
        after.args[3] = old_paths
    if not entries or len(entries) > NEW_COMPONENT_MAX_GROUPS or len(additions) > 16:
        raise ValueError("NO_BOUNDED_EXISTING_OWNER_ADDITIONS")
    main_fn = next(n for n in original_tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "approved_groups")
    candidate_fn = next(n for n in candidate_tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "approved_groups")
    if ast.dump(main_fn, include_attributes=False) != ast.dump(candidate_fn, include_attributes=False):
        raise ValueError("EXISTING_OWNER_UNRELATED_REGISTRY_MUTATION")
    return tuple(entries)


def _read_candidate_registry(api: Callable, base: str, head: str) -> str:
    path = f"{base}/contents/{ONBOARDING_REGISTRY_PATH}?ref={head}"
    response = api(path)
    if (not isinstance(response, dict) or response.get("type") != "file"
            or response.get("encoding") != "base64"
            or not isinstance(response.get("content"), str)
            or not isinstance(response.get("sha"), str)):
        raise ValueError("NEW_GROUP_GITHUB_FILE_UNPROVEN")
    try:
        data = base64.b64decode(response["content"], validate=False)
        if len(data) > NEW_COMPONENT_MAX_SOURCE_BYTES:
            raise ValueError("NEW_GROUP_SOURCE_TOO_LARGE")
        # A real Git blob hash, not merely a self-described file label.
        expected = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if expected != response["sha"]:
            raise ValueError("NEW_GROUP_GITHUB_BLOB_INTEGRITY_FAILED")
        return data.decode("utf-8")
    except (binascii.Error, UnicodeError) as exc:
        raise ValueError("NEW_GROUP_GITHUB_FILE_INVALID") from exc


def onboarded_groups_for_pr(api: Callable, base: str, head: str, changed_files: list):
    changes = {f.get("filename"): f.get("status") for f in changed_files
               if isinstance(f, dict)}
    if (changes.get(ONBOARDING_REGISTRY_PATH) != "modified"
            or not any(status == "added" and isinstance(path, str)
                       and path.startswith("src/ai_commerce/") and path.endswith(".py")
                       for path, status in changes.items())):
        return ()
    main_source = (Path(__file__).resolve().parents[2] / ONBOARDING_REGISTRY_PATH).read_text(encoding="utf-8")
    candidate_source = _read_candidate_registry(api, base, head)
    try:
        return review_new_component_registry(main_source, candidate_source,
                                             changed_files, approved_groups())
    except ValueError:
        return review_existing_owner_source_additions(
            main_source, candidate_source, changed_files, approved_groups())


def reviewed_coverage_for_changed_files(files: object, *, onboarded_groups=()):
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
    newly_owned = {}
    for group in onboarded_groups:
        for path in (*group["paths"], *group["tests"], *group["contracts"]):
            newly_owned.setdefault(path, set()).update(group["tests"])
    for path in changed:
        owning = [g for g in groups if path in
                  (set(g.paths) | set(g.tests) | set(g.consumers) | set(g.contracts))]
        for group in owning:
            required.update(group.tests)
        new_group_tests = newly_owned.get(path)
        if new_group_tests is not None:
            if next((e["status"] for e in files if e["filename"] == path), None) != "added":
                return None
            required.update(new_group_tests)
        if (not owning and new_group_tests is None and path not in REVIEWED_CONTROL_TESTS and
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
    if reviewed is None and isinstance(changed_files, list):
        try:
            onboarded = onboarded_groups_for_pr(api, base, head, changed_files)
            if onboarded:
                reviewed = reviewed_coverage_for_changed_files(
                    changed_files, onboarded_groups=onboarded)
        except (ValueError, SyntaxError, OSError, TypeError, KeyError, urllib.error.URLError):
            reviewed = None
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
        except urllib.error.HTTPError as exc:
            return Verdict("WAIT_EVIDENCE", f"V21_ARTIFACT_HTTP_{exc.code}", number, head, latest["id"])
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
        """Require GitHub's exact ZIP redirect; never forward the token to storage.

        GitHub serves Actions artifact bytes from temporary signed storage,
        not from api.github.com. Python's default redirect handling can carry
        Authorization across hosts. We follow one strictly allowlisted HTTPS
        storage redirect without any bearer token, preserving artifact bytes.
        """
        if type(artifact_id) is not int or artifact_id < 1:
            raise ValueError("INVALID_ARTIFACT_ID")
        path = f"/repos/{self.repository}/actions/artifacts/{artifact_id}/zip"
        req = urllib.request.Request(
            "https://api.github.com" + path,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        class NoFollow(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoFollow)
        try:
            with opener.open(req, timeout=18) as response:
                # An unexpected direct body is not proof of an artifact ZIP.
                raise ValueError("ARTIFACT_SIGNED_REDIRECT_MISSING")
        except urllib.error.HTTPError as exc:
            if exc.code not in (302, 303, 307):
                raise
            signed = exc.headers.get("Location")
        if not isinstance(signed, str):
            raise ValueError("ARTIFACT_REDIRECT_MISSING")
        parts = urllib.parse.urlsplit(signed)
        host = (parts.hostname or "").lower()
        approved = (
            host == "actions.githubusercontent.com" or
            host.endswith(".actions.githubusercontent.com") or
            host == "objects.githubusercontent.com" or
            host.endswith(".blob.core.windows.net")
        )
        if (parts.scheme != "https" or not approved or not parts.path
                or parts.username is not None or parts.password is not None
                or parts.fragment):
            raise ValueError("ARTIFACT_REDIRECT_HOST_INVALID")
        # A signed download URL must never inherit an API credential.
        signed_req = urllib.request.Request(
            signed, headers={"Accept": "application/zip",
                             "User-Agent": "AI-Commerce-Merge-Gate/1.0"})
        with urllib.request.urlopen(signed_req, timeout=18) as response:
            content = response.read(24 * 1024 * 1024 + 1)
        if len(content) > 24 * 1024 * 1024:
            raise ValueError("ARTIFACT_TOO_LARGE")
        if not content.startswith(b"PK"):
            raise ValueError("ARTIFACT_NOT_A_ZIP")
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
