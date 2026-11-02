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


def stabilize_candidate_newlines_before_verify(
    repo: Path, paths: Iterable[str], *, allowed_paths: Iterable[str],
    git_executable: str | None = None,
) -> list[dict[str, str]]:
    """Normalize *new, owned* CRLF text before tests and manifest hashing.

    Only an exact Git-clean == LF-converted-byte comparison authorizes a
    rewrite. Tracked files, out-of-scope paths and other Git filters are
    rejected instead. The publisher retains its independent post-verify
    byte check; no verified bytes are ever changed.
    """
    from .source_safety import checked_path, ordinary_scope_drift

    allowed = tuple(p.rstrip("/") for p in allowed_paths)
    changes: list[dict[str, str]] = []
    for relative in paths:
        try:
            path = checked_path(repo, relative)
        except ValueError as exc:
            raise GitByteConsistencyError("PREVERIFY_UNSAFE_PATH:" + relative) from exc
        if not path.exists():
            continue  # Deletion; validated by the verified candidate manifest.
        if not path.is_file():
            raise GitByteConsistencyError("PREVERIFY_NON_FILE:" + relative)

        content = path.read_bytes()
        raw = _git(repo, "hash-object", "--stdin", data=content,
                   git_executable=git_executable)
        filtered = _git(repo, "hash-object", "--stdin", "--path=" + relative,
                        data=content, git_executable=git_executable)
        if raw.returncode or filtered.returncode:
            raise GitByteConsistencyError("PREVERIFY_GIT_FILTER_UNAVAILABLE:" + relative)
        if raw.stdout.strip() == filtered.stdout.strip():
            continue

        # A CRLF-only transform is the one case that can be made deterministic
        # without guessing at custom filters, encodings or file semantics.
        normalized = content.replace(b"\r\n", b"\n")
        converted = _git(repo, "hash-object", "--stdin", data=normalized,
                         git_executable=git_executable)
        converted_filtered = _git(repo, "hash-object", "--stdin",
                                  "--path=" + relative, data=normalized,
                                  git_executable=git_executable)
        if (b"\r\n" not in content or b"\x00" in content or
                not ordinary_scope_drift([relative]) or
                not converted.stdout or converted.returncode or
                converted_filtered.returncode or
                filtered.stdout.strip() != converted.stdout.strip() or
                converted_filtered.stdout.strip() != converted.stdout.strip()):
            raise GitByteConsistencyError("PREVERIFY_GIT_FILTER_UNSUPPORTED:" + relative)

        if (not allowed or not any(relative == p or relative.startswith(p + "/")
                                   for p in allowed)):
            raise GitByteConsistencyError("PREVERIFY_OUT_OF_SCOPE_FILTER:" + relative)

        tracked = _git(repo, "ls-files", "--error-unmatch", "--", relative,
                       git_executable=git_executable)
        if tracked.returncode == 0:
            # Existing tracked evidence may depend on original CRLF bytes;
            # do not silently modify its semantics.
            raise GitByteConsistencyError("PREVERIFY_TRACKED_FILTER_MISMATCH:" + relative)
        if tracked.returncode != 1:
            raise GitByteConsistencyError("PREVERIFY_GIT_INDEX_UNAVAILABLE:" + relative)

        before = hashlib.sha256(content).hexdigest()
        path.write_bytes(normalized)
        if path.read_bytes() != normalized:
            raise GitByteConsistencyError("PREVERIFY_NORMALIZED_BYTES_CHANGED:" + relative)
        changes.append({
            "path": relative, "transformation": "CRLF_TO_LF_PRE_VERIFY",
            "before_sha256": before,
            "after_sha256": hashlib.sha256(normalized).hexdigest(),
        })
    return changes
