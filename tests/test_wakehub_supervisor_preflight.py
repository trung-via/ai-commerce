"""WakeHub-owned source-only supervisor regression; no native host authority."""
import pytest
from ai_commerce.wakehub.supervisor_preflight import (
    WakeHubPreflight, assess_wakehub_preflight,
)


def sample(**changes):
    inputs = dict(
        hostops_task="RUNNING",
        hostops_listener="PRESENT",
        engineering_listeners=("PRESENT", "ABSENT"),
        interactive_session_proven=True,
        chrome_ownership="OWNED_CDP_9444",
        source_current=True,
        unknown_effect=False,
        trusted_human_pause=False,
    )
    inputs.update(changes)
    return assess_wakehub_preflight(**inputs)


def test_wakehub_supervisor_is_standalone_and_no_effect():
    state = sample()
    assert type(state) is WakeHubPreflight
    assert WakeHubPreflight.__module__ == "ai_commerce.wakehub.supervisor_preflight"
    assert (state.code, state.next_action) == (
        "READY_FOR_ORIGIN_PREPARE", "REUSE_9444_WITHOUT_RELAUNCH")
    assert state.cdp_port == 9444
    assert state.ready_for_origin_prepare is True
    assert state.start_permitted is False
    assert state.wake_send_authorized is False
    assert state.effect_attempted is False


@pytest.mark.parametrize("changes,expected", [
    ({"unknown_effect": True}, "HOLD_UNKNOWN_EFFECT"),
    ({"source_current": False}, "HOLD_SOURCE_DRIFT"),
    ({"hostops_listener": "ABSENT"}, "HOLD_EVENT_RECEIVER_UNAVAILABLE"),
    ({"trusted_human_pause": True}, "PAUSED_BY_HUMAN"),
    ({"interactive_session_proven": False}, "WAIT_INTERACTIVE_SESSION"),
    ({"chrome_ownership": "UNVERIFIED"}, "HOLD_CHROME_OWNERSHIP"),
    ({"chrome_ownership": "ABSENT_INDEPENDENTLY_VERIFIED"}, "WAIT_PRIVATE_CHROME_START_GRANT"),
    ({"engineering_listeners": ("ABSENT", "ABSENT")}, "READY_DEGRADED_ENGINEERING"),
])
def test_wakehub_preflight_precedence_and_no_effect(changes, expected):
    state = sample(**changes)
    assert state.code == expected
    assert state.start_permitted is False
    assert state.wake_send_authorized is False
    assert state.effect_attempted is False


@pytest.mark.parametrize("changes", [
    {"engineering_listeners": ("PRESENT",)},
    {"hostops_listener": "UNKNOWN"},
    {"chrome_ownership": "localhost:9444"},
    {"unknown_effect": "false"},
    {"interactive_session_proven": 1},
])
def test_wakehub_preflight_rejects_untrusted_inputs(changes):
    with pytest.raises(ValueError, match="WAKEHUB_PREFLIGHT_INPUT_INVALID"):
        sample(**changes)
