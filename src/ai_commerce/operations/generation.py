"""Durable V2 async generation coordination, enabled only for local fixtures.

Immutable intent, pre-submit marker, and a checksummed observation journal survive
runner recreation. Reconciliation never calls submit. No automatic retry exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes, timestamp_utc
from ai_commerce.media.base import Cost, MediaProviderError, MediaValidationError
from ai_commerce.media.files import hash_file
from ai_commerce.media.generation import (
    AsyncGenerationProvider, GenerationEvent, GenerationFile, GenerationJobRequest,
    GenerationProviderCapabilities, TERMINAL,
)
from ai_commerce.media.provenance import MediaProvenance
from .errors import JobCollisionError, ManifestValidationError, RetryUnsafeError
from .manifest import JOB_ID, strict_json
from .registry import ArtifactRegistry

MAX_RECORD_BYTES = 1024 * 1024
MAX_ENTRIES = 10000


@dataclass(frozen=True)
class GenerationJobStatus:
    job_id: str
    request_digest: str
    state: str = "UNKNOWN"
    external_request_id: str | None = None
    side_effects: str = "UNKNOWN"
    cost: Cost = field(default_factory=Cost)
    retry_safety: str = "RECONCILE_REQUIRED"
    event_ids: tuple[str, ...] = ()
    diagnostic: str | None = None
    completion: GenerationEvent | None = None
    receipt_version: int = field(default=2, init=False)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def to_dict(self) -> dict:
        return asdict(self)


class GenerationJobRunner:
    def __init__(self, registry: ArtifactRegistry, provider: AsyncGenerationProvider):
        caps = provider.capabilities
        if (not isinstance(caps, GenerationProviderCapabilities)
                or caps.synthetic_fixture is not True or caps.network_required is not False
                or caps.external_spend_possible is not False):
            raise MediaValidationError("V2 execution is enabled only for an offline synthetic fixture backend")
        self.registry, self.provider, self.caps = registry, provider, caps

    @staticmethod
    def _base(job_id: str) -> str:
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise ValueError("INVALID_JOB_ID")
        return "generation-jobs/" + job_id

    @staticmethod
    def _seal(payload: dict) -> dict:
        return {**payload, "record_sha256": sha256_bytes(canonical_bytes(payload))}

    def _json(self, relative: str) -> dict:
        path = self.registry.safe_path(relative)
        if not self.registry.exists(relative):
            return strict_json(self.registry.read(relative))
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise ManifestValidationError("V2 record size limit exceeded")
        return strict_json(self.registry.read(relative))

    def _put(self, relative: str, payload: dict) -> None:
        raw = canonical_bytes(self._seal(payload))
        if len(raw) + 1 > MAX_RECORD_BYTES:
            raise ManifestValidationError("V2 record size limit exceeded")
        self.registry.put_immutable(relative, raw + b"\n")

    def _unseal(self, record: dict) -> dict:
        payload = {k: v for k, v in record.items() if k != "record_sha256"}
        if record.get("record_sha256") != sha256_bytes(canonical_bytes(payload)):
            raise ManifestValidationError("V2 record checksum mismatch")
        return payload

    def _request(self, job_id: str) -> tuple[GenerationJobRequest, str]:
        record = self._unseal(self._json(self._base(job_id) + "/request.json"))
        try:
            if set(record) != {"job_id", "request", "request_digest", "client_token"} or record["job_id"] != job_id:
                raise ValueError("Invalid V2 intent binding")
            request = GenerationJobRequest.from_dict(record["request"])
            token = sha256_bytes(canonical_bytes({"job_id": job_id, "request_digest": request.digest}))
            if record["request_digest"] != request.digest or record["client_token"] != token:
                raise ValueError("V2 request/token identity mismatch")
            return request, token
        except (TypeError, ValueError, KeyError) as exc:
            raise ManifestValidationError(str(exc)) from exc

    def _entries(self, job_id: str) -> list[dict]:
        base = self._base(job_id) + "/observations"
        directory = self.registry.safe_path(base)
        if not self.registry.exists(base):
            return []
        refs = []
        for entry in directory.iterdir():
            if len(refs) >= MAX_ENTRIES:
                raise ManifestValidationError("V2 journal entry limit exceeded")
            self.registry.safe_path(base + "/" + entry.name)
            refs.append(entry.name)
        records = []
        for ordinal, name in enumerate(sorted(refs)):
            if name != f"{ordinal:08}.json":
                raise ManifestValidationError("Invalid/gapped V2 journal")
            record = self._unseal(self._json(base + "/" + name))
            if (set(record) != {"journal_version", "job_id", "request_digest", "ordinal",
                               "operation", "events", "error"}
                    or type(record["journal_version"]) is not int or record["journal_version"] != 2
                    or record["job_id"] != job_id or type(record["ordinal"]) is not int
                    or record["ordinal"] != ordinal
                    or record["operation"] not in {"submit", "observe", "reconcile"}
                    or not isinstance(record["events"], list) or len(record["events"]) > 1024
                    or (record["error"] is not None and not isinstance(record["error"], dict))):
                raise ManifestValidationError("Invalid V2 journal entry")
            records.append(record)
        return records

    def _fold(self, job_id: str, request: GenerationJobRequest, entries: list[dict]) -> GenerationJobStatus:
        seen: dict[str, dict] = {}
        current, external, conflict = None, None, False
        cost, cost_sequence, effects = Cost(), -1, "UNKNOWN"
        uncertain, diagnostic = True, "Submission outcome has not been reconciled"
        rejected_before_acceptance = False
        for entry in entries:
            if entry["request_digest"] != request.digest:
                raise ManifestValidationError("Journal intent mismatch")
            parsed, fresh = [], []
            for record in entry["events"]:
                try:
                    event = GenerationEvent.from_dict(record)
                except (ValueError, TypeError, KeyError) as exc:
                    raise ManifestValidationError(f"Invalid provider observation: {exc}") from exc
                if event.request_digest != request.digest:
                    raise ManifestValidationError("Provider event belongs to a different request")
                if external is not None and event.external_request_id != external:
                    raise ManifestValidationError("Provider external request ID changed")
                external = event.external_request_id
                if event.event_id in seen and seen[event.event_id] != record:
                    raise ManifestValidationError("Duplicate event ID has conflicting content")
                if event.event_id not in seen:
                    fresh.append(event)
                seen[event.event_id] = record
                parsed.append(event)
            for event in sorted(fresh, key=lambda x: (x.sequence, x.event_id)):
                if event.side_effects == "CONFIRMED":
                    effects = "CONFIRMED"
                elif effects != "CONFIRMED" and event.side_effects == "NONE":
                    effects = "NONE"
                if (event.cost.status != "UNKNOWN" and event.sequence >= cost_sequence
                        and not (cost.status == "CHARGED" and event.cost.status != "CHARGED")):
                    cost, cost_sequence = event.cost, event.sequence
                if (current is not None and current.state in TERMINAL and event.state in TERMINAL
                        and current.state != event.state):
                    conflict = True
                if current is None:
                    current = event
                elif event.sequence == current.sequence:
                    if (event.state, event.output_sha256, event.output_kind) != (
                            current.state, current.output_sha256, current.output_kind):
                        conflict = True
                elif event.sequence > current.sequence:
                    if current.state in TERMINAL and event.state not in TERMINAL:
                        continue  # Delayed RUNNING cannot regress a terminal receipt.
                    if current.state in TERMINAL and event.state != current.state:
                        conflict = True
                    current = event
            if entry["error"] is not None:
                error = entry["error"]
                try:
                    if set(error) != {"code", "detail", "side_effects", "cost"}:
                        raise ValueError("Invalid error receipt fields")
                    if (not isinstance(error["code"], str) or not isinstance(error["detail"], str)
                            or error["side_effects"] not in {"NONE", "UNKNOWN"}):
                        raise ValueError("Invalid error receipt certainty")
                    error_cost = Cost(**error["cost"])
                except (ValueError, TypeError, KeyError) as exc:
                    raise ManifestValidationError(f"Invalid V2 error receipt: {exc}") from exc
                if error_cost.status != "UNKNOWN" and cost.status != "CHARGED":
                    cost = error_cost
                if error["side_effects"] == "NONE" and external is None:
                    effects, rejected_before_acceptance = "NONE", True
                    uncertain = False
                else:
                    uncertain = True
                diagnostic = error["code"] + ": " + error["detail"]
            elif entry["operation"] == "submit" and parsed:
                uncertain, diagnostic = False, None
            elif entry["operation"] == "reconcile" and parsed:
                authoritative = max(parsed, key=lambda x: x.sequence)
                if current is not None and current.state in TERMINAL and authoritative.state not in TERMINAL:
                    uncertain, diagnostic = True, "Reconciliation cannot regress a terminal provider receipt"
                elif current is None or authoritative.sequence >= current.sequence:
                    current, conflict, uncertain, diagnostic = authoritative, False, False, None
        state = current.state if current is not None else ("FAILED" if rejected_before_acceptance else "UNKNOWN")
        if uncertain or conflict:
            state = "UNKNOWN"
        if conflict:
            diagnostic = "Conflicting terminal/sequence evidence requires explicit reconciliation"
        retry = "NO_RETRY" if state in TERMINAL else "RECONCILE_REQUIRED"
        return GenerationJobStatus(job_id, request.digest, state, external, effects, cost,
                                   retry, tuple(sorted(seen)), diagnostic,
                                   current if state == "SUCCEEDED" else None)

    def load(self, job_id: str) -> GenerationJobStatus:
        request, token = self._request(job_id)
        marker = self._unseal(self._json(self._base(job_id) + "/submitted.started.json"))
        if marker != {"job_id": job_id, "request_digest": request.digest, "client_token": token}:
            raise ManifestValidationError("Invalid pre-submit marker")
        return self._fold(job_id, request, self._entries(job_id))

    def _check_provider(self, request: GenerationJobRequest) -> None:
        if (self.provider.capabilities != self.caps or request.provider != self.caps.name
                or request.model != self.caps.model):
            raise MediaValidationError("Provider/model identity does not match the bound offline backend")

    def _interact(self, job_id: str, request: GenerationJobRequest, operation: str,
                  call: Callable[[], tuple[GenerationEvent, ...]]) -> GenerationJobStatus:
        entries = self._entries(job_id)
        if len(entries) >= MAX_ENTRIES:
            raise ManifestValidationError("V2 journal entry limit exceeded")
        error, events = None, ()
        try:
            events = call()
            if not isinstance(events, tuple) or len(events) > 1024 or not all(
                    isinstance(e, GenerationEvent) for e in events):
                raise ValueError("Expected a bounded tuple of typed provider observations")
        except Exception as exc:
            # Ambiguity is persisted, including malformed/unknown errors. No replay.
            error = {"code": getattr(exc, "code", type(exc).__name__), "detail": str(exc),
                     "side_effects": exc.side_effects if isinstance(exc, MediaProviderError) else "UNKNOWN",
                     "cost": asdict(exc.cost if isinstance(exc, MediaProviderError) else Cost())}
            events = ()
        entry = {
            "journal_version": 2, "job_id": job_id, "request_digest": request.digest,
            "ordinal": len(entries), "operation": operation,
            "events": [event.to_dict() for event in events], "error": error,
        }
        result = self._fold(job_id, request, [*entries, entry])
        self._put(self._base(job_id) + f"/observations/{len(entries):08}.json", entry)
        return result

    def submit(self, request: GenerationJobRequest, *, job_id: str) -> GenerationJobStatus:
        if not isinstance(request, GenerationJobRequest):
            raise MediaValidationError("Expected a V2 GenerationJobRequest")
        self._check_provider(request)
        base = self._base(job_id)
        token = sha256_bytes(canonical_bytes({"job_id": job_id, "request_digest": request.digest}))
        with self.registry.worker():
            if self.registry.exists(base + "/request.json"):
                bound, _ = self._request(job_id)
                if bound != request:
                    raise JobCollisionError("Generation job already has a different immutable intent")
            else:
                self._put(base + "/request.json", {"job_id": job_id, "request": request.to_dict(),
                          "request_digest": request.digest, "client_token": token})
            marker = base + "/submitted.started.json"
            if self.registry.exists(marker):
                return self.load(job_id)  # Never resubmit after a crash/timeout.
            self._put(marker, {"job_id": job_id, "request_digest": request.digest, "client_token": token})
            return self._interact(job_id, request, "submit",
                                  lambda: (self.provider.submit(request, client_token=token),))

    def observe(self, job_id: str) -> GenerationJobStatus:
        with self.registry.worker():
            request, _ = self._request(job_id)
            self._check_provider(request)
            previous = self.load(job_id)
            if previous.external_request_id is None:
                raise RetryUnsafeError("No external request ID; reconcile by client token before observation")
            return self._interact(job_id, request, "observe",
                                  lambda: self.provider.observe(previous.external_request_id))

    def reconcile(self, job_id: str) -> GenerationJobStatus:
        with self.registry.worker():
            request, token = self._request(job_id)
            self._check_provider(request)
            previous = self.load(job_id)
            return self._interact(job_id, request, "reconcile", lambda: self.provider.reconcile(
                request, client_token=token, external_request_id=previous.external_request_id))

    def fetch(self, job_id: str) -> dict:
        """Explicit download/custody by known ID; this never generates or submits."""
        with self.registry.worker():
            request, _ = self._request(job_id)
            self._check_provider(request)
            status = self.load(job_id)
            if status.state != "SUCCEEDED" or status.completion is None:
                raise RetryUnsafeError("Fetch requires a reconciled successful provider receipt")
            ref = self._base(job_id) + "/output.receipt.json"
            if self.registry.exists(ref):
                return self._load_output(job_id, request, status, ref)
            output = self.provider.fetch(status.external_request_id)
            if (not isinstance(output, GenerationFile) or output.external_request_id != status.external_request_id
                    or output.mime_type != request.mime_type
                    or output.sha256 != status.completion.output_sha256):
                raise MediaValidationError("Fetched output is not bound to the observed completion")
            if hash_file(output.path)[0] != output.sha256:
                raise MediaValidationError("Fetched output differs from provider's observed digest")
            # This runner permits only a synthetic backend. No rights or generation
            # claim can be inherited from an intent, provider name or file label.
            snapshot = {
                "snapshot_id": "snapshot:" + request.digest, "source_ref": "generation-request:" + request.digest,
                "source_kind": "GENERATION_INTENT", "captured_at": timestamp_utc(),
                "sha256": request.digest, "verification_status": "SOURCE_OBSERVED", "rights_status": "UNKNOWN",
            }
            artifact = self.registry.register_file(
                output.path, output.mime_type, provider=self.caps.name, snapshot=snapshot,
                mode="mock", origin="MOCK",
            )
            provenance = MediaProvenance.generation(
                request, status.completion, observed_sha256=artifact["sha256"], synthetic_fixture=True)
            if provenance.classification != "SYNTHETIC_FIXTURE":
                raise MediaValidationError("Fake backend cannot establish model or render generation")
            self._put(ref, {"receipt_version": 2, "job_id": job_id, "request_digest": request.digest,
                           "external_request_id": status.external_request_id, "artifact": artifact,
                           "provenance": provenance.to_dict()})
            return self._load_output(job_id, request, status, ref)

    def _load_output(self, job_id, request, status, ref) -> dict:
        output = self._unseal(self._json(ref))
        if (set(output) != {"receipt_version", "job_id", "request_digest", "external_request_id",
                            "artifact", "provenance"} or type(output["receipt_version"]) is not int
                or output["receipt_version"] != 2 or output["job_id"] != job_id
                or output["request_digest"] != request.digest
                or output["external_request_id"] != status.external_request_id):
            raise ManifestValidationError("V2 output receipt identity mismatch")
        artifact = output["artifact"]
        self.registry.verify_file(artifact)
        expected = MediaProvenance.generation(
            request, status.completion, observed_sha256=artifact["sha256"], synthetic_fixture=True)
        if (output["provenance"] != expected.to_dict() or expected.classification != "SYNTHETIC_FIXTURE"
                or artifact.get("provider") != request.provider or artifact.get("mode") != "mock"
                or artifact.get("origin") != "MOCK" or artifact.get("generation_performed") is not False
                or artifact["rights_status"] != "UNKNOWN" or artifact.get("rights_ref") is not None
                or artifact.get("source_generation") != "SYNTHETIC_FIXTURE"
                or artifact["mime_type"] != request.mime_type
                or artifact["source_snapshot_ids"] != ["snapshot:" + request.digest]
                or artifact.get("source_ref") != "generation-request:" + request.digest):
            raise ManifestValidationError("V2 output provenance/rights/source binding mismatch")
        return output
