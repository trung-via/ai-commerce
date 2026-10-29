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
    if report.get("kind") != "AI_COMMERCE_REMOTE_BATCH_RESULT" or report.get("version") != 1:
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
        if entry["status"] == "NOT_STARTED":
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
