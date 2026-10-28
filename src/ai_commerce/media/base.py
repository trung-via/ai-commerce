"""Typed, provider-neutral media boundary. No built-in network/model provider."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

RunMode = Literal["import", "mock", "dry-run"]
Origin = Literal["LOCAL_IMPORT", "MOCK", "DRY_RUN", "MODEL_GENERATED"]


@dataclass(frozen=True)
class Cost:
    status: str = "UNKNOWN"
    amount: float | None = None

    def __post_init__(self) -> None:
        if self.status not in {"UNKNOWN", "NOT_APPLICABLE", "ESTIMATED", "CHARGED"}:
            raise ValueError("INVALID_COST_STATUS")
        if self.status in {"UNKNOWN", "NOT_APPLICABLE"}:
            if self.amount is not None:
                raise ValueError("UNKNOWN_OR_INAPPLICABLE_COST_MUST_BE_NULL")
        elif (isinstance(self.amount, bool) or not isinstance(self.amount, (int, float))
              or not math.isfinite(self.amount) or self.amount < 0):
            raise ValueError("KNOWN_COST_REQUIRES_NONNEGATIVE_FINITE_AMOUNT")


@dataclass(frozen=True)
class MediaRequest:
    mode: RunMode = "import"
    source_path: Path | None = None
    mime_type: str | None = None
    rights_status: str = "UNKNOWN"
    rights_ref: str | None = None


@dataclass(frozen=True)
class MediaInput:
    """One captured input, never a request to reopen a mutable source path."""

    mode: RunMode
    content: bytes | None
    mime_type: str | None
    source_ref: str | None


@dataclass(frozen=True)
class MediaOutput:
    content: bytes
    mime_type: str
    cost: Cost = field(default_factory=Cost)


@dataclass(frozen=True)
class MediaCapabilities:
    name: str
    origin: Origin
    modes: tuple[RunMode, ...]
    mime_types: tuple[str, ...]
    network_required: bool = False
    external_spend_possible: bool = False


class MediaProviderError(RuntimeError):
    """Only explicit NONE permits a manual retry; the default is ambiguous."""

    def __init__(self, code: str, detail: str, *, side_effects: str = "UNKNOWN",
                 cost: Cost | None = None):
        if side_effects not in {"NONE", "UNKNOWN"}:
            raise ValueError("INVALID_SIDE_EFFECT_STATUS")
        self.code = code
        self.side_effects = side_effects
        self.cost = cost if cost is not None else Cost()
        super().__init__(detail)


class MediaValidationError(MediaProviderError):
    def __init__(self, detail: str):
        super().__init__("INVALID_MEDIA", detail, side_effects="NONE",
                         cost=Cost("NOT_APPLICABLE"))


class MediaProviderTimeout(MediaProviderError, TimeoutError):
    def __init__(self, detail: str = "Provider outcome requires reconciliation"):
        super().__init__("PROVIDER_TIMEOUT", detail)


class MediaProvider(Protocol):
    @property
    def capabilities(self) -> MediaCapabilities: ...

    def produce(self, request: MediaInput) -> MediaOutput: ...
