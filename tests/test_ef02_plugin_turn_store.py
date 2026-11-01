"""D5 turn private store fixture tests; never CDP, GitHub or Send."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from ai_commerce.remote.plugin_turn_handoff import TurnHandoffError
from ai_commerce.remote.plugin_turn_store import TurnSessionStore
from test_ef02_d5_local_handoff import provision


@pytest.fixture
def ledger(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    private = provision(tmp_path / "private")
    return TurnSessionStore(path=private / "turn.json", repository_root=root)


def new(ledger):
    return ledger.create(
        handoff_id="d5-turn-" + "1" * 32, issue_number=181,
        source_sha="2" * 40)


def test_private_store_requires_explicit_create(ledger):
    with pytest.raises(TurnHandoffError, match="NOT_FOUND"):
        ledger.read()
    assert not ledger.state.path.exists()


def test_private_store_persists_immutable_unique_session(ledger):
    first = new(ledger)
    assert ledger.read() == first
    with pytest.raises(TurnHandoffError, match="ALREADY_EXISTS"):
        new(ledger)
    assert ledger.read() == first


def test_private_store_refuses_unauthorized_intent_without_writes(ledger):
    new(ledger)
    before = ledger.state.path.read_bytes()
    with pytest.raises(TurnHandoffError, match="APPROVAL_REQUIRED"):
        ledger.record_intent(operator_approved=False)
    assert ledger.state.path.read_bytes() == before


def test_private_store_persists_unknown_and_no_auto_replay(ledger):
    new(ledger)
    unknown = ledger.record_intent(operator_approved=True)
    assert unknown["phase"] == "SUBMISSION_UNKNOWN"
    assert ledger.read() == unknown
    before = ledger.state.path.read_bytes()
    with pytest.raises(TurnHandoffError, match="NOT_READY"):
        ledger.record_intent(operator_approved=True)
    assert ledger.state.path.read_bytes() == before


def test_checked_intent_requires_approval_and_exact_snapshot(ledger):
    ready = new(ledger)
    before = ledger.state.path.read_bytes()
    with pytest.raises(TurnHandoffError, match="APPROVAL_REQUIRED"):
        ledger.record_intent_checked(expected_state=ready)
    assert ledger.state.path.read_bytes() == before

    stale = deepcopy(ready)
    stale["source_sha"] = "3" * 40
    with pytest.raises(TurnHandoffError, match="STATE_CHANGED"):
        ledger.record_intent_checked(
            expected_state=stale, operator_approved=True)
    assert ledger.state.path.read_bytes() == before

    unknown = ledger.record_intent_checked(
        expected_state=ready, operator_approved=True)
    assert unknown["phase"] == "SUBMISSION_UNKNOWN"
    assert ledger.read() == unknown
    written = ledger.state.path.read_bytes()
    with pytest.raises(TurnHandoffError, match="STATE_CHANGED"):
        ledger.record_intent_checked(
            expected_state=ready, operator_approved=True)
    assert ledger.state.path.read_bytes() == written


def test_checked_intent_racing_same_snapshot_only_one_wins(ledger):
    ready = new(ledger)

    def attempt(_):
        try:
            ledger.record_intent_checked(
                expected_state=ready, operator_approved=True)
            return "OK"
        except TurnHandoffError:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sorted(outcomes) == ["BLOCKED", "OK"]
    assert ledger.read()["phase"] == "SUBMISSION_UNKNOWN"


def test_private_store_human_reconciles_three_distinct_turns(ledger):
    new(ledger)
    for i in range(3):
        ledger.record_intent(operator_approved=True)
        state = ledger.observe_reply(
            issue_comment_ref=(
                "https://github.com/trung-via/ai-commerce/issues/181#issuecomment-"
                + str(100 + i)),
            operator_verified=True)
        assert state["turn_index"] == i + 1
    assert ledger.read()["phase"] == "COMPLETE_UI_ONLY"
    with pytest.raises(TurnHandoffError, match="NOT_READY"):
        ledger.record_intent(operator_approved=True)


def test_private_store_bad_evidence_never_advances(ledger):
    new(ledger)
    ledger.record_intent(operator_approved=True)
    before = ledger.state.path.read_bytes()
    with pytest.raises(TurnHandoffError, match="EVIDENCE_INVALID"):
        ledger.observe_reply(
            issue_comment_ref="https://example.com/secret",
            operator_verified=True)
    assert ledger.state.path.read_bytes() == before


def test_private_store_invalid_location_inside_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    with pytest.raises(ValueError):
        TurnSessionStore(path=checkout / "turn.json", repository_root=checkout)


def test_private_store_rejects_corrupted_saved_state(ledger):
    new(ledger)
    ledger.state.path.write_text('{"phase":"READY"}', encoding="utf-8")
    with pytest.raises(TurnHandoffError, match="SESSION_INVALID"):
        ledger.read()


def test_private_store_intent_cas_concurrent_writers(ledger):
    new(ledger)
    def attempt(_):
        try:
            ledger.record_intent(operator_approved=True)
            return "OK"
        except TurnHandoffError:
            return "BLOCKED"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sorted(outcomes) == ["BLOCKED", "OK"]
    assert ledger.read()["phase"] == "SUBMISSION_UNKNOWN"


def test_private_store_result_is_detached(ledger):
    new(ledger)
    detached = ledger.read()
    detached["evidence"].append({"private": "not_written"})
    assert ledger.read()["evidence"] == []


def test_private_store_no_transport_code():
    import inspect
    import ai_commerce.remote.plugin_turn_store as module
    code = inspect.getsource(module)
    assert "playwright" not in code
    assert "button.click(" not in code
    assert "requests." not in code
    assert "subprocess" not in code
    assert "urlopen" not in code
