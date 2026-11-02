"""Bounded, read-only recovery of exact verified EF02 #223 source bytes.

No generic directory arguments, arbitrary paths, Git writes, provider calls or
outbound network. The only export is to the self-hosted job's private artifact
scratch directory. The original source worktree remains unmodified.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import zipfile

OPERATION = "ef02_e04_source_recovery_read"
SOURCE_SHA = "adef706456ef531ec187ee42b2686f29465e6042"
WORKTREE_BASENAME = "ai-commerce-ef02-a14_e04-27kbntwu"
EXPECTED = {
    "contracts/AffiliateCommerceRun.v1.schema.json": "18b8392063f11cfc55ba94a8d6eb2288f2be7fa46325c6b23be29be366e42df9",
    "docs/engineering/A14_E04_IMPLEMENTATION_REPORT.md": "6f3beb700f08d28b2035ebeb8f7b55e39d84a5e41ce6f6d6888411443c36771f",
    "src/ai_commerce/affiliate_commerce/__init__.py": "92b8b9300463b6d6014085733a1c47284defedc58ec9ee0403330c31c35a0c84",
    "src/ai_commerce/affiliate_commerce/__main__.py": "da76430896f7765b661bfd5ad5efe0139dd673ab1c674eec71daf304b626818a",
    "src/ai_commerce/affiliate_commerce/schemas.py": "7f34dafb11e9306ad7429ae77c1db0a55d52310d7106fd6242878556d9b536d4",
    "src/ai_commerce/affiliate_commerce/service.py": "62a2fb879f266ac7e86283bbc526adc8301323df99c9a87a5e6796894bd27bac",
    "tests/test_a14_e04.py": "c18b8a3bed90e738293cb58773e9720dbb9aa14fb6b6a39e79733d8000d638df"
}
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 4_000_000


class RecoveryDenied(Exception):
    """Typed, non-sensitive failure (never include a filesystem path)."""


def snapshot(workdir: Path) -> dict:
    """Export only the 7 original SHA-256-verified E04 files to job-local ZIP.

    If the exact old worktree is gone, return NOT_FOUND without scanning
    unrelated user directories or launching an LLM. A hash mismatch fails
    closed; no partial archive is accepted.
    """
    temp = Path(tempfile.gettempdir())
    root = temp / WORKTREE_BASENAME
    if root.is_symlink():
        raise RecoveryDenied("E04_WORKTREE_SYMLINK_DENIED")
    if not root.is_dir():
        return {"operation": OPERATION, "found": False,
                "file_count": 0, "archive_sha256": None,
                "source_sha_verified": False}
    try:
        p = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryDenied("E04_SOURCE_GIT_UNAVAILABLE") from exc
    if p.returncode or p.stdout.decode("ascii", "replace").strip() != SOURCE_SHA:
        raise RecoveryDenied("E04_SOURCE_SHA_MISMATCH")
    captured: dict[str, bytes] = {}
    total = 0
    for relative, expected in sorted(EXPECTED.items()):
        parts = relative.split("/")
        path = root
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise RecoveryDenied("E04_SOURCE_SYMLINK_DENIED")
        try:
            s = path.stat()
        except OSError as exc:
            raise RecoveryDenied("E04_SOURCE_INCOMPLETE") from exc
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > MAX_FILE_BYTES:
            raise RecoveryDenied("E04_SOURCE_NOT_REGULAR")
        data = path.read_bytes()
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise RecoveryDenied("E04_SOURCE_TOO_LARGE")
        if hashlib.sha256(data).hexdigest() != expected:
            raise RecoveryDenied("E04_SOURCE_HASH_MISMATCH")
        captured[relative] = data

    scratch = workdir / ".local" / "hostops-v2-ef02-223"
    scratch.mkdir(parents=True, exist_ok=True)
    destination = scratch / "verified-source.zip"
    try:
        with zipfile.ZipFile(destination, mode="x", compression=zipfile.ZIP_DEFLATED) as z:
            for path, data in captured.items():
                z.writestr(path, data)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    except (OSError, zipfile.BadZipFile) as exc:
        raise RecoveryDenied("E04_SNAPSHOT_EXPORT_FAILED") from exc
    return {"operation": OPERATION, "found": True,
            "file_count": len(captured), "archive_sha256": digest,
            "source_sha_verified": True}
