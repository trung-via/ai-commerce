"""EF02-D D2 tests: local trusted-origin, exact-conversation wake adapter, provenance and idempotency."""
from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor, TimeoutError
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


class FakeOriginAuthority:
    """Test-only enrollment, independent of flags in an incoming request."""
    def __init__(self):
        self.enrolled = []

    def authorize_origin(self, attestation, *, operation, context):
        return (any(attestation is enrolled for enrolled in self.enrolled) and
                operation in {"register_route", "bind_claim", "resolve_claim_route", "deliver_wake"})


class FakeArtifactAuthority:
    """Test-only authenticated artifact snapshots, provisioned by fixture setup."""
    def __init__(self):
        self.records = {}

    def verify_artifact(self, provenance, *, event, batch_result):
        identity = (provenance.repository, provenance.run_id, provenance.run_attempt,
                    provenance.artifact_name, provenance.artifact_digest)
        return self.records.get(identity, {}).get(event["event_id"]) == (event, batch_result)


ORIGIN_AUTHORITY = FakeOriginAuthority()
ARTIFACT_AUTHORITY = FakeArtifactAuthority()


def trusted_registrar(path, root):
    return LocalOriginRegistrar(path, root, authority=ORIGIN_AUTHORITY)


def verify_fixture_provenance(event, result, provenance):
    return verify_provenance(event, result, provenance, authority=ARTIFACT_AUTHORITY)


def deliver_fixture_wake(**kwargs):
    return deliver_exact_chat_wake(**kwargs, origin_authority=ORIGIN_AUTHORITY,
                                   artifact_authority=ARTIFACT_AUTHORITY)


class UnknownFakeAdapter:
    """Synthetic transport only. Never opens a browser or proves receipt."""
    def __init__(self):
        self.messages = []
        self.lock = threading.Lock()

    def send_wake(self, *, binding, message, event_id):
        with self.lock:
            self.messages.append(message)
        return WakeReceipt(event_id=event_id, route_id=binding.route_id,
                           generation=binding.generation, delivery_status="UNKNOWN")


def test_caller_flags_cannot_mint_origin_authority(tmp_path):
    registry, _, root = setup_env(tmp_path)
    attestation = make_attestation()
    assert attestation.is_trusted() is False
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        LocalOriginRegistrar(registry, root).register_route(
            route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT,
            consent=True, attestation=attestation,
        )
    assert not registry.exists()


def test_matching_jsons_and_provenance_flag_are_not_authentication():
    event, result = make_event()
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_provenance(event, result, make_provenance())


def test_intent_cannot_be_claimed_twice(tmp_path):
    _, path, root = setup_env(tmp_path)
    event, _ = make_event()
    store = DurableIdempotencyStore(path, root)
    assert store.record_intent(event["event_id"], "routeA", 1) is True
    assert store.record_intent(event["event_id"], "routeA", 1) is False


def test_forged_receipt_never_confirms_or_persists_private_witness(tmp_path):
    _, path, root = setup_env(tmp_path)
    event, _ = make_event()
    store = DurableIdempotencyStore(path, root)
    store.record_intent(event["event_id"], "routeA", 1)
    store.record_outcome(
        event["event_id"], "routeA", 1, delivery_status="CONFIRMED",
        chat_delivery_proven=True,
        witness_evidence={"receipt_verified": True, "exact_chat_turn_id": "fake", "cookie": "SECRET_COOKIE"},
    )
    record = store.get_record(event["event_id"], "routeA", 1)
    assert record["delivery_status"] == "UNKNOWN"
    assert record["chat_delivery_proven"] is False
    assert "SECRET_COOKIE" not in path.read_text(encoding="utf-8")


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
        self.attached = []

    def get_targets(self) -> list[dict[str, Any]]:
        return self.targets

    def attach_to_target(self, target_id: str) -> FakeCdpSession:
        self.attached.append(target_id)
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
    identity = (REPO, run_id, run_attempt, "ef02-evidence", "sha256:" + "c" * 64)
    ARTIFACT_AUTHORITY.records.setdefault(identity, {})[ev["event_id"]] = (copy.deepcopy(ev), copy.deepcopy(res))
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
    attestation = LocalCallerAttestation(
        caller_id=caller_id,
        is_local=is_local,
        auth_token=token,
        attestation_verified=verified,
    )
    ORIGIN_AUTHORITY.enrolled.append(attestation)
    return attestation


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
    reg = trusted_registrar(reg_p, root)

    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.register_route(consent=True, route_id="routeB", chat_url=CHAT_B, cdp_endpoint=CDP_PORT, attestation=att)

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
    rcpt_a = deliver_fixture_wake(
        event=ev_a, batch_result=res_a, provenance=prov_a, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt_a.delivery_status == "UNSUPPORTED"
    assert rcpt_a.chat_delivery_proven is False
    assert rcpt_a.route_id == "routeA"
    assert len(sess_a.messages) == 0
    assert client.attached == ["tgt-a"]
    assert len(sess_b.messages) == 0

    # Deliver Event B -> Goes only to Tab B
    rcpt_b = deliver_fixture_wake(
        event=ev_b, batch_result=res_b, provenance=prov_b, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt_b.delivery_status == "UNSUPPORTED"
    assert rcpt_b.chat_delivery_proven is False
    assert rcpt_b.route_id == "routeB"
    assert len(sess_b.messages) == 0
    assert client.attached == ["tgt-a", "tgt-b"]
    assert len(sess_a.messages) == 0  # Tab A was not touched again


# ---------------------------------------------------------------------------
# 2. Stale, duplicate, and ambiguous claims
# ---------------------------------------------------------------------------

def test_stale_claim_denied(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = trusted_registrar(reg_p, root)

    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, generation=1, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, generation=1, attestation=att)

    # Advance route generation to 2
    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, generation=2, attestation=att)

    ev, res = make_event("batchA", run_id=42, issue_number=12)
    prov = make_provenance(run_id=42)

    rcpt = deliver_fixture_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root,
    )
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.error_code == "STALE_CLAIM"
    assert rcpt.chat_delivery_proven is False


