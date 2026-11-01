"""Non-delivering preview for a two-plugin EF02 Wake in the registered test chat.

Textual ``@`` markers are NOT ChatGPT host-selected plugin chips. This helper
never controls a browser, dispatches GitHub Actions or asserts plugin selection.
"""
from __future__ import annotations

import argparse
import re

_REPOSITORY = "trung-via/ai-commerce"
_EVENT = re.compile(r"ef02-completion-v1:[0-9a-f]{64}\Z")
_PLUGIN_LINE = "@AI Commerce Brain @AI Commerce Engineering Workflow"


def build_dual_plugin_wake_preview(event_id: str, repository: str = _REPOSITORY) -> str:
    """Make a bounded human-reviewable prompt, not an authenticated host mention."""
    if (not isinstance(event_id, str) or not _EVENT.fullmatch(event_id)
            or repository != _REPOSITORY):
        raise ValueError("DUAL_PLUGIN_PREVIEW_INPUT_INVALID")
    return "\n".join((
        "[AI COMMERCE DUAL-PLUGIN WAKE PREVIEW — NOT SENT]",
        "event_id: " + event_id,
        "repository: " + repository,
        "fresh_brain_sync_required: true",
        _PLUGIN_LINE,
        "AI Commerce Brain: Brain Sync from current main and this fresh event; "
        "use Brain-native research only if needed, and produce an evidence-backed handoff.",
        "AI Commerce Engineering Workflow: independently review the bounded "
        "handoff using available GitHub evidence; execute only separately "
        "authorized work, and report actual evidence.",
        "AI Commerce Brain: reconcile Engineering evidence and report the "
        "decision and unresolved gaps in this same response.",
        "Do not dispatch EF02, merge, send another Wake, retry an UNKNOWN event, "
        "or use a separate Deep Research task / MCP connection by default.",
    ))



def build_engineering_failure_wake_preview(event: dict) -> str:
    """Preview Engineering-only triage for an EF02 partial/failed event.

    Event identity is self-consistent, NOT authenticated provenance. D3 must
    independently check real GitHub evidence and operator consent before Send.
    No Brain role is requested for routine implementation failures.
    """
    from .candidate_policy import candidate_accepted
    from .completion_event import CompletionEventError, validate_completion_event

    try:
        checked = validate_completion_event(event)
    except (CompletionEventError, TypeError, KeyError, ValueError) as exc:
        raise ValueError("ENGINEERING_FAILURE_PREVIEW_EVENT_INVALID") from exc
    if checked["repository"] != _REPOSITORY or checked["batch_status"] != "PARTIAL_OR_FAILED":
        raise ValueError("ENGINEERING_FAILURE_PREVIEW_NOT_FAILED")
    failed = [action for action in checked["actions"] if not candidate_accepted(action)]
    if not failed:
        raise ValueError("ENGINEERING_FAILURE_PREVIEW_NOT_FAILED")
    rows = [
        "[AI COMMERCE ENGINEERING-ONLY FAILURE WAKE PREVIEW — NOT SENT]",
        "event_id: " + checked["event_id"],
        "repository: " + checked["repository"],
        "run_id: " + str(checked["run_id"]),
        "run_attempt: " + str(checked["run_attempt"]),
        "issue_number: " + str(checked["issue_number"]),
        "batch_id: " + checked["batch_id"],
        "source_sha: " + checked["source_sha"],
        "batch_status: PARTIAL_OR_FAILED",
        "fresh_brain_sync_required: false",
        "@AI Commerce Engineering Workflow",
    ]
    for action in failed:
        rows.append(
            "failed_action: " + action["action_id"] +
            " attempt=" + action["attempt_id"] +
            " status=" + action["status"]
        )
    rows.extend((
        "Engineering only: verify authenticated GitHub run/artifact and these "
        "exact action/attempt statuses; diagnose root cause, scope and tests.",
        "Apply Verify-Fix only if existing explicit authorization and the "
        "registered EF02 contract permit it; otherwise report a bounded "
        "repair plan and request authorization.",
        "Do not call AI Commerce Brain for routine engineering failure triage. "
        "Escalate to Brain only when a new roadmap/architecture/product decision "
        "is genuinely necessary.",
        "No automatic EF02 retry/dispatch, no main merge, no unknown-side-effect "
        "replay, no new Wake, no Deep Research app or plugin MCP connection.",
        "This is plain text only; @Engineering is NOT a host-selected plugin "
        "until the actual ChatGPT composer selection is verified.",
    ))
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a dual-@ test-chat Wake PREVIEW; never send it.")
    parser.add_argument("--event-id", required=True,
                        help="Fresh independently authorized EF02 completion event only")
    args = parser.parse_args(argv)
    try:
        print(build_dual_plugin_wake_preview(args.event_id))
    except ValueError as exc:
        parser.error(str(exc))
    print("\n[PREVIEW_ONLY: select BOTH installed plugins via ChatGPT @ picker "
          "in the registered test conversation. Literal @ text is not proof "
          "of real plugin selection. No browser Send was attempted.]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
