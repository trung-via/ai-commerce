"""Mandatory native Windows BQ-05b.0 offline evidence; live custody unproven.

Native positives use real B0.4 subprocesses and FFmpeg, never a mock bridge.
Broker fixtures are explicitly untrusted drafts, never a fake live broker.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from ai_commerce.creative_quality import pulse_studio_effect_preflight as preflight
from ai_commerce.creative_quality import pulse_studio_offline_bridge as bridge
from ai_commerce.engineering import impact_v21, minimum_test
from ai_commerce.production_runtime import broker_intake as intake, broker_readiness as broker
from ai_commerce.production_runtime import attended_worker as w, handoff, operator
from ai_commerce.production_runtime.contracts import canonical_bytes, seal_contract
from test_b0_4_operator import case, cli, setup, media, PRIVATE, audit, count
from test_bq_05a_pulse_studio_offline_bridge import snapshot, restore_test_fixture, _tamper_native_asset
from test_b0_s3b_broker_readiness import (
    selection, isolation, receipt, state_snapshot, AT, repin_draft, ref,
)

ROOT = Path(__file__).resolve().parents[1]
WI = "work_items/BQ-05B0_EFFECT_PREFLIGHT_001.json"
MANIFEST = "work_items/mt01-components/bq-05b0.json"


@pytest.fixture
def journey(case):
    media()
    # Both native closed contracts and broker drafts accept this digest ref.
    case["body"]["journey_id"] = "journey:" + "4" * 64
    case["directive"].write_bytes(seal_contract("CreativeDirective", case["body"]).raw)
    setup(case)
    assert cli(case, "run")["state"] == "WAIT_BRAIN"
    cli(case, "handoff")
    return case


def inspect(c, **kwargs):
    return preflight.inspect_effect_preflight(str(c["session"]), **kwargs)


def assert_live_hold(result):
    data = result.to_dict()
    assert [r["effect"] for r in data["effect_matrix"]] == list(intake.ACTIONS)
    assert all(r["decision"] == "LIVE_EFFECT_HOLD" and r["reason"] == "PRIVATE_VERIFIER_NOT_ENROLLED"
               and r["missing_proofs"] == list(preflight.PROOF_CODES) for r in data["effect_matrix"])
    assert data["broker"]["external_effect_decision"] == "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED"
    assert data["limits"]["rights"] == data["limits"]["business_quality"] == "UNKNOWN"
    assert data["limits"]["spend_ceiling_minor"] is data["limits"]["currency"] is None
    assert data["product_ref"] is None


def positive(c, **kwargs):
    before = snapshot(c)
    result = inspect(c, **kwargs)
    assert result.to_dict()["offline_status"] == preflight.OFFLINE_READY, bridge._diagnose_wait_brain(str(c["session"]))
    assert snapshot(c) == before
    assert_live_hold(result)
    return result


def hold(c, **kwargs):
    before = snapshot(c)
    result = inspect(c, **kwargs)
    data = result.to_dict()
    assert data["offline_status"] == preflight.OFFLINE_HOLD
    assert all(data[k] is None for k in ("binding", "q4", "q5", "provenance"))
    assert before == snapshot(c)
    assert_live_hold(result)
    for secret in (PRIVATE, str(c["root"]), "SELF_RESEALED", "Bearer secret"):
        assert secret.encode() not in result.raw
    return result


def test_native_bq05a_qa_source_binding(journey, monkeypatch, record_property):
    c = journey
    before = snapshot(c)
    script = ("from ai_commerce.creative_quality.pulse_studio_effect_preflight import inspect_effect_preflight; "
              "import sys; sys.stdout.buffer.write(inspect_effect_preflight(sys.argv[1]).raw)")
    proc = subprocess.run([sys.executable, "-c", script, str(c["session"])], cwd=c["root"],
                          env=c["env"], capture_output=True, timeout=180)
    assert proc.returncode == 0 and not proc.stderr
    actual = preflight.BQ05bEffectPreflight(proc.stdout)
    assert actual.to_dict()["offline_status"] == preflight.OFFLINE_READY
    observed = []
    replay = bridge.recheck_sidecar
    def checked(raw, path, **kwargs):
        current = replay(raw, path, **kwargs)
        assert raw == current.raw and kwargs == {"sample": True}
        observed.append(current)
        return current
    monkeypatch.setattr(bridge, "recheck_sidecar", checked)
    result = positive(c)
    assert result.raw == actual.raw == positive(c).raw
    assert len(observed) == 2 and observed[0].raw == observed[1].raw
    data, qa = result.to_dict(), observed[0].to_dict()
    assert {k: data["binding"][k] for k in preflight.BINDING_FIELDS} == qa["binding"]
    assert data["binding"]["bq05a_sidecar_sha256"] == observed[0].digest
    assert data["binding"]["bq05a_record_sha256"] == qa["record_sha256"]
    session, _, root, directive, intent, _ = operator.load(str(c["session"]))
    sealed, packet, proof = handoff.verified(root, directive, intent)
    movie = w._render_path(root, intent) / "video.mp4"
    assert sealed.to_dict()["state"] == "WAIT_BRAIN" and sealed.to_dict()["brain_decision"] is None
    assert data["binding"]["runtime_sha256"] == session["intent"]["runtime_sha256"] == w._runtime_hash()
    assert data["binding"]["artifact_sha256"] == sha256(movie.read_bytes()).hexdigest()
    assert data["binding"]["packet_file_sha256"] == sha256(packet).hexdigest()
    assert data["binding"]["journal_checkpoint"] == proof["journal_checkpoint"]
    assert data["q4"]["status"] == data["q4"]["t2_status"] == "OBSERVED"
    assert data["q5"]["executed"] is False and data["q5"]["editor"] == "NONE"
    assert snapshot(c) == before and count(c, "render_call") == count(c, "native_run_call") == 1
    events = audit(c)
    assert any(r.get("media_process") == "ffprobe" for r in events)
    assert any(r.get("media_process") == "ffmpeg" and r.get("decode") for r in events)
    assert not any(r.get("denied_network") or r.get("denied_process") for r in events)
    record_property("ac", "BQ-05b0.native-bq05a-qa-source-binding")
    record_property("actual_windows_q4_t1_t2", True)
    record_property("a06_current_asset_sha256", data["binding"]["artifact_sha256"])
    record_property("bq05a_sidecar_sha256", observed[0].digest)
    record_property("preflight_sha256", result.digest)
    record_property("retained_native_bytes_unchanged", True)


def test_readiness_matrix_preserves_b0s_denial(journey, selection, isolation, record_property):
    # Independently admissible closed drafts, with no issuer authentication.
    for draft in (selection, isolation):
        assert intake.validate_broker_draft(draft).trust_label == "UNTRUSTED"
    assert broker.evaluate_readiness(selection, at=AT, isolation=isolation).status == "DESIGN_READY_FOR_HUMAN_SELECTION"
    upstream = broker.decide_external_effect(selection, at=AT, isolation=isolation)
    result = positive(journey, selection=selection, isolation=isolation, at=AT)
    data = result.to_dict()
    assert data["broker"]["design_status"] == "DESIGN_READY_FOR_HUMAN_SELECTION"
    assert data["broker"]["external_reason"] == upstream.reason == "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED"
    assert data["broker"]["custody_reason"] == upstream.custody_gate_reason
    assert data["private_evidence_checklist"]["mode"] == "STATIC_PROPOSAL_ONLY"
    assert {row["owner"] for row in data["private_evidence_checklist"]["items"]} == {"HUMAN", "BRAIN", "PRIVATE_BROKER", "PROVIDER"}
    record_property("ac", "BQ-05b0.readiness-matrix-preserves-b0s-denial")
    record_property("live_effect_decision", upstream.external_effect_decision)


def reseal(data):
    data["record_sha256"] = sha256(canonical_bytes({k: v for k, v in data.items() if k != "record_sha256"})).hexdigest()
    return canonical_bytes(data)


def test_closed_redacted_brain_handoff(journey, record_property):
    result = positive(journey)
    data = result.to_dict()
    assert set(data) == preflight.TOP and data["schema_version"] == "BQ05bEffectPreflight.v1"
    assert result.raw == reseal(data) and result.digest == sha256(result.raw).hexdigest()
    assert preflight.recheck_preflight(result.raw, str(journey["session"])).raw == result.raw
    for secret in (PRIVATE, str(journey["root"]), "freeform", "fixture_token", "actor_ref"):
        assert secret.encode() not in result.raw
    # account_ref is a fixed checklist field label, never an account value.
    assert data["provenance"] == {"evidence_type": "SYNTHETIC_FIXTURE", "origin": "LOCAL_IMPORT", "provider_attested": False}
    assert data["limits"]["brain_authentication"] == "NOT_PROVEN"
    assert {"currency", "spend_ceiling_minor", "expires_at", "idempotency_key", "nonce", "final_media_sha256"} <= set(
        data["private_evidence_checklist"]["required_effect_scope_fields"])
    for mutate in (lambda d: d.update(authorized=True),
                   lambda d: d["q4"].update(private_path="C:\\secret"),
                   lambda d: d["provenance"].update(evidence_type="PROVIDER_ATTESTED"),
                   lambda d: d["effect_matrix"][0].update(decision="PASS"),
                   lambda d: d["broker"].update(design_status="PRIVATE_VERIFIED"),
                   lambda d: d["limits"].update(spend_ceiling_minor=0)):
        forged = result.to_dict()
        mutate(forged)
        with pytest.raises(preflight.PreflightHold, match="PREFLIGHT_INVALID"):
            preflight.BQ05bEffectPreflight(reseal(forged))
    record_property("ac", "BQ-05b0.closed-redacted-brain-handoff")
    record_property("brain_authentication", "NOT_PROVEN")


def test_actual_native_asset_receipt_and_journal_damage(journey):
    saved = snapshot(journey)
    old = positive(journey)
    qa = bridge.inspect_wait_brain(str(journey["session"]))
    for damage in ("mutated-video", "foreign-video", "different-native-asset", "receipt", "packet",
                   "checkpoint", "journal-edit", "journal-reorder", "audit", "admission"):
        restore_test_fixture(saved)
        _tamper_native_asset(journey, damage)
        hold(journey)
        with pytest.raises(preflight.PreflightHold):
            preflight.recheck_preflight(old.raw, str(journey["session"]))
        with pytest.raises(bridge.BridgeHold):
            bridge.recheck_sidecar(qa.raw, str(journey["session"]))
    restore_test_fixture(saved)


def test_native_replay_detects_change_between_fresh_sidecar_and_recheck(journey, monkeypatch):
    original = bridge.recheck_sidecar
    def changed(raw, path, **kwargs):
        _tamper_native_asset(journey, "mutated-video")
        return original(raw, path, **kwargs)
    monkeypatch.setattr(bridge, "recheck_sidecar", changed)
    result = inspect(journey)
    assert result.to_dict()["offline_status"] == preflight.OFFLINE_HOLD
    assert result.to_dict()["binding"] is None


def test_self_resealed_native_pins_and_forged_receipt(journey):
    saved = snapshot(journey)
    for field, value in (("journey_id", ref("journey", "8")), ("directive_version", 2),
                         ("directive_sha256", "0" * 64), ("source_sha", "0" * 40),
                         ("runtime_sha256", "0" * 64), ("fixture_token", "SELF_RESEALED")):
        restore_test_fixture(saved)
        data = json.loads(journey["session"].read_bytes())
        data["intent"][field] = value
        data["session_sha256"] = sha256(canonical_bytes({k: v for k, v in data.items() if k != "session_sha256"})).hexdigest()
        journey["session"].write_bytes(canonical_bytes(data))
        hold(journey)
    restore_test_fixture(saved)
    path = journey["export"] / handoff.RECEIPT
    for field, value in (("state", "UNKNOWN_EFFECT"), ("provenance", {"evidence_type": "PROVIDER_ATTESTED"})):
        restore_test_fixture(saved)
        data = json.loads(path.read_bytes())
        data[field] = value
        data["receipt_sha256"] = sha256(canonical_bytes({k: v for k, v in data.items() if k != "receipt_sha256"})).hexdigest()
        path.write_bytes(canonical_bytes(data))
        hold(journey)
    restore_test_fixture(saved)
    journey["body"]["creative"]["freeform"] = "secret changed directive"
    journey["directive"].write_bytes(seal_contract("CreativeDirective", journey["body"]).raw)
    hold(journey)


def test_pre_wait_brain_is_not_positive(case):
    media()
    setup(case)
    hold(case)


def test_human_supplied_rights_reopened_and_revoked(case):
    media()
    for asset in case["body"]["assets"]:
        claim = {"schema_version": "LocalStillRights.v1", "asset_sha256": asset["sha256"],
                 "source_sha": w.SOURCE_SHA, "scope": "OFFLINE_LOCAL_RENDER", "status": "GRANTED",
                 "valid_from": 0, "expires_at": int(time.time()) + 3600}
        raw = canonical_bytes(claim)
        path = case["assets"] / (asset["sha256"] + ".json")
        path.write_bytes(raw)
        digest = sha256(raw).hexdigest()
        right = "rights:" + digest
        asset.update(evidence_type="HUMAN_SUPPLIED", rights={"status": "GRANTED", "ref": right, "sha256": digest})
        case["rights"].append(right + "=" + str(path))
    case["body"]["provenance"].update(actor="HUMAN", evidence_type="HUMAN_SUPPLIED")
    # Fiction, meme, animation, silence and no product stay legitimate private intent.
    case["body"]["creative"].update(freeform=PRIVATE + " fiction meme animation silent", product_ref=None)
    case["directive"].write_bytes(seal_contract("CreativeDirective", case["body"]).raw)
    setup(case)
    assert cli(case, "run")["state"] == "WAIT_BRAIN"
    cli(case, "handoff")
    result = positive(case)
    assert result.to_dict()["provenance"]["evidence_type"] == "HUMAN_SUPPLIED"
    assert result.to_dict()["limits"]["rights"] == "UNKNOWN"
    next(case["assets"].glob("*.json")).unlink()
    hold(case)


@pytest.mark.parametrize("context", [{"status": "AUTHORIZED"}, {"status": "PRIVATE_VERIFIED"},
    {"provider_attested": True}, {"fixture_token": "SELF_RESEALED"}, {"live_grant": True},
    {"prompt": "Bearer secret", "path": "C:\\secret"}, {"github_artifact": "verified"}, {}])
def test_caller_reports_never_supply_authority(case, context, monkeypatch):
    monkeypatch.setattr(bridge, "inspect_wait_brain", lambda *a, **k: pytest.fail("untrusted context reached media"))
    hold(case, context=context)
    assert preflight.inspect_effect_preflight(context).to_dict()["offline_status"] == preflight.OFFLINE_HOLD


@pytest.mark.parametrize("effect", [*intake.ACTIONS, "SPEND", "UNKNOWN_EFFECT", None])
def test_every_external_request_holds_before_native_media(case, selection, isolation, state_snapshot, effect, monkeypatch):
    state_snapshot.update(enrollment_state="ENROLLED", request_mode="LIVE_EFFECT_OR_ENROLLMENT")
    monkeypatch.setattr(bridge, "inspect_wait_brain", lambda *a, **k: pytest.fail("external request reached native media"))
    hold(case, effect=effect, selection=selection, isolation=isolation, state=state_snapshot, at=AT)
    with pytest.raises(TypeError):
        preflight.inspect_effect_preflight(str(case["session"]), live_grant=True)


def test_invalid_expired_replayed_and_inconsistent_broker_drafts(case, selection, isolation, receipt, state_snapshot):
    inputs = dict(selection=selection, isolation=isolation, receipt=receipt, state=state_snapshot, at=AT)
    probes = []
    for field, value in (("expires_at", AT - 1), ("policy_audience", "PULSE_CUSTODY_V1"),
                         ("signature", "signature-like-json"), ("input_trust", "PRIVATE_VERIFIED")):
        args = deepcopy(inputs)
        args["selection"][field] = value
        args["selection"] = repin_draft(args["selection"])
        probes.append(args)
    for field, value in (("used_nonces", [selection["nonce"]]), ("used_requests", [receipt["idempotency_key"]]),
                         ("rollback_detected", True), ("effect_status", "UNKNOWN_EFFECT")):
        args = deepcopy(inputs)
        args["state"][field] = value
        if field == "effect_status":
            args["state"].update(original_idempotency_key=receipt["idempotency_key"], reservation_ref=receipt["reservation_ref"])
        probes.append(args)
    for field, value in (("provider_ref", ref("provider", "b")), ("final_media_sha256", "b" * 64),
                         ("action", "UPLOAD"), ("currency", "EUR"), ("spend_ceiling_minor", 101)):
        args = deepcopy(inputs)
        args["receipt"][field] = value
        args["receipt"] = repin_draft(args["receipt"])
        probes.append(args)
    for args in probes:
        upstream = broker.decide_external_effect(**args)
        result = hold(case, effect="PUBLISH", **args).to_dict()
        assert result["broker"]["external_reason"] == upstream.reason
        assert result["broker"]["external_status"] == upstream.status
        assert upstream.status in {"HOLD", "REJECT"}


def test_broker_receipt_scope_compared_to_current_native(journey, selection, isolation, receipt):
    native = positive(journey).to_dict()["binding"]
    for draft in (selection, isolation, receipt):
        draft["source_sha"] = native["pulse_source_sha"]
    receipt.update(journey_id=native["journey_id"], directive_version=native["directive_version"],
                   directive_sha256=native["directive_sha256"], final_media_sha256=native["artifact_sha256"])
    inputs = dict(selection=repin_draft(selection), isolation=repin_draft(isolation), receipt=repin_draft(receipt), at=AT)
    result = positive(journey, **inputs)
    assert result.to_dict()["broker"]["native_scope_status"] == "MATCHED_UNTRUSTED"
    for field, value in (("journey_id", ref("journey", "8")), ("directive_sha256", "0" * 64),
                         ("final_media_sha256", "0" * 64), ("source_sha", "0" * 40)):
        args = deepcopy(inputs)
        args["receipt"][field] = value
        args["receipt"] = repin_draft(args["receipt"])
        assert positive(journey, **args).to_dict()["broker"]["native_scope_status"] == "MISMATCH_HOLD"


def test_self_resealed_preflight_still_requires_current_owner_replay(journey):
    original = positive(journey)
    data = original.to_dict()
    data["binding"]["bq05a_sidecar_sha256"] = "0" * 64
    forged = preflight.BQ05bEffectPreflight(reseal(data))
    with pytest.raises(preflight.PreflightHold, match="CURRENT_NATIVE_REPLAY_REQUIRED"):
        preflight.recheck_preflight(forged.raw, str(journey["session"]))


def test_native_hardlink_protection_inherited(journey, monkeypatch):
    movie = next(journey["private"].glob("e01-*/video.mp4"))
    os.link(movie, journey["root"] / "hardlink.mp4")
    assert movie.stat().st_nlink == 2
    monkeypatch.setattr(handoff, "verified", lambda *a: pytest.fail("hardlink reached handoff"))
    hold(journey)


def test_native_junction_protection_inherited(case, monkeypatch):
    link = case["root"] / "junction"
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(case["operator"])], capture_output=True, timeout=15)
    assert proc.returncode == 0, "NATIVE_JUNCTION_ENVIRONMENT_HOLD"
    try:
        assert link.lstat().st_file_attributes & 0x400
        monkeypatch.setattr(operator, "load", lambda *a: pytest.fail("junction reached operator"))
        assert preflight.inspect_effect_preflight(str(link / "SESSION.json")).to_dict()["offline_status"] == preflight.OFFLINE_HOLD
    finally:
        os.rmdir(link)


def test_current_native_q4_failure_is_not_readiness(journey, monkeypatch):
    original = subprocess.Popen
    def failed(argv, *args, **kwargs):
        if "ffmpeg" in Path(argv[0]).stem.lower():
            raise OSError("Bearer secret C:\\private")
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", failed)
    hold(journey)


def test_mt01_onboarding_owner_consumer_closure():
    work = json.loads((ROOT / WI).read_text())
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    files = minimum_test.file_hashes(ROOT)
    base = {p: d for p, d in files.items() if p not in work["add_only_paths"]}
    plan = impact_v21.registered_action_plan(repo=ROOT, base_sha=work["source_sha"], base_files=base,
        candidate_files=files, changed_paths=work["allowed_paths"], required_acs=work["acceptance"],
        fallback_argv=profiles["mt01-component-onboarding"], groups=minimum_test.approved_groups(),
        registered_profiles=profiles, allowed_paths=work["allowed_paths"])
    assert not plan["fallback"] and not plan["impact_v21"]["missing_mapping"], plan
    assert {"a06", "b0-3", "b0-4", "b0-s", "b0-s3", "b0-s3b", "bq-05a", "bq-05b0"} <= set(plan["selected_groups"])
    new = next(g for g in plan["groups"] if g["group"] == "bq-05b0")
    assert new["binding"]["registered_profile"] == "mt01-component-onboarding"
    assert new["binding"]["argv"] == ["python", "-m", "pytest", "tests/test_bq_05b_effect_preflight.py", "-q", "-n", "12"]
    manifest = json.loads((ROOT / MANIFEST).read_text())
    assert manifest["sources"] == work["add_only_paths"][:1]
    assert manifest["tests"] == work["add_only_paths"][1:2]
    assert manifest["acs"] == work["acceptance"] and len(manifest["acs"]) == 3
