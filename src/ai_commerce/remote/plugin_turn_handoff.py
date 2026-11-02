"""D5: non-delivering, explicit Brain -> Engineering -> Brain handoff ledger.

A *public* packet/state machine only. No browser, network, GitHub mutation,
private chat routes, automatic Send, EF02 dispatch, or host invocation proof.
The trusted caller must durably persist every intent BEFORE any external Send;
SUBMISSION_UNKNOWN forbids automatic replay after ambiguous side effects.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

_REPO = "trung-via/ai-commerce"
_ID = re.compile(r"d5-turn-[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_STEPS = ("BRAIN", "ENGINEERING", "BRAIN")
_PHASES = frozenset({"READY", "SUBMISSION_UNKNOWN", "COMPLETE_UI_ONLY"})
_FIELDS = {"version", "handoff_id", "repository", "issue_number", "source_sha",
           "turn_index", "phase", "intent_fingerprint", "evidence"}
_OBS_FIELDS = {"turn_index", "issue_comment_ref", "observation"}


class TurnHandoffError(ValueError):
    """Fail with one fixed, non-sensitive diagnostic code."""


def _evidence_ref(value: object, issue_number: int) -> bool:
    return (isinstance(value, str) and re.fullmatch(
        rf"https://github\.com/trung-via/ai-commerce/issues/{issue_number}"
        r"#issuecomment-[1-9][0-9]{0,19}", value) is not None)


def _fingerprint(state: dict) -> str:
    """Deterministic idempotency fingerprint; not an authentication signature."""
    canonical = {k: state[k] for k in ("version", "handoff_id", "repository",
                                       "issue_number", "source_sha", "turn_index")}
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_session(session: object) -> dict:
    """Validate strictly; return a detached copy safe for pure transitions."""
    try:
        if (not isinstance(session, dict) or set(session) != _FIELDS or
                type(session["version"]) is not int or session["version"] != 1 or
                not isinstance(session["handoff_id"], str) or
                not _ID.fullmatch(session["handoff_id"]) or
                session["repository"] != _REPO or
                type(session["issue_number"]) is not int or session["issue_number"] < 1 or
                not isinstance(session["source_sha"], str) or
                not _SHA.fullmatch(session["source_sha"]) or
                type(session["turn_index"]) is not int or
                not 0 <= session["turn_index"] <= len(_STEPS) or
                session["phase"] not in _PHASES or
                not isinstance(session["evidence"], list) or
                len(session["evidence"]) != session["turn_index"]):
            raise ValueError
        for i, item in enumerate(session["evidence"]):
            if (not isinstance(item, dict) or set(item) != _OBS_FIELDS or
                    type(item["turn_index"]) is not int or item["turn_index"] != i or
                    item["observation"] != "OPERATOR_UI_RESPONSE_OBSERVED" or
                    not _evidence_ref(item["issue_comment_ref"], session["issue_number"])):
                raise ValueError
        if session["phase"] == "COMPLETE_UI_ONLY":
            if session["turn_index"] != len(_STEPS) or session["intent_fingerprint"] is not None:
                raise ValueError
        elif session["turn_index"] == len(_STEPS):
            raise ValueError
        elif session["phase"] == "READY":
            if session["intent_fingerprint"] is not None:
                raise ValueError
        elif (not isinstance(session["intent_fingerprint"], str) or
              session["intent_fingerprint"] != _fingerprint(session)):
            raise ValueError
        return deepcopy(session)
    except (KeyError, ValueError, TypeError, OverflowError, RecursionError):
        raise TurnHandoffError("TURN_SESSION_INVALID") from None


def new_session(*, handoff_id: str, issue_number: int, source_sha: str) -> dict:
    """Construct a public, non-authorizing plan. IDs are caller supplied."""
    return validate_session({
        "version": 1, "handoff_id": handoff_id, "repository": _REPO,
        "issue_number": issue_number, "source_sha": source_sha,
        "turn_index": 0, "phase": "READY", "intent_fingerprint": None,
        "evidence": [],
    })


def preview_next_turn(session: dict) -> dict:
    """Return only the next intended role and safe prompt; no literal @ chip."""
    state = validate_session(session)
    if state["phase"] != "READY":
        raise TurnHandoffError("TURN_NOT_READY")
    index = state["turn_index"]
    role = _STEPS[index]
    aliases = {"BRAIN": "AI Commerce Brain",
               "ENGINEERING": "AI Commerce Engineering Workflow"}
    jobs = (
        "Read current GitHub evidence; decide bounded Engineering review scope only.",
        "Read GitHub and review the approved issue scope only. No writes/dispatch.",
        "Read Engineering evidence; make final HOLD or READY_FOR_HUMAN decision.",
    )
    prompt = "\n".join((
        "[AI COMMERCE D5 EXPLICIT HANDOFF — PREVIEW / NOT SENT]",
        "handoff_id: " + state["handoff_id"],
        "repository: " + state["repository"],
        "issue_number: " + str(state["issue_number"]),
        "source_sha: " + state["source_sha"],
        "turn_index: " + str(index),
        "required_role: " + role,
        jobs[index],
        "Do not infer host invocation from this text or a blue plugin chip.",
        "Do not merge, dispatch EF02, create Wake, retry old events or Send a second turn.",
    ))
    return {"turn_index": index, "role": role,
            "plugin_alias_for_operator_selection": aliases[role],
            "preview_only": True, "host_selection_proven": False,
            "send_authorized": False, "prompt": prompt}


def record_send_intent(session: dict, *, operator_approved: bool = False) -> dict:
    """Pure write-ahead intent; only a separate authorized caller may Send.

    IMPORTANT: before any real browser side effect, its caller must persist the
    result transactionally. This function never sends or persists anything.
    """
    state = validate_session(session)
    if state["phase"] != "READY":
        raise TurnHandoffError("TURN_NOT_READY")
    if operator_approved is not True:
        raise TurnHandoffError("TURN_OPERATOR_APPROVAL_REQUIRED")
    state["phase"] = "SUBMISSION_UNKNOWN"
    state["intent_fingerprint"] = _fingerprint(state)
    return validate_session(state)


def reconcile_ui_observation(session: dict, *, issue_comment_ref: str,
                             operator_verified: bool = False) -> dict:
    """Record a Human-verified UI reply, NOT a ChatGPT server/plugin ACK.

    No retry path exists for unresolved SUBMISSION_UNKNOWN. Caller-supplied
    evidence reference is a locator, not cryptographically verified by here.
    """
    state = validate_session(session)
    if state["phase"] != "SUBMISSION_UNKNOWN":
        raise TurnHandoffError("TURN_RECONCILIATION_BLOCKED")
    if operator_verified is not True:
        raise TurnHandoffError("TURN_OPERATOR_APPROVAL_REQUIRED")
    if not _evidence_ref(issue_comment_ref, state["issue_number"]):
        raise TurnHandoffError("TURN_EVIDENCE_INVALID")
    index = state["turn_index"]
    state["evidence"].append({
        "turn_index": index, "issue_comment_ref": issue_comment_ref,
        "observation": "OPERATOR_UI_RESPONSE_OBSERVED",
    })
    state["turn_index"] += 1
    state["intent_fingerprint"] = None
    state["phase"] = ("COMPLETE_UI_ONLY" if state["turn_index"] == len(_STEPS)
                      else "READY")
    return validate_session(state)
