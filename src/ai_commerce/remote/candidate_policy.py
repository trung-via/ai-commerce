"""Candidate publication is evidence retention, never scope or merge approval."""
from __future__ import annotations

import hashlib
import json
import re

from ai_commerce.engineering.runner import _outside_scope
from ai_commerce.engineering.source_safety import protected_change, ordinary_scope_drift

REVIEW_STATUS = "CANDIDATE_READY_SCOPE_REVIEW"
VERIFIED_STATUSES = ("BUILD_VERIFIED_FOR_SCOPE", "VERIFIED_SCOPE_REVIEW_REQUIRED")
EVIDENCE_MARKER = "\n\nEF02-CANDIDATE-EVIDENCE-V1\n"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def evidence_digest(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def has_scope_review(value: dict) -> bool:
    """Flags cannot be erased by merely renaming a result's status."""
    return (value.get("status") in (REVIEW_STATUS, "VERIFIED_SCOPE_REVIEW_REQUIRED") or
            value.get("review_required", False) is not False or
            value.get("scope_review_required", False) is not False or
            ("scope_valid" in value and value["scope_valid"] is not True) or
            value.get("out_of_scope_files", []) != [] or
            (isinstance(value.get("candidate_evidence"), dict) and
             has_scope_review(value["candidate_evidence"])))


def candidate_accepted(value: dict) -> bool:
    return value.get("status") == "CANDIDATE_READY" and not has_scope_review(value)


def validate_candidate_evidence(value: dict) -> dict:
    """Validate the immutable contract, including status/scope/hash consistency."""
    try:
        changed = value["changed_files"]
        hashes = value["candidate_file_sha256"]
        outside = value["out_of_scope_files"]
        review = bool(outside)
        verify = value["verification"]
        provenance = value["provenance"]
        if (set(value) != {"version", "status", "review_required", "scope_valid", "out_of_scope_files",
                           "changed_files", "allowed_paths", "candidate_file_sha256", "tracked_diff_sha256",
                           "source_sha", "effective_source_sha", "action_id", "attempt_id", "fingerprint",
                           "verification", "provenance"} or
                set(verify) != {"status", "exit_code", "profile", "argv", "evidence_sha256", "report_sha256"} or
                set(provenance) != {"executor", "model", "effort", "dependency_candidates", "run_id", "run_attempt"} or
                value["version"] != 1 or type(value["version"]) is not int or
                not isinstance(changed, list) or not changed or changed != sorted(set(changed)) or
                protected_change(changed) or not isinstance(hashes, dict) or set(hashes) != set(changed) or
                any(h is not None and (not isinstance(h, str) or not _HASH.fullmatch(h)) for h in hashes.values()) or
                not isinstance(value["allowed_paths"], list) or not value["allowed_paths"] or
                protected_change(value["allowed_paths"]) or
                outside != _outside_scope(changed, value["allowed_paths"]) or
                not ordinary_scope_drift(outside) or
                value["scope_valid"] is not (not review) or value["review_required"] is not review or
                value["status"] != (REVIEW_STATUS if review else "CANDIDATE_READY") or
                verify["status"] != VERIFIED_STATUSES[int(review)] or
                type(verify["exit_code"]) is not int or verify["exit_code"] != 0 or
                not isinstance(verify["argv"], list) or not verify["argv"] or
                not all(isinstance(a, str) for a in verify["argv"]) or
                not isinstance(verify["profile"], str) or not verify["profile"] or
                not isinstance(verify["evidence_sha256"], dict) or not verify["evidence_sha256"] or
                any(not _HASH.fullmatch(h) for h in verify["evidence_sha256"].values()) or
                verify["evidence_sha256"].get("tracked_diff") != value["tracked_diff_sha256"] or
                not _HASH.fullmatch(verify["report_sha256"]) or
                not _HASH.fullmatch(value["tracked_diff_sha256"]) or
                not _HASH.fullmatch(value["fingerprint"]) or
                not all(_SHA.fullmatch(value[k]) for k in ("source_sha", "effective_source_sha")) or
                not all(isinstance(value[k], str) and value[k] for k in ("attempt_id", "action_id")) or
                not all(isinstance(provenance[k], str) and provenance[k] for k in ("executor", "model", "effort")) or
                not isinstance(provenance["dependency_candidates"], list) or
                not all(_SHA.fullmatch(s) for s in provenance["dependency_candidates"]) or
                not all(provenance[k] is None or isinstance(provenance[k], str) for k in ("run_id", "run_attempt"))):
            raise ValueError("CANDIDATE_EVIDENCE_INVALID")
        # Also reject non-JSON/nonfinite values before any remote write.
        canonical(value)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("CANDIDATE_EVIDENCE_INVALID") from exc
    return value


def commit_evidence(message: str) -> dict | None:
    """Legacy candidates are readable; malformed new evidence never downgrades."""
    if EVIDENCE_MARKER not in message:
        if not re.fullmatch(r"EF02 candidate attempt [A-Za-z0-9][A-Za-z0-9_-]{0,127}", message):
            raise ValueError("CANDIDATE_COMMIT_UNPROVEN")
        return None
    prefix, payload = message.split(EVIDENCE_MARKER, 1)
    value = validate_candidate_evidence(json.loads(payload))
    if prefix != "EF02 candidate attempt " + value["attempt_id"] or payload != canonical(value):
        raise ValueError("CANDIDATE_COMMIT_EVIDENCE_MISMATCH")
    return value
