"""B3.3: native B3.2 and A18 M06, read-only comparative context, not a join.

B3.2 owns A09/A11/A17 freshness. A18 owns all native portfolio facts and
currency grouping. This bridge cannot authenticate a common campaign or grant
strategy, spend, generation, provider or PULSE authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.creative_quality.b3_next_brief_handoff import (
    READY as B3_READY, review_next_brief,
)
from ai_commerce.opportunity.portfolio import EvidenceBundle, recommend_portfolio
from ai_commerce.opportunity.portfolio.schemas import digest

SCHEMA = "B3PortfolioReview.v1"
ADVISORY = "CONTEXT_COMPATIBLE_JOIN_UNVERIFIED"
HOLD = "INSUFFICIENT_EVIDENCE_HOLD"
REASONS = frozenset({
    "WINDOWS_REQUIRED", "INVALID_INPUT", "B3_SOURCE_HOLD",
    "A18_NATIVE_EVALUATION_FAILED", "INCOMPARABLE_CONTEXT",
    "CROSS_SOURCE_JOIN_UNVERIFIED",
})
BOUNDARY = {
    "source_join": "UNVERIFIED",
    "provider_attested": False,
    "causal_lift": None,
    "roi": None,
    "human_adoption": None,
    "brain_decision": None,
    "financial_commitment": "NONE",
    "execution_authority": "NONE",
    "writes_performed": False,
}
FIELDS = frozenset({
    "schema_version", "status", "reason_codes", "b3_handoff_sha256",
    "a18_recommendation_id", "a18_bytes_sha256", "candidate_id",
    "next_brief_sha256", "comparable_dimensions", "b3_source_kind",
    "b3_counterevidence_count", "a18_evidence_quality", "a18_source_count",
    "a18_counterevidence_count", "a18_reason_codes", "a18_missingness",
    "a18_local_advisory", "a18_cost_buckets", "authority", "record_sha256",
})


@dataclass(frozen=True, slots=True)
class B3PortfolioReview:
    raw: bytes

    def __post_init__(self):
        if type(self.raw) is not bytes or not 0 < len(self.raw) <= 65536:
            raise ValueError("B3_3_PACKET_SIZE_INVALID")
        try:
            value = json.loads(self.raw)
            if type(value) is not dict or canonical_bytes(value) != self.raw:
                raise ValueError("B3_3_PACKET_NOT_CANONICAL")
            body = {k: v for k, v in value.items() if k != "record_sha256"}
            if value.get("record_sha256") != sha256(canonical_bytes(body)).hexdigest():
                raise ValueError("B3_3_PACKET_DIGEST_INVALID")
            if set(value) != FIELDS or value["schema_version"] != SCHEMA:
                raise ValueError("B3_3_PACKET_FIELDS_INVALID")
            if value["authority"] != BOUNDARY or value["status"] not in {ADVISORY, HOLD}:
                raise ValueError("B3_3_PACKET_AUTHORITY_INVALID")
            if value["reason_codes"] != sorted(set(value["reason_codes"])) or not set(value["reason_codes"]) <= REASONS:
                raise ValueError("B3_3_PACKET_REASON_INVALID")
            if value["a18_local_advisory"] not in {None, "INCONCLUSIVE", "KEEP", "CHANGE", "STOP"}:
                raise ValueError("B3_3_PACKET_ADVISORY_INVALID")
            if value["status"] == ADVISORY:
                if value["reason_codes"] != ["CROSS_SOURCE_JOIN_UNVERIFIED"]:
                    raise ValueError("B3_3_PACKET_JOIN_INVALID")
                if value["a18_recommendation_id"] is None or value["b3_handoff_sha256"] is None:
                    raise ValueError("B3_3_PACKET_NATIVE_SOURCE_MISSING")
                if value["comparable_dimensions"] != {
                    "cohort": True, "metric": True, "timezone": True, "window": True,
                }:
                    raise ValueError("B3_3_PACKET_COMPARABILITY_INVALID")
            elif value["next_brief_sha256"] is not None:
                raise ValueError("B3_3_PACKET_HOLD_LEAKS_BRIEF")
        except (TypeError, KeyError, AttributeError, json.JSONDecodeError):
            raise ValueError("B3_3_PACKET_INVALID") from None

    def to_dict(self) -> dict:
        return json.loads(self.raw)

    @property
    def digest(self) -> str:
        return sha256(self.raw).hexdigest()


def _seal(*, reason=None, b3=None, a18=None, compatible=None):
    if reason is not None and reason not in REASONS:
        raise ValueError("B3_3_UNREGISTERED_HOLD")
    ready = reason is None
    proposal = (b3["proposal"] if ready else None)
    source = a18["request"] if a18 else None
    body = {
        "schema_version": SCHEMA,
        "status": ADVISORY if ready else HOLD,
        "reason_codes": ["CROSS_SOURCE_JOIN_UNVERIFIED"] if ready else [reason],
        "b3_handoff_sha256": None if b3 is None else b3["record_sha256"],
        "a18_recommendation_id": None if a18 is None else a18["recommendation_id"],
        "a18_bytes_sha256": None if a18 is None else sha256(canonical_bytes(a18)).hexdigest(),
        "candidate_id": None if source is None else source["candidate_id"],
        "next_brief_sha256": None if proposal is None else proposal["next_brief"]["sha256"],
        "comparable_dimensions": compatible,
        "b3_source_kind": None if b3 is None else b3["source_kind"],
        "b3_counterevidence_count": None if proposal is None else len(proposal["counterevidence_ids"]),
        "a18_evidence_quality": None if a18 is None else a18["evidence_quality"],
        "a18_source_count": None if a18 is None else len(a18["sources"]),
        "a18_counterevidence_count": None if a18 is None else len(a18["counterevidence"]),
        "a18_reason_codes": [] if a18 is None else list(a18["reason_codes"]),
        "a18_missingness": [] if a18 is None else list(a18["missingness"]),
        "a18_local_advisory": None if a18 is None else a18["decision"],
        "a18_cost_buckets": [] if a18 is None else a18["cost_totals"],
        "authority": dict(BOUNDARY),
    }
    raw = canonical_bytes({**body, "record_sha256": sha256(canonical_bytes(body)).hexdigest()})
    return B3PortfolioReview(raw)


def review_b3_portfolio(*, ledger_path, pin, bq06a_raw, source_arguments,
                        evaluation, evidence=None) -> B3PortfolioReview:
    """Run BOTH native owners afresh. Matched dimensions are NOT a native ID join.

    No retained A18 recommendation is accepted as proof of current custody.
    A09, A11 and A18 are different stores: this is NOT an atomic snapshot.
    """
    if os.name != "nt":
        return _seal(reason="WINDOWS_REQUIRED")
    if (type(ledger_path) is not str or type(bq06a_raw) is not bytes
            or type(source_arguments) is not dict or type(evaluation) is not dict
            or evidence is not None and type(evidence) is not EvidenceBundle):
        return _seal(reason="INVALID_INPUT")
    b3_packet = review_next_brief(
        ledger_path=ledger_path, pin=pin,
        bq06a_raw=bq06a_raw, source_arguments=source_arguments)
    b3 = b3_packet.to_dict()
    if b3["status"] != B3_READY:
        return _seal(reason="B3_SOURCE_HOLD", b3=b3)
    try:
        a18 = recommend_portfolio(
            evaluation, EvidenceBundle() if evidence is None else evidence).to_dict()
    except Exception:
        # Native owner validates source pins. Never echo paths or private payloads.
        return _seal(reason="A18_NATIVE_EVALUATION_FAILED", b3=b3)
    request = a18["request"]
    scope = request["scope"]
    context = b3["comparison"]
    comparable = {
        "metric": context["metric_sha256"] == digest(request["metric"]),
        "cohort": scope["cohort"] is not None and
                  context["cohort_sha256"] == digest(scope["cohort"]),
        "window": context["before_window"] == request["measurement_window"],
        "timezone": context["timezone"] == scope["timezone"],
    }
    if not all(comparable.values()):
        return _seal(reason="INCOMPARABLE_CONTEXT", b3=b3, a18=a18,
                     compatible=comparable)
    return _seal(b3=b3, a18=a18, compatible=comparable)
