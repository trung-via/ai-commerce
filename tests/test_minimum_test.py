"""MT-01 deterministic selection/evidence and real local verification; no vendors."""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ai_commerce.engineering import minimum_test as mt
from ai_commerce.engineering import verification
from ai_commerce.remote.validation import approved_verification_plan
from ai_commerce.remote.scheduler import run_schedule

FULL = ["python", "-m", "pytest", "tests", "-q"]
ROOT = Path(__file__).resolve().parents[1]


def _shadow_workload(count=10, *, history_count=None):
    """Bounded synthetic observations; no pytest collection or execution."""
    import copy
    binding = dict(candidate_sha="a" * 40, source_sha256="b" * 64,
                   environment_sha256="c" * 64, argv_sha256=mt.digest(mt.canonical(
                       ["python", "-m", "pytest", "tests", "-q", "-n", "12"]).encode()),
                   registered_profile="project-tests", platform="Windows",
                   workers_requested=12, scheduler="load", collection_scope=["tests"])
    nodeids = [f"tests/test_family_{i // 10}.py::test_case[variant-{i}]" for i in range(count)]
    collection = dict(schema_version=verification.WORKLOAD_SCHEMA_VERSION,
                      provenance="SYNTHETIC", evidence_sha256="d" * 64,
                      binding=copy.deepcopy(binding), test_count=count, nodeids=nodeids,
                      nodeids_sha256=verification.workload_nodeids_digest(nodeids))
    known = count if history_count is None else history_count
    history = dict(schema_version=verification.WORKLOAD_SCHEMA_VERSION,
                   provenance="SYNTHETIC", evidence_sha256="e" * 64,
                   binding=copy.deepcopy(binding), test_count=known,
                   duration_semantics="setup_call_teardown", age_seconds=0,
                   durations=[dict(nodeid=n, seconds=1.0) for n in nodeids[:known]],
                   startup_seconds=2.0, overhead_seconds=3.0, fixture_seconds=4.0,
                   serial_seconds=0.0, workers_observed=12,
                   wall_seconds=9.0 + known / 2)
    return dict(binding=binding, selected_test_count=count, collection=collection, history=history)


def _assert_shadow_only(receipt):
    assert receipt["status"] == "SHADOW_ONLY"
    assert receipt["execution_status"] == "NOT_EXECUTED"
    assert receipt["state"] == "UNPROVEN"
    assert receipt["acceptance_pass"] is False
    assert receipt["tests_executed"] is None
    assert receipt["soft_deadline_seconds"] is None
    assert receipt["hard_deadline_seconds"] is None
    assert receipt["confidence"] in ("PARTIAL", "UNCALIBRATED")
    assert "PASS" not in receipt.values()
    mt.canonical(receipt)  # Every output must be finite, sanitized JSON data.


def test_shadow_workload_ten_vs_ten_thousand_parameterized_identities_scale():
    small = verification.estimate_workload(**_shadow_workload(10))
    large = verification.estimate_workload(**_shadow_workload(10_000))
    assert small["selected_test_count"] == small["input_nodeid_count"] == 10
    assert large["selected_test_count"] == large["input_nodeid_count"] == 10_000
    assert large["estimated_seconds"] > small["estimated_seconds"] * 100
    assert large["historical_coverage_count"] == 10_000
    assert large["coverage_fraction"] == 1.0
    assert large["nodeids_sha256"] != small["nodeids_sha256"]
    assert len(set(large["nodeid_hashes"])) == 10_000
    for receipt in (small, large):
        _assert_shadow_only(receipt)
        assert receipt["tests_collected"] is None  # Synthetic identities are not collection proof.


def test_shadow_nodeid_normalization_retains_parameters_and_rejects_duplicates():
    digest = verification.workload_nodeids_digest
    first = "tests/test_family.py::test_case[a\\b]"
    second = "tests/test_family.py::test_case[a/b]"
    assert digest([first, second]) == digest([second, first])
    assert digest([first]) != digest([second])
    assert digest([first]) == digest(["./tests\\test_family.py::test_case[a\\b]"])
    with pytest.raises(ValueError, match="DUPLICATE_NODEID"):
        digest([first, "./tests\\test_family.py::test_case[a\\b]"])
    inputs = _shadow_workload()
    inputs["collection"]["nodeids"][1] = inputs["collection"]["nodeids"][0]
    receipt = verification.estimate_workload(**inputs)
    assert "DUPLICATE_NODEID" in receipt["reason_codes"]
    assert receipt["estimated_seconds"] is None
    _assert_shadow_only(receipt)


def test_shadow_workload_heavy_test_and_slow_file_increase_estimate():
    inputs = _shadow_workload(120)
    baseline = verification.estimate_workload(**inputs)
    inputs["history"]["durations"][0]["seconds"] = 200.0
    inputs["history"]["wall_seconds"] = 209.0  # Matching synthetic elapsed, including shared costs.
    heavy = verification.estimate_workload(**inputs)
    assert heavy["estimated_seconds"] > baseline["estimated_seconds"]
    assert heavy["components"]["slow_file_seconds"] == 209.0
    assert heavy["estimated_seconds"] >= 218.0
    spread = _shadow_workload(120)
    concentrated = _shadow_workload(120)
    # Equal count, durations, throughput and wall time; only the file tail differs.
    for record in (concentrated["collection"]["nodeids"], concentrated["history"]["durations"]):
        for i, item in enumerate(record):
            nodeid = f"tests/test_slow_file.py::test_case[variant-{i}]"
            if isinstance(item, dict):
                item["nodeid"] = nodeid
            else:
                record[i] = nodeid
    concentrated["collection"]["nodeids_sha256"] = verification.workload_nodeids_digest(
        concentrated["collection"]["nodeids"])
    assert verification.estimate_workload(**concentrated)["estimated_seconds"] > verification.estimate_workload(
        **spread)["estimated_seconds"]
    _assert_shadow_only(heavy)


@pytest.mark.parametrize("missing", ["workers_observed", "wall_seconds", "startup_seconds"])
def test_shadow_requested_twelve_workers_require_effective_throughput(missing):
    inputs = _shadow_workload(120)
    observed = verification.estimate_workload(**inputs)
    assert observed["effective_workers"] == 2.0
    assert observed["throughput_tests_per_second"] == 2.0
    del inputs["history"][missing]
    receipt = verification.estimate_workload(**inputs)
    assert receipt["workers_requested"] == 12
    assert receipt["effective_workers"] == 1.0
    assert receipt["throughput_tests_per_second"] is None
    assert receipt["estimated_seconds"] > observed["estimated_seconds"]
    assert "EFFECTIVE_THROUGHPUT_UNPROVEN_SERIAL_ASSUMED" in receipt["reason_codes"]
    _assert_shadow_only(receipt)


def test_shadow_throughput_requires_matching_elapsed_and_serial_profile():
    inputs = _shadow_workload(120)
    inputs["history"]["wall_seconds"] = 19.0
    parallel = verification.estimate_workload(**inputs)
    assert parallel["effective_workers"] == parallel["throughput_tests_per_second"] == 12.0
    for binding in (inputs["binding"], inputs["collection"]["binding"], inputs["history"]["binding"]):
        binding.update(workers_requested=0, scheduler="serial", registered_profile="project-tests-serial",
                       argv_sha256=mt.digest(mt.canonical(FULL).encode()))
    inputs["history"].update(workers_observed=0, wall_seconds=129.0)
    serial = verification.estimate_workload(**inputs)
    assert serial["effective_workers"] == serial["throughput_tests_per_second"] == 1.0
    assert serial["estimated_seconds"] > parallel["estimated_seconds"]
    _assert_shadow_only(parallel)
    _assert_shadow_only(serial)


def test_shadow_no_history_and_half_unknown_use_explicit_conservative_assumptions():
    inputs = _shadow_workload(20)
    inputs["history"] = None
    cold = verification.estimate_workload(**inputs)
    partial = verification.estimate_workload(**_shadow_workload(20, history_count=10))
    for receipt in (cold, partial):
        _assert_shadow_only(receipt)
        low, high = receipt["estimate_envelope_seconds"]
        assert 0 < low <= high <= verification._MAX_WORKLOAD_SECONDS
        assert "COLD_TESTS_UNCALIBRATED" in receipt["reason_codes"]
        assert receipt["uncertainty_kind"] == "ASSUMPTION_ENVELOPE_NOT_PREDICTION_INTERVAL"
    assert cold["confidence"] == "UNCALIBRATED"
    assert cold["coverage_fraction"] == cold["historical_coverage_count"] == 0
    assert cold["effective_workers"] == 1.0
    assert partial["confidence"] == "PARTIAL"
    assert partial["coverage_fraction"] == 0.5
    assert partial["historical_coverage_count"] == 10
    assert partial["estimated_seconds"] >= 10 * partial["cold_test_assumptions_seconds"][0] / 2


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), -float("inf"), -1, 0,
                                     10**1000, 86401, True, "1", None])
def test_shadow_corrupt_duration_rejects_entire_history(seconds):
    inputs = _shadow_workload()
    inputs["history"]["durations"][0]["seconds"] = seconds
    receipt = verification.estimate_workload(**inputs)
    assert "INVALID_DURATION" in receipt["reason_codes"]
    assert receipt["confidence"] == "UNCALIBRATED"
    assert receipt["historical_coverage_count"] == 0
    assert receipt["estimated_seconds"] >= 10 * 60
    _assert_shadow_only(receipt)


@pytest.mark.parametrize("key,value", [
    ("candidate_sha", "f" * 40), ("source_sha256", "f" * 64),
    ("environment_sha256", "f" * 64), ("argv_sha256", "f" * 64),
    ("registered_profile", "mt01-mt01-tests"), ("scheduler", "loadfile"),
    ("collection_scope", ["src/package/tests"]),
])
@pytest.mark.parametrize("record", ["collection", "history"])
def test_shadow_source_environment_argv_profile_or_scope_drift_cannot_prove(record, key, value):
    inputs = _shadow_workload()
    inputs[record]["binding"][key] = value
    receipt = verification.estimate_workload(**inputs)
    assert "OBSERVATION_IDENTITY_MISMATCH" in receipt["reason_codes"]
    assert receipt["confidence"] == "UNCALIBRATED"
    assert receipt["historical_coverage_count"] == 0
    if record == "collection":
        assert receipt["estimated_seconds"] is None
        assert receipt["tests_collected"] is None
    _assert_shadow_only(receipt)


@pytest.mark.parametrize("case,reason", [
    ("missing_collection", "OBSERVATION_MISSING"),
    ("missing_evidence", "OBSERVATION_EVIDENCE_MISSING"),
    ("unknown_provenance", "OBSERVATION_PROVENANCE_MISSING"),
    ("bad_schema", "OBSERVATION_SCHEMA_UNSUPPORTED"),
    ("missing_environment", "BINDING_IDENTITY_UNPROVEN"),
    ("unknown_platform", "ENVIRONMENT_UNPROVEN"),
    ("unsupported_scope", "UNSUPPORTED_COLLECTION_SCOPE"),
    ("missing_nested_scope", "COLLECTION_SCOPE_INCOMPLETE"),
    ("out_of_scope", "COLLECTION_SCOPE_INCOMPLETE"),
    ("missing_id", "COLLECTION_COUNT_MISMATCH"),
    ("changed_id", "COLLECTION_DIGEST_MISMATCH"),
    ("digest", "COLLECTION_DIGEST_MISMATCH"),
    ("oversized_count", "INVALID_NODEID_COUNT"),
    ("malformed_id", "UNSUPPORTED_COLLECTION_SCOPE"),
    ("invalid_unicode", "INVALID_NODEID_ENCODING"),
    ("selected_count", "SELECTED_TEST_COUNT_UNPROVEN"),
])
def test_shadow_collection_validation_fails_closed(case, reason):
    inputs = _shadow_workload()
    collection = inputs["collection"]
    if case == "missing_collection":
        inputs["collection"] = None
    elif case == "missing_evidence":
        del collection["evidence_sha256"]
    elif case == "unknown_provenance":
        collection["provenance"] = "console_guess"
    elif case == "bad_schema":
        collection["schema_version"] = True
    elif case == "missing_environment":
        del inputs["binding"]["environment_sha256"]
    elif case == "unknown_platform":
        inputs["binding"]["platform"] = "unknown"
    elif case == "unsupported_scope":
        inputs["binding"]["collection_scope"] = ["../private"]
    elif case == "missing_nested_scope":
        for binding in (inputs["binding"], collection["binding"]):
            binding["collection_scope"].append("src/package/tests")
    elif case == "out_of_scope":
        collection["nodeids"][0] = "src/package/tests/test_nested.py::test_a"
        collection["nodeids_sha256"] = verification.workload_nodeids_digest(collection["nodeids"])
    elif case == "missing_id":
        collection["nodeids"].pop()
    elif case == "changed_id":
        collection["nodeids"][0] += "changed"
    elif case == "digest":
        collection["nodeids_sha256"] = "f" * 64
    elif case == "oversized_count":
        collection["nodeids"] = [collection["nodeids"][0]] * 100_001
    elif case == "malformed_id":
        collection["nodeids"][0] = "tests/../secret.py::test_a"
    elif case == "invalid_unicode":
        collection["nodeids"][0] = "tests/test_a.py::test_a[\ud800]"
    else:
        inputs["selected_test_count"] = True
    receipt = verification.estimate_workload(**inputs)
    assert reason in receipt["reason_codes"]
    assert receipt["estimated_seconds"] is None
    assert receipt["estimate_envelope_seconds"] is None
    assert receipt["tests_collected"] is None
    _assert_shadow_only(receipt)


@pytest.mark.parametrize("case,reason", [
    ("duplicate", "DUPLICATE_NODEID"), ("count", "HISTORY_IDENTITIES_MISMATCH"),
    ("unknown_id", "HISTORY_IDENTITIES_MISMATCH"), ("oversized", "HISTORY_COUNT_INVALID"),
    ("stale", "INVALID_DURATION"), ("semantics", "HISTORY_DURATION_SEMANTICS_UNPROVEN"),
    ("missing_evidence", "OBSERVATION_EVIDENCE_MISSING"),
    ("startup_nan", "INVALID_DURATION"), ("fixture_negative", "INVALID_DURATION"),
    ("wall_inconsistent", "HISTORY_WALL_INCONSISTENT"),
    ("workers_mismatch", "HISTORY_WORKERS_MISMATCH"),
])
def test_shadow_invalid_history_uses_uncalibrated_fallback(case, reason):
    inputs = _shadow_workload()
    history = inputs["history"]
    if case == "duplicate":
        history["durations"][1]["nodeid"] = history["durations"][0]["nodeid"]
    elif case == "count":
        history["test_count"] += 1
    elif case == "unknown_id":
        history["durations"][0]["nodeid"] = "tests/test_other.py::test_case"
    elif case == "oversized":
        history["durations"] *= 2
    elif case == "stale":
        history["age_seconds"] = 31 * 24 * 60 * 60
    elif case == "semantics":
        history["duration_semantics"] = "call_only"
    elif case == "missing_evidence":
        del history["evidence_sha256"]
    elif case == "startup_nan":
        history["startup_seconds"] = float("nan")
    elif case == "fixture_negative":
        history["fixture_seconds"] = -1
    elif case == "wall_inconsistent":
        history["wall_seconds"] = 1
    else:
        history["workers_observed"] = 4
    receipt = verification.estimate_workload(**inputs)
    assert reason in receipt["reason_codes"]
    assert receipt["confidence"] == "UNCALIBRATED"
    assert receipt["historical_coverage_count"] == 0
    assert receipt["effective_workers"] == 1.0
    _assert_shadow_only(receipt)


def test_shadow_startup_fixture_serial_overhead_and_wall_scaling_are_visible():
    inputs = _shadow_workload(120)
    baseline = verification.estimate_workload(**inputs)
    inputs["history"].update(startup_seconds=20, overhead_seconds=30, fixture_seconds=40,
                              serial_seconds=300, wall_seconds=390)
    receipt = verification.estimate_workload(**inputs)
    assert receipt["estimated_seconds"] > baseline["estimated_seconds"]
    assert receipt["estimated_seconds"] >= 390
    assert receipt["components"] == dict(test_effort_seconds=120.0, slow_file_seconds=10.0,
                                         startup_seconds=20.0, fixture_seconds=40.0,
                                         overhead_seconds=30.0, serial_seconds=300.0,
                                         historical_wall_scaled_seconds=300.0)
    _assert_shadow_only(receipt)


def test_shadow_reporting_bound_does_not_become_a_clamped_deadline():
    inputs = _shadow_workload(10_000)
    inputs["history"] = None
    receipt = verification.estimate_workload(**inputs)
    assert "ESTIMATE_OUTSIDE_REPORTING_BOUND" in receipt["reason_codes"]
    assert receipt["estimated_seconds"] is None
    assert receipt["estimate_envelope_seconds"] is None
    _assert_shadow_only(receipt)


def test_shadow_receipt_is_deterministic_private_and_has_no_effects(monkeypatch):
    import copy
    inputs = _shadow_workload()
    private = "tests/test_family_0.py::test_case[private-token-value]"
    inputs["collection"]["nodeids"][0] = private
    inputs["collection"]["nodeids_sha256"] = verification.workload_nodeids_digest(inputs["collection"]["nodeids"])
    inputs["history"]["durations"][0]["nodeid"] = private
    before = copy.deepcopy(inputs)
    def forbidden(*args, **kwargs):
        raise AssertionError("Shadow estimator performed an external effect")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(run=forbidden))
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(verification, "time", SimpleNamespace(monotonic=forbidden))
    receipt = verification.estimate_workload(**inputs)
    assert receipt == verification.estimate_workload(**inputs)
    assert inputs == before
    serialized = mt.canonical(receipt)
    assert "private-token-value" not in serialized
    assert "tests/test_family" not in serialized
    assert receipt["schema_version"] == 1
    assert receipt["confidence"] == "PARTIAL"
    _assert_shadow_only(receipt)
    for record in (inputs["collection"], inputs["history"]):
        record["provenance"] = "TRUSTED_OBSERVATION"
    trusted = verification.estimate_workload(**inputs)
    assert trusted["tests_collected"] == 10
    assert trusted["collection_evidence_sha256"] == "d" * 64
    assert trusted["tests_executed"] is None
    _assert_shadow_only(trusted)


def test_shadow_foundation_preserves_timeout_defaults_and_registered_mt01_argv():
    import inspect
    from ai_commerce.engineering.cli import build_parser
    from ai_commerce.engineering.runner import execute_attempt, DEFAULT_ACTION_TIMEOUT_SECONDS
    parser = build_parser()
    assert parser.get_default("verify_timeout_seconds") == 1800
    assert parser.get_default("timeout_seconds") == parser.get_default("action_timeout_seconds") == 7200
    assert DEFAULT_ACTION_TIMEOUT_SECONDS == 7200
    defaults = inspect.signature(execute_attempt).parameters
    assert defaults["verify_timeout_seconds"].default == 1800
    assert defaults["action_timeout_seconds"].default == 7200
    registry = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text(encoding="utf-8"))
    assert registry["mt01-mt01-tests"] == ["python", "-m", "pytest", "tests/test_minimum_test.py", "-q", "-n", "12"]
    assert registry["project-tests"] == ["python", "-m", "pytest", "tests", "-q", "-n", "12"]