def test_bind_stale_or_future_generation_denied(tmp_path):
    reg_p, _, root = setup_env(tmp_path)
    att = make_attestation()
    reg = trusted_registrar(reg_p, root)

    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, generation=2, attestation=att)

    with pytest.raises(OriginError, match="STALE_GENERATION_CLAIM"):
        reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, generation=1, attestation=att)

    with pytest.raises(OriginError, match="FUTURE_GENERATION_CLAIM"):
        reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, generation=3, attestation=att)


def test_duplicate_claim_for_same_batch_denied(tmp_path):
    reg_p, _, root = setup_env(tmp_path)
    att = make_attestation()
    reg = trusted_registrar(reg_p, root)

    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.register_route(consent=True, route_id="routeB", chat_url=CHAT_B, cdp_endpoint=CDP_PORT, attestation=att)

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
    reg = trusted_registrar(reg_p, root)

    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    with pytest.raises(OriginError, match="ROUTE_CONFLICT"):
        reg.register_route(consent=True, route_id="routeB", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)


# ---------------------------------------------------------------------------
# 3. Modified event or digest rejected
# ---------------------------------------------------------------------------

def test_modified_event_source_sha_rejected(tmp_path):
    ev, res = make_event()
    prov = make_provenance()
    tampered_ev = copy.deepcopy(ev)
    tampered_ev["source_sha"] = "b" * 40

    with pytest.raises(ProvenanceError, match="EVENT_INVALID"):
        verify_fixture_provenance(tampered_ev, res, prov)


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
        verify_fixture_provenance(recomputed, res, prov)


def test_modified_action_candidate_sha_rejected(tmp_path):
    ev, res = make_event()
    prov = make_provenance()
    res["actions"][0]["candidate_sha"] = "f" * 40
    with pytest.raises(ProvenanceError, match="ACTIONS_MISMATCH"):
        verify_fixture_provenance(ev, res, prov)


# ---------------------------------------------------------------------------
# 4. Missing or mismatching run provenance
# ---------------------------------------------------------------------------

def test_missing_or_unverified_provenance_fails_closed(tmp_path):
    ev, res = make_event()
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_fixture_provenance(ev, res, None)

    unverified = make_provenance(attestation_verified=False)
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_fixture_provenance(ev, res, unverified)


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
        verify_fixture_provenance(ev, res, prov)


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
        verify_fixture_provenance(ev, res, prov)


# ---------------------------------------------------------------------------
# 5. No default chat & wrong tab fails closed
# ---------------------------------------------------------------------------

def test_no_default_chat_when_tab_missing(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = trusted_registrar(reg_p, root)
    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
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

    rcpt = deliver_fixture_wake(
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
    reg = trusted_registrar(reg_p, root)
    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
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

    rcpt = deliver_fixture_wake(
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
    reg = trusted_registrar(reg_p, root)

    # Consent False
    att = make_attestation()
    with pytest.raises(OriginError, match="CONSENT_REQUIRED"):
        reg.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, consent=False, attestation=att)

    # Unverified caller attestation
    unverified = make_attestation(verified=False)
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=unverified)

    # Non-local caller attestation
    non_local = make_attestation(is_local=False)
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=non_local)

    # Invalid chat URL
    with pytest.raises(OriginError, match="CHAT_URL_INVALID"):
        reg.register_route(consent=True, route_id="routeA", chat_url="https://chatgpt.com/c/not-uuid", cdp_endpoint=CDP_PORT, attestation=att)

    # Non-loopback CDP endpoint
    with pytest.raises(OriginError, match="CDP_ENDPOINT_INVALID"):
        reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint="http://example.com:9222", attestation=att)


# ---------------------------------------------------------------------------
# 7. Unsupported transport & uncertain receipt
# ---------------------------------------------------------------------------

def test_default_adapter_does_not_probe_browser(tmp_path, monkeypatch):
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint="http://127.0.0.1:54321")
    adapter = LocalCdpWakeAdapter()
    monkeypatch.setattr(adapter, "_query_live_targets", lambda endpoint: pytest.fail("unexpected browser probe"))
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.error_code == "CDP_PROBE_DISABLED"


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


