"""Explicit native custody selectors. No discovery, callbacks, transport or writers."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Context, localcontext

from ai_commerce.operations.cost_ledger import CostEntry, CostLedger
from ai_commerce.opportunity.portfolio.recommend import PortfolioRecommendation
from ai_commerce.opportunity.own_brand.brief import OwnBrandDecisionBrief, build_brief
from ai_commerce.opportunity.own_brand.sources import (
    CommercialSource, OwnBrandSources, PortfolioSource, resolve as resolve_native,
)
from ai_commerce.settlement.review import CommercialReview

from .schemas import (
    MAX_BYTES, MAX_NATIVE_BYTES, ScaleOptionError, bounded, canonical_bytes, digest, require, sha256_bytes,
)


@dataclass(frozen=True, slots=True)
class OwnBrandSource:
    brief: OwnBrandDecisionBrief
    brief_id: str
    sha256: str
    # A request hash cannot recover private declarations. CURRENT requires the
    # caller's exact ORIGINAL request and native selectors, never a new clock.
    original_request_bytes: bytes | None = None
    original_sources: OwnBrandSources | None = None


@dataclass(frozen=True, slots=True)
class CostReference:
    entry_id: str
    sha256: str
    ledger: CostLedger


@dataclass(frozen=True, slots=True)
class ScaleOptionSources:
    portfolio: PortfolioSource | None = None
    own_brand: OwnBrandSource | None = None
    commercial_review: CommercialSource | None = None
    costs: tuple[CostReference, ...] = ()


def _pin(raw, expected, maximum):
    require(type(raw) is bytes and 0 < len(raw) <= maximum, "NATIVE_SIZE_LIMIT")
    require(type(expected) is str and len(expected) == 64 and sha256_bytes(raw) == expected,
            "SOURCE_HASH_PIN_MISMATCH")


def _scope(actual, *, concept=None):
    return {"campaign_ref": actual.get("campaign_ref"), "concept_ref": concept or actual.get("concept_ref"),
        "product_ref": actual.get("product_ref", actual.get("product_id")), "market": actual.get("market"),
        "account_ref": deepcopy(actual.get("account_ref")), "provider_ref": actual.get("provider_ref"),
        "platform": actual.get("platform"), "surface": actual.get("surface"),
        "creative_ref": actual.get("creative_ref"), "creative_sha256": actual.get("creative_sha256"),
        "measurement_window": deepcopy(actual.get("measurement_window"))}


def _lineage(rows):
    mapping = {}
    for kind, source_id, sha in rows:
        key = kind, source_id
        require(key not in mapping or mapping[key] == sha, "CROSS_SOURCE_VERSION_MISMATCH")
        mapping[key] = sha
    return [{"kind": kind, "source_id": key, "sha256": sha}
            for (kind, key), sha in sorted(mapping.items())]


def _kind(kinds):
    synthetic = bool(set(kinds) & {"SYNTHETIC_FIXTURE", "MIXED"})
    user = bool(set(kinds) & {"USER_SUPPLIED", "MIXED"})
    return "MIXED" if synthetic and user else "SYNTHETIC_FIXTURE" if synthetic else "USER_SUPPLIED" if user else "UNVERIFIED"


def _portfolio(selector, receipt):
    # resolve_native already performs the actual .to_bytes/.from_bytes, pinned
    # M07 reference and optional native bundle replay with the ORIGINAL request.
    require(type(selector.recommendation) is PortfolioRecommendation, "NATIVE_RECOMMENDATION_REQUIRED")
    body = PortfolioRecommendation.from_bytes(selector.recommendation.to_bytes(),
        expected_id=selector.recommendation_id).to_dict()
    request = body["request"]
    actual = {**request["scope"], "measurement_window": request["measurement_window"]}
    current = (selector.evidence is not None and bool(body["sources"])
               and all(row["custody"] == "NATIVE_REPLAY" for row in body["sources"]))
    rows = [("COST" if r["kind"] == "COST" else "REVIEW" if r["kind"] == "REVIEW" else "NATIVE",
             r["source_id"], r["sha256"]) for r in body["sources"]]
    for row in body["sources"]:
        rows.extend(("COST" if r["ref"].startswith("cost:") else "NATIVE",
                     r["ref"][5:] if r["ref"].startswith("cost:") else r["ref"], r["sha256"])
                    for r in row["native_refs"])
    return {"source_id": body["recommendation_id"], "sha256": selector.sha256,
        "native_version": body["schema_version"], "evaluator_version": body["evaluator_version"],
        "source_sha": body["source_sha"], "source_as_of": request["as_of"],
        "scope": _scope(actual, concept=request["candidate_id"]), "currencies": [request["currency"]],
        "custody": "CURRENT_REPLAY" if current else "ARCHIVED_ONLY",
        "original_request_replayed": selector.evidence is not None,
        "observed_timestamps": receipt["observed_timestamps"], "source_codes": body["reason_codes"],
        "lineage": _lineage(rows), "attestation": "NOT_PROVIDER_ATTESTED", "decision": body["decision"],
        "observation_count": receipt["current_observation_count"] if current else 0,
        "data_kind": _kind([r["data_kind"] for r in body["sources"]]),
        "counterevidence_codes": sorted({r["code"] for r in body["counterevidence"]}),
        "scope_discrepancy": any(r["scope_link"] == "MISMATCH" for r in body["sources"]),
        "interior_timestamps_complete": not any(r["kind"] in {"PROPOSAL", "ADVISORY", "REVIEW"} for r in body["sources"]),
        "native_max_age_seconds": receipt["native_max_evidence_age_seconds"], "evidence_quality": body["evidence_quality"]}


def _review(selector, receipt):
    require(type(selector.review) is CommercialReview, "NATIVE_COMMERCIAL_REVIEW_REQUIRED")
    body = CommercialReview.from_bytes(selector.review.to_bytes(), expected_id=selector.review_id).to_dict()
    actual = body["scope"]
    # A16 has no campaign/concept join. Use a separate nullable native scope
    # below; absent joins are recorded, never guessed from the Human request.
    scope = _scope(actual)
    rows = [("COST" if r["ref"].startswith("cost:") else "NATIVE",
             r["ref"][5:] if r["ref"].startswith("cost:") else r["ref"], r["sha256"])
            for r in receipt["native_refs"]]
    return {"source_id": body["review_id"], "sha256": selector.sha256,
        "native_version": body["schema_version"], "evaluator_version": body["evaluator_version"], "source_sha": None,
        "source_as_of": receipt["source_as_of"], "scope": scope, "currencies": receipt["currencies"],
        "custody": receipt["custody"], "original_request_replayed": receipt["custody"] == "CURRENT_REPLAY",
        "observed_timestamps": receipt["observed_timestamps"], "source_codes": receipt["source_codes"],
        "lineage": _lineage(rows), "attestation": "NOT_PROVIDER_ATTESTED", "status": receipt["status"],
        "pending": receipt["pending_financial_evidence"], "refund_or_revocation": receipt["refund_return_present"],
        "reported_statuses": receipt["reported_statuses"], "data_kind": receipt["data_kind"],
        "actual_paid_orders": "UNKNOWN", "actual_settlement": "UNKNOWN", "realized_profit": "UNKNOWN"}


def _brand(selector):
    require(type(selector) is OwnBrandSource and type(selector.brief) is OwnBrandDecisionBrief,
            "TYPED_OWN_BRAND_SOURCE_REQUIRED")
    require(type(selector.brief_id) is str and bool(selector.brief_id), "BRIEF_ID_PIN_REQUIRED")
    raw = selector.brief.to_bytes()
    _pin(raw, selector.sha256, MAX_BYTES)
    brief = OwnBrandDecisionBrief.from_bytes(raw, expected_id=selector.brief_id)
    require(brief.to_bytes() == raw, "NONCANONICAL_NATIVE_BRIEF")
    handles = selector.original_request_bytes, selector.original_sources
    require(all(h is None for h in handles) or all(h is not None for h in handles), "INCOMPLETE_ORIGINAL_BRIEF_CUSTODY")
    replayed = selector.original_request_bytes is not None
    if replayed:
        require(type(selector.original_request_bytes) is bytes and len(selector.original_request_bytes) <= MAX_BYTES
                and type(selector.original_sources) is OwnBrandSources, "ORIGINAL_BRIEF_REQUEST_AND_SOURCES_REQUIRED")
        replay = build_brief(selector.original_request_bytes, selector.original_sources)
        require(replay.to_bytes() == raw, "OWN_BRAND_CURRENT_REPLAY_MISMATCH")
    body = brief.to_dict()
    inputs, native = body["inputs"], body["sources"]
    rows, dates = [], {inputs["as_of"], inputs["human"]["declared_at"]}
    if inputs["scope"]["measurement_window"] is not None:
        dates.add(inputs["scope"]["measurement_window"][1])
    custody = {k: native[k]["custody"] if native[k] is not None else None for k in ("portfolio", "commercial_review")}
    present = [r for r in native.values() if r is not None]
    portfolio, review = native["portfolio"], native["commercial_review"]
    complete_bundle = (bool(present) and all(r["custody"] == "CURRENT_REPLAY" for r in present)
        and (portfolio is None or bool(portfolio["native_sources"]) and all(
            r["custody"] == "NATIVE_REPLAY" for r in portfolio["native_sources"])))
    current = replayed and complete_bundle
    if portfolio is not None:
        ref = portfolio["reference"]
        rows.append(("PORTFOLIO", ref["recommendation_id"], ref["sha256"]))
        rows.extend(("COST" if r["kind"] == "COST" else "REVIEW" if r["kind"] == "REVIEW" else "NATIVE",
                     r["source_id"], r["sha256"]) for r in portfolio["native_sources"])
        for row in portfolio["native_sources"]:
            rows.extend(("COST" if r["ref"].startswith("cost:") else "NATIVE",
                         r["ref"][5:] if r["ref"].startswith("cost:") else r["ref"], r["sha256"])
                        for r in row["native_refs"])
    if review is not None:
        rows.append(("REVIEW", review["review_id"], review["sha256"]))
        rows.extend(("COST" if r["ref"].startswith("cost:") else "NATIVE",
                     r["ref"][5:] if r["ref"].startswith("cost:") else r["ref"], r["sha256"])
                    for r in review["native_refs"])
    for row in present:
        dates.update(row["observed_timestamps"])
    require(len(dates) <= 512, "NATIVE_TIMESTAMP_LIMIT")
    policy = inputs["human_policy"]
    return {"source_id": body["brief_id"], "sha256": selector.sha256,
        "native_version": body["schema_version"], "evaluator_version": body["evaluator_version"],
        "source_sha": body["source_sha"], "source_as_of": inputs["as_of"], "scope": _scope(inputs["scope"]),
        "currencies": [inputs["currency"]], "custody": "CURRENT_REPLAY" if current else "ARCHIVED_ONLY",
        "original_request_replayed": replayed, "native_custody": custody,
        "native_bundle_replayed": replayed and complete_bundle,
        "observed_timestamps": sorted(dates), "source_codes": body["reason_codes"],
        "lineage": _lineage(rows), "attestation": "NOT_PROVIDER_ATTESTED", "decision": body["decision"],
        "blocking_reasons": body["blocking_reasons"], "evidence_grade": body["evidence_grade"],
        "native_max_age_seconds": policy["max_evidence_age_seconds"] if policy else 2592000,
        "interior_timestamps_complete": portfolio is None or not any(
            r["kind"] in {"PROPOSAL", "ADVISORY", "REVIEW"} for r in portfolio["native_sources"]),
        "feasibility": body["calculations"], "feasibility_sha256": digest(body["calculations"]),
        "moq": inputs["human"]["moq"]["value"],
        "research_budget": body["human_action"]["budget_ceiling"],
        "max_research_units": body["human_action"]["unit_ceiling"],
        "pending": review is not None and review["pending_financial_evidence"],
        "refund_or_revocation": review is not None and review["refund_return_present"],
        "counterevidence_codes": sorted({r["code"] for r in body["counterevidence"]}), "proven_demand": False}


def validate_lineage(sources):
    """Reconcile version pins across independent receipts, without adding costs."""
    versions = {}
    for name, kind in (("portfolio", "PORTFOLIO"), ("own_brand", "OWN_BRAND"), ("commercial_review", "REVIEW")):
        row = sources[name]
        if row is None:
            continue
        refs = [{"kind": kind, "source_id": row["source_id"], "sha256": row["sha256"]}, *row["lineage"]]
        require(row["lineage"] == _lineage([(r["kind"], r["source_id"], r["sha256"]) for r in row["lineage"]]),
                "NONCANONICAL_NATIVE_LINEAGE")
        for ref in refs:
            key = ref["kind"], ref["source_id"]
            require(key not in versions or versions[key] == ref["sha256"], "CROSS_SOURCE_VERSION_MISMATCH")
            versions[key] = ref["sha256"]
    costs = sources["costs"]
    require(costs == sorted(costs, key=lambda r: r["entry_id"])
            and len({r["entry_id"] for r in costs}) == len(costs), "DUPLICATE_COST_REFERENCE")
    for row in costs:
        key = "COST", row["entry_id"]
        require(key not in versions or versions[key] == row["sha256"], "CROSS_SOURCE_COST_VERSION_MISMATCH")
    brand, portfolio, review = sources["own_brand"], sources["portfolio"], sources["commercial_review"]
    if brand is not None:
        for kind, direct in (("PORTFOLIO", portfolio), ("REVIEW", review)):
            embedded = [r for r in brand["lineage"] if r["kind"] == kind]
            require(direct is None or not embedded or all(
                r["source_id"] == direct["source_id"] and r["sha256"] == direct["sha256"] for r in embedded),
                "M07_SELECTED_SOURCE_DISCREPANCY")


def resolve(sources):
    require(type(sources) is ScaleOptionSources, "TYPED_SCALE_OPTION_SOURCES_REQUIRED")
    require(type(sources.costs) is tuple and len(sources.costs) <= 32, "COST_REFERENCE_LIMIT")
    try:
        if sources.portfolio is not None:
            require(type(sources.portfolio) is PortfolioSource, "TYPED_PORTFOLIO_SOURCE_REQUIRED")
            require(type(sources.portfolio.recommendation) is PortfolioRecommendation, "NATIVE_RECOMMENDATION_REQUIRED")
            _pin(sources.portfolio.recommendation.to_bytes(), sources.portfolio.sha256, MAX_NATIVE_BYTES)
        if sources.commercial_review is not None:
            require(type(sources.commercial_review) is CommercialSource, "TYPED_COMMERCIAL_SOURCE_REQUIRED")
            require(type(sources.commercial_review.review) is CommercialReview, "NATIVE_COMMERCIAL_REVIEW_REQUIRED")
            _pin(sources.commercial_review.review.to_bytes(), sources.commercial_review.sha256, MAX_NATIVE_BYTES)
        native = resolve_native(OwnBrandSources(sources.portfolio, sources.commercial_review))
        portfolio = _portfolio(sources.portfolio, native["portfolio"]) if sources.portfolio is not None else None
        review = _review(sources.commercial_review, native["commercial_review"]) if sources.commercial_review is not None else None
        brand = _brand(sources.own_brand) if sources.own_brand is not None else None
        costs, ids, receipts, observations = [], set(), set(), set()
        for selected in sources.costs:
            require(type(selected) is CostReference and type(selected.ledger) is CostLedger, "TYPED_COST_REFERENCE_REQUIRED")
            require(selected.entry_id not in ids, "DUPLICATE_COST_REFERENCE")
            ids.add(selected.entry_id)
            entry = selected.ledger.get(selected.entry_id)
            require(type(entry) is CostEntry, "NATIVE_COST_ENTRY_REQUIRED")
            record = entry.to_record()
            _pin(canonical_bytes(record), selected.sha256, MAX_BYTES)
            entry = CostEntry.from_record(record)
            identity = (entry.job_id, entry.attempt, entry.intent_digest, entry.provider, entry.status,
                        entry.evidence_ref, entry.evidence_sha256)
            require(identity not in observations, "DUPLICATE_COST_OBSERVATION")
            observations.add(identity)
            if entry.status == "CHARGED":
                require(entry.evidence_sha256 not in receipts, "DUPLICATE_COST_RECEIPT")
                receipts.add(entry.evidence_sha256)
            with localcontext(Context(prec=80)):
                selected.ledger.summarize([entry.entry_id])
            costs.append({"entry_id": entry.entry_id, "sha256": selected.sha256, "observed_at": entry.observed_at,
                "status": entry.status, "amount": entry.amount, "currency": entry.currency,
                "data_kind": entry.evidence_origin, "allocation": "REFERENCE_ONLY_NO_NATIVE_SCOPE_BINDING",
                "provider_charge_verified": False})
        result = {"portfolio": portfolio, "own_brand": brand, "commercial_review": review,
                  "costs": sorted(costs, key=lambda r: r["entry_id"])}
        bounded(result, private=True)
        validate_lineage(result)
        return result
    except ScaleOptionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ScaleOptionError("NATIVE_SOURCE_REVALIDATION_FAILED") from None
