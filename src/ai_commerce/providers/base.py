"""Small provider interface for product creative adapters (not code execution)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(RuntimeError):
    def __init__(self, error_code: str, detail: str):
        self.error_code = error_code
        super().__init__(detail)


@dataclass(frozen=True)
class ProviderCapabilities:
    name: str
    output_kinds: tuple[str, ...]
    network_required: bool
    external_spend_possible: bool


class ContentProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def generate(self, brief: dict[str, object]) -> bytes: ...


class MockContentProvider:
    """An explicit TEST-ONLY provider, no claim to create real media."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="local-mock", output_kinds=("text",),
            network_required=False, external_spend_possible=False
        )

    def generate(self, brief: dict[str, object]) -> bytes:
        objective = brief["objective"]
        prompt = brief.get("prompt", "")
        if not isinstance(objective, str) or not isinstance(prompt, str):
            raise ProviderError("INVALID_INPUT", "expected string objective/prompt")
        return (f"AI Commerce MOCK OUTPUT (not generated media)\n"
                f"Objective: {objective}\nPrompt: {prompt}\n").encode("utf-8")
