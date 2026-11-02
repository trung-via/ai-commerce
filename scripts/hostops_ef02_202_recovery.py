"""Bounded, read-only recovery of exact verified EF02 #202 source bytes.

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

OPERATION = "ef02_l04_source_recovery_read"
SOURCE_SHA = "54822f494b3119fc2edaab357b63333aa4fcec48"
WORKTREE_BASENAME = "ai-commerce-ef02-a14_l04-c1fz134d"
EXPECTED = {
    "contracts/AffiliateAttributionEvidence.v1.schema.json": "50b7b590c288b0734242629d0da9dd26f59767a287fcd87331ebaad6eeb49640",
    "docs/engineering/A14_L04_IMPLEMENTATION_REPORT.md": "50107c60cdb5b1e9f6b3f09d98cdbf7983adc12b01730693c21e48a540cfb0da",
    "src/ai_commerce/affiliate_attribution/README.md": "cdb684db960dece77b5f46de45c8c253de59c0015941beb17f755afa5cb86a71",
    "src/ai_commerce/affiliate_attribution/__init__.py": "d204e8e3d0a3d507fa476b19def37483c47fe1610fc3342c47dabaf5978c16d2",
    "src/ai_commerce/affiliate_attribution/__main__.py": "dfcec1f52d27428fe68bc5ce124eae48159fb273a81912df8b53ac183a0ee6d8",
    "src/ai_commerce/affiliate_attribution/evidence.py": "5be09fe9c3645bb76d1925da0f048e2c006560d2fc78d15370b9e79127021895",
    "src/ai_commerce/affiliate_attribution/ledger.py": "5740d5451097cccbe0ffb017af8edbac5b6c763ab7b9fb2560727f7a136357ef",
    "src/ai_commerce/affiliate_attribution/schemas.py": "ba7b3c113c08f5963d28643f622c0801977805307cd223d3b7baee2c3e0b2ae1",
    "src/ai_commerce/affiliate_attribution/verification/candidate-bytes.json": "6eb50a224d4bbe9dbf031c28942214c79667f9e83b0f76b4fa3843cb0dbe5047",
    "src/ai_commerce/affiliate_attribution/verification/consumer-tests.txt": "f5d984d47e3922cd210134d307d3e771200afa7db233531008f0066f13f5a180",
    "src/ai_commerce/affiliate_attribution/verification/verification.json": "3bed81f609e4cc8b8747bccfcc2b1fea1bb3ecc38f951fa1e1722a90ef656b5c",
    "tests/test_a14_attribution.py": "f6fcbc818161560a0a4857519a4d67f24b37cf8f6ff784acbe6e204de8302020",
}
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 4_000_000


class RecoveryDenied(Exception):
    """Typed, non-sensitive failure (never include a filesystem path)."""


def snapshot(workdir: Path) -> dict:
    """Export only the 12 original SHA-256-verified files to job-local ZIP.

    If the exact old worktree is gone, return NOT_FOUND without scanning
    unrelated user directories or launching an LLM. A hash mismatch fails
    closed; no partial archive is accepted.
    """
    temp = Path(tempfile.gettempdir())
    root = temp / WORKTREE_BASENAME
    if root.is_symlink():
        raise RecoveryDenied("L04_WORKTREE_SYMLINK_DENIED")
    if not root.is_dir():
        return {"operation": OPERATION, "found": False,
                "file_count": 0, "archive_sha256": None,
                "source_sha_verified": False}
    try:
        p = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryDenied("L04_SOURCE_GIT_UNAVAILABLE") from exc
    if p.returncode or p.stdout.decode("ascii", "replace").strip() != SOURCE_SHA:
        raise RecoveryDenied("L04_SOURCE_SHA_MISMATCH")
    captured: dict[str, bytes] = {}
    total = 0
    for relative, expected in sorted(EXPECTED.items()):
        parts = relative.split("/")
        path = root
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise RecoveryDenied("L04_SOURCE_SYMLINK_DENIED")
        try:
            s = path.stat()
        except OSError as exc:
            raise RecoveryDenied("L04_SOURCE_INCOMPLETE") from exc
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > MAX_FILE_BYTES:
            raise RecoveryDenied("L04_SOURCE_NOT_REGULAR")
        data = path.read_bytes()
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise RecoveryDenied("L04_SOURCE_TOO_LARGE")
        if hashlib.sha256(data).hexdigest() != expected:
            raise RecoveryDenied("L04_SOURCE_HASH_MISMATCH")
        captured[relative] = data

    scratch = workdir / ".local" / "hostops-v2-ef02-202"
    scratch.mkdir(parents=True, exist_ok=True)
    destination = scratch / "verified-source.zip"
    try:
        with zipfile.ZipFile(destination, mode="x", compression=zipfile.ZIP_DEFLATED) as z:
            for path, data in captured.items():
                z.writestr(path, data)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    except (OSError, zipfile.BadZipFile) as exc:
        raise RecoveryDenied("L04_SNAPSHOT_EXPORT_FAILED") from exc
    return {"operation": OPERATION, "found": True,
            "file_count": len(captured), "archive_sha256": digest,
            "source_sha_verified": True}