@pytest.fixture(autouse=True)
def _isolate_legacy_selector_mode(monkeypatch):
    """Legacy MT-01 contracts must not inherit the EF01 V2.1 selector toggle.

    The EF01 Windows runner can set MT01_V21_ENABLED=1 for its *outer* impact
    planner.  This test module deliberately covers the V1 selector as well;
    individual V2 tests opt in via monkeypatch.setenv inside their bodies.
    Only the child pytest process is isolated; runtime/CI policy is unchanged.
    """
    monkeypatch.delenv("MT01_V21_ENABLED", raising=False)


def test_legacy_selector_mode_does_not_inherit_runner_toggle(monkeypatch):
    assert os.environ.get("MT01_V21_ENABLED") != "1"
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    assert os.environ["MT01_V21_ENABLED"] == "1"



@pytest.fixture(scope="module")
def real_tree():
    files = mt.file_hashes(ROOT)
    groups = mt.approved_groups()
    # Persisted V2.2 component owners on trusted HEAD are reviewed data too.
    # Do not silently label their committed tests UNMAPPED just because the
    # pre-registration Python registry was deliberately left unchanged.
    import subprocess
    from ai_commerce.engineering.impact_v21 import load_baseline_component_owner_records
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    records = load_baseline_component_owner_records(root=ROOT, base_sha=sha, existing_groups=groups)
    groups = (*groups, *(r["group"] for r in records))
    registry = {"mt01-" + g.name + "-tests": ["python", "-m", "pytest", *g.tests, "-q", "-n", "12"]
                for g in groups}
    registry["project-tests"] = [*FULL, "-n", "12"]
    return SimpleNamespace(files=files, groups=groups, registry=registry, sha=sha,
                           persisted_names=frozenset(r["group"].name for r in records),
                           graph=mt.import_graph(ROOT, files))


def real_plan(real_tree, monkeypatch, path, *, registry=None, acs=(), aggregate=()):
    monkeypatch.setattr(mt, "import_graph", lambda *_: real_tree.graph)
    return mt.select_plan(repo=ROOT, base_sha="6601881e7b498ae139fbe8a8f04c195905b15ed3",
        base_files=real_tree.files, candidate_files=real_tree.files, changed_paths=[path], required_acs=acs,
        fallback_argv=real_tree.registry["project-tests"], registered_only=True,
        groups=real_tree.groups,
        registered_profiles=real_tree.registry if registry is None else registry, aggregate_sources=aggregate, env={})


def test_reviewed_map_contains_exact_real_paths_and_all_current_python_test_families(real_tree):
    declared = [p for g in real_tree.groups for p in (*g.paths, *g.tests, *g.consumers, *g.contracts)]
    assert set(declared) <= real_tree.files.keys()
    mapped_tests = [p for g in real_tree.groups for p in g.tests]
    assert len(mapped_tests) == len(set(mapped_tests))
    # MT-01 bootstrap: new uncommitted tests may be broad-verified by EF01.
    # Once committed (including PR CI's merge HEAD), missing reviewed mapping
    # is still a failure; an unknown test never earns selective coverage.
    registration = mt.reviewed_test_registration(ROOT, real_tree.files, real_tree.groups)
    assert registration["unregistered_committed"] == []
    assert registration["unsafe_new_tests"] == []
    assert "src/ai_commerce/opportunity/trends/tests/test_m02.py" in mapped_tests
    assert {f"a{n:02}" for n in range(2, 13)} <= {g.name for g in real_tree.groups}
    assert "a13" in {g.name for g in real_tree.groups}


@pytest.mark.parametrize("path,expected", [
    ("tests/test_a13_evidence_pack.py", "a13"),
    ("tests/test_a12_claims.py", "a12"),
    ("src/ai_commerce/remote/chrome_acquisition.py", "ef02-wake"),
    ("scripts/hostops_request.py", "hostops-v1"),
    ("scripts/hostops_v2.py", "hostops-v2"),
])
def test_real_known_component_uses_exact_registered_companion(real_tree, monkeypatch, path, expected):
    selected = real_plan(real_tree, monkeypatch, path)
    assert not selected["fallback"], selected["reason_codes"]
    groups = set(selected["selected_groups"])
    assert expected in groups
    # Newly committed declarative owners extend the true downstream closure;
    # every extra group must come from an independently validated base manifest.
    assert groups - {expected} <= real_tree.persisted_names
    if path == "src/ai_commerce/remote/chrome_acquisition.py":
        assert "hostops-personal" in groups  # real reverse consumer of D5 Chrome
    for entry in selected["groups"]:
        profile = "mt01-" + entry["group"] + "-tests"
        assert entry["selected_profile"] == profile
        assert entry["binding"]["argv"] == real_tree.registry[profile]
        assert entry["binding"]["argv"][-2:] == ["-n", "12"]
    assert not selected["narrow_execution_observed"]  # Planning is not an execution saving.


def test_real_source_and_schema_preserve_cli_and_import_consumers(real_tree, monkeypatch):
    selected = real_plan(real_tree, monkeypatch, "contracts/ProductClaimEvidence.v1.schema.json")
    assert not selected["fallback"]
    assert {"a12", "a11", "a04", "cli"} <= set(selected["selected_groups"])
    assert "tests/test_a12_registry.py" in selected["consumer_map"]["a12"]["tests"]
    assert "contracts/ProductClaimEvidence.v1.schema.json" in selected["consumer_map"]["cli"]["closure"]


@pytest.mark.parametrize("path,acs,aggregate,reason", [
    ("pyproject.toml", (), (), "SHARED_TEST_CONFIGURATION_REQUIRES_FULL_SUITE"),
    ("unknown/path.py", (), (), "PATH_OR_AC_MAP_INCOMPLETE"),
    ("tests/test_a12_claims.py", ("AC-UNKNOWN",), (), "PATH_OR_AC_MAP_INCOMPLETE"),
    ("tests/test_a12_claims.py", (), ("b" * 40,), "AGGREGATE_REQUIRES_FRESH_COMBINED_CONSUMER_COVERAGE"),
])
def test_real_uncertainty_config_and_fanin_use_exact_broad_registry(real_tree, monkeypatch, path, acs, aggregate, reason):
    selected = real_plan(real_tree, monkeypatch, path, acs=acs, aggregate=aggregate)
    assert selected["fallback"] and reason in selected["reason_codes"]
    assert selected["changed_paths"] == [path]
    assert selected["groups"][0]["binding"]["argv"] == real_tree.registry["project-tests"]
    assert selected["groups"][0]["state"] == ("UNPROVEN" if path == "pyproject.toml" else "EXECUTE")
    if path == "pyproject.toml":
        assert "A10.trends" in selected["required_acs"] and "a10-m02" in selected["consumer_map"]
    assert selected["fallback_profile"] == "project-tests"


def test_current_registry_does_not_activate_unregistered_groups(real_tree, monkeypatch):
    current = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    selected = real_plan(real_tree, monkeypatch, "tests/test_a12_claims.py", registry=current)
    if "mt01-a12-tests" in current:
        assert not selected["fallback"] and selected["groups"][0]["selected_profile"] == "mt01-a12-tests"
    else:
        assert selected["fallback"]
        assert selected["groups"][0]["binding"]["argv"] == current["project-tests"]
        assert "REGISTERED_ARGV_ONLY_COMPANION_REGISTRATION_REQUIRED" in selected["reason_codes"]


def test_companion_report_has_exact_reviewed_parallel_commands(real_tree):
    report = (ROOT / "docs/engineering/MT01_COMPONENT_AWARE_IMPLEMENTATION_REPORT.md").read_text()
    proposed = json.loads(report.split("```json\n", 1)[1].split("\n```", 1)[0])
    current = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    for key, historical_argv in proposed.items():
        if key not in current:
            continue
        if key == "mt01-ef02-wake-tests":
            # Reviewed D5-only extension: all older Wake tests remain.
            live_tests = current[key][3:-3]
            historical_tests = historical_argv[3:-3]
            assert current[key][:3] == historical_argv[:3]
            assert current[key][-3:] == historical_argv[-3:]
            assert all(path in live_tests for path in historical_tests)
            assert [p for p in live_tests if p not in historical_tests] == [
                "tests/test_ef02_plugin_turn_handoff.py",
                "tests/test_ef02_plugin_turn_store.py",
                "tests/test_ef02_plugin_turn_stage_only.py",
                "tests/test_ef02_plugin_turn_send_once.py",
                "tests/test_ef02_plugin_turn_cli.py",
                "tests/test_ef02_plugin_turn_evidence.py",
                "tests/test_ef02_d5_wake_readiness_flow.py",
                "tests/test_ef02_d5_wake_readiness_cache.py",
                "tests/test_ef02_d5_wake_readiness_bootstrap.py",
            ]
            assert len(live_tests) == len(set(live_tests))
        elif key == "mt01-ef02-recovery-tests":
            # Preserve the dated MT-01 report; only deleted M0-R probe-only
            # tests may disappear from its historical recovery selection.
            historical_tests = historical_argv[3:-3]
            kept = [path for path in historical_tests if (ROOT / path).is_file()]
            retired = [path for path in historical_tests if not (ROOT / path).exists()]
            assert len(retired) == 8
            assert all(path.startswith("tests/test_ef02_m0r_") for path in retired)
            assert current[key] == [*historical_argv[:3], *kept, *historical_argv[-3:]]
        else:
            assert current[key] == historical_argv

    # Historical companion profiles may use aliases rather than a group key.
    # Every current/declared group must still match an actual reviewed argv.
    # The changed EF02 recovery group MUST be registered under its live key.
    combined = {**proposed, **current}
    for g in real_tree.groups:
        expected = ["python", "-m", "pytest", *g.tests, "-q", "-n", "12"]
        key = f"mt01-{g.name}-tests"
        if g.name == "ef02-recovery":
            assert current.get(key) == expected
        elif key in combined:
            assert combined[key] == expected
        elif g.name in real_tree.persisted_names:
            # V2.2 trusted base-commit manifest owners are validated above.
            # They do not require a second static registry registration.
            assert (ROOT / "work_items" / "mt01-components" /
                    f"{g.name}.json").is_file()
        else:
            assert expected in combined.values()


def test_component_local_m02_requires_profile_that_actually_collects_it(real_tree, monkeypatch):
    """M02 must be collected by a real profile OR fail closed on uncertain imports.

    Persisted component consumers can expand the source tree after this legacy
    test was authored. Never interpret a registered profile as coverage when
    imports/consumers are still unproven: the broad 'tests/' fallback does not
    collect the component-local M02 test under 'src/'.
    """
    current = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    path = "src/ai_commerce/opportunity/trends/tracking.py"

    def assert_proven_or_safe_hold(plan, registry):
        assert "a10-m02" in plan["selected_groups"]
        if plan["fallback"]:
            # A source graph that grew with reviewed owner manifests may have
            # unresolved import/consumer edges. Do not fake narrow coverage or
            # execute the registered broad profile that omits local M02 tests.
            # Either incomplete imports OR a missing registered M02 companion
            # must HOLD. The latter is more precise once persisted owners are
            # loaded and the import graph is complete.
            reasons = set(plan["reason_codes"])
            assert reasons & {
                "IMPORT_OR_CONSUMER_MAP_INCOMPLETE",
                "REGISTERED_ARGV_ONLY_COMPANION_REGISTRATION_REQUIRED",
            }, plan
            if "IMPORT_OR_CONSUMER_MAP_INCOMPLETE" in reasons:
                assert any(plan.get(k) for k in ("unknown_tests", "unknown_consumers")) or (
                    "IMPORT_OR_CONSUMER_MAP_INCOMPLETE" in reasons
                )
            assert len(plan["groups"]) == 1
            assert plan["groups"][0]["state"] == "UNPROVEN"
            assert plan["groups"][0]["execute"] is False
            assert plan["groups"][0]["binding"]["argv"] == registry["project-tests"]
            assert "src/ai_commerce/opportunity/trends/tests/test_m02.py" not in registry["project-tests"]
        else:
            assert plan["groups"][0]["state"] != "UNPROVEN"
            assert any(
                "src/ai_commerce/opportunity/trends/tests/test_m02.py" in
                g["binding"]["argv"] for g in plan["groups"]
            )

    selected = real_plan(real_tree, monkeypatch, path, registry=current)
    if "mt01-a10-m02-tests" in current:
        assert_proven_or_safe_hold(selected, current)
    else:
        assert selected["fallback"] and selected["groups"][0]["state"] == "UNPROVEN"
        assert selected["groups"][0]["binding"]["argv"] == current["project-tests"]
    activated = real_plan(real_tree, monkeypatch, path)
    assert_proven_or_safe_hold(activated, real_tree.registry)


@pytest.mark.parametrize("path", ["tests/ef02-watch.test.cjs", "tests/ef02-reconcile.test.cjs"])
def test_python_registry_cannot_claim_javascript_suite_coverage(real_tree, monkeypatch, path):
    selected = real_plan(real_tree, monkeypatch, path)
    assert selected["groups"][0]["state"] == "UNPROVEN" and selected["unsupported_paths"] == [path]
    assert "NON_PYTHON_VERIFICATION_SCOPE_UNPROVEN" in selected["reason_codes"]


@pytest.fixture
def mapped(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "feature_a.py").write_text("VALUE = 0\n")
    (repo / "feature_b.py").write_text("VALUE = 0\n")
    (repo / "contract.json").write_text('{"version": 1}')
    for name in ("a", "b"):
        (repo / "tests" / f"test_{name}.py").write_text(
            f"import feature_{name}\ndef test_value():\n    assert feature_{name}.VALUE == 1\n")
    groups = (
        mt.TestGroup("a", ("tests/test_a.py",), ("AC-A",), ("feature_a.py",), contracts=("contract.json",)),
        mt.TestGroup("b", ("tests/test_b.py",), ("AC-B",), ("feature_b.py",)),
    )
    base = mt.file_hashes(repo)
    (repo / "feature_a.py").write_text("VALUE = 1\n")
    return SimpleNamespace(repo=repo, base=base, groups=groups, evidence=tmp_path / "evidence")


def plan(mapped, changed=("feature_a.py",), acs=(), **kwargs):
    return mt.select_plan(repo=mapped.repo, base_sha="a" * 40, base_files=mapped.base,
                          candidate_files=mt.file_hashes(mapped.repo), changed_paths=list(changed),
                          required_acs=acs, fallback_argv=kwargs.pop("fallback_argv", FULL),
                          groups=mapped.groups, env=kwargs.pop("env", {"dependencies": [["pytest", "8.4.2"]]}), **kwargs)


def evidence(mapped, entry, name="prior", **kwargs):
    mapped.evidence.mkdir(exist_ok=True)
    out = mapped.evidence / (name + ".stdout")
    out.write_bytes(b"1 passed in 0.01s\n")
    return mt.Evidence.from_success(entry["binding"], exit_code=kwargs.pop("exit_code", 0),
                                   stdout=kwargs.pop("stdout", out.read_bytes()),
                                   artifacts={"stdout": out.name}, evidence_root=mapped.evidence, **kwargs)


def test_component_selection_is_finite_and_covers_all_applicable_acs(mapped):
    selected = plan(mapped)
    assert not selected["fallback"]
    assert selected["required_acs"] == ["AC-A"]
    assert [g["group"] for g in selected["groups"]] == ["a"]
    entry = selected["groups"][0]
    assert entry["state"] == "EXECUTE" and entry["execute"]
    assert entry["binding"]["argv"] == [sys.executable, "-m", "pytest", "tests/test_a.py", "-q"]
    assert "contract.json" in entry["binding"]["candidate_files"]
    assert "feature_b.py" not in entry["binding"]["candidate_files"]
    both = plan(mapped, acs=("AC-A", "AC-B"))
    assert {g["group"] for g in both["groups"]} == {"a", "b"}


def test_transitive_imports_consumers_and_contract_changes_select_affected_groups(mapped):
    (mapped.repo / "interface.py").write_text("VALUE = 1\n")
    (mapped.repo / "feature_a.py").write_text("from interface import VALUE\n")
    (mapped.repo / "consumer.py").write_text("from feature_a import VALUE\n")
    (mapped.repo / "tests/test_b.py").write_text("from consumer import VALUE\ndef test_consumer():\n    assert VALUE\n")
    selected = plan(mapped, changed=("interface.py",))
    assert {g["group"] for g in selected["groups"]} == {"a", "b"}
    assert not selected["fallback"]
    # Non-Python contract changes propagate through the producer to its consumers.
    assert [g["group"] for g in plan(mapped, changed=("contract.json",))["groups"]] == ["a", "b"]


@pytest.mark.parametrize("kind", ["unknown-path", "unknown-ac", "new-consumer", "missing-test", "dynamic-import"])
def test_incomplete_mapping_uses_exact_approved_broad_profile(mapped, kind):
    changes, acs = ("feature_a.py",), ()
    if kind == "unknown-path":
        changes = ("unmapped.py",)
    elif kind == "unknown-ac":
        acs = ("AC-NOT-MAPPED",)
    elif kind == "new-consumer":
        (mapped.repo / "tests/test_new.py").write_text("import feature_a\n")
    elif kind == "missing-test":
        (mapped.repo / "tests/test_a.py").unlink()
    else:
        (mapped.repo / "feature_a.py").write_text("module = __import__(name)\n")
    selected = plan(mapped, changed=changes, acs=acs)
    assert selected["fallback"] and len(selected["groups"]) == 1
    assert selected["groups"][0]["binding"]["argv"] == FULL
    assert selected["groups"][0]["state"] == ("UNPROVEN" if kind == "missing-test" else "EXECUTE")


def test_no_broad_profile_is_unproven_and_empty_never_becomes_pass(mapped):
    for fallback in ([], ["python", "-m", "pytest", "tests/test_a.py", "-q"]):
        selected = plan(mapped, changed=("unknown.py",), fallback_argv=fallback)
        assert selected["groups"][0]["state"] == "UNPROVEN"
        assert not selected["groups"][0]["execute"]


def test_shared_config_uses_broad_execution_when_all_required_tests_are_collected(mapped):
    (mapped.repo / "pytest.ini").write_text("[pytest]\n")
    selected = plan(mapped, changed=("pytest.ini",))
    assert selected["fallback"] and selected["groups"][0]["state"] == "EXECUTE"
    assert selected["required_acs"] == ["AC-A", "AC-B"]
    assert selected["groups"][0]["binding"]["argv"] == FULL


def test_unchanged_closure_reuses_immutable_success_and_unrelated_edit_is_safe(mapped):
    selected = plan(mapped)
    previous = evidence(mapped, selected["groups"][0])
    (mapped.repo / "feature_b.py").write_text("VALUE = 99\n")
    current = plan(mapped)
    mt.apply_evidence(current, {"a": previous}, mapped.evidence)
    assert current["groups"][0]["state"] == "REUSE"
    assert current["groups"][0]["execute"] is False
    with pytest.raises(FrozenInstanceError):
        previous.payload = "changed"