def test_probe_does_not_send_without_receipt_capability(tmp_path):
    sess = FakeCdpSession(witness_result=None)  # Sent, but DOM witness timed out
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.chat_delivery_proven is False
    assert rcpt.error_code == "PLATFORM_WRITE_UNSUPPORTED"
    assert len(sess.messages) == 0


def test_click_success_alone_not_receipt_proof():
    # Only click_success=True without verified DOM witness
    sess = FakeCdpSession(witness_result={"click_success": True})
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.chat_delivery_proven is False
    assert sess.messages == []


def test_native_brain_continuation_always_not_proven(tmp_path):
    sess = FakeCdpSession(witness_result={"receipt_verified": True, "exact_chat_turn_id": "turn-999"})
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)
    binding = ResolvedBinding(route_id="r1", generation=1, chat_url=CHAT_A, cdp_endpoint=CDP_PORT)
    rcpt = adapter.send_wake(binding=binding, message="test", event_id="ef02-completion-v1:abc")
    assert rcpt.delivery_status == "UNSUPPORTED"
    assert rcpt.chat_delivery_proven is False
    assert rcpt.brain_continuation_proven is False


# ---------------------------------------------------------------------------
# 8. Replay and crash-recovery
# ---------------------------------------------------------------------------

def test_unsupported_probe_is_not_retried(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = trusted_registrar(reg_p, root)
    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, res = make_event()
    prov = make_provenance()

    sess = FakeCdpSession(witness_result={"receipt_verified": True, "exact_chat_turn_id": "turn-1"})
    client = FakeCdpClient([{"id": "tab-1", "type": "page", "url": CHAT_A}], {"tab-1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)

    # Initial delivery
    rcpt1 = deliver_fixture_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt1.delivery_status == "UNSUPPORTED"
    assert rcpt1.replayed is False
    assert len(sess.messages) == 0

    # Second delivery (replay)
    rcpt2 = deliver_fixture_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt2.delivery_status == "UNSUPPORTED"
    assert rcpt2.replayed is True
    assert len(sess.messages) == 0
    assert client.attached == ["tab-1"]  # Cached UNSUPPORTED; no repeated probe


def test_crash_recovery_holds_uncertain_send(tmp_path):
    reg_p, idemp_p, root = setup_env(tmp_path)
    att = make_attestation()
    reg = trusted_registrar(reg_p, root)
    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, res = make_event()
    prov = make_provenance()

    # Pre-record an UNKNOWN write-ahead state as if a crash occurred mid-send
    store = DurableIdempotencyStore(idemp_p, root)
    store.record_intent(ev["event_id"], "routeA", 1)

    sess = FakeCdpSession()
    client = FakeCdpClient([{"id": "tab-1", "type": "page", "url": CHAT_A}], {"tab-1": sess})
    adapter = LocalCdpWakeAdapter(cdp_client=client)

    rcpt = deliver_fixture_wake(
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
    reg = trusted_registrar(reg_p, root)

    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
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
    reg = trusted_registrar(reg_p, root)
    reg.register_route(consent=True, route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT, attestation=att)
    reg.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA", source_sha=SHA, issue_number=12, attestation=att)

    ev, res = make_event(status=status)
    assert ev["batch_status"] == "PARTIAL_OR_FAILED"
    assert res["status"] == "PARTIAL_OR_FAILED"
    prov = make_provenance()

    sess = FakeCdpSession(witness_result={"receipt_verified": True, "exact_chat_turn_id": "turn-fail-1"})
    client = FakeCdpClient([{"id": "tab-1", "type": "page", "url": CHAT_A}], {"tab-1": sess})
    adapter = UnknownFakeAdapter()

    rcpt = deliver_fixture_wake(
        event=ev, batch_result=res, provenance=prov, origin_attestation=att,
        registry_path=reg_p, idempotency_path=idemp_p, repository_root=root, adapter=adapter,
    )
    assert rcpt.delivery_status == "UNKNOWN"
    assert rcpt.chat_delivery_proven is False
    assert len(adapter.messages) == 1
    assert "PARTIAL_OR_FAILED" in adapter.messages[0]
    assert "INTERNAL_DIAGNOSTIC_DATA" not in adapter.messages[0]


def prepared_delivery(tmp_path):
    registry, store, root = setup_env(tmp_path)
    attestation = make_attestation()
    registrar = trusted_registrar(registry, root)
    registrar.register_route(route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT,
                             consent=True, attestation=attestation)
    registrar.bind_claim(route_id="routeA", repository=REPO, batch_id="batchA",
                         source_sha=SHA, issue_number=12, attestation=attestation)
    event, result = make_event()
    return dict(event=event, batch_result=result, provenance=make_provenance(),
                origin_attestation=attestation, registry_path=registry,
                idempotency_path=store, repository_root=root)


def test_consent_is_explicit_even_with_enrolled_local_authority(tmp_path):
    path, _, root = setup_env(tmp_path)
    with pytest.raises(OriginError, match="CONSENT_REQUIRED"):
        trusted_registrar(path, root).register_route(
            route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT,
            attestation=make_attestation())
    assert not path.exists()


def test_token_equality_and_forged_subclass_do_not_grant_authority():
    attestation = make_attestation()
    assert attestation.is_trusted(expected_token=attestation.auth_token) is False
    forged = LocalCallerAttestation("unknown", True, "token-xyz", True)
    assert forged.is_trusted(authority=ORIGIN_AUTHORITY, operation="deliver_wake") is False


@pytest.mark.parametrize("hint", [1, "true", {}, None])
def test_nonboolean_attestation_hints_are_rejected(tmp_path, hint):
    path, _, root = setup_env(tmp_path)
    attestation = make_attestation(verified=hint)
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        trusted_registrar(path, root).register_route(
            route_id="routeA", chat_url=CHAT_A, cdp_endpoint=CDP_PORT,
            consent=True, attestation=attestation)


def test_matching_forged_event_and_result_rejected_by_pinned_authority():
    event, result = make_event()
    result["source_sha"] = "d" * 40
    result["actions"][0]["candidate_sha"] = "e" * 40
    forged = build_completion_event(result, repository=REPO, run_id=42,
                                     run_attempt=1, issue_number=12)
    result["wake_event_id"] = forged["event_id"]
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_fixture_provenance(forged, result, make_provenance())


@pytest.mark.parametrize("field,value", [
    ("github_workflow_run_id", None), ("github_workflow_run_id", 42.0),
    ("github_workflow_run_id", True), ("github_workflow_run_id", "042"),
    ("github_workflow_run_attempt", 2), ("github_workflow_run_attempt", True),
    ("github_issue_number", 12.0), ("total", True), ("succeeded", True), ("version", True),
])
def test_batch_identity_uses_strict_types_and_exact_run_attempt(field, value):
    event, result = make_event()
    result[field] = value
    with pytest.raises(ProvenanceError):
        verify_fixture_provenance(event, result, make_provenance())


def test_authorities_fail_closed_without_leaking_exceptions(tmp_path):
    kwargs = prepared_delivery(tmp_path)

    class BrokenAuthority:
        def authorize_origin(self, *args, **kw):
            raise RuntimeError(CHAT_A + " SECRET_TOKEN")

        def verify_artifact(self, *args, **kw):
            raise RuntimeError(CHAT_A + " SECRET_TOKEN")

    receipt = deliver_exact_chat_wake(**kwargs, origin_authority=BrokenAuthority(),
                                      artifact_authority=ARTIFACT_AUTHORITY)
    assert receipt.error_code == "UNVERIFIED_ORIGIN"
    receipt = deliver_exact_chat_wake(**kwargs, origin_authority=ORIGIN_AUTHORITY,
                                      artifact_authority=BrokenAuthority())
    assert receipt.error_code == "PROVENANCE_UNVERIFIED"
    assert "SECRET_TOKEN" not in repr(receipt)
    assert not kwargs["idempotency_path"].exists()


def test_delivery_requires_each_separate_authority(tmp_path):
    kwargs = prepared_delivery(tmp_path)
    receipt = deliver_exact_chat_wake(**kwargs, artifact_authority=ARTIFACT_AUTHORITY)
    assert receipt.error_code == "UNVERIFIED_ORIGIN"
    receipt = deliver_exact_chat_wake(**kwargs, origin_authority=ORIGIN_AUTHORITY)
    assert receipt.error_code == "PROVENANCE_UNVERIFIED"
    assert not kwargs["idempotency_path"].exists()


def test_registry_mutation_cannot_bypass_origin_authority(tmp_path):
    path, _, root = setup_env(tmp_path)
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        LocalOriginRegistrar(path, root).write_registry({"version": 1, "routes": [], "claims": []})
    assert not path.exists()


def _claim_process(path, root, event_id, ready, gate, results, crash=False):
    ready.put(True)
    gate.wait(15)
    result = DurableIdempotencyStore(Path(path), Path(root)).record_intent(event_id, "routeA", 1)
    if crash:
        os._exit(19)
    results.put(result)


def test_concurrent_processes_claim_one_durable_intent(tmp_path):
    _, path, root = setup_env(tmp_path)
    event, _ = make_event()
    context = multiprocessing.get_context("spawn")
    ready, gate, results = context.Queue(), context.Event(), context.Queue()
    workers = [context.Process(target=_claim_process,
               args=(str(path), str(root), event["event_id"], ready, gate, results)) for _ in range(4)]
    try:
        for worker in workers:
            worker.start()
        for _ in workers:
            assert ready.get(timeout=20) is True
        gate.set()
        claimed = [results.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(timeout=20)
            assert worker.exitcode == 0
        assert claimed.count(True) == 1
        assert claimed.count(False) == 3
        assert len(DurableIdempotencyStore(path, root).read_store()["records"]) == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)


def test_actual_process_crash_preserves_unknown_and_blocks_reclaim(tmp_path):
    _, path, root = setup_env(tmp_path)
    event, _ = make_event()
    context = multiprocessing.get_context("spawn")
    ready, gate, results = context.Queue(), context.Event(), context.Queue()
    worker = context.Process(target=_claim_process, args=(str(path), str(root), event["event_id"],
                                                        ready, gate, results, True))
    try:
        worker.start()
        assert ready.get(timeout=20) is True
        gate.set()
        worker.join(timeout=20)
        assert worker.exitcode == 19
        store = DurableIdempotencyStore(path, root)
        assert store.get_record(event["event_id"], "routeA", 1)["delivery_status"] == "UNKNOWN"
        assert store.record_intent(event["event_id"], "routeA", 1) is False
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)


def test_concurrent_delivery_and_unknown_replay_invoke_adapter_once(tmp_path):
    kwargs = prepared_delivery(tmp_path)
    adapter = UnknownFakeAdapter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda _: deliver_fixture_wake(**kwargs, adapter=adapter), range(6)))
    assert len(adapter.messages) == 1
    assert all(r.delivery_status == "UNKNOWN" and not r.chat_delivery_proven for r in receipts)
    assert sum(r.replayed for r in receipts) == 5
    assert deliver_fixture_wake(**kwargs, adapter=adapter).error_code == "DELIVERY_UNCERTAIN_HELD"
    assert len(adapter.messages) == 1


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(event_id="ef02-completion-v1:" + "0" * 64),
    lambda r: r.update(route_id="otherChat"), lambda r: r.update(generation=2),
    lambda r: r.update(generation=True), lambda r: r.update(delivery_status="CONFIRMED", chat_delivery_proven=True),
    lambda r: r.update(brain_continuation_proven=True), lambda r: r.update(delivery_status="NEW_UNKNOWN_STATE"),
])
def test_caller_receipt_cannot_confirm_wrong_identity_or_upgrade_unknown(tmp_path, mutate):
    kwargs = prepared_delivery(tmp_path)

    class ForgedAdapter(UnknownFakeAdapter):
        def send_wake(self, **kw):
            super().send_wake(**kw)
            fields = dict(event_id=kw["event_id"], route_id=kw["binding"].route_id,
                          generation=kw["binding"].generation, delivery_status="UNKNOWN",
                          witness_evidence={"receipt_verified": True, "cookie": "PRIVATE_COOKIE"})
            mutate(fields)
            return WakeReceipt(**fields)

    adapter = ForgedAdapter()
    receipt = deliver_fixture_wake(**kwargs, adapter=adapter)
    assert receipt.delivery_status == "UNKNOWN"
    assert receipt.chat_delivery_proven is receipt.brain_continuation_proven is False
    assert receipt.witness_evidence is None
    assert deliver_fixture_wake(**kwargs, adapter=adapter).error_code == "DELIVERY_UNCERTAIN_HELD"
    assert len(adapter.messages) == 1
    assert "PRIVATE_COOKIE" not in kwargs["idempotency_path"].read_text(encoding="utf-8")


