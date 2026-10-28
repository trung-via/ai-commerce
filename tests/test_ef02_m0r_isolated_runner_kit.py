"""Structural guard tests; never touch a service, token or Windows runner."""
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
WF = BASE / ".github/workflows/ef02-m0r-isolated-runner.yml"
PS = BASE / "scripts/EF02-M0R-IsolatedRunner-Control.ps1"
DOC = BASE / "docs/engineering/EF02_M0R_ISOLATED_RUNNER_LOSS.md"


def test_workflow_routes_only_isolated_runner_without_executor():
    text = WF.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert text.count("runs-on: [self-hosted, Windows, X64, ai-commerce-m0r-isolated]") == 2
    assert text.count("github.actor == github.repository_owner") == 2
    assert text.count("AI-COMMERCE-M0R-ISOLATED-01") == 2
    assert "ai-commerce-ef02" not in text
    assert "Start-Sleep -Seconds 1200" in text
    assert "Stop-Service" not in text and "GITHUB_TOKEN" not in text
    assert "codex exec" not in text and "agy " not in text


def test_service_control_identity_and_recovery_guards():
    text = PS.read_text(encoding="utf-8")
    required = [
        "SupportsShouldProcess=$true",
        "[string]$Mode = 'Inspect'",
        "AI-COMMERCE-M0R-ISOLATED-01",
        "actions.runner.trung-via-ai-commerce.AI-COMMERCE-M0R-ISOLATED-01",
        r"C:\TOOL\ai-commerce-m0r-isolated-runner",
        "SHARED_WINDOWS_HOST_OR_OTHER_RUNNER_DETECTED",
        "ISOLATED_SERVICE_BINARY_PATH_MISMATCH",
        "ISOLATED_RUNNER_CONFIG_IDENTITY_MISMATCH",
        "Assert-Administrator",
        "PRIOR_OUTAGE_MARKER_PRESENT_NO_SECOND_STOP",
        "INDEPENDENT_RECOVERY_WATCHDOG_NOT_ARMED",
        "'STOP-ISOLATED-M0R-ONLY'",
        "schtasks.exe /Create /SC MINUTE /MO 1",
        "Stop-Service -Name $ServiceName",
        "Start-Service -Name $ServiceName",
        "[DateTimeOffset]::UtcNow.AddMinutes(12)",
        "[IO.FileMode]::CreateNew",
    ]
    for value in required:
        assert value in text, value
    assert "Restart-Computer" not in text
    assert "Stop-Computer" not in text


def test_documentation_is_explicitly_nonexecuting_and_fail_closed():
    text = DOC.read_text(encoding="utf-8")
    assert "PREPARED, NOT EXECUTED" in text
    assert "separate Windows x64 VM" in text
    assert "AI-COMMERCE-EF02-01" in text
    assert "watchdog_installed" in text
    assert "no EF01 or model invocation" in text
    assert "UNKNOWN" in text
