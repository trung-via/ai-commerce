"""Execute MT-01 groups inside EF01's existing action budget; no provider calls."""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import os
import re
import subprocess
import tempfile
import time
from typing import Callable, Mapping
import xml.etree.ElementTree as ET

from .minimum_test import Evidence, apply_evidence, canonical, file_hashes, select_plan, pytest_counts, select_provisional_tests
from .pytest_profile import require_managed_parallelism, registered_workers


WORKLOAD_SCHEMA_VERSION = 1
_MAX_WORKLOAD_TESTS = 100_000
_MAX_WORKLOAD_ID_BYTES = 8 * 1024 * 1024
# Input/reporting limits only. execute_plan never consumes these constants.
_MAX_WORKLOAD_SECONDS = 7 * 24 * 60 * 60
_MAX_TEST_SECONDS = 24 * 60 * 60
# Reporting assumptions, not measured priors, prediction intervals or timeouts.
_COLD_TEST_SECONDS = (60.0, 600.0)
_COLD_STARTUP_SECONDS = 60.0
_COLD_OVERHEAD_SECONDS = 60.0


def _workload_hash(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _workload_path(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        raise ValueError("UNSUPPORTED_COLLECTION_SCOPE")
    path = value.replace("\\", "/")
    if (path.startswith("/") or ":" in path or ".." in path.split("/")
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise ValueError("UNSUPPORTED_COLLECTION_SCOPE")
    parts = [p for p in path.split("/") if p and p != "."]
    if not parts or not (parts[0] == "tests" or (parts[0] == "src" and "tests" in parts)):
        raise ValueError("UNSUPPORTED_COLLECTION_SCOPE")
    return "/".join(parts)


def _workload_nodeid(value: object) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("INVALID_NODEID")
    path, sep, case = value.partition("::")
    path = _workload_path(path)
    if not sep or not case or not path.endswith(".py"):
        raise ValueError("INVALID_NODEID")
    # Normalize only the path: parameter IDs (including backslashes) are identity.
    return path + sep + case


def workload_nodeids_digest(nodeids: object) -> str:
    """Hash bounded unique pytest identities; never collect or import test modules."""
    return _workload_hash(_workload_nodeids(nodeids))


def _workload_nodeids(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= _MAX_WORKLOAD_TESTS:
        raise ValueError("INVALID_NODEID_COUNT")
    result = []
    size = 0
    for value in values:
        nodeid = _workload_nodeid(value)
        size += len(nodeid.encode("utf-8"))
        if size > _MAX_WORKLOAD_ID_BYTES:
            raise ValueError("NODEIDS_OVERSIZED")
        result.append(nodeid)
    if len(set(result)) != len(result):
        raise ValueError("DUPLICATE_NODEID")
    return sorted(result)


def _workload_binding(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("BINDING_MISSING")
    result = {}
    for key, pattern in (("candidate_sha", r"[0-9a-f]{40}"),
                         ("source_sha256", r"[0-9a-f]{64}"),
                         ("environment_sha256", r"[0-9a-f]{64}"),
                         ("argv_sha256", r"[0-9a-f]{64}"),
                         ("registered_profile", r"[a-z0-9][a-z0-9_.-]{0,79}")):
        item = value.get(key)
        if not isinstance(item, str) or re.fullmatch(pattern, item) is None:
            raise ValueError("BINDING_IDENTITY_UNPROVEN")
        result[key] = item
    if value.get("platform") != "Windows":
        raise ValueError("ENVIRONMENT_UNPROVEN")
    workers = value.get("workers_requested")
    scheduler = value.get("scheduler")
    if (type(workers) is not int or workers not in (0, 12)
            or scheduler not in ("serial", "load", "loadfile", "worksteal")
            or (workers == 0) != (scheduler == "serial")):
        raise ValueError("PROFILE_UNPROVEN")
    scope = value.get("collection_scope")
    if not isinstance(scope, (list, tuple)) or not 1 <= len(scope) <= 256:
        raise ValueError("UNSUPPORTED_COLLECTION_SCOPE")
    roots = sorted({_workload_path(p) for p in scope})
    if len(roots) != len(scope):
        raise ValueError("UNSUPPORTED_COLLECTION_SCOPE")
    result.update(platform="Windows", workers_requested=workers, scheduler=scheduler,
                  collection_scope=roots)
    return result


def _workload_record(record: object, binding: dict) -> dict:
    if not isinstance(record, dict):
        raise ValueError("OBSERVATION_MISSING")
    if type(record.get("schema_version")) is not int or record["schema_version"] != WORKLOAD_SCHEMA_VERSION:
        raise ValueError("OBSERVATION_SCHEMA_UNSUPPORTED")
    if record.get("provenance") not in ("SYNTHETIC", "TRUSTED_OBSERVATION"):
        raise ValueError("OBSERVATION_PROVENANCE_MISSING")
    evidence = record.get("evidence_sha256")
    if not isinstance(evidence, str) or re.fullmatch(r"[0-9a-f]{64}", evidence) is None:
        raise ValueError("OBSERVATION_EVIDENCE_MISSING")
    if _workload_binding(record.get("binding")) != binding:
        raise ValueError("OBSERVATION_IDENTITY_MISMATCH")
    return record


def _workload_seconds(value: object, *, positive: bool = False, maximum: float = _MAX_WORKLOAD_SECONDS) -> float:
    # Compare before conversion: huge integers, NaN, bool and Inf fail closed.
    if (type(value) not in (int, float) or not 0 <= value <= maximum
            or (positive and value < 1e-9)):
        raise ValueError("INVALID_DURATION")
    return float(value)


def estimate_workload(*, binding: object, selected_test_count: object,
                      collection: object = None, history: object = None) -> dict:
    """ATB-01 P0 data-only estimate; never authorizes collection, execution or PASS.

    The caller must authenticate immutable observations *before* passing data;
    provenance labels/digests are not an admission gate. Binding fingerprints
    cover exact candidate/test/fixture definitions, Python/deps/plugins, reviewed
    argv, scheduler and owner scope. No shell strings, files or environment are
    read here. History durations include setup/call/teardown; fixture_seconds is
    additional shared cost excluded from those durations. age_seconds is trusted
    receipt age supplied by the caller, keeping this function deterministic.
    """
    receipt = dict(schema_version=WORKLOAD_SCHEMA_VERSION, model_version="ATB01-P0-v1",
                   status="SHADOW_ONLY", state="UNPROVEN", execution_status="NOT_EXECUTED",
                   confidence="UNCALIBRATED", acceptance_pass=False,
                   selected_test_count=None, input_nodeid_count=None, tests_collected=None,
                   tests_executed=None, nodeids_sha256=None, nodeid_hashes=[],
                   historical_coverage_count=0, coverage_fraction=0.0,
                   effective_workers=1.0, workers_observed=None, throughput_tests_per_second=None,
                   estimated_seconds=None, estimate_envelope_seconds=None,
                   soft_deadline_seconds=None, hard_deadline_seconds=None,
                   uncertainty_kind="ASSUMPTION_ENVELOPE_NOT_PREDICTION_INTERVAL",
                   reason_codes=[])
    reasons = receipt["reason_codes"]
    try:
        identity = _workload_binding(binding)
        if type(selected_test_count) is not int or not 1 <= selected_test_count <= _MAX_WORKLOAD_TESTS:
            raise ValueError("SELECTED_TEST_COUNT_UNPROVEN")
        receipt.update(selected_test_count=selected_test_count, candidate_sha=identity["candidate_sha"],
                       binding_sha256=_workload_hash(identity), workers_requested=identity["workers_requested"])
        observed = _workload_record(collection, identity)
        nodeids = _workload_nodeids(observed.get("nodeids"))
        if (type(observed.get("test_count")) is not int or observed["test_count"] != len(nodeids)
                or selected_test_count != len(nodeids)):
            raise ValueError("COLLECTION_COUNT_MISMATCH")
        node_digest = _workload_hash(nodeids)
        if observed.get("nodeids_sha256") != node_digest:
            raise ValueError("COLLECTION_DIGEST_MISMATCH")
        paths = {n.partition("::")[0] for n in nodeids}
        def within(path, root):
            return path == root or path.startswith(root + "/")
        roots = identity["collection_scope"]
        if (any(not any(within(p, root) for root in roots) for p in paths)
                or any(not any(within(p, root) for p in paths) for root in roots)):
            raise ValueError("COLLECTION_SCOPE_INCOMPLETE")
    except UnicodeError:
        reasons.append("INVALID_NODEID_ENCODING")
        return receipt
    except ValueError as exc:
        reasons.append(str(exc))
        return receipt
    receipt.update(input_nodeid_count=len(nodeids), nodeids_sha256=node_digest,
                   nodeid_hashes=[hashlib.sha256(n.encode()).hexdigest() for n in nodeids],
                   collection_provenance=observed["provenance"],
                   collection_evidence_sha256=observed["evidence_sha256"],
                   tests_collected=len(nodeids) if observed["provenance"] == "TRUSTED_OBSERVATION" else None)
    durations = {}
    startup, overhead, fixture, serial = _COLD_STARTUP_SECONDS, _COLD_OVERHEAD_SECONDS, 0.0, 0.0
    effective, wall, workers, throughput = 1.0, None, None, None
    try:
        if history is None:
            raise ValueError("NO_HISTORY")
        hist = _workload_record(history, identity)
        if hist.get("duration_semantics") != "setup_call_teardown":
            raise ValueError("HISTORY_DURATION_SEMANTICS_UNPROVEN")
        _workload_seconds(hist.get("age_seconds"), maximum=30 * 24 * 60 * 60)
        records = hist.get("durations")
        if not isinstance(records, (list, tuple)) or not 1 <= len(records) <= len(nodeids):
            raise ValueError("HISTORY_COUNT_INVALID")
        ids = _workload_nodeids([r.get("nodeid") if isinstance(r, dict) else None for r in records])
        if (type(hist.get("test_count")) is not int or hist["test_count"] != len(ids)
                or not set(ids) <= set(nodeids)):
            raise ValueError("HISTORY_IDENTITIES_MISMATCH")
        durations = {_workload_nodeid(r["nodeid"]): _workload_seconds(
            r.get("seconds"), positive=True, maximum=_MAX_TEST_SECONDS) for r in records}
        metadata = ("startup_seconds", "overhead_seconds", "fixture_seconds", "serial_seconds")
        startup, overhead, fixture, serial = (
            _workload_seconds(hist.get(k, default)) for k, default in zip(
                metadata, (_COLD_STARTUP_SECONDS, _COLD_OVERHEAD_SECONDS, 0.0, 0.0)))
        if not all(k in hist for k in metadata):
            reasons.append("HISTORY_METADATA_INCOMPLETE")
        workers = hist.get("workers_observed")
        if workers is not None and (type(workers) is not int or workers != identity["workers_requested"]):
            raise ValueError("HISTORY_WORKERS_MISMATCH")
        if hist.get("wall_seconds") is not None:
            wall = _workload_seconds(hist["wall_seconds"], positive=True)
            compute = wall - startup - overhead - fixture
            if compute <= 0 or serial > compute:
                raise ValueError("HISTORY_WALL_INCONSISTENT")
            if workers is not None and all(k in hist for k in metadata):
                measured = math.fsum(durations.values()) / compute
                if measured > max(1, workers) + 1e-6:
                    raise ValueError("HISTORY_WALL_INCONSISTENT")
                effective = min(float(max(1, workers)), measured)
                throughput = len(durations) / compute
        if workers is None or wall is None or not all(k in hist for k in metadata):
            reasons.append("EFFECTIVE_THROUGHPUT_UNPROVEN_SERIAL_ASSUMED")
        receipt.update(history_provenance=hist["provenance"], history_evidence_sha256=hist["evidence_sha256"])
    except ValueError as exc:
        reasons.append("INVALID_NODEID_ENCODING" if isinstance(exc, UnicodeError) else str(exc))
        durations = {}
        startup, overhead, fixture, serial = _COLD_STARTUP_SECONDS, _COLD_OVERHEAD_SECONDS, 0.0, 0.0
        effective, wall, workers, throughput = 1.0, None, None, None
    known = len(durations)
    unknown = len(nodeids) - known
    cold_low = max(_COLD_TEST_SECONDS[0], max(durations.values(), default=0.0))
    cold_high = max(_COLD_TEST_SECONDS[1], cold_low)
    effort = math.fsum(durations.get(n, cold_low) for n in nodeids)
    files = {}
    for n in nodeids:
        path = n.partition("::")[0]
        files[path] = files.get(path, 0.0) + durations.get(n, cold_low)
    tail = max(files.values())  # Conservative file tail even for load/worksteal.
    wall_scaled = ((wall - startup - overhead - fixture) * len(nodeids) / known
                   if wall is not None and known else 0.0)
    estimate = startup + fixture + max(effort / effective, tail, serial, wall_scaled) + overhead
    # Upper scenario assumes serial work, cold upper cost, plus known shared cost.
    upper = max(estimate, startup + overhead + fixture + serial
                + math.fsum(durations.values()) + unknown * cold_high)
    receipt.update(historical_coverage_count=known, coverage_fraction=known / len(nodeids),
                   effective_workers=effective, workers_observed=workers,
                   throughput_tests_per_second=throughput,
                   confidence="PARTIAL" if known else "UNCALIBRATED",
                   components=dict(test_effort_seconds=effort, slow_file_seconds=tail,
                                   startup_seconds=startup, fixture_seconds=fixture,
                                   overhead_seconds=overhead, serial_seconds=serial,
                                   historical_wall_scaled_seconds=wall_scaled),
                   cold_test_assumptions_seconds=[cold_low, cold_high] if unknown else None)
    if unknown:
        reasons.append("COLD_TESTS_UNCALIBRATED")
    reasons.append("PREDICTION_INTERVAL_UNCALIBRATED")
    if not math.isfinite(upper) or upper > _MAX_WORKLOAD_SECONDS:
        reasons.append("ESTIMATE_OUTSIDE_REPORTING_BOUND")
    else:
        receipt.update(estimated_seconds=estimate, estimate_envelope_seconds=[estimate, upper])
    return receipt


_OBSERVER_PLUGIN = "ai_commerce.engineering.pytest_profile"
_JUNIT_PLUGIN_NAME = "_ai_commerce_mt01_junit_plugin"
_JUNIT_ENV_VAR = "AI_COMMERCE_MT01_JUNIT_PATH"
_MAX_JUNIT_BYTES = 8 * 1024 * 1024
_NATIVE_SYMLINK_SKIP_REASON = "NATIVE_SYMLINK_CREATION_UNPROVEN: WinError 1314"
_INFRASTRUCTURE_MARKERS = (
    "permission denied", "permissionerror", "no module named pytest", "internalerror",
    "worker crashed", "workers crashed", "winerror", "subprocess.timeoutexpired",
    "memoryerror", "cannot allocate memory", "can't start new thread",
    "failed to start worker", "could not start worker", "error while receiving from worker",
)
_SKIPPED_LINE_RE = re.compile(rb"SKIPPED \[([1-9][0-9]*)\] ([^:\r\n]+):([1-9][0-9]*): (.+)")
_JUNIT_PLUGIN_SOURCE = (
    "import os\n"
    "import pytest\n\n"
    "@pytest.hookimpl(tryfirst=True)\n"
    "def pytest_configure(config):\n"
    "    if hasattr(config, 'workerinput'):\n"
    "        return\n"
    "    target = os.environ.get('AI_COMMERCE_MT01_JUNIT_PATH')\n"
    "    if target and not getattr(config.option, 'xmlpath', None):\n"
    "        config.option.xmlpath = target\n"
)


def pytest_observation_env() -> dict[str, str]:
    """Instrument the pytest controller without modifying registered argv."""
    env = os.environ.copy()
    env.pop(_JUNIT_ENV_VAR, None)
    plugins = [
        item.strip() for item in env.get("PYTEST_PLUGINS", "").split(",")
        if item.strip() and item.strip() != _JUNIT_PLUGIN_NAME
    ]
    if _OBSERVER_PLUGIN not in plugins:
        plugins.append(_OBSERVER_PLUGIN)
    env["PYTEST_PLUGINS"] = ",".join(plugins)
    return env


def observed_xdist_workers(out: bytes) -> int | None:
    """Accept exactly one complete controller hook receipt, never console guesses."""
    matches = re.findall(rb"(?m)^MT01_XDIST_WORKERS_COLLECTED=([0-9]+)\r?$", out)
    return int(matches[0]) if len(matches) == 1 else None


def observed_console_workers(out: bytes) -> int | None:
    """Diagnostic only; startup/progress text cannot establish workers_actual."""
    match = re.search(rb"\b([0-9]+) workers \[|created:\s*([0-9]+)/[0-9]+ workers", out)
    return int(match[1] or match[2]) if match else None


@contextmanager
def _junit_capture_env(repo: Path):
    """Capture JUnit XML in a private scratch dir outside repo without mutating argv."""
    root = repo.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if temp_root == root or root in temp_root.parents:
        raise ValueError("PYTEST_JUNIT_SCRATCH_INSIDE_WORKSPACE")
    with tempfile.TemporaryDirectory(
        prefix="mt01j_", dir=temp_root, ignore_cleanup_errors=True
    ) as scratch_dir:
        scratch = Path(scratch_dir)
        (scratch / f"{_JUNIT_PLUGIN_NAME}.py").write_text(_JUNIT_PLUGIN_SOURCE, encoding="utf-8")
        junit_path = scratch / "junit.xml"
        env = pytest_observation_env()
        plugins = [item.strip() for item in env.get("PYTEST_PLUGINS", "").split(",") if item.strip()]
        if _JUNIT_PLUGIN_NAME not in plugins:
            plugins.append(_JUNIT_PLUGIN_NAME)
        env["PYTEST_PLUGINS"] = ",".join(plugins)
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{scratch}{os.pathsep}{existing_pp}" if existing_pp else str(scratch)
        env[_JUNIT_ENV_VAR] = str(junit_path)
        yield env, junit_path, scratch


def _read_captured_junit(junit_path: Path, scratch: Path) -> tuple[bytes | None, str | None]:
    """Read captured JUnit XML strictly within the private scratch directory and size cap."""
    if junit_path.is_symlink():
        return None, "JUNIT_SYMLINK_UNSAFE"
    if not junit_path.exists():
        return None, None
    if not junit_path.is_file():
        return None, "JUNIT_NOT_REGULAR_FILE"
    if junit_path.resolve().parent != scratch.resolve():
        return None, "JUNIT_PATH_ESCAPE"
    if junit_path.stat().st_size > _MAX_JUNIT_BYTES:
        return None, "JUNIT_OVERSIZED"
    return junit_path.read_bytes(), None


def _run_pytest_with_junit(argv: list[str], *, repo: Path, timeout: float):
    """Execute pytest via verification.subprocess.run with bounded JUnit capture."""
    with _junit_capture_env(repo) as (env, junit_path, scratch):
        result = subprocess.run(
            argv, cwd=repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            check=False, env=env,
        )
        out = result.stdout.encode() if isinstance(result.stdout, str) else (result.stdout or b"")
        err = result.stderr.encode() if isinstance(result.stderr, str) else (result.stderr or b"")
        junit_bytes, junit_error = _read_captured_junit(junit_path, scratch)
        return result, out, err, junit_bytes, junit_error


def _parse_safe_junit(junit: bytes | None) -> ET.Element | None:
    """Parse bounded JUnit XML while rejecting DOCTYPE/ENTITY and oversized payloads."""
    if not junit or not isinstance(junit, (bytes, bytearray)) or len(junit) > _MAX_JUNIT_BYTES:
        return None
    upper = junit.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        return None
    try:
        return ET.fromstring(junit)
    except (ET.ParseError, ValueError, TypeError):
        return None


def _is_skip_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in ("skipTest", "skip", "skipIf", "skipUnless")
    if isinstance(func, ast.Name):
        return func.id in ("skipTest", "skip", "skipIf", "skipUnless")
    return False


def _is_unittest_skip_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skipTest"
    )


def _ast_matches_skip_case(
    file_path: Path,
    *,
    module_variants: tuple[str, ...],
    classname: str,
    case_name: str,
    lineno: int,
) -> bool:
    """Verify JUnit (classname, name) matches an actual skip call at lineno in file_path."""
    matched_mod = next(
        (m for m in module_variants if classname == m or classname.startswith(m + ".")),
        None,
    )
    if matched_mod is None:
        return False
    class_parts = [] if classname == matched_mod else classname[len(matched_mod) + 1:].split(".")
    if any(not part or not part.isidentifier() for part in class_parts):
        return False
    func_name = case_name.split("[", 1)[0]
    if not func_name or not func_name.isidentifier():
        return False
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError, UnicodeError):
        return False
    scope_body = tree.body
    for cls_name in class_parts:
        cls_node = next(
            (n for n in scope_body if isinstance(n, ast.ClassDef) and n.name == cls_name),
            None,
        )
        if cls_node is None:
            return False
        scope_body = cls_node.body
    func_node = next(
        (
            n for n in scope_body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name
        ),
        None,
    )
    if func_node is None or func_node.end_lineno is None:
        return False
    start_line = min([func_node.lineno, *(d.lineno for d in func_node.decorator_list)])
    if not (start_line <= lineno <= func_node.end_lineno):
        return False
    for sub in ast.walk(func_node):
        if _is_skip_call(sub):
            end = getattr(sub, "end_lineno", sub.lineno)
            if sub.lineno <= lineno <= end:
                return True
            # unittest.TestCase.skipTest reports the test method's def/decorator
            # line in pytest stdout and JUnit XML rather than the call line.
            if class_parts and lineno in (start_line, func_node.lineno) and _is_unittest_skip_call(sub):
                return True
    return False


def _strip_verified_symlink_skip(
    stdout: bytes,
    stderr: bytes,
    exit_code: int,
    junit: bytes | None,
    *,
    repo: Path | None = None,
    candidate_files: Mapping[str, str] | None = None,
) -> bytes:
    """Exclude JUnit- and AST-verified WinError 1314 SKIP lines from infra classification only.

    Original stdout, stderr, JUnit XML, and skipped counts are preserved intact.
    Any unverified WinError, JUnit mismatch, ambiguous skip summary, or invalid
    XML leaves stdout unchanged so infrastructure classification fails closed.
    """
    if b"winerror" not in stdout.lower():
        return stdout
    if exit_code != 0 or b"winerror" in stderr.lower() or repo is None:
        return stdout
    root = _parse_safe_junit(junit)
    if root is None:
        return stdout
    suites = (
        [root] if root.tag == "testsuite"
        else list(root) if root.tag == "testsuites"
        else []
    )
    if len(suites) != 1 or suites[0].tag != "testsuite":
        return stdout
    suite = suites[0]
    cases = list(suite.findall("testcase"))
    if not cases:
        return stdout
    try:
        suite_tests = int(suite.get("tests", "-1"))
        suite_errors = int(suite.get("errors", "-1"))
        suite_failures = int(suite.get("failures", "-1"))
        suite_skipped = int(suite.get("skipped", "-1"))
    except (ValueError, TypeError):
        return stdout
    if (suite_tests != len(cases) or suite_errors != 0
            or suite_failures != 0 or suite_skipped < 1):
        return stdout
    if any(c.find("failure") is not None or c.find("error") is not None for c in cases):
        return stdout
    skipped_cases: list[tuple[ET.Element, ET.Element]] = []
    for case in cases:
        skips = case.findall("skipped")
        if len(skips) > 1:
            return stdout
        if len(skips) == 1:
            skipped_cases.append((case, skips[0]))
    if len(skipped_cases) != suite_skipped:
        return stdout

    skip_xml = 0
    xfail_xml = 0
    for _, skip in skipped_cases:
        stype = skip.get("type")
        if stype == "pytest.skip":
            skip_xml += 1
        elif stype == "pytest.xfail":
            xfail_xml += 1
        else:
            return stdout
    if skip_xml < 1:
        return stdout

    counts = pytest_counts(stdout)
    if (counts["passed"] < 1
            or counts["failed"] != 0
            or counts["errors"] != 0
            or counts["skipped"] != skip_xml
            or counts["xfailed"] != xfail_xml
            or (counts["passed"] + counts["skipped"] + counts["xfailed"] + counts["xpassed"]) != len(cases)):
        return stdout

    lines = stdout.splitlines(keepends=True)
    skip_indices = {i for i, line in enumerate(lines) if line.rstrip(b"\r\n").startswith(b"SKIPPED [")}
    marked_indices = [i for i, line in enumerate(lines) if b"winerror" in line.lower()]
    if not marked_indices or any(i not in skip_indices for i in marked_indices):
        return stdout

    parsed_skips: list[tuple[int, int, str, int, str]] = []
    seen_locations: set[tuple[str, int, str]] = set()
    total_stdout_skips = 0
    for idx in sorted(skip_indices):
        match = _SKIPPED_LINE_RE.fullmatch(lines[idx].rstrip(b"\r\n"))
        if match is None:
            return stdout
        try:
            count = int(match.group(1))
            raw_path = match.group(2).decode("utf-8", "strict")
            lineno = int(match.group(3))
            reason = match.group(4).decode("utf-8", "strict").strip()
        except (UnicodeDecodeError, ValueError):
            return stdout
        norm_path = raw_path.replace("\\", "/")
        if not norm_path or norm_path.startswith("/") or ".." in Path(norm_path).parts:
            return stdout
        loc_key = (norm_path, lineno, reason)
        if loc_key in seen_locations:
            return stdout
        seen_locations.add(loc_key)
        total_stdout_skips += count
        parsed_skips.append((idx, count, norm_path, lineno, reason))
    if total_stdout_skips != skip_xml:
        return stdout

    repo_root = repo.resolve()
    claimed_junit: set[int] = set()
    marked_set = set(marked_indices)
    for idx, count, norm_path, lineno, reason in parsed_skips:
        if idx not in marked_set:
            continue
        if reason != _NATIVE_SYMLINK_SKIP_REASON or not norm_path.endswith(".py"):
            return stdout
        if candidate_files is not None:
            digest_or_link = candidate_files.get(norm_path)
            if not digest_or_link or str(digest_or_link).startswith("SYMLINK:"):
                return stdout
        file_path = repo / norm_path
        if file_path.is_symlink() or not file_path.is_file():
            return stdout
        if repo_root not in file_path.resolve().parents:
            return stdout
        mod = norm_path.removesuffix(".py").replace("/", ".")
        module_variants = (mod, mod.removeprefix("src.")) if mod.startswith("src.") else (mod,)
        matched_for_line: list[int] = []
        for j_idx, (case, skip) in enumerate(skipped_cases):
            if j_idx in claimed_junit or skip.get("type") != "pytest.skip":
                continue
            if (skip.get("message") or "").strip() != _NATIVE_SYMLINK_SKIP_REASON:
                continue
            if skip.text:
                match_text = re.fullmatch(r"(.+):([1-9][0-9]*): (.+)", skip.text.strip())
                if match_text is None:
                    continue
                raw_file, raw_line, raw_reason = match_text.groups()
                same_file = raw_file.replace("\\", "/") == norm_path
                if not same_file and Path(raw_file).is_absolute():
                    try:
                        same_file = Path(raw_file).resolve() == file_path.resolve()
                    except OSError:
                        same_file = False
                if not same_file or raw_line != str(lineno) or raw_reason.strip() != reason:
                    continue
            if _ast_matches_skip_case(
                file_path,
                module_variants=module_variants,
                classname=case.get("classname") or "",
                case_name=case.get("name") or "",
                lineno=lineno,
            ):
                matched_for_line.append(j_idx)
        if len(matched_for_line) != count:
            return stdout
        claimed_junit.update(matched_for_line)

    for j_idx, (_, skip) in enumerate(skipped_cases):
        if j_idx in claimed_junit:
            continue
        if ("winerror" in (skip.get("message") or "").lower()
                or "winerror" in (skip.text or "").lower()):
            return stdout

    return b"".join(line for i, line in enumerate(lines) if i not in marked_set)


def _diagnose_junit_and_infra(
    out: bytes,
    err: bytes,
    returncode: int,
    junit_bytes: bytes | None,
    junit_error: str | None,
    *,
    repo: Path,
    candidate_files: Mapping[str, str],
) -> tuple[str, str | None]:
    """Evaluate verified WinError symlink skips alongside bounded JUnit integrity."""
    diag_out = _strip_verified_symlink_skip(
        out, err, returncode, junit_bytes,
        repo=repo, candidate_files=candidate_files,
    )
    diagnostics = (diag_out + b"\n" + err).decode("utf-8", "replace").lower()
    junit_issue = junit_error
    if junit_issue is None and junit_bytes is not None and _parse_safe_junit(junit_bytes) is None:
        junit_issue = "JUNIT_XML_MALFORMED_OR_UNSAFE"
    elif junit_issue is None and junit_bytes is None and _NATIVE_SYMLINK_SKIP_REASON.lower() in diagnostics:
        junit_issue = "JUNIT_XML_ABSENT"
    return diagnostics, junit_issue


def registered_binding_problems(plan: dict, profiles: Mapping[str, list[str]]) -> list[str]:
    """Early, bounded form of EF02's independent registered-binding check.

    Do not run expensive pytest groups with an ambiguous profile that the
    trusted publisher must reject. This is a *negative* preflight, not a
    replacement for post-execution hash/manifest/evidence validation.
    """
    problems = []
    for index, entry in enumerate(plan.get("groups", ())):
        binding = entry.get("binding", {})
        name = binding.get("registered_profile")
        if name == "mt01-component-onboarding":
            if (binding.get("onboarding_data_only") is not True
                    or not binding.get("onboarding_manifest_path")
                    or not binding.get("onboarding_manifest_sha256")):
                problems.append(f"MT01_GROUP_{index}_ONBOARDING_PROOF_MISSING")
        elif (not isinstance(name, str)
                or profiles.get(name) != binding.get("argv")):
            problems.append(f"MT01_GROUP_{index}_REGISTERED_BINDING_REJECTED")
    return problems


def execute_plan(*, repo: Path, output_dir: Path, base_sha: str, base_files: dict,
                 changed_paths: list[str], required_acs: tuple[str, ...], fallback_argv: list[str],
                 prior: dict[str, Evidence], save: Callable, evidence_files: Mapping[str, str],
                 deadline: float, timeout_seconds: float, registered_only: bool = False,
                 aggregate_sources: tuple[str, ...] = (), groups=None, command_transform=None,
                 registered_profiles=None, human_full_suite_authorized: bool = False, on_plan: Callable | None = None,
                 allowed_paths: tuple[str, ...] = ()) -> tuple:
    started_plan = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    verify_deadline = min(deadline, started_plan + timeout_seconds)
    require_managed_parallelism(repo, fallback_argv, coverage_required=True, registered_only=registered_only)
    before = file_hashes(repo)
    plan = select_plan(repo=repo, base_sha=base_sha, base_files=base_files, candidate_files=before,
                       changed_paths=changed_paths, required_acs=required_acs, fallback_argv=fallback_argv,
                       groups=groups, registered_only=registered_only, aggregate_sources=aggregate_sources,
                       registered_profiles=registered_profiles,
                       human_full_suite_authorized=human_full_suite_authorized,
                       allowed_paths=allowed_paths)
    if registered_only and registered_profiles is not None:
        binding_errors = registered_binding_problems(plan, registered_profiles)
        if binding_errors:
            plan["reason_codes"] = list(plan.get("reason_codes", ())) + binding_errors
            # An untrusted/ambiguous profile is a hard HOLD before running
            # pytest; it cannot become repair-eligible test failure.
            for entry in plan["groups"]:
                entry.update(state="UNPROVEN", execute=False)
                entry["explanation"] = list(entry.get("explanation", ())) + binding_errors
    if command_transform:
        for entry in plan["groups"]:
            argv, decision = command_transform(entry["binding"]["argv"], entry["group"])
            entry["binding"]["argv"] = argv
            entry["pytest_profile"] = decision
            entry["binding"]["pytest_profile"] = {k: decision[k] for k in ("profile", "workers", "host_slots")}
    # A provisional test family is only a diagnostic preflight. An actual
    # candidate PASS still requires the final, exact registered full profile.
    provisional = (select_provisional_tests(
        base_files=base_files, candidate_files=before,
        changed_paths=changed_paths, plan=plan, fallback_argv=fallback_argv)
        if registered_only and not human_full_suite_authorized else None)
    if provisional is not None:
        plan["provisional_mapping"] = provisional
    apply_evidence(plan, prior, output_dir)
    plan["started_at"] = started_at
    plan["selection_seconds"] = time.monotonic() - started_plan
    for entry in plan["groups"]:
        entry.update(actual_argv=None, tests_executed=pytest_counts(b""),
                     workers_requested=(registered_workers(entry["binding"]["argv"]) if registered_only
                                        else entry.get("pytest_profile", {}).get("workers", 0)),
                     workers_actual=None, wall_seconds=0.0)
    if on_plan:
        on_plan(plan)  # Keep failure diagnostics visible in EF01 and the action result.
    save("mt01_plan", "mt01.plan.json", (canonical(plan) + "\n").encode())
    stdout, stderr = [], []
    code = 0
    def finish():
        for group in plan["groups"]:
            if group["state"] not in ("REUSE", "UNPROVEN") and "result" not in group:
                group.update(state="UNPROVEN", execute=False, result="UNPROVEN",
                             reason="NOT_EXECUTED_AFTER_UNPROVEN_GROUP")
        plan["finished_at"] = datetime.now(timezone.utc).isoformat()
        plan["wall_seconds"] = time.monotonic() - started_plan
        plan["narrow_execution_observed"] = not plan["fallback"] and any(
            g.get("actual_exit_code") == 0 and g.get("result") == "PASS" for g in plan["groups"])
        save("mt01_results", "mt01.results.json", (canonical(plan) + "\n").encode())

    def unproven(entry, reason, started):
        entry.update(state="UNPROVEN", result="UNPROVEN", reason=reason,
                     actual_exit_code=None, wall_seconds=time.monotonic() - started,
                     finished_at=datetime.now(timezone.utc).isoformat())

    def observe_workers(entry, out):
        actual = observed_xdist_workers(out)
        entry["workers_actual"] = actual
        entry["workers_console_reported"] = observed_console_workers(out)
        entry["worker_count_provenance"] = (
            "XDIST_CONTROLLER_COLLECTION_HOOK" if actual is not None else "UNOBSERVED"
        )
        if "pytest_profile" in entry:
            entry["pytest_profile"].update(
                workers_requested=entry["workers_requested"],
                workers_actual=actual, observed_workers=actual,
                worker_count_provenance=entry["worker_count_provenance"],
                execution_status="12_WORKERS_EXECUTED" if actual == 12 and entry["workers_requested"] == 12
                else "SERIAL_PYTEST_EXECUTED" if actual == 0 and entry["workers_requested"] == 0
                else "WORKER_COUNT_UNPROVEN")
    # Run provisional tests BEFORE expensive broad regression. A nonzero
    # determinate result must lead to bounded Verify-Fix without falsely
    # claiming a failed/successful registered verification. Unproven
    # infrastructure or source drift stops both preflight and full suite.
    if provisional is not None:
        argv = provisional["argv"]
        started = time.monotonic()
        try:
            remaining = verify_deadline - started
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout_seconds)
            response, out, err, junit_bytes, junit_error = _run_pytest_with_junit(
                argv, repo=repo, timeout=remaining,
            )
        except ValueError as exc:
            provisional.update(result="UNPROVEN", reason=str(exc), actual_exit_code=None)
            finish()
            return subprocess.CompletedProcess(fallback_argv, 3, "", str(exc)), plan
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode()
            err = exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode()
            save("mt01_provisional_stdout", "mt01.provisional.stdout.log", out)
            save("mt01_provisional_stderr", "mt01.provisional.stderr.log", err)
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_TIMEOUT",
                               actual_exit_code=None)
            finish()
            raise subprocess.TimeoutExpired(argv, timeout_seconds, out, err) from exc
        except OSError:
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_LAUNCH_ERROR",
                               actual_exit_code=None)
            finish()
            raise
        save("mt01_provisional_stdout", "mt01.provisional.stdout.log", out)
        save("mt01_provisional_stderr", "mt01.provisional.stderr.log", err)
        if junit_bytes is not None:
            save("mt01_provisional_junit", "mt01.provisional.junit.xml", junit_bytes)
        current = file_hashes(repo)
        workers = observed_xdist_workers(out)
        provisional.update(worker_count_provenance=(
                               "XDIST_CONTROLLER_COLLECTION_HOOK" if workers is not None else "UNOBSERVED"),
                           workers_console_reported=observed_console_workers(out),
                           actual_exit_code=response.returncode,
                           wall_seconds=time.monotonic()-started,
                           tests_executed=pytest_counts(out), workers_requested=12,
                           workers_actual=workers)
        if current != before:
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_SOURCE_DRIFT")
            finish()
            raise RuntimeError("MT01_SOURCE_DRIFT_DURING_VERIFICATION")
        diag, junit_issue = _diagnose_junit_and_infra(
            out, err, response.returncode, junit_bytes, junit_error,
            repo=repo, candidate_files=before,
        )
        if junit_issue is not None or workers not in (None, 12) or any(s in diag for s in _INFRASTRUCTURE_MARKERS):
            reason = f"PROVISIONAL_{junit_issue}" if junit_issue is not None else "PROVISIONAL_INFRASTRUCTURE_ERROR"
            provisional.update(result="UNPROVEN", reason=reason)
            if junit_issue is not None:
                provisional["junit_error"] = junit_issue
            finish()
            return subprocess.CompletedProcess(fallback_argv, 3, out.decode("utf-8","replace"),
                                               err.decode("utf-8","replace")), plan
        if response.returncode == 1 and re.search(
                rb"(?m)^FAILED\s|^=+ FAILURES =+|\b[1-9][0-9]* failed\b", out + b"\n" + err):
            provisional.update(result="FAIL", reason="PROVISIONAL_TEST_FAILED")
            finish()
            return subprocess.CompletedProcess(fallback_argv, 1, out.decode("utf-8","replace"),
                                               err.decode("utf-8","replace")), plan
        if response.returncode != 0 or workers != 12 or not re.search(rb"\b[1-9][0-9]* passed\b", out):
            provisional.update(result="UNPROVEN", reason="PROVISIONAL_NO_VALID_PYTEST_PASS")
            finish()
            return subprocess.CompletedProcess(fallback_argv, 3, out.decode("utf-8","replace"),
                                               err.decode("utf-8","replace")), plan
        provisional["result"] = "PASS_PREFLIGHT_ONLY"
    if not plan.get("groups"):
        code = 5
    # Shared verify budget, not a fresh timeout per test group.
    for index, entry in enumerate(plan["groups"]):
        if entry["state"] == "REUSE":
            continue
        if not entry["execute"]:
            entry.update(result="UNPROVEN", reason="NO_APPROVED_SCOPE")
            code = 5
            break
        argv = entry["binding"]["argv"]
        remaining = verify_deadline - time.monotonic()
        started = time.monotonic()
        entry.update(actual_argv=list(argv), started_at=datetime.now(timezone.utc).isoformat())
        try:
            if remaining <= 0:
                entry["actual_argv"] = None
                raise subprocess.TimeoutExpired(argv, timeout_seconds)
            result, out, err, junit_bytes, junit_error = _run_pytest_with_junit(
                argv, repo=repo, timeout=remaining,
            )
        except ValueError as exc:
            unproven(entry, str(exc), started)
            code = 3
            break
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode()
            err = exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode()
            save(f"mt01_{index}_stdout", f"mt01.{index}.stdout.log", out)
            save(f"mt01_{index}_stderr", f"mt01.{index}.stderr.log", err)
            entry["tests_executed"] = pytest_counts(out)
            observe_workers(entry, out)
            unproven(entry, "VERIFY_TIMEOUT", started)
            finish()
            raise subprocess.TimeoutExpired(argv, timeout_seconds, b"\n".join(stdout + [out]),
                                          b"\n".join(stderr + [err])) from exc
        except OSError as exc:
            save(f"mt01_{index}_stderr", f"mt01.{index}.stderr.log", str(exc).encode())
            unproven(entry, "VERIFY_INFRASTRUCTURE_ERROR", started)
            finish()
            raise
        stdout.append(out)
        stderr.append(err)
        refs = {}
        for kind, data in (("stdout", out), ("stderr", err)):
            key = f"mt01_{index}_{kind}"
            save(key, f"mt01.{index}.{kind}.log", data)
            refs[kind] = evidence_files[key]
        if junit_bytes is not None:
            junit_key = f"mt01_{index}_junit"
            save(junit_key, f"mt01.{index}.junit.xml", junit_bytes)
            refs["junit"] = evidence_files[junit_key]
        entry.update(actual_exit_code=result.returncode, wall_seconds=time.monotonic() - started,
                     finished_at=datetime.now(timezone.utc).isoformat(), tests_executed=pytest_counts(out),
                     artifacts=refs)
        observe_workers(entry, out)
        if file_hashes(repo) != before:
            # Do not preserve any PASS from a verifier that mutated its source.
            prior.clear()
            for previous in plan["groups"]:
                if previous.get("result") == "PASS" or previous["state"] == "REUSE":
                    previous.update(state="UNPROVEN", result="UNPROVEN", reason="MT01_SOURCE_DRIFT_DURING_VERIFICATION")
            unproven(entry, "MT01_SOURCE_DRIFT_DURING_VERIFICATION", started)
            finish()
            raise RuntimeError("MT01_SOURCE_DRIFT_DURING_VERIFICATION")
        diagnostics, junit_issue = _diagnose_junit_and_infra(
            out, err, result.returncode, junit_bytes, junit_error,
            repo=repo, candidate_files=before,
        )
        if junit_issue is not None:
            entry["junit_error"] = junit_issue
        # A successful -n12 command without a controller receipt is UNPROVEN,
        # even when the quiet console or command line claims twelve workers.
        # Serial profiles must also remain serial (never silently run xdist).
        if entry["workers_requested"] == 12:
            worker_mismatch = (
                entry["workers_actual"] not in (None, 12)
                or (result.returncode == 0 and entry["workers_actual"] is None)
            )
        elif entry["workers_requested"] == 0:
            worker_mismatch = (
                entry["workers_actual"] not in (None, 0)
                or entry.get("workers_console_reported") not in (None, 0)
            )
        else:
            worker_mismatch = True
        infrastructure = (
            worker_mismatch
            or junit_issue is not None
            or any(s in diagnostics for s in _INFRASTRUCTURE_MARKERS)
        )
        passed = bool(re.search(rb"\b[1-9][0-9]* passed\b", out))
        if result.returncode == 0 and passed and not infrastructure:
            evidence = Evidence.from_success(entry["binding"], exit_code=0, stdout=out,
                                             artifacts=refs, evidence_root=output_dir)
            save(f"mt01_{index}_evidence", f"mt01.{index}.evidence.json", evidence.payload.encode())
            prior[entry["group"]] = evidence
            entry["evidence_sha256"] = evidence.sha256
            entry["result"] = "PASS"
        else:
            determinate = result.returncode == 1 and not infrastructure and bool(re.search(
                rb"(?m)^FAILED\s|^=+ FAILURES =+|\b[1-9][0-9]* failed\b", out + b"\n" + err))
            entry["result"] = "FAIL" if determinate else "UNPROVEN"
            if not determinate:
                reason = (
                    "PYTEST_WORKER_COUNT_MISMATCH" if worker_mismatch
                    else junit_issue if junit_issue is not None
                    else "VERIFY_INFRASTRUCTURE_ERROR" if infrastructure
                    else "NO_SUCCESSFUL_NONEMPTY_PYTEST_RESULT"
                )
                entry.update(state="UNPROVEN", reason=reason)
            # Only a known code failure permits collecting other groups, then repair.
            code = 1 if determinate else (result.returncode if result.returncode not in (0, 1) else 3)
            if not determinate:
                break
    finish()
    if not stdout and all(e["state"] == "REUSE" for e in plan["groups"]):
        stdout = [b"MT01: all applicable groups have strict prior successful evidence\n"]
    return subprocess.CompletedProcess(fallback_argv, code, b"\n".join(stdout).decode(),
                                       b"\n".join(stderr).decode()), plan