def test_transport_exception_is_redacted_and_unknown_is_held(tmp_path):
    kwargs = prepared_delivery(tmp_path)

    class BrokenAdapter:
        def send_wake(self, **kw):
            raise RuntimeError(CHAT_A + " PRIVATE_COOKIE")

    receipt = deliver_fixture_wake(**kwargs, adapter=BrokenAdapter())
    assert receipt.delivery_status == "UNKNOWN"
    assert receipt.error_code == "ADAPTER_FAILED"
    assert "PRIVATE_COOKIE" not in kwargs["idempotency_path"].read_text(encoding="utf-8")
    assert deliver_fixture_wake(**kwargs, adapter=BrokenAdapter()).error_code == "DELIVERY_UNCERTAIN_HELD"


@pytest.mark.parametrize("malformed", [
    '{"version":1,"version":1,"routes":[],"claims":[]}',
    '{"version":true,"routes":[],"claims":[]}',
    '{"version":1,"routes":{},"claims":[]}', "{}", "not json",
])
def test_malformed_or_duplicate_registry_denies_before_adapter(tmp_path, malformed):
    kwargs = prepared_delivery(tmp_path)
    kwargs["registry_path"].write_text(malformed, encoding="utf-8")
    adapter = UnknownFakeAdapter()
    receipt = deliver_fixture_wake(**kwargs, adapter=adapter)
    assert receipt.delivery_status == "UNSUPPORTED"
    assert adapter.messages == []


