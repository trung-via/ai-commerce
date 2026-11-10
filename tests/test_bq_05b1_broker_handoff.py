"""BQ-05b.1 Windows-native, read-only broker trial handoff acceptance.

The real positive fixture runs B0.4 CLI + A06 FFmpeg; no fake provider,
private issuer, synthetic WorkOwner release, network or PULSE live effect.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from ai_commerce.creative_quality import pulse_studio_broker_handoff as trial
from ai_commerce.creative_quality import pulse_studio_effect_preflight as preflight
from ai_commerce.engineering import impact_v21, minimum_test
from ai_commerce.production_runtime.contracts import canonical_bytes
from test_bq_05b_effect_preflight import journey, positive as preflight_positive
from test_bq_05a_pulse_studio_offline_bridge import (
    snapshot, restore_test_fixture, _tamper_native_asset,
)
from test_b0_s3b_broker_readiness import selection, AT

ROOT = Path(__file__).resolve().parents[1]
WORK = "work_items/BQ-05B1_PRIVATE_BROKER_TRIAL_HANDOFF_001.json"
MANIFEST = "work_items/mt01-components/bq-05b1.json"


def _prepare(c, **kwargs):
    return trial.prepare_broker_trial_handoff(str(c["session"]), **kwargs)


def _positive(c, **kwargs):
    before = snapshot(c)
    result = _prepare(c, **kwargs)
    data = result.to_dict()
    assert data["status"] == trial.BOUND
    assert data["trial_interface"]["provider_effect_allowed"] is False
    assert data["trial_interface"]["private_issuer_enrolled"] is False
    assert data["limits"]["spend_minor"] is None
    assert data["limits"]["currency"] is None
    assert snapshot(c) == before
    return result


def _hold(c, **kwargs):
    before = snapshot(c)
    result = _prepare(c, **kwargs)
    data = result.to_dict()
    assert data["status"] == trial.HOLD
    assert data["binding"] is data["q4"] is data["q5"] is None
    assert data["trial_interface"]["request_digest"] is None
    assert data["trial_interface"]["provider_effect_allowed"] is False
    assert snapshot(c) == before
    return result


def _reseal(body):
    body["record_sha256"] = sha256(canonical_bytes(
        {key: val for key, val in body.items() if key != "record_sha256"}
    )).hexdigest()
    return canonical_bytes(body)


def test_native_current_handoff_binding_real_b04_a06_q4_q5(journey, record_property):
    before = snapshot(journey)
    upstream = preflight_positive(journey)
    result = _positive(journey)
    replay = trial.recheck_broker_trial_handoff(result.raw, str(journey["session"]))
    assert result.raw == replay.raw == _positive(journey).raw
    data = result.to_dict()
    binding = data["binding"]
    assert binding["bq05b0_preflight_sha256"] == upstream.digest
    assert binding["artifact_sha256"] == upstream.to_dict()["binding"]["artifact_sha256"]
    assert binding["bq05a_sidecar_sha256"] == upstream.to_dict()["binding"]["bq05a_sidecar_sha256"]
    assert binding["journal_checkpoint"] == upstream.to_dict()["binding"]["journal_checkpoint"]
    assert data["q4"]["status"] == data["q4"]["t2_status"] == "OBSERVED"
    assert data["q5"]["executed"] is False and data["q5"]["editor"] == "NONE"
    assert result.raw == _reseal(result.to_dict())
    assert snapshot(journey) == before
    record_property("ac", "BQ-05b1.native-current-handoff-binding")
    record_property("a06_video_sha256", binding["artifact_sha256"])
    record_property("preflight_sha256", upstream.digest)
    record_property("read_only_native_bytes_unchanged", True)


def test_no_secret_deterministic_trial_vectors(journey, selection, record_property):
    result = _positive(journey, selection=selection, at=AT)
    data = result.to_dict()
    assert data["broker_design"] == "UNTRUSTED_DESIGN_SELECTION"
    assert data["trial_interface"]["mode"] == "INERT_FIELD_ONLY"
    assert data["trial_interface"]["challenge_label"] == "UNAUTHENTICATED_DESIGN_LABEL_NOT_A_NONCE"
    assert data["trial_interface"]["request_digest"] == _prepare(journey, selection=selection, at=AT).to_dict()[
        "trial_interface"]["request_digest"]
    assert [v["vector_id"] for v in data["conformance_vectors"]] == [v[0] for v in trial.VECTORS]
    assert all(v["expected"] == "DENY_UNTIL_AUTHENTICATED"
               and v["evidence_status"] == "NOT_OBSERVED"
               for v in data["conformance_vectors"])
    assert all(p["status"] == "UNPROVEN" for p in data["independent_proofs"]["proofs"])
    assert set(data["independent_proofs"]["required_effect_scope_fields"]) == set(preflight.SCOPE_FIELDS)
    for value in (str(journey["root"]), str(journey["private"]), "Bearer secret", "SELF_RESEALED"):
        assert value.encode() not in result.raw
    assert not any(key in data["trial_interface"] for key in (
        "provider_ref", "account_ref", "currency", "spend_ceiling_minor", "nonce", "grant", "token"
    ))
    record_property("ac", "BQ-05b1.independent-broker-no-secret-trial-vectors")


def test_no_live_authority_translation_or_provider_effect(journey, monkeypatch, record_property):
    native = _positive(journey).to_dict()
    assert native["limits"]["private_authority"] == "UNPROVEN"
    assert native["limits"]["effect_execution"] == "NOT_AUTHORIZED"
    assert native["trial_interface"]["atomic_budget_reserved"] is False
    assert native["trial_interface"]["replay_authorized"] is False
    assert "NO_PUBLISH" in native["flags"] and "NO_SPEND" in native["flags"]
    # Even a forged caller grant or an external action cannot enter the media reader.
    monkeypatch.setattr(preflight, "inspect_effect_preflight",
                        lambda *a, **kw: pytest.fail("untrusted caller reached media"))
    for effect in ("PUBLISH", "UPLOAD", "SPEND", "UNKNOWN_EFFECT", None):
        result = _hold(journey, effect=effect)
        assert result.to_dict()["reason_codes"] == [trial.REASONS[3]]
    for context in ({"provider_verified": True}, {"human_grant": True},
                    {"credential": "Bearer secret"}, {}, {"brain_decision": "PASS"}):
        assert _hold(journey, context=context).to_dict()["reason_codes"] == [trial.REASONS[3]]
    record_property("ac", "BQ-05b1.no-live-authority-translation")


def test_stale_media_journal_receipt_and_current_native_replay_hold(journey):
    saved = snapshot(journey)
    original = _positive(journey)
    for damage in ("mutated-video", "foreign-video", "receipt", "packet",
                   "checkpoint", "journal-edit", "journal-reorder", "admission"):
        restore_test_fixture(saved)
        _tamper_native_asset(journey, damage)
        result = _hold(journey)
        assert result.to_dict()["reason_codes"] == [trial.REASONS[1]]
        with pytest.raises(trial.BrokerTrialHold, match="CURRENT_NATIVE_REPLAY_REQUIRED"):
            trial.recheck_broker_trial_handoff(original.raw, str(journey["session"]))
    restore_test_fixture(saved)


def test_self_resealed_or_injected_trial_does_not_authenticate(journey):
    initial = _positive(journey)
    for change in (
        lambda d: d["q4"].update(review_status=(
            "REVISE" if d["q4"]["review_status"] != "REVISE" else "HOLD")),
        lambda d: d["trial_interface"].update(request_digest="0" * 64),
        lambda d: d["limits"].update(spend_minor=0),
        lambda d: d.update(live_grant=True),
        lambda d: d["independent_proofs"]["proofs"][0].update(status="VERIFIED"),
    ):
        data = initial.to_dict()
        change(data)
        raw = _reseal(data)
        try:
            trial.BQ05bBrokerTrialHandoff(raw)
        except trial.BrokerTrialHold:
            pass
        else:
            with pytest.raises(trial.BrokerTrialHold, match="CURRENT_NATIVE_REPLAY_REQUIRED"):
                trial.recheck_broker_trial_handoff(raw, str(journey["session"]))


def test_untrusted_selection_expired_scope_and_replay_holds_before_native(journey, selection, monkeypatch):
    baseline = _positive(journey, selection=selection, at=AT)
    assert baseline.to_dict()["broker_design"] == "UNTRUSTED_DESIGN_SELECTION"
    monkeypatch.setattr(preflight, "inspect_effect_preflight",
                        lambda *a, **kw: pytest.fail("invalid draft reached media"))
    for change in (
        lambda d: d.update(signature="SELF_SEALED"),
        lambda d: d.update(vendor_neutral_slot="PRESELECTED_PROVIDER"),
        lambda d: d.update(credential_custody_mode="CALLER_HAS_BEARER"),
        lambda d: d.update(expires_at=AT - 1),
        lambda d: d.update(input_trust="PRIVATE_VERIFIED"),
        lambda d: d.update(draft_sha256="0" * 64),
    ):
        d = deepcopy(selection)
        change(d)
        result = _hold(journey, selection=d, at=AT)
        assert result.to_dict()["reason_codes"] == [trial.REASONS[2]]


def test_pre_wait_brain_does_not_establish_trial(case):
    # No mock receipt, test fixture token or caller assertion can upgrade stage.
    from test_b0_4_operator import media, setup
    media()
    setup(case)
    assert _hold(case).to_dict()["reason_codes"] == [trial.REASONS[1]]


def test_native_hardlink_alias_not_promoted(journey):
    movie = next(journey["private"].glob("e01-*/video.mp4"))
    alias = journey["root"] / "trial-hardlink.mp4"
    os.link(movie, alias)
    assert movie.stat().st_nlink >= 2
    assert _hold(journey).to_dict()["status"] == trial.HOLD


def test_mt01_registration_and_real_consumer_fan_in():
    work = json.loads((ROOT / WORK).read_text())
    manifest = json.loads((ROOT / MANIFEST).read_text())
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    assert manifest["sources"] == work["add_only_paths"][:1]
    assert manifest["tests"] == work["add_only_paths"][1:2]
    assert manifest["acs"] == work["acceptance"] and len(manifest["acs"]) == 3
    files = minimum_test.file_hashes(ROOT)
    base = {path: digest for path, digest in files.items() if path not in work["add_only_paths"]}
    plan = impact_v21.registered_action_plan(
        repo=ROOT, base_sha=work["source_sha"], base_files=base,
        candidate_files=files, changed_paths=work["allowed_paths"],
        required_acs=work["acceptance"], fallback_argv=profiles["mt01-component-onboarding"],
        groups=minimum_test.approved_groups(), registered_profiles=profiles,
        allowed_paths=work["allowed_paths"]
    )
    assert not plan["fallback"] and not plan["impact_v21"]["missing_mapping"], plan
    assert {"a06", "b0-3", "b0-4", "b0-s3b", "bq-05a", "bq-05b0", "bq-05b1"} <= set(plan["selected_groups"])
    row = next(g for g in plan["groups"] if g["group"] == "bq-05b1")
    assert row["binding"]["registered_profile"] == "mt01-component-onboarding"
    assert row["binding"]["argv"] == [
        "python", "-m", "pytest", "tests/test_bq_05b1_broker_handoff.py", "-q", "-n", "12"
    ]
