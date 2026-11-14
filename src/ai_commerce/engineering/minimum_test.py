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
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


COLLECTION_SCHEMA_VERSION = 1
# Input/reporting bounds only; no runtime deadline or subprocess is activated.
COLLECTION_TIMEOUT_SECONDS = 60
_COLLECTION_MAX_IDS = 100_000
_COLLECTION_MAX_BYTES = 16 * 1024 * 1024
_COLLECTION_MAX_OUTPUT = 256 * 1024
_COLLECTION_HOLD = "P1B_BOUNDED_WINDOWS_COLLECTION_EXECUTOR_REQUIRED"


# Reviewed MT01 source registry, deliberately separate from EF02 run profiles.
# This argv is an identity for the collect-only observer, never a run command.
COLLECTION_ONLY_MODE = "COLLECTION_ONLY"
COLLECTION_ONLY_SCOPE = ("tests/test_minimum_test.py",)
COLLECTION_ONLY_PROFILES = MappingProxyType({
    "mt01-atb01-collect-serial": (
        "python", "-m", "pytest", "tests/test_minimum_test.py", "-q"),
})
COLLECTION_ONLY_REGISTRY_SHA256 = digest(canonical(dict(
    version=1, mode=COLLECTION_ONLY_MODE, scope=COLLECTION_ONLY_SCOPE,
    workers_requested=0, scheduler="serial", profiles=dict(COLLECTION_ONLY_PROFILES),
)).encode())


def select_collection_only_profile(profile: str, *, mode: str) -> tuple[str, ...]:
    """Explicit collection identity only; no CLI, environment or run fallback."""
    if (mode != COLLECTION_ONLY_MODE or not isinstance(profile, str)
            or profile not in COLLECTION_ONLY_PROFILES):
        raise ValueError("COLLECTION_ONLY_PROFILE_SELECTION_REQUIRED")
    return COLLECTION_ONLY_PROFILES[profile]


def _collection_path(value: object) -> str:
    # P0 path semantics. Only the path part of a nodeid is normalized.
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