@pytest.mark.parametrize("change", [
    lambda d: d.update(version=True), lambda d: d.update(unexpected=True),
    lambda d: next(iter(d["records"].values())).update(delivery_status="READY_TO_RETRY"),
    lambda d: next(iter(d["records"].values())).update(generation=True),
    lambda d: next(iter(d["records"].values())).update(timestamp=float("nan")),
])
def test_invalid_idempotency_state_never_authorizes_retry(tmp_path, change):
    kwargs = prepared_delivery(tmp_path)
    store = DurableIdempotencyStore(kwargs["idempotency_path"], kwargs["repository_root"])
    store.record_intent(kwargs["event"]["event_id"], "routeA", 1)
    data = store.read_store()
    change(data)
    kwargs["idempotency_path"].write_text(json.dumps(data), encoding="utf-8")
    adapter = UnknownFakeAdapter()
    receipt = deliver_fixture_wake(**kwargs, adapter=adapter)
    assert receipt.delivery_status == "UNKNOWN"
    assert adapter.messages == []


def test_legacy_forged_confirmed_record_is_held_without_private_witness(tmp_path):
    kwargs = prepared_delivery(tmp_path)
    store = DurableIdempotencyStore(kwargs["idempotency_path"], kwargs["repository_root"])
    store.record_intent(kwargs["event"]["event_id"], "routeA", 1)
    data = store.read_store()
    record = next(iter(data["records"].values()))
    record.update(delivery_status="CONFIRMED", chat_delivery_proven=True,
                  witness_evidence={"receipt_verified": True, "cookie": "PRIVATE_COOKIE"})
    kwargs["idempotency_path"].write_text(json.dumps(data), encoding="utf-8")
    adapter = UnknownFakeAdapter()
    receipt = deliver_fixture_wake(**kwargs, adapter=adapter)
    assert receipt.delivery_status == "UNKNOWN"
    assert receipt.error_code == "DELIVERY_UNCERTAIN_HELD"
    assert receipt.witness_evidence is None
    assert receipt.chat_delivery_proven is False
    assert adapter.messages == []


