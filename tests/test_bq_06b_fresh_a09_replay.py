"""Native disposable Windows A09 fresh replay pinned to A11 -> BQ-06b tests.

No provider effects, no unverified claims. All link tests must be strict.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.analytics.schemas import CONTEXT, VERIFICATION
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.creative_quality import (
    behavioral_learning_bridge as bridge,
    behavioral_learning_fresh_replay as fresh,
)
from ai_commerce.creative_scaling import propose_next_creative
from ai_commerce.learning import (
    LearningRegistry,
    SnapshotPin,
    build_performance_report,
    export_advisory,
    read_snapshot,
)
from ai_commerce.learning.core import digest
from ai_commerce.engineering import impact_v21, minimum_test
from test_a17_feedback import learning_case, proposal_args, seal_export
from test_a17_variants import file_bytes

ROOT = Path(__file__).resolve().parents[1]
WI = "work_items/BQ-06B_FRESH_A09_REPLAY_001.json"
MANIFEST = "work_items/mt01-components/bq-06b.json"


@pytest.fixture(autouse=True)
def native_windows():
    assert os.name == "nt", "NATIVE_WINDOWS_REQUIRED_HOLD"


def source_case(tmp_path, *, complete=True, source_kind="SYNTHETIC_FIXTURE", **options):
    advisory, consumer, learning, a09 = learning_case(tmp_path, source_kind=source_kind, **options)
    first = learning.inspect()[0].to_dict()
    ref = first["observation_sources"][0]["observation_ref"]
    imported = AnalyticsLedger(a09).inspect(ref["import_id"])
    pin = SnapshotPin(
        ref["import_id"],
        ref["context_sha256"],
        ref["raw_sha256"],
        tuple(o["observation_id"] for o in imported["observations"]),
    )
    source = read_snapshot(a09, pin=pin)

    if complete:
        retained = LearningRegistry(tmp_path / "complete-a11.sqlite")
        for entry in learning.inspect()[:2]:
            value = entry.to_dict()
            reports = (
                []
                if not options.get("with_report")
                else [build_performance_report(source, denominator_value=options.get("denominator_value"))]
            )
            retained.append(
                **{k: value[k] for k in ("hypothesis", "evidence", "record", "revision")},
                snapshots=[source],
                reports=reports,
            )
        learning = retained
        advisory = export_advisory(learning)
        consumer.update(expected_export_id=advisory.export_id, expected_revision=2)

    value = advisory.to_dict()["entries"][-1]["entry"]
    args = {
        "registry_path": str(learning.path),
        "expected_export_id": advisory.export_id,
        "learning_key": consumer["learning_key"],
        "expected_revision": consumer["expected_revision"],
        "expected_context": {k: value["hypothesis"][k] for k in CONTEXT},
        "expected_metric": value["observation_sources"][0]["metric"],
        "a09_ledger_path": str(a09),
        "a09_pin": pin,
    }
    proposal = attach_proposal(args, advisory, consumer)
    return args, advisory, consumer, learning, a09, pin, proposal


def attach_proposal(args, advisory, consumer):
    proposal = propose_next_creative(consumer["batch"], **proposal_args(advisory, consumer))
    args["a17"] = {
        "registry_root": str(consumer["registry"].root),
        "batch_id": consumer["batch"]["batch_id"],
        "source_id": consumer["source_id"],
        "proposal_id": proposal["proposal_id"],
        "expected_proposal_sha256": proposal["record_sha256"],
        "opt_in": True,
    }
    return proposal


def reject(args):
    result = fresh.review_fresh_replay(**args)
    assert result.to_dict()["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert result.to_dict()["missing_requirements"]
    assert result.to_dict()["flags"] == fresh.FLAGS
    return result


# --- Positive AC: BQ-06b.a09-raw-replay-pinned-to-a11 ---

def test_a09_raw_replay_pinned_to_a11_complete_match(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    before = file_bytes(tmp_path)
    result = fresh.review_fresh_replay(**args)
    value = result.to_dict()
    assert value["status"] == "LOCAL_A09_RAW_REPLAY"
    assert value["replay_type"] == "LOCAL_A09_RAW_REPLAY"
    assert value["a09_replay"] is not None
    assert value["a09_replay"]["import_id"] == pin.import_id
    assert value["a09_replay"]["matched_observation_count"] == 2
    assert value["a09_replay"]["observation_count"] == 4
    assert len(value["a09_replay"]["replayed_observations"]) == 2
    assert value["bq06a"]["status"] == "EVIDENCE_REVIEW_FOR_BRAIN"
    assert value["flags"] == fresh.FLAGS
    assert value["limits"] == fresh.LIMITS
    assert Draft202012Validator(fresh.fresh_review_schema()).is_valid(value)

    # Replay verification
    rechecked = fresh.recheck_fresh_replay(result.raw, **args)
    assert rechecked.raw == result.raw
    assert file_bytes(tmp_path) == before


def test_a09_replay_exact_selected_history_subset(tmp_path):
    # Setup where A11 only selected a subset of A09 observations
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    result = fresh.review_fresh_replay(**args)
    value = result.to_dict()
    assert value["status"] == "LOCAL_A09_RAW_REPLAY"
    obs = value["a09_replay"]["replayed_observations"]
    # Replayed observations contain only selected evidence observations
    assert len(obs) == value["a09_replay"]["matched_observation_count"]
    for item in obs:
        assert item["observation_id"].startswith("obs-")
        assert len(item["observation_ref_sha256"]) == 64
        assert item["source_status"] == VERIFICATION


def test_a09_read_snapshot_preserves_path_and_wal_controls(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    snapshot = read_snapshot(a09, pin=pin)
    assert snapshot.import_id == pin.import_id
    assert snapshot.to_dict()["verification_scope"] == "LOCAL_HASH_AND_A09_REPLAY_ONLY"
    assert snapshot.to_dict()["source_status"] == VERIFICATION


# --- Positive AC: BQ-06b.missingness-and-comparability ---

def test_source_zero_vs_missing_distinct_and_preserved(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    result = fresh.review_fresh_replay(**args)
    value = result.to_dict()
    obs = value["a09_replay"]["replayed_observations"]
    available_zeros = [o for o in obs if o["state"] == "AVAILABLE" and o["value"] == 0]
    assert len(available_zeros) == 1
    assert available_zeros[0]["value"] == 0
    assert type(available_zeros[0]["value"]) is int

    # Missing is distinct and preserved as null in the underlying snapshot
    snap_obs = read_snapshot(a09, pin=pin).to_dict()["observations"]
    missing_obs = [o for o in snap_obs if o["status"] == "NOT_AVAILABLE"]
    assert len(missing_obs) == 1
    assert missing_obs[0]["value"] is None

    # When incomplete revision with missing evidence is evaluated, reports hold
    args_raw, *_ = source_case(tmp_path / "raw_inc", complete=False)
    res_inc = fresh.review_fresh_replay(**args_raw)
    assert res_inc.to_dict()["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert "LATEST_EVIDENCE_INCOMPLETE" in res_inc.to_dict()["missing_requirements"]


def test_counterevidence_and_corrections_preserved_without_invented_comparison(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    result = fresh.review_fresh_replay(**args)
    value = result.to_dict()
    assert value["association"] == "UNBOUND"
    assert value["comparability"] == "NOT_COMPARABLE"
    assert value["counterexamples"] == fresh.COUNTEREXAMPLES
    history = value["bq06a"]["archive"]["history"]
    latest = history[-1]["observations"]
    assert any(o["role"] == "COUNTEREVIDENCE" for o in latest)


# --- Positive AC: BQ-06b.brain-review-non-authoritative ---

def test_brain_review_accepts_only_bq06a_readonly_advisory_replay(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    result = fresh.review_fresh_replay(**args)
    value = result.to_dict()
    assert value["bq06a"] is not None
    assert value["bq06a"]["status"] == "EVIDENCE_REVIEW_FOR_BRAIN"
    assert value["questions"] == fresh.QUESTIONS
    assert value["testable_hypotheses"] == fresh.HYPOTHESES


def test_archived_a11_only_mode_without_a09_replay(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    args["a09_ledger_path"] = None
    args["a09_pin"] = None
    before = file_bytes(tmp_path)
    result = fresh.review_fresh_replay(**args)
    value = result.to_dict()
    assert value["status"] == "ARCHIVED_A11_ONLY"
    assert value["replay_type"] == "ARCHIVED_A11_ONLY"
    assert value["a09_replay"] is None
    assert value["bq06a"]["status"] == "EVIDENCE_REVIEW_FOR_BRAIN"
    assert file_bytes(tmp_path) == before


def test_supplied_provenance_never_upgraded_to_provider(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    result = fresh.review_fresh_replay(**args)
    value = result.to_dict()
    assert value["flags"]["provenance"] == "LOCAL_SUPPLIED_OR_SYNTHETIC_ONLY"
    assert value["limits"]["provider_attested"] is False
    assert value["a09_replay"]["provider_attested"] is False
    assert value["a09_replay"]["source_status"] == VERIFICATION
    for o in value["a09_replay"]["replayed_observations"]:
        assert o["provenance"] in ("SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED")
        assert o["source_status"] == VERIFICATION


def test_mt01_new_owner_has_real_registered_consumer_closure():
    work = json.loads((ROOT / WI).read_text(encoding="utf-8"))
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text(encoding="utf-8"))
    files = minimum_test.file_hashes(ROOT)
    base = {p: d for p, d in files.items() if p not in work["add_only_paths"]}
    plan = impact_v21.registered_action_plan(
        repo=ROOT,
        base_sha=work["source_sha"],
        base_files=base,
        candidate_files=files,
        changed_paths=work["allowed_paths"],
        required_acs=work["acceptance"],
        fallback_argv=profiles["mt01-component-onboarding"],
        groups=minimum_test.approved_groups(),
        registered_profiles=profiles,
        allowed_paths=work["allowed_paths"],
    )
    assert not plan["fallback"] and not plan["impact_v21"]["missing_mapping"], plan
    assert {"a09", "a11", "a17-1", "bq-01a", "bq-05a", "bq-05b0", "b0-4", "bq-06a"} <= set(plan["selected_groups"])
    new = next(g for g in plan["groups"] if g["group"] == "bq-06b")
    assert new["binding"]["registered_profile"] == "mt01-component-onboarding"
    assert new["binding"]["argv"] == ["python", "-m", "pytest", "tests/test_bq_06b_fresh_a09_replay.py", "-q", "-n", "12"]
    manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["acs"] == work["acceptance"] and len(manifest["acs"]) == 3
    assert manifest["sources"] == work["add_only_paths"][:1]
    assert manifest["tests"] == work["add_only_paths"][1:2]


# --- Negative ACs ---

@pytest.mark.parametrize("mutation", ["context_sha256", "raw_sha256", "import_id"])
def test_mismatched_a09_context_raw_sha_or_import_id_rejected(tmp_path, mutation):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    d = pin.to_dict()
    if mutation == "import_id":
        d["import_id"] = "a09-" + "0" * 64
    else:
        d[mutation] = "0" * 64
    args["a09_pin"] = d
    before = file_bytes(tmp_path)
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("mutation", ["reorder", "delete", "duplicate"])
def test_reordered_deleted_or_duplicated_observation_ids_hold(tmp_path, mutation):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    d = pin.to_dict()
    if mutation == "reorder":
        d["observation_ids"] = list(reversed(d["observation_ids"]))
    elif mutation == "delete":
        d["observation_ids"] = d["observation_ids"][:-1]
    else:
        d["observation_ids"] = [d["observation_ids"][0]] + d["observation_ids"]
    args["a09_pin"] = d
    before = file_bytes(tmp_path)
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    assert file_bytes(tmp_path) == before


def test_stale_a11_selected_revision_or_source_refs_hold(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    args["expected_revision"] = 1  # Stale revision relative to latest revision 2
    before = file_bytes(tmp_path)
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("mutation", ["corrupt", "truncate", "replace"])
def test_after_the_fact_replacement_of_a09_raw_or_ledger_fails(tmp_path, mutation):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    path = Path(a09)
    if mutation == "corrupt":
        path.write_bytes(b"not a valid sqlite database")
    elif mutation == "truncate":
        path.write_bytes(path.read_bytes()[:128])
    else:
        path.write_bytes(Path(learning.path).read_bytes())
    before = file_bytes(tmp_path)
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "field,value,expected_reason",
    [
        ("metric_key", "different_key", "METRIC_DEFINITION_DRIFT"),
        ("definition_version", "different_version", "METRIC_DEFINITION_DRIFT"),
        ("unit", "different_unit", "METRIC_DEFINITION_DRIFT"),
        ("denominator", "different_denominator", "DENOMINATOR_DRIFT"),
        ("cohort", "different_cohort", "COHORT_DRIFT"),
        ("timezone", "UTC+09:00", "TIMEZONE_DRIFT"),
        ("measurement_window", ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"], "WINDOW_DRIFT"),
        ("account_ref", {"namespace": "internal_alias", "id": "a11-account-" + "0" * 64}, "ACCOUNT_DRIFT"),
    ],
)
def test_conflicting_account_window_timezone_metric_version_denominator_cohort_drift_holds(
    tmp_path, field, value, expected_reason
):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    if field in ("metric_key", "definition_version", "unit", "denominator"):
        args["expected_metric"] = {**args["expected_metric"], field: value}
    else:
        args["expected_context"] = {**args["expected_context"], field: value}
    res = reject(args)
    assert expected_reason in res.to_dict()["missing_requirements"]


def test_source_correcting_reprocessing_old_imports_holds(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    db = sqlite3.connect(a09)
    try:
        for op in ("UPDATE", "DELETE"):
            db.execute(f"DROP TRIGGER IF EXISTS imports_no_{op.lower()}")
        row = db.execute("SELECT payload FROM imports WHERE import_id = ?", (pin.import_id,)).fetchone()[0]
        data = json.loads(row.decode("utf-8"))
        data["lineage"] = [{"step": 1, "action": "CORRECTED", "target": "old_import"}]
        db.execute("UPDATE imports SET payload = ? WHERE import_id = ?", (canonical_bytes(data), pin.import_id))
        db.commit()
    finally:
        db.close()
    before = file_bytes(tmp_path)
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    missing = res.to_dict()["missing_requirements"]
    assert "A09_LINEAGE_STALE_OR_CORRECTED" in missing or "A09_SNAPSHOT_MISMATCH" in missing
    assert file_bytes(tmp_path) == before


def test_missing_zero_conflation_rejected(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    db = sqlite3.connect(a09)
    try:
        for op in ("UPDATE", "DELETE"):
            db.execute(f"DROP TRIGGER IF EXISTS observations_no_{op.lower()}")
        rows = db.execute("SELECT observation_id, payload FROM observations WHERE import_id = ?", (pin.import_id,)).fetchall()
        for obs_id, payload in rows:
            data = json.loads(payload.decode("utf-8"))
            if data["status"] == "NOT_AVAILABLE":
                data["value"] = 0
                db.execute("UPDATE observations SET payload = ? WHERE observation_id = ?", (canonical_bytes(data), obs_id))
                break
        db.commit()
    finally:
        db.close()
    before = file_bytes(tmp_path)
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    assert file_bytes(tmp_path) == before


def test_same_creative_cohort_display_name_independent_origin_rejected(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    db = sqlite3.connect(learning.path)
    try:
        db.execute("DROP TRIGGER a11_no_delete")
        db.execute("DROP TRIGGER a11_no_update")
        row = db.execute("SELECT payload FROM a11_events WHERE seq=2").fetchone()[0]
        data = json.loads(row.decode("utf-8"))
        data["hypothesis"]["creative_ref"]["content_sha256"] = "0" * 64
        db.execute("UPDATE a11_events SET payload = ? WHERE seq=2", (canonical_bytes(data),))
        db.commit()
    finally:
        db.close()
    before = file_bytes(tmp_path)
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    assert file_bytes(tmp_path) == before


def test_forged_archive_export_rehashed_without_receiver_pin_fails(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    value = advisory.to_dict()
    value["entries"][-1]["entry"]["hypothesis"]["statement"] = "forged"
    raw = canonical_bytes(seal_export(value))
    path = tmp_path / "forged_export.json"
    path.write_bytes(raw)
    res = reject({**args, "registry_path": None, "archive_path": str(path)})
    assert res.to_dict()["a09_replay"] is None


def test_native_windows_hardlink_denied_before_a09_or_a11(tmp_path, monkeypatch):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    path = Path(args["a09_ledger_path"])
    os.link(path, tmp_path / "hardlink_alias.sqlite")
    assert path.stat().st_nlink == 2, "NATIVE_HARDLINK_CONSTRUCTION_HOLD"
    monkeypatch.setattr(AnalyticsLedger, "inspect", lambda *a: pytest.fail("hardlink reached A09"))
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None


def test_native_windows_junction_denied_before_a09_or_a11(tmp_path, monkeypatch):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    link = tmp_path / "junction_dir"
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(tmp_path)], capture_output=True, timeout=15)
    assert proc.returncode == 0, "NATIVE_JUNCTION_CONSTRUCTION_HOLD"
    try:
        assert link.lstat().st_file_attributes & 0x400
        monkeypatch.setattr(AnalyticsLedger, "inspect", lambda *a: pytest.fail("junction reached A09"))
        res = reject({**args, "a09_ledger_path": str(link / Path(args["a09_ledger_path"]).name)})
        assert res.to_dict()["a09_replay"] is None
    finally:
        os.rmdir(link)


def test_native_windows_symlink_denied_before_a09_or_a11(tmp_path, monkeypatch, record_property):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    link = tmp_path / "symlink_a09.sqlite"
    try:
        os.symlink(args["a09_ledger_path"], link)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            record_property("native_symlink", "NATIVE_SYMLINK_CREATION_UNPROVEN")
            record_property("NATIVE_SYMLINK_CREATION_UNPROVEN", True)
            pytest.skip("NATIVE_SYMLINK_CREATION_UNPROVEN")
        raise
    assert link.is_symlink(), "NATIVE_SYMLINK_CONSTRUCTION_HOLD"
    monkeypatch.setattr(AnalyticsLedger, "inspect", lambda *a: pytest.fail("symlink reached A09"))
    res = reject({**args, "a09_ledger_path": str(link)})
    assert res.to_dict()["a09_replay"] is None


def test_native_windows_write_and_rename_race_denied_during_owner_replay(tmp_path, monkeypatch):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    original_inspect = AnalyticsLedger.inspect
    observed = []

    def racing_inspect(ledger, import_id):
        for op in (
            lambda: open(ledger.path, "r+b"),
            lambda: os.replace(ledger.path, str(ledger.path) + ".moved"),
        ):
            with pytest.raises(OSError):
                op()
            observed.append(True)
        return original_inspect(ledger, import_id)

    monkeypatch.setattr(AnalyticsLedger, "inspect", racing_inspect)
    before = file_bytes(tmp_path)
    result = fresh.review_fresh_replay(**args)
    assert result.to_dict()["status"] == "LOCAL_A09_RAW_REPLAY"
    assert len(observed) == 2 and file_bytes(tmp_path) == before


@pytest.mark.parametrize("suffix", [":hidden", "\\..\\a09.sqlite", "\\.\\a09.sqlite"])
def test_native_ads_traversal_dot_path_denied(tmp_path, suffix):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    args["a09_ledger_path"] += suffix
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_nonquiescent_a09_or_a11_holds_without_owner_write(tmp_path, suffix):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    Path(args["a09_ledger_path"] + suffix).write_bytes(b"sidecar bytes")
    before = file_bytes(tmp_path)
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    assert file_bytes(tmp_path) == before


def test_native_wal_header_without_sidecars_holds_before_sqlite_open(tmp_path, monkeypatch):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    path = Path(args["a09_ledger_path"])
    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        db.close()
    assert path.read_bytes()[18:20] == b"\x02\x02"
    assert all(not Path(str(path) + s).exists() for s in ("-wal", "-shm", "-journal"))
    before = file_bytes(tmp_path)
    monkeypatch.setattr(AnalyticsLedger, "inspect", lambda *a: pytest.fail("WAL reached owner SQLite open"))
    res = reject(args)
    assert res.to_dict()["a09_replay"] is None
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "forged_context",
    [
        {"provider_attested": True},
        {"brain_authenticated": True},
        {"human_authorized": True},
        {"private_prompt": "secret"},
    ],
)
def test_caller_forged_human_brain_provider_rights_hold(tmp_path, forged_context):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    res = reject({**args, "context": forged_context})
    assert res.to_dict()["missing_requirements"] == ["UNSUPPORTED_REQUEST"]


@pytest.mark.parametrize(
    "unauthorized_kwargs",
    [
        {"budget": 1000},
        {"clickthrough": 0.05},
        {"profit": 500},
        {"lift": 0.12},
    ],
)
def test_budget_clickthrough_profit_lift_claims_rejected(tmp_path, unauthorized_kwargs):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    res = reject({**args, **unauthorized_kwargs})
    assert res.to_dict()["missing_requirements"] == ["UNSUPPORTED_REQUEST"]


def test_all_failures_leave_a09_a11_a17_pulse_file_bytes_unchanged(tmp_path):
    args, advisory, consumer, learning, a09, pin, proposal = source_case(tmp_path)
    before = file_bytes(tmp_path)
    reject({**args, "expected_revision": 999})
    reject({**args, "budget": 5000})
    reject({**args, "context": {"forged": True}})
    assert file_bytes(tmp_path) == before
