"""Regression: M0-R diagnostics must never subscribe to ordinary EF02 Issues."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    "ef02-m0r-windows-boundary.yml",
    "ef02-m0r-worker-os-kill.yml",
    "ef02-m0r-codex-provider-interruption.yml",
    "ef02-m0r-hosted-checkpoint.yml",
    "ef02-m0r-real-e2e-interruption.yml",
    "ef02-m0r-windows-child-probe.yml",
)

def test_m0r_diagnostics_manual_only_with_owner_main_guard():
    for name in WORKFLOWS:
        path = ROOT / ".github" / "workflows" / name
        workflow = path.read_text(encoding="utf-8")
        assert "on:\n  workflow_dispatch:" in workflow, name
        assert "  issues:" not in workflow, name
        assert "github.actor == github.repository_owner" in workflow, name
        assert "github.ref == 'refs/heads/main'" in workflow, name
        assert "runs-on:" in workflow, name

def test_ef02_primary_issue_ingress_unchanged():
    workflow = (ROOT / ".github" / "workflows" / "ef02-batch.yml").read_text(encoding="utf-8")
    assert "on:\n  issues:\n    types: [opened, labeled]" in workflow
    assert "startsWith(github.event.issue.title, '[EF02]')" in workflow
