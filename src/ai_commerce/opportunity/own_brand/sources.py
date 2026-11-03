"""Exact typed M06/L05 selectors; native reads only, no new evidence store."""
from __future__ import annotations

from dataclasses import dataclass

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.operations.cost_ledger import CostLedger
from ai_commerce.opportunity.portfolio import (
    EvidenceBundle, PortfolioRecommendation, own_brand_reference,
    recommend_portfolio, validate_recommendation,
)
from ai_commerce.settlement.ledger import SettlementLedger
from ai_commerce.settlement.review import CommercialReview, commercial_review

from .schemas import MAX_NATIVE_BYTES, OwnBrandError, bounded, parse, require


@dataclass(frozen=True, slots=True)
class PortfolioSource:
    recommendation: PortfolioRecommendation
    recommendation_id: str
    sha256: str
    reference_bytes: bytes
    evidence: EvidenceBundle | None = None


@dataclass(frozen=True, slots=True)
class CommercialSource:
    review: CommercialReview
    review_id: str
    sha256: str
    native_refs: tuple[tuple[str, str], ...]
    # commercial_review itself enforces the exact native AffiliateCommerceRun.
    commerce_run: object | None = None
    settlement_ledger: SettlementLedger | None = None
    cost_ledger: CostLedger | None = None


@dataclass(frozen=True, slots=True)
class OwnBrandSources:
    portfolio: PortfolioSource | None = None
    commercial_review: CommercialSource | None = None


def _pin(raw, expected):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_NATIVE_BYTES, "NATIVE_SIZE_LIMIT")
    require(type(expected) is str and len(expected) == 64
            and sha256_bytes(raw) == expected, "SOURCE_HASH_PIN_MISMATCH")
    return parse(raw, native=True)


def _portfolio(source):
    require(type(source) is PortfolioSource, "TYPED_PORTFOLIO_SOURCE_REQUIRED")
    require(type(source.recommendation) is PortfolioRecommendation, "NATIVE_RECOMMENDATION_REQUIRED")
    require(type(source.recommendation_id) is str and bool(source.recommendation_id), "RECOMMENDATION_ID_PIN_REQUIRED")
    raw = source.recommendation.to_bytes()
    _pin(raw, source.sha256)
    native = PortfolioRecommendation.from_bytes(raw, expected_id=source.recommendation_id)
    require(native.to_bytes() == raw, "NONCANONICAL_NATIVE_RECOMMENDATION")
    body = validate_recommendation(native.to_dict())
    reference = own_brand_reference(native)
    require(type(source.reference_bytes) is bytes and len(source.reference_bytes) <= 4096
            and source.reference_bytes == canonical_bytes(reference), "OWN_BRAND_REFERENCE_PIN_MISMATCH")
    if source.evidence is not None:
        require(type(source.evidence) is EvidenceBundle, "NATIVE_EVIDENCE_BUNDLE_REQUIRED")
        # ORIGINAL request, including its clock/policy. Replacing as_of would
        # silently create a different M06 decision and invalidate the pin.
        replay = recommend_portfolio(body["request"], source.evidence)
        require(replay.to_bytes() == raw, "PORTFOLIO_CURRENT_REPLAY_MISMATCH")
    request, scope = body["request"], body["request"]["scope"]
    dates = {reference["as_of"], request["measurement_window"][1]}
    dates.update(row["measurement_window"][1] for row in body["measurements"])
    dates.update(row["observed_at"] for row in body["costs"])
    return {
        "reference": reference, "reference_sha256": sha256_bytes(source.reference_bytes),
        "custody": "CURRENT_REPLAY" if source.evidence is not None else "ARCHIVED_ONLY",
        "scope": {"campaign_ref": scope["campaign_ref"], "concept_ref": request["candidate_id"],
            "product_ref": scope["product_ref"], "market": scope["market"],
            "account_ref": scope["account_ref"], "measurement_window": request["measurement_window"]},
        "currency": request["currency"],
        "native_max_evidence_age_seconds": request["human_policy"]["max_evidence_age_seconds"] if request["human_policy"] else 2592000,
        "evidence_quality": body["evidence_quality"],
        "source_codes": body["reason_codes"], "counterevidence": body["counterevidence"],
        "native_sources": body["sources"], "observed_timestamps": sorted(dates),
        "current_observation_count": sum(row["role"] == "CURRENT" and row["status"] == "AVAILABLE"
                                         for row in body["measurements"]),
        "interpretation": "HUMAN_SUPPLIED_DESCRIPTIVE_ONLY", "renewed_execution_authority": "NONE",
    }


