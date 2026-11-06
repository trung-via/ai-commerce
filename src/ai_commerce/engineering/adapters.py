"""Selective CLI invocation adapters, not AIOS Runtime, Kernel, or Publisher.

Command layouts were adapted from pinned AIOS codex_adapter/antigravity_adapter.
AGY flag support must be confirmed against the user's installed CLI version.
"""
from __future__ import annotations

import os
from pathlib import Path

from .profile import ExecutorSelection
from .source_safety import is_link


def agy_directory(path: Path) -> Path:
    """Bind an existing physical directory without accepting reparse aliases."""
    if ".." in Path(path).parts:
        raise ValueError("AGY_PATH_INDIRECTION")
    absolute = Path(os.path.abspath(path))
    if any(is_link(p) for p in (absolute, *absolute.parents)):
        raise ValueError("AGY_PATH_INDIRECTION")
    resolved = absolute.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("AGY_DIRECTORY_REQUIRED")
    return resolved


def agy_data_directory(path: Path) -> Path:
    """Input data must not acquire a checkout's implicit source authority."""
    directory = agy_directory(path)
    for ancestor in (directory, *directory.parents):
        marker = ancestor / ".git"
        if marker.exists() or is_link(marker) or (
                (ancestor / "HEAD").is_file() and (ancestor / "objects").is_dir()
                and (ancestor / "refs").is_dir()):
            raise ValueError("AGY_INPUT_INSIDE_GIT")
    return directory


def agy_input_files(repo: Path, path: Path) -> dict[str, bytes]:
    source, directory = agy_directory(repo), agy_data_directory(path)
    if source == directory or source in directory.parents or directory in source.parents:
        raise ValueError("AGY_INPUT_SOURCE_OVERLAP")
    files = list(directory.iterdir())
    if (not (directory / "instruction.txt").is_file() or
            any(p.name not in ("instruction.txt", "repair_feedback.json") or
                is_link(p) or not p.is_file() for p in files)):
        raise ValueError("AGY_EVIDENCE_DIR_NOT_PREPARED")
    return {p.name: p.read_bytes() for p in files}


def command_for(selection: ExecutorSelection, repo: Path, instruction: str,
                *, codex_bin: str = "codex", antigravity_bin: str = "agy",
                codex_windows_sandbox: str | None = None,
                antigravity_evidence_dir: Path | None = None) -> tuple[list[str], str | None]:
    """Return argv and optional stdin; no shell and no silent alternative provider.

    Only the EF02 Windows Service caller opts into MXC. Manual EF01 remains
    unchanged, and Antigravity never receives Codex-specific sandbox flags.
    """
    if codex_windows_sandbox not in (None, "mxc"):
        raise ValueError("UNSUPPORTED_CODEX_WINDOWS_SANDBOX")
    if codex_windows_sandbox is not None and selection.executor != "codex":
        raise ValueError("CODEX_SANDBOX_WITH_NON_CODEX_EXECUTOR")
    if antigravity_evidence_dir is not None and selection.executor != "antigravity":
        raise ValueError("AGY_EVIDENCE_WITH_NON_AGY_EXECUTOR")
    if selection.executor == "codex":
        mxc_override = ["-c", "windows.sandbox=mxc"] if codex_windows_sandbox == "mxc" else []
        return [
            codex_bin, *mxc_override, "exec", "--cd", str(repo), "-m", selection.model,
            "-c", f'model_reasoning_effort="{selection.effort}"',
            "--sandbox", "workspace-write", "--color", "never", "--json", "-",
        ], instruction
    # Register only the bound source and disposable input, never runner evidence
    # or the primary checkout. The second root carries data, not source authority.
    source = agy_directory(repo)
    evidence_args: list[str] = []
    if antigravity_evidence_dir is not None:
        evidence = agy_data_directory(antigravity_evidence_dir)
        agy_input_files(source, evidence)
        evidence_args = ["--add-dir", str(evidence)]
    return [
        antigravity_bin, "--print", instruction, "--add-dir", str(source),
        *evidence_args, "--model", selection.model, "--effort", selection.effort,
        "--mode", "accept-edits", "--disable-slash-commands", "--output-format", "json",
    ], None
