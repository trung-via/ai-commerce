"""A15 O05/O06/O07: offline evidence and no-side-effect regressions."""
import json

import pytest

from ai_commerce.media import (MediaRequest, MediaInput, MockMediaProvider,
                               FakeAsyncMediaProvider, GenerationJobRequest)
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.operations import (ArtifactCollisionError, ArtifactRegistry,
                                    LocalMediaRunner, GenerationJobRunner)
from ai_commerce.operations.cost_ledger import CostEntry, CostEvidenceError, CostLedger
from ai_commerce.operations.retry_guard import (AttemptObservation, ReconciliationObservation,
                                                RetryEvidenceError, RetryGuard)
from ai_commerce.operations.status_report import media_job_status, generation_job_status
from ai_commerce.operations.a15_cli import main as a15_cli_main

DIGEST = "a" * 64
EVIDENCE = "b" * 64
OBSERVED = "2026-11-02T15:00:00Z"


@pytest.fixture
def registry(tmp_path):
    return ArtifactRegistry(tmp_path / "registry")


@pytest.fixture
def ledger(registry):
    return CostLedger(registry)


def cost(**changes):
    values = {
        "entry_id": "cost-1", "job_id": "job-1", "attempt": 1,
        "intent_digest": DIGEST, "provider": "offline-provider",
        "model": None, "status": "ESTIMATED", "amount": "1.20",
        "currency": "USD", "evidence_ref": None,
        "evidence_sha256": None, "evidence_origin": "UNVERIFIED",
        "observed_at": OBSERVED,
    }
    values.update(changes)
    return CostEntry(**values)


def charged(**changes):
    values = {
        "status": "CHARGED", "evidence_ref": "manual:receipt-1",
        "evidence_sha256": EVIDENCE, "evidence_origin": "USER_SUPPLIED",
    }
    values.update(changes)
    return cost(**values)


def obs(**changes):
    fields = {"intent_id": "intent-1", "attempt": 1,
              "outcome": "UNKNOWN_EFFECT", "side_effects": "UNKNOWN"}
    fields.update(changes)
    return AttemptObservation(**fields)


def test_cost_write_replay_and_durable_read(registry, ledger):
    entry = cost()
    assert ledger.record(entry) == entry
    assert ledger.record(entry) == entry
    assert CostLedger(ArtifactRegistry(registry.root)).get("cost-1") == entry
    assert ledger.summarize(("cost-1",))["by_currency"]["USD"]["estimated"] == "1.20"


def test_cost_collision_different_payload_same_id_holds(ledger):
    ledger.record(cost())
    with pytest.raises(ArtifactCollisionError):
        ledger.record(cost(amount="2.20"))
    assert ledger.get("cost-1").amount == "1.20"


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "01", "1e4",
                                  "1.0000000", 1.25, True, ""])
def test_cost_rejects_float_nonfinite_negative_and_bad_precision(amount):
    with pytest.raises(CostEvidenceError):
        cost(amount=amount)


def test_cost_rejects_charge_without_sourced_evidence():
    with pytest.raises(CostEvidenceError, match="CHARGE_REQUIRES_SUPPLIED_RECEIPT"):
        cost(status="CHARGED")
    with pytest.raises(CostEvidenceError, match="CHARGE_REQUIRES_SUPPLIED_RECEIPT"):
        cost(status="CHARGED", evidence_ref="manual:claim",
             evidence_sha256=EVIDENCE, evidence_origin="UNVERIFIED")


def test_cost_pending_unknown_not_zero_and_status_amount_disjoint(ledger):
    for i, status in enumerate(("PENDING", "UNKNOWN", "NOT_APPLICABLE"), 1):
        ledger.record(cost(entry_id=f"missing-{i}", status=status,
                           amount=None, currency=None))
    report = ledger.summarize(("missing-1", "missing-2", "missing-3"))
    assert report["counts"]["PENDING"] == 1
    assert report["counts"]["UNKNOWN"] == 1
    assert report["counts"]["NOT_APPLICABLE"] == 1
    assert report["by_currency"] == {}
    with pytest.raises(CostEvidenceError, match="AMOUNT_STATUS_MISMATCH"):
        cost(status="PENDING", amount="0")


