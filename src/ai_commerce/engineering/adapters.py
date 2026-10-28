"""Selective CLI invocation adapters, not AIOS Runtime, Kernel, or Publisher.

Command layouts were adapted from pinned AIOS codex_adapter/antigravity_adapter.
AGY flag support must be confirmed against the user's installed CLI version.
"""
from __future__ import annotations

from pathlib import Path

from .profile import ExecutorSelection


def command_for(selection: ExecutorSelection, repo: Path, instruction: str,
                *, codex_bin: str = "codex", antigravity_bin: str = "agy",
                codex_windows_sandbox: str | None = None) -> tuple[list[str], str | None]:
    """Return argv and optional stdin; no shell and no silent alternative provider.

    Only the EF02 Windows Service caller opts into MXC. Manual EF01 remains
    unchanged, and Antigravity never receives Codex-specific sandbox flags.
    """
    if codex_windows_sandbox not in (None, "mxc"):
        raise ValueError("UNSUPPORTED_CODEX_WINDOWS_SANDBOX")
    if codex_windows_sandbox is not None and selection.executor != "codex":
        raise ValueError("CODEX_SANDBOX_WITH_NON_CODEX_EXECUTOR")
    if selection.executor == "codex":
        mxc_override = ["-c", "windows.sandbox=mxc"] if codex_windows_sandbox == "mxc" else []
        return [
            codex_bin, *mxc_override, "exec", "--cd", str(repo), "-m", selection.model,
            "-c", f'model_reasoning_effort="{selection.effort}"',
            "--sandbox", "workspace-write", "--color", "never", "--json", "-",
        ], instruction
    return [
        antigravity_bin, "--print", instruction, "--add-dir", str(repo),
        "--model", selection.model, "--effort", selection.effort,
        "--mode", "accept-edits", "--disable-slash-commands", "--output-format", "json",
    ], None
