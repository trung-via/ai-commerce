"""WakeHub-only pure lifecycle preflight; no host, browser, provider or WP effects.

This is a source-only extraction of the independent WH-01A supervisor logic from
the later Engineering topology. The pre-WP Engineering topology remains intact.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# WakeHub WH-01A: source-only lifecycle decision; no OS, IPC, browser or
# scheduler operation is reachable from this module. Never treat a GitHub
# read receipt as independent proof of Chrome profile ownership.
_RUNNER_TASK_STATES = frozenset({
    "RUNNING", "READY", "QUEUED", "DISABLED", "NOT_REGISTERED", "UNAVAILABLE",
})
_LISTENER_STATES = frozenset({"PRESENT", "ABSENT", "UNAVAILABLE"})
_CHROME_STATES = frozenset({
    "OWNED_CDP_9444", "ABSENT_INDEPENDENTLY_VERIFIED",
    "UNVERIFIED", "PROFILE_CONFLICT",
})

WakeHubDecisionCode = Literal[
    "HOLD_UNKNOWN_EFFECT", "HOLD_SOURCE_DRIFT",
    "HOLD_EVENT_RECEIVER_UNAVAILABLE", "PAUSED_BY_HUMAN",
    "WAIT_INTERACTIVE_SESSION", "HOLD_CHROME_OWNERSHIP",
    "WAIT_PRIVATE_CHROME_START_GRANT", "READY_DEGRADED_ENGINEERING",
    "READY_FOR_ORIGIN_PREPARE",
]
WakeHubNextAction = Literal[
    "RECONCILE_ONLY", "REFRESH_SOURCE_EVIDENCE",
    "RESTORE_RECEIVER_WITH_SEPARATE_NATIVE_AUTHORITY", "NO_EFFECT_KEEP_STOPPED",
    "WAIT_FOR_OWNER_LOGON_NO_BROWSER_ACTION", "VERIFY_EXACT_OWNER_AND_PROFILE",
    "REQUEST_BOUNDED_START_AUTHORITY",
    "REUSE_9444_AND_REPORT_ENGINEERING_LISTENERS", "REUSE_9444_WITHOUT_RELAUNCH",
]


@dataclass(frozen=True)
class WakeHubPreflight:
    """Sanitized decision only, never an effect/grant or Wake authorization."""

    code: WakeHubDecisionCode
    next_action: WakeHubNextAction
    cdp_port: Literal[9444]
    listener_count: int
    ready_for_origin_prepare: bool
    start_permitted: Literal[False] = False
    wake_send_authorized: Literal[False] = False
    effect_attempted: Literal[False] = False


def assess_wakehub_preflight(
    *,
    hostops_task: str,
    hostops_listener: str,
    engineering_listeners: tuple[str, str],
    interactive_session_proven: bool,
    chrome_ownership: str,
    source_current: bool,
    unknown_effect: bool,
    trusted_human_pause: bool = False,
) -> WakeHubPreflight:
    """Choose a bounded no-effect next action for the single-user Windows host.

    Facts must come from independently authenticated, fresh native receipts.
    A non-running Scheduled Task with a PRESENT runner listener is *not*
    automatically offline. One always-online receiver is required; it cannot
    receive a GitHub event if every runner is offline. The returned action
    strings instruct the future private operator to recheck authority, not
    execute a start/retry/send.

    trusted_human_pause is a boolean supplied by a separately trusted private
    operator after authenticating Human intent. This function does not verify
    or sign a pause token, or authenticate a GitHub Issue self-assertion. True
    preserves an intentional stop even with independently absent Chrome;
    False (the backward-compatible default) only releases this pause gate and
    never grants native start authority. Pause proves no session, ownership,
    private enrollment, installed logon task or successful logon.

    interactive_session_proven requires the exact authenticated owner's usable
    interactive desktop; wrong, locked, absent or ambiguous sessions are False.
    OWNED_CDP_9444 requires fresh independent process/session/profile ownership
    evidence, not merely a listening port or a NOT_RUNNING probe receipt.
    """
    if (
        type(hostops_task) is not str or hostops_task not in _RUNNER_TASK_STATES
        or type(hostops_listener) is not str
        or hostops_listener not in _LISTENER_STATES
        or type(engineering_listeners) is not tuple
        or len(engineering_listeners) != 2
        or any(type(v) is not str or v not in _LISTENER_STATES
               for v in engineering_listeners)
        or type(interactive_session_proven) is not bool
        or type(chrome_ownership) is not str
        or chrome_ownership not in _CHROME_STATES
        or type(source_current) is not bool
        or type(unknown_effect) is not bool
        or type(trusted_human_pause) is not bool
    ):
        raise ValueError("WAKEHUB_PREFLIGHT_INPUT_INVALID")

    count = sum(v == "PRESENT" for v in
                (hostops_listener, *engineering_listeners))

    def result(code: WakeHubDecisionCode, action: WakeHubNextAction,
               ready: bool = False) -> WakeHubPreflight:
        return WakeHubPreflight(
            code=code, next_action=action, cdp_port=9444,
            listener_count=count, ready_for_origin_prepare=ready,
        )

    # An effect may have occurred before a lost receipt. Never infer that a
    # new event, Chrome instance or Send would be a safe replay.
    if unknown_effect:
        return result("HOLD_UNKNOWN_EFFECT", "RECONCILE_ONLY")
    if not source_current:
        return result("HOLD_SOURCE_DRIFT", "REFRESH_SOURCE_EVIDENCE")
    if hostops_listener != "PRESENT":
        return result("HOLD_EVENT_RECEIVER_UNAVAILABLE",
                      "RESTORE_RECEIVER_WITH_SEPARATE_NATIVE_AUTHORITY")
    # Pause cannot reconcile UNKNOWN, refresh source or restore the event
    # receiver, so those HOLDs take precedence. Keep-stop advice needs no
    # desktop/browser authority and must precede every reuse/start suggestion.
    if trusted_human_pause:
        return result("PAUSED_BY_HUMAN", "NO_EFFECT_KEEP_STOPPED")
    if not interactive_session_proven:
        return result("WAIT_INTERACTIVE_SESSION",
                      "WAIT_FOR_OWNER_LOGON_NO_BROWSER_ACTION")
    if chrome_ownership in {"PROFILE_CONFLICT", "UNVERIFIED"}:
        return result("HOLD_CHROME_OWNERSHIP",
                      "VERIFY_EXACT_OWNER_AND_PROFILE")
    if chrome_ownership == "ABSENT_INDEPENDENTLY_VERIFIED":
        return result("WAIT_PRIVATE_CHROME_START_GRANT",
                      "REQUEST_BOUNDED_START_AUTHORITY")

    # Existing owned 9444 browser is reusable without a new instance. The
    # HostOps listener is enough for a Send-only pipeline, but offline
    # Engineering runners must remain visible as degraded status.
    if "PRESENT" not in engineering_listeners:
        return result("READY_DEGRADED_ENGINEERING",
                      "REUSE_9444_AND_REPORT_ENGINEERING_LISTENERS", True)
    return result("READY_FOR_ORIGIN_PREPARE",
                  "REUSE_9444_WITHOUT_RELAUNCH", True)
