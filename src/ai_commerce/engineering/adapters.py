"""Selective CLI invocation adapters, not AIOS Runtime, Kernel, or Publisher.

Command layouts were adapted from pinned AIOS codex_adapter/antigravity_adapter.
AGY flag support must be confirmed against the user's installed CLI version.
"""
from __future__ import annotations

from pathlib import Path

from .profile import ExecutorSelection


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
    # The EF02 evidence directory is outside the temporary worktree. In headless
    # AGY, reading such an unregistered directory can require an interactive
    # read_file approval (impossible in --print mode). Add ONLY this attempt\x27s
    # evidence directory as a workspace root; never allow every file or command.
    evidence_args: list[str] = []
    if antigravity_evidence_dir is not None:
        evidence = Path(antigravity_evidence_dir).resolve(strict=True)
        if not evidence.is_dir() or not (evidence / "instruction.txt").is_file():
            raise ValueError("AGY_EVIDENCE_DIR_NOT_PREPARED")
        evidence_args = ["--add-dir", str(evidence)]
    return [
        antigravity_bin, "--print", instruction, "--add-dir", str(repo),
        *evidence_args, "--model", selection.model, "--effort", selection.effort,
        "--mode", "accept-edits", "--disable-slash-commands", "--output-format", "json",
    ], None
