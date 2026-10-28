"""Selective CLI invocation adapters, not AIOS Runtime, Kernel, or Publisher.

Command layouts were adapted from pinned AIOS codex_adapter/antigravity_adapter.
AGY flag support must be confirmed against the user's installed CLI version.
"""
from __future__ import annotations

from pathlib import Path

from .profile import ExecutorSelection


def command_for(selection: ExecutorSelection, repo: Path, instruction: str,
                *, codex_bin: str = "codex", antigravity_bin: str = "agy") -> tuple[list[str], str | None]:
    """Return argv and optional stdin; no shell and no silent alternative provider."""
    if selection.executor == "codex":
        return [
            codex_bin, "exec", "--cd", str(repo), "-m", selection.model,
            "-c", f'model_reasoning_effort="{selection.effort}"',
            "--sandbox", "workspace-write", "--color", "never", "--json", "-",
        ], instruction
    return [
        antigravity_bin, "--print", instruction, "--add-dir", str(repo),
        "--model", selection.model, "--effort", selection.effort,
        "--mode", "accept-edits", "--disable-slash-commands", "--output-format", "json",
    ], None
