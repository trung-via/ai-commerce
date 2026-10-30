"""Optional local EF02 authoring checks. No dispatch, model call or authorization."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from jsonschema import ValidationError, SchemaError

from ai_commerce.engineering.git import git_argv
from ai_commerce.engineering.runner import _protected_change, _safe_scope
from .reuse import load_approved_candidates
from .validation import (HEX_SHA, IDENTITY, RemoteRequestError, fingerprint, safe_path,
                         validate_batch_content, verify_registry)


def _ordinary_path(value: str) -> str:
    safe_path(value)
    if (any(c in value for c in "*?[]") or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or _protected_change([value])):
        raise RemoteRequestError("INVALID_ORDINARY_PATH")
    return value


def _rejected(code: str, data: dict | None = None) -> dict:
    # Never stringify a schema error: its message can contain the entire request.
    data = data if isinstance(data, dict) else {}
    source = data.get("source_sha")
    source = source if isinstance(source, str) and HEX_SHA.fullmatch(source) else None
    actions = data.get("actions")
    attempt = (actions[0].get("attempt_id") if isinstance(actions, list) and len(actions) == 1
               and isinstance(actions[0], dict) else None)
    attempt = attempt if isinstance(attempt, str) and IDENTITY.fullmatch(attempt) else None
    try:
        request_hash = fingerprint(data)
    except (ValueError, TypeError):
        request_hash = None
    signature = fingerprint({"phase": "REQUEST_VALIDATION", "error_code": code,
                             "request_fingerprint": request_hash})
    return {"status": "PREFLIGHT_REJECTED", "error_code": code,
            "authorization": "UNPROVEN", "dispatch_performed": False,
            "failure_provenance": {"phase": "REQUEST_VALIDATION", "tool": "authoring_preflight",
                "status": "REJECTED", "error_code": code, "source_sha": source,
                "attempt_id": attempt, "environment": "LOCAL_OFFLINE",
                "fingerprint": signature, "replay_authorized": False}}


def preflight_batch(data: dict, *, schema: Path, registry: dict[str, list[str]],
                    expected_files: dict[str, list[str]] | None = None,
                    approved_reuse: dict | None = None,
                    source_repo: Path | None = None) -> dict:
    """Check a draft against local contracts; local approval registries are inputs.

    Passing does not attest registry authenticity, candidate bytes, actor rights or
    future server acceptance. expected_files is a separate action-id -> paths map,
    never an invented field in the immutable RemoteActionBatch packet.
    """
    try:
        validate_batch_content(data, schema=schema, registry=registry, approved_reuse=approved_reuse)
        actions = {a["action_id"]: a for a in data["actions"]}
        for a in actions.values():
            for path in a["allowed_paths"]:
                _ordinary_path(path)
        if expected_files is not None:
            if not isinstance(expected_files, dict):
                raise RemoteRequestError("INVALID_EXPECTED_FILES")
            for action_id, paths in expected_files.items():
                if action_id not in actions:
                    raise RemoteRequestError("EXPECTED_ACTION_UNKNOWN")
                if not isinstance(paths, list):
                    raise RemoteRequestError("INVALID_EXPECTED_FILES")
                for path in paths:
                    _ordinary_path(path)
                    if not _safe_scope([path], actions[action_id]["allowed_paths"]):
                        raise RemoteRequestError("EXPECTED_PATH_OUTSIDE_SCOPE")
        source_check = "NOT_REQUESTED"
        if source_repo is not None:
            observed = subprocess.run(git_argv(source_repo, "rev-parse", "HEAD"),
                capture_output=True, text=True, encoding="utf-8", errors="strict", check=False, timeout=30)
            if observed.returncode:
                raise RemoteRequestError("SOURCE_HEAD_UNAVAILABLE")
            if observed.stdout.strip() != data["source_sha"]:
                raise RemoteRequestError("SOURCE_SHA_MISMATCH")
            source_check = "EXACT_HEAD_MATCH"
        return {"status": "PREFLIGHT_PASS", "error_code": None, "authorization": "UNPROVEN",
                "dispatch_performed": False, "source_check": source_check,
                "request_fingerprint": fingerprint(data), "action_count": len(actions),
                "reuse_check": "LOCAL_REGISTRY_MATCH_ONLY" if any(
                    a.get("reuse_candidate") for a in actions.values()) else "NOT_REQUESTED"}
    except ValidationError as exc:
        if list(exc.absolute_path) == ["source_sha"]:
            code = "INVALID_SOURCE_SHA"
        elif exc.validator == "uniqueItems" and list(exc.absolute_path)[-1:] == ["allowed_paths"]:
            code = "DUPLICATE_ALLOWED_PATHS"
        else:
            code = "REQUEST_SCHEMA_INVALID"
        return _rejected(code, data)
    except RemoteRequestError as exc:
        code = str(exc).split(":", 1)[0]
        return _rejected("REUSE_UNAPPROVED" if code.startswith("REUSE_") else code, data)
    except (OSError, ValueError, TypeError, SchemaError, subprocess.SubprocessError):
        return _rejected("LOCAL_INPUT_INVALID", data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--expected-files", type=Path,
                        help="Optional JSON object mapping action_id to expected ordinary file paths")
    parser.add_argument("--approved-reuse", type=Path,
                        help="Local approved-candidates.v1 registry (default: checkout registered file if present)")
    parser.add_argument("--check-source", action="store_true", help="Require source_sha to equal checkout HEAD")
    args = parser.parse_args(argv)
    try:
        checkout = args.checkout.resolve()
        data = json.loads(args.request_file.read_text(encoding="utf-8-sig"))
        registry = verify_registry(checkout / ".github/ef02/verify-profiles.json")
        approval_path = args.approved_reuse or checkout / ".github/ef02/approved-candidates.v1.json"
        approved = (load_approved_candidates(approval_path) if args.approved_reuse or approval_path.is_file() else None)
        expected = json.loads(args.expected_files.read_text(encoding="utf-8-sig")) if args.expected_files else None
        result = preflight_batch(data, schema=checkout / "contracts/RemoteActionBatch.v1.schema.json",
            registry=registry, expected_files=expected, approved_reuse=approved,
            source_repo=checkout if args.check_source else None)
    except (OSError, ValueError, TypeError, AttributeError):
        result = _rejected("LOCAL_INPUT_INVALID")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PREFLIGHT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
