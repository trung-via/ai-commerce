"""D1 tests: determinism, no leakage, fail-closed exact route isolation."""
import copy
import hashlib
import json

import pytest

from ai_commerce.remote.completion_event import CompletionEventError, build_completion_event, validate_completion_event
from ai_commerce.remote.conversation_binding import BindingError, resolve_binding

SHA = "a" * 40
CHAT_A = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001"
CHAT_B = "https://chatgpt.com/g/g-abc123/c/00000000-0000-0000-0000-000000000002"


def result(batch_id="batchA", *, status="CANDIDATE_READY"):
    return {"kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
            "batch_id": batch_id, "source_sha": SHA,
            "status": "COMPLETE" if status == "CANDIDATE_READY" else "PARTIAL_OR_FAILED",
            "total": 1, "succeeded": int(status == "CANDIDATE_READY"),
            "actions": [{"action_id": "a", "attempt_id": "first", "status": status,
                         "candidate_sha": "b" * 40 if status == "CANDIDATE_READY" else None,
                         "diagnostic": "SECRET_OR_PRIVATE_LOG"}],
            "feedback_status": "GITHUB_ISSUE_COMMENT_PUBLISHED"}


def event(batch_id="batchA", run_id=42, issue_number=12):
    return build_completion_event(result(batch_id), repository="trung-via/ai-commerce",
                                  run_id=run_id, run_attempt=1, issue_number=issue_number)


def route(route_id, chat_url):
    return {"route_id": route_id, "generation": 1, "chat_url": chat_url,
            "cdp_endpoint": "http://127.0.0.1:9222", "consent": True}


def claim(route_id, batch_id, issue_number):
    return {"route_id": route_id, "generation": 1, "repository": "trung-via/ai-commerce",
            "batch_id": batch_id, "source_sha": SHA, "issue_number": issue_number}


def registry(tmp_path, routes, claims=None):
    outside = tmp_path / "local"
    outside.mkdir(exist_ok=True)
    p = outside / "bindings.json"
    p.write_text(json.dumps({"version": 1, "routes": routes, "claims": claims if claims is not None else [claim("routeA", "batchA", 12)]}), encoding="utf-8")
    root = tmp_path / "checkout"
    root.mkdir(exist_ok=True)
    return p, root


def test_deterministic_event_and_changes_affect_dedup():
    a = event()
    assert a == event() and a["event_id"].startswith("ef02-completion-v1:")
    assert a["event_id"] != event(run_id=43)["event_id"]
    assert a["event_id"] != event(batch_id="batchB")["event_id"]
    assert a["origin_binding_status"] == "UNBOUND"
    assert a["delivery_status"] == "NOT_ATTEMPTED"
    assert a["chat_delivery_proven"] is False
    assert "SECRET_OR_PRIVATE_LOG" not in json.dumps(a)
    assert "chat_url" not in json.dumps(a)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(status="IN_PROGRESS"),
    lambda r: r["actions"][0].update(status="NOT_STARTED"),
    lambda r: r.update(total=2),
    lambda r: r.update(succeeded=0),
    lambda r: r.update(batch_id="bad/route"),
])
def test_invalid_or_nonterminal_result_rejected(mutate):
    r = result()
    mutate(r)
    with pytest.raises(CompletionEventError):
        build_completion_event(r, repository="trung-via/ai-commerce", run_id=42,
                               run_attempt=1, issue_number=12)


