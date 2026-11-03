"""Pure M07 scenario arithmetic and Human-only research decisions, without effects."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext
import re

from jsonschema import Draft202012Validator
from referencing import Registry

from ai_commerce.contracts.schema import _format_checker
from ai_commerce.core.provenance import canonical_bytes

from .schemas import (
    COST_BASES, EVALUATOR_VERSION, INPUTS, INVENTORY_COSTS, LIMITATIONS, REQUEST,
    SCENARIOS, SOURCE_SHA, OwnBrandError, bounded, digest, instant, parse, require, validator,
)
from .sources import OwnBrandSources, resolve

_REQUEST = Draft202012Validator(REQUEST, registry=Registry(), format_checker=_format_checker())
_INPUTS = Draft202012Validator(INPUTS, registry=Registry(), format_checker=_format_checker())
_QUANTUM = Decimal("0.000001")
_ZERO = Decimal("0")
_TEXT_FIELDS = ("differentiated_offer", "demand_rationale", "supplier_quote")
_TEXT_LISTS = ("counterevidence", "market_unknowns", "supplier_unknowns")


def _request(value):
    value = parse(value, private=True) if type(value) is bytes else parse(bounded(value, private=True), private=True)
    require(_REQUEST.is_valid(value), "INVALID_OWN_BRAND_REQUEST")
    for key in ("product_ref", "account_ref", "measurement_window"):
        value["scope"].setdefault(key, None)
    for name in _TEXT_FIELDS:
        text = value["human"][name]["text"]
        require(text is None or bool(text.strip()), "EMPTY_HUMAN_DECLARATION")
    for name in _TEXT_LISTS:
        rows = value["human"][name]
        require(all(row["text"] is None or bool(row["text"].strip()) for row in rows), "EMPTY_HUMAN_DECLARATION")
        hashes = [digest(row["text"]) for row in rows if row["text"] is not None]
        require(len(hashes) == len(set(hashes)), "DUPLICATE_HUMAN_EVIDENCE")
    _clocks(value)
    return value


def _clocks(inputs):
    window = inputs["scope"]["measurement_window"]
    require(window is None or instant(window[0]) < instant(window[1]), "INVALID_MEASUREMENT_WINDOW")
    policy = inputs["human_policy"]
    require(policy is None or instant(policy["valid_from"]) < instant(policy["valid_until"]), "INVALID_POLICY_WINDOW")


def _inputs(request):
    result = {key: deepcopy(request[key]) for key in ("scope", "currency", "as_of", "human", "human_policy")}

    def receipt(row):
        return {"status": row["status"], "sha256": digest(row["text"]) if row["text"] is not None else None}

    for key in _TEXT_FIELDS:
        result["human"][key] = receipt(request["human"][key])
    for key in _TEXT_LISTS:
        result["human"][key] = sorted((receipt(row) for row in request["human"][key]), key=lambda r: (r["sha256"] or "", r["status"]))
    return result


def _round(value):
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_UP)


def _measure(value, reasons=()):
    return {"value": format(_round(value) + _ZERO, ".6f") if value is not None else None,
            "reason_codes": [] if value is not None else sorted(set(reasons) or {"UNKNOWN_INPUT"})}


def _units(value, reasons=()):
    return {"value": value, "reason_codes": [] if value is not None else sorted(set(reasons) or {"UNKNOWN_INPUT"})}


def _calculations(inputs):
    """All costs are supplied once. No A15/A16 subtotal enters this arithmetic.

    Orders contain the Human-declared units_per_order. Each per-order cost is
    divided and rounded to six places before summing. Freight and outbound
    shipping use the shipping stress; returns/warranty use the returns stress.
    All MOQ inventory is paid and written off within this one-batch horizon.
    Buffer is a reserve, not an expense. No salvage, tax inference or reorder.
    """
    human, currency = inputs["human"], inputs["currency"]
    costs, reasons, values = human["costs"], {}, {}
    for name in COST_BASES:
        row = costs[name]
        codes = set()
        if row["amount"] is None:
            codes.add("MISSING_COST_" + name.upper())
        if row["currency"] != currency:
            codes.add("CURRENCY_MISMATCH_NO_FX")
        reasons[name] = codes
        values[name] = None if codes else Decimal(row["amount"])
    moq, per_order = human["moq"]["value"], human["units_per_order"]["value"]
    quantity_reasons = {"MISSING_MOQ"} if moq is None else set()
    order_reasons = {"MISSING_UNITS_PER_ORDER"} if per_order is None else set()

    def scenario(name):
        given = human["scenarios"][name]
        sales = given["unit_sales"]["value"]
        missing_sales = {"MISSING_" + name.upper() + "_UNIT_SALES"} if sales is None else set()
        bad_sales = {"UNIT_SALES_EXCEED_MOQ"} if sales is not None and moq is not None and sales > moq else set()
        shipping, returns = given["shipping_multiplier"]["value"], given["returns_multiplier"]["value"]
        unit, fixed, component_reasons = {}, {}, set()
        for key in COST_BASES:
            if key in {"sale_price", "available_capital", "buffer"}:
                continue
            amount, basis = values[key], costs[key]["basis"]
            codes = set(reasons[key])
            if key in {"inbound_freight", "outbound_shipping"}:
                if shipping is None:
                    codes.add("MISSING_" + name.upper() + "_SHIPPING_STRESS")
                elif amount is not None:
                    amount = _round(amount * Decimal(shipping))
            if key == "returns_warranty":
                if returns is None:
                    codes.add("MISSING_" + name.upper() + "_RETURNS_STRESS")
                elif amount is not None:
                    amount = _round(amount * Decimal(returns))
            if basis == "PER_ORDER":
                codes.update(order_reasons)
                if amount is not None and per_order is not None:
                    amount = _round(amount / Decimal(per_order))
            if basis == "FIXED":
                fixed[key] = None if codes else amount
                unit[key] = _ZERO
            else:
                unit[key] = None if codes else amount
                fixed[key] = _ZERO
            component_reasons.update(codes)
        unit_reasons = set(reasons["sale_price"])
        for key, amount in unit.items():
            if amount is None:
                unit_reasons.update(reasons[key])
                if costs[key]["basis"] == "PER_ORDER":
                    unit_reasons.update(order_reasons)
                if shipping is None and key in {"inbound_freight", "outbound_shipping"}:
                    unit_reasons.add("MISSING_" + name.upper() + "_SHIPPING_STRESS")
                if returns is None and key == "returns_warranty":
                    unit_reasons.add("MISSING_" + name.upper() + "_RETURNS_STRESS")
        inventory = sum((unit[k] for k in INVENTORY_COSTS), _ZERO) if all(unit[k] is not None for k in INVENTORY_COSTS) else None
        selling = sum((v for k, v in unit.items() if k not in INVENTORY_COSTS), _ZERO) if all(v is not None for k, v in unit.items() if k not in INVENTORY_COSTS) else None
        fixed_total = sum(fixed.values(), _ZERO) if all(v is not None for v in fixed.values()) else None
        fixed_inventory = sum((fixed[k] for k in INVENTORY_COSTS), _ZERO) if all(fixed[k] is not None for k in INVENTORY_COSTS) else None
        price = values["sale_price"]
        contribution = price - inventory - selling if price is not None and inventory is not None and selling is not None else None
        total_reasons = component_reasons | quantity_reasons | missing_sales | bad_sales
        cash_cost = moq * inventory + fixed_total + sales * selling if not total_reasons and None not in (inventory, selling, fixed_total) else None
        revenue = sales * price if sales is not None and price is not None else None
        profit = revenue - cash_cost if revenue is not None and cash_cost is not None else None
        cash_reasons = component_reasons | quantity_reasons | reasons["buffer"]
        cash_requirement = moq * (inventory + selling) + fixed_total + values["buffer"] if not cash_reasons and None not in (inventory, selling, fixed_total) else None
        no_sale_loss = moq * inventory + fixed_total if not component_reasons | quantity_reasons and None not in (inventory, fixed_total) else None
        exposure = moq * inventory + fixed_inventory if not component_reasons | quantity_reasons and None not in (inventory, fixed_inventory) else None
        result = {"name": name, "unit_sales": sales,
            "gross_contribution_per_unit": _measure(contribution, unit_reasons),
            "revenue": _measure(revenue, missing_sales | reasons["sale_price"]),
            "cash_cost": _measure(cash_cost, total_reasons),
            "profit_or_loss": _measure(profit, total_reasons | reasons["sale_price"]),
            "loss": _measure(max(-profit, _ZERO) if profit is not None else None, total_reasons | reasons["sale_price"]),
            "unsold_units": _units(moq - sales if moq is not None and sales is not None and not bad_sales else None,
                                   quantity_reasons | missing_sales | bad_sales),
            "cash_requirement": _measure(cash_requirement, cash_reasons)}
        return result, {"contribution": contribution, "inventory": inventory, "selling": selling,
            "fixed": fixed_total, "no_sale_loss": no_sale_loss, "exposure": exposure,
            "cash_requirement": cash_requirement, "reasons": component_reasons | quantity_reasons,
            "unit_reasons": unit_reasons}

    with localcontext(Context(prec=80, rounding=ROUND_HALF_UP, Emin=-999, Emax=999)):
        derived = [scenario(name) for name in SCENARIOS]
        base, bear = derived[1][1], derived[0][1]
        contribution = base["contribution"]
        break_reasons = set(base["reasons"]) | reasons["sale_price"]
        if contribution is not None and contribution <= 0:
            break_reasons.add("NONPOSITIVE_BREAK_EVEN_DENOMINATOR")
        break_even = int((base["fixed"] / contribution).to_integral_value(rounding=ROUND_CEILING)) if contribution is not None and contribution > 0 and base["fixed"] is not None else None
        denominator = values["sale_price"] - base["selling"] if values["sale_price"] is not None and base["selling"] is not None else None
        batch_reasons = set(break_reasons)
        if denominator is not None and denominator <= 0:
            batch_reasons.add("NONPOSITIVE_BATCH_BREAK_EVEN_DENOMINATOR")
        batch_even = int((base["no_sale_loss"] / denominator).to_integral_value(rounding=ROUND_CEILING)) if denominator is not None and denominator > 0 and base["no_sale_loss"] is not None else None
        all_cash = [row[1]["cash_requirement"] for row in derived]
        gap_reasons = set().union(*(row[1]["reasons"] for row in derived), reasons["buffer"], reasons["available_capital"])
        capital_gap = max(max(all_cash) - values["available_capital"], _ZERO) if all(v is not None for v in all_cash) and values["available_capital"] is not None else None
        worst_loss = None
        if bear["no_sale_loss"] is not None and bear["contribution"] is not None:
            # Include the full-sell stress: selling can deepen a loss when its
            # marginal expense exceeds price. Unsold stock has zero salvage.
            full_sale_loss = moq * (bear["inventory"] + bear["selling"] - values["sale_price"]) + bear["fixed"]
            worst_loss = max(bear["no_sale_loss"], full_sale_loss, _ZERO)
        worst_cash = worst_loss + values["buffer"] if worst_loss is not None and values["buffer"] is not None else None
        return {"rounding": "DECIMAL_80_DIGITS_HALF_UP_6_PLACES_AT_UNIT_COST_AND_RESULT",
            "inventory_policy": "ONE_MOQ_BATCH_NO_SALVAGE_UNSOLD_WRITTEN_OFF_NO_REORDER",
            "buffer_policy": "RESERVE_IN_CASH_REQUIREMENT_EXCLUDED_FROM_PROFIT_OR_LOSS",
            "a15_a16_allocation": "REFERENCE_ONLY_NO_COSTS_OR_PROFIT_IMPORTED",
            "gross_contribution_per_unit": derived[1][0]["gross_contribution_per_unit"],
            "moq_cash_requirement": derived[1][0]["cash_requirement"],
            "break_even_units": _units(break_even, break_reasons),
            "batch_break_even_units": _units(batch_even, batch_reasons),
            "worst_case_inventory_exposure": _measure(bear["exposure"], bear["reasons"]),
            "worst_case_loss": _measure(worst_loss, bear["reasons"] | reasons["sale_price"]),
            "worst_case_cash_exposure": _measure(worst_cash, bear["reasons"] | reasons["sale_price"] | reasons["buffer"]),
            "capital_gap": _measure(capital_gap, gap_reasons), "scenarios": [row[0] for row in derived]}


def _source_checks(sources):
    """Serialized projection checks do not re-attest original native custody."""
    bounded(sources, private=True)
    portfolio, review = sources["portfolio"], sources["commercial_review"]
    if portfolio is not None:
        ref = portfolio["reference"]
        require(re.fullmatch(r"a18-portfolio-[a-f0-9]{64}", ref["recommendation_id"]) is not None,
                "INVALID_PORTFOLIO_REFERENCE")
        require(portfolio["reference_sha256"] == digest(ref)
                and ref["candidate_id"] == portfolio["scope"]["concept_ref"]
                and ref["as_of"] in portfolio["observed_timestamps"], "PORTFOLIO_REFERENCE_MISMATCH")
        rows = portfolio["native_sources"]
        require(rows == sorted(rows, key=lambda row: (row["kind"], row["source_id"]))
                and len({r["source_id"] for r in rows}) == len(rows)
                and len({r["sha256"] for r in rows}) == len(rows), "DUPLICATE_NATIVE_EVIDENCE")
        require(all(r["source_id"] in {row["source_id"] for row in rows} for r in portfolio["counterevidence"]),
                "COUNTEREVIDENCE_REFERENCE_MISMATCH")
    if review is not None:
        require(re.fullmatch(r"a16-review-[a-f0-9]{64}", review["review_id"]) is not None, "INVALID_COMMERCIAL_REFERENCE")
        refs = review["native_refs"]
        require(refs == sorted(refs, key=lambda row: (row["ref"], row["sha256"]))
                and len({r["ref"] for r in refs}) == len(refs)
                and {"commerce_run", "settlement_history"} <= {r["ref"] for r in refs}, "COMMERCIAL_NATIVE_REFERENCES_MISMATCH")
        if portfolio is not None:
            require(all(row["sha256"] == review["sha256"] for row in portfolio["native_sources"]
                        if row["source_id"] == review["review_id"]), "CROSS_SOURCE_VERSION_MISMATCH")
            costs = {row["ref"]: row["sha256"] for row in refs if row["ref"].startswith("cost:")}
            require(all(row["sha256"] == costs["cost:" + row["source_id"]]
                        for row in portfolio["native_sources"]
                        if row["kind"] == "COST" and "cost:" + row["source_id"] in costs),
                    "CROSS_SOURCE_COST_VERSION_MISMATCH")
    for receipt in (portfolio, review):
        if receipt is None:
            continue
        for key in ("source_codes", "observed_timestamps"):
            require(receipt[key] == sorted(set(receipt[key])), "NONCANONICAL_SOURCE_PROJECTION")
        window = receipt["scope"]["measurement_window"]
        require(instant(window[0]) < instant(window[1]), "INVALID_NATIVE_WINDOW")


def _derive(inputs, sources, request_sha256):
    bounded(inputs, private=True)
    require(_INPUTS.is_valid(inputs), "INVALID_OWN_BRAND_INPUT_PROJECTION")
    _clocks(inputs)
    _source_checks(sources)
    calculations = _calculations(inputs)
    human, policy, clock = inputs["human"], inputs["human_policy"], instant(inputs["as_of"])
    reasons = {"OFFLINE_UNATTESTED", "HUMAN_ASSUMPTIONS_NOT_MARKET_FACTS", "NO_PURCHASE_OR_STOCK_AUTHORITY"}
    defer, no_go, checklist, counters = set(), set(), set(), set()
    checklist.update({"VALIDATE_DEMAND_WITHOUT_CAUSAL_CLAIM", "VALIDATE_DIFFERENTIATION", "VALIDATE_SUPPLIER_AND_QUOTE",
        "VALIDATE_RIGHTS_AND_CERTIFICATION", "VALIDATE_LOGISTICS_AND_LEAD_TIME", "VALIDATE_SHIPPING_RETURNS_AND_WARRANTY",
        "VALIDATE_ALL_COST_BASES_AND_EXPLICIT_ZEROS", "RECONCILE_PENDING_REFUNDS_AND_FINANCIAL_EVIDENCE",
        "HUMAN_APPROVE_ANY_CAPITAL_INVENTORY_OR_VENDOR_OUTREACH"})
    if inputs["scope"]["market"] is None:
        defer.add("MISSING_MARKET")
    if instant(human["declared_at"]) > clock:
        defer.add("FUTURE_HUMAN_ASSUMPTIONS")
    if inputs["scope"]["measurement_window"] is not None and instant(inputs["scope"]["measurement_window"][1]) > clock:
        defer.add("FUTURE_MEASUREMENT_WINDOW")
    for field in ("differentiated_offer", "demand_rationale"):
        if human[field]["sha256"] is None:
            defer.add("MISSING_" + field.upper())
    for field in ("moq", "units_per_order", "logistics_lead_time_days"):
        if human[field]["value"] is None:
            defer.add("MISSING_" + field.upper())
    for name in COST_BASES:
        row = human["costs"][name]
        if row["amount"] is None:
            defer.add("MISSING_COST_" + name.upper())
        if row["currency"] != inputs["currency"]:
            defer.add("CURRENCY_MISMATCH_NO_FX")
    for name in SCENARIOS:
        given = human["scenarios"][name]
        for field in ("unit_sales", "shipping_multiplier", "returns_multiplier"):
            if given[field]["value"] is None:
                defer.add("MISSING_" + name.upper() + "_" + field.upper())
        sales = given["unit_sales"]["value"]
        if sales is not None and human["moq"]["value"] is not None and sales > human["moq"]["value"]:
            defer.add("UNIT_SALES_EXCEED_MOQ")
    declared = human["scenarios"]
    for field in ("unit_sales", "shipping_multiplier", "returns_multiplier"):
        values = [declared[name][field]["value"] for name in SCENARIOS]
        if all(v is not None for v in values):
            values = [Decimal(v) for v in values]
            if (field == "unit_sales" and values != sorted(values)) or (field != "unit_sales" and values != sorted(values, reverse=True)):
                defer.add("SCENARIOS_NOT_CONSERVATIVE_OR_ORDERED")
    for row in human["counterevidence"]:
        if row["sha256"] is not None:
            counters.add(("human-" + row["sha256"], "HUMAN_UNRESOLVED_COUNTEREVIDENCE"))
            defer.add("UNRESOLVED_HUMAN_COUNTEREVIDENCE")
    max_age = policy["max_evidence_age_seconds"] if policy else 2592000

    def age(when):
        elapsed = (clock - instant(when)).total_seconds()
        if elapsed < 0:
            defer.add("FUTURE_EVIDENCE")
        elif elapsed > max_age:
            defer.add("STALE_EVIDENCE")

    age(human["declared_at"])

    def scope_check(actual):
        partial = False
        for key in ("campaign_ref", "concept_ref", "product_ref", "market", "account_ref", "measurement_window"):
            wanted, got = inputs["scope"][key], actual[key]
            if wanted is not None and got is not None and wanted != got:
                defer.add("SOURCE_" + key.upper() + "_MISMATCH")
            elif wanted != got:
                partial = True
        if partial:
            reasons.add("PARTIAL_SCOPE_NO_AUTOMATIC_JOIN")

    portfolio, review = sources["portfolio"], sources["commercial_review"]
    synthetic, archived, observations = human["data_kind"] == "SYNTHETIC_FIXTURE", False, 0
    if portfolio is not None:
        scope_check(portfolio["scope"])
        if portfolio["currency"] != inputs["currency"]:
            defer.add("SOURCE_CURRENCY_MISMATCH_NO_FX")
        archived |= portfolio["custody"] == "ARCHIVED_ONLY"
        if archived:
            reasons.add("PORTFOLIO_ARCHIVED_ONLY_NO_RENEWED_AUTHORITY")
        else:
            observations = portfolio["current_observation_count"]
        reasons.add("PORTFOLIO_PROVISIONAL_DESCRIPTIVE_ONLY")
        for when in portfolio["observed_timestamps"]:
            age(when)
        codes = portfolio["source_codes"]
        for code in codes:
            if "MISMATCH" in code or code in {"STALE_EVIDENCE", "FUTURE_EVIDENCE", "POLICY_OUTSIDE_VALIDITY", "EVALUATION_WINDOW_FUTURE"}:
                defer.add("M06_" + code)
        if portfolio["evidence_quality"] == "CONFLICTED":
            defer.add("CONFLICTED_PORTFOLIO_EVIDENCE")
        for row in portfolio["counterevidence"]:
            counters.add((row["source_id"], row["code"]))
            defer.add("UNRESOLVED_NATIVE_COUNTEREVIDENCE")
        for row in portfolio["native_sources"]:
            synthetic |= row["data_kind"] in {"SYNTHETIC_FIXTURE", "MIXED"}
            if row["scope_link"] == "MISMATCH":
                defer.add("NATIVE_SOURCE_SCOPE_MISMATCH")
            # M06 has no projected timestamp for these source interiors.
            # Replaying its ORIGINAL clock cannot refresh their observation age.
            if row["kind"] in {"PROPOSAL", "ADVISORY", "REVIEW"} and (
                instant(portfolio["reference"]["as_of"]) != clock
                or max_age < portfolio["native_max_evidence_age_seconds"]
            ):
                defer.add("PORTFOLIO_INTERIOR_FRESHNESS_NOT_RENEWED")
    if review is not None:
        scope_check(review["scope"])
        if any(currency != inputs["currency"] for currency in review["currencies"]):
            defer.add("SOURCE_CURRENCY_MISMATCH_NO_FX")
        archived |= review["custody"] == "ARCHIVED_ONLY"
        synthetic |= review["data_kind"] in {"SYNTHETIC_FIXTURE", "MIXED"}
        reasons.add("A16_PAID_ORDERS_SETTLEMENT_PROFIT_UNKNOWN")
        reasons.add("A15_A16_COSTS_REFERENCE_ONLY")
        for when in review["observed_timestamps"]:
            age(when)
        if review["status"] == "HOLD":
            counters.add((review["review_id"], "A16_UPSTREAM_HOLD"))
            defer.add("A16_UPSTREAM_HOLD")
        if review["refund_return_present"]:
            counters.add((review["review_id"], "SETTLEMENT_REFUND_OR_REVOCATION"))
            defer.add("UNRESOLVED_REFUND_OR_REVOCATION")
        if review["pending_financial_evidence"]:
            reasons.add("PENDING_FINANCIAL_EVIDENCE_NOT_ZERO")
    if synthetic:
        reasons.add("SYNTHETIC_NON_ATTESTED_RESEARCH_ONLY")
    if policy is None:
        defer.add("MISSING_HUMAN_POLICY")
    else:
        risk, budget, unit_ceiling = policy["max_downside"], policy["research_budget"], policy["max_research_units"]
        if risk is None:
            defer.add("MISSING_HUMAN_RISK_TOLERANCE")
        if budget is None:
            defer.add("MISSING_RESEARCH_BUDGET")
        if unit_ceiling is None:
            defer.add("MISSING_RESEARCH_UNIT_CEILING")
        if not instant(policy["valid_from"]) <= clock < instant(policy["valid_until"]):
            defer.add("POLICY_OUTSIDE_VALIDITY")
        if policy["currency"] != inputs["currency"]:
            defer.add("POLICY_CURRENCY_MISMATCH_NO_FX")
        if synthetic and not policy["allow_synthetic_research"]:
            defer.add("SYNTHETIC_RESEARCH_NOT_PERMITTED_BY_HUMAN_POLICY")
        if not policy["allow_hypothesis_only_research"] and not (
            portfolio is not None and portfolio["custody"] == "CURRENT_REPLAY" and observations > 0
            and any(row["kind"] == "REPORT" and row["custody"] == "NATIVE_REPLAY"
                    and row["scope_link"] == "MATCHED" for row in portfolio["native_sources"])):
            defer.add("CURRENT_COMPARABLE_DESCRIPTIVE_EVIDENCE_REQUIRED")
        for scenario in calculations["scenarios"]:
            contribution = scenario["gross_contribution_per_unit"]["value"]
            if contribution is not None and Decimal(contribution) <= 0:
                no_go.add("NONPOSITIVE_CONSERVATIVE_UNIT_CONTRIBUTION")
        worst = calculations["worst_case_cash_exposure"]["value"]
        if worst is not None and risk is not None and Decimal(worst) > Decimal(risk):
            no_go.add("DOWNSIDE_EXCEEDS_HUMAN_RISK_TOLERANCE")
        gap = calculations["capital_gap"]["value"]
        if gap is not None and Decimal(gap) > 0:
            no_go.add("INSUFFICIENT_CAPITAL_FOR_CONSERVATIVE_MOQ_SCENARIO")
        capital = human["costs"]["available_capital"]
        if capital["amount"] is not None and capital["currency"] == inputs["currency"] and budget is not None and risk is not None:
            if Decimal(budget) > min(Decimal(capital["amount"]), Decimal(risk)):
                no_go.add("RESEARCH_BUDGET_EXCEEDS_CAPITAL_OR_RISK_LIMIT")
        if policy["proposed_action"] != "RESEARCH" and budget is not None and Decimal(budget) <= 0:
            defer.add("POSITIVE_VALIDATION_BUDGET_REQUIRED")
        if human["moq"]["value"] is not None and unit_ceiling is not None and unit_ceiling > human["moq"]["value"]:
            defer.add("RESEARCH_UNIT_CEILING_EXCEEDS_SINGLE_BATCH")
    decision = "DEFER" if defer else "NO_GO" if no_go else "CONDITIONAL_GO"
    blocking = defer | no_go
    reasons.update(blocking)
    if decision == "CONDITIONAL_GO":
        reasons.add("COMPLETE_BOUNDED_HUMAN_RESEARCH_POLICY_AND_SCENARIOS")
    checklist.update(defer)
    conflicted = bool(counters) or any("MISMATCH" in c or "STALE" in c or "CONFLICT" in c for c in defer)
    grade = ("CONFLICTED_UNATTESTED" if conflicted else "SYNTHETIC_NON_ATTESTED" if synthetic else
        "ARCHIVED_UNATTESTED" if archived else "USER_SUPPLIED_DESCRIPTIVE_UNATTESTED" if observations else "HYPOTHESIS_ONLY_UNATTESTED")
    action = ("COMPLETE_OR_RECONCILE_BRIEF" if decision == "DEFER" else "REVIEW_NO_GO" if decision == "NO_GO"
              else "CONSIDER_BOUNDED_" + policy["proposed_action"])
    return {"schema_version": "OwnBrandDecisionBrief.v1", "evaluator_version": EVALUATOR_VERSION,
        "source_sha": SOURCE_SHA, "request_sha256": request_sha256, "inputs": inputs, "sources": sources,
        "calculations": calculations, "decision": decision,
        "decision_meaning": "HUMAN_REVIEW_OF_BOUNDED_VALIDATION_ONLY", "reason_codes": sorted(reasons),
        "blocking_reasons": sorted(blocking), "evidence_grade": grade,
        "confidence_meaning": "EVIDENCE_RATING_NOT_PROBABILITY",
        "demand": {"rationale": human["demand_rationale"], "status": "HYPOTHESIS_NOT_PROVEN",
            "descriptive_observation_count": observations, "observed_market_demand": "UNKNOWN", "proven_demand": False},
        "unknowns": ["MARKET_SIZE", "SUPPLIER_AUTHENTICITY", "QUOTE_VALIDITY", "RIGHTS", "CERTIFICATION",
            "LOGISTICS_RELIABILITY", "RETURN_RATE", "LIVE_PRICE", "PAID_DEMAND"],
        "counterevidence": [{"source_id": ref, "code": code} for ref, code in sorted(counters)],
        "human_validation_checklist": sorted(checklist),
        "human_action": {"kind": action, "proposed_only": True,
            "budget_ceiling": policy["research_budget"] if policy else None, "currency": inputs["currency"],
            "unit_ceiling": policy["max_research_units"] if policy else None, "human_decision_required": True,
            "execution_authority": "NONE"}, "limitations": list(LIMITATIONS),
        "execution_authority": "NONE", "financial_commitment": "NONE", "external_live_verified": False}


def validate_brief(value):
    """Replay retained arithmetic and decision, without renewing native authority."""
    value = parse(bounded(value))
    require(validator().is_valid(value), "INVALID_OWN_BRAND_BRIEF")
    body = _derive(value["inputs"], value["sources"], value["request_sha256"])
    require(value == {**body, "brief_id": "a18-own-brand-" + digest(body)}, "OWN_BRAND_DERIVATION_OR_ID_MISMATCH")
    return value


@dataclass(frozen=True, slots=True, init=False)
class OwnBrandDecisionBrief:
    _raw: bytes

    def __init__(self, value):
        object.__setattr__(self, "_raw", canonical_bytes(validate_brief(value)))

    @classmethod
    def from_bytes(cls, raw, *, expected_id=None):
        """Archived validation only; call build_brief again for native rereads."""
        result = cls(parse(raw))
        require(expected_id is None or result.brief_id == expected_id, "BRIEF_PIN_MISMATCH")
        return result

    def to_bytes(self):
        return self._raw

    def to_dict(self):
        return parse(self._raw)

    @property
    def brief_id(self):
        return self.to_dict()["brief_id"]


def build_brief(request: dict | bytes, sources: OwnBrandSources = OwnBrandSources()) -> OwnBrandDecisionBrief:
    """Build one offline brief. Human controls every capital/inventory decision."""
    request = _request(request)
    body = _derive(_inputs(request), resolve(sources), digest(request))
    return OwnBrandDecisionBrief({**body, "brief_id": "a18-own-brand-" + digest(body)})
