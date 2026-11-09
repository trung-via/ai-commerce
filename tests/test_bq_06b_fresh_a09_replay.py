"""BQ-06b bootstrap/admission tests (not an A09 product acceptance suite).

After EF02 independent authoring, replace/add true A09 correction/reprocess
native owner tests; do not confuse these bootstrap tests with BQ06b PASS.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess

import pytest
from ai_commerce.remote.validation import require_preexecutor_acceptance, RemoteRequestError
from ai_commerce.creative_quality import behavioral_learning_fresh_replay as owner

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "src/ai_commerce/creative_quality/behavioral_learning_fresh_replay.py"
TEST = "tests/test_bq_06b_fresh_a09_replay.py"
MANIFEST = "work_items/mt01-components/bq-06b.json"


def _head_sha():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=15).strip()


def _action(acs=None, paths=None):
    return {
        "instruction": "MT01_ACCEPTANCE_JSON:" + json.dumps(
            acs if acs is not None else ["A09.analytics","A11.learning","A17.C07.C10.E06.offline-creative-learning"]),
        "allowed_paths": paths if paths is not None else [SOURCE, TEST],
    }


def test_registration_is_only_bootstrap_and_cannot_claim_current_replay():
    assert owner.IMPLEMENTATION_STATUS == "NOT_IMPLEMENTED"
    assert owner.SCHEMA == "BQ06bFreshEvidenceReview.v1"
    with pytest.raises(owner.BQ06bNotImplemented, match="BQ06B_INDEPENDENT_IMPLEMENTATION_REQUIRED"):
        owner.review_fresh_evidence()


def test_bq06b_manifest_is_reviewed_and_bound_to_exact_source_and_test():
    manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "MT01ComponentOwner.v1"
    assert manifest["component"] == "bq-06b"
    assert manifest["sources"] == [SOURCE]
    assert manifest["tests"] == [TEST]
    assert set(manifest["acs"]) == {
        "BQ-06b.a09-raw-replay-pinned-to-a11",
        "BQ-06b.missingness-and-comparability",
        "BQ-06b.brain-review-non-authoritative",
    }


def test_real_git_committed_baseline_derives_exact_bq06b_owner_and_consumer_acs():
    # This is not a fabricated mock group. Trust comes from the actual pinned
    # Git tree + matching bytes and existing reviewed source owners.
    pin = _head_sha()
    plan = require_preexecutor_acceptance(
        _action(), source_closure=True, root=ROOT, source_sha=pin)
    assert "BQ-06b.a09-raw-replay-pinned-to-a11" in plan["ids"]
    assert "BQ-06b.missingness-and-comparability" in plan["ids"]
    assert "BQ-06b.brain-review-non-authoritative" in plan["ids"]
    assert {"A09.analytics","A11.learning"} <= set(plan["ids"])
    assert "bq-06b" in {v["group"] for v in plan["consumer_map"].values()}


def test_invalid_exact_tree_sha_fails_before_any_executor_claim():
    with pytest.raises(RemoteRequestError, match="INTAKE_BASELINE_OWNER_UNTRUSTED"):
        require_preexecutor_acceptance(
            _action(), source_closure=True, root=ROOT, source_sha="0" * 40)


def test_unknown_acceptance_or_unmapped_write_is_rejected_before_provider():
    with pytest.raises(RemoteRequestError, match="INTAKE_AC_UNREGISTERED"):
        require_preexecutor_acceptance(
            _action(acs=["MALICIOUS.FAKE.AC"]), source_closure=True,
            root=ROOT, source_sha=_head_sha())
    with pytest.raises(RemoteRequestError, match="INTAKE_SOURCE_OWNER_UNMAPPED"):
        require_preexecutor_acceptance(
            _action(paths=[SOURCE, "unowned/new_component.py"]),
            source_closure=True, root=ROOT, source_sha=_head_sha())


def test_no_forged_component_from_issue_or_untrusted_working_tree():
    with pytest.raises(RemoteRequestError, match="INTAKE_BASELINE_IDENTITY_UNPROVEN"):
        require_preexecutor_acceptance(
            _action(), source_closure=True, root=ROOT, source_sha=None)