@pytest.mark.parametrize("status", [
    "VERIFIED_SCOPE_REVIEW_REQUIRED", "NO_CODE_CHANGE", "SOURCE_DRIFT",
    "EXECUTOR_UNAVAILABLE", "EXECUTOR_PERMISSION_DENIED",
])
def test_terminal_ef01_nonpublishable_result_is_recorded_and_bound(tmp_path, status):
    # Real EF01 final statuses are forwarded by EF02's failed-action path.
    # Recording completion must not imply a verified/published candidate.
    p, root = registry(tmp_path, [route("routeA", CHAT_A)])
    e = build_completion_event(result(status=status),
                               repository="trung-via/ai-commerce",
                               run_id=42, run_attempt=1, issue_number=12)
    assert e["batch_status"] == "PARTIAL_OR_FAILED"
    assert e["actions"] == [{"action_id": "a", "attempt_id": "first", "status": status}]
    assert e["delivery_status"] == "NOT_ATTEMPTED"
    assert e["chat_delivery_proven"] is False
    assert "SECRET_OR_PRIVATE_LOG" not in json.dumps(e)
    assert validate_completion_event(e) == e
    assert resolve_binding(e, route_id="routeA", registry_path=p,
                           repository_root=root).chat_url == CHAT_A


def test_mixed_batch_with_scope_review_is_partial_not_complete():
    r = result()
    r["status"] = "PARTIAL_OR_FAILED"
    r["total"] = 2
    r["actions"].append({
        "action_id": "b", "attempt_id": "second",
        "status": "VERIFIED_SCOPE_REVIEW_REQUIRED",
        "candidate_sha": None, "diagnostic": "PRIVATE_SCOPE_LOG",
    })
    e = build_completion_event(r, repository="trung-via/ai-commerce",
                               run_id=44, run_attempt=2, issue_number=14)
    assert e["batch_status"] == "PARTIAL_OR_FAILED"
    assert e["actions"][0]["status"] == "CANDIDATE_READY"
    assert e["actions"][1] == {
        "action_id": "b", "attempt_id": "second",
        "status": "VERIFIED_SCOPE_REVIEW_REQUIRED",
    }
    assert "PRIVATE_SCOPE_LOG" not in json.dumps(e)
    assert validate_completion_event(e) == e


def test_all_failed_batch_preserves_final_statuses_and_exact_route(tmp_path):
    r = result(status="VERIFICATION_FAILED")
    r["total"] = 2
    r["actions"].append({
        "action_id": "b", "attempt_id": "second",
        "status": "BLOCKED_DEPENDENCY", "candidate_sha": None,
        "diagnostic": "PRIVATE_DEPENDENCY_LOG",
    })
    assert r["succeeded"] == 0 and r["status"] == "PARTIAL_OR_FAILED"
    e = build_completion_event(r, repository="trung-via/ai-commerce",
                               run_id=42, run_attempt=1, issue_number=12)
    assert e == build_completion_event(r, repository="trung-via/ai-commerce",
                                       run_id=42, run_attempt=1, issue_number=12)
    assert e["batch_status"] == "PARTIAL_OR_FAILED"
    assert e["actions"] == [
        {"action_id": "a", "attempt_id": "first", "status": "VERIFICATION_FAILED"},
        {"action_id": "b", "attempt_id": "second", "status": "BLOCKED_DEPENDENCY"},
    ]
    assert validate_completion_event(e) == e
    serialized = json.dumps(e)
    assert "candidate_sha" not in serialized
    assert "diagnostic" not in serialized
    assert "SECRET_OR_PRIVATE_LOG" not in serialized
    assert "PRIVATE_DEPENDENCY_LOG" not in serialized
    assert e["origin_binding_status"] == "UNBOUND"
    assert e["delivery_status"] == "NOT_ATTEMPTED"
    assert e["chat_delivery_proven"] is False
    p, root = registry(tmp_path, [route("routeB", CHAT_B), route("routeA", CHAT_A)],
                       [claim("routeA", "batchA", 12)])
    bound = resolve_binding(e, route_id="routeA", registry_path=p, repository_root=root)
    assert bound.route_id == "routeA" and bound.chat_url == CHAT_A
    with pytest.raises(BindingError, match="ROUTE_EVENT_MISMATCH"):
        resolve_binding(e, route_id="routeB", registry_path=p, repository_root=root)


