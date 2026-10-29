"""EF02-D D2 tests: local trusted-origin, exact-conversation wake adapter, provenance and idempotency."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ai_commerce.remote.completion_event import (
    CompletionEventError,
    build_completion_event,
    validate_completion_event,
)
from ai_commerce.remote.conversation_binding import (
    BindingError,
    ResolvedBinding,
    resolve_binding,
)
from ai_commerce.remote.trusted_origin import (
    LocalCallerAttestation,
    LocalOriginRegistrar,
    OriginError,
)
from ai_commerce.remote.wake_adapter import (
    DurableIdempotencyStore,
    GitHubArtifactProvenance,
    LocalCdpWakeAdapter,
    ProvenanceError,
    WakeReceipt,
    deliver_exact_chat_wake,
    verify_provenance,
)

SHA = "a" * 40
CHAT_A = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001"
CHAT_B = "https://chatgpt.com/g/g-abc123/c/00000000-0000-0000-0000-000000000002"
REPO = "trung-via/ai-commerce"
CDP_PORT = "http://127.0.0.1:9222"


class FakeCdpSession:
    def __init__(
        self,
        *,
        authenticated: bool = True,
        post_success: bool = True,
        witness_result: dict[str, Any] | None = None,
    ):
        self.authenticated = authenticated
        self.post_success = post_success
        self.witness_result = witness_result
        self.messages: list[str] = []
        self.closed = False

    def is_authenticated(self) -> bool:
        return self.authenticated

    def post_chat_message(self, text: str) -> bool:
        if not self.post_success:
            return False
        self.messages.append(text)
        return True

    def witness_exact_receipt(self, event_id: str, timeout: float = 3.0) -> dict[str, Any] | None:
        return self.witness_result

    def close(self) -> None:
        self.closed = True


class FakeCdpClient:
    def __init__(
        self,
        targets: list[dict[str, Any]],
        sessions: dict[str, FakeCdpSession] | None = None,
    ):
        self.targets = targets
        self.sessions = sessions or {}

    def get_targets(self) -> list[dict[str, Any]]:
        return self.targets

    def attach_to_target(self, target_id: str) -> FakeCdpSession:
        return self.sessions.get(target_id) or FakeCdpSession()


def make_result(
    batch_id: str = "batchA",
    *,
    status: str = "CANDIDATE_READY",
    run_id: int = 42,
    issue_number: int = 12,
    wake_event_status: str = "RECORDED_UNBOUND",
    wake_event_id: str | None = None,
) -> dict:
    is_ready = status == "CANDIDATE_READY"
    action = {
        "action_id": "act-1",
        "attempt_id": "att-1",
        "status": status,
        "candidate_sha": "b" * 40 if is_ready else None,
        "diagnostic": "INTERNAL_DIAGNOSTIC_DATA",
    }
    return {
        "kind": "AI_COMMERCE_REMOTE_BATCH_RESULT",
        "version": 1,
        "batch_id": batch_id,
        "source_sha": SHA,
        "status": "COMPLETE" if is_ready else "PARTIAL_OR_FAILED",
        "total": 1,
        "succeeded": 1 if is_ready else 0,
        "actions": [action],
        "github_workflow_run_id": str(run_id),
        "github_issue_number": issue_number,
        "feedback_status": "GITHUB_ISSUE_COMMENT_PUBLISHED",
        "wake_event_status": wake_event_status,
        "wake_event_id": wake_event_id or "placeholder",
    }


def make_event(
    batch_id: str = "batchA",
    run_id: int = 42,
    run_attempt: int = 1,
    issue_number: int = 12,
    status: str = "CANDIDATE_READY",
) -> tuple[dict, dict]:
    res = make_result(batch_id=batch_id, status=status, run_id=run_id, issue_number=issue_number)
    ev = build_completion_event(
        res,
        repository=REPO,
        run_id=run_id,
        run_attempt=run_attempt,
        issue_number=issue_number,
    )
    res["wake_event_id"] = ev["event_id"]
    return ev, res


def make_provenance(
    *,
    repository: str = REPO,
    run_id: int = 42,
    run_attempt: int = 1,
    status: str = "completed",
    attestation_verified: bool = True,
) -> GitHubArtifactProvenance:
    return GitHubArtifactProvenance(
        repository=repository,
        run_id=run_id,
        run_attempt=run_attempt,
        artifact_name="ef02-evidence",
        workflow_run_status=status,
        artifact_digest="sha256:" + "c" * 64,
        attestation_verified=attestation_verified,
    )


def make_attestation(
    *,
    caller_id: str = "local-dev",
    is_local: bool = True,
    verified: bool = True,
    token: str | None = "token-xyz",
) -> LocalCallerAttestation:
    return LocalCallerAttestation(
        caller_id=caller_id,
        is_local=is_local,
        auth_token=token,
        attestation_verified=verified,
    )


def setup_env(tmp_path: Path):
    outside = tmp_path / "outside_local"
    outside.mkdir(parents=True, exist_ok=True)
    registry_path = outside / "chat_routes.json"
    idempotency_path = outside / "idempotency.json"
    repo_root = tmp_path / "repo_checkout"
    repo_root.mkdir(parents=True, exist_ok=True)
    return registry_path, idempotency_path, repo_root


# ---------------------------------------------------------------------------
# 1. Route A/B isolation
# ---------------------------------------------------------------------------

def test_route_ab_isolation(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)

    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.register_route(route_id="routeB", chat_url=CHAT_B, cdp_endpoint=CDP_PORT, attestation=att)

    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)
    reg.bind_claim(route_id="routeB", repository=REPO, batch_id="batchB", source_sha=SHA, issue_number=13, attestation=att)

    ev_a, res_a = make_event("batchA", run_id=42, issue_number=12)
    ev_b, res_b = make_event("batchB", run_id=43, issue_number=13)
    prov_a = make_provenance(run_id=42)
    prov_b = make_provenance(run_id=43)

    sess_a = FakeCdpSession(witness_result={"receipt_verified": True, "exact_chat_turn_id": "turn-101"})
    sess_b = FakeCdpSession(witness_result={"receipt_verified": True, "exact_chat_turn_id": "turn-202"})

    targets = [
        {"id": "tgt-a", "type": "page", "url": CHAT_A},
        {"id": "tgt-b", "type": "page", "url": CHAT_B},
    ]
    client = FakeCdpClient(targets, {"tgt-a": sess_a, "tgt-b": sess_b})
    adapter = LocalCdpWakeAdapter(cdp_client=client)

    # Deliver Event A -> Goes only to Tab A
    rcpt_a = deliver_exact_chat_wake(
        event=ev_a, batch_result=res_a, provenance=prov_a, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt_a.delivery_status == "CONFIRMED"
    assert rcpt_a.chat_delivery_proven is True
    assert rcpt_a.route_id == "routeA"
    assert len(sess_a.messages) == 1
    assert "batchA" in sess_a.messages[0]
    assert len(sess_b.messages) == 0

    # Deliver Event B -> Goes only to Tab B
    rcpt_b = deliver_exact_chat_wake(
        event=ev_b, batch_result=res_b, provenance=prov_b, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt_b.delivery_status == "CONFIRMED"
    assert rcpt_b.chat_delivery_proven is True
    assert rcpt_b.route_id == "routeB"
    assert len(sess_b.messages) == 1
    assert "batchB" in sess_b.messages[0]
    assert len(sess_a.messages) == 1  # Tab A was not touched again


# ---------------------------------------------------------------------------
# 2. Stale, duplicate, and ambiguous claims
# ---------------------------------------------------------------------------

def test_stale_claim_denied(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)

    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, generation=1, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, generation=1, attestation=att)

    # Advance route generation to 2
    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, generation=2, attestation=att)

    ev, res = make_event("batchA", run_id=42, issue_number=12)
    prov = make_provenance(run_id=42)

    rcpt = deliver_exact_chat_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root,
    )
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.error_code == "STALE_CLAIM"
    assert rcpt.chat_delivery_proven is False


def test_bind_stale_or_future_generation_denied(tmp_path):
    reg_p, _, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)

    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, generation=2, attestation=att)

    with pytest.raises(OriginError, match="STALE_GENERATION_CLAIM"):
        reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, generation=1, attestation=att)

    with pytest.raises(OriginError, match="FUTURE_GENERATION_CLAIM"):
        reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, generation=3, attestation=att)


def test_duplicate_claim_for_same_batch_denied(tmp_path):
    reg_p, _, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)

    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.register_route(route_id="routeB", chat_url=CHAT_B, cdp_endpoint=CDP_PORT, attestation=att)

    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    # Same batch to same route -> conflict
    with pytest.raises(OriginError, match="CLAIM_CONFLICT"):
        reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    # Same batch cross-chat to routeB -> conflict
    with pytest.raises(OriginError, match="CLAIM_CONFLICT"):
        reg.bind_claim(route_id="routeB", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)


def test_duplicate_chat_uuid_across_routes_denied(tmp_path):
    reg_p, _, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)

    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    with pytest.raises(OriginError, match="ROUTE_CONFLICT"):
        reg.register_route(route_id="routeB", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)


# ---------------------------------------------------------------------------
# 3. Modified event or digest rejected
# ---------------------------------------------------------------------------

def test_modified_event_source_sha_rejected(tmp_path):
    ev, res = make_event()
    prov = make_provenance()
    tampered_ev = copy.deepcopy(ev)
    tampered_ev["source_sha"] = "b" * 40

    with pytest.raises(ProvenanceError, match="EVENT_INVALID"):
        verify_provenance(tampered_ev, res, prov)


def test_recomputed_hash_adversary_cannot_bypass_provenance(tmp_path):
    ev, res = make_event()
    prov = make_provenance()

    # Adversary recomputes public hash on modified action
    tampered_ev = copy.deepcopy(ev)
    tampered_ev["batch_status"] = "PARTIAL_OR_FAILED"
    tampered_ev["actions"][0] = {
        "action_id": "act-1",
        "attempt_id": "att-1",
        "status": "VERIFIED_SCOPE_REVIEW_REQUIRED",
    }
    recomputed = build_completion_event(
        {
            "kind": "AI_COMMERCE_REMOTE_BATCH_RESULT",
            "version": 1,
            "batch_id": "batchA",
            "source_sha": SHA,
            "status": "PARTIAL_OR_FAILED",
            "total": 1,
            "succeeded": 0,
            "actions": [tampered_ev["actions"][0]],
        },
        repository=REPO,
        run_id=42,
        run_attempt=1,
        issue_number=12,
    )
    # The event is valid in isolation, but mismatches the final batch_result
    with pytest.raises(ProvenanceError, match="BATCH_RESULT_MISMATCH"):
        verify_provenance(recomputed, res, prov)


def test_modified_action_candidate_sha_rejected(tmp_path):
    ev, res = make_event()
    prov = make_provenance()
    res["actions"][0]["candidate_sha"] = "f" * 40
    with pytest.raises(ProvenanceError, match="ACTIONS_MISMATCH"):
        verify_provenance(ev, res, prov)


# ---------------------------------------------------------------------------
# 4. Missing or mismatching run provenance
# ---------------------------------------------------------------------------

def test_missing_or_unverified_provenance_fails_closed(tmp_path):
    ev, res = make_event()
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_provenance(ev, res, None)

    unverified = make_provenance(attestation_verified=False)
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_provenance(ev, res, unverified)


@pytest.mark.parametrize("kw", [
    {"repository": "other/repo"},
    {"run_id": 999},
    {"run_attempt": 2},
    {"status": "in_progress"},
])
def test_mismatched_provenance_fields_fail_closed(kw):
    ev, res = make_event()
    prov = make_provenance(**kw)
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_provenance(ev, res, prov)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(batch_id="otherBatch"),
    lambda r: r.update(source_sha="0" * 40),
    lambda r: r.update(status="PARTIAL_OR_FAILED"),
    lambda r: r.update(wake_event_status="NOT_RECORDED"),
    lambda r: r.update(wake_event_id="ef02-completion-v1:forged"),
    lambda r: r.update(github_workflow_run_id="999"),
    lambda r: r.update(github_issue_number=999),
    lambda r: r.update(total=2),
    lambda r: r.update(succeeded=0),
])
def test_mismatched_batch_result_fails_closed(mutate):
    ev, res = make_event()
    prov = make_provenance()
    mutate(res)
    with pytest.raises(ProvenanceError):
        verify_provenance(ev, res, prov)


# ---------------------------------------------------------------------------
# 5. No default chat & wrong tab fails closed
# ---------------------------------------------------------------------------

def test_no_default_chat_when_tab_missing(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)
    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, res = make_event()
    prov = make_provenance()

    # Browser only has unrelated tabs open
    sess = FakeCdpSession()
    targets = [
        {"id": "tab-1", "type": "page", "url": "https://chatgpt.com/"},
        {"id": "tab-2", "type": "page", "url": "https://google.com/"},
        {"id": "tab-3", "type": "page", "url": "https://chatgpt.com/c/00000000-0000-0000-0000-999999999999"},
    ]
    client = FakeCdpClient(targets, {"tab-1": sess, "tab-2": sess, "tab-3": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)

    rcpt = deliver_exact_chat_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.error_code == "EXACT_CHAT_TAB_NOT_FOUND"
    assert rcpt.chat_delivery_proven is False
    assert len(sess.messages) == 0


def test_ambiguous_multiple_tabs_with_same_chat_fails_closed(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)
    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, res = make_event()
    prov = make_provenance()

    sess = FakeCdpSession()
    targets = [
        {"id": "tab-1", "type": "page", "url": CHAT_A},
        {"id": "tab-2", "type": "page", "url": CHAT_A},
    ]
    client = FakeCdpClient(targets, {"tab-1": sess, "tab-2": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)

    rcpt = deliver_exact_chat_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.error_code == "AMBIGUOUS_CHAT_TARGETS"
    assert len(sess.messages) == 0


# ---------------------------------------------------------------------------
# 6. Consent and caller attestation enforced
# ---------------------------------------------------------------------------

def test_consent_and_caller_attestation_enforced(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    reg = LocalOriginRegistrar(reg_p, root)

    # Consent False
    att = make_attestation()
    with pytest.raises(OriginError, match="CONSENT_REQUIRED"):
        reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, consent=False, attestation=att)

    # Unverified caller attestation
    unverified = make_attestation(verified=False)
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=unverified)

    # Non-local caller attestation
    non_local = make_attestation(is_local=False)
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=non_local)

    # Invalid chat URL
    with pytest.raises(OriginError, match="CHAT_URL_INVALID"):
        reg.register_route(route_id="routeA", chat_url="https://chatgpt.com/c/not-uuid", cdp_endpoint=CDP_PORT, attestation=att)

    # Non-loopback CDP endpoint
    with pytest.raises(OriginError, match="CDP_ENDPOINT_INVALID"):
        reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint="http://example.com:9222", attestation=att)


# ---------------------------------------------------------------------------
# 7. Unsupported transport & uncertain receipt
# ---------------------------------------------------------------------------

def test_cdp_endpoint_unavailable_fails_closed(tmp_path):
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint="http://127.0.0.1:54321")
    adapter = LocalCdpWakeAdapter()  # No injected client, real probe to dead port
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.error_code == "CDP_UNAVAILABLE"


def test_unauthenticated_session_fails_closed(tmp_path):
    sess = FakeCdpSession(authenticated=False)
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.error_code == "SESSION_UNAUTHENTICATED_OR_CAPTCHA"


def test_platform_write_failure_fails_closed(tmp_path):
    sess = FakeCdpSession(post_success=False)
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.error_code == "PLATFORM_WRITE_UNSUPPORTED"


def test_receipt_not_witnessed_returns_unknown(tmp_path):
    sess = FakeCdpSession(witness_result=None)  # Sent, but DOM witness timed out
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNKNOWN"
    assert rcpt.chat_delivery_proven is False
    assert rcpt.error_code == "RECEIPT_NOT_WITNESSED"
    assert len(sess.messages) == 1


def test_click_success_alone_not_receipt_proof():
    # Only click_success=True without verified DOM witness
    sess = FakeCdpSession(witness_result={"click_success": True})
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNKNOWN"
    assert rcpt.chat_delivery_proven is False


def test_native_brain_continuation_always_not_proven(tmp_path):
    sess = FakeCdpSession(witness_result={"receipt_verified": True, "exact_chat_turn_id": "turn-999"})
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "CONFIRMED"
    assert rcpt.chat_delivery_proven is True
    assert rcpt.brain_continuation_proven is False


# ---------------------------------------------------------------------------
# 8. Replay and crash-recovery
# ---------------------------------------------------------------------------

def test_confirmed_delivery_replayed_without_resend(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)
    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, res = make_event()
    prov = make_provenance()

    sess = FakeCdpSession(witness_result={"receipt_verified": True, "exact_chat_turn_id": "turn-1"})
    client = FakeCdpClient([{"id": "tab-1", "type": "page", "url": CHAT_A}], {"tab-1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)

    # Initial delivery
    rcpt1 = deliver_exact_chat_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt1.delivery_status == "CONFIRMED"
    assert rcpt1.replayed is False
    assert len(sess.messages) == 1

    # Second delivery (replay)
    rcpt2 = deliver_exact_chat_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt2.delivery_status == "CONFIRMED"
    assert rcpt2.replayed is True
    assert len(sess.messages) == 1  # No duplicate send


def test_crash_recovery_holds_uncertain_send(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)
    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, res = make_event()
    prov = make_provenance()

    # Pre-record an UNKNOWN write-ahead state as if a crash occurred mid-send
    store = DurableIdempotencyStore(idemp_p, root)
    store.record_intent(ev["event_id"], "routeA", 1)

    sess = FakeCdpSession()
    client = FakeCdpClient([{"id": "tab-1", "type": "page", "url": CHAT_A}], {"tab-1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)

    rcpt = deliver_exact_chat_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt.delivery_status == "UNKNOWN"
    assert rcpt.error_code == "DELIVERY_UNCERTAIN_HELD"
    assert rcpt.chat_delivery_proven is False
    assert len(sess.messages) == 0  # No blind retry or duplicate message sent


# ---------------------------------------------------------------------------
# 9. No secrets leaked
# ---------------------------------------------------------------------------

def test_no_secrets_in_repr_or_logs():
    att = LocalCallerAttestation(caller_id="dev", is_local=True, auth_token="SECRET_TOKEN_999", attestation_verified=True)
    assert "SECRET_TOKEN_999" not in repr(att)

    prov = GitHubArtifactProvenance(repository=REPO, run_id=1, run_attempt=1, artifact_name="test", workflow_run_status="success", artifact_digest="PRIVATE_DIGEST", attestation_verified=True)
    assert "PRIVATE_DIGEST" not in repr(prov)

    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    assert "chatgpt.com" not in repr(binding)
    assert "127.0.0.1" not in repr(binding)

    rcpt = WakeReceipt(event_id="e1", route_id="r1", generation=1, delivery_status="CONFIRMED", chat_delivery_proven=True)
    assert "chatgpt" not in repr(rcpt)


def test_registry_and_idempotency_store_inside_repo_rejected(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    inside_reg = root / "registry.json"
    with pytest.raises(OriginError, match="REGISTRY_LOCATION_INVALID"):
        LocalOriginRegistrar(inside_reg, root)

    inside_idemp = root / "idempotency.json"
    with pytest.raises(OriginError, match="IDEMPOTENCY_STORE_LOCATION_INVALID"):
        DurableIdempotencyStore(inside_idemp, root)


# ---------------------------------------------------------------------------
# 10. D1 regression and interoperability
# ---------------------------------------------------------------------------

def test_d1_resolve_binding_reads_d2_registry(tmp_path):
    reg_p, _, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)

    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, _ = make_event()
    assert validate_completion_event(ev) == ev

    # D1 resolve_binding directly reading the D2-created registry
    binding = resolve_binding(ev, route_id="routeA", registry_path=reg_p, repository_root=root)
    assert binding.route_id == "routeA"
    assert binding.chat_url == CHAT_A
    assert binding.generation == 1


# ---------------------------------------------------------------------------
# 11. Valid final failed EF01 statuses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [
    "VERIFIED_SCOPE_REVIEW_REQUIRED",
    "NO_CODE_CHANGE",
    "SOURCE_DRIFT",
    "EXECUTOR_UNAVAILABLE",
    "EXECUTOR_PERMISSION_DENIED",
    "EXECUTION_UNKNOWN",
])
def test_terminal_failed_ef01_statuses_produce_valid_wakes(tmp_path, status):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = LocalOriginRegistrar(reg_p, root)
    reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, res = make_event(status=status)
    assert ev["batch_status"] == "PARTIAL_OR_FAILED"
    assert res["status"] == "PARTIAL_OR_FAILED"
    prov = make_provenance()

    sess = FakeCdpSession(witness_result={"receipt_verified": True, "exact_chat_turn_id": "turn-fail-1"})
    client = FakeCdpClient([{"id": "tab-1", "type": "page", "url": CHAT_A}], {"tab-1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)

    rcpt = deliver_exact_chat_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt.delivery_status == "CONFIRMED"
    assert rcpt.chat_delivery_proven is True
    assert len(sess.messages) == 1
    assert "PARTIAL_OR_FAILED" in sess.messages[0]
    assert "INTERNAL_DIAGNOSTIC_DATA" not in sess.messages[0]
