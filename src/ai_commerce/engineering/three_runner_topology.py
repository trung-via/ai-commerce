"""Pure plan for the HUMAN-attended, single-account Windows runner rollout.

Source/configuration evidence only: this module neither registers a runner nor
authorizes any new HostOps command, GitHub credential or browser/CDP action.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
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


# WH-04A is a pure policy over PRIVATE composition-root projections. These
# types neither implement the WH-03 origin issuer nor compose the WH-04B
# runtime. Constructing a CURRENT member authenticates nothing outside Python.
class WakeHubSmartInputError(ValueError):
    """Closed-schema diagnostic without caller values or private identifiers."""

    def __init__(self) -> None:
        super().__init__("WAKEHUB_SMART_READINESS_INPUT_INVALID")


class WakeHubProofState(str, Enum):
    MISSING = "MISSING"
    INDEPENDENTLY_CURRENT = "INDEPENDENTLY_CURRENT"
    STALE = "STALE"
    MISMATCH = "MISMATCH"
    AMBIGUOUS = "AMBIGUOUS"


class WakeHubDesktopState(str, Enum):
    ABSENT = "ABSENT"
    LOCKED = "LOCKED"
    SESSION_ZERO = "SESSION_ZERO"
    OWNER_INTERACTIVE = "OWNER_INTERACTIVE"


class WakeHubEndpointHost(str, Enum):
    IPV4_LOOPBACK = "IPV4_LOOPBACK"  # Exactly 127.0.0.1, not a hostname/range.
    IPV6_LOOPBACK = "IPV6_LOOPBACK"
    OTHER = "OTHER"


class WakeHubEventState(str, Enum):
    UNIQUE_CURRENT = "UNIQUE_CURRENT"
    ABSENT = "ABSENT"
    STALE = "STALE"
    DUPLICATE_AMBIGUOUS = "DUPLICATE_AMBIGUOUS"


class WakeHubConsentState(str, Enum):
    CURRENT = "CURRENT"
    ABSENT = "ABSENT"
    REVOKED = "REVOKED"
    STALE = "STALE"


class WakeHubSmartCoreStatus(str, Enum):
    HOLD_UNKNOWN_EFFECT = "HOLD_UNKNOWN_EFFECT"
    HOLD_SOURCE_DRIFT = "HOLD_SOURCE_DRIFT"
    HOLD_PRIVATE_PROJECTION = "HOLD_PRIVATE_PROJECTION"
    WAIT_RECEIVER = "WAIT_RECEIVER"
    WAIT_EVENT = "WAIT_EVENT"
    HOLD_EVENT_STALE = "HOLD_EVENT_STALE"
    HOLD_EVENT_AMBIGUITY = "HOLD_EVENT_AMBIGUITY"
    WAIT_PENDING_WORK = "WAIT_PENDING_WORK"
    CORE_RECEIVE_IDLE_HEALTHY = "CORE_RECEIVE_IDLE_HEALTHY"


class WakeHubSmartReadinessCode(str, Enum):
    HOLD_UNKNOWN_EFFECT = "HOLD_UNKNOWN_EFFECT"
    HOLD_SOURCE_DRIFT = "HOLD_SOURCE_DRIFT"
    HOLD_PRIVATE_PROJECTION = "HOLD_PRIVATE_PROJECTION"
    WAIT_RECEIVER = "WAIT_RECEIVER"
    WAIT_EVENT = "WAIT_EVENT"
    HOLD_EVENT_STALE = "HOLD_EVENT_STALE"
    HOLD_EVENT_AMBIGUITY = "HOLD_EVENT_AMBIGUITY"
    WAIT_PENDING_WORK = "WAIT_PENDING_WORK"
    HOLD_HUMAN_PAUSED = "HOLD_HUMAN_PAUSED"
    WAIT_INTERACTIVE_SESSION = "WAIT_INTERACTIVE_SESSION"
    HOLD_SESSION_OWNER = "HOLD_SESSION_OWNER"
    WAIT_CHROME = "WAIT_CHROME"
    HOLD_CHROME_OWNERSHIP = "HOLD_CHROME_OWNERSHIP"
    HOLD_CHROME_ENDPOINT = "HOLD_CHROME_ENDPOINT"
    HOLD_CHROME_IDENTITY = "HOLD_CHROME_IDENTITY"
    HOLD_BROWSER_ACCOUNT = "HOLD_BROWSER_ACCOUNT"
    HOLD_ORIGIN_ISSUER = "HOLD_ORIGIN_ISSUER"
    HOLD_ORIGIN_OWNER = "HOLD_ORIGIN_OWNER"
    HOLD_ORIGIN_ROUTE = "HOLD_ORIGIN_ROUTE"
    HOLD_ORIGIN_GENERATION = "HOLD_ORIGIN_GENERATION"
    HOLD_ORIGIN_CONSENT = "HOLD_ORIGIN_CONSENT"
    READY_TO_PREPARE_ADVISORY_ONLY = "READY_TO_PREPARE_ADVISORY_ONLY"


class WakeHubBrowserAdvice(str, Enum):
    WAIT = "WAIT"
    REUSE_VERIFIED_OWNER = "REUSE_VERIFIED_OWNER"


class _WakeHubSmartProjection:
    """Required keyword fields only; even unknown-field errors are sanitized.

    This is representation validation, not an authentication boundary. A
    separately authenticated private root must derive these categories from
    independent receipts; public Issue dictionaries cannot establish them.
    """

    __slots__ = ()

    def __init__(self, *args: object, **values: object) -> None:
        schema = fields(self)
        if args or len(values) != len(schema) or any(
            item.name not in values for item in schema
        ):
            raise WakeHubSmartInputError()
        for item in schema:
            object.__setattr__(self, item.name, values[item.name])
        self._validate()


def _wakehub_smart_enums(*pairs: tuple[object, type[Enum]]) -> None:
    if any(not _wakehub_enum(value, expected) for value, expected in pairs):
        raise WakeHubSmartInputError()


@dataclass(frozen=True, slots=True, init=False)
class WakeHubSmartBrowserEvidence(_WakeHubSmartProjection):
    """Sanitized independent browser facts, never paths, argv or credentials.

    pid_creation binds the PID to its exact process creation identity;
    executable/profile/port_owner bind that same process and owner session.
    identity_current requires unchanged, privately current evidence for the
    entire binding. retained_account requires independent authentication of
    the retained account, not merely an open page or a listening port.
    """

    ownership: WakeHubChromeOwnership
    host: WakeHubEndpointHost
    port: int
    pid: int
    pid_creation: WakeHubProofState
    executable: WakeHubProofState
    profile: WakeHubProofState
    port_owner: WakeHubProofState
    identity_current: WakeHubProofState
    retained_account: WakeHubProofState

    def _validate(self) -> None:
        _wakehub_smart_enums(
            (self.ownership, WakeHubChromeOwnership),
            (self.host, WakeHubEndpointHost),
            *((value, WakeHubProofState) for value in (
                self.pid_creation, self.executable, self.profile,
                self.port_owner, self.identity_current, self.retained_account,
            )),
        )
        if (type(self.port) is not int or not 0 <= self.port <= 65535
                or type(self.pid) is not int
                or not 0 <= self.pid <= _MAX_WINDOWS_COUNT):
            raise WakeHubSmartInputError()


@dataclass(frozen=True, slots=True, init=False)
class WakeHubSmartOriginEvidence(_WakeHubSmartProjection):
    """Projection from an independently proven issuer, never a route ticket.

    route requires independently proven uniqueness of originating Chat A,
    excluding copied/ambiguous Chat B. generation must be freshly bound to
    that route, event, owner and consent. No raw route/account/token is held.
    WH-03 owner registration remains external and blocked until proven; no
    constructor, matching integer, enum or self-issued permission proves it.
    """

    event: WakeHubEventState
    issuer: WakeHubProofState
    owner: WakeHubProofState
    route: WakeHubProofState
    generation: WakeHubProofState
    consent: WakeHubConsentState

    def _validate(self) -> None:
        _wakehub_smart_enums(
            (self.event, WakeHubEventState),
            (self.consent, WakeHubConsentState),
            *((value, WakeHubProofState) for value in (
                self.issuer, self.owner, self.route, self.generation,
            )),
        )


@dataclass(frozen=True, slots=True, init=False)
class WakeHubSmartReadinessEvidence(_WakeHubSmartProjection):
    """PRIVATE independently authenticated projection; synthetic in WH-04A.

    projection attests provenance/freshness of the complete composition;
    source binds fresh evidence to the exact independently approved source.
    listeners independently proves the existing three-runner inventory and
    their supplied listener states, including the one always-online HostOps
    receiver. A ScheduledTask READY value is never listener proof.
    session_owner binds the usable desktop and browser to the exact owner.
    No constructor performs these attestations or upgrades public assertions.
    """

    core: WakeHubCoreIdleEvidence
    projection: WakeHubProofState
    source: WakeHubProofState
    listeners: WakeHubProofState
    desktop: WakeHubDesktopState
    session_owner: WakeHubProofState
    browser: WakeHubSmartBrowserEvidence
    origin: WakeHubSmartOriginEvidence

    def _validate(self) -> WakeHubCoreIdlePlan:
        _wakehub_smart_enums(
            (self.desktop, WakeHubDesktopState),
            *((value, WakeHubProofState) for value in (
                self.projection, self.source, self.listeners, self.session_owner,
            )),
        )
        if (type(self.core) is not WakeHubCoreIdleEvidence
                or type(self.browser) is not WakeHubSmartBrowserEvidence
                or type(self.origin) is not WakeHubSmartOriginEvidence):
            raise WakeHubSmartInputError()
        self.browser._validate()
        self.origin._validate()
        try:
            return assess_wakehub_core_idle(self.core)
        except WakeHubCoreInputError:
            raise WakeHubSmartInputError() from None


@dataclass(frozen=True, slots=True)
class WakeHubSmartReadinessPlan(WakeHubNoEffectPlan):
    """Categorical advice only, including READY; never consumable as a grant."""

    code: WakeHubSmartReadinessCode
    core_status: WakeHubSmartCoreStatus
    browser_advice: WakeHubBrowserAdvice
    listener_count: int
    paused_by_human: bool
    scope: Literal["PURE_SYNTHETIC_POLICY_ONLY"] = field(
        default="PURE_SYNTHETIC_POLICY_ONLY", init=False)
    keep_event_receiver_alive: Literal[True] = field(default=True, init=False)
    native_operations: Literal[False] = field(default=False, init=False)
    start: Literal[False] = field(default=False, init=False)
    stop: Literal[False] = field(default=False, init=False)
    task: Literal[False] = field(default=False, init=False)
    chrome: Literal[False] = field(default=False, init=False)
    send: Literal[False] = field(default=False, init=False)
    plugin: Literal[False] = field(default=False, init=False)
    prepare_authorized: Literal[False] = field(default=False, init=False)


def assess_wakehub_smart_readiness(
    evidence: WakeHubSmartReadinessEvidence | None = None, **unknown: object,
) -> WakeHubSmartReadinessPlan:
    """Deterministic WH-04A policy; no probing, clocks, attach, start or Send.

    Validate the entire closed input before selecting a reason. Priority is
    unknown/lost effect, source, private provenance, receiver, event, pending
    work, Human pause, desktop/owner, browser identity/account, then origin
    issuer/owner/route/generation/consent. Missing proof always waits or holds.
    Core receive/idle health is independent of desktop, Chrome, Human pause,
    route availability and optional WH-02A measurements. No resource metrics
    are required and no savings are claimed. READY/reuse are advisory words,
    not authority for a private operator or a Playwright attach. Real runtime
    composition, origin registration and authenticated UI remain separate.
    """
    if unknown or type(evidence) is not WakeHubSmartReadinessEvidence:
        raise WakeHubSmartInputError()
    core_plan = evidence._validate()
    core = evidence.core
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    status = WakeHubSmartCoreStatus
    code = WakeHubSmartReadinessCode
    if core_plan.code is WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT:
        core_status = status.HOLD_UNKNOWN_EFFECT
    elif (core_plan.code is WakeHubCoreIdleCode.HOLD_SOURCE_DRIFT
          or evidence.source is not current):
        core_status = status.HOLD_SOURCE_DRIFT
    elif evidence.projection is not current:
        core_status = status.HOLD_PRIVATE_PROJECTION
    elif (core_plan.code is WakeHubCoreIdleCode.WAIT_RECEIVER
          or evidence.listeners is not current):
        core_status = status.WAIT_RECEIVER
    elif evidence.origin.event is WakeHubEventState.ABSENT:
        core_status = status.WAIT_EVENT
    elif evidence.origin.event is WakeHubEventState.STALE:
        core_status = status.HOLD_EVENT_STALE
    elif evidence.origin.event is WakeHubEventState.DUPLICATE_AMBIGUOUS:
        core_status = status.HOLD_EVENT_AMBIGUITY
    elif core_plan.code is WakeHubCoreIdleCode.KEEP_ACTIVE_PENDING:
        core_status = status.WAIT_PENDING_WORK
    else:
        core_status = status.CORE_RECEIVE_IDLE_HEALTHY

    def result(reason: WakeHubSmartReadinessCode) -> WakeHubSmartReadinessPlan:
        return WakeHubSmartReadinessPlan(
            code=reason, core_status=core_status,
            browser_advice=(WakeHubBrowserAdvice.REUSE_VERIFIED_OWNER
                            if reason is code.READY_TO_PREPARE_ADVISORY_ONLY
                            else WakeHubBrowserAdvice.WAIT),
            listener_count=core_plan.listener_count,
            paused_by_human=core.trusted_human_pause,
        )

    if core_status is not status.CORE_RECEIVE_IDLE_HEALTHY:
        return result(code(core_status.value))
    if core.trusted_human_pause:
        return result(code.HOLD_HUMAN_PAUSED)
    if (evidence.desktop is not WakeHubDesktopState.OWNER_INTERACTIVE
            or not core.interactive_session_proven):
        return result(code.WAIT_INTERACTIVE_SESSION)
    if evidence.session_owner is not current:
        return result(code.HOLD_SESSION_OWNER)
    browser = evidence.browser
    if (browser.ownership is WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED
            or core.chrome_ownership
            is WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED):
        return result(code.WAIT_CHROME)
    if (browser.ownership is not WakeHubChromeOwnership.OWNED_CDP_9444
            or core.chrome_ownership is not WakeHubChromeOwnership.OWNED_CDP_9444):
        return result(code.HOLD_CHROME_OWNERSHIP)
    if browser.host is not WakeHubEndpointHost.IPV4_LOOPBACK or browser.port != 9444:
        return result(code.HOLD_CHROME_ENDPOINT)
    if browser.pid == 0 or any(value is not current for value in (
        browser.pid_creation, browser.executable, browser.profile,
        browser.port_owner, browser.identity_current,
    )):
        return result(code.HOLD_CHROME_IDENTITY)
    if browser.retained_account is not current:
        return result(code.HOLD_BROWSER_ACCOUNT)
    origin = evidence.origin
    for proof, reason in (
        (origin.issuer, code.HOLD_ORIGIN_ISSUER),
        (origin.owner, code.HOLD_ORIGIN_OWNER),
        (origin.route, code.HOLD_ORIGIN_ROUTE),
        (origin.generation, code.HOLD_ORIGIN_GENERATION),
    ):
        if proof is not current:
            return result(reason)
    if origin.consent is not WakeHubConsentState.CURRENT:
        return result(code.HOLD_ORIGIN_CONSENT)
    return result(code.READY_TO_PREPARE_ADVISORY_ONLY)


# WH-05A is a pure source policy over PRIVATE exact-chat autoload projections.
# It advises only: no browser, Playwright, CDP, task, network, file or Send
# operation is reachable or authorized from this module.
class WakeHubAutoloadInputError(ValueError):
    """Closed-schema diagnostic without caller values or private identifiers."""

    def __init__(self) -> None:
        super().__init__("WAKEHUB_AUTOLOAD_INPUT_INVALID")


class WakeHubChatBinding(str, Enum):
    EXACT_CHAT_A = "EXACT_CHAT_A"
    WRONG_CHAT_B = "WRONG_CHAT_B"
    UNBOUND = "UNBOUND"
    AMBIGUOUS = "AMBIGUOUS"


class WakeHubAutoloadAdvice(str, Enum):
    ATTACH_EXISTING_ADVISORY_ONLY = "ATTACH_EXISTING_ADVISORY_ONLY"
    OPEN_DEDICATED_TAB_ADVISORY_ONLY = "OPEN_DEDICATED_TAB_ADVISORY_ONLY"
    WAIT = "WAIT"
    HOLD = "HOLD"


class WakeHubAutoloadReason(str, Enum):
    # Advisory outcomes
    ATTACH_EXISTING_EXACT_CHAT_A_TAB = "ATTACH_EXISTING_EXACT_CHAT_A_TAB"
    OPEN_NEW_DEDICATED_TAB_ROUTE_PROVEN = "OPEN_NEW_DEDICATED_TAB_ROUTE_PROVEN"

    # Wait conditions
    WAIT_RECEIVER = "WAIT_RECEIVER"
    WAIT_EVENT = "WAIT_EVENT"
    WAIT_PENDING_WORK = "WAIT_PENDING_WORK"
    WAIT_INTERACTIVE_SESSION = "WAIT_INTERACTIVE_SESSION"
    WAIT_CHROME = "WAIT_CHROME"
    WAIT_RESPONSE_STREAMING_ACTIVE = "WAIT_RESPONSE_STREAMING_ACTIVE"
    WAIT_POST_NAVIGATION_RECHECK = "WAIT_POST_NAVIGATION_RECHECK"
    WAIT_DEDICATED_CONTEXT = "WAIT_DEDICATED_CONTEXT"

    # Hold conditions
    HOLD_UNKNOWN_EFFECT = "HOLD_UNKNOWN_EFFECT"
    HOLD_SOURCE_DRIFT = "HOLD_SOURCE_DRIFT"
    HOLD_PRIVATE_PROJECTION = "HOLD_PRIVATE_PROJECTION"
    HOLD_EVENT_STALE = "HOLD_EVENT_STALE"
    HOLD_EVENT_AMBIGUITY = "HOLD_EVENT_AMBIGUITY"
    HOLD_HUMAN_PAUSED = "HOLD_HUMAN_PAUSED"
    HOLD_SESSION_OWNER = "HOLD_SESSION_OWNER"
    HOLD_CHROME_OWNERSHIP = "HOLD_CHROME_OWNERSHIP"
    HOLD_CHROME_ENDPOINT = "HOLD_CHROME_ENDPOINT"
    HOLD_CHROME_IDENTITY = "HOLD_CHROME_IDENTITY"
    HOLD_BROWSER_ACCOUNT = "HOLD_BROWSER_ACCOUNT"
    HOLD_ORIGIN_ISSUER = "HOLD_ORIGIN_ISSUER"
    HOLD_ORIGIN_OWNER = "HOLD_ORIGIN_OWNER"
    HOLD_ORIGIN_ROUTE = "HOLD_ORIGIN_ROUTE"
    HOLD_ORIGIN_GENERATION = "HOLD_ORIGIN_GENERATION"
    HOLD_ORIGIN_CONSENT = "HOLD_ORIGIN_CONSENT"
    HOLD_DUPLICATE_MATCHING_TABS = "HOLD_DUPLICATE_MATCHING_TABS"
    HOLD_WRONG_CHAT_TARGET = "HOLD_WRONG_CHAT_TARGET"
    HOLD_MARKER_AMBIGUOUS = "HOLD_MARKER_AMBIGUOUS"
    HOLD_LOGIN_OR_CAPTCHA_CHALLENGE = "HOLD_LOGIN_OR_CAPTCHA_CHALLENGE"
    HOLD_NON_DEDICATED_CONTEXT = "HOLD_NON_DEDICATED_CONTEXT"
    HOLD_HUMAN_DRAFT_PRESENT = "HOLD_HUMAN_DRAFT_PRESENT"
    HOLD_GENERATION_DRIFT = "HOLD_GENERATION_DRIFT"
    HOLD_POST_NAVIGATION_RECHECK_REQUIRED = "HOLD_POST_NAVIGATION_RECHECK_REQUIRED"
    HOLD_UNRELATED_TAB_MUTATION = "HOLD_UNRELATED_TAB_MUTATION"


# Code alias for parity with earlier readiness planners
WakeHubAutoloadCode = WakeHubAutoloadReason


class _WakeHubAutoloadProjection:
    """Closed-schema representation validation for WH-05A projections."""

    __slots__ = ()

    def __init__(self, *args: object, **values: object) -> None:
        schema = fields(self)
        if args or len(values) != len(schema) or any(
            item.name not in values for item in schema
        ):
            raise WakeHubAutoloadInputError()
        for item in schema:
            object.__setattr__(self, item.name, values[item.name])
        self._validate()


def _wakehub_autoload_enums(*pairs: tuple[object, type[Enum]]) -> None:
    if any(not _wakehub_enum(value, expected) for value, expected in pairs):
        raise WakeHubAutoloadInputError()


@dataclass(frozen=True, slots=True, init=False)
class WakeHubChatTabEvidence(_WakeHubAutoloadProjection):
    """Sanitized evidence for an observed tab candidate; no URLs, scripts or DOM."""

    tab_id: int
    chat_binding: WakeHubChatBinding
    marker: WakeHubProofState
    dedicated_context: bool
    human_draft_present: bool
    actively_generating: bool
    security_challenge_present: bool
    generation: WakeHubProofState
    redirect_drift: bool
    post_navigation_recheck: WakeHubProofState
    account_proof: WakeHubProofState

    def _validate(self) -> None:
        _wakehub_autoload_enums(
            (self.chat_binding, WakeHubChatBinding),
            (self.marker, WakeHubProofState),
            (self.generation, WakeHubProofState),
            (self.post_navigation_recheck, WakeHubProofState),
            (self.account_proof, WakeHubProofState),
        )
        for bool_field in (
            self.dedicated_context, self.human_draft_present,
            self.actively_generating, self.security_challenge_present,
            self.redirect_drift,
        ):
            if type(bool_field) is not bool:
                raise WakeHubAutoloadInputError()
        if type(self.tab_id) is not int or not 0 <= self.tab_id <= _MAX_WINDOWS_COUNT:
            raise WakeHubAutoloadInputError()


@dataclass(frozen=True, slots=True, init=False)
class WakeHubChatAutoloadEvidence(_WakeHubAutoloadProjection):
    """Sanitized independent evidence projection for exact Chat A autoload planning."""

    readiness: WakeHubSmartReadinessEvidence
    tabs: tuple[WakeHubChatTabEvidence, ...]
    dedicated_context: WakeHubProofState
    tab_generation: WakeHubProofState
    route_authorized: WakeHubProofState
    unrelated_tab_mutation: bool

    def _validate(self) -> WakeHubSmartReadinessPlan:
        _wakehub_autoload_enums(
            (self.dedicated_context, WakeHubProofState),
            (self.tab_generation, WakeHubProofState),
            (self.route_authorized, WakeHubProofState),
        )
        if type(self.unrelated_tab_mutation) is not bool:
            raise WakeHubAutoloadInputError()
        if type(self.readiness) is not WakeHubSmartReadinessEvidence:
            raise WakeHubAutoloadInputError()
        if type(self.tabs) is not tuple or len(self.tabs) > WAKEHUB_MAX_SAMPLES:
            raise WakeHubAutoloadInputError()
        for tab in self.tabs:
            if type(tab) is not WakeHubChatTabEvidence:
                raise WakeHubAutoloadInputError()
            tab._validate()
        try:
            return assess_wakehub_smart_readiness(self.readiness)
        except WakeHubSmartInputError:
            raise WakeHubAutoloadInputError() from None


@dataclass(frozen=True, slots=True)
class WakeHubChatAutoloadPlan(WakeHubNoEffectPlan):
    """Sanitized categorical autoload advice only; never permits execution."""

    advice: WakeHubAutoloadAdvice
    reason: WakeHubAutoloadReason
    listener_count: int
    readiness_code: WakeHubSmartReadinessCode
    paused_by_human: bool
    scope: Literal["PURE_EXACT_CHAT_AUTOLOAD_POLICY_ONLY"] = field(
        default="PURE_EXACT_CHAT_AUTOLOAD_POLICY_ONLY", init=False)
    keep_event_receiver_alive: Literal[True] = field(default=True, init=False)
    native_operations: Literal[False] = field(default=False, init=False)
    start: Literal[False] = field(default=False, init=False)
    stop: Literal[False] = field(default=False, init=False)
    task: Literal[False] = field(default=False, init=False)
    chrome: Literal[False] = field(default=False, init=False)
    navigation: Literal[False] = field(default=False, init=False)
    send: Literal[False] = field(default=False, init=False)
    plugin: Literal[False] = field(default=False, init=False)
    attach_authorized: Literal[False] = field(default=False, init=False)
    open_tab_authorized: Literal[False] = field(default=False, init=False)
    click_permitted: Literal[False] = field(default=False, init=False)
    dom_ack_counted: Literal[False] = field(default=False, init=False)
    server_ack_counted: Literal[False] = field(default=False, init=False)
    post_navigation_recheck_required: Literal[True] = field(
        default=True, init=False)

    @property
    def code(self) -> WakeHubAutoloadReason:
        return self.reason


def assess_wakehub_chat_autoload_plan(
    evidence: WakeHubChatAutoloadEvidence | None = None, **unknown: object,
) -> WakeHubChatAutoloadPlan:
    """Pure, deterministic WH-05A exact Chat A autoload advisory planner.

    Evaluates private projections without browser actions, Playwright attach,
    CDP navigation or DOM interaction. Strictly fail-closed on unknown effect,
    source drift, stale/missing proof, wrong account, captcha, human draft or
    redirects. Advises ATTACH_EXISTING_ADVISORY_ONLY when exactly one confirmed
    Chat A tab is verified in dedicated context, or OPEN_DEDICATED_TAB_ADVISORY_ONLY
    when zero matching tabs exist and origin route is authorized.
    """
    if unknown or type(evidence) is not WakeHubChatAutoloadEvidence:
        raise WakeHubAutoloadInputError()
    readiness_plan = evidence._validate()
    readiness = evidence.readiness
    core = readiness.core
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    advice_enum = WakeHubAutoloadAdvice
    reason_enum = WakeHubAutoloadReason

    def result(reason: WakeHubAutoloadReason) -> WakeHubChatAutoloadPlan:
        if reason is reason_enum.ATTACH_EXISTING_EXACT_CHAT_A_TAB:
            advice = advice_enum.ATTACH_EXISTING_ADVISORY_ONLY
        elif reason is reason_enum.OPEN_NEW_DEDICATED_TAB_ROUTE_PROVEN:
            advice = advice_enum.OPEN_DEDICATED_TAB_ADVISORY_ONLY
        elif reason.value.startswith("WAIT_"):
            advice = advice_enum.WAIT
        else:
            advice = advice_enum.HOLD
        return WakeHubChatAutoloadPlan(
            advice=advice,
            reason=reason,
            listener_count=readiness_plan.listener_count,
            readiness_code=readiness_plan.code,
            paused_by_human=core.trusted_human_pause,
        )

    # 1. Gate on WH04A smart readiness
    if readiness_plan.code is not WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY:
        return result(reason_enum(readiness_plan.code.value))

    # 2. Gate on dedicated context proof
    if evidence.dedicated_context is WakeHubProofState.MISSING:
        return result(reason_enum.WAIT_DEDICATED_CONTEXT)
    if evidence.dedicated_context is not current:
        return result(reason_enum.HOLD_NON_DEDICATED_CONTEXT)

    # 3. Gate on tab generation drift
    if evidence.tab_generation is not current:
        return result(reason_enum.HOLD_GENERATION_DRIFT)

    # 4. Gate on unrelated tab mutation
    if evidence.unrelated_tab_mutation:
        return result(reason_enum.HOLD_UNRELATED_TAB_MUTATION)

    # 5. Evaluate tabs
    matching_tabs = [
        t for t in evidence.tabs
        if t.chat_binding is WakeHubChatBinding.EXACT_CHAT_A
    ]

    # Multiple tabs or duplicate matching tabs
    if len(matching_tabs) > 1 or len(evidence.tabs) > 1:
        return result(reason_enum.HOLD_DUPLICATE_MATCHING_TABS)

    # Exactly one tab present
    if len(evidence.tabs) == 1:
        tab = evidence.tabs[0]
        if tab.tab_id == 0:
            return result(reason_enum.HOLD_NON_DEDICATED_CONTEXT)
        if tab.chat_binding is WakeHubChatBinding.WRONG_CHAT_B:
            return result(reason_enum.HOLD_WRONG_CHAT_TARGET)
        if tab.chat_binding is not WakeHubChatBinding.EXACT_CHAT_A:
            return result(reason_enum.HOLD_WRONG_CHAT_TARGET)
        if tab.marker is not current:
            return result(reason_enum.HOLD_MARKER_AMBIGUOUS)
        if not tab.dedicated_context:
            return result(reason_enum.HOLD_NON_DEDICATED_CONTEXT)
        if tab.account_proof is not current:
            return result(reason_enum.HOLD_BROWSER_ACCOUNT)
        if tab.security_challenge_present:
            return result(reason_enum.HOLD_LOGIN_OR_CAPTCHA_CHALLENGE)
        if tab.human_draft_present:
            return result(reason_enum.HOLD_HUMAN_DRAFT_PRESENT)
        if tab.actively_generating:
            return result(reason_enum.WAIT_RESPONSE_STREAMING_ACTIVE)
        if tab.redirect_drift or tab.generation is not current:
            return result(reason_enum.HOLD_GENERATION_DRIFT)
        if tab.post_navigation_recheck is WakeHubProofState.MISSING:
            return result(reason_enum.WAIT_POST_NAVIGATION_RECHECK)
        if tab.post_navigation_recheck is not current:
            return result(reason_enum.HOLD_POST_NAVIGATION_RECHECK_REQUIRED)
        return result(reason_enum.ATTACH_EXISTING_EXACT_CHAT_A_TAB)

    # Zero tabs present: check if open dedicated tab is authorized
    if evidence.route_authorized is not current:
        return result(reason_enum.HOLD_ORIGIN_ROUTE)

    return result(reason_enum.OPEN_NEW_DEDICATED_TAB_ROUTE_PROVEN)


# WH-06A projects private observer categories only. WH-03 issuance and WH-04B
# native composition remain external/unresolved; no constructor is a verifier.
class WakeHubSendPreviewInputError(ValueError):
    """Fixed diagnostic; never interpolate caller fields, values or secrets."""

    def __init__(self) -> None:
        super().__init__("WAKEHUB_SEND_PREVIEW_INPUT_INVALID")


class WakeHubCompletionState(str, Enum):
    NEW_WORK_COMPLETION = "NEW_WORK_COMPLETION"
    ABSENT = "ABSENT"
    PUBLIC_ASSERTION = "PUBLIC_ASSERTION"
    STALE = "STALE"
    DUPLICATE = "DUPLICATE"


class WakeHubSendConsentScope(str, Enum):
    SEND_ONLY = "SEND_ONLY"
    OTHER = "OTHER"
    ABSENT = "ABSENT"


class WakeHubSendIntentState(str, Enum):
    WRITE_AHEAD_RECORDED = "WRITE_AHEAD_RECORDED"
    NOT_RECORDED = "NOT_RECORDED"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    UNKNOWN = "UNKNOWN"


class WakeHubSendDeliveryState(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    ATTEMPT_BEGUN = "ATTEMPT_BEGUN"
    RECEIPT_LOST = "RECEIPT_LOST"
    OUTCOME_UNOBSERVED = "OUTCOME_UNOBSERVED"
    DUPLICATE_KEY = "DUPLICATE_KEY"


class WakeHubSendPreviewAdvice(str, Enum):
    PREVIEW_READY_ADVISORY_ONLY = "PREVIEW_READY_ADVISORY_ONLY"
    WAIT = "WAIT"
    HOLD = "HOLD"
    RECONCILE_UNKNOWN_NO_RETRY = "RECONCILE_UNKNOWN_NO_RETRY"
    COMPLETE_UI_ONLY = "COMPLETE_UI_ONLY"


class WakeHubSendPreviewSafety(str, Enum):
    ADVISORY_NO_EFFECT = "ADVISORY_NO_EFFECT"
    BLOCKED_NO_EFFECT = "BLOCKED_NO_EFFECT"
    RECONCILE_ONLY_NO_RETRY = "RECONCILE_ONLY_NO_RETRY"
    UI_ONLY_WITHOUT_SERVER_ACK = "UI_ONLY_WITHOUT_SERVER_ACK"


class WakeHubSendPreviewReason(str, Enum):
    PREVIEW_REQUIREMENTS_CURRENT = "PREVIEW_REQUIREMENTS_CURRENT"
    COMPLETE_UI_USER_TURN_WITHOUT_SERVER_ACK = "COMPLETE_UI_USER_TURN_WITHOUT_SERVER_ACK"
    RECONCILE_UNKNOWN_EFFECT = "RECONCILE_UNKNOWN_EFFECT"
    RECONCILE_DUPLICATE_KEY = "RECONCILE_DUPLICATE_KEY"
    RECONCILE_PRIOR_ATTEMPT = "RECONCILE_PRIOR_ATTEMPT"
    RECONCILE_UNBOUND_UI_WITNESS = "RECONCILE_UNBOUND_UI_WITNESS"
    HOLD_PRIVATE_SEND_PROJECTION = "HOLD_PRIVATE_SEND_PROJECTION"
    HOLD_NATIVE_OBSERVER_PROOF = "HOLD_NATIVE_OBSERVER_PROOF"
    WAIT_NEW_WORK_COMPLETION = "WAIT_NEW_WORK_COMPLETION"
    HOLD_COMPLETION_PROOF = "HOLD_COMPLETION_PROOF"
    HOLD_SEND_ONLY_CONSENT = "HOLD_SEND_ONLY_CONSENT"
    HOLD_CONSENT_SIGNATURE = "HOLD_CONSENT_SIGNATURE"
    HOLD_CONSENT_SIGNER = "HOLD_CONSENT_SIGNER"
    HOLD_SEND_ORIGIN_BINDING = "HOLD_SEND_ORIGIN_BINDING"
    HOLD_SEND_LEASE = "HOLD_SEND_LEASE"
    HOLD_SEND_CORRELATION = "HOLD_SEND_CORRELATION"
    WAIT_D2_WRITE_AHEAD_INTENT = "WAIT_D2_WRITE_AHEAD_INTENT"
    HOLD_D2_INTENT_PROOF = "HOLD_D2_INTENT_PROOF"
    WAIT_NAVIGATION_AND_REPROOF = "WAIT_NAVIGATION_AND_REPROOF"
    HOLD_COMPOSER_NOT_EMPTY = "HOLD_COMPOSER_NOT_EMPTY"
    WAIT_VISIBLE_SEND_CONTROL = "WAIT_VISIBLE_SEND_CONTROL"
    HOLD_SEND_CONTROL_AMBIGUOUS = "HOLD_SEND_CONTROL_AMBIGUOUS"
    WAIT_SEND_CONTROL_ENABLED = "WAIT_SEND_CONTROL_ENABLED"


class _WakeHubSendPreviewProjection:
    __slots__ = ()

    def __init__(self, *args: object, **values: object) -> None:
        schema = fields(self)
        if args or len(values) != len(schema) or any(
            item.name not in values for item in schema
        ):
            raise WakeHubSendPreviewInputError()
        for item in schema:
            object.__setattr__(self, item.name, values[item.name])
        try:
            self._validate()
        except AttributeError:
            raise WakeHubSendPreviewInputError() from None


@dataclass(frozen=True, slots=True, init=False)
class WakeHubSendAuthorizationEvidence(_WakeHubSendPreviewProjection):
    """Synthetic PRIVATE projection, never a grant or public Issue input.

    native_observer independently authenticates current native observations;
    completion_proof verifies a genuine new work-completion event, never a
    synthetic-data flag. consent_signature includes independent signature,
    scope, freshness and revocation verification by the current trusted signer.
    origin_binding independently binds source, event, consent, owner, original
    Chat A route/proof/generation, lease and correlation to ONE origin. Matching
    strings/integers cannot establish this binding. No raw identifier is held.
    intent_proof independently witnesses the same D2 idempotent intent durably
    recorded BEFORE any click, including completion/delivery key uniqueness.
    This policy reads categories; it implements none of those attestations.
    """

    projection: WakeHubProofState
    native_observer: WakeHubProofState
    completion: WakeHubCompletionState
    completion_proof: WakeHubProofState
    consent: WakeHubConsentState
    consent_scope: WakeHubSendConsentScope
    consent_signature: WakeHubProofState
    signer: WakeHubProofState
    origin_binding: WakeHubProofState
    lease: WakeHubProofState
    correlation: WakeHubProofState
    intent: WakeHubSendIntentState
    intent_proof: WakeHubProofState

    def _validate(self) -> None:
        pairs = (
            (self.completion, WakeHubCompletionState),
            (self.consent, WakeHubConsentState),
            (self.consent_scope, WakeHubSendConsentScope),
            (self.intent, WakeHubSendIntentState),
            *((getattr(self, name), WakeHubProofState) for name in (
                "projection", "native_observer", "completion_proof",
                "consent_signature", "signer", "origin_binding", "lease",
                "correlation", "intent_proof",
            )),
        )
        if any(not _wakehub_enum(value, expected) for value, expected in pairs):
            raise WakeHubSendPreviewInputError()


@dataclass(frozen=True, slots=True, init=False)
class WakeHubSendOnlyPreviewEvidence(_WakeHubSendPreviewProjection):
    """Immutable observer projection, not precomputed READY or executable data.

    ui_user_turn is an independent current DOM witness of the EXACT matching
    user turn after this intent's prior Send, bound to the original Chat A,
    event, source, owner and generation. It is never a ChatGPT server ACK.
    Counts/booleans describe privately observed composer/Send controls only;
    their constructors authenticate neither the DOM nor any native authority.
    """

    autoload: WakeHubChatAutoloadEvidence
    authorization: WakeHubSendAuthorizationEvidence
    composer_empty: bool
    send_control_count: int
    send_control_visible: bool
    send_control_enabled: bool
    delivery: WakeHubSendDeliveryState
    ui_user_turn: WakeHubProofState

    def _validate(self) -> WakeHubChatAutoloadPlan:
        if (type(self.autoload) is not WakeHubChatAutoloadEvidence
                or type(self.authorization) is not WakeHubSendAuthorizationEvidence
                or not _wakehub_enum(self.delivery, WakeHubSendDeliveryState)
                or not _wakehub_enum(self.ui_user_turn, WakeHubProofState)
                or any(type(value) is not bool for value in (
                    self.composer_empty, self.send_control_visible,
                    self.send_control_enabled,
                ))
                or type(self.send_control_count) is not int
                or not 0 <= self.send_control_count <= _MAX_WINDOWS_COUNT):
            raise WakeHubSendPreviewInputError()
        self.authorization._validate()
        try:
            return assess_wakehub_chat_autoload_plan(self.autoload)
        except WakeHubAutoloadInputError:
            raise WakeHubSendPreviewInputError() from None


@dataclass(frozen=True, slots=True)
class WakeHubSendOnlyPreviewPlan(WakeHubNoEffectPlan):
    """Every effect flag describes THIS evaluation and is permanently false."""

    advice: WakeHubSendPreviewAdvice
    reason: WakeHubSendPreviewReason | WakeHubAutoloadReason
    safety_state: WakeHubSendPreviewSafety
    paused_by_human: bool
    scope: Literal["WH06A_PURE_SEND_PREVIEW_POLICY_ONLY"] = field(
        default="WH06A_PURE_SEND_PREVIEW_POLICY_ONLY", init=False)
    keep_event_receiver_alive: Literal[True] = field(default=True, init=False)
    native_operations: Literal[False] = field(default=False, init=False)
    chrome: Literal[False] = field(default=False, init=False)
    navigation: Literal[False] = field(default=False, init=False)
    send: Literal[False] = field(default=False, init=False)
    click: Literal[False] = field(default=False, init=False)
    plugin: Literal[False] = field(default=False, init=False)
    start: Literal[False] = field(default=False, init=False)
    stop: Literal[False] = field(default=False, init=False)
    task: Literal[False] = field(default=False, init=False)
    process: Literal[False] = field(default=False, init=False)
    grant: Literal[False] = field(default=False, init=False)
    authorization: Literal[False] = field(default=False, init=False)
    d2_intent_written: Literal[False] = field(default=False, init=False)
    server_ack_counted: Literal[False] = field(default=False, init=False)


def assess_wakehub_send_only_preview(
    evidence: WakeHubSendOnlyPreviewEvidence | None = None, **unknown: object,
) -> WakeHubSendOnlyPreviewPlan:
    """Pure WH-06A advice; no runtime, clock, I/O, issuer, signing or effects.

    Validate the complete schema, derive WH05A/WH04A readiness internally,
    then apply Human pause, no-retry history, private provenance and UI gates.
    OPEN_DEDICATED_TAB advice requires future real navigation and new proof.
    READY is synthetic policy coverage only; no native origin is attested here.
    """
    if unknown or type(evidence) is not WakeHubSendOnlyPreviewEvidence:
        raise WakeHubSendPreviewInputError()
    try:
        autoload = evidence._validate()
    except AttributeError:
        raise WakeHubSendPreviewInputError() from None
    auth = evidence.authorization
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    advice = WakeHubSendPreviewAdvice
    reason = WakeHubSendPreviewReason
    upstream = WakeHubAutoloadReason
    safety = WakeHubSendPreviewSafety

    def result(code: WakeHubSendPreviewReason | WakeHubAutoloadReason,
               decision: WakeHubSendPreviewAdvice) -> WakeHubSendOnlyPreviewPlan:
        state = safety.BLOCKED_NO_EFFECT
        if decision is advice.PREVIEW_READY_ADVISORY_ONLY:
            state = safety.ADVISORY_NO_EFFECT
        elif decision is advice.RECONCILE_UNKNOWN_NO_RETRY:
            state = safety.RECONCILE_ONLY_NO_RETRY
        elif decision is advice.COMPLETE_UI_ONLY:
            state = safety.UI_ONLY_WITHOUT_SERVER_ACK
        return WakeHubSendOnlyPreviewPlan(
            advice=decision, reason=code, safety_state=state,
            paused_by_human=autoload.paused_by_human,
        )

    if autoload.paused_by_human:
        return result(upstream.HOLD_HUMAN_PAUSED, advice.HOLD)
    if (autoload.reason is upstream.HOLD_UNKNOWN_EFFECT
            or auth.intent is WakeHubSendIntentState.UNKNOWN):
        return result(reason.RECONCILE_UNKNOWN_EFFECT, advice.RECONCILE_UNKNOWN_NO_RETRY)
    if (auth.completion is WakeHubCompletionState.DUPLICATE
            or auth.intent is WakeHubSendIntentState.DUPLICATE_KEY
            or evidence.delivery is WakeHubSendDeliveryState.DUPLICATE_KEY
            or autoload.reason is upstream.HOLD_EVENT_AMBIGUITY):
        return result(reason.RECONCILE_DUPLICATE_KEY, advice.RECONCILE_UNKNOWN_NO_RETRY)

    # Each proof has independent provenance semantics, not enum authentication.
    blocked = None
    for value, code in (
        (auth.projection, reason.HOLD_PRIVATE_SEND_PROJECTION),
        (auth.native_observer, reason.HOLD_NATIVE_OBSERVER_PROOF),
        (auth.completion_proof, reason.HOLD_COMPLETION_PROOF),
        (auth.consent_signature, reason.HOLD_CONSENT_SIGNATURE),
        (auth.signer, reason.HOLD_CONSENT_SIGNER),
        (auth.origin_binding, reason.HOLD_SEND_ORIGIN_BINDING),
        (auth.lease, reason.HOLD_SEND_LEASE),
        (auth.correlation, reason.HOLD_SEND_CORRELATION),
        (auth.intent_proof, reason.HOLD_D2_INTENT_PROOF),
    ):
        if value is not current:
            blocked = code
            break
    if auth.completion is not WakeHubCompletionState.NEW_WORK_COMPLETION:
        blocked = (reason.WAIT_NEW_WORK_COMPLETION
                   if auth.completion is WakeHubCompletionState.ABSENT
                   else reason.HOLD_COMPLETION_PROOF)
    elif (auth.consent is not WakeHubConsentState.CURRENT
          or auth.consent_scope is not WakeHubSendConsentScope.SEND_ONLY):
        blocked = reason.HOLD_SEND_ONLY_CONSENT
    elif auth.intent is WakeHubSendIntentState.NOT_RECORDED:
        blocked = reason.WAIT_D2_WRITE_AHEAD_INTENT

    if evidence.delivery is not WakeHubSendDeliveryState.NOT_ATTEMPTED:
        # A response stream or new Human draft cannot erase an exact prior
        # user-turn witness. Still require unchanged Chat A/recheck/generation;
        # WH05A checks draft/stream before those later integrity checks.
        tabs = evidence.autoload.tabs
        if (blocked is None and evidence.ui_user_turn is current
                and evidence.autoload.route_authorized is current
                and autoload.reason in (
                    upstream.ATTACH_EXISTING_EXACT_CHAT_A_TAB,
                    upstream.WAIT_RESPONSE_STREAMING_ACTIVE,
                    upstream.HOLD_HUMAN_DRAFT_PRESENT,
                ) and len(tabs) == 1 and not tabs[0].redirect_drift
                and tabs[0].generation is current
                and tabs[0].post_navigation_recheck is current):
            return result(reason.COMPLETE_UI_USER_TURN_WITHOUT_SERVER_ACK,
                          advice.COMPLETE_UI_ONLY)
        return result(reason.RECONCILE_PRIOR_ATTEMPT, advice.RECONCILE_UNKNOWN_NO_RETRY)
    if evidence.ui_user_turn is not WakeHubProofState.MISSING:
        return result(reason.RECONCILE_UNBOUND_UI_WITNESS, advice.RECONCILE_UNKNOWN_NO_RETRY)
    if blocked is not None:
        return result(blocked, advice.WAIT if blocked in (
            reason.WAIT_NEW_WORK_COMPLETION, reason.WAIT_D2_WRITE_AHEAD_INTENT,
        ) else advice.HOLD)
    if autoload.advice is WakeHubAutoloadAdvice.OPEN_DEDICATED_TAB_ADVISORY_ONLY:
        return result(reason.WAIT_NAVIGATION_AND_REPROOF, advice.WAIT)
    if autoload.advice is not WakeHubAutoloadAdvice.ATTACH_EXISTING_ADVISORY_ONLY:
        return result(autoload.reason, advice.WAIT if autoload.advice
                      is WakeHubAutoloadAdvice.WAIT else advice.HOLD)
    if evidence.autoload.route_authorized is not current:
        return result(upstream.HOLD_ORIGIN_ROUTE, advice.HOLD)
    if not evidence.composer_empty:
        return result(reason.HOLD_COMPOSER_NOT_EMPTY, advice.HOLD)
    if evidence.send_control_count > 1:
        return result(reason.HOLD_SEND_CONTROL_AMBIGUOUS, advice.HOLD)
    if evidence.send_control_count == 0 or not evidence.send_control_visible:
        return result(reason.WAIT_VISIBLE_SEND_CONTROL, advice.WAIT)
    if not evidence.send_control_enabled:
        return result(reason.WAIT_SEND_CONTROL_ENABLED, advice.WAIT)
    return result(reason.PREVIEW_REQUIREMENTS_CURRENT, advice.PREVIEW_READY_ADVISORY_ONLY)


# WH-04B-00 composes source policy only. WH-03 issuance, WH-04B native
# composition, WH-05B navigation, WH-06B click and WH-07 live A-to-A remain
# UNPROVEN. Neither constructing evidence nor rendering this status proves them.
class WakeHubControlInputError(ValueError):
    """Fixed diagnostic with no caller fields, private values or identifiers."""

    def __init__(self) -> None:
        super().__init__("WAKEHUB_CONTROL_PROJECTION_INPUT_INVALID")


class WakeHubControlStage(str, Enum):
    HOLD = "HOLD"
    RECONCILE_NO_RETRY = "RECONCILE_NO_RETRY"
    WAIT_CORE = "WAIT_CORE"
    WAIT_PRIVATE_ORIGIN = "WAIT_PRIVATE_ORIGIN"
    WAIT_DESKTOP_OR_TAB = "WAIT_DESKTOP_OR_TAB"
    WAIT_SEND_GATES = "WAIT_SEND_GATES"
    PREVIEW_ADVISORY_ONLY = "PREVIEW_ADVISORY_ONLY"
    UI_ONLY_COMPLETE_STOP = "UI_ONLY_COMPLETE_STOP"


class WakeHubControlNextAction(str, Enum):
    HOLD_FOR_PRIVATE_REVIEW_NO_EFFECT = "HOLD_FOR_PRIVATE_REVIEW_NO_EFFECT"
    RECONCILE_PRIVATE_RECEIPTS_NO_RETRY = "RECONCILE_PRIVATE_RECEIPTS_NO_RETRY"
    WAIT_FOR_CORE_EVIDENCE_KEEP_RECEIVER = "WAIT_FOR_CORE_EVIDENCE_KEEP_RECEIVER"
    WAIT_FOR_PRIVATE_ORIGIN_PROOF = "WAIT_FOR_PRIVATE_ORIGIN_PROOF"
    PREPARE_AND_REPROVE_WITH_SEPARATE_AUTHORITY = "PREPARE_AND_REPROVE_WITH_SEPARATE_AUTHORITY"
    WAIT_FOR_PRIVATE_SEND_GATE_PROOF = "WAIT_FOR_PRIVATE_SEND_GATE_PROOF"
    REVIEW_ADVISORY_WITH_SEPARATE_AUTHORITY = "REVIEW_ADVISORY_WITH_SEPARATE_AUTHORITY"
    STOP_UI_WITNESS_WITHOUT_SERVER_ACK = "STOP_UI_WITNESS_WITHOUT_SERVER_ACK"


def _wakehub_control_stage(
    reason: WakeHubSendPreviewReason | WakeHubAutoloadReason,
    advice: WakeHubSendPreviewAdvice,
) -> WakeHubControlStage:
    """Total, conservative display mapping, including inconsistent pairs.

    This classifies already-derived reasons, never reimplements an evidence
    gate. Only the exact two successful WH06A reason/advice pairs can display
    preview or UI completion. HOLD/WAIT reasons cannot be upgraded by advice.
    """
    if (not (_wakehub_enum(reason, WakeHubSendPreviewReason)
             or _wakehub_enum(reason, WakeHubAutoloadReason))
            or not _wakehub_enum(advice, WakeHubSendPreviewAdvice)):
        raise WakeHubControlInputError()
    stage = WakeHubControlStage
    upstream = WakeHubAutoloadReason
    send = WakeHubSendPreviewReason
    decision = WakeHubSendPreviewAdvice
    if reason is upstream.HOLD_HUMAN_PAUSED:
        return stage.HOLD
    if (advice is decision.RECONCILE_UNKNOWN_NO_RETRY or reason in (
        upstream.HOLD_UNKNOWN_EFFECT, upstream.HOLD_EVENT_AMBIGUITY,
        send.RECONCILE_UNKNOWN_EFFECT, send.RECONCILE_DUPLICATE_KEY,
        send.RECONCILE_PRIOR_ATTEMPT, send.RECONCILE_UNBOUND_UI_WITNESS,
    )):
        return stage.RECONCILE_NO_RETRY
    if advice is decision.HOLD or reason.value.startswith("HOLD_"):
        return stage.HOLD
    if reason in (upstream.WAIT_RECEIVER, upstream.WAIT_PENDING_WORK):
        return stage.WAIT_CORE
    if reason is upstream.WAIT_EVENT:
        return stage.WAIT_PRIVATE_ORIGIN
    if reason in (
        upstream.WAIT_INTERACTIVE_SESSION, upstream.WAIT_CHROME,
        upstream.WAIT_RESPONSE_STREAMING_ACTIVE,
        upstream.WAIT_POST_NAVIGATION_RECHECK, upstream.WAIT_DEDICATED_CONTEXT,
        send.WAIT_NAVIGATION_AND_REPROOF,
    ):
        return stage.WAIT_DESKTOP_OR_TAB
    if reason in (
        send.WAIT_NEW_WORK_COMPLETION, send.WAIT_D2_WRITE_AHEAD_INTENT,
        send.WAIT_VISIBLE_SEND_CONTROL, send.WAIT_SEND_CONTROL_ENABLED,
    ) or advice is decision.WAIT:
        return stage.WAIT_SEND_GATES
    if (reason is send.PREVIEW_REQUIREMENTS_CURRENT
            and advice is decision.PREVIEW_READY_ADVISORY_ONLY):
        return stage.PREVIEW_ADVISORY_ONLY
    if (reason is send.COMPLETE_UI_USER_TURN_WITHOUT_SERVER_ACK
            and advice is decision.COMPLETE_UI_ONLY):
        return stage.UI_ONLY_COMPLETE_STOP
    return stage.HOLD


@dataclass(frozen=True, slots=True, init=False)
class WakeHubReadOnlyControlSnapshot(WakeHubSendOnlyPreviewPlan):
    """Sanitized disabled-safe status, constructed only from WH06A evidence.

    All output fields are init=False: neither the constructor nor replace
    accepts a caller-selected status, reason, ACK or effect flag. Evidence is
    revalidated through WH06A -> WH05A -> WH04A and is never retained here.
    Inherited effect flags describe THIS evaluation, not historical effects.
    Keep-alive fields are retention policy, never observed receiver liveness.
    """

    advice: WakeHubSendPreviewAdvice = field(init=False)
    reason: WakeHubSendPreviewReason | WakeHubAutoloadReason = field(init=False)
    safety_state: WakeHubSendPreviewSafety = field(init=False)
    paused_by_human: bool = field(init=False)
    stage: WakeHubControlStage = field(init=False)
    next_action: WakeHubControlNextAction = field(init=False)
    scope: Literal["WH04B00_PURE_READ_ONLY_CONTROL_POLICY_ONLY"] = field(
        default="WH04B00_PURE_READ_ONLY_CONTROL_POLICY_ONLY", init=False)
    keep_headless_core_alive: Literal[True] = field(default=True, init=False)
    controls_enabled: Literal[False] = field(default=False, init=False)
    can_send: Literal[False] = field(default=False, init=False)
    prepare_authorized: Literal[False] = field(default=False, init=False)
    attach_authorized: Literal[False] = field(default=False, init=False)
    open_tab_authorized: Literal[False] = field(default=False, init=False)
    click_permitted: Literal[False] = field(default=False, init=False)
    dom_ack_counted: Literal[False] = field(default=False, init=False)

    def __init__(
        self, evidence: WakeHubSendOnlyPreviewEvidence | None = None,
        *args: object, **unknown: object,
    ) -> None:
        if args or unknown or type(self) is not WakeHubReadOnlyControlSnapshot:
            raise WakeHubControlInputError()
        try:
            preview = assess_wakehub_send_only_preview(evidence)
        except WakeHubSendPreviewInputError:
            raise WakeHubControlInputError() from None
        stage = _wakehub_control_stage(preview.reason, preview.advice)
        actions = {
            WakeHubControlStage.HOLD: WakeHubControlNextAction.HOLD_FOR_PRIVATE_REVIEW_NO_EFFECT,
            WakeHubControlStage.RECONCILE_NO_RETRY: WakeHubControlNextAction.RECONCILE_PRIVATE_RECEIPTS_NO_RETRY,
            WakeHubControlStage.WAIT_CORE: WakeHubControlNextAction.WAIT_FOR_CORE_EVIDENCE_KEEP_RECEIVER,
            WakeHubControlStage.WAIT_PRIVATE_ORIGIN: WakeHubControlNextAction.WAIT_FOR_PRIVATE_ORIGIN_PROOF,
            WakeHubControlStage.WAIT_DESKTOP_OR_TAB: WakeHubControlNextAction.PREPARE_AND_REPROVE_WITH_SEPARATE_AUTHORITY,
            WakeHubControlStage.WAIT_SEND_GATES: WakeHubControlNextAction.WAIT_FOR_PRIVATE_SEND_GATE_PROOF,
            WakeHubControlStage.PREVIEW_ADVISORY_ONLY: WakeHubControlNextAction.REVIEW_ADVISORY_WITH_SEPARATE_AUTHORITY,
            WakeHubControlStage.UI_ONLY_COMPLETE_STOP: WakeHubControlNextAction.STOP_UI_WITNESS_WITHOUT_SERVER_ACK,
        }
        derived = dict(
            advice=preview.advice, reason=preview.reason,
            safety_state=preview.safety_state, paused_by_human=preview.paused_by_human,
            stage=stage, next_action=actions[stage],
        )
        for item in fields(WakeHubReadOnlyControlSnapshot):
            object.__setattr__(self, item.name, derived.get(item.name, item.default))


def assess_wakehub_control_projection(
    evidence: WakeHubSendOnlyPreviewEvidence | None = None,
    *args: object, **unknown: object,
) -> WakeHubReadOnlyControlSnapshot:
    """Pure WH-04B-00 status; no renderer, clock, observer or effect adapter.

    A typed input is representation validation only, not authentication of a
    public assertion or model-generated READY. The future private root owns
    proof custody and high-risk route selection. OPEN_DEDICATED_TAB only
    advises preparation and new proof under separate native authority;
    PREVIEW never approves execution and UI completion never claims server ACK.
    """
    return WakeHubReadOnlyControlSnapshot(evidence, *args, **unknown)

