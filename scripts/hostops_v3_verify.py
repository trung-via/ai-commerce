"""Parent-context diagnostic helper; EF01 owns registered verification.

Never run the broad suite from a restricted Codex child. This helper does not
remove GitGuard/sandbox environment or claim EF01 verification provenance.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    if any(name in os.environ for name in (
            "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI", "AI_COMMERCE_GIT_GUARD_PATHS")):
        print("PARENT_EF01_VERIFICATION_REQUIRED", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    registry = root / ".github/ef02/verify-profiles.json"
    raw = registry.read_bytes()
    argv = json.loads(raw)["project-tests"]
    if argv != ["python", "-m", "pytest", "tests", "-q", "-n", "12"]:
        raise SystemExit("REGISTERED_PROFILE_MISMATCH")
    folder = root / ".local/hostops-v3-repair/parent-helper"
    folder.mkdir(parents=True, exist_ok=True)
    start = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    # No extra pytest flags, filtering, environment changes or Git operations.
    p = subprocess.run(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    log = folder / "project-tests-windows.txt"
    log.write_bytes(p.stdout)
    receipt = {"schema": "hostops.verification.v3", "profile": "project-tests", "argv": argv,
               "verification_origin": "MANUAL_PARENT_HELPER_NOT_REGISTERED_EF01",
               "registry_sha256": hashlib.sha256(raw).hexdigest(), "platform": sys.platform,
               "started_at": start, "finished_at": datetime.now(timezone.utc).isoformat(),
               "elapsed_seconds": round(time.monotonic() - clock, 3), "exit_code": p.returncode,
               "stdout_sha256": hashlib.sha256(p.stdout).hexdigest(), "log": log.name,
               "requested_workers": 12, "filtered": False, "live_ready": False}
    (folder / "project-tests-windows.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    sys.stdout.buffer.write(p.stdout)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
