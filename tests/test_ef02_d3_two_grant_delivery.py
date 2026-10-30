"""EF02-D3.2/D3.3 bridge: exact 2-grant D2 flow, without real ChatGPT writes."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from ai_commerce.remote.completion_event import build_completion_event
from ai_commerce.remote.signed_origin_consent import (
    SignedOriginConsentAuthority, registry_sha256,
)
from ai_commerce.remote.trusted_origin import (
    LocalCallerAttestation, LocalOriginRegistrar,
)
from ai_commerce.remote.wake_adapter import (
    GitHubArtifactProvenance, WakeReceipt, deliver_exact_chat_wake,
)

_REPO = "trung-via/ai-commerce"
_SHA = "a" * 40
_PRINCIPAL = "operator-observed-principal"
_CALLER = "trusted-worker"
_KEY = b"operator-controlled-key-for-deterministic-fake-tests" * 2
_ATTESTATION = LocalCallerAttestation(
    caller_id=_CALLER, is_local=True, attestation_verified=True,
)


def _signed(operation, scope, registry, *, human_consent=True):
    data = {
        "kind": "AI_COMMERCE_SIGNED_ORIGIN_CONSENT", "version": 1,
        "caller_id": _CALLER, "principal": _PRINCIPAL,
        "operation": operation, "scope": scope,
        "registry_sha256": registry_sha256(registry),
        "valid_until": 2_000_000_000, "human_consent": human_consent,
    }
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    data["signature"] = hmac.new(_KEY, payload, hashlib.sha256).hexdigest()
    return data


def _authority(grants):
    return SignedOriginConsentAuthority(
        grants=grants, secret_provider=lambda: _KEY,
        principal_provider=lambda: _PRINCIPAL, clock=lambda: 1_000_000_000,
    )


class _PinnedArtifactAuthority:
    """Test-only stub: real D3.1 authenticated GitHub retrieval is tested separately."""
    def __init__(self, event, report):
        self.event = event
        self.report = report

    def verify_artifact(self, provenance, *, event, batch_result):
        return event == self.event and batch_result == self.report


class _UnsupportedTransport:
    """A local fake cannot claim a ChatGPT delivery or independent receipt."""
    def __init__(self):
        self.calls = []

    def send_wake(self, *, binding, message, event_id):
        self.calls.append((binding.route_id, binding.generation, event_id))
        return WakeReceipt(
            event_id=event_id, route_id=binding.route_id,
            generation=binding.generation, delivery_status="UNSUPPORTED",
            error_code="PLATFORM_WRITE_UNSUPPORTED",
        )


def _prepared(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    private = tmp_path / "operator-private"
    private.mkdir()
    registry_path = private / "routes.json"
    store_path = private / "delivery.json"

    empty_registry = {"version": 1, "routes": [], "claims": []}
    route = {
        "route_id": "routeA",
        "chat_url": "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001",
        "cdp_endpoint": "http://127.0.0.1:9222",
        "consent": True, "generation": 1,
    }
    register = LocalOriginRegistrar(
        registry_path, root, authority=_authority([
            _signed("register_route", route, empty_registry)
        ]),
    )
    register.register_route(attestation=_ATTESTATION, **route)
    after_register = LocalOriginRegistrar(registry_path, root).read_registry()
    claim = {
        "route_id": "routeA", "repository": _REPO,
        "batch_id": "batchA", "source_sha": _SHA,
        "issue_number": 109, "generation": 1,
    }
    binder = LocalOriginRegistrar(
        registry_path, root, authority=_authority([
            _signed("bind_claim", claim, after_register)
        ]),
    )
    binder.bind_claim(attestation=_ATTESTATION, **claim)
    pinned_registry = LocalOriginRegistrar(registry_path, root).read_registry()

    report = {
        "kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
        "batch_id": "batchA", "source_sha": _SHA,
        "status": "COMPLETE", "total": 1, "succeeded": 1,
        "actions": [{
            "action_id": "D3-01", "attempt_id": "D3-attempt1",
            "status": "CANDIDATE_READY", "candidate_sha": "b" * 40,
        }],
        "github_workflow_run_id": "42", "github_issue_number": 109,
    }
    event = build_completion_event(
        report, repository=_REPO, run_id=42, run_attempt=1, issue_number=109,
    )
    report["wake_event_status"] = "RECORDED_UNBOUND"
    report["wake_event_id"] = event["event_id"]
    provenance = GitHubArtifactProvenance(
        repository=_REPO, run_id=42, run_attempt=1,
        artifact_name="test-evidence", workflow_run_status="completed",
        attestation_verified=True,
    )
    resolve_scope = {
        "repository": _REPO, "batch_id": "batchA",
        "source_sha": _SHA, "issue_number": 109,
    }
    resolve = _signed("resolve_claim_route", resolve_scope, pinned_registry)
    deliver = _signed("deliver_wake", {"event": event}, pinned_registry)
    return {
        "event": event, "batch_result": report, "provenance": provenance,
        "origin_attestation": _ATTESTATION,
        "registry_path": registry_path, "idempotency_path": store_path,
        "repository_root": root,
        "artifact_authority": _PinnedArtifactAuthority(event, report),
    }, resolve, deliver, pinned_registry


@pytest.mark.parametrize("grant_kind", ["resolve_only", "deliver_only", "none"])
def test_missing_one_of_two_grants_must_not_invoke_transport(tmp_path, grant_kind):
    args, resolve, deliver, _ = _prepared(tmp_path)
    grants = {"resolve_only": [resolve], "deliver_only": [deliver], "none": []}[grant_kind]
    transport = _UnsupportedTransport()
    receipt = deliver_exact_chat_wake(
        **args, origin_authority=_authority(grants), adapter=transport,
    )
    assert receipt.chat_delivery_proven is False
    assert receipt.delivery_status in ("UNSUPPORTED", "UNKNOWN")
    assert transport.calls == []
    assert not args["idempotency_path"].exists()


def test_exact_two_grants_allow_only_bounded_unsupported_probe_once(tmp_path):
    args, resolve, deliver, _ = _prepared(tmp_path)
    transport = _UnsupportedTransport()
    kwargs = dict(
        **args, origin_authority=_authority([resolve, deliver]),
        adapter=transport,
    )
    first = deliver_exact_chat_wake(**kwargs)
    assert first.delivery_status == "UNSUPPORTED"
    assert first.error_code == "PLATFORM_WRITE_UNSUPPORTED"
    assert first.chat_delivery_proven is False
    assert first.brain_continuation_proven is False
    assert transport.calls == [("routeA", 1, args["event"]["event_id"])]

    replay = deliver_exact_chat_wake(**kwargs)
    assert replay.delivery_status == "UNSUPPORTED"
    assert replay.replayed is True
    assert len(transport.calls) == 1


@pytest.mark.parametrize("expired_action", ["resolve", "deliver"])
def test_old_registry_digest_or_wrong_scope_blocks_wake(tmp_path, expired_action):
    args, resolve, deliver, registry = _prepared(tmp_path)
    wrong_snapshot = {**registry, "claims": []}
    bad_scope = (
        {"repository": _REPO, "batch_id": "batchB",
         "source_sha": _SHA, "issue_number": 109}
        if expired_action == "resolve"
        else {"event": {**args["event"], "batch_id": "batchB"}}
    )
    if expired_action == "resolve":
        resolve = _signed("resolve_claim_route", bad_scope, wrong_snapshot)
    else:
        deliver = _signed("deliver_wake", bad_scope, wrong_snapshot)
    transport = _UnsupportedTransport()
    receipt = deliver_exact_chat_wake(
        **args, origin_authority=_authority([resolve, deliver]),
        adapter=transport,
    )
    assert receipt.chat_delivery_proven is False
    assert receipt.delivery_status in ("UNKNOWN", "UNSUPPORTED")
    assert transport.calls == []
