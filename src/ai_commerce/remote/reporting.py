"""One bounded GitHub Issue summary for an already completed EF02 batch.

Presentation only. No GitHub requests, runner actions, Wake or model calls.
The original batch_result.json remains authoritative for full diagnostics.
"""
from __future__ import annotations

import re
from .candidate_policy import candidate_accepted, has_scope_review

_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,159}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_UNKNOWN = frozenset({"EXECUTION_UNKNOWN", "CLAIM_UNAVAILABLE", "SOURCE_MISMATCH"})


def _token(value: object) -> str:
    """Allowlist output so result/exception values cannot inject Markdown."""
    return value if type(value) is str and _SAFE.fullmatch(value) else "UNPROVEN"


def _number(value: object) -> str:
    return str(value) if type(value) is int and value >= 0 else "UNPROVEN"


def _candidate_sha(value: object) -> str:
    return value if type(value) is str and _SHA.fullmatch(value) else "UNPROVEN"


def _next_step(action: dict) -> str:
    # Unknown effects outrank even a scope-review flag: never invite retry.
    if action.get("status") in _UNKNOWN:
        return "UNKNOWN_EFFECT_STOP_NO_BLIND_RETRY"
    if candidate_accepted(action):
        return "REVIEW_CANDIDATE_HUMAN_MERGE_REQUIRED"
    if has_scope_review(action):
        return "SCOPE_REVIEW_REQUIRED"
    if action.get("status") == "BLOCKED_DEPENDENCY":
        return "WAIT_FOR_DEPENDENCY_REPAIR"
    return "DIAGNOSE_FAILURE_BEFORE_NEW_ATTEMPT"


def format_batch_summary(report: dict, *, repository: str) -> str:
    """Format one deterministic, truthful Issue comment from scheduler evidence.

    Does NOT infer a combined-source PASS, receipt, hosted CI, merge, or Wake.
    Values from executors are always sanitized and never copied verbatim.
    """
    if type(report) is not dict or type(report.get("actions")) is not list:
        raise ValueError("INVALID_BATCH_REPORT")
    if type(repository) is not str or not _REPO.fullmatch(repository):
        raise ValueError("INVALID_REPOSITORY")
    run = _number(report.get("github_workflow_run_id"))
    # GitHub environment variables are strings, unlike typed JSON counts.
    if run == "UNPROVEN":
        raw_run = report.get("github_workflow_run_id")
        if type(raw_run) is str and 1 <= len(raw_run) <= 20 and raw_run.isascii() and raw_run.isdecimal():
            run = str(int(raw_run))
    attempt = _number(report.get("github_workflow_run_attempt"))
    if attempt == "UNPROVEN":
        raw_attempt = report.get("github_workflow_run_attempt")
        if type(raw_attempt) is str and 1 <= len(raw_attempt) <= 10 and raw_attempt.isascii() and raw_attempt.isdecimal():
            attempt = str(int(raw_attempt))
    run_url = (f"https://github.com/{repository}/actions/runs/{run}"
               if run != "UNPROVEN" else "UNPROVEN")
    source = _candidate_sha(report.get("source_sha"))
    lines = [
        "EF02_EXECUTION_SUMMARY_V1",
        f"Batch: `{_token(report.get('batch_id'))}` | "
        f"Status: `{_token(report.get('status'))}` | "
        f"Actions: {_number(report.get('succeeded'))}/{_number(report.get('total'))}",
        f"Source: `{source}` | Workflow: {run_url}",
        "Action results (artifact is authoritative):",
    ]
    needs_review = False
    has_unknown = False
    has_failure = False
    for item in report["actions"]:
        if type(item) is not dict:
            raise ValueError("INVALID_ACTION_RESULT")
        state = _token(item.get("status"))
        step = _next_step(item)
        needs_review |= step in ("REVIEW_CANDIDATE_HUMAN_MERGE_REQUIRED",
                                 "SCOPE_REVIEW_REQUIRED")
        has_unknown |= step == "UNKNOWN_EFFECT_STOP_NO_BLIND_RETRY"
        has_failure |= step in ("DIAGNOSE_FAILURE_BEFORE_NEW_ATTEMPT",
                                "WAIT_FOR_DEPENDENCY_REPAIR")
        parts = [
            f"- `{_token(item.get('action_id'))}` "
            f"(attempt `{_token(item.get('attempt_id'))}`): `{state}`",
            f"next=`{step}`",
        ]
        if item.get("candidate_ref"):
            parts.append(f"candidate=`{_token(item.get('candidate_ref'))}`")
        if item.get("candidate_sha"):
            parts.append(f"candidate_sha=`{_candidate_sha(item.get('candidate_sha'))}`")
        if item.get("error_code"):
            parts.append(f"error=`{_token(item.get('error_code'))}`")
        if type(item.get("verify_exit_code")) is int:
            parts.append(f"verify_exit={item['verify_exit_code']}")
        if type(item.get("repair_rounds_used")) is int:
            parts.append(f"repair_rounds={_number(item['repair_rounds_used'])}")
        if type(item.get("observed_pytest_workers")) is int:
            parts.append(f"observed_pytest_workers={_number(item['observed_pytest_workers'])}")
        mt = item.get("minimum_test")
        if type(mt) is dict:
            selected = mt.get("selected_groups")
            if type(selected) is list and selected:
                parts.append("MT01_groups=" + ",".join(_token(x) for x in selected[:12]))
            if type(mt.get("fallback")) is bool:
                parts.append("MT01_fallback=" + str(mt["fallback"]).lower())
        lines.append("; ".join(parts))
    agg = report.get("aggregate_verification")
    agg_state = _token(agg.get("state")) if type(agg) is dict else "UNPROVEN"
    lines.append("Combined-source verification: `" + agg_state +
                 "` (NOT inferred from per-action PASS).")
    if has_unknown:
        decision = "HOLD_UNKNOWN_NO_RETRY"
    elif has_failure:
        decision = "DIAGNOSE_FAILED_ACTIONS"
    elif needs_review:
        decision = "HUMAN_REVIEW_CANDIDATES"
    else:
        decision = "READ_ARTIFACT_BEFORE_DECISION"
    lines.append("Next: `" + decision +
                 "` — no automatic EF02 dispatch, merge or Wake.")
    artifact = (f"ef02-results-{run}-{attempt}"
                if run != "UNPROVEN" and attempt != "UNPROVEN" else "UNPROVEN")
    lines.append(f"Evidence: Actions artifact `{artifact}` (14-day retention); "
                 "batch_result.json and per-action files. No ChatGPT receipt claim.")
    return "\n".join(lines)
