"""WAKEHUB.cdp-verified-turn-seam: deterministic offline source invariants.

Every ID, signature, durable receipt and native source label here is a MOCK
claim. No fixture authenticates an account or USER turn. These tests provide
NO native Windows/CDP 9444, real ChatGPT turn or plugin invocation proof.
Trusted EF01 owns execution and owner/consumer MT01 selection after editing.
"""
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from ai_commerce import wakehub_cdp as cdp
from ai_commerce.wakehub_cdp.authenticated_turn import RegisteredTurnContext


NOW = 100


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Native/network/process operation attempted")

    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)


@pytest.fixture
def claims():
    conversation_id = "00000000-0000-0000-0000-000000000001"
    pins = cdp.OriginPins(
        account_id="fixture-account-a", conversation_id=conversation_id,
        session_ref="fixture-session", route_generation=1,
        registration_marker="fixture-marker", browser_custody_ref="fixture-custody",
        chrome_profile_id="fixture-profile", context_ref="fixture-context",
        tab_ref="fixture-tab", composer_ref="fixture-composer",
    )
    context = RegisteredTurnContext(
        chat_url="https://chatgpt.com/c/" + conversation_id,
        windows_sid="S-1-5-21-111-222-333-1001", windows_session_id=7,
        windows_logon_id="fixture-logon", desktop=r"WinSta0\Default",
        event_payload_sha256="b" * 64,
    )
    message = "fixture exact wake message\r\nTiếng Việt".encode("utf-8")
    scope = cdp.TurnScope(pins, "ef02-completion-v1:" + "a" * 64,
                          hashlib.sha256(message).hexdigest(), registered_context=context)
    request = cdp.WakeRequest(scope, message)
    grant = cdp.SignedWakeGrant(scope, "fixture-grant", "fixture-issuer", "fixture-key",
                                b"NOT-A-REAL-SIGNATURE", 80, 120)
    reservation = cdp.DurableReservation(scope, "fixture-intent", grant.grant_ref,
                                          "fixture-NOT-durable-receipt", 90)
    observation = cdp.BrowserObservation(scope, "fixture-observation", 1, 1, True, True)
    # Deliberately spoof the native label. Even a perfect-looking shape must
    # fail closed; this label and signature supply NO native authentication.
    attestation = cdp.NativeAttestation(
        scope, cdp.AttestationPhase.PRE_SEND, cdp.EvidenceSource.NATIVE_PRIVATE_PROVIDER,
        "fixture-attestation", "fixture-private-issuer", "fixture-key",
        b"NOT-A-REAL-ATTESTATION", grant.grant_ref, reservation.reservation_ref,
        observation.observation_ref, 95, 115,
    )
    evidence = cdp.TurnEvidence(observation, grant, reservation, attestation)
    turn = cdp.ChatGPTUserTurnRef(
        scope, "fixture-forged-turn", "fixture-forged-message", attestation.attestation_ref,
        grant.grant_ref, reservation.reservation_ref, cdp.EvidenceSource.NATIVE_PRIVATE_PROVIDER,
    )
    return request, evidence, turn


def assert_closed(result, status, code, phase):
    assert result.status is status
    assert result.code is code
    assert result.phase is phase
    assert result.send_allowed is False
    assert result.retry_allowed is False
    assert result.authenticated_user_turn is None


def post_evidence(evidence):
    if type(evidence) is cdp.TurnEvidence and type(evidence.attestation) is cdp.NativeAttestation:
        return replace(evidence, attestation=replace(
            evidence.attestation, phase=cdp.AttestationPhase.POST_SEND))
    return evidence


def assert_rejected_both(request, evidence, turn, code):
    assert_closed(cdp.pre_send(request, evidence, now=NOW), cdp.GateStatus.HOLD,
                  code, cdp.AttestationPhase.PRE_SEND)
    assert_closed(cdp.post_send(request, post_evidence(evidence), now=NOW,
                               outcome=cdp.SendOutcome.CLICK_REPORTED, user_turn=turn),
                  cdp.GateStatus.UNKNOWN_EFFECT, code, cdp.AttestationPhase.POST_SEND)