@pytest.mark.parametrize("args", [
    ("bad", "routeA", 1), ("ef02-completion-v1:" + "0" * 64, "r::x", 1),
    ("ef02-completion-v1:" + "0" * 64, "routeA", True),
])
def test_malformed_idempotency_key_rejected(tmp_path, args):
    _, path, root = setup_env(tmp_path)
    with pytest.raises(OriginError, match="IDEMPOTENCY_KEY_INVALID"):
        DurableIdempotencyStore(path, root).record_intent(*args)


def test_public_store_replacement_cannot_erase_an_unknown_claim(tmp_path):
    _, path, root = setup_env(tmp_path)
    store = DurableIdempotencyStore(path, root)
    event, _ = make_event()
    store.record_intent(event["event_id"], "routeA", 1)
    with pytest.raises(OriginError, match="REPLACEMENT_FORBIDDEN"):
        store.write_store({"version": 1, "records": {}})
    assert store.record_intent(event["event_id"], "routeA", 1) is False


@pytest.mark.parametrize("state_name", ["chat_routes.json", "idempotency.json", "idempotency.json.lock"])
def test_hardlinked_state_or_lock_is_rejected_without_touching_victim(tmp_path, state_name):
    registry, path, root = setup_env(tmp_path)
    victim = tmp_path / "victim.json"
    victim.write_text("PRIVATE_VICTIM", encoding="utf-8")
    native_hardlink(victim, path.parent / state_name)
    event, _ = make_event()
    with pytest.raises(OriginError):
        if state_name == "chat_routes.json":
            trusted_registrar(registry, root).read_registry()
        else:
            DurableIdempotencyStore(path, root).record_intent(event["event_id"], "routeA", 1)
    assert victim.read_text(encoding="utf-8") == "PRIVATE_VICTIM"


def test_old_fixed_part_path_cannot_redirect_new_atomic_write(tmp_path):
    _, path, root = setup_env(tmp_path)
    victim = tmp_path / "victim.json"
    victim.write_text("PRIVATE_VICTIM", encoding="utf-8")
    native_hardlink(victim, path.with_suffix(".json.part"))
    event, _ = make_event()
    assert DurableIdempotencyStore(path, root).record_intent(event["event_id"], "routeA", 1) is True
    assert victim.read_text(encoding="utf-8") == "PRIVATE_VICTIM"


@pytest.mark.parametrize("link_parent", [False, True])
def test_symlinked_state_or_parent_fails_closed(tmp_path, link_parent):
    _, path, root = setup_env(tmp_path)
    victim = tmp_path / "victim"
    if link_parent:
        victim.mkdir()
        link = tmp_path / "linked_parent"
        target = link / "idempotency.json"
    else:
        victim.write_text("PRIVATE_VICTIM", encoding="utf-8")
        link = target = path
    try:
        os.symlink(victim, link, target_is_directory=link_parent)
    except OSError as exc:
        pytest.skip(f"Native symlink unavailable: {exc.winerror if os.name == 'nt' else exc.errno}")
    with pytest.raises(OriginError, match="LOCATION_INVALID"):
        DurableIdempotencyStore(target, root)
    if not link_parent:
        assert victim.read_text(encoding="utf-8") == "PRIVATE_VICTIM"


def test_state_path_substitution_after_constructor_fails_closed(tmp_path):
    _, path, root = setup_env(tmp_path)
    store = DurableIdempotencyStore(path, root)
    victim = tmp_path / "victim.json"
    victim.write_text("PRIVATE_VICTIM", encoding="utf-8")
    native_hardlink(victim, path)
    event, _ = make_event()
    with pytest.raises(OriginError, match="LOCATION_INVALID"):
        store.record_intent(event["event_id"], "routeA", 1)
    assert victim.read_text(encoding="utf-8") == "PRIVATE_VICTIM"


@pytest.mark.parametrize("suffix", ["", ".lock"])
def test_registry_and_store_paths_must_not_alias(tmp_path, suffix):
    kwargs = prepared_delivery(tmp_path)
    kwargs["idempotency_path"] = kwargs["registry_path"].with_name(kwargs["registry_path"].name + suffix)
    adapter = UnknownFakeAdapter()
    receipt = deliver_fixture_wake(**kwargs, adapter=adapter)
    assert receipt.error_code == "STATE_PATH_CONFLICT"
    assert adapter.messages == []


