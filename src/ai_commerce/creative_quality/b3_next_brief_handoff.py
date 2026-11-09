"""B3.2: read-only, non-authoritative next-Brain-brief handoff.

A17 already owns proposal creation and Human adoption. This consumer only
replays native A09/A11/BQ06a/BQ06b and the genuinely stored pending A17 r1.
No proposal write, BrainDecision, strategy change, provider or PULSE effect.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import os

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import SnapshotPin
from ai_commerce.creative_quality import behavioral_learning_bridge as bq06a
from ai_commerce.creative_quality import behavioral_learning_fresh_replay as bq06b
from ai_commerce.creative_scaling.feedback import load_proposal, proposal_ref
from ai_commerce.creative_scaling.schemas import digest
from ai_commerce.operations import ArtifactRegistry

SCHEMA = "B3NextBriefHandoff.v1"
READY = "READY_FOR_ATTENDED_BRAIN_REVIEW"
HOLD = "INSUFFICIENT_EVIDENCE_HOLD"
MAX_PACKET_BYTES = 64 * 1024
REASONS = frozenset({
    "WINDOWS_REQUIRED", "INPUT_OR_PIN_INVALID",
    "SOURCE_EVIDENCE_NOT_CURRENT", "A11_REPLAY_FAILED",
    "A17_PENDING_SOURCE_NOT_REPLAYABLE", "A17_SOURCE_PIN_MISMATCH",
    "A17_COMPARISON_NOT_PROVEN", "COUNTEREVIDENCE_REQUIRED",
    "CROSS_OWNER_RECHECK_CHANGED",
})


class _Hold(ValueError):
    def __init__(self, reason: str):
        if reason not in REASONS:
            raise ValueError("UNREGISTERED_B3_HOLD_REASON")
        self.reason = reason
        super().__init__(reason)


def _require(value: bool, reason: str) -> None:
    if not value:
        raise _Hold(reason)


@dataclass(frozen=True, slots=True)
class B3NextBriefHandoff:
    """Canonical digest gives identity, not signer authority or a decision."""

    raw: bytes

    def __post_init__(self):
        if type(self.raw) is not bytes or not 0 < len(self.raw) < MAX_PACKET_BYTES:
            raise ValueError("B3_PACKET_SIZE_INVALID")
        value = json.loads(self.raw)
        if type(value) is not dict or canonical_bytes(value) != self.raw:
            raise ValueError("B3_PACKET_NOT_CANONICAL")
        body = {key: obj for key, obj in value.items() if key != "record_sha256"}
        if value.get("record_sha256") != sha256(canonical_bytes(body)).hexdigest():
            raise ValueError("B3_PACKET_DIGEST_INVALID")
        expected = {
            "schema_version", "status", "reason_codes", "source_reason_codes",
            "fresh_a09_a11_review_sha256", "a11_review_sha256", "proposal",
            "question_for_brain", "comparison", "source_kind",
            "evidence_scope", "provider_attested", "causal_lift", "roi",
            "human_adoption", "brain_decision", "writes_performed", "record_sha256",
        }
        if set(value) != expected or value["schema_version"] != SCHEMA:
            raise ValueError("B3_PACKET_FIELDS_INVALID")
        if value["provider_attested"] is not False or value["writes_performed"] is not False:
            raise ValueError("B3_PACKET_AUTHORITY_INVALID")
        if any(value[field] is not None for field in (
            "causal_lift", "roi", "human_adoption", "brain_decision",
        )):
            raise ValueError("B3_PACKET_AUTHORITY_INVALID")
        if value["status"] not in (READY, HOLD):
            raise ValueError("B3_PACKET_STATUS_INVALID")
        if value["status"] == READY:
            if value["reason_codes"] or value["proposal"] is None or value["comparison"] is None:
                raise ValueError("B3_PACKET_READY_INVALID")
            if value["proposal"]["adoption"] != "PENDING":
                raise ValueError("B3_PACKET_ADOPTION_INVALID")
        elif (value["proposal"] is not None or not value["reason_codes"]
              or value["comparison"] is not None):
            raise ValueError("B3_PACKET_HOLD_INVALID")
        if (value["reason_codes"] != sorted(set(value["reason_codes"]))
            or not set(value["reason_codes"]) <= REASONS):
            raise ValueError("B3_PACKET_REASON_INVALID")

    def to_dict(self) -> dict:
        return json.loads(self.raw)

    @property
    def digest(self) -> str:
        return sha256(self.raw).hexdigest()


def _seal(*, reason=None, source_reasons=(), fresh=None, a11=None,
          proposal=None, comparison=None, source_kind=None) -> B3NextBriefHandoff:
    if reason is not None and reason not in REASONS:
        raise ValueError("B3_UNREGISTERED_REASON")
    body = {
        "schema_version": SCHEMA,
        "status": READY if reason is None else HOLD,
        "reason_codes": [] if reason is None else [reason],
        "source_reason_codes": sorted(set(source_reasons)),
        "fresh_a09_a11_review_sha256": None if fresh is None else fresh.digest,
        "a11_review_sha256": None if a11 is None else a11.digest,
        "proposal": proposal if reason is None else None,
        "question_for_brain": "Review or revise this evidence-linked next creative hypothesis; no auto-adoption.",
        "comparison": comparison if reason is None else None,
        "source_kind": source_kind,
        "evidence_scope": "LOCAL_REPLAY_NON_ATOMIC_AND_NON_AUTHORITATIVE",
        "provider_attested": False, "causal_lift": None, "roi": None,
        "human_adoption": None, "brain_decision": None, "writes_performed": False,
    }
    raw = canonical_bytes({**body, "record_sha256": sha256(canonical_bytes(body)).hexdigest()})
    return B3NextBriefHandoff(raw)


def _read_pending_a17(options: dict, a11: dict, source_args: dict) -> tuple[dict, dict]:
    _require(type(options) is dict and set(options) == {
        "registry_root", "batch_id", "source_id", "proposal_id",
        "expected_proposal_sha256", "opt_in",
    } and options["opt_in"] is True, "A17_PENDING_SOURCE_NOT_REPLAYABLE")
    # The native BQ06a reader already verifies this exact A17 source tree.
    # Hold its original Windows frozen directory and files during this replay.
    with bq06a._frozen(trees=[options["registry_root"]]):
        registry = ArtifactRegistry(options["registry_root"])
        proposal = load_proposal(registry, options["proposal_id"])
        _require(proposal["record_sha256"] == options["expected_proposal_sha256"]
                 and proposal["record_sha256"] == a11["a17"]["proposal_sha256"],
                 "A17_SOURCE_PIN_MISMATCH")
        _require(proposal["revision"] == 1 and proposal["parent"] is None
                 and proposal["adoption"] == {"decision": "PENDING", "actor": None, "note": None}
                 and not registry.exists(proposal_ref(options["proposal_id"], 2)),
                 "A17_PENDING_SOURCE_NOT_REPLAYABLE")
        _require(proposal["batch_id"] == options["batch_id"]
                 and proposal["source"]["id"] == options["source_id"]
                 and proposal["advisory"]["export_id"] == source_args["expected_export_id"]
                 and proposal["advisory"]["learning_key"] == source_args["learning_key"]
                 and proposal["advisory"]["revision"] == source_args["expected_revision"]
                 and proposal["scope"]["cohort"] == source_args["expected_context"]["cohort"]
                 and proposal["comparison"]["metric"] == source_args["expected_metric"],
                 "A17_SOURCE_PIN_MISMATCH")
        comparison = proposal["comparison"]
        _require(comparison["before_window"] == source_args["expected_context"]["measurement_window"]
                 and comparison["timezone"] == source_args["expected_context"]["timezone"]
                 and comparison["cohort"] == source_args["expected_context"]["cohort"]
                 and comparison["after"] == {"status": "UNKNOWN", "value": None, "denominator": None}
                 and comparison["causal_lift"] is None
                 and comparison["trend"] == "NOT_ESTABLISHED"
                 and "AFTER_WINDOW_NOT_OBSERVED" in proposal["missing_data"],
                 "A17_COMPARISON_NOT_PROVEN")
        _require(proposal["negative_evidence_ids"]
                 and set(proposal["negative_evidence_ids"]) <= set(proposal["evidence_ids"]),
                 "COUNTEREVIDENCE_REQUIRED")
        _require(proposal["generation_performed"] is False
                 and proposal["publication_status"] == "NOT_ATTEMPTED"
                 and proposal["inference"] == "HYPOTHESIS_ONLY"
                 and proposal["generation_authorization"] == "REQUIRED_SEPARATELY",
                 "A17_PENDING_SOURCE_NOT_REPLAYABLE")

        # Preserve A17's genuine private brief suggestion only in the local
        # returned packet. Never persist, auto-approve, send or post it here.
        result = {
            "id": proposal["proposal_id"], "revision": 1,
            "record_sha256": proposal["record_sha256"],
            "next_brief": deepcopy(proposal["next_brief"]),
            "suggestion": proposal["suggestion"],
            "hypothesis": proposal["hypothesis"],
            "uncertainty": deepcopy(proposal["uncertainty"]),
            "evidence_ids": list(proposal["evidence_ids"]),
            "counterevidence_ids": list(proposal["negative_evidence_ids"]),
            "missing_data": list(proposal["missing_data"]),
            "adoption": "PENDING", "proposal_status": "HYPOTHESIS_ONLY",
        }
        before = comparison["before"]
        context = {
            "before_window": comparison["before_window"],
            "after_window": comparison["after_window"],
            "after_status": "UNKNOWN",
            "metric_sha256": digest(comparison["metric"]),
            "cohort_sha256": digest(comparison["cohort"]),
            "timezone": comparison["timezone"],
            "before_available_zero": sum(
                item["status"] == "AVAILABLE" and item["value"] == 0 for item in before),
            "before_unknown": sum(item["status"] == "UNKNOWN" for item in before),
            "denominator_unknown": any(item["denominator"] is None for item in before),
            "provider_metrics_verified": False, "comparison_verified": False,
        }
    return result, context


def review_next_brief(*, ledger_path: str, pin: SnapshotPin,
                      bq06a_raw: bytes, source_arguments: dict) -> B3NextBriefHandoff:
    """Revalidate native source owners and prepare an attended Brain review, never a decision.

    A09 and A11 live in different SQLite files; a byte replay is not a distributed
    transaction. This packet is an offline snapshot for a Human/Brain to review.
    """
    if os.name != "nt":
        return _seal(reason="WINDOWS_REQUIRED")
    fresh = reviewed = None
    try:
        _require(type(ledger_path) is str and type(pin) is SnapshotPin
                 and type(bq06a_raw) is bytes and type(source_arguments) is dict,
                 "INPUT_OR_PIN_INVALID")
        args = deepcopy(source_arguments)
        fresh = bq06b.review_fresh_evidence(
            ledger_path=ledger_path, pin=pin, bq06a_raw=bq06a_raw,
            source_arguments=args)
        fv = fresh.to_dict()
        _require(fv["status"] == bq06b.CURRENT, "SOURCE_EVIDENCE_NOT_CURRENT")
        reviewed = bq06a.recheck_review(bq06a_raw, **args)
        rv = reviewed.to_dict()
        _require(rv["status"] == "EVIDENCE_REVIEW_FOR_BRAIN"
                 and rv["a17"] is not None and rv["a17"]["adoption"] == "PENDING",
                 "A11_REPLAY_FAILED")
        proposal, comparison = _read_pending_a17(args.get("a17"), rv, args)
        again = bq06b.review_fresh_evidence(
            ledger_path=ledger_path, pin=pin, bq06a_raw=bq06a_raw,
            source_arguments=args)
        replayed = bq06a.recheck_review(bq06a_raw, **args)
        _require(again.raw == fresh.raw and replayed.raw == reviewed.raw,
                 "CROSS_OWNER_RECHECK_CHANGED")
        return _seal(fresh=fresh, a11=reviewed, proposal=proposal,
                     comparison=comparison, source_kind=fv["source_kind"])
    except _Hold as exc:
        return _seal(reason=exc.reason,
                     source_reasons=() if fresh is None else fresh.to_dict()["reason_codes"],
                     fresh=fresh, a11=reviewed,
                     source_kind=None if fresh is None else fresh.to_dict()["source_kind"])
    except Exception:
        # Do not expose private owner paths, payloads, OS diagnostics or secrets.
        return _seal(reason="A11_REPLAY_FAILED", fresh=fresh, a11=reviewed)
