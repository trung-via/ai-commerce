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