def test_replaying_matching_event_and_grant_claims_never_becomes_authority(claims):
    request, evidence, turn = claims
    for _ in range(2):
        assert_closed(cdp.pre_send(request, evidence, now=NOW), cdp.GateStatus.UNAVAILABLE,
                      cdp.GateCode.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE, cdp.AttestationPhase.PRE_SEND)
        assert_closed(cdp.post_send(request, post_evidence(evidence), now=NOW,
                                   outcome=cdp.SendOutcome.CLICK_REPORTED, user_turn=turn),
                      cdp.GateStatus.UNKNOWN_EFFECT, cdp.GateCode.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE,
                      cdp.AttestationPhase.POST_SEND)


@pytest.mark.parametrize("owner,code", [
    ("observation", cdp.GateCode.SCOPE_MISMATCH),
    ("grant", cdp.GateCode.GRANT_SCOPE_MISMATCH),
    ("reservation", cdp.GateCode.RESERVATION_SCOPE_MISMATCH),
    ("attestation", cdp.GateCode.ATTESTATION_SCOPE_MISMATCH),
    ("user_turn", cdp.GateCode.USER_TURN_SCOPE_MISMATCH),
])
@pytest.mark.parametrize("field,value", [
    ("account_id", "wrong-account"), ("conversation_id", "wrong-chat"),
    ("session_ref", "wrong-session"), ("registration_marker", "wrong-marker"),
    ("route_generation", 2), ("chrome_profile_id", "wrong-profile"),
    ("browser_custody_ref", "wrong-browser-custody"),
    ("context_ref", "wrong-context"), ("tab_ref", "wrong-tab"),
    ("composer_ref", "wrong-composer"), ("cdp_endpoint", "http://127.0.0.1:9222"),
])
def test_every_origin_pin_must_match_every_claim(claims, owner, code, field, value):
    request, evidence, turn = claims
    wrong_scope = replace(request.scope, origin=replace(request.scope.origin, **{field: value}))
    if owner == "user_turn":
        result = cdp.post_send(request, post_evidence(evidence), now=NOW,
                               outcome=cdp.SendOutcome.CLICK_REPORTED,
                               user_turn=replace(turn, scope=wrong_scope))
        assert_closed(result, cdp.GateStatus.UNKNOWN_EFFECT, code, cdp.AttestationPhase.POST_SEND)
    else:
        evidence = replace(evidence, **{owner: replace(getattr(evidence, owner), scope=wrong_scope)})
        assert_rejected_both(request, evidence, turn, code)


@pytest.mark.parametrize("owner,code", [
    ("observation", cdp.GateCode.SCOPE_MISMATCH),
    ("grant", cdp.GateCode.GRANT_SCOPE_MISMATCH),
    ("reservation", cdp.GateCode.RESERVATION_SCOPE_MISMATCH),
    ("attestation", cdp.GateCode.ATTESTATION_SCOPE_MISMATCH),
])
@pytest.mark.parametrize("field,value", [
    ("event_id", "ef02-completion-v1:" + "b" * 64), ("message_sha256", "c" * 64),
])
def test_event_and_exact_digest_bound_to_each_claim(claims, owner, code, field, value):
    request, evidence, turn = claims
    candidate = replace(getattr(evidence, owner), scope=replace(request.scope, **{field: value}))
    assert_rejected_both(request, replace(evidence, **{owner: candidate}), turn, code)


@pytest.mark.parametrize("field,value", [
    ("chat_url", "https://chatgpt.com/g/g-fixture/c/00000000-0000-0000-0000-000000000001"),
    ("windows_sid", "S-1-5-21-111-222-333-1002"),
    ("windows_session_id", 8), ("windows_logon_id", "other-logon"),
    ("desktop", r"WinSta0\Other"), ("event_payload_sha256", "c" * 64),
])
def test_copied_marker_cannot_cover_registered_context_drift(claims, field, value):
    request, evidence, turn = claims
    context = replace(request.scope.registered_context, **{field: value})
    scope = replace(request.scope, registered_context=context)
    changed = replace(evidence, observation=replace(evidence.observation, scope=scope))
    assert_rejected_both(request, changed, turn, cdp.GateCode.SCOPE_MISMATCH)


def test_registered_url_must_name_the_expected_conversation(claims):
    request, evidence, turn = claims
    context = replace(request.scope.registered_context,
                      chat_url="https://chatgpt.com/c/00000000-0000-0000-0000-000000000002")
    request = replace(request, scope=replace(request.scope, registered_context=context))
    assert_rejected_both(request, evidence, turn, cdp.GateCode.INVALID_REQUEST)


