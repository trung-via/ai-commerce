"""MT-01 V2.1 adversarial selection contract. No model, network or host effects."""
import os
from pathlib import Path
import pytest
from ai_commerce.engineering.minimum_test import TestGroup, file_hashes
from ai_commerce.engineering.impact_v21 import registered_action_plan

from ai_commerce.engineering.impact_v21 import select_impact


@pytest.fixture(autouse=True)
def _external_test_git_sandbox(tmp_path):
    # Disposable repositories are disjoint from the protected checkout.
    # Inherit its guard unchanged; the hook can independently verify this.
    root = Path(__file__).resolve().parents[1]
    assert root != tmp_path.resolve() and root not in tmp_path.resolve().parents


def mk(repo: Path, path: str, data: str = "# test\n"):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(data, encoding="utf-8")
    return f


def group(name, tests, paths, consumers=(), contracts=()):
    return TestGroup(name, tuple(tests), ("MT01.test",), tuple(paths),
                     tuple(consumers), tuple(contracts))


def fixture(tmp_path):
    a12 = "tests/test_a12_identity.py"
    a14 = "tests/test_a14_attribution.py"
    mk(tmp_path, "src/ai_commerce/product/identity.py")
    mk(tmp_path, "src/ai_commerce/affiliate_attribution/evidence.py")
    mk(tmp_path, a12, "from ai_commerce.product import identity\n")
    mk(tmp_path, a14)
    groups = (
        group("a12", (a12,), ("src/ai_commerce/product/identity.py",)),
        group("a14", (a14,), ("src/ai_commerce/affiliate_attribution/evidence.py",)),
    )
    return groups, a12, a14


def test_a12_narrow_unaffected_by_unrelated_new_e04_test(tmp_path):
    groups, a12, a14 = fixture(tmp_path)
    clean = select_impact(root=tmp_path, groups=groups,
                          changed_paths=["src/ai_commerce/product/identity.py"])
    assert clean.runnable and clean.selected_tests == (a12,)
    mk(tmp_path, "tests/test_a14_e04.py")
    polluted = select_impact(root=tmp_path, groups=groups,
                             changed_paths=["src/ai_commerce/product/identity.py"])
    assert polluted.mode == clean.mode
    assert polluted.selected_tests == clean.selected_tests
    assert "tests/test_a14_e04.py:UNRELATED_UNMAPPED_TEST" in polluted.inventory_warnings


def test_new_e04_test_is_held_but_always_in_selected_plan(tmp_path):
    groups, _, _ = fixture(tmp_path)
    mk(tmp_path, "tests/test_a14_e04.py")
    decision = select_impact(
        root=tmp_path, groups=groups,
        changed_paths=["tests/test_a14_e04.py", "src/ai_commerce/affiliate_attribution/evidence.py"])
    assert decision.mode == "HOLD_MAPPING"
    assert "tests/test_a14_e04.py" in decision.selected_tests
    assert "tests/test_a14_e04.py:TEST_REGISTRATION_REQUIRED" in decision.missing_mapping


def test_unknown_source_holds_instead_of_full(tmp_path):
    groups, _, _ = fixture(tmp_path)
    mk(tmp_path, "src/ai_commerce/affiliate_commerce/service.py")
    decision = select_impact(root=tmp_path, groups=groups,
                             changed_paths=["src/ai_commerce/affiliate_commerce/service.py"])
    assert decision.mode == "HOLD_MAPPING"
    assert any("SOURCE_OWNERSHIP_REQUIRED" in m for m in decision.missing_mapping)
    assert "tests" not in decision.selected_tests


def test_control_change_uses_reviewed_regressions(tmp_path):
    groups, _, _ = fixture(tmp_path)
    for t in ("tests/test_pr_merge_gate.py", "tests/test_minimum_test.py"):
        mk(tmp_path, t)
    decision = select_impact(root=tmp_path, groups=groups,
                             changed_paths=[".github/scripts/pr-merge-gate.py"])
    assert decision.runnable
    assert set(decision.selected_tests) == {
        "tests/test_pr_merge_gate.py", "tests/test_minimum_test.py"}


def test_missing_control_regression_holds(tmp_path):
    groups, _, _ = fixture(tmp_path)
    decision = select_impact(root=tmp_path, groups=groups,
                             changed_paths=[".github/scripts/pr-merge-gate.py"])
    assert decision.mode == "HOLD_MAPPING"
    assert "tests/test_pr_merge_gate.py:REQUIRED_TEST_MISSING_OR_UNSAFE" in decision.missing_mapping


def test_shared_collection_config_holds_not_full(tmp_path):
    groups, _, _ = fixture(tmp_path)
    decision = select_impact(root=tmp_path, groups=groups, changed_paths=["pyproject.toml"])
    assert decision.mode == "HOLD_COVERAGE"


def test_unrelated_dynamic_import_does_not_poison_a12(tmp_path):
    groups, a12, _ = fixture(tmp_path)
    mk(tmp_path, "src/ai_commerce/other/unknown.py", 'import importlib\nimportlib.import_module(name)\n')
    decision = select_impact(root=tmp_path, groups=groups,
                             changed_paths=["src/ai_commerce/product/identity.py"])
    assert decision.runnable
    assert a12 in decision.selected_tests


def test_related_dynamic_import_requires_coverage_proof(tmp_path):
    groups, _, _ = fixture(tmp_path)
    mk(tmp_path, "src/ai_commerce/product/identity.py", 'import importlib\nimportlib.import_module(name)\n')
    decision = select_impact(root=tmp_path, groups=groups,
                             changed_paths=["src/ai_commerce/product/identity.py"])
    assert decision.mode == "HOLD_COVERAGE"
    assert any(m.endswith(":UNRESOLVED_IMPORT") for m in decision.missing_mapping)


def test_101_selected_files_do_not_trigger_full(tmp_path):
    test_files = [f"tests/test_component_{i:03d}.py" for i in range(101)]
    for path in test_files:
        mk(tmp_path, path)
    src = "src/ai_commerce/component/service.py"
    mk(tmp_path, src)
    groups = (group("component", test_files, [src]),)
    decision = select_impact(root=tmp_path, groups=groups, changed_paths=[src])
    assert decision.runnable and len(decision.selected_tests) == 101


def test_docs_only_does_not_claim_pytest_pass(tmp_path):
    mk(tmp_path, "docs/engineering/REVIEW.md")
    decision = select_impact(root=tmp_path, groups=(), changed_paths=["docs/engineering/REVIEW.md"])
    assert decision.mode == "NO_PYTEST_APPLICABLE"
    assert not decision.selected_tests


def test_traversal_cannot_choose_tests(tmp_path):
    decision = select_impact(root=tmp_path, groups=(), changed_paths=["../secrets"])
    assert decision.mode == "HOLD_PROOF"


def test_exact_new_orphan_file_is_not_implicitly_exempt(tmp_path):
    groups, _, _ = fixture(tmp_path)
    mk(tmp_path, "tests/test_a14_e04.py")
    decision = select_impact(root=tmp_path, groups=groups, changed_paths=["tests/test_a14_e04.py"],
                             previous_tests=["tests/test_a14_e04.py"])
    assert decision.mode == "HOLD_MAPPING"
    assert any("TEST_REGISTRATION_REQUIRED" in p for p in decision.missing_mapping)


def test_ef02_v21_exact_registered_profile_and_affected_group(tmp_path):
    groups, a12, _ = fixture(tmp_path)
    files = file_hashes(tmp_path)
    command = ["python", "-m", "pytest", a12, "-q", "-n", "12"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files, changed_paths=["src/ai_commerce/product/identity.py"],
        required_acs=(), fallback_argv=command, groups=groups,
        registered_profiles={"mt01-a12-tests": command}, env={},
    )
    assert not plan["fallback"]
    assert plan["selected_groups"] == ["a12"]
    assert plan["groups"][0]["state"] == "EXECUTE"
    assert plan["groups"][0]["binding"]["argv"] == command


def test_ef02_v21_missing_profile_is_unproven_not_full(tmp_path):
    groups, a12, _ = fixture(tmp_path)
    files = file_hashes(tmp_path)
    command = ["python", "-m", "pytest", a12, "-q", "-n", "12"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files, changed_paths=["src/ai_commerce/product/identity.py"],
        required_acs=(), fallback_argv=command, groups=groups,
        registered_profiles={}, env={},
    )
    assert plan["fallback"] is False
    assert not any(g["execute"] for g in plan["groups"])
    assert "a12:REGISTERED_PROFILE_REQUIRED" in plan["reason_codes"]


def test_ef02_v21_preserves_authorized_narrow_profile(monkeypatch):
    from ai_commerce.remote.validation import approved_verification_plan
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    narrow = ["python", "-m", "pytest", "tests/test_a12_identity.py", "-q", "-n", "12"]
    registry = {
        "mt01-a12-tests": narrow,
        "project-tests": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
    }
    plan = approved_verification_plan(
        {"verification_profile": "mt01-a12-tests", "depends_on": []}, registry)
    assert plan["profile"] == "mt01-a12-tests"
    assert plan["argv"] == narrow
    assert plan["coverage"] == "V21_REQUIRES_POST_EXECUTOR_IMPACT_PROOF"


def test_v21_junit_errors_use_singular_receipt_counter():
    import importlib.util
    import sys
    script = Path(__file__).resolve().parents[1] / ".github/scripts/ef02-pr-ci-tests.py"
    spec = importlib.util.spec_from_file_location("mt01_ci_v21_audit", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    receipt = {"passed_count": 0, "skipped_count": 0,
               "failed_count": 0, "error_count": 0}
    module._add_junit_counts(receipt, {
        "passed": 11, "skipped": 2, "failed": 3, "errors": 4})
    assert receipt == {"passed_count": 11, "skipped_count": 2,
                       "failed_count": 3, "error_count": 4}


def test_v21_registered_full_packet_does_not_disarm_impact_planner(tmp_path, monkeypatch):
    from ai_commerce.engineering.minimum_test import select_plan
    groups, a12, _ = fixture(tmp_path)
    files = file_hashes(tmp_path)
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    broad = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    narrow = ["python", "-m", "pytest", a12, "-q", "-n", "12"]
    plan = select_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files, changed_paths=["src/ai_commerce/product/identity.py"],
        required_acs=(), fallback_argv=broad, groups=groups,
        registered_only=True,
        registered_profiles={"project-tests": broad, "mt01-a12-tests": narrow},
    )
    assert plan["version"] == 2
    assert not plan["fallback"]
    assert plan["selected_groups"] == ["a12"]
    assert plan["groups"][0]["binding"]["argv"] == narrow
    assert all("tests" not in e["binding"]["argv"][3:5] for e in plan["groups"])


def test_v21_unknown_acceptance_runs_tests_but_requires_brain_review(tmp_path):
    """Unregistered AC cannot claim coverage, but must not suppress mapped tests."""
    groups, a12, _ = fixture(tmp_path)
    files = file_hashes(tmp_path)
    narrow = ["python", "-m", "pytest", a12, "-q", "-n", "12"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files, changed_paths=["src/ai_commerce/product/identity.py"],
        required_acs=("UNKNOWN_AC",), fallback_argv=narrow, groups=groups,
        registered_profiles={"mt01-a12-tests": narrow}, env={},
    )
    assert plan["unknown_acs"] == plan["pending_brain_acs"] == ["UNKNOWN_AC"]
    assert plan["ac_adoption_state"] == "PENDING_BRAIN_REVIEW"
    assert "AC_PENDING_BRAIN_REVIEW:UNKNOWN_AC" in plan["reason_codes"]
    assert plan["groups"][0]["state"] == "EXECUTE"
    assert plan["groups"][0]["execute"] is True
    assert "UNKNOWN_AC" not in plan["groups"][0]["acs"]


def test_v21_unknown_acceptance_does_not_waive_unmapped_source(tmp_path):
    groups, a12, _ = fixture(tmp_path)
    unknown_source = "src/ai_commerce/other/new_component.py"
    mk(tmp_path, unknown_source)
    files = file_hashes(tmp_path)
    narrow = ["python", "-m", "pytest", a12, "-q", "-n", "12"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files, changed_paths=[unknown_source],
        required_acs=("UNKNOWN_AC",), fallback_argv=narrow, groups=groups,
        registered_profiles={"mt01-a12-tests": narrow}, env={},
    )
    assert plan["pending_brain_acs"] == ["UNKNOWN_AC"]
    assert all(not g["execute"] for g in plan["groups"])
    assert plan["impact_v21"]["mode"] == "HOLD_MAPPING"


