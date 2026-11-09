"""Native disposable Windows A09 -> A11 -> A17 and separate B0.4/Q4 evidence.

Link construction failures are failures, never skip-as-pass. No provider effects.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.analytics.schemas import CONTEXT
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.creative import brief_digest, intake_brief
from ai_commerce.creative_quality import behavioral, behavioral_learning_bridge as bridge
from ai_commerce.creative_quality import pulse_studio_effect_preflight as preflight
from ai_commerce.creative_scaling import propose_next_creative
from ai_commerce.learning import LearningRegistry, SnapshotPin, build_performance_report, export_advisory, read_snapshot
from ai_commerce.learning.core import digest
from ai_commerce.engineering import impact_v21, minimum_test
from test_a17_feedback import learning_case, proposal_args, seal_export
from test_a17_variants import file_bytes, offline
from test_bq_05a_pulse_studio_offline_bridge import journey, snapshot, case

ROOT = Path(__file__).resolve().parents[1]
WI = "work_items/BQ-06A_OFFLINE_LEARNING_001.json"
MANIFEST = "work_items/mt01-components/bq-06a.json"


@pytest.fixture(autouse=True)
def native_windows():
    assert os.name == "nt", "NATIVE_WINDOWS_REQUIRED_HOLD"


def source_case(tmp_path, *, complete=False, source_kind="SYNTHETIC_FIXTURE", **options):
    advisory, consumer, learning, a09 = learning_case(tmp_path, source_kind=source_kind, **options)
    if complete:
        # Rebuild two genuine revisions using the native replayed snapshot and
        # native append API, never fake a database or import caller JSON history.
        first = learning.inspect()[0].to_dict()
        ref = first["observation_sources"][0]["observation_ref"]
        imported = AnalyticsLedger(a09).inspect(ref["import_id"])
        pin = SnapshotPin(ref["import_id"], ref["context_sha256"], ref["raw_sha256"],
                          tuple(o["observation_id"] for o in imported["observations"]))
        source = read_snapshot(a09, pin=pin)
        retained = LearningRegistry(tmp_path / "complete-a11.sqlite")
        for entry in learning.inspect()[:2]:
            value = entry.to_dict()
            reports = [] if not options.get("with_report") else [build_performance_report(
                source, denominator_value=options.get("denominator_value"))]
            retained.append(**{k: value[k] for k in ("hypothesis", "evidence", "record", "revision")},
                            snapshots=[source], reports=reports)
        learning = retained
        advisory = export_advisory(learning)
        consumer.update(expected_export_id=advisory.export_id, expected_revision=2)
    value = advisory.to_dict()["entries"][-1]["entry"]
    args = {"registry_path": str(learning.path), "expected_export_id": advisory.export_id,
            "learning_key": consumer["learning_key"], "expected_revision": consumer["expected_revision"],
            "expected_context": {k: value["hypothesis"][k] for k in CONTEXT},
            "expected_metric": value["observation_sources"][0]["metric"]}
    return args, advisory, consumer, learning, a09


def attach_proposal(args, advisory, consumer):
    proposal = propose_next_creative(consumer["batch"], **proposal_args(advisory, consumer))
    args["a17"] = {"registry_root": str(consumer["registry"].root), "batch_id": consumer["batch"]["batch_id"],
        "source_id": consumer["source_id"], "proposal_id": proposal["proposal_id"],
        "expected_proposal_sha256": proposal["record_sha256"], "opt_in": True}
    return proposal


def reject(args):
    result = bridge.review_learning(**args)
    assert result.to_dict()["status"] == "INSUFFICIENT_EVIDENCE_HOLD"
    assert result.to_dict()["missing_requirements"]
    assert result.to_dict()["flags"] == bridge.FLAGS
    return result


def test_a11_sourced_evidence_lineage_zero_missing_counterevidence_readonly(tmp_path, monkeypatch):
    args, advisory, consumer, learning, a09 = source_case(tmp_path)
    before = file_bytes(tmp_path)
    monkeypatch.setattr(AnalyticsLedger, "_connect", lambda *a, **k: pytest.fail("fresh A09 replay claimed"))
    result = reject(args)
    value = result.to_dict()
    assert value["archive"]["export_id"] == advisory.export_id
    history = value["archive"]["history"]
    assert [e["revision"] for e in history] == [1, 2, 3]
    latest = history[-1]["observations"]
    assert sorted(o["value"] for o in latest if o["state"] == "AVAILABLE") == [-3, 0, 12]
    assert [o["value"] for o in latest if o["state"] == "NOT_AVAILABLE"] == [None]
    assert len([o for o in latest if o["role"] == "COUNTEREVIDENCE"]) == 2
    assert all(o["provenance"] == "SYNTHETIC_FIXTURE" for o in latest)
    assert history[-1]["confounders"] and history[-1]["counterevidence_refs"]
    assert value["limits"]["verification_scope"] == "ARCHIVED_LOCAL_HASH_CHAIN_ONLY"
    assert bridge.review_learning(**args).raw == result.raw
    assert bridge.recheck_review(result.raw, **args).raw == result.raw
    assert file_bytes(tmp_path) == before
    archive_path = tmp_path / "retained-export.json"
    archive_path.write_bytes(advisory.to_bytes())
    assert bridge.review_learning(**{**args, "registry_path": None, "archive_path": str(archive_path)}).raw == result.raw


def test_brain_handoff_actual_native_a17_pending_proposal_no_adoption(tmp_path, monkeypatch, record_property):
    args, advisory, consumer, learning, a09 = source_case(tmp_path, complete=True)
    proposal = attach_proposal(args, advisory, consumer)
    before = file_bytes(tmp_path)
    import ai_commerce.creative_scaling.feedback as feedback
    monkeypatch.setattr(feedback, "record_adoption", lambda *a, **k: pytest.fail("automatic adoption"))
    monkeypatch.setattr(feedback, "propose_next_creative", lambda *a, **k: pytest.fail("adapter wrote a proposal"))
    result = bridge.review_learning(**args)
    value = result.to_dict()
    assert value["status"] == "EVIDENCE_REVIEW_FOR_BRAIN", value
    assert value["a17"]["proposal_sha256"] == proposal["record_sha256"]
    assert value["a17"]["adoption"] == "PENDING" and value["a17"]["generation_performed"] is False
    assert value["limits"]["authority"] == "NONE" and value["limits"]["causal_lift"] is None
    assert value["association"] == "UNBOUND" and value["comparability"] == "NOT_COMPARABLE"
    assert result.raw == bridge.review_learning(**args).raw
    assert file_bytes(tmp_path) == before
    record_property("actual_a17_native_pending_proposal_review", True)
    record_property("no_auto_adoption_and_retained_bytes_unchanged", True)


@pytest.mark.parametrize("kind", ["USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT"])
def test_supplied_provenance_never_upgraded(tmp_path, kind):
    args, *_ = source_case(tmp_path, source_kind=kind)
    value = reject(args).to_dict()
    assert value["archive"] is not None
    assert all(o["provenance"] == "HUMAN_SUPPLIED" for h in value["archive"]["history"] for o in h["observations"])
    assert value["limits"]["provider_attested"] is False


@pytest.mark.parametrize("denominator", [0, 100])
def test_report_denominator_window_uncertainty_preserved(tmp_path, denominator):
    args, advisory, *_ = source_case(tmp_path, denominator="supplied exposures", denominator_value=denominator, with_report=True)
    value = reject(args).to_dict()
    assert value["archive"] is not None, value
    observations = value["archive"]["history"][-1]["observations"]
    assert all(o["report"]["denominator"] == denominator for o in observations)
    assert value["archive"]["context"]["measurement_window"] == args["expected_context"]["measurement_window"]
    assert value["archive"]["metric"]["definition_sha256"] == digest(args["expected_metric"])
    if denominator == 0:
        assert all(o["value"] is None for o in observations)
        assert any(o["source_value"] == 0 for o in observations)
    assert value["archive"]["history"][-1]["uncertainty_sha256"]


@pytest.mark.parametrize("field", ["definition_version", "unit", "denominator", "cohort", "metric_key"])
def test_mismatched_metric_definition_rejected(tmp_path, field):
    args, *_ = source_case(tmp_path)
    args["expected_metric"] = {**args["expected_metric"], field: "different"}
    assert reject(args).to_dict()["archive"] is None


@pytest.mark.parametrize("field,value", [("cohort", "different"), ("timezone", "UTC+07:00"),
    ("measurement_window", ["2026-10-03T00:00:00Z", "2026-10-05T00:00:00Z"]),
    ("account_ref", {"namespace": "internal_alias", "id": "raw-private-account"})])
def test_mismatched_context_rejected(tmp_path, field, value):
    args, *_ = source_case(tmp_path)
    args["expected_context"] = {**args["expected_context"], field: value}
    assert reject(args).to_dict()["archive"] is None


@pytest.mark.parametrize("mutation", ["suffix", "fabricated", "zero", "trust", "extra", "corrupt"])
def test_rehashed_fabricated_truncated_archive_cannot_replace_retained_pin(tmp_path, mutation):
    args, advisory, *_ = source_case(tmp_path)
    value = advisory.to_dict()
    if mutation == "suffix":
        value["entries"] = value["entries"][:1]
        value["event_count"] = 1
        value["head_event_hash"] = value["entries"][-1]["event_hash"]
    elif mutation == "fabricated": value["entries"][-1]["entry"]["hypothesis"]["statement"] = "fabricated"
    elif mutation == "zero":
        next(f for f in value["entries"][-1]["entry"]["observation_sources"] if f["value"] is None)["value"] = 0
    elif mutation == "trust": value["source_status"] = "PROVIDER_ATTESTED"
    elif mutation == "extra": value["brain_authenticated"] = True
    raw = canonical_bytes(seal_export(value)) if mutation != "corrupt" else b"{broken"
    path = tmp_path / "fake.json"
    path.write_bytes(raw)
    assert reject({**args, "registry_path": None, "archive_path": str(path)}).to_dict()["archive"] is None


@pytest.mark.parametrize("mutation", ["replace", "truncate", "corrupt", "rollback"])
def test_native_registry_replacement_and_rollback_fail_without_writes(tmp_path, mutation):
    args, advisory, consumer, learning, a09 = source_case(tmp_path)
    path = Path(learning.path)
    if mutation == "replace": path.write_bytes(a09.read_bytes())
    elif mutation == "truncate": path.write_bytes(path.read_bytes()[:512])
    elif mutation == "corrupt": path.write_bytes(b"not sqlite")
    else:
        with sqlite3.connect(path) as db:
            db.execute("DROP TRIGGER a11_no_delete")
            db.execute("DELETE FROM a11_events WHERE seq=3")
    before = file_bytes(tmp_path)
    assert reject(args).to_dict()["archive"] is None
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("mutation", ["source", "proposal-pin", "opt-in", "batch", "archive-revision", "composition"])
def test_stale_a17_source_revision_content_and_proposal_fail_closed(tmp_path, mutation):
    args, advisory, consumer, *_ = source_case(tmp_path, complete=True)
    attach_proposal(args, advisory, consumer)
    if mutation == "source": args["a17"]["source_id"] = consumer["batch"]["variants"][1]["variant_id"]
    elif mutation == "proposal-pin": args["a17"]["expected_proposal_sha256"] = "f" * 64
    elif mutation == "opt-in": args["a17"]["opt_in"] = False
    elif mutation == "batch": args["a17"]["batch_id"] = "missing"
    elif mutation == "archive-revision": args["expected_revision"] = 1
    else:
        files = list(consumer["registry"].root.glob("compositions/**/*.json"))
        assert files
        files[0].write_bytes(b"{}")
    before = file_bytes(tmp_path)
    reject(args)
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("context", [{}, {"provider_attested": True}, {"brain_authenticated": True},
    {"human_authorized": True}, {"private_prompt": "Bearer secret"}])
def test_caller_json_cannot_authenticate_any_authority(tmp_path, context, monkeypatch):
    args, *_ = source_case(tmp_path)
    monkeypatch.setattr(LearningRegistry, "inspect", lambda *a: pytest.fail("untrusted request reached A11"))
    result = reject({**args, "context": context})
    assert result.to_dict()["missing_requirements"] == ["UNSUPPORTED_REQUEST"]


@pytest.mark.parametrize("request_options", [{"association": "MATCHED_NAMES"}, {"effect": "PUBLISH"},
    {"effect": "SPEND"}, {"effect": "BRAIN_DECISION"}, {"a09_current": {"verified": True}}])
def test_unsupported_effect_association_and_fake_current_replay_hold(tmp_path, request_options):
    args, *_ = source_case(tmp_path)
    assert reject({**args, **request_options}).to_dict()["archive"] is None


def test_private_prose_account_prompt_not_exported_and_closed_replay(tmp_path):
    args, advisory, consumer, *_ = source_case(tmp_path, complete=True)
    proposal = attach_proposal(args, advisory, consumer)
    result = bridge.review_learning(**args)
    assert result.to_dict()["status"] == "EVIDENCE_REVIEW_FOR_BRAIN"
    for secret in (str(tmp_path), "fixture-account", "fixture-post", "fixture-human", proposal["suggestion"],
                   advisory.to_dict()["entries"][0]["entry"]["hypothesis"]["statement"]):
        assert secret.encode() not in result.raw
    value = result.to_dict()
    value["a17"]["source_sha256"] = "f" * 64
    body = {k: v for k, v in value.items() if k != "record_sha256"}
    forged = canonical_bytes({**body, "record_sha256": digest(body)})
    bridge.BQ06aLearningBriefReview(forged)  # identity alone is deliberately not authentication
    with pytest.raises(bridge.LearningBriefHold, match="CURRENT_OWNER_REPLAY_REQUIRED"):
        bridge.recheck_review(forged, **args)
    value["archive"]["history"][0]["observations"][0]["provider_attested"] = True
    body = {k: v for k, v in value.items() if k != "record_sha256"}
    with pytest.raises(bridge.LearningBriefHold, match="REVIEW_INVALID"):
        bridge.BQ06aLearningBriefReview(canonical_bytes({**body, "record_sha256": digest(body)}))
    assert Draft202012Validator(bridge.review_schema()).is_valid(result.to_dict())


def test_existing_human_adoption_is_never_relabelled_pending(tmp_path):
    from ai_commerce.creative_scaling import record_adoption
    args, advisory, consumer, *_ = source_case(tmp_path, complete=True)
    proposal = attach_proposal(args, advisory, consumer)
    adopted = record_adoption(proposal, "ACCEPTED", registry=consumer["registry"], human_authorized=True,
                              note="Disposable native test decision")
    assert adopted["adoption"]["decision"] == "ACCEPTED"
    before = file_bytes(tmp_path)
    assert reject(args).to_dict()["a17"] is None
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_nonquiescent_a11_registry_holds_without_owner_write(tmp_path, suffix):
    args, *_ = source_case(tmp_path)
    Path(args["registry_path"] + suffix).write_bytes(b"untrusted sidecar")
    before = file_bytes(tmp_path)
    assert reject(args).to_dict()["archive"] is None
    assert file_bytes(tmp_path) == before


def test_native_wal_header_without_sidecars_holds_before_sqlite_open(tmp_path, monkeypatch):
    args, *_ = source_case(tmp_path)
    path = Path(args["registry_path"])
    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        db.close()
    assert path.read_bytes()[18:20] == b"\x02\x02"
    assert all(not Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))
    before = file_bytes(tmp_path)
    monkeypatch.setattr(LearningRegistry, "inspect", lambda *a: pytest.fail("WAL reached owner SQLite open"))
    assert reject(args).to_dict()["archive"] is None
    assert file_bytes(tmp_path) == before


def test_public_schema_is_detached_from_authority_flags_and_limits():
    before = canonical_bytes(bridge.review_schema())
    supplied = bridge.review_schema()
    supplied["properties"]["flags"]["const"]["not_authorized"].clear()
    supplied["properties"]["limits"]["const"]["authority"] = "HUMAN_AUTHORIZED"
    supplied["properties"]["questions"]["const"].append("Publish now")
    assert canonical_bytes(bridge.review_schema()) == before


@pytest.mark.parametrize("suffix", [":hidden", "\\..\\complete-a11.sqlite", "\\.\\complete-a11.sqlite"])
def test_native_ads_traversal_dot_path_denied(tmp_path, suffix):
    args, *_ = source_case(tmp_path)
    args["registry_path"] += suffix
    assert reject(args).to_dict()["archive"] is None


def test_native_windows_hardlink_denied_before_registry(tmp_path, monkeypatch):
    args, *_ = source_case(tmp_path)
    path = Path(args["registry_path"])
    os.link(path, tmp_path / "alias.sqlite")
    assert path.stat().st_nlink == 2, "NATIVE_HARDLINK_CONSTRUCTION_HOLD"
    monkeypatch.setattr(LearningRegistry, "inspect", lambda *a: pytest.fail("hardlink reached A11"))
    assert reject(args).to_dict()["archive"] is None


def test_native_windows_junction_denied_before_registry(tmp_path, monkeypatch):
    args, *_ = source_case(tmp_path)
    link = tmp_path / "junction"
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(tmp_path)], capture_output=True, timeout=15)
    assert proc.returncode == 0, "NATIVE_JUNCTION_CONSTRUCTION_HOLD"
    try:
        assert link.lstat().st_file_attributes & 0x400
        monkeypatch.setattr(LearningRegistry, "inspect", lambda *a: pytest.fail("junction reached A11"))
        assert reject({**args, "registry_path": str(link / Path(args["registry_path"]).name)}).to_dict()["archive"] is None
    finally:
        os.rmdir(link)


def test_native_windows_symlink_denied_before_registry(tmp_path, monkeypatch, record_property):
    args, *_ = source_case(tmp_path)
    link = tmp_path / "symlink.sqlite"
    try:
        os.symlink(args["registry_path"], link)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            record_property("native_symlink", "NATIVE_SYMLINK_CREATION_UNPROVEN")
            record_property("NATIVE_SYMLINK_CREATION_UNPROVEN", True)
            pytest.skip("NATIVE_SYMLINK_CREATION_UNPROVEN")
        raise
    assert link.is_symlink(), "NATIVE_SYMLINK_CONSTRUCTION_HOLD"
    monkeypatch.setattr(LearningRegistry, "inspect", lambda *a: pytest.fail("symlink reached A11"))
    assert reject({**args, "registry_path": str(link)}).to_dict()["archive"] is None


def test_native_windows_write_and_rename_race_denied_during_owner_replay(tmp_path, monkeypatch):
    args, *_ = source_case(tmp_path)
    original = LearningRegistry.inspect
    observed = []
    def racing_read(registry):
        for operation in (lambda: open(registry.path, "r+b"),
                          lambda: os.replace(registry.path, str(registry.path) + ".moved")):
            with pytest.raises(OSError):
                operation()
            observed.append(True)
        return original(registry)
    monkeypatch.setattr(LearningRegistry, "inspect", racing_read)
    before = file_bytes(tmp_path)
    assert reject(args).to_dict()["archive"] is not None
    assert len(observed) == 2 and file_bytes(tmp_path) == before


def test_technical_quality_unbound_from_audience_native_wait_brain(journey, tmp_path, record_property):
    source = tmp_path / "learning"
    source.mkdir()
    args, advisory, *_ = source_case(source)
    before = snapshot(journey), file_bytes(source)
    native = bridge.q4_owner.inspect_wait_brain(str(journey["session"]))
    assert native.to_dict()["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY"
    args.update(pulse_session=str(journey["session"]), q4_sidecar=native.raw)
    result = reject(args)
    value = result.to_dict()
    assert value["archive"] is not None and value["technical"] is not None, value
    assert value["technical"]["artifact_sha256"] == native.to_dict()["binding"]["artifact_sha256"]
    assert value["technical"]["audience_metric"] is None and value["association"] == "UNBOUND"
    assert value["comparability"] == "NOT_COMPARABLE"
    assert bridge.review_learning(**args).raw == result.raw
    readiness = preflight.inspect_effect_preflight(str(journey["session"])).to_dict()
    assert readiness["broker"]["external_effect_decision"] == "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED"
    assert all(row["decision"] == "LIVE_EFFECT_HOLD" for row in readiness["effect_matrix"])
    assert (snapshot(journey), file_bytes(source)) == before
    assert b"B04_SYNTHETIC_PRIVATE_PROMPT" not in result.raw
    record_property("native_b04_a06_q4_audience_association", "UNBOUND_NOT_COMPARABLE")


@pytest.mark.parametrize("mutation", ["sidecar", "video", "journal"])
def test_forged_q4_or_changed_native_media_journal_holds(journey, tmp_path, mutation):
    source = tmp_path / "learning"
    source.mkdir()
    args, *_ = source_case(source)
    native = bridge.q4_owner.inspect_wait_brain(str(journey["session"]))
    assert native.to_dict()["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY"
    raw = native.raw
    if mutation == "sidecar":
        value = native.to_dict()
        value["binding"]["artifact_sha256"] = "f" * 64
        value["q4"]["source_asset_sha256"] = value["q4"]["result_asset_sha256"] = "f" * 64
        body = {k: v for k, v in value.items() if k != "record_sha256"}
        raw = canonical_bytes({**body, "record_sha256": digest(body)})
    elif mutation == "video":
        movie = next(journey["private"].glob("e01-*/video.mp4"))
        movie.write_bytes(movie.read_bytes() + b"changed")
    else:
        journals = list(journey["private"].rglob("*.sqlite*"))
        assert journals
        journals[0].write_bytes(b"corrupt")
    before = snapshot(journey), file_bytes(source)
    result = reject({**args, "pulse_session": str(journey["session"]), "q4_sidecar": raw})
    assert result.to_dict()["technical"] is None
    assert (snapshot(journey), file_bytes(source)) == before


def test_detached_bq01a_fiction_silence_intent_is_never_rewritten():
    brief = intake_brief({"brief_id": "silent-animation", "objective": "Silent fictional animation", "product_ref": None})
    before = canonical_bytes(brief)
    hypotheses = [{"id": "h1", "statement": "A quiet opening may invite a pause",
        "assumptions": ["No observed audience evidence is supplied"],
        "counterexamples": ["Some viewers may prefer spoken explanation"],
        "source_refs": [behavioral.A03_INTAKE_REF], "truth": "HYPOTHESIS"}]
    draft = behavioral.build_behavioral_brief(brief, source_brief_digest=brief_digest(brief), hypotheses=hypotheses,
                                              provenance="HUMAN_SUPPLIED")
    assert draft.to_dict()["product_ref"] is None
    assert canonical_bytes(brief) == before


def test_mt01_new_owner_has_real_registered_consumer_closure():
    work = json.loads((ROOT / WI).read_text())
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    files = minimum_test.file_hashes(ROOT)
    base = {p: d for p, d in files.items() if p not in work["add_only_paths"]}
    plan = impact_v21.registered_action_plan(repo=ROOT, base_sha=work["source_sha"], base_files=base,
        candidate_files=files, changed_paths=work["allowed_paths"], required_acs=work["acceptance"],
        fallback_argv=profiles["mt01-component-onboarding"], groups=minimum_test.approved_groups(),
        registered_profiles=profiles, allowed_paths=work["allowed_paths"])
    assert not plan["fallback"] and not plan["impact_v21"]["missing_mapping"], plan
    assert {"a09", "a11", "a17-1", "bq-01a", "bq-05a", "bq-05b0", "b0-4"} <= set(plan["selected_groups"])
    new = next(g for g in plan["groups"] if g["group"] == "bq-06a")
    assert new["binding"]["registered_profile"] == "mt01-component-onboarding"
    assert new["binding"]["argv"] == ["python", "-m", "pytest", "tests/test_bq_06a_behavioral_learning.py", "-q", "-n", "12"]
    manifest = json.loads((ROOT / MANIFEST).read_text())
    assert manifest["acs"] == work["acceptance"] and len(manifest["acs"]) == 3
    assert manifest["sources"] == work["add_only_paths"][:1]
    assert manifest["tests"] == work["add_only_paths"][1:2]
