"""MT-01 V2.1 adversarial selection contract. No model, network or host effects."""
from pathlib import Path
from ai_commerce.engineering.minimum_test import TestGroup, file_hashes
from ai_commerce.engineering.impact_v21 import registered_action_plan

from ai_commerce.engineering.impact_v21 import select_impact


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


def test_v21_unknown_acceptance_id_never_passes(tmp_path):
    groups, a12, _ = fixture(tmp_path)
    files = file_hashes(tmp_path)
    narrow = ["python", "-m", "pytest", a12, "-q", "-n", "12"]
    plan = registered_action_plan(
        repo=tmp_path, base_sha="a" * 40, base_files=files,
        candidate_files=files, changed_paths=["src/ai_commerce/product/identity.py"],
        required_acs=("UNKNOWN_AC",), fallback_argv=narrow, groups=groups,
        registered_profiles={"mt01-a12-tests": narrow}, env={},
    )
    assert "UNKNOWN_AC" in plan["unknown_acs"]
    assert "AC_UNMAPPED:UNKNOWN_AC" in plan["reason_codes"]
    assert any(g["state"] == "UNPROVEN" for g in plan["groups"])


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
