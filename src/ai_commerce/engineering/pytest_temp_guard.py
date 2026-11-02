"""EF01-only pytest scratch isolation for provider subprocesses.

Pytest loads this plugin through trusted PYTEST_PLUGINS environment injection.
It changes temporary placement only; it never modifies source-scope checks,
Git protection, test selection, or the independent EF01 verifier.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    root_text = os.environ.get("AI_COMMERCE_EF01_PYTEST_BASETEMP_ROOT")
    if root_text is None:
        return
    worktree_text = os.environ.get("AI_COMMERCE_EF01_WORKTREE")
    if not worktree_text:
        raise pytest.UsageError("EF01_TEST_TEMP_WORKTREE_UNBOUND")
    root = Path(root_text).resolve()
    worktree = Path(worktree_text).resolve()
    if root == worktree or worktree in root.parents or not root.is_dir():
        raise pytest.UsageError("EF01_TEST_TEMP_INSIDE_SOURCE_OR_UNAVAILABLE")
    # A fresh directory per pytest invocation avoids collisions when the
    # executor starts multiple test processes within its single EF01 action.
    # Also overrides an explicit provider-side --basetemp=.local/... option.
    config.option.basetemp = tempfile.mkdtemp(prefix="pytest-", dir=root)
