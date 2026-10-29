"""Source-verified, no-provider regression for the isolated process probe."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "ef02-m0r-child-probe.py"


def test_isolated_child_is_killed_but_stale_receipt_cannot_claim_pass(tmp_path):
    spec = importlib.util.spec_from_file_location("ef02_m0r_child_probe", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "diagnostics" / "child-probe.json"

    result = module.run_probe(out)
    persisted = json.loads(out.read_text(encoding="utf-8"))
    assert persisted == result
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "SIMULATED_CHILD_INTERRUPTED"
    assert result["receipt_after_interrupt"] is True
    assert result["receipt_status_ignored"] == "BUILD_VERIFIED_FOR_SCOPE"
    assert result["child_terminated"] is True
    assert isinstance(result["child_process_id"], int)
    assert result["child_exit_code"] is not None
    assert result["model_invoked"] is False
    assert result["executor_invoked"] is False
    assert result["claim_ref"] is None and result["candidate_ref"] is None
    assert result["replay_authorized"] is False
    assert result["scope"] == "OWNED_CHILD_PROCESS_ONLY_NOT_REAL_RUNNER_CRASH"


def test_probe_workflow_is_owner_only_and_never_runs_ef02_executor():
    workflow = (ROOT / ".github" / "workflows" /
                "ef02-m0r-windows-child-probe.yml").read_text(encoding="utf-8")
    primary = (ROOT / ".github" / "workflows" /
               "ef02-batch.yml").read_text(encoding="utf-8")
    assert "github.actor == github.repository_owner" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "runs-on: [self-hosted, Windows, ai-commerce-ef02]" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "python .github/scripts/ef02-m0r-child-probe.py" in workflow
    assert "python -m ai_commerce.remote run" not in workflow
    assert "codex " not in workflow.lower()
    assert "antigravity " not in workflow.lower()
    assert "on:\n  workflow_dispatch:" in workflow
    assert "  issues:" not in workflow
    # Primary EF02 validate is gated by [EF02] Issue prefix, not [M0R].
    assert "startsWith(github.event.issue.title, '[EF02]')" in primary
    assert "github.event_name == 'workflow_dispatch'" in primary