def test_post_send_user_turn_cannot_omit_registered_context(claims):
    request, evidence, turn = claims
    turn = replace(turn, scope=replace(turn.scope, registered_context=None))
    assert_closed(cdp.post_send(request, post_evidence(evidence), now=NOW,
                               outcome=cdp.SendOutcome.CLICK_REPORTED, user_turn=turn),
                  cdp.GateStatus.UNKNOWN_EFFECT, cdp.GateCode.USER_TURN_SCOPE_MISMATCH,
                  cdp.AttestationPhase.POST_SEND)


@pytest.mark.parametrize("message", [b"different", b"fixture exact wake message\nTieng Viet", b" "])
def test_exact_message_bytes_cannot_be_replaced_or_normalized(claims, message):
    request, evidence, turn = claims
    assert_rejected_both(replace(request, message_bytes=message), evidence, turn,
                         cdp.GateCode.MESSAGE_DIGEST_MISMATCH)


@pytest.mark.parametrize("request_change", [
    {"message_bytes": "not-bytes"}, {"message_bytes": b""}, {"scope": None},
])
def test_invalid_request_has_typed_hold_without_native_work(claims, request_change):
    request, evidence, turn = claims
    assert_rejected_both(replace(request, **request_change), evidence, turn,
                         cdp.GateCode.INVALID_REQUEST)


@pytest.mark.parametrize("field,value", [
    ("route_generation", True), ("route_generation", 0), ("account_id", ""),
    ("cdp_endpoint", "http://localhost:9444"), ("tab_ref", "bad\nref"),
])
def test_invalid_expected_pins_do_not_create_authority(claims, field, value):
    request, evidence, turn = claims
    scope = replace(request.scope, origin=replace(request.scope.origin, **{field: value}))
    assert_rejected_both(replace(request, scope=scope), evidence, turn, cdp.GateCode.INVALID_REQUEST)


@pytest.mark.parametrize("now", [True, -1, 100.0, None])
def test_time_input_only_rejects_never_authenticates(claims, now):
    request, evidence, turn = claims
    assert_closed(cdp.pre_send(request, evidence, now=now), cdp.GateStatus.HOLD,
                  cdp.GateCode.INVALID_CLOCK, cdp.AttestationPhase.PRE_SEND)
    assert_closed(cdp.post_send(request, post_evidence(evidence), now=now,
                               outcome=cdp.SendOutcome.CLICK_REPORTED, user_turn=turn),
                  cdp.GateStatus.UNKNOWN_EFFECT, cdp.GateCode.INVALID_CLOCK,
                  cdp.AttestationPhase.POST_SEND)


@pytest.mark.parametrize("field,code", [
    ("observation", cdp.GateCode.OBSERVATION_MISSING),
    ("grant", cdp.GateCode.GRANT_MISSING),
    ("reservation", cdp.GateCode.RESERVATION_MISSING),
    ("attestation", cdp.GateCode.NATIVE_ATTESTATION_MISSING),
])
def test_missing_required_evidence(claims, field, code):
    request, evidence, turn = claims
    assert_rejected_both(request, replace(evidence, **{field: None}), turn, code)


def test_untyped_evidence_is_not_a_provider(claims):
    request, _, turn = claims
    assert_rejected_both(request, {"verified": True}, turn, cdp.GateCode.INVALID_EVIDENCE)


@pytest.mark.parametrize("field,value,code", [
    ("context_count", 2, cdp.GateCode.SURFACE_NOT_EXCLUSIVE),
    ("tab_count", 2, cdp.GateCode.SURFACE_NOT_EXCLUSIVE),
    ("context_count", 0, cdp.GateCode.SURFACE_NOT_EXCLUSIVE),
    ("tab_count", True, cdp.GateCode.SURFACE_NOT_EXCLUSIVE),
    ("composer_clean", False, cdp.GateCode.COMPOSER_DIRTY),
    ("composer_clean", 1, cdp.GateCode.COMPOSER_DIRTY),
    ("surface_stable", False, cdp.GateCode.SURFACE_UNSTABLE),
    ("observation_ref", "", cdp.GateCode.SURFACE_UNSTABLE),
])
def test_exclusive_stable_clean_surface_required(claims, field, value, code):
    request, evidence, turn = claims
    assert_rejected_both(request, replace(evidence, observation=replace(
        evidence.observation, **{field: value})), turn, code)