@pytest.mark.parametrize("kind", ["code", "test", "fixture", "contract", "import", "dependency", "environment", "base", "artifact", "mapping", "argv"])
def test_affected_success_is_invalidated_and_must_execute(mapped, kind):
    previous_plan = plan(mapped)
    previous = evidence(mapped, previous_plan["groups"][0])
    options = {}
    if kind in ("code", "test", "contract"):
        name = {"code": "feature_a.py", "test": "tests/test_a.py", "contract": "contract.json"}[kind]
        path = mapped.repo / name
        path.write_text(path.read_text() + ("\n# changed\n" if kind != "contract" else " "))
    elif kind == "fixture":
        (mapped.repo / "tests/conftest.py").write_text("import pytest\n")
    elif kind == "import":
        (mapped.repo / "feature_a.py").write_text("import feature_b\nVALUE = feature_b.VALUE\n")
    elif kind in ("dependency", "environment"):
        options["env"] = {kind: "changed"}
    elif kind == "artifact":
        (mapped.evidence / "prior.stdout").write_text("tampered")
    elif kind == "mapping":
        group = mapped.groups[0]
        mapped.groups = (mt.TestGroup(group.name, group.tests, (*group.acs, "ADDED-AC"), group.paths, contracts=group.contracts), mapped.groups[1])
    current = plan(mapped, **options)
    if kind == "base":
        current["groups"][0]["binding"]["base_sha"] = "b" * 40
    if kind == "argv":
        current["groups"][0]["binding"]["argv"].append("-v")
    mt.apply_evidence(current, {"a": previous}, mapped.evidence)
    entry = current["groups"][0]
    assert entry["state"] == "INVALIDATED" and entry["execute"]


@pytest.mark.parametrize("exit_code,stdout", [(1, b"1 passed"), (5, b"no tests ran"), (0, b"no tests ran"), (0, b"1 skipped")])
def test_only_observed_exit_zero_with_nonempty_success_can_be_recorded(mapped, exit_code, stdout):
    with pytest.raises(ValueError, match="SUCCESSFUL_NONEMPTY"):
        evidence(mapped, plan(mapped)["groups"][0], exit_code=exit_code, stdout=stdout)


def test_artifact_escape_or_generic_sha_cannot_supply_reuse(mapped):
    mapped.evidence.mkdir()
    (mapped.evidence.parent / "outside.log").write_bytes(b"1 passed")
    with pytest.raises(ValueError, match="OUTSIDE_ATTEMPT"):
        mt.Evidence.from_success(plan(mapped)["groups"][0]["binding"], exit_code=0, stdout=b"1 passed",
                                artifacts={"stdout": "../outside.log"}, evidence_root=mapped.evidence)
    fake = mt.Evidence(mt.canonical({"base_sha": "a" * 40, "test_name": "test_value", "exit_code": 0}), "0" * 64)
    selected = plan(mapped)
    mt.apply_evidence(selected, {"a": fake}, mapped.evidence)
    assert selected["groups"][0]["state"] == "INVALIDATED"


def test_unknown_executed_interpreter_environment_prevents_reuse(mapped):
    selected = plan(mapped)
    selected["groups"][0]["binding"]["environment_proven"] = False
    previous = evidence(mapped, selected["groups"][0])
    mt.apply_evidence(selected, {"a": previous}, mapped.evidence)
    assert selected["groups"][0]["state"] == "INVALIDATED"


def test_fanin_invalidates_sibling_evidence_even_for_identical_files(mapped):
    selected = plan(mapped, registered_only=True)
    previous = evidence(mapped, selected["groups"][0])
    current = plan(mapped, registered_only=True, aggregate_sources=("b" * 40, "c" * 40))
    mt.apply_evidence(current, {"registered-fallback": previous}, mapped.evidence)
    assert current["groups"][0]["state"] == "INVALIDATED"
    assert current["groups"][0]["binding"]["argv"] == FULL
    assert current["groups"][0]["binding"]["aggregate_sources"] == ["b" * 40, "c" * 40]


def test_ef02_only_uses_registered_full_argv_and_missing_registration_is_unproven():
    action = {"verification_profile": "targeted", "depends_on": ["a", "b"]}
    registry = {"targeted": ["python", "-m", "pytest", "tests/test_ef02.py", "-q"], "project-tests": FULL}
    result = approved_verification_plan(action, registry)
    assert result["argv"] == registry["project-tests"] and result["registered_only"]
    assert result["aggregate_requires_fresh_execution"]
    assert approved_verification_plan(action, {"targeted": registry["targeted"]})["state"] == "UNPROVEN"
    out = run_schedule({"batch_id": "x", "source_sha": "a" * 40, "actions": [
        {"action_id": name, "attempt_id": name, "depends_on": []} for name in ("a", "b")]},
        lambda _: {"status": "CANDIDATE_READY"})
    assert out["status"] == "COMPLETE" and out["aggregate_verification"]["state"] == "UNPROVEN"


@pytest.mark.parametrize("requested,argv,worker_count", [
    ("project-tests", ["python", "-m", "pytest", "tests", "-q", "-n", "12"], 12),
    ("project-tests-serial", ["python", "-m", "pytest", "tests", "-q"], 0),
])
def test_explicit_full_suite_keeps_requested_profile(requested, argv, worker_count):
    registry = {
        "project-tests": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
        "project-tests-serial": ["python", "-m", "pytest", "tests", "-q"],
        "ef02-tests": ["python", "-m", "pytest", "tests/test_ef02.py", "-q", "-n", "12"],
    }
    result = approved_verification_plan(
        {"verification_profile": requested, "depends_on": []}, registry)
    assert result["state"] == "EXECUTE"
    assert result["profile"] == requested
    assert result["requested_profile"] == requested
    assert result["argv"] == argv
    assert result["registered_workers"] == worker_count
    assert result["reason"] == "EXPLICIT_REGISTERED_FULL_SUITE"


def test_targeted_requested_profile_falls_back_only_when_required_for_ac_coverage():
    registry = {
        "project-tests": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
        "ef02-tests": ["python", "-m", "pytest", "tests/test_ef02.py", "-q", "-n", "12"],
    }
    result = approved_verification_plan(
        {"verification_profile": "ef02-tests", "depends_on": []}, registry)
    assert result["state"] == "EXECUTE"
    assert result["requested_profile"] == "ef02-tests"
    assert result["profile"] == "project-tests"
    assert result["argv"] == registry["project-tests"]
    assert result["reason"] == "MT01_SELECTOR_REGISTRATION_REQUIRES_COMPANION_PR"
    assert approved_verification_plan(
        {"verification_profile": "ef02-tests", "depends_on": []},
        {"ef02-tests": registry["ef02-tests"]})["state"] == "UNPROVEN"


def test_registered_known_group_selects_exact_minimal_command_and_invalidates_registry_edit(mapped, monkeypatch):
    command = ["python", "-m", "pytest", "tests/test_a.py", "-q", "-n", "12"]
    parallel_full = [*FULL, "-n", "12"]
    registry = {"project-tests": parallel_full, "component-a": command}
    # Pin "python" resolution to the active verified interpreter so host PATH
    # differences (e.g. Windows venv vs base python) do not mask registry REUSE.
    monkeypatch.setattr(mt.shutil, "which", lambda cmd: sys.executable if cmd == "python" else None)
    selected = plan(mapped, registered_only=True, registered_profiles=registry,
                    fallback_argv=parallel_full)
    assert not selected["fallback"] and [g["group"] for g in selected["groups"]] == ["a"]
    assert selected["groups"][0]["binding"]["argv"] == command
    assert selected["groups"][0]["binding"]["registered_profile"] == "component-a"
    assert selected["groups"][0]["binding"]["environment_proven"] is True
    assert selected["groups"][0]["binding"]["executable_sha256"] == mt.digest(Path(sys.executable).read_bytes())
    previous = evidence(mapped, selected["groups"][0])
    current = plan(mapped, registered_only=True, registered_profiles=registry,
                   fallback_argv=parallel_full)
    mt.apply_evidence(current, {"a": previous}, mapped.evidence)
    assert current["groups"][0]["state"] == "REUSE"
    renamed = plan(mapped, registered_only=True,
                   registered_profiles={"other-approved-name": command},
                   fallback_argv=parallel_full)
    mt.apply_evidence(renamed, {"a": previous}, mapped.evidence)
    assert renamed["groups"][0]["state"] == "INVALIDATED"
    # Unproven interpreter resolution must still invalidate reuse even when
    # prior evidence exists, and forged unproven evidence cannot grant REUSE.
    monkeypatch.setattr(mt.shutil, "which", lambda _cmd: None)
    unproven = plan(mapped, registered_only=True, registered_profiles=registry,
                    fallback_argv=parallel_full)
    assert unproven["groups"][0]["binding"]["environment_proven"] is False
    mt.apply_evidence(unproven, {"a": previous}, mapped.evidence)
    assert unproven["groups"][0]["state"] == "INVALIDATED"
    forged = evidence(mapped, unproven["groups"][0], name="forged-unproven")
    mt.apply_evidence(unproven, {"a": forged}, mapped.evidence)
    assert unproven["groups"][0]["state"] == "INVALIDATED"


@pytest.mark.parametrize("has_serial_group", [False, True])
def test_serial_profile_survives_mt01_selection_and_actual_pytest_dispatch(mapped, monkeypatch, has_serial_group):
    # Regression for a registered parallel component group silently overriding
    # the explicitly selected project-tests-serial EF02 action.
    serial_group = ["python", "-m", "pytest", "tests/test_a.py", "-q"]
    parallel_group = [*serial_group, "-n", "12"]
    registry = {
        "project-tests": [*FULL, "-n", "12"],
        "project-tests-serial": FULL,
        "component-a-parallel": parallel_group,
    }
    if has_serial_group:
        registry["component-a-serial"] = serial_group
    action_plan = approved_verification_plan(
        {"verification_profile": "project-tests-serial", "depends_on": []}, registry)
    assert action_plan["profile"] == "project-tests-serial"
    assert action_plan["argv"] == FULL

    calls = []
    def run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess,
        TimeoutExpired=subprocess.TimeoutExpired))

    mapped.evidence.mkdir(exist_ok=True)
    evidence_files = {}
    def save(key, filename, payload):
        relative = "registered-serial/" + filename
        destination = mapped.evidence / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        evidence_files[key] = relative

    result, actual_plan = verification.execute_plan(
        repo=mapped.repo, output_dir=mapped.evidence, base_sha="a" * 40,
        base_files=mapped.base, changed_paths=["feature_a.py"],
        required_acs=("AC-A",), fallback_argv=action_plan["argv"],
        prior={}, save=save, evidence_files=evidence_files,
        deadline=time.monotonic() + 60, timeout_seconds=30,
        registered_only=True, registered_profiles=registry,
        groups=mapped.groups)
    expected = serial_group if has_serial_group else FULL
    assert result.returncode == 0
    assert calls == [expected]  # Actual subprocess.run argv, not merely planned JSON.
    assert actual_plan["groups"][0]["binding"]["argv"] == expected
    assert actual_plan["groups"][0]["actual_argv"] == expected
    assert actual_plan["fallback"] is not has_serial_group


def test_parallel_action_does_not_pick_only_registered_serial_group(mapped):
    parallel_full = [*FULL, "-n", "12"]
    registry = {"project-tests": parallel_full,
                "component-a-serial": ["python", "-m", "pytest", "tests/test_a.py", "-q"]}
    selected = plan(mapped, registered_only=True, registered_profiles=registry,
                    fallback_argv=parallel_full)
    assert selected["fallback"]
    assert selected["groups"][0]["binding"]["argv"] == parallel_full


@pytest.mark.parametrize("suffix", [["-k", "only_one"], [";", "shell"], ["-n", "2"]])
def test_registered_group_must_match_exact_finite_test_scope(mapped, suffix):
    registry = {"bad-a": ["python", "-m", "pytest", "tests/test_a.py", "-q", *suffix]}
    selected = plan(mapped, registered_only=True, registered_profiles=registry)
    assert selected["fallback"] and selected["groups"][0]["binding"]["argv"] == FULL


@pytest.mark.parametrize("code,stdout", [(5, "no tests ran"), (3, "INTERNALERROR"), (0, "no tests ran"), (1, "workers crashed")])
def test_group_failures_no_tests_and_internal_errors_never_pass(mapped, monkeypatch, code, stdout):
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=lambda *_a, **_kw: subprocess.CompletedProcess([], code, stdout, ""),
        CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired))
    result, selected, prior = _execute(mapped)
    assert result.returncode != 0 and not prior
    assert selected["groups"][0]["state"] == selected["groups"][0]["result"] == "UNPROVEN"


def _execute(mapped, *, prior=None, acs=("AC-A",), changed=("feature_a.py",), name="round-0", **options):
    mapped.evidence.mkdir(exist_ok=True)
    files = {}
    def save(key, filename, data):
        relative = name + "/" + filename
        path = mapped.evidence / relative
        path.parent.mkdir(exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
        files[key] = relative
    prior = {} if prior is None else prior
    result, selected = verification.execute_plan(
        repo=mapped.repo, output_dir=mapped.evidence, base_sha="a" * 40, base_files=mapped.base,
        changed_paths=list(changed), required_acs=acs, fallback_argv=options.pop("fallback_argv", FULL), prior=prior,
        # Real nested pytest startup on two twelve-worker actions is not the
        # behavior under test. Timeout tests inject their exception separately.
        save=save, evidence_files=files, deadline=time.monotonic() + 90, timeout_seconds=60,
        groups=mapped.groups, **options)
    return result, selected, prior


def test_group_timeout_preserves_partial_logs_and_cannot_repair(mapped, monkeypatch):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], b"created: 12/12 workers\npartial", b"timeout")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=timeout, TimeoutExpired=subprocess.TimeoutExpired))
    with pytest.raises(subprocess.TimeoutExpired):
        _execute(mapped)
    assert (mapped.evidence / "round-0/mt01.0.stdout.log").read_bytes() == b"created: 12/12 workers\npartial"
    receipt = json.loads((mapped.evidence / "round-0/mt01.results.json").read_text())
    assert receipt["groups"][0]["state"] == "UNPROVEN" and receipt["groups"][0]["workers_actual"] is None
    assert receipt["groups"][0]["workers_console_reported"] == 12
    assert not list(mapped.evidence.rglob("*.evidence.json"))


@pytest.mark.parametrize("expression", ["__import__(name)", "builtins.__import__(name)",
    "importlib.import_module(name)", "load(name)"])
def test_dynamic_import_aliases_never_establish_narrow_coverage(mapped, expression):
    (mapped.repo / "feature_a.py").write_text("from importlib import import_module as load\n" + expression)
    assert plan(mapped)["fallback"]


def test_builtin_import_alias_is_unknown_when_target_is_dynamic(mapped):
    (mapped.repo / "feature_a.py").write_text("from builtins import __import__ as load\nload(name)\n")
    assert plan(mapped)["fallback"]


def test_unregistered_fallback_cannot_establish_registered_only_pass(mapped):
    selected = plan(mapped, changed=("unknown.py",), registered_only=True,
                    registered_profiles={"component-a": ["python", "-m", "pytest", "tests/test_a.py", "-q"]})
    assert selected["groups"][0]["state"] == "UNPROVEN"


def test_new_reverse_consumer_test_outside_broad_collection_root_is_unproven(mapped):
    (mapped.repo / "new_test.py").write_text("import feature_a\n")
    selected = plan(mapped)
    assert selected["fallback"] and selected["unknown_tests"] == ["new_test.py"]
    assert selected["groups"][0]["state"] == "UNPROVEN"


def test_unmapped_python_consumer_outside_src_still_requires_broad_verification(mapped):
    (mapped.repo / "consumer.py").write_text("import feature_a\n")
    selected = plan(mapped)
    assert selected["fallback"] and selected["unknown_consumers"] == ["consumer.py"]


@pytest.mark.parametrize("workers", [8, 12])
def test_parallel_execution_receipt_has_observed_counts_argv_and_timing(mapped, monkeypatch, workers):
    calls = []
    def run(argv, **_):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, f"MT01_XDIST_WORKERS_COLLECTED={workers}\n2 passed, 1 skipped in 0.02s", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired,
                                                                 CompletedProcess=subprocess.CompletedProcess))
    argv = ["python", "-m", "pytest", "tests/test_a.py", "-q", "-n", "12"]
    result, selected, prior = _execute(mapped, registered_only=True, fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "component-a": argv})
    entry = selected["groups"][0]
    assert calls == [argv] and entry["workers_requested"] == 12 and entry["workers_actual"] == workers
    assert entry["worker_count_provenance"] == "XDIST_CONTROLLER_COLLECTION_HOOK"
    assert "ai_commerce.engineering.pytest_profile" in verification.pytest_observation_env()["PYTEST_PLUGINS"]
    if workers == 12:
        assert result.returncode == 0 and set(prior) == {"a"} and selected["narrow_execution_observed"]
    else:
        assert result.returncode != 0 and not prior and not selected["narrow_execution_observed"]
        assert entry["state"] == "UNPROVEN" and entry["reason"] == "PYTEST_WORKER_COUNT_MISMATCH"
    assert entry["actual_argv"] == argv
    assert entry["tests_executed"]["passed"] == 2 and entry["tests_executed"]["skipped"] == 1
    assert entry["started_at"] <= entry["finished_at"] and entry["wall_seconds"] >= 0
    assert json.loads((mapped.evidence / "round-0/mt01.results.json").read_text()) == selected


def test_affected_proof_executes_fresh_and_preserves_immutable_original(mapped, monkeypatch):
    previous = evidence(mapped, plan(mapped)["groups"][0])
    original = previous.payload
    (mapped.repo / "feature_a.py").write_text("VALUE = 2\n")
    calls = []
    def run(argv, **_):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "1 passed in 0.02s", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired,
                                                                 CompletedProcess=subprocess.CompletedProcess))
    _, selected, prior = _execute(mapped, prior={"a": previous})
    assert selected["groups"][0]["state"] == "INVALIDATED" and len(calls) == 1
    assert prior["a"].sha256 != previous.sha256 and previous.payload == original


def test_fanin_performs_fresh_broad_execution_even_with_valid_prior(mapped, monkeypatch):
    first = plan(mapped, aggregate_sources=("b" * 40,))
    previous = evidence(mapped, first["groups"][0])
    calls = []
    def run(argv, **_):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "1 passed", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired,
                                                                 CompletedProcess=subprocess.CompletedProcess))
    _, selected, _ = _execute(mapped, prior={"registered-fallback": previous}, aggregate_sources=("b" * 40,))
    assert selected["groups"][0]["state"] == "INVALIDATED" and calls == [FULL]
    assert not selected["narrow_execution_observed"]


def test_groups_share_remaining_budget_and_preserve_completed_success_on_timeout(mapped, monkeypatch):
    tick = [10.0]
    budgets = []
    def run(argv, **kwargs):
        budgets.append(kwargs["timeout"])
        tick[0] += 4
        if len(budgets) == 2:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], b"partial", b"")
        return subprocess.CompletedProcess(argv, 0, "1 passed", "")
    monkeypatch.setattr(verification, "time", SimpleNamespace(monotonic=lambda: tick[0]))
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    mapped.evidence.mkdir()
    files, prior = {}, {}
    def save(key, name, data):
        (mapped.evidence / name).write_bytes(data)
        files[key] = name
    with pytest.raises(subprocess.TimeoutExpired):
        verification.execute_plan(repo=mapped.repo, output_dir=mapped.evidence, base_sha="a" * 40,
            base_files=mapped.base, changed_paths=["feature_a.py"], required_acs=("AC-A", "AC-B"),
            fallback_argv=FULL, prior=prior, save=save, evidence_files=files,
            deadline=100, timeout_seconds=6, groups=mapped.groups)
    assert budgets == [6, 2] and set(prior) == {"a"}
    result = json.loads((mapped.evidence / "mt01.results.json").read_text())
    assert result["groups"][0]["result"] == "PASS" and result["groups"][1]["state"] == "UNPROVEN"
    assert result["groups"][1]["reason"] == "VERIFY_TIMEOUT"


