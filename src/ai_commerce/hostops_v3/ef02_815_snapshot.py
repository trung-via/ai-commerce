"""One-shot bounded #815 source recovery snapshot, without executor or repo writes.

The only publishable bytes are the two pre-authorized Production BQ-01a
source/test paths from the surviving old isolated worktree. They are put in
one GitHub Actions artifact for subsequent independent review; no PR or new
attempt is claimed by this operator. No untrusted GitHub Issue input reaches
a file path or command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

from . import ef02_815_forensic_readonly as observed

SCHEMA = "ai-commerce.hostops.ef02-815-snapshot.v1"
OPERATION = "EF02_815_SOURCE_SNAPSHOT_READONLY"
ARTIFACT_NAME = "ef02-815-candidate-snapshot.zip"
MAX_BYTES = 4 * 1024 * 1024
NO_REPLAY = False


class SnapshotDenied(ValueError):
    """No private source path or file bytes in this exception."""


def _status_paths(raw: bytes) -> set[str]:
    try:
        entries = raw.decode("utf-8", "strict").split("\0")
    except UnicodeError as exc:
        raise SnapshotDenied("SOURCE_STATUS_UNVERIFIED") from exc
    found: set[str] = set()
    for entry in filter(None, entries):
        if (len(entry) < 4 or entry[2] != " " or
                entry[:2].strip() in ("R", "C") or
                entry[3:] not in observed.SOURCE_PATHS):
            raise SnapshotDenied("OUTSIDE_SCOPE_OR_UNVERIFIED")
        if entry[3:] in found:
            raise SnapshotDenied("SOURCE_STATUS_UNVERIFIED")
        found.add(entry[3:])
    return found


def _read_fixed(path: Path) -> bytes:
    if not observed._safe(path) or path.stat().st_size > MAX_BYTES:
        raise SnapshotDenied("SOURCE_UNSAFE")
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            content = handle.read(MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
        if (identity(before) != identity(after) or len(content) > MAX_BYTES or
                not observed._safe(path)):
            raise SnapshotDenied("SOURCE_UNSAFE")
        return content
    except OSError as exc:
        raise SnapshotDenied("SOURCE_READ_UNKNOWN") from exc


def snapshot(output: Path) -> dict:
    worktree = observed._source_worktree()
    if worktree is None or not observed._safe(worktree, directory=True):
        raise SnapshotDenied("WORKTREE_UNVERIFIED")
    head = observed._read_git(worktree, "head")
    if head is None or head.strip() != observed.SOURCE_SHA.encode():
        raise SnapshotDenied("SOURCE_BASELINE_UNVERIFIED")
    status = observed._read_git(worktree, "status")
    if status is None:
        raise SnapshotDenied("SOURCE_STATUS_UNVERIFIED")
    dirty = _status_paths(status)
    collected: list[tuple[str, bytes]] = []
    fingerprints: dict[str, dict[str, str]] = {}
    for relative in observed.SOURCE_PATHS:
        file = worktree.joinpath(*relative.split("/"))
        content = _read_fixed(file)
        original = observed._read_git(worktree, "blob", relative)
        if original is None or len(original) > MAX_BYTES:
            raise SnapshotDenied("SOURCE_BASELINE_UNVERIFIED")
        fingerprints[relative] = {
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "baseline_sha256": hashlib.sha256(original).hexdigest(),
        }
        if content != original:
            collected.append((relative, content))
    changed = {item[0] for item in collected}
    if not changed or changed != dirty:
        raise SnapshotDenied("SOURCE_DIFF_OR_STATUS_UNVERIFIED")
    manifest = {
        "schema": SCHEMA, "operation": OPERATION, "target_issue": 815,
        "source_sha": observed.SOURCE_SHA, "attempt_id": observed.ATTEMPT,
        "source_files": sorted(changed), "source_changed_count": len(changed),
        "source_fingerprints": {p: fingerprints[p] for p in sorted(changed)},
        "source_only": True, "candidate_verified": False,
        "provider_outcome_proven": False, "replay_authorized": False,
    }
    # Construct exactly one archive in GitHub runner temporary storage. The
    # isolated EF02 worktree and its evidence are never mutated or deleted.
    try:
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED,
                             allowZip64=False) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True,
                                                         separators=(",", ":")))
            for relative, content in collected:
                archive.writestr("source/" + relative, content)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SnapshotDenied("ARCHIVE_WRITE_FAILED") from exc
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    temp = os.environ.get("RUNNER_TEMP")
    expected = Path(temp) / ARTIFACT_NAME if temp else None
    if expected is None or args.output != expected or args.output.exists():
        parser.error("HOSTOPS_815_SNAPSHOT_OUTPUT_PATH_DENIED")
    snapshot(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
