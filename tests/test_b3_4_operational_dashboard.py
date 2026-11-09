"""B3.4 scoped actual B0.4/A15 native read regression; no live user telemetry."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json

import pytest

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.cost_ledger import CostLedger
from ai_commerce.production_runtime import metrics
from ai_commerce.production_runtime.b3_operational_dashboard import (
    B3OperationalDashboard, DashboardHold, operational_dashboard,
)
from test_a15_operations import cost, charged

SESSION_SHA = "a" * 64
ATTEMPT_ONE = "1" * 32
ATTEMPT_TWO = "2" * 32


def native_operator(tmp_path):
    root = tmp_path / "operator"
    root.mkdir()
    metrics.initialize(root, SESSION_SHA)
    metrics.append(root, SESSION_SHA, command="run", attempt_id=ATTEMPT_ONE,
                   phase="ATTEMPT", wall_ns=100, native_admission=True)
    metrics.append(root, SESSION_SHA, command="run", attempt_id=ATTEMPT_ONE,
                   phase="RESULT", outcome="HOLD", wall_ns=200, active_ns=50,
                   wall_elapsed_ns=100, clock_drift=False,
                   counters={"media_processes": 1, "network_attempts": 0,
                             "other_process_attempts": 0})
    metrics.append(root, SESSION_SHA, command="reconcile", attempt_id=ATTEMPT_TWO,
                   phase="ATTEMPT", wall_ns=210, native_admission=False)
    head = metrics.read(root, SESSION_SHA)[-1]["checksum"]
    return root, head


def native_costs(tmp_path):
    ledger = CostLedger(ArtifactRegistry(tmp_path / "a15"))
    ledger.record(cost(entry_id="estimated", amount="5.00", currency="USD"))
    ledger.record(charged(entry_id="supplied", amount="2.50", currency="USD",
                          evidence_ref="manual:provider-1"))
    ledger.record(charged(entry_id="synthetic", amount="40.00", currency="VND",
                          evidence_ref="synthetic:receipt-3",
                          evidence_sha256="c" * 64, evidence_origin="SYNTHETIC_FIXTURE"))
    return ledger


def snapshot_files(root):
    return {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_absent_sources_are_missing_not_zero_and_not_external_quota():
    out = operational_dashboard().to_dict()
    assert out["status"] == "INSUFFICIENT_OBSERVATIONS"
    assert out["observed"]["b04_attempts"] is None
    assert out["observed"]["b04_agent_calls_within_audited_boundary"] is None
    assert out["cost_summary"] is None
    assert out["unobserved"]["brain_turns"] is None
    assert out["unobserved"]["codex_quota_remaining"] is None
    assert out["unobserved"]["lost_work_units"] is None
    assert "B04_AUDIT_NOT_SELECTED" in out["missingness"]
    assert "A15_COST_ENTRIES_NOT_SELECTED" in out["missingness"]


def test_native_b04_recovery_unknown_and_a15_currency_separation_readonly(tmp_path):
    root, head = native_operator(tmp_path)
    costs = native_costs(tmp_path)
    before = snapshot_files(tmp_path)
    args = dict(operator_root=root, session_sha256=SESSION_SHA,
                expected_audit_head_sha256=head, cost_ledger=costs,
                cost_entry_ids=("estimated", "supplied", "synthetic"))
    packet = operational_dashboard(**args)
    out = packet.to_dict()
    assert out["status"] == "SCOPED_LOCAL_EVIDENCE_ONLY"
    assert out["observed"]["b04_attempts"] == 2
    assert out["observed"]["b04_outcomes"] == {"PASS": 0, "HOLD": 1, "UNKNOWN": 0}
    assert out["observed"]["b04_incomplete_unknown"] == 1
    assert out["observed"]["b04_recovery_outcomes"]["INCOMPLETE_UNKNOWN"] == 1
    assert out["observed"]["b04_active_completed_ns"] == 50
    assert out["observed"]["b04_observed_wall_span_ns"] == 110
    assert out["observed"]["b04_agent_calls_within_audited_boundary"] == 0
    assert out["unobserved"]["global_ef02_calls"] is None
    assert out["unobserved"]["brain_turns"] is None
    assert out["unobserved"]["lost_work_units"] is None
    assert out["cost_summary"]["by_currency"]["USD"]["estimated"] == "5.00"
    assert out["cost_summary"]["by_currency"]["USD"]["supplied_charged"] == "2.50"
    assert out["cost_summary"]["by_currency"]["VND"]["synthetic_charged"] == "40.00"
    assert out["cost_summary"]["external_provider_charge_verified"] is False
    assert out["limits"]["cross_owner_identity"] == "NOT_ESTABLISHED"
    assert "B04_INCOMPLETE_ACTIVE_DURATION_UNKNOWN" in out["missingness"]
    assert B3OperationalDashboard(packet.raw).record_sha256 == packet.record_sha256
    assert operational_dashboard(**args).raw == packet.raw
    assert snapshot_files(tmp_path) == before


def test_empty_native_b04_has_no_fabricated_agent_observations(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    metrics.initialize(root, SESSION_SHA)
    out = operational_dashboard(operator_root=root, session_sha256=SESSION_SHA,
                                expected_audit_head_sha256="0" * 64).to_dict()
    assert out["observed"]["b04_attempts"] == 0
    assert out["observed"]["b04_agent_calls_within_audited_boundary"] is None
    assert "B04_NO_COMPLETED_RESULTS" in out["missingness"]


def test_tampered_native_head_fails_closed_no_repair(tmp_path):
    root, head = native_operator(tmp_path)
    with pytest.raises(DashboardHold, match="B34_AUDIT_HEAD_CHANGED"):
        operational_dashboard(operator_root=root, session_sha256=SESSION_SHA,
                              expected_audit_head_sha256="f" * 64)
    (root / "audit-head-000003.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DashboardHold, match="B34_B04_NATIVE_HOLD"):
        operational_dashboard(operator_root=root, session_sha256=SESSION_SHA,
                              expected_audit_head_sha256=head)


@pytest.mark.parametrize("ids", [
    ("estimated", "estimated"), ("not-persisted",),
])
def test_native_cost_duplicates_and_missing_records_are_not_zero(tmp_path, ids):
    ledger = native_costs(tmp_path)
    with pytest.raises(DashboardHold, match="B34_COST_SELECTION_INVALID|B34_A15_NATIVE_HOLD"):
        operational_dashboard(cost_ledger=ledger, cost_entry_ids=ids)


def test_unknown_cost_and_no_provider_attestation(tmp_path):
    ledger = native_costs(tmp_path)
    ledger.record(cost(entry_id="pending", status="UNKNOWN",
                       amount=None, currency=None))
    out = operational_dashboard(cost_ledger=ledger,
                                cost_entry_ids=("pending",)).to_dict()
    assert out["cost_summary"]["counts"]["UNKNOWN"] == 1
    assert out["cost_summary"]["by_currency"] == {}
    assert "A15_CHARGE_AMOUNT_UNKNOWN_OR_PENDING" in out["missingness"]
    assert out["unobserved"]["verified_provider_cost"] is None


def test_packet_digest_and_false_authority_rejected(tmp_path):
    good = operational_dashboard().to_dict()
    good["limits"]["provider_effect_authority"] = "PUBLISH"
    with pytest.raises(DashboardHold, match="B34_PACKET_IDENTITY"):
        B3OperationalDashboard(canonical_bytes(good))
    good["record_sha256"] = sha256(canonical_bytes({
        k: v for k, v in good.items() if k != "record_sha256"})).hexdigest()
    with pytest.raises(DashboardHold, match="B34_PACKET_AUTHORITY"):
        B3OperationalDashboard(canonical_bytes(good))
    with pytest.raises(DashboardHold, match="B34_PACKET_FALSE_GLOBAL_COUNTER"):
        forged = operational_dashboard().to_dict()
        forged["unobserved"]["brain_turns"] = 999
        forged["record_sha256"] = sha256(canonical_bytes({
            k: v for k, v in forged.items() if k != "record_sha256"})).hexdigest()
        B3OperationalDashboard(canonical_bytes(forged))


def test_partial_native_selection_requires_exact_type(tmp_path):
    ledger = native_costs(tmp_path)
    with pytest.raises(DashboardHold, match="B34_OPERATOR_SELECTION_INVALID"):
        operational_dashboard(operator_root=tmp_path)
    with pytest.raises(DashboardHold, match="B34_COST_SELECTION_INVALID"):
        operational_dashboard(cost_ledger=ledger, cost_entry_ids=["estimated"])
    with pytest.raises(DashboardHold, match="B34_COST_SELECTION_INVALID"):
        operational_dashboard(cost_entry_ids=("estimated",))

def test_b04_native_selected_path_provisions_real_ffmpeg_without_global_install():
    """True CI selector dependency; avoid hiding native B0.4 tests."""
    import runpy
    from types import SimpleNamespace

    source = Path(__file__).resolve().parents[1] / ".github/scripts/ef02-pr-ci-tests.py"
    checker = runpy.run_path(str(source))["_needs_ffmpeg_for_plan"]
    assert checker(SimpleNamespace(selected_tests=["tests/test_b0_4_operator.py"])) is True
    assert checker(SimpleNamespace(selected_tests=[
        "tests/test_b3_4_operational_dashboard.py", "tests/test_b0_4_operator.py"])) is True
    assert checker(SimpleNamespace(selected_tests=["tests/test_a15_operations.py"])) is False