def test_v21_postmerge_disables_unproven_reuse_without_full(monkeypatch, tmp_path):
    import importlib.util
    import sys
    script = Path(__file__).resolve().parents[1] / ".github/scripts/ef02-pr-ci-tests.py"
    spec = importlib.util.spec_from_file_location("mt01_v21_postmerge_guard", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    monkeypatch.setenv("MT01_CI_EVENT", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    def should_not_reuse(**kwargs):
        raise AssertionError("V2 postmerge must not trust old V1 selector")
    monkeypatch.setattr(module, "postmerge_reuse", should_not_reuse)
    assert module.postmerge_preflight() == 0
    assert "skip_pytest=false" in output.read_text()


def test_required_ac_on_unselected_registered_component_holds(tmp_path):
    """Regression #229: global AC registration is not selected test coverage."""
    groups, a12, a14 = fixture(tmp_path)
    # The original fixture groups share a test-only generic AC; bind distinct
    # accepted AC identities to expose cross-component leakage.
    groups = (
        TestGroup("a12", (a12,), ("A12.product-evidence",),
                  ("src/ai_commerce/product/identity.py",)),
        TestGroup("a14", (a14,), ("A14.L04.offline-attribution-evidence",),
                  ("src/ai_commerce/affiliate_attribution/evidence.py",)),
    )
    files = file_hashes(tmp_path)
    fallback = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    profiles = {
        "mt01-a12-tests": ["python", "-m", "pytest", a12, "-q", "-n", "12"],
        "mt01-a14-tests": ["python", "-m", "pytest", a14, "-q", "-n", "12"],
    }
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files,
        changed_paths=["src/ai_commerce/affiliate_attribution/evidence.py"],
        required_acs=("A12.product-evidence",), fallback_argv=fallback,
        groups=groups, registered_profiles=profiles, env={},
    )
    assert plan["selected_groups"] == ["a14"]
    assert not plan["unknown_acs"]
    assert "AC_REQUIRED_GROUP_NOT_SELECTED:A12.product-evidence" in plan["reason_codes"]
    assert all(not g["execute"] and g["state"] == "UNPROVEN"
               for g in plan["groups"])
    assert plan["impact_v21"]["mode"] == "HOLD_COVERAGE"


def test_required_ac_covered_by_selected_component_is_proven(tmp_path):
    """Positive: the required AC's own group is selected and registered."""
    groups, a12, a14 = fixture(tmp_path)
    groups = (
        TestGroup("a12", (a12,), ("A12.product-evidence",),
                  ("src/ai_commerce/product/identity.py",)),
        TestGroup("a14", (a14,), ("A14.L04.offline-attribution-evidence",),
                  ("src/ai_commerce/affiliate_attribution/evidence.py",)),
    )
    files = file_hashes(tmp_path)
    narrow = ["python", "-m", "pytest", a12, "-q", "-n", "12"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files,
        changed_paths=["src/ai_commerce/product/identity.py"],
        required_acs=("A12.product-evidence",), fallback_argv=narrow,
        groups=groups, registered_profiles={"mt01-a12-tests": narrow}, env={},
    )
    assert plan["selected_groups"] == ["a12"]
    assert not any(code.startswith("AC_") for code in plan["reason_codes"])
    assert plan["groups"][0]["state"] == "EXECUTE"

def test_required_cross_component_ac_with_both_groups_selected(tmp_path):
    """Positive fan-in: A12 required AC is covered by an actually selected A12."""
    groups, a12, a14 = fixture(tmp_path)
    groups = (
        TestGroup("a12", (a12,), ("A12.product-evidence",),
                  ("src/ai_commerce/product/identity.py",)),
        TestGroup("a14", (a14,), ("A14.L04.offline-attribution-evidence",),
                  ("src/ai_commerce/affiliate_attribution/evidence.py",)),
    )
    files = file_hashes(tmp_path)
    broad = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    profiles = {
        "mt01-a12-tests": ["python", "-m", "pytest", a12, "-q", "-n", "12"],
        "mt01-a14-tests": ["python", "-m", "pytest", a14, "-q", "-n", "12"],
    }
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files,
        changed_paths=["src/ai_commerce/product/identity.py",
                       "src/ai_commerce/affiliate_attribution/evidence.py"],
        required_acs=("A12.product-evidence",), fallback_argv=broad,
        groups=groups, registered_profiles=profiles, env={},
    )
    assert plan["selected_groups"] == ["a12", "a14"]
    assert not any(code.startswith("AC_") for code in plan["reason_codes"])
    assert all(g["state"] == "EXECUTE" for g in plan["groups"])



def test_independent_baseline_oracle_rejects_self_selected_omissions(tmp_path):
    import importlib.util
    import sys
    script = Path(__file__).resolve().parents[1] / ".github/scripts/mt01_v21_oracle.py"
    spec = importlib.util.spec_from_file_location("mt01_impact_independent_oracle", script)
    oracle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = oracle
    spec.loader.exec_module(oracle)
    groups, a12, a14 = fixture(tmp_path)
    src_a12 = "src/ai_commerce/product/identity.py"
    selected = {"version": 2, "changed_paths": [src_a12],
                "selected_tests": [a14], "selected_groups": ["a14"]}
    present = {a12, a14}
    ok, reasons = oracle.check_selection(
        selected, base_groups=groups, candidate_groups=groups,
        changed_paths={src_a12}, present_paths=present)
    assert not ok
    assert "REQUIRED_GROUP_OMITTED:a12" in reasons
    assert "REQUIRED_TEST_OMITTED:" + a12 in reasons
    # Even self-consistent JUnit receipts cannot authorize an omitted group.
    selected.update(selected_tests=[a12, a14], selected_groups=["a12", "a14"])
    ok, reasons = oracle.check_selection(
        selected, base_groups=groups, candidate_groups=groups,
        changed_paths={src_a12}, present_paths=present)
    assert ok, reasons


def test_independent_baseline_oracle_preserves_prior_owner_after_group_removal(tmp_path):
    import importlib.util
    import sys
    script = Path(__file__).resolve().parents[1] / ".github/scripts/mt01_v21_oracle.py"
    spec = importlib.util.spec_from_file_location("mt01_baseline_ownership_oracle", script)
    oracle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = oracle
    spec.loader.exec_module(oracle)
    groups, a12, a14 = fixture(tmp_path)
    src_a12 = "src/ai_commerce/product/identity.py"
    # Candidate removes an owner. A base-trusted registry must still demand it.
    selected = {"version": 2, "changed_paths": [src_a12],
                "selected_tests": [a14], "selected_groups": ["a14"]}
    ok, reasons = oracle.check_selection(
        selected, base_groups=groups, candidate_groups=(groups[1],),
        changed_paths={src_a12}, present_paths={a12, a14})
    assert not ok and "REQUIRED_TEST_OMITTED:" + a12 in reasons



def test_oracle_base_resolution_restricts_push_to_main(monkeypatch, tmp_path):
    import importlib.util
    import sys
    script = Path(__file__).resolve().parents[1] / ".github/scripts/mt01_v21_oracle.py"
    spec = importlib.util.spec_from_file_location("mt01_base_event_guard", script)
    oracle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = oracle
    spec.loader.exec_module(oracle)
    # A missing base on arbitrary events must not be inferred from HEAD^.
    monkeypatch.delenv("MT01_CI_EVENT", raising=False)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    monkeypatch.setattr(sys, "argv", ["oracle", "--repo", str(tmp_path),
                                       "--plan", str(tmp_path / "plan.json")])
    assert oracle.main() == 3



def test_v21_only_reviewed_node_files_skip_python_with_real_node_step_obligation(tmp_path):
    from ai_commerce.engineering.impact_v21 import select_impact
    for p in (".github/scripts/ef02-watch.cjs", "tests/ef02-watch.test.cjs"):
        mk(tmp_path, p, "module.exports = {};\n")
    plan = select_impact(
        root=tmp_path,
        changed_paths=[".github/scripts/ef02-watch.cjs", "tests/ef02-watch.test.cjs"],
        groups=())
    assert plan.mode == "NO_PYTEST_APPLICABLE"
    assert "REVIEWED_NODE_TEST_STEP_REQUIRED" in plan.reason_codes
    assert not plan.selected_tests
    assert not plan.missing_mapping


def test_v21_unknown_node_control_and_powershell_hold_without_full(tmp_path):
    from ai_commerce.engineering.impact_v21 import select_impact
    for path in (".github/scripts/new-service.cjs", ".github/scripts/unsafe.ps1"):
        mk(tmp_path, path, "unreviewed")
        result = select_impact(root=tmp_path, changed_paths=[path], groups=())
        assert result.mode == "HOLD_MAPPING"
        assert result.selected_tests == ()
        assert path + ":UNREVIEWED_CI_CONTROL" in result.missing_mapping


def test_v21_mixed_node_and_python_keeps_python_consumers(tmp_path):
    from ai_commerce.engineering.impact_v21 import select_impact
    groups, a12, _ = fixture(tmp_path)
    mk(tmp_path, ".github/scripts/ef02-watch.cjs", "module.exports = {};\n")
    result = select_impact(
        root=tmp_path,
        changed_paths=["src/ai_commerce/product/identity.py", ".github/scripts/ef02-watch.cjs"],
        groups=groups)
    assert result.runnable and a12 in result.selected_tests
    assert "REVIEWED_NODE_TEST_STEP_REQUIRED" in result.reason_codes


# OFFLINE E2E: real local git and pytest; the executor is a deterministic file
# writer, and GitHub candidate publication is deliberately NOT simulated as live.
def _v21_make_git_sandbox(tmp_path):
    import subprocess

    root = tmp_path / "ef02-local-git"
    root.mkdir()
    alpha = "src/ai_commerce/product/alpha.py"
    beta = "src/ai_commerce/product/beta.py"
    mk(root, "src/ai_commerce/__init__.py", "")
    mk(root, "src/ai_commerce/product/__init__.py", "")
    mk(root, alpha, "def value():\n    return 0\n")
    mk(root, beta, "def value():\n    return 0\n")
    mk(root, "tests/test_alpha.py",
       "from ai_commerce.product.alpha import value\n"
       "def test_alpha():\n    assert value() == 1\n")
    mk(root, "tests/test_beta.py",
       "from ai_commerce.product.beta import value\n"
       "def test_beta():\n    assert value() == 2\n")
    mk(root, "tests/test_aggregate.py",
       "from ai_commerce.product.alpha import value as a\n"
       "from ai_commerce.product.beta import value as b\n"
       "def test_aggregate():\n    assert b() in (0, 2)\n    if b() == 2:\n        assert a() == 1\n")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args],
                                       stderr=subprocess.STDOUT).decode().strip()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=MT01 Test", "-c", "user.email=mt01@example.invalid",
        "commit", "-qm", "fixture baseline")
    return root, git, git("rev-parse", "HEAD")


