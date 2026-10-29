"""A07 contract and offline registry security/regression tests."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from ai_commerce.cli import main
from ai_commerce.distribution import CapabilityError, CapabilityRegistry, evaluate, validate_snapshot
from ai_commerce.distribution.model import provider_profiles

AT = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
ACCOUNT = {"namespace": "tiktok_open_id", "id": "account-A"}
APP = {"namespace": "tiktok_client_key", "id": "app-A"}


def proof(account=ACCOUNT, app=APP, kind="ACCOUNT_AUTHORIZATION"):
    return {
        "evidence_id": "ev_scope_A_00001", "kind": kind,
        "integrity": "HASHED", "sha256": "a" * 64,
        "scope": "TARGET", "subject": {"account_ref": account, "app_ref": app},
        "captured_at": "2026-10-29T08:00:00Z",
    }


def snap(**changes):
    record = {
        "schema_version": "ChannelCapabilitySnapshot.v1",
        "snapshot_id": "ccs_capability_A_0001",
        "platform": "TIKTOK", "account_ref": ACCOUNT, "app_ref": APP,
        "market": "VN", "surface": "VIDEO", "action": "API_UPLOAD",
        "state": "ALLOWED", "constraints": {"visibility": ["PUBLIC", "SELF_ONLY"]},
        "scope_claims": [{"name": "video.upload", "status": "GRANTED"}],
        "evidence_refs": [proof()], "observed_at": "2026-10-29T09:00:00Z",
        "recorded_at": "2026-10-29T09:10:00Z",
        "expires_at": "2026-10-30T09:00:00Z",
        "source_mode": "OBSERVED", "profile_version": "v1.0",
    }
    record.update(changes)
    return record


def request(**changes):
    record = {
        "schema_version": "ChannelCapabilityRequest.v1",
        "platform": "TIKTOK", "account_ref": ACCOUNT, "app_ref": APP,
        "market": "VN", "surface": "VIDEO", "action": "API_UPLOAD",
    }
    record.update(changes)
    return record


def check(tmp_path, snapshot=None, req=None, now=AT):
    db = CapabilityRegistry(tmp_path / "a07.db")
    if snapshot is not None:
        db.import_snapshot(snapshot)
    return evaluate(req or request(), db, at=now)


def test_schema_and_all_four_provider_profiles():
    assert validate_snapshot(snap())["state"] == "ALLOWED"
    profiles = provider_profiles()
    assert set(profiles["platforms"]) == {"TIKTOK", "FACEBOOK", "INSTAGRAM", "YOUTUBE"}
    assert all(profiles["platforms"][key]["docs"] for key in profiles["platforms"])


def test_observed_allow_is_offline_not_live_verified(tmp_path):
    response = check(tmp_path, snap())
    assert response["decision"] == "ALLOW"
    assert response["verification"] == "OFFLINE_RECORDED_NOT_LIVE_VERIFIED"
    assert response["simulation"] is False


@pytest.mark.parametrize("difference", [
    {"account_ref": {"namespace": "tiktok_open_id", "id": "account-B"}},
    {"app_ref": {"namespace": "tiktok_client_key", "id": "app-B"}},
    {"market": "US"}, {"surface": "REEL"}, {"platform": "INSTAGRAM"},
    {"action": "API_DIRECT_POST"},
])
def test_no_cross_identity_inheritance(tmp_path, difference):
    assert check(tmp_path, snap(), request(**difference))["decision"] == "UNKNOWN"


def test_unknown_denied_revoked_expired(tmp_path):
    assert check(tmp_path)["decision"] == "UNKNOWN"
    for state, desired in (
        ("UNKNOWN", "UNKNOWN"), ("DENIED", "DENY"),
        ("REVOKED", "DENY"), ("EXPIRED", "NEEDS_RECHECK")
    ):
        assert check(tmp_path, snap(state=state))["decision"] == desired


def test_expiry_boundary_and_unbounded_evidence(tmp_path):
    until = datetime(2026, 10, 30, 9, tzinfo=timezone.utc)
    assert check(tmp_path, snap(), now=until)["decision"] == "NEEDS_RECHECK"
    assert check(tmp_path, snap(expires_at=None))["decision"] == "NEEDS_RECHECK"


def test_revoke_snapshot_supersedes_not_falls_back(tmp_path):
    db = CapabilityRegistry(tmp_path / "a07.db")
    first = snap()
    db.import_snapshot(first)
    second = snap(
        snapshot_id="ccs_capability_A_0002",
        supersedes_snapshot_id=first["snapshot_id"],
        state="REVOKED", observed_at="2026-10-29T09:20:00Z",
        recorded_at="2026-10-29T09:25:00Z"
    )
    db.import_snapshot(second)
    assert [x["snapshot_id"] for x in db.history(request())] == [
        first["snapshot_id"], second["snapshot_id"]
    ]
    assert evaluate(request(), db, at=AT)["decision"] == "DENY"


def test_revoke_timestamp_overrides_allowed(tmp_path):
    assert check(tmp_path, snap(revoked_at="2026-10-29T09:40:00Z"))["decision"] == "DENY"


def test_append_only_no_fork_no_stale_replacement(tmp_path):
    db = CapabilityRegistry(tmp_path / "a07.db")
    initial = snap()
    assert db.import_snapshot(initial) == db.import_snapshot(initial)
    with pytest.raises(CapabilityError, match="SUPERSESSION_REQUIRED"):
        db.import_snapshot(snap(snapshot_id="ccs_capability_A_0002"))
    with pytest.raises(CapabilityError, match="STALE_OBSERVATION"):
        db.import_snapshot(snap(snapshot_id="ccs_capability_A_0002",
            supersedes_snapshot_id=initial["snapshot_id"],
            observed_at="2026-10-29T08:00:00Z"))
    with pytest.raises(CapabilityError, match="SNAPSHOT_ID_COLLISION"):
        db.import_snapshot(snap(state="DENIED"))


def test_db_payload_tamper_fails_closed(tmp_path):
    db = CapabilityRegistry(tmp_path / "a07.db")
    db.import_snapshot(snap())
    with sqlite3.connect(db.path) as con:
        con.execute("UPDATE snapshots SET payload_sha256=? WHERE snapshot_id=?",
                    ("0" * 64, snap()["snapshot_id"]))
    with pytest.raises(CapabilityError, match="SNAPSHOT_INTEGRITY_MISMATCH"):
        evaluate(request(), db, at=AT)


def test_account_evidence_and_required_scopes_must_be_proven(tmp_path):
    assert check(tmp_path, snap(evidence_refs=[]))["decision"] == "NEEDS_RECHECK"
    assert check(tmp_path, snap(scope_claims=[]))["decision"] == "NEEDS_RECHECK"
    denied = [{"name": "video.upload", "status": "NOT_GRANTED"}]
    assert check(tmp_path, snap(scope_claims=denied))["decision"] == "DENY"


def test_tiktok_upload_direct_post_visibility_separate(tmp_path):
    direct = snap(action="API_DIRECT_POST", constraints={"visibility": ["SELF_ONLY"]},
        scope_claims=[{"name": "video.publish", "status": "GRANTED"}])
    own = request(action="API_DIRECT_POST", required_constraints={"visibility": "SELF_ONLY"})
    public = request(action="API_DIRECT_POST", required_constraints={"visibility": "PUBLIC"})
    assert check(tmp_path, direct, own)["decision"] == "ALLOW"
    assert check(tmp_path, direct, public)["decision"] == "DENY"
    no_proof = snap(action="API_DIRECT_POST", constraints={},
        scope_claims=[{"name": "video.publish", "status": "GRANTED"}])
    assert check(tmp_path, no_proof, request(action="API_DIRECT_POST"))["decision"] == "NEEDS_RECHECK"


def test_no_app_identity_not_api_allowed(tmp_path):
    s = snap(app_ref=None, evidence_refs=[proof(app=None)])
    assert check(tmp_path, s, request(app_ref=None))["decision"] == "NEEDS_RECHECK"


def test_constraints_are_exact_not_assumed(tmp_path):
    assert check(tmp_path, snap(), request(required_constraints={"audio": "AAC"}))["decision"] == "NEEDS_RECHECK"
    assert check(tmp_path, snap(), request(required_constraints={"visibility": "FRIENDS"}))["decision"] == "DENY"


def test_fixture_is_never_granted_as_live_capability(tmp_path):
    simulated = snap(source_mode="TEST_FIXTURE", evidence_refs=[proof(kind="TEST_FIXTURE")])
    assert check(tmp_path, simulated)["decision"] == "NEEDS_RECHECK"
    result = check(tmp_path, simulated, request(simulation=True))
    assert result["decision"] == "ALLOW"
    assert result["simulation"] is True


def test_facebook_page_reel_upload_does_not_imply_publish(tmp_path):
    account = {"namespace": "facebook_page_id", "id": "page-A"}
    app = {"namespace": "meta_app_id", "id": "meta-A"}
    record = snap(platform="FACEBOOK", account_ref=account, app_ref=app,
        surface="PAGE_REEL", scope_claims=[], evidence_refs=[proof(account, app)])
    req = request(platform="FACEBOOK", account_ref=account, app_ref=app, surface="PAGE_REEL")
    assert check(tmp_path, record, req)["decision"] == "ALLOW"
    assert check(tmp_path, record, {**req, "action": "API_DIRECT_POST"})["decision"] == "UNKNOWN"


@pytest.mark.parametrize("mode,names", [
    ("FACEBOOK_LOGIN", ["instagram_basic","instagram_content_publish","pages_read_engagement","pages_show_list"]),
    ("INSTAGRAM_LOGIN", ["instagram_business_basic","instagram_business_content_publish"]),
])
def test_instagram_modes_account_and_scopes(tmp_path, mode, names):
    account = {"namespace": "instagram_user_id", "id": "ig-A"}
    app = {"namespace": "meta_app_id", "id": "meta-A"}
    conditions = {"login_mode": mode, "account_class": "PROFESSIONAL"}
    if mode == "FACEBOOK_LOGIN":
        conditions["linked_page_verified"] = "TRUE"
    record = snap(platform="INSTAGRAM", account_ref=account, app_ref=app,
        surface="REEL", action="API_DIRECT_POST", constraints=conditions,
        scope_claims=[{"name": name, "status": "GRANTED"} for name in names],
        evidence_refs=[proof(account, app)])
    req = request(platform="INSTAGRAM", account_ref=account, app_ref=app,
        surface="REEL", action="API_DIRECT_POST")
    assert check(tmp_path, record, req)["decision"] == "ALLOW"
    record["scope_claims"].pop()
    assert check(tmp_path, record, req)["decision"] == "NEEDS_RECHECK"
    if mode == "FACEBOOK_LOGIN":
        record["scope_claims"].append({"name": names[-1], "status": "GRANTED"})
        record["constraints"].pop("linked_page_verified")
        assert check(tmp_path, record, req)["decision"] == "NEEDS_RECHECK"


def test_youtube_upload_not_equivalent_to_public_visibility(tmp_path):
    account = {"namespace": "youtube_channel_id", "id": "UC_FAKE"}
    app = {"namespace": "google_cloud_project_id", "id": "google-A"}
    record = snap(platform="YOUTUBE", account_ref=account, app_ref=app,
        surface="SHORT",
        scope_claims=[{"name": "https://www.googleapis.com/auth/youtube.upload", "status": "GRANTED"}],
        evidence_refs=[proof(account, app)])
    req = request(platform="YOUTUBE", account_ref=account, app_ref=app, surface="SHORT")
    assert check(tmp_path, record, req)["decision"] == "ALLOW"
    assert check(tmp_path, record, {**req, "action": "PUBLIC_VISIBILITY"})["decision"] == "UNKNOWN"


@pytest.mark.parametrize("invalid", [
    {"expires_at": "2026-10-29T08:00:00Z"},
    {"observed_at": "2026-02-30T09:00:00Z"},
    {"scope_claims": [
        {"name": "video.upload", "status": "GRANTED"},
        {"name": "video.upload", "status": "REVOKED"}]},
    {"access_token": "not-allowed"}
])
def test_invalid_contracts_reject(invalid):
    with pytest.raises(CapabilityError):
        validate_snapshot(snap(**invalid))


def test_evidence_subject_hash_secret_reject():
    record = snap()
    record["evidence_refs"][0]["subject"]["account_ref"]["id"] = "other"
    with pytest.raises(CapabilityError, match="EVIDENCE_SUBJECT_MISMATCH"):
        validate_snapshot(record)
    record = snap()
    record["evidence_refs"][0]["sha256"] = None
    with pytest.raises(CapabilityError, match="TARGET_EVIDENCE_MUST_BE_HASHED"):
        validate_snapshot(record)
    record = snap()
    record["evidence_refs"][0]["uri"] = "https://example.invalid/?access_token=abc"
    with pytest.raises(CapabilityError, match="CREDENTIAL_MATERIAL_NOT_ALLOWED"):
        validate_snapshot(record)


def test_profile_obsolescence_deterministic(tmp_path):
    record = snap(profile_version="v0.9")
    first = check(tmp_path, record)
    assert first["decision"] == "NEEDS_RECHECK"
    assert first == check(tmp_path, record)


def test_cli_full_roundtrip(tmp_path, capsys):
    record = tmp_path / "snapshot.json"
    record.write_text(json.dumps(snap()), encoding="utf-8")
    req = tmp_path / "req.json"
    req.write_text(json.dumps(request()), encoding="utf-8")
    db = tmp_path / "registry.db"
    assert main(["channel-capability", "validate", "--file", str(record)]) == 0
    assert main(["channel-capability", "import", "--file", str(record), "--db", str(db)]) == 0
    assert main(["channel-capability", "evaluate", "--request", str(req), "--db", str(db),
                 "--at", "2026-10-29T10:00:00Z"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["decision"] == "ALLOW"
    assert main(["channel-capability", "history", "--request", str(req), "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1