def test_group_source_drift_discards_pass_and_stops(mapped, monkeypatch):
    def mutate(*_a, **_kw):
        (mapped.repo / "feature_a.py").write_text("drift = True\n")
        return subprocess.CompletedProcess([], 0, "1 passed", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(run=mutate, TimeoutExpired=subprocess.TimeoutExpired))
    with pytest.raises(RuntimeError, match="SOURCE_DRIFT"):
        _execute(mapped)
    assert not list(mapped.evidence.rglob("*.evidence.json"))


def test_real_pytest_group_pass_reused_after_other_group_repair(mapped, monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONPATH", str(mapped.repo))
    result, selected, prior = _execute(mapped, acs=("AC-A", "AC-B"))
    assert result.returncode == 1 and set(prior) == {"a"}
    first_bytes = prior["a"].payload
    (mapped.repo / "feature_b.py").write_text("VALUE = 1\n")
    repaired, selection, prior = _execute(mapped, prior=prior, acs=("AC-A", "AC-B"),
                                         changed=("feature_a.py", "feature_b.py"), name="round-1")
    assert repaired.returncode == 0
    assert [g["state"] for g in selection["groups"]] == ["REUSE", "EXECUTE"]
    assert set(prior) == {"a", "b"} and prior["a"].payload == first_bytes


def test_ef01_repair_preserves_valid_group_pass_without_duplicate_provider(tmp_path, monkeypatch):
    # Reuse the established subprocess-only EF01 fixture, with two explicit ACs.
    from test_ef01_repair import _files, _provider, _snapshot_fake
    from ai_commerce.engineering import runner
    repo = tmp_path / "workspace"
    repo.mkdir()
    (repo / "test_scope.py").write_text("from pathlib import Path\ndef test_value():\n    assert Path('impl.py').read_text() == 'good'\n")
    (repo / "test_kept.py").write_text("from pathlib import Path\ndef test_kept():\n    assert Path('kept.txt').read_text() == 'initial cumulative change'\n")
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    baseline = _files(repo)
    fixture = SimpleNamespace(repo=repo, root=tmp_path, monkeypatch=monkeypatch,
                              state={"scope": ["impl.py", "kept.txt"]})
    monkeypatch.setattr(runner, "_snapshot", lambda r, _p, _d: _snapshot_fake(r, baseline, fixture.state))
    groups = (mt.TestGroup("kept", ("test_kept.py",), ("KEEP",), ("kept.txt",)),
              mt.TestGroup("value", ("test_scope.py",), ("VALUE",), ("impl.py",)))
    monkeypatch.setattr(mt, "approved_groups", lambda: groups)
    selection, calls_file, _ = _provider(fixture)
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="mt-repair", attempt_id="one-claim",
                                    selection=selection, instruction="Make scoped changes", verify_argv=FULL,
                                    allowed_prefixes=fixture.state["scope"], minimum_test=True, acceptance_ids=("KEEP", "VALUE"))
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["invocation_count"] == 2 and result["repair_rounds_used"] == 1
    assert len(calls_file.read_text().splitlines()) == 2
    assert [g["state"] for g in result["minimum_test"]["groups"]] == ["REUSE", "EXECUTE"]
    assert not result["replay_authorized"]
    for invocation in result["invocations"]:
        for key, relative in invocation["evidence_files"].items():
            assert mt.digest((tmp_path / "evidence" / relative).read_bytes()) == invocation["evidence_sha256"][key]


@pytest.mark.parametrize("kind", ["no-tests", "internal", "timeout", "permission"])
def test_mt01_unknown_or_infrastructure_never_replays_provider(tmp_path, monkeypatch, kind):
    from test_ef01_repair import _files, _provider, _snapshot_fake
    from ai_commerce.engineering import runner
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_scope.py").write_text("def test_value():\n    assert True\n")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    baseline = _files(repo)
    fixture = SimpleNamespace(repo=repo, root=tmp_path, monkeypatch=monkeypatch,
                              state={"scope": ["impl.py", "kept.txt"]})
    monkeypatch.setattr(runner, "_snapshot", lambda r, _p, _d: _snapshot_fake(r, baseline, fixture.state))
    monkeypatch.setattr(mt, "approved_groups", lambda: (
        mt.TestGroup("scope", ("test_scope.py",), ("AC",), ("impl.py", "kept.txt")),))
    def run(argv, **kwargs):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], b"partial", b"timeout")
        if kind == "permission":
            raise PermissionError("permission denied")
        return subprocess.CompletedProcess(argv, 5 if kind == "no-tests" else 3, "no tests ran", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess))
    selection, calls, _ = _provider(fixture)
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence", work_id="unknown", attempt_id="single",
                                    selection=selection, instruction="fixture", verify_argv=FULL,
                                    allowed_prefixes=fixture.state["scope"], minimum_test=True, acceptance_ids=("AC",))
    assert result["status"] != "BUILD_VERIFIED_FOR_SCOPE" and result["invocation_count"] == 1
    assert len(calls.read_text().splitlines()) == 1 and not result["replay_authorized"]
    assert result["minimum_test"]["groups"][0]["state"] == "UNPROVEN"
    receipt = json.loads((tmp_path / "evidence" / result["evidence_files"]["mt01_results"]).read_text())
    assert receipt == result["minimum_test"]


def test_ef02_missing_broad_registration_stops_without_provider(tmp_path, monkeypatch):
    from test_ef01_repair import _worker_fixture, _action, VERIFY
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    context.worker.profiles = {"scope": VERIFY}
    result = context.worker.execute_action(_action("a"))
    assert result["status"] == "UNPROVEN" and not context.calls and not context.children and not context.candidates


def test_ef02_fanin_receipt_without_fresh_group_proof_cannot_publish(tmp_path, monkeypatch):
    from test_ef01_repair import _worker_fixture, _action, build_parser, remote
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    child = remote.subprocess.Popen
    class StaleReceipt(child):
        def communicate(self, timeout=None):
            output = super().communicate(timeout)
            args = build_parser().parse_args(self.argv[3:])
            file = args.out_dir / "engineering_result.json"
            value = json.loads(file.read_text())
            value["minimum_test"]["groups"][0]["state"] = "REUSE"
            file.write_text(json.dumps(value))
            return output
    monkeypatch.setattr(remote.subprocess, "Popen", StaleReceipt)
    action = _action("c", ("a", "b"))
    action["resolved_dependencies"] = [{"action_id": name, "candidate_sha": sha * 40, "status": "CANDIDATE_READY"}
                                       for name, sha in (("a", "b"), ("b", "c"))]
    result = context.worker.execute_action(action)
    assert result["status"] == "UNPROVEN" and result["error_code"] == "AGGREGATE_FRESH_COVERAGE_UNCONFIRMED"
    assert not context.candidates and len(context.calls) == 2 and len(context.claims) == len(context.children) == 1


@pytest.mark.parametrize("kind", ["source", "files", "argv", "artifact", "digest", "plan-type", "group-type", "binding-type"])
def test_ef02_fanin_rejects_tampered_aggregate_fingerprint_before_publish(tmp_path, monkeypatch, kind):
    from test_ef01_repair import _worker_fixture, _action, build_parser, remote
    context = _worker_fixture(tmp_path, monkeypatch, real_git=False)
    child = remote.subprocess.Popen
    class TamperedReceipt(child):
        def communicate(self, timeout=None):
            output = super().communicate(timeout)
            args = build_parser().parse_args(self.argv[3:])
            file = args.out_dir / "engineering_result.json"
            value = json.loads(file.read_text())
            group = value["minimum_test"]["groups"][0]
            if kind == "source":
                group["binding"]["base_sha"] = "d" * 40
            elif kind == "files":
                group["binding"]["candidate_files"] = {}
            elif kind == "argv":
                group["binding"]["argv"] = ["python", "-m", "pytest", "no_tests.py"]
            elif kind == "digest":
                group["evidence_sha256"] = "0" * 64
            elif kind == "plan-type":
                value["minimum_test"] = []
            elif kind == "group-type":
                value["minimum_test"]["groups"] = [None]
            elif kind == "binding-type":
                group["binding"] = []
            else:
                artifact = args.out_dir / value["evidence_files"]["mt01_0_evidence"]
                artifact.write_text("{}")
            file.write_text(json.dumps(value))
            return output
    monkeypatch.setattr(remote.subprocess, "Popen", TamperedReceipt)
    action = _action("c", ("a", "b"))
    action["resolved_dependencies"] = [{"action_id": name, "candidate_sha": sha * 40, "status": "CANDIDATE_READY"}
                                       for name, sha in (("a", "b"), ("b", "c"))]
    result = context.worker.execute_action(action)
    assert result["status"] == "UNPROVEN" and result["error_code"] == "AGGREGATE_FRESH_COVERAGE_UNCONFIRMED"
    assert not context.candidates


def test_a13_mapping_and_registry_are_exact_and_do_not_waive_future_coverage(real_tree, monkeypatch):
    current = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    a13 = next(g for g in real_tree.groups if g.name == "a13")
    assert set(a13.tests) == {
        "tests/test_a13_media_inspection.py", "tests/test_a13_claim_rights.py",
        "tests/test_a13_link.py", "tests/test_a13_evidence_pack.py",
    }
    assert current["mt01-a13-tests"] == ["python", "-m", "pytest", *a13.tests, "-q", "-n", "12"]
    assert "A13.affiliate-release" in a13.acs
    selected = real_plan(real_tree, monkeypatch, "tests/test_a13_evidence_pack.py",
                         registry=current, acs=("A13.affiliate-release",))
    assert not selected["fallback"]
    assert selected["selected_groups"] == ["a13"]
    assert selected["groups"][0]["selected_profile"] == "mt01-a13-tests"
    unknown = real_plan(real_tree, monkeypatch, "tests/test_a13_future_unmapped.py",
                        registry=current, acs=("A13.affiliate-release",))
    assert unknown["fallback"]
    assert "PATH_OR_AC_MAP_INCOMPLETE" in unknown["reason_codes"]


# MT-01 PR CI integration: do not silently narrow CI when coverage is uncertain.
@pytest.fixture(scope="module")
def pr_ci_module():
    import importlib.util
    script = ROOT / ".github" / "scripts" / "ef02-pr-ci-tests.py"
    spec = importlib.util.spec_from_file_location("ef02_pr_ci_tests", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pr_ci_plan(tests=("tests/test_ef02_d3_github_artifact_authority.py",),
                *, fallback=False, reasons=(), executable=True):
    return {
        "fallback": fallback, "reason_codes": list(reasons),
        "groups": [{"group": "ef02-wake",
                    "state": "EXECUTE" if executable else "UNPROVEN",
                    "execute": executable,
                    "binding": {"mapping": {"tests": list(tests)}}}],
    }


def test_pr_ci_push_stays_full(tmp_path, pr_ci_module):
    result = pr_ci_module.choose(tmp_path, event="push", base="a" * 40)
    assert result[0] == ("tests",)
    assert result[1] == "FULL_PROJECT_REQUIRED"
    assert result[2] == ("PUSH_OR_NON_PR_EVENT",)


def test_pr_ci_missing_base_stays_full(tmp_path, pr_ci_module):
    result = pr_ci_module.choose(tmp_path, event="pull_request", base="invalid")
    assert result[1] == "FULL_PROJECT_REQUIRED"


def test_pr_ci_d3_changes_select_approved_consumer_tests(tmp_path, pr_ci_module):
    test = "tests/test_ef02_d3_github_artifact_authority.py"
    (tmp_path / "tests").mkdir()
    (tmp_path / test).touch()
    result = pr_ci_module.choose(
        tmp_path, event="pull_request", base="a" * 40,
        paths={"src/ai_commerce/remote/github_artifact_authority.py", test},
        selection_fn=lambda **_: _pr_ci_plan(),
    )
    assert result == ((test,), "SELECTED_AFFECTED_INTEGRATION",
                      ("MT01_PROVEN_CONSUMER_CLOSURE",), ("ef02-wake",))


@pytest.mark.parametrize("path", [
    ".github/workflows/ef02-tests.yml", ".github/scripts/ef02-pr-ci-tests.py",
    "pyproject.toml",
])
def test_pr_ci_workflow_or_config_changes_require_full(tmp_path, pr_ci_module, path):
    result = pr_ci_module.choose(
        tmp_path, event="pull_request", base="a" * 40, paths={path},
        selection_fn=lambda **_: _pr_ci_plan(),
    )
    assert result[0] == ("tests",)
    assert result[2] == ("CI_OR_SHARED_CONFIG_CHANGED",)


@pytest.mark.parametrize("plan", [
    _pr_ci_plan(fallback=True, reasons=("IMPORT_OR_CONSUMER_MAP_INCOMPLETE",)),
    {"fallback": False, "reason_codes": [], "groups": []},
    _pr_ci_plan(executable=False),
])
def test_pr_ci_unproven_mapping_requires_full(tmp_path, pr_ci_module, plan):
    result = pr_ci_module.choose(
        tmp_path, event="pull_request", base="a" * 40, paths={"x.py"},
        selection_fn=lambda **_: plan,
    )
    assert result[1] == "FULL_PROJECT_REQUIRED"


def test_pr_ci_modified_test_must_be_selected(tmp_path, pr_ci_module):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_ef02_d3_github_artifact_authority.py").touch()
    result = pr_ci_module.choose(
        tmp_path, event="pull_request", base="a" * 40,
        paths={"tests/test_ef02_d5_local_wake_entry.py"},
        selection_fn=lambda **_: _pr_ci_plan(),
    )
    assert result[0] == ("tests",)
    assert result[2] == ("MODIFIED_TEST_NOT_SELECTED",)


def test_pr_ci_selection_exception_requires_full(tmp_path, pr_ci_module):
    def broken(**kwargs):
        raise OSError("synthetic selector failure")
    result = pr_ci_module.choose(
        tmp_path, event="pull_request", base="a" * 40, paths={"x.py"},
        selection_fn=broken,
    )
    assert result[0] == ("tests",)
    assert result[2] == ("SELECTOR_ERROR_FAILS_TO_FULL",)


def test_pr_ci_git_rename_requires_full(tmp_path, pr_ci_module):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "CI Test"], check=True)
    (repo / "old.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "old.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    (repo / "old.py").rename(repo / "new.py")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "rename"], check=True)
    paths, renamed = pr_ci_module.changed_paths(repo, base)
    assert paths == {"old.py", "new.py"} and renamed
    result = pr_ci_module.choose(repo, event="pull_request", base=base)
    assert result[2] == ("RENAMED_PATH_REQUIRES_FULL",)


def test_pr_ci_empty_diff_requires_full(tmp_path, pr_ci_module):
    result = pr_ci_module.choose(
        tmp_path, event="pull_request", base="a" * 40, paths=set(),
        selection_fn=lambda **_: _pr_ci_plan(),
    )
    assert result[2] == ("EMPTY_DIFF",)


# Hosted PR CI improvements: bounded parallel workers and reviewed control edits.
def test_pr_ci_additive_registry_requires_exact_reviewed_commands(pr_ci_module):
    from ai_commerce.engineering import minimum_test as mt
    group = next(g for g in mt.approved_groups() if g.name == "ef02-wake")
    before = {"project-tests": ["python", "-m", "pytest", "tests", "-q", "-n", "12"]}
    argv = ["python", "-m", "pytest", *group.tests, "-q", "-n", "12"]
    added = {**before, "mt01-ef02-wake-tests": argv}
    approved = pr_ci_module.additive_registry_tests(before, added, mt.approved_groups())
    assert set(approved) == {*group.tests, *pr_ci_module.REVIEWED_REGISTRY_REGRESSIONS}
    assert pr_ci_module.additive_registry_tests(before, {**before, "mt01-ef02-wake-tests": argv[:-1] + ["4"]}, mt.approved_groups()) is None
    assert pr_ci_module.additive_registry_tests(before, {**before, "unsafe": argv}, mt.approved_groups()) is None
    assert pr_ci_module.additive_registry_tests(before, {"mt01-ef02-wake-tests": argv}, mt.approved_groups()) is None
    assert pr_ci_module.additive_registry_tests(before, before, mt.approved_groups()) is None
    assert pr_ci_module.additive_registry_tests(before, {**added, "project-tests": ["pytest"]}, mt.approved_groups()) is None
    assert pr_ci_module.additive_registry_tests(
        before, {**before, "mt01-wrong-name": argv}, mt.approved_groups()) is None


def test_pr_ci_additive_registry_only_selects_reviewed_tests(pr_ci_module):
    selected = pr_ci_module.choose(
        ROOT, event="pull_request", base="a" * 40,
        paths={pr_ci_module.REGISTRY},
        registry_tests_fn=lambda root, base: ("tests/test_minimum_test.py", "tests/test_ef02_mt01_registry.py"),
    )
    assert selected[0] == ("tests/test_ef02_mt01_registry.py", "tests/test_minimum_test.py")
    assert selected[1] == "SELECTED_AFFECTED_INTEGRATION"
    held = pr_ci_module.choose(
        ROOT, event="pull_request", base="a" * 40,
        paths={pr_ci_module.REGISTRY},
        registry_tests_fn=lambda root, base: None,
    )
    assert held[1] == "FULL_PROJECT_REQUIRED"
    assert held[2] == ("REGISTRY_CHANGE_NOT_PROVEN_ADDITIVE",)


def test_pr_ci_registry_plus_source_includes_all_consumers(pr_ci_module):
    selected = pr_ci_module.choose(
        ROOT, event="pull_request", base="a" * 40,
        paths={pr_ci_module.REGISTRY, "src/ai_commerce/remote/github_artifact_authority.py"},
        selection_fn=lambda **_: _pr_ci_plan(),
        registry_tests_fn=lambda root, base: ("tests/test_minimum_test.py", "tests/test_ef02_mt01_registry.py"),
    )
    assert selected[1] == "SELECTED_AFFECTED_INTEGRATION"
    assert set(selected[0]) == {"tests/test_ef02_d3_github_artifact_authority.py",
                                "tests/test_minimum_test.py", "tests/test_ef02_mt01_registry.py"}


def test_pr_ci_report_changes_run_existing_test_consumer(pr_ci_module):
    selected = pr_ci_module.choose(
        ROOT, event="pull_request", base="a" * 40,
        paths={pr_ci_module.MT01_REPORT},
    )
    assert selected[0] == ("tests/test_minimum_test.py",)
    assert selected[1] == "SELECTED_AFFECTED_INTEGRATION"


@pytest.mark.parametrize("control", [
    ".github/scripts/ef02-watch.cjs",
    ".github/ef02/approved-candidates.v1.json",
])
def test_pr_ci_other_github_controls_still_require_full(pr_ci_module, control):
    selected = pr_ci_module.choose(
        ROOT, event="pull_request", base="a" * 40,
        paths={control}, selection_fn=lambda **_: _pr_ci_plan(),
    )
    assert selected[1] == "FULL_PROJECT_REQUIRED"
    assert selected[2] == ("CI_OR_SHARED_CONFIG_CHANGED",)


@pytest.mark.parametrize("workers", [0, 2, 4])
def test_pr_ci_hosted_worker_command_is_bounded(pr_ci_module, workers):
    argv = pr_ci_module.pytest_command(("tests/test_minimum_test.py",), workers)
    assert argv[:3] == (sys.executable, "-m", "pytest")
    assert argv[3:5] == ("tests/test_minimum_test.py", "-q")
    assert argv[5:] == (("-n", str(workers), "--dist", "loadfile") if workers else ())


