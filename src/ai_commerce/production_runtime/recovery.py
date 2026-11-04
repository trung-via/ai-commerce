"""Read-only B0.2 recovery. Hash consistency is not private authority/custody.

A retained checkpoint detects some accidental rollback/truncation. An actor
replacing the database AND that anchor under the same SID is NOT defeated.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256

from .contracts import canonical_bytes
from .journal import (Checkpoint, Journal, JournalHold, OwnerRef, _binding,
                      _decode_event, _need)
from .state_machine import Event, EventKind, Journey, State, transition


@dataclass(frozen=True, slots=True)
class Recovery:
    state: str
    reason: str
    snapshot: Journey | None = None
    event_sequence: tuple[tuple[int, str, str], ...] = ()
    evidence_sha256: str | None = None
    owner_refs: tuple[OwnerRef, ...] = ()
    checkpoint: Checkpoint | None = None
    effect_key: str | None = None
    request_sha256: str | None = None
    semantic_sha256: str | None = None
    modeled_outcome: str | None = None
    modeled_evidence_type: str | None = None
    effect_status: str = "NONE"
    may_retry: bool = False
    provider_verified: bool = False
    anti_rollback_proof: str = "NOT_PROVEN"


def replay_verified(journal, history, journey_id, *, directive=None, expected_generation=None):
    """Internal: history must come from Journal.inspect in the current read txn.

    Original directive bytes are supplied separately and digest-pinned, never
    copied to SQLite. No persisted snapshot/state or authority flags are read.
    """
    records = tuple(r for r in history.records if r.journey_id == journey_id)
    _need(bool(records), "JOURNEY_MISSING")
    original = records[0].data()
    pins = {}
    intent, observation = None, None
    for record in records:
        data = record.data()
        for pin in data["owner_refs"]:
            pins[(pin["ledger"], pin["ref"])] = OwnerRef(**pin)
        if record.kind == "EFFECT_RESERVED":
            intent = data
        elif record.kind == "MODELED_OBSERVATION":
            observation = data
    owners = tuple(pins[k] for k in sorted(pins))
    result = Recovery(
        state="UNKNOWN_EFFECT" if intent else "HOLD", reason="OFFLINE_UNVERIFIED_EFFECT" if intent else "DIRECTIVE_REPLAY_UNAVAILABLE",
        event_sequence=tuple((r.sequence, r.event_id, r.sha256) for r in records),
        evidence_sha256=sha256(canonical_bytes({"owner_refs": [
            {"ledger": p.ledger, "ref": p.ref, "sha256": p.sha256} for p in owners],
            "payload_sha256": [r.payload_sha256 for r in records]})).hexdigest(),
        owner_refs=owners, checkpoint=history.checkpoint,
        effect_key=intent["effect_key"] if intent else None,
        request_sha256=intent["request_sha256"] if intent else None,
        semantic_sha256=intent["semantic_sha256"] if intent else None,
        modeled_outcome=observation["outcome"] if observation else None,
        modeled_evidence_type=observation["evidence_type"] if observation else None,
        effect_status="UNKNOWN" if intent else "NONE")
    if expected_generation is not None and (
            type(expected_generation) is not int or expected_generation != history.checkpoint.generation):
        return replace(result, state="HOLD", reason="STALE_FENCING_GENERATION")
    if directive is None:
        return result
    try:
        _need(_binding(directive, original["directive_ref"]) ==
              {k: v for k, v in original.items() if k != "owner_refs"}, "DIRECTIVE_PIN_MISMATCH")
        value = directive.to_dict()
        _need(value["journey_id"] == journey_id and value["source_sha"] == journal.source_sha,
              "DIRECTIVE_SOURCE_OR_JOURNEY_MISMATCH")
        snapshot = transition(Journey(journey_id, journal.source_sha),
            Event(records[0].event_id, EventKind.INPUT_ACCEPTED, journey_id, journal.source_sha, record=directive),
            at=records[0].at)
        for record in records[1:]:
            if record.kind == "TRANSITION":
                event = replace(_decode_event(record), source_sha=journal.source_sha)
                snapshot = transition(snapshot, event, at=record.at)
            elif record.kind == "EFFECT_RESERVED":
                _need(snapshot.state == State.WAIT_BRAIN, "OFFLINE_INTENT_STATE_MISMATCH")
        # The reducer stays WAIT_BRAIN. The coordination safety overlay blocks
        # all effects and completion; it never fabricates a RELEASE_REQUESTED
        # event or a hypothetical VerificationContext to skip private gates.
        return replace(result, state="UNKNOWN_EFFECT" if intent else snapshot.state.value,
                       reason="OFFLINE_UNVERIFIED_EFFECT" if intent else "VERIFIED_OFFLINE_REPLAY",
                       snapshot=snapshot)
    except (ValueError, TypeError, KeyError, AttributeError):
        return replace(result, state="HOLD", reason="SAFE_REDUCER_REPLAY_FAILED")


def recover(path: str, *, journal_id: str, source_sha: str, journey_id: str,
            directive=None, checkpoint=None, expected_generation=None):
    """Missing/corrupt/foreign journals HOLD; never silently create/reset one."""
    try:
        journal = Journal(path, journal_id, source_sha, checkpoint)
        history = journal.inspect()
        return replay_verified(journal, history, journey_id, directive=directive,
                               expected_generation=expected_generation)
    except (JournalHold, ValueError, TypeError):
        return Recovery("HOLD", "JOURNAL_VERIFICATION_FAILED")