def test_v21_offline_ef02_two_action_fanin_real_git_pytest_verify_fix(tmp_path, monkeypatch):
    """Actual diff -> MT-01 -> real pytest -> bounded fix -> local candidate SHA.

    This proves local integration behavior only. No LocalBatchWorker GitHub
    checkpoint, provider API, remote Windows runner or published EF02 candidate.
    """
    import os
    import subprocess
    import sys
    from ai_commerce.remote.scheduler import run_schedule
    from ai_commerce.engineering.minimum_test import select_plan, file_hashes
    from ai_commerce.engineering.runner import _outside_scope

    root, git, source = _v21_make_git_sandbox(tmp_path)
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    alpha = "src/ai_commerce/product/alpha.py"
    beta = "src/ai_commerce/product/beta.py"
    groups = (
        group("alpha", ("tests/test_alpha.py",), (alpha,)),
        group("beta", ("tests/test_beta.py",), (beta,)),
        group("aggregate", ("tests/test_aggregate.py",), (alpha, beta)),
    )
    # The existing group() fixture uses the common AC label MT01.test.
    # Keep distinct ACs, as they would be in a reviewed registered batch.
    from ai_commerce.engineering.minimum_test import TestGroup
    groups = tuple(TestGroup(g.name, g.tests, ("AC." + g.name,),
                             g.paths, g.consumers, g.contracts) for g in groups)
    profiles = {
        "local-" + g.name: ["python", "-m", "pytest", *g.tests, "-q"]
        for g in groups
    }
    broad = ["python", "-m", "pytest", "tests", "-q"]
    profiles["project-tests"] = broad
    source_files = file_hashes(root)
    trace = []

    def work(action):
        aid = action["action_id"]
        parents = action.get("resolved_dependencies", [])
        assert git("rev-parse", "HEAD") == (parents[-1]["candidate_sha"] if parents else source)
        path = alpha if aid == "a" else beta
        # The executor substitute really edits a tracked file.
        mk(root, path, "def value():\n    return " + ("1" if aid == "a" else "99") + "\n")
        changed = git("diff", "--name-only", source).splitlines()
        required = ("AC.alpha",) if aid == "a" else ("AC.beta",)
        plan = select_plan(
            repo=root, base_sha=source, base_files=source_files,
            candidate_files=file_hashes(root), changed_paths=changed,
            required_acs=required, fallback_argv=broad,
            groups=groups, registered_only=True, registered_profiles=profiles,
            aggregate_sources=tuple(p["candidate_sha"] for p in parents),
        )
        assert plan["version"] == 2 and not plan["fallback"]
        assert all(x["state"] == "EXECUTE" for x in plan["groups"])
        assert set(plan["selected_groups"]) >= ({"alpha", "aggregate"} if aid == "a"
                                                else {"alpha", "beta", "aggregate"})
        assert all(x["binding"]["argv"] != broad for x in plan["groups"])
        assert not _outside_scope([path], action["allowed_paths"])
        selected = sorted({p for g in plan["groups"] for p in g["binding"]["mapping"]["tests"]})
        assert selected
        def verify():
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("GIT_CONFIG_") and k not in (
                       "AI_COMMERCE_GIT_GUARD_PATHS", "PYTEST_CURRENT_TEST", "PYTEST_PLUGINS"
                   )}
            env["PYTHONPATH"] = str(root / "src")
            return subprocess.run([sys.executable, "-m", "pytest", *selected,
                                   "-q", "-p", "no:cacheprovider"],
                                  cwd=root, capture_output=True, text=True,
                                  timeout=90, check=False, env=env)
        first = verify()
        if aid == "a":
            assert first.returncode == 0, first.stdout + first.stderr
            attempts = [first.returncode]
        else:
            assert first.returncode != 0 and "failed" in first.stdout.lower()
            # Deterministic Verify-Fix: change the *cause*, rerun exact selection.
            mk(root, beta, "def value():\n    return 2\n")
            second = verify()
            assert second.returncode == 0, second.stdout + second.stderr
            attempts = [first.returncode, second.returncode]
        git("add", path)
        git("-c", "user.name=MT01 Test", "-c", "user.email=mt01@example.invalid",
            "commit", "-qm", "offline fake executor " + aid)
        candidate = git("rev-parse", "HEAD")
        trace.append({"action_id": aid, "effective_source_sha": parents[-1]["candidate_sha"]
                      if parents else source, "candidate_sha": candidate,
                      "attempt_exits": attempts, "selected_groups": plan["selected_groups"],
                      "selected_tests": selected, "aggregate_sources": plan["aggregate_sources"]})
        return {"status": "CANDIDATE_READY", "candidate_sha": candidate,
                "scope_valid": True, "review_required": False,
                "out_of_scope_files": []}

    actions = [
        {"action_id": "a", "attempt_id": "offline-a-001", "depends_on": [],
         "allowed_paths": [alpha], "resources": []},
        {"action_id": "b", "attempt_id": "offline-b-001", "depends_on": ["a"],
         "allowed_paths": [beta], "resources": []},
    ]
    result = run_schedule({"batch_id": "mt01-v21-offline-fanin",
                           "source_sha": source, "max_parallel": 1,
                           "actions": actions}, work, max_workers=1)
    assert result["status"] == "COMPLETE", result
    assert result["succeeded"] == 2
    assert len(trace) == 2
    assert trace[0]["attempt_exits"] == [0]
    assert trace[1]["attempt_exits"][0] != 0
    assert trace[1]["attempt_exits"][1] == 0
    assert trace[1]["aggregate_sources"] == [trace[0]["candidate_sha"]]
    assert trace[0]["candidate_sha"] != trace[1]["candidate_sha"] != source
    assert git("rev-parse", "HEAD") == trace[1]["candidate_sha"]


def test_v21_offline_ef02_scope_review_blocks_downstream_without_provider(tmp_path):
    """Never promote an out-of-scope file into a schedulable PASS."""
    from ai_commerce.remote.scheduler import run_schedule
    from ai_commerce.engineering.runner import _outside_scope
    root, git, source = _v21_make_git_sandbox(tmp_path)
    attempted = []
    actions = [
        {"action_id": "a", "attempt_id": "offline-scope-001",
         "depends_on": [], "resources": [],
         "allowed_paths": ["src/ai_commerce/product/alpha.py"]},
        {"action_id": "b", "attempt_id": "offline-dependent-001",
         "depends_on": ["a"], "resources": [],
         "allowed_paths": ["src/ai_commerce/product/beta.py"]},
    ]
    def work(action):
        attempted.append(action["action_id"])
        assert action["action_id"] == "a"
        bad = "src/ai_commerce/product/beta.py"
        mk(root, bad, "def value():\n    return 9\n")
        changed = git("diff", "--name-only", source).splitlines()
        outside = _outside_scope(changed, action["allowed_paths"])
        assert outside == [bad]
        return {"status": "CANDIDATE_READY_SCOPE_REVIEW",
                "scope_valid": False, "review_required": True,
                "out_of_scope_files": outside, "candidate_sha": None}
    result = run_schedule({"batch_id": "mt01-v21-offline-scope",
                           "source_sha": source, "max_parallel": 1,
                           "actions": actions}, work, max_workers=1)
    assert attempted == ["a"]
    assert result["status"] == "PARTIAL_OR_FAILED"
    assert result["actions"][0]["status"] == "CANDIDATE_READY_SCOPE_REVIEW"
    assert result["actions"][1]["status"] == "BLOCKED_DEPENDENCY"



def test_oracle_postmerge_resolves_real_github_push_context_only(monkeypatch, tmp_path):
    import importlib.util
    import sys
    path = Path(__file__).resolve().parents[1] / ".github/scripts/mt01_v21_oracle.py"
    spec = importlib.util.spec_from_file_location("mt01_oracle_github_push_regression", path)
    oracle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = oracle
    spec.loader.exec_module(oracle)
    calls = []

    def git(root, *argv):
        calls.append(tuple(argv))
        assert tuple(argv) == ("rev-parse", "HEAD^")
        return (b"a" * 40) + b"\n"

    monkeypatch.setattr(oracle, "_git", git)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    assert oracle.resolve_base(tmp_path, "") == "a" * 40
    assert calls == [("rev-parse", "HEAD^")]
    assert oracle.resolve_base(tmp_path, "b" * 40) == "b" * 40
    assert len(calls) == 1
    monkeypatch.setenv("GITHUB_REF", "refs/heads/other")
    assert oracle.resolve_base(tmp_path, "") == ""
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert oracle.resolve_base(tmp_path, "") == ""
    assert len(calls) == 1



def test_postmerge_v21_workflow_runs_on_every_main_push():
    # A changed-only path filter can silently suppress the postmerge baseline
    # oracle after a PR changes an unlisted control or introduces a new owner.
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/ef02-tests.yml").read_text(encoding="utf-8")
    push = workflow.split("  push:\n", 1)[1].split("  pull_request:\n", 1)[0]
    assert "branches: [ef02-multi-action-implementation, main]" in push
    assert "paths:" not in push



def test_hostops_v3_hosted_queue_is_reviewed_control_not_generic_ci_escape(tmp_path):
    """Permit only the pinned queue YAML and demand exact independent test closure."""
    groups, _, _ = fixture(tmp_path)
    expected = (
        "tests/test_hostops_v3_integration.py",
        "tests/test_pr_merge_gate.py",
        "tests/test_mt01_v21.py",
    )
    for path in expected:
        mk(tmp_path, path)
    decision = select_impact(
        root=tmp_path, groups=groups,
        changed_paths=[".github/workflows/hostops-v3.yml"],
    )
    assert decision.runnable, decision
    assert not decision.missing_mapping
    assert set(decision.selected_tests) == set(expected)
    denied = select_impact(
        root=tmp_path, groups=groups,
        changed_paths=[".github/workflows/unknown-host-ops.yml"],
    )
    assert denied.mode == "HOLD_MAPPING"
    assert ".github/workflows/unknown-host-ops.yml:UNREVIEWED_CI_CONTROL" in denied.missing_mapping


def _v22_manifest_fixture(tmp_path):
    import json
    from ai_commerce.engineering.impact_v21 import validate_component_onboarding_manifest
    old_source = "src/ai_commerce/learning/export.py"
    old_test = "tests/test_prior.py"
    new_source = "src/ai_commerce/fresh/svc.py"
    new_test = "tests/test_fresh_svc.py"
    contract = "contracts/FreshRun.v1.schema.json"
    manifest = "work_items/mt01-components/fresh.json"
    for path in (old_source, old_test, new_source, new_test, contract):
        mk(tmp_path, path)
    values = {
        "schema_version": "MT01ComponentOwner.v1",
        "component": "fresh",
        "sources": [new_source],
        "tests": [new_test],
        "acs": ["FRESH.offline"],
        "consumers": [old_source],
        "contracts": [contract],
    }
    mk(tmp_path, manifest, json.dumps(values))
    change = (new_source, new_test, contract, manifest)
    original = (group("prior", [old_test], [old_source]),)
    context = {
        "manifest_path": manifest,
        "changed_paths": change,
        "base_paths": (old_source, old_test),
        "existing_groups": original,
        "allowed_paths": change,
        "root": tmp_path,
    }
    return validate_component_onboarding_manifest, values, context


def test_v22_manifest_data_only_admission(tmp_path):
    validator, values, context = _v22_manifest_fixture(tmp_path)
    admission = validator(values, **context)
    assert admission["name"] == "fresh"
    assert admission["tests"] == ("tests/test_fresh_svc.py",)
    assert admission["acs"] == ("FRESH.offline",)
    assert len(admission["manifest_sha256"]) == 64
    assert admission["status"] == "VALIDATED_DATA_ONLY_NOT_VERIFICATION_PASS"


def test_v22_manifest_rejects_owner_theft_and_unsafe_changes(tmp_path):
    import copy
    import pytest
    validator, original, ctx = _v22_manifest_fixture(tmp_path)
    for mutate, keyword, code in (
        (lambda p: p.update(component="prior"), {}, "ONBOARD_MANIFEST_LOCATION"),
        (lambda p: p.update(tests=["tests/test_prior.py"]), {}, "ONBOARD_REOWN_OR_NOT_ADDED"),
        (lambda p: p.update(sources=["src/ai_commerce/engineering/worker.py"]), {}, "ONBOARD_UNSAFE_SOURCE_FAMILY"),
        (lambda p: p.update(tests=["tests/../../secrets.py"]), {}, "ONBOARD_UNSAFE_PATH"),
        (lambda p: p.update(acs=["FRESH.offline", "FRESH.offline"]), {}, "ONBOARD_INVALID_ACS"),
        (lambda p: p.update(consumers=["src/ai_commerce/other/hidden.py"]), {}, "ONBOARD_CONSUMER_UNPROVEN"),
        (lambda p: p.update(sources=["src/ai_commerce/fresh/svc.py"]*2), {}, "ONBOARD_INVALID_SOURCES"),
        (lambda p: p.update(argv=["python", "-m", "pytest"]), {}, "ONBOARD_MANIFEST_SHAPE"),
        (lambda p: p.update(schema_version="v999"), {}, "ONBOARD_SCHEMA_VERSION"),
        (lambda p: None, {"allowed_paths": ["src/ai_commerce/fresh/svc.py"]}, "ONBOARD_MANIFEST_NOT_NEW_AND_AUTHORIZED"),
        (lambda p: None, {"base_paths": list(ctx["base_paths"]) + ["tests/test_fresh_svc.py"]}, "ONBOARD_REOWN_OR_NOT_ADDED"),
    ):
        candidate = copy.deepcopy(original)
        mutate(candidate)
        args = dict(ctx)
        args.update(keyword)
        with pytest.raises(ValueError, match=code):
            validator(candidate, **args)