@pytest.mark.parametrize("workers", [-1, 1, 12, "auto", True])
def test_pr_ci_unapproved_hosted_worker_count_rejected(pr_ci_module, workers):
    with pytest.raises(ValueError, match="UNAPPROVED_HOSTED_WORKER_COUNT"):
        pr_ci_module.pytest_command(("tests",), workers)


# MT-01 protected profile-extension regression matrix (post #188).
def test_pr_ci_additive_existing_profile_preserves_full_reviewed_group(pr_ci_module):
    from ai_commerce.engineering import minimum_test as mt
    group = next(g for g in mt.approved_groups() if g.name == "ef02-wake")
    assert len(group.tests) > 4
    name = "mt01-ef02-wake-tests"
    # Allow an earlier profile to omit known group tests, but never omit them
    # from the resulting CI selection.
    before = {
        "project-tests": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
        name: ["python", "-m", "pytest", *group.tests[:3],
               *group.tests[5:], "-q", "-n", "12"],
    }
    after = {
        **before,
        name: ["python", "-m", "pytest", *group.tests, "-q", "-n", "12"],
    }
    selected = pr_ci_module.additive_registry_tests(before, after, mt.approved_groups())
    assert set(selected) == {*group.tests, *pr_ci_module.REVIEWED_REGISTRY_REGRESSIONS}
    assert len(selected) == len(set(selected))


@pytest.mark.parametrize("mutation", [
    "remove-profile", "remove-test", "reorder", "replace", "unknown-test",
    "worker", "pytest-flags", "duplicate-old-test", "unknown-profile",
    "existing-profile-no-delta",
])
def test_pr_ci_existing_profile_refuses_unsafe_change(pr_ci_module, mutation):
    from ai_commerce.engineering import minimum_test as mt
    group = next(g for g in mt.approved_groups() if g.name == "ef02-wake")
    name = "mt01-ef02-wake-tests"
    before_tests = list(group.tests[:-1])
    before_cmd = ["python", "-m", "pytest", *before_tests, "-q", "-n", "12"]
    after_cmd = ["python", "-m", "pytest", *group.tests, "-q", "-n", "12"]
    before = {"project-tests": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
              name: before_cmd}
    after = {**before, name: after_cmd}
    if mutation == "remove-profile":
        after.pop(name)
    elif mutation == "remove-test":
        after[name] = before_cmd[:-4] + before_cmd[-3:]  # trim an old test
    elif mutation == "reorder":
        after[name] = ["python", "-m", "pytest", *group.tests[1:2],
                       *group.tests[:1], *group.tests[2:], "-q", "-n", "12"]
    elif mutation == "replace":
        after[name] = ["python", "-m", "pytest", "tests/test_unreviewed.py",
                       *group.tests[1:], "-q", "-n", "12"]
    elif mutation == "unknown-test":
        after[name] = ["python", "-m", "pytest", *group.tests,
                       "tests/test_ef02_future_unapproved.py", "-q", "-n", "12"]
    elif mutation == "worker":
        after[name] = after_cmd[:-1] + ["4"]
    elif mutation == "pytest-flags":
        after[name] = after_cmd[:-3] + ["-x", "-n", "12"]
    elif mutation == "duplicate-old-test":
        before[name] = before_cmd[:4] + [before_tests[0]] + before_cmd[4:]
    elif mutation == "unknown-profile":
        after["unreviewed-profile"] = ["python", "-m", "pytest", "tests", "-q"]
    elif mutation == "existing-profile-no-delta":
        after = before.copy()
    assert pr_ci_module.additive_registry_tests(before, after, mt.approved_groups()) is None


def test_pr_ci_existing_profile_only_selects_proven_consumer_set(pr_ci_module):
    from ai_commerce.engineering import minimum_test as mt
    group = next(g for g in mt.approved_groups() if g.name == "ef02-wake")
    selected = pr_ci_module.choose(
        ROOT, event="pull_request", base="a" * 40,
        paths={pr_ci_module.REGISTRY, pr_ci_module.MT01_REPORT},
        registry_tests_fn=lambda *_: tuple(group.tests) + pr_ci_module.REVIEWED_REGISTRY_REGRESSIONS,
    )
    assert selected[1] == "SELECTED_AFFECTED_INTEGRATION"
    assert set(selected[0]) == {*group.tests, *pr_ci_module.REVIEWED_REGISTRY_REGRESSIONS}


@pytest.mark.parametrize("paths", [
    {"src/ai_commerce/engineering/minimum_test.py"},
    {"src/ai_commerce/engineering/minimum_test.py", ".github/ef02/verify-profiles.json"},
    {"src/ai_commerce/engineering/minimum_test.py", "tests/test_ef02_wake_d1.py"},
])
def test_pr_ci_mt01_engine_changes_require_full_even_with_registry_extension(
        pr_ci_module, paths):
    result = pr_ci_module.choose(
        ROOT, event="pull_request", base="a" * 40, paths=paths,
        registry_tests_fn=lambda *_: ("tests/test_minimum_test.py",),
        selection_fn=lambda **_: _pr_ci_plan(),
    )
    assert result[0] == ("tests",)
    assert result[1] == "FULL_PROJECT_REQUIRED"
    assert result[2] == ("MT01_ENGINE_CHANGED",)


def test_mt01_new_component_test_is_pending_not_a_full_suite_failure(monkeypatch, tmp_path):
    """Reproduce A14: EF01 creates tests/test_a14_attribution.py after HEAD."""
    original = "tests/test_known.py"
    new = "tests/test_a14_attribution.py"
    calls = []

    def git_ls_tree(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, (original + "\0").encode(), b"")

    monkeypatch.setattr(mt.subprocess, "run", git_ls_tree)
    groups = (mt.TestGroup("known", (original,), ("KNOWN.ac",), ("known.py",)),)
    files = {original: "old", new: "new", "known.py": "old"}
    report = mt.reviewed_test_registration(tmp_path, files, groups)
    assert report == {
        "unregistered_committed": [], "pending_new_tests": [new],
        "unsafe_new_tests": [],
    }
    assert calls[0][0] == ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"]
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["capture_output"] is True


def test_mt01_newly_committed_unmapped_test_still_requires_registration(monkeypatch, tmp_path):
    """PR checkout must not silently accept source-committed test families."""
    old = "tests/test_known.py"
    unregistered = "tests/test_a14_attribution.py"

    def git_ls_tree(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, (old + "\0" + unregistered + "\0").encode(), b""
        )

    monkeypatch.setattr(mt.subprocess, "run", git_ls_tree)
    report = mt.reviewed_test_registration(
        tmp_path, {old: "1", unregistered: "2"},
        (mt.TestGroup("known", (old,), ("KNOWN.ac",), ("known.py",)),),
    )
    assert report["unregistered_committed"] == [unregistered]
    assert report["pending_new_tests"] == []


def test_mt01_new_symlinked_test_is_never_accepted_as_pending(monkeypatch, tmp_path):
    def git_ls_tree(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(mt.subprocess, "run", git_ls_tree)
    report = mt.reviewed_test_registration(
        tmp_path, {"tests/test_escape.py": "SYMLINK:../../secrets"}, (),
    )
    assert report["unsafe_new_tests"] == ["tests/test_escape.py"]


def test_mt01_baseline_git_failure_fails_closed(monkeypatch, tmp_path):
    def git_ls_tree(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 128, b"", b"fatal: not a git repository")

    monkeypatch.setattr(mt.subprocess, "run", git_ls_tree)
    with pytest.raises(ValueError, match="MT01_COMMITTED_TREE_UNAVAILABLE"):
        mt.reviewed_test_registration(tmp_path, {}, ())


def test_mt01_new_a14_file_retains_registered_full_fallback(mapped):
    """Bootstrap permission does not confer a narrow test-selection shortcut."""
    candidate = mapped.repo / "tests/test_a14_attribution.py"
    candidate.write_text("def test_new_a14():\n    assert True\n")
    selected = plan(mapped, changed=("tests/test_a14_attribution.py",),
                    registered_only=True,
                    registered_profiles={"project-tests": [*FULL, "-n", "12"]},
                    fallback_argv=[*FULL, "-n", "12"])
    assert selected["fallback"]
    assert "tests/test_a14_attribution.py" in selected["unknown_tests"]
    assert selected["groups"][0]["binding"]["argv"] == [*FULL, "-n", "12"]


def test_provisional_new_component_selects_new_test_and_real_reverse_consumer(mapped):
    """L04 onboarding can triage uncommitted A14 tests before registered full."""
    new_source = "src/ai_commerce/affiliate_attribution/evidence.py"
    new_test = "tests/test_a14_attribution.py"
    (mapped.repo / "src/ai_commerce/affiliate_attribution").mkdir(parents=True)
    (mapped.repo / new_source).write_text("ANSWER = 42\n")
    (mapped.repo / new_test).write_text(
        "from ai_commerce.affiliate_attribution import evidence\n"
        "def test_a14():\n    assert evidence.ANSWER == 42\n")
    changed = [new_source, new_test]
    selection = plan(mapped, changed=changed, registered_only=True,
                     registered_profiles={"project-tests": [*FULL, "-n", "12"]},
                     fallback_argv=[*FULL, "-n", "12"])
    result = mt.select_provisional_tests(
        base_files=mapped.base, candidate_files=mt.file_hashes(mapped.repo),
        changed_paths=changed, plan=selection, fallback_argv=[*FULL, "-n", "12"])
    assert selection["fallback"] is True and result is not None
    assert result["new_tests"] == [new_test]
    assert result["argv"] == ["python", "-m", "pytest", new_test, "-q", "-n", "12"]
    assert result["authoritative"] is False
    assert result["registered_final_argv"] == [*FULL, "-n", "12"]


@pytest.mark.parametrize("changed,why", [
    (["src/ai_commerce/affiliate_attribution/evidence.py", "pyproject.toml"], "shared config"),
    (["src/ai_commerce/affiliate_attribution/evidence.py", "tests/test_a.py"], "old registered test"),
    (["src/ai_commerce/affiliate_attribution/evidence.py", "tests/test_a14_attribution.py",
      "src/ai_commerce/shared.py"], "other changed package"),
])
def test_provisional_refuses_unsafe_or_shared_scope(mapped, changed, why):
    new_source = "src/ai_commerce/affiliate_attribution/evidence.py"
    test_path = "tests/test_a14_attribution.py"
    (mapped.repo / "src/ai_commerce/affiliate_attribution").mkdir(parents=True)
    (mapped.repo / new_source).write_text("ANSWER = 42\n")
    (mapped.repo / test_path).write_text("def test_new():\n    assert True\n")
    files = mt.file_hashes(mapped.repo)
    selected = {"fallback": True, "aggregate_sources": [], "unknown_tests": [test_path],
                "affected_paths": [test_path], "unsupported_paths": []}
    assert mt.select_provisional_tests(
        base_files=mapped.base, candidate_files=files, changed_paths=changed,
        plan=selected, fallback_argv=[*FULL, "-n", "12"]) is None, why


def test_provisional_refuses_fanin_symlink_unregistered_profile_and_no_new_tests(mapped):
    files = mt.file_hashes(mapped.repo)
    files["tests/test_a14_attribution.py"] = "SYMLINK:../../outside"
    files["src/ai_commerce/affiliate_attribution/evidence.py"] = "sha"
    change = ["tests/test_a14_attribution.py", "src/ai_commerce/affiliate_attribution/evidence.py"]
    basic = {"fallback": True, "aggregate_sources": [], "unknown_tests": [change[0]],
             "affected_paths": [change[0]], "unsupported_paths": []}
    def selected(plan=basic, argv=None):
        return mt.select_provisional_tests(
            base_files=mapped.base, candidate_files=files, changed_paths=change,
            plan=plan, fallback_argv=argv or [*FULL, "-n", "12"])
    assert selected() is None
    files[change[0]] = "sha"
    assert selected({**basic, "aggregate_sources": ["b" * 40]}) is None
    assert selected(argv=["python", "-m", "pytest", "tests/test_a.py", "-q", "-n", "12"]) is None
    assert selected({**basic, "unknown_tests": []}) is None


def test_provisional_failure_skips_expensive_full_suite_without_claiming_pass(mapped, monkeypatch):
    new_source = "src/ai_commerce/affiliate_attribution/future_adapter.py"
    new_test = "tests/test_a14_provisional_future.py"
    (mapped.repo / "src/ai_commerce/affiliate_attribution").mkdir(parents=True)
    (mapped.repo / new_source).write_text("ANSWER = 42\n")
    (mapped.repo / new_test).write_text(
        "from ai_commerce.affiliate_attribution import future_adapter\n"
        "def test_bad():\n    assert future_adapter.ANSWER == 0\n")
    # The stdout is from a real-shaped pytest failure, not a fabricated PASS.
    calls = []
    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "1 failed in 0.02s\nFAILED tests/test_a14_provisional_future.py::test_bad", "")
    monkeypatch.setattr(verification.subprocess, "run", run)
    mapped.evidence.mkdir(exist_ok=True)
    files = {}
    def save(key, filename, data):
        relative = "preflight/" + filename
        path = mapped.evidence / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[key] = relative
    outcome, plan_data = verification.execute_plan(
        repo=mapped.repo, output_dir=mapped.evidence, base_sha="a"*40,
        base_files=mapped.base, changed_paths=[new_source, new_test],
        required_acs=(), fallback_argv=[*FULL, "-n", "12"], prior={},
        save=save, evidence_files=files, deadline=time.monotonic()+60,
        timeout_seconds=45, registered_only=True,
        registered_profiles={"project-tests": [*FULL, "-n", "12"]})
    assert outcome.returncode == 1
    assert len(calls) == 1 and calls[0][3] == new_test
    assert plan_data["provisional_mapping"]["result"] == "FAIL"
    assert plan_data["groups"][0]["result"] == "UNPROVEN"
    assert "mt01_provisional_stdout" in files


def test_provisional_pass_still_runs_registered_full_suite(mapped, monkeypatch):
    new_source = "src/ai_commerce/affiliate_attribution/future_adapter.py"
    new_test = "tests/test_a14_provisional_future.py"
    (mapped.repo / "src/ai_commerce/affiliate_attribution").mkdir(parents=True)
    (mapped.repo / new_source).write_text("ANSWER = 42\n")
    (mapped.repo / new_test).write_text(
        "from ai_commerce.affiliate_attribution import future_adapter\n"
        "def test_ok():\n    assert future_adapter.ANSWER == 42\n")
    calls = []
    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "created: 12/12 workers\nMT01_XDIST_WORKERS_COLLECTED=12\n1 passed in 0.02s", "")
    monkeypatch.setattr(verification.subprocess, "run", run)
    mapped.evidence.mkdir(exist_ok=True)
    files = {}
    def save(key, filename, data):
        relative = "preflight/" + filename
        path = mapped.evidence / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[key] = relative
    outcome, plan_data = verification.execute_plan(
        repo=mapped.repo, output_dir=mapped.evidence, base_sha="a"*40,
        base_files=mapped.base, changed_paths=[new_source, new_test],
        required_acs=(), fallback_argv=[*FULL, "-n", "12"], prior={},
        save=save, evidence_files=files, deadline=time.monotonic()+60,
        timeout_seconds=45, registered_only=True,
        registered_profiles={"project-tests": [*FULL, "-n", "12"]})
    assert outcome.returncode == 0 and len(calls) == 2
    assert calls[0][3] == new_test and calls[1] == [*FULL, "-n", "12"]
    assert plan_data["provisional_mapping"]["result"] == "PASS_PREFLIGHT_ONLY"
    assert plan_data["groups"][0]["result"] == "PASS"
    assert plan_data["groups"][0]["binding"]["argv"] == [*FULL, "-n", "12"]


# Issue #209: CI must reuse exact-tree, Windows trustworthy PR evidence
# without confusing a successful GitHub wrapper for tests actually executed.
def _mt01_verified_pr_proof(pr_ci_module, *, changed_tree=False, windows_job_ok=True,
                            steps_complete=True, exact_head=True, event="push",
                            branch="refs/heads/main", token="test-only-token"):
    parent = "b" * 40
    merge = "c" * 40
    good_step = [{"name": "MT-01 PR CI selective tests (full fallback)",
                  "conclusion": "success"},
                 {"name": "Run node --test tests/ef02-watch.test.cjs tests/ef02-reconcile.test.cjs",
                  "conclusion": "success"}]
    def git(*argv):
        if argv == ("rev-parse", "HEAD"):
            return merge
        if argv == ("rev-list", "--parents", "-n", "1", merge):
            return f"{merge} {'a' * 40} {parent}"
        if argv == ("rev-parse", merge + "^{tree}"):
            return "1" * 40
        if argv == ("rev-parse", parent + "^{tree}"):
            return ("2" if changed_tree else "1") * 40
        raise AssertionError(f"unexpected git call {argv}")
    def api(url):
        if "/jobs?" in url:
            return {"jobs": [
                {"name": "tests (windows-latest)", "status": "completed",
                 "conclusion": "success" if windows_job_ok else "failure",
                 "steps": good_step if steps_complete else good_step[:1]},
            ]}
        return {"workflow_runs": [{
            "id": 111, "event": "pull_request", "head_sha": parent if exact_head else "d" * 40,
            "status": "completed", "conclusion": "success",
            "path": ".github/workflows/ef02-tests.yml@refs/pull/12/merge"
        }]}
    return pr_ci_module.postmerge_reuse(
        repo=ROOT, event=event, branch=branch, current_sha=merge,
        repository="trung-via/ai-commerce", token=token, git_fn=git, api_fn=api)


def test_pr_ci_postmerge_proof_skips_only_true_identical_windows_run(pr_ci_module):
    allowed, reason, run = _mt01_verified_pr_proof(pr_ci_module)
    assert allowed is True
    assert reason == "EXACT_TREE_AND_WINDOWS_PR_CI_VERIFIED"
    assert run == "111"


@pytest.mark.parametrize("kwargs,reason", [
    ({"changed_tree": True}, "MERGE_TREE_DIFFERS_FROM_TESTED_PR"),
    ({"windows_job_ok": False}, "NO_WINDOWS_SUCCESSFUL_EXACT_HEAD_PR_RUN"),
    ({"steps_complete": False}, "NO_WINDOWS_SUCCESSFUL_EXACT_HEAD_PR_RUN"),
    ({"exact_head": False}, "NO_WINDOWS_SUCCESSFUL_EXACT_HEAD_PR_RUN"),
    ({"event": "pull_request"}, "NON_MAIN_PUSH_OR_PR"),
    ({"branch": "refs/heads/feature"}, "NON_MAIN_PUSH_OR_PR"),
    ({"token": ""}, "MISSING_GITHUB_ACTION_TOKEN"),
])
def test_pr_ci_rejects_unproven_postmerge_reuse(pr_ci_module, kwargs, reason):
    decision = _mt01_verified_pr_proof(pr_ci_module, **kwargs)
    assert decision[0] is False
    assert decision[1] == reason


def test_pr_ci_postmerge_denies_untrusted_provider_error(pr_ci_module):
    def bad_api(_):
        raise OSError("synthetic missing GitHub evidence")
    current = "c" * 40
    result = pr_ci_module.postmerge_reuse(
        repo=ROOT, event="push", branch="refs/heads/main", current_sha=current,
        repository="trung-via/ai-commerce", token="test-only-token",
        git_fn=lambda *argv: current if argv == ("rev-parse", "HEAD")
                else f"{current} {'a' * 40} {'b' * 40}" if argv[0] == "rev-list"
                else "1" * 40,
        api_fn=bad_api)
    assert result[0] is False
    assert result[1] == "PROOF_UNAVAILABLE_OSError"