def _collection_nodeids(values: object) -> list[str]:
    """P0-compatible identities, retaining order for worker reconciliation.

    P0 imports this module: do not import verification back here. A later P1-B
    companion adapter can share these primitives with P0. Parity tests pin its
    existing sorted digest, path normalization and parameter identity semantics.
    """
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= _COLLECTION_MAX_IDS:
        raise ValueError("INVALID_NODEID_COUNT")
    result = []
    size = 0
    for value in values:
        if (not isinstance(value, str) or not 1 <= len(value) <= 2048
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError("INVALID_NODEID")
        path, sep, case = value.partition("::")
        path = _collection_path(path)
        if not sep or not case or not path.endswith(".py"):
            raise ValueError("INVALID_NODEID")
        nodeid = path + sep + case
        try:
            size += len(nodeid.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("INVALID_NODEID_ENCODING") from exc
        if size > 8 * 1024 * 1024:
            raise ValueError("NODEIDS_OVERSIZED")
        result.append(nodeid)
    if len(set(result)) != len(result):
        raise ValueError("DUPLICATE_NODEID")
    return result


def collection_nodeids_digest(nodeids: object) -> str:
    """Canonical P0 identity digest; worker order is checked separately."""
    return digest(canonical(sorted(_collection_nodeids(nodeids))).encode())


def _collection_binding(value: object, registered_profiles: Mapping[str, Sequence[str]]) -> dict:
    if not isinstance(value, dict) or not isinstance(registered_profiles, Mapping):
        raise ValueError("COLLECTION_BINDING_REQUIRED")
    result = {}
    for key, pattern in (("candidate_sha", r"[0-9a-f]{40}"),
                         ("source_sha256", r"[0-9a-f]{64}"),
                         ("environment_sha256", r"[0-9a-f]{64}"),
                         ("python_sha256", r"[0-9a-f]{64}"),
                         ("argv_sha256", r"[0-9a-f]{64}"),
                         ("registered_profile", r"[a-z0-9][a-z0-9_.-]{0,79}")):
        item = value.get(key)
        if not isinstance(item, str) or re.fullmatch(pattern, item) is None:
            raise ValueError("COLLECTION_BINDING_REQUIRED")
        result[key] = item
    scope = value.get("collection_scope")
    if not isinstance(scope, (list, tuple)) or not 1 <= len(scope) <= 256:
        raise ValueError("UNSUPPORTED_COLLECTION_SCOPE")
    roots = [_collection_path(p) for p in scope]
    # Registered argv use exact canonical relative paths, never node selectors,
    # options, shell strings, Windows device paths, wildcards or traversal.
    if (list(scope) != roots or len(set(roots)) != len(roots)
            or any(not re.fullmatch(r"[A-Za-z0-9_./-]+", p) for p in roots)
            or any(part.endswith(".") or re.fullmatch(
                r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part)
                   for p in roots for part in p.split("/"))):
        raise ValueError("UNSUPPORTED_COLLECTION_SCOPE")
    argv = value.get("profile_argv")
    registered = registered_profiles.get(result["registered_profile"])
    if (not isinstance(argv, (list, tuple)) or not isinstance(registered, (list, tuple))
            or list(argv) != list(registered)):
        raise ValueError("UNREGISTERED_COLLECTION_ARGV")
    parallel = list(argv[-2:]) == ["-n", "12"]
    expected = ["-m", "pytest", *roots, "-q", *(["-n", "12"] if parallel else [])]
    if not argv or argv[0] not in ("python", "py") or list(argv[1:]) != expected:
        raise ValueError("UNSAFE_COLLECTION_ARGV")
    if result["argv_sha256"] != digest(canonical(list(argv)).encode()):
        raise ValueError("COLLECTION_ARGV_DIGEST_MISMATCH")
    workers = 12 if parallel else 0
    if (value.get("platform") != "Windows" or type(value.get("workers_requested")) is not int
            or value["workers_requested"] != workers
            or value.get("scheduler") != ("load" if parallel else "serial")):
        raise ValueError("COLLECTION_PROFILE_BINDING_MISMATCH")
    if result["registered_profile"] in COLLECTION_ONLY_PROFILES:
        exact = select_collection_only_profile(
            result["registered_profile"], mode=value.get("profile_mode"))
        if (list(argv) != list(exact) or roots != list(COLLECTION_ONLY_SCOPE)
                or workers != 0):
            raise ValueError("COLLECTION_ONLY_PROFILE_MISMATCH")
        if value.get("registry_sha256") != COLLECTION_ONLY_REGISTRY_SHA256:
            raise ValueError("COLLECTION_REGISTRY_DIGEST_MISMATCH")
        result.update(profile_mode=COLLECTION_ONLY_MODE,
                      registry_sha256=COLLECTION_ONLY_REGISTRY_SHA256)
    result.update(profile_argv=list(argv), collection_scope=roots, platform="Windows",
                  workers_requested=workers, scheduler=value["scheduler"])
    if set(value) != set(result):
        raise ValueError("COLLECTION_BINDING_REQUIRED")
    return result


def validate_collection_observation(*, expected_binding: object,
                                    registered_profiles: Mapping[str, Sequence[str]],
                                    observation: object) -> dict:
    """Pure, bounded admission checks; never execute pytest or establish PASS.

    The caller must obtain the registry and expected binding from an immutable
    trusted candidate, not from this observation. These checks cannot attest
    that caller-supplied source/environment/scratch claims are true. P1-A has
    NO subprocess launcher, scratch creation, artifact writer or trusted-proof
    promotion. Every result holds for the separately reviewed P1-B Windows
    process containment/source-snapshot adapter, then independent verification.
    Raw parameterized identities remain private input, never public receipts.
    """
    receipt = dict(schema_version=COLLECTION_SCHEMA_VERSION, kind="MT01_COLLECTION_OBSERVATION",
                   status="HOLD", hold_reason=_COLLECTION_HOLD, collection_status="INVALID",
                   provenance="UNPROVEN", trusted_windows_proof=False, tests_collected=None,
                   tests_executed=None, acceptance_pass=False, candidate_sha=None,
                   source_sha256=None, argv_sha256=None, environment_binding_sha256=None,
                   binding_sha256=None, registered_profile=None, selected_test_paths=[],
                   profile_mode=None, registry_sha256=None,
                   collected_count=None, nodeids_sha256=None, ordered_nodeids_sha256=None,
                   workers_requested=None, workers_observed=None, worker_consistency="UNPROVEN",
                   elapsed_seconds=None, reason_codes=[])
    try:
        binding = _collection_binding(expected_binding, registered_profiles)
        receipt.update(candidate_sha=binding["candidate_sha"], source_sha256=binding["source_sha256"],
                       argv_sha256=binding["argv_sha256"], registered_profile=binding["registered_profile"],
                       selected_test_paths=binding["collection_scope"],
                       profile_mode=binding.get("profile_mode"),
                       registry_sha256=binding.get("registry_sha256"),
                       workers_requested=binding["workers_requested"],
                       binding_sha256=digest(canonical(binding).encode()),
                       environment_binding_sha256=digest(canonical({k: binding[k] for k in
                           ("platform", "python_sha256", "environment_sha256")}).encode()))
        if not isinstance(observation, dict):
            raise ValueError("COLLECTION_OBSERVATION_REQUIRED")
        if (type(observation.get("schema_version")) is not int
                or observation["schema_version"] != COLLECTION_SCHEMA_VERSION):
            raise ValueError("COLLECTION_SCHEMA_UNSUPPORTED")
        provenance = observation.get("provenance")
        if provenance not in ("SYNTHETIC", "PYTEST_CONTROLLER"):
            raise ValueError("COLLECTION_PROVENANCE_UNPROVEN")
        receipt["provenance"] = provenance
        if _collection_binding(observation.get("binding"), registered_profiles) != binding:
            raise ValueError("COLLECTION_IDENTITY_MISMATCH")
        if observation.get("collect_only") is not True or observation.get("errors") != []:
            raise ValueError("COLLECTION_CONTROLLER_INCOMPLETE")
        outcome = observation.get("outcome")
        if not isinstance(outcome, dict):
            raise ValueError("COLLECTION_OUTCOME_REQUIRED")
        if outcome.get("timed_out") is not False:
            raise ValueError("COLLECTION_TIMEOUT")
        elapsed = outcome.get("elapsed_seconds")
        if type(elapsed) not in (int, float) or not 0 <= elapsed <= COLLECTION_TIMEOUT_SECONDS:
            raise ValueError("COLLECTION_ELAPSED_INVALID")
        receipt["elapsed_seconds"] = float(elapsed)
        if type(outcome.get("exit_code")) is not int or outcome["exit_code"] != 0:
            raise ValueError("COLLECTION_PROCESS_FAILED")
        if (type(observation.get("session_exit_code")) is not int
                or observation["session_exit_code"] != outcome["exit_code"]):
            raise ValueError("COLLECTION_SESSION_INCOMPLETE")
        if any(outcome.get(k) != binding["source_sha256"] for k in
               ("source_before_sha256", "source_after_sha256")):
            raise ValueError("COLLECTION_SOURCE_MUTATED")
        if (outcome.get("scratch_safe") is not True or outcome.get("outputs_safe") is not True
                or outcome.get("cleanup_complete") is not True):
            raise ValueError("COLLECTION_CONTAINMENT_UNPROVEN")
        for key, maximum in (("output_bytes", _COLLECTION_MAX_OUTPUT),
                             ("artifact_bytes", _COLLECTION_MAX_BYTES)):
            if type(outcome.get(key)) is not int or not 0 <= outcome[key] <= maximum:
                raise ValueError("COLLECTION_OUTPUT_OVERSIZED")
        workers = binding["workers_requested"]
        started, finished = observation.get("workers_started"), observation.get("workers_finished")
        records = observation.get("worker_collections")
        if not all(isinstance(v, (list, tuple)) for v in (started, finished, records)):
            raise ValueError("COLLECTION_WORKERS_INCOMPLETE")
        required = [f"gw{i}" for i in range(workers)]
        if (len(started) != workers or len(finished) != workers
                or any(not isinstance(w, str) for w in (*started, *finished))
                or set(started) != set(required) or set(finished) != set(required)):
            raise ValueError("COLLECTION_WORKERS_INCOMPLETE")
        if workers:
            if observation.get("serial_nodeids") is not None or len(records) != workers:
                raise ValueError("COLLECTION_MODE_MISMATCH")
            if any(not isinstance(r, dict) or r.get("worker_id") not in required for r in records):
                raise ValueError("COLLECTION_WORKERS_INCOMPLETE")
            if len({r["worker_id"] for r in records}) != workers:
                raise ValueError("COLLECTION_WORKERS_INCOMPLETE")
            lists = [r.get("nodeids") for r in records]
        else:
            if records:
                raise ValueError("COLLECTION_MODE_MISMATCH")
            lists = [observation.get("serial_nodeids")]
        reference = None
        total = 0
        for values in lists:
            ids = _collection_nodeids(values)
            total += sum(len(n.encode()) for n in ids)
            if total > _COLLECTION_MAX_BYTES:
                raise ValueError("COLLECTION_ARTIFACT_OVERSIZED")
            if reference is not None and ids != reference:
                raise ValueError("COLLECTION_WORKER_ORDER_MISMATCH")
            reference = ids
        paths = {n.partition("::")[0] for n in reference}
        roots = binding["collection_scope"]
        if (any(not any(p == root or p.startswith(root + "/") for root in roots) for p in paths)
                or any(not any(p == root or p.startswith(root + "/") for p in paths) for root in roots)):
            raise ValueError("COLLECTION_SCOPE_INCOMPLETE")
        node_digest = collection_nodeids_digest(reference)
        if observation.get("nodeids_sha256") != node_digest:
            raise ValueError("COLLECTION_DIGEST_MISMATCH")
        receipt.update(collection_status="SYNTHETIC" if provenance == "SYNTHETIC" else "OBSERVED_UNTRUSTED",
                       collected_count=len(reference), nodeids_sha256=node_digest,
                       ordered_nodeids_sha256=digest(canonical(reference).encode()),
                       workers_observed=workers, worker_consistency="CONSISTENT" if workers else "SERIAL",
                       tests_executed=0)
    except ValueError as exc:
        receipt["reason_codes"].append(str(exc))
    receipt["reason_codes"].append(_COLLECTION_HOLD)
    return receipt


def collection_observer(*, binding: dict, registered_profiles: Mapping[str, Sequence[str]]):
    """Create an opt-in public-hook plugin, without installing or running it.

    Controller only. Native xdist --collect-only may collect serially; that is
    NOT twelve-worker evidence and fails closed here. P1-B must separately
    review worker collection mechanics and containment before launching it.
    The snapshot is private; publish only validate_collection_observation's
    sanitized HOLD receipt. No path/env/provider/shell operations occur here.
    """
    bound = _collection_binding(binding, registered_profiles)
    import pytest  # Optional test dependency, loaded only on explicit construction.

    class Observer:
        def __init__(self):
            self.collect_only = False
            self.started = []
            self.finished = []
            self.records = []
            self.serial = None
            self.errors = []
            self.exit_code = None
            self.size = 0

        def fail(self, code):
            if code not in self.errors and len(self.errors) < 16:
                self.errors.append(code)

        def worker_id(self, node):
            name = getattr(getattr(node, "gateway", None), "id", None)
            if (not isinstance(name, str)
                    or name not in [f"gw{i}" for i in range(bound["workers_requested"])]):
                self.fail("COLLECTION_WORKERS_INCOMPLETE")
                return None
            return name

        def identities(self, values):
            try:
                ids = _collection_nodeids(values)
                self.size += sum(len(n.encode()) for n in ids)
                if self.size > _COLLECTION_MAX_BYTES:
                    raise ValueError("COLLECTION_ARTIFACT_OVERSIZED")
                return ids
            except ValueError:
                self.fail("COLLECTION_IDENTITIES_INVALID")
                return None

        def pytest_configure(self, config):
            self.collect_only = config.getoption("collectonly", False) is True
            requested = config.getoption("numprocesses", 0)
            if (not self.collect_only or hasattr(config, "workerinput")
                    or (requested or 0) != bound["workers_requested"]
                    or list(config.args) != bound["collection_scope"]
                    or any(config.getoption(key, None) for key in (
                        "keyword", "markexpr", "deselect", "ignore", "ignore_glob", "lf", "stepwise"))):
                self.fail("COLLECTION_CONFIG_UNPROVEN")
                raise ValueError("COLLECTION_CONTROLLER_COLLECT_ONLY_REQUIRED")

        @pytest.hookimpl(optionalhook=True)
        def pytest_testnodeready(self, node):
            name = self.worker_id(node)
            if name is not None:
                if name in self.started:
                    self.fail("COLLECTION_DUPLICATE_WORKER")
                elif len(self.started) < 12:
                    self.started.append(name)

        @pytest.hookimpl(optionalhook=True)
        def pytest_xdist_node_collection_finished(self, node, ids):
            name = self.worker_id(node)
            if name is None:
                return
            if (name not in self.started or name in self.finished
                    or any(r["worker_id"] == name for r in self.records)):
                self.fail("COLLECTION_DUPLICATE_OR_UNSTARTED_WORKER")
                return
            values = self.identities(ids)
            if values is not None and len(self.records) < 12:
                self.records.append(dict(worker_id=name, nodeids=values))

        @pytest.hookimpl(optionalhook=True)
        def pytest_testnodedown(self, node, error):
            name = self.worker_id(node)
            if error is not None or name not in self.started or name in self.finished:
                self.fail("COLLECTION_WORKER_FAILED")
            if name is not None and name not in self.finished and len(self.finished) < 12:
                self.finished.append(name)

        def pytest_collection_finish(self, session):
            if not bound["workers_requested"]:
                if self.serial is not None:
                    self.fail("COLLECTION_DUPLICATE_SERIAL")
                    return
                # Bound iteration before constructing a potentially huge list.
                if len(session.items) > _COLLECTION_MAX_IDS:
                    self.fail("COLLECTION_IDENTITIES_INVALID")
                    return
                self.serial = self.identities([item.nodeid for item in session.items])

        def pytest_runtest_logreport(self, report):
            self.fail("COLLECTION_TEST_EXECUTION_OBSERVED")

        def pytest_collectreport(self, report):
            if report.failed:
                self.fail("COLLECTION_IMPORT_FAILED")

        def pytest_deselected(self, items):
            if items:
                self.fail("COLLECTION_FILTERED_SCOPE")

        def pytest_sessionfinish(self, session, exitstatus):
            if self.exit_code is not None:
                self.fail("COLLECTION_DUPLICATE_SESSION_FINISH")
            self.exit_code = int(exitstatus)

        def snapshot(self, *, outcome: dict) -> dict:
            reference = self.serial if self.serial is not None else (
                self.records[0]["nodeids"] if self.records else None)
            payload = canonical(dict(
                schema_version=COLLECTION_SCHEMA_VERSION, provenance="PYTEST_CONTROLLER",
                binding=bound, collect_only=self.collect_only, errors=self.errors,
                session_exit_code=self.exit_code, workers_started=self.started,
                workers_finished=self.finished, worker_collections=self.records,
                serial_nodeids=self.serial, outcome=outcome,
                nodeids_sha256=collection_nodeids_digest(reference) if reference else None))
            if len(payload.encode()) > _COLLECTION_MAX_BYTES:
                raise ValueError("COLLECTION_ARTIFACT_OVERSIZED")
            return json.loads(payload)

    return Observer()


_WINDOWS_COLLECTION_HOLD = "P1B_WINDOWS_IMPORT_ISOLATION_AND_JOB_ATTESTATION_REQUIRED"
_WINDOWS_COLLECTION_PROVIDER_REQUIREMENTS = (
    "Independently reviewed AppContainer with no network capabilities; actual hosted Windows "
    "tests must observe Win32 denial codes for disposable outside-source reads, writes and loopback.",
    "Pin immutable candidate SHA/tree/files, interpreter, collection registry digest and "
    "allowlisted environment; deny source writes and unsafe Python/pytest/auth overrides.",
    "Read-only candidate and action-local scratch; use handle-based junction/reparse checks "
    "and observe denied escapes, including replacement races.",
    "Create child suspended, assign to a kill-on-close Job before resume/imports; prove "
    "bounded whole-descendant termination and drain on completion, timeout and cancellation.",
    "Entire lifecycle below 60s with bounded output/private nodeids, exact serial profile, "
    "public pytest hooks, source/environment before/after and confirmed handle/scratch cleanup.",
    "Pin actual Windows denial/cleanup test artifacts to provider and candidate; synthetic "
    "fixtures, caller flags, Job/timeouts or environment scrubbing alone never attest isolation.",
)


_WINDOWS_NATIVE_APIS = MappingProxyType({
    "userenv.dll": ("CreateAppContainerProfile", "DeleteAppContainerProfile",
                    "DeriveAppContainerSidFromAppContainerName"),
    "advapi32.dll": ("OpenProcessToken", "GetTokenInformation", "SetSecurityInfo"),
    "kernel32.dll": (
        "CreateProcessW", "InitializeProcThreadAttributeList", "UpdateProcThreadAttribute",
        "DeleteProcThreadAttributeList", "CreateJobObjectW", "SetInformationJobObject",
        "AssignProcessToJobObject", "IsProcessInJob", "QueryInformationJobObject",
        "ResumeThread", "TerminateJobObject", "WaitForSingleObject", "CloseHandle",
        "CreateFileW", "GetFileInformationByHandleEx", "GetFinalPathNameByHandleW"),
    "ws2_32.dll": ("WSAStartup", "socket", "connect", "WSAGetLastError", "closesocket"),
})
_WINDOWS_PROVIDER_BLOCKERS = (
    "HOSTED_WINDOWS_RUNNER_PROVENANCE_UNVERIFIED",
    "NATIVE_APPCONTAINER_LAUNCHER_NOT_IMPLEMENTED",
    "HANDLE_BASED_READONLY_SNAPSHOT_UNPROVEN",
    "NATIVE_DENIAL_AND_JOB_CLEANUP_UNPROVEN",
    "SANDBOX_COLLECTION_ADAPTER_NOT_CONNECTED",
)
_WINDOWS_PROVIDER_ENV = MappingProxyType({
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
})


def windows_collection_native_support(*, enabled: bool = False) -> dict:
    """Read-only export inventory, NOT a sandbox probe or runner attestation.

    No exported function is invoked, profile/ACL/job is created, or child is
    started. Even present exports cannot establish effective OS containment.
    DLLs are loaded from System32 only. Never use CI environment flags as
    evidence that a runner is hosted, disposable, or safe for native probes.
    """
    receipt = dict(status="NOT_INSPECTED", native_isolation="UNPROVEN",
                   trusted_windows_proof=False, acceptance_pass=False,
                   api_surface_sha256=digest(canonical(dict(_WINDOWS_NATIVE_APIS)).encode()),
                   missing_apis=[], reason_codes=[])
    if enabled is not True:
        receipt["reason_codes"] = ["COLLECTION_OPT_IN_REQUIRED"]
        return receipt
    if os.name != "nt":
        receipt.update(status="UNAVAILABLE", reason_codes=["WINDOWS_OS_REQUIRED"])
        return receipt
    import ctypes

    missing = []
    for library, names in _WINDOWS_NATIVE_APIS.items():
        try:
            dll = ctypes.WinDLL(library, winmode=0x800, use_last_error=True)  # SYSTEM32
        except (OSError, AttributeError):
            missing.extend(library + ":" + name for name in names)
            continue
        missing.extend(library + ":" + name for name in names if not hasattr(dll, name))
    receipt.update(status="UNAVAILABLE" if missing else "EXPORTS_PRESENT_UNPROVEN",
                   missing_apis=missing,
                   reason_codes=["WINDOWS_NATIVE_API_UNAVAILABLE"] if missing else
                   ["EXPORT_PRESENCE_IS_NOT_ISOLATION_PROOF"])
    return receipt


def _windows_provider_path(value: object, *, absolute: bool) -> str:
    """Lexical admission only. No resolve/stat/open; junction safety stays HOLD."""
    if not isinstance(value, str) or not 1 <= len(value) <= 240:
        raise ValueError("WINDOWS_PROVIDER_UNSAFE_PATH")
    path = value.replace("\\", "/")
    if absolute:
        if re.fullmatch(r"[A-Za-z]:/[A-Za-z0-9_./ -]+", path) is None:
            raise ValueError("WINDOWS_PROVIDER_UNSAFE_PATH")
        path = path[0].upper() + path[1:]
        parts = path[3:].split("/")
    else:
        if path != value or re.fullmatch(r"[A-Za-z0-9_./-]+", path) is None:
            raise ValueError("WINDOWS_PROVIDER_UNSAFE_PATH")
        parts = path.split("/")
    if any(not part or part.startswith(".") or part.endswith((".", " "))
           or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part)
           for part in parts):
        raise ValueError("WINDOWS_PROVIDER_UNSAFE_PATH")
    return path


def windows_collection_provider_preflight(*, enabled: bool = False, binding: object = None,
                                         request: object = None, probe: object = None) -> dict:
    """Bounded, offline P1-B2b provider admission; it has NO execution path.

    Request/probe data is untrusted, including Win32 error codes and hashes.
    This API neither authenticates a hosted runner nor verifies file handles,
    ACLs, token capabilities, a Job, elapsed time or cleanup. It deliberately
    accepts no launcher, runner flags, credentials or command overrides.
    There is no native probe until independent hosted-runner authorization is
    available. Future probes may run only generated inert fixtures, and must
    create an AppContainer with zero network capabilities, create a suspended
    child, assign/confirm its no-breakaway kill-on-close Job BEFORE resume,
    and retain handles to reject source/scratch reparse replacement races.
    Real denial, process-tree death and cleanup must precede candidate imports.

    Manifest SHA = sha256(canonical(relative-file -> sha256)); environment SHA
    = sha256(canonical(exact child environment)). Both are declarations here,
    never observations. Native denial codes must come from CreateFileW and
    Winsock against disposable outside-scratch/source/loopback fixtures, never
    real secrets or network endpoints. SYNTHETIC fixtures cannot become proof.
    Only sanitized statuses and digests leave this API; even valid probe data
    retains every blocker and never connects collection_observer to a process.
    The 55s declaration ceiling reserves time below 60s, but is not a runtime
    deadline or a change to MT01 AUTO/HUMAN_FULL budgets.
    """
    receipt = dict(kind="MT01_WINDOWS_NATIVE_PROVIDER_PREFLIGHT", schema_version=1,
                   status="HOLD", scope_verdict="SOURCE_ONLY_PARTIAL",
                   hold_reason=_WINDOWS_COLLECTION_HOLD, execution_status="NOT_STARTED",
                   native_isolation="UNPROVEN", trusted_windows_proof=False,
                   acceptance_pass=False, tests_executed=0, tests_collected=None,
                   cleanup_complete=False, request_status="INVALID", probe_status="NOT_OBSERVED",
                   binding_sha256=None, request_sha256=None, probe_sha256=None,
                   limits=dict(lifecycle_seconds=55, output_bytes=_COLLECTION_MAX_OUTPUT,
                               artifact_bytes=_COLLECTION_MAX_BYTES, nodeids=_COLLECTION_MAX_IDS),
                   blockers=list(_WINDOWS_PROVIDER_BLOCKERS), reason_codes=[])
    if enabled is not True:
        receipt["reason_codes"] = ["COLLECTION_OPT_IN_REQUIRED"]
        return receipt
    try:
        bound = _collection_binding(binding, COLLECTION_ONLY_PROFILES)
        if bound.get("profile_mode") != COLLECTION_ONLY_MODE:
            raise ValueError("COLLECTION_ONLY_PROFILE_SELECTION_REQUIRED")
        if not isinstance(request, dict) or len(request) != 5 or set(request) != {
                "security_capability", "source_root", "scratch_root", "source_files", "environment"}:
            raise ValueError("WINDOWS_PROVIDER_REQUEST_REQUIRED")
        if request["security_capability"] != "APPCONTAINER_NO_CAPABILITIES":
            raise ValueError("WINDOWS_SECURITY_CAPABILITY_UNSUPPORTED")
        source = _windows_provider_path(request["source_root"], absolute=True).casefold()
        scratch = _windows_provider_path(request["scratch_root"], absolute=True).casefold()
        if (source == scratch or source.startswith(scratch + "/")
                or scratch.startswith(source + "/")):
            raise ValueError("WINDOWS_PROVIDER_ROOTS_OVERLAP")
        files = request["source_files"]
        if not isinstance(files, dict) or not 1 <= len(files) <= 4096:
            raise ValueError("WINDOWS_PROVIDER_MANIFEST_INVALID")
        paths = set()
        for path, sha in files.items():
            normalized = _windows_provider_path(path, absolute=False).casefold()
            if (normalized in paths or not isinstance(sha, str)
                    or re.fullmatch(r"[0-9a-f]{64}", sha) is None):
                raise ValueError("WINDOWS_PROVIDER_MANIFEST_INVALID")
            paths.add(normalized)
        if not set(bound["collection_scope"]).issubset(files):
            raise ValueError("WINDOWS_PROVIDER_MANIFEST_SCOPE_MISSING")
        if digest(canonical(files).encode()) != bound["source_sha256"]:
            raise ValueError("WINDOWS_PROVIDER_SOURCE_DIGEST_MISMATCH")
        env = request["environment"]
        expected_env = dict(_WINDOWS_PROVIDER_ENV, TEMP=request["scratch_root"],
                            TMP=request["scratch_root"])
        if not isinstance(env, dict) or env != expected_env:
            raise ValueError("WINDOWS_PROVIDER_UNSAFE_ENVIRONMENT")
        if digest(canonical(env).encode()) != bound["environment_sha256"]:
            raise ValueError("WINDOWS_PROVIDER_ENVIRONMENT_DIGEST_MISMATCH")
        receipt.update(request_status="DECLARED_UNTRUSTED",
                       binding_sha256=digest(canonical(bound).encode()),
                       request_sha256=digest(canonical(dict(binding=bound, request=request,
                                                           limits=receipt["limits"])).encode()))
        if probe is not None:
            _windows_collection_probe_declaration(probe, bound, receipt)
    except ValueError as exc:
        # Only fixed source-owned error codes; never echo paths/env/nodeids.
        receipt["reason_codes"].append(str(exc))
    receipt["reason_codes"].append(_WINDOWS_COLLECTION_HOLD)
    return receipt


def _windows_collection_probe_declaration(probe: object, bound: dict, receipt: dict) -> None:
    """Reject incomplete/negative declarations; this function cannot attest them."""
    receipt["probe_status"] = "INVALID"
    required = {"provenance", "binding_sha256", "request_sha256", "source_after_sha256",
                "environment_after_sha256", "python_sha256", "security_capability",
                "capabilities", "job_limit_flags", "events", "denials", "exit_code",
                "timed_out", "orphan_count", "cleanup_complete", "elapsed_seconds",
                "output_bytes", "artifact_bytes"}
    if (not isinstance(probe, dict) or len(probe) != len(required) or set(probe) != required
            or probe["provenance"] not in ("SYNTHETIC", "WINDOWS_NATIVE_UNVERIFIED")):
        raise ValueError("WINDOWS_NATIVE_PROBE_DECLARATION_REQUIRED")
    if (probe["binding_sha256"] != receipt["binding_sha256"]
            or probe["request_sha256"] != receipt["request_sha256"]
            or probe["python_sha256"] != bound["python_sha256"]):
        raise ValueError("WINDOWS_NATIVE_PROBE_BINDING_MISMATCH")
    if probe["source_after_sha256"] != bound["source_sha256"]:
        raise ValueError("WINDOWS_NATIVE_SOURCE_MUTATED")
    if probe["environment_after_sha256"] != bound["environment_sha256"]:
        raise ValueError("WINDOWS_NATIVE_ENVIRONMENT_DRIFT")
    if (probe["security_capability"] != "APPCONTAINER_NO_CAPABILITIES"
            or probe["capabilities"] != []):
        raise ValueError("WINDOWS_SECURITY_CAPABILITY_UNSUPPORTED")
    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE only; neither breakaway flag allowed.
    if type(probe["job_limit_flags"]) is not int or probe["job_limit_flags"] != 0x2000:
        raise ValueError("WINDOWS_NATIVE_JOB_UNPROVEN")
    if probe["events"] != ["CREATE_SUSPENDED", "ASSIGN_JOB", "CONFIRM_JOB",
                           "RESUME", "CHILD_AND_DESCENDANT_READY", "CLOSE_LAST_JOB_HANDLE",
                           "WAIT_CHILD_SIGNALED", "WAIT_DESCENDANT_SIGNALED", "CLEANUP_COMPLETE"]:
        raise ValueError("WINDOWS_NATIVE_JOB_SEQUENCE_UNPROVEN")
    denials = probe["denials"]
    # ERROR_ACCESS_DENIED and WSAEACCES, with successful positive controls.
    codes = {"outside_scratch_read": 5, "readonly_source_write": 5,
             "junction_escape_read": 5, "loopback_connect": 10013}
    if not isinstance(denials, dict) or len(denials) != len(codes) or set(denials) != set(codes):
        raise ValueError("WINDOWS_NATIVE_DENIAL_UNPROVEN")
    for name, code in codes.items():
        record = denials[name]
        if (not isinstance(record, dict) or len(record) != 3
                or set(record) != {"api", "error", "control_succeeded"}
                or record["api"] != ("connect/WSAGetLastError" if name == "loopback_connect"
                                     else "CreateFileW/GetLastError")
                or type(record["error"]) is not int or record["error"] != code
                or record["control_succeeded"] is not True):
            raise ValueError("WINDOWS_NATIVE_DENIAL_UNPROVEN")
    if probe["timed_out"] is not False:
        raise ValueError("WINDOWS_NATIVE_TIMEOUT")
    if type(probe["exit_code"]) is not int or probe["exit_code"] != 0:
        raise ValueError("WINDOWS_NATIVE_PROCESS_FAILED")
    if (type(probe["orphan_count"]) is not int or probe["orphan_count"] != 0
            or probe["cleanup_complete"] is not True):
        raise ValueError("WINDOWS_NATIVE_CLEANUP_UNPROVEN")
    elapsed = probe["elapsed_seconds"]
    if type(elapsed) not in (int, float) or not 0 < elapsed <= receipt["limits"]["lifecycle_seconds"]:
        raise ValueError("WINDOWS_NATIVE_DEADLINE_EXCEEDED")
    for key in ("output_bytes", "artifact_bytes"):
        if type(probe[key]) is not int or not 0 <= probe[key] <= receipt["limits"][key]:
            raise ValueError("WINDOWS_NATIVE_OUTPUT_OVERSIZED")
    receipt.update(probe_status="SYNTHETIC" if probe["provenance"] == "SYNTHETIC"
                   else "DECLARED_UNTRUSTED", probe_sha256=digest(canonical(probe).encode()))
    receipt["reason_codes"].append("WINDOWS_NATIVE_PROOF_NOT_INDEPENDENTLY_ATTESTED")


def bounded_windows_collection(*, enabled: bool = False, expected_binding: object = None,
                               collection_profile: str | None = None,
                               collection_binding: object = None,
                               registered_profiles: Mapping[str, Sequence[str]] | None = None,
                               observation: object = None) -> dict:
    """Opt-in, source-only P1-B2 registration/HOLD; deliberately no subprocess.

    Collection imports arbitrary candidate code. Environment filtering and a
    timeout (even taskkill /T) cannot attest filesystem/network isolation or
    race-free descendant termination. This two-file owner has no reviewed
    native isolation provider, so enabling this API MUST NOT launch Python.
    Caller flags, hook events and source hashes cannot supply that authority.

    Companion PR contract, before any launcher can be enabled:
    * Use the explicit COLLECTION_ONLY source registry identity, separate from
      EF02 verification_profile. Preserve the actual registered run argv.
    * Supply independently verified Windows import isolation (read-only exact
      source, action-local scratch, no credential/network access), an actual OS
      restricted token/AppContainer or equivalent verified native sandbox, handle-based
      rejection of reparse points, and a suspended child assigned to a Job
      before imports, with bounded drain and proven whole-job termination.
    * Attest immutable selected SHA/tree/files, interpreter, registry and
      allowlisted child-environment snapshots before/after collection. Strip
      auth/provider/GitHub secrets and unsafe pytest/Python overrides. Bound
      output/artifact/nodeid bytes and the entire lifecycle to at most 60s;
      report cleanup only after handles, descendants and scratch are gone.
    * Use collection_observer's real sessionfinish/identities privately and
      validate_collection_observation publicly. Import/config/selection drift,
      truncation, crashes or missing native proof must HOLD. Serial collection
      never attests twelve workers. Actual Windows denial/cleanup observations
      and independent provider review are required in a separate scoped PR.

    Bindings/registry are expected trusted-candidate inputs to the pure P1-A
    validator, NOT native attestations. Optional raw observations stay private
    caller input; only the sanitized validator receipt is returned. This API
    creates no scratch, reads no source/environment, installs no hook, and is
    not called by ordinary MT01/EF01/EF02 verification. No runtime budget is
    activated. A future host/registry change requires its own reviewed scope.
    """
    receipt = dict(
        schema_version=COLLECTION_SCHEMA_VERSION, kind="MT01_BOUNDED_WINDOWS_COLLECTION",
        status="HOLD", hold_reason="COLLECTION_OPT_IN_REQUIRED", collection_status="UNPROVEN",
        scope_verdict="SOURCE_ONLY_PARTIAL", native_isolation="UNPROVEN",
        execution_status="NOT_STARTED", provenance="UNPROVEN", trusted_windows_proof=False,
        acceptance_pass=False, tests_collected=None, tests_executed=0,
        candidate_sha=None, source_sha256=None, environment_binding_sha256=None,
        execution_profile=None, execution_argv_sha256=None, execution_binding_sha256=None,
        workers_requested=None, workers_observed=None, collection_profile=None,
        collection_mode=None, collection_workers_requested=None, collection_registry_sha256=None,
        collection_argv_sha256=None, collection_binding_sha256=None, request_sha256=None,
        observation_receipt=None, elapsed_seconds=None, cleanup_complete=False,
        native_provider_contract=dict(version=1, status="REQUIRED_NOT_AVAILABLE",
                                      blockers=list(_WINDOWS_PROVIDER_BLOCKERS),
                                      requirements=list(_WINDOWS_COLLECTION_PROVIDER_REQUIREMENTS)),
        limits=dict(deadline_seconds=COLLECTION_TIMEOUT_SECONDS, nodeids=_COLLECTION_MAX_IDS,
                    output_bytes=_COLLECTION_MAX_OUTPUT, artifact_bytes=_COLLECTION_MAX_BYTES),
        reason_codes=[])
    # Exact bool, so strings/integers cannot accidentally enable even preflight.
    if enabled is not True:
        receipt["reason_codes"] = ["COLLECTION_OPT_IN_REQUIRED"]
        return receipt
    receipt["hold_reason"] = _WINDOWS_COLLECTION_HOLD
    try:
        if isinstance(registered_profiles, Mapping) and any(
                name in registered_profiles for name in COLLECTION_ONLY_PROFILES):
            raise ValueError("COLLECTION_ONLY_PROFILE_NOT_EXECUTION")
        actual = _collection_binding(expected_binding, registered_profiles)
        receipt.update(
            candidate_sha=actual["candidate_sha"], source_sha256=actual["source_sha256"],
            execution_profile=actual["registered_profile"],
            execution_argv_sha256=actual["argv_sha256"],
            execution_binding_sha256=digest(canonical(actual).encode()),
            workers_requested=actual["workers_requested"],
            environment_binding_sha256=digest(canonical({k: actual[k] for k in
                ("platform", "python_sha256", "environment_sha256")}).encode()))
        selected_argv = select_collection_only_profile(collection_profile, mode=COLLECTION_ONLY_MODE)
        collection_registry = {collection_profile: selected_argv}
        if (not isinstance(collection_binding, dict)
                or collection_binding.get("registered_profile") != collection_profile):
            raise ValueError("COLLECTION_SERIAL_PROFILE_MAPPING_REQUIRED")
        serial = _collection_binding(collection_binding, collection_registry)
        # Never derive a serial profile by dropping -n12 or changing workers.
        # A separately registered name is necessary but is not an attestation.
        if (serial["workers_requested"] != 0
                or serial["registered_profile"] == actual["registered_profile"]):
            raise ValueError("COLLECTION_SERIAL_PROFILE_MAPPING_REQUIRED")
        if any(serial[k] != actual[k] for k in (
                "candidate_sha", "source_sha256", "environment_sha256", "python_sha256",
                "platform", "collection_scope")):
            raise ValueError("COLLECTION_SERIAL_BINDING_MISMATCH")
        receipt.update(
            collection_profile=serial["registered_profile"],
            collection_mode=serial["profile_mode"], collection_workers_requested=0,
            collection_registry_sha256=serial["registry_sha256"],
            collection_argv_sha256=serial["argv_sha256"],
            collection_binding_sha256=digest(canonical(serial).encode()),
            request_sha256=digest(canonical(dict(
                execution_binding=actual, collection_binding=serial,
                mode=COLLECTION_ONLY_MODE, limits=receipt["limits"])).encode()))
        # No observation can turn this request into native proof or a launch.
        # In particular, caller-supplied scratch_safe/cleanup claims retain
        # P1-A's SYNTHETIC/OBSERVED_UNTRUSTED HOLD, even with valid hook records.
        if observation is not None:
            receipt["observation_receipt"] = validate_collection_observation(
                expected_binding=serial, registered_profiles=collection_registry,
                observation=observation)
        else:
            receipt["reason_codes"].append("COLLECTION_OBSERVATION_NOT_STARTED")
    except ValueError as exc:
        receipt["reason_codes"].append(str(exc))
    receipt["reason_codes"].append(_WINDOWS_COLLECTION_HOLD)
    return receipt


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
        # A15 Phase 4: offline cost, retry safety, truthful operations summary.
        TestGroup("a15", ("tests/test_a15_operations.py",),
                  ("A15.cost-and-idempotency",),
                  sources("operations", "cost_ledger retry_guard status_report a15_cli"),
                  consumers=("src/ai_commerce/operations/manifest.py",
                             "src/ai_commerce/operations/generation.py",
                             "src/ai_commerce/operations/runner.py",
                             "src/ai_commerce/operations/registry.py"),
                  contracts=("work_items/A15-OPERATIONS-COST-001.json",
                             "docs/engineering/A15_IMPLEMENTATION_REPORT.md")),
        # A16: pre-dispatch ownership only. The inert stubs do NOT prove commercial AC.
        TestGroup("a16", ("tests/test_a16_settlement.py", "tests/test_a16_commercial_review.py"),
                  ("A16.L05.E05.offline-settlement",),
                  sources("settlement", "__init__ schemas evidence ledger review"),
                  consumers=("src/ai_commerce/affiliate_attribution/evidence.py",
                             "src/ai_commerce/affiliate_commerce/service.py",
                             "src/ai_commerce/operations/cost_ledger.py"),
                  contracts=("contracts/SettlementEvidence.v1.schema.json",
                             "contracts/CommercialReview.v1.schema.json",
                             "work_items/A16-SETTLEMENT-EVIDENCE-001.json",
                             "docs/engineering/A16_IMPLEMENTATION_REPORT.md")),
        # A17.1: pre-dispatch owner registration ONLY. Sentinel tests prove NOT IMPLEMENTED.
        TestGroup("a17-1", ("tests/test_a17_variants.py", "tests/test_a17_feedback.py"),
                  ("A17.C07.C10.E06.offline-creative-learning",),
                  sources("creative_scaling", "__init__ variants learning_bridge feedback schemas"),
                  consumers=("src/ai_commerce/composition/authoring.py",
                             "src/ai_commerce/composition/models.py",
                             "src/ai_commerce/learning/export.py",
                             "src/ai_commerce/operations/cost_ledger.py"),
                  contracts=("contracts/CreativeVariantBatch.v1.schema.json",
                             "contracts/CreativeFeedbackProposal.v1.schema.json",
                             "work_items/A17-1-CREATIVE-LEARNING-001.json",
                             "docs/engineering/A17_1_IMPLEMENTATION_REPORT.md")),
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
        # A14 L04: the test is now committed on the recovered review branch.
        # Exact component/test/contract ownership is mandatory at PR integration;
        # no global test suppression and no permission to edit other components.
        TestGroup("a14", ("tests/test_a14_attribution.py",),
                  ("A14.L04.offline-attribution-evidence",),
                  sources("affiliate_attribution", "__init__ __main__ evidence ledger schemas"),
                  contracts=(
                      "contracts/AffiliateAttributionEvidence.v1.schema.json",
                      "src/ai_commerce/affiliate_attribution/README.md",
                      "src/ai_commerce/affiliate_attribution/verification/candidate-bytes.json",
                      "src/ai_commerce/affiliate_attribution/verification/consumer-tests.txt",
                      "src/ai_commerce/affiliate_attribution/verification/verification.json",
                      "docs/engineering/A14_L04_IMPLEMENTATION_REPORT.md",
                  )),
        # A14 E04: reviewed integration of SHA256-verified #223 source; independent of L04.
        TestGroup("a14-e04", ("tests/test_a14_commerce_e2e.py",),
                  ("A14.E04.offline-affiliate-commerce",),
                  sources("affiliate_commerce", "__init__ __main__ schemas service"),
                  contracts=(
                      "contracts/AffiliateCommerceRun.v1.schema.json",
                      "docs/engineering/A14_E04_IMPLEMENTATION_REPORT.md",
                  )),
        TestGroup("foundation", ("tests/test_foundation.py",), ("A01.foundation",), (),
                  contracts=contracts("WorkItem.v0.3 AuthorizationEnvelope.v0.3") + (
                      "docs/engineering/SEMANTIC_OWNERS.md", "docs/engineering/SELECTIVE_COMPONENT_ADOPTION.md",
                      "MANIFESTO.md", "CONSTITUTION.md", "PROJECT_CONTRACT.md")),
        TestGroup("cli", ("tests/test_cli.py", "tests/test_isolation.py"), ("A01.cli-and-isolation",),
                  ("src/ai_commerce/cli.py", "src/ai_commerce/__init__.py", "src/ai_commerce/__main__.py")
                  + sources("core", "__init__ identity provenance")),
        TestGroup("ef01", ("tests/test_ef01.py",), ("EF01.execution",),
                  (eng + "runner.py", eng + "adapters.py", eng + "profile.py", eng + "cli.py", eng + "__main__.py", eng + "__init__.py", eng + "model_defaults.py"),
                  (rem + "worker.py",),
                  ("config/engineering/model-defaults.v1.json",
                   "docs/governance/HUMAN_DECISION_2026-11-02_SOFT_MODEL_DEFAULTS.md")),
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
        TestGroup("merge-queue", ("tests/test_pr_merge_gate.py",),
                  ("MT01.brain-pass-auto-merge",),
                  (".github/scripts/pr-merge-gate.py",),
                  contracts=(".github/workflows/brain-pass-auto-merge.yml",)),
        TestGroup("mt01-v21", ("tests/test_mt01_v21.py",), ("MT01.v2-impact-selection",),
                  (eng + "impact_v21.py",), consumers=(".github/scripts/ef02-pr-ci-tests.py",)),
        # HostOps V4 Windows PLAN is an independently reviewed CI control.
        # A change to it must exercise both HostOps runtime invariants and
        # MT-01 coverage/selection itself; the protected merge gate already
        # requires tests/test_minimum_test.py for this exact script path.
        # This is a CONSUMER-only addition: keep registered test argv unchanged.
        TestGroup("mt01", ("tests/test_minimum_test.py",), ("MT01.selection-and-evidence",),
                  (eng + "minimum_test.py", eng + "verification.py"),
                  (eng + "runner.py", rem + "worker.py", "scripts/HostOps-V4-InstallPlan.ps1",
                   "config/hostops-v3/config-v4.example.json")),
        TestGroup("ef01p", ("tests/test_ef01_performance.py",), ("EF01P.git-and-host-budget",),
                  (eng + "git.py", eng + "pytest_profile.py"), (eng + "runner.py", rem + "worker.py")),
        TestGroup("diagnostics", ("tests/test_ef01_agy_diagnostics.py", "tests/test_ef02_authoring_preflight.py"),
                  ("EF01.provider-diagnostics",), (eng + "runner.py", rem + "authoring_preflight.py"),
                  consumers=("scripts/EF02-Authoring-Preflight.py",)),
        TestGroup("git-integrity", ("tests/test_git_byte_consistency.py", "tests/test_ef02_worktree_line_endings.py",
                                    "tests/test_ef01_git_write_guard.py"), ("EF01.git-integrity",),
                  (eng + "git_byte_consistency.py", eng + "git_write_guard.py")),
        TestGroup("ef02-recovery", ("tests/test_ef02_m0r_fault_injection.py",
                                       "tests/test_ef02_github_read_retry.py"), ("EF02.recovery",),
                  (rem + "worker.py", rem + "transport.py", rem + "github_read_retry.py")),
        TestGroup("hostops-v1", ("tests/test_hostops_request.py",), ("HOSTOPS.v1.readonly",),
                  ("scripts/hostops_request.py",)),
        # HostOps v3: reviewed offline contract, consumer, fixed PowerShell and 48-ID
        # scenario-manifest ownership. This registers test closure only; NOT a
        # production broker, live Windows two-account or laptop access grant.
        TestGroup("hostops-v3", (
            "tests/test_hostops_v3.py",
            "tests/test_hostops_v3_integration.py",
            "tests/test_hostops_v3_repair.py",
        ), ("HOSTOPS.v3.security-contract", "HOSTOPS.v3.issue225-matrix"),
                   sources("hostops_v3",
                           "__init__ contract auth filesystem journal worker security broker transport cli")
                   ,
                   # Executable CLI/PowerShell entrypoints are governed interface
                   # contracts in this first-component onboarding snapshot;
                   # the protected gate demands source-only TestGroup.paths.
                   contracts=(
                       "scripts/hostops_v3.py",
                       "scripts/hostops_v3_release.py",
                       "scripts/hostops_v3_schemas.py",
                       "scripts/hostops_v3_verify.py",
                       "scripts/HostOps-V3-Launch.py",
                       "scripts/HostOps-V3-TokenInventory.ps1",
                       "scripts/HostOps-V4-InstallPlan.ps1",
                       "contracts/hostops-v3.request.schema.json",
                       "contracts/hostops-v3.lease.schema.json",
                       "contracts/hostops-v3.envelope.schema.json",
                       "contracts/hostops-v3.receipt.schema.json",
                       "config/hostops-v3/config.example.json",
                       "config/hostops-v3/config-v4.example.json",
                       "config/hostops-v3/revocations.example.json",
                       "docs/engineering/HOSTOPS_V3_DESIGN.md",
                       "docs/engineering/HOSTOPS_V3_ENGINEERING_REPORT.md",
                       "docs/engineering/hostops_v3_evidence/scenarios.json")),
        TestGroup("hostops-v2", ("tests/test_hostops_v2.py", "tests/test_hostops_ef02_202_recovery.py"),
                  ("HOSTOPS.v2.typed-operations", "HOSTOPS.ef02-202-recovery"),
                  ("scripts/hostops_v2.py", "scripts/hostops_ef02_202_recovery.py")),
        TestGroup("ef02-wake", tuple("tests/" + name + ".py" for name in (
            "test_ef02_wake_d1", "test_ef02_wake_d2", "test_ef02_cdp_doorbell", "test_ef02_plugin_selection_probe", "test_ef02_plugin_turn_handoff", "test_ef02_plugin_turn_store", "test_ef02_plugin_turn_stage_only", "test_ef02_plugin_turn_send_once", "test_ef02_plugin_turn_cli", "test_ef02_plugin_turn_evidence", "test_ef02_d3_github_artifact_authority",
            "test_ef02_d3_signed_origin_consent", "test_ef02_d3_two_grant_delivery", "test_ef02_d5_chrome_acquisition",
            "test_ef02_d5_local_handoff", "test_ef02_d5_local_wake_entry", "test_ef02_d5_local_wake_trigger", "test_ef02_d5_run_registration",
            "test_ef02_d5_wake_readiness_flow", "test_ef02_d5_wake_readiness_cache", "test_ef02_d5_wake_readiness_bootstrap")),
                  ("EF02.wake-boundaries",), sources("remote", "_private_state cdp_doorbell chrome_acquisition completion_event conversation_binding github_artifact_authority local_completion_handoff local_run_registration local_wake_entry local_wake_trigger plugin_selection_probe plugin_selection_probe_cli plugin_turn_handoff plugin_turn_store plugin_turn_stage_only plugin_turn_send_once plugin_turn_cli plugin_turn_evidence wake_control_center signed_origin_consent trusted_origin wake_adapter wake_dual_plugin_preview wake_readiness_flow wake_readiness_cache wake_readiness_bootstrap")),
        # Reviewed, source-only three-runner topology; actual installation is
        # a separate Human-attended Windows admin action.
        TestGroup("three-runner-topology",
                  ("tests/test_three_runner_topology.py",),
                  ("HOSTOPS.three-runner-topology",),
                  ("src/ai_commerce/engineering/three_runner_topology.py",),
                  contracts=("scripts/AI-Commerce-Three-Runners.ps1",)),
        # Independent additive owner: main's protected merge gate can review
        # the exact new source/test and existing EF01 consumer as static data.
        # Do not mutate the existing EF01 registration for a new component.
        TestGroup("ef01-agy-stream",
                  ("tests/test_ef01_agy_stream_profile.py",),
                  ("EF01.agy-stream-observation",),
                  sources("engineering", "agy_stream_profile"),
                  consumers=("src/ai_commerce/engineering/runner.py",)),
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
                registered_profiles: Mapping[str, Sequence[str]] | None = None,
                human_full_suite_authorized: bool = False,
                allowed_paths: Sequence[str] = ()) -> dict:
    # Collection identities must never enter AUTO/HUMAN_FULL run selection,
    # including V2 delegation or a caller merging the two separate registries.
    if registered_profiles is not None and any(
            name in registered_profiles for name in COLLECTION_ONLY_PROFILES):
        raise ValueError("COLLECTION_ONLY_PROFILE_NOT_EXECUTION")
    groups = tuple(approved_groups() if groups is None else groups)
    if human_full_suite_authorized:
        full_commands = (["python", "-m", "pytest", "tests", "-q"],
                         ["python", "-m", "pytest", "tests", "-q", "-n", "12"])
        if (not registered_only or registered_profiles is None or
                list(fallback_argv) not in full_commands or
                list(fallback_argv) not in [list(v) for v in registered_profiles.values()]):
            raise ValueError("UNTRUSTED_OR_UNREGISTERED_HUMAN_FULL_PROFILE")
        # Keep reviewed owner/consumer groups and AC mappings even when the
        # HUMAN chooses the registered full-suite command. Full scope is
        # execution authorization, not permission to invent missing owners.
        #
        # V2.1 already loads reviewed component manifests from immutable HEAD,
        # but this legacy full route previously saw only static approved_groups.
        # Reconstruct the SAME trusted baseline owner graph, and reject any
        # conflicting caller-provided mapping instead of silently replacing it.
        if (repo / ".git").exists():
            from .impact_v21 import load_baseline_component_owner_records
            persisted = load_baseline_component_owner_records(
                root=repo, base_sha=base_sha, existing_groups=approved_groups())
            seen = {group.name: group for group in groups}
            extra = []
            for record in persisted:
                owner = record["group"]
                previous = seen.get(owner.name)
                if previous is not None:
                    if previous.mapping() != owner.mapping():
                        raise ValueError("MT01_HUMAN_FULL_BASELINE_OWNER_CONFLICT")
                else:
                    extra.append(owner)
                    seen[owner.name] = owner
            groups = (*groups, *extra)
    # V2.1 chooses from *actual post-executor changed paths*, not the incoming
    # packet's profile. A broad authoring default is never itself full
    # regression approval. Preserve explicit release/full via separately
    # reviewed workflows, never by guessing intent from tests/-q.
    if (os.environ.get("MT01_V21_ENABLED") == "1" and registered_only and
            registered_profiles is not None and not human_full_suite_authorized):
        from .impact_v21 import registered_action_plan
        return registered_action_plan(
            repo=repo, base_sha=base_sha, base_files=base_files,
            candidate_files=candidate_files, changed_paths=changed_paths,
            required_acs=required_acs, fallback_argv=fallback_argv,
            groups=groups, registered_profiles=registered_profiles,
            aggregate_sources=aggregate_sources, env=env,
            allowed_paths=allowed_paths,
        )
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
    # Production sources such as engineering/minimum_test.py may end in
    # "_test.py" without being pytest modules. Respect reviewed source-owner
    # paths while retaining fail-closed detection for unowned test-like files.
    reviewed_tests = {t for g in groups for t in g.tests}
    reviewed_sources = {p for g in groups for p in g.paths}
    affected_tests = {p for p in affected
                      if (Path(p).name.startswith("test_") or Path(p).name.endswith("_test.py"))
                      and (p not in reviewed_sources or p in reviewed_tests)}
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
        candidates = [
            (name, list(argv)) for name, argv in sorted((registered_profiles or {}).items())
            if name != "mt01-component-onboarding"
            and list(argv[:3]) in (["python", "-m", "pytest"], ["py", "-m", "pytest"])
            and list(argv[3:]) in eligible_args
        ]
        preferred = f"mt01-{group.name}-tests"
        matching_owner = next((entry for entry in candidates if entry[0] == preferred), None)
        if matching_owner is not None:
            registered[group.name] = matching_owner
        elif len(candidates) == 1:
            registered[group.name] = candidates[0]
        # Multiple unrelated aliases are not independent registered proof.
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
    if human_full_suite_authorized:
        reasons.append("AUTHENTICATED_HUMAN_EXPLICIT_FULL_SUITE")
        # A full test run cannot establish an AC with no reviewed owner.
        # Do not let the broader command silently turn an unknown contract
        # into a passing acceptance receipt.
        if set(required_acs) - {ac for g in groups for ac in g.acs}:
            reasons.append("HUMAN_FULL_AC_MAPPING_UNPROVEN")
            fallback_proven_scope = False
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
            "fallback": bool(reasons), "aggregate_sources": list(aggregate_sources),
            "human_full_suite_authorized": human_full_suite_authorized, "groups": entries}


