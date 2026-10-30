"""Retain this attempt's observed pytest streams and source hashes, never Git writes."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent


def hashes():
    paths = sorted((ROOT / "src/ai_commerce/product").glob("*.py"))
    paths += [ROOT / name for name in ("tests/test_a12_rights.py", "tests/test_a12_registry.py",
                                      "contracts/ProductAssetRights.v1.schema.json")]
    return {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in paths}


label, profile = sys.argv[1:]
assert label.replace(".", "").replace("-", "").isalnum()
assert profile in ("targeted", "project-tests")
args = (["tests/test_a12_rights.py", "tests/test_a12_registry.py"] if profile == "targeted" else ["tests"])
command = ["python", "-m", "pytest", *args, "-q", "-n", "12"]
before = hashes()
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout.decode().strip()
started = time.monotonic()
result = subprocess.run(command, cwd=ROOT, capture_output=True)
elapsed = time.monotonic() - started
(OUT / (label + ".stdout.log")).write_bytes(result.stdout)
(OUT / (label + ".stderr.log")).write_bytes(result.stderr)
stdout = result.stdout.decode("utf-8", errors="replace")
metadata = {"command": " ".join(command), "source_sha": head, "exit_code": result.returncode,
            "duration_seconds": round(elapsed, 3), "python": sys.version, "source_hashes": before,
            "source_hashes_unchanged": before == hashes(),
            "stdout_sha256": sha256(result.stdout).hexdigest(), "stderr_sha256": sha256(result.stderr).hexdigest(),
            "summary_lines": [line[:500] for line in stdout.splitlines() if line.startswith(("FAILED ", "ERROR "))
                              or ("passed" in line and " in " in line)]}
(OUT / (label + ".json")).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8", newline="\n")
print(json.dumps({key: metadata[key] for key in ("command", "exit_code", "duration_seconds", "summary_lines")}))
if result.returncode:
    print("\n".join(line[:500] for line in stdout[-24000:].splitlines()))
    print(result.stderr.decode("utf-8", errors="replace")[-2000:])
sys.exit(result.returncode)
