"""One-shot, non-mutating HostOps probe for the immutable EF02 #698 attempt.

The caller cannot select a PID, issue, attempt, command, path, or operation.
This probes source/EF01 evidence only; it never adjudicates safe provider replay.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

SCHEMA = "ai-commerce.hostops.ef02-attempt-reconcile.v1"
ISSUE = 698
OPERATION = "EF02_ATTEMPT_RECONCILE_READONLY"
SOURCE_SHA = "b9abe11c31954706caa816d9c53ca6c0519e913e"
ATTEMPT = "bq06b_r2_agy38flashhigh_legit_correction_reprocess_main_b9abe11c_20261109"
ACTION = "BQ06B-R2-AGY38FLASHHIGH-A09-LINEAGE-SUPERSESSION-ROOTFIX-01"
# EF02 evidence is stored under LocalBatchWorker.root, not Git worktree/ef01.
# Exact path reconstructed from the authenticated Windows run 42572795120.
# Never allow an Issue or command-line argument to choose this directory.
BATCH = "bq06b-r2-valid-a09-lineage-rootfix-agy38flashhigh-main-b9abe11c-20261109"
_EVIDENCE_ACTION_ROOT = (
    Path(r"C:\TOOL\ai-commerce-runner\_work\ai-commerce\ai-commerce\.local\ef02")
    / BATCH / (ACTION + "-" + ATTEMPT)
)
PID = 14636
# Original GitHub SPAWNED checkpoint was created at 2026-11-09T06:26:05Z.
NOT_BEFORE = dt.datetime(2026, 11, 9, 6, 25, tzinfo=dt.timezone.utc)
NOT_AFTER = dt.datetime(2026, 11, 9, 6, 28, tzinfo=dt.timezone.utc)
MAX_RESULTS = 4
MAX_SOURCE_BYTES = 4 * 1024 * 1024
EXPECTED_RELATIVE = (
    "src/ai_commerce/creative_quality/behavioral_learning_fresh_replay.py",
    "tests/test_bq_06b_fresh_a09_replay.py",
    "work_items/mt01-components/bq-06b.json",
    "work_items/BQ-06B_FRESH_A09_REPLAY_001.json",
    "docs/creative/BQ_06B_FRESH_EVIDENCE_PROTOCOL.md",
)
_CODES_PROCESS = {"PID_ABSENT", "PID_MATCHED_ACTIVE", "PID_REUSED_OR_UNRELATED", "PROCESS_READ_UNAVAILABLE"}
_CODES_WORKTREE = {"WORKTREE_ABSENT", "WORKTREE_SINGLE", "WORKTREE_AMBIGUOUS", "WORKTREE_REPARSE_OR_HARDLINK", "WORKTREE_READ_UNAVAILABLE"}
_CODES_SOURCE = {"SOURCE_ABSENT_IN_WORKTREE", "SOURCE_BYTES_PRESENT_UNVERIFIED", "SOURCE_READ_UNAVAILABLE", "SOURCE_NOT_INSPECTED"}
_CODES_RECEIPT = {"NO_TERMINAL_RECEIPT_OBSERVED", "LOCAL_RESULT_PRESENT_UNVERIFIED", "RECEIPT_READ_UNAVAILABLE", "RECEIPT_NOT_INSPECTED"}

# An exact constant command, not a shell or issue-supplied script.
_WINDOWS_CIM_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=14636';"
    "if($null -eq $p){'{\"present\":false}'}else{"
    "@{present=$true;name=[string]$p.Name;command=[string]$p.CommandLine;"
    "created=([datetime]$p.CreationDate).ToUniversalTime().ToString('o')}"
    "|ConvertTo-Json -Compress -Depth 2}"
)


def _probe_pid() -> str:
    if os.name != "nt":
        return "PROCESS_READ_UNAVAILABLE"
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_CIM_SCRIPT],
            shell=False, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=12, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or len(result.stdout) > 16384:
            return "PROCESS_READ_UNAVAILABLE"
        data = json.loads(result.stdout.decode("utf-8-sig"))
        if type(data) is not dict or type(data.get("present")) is not bool:
            return "PROCESS_READ_UNAVAILABLE"
        if not data["present"]:
            return "PID_ABSENT"
        created = dt.datetime.fromisoformat(data["created"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            return "PROCESS_READ_UNAVAILABLE"
        command = data.get("command", "")
        name = data.get("name", "")
        if (
            NOT_BEFORE <= created.astimezone(dt.timezone.utc) <= NOT_AFTER
            and type(command) is str and ATTEMPT in command
            and "ai_commerce.engineering" in command and "--attempt-id" in command
            and type(name) is str and name.lower() in ("python.exe", "pythonw.exe")
        ):
            return "PID_MATCHED_ACTIVE"
        return "PID_REUSED_OR_UNRELATED"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.TimeoutExpired, UnicodeError):
        return "PROCESS_READ_UNAVAILABLE"


def _safe_chain(path: Path, base: Path) -> bool:
    """Deny reparse points/hardlinks and refuse paths outside exact temp base."""
    try:
        root = base.resolve(strict=True)
        if path == base or root not in path.resolve(strict=True).parents:
            return False
        cursor = base
        for name in path.relative_to(base).parts:
            if name in ("", ".", "..") or ":" in name or "\\" in name:
                return False
            cursor = cursor / name
            stat = cursor.lstat()
            if (cursor.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400
                    or (cursor.is_file() and stat.st_nlink > 1)):
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _probe_worktree() -> tuple[str, str, str]:
    if os.name != "nt":
        return "WORKTREE_READ_UNAVAILABLE", "SOURCE_NOT_INSPECTED", "RECEIPT_NOT_INSPECTED"
    try:
        base = Path(tempfile.gettempdir())
        if base.is_symlink() or not base.is_dir():
            return "WORKTREE_READ_UNAVAILABLE", "SOURCE_NOT_INSPECTED", "RECEIPT_NOT_INSPECTED"
        pattern = "ai-commerce-ef02-" + ACTION + "-*"
        matches = list(base.glob(pattern))
        if not matches:
            return "WORKTREE_ABSENT", "SOURCE_NOT_INSPECTED", "RECEIPT_NOT_INSPECTED"
        if len(matches) != 1 or len(matches) > MAX_RESULTS:
            return "WORKTREE_AMBIGUOUS", "SOURCE_NOT_INSPECTED", "RECEIPT_NOT_INSPECTED"
        root = matches[0]
        if not _safe_chain(root, base) or not root.is_dir():
            return "WORKTREE_REPARSE_OR_HARDLINK", "SOURCE_NOT_INSPECTED", "RECEIPT_NOT_INSPECTED"
        seen = 0
        for rel in EXPECTED_RELATIVE:
            target = root.joinpath(*rel.split("/"))
            if target.exists():
                if not _safe_chain(target, base) or not target.is_file():
                    return "WORKTREE_REPARSE_OR_HARDLINK", "SOURCE_READ_UNAVAILABLE", "RECEIPT_READ_UNAVAILABLE"
                if target.stat().st_size > MAX_SOURCE_BYTES:
                    return "WORKTREE_SINGLE", "SOURCE_READ_UNAVAILABLE", "RECEIPT_READ_UNAVAILABLE"
                # Inspect bytes privately, never publish path/content/hash.
                with target.open("rb") as handle:
                    sha = hashlib.sha256(handle.read(MAX_SOURCE_BYTES + 1)).hexdigest()
                if not re.fullmatch("[0-9a-f]{64}", sha):
                    return "WORKTREE_SINGLE", "SOURCE_READ_UNAVAILABLE", "RECEIPT_READ_UNAVAILABLE"
                seen += 1
        source = "SOURCE_BYTES_PRESENT_UNVERIFIED" if seen else "SOURCE_ABSENT_IN_WORKTREE"
        # EF01 receipts normally live outside the detached source worktree.
        # Do not infer non-invocation when absent here.
        terminal = root / "ef01" / "engineering_result.json"
        if terminal.exists():
            if not _safe_chain(terminal, base) or terminal.stat().st_size > MAX_SOURCE_BYTES:
                return "WORKTREE_SINGLE", source, "RECEIPT_READ_UNAVAILABLE"
            with terminal.open("rb") as f:
                json.loads(f.read(MAX_SOURCE_BYTES + 1))
            receipt = "LOCAL_RESULT_PRESENT_UNVERIFIED"
        else:
            receipt = "NO_TERMINAL_RECEIPT_OBSERVED"
        return "WORKTREE_SINGLE", source, receipt
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return "WORKTREE_READ_UNAVAILABLE", "SOURCE_READ_UNAVAILABLE", "RECEIPT_READ_UNAVAILABLE"


def _safe_absolute_receipt_path(target: Path) -> bool:
    """Fail closed for symlinks/reparse and nonregular evidence-path ancestors."""
    try:
        if not target.is_absolute():
            return False
        cursor = Path(target.anchor)
        for name in target.parts[1:]:
            if name in ("", ".", "..") or ":" in name or "\\" in name:
                return False
            cursor = cursor / name
            info = cursor.lstat()
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                return False
            if cursor == target:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    return False
            elif not stat.S_ISDIR(info.st_mode):
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _probe_ef02_evidence_root() -> str:
    """Observe only one fixed EF02 result; never publish private bytes or paths.

    JSON presence is not an EF01 signer, verified provider return or replay grant.
    """
    if os.name != "nt":
        return "RECEIPT_READ_UNAVAILABLE"
    terminal = _EVIDENCE_ACTION_ROOT / "ef01" / "engineering_result.json"
    try:
        if not _EVIDENCE_ACTION_ROOT.exists():
            return "NO_TERMINAL_RECEIPT_OBSERVED"
        if not _EVIDENCE_ACTION_ROOT.is_dir() or not terminal.exists():
            return "NO_TERMINAL_RECEIPT_OBSERVED"
        if not _safe_absolute_receipt_path(terminal):
            return "RECEIPT_READ_UNAVAILABLE"
        prior = terminal.lstat()
        if prior.st_size < 2 or prior.st_size > MAX_SOURCE_BYTES:
            return "RECEIPT_READ_UNAVAILABLE"
        with terminal.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            content = handle.read(MAX_SOURCE_BYTES + 1)
            closed = os.fstat(handle.fileno())
        identity = lambda o: (o.st_dev, o.st_ino, o.st_size, o.st_mtime_ns, o.st_nlink)
        if (len(content) > MAX_SOURCE_BYTES
                or identity(prior) != identity(opened)
                or identity(opened) != identity(closed)
                or not _safe_absolute_receipt_path(terminal)):
            return "RECEIPT_READ_UNAVAILABLE"
        result = json.loads(content.decode("utf-8-sig"))
        if (type(result) is not dict or result.get("attempt_id") != ATTEMPT
                or result.get("work_id") != BATCH + ":" + ACTION):
            return "RECEIPT_READ_UNAVAILABLE"
        return "LOCAL_RESULT_PRESENT_UNVERIFIED"
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return "RECEIPT_READ_UNAVAILABLE"


def observe() -> dict:
    process = _probe_pid()
    worktree, source, receipt = _probe_worktree()
    if worktree == "WORKTREE_SINGLE" and receipt == "NO_TERMINAL_RECEIPT_OBSERVED":
        receipt = _probe_ef02_evidence_root()
    assert process in _CODES_PROCESS and worktree in _CODES_WORKTREE
    assert source in _CODES_SOURCE and receipt in _CODES_RECEIPT
    return {
        "schema": SCHEMA, "issue_number": ISSUE, "operation": OPERATION,
        "source_sha": SOURCE_SHA, "attempt_id": ATTEMPT,
        "status": "HOLD_UNKNOWN_EFFECT", "process": process,
        "worktree": worktree, "source": source, "local_receipt": receipt,
        "replay_authorized": False, "write_attempted": False,
        "provider_outcome_proven": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    # Output is only an ephemeral GitHub Actions runner receipt, never any
    # worktree, WorkOwner journal, provider config or process file.
    output = Path(args.output)
    if not os.getenv("RUNNER_TEMP") or output.parent.resolve() != Path(os.environ["RUNNER_TEMP"]).resolve():
        raise SystemExit("OUTPUT_NOT_IN_RUNNER_TEMP")
    output.write_text(json.dumps(observe(), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