def test_v22_manifest_rejects_missing_or_linked_source(tmp_path, monkeypatch):
    import pytest
    from pathlib import Path
    validator, values, ctx = _v22_manifest_fixture(tmp_path)
    source = tmp_path / values["sources"][0]
    source.unlink()
    with pytest.raises(ValueError, match="ONBOARD_FILE_MISSING_OR_LINK"):
        validator(values, **ctx)
    try:
        source.symlink_to(tmp_path / "tests/test_prior.py")
        with pytest.raises(ValueError, match="ONBOARD_FILE_MISSING_OR_LINK"):
            validator(values, **ctx)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:
            raise
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda p: p == source or original(p))
        with pytest.raises(ValueError, match="ONBOARD_FILE_MISSING_OR_LINK"):
            validator(values, **ctx)


def test_v22_manifest_binding_rejects_untrusted_in_memory_owner(tmp_path):
    import pytest
    validator, values, ctx = _v22_manifest_fixture(tmp_path)
    path = tmp_path / ctx["manifest_path"]
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="ONBOARD_MANIFEST_BYTES_MISMATCH"):
        validator(values, **ctx)


def test_v22_registered_action_onboards_new_component_without_companion_pr(tmp_path):
    from ai_commerce.engineering.minimum_test import file_hashes
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    validator, values, ctx = _v22_manifest_fixture(tmp_path)
    profile = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    result = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40,
        base_files={p: "baseline" for p in ctx["base_paths"]},
        candidate_files=file_hashes(tmp_path),
        changed_paths=ctx["changed_paths"],
        required_acs=values["acs"], fallback_argv=profile,
        groups=ctx["existing_groups"],
        registered_profiles={
            "mt01-component-onboarding": profile,
            "mt01-prior-tests": ["python", "-m", "pytest",
                                "tests/test_prior.py", "-q", "-n", "12"]},
        allowed_paths=ctx["allowed_paths"])
    assert result["selected_groups"] == ["fresh", "prior"]
    assert result["unknown_acs"] == []
    assert "ONBOARD_CONSUMER_OWNER_CLOSURE" in result["reason_codes"]
    assert all(g["execute"] for g in result["groups"])
    selected = [g for g in result["groups"] if g["group"] == "fresh"]
    assert len(selected) == 1 and selected[0]["execute"]
    binding = selected[0]["binding"]
    assert binding["registered_profile"] == "mt01-component-onboarding"
    assert binding["argv"] == ["python", "-m", "pytest", "tests/test_fresh_svc.py", "-q", "-n", "12"]
    assert len(binding["onboarding_manifest_sha256"]) == 64


def test_v22_onboarding_without_manifest_never_passes(tmp_path):
    from ai_commerce.engineering.minimum_test import file_hashes
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    _, values, ctx = _v22_manifest_fixture(tmp_path)
    (tmp_path / ctx["manifest_path"]).unlink()
    paths = tuple(x for x in ctx["changed_paths"] if x != ctx["manifest_path"])
    profile = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="b" * 40,
        base_files={p: "baseline" for p in ctx["base_paths"]},
        candidate_files=file_hashes(tmp_path), changed_paths=paths,
        required_acs=values["acs"], fallback_argv=profile,
        groups=ctx["existing_groups"],
        registered_profiles={"mt01-component-onboarding": profile},
        allowed_paths=ctx["allowed_paths"])
    assert any("ONBOARD_EXACTLY_ONE_NEW_MANIFEST_REQUIRED" in x for x in plan["reason_codes"])
    assert all(not entry["execute"] for entry in plan["groups"])


def test_v22_independent_baseline_oracle_requires_new_owner_tests(tmp_path):
    import runpy
    oracle = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                        ".github/scripts/mt01_v21_oracle.py"))
    _, values, ctx = _v22_manifest_fixture(tmp_path)
    new_groups = oracle["independently_reviewed_manifest_groups"](
        repo=tmp_path, changed_paths=set(ctx["changed_paths"]),
        base_paths=set(ctx["base_paths"]),
        base_groups=ctx["existing_groups"])
    assert len(new_groups) == 1 and new_groups[0].name == "fresh"
    present = set(ctx["changed_paths"]) | set(ctx["base_paths"])
    plan = {
        "version": 2, "changed_paths": sorted(ctx["changed_paths"]),
        "selected_groups": ["fresh", "prior"],
        "selected_tests": ["tests/test_fresh_svc.py", "tests/test_prior.py"],
    }
    good, reasons = oracle["check_selection"](
        plan, base_groups=ctx["existing_groups"],
        candidate_groups=new_groups, changed_paths=set(ctx["changed_paths"]),
        present_paths=present)
    assert good, reasons
    plan["selected_tests"] = ["tests/test_fresh_svc.py"]
    good, reasons = oracle["check_selection"](
        plan, base_groups=ctx["existing_groups"],
        candidate_groups=new_groups, changed_paths=set(ctx["changed_paths"]),
        present_paths=present)
    assert not good
    assert "REQUIRED_TEST_OMITTED:tests/test_prior.py" in reasons


def test_v22_independent_baseline_oracle_rejects_manifest_argv(tmp_path):
    import json
    import runpy
    import pytest
    oracle = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                        ".github/scripts/mt01_v21_oracle.py"))
    _, values, ctx = _v22_manifest_fixture(tmp_path)
    values["argv"] = ["sh", "-c", "true"]
    (tmp_path / ctx["manifest_path"]).write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError, match="ONBOARD_MANIFEST_UNTRUSTED_FIELDS"):
        oracle["independently_reviewed_manifest_groups"](
            repo=tmp_path, changed_paths=set(ctx["changed_paths"]),
            base_paths=set(ctx["base_paths"]),
            base_groups=ctx["existing_groups"])


def test_v22_ef02_replays_dynamic_argv_against_immutable_git_baseline(tmp_path, monkeypatch):
    """No provider invocation: actual Git baseline + receipt binding replay."""
    import subprocess
    import copy
    from ai_commerce.engineering import minimum_test as mt
    from ai_commerce.engineering.impact_v21 import (
        registered_action_plan, verified_onboarding_binding)
    old_src = "src/ai_commerce/learning/export.py"
    old_test = "tests/test_prior.py"
    mk(tmp_path, old_src)
    mk(tmp_path, old_test)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, stderr=subprocess.DEVNULL).decode().strip()
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git("-c", "user.name=MT01", "-c", "user.email=mt01@example.invalid", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    baseline_files = mt.file_hashes(tmp_path)
    _, data, ctx = _v22_manifest_fixture(tmp_path)
    monkeypatch.setattr(mt, "approved_groups", lambda: ctx["existing_groups"])
    ingress = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {
        "mt01-component-onboarding": ingress,
        "mt01-prior-tests": ["python", "-m", "pytest",
                            "tests/test_prior.py", "-q", "-n", "12"]}
    plan = registered_action_plan(
        repo=tmp_path, base_sha=base, base_files=baseline_files,
        candidate_files=mt.file_hashes(tmp_path),
        changed_paths=ctx["changed_paths"],
        required_acs=data["acs"], fallback_argv=ingress,
        groups=ctx["existing_groups"], registered_profiles=profiles,
        allowed_paths=ctx["allowed_paths"])
    binding = next(g["binding"] for g in plan["groups"] if g["group"] == "fresh")
    kwargs = dict(workspace=tmp_path, binding=binding,
                  changed_paths=ctx["changed_paths"],
                  allowed_paths=ctx["allowed_paths"], base_sha=base,
                  registered_profiles=profiles, expected_group="fresh")
    assert verified_onboarding_binding(**kwargs)
    assert not verified_onboarding_binding(**{**kwargs, "expected_group": "prior"})
    copied = copy.deepcopy(binding)
    copied["argv"] = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    assert not verified_onboarding_binding(**{**kwargs, "binding": copied})
    assert not verified_onboarding_binding(**{**kwargs,
           "allowed_paths": ("tests/test_prior.py",)})


def test_v22_existing_owner_binding_replays_with_new_owner_on_same_candidate(tmp_path, monkeypatch):
    """Regression #276: persisted A17.2 and new A17.3 share one proof graph.

    EF01 executed both groups. EF02 must replay selected_impact_tests with
    the newly validated candidate owner even for the persisted owner, without
    trusting candidate-supplied test commands or weakening SHA/ACL gates.
    """
    import copy
    import json
    import subprocess
    from ai_commerce.engineering import minimum_test as mt
    from ai_commerce.engineering.impact_v21 import (
        registered_action_plan, verified_onboarding_binding)

    _, old_manifest, ctx = _v22_manifest_fixture(tmp_path)
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=tmp_path, stderr=subprocess.DEVNULL).decode().strip()
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git("-c", "user.name=MT01", "-c", "user.email=mt01@example.invalid",
        "commit", "-qm", "main with persisted first owner")
    base_sha = git("rev-parse", "HEAD")
    base_files = mt.file_hashes(tmp_path)
    monkeypatch.setattr(mt, "approved_groups", lambda: ctx["existing_groups"])

    new_source = "src/ai_commerce/newer/service.py"
    new_test = "tests/test_newer.py"
    new_contract = "contracts/Newer.v1.schema.json"
    new_path = "work_items/mt01-components/newer.json"
    new_manifest = {
        "schema_version": "MT01ComponentOwner.v1", "component": "newer",
        "sources": [new_source], "tests": [new_test],
        "acs": ["NEWER.offline"], "consumers": old_manifest["sources"],
        "contracts": [new_contract],
    }
    for path in (new_source, new_test, new_contract):
        mk(tmp_path, path)
    mk(tmp_path, new_path, json.dumps(new_manifest))
    changed = [new_source, new_test, new_contract, new_path]
    ingress = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {"mt01-component-onboarding": ingress,
                "mt01-prior-tests": ["python", "-m", "pytest",
                                   "tests/test_prior.py", "-q", "-n", "12"]}
    plan = registered_action_plan(
        repo=tmp_path, base_sha=base_sha, base_files=base_files,
        candidate_files=mt.file_hashes(tmp_path), changed_paths=changed,
        required_acs=new_manifest["acs"], fallback_argv=ingress,
        groups=ctx["existing_groups"], registered_profiles=profiles,
        allowed_paths=changed)
    assert plan["impact_v21"]["mode"] in ("TARGETED", "AFFECTED")
    assert set(plan["selected_groups"]) == {"fresh", "newer"}
    assert all(entry["state"] == "EXECUTE" for entry in plan["groups"])
    persisted = next(entry["binding"] for entry in plan["groups"]
                     if entry["group"] == "fresh")
    novel = next(entry["binding"] for entry in plan["groups"]
                 if entry["group"] == "newer")
    kw = dict(workspace=tmp_path, changed_paths=changed,
              allowed_paths=changed, base_sha=base_sha,
              registered_profiles=profiles)
    assert verified_onboarding_binding(
        **kw, binding=persisted, expected_group="fresh")
    assert verified_onboarding_binding(
        **kw, binding=novel, expected_group="newer")

    # No accidental test omission, wrong owner or wrong source hash may pass.
    modified = copy.deepcopy(persisted)
    modified["selected_impact_tests"] = [new_test]
    assert not verified_onboarding_binding(
        **kw, binding=modified, expected_group="fresh")
    assert not verified_onboarding_binding(
        **kw, binding=persisted, expected_group="newer")
    assert not verified_onboarding_binding(
        **{**kw, "allowed_paths": [new_source]}, binding=persisted,
        expected_group="fresh")
    # An altered new-owner declaration invalidates the persisted owner's
    # original receipt, even though its committed manifest itself is intact.
    (tmp_path / new_path).write_text('{"untrusted":true}', encoding="utf-8")
    assert not verified_onboarding_binding(
        **kw, binding=persisted, expected_group="fresh")


