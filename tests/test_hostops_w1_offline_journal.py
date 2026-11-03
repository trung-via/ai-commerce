"""W1 follow-up: real Ed25519 offline admission, durable replay HOLD, zero host effects."""
from __future__ import annotations

import pytest

from ai_commerce.hostops_v3 import auth, contract as c
from ai_commerce.hostops_v3.w1_offline_journal import (
    OfflineW1IntentLedger, classify_custody_claim,
)
from test_hostops_v3 import h
from test_hostops_v3_integration import _personal_w1_signed_fixture


def test_w1_offline_intent_persists_and_replay_is_unresolved(h, tmp_path):
    proposal, verified = _personal_w1_signed_fixture(h)
    db = tmp_path / "w1-intent.sqlite3"
    with OfflineW1IntentLedger(db) as ledger:
        first = ledger.record_verified_proposal(proposal, **verified)
    assert first["status"] == "INTENT_STAGED_NO_EFFECT"
    assert first["execution_allowed"] is False
    assert first["disk_effect_verified"] is False
    with OfflineW1IntentLedger(db) as reopened:
        second = reopened.record_verified_proposal(proposal, **verified)
        row = reopened.db.execute(
            "SELECT state FROM w1_intents WHERE request_id=?",
            (proposal["request_id"],),
        ).fetchone()
    assert second["status"] == "HOLD_REPLAY_UNKNOWN"
    assert row == ("UNKNOWN_UNRECONCILED",)
    assert not (h.data / proposal["parameters"]["filename"]).exists()


def test_w1_same_request_id_with_other_genuinely_signed_bytes_denied(h, tmp_path):
    proposal, verified = _personal_w1_signed_fixture(h)
    changed_unsigned = {key: value for key, value in proposal.items()
                        if key != "approval_signature_b64"}
    changed_unsigned["parameters"] = {
        **changed_unsigned["parameters"], "filename": "different.txt",
    }
    changed = {**changed_unsigned, "approval_signature_b64": h.sign(
        changed_unsigned, "human", auth.PERSONAL_W1_NAMESPACE,
    )}
    with OfflineW1IntentLedger(tmp_path / "w1-intent.sqlite3") as ledger:
        ledger.record_verified_proposal(proposal, **verified)
        with pytest.raises(c.Denied, match="W1_REQUEST_ID_CONFLICT"):
            ledger.record_verified_proposal(changed, **verified)
        assert ledger.db.execute("SELECT COUNT(*) FROM w1_intents").fetchone()[0] == 1
    assert not (h.data / "different.txt").exists()


def test_w1_rejects_tamper_wrong_issue_source_expired_without_record(h, tmp_path):
    proposal, verified = _personal_w1_signed_fixture(h)
    with OfflineW1IntentLedger(tmp_path / "w1-intent.sqlite3") as ledger:
        tamper = {**proposal, "parameters": {
            **proposal["parameters"], "content_utf8": "changed",
        }}
        cases = [
            (tamper, verified),
            (proposal, {**verified, "issue_number": 222}),
            (proposal, {**verified, "expected_source_sha": "a" * 40}),
            (proposal, {**verified, "now": 1800001000}),
        ]
        for data, options in cases:
            with pytest.raises(c.Denied):
                ledger.record_verified_proposal(data, **options)
        assert ledger.db.execute("SELECT COUNT(*) FROM w1_intents").fetchone()[0] == 0


def test_w1_no_host_effect_or_runtime_integration(h, tmp_path):
    proposal, verified = _personal_w1_signed_fixture(h)
    with OfflineW1IntentLedger(tmp_path / "w1-intent.sqlite3") as ledger:
        receipt = ledger.record_verified_proposal(proposal, **verified)
        assert not hasattr(ledger, "execute")
        assert not hasattr(ledger, "apply")
        assert receipt["credential_custody_proven"] is False
    assert not (h.data / proposal["parameters"]["filename"]).exists()


def test_w1_custody_never_self_attests_unattended_access():
    base = {
        "single_windows_account": True,
        "public_runner_same_sid": True,
        "signing_key_on_host": False,
        "long_lived_token_on_host": False,
        "external_signer_claimed": False,
        "external_token_broker_claimed": False,
    }
    assert classify_custody_claim(base)["status"] == "HOLD_EXTERNAL_BOUNDARY_MISSING"
    assert classify_custody_claim({
        **base, "signing_key_on_host": True,
    })["status"] == "HOLD_SAME_SID_SECRET_EXPOSURE"
    claimed = classify_custody_claim({
        **base, "external_signer_claimed": True, "external_token_broker_claimed": True,
    })
    assert claimed["status"] == "HOLD_EXTERNAL_ATTESTATION_REQUIRED"
    assert claimed["unattended_ready"] is False
    assert claimed["write_enabled"] is False
    for wrong in ({**base, "single_windows_account": False},
                  {**base, "signing_key_on_host": "false"},
                  {**base, "unexpected": True}):
        with pytest.raises(c.Denied):
            classify_custody_claim(wrong)
