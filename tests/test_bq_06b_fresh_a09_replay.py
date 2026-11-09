"""BQ06b CI-first registered native owner tests.

This suite uses disposable authentic owner imports/registry records. It never
executes a model, spends, publishes, or mutates any production owner.
"""
import os
from dataclasses import replace
from pathlib import Path

import pytest

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.learning import SnapshotPin
from ai_commerce.creative_quality import behavioral_learning_bridge as bq06a
from ai_commerce.creative_quality import behavioral_learning_fresh_replay as fresh
from test_bq_06a_behavioral_learning import source_case, attach_proposal
from test_a17_variants import file_bytes


def native_windows():
    if os.name != "nt":
        pytest.skip("WINDOWS_NATIVE_OWNER_REQUIRED_NOT_A_PASS")


def complete_case(tmp_path):
    native_windows()
    args, advisory, consumer, learning, a09 = source_case(tmp_path, complete=True)
    attach_proposal(args, advisory, consumer)
    old = bq06a.review_learning(**args)
    assert old.to_dict()["status"] == "EVIDENCE_REVIEW_FOR_BRAIN"
    ref = advisory.to_dict()["entries"][-1]["entry"]["observation_sources"][0]["observation_ref"]
    actual = AnalyticsLedger(a09).inspect(ref["import_id"])
    record = actual["import"]
    pin = SnapshotPin(
        record["import_id"], record["context_sha256"], record["raw_sha256"],
        tuple(o["observation_id"] for o in actual["observations"]),
    )
    return {
        "a09_path": str(a09),
        "snapshot_pin": pin,
        "bq06a_raw": old.raw,
        "source_arguments": args,
    }, record


def test_real_native_a09_a11_a17_receiver_replay_keeps_every_byte(tmp_path):
    params, record = complete_case(tmp_path)
    before = file_bytes(tmp_path)
    observed = fresh.review_fresh(**params)
    data = observed.to_dict()
    assert data["status"] == "EVIDENCE_REVIEW_FOR_BRAIN", data
    assert data["a09"]["verification_scope"] == "LOCAL_A09_RAW_REPLAY"
    assert data["a09"]["matched_selected_a11_refs"] > 0
    assert data["limits"]["causal_lift"] is None
    assert data["limits"]["provider_attested"] is False
    assert data["limits"]["authority"] == "NONE"
    assert fresh.recheck_fresh(observed.raw, **params).raw == observed.raw
    assert file_bytes(tmp_path) == before
    assert not any(Path(params["a09_path"] + suffix).exists()
                   for suffix in ("-wal", "-shm", "-journal"))


def test_authentic_native_reprocess_child_holds_archived_parent_pin(tmp_path):
    params, record = complete_case(tmp_path)
    ledger = AnalyticsLedger(params["a09_path"])
    profile = dict(record["mapping"])
    profile["mapping_version"] = "bq06b-native-reprocess-different-version"
    new = ledger.reprocess(record["import_id"], plan=record["plan"], profile=profile)
    assert new["import"]["lineage"] == {
        "relationship": "REPROCESS", "parent_import_id": record["import_id"],
    }
    before = file_bytes(tmp_path)
    observed = fresh.review_fresh(**params).to_dict()
    assert observed["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert observed["missing_requirements"] == ["A09_LINEAGE_STALE_OR_CORRECTED"], observed
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("field", ["context_sha256", "raw_sha256", "import_id"])
def test_independently_wrong_snapshot_pin_fails_closed(tmp_path, field):
    params, _ = complete_case(tmp_path)
    params["snapshot_pin"] = replace(params["snapshot_pin"], **{
        field: ("a09-" if field == "import_id" else "") + "f" * 64,
    })
    before = file_bytes(tmp_path)
    result = fresh.review_fresh(**params).to_dict()
    assert result["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert result["a09"] is None
    assert file_bytes(tmp_path) == before


def test_edited_archive_review_cannot_become_native_evidence(tmp_path):
    params, _ = complete_case(tmp_path)
    params["bq06a_raw"] = params["bq06a_raw"].replace(b"EVIDENCE_REVIEW_FOR_BRAIN", b"AUTHENTIC_PROVIDER_EVIDENCE")
    before = file_bytes(tmp_path)
    data = fresh.review_fresh(**params).to_dict()
    assert data["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert data["a09"] is None
    assert file_bytes(tmp_path) == before


def test_original_native_hardlink_fails_before_a09_read(tmp_path):
    params, _ = complete_case(tmp_path)
    original = Path(params["a09_path"])
    alias = tmp_path / "alias-of-native-A09.sqlite"
    os.link(original, alias)
    before = file_bytes(tmp_path)
    data = fresh.review_fresh(**params).to_dict()
    assert data["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert data["a09"] is None
    assert file_bytes(tmp_path) == before


def test_native_windows_symlink_fixture_is_not_false_security_pass(tmp_path, record_property):
    params, _ = complete_case(tmp_path)
    alias = tmp_path / "alias-link-native-A09.sqlite"
    try:
        os.symlink(params["a09_path"], alias)
    except OSError as exc:
        record_property("native_symlink_creation", "NATIVE_SYMLINK_CREATION_UNPROVEN")
        pytest.skip(f"NATIVE_SYMLINK_CREATION_UNPROVEN: {getattr(exc, 'winerror', None)}")
    before = file_bytes(tmp_path)
    params["a09_path"] = str(alias)
    data = fresh.review_fresh(**params).to_dict()
    assert data["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert data["a09"] is None
    assert file_bytes(tmp_path) == before


def test_forged_bq06a_a17_permissions_do_not_write(tmp_path):
    params, _ = complete_case(tmp_path)
    params["source_arguments"]["a17"]["opt_in"] = False
    before = file_bytes(tmp_path)
    data = fresh.review_fresh(**params).to_dict()
    assert data["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert data["limits"]["brain_decision"] == "NOT_MADE"
    assert file_bytes(tmp_path) == before


def test_bounded_history_bad_cursor_fails_closed_on_authentic_selection(tmp_path):
    params, record = complete_case(tmp_path)
    real_page = AnalyticsLedger(params["a09_path"]).history(plan=record["plan"], limit=100, offset=0)
    class BadCursor:
        def history(self, *, plan, limit, offset):
            if offset == 0:
                return {"imports": real_page["imports"], "count": real_page["count"],
                        "next_offset": 0}
            raise AssertionError("should not follow invalid cursor")
    with pytest.raises(fresh.FreshEvidenceHold, match="HISTORY_UNPROVEN"):
        fresh._history(BadCursor(), record)
