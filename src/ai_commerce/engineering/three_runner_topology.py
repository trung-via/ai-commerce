"""Pure plan for the HUMAN-attended, single-account Windows runner rollout.

Source/configuration evidence only: this module neither registers a runner nor
authorizes any new HostOps command, GitHub credential or browser/CDP action.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerSpec:
    name: str
    label: str
    root: str
    task: str


EXISTING_RUNNER = RunnerSpec(
    "AI-COMMERCE-EF02-01", "ai-commerce-ef02",
    r"C:\TOOL\ai-commerce-runner", "AI-Commerce-EF02-Interactive",
)

NEW_RUNNERS = (
    RunnerSpec(
        "AI-COMMERCE-EF02-02", "ai-commerce-ef02",
        r"C:\TOOL\ai-commerce-runner-ef02-02", "AI-Commerce-EF02-02-Interactive",
    ),
    RunnerSpec(
        "AI-COMMERCE-HOSTOPS-01", "ai-commerce-hostops",
        r"C:\TOOL\ai-commerce-runner-hostops-01", "AI-Commerce-HostOps-01-Interactive",
    ),
)


def planned_topology() -> tuple[RunnerSpec, ...]:
    """Three runner instances, not three Windows accounts or machines."""
    return (EXISTING_RUNNER, *NEW_RUNNERS)


def hostops_route_activation_allowed(online_runner_names: set[str]) -> bool:
    """Do not move HostOps YAML before the dedicated runner is proven online."""
    return "AI-COMMERCE-HOSTOPS-01" in online_runner_names


def resource_policy() -> str:
    """Human elected observation first, no CPU/RAM quotas in this release."""
    return "OBSERVE_ONLY_NO_CPU_RAM_LIMITS"


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


@dataclass(frozen=True)
class WakeHubPreflight:
    """Sanitized decision only, never an effect/grant or Wake authorization."""

    code: str
    next_action: str
    cdp_port: int
    listener_count: int
    ready_for_origin_prepare: bool
    start_permitted: bool = False
    wake_send_authorized: bool = False
    effect_attempted: bool = False


def assess_wakehub_preflight(
    *,
    hostops_task: str,
    hostops_listener: str,
    engineering_listeners: tuple[str, str],
    interactive_session_proven: bool,
    chrome_ownership: str,
    source_current: bool,
    unknown_effect: bool,
) -> WakeHubPreflight:
    """Choose a bounded no-effect next action for the single-user Windows host.

    Facts must come from independently authenticated, fresh native receipts.
    A non-running Scheduled Task with a PRESENT runner listener is *not*
    automatically offline. One always-online receiver is required; it cannot
    receive a GitHub event if every runner is offline. The returned action
    strings instruct the future private operator to recheck authority, not
    execute a start/retry/send.
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
    ):
        raise ValueError("WAKEHUB_PREFLIGHT_INPUT_INVALID")

    count = sum(v == "PRESENT" for v in
                (hostops_listener, *engineering_listeners))

    def result(code: str, action: str, ready: bool = False) -> WakeHubPreflight:
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
