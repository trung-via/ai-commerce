"""Offline acceptance for the non-disruptive three-runner Windows bootstrap.

No registration token, GitHub call, Windows task or model is invoked here.
"""
from __future__ import annotations

import builtins
from dataclasses import FrozenInstanceError
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
