"""Deterministic E07 comparisons. No clock discovery, transport or execution API."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext

from .schemas import (
    EVALUATOR_VERSION, INPUTS, LIMITATIONS, PER_ASSET, REQUEST, SCENARIOS, SOURCES, SOURCE_SHA,
    bounded, canonical_bytes, digest, instant, parse, require, sha256_bytes, validator,
)
from .sources import ScaleOptionSources, resolve, validate_lineage

_QUANTUM = Decimal("0.000001")
_ZERO = Decimal("0")


def _clocks(value):
    for window in (value["scope"]["measurement_window"], value["scale"]["window"] if value["scale"] else None):
        require(window is None or instant(window[0]) < instant(window[1]), "INVALID_WINDOW")
    policy = value["human_policy"]
    require(policy is None or instant(policy["valid_from"]) < instant(policy["valid_until"]), "INVALID_POLICY_WINDOW")
    scope = value["scope"]
    require((scope["creative_ref"] is None) == (scope["creative_sha256"] is None), "CREATIVE_PIN_REQUIRED")


def _request(value):
    value = parse(value, private=True) if type(value) is bytes else parse(bounded(value, private=True), private=True)
    require(validator(REQUEST).is_valid(value), "INVALID_SCALE_BRAND_REQUEST")
    _clocks(value)
    human = value["human"]
    for rows in ([human["rationale"]], human["counterevidence"], human["uncertainties"],
                 [value["scale"]["uncertainty"]] if value["scale"] else []):
        require(all(bool(row.strip()) for row in rows), "EMPTY_HUMAN_DECLARATION")
        require(len(set(rows)) == len(rows), "DUPLICATE_HUMAN_DECLARATION")
    return value


def _inputs(request):
    result = {key: deepcopy(request[key]) for key in ("scope", "currency", "as_of", "human", "scale", "permissions", "human_policy")}
    result["human"]["rationale"] = digest(request["human"]["rationale"])
    for key in ("counterevidence", "uncertainties"):
        result["human"][key] = sorted(digest(row) for row in request["human"][key])
    if result["scale"] is not None:
        result["scale"]["uncertainty"] = digest(request["scale"]["uncertainty"])
    return result


def _measure(value, reasons=()):
    return {"value": format(value.quantize(_QUANTUM, rounding=ROUND_HALF_UP) + _ZERO, ".6f") if value is not None else None,
            "reason_codes": [] if value is not None else sorted(set(reasons) or {"UNKNOWN_INPUT"})}


def _units(value, reasons=()):
    return {"value": value, "reason_codes": [] if value is not None else sorted(set(reasons) or {"UNKNOWN_INPUT"})}


def _calculations(inputs):
    scale, policy = inputs["scale"], inputs["human_policy"]
    if scale is None:
        return None
    rows = []
    with localcontext(Context(prec=80, rounding=ROUND_HALF_UP, Emin=-999, Emax=999)):
        delta = instant(scale["window"][1]) - instant(scale["window"][0])
        seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal("1000000")
        days = seconds / Decimal("86400")
        for name in SCENARIOS:
            given = scale["scenarios"][name]
            assets, capacity = given["assets"], given["capacity_assets"]
            costs = {key: Decimal(value) if value is not None else None for key, value in given["costs"].items()}
            cost_reasons = {"MISSING_" + key.upper() for key, value in costs.items() if value is None}
            unit_reasons = {"MISSING_" + key.upper() for key in PER_ASSET if costs[key] is None}
            unit_cost = sum((costs[key] for key in PER_ASSET), _ZERO) if not unit_reasons else None
            total_reasons = unit_reasons | ({"MISSING_FIXED_COST"} if costs["fixed_cost"] is None else set())
            if assets is None:
                total_reasons.add("MISSING_ASSET_COUNT")
            total = unit_cost * assets + costs["fixed_cost"] if not total_reasons else None
            exposure = total + costs["reserve"] if total is not None and costs["reserve"] is not None else None
            risk_reasons = cost_reasons | ({"MISSING_ASSET_COUNT"} if assets is None else set())
            value = Decimal(given["hypothetical_value_per_asset"]) if given["hypothetical_value_per_asset"] is not None else None
            contribution = value - unit_cost if value is not None and unit_cost is not None else None
            hypothetical_total = assets * value if assets is not None and value is not None else None
            surplus = hypothetical_total - total if hypothetical_total is not None and total is not None else None

            def gap(key):
                cap = policy[key] if policy else None
                return _measure(max(exposure - Decimal(cap), _ZERO) if exposure is not None and cap is not None else None,
                                risk_reasons | ({"MISSING_" + key.upper()} if cap is None else set()))

            rows.append({"name": name, "assets": assets, "capacity_assets": capacity,
                "unit_cost": _measure(unit_cost, unit_reasons), "experiment_cost": _measure(total, total_reasons),
                "cash_exposure": _measure(exposure, risk_reasons), "budget_gap": gap("available_budget"),
                "ceiling_gap": gap("spend_ceiling"),
                "hypothetical_unit_contribution": _measure(contribution, unit_reasons | {"HYPOTHETICAL_VALUE_UNKNOWN"}),
                "hypothetical_total_value": _measure(hypothetical_total, {"HYPOTHETICAL_VALUE_OR_ASSETS_UNKNOWN"}),
                "hypothetical_surplus": _measure(surplus, total_reasons | {"HYPOTHETICAL_VALUE_UNKNOWN"}),
                "capacity_gap": _units(max(assets - capacity, 0) if assets is not None and capacity is not None else None,
                                       {"CAPACITY_OR_ASSETS_UNKNOWN"}),
                "throughput_assets_per_day": _measure(Decimal(assets) / days if assets is not None else None, {"MISSING_ASSET_COUNT"}),
                "claim": "HUMAN_COST_SCENARIO_NOT_OBSERVED_PERFORMANCE"})
        exposures = [r["cash_exposure"]["value"] for r in rows]
        return {"currency": inputs["currency"], "rounding": "DECIMAL_80_DIGITS_HALF_UP_6_PLACES_RESULTS",
            "cost_allocation": "A15_A16_REFERENCE_ONLY_NO_AUTOMATIC_ADDITION",
            "downside_basis": "ZERO_HYPOTHETICAL_VALUE_FULL_COST_AND_RESERVE_AT_RISK",
            "worst_case_exposure": _measure(max(Decimal(v) for v in exposures) if all(v is not None for v in exposures) else None,
                                           {"INCOMPLETE_EXPOSURE_SCENARIOS"}), "scenarios": rows}


def _source_checks(sources):
    bounded(sources, private=True)
    require(validator(SOURCES).is_valid(sources), "INVALID_NATIVE_PROJECTION")
    validate_lineage(sources)
    for name in ("portfolio", "own_brand", "commercial_review"):
        row = sources[name]
        if row is None:
            continue
        for key in ("source_codes", "observed_timestamps", "currencies", "counterevidence_codes", "reported_statuses", "blocking_reasons"):
            if key not in row:
                continue
            require(row[key] == sorted(set(row[key])), "NONCANONICAL_NATIVE_PROJECTION")
        require(row["source_as_of"] in row["observed_timestamps"], "SOURCE_TIMESTAMP_PIN_MISMATCH")
        window = row["scope"]["measurement_window"]
        require(window is None or instant(window[0]) < instant(window[1]), "INVALID_NATIVE_WINDOW")
        require(row["custody"] != "CURRENT_REPLAY" or row["original_request_replayed"], "NATIVE_CUSTODY_MISMATCH")
        if name == "portfolio":
            require(row["custody"] != "ARCHIVED_ONLY" or row["observation_count"] == 0, "ARCHIVED_OBSERVATION_PROMOTION")
            require(row["custody"] != "CURRENT_REPLAY" or bool(row["lineage"]), "EMPTY_NATIVE_CUSTODY")
        if name == "own_brand":
            require(row["feasibility_sha256"] == digest(row["feasibility"]), "M07_FEASIBILITY_PIN_MISMATCH")
            grades = [g for g in row["native_custody"].values() if g is not None]
            require(row["custody"] != "CURRENT_REPLAY" or row["native_bundle_replayed"] and bool(grades) and all(g == "CURRENT_REPLAY" for g in grades),
                    "M07_ARCHIVED_CUSTODY_PROMOTION")
            require([r["name"] for r in row["feasibility"]["scenarios"]] == ["bear", "base", "bull"], "M07_SCENARIO_LINEAGE_MISMATCH")
            pending = [row["feasibility"]]
            while pending:
                item = pending.pop()
                if type(item) is dict:
                    if set(item) == {"value", "reason_codes"}:
                        codes = item["reason_codes"]
                        require(codes == sorted(set(codes)) and ((item["value"] is None) == bool(codes)), "M07_UNKNOWN_MEASURE_MISMATCH")
                    pending.extend(item.values())
                elif type(item) is list:
                    pending.extend(item)
    for row in sources["costs"]:
        require((row["status"] in {"ESTIMATED", "CHARGED"}) == (row["amount"] is not None)
                and (row["amount"] is None or row["currency"] is not None), "NATIVE_COST_UNKNOWN_ZERO_MISMATCH")


def _derive(inputs, sources, request_sha256):
    bounded(inputs, private=True)
    require(validator(INPUTS).is_valid(inputs), "INVALID_SCALE_BRAND_INPUT_PROJECTION")
    _clocks(inputs)
    _source_checks(sources)
    for key in ("counterevidence", "uncertainties"):
        require(inputs["human"][key] == sorted(set(inputs["human"][key])), "NONCANONICAL_HUMAN_PROJECTION")
    clock, policy, human = instant(inputs["as_of"]), inputs["human_policy"], inputs["human"]
    common, general = set(), {"OFFLINE_UNATTESTED", "HUMAN_ASSUMPTIONS_NOT_OBSERVED_DEMAND", "NO_LIVE_OR_FINANCIAL_AUTHORITY"}
    max_age = policy["max_evidence_age_seconds"] if policy else 2592000

    def age(when, blockers):
        delta = clock - instant(when)
        elapsed = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        if elapsed < 0:
            blockers.add("FUTURE_EVIDENCE")
        elif elapsed > max_age * 1000000:
            blockers.add("STALE_EVIDENCE")

    age(human["declared_at"], common)
    if inputs["scope"]["measurement_window"] is not None and instant(inputs["scope"]["measurement_window"][1]) > clock:
        common.add("FUTURE_MEASUREMENT_WINDOW")
    if human["counterevidence"]:
        common.add("UNRESOLVED_HUMAN_COUNTEREVIDENCE")
    if policy is None:
        common.add("MISSING_HUMAN_POLICY")
    else:
        for key in ("spend_ceiling", "available_budget", "max_downside", "max_experiment_assets"):
            if policy[key] is None:
                common.add("MISSING_" + key.upper())
        if not instant(policy["valid_from"]) <= clock < instant(policy["valid_until"]):
            common.add("POLICY_OUTSIDE_VALIDITY")
        if policy["currency"] != inputs["currency"]:
            common.add("POLICY_CURRENCY_MISMATCH_NO_FX")
        if human["data_kind"] == "SYNTHETIC_FIXTURE" and not policy["allow_synthetic_research"]:
            common.add("SYNTHETIC_RESEARCH_NOT_PERMITTED")
        if policy["capital_or_stock_commitment"] or policy["campaign_or_account_changes"]:
            common.add("IRREVERSIBLE_OR_CAMPAIGN_EFFECTS_OUT_OF_SCOPE")
        if policy["proposed_action"] == "LIVE_SCALE":
            common.add("LIVE_ACTION_OUT_OF_SCOPE")
            if inputs["scope"]["account_ref"] is None or inputs["scope"]["provider_ref"] is None:
                common.add("LIVE_ACCOUNT_OR_PROVIDER_SCOPE_UNKNOWN")
            if any(inputs["permissions"][key] != "HUMAN_DECLARED" for key in ("rights_status", "provider_entitlement")):
                common.add("LIVE_RIGHTS_OR_ENTITLEMENT_UNKNOWN")
    permissions = inputs["permissions"]
    if "REVOKED" in (permissions["rights_status"], permissions["provider_entitlement"]):
        common.add("RIGHTS_OR_ENTITLEMENT_REVOKED")
    before, after = permissions["prior_rights_sha256"], permissions["current_rights_sha256"]
    if before is not None and before != after:
        common.add("RIGHTS_CHANGED_OR_MISSING_PIN")
    if permissions["valid_until"] is not None and clock >= instant(permissions["valid_until"]):
        common.add("RIGHTS_OR_ENTITLEMENT_EXPIRED")
    if any(permissions[key] == "HUMAN_DECLARED" for key in ("rights_status", "provider_entitlement")) and (
        after is None or permissions["valid_until"] is None
    ):
        common.add("INCOMPLETE_HUMAN_PERMISSION_PIN")

    def scope_check(actual, blockers, reasons, *, brand=False, commercial=False):
        for key, wanted in inputs["scope"].items():
            got = actual[key]
            if wanted is not None and got is not None and wanted != got:
                blockers.add("SOURCE_" + key.upper() + "_MISMATCH")
            elif wanted != got:
                reasons.add("PARTIAL_SCOPE_NO_AUTOMATIC_JOIN")
                # A product cannot be invented from a content-only M07 idea.
                if key == "product_ref" or brand and key in {"account_ref", "market", "measurement_window"}:
                    blockers.add("SOURCE_" + key.upper() + "_UNBOUND")
                elif policy and policy["evidence_requirement"] != "HYPOTHESIS_RESEARCH" and not (
                    commercial and key in {"campaign_ref", "concept_ref"}
                ):
                    blockers.add("REQUIRED_SOURCE_SCOPE_INCOMPLETE")

    def source_check(row, blockers, reasons, *, brand=False, commercial=False):
        if row is None:
            return
        scope_check(row["scope"], blockers, reasons, brand=brand, commercial=commercial)
        if any(c != inputs["currency"] for c in row["currencies"]):
            blockers.add("SOURCE_CURRENCY_MISMATCH_NO_FX")
        for when in row["observed_timestamps"]:
            age(when, blockers)
        if row["custody"] == "ARCHIVED_ONLY":
            reasons.add("ARCHIVED_SOURCE_HISTORICAL_ONLY")
        else:
            reasons.add("ORIGINAL_NATIVE_CUSTODY_REPLAYED_NOT_ATTESTED")
        if row.get("data_kind") in {"SYNTHETIC_FIXTURE", "MIXED"} or row.get("evidence_grade") == "SYNTHETIC_NON_ATTESTED":
            reasons.add("SYNTHETIC_CALCULATION_AND_RESEARCH_ONLY")
            if policy and not policy["allow_synthetic_research"]:
                blockers.add("SYNTHETIC_RESEARCH_NOT_PERMITTED")
        if not row.get("interior_timestamps_complete", True) and (
            clock != instant(row["source_as_of"]) or max_age < row["native_max_age_seconds"]
        ):
            blockers.add("NATIVE_INTERIOR_FRESHNESS_NOT_RENEWED")
        for code in row["source_codes"]:
            if any(word in code for word in ("MISMATCH", "CONFLICT", "STALE", "FUTURE", "REVOK", "REFUND")) or code == "POLICY_OUTSIDE_VALIDITY":
                blockers.add("NATIVE_SOURCE_DISCREPANCY")
        if row.get("counterevidence_codes"):
            blockers.add("UNRESOLVED_NATIVE_COUNTEREVIDENCE")
        if row.get("scope_discrepancy") or row.get("evidence_quality") == "CONFLICTED":
            blockers.add("PROVISIONAL_SOURCE_DISCREPANCY")
        if row.get("refund_or_revocation"):
            blockers.add("UNRESOLVED_REFUND_OR_REVOCATION")
        if row.get("pending"):
            reasons.add("PENDING_FINANCIAL_EVIDENCE_NOT_ZERO")
        if row.get("status") == "HOLD":
            blockers.add("A16_UPSTREAM_HOLD")

    calculations = _calculations(inputs)
    scale_defer, scale_no, scale_reasons = set(common), set(), {"BOUNDED_ASSET_COST_AND_CAPACITY_ASSUMPTIONS"}
    brand_defer, brand_no, brand_reasons = set(common), set(), {"M07_FEASIBILITY_REUSED_WITHOUT_RECALCULATION", "DEMAND_UNPROVEN"}
    portfolio, brand, review = sources["portfolio"], sources["own_brand"], sources["commercial_review"]
    if inputs["scale"] is not None:
        if policy and policy["max_unit_cost"] is None:
            scale_defer.add("MISSING_MAX_UNIT_COST")
        source_check(portfolio, scale_defer, scale_reasons)
        source_check(review, scale_defer, scale_reasons, commercial=True)
        if portfolio is not None:
            scale_reasons.add("M06_KEEP_CHANGE_STOP_HAS_NO_ACTION_AUTHORITY")
        requirement = policy["evidence_requirement"] if policy else "HYPOTHESIS_RESEARCH"
        if requirement != "HYPOTHESIS_RESEARCH" and not (
            portfolio is not None and portfolio["custody"] == "CURRENT_REPLAY" and portfolio["observation_count"] > 0
        ):
            scale_defer.add("CURRENT_DESCRIPTIVE_PORTFOLIO_REQUIRED")
        if requirement == "CURRENT_COMMERCIAL" and not (
            review is not None and review["custody"] == "CURRENT_REPLAY" and not review["pending"]
        ):
            scale_defer.add("CURRENT_COMPLETE_COMMERCIAL_CUSTODY_REQUIRED")
        scale = inputs["scale"]
        if instant(scale["window"][0]) < clock:
            scale_defer.add("EXPERIMENT_WINDOW_ALREADY_STARTED")
        for name, row in zip(SCENARIOS, calculations["scenarios"]):
            given = scale["scenarios"][name]
            if given["assets"] is None:
                scale_defer.add("MISSING_ASSET_COUNT")
            elif given["assets"] == 0:
                scale_defer.add("EMPTY_EXPERIMENT")
            if given["capacity_assets"] is None:
                scale_defer.add("MISSING_CAPACITY")
            for key, amount in given["costs"].items():
                if amount is None:
                    scale_defer.add("MISSING_" + key.upper())
            value = given["hypothetical_value_per_asset"]
            if scale["economic_basis"] == "HYPOTHETICAL_VALUE_PER_ASSET":
                if value is None:
                    scale_defer.add("MISSING_HYPOTHETICAL_VALUE")
                elif row["hypothetical_unit_contribution"]["value"] is not None and Decimal(row["hypothetical_unit_contribution"]["value"]) <= 0:
                    scale_no.add("NONPOSITIVE_HUMAN_UNIT_CONTRIBUTION")
            elif value is not None:
                scale_defer.add("CONTRADICTORY_COST_ONLY_VALUE_ASSUMPTION")
            for metric, reason in (("capacity_gap", "CAPACITY_LIMIT_EXCEEDED"), ("budget_gap", "AVAILABLE_BUDGET_EXCEEDED"),
                                   ("ceiling_gap", "HUMAN_SPEND_CEILING_EXCEEDED")):
                if row[metric]["value"] is not None and Decimal(row[metric]["value"]) > 0:
                    scale_no.add(reason)
            if policy:
                for metric, cap, reason in (("cash_exposure", "max_downside", "HUMAN_DOWNSIDE_LIMIT_EXCEEDED"),
                    ("unit_cost", "max_unit_cost", "HUMAN_UNIT_COST_LIMIT_EXCEEDED")):
                    if row[metric]["value"] is not None and policy[cap] is not None and Decimal(row[metric]["value"]) > Decimal(policy[cap]):
                        scale_no.add(reason)
                if given["assets"] is not None and policy["max_experiment_assets"] is not None and given["assets"] > policy["max_experiment_assets"]:
                    scale_no.add("HUMAN_EXPERIMENT_SIZE_LIMIT_EXCEEDED")
        for row in sources["costs"]:
            age(row["observed_at"], scale_defer)
            scale_reasons.add("A15_COST_REFERENCE_ONLY_NOT_ATTRIBUTED")
            if row["currency"] not in {None, inputs["currency"]}:
                scale_defer.add("COST_REFERENCE_CURRENCY_MISMATCH_NO_FX")
            if row["status"] in {"PENDING", "UNKNOWN"}:
                scale_reasons.add("UNKNOWN_REFERENCE_COST_NOT_ZERO")

    if inputs["scope"]["product_ref"] is not None:
        if brand is None:
            brand_defer.add("MISSING_NATIVE_M07_BRIEF")
        else:
            source_check(brand, brand_defer, brand_reasons, brand=True)
            source_check(review, brand_defer, brand_reasons, commercial=True)
            if brand["custody"] != "CURRENT_REPLAY":
                brand_defer.add("M07_ORIGINAL_NATIVE_CUSTODY_REQUIRED")
            if brand["pending"] or review is not None and review["pending"]:
                brand_defer.add("PENDING_COMMERCIAL_EVIDENCE_REQUIRES_RECONCILIATION")
            if brand["decision"] == "DEFER":
                brand_defer.add("M07_REQUIRED_INPUTS_UNRESOLVED")
            elif brand["decision"] == "NO_GO":
                brand_no.add("M07_CONSERVATIVE_FEASIBILITY_NO_GO")
            if policy:
                for key, cap, code in (("worst_case_cash_exposure", "max_downside", "M07_DOWNSIDE_EXCEEDS_E07_CAP"),
                                      ("moq_cash_requirement", "available_budget", "M07_CAPITAL_REQUIREMENT_EXCEEDS_E07_BUDGET")):
                    value = brand["feasibility"][key]["value"]
                    if value is None:
                        brand_defer.add("M07_FEASIBILITY_UNKNOWN")
                    elif policy[cap] is not None and Decimal(value) > Decimal(policy[cap]):
                        brand_no.add(code)
                if brand["research_budget"] is None or brand["max_research_units"] is None:
                    brand_defer.add("M07_RESEARCH_LIMITS_UNKNOWN")
                elif policy["spend_ceiling"] is not None and Decimal(brand["research_budget"]) > Decimal(policy["spend_ceiling"]):
                    brand_no.add("M07_RESEARCH_BUDGET_EXCEEDS_E07_CAP")
                if brand["max_research_units"] is not None and policy["max_experiment_assets"] is not None and brand["max_research_units"] > policy["max_experiment_assets"]:
                    brand_no.add("M07_RESEARCH_UNITS_EXCEED_E07_CAP")

    def lane(name, applicable, defer, no, reasons):
        state = "NOT_APPLICABLE" if not applicable else "DEFER" if defer else "DO_NOT_SCALE" if no else "CONDITIONAL_VALIDATE"
        blockers = sorted(defer | no) if applicable else []
        action = ("NONE" if not applicable else "COMPLETE_OR_RECONCILE_INPUTS" if defer else "REVIEW_LIMITS" if no
                  else "CONSIDER_BOUNDED_" + policy["proposed_action"])
        budget, units = (policy["spend_ceiling"], policy["max_experiment_assets"]) if applicable and policy else (None, None)
        if name == "OWN_BRAND" and applicable and brand is not None:
            if budget is not None and brand["research_budget"] is not None:
                budget = min((budget, brand["research_budget"]), key=Decimal)
            if units is not None and brand["max_research_units"] is not None:
                units = min(units, brand["max_research_units"])
        if not applicable:
            reasons = {"CONTENT_ONLY_BRAND_NOT_APPLICABLE" if name == "OWN_BRAND" else "SCALE_VALIDATION_NOT_REQUESTED"}
        return {"lane": name, "state": state, "blocking_reasons": blockers,
            "rationale_codes": sorted(reasons | set(blockers)),
            "alternative_paths": ["MAINTAIN_CURRENT_WITHOUT_COMMITMENT", "GATHER_OFFLINE_EVIDENCE",
                "HUMAN_REVIEW_BOUNDED_VALIDATION"] if applicable else ["CONTINUE_OPTIONAL_CREATIVE_WORK"],
            "human_action": {"kind": action, "budget_ceiling": budget, "unit_ceiling": units,
                "currency": inputs["currency"], "human_decision_required": True, "proposed_only": True,
                "execution_authority": "NONE"}}

    options = [lane("PORTFOLIO_SCALE", inputs["scale"] is not None, scale_defer, scale_no, scale_reasons),
               lane("OWN_BRAND", inputs["scope"]["product_ref"] is not None, brand_defer, brand_no, brand_reasons)]
    states = {r["state"] for r in options}
    decision = ("DEFER" if "DEFER" in states or states == {"NOT_APPLICABLE"} else
                "DO_NOT_SCALE" if "DO_NOT_SCALE" in states else "CONDITIONAL_VALIDATE")
    if states == {"NOT_APPLICABLE"}:
        general.add("NO_OPTION_REQUESTED")
    general.update(code for row in options for code in row["rationale_codes"])
    if human["data_kind"] == "SYNTHETIC_FIXTURE":
        general.add("SYNTHETIC_CALCULATION_AND_RESEARCH_ONLY")
    return {"schema_version": "ScaleBrandDecisionMemo.v1", "evaluator_version": EVALUATOR_VERSION,
        "source_sha": SOURCE_SHA, "request_sha256": request_sha256, "inputs": inputs, "sources": sources,
        "scale_calculations": calculations, "options": options, "decision": decision, "reason_codes": sorted(general),
        "comparison_basis": "INDEPENDENT_LANES_NO_SCORE_NO_RANKING_NO_AUTHORITY_TRANSFER",
        "epistemic_status": {"human_assumptions": "HYPOTHESIS", "native_observations": "LOCAL_DESCRIPTIVE_NOT_PROVIDER_ATTESTED",
            "validated_provider_receipts": "NONE", "verified_demand": False, "actual_revenue": "UNKNOWN",
            "actual_profit": "UNKNOWN", "actual_returns": "UNKNOWN", "actual_fees": "UNKNOWN", "actual_settlement": "UNKNOWN"},
        "archive_validation": "CONTENT_ONLY_NOT_CURRENT_NATIVE_CUSTODY", "limitations": list(LIMITATIONS),
        "execution_authority": "NONE", "financial_commitment": "NONE", "external_live_verified": False}


def _sealed(body):
    sha = digest(body)
    return {**body, "content_sha256": sha, "memo_id": "a18-scale-brand-" + sha}


def validate_memo(value):
    """Replay retained E07 derivation; this does not reopen or renew native custody."""
    value = parse(value) if type(value) is bytes else parse(bounded(value))
    require(validator().is_valid(value), "INVALID_SCALE_BRAND_MEMO")
    body = _derive(value["inputs"], value["sources"], value["request_sha256"])
    require(value == _sealed(body), "MEMO_DERIVATION_OR_ID_MISMATCH")
    return value


@dataclass(frozen=True, slots=True, init=False)
class ScaleBrandDecisionMemo:
    _raw: bytes

    def __init__(self, value):
        object.__setattr__(self, "_raw", canonical_bytes(validate_memo(value)))

    @classmethod
    def from_bytes(cls, raw, *, expected_id=None, expected_sha256=None):
        result = cls(parse(raw))
        require(expected_id is None or result.memo_id == expected_id, "MEMO_ID_PIN_MISMATCH")
        require(expected_sha256 is None or result.sha256 == expected_sha256, "MEMO_BYTES_HASH_PIN_MISMATCH")
        return result

    def to_bytes(self):
        return self._raw

    def to_dict(self):
        return parse(self._raw)

    @property
    def memo_id(self):
        return self.to_dict()["memo_id"]

    @property
    def content_sha256(self):
        return self.to_dict()["content_sha256"]

    @property
    def sha256(self):
        """SHA256 of canonical transport bytes, distinct from the body content ID."""
        return sha256_bytes(self._raw)


def build_memo(request: dict | bytes, sources: ScaleOptionSources = ScaleOptionSources()) -> ScaleBrandDecisionMemo:
    """Build Human-only research advice. Every supplied native handle is reread."""
    request = _request(request)
    return ScaleBrandDecisionMemo(_sealed(_derive(_inputs(request), resolve(sources), digest(request))))