def test_cost_ledger_does_not_mix_currencies_or_synthetic_charges(ledger):
    ledger.record(charged(entry_id="charged-real", amount="10.00", currency="USD"))
    ledger.record(charged(entry_id="charged-fake", amount="55.25",
                          currency="USD", evidence_ref="synthetic:fixture-2",
                          evidence_sha256="c" * 64, evidence_origin="SYNTHETIC_FIXTURE"))
    ledger.record(cost(entry_id="estimate-vnd", amount="1000", currency="VND"))
    report = ledger.summarize(("charged-real", "charged-fake", "estimate-vnd"))
    assert report["by_currency"]["USD"]["supplied_charged"] == "10.00"
    assert report["by_currency"]["USD"]["synthetic_charged"] == "55.25"
    assert report["by_currency"]["VND"]["estimated"] == "1000"
    assert report["external_provider_charge_verified"] is False
    assert report["business_revenue_or_settlement_verified"] is False


def test_cost_projection_duplicate_rejected(ledger):
    ledger.record(cost())
    with pytest.raises(CostEvidenceError, match="DUPLICATE_PROJECTION_ENTRY"):
        ledger.summarize(["cost-1", "cost-1"])


def test_cost_checksum_corrupt_fails_closed(registry, ledger):
    ledger.record(cost())
    path = registry.safe_path(CostLedger.ref("cost-1"))
    row = json.loads(path.read_text())
    row["amount"] = "5000"
    path.write_text(json.dumps(row))
    with pytest.raises(CostEvidenceError, match="COST_RECORD_CHECKSUM_MISMATCH"):
        ledger.get("cost-1")


def test_unknown_amount_does_not_turn_into_estimate():
    with pytest.raises(CostEvidenceError):
        cost(status="UNKNOWN", amount="0")


def test_cost_rejects_non_utc_observed_time():
    with pytest.raises(CostEvidenceError, match="OBSERVATION_MUST_BE_UTC"):
        cost(observed_at="2026-11-02T15:00:00+07:00")


def test_intent_identity_replay_and_collision(registry):
    guard = RetryGuard(registry)
    assert guard.register_intent("intent-1", DIGEST)["request_digest"] == DIGEST
    assert guard.register_intent("intent-1", DIGEST)["request_digest"] == DIGEST
    with pytest.raises(ArtifactCollisionError):
        guard.register_intent("intent-1", EVIDENCE)
    assert guard.decision("intent-1")["decision"] == "REQUIRES_HUMAN_AUTHORIZATION"


def test_unknown_effect_refuses_implicit_retry(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs())
    assert guard.record_attempt(obs())["outcome"] == "UNKNOWN_EFFECT"
    decision = guard.decision("intent-1", last_attempt=1)
    assert decision["decision"] == "RECONCILE_REQUIRED"
    assert decision["retry_dispatched"] is False
    assert decision["provider_called"] is False


def test_explicit_failure_no_effect_only_allows_human_authorization(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs(outcome="FAILED_NO_EFFECT", side_effects="NONE",
                             evidence_ref="local:verified-failure",
                             evidence_sha256=EVIDENCE))
    decision = guard.decision("intent-1", last_attempt=1)
    assert decision["decision"] == "REQUIRES_HUMAN_AUTHORIZATION"
    assert decision["reason"] == "NO_EFFECT_EVIDENCED"
    assert decision["retry_dispatched"] is False


def test_confirmed_side_effects_no_retry(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs(outcome="SUCCEEDED", side_effects="CONFIRMED",
                             evidence_ref="manual:observation",
                             evidence_sha256=EVIDENCE))
    assert guard.decision("intent-1", last_attempt=1)["decision"] == "NO_RETRY"


@pytest.mark.parametrize("state,side_effects", [
    ("FAILED_NO_EFFECT", "UNKNOWN"),
    ("SUCCEEDED", "UNKNOWN"),
    ("PENDING", "NONE"),
    ("RECONCILED_CONFIRMED", "NONE"),
])
def test_invalid_attempt_proof_refuses_state_upgrade(state, side_effects):
    with pytest.raises(RetryEvidenceError):
        obs(outcome=state, side_effects=side_effects)


def test_attempt_same_key_changed_state_is_collision(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs())
    with pytest.raises(ArtifactCollisionError):
        guard.record_attempt(obs(outcome="SUCCEEDED", side_effects="CONFIRMED",
                                 evidence_ref="manual:report",
                                 evidence_sha256=EVIDENCE))


def test_attempt_checks_missing_identity(registry):
    guard = RetryGuard(registry)
    with pytest.raises(Exception):
        guard.record_attempt(obs())


