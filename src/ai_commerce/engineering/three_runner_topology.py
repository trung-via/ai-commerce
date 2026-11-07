"""Pure plan for the HUMAN-attended, single-account Windows runner rollout.

Source/configuration evidence only: this module neither registers a runner nor
authorizes any new HostOps command, GitHub credential or browser/CDP action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Literal


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


# WakeHub WH-02A is independent of WH-01A and any previous UNKNOWN effect.
# These are schema/representation bounds, never host resource limits, a
# sampling schedule, readiness targets or an automatic lifecycle policy.
WAKEHUB_MAX_SAMPLES = 4096
WAKEHUB_MAX_PIDS_PER_SAMPLE = 4096
_MAX_EXACT_INTEGER = 2**53 - 1
_MAX_WINDOWS_COUNT = 2**32 - 1
_IDLE_BASELINE_SECONDS = 30 * 60


class WakeHubCoreInputError(ValueError):
    """Closed-schema failure; the diagnostic never includes caller data."""

    def __init__(self) -> None:
        super().__init__("WAKEHUB_CORE_INPUT_INVALID")


class WakeHubTaskState(str, Enum):
    RUNNING = "RUNNING"
    READY = "READY"
    QUEUED = "QUEUED"
    DISABLED = "DISABLED"
    NOT_REGISTERED = "NOT_REGISTERED"
    UNAVAILABLE = "UNAVAILABLE"


class WakeHubListenerState(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"


class WakeHubChromeOwnership(str, Enum):
    OWNED_CDP_9444 = "OWNED_CDP_9444"
    ABSENT_INDEPENDENTLY_VERIFIED = "ABSENT_INDEPENDENTLY_VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    PROFILE_CONFLICT = "PROFILE_CONFLICT"


class WakeHubWorkState(str, Enum):
    CLEAR = "CLEAR"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class WakeHubObservationState(str, Enum):
    NOT_MEASURED = "NOT_MEASURED"
    UNVERIFIED = "UNVERIFIED"
    INDEPENDENTLY_OBSERVED = "INDEPENDENTLY_OBSERVED"


class WakeHubMeasurementCode(str, Enum):
    NOT_MEASURED = "NOT_MEASURED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    MEASURED_SAMPLES_COMPLETE = "MEASURED_SAMPLES_COMPLETE"


class WakeHubCoreIdleCode(str, Enum):
    HOLD_UNKNOWN_EFFECT = "HOLD_UNKNOWN_EFFECT"
    HOLD_SOURCE_DRIFT = "HOLD_SOURCE_DRIFT"
    WAIT_RECEIVER = "WAIT_RECEIVER"
    PAUSED_BY_HUMAN = "PAUSED_BY_HUMAN"
    KEEP_ACTIVE_PENDING = "KEEP_ACTIVE_PENDING"
    WAIT_INTERACTIVE_SESSION = "WAIT_INTERACTIVE_SESSION"
    HOLD_CHROME_OWNERSHIP = "HOLD_CHROME_OWNERSHIP"
    WAIT_PRIVATE_CHROME_START_GRANT = "WAIT_PRIVATE_CHROME_START_GRANT"
    WAIT_MEASUREMENT = "WAIT_MEASUREMENT"
    CORE_LOGICAL_IDLE_ELIGIBLE = "CORE_LOGICAL_IDLE_ELIGIBLE"


@dataclass(frozen=True, slots=True)
class WakeHubIdleSample:
    """Sanitized host totals at a relative observation time; no process paths."""

    elapsed_seconds: int | float
    cpu_percent: int | float  # Normalized whole-host CPU, in [0, 100].
    ram_bytes: int
    handles: int
    pids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WakeHubReadinessSample:
    """One observed readiness duration; warm/cold streams have separate clocks."""

    elapsed_seconds: int | float
    duration_ms: int | float


@dataclass(frozen=True, slots=True)
class WakeHubBaseline:
    """Caller-attested observations, not authentication performed by this module.

    A trusted independent native observer must attest the continuous idle
    window and actual CPU/RAM/handle/PID and warm/cold readiness samples.
    Timestamps alone cannot prove that the host stayed idle between samples.
    No raw receipt, identity, secret, executable path or command is accepted.
    """

    observation: WakeHubObservationState
    continuous_idle_window_proven: bool = False
    idle_samples: tuple[WakeHubIdleSample, ...] = ()
    warm_samples: tuple[WakeHubReadinessSample, ...] = ()
    cold_samples: tuple[WakeHubReadinessSample, ...] = ()


@dataclass(frozen=True, slots=True)
class WakeHubNoEffectPlan:
    """Fixed non-authority on every WH-02A result, including measurement plans."""

    resource_mode: Literal["OBSERVE_ONLY_NO_CPU_RAM_LIMITS"] = field(
        default="OBSERVE_ONLY_NO_CPU_RAM_LIMITS", init=False)
    effect_attempted: Literal[False] = field(default=False, init=False)
    start_permitted: Literal[False] = field(default=False, init=False)
    stop_permitted: Literal[False] = field(default=False, init=False)
    kill_permitted: Literal[False] = field(default=False, init=False)
    wake_send_authorized: Literal[False] = field(default=False, init=False)
    chrome_operation_authorized: Literal[False] = field(default=False, init=False)
    native_operation_authorized: Literal[False] = field(default=False, init=False)
    receiver_disable_authorized: Literal[False] = field(default=False, init=False)
    native_owner_proof: Literal["UNRESOLVED"] = field(
        default="UNRESOLVED", init=False)
    savings: Literal["NOT_MEASURED"] = field(default="NOT_MEASURED", init=False)


@dataclass(frozen=True, slots=True)
class WakeHubReadinessPercentiles:
    sample_count: int
    p50_ms: int | float
    p95_ms: int | float
    method: Literal["EMPIRICAL_NEAREST_RANK"] = field(
        default="EMPIRICAL_NEAREST_RANK", init=False)


@dataclass(frozen=True, slots=True)
class WakeHubMeasurementPlan(WakeHubNoEffectPlan):
    code: WakeHubMeasurementCode
    idle_sample_count: int | None = None
    idle_duration_seconds: int | float | None = None
    warm: WakeHubReadinessPercentiles | None = None
    cold: WakeHubReadinessPercentiles | None = None


def _wakehub_enum(value: object, expected: type[Enum]) -> bool:
    return type(value) is expected and any(value is member for member in expected)


def _wakehub_number(value: object, upper: int) -> None:
    # Check type and integer bounds before isfinite: bool, numeric subclasses
    # and enormous integers must not reach coercion, overflow or user code.
    if (type(value) not in (int, float) or not 0 <= value <= upper
            or not isfinite(value)):
        raise WakeHubCoreInputError()


def _wakehub_samples(samples: object, *, idle: bool) -> None:
    if type(samples) is not tuple or len(samples) > WAKEHUB_MAX_SAMPLES:
        raise WakeHubCoreInputError()
    previous = -1
    for sample in samples:
        expected = WakeHubIdleSample if idle else WakeHubReadinessSample
        if type(sample) is not expected:
            raise WakeHubCoreInputError()
        _wakehub_number(sample.elapsed_seconds, _MAX_EXACT_INTEGER)
        if sample.elapsed_seconds <= previous:
            raise WakeHubCoreInputError()
        previous = sample.elapsed_seconds
        if not idle:
            _wakehub_number(sample.duration_ms, _MAX_EXACT_INTEGER)
            continue
        _wakehub_number(sample.cpu_percent, 100)
        if (type(sample.ram_bytes) is not int
                or type(sample.handles) is not int
                or type(sample.pids) is not tuple
                or not 1 <= len(sample.pids) <= WAKEHUB_MAX_PIDS_PER_SAMPLE):
            raise WakeHubCoreInputError()
        _wakehub_number(sample.ram_bytes, _MAX_EXACT_INTEGER)
        _wakehub_number(sample.handles, _MAX_WINDOWS_COUNT)
        for pid in sample.pids:
            if type(pid) is not int or not 1 <= pid <= _MAX_WINDOWS_COUNT:
                raise WakeHubCoreInputError()
        if len(set(sample.pids)) != len(sample.pids):
            raise WakeHubCoreInputError()


def _wakehub_readiness_percentiles(
    samples: tuple[WakeHubReadinessSample, ...],
) -> WakeHubReadinessPercentiles:
    # Empirical inverse-CDF: rank = ceil(p * n). Values are observed samples,
    # never extrapolated population estimates or readiness acceptance targets.
    values = sorted(sample.duration_ms for sample in samples)
    count = len(values)
    return WakeHubReadinessPercentiles(
        count, values[(count + 1) // 2 - 1],
        values[(95 * count + 99) // 100 - 1])


def plan_wakehub_measurements(baseline: WakeHubBaseline) -> WakeHubMeasurementPlan:
    """Validate bounded supplied samples; never probe a host or invent evidence.

    Completeness requires independently attested continuous idle observations
    spanning at least 30 minutes, all four host metrics, and both readiness
    streams. No sample cadence, minimum statistical population or performance
    threshold is prescribed here. Counts/method are explicit; sample p50/p95
    prove no population accuracy, savings, WH-01B deployment, reboot acceptance
    or future native owner authority. Incomplete/untrusted data has no numeric
    summary. No data is NOT_MEASURED, including when no native observer exists.
    """
    if (type(baseline) is not WakeHubBaseline
            or not _wakehub_enum(baseline.observation, WakeHubObservationState)
            or type(baseline.continuous_idle_window_proven) is not bool):
        raise WakeHubCoreInputError()
    _wakehub_samples(baseline.idle_samples, idle=True)
    _wakehub_samples(baseline.warm_samples, idle=False)
    _wakehub_samples(baseline.cold_samples, idle=False)
    has_data = bool(baseline.idle_samples or baseline.warm_samples
                    or baseline.cold_samples)
    if baseline.observation is WakeHubObservationState.NOT_MEASURED and (
        has_data or baseline.continuous_idle_window_proven
    ):
        raise WakeHubCoreInputError()
    if not has_data:
        return WakeHubMeasurementPlan(WakeHubMeasurementCode.NOT_MEASURED)
    if (baseline.observation is not WakeHubObservationState.INDEPENDENTLY_OBSERVED
            or not baseline.continuous_idle_window_proven
            or len(baseline.idle_samples) < 2
            or baseline.idle_samples[-1].elapsed_seconds
            - baseline.idle_samples[0].elapsed_seconds < _IDLE_BASELINE_SECONDS
            or not baseline.warm_samples or not baseline.cold_samples):
        return WakeHubMeasurementPlan(WakeHubMeasurementCode.INSUFFICIENT_EVIDENCE)
    return WakeHubMeasurementPlan(
        WakeHubMeasurementCode.MEASURED_SAMPLES_COMPLETE,
        len(baseline.idle_samples),
        baseline.idle_samples[-1].elapsed_seconds
        - baseline.idle_samples[0].elapsed_seconds,
        _wakehub_readiness_percentiles(baseline.warm_samples),
        _wakehub_readiness_percentiles(baseline.cold_samples),
    )


@dataclass(frozen=True, slots=True)
class WakeHubCoreIdleEvidence:
    """Fresh sanitized facts supplied by a separately trusted private observer.

    PRESENT means an independently verified real listener, never task READY.
    source_current requires fresh evidence for the exact approved source.
    Owner desktop and 9444 ownership have the same independent meaning as in
    WH-01A; none of these booleans authenticates a public Issue assertion.
    """

    source_current: bool
    unknown_effect: bool
    lost_effect_receipt: bool
    hostops_task: WakeHubTaskState
    hostops_listener: WakeHubListenerState
    engineering_listeners: tuple[WakeHubListenerState, WakeHubListenerState]
    queue_state: WakeHubWorkState
    pending_work: WakeHubWorkState
    outstanding_send: WakeHubWorkState
    outstanding_signer: WakeHubWorkState
    outstanding_lease: WakeHubWorkState
    pending_wake: WakeHubWorkState
    trusted_human_pause: bool
    interactive_session_proven: bool
    chrome_ownership: WakeHubChromeOwnership
    baseline: WakeHubBaseline


@dataclass(frozen=True, slots=True)
class WakeHubCoreIdlePlan(WakeHubNoEffectPlan):
    code: WakeHubCoreIdleCode
    measurements: WakeHubMeasurementPlan
    listener_count: int
    receiver_present: bool
    desktop_ready_for_planning: bool
    core_logical_idle_eligible: bool
    paused_by_human: bool
    keep_event_receiver_alive: Literal[True] = field(default=True, init=False)
    idle_scope: Literal["HEADLESS_LOGICAL_CORE_ONLY"] = field(
        default="HEADLESS_LOGICAL_CORE_ONLY", init=False)


def assess_wakehub_core_idle(evidence: WakeHubCoreIdleEvidence) -> WakeHubCoreIdlePlan:
    """Plan logical-core eligibility only, with no native operation reachable.

    Inbox retention is independent of desktop readiness and is required on
    every return, including absent/locked owner sessions and Human pause.
    An offline HostOps receiver cannot wake itself; Engineering listeners do
    not substitute for it and are never recommended for shutdown. Core idle
    requires fresh exact source, no unknown effect or lost receipt, and every
    work/queue/send/signer/lease/wake state CLEAR with no queued HostOps task.
    Desktop/9444 readiness, measurement completeness and Human pause are
    separate results and cannot gate headless logical-core eligibility.

    paused_by_human preserves trusted stop intent even on safety HOLDs and
    suppresses desktop preparation. It never disables the event receiver or
    authorizes startup/Send. Missing measurements publish no numeric summary;
    neither measurement nor logical eligibility authorizes resource actions.

    Eligible permits no Windows sleep, runner unload, task reconfiguration,
    Playwright attach/detach or browser shutdown. WH-01B deployment, private
    native owner proof and real baseline acceptance remain separate work.
    """
    if type(evidence) is not WakeHubCoreIdleEvidence:
        raise WakeHubCoreInputError()
    if (any(type(value) is not bool for value in (
        evidence.source_current, evidence.unknown_effect,
        evidence.lost_effect_receipt, evidence.trusted_human_pause,
        evidence.interactive_session_proven,
    )) or not _wakehub_enum(evidence.hostops_task, WakeHubTaskState)
            or not _wakehub_enum(evidence.hostops_listener, WakeHubListenerState)
            or type(evidence.engineering_listeners) is not tuple
            or len(evidence.engineering_listeners) != 2
            or any(not _wakehub_enum(value, WakeHubListenerState)
                   for value in evidence.engineering_listeners)
            or not _wakehub_enum(evidence.chrome_ownership, WakeHubChromeOwnership)):
        raise WakeHubCoreInputError()
    work = (evidence.queue_state, evidence.pending_work, evidence.outstanding_send,
            evidence.outstanding_signer, evidence.outstanding_lease,
            evidence.pending_wake)
    if any(not _wakehub_enum(value, WakeHubWorkState) for value in work):
        raise WakeHubCoreInputError()
    measurements = plan_wakehub_measurements(evidence.baseline)
    receiver_present = evidence.hostops_listener is WakeHubListenerState.PRESENT
    desktop_ready = (not evidence.trusted_human_pause
                     and evidence.interactive_session_proven
                     and evidence.chrome_ownership
                     is WakeHubChromeOwnership.OWNED_CDP_9444)
    if (evidence.unknown_effect or evidence.lost_effect_receipt
            or WakeHubWorkState.UNKNOWN in work):
        code = WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT
    elif not evidence.source_current:
        code = WakeHubCoreIdleCode.HOLD_SOURCE_DRIFT
    elif not receiver_present:
        code = WakeHubCoreIdleCode.WAIT_RECEIVER
    elif (WakeHubWorkState.PENDING in work
          or evidence.hostops_task is WakeHubTaskState.QUEUED):
        code = WakeHubCoreIdleCode.KEEP_ACTIVE_PENDING
    else:
        code = WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE
    return WakeHubCoreIdlePlan(
        code=code, measurements=measurements,
        listener_count=sum(value is WakeHubListenerState.PRESENT for value in (
            evidence.hostops_listener, *evidence.engineering_listeners)),
        receiver_present=receiver_present, desktop_ready_for_planning=desktop_ready,
        core_logical_idle_eligible=(code is WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
        paused_by_human=evidence.trusted_human_pause,
    )
