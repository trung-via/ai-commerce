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
import subprocess
import sys
from typing import Iterable, Mapping, Sequence


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pytest_counts(stdout: bytes) -> dict[str, int]:
    """Observed summary counts, never an estimate from selected filenames."""
    counts = {name: 0 for name in ("passed", "failed", "skipped", "errors", "xfailed", "xpassed")}
    for count, name in re.findall(rb"\b([0-9]+) (passed|failed|skipped|errors?|xfailed|xpassed)\b", stdout):
        counts["errors" if name in (b"error", b"errors") else name.decode()] = int(count)
    return counts


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


def reviewed_test_registration(repo: Path, files: Mapping[str, str],
                               groups: Sequence[TestGroup]) -> dict[str, list[str]]:
    """Distinguish uncommitted onboarding tests from committed mapping drift.

    New tests in an EF01 working tree must be executed via MT-01's registered
    FULL fallback, but cannot amend protected approved_groups or the profile
    registry within an unrelated component's allowed_paths. Conversely, a test
    already committed to the source HEAD needs a reviewed mapping: this stops
    incomplete companion registration at PR/main integration time.

    The Git tree is the immutable source-of-truth here; the mutable Git index
    and working tree must never decide which tests are "reviewed".
    """
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
        cwd=repo, capture_output=True, check=False,
    )
    if proc.returncode:
        raise ValueError("MT01_COMMITTED_TREE_UNAVAILABLE")
    try:
        committed = {p.decode("utf-8") for p in proc.stdout.split(b"\0") if p}
    except UnicodeDecodeError as exc:
        raise ValueError("MT01_COMMITTED_TREE_INVALID_ENCODING") from exc
    mapped = {p for group in groups for p in group.tests}
    exception = {"tests/test_ef02_mt01_registry.py"}
    present = {p for p in files if p.startswith("tests/test_") and p.endswith(".py")}
    unregistered = present - mapped - exception
    pending = unregistered - committed
    return {
        "unregistered_committed": sorted(unregistered & committed),
        "pending_new_tests": sorted(pending),
        "unsafe_new_tests": sorted(
            p for p in pending if str(files[p]).startswith("SYMLINK:")
        ),
    }


