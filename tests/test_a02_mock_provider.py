"""Concrete mock-only provider integration and failure cases."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.providers.base import MockContentProvider, ProviderCapabilities, ProviderError
from ai_commerce.providers.mock_run import run_mock_job

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/creative_only_brief.json"


def test_mock_output_is_traceable_and_noncommercial(tmp_path: Path) -> None:
    result = run_mock_job(FIXTURE, tmp_path)
    validate_record("JobResult", result)
    assert result["status"] == "SUCCEEDED"
    assert result["provider"] == "local-mock"
    assert result["publication_status"] == "NOT_ATTEMPTED"
    assert result["source_snapshot"]["sha256"] == sha256_bytes(FIXTURE.read_bytes())
    assert result["artifact"]["source_snapshot_ids"] == [result["source_snapshot"]["snapshot_id"]]
    assert result["artifact"]["rights_status"] == "UNKNOWN"
    assert result["artifact"]["kind"] == "text"
    assert result["cost_status"] == "NOT_APPLICABLE" and result["cost_amount"] is None
    assert (tmp_path / result["artifact"]["path"]).read_bytes().startswith(b"AI Commerce MOCK")


def test_same_input_does_not_overwrite_manifest(tmp_path: Path) -> None:
    first = run_mock_job(FIXTURE, tmp_path)
    target = tmp_path / f"{first['job_id']}.result.json"
    before = target.read_bytes()
    second = run_mock_job(FIXTURE, tmp_path)
    assert first == second
    assert target.read_bytes() == before


def test_content_change_changes_source_and_artifact_identity(tmp_path: Path) -> None:
    inp = tmp_path / "brief.json"
    inp.write_bytes(FIXTURE.read_bytes())
    first = run_mock_job(inp, tmp_path)
    inp.write_text(json.dumps({"brief_id": "new", "objective": "Another distinct objective"}), encoding="utf-8")
    second = run_mock_job(inp, tmp_path)
    assert first["job_id"] != second["job_id"]
    assert first["artifact"]["sha256"] != second["artifact"]["sha256"]


def test_missing_artifact_is_not_claimed_success_on_resume(tmp_path: Path) -> None:
    result = run_mock_job(FIXTURE, tmp_path)
    (tmp_path / result["artifact"]["path"]).unlink()
    with pytest.raises(FileNotFoundError, match="ARTIFACT_MISSING_OR_CORRUPTED"):
        run_mock_job(FIXTURE, tmp_path)


def test_tampered_manifest_does_not_silently_pass(tmp_path: Path) -> None:
    result = run_mock_job(FIXTURE, tmp_path)
    target = tmp_path / f"{result['job_id']}.result.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["source_snapshot"]["sha256"] = "0" * 64
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FileExistsError, match="RESULT_SOURCE_MISMATCH"):
        run_mock_job(FIXTURE, tmp_path)


class BrokenProvider(MockContentProvider):
    def generate(self, brief: dict) -> bytes:
        raise ProviderError("MOCK_FAILURE", "simulated failure")


def test_provider_error_has_failure_receipt_and_no_artifact(tmp_path: Path) -> None:
    result = run_mock_job(FIXTURE, tmp_path, BrokenProvider())
    assert result["status"] == "FAILED"
    assert result["error_code"] == "MOCK_FAILURE"
    assert result["artifact"] is None
    assert list(tmp_path.glob("*.result.json"))
    assert not list(tmp_path.glob("*.txt"))


class NetworkProvider(MockContentProvider):
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("not-for-A02", ("text",), True, True)


def test_mock_runner_rejects_provider_that_could_spend_or_call_network(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="forbids providers"):
        run_mock_job(FIXTURE, tmp_path, NetworkProvider())


def test_cli_validate_and_mock_generate(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    base = [sys.executable, "-m", "ai_commerce"]
    good = subprocess.run(base + ["validate", "--schema", "ContentBrief", "--file", str(FIXTURE)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert good.returncode == 0 and "VALID" in good.stdout
    bad = subprocess.run(base + ["validate", "--schema", "ContentBrief", "--file", str(ROOT / "tests/fixtures/invalid_brief.json")], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert bad.returncode == 2 and "INVALID_ContentBrief" in bad.stderr
    generated = subprocess.run(base + ["mock-generate", "--brief", str(FIXTURE), "--out-dir", str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert generated.returncode == 0, generated.stderr
    payload = json.loads(generated.stdout)
    assert payload["status"] == "SUCCEEDED"
    assert payload["artifact"]["kind"] == "text"
    assert len(list(tmp_path.glob("*.result.json"))) == 1
