"""Deterministic offline M06 advice. No transport, intent, writer or wall clock."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from jsonschema import Draft202012Validator
from referencing import Registry

from ai_commerce.contracts.schema import _format_checker
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from .schemas import (
    EVALUATOR_VERSION, LIMITATIONS, REQUEST, SOURCE_SHA, PortfolioError,
    bounded, digest, instant, parse, require, validator,
)
from .sources import EvidenceBundle, resolve

_REQUEST = Draft202012Validator(REQUEST, registry=Registry(), format_checker=_format_checker())
_EPISTEMIC = {"PROPOSAL": "HYPOTHESIS", "REPORT": "DESCRIPTIVE_OBSERVATION",
              "COMPARISON": "DESCRIPTIVE_COMPARISON", "ADVISORY": "ADVISORY",
              "COST": "COST_ASSERTION", "REVIEW": "COMMERCIAL_ASSERTION"}


def _request(value):
    value = parse(value) if type(value) is bytes else parse(bounded(value))
    require(_REQUEST.is_valid(value), "INVALID_PORTFOLIO_REQUEST")
    scope, metric = value["scope"], value["metric"]
    require(metric["platform"] == scope["platform"] and metric["cohort"] == scope["cohort"],
            "METRIC_SCOPE_DEFINITION_MISMATCH")
    require((scope["creative_ref"] is None) == (scope["creative_sha256"] is None), "CREATIVE_PIN_REQUIRED")
    for window in (value["measurement_window"], value["baseline_window"]):
        require(window is None or instant(window[0]) < instant(window[1]), "INVALID_MEASUREMENT_WINDOW")
    policy = value["human_policy"]
    if policy is not None:
        require(instant(policy["valid_from"]) < instant(policy["valid_until"]), "INVALID_POLICY_WINDOW")
        require(Decimal(policy["stop_at_or_below"]) < Decimal(policy["keep_at_or_above"]), "CONFLICTING_POLICY_THRESHOLDS")
    return value


def _number(value):
    if value is None:
        return None
    with localcontext() as context:
        context.prec = 128
        result = format(Decimal(str(value)), "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result in {"-0", ""} else result


def _cost_totals(costs, receipts):
    buckets = {}
    for row in costs:
        if row["currency"] is None or receipts[row["source_id"]]["custody"] != "NATIVE_REPLAY":
            continue
        bucket = buckets.setdefault(row["currency"], {"currency": row["currency"],
            "estimated": None, "supplied_charged": None, "synthetic_charged": None,
            "pending_count": 0, "unknown_count": 0, "complete": False, "provider_charge_verified": False})
        if row["status"] in {"PENDING", "UNKNOWN"}:
            bucket["pending_count" if row["status"] == "PENDING" else "unknown_count"] += 1
        if row["status"] in {"ESTIMATED", "CHARGED"}:
            key = ("estimated" if row["status"] == "ESTIMATED" else
                   "synthetic_charged" if row["evidence_origin"] == "SYNTHETIC_FIXTURE" else "supplied_charged")
            with localcontext() as context:
                context.prec = 128
                bucket[key] = _number(Decimal(bucket[key] or "0") + Decimal(row["amount"]))
    return [buckets[key] for key in sorted(buckets)]


def _report_link(row, request):
    body, scope = row.body, request["scope"]
    if scope["account_ref"] is None or not row.plans:
        return "UNLINKED"
    fields = ("platform", "surface", "account_ref", "cohort", "timezone")
    if any(body[key] != scope[key] for key in fields):
        return "MISMATCH"
    if any(plan["campaign_ref"] != scope["campaign_ref"]
           or plan["attribution_tier"] != scope["attribution_tier"] for plan in row.plans):
        return "MISMATCH"
    # Native MeasurementPlan has campaign identity, but no product, creative,
    # commerce-provider or market binding. Never invent that join.
    return "PARTIAL" if any(scope[k] is not None for k in (
        "creative_ref", "product_ref", "provider_ref", "market")) else "MATCHED"


def _review_link(body, request):
    scope, actual = request["scope"], body["scope"]
    for key in ("platform", "surface", "timezone", "account_ref", "creative_ref",
                "creative_sha256", "provider_ref", "market"):
        if scope[key] is not None and scope[key] != actual[key]:
            return "MISMATCH"
    if scope["product_ref"] is not None and scope["product_ref"] != actual["product_id"]:
        return "MISMATCH"
    if actual["measurement_window"] != request["measurement_window"]:
        return "MISMATCH"
    return "PARTIAL"  # A16 has no native campaign_ref; no implicit campaign join.


def recommend_portfolio(request: dict | bytes, evidence: EvidenceBundle = EvidenceBundle()) -> PortfolioRecommendation:
    """Evaluate one candidate; fresh native reads are mandatory on every call.

    Current native grades are local/supplied, so v1 cannot issue a decisive
    KEEP/CHANGE/STOP. Explicit thresholds cannot upgrade evidence authority.
    Hash/custody corruption raises a bounded error instead of reusing old advice.
    """
    request = _request(request)
    rows = resolve(evidence)
    clock, scope = instant(request["as_of"]), request["scope"]
    reasons, missing, negative = {"OFFLINE_UNATTESTED", "NO_CAUSAL_OR_FINANCIAL_CLAIM"}, set(), set()
    receipts, measurements, costs, comparisons, review_attribution = [], [], [], [], []
    policy = request["human_policy"]
    max_age = policy["max_evidence_age_seconds"] if policy else 2592000
    if policy is None:
        missing.add("HUMAN_POLICY")
        reasons.add("EXPLICIT_HUMAN_POLICY_REQUIRED")
    else:
        if not instant(policy["valid_from"]) <= clock < instant(policy["valid_until"]):
            reasons.add("POLICY_OUTSIDE_VALIDITY")
        if policy["metric_definition_sha256"] != digest(request["metric"]):
            reasons.add("POLICY_METRIC_MISMATCH")
        if policy["currency"] != request["currency"]:
            reasons.add("POLICY_CURRENCY_MISMATCH")
    if instant(request["measurement_window"][1]) > clock:
        reasons.add("EVALUATION_WINDOW_FUTURE")
    baseline = request["baseline_window"]
    if baseline is not None:
        current = request["measurement_window"]
        if (instant(baseline[1]) > instant(current[0]) or
                instant(baseline[1]) - instant(baseline[0]) != instant(current[1]) - instant(current[0])):
            reasons.add("COMPARISON_WINDOW_MISMATCH")

    def age(when):
        if when is not None:
            elapsed = (clock - instant(when)).total_seconds()
            if elapsed < 0:
                reasons.add("FUTURE_EVIDENCE")
            elif elapsed > max_age:
                reasons.add("STALE_EVIDENCE")

    for row in rows:
        body, link = row.body, "UNLINKED"
        if row.custody != "NATIVE_REPLAY":
            reasons.add("SOURCE_CUSTODY_UNVERIFIED")
        if row.data_kind in {"SYNTHETIC_FIXTURE", "MIXED"}:
            reasons.add("SYNTHETIC_FIXTURE_NOT_LIVE_OUTCOME")
        if row.kind == "PROPOSAL":
            reasons.add("A10_HYPOTHESIS_NOT_OBSERVED_DEMAND")
            age(body["as_of"])
            for fact in body["signal_evidence"]:
                age(fact["observed_at"])
                age(fact["collected_at"])
            if body["creative"]["product_ref"] is not None and body["creative"]["product_ref"] != scope["product_ref"]:
                link = "MISMATCH"
            for reason in body["reasons"]:
                if reason["polarity"] == "COUNTEREVIDENCE":
                    negative.add((row.source_id, "A10_COUNTEREVIDENCE"))
        elif row.kind == "REPORT":
            link = _report_link(row, request)
            age(body["measurement_window"][1])
            age(body.get("as_of"))
            role = ("CURRENT" if body["measurement_window"] == request["measurement_window"] else
                    "BASELINE" if baseline is not None and body["measurement_window"] == baseline else "UNMATCHED")
            comparable = link == "MATCHED" and role != "UNMATCHED" and body["metric"] == request["metric"]
            if role == "UNMATCHED":
                reasons.add("MEASUREMENT_WINDOW_MISMATCH")
            if body["metric"] != request["metric"]:
                reasons.add("METRIC_DEFINITION_MISMATCH")
            state = body["measurement"]["status"] if comparable else "NOT_COMPARABLE"
            if row.custody != "NATIVE_REPLAY":
                state = "UNVERIFIED"
            if state != "AVAILABLE":
                missing.add("COMPARABLE_MEASUREMENT")
            if body["not_available_count"]:
                missing.add("REPORT_OBSERVATIONS_PARTIAL")
                reasons.add("MISSING_OBSERVATIONS_NOT_ZERO")
            if body["confounders"]:
                negative.add((row.source_id, "A11_CONFOUNDERS"))
            if body["measurement"]["value"] is not None and body["measurement"]["value"] < 0:
                negative.add((row.source_id, "NEGATIVE_MEASUREMENT_REVIEW_REQUIRED"))
            trusted_local = state in {"AVAILABLE", "INSUFFICIENT_DATA"}
            measurements.append({"source_id": row.source_id, "role": role, "status": state,
                "value": _number(body["measurement"]["value"]) if state == "AVAILABLE" else None,
                "numerator": _number(body["measurement"]["numerator"]) if trusted_local else None,
                "denominator_value": _number(body["measurement"]["denominator"]) if trusted_local else None,
                "unit": body["metric"]["unit"], "denominator": body["metric"]["denominator"],
                "metric_definition_sha256": digest(body["metric"]),
                "measurement_window": body["measurement_window"],
                "available_count": body["available_count"], "not_available_count": body["not_available_count"],
                "claim": "LOCAL_DESCRIPTIVE_ONLY"})
        elif row.kind == "ADVISORY":
            reasons.add("A11_ADVISORY_NOT_CAUSAL_UPLIFT")
            for item in body["entries"]:
                entry = item["entry"]
                age(entry["revision"]["recorded_at"])
                age(entry["hypothesis"]["measurement_window"][1])
                if entry["record"]["counterevidence_refs"] or entry["record"]["status"] in {"CONTRADICTED", "MIXED", "RETIRED"}:
                    negative.add((row.source_id, "A11_COUNTEREVIDENCE_OR_RETRACTION"))
        elif row.kind == "COST":
            age(body["observed_at"])
            reasons.add("COST_ALLOCATION_AND_CHARGE_UNVERIFIED")
            if body["currency"] not in {None, request["currency"]}:
                reasons.add("CURRENCY_MISMATCH_NO_FX")
            if body["status"] in {"PENDING", "UNKNOWN"}:
                missing.add("COST_AMOUNT_OR_CURRENCY")
            costs.append({"source_id": row.source_id,
                **{key: body[key] for key in ("entry_id", "job_id", "attempt", "intent_digest", "provider",
                    "status", "amount", "currency", "evidence_origin", "evidence_sha256", "observed_at")},
                "evidence_ref_sha256": sha256_bytes(body["evidence_ref"].encode()) if body["evidence_ref"] else None,
                "allocation": "UNVERIFIED_NO_NATIVE_CAMPAIGN_BINDING"})
        elif row.kind == "REVIEW":
            link = _review_link(body, request)
            age(body["scope"]["measurement_window"][1])
            reasons.add("A16_SETTLEMENT_AND_PROFIT_UNVERIFIED")
            review_attribution.append({"source_id": row.source_id, "scope_link": link,
                "tiers": sorted({order["attribution_tier"] for order in body["orders"]})})
            for order in body["orders"]:
                if order["reported_status"] in {"PENDING", "PARTIALLY_SETTLED", "UNKNOWN"}:
                    missing.add("PAID_SETTLEMENT_PROOF")
                if order["reported_status"] in {"REFUNDED", "REVOKED"}:
                    negative.add((row.source_id, "SETTLEMENT_REFUND_OR_REVOCATION"))
            if body["status"] == "HOLD":
                negative.add((row.source_id, "A16_UPSTREAM_HOLD"))
            if any(bucket["currency"] != request["currency"] for bucket in body["by_currency"]):
                reasons.add("CURRENCY_MISMATCH_NO_FX")
        if link == "MISMATCH":
            reasons.add("SOURCE_SCOPE_MISMATCH")
        elif link in {"UNLINKED", "PARTIAL"} and row.kind != "COMPARISON":
            reasons.add("CROSS_SOURCE_JOIN_NOT_ESTABLISHED")
        receipts.append({"kind": row.kind, "source_id": row.source_id, "sha256": row.sha256,
            "native_version": body.get("schema_version", "CostEntry.v1"), "custody": row.custody,
            "epistemic_status": _EPISTEMIC[row.kind], "attestation": "NOT_PROVIDER_ATTESTED",
            "data_kind": row.data_kind, "scope_link": link,
            "native_refs": [{"ref": key, "sha256": sha} for key, sha in row.refs]})

    by_measurement = {row["source_id"]: row for row in measurements}
    by_receipt = {row["source_id"]: row for row in receipts}
    for row in rows:
        if row.kind != "COMPARISON":
            continue
        body = row.body
        left_id, right_id = body["left_report_ref"]["report_id"], body["right_report_ref"]["report_id"]
        left, right = by_measurement.get(left_id), by_measurement.get(right_id)
        comparable = (left is not None and right is not None and left["role"] == "BASELINE"
            and right["role"] == "CURRENT" and left["status"] == right["status"] == "AVAILABLE"
            and left["not_available_count"] == right["not_available_count"] == 0
            and "COMPARISON_WINDOW_MISMATCH" not in reasons)
        status = body["status"] if comparable else "NOT_COMPARABLE"
        if row.custody != "NATIVE_REPLAY":
            status = "UNVERIFIED"
        if status != "AVAILABLE":
            reasons.add("COMPARISON_NOT_AVAILABLE_OR_COMPARABLE")
        by_receipt[row.source_id]["scope_link"] = "MATCHED" if comparable else "UNLINKED"
        comparisons.append({"source_id": row.source_id, "left_report_id": left_id, "right_report_id": right_id,
            "status": status, "absolute_change": _number(body["absolute_change"]["value"]) if status == "AVAILABLE" else None,
            "relative_change": _number(body["relative_change"]["value"]) if status == "AVAILABLE" else None,
            "unit": body["absolute_change"]["unit"], "claim": "DESCRIPTIVE_NO_CAUSAL_UPLIFT"})
    current = [m for m in measurements if m["role"] == "CURRENT" and m["status"] == "AVAILABLE"]
    if len({m["value"] for m in current}) > 1:
        reasons.add("CONFLICTING_CURRENT_MEASUREMENTS")
        negative.update((m["source_id"], "CONFLICTING_CURRENT_MEASUREMENTS") for m in current)
    if not current:
        missing.add("COMPARABLE_CURRENT_PERFORMANCE")
    for kind in ("PROPOSAL", "REPORT", "ADVISORY", "COST", "REVIEW"):
        if not any(row.kind == kind for row in rows):
            missing.add("NATIVE_" + kind)
    missing.add("AUTHORITATIVE_OBSERVATIONS")
    if costs:
        missing.add("VERIFIED_CAMPAIGN_COST_ALLOCATION")
    if review_attribution:
        missing.add("PAID_SETTLEMENT_PROOF")
    reasons.add("AUTHORITATIVE_EVIDENCE_REQUIRED_FOR_KEEP_CHANGE_STOP")
    conflicted = bool(negative) or any("MISMATCH" in code or code in {
        "STALE_EVIDENCE", "FUTURE_EVIDENCE", "POLICY_OUTSIDE_VALIDITY", "EVALUATION_WINDOW_FUTURE"} for code in reasons)
    quality = ("MISSING" if not rows else "CONFLICTED" if conflicted else
               "UNVERIFIED" if any(row.custody != "NATIVE_REPLAY" for row in rows) else "LOCAL_REPLAY_ONLY")
    body = {"schema_version": "PortfolioRecommendation.v1", "evaluator_version": EVALUATOR_VERSION,
        "source_sha": SOURCE_SHA, "request": request, "request_sha256": digest(request),
        "policy_sha256": digest(policy) if policy else None, "decision": "INCONCLUSIVE",
        "evidence_quality": quality, "confidence": "INSUFFICIENT_FOR_DECISIVE_RECOMMENDATION",
        "confidence_meaning": "EVIDENCE_RATING_NOT_PROBABILITY", "sources": receipts,
        "measurements": measurements, "comparisons": comparisons, "costs": costs,
        "cost_totals": _cost_totals(costs, by_receipt),
        "attribution": {"declared_tier": scope["attribution_tier"], "reviews": review_attribution,
                        "settlement_status": "UNVERIFIED", "paid_proof_verified": False},
        "reason_codes": sorted(reasons), "missingness": sorted(missing),
        "counterevidence": [{"source_id": ref, "code": code} for ref, code in sorted(negative)],
        "assumptions": list(LIMITATIONS), "human_action": {
            "kind": "REVIEW_CONFLICTS" if conflicted else "GATHER_COMPARABLE_EVIDENCE",
            "proposed_only": True, "execution_authority": "NONE"},
        "downstream_use": "HUMAN_OR_A18_2_VERSIONED_REFERENCE_ONLY",
        "financial_commitment": "NONE", "live_outcome_verified": False}
    return PortfolioRecommendation({**body, "recommendation_id": "a18-portfolio-" + digest(body)})


def validate_recommendation(value: dict) -> dict:
    """Validate structure, identity and projections, without renewing custody."""
    value = parse(bounded(value))
    require(validator().is_valid(value), "INVALID_PORTFOLIO_RECOMMENDATION")
    _request(value["request"])
    require(value["request_sha256"] == digest(value["request"]), "REQUEST_HASH_MISMATCH")
    policy = value["request"]["human_policy"]
    require(value["policy_sha256"] == (digest(policy) if policy else None), "POLICY_HASH_MISMATCH")
    require(value["recommendation_id"] == "a18-portfolio-" + digest({k: v for k, v in value.items() if k != "recommendation_id"}),
            "RECOMMENDATION_ID_MISMATCH")
    receipts = {row["source_id"]: row for row in value["sources"]}
    require(len(receipts) == len(value["sources"]) and value["sources"] == sorted(value["sources"], key=lambda r: (r["kind"], r["source_id"])),
            "DUPLICATE_OR_UNORDERED_SOURCE")
    require(len({row["sha256"] for row in value["sources"]}) == len(receipts), "DUPLICATE_SOURCE_HASH")
    for source in value["sources"]:
        require(source["epistemic_status"] == _EPISTEMIC[source["kind"]], "SOURCE_EPISTEMIC_MISMATCH")
        refs = source["native_refs"]
        require(refs == sorted(refs, key=lambda r: (r["ref"], r["sha256"]))
                and len({(r["ref"], r["sha256"]) for r in refs}) == len(refs), "NATIVE_REFERENCE_MISMATCH")
    for key in ("reason_codes", "missingness"):
        require(value[key] == sorted(set(value[key])), "NONCANONICAL_REASON_CODES")
    for key, kind in (("measurements", "REPORT"), ("comparisons", "COMPARISON"), ("costs", "COST")):
        seen = set()
        for row in value[key]:
            ref = row["source_id"]
            require(ref in receipts and receipts[ref]["kind"] == kind and ref not in seen, "PROJECTION_SOURCE_MISMATCH")
            seen.add(ref)
            if key == "measurements":
                request = value["request"]
                role = ("CURRENT" if row["measurement_window"] == request["measurement_window"] else
                    "BASELINE" if request["baseline_window"] is not None and row["measurement_window"] == request["baseline_window"] else "UNMATCHED")
                require(row["role"] == role, "MEASUREMENT_ROLE_MISMATCH")
                if row["status"] == "AVAILABLE":
                    require(receipts[ref]["custody"] == "NATIVE_REPLAY" and receipts[ref]["scope_link"] == "MATCHED"
                        and role != "UNMATCHED" and row["available_count"] > 0
                        and row["metric_definition_sha256"] == digest(request["metric"])
                        and row["unit"] == request["metric"]["unit"]
                        and row["denominator"] == request["metric"]["denominator"], "MEASUREMENT_CUSTODY_MISMATCH")
            if key == "comparisons" and row["status"] != "AVAILABLE":
                require(row["absolute_change"] is None and row["relative_change"] is None, "UNSUPPORTED_NUMERIC_DELTA")
            if key == "costs":
                require(row["source_id"] == row["entry_id"] and
                        ((row["status"] in {"ESTIMATED", "CHARGED"}) == (row["amount"] is not None)), "COST_PROJECTION_MISMATCH")
                if row["amount"] is not None:
                    require(Decimal(row["amount"]) >= 0 and row["currency"] is not None, "COST_PROJECTION_MISMATCH")
    measures = {row["source_id"]: row for row in value["measurements"]}
    for row in value["comparisons"]:
        if row["status"] != "AVAILABLE":
            continue
        left, right = measures.get(row["left_report_id"]), measures.get(row["right_report_id"])
        require(left is not None and right is not None and left["role"] == "BASELINE" and right["role"] == "CURRENT"
            and left["status"] == right["status"] == "AVAILABLE"
            and left["not_available_count"] == right["not_available_count"] == 0
            and left["metric_definition_sha256"] == right["metric_definition_sha256"]
            and receipts[row["source_id"]]["custody"] == "NATIVE_REPLAY"
            and row["unit"] == left["unit"] == right["unit"], "UNSUPPORTED_NUMERIC_DELTA")
    require(value["cost_totals"] == _cost_totals(value["costs"], receipts), "COST_TOTAL_MISMATCH")
    require(all(row["source_id"] in receipts for row in value["counterevidence"]), "COUNTEREVIDENCE_REF_MISMATCH")
    require(value["attribution"]["declared_tier"] == value["request"]["scope"]["attribution_tier"], "ATTRIBUTION_SCOPE_MISMATCH")
    for row in value["attribution"]["reviews"]:
        source = receipts.get(row["source_id"])
        require(source is not None and source["kind"] == "REVIEW" and source["scope_link"] == row["scope_link"], "ATTRIBUTION_SCOPE_MISMATCH")
    return value


@dataclass(frozen=True, slots=True, init=False)
class PortfolioRecommendation:
    _raw: bytes

    def __init__(self, value: dict):
        object.__setattr__(self, "_raw", canonical_bytes(validate_recommendation(value)))

    @classmethod
    def from_bytes(cls, raw: bytes, *, expected_id: str | None = None):
        result = cls(parse(raw))
        require(expected_id is None or result.recommendation_id == expected_id, "RECOMMENDATION_PIN_MISMATCH")
        return result

    def to_bytes(self) -> bytes:
        return self._raw

    def to_dict(self) -> dict:
        return parse(self._raw)

    @property
    def recommendation_id(self) -> str:
        return self.to_dict()["recommendation_id"]


def recommend_many(evaluations: tuple[tuple[dict, EvidenceBundle], ...]) -> tuple[PortfolioRecommendation, ...]:
    """Independent candidates, stable ID order, no ranking or shared privileges."""
    require(type(evaluations) is tuple and 1 <= len(evaluations) <= 16, "PORTFOLIO_CANDIDATE_LIMIT")
    require(all(type(item) is tuple and len(item) == 2 for item in evaluations), "TYPED_CANDIDATE_PAIR_REQUIRED")
    checked = [(_request(request), evidence) for request, evidence in evaluations]
    require(len({request["candidate_id"] for request, _ in checked}) == len(checked), "DUPLICATE_CANDIDATE")
    return tuple(recommend_portfolio(request, evidence) for request, evidence in sorted(checked, key=lambda pair: pair[0]["candidate_id"]))


def own_brand_reference(record: PortfolioRecommendation) -> dict:
    """Typed M07 source bridge only; no capital, inventory or account authority."""
    require(type(record) is PortfolioRecommendation, "NATIVE_RECOMMENDATION_REQUIRED")
    body = validate_recommendation(record.to_dict())
    return {"schema_version": "PortfolioRecommendationRef.v1", "recommendation_id": body["recommendation_id"],
            "sha256": sha256_bytes(record.to_bytes()), "source_sha": SOURCE_SHA,
            "candidate_id": body["request"]["candidate_id"], "scope_sha256": digest(body["request"]["scope"]),
            "as_of": body["request"]["as_of"], "decision": body["decision"],
            "use": "A18_2_M07_HUMAN_REVIEW_DATA_ONLY", "financial_commitment": "NONE",
            "execution_authority": "NONE"}
