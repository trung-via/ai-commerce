"""Offline acceptance for the non-disruptive three-runner Windows bootstrap.

No registration token, GitHub call, Windows task or model is invoked here.
"""
from __future__ import annotations

import builtins
from dataclasses import FrozenInstanceError, fields, replace
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest

from ai_commerce.engineering.three_runner_topology import (
    EXISTING_RUNNER, NEW_RUNNERS, hostops_route_activation_allowed,
    planned_topology, resource_policy, assess_wakehub_preflight,
    WakeHubPreflight,
    WAKEHUB_MAX_SAMPLES, WAKEHUB_MAX_PIDS_PER_SAMPLE,
    WakeHubBaseline, WakeHubChromeOwnership, WakeHubCoreIdleCode,
    WakeHubCoreIdleEvidence, WakeHubCoreIdlePlan, WakeHubCoreInputError,
    WakeHubIdleSample, WakeHubListenerState, WakeHubMeasurementCode,
    WakeHubObservationState, WakeHubReadinessSample, WakeHubTaskState,
    WakeHubWorkState, assess_wakehub_core_idle, plan_wakehub_measurements,
    WakeHubBrowserAdvice, WakeHubConsentState, WakeHubDesktopState,
    WakeHubEndpointHost, WakeHubEventState, WakeHubProofState,
    WakeHubSmartBrowserEvidence, WakeHubSmartCoreStatus, WakeHubSmartInputError,
    WakeHubSmartOriginEvidence, WakeHubSmartReadinessCode,
    WakeHubSmartReadinessEvidence, assess_wakehub_smart_readiness,
    WakeHubAutoloadAdvice, WakeHubAutoloadCode, WakeHubAutoloadInputError,
    WakeHubAutoloadReason, WakeHubChatBinding, WakeHubChatTabEvidence,
    WakeHubChatAutoloadEvidence, WakeHubChatAutoloadPlan,
    assess_wakehub_chat_autoload_plan,
    WakeHubCompletionState, WakeHubSendConsentScope, WakeHubSendIntentState,
    WakeHubSendDeliveryState, WakeHubSendPreviewAdvice, WakeHubSendPreviewReason,
    WakeHubSendPreviewSafety, WakeHubSendPreviewInputError,
    WakeHubSendAuthorizationEvidence, WakeHubSendOnlyPreviewEvidence,
    assess_wakehub_send_only_preview,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "AI-Commerce-Three-Runners.ps1"
INGRESS = ROOT / ".github" / "workflows" / "issue-ingress.yml"
EF02 = ROOT / ".github" / "workflows" / "ef02-batch.yml"


def test_exact_three_runner_spec_and_distinct_workspaces():
    specs = planned_topology()
    assert len(specs) == 3
    assert specs[0] == EXISTING_RUNNER
    assert len(NEW_RUNNERS) == 2
    assert [s.name for s in specs] == [
        "AI-COMMERCE-EF02-01", "AI-COMMERCE-EF02-02", "AI-COMMERCE-HOSTOPS-01"
    ]
    assert [s.label for s in specs] == [
        "ai-commerce-ef02", "ai-commerce-ef02", "ai-commerce-hostops"
    ]
    assert len(set(s.root.casefold() for s in specs)) == 3
    assert len(set(s.task.casefold() for s in specs)) == 3
    assert resource_policy() == "OBSERVE_ONLY_NO_CPU_RAM_LIMITS"
    assert not hostops_route_activation_allowed({"AI-COMMERCE-EF02-01", "AI-COMMERCE-EF02-02"})
    assert hostops_route_activation_allowed({"AI-COMMERCE-HOSTOPS-01"})


def test_install_mode_owner_interactive_and_no_existing_runner_mutation():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "ValidateSet('Plan', 'Install', 'Status')" in source
    assert "LOCAL_ATTENDED_ONLY_NOT_GITHUB_ACTIONS" in source
    assert "INTERACTIVE_WINDOWS_SESSION_REQUIRED" in source
    assert "SID_MISMATCH_WITH_EXISTING_EF02" in source
    assert "AI-Commerce-EF02-Interactive" in source
    assert "EXISTING_EF02_01=NOT_MODIFIED" in source
    assert "AI-COMMERCE-EF02-02" in source
    assert "AI-COMMERCE-HOSTOPS-01" in source
    assert "ai-commerce-hostops" in source
    assert "Get-RunnerInventory" in source
    assert "TARGET_ROOT_EXISTS_RECONCILE_FIRST" in source
    assert "TARGET_GITHUB_RUNNER_EXISTS_RECONCILE_FIRST" in source
    assert "--replace" not in source
    assert "--ephemeral" not in source
    assert "Unregister-ScheduledTask" not in source
    assert "Stop-ScheduledTask" not in source
    assert "Stop-Service" not in source
    assert "Set-Service" not in source
    assert "Remove-Item -LiteralPath $Target.Root" not in source


def test_release_digest_one_time_token_and_private_local_tasks():
    source = SCRIPT.read_text(encoding="utf-8")
    for fragment in (
        "actions/runner/releases/latest",
        "actions-runner-win-x64-",
        "sha256:",
        "Get-FileHash",
        "RUNNER_ARCHIVE_SHA256_MISMATCH",
        "repos/$repo/actions/runners/registration-token",
        "--unattended --url $repoUrl --token $token",
        "--work '_work'",
        "Runner-Hidden.vbs",
        "New-ScheduledTaskPrincipal",
        "-LogonType Interactive -RunLevel Limited",
        "-MultipleInstances IgnoreNew",
        "REGISTERED_AND_TASK_STARTED=",
        "INSTALL_HOLD_RECONCILE_LOCAL_AND_GITHUB_STATE",
    ):
        assert fragment in source, fragment
    assert "Write-Host $token" not in source
    assert "NO_CPU_RAM_LIMITS" in source
    # Status text explicitly says NO_CPU_RAM_QUOTAS_CONFIGURED.
    # Exclude actual resource control, not that truthful reporting string.
    assert "Set-ProcessMitigation" not in source
    assert "--cpu-limit" not in source
    assert "--memory-limit" not in source


def test_dedicated_hostops_route_after_operator_reports_both_runners_online():
    ingress = INGRESS.read_text(encoding="utf-8")
    ef02 = EF02.read_text(encoding="utf-8")
    assert "  hostops-personal:" in ingress
    assert "group: personal-hostops-workspace" in ingress
    assert "github.event.comment.body == format('APPROVE WRITE #{0}', github.event.issue.number)" in ingress
    # HostOps must not compete for the EF02 runner label any more.
    assert "runs-on: [self-hosted, Windows, ai-commerce-hostops]" in ingress
    assert "runs-on: [self-hosted, Windows, ai-commerce-ef02]" not in ingress
    assert "  execute:" in ef02
    assert "runs-on: [self-hosted, Windows, ai-commerce-ef02]" in ef02
    assert "runs-on: [self-hosted, Windows, ai-commerce-hostops]" not in ef02
    assert "group: ef02-issue-" in ef02
    assert '("ai-commerce-ef02-" + $env:GITHUB_RUN_ID' in ef02
    assert "python -m venv" in ef02
    assert "EF02_VENV_ALREADY_EXISTS_RECONCILE" in ef02
    assert "VIRTUAL_ENV=" in ef02
    assert "Scripts\\python.exe" in ef02
    assert "Install isolated per-job EF02 Python environment" in ef02
    # Hosted validation has its own fresh Windows environment; only the two
    # concurrently executable self-hosted jobs need distinct venvs.
    assert "python -m pip install -e" not in ef02.split("  execute:\n", 1)[1]
    # Retained protection against accidental reactivation of retired queues.
    for old in ("hostops-readonly.yml", "hostops-v2.yml", "hostops-v3.yml"):
        assert "uses: ./.github/workflows/" + old not in ingress


def test_windows_powershell_parser_accepts_installer():
    if os.name != "nt":
        return
    shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert shell is not None
    command = (
        "$errorsFound=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "'" + str(SCRIPT).replace("'", "''") + "',"
        "[ref]$null,[ref]$errorsFound)|Out-Null;"
        "if(@($errorsFound).Count -gt 0){$errorsFound|Out-String|Write-Host;exit 1}"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr + result.stdout



def _wakehub(**overrides):
    # Only synthetic read-only receipt flags; no actual Windows operation.
    data = dict(
        hostops_task="READY", hostops_listener="PRESENT",
        engineering_listeners=("PRESENT", "PRESENT"),
        interactive_session_proven=True,
        chrome_ownership="OWNED_CDP_9444",
        source_current=True, unknown_effect=False,
    )
    data.update(overrides)
    return assess_wakehub_preflight(**data)


def test_wakehub_preflight_reuses_verified_9444_and_all_three_listeners():
    result = _wakehub()
    assert isinstance(result, WakeHubPreflight)
    assert result.code == "READY_FOR_ORIGIN_PREPARE"
    assert result.next_action == "REUSE_9444_WITHOUT_RELAUNCH"
    assert result.cdp_port == 9444
    assert result.listener_count == 3
    assert result.ready_for_origin_prepare
    assert not result.start_permitted
    assert not result.wake_send_authorized
    assert not result.effect_attempted
    # The scheduled task can be READY while its separate runner listener
    # is PRESENT. This must never be classified as "offline".
    assert _wakehub(hostops_task="RUNNING") == result


def test_wakehub_preflight_avoids_all_runner_offline_deadlock():
    result = _wakehub(hostops_listener="ABSENT",
                      engineering_listeners=("PRESENT", "PRESENT"))
    assert result.code == "HOLD_EVENT_RECEIVER_UNAVAILABLE"
    assert result.next_action == "RESTORE_RECEIVER_WITH_SEPARATE_NATIVE_AUTHORITY"
    assert result.listener_count == 2
    assert not result.ready_for_origin_prepare
    assert not result.start_permitted
    assert _wakehub(hostops_listener="UNAVAILABLE").code == result.code


def test_wakehub_preflight_no_browser_mutation_when_logged_off_or_unverified():
    no_session = _wakehub(interactive_session_proven=False)
    assert no_session.code == "WAIT_INTERACTIVE_SESSION"
    assert no_session.next_action == "WAIT_FOR_OWNER_LOGON_NO_BROWSER_ACTION"
    absent = _wakehub(chrome_ownership="ABSENT_INDEPENDENTLY_VERIFIED")
    assert absent.code == "WAIT_PRIVATE_CHROME_START_GRANT"
    assert absent.next_action == "REQUEST_BOUNDED_START_AUTHORITY"
    assert not absent.start_permitted
    for state in ("UNVERIFIED", "PROFILE_CONFLICT"):
        r = _wakehub(chrome_ownership=state)
        assert r.code == "HOLD_CHROME_OWNERSHIP"
        assert r.next_action == "VERIFY_EXACT_OWNER_AND_PROFILE"
        assert not r.ready_for_origin_prepare
        assert not r.effect_attempted


def test_wakehub_preflight_requires_fresh_source_and_unknown_reconciliation():
    assert _wakehub(source_current=False).code == "HOLD_SOURCE_DRIFT"
    ambiguous = _wakehub(
        unknown_effect=True, source_current=False, hostops_listener="ABSENT",
        chrome_ownership="PROFILE_CONFLICT")
    assert ambiguous.code == "HOLD_UNKNOWN_EFFECT"
    assert ambiguous.next_action == "RECONCILE_ONLY"
    assert not ambiguous.wake_send_authorized


def test_wakehub_preflight_reports_engineering_listener_degraded_without_killing_core():
    result = _wakehub(engineering_listeners=("ABSENT", "UNAVAILABLE"))
    assert result.code == "READY_DEGRADED_ENGINEERING"
    assert result.listener_count == 1
    assert result.ready_for_origin_prepare
    assert result.next_action == "REUSE_9444_AND_REPORT_ENGINEERING_LISTENERS"
    assert not result.effect_attempted


def test_wakehub_preflight_closed_input_rejects_untrusted_runner_or_browser_fields():
    invalid = [
        {"hostops_task": "NOT_A_TASK_STATE"},
        {"hostops_listener": "RUNNING"},
        {"engineering_listeners": ["PRESENT", "PRESENT"]},
        {"engineering_listeners": ("PRESENT", "PRESENT", "PRESENT")},
        {"engineering_listeners": ("PRESENT", "PRIVILEGED")},
        {"interactive_session_proven": 1},
        {"chrome_ownership": "ANY_BROWSER"},
        {"chrome_ownership": "OWNED_CDP_ANY"},
        {"chrome_ownership": "OWNED_CDP_9333"},
        {"chrome_ownership": "OWNED_CDP_9222"},
        {"source_current": 1},
        {"unknown_effect": "False"},
        {"trusted_human_pause": None},
        {"trusted_human_pause": 0},
        {"trusted_human_pause": 1},
        {"trusted_human_pause": "False"},
        {"trusted_human_pause": "EXPECTED_MANUAL_STOP"},
        {"trusted_human_pause": {"issue": 500, "human_pause": True}},
    ]
    for item in invalid:
        try:
            _wakehub(**item)
        except ValueError as exc:
            assert str(exc) == "WAKEHUB_PREFLIGHT_INPUT_INVALID"
        else:
            raise AssertionError("untrusted WH-01A preflight value was accepted")


def test_wakehub_preflight_is_pure_and_repeats_without_starting_anything():
    first = _wakehub()
    assert _wakehub() == first
    import inspect
    source = inspect.getsource(assess_wakehub_preflight)
    for forbidden in ("Start-Process", "Stop-Process", "subprocess",
                      "os.system", "requests.", "playwright.",
                      "open(", "browser.click("):
        assert forbidden not in source
    assert first.effect_attempted is False


def test_wakehub_supervisor_paused_absent_is_idempotent_and_frozen():
    evidence = dict(trusted_human_pause=True,
                    chrome_ownership="ABSENT_INDEPENDENTLY_VERIFIED")
    paused = _wakehub(**evidence)
    assert paused.code == "PAUSED_BY_HUMAN"
    assert paused.next_action == "NO_EFFECT_KEEP_STOPPED"
    assert paused.cdp_port == 9444
    assert not paused.ready_for_origin_prepare
    assert not paused.start_permitted
    assert not paused.wake_send_authorized
    assert not paused.effect_attempted
    assert _wakehub(**evidence) == _wakehub(**evidence) == paused
    for field, value in (("code", "READY_FOR_ORIGIN_PREPARE"),
                         ("next_action", "REQUEST_BOUNDED_START_AUTHORITY"),
                         ("start_permitted", True),
                         ("wake_send_authorized", True),
                         ("effect_attempted", True)):
        with pytest.raises(FrozenInstanceError):
            setattr(paused, field, value)


@pytest.mark.parametrize(("interactive_session_proven", "chrome_ownership"), [
    (False, "UNVERIFIED"),
    (False, "OWNED_CDP_9444"),
    (False, "ABSENT_INDEPENDENTLY_VERIFIED"),
    (True, "UNVERIFIED"),
    (True, "PROFILE_CONFLICT"),
    (True, "OWNED_CDP_9444"),
])
def test_wakehub_supervisor_pause_never_proves_session_or_ownership(
    interactive_session_proven, chrome_ownership,
):
    # Keep-stop advice needs no desktop/browser authority. Pause is not
    # evidence that this owner logged on or owns an existing Chrome process.
    paused = _wakehub(trusted_human_pause=True,
                      interactive_session_proven=interactive_session_proven,
                      chrome_ownership=chrome_ownership)
    assert paused.code == "PAUSED_BY_HUMAN"
    assert paused.next_action == "NO_EFFECT_KEEP_STOPPED"
    assert not paused.ready_for_origin_prepare
    assert not paused.start_permitted
    assert not paused.wake_send_authorized
    assert not paused.effect_attempted


def test_wakehub_supervisor_explicit_resume_still_needs_private_native_grant():
    evidence = dict(chrome_ownership="ABSENT_INDEPENDENTLY_VERIFIED")
    paused = _wakehub(trusted_human_pause=True, **evidence)
    resumed = _wakehub(trusted_human_pause=False, **evidence)
    assert resumed == _wakehub(**evidence)  # Backward-compatible default.
    assert resumed.code == "WAIT_PRIVATE_CHROME_START_GRANT"
    assert resumed.next_action == "REQUEST_BOUNDED_START_AUTHORITY"
    assert not resumed.ready_for_origin_prepare
    assert not resumed.start_permitted
    assert not resumed.wake_send_authorized
    assert not resumed.effect_attempted
    assert _wakehub(trusted_human_pause=True, **evidence) == paused
    assert _wakehub(trusted_human_pause=False) == _wakehub()


@pytest.mark.parametrize(("overrides", "code", "action"), [
    # A pause cannot resolve a lost effect receipt, refresh stale source, or
    # restore the receiver. Those independent HOLDs must remain visible.
    (dict(unknown_effect=True, source_current=False, hostops_listener="ABSENT"),
     "HOLD_UNKNOWN_EFFECT", "RECONCILE_ONLY"),
    (dict(source_current=False, hostops_listener="ABSENT"),
     "HOLD_SOURCE_DRIFT", "REFRESH_SOURCE_EVIDENCE"),
    (dict(hostops_listener="ABSENT"), "HOLD_EVENT_RECEIVER_UNAVAILABLE",
     "RESTORE_RECEIVER_WITH_SEPARATE_NATIVE_AUTHORITY"),
    (dict(hostops_listener="UNAVAILABLE"), "HOLD_EVENT_RECEIVER_UNAVAILABLE",
     "RESTORE_RECEIVER_WITH_SEPARATE_NATIVE_AUTHORITY"),
])
def test_wakehub_supervisor_holds_take_precedence_over_trusted_pause(
    overrides, code, action,
):
    result = _wakehub(trusted_human_pause=True,
                      interactive_session_proven=False,
                      chrome_ownership="ABSENT_INDEPENDENTLY_VERIFIED",
                      **overrides)
    assert result.code == code
    assert result.next_action == action
    assert not result.ready_for_origin_prepare
    assert not result.start_permitted
    assert not result.wake_send_authorized
    assert not result.effect_attempted


@pytest.mark.parametrize("chrome_ownership", [
    "OWNED_CDP_9444", "ABSENT_INDEPENDENTLY_VERIFIED",
    "UNVERIFIED", "PROFILE_CONFLICT",
])
def test_wakehub_supervisor_resume_requires_proven_owner_desktop(chrome_ownership):
    # False covers wrong, locked, absent and ambiguous owner desktops; even
    # owned/absent Chrome evidence cannot substitute for this independent gate.
    result = _wakehub(trusted_human_pause=False,
                      interactive_session_proven=False,
                      chrome_ownership=chrome_ownership)
    assert result.code == "WAIT_INTERACTIVE_SESSION"
    assert result.next_action == "WAIT_FOR_OWNER_LOGON_NO_BROWSER_ACTION"
    assert not result.ready_for_origin_prepare
    assert not result.start_permitted


def test_wakehub_supervisor_decisions_have_no_external_effects_or_legacy_port(monkeypatch):
    cases = [
        (dict(unknown_effect=True), "HOLD_UNKNOWN_EFFECT"),
        (dict(source_current=False), "HOLD_SOURCE_DRIFT"),
        (dict(hostops_listener="ABSENT"), "HOLD_EVENT_RECEIVER_UNAVAILABLE"),
        (dict(trusted_human_pause=True,
              chrome_ownership="ABSENT_INDEPENDENTLY_VERIFIED"), "PAUSED_BY_HUMAN"),
        (dict(interactive_session_proven=False), "WAIT_INTERACTIVE_SESSION"),
        (dict(chrome_ownership="PROFILE_CONFLICT"), "HOLD_CHROME_OWNERSHIP"),
        (dict(chrome_ownership="UNVERIFIED"), "HOLD_CHROME_OWNERSHIP"),
        (dict(chrome_ownership="ABSENT_INDEPENDENTLY_VERIFIED"),
         "WAIT_PRIVATE_CHROME_START_GRANT"),
        (dict(engineering_listeners=("ABSENT", "UNAVAILABLE")),
         "READY_DEGRADED_ENGINEERING"),
        ({}, "READY_FOR_ORIGIN_PREPARE"),
    ]

    def forbidden(*args, **kwargs):
        raise AssertionError("Supervisor attempted an external effect")

    # Tripwires run only around the pure decisions. No Chrome, Windows task,
    # network/Wake, process launch or profile/file access occurs in this test.
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(os, "system", forbidden)
        guard.setattr(socket, "socket", forbidden)
        for overrides, expected_code in cases:
            result = _wakehub(**overrides)
            assert result == _wakehub(**overrides)
            assert result.code == expected_code
            assert result.cdp_port == 9444  # Never route to legacy D5 9333.
            assert result.start_permitted is False
            assert result.wake_send_authorized is False
            assert result.effect_attempted is False


# WH-02A fixtures are explicitly synthetic. No fixture is a host measurement,
# a savings claim, native deployment evidence or an accepted readiness target.
def _synthetic_baseline(**overrides):
    data = dict(
        observation=WakeHubObservationState.INDEPENDENTLY_OBSERVED,
        continuous_idle_window_proven=True,
        idle_samples=tuple(
            WakeHubIdleSample(t, 0.5, 2**30, 1234, (10, 20))
            for t in (0, 900, 1800)
        ),
        warm_samples=tuple(
            WakeHubReadinessSample(i, (20 - i) * 10) for i in range(20)
        ),
        cold_samples=tuple(
            WakeHubReadinessSample(i, (20 - i) * 100) for i in range(20)
        ),
    )
    data.update(overrides)
    return WakeHubBaseline(**data)


def _core_evidence(**overrides):
    data = dict(
        source_current=True, unknown_effect=False, lost_effect_receipt=False,
        hostops_task=WakeHubTaskState.READY,
        hostops_listener=WakeHubListenerState.PRESENT,
        engineering_listeners=(WakeHubListenerState.PRESENT,
                               WakeHubListenerState.PRESENT),
        queue_state=WakeHubWorkState.CLEAR, pending_work=WakeHubWorkState.CLEAR,
        outstanding_send=WakeHubWorkState.CLEAR,
        outstanding_signer=WakeHubWorkState.CLEAR,
        outstanding_lease=WakeHubWorkState.CLEAR,
        pending_wake=WakeHubWorkState.CLEAR,
        trusted_human_pause=False, interactive_session_proven=True,
        chrome_ownership=WakeHubChromeOwnership.OWNED_CDP_9444,
        baseline=_synthetic_baseline(),
    )
    data.update(overrides)
    return WakeHubCoreIdleEvidence(**data)


def _assert_wh02a_no_authority(plan):
    for name in (
        "effect_attempted", "start_permitted", "stop_permitted", "kill_permitted",
        "wake_send_authorized", "chrome_operation_authorized",
        "native_operation_authorized", "receiver_disable_authorized",
    ):
        assert getattr(plan, name) is False
        assert next(item for item in fields(plan) if item.name == name).init is False
    assert plan.resource_mode == resource_policy() == "OBSERVE_ONLY_NO_CPU_RAM_LIMITS"
    assert plan.native_owner_proof == "UNRESOLVED"
    assert plan.savings == "NOT_MEASURED"


def test_wh02a_synthetic_readiness_percentiles_are_only_measured_input_values():
    evidence = _synthetic_baseline()
    result = plan_wakehub_measurements(evidence)
    assert result == plan_wakehub_measurements(evidence)
    assert result.code is WakeHubMeasurementCode.MEASURED_SAMPLES_COMPLETE
    assert result.idle_duration_seconds == 1800
    assert result.idle_sample_count == 3
    assert result.warm.sample_count == result.cold.sample_count == 20
    assert (result.warm.p50_ms, result.warm.p95_ms) == (100, 190)
    assert (result.cold.p50_ms, result.cold.p95_ms) == (1000, 1900)
    assert result.warm.method == result.cold.method == "EMPIRICAL_NEAREST_RANK"
    changed = plan_wakehub_measurements(replace(
        evidence, warm_samples=(WakeHubReadinessSample(0, 7.5),)))
    assert changed.warm.sample_count == 1
    assert changed.warm.p50_ms == changed.warm.p95_ms == 7.5
    assert changed.cold == result.cold  # No warm/cold mixing or hidden history.
    assert plan_wakehub_measurements(evidence) == result
    _assert_wh02a_no_authority(result)


@pytest.mark.parametrize(("values", "p50", "p95"), [
    ((0,), 0, 0), ((1, 2), 1, 2), ((3, 1, 2), 2, 3),
    (tuple(range(1, 22)), 11, 20),
])
def test_wh02a_empirical_nearest_rank_rounding_is_explicit(values, p50, p95):
    baseline = _synthetic_baseline(warm_samples=tuple(
        WakeHubReadinessSample(i, value) for i, value in enumerate(values)))
    result = plan_wakehub_measurements(baseline)
    assert (result.warm.p50_ms, result.warm.p95_ms) == (p50, p95)
    assert result.warm.sample_count == len(values)
    assert result.warm.p50_ms in values and result.warm.p95_ms in values


def test_wh02a_no_observations_has_no_numeric_claims():
    for observation in WakeHubObservationState:
        baseline = WakeHubBaseline(observation)
        result = plan_wakehub_measurements(baseline)
        assert result.code is WakeHubMeasurementCode.NOT_MEASURED
        assert result.idle_sample_count is result.idle_duration_seconds is None
        assert result.warm is result.cold is None
        _assert_wh02a_no_authority(result)
        plan = assess_wakehub_core_idle(_core_evidence(baseline=baseline))
        assert plan.code is WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE
        assert plan.core_logical_idle_eligible
        assert plan.keep_event_receiver_alive


@pytest.mark.parametrize("overrides", [
    dict(observation=WakeHubObservationState.UNVERIFIED),
    dict(continuous_idle_window_proven=False),
    dict(idle_samples=()), dict(warm_samples=()), dict(cold_samples=()),
    dict(idle_samples=(WakeHubIdleSample(1800, 1, 100, 10, (1,)),)),
    dict(idle_samples=(WakeHubIdleSample(0, 1, 100, 10, (1,)),
                       WakeHubIdleSample(1799.999, 1, 100, 10, (1,)))),
    dict(idle_samples=(WakeHubIdleSample(100, 1, 100, 10, (1,)),
                       WakeHubIdleSample(1800, 1, 100, 10, (1,)))),
])
def test_wh02a_insufficient_30_minute_measurements_do_not_gate_core_idle(overrides):
    baseline = _synthetic_baseline(**overrides)
    result = plan_wakehub_measurements(baseline)
    assert result.code is WakeHubMeasurementCode.INSUFFICIENT_EVIDENCE
    assert result.idle_sample_count is result.idle_duration_seconds is None
    assert result.warm is result.cold is None
    plan = assess_wakehub_core_idle(_core_evidence(baseline=baseline))
    assert plan.code is WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE
    assert plan.core_logical_idle_eligible
    _assert_wh02a_no_authority(plan)


@pytest.mark.parametrize("hostops_task", [
    value for value in WakeHubTaskState if value is not WakeHubTaskState.QUEUED
])
@pytest.mark.parametrize("engineering_listeners", [
    (left, right) for left in WakeHubListenerState for right in WakeHubListenerState
])
def test_wh02a_only_verified_hostops_receiver_allows_logical_idle(
    hostops_task, engineering_listeners,
):
    evidence = _core_evidence(hostops_task=hostops_task,
                              engineering_listeners=engineering_listeners)
    result = assess_wakehub_core_idle(evidence)
    assert type(result) is WakeHubCoreIdlePlan
    assert result == assess_wakehub_core_idle(evidence)
    assert result.code is WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE
    assert result.core_logical_idle_eligible
    assert result.receiver_present and result.keep_event_receiver_alive
    assert result.desktop_ready_for_planning
    assert result.idle_scope == "HEADLESS_LOGICAL_CORE_ONLY"
    assert result.listener_count == 1 + sum(
        value is WakeHubListenerState.PRESENT for value in engineering_listeners)
    _assert_wh02a_no_authority(result)
    _assert_wh02a_no_authority(result.measurements)
    # Task status cannot substitute for a listener, even when both Engineering
    # listeners are online. An absent receiver gets no self-wake/start advice.
    for absent in (WakeHubListenerState.ABSENT, WakeHubListenerState.UNAVAILABLE):
        held = assess_wakehub_core_idle(replace(evidence, hostops_listener=absent))
        assert held.code is WakeHubCoreIdleCode.WAIT_RECEIVER
        assert not held.receiver_present and not held.core_logical_idle_eligible
        assert held.keep_event_receiver_alive
        _assert_wh02a_no_authority(held)


@pytest.mark.parametrize(("overrides", "code"), [
    (dict(unknown_effect=True, lost_effect_receipt=True, source_current=False,
          hostops_listener=WakeHubListenerState.ABSENT, trusted_human_pause=True),
     WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT),
    (dict(lost_effect_receipt=True), WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT),
    (dict(source_current=False, hostops_listener=WakeHubListenerState.ABSENT,
          trusted_human_pause=True), WakeHubCoreIdleCode.HOLD_SOURCE_DRIFT),
    (dict(hostops_listener=WakeHubListenerState.ABSENT, trusted_human_pause=True),
     WakeHubCoreIdleCode.WAIT_RECEIVER),
    (dict(trusted_human_pause=True), WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
    (dict(hostops_task=WakeHubTaskState.QUEUED),
     WakeHubCoreIdleCode.KEEP_ACTIVE_PENDING),
    (dict(interactive_session_proven=False),
     WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
    (dict(chrome_ownership=WakeHubChromeOwnership.UNVERIFIED),
     WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
    (dict(chrome_ownership=WakeHubChromeOwnership.PROFILE_CONFLICT),
     WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
    (dict(chrome_ownership=WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED),
     WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
])
def test_wh02a_safety_fences_are_idempotent(overrides, code):
    evidence = _core_evidence(**overrides)
    result = assess_wakehub_core_idle(evidence)
    assert result == assess_wakehub_core_idle(evidence)
    assert result.code is code
    assert result.core_logical_idle_eligible is (
        code is WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE)
    assert result.paused_by_human is evidence.trusted_human_pause
    assert result.keep_event_receiver_alive
    _assert_wh02a_no_authority(result)


@pytest.mark.parametrize("work_field", [
    "queue_state", "pending_work", "outstanding_send", "outstanding_signer",
    "outstanding_lease", "pending_wake",
])
@pytest.mark.parametrize(("state", "code"), [
    (WakeHubWorkState.PENDING, WakeHubCoreIdleCode.KEEP_ACTIVE_PENDING),
    (WakeHubWorkState.UNKNOWN, WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT),
])
def test_wh02a_queued_jobs_send_signer_duplicate_wake_and_lease_block_idle(
    work_field, state, code,
):
    evidence = _core_evidence(**{work_field: state})
    result = assess_wakehub_core_idle(evidence)
    assert result.code is code
    assert not result.core_logical_idle_eligible
    assert result == assess_wakehub_core_idle(evidence)
    _assert_wh02a_no_authority(result)
    # Headless and paused operation cannot hide pending or unknown work, even
    # without measurements and with every Engineering listener offline.
    for paused in (False, True):
        headless = assess_wakehub_core_idle(replace(
            evidence, trusted_human_pause=paused,
            interactive_session_proven=False,
            chrome_ownership=WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED,
            engineering_listeners=(WakeHubListenerState.ABSENT,
                                   WakeHubListenerState.UNAVAILABLE),
            baseline=WakeHubBaseline(WakeHubObservationState.NOT_MEASURED)))
        assert headless.code is code
        assert not headless.core_logical_idle_eligible
        assert not headless.desktop_ready_for_planning
        assert headless.paused_by_human is paused
        assert headless.receiver_present and headless.keep_event_receiver_alive
        _assert_wh02a_no_authority(headless)


@pytest.mark.parametrize("chrome_ownership", list(WakeHubChromeOwnership))
def test_wh02a_core_inbox_survives_absent_desktop_and_human_pause(chrome_ownership):
    evidence = _core_evidence(interactive_session_proven=False,
                              chrome_ownership=chrome_ownership)
    paused = assess_wakehub_core_idle(replace(evidence, trusted_human_pause=True))
    resumed = assess_wakehub_core_idle(evidence)
    assert paused.code is resumed.code is WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE
    assert paused.paused_by_human is True
    assert resumed.paused_by_human is False
    for result in (paused, resumed):
        assert result.keep_event_receiver_alive and result.receiver_present
        assert not result.desktop_ready_for_planning
        assert result.core_logical_idle_eligible
        _assert_wh02a_no_authority(result)
    # Explicitly releasing a pause cannot grant private Chrome start either.
    absent = _core_evidence(
        chrome_ownership=WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED)
    assert assess_wakehub_core_idle(absent).code is (
        WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE)
    assert not assess_wakehub_core_idle(absent).desktop_ready_for_planning
    _assert_wh02a_no_authority(assess_wakehub_core_idle(absent))


@pytest.mark.parametrize("interactive_session_proven", [False, True])
@pytest.mark.parametrize("chrome_ownership", list(WakeHubChromeOwnership))
@pytest.mark.parametrize("trusted_human_pause", [False, True])
@pytest.mark.parametrize("baseline_kind", [
    "not_measured", "insufficient_idle", "missing_readiness", "complete",
])
def test_wh02a_headless_core_idle_is_independent_of_desktop_pause_and_measurements(
    interactive_session_proven, chrome_ownership, trusted_human_pause, baseline_kind,
):
    baseline = {
        "not_measured": WakeHubBaseline(WakeHubObservationState.NOT_MEASURED),
        "insufficient_idle": _synthetic_baseline(idle_samples=(
            WakeHubIdleSample(0, 1, 100, 10, (1,)),
            WakeHubIdleSample(1799, 1, 100, 10, (1,)))),
        "missing_readiness": _synthetic_baseline(warm_samples=(), cold_samples=()),
        "complete": _synthetic_baseline(),
    }[baseline_kind]
    evidence = _core_evidence(
        interactive_session_proven=interactive_session_proven,
        chrome_ownership=chrome_ownership, trusted_human_pause=trusted_human_pause,
        baseline=baseline,
        engineering_listeners=(WakeHubListenerState.ABSENT,
                               WakeHubListenerState.UNAVAILABLE))
    result = assess_wakehub_core_idle(evidence)
    assert result.code is WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE
    assert result.core_logical_idle_eligible
    assert result.receiver_present and result.keep_event_receiver_alive
    assert result.listener_count == 1
    assert result.paused_by_human is trusted_human_pause
    assert result.desktop_ready_for_planning is (
        interactive_session_proven and not trusted_human_pause
        and chrome_ownership is WakeHubChromeOwnership.OWNED_CDP_9444)
    assert result.measurements == plan_wakehub_measurements(baseline)
    if baseline_kind != "complete":
        assert result.measurements.code is (
            WakeHubMeasurementCode.NOT_MEASURED if baseline_kind == "not_measured"
            else WakeHubMeasurementCode.INSUFFICIENT_EVIDENCE)
        assert result.measurements.warm is result.measurements.cold is None
        assert result.measurements.idle_sample_count is None
        assert result.measurements.idle_duration_seconds is None
    _assert_wh02a_no_authority(result)
    _assert_wh02a_no_authority(result.measurements)
    # Each independent core safety gate still wins in every desktop context.
    for overrides, code in (
        (dict(unknown_effect=True), WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT),
        (dict(lost_effect_receipt=True), WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT),
        (dict(source_current=False), WakeHubCoreIdleCode.HOLD_SOURCE_DRIFT),
        (dict(hostops_listener=WakeHubListenerState.ABSENT),
         WakeHubCoreIdleCode.WAIT_RECEIVER),
        (dict(hostops_listener=WakeHubListenerState.UNAVAILABLE),
         WakeHubCoreIdleCode.WAIT_RECEIVER),
        (dict(hostops_task=WakeHubTaskState.QUEUED),
         WakeHubCoreIdleCode.KEEP_ACTIVE_PENDING),
    ):
        held = assess_wakehub_core_idle(replace(evidence, **overrides))
        assert held.code is code
        assert not held.core_logical_idle_eligible
        assert held.paused_by_human is trusted_human_pause
        assert held.keep_event_receiver_alive
        _assert_wh02a_no_authority(held)


@pytest.mark.parametrize("overrides", [
    dict(source_current=1), dict(unknown_effect="False"),
    dict(lost_effect_receipt=0), dict(trusted_human_pause=None),
    dict(interactive_session_proven=1), dict(hostops_task="READY"),
    dict(hostops_listener="PRESENT"), dict(hostops_listener="OFFLINE"),
    dict(hostops_listener=WakeHubWorkState.CLEAR),
    dict(engineering_listeners=[WakeHubListenerState.PRESENT] * 2),
    dict(engineering_listeners=()),
    dict(engineering_listeners=(WakeHubListenerState.PRESENT,) * 3),
    dict(engineering_listeners=(WakeHubListenerState.PRESENT, "PRESENT")),
    dict(queue_state=None), dict(pending_work="CLEAR"),
    dict(outstanding_send=False), dict(outstanding_signer={"token": "synthetic-secret"}),
    dict(outstanding_lease="CLEAR"), dict(pending_wake=0),
    dict(chrome_ownership="OWNED_CDP_9444"),
    dict(chrome_ownership="OWNED_CDP_9333"),
    dict(chrome_ownership="OWNED_CDP_9222"),
    dict(chrome_ownership=r"C:\private\synthetic-profile"),
    dict(baseline={"secret": "synthetic-secret"}),
])
def test_wh02a_closed_core_schema_rejects_missing_or_wrong_enums_privately(overrides):
    with pytest.raises(WakeHubCoreInputError) as caught:
        assess_wakehub_core_idle(_core_evidence(**overrides))
    assert caught.value.args == ("WAKEHUB_CORE_INPUT_INVALID",)
    assert caught.value.__context__ is None


def test_wh02a_missing_fields_and_unknown_schema_never_get_a_default_grant():
    with pytest.raises(TypeError):
        WakeHubCoreIdleEvidence()
    with pytest.raises(TypeError):
        WakeHubBaseline(WakeHubObservationState.NOT_MEASURED, secret="synthetic")
    for value in (None, {}, "synthetic-secret", r"C:\private\profile"):
        with pytest.raises(WakeHubCoreInputError, match="^WAKEHUB_CORE_INPUT_INVALID$"):
            assess_wakehub_core_idle(value)
        with pytest.raises(WakeHubCoreInputError, match="^WAKEHUB_CORE_INPUT_INVALID$"):
            plan_wakehub_measurements(value)


def test_wh02a_only_canonical_enum_members_and_builtin_numbers_are_accepted():
    # A string-backed enum's type alone does not guarantee a declared member.
    for name, enum_type, value in (
        ("hostops_task", WakeHubTaskState, "READY"),
        ("hostops_listener", WakeHubListenerState, "PRESENT"),
        ("queue_state", WakeHubWorkState, "CLEAR"),
        ("chrome_ownership", WakeHubChromeOwnership, "OWNED_CDP_9444"),
    ):
        forged = str.__new__(enum_type, value)
        with pytest.raises(WakeHubCoreInputError):
            assess_wakehub_core_idle(_core_evidence(**{name: forged}))
    with pytest.raises(WakeHubCoreInputError):
        plan_wakehub_measurements(_synthetic_baseline(observation=str.__new__(
            WakeHubObservationState, "INDEPENDENTLY_OBSERVED")))

    class UntrustedNumber(float):
        def __le__(self, other):
            raise AssertionError("Untrusted numeric operator was invoked")

        def __ge__(self, other):
            raise AssertionError("Untrusted numeric operator was invoked")

    with pytest.raises(WakeHubCoreInputError) as caught:
        plan_wakehub_measurements(_synthetic_baseline(
            warm_samples=(WakeHubReadinessSample(0, UntrustedNumber(1)),)))
    assert caught.value.args == ("WAKEHUB_CORE_INPUT_INVALID",)


def test_wh02a_schema_upper_bounds_are_accepted_without_resource_policy_changes():
    sample = WakeHubIdleSample(0, 100, 2**53 - 1, 2**32 - 1,
                               tuple(range(1, WAKEHUB_MAX_PIDS_PER_SAMPLE + 1)))
    baseline = _synthetic_baseline(
        idle_samples=(sample, replace(sample, elapsed_seconds=1800)),
        warm_samples=tuple(WakeHubReadinessSample(i, 2**53 - 1)
                           for i in range(WAKEHUB_MAX_SAMPLES)),
    )
    result = plan_wakehub_measurements(baseline)
    assert result.code is WakeHubMeasurementCode.MEASURED_SAMPLES_COMPLETE
    assert result.warm.sample_count == WAKEHUB_MAX_SAMPLES
    assert result.warm.p50_ms == result.warm.p95_ms == 2**53 - 1
    _assert_wh02a_no_authority(result)


@pytest.mark.parametrize(("name", "value"), [
    ("elapsed_seconds", True), ("elapsed_seconds", -1),
    ("elapsed_seconds", float("nan")), ("elapsed_seconds", float("inf")),
    ("elapsed_seconds", 10**1000), ("elapsed_seconds", 2**53),
    ("cpu_percent", True), ("cpu_percent", -0.1), ("cpu_percent", 100.1),
    ("cpu_percent", float("nan")), ("cpu_percent", float("-inf")),
    ("cpu_percent", 10**1000),
    ("cpu_percent", "synthetic-secret"),
    ("ram_bytes", False), ("ram_bytes", 1.5), ("ram_bytes", -1),
    ("ram_bytes", 2**53), ("ram_bytes", float("inf")),
    ("ram_bytes", 10**1000),
    ("handles", True), ("handles", -1), ("handles", 2**32),
    ("handles", float("nan")),
    ("handles", 10**1000),
    ("pids", ()), ("pids", [1, 2]), ("pids", (True,)),
    ("pids", (0,)), ("pids", (-1,)), ("pids", (2**32,)),
    ("pids", (10**1000,)),
    ("pids", (1, 1)), ("pids", ("synthetic-secret",)),
    ("pids", tuple(range(1, WAKEHUB_MAX_PIDS_PER_SAMPLE + 2))),
])
def test_wh02a_invalid_host_metrics_raise_typed_private_error(name, value):
    baseline = _synthetic_baseline()
    invalid = replace(baseline.idle_samples[0], **{name: value})
    baseline = replace(baseline, idle_samples=(invalid, *baseline.idle_samples[1:]))
    with pytest.raises(WakeHubCoreInputError) as caught:
        plan_wakehub_measurements(baseline)
    assert str(caught.value) == "WAKEHUB_CORE_INPUT_INVALID"


@pytest.mark.parametrize("value", [
    True, -1, float("nan"), float("inf"), float("-inf"),
    10**1000, 2**53, "synthetic-secret", r"C:\private\receipt", None,
])
@pytest.mark.parametrize("stream", ["warm_samples", "cold_samples"])
@pytest.mark.parametrize("name", ["elapsed_seconds", "duration_ms"])
def test_wh02a_readiness_numeric_failures_are_closed(stream, name, value):
    invalid = replace(WakeHubReadinessSample(0, 10), **{name: value})
    baseline = _synthetic_baseline(**{stream: (invalid,)})
    with pytest.raises(WakeHubCoreInputError) as caught:
        plan_wakehub_measurements(baseline)
    assert caught.value.args == ("WAKEHUB_CORE_INPUT_INVALID",)


@pytest.mark.parametrize("stream", ["idle_samples", "warm_samples", "cold_samples"])
def test_wh02a_unordered_duplicate_huge_or_open_sample_arrays_fail_closed(stream):
    baseline = _synthetic_baseline()
    samples = getattr(baseline, stream)
    invalid_sets = (
        list(samples), tuple(reversed(samples)), (samples[0], samples[0]),
        (samples[0],) * (WAKEHUB_MAX_SAMPLES + 1), (None,),
        ({"path": r"C:\private\sample", "token": "synthetic-secret"},),
    )
    for invalid in invalid_sets:
        with pytest.raises(WakeHubCoreInputError) as caught:
            plan_wakehub_measurements(replace(baseline, **{stream: invalid}))
        assert caught.value.args == ("WAKEHUB_CORE_INPUT_INVALID",)


@pytest.mark.parametrize("overrides", [
    dict(observation="INDEPENDENTLY_OBSERVED"),
    dict(observation=WakeHubMeasurementCode.NOT_MEASURED),
    dict(observation=WakeHubObservationState.NOT_MEASURED),
    dict(continuous_idle_window_proven=1),
])
def test_wh02a_measurement_provenance_and_coverage_types_are_strict(overrides):
    with pytest.raises(WakeHubCoreInputError):
        plan_wakehub_measurements(_synthetic_baseline(**overrides))
    with pytest.raises(WakeHubCoreInputError):
        plan_wakehub_measurements(WakeHubBaseline(
            WakeHubObservationState.NOT_MEASURED, continuous_idle_window_proven=True))


def test_wh02a_outputs_and_nested_evidence_are_frozen_and_cannot_enable_effects():
    evidence = _core_evidence()
    result = assess_wakehub_core_idle(evidence)
    for item, name, value in (
        (evidence, "source_current", False),
        (evidence.baseline, "continuous_idle_window_proven", False),
        (evidence.baseline.idle_samples[0], "cpu_percent", 1),
        (evidence.baseline.warm_samples[0], "duration_ms", 1),
        (result, "code", WakeHubCoreIdleCode.WAIT_RECEIVER),
        (result, "paused_by_human", True),
        (result.measurements, "idle_duration_seconds", 0),
        (result.measurements.warm, "p95_ms", 0),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(item, name, value)
    for name in (
        "start_permitted", "stop_permitted", "kill_permitted", "effect_attempted",
        "wake_send_authorized", "chrome_operation_authorized",
        "native_operation_authorized", "receiver_disable_authorized",
    ):
        for plan in (result, result.measurements):
            with pytest.raises(FrozenInstanceError):
                setattr(plan, name, True)
            # Python 3.14 uses TypeError for init=False replacement; older
            # versions use ValueError. Both must reject the specific field.
            with pytest.raises(
                (TypeError, ValueError),
                match=rf"^field {name} is declared with init=False,",
            ):
                replace(plan, **{name: True})
            assert getattr(plan, name) is False
    assert assess_wakehub_core_idle(evidence) == result


def test_wh02a_all_decisions_and_exceptions_have_no_os_network_or_file_effects(monkeypatch):
    cases = [
        (dict(unknown_effect=True), WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT),
        (dict(lost_effect_receipt=True), WakeHubCoreIdleCode.HOLD_UNKNOWN_EFFECT),
        (dict(source_current=False), WakeHubCoreIdleCode.HOLD_SOURCE_DRIFT),
        (dict(hostops_listener=WakeHubListenerState.ABSENT),
         WakeHubCoreIdleCode.WAIT_RECEIVER),
        (dict(trusted_human_pause=True), WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
        (dict(pending_wake=WakeHubWorkState.PENDING),
         WakeHubCoreIdleCode.KEEP_ACTIVE_PENDING),
        (dict(interactive_session_proven=False),
         WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
        (dict(chrome_ownership=WakeHubChromeOwnership.PROFILE_CONFLICT),
         WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
        (dict(chrome_ownership=WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED),
         WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
        (dict(baseline=WakeHubBaseline(WakeHubObservationState.NOT_MEASURED)),
         WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
        (dict(engineering_listeners=(WakeHubListenerState.ABSENT,
                                    WakeHubListenerState.UNAVAILABLE)),
         WakeHubCoreIdleCode.CORE_LOGICAL_IDLE_ELIGIBLE),
    ]

    def forbidden(*args, **kwargs):
        raise AssertionError("WH-02A attempted an external effect")

    with monkeypatch.context() as guard:
        for owner, name in (
            (builtins, "open"), (Path, "open"), (Path, "write_text"),
            (Path, "write_bytes"), (subprocess, "Popen"), (os, "system"),
            (os, "kill"), (os, "remove"), (os, "rename"), (socket, "socket"),
        ):
            guard.setattr(owner, name, forbidden)
        for overrides, code in cases:
            evidence = _core_evidence(**overrides)
            result = assess_wakehub_core_idle(evidence)
            assert result == assess_wakehub_core_idle(evidence)
            assert result.code is code
            assert result.keep_event_receiver_alive
            _assert_wh02a_no_authority(result)
            _assert_wh02a_no_authority(result.measurements)
        for invalid in (
            _core_evidence(outstanding_send="synthetic-secret"),
            _core_evidence(baseline=_synthetic_baseline(
                warm_samples=(WakeHubReadinessSample(0, float("nan")),))),
            # Even a HOLD must validate/reject malformed private input.
            _core_evidence(unknown_effect=True, baseline={"token": "synthetic-secret"}),
        ):
            for _ in range(2):
                with pytest.raises(WakeHubCoreInputError) as caught:
                    assess_wakehub_core_idle(invalid)
                assert caught.value.args == ("WAKEHUB_CORE_INPUT_INVALID",)


# All WH-04A positive facts below are synthetic private-root projections.
# They prove policy behavior, never a native owner, route, account or grant.
def _smart_evidence(**overrides):
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    values = dict(
        core=_core_evidence(baseline=WakeHubBaseline(
            WakeHubObservationState.NOT_MEASURED)),
        projection=current, source=current, listeners=current,
        desktop=WakeHubDesktopState.OWNER_INTERACTIVE, session_owner=current,
        browser=WakeHubSmartBrowserEvidence(
            ownership=WakeHubChromeOwnership.OWNED_CDP_9444,
            host=WakeHubEndpointHost.IPV4_LOOPBACK, port=9444, pid=123,
            pid_creation=current, executable=current, profile=current,
            port_owner=current, identity_current=current, retained_account=current,
        ),
        origin=WakeHubSmartOriginEvidence(
            event=WakeHubEventState.UNIQUE_CURRENT, issuer=current,
            owner=current, route=current, generation=current,
            consent=WakeHubConsentState.CURRENT,
        ),
    )
    values.update(overrides)
    return WakeHubSmartReadinessEvidence(**values)


def _smart_case(section, **changes):
    evidence = _smart_evidence()
    return replace(evidence, **{section: replace(getattr(evidence, section), **changes)})


def _assert_wh04a_no_effect(plan):
    _assert_wh02a_no_authority(plan)
    assert plan.scope == "PURE_SYNTHETIC_POLICY_ONLY"
    assert plan.keep_event_receiver_alive is True
    for name in ("native_operations", "start", "stop", "task", "chrome", "send",
                 "plugin", "prepare_authorized"):
        assert getattr(plan, name) is False
    assert plan.native_owner_proof == "UNRESOLVED"
    assert plan.savings == "NOT_MEASURED"
    assert plan.resource_mode == "OBSERVE_ONLY_NO_CPU_RAM_LIMITS"


def test_wh04a_synthetic_ready_is_deterministic_advice_without_measurements_or_effects():
    evidence = _smart_evidence()
    plan = assess_wakehub_smart_readiness(evidence)
    assert plan == assess_wakehub_smart_readiness(evidence)
    assert plan.code is WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY
    assert plan.core_status is WakeHubSmartCoreStatus.CORE_RECEIVE_IDLE_HEALTHY
    assert plan.browser_advice is WakeHubBrowserAdvice.REUSE_VERIFIED_OWNER
    assert plan.listener_count == len(planned_topology()) == 3
    assert not plan.paused_by_human
    _assert_wh04a_no_effect(plan)
    assert "REUSE_EFFECT_EXECUTED" not in repr(plan)
    # Optional historical samples cannot change the categorical policy output.
    measured = replace(evidence, core=replace(evidence.core, baseline=_synthetic_baseline()))
    assert assess_wakehub_smart_readiness(measured) == plan


@pytest.mark.parametrize("desktop", list(WakeHubDesktopState))
@pytest.mark.parametrize("paused", [False, True])
def test_wh04a_headless_receive_idle_survives_desktop_lock_absence_session_zero_pause(
    desktop, paused,
):
    evidence = _smart_evidence(desktop=desktop)
    evidence = replace(evidence, core=replace(evidence.core, trusted_human_pause=paused))
    plan = assess_wakehub_smart_readiness(evidence)
    assert plan.core_status is WakeHubSmartCoreStatus.CORE_RECEIVE_IDLE_HEALTHY
    if paused:
        assert plan.code is WakeHubSmartReadinessCode.HOLD_HUMAN_PAUSED
    elif desktop is WakeHubDesktopState.OWNER_INTERACTIVE:
        assert plan.code is WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY
    else:
        assert plan.code is WakeHubSmartReadinessCode.WAIT_INTERACTIVE_SESSION
    assert plan.paused_by_human is paused
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize("listener", [WakeHubListenerState.ABSENT,
                                    WakeHubListenerState.UNAVAILABLE])
@pytest.mark.parametrize("task", list(WakeHubTaskState))
def test_wh04a_task_ready_or_running_never_substitutes_for_online_receiver(listener, task):
    plan = assess_wakehub_smart_readiness(_smart_case(
        "core", hostops_task=task, hostops_listener=listener))
    assert plan.code is WakeHubSmartReadinessCode.WAIT_RECEIVER
    assert plan.core_status is WakeHubSmartCoreStatus.WAIT_RECEIVER
    assert plan.browser_advice is WakeHubBrowserAdvice.WAIT
    _assert_wh04a_no_effect(plan)


def test_wh04a_exact_three_registered_runners_with_degraded_engineering_keeps_receiver():
    plan = assess_wakehub_smart_readiness(_smart_case(
        "core", hostops_task=WakeHubTaskState.READY,
        engineering_listeners=(WakeHubListenerState.ABSENT, WakeHubListenerState.UNAVAILABLE)))
    assert len(planned_topology()) == 3
    assert plan.listener_count == 1
    assert plan.code is WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize("name", [
    "queue_state", "pending_work", "outstanding_send", "outstanding_signer",
    "outstanding_lease", "pending_wake",
])
@pytest.mark.parametrize(("state", "expected"), [
    (WakeHubWorkState.UNKNOWN, WakeHubSmartReadinessCode.HOLD_UNKNOWN_EFFECT),
    (WakeHubWorkState.PENDING, WakeHubSmartReadinessCode.WAIT_PENDING_WORK),
])
def test_wh04a_each_unknown_or_pending_effect_blocks_even_with_all_browser_proofs(
    name, state, expected,
):
    plan = assess_wakehub_smart_readiness(_smart_case("core", **{name: state}))
    assert plan.code is expected
    assert plan.core_status.value == expected.value
    assert plan.browser_advice is WakeHubBrowserAdvice.WAIT
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize(("changes", "expected"), [
    (dict(unknown_effect=True), WakeHubSmartReadinessCode.HOLD_UNKNOWN_EFFECT),
    (dict(lost_effect_receipt=True), WakeHubSmartReadinessCode.HOLD_UNKNOWN_EFFECT),
    (dict(source_current=False), WakeHubSmartReadinessCode.HOLD_SOURCE_DRIFT),
    (dict(hostops_task=WakeHubTaskState.QUEUED), WakeHubSmartReadinessCode.WAIT_PENDING_WORK),
    (dict(interactive_session_proven=False), WakeHubSmartReadinessCode.WAIT_INTERACTIVE_SESSION),
])
def test_wh04a_source_receipts_task_and_session_fences(changes, expected):
    plan = assess_wakehub_smart_readiness(_smart_case("core", **changes))
    assert plan.code is expected
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize(("name", "expected"), [
    ("projection", WakeHubSmartReadinessCode.HOLD_PRIVATE_PROJECTION),
    ("source", WakeHubSmartReadinessCode.HOLD_SOURCE_DRIFT),
    ("listeners", WakeHubSmartReadinessCode.WAIT_RECEIVER),
    ("session_owner", WakeHubSmartReadinessCode.HOLD_SESSION_OWNER),
])
@pytest.mark.parametrize("proof", [state for state in WakeHubProofState
                                  if state is not WakeHubProofState.INDEPENDENTLY_CURRENT])
def test_wh04a_independent_private_source_listener_and_owner_proofs_fail_closed(name, expected, proof):
    plan = assess_wakehub_smart_readiness(_smart_evidence(**{name: proof}))
    assert plan.code is expected
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize(("event", "expected"), [
    (WakeHubEventState.ABSENT, WakeHubSmartReadinessCode.WAIT_EVENT),
    (WakeHubEventState.STALE, WakeHubSmartReadinessCode.HOLD_EVENT_STALE),
    (WakeHubEventState.DUPLICATE_AMBIGUOUS, WakeHubSmartReadinessCode.HOLD_EVENT_AMBIGUITY),
])
def test_wh04a_missing_stale_or_ambiguous_duplicate_event_blocks_idle_and_prepare(event, expected):
    plan = assess_wakehub_smart_readiness(_smart_case("origin", event=event))
    assert plan.code is expected
    assert plan.core_status.value == expected.value
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize("section", ["core", "browser"])
@pytest.mark.parametrize("ownership", [state for state in WakeHubChromeOwnership
                                      if state is not WakeHubChromeOwnership.OWNED_CDP_9444])
def test_wh04a_absent_conflicting_unverified_chrome_keeps_headless_core_healthy(section, ownership):
    field_name = "chrome_ownership" if section == "core" else "ownership"
    plan = assess_wakehub_smart_readiness(_smart_case(section, **{field_name: ownership}))
    expected = (WakeHubSmartReadinessCode.WAIT_CHROME
                if ownership is WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED
                else WakeHubSmartReadinessCode.HOLD_CHROME_OWNERSHIP)
    assert plan.code is expected
    assert plan.core_status is WakeHubSmartCoreStatus.CORE_RECEIVE_IDLE_HEALTHY
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize("changes", [
    dict(port=9333), dict(port=0), dict(port=65535),
    dict(host=WakeHubEndpointHost.IPV6_LOOPBACK), dict(host=WakeHubEndpointHost.OTHER),
])
def test_wh04a_only_exact_ipv4_loopback_9444_is_advisable(changes):
    plan = assess_wakehub_smart_readiness(_smart_case("browser", **changes))
    assert plan.code is WakeHubSmartReadinessCode.HOLD_CHROME_ENDPOINT
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize("name", [
    "pid_creation", "executable", "profile", "port_owner", "identity_current", "retained_account",
])
@pytest.mark.parametrize("proof", [state for state in WakeHubProofState
                                  if state is not WakeHubProofState.INDEPENDENTLY_CURRENT])
def test_wh04a_browser_pid_creation_executable_profile_port_account_drift_blocks_reuse(name, proof):
    plan = assess_wakehub_smart_readiness(_smart_case("browser", **{name: proof}))
    expected = (WakeHubSmartReadinessCode.HOLD_BROWSER_ACCOUNT if name == "retained_account"
                else WakeHubSmartReadinessCode.HOLD_CHROME_IDENTITY)
    assert plan.code is expected
    assert plan.core_status is WakeHubSmartCoreStatus.CORE_RECEIVE_IDLE_HEALTHY
    assert plan.browser_advice is WakeHubBrowserAdvice.WAIT
    _assert_wh04a_no_effect(plan)


def test_wh04a_missing_pid_holds_and_maximum_windows_pid_is_a_schema_bound_only():
    plan = assess_wakehub_smart_readiness(_smart_case("browser", pid=0))
    assert plan.code is WakeHubSmartReadinessCode.HOLD_CHROME_IDENTITY
    upper = assess_wakehub_smart_readiness(_smart_case("browser", pid=2**32 - 1))
    assert upper.code is WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY
    _assert_wh04a_no_effect(upper)


@pytest.mark.parametrize(("name", "expected"), [
    ("issuer", WakeHubSmartReadinessCode.HOLD_ORIGIN_ISSUER),
    ("owner", WakeHubSmartReadinessCode.HOLD_ORIGIN_OWNER),
    ("route", WakeHubSmartReadinessCode.HOLD_ORIGIN_ROUTE),
    ("generation", WakeHubSmartReadinessCode.HOLD_ORIGIN_GENERATION),
])
@pytest.mark.parametrize("proof", [state for state in WakeHubProofState
                                  if state is not WakeHubProofState.INDEPENDENTLY_CURRENT])
def test_wh04a_copied_chat_wrong_owner_missing_issuer_or_generation_cannot_prepare(name, expected, proof):
    plan = assess_wakehub_smart_readiness(_smart_case("origin", **{name: proof}))
    assert plan.code is expected
    assert plan.core_status is WakeHubSmartCoreStatus.CORE_RECEIVE_IDLE_HEALTHY
    _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize("consent", [WakeHubConsentState.ABSENT, WakeHubConsentState.REVOKED,
                                   WakeHubConsentState.STALE])
def test_wh04a_absent_revoked_or_stale_consent_prevents_prepare(consent):
    plan = assess_wakehub_smart_readiness(_smart_case("origin", consent=consent))
    assert plan.code is WakeHubSmartReadinessCode.HOLD_ORIGIN_CONSENT
    _assert_wh04a_no_effect(plan)


def test_wh04a_reason_priority_is_typed_and_preserves_pause_on_holds():
    # Remove one higher-priority fault at a time from a multi-fault input.
    missing = WakeHubProofState.MISSING
    evidence = _smart_evidence(
        projection=missing, source=missing, listeners=missing,
        desktop=WakeHubDesktopState.LOCKED, session_owner=missing,
    )
    evidence = replace(evidence, core=replace(
        evidence.core, lost_effect_receipt=True, source_current=False,
        hostops_listener=WakeHubListenerState.ABSENT, outstanding_send=WakeHubWorkState.PENDING,
        trusted_human_pause=True,
    ), origin=replace(evidence.origin, event=WakeHubEventState.DUPLICATE_AMBIGUOUS))
    steps = [
        (None, {}, WakeHubSmartReadinessCode.HOLD_UNKNOWN_EFFECT),
        ("core", dict(lost_effect_receipt=False), WakeHubSmartReadinessCode.HOLD_SOURCE_DRIFT),
        ("core", dict(source_current=True), WakeHubSmartReadinessCode.HOLD_SOURCE_DRIFT),
        (None, dict(source=WakeHubProofState.INDEPENDENTLY_CURRENT),
         WakeHubSmartReadinessCode.HOLD_PRIVATE_PROJECTION),
        (None, dict(projection=WakeHubProofState.INDEPENDENTLY_CURRENT),
         WakeHubSmartReadinessCode.WAIT_RECEIVER),
        ("core", dict(hostops_listener=WakeHubListenerState.PRESENT),
         WakeHubSmartReadinessCode.WAIT_RECEIVER),
        (None, dict(listeners=WakeHubProofState.INDEPENDENTLY_CURRENT),
         WakeHubSmartReadinessCode.HOLD_EVENT_AMBIGUITY),
        ("origin", dict(event=WakeHubEventState.UNIQUE_CURRENT),
         WakeHubSmartReadinessCode.WAIT_PENDING_WORK),
        ("core", dict(outstanding_send=WakeHubWorkState.CLEAR),
         WakeHubSmartReadinessCode.HOLD_HUMAN_PAUSED),
        ("core", dict(trusted_human_pause=False), WakeHubSmartReadinessCode.WAIT_INTERACTIVE_SESSION),
        (None, dict(desktop=WakeHubDesktopState.OWNER_INTERACTIVE),
         WakeHubSmartReadinessCode.HOLD_SESSION_OWNER),
    ]
    for section, changes, expected in steps:
        evidence = (replace(evidence, **{section: replace(getattr(evidence, section), **changes)})
                    if section else replace(evidence, **changes))
        plan = assess_wakehub_smart_readiness(evidence)
        assert plan.code is expected
        assert type(plan.code) is WakeHubSmartReadinessCode
        assert type(plan.core_status) is WakeHubSmartCoreStatus
        assert plan.paused_by_human is evidence.core.trusted_human_pause
        _assert_wh04a_no_effect(plan)


def test_wh04a_browser_and_origin_priority_never_infers_reuse_from_partial_proofs():
    missing = WakeHubProofState.MISSING
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    evidence = _smart_case(
        "browser", ownership=WakeHubChromeOwnership.UNVERIFIED, port=9333,
        pid_creation=missing, retained_account=missing,
    )
    evidence = replace(evidence, origin=replace(
        evidence.origin, issuer=missing, owner=missing, route=missing,
        generation=missing, consent=WakeHubConsentState.REVOKED,
    ))
    steps = [
        ("browser", {}, WakeHubSmartReadinessCode.HOLD_CHROME_OWNERSHIP),
        ("browser", dict(ownership=WakeHubChromeOwnership.OWNED_CDP_9444),
         WakeHubSmartReadinessCode.HOLD_CHROME_ENDPOINT),
        ("browser", dict(port=9444), WakeHubSmartReadinessCode.HOLD_CHROME_IDENTITY),
        ("browser", dict(pid_creation=current), WakeHubSmartReadinessCode.HOLD_BROWSER_ACCOUNT),
        ("browser", dict(retained_account=current), WakeHubSmartReadinessCode.HOLD_ORIGIN_ISSUER),
        ("origin", dict(issuer=current), WakeHubSmartReadinessCode.HOLD_ORIGIN_OWNER),
        ("origin", dict(owner=current), WakeHubSmartReadinessCode.HOLD_ORIGIN_ROUTE),
        ("origin", dict(route=current), WakeHubSmartReadinessCode.HOLD_ORIGIN_GENERATION),
        ("origin", dict(generation=current), WakeHubSmartReadinessCode.HOLD_ORIGIN_CONSENT),
        ("origin", dict(consent=WakeHubConsentState.CURRENT),
         WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY),
    ]
    for section, changes, expected in steps:
        evidence = replace(evidence, **{section: replace(getattr(evidence, section), **changes)})
        plan = assess_wakehub_smart_readiness(evidence)
        assert plan.code is expected
        assert plan.core_status is WakeHubSmartCoreStatus.CORE_RECEIVE_IDLE_HEALTHY
        assert (plan.browser_advice is WakeHubBrowserAdvice.REUSE_VERIFIED_OWNER) is (
            expected is WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY)
        _assert_wh04a_no_effect(plan)


@pytest.mark.parametrize(("section", "name", "value"), [
    (None, "projection", True), (None, "source", "INDEPENDENTLY_CURRENT"),
    (None, "listeners", None), (None, "desktop", WakeHubListenerState.ABSENT),
    (None, "session_owner", "synthetic-secret"), (None, "core", {}),
    (None, "browser", {}), (None, "origin", None),
    ("browser", "ownership", "OWNED_CDP_9444"),
    ("browser", "ownership", WakeHubListenerState.PRESENT),
    ("browser", "host", "127.0.0.1"),
    ("browser", "host", "https://synthetic-secret.invalid/private"),
    ("browser", "pid_creation", WakeHubConsentState.CURRENT),
    ("browser", "port", True), ("browser", "port", 9444.0),
    ("browser", "port", None), ("browser", "port", -1),
    ("browser", "port", 65536), ("browser", "port", 10**1000),
    ("browser", "pid", True), ("browser", "pid", 2**32),
    ("browser", "pid", 10**1000), ("browser", "pid", -1),
    ("browser", "pid", (1,) * (WAKEHUB_MAX_SAMPLES + 1)),
    ("origin", "event", "UNIQUE_CURRENT"),
    ("origin", "generation", True), ("origin", "route", {"verified": True}),
    ("origin", "consent", WakeHubProofState.INDEPENDENTLY_CURRENT),
    ("core", "engineering_listeners", [WakeHubListenerState.PRESENT] * 2),
    ("core", "engineering_listeners", (WakeHubListenerState.PRESENT,)),
    ("core", "engineering_listeners", (WakeHubListenerState.PRESENT,) * (WAKEHUB_MAX_SAMPLES + 1)),
    ("core", "queue_state", None), ("core", "outstanding_send", "synthetic-secret"),
    ("core", "source_current", 1), ("core", "baseline", {"token": "synthetic-secret"}),
    ("core", "baseline", WakeHubBaseline(WakeHubObservationState.UNVERIFIED,
                                         idle_samples=(None,) * (WAKEHUB_MAX_SAMPLES + 1))),
])
def test_wh04a_closed_nested_schema_errors_are_typed_and_sanitized(section, name, value):
    evidence = _smart_evidence()
    with pytest.raises(WakeHubSmartInputError) as caught:
        if section:
            changed = replace(getattr(evidence, section), **{name: value})
            assess_wakehub_smart_readiness(replace(evidence, **{section: changed}))
        else:
            assess_wakehub_smart_readiness(replace(evidence, **{name: value}))
    assert caught.value.args == ("WAKEHUB_SMART_READINESS_INPUT_INVALID",)
    assert "synthetic-secret" not in str(caught.value)


def test_wh04a_missing_unknown_fields_and_public_dict_cannot_authenticate_or_enable_effects():
    evidence = _smart_evidence()
    for item in (evidence, evidence.browser, evidence.origin):
        values = {item_field.name: getattr(item, item_field.name) for item_field in fields(item)}
        for invalid in ({}, {**values, "synthetic-secret": "private-value"},
                        {**values, "verified": True}, {**values, "send": True}):
            with pytest.raises(WakeHubSmartInputError) as caught:
                type(item)(**invalid)
            assert caught.value.args == ("WAKEHUB_SMART_READINESS_INPUT_INVALID",)
        with pytest.raises(WakeHubSmartInputError):
            type(item)(values)
    for invalid in (None, {}, {"verified": True}, "synthetic-secret", (evidence,)):
        with pytest.raises(WakeHubSmartInputError) as caught:
            assess_wakehub_smart_readiness(invalid)
        assert caught.value.args == ("WAKEHUB_SMART_READINESS_INPUT_INVALID",)
    with pytest.raises(WakeHubSmartInputError) as caught:
        assess_wakehub_smart_readiness(evidence, **{"synthetic-secret": True})
    assert caught.value.args == ("WAKEHUB_SMART_READINESS_INPUT_INVALID",)


def test_wh04a_rejects_subclasses_and_forged_enum_members_without_invoking_user_code():
    class UnsafeInt(int):
        def __le__(self, other):
            pytest.fail("numeric subclass executed")

    class UnsafeStr(str):
        def __eq__(self, other):
            pytest.fail("string subclass executed")

    class UnsafeEvidence(WakeHubSmartReadinessEvidence):
        def _validate(self):
            pytest.fail("evidence subclass executed")

    forged = str.__new__(WakeHubProofState, "INDEPENDENTLY_CURRENT")
    for changes in (dict(source=forged), dict(source=UnsafeStr("INDEPENDENTLY_CURRENT"))):
        with pytest.raises(WakeHubSmartInputError):
            _smart_evidence(**changes)
    with pytest.raises(WakeHubSmartInputError):
        _smart_case("browser", port=UnsafeInt(9444))
    with pytest.raises(WakeHubSmartInputError):
        assess_wakehub_smart_readiness(object.__new__(UnsafeEvidence))


def test_wh04a_nested_evidence_and_every_external_effect_field_are_frozen():
    evidence = _smart_evidence()
    plan = assess_wakehub_smart_readiness(evidence)
    for item, name, value in (
        (evidence, "source", WakeHubProofState.MISSING),
        (evidence.core, "source_current", False),
        (evidence.core.baseline, "idle_samples", ()),
        (evidence.browser, "port", 9333),
        (evidence.origin, "consent", WakeHubConsentState.REVOKED),
        (plan, "code", WakeHubSmartReadinessCode.WAIT_RECEIVER),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(item, name, value)
    for item_field in fields(plan):
        if item_field.init:
            continue
        name = item_field.name
        original = getattr(plan, name)
        with pytest.raises(FrozenInstanceError):
            setattr(plan, name, True)
        with pytest.raises((TypeError, ValueError), match=rf"^field {name} is declared with init=False,"):
            replace(plan, **{name: True})
        assert getattr(plan, name) == original
    for item in (evidence, evidence.core, evidence.core.baseline, evidence.browser, evidence.origin, plan):
        assert not hasattr(item, "__dict__")


def test_wh04a_every_reason_and_invalid_input_are_pure_under_external_effect_traps(monkeypatch):
    cases = [
        _smart_evidence(),
        *(_smart_case("core", **change) for change in (
            dict(unknown_effect=True), dict(lost_effect_receipt=True), dict(source_current=False),
            dict(hostops_listener=WakeHubListenerState.ABSENT),
            dict(outstanding_send=WakeHubWorkState.PENDING), dict(trusted_human_pause=True),
            dict(interactive_session_proven=False),
        )),
        _smart_evidence(projection=WakeHubProofState.MISSING),
        _smart_evidence(session_owner=WakeHubProofState.MISSING),
        *(_smart_case("origin", event=state) for state in WakeHubEventState),
        *(_smart_case("browser", ownership=state) for state in WakeHubChromeOwnership),
        _smart_case("browser", port=9333),
        _smart_case("browser", pid_creation=WakeHubProofState.MISMATCH),
        _smart_case("browser", retained_account=WakeHubProofState.MISSING),
        *(_smart_case("origin", **{name: WakeHubProofState.MISSING})
          for name in ("issuer", "owner", "route", "generation")),
        _smart_case("origin", consent=WakeHubConsentState.REVOKED),
    ]

    def forbidden(*args, **kwargs):
        raise AssertionError("WH-04A attempted an external effect")

    codes = set()
    with monkeypatch.context() as guard:
        for owner, names in (
            (builtins, ("open",)),
            (Path, ("open", "read_text", "read_bytes", "write_text", "write_bytes", "stat",
                    "iterdir", "mkdir", "unlink", "rename", "replace")),
            (os, ("open", "stat", "listdir", "scandir", "mkdir", "remove", "rename",
                  "replace", "system", "kill", "startfile", "popen", "spawnv", "spawnve")),
            (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
            (socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname")),
            (time, ("sleep", "time", "monotonic", "perf_counter")),
        ):
            for name in names:
                guard.setattr(owner, name, forbidden)
        for evidence in cases:
            plan = assess_wakehub_smart_readiness(evidence)
            assert plan == assess_wakehub_smart_readiness(evidence)
            codes.add(plan.code)
            _assert_wh04a_no_effect(plan)
        # Validation must still fail before a priority HOLD and leak no input.
        for invalid in (None, {"token": "synthetic-secret"}):
            with pytest.raises(WakeHubSmartInputError) as caught:
                assess_wakehub_smart_readiness(invalid)
            assert caught.value.args == ("WAKEHUB_SMART_READINESS_INPUT_INVALID",)
        with pytest.raises(WakeHubSmartInputError):
            _smart_case("core", unknown_effect=True, outstanding_send="synthetic-secret")
    assert codes == set(WakeHubSmartReadinessCode)


# WH-05A pure exact Chat A autoload helpers
def _autoload_tab(**overrides):
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    values = dict(
        tab_id=1,
        chat_binding=WakeHubChatBinding.EXACT_CHAT_A,
        marker=current,
        dedicated_context=True,
        human_draft_present=False,
        actively_generating=False,
        security_challenge_present=False,
        generation=current,
        redirect_drift=False,
        post_navigation_recheck=current,
        account_proof=current,
    )
    values.update(overrides)
    return WakeHubChatTabEvidence(**values)


def _autoload_evidence(**overrides):
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    values = dict(
        readiness=_smart_evidence(),
        tabs=(_autoload_tab(),),
        dedicated_context=current,
        tab_generation=current,
        route_authorized=current,
        unrelated_tab_mutation=False,
    )
    values.update(overrides)
    return WakeHubChatAutoloadEvidence(**values)


def _assert_wh05a_no_effect(plan):
    _assert_wh02a_no_authority(plan)
    assert plan.scope == "PURE_EXACT_CHAT_AUTOLOAD_POLICY_ONLY"
    assert plan.keep_event_receiver_alive is True
    for name in (
        "native_operations", "start", "stop", "task", "chrome",
        "navigation", "send", "plugin", "attach_authorized",
        "open_tab_authorized", "click_permitted",
        "dom_ack_counted", "server_ack_counted",
    ):
        assert getattr(plan, name) is False
    assert plan.post_navigation_recheck_required is True


def test_wh05a_positive_attach_existing_tab_advisory_is_pure_and_deterministic():
    evidence = _autoload_evidence()
    plan = assess_wakehub_chat_autoload_plan(evidence)
    assert plan == assess_wakehub_chat_autoload_plan(evidence)
    assert plan.advice is WakeHubAutoloadAdvice.ATTACH_EXISTING_ADVISORY_ONLY
    assert plan.reason is WakeHubAutoloadReason.ATTACH_EXISTING_EXACT_CHAT_A_TAB
    assert plan.code is WakeHubAutoloadReason.ATTACH_EXISTING_EXACT_CHAT_A_TAB
    assert plan.listener_count == len(planned_topology()) == 3
    assert plan.readiness_code is WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY
    assert not plan.paused_by_human
    _assert_wh05a_no_effect(plan)


def test_wh05a_positive_zero_tabs_open_dedicated_tab_advisory_is_pure_and_deterministic():
    evidence = _autoload_evidence(tabs=())
    plan = assess_wakehub_chat_autoload_plan(evidence)
    assert plan == assess_wakehub_chat_autoload_plan(evidence)
    assert plan.advice is WakeHubAutoloadAdvice.OPEN_DEDICATED_TAB_ADVISORY_ONLY
    assert plan.reason is WakeHubAutoloadReason.OPEN_NEW_DEDICATED_TAB_ROUTE_PROVEN
    assert plan.code is WakeHubAutoloadReason.OPEN_NEW_DEDICATED_TAB_ROUTE_PROVEN
    assert plan.listener_count == 3
    assert plan.readiness_code is WakeHubSmartReadinessCode.READY_TO_PREPARE_ADVISORY_ONLY
    assert not plan.paused_by_human
    _assert_wh05a_no_effect(plan)


def test_wh05a_duplicate_matching_tabs_and_multiple_tabs_hold():
    # Two exact Chat A tabs
    two_matching = _autoload_evidence(tabs=(_autoload_tab(tab_id=1), _autoload_tab(tab_id=2)))
    plan1 = assess_wakehub_chat_autoload_plan(two_matching)
    assert plan1.advice is WakeHubAutoloadAdvice.HOLD
    assert plan1.reason is WakeHubAutoloadReason.HOLD_DUPLICATE_MATCHING_TABS
    _assert_wh05a_no_effect(plan1)

    # One Chat A and one Chat B
    two_mixed = _autoload_evidence(tabs=(
        _autoload_tab(tab_id=1),
        _autoload_tab(tab_id=2, chat_binding=WakeHubChatBinding.WRONG_CHAT_B),
    ))
    plan2 = assess_wakehub_chat_autoload_plan(two_mixed)
    assert plan2.advice is WakeHubAutoloadAdvice.HOLD
    assert plan2.reason is WakeHubAutoloadReason.HOLD_DUPLICATE_MATCHING_TABS
    _assert_wh05a_no_effect(plan2)


@pytest.mark.parametrize("binding", [
    WakeHubChatBinding.WRONG_CHAT_B,
    WakeHubChatBinding.UNBOUND,
    WakeHubChatBinding.AMBIGUOUS,
])
def test_wh05a_wrong_or_unbound_chat_tab_holds(binding):
    evidence = _autoload_evidence(tabs=(_autoload_tab(chat_binding=binding),))
    plan = assess_wakehub_chat_autoload_plan(evidence)
    assert plan.advice is WakeHubAutoloadAdvice.HOLD
    assert plan.reason is WakeHubAutoloadReason.HOLD_WRONG_CHAT_TARGET
    _assert_wh05a_no_effect(plan)


@pytest.mark.parametrize("marker_state", [
    state for state in WakeHubProofState
    if state is not WakeHubProofState.INDEPENDENTLY_CURRENT
])
def test_wh05a_copied_or_ambiguous_marker_holds(marker_state):
    evidence = _autoload_evidence(tabs=(_autoload_tab(marker=marker_state),))
    plan = assess_wakehub_chat_autoload_plan(evidence)
    assert plan.advice is WakeHubAutoloadAdvice.HOLD
    assert plan.reason is WakeHubAutoloadReason.HOLD_MARKER_AMBIGUOUS
    _assert_wh05a_no_effect(plan)


def test_wh05a_tab_negative_conditions_fail_closed():
    # Non-dedicated tab
    p_context = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(dedicated_context=False),)))
    assert p_context.advice is WakeHubAutoloadAdvice.HOLD
    assert p_context.reason is WakeHubAutoloadReason.HOLD_NON_DEDICATED_CONTEXT
    _assert_wh05a_no_effect(p_context)

    # Missing tab_id / 0
    p_zero = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(tab_id=0),)))
    assert p_zero.advice is WakeHubAutoloadAdvice.HOLD
    assert p_zero.reason is WakeHubAutoloadReason.HOLD_NON_DEDICATED_CONTEXT
    _assert_wh05a_no_effect(p_zero)

    # Wrong account
    p_account = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(account_proof=WakeHubProofState.MISMATCH),)))
    assert p_account.advice is WakeHubAutoloadAdvice.HOLD
    assert p_account.reason is WakeHubAutoloadReason.HOLD_BROWSER_ACCOUNT
    _assert_wh05a_no_effect(p_account)

    # Login / Captcha challenge
    p_captcha = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(security_challenge_present=True),)))
    assert p_captcha.advice is WakeHubAutoloadAdvice.HOLD
    assert p_captcha.reason is WakeHubAutoloadReason.HOLD_LOGIN_OR_CAPTCHA_CHALLENGE
    _assert_wh05a_no_effect(p_captcha)

    # Human draft present
    p_draft = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(human_draft_present=True),)))
    assert p_draft.advice is WakeHubAutoloadAdvice.HOLD
    assert p_draft.reason is WakeHubAutoloadReason.HOLD_HUMAN_DRAFT_PRESENT
    _assert_wh05a_no_effect(p_draft)

    # Actively generating response
    p_gen = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(actively_generating=True),)))
    assert p_gen.advice is WakeHubAutoloadAdvice.WAIT
    assert p_gen.reason is WakeHubAutoloadReason.WAIT_RESPONSE_STREAMING_ACTIVE
    _assert_wh05a_no_effect(p_gen)

    # Redirect drift
    p_redirect = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(redirect_drift=True),)))
    assert p_redirect.advice is WakeHubAutoloadAdvice.HOLD
    assert p_redirect.reason is WakeHubAutoloadReason.HOLD_GENERATION_DRIFT
    _assert_wh05a_no_effect(p_redirect)

    # Tab generation drift
    p_tabgen = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(generation=WakeHubProofState.STALE),)))
    assert p_tabgen.advice is WakeHubAutoloadAdvice.HOLD
    assert p_tabgen.reason is WakeHubAutoloadReason.HOLD_GENERATION_DRIFT
    _assert_wh05a_no_effect(p_tabgen)

    # Post-navigation recheck missing -> WAIT
    p_recheck_wait = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(post_navigation_recheck=WakeHubProofState.MISSING),)))
    assert p_recheck_wait.advice is WakeHubAutoloadAdvice.WAIT
    assert p_recheck_wait.reason is WakeHubAutoloadReason.WAIT_POST_NAVIGATION_RECHECK
    _assert_wh05a_no_effect(p_recheck_wait)

    # Post-navigation recheck stale/ambiguous/mismatch -> HOLD
    for recheck_state in (WakeHubProofState.STALE, WakeHubProofState.AMBIGUOUS, WakeHubProofState.MISMATCH):
        p_recheck_hold = assess_wakehub_chat_autoload_plan(_autoload_evidence(
            tabs=(_autoload_tab(post_navigation_recheck=recheck_state),)))
        assert p_recheck_hold.advice is WakeHubAutoloadAdvice.HOLD
        assert p_recheck_hold.reason is WakeHubAutoloadReason.HOLD_POST_NAVIGATION_RECHECK_REQUIRED
        _assert_wh05a_no_effect(p_recheck_hold)


