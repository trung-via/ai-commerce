"""Single-worker offline asset jobs, with immutable attempts and no blind replay."""

from __future__ import annotations

import os
import stat
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes, source_snapshot, timestamp_utc
from ai_commerce.media import (
    Cost, LocalImportAdapter, MediaCapabilities, MediaInput, MediaOutput, MediaProvider,
    MediaProviderError, MediaRequest, MediaValidationError, MockMediaProvider,
)
from ai_commerce.media.local import validate_media
from ai_commerce.media.files import FileCapture, SUPPORTED_MIME_KINDS, validate_media_file
from .errors import (
    JobCollisionError, ManifestValidationError, MediaOperationError, RetryUnsafeError,
)
from .manifest import AssetJobManifest, JOB_ID, JobError, strict_json, validate_request
from .registry import ArtifactRegistry


class LocalMediaRunner:
    def __init__(self, registry: ArtifactRegistry):
        self.registry = registry

    @staticmethod
    def manifest_ref(job_id: str, attempt: int = 1) -> str:
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise ValueError("INVALID_JOB_ID")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("INVALID_ATTEMPT")
        return f"jobs/{job_id}/attempt-{attempt}.manifest.json"

    def load(self, job_id: str, attempt: int = 1) -> AssetJobManifest:
        """Validate identity, provenance, state and actual output bytes for A05."""
        ref = self.manifest_ref(job_id, attempt)
        manifest = AssetJobManifest.from_dict(strict_json(self.registry.read(ref)))
        binding = strict_json(self.registry.read(f"jobs/{job_id}/request.json"))
        expected = {"job_id": job_id, "request_digest": manifest.request_digest,
                    "request": manifest.request}
        if binding != expected or manifest.job_id != job_id or manifest.attempt != attempt:
            raise ManifestValidationError("Manifest does not match its job/attempt/request binding")
        if manifest.state == "RUNNING":
            raise ManifestValidationError("A final manifest cannot be RUNNING")
        artifact = manifest.result["artifact"]
        if artifact is not None:
            if artifact["mime_type"] in {"image/jpeg", "image/webp", "video/mp4"}:
                self.registry.verify_file(artifact)
            else:
                self.registry.verify(artifact)
        return manifest

    def run(self, request: MediaRequest, *, job_id: str | None = None, attempt: int = 1,
            provider: MediaProvider | None = None) -> AssetJobManifest:
        record, media_input, snapshot, service = self._prepare(request, provider)
        return self._run_prepared(request, record, media_input, snapshot, service,
                                  job_id=job_id, attempt=attempt)

    def import_file(self, request: MediaRequest, *, job_id: str | None = None,
                    attempt: int = 1) -> AssetJobManifest:
        """Explicit opt-in streaming import; original run()/providers stay V1."""
        if not isinstance(request, MediaRequest) or request.mode != "import" or request.source_path is None:
            raise MediaValidationError("File import requires import mode and a local source")
        with self.registry.capture_file(request.source_path) as captured:
            validate_media_file(captured.path, request.mime_type)
            caps = MediaCapabilities("local-validated-file-import", "LOCAL_IMPORT",
                                     ("import",), tuple(SUPPORTED_MIME_KINDS))
            record = strict_json(canonical_bytes({
                "mode": "import", "mime_type": request.mime_type,
                "input": {"source_ref": captured.source_ref, "requested_ref": captured.requested_ref,
                          "sha256": captured.sha256, "size_bytes": captured.size_bytes},
                "rights_status": request.rights_status, "rights_ref": request.rights_ref,
                "provider": asdict(caps),
            }))
            try:
                validate_request(record)
            except (ValueError, TypeError) as exc:
                raise MediaValidationError(str(exc)) from exc
            snapshot = {
                "snapshot_id": "snapshot:" + captured.sha256, "source_ref": captured.source_ref,
                "source_kind": "LOCAL_MEDIA_FILE", "captured_at": timestamp_utc(),
                "sha256": captured.sha256, "verification_status": "SOURCE_OBSERVED",
                "rights_status": request.rights_status,
            }
            return self._run_prepared(request, record, None, snapshot, None,
                                      job_id=job_id, attempt=attempt, captured=captured)

    def _run_prepared(self, request, record, media_input, snapshot, service, *,
                      job_id, attempt, captured: FileCapture | None = None) -> AssetJobManifest:
        digest = sha256_bytes(canonical_bytes(record))
        job_id = job_id if job_id is not None else "media-" + digest
        final_ref = self.manifest_ref(job_id, attempt)
        request_ref = f"jobs/{job_id}/request.json"
        started_ref = final_ref.replace(".manifest.json", ".started.json")
        binding = {"job_id": job_id, "request_digest": digest, "request": record}
        with self.registry.worker():
            if self.registry.exists(request_ref):
                if strict_json(self.registry.read(request_ref)) != binding:
                    raise JobCollisionError("Job ID is already bound to different immutable input/provider identity")
            else:
                self.registry.put_immutable(request_ref, canonical_bytes(binding))
            if self.registry.exists(final_ref):
                return self.load(job_id, attempt)
            if attempt > 1:
                previous_ref = self.manifest_ref(job_id, attempt - 1)
                if not self.registry.exists(previous_ref):
                    raise RetryUnsafeError("A consecutive, reconciled previous attempt is required")
                previous = self.load(job_id, attempt - 1)
                if previous.state != "FAILED" or previous.retry_safety != "SAFE_LOCAL":
                    raise RetryUnsafeError("Previous attempt cannot be retried; reconcile unknown effects first")
            if self.registry.exists(started_ref):
                started = AssetJobManifest.from_dict(strict_json(self.registry.read(started_ref)))
                if (started.job_id != job_id or started.attempt != attempt
                        or started.request != record or started.state != "RUNNING"):
                    raise ManifestValidationError("Invalid interrupted-attempt record")
                # Never call the provider again merely because the final receipt is absent.
                final = self._manifest(job_id, attempt, record, started.result["source_snapshot"],
                                       "UNKNOWN", "PROVIDER", Cost(), None,
                                       "PROVIDER_OUTCOME_UNKNOWN", "Interrupted attempt requires reconciliation")
                return self._save(final_ref, final)
            if request.mode == "dry-run":
                final = self._manifest(job_id, attempt, record, snapshot, "DRY_RUN", "COMPLETE",
                                       Cost("NOT_APPLICABLE"), None, "DRY_RUN_NO_EXECUTION",
                                       "Plan only; no provider invocation or media output")
                return self._save(final_ref, final)
            started = self._manifest(job_id, attempt, record, snapshot, "RUNNING", "PROVIDER",
                                     Cost(), None, "INCOMPLETE_ATTEMPT",
                                     "Provider execution has not been reconciled")
            self._save(started_ref, started)
            stage, cost, artifact = "PROVIDER", Cost(), None
            try:
                if captured is not None:
                    stage, cost = "REGISTER", Cost("NOT_APPLICABLE")
                    artifact = self.registry.register_file(
                        captured.path, request.mime_type, provider=record["provider"]["name"],
                        snapshot=snapshot, mode="import", origin="LOCAL_IMPORT",
                        rights_ref=request.rights_ref,
                    )
                else:
                    output = service.produce(media_input)
                    stage = "REGISTER"
                    if not isinstance(output, MediaOutput) or not isinstance(output.cost, Cost):
                        raise MediaValidationError("Expected typed media output and cost")
                    cost = output.cost
                    if output.mime_type != record["mime_type"]:
                        raise MediaValidationError("Provider returned an unrequested MIME")
                    if request.mode == "import" and output.content != media_input.content:
                        raise MediaValidationError("Local import changed the true source bytes")
                    artifact = self.registry.register(
                        output.content, output.mime_type, provider=record["provider"]["name"],
                        snapshot=snapshot, mode=request.mode, origin=record["provider"]["origin"],
                        rights_ref=request.rights_ref,
                    )
                final = self._manifest(job_id, attempt, record, snapshot, "SUCCEEDED",
                                       "COMPLETE", cost, artifact)
            except MediaProviderError as exc:
                state = "FAILED" if exc.side_effects == "NONE" else "UNKNOWN"
                final = self._manifest(job_id, attempt, record, snapshot, state, stage,
                                       exc.cost if stage == "PROVIDER" else cost, None, exc.code, str(exc))
            except TimeoutError as exc:
                final = self._manifest(job_id, attempt, record, snapshot, "UNKNOWN", stage,
                                       Cost() if stage == "PROVIDER" else cost, None, "PROVIDER_TIMEOUT", str(exc))
            except MediaOperationError as exc:
                state = "UNKNOWN" if stage == "PROVIDER" else "FAILED"
                final = self._manifest(job_id, attempt, record, snapshot, state, stage,
                                       cost, None, exc.code, str(exc))
            except Exception as exc:
                state = "UNKNOWN" if stage == "PROVIDER" else "FAILED"
                code = "PROVIDER_ERROR" if stage == "PROVIDER" else "INVALID_PROVIDER_OUTPUT"
                final = self._manifest(job_id, attempt, record, snapshot, state, stage,
                                       cost, None, code, f"{type(exc).__name__}: {exc}")
            return self._save(final_ref, final)

    def _save(self, ref: str, manifest: AssetJobManifest) -> AssetJobManifest:
        self.registry.put_immutable(ref, canonical_bytes(manifest.to_dict()) + b"\n")
        return manifest

    @staticmethod
    def _prepare(request: MediaRequest, provider: MediaProvider | None) -> tuple[
        dict[str, Any], MediaInput, dict[str, Any], MediaProvider | None
    ]:
        if not isinstance(request, MediaRequest):
            raise MediaValidationError("Expected MediaRequest")
        if request.mode == "dry-run":
            service = None
            caps = MediaCapabilities("local-dry-run", "DRY_RUN", ("dry-run",), ())
        else:
            service = provider if provider is not None else (
                LocalImportAdapter() if request.mode == "import" else MockMediaProvider())
            caps = service.capabilities
        if not isinstance(caps, MediaCapabilities):
            raise MediaValidationError("Expected typed media capabilities (A02 text providers are not media providers)")
        source, raw, snapshot = None, None, None
        mime = request.mime_type or ("image/png" if request.mode == "mock" else None)
        if request.source_path is not None:
            if request.mode == "mock":
                raise MediaValidationError("Mock does not import a real source")
            if mime not in {"image/png", "video/x-msvideo"}:
                raise MediaValidationError("V1 bytes import supports PNG/AVI; explicitly use import_file() for JPEG/WebP/MP4")
            path = Path(request.source_path)
            try:
                resolved = path.resolve(strict=True)
                with resolved.open("rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise MediaValidationError("Source must be a regular file")
                    raw = stream.read()
                    after = os.fstat(stream.fileno())
                fingerprint = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                if fingerprint(before) != fingerprint(after) or len(raw) != before.st_size:
                    raise MediaValidationError("Source changed during capture")
            except OSError as exc:
                raise MediaValidationError(f"Cannot capture local source: {exc}") from exc
            validate_media(raw, mime)
            snapshot = source_snapshot(resolved, raw)
            snapshot.update(source_kind="LOCAL_MEDIA_FILE", rights_status=request.rights_status)
            source = {"source_ref": str(resolved), "requested_ref": str(path.absolute()),
                      "sha256": snapshot["sha256"], "size_bytes": len(raw)}
        record = {
            "mode": request.mode, "input": source, "mime_type": mime,
            "rights_status": request.rights_status, "rights_ref": request.rights_ref,
            "provider": asdict(caps),
        }
        # Normalize tuples through the same A02 canonical JSON used for the digest.
        record = strict_json(canonical_bytes(record))
        try:
            validate_request(record)
        except (ValueError, TypeError) as exc:
            raise MediaValidationError(str(exc)) from exc
        digest = sha256_bytes(canonical_bytes(record))
        if snapshot is None:
            snapshot = {
                "snapshot_id": "snapshot:" + digest, "source_ref": "request:" + digest,
                "source_kind": "MEDIA_REQUEST", "captured_at": timestamp_utc(), "sha256": digest,
                "verification_status": "SOURCE_OBSERVED", "rights_status": request.rights_status,
            }
        return record, MediaInput(request.mode, raw, mime, source["source_ref"] if source else None), snapshot, service

    @staticmethod
    def _manifest(job_id: str, attempt: int, request: dict[str, Any], snapshot: dict[str, Any],
                  state: str, stage: str, cost: Cost, artifact: dict[str, Any] | None,
                  code: str | None = None, detail: str | None = None) -> AssetJobManifest:
        digest = sha256_bytes(canonical_bytes(request))
        evidence = {
            "evidence_id": "source:" + snapshot["sha256"],
            "source_ref": snapshot["source_ref"], "source_kind": snapshot["source_kind"],
            "observed_at": snapshot["captured_at"], "sha256": snapshot["sha256"],
            "verification_status": snapshot["verification_status"],
            "rights_status": snapshot["rights_status"],
        }
        result = {
            "job_id": job_id, "attempt": attempt,
            "status": state if state in {"SUCCEEDED", "FAILED", "UNKNOWN"} else "UNKNOWN",
            "provider": request["provider"]["name"], "source_snapshot": snapshot,
            "artifact": artifact, "evidence_refs": [evidence["evidence_id"]], "evidence": [evidence],
            "cost_status": cost.status, "cost_amount": cost.amount,
            "error_code": code, "error_detail": detail, "mode": request["mode"],
            "origin": request["provider"]["origin"], "generation_performed": False,
            "publication_status": "NOT_ATTEMPTED",
        }
        trace = [("PREPARE", "READY")]
        if state != "DRY_RUN":
            trace.append(("PROVIDER", "RUNNING"))
            if stage in {"REGISTER", "COMPLETE"}:
                trace.append(("REGISTER", "RUNNING"))
        if state != "RUNNING":
            trace.append((stage, state))
        error = JobError(code, detail, stage, "READY" if state == "DRY_RUN" else "RUNNING") if code else None
        retry = {"SUCCEEDED": "NO_RETRY", "DRY_RUN": "NO_RETRY", "FAILED": "SAFE_LOCAL",
                 "UNKNOWN": "RECONCILE_REQUIRED", "RUNNING": "RECONCILE_REQUIRED"}[state]
        candidate = AssetJobManifest(
            job_id, attempt, digest, stage, state,
            (snapshot["snapshot_id"],) + ((artifact["path"],) if artifact else ()),
            cost, error, retry, tuple(trace), canonical_bytes(request), canonical_bytes(result),
        )
        return AssetJobManifest.from_dict(candidate.to_dict())
