"""Git Byte Consistency v1 — source/candidate checks before model or Git API publication.

EF02 candidates use raw GitHub Git blobs, bypassing Git's normal clean filters.
If those raw bytes differ from a normal `git hash-object --path` round trip,
a subsequent Git-for-Windows worktree may appear dirty without a code edit.
Never rewrite verified bytes or suppress real Git changes to compensate.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Iterable

from .git import git_argv


class GitByteConsistencyError(RuntimeError):
    """Fail closed when Git status or candidate byte behavior is unproven."""


def _git(repo: Path, *args: str, data: bytes | None = None,
         git_executable: str | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        git_argv(repo, *args, executable=git_executable),
        input=data, capture_output=True, check=False, timeout=30,
    )


def checkout_diagnostics(repo: Path, *, refresh_index: bool = False,
                         git_executable: str | None = None) -> dict:
    """Read-only worktree check, optionally refreshing only tracked stat metadata.

    A dirty index/worktree never gets reset, cleaned, assumed-unchanged or hidden.
    Returned paths are diagnostics only (not a bypass list); bound the output.
    """
    def status() -> bytes:
        p = _git(repo, "status", "--porcelain=v1", "--untracked-files=all", "-z",
                 git_executable=git_executable)
        if p.returncode:
            raise GitByteConsistencyError("GIT_STATUS_FAILED")
        return p.stdout

    before = status()
    refresh_exit = None
    after = before
    if before and refresh_index:
        p = _git(repo, "update-index", "-q", "--refresh", git_executable=git_executable)
        refresh_exit = p.returncode
        after = status()
    paths = [s.decode("utf-8", "backslashreplace") for s in after.split(b"\x00") if s]
    return {
        "clean": not after,
        "dirty_entries": paths[:40],
        "dirty_entry_count": len(paths),
        "index_refresh_exit": refresh_exit,
        "metadata_only_reconciled": bool(before and not after),
    }


def assert_candidate_git_filter_consistency(
    repo: Path, prepared: Iterable[tuple[str, bytes | None, str | None]],
    *, git_executable: str | None = None,
) -> None:
    """Prove Git clean filters will not transform any verified candidate blob.

    This check runs after EF01 verification, immediately before the first remote
    blob/ref write. It never mutates a file, index or source commit. Deletions
    are checked by the existing verified manifest.
    """
    for relative, content, _mode in prepared:
        if content is None:
            continue
        if (not relative or relative.startswith("/") or "\\" in relative or
                any(x in ("", ".", "..") for x in relative.split("/"))):
            raise GitByteConsistencyError("CANDIDATE_PATH_INVALID")
        raw = _git(repo, "hash-object", "--stdin", data=content,
                   git_executable=git_executable)
        filtered = _git(repo, "hash-object", "--stdin", "--path=" + relative,
                        data=content, git_executable=git_executable)
        if raw.returncode or filtered.returncode:
            raise GitByteConsistencyError("CANDIDATE_GIT_FILTER_UNAVAILABLE:" + relative)
        if raw.stdout.strip() != filtered.stdout.strip():
            raise GitByteConsistencyError(
                "CANDIDATE_GIT_FILTER_MISMATCH:" + relative +
                ": verified SHA256=" + hashlib.sha256(content).hexdigest() +
                "; Git clean filter would produce different blob bytes. "
                "Fix .gitattributes or normalize BEFORE re-verification; "
                "do not rewrite already-verified output."
            )
