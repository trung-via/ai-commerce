"""Authenticated GitHub comment intake for D5 ledger; all network mocked."""
import json
from copy import deepcopy

import pytest

from ai_commerce.remote.plugin_turn_evidence import (
    GitHubTurnEvidenceAuthority, expected_observation_packet,
)
from ai_commerce.remote.plugin_turn_handoff import (
    new_session, record_send_intent,
)
from ai_commerce.remote.plugin_turn_store import TurnSessionStore
from test_ef02_d5_local_handoff import provision

API = "https://api.github.com/repos/trung-via/ai-commerce"
REF = "https://github.com/trung-via/ai-commerce/issues/181#issuecomment-987"
SHA = "b" * 40
ID = "d5-turn-" + "a" * 32


def pending():
    return record_send_intent(new_session(
        handoff_id=ID, issue_number=181, source_sha=SHA),
        operator_approved=True)


def fixture_json(state, *, author=123):
    return {
        "id": 987, "html_url": REF, "issue_url": API + "/issues/181",
        "user": {"id": author},
        "created_at": "2026-11-01T00:00:00Z",
        "updated_at": "2026-11-01T00:00:00Z",
        "body": json.dumps(expected_observation_packet(state)),
    }


def authority(state, *, comment=None, branch=None):
    calls = []
    c = fixture_json(state) if comment is None else comment
    b = {"name": "main", "commit": {"sha": SHA}} if branch is None else branch

    def read(url, token):
        calls.append(url)
        assert token == "opaque-test-token"
        if url == API + "/issues/comments/987":
            return deepcopy(c)
        if url == API + "/branches/main":
            return deepcopy(b)
        raise ValueError("must not query an untrusted URL")

    return GitHubTurnEvidenceAuthority(
        token_provider=lambda: "opaque-test-token",
        expected_author_id=123, read_json=read), calls


def test_strict_authenticated_comment_and_head_match():
    state = pending()
    verified, calls = authority(state)
    assert verified.verify(state, REF) is True
    assert calls == [API + "/issues/comments/987", API + "/branches/main"]


@pytest.mark.parametrize("change", [
    ("id", 988),
    ("html_url", "https://github.com/attacker/repo"),
    ("issue_url", API + "/issues/180"),
    ("user", {"id": 999}),
    ("updated_at", "2026-11-01T00:01:00Z"),
    ("body", "{}"),
    ("body", "very private token " * 500),
])
def test_comment_identity_and_structure_fail_closed(change):
    state = pending()
    c = fixture_json(state)
    c[change[0]] = change[1]
    verified, _ = authority(state, comment=c)
    assert verified.verify(state, REF) is False


@pytest.mark.parametrize("field,value", [
    ("schema", "unknown"),
    ("issue_number", True),
    ("issue_number", 180),
    ("turn_index", 1),
    ("turn_index", True),
    ("role", "ENGINEERING"),
    ("source_sha", "c" * 40),
    ("handoff_id", "d5-turn-" + "f" * 32),
    ("observation", "PLUGIN_INVOKED"),
    ("host_selection_proven", True),
])
def test_packet_mismatch_fails_closed(field, value):
    state = pending()
    c = fixture_json(state)
    p = json.loads(c["body"])
    p[field] = value
    c["body"] = json.dumps(p)
    verified, _ = authority(state, comment=c)
    assert verified.verify(state, REF) is False


def test_duplicate_key_fails_closed():
    state = pending()
    c = fixture_json(state)
    c["body"] = c["body"].replace('"role": "BRAIN"',
                                    '"role": "BRAIN", "role": "BRAIN"')
    verified, _ = authority(state, comment=c)
    assert verified.verify(state, REF) is False


def test_source_main_drift_blocks():
    state = pending()
    verified, _ = authority(
        state, branch={"name": "main", "commit": {"sha": "c" * 40}})
    assert verified.verify(state, REF) is False


@pytest.mark.parametrize("bad_ref", [
    "https://github.com/trung-via/ai-commerce/issues/181#issuecomment-0",
    "https://github.com/trung-via/ai-commerce/issues/181#issuecomment-987?x=1",
    "https://github.com/trung-via/ai-commerce/issues/180#issuecomment-987",
    "https://evil.test/#issuecomment-987",
])
def test_untrusted_ref_never_networks(bad_ref):
    state = pending()
    verified, calls = authority(state)
    assert verified.verify(state, bad_ref) is False
    assert calls == []


def test_no_git_hub_lookup_for_ready():
    state = new_session(handoff_id=ID, issue_number=181, source_sha=SHA)
    verified, calls = authority(pending())
    assert verified.verify(state, REF) is False
    assert calls == []


def test_invalid_token_and_exception_fail_closed():
    state = pending()
    for token in ("", "x\nsecret", "a" * 4097):
        v = GitHubTurnEvidenceAuthority(
            token_provider=lambda: token,
            expected_author_id=123,
            read_json=lambda *_: pytest.fail("invalid token cannot read"))
        assert v.verify(state, REF) is False

    v = GitHubTurnEvidenceAuthority(
        token_provider=lambda: "private-token",
        expected_author_id=123,
        read_json=lambda *_: (_ for _ in ()).throw(
            OSError("private token leaked?")))
    assert v.verify(state, REF) is False


def test_live_transport_not_called_by_tests():
    import inspect
    import ai_commerce.remote.plugin_turn_evidence as module
    source = inspect.getsource(module)
    assert "button.click(" not in source
    assert "subprocess" not in source
    assert "page.goto(" not in source


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    private = provision(tmp_path / "private")
    ledger = TurnSessionStore(
        path=private / "turn.json", repository_root=root)
    ledger.create(handoff_id=ID, issue_number=181, source_sha=SHA)
    ledger.record_intent(operator_approved=True)
    return ledger


def test_verified_reconciliation_requires_both_human_and_github(store):
    snapshot = store.read()
    verified, _ = authority(snapshot)
    with pytest.raises(Exception, match="APPROVAL_REQUIRED"):
        store.observe_verified_reply(issue_comment_ref=REF, authority=verified)
    assert store.read() == snapshot
    value = store.observe_verified_reply(
        issue_comment_ref=REF, authority=verified, operator_verified=True)
    assert value["turn_index"] == 1
    assert value["phase"] == "READY"
    assert store.read() == value


def test_verified_reconciliation_rejects_fake_backend(store):
    snapshot = store.read()
    verified, _ = authority(snapshot, comment=fixture_json(snapshot, author=999))
    with pytest.raises(Exception, match="EVIDENCE_UNVERIFIED"):
        store.observe_verified_reply(
            issue_comment_ref=REF, authority=verified, operator_verified=True)
    assert store.read() == snapshot


def test_verified_reconciliation_does_not_race_state_change(store):
    snapshot = store.read()

    class RacyAuthority:
        def verify(self, state, ref):
            assert state == snapshot
            # Another Human legitimately reconciled the one pending turn.
            store.observe_reply(issue_comment_ref=REF, operator_verified=True)
            return True

    with pytest.raises(Exception, match="STATE_CHANGED"):
        store.observe_verified_reply(
            issue_comment_ref=REF, authority=RacyAuthority(),
            operator_verified=True)
    assert store.read()["turn_index"] == 1


def test_non_boolean_verifier_result_rejected(store):
    with pytest.raises(Exception, match="EVIDENCE_UNVERIFIED"):
        store.observe_verified_reply(
            issue_comment_ref=REF,
            authority=type("Pretend", (), {"verify": lambda *x: 1})(),
            operator_verified=True)
    assert store.read()["phase"] == "SUBMISSION_UNKNOWN"
