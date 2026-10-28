"""Immutable artifact identity and local-source provenance (A02 only)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_snapshot(path: Path, raw: bytes) -> dict[str, Any]:
    digest = sha256_bytes(raw)
    return {
        "snapshot_id": "snapshot:" + digest,
        "source_ref": str(path.resolve()),
        "source_kind": "LOCAL_JSON_FILE",
        "captured_at": timestamp_utc(),
        "sha256": digest,
        "verification_status": "SOURCE_OBSERVED",
        "rights_status": "UNKNOWN",
    }


def put_immutable(path: Path, content: bytes) -> bool:
    """Write only new content; return False if matching pre-existing bytes."""
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"ARTIFACT_CONFLICT: refusing to overwrite {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True