def test_v22_ef02_receipt_diagnostics_fail_closed_without_private_details(tmp_path):
    """Missing/falsified evidence emits only reviewed condition codes."""
    from ai_commerce.remote.worker import _verified_receipt_evidence

    args = dict(root=tmp_path, workspace=tmp_path,
                source_sha="a" * 40, sources=[], profiles={})
    reasons = []
    assert not _verified_receipt_evidence({}, **args, diagnostics=reasons)
    assert reasons == ["RECEIPT_EVIDENCE_UNAVAILABLE"]
    reasons = []
    assert not _verified_receipt_evidence(
        {"evidence_files": {}, "evidence_sha256": {},
         "verify_completed": True, "provider_return_determinate": True},
        **args, diagnostics=reasons)
    assert reasons == ["RECEIPT_PROVENANCE_INCOMPLETE"]


def test_v22_manifest_respects_authorized_directory_prefixes(tmp_path):
    validator, data, context = _v22_manifest_fixture(tmp_path)
    allowed = ("src/ai_commerce/fresh", "tests", "contracts",
               "work_items/mt01-components")
    admitted = validator(data, **{**context, "allowed_paths": allowed})
    assert admitted["name"] == "fresh"
    assert admitted["status"] == "VALIDATED_DATA_ONLY_NOT_VERIFICATION_PASS"


def test_v22_pr_ci_cancels_only_superseded_pull_requests():
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/ef02-tests.yml").read_text(encoding="utf-8")
    assert "concurrency:" in workflow
    assert "github.event.pull_request.number || github.ref" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert "branches: [ef02-multi-action-implementation, main]" in workflow


def test_v22_cold_component_one_pr_offline_canary(tmp_path, monkeypatch):
    """A17-before-prereg analogue: Git base lacks owner; one candidate adds code,
    test, contract and data-only manifest; EF02, CI oracle and pytest agree.
    No provider invocation, network, GH PR or write to the real repo.
    """
    import base64
    import hashlib
    import json
    import os
    import runpy
    import subprocess
    import sys
    from ai_commerce.engineering import minimum_test as mt
    from ai_commerce.engineering.impact_v21 import (
        registered_action_plan, verified_onboarding_binding, candidate_onboarding_groups)
    from ai_commerce.engineering.impact_v21 import select_impact
    from ai_commerce.engineering.minimum_test import file_hashes

    old_src = "src/ai_commerce/learning/export.py"
    old_test = "tests/test_prior.py"
    new_src = "src/ai_commerce/fresh/svc.py"
    new_test = "tests/test_fresh_svc.py"
    contract = "contracts/FreshRun.v1.schema.json"
    manifest = "work_items/mt01-components/fresh.json"
    mk(tmp_path, old_src, "def handle(value):\n    return value + 1\n")
    mk(tmp_path, old_test,
       'import runpy\ndef test_prior():\n'
       '    assert runpy.run_path("src/ai_commerce/learning/export.py")["handle"](7) == 8\n')
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path,
                                       stderr=subprocess.DEVNULL).decode().strip()
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git("-c", "user.name=MT01", "-c", "user.email=mt01@example.invalid",
        "commit", "-qm", "base without new owner")
    base_sha = git("rev-parse", "HEAD")
    baseline_files = file_hashes(tmp_path)

    mk(tmp_path, new_src, "def deliver():\n    return 7\n")
    mk(tmp_path, new_test,
       'import runpy\ndef test_new_and_consumer():\n'
       '    value = runpy.run_path("src/ai_commerce/fresh/svc.py")["deliver"]()\n'
       '    assert runpy.run_path("src/ai_commerce/learning/export.py")["handle"](value) == 8\n')
    mk(tmp_path, contract, '{"type":"object"}\n')
    manifest_data = {
        "schema_version": "MT01ComponentOwner.v1",
        "component": "fresh", "sources": [new_src],
        "tests": [new_test], "acs": ["FRESH.offline"],
        "consumers": [old_src], "contracts": [contract],
    }
    raw = json.dumps(manifest_data, sort_keys=True).encode()
    mk(tmp_path, manifest, raw.decode())
    changed = [new_src, new_test, contract, manifest]
    old_groups = (group("prior", [old_test], [old_src]),)
    monkeypatch.setattr(mt, "approved_groups", lambda: old_groups)
    ingress = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {"mt01-component-onboarding": ingress,
                "mt01-prior-tests": ["python", "-m", "pytest", old_test, "-q", "-n", "12"]}
    plan = registered_action_plan(
        repo=tmp_path, base_sha=base_sha, base_files=baseline_files,
        candidate_files=file_hashes(tmp_path), changed_paths=changed,
        required_acs=manifest_data["acs"], fallback_argv=ingress,
        groups=old_groups, registered_profiles=profiles, allowed_paths=changed)
    assert plan["selected_groups"] == ["fresh", "prior"]
    assert not plan["fallback"]
    assert not plan["unknown_acs"]
    assert all(item["execute"] for item in plan["groups"])
    new_binding = next(item["binding"] for item in plan["groups"]
                       if item["group"] == "fresh")
    assert verified_onboarding_binding(
        workspace=tmp_path, binding=new_binding, changed_paths=changed,
        allowed_paths=changed, base_sha=base_sha, registered_profiles=profiles,
        expected_group="fresh")

    selected = select_impact(
        root=tmp_path, changed_paths=changed,
        groups=(*old_groups, *candidate_onboarding_groups(
            root=tmp_path, changed_paths=changed,
            base_paths=list(baseline_files), existing_groups=old_groups,
            allowed_paths=changed)), base_sha=base_sha)
    assert selected.runnable
    assert set(selected.selected_tests) == {new_test, old_test}
    oracle = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                ".github/scripts/mt01_v21_oracle.py"))
    independent_groups = oracle["independently_reviewed_manifest_groups"](
        repo=tmp_path, changed_paths=set(changed),
        base_paths=set(baseline_files), base_groups=old_groups)
    valid, reasons = oracle["check_selection"](
        {"version": 2, "changed_paths": changed,
         "selected_groups": list(selected.selected_groups),
         "selected_tests": list(selected.selected_tests)},
        base_groups=old_groups, candidate_groups=independent_groups,
        changed_paths=set(changed), present_paths=set(file_hashes(tmp_path)))
    assert valid, reasons

    # Real collection and execution of both product and legacy consumer
    # tests, separate from the registry binding's fixed 12-worker request.
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    execution = subprocess.run(
        [sys.executable, "-m", "pytest", new_test, old_test, "-q"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert execution.returncode == 0, (execution.stdout, execution.stderr)
    assert "2 passed" in execution.stdout


def test_v22_committed_component_owner_persists_in_followup_pr(tmp_path, monkeypatch):
    """After a one-PR new owner is merged, next source-only PR stays targeted."""
    import subprocess
    import runpy
    import pytest
    from ai_commerce.engineering import minimum_test as mt
    from ai_commerce.engineering.impact_v21 import (
        load_baseline_component_owner_records, registered_action_plan,
        verified_onboarding_binding, select_impact)

    _, data, ctx = _v22_manifest_fixture(tmp_path)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path,
                                       stderr=subprocess.DEVNULL).decode().strip()
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git("-c", "user.name=MT01", "-c", "user.email=mt01@example.invalid",
        "commit", "-qm", "baseline with already reviewed manifest owner")
    base = git("rev-parse", "HEAD")
    base_files = mt.file_hashes(tmp_path)
    old_groups = ctx["existing_groups"]
    monkeypatch.setattr(mt, "approved_groups", lambda: old_groups)
    records = load_baseline_component_owner_records(
        root=tmp_path, base_sha=base, existing_groups=old_groups)
    assert len(records) == 1 and records[0]["group"].name == "fresh"

    changed = data["sources"][0]
    mk(tmp_path, changed, "def improved():\n    return 'after-onboarding'\n")
    ingress = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {"mt01-component-onboarding": ingress,
                "mt01-prior-tests": ["python", "-m", "pytest",
                                    "tests/test_prior.py", "-q", "-n", "12"]}
    plan = registered_action_plan(
        repo=tmp_path, base_sha=base, base_files=base_files,
        candidate_files=mt.file_hashes(tmp_path),
        changed_paths=[changed], required_acs=data["acs"],
        fallback_argv=ingress, groups=old_groups,
        registered_profiles=profiles, allowed_paths=[changed])
    assert set(plan["selected_groups"]) == {"fresh", "prior"}
    assert plan["unknown_acs"] == []
    assert not plan["fallback"]
    assert all(g["execute"] for g in plan["groups"])
    binding = next(g["binding"] for g in plan["groups"] if g["group"] == "fresh")
    assert verified_onboarding_binding(
        workspace=tmp_path, binding=binding, changed_paths=[changed],
        allowed_paths=[changed], base_sha=base,
        registered_profiles=profiles, expected_group="fresh")
    independent = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                    ".github/scripts/mt01_v21_oracle.py"))
    reviewed = independent["trusted_baseline_manifest_groups"](
        repo=tmp_path, base_sha=base, base_groups=old_groups)
    assert len(reviewed) == 1 and reviewed[0].name == "fresh"
    selected = select_impact(
        root=tmp_path, changed_paths=[changed],
        groups=(*old_groups, *reviewed), base_sha=base)
    assert set(selected.selected_tests) == {
        "tests/test_prior.py", "tests/test_fresh_svc.py"}
    good, reasons = independent["check_selection"](
        {"version": 2, "changed_paths": [changed],
         "selected_tests": list(selected.selected_tests),
         "selected_groups": list(selected.selected_groups)},
        base_groups=(*old_groups, *reviewed), candidate_groups=(),
        changed_paths={changed}, present_paths=set(mt.file_hashes(tmp_path)))
    assert good, reasons

    # Replacing an immutable baseline manifest is not a new registration.
    manifest = tmp_path / ctx["manifest_path"]
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="ONBOARD_BASELINE_MANIFEST_DRIFT"):
        load_baseline_component_owner_records(
            root=tmp_path, base_sha=base, existing_groups=old_groups)


def test_issue_ingress_and_skipped_cleanup_reviewed_safety_contract():
    """Personal is the only live HostOps route; EF02 and cleanup stay reviewed."""
    from ai_commerce.engineering.impact_v21 import CONTROL_TESTS

    root = Path(__file__).resolve().parents[1]
    expected = (
        ".github/workflows/issue-ingress.yml",
        ".github/workflows/actions-skipped-cleanup.yml",
    )
    for path in expected:
        assert path in CONTROL_TESTS
        assert (root / path).is_file()
        assert "tests/test_mt01_v21.py" in CONTROL_TESTS[path]

    ingress = (root / expected[0]).read_text(encoding="utf-8")
    assert "types: [opened, labeled]" in ingress
    assert "uses: ./.github/workflows/ef02-batch.yml" in ingress
    assert "  hostops-personal:" in ingress
    assert "APPROVE WRITE #" in ingress
    assert "github.event.repository.full_name == 'trung-via/ai-commerce'" in ingress
    for old in ("hostops-readonly.yml", "hostops-v2.yml", "hostops-v3.yml",
                "hostops-ci.yml", "hostops-v2-ci.yml"):
        assert not (root / ".github/workflows" / old).exists()
        assert "uses: ./.github/workflows/" + old not in ingress
        assert ".github/workflows/" + old in CONTROL_TESTS
    called = (root / ".github/workflows/ef02-batch.yml").read_text(encoding="utf-8")
    assert "  workflow_call:" in called

    cleanup = (root / expected[1]).read_text(encoding="utf-8")
    for old in ("hostops-readonly.yml", "hostops-v2.yml", "hostops-v3.yml"):
        assert old not in cleanup
    for required in (
        "status: 'skipped'", "run.status !== 'completed'",
        "run.conclusion !== 'skipped'", "run.run_attempt !== 1",
        "allowed.get(run.path) !== run.event",
        "Date.parse(run.updated_at) > cutoff",
        "jobs.jobs.every(job =>", "job.conclusion === 'skipped'",
        "artifacts.total_count !== 0", "const maxDeletes = 500",
        "const dryRun = process.env.DRY_RUN === 'true'",
        "actions: write", "skipped-cleanup-receipt.json",
    ):
        assert required in cleanup, required