def test_wh05a_top_level_evidence_gates_fail_closed():
    # Dedicated context missing -> WAIT
    p_ctx_wait = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        dedicated_context=WakeHubProofState.MISSING))
    assert p_ctx_wait.advice is WakeHubAutoloadAdvice.WAIT
    assert p_ctx_wait.reason is WakeHubAutoloadReason.WAIT_DEDICATED_CONTEXT
    _assert_wh05a_no_effect(p_ctx_wait)

    # Dedicated context stale/mismatch/ambiguous -> HOLD
    p_ctx_hold = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        dedicated_context=WakeHubProofState.STALE))
    assert p_ctx_hold.advice is WakeHubAutoloadAdvice.HOLD
    assert p_ctx_hold.reason is WakeHubAutoloadReason.HOLD_NON_DEDICATED_CONTEXT
    _assert_wh05a_no_effect(p_ctx_hold)

    # Tab generation drift
    p_drift = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tab_generation=WakeHubProofState.STALE))
    assert p_drift.advice is WakeHubAutoloadAdvice.HOLD
    assert p_drift.reason is WakeHubAutoloadReason.HOLD_GENERATION_DRIFT
    _assert_wh05a_no_effect(p_drift)

    # Unrelated tab mutation
    p_mut = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        unrelated_tab_mutation=True))
    assert p_mut.advice is WakeHubAutoloadAdvice.HOLD
    assert p_mut.reason is WakeHubAutoloadReason.HOLD_UNRELATED_TAB_MUTATION
    _assert_wh05a_no_effect(p_mut)

    # Zero tabs with route not authorized / missing
    p_route = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(), route_authorized=WakeHubProofState.MISSING))
    assert p_route.advice is WakeHubAutoloadAdvice.HOLD
    assert p_route.reason is WakeHubAutoloadReason.HOLD_ORIGIN_ROUTE
    _assert_wh05a_no_effect(p_route)


