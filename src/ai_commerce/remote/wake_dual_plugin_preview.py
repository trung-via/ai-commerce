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
