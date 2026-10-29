"""Owner-only hosted GitHub pre-spawn proof. Never imports/runs EF01 or model CLIs.

This live probe creates a *new immutable* EF02 claim and its pre-spawn ref.
The attempt ID is unique to this GitHub Actions run and MUST NOT be replayed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from ai_commerce.remote.transport import GitHubTransport

TITLE = "[M0R] Hosted remote pre-spawn checkpoint probe"
SHA = re.compile(r"^[0-9a-f]{40}$")


def run_probe(environ: dict, event: dict, transport_factory=GitHubTransport) -> dict:
    repo = environ.get("GITHUB_REPOSITORY", "")
    owner = environ.get("GITHUB_REPOSITORY_OWNER", "")
    source = environ.get("GITHUB_SHA", "")
    run_id = environ.get("GITHUB_RUN_ID", "")
    if (environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or event.get("repository", {}).get("full_name") != repo
            or environ.get("GITHUB_ACTOR") != owner
            or event.get("sender", {}).get("login") != owner
            or environ.get("GITHUB_REF") != "refs/heads/main"
            or event.get("ref") != "refs/heads/main"
            or not owner or not repo.startswith(owner + "/")
            or not SHA.fullmatch(source) or not run_id.isdecimal()
            or int(run_id) < 1):
        raise ValueError("M0R_HOSTED_PROBE_GUARD_REJECTED")
    attempt = "m0r-hosted-checkpoint-" + run_id
    fingerprint = hashlib.sha256(json.dumps(
        {"kind": "M0R_HOSTED_REMOTE_CHECKPOINT_NO_MODEL",
         "repo": repo, "run_id": run_id, "source_sha": source},
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    transport = transport_factory(repo, environ.get("GITHUB_TOKEN", ""))
    claim = transport.claim(source_sha=source, attempt_id=attempt,
                            fingerprint=fingerprint)
    if claim != "CLAIMED":
        raise RuntimeError("M0R_CLAIM_NOT_NEW_NO_REPLAY")
    checkpoint_ref = transport.pre_spawn_checkpoint(
        source_sha=source, attempt_id=attempt, fingerprint=fingerprint)
    expected = "refs/heads/ef02/pre-spawn/" + transport.attempt_key(attempt)
    if checkpoint_ref != expected:
        raise RuntimeError("M0R_CHECKPOINT_REF_UNEXPECTED")
    return {"probe": "M0R_HOSTED_REMOTE_CHECKPOINT_NO_MODEL",
            "status": "CHECKPOINT_CREATED_NOT_PROVIDER_PROOF",
            "repo": repo, "source_sha": source, "attempt_id": attempt,
            "checkpoint_ref": checkpoint_ref, "model_invoked": False,
            "ef01_invoked": False, "windows_runner_invoked": False,
            "candidate_created": False, "replay_authorized": False}


def main() -> int:
    try:
        path = Path(os.environ["GITHUB_EVENT_PATH"])
        event = json.loads(path.read_text(encoding="utf-8"))
        result = run_probe(os.environ, event)
    except Exception as exc:
        # Never log GitHub authorization credentials or exception bodies.
        print("M0R_REMOTE_CHECKPOINT_NONPASS=" + type(exc).__name__, file=sys.stderr)
        return 1
    dest = Path(".local/ef02-m0r/remote-checkpoint.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("M0R_REMOTE_CHECKPOINT_CREATED=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