@pytest.mark.parametrize("field,value,code", [
    ("operation", "resolve_claim_route", cdp.GateCode.GRANT_INVALID),
    ("one_shot", False, cdp.GateCode.GRANT_INVALID),
    ("signature", b"", cdp.GateCode.GRANT_INVALID),
    ("signature", "signature", cdp.GateCode.GRANT_INVALID),
    ("issuer_ref", "", cdp.GateCode.GRANT_INVALID),
    ("key_id", "", cdp.GateCode.GRANT_INVALID),
    ("grant_ref", "", cdp.GateCode.GRANT_INVALID),
    ("use_count", 1, cdp.GateCode.GRANT_REUSED),
    ("use_count", -1, cdp.GateCode.GRANT_REUSED),
    ("use_count", False, cdp.GateCode.GRANT_REUSED),
    ("issued_at", NOW + 1, cdp.GateCode.GRANT_NOT_FRESH),
    ("expires_at", NOW, cdp.GateCode.GRANT_NOT_FRESH),
    ("expires_at", NOW - 1, cdp.GateCode.GRANT_NOT_FRESH),
])
def test_signed_one_shot_grant_rejects_wrong_scope_reuse_and_expiry(claims, field, value, code):
    request, evidence, turn = claims
    assert_rejected_both(request, replace(evidence, grant=replace(
        evidence.grant, **{field: value})), turn, code)


@pytest.mark.parametrize("field,value,code", [
    ("grant_ref", "other-grant", cdp.GateCode.RESERVATION_SCOPE_MISMATCH),
    ("state", "PREPARED", cdp.GateCode.RESERVATION_NOT_DURABLE_RESERVED),
    ("durable_receipt_ref", "", cdp.GateCode.RESERVATION_NOT_DURABLE_RESERVED),
    ("reservation_ref", "", cdp.GateCode.RESERVATION_NOT_DURABLE_RESERVED),
    ("reserved_at", NOW + 1, cdp.GateCode.RESERVATION_NOT_DURABLE_RESERVED),
    ("reserved_at", 79, cdp.GateCode.RESERVATION_NOT_DURABLE_RESERVED),
])
def test_reservation_must_claim_durable_reserved_intent_for_same_grant(claims, field, value, code):
    request, evidence, turn = claims
    assert_rejected_both(request, replace(evidence, reservation=replace(
        evidence.reservation, **{field: value})), turn, code)


@pytest.mark.parametrize("source", [
    source for source in cdp.EvidenceSource if source is not cdp.EvidenceSource.NATIVE_PRIVATE_PROVIDER
] + ["NATIVE_PRIVATE_PROVIDER"])
def test_non_native_attestation_sources_never_authenticate(claims, source):
    request, evidence, turn = claims
    assert_rejected_both(request, replace(evidence, attestation=replace(
        evidence.attestation, source=source)), turn, cdp.GateCode.WRONG_AUTHORITY_SOURCE)


@pytest.mark.parametrize("field,value,code", [
    ("signature", b"", cdp.GateCode.ATTESTATION_INVALID),
    ("attestation_ref", "", cdp.GateCode.ATTESTATION_INVALID),
    ("issuer_ref", "", cdp.GateCode.ATTESTATION_INVALID),
    ("key_id", "", cdp.GateCode.ATTESTATION_INVALID),
    ("grant_ref", "other-grant", cdp.GateCode.ATTESTATION_SCOPE_MISMATCH),
    ("reservation_ref", "other-reservation", cdp.GateCode.ATTESTATION_SCOPE_MISMATCH),
    ("observation_ref", "other-observation", cdp.GateCode.ATTESTATION_SCOPE_MISMATCH),
    ("issued_at", 89, cdp.GateCode.ATTESTATION_NOT_FRESH),
    ("issued_at", NOW + 1, cdp.GateCode.ATTESTATION_NOT_FRESH),
    ("expires_at", NOW, cdp.GateCode.ATTESTATION_NOT_FRESH),
])
def test_attestation_requires_bound_signed_fresh_native_claims(claims, field, value, code):
    request, evidence, turn = claims
    assert_rejected_both(request, replace(evidence, attestation=replace(
        evidence.attestation, **{field: value})), turn, code)


