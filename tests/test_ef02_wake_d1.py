"""D1 tests: determinism, no leakage, fail-closed exact route isolation."""
import copy
import json

import pytest

from ai_commerce.remote.completion_event import CompletionEventError, build_completion_event
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
    with pytest.raises(BindingError, match="REGISTRY_INVALID"):
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
