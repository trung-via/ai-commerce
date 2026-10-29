"""EF02-D D1: immutable, selector-only completion evidence. No ChatGPT transport."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SAFE_STATUS = re.compile(r"[A-Z][A-Z0-9_]{0,95}\Z")


# Explicit final EF02 outcomes. A new unknown status requires review before
# it becomes eligible for a wake. The event remains optional if unsupported.
FINAL_ACTION_STATUSES = frozenset({
    "CANDIDATE_READY", "INVALID_ACTION", "UNPROVEN", "REUSE_VERIFICATION_FAILED",
    "DUPLICATE_CLAIM_NO_REEXECUTION", "ATTEMPT_ID_CONFLICT",
    "CLAIM_UNAVAILABLE", "INTEGRATION_FAILED", "SOURCE_WORKTREE_FAILED",
    "PRESPAWN_CHECKPOINT_UNCONFIRMED", "EXECUTION_UNKNOWN",
    "VERIFICATION_FAILED", "EXECUTOR_FAILED", "MODEL_AT_CAPACITY",
    "QUOTA_EXHAUSTED", "MODEL_UNSUPPORTED", "SOURCE_MISMATCH",
    "CANDIDATE_INTEGRITY_FAILED", "RESULT_PUBLICATION_FAILED",
    "BLOCKED_DEPENDENCY",
})

class CompletionEventError(ValueError):
    """Untrusted, unfinished, or unidentifiable EF02 result."""


def _require(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CompletionEventError("INVALID_" + label)
    return value


def _positive(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise CompletionEventError("INVALID_" + label)
    return value


def build_completion_event(report: Mapping, *, repository: str,
                           run_id: int, run_attempt: int, issue_number: int | None) -> dict:
    """Project one final EF02 batch report into a non-delivering wake candidate.

    Only allowlisted, bounded identities may leave this boundary. A GitHub Issue
    comment, artifact upload, or this file cannot prove actual ChatGPT delivery.
    """
    repository = _require(repository, _REPO, "REPOSITORY")
    run_id = _positive(run_id, "RUN_ID")
    run_attempt = _positive(run_attempt, "RUN_ATTEMPT")
    if issue_number is not None:
        issue_number = _positive(issue_number, "ISSUE_NUMBER")
    if report.get("kind") != "AI_COMMERCE_REMOTE_BATCH_RESULT" or type(report.get("version")) is not int or report.get("version") != 1:
        raise CompletionEventError("INVALID_REPORT_KIND")
    batch_id = _require(report.get("batch_id"), _ID, "BATCH_ID")
    source_sha = _require(report.get("source_sha"), _SHA, "SOURCE_SHA")
    if report.get("status") not in ("COMPLETE", "PARTIAL_OR_FAILED"):
        raise CompletionEventError("REPORT_NOT_FINAL")
    actions = report.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 128:
        raise CompletionEventError("INVALID_ACTIONS")
    projected = []
    for action in actions:
        if not isinstance(action, Mapping):
            raise CompletionEventError("INVALID_ACTION")
        entry = {
            "action_id": _require(action.get("action_id"), _ID, "ACTION_ID"),
            "attempt_id": _require(action.get("attempt_id"), _ID, "ATTEMPT_ID"),
            "status": _require(action.get("status"), _SAFE_STATUS, "ACTION_STATUS"),
        }
        if entry["status"] not in FINAL_ACTION_STATUSES:
            raise CompletionEventError("ACTION_NOT_FINAL")
        candidate_sha = action.get("candidate_sha")
        if entry["status"] == "CANDIDATE_READY" and candidate_sha is None:
            raise CompletionEventError("CANDIDATE_UNPROVEN")
        if candidate_sha is not None:
            entry["candidate_sha"] = _require(candidate_sha, _SHA, "CANDIDATE_SHA")
        projected.append(entry)
    if len({a["action_id"] for a in projected}) != len(projected) or len({a["attempt_id"] for a in projected}) != len(projected):
        raise CompletionEventError("DUPLICATE_ACTION_OR_ATTEMPT")
    if type(report.get("total")) is not int or report["total"] != len(projected):
        raise CompletionEventError("TOTAL_MISMATCH")
    succeeded = sum(a["status"] == "CANDIDATE_READY" for a in projected)
    if (type(report.get("succeeded")) is not int or report["succeeded"] != succeeded or
            report["status"] != ("COMPLETE" if succeeded == len(projected) else "PARTIAL_OR_FAILED")):
        raise CompletionEventError("AGGREGATE_MISMATCH")
    immutable = {
        "repository": repository,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "issue_number": issue_number,
        "batch_id": batch_id,
        "source_sha": source_sha,
        "batch_status": report["status"],
        "actions": projected,
    }
    digest = hashlib.sha256(json.dumps(immutable, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "kind": "AI_COMMERCE_EF02_COMPLETION_EVENT", "version": 1,
        "event_id": "ef02-completion-v1:" + digest,
        **immutable,
        "origin_binding_status": "UNBOUND",
        "delivery_status": "NOT_ATTEMPTED",
        "chat_delivery_proven": False,
    }

def validate_completion_event(event: Mapping) -> dict:
    """Verify event finality and exact shape, NOT trusted provenance.

    The public SHA-256 event ID is not a MAC. D2 must independently authenticate
    originating GitHub evidence and a consented route before any delivery.
    """
    if not isinstance(event, Mapping) or set(event) != {
        "kind", "version", "event_id", "repository", "run_id", "run_attempt",
        "issue_number", "batch_id", "source_sha", "batch_status", "actions",
        "origin_binding_status", "delivery_status", "chat_delivery_proven",
    }:
        raise CompletionEventError("EVENT_SHAPE_INVALID")
    if (event["kind"] != "AI_COMMERCE_EF02_COMPLETION_EVENT" or
            type(event["version"]) is not int or event["version"] != 1 or
            event["chat_delivery_proven"] is not False):
        # The producer's unproven-delivery assertion is immutable identity.
        # Do not treat a forged delivery proof as a merely ineligible event.
        raise CompletionEventError("EVENT_IDENTITY_INVALID")
    if (event["origin_binding_status"] != "UNBOUND" or
            event["delivery_status"] != "NOT_ATTEMPTED"):
        raise CompletionEventError("EVENT_NOT_ELIGIBLE")
    actions = event["actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 128:
        raise CompletionEventError("INVALID_ACTIONS")
    for action in actions:
        if (not isinstance(action, Mapping) or
                set(action) not in ({"action_id", "attempt_id", "status"},
                                    {"action_id", "attempt_id", "status", "candidate_sha"})):
            raise CompletionEventError("EVENT_ACTION_SHAPE_INVALID")
    report = {
        "kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
        "batch_id": event["batch_id"], "source_sha": event["source_sha"],
        "status": event["batch_status"], "actions": actions,
        "total": len(actions),
        "succeeded": sum(action.get("status") == "CANDIDATE_READY" for action in actions),
    }
    try:
        expected = build_completion_event(
            report, repository=event["repository"], run_id=event["run_id"],
            run_attempt=event["run_attempt"], issue_number=event["issue_number"])
    except (TypeError, ValueError, KeyError) as exc:
        raise CompletionEventError("EVENT_IDENTITY_INVALID") from exc
    if dict(event) != expected:
        raise CompletionEventError("EVENT_IDENTITY_INVALID")
    return expected