def _v22_two_baseline_owners_in_git(tmp_path, *, cyclic=False, ghost=False):
    """A lexically earlier manifest consumes a later reviewed source owner."""
    import json
    import subprocess
    _, original, ctx = _v22_manifest_fixture(tmp_path)
    dependent_source = "src/ai_commerce/aa_dependent/client.py"
    dependent_test = "tests/test_aa_dependent.py"
    dependent_path = "work_items/mt01-components/aa-dependent.json"
    mk(tmp_path, dependent_source, "def value():\n    return 2\n")
    mk(tmp_path, dependent_test, "def test_dep():\n    assert True\n")
    dependent = {
        "schema_version": "MT01ComponentOwner.v1",
        "component": "aa-dependent",
        "sources": [dependent_source],
        "tests": [dependent_test],
        "acs": ["AA_DEPENDENT.offline"],
        "consumers": [original["sources"][0]],
        "contracts": [],
    }
    mk(tmp_path, dependent_path, json.dumps(dependent))
    if cyclic or ghost:
        original["consumers"].append(
            dependent_source if cyclic else "src/ai_commerce/unknown/ghost.py")
        (tmp_path / ctx["manifest_path"]).write_text(
            json.dumps(original), encoding="utf-8")
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=tmp_path, stderr=subprocess.DEVNULL).decode().strip()
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git("-c", "user.name=MT01", "-c", "user.email=mt01@example.invalid",
        "commit", "-qm", "fixture with dependency-ordered owner manifests")
    return git("rev-parse", "HEAD"), ctx["existing_groups"]


def test_v22_baseline_owner_dependency_order_beats_lexicographic_order(tmp_path):
    import runpy
    from ai_commerce.engineering.impact_v21 import load_baseline_component_owner_records
    base, old_groups = _v22_two_baseline_owners_in_git(tmp_path)
    records = load_baseline_component_owner_records(
        root=tmp_path, base_sha=base, existing_groups=old_groups)
    assert [item["group"].name for item in records] == [
        "fresh", "aa-dependent"]
    oracle = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                ".github/scripts/mt01_v21_oracle.py"))
    independent = oracle["trusted_baseline_manifest_groups"](
        repo=tmp_path, base_sha=base, base_groups=old_groups)
    assert [item.name for item in independent] == [
        "fresh", "aa-dependent"]
    assert [g.tests for g in independent] == [
        ("tests/test_fresh_svc.py",), ("tests/test_aa_dependent.py",)]


def test_v22_baseline_dependency_cycle_stays_denied_by_planner_and_oracle(tmp_path):
    import runpy
    import pytest
    from ai_commerce.engineering.impact_v21 import load_baseline_component_owner_records
    base, old_groups = _v22_two_baseline_owners_in_git(tmp_path, cyclic=True)
    with pytest.raises(ValueError, match="ONBOARD_BASELINE_DEPENDENCY_CYCLE"):
        load_baseline_component_owner_records(
            root=tmp_path, base_sha=base, existing_groups=old_groups)
    oracle = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                ".github/scripts/mt01_v21_oracle.py"))
    with pytest.raises(ValueError, match="BASELINE_OWNER_DEPENDENCY_CYCLE"):
        oracle["trusted_baseline_manifest_groups"](
            repo=tmp_path, base_sha=base, base_groups=old_groups)


def test_v22_baseline_unowned_consumer_never_silently_admitted(tmp_path):
    import runpy
    import pytest
    from ai_commerce.engineering.impact_v21 import load_baseline_component_owner_records
    base, old_groups = _v22_two_baseline_owners_in_git(tmp_path, ghost=True)
    with pytest.raises(ValueError, match="ONBOARD_CONSUMER_UNPROVEN"):
        load_baseline_component_owner_records(
            root=tmp_path, base_sha=base, existing_groups=old_groups)
    oracle = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                ".github/scripts/mt01_v21_oracle.py"))
    with pytest.raises(ValueError, match="BASELINE_OWNER_CLOSURE_MISSING"):
        oracle["trusted_baseline_manifest_groups"](
            repo=tmp_path, base_sha=base, base_groups=old_groups)

def test_d5_attended_cdp_script_control_map_and_independent_oracle(tmp_path):
    """Only exact D5 operator script selects its mandatory regression closure."""
    import runpy
    from ai_commerce.engineering.impact_v21 import CONTROL_TESTS, select_impact
    path = "scripts/AI-Commerce-D5-Attended-CDP.ps1"
    required = {
        "tests/test_ef02_d5_chrome_acquisition.py", "tests/test_minimum_test.py",
        "tests/test_mt01_v21.py", "tests/test_pr_merge_gate.py",
    }
    assert set(CONTROL_TESTS[path]) == required
    for p in required:
        mk(tmp_path, p, "# synthetic registered test file\n")
    mk(tmp_path, path, "# synthetic PowerShell control only\n")
    plan = select_impact(root=tmp_path, changed_paths=[path], groups=())
    assert plan.runnable
    assert set(plan.selected_tests) == required
    assert "REVIEWED_CONTROL_CLOSURE" in plan.reason_codes
    oracle = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                ".github/scripts/mt01_v21_oracle.py"))
    assert oracle["CONTROL_TESTS"][path] == required
    proof = {"version": 2, "changed_paths": [path], "selected_tests": sorted(required),
             "selected_groups": []}
    ok, reasons = oracle["check_selection"](
        proof, base_groups=(), candidate_groups=(),
        changed_paths={path}, present_paths=required)
    assert ok, reasons
    proof["selected_tests"] = sorted(required - {"tests/test_mt01_v21.py"})
    ok, reasons = oracle["check_selection"](
        proof, base_groups=(), candidate_groups=(),
        changed_paths={path}, present_paths=required)
    assert not ok
    assert "REQUIRED_TEST_OMITTED:tests/test_mt01_v21.py" in reasons
    other = "scripts/AI-Commerce-D5-Unreviewed-Remote-Shell.ps1"
    mk(tmp_path, other, "# do not trust this script\n")
    blocked = select_impact(root=tmp_path, changed_paths=[other], groups=())
    assert not blocked.runnable
    assert other not in CONTROL_TESTS


def test_mt01_impact_control_only_selects_reviewed_self_tests():
    """Bootstrap regression #386: planner control change must not fan out to unrelated suites."""
    from ai_commerce.engineering.minimum_test import approved_groups
    from ai_commerce.engineering.impact_v21 import CONTROL_TESTS, select_impact

    source = "src/ai_commerce/engineering/impact_v21.py"
    root = Path(__file__).resolve().parents[1]
    assert CONTROL_TESTS[source] == ("tests/test_mt01_v21.py", "tests/test_minimum_test.py")
    selected = select_impact(
        root=root, changed_paths=(source, "tests/test_mt01_v21.py"),
        groups=approved_groups(),
    )
    assert selected.runnable, selected
    assert selected.selected_groups == ("mt01-v21",)
    assert selected.selected_tests == ("tests/test_minimum_test.py", "tests/test_mt01_v21.py")


def test_d5_operator_bridge_pr_ci_covers_independent_hostops_personal_consumer(tmp_path):
    """The trusted merge gate's mandatory D5 script tests must be selected by CI.

    Regression for #450: before this mapping was added, 1,064 hosted tests
    passed but the merge gate correctly returned V21_CONTENT_PROOF_INVALID
    because tests/test_hostops_personal.py was never selected.
    """
    from ai_commerce.engineering.impact_v21 import CONTROL_TESTS, select_impact

    path = "scripts/AI-Commerce-D5-Operator-Bridge.ps1"
    mandatory = {
        "tests/test_hostops_d5_control.py",
        "tests/test_ef02_d5_chrome_acquisition.py",
        "tests/test_hostops_personal.py",
        "tests/test_hostops_v3.py",
    }
    assert set(CONTROL_TESTS[path]) == mandatory
    for test_file in mandatory:
        mk(tmp_path, test_file, "# synthetic test file; no Windows effects\n")
    mk(tmp_path, path, "# source-pinned PowerShell path only\n")
    plan = select_impact(root=tmp_path, changed_paths=[path], groups=())
    assert plan.runnable, plan
    assert mandatory <= set(plan.selected_tests)
    assert "REVIEWED_CONTROL_CLOSURE" in plan.reason_codes

    # The independent protected Brain merge gate requires precisely this
    # already-reviewed list; never drop the Personal HostOps consumer.
    assert "tests/test_hostops_personal.py" in plan.selected_tests


def test_mt01_duplicate_onboarding_command_never_claims_static_owner(tmp_path):
    """#447/#449: identical argv must not switch an ordinary test into onboarding."""
    from ai_commerce.engineering.impact_v21 import canonical_registered_profile
    argv = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {"mt01-component-onboarding": argv, "mt01-mt01-v21-tests": argv}
    assert canonical_registered_profile(
        profiles, argv, group_name="mt01-v21", onboarding=False) == "mt01-mt01-v21-tests"
    assert canonical_registered_profile(
        profiles, argv, group_name="fresh-owner", onboarding=True) == "mt01-component-onboarding"
    assert canonical_registered_profile(
        {"mt01-component-onboarding": argv}, argv,
        group_name="mt01-v21", onboarding=False) is None
    assert canonical_registered_profile(
        {"unrelated-a": argv, "unrelated-b": argv}, argv,
        group_name="mt01-v21", onboarding=False) is None
    # Onboarding argv is derived from a validated manifest, not the
    # registration's fixed bootstrap argv.
    assert canonical_registered_profile(
        {"mt01-component-onboarding": ["python", "-m", "pytest", "other.py"]},
        argv, group_name="new-owner", onboarding=True) == "mt01-component-onboarding"
    assert canonical_registered_profile(
        {}, argv, group_name="new-owner", onboarding=True) is None


def test_mt01_real_impact_planner_uses_static_owner_not_reserved_profile(tmp_path):
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    src = "src/ai_commerce/fresh/service.py"
    test = "tests/test_mt01_v21.py"
    mk(tmp_path, src, "x = 1\\n")
    mk(tmp_path, test, "def test_value(): assert True\\n")
    baseline = file_hashes(tmp_path)
    mk(tmp_path, src, "x = 2\\n")
    command = ["python", "-m", "pytest", test, "-q", "-n", "12"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=baseline,
        candidate_files=file_hashes(tmp_path), changed_paths=[src],
        required_acs=["MT01.test"], fallback_argv=command,
        groups=(group("mt01-v21", (test,), (src,)),),
        registered_profiles={
            "mt01-component-onboarding": command,
            "mt01-mt01-v21-tests": command,
        },
    )
    assert plan["selected_groups"] == ["mt01-v21"]
    # A disposable minimal sandbox may remain UNPROVEN for unrelated source/
    # consumer coverage. This regression is specifically about *identity*;
    # it must not claim an EF01 or EF02 PASS from the synthetic planner.
    assert plan["groups"][0]["binding"]["registered_profile"] == "mt01-mt01-v21-tests"
    assert not any("REGISTERED_PROFILE_REQUIRED" in reason for reason in plan["reason_codes"])
    assert "onboarding_data_only" not in plan["groups"][0]["binding"]


def test_mt01_preflight_refuses_bad_profile_before_running_any_pytest():
    from ai_commerce.engineering.verification import registered_binding_problems
    argv = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {"mt01-component-onboarding": argv, "mt01-mt01-v21-tests": argv}
    ordinary = {"groups": [{"binding": {
        "registered_profile": "mt01-mt01-v21-tests", "argv": argv,
    }}]}
    assert registered_binding_problems(ordinary, profiles) == []
    forged = {"groups": [{"binding": {
        "registered_profile": "mt01-component-onboarding", "argv": argv,
    }}]}
    assert registered_binding_problems(forged, profiles) == [
        "MT01_GROUP_0_ONBOARDING_PROOF_MISSING"]
    wrong_name = {"groups": [{"binding": {
        "registered_profile": "other", "argv": argv,
    }}]}
    assert registered_binding_problems(wrong_name, profiles) == [
        "MT01_GROUP_0_REGISTERED_BINDING_REJECTED"]
    valid_owner = {"groups": [{"binding": {
        "registered_profile": "mt01-component-onboarding", "argv": argv,
        "onboarding_data_only": True, "onboarding_manifest_path":
            "work_items/mt01-components/new-owner.json",
        "onboarding_manifest_sha256": "f" * 64,
    }}]}
    assert registered_binding_problems(valid_owner, profiles) == []
    # Still only a preflight; private manifest/evidence validation happens in EF02.


