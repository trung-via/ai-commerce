"""Local mock content job; no network, paid model, product sale or media rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import (
    canonical_bytes, put_immutable, sha256_bytes, source_snapshot,
)
from ai_commerce.providers.base import ContentProvider, MockContentProvider, ProviderError


def run_mock_job(brief_path: Path, output_dir: Path,
                 provider: ContentProvider | None = None) -> dict[str, Any]:
    """Return stable job receipt. An existing same-input receipt is reused.

    This is deliberately a small A02 fixture capability, not O02 media-job runtime.
    """
    raw = brief_path.read_bytes()
    brief = validate_record("ContentBrief", json.loads(raw.decode("utf-8")))
    digest = sha256_bytes(raw)
    job_id = "mock-" + digest
    target = output_dir / f"{job_id}.result.json"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        validate_record("JobResult", existing)
        snapshot = existing["source_snapshot"]
        if snapshot is None or snapshot["sha256"] != digest:
            raise FileExistsError("RESULT_SOURCE_MISMATCH")
        artifact = existing["artifact"]
        if artifact is not None:
            output_path = output_dir / artifact["path"]
            if not output_path.is_file() or sha256_bytes(output_path.read_bytes()) != artifact["sha256"]:
                raise FileNotFoundError("ARTIFACT_MISSING_OR_CORRUPTED")
        return existing

    service = provider or MockContentProvider()
    capability = service.capabilities
    if capability.network_required or capability.external_spend_possible:
        raise ProviderError("MOCK_ONLY", "A02 mock runner forbids providers with network or spending")
    snapshot = source_snapshot(brief_path, raw)
    validate_record("SourceSnapshot", snapshot)
    evidence = {
        "evidence_id": "source:" + digest,
        "source_kind": "LOCAL_JSON_FILE",
        "source_ref": snapshot["source_ref"],
        "observed_at": snapshot["captured_at"],
        "sha256": digest,
        "verification_status": "SOURCE_OBSERVED",
        "rights_status": "UNKNOWN",
    }
    validate_record("EvidenceRef", evidence)
    result: dict[str, Any] = {
        "job_id": job_id, "status": "FAILED", "provider": capability.name,
        "source_snapshot": snapshot, "artifact": None,
        "evidence_refs": [evidence["evidence_id"]], "evidence": [evidence],
        "cost_status": "NOT_APPLICABLE", "cost_amount": None,
        "error_code": None, "error_detail": None,
        "publication_status": "NOT_ATTEMPTED",
    }
    try:
        output = service.generate(brief)
        if not isinstance(output, bytes):
            raise ProviderError("INVALID_PROVIDER_OUTPUT", "Expected bytes from test provider")
        file_name = f"{job_id}.txt"
        artifact = {
            "artifact_id": "artifact:" + sha256_bytes(output),
            "kind": "text", "mime_type": "text/plain", "sha256": sha256_bytes(output),
            "size_bytes": len(output), "path": file_name,
            "source_snapshot_ids": [snapshot["snapshot_id"]],
            "rights_status": "UNKNOWN", "provider": capability.name,
        }
        validate_record("MediaArtifact", artifact)
        put_immutable(output_dir / file_name, output)
        result["artifact"] = artifact
        result["status"] = "SUCCEEDED"
    except ProviderError as exc:
        result["error_code"] = exc.error_code
        result["error_detail"] = str(exc)
    except Exception as exc:
        result["error_code"] = "PROVIDER_ERROR"
        result["error_detail"] = f"{type(exc).__name__}: {exc}"
    validate_record("JobResult", result)
    put_immutable(target, canonical_bytes(result) + b"\n")
    return result