@pytest.mark.parametrize(("smart_code", "expected_advice"), [
    (WakeHubSmartReadinessCode.HOLD_UNKNOWN_EFFECT, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_SOURCE_DRIFT, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_PRIVATE_PROJECTION, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.WAIT_RECEIVER, WakeHubAutoloadAdvice.WAIT),
    (WakeHubSmartReadinessCode.WAIT_EVENT, WakeHubAutoloadAdvice.WAIT),
    (WakeHubSmartReadinessCode.HOLD_EVENT_STALE, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_EVENT_AMBIGUITY, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.WAIT_PENDING_WORK, WakeHubAutoloadAdvice.WAIT),
    (WakeHubSmartReadinessCode.HOLD_HUMAN_PAUSED, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.WAIT_INTERACTIVE_SESSION, WakeHubAutoloadAdvice.WAIT),
    (WakeHubSmartReadinessCode.HOLD_SESSION_OWNER, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.WAIT_CHROME, WakeHubAutoloadAdvice.WAIT),
    (WakeHubSmartReadinessCode.HOLD_CHROME_OWNERSHIP, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_CHROME_ENDPOINT, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_CHROME_IDENTITY, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_BROWSER_ACCOUNT, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_ORIGIN_ISSUER, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_ORIGIN_OWNER, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_ORIGIN_ROUTE, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_ORIGIN_GENERATION, WakeHubAutoloadAdvice.HOLD),
    (WakeHubSmartReadinessCode.HOLD_ORIGIN_CONSENT, WakeHubAutoloadAdvice.HOLD),
])
def test_wh05a_propagates_unready_smart_readiness_code_and_advice(smart_code, expected_advice):
    smart = _smart_evidence()
    if smart_code is WakeHubSmartReadinessCode.HOLD_UNKNOWN_EFFECT:
        smart = replace(smart, core=replace(smart.core, unknown_effect=True))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_SOURCE_DRIFT:
        smart = replace(smart, source=WakeHubProofState.MISSING)
    elif smart_code is WakeHubSmartReadinessCode.HOLD_PRIVATE_PROJECTION:
        smart = replace(smart, projection=WakeHubProofState.MISSING)
    elif smart_code is WakeHubSmartReadinessCode.WAIT_RECEIVER:
        smart = replace(smart, core=replace(smart.core, hostops_listener=WakeHubListenerState.ABSENT))
    elif smart_code is WakeHubSmartReadinessCode.WAIT_EVENT:
        smart = replace(smart, origin=replace(smart.origin, event=WakeHubEventState.ABSENT))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_EVENT_STALE:
        smart = replace(smart, origin=replace(smart.origin, event=WakeHubEventState.STALE))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_EVENT_AMBIGUITY:
        smart = replace(smart, origin=replace(smart.origin, event=WakeHubEventState.DUPLICATE_AMBIGUOUS))
    elif smart_code is WakeHubSmartReadinessCode.WAIT_PENDING_WORK:
        smart = replace(smart, core=replace(smart.core, outstanding_send=WakeHubWorkState.PENDING))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_HUMAN_PAUSED:
        smart = replace(smart, core=replace(smart.core, trusted_human_pause=True))
    elif smart_code is WakeHubSmartReadinessCode.WAIT_INTERACTIVE_SESSION:
        smart = replace(smart, core=replace(smart.core, interactive_session_proven=False))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_SESSION_OWNER:
        smart = replace(smart, session_owner=WakeHubProofState.MISSING)
    elif smart_code is WakeHubSmartReadinessCode.WAIT_CHROME:
        smart = replace(smart, core=replace(smart.core, chrome_ownership=WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED),
                        browser=replace(smart.browser, ownership=WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_CHROME_OWNERSHIP:
        smart = replace(smart, browser=replace(smart.browser, ownership=WakeHubChromeOwnership.PROFILE_CONFLICT),
                        core=replace(smart.core, chrome_ownership=WakeHubChromeOwnership.PROFILE_CONFLICT))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_CHROME_ENDPOINT:
        smart = replace(smart, browser=replace(smart.browser, port=9333))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_CHROME_IDENTITY:
        smart = replace(smart, browser=replace(smart.browser, pid=0))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_BROWSER_ACCOUNT:
        smart = replace(smart, browser=replace(smart.browser, retained_account=WakeHubProofState.MISSING))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_ORIGIN_ISSUER:
        smart = replace(smart, origin=replace(smart.origin, issuer=WakeHubProofState.MISSING))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_ORIGIN_OWNER:
        smart = replace(smart, origin=replace(smart.origin, owner=WakeHubProofState.MISSING))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_ORIGIN_ROUTE:
        smart = replace(smart, origin=replace(smart.origin, route=WakeHubProofState.MISSING))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_ORIGIN_GENERATION:
        smart = replace(smart, origin=replace(smart.origin, generation=WakeHubProofState.MISSING))
    elif smart_code is WakeHubSmartReadinessCode.HOLD_ORIGIN_CONSENT:
        smart = replace(smart, origin=replace(smart.origin, consent=WakeHubConsentState.REVOKED))

    evidence = _autoload_evidence(readiness=smart)
    plan = assess_wakehub_chat_autoload_plan(evidence)
    assert plan.advice is expected_advice
    assert plan.reason.value == smart_code.value
    assert plan.readiness_code is smart_code
    _assert_wh05a_no_effect(plan)


@pytest.mark.parametrize(("section", "name", "value"), [
    (None, "readiness", {}),
    (None, "tabs", None),
    (None, "tabs", [1, 2]),
    (None, "tabs", (_autoload_tab(),) * (WAKEHUB_MAX_SAMPLES + 1)),
    (None, "dedicated_context", "INDEPENDENTLY_CURRENT"),
    (None, "dedicated_context", True),
    (None, "tab_generation", 123),
    (None, "route_authorized", "synthetic-secret"),
    (None, "unrelated_tab_mutation", 1),
    (None, "unrelated_tab_mutation", "false"),
    ("tab", "tab_id", True),
    ("tab", "tab_id", -1),
    ("tab", "tab_id", 2**32),
    ("tab", "tab_id", 10**1000),
    ("tab", "tab_id", 1.5),
    ("tab", "chat_binding", "EXACT_CHAT_A"),
    ("tab", "chat_binding", WakeHubListenerState.PRESENT),
    ("tab", "marker", "CURRENT"),
    ("tab", "dedicated_context", 1),
    ("tab", "human_draft_present", "synthetic-secret"),
    ("tab", "actively_generating", 0),
    ("tab", "security_challenge_present", None),
    ("tab", "generation", True),
    ("tab", "redirect_drift", 1),
    ("tab", "post_navigation_recheck", "synthetic-secret"),
    ("tab", "account_proof", 9444),
])
def test_wh05a_closed_schema_errors_are_typed_and_sanitized(section, name, value):
    evidence = _autoload_evidence()
    with pytest.raises(WakeHubAutoloadInputError) as caught:
        if section == "tab":
            tab = replace(evidence.tabs[0], **{name: value})
            assess_wakehub_chat_autoload_plan(replace(evidence, tabs=(tab,)))
        else:
            assess_wakehub_chat_autoload_plan(replace(evidence, **{name: value}))
    assert caught.value.args == ("WAKEHUB_AUTOLOAD_INPUT_INVALID",)
    assert "synthetic-secret" not in str(caught.value)


def test_wh05a_missing_unknown_fields_and_dict_cannot_authenticate_or_enable_effects():
    evidence = _autoload_evidence()
    tab = evidence.tabs[0]
    for item in (evidence, tab):
        values = {item_field.name: getattr(item, item_field.name) for item_field in fields(item)}
        for invalid in ({}, {**values, "synthetic-secret": "private-value"},
                        {**values, "verified": True}, {**values, "navigation": True}):
            with pytest.raises(WakeHubAutoloadInputError) as caught:
                type(item)(**invalid)
            assert caught.value.args == ("WAKEHUB_AUTOLOAD_INPUT_INVALID",)
        with pytest.raises(WakeHubAutoloadInputError):
            type(item)(values)
    for invalid in (None, {}, {"verified": True}, "synthetic-secret", (evidence,)):
        with pytest.raises(WakeHubAutoloadInputError) as caught:
            assess_wakehub_chat_autoload_plan(invalid)
        assert caught.value.args == ("WAKEHUB_AUTOLOAD_INPUT_INVALID",)
    with pytest.raises(WakeHubAutoloadInputError) as caught:
        assess_wakehub_chat_autoload_plan(evidence, **{"synthetic-secret": True})
    assert caught.value.args == ("WAKEHUB_AUTOLOAD_INPUT_INVALID",)


def test_wh05a_rejects_subclasses_and_forged_enum_members_without_invoking_user_code():
    class UnsafeInt(int):
        def __le__(self, other):
            pytest.fail("numeric subclass executed")

    class UnsafeStr(str):
        def __eq__(self, other):
            pytest.fail("string subclass executed")

    class UnsafeTabEvidence(WakeHubChatTabEvidence):
        def _validate(self):
            pytest.fail("tab evidence subclass executed")

    class UnsafeAutoloadEvidence(WakeHubChatAutoloadEvidence):
        def _validate(self):
            pytest.fail("autoload evidence subclass executed")

    forged = str.__new__(WakeHubProofState, "INDEPENDENTLY_CURRENT")
    with pytest.raises(WakeHubAutoloadInputError):
        _autoload_evidence(dedicated_context=forged)
    with pytest.raises(WakeHubAutoloadInputError):
        _autoload_evidence(dedicated_context=UnsafeStr("INDEPENDENTLY_CURRENT"))
    with pytest.raises(WakeHubAutoloadInputError):
        _autoload_tab(tab_id=UnsafeInt(1))
    with pytest.raises(WakeHubAutoloadInputError):
        _autoload_evidence(tabs=(object.__new__(UnsafeTabEvidence),))
    with pytest.raises(WakeHubAutoloadInputError):
        assess_wakehub_chat_autoload_plan(object.__new__(UnsafeAutoloadEvidence))


def test_wh05a_maximum_windows_tab_id_is_a_schema_bound_only():
    upper = assess_wakehub_chat_autoload_plan(_autoload_evidence(
        tabs=(_autoload_tab(tab_id=2**32 - 1),)))
    assert upper.advice is WakeHubAutoloadAdvice.ATTACH_EXISTING_ADVISORY_ONLY
    assert upper.reason is WakeHubAutoloadReason.ATTACH_EXISTING_EXACT_CHAT_A_TAB
    _assert_wh05a_no_effect(upper)


def test_wh05a_nested_evidence_and_every_external_effect_field_are_frozen():
    evidence = _autoload_evidence()
    tab = evidence.tabs[0]
    plan = assess_wakehub_chat_autoload_plan(evidence)
    for item, name, value in (
        (evidence, "dedicated_context", WakeHubProofState.MISSING),
        (tab, "tab_id", 2),
        (tab, "chat_binding", WakeHubChatBinding.WRONG_CHAT_B),
        (plan, "advice", WakeHubAutoloadAdvice.WAIT),
        (plan, "reason", WakeHubAutoloadReason.WAIT_RECEIVER),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(item, name, value)
    for item_field in fields(plan):
        if item_field.init:
            continue
        name = item_field.name
        original = getattr(plan, name)
        with pytest.raises(FrozenInstanceError):
            setattr(plan, name, True)
        with pytest.raises((TypeError, ValueError), match=rf"^field {name} is declared with init=False,"):
            replace(plan, **{name: True})
        assert getattr(plan, name) == original
    for item in (evidence, tab, plan):
        assert not hasattr(item, "__dict__")


def test_wh05a_every_reason_and_invalid_input_are_pure_under_external_effect_traps(monkeypatch):
    cases = [
        # Positive attach existing
        _autoload_evidence(),
        # Positive open dedicated tab
        _autoload_evidence(tabs=()),
        # Duplicate matching tabs
        _autoload_evidence(tabs=(_autoload_tab(tab_id=1), _autoload_tab(tab_id=2))),
        # Wrong chat binding
        _autoload_evidence(tabs=(_autoload_tab(chat_binding=WakeHubChatBinding.WRONG_CHAT_B),)),
        # Ambiguous marker
        _autoload_evidence(tabs=(_autoload_tab(marker=WakeHubProofState.AMBIGUOUS),)),
        # Captcha
        _autoload_evidence(tabs=(_autoload_tab(security_challenge_present=True),)),
        # Non-dedicated tab
        _autoload_evidence(tabs=(_autoload_tab(dedicated_context=False),)),
        # Human draft
        _autoload_evidence(tabs=(_autoload_tab(human_draft_present=True),)),
        # Generation drift (tab redirect)
        _autoload_evidence(tabs=(_autoload_tab(redirect_drift=True),)),
        # Generation drift (top-level tab generation)
        _autoload_evidence(tab_generation=WakeHubProofState.STALE),
        # Actively generating response (WAIT)
        _autoload_evidence(tabs=(_autoload_tab(actively_generating=True),)),
        # Post-navigation recheck missing (WAIT)
        _autoload_evidence(tabs=(_autoload_tab(post_navigation_recheck=WakeHubProofState.MISSING),)),
        # Post-navigation recheck stale (HOLD)
        _autoload_evidence(tabs=(_autoload_tab(post_navigation_recheck=WakeHubProofState.STALE),)),
        # Dedicated context missing (WAIT)
        _autoload_evidence(dedicated_context=WakeHubProofState.MISSING),
        # Dedicated context stale (HOLD)
        _autoload_evidence(dedicated_context=WakeHubProofState.STALE),
        # Wrong account (HOLD)
        _autoload_evidence(tabs=(_autoload_tab(account_proof=WakeHubProofState.MISSING),)),
        # Unrelated tab mutation (HOLD)
        _autoload_evidence(unrelated_tab_mutation=True),
        # Route not authorized when opening (HOLD)
        _autoload_evidence(tabs=(), route_authorized=WakeHubProofState.MISSING),
        # Propagated WH04A reasons:
        _autoload_evidence(readiness=_smart_case("core", unknown_effect=True)),
        _autoload_evidence(readiness=_smart_case("core", source_current=False)),
        _autoload_evidence(readiness=_smart_evidence(projection=WakeHubProofState.MISSING)),
        _autoload_evidence(readiness=_smart_case("origin", event=WakeHubEventState.STALE)),
        _autoload_evidence(readiness=_smart_case("origin", event=WakeHubEventState.DUPLICATE_AMBIGUOUS)),
        _autoload_evidence(readiness=_smart_case("core", hostops_listener=WakeHubListenerState.ABSENT)),
        _autoload_evidence(readiness=_smart_case("origin", event=WakeHubEventState.ABSENT)),
        _autoload_evidence(readiness=_smart_case("core", outstanding_send=WakeHubWorkState.PENDING)),
        _autoload_evidence(readiness=_smart_case("core", trusted_human_pause=True)),
        _autoload_evidence(readiness=_smart_case("core", interactive_session_proven=False)),
        _autoload_evidence(readiness=_smart_evidence(session_owner=WakeHubProofState.MISSING)),
        _autoload_evidence(readiness=_smart_case("browser", ownership=WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED)),
        _autoload_evidence(readiness=_smart_case("browser", ownership=WakeHubChromeOwnership.PROFILE_CONFLICT)),
        _autoload_evidence(readiness=_smart_case("browser", port=9333)),
        _autoload_evidence(readiness=_smart_case("browser", pid=0)),
        _autoload_evidence(readiness=_smart_case("browser", retained_account=WakeHubProofState.MISSING)),
        _autoload_evidence(readiness=_smart_case("origin", issuer=WakeHubProofState.MISSING)),
        _autoload_evidence(readiness=_smart_case("origin", owner=WakeHubProofState.MISSING)),
        _autoload_evidence(readiness=_smart_case("origin", route=WakeHubProofState.MISSING)),
        _autoload_evidence(readiness=_smart_case("origin", generation=WakeHubProofState.MISSING)),
        _autoload_evidence(readiness=_smart_case("origin", consent=WakeHubConsentState.REVOKED)),
    ]

    def forbidden(*args, **kwargs):
        raise AssertionError("WH-05A attempted an external effect")

    codes = set()
    with monkeypatch.context() as guard:
        for owner, names in (
            (builtins, ("open",)),
            (Path, ("open", "read_text", "read_bytes", "write_text", "write_bytes", "stat",
                    "iterdir", "mkdir", "unlink", "rename", "replace")),
            (os, ("open", "stat", "listdir", "scandir", "mkdir", "remove", "rename",
                  "replace", "system", "kill", "startfile", "popen", "spawnv", "spawnve")),
            (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
            (socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname")),
            (time, ("sleep", "time", "monotonic", "perf_counter")),
        ):
            for name in names:
                guard.setattr(owner, name, forbidden)
        for evidence in cases:
            plan = assess_wakehub_chat_autoload_plan(evidence)
            assert plan == assess_wakehub_chat_autoload_plan(evidence)
            codes.add(plan.reason)
            _assert_wh05a_no_effect(plan)
        for invalid in (None, {"token": "synthetic-secret"}):
            with pytest.raises(WakeHubAutoloadInputError) as caught:
                assess_wakehub_chat_autoload_plan(invalid)
            assert caught.value.args == ("WAKEHUB_AUTOLOAD_INPUT_INVALID",)
    assert codes == set(WakeHubAutoloadReason)


# WH06A facts are synthetic PRIVATE observer projections, never native proof.
def _send_authorization(**overrides):
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    values = dict(
        projection=current, native_observer=current,
        completion=WakeHubCompletionState.NEW_WORK_COMPLETION,
        completion_proof=current, consent=WakeHubConsentState.CURRENT,
        consent_scope=WakeHubSendConsentScope.SEND_ONLY,
        consent_signature=current, signer=current, origin_binding=current,
        lease=current, correlation=current,
        intent=WakeHubSendIntentState.WRITE_AHEAD_RECORDED, intent_proof=current,
    )
    return WakeHubSendAuthorizationEvidence(**(values | overrides))


def _send_preview(**overrides):
    values = dict(
        autoload=_autoload_evidence(), authorization=_send_authorization(),
        composer_empty=True, send_control_count=1, send_control_visible=True,
        send_control_enabled=True, delivery=WakeHubSendDeliveryState.NOT_ATTEMPTED,
        ui_user_turn=WakeHubProofState.MISSING,
    )
    return WakeHubSendOnlyPreviewEvidence(**(values | overrides))


def _assert_wh06a_no_effect(plan):
    _assert_wh02a_no_authority(plan)
    assert plan.scope == "WH06A_PURE_SEND_PREVIEW_POLICY_ONLY"
    assert plan.keep_event_receiver_alive is True
    for name in ("native_operations", "chrome", "navigation", "send", "click",
                 "plugin", "start", "stop", "task", "process", "grant",
                 "authorization", "d2_intent_written", "server_ack_counted"):
        assert getattr(plan, name) is False
        assert next(item for item in fields(plan) if item.name == name).init is False


def _send_policy_cases():
    reason = WakeHubSendPreviewReason
    advice = WakeHubSendPreviewAdvice
    current = WakeHubProofState.INDEPENDENTLY_CURRENT
    cases = [
        (_send_preview(), reason.PREVIEW_REQUIREMENTS_CURRENT,
         advice.PREVIEW_READY_ADVISORY_ONLY),
        (_send_preview(autoload=_autoload_evidence(tabs=())),
         reason.WAIT_NAVIGATION_AND_REPROOF, advice.WAIT),
        (_send_preview(composer_empty=False), reason.HOLD_COMPOSER_NOT_EMPTY, advice.HOLD),
        (_send_preview(send_control_count=2), reason.HOLD_SEND_CONTROL_AMBIGUOUS, advice.HOLD),
        (_send_preview(send_control_count=2**32 - 1), reason.HOLD_SEND_CONTROL_AMBIGUOUS, advice.HOLD),
        (_send_preview(send_control_count=0), reason.WAIT_VISIBLE_SEND_CONTROL, advice.WAIT),
        (_send_preview(send_control_visible=False), reason.WAIT_VISIBLE_SEND_CONTROL, advice.WAIT),
        (_send_preview(send_control_enabled=False), reason.WAIT_SEND_CONTROL_ENABLED, advice.WAIT),
        (_send_preview(ui_user_turn=current), reason.RECONCILE_UNBOUND_UI_WITNESS,
         advice.RECONCILE_UNKNOWN_NO_RETRY),
        (_send_preview(autoload=_autoload_evidence(readiness=_smart_case("core", unknown_effect=True))),
         reason.RECONCILE_UNKNOWN_EFFECT, advice.RECONCILE_UNKNOWN_NO_RETRY),
        (_send_preview(authorization=_send_authorization(intent=WakeHubSendIntentState.UNKNOWN)),
         reason.RECONCILE_UNKNOWN_EFFECT, advice.RECONCILE_UNKNOWN_NO_RETRY),
    ]
    for name, code in (
        ("projection", reason.HOLD_PRIVATE_SEND_PROJECTION),
        ("native_observer", reason.HOLD_NATIVE_OBSERVER_PROOF),
        ("completion_proof", reason.HOLD_COMPLETION_PROOF),
        ("consent_signature", reason.HOLD_CONSENT_SIGNATURE),
        ("signer", reason.HOLD_CONSENT_SIGNER),
        ("origin_binding", reason.HOLD_SEND_ORIGIN_BINDING),
        ("lease", reason.HOLD_SEND_LEASE),
        ("correlation", reason.HOLD_SEND_CORRELATION),
        ("intent_proof", reason.HOLD_D2_INTENT_PROOF),
    ):
        for proof in WakeHubProofState:
            if proof is not current:
                cases.append((_send_preview(authorization=_send_authorization(**{name: proof})),
                              code, advice.HOLD))
    for state in WakeHubCompletionState:
        if state is WakeHubCompletionState.NEW_WORK_COMPLETION:
            continue
        code, decision = reason.HOLD_COMPLETION_PROOF, advice.HOLD
        if state is WakeHubCompletionState.ABSENT:
            code, decision = reason.WAIT_NEW_WORK_COMPLETION, advice.WAIT
        elif state is WakeHubCompletionState.DUPLICATE:
            code, decision = reason.RECONCILE_DUPLICATE_KEY, advice.RECONCILE_UNKNOWN_NO_RETRY
        cases.append((_send_preview(authorization=_send_authorization(completion=state)), code, decision))
    for state in WakeHubConsentState:
        if state is not WakeHubConsentState.CURRENT:
            cases.append((_send_preview(authorization=_send_authorization(consent=state)),
                          reason.HOLD_SEND_ONLY_CONSENT, advice.HOLD))
    for scope in (WakeHubSendConsentScope.OTHER, WakeHubSendConsentScope.ABSENT):
        cases.append((_send_preview(authorization=_send_authorization(consent_scope=scope)),
                      reason.HOLD_SEND_ONLY_CONSENT, advice.HOLD))
    cases.append((_send_preview(authorization=_send_authorization(intent=WakeHubSendIntentState.NOT_RECORDED)),
                  reason.WAIT_D2_WRITE_AHEAD_INTENT, advice.WAIT))
    for changes in (
        dict(authorization=_send_authorization(intent=WakeHubSendIntentState.DUPLICATE_KEY)),
        dict(delivery=WakeHubSendDeliveryState.DUPLICATE_KEY),
        dict(autoload=_autoload_evidence(readiness=_smart_case(
            "origin", event=WakeHubEventState.DUPLICATE_AMBIGUOUS))),
    ):
        cases.append((_send_preview(**changes), reason.RECONCILE_DUPLICATE_KEY,
                      advice.RECONCILE_UNKNOWN_NO_RETRY))
    for delivery in (WakeHubSendDeliveryState.ATTEMPT_BEGUN,
                     WakeHubSendDeliveryState.RECEIPT_LOST,
                     WakeHubSendDeliveryState.OUTCOME_UNOBSERVED):
        for proof in WakeHubProofState:
            complete = proof is current
            cases.append((_send_preview(delivery=delivery, ui_user_turn=proof),
                          reason.COMPLETE_UI_USER_TURN_WITHOUT_SERVER_ACK if complete
                          else reason.RECONCILE_PRIOR_ATTEMPT,
                          advice.COMPLETE_UI_ONLY if complete else advice.RECONCILE_UNKNOWN_NO_RETRY))
    return cases


@pytest.mark.parametrize(("evidence", "reason", "advice"), _send_policy_cases())
def test_wh06a_every_send_policy_gate(evidence, reason, advice):
    plan = assess_wakehub_send_only_preview(evidence)
    assert plan == assess_wakehub_send_only_preview(evidence)
    assert plan.reason is reason
    assert plan.advice is advice
    expected_safety = {
        WakeHubSendPreviewAdvice.PREVIEW_READY_ADVISORY_ONLY: WakeHubSendPreviewSafety.ADVISORY_NO_EFFECT,
        WakeHubSendPreviewAdvice.WAIT: WakeHubSendPreviewSafety.BLOCKED_NO_EFFECT,
        WakeHubSendPreviewAdvice.HOLD: WakeHubSendPreviewSafety.BLOCKED_NO_EFFECT,
        WakeHubSendPreviewAdvice.RECONCILE_UNKNOWN_NO_RETRY: WakeHubSendPreviewSafety.RECONCILE_ONLY_NO_RETRY,
        WakeHubSendPreviewAdvice.COMPLETE_UI_ONLY: WakeHubSendPreviewSafety.UI_ONLY_WITHOUT_SERVER_ACK,
    }
    assert plan.safety_state is expected_safety[advice]
    _assert_wh06a_no_effect(plan)


def _send_upstream_cases():
    cases = []
    for section, changes, reason in (
        ("core", dict(source_current=False), "HOLD_SOURCE_DRIFT"),
        ("core", dict(hostops_listener=WakeHubListenerState.ABSENT), "WAIT_RECEIVER"),
        ("core", dict(pending_work=WakeHubWorkState.PENDING), "WAIT_PENDING_WORK"),
        ("core", dict(interactive_session_proven=False), "WAIT_INTERACTIVE_SESSION"),
        ("core", dict(chrome_ownership=WakeHubChromeOwnership.ABSENT_INDEPENDENTLY_VERIFIED), "WAIT_CHROME"),
        ("browser", dict(port=9333), "HOLD_CHROME_ENDPOINT"),
        ("browser", dict(ownership=WakeHubChromeOwnership.PROFILE_CONFLICT), "HOLD_CHROME_OWNERSHIP"),
        ("browser", dict(host=WakeHubEndpointHost.IPV6_LOOPBACK), "HOLD_CHROME_ENDPOINT"),
        ("browser", dict(pid=0), "HOLD_CHROME_IDENTITY"),
        ("browser", dict(profile=WakeHubProofState.MISMATCH), "HOLD_CHROME_IDENTITY"),
        ("browser", dict(retained_account=WakeHubProofState.MISSING), "HOLD_BROWSER_ACCOUNT"),
        ("origin", dict(event=WakeHubEventState.ABSENT), "WAIT_EVENT"),
        ("origin", dict(event=WakeHubEventState.STALE), "HOLD_EVENT_STALE"),
        ("origin", dict(issuer=WakeHubProofState.MISSING), "HOLD_ORIGIN_ISSUER"),
        ("origin", dict(owner=WakeHubProofState.MISMATCH), "HOLD_ORIGIN_OWNER"),
        ("origin", dict(route=WakeHubProofState.MISSING), "HOLD_ORIGIN_ROUTE"),
        ("origin", dict(generation=WakeHubProofState.STALE), "HOLD_ORIGIN_GENERATION"),
        ("origin", dict(consent=WakeHubConsentState.REVOKED), "HOLD_ORIGIN_CONSENT"),
    ):
        cases.append((_autoload_evidence(readiness=_smart_case(section, **changes)),
                      WakeHubAutoloadReason[reason]))
    for changes, reason in (
        (dict(projection=WakeHubProofState.MISSING), "HOLD_PRIVATE_PROJECTION"),
        (dict(session_owner=WakeHubProofState.MISMATCH), "HOLD_SESSION_OWNER"),
    ):
        cases.append((_autoload_evidence(readiness=_smart_evidence(**changes)),
                      WakeHubAutoloadReason[reason]))
    for changes, reason in (
        (dict(tabs=(_autoload_tab(), _autoload_tab(tab_id=2))), "HOLD_DUPLICATE_MATCHING_TABS"),
        (dict(dedicated_context=WakeHubProofState.MISSING), "WAIT_DEDICATED_CONTEXT"),
        (dict(dedicated_context=WakeHubProofState.MISMATCH), "HOLD_NON_DEDICATED_CONTEXT"),
        (dict(tab_generation=WakeHubProofState.STALE), "HOLD_GENERATION_DRIFT"),
        (dict(unrelated_tab_mutation=True), "HOLD_UNRELATED_TAB_MUTATION"),
        (dict(route_authorized=WakeHubProofState.MISSING), "HOLD_ORIGIN_ROUTE"),
    ):
        cases.append((_autoload_evidence(**changes), WakeHubAutoloadReason[reason]))
    for changes, reason in (
        (dict(chat_binding=WakeHubChatBinding.WRONG_CHAT_B), "HOLD_WRONG_CHAT_TARGET"),
        (dict(chat_binding=WakeHubChatBinding.UNBOUND), "HOLD_WRONG_CHAT_TARGET"),
        (dict(marker=WakeHubProofState.AMBIGUOUS), "HOLD_MARKER_AMBIGUOUS"),
        (dict(security_challenge_present=True), "HOLD_LOGIN_OR_CAPTCHA_CHALLENGE"),
        (dict(human_draft_present=True), "HOLD_HUMAN_DRAFT_PRESENT"),
        (dict(actively_generating=True), "WAIT_RESPONSE_STREAMING_ACTIVE"),
        (dict(redirect_drift=True), "HOLD_GENERATION_DRIFT"),
        (dict(post_navigation_recheck=WakeHubProofState.MISSING), "WAIT_POST_NAVIGATION_RECHECK"),
        (dict(post_navigation_recheck=WakeHubProofState.STALE), "HOLD_POST_NAVIGATION_RECHECK_REQUIRED"),
        (dict(account_proof=WakeHubProofState.MISMATCH), "HOLD_BROWSER_ACCOUNT"),
    ):
        cases.append((_autoload_evidence(tabs=(_autoload_tab(**changes),)), WakeHubAutoloadReason[reason]))
    return cases


@pytest.mark.parametrize(("autoload", "reason"), _send_upstream_cases())
def test_wh06a_derives_unready_from_wh05a_and_wh04a(autoload, reason):
    plan = assess_wakehub_send_only_preview(_send_preview(autoload=autoload))
    assert plan.reason is reason
    assert plan.advice in (WakeHubSendPreviewAdvice.WAIT, WakeHubSendPreviewAdvice.HOLD)
    _assert_wh06a_no_effect(plan)


@pytest.mark.parametrize("unknown", [False, True])
def test_wh06a_human_pause_wins_even_over_unknown_or_completed_ui(unknown):
    evidence = _send_preview(
        autoload=_autoload_evidence(readiness=_smart_case(
            "core", trusted_human_pause=True, unknown_effect=unknown)),
        delivery=WakeHubSendDeliveryState.RECEIPT_LOST,
        ui_user_turn=WakeHubProofState.INDEPENDENTLY_CURRENT,
    )
    plan = assess_wakehub_send_only_preview(evidence)
    assert plan.advice is WakeHubSendPreviewAdvice.HOLD
    assert plan.reason is WakeHubAutoloadReason.HOLD_HUMAN_PAUSED
    assert plan.paused_by_human
    _assert_wh06a_no_effect(plan)


@pytest.mark.parametrize("changes", [dict(actively_generating=True), dict(human_draft_present=True)])
def test_wh06a_ui_completion_is_independent_of_composer_and_send_controls(changes):
    evidence = _send_preview(
        autoload=_autoload_evidence(tabs=(_autoload_tab(**changes),)),
        delivery=WakeHubSendDeliveryState.ATTEMPT_BEGUN,
        ui_user_turn=WakeHubProofState.INDEPENDENTLY_CURRENT,
        composer_empty=False, send_control_count=0, send_control_enabled=False,
    )
    plan = assess_wakehub_send_only_preview(evidence)
    assert plan.advice is WakeHubSendPreviewAdvice.COMPLETE_UI_ONLY
    assert plan.safety_state is WakeHubSendPreviewSafety.UI_ONLY_WITHOUT_SERVER_ACK
    _assert_wh06a_no_effect(plan)
    for drift in (dict(redirect_drift=True), dict(generation=WakeHubProofState.STALE),
                  dict(post_navigation_recheck=WakeHubProofState.MISSING)):
        changed = replace(evidence, autoload=_autoload_evidence(tabs=(_autoload_tab(**(changes | drift)),)))
        assert assess_wakehub_send_only_preview(changed).advice is WakeHubSendPreviewAdvice.RECONCILE_UNKNOWN_NO_RETRY


@pytest.mark.parametrize("section", ["native_observer", "origin_binding", "intent_proof", "lease", "consent_signature"])
def test_wh06a_prior_attempt_cannot_be_retried_when_private_proof_is_missing(section):
    evidence = _send_preview(
        authorization=_send_authorization(**{section: WakeHubProofState.MISSING}),
        delivery=WakeHubSendDeliveryState.RECEIPT_LOST,
        ui_user_turn=WakeHubProofState.INDEPENDENTLY_CURRENT,
    )
    plan = assess_wakehub_send_only_preview(evidence)
    assert plan.advice is WakeHubSendPreviewAdvice.RECONCILE_UNKNOWN_NO_RETRY
    _assert_wh06a_no_effect(plan)


@pytest.mark.parametrize("name", ["queue_state", "pending_work", "outstanding_send",
                                 "outstanding_signer", "outstanding_lease", "pending_wake"])
@pytest.mark.parametrize("state", [WakeHubWorkState.PENDING, WakeHubWorkState.UNKNOWN])
def test_wh06a_pending_or_unknown_work_never_becomes_send_ready(name, state):
    plan = assess_wakehub_send_only_preview(_send_preview(
        autoload=_autoload_evidence(readiness=_smart_case("core", **{name: state}))))
    assert plan.advice is (WakeHubSendPreviewAdvice.WAIT if state is WakeHubWorkState.PENDING
                           else WakeHubSendPreviewAdvice.RECONCILE_UNKNOWN_NO_RETRY)
    _assert_wh06a_no_effect(plan)


def test_wh06a_lost_core_receipt_is_not_resolved_by_an_unrelated_ui_witness():
    evidence = _send_preview(
        autoload=_autoload_evidence(readiness=_smart_case("core", lost_effect_receipt=True)),
        delivery=WakeHubSendDeliveryState.RECEIPT_LOST,
        ui_user_turn=WakeHubProofState.INDEPENDENTLY_CURRENT,
    )
    plan = assess_wakehub_send_only_preview(evidence)
    assert plan.advice is WakeHubSendPreviewAdvice.RECONCILE_UNKNOWN_NO_RETRY
    assert plan.reason is WakeHubSendPreviewReason.RECONCILE_UNKNOWN_EFFECT


@pytest.mark.parametrize(("name", "value"), [
    ("autoload", {}), ("autoload", assess_wakehub_chat_autoload_plan(_autoload_evidence())),
    ("authorization", {}), ("composer_empty", 1), ("composer_empty", "secret-value"),
    ("send_control_count", True), ("send_control_count", -1), ("send_control_count", 2**32),
    ("send_control_count", 10**1000), ("send_control_count", 1.0),
    ("send_control_count", float("inf")), ("send_control_count", float("nan")),
    ("send_control_visible", 0), ("send_control_enabled", None),
    ("delivery", "NOT_ATTEMPTED"), ("delivery", WakeHubWorkState.CLEAR),
    ("ui_user_turn", "INDEPENDENTLY_CURRENT"), ("ui_user_turn", True),
])
def test_wh06a_strict_closed_schema_rejects_untrusted_values(name, value):
    with pytest.raises(WakeHubSendPreviewInputError) as caught:
        replace(_send_preview(), **{name: value})
    assert caught.value.args == ("WAKEHUB_SEND_PREVIEW_INPUT_INVALID",)


def test_wh06a_all_authorization_fields_are_strict_and_errors_sanitized():
    evidence = _send_preview()
    for item in fields(evidence.authorization):
        for value in ("secret-value", True, 1, None, WakeHubWorkState.CLEAR):
            with pytest.raises(WakeHubSendPreviewInputError) as caught:
                replace(evidence.authorization, **{item.name: value})
            assert caught.value.args == ("WAKEHUB_SEND_PREVIEW_INPUT_INVALID",)
    for item in (evidence, evidence.authorization):
        values = {f.name: getattr(item, f.name) for f in fields(item)}
        for invalid in ({}, values | {"secret-key": "secret-value"},
                        values | {"verified": True}, values | {"send": True}):
            with pytest.raises(WakeHubSendPreviewInputError) as caught:
                type(item)(**invalid)
            assert caught.value.args == ("WAKEHUB_SEND_PREVIEW_INPUT_INVALID",)
        with pytest.raises(WakeHubSendPreviewInputError):
            type(item)(values)
    for invalid in (None, {}, "secret-value", (evidence,)):
        with pytest.raises(WakeHubSendPreviewInputError) as caught:
            assess_wakehub_send_only_preview(invalid)
        assert caught.value.args == ("WAKEHUB_SEND_PREVIEW_INPUT_INVALID",)
    with pytest.raises(WakeHubSendPreviewInputError) as caught:
        assess_wakehub_send_only_preview(evidence, **{"secret-key": "secret-value"})
    assert caught.value.args == ("WAKEHUB_SEND_PREVIEW_INPUT_INVALID",)


def test_wh06a_rejects_subclasses_forged_enums_and_nested_invalid_evidence():
    class UnsafeInt(int):
        def __le__(self, other):
            pytest.fail("numeric subclass executed")

    class UnsafeEvidence(WakeHubSendOnlyPreviewEvidence):
        def _validate(self):
            pytest.fail("evidence subclass executed")

    class UnsafeAuthorization(WakeHubSendAuthorizationEvidence):
        def _validate(self):
            pytest.fail("authorization subclass executed")

    for invalid in (object.__new__(UnsafeEvidence), object.__new__(WakeHubSendOnlyPreviewEvidence)):
        with pytest.raises(WakeHubSendPreviewInputError):
            assess_wakehub_send_only_preview(invalid)
    with pytest.raises(WakeHubSendPreviewInputError):
        _send_preview(authorization=object.__new__(UnsafeAuthorization))
    with pytest.raises(WakeHubSendPreviewInputError):
        _send_preview(send_control_count=UnsafeInt(1))
    for enum_type, name in ((WakeHubSendDeliveryState, "delivery"), (WakeHubProofState, "ui_user_turn")):
        forged = str.__new__(enum_type, next(iter(enum_type)).value)
        with pytest.raises(WakeHubSendPreviewInputError):
            _send_preview(**{name: forged})
    bad_core = replace(_smart_evidence().core, source_current="secret-value")
    # Revalidate nested WH01/02/04/05 facts; never accept a stored READY plan.
    bad_smart = object.__new__(WakeHubSmartReadinessEvidence)
    for item in fields(_smart_evidence()):
        object.__setattr__(bad_smart, item.name, bad_core if item.name == "core"
                           else getattr(_smart_evidence(), item.name))
    bad_autoload = object.__new__(WakeHubChatAutoloadEvidence)
    for item in fields(_autoload_evidence()):
        object.__setattr__(bad_autoload, item.name, bad_smart if item.name == "readiness"
                           else getattr(_autoload_evidence(), item.name))
    with pytest.raises(WakeHubSendPreviewInputError) as caught:
        _send_preview(autoload=bad_autoload)
    assert caught.value.args == ("WAKEHUB_SEND_PREVIEW_INPUT_INVALID",)


def test_wh06a_nested_projections_and_every_no_effect_flag_are_immutable():
    evidence = _send_preview()
    plan = assess_wakehub_send_only_preview(evidence)
    for item, name in ((evidence, "composer_empty"), (evidence.authorization, "native_observer"),
                       (plan, "advice")):
        assert not hasattr(item, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(item, name, True)
    for item in fields(plan):
        if not item.init:
            original = getattr(plan, item.name)
            with pytest.raises(FrozenInstanceError):
                setattr(plan, item.name, True)
            with pytest.raises(
                (TypeError, ValueError),
                match=rf"^field {item.name} is declared with init=False,",
            ):
                replace(plan, **{item.name: True})
            assert getattr(plan, item.name) == original


def test_wh06a_all_reasons_and_invalid_inputs_under_external_effect_traps(monkeypatch):
    cases = _send_policy_cases()
    upstream_cases = _send_upstream_cases()

    def forbidden(*args, **kwargs):
        raise AssertionError("WH06A attempted an external effect")

    seen = set()
    with monkeypatch.context() as guard:
        for owner, names in (
            (builtins, ("open",)),
            (Path, ("open", "read_text", "read_bytes", "write_text", "write_bytes", "stat",
                    "iterdir", "mkdir", "unlink", "rename", "replace")),
            (os, ("open", "stat", "listdir", "scandir", "mkdir", "remove", "rename", "replace",
                  "system", "kill", "startfile", "popen", "spawnv", "spawnve")),
            (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
            (socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname")),
            (time, ("sleep", "time", "monotonic", "perf_counter")),
        ):
            for name in names:
                guard.setattr(owner, name, forbidden)
        for evidence, reason, advice in cases:
            plan = assess_wakehub_send_only_preview(evidence)
            assert plan == assess_wakehub_send_only_preview(evidence)
            assert plan.reason is reason and plan.advice is advice
            seen.add(plan.reason)
            _assert_wh06a_no_effect(plan)
        for autoload, reason in upstream_cases:
            assert assess_wakehub_send_only_preview(_send_preview(autoload=autoload)).reason is reason
        for invalid in (None, {"token": "secret-value"}):
            with pytest.raises(WakeHubSendPreviewInputError):
                assess_wakehub_send_only_preview(invalid)
    assert seen == set(WakeHubSendPreviewReason)

