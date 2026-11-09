"""R0.2 forensic-only local read for the immutable BQ-06b EF02 attempt #698.

The public Issue cannot select any path, PID, model or command. This observer
does not modify WorkOwner, EF01, the worktree, provider state or any source.
JSON presence/byte comparison is NOT authenticated model or replay evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from . import ef02_attempt_reconcile_readonly as prior

SCHEMA = "ai-commerce.hostops.ef02-698-r02.v1"
OPERATION = "EF02_698_FORENSIC_READONLY_R02"
PATHS = prior.EXPECTED_RELATIVE + (
    "docs/roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md",
)
BOUNDARY_FILES = (
    "ef01/provider_boundary.json",
    "ef01/provider_return_observed.json",
    "ef01/engineering_result.json",
)
EVIDENCE_ROOT = prior._EVIDENCE_ACTION_ROOT
MAX_FILE = 4 * 1024 * 1024
MAX_SCOPE = 6 * MAX_FILE
GIT_TIMEOUT = 10
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _safe_absolute(path: Path, *, directory: bool = False) -> bool:
    """Check every existing path component, no linked ancestors or hardlink files."""
    if not path.is_absolute():
        return False
    try:
        cursor = Path(path.anchor)
        for part in path.parts[1:]:
            if part in ("", ".", "..") or ":" in part or "\\" in part:
                return False
            cursor = cursor / part
            item = cursor.lstat()
            if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
                return False
            if cursor != path:
                if not stat.S_ISDIR(item.st_mode):
                    return False
            elif directory:
                if not stat.S_ISDIR(item.st_mode):
                    return False
            elif not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _read_fixed_json(root: Path, rel: str) -> str:
    target = root.joinpath(*rel.split("/"))
    try:
        if target.is_symlink():
            return "UNSAFE"
        if not target.exists():
            return "MISSING"
        if not _safe_absolute(target):
            return "UNSAFE"
        s0 = target.lstat()
        if s0.st_size < 2 or s0.st_size > MAX_FILE:
            return "UNSAFE"
        with target.open("rb") as fh:
            s1 = os.fstat(fh.fileno())
            content = fh.read(MAX_FILE + 1)
            s2 = os.fstat(fh.fileno())
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink)
        if identity(s0) != identity(s1) or identity(s1) != identity(s2) or not _safe_absolute(target):
            return "UNSAFE"
        data = json.loads(content.decode("utf-8-sig"))
        if type(data) is not dict or data.get("attempt_id") != prior.ATTEMPT:
            return "UNSAFE"
        if rel == "ef01/engineering_result.json":
            if data.get("work_id") != prior.BATCH + ":" + prior.ACTION:
                return "UNSAFE"
        if rel == "ef01/provider_return_observed.json" and data.get("stage") != "EXECUTOR_SUBPROCESS_RETURNED":
            return "UNSAFE"
        if rel == "ef01/provider_boundary.json" and data.get("stage") != "BEFORE_EXECUTOR_SUBPROCESS":
            return "UNSAFE"
        return "PRESENT_UNVERIFIED"
    except (OSError, ValueError, UnicodeError, TypeError, json.JSONDecodeError):
        return "READ_UNKNOWN"


def _probe_evidence_root() -> tuple[str, list[str]]:
    root = EVIDENCE_ROOT
    batch = root.parent
    try:
        if batch.is_symlink():
            return "ROOT_UNSAFE", ["READ_UNKNOWN"] * len(BOUNDARY_FILES)
        if not batch.exists():
            return "BATCH_ABSENT", ["ROOT_ABSENT"] * len(BOUNDARY_FILES)
        if not _safe_absolute(batch, directory=True):
            return "ROOT_UNSAFE", ["READ_UNKNOWN"] * len(BOUNDARY_FILES)
        if root.is_symlink():
            return "ROOT_UNSAFE", ["READ_UNKNOWN"] * len(BOUNDARY_FILES)
        if not root.exists():
            return "ACTION_ABSENT", ["ROOT_ABSENT"] * len(BOUNDARY_FILES)
        if not _safe_absolute(root, directory=True):
            return "ROOT_UNSAFE", ["READ_UNKNOWN"] * len(BOUNDARY_FILES)
        return "ACTION_PRESENT", [_read_fixed_json(root, p) for p in BOUNDARY_FILES]
    except (OSError, ValueError):
        return "ROOT_READ_UNKNOWN", ["READ_UNKNOWN"] * len(BOUNDARY_FILES)


def _get_isolated_worktree() -> tuple[str, Path | None]:
    if os.name != "nt":
        return "SOURCE_READ_UNKNOWN", None
    try:
        base = Path(tempfile.gettempdir())
        if not _safe_absolute(base, directory=True):
            return "SOURCE_UNSAFE", None
        matches = list(base.glob("ai-commerce-ef02-" + prior.ACTION + "-*"))
        if not matches:
            return "WORKTREE_ABSENT", None
        if len(matches) != 1:
            return "WORKTREE_AMBIGUOUS", None
        root = matches[0]
        if not prior._safe_chain(root, base) or not _safe_absolute(root, directory=True):
            return "SOURCE_UNSAFE", None
        return "WORKTREE_SINGLE", root
    except (OSError, RuntimeError, ValueError):
        return "SOURCE_READ_UNKNOWN", None


def _git_blob(root: Path, path: str) -> bytes | None:
    """Local exact-commit read: no remote, hooks, Git writes or shell."""
    if path not in PATHS:
        raise ValueError("PATH_NOT_PINNED")
    env = dict(os.environ)
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull})
    p = subprocess.run(
        ["git", "-C", str(root), "show", prior.SOURCE_SHA + ":" + path],
        shell=False, stdin=subprocess.DEVNULL, capture_output=True, timeout=GIT_TIMEOUT,
        env=env, check=False,
    )
    if p.returncode:
        raise ValueError("BASELINE_BLOB_UNKNOWN")
    if len(p.stdout) > MAX_FILE:
        raise ValueError("BASELINE_BLOB_OVERSIZE")
    return p.stdout


def _read_scope() -> tuple[str, int, int, int, str | None]:
    state, root = _get_isolated_worktree()
    if state != "WORKTREE_SINGLE" or root is None:
        return state, 0, 0, 0, None
    seen, changed, absent, fingerprint = 0, 0, 0, hashlib.sha256()
    try:
        # Original R2 source may not have all five new files; for those, compare
        # current bytes only, without claiming their origin or provider authorship.
        for rel in PATHS:
            file = root.joinpath(*rel.split("/"))
            if file.is_symlink():
                return "SOURCE_UNSAFE", 0, 0, 0, None
            if not file.exists():
                absent += 1
                fingerprint.update((rel + ":ABSENT\n").encode())
                continue
            if not prior._safe_chain(file, Path(tempfile.gettempdir())) or not _safe_absolute(file):
                return "SOURCE_UNSAFE", 0, 0, 0, None
            s0 = file.lstat()
            if s0.st_size > MAX_FILE:
                return "SOURCE_UNSAFE", 0, 0, 0, None
            with file.open("rb") as handle:
                s1 = os.fstat(handle.fileno())
                content = handle.read(MAX_FILE + 1)
                s2 = os.fstat(handle.fileno())
            identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink)
            if (len(content) > MAX_FILE or identity(s0) != identity(s1)
                    or identity(s1) != identity(s2) or not _safe_absolute(file)):
                return "SOURCE_UNSAFE", 0, 0, 0, None
            digest = hashlib.sha256(content).hexdigest()
            fingerprint.update((rel + ":" + digest + "\n").encode())
            seen += 1
            # Only the roadmap is known to have existed before R2. An absent
            # baseline for a new addition is not evidence of authorship.
            if rel == PATHS[-1]:
                base = _git_blob(root, rel)
                if content != base:
                    changed += 1
        if absent:
            status = "SCOPE_PARTIAL_UNVERIFIED"
        else:
            status = "SCOPE_PRESENT_UNVERIFIED"
        return status, seen, absent, changed, fingerprint.hexdigest()
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        return "SOURCE_READ_UNKNOWN", 0, 0, 0, None


def observe() -> dict:
    root, boundary = _probe_evidence_root()
    scope, seen, absent, roadmap_diff, digest = _read_scope()
    return {
        "schema": SCHEMA, "operation": OPERATION, "issue_number": prior.ISSUE,
        "source_sha": prior.SOURCE_SHA, "attempt_id": prior.ATTEMPT,
        "status": "HOLD_UNKNOWN_EFFECT", "evidence_root": root,
        "boundary": boundary[0], "provider_return": boundary[1], "terminal": boundary[2],
        "source_state": scope, "scope_seen": seen, "scope_missing": absent,
        "roadmap_diff_unverified": roadmap_diff, "scope_manifest_digest": digest,
        "replay_authorized": False, "provider_outcome_proven": False,
        "write_attempted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = Path(args.output)
    if (not os.getenv("RUNNER_TEMP")
            or target.parent.resolve() != Path(os.environ["RUNNER_TEMP"]).resolve()):
        raise SystemExit("OUTPUT_NOT_IN_RUNNER_TEMP")
    target.write_text(json.dumps(observe(), sort_keys=True, separators=(",", ":")) + "\n",
                      encoding="utf-8")


if __name__ == "__main__":
    main()
