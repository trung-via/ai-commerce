"""EF02-D3.2: operator-signed grants bound to exact local principal and registry."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
from dataclasses import replace

import pytest

from ai_commerce.remote.signed_origin_consent import (
    SignedOriginConsentAuthority, registry_sha256,
)
from ai_commerce.remote.trusted_origin import (
    LocalCallerAttestation, LocalOriginRegistrar, OriginError,
)

KEY = b"operator-private-secret-not-from-an-issue" * 2
PRINCIPAL = "local-operator-session"
CALLER = "trusted-local-bridge"
ATT = LocalCallerAttestation(caller_id=CALLER, is_local=True, attestation_verified=True)
EMPTY = {"version": 1, "routes": [], "claims": []}
REGISTER = {
    "route_id": "routeA",
    "chat_url": "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001",
    "cdp_endpoint": "http://127.0.0.1:9222",
    "consent": True,
    "generation": 1,
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def signed_grant(operation="register_route", scope=None, registry=None, *,
                 valid_until=1200, principal=PRINCIPAL, caller=CALLER, consent=True, key=KEY):
    body = {
        "kind": "AI_COMMERCE_SIGNED_ORIGIN_CONSENT", "version": 1,
        "caller_id": caller, "principal": principal,
        "operation": operation,
        "scope": copy.deepcopy(REGISTER if scope is None else scope),
        "registry_sha256": registry_sha256(EMPTY if registry is None else registry),
        "valid_until": valid_until,
        "human_consent": consent,
    }
    return {**body, "signature": hmac.new(key, canonical(body), hashlib.sha256).hexdigest()}


def authority(grants, *, key=KEY, principal=PRINCIPAL, now=1000):
    return SignedOriginConsentAuthority(
        grants=grants, secret_provider=lambda: key,
        principal_provider=lambda: principal, clock=lambda: now,
    )


def test_signed_exact_consent_authorizes_only_expected_local_principal():
    grant = signed_grant()
    a = authority([grant])
    assert a.authorize_origin(ATT, operation="register_route", context=REGISTER) is True
    assert a.authorize_origin(ATT, operation="register_route",
                              context={**REGISTER, "registry": EMPTY}) is True


@pytest.mark.parametrize("mutation", [
    lambda g: {**g, "scope": {**REGISTER, "chat_url": "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"}},
    lambda g: {**g, "scope": {**REGISTER, "consent": False}},
    lambda g: {**g, "principal": "someone-else"},
    lambda g: {**g, "caller_id": "someone-else"},
    lambda g: {**g, "registry_sha256": "0" * 64},
    lambda g: {**g, "signature": "0" * 64},
    lambda g: {**g, "valid_until": 999},
    lambda g: {**g, "human_consent": False},
])
def test_tampered_operator_grant_cannot_authorize(mutation):
    a = authority([mutation(signed_grant())])
    assert not a.authorize_origin(ATT, operation="register_route",
                                  context={**REGISTER, "registry": EMPTY})


@pytest.mark.parametrize("call", [
    {"operation": "register_route", "context": {**REGISTER, "chat_url":
     "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"}},
    {"operation": "register_route", "context": {**REGISTER, "generation": 2}},
    {"operation": "register_route", "context": {**REGISTER, "cdp_endpoint": "http://127.0.0.1:9333"}},
    {"operation": "register_route", "context": {**REGISTER, "consent": False}},
    {"operation": "bind_claim", "context": {"route_id": "routeA"}},
])
def test_legitimate_signed_grant_rejects_wrong_operation_or_scope(call):
    assert not authority([signed_grant()]).authorize_origin(ATT, **call)


def test_expired_and_missing_grants_denied():
    assert not authority([], now=1000).authorize_origin(
        ATT, operation="register_route", context=REGISTER)
    assert not authority([signed_grant(valid_until=1000)]).authorize_origin(
        ATT, operation="register_route", context=REGISTER)


def test_auth_token_and_untrusted_attestation_flags_are_not_authority():
    a = authority([signed_grant()])
    assert not a.authorize_origin(replace(ATT, caller_id="other",
                                        auth_token="same"), operation="register_route", context=REGISTER)
    assert not a.authorize_origin(replace(ATT, attestation_verified=False),
                                  operation="register_route", context=REGISTER)
    assert not a.authorize_origin(replace(ATT, is_local=False),
                                  operation="register_route", context=REGISTER)
    assert not authority([signed_grant()], principal="another-os-session").authorize_origin(
        ATT, operation="register_route", context=REGISTER)


def test_wrong_key_fails_despite_all_caller_hints():
    a = authority([signed_grant()], key=b"x" * 32)
    assert a.authorize_origin(ATT, operation="register_route", context=REGISTER) is False
    assert authority([signed_grant()], key=b"short").authorize_origin(
        ATT, operation="register_route", context=REGISTER) is False


def test_registry_snapshot_must_match_operator_signature():
    a = authority([signed_grant()])
    altered = {**EMPTY, "routes": [{"route_id": "changed"}]}
    assert a.authorize_origin(ATT, operation="register_route",
                              context={**REGISTER, "registry": altered}) is False


def test_grants_are_snapshotted_at_trusted_constructor():
    grant = signed_grant()
    a = authority([grant])
    grant["scope"]["route_id"] = "changed"
    assert a.authorize_origin(ATT, operation="register_route", context=REGISTER) is True


def test_registrar_real_registration_requires_signed_exact_consent(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    state = tmp_path / "private"
    state.mkdir()
    reg = state / "registry.json"
    registrar = LocalOriginRegistrar(reg, root, authority=authority([signed_grant()]))
    item = registrar.register_route(attestation=ATT, **REGISTER)
    assert item["route_id"] == "routeA"
    assert item["consent"] is True
    assert reg.exists()


def test_registrar_tampered_scope_cannot_touch_registry(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    reg = private / "registry.json"
    registrar = LocalOriginRegistrar(reg, root, authority=authority([signed_grant()]))
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        registrar.register_route(attestation=ATT, **{**REGISTER, "generation": 2})
    assert not reg.exists()


def test_new_signed_snapshot_required_after_registry_mutates(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    reg = private / "registry.json"
    LocalOriginRegistrar(reg, root, authority=authority([signed_grant()])).register_route(
        attestation=ATT, **REGISTER)
    # Fresh independent operator consent pins the *new* registry bytes.
    registry = LocalOriginRegistrar(reg, root).read_registry()
    scope = {
        "route_id": "routeA", "repository": "trung-via/ai-commerce",
        "batch_id": "some-batch", "source_sha": "a" * 40,
        "issue_number": 109, "generation": 1,
    }
    ok = LocalOriginRegistrar(reg, root, authority=authority([
        signed_grant("bind_claim", scope, registry)]))
    claim = ok.bind_claim(attestation=ATT, **scope)
    assert claim["batch_id"] == "some-batch"
    old = LocalOriginRegistrar(reg, root, authority=authority([
        signed_grant("bind_claim", {**scope, "batch_id": "another-batch"}, registry)]))
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        old.bind_claim(attestation=ATT, **{**scope, "batch_id": "another-batch"})
