"""Offline acceptance for the non-disruptive three-runner Windows bootstrap.

No registration token, GitHub call, Windows task or model is invoked here.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from ai_commerce.engineering.three_runner_topology import (
    EXISTING_RUNNER, NEW_RUNNERS, hostops_route_activation_allowed,
    planned_topology, resource_policy,
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
    assert "CPU_QUOTA" not in source
    assert "RAM_QUOTA" not in source


def test_current_route_intentionally_unmoved_until_live_hostops_online():
    ingress = INGRESS.read_text(encoding="utf-8")
    ef02 = EF02.read_text(encoding="utf-8")
    assert "  hostops-personal:" in ingress
    assert "group: personal-hostops-workspace" in ingress
    assert "github.event.comment.body == format('APPROVE WRITE #{0}', github.event.issue.number)" in ingress
    assert "runs-on: [self-hosted, Windows, ai-commerce-ef02]" in ingress
    assert "  execute:" in ef02
    assert "runs-on: [self-hosted, Windows, ai-commerce-ef02]" in ef02
    assert "group: ef02-issue-" in ef02
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
