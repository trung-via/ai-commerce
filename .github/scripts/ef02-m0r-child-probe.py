"""No-model isolated Windows child interruption conformance probe.

This does NOT simulate a crashed self-hosted runner or prove provider side effects.
It kills only the Python child PID created by this script, never a runner service.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


CHILD_SCRIPT = """
import json
import pathlib
import sys
import time

p = pathlib.Path(sys.argv[1])
p.write_text(json.dumps({"status": "BUILD_VERIFIED_FOR_SCOPE",
                         "verify_exit_code": 0,
                         "fixture_only": True}), encoding="utf-8")
time.sleep(90)
"""


def run_probe(out: Path) -> dict:
    """Kill an owned child after it writes a success-shaped but untrusted receipt."""
    with tempfile.TemporaryDirectory(prefix="ef02-m0r-child-") as directory:
        receipt_path = Path(directory) / "engineering_result.json"
        child = subprocess.Popen([sys.executable, "-c", CHILD_SCRIPT, str(receipt_path)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not receipt_path.is_file():
                if child.poll() is not None:
                    raise RuntimeError("PROBE_CHILD_EXITED_BEFORE_RECEIPT")
                if time.monotonic() >= deadline:
                    raise RuntimeError("PROBE_CHILD_RECEIPT_TIMEOUT")
                time.sleep(0.05)
            # The simulated child remains alive after writing an apparently
            # successful receipt, reproducing the unsafe stale-receipt ambiguity.
            if child.poll() is not None:
                raise RuntimeError("PROBE_CHILD_NOT_RUNNING_BEFORE_INTERRUPT")
            child.kill()
            child.wait(timeout=5)
            if child.poll() is None:
                raise RuntimeError("PROBE_CHILD_STILL_RUNNING")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("status") != "BUILD_VERIFIED_FOR_SCOPE":
                raise RuntimeError("PROBE_STALE_RECEIPT_NOT_CREATED")
            result = {
                "probe": "EF02_M0R_ISOLATED_WINDOWS_CHILD",
                "status": "EXECUTION_UNKNOWN",
                "error_code": "SIMULATED_CHILD_INTERRUPTED",
                "receipt_after_interrupt": True,
                "receipt_status_ignored": receipt["status"],
                "child_process_id": child.pid,
                "child_exit_code": child.returncode,
                "child_terminated": True,
                "executor_invoked": False,
                "model_invoked": False,
                "claim_ref": None,
                "candidate_ref": None,
                "candidate_sha": None,
                "runner_service_interrupted": False,
                "replay_authorized": False,
                "scope": "OWNED_CHILD_PROCESS_ONLY_NOT_REAL_RUNNER_CRASH",
            }
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
            return result
        finally:
            # Scope strictly to this Popen handle; do not terminate any other PID.
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_probe(args.out)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        print("EF02_M0R_CHILD_PROBE_NONPASS=" + type(exc).__name__ + ":" + str(exc),
              file=sys.stderr)
        return 1
    print("EF02_M0R_CHILD_PROBE_PASS=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