@pytest.mark.parametrize("status", [
    "NOT_STARTED", "EXECUTOR_RETURNED_UNVERIFIED",
    "BUILD_VERIFIED_FOR_SCOPE", "CLAIMED", "RUNNING",
])
def test_transient_ef01_status_is_still_rejected(status):
    with pytest.raises(CompletionEventError, match="ACTION_NOT_FINAL"):
        build_completion_event(result(status=status),
                               repository="trung-via/ai-commerce",
                               run_id=42, run_attempt=1, issue_number=12)


def test_two_conversations_match_only_their_exact_origin(tmp_path):
    e_a = event()
    e_b = event("batchB", 43, 13)
    p, root = registry(tmp_path, [route("routeA", CHAT_A), route("routeB", CHAT_B)],
                       [claim("routeA", "batchA", 12), claim("routeB", "batchB", 13)])
    got_a = resolve_binding(e_a, route_id="routeA", registry_path=p, repository_root=root)
    got_b = resolve_binding(e_b, route_id="routeB", registry_path=p, repository_root=root)
    assert got_a.chat_url == CHAT_A and got_b.chat_url == CHAT_B
    assert "chatgpt" not in repr(got_a)
    with pytest.raises(BindingError, match="ROUTE_EVENT_MISMATCH"):
        resolve_binding(e_b, route_id="routeA", registry_path=p, repository_root=root)
    with pytest.raises(BindingError, match="ROUTE_EVENT_MISMATCH"):
        resolve_binding(e_a, route_id="routeB", registry_path=p, repository_root=root)


@pytest.mark.parametrize("change", [
    lambda r: r[1].update(route_id="routeA"),
    lambda r: r[1].update(chat_url=CHAT_A),
    lambda r: r[0].update(consent=False),
    lambda r: r[0].update(cdp_endpoint="http://example.com:9222"),
    lambda r: r[0].update(generation=0),
    lambda r: r[0].update(chat_url="https://evil.example/c/" + CHAT_A.rsplit("/", 1)[-1]),
])
def test_conflicts_or_untrusted_surface_fail_closed(tmp_path, change):
    routes = [route("routeA", CHAT_A), route("routeB", CHAT_B)]
    change(routes)
    p, root = registry(tmp_path, routes)
    with pytest.raises(BindingError):
        resolve_binding(event(), route_id="routeA", registry_path=p, repository_root=root)


def test_missing_wrong_or_in_repo_registry_rejected(tmp_path):
    p, root = registry(tmp_path, [route("routeA", CHAT_A)])
    with pytest.raises(BindingError, match="ROUTE_EVENT_MISMATCH"):
        resolve_binding(event(), route_id="unknown", registry_path=p, repository_root=root)
    with pytest.raises(BindingError, match="REGISTRY_LOCATION_INVALID"):
        resolve_binding(event(), route_id="routeA", registry_path=root / "bindings.json", repository_root=root)
    p.write_text('{"version":1,"version":1,"routes":[],"claims":[]}', encoding="utf-8")
    with pytest.raises(BindingError, match="REGISTRY_DUPLICATE_KEY"):
        resolve_binding(event(), route_id="routeA", registry_path=p, repository_root=root)


def test_event_cannot_self_certify_delivery_or_select_default_chat(tmp_path):
    p, root = registry(tmp_path, [route("routeA", CHAT_A)])
    e = event()
    for key, value in (("delivery_status", "DELIVERED_TO_EXACT_CHAT"),
                       ("origin_binding_status", "BOUND")):
        forged = copy.deepcopy(e)
        forged[key] = value
        with pytest.raises(BindingError, match="EVENT_NOT_ELIGIBLE"):
            resolve_binding(forged, route_id="routeA", registry_path=p, repository_root=root)


