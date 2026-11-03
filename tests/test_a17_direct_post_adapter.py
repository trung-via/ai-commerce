"""A17.D07.D08.offline-capability-gated-adapters: native custody and no effects."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import urllib.request

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.channel_adapters import (AdapterError, DirectPostRequest, HumanConsent, NativeBinding,
    OfflineDirectPostAdapter, SyntheticReceipt, capability_for, propose_human_opt_in, submit_live)
from ai_commerce.channel_adapters.schemas import (PROFILES, VERSION, canonical, digest, direct_post_contract,
    catalog_contract, parse, validate)
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.distribution.assisted import export_package
from ai_commerce.distribution.evaluator import evaluate
from ai_commerce.distribution.model import provider_profiles
from ai_commerce.distribution.registry import CapabilityRegistry
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.distribution_intelligence.dashboard import PublicationSelection
from ai_commerce.distribution_intelligence.reconciliation import (ReconciliationLedger, inspect_sources,
    publication_request_digest)
from ai_commerce.distribution_intelligence.schemas import IntelligenceError
from ai_commerce.operations.registry import ArtifactRegistry
from ai_commerce.operations.retry_guard import AttemptObservation, ReconciliationObservation, RetryGuard

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 3, 2, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline adapter invoked an outbound/provider effect")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "request", forbidden)
    monkeypatch.setattr(http.client.HTTPSConnection, "request", forbidden)
    original = subprocess.Popen

    def local_read_only_process(args, *rest, **kwargs):
        # Local decoder reads remain usable. No provider client or shell may run.
        if isinstance(args, (list, tuple)) and args and Path(args[0]).stem.lower() == "ffprobe" and not kwargs.get("shell"):
            return original(args, *rest, **kwargs)
        return forbidden()
    monkeypatch.setattr(subprocess, "Popen", local_read_only_process)
    from ai_commerce.media.fake_generation import FakeAsyncMediaProvider
    from ai_commerce.operations.generation import GenerationJobRunner
    from ai_commerce.creative.live import LiveCreativeReasoner
    monkeypatch.setattr(FakeAsyncMediaProvider, "submit", forbidden)
    monkeypatch.setattr(LiveCreativeReasoner, "reason", forbidden)
    for name in ("submit", "observe", "fetch", "reconcile"):
        monkeypatch.setattr(GenerationJobRunner, name, forbidden)


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def guard_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("adapter mutated native A07/A08/A15/D05 custody")
    for owner, names in ((PublicationJournal, ("create", "record", "_connect")),
                         (RetryGuard, ("register_intent", "record_attempt", "record_reconciliation")),
                         (ReconciliationLedger, ("reconcile",)),
                         (CapabilityRegistry, ("import_snapshot",)),
                         (ArtifactRegistry, ("put_immutable", "worker"))):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)


def make_native_case(tmp_path, monkeypatch, *, platform="TIKTOK", label="fixture", state="ALLOWED",
                     a07_expires="2026-11-04T00:00:00Z", receipt=False, generic_no_effect=False,
                     independent_effect=None, account_id="fixture-account", app_id="fixture-app",
                     measurement_window=None):
    """Actual A07 SQLite, A08 package/journal, A15 records, D05 ledger and L07 selector.

    Only E01 decoding is replaced: these are labeled synthetic MP4 bytes. Native
    A08/D05 reopen all exported files and verify every digest without stubs.
    """
    root = tmp_path / (platform.lower() + "-" + label)
    root.mkdir()
    profile = PROFILES[platform]
    account = {"namespace": profile["account_namespace"], "id": account_id}
    app = {"namespace": profile["app_namespace"], "id": app_id}
    source = root / "delivery"
    source.mkdir()
    video = ("SYNTHETIC_FIXTURE MP4 bytes " + label + platform).encode()
    (source / "video.mp4").write_bytes(video)
    (source / "e01-manifest.json").write_bytes(canonical_bytes({"run_id": "fixture-e01-" + label,
        "publication_status": "NOT_ATTEMPTED", "output_sha256": sha256_bytes(video)}))
    monkeypatch.setattr("ai_commerce.distribution.assisted.verify_e01", lambda _: {"state": "DELIVERED"})
    monkeypatch.setattr("ai_commerce.distribution.receipt._now", lambda: "2026-11-03T01:00:00Z")
    package_dir = root / "package"
    package = export_package(source, package_dir, platform=platform, surface=profile["surface"],
        account=account["namespace"] + ":" + account["id"], market="VN", app_ref=app,
        title="Synthetic fixture", caption="Offline fixture only")
    scope = {"platform": platform, "surface": profile["surface"], "account_ref": account, "app_ref": app,
        "provider_ref": None, "market": "VN", "channel": None, "product_ref": None, "listing_ref": None,
        "variant_ref": None, "link_ref": None, "link_url_sha256": None, "creative_ref": None,
        "creative_revision": None, "creative_sha256": None, "content_sha256": package["video_sha256"],
        "measurement_window": measurement_window or ["2026-11-01T00:00:00Z", "2026-11-04T00:00:00Z"],
        "timezone": "UTC", "cohort": None}
    journal = PublicationJournal(root / "publication.sqlite")
    pid = journal.create(package_dir, idempotency_key="native-publication")["publication_id"]
    journal.record(pid, "SUBMISSION_REPORTED")
    journal.record(pid, "OUTCOME_UNKNOWN")
    if receipt:
        proof = root / "receipt.txt"
        proof.write_bytes(b"synthetic supplied receipt for native journal")
        journal.record(pid, "PUBLICATION_RECEIPT", evidence_file=proof, provider_post_id="fixture-post")
        visibility = root / "visibility.txt"
        visibility.write_bytes(b"synthetic separate supplied visibility screenshot")
        journal.record(pid, "VISIBILITY_EVIDENCE", evidence_file=visibility, public_url="https://example.test/fixture-post")
    registry = ArtifactRegistry(root / "a15")
    guard = RetryGuard(registry)
    request_digest = publication_request_digest(package, pid, scope)
    guard.register_intent("fixture-intent", request_digest)
    proof = b"synthetic uncertain native attempt"
    registry.put_immutable("proofs/attempt.json", proof)
    guard.record_attempt(AttemptObservation("fixture-intent", 1, "UNKNOWN_EFFECT", "UNKNOWN",
                                           "proofs/attempt.json", sha256_bytes(proof)))
    if generic_no_effect:
        proof = b"synthetic supplied generic no-effect claim"
        registry.put_immutable("proofs/generic.json", proof)
        guard.record_reconciliation(ReconciliationObservation("fixture-intent", 1, request_digest,
            digest(guard.read_attempt("fixture-intent", 1)), "NONE", "proofs/generic.json", sha256_bytes(proof)))
    args = {"package_dir": package_dir, "journal": journal, "guard": guard, "scope": scope,
            "publication_id": pid, "intent_id": "fixture-intent", "attempt": 1}
    if independent_effect is not None:
        proof = b"synthetic fixture of separately supplied scoped observation"
        registry.put_immutable("proofs/independent.json", proof)
        doc = {"schema_version": "DistributionScopedObservation.v1", "observation_id": "independent-fixture",
            "publication_id": pid, "intent_id": "fixture-intent", "attempt": 1, "request_digest": request_digest,
            "original_record_sha256": digest(guard.read_attempt("fixture-intent", 1)), "scope": deepcopy(scope),
            "observed_at": "2026-11-03T01:30:00Z", "side_effects": independent_effect,
            "provider_post_id": "fixture-post" if independent_effect == "CONFIRMED" else None,
            "evidence_ref": "proofs/independent.json", "evidence_sha256": sha256_bytes(proof), "evidence_origin": "USER_SUPPLIED"}
        raw = canonical_bytes(doc)
        registry.put_immutable("observations/independent.json", raw)
        args["observation_pins"] = ({"ref": "observations/independent.json", "sha256": sha256_bytes(raw)},)
    sources = inspect_sources(**args)
    ledger = ReconciliationLedger(ArtifactRegistry(root / "d05"))
    correction = ledger.reconcile(key="native-correction", expected_source_sha256=sources["source_sha256"], **args)
    selection = PublicationSelection(package_dir=package_dir, journal=journal, guard=guard, publication_id=pid,
        intent_id="fixture-intent", attempt=1, source_sha256=sources["source_sha256"],
        observation_pins=args.get("observation_pins", ()), reconciliation_ledger=ledger, reconciliation_id=correction.record_id)
    binding = NativeBinding(selection, deepcopy(scope))
    a07 = CapabilityRegistry(root / "a07.sqlite")
    action = profile["a07_action"]
    action_profile = provider_profiles()["platforms"][platform]["actions"][action]
    scopes = action_profile.get("required_scopes", [])
    constraints = {"visibility": ["SELF_ONLY"]} if platform == "TIKTOK" else {}
    if platform == "INSTAGRAM":
        scopes = action_profile["login_modes"]["INSTAGRAM_LOGIN"]
        constraints = {"login_mode": "INSTAGRAM_LOGIN", "account_class": "PROFESSIONAL"}
    snapshot = {"schema_version": "ChannelCapabilitySnapshot.v1", "snapshot_id": "ccs_fixture_rights_0001",
        "platform": platform, "surface": profile["surface"], "account_ref": account, "app_ref": app,
        "market": "VN", "action": action, "state": state, "constraints": constraints,
        "scope_claims": [{"name": name, "status": "GRANTED"} for name in scopes],
        "evidence_refs": [{"evidence_id": "ev_fixture_rights_0001", "kind": "TEST_FIXTURE", "integrity": "HASHED",
            "sha256": sha256_bytes(b"synthetic A07 fixture rights"), "scope": "TARGET",
            "subject": {"account_ref": account, "app_ref": app}, "captured_at": "2026-11-03T00:00:00Z"}],
        "observed_at": "2026-11-03T00:00:00Z", "recorded_at": "2026-11-03T00:01:00Z",
        "expires_at": a07_expires, "source_mode": "TEST_FIXTURE", "profile_version": "v1.0"}
    a07.import_snapshot(snapshot)
    request = DirectPostRequest.from_dict({"schema_version": "DirectPostRequest.v1", "adapter_version": VERSION,
        "mode": "OFFLINE", "target": {"platform": platform, "surface": profile["surface"], "account_ref": account,
            "app_ref": app, "market": "VN", "package_id": package["package_id"], "package_sha256": package["package_sha256"],
            "content_sha256": package["video_sha256"], "media_type": "video/mp4"},
        "source_sha256": sources["source_sha256"], "idempotency_key": "fixture-request",
        "requested_at": "2026-11-03T02:00:00Z", "expires_at": "2026-11-03T03:00:00Z"})
    consent = propose_human_opt_in(request, actor_alias="fixture-human", decided_at="2026-11-03T02:00:00Z",
                                  expires_at="2026-11-03T03:00:00Z")
    return {"root": root, "source": binding, "request": request, "consent": consent, "registry": a07,
            "snapshot": snapshot, "args": args, "ledger": ledger, "package": package,
            "adapter": OfflineDirectPostAdapter(platform, a07)}


def simulate(case, **changes):
    values = {"source": case["source"], "consent": case["consent"], "at": AT}
    values.update(changes)
    return case["adapter"].simulate(case["request"], **values)


def callback_for(receipt, **changes):
    value = receipt.to_dict()
    result = {"schema_version": "SyntheticAdapterCallback.v1", "event_id": "fixture-callback",
        "receipt_sha256": value["record_sha256"], "target": value["target"], "source_sha256": value["source_sha256"],
        "outcome": "UNKNOWN", "provenance": "SYNTHETIC_FIXTURE", "provider_attested": False}
    result.update(changes)
    return result


def reseal(value):
    body = {k: v for k, v in value.items() if k not in {"receipt_id", "record_sha256"}}
    sha = digest(body)
    return SyntheticReceipt.from_bytes(canonical({**body, "record_sha256": sha,
        "receipt_id": "synthetic-" + value["target"]["platform"].lower() + "-" + sha}))


@pytest.mark.parametrize("platform", ["TIKTOK", "INSTAGRAM", "YOUTUBE"])
@pytest.mark.parametrize("outcome", ["SUBMITTED", "UNKNOWN", "REJECTED", "CONFIRMED"])
def test_native_rehearsal_replay_no_write_no_live(platform, outcome, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch, platform=platform)
    before = tree_bytes(case["root"])
    guard_effects(monkeypatch)
    receipt = simulate(case, outcome=outcome)
    value = receipt.to_dict()
    assert value["fixture_state"] == outcome and value["provenance"] == "SYNTHETIC_FIXTURE"
    assert value["publication_status"] == value["public_visibility"] == "UNKNOWN"
    assert value["native_decision"] == "RECONCILE_REQUIRED"
    assert not value["real_post_created"] and not value["provider_attested"] and not value["retry_dispatched"]
    assert simulate(case, outcome=outcome).to_bytes() == receipt.to_bytes()
    assert simulate(case, outcome=outcome, at=AT + timedelta(minutes=1)).to_bytes() == receipt.to_bytes()
    assert SyntheticReceipt.from_bytes(receipt.to_bytes()) == receipt
    denial = submit_live(case["request"])
    assert denial["reason"] == "ENTITLEMENT_UNPROVEN" and not denial["provider_called"]
    assert tree_bytes(case["root"]) == before


def test_a07_allow_is_unchanged_offline_evidence_and_never_entitlement(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    native_request = {"schema_version": "ChannelCapabilityRequest.v1",
        **{k: case["request"].to_dict()["target"][k] for k in ("platform", "surface", "account_ref", "app_ref", "market")},
        "action": "API_DIRECT_POST", "simulation": True}
    before = evaluate(native_request, case["registry"], at=AT)
    guard_effects(monkeypatch)
    view = capability_for(case["request"], case["registry"], at=AT)
    assert view["a07_decision_sha256"] == digest(before) and view["a07_decision"] == "ALLOW"
    view["live_eligible"] = True
    assert capability_for(case["request"], case["registry"], at=AT)["live_eligible"] is False
    assert submit_live(case["request"])["live_authorized"] is False


@pytest.mark.parametrize("state,expires", [("UNKNOWN", "2026-11-04T00:00:00Z"), ("REVOKED", "2026-11-04T00:00:00Z"),
    ("DENIED", "2026-11-04T00:00:00Z"), ("ALLOWED", "2026-11-03T02:00:00Z")])
def test_stale_revoked_unproven_rights_fail_closed(state, expires, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch, state=state, a07_expires=expires)
    guard_effects(monkeypatch)
    with pytest.raises(AdapterError, match="OFFLINE_CAPABILITY_NOT_ALLOWED"):
        simulate(case)
    assert not case["adapter"]._receipts


def test_rights_are_reopened_on_replay_and_revocation_is_not_cached(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    simulate(case)
    snapshot = deepcopy(case["snapshot"])
    snapshot.update(snapshot_id="ccs_fixture_rights_0002", supersedes_snapshot_id=case["snapshot"]["snapshot_id"],
                    state="REVOKED", observed_at="2026-11-03T01:00:00Z", recorded_at="2026-11-03T01:01:00Z")
    case["registry"].import_snapshot(snapshot)
    guard_effects(monkeypatch)
    with pytest.raises(AdapterError, match="OFFLINE_CAPABILITY_NOT_ALLOWED"):
        simulate(case)


@pytest.mark.parametrize("field,new", [("content_sha256", "b" * 64), ("package_sha256", "c" * 64),
    ("package_id", "a08-wrong-package"), ("market", "US"),
    ("account_ref", {"namespace": "tiktok_open_id", "id": "other-account"}),
    ("app_ref", {"namespace": "tiktok_client_key", "id": "other-app"})])
def test_native_identity_substitution_rejected(field, new, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    value = case["request"].to_dict()
    value["target"][field] = new
    request = DirectPostRequest.from_dict(value)
    guard_effects(monkeypatch)
    with pytest.raises(AdapterError):
        case["source"].reopen(request)


@pytest.mark.parametrize("change", [{"surface": "REEL"}, {"platform": "INSTAGRAM"},
    {"account_ref": {"namespace": "internal_alias", "id": "fixture-account"}},
    {"app_ref": {"namespace": "google_cloud_project_id", "id": "fixture-app"}}])
def test_channel_surface_and_namespace_are_not_portable(change, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    value = case["request"].to_dict()
    value["target"].update(change)
    with pytest.raises(AdapterError):
        DirectPostRequest.from_dict(value)


@pytest.mark.parametrize("kind", ["changed-key-payload", "changed-outcome", "rekey", "consent-cross-request"])
def test_idempotency_unknown_collision_and_rekey_require_reconciliation(kind, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    original = simulate(case)
    if kind == "changed-outcome":
        with pytest.raises(AdapterError, match="IDEMPOTENCY_COLLISION"):
            simulate(case, outcome="CONFIRMED")
    else:
        raw = case["request"].to_dict()
        raw["idempotency_key" if kind == "rekey" else "expires_at"] = "another-key" if kind == "rekey" else "2026-11-03T02:59:00Z"
        request = DirectPostRequest.from_dict(raw)
        consent = case["consent"] if kind == "consent-cross-request" else propose_human_opt_in(request,
            actor_alias="fixture-human", decided_at="2026-11-03T02:00:00Z", expires_at=raw["expires_at"])
        with pytest.raises(AdapterError, match="REKEYED|COLLISION|CONSENT_BINDING"):
            case["adapter"].simulate(request, source=case["source"], consent=consent, at=AT)
    assert simulate(case).to_bytes() == original.to_bytes() and len(case["adapter"]._receipts) == 1


def test_concurrent_explicit_simulations_have_one_process_local_receipt(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: simulate(case).to_bytes(), range(4)))
    assert len(set(receipts)) == 1 and len(case["adapter"]._receipts) == 1


@pytest.mark.parametrize("outcome", ["UNKNOWN", "AMBIGUOUS", "NO_EFFECT", "CONFIRMED"])
def test_callbacks_never_promote_unknown_or_authorize_retry(outcome, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    before = tree_bytes(case["root"])
    guard_effects(monkeypatch)
    receipt = simulate(case)
    callback = callback_for(receipt, outcome=outcome)
    first = case["adapter"].inspect_callback(case["request"], receipt, callback)
    assert first["decision"] == "RECONCILE_REQUIRED" and not first["receipt_changed"] and not first["retry_dispatched"]
    assert case["adapter"].inspect_callback(case["request"], receipt, callback) == first
    with pytest.raises(AdapterError, match="REKEYED_CALLBACK"):
        case["adapter"].inspect_callback(case["request"], receipt, {**callback, "event_id": "new-event-key"})
    assert receipt.to_dict()["fixture_state"] == "UNKNOWN" and tree_bytes(case["root"]) == before


def test_callback_collision_and_forged_receipt_cannot_enter_custody(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    receipt = simulate(case)
    callback = callback_for(receipt)
    case["adapter"].inspect_callback(case["request"], receipt, callback)
    with pytest.raises(AdapterError, match="CALLBACK_ID_COLLISION"):
        case["adapter"].inspect_callback(case["request"], receipt, {**callback, "outcome": "NO_EFFECT"})
    forged = reseal({**receipt.to_dict(), "fixture_state": "CONFIRMED"})
    with pytest.raises(AdapterError, match="RECEIPT_NOT_ISSUED_HERE"):
        case["adapter"].require_issued(forged, case["request"])
    changed = deepcopy(callback)
    changed["target"]["app_ref"]["id"] = "wrong-app"
    with pytest.raises(AdapterError, match="CALLBACK_SOURCE_MISMATCH"):
        case["adapter"].inspect_callback(case["request"], receipt, changed)


@pytest.mark.parametrize("field,value", [("provider_attested", True), ("public_visibility", "PUBLIC"),
    ("real_post_created", True), ("publication_status", "PUBLISHED_CONFIRMED"), ("provider_post_id", "forged-post")])
def test_synthetic_receipt_cannot_claim_provider_publication(field, value, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    receipt = simulate(case, outcome="CONFIRMED").to_dict()
    receipt[field] = value
    with pytest.raises(AdapterError):
        SyntheticReceipt.from_bytes(canonical(receipt))


@pytest.mark.parametrize("kind", ["package-byte", "source-pin", "d05-file", "journal-corrupt", "retry-proof"])
def test_native_custody_is_reopened_and_corruption_fails_closed(kind, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    if kind == "package-byte":
        (case["source"].selection.package_dir / "video.mp4").write_bytes(b"changed media")
    elif kind == "source-pin":
        case["source"] = replace(case["source"], selection=replace(case["source"].selection, source_sha256="a" * 64))
    elif kind == "d05-file":
        path = case["ledger"].registry.safe_path(case["ledger"].ref(1))
        path.write_bytes(b'{}')
    elif kind == "journal-corrupt":
        case["args"]["journal"].path.write_bytes(b"invalid sqlite")
    else:
        case["args"]["guard"].registry.safe_path("proofs/attempt.json").write_bytes(b"changed proof")
    guard_effects(monkeypatch)
    with pytest.raises(AdapterError):
        simulate(case)


@pytest.mark.parametrize("independent_effect,decision", [(None, "RECONCILE_REQUIRED"),
    ("NONE", "REQUIRES_SEPARATE_AUTHORIZATION"), ("CONFIRMED", "NO_RETRY")])
def test_native_uncertainty_no_effect_and_confirmed_never_enable_effects(independent_effect, decision, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch, independent_effect=independent_effect)
    guard_effects(monkeypatch)
    receipt = simulate(case, outcome="CONFIRMED")
    assert receipt.to_dict()["native_decision"] == decision and not receipt.to_dict()["retry_dispatched"]
    assert not submit_live(case["request"])["real_post_created"]


def test_generic_no_effect_and_user_public_receipts_cannot_supply_entitlements(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch, receipt=True, generic_no_effect=True)
    before = tree_bytes(case["root"])
    guard_effects(monkeypatch)
    native = case["source"].reopen(case["request"])
    assert native["dashboard"].to_dict()["publication"]["reported_visibility"] == "PUBLIC"
    assert native["dashboard"].to_dict()["publication"]["public_visibility"] == "UNKNOWN"
    assert simulate(case).to_dict()["native_decision"] == "RECONCILE_REQUIRED"
    assert tree_bytes(case["root"]) == before


def test_receipt_is_not_a_native_d05_observation(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    receipt = simulate(case, outcome="CONFIRMED")
    registry = case["args"]["guard"].registry
    registry.put_immutable("observations/adapter.json", receipt.to_bytes())
    args = {**case["args"], "observation_pins": ({"ref": "observations/adapter.json", "sha256": sha256_bytes(receipt.to_bytes())},)}
    guard_effects(monkeypatch)
    with pytest.raises(IntelligenceError):
        inspect_sources(**args)
    assert case["source"].reopen(case["request"])["decision"] == "RECONCILE_REQUIRED"


@pytest.mark.parametrize("value", [None, [], "LIVE", float("nan"), {"x": 1 << 100}, {"x": "x" * 4097}])
def test_unbounded_or_wrong_contract_values_are_rejected(value):
    with pytest.raises(AdapterError):
        DirectPostRequest.from_dict(value)


@pytest.mark.parametrize("field,value", [("mode", "LIVE"), ("transport", "provider"), ("permissions", ["publish"]),
    ("expires_at", "2026-11-03T02:00:00"), ("expires_at", "2026-11-05T02:00:00Z"),
    ("idempotency_key", "person@example.test"), ("idempotency_key", "Bearer abcdefghijkl"),
    ("idempotency_key", "+84912345678"), ("access_token", "synthetic-secret"), ("phone", "+84912345678")])
def test_contract_extras_time_secrets_and_contact_data_rejected(field, value, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    raw = case["request"].to_dict()
    raw[field] = value
    with pytest.raises(AdapterError) as error:
        DirectPostRequest.from_dict(raw)
    assert "example.test" not in str(error.value) and "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize("change", [{"live_authorized": True}, {"authorization_verified": True},
    {"permissions": ["publish"]}, {"decision": "ALLOW"}, {"actor_alias": "person@example.test"}])
def test_human_proposal_cannot_forge_actual_authorization(change, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    raw = case["consent"].to_dict()
    raw.update(change)
    with pytest.raises(AdapterError):
        HumanConsent.from_dict(raw)


@pytest.mark.parametrize("when", [AT - timedelta(seconds=1), AT + timedelta(hours=1)])
def test_request_and_consent_expiry_boundary_is_closed(when, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    with pytest.raises(AdapterError, match="REQUEST_EXPIRED_OR_FUTURE"):
        simulate(case, at=when)


def test_exact_keys_duplicate_json_and_immutable_detached_records(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    with pytest.raises(AdapterError, match="JSON_DUPLICATE_KEY"):
        DirectPostRequest.from_bytes(b'{"mode":"OFFLINE","mode":"LIVE"}')
    with pytest.raises(FrozenInstanceError):
        case["request"]._raw = b"changed"
    raw = case["request"].to_dict()
    raw["target"]["account_ref"]["id"] = "other-account"
    assert case["request"].to_dict()["target"]["account_ref"]["id"] == "fixture-account"
    with pytest.raises(AdapterError, match="TYPED_REQUEST_REQUIRED"):
        submit_live(case["request"].to_dict())
    with pytest.raises(TypeError):
        submit_live(case["request"], transport=lambda: pytest.fail("transport ran"))


def test_contract_mirrors_manifest_and_exact_consumer_ownership_shape():
    for path, expected in (("contracts/DirectPostAdapter.v1.schema.json", direct_post_contract()),
                           ("contracts/ChannelAdapterCatalog.v1.schema.json", catalog_contract())):
        actual = json.loads((ROOT / path).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(actual)
        assert actual == expected
    manifest = json.loads((ROOT / "work_items/mt01-components/a17-3.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"schema_version", "component", "sources", "tests", "acs", "consumers", "contracts"}
    assert manifest["schema_version"] == "MT01ComponentOwner.v1" and manifest["component"] == "a17-3"
    assert manifest["acs"] == ["A17.D07.D08.offline-capability-gated-adapters"]
    assert len(manifest["sources"]) == 5 and len(manifest["tests"]) == 2 and len(manifest["contracts"]) == 2
    assert manifest["consumers"] == ["src/ai_commerce/distribution/evaluator.py", "src/ai_commerce/distribution/model.py",
        "src/ai_commerce/distribution/receipt.py", "src/ai_commerce/distribution_intelligence/reconciliation.py",
        "src/ai_commerce/distribution_intelligence/dashboard.py"]
    assert all((ROOT / path).is_file() for path in manifest["consumers"])
