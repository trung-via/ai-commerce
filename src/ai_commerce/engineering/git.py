"""Direct Git argv shared by EF01 observations and scoped EF02 administration."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


def resolve_git_executable(value: str | None = None) -> str:
    configured = value if value is not None else os.environ.get("AI_COMMERCE_GIT_EXECUTABLE")
    if configured is None:
        return "git"  # Preserve the existing PATH/credential behavior.
    if not configured or "\x00" in configured or '"' in configured or "\n" in configured:
        raise ValueError("GIT_EXECUTABLE_INVALID: use an executable path, without shell/arguments")
    found = shutil.which(configured)
    if found is None:
        raise ValueError("GIT_EXECUTABLE_UNAVAILABLE: " + configured)
    path = Path(found).resolve()
    if (path.suffix.lower() in (".cmd", ".bat", ".ps1", ".sh", ".py") or
            path.stem.lower() in ("cmd", "powershell", "pwsh", "bash", "sh", "dash", "zsh", "wscript", "cscript") or
            (os.name == "nt" and path.suffix.lower() != ".exe")):
        raise ValueError("GIT_EXECUTABLE_WRAPPER_FORBIDDEN: " + configured)
    if not path.is_file():
        raise ValueError("GIT_EXECUTABLE_NOT_FILE: " + configured)
    return str(path)


def git_argv(repo: Path, *args: str, executable: str | None = None) -> list[str]:
    return [resolve_git_executable(executable), "-C", str(repo), *args]