def test_same_conversation_serves_multiple_batches_without_default_route(tmp_path):
    e_a = event()
    e_b = event("batchB", 43, 13)
    p, root = registry(tmp_path, [route("routeA", CHAT_A)],
                       [claim("routeA", "batchA", 12), claim("routeA", "batchB", 13)])
    for item in (e_a, e_b):
        bound = resolve_binding(item, route_id="routeA", registry_path=p, repository_root=root)
        assert bound.chat_url == CHAT_A
    with pytest.raises(BindingError, match="ROUTE_EVENT_MISMATCH"):
        resolve_binding(e_a, route_id="nonexistent", registry_path=p, repository_root=root)


def test_rebinding_generation_invalidates_stale_claim(tmp_path):
    p, root = registry(tmp_path, [route("routeA", CHAT_A)])
    content = json.loads(p.read_text())
    content["routes"][0]["generation"] = 2
    p.write_text(json.dumps(content))
    with pytest.raises(BindingError, match="ROUTE_EVENT_MISMATCH"):
        resolve_binding(event(), route_id="routeA", registry_path=p, repository_root=root)


def test_changed_source_or_event_digest_is_rejected(tmp_path):
    p, root = registry(tmp_path, [route("routeA", CHAT_A)])
    forged = event()
    forged["source_sha"] = "b" * 40
    with pytest.raises(BindingError, match="EVENT_IDENTITY_INVALID"):
        resolve_binding(forged, route_id="routeA", registry_path=p, repository_root=root)
    forged = event()
    forged["chat_delivery_proven"] = True
    with pytest.raises(BindingError, match="EVENT_IDENTITY_INVALID"):
        resolve_binding(forged, route_id="routeA", registry_path=p, repository_root=root)

def rehash(e):
    immutable = {key: e[key] for key in (
        "repository", "run_id", "run_attempt", "issue_number", "batch_id",
        "source_sha", "batch_status", "actions")}
    e["event_id"] = "ef02-completion-v1:" + hashlib.sha256(
        json.dumps(immutable, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(batch_status="IN_PROGRESS"),
    lambda e: e["actions"][0].update(status="RUNNING"),
    lambda e: e.update(batch_status="PARTIAL_OR_FAILED"),
    lambda e: e["actions"][0].update(status="EXECUTION_UNKNOWN"),
    lambda e: e.update(version=True),
    lambda e: e["actions"][0].update(diagnostic="NOT_PUBLIC"),
])
def test_recomputed_hash_does_not_bypass_finality_or_shape(tmp_path, mutate):
    p, root = registry(tmp_path, [route("routeA", CHAT_A)])
    e = event()
    mutate(e)
    rehash(e)  # adversary can recompute a *public* SHA-256, not attest the source
    with pytest.raises((BindingError, CompletionEventError)):
        resolve_binding(e, route_id="routeA", registry_path=p, repository_root=root)


def test_previous_generation_claim_is_preserved_but_not_reused(tmp_path):
    p, root = registry(tmp_path, [route("routeA", CHAT_A)],
                       [claim("routeA", "batchA", 12), claim("routeA", "batchB", 13)])
    content = json.loads(p.read_text())
    content["routes"][0]["generation"] = 2
    content["claims"][1]["generation"] = 2
    p.write_text(json.dumps(content))
    fresh = resolve_binding(event("batchB", 43, 13), route_id="routeA",
                            registry_path=p, repository_root=root)
    assert fresh.generation == 2 and fresh.chat_url == CHAT_A
    with pytest.raises(BindingError, match="ROUTE_EVENT_MISMATCH"):
        resolve_binding(event("batchA", 42, 12), route_id="routeA",
                        registry_path=p, repository_root=root)
    assert content["claims"][0]["generation"] == 1


def test_old_claim_in_route_b_cannot_poison_new_route_a(tmp_path):
    p, root = registry(tmp_path, [route("routeA", CHAT_A), route("routeB", CHAT_B)],
                       [claim("routeA", "batchA", 12), claim("routeB", "oldB", 21)])
    data = json.loads(p.read_text())
    data["routes"][1]["generation"] = 3
    p.write_text(json.dumps(data))
    assert resolve_binding(event(), route_id="routeA", registry_path=p,
                           repository_root=root).chat_url == CHAT_A


