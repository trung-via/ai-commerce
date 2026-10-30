"""Executor-scoped Git ref-write guard for EF01/EF02 (not a security sandbox).

Git hooks intercept ordinary commit/ref updates even when the executor resolves
Git via an absolute path. The worker/publisher never inherits this environment.
A hostile process can override hook configuration or edit refs directly, so
EF01's independent post-execution HEAD and worktree checks remain mandatory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
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
_HOOK_NAMES = ("reference-transaction", "pre-commit", "pre-merge-commit", "pre-rebase", "pre-push")
_HOOK_BODY = (
    "#!/bin/sh\n"
    "printf 'AI_COMMERCE_GIT_WRITE_BLOCKED\\n' >&2\n"
    "exit 1\n"
)
_PROTECTED_PATHS_ENV = "AI_COMMERCE_GIT_GUARD_PATHS"


def _git_identity(repo: Path, *, git_executable: str = "git") -> list[Path] | None:
    """Observe physical worktree/admin/common directories without changing Git."""
    paths = []
    try:
        for option in ("--show-toplevel", "--absolute-git-dir", "--git-common-dir"):
            result = subprocess.run(
                [git_executable, "-C", str(repo), "rev-parse", "--path-format=absolute", option],
                capture_output=True, text=True, encoding="utf-8", errors="strict",
                check=False, timeout=10,
            )
            value = result.stdout.strip()
            if result.returncode or not value or not Path(value).is_absolute():
                return None
            path = Path(value).resolve(strict=True)
            if not path.is_dir():
                return None
            paths.append(path)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return paths


def _independent_repository(protected: list[Path] | None, identity: list[Path] | None) -> bool:
    """Only a completely observed, disjoint repository can escape source hooks."""
    if not protected or not identity or len(identity) != 3:
        return False
    return not any(a == b or a in b.parents or b in a.parents
                   for a in protected for b in identity)


def _guard_paths(repo: Path | None, env: Mapping[str, str], git_executable: str) -> list[Path] | None:
    if repo is None:
        return None
    identity = _git_identity(repo, git_executable=git_executable)
    if identity is None:
        return None
    # Nested EF01 invocations must retain their ancestor's protected identities.
    # An inherited, unidentifiable hooks policy remains block-all.
    inherited = env.get(_PROTECTED_PATHS_ENV)
    if inherited is None:
        if any(value.lower() == "core.hookspath" for key, value in env.items()
               if key.startswith("GIT_CONFIG_KEY_")):
            return None
        return identity
    try:
        values = json.loads(inherited)
        if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and Path(value).is_absolute() for value in values):
            return None
        ancestors = [Path(value).resolve(strict=True) for value in values]
        if not all(path.is_dir() for path in ancestors):
            return None
        return list(dict.fromkeys(ancestors + identity))
    except (OSError, ValueError, TypeError):
        return None


def _check_hook(manifest: Path) -> int:
    """Run from Git's hook cwd; missing/ambiguous identity always denies writes."""
    try:
        policy = json.loads(manifest.read_text(encoding="utf-8"))
        values = policy["protected_paths"]
        if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and Path(value).is_absolute() for value in values):
            raise ValueError("GIT_WRITE_GUARD_IDENTITY_UNPROVEN")
        protected = [Path(value).resolve(strict=True) for value in values]
        if all(path.is_dir() for path in protected) and _independent_repository(
                protected, _git_identity(Path.cwd(), git_executable=policy["git_executable"])):
            return 0
    except (OSError, ValueError, KeyError, TypeError):
        pass
    print("AI_COMMERCE_GIT_WRITE_BLOCKED", file=sys.stderr)
    return 1


def git_write_guard_env(
    evidence_dir: Path, *, parent_env: Mapping[str, str] | None = None,
    repo: Path | None = None, git_executable: str = "git",
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
    protected = _guard_paths(repo, env, git_executable)
    env[_PROTECTED_PATHS_ENV] = json.dumps(
        [str(path) for path in protected] if protected else None)
    body = _HOOK_BODY
    if protected:
        policy = guard_dir / "policy.py"
        policy.write_bytes(Path(__file__).read_bytes())
        manifest = guard_dir / "identity.json"
        manifest.write_text(json.dumps({"protected_paths": [str(path) for path in protected],
                                        "git_executable": git_executable}), encoding="utf-8")
        body = "#!/bin/sh\nexec " + " ".join(shlex.quote(path.as_posix()) for path in (
            Path(sys.executable), policy, manifest)) + "\n"
    for name in _HOOK_NAMES:
        file = guard_dir / name
        file.write_bytes(body.encode("utf-8"))
        file.chmod(0o755)

    env[f"GIT_CONFIG_KEY_{count}"] = "core.hooksPath"
    env[f"GIT_CONFIG_VALUE_{count}"] = str(guard_dir)
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


if __name__ == "__main__":
    raise SystemExit(_check_hook(Path(sys.argv[1])) if len(sys.argv) == 2 else 1)