def select_provisional_tests(*, base_files: Mapping[str, str],
                             candidate_files: Mapping[str, str],
                             changed_paths: Sequence[str], plan: dict,
                             fallback_argv: Sequence[str]) -> dict | None:
    """Attempt-local, non-authoritative test preflight for a new component.

    Never changes approved_groups, registered profiles or the final MT-01
    verification binding. Run only new tests and impacted consumer tests from
    the actual candidate import graph, in a tightly bounded pytest command.
    Success is diagnostic only; final registered broad verification remains
    mandatory. Missing or unsafe provenance returns None (full suite only).
    """
    full = list(fallback_argv)
    if (full not in (["python", "-m", "pytest", "tests", "-q", "-n", "12"],
                     ["py", "-m", "pytest", "tests", "-q", "-n", "12"]) or
            not plan.get("fallback") or plan.get("aggregate_sources") or
            plan.get("unsupported_paths")):
        return None
    changed = set(changed_paths)
    if not changed or any(p not in candidate_files for p in changed):
        return None
    tests = sorted(p for p in changed if p.startswith("tests/test_") and p.endswith(".py"))
    if not tests or len(tests) > 24 or any(p in base_files for p in tests):
        return None
    new_sources = sorted(p for p in changed if p.startswith("src/ai_commerce/") and p.endswith(".py"))
    if not new_sources or any(p in base_files for p in new_sources):
        return None
    # An onboarding patch cannot also modify an existing shared module, test,
    # workflow or configuration. Use the ordinary registered full suite then.
    package = {p.split("/")[2] for p in new_sources}
    if len(package) != 1 or package == {"__init__.py"}:
        return None
    permitted = set(new_sources) | set(tests)
    permitted.update(p for p in changed if p.startswith("contracts/") and p.endswith(".schema.json"))
    permitted.update(p for p in changed if p.startswith("docs/engineering/") and p.endswith(".md"))
    if changed - permitted:
        return None
    if not set(tests) <= set(plan.get("unknown_tests", ())):
        return None
    affected = set(plan.get("affected_paths", ()))
    consumer_tests = sorted(p for p in affected if p.startswith("tests/test_") and
                            p.endswith(".py") and p in candidate_files)
    selected = sorted(set(tests) | set(consumer_tests))
    if not selected or len(selected) > 24:
        return None
    if any(str(candidate_files[p]).startswith("SYMLINK:") or
           (not re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py", p))
           for p in selected):
        return None
    return {
        "kind": "MT01_PROVISIONAL_PREFLIGHT_ONLY",
        "test_paths": selected,
        "new_tests": tests,
        "consumer_tests": sorted(set(selected) - set(tests)),
        "source_package": next(iter(package)),
        "candidate_sha256": {p: candidate_files[p] for p in selected},
        "argv": [*full[:3], *selected, "-q", "-n", "12"],
        "registered_final_argv": full,
        "reason": "NEW_UNCOMMITTED_COMPONENT_TESTS_REQUIRE_EARLY_TRIAGE",
        "result": "NOT_RUN",
        "authoritative": False,
    }


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
