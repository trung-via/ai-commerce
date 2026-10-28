"""EF02 approved candidate reuse: source/profile/scope pin and zero model execution."""
from __future__ import annotations
import json
import re
from pathlib import Path
from .validation import RemoteRequestError

SHA = re.compile(r"^[0-9a-f]{40}$")

def load_approved_candidates(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("version") != 1 or not isinstance(data.get("candidates"), dict):
        raise RemoteRequestError("REUSE_REGISTRY_INVALID")
    return data

def approve_reuse(batch: dict, action: dict, approved: dict) -> dict:
    request = action.get("reuse_candidate")
    entry = approved.get("candidates", {}).get(action["attempt_id"])
    if not isinstance(entry, dict):
        raise RemoteRequestError("REUSE_ATTEMPT_NOT_APPROVED")
    for field in ("action_id", "attempt_id", "executor", "model", "effort",
                  "allowed_paths", "verification_profile", "depends_on"):
        if action.get(field) != entry.get(field):
            raise RemoteRequestError("REUSE_PROFILE_OR_SCOPE_MISMATCH:" + field)
    if batch["source_sha"] != approved.get("approved_source_sha") or batch["source_sha"] != entry.get("source_sha"):
        raise RemoteRequestError("REUSE_SOURCE_SHA_MISMATCH")
    if request != {"candidate_sha": entry.get("candidate_sha"), "verified_run_id": entry.get("verified_run_id")}:
        raise RemoteRequestError("REUSE_CANDIDATE_NOT_APPROVED")
    if (entry.get("verification_status") != "BUILD_VERIFIED_FOR_SCOPE"
        or entry.get("verify_exit_code") != 0 or entry.get("scope_valid") is not True
        or any(not isinstance(entry.get(p), str) or not SHA.fullmatch(entry[p])
               for p in ("parent_sha", "tree_sha", "candidate_sha"))):
        raise RemoteRequestError("REUSE_APPROVAL_NOT_VERIFIED")
    return entry