def test_a04_dry_run_status_is_not_commercial_success(registry, ledger):
    runner = LocalMediaRunner(registry)
    manifest = runner.run(MediaRequest(mode="dry-run"), job_id="job-1")
    assert manifest.state == "DRY_RUN"
    assert manifest.result["publication_status"] == "NOT_ATTEMPTED"
    ledger.record(cost(entry_id="cost-dry", status="NOT_APPLICABLE",
                       amount=None, currency=None))
    report = media_job_status(manifest, ledger, ("cost-dry",))
    assert report["asset_job_state"] == "DRY_RUN"
    assert report["retry_decision"]["decision"] == "RECONCILE_REQUIRED"
    assert report["publication_status"] == "NOT_ATTEMPTED"
    assert report["click_status"] == report["order_status"] == "UNKNOWN"
    assert report["settlement_status"] == "UNKNOWN"
    assert report["additional_provider_calls"] is False
    assert report["money_movement_verified"] is False


def test_status_refuses_cost_from_different_job(registry, ledger):
    manifest = LocalMediaRunner(registry).run(MediaRequest(mode="dry-run"), job_id="job-1")
    ledger.record(cost(entry_id="wrong-job", job_id="other"))
    with pytest.raises(CostEvidenceError, match="COST_ENTRY_SOURCE_IDENTITY_MISMATCH"):
        media_job_status(manifest, ledger, ("wrong-job",))


def test_retry_guard_refuses_stale_last_attempt_hint(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs())
    decision = guard.decision("intent-1", last_attempt=0)
    assert decision["decision"] == "RECONCILE_REQUIRED"
    assert decision["reason"] == "STALE_ATTEMPT_VIEW"


def test_retry_guard_refuses_second_attempt_after_unknown(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs())
    with pytest.raises(RetryEvidenceError, match="PREVIOUS_EFFECT_RECONCILIATION_REQUIRED"):
        guard.record_attempt(obs(attempt=2))