def test_non_loopback_probe_rejected_before_any_client_activity():
    class UnexpectedClient:
        def get_targets(self):
            pytest.fail("remote CDP endpoint used")

    binding = ResolvedBinding("routeA", 1, CHAT_A, "http://example.com:9222")
    receipt = LocalCdpWakeAdapter(UnexpectedClient()).send_wake(binding=binding, message="fake", event_id="fake")
    assert receipt.error_code == "CDP_ENDPOINT_INVALID"


@pytest.mark.parametrize("targets", [None, [None], [{"id": "t"}],
    [{"id": "same", "type": "page", "url": CHAT_A}, {"id": "same", "type": "page", "url": CHAT_B}],
])
def test_malformed_or_duplicate_cdp_targets_never_attach(targets):
    client = FakeCdpClient(targets)
    receipt = LocalCdpWakeAdapter(client).send_wake(
        binding=ResolvedBinding("routeA", 1, CHAT_A, CDP_PORT), message="fake", event_id="fake")
    assert receipt.error_code == "CDP_TARGETS_INVALID"
    assert client.attached == []


@pytest.mark.parametrize("stage", ["get_targets", "attach_to_target", "is_authenticated"])
def test_probe_errors_never_expose_local_secrets(stage):
    session = FakeCdpSession()
    client = FakeCdpClient([{"id": "t1", "type": "page", "url": CHAT_A}], {"t1": session})

    def broken(*args, **kw):
        raise RuntimeError(CHAT_A + " PRIVATE_COOKIE")

    setattr(session if stage == "is_authenticated" else client, stage, broken)
    receipt = LocalCdpWakeAdapter(client).send_wake(
        binding=ResolvedBinding("routeA", 1, CHAT_A, CDP_PORT), message="fake", event_id="fake")
    assert receipt.delivery_status == "UNSUPPORTED"
    assert "PRIVATE_COOKIE" not in repr(receipt) + receipt.error_code
    assert session.messages == []


def native_hardlink(source, target):
    try:
        os.link(source, target)
    except OSError as exc:
        if isinstance(exc, PermissionError) or exc.errno in (38, 95):
            pytest.skip(f"Native hardlink unavailable: {exc.winerror if os.name == 'nt' else exc.errno}")
        raise


@pytest.mark.parametrize("surprise", ["hardlink", "reparse_file", "reparse_parent"])
def test_path_metadata_surprises_fail_before_state_access(tmp_path, monkeypatch, surprise):
    _, path, root = setup_env(tmp_path)
    path.write_text("PRIVATE_VICTIM", encoding="utf-8")
    original = Path.lstat

    def changed_info(current, *args, **kwargs):
        info = original(current, *args, **kwargs)
        target = path.parent if surprise == "reparse_parent" else path
        if current == target:
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=2 if surprise == "hardlink" else 1,
                                   st_file_attributes=0 if surprise == "hardlink" else 0x400)
        return info

    monkeypatch.setattr(Path, "lstat", changed_info)
    with pytest.raises(OriginError, match="LOCATION_INVALID"):
        DurableIdempotencyStore(path, root)
    assert path.read_text(encoding="utf-8") == "PRIVATE_VICTIM"


def test_opened_hardlink_is_rejected_even_after_initial_path_check(tmp_path, monkeypatch):
    from ai_commerce.remote import _private_state
    _, path, root = setup_env(tmp_path)
    store = DurableIdempotencyStore(path, root)
    original_regular = _private_state._regular

    def opened_hardlink(info):
        # Keep real os.fstat() intact: patching the shared os module also
        # affects Python's own descriptor handling on GitHub Python 3.11.
        return original_regular(SimpleNamespace(
            st_mode=info.st_mode, st_nlink=2, st_file_attributes=0,
        ))

    monkeypatch.setattr(_private_state, "_regular", opened_hardlink)
    event, _ = make_event()
    with pytest.raises(OriginError, match="PATH_UNSAFE"):
        store.record_intent(event["event_id"], "routeA", 1)
    assert not path.exists()


def test_concurrent_registry_writers_preserve_routes_and_claims(tmp_path):
    path, _, root = setup_env(tmp_path)
    attestation = make_attestation()
    gate = threading.Barrier(4)

    def register(index):
        registrar = trusted_registrar(path, root)
        gate.wait(timeout=10)
        registrar.register_route(route_id=f"route{index}", consent=True, attestation=attestation,
                                 chat_url=f"https://chatgpt.com/c/00000000-0000-0000-0000-{index + 10:012x}",
                                 cdp_endpoint=CDP_PORT)
        registrar.bind_claim(route_id=f"route{index}", repository=REPO, batch_id=f"batch{index}",
                             source_sha=SHA, issue_number=12 + index, attestation=attestation)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(register, range(4)))
    data = trusted_registrar(path, root).read_registry()
    assert len(data["routes"]) == len(data["claims"]) == 4
    assert {r["route_id"] for r in data["routes"]} == {f"route{i}" for i in range(4)}