def approved_groups() -> tuple[TestGroup, ...]:
    """Code-reviewed AC/consumer/contract map, never supplied by an executor."""
    eng = "src/ai_commerce/engineering/"
    rem = "src/ai_commerce/remote/"
    def sources(package: str, names: str) -> tuple[str, ...]:
        # Finite filenames, not a glob: new/unmapped files cannot acquire coverage.
        return tuple(f"src/ai_commerce/{package}/{name}.py" for name in names.split())

    def contracts(names: str) -> tuple[str, ...]:
        return tuple(f"contracts/{name}.schema.json" for name in names.split())

    return (
        TestGroup("a02", ("tests/test_a02_contracts.py", "tests/test_a02_mock_provider.py"),
                  ("A02.contracts-and-provider",),
                  sources("contracts", "__init__ schema") + sources("providers", "__init__ base mock_run"),
                  contracts=tuple(f"src/ai_commerce/contracts/schemas/{name}.v0.1.schema.json"
                                  for name in ("ContentBrief", "EvidenceRef", "JobResult", "MediaArtifact", "SourceSnapshot"))
                  + ("tests/fixtures/creative_only_brief.json", "tests/fixtures/invalid_brief.json")),
        TestGroup("a03", ("tests/test_a03_creative.py", "tests/test_a03_upgrade_v11.py"),
                  ("A03.creative",), sources("creative", "__init__ _validation briefs decisions intent live models reasoner sidecars store v2")),
        TestGroup("a04", ("tests/test_a04_media_operations.py", "tests/test_a04_upgrade_v11.py", "tests/test_a03_a04_integration.py"),
                  ("A04.media-and-operations",),
                  sources("media", "__init__ base fake_generation files generation local provenance")
                  + sources("operations", "__init__ errors generation manifest registry runner")),
        TestGroup("a05", ("tests/test_a05_composition.py", "tests/test_a05_render.py", "tests/test_a05_integration.py",
                          "tests/test_creative_asset_link_v11.py"), ("A05.composition-and-render",),
                  sources("composition", "__init__ _validation authoring compiler models sources store")
                  + sources("bridge", "__init__ link render_plan store")
                  + sources("render", "__init__ manifest models process renderer verify")),
        TestGroup("a06", ("tests/test_a06_e01.py",), ("A06.e01",), sources("e2e", "__init__ service"),
                  consumers=("scripts/a06_demo.py",)),
        TestGroup("a07", ("tests/test_a07_capability.py",), ("A07.capability",),
                  sources("distribution", "__init__ cli evaluator model registry"),
                  contracts=tuple(f"src/ai_commerce/distribution/schemas/{name}.v1.schema.json" for name in (
                      "ChannelCapabilityDecision", "ChannelCapabilityRequest", "ChannelCapabilitySnapshot"))
                  + ("src/ai_commerce/distribution/profiles.json",)),
        TestGroup("a08", ("tests/test_a08_distribution.py",), ("A08.distribution",),
                  sources("distribution", "assisted assisted_cli receipt"),
                  contracts=contracts("PublicationRecord.v0.2") + tuple(
                      f"src/ai_commerce/distribution/schemas/{name}.v1.schema.json" for name in ("DistributionPackage", "PublicationEvent"))),
        TestGroup("a09", ("tests/test_a09_analytics.py", "tests/test_a09_e02.py"), ("A09.analytics",),
                  sources("analytics", "__init__ cli files importer ledger matching model profiles schemas"),
                  consumers=("scripts/a09_demo.py",),
                  contracts=contracts("AnalyticsImport.v1 ComparisonResult.v1 MeasurementPlan.v0.2 MeasurementPlan.v1 MetricDefinition.v1 ObservationSnapshot.v1 PerformanceObservation.v1")),
        TestGroup("a10", ("tests/test_a10_m01.py", "tests/test_a10_m03.py", "tests/test_a10_m04.py", "tests/test_a10_m05.py"),
                  ("A10.opportunity",), sources("opportunity", "__init__")
                  + sources("opportunity/core", "__init__ models schemas")
                  + sources("opportunity/signals", "__init__ files importer ledger profiles")
                  + sources("opportunity/audience", "__init__ __main__ ledger models schemas")
                  + sources("opportunity/proposals", "__init__ planning schemas")
                  + sources("opportunity/bridge", "__init__ briefs cli schemas"),
                  consumers=("scripts/a10_demo.py",),
                  contracts=contracts("SignalImport.v1 SignalSourceSnapshot.v1 TrendSignal.v1 AudienceInsight.v1 OpportunityProposal.v1 OpportunityCreativeBridge.v1")
                  + ("src/ai_commerce/opportunity/proposals/verification.json", "src/ai_commerce/opportunity/bridge/verification.json")),
        TestGroup("a10-m02", ("src/ai_commerce/opportunity/trends/tests/test_m02.py",), ("A10.trends",),
                  sources("opportunity/trends", "__init__ schemas tracking"),
                  contracts=tuple(f"src/ai_commerce/opportunity/trends/{name}.v1.schema.json"
                                  for name in ("TrendComparisonContext", "TrendHypothesis", "TrendSeries"))
                  + ("src/ai_commerce/opportunity/trends/verification.json",)),
        TestGroup("a11", ("tests/test_a11_reader.py", "tests/test_a11_registry.py", "tests/test_a11_reporting.py", "tests/test_a11_integration.py"),
                  ("A11.learning",), sources("learning", "__init__ cli core demo export reader registry reporting schemas"),
                  consumers=("scripts/a11_demo.py",),
                  contracts=contracts("LearningEvidence.v1 LearningHypothesis.v1 LearningRecord.v1 LearningRevision.v1 PerformanceReport.v1")),
        TestGroup("a12", ("tests/test_a12_snapshot.py", "tests/test_a12_identity.py", "tests/test_a12_claims.py", "tests/test_a12_offer.py",
                          "tests/test_a12_rights.py", "tests/test_a12_registry.py", "tests/test_a12_integration.py", "tests/test_a12_cli.py"),
                  ("A12.product-evidence",), sources("product", "__init__ claims cli core demo evidence evidence_schemas files history identity offers pack pack_schemas reader registry rights rights_schemas schemas storage_privacy"),
                  consumers=("scripts/a12_demo.py",),
                  contracts=contracts("ProductListingSnapshot.v1 ProductIdentity.v1 ProductVariant.v1 ProductClaimEvidence.v1 ProductOfferObservation.v1 ProductAssetRights.v1 ProductEvidencePack.v1")),
        # A13 C08/C09/D06/E03: explicit reviewed paths and exact existing test families.
        # Future A13 tests must be registered, not silently excluded from coverage.
        TestGroup("a13", (
            "tests/test_a13_media_inspection.py", "tests/test_a13_claim_rights.py",
            "tests/test_a13_link.py", "tests/test_a13_evidence_pack.py",
        ), ("A13.media-inspection", "A13.claim-rights",
            "A13.affiliate-placement", "A13.affiliate-release"),
                  sources("affiliate_release", "__init__ inspection process schemas claim_rights claim_rights_schemas placement placement_schemas __main__ cli demo evidence_pack evidence_pack_schemas"),
                  contracts=contracts("ClaimRightsReleaseReview.v1 AffiliatePlacementEvidence.v1 AffiliateReleaseEvidencePack.v1")),
        TestGroup("foundation", ("tests/test_foundation.py",), ("A01.foundation",), (),
                  contracts=contracts("WorkItem.v0.3 AuthorizationEnvelope.v0.3") + (
                      "docs/engineering/SEMANTIC_OWNERS.md", "docs/engineering/SELECTIVE_COMPONENT_ADOPTION.md",
                      "MANIFESTO.md", "CONSTITUTION.md", "PROJECT_CONTRACT.md")),
        TestGroup("cli", ("tests/test_cli.py", "tests/test_isolation.py"), ("A01.cli-and-isolation",),
                  ("src/ai_commerce/cli.py", "src/ai_commerce/__init__.py", "src/ai_commerce/__main__.py")
                  + sources("core", "__init__ identity provenance")),
        TestGroup("ef01", ("tests/test_ef01.py",), ("EF01.execution",),
                  (eng + "runner.py", eng + "adapters.py", eng + "profile.py", eng + "cli.py", eng + "__main__.py", eng + "__init__.py"),
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
        TestGroup("ef02-scope", ("tests/test_ef02_candidate_integrity.py", "tests/test_ef02_scope_main_integration.py"),
                  ("EF02.scope-review",),
                  (eng + "source_safety.py", rem + "candidate_policy.py"),
                  consumers=(eng + "runner.py", rem + "worker.py"),
                  contracts=("contracts/RemoteCandidateEvidence.v1.schema.json",)),
        TestGroup("mt01", ("tests/test_minimum_test.py",), ("MT01.selection-and-evidence",),
                  (eng + "minimum_test.py", eng + "verification.py"), (eng + "runner.py", rem + "worker.py")),
        TestGroup("ef01p", ("tests/test_ef01_performance.py",), ("EF01P.git-and-host-budget",),
                  (eng + "git.py", eng + "pytest_profile.py"), (eng + "runner.py", rem + "worker.py")),
        TestGroup("diagnostics", ("tests/test_ef01_agy_diagnostics.py", "tests/test_ef02_authoring_preflight.py"),
                  ("EF01.provider-diagnostics",), (eng + "runner.py", rem + "authoring_preflight.py"),
                  consumers=("scripts/EF02-Authoring-Preflight.py",)),
        TestGroup("git-integrity", ("tests/test_git_byte_consistency.py", "tests/test_ef02_worktree_line_endings.py",
                                    "tests/test_ef01_git_write_guard.py"), ("EF01.git-integrity",),
                  (eng + "git_byte_consistency.py", eng + "git_write_guard.py")),
        TestGroup("ef02-recovery", tuple("tests/" + name + ".py" for name in (
            "test_ef02_m0r_codex_provider_interruption", "test_ef02_m0r_dispatch_only", "test_ef02_m0r_fault_injection",
            "test_ef02_m0r_hosted_checkpoint", "test_ef02_m0r_isolated_runner_kit", "test_ef02_m0r_live_child_probe",
            "test_ef02_m0r_real_e2e_interruption", "test_ef02_m0r_windows_boundary_probe", "test_ef02_m0r_worker_os_kill",
            "test_ef02_github_read_retry")), ("EF02.recovery",),
                  (rem + "worker.py", rem + "transport.py", rem + "github_read_retry.py")),
        TestGroup("ef02-wake", tuple("tests/" + name + ".py" for name in (
            "test_ef02_wake_d1", "test_ef02_wake_d2", "test_ef02_cdp_doorbell", "test_ef02_d3_github_artifact_authority",
            "test_ef02_d3_signed_origin_consent", "test_ef02_d3_two_grant_delivery", "test_ef02_d5_chrome_acquisition",
            "test_ef02_d5_local_handoff", "test_ef02_d5_local_wake_entry", "test_ef02_d5_local_wake_trigger", "test_ef02_d5_run_registration")),
                  ("EF02.wake-boundaries",), sources("remote", "_private_state cdp_doorbell chrome_acquisition completion_event conversation_binding github_artifact_authority local_completion_handoff local_run_registration local_wake_entry local_wake_trigger signed_origin_consent trusted_origin wake_adapter")),
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
        dynamic_names = {"__import__"} | {alias.asname or alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module in ("importlib", "builtins")
            for alias in node.names if alias.name in ("import_module", "__import__")}
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
                isinstance(node.func, ast.Name) and node.func.id in dynamic_names or
                isinstance(node.func, ast.Attribute) and node.func.attr in ("import_module", "__import__")
            ):
                if (node.args and isinstance(node.args[0], ast.Constant) and
                        isinstance(node.args[0].value, str) and not node.args[0].value.startswith(".")):
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
                    binding.get("environment_proven") is not True or "stdout" not in value["artifacts"]):
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
    # Runtime schema/file reads have no Python import edge. Bind the declared
    # contracts to their producers so reverse consumers inherit those changes.
    for group in groups:
        for path in group.paths:
            graph.setdefault(path, set()).update(group.contracts)
    global_files = {p for p in set(base_files) | set(candidate_files) if (
        Path(p).name in ("conftest.py", "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "setup.py") or
        Path(p).name.startswith(("requirements", "constraints")) or p.endswith((".lock", ".pth")))}
    closures = {g.name: _closure((*g.paths, *g.tests, *g.consumers, *g.contracts), graph) | global_files
                for g in groups}
    selected = [g for g in groups if set(changed_paths) & closures[g.name]
                or set(required_acs) & set(g.acs)]
    coverage = set().union(*(set(g.acs) for g in selected)) if selected else set()
    known = set().union(*(closures[g.name] for g in selected)) if selected else set()
    affected = _closure(changed_paths, {p: {c for c, deps in graph.items() if p in deps} for p in graph})
    covered_tests = {t for g in selected for t in g.tests}
    affected_tests = {p for p in affected if Path(p).name.startswith("test_") or Path(p).name.endswith("_test.py")}
    unknown_tests = affected_tests - covered_tests
    unknown_consumers = {p for p in affected if p.endswith(".py") and p not in affected_tests} - known
    unsupported_paths = {p for p in changed_paths if Path(p).suffix in (
        ".js", ".cjs", ".mjs", ".ts", ".tsx", ".ps1", ".sh") and p not in known}
    registered = {}
    # Registered MT-01 groups must preserve the *selected action profile*.
    # In particular, an explicit project-tests-serial action must not silently
    # execute a registered "-n 12" group just because its test paths match.
    parallel_requested = list(fallback_argv[-2:]) == ["-n", "12"]
    for group in selected:
        serial_args = list(group.tests) + ["-q"]
        parallel_args = serial_args + ["-n", "12"]
        eligible_args = ((parallel_args,) if parallel_requested else (serial_args,)) if registered_only else (
            serial_args, parallel_args)
        for name, argv in sorted((registered_profiles or {}).items()):
            if (list(argv[:3]) in (["python", "-m", "pytest"], ["py", "-m", "pytest"]) and
                    list(argv[3:]) in eligible_args):
                registered[group.name] = (name, list(argv))
                break
    reasons = []
    if set(changed_paths) & global_files:
        reasons.append("SHARED_TEST_CONFIGURATION_REQUIRES_FULL_SUITE")
    if registered_only and any(g.name not in registered for g in selected):
        reasons.append("REGISTERED_ARGV_ONLY_COMPANION_REGISTRATION_REQUIRED")
    if aggregate_sources:
        reasons.append("AGGREGATE_REQUIRES_FRESH_COMBINED_CONSUMER_COVERAGE")
    if not imports_complete or unknown_tests or unknown_consumers:
        reasons.append("IMPORT_OR_CONSUMER_MAP_INCOMPLETE")
    if unsupported_paths:
        reasons.append("NON_PYTHON_VERIFICATION_SCOPE_UNPROVEN")
    if set(changed_paths) - known or set(required_acs) - coverage or not selected:
        reasons.append("PATH_OR_AC_MAP_INCOMPLETE")
    if any(not all(p in candidate_files for p in g.tests) for g in selected):
        reasons.append("TEST_DEFINITION_MISSING")
    candidate_groups = list(selected)
    fallback_profile = next((name for name, argv in sorted((registered_profiles or {}).items())
                             if list(argv) == list(fallback_argv)), None)
    fallback_registered = not registered_only or registered_profiles is None or fallback_profile is not None
    env = environment() if env is None else env
    entries = []
    scope_args = list(fallback_argv[3:])
    if registered_only and scope_args[-2:] == ["-n", "12"]:
        scope_args = scope_args[:-2]
    fallback_proven_scope = list(fallback_argv[1:3]) == ["-m", "pytest"] and scope_args in (
        ["tests", "-q"], ["tests"], [".", "-q"], ["."])
    # A tests/ registry command does not collect component-local tests under src/.
    required_tests = covered_tests | affected_tests
    fallback_proven_scope = (fallback_proven_scope and not unsupported_paths and
        all(t in candidate_files for t in required_tests) and
        (scope_args[0] == "." or all(t.startswith("tests/") for t in required_tests)))
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
            "registered_profile": registration[0] if registration else fallback_profile if reasons else None,
        }
        executable_scope = bool(argv) and (not reasons or fallback_proven_scope and fallback_registered)
        entries.append({"group": group.name, "state": "EXECUTE" if executable_scope else "UNPROVEN",
                        "execute": executable_scope, "acs": list(group.acs), "binding": json.loads(canonical(binding)),
                        "selected_profile": registration[0] if registration else fallback_profile if reasons else None,
                        "test_files_count": len(group.tests) if not reasons else None,
                        "explanation": (reasons + ([] if executable_scope else ["NO_APPROVED_BROAD_PROFILE_FOR_REQUIRED_SCOPE"]))
                        or ["EXPLICIT_AC_AND_CHANGED_IMPORT_CONSUMER_CONTRACT_CLOSURE"]})
    return {"kind": "AI_COMMERCE_MT01_PLAN", "version": 1, "required_acs": sorted(set(required_acs) | coverage),
            "changed_paths": sorted(set(changed_paths)), "affected_paths": sorted(affected),
            "unknown_acs": sorted(set(required_acs) - coverage), "unknown_tests": sorted(unknown_tests),
            "unknown_consumers": sorted(unknown_consumers), "reason_codes": reasons,
            "unsupported_paths": sorted(unsupported_paths),
            "consumer_map": {g.name: {**g.mapping(), "closure": sorted(closures[g.name])} for g in candidate_groups},
            "selected_groups": [g.name for g in candidate_groups],
            "selected_profiles": [e["selected_profile"] for e in entries if e["selected_profile"]],
            "fallback_profile": fallback_profile, "fallback_argv": list(fallback_argv),
            "narrow_execution_observed": False,
            "fallback": bool(reasons), "aggregate_sources": list(aggregate_sources), "groups": entries}


def apply_evidence(plan: dict, prior: Mapping[str, Evidence], evidence_root: Path) -> None:
    for entry in plan["groups"]:
        if entry["state"] == "UNPROVEN":
            continue
        evidence = prior.get(entry["group"])
        # Even a coincidentally identical aggregate binding needs a fresh run.
        if evidence and not plan["aggregate_sources"] and evidence.valid_for(entry["binding"], evidence_root):
            entry.update(state="REUSE", execute=False, evidence_sha256=evidence.sha256)
            refs = json.loads(evidence.payload)["artifacts"]
            entry["reused_artifacts"] = refs
            entry["tests_proven"] = pytest_counts((evidence_root / refs["stdout"]["path"]).read_bytes())
            entry["explanation"].append("UNCHANGED_CLOSURE_AND_SUCCESSFUL_IMMUTABLE_ATTEMPT_EVIDENCE")
        elif evidence:
            entry.update(state="INVALIDATED", execute=True)
            entry["explanation"].append("BINDING_OR_ARTIFACT_CHANGED_REEXECUTION_REQUIRED")
