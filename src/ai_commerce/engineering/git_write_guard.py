"""Executor-scoped Git ref-write guard for EF01/EF02 (not a security sandbox).

Git hooks intercept ordinary commit/ref updates even when the executor resolves
Git via an absolute path. The worker/publisher never inherits this environment.
A hostile process can override hook configuration or edit refs directly, so
EF01's independent post-execution HEAD and worktree checks remain mandatory.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

GIT_GUARD_INSTRUCTION = (
    "AI COMMERCE GIT WRITE GUARD: You are an EF01/EF02 code executor, NOT the "
    "candidate publisher. Edit ordinary allowed worktree files only. Do NOT "
    "run git commit, reset, checkout, switch, branch, merge, rebase, push, "
    "update-ref, symbolic-ref, stash, worktree, or change Git HEAD/refs/config. "
    "Read-only git status, diff, log and rev-parse are allowed. "
    "Do not bypass hooks, environment or the guard. The EF02 worker alone "
    "publishes the verified candidate. If Git writes are blocked, continue "
    "editing files and let EF01 verify; do not retry with alternate Git options."
)

# Git for Windows invokes shell hooks through its bundled Git Bash environment.
# reference-transaction protects normal Git ref updates including --no-verify
# commits; pre-commit gives an earlier, explicit diagnostic.
_HOOK_NAMES = ("reference-transaction", "pre-commit", "pre-merge-commit", "pre-rebase")
_HOOK_BODY = (
    "#!/bin/sh\n"
    "printf 'AI_COMMERCE_GIT_WRITE_BLOCKED\\n' >&2\n"
    "exit 1\n"
)


def git_write_guard_env(
    evidence_dir: Path, *, parent_env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Install per-invocation hooks and return *child-only* Git configuration.

    Never install hooks in the repository .git directory. The publisher's Git
    operations run in the parent environment and are not restricted by this
    function. Refuse to launch a provider if the guard cannot be prepared.
    """
    env = dict(os.environ if parent_env is None else parent_env)
    raw_count = env.get("GIT_CONFIG_COUNT", "0")
    if not raw_count.isascii() or not raw_count.isdecimal():
        raise RuntimeError("GIT_WRITE_GUARD_ENV_INVALID")
    count = int(raw_count)
    if count > 64:
        raise RuntimeError("GIT_WRITE_GUARD_ENV_INVALID")

    guard_dir = (Path(evidence_dir) / "git-write-guard-hooks").resolve()
    guard_dir.mkdir(parents=True, exist_ok=False)
    for name in _HOOK_NAMES:
        file = guard_dir / name
        file.write_bytes(_HOOK_BODY.encode("ascii"))
        file.chmod(0o755)

    env[f"GIT_CONFIG_KEY_{count}"] = "core.hooksPath"
    env[f"GIT_CONFIG_VALUE_{count}"] = str(guard_dir)
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env
