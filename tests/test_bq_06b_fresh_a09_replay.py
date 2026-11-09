"""BQ-06b independent tests using real native A09 imports/A11 pins/A17 handoff.

Synthetic media/account observations remain LOCAL, never provider attested.
No historical R1/R2 candidate or WorkOwner artifacts are reused.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import SnapshotPin, read_snapshot
from ai_commerce.creative_quality import behavioral_learning_bridge as a06
from ai_commerce.creative_quality import behavioral_learning_fresh_replay as b06
from test_bq_06a_behavioral_learning import source_case, attach_proposal
from test_a17_variants import file_bytes


@pytest.fixture(autouse=True)
def native_windows():
    assert os.name == "nt", "BQ06B_NATIVE_WINDOWS_REQUIRED"


def actual_case(tmp_path, *, source_kind="SYNTHETIC_FIXTURE"):
    args, advisory, consumer, learning, a09 = source_case(
        tmp_path, complete=True, source_kind=source_kind)
    attach_proposal(args, advisory, consumer)
    parent = AnalyticsLedger(a09).history(plan=AnalyticsLedger(a09).inspect(
        advisory.to_dict()["entries"][-1]["entry"]["observation_sources"][0][
            "observation_ref"]["import_id"])["import"]["plan"])
    assert parent["count"] == 1
    record = parent["imports"][0]
    pin = SnapshotPin(
        record["import_id"], record["context_sha256"], record["raw_sha256"],
        tuple(o["observation_id"] for o in AnalyticsLedger(a09).inspect(
            record["import_id"])["observations"])
    )
    review = a06.review_learning(**args)
    assert review.to_dict()["status"] == "EVIDENCE_REVIEW_FOR_BRAIN"
    return args, advisory, learning, AnalyticsLedger(a09), pin, review


def run_fresh(args, ledger, pin, review):
    return b06.review_fresh_evidence(
        ledger_path=str(ledger.path), pin=pin, bq06a_raw=review.raw,
        source_arguments=args,
    ).to_dict()


def make_root_or_correction(ledger, pin, tmp_path, *, parent=None, number=99):
    old = ledger.inspect(pin.import_id)["import"]
    raw = json.loads(ledger.raw_bytes(old["raw_sha256"]))
    assert raw["schema_version"] == "ManualAnalyticsRows.v1"
    raw["rows"][0]["value"] = number
    raw["rows"][0]["status"] = "AVAILABLE"
    file = tmp_path / ("root" if parent is None else "correction") / f"{number}.json"
    file.parent.mkdir(exist_ok=True)
    file.write_bytes(canonical_bytes(raw))
    options = {
        "plan": old["plan"], "profile": old["mapping"],
        "file_format": old["format"], "source_kind": old["source_kind"],
    }
    if parent is not None:
        options["correction_of"] = parent
    return ledger.import_file(file, **options)["import"]


def reprocess(ledger, parent):
    rec = ledger.inspect(parent)["import"]
    mapping = deepcopy(rec["mapping"])
    mapping["mapping_version"] = "bq06b-independent-reprocess-2"
    return ledger.reprocess(parent, plan=rec["plan"], profile=mapping)["import"]


def test_native_a09_a11_a17_fresh_replay_preserves_zero_null_counterevidence(tmp_path):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    before = file_bytes(tmp_path)
    result = run_fresh(args, ledger, pin, review)
    assert result["status"] == b06.CURRENT, result
    assert result["reason_codes"] == []
    # A11 retains only independently selected observation refs. The original
    # A09 import has four observations, but the actual selected A11 revision
    # may use fewer; every selected ref has already been matched in full by
    # the native reviewer. Never assert a fabricated all-import selection.
    selected_facts = advisory.to_dict()["entries"][-1]["entry"]["observation_sources"]
    assert 0 < len(selected_facts) < len(ledger.inspect(pin.import_id)["observations"])
    assert result["selected_observation_count"] == len(selected_facts)
    assert result["history_count"] == 1
    assert result["source_kind"] == "SYNTHETIC_FIXTURE"
    assert result["verification_scope"] == "LOCAL_A09_RAW_AND_A11_ARCHIVE_ONLY"
    assert result["provider_attested"] is False
    assert result["brain_decision"] is None
    assert result["human_adoption"] is None
    assert result["causal_lift"] is None and result["roi"] is None
    assert result["writes_performed"] is False
    rows = ledger.inspect(pin.import_id)["observations"]
    assert any(o["value"] == 0 and o["status"] == "AVAILABLE" for o in rows)
    assert any(o["value"] is None and o["status"] == "NOT_AVAILABLE" for o in rows)
    assert any(o["role"] == "COUNTEREVIDENCE" for o in
               advisory.to_dict()["entries"][-1]["entry"]["evidence"])
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("source_kind", [
    "USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT",
])
def test_human_supplied_source_never_becomes_provider_attested(tmp_path, source_kind):
    args, advisory, learning, ledger, pin, review = actual_case(
        tmp_path, source_kind=source_kind)
    result = run_fresh(args, ledger, pin, review)
    assert result["status"] == b06.CURRENT, result
    assert result["source_kind"] == source_kind
    assert result["provider_attested"] is False


def test_legitimate_correction_child_causes_stale_parent_hold(tmp_path):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    child = make_root_or_correction(ledger, pin, tmp_path, parent=pin.import_id, number=13)
    assert child["lineage"] == {"relationship": "CORRECTION",
                                 "parent_import_id": pin.import_id}
    assert read_snapshot(ledger.path, pin=pin).import_id == pin.import_id
    before = file_bytes(tmp_path)
    result = run_fresh(args, ledger, pin, review)
    assert result["status"] == b06.HOLD
    assert result["reason_codes"] == ["A09_LINEAGE_STALE_OR_CORRECTED"]
    assert file_bytes(tmp_path) == before


def test_legitimate_native_reprocess_child_causes_parent_hold(tmp_path):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    child = reprocess(ledger, pin.import_id)
    assert child["lineage"] == {"relationship": "REPROCESS",
                                 "parent_import_id": pin.import_id}
    assert read_snapshot(ledger.path, pin=pin).import_id == pin.import_id
    before = file_bytes(tmp_path)
    result = run_fresh(args, ledger, pin, review)
    assert result["reason_codes"] == ["A09_LINEAGE_STALE_OR_CORRECTED"]
    assert file_bytes(tmp_path) == before


def test_two_hop_correction_then_reprocess_invalidates_original_pin(tmp_path):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    child = make_root_or_correction(ledger, pin, tmp_path, parent=pin.import_id, number=18)
    grandchild = reprocess(ledger, child["import_id"])
    assert grandchild["lineage"]["parent_import_id"] == child["import_id"]
    assert [r["lineage"]["relationship"] for r in
            ledger.history(plan=ledger.inspect(pin.import_id)["import"]["plan"])["imports"]
           ] == ["ORIGINAL", "CORRECTION", "REPROCESS"]
    result = run_fresh(args, ledger, pin, review)
    assert result["reason_codes"] == ["A09_LINEAGE_STALE_OR_CORRECTED"]


def test_unrelated_valid_root_in_same_scope_does_not_supersede_pin(tmp_path):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    other = make_root_or_correction(ledger, pin, tmp_path, number=28)
    assert other["lineage"] == {"relationship": "ORIGINAL", "parent_import_id": None}
    result = run_fresh(args, ledger, pin, review)
    assert result["status"] == b06.CURRENT, result
    assert result["history_count"] == 2


def test_over_100_native_imports_paginate_to_full_history(tmp_path):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    for number in range(100, 204):
        make_root_or_correction(ledger, pin, tmp_path, number=number)
    assert ledger.history(plan=ledger.inspect(pin.import_id)["import"]["plan"],
                          limit=100)["next_offset"] == 100
    result = run_fresh(args, ledger, pin, review)
    assert result["status"] == b06.CURRENT, result
    assert result["history_count"] == 105


def test_history_over_cap_or_stalled_cursor_remains_hold(tmp_path, monkeypatch):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    def stalled_history(self, **kwargs):
        return {"imports": [], "count": 0, "next_offset": 0}
    monkeypatch.setattr(AnalyticsLedger, "history", stalled_history)
    result = run_fresh(args, ledger, pin, review)
    assert result["status"] == b06.HOLD
    assert result["reason_codes"] == ["HISTORY_UNPROVEN"]


def test_new_native_a09_child_requires_independently_reissued_a11_pins(tmp_path):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    child = make_root_or_correction(ledger, pin, tmp_path,
                                     parent=pin.import_id, number=55)
    result = run_fresh(args, ledger, pin, review)
    assert result["reason_codes"] == ["A09_LINEAGE_STALE_OR_CORRECTED"]
    native = ledger.inspect(child["import_id"])
    newpin = SnapshotPin(child["import_id"], child["context_sha256"],
                         child["raw_sha256"],
                         tuple(o["observation_id"] for o in native["observations"]))
    # A valid native child is not enough: the old A11 latest revision remains
    # bound to the original import. Never silently re-pin the receiver.
    assert run_fresh(args, ledger, newpin, review)["reason_codes"] == [
        "A11_A09_OBSERVATION_REF_MISMATCH"]


@pytest.mark.parametrize("mutation", ["wrong_context", "wrong_raw", "missing_a17",
                                      "wrong_revision", "wrong_metric", "fake_raw"])
def test_stale_or_forged_pins_fail_closed(tmp_path, mutation):
    args, advisory, learning, ledger, pin, review = actual_case(tmp_path)
    bad = dict(args)
    newpin = pin
    raw = review.raw
    if mutation == "wrong_context":
        newpin = SnapshotPin(pin.import_id, "f" * 64, pin.raw_sha256,
                             pin.observation_ids)
    elif mutation == "wrong_raw":
        newpin = SnapshotPin(pin.import_id, pin.context_sha256, "e" * 64,
                             pin.observation_ids)
    elif mutation == "missing_a17":
        bad["a17"] = None
    elif mutation == "wrong_revision":
        bad["expected_revision"] = 1
    elif mutation == "wrong_metric":
        bad["expected_metric"] = {**args["expected_metric"],
                                  "definition_version": "forged"}
    else:
        raw = b"{}"
    result = run_fresh(bad, ledger, newpin, a06.BQ06aLearningBriefReview(raw)
                       if mutation != "fake_raw" else type("Raw", (), {"raw": raw})())
    assert result["status"] == b06.HOLD
    assert result["reason_codes"]


def test_public_result_digest_is_identity_only_and_unknown_fields_rejected(tmp_path):
    args, _, _, ledger, pin, review = actual_case(tmp_path)
    result = b06.review_fresh_evidence(
        ledger_path=str(ledger.path), pin=pin, bq06a_raw=review.raw,
        source_arguments=args)
    assert result.raw == b06.review_fresh_evidence(
        ledger_path=str(ledger.path), pin=pin, bq06a_raw=review.raw,
        source_arguments=args).raw
    changed = result.to_dict()
    changed["provider_attested"] = True
    with pytest.raises(ValueError, match="REVIEW_DIGEST_MISMATCH"):
        b06.BQ06bFreshEvidenceReview(canonical_bytes(changed))


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sqlite_sidecars_fail_closed_without_writing(tmp_path, suffix):
    args, _, _, ledger, pin, review = actual_case(tmp_path)
    sidecar = Path(str(ledger.path) + suffix)
    sidecar.write_bytes(b"untrusted")
    before = file_bytes(tmp_path)
    result = run_fresh(args, ledger, pin, review)
    assert result["status"] == b06.HOLD
    assert file_bytes(tmp_path) == before


def test_unsafe_linked_a09_path_cannot_upgrade_native_source(tmp_path):
    args, _, _, ledger, pin, review = actual_case(tmp_path)
    linked = tmp_path / "a09-link.sqlite"
    try:
        os.link(ledger.path, linked)
    except OSError as exc:
        pytest.skip("NATIVE_HARDLINK_CAPABILITY_UNPROVEN: " + type(exc).__name__)
    result = b06.review_fresh_evidence(
        ledger_path=str(linked), pin=pin, bq06a_raw=review.raw,
        source_arguments=args).to_dict()
    assert result["status"] == b06.HOLD
    assert result["provider_attested"] is False