def test_pre_and_post_attestations_cannot_substitute_for_each_other(claims):
    request, evidence, turn = claims
    assert_closed(cdp.pre_send(request, post_evidence(evidence), now=NOW), cdp.GateStatus.HOLD,
                  cdp.GateCode.ATTESTATION_PHASE_MISMATCH, cdp.AttestationPhase.PRE_SEND)
    assert_closed(cdp.post_send(request, evidence, now=NOW,
                               outcome=cdp.SendOutcome.CLICK_REPORTED, user_turn=turn),
                  cdp.GateStatus.UNKNOWN_EFFECT, cdp.GateCode.ATTESTATION_PHASE_MISMATCH,
                  cdp.AttestationPhase.POST_SEND)


@pytest.mark.parametrize("turn", [None, "locally-forged-ref", {"role": "USER", "verified": True}])
def test_missing_or_untyped_user_turn_is_unknown_not_success(claims, turn):
    request, evidence, _ = claims
    assert_closed(cdp.post_send(request, post_evidence(evidence), now=NOW,
                               outcome=cdp.SendOutcome.CLICK_REPORTED, user_turn=turn),
                  cdp.GateStatus.UNKNOWN_EFFECT, cdp.GateCode.USER_TURN_MISSING,
                  cdp.AttestationPhase.POST_SEND)


@pytest.mark.parametrize("field,value,code", [
    ("source", cdp.EvidenceSource.DOM, cdp.GateCode.WRONG_AUTHORITY_SOURCE),
    ("source", cdp.EvidenceSource.URL, cdp.GateCode.WRONG_AUTHORITY_SOURCE),
    ("source", cdp.EvidenceSource.SCREENSHOT, cdp.GateCode.WRONG_AUTHORITY_SOURCE),
    ("source", cdp.EvidenceSource.LOCAL_DIGEST, cdp.GateCode.WRONG_AUTHORITY_SOURCE),
    ("source", cdp.EvidenceSource.LOCAL_TIME, cdp.GateCode.WRONG_AUTHORITY_SOURCE),
    ("source", cdp.EvidenceSource.CALLBACK, cdp.GateCode.WRONG_AUTHORITY_SOURCE),
    ("source", cdp.EvidenceSource.SYNTHETIC_FIXTURE, cdp.GateCode.WRONG_AUTHORITY_SOURCE),
    ("source", "NATIVE_PRIVATE_PROVIDER", cdp.GateCode.WRONG_AUTHORITY_SOURCE),
    ("role", "ASSISTANT", cdp.GateCode.USER_TURN_INVALID),
    ("user_turn_id", "", cdp.GateCode.USER_TURN_INVALID),
    ("message_ref", "", cdp.GateCode.USER_TURN_INVALID),
    ("attestation_ref", "other-attestation", cdp.GateCode.USER_TURN_SCOPE_MISMATCH),
    ("grant_ref", "other-grant", cdp.GateCode.USER_TURN_SCOPE_MISMATCH),
    ("reservation_ref", "other-intent", cdp.GateCode.USER_TURN_SCOPE_MISMATCH),
])
def test_user_turn_requires_exact_role_source_and_provenance_bindings(claims, field, value, code):
    request, evidence, turn = claims
    assert_closed(cdp.post_send(request, post_evidence(evidence), now=NOW,
                               outcome=cdp.SendOutcome.CLICK_REPORTED,
                               user_turn=replace(turn, **{field: value})),
                  cdp.GateStatus.UNKNOWN_EFFECT, code, cdp.AttestationPhase.POST_SEND)


@pytest.mark.parametrize("forged_ref", [
    "native-looking-forged-id", "dom:user:1", "sha256:" + "f" * 64,
    "time:2026-11-18", "https://chatgpt.com/c/fixture#user-1", "callback:verified",
])
def test_forged_native_looking_refs_and_matching_hashes_never_become_proof(claims, forged_ref):
    request, evidence, turn = claims
    assert_closed(cdp.post_send(request, post_evidence(evidence), now=NOW,
                               outcome=cdp.SendOutcome.CLICK_REPORTED,
                               user_turn=replace(turn, user_turn_id=forged_ref, message_ref=forged_ref)),
                  cdp.GateStatus.UNKNOWN_EFFECT, cdp.GateCode.NATIVE_PRIVATE_PROVIDER_UNAVAILABLE,
                  cdp.AttestationPhase.POST_SEND)