def test_mt01_project_model_policy_is_reviewed_ef01_consumer():
    from ai_commerce.engineering.minimum_test import approved_groups
    group = next(g for g in approved_groups() if g.name == "ef01")
    assert "src/ai_commerce/engineering/model_defaults.py" in group.paths
    assert "config/engineering/model-defaults.v1.json" in group.contracts
    assert "tests/test_ef01.py" in group.tests


def test_pr_ci_real_a12_component_can_select_narrow_test_files(pr_ci_module):
    import subprocess
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result = pr_ci_module.choose(
        ROOT, event="pull_request", base=base,
        paths={"src/ai_commerce/product/identity.py"},
    )
    assert result[1] == "SELECTED_AFFECTED_INTEGRATION", result
    assert "tests/test_a12_identity.py" in result[0]
    assert "tests" not in result[0]



def test_v21_authenticated_full_runs_entire_registered_scope_not_affected_subset(monkeypatch, real_tree):
    from ai_commerce.engineering.minimum_test import select_plan
    groups = real_tree.groups
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    files = real_tree.files
    broad = real_tree.registry["project-tests"]
    registry = real_tree.registry
    kwargs = dict(repo=ROOT, base_sha=real_tree.sha, base_files=files,
                  candidate_files=files, changed_paths=["src/ai_commerce/product/identity.py"],
                  required_acs=(), fallback_argv=broad, groups=groups,
                  registered_only=True, registered_profiles=registry)
    scoped = select_plan(**kwargs)
    assert scoped["version"] == 2 and not scoped["fallback"]
    assert "a12" in scoped["selected_groups"]
    full = select_plan(**kwargs, human_full_suite_authorized=True)
    assert full["version"] == 1 and full["fallback"]
    assert full["human_full_suite_authorized"] is True
    assert full["groups"][0]["state"] == "EXECUTE"
    assert full["groups"][0]["binding"]["argv"] == broad
    assert "tests/test_minimum_test.py" in full["groups"][0]["binding"]["candidate_files"]
    assert "AUTHENTICATED_HUMAN_EXPLICIT_FULL_SUITE" in full["reason_codes"]
    with pytest.raises(ValueError, match="UNTRUSTED_OR_UNREGISTERED"):
        select_plan(**{**kwargs, "fallback_argv": registry["mt01-a12-tests"]},
                    human_full_suite_authorized=True)


@pytest.mark.parametrize("owner", ["minimum_test.py", "verification.py"])
def test_human_full_preserves_owner_ac_mapping_for_minimum_test_source(monkeypatch, real_tree, owner):
    """An owner source ending in _test.py must not be mistaken for a pytest file."""
    from ai_commerce.engineering.minimum_test import select_plan

    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    files = real_tree.files
    registry = real_tree.registry
    planned = select_plan(
        repo=ROOT, base_sha=real_tree.sha, base_files=files, candidate_files=files,
        changed_paths=["src/ai_commerce/engineering/" + owner],
        required_acs=("MT01.selection-and-evidence",),
        fallback_argv=registry["project-tests"], groups=real_tree.groups,
        registered_only=True, registered_profiles=registry,
        human_full_suite_authorized=True,
    )
    assert planned["fallback"] is True
    assert "mt01" in planned["selected_groups"]
    assert "MT01.selection-and-evidence" in planned["required_acs"]
    assert "src/ai_commerce/engineering/" + owner not in planned["unknown_tests"]
    assert planned["groups"][0]["state"] == "EXECUTE"
    assert planned["groups"][0]["binding"]["argv"] == registry["project-tests"]
    assert "tests/test_minimum_test.py" in planned["groups"][0]["binding"]["candidate_files"]


@pytest.mark.parametrize("owner", ["minimum_test.py", "verification.py"])
def test_shadow_owner_change_preserves_auto_selection_and_registered_argv(monkeypatch, real_tree, owner):
    planned = real_plan(real_tree, monkeypatch, "src/ai_commerce/engineering/" + owner,
                        acs=("MT01.selection-and-evidence",))
    assert "mt01" in planned["selected_groups"]
    assert "MT01.selection-and-evidence" in planned["required_acs"]
    assert planned["narrow_execution_observed"] is False
    assert "tests/test_minimum_test.py" in planned["consumer_map"]["mt01"]["tests"]
    for entry in planned["groups"]:
        assert entry["binding"]["argv"] == real_tree.registry[entry["selected_profile"]]
        assert entry["binding"]["argv"][-2:] == ["-n", "12"]


def test_human_full_cannot_auto_approve_unmapped_acceptance(monkeypatch, real_tree):
    """Full pytest tests/ does not prove an AC without a reviewed owner."""
    from ai_commerce.engineering.minimum_test import select_plan

    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    files = real_tree.files
    registry = real_tree.registry
    planned = select_plan(
        repo=ROOT, base_sha=real_tree.sha, base_files=files, candidate_files=files,
        changed_paths=["src/ai_commerce/engineering/minimum_test.py"],
        required_acs=("MT01.NOT_REGISTERED_R7",),
        fallback_argv=registry["project-tests"], groups=real_tree.groups,
        registered_only=True, registered_profiles=registry,
        human_full_suite_authorized=True,
    )
    assert "HUMAN_FULL_AC_MAPPING_UNPROVEN" in planned["reason_codes"]
    assert planned["groups"][0]["state"] == "UNPROVEN"
    assert planned["groups"][0]["execute"] is False


def test_human_full_loads_existing_trusted_personal_hostops_ac(monkeypatch, real_tree):
    """An existing V2.2 baseline owner is valid in the HUMAN_FULL route."""
    from ai_commerce.engineering.minimum_test import select_plan

    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    ac = "HOSTOPS.PERSONAL.chat-approved-public-read-owner-comment-new-scratch-write"
    assert "hostops-personal" in real_tree.persisted_names
    plan = select_plan(
        repo=ROOT, base_sha=real_tree.sha, base_files=real_tree.files,
        candidate_files=real_tree.files,
        changed_paths=["src/ai_commerce/hostops_v3/personal.py"],
        required_acs=(ac,), fallback_argv=real_tree.registry["project-tests"],
        groups=mt.approved_groups(), registered_only=True,
        registered_profiles=real_tree.registry, human_full_suite_authorized=True,
    )
    assert "hostops-personal" in plan["selected_groups"]
    assert ac in plan["required_acs"]
    assert ac not in plan["unknown_acs"]
    assert "HUMAN_FULL_AC_MAPPING_UNPROVEN" not in plan["reason_codes"]
    assert plan["groups"][0]["state"] == "EXECUTE"
    assert plan["groups"][0]["binding"]["argv"] == real_tree.registry["project-tests"]
    assert "tests/test_hostops_personal.py" in plan["groups"][0]["binding"]["candidate_files"]


def test_human_full_rejects_conflicting_caller_owner_mapping(monkeypatch, real_tree):
    """Mutable caller metadata cannot impersonate an immutable baseline owner."""
    from ai_commerce.engineering.minimum_test import select_plan, TestGroup

    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    trusted = next(g for g in real_tree.groups if g.name == "hostops-personal")
    forged = TestGroup(trusted.name, trusted.tests, ("HOSTOPS.PERSONAL.FORGED",),
                       trusted.paths, trusted.consumers, trusted.contracts)
    groups = (*mt.approved_groups(), forged)
    with pytest.raises(ValueError, match="MT01_HUMAN_FULL_BASELINE_OWNER_CONFLICT"):
        select_plan(
            repo=ROOT, base_sha=real_tree.sha, base_files=real_tree.files,
            candidate_files=real_tree.files,
            changed_paths=["src/ai_commerce/hostops_v3/personal.py"],
            required_acs=("HOSTOPS.PERSONAL.FORGED",),
            fallback_argv=real_tree.registry["project-tests"], groups=groups,
            registered_only=True, registered_profiles=real_tree.registry,
            human_full_suite_authorized=True,
        )


def test_hostops_v3_existing_28_and_v4_plan_files_are_registered(real_tree):
    """HostOps v3 bootstrap is reviewed, not an unknown-component full-suite bypass."""
    group = next(g for g in real_tree.groups if g.name == "hostops-v3")
    expected_sources = {
        "src/ai_commerce/hostops_v3/" + name + ".py"
        for name in ("__init__", "contract", "auth", "filesystem", "journal",
                     "worker", "security", "broker", "transport", "cli")
    }
    expected_scripts = {
        "scripts/hostops_v3.py", "scripts/hostops_v3_release.py",
        "scripts/hostops_v3_schemas.py", "scripts/hostops_v3_verify.py",
        "scripts/HostOps-V3-Launch.py", "scripts/HostOps-V3-TokenInventory.ps1",
        "scripts/HostOps-V4-InstallPlan.ps1",
    }
    expected_contracts = {
        "contracts/hostops-v3.request.schema.json",
        "contracts/hostops-v3.lease.schema.json",
        "contracts/hostops-v3.envelope.schema.json",
        "contracts/hostops-v3.receipt.schema.json",
        "config/hostops-v3/config.example.json",
        "config/hostops-v3/config-v4.example.json",
        "config/hostops-v3/revocations.example.json",
        "docs/engineering/HOSTOPS_V3_DESIGN.md",
        "docs/engineering/HOSTOPS_V3_ENGINEERING_REPORT.md",
        "docs/engineering/hostops_v3_evidence/scenarios.json",
    }
    expected_tests = {
        "tests/test_hostops_v3.py",
        "tests/test_hostops_v3_integration.py",
        "tests/test_hostops_v3_repair.py",
    }
    assert set(group.paths) == expected_sources
    assert set(group.contracts) == expected_contracts | expected_scripts
    assert set(group.tests) == expected_tests
    assert len(expected_sources | expected_scripts | expected_contracts | expected_tests) == 30
    assert set(group.acs) == {"HOSTOPS.v3.security-contract", "HOSTOPS.v3.issue225-matrix"}
    registry = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    assert registry["mt01-hostops-v3-tests"] == [
        "python", "-m", "pytest", *group.tests, "-q", "-n", "12",
    ]


def test_hostops_v3_script_and_schema_change_select_registered_group(real_tree, monkeypatch):
    """Unrelated HostOps V3/contract changes retain their existing narrow tests."""
    for path in ("scripts/HostOps-V3-TokenInventory.ps1",
                 "contracts/hostops-v3.request.schema.json"):
        selected = real_plan(real_tree, monkeypatch, path)
        assert not selected["fallback"]
        owners = set(selected["selected_groups"])
        assert "hostops-v3" in owners
        assert owners - {"hostops-v3"} <= real_tree.persisted_names
        assert any(entry["selected_profile"] == "mt01-hostops-v3-tests"
                   for entry in selected["groups"])
        for entry in selected["groups"]:
            profile = "mt01-" + entry["group"] + "-tests"
            assert entry["selected_profile"] == profile
            assert entry["binding"]["argv"] == real_tree.registry[profile]


@pytest.mark.parametrize("changed", [
    "scripts/HostOps-V4-InstallPlan.ps1",
    "config/hostops-v3/config-v4.example.json",
])
def test_hostops_v4_plan_requires_both_hostops_and_mt01_owner_tests(real_tree, monkeypatch, changed):
    """#288: protected-main merge-gate control closure for both PLAN-only inputs."""
    selected = real_plan(real_tree, monkeypatch, changed)
    assert not selected["fallback"]
    required = {"hostops-v3", "mt01"}
    actual = set(selected["selected_groups"])
    assert required <= actual
    assert actual - required <= real_tree.persisted_names
    bindings = {entry["group"]: entry for entry in selected["groups"]}
    for group in actual:
        expected = real_tree.registry["mt01-" + group + "-tests"]
        assert bindings[group]["selected_profile"] == "mt01-" + group + "-tests"
        assert bindings[group]["binding"]["argv"] == expected
    selected_tests = {test for entry in bindings.values()
                      for test in entry["binding"]["mapping"]["tests"]}
    assert {"tests/test_hostops_v3_integration.py", "tests/test_minimum_test.py"} <= selected_tests
    assert changed in next(g for g in real_tree.groups if g.name == "mt01").consumers


def test_xdist_receipt_must_be_unique_and_controller_grounded():
    parse = verification.observed_xdist_workers
    assert parse(b"12 workers [100 items]\ncreated: 12/12 workers") is None
    assert parse(b"MT01_XDIST_WORKERS_COLLECTED=12\n") == 12
    assert parse(b"MT01_XDIST_WORKERS_COLLECTED=0\r\n") == 0
    assert parse(b"MT01_XDIST_WORKERS_COLLECTED=12\nMT01_XDIST_WORKERS_COLLECTED=8\n") is None
    assert parse(b"prefix MT01_XDIST_WORKERS_COLLECTED=12") is None
    assert parse(b"MT01_XDIST_WORKERS_COLLECTED=invalid") is None


def test_worker_observation_env_preserves_other_plugins_without_duplicates(monkeypatch):
    plugin = "ai_commerce.engineering.pytest_profile"
    monkeypatch.setenv("PYTEST_PLUGINS", "existing.module," + plugin)
    observed = verification.pytest_observation_env()
    assert observed["PYTEST_PLUGINS"].split(",") == ["existing.module", plugin]


def test_registered_parallel_success_without_receipt_is_unproven(mapped, monkeypatch):
    argv = ["python", "-m", "pytest", "tests/test_a.py", "-q", "-n", "12"]
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "12 workers [3 items]\n2 passed in 0.01s", "")
    monkeypatch.setattr(verification.subprocess, "run", run)
    outcome, selected, prior = _execute(
        mapped, registered_only=True, fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "component-a": argv})
    entry = selected["groups"][0]
    assert outcome.returncode != 0 and not prior
    assert entry["workers_actual"] is None
    assert entry["workers_console_reported"] == 12
    assert entry["worker_count_provenance"] == "UNOBSERVED"
    assert entry["result"] == "UNPROVEN"
    assert entry["reason"] == "PYTEST_WORKER_COUNT_MISMATCH"


def test_xdist_controller_observer_counts_distinct_gateway_collections():
    from types import SimpleNamespace as NS
    from ai_commerce.engineering import pytest_profile as observer
    observed = []
    try:
        observer._COLLECTED_GATEWAYS.clear()
        for worker_id in ("gw0", "gw1", "gw0"):
            observer.pytest_xdist_node_collection_finished(
                NS(gateway=NS(id=worker_id)), ["test_one.py::test_ok"])
        observer.pytest_terminal_summary(NS(write_line=observed.append), 0)
        assert observed == ["MT01_XDIST_WORKERS_COLLECTED=2"]
    finally:
        observer._COLLECTED_GATEWAYS.clear()


def test_live_twelve_workers_self_host_only(tmp_path):
    """Explicit Windows self-host opt-in; never benchmark hosted CI as the host."""
    import importlib.util
    if os.name != "nt" or os.getenv("AI_COMMERCE_SELF_HOST_WORKER_PROOF") != "1":
        pytest.skip("requires explicit Windows self-host worker evidence opt-in")
    assert importlib.util.find_spec("xdist") is not None, "xdist must be installed on self-host"
    fixture = tmp_path / "test_worker_fixture.py"
    fixture.write_text("def test_one():\n    assert True\n")
    env = verification.pytest_observation_env()
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    run = subprocess.run(
        [sys.executable, "-m", "pytest", str(fixture), "-q", "-n", "12"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    stdout = run.stdout.encode()
    assert run.returncode == 0, run.stdout + run.stderr
    assert verification.observed_xdist_workers(stdout) == 12, run.stdout


# MT-01 JUnit- and AST-backed WinError 1314 native skip verification (non-bypass).
_MT01_NATIVE_SKIP = "NATIVE_SYMLINK_CREATION_UNPROVEN: WinError 1314"
_R02_TEST_PATH = "tests/test_hostops_ef02_attempt_r02_readonly.py"
_R02_SKIP_CLASS = "tests.test_hostops_ef02_attempt_r02_readonly.EF02ForensicR02Tests"
_R02_SKIP_NAME = "test_symlink_scope_path_is_unsafe"
_RECONCILE_TEST_PATH = "tests/test_hostops_ef02_attempt_reconcile_readonly.py"
_RECONCILE_SKIP_CLASS = "tests.test_hostops_ef02_attempt_reconcile_readonly.EF02Attempt698ReadOnlyTests"


def _build_native_skip_repo(tmp_path: Path, *, test_path: str, class_name: str | None,
                            skip_func: str, pass_count: int, use_unittest: bool = True):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "feature_a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "feature_b.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests/test_b.py").write_text(
        "import feature_b\ndef test_b_ok():\n    assert feature_b.VALUE == 1\n",
        encoding="utf-8",
    )
    lines = ["import feature_a"]
    if use_unittest:
        lines.append("import unittest")
    else:
        lines.append("import pytest")
    lines.append("")
    if class_name:
        base_cls = "(unittest.TestCase)" if use_unittest else ""
        lines.append(f"class {class_name}{base_cls}:")
        indent = "    "
    else:
        indent = ""
    for i in range(pass_count):
        lines.append(f"{indent}def test_pass_{i}(self):" if class_name else f"def test_pass_{i}():")
        lines.append(f"{indent}    assert feature_a.VALUE == 1")
    lines.append(f"{indent}def {skip_func}(self):" if class_name else f"def {skip_func}():")
    skip_line_no = len(lines) + 1
    if use_unittest:
        lines.append(f'{indent}    self.skipTest("{_MT01_NATIVE_SKIP}")')
    else:
        lines.append(f'{indent}    pytest.skip("{_MT01_NATIVE_SKIP}")')
    target = repo / test_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    base = mt.file_hashes(repo)
    (repo / "feature_a.py").write_text("VALUE = 2\n", encoding="utf-8")
    groups = (
        mt.TestGroup("a", (test_path,), ("AC-A",), ("feature_a.py",)),
        mt.TestGroup("b", ("tests/test_b.py",), ("AC-B",), ("feature_b.py",)),
    )
    return SimpleNamespace(
        repo=repo, base=base, groups=groups,
        evidence=tmp_path / "evidence", skip_line=skip_line_no,
    )


def _make_win_junit(*, classname: str, name: str, pass_count: int = 7,
                    skip_type: str = "pytest.skip", message: str = _MT01_NATIVE_SKIP,
                    skip_text: str = "", tests: int | None = None,
                    errors: int = 0, failures: int = 0, skipped: int = 1,
                    extra_skips: tuple[tuple[str, str, str, str], ...] = ()) -> bytes:
    total = (pass_count + 1 + len(extra_skips)) if tests is None else tests
    mod = classname.rsplit(".", 1)[0] if "." in classname else classname
    cases = "".join(f'<testcase classname="{mod}" name="test_pass_{i}"/>' for i in range(pass_count))
    inner = skip_text
    cases += (f'<testcase classname="{classname}" name="{name}">'
              f'<skipped type="{skip_type}" message="{message}">{inner}</skipped>'
              f'</testcase>')
    for e_cls, e_name, e_type, e_msg in extra_skips:
        cases += (f'<testcase classname="{e_cls}" name="{e_name}">'
                  f'<skipped type="{e_type}" message="{e_msg}"/>'
                  f'</testcase>')
    return (f'<testsuites><testsuite tests="{total}" errors="{errors}" '
            f'failures="{failures}" skipped="{skipped}">{cases}'
            f'</testsuite></testsuites>').encode()


def _win_skip_stdout(*, test_path: str, lineno: int, slash: str = "\\",
                     workers: int = 12, pass_count: int = 7, skip_count: int = 1,
                     reason: str = _MT01_NATIVE_SKIP, extra: str = "",
                     extra_skip_lines: str = "") -> bytes:
    display_path = test_path.replace("/", slash)
    return (
        f"bringing up nodes...\n"
        f"MT01_XDIST_WORKERS_COLLECTED={workers}\n"
        f"================ short test summary info ==================\n"
        f"SKIPPED [1] {display_path}:{lineno}: {reason}\n"
        f"{extra_skip_lines}{extra}"
        f"{pass_count} passed, {skip_count} skipped in 1.83s\n"
    ).encode()


@pytest.mark.parametrize("slash", ["\\", "/"])
def test_mt01_r02_winerror1314_junit_skip_runs_subsequent_group(tmp_path, monkeypatch, slash):
    fix = _build_native_skip_repo(
        tmp_path, test_path=_R02_TEST_PATH, class_name="EF02ForensicR02Tests",
        skip_func=_R02_SKIP_NAME, pass_count=7, use_unittest=True,
    )
    stdout_r02 = _win_skip_stdout(
        test_path=_R02_TEST_PATH, lineno=fix.skip_line, slash=slash, pass_count=7,
    )
    junit_r02 = _make_win_junit(
        classname=_R02_SKIP_CLASS, name=_R02_SKIP_NAME, pass_count=7,
        skip_text=f"{_R02_TEST_PATH.replace('/', slash)}:{fix.skip_line}: {_MT01_NATIVE_SKIP}",
    )
    junit_b = b'<testsuites><testsuite tests="1" errors="0" failures="0" skipped="0"><testcase classname="tests.test_b" name="test_b_ok"/></testsuite></testsuites>'
    argv_a = ["python", "-m", "pytest", _R02_TEST_PATH, "-q", "-n", "12"]
    argv_b = ["python", "-m", "pytest", "tests/test_b.py", "-q", "-n", "12"]
    calls = []

    def run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs.get("env") or {})))
        junit_target = Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"])
        assert fix.repo.resolve() not in junit_target.resolve().parents
        if _R02_TEST_PATH in argv:
            junit_target.write_bytes(junit_r02)
            return subprocess.CompletedProcess(argv, 0, stdout_r02.decode(), "")
        junit_target.write_bytes(junit_b)
        return subprocess.CompletedProcess(argv, 0, "MT01_XDIST_WORKERS_COLLECTED=12\n1 passed in 0.02s\n", "")

    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    result, selected, prior = _execute(
        fix, acs=("AC-A", "AC-B"), registered_only=True,
        fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "mt01-a-tests": argv_a, "mt01-b-tests": argv_b},
    )
    assert result.returncode == 0
    assert [c[0] for c in calls] == [argv_a, argv_b]
    assert [g["result"] for g in selected["groups"]] == ["PASS", "PASS"]
    assert selected["groups"][0]["workers_actual"] == 12
    assert selected["groups"][0]["tests_executed"]["passed"] == 7
    assert selected["groups"][0]["tests_executed"]["skipped"] == 1
    assert set(prior) == {"a", "b"}
    assert (fix.evidence / "round-0/mt01.0.stdout.log").read_bytes() == stdout_r02
    assert b"WinError 1314" in (fix.evidence / "round-0/mt01.0.stdout.log").read_bytes()
    assert (fix.evidence / "round-0/mt01.0.junit.xml").read_bytes() == junit_r02
    ev_payload = json.loads((fix.evidence / "round-0/mt01.0.evidence.json").read_text())
    assert ev_payload["artifacts"]["junit"]["sha256"] == mt.digest(junit_r02)