def test_route_generation_stays_pinned_during_adapter_call(tmp_path):
    kwargs = prepared_delivery(tmp_path)
    entered, release, advancing = threading.Event(), threading.Event(), threading.Event()

    class PausedAdapter(UnknownFakeAdapter):
        def send_wake(self, **kw):
            entered.set()
            assert release.wait(timeout=4)
            return super().send_wake(**kw)

    def advance():
        advancing.set()
        return trusted_registrar(kwargs["registry_path"], kwargs["repository_root"]).register_route(
            route_id="routeA", consent=True, generation=2, chat_url=CHAT_A,
            cdp_endpoint=CDP_PORT, attestation=kwargs["origin_attestation"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery = pool.submit(deliver_fixture_wake, **kwargs, adapter=PausedAdapter())
        try:
            assert entered.wait(timeout=4)
            update = pool.submit(advance)
            assert advancing.wait(timeout=4)
            with pytest.raises(TimeoutError):
                update.result(timeout=0.05)
        finally:
            release.set()
        assert delivery.result(timeout=4).generation == 1
        assert update.result(timeout=4)["generation"] == 2
    assert deliver_fixture_wake(**kwargs).error_code == "STALE_CLAIM"


def test_origin_authority_receives_and_rejects_tampered_exact_registry(tmp_path):
    kwargs = prepared_delivery(tmp_path)
    original = json.loads(kwargs["registry_path"].read_text(encoding="utf-8"))

    class PinnedOriginAuthority:
        def authorize_origin(self, attestation, *, operation, context):
            return (attestation is kwargs["origin_attestation"] and
                    ("registry" not in context or context["registry"] == original))

    changed = copy.deepcopy(original)
    changed["routes"][0]["chat_url"] = CHAT_B
    kwargs["registry_path"].write_text(json.dumps(changed), encoding="utf-8")
    adapter = UnknownFakeAdapter()
    receipt = deliver_exact_chat_wake(**kwargs, adapter=adapter, origin_authority=PinnedOriginAuthority(),
                                      artifact_authority=ARTIFACT_AUTHORITY)
    assert receipt.error_code == "UNVERIFIED_ORIGIN"
    assert adapter.messages == []


def test_duplicate_store_keys_fail_closed(tmp_path):
    kwargs = prepared_delivery(tmp_path)
    kwargs["idempotency_path"].write_text('{"version":1,"records":{},"records":{}}', encoding="utf-8")
    adapter = UnknownFakeAdapter()
    receipt = deliver_fixture_wake(**kwargs, adapter=adapter)
    assert receipt.error_code == "IDEMPOTENCY_STORE_DUPLICATE_KEY"
    assert adapter.messages == []


def test_no_outcome_without_persistent_intent(tmp_path):
    _, path, root = setup_env(tmp_path)
    event, _ = make_event()
    with pytest.raises(OriginError, match="INTENT_REQUIRED"):
        DurableIdempotencyStore(path, root).record_outcome(
            event["event_id"], "routeA", 1, delivery_status="UNSUPPORTED", chat_delivery_proven=False)
    assert not path.exists()


def test_failed_durable_write_never_dispatches_adapter(tmp_path, monkeypatch):
    from ai_commerce.remote import _private_state
    kwargs = prepared_delivery(tmp_path)

    def disk_failure(fd):
        raise OSError("PRIVATE_DISK_PATH")

    monkeypatch.setattr(_private_state.os, "fsync", disk_failure)
    adapter = UnknownFakeAdapter()
    receipt = deliver_fixture_wake(**kwargs, adapter=adapter)
    assert receipt.delivery_status == "UNKNOWN"
    assert receipt.error_code == "IDEMPOTENCY_STORE_UNAVAILABLE"
    assert adapter.messages == []
    assert "PRIVATE_DISK_PATH" not in repr(receipt)


def test_live_probe_is_explicit_bounded_and_does_not_follow_redirects(monkeypatch):
    from ai_commerce.remote import wake_adapter
    probes = []

    class FakeConnection:
        def __init__(self, host, port, timeout):
            assert (host, port) == ("127.0.0.1", 9222)

        def request(self, method, path):
            probes.append((method, path))

        def getresponse(self):
            return SimpleNamespace(status=302, read=lambda size: pytest.fail("redirect body consumed"))

        def close(self):
            pass

    monkeypatch.setattr(wake_adapter.http.client, "HTTPConnection", FakeConnection)
    binding = ResolvedBinding("routeA", 1, CHAT_A, "http://localhost:9222")
    receipt = LocalCdpWakeAdapter(probe_enabled=True).send_wake(binding=binding, message="fake", event_id="fake")
    assert receipt.error_code == "CDP_UNAVAILABLE"
    assert probes == [("GET", "/json/list")]


@pytest.mark.parametrize("accepted", [False, 1, "true", None])
def test_authority_must_return_explicit_true(accepted):
    class NonVerifier:
        def authorize_origin(self, *args, **kw):
            return accepted

        def verify_artifact(self, *args, **kw):
            return accepted

    attestation = make_attestation()
    assert attestation.is_trusted(authority=NonVerifier(), operation="deliver_wake") is False
    event, result = make_event()
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_provenance(event, result, make_provenance(), authority=NonVerifier())
