"""Pure D5 explicit-turn ledger, no network, browser or Send transports."""
from copy import deepcopy
import pytest

from ai_commerce.remote.plugin_turn_handoff import (
    TurnHandoffError, new_session, preview_next_turn, record_send_intent,
    reconcile_ui_observation, validate_session,
)

ID = "d5-turn-" + "a" * 32
SHA = "b" * 40
REFS = [f"https://github.com/trung-via/ai-commerce/issues/181#issuecomment-{n}"
        for n in (123, 124, 125)]


def new():
    return new_session(handoff_id=ID, issue_number=181, source_sha=SHA)


def test_initial_session_is_pure_ready_and_private_free():
    state = new()
    assert state["phase"] == "READY" and state["turn_index"] == 0
    assert "chatgpt.com" not in str(state)
    assert "cdp_endpoint" not in state
    assert preview_next_turn(state)["plugin_alias_for_operator_selection"] == "AI Commerce Brain"
    assert preview_next_turn(state)["send_authorized"] is False


def test_three_distinct_roles_and_only_ui_level_complete():
    state = new()
    for index, role in enumerate(("BRAIN", "ENGINEERING", "BRAIN")):
        preview = preview_next_turn(state)
        assert (preview["turn_index"], preview["role"]) == (index, role)
        assert preview["host_selection_proven"] is False
        sent = record_send_intent(state, operator_approved=True)
        assert sent["phase"] == "SUBMISSION_UNKNOWN"
        assert sent["intent_fingerprint"] and len(sent["intent_fingerprint"]) == 64
        state = reconcile_ui_observation(sent, issue_comment_ref=REFS[index],
                                         operator_verified=True)
    assert state["phase"] == "COMPLETE_UI_ONLY"
    assert len(state["evidence"]) == 3
    assert all(e["observation"] == "OPERATOR_UI_RESPONSE_OBSERVED" for e in state["evidence"])
    with pytest.raises(TurnHandoffError, match="NOT_READY"):
        preview_next_turn(state)


def test_requires_literal_operator_approval():
    state = new()
    for value in (False, None, 1, "true"):
        with pytest.raises(TurnHandoffError, match="APPROVAL_REQUIRED"):
            record_send_intent(state, operator_approved=value)
    assert state["phase"] == "READY"


def test_unknown_submission_can_never_auto_retry_or_repreview():
    state = record_send_intent(new(), operator_approved=True)
    for action in (lambda: preview_next_turn(state),
                   lambda: record_send_intent(state, operator_approved=True)):
        with pytest.raises(TurnHandoffError, match="NOT_READY"):
            action()
    assert state["phase"] == "SUBMISSION_UNKNOWN"


def test_reconcile_requires_human_observation_and_never_mutates_caller():
    state = record_send_intent(new(), operator_approved=True)
    snap = deepcopy(state)
    with pytest.raises(TurnHandoffError, match="APPROVAL_REQUIRED"):
        reconcile_ui_observation(state, issue_comment_ref=REFS[0])
    assert state == snap
    new_state = reconcile_ui_observation(state, issue_comment_ref=REFS[0], operator_verified=True)
    assert state == snap and new_state["turn_index"] == 1


@pytest.mark.parametrize("ref", [
    "https://chatgpt.com/c/test",
    "https://github.com/trung-via/ai-commerce/issues/180#issuecomment-100",
    "https://github.com/trung-via/ai-commerce/issues/181#issuecomment-0",
    "https://github.com/trung-via/ai-commerce/issues/181#issuecomment-123?token=x",
    "https://github.com/attacker/repo/issues/181#issuecomment-123",
    "opaque-private-route",
])
def test_reconcile_rejects_untrusted_or_private_evidence_ref(ref):
    state = record_send_intent(new(), operator_approved=True)
    with pytest.raises(TurnHandoffError, match="EVIDENCE_INVALID"):
        reconcile_ui_observation(state, issue_comment_ref=ref, operator_verified=True)


@pytest.mark.parametrize("id_,issue,sha", [
    ("wrong", 181, SHA), (ID, True, SHA), (ID, 0, SHA),
    (ID, 181, "x" * 40), (ID, 181, "too-short"),
])
def test_invalid_envelope_is_rejected(id_, issue, sha):
    with pytest.raises(TurnHandoffError, match="SESSION_INVALID"):
        new_session(handoff_id=id_, issue_number=issue, source_sha=sha)


def test_extra_private_fields_and_forged_proof_are_rejected():
    state = new()
    for k, v in (("chat_url", "https://chatgpt.com/c/private"),
                 ("host_selection_proven", True)):
        forged = deepcopy(state)
        forged[k] = v
        with pytest.raises(TurnHandoffError, match="SESSION_INVALID"):
            validate_session(forged)


def test_state_fingerprint_and_evidence_cannot_be_forged_or_skipped():
    state = record_send_intent(new(), operator_approved=True)
    for key, val in (("turn_index", 1), ("phase", "READY"),
                     ("intent_fingerprint", "0" * 64)):
        forged = deepcopy(state)
        forged[key] = val
        with pytest.raises(TurnHandoffError, match="SESSION_INVALID"):
            validate_session(forged)
    forged = deepcopy(new())
    forged["turn_index"] = 2
    with pytest.raises(TurnHandoffError, match="SESSION_INVALID"):
        validate_session(forged)


def test_module_is_non_delivering():
    import inspect
    import ai_commerce.remote.plugin_turn_handoff as module
    src = inspect.getsource(module)
    assert "playwright" not in src.lower()
    assert "requests." not in src
    assert "button.click(" not in src
    assert 'press("Enter")' not in src
    assert "subprocess" not in src
    assert "urlopen" not in src
