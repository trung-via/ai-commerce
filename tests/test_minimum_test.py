"""MT-01 deterministic selection/evidence and real local verification; no vendors."""
from dataclasses import FrozenInstanceError
import json
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


@pytest.fixture(scope="module")
def real_tree():
    files = mt.file_hashes(ROOT)
    groups = mt.approved_groups()
    registry = {"mt01-" + g.name + "-tests": ["python", "-m", "pytest", *g.tests, "-q", "-n", "12"]
                for g in groups}
    registry["project-tests"] = [*FULL, "-n", "12"]
    return SimpleNamespace(files=files, groups=groups, registry=registry, graph=mt.import_graph(ROOT, files))


def real_plan(real_tree, monkeypatch, path, *, registry=None, acs=(), aggregate=()):
    monkeypatch.setattr(mt, "import_graph", lambda *_: real_tree.graph)
    return mt.select_plan(repo=ROOT, base_sha="6601881e7b498ae139fbe8a8f04c195905b15ed3",
        base_files=real_tree.files, candidate_files=real_tree.files, changed_paths=[path], required_acs=acs,
        fallback_argv=real_tree.registry["project-tests"], registered_only=True,
        registered_profiles=real_tree.registry if registry is None else registry, aggregate_sources=aggregate, env={})


def test_reviewed_map_contains_exact_real_paths_and_all_current_python_test_families(real_tree):
    declared = [p for g in real_tree.groups for p in (*g.paths, *g.tests, *g.consumers, *g.contracts)]
    assert set(declared) <= real_tree.files.keys()
    mapped_tests = [p for g in real_tree.groups for p in g.tests]
    assert len(mapped_tests) == len(set(mapped_tests))
    # Registry-only control test is intentionally broad-verified until protected PR #160 activates.
    assert {p for p in real_tree.files if p.startswith("tests/test_") and p.endswith(".py")} - {"tests/test_ef02_mt01_registry.py"} <= set(mapped_tests)
    assert "src/ai_commerce/opportunity/trends/tests/test_m02.py" in mapped_tests
    assert {f"a{n:02}" for n in range(2, 13)} <= {g.name for g in real_tree.groups}


@pytest.mark.parametrize("path,expected", [
    ("tests/test_a12_claims.py", "a12"),
    ("src/ai_commerce/remote/chrome_acquisition.py", "ef02-wake"),
])
def test_real_known_component_uses_exact_registered_companion(real_tree, monkeypatch, path, expected):
    selected = real_plan(real_tree, monkeypatch, path)
    assert not selected["fallback"] and selected["selected_groups"] == [expected]
    entry = selected["groups"][0]
    assert entry["binding"]["argv"] == real_tree.registry["mt01-" + expected + "-tests"]
    assert entry["selected_profile"] == "mt01-" + expected + "-tests"
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
    assert all(current[key] == argv for key, argv in proposed.items() if key in current)  # Companions may already be active.
    combined = {**current, **proposed}
    for g in real_tree.groups:
        expected = ["python", "-m", "pytest", *g.tests, "-q", "-n", "12"]
        assert expected in combined.values()


def test_component_local_m02_requires_profile_that_actually_collects_it(real_tree, monkeypatch):
    current = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    path = "src/ai_commerce/opportunity/trends/tracking.py"
    selected = real_plan(real_tree, monkeypatch, path, registry=current)
    if "mt01-a10-m02-tests" in current:
        assert not selected["fallback"] and "a10-m02" in selected["selected_groups"]
    else:
        assert selected["fallback"] and selected["groups"][0]["state"] == "UNPROVEN"
        assert selected["groups"][0]["binding"]["argv"] == current["project-tests"]
    activated = real_plan(real_tree, monkeypatch, path)
    assert not activated["fallback"] and "a10-m02" in activated["selected_groups"]


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


def test_registered_known_group_selects_exact_minimal_command_and_invalidates_registry_edit(mapped):
    command = ["python", "-m", "pytest", "tests/test_a.py", "-q", "-n", "12"]
    parallel_full = [*FULL, "-n", "12"]
    registry = {"project-tests": parallel_full, "component-a": command}
    selected = plan(mapped, registered_only=True, registered_profiles=registry,
                    fallback_argv=parallel_full)
    assert not selected["fallback"] and [g["group"] for g in selected["groups"]] == ["a"]
    assert selected["groups"][0]["binding"]["argv"] == command
    assert selected["groups"][0]["binding"]["registered_profile"] == "component-a"
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
    assert receipt["groups"][0]["state"] == "UNPROVEN" and receipt["groups"][0]["workers_actual"] == 12
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
        return subprocess.CompletedProcess(argv, 0, f"{workers} workers [3 items]\n2 passed, 1 skipped in 0.02s", "")
    monkeypatch.setattr(verification, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired,
                                                                 CompletedProcess=subprocess.CompletedProcess))
    argv = ["python", "-m", "pytest", "tests/test_a.py", "-q", "-n", "12"]
    result, selected, prior = _execute(mapped, registered_only=True, fallback_argv=[*FULL, "-n", "12"],
        registered_profiles={"project-tests": [*FULL, "-n", "12"], "component-a": argv})
    entry = selected["groups"][0]
    assert calls == [argv] and entry["workers_requested"] == 12 and entry["workers_actual"] == workers
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
