"""AI Commerce HostOps v1: bounded, read-only Windows diagnostics.

Never interpret Issue text as PowerShell. Two supported operations map to one
repository-owned script with a closed argument set. No provider/Chrome Send/EF02
side effects are part of this entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY = "trung-via/ai-commerce"
REQUEST_SCHEMA = "ai-commerce-hostops-v1"
RESULT_SCHEMA = "ai-commerce-hostops-result-v1"
OPERATIONS = frozenset({"runner_readonly", "d5_readonly"})
MAX_INPUT_BYTES = 4096
MAX_OUTPUT_BYTES = 8192


class RequestRejected(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RequestRejected("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def validate_request(raw: bytes, *, repository: str, issuer: str, owner: str,
                     source_sha: str, is_private: bool) -> dict:
    if repository != REPOSITORY or not is_private:
        raise RequestRejected("REPO_NOT_PRIVATE_OR_WRONG")
    if issuer != owner or owner != REPOSITORY.split("/")[0]:
        raise RequestRejected("ISSUER_NOT_OWNER")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise RequestRejected("INVALID_SOURCE_SHA")
    if not 1 <= len(raw) <= MAX_INPUT_BYTES:
        raise RequestRejected("INVALID_REQUEST_LENGTH")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise RequestRejected("INVALID_JSON") from exc
    if not isinstance(data, dict) or set(data) != {
        "schema", "request_id", "repository", "source_sha", "operation", "parameters"
    }:
        raise RequestRejected("INVALID_REQUEST_SHAPE")
    if data["schema"] != REQUEST_SCHEMA or data["repository"] != REPOSITORY:
        raise RequestRejected("INVALID_IDENTITY")
    if data["source_sha"] != source_sha:
        raise RequestRejected("SOURCE_SHA_MISMATCH")
    request_id = data["request_id"]
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}", request_id):
        raise RequestRejected("INVALID_REQUEST_ID")
    if type(data["operation"]) is not str or data["operation"] not in OPERATIONS:
        raise RequestRejected("UNSUPPORTED_OPERATION")
    if data["parameters"] != {} or type(data["parameters"]) is not dict:
        raise RequestRejected("PARAMETERS_MUST_BE_EMPTY")
    return data


def read_request(path: str, args: argparse.Namespace) -> tuple[dict, str]:
    try:
        with open(path, "rb") as file:
            raw = file.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise RequestRejected("REQUEST_NOT_READABLE") from exc
    request = validate_request(
        raw, repository=args.repository, issuer=args.issuer,
        owner=args.owner, source_sha=args.source_sha,
        is_private=args.is_private == "true",
    )
    digest = hashlib.sha256(raw).hexdigest()
    return request, digest


def run_diagnostics(request: dict, digest: str, *, timeout: int = 45) -> dict:
    script = Path(__file__).resolve().with_name("HostOps-Diagnostics.ps1")
    if not script.is_file():
        raise RequestRejected("DIAGNOSTIC_SCRIPT_MISSING")
    try:
        process = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
             str(script), "-Operation", request["operation"]],
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RequestRejected("POWERSHELL_UNAVAILABLE_OR_TIMEOUT") from exc
    if process.returncode != 0 or len(process.stdout) > MAX_OUTPUT_BYTES:
        # Raw stdout/stderr can contain private Windows data. Never propagate.
        raise RequestRejected("DIAGNOSTIC_COMMAND_FAILED")
    try:
        payload = json.loads(process.stdout.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise RequestRejected("INVALID_DIAGNOSTIC_OUTPUT") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "operation", "session_id", "pwsh_major", "runner_listener_count",
        "chrome_process_count", "cdp_9333_listening"
    }:
        raise RequestRejected("INVALID_DIAGNOSTIC_SHAPE")
    if payload["operation"] != request["operation"]:
        raise RequestRejected("DIAGNOSTIC_OPERATION_MISMATCH")
    for field in ("session_id", "pwsh_major", "runner_listener_count", "chrome_process_count"):
        if type(payload[field]) is not int or not 0 <= payload[field] <= 10000:
            raise RequestRejected("INVALID_DIAGNOSTIC_FIELD")
    if type(payload["cdp_9333_listening"]) is not bool:
        raise RequestRejected("INVALID_DIAGNOSTIC_FIELD")
    return {
        "schema": RESULT_SCHEMA, "request_id": request["request_id"],
        "source_sha": request["source_sha"], "operation": request["operation"],
        "request_sha256": digest, "status": "OBSERVED", "diagnostics": payload,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "execute"))
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--is-private", required=True, choices=("true", "false"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output_path = Path(args.output)
    try:
        request, digest = read_request(args.request_file, args)
        if args.command == "validate":
            output = {"schema": REQUEST_SCHEMA, "request_id": request["request_id"],
                      "request_sha256": digest, "operation": request["operation"],
                      "source_sha": request["source_sha"]}
        else:
            output = run_diagnostics(request, digest)
    except RequestRejected as exc:
        output = {"schema": RESULT_SCHEMA, "status": "BLOCKED", "error_code": exc.code}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, sort_keys=True) + "\n", encoding="utf-8")
        print("HOSTOPS_BLOCKED=" + exc.code, file=sys.stderr)
        return 2
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, sort_keys=True) + "\n", encoding="utf-8")
    print("HOSTOPS_" + ("VALIDATED" if args.command == "validate" else "OBSERVED"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