def _commercial(source):
    require(type(source) is CommercialSource, "TYPED_COMMERCIAL_SOURCE_REQUIRED")
    require(type(source.review) is CommercialReview, "NATIVE_COMMERCIAL_REVIEW_REQUIRED")
    require(type(source.review_id) is str and bool(source.review_id), "REVIEW_ID_PIN_REQUIRED")
    raw = source.review.to_bytes()
    _pin(raw, source.sha256)
    native = CommercialReview.from_bytes(raw, expected_id=source.review_id)
    require(native.to_bytes() == raw, "NONCANONICAL_NATIVE_REVIEW")
    body = native.to_dict()
    sources = body["sources"]
    # Exact native envelope hashes, not caller-authored aliases or claimed sums.
    refs = [("commerce_run", sources["commerce_run"]["raw_sha256"]),
            ("settlement_history", sources["settlement_history"]["raw_sha256"])]
    refs.extend(("cost:" + row["entry_id"], sha256_bytes(canonical_bytes(row))) for row in sources["cost_records"])
    refs = tuple(sorted(refs))
    require(type(source.native_refs) is tuple and len(source.native_refs) <= 130
            and all(type(row) is tuple and len(row) == 2 for row in source.native_refs)
            and source.native_refs == refs, "COMMERCIAL_NATIVE_REFERENCES_MISMATCH")
    handles = (source.commerce_run, source.settlement_ledger, source.cost_ledger)
    require(all(h is None for h in handles) or all(h is not None for h in handles), "INCOMPLETE_NATIVE_REVIEW_CUSTODY")
    current = source.commerce_run is not None
    if current:
        require(type(source.settlement_ledger) is SettlementLedger and type(source.cost_ledger) is CostLedger,
                "NATIVE_REVIEW_LEDGERS_REQUIRED")
        replay = commercial_review(commerce_run=source.commerce_run,
            settlement_ledger=source.settlement_ledger, cost_ledger=source.cost_ledger,
            cost_selections=sources["cost_selections"])
        require(replay.to_bytes() == raw, "COMMERCIAL_CURRENT_REPLAY_MISMATCH")
    run = parse(sources["commerce_run"]["raw_utf8"].encode("utf-8"), native=True)
    history = parse(sources["settlement_history"]["raw_utf8"].encode("utf-8"), native=True)
    dates = {run["request"]["as_of"], body["scope"]["measurement_window"][1]}
    for record in history["records"]:
        document = record["evidence"]["source"]["document"]
        dates.add(document["recorded_at"])
        dates.update(row["occurred_at"] for row in document["receipts"])
    dates.update(row["observed_at"] for row in sources["cost_records"])
    require(len(dates) <= 512, "NATIVE_TIMESTAMP_LIMIT")
    kinds = {row["source_kind"] for row in body["orders"]}
    kinds.update(row["evidence_origin"] for row in sources["cost_records"])
    synthetic = "SYNTHETIC_EVIDENCE_PRESENT" in body["reason_codes"] or "SYNTHETIC_FIXTURE" in kinds
    user = bool(kinds - {"SYNTHETIC_FIXTURE", "UNVERIFIED"})
    data_kind = ("MIXED" if synthetic and user else "SYNTHETIC_FIXTURE" if synthetic
                 else "USER_SUPPLIED" if user and "UNVERIFIED" not in kinds else "UNVERIFIED")
    scope = body["scope"]
    statuses = sorted({row["reported_status"] for row in body["orders"]})
    return {
        "review_id": body["review_id"], "sha256": source.sha256, "native_version": body["schema_version"],
        "custody": "CURRENT_REPLAY" if current else "ARCHIVED_ONLY",
        "native_refs": [{"ref": key, "sha256": sha} for key, sha in refs],
        "scope": {"campaign_ref": None, "concept_ref": None, "product_ref": scope["product_id"],
            "market": scope["market"], "account_ref": scope["account_ref"],
            "measurement_window": scope["measurement_window"]},
        "currencies": sorted({row["currency"] for row in body["by_currency"]}),
        "source_as_of": run["request"]["as_of"], "observed_timestamps": sorted(dates),
        "status": body["status"], "source_codes": body["reason_codes"], "reported_statuses": statuses,
        "data_kind": data_kind,
        "pending_financial_evidence": not bool(body["orders"]) or bool(set(statuses) & {"PENDING", "PARTIALLY_SETTLED", "UNKNOWN"})
            or "COMMERCIAL_EVIDENCE_INCOMPLETE" in body["reason_codes"] or "UNKNOWN_COST_CURRENCY" in body["reason_codes"],
        "refund_return_present": bool(set(statuses) & {"REFUNDED", "REVOKED"}),
        "actual_paid_orders": "UNKNOWN", "actual_settlement": "UNKNOWN", "realized_profit": "UNKNOWN",
        "costs_added_to_scenario": False, "attestation": "NOT_PROVIDER_ATTESTED",
    }


def resolve(sources):
    """Reopen only explicitly selected native custody; no discovery or callbacks."""
    require(type(sources) is OwnBrandSources, "TYPED_OWN_BRAND_SOURCES_REQUIRED")
    try:
        portfolio = _portfolio(sources.portfolio) if sources.portfolio is not None else None
        review = _commercial(sources.commercial_review) if sources.commercial_review is not None else None
        if portfolio is not None and review is not None:
            costs = {row["ref"]: row["sha256"] for row in review["native_refs"] if row["ref"].startswith("cost:")}
            for row in portfolio["native_sources"]:
                if row["kind"] == "REVIEW" and row["source_id"] == review["review_id"]:
                    require(row["sha256"] == review["sha256"], "CROSS_SOURCE_VERSION_MISMATCH")
                if row["kind"] == "COST" and "cost:" + row["source_id"] in costs:
                    require(row["sha256"] == costs["cost:" + row["source_id"]], "CROSS_SOURCE_COST_VERSION_MISMATCH")
        result = {"portfolio": portfolio, "commercial_review": review}
        bounded(result)
        return result
    except OwnBrandError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        # Native errors may retain paths/private receipts: export a fixed code.
        raise OwnBrandError("NATIVE_SOURCE_REVALIDATION_FAILED") from None