def test_forged_onboarding_binding_rejected_by_verified_onboarding_binding(tmp_path):
    from ai_commerce.engineering.impact_v21 import verified_onboarding_binding
    argv = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {
        "mt01-component-onboarding": argv,
        "mt01-mt01-v21-tests": argv,
    }
    # Forged onboarding profile on normal group without manifest metadata
    forged_binding = {
        "base_sha": "d" * 40,
        "argv": argv,
        "registered_profile": "mt01-component-onboarding",
    }
    assert not verified_onboarding_binding(
        workspace=tmp_path,
        binding=forged_binding,
        changed_paths=["src/ai_commerce/engineering/impact_v21.py"],
        allowed_paths=["src/ai_commerce/engineering/impact_v21.py"],
        base_sha="d" * 40,
        registered_profiles=profiles,
        expected_group="mt01-v21",
    )


def _v22_normal_owner_repo(tmp_path, monkeypatch, *, manifest=True, malformed=False):
    """Real immutable base in an external repository, with inherited Git guard."""
    import json
    import subprocess
    from ai_commerce.engineering import minimum_test as mt

    _, data, ctx = _v22_manifest_fixture(tmp_path)
    source, test = data["sources"][0], data["tests"][0]
    mk(tmp_path, source, "def value():\n    return 1\n")
    mk(tmp_path, test, 'import runpy\ndef test_value():\n'
       f'    assert runpy.run_path("{source}")["value"]() == 1\n')
    if not manifest:
        (tmp_path / ctx["manifest_path"]).unlink()
    else:
        (tmp_path / ctx["manifest_path"]).write_bytes(
            json.dumps({} if malformed else data, indent=2).encode() + b"\n")

    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=tmp_path, stderr=subprocess.STDOUT).decode().strip()

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git("-c", "user.name=MT01", "-c", "user.email=mt01@example.invalid",
        "commit", "-qm", "reviewed baseline owner fixture")
    base_sha, base_files = git("rev-parse", "HEAD"), file_hashes(tmp_path)
    monkeypatch.setattr(mt, "approved_groups", lambda: ctx["existing_groups"])
    mk(tmp_path, source, "def value():\n    return 2\n")
    mk(tmp_path, test, 'import runpy\ndef test_value():\n'
       f'    assert runpy.run_path("{source}")["value"]() == 2\n')
    ingress = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    project = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    return data, ctx, dict(
        repo=tmp_path, base_sha=base_sha, base_files=base_files,
        candidate_files=file_hashes(tmp_path), changed_paths=[source, test],
        allowed_paths=[source, test], required_acs=data["acs"],
        fallback_argv=project, groups=ctx["existing_groups"],
        registered_profiles={
            "project-tests": project, "mt01-component-onboarding": ingress,
            "mt01-prior-tests": ["python", "-m", "pytest", "tests/test_prior.py", "-q", "-n", "12"],
        })


def test_v22_normal_project_action_executes_committed_owner_and_replays_binding(tmp_path, monkeypatch):
    import copy
    import hashlib
    from ai_commerce.engineering import verification
    from ai_commerce.engineering.impact_v21 import verified_onboarding_binding

    data, ctx, args = _v22_normal_owner_repo(tmp_path, monkeypatch)
    plan = registered_action_plan(**args)
    assert plan["selected_groups"] == ["fresh", "prior"]
    assert plan["impact_v21"]["missing_mapping"] == ()
    assert plan["unknown_acs"] == [] and not plan["fallback"]
    assert all(entry["execute"] for entry in plan["groups"])
    owner, prior = plan["groups"]
    assert prior["selected_profile"] == "mt01-prior-tests"
    assert "onboarding_data_only" not in prior["binding"]
    binding = owner["binding"]
    expected = ["python", "-m", "pytest", *data["tests"], "-q", "-n", "12"]
    assert owner["selected_profile"] == "mt01-component-onboarding"
    assert binding["argv"] == expected
    assert binding["onboarding_manifest_path"] == ctx["manifest_path"]
    assert binding["onboarding_manifest_sha256"] == hashlib.sha256(
        (tmp_path / ctx["manifest_path"]).read_bytes()).hexdigest()
    assert binding["selected_impact_tests"] == sorted([*data["tests"], "tests/test_prior.py"])
    replay = dict(workspace=tmp_path, binding=binding,
                  base_sha=args["base_sha"], changed_paths=args["changed_paths"],
                  allowed_paths=args["allowed_paths"],
                  registered_profiles=args["registered_profiles"], expected_group="fresh")
    assert verified_onboarding_binding(**replay)
    for key, value in (("base_sha", "f" * 40), ("onboarding_manifest_sha256", "0" * 64),
                       ("mapping", prior["binding"]["mapping"]),
                       ("selected_impact_tests", data["tests"]),
                       ("argv", args["fallback_argv"])):
        forged = copy.deepcopy(binding)
        forged[key] = value
        assert not verified_onboarding_binding(**{**replay, "binding": forged}), key
    assert verification.registered_binding_problems(plan, args["registered_profiles"]) == []
    execution = verification.run_pytest(expected, repo=tmp_path, timeout=90)
    assert execution.returncode == 0, execution.stdout + execution.stderr
    assert "1 passed" in execution.stdout
    assert verification.observed_xdist_workers(execution.stdout.encode()) == 12
    # A later source change cannot reuse the receipt's exact source binding.
    mk(tmp_path, data["sources"][0], "def value():\n    return 3\n")
    newer = registered_action_plan(**{**args, "candidate_files": file_hashes(tmp_path)})
    new_binding = next(e["binding"] for e in newer["groups"] if e["group"] == "fresh")
    assert new_binding["candidate_files"] != binding["candidate_files"]


def test_v22_serial_action_with_committed_owner_holds_without_parallel_upgrade(tmp_path, monkeypatch):
    """A serial WorkItem may not inherit the reserved -n12 manifest capability."""
    from ai_commerce.engineering.impact_v21 import verified_onboarding_binding

    data, _, args = _v22_normal_owner_repo(tmp_path, monkeypatch)
    serial = ["python", "-m", "pytest", "tests", "-q"]
    prior_serial = ["python", "-m", "pytest", "tests/test_prior.py", "-q"]
    args["fallback_argv"] = serial
    args["registered_profiles"]["project-tests-serial"] = serial
    args["registered_profiles"]["mt01-prior-serial-tests"] = prior_serial
    plan = registered_action_plan(**args)

    assert plan["fallback"] is False
    assert "fresh:SERIAL_MANIFEST_OWNER_PROFILE_UNPROVEN" in plan["reason_codes"]
    assert plan["impact_v21"]["mode"] == "HOLD_COVERAGE"
    assert all(entry["execute"] is False for entry in plan["groups"])
    assert plan["groups"][-1]["group"] == "v21-coverage-hold"
    owner = next(e for e in plan["groups"] if e["group"] == "fresh")
    prior = next(e for e in plan["groups"] if e["group"] == "prior")
    assert owner["binding"]["argv"] == ["python", "-m", "pytest", *data["tests"], "-q"]
    assert "-n" not in owner["binding"]["argv"]
    assert prior["binding"]["argv"] == prior_serial
    assert prior["selected_profile"] == "mt01-prior-serial-tests"
    assert not verified_onboarding_binding(
        workspace=tmp_path, binding=owner["binding"],
        changed_paths=args["changed_paths"], allowed_paths=args["allowed_paths"],
        base_sha=args["base_sha"], registered_profiles=args["registered_profiles"],
        expected_group="fresh",
    )


def test_v22_normal_project_action_without_baseline_owner_stays_held(tmp_path, monkeypatch):
    _, _, args = _v22_normal_owner_repo(tmp_path, monkeypatch, manifest=False)
    plan = registered_action_plan(**args)
    assert not any(entry["execute"] for entry in plan["groups"])
    assert any("SOURCE_OWNERSHIP_REQUIRED" in p for p in plan["impact_v21"]["missing_mapping"])
    assert any("TEST_REGISTRATION_REQUIRED" in p for p in plan["impact_v21"]["missing_mapping"])


def test_v22_candidate_manifest_requires_explicit_ingress_with_real_base(tmp_path, monkeypatch):
    import subprocess
    from ai_commerce.engineering import minimum_test as mt

    old_source, old_test = "src/ai_commerce/learning/export.py", "tests/test_prior.py"
    mk(tmp_path, old_source)
    mk(tmp_path, old_test)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path,
                                       stderr=subprocess.STDOUT).decode().strip()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=MT01", "-c", "user.email=mt01@example.invalid",
        "commit", "-qm", "base without new component")
    base_sha, base_files = git("rev-parse", "HEAD"), file_hashes(tmp_path)
    _, data, ctx = _v22_manifest_fixture(tmp_path)
    monkeypatch.setattr(mt, "approved_groups", lambda: ctx["existing_groups"])
    project = ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    ingress = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    args = dict(repo=tmp_path, base_sha=base_sha, base_files=base_files,
                candidate_files=file_hashes(tmp_path), changed_paths=ctx["changed_paths"],
                allowed_paths=ctx["allowed_paths"], required_acs=data["acs"],
                fallback_argv=project, groups=ctx["existing_groups"],
                registered_profiles={"project-tests": project, "mt01-component-onboarding": ingress,
                    "mt01-prior-tests": ["python", "-m", "pytest", old_test, "-q", "-n", "12"]})
    denied = registered_action_plan(**args)
    assert "ONBOARD_EXPLICIT_INGRESS_REQUIRED" in denied["reason_codes"]
    assert not any(entry["execute"] for entry in denied["groups"])
    admitted = registered_action_plan(**{**args, "fallback_argv": ingress})
    assert all(entry["execute"] for entry in admitted["groups"])
    assert admitted["selected_groups"] == ["fresh", "prior"]


@pytest.mark.parametrize("failure", [
    "malformed", "drifted", "crlf", "missing", "no-capability", "wrong-base",
    "unknown-source", "unknown-test", "unknown-ac", "changed-manifest", "unsafe-edit",
])
def test_v22_normal_baseline_owner_failures_hold_entire_action(tmp_path, monkeypatch, failure):
    from ai_commerce.engineering.impact_v21 import verified_onboarding_binding

    data, ctx, args = _v22_normal_owner_repo(tmp_path, monkeypatch, malformed=failure == "malformed")
    manifest = tmp_path / ctx["manifest_path"]
    if failure == "drifted":
        manifest.write_text("{}", encoding="utf-8")
    elif failure == "crlf":
        manifest.write_bytes(manifest.read_bytes().replace(b"\n", b"\r\n"))
    elif failure == "missing":
        manifest.unlink()
    elif failure == "no-capability":
        args["registered_profiles"].pop("mt01-component-onboarding")
    elif failure == "wrong-base":
        args["base_sha"] = "f" * 40
    elif failure in ("unknown-source", "unknown-test", "unsafe-edit"):
        path = {"unknown-source": "src/ai_commerce/unknown/service.py",
                "unknown-test": "tests/test_unknown.py", "unsafe-edit": "pytest.ini"}[failure]
        mk(tmp_path, path)
        args["changed_paths"].append(path)
        args["allowed_paths"].append(path)
    elif failure == "unknown-ac":
        args["required_acs"] = [*data["acs"], "UNKNOWN.offline"]
    elif failure == "changed-manifest":
        # Even unchanged manifest bytes cannot be claimed as new admission.
        args["changed_paths"].append(ctx["manifest_path"])
        args["allowed_paths"].append(ctx["manifest_path"])
    args["candidate_files"] = file_hashes(tmp_path)
    plan = registered_action_plan(**args)
    assert plan["impact_v21"]["mode"].startswith("HOLD"), plan
    assert not any(entry["execute"] for entry in plan["groups"]), plan
    assert plan["groups"][-1]["group"] == "v21-coverage-hold"
    for entry in plan["groups"]:
        if entry["group"] == "fresh" and failure != "unknown-ac":
            assert not verified_onboarding_binding(
                workspace=tmp_path, binding=entry["binding"], changed_paths=args["changed_paths"],
                allowed_paths=args["allowed_paths"], base_sha=args["base_sha"],
                registered_profiles=args["registered_profiles"], expected_group="fresh")


