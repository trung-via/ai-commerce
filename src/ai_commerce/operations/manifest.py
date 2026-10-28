"""Versioned A04 manifests wrap, and validate, the unchanged A02 JobResult."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.media.base import Cost
from ai_commerce.media.local import MIME_KINDS
from .errors import ManifestValidationError

JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
DIGEST = re.compile(r"[0-9a-f]{64}")


def strict_json(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if not isinstance(record, dict):
            raise ValueError("Expected JSON object")
        canonical_bytes(record)  # Also rejects non-finite numbers.
        return record
    except (ValueError, UnicodeError) as exc:
        raise ManifestValidationError(f"Invalid manifest JSON: {exc}") from exc


def validate_request(record: dict[str, Any]) -> None:
    if set(record) != {"mode", "input", "mime_type", "rights_status", "rights_ref", "provider"}:
        raise ValueError("Invalid request fields")
    mode, provider, source = record["mode"], record["provider"], record["input"]
    if mode not in {"import", "mock", "dry-run"}:
        raise ValueError("Invalid mode")
    if set(provider) != {"name", "origin", "modes", "mime_types", "network_required", "external_spend_possible"}:
        raise ValueError("Invalid provider identity")
    if (not isinstance(provider["name"], str) or not provider["name"]
            or provider["network_required"] is not False or provider["external_spend_possible"] is not False
            or provider["origin"] != {"import": "LOCAL_IMPORT", "mock": "MOCK", "dry-run": "DRY_RUN"}[mode]
            or not isinstance(provider["modes"], list) or mode not in provider["modes"]
            or not isinstance(provider["mime_types"], list)):
        raise ValueError("Provider is outside the offline A04 policy")
    if record["rights_status"] not in {"UNKNOWN", "GRANTED", "RESTRICTED", "NOT_APPLICABLE"}:
        raise ValueError("Invalid rights status")
    rights_ref = record["rights_ref"]
    if rights_ref is not None and (not isinstance(rights_ref, str) or not rights_ref.strip()):
        raise ValueError("Invalid rights evidence reference")
    if record["rights_status"] == "GRANTED" and rights_ref is None:
        raise ValueError("GRANTED requires an explicit rights evidence reference")
    if record["mime_type"] is not None and record["mime_type"] not in MIME_KINDS:
        raise ValueError("Unsupported MIME")
    if mode != "dry-run" and (record["mime_type"] not in MIME_KINDS
                             or record["mime_type"] not in provider["mime_types"]):
        raise ValueError("Unsupported MIME/provider capability")
    if (mode == "import" and source is None) or (mode == "mock" and source is not None):
        raise ValueError("Invalid source for mode")
    if source is not None:
        if (set(source) != {"source_ref", "requested_ref", "sha256", "size_bytes"}
                or not all(isinstance(source[k], str) and source[k] for k in ("source_ref", "requested_ref"))
                or not DIGEST.fullmatch(source["sha256"])
                or type(source["size_bytes"]) is not int or source["size_bytes"] <= 0):
            raise ValueError("Invalid source identity")


@dataclass(frozen=True)
class JobError:
    code: str
    detail: str
    stage: str
    last_state: str


@dataclass(frozen=True)
class AssetJobManifest:
    job_id: str
    attempt: int
    request_digest: str
    stage: str
    state: str
    refs: tuple[str, ...]
    cost: Cost
    error: JobError | None
    retry_safety: str
    trace: tuple[tuple[str, str], ...]
    _request_json: bytes = field(repr=False)
    _result_json: bytes = field(repr=False)

    @property
    def request(self) -> dict[str, Any]:
        return strict_json(self._request_json)

    @property
    def result(self) -> dict[str, Any]:
        """A fresh, A02-schema-valid JobResult for A05 or other local consumers."""
        return strict_json(self._result_json)

    def to_dict(self) -> dict[str, Any]:
        record = {
            "manifest_version": 1, "job_id": self.job_id, "attempt": self.attempt,
            "request_digest": self.request_digest, "request": self.request,
            "stage": self.stage, "state": self.state, "refs": list(self.refs),
            "cost": asdict(self.cost), "error": asdict(self.error) if self.error else None,
            "retry_safety": self.retry_safety,
            "trace": [{"stage": stage, "state": state} for stage, state in self.trace],
            "result": self.result,
        }
        return {**record, "record_sha256": sha256_bytes(canonical_bytes(record))}

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> AssetJobManifest:
        try:
            return cls._validate(record)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ManifestValidationError(f"Invalid A04 manifest: {exc}") from exc

    @classmethod
    def _validate(cls, record: dict[str, Any]) -> AssetJobManifest:
        keys = {"manifest_version", "job_id", "attempt", "request_digest", "request",
                "stage", "state", "refs", "cost", "error", "retry_safety", "trace",
                "result", "record_sha256"}
        if not isinstance(record, dict) or set(record) != keys or type(record["manifest_version"]) is not int or record["manifest_version"] != 1:
            raise ValueError("Invalid manifest fields/version")
        payload = {k: v for k, v in record.items() if k != "record_sha256"}
        if record["record_sha256"] != sha256_bytes(canonical_bytes(payload)):
            raise ValueError("Manifest checksum mismatch")
        job, attempt, request = record["job_id"], record["attempt"], record["request"]
        if not JOB_ID.fullmatch(job) or type(attempt) is not int or attempt < 1:
            raise ValueError("Invalid job/attempt")
        validate_request(request)
        digest = sha256_bytes(canonical_bytes(request))
        if digest != record["request_digest"]:
            raise ValueError("Immutable request digest mismatch")
        if set(record["cost"]) != {"status", "amount"}:
            raise ValueError("Invalid cost fields")
        cost = Cost(**record["cost"])
        state, stage, error = record["state"], record["stage"], record["error"]
        paths = {
            "RUNNING": [("PREPARE", "READY"), ("PROVIDER", "RUNNING")],
            "DRY_RUN": [("PREPARE", "READY"), ("COMPLETE", "DRY_RUN")],
            "SUCCEEDED": [("PREPARE", "READY"), ("PROVIDER", "RUNNING"),
                          ("REGISTER", "RUNNING"), ("COMPLETE", "SUCCEEDED")],
        }
        if state in {"FAILED", "UNKNOWN"} and stage in {"PROVIDER", "REGISTER"}:
            paths[state] = [("PREPARE", "READY"), ("PROVIDER", "RUNNING")]
            if stage == "REGISTER":
                paths[state].append(("REGISTER", "RUNNING"))
            paths[state].append((stage, state))
        trace = [{"stage": a, "state": b} for a, b in paths.get(state, [])]
        if not trace or trace != record["trace"] or stage != trace[-1]["stage"]:
            raise ValueError("Invalid stage/state trace")
        retry = {"SUCCEEDED": "NO_RETRY", "DRY_RUN": "NO_RETRY",
                 "FAILED": "SAFE_LOCAL", "UNKNOWN": "RECONCILE_REQUIRED",
                 "RUNNING": "RECONCILE_REQUIRED"}[state]
        if record["retry_safety"] != retry:
            raise ValueError("Invalid retry safety")
        result = validate_record("JobResult", record["result"])
        expected_result_keys = {
            "job_id", "attempt", "status", "provider", "source_snapshot", "artifact",
            "evidence_refs", "evidence", "cost_status", "cost_amount", "error_code",
            "error_detail", "mode", "origin", "generation_performed", "publication_status",
        }
        if set(result) != expected_result_keys:
            raise ValueError("Invalid JobResult fields")
        status = state if state in {"SUCCEEDED", "FAILED", "UNKNOWN"} else "UNKNOWN"
        provider, source = request["provider"], request["input"]
        if (result["job_id"] != job or type(result["attempt"]) is not int or result["attempt"] != attempt
                or result["status"] != status or result["provider"] != provider["name"]
                or result["mode"] != request["mode"] or result["origin"] != provider["origin"]
                or result["generation_performed"] is not False
                or result["publication_status"] != "NOT_ATTEMPTED"
                or result["cost_status"] != cost.status or result["cost_amount"] != cost.amount):
            raise ValueError("JobResult identity/state/cost mismatch")
        snapshot = validate_record("SourceSnapshot", result["source_snapshot"])
        source_hash = source["sha256"] if source else digest
        source_ref = source["source_ref"] if source else "request:" + digest
        if (snapshot["sha256"] != source_hash or snapshot["snapshot_id"] != "snapshot:" + source_hash
                or snapshot["source_ref"] != source_ref
                or snapshot["source_kind"] != ("LOCAL_MEDIA_FILE" if source else "MEDIA_REQUEST")
                or snapshot["verification_status"] != "SOURCE_OBSERVED"
                or snapshot["rights_status"] != request["rights_status"]):
            raise ValueError("True source provenance mismatch")
        evidence = result["evidence"]
        if not isinstance(evidence, list) or len(evidence) != 1:
            raise ValueError("Missing source evidence")
        ev = validate_record("EvidenceRef", evidence[0])
        expected_evidence = {
            "evidence_id": "source:" + source_hash, "source_ref": source_ref,
            "source_kind": snapshot["source_kind"], "sha256": source_hash,
            "observed_at": snapshot["captured_at"], "verification_status": "SOURCE_OBSERVED",
            "rights_status": request["rights_status"],
        }
        if ev != expected_evidence or result["evidence_refs"] != [ev["evidence_id"]]:
            raise ValueError("Source evidence mismatch")
        artifact = result["artifact"]
        if (artifact is not None) != (state == "SUCCEEDED"):
            raise ValueError("Only a successful media run may have an artifact")
        if artifact is not None:
            if (artifact["provider"] != provider["name"]
                    or artifact["source_snapshot_ids"] != [snapshot["snapshot_id"]]
                    or artifact["source_ref"] != source_ref
                    or artifact["rights_status"] != request["rights_status"]
                    or artifact["rights_ref"] != request["rights_ref"]
                    or artifact["mode"] != request["mode"] or artifact["origin"] != provider["origin"]
                    or artifact["mime_type"] != request["mime_type"]
                    or artifact["generation_performed"] is not False
                    or artifact["source_generation"] != ("UNKNOWN" if source else "SYNTHETIC_FIXTURE")
                    or artifact["path"] != "objects/" + artifact["sha256"]
                    or artifact["artifact_id"] != "artifact:" + artifact["sha256"]
                    or artifact["kind"] != MIME_KINDS[artifact["mime_type"]]):
                raise ValueError("Artifact provenance/identity mismatch")
            if source and (artifact["sha256"] != source_hash or artifact["size_bytes"] != source["size_bytes"]):
                raise ValueError("Local import must preserve exact source bytes")
        refs = [snapshot["snapshot_id"]] + ([artifact["path"]] if artifact else [])
        if record["refs"] != refs:
            raise ValueError("Manifest references mismatch")
        if state == "SUCCEEDED":
            if error is not None or result["error_code"] is not None or result["error_detail"] is not None:
                raise ValueError("Successful run cannot contain an error")
            parsed_error = None
        else:
            if set(error) != {"code", "detail", "stage", "last_state"}:
                raise ValueError("Invalid diagnostic fields")
            parsed_error = JobError(**error)
            if (not parsed_error.code or not isinstance(parsed_error.detail, str)
                    or parsed_error.stage != stage or parsed_error.last_state != ("READY" if state == "DRY_RUN" else "RUNNING")
                    or result["error_code"] != parsed_error.code or result["error_detail"] != parsed_error.detail):
                raise ValueError("Diagnostic mismatch")
            if state == "DRY_RUN" and (parsed_error.code != "DRY_RUN_NO_EXECUTION" or cost != Cost("NOT_APPLICABLE")):
                raise ValueError("Dry-run must remain unexecuted and cost-inapplicable")
        if (state == "DRY_RUN") != (request["mode"] == "dry-run"):
            raise ValueError("Dry-run cannot masquerade as media execution")
        return cls(job, attempt, digest, stage, state, tuple(refs), cost, parsed_error,
                   retry, tuple(paths[state]), canonical_bytes(request), canonical_bytes(result))
