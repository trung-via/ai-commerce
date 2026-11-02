"""Bounded read-only GitHub evidence verification for D5 explicit plugin turns.

Verifies that a comment EXISTS as an immutable-looking, exact-schema evidence
packet authored by one configured GitHub user and tied to one pinned source SHA.
Does NOT authenticate ChatGPT UI, plugin invocation, or server Send receipt.
A separate Human UI confirmation remains mandatory for ledger reconciliation.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .conversation_binding import _decode_unique
from .github_artifact_authority import GitHubRestArtifactAuthority
from .plugin_turn_handoff import _evidence_ref, _STEPS, validate_session

_REPO_API = "https://api.github.com/repos/trung-via/ai-commerce"
_REPO = "trung-via/ai-commerce"
_COMMENT_ID = re.compile(
    r"https://github\.com/trung-via/ai-commerce/issues/[1-9][0-9]*"
    r"#issuecomment-([1-9][0-9]{0,19})\Z"
)
_SCHEMA = "ai-commerce-d5-turn-observation-v1"
_OBSERVATION = "OPERATOR_UI_RESPONSE_OBSERVED"
_FIELDS = frozenset({
    "schema", "repository", "issue_number", "handoff_id",
    "turn_index", "role", "source_sha", "observation",
    "host_selection_proven",
})


class GitHubTurnEvidenceAuthority:
    """Non-writing GitHub REST reader with pinned author and source branch.

    The token provider is trusted local configuration. Tests may inject a
    reader; production uses D3's bounded GitHub REST read routine. The GitHub
    comment body is DATA, never an instruction or authorization to Send.
    """

    def __init__(self, *, token_provider: Callable[[], str],
                 expected_author_id: int,
                 read_json: Callable[[str, str], dict] | None = None):
        if (not callable(token_provider) or
                type(expected_author_id) is not int or expected_author_id < 1 or
                (read_json is not None and not callable(read_json))):
            raise ValueError("TURN_EVIDENCE_CONFIG_INVALID")
        self._token_provider = token_provider
        self._author_id = expected_author_id
        self._read_json = read_json or GitHubRestArtifactAuthority._read_json

    def verify(self, session: dict, issue_comment_ref: str) -> bool:
        """Return True only for exact matching GitHub bytes and current main.

        True means the trusted GitHub API reported a matching comment/author,
        NOT ChatGPT host/assistant/plugin acceptance. Fail closed without
        returning bearer token, comment body or a private local path.
        """
        try:
            state = validate_session(session)
            if state["phase"] != "SUBMISSION_UNKNOWN":
                return False
            if not _evidence_ref(issue_comment_ref, state["issue_number"]):
                return False
            match = _COMMENT_ID.fullmatch(issue_comment_ref)
            if match is None:
                return False
            comment_id = int(match.group(1))
            token = self._token_provider()
            if (not isinstance(token, str) or not 1 <= len(token) <= 4096 or
                    any(char in token for char in "\r\n\x00")):
                return False
            comment = self._read_json(
                _REPO_API + "/issues/comments/" + str(comment_id), token)
            if (not isinstance(comment, dict) or
                    type(comment.get("id")) is not int or comment["id"] != comment_id or
                    comment.get("html_url") != issue_comment_ref or
                    comment.get("issue_url") != (
                        _REPO_API + "/issues/" + str(state["issue_number"])) or
                    not isinstance(comment.get("user"), dict) or
                    type(comment["user"].get("id")) is not int or
                    comment["user"]["id"] != self._author_id or
                    not isinstance(comment.get("created_at"), str) or
                    comment.get("updated_at") != comment["created_at"]):
                return False
            body = comment.get("body")
            if not isinstance(body, str) or not 1 <= len(body.encode("utf-8")) <= 4096:
                return False
            packet = _decode_unique(body)
            if not isinstance(packet, dict) or set(packet) != _FIELDS:
                return False
            index = state["turn_index"]
            if not (packet["schema"] == _SCHEMA and
                    packet["repository"] == _REPO and
                    type(packet["issue_number"]) is int and
                    packet["issue_number"] == state["issue_number"] and
                    packet["handoff_id"] == state["handoff_id"] and
                    type(packet["turn_index"]) is int and packet["turn_index"] == index and
                    packet["role"] == _STEPS[index] and
                    packet["source_sha"] == state["source_sha"] and
                    packet["observation"] == _OBSERVATION and
                    packet["host_selection_proven"] is False):
                return False
            # The source remains current. A stale source never auto-advances.
            branch = self._read_json(_REPO_API + "/branches/main", token)
            return (isinstance(branch, dict) and branch.get("name") == "main" and
                    isinstance(branch.get("commit"), dict) and
                    branch["commit"].get("sha") == state["source_sha"])
        except Exception:
            # No token, raw body, exception chain, URL or local data escapes.
            return False


def expected_observation_packet(session: dict) -> dict:
    """A public template to prepare for Human review; never publish/send it."""
    state = validate_session(session)
    if state["phase"] != "SUBMISSION_UNKNOWN":
        raise ValueError("TURN_EVIDENCE_NOT_PENDING")
    return {
        "schema": _SCHEMA, "repository": _REPO,
        "issue_number": state["issue_number"],
        "handoff_id": state["handoff_id"],
        "turn_index": state["turn_index"],
        "role": _STEPS[state["turn_index"]],
        "source_sha": state["source_sha"],
        "observation": _OBSERVATION,
        "host_selection_proven": False,
    }