@pytest.mark.parametrize("changed", [
    ["src/ai_commerce/production_runtime/attended_worker.py", "tests/test_b0_3_attended_worker.py"],
    ["tests/test_b0_3_attended_worker.py"],
    ["src/ai_commerce/production_runtime/operator.py"],
    ["src/ai_commerce/creative_quality/pulse_studio_offline_bridge.py"],
    ["src/ai_commerce/creative_quality/pulse_studio_effect_preflight.py"],
])
def test_v22_normal_real_main_manifests_resolve_production_owner_closure(changed):
    import json
    import subprocess
    from ai_commerce.engineering.minimum_test import approved_groups
    from ai_commerce.engineering.impact_v21 import verified_onboarding_binding

    root = Path(__file__).resolve().parents[1]
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    profiles = json.loads((root / ".github/ef02/verify-profiles.json").read_text(encoding="utf-8-sig"))
    files = file_hashes(root)
    plan = registered_action_plan(
        repo=root, base_sha=base, base_files=files, candidate_files=files,
        changed_paths=changed, allowed_paths=changed, required_acs=(),
        fallback_argv=profiles["project-tests"], groups=approved_groups(), registered_profiles=profiles)
    assert not plan["impact_v21"]["missing_mapping"], plan["reason_codes"]
    assert all(entry["execute"] for entry in plan["groups"]), plan["reason_codes"]
    expected = {"b0-3", "b0-4", "bq-05a", "bq-05b0"} if changed[0] == "src/ai_commerce/production_runtime/attended_worker.py" else {
        "tests/test_b0_3_attended_worker.py": {"b0-3"},
        "src/ai_commerce/production_runtime/operator.py": {"b0-4", "bq-05a", "bq-05b0"},
        "src/ai_commerce/creative_quality/pulse_studio_offline_bridge.py": {"bq-05a", "bq-05b0"},
        "src/ai_commerce/creative_quality/pulse_studio_effect_preflight.py": {"bq-05b0"},
    }[changed[0]]
    assert expected <= set(plan["selected_groups"])
    for entry in plan["groups"]:
        binding = entry["binding"]
        if "onboarding_manifest_path" in binding:
            assert binding["registered_profile"] == "mt01-component-onboarding"
            assert verified_onboarding_binding(
                workspace=root, binding=binding, changed_paths=changed, allowed_paths=changed,
                base_sha=base, registered_profiles=profiles, expected_group=entry["group"])
        else:
            assert binding["registered_profile"] == f'mt01-{entry["group"]}-tests'
            assert profiles[binding["registered_profile"]] == binding["argv"]

def test_710_g3_baseline_oracle_has_exact_no_model_workflow_control():
    # Independent trusted baseline must not inherit arbitrary new controls.
    import importlib.util
    import sys
    path = Path(__file__).resolve().parents[1] / ".github/scripts/mt01_v21_oracle.py"
    spec = importlib.util.spec_from_file_location("mt01_g3_trusted_control_oracle", path)
    oracle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = oracle
    try:
        spec.loader.exec_module(oracle)
        exact = ".github/workflows/ef02-antigravity-headless-preflight.yml"
        assert oracle.CONTROL_TESTS[exact] == {
            "tests/test_ef02.py", "tests/test_ef02_scope_main_integration.py",
            "tests/test_mt01_v21.py"}
        assert ".github/workflows/ef02-headless-unsafe.yml" not in oracle.CONTROL_TESTS
    finally:
        sys.modules.pop(spec.name, None)



def test_710_no_model_headless_workflow_has_trusted_mt01_control_closure(tmp_path):
    from ai_commerce.engineering.impact_v21 import CONTROL_TESTS, select_impact
    path = ".github/workflows/ef02-antigravity-headless-preflight.yml"
    reviewed = ("tests/test_ef02.py", "tests/test_ef02_scope_main_integration.py",
                "tests/test_mt01_v21.py")
    assert tuple(CONTROL_TESTS[path]) == reviewed
    for test_path in reviewed:
        mk(tmp_path, test_path, "# registered independent test\n")
    result = select_impact(root=tmp_path, changed_paths=[path], groups=())
    assert result.runnable, result
    assert set(result.selected_tests) == set(reviewed)
    assert result.missing_mapping == ()
    # An attacker introducing another workflow does not inherit this approval.
    unrelated = ".github/workflows/ef02-antigravity-unsafe.yml"
    denied = select_impact(root=tmp_path, changed_paths=[unrelated], groups=())
    assert denied.mode == "HOLD_MAPPING"
    assert unrelated + ":UNREVIEWED_CI_CONTROL" in denied.missing_mapping



def test_710_g3b_existing_protected_workflow_mt01_owner_consumer_closure(tmp_path):
    from ai_commerce.engineering.impact_v21 import CONTROL_TESTS, select_impact
    path = ".github/workflows/ef02-antigravity-headless-preflight.yml"
    required = ("tests/test_ef02.py", "tests/test_ef02_scope_main_integration.py",
                "tests/test_mt01_v21.py")
    assert tuple(CONTROL_TESTS[path]) == required
    for name in required:
        mk(tmp_path, name, "# trusted consumer fixture\\n")
    actual = select_impact(root=tmp_path, changed_paths=[path], groups=())
    assert actual.runnable and actual.missing_mapping == ()
    assert set(actual.selected_tests) == set(required)
    denied = select_impact(root=tmp_path, changed_paths=[
        ".github/workflows/ef02-antigravity-headless-unsafe.yml"], groups=())
    assert denied.mode == "HOLD_MAPPING"


def test_epwo750_approved_allowed_path_closes_required_legacy_ac(tmp_path):
    """Actual changed-path impact remains narrow, approved AC tests still execute."""
    groups, a12, a14 = fixture(tmp_path)
    groups = (
        TestGroup("a12", (a12,), ("A12.product-evidence",),
                  ("src/ai_commerce/product/identity.py",)),
        TestGroup("a14", (a14,), ("A14.L04.offline-attribution-evidence",),
                  ("src/ai_commerce/affiliate_attribution/evidence.py",)),
    )
    files = file_hashes(tmp_path)
    profiles = {
        "mt01-a12-tests": ["python", "-m", "pytest", a12, "-q", "-n", "12"],
        "mt01-a14-tests": ["python", "-m", "pytest", a14, "-q", "-n", "12"],
    }
    changed = ["src/ai_commerce/affiliate_attribution/evidence.py"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files, changed_paths=changed,
        required_acs=("A12.product-evidence",),
        fallback_argv=["python", "-m", "pytest", "tests", "-q", "-n", "12"],
        groups=groups, registered_profiles=profiles, env={},
        allowed_paths=("src/ai_commerce/product/identity.py", *changed),
    )
    assert plan["changed_paths"] == changed
    assert tuple(plan["impact_v21"]["selected_groups"]) == ("a14",)
    assert plan["selected_groups"] == ["a12", "a14"]
    assert plan["required_ac_group_closure"] == ["a12"]
    assert "REVIEWED_ALLOWED_SCOPE_AC_GROUP:a12" in plan["reason_codes"]
    assert not any(x.startswith("AC_REQUIRED_GROUP_NOT_SELECTED") for x in plan["reason_codes"])
    assert all(g["execute"] and g["state"] == "EXECUTE" for g in plan["groups"])
    assert set(plan["consumer_map"]) == {"a12", "a14"}


@pytest.mark.parametrize("allowed", [
    ("src/ai_commerce/affiliate_attribution/evidence.py",),
    ("src/ai_commerce/product/untracked_identity.py",),
    (),
])
def test_epwo750_unapproved_or_untracked_path_cannot_waive_ac(tmp_path, allowed):
    groups, a12, a14 = fixture(tmp_path)
    groups = (
        TestGroup("a12", (a12,), ("A12.product-evidence",),
                  ("src/ai_commerce/product/identity.py",)),
        TestGroup("a14", (a14,), ("A14.L04.offline-attribution-evidence",),
                  ("src/ai_commerce/affiliate_attribution/evidence.py",)),
    )
    files = file_hashes(tmp_path)
    profiles = {
        "mt01-a12-tests": ["python", "-m", "pytest", a12, "-q", "-n", "12"],
        "mt01-a14-tests": ["python", "-m", "pytest", a14, "-q", "-n", "12"],
    }
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files,
        changed_paths=["src/ai_commerce/affiliate_attribution/evidence.py"],
        required_acs=("A12.product-evidence",),
        fallback_argv=["python", "-m", "pytest", "tests", "-q", "-n", "12"],
        groups=groups, registered_profiles=profiles, env={}, allowed_paths=allowed,
    )
    assert plan["selected_groups"] == ["a14"]
    assert plan["required_ac_group_closure"] == []
    assert "AC_REQUIRED_GROUP_NOT_SELECTED:A12.product-evidence" in plan["reason_codes"]
    assert all(not g["execute"] and g["state"] == "UNPROVEN"
               for g in plan["groups"])


def test_epwo750_all_11_ac_owners_intersect_original_reviewed_allowed_scope():
    """Source registry, not the unaccepted provider patch, owns this test graph."""
    from ai_commerce.engineering.minimum_test import approved_groups
    allowed = {
        "src/ai_commerce/remote/validation.py",
        "src/ai_commerce/remote/__main__.py",
        "src/ai_commerce/remote/worker.py",
        "tests/test_ef01_performance.py",
        "tests/test_ef01_repair.py",
        "tests/test_ef02.py",
        "tests/test_ef02_d5_local_handoff.py",
        "tests/test_ef02_m0r_fault_injection.py",
        "tests/test_ef02_wake_d1.py",
        "tests/test_mt01_v21.py",
    }
    required = {
        "EF01.execution", "EF01.repair", "EF01.source-integrity",
        "EF01.provider-boundary", "EF01P.git-and-host-budget",
        "EF02.action-and-fanin", "EF02.recovery", "EF02.scope-review",
        "MT01.selection-and-evidence", "MT01.v2-impact-selection",
        "EF02.wake-boundaries",
    }
    owners = approved_groups()
    assert all(any(ac in g.acs and allowed.intersection(
        set(g.tests) | set(g.paths) | set(g.consumers) | set(g.contracts))
        for g in owners) for ac in required)


def test_epwo750_real_registered_one_file_change_covers_all_original_11_acs():
    """Dry-run the exact #750 MT01 selection without a model or changed file."""
    import json
    import subprocess
    from ai_commerce.engineering.minimum_test import approved_groups
    root = Path(__file__).resolve().parents[1]
    pinned = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
        text=True, check=True, timeout=20).stdout.strip()
    assert len(pinned) == 40
    files = file_hashes(root)
    profiles = json.loads(
        (root / ".github/ef02/verify-profiles.json").read_text(encoding="utf-8"))
    allowed = (
        "src/ai_commerce/remote/validation.py",
        "src/ai_commerce/remote/__main__.py",
        "src/ai_commerce/remote/worker.py",
        "tests/test_ef01_performance.py",
        "tests/test_ef01_repair.py",
        "tests/test_ef02.py",
        "tests/test_ef02_d5_local_handoff.py",
        "tests/test_ef02_m0r_fault_injection.py",
        "tests/test_ef02_wake_d1.py",
        "tests/test_mt01_v21.py",
    )
    required = (
        "EF01.execution", "EF01.repair", "EF01.source-integrity",
        "EF01.provider-boundary", "EF01P.git-and-host-budget",
        "EF02.action-and-fanin", "EF02.recovery", "EF02.scope-review",
        "MT01.selection-and-evidence", "MT01.v2-impact-selection",
        "EF02.wake-boundaries",
    )
    plan = registered_action_plan(
        repo=root, base_sha=pinned, base_files=files, candidate_files=files,
        changed_paths=["tests/test_ef02_m0r_fault_injection.py"],
        required_acs=required, fallback_argv=profiles["ef02-tests"],
        groups=approved_groups(), registered_profiles=profiles,
        allowed_paths=allowed, env={},
    )
    assert plan["changed_paths"] == ["tests/test_ef02_m0r_fault_injection.py"]
    assert plan["unknown_acs"] == []
    assert plan["impact_v21"]["mode"] in ("TARGETED", "AFFECTED")
    assert not any(code.startswith("AC_REQUIRED_GROUP_NOT_SELECTED:")
                   for code in plan["reason_codes"])
    assert all(g["execute"] and g["state"] == "EXECUTE" for g in plan["groups"])
    assert not any(g["group"] == "v21-coverage-hold" for g in plan["groups"])
    executed_acs = {ac for group in plan["groups"] for ac in group["acs"]}
    assert set(required) <= executed_acs
    assert all(group["binding"]["registered_profile"] in profiles
               for group in plan["groups"])
    assert {"ef01", "repair", "integrity", "provider", "ef02-wake",
            "mt01", "mt01-v21"} <= set(plan["selected_groups"])
