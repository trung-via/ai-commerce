"""EF01/EF02 Git byte-portability checks (no checkout mutation).

GitHub's Git API stores the exact verified candidate bytes, bypassing the
local Git clean filter. Reject a candidate whose clean-filtered Git blob
would differ from the verified blob before publishing any remote object.
A clean detached checkout is checked before spawning EF01 or a provider.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from .git import git_argv, resolve_git_executable


class GitByteConsistencyError(RuntimeError):
    """Git source/clean-filter evidence did not satisfy candidate portability."""


def _git(repo: Path, *args: str, raw: bytes | None = None,
         git_executable: str | None = None) -> subprocess.CompletedProcess[bytes]:
    executable = git_executable
    if executable is None and os.environ.get("AI_COMMERCE_GIT_EXECUTABLE"):
        executable = resolve_git_executable(os.environ["AI_COMMERCE_GIT_EXECUTABLE"])
    try:
        return subprocess.run(
            git_argv(repo, *args, executable=executable),
            input=raw, capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitByteConsistencyError("GIT_BYTE_PROBE_UNAVAILABLE") from exc


def assert_candidate_blob_portable(
    repo: Path, relative: str, content: bytes, *,
    git_executable: str | None = None,
) -> None:
    """Reject raw blobs that Git would rewrite on an ordinary clean/filter pass.

    A GitHub API candidate does not go through 'git add'. Compare the raw
    Git-object hash with 'git hash-object --path' using the same content and
    the *candidate worktree's* attributes. Do not normalize after testing:
    that would silently publish different bytes from those EF01 verified.
    """
    parts = Path(relative.replace("\\", "/")).parts
    if (not relative or Path(relative).is_absolute() or
            any(p in ("", ".", "..", ".git") for p in parts)):
        raise GitByteConsistencyError("CANDIDATE_INVALID_BLOB_PATH")
    raw = _git(repo, "hash-object", "--no-filters", "--stdin",
               raw=content, git_executable=git_executable)
    filtered = _git(repo, "hash-object", "--stdin", "--path=" + relative,
                    raw=content, git_executable=git_executable)
    if raw.returncode or filtered.returncode:
        raise GitByteConsistencyError("CANDIDATE_GIT_FILTER_PROBE_FAILED:" + relative)
    if raw.stdout.strip() != filtered.stdout.strip():
        raise GitByteConsistencyError("CANDIDATE_GIT_FILTER_MISMATCH:" + relative)


def inspect_fresh_checkout(repo: Path, expected_sha: str, *,
                           git_executable: str | None = None) -> dict:
    """Read-only post-worktree-add check. Never reset, clean or suppress paths."""
    head = _git(repo, "rev-parse", "HEAD", git_executable=git_executable)
    status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                  git_executable=git_executable)
    if head.returncode or status.returncode:
        raise GitByteConsistencyError("GIT_CHECKOUT_PROBE_FAILED")
    actual = head.stdout.decode("ascii", "replace").strip()
    entries = [item.decode("utf-8", "replace") for item in status.stdout.split(b"\x00") if item]
    # The complete status digest is retained; bound inline path disclosure.
    return {
        "expected_sha": expected_sha,
        "actual_sha": actual,
        "clean": actual == expected_sha and not entries,
        "head_matches": actual == expected_sha,
        "git_status_sha256": hashlib.sha256(status.stdout).hexdigest(),
        "porcelain_entries": entries[:80],
        "porcelain_entry_count": len(entries),
        "error_code": (
            "GIT_CHECKOUT_HEAD_MISMATCH" if actual != expected_sha
            else "GIT_WORKTREE_DIRTY_AFTER_CHECKOUT" if entries
            else None
        ),
    }
