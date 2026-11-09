"""B3.2 negative and positive Windows-native Brain proposal handoff tests."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.creative_quality import b3_next_brief_handoff as b3
from ai_commerce.creative_quality import behavioral_learning_bridge as bq06a
from ai_commerce.creative_scaling import feedback
from ai_commerce.operations import ArtifactRegistry
from test_bq_06b_fresh_a09_replay import (
    actual_case, make_root_or_correction, reprocess,
)
from test_a17_variants import file_bytes


@pytest.fixture(autouse=True)
def windows_only():
    assert os.name == "nt", "B3_2_NATIVE_WINDOWS_REQUIRED"


def review(args, ledger, pin, review):
    return b3.review_next_brief(
        ledger_path=str(ledger.path), pin=pin,
        bq06a_raw=review.raw, source_arguments=args).to_dict()


def test_authentic_native_b3_next_brief_pending_source_and_uncertainty_no_writes(tmp_path, monkeypatch):
    args, advisory, learning, ledger, pin, old = actual_case(tmp_path)
    before = file_bytes(tmp_path)
    monkeypatch.setattr(feedback, "record_adoption",
                        lambda *a, **k: pytest.fail("B3 AUTO-ADOPTED"))
    monkeypatch.setattr(feedback, "propose_next_creative",
                        lambda *a, **k: pytest.fail("B3 CREATED A17"))
    result = review(args, ledger, pin, old)
    assert result["status"] == b3.READY, result
    assert result["reason_codes"] == [] and result["source_reason_codes"] == []
    proposal = result["proposal"]
    assert proposal["adoption"] == "PENDING"
    assert proposal["next_brief"]["ref"] and proposal["next_brief"]["sha256"]
    assert proposal["suggestion"] and proposal["hypothesis"]
    assert proposal["negative_evidence_ids"] and proposal["evidence_ids"]
    assert set(proposal["negative_evidence_ids"]) <= set(proposal["evidence_ids"])
    assert proposal["missing_data"] and "AFTER_WINDOW_NOT_OBSERVED" in proposal["missing_data"]
    assert result["comparison"]["after_status"] == "UNKNOWN"
    assert result["comparison"]["provider_metrics_verified"] is False
    assert result["comparison"]["comparison_verified"] is False
    assert result["source_kind"] == "SYNTHETIC_FIXTURE"
    for field in ("human_adoption", "brain_decision", "causal_lift", "roi"):
        assert result[field] is None
    assert result["provider_attested"] is False and result["writes_performed"] is False
    assert file_bytes(tmp_path) == before
    assert review(args, ledger, pin, old) == result


@pytest.mark.parametrize("source_kind", [
    "USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT",
])
def test_manual_supplied_source_does_not_promote_to_provider_result(tmp_path, source_kind):
    from test_bq_06a_behavioral_learning import source_case, attach_proposal
    from ai_commerce.learning import SnapshotPin
    args, advisory, consumer, learning, a09 = source_case(
        tmp_path, complete=True, source_kind=source_kind)
    attach_proposal(args, advisory, consumer)
    row = advisory.to_dict()["entries"][-1]["entry"]["observation_sources"][0]["observation_ref"]
    record = AnalyticsLedger(a09).inspect(row["import_id"])
    pin = SnapshotPin(row["import_id"], row["context_sha256"], row["raw_sha256"],
                      tuple(x["observation_id"] for x in record["observations"]))
    old = bq06a.review_learning(**args)
    val = review(args, AnalyticsLedger(a09), pin, old)
    assert val["status"] == b3.READY, val
    assert val["source_kind"] == source_kind
    assert val["provider_attested"] is False
    assert val["comparison"]["after_status"] == "UNKNOWN"


@pytest.mark.parametrize("child_kind", ["correction", "reprocess", "two_hop"])
def test_real_native_a09_supersession_holds_b3_handoff(tmp_path, child_kind):
    args, advisory, learning, ledger, pin, old = actual_case(tmp_path)
    if child_kind in ("correction", "two_hop"):
        child = make_root_or_correction(
            ledger, pin, tmp_path, parent=pin.import_id, number=57)
        if child_kind == "two_hop":
            reprocess(ledger, child["import_id"])
    else:
        reprocess(ledger, pin.import_id)
    before = file_bytes(tmp_path)
    value = review(args, ledger, pin, old)
    assert value["status"] == b3.HOLD
    assert value["reason_codes"] == ["SOURCE_EVIDENCE_NOT_CURRENT"]
    assert "A09_LINEAGE_STALE_OR_CORRECTED" in value["source_reason_codes"]
    assert value["proposal"] is None and value["brain_decision"] is None
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("mutation", [
    "no_opt_in", "wrong_proposal_sha", "wrong_revision",
    "wrong_metric", "stale_ledger_pin", "invalid_review", "missing_a17",
])
def test_tampered_or_stale_receiver_inputs_never_produce_next_brief(tmp_path, mutation):
    from ai_commerce.learning import SnapshotPin
    args, advisory, learning, ledger, pin, old = actual_case(tmp_path)
    args = deepcopy(args)
    raw = old.raw
    if mutation == "no_opt_in":
        args["a17"]["opt_in"] = False
    elif mutation == "wrong_proposal_sha":
        args["a17"]["expected_proposal_sha256"] = "f" * 64
    elif mutation == "wrong_revision":
        args["expected_revision"] = 1
    elif mutation == "wrong_metric":
        args["expected_metric"]["definition_version"] = "forged"
    elif mutation == "stale_ledger_pin":
        pin = SnapshotPin(pin.import_id, "f" * 64, pin.raw_sha256, pin.observation_ids)
    elif mutation == "invalid_review":
        raw = b"{}"
    else:
        args["a17"] = None
    result = b3.review_next_brief(
        ledger_path=str(ledger.path), pin=pin,
        bq06a_raw=raw, source_arguments=args).to_dict()
    assert result["status"] == b3.HOLD, result
    assert result["proposal"] is None and result["reason_codes"]
    assert result["writes_performed"] is False


def test_native_a17_human_revision_2_prevents_replaying_as_pending(tmp_path):
    args, advisory, learning, ledger, pin, old = actual_case(tmp_path)
    registry = ArtifactRegistry(args["a17"]["registry_root"])
    proposal = feedback.load_proposal(registry, args["a17"]["proposal_id"])
    # The test fixture separately simulates an authorized Human revision;
    # the B3 review itself must never call this writer.
    feedback.record_adoption(proposal, "DECLINED", registry=registry,
                             human_authorized=True, note="negative test fixture")
    before = file_bytes(tmp_path)
    result = review(args, ledger, pin, old)
    assert result["status"] == b3.HOLD and result["proposal"] is None
    assert result["human_adoption"] is None
    assert file_bytes(tmp_path) == before


def test_rehashed_forged_a17_hypothesis_cannot_become_b3_source(tmp_path):
    args, advisory, learning, ledger, pin, old = actual_case(tmp_path)
    root = Path(args["a17"]["registry_root"])
    source = root / feedback.proposal_ref(args["a17"]["proposal_id"])
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["suggestion"] = "Unverified new strategy claim"
    from ai_commerce.creative_scaling.schemas import seal
    payload = seal({k: v for k, v in payload.items() if k != "record_sha256"})
    source.write_bytes(canonical_bytes(payload))
    result = review(args, ledger, pin, old)
    assert result["status"] == b3.HOLD and result["proposal"] is None
    assert result["provider_attested"] is False


def test_native_mutation_between_initial_and_final_replays_is_detected(tmp_path, monkeypatch):
    args, advisory, learning, ledger, pin, old = actual_case(tmp_path)
    real = b3._read_pending_a17

    def race(options, a11, source_args):
        result = real(options, a11, source_args)
        make_root_or_correction(ledger, pin, tmp_path,
                                parent=pin.import_id, number=66)
        return result

    monkeypatch.setattr(b3, "_read_pending_a17", race)
    result = review(args, ledger, pin, old)
    assert result["status"] == b3.HOLD
    assert result["proposal"] is None


def test_closed_public_packet_identity_cannot_claim_brain_adoption(tmp_path):
    args, advisory, learning, ledger, pin, old = actual_case(tmp_path)
    value = b3.review_next_brief(
        ledger_path=str(ledger.path), pin=pin,
        bq06a_raw=old.raw, source_arguments=args)
    assert value.to_dict()["status"] == b3.READY
    mutated = value.to_dict()
    mutated["brain_decision"] = "ACCEPTED"
    with pytest.raises(ValueError, match="B3_PACKET_DIGEST_INVALID"):
        b3.B3NextBriefHandoff(canonical_bytes(mutated))
    mutated["record_sha256"] = b3.sha256(canonical_bytes(
        {k: v for k, v in mutated.items() if k != "record_sha256"})).hexdigest()
    with pytest.raises(ValueError, match="B3_PACKET_AUTHORITY_INVALID"):
        b3.B3NextBriefHandoff(canonical_bytes(mutated))
