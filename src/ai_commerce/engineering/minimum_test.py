"""MT-01: finite, trusted test groups, dependency closure and attempt-local evidence.

This is deliberately conservative. Import discovery supplements the explicit AC,
consumer and contract map; it never invents coverage. Unknown paths/ACs use the
approved broad profile. No disk cache is searched or written by this module.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
from typing import Iterable, Mapping, Sequence


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_IGNORED = {".git", ".local", ".venv", "venv", "node_modules", "__pycache__",
            ".pytest_cache", ".ruff_cache", "build", "dist"}


def file_hashes(repo: Path) -> dict[str, str]:
    """Include additions/deletions and config/fixtures, without following symlinks."""
    result = {}
    for directory, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _IGNORED and not d.endswith(".egg-info"))
        for name in sorted(files + [d for d in dirs if (Path(directory) / d).is_symlink()]):
            path = Path(directory) / name
            relative = path.relative_to(repo).as_posix()
            # Resolving a symlink ancestor could read outside the authorized tree.
            if path.is_symlink():
                result[relative] = "SYMLINK:" + os.readlink(path)
            else:
                result[relative] = digest(path.read_bytes())
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
    return result


@dataclass(frozen=True)
class TestGroup:
    name: str
    tests: tuple[str, ...]
    acs: tuple[str, ...]
    paths: tuple[str, ...]
    consumers: tuple[str, ...] = ()
    contracts: tuple[str, ...] = ()

    def mapping(self) -> dict:
        return {key: list(getattr(self, key)) for key in ("tests", "acs", "paths", "consumers", "contracts")}


def approved_groups() -> tuple[TestGroup, ...]:
    """Code-reviewed AC/consumer/contract map, never supplied by an executor."""
    eng = "src/ai_commerce/engineering/"
    rem = "src/ai_commerce/remote/"
    return (
        TestGroup("ef01", ("tests/test_ef01.py",), ("EF01.execution",),
                  (eng + "runner.py", eng + "adapters.py", eng + "profile.py", eng + "cli.py"),
                  (rem + "worker.py",)),
        TestGroup("repair", ("tests/test_ef01_repair.py",), ("EF01.repair",),
                  (eng + "runner.py",), (rem + "worker.py", rem + "scheduler.py")),
        TestGroup("integrity", ("tests/test_ef01_ef02_integrity.py",), ("EF01.source-integrity",),
                  (eng + "runner.py", rem + "transport.py"), (rem + "worker.py",)),
        TestGroup("provider", ("tests/test_ef01_provider_error_payload.py",), ("EF01.provider-boundary",),
                  (eng + "runner.py",), (rem + "worker.py",)),
        TestGroup("ef02", ("tests/test_ef02.py",), ("EF02.action-and-fanin",),
                  (rem + "worker.py", rem + "validation.py", rem + "scheduler.py", rem + "reuse.py"),
                  (rem + "__main__.py",), ("contracts/RemoteActionBatch.v1.schema.json",
                    ".github/ef02/verify-profiles.json", ".github/ef02/approved-candidates.v1.json")),
        TestGroup("mt01", ("tests/test_minimum_test.py",), ("MT01.selection-and-evidence",),
                  (eng + "minimum_test.py", eng + "verification.py"), (eng + "runner.py", rem + "worker.py")),
        TestGroup("ef01p", ("tests/test_ef01_performance.py",), ("EF01P.git-and-host-budget",),
                  (eng + "git.py", eng + "pytest_profile.py"), (eng + "runner.py", rem + "worker.py")),
    )


def import_graph(repo: Path, files: Mapping[str, str]) -> tuple[dict[str, set[str]], bool]:
    """Resolve local absolute/relative import edges, including from-package imports.

    Dynamic imports or unparsable Python make a narrow proof incomplete. Full
    registered execution remains available. Explicit contracts cover non-Python
    interfaces; undisclosed file I/O cannot be inferred from a test name.
    """
    modules = {}
    for path in files:
        if path.endswith(".py"):
            module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
            modules[module.removesuffix(".__init__")] = path
            if path.startswith("tests/"):
                modules.setdefault(Path(path).stem, path)
    graph: dict[str, set[str]] = {}
    complete = True
    for path in sorted(set(modules.values())):
        if str(files[path]).startswith("SYMLINK:"):
            complete = False
            continue
        try:
            tree = ast.parse((repo / path).read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeError):
            complete = False
            continue
        edges: set[str] = set()
        module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        package = module.removesuffix(".__init__") if path.endswith("/__init__.py") else module.rpartition(".")[0]
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    parts = package.split(".")
                    prefix = ".".join(parts[:len(parts) - node.level + 1] + ([prefix] if prefix else []))
                names = [prefix] + [prefix + "." + alias.name for alias in node.names]
            elif isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "__import__" or
                isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            ):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    names = [node.args[0].value]
                else:
                    complete = False
            for name in names:
                while name:
                    if name in modules:
                        edges.add(modules[name])
                    name = name.rpartition(".")[0]
        graph[path] = edges
    return graph, complete


def _closure(seeds: Iterable[str], graph: Mapping[str, set[str]]) -> set[str]:
    found = set(seeds)
    pending = list(found)
    while pending:
        for dependency in graph.get(pending.pop(), ()):
            if dependency not in found:
                found.add(dependency)
                pending.append(dependency)
    return found


def environment() -> dict:
    # Hash values instead of publishing secrets, tokens or private PATH entries.
    return {
        "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
        "python_flags": repr(sys.flags), "implementation": platform.python_implementation(),
        "dependencies": sorted((d.metadata["Name"].lower(), d.version) for d in importlib.metadata.distributions()
                               if d.metadata["Name"]),
        "environment_sha256": digest(canonical(dict(os.environ)).encode()),
    }


@dataclass(frozen=True)
class Evidence:
    """Canonical serialized bytes cannot be mutated through a nested dict."""
    payload: str
    sha256: str

    @classmethod
    def from_success(cls, binding: dict, *, exit_code: int, stdout: bytes,
                     artifacts: Mapping[str, str], evidence_root: Path) -> Evidence:
        if exit_code != 0 or not re.search(rb"\b[1-9][0-9]* passed\b", stdout):
            raise ValueError("MT01_SUCCESSFUL_NONEMPTY_PYTEST_REQUIRED")
        if not artifacts:
            raise ValueError("MT01_IMMUTABLE_ARTIFACTS_REQUIRED")
        refs = {}
        for name, relative in artifacts.items():
            path = (evidence_root / relative).resolve()
            if evidence_root.resolve() not in path.parents or not path.is_file():
                raise ValueError("MT01_EVIDENCE_OUTSIDE_ATTEMPT")
            refs[name] = {"path": relative, "sha256": digest(path.read_bytes())}
        if refs.get("stdout", {}).get("sha256") != digest(stdout):
            raise ValueError("MT01_STDOUT_ARTIFACT_MISMATCH")
        payload = canonical({"binding": binding, "exit_code": 0, "nonempty": True, "artifacts": refs})
        return cls(payload, digest(payload.encode()))

    def valid_for(self, binding: dict, evidence_root: Path) -> bool:
        try:
            value = json.loads(self.payload)
            if (digest(self.payload.encode()) != self.sha256 or value["binding"] != binding or
                    value["exit_code"] != 0 or value["nonempty"] is not True or not value["artifacts"] or
                    binding.get("environment_proven") is not True):
                return False
            for ref in value["artifacts"].values():
                path = (evidence_root / ref["path"]).resolve()
                if evidence_root.resolve() not in path.parents or digest(path.read_bytes()) != ref["sha256"]:
                    return False
            return True
        except (ValueError, KeyError, TypeError, OSError):
            return False


def select_plan(*, repo: Path, base_sha: str, base_files: Mapping[str, str],
                candidate_files: Mapping[str, str], changed_paths: Sequence[str],
                required_acs: Sequence[str], fallback_argv: Sequence[str],
                groups: Sequence[TestGroup] | None = None, registered_only: bool = False,
                aggregate_sources: Sequence[str] = (), env: dict | None = None,
                registered_profiles: Mapping[str, Sequence[str]] | None = None) -> dict:
    groups = tuple(approved_groups() if groups is None else groups)
    graph, imports_complete = import_graph(repo, candidate_files)
    global_files = {p for p in set(base_files) | set(candidate_files) if (
        Path(p).name in ("conftest.py", "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "setup.py") or
        Path(p).name.startswith(("requirements", "constraints")) or p.endswith((".lock", ".pth")))}
    closures = {g.name: _closure((*g.paths, *g.tests, *g.consumers, *g.contracts), graph) | global_files
                for g in groups}
    selected = [g for g in groups if set(changed_paths) & closures[g.name] or set(required_acs) & set(g.acs)]
    coverage = set().union(*(set(g.acs) for g in selected)) if selected else set()
    known = set().union(*(closures[g.name] for g in selected)) if selected else set()
    affected = _closure(changed_paths, {p: {c for c, deps in graph.items() if p in deps} for p in graph})
    covered_tests = {t for g in selected for t in g.tests}
    unknown_tests = {p for p in affected if Path(p).name.startswith("test_")} - covered_tests
    registered = {}
    for group in selected:
        for name, argv in sorted((registered_profiles or {}).items()):
            if (list(argv[:3]) in (["python", "-m", "pytest"], ["py", "-m", "pytest"]) and
                    list(argv[3:]) in (list(group.tests) + ["-q"], list(group.tests) + ["-q", "-n", "12"])):
                registered[group.name] = (name, list(argv))
                break
    reasons = []
    if registered_only and any(g.name not in registered for g in selected):
        reasons.append("REGISTERED_ARGV_ONLY_COMPANION_REGISTRATION_REQUIRED")
    if aggregate_sources:
        reasons.append("AGGREGATE_REQUIRES_FRESH_COMBINED_CONSUMER_COVERAGE")
    if not imports_complete or unknown_tests:
        reasons.append("IMPORT_OR_CONSUMER_MAP_INCOMPLETE")
    if set(changed_paths) - known or set(required_acs) - coverage or not selected:
        reasons.append("PATH_OR_AC_MAP_INCOMPLETE")
    if any(not all(p in candidate_files for p in g.tests) for g in selected):
        reasons.append("TEST_DEFINITION_MISSING")
    env = environment() if env is None else env
    entries = []
    scope_args = list(fallback_argv[3:])
    if registered_only and scope_args[-2:] == ["-n", "12"]:
        scope_args = scope_args[:-2]
    fallback_proven_scope = list(fallback_argv[1:3]) == ["-m", "pytest"] and scope_args in (
        ["tests", "-q"], ["tests"], [".", "-q"], ["."])
    if reasons:
        # An approved full profile is a conservative proof for all applicable ACs.
        # Caller must supply the broader approved scope; no command is synthesized.
        groups = (TestGroup("registered-fallback", (), tuple(sorted(set(required_acs) | coverage)), ()),)
        selected = list(groups)
    for group in selected:
        files = set(base_files) | set(candidate_files) if reasons else closures[group.name]
        argv = list(fallback_argv) if reasons else [sys.executable, "-m", "pytest", *group.tests, "-q"]
        registration = registered.get(group.name) if registered_only and not reasons else None
        if registration:
            argv = registration[1]
        executable = shutil.which(argv[0]) if argv else None
        binding = {
            "version": 1, "base_sha": base_sha, "aggregate_sources": list(aggregate_sources),
            "workspace": str(repo.resolve()),
            "base_files": {p: base_files.get(p) for p in sorted(files)},
            "candidate_files": {p: candidate_files.get(p) for p in sorted(files)},
            "argv": argv, "mapping": group.mapping(), "environment": env,
            "executable_sha256": digest(Path(executable).read_bytes()) if executable else None,
            "environment_proven": bool(executable and Path(executable).resolve() == Path(sys.executable).resolve()),
            "registered_profile": registration[0] if registration else None,
        }
        executable_scope = bool(argv) and (not reasons or fallback_proven_scope)
        entries.append({"group": group.name, "state": "EXECUTE" if executable_scope else "UNPROVEN",
                        "execute": executable_scope, "acs": list(group.acs), "binding": json.loads(canonical(binding)),
                        "explanation": (reasons + ([] if executable_scope else ["NO_APPROVED_BROAD_PROFILE_FOR_REQUIRED_SCOPE"]))
                        or ["EXPLICIT_AC_AND_CHANGED_IMPORT_CONSUMER_CONTRACT_CLOSURE"]})
    return {"kind": "AI_COMMERCE_MT01_PLAN", "version": 1, "required_acs": sorted(set(required_acs) | coverage),
            "fallback": bool(reasons), "aggregate_sources": list(aggregate_sources), "groups": entries}


def apply_evidence(plan: dict, prior: Mapping[str, Evidence], evidence_root: Path) -> None:
    for entry in plan["groups"]:
        if entry["state"] == "UNPROVEN":
            continue
        evidence = prior.get(entry["group"])
        # Even a coincidentally identical aggregate binding needs a fresh run.
        if evidence and not plan["aggregate_sources"] and evidence.valid_for(entry["binding"], evidence_root):
            entry.update(state="REUSE", execute=False, evidence_sha256=evidence.sha256)
            entry["explanation"].append("UNCHANGED_CLOSURE_AND_SUCCESSFUL_IMMUTABLE_ATTEMPT_EVIDENCE")
        elif evidence:
            entry.update(state="INVALIDATED", execute=True)
            entry["explanation"].append("BINDING_OR_ARTIFACT_CHANGED_REEXECUTION_REQUIRED")