def test_retry_guard_only_accepts_consecutive_attempts(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    with pytest.raises(RetryEvidenceError, match="MISSING_PREVIOUS_ATTEMPT"):
        guard.record_attempt(obs(attempt=3))


def test_duplicate_charge_source_across_distinct_entry_ids_fails_closed(ledger):
    ledger.record(charged(entry_id="paid-1", amount="10.00"))
    ledger.record(charged(entry_id="paid-2", amount="10.00"))
    with pytest.raises(CostEvidenceError, match="DUPLICATE_CHARGE_EVIDENCE"):
        ledger.summarize(("paid-1", "paid-2"))


def _reconciliation(guard, *, intent_id="intent-1", attempt=1,
                    side_effects="NONE", request_digest=DIGEST,
                    original_record_sha256=None, evidence_ref="manual:verified-observation"):
    original = guard.read_attempt(intent_id, attempt)
    return ReconciliationObservation(
        intent_id=intent_id, attempt=attempt, request_digest=request_digest,
        original_record_sha256=original_record_sha256 or sha256_bytes(canonical_bytes(original)),
        side_effects=side_effects, evidence_ref=evidence_ref,
        evidence_sha256=EVIDENCE,
    )


def test_unknown_effect_append_only_reconciled_no_effect_permits_human_gated_next_attempt(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs())
    receipt = _reconciliation(guard)
    assert guard.record_reconciliation(receipt)["side_effects"] == "NONE"
    assert guard.record_reconciliation(receipt)["side_effects"] == "NONE"
    assert guard.read_attempt("intent-1", 1)["outcome"] == "UNKNOWN_EFFECT"
    decision = guard.decision("intent-1", last_attempt=1)
    assert decision["decision"] == "REQUIRES_HUMAN_AUTHORIZATION"
    assert decision["reason"] == "RECONCILED_NO_EFFECT_SUPPLIED"
    assert decision["provider_called"] is False
    assert decision["retry_dispatched"] is False
    assert guard.record_attempt(obs(attempt=2))["attempt"] == 2
    assert guard.decision("intent-1", last_attempt=1)["reason"] == "STALE_ATTEMPT_VIEW"


def test_unknown_effect_confirmed_reconciles_without_retry(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs())
    guard.record_reconciliation(_reconciliation(guard, side_effects="CONFIRMED"))
    assert guard.decision("intent-1", last_attempt=1)["decision"] == "NO_RETRY"
    with pytest.raises(RetryEvidenceError, match="PREVIOUS_EFFECT_RECONCILIATION_REQUIRED"):
        guard.record_attempt(obs(attempt=2))


def test_reconciliation_refuses_wrong_intent_and_original_receipt(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs())
    with pytest.raises(RetryEvidenceError, match="RECONCILIATION_BINDING_MISMATCH"):
        guard.record_reconciliation(_reconciliation(guard, request_digest=EVIDENCE))
    with pytest.raises(RetryEvidenceError, match="RECONCILIATION_BINDING_MISMATCH"):
        guard.record_reconciliation(_reconciliation(guard, original_record_sha256=EVIDENCE))


def test_reconciliation_conflict_does_not_rewrite_original(registry):
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", DIGEST)
    guard.record_attempt(obs())
    guard.record_reconciliation(_reconciliation(guard))
    with pytest.raises(ArtifactCollisionError):
        guard.record_reconciliation(_reconciliation(guard, side_effects="CONFIRMED"))
    assert guard.read_reconciliation("intent-1", 1)["side_effects"] == "NONE"


def test_report_rejects_wrong_cost_digest_and_provider(registry, ledger):
    manifest = LocalMediaRunner(registry).run(MediaRequest(mode="dry-run"), job_id="job-1")
    args = {"status": "NOT_APPLICABLE", "amount": None, "currency": None}
    ledger.record(cost(entry_id="bad-digest", intent_digest=EVIDENCE,
                       provider="local-dry-run", **args))
    ledger.record(cost(entry_id="bad-provider", intent_digest=manifest.request_digest,
                       provider="fake-other-provider", **args))
    for entry_id in ("bad-digest", "bad-provider"):
        with pytest.raises(CostEvidenceError, match="COST_ENTRY_SOURCE_IDENTITY_MISMATCH"):
            media_job_status(manifest, ledger, (entry_id,))


def test_report_rejects_unpersisted_cost_and_retry_intent_binding(registry, ledger):
    manifest = LocalMediaRunner(registry).run(MediaRequest(mode="dry-run"), job_id="job-1")
    guard = RetryGuard(registry)
    guard.register_intent("intent-1", EVIDENCE)
    with pytest.raises(CostEvidenceError, match="RETRY_INTENT_DIGEST_MISMATCH"):
        media_job_status(manifest, ledger, (), guard=guard, intent_id="intent-1")


def test_a04_v1_mock_report_uses_persisted_artifact_without_new_provider_call(registry, ledger):
    runner = LocalMediaRunner(registry)
    manifest = runner.run(MediaRequest(mode="mock"), job_id="job-1")
    report = media_job_status(manifest, ledger, ())
    assert report["asset_job_state"] == "SUCCEEDED"
    assert report["additional_provider_calls"] is False
    assert report["money_movement_verified"] is False
    assert report["historical_provider_side_effects"] == "UNKNOWN"


def test_a04_v2_generation_projection_never_resubmits(registry, ledger, tmp_path, monkeypatch):
    raw = MockMediaProvider().produce(MediaInput("mock", None, "image/png", None)).content
    source = tmp_path / "fake-v2.png"
    source.write_bytes(raw)
    fake = FakeAsyncMediaProvider(source)
    request = GenerationJobRequest(
        fake.capabilities.name, fake.capabilities.model, "prompt:fixture",
        DIGEST, prompt_sha256=EVIDENCE, prompt_version="1")
    runner = GenerationJobRunner(registry, fake)
    original = runner.submit(request, job_id="gen-1")
    assert original.job_id == "gen-1"
    def forbidden(*args, **kwargs):
        raise AssertionError("REPORT_MUST_NOT_CALL_PROVIDER")
    for name in ("submit", "observe", "fetch", "reconcile"):
        monkeypatch.setattr(fake, name, forbidden)
    report = generation_job_status(runner, "gen-1", ledger)
    assert report["source_kind"] == "A04_GENERATION_V2_SYNTHETIC"
    assert report["request_digest"] == request.digest
    assert report["additional_provider_calls"] is False
    assert report["historical_provider_charge_verified"] is False


def test_a15_cli_reads_existing_registry_without_new_job(registry, capsys):
    manifest = LocalMediaRunner(registry).run(MediaRequest(mode="dry-run"), job_id="cli-job")
    assert a15_cli_main(["--registry", str(registry.root), "--job-id", "cli-job"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["job_id"] == manifest.job_id
    assert result["publication_status"] == "NOT_ATTEMPTED"
    assert result["money_movement_verified"] is False
    assert result["additional_provider_calls"] is False
