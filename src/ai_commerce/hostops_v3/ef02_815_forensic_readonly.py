"""Pinned, read-only forensic observation of EF02 Issue #815's failed attempt.

This is not a recovery/replay interface. GitHub Issue content cannot select a
path, PID, command, source, provider or operation. Unknown remains UNKNOWN.
No import-time effects; no writes to observed targets, shell, network,
Git mutations or model calls. Only an allowlisted runner-temp receipt is written.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

SCHEMA = "ai-commerce.hostops.ef02-815-forensic.v1"
OPERATION = "EF02_815_FORENSIC_READONLY"
TARGET_ISSUE = 815
SOURCE_SHA = "9ecb1b72bc43c8eb38e6d5ca70ff99dab9c4154b"
BATCH = "production-q1q3-agy38-fallback-9ecb1b72-20261112"
ACTION = "PRODUCTION-Q1Q3-BQ01A-AGY38-FALLBACK-01"
ATTEMPT = "production_q1q3_bq01a_agy38_9ecb1b72_20261112_fresh01"
PID = 3780
CHECKOUT = Path(r"C:\TOOL\ai-commerce-runner\_work\ai-commerce\ai-commerce")
EVIDENCE = CHECKOUT / ".local" / "ef02" / BATCH / (ACTION + "-" + ATTEMPT)
START_EARLIEST = dt.datetime(2026, 11, 11, 18, 10, tzinfo=dt.timezone.utc)
START_LATEST = dt.datetime(2026, 11, 11, 18, 14, tzinfo=dt.timezone.utc)
MAX_BYTES = 4 * 1024 * 1024
MAX_GLOB = 4
HOST_IS_WINDOWS = os.name == "nt"
RELS = (
    ("ef01/invocations/000/provider_boundary.json", "BEFORE_EXECUTOR_SUBPROCESS"),
    ("ef01/invocations/000/provider_return_observed.json", "EXECUTOR_SUBPROCESS_RETURNED"),
    ("ef01/engineering_result.json", None),
)
_PROCESS_CMD = (
    "$ErrorActionPreference='Stop';"
    "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=3780';"
    "if($null -eq $p){'{\"present\":false}'}else{"
    "@{present=$true;name=[string]$p.Name;command=[string]$p.CommandLine;"
    "created=([datetime]$p.CreationDate).ToUniversalTime().ToString('o')}"
    "|ConvertTo-Json -Compress -Depth 2}"
)


def _safe(path: Path, *, directory: bool = False) -> bool:
    """Deny symlink, junction, reparse, unsafe hardlink and parent path substitution."""
    if not path.is_absolute():
        return False
    try:
        parts = [*reversed(path.parents), path]
        for p in parts:
            meta = p.lstat()
            if stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
                return False
            if p != path or directory:
                if not stat.S_ISDIR(meta.st_mode):
                    return False
            elif not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _process() -> str:
    if not HOST_IS_WINDOWS:
        return "PROCESS_READ_UNKNOWN"
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PROCESS_CMD],
            shell=False, stdin=subprocess.DEVNULL, capture_output=True, timeout=12,
            check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode or len(p.stdout) > 16384:
            return "PROCESS_READ_UNKNOWN"
        row = json.loads(p.stdout.decode("utf-8-sig"))
        if type(row) is not dict or type(row.get("present")) is not bool:
            return "PROCESS_READ_UNKNOWN"
        if not row["present"]:
            return "PID_ABSENT"
        created = dt.datetime.fromisoformat(row["created"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            return "PROCESS_READ_UNKNOWN"
        matched = (
            START_EARLIEST <= created.astimezone(dt.timezone.utc) <= START_LATEST
            and type(row.get("name")) is str
            and row["name"].lower() in ("python.exe", "pythonw.exe")
            and type(row.get("command")) is str
            and "ai_commerce.engineering" in row["command"]
            and "--attempt-id" in row["command"]
            and ATTEMPT in row["command"]
        )
        return "PID_MATCHED_ACTIVE" if matched else "PID_REUSED_OR_UNRELATED"
    except (OSError, ValueError, TypeError, KeyError, UnicodeError,
            subprocess.TimeoutExpired):
        return "PROCESS_READ_UNKNOWN"


def _read_stage(path: Path, stage: str | None) -> str:
    if not path.exists() and not path.is_symlink():
        return "MISSING"
    if not _safe(path):
        return "UNSAFE"
    try:
        meta = path.stat()
        if not 2 <= meta.st_size <= MAX_BYTES:
            return "UNSAFE"
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read(MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
        identity = lambda v: (v.st_dev, v.st_ino, v.st_size, v.st_mtime_ns, v.st_nlink)
        if len(raw) > MAX_BYTES or identity(before) != identity(after):
            return "READ_UNKNOWN"
        data = json.loads(raw.decode("utf-8-sig"))
        if type(data) is not dict or data.get("attempt_id") != ATTEMPT:
            return "UNSAFE"
        if data.get("replay_authorized") is True or data.get("provider_effects_reconciled") is True:
            return "UNSAFE"
        if stage is not None and data.get("stage") != stage:
            return "UNSAFE"
        if stage is None and data.get("work_id") != BATCH + ":" + ACTION:
            return "UNSAFE"
        if not _safe(path):
            return "READ_UNKNOWN"
        return "PRESENT_UNVERIFIED"
    except (OSError, UnicodeError, ValueError, TypeError):
        return "READ_UNKNOWN"


def _evidence() -> tuple[str, list[str]]:
    try:
        if not EVIDENCE.exists():
            if EVIDENCE.is_symlink():
                return "ROOT_UNSAFE", ["UNSAFE"] * len(RELS)
            return "ACTION_ABSENT", ["ROOT_ABSENT"] * len(RELS)
        if not _safe(EVIDENCE, directory=True):
            return "ROOT_UNSAFE", ["UNSAFE"] * len(RELS)
        return "ACTION_PRESENT", [
            _read_stage(EVIDENCE.joinpath(*path.split("/")), stage)
            for path, stage in RELS
        ]
    except (OSError, ValueError, RuntimeError):
        return "ROOT_READ_UNKNOWN", ["READ_UNKNOWN"] * len(RELS)


def _worktree() -> str:
    if not HOST_IS_WINDOWS:
        return "WORKTREE_READ_UNKNOWN"
    try:
        temp = Path(tempfile.gettempdir())
        if not _safe(temp, directory=True):
            return "WORKTREE_READ_UNKNOWN"
        paths = list(temp.glob("ai-commerce-ef02-" + ACTION + "-*"))
        if not paths:
            return "WORKTREE_ABSENT"
        if len(paths) > MAX_GLOB or len(paths) != 1:
            return "WORKTREE_AMBIGUOUS"
        return "WORKTREE_SINGLE_UNVERIFIED" if _safe(paths[0], directory=True) else "WORKTREE_UNSAFE"
    except (OSError, RuntimeError, ValueError):
        return "WORKTREE_READ_UNKNOWN"


def inspect() -> dict:
    process = _process()
    evidence_root, states = _evidence()
    return {
        "schema": SCHEMA, "operation": OPERATION, "target_issue": TARGET_ISSUE,
        "source_sha": SOURCE_SHA, "attempt_id": ATTEMPT,
        "status": "HOLD_UNKNOWN_EFFECT",
        "process": process, "evidence_root": evidence_root,
        "provider_boundary": states[0], "provider_return": states[1],
        "terminal_receipt": states[2], "worktree": _worktree(),
        "replay_authorized": False, "provider_outcome_proven": False,
        "write_attempted": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    # No caller-selected read target or arbitrary output write. Only the exact
    # CI runner's owned receipt path is valid.
    tmp = os.environ.get("RUNNER_TEMP")
    expected = Path(tmp) / "ef02-815-forensic.json" if tmp else None
    if expected is None or args.output != expected or args.output.exists():
        parser.error("HOSTOPS_EF02_815_OUTPUT_NOT_RUNNER_TEMP")
    args.output.write_text(json.dumps(inspect(), separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