@pytest.mark.parametrize("outcome,code", [
    (cdp.SendOutcome.CLICK_AMBIGUOUS, cdp.GateCode.CLICK_AMBIGUOUS_NO_REPLAY),
    (cdp.SendOutcome.TRANSPORT_TIMEOUT, cdp.GateCode.TRANSPORT_TIMEOUT_NO_REPLAY),
    (None, cdp.GateCode.INVALID_SEND_OUTCOME_NO_REPLAY),
    ("NOT_ATTEMPTED", cdp.GateCode.INVALID_SEND_OUTCOME_NO_REPLAY),
])
def test_ambiguous_click_or_transport_timeout_stays_unknown_without_replay(claims, outcome, code):
    request, _, _ = claims
    # Even missing/invalid claims must not downgrade a possible effect to HOLD.
    for _ in range(2):
        assert_closed(cdp.post_send(request, None, now=NOW, outcome=outcome),
                      cdp.GateStatus.UNKNOWN_EFFECT, code, cdp.AttestationPhase.POST_SEND)


def test_only_explicit_not_attempted_is_post_send_hold(claims):
    request, evidence, _ = claims
    assert_closed(cdp.post_send(request, evidence, now=NOW, outcome=cdp.SendOutcome.NOT_ATTEMPTED),
                  cdp.GateStatus.HOLD, cdp.GateCode.SEND_NOT_ATTEMPTED,
                  cdp.AttestationPhase.POST_SEND)


def test_no_callback_or_provider_flag_can_open_the_boundary(claims):
    request, evidence, _ = claims
    calls = []

    def pseudo_provider(*args, **kwargs):
        calls.append(True)
        return {"verified": True, "account_id": request.scope.origin.account_id}

    for keyword in ("provider", "verifier", "native_authority", "attestation_verified"):
        with pytest.raises(TypeError):
            cdp.pre_send(request, evidence, now=NOW, **{keyword: pseudo_provider})
    assert calls == []


def test_inputs_are_frozen_and_diagnostics_do_not_disclose_private_claims(claims):
    request, evidence, turn = claims
    result = cdp.pre_send(request, evidence, now=NOW)
    values = (request.scope.origin, request.scope.registered_context, request.scope,
              request, evidence.observation,
              evidence.grant, evidence.reservation, evidence.attestation, evidence, turn, result)
    private_tokens = (request.scope.origin.account_id, request.scope.origin.session_ref,
                      request.scope.origin.registration_marker, "NOT-A-REAL-SIGNATURE",
                      "NOT-A-REAL-ATTESTATION", "fixture exact wake message", turn.user_turn_id,
                      request.scope.registered_context.chat_url)
    for value in values:
        field = fields(value)[0]
        with pytest.raises(FrozenInstanceError):
            setattr(value, field.name, getattr(value, field.name))
        assert not any(token in repr(value) for token in private_tokens)
    with pytest.raises(TypeError):
        replace(result, send_allowed=True)


def test_atomic_owner_manifest_registers_exact_new_package_and_existing_consumers():
    root = Path(__file__).resolve().parents[1]
    manifest_path = "work_items/mt01-components/wakehub-cdp-native.json"
    manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    assert manifest == {
        "schema_version": "MT01ComponentOwner.v1", "component": "wakehub-cdp-native",
        "sources": ["src/ai_commerce/wakehub_cdp/__init__.py",
                    "src/ai_commerce/wakehub_cdp/authenticated_turn.py"],
        "tests": ["tests/test_wakehub_cdp_authenticated_turn.py"],
        "acs": ["WAKEHUB.cdp-verified-turn-seam"],
        "consumers": ["src/ai_commerce/wakehub_runtime/runner.py",
                      "src/ai_commerce/remote/cdp_doorbell.py"],
        "contracts": [],
    }
    for path in (*manifest["sources"], *manifest["tests"], *manifest["consumers"]):
        assert (root / path).is_file()