@pytest.mark.parametrize("skip_func", [
    "test_symlink_or_junction_is_never_read_as_source",
    "test_fixed_ef02_root_does_not_follow_reparse",
])
def test_mt01_reconcile_winerror1314_junit_skips_pass_with_unmodified_evidence(tmp_path, monkeypatch, skip_func):
    fix = _build_native_skip_repo(
        tmp_path, test_path=_RECONCILE_TEST_PATH, class_name="EF02Attempt698ReadOnlyTests",
        skip_func=skip_func, pass_count=5, use_unittest=True,
    )
    stdout = _win_skip_stdout(test_path=_RECONCILE_TEST_PATH, lineno=fix.skip_line, pass_count=5)
    junit = _make_win_junit(classname=_RECONCILE_SKIP_CLASS, name=skip_func, pass_count=5)
    argv = ["python", "-m", "pytest", _RECONCILE_TEST_PATH, "-q", "-n", "12"]

    def run(cmd, **kwargs):
        Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"]).write_bytes(junit)
        return subprocess.CompletedProcess(cmd, 0, stdout.decode(), "")

    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    result, selected, prior = _execute(
        fix, acs=("AC-A",), registered_only=True,
        fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "mt01-a-tests": argv},
    )
    assert result.returncode == 0
    assert selected["groups"][0]["result"] == "PASS"
    assert selected["groups"][0]["tests_executed"]["passed"] == 5
    assert selected["groups"][0]["tests_executed"]["skipped"] == 1
    assert "a" in prior
    assert (fix.evidence / "round-0/mt01.0.stdout.log").read_bytes() == stdout


def test_mt01_other_optional_native_skip_with_matching_junit_and_coexisting_skip(tmp_path, monkeypatch):
    test_path = "tests/test_native_optional_symlink.py"
    fix = _build_native_skip_repo(
        tmp_path, test_path=test_path, class_name=None,
        skip_func="test_optional_native_symlink", pass_count=2, use_unittest=False,
    )
    stdout = _win_skip_stdout(
        test_path=test_path, lineno=fix.skip_line, slash="/", pass_count=2, skip_count=2,
        extra_skip_lines="SKIPPED [1] tests/test_other_skip.py:12: requires optional device\n",
    )
    junit = _make_win_junit(
        classname="tests.test_native_optional_symlink", name="test_optional_native_symlink",
        pass_count=2, skipped=2, tests=4,
        extra_skips=(("tests.test_other_skip", "test_dev", "pytest.skip", "requires optional device"),),
    )
    argv = ["python", "-m", "pytest", test_path, "-q", "-n", "12"]

    def run(cmd, **kwargs):
        Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"]).write_bytes(junit)
        return subprocess.CompletedProcess(cmd, 0, stdout.decode(), "")

    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    result, selected, prior = _execute(
        fix, acs=("AC-A",), registered_only=True,
        fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "mt01-a-tests": argv},
    )
    assert result.returncode == 0
    assert selected["groups"][0]["result"] == "PASS"
    assert selected["groups"][0]["tests_executed"]["passed"] == 2
    assert selected["groups"][0]["tests_executed"]["skipped"] == 2
    assert "a" in prior


@pytest.mark.parametrize("case", [
    "false_fake_skip", "junit_class_mismatch", "junit_name_mismatch",
    "junit_type_mismatch", "junit_message_mismatch", "junit_path_mismatch",
    "junit_text_path_mismatch", "duplicate_skip_lines", "extra_unbacked_skip_line",
    "stderr_winerror", "malformed_xml", "doctype_xml", "oversized_junit",
    "missing_junit", "actual_worker_11", "no_valid_worker_receipt",
    "raw_error_outside_skip", "nonzero_exit", "missing_fixture",
    "permission_denied", "no_selected_tests",
])
def test_mt01_winerror1314_negative_matrix_holds(tmp_path, monkeypatch, case):
    fix = _build_native_skip_repo(
        tmp_path, test_path=_R02_TEST_PATH, class_name="EF02ForensicR02Tests",
        skip_func=_R02_SKIP_NAME, pass_count=7, use_unittest=True,
    )
    stdout = _win_skip_stdout(test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7)
    stderr = b""
    code = 0
    junit: bytes | None = _make_win_junit(classname=_R02_SKIP_CLASS, name=_R02_SKIP_NAME, pass_count=7)

    if case == "false_fake_skip":
        stdout = _win_skip_stdout(
            test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7,
            reason="NATIVE_SYMLINK_CREATION_UNPROVEN: WinError 5",
        )
    elif case == "junit_class_mismatch":
        junit = _make_win_junit(classname="tests.test_hostops_ef02_attempt_r02_readonly.FakeR02Tests", name=_R02_SKIP_NAME, pass_count=7)
    elif case == "junit_name_mismatch":
        junit = _make_win_junit(classname=_R02_SKIP_CLASS, name="test_pass_0", pass_count=7)
    elif case == "junit_type_mismatch":
        junit = _make_win_junit(classname=_R02_SKIP_CLASS, name=_R02_SKIP_NAME, pass_count=7, skip_type="other")
    elif case == "junit_message_mismatch":
        junit = _make_win_junit(classname=_R02_SKIP_CLASS, name=_R02_SKIP_NAME, pass_count=7, message="NATIVE_SYMLINK_CREATION_UNPROVEN: [WinError 1314]")
    elif case == "junit_path_mismatch":
        stdout = _win_skip_stdout(test_path="tests/test_b.py", lineno=2, pass_count=7)
    elif case == "junit_text_path_mismatch":
        junit = _make_win_junit(
            classname=_R02_SKIP_CLASS, name=_R02_SKIP_NAME, pass_count=7,
            skip_text=f"tests/test_other.py:{fix.skip_line}: {_MT01_NATIVE_SKIP}",
        )
    elif case == "duplicate_skip_lines":
        dup = f"SKIPPED [1] tests\\test_hostops_ef02_attempt_r02_readonly.py:{fix.skip_line}: {_MT01_NATIVE_SKIP}\n"
        stdout = _win_skip_stdout(test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7, extra_skip_lines=dup)
    elif case == "extra_unbacked_skip_line":
        stdout = _win_skip_stdout(
            test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7,
            extra_skip_lines="SKIPPED [1] tests/other.py:1: other\n",
        )
    elif case == "stderr_winerror":
        stderr = b"OSError: WinError 1314"
    elif case == "malformed_xml":
        junit = b"<testsuites><testsuite"
    elif case == "doctype_xml":
        junit = b"<!DOCTYPE malicious>" + junit
    elif case == "oversized_junit":
        junit = b"x" * (8 * 1024 * 1024 + 1)
    elif case == "missing_junit":
        junit = None
    elif case == "actual_worker_11":
        stdout = _win_skip_stdout(test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7, workers=11)
    elif case == "no_valid_worker_receipt":
        stdout = stdout.replace(b"MT01_XDIST_WORKERS_COLLECTED=12\n", b"12 workers [8 items]\n")
    elif case == "raw_error_outside_skip":
        stdout = _win_skip_stdout(test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7, extra="Traceback: WinError 1314\n")
    elif case == "nonzero_exit":
        code = 1
    elif case == "missing_fixture":
        code = 1
        stdout = b"MT01_XDIST_WORKERS_COLLECTED=12\n1 error in 0.05s\nfixture 'missing_dep' not found\n"
    elif case == "permission_denied":
        stderr = b"PermissionError: [WinError 5] Access is denied"
    elif case == "no_selected_tests":
        code = 5
        stdout = b"MT01_XDIST_WORKERS_COLLECTED=12\nno tests ran in 0.01s\n"

    argv = ["python", "-m", "pytest", _R02_TEST_PATH, "-q", "-n", "12"]

    def run(cmd, **kwargs):
        if junit is not None:
            Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"]).write_bytes(junit)
        return subprocess.CompletedProcess(cmd, code, stdout.decode(), stderr.decode())

    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    result, selected, prior = _execute(
        fix, acs=("AC-A", "AC-B"), registered_only=True,
        fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "mt01-a-tests": argv,
                             "mt01-b-tests": ["python", "-m", "pytest", "tests/test_b.py", "-q", "-n", "12"]},
    )
    assert result.returncode != 0
    assert not prior
    assert selected["groups"][0]["result"] == "UNPROVEN"
    assert selected["groups"][1]["result"] == "UNPROVEN"
    assert selected["groups"][1]["reason"] == "NOT_EXECUTED_AFTER_UNPROVEN_GROUP"
    assert not list(fix.evidence.rglob("*.evidence.json"))


def test_mt01_explicit_serial_mismatch_holds(tmp_path, monkeypatch):
    fix = _build_native_skip_repo(
        tmp_path, test_path=_R02_TEST_PATH, class_name="EF02ForensicR02Tests",
        skip_func=_R02_SKIP_NAME, pass_count=7, use_unittest=True,
    )
    serial_argv = ["python", "-m", "pytest", _R02_TEST_PATH, "-q"]
    stdout = _win_skip_stdout(test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7, workers=12)
    junit = _make_win_junit(classname=_R02_SKIP_CLASS, name=_R02_SKIP_NAME, pass_count=7)

    def run(cmd, **kwargs):
        Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"]).write_bytes(junit)
        return subprocess.CompletedProcess(cmd, 0, stdout.decode(), "")

    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    result, selected, prior = _execute(
        fix, acs=("AC-A",), registered_only=True,
        fallback_argv=FULL,
        registered_profiles={"project-tests-serial": FULL, "mt01-a-tests": serial_argv},
    )
    assert result.returncode != 0
    assert not prior
    assert selected["groups"][0]["result"] == "UNPROVEN"
    assert selected["groups"][0]["reason"] == "PYTEST_WORKER_COUNT_MISMATCH"


def test_mt01_winerror1314_source_drift_discards_evidence(tmp_path, monkeypatch):
    fix = _build_native_skip_repo(
        tmp_path, test_path=_R02_TEST_PATH, class_name="EF02ForensicR02Tests",
        skip_func=_R02_SKIP_NAME, pass_count=7, use_unittest=True,
    )
    stdout = _win_skip_stdout(test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7)
    junit = _make_win_junit(classname=_R02_SKIP_CLASS, name=_R02_SKIP_NAME, pass_count=7)
    argv = ["python", "-m", "pytest", _R02_TEST_PATH, "-q", "-n", "12"]

    def mutate(cmd, **kwargs):
        Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"]).write_bytes(junit)
        (fix.repo / "feature_a.py").write_text("VALUE = 999\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout.decode(), "")

    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=mutate, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    with pytest.raises(RuntimeError, match="SOURCE_DRIFT"):
        _execute(
            fix, acs=("AC-A",), registered_only=True,
            fallback_argv=[*FULL, "-n", "12"],
            registered_profiles={"project-tests": [*FULL, "-n", "12"], "mt01-a-tests": argv},
        )
    assert not list(fix.evidence.rglob("*.evidence.json"))


def test_mt01_unsafe_junit_scratch_inside_workspace_holds(tmp_path, monkeypatch):
    fix = _build_native_skip_repo(
        tmp_path, test_path=_R02_TEST_PATH, class_name="EF02ForensicR02Tests",
        skip_func=_R02_SKIP_NAME, pass_count=7, use_unittest=True,
    )
    inside = fix.repo / ".unsafe-tmp"
    inside.mkdir()
    monkeypatch.setattr(verification.tempfile, "gettempdir", lambda: str(inside))
    argv = ["python", "-m", "pytest", _R02_TEST_PATH, "-q", "-n", "12"]
    result, selected, prior = _execute(
        fix, acs=("AC-A",), registered_only=True,
        fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "mt01-a-tests": argv},
    )
    assert result.returncode != 0
    assert not prior
    assert selected["groups"][0]["result"] == "UNPROVEN"
    assert selected["groups"][0]["reason"] == "PYTEST_JUNIT_SCRATCH_INSIDE_WORKSPACE"


def test_mt01_winerror1314_junit_accepts_absolute_test_file_path(tmp_path, monkeypatch):
    fix = _build_native_skip_repo(
        tmp_path, test_path=_R02_TEST_PATH, class_name="EF02ForensicR02Tests",
        skip_func=_R02_SKIP_NAME, pass_count=7, use_unittest=True,
    )
    abs_test_path = str((fix.repo / _R02_TEST_PATH).resolve())
    stdout = _win_skip_stdout(test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7)
    junit = _make_win_junit(
        classname=_R02_SKIP_CLASS, name=_R02_SKIP_NAME, pass_count=7,
        skip_text=f"{abs_test_path}:{fix.skip_line}: {_MT01_NATIVE_SKIP}",
    )
    argv = ["python", "-m", "pytest", _R02_TEST_PATH, "-q", "-n", "12"]

    def run(cmd, **kwargs):
        Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"]).write_bytes(junit)
        return subprocess.CompletedProcess(cmd, 0, stdout.decode(), "")

    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    result, selected, prior = _execute(
        fix, acs=("AC-A",), registered_only=True,
        fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "mt01-a-tests": argv},
    )
    assert result.returncode == 0
    assert selected["groups"][0]["result"] == "PASS"
    assert "a" in prior


def test_pytest_observation_env_strips_outer_junit_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMMERCE_MT01_JUNIT_PATH", str(tmp_path / "outer.junit.xml"))
    monkeypatch.setenv(
        "PYTEST_PLUGINS",
        "existing.plugin, _ai_commerce_mt01_junit_plugin , another.plugin",
    )
    obs_env = verification.pytest_observation_env()
    assert "AI_COMMERCE_MT01_JUNIT_PATH" not in obs_env
    obs_plugins = [p.strip() for p in obs_env["PYTEST_PLUGINS"].split(",") if p.strip()]
    assert "_ai_commerce_mt01_junit_plugin" not in obs_plugins
    assert "existing.plugin" in obs_plugins
    assert "another.plugin" in obs_plugins
    assert verification._OBSERVER_PLUGIN in obs_plugins

    with verification._junit_capture_env(tmp_path) as (cap_env, junit_path, _scratch):
        assert cap_env["AI_COMMERCE_MT01_JUNIT_PATH"] == str(junit_path)
        cap_plugins = [p.strip() for p in cap_env["PYTEST_PLUGINS"].split(",") if p.strip()]
        assert "_ai_commerce_mt01_junit_plugin" in cap_plugins
        assert verification._OBSERVER_PLUGIN in cap_plugins


@pytest.mark.parametrize(
    ("junit_payload", "expected_reason"),
    [
        (None, "JUNIT_XML_ABSENT"),
        (b"<testsuites><testsuite", "JUNIT_XML_MALFORMED_OR_UNSAFE"),
    ],
)
def test_mt01_winerror1314_junit_reason_recorded_when_absent_or_malformed(
    tmp_path, monkeypatch, junit_payload, expected_reason,
):
    fix = _build_native_skip_repo(
        tmp_path, test_path=_R02_TEST_PATH, class_name="EF02ForensicR02Tests",
        skip_func=_R02_SKIP_NAME, pass_count=7, use_unittest=True,
    )
    stdout = _win_skip_stdout(test_path=_R02_TEST_PATH, lineno=fix.skip_line, pass_count=7)
    argv = ["python", "-m", "pytest", _R02_TEST_PATH, "-q", "-n", "12"]

    def run(cmd, **kwargs):
        if junit_payload is not None:
            Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"]).write_bytes(junit_payload)
        return subprocess.CompletedProcess(cmd, 0, stdout.decode(), "")

    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(
        run=run, CompletedProcess=subprocess.CompletedProcess, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    result, selected, prior = _execute(
        fix, acs=("AC-A",), registered_only=True,
        fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "mt01-a-tests": argv},
    )
    assert result.returncode != 0
    assert not prior
    assert selected["groups"][0]["result"] == "UNPROVEN"
    assert selected["groups"][0]["junit_error"] == expected_reason
    assert selected["groups"][0]["reason"] == expected_reason