def test_valid_partial_completion_remains_wake_eligible(tmp_path):
    p, root = registry(tmp_path, [route("routeA", CHAT_A)])
    e = build_completion_event(result(status="EXECUTION_UNKNOWN"),
                               repository="trung-via/ai-commerce",
                               run_id=42, run_attempt=1, issue_number=12)
    assert validate_completion_event(e) == e
    assert resolve_binding(e, route_id="routeA", registry_path=p,
                           repository_root=root).chat_url == CHAT_A


def test_cli_records_event_without_changing_ef02_result(monkeypatch, tmp_path):
    from ai_commerce.remote import __main__ as cli

    class FakeGitHub:
        comments = []
        def __init__(self, repo, token):
            assert repo == "trung-via/ai-commerce"
        def comment(self, issue_number, text):
            self.comments.append((issue_number, text))

    class FakeWorker:
        invokes = 0
        def __init__(self, **kwargs):
            self.root = tmp_path / "evidence"
            self.root.mkdir(exist_ok=True)
        def execute_action(self, action):
            self.invokes += 1
            return {"status": "CANDIDATE_READY"}

    monkeypatch.setattr(cli, "GitHubTransport", FakeGitHub)
    monkeypatch.setattr(cli, "LocalBatchWorker", FakeWorker)
    monkeypatch.setattr(cli, "validate_batch", lambda x, **kw: result())
    monkeypatch.setattr(cli, "verify_registry", lambda p: {"project-tests": ["python", "-m", "pytest", "tests", "-q"]})
    monkeypatch.setattr(cli, "load_approved_candidates", lambda p: {})
    monkeypatch.setattr(cli, "check_source", lambda *a, **kw: None)
    called = []
    def fake_schedule(batch, worker, *, max_workers):
        called.append(max_workers)
        return result()
    monkeypatch.setattr(cli, "run_schedule", fake_schedule)
    monkeypatch.setenv("GITHUB_TOKEN", "INTERNAL_ONLY")
    monkeypatch.setenv("GITHUB_REPOSITORY", "trung-via/ai-commerce")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("EF02_HOST_SLOTS", "2")
    request = tmp_path / "request.json"
    request.write_text("{}")

    args = ["run", "--checkout", str(tmp_path), "--request-file", str(request),
            "--issuer-login", "trung-via", "--owner-login", "trung-via",
            "--issue-number", "12"]
    assert cli.main(args) == 0
    root = tmp_path / "evidence"
    e = json.loads((root / "completion_event.json").read_text())
    r = json.loads((root / "batch_result.json").read_text())
    assert validate_completion_event(e) == e
    assert e["run_id"] == 99 and e["issue_number"] == 12
    assert r["wake_event_status"] == "RECORDED_UNBOUND"
    assert r["feedback_status"] == "GITHUB_ISSUE_COMMENT_PUBLISHED"
    assert r["status"] == "COMPLETE" and r["succeeded"] == 1
    assert len(FakeGitHub.comments) == 1 and called == [2]
    assert "INTERNAL_ONLY" not in (root / "completion_event.json").read_text()
    assert e["delivery_status"] == "NOT_ATTEMPTED"
    
    # A broken optional event emitter cannot cause reexecution or change EF02.
    monkeypatch.setattr(cli, "build_completion_event", lambda *a, **kw: (_ for _ in ()).throw(ValueError("BROKEN")))
    assert cli.main(args) == 0
    r2 = json.loads((root / "batch_result.json").read_text())
    assert r2["wake_event_status"] == "NOT_RECORDED"
    assert r2["wake_event_error_code"] == "ValueError"
    assert r2["status"] == "COMPLETE" and r2["succeeded"] == 1
    assert not (root / "completion_event.json").exists()
    assert called == [2, 2]  # two explicitly distinct simulated CLI runs; no wake retry
