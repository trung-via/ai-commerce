"""EF01 profile validation adapted from a pinned AIOS execution_profile boundary.

No implicit model choice, default executor or cross-executor retry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

EXECUTORS = frozenset({"codex", "antigravity"})
MODEL_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$")
EFFORT_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")


class SelectionError(ValueError):
    """Reject a malformed or missing human-selected executor profile."""


@dataclass(frozen=True, slots=True)
class ExecutorSelection:
    executor: str
    model: str
    effort: str

    def __post_init__(self) -> None:
        if self.executor not in EXECUTORS:
            raise SelectionError("Executor must be manually selected: codex or antigravity")
        if not isinstance(self.model, str) or not MODEL_PATTERN.fullmatch(self.model):
            raise SelectionError("Exact model identifier is required (1-128 safe characters)")
        if not isinstance(self.effort, str) or not EFFORT_PATTERN.fullmatch(self.effort):
            raise SelectionError("Exact reasoning effort is required")
