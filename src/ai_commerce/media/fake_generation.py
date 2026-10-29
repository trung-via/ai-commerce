"""In-process fixture backend for async lifecycle tests. No model/API client."""

from __future__ import annotations

from pathlib import Path

from .base import Cost, MediaValidationError
from .files import hash_file, validate_media_file
from .generation import (
    GenerationEvent, GenerationFile, GenerationJobRequest, GenerationProviderCapabilities,
)


class FakeAsyncMediaProvider:
    def __init__(self, fixture_path: Path, mime_type: str = "image/png"):
        self.fixture_path, self.mime_type = Path(fixture_path), mime_type
        self.submit_calls = self.observe_calls = self.fetch_calls = self.reconcile_calls = 0
        self._jobs: dict[str, GenerationEvent] = {}
        self._tokens: dict[str, str] = {}
        self._pending: dict[str, list[GenerationEvent]] = {}

    @property
    def capabilities(self) -> GenerationProviderCapabilities:
        return GenerationProviderCapabilities("fake-async-media", "fixture-v2", synthetic_fixture=True)

    def submit(self, request: GenerationJobRequest, *, client_token: str) -> GenerationEvent:
        self.submit_calls += 1
        if request.mime_type != self.mime_type:
            raise MediaValidationError("Fake fixture MIME does not match generation intent")
        if client_token in self._tokens:
            event = self._jobs[self._tokens[client_token]]
            if event.request_digest != request.digest:
                raise ValueError("Fake client token collision")
            return event
        external = "fake:" + client_token
        event = GenerationEvent(external + ":0", external, request.digest, 0, "SUBMITTED", "CONFIRMED")
        self._jobs[external], self._tokens[client_token], self._pending[external] = event, external, []
        return event

    def emit(self, external_request_id: str, state: str, *, sequence: int | None = None,
             cost: Cost | None = None, event_id: str | None = None) -> GenerationEvent:
        previous = self._jobs[external_request_id]
        sequence = previous.sequence + 1 if sequence is None else sequence
        digest = None
        if state == "SUCCEEDED":
            validate_media_file(self.fixture_path, self.mime_type)
            digest = hash_file(self.fixture_path)[0]
        event = GenerationEvent(
            event_id or external_request_id + ":" + str(sequence), external_request_id,
            previous.request_digest, sequence, state, "CONFIRMED", cost or Cost(),
            digest, "SYNTHETIC_FIXTURE" if digest else "UNKNOWN",
        )
        self._pending[external_request_id].append(event)
        if sequence > previous.sequence:
            self._jobs[external_request_id] = event
        return event

    def deliver(self, *events: GenerationEvent) -> None:
        """Deliver duplicates/stale/reordered observations without changing truth."""
        for event in events:
            self._pending[event.external_request_id].append(event)

    def observe(self, external_request_id: str) -> tuple[GenerationEvent, ...]:
        self.observe_calls += 1
        events = tuple(self._pending[external_request_id])
        self._pending[external_request_id].clear()
        return events

    def fetch(self, external_request_id: str) -> GenerationFile:
        self.fetch_calls += 1
        event = self._jobs[external_request_id]
        if event.state != "SUCCEEDED" or event.output_sha256 is None:
            raise MediaValidationError("Fake output is not ready")
        return GenerationFile(self.fixture_path, self.mime_type, event.output_sha256, external_request_id)

    def reconcile(self, request: GenerationJobRequest, *, client_token: str,
                  external_request_id: str | None) -> tuple[GenerationEvent, ...]:
        self.reconcile_calls += 1
        external = self._tokens.get(client_token)
        if external is None:
            return ()  # Unknown is not proof of no side effects.
        if external_request_id is not None and external != external_request_id:
            raise ValueError("Reconciliation request identity mismatch")
        event = self._jobs[external]
        if event.request_digest != request.digest:
            raise ValueError("Reconciliation intent mismatch")
        return (event,)
