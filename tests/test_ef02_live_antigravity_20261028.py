"""EF02 live parallel conformance test for antigravity executor."""
from __future__ import annotations

import pytest

from ai_commerce.engineering.profile import ExecutorSelection


def test_ef02_live_antigravity_executor_selection() -> None:
    selection = ExecutorSelection("antigravity", "gemini-3.8-flash", "medium")
    assert selection.executor == "antigravity"
    assert selection.model == "gemini-3.8-flash"
    assert selection.effort == "medium"
