"""V2 generation intent and asynchronous provider observations, separate from V1.

The async contract describes jobs that finish later; methods make one bounded
local interaction and never poll, retry, or start a background worker.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from .base import Cost
from .files import SUPPORTED_MIME_KINDS

DIGEST = re.compile(r"[0-9a-f]{64}")
STATES = {"SUBMITTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "UNKNOWN"}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError(f"Invalid {name}")


def _digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError(f"Invalid {name}")


@dataclass(frozen=True)
class GenerationJobRequest:
    provider: str
    model: str
    source_ref: str
    source_sha256: str
    mime_type: str = "image/png"
    prompt_sha256: str | None = None
    prompt_version: str | None = None
    rights_status: str = "UNKNOWN"
    rights_ref: str | None = None
    job_version: int = field(default=2, init=False)

    def __post_init__(self) -> None:
        for name in ("provider", "model", "source_ref"):
            _text(getattr(self, name), name)
        _digest(self.source_sha256, "source_sha256")
        if self.mime_type not in SUPPORTED_MIME_KINDS:
            raise ValueError("Unsupported output MIME")
        if self.prompt_sha256 is not None:
            _digest(self.prompt_sha256, "prompt_sha256")
        if self.prompt_version is not None:
            _text(self.prompt_version, "prompt_version")
            if self.prompt_sha256 is None:
                raise ValueError("Prompt version requires a prompt digest")
        if self.rights_status not in {"UNKNOWN", "GRANTED", "RESTRICTED", "NOT_APPLICABLE"}:
            raise ValueError("Invalid rights status")
        if self.rights_ref is not None:
            _text(self.rights_ref, "rights_ref")
        if self.rights_status == "GRANTED" and self.rights_ref is None:
            raise ValueError("GRANTED requires rights evidence; a generation label grants no rights")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, record: dict) -> GenerationJobRequest:
        if (not isinstance(record, dict) or set(record) != set(cls.__dataclass_fields__)
                or type(record["job_version"]) is not int or record["job_version"] != 2):
            raise ValueError("Invalid generation request fields/version")
        return cls(**{k: v for k, v in record.items() if k != "job_version"})


@dataclass(frozen=True)
class GenerationEvent:
    event_id: str
    external_request_id: str
    request_digest: str
    sequence: int
    state: str
    side_effects: str = "UNKNOWN"
    cost: Cost = field(default_factory=Cost)
    output_sha256: str | None = None
    output_kind: str = "UNKNOWN"

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _text(self.external_request_id, "external_request_id")
        _digest(self.request_digest, "request_digest")
        if type(self.sequence) is not int or self.sequence < 0 or self.state not in STATES:
            raise ValueError("Invalid provider state/sequence")
        if self.side_effects not in {"NONE", "UNKNOWN", "CONFIRMED"} or not isinstance(self.cost, Cost):
            raise ValueError("Invalid side-effect/cost certainty")
        if self.output_kind not in {"UNKNOWN", "SYNTHETIC_FIXTURE", "MODEL_GENERATED", "DERIVED_RENDER"}:
            raise ValueError("Invalid output provenance")
        if self.output_sha256 is not None:
            _digest(self.output_sha256, "output_sha256")
        if self.output_kind != "UNKNOWN" and (self.output_sha256 is None or self.state != "SUCCEEDED"
                                               or self.side_effects != "CONFIRMED"):
            raise ValueError("Proven output requires observed success, effects and output hash")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict) -> GenerationEvent:
        if not isinstance(record, dict) or set(record) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid provider event fields")
        if not isinstance(record["cost"], dict) or set(record["cost"]) != {"status", "amount"}:
            raise ValueError("Invalid event cost")
        return cls(**{**record, "cost": Cost(**record["cost"])})


@dataclass(frozen=True)
class GenerationProviderCapabilities:
    name: str
    model: str
    synthetic_fixture: bool = False
    network_required: bool = False
    external_spend_possible: bool = False


@dataclass(frozen=True)
class GenerationFile:
    path: Path
    mime_type: str
    sha256: str
    external_request_id: str


class AsyncGenerationProvider(Protocol):
    @property
    def capabilities(self) -> GenerationProviderCapabilities: ...

    def submit(self, request: GenerationJobRequest, *, client_token: str) -> GenerationEvent: ...

    def observe(self, external_request_id: str) -> tuple[GenerationEvent, ...]: ...

    def fetch(self, external_request_id: str) -> GenerationFile: ...

    def reconcile(self, request: GenerationJobRequest, *, client_token: str,
                  external_request_id: str | None) -> tuple[GenerationEvent, ...]: ...