def test_mt01_windows_real_subprocess_xdist_junit_winerror1314_and_replay_holds(tmp_path, monkeypatch):
    """Real Windows pytest -n 12 + JUnit XML subprocess integration proof for WinError 1314 skips."""
    if os.name != "nt":
        pytest.skip("Windows-only real JUnit + xdist WinError 1314 integration test")
    import ast
    import importlib.util
    import re
    import shutil

    assert importlib.util.find_spec("xdist") is not None, "xdist must be installed"

    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\naddopts = "-ra"\n',
        encoding="utf-8",
    )
    (repo / "feature_a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "feature_b.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests/test_b.py").write_text(
        "import feature_b\n\n\ndef test_b_ok():\n    assert feature_b.VALUE == 1\n",
        encoding="utf-8",
    )

    native_lines = [
        "import os",
        "from pathlib import Path",
        "import tempfile",
        "import unittest",
        "import unittest.mock",
        "",
        "import feature_a",
        "",
        "",
        "class TestNativeSymlink(unittest.TestCase):",
    ]
    for idx in range(7):
        native_lines.extend([
            f"    def test_pass_{idx}(self):",
            "        self.assertEqual(feature_a.VALUE, 2)",
            "",
        ])
    native_lines.extend([
        '    @unittest.skipUnless(os.name == "nt", "Windows native symlink test")',
        "    def test_native_symlink_winerror_1314(self):",
        "        with tempfile.TemporaryDirectory() as folder:",
        "            base = Path(folder)",
        '            target = base / "target.txt"',
        '            target.write_text("ok", encoding="utf-8")',
        '            link = base / "link.txt"',
        "            try:",
        "                link.symlink_to(target)",
        "            except OSError as exc:",
        '                if getattr(exc, "winerror", None) == 1314:',
        f'                    self.skipTest("{_MT01_NATIVE_SKIP}")  # real Windows native observation',
        "                raise",
        "            # SYNTHETIC OS-boundary privilege fault injection when the host",
        "            # grants SeCreateSymbolicLinkPrivilege, so both privileged and",
        "            # unprivileged Windows hosts force the exact WinError 1314 skip.",
        "            def _deny_symlink(*_args, **_kwargs):",
        '                raise OSError(13, "A required privilege is not held by the client", None, 1314)',
        '            with unittest.mock.patch.object(Path, "symlink_to", side_effect=_deny_symlink):',
        "                try:",
        '                    (base / "synthetic_link.txt").symlink_to(target)',
        "                except OSError as exc:",
        '                    if getattr(exc, "winerror", None) == 1314:',
        f'                        self.skipTest("{_MT01_NATIVE_SKIP}")  # synthetic privilege denial',
        "                    raise",
        "",
    ])
    native_source = "\n".join(native_lines)
    pre_tree = ast.parse(native_source)
    assert not [
        n.name for n in pre_tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    pre_cls = next(
        n for n in pre_tree.body
        if isinstance(n, ast.ClassDef) and n.name == "TestNativeSymlink"
    )
    pre_methods = [
        n.name for n in pre_cls.body
        if isinstance(n, ast.FunctionDef)
    ]
    assert pre_methods == [*(f"test_pass_{idx}" for idx in range(7)), "test_native_symlink_winerror_1314"]
    pre_skip_fn = next(
        n for n in pre_cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "test_native_symlink_winerror_1314"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skipTest"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == _MT01_NATIVE_SKIP
        for node in ast.walk(pre_skip_fn)
    )
    native_test_path = repo / "tests/test_native.py"
    native_test_path.write_text(native_source, encoding="utf-8")

    base = mt.file_hashes(repo)
    (repo / "feature_a.py").write_text("VALUE = 2\n", encoding="utf-8")
    groups = (
        mt.TestGroup("a", ("tests/test_native.py",), ("AC-A",), ("feature_a.py",)),
        mt.TestGroup("b", ("tests/test_b.py",), ("AC-B",), ("feature_b.py",)),
    )
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    for key in (
        "PYTEST_ADDOPTS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "AI_COMMERCE_MT01_JUNIT_PATH",
        "COV_CORE_SOURCE",
        "COV_CORE_CONFIG",
        "COV_CORE_DATAFILE",
    ):
        monkeypatch.delenv(key, raising=False)
    src_dir = str(ROOT / "src")
    existing_pp = os.environ.get("PYTHONPATH", "")
    pp_parts = [p for p in existing_pp.split(os.pathsep) if p and p != src_dir]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src_dir, *pp_parts]))
    exe_dir = str(Path(sys.executable).parent)
    existing_path = os.environ.get("PATH", "")
    path_parts = [
        p for p in existing_path.split(os.pathsep)
        if p and os.path.normcase(p) != os.path.normcase(exe_dir)
    ]
    monkeypatch.setenv("PATH", os.pathsep.join([exe_dir, *path_parts]))
    resolved_python = shutil.which("python")
    assert resolved_python is not None
    assert Path(resolved_python).resolve() == Path(sys.executable).resolve()

    argv_a = ["python", "-m", "pytest", "tests/test_native.py", "-q", "-n", "12"]
    argv_b = ["python", "-m", "pytest", "tests/test_b.py", "-q", "-n", "12"]
    fallback_argv = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    profiles = {
        "project-tests": fallback_argv,
        "mt01-a-tests": argv_a,
        "mt01-b-tests": argv_b,
    }

    pre_plan = mt.select_plan(
        repo=repo,
        base_sha="a" * 40,
        base_files=base,
        candidate_files=mt.file_hashes(repo),
        changed_paths=["feature_a.py"],
        required_acs=("AC-A", "AC-B"),
        fallback_argv=fallback_argv,
        groups=groups,
        registered_only=True,
        registered_profiles=profiles,
    )
    assert pre_plan["fallback"] is False
    assert pre_plan["reason_codes"] == []
    assert [g["group"] for g in pre_plan["groups"]] == ["a", "b"]
    assert [g["selected_profile"] for g in pre_plan["groups"]] == ["mt01-a-tests", "mt01-b-tests"]
    assert [g["binding"]["argv"] for g in pre_plan["groups"]] == [argv_a, argv_b]
    assert all(g["state"] == "EXECUTE" and g["execute"] is True for g in pre_plan["groups"])
    assert all(g["binding"]["environment_proven"] is True for g in pre_plan["groups"])

    real_run_pytest_with_junit = verification._run_pytest_with_junit
    captured_runs = []

    def observed_run_pytest(argv, *, repo: Path, timeout: float):
        outcome = real_run_pytest_with_junit(argv, repo=repo, timeout=timeout)
        captured_runs.append((list(argv), outcome))
        return outcome

    monkeypatch.setattr(verification, "_run_pytest_with_junit", observed_run_pytest)

    saved_files: dict[str, str] = {}

    def save(key: str, filename: str, data: bytes) -> None:
        relative = "real-0/" + filename
        path = evidence_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
        saved_files[key] = relative

    prior: dict[str, mt.Evidence] = {}
    result, selected = verification.execute_plan(
        repo=repo,
        output_dir=evidence_dir,
        base_sha="a" * 40,
        base_files=base,
        changed_paths=["feature_a.py"],
        required_acs=("AC-A", "AC-B"),
        fallback_argv=fallback_argv,
        prior=prior,
        save=save,
        evidence_files=saved_files,
        deadline=time.monotonic() + 120,
        timeout_seconds=120,
        registered_only=True,
        groups=groups,
        registered_profiles=profiles,
    )

    assert len(captured_runs) == 2
    assert [entry[0] for entry in captured_runs] == [argv_a, argv_b]
    res_a, out_a, err_a, junit_a, junit_err_a = captured_runs[0][1]

    # (1) Real exit 0, xdist controller receipt == 12, 7 passed / 1 skipped, WinError 1314 line.
    assert res_a.returncode == 0, out_a.decode("utf-8", "replace") + err_a.decode("utf-8", "replace")
    assert verification.observed_xdist_workers(out_a) == 12, out_a.decode("utf-8", "replace")
    assert mt.pytest_counts(out_a) == {
        "passed": 7,
        "failed": 0,
        "skipped": 1,
        "errors": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    skip_lines = [ln for ln in out_a.splitlines() if ln.startswith(b"SKIPPED [")]
    assert len(skip_lines) == 1, out_a.decode("utf-8", "replace")
    m_skip = verification._SKIPPED_LINE_RE.fullmatch(skip_lines[0])
    assert m_skip is not None
    assert m_skip.group(1) == b"1"
    assert m_skip.group(2).decode("utf-8").replace("\\", "/") == "tests/test_native.py"
    stdout_lineno = int(m_skip.group(3))
    assert m_skip.group(4).decode("utf-8").strip() == _MT01_NATIVE_SKIP

    # (2) Safe non-empty JUnit XML with one skip case matching source AST and stdout.
    assert junit_err_a is None
    assert junit_a is not None
    root_xml = verification._parse_safe_junit(junit_a)
    assert root_xml is not None
    suites = [root_xml] if root_xml.tag == "testsuite" else list(root_xml)
    assert len(suites) == 1 and suites[0].tag == "testsuite"
    suite = suites[0]
    assert (
        suite.get("tests"),
        suite.get("errors"),
        suite.get("failures"),
        suite.get("skipped"),
    ) == ("8", "0", "0", "1")
    cases = list(suite.findall("testcase"))
    assert len(cases) == 8
    skipped_cases = [c for c in cases if c.find("skipped") is not None]
    assert len(skipped_cases) == 1
    sc = skipped_cases[0]
    skip_el = sc.find("skipped")
    assert skip_el is not None
    assert sc.get("classname") == "tests.test_native.TestNativeSymlink"
    assert sc.get("name") == "test_native_symlink_winerror_1314"
    assert skip_el.get("type") == "pytest.skip"
    assert (skip_el.get("message") or "").strip() == _MT01_NATIVE_SKIP
    m_xml_text = re.fullmatch(r"(.+):([1-9][0-9]*): (.+)", (skip_el.text or "").strip())
    assert m_xml_text is not None
    xml_raw_path, xml_raw_line, xml_raw_reason = m_xml_text.groups()
    assert Path(xml_raw_path).resolve() == native_test_path.resolve()
    assert int(xml_raw_line) == stdout_lineno
    assert xml_raw_reason.strip() == _MT01_NATIVE_SKIP

    tree = ast.parse(native_source)
    cls_node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestNativeSymlink")
    fn_node = next(
        n for n in cls_node.body
        if isinstance(n, ast.FunctionDef) and n.name == "test_native_symlink_winerror_1314"
    )
    fn_start = min([fn_node.lineno, *(d.lineno for d in fn_node.decorator_list)])
    assert stdout_lineno in (fn_start, fn_node.lineno)
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skipTest"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == _MT01_NATIVE_SKIP
        for node in ast.walk(fn_node)
    )
    assert verification._ast_matches_skip_case(
        native_test_path,
        module_variants=("tests.test_native",),
        classname=sc.get("classname") or "",
        case_name=sc.get("name") or "",
        lineno=stdout_lineno,
    )

    # (3) Classifier copy strips ONLY the proven WinError line; persisted logs & next group pass.
    candidate_files = mt.file_hashes(repo)
    stripped = verification._strip_verified_symlink_skip(
        out_a, err_a, res_a.returncode, junit_a, repo=repo, candidate_files=candidate_files,
    )
    expected_stripped = b"".join(
        line for line in out_a.splitlines(keepends=True) if b"winerror" not in line.lower()
    )
    assert stripped == expected_stripped
    assert stripped != out_a
    assert b"winerror" not in stripped.lower()
    assert b"7 passed, 1 skipped" in stripped
    assert b"MT01_XDIST_WORKERS_COLLECTED=12" in stripped
    diag, j_issue = verification._diagnose_junit_and_infra(
        out_a, err_a, res_a.returncode, junit_a, junit_err_a,
        repo=repo, candidate_files=candidate_files,
    )
    assert j_issue is None
    assert not any(marker in diag for marker in verification._INFRASTRUCTURE_MARKERS)
    assert result.returncode == 0
    assert selected["fallback"] is False
    assert [g["group"] for g in selected["groups"]] == ["a", "b"]
    assert [g["selected_profile"] for g in selected["groups"]] == ["mt01-a-tests", "mt01-b-tests"]
    assert [g["result"] for g in selected["groups"]] == ["PASS", "PASS"]
    assert [g["workers_actual"] for g in selected["groups"]] == [12, 12]
    assert set(prior) == {"a", "b"}
    assert (evidence_dir / "real-0/mt01.0.stdout.log").read_bytes() == out_a
    assert b"WinError 1314" in (evidence_dir / "real-0/mt01.0.stdout.log").read_bytes()
    assert (evidence_dir / "real-0/mt01.0.junit.xml").read_bytes() == junit_a
    ev_payload = json.loads((evidence_dir / "real-0/mt01.0.evidence.json").read_text(encoding="utf-8"))
    assert ev_payload["artifacts"]["junit"]["sha256"] == mt.digest(junit_a)
    assert selected["groups"][0]["evidence_sha256"] == prior["a"].sha256
    assert prior["a"].valid_for(selected["groups"][0]["binding"], evidence_dir)

    res_b, out_b, err_b, junit_b, junit_err_b = captured_runs[1][1]
    assert res_b.returncode == 0, out_b.decode("utf-8", "replace") + err_b.decode("utf-8", "replace")
    assert verification.observed_xdist_workers(out_b) == 12, out_b.decode("utf-8", "replace")
    assert junit_err_b is None
    assert junit_b is not None
    assert mt.pytest_counts(out_b) == {
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    assert (evidence_dir / "real-0/mt01.1.stdout.log").read_bytes() == out_b
    assert (evidence_dir / "real-0/mt01.1.junit.xml").read_bytes() == junit_b
    ev_payload_b = json.loads((evidence_dir / "real-0/mt01.1.evidence.json").read_text(encoding="utf-8"))
    assert ev_payload_b["artifacts"]["junit"]["sha256"] == mt.digest(junit_b)
    assert selected["groups"][1]["evidence_sha256"] == prior["b"].sha256
    assert prior["b"].valid_for(selected["groups"][1]["binding"], evidence_dir)

    # (4) Replay the SAME REAL stdout under tampered/missing JUnit, wrong worker, stderr WinError,
    # mismatched skip text, malicious XML, and extra stdout WinError -> HOLD / UNPROVEN.
    monkeypatch.setattr(verification, "_run_pytest_with_junit", real_run_pytest_with_junit)
    forged_junit_name = junit_a.replace(b'name="test_native_symlink_winerror_1314"', b'name="test_pass_0"')
    incorrect_junit_msg = junit_a.replace(
        f'message="{_MT01_NATIVE_SKIP}"'.encode(),
        b'message="NATIVE_SYMLINK_CREATION_UNPROVEN: [WinError 1314]"',
    )
    wrong_worker_out = out_a.replace(b"MT01_XDIST_WORKERS_COLLECTED=12", b"MT01_XDIST_WORKERS_COLLECTED=11")
    mismatched_skip_text = junit_a.replace(f":{stdout_lineno}: ".encode(), f":{stdout_lineno + 1}: ".encode())
    assert forged_junit_name != junit_a
    assert incorrect_junit_msg != junit_a
    assert wrong_worker_out != out_a
    assert mismatched_skip_text != junit_a
    replay_cases = [
        ("missing_junit", out_a, err_a, None, "JUNIT_XML_ABSENT"),
        ("forged_junit_name", out_a, err_a, forged_junit_name, "VERIFY_INFRASTRUCTURE_ERROR"),
        ("incorrect_junit_message", out_a, err_a, incorrect_junit_msg, "VERIFY_INFRASTRUCTURE_ERROR"),
        ("wrong_worker_receipt", wrong_worker_out, err_a, junit_a, "PYTEST_WORKER_COUNT_MISMATCH"),
        (
            "stderr_winerror",
            out_a,
            err_a + b"\nOSError: [WinError 1314] A required privilege is not held by the client\n",
            junit_a,
            "VERIFY_INFRASTRUCTURE_ERROR",
        ),
        ("mismatched_skip_text", out_a, err_a, mismatched_skip_text, "VERIFY_INFRASTRUCTURE_ERROR"),
        ("malicious_xml", out_a, err_a, b"<!DOCTYPE malicious>" + junit_a, "JUNIT_XML_MALFORMED_OR_UNSAFE"),
        (
            "extra_stdout_winerror",
            out_a + b"\nTraceback: WinError 1314\n",
            err_a,
            junit_a,
            "VERIFY_INFRASTRUCTURE_ERROR",
        ),
    ]
    fix = SimpleNamespace(repo=repo, base=base, groups=groups, evidence=tmp_path / "replay_evidence")
    for case_name, r_out, r_err, r_junit, expected_reason in replay_cases:
        def replay_run(cmd, **kwargs):
            if r_junit is not None:
                Path(kwargs["env"]["AI_COMMERCE_MT01_JUNIT_PATH"]).write_bytes(r_junit)
            return subprocess.CompletedProcess(
                cmd, 0, r_out.decode("utf-8", "replace"), r_err.decode("utf-8", "replace"),
            )

        monkeypatch.setattr(
            verification,
            "subprocess",
            SimpleNamespace(
                run=replay_run,
                CompletedProcess=subprocess.CompletedProcess,
                TimeoutExpired=subprocess.TimeoutExpired,
            ),
        )
        replay_res, replay_plan, replay_prior = _execute(
            fix,
            acs=("AC-A", "AC-B"),
            changed=("feature_a.py",),
            name=f"replay-{case_name}",
            registered_only=True,
            fallback_argv=fallback_argv,
            registered_profiles=profiles,
        )
        assert replay_res.returncode != 0, case_name
        assert not replay_prior, case_name
        assert replay_plan["groups"][0]["result"] == "UNPROVEN", (case_name, replay_plan["groups"][0])
        assert replay_plan["groups"][0]["reason"] == expected_reason, (case_name, replay_plan["groups"][0])
        assert replay_plan["groups"][1]["result"] == "UNPROVEN", (case_name, replay_plan["groups"][1])
        assert replay_plan["groups"][1]["reason"] == "NOT_EXECUTED_AFTER_UNPROVEN_GROUP", case_name
        assert not list((fix.evidence / f"replay-{case_name}").glob("*.evidence.json")), case_name


def test_v21_select_plan_loads_committed_manifests_under_project_tests(monkeypatch, real_tree):
    """V2.1 select_plan honors committed manifest owners even for non-onboarding project-tests."""
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    profiles = json.loads(
        (ROOT / ".github/ef02/verify-profiles.json").read_text(encoding="utf-8"))
    head_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    changed = "src/ai_commerce/hostops_v3/personal.py"
    plan = mt.select_plan(
        repo=ROOT, base_sha=head_sha,
        base_files=real_tree.files, candidate_files=real_tree.files,
        changed_paths=[changed], required_acs=(),
        fallback_argv=profiles["project-tests"],
        groups=mt.approved_groups(),
        registered_only=True, registered_profiles=profiles,
        allowed_paths=[changed])
    assert plan["version"] == 2 and not plan["fallback"]
    assert {"hostops-personal", "hostops-v3"} <= set(plan["selected_groups"])
    assert all(g["execute"] and g["state"] == "EXECUTE" for g in plan["groups"])
