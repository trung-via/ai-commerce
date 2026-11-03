"""M07 deterministic research-only utility; all fixtures are non-attested."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Inexact, localcontext
from hashlib import sha256
import http.client
import json
from pathlib import Path
import socket
import urllib.request

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.opportunity.own_brand import (
    OwnBrandDecisionBrief, OwnBrandError, OwnBrandSources, build_brief, schema, validate_brief,
)
from ai_commerce.opportunity.own_brand.schemas import COST_BASES, SOURCE_SHA, digest, parse

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("M07 attempted network access")
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", deny)


def graded(value, *, status="ESTIMATED_USER_SUPPLIED", field="value"):
    return {"status": "UNKNOWN" if value is None else status, field: value}


def text(value):
    return graded(value, status="USER_SUPPLIED", field="text")


def request(*, synthetic=True):
    prices = {"cogs": "4", "sale_price": "15", "inbound_freight": "1", "outbound_shipping": "2",
        "fulfillment": "1", "storage": "0.25", "returns_warranty": "0.5", "setup": "100",
        "certification": "50", "marketing": "1", "fees": "0.75", "buffer": "50", "available_capital": "2000"}
    costs = {key: {**graded(value, field="amount"), "currency": "USD", "basis": COST_BASES[key][0]}
             for key, value in prices.items()}
    costs["outbound_shipping"]["basis"] = costs["fulfillment"]["basis"] = "PER_ORDER"
    return {"schema_version": "OwnBrandEvaluation.v1",
        "scope": {"campaign_ref": "fixture-campaign", "concept_ref": "fixture-concept", "market": "VN",
                  "product_ref": None, "account_ref": None, "measurement_window": None},
        "currency": "USD", "as_of": "2026-11-03T00:00:00Z",
        "human": {"authored_by": "HUMAN_DECLARED_NOT_AUTHENTICATED", "declared_at": "2026-11-02T00:00:00Z",
            "data_kind": "SYNTHETIC_FIXTURE" if synthetic else "USER_SUPPLIED",
            "differentiated_offer": text("Fictional easy-clean design for a research prototype"),
            "demand_rationale": text("Hypothesis from Human notes requiring market validation"),
            "supplier_quote": text("Fictional unverified quote used only for arithmetic"),
            "counterevidence": [], "market_unknowns": [text("Addressable audience unknown")],
            "supplier_unknowns": [text("Supplier capability unknown")],
            "moq": graded(100), "units_per_order": graded(2), "logistics_lead_time_days": graded(30),
            "costs": costs,
            "scenarios": {name: {"unit_sales": graded(sales), "shipping_multiplier": graded(stress),
                                  "returns_multiplier": graded(stress)}
                          for name, sales, stress in (("bear", 20, "2"), ("base", 60, "1"), ("bull", 100, "1"))}},
        "human_policy": {"schema_version": "OwnBrandHumanPolicy.v1", "policy_id": "fixture-policy",
            "authored_by": "HUMAN_DECLARED_NOT_AUTHENTICATED", "valid_from": "2026-11-01T00:00:00Z",
            "valid_until": "2026-12-01T00:00:00Z", "currency": "USD", "max_evidence_age_seconds": 2592000,
            "allow_hypothesis_only_research": True, "allow_synthetic_research": True,
            "max_downside": "1000", "research_budget": "50", "max_research_units": 2,
            "proposed_action": "SAMPLE_VALIDATION"}}


def metric(value, name):
    return value["calculations"][name]["value"]


def reseal(value):
    value = deepcopy(value)
    value["brief_id"] = "a18-own-brand-" + digest({k: v for k, v in value.items() if k != "brief_id"})
    return value


def test_positive_conditional_arithmetic_is_only_non_attested_sample_research():
    value = build_brief(request()).to_dict()
    assert value["decision"] == "CONDITIONAL_GO" and value["blocking_reasons"] == []
    assert value["human_action"] == {"kind": "CONSIDER_BOUNDED_SAMPLE_VALIDATION", "budget_ceiling": "50",
        "currency": "USD", "unit_ceiling": 2, "proposed_only": True, "human_decision_required": True, "execution_authority": "NONE"}
    assert metric(value, "gross_contribution_per_unit") == "6.000000"
    assert metric(value, "moq_cash_requirement") == "1100.000000"
    assert metric(value, "break_even_units") == 25
    assert metric(value, "batch_break_even_units") == 60
    assert metric(value, "worst_case_inventory_exposure") == "625.000000"
    assert metric(value, "worst_case_loss") == "775.000000"
    assert metric(value, "worst_case_cash_exposure") == "825.000000"
    assert metric(value, "capital_gap") == "0.000000"
    bear, base, bull = value["calculations"]["scenarios"]
    assert [row["unit_sales"] for row in (bear, base, bull)] == [20, 60, 100]
    assert [row["profit_or_loss"]["value"] for row in (bear, base, bull)] == ["-580.000000", "0.000000", "450.000000"]
    assert [row["loss"]["value"] for row in (bear, base, bull)] == ["580.000000", "0.000000", "0.000000"]
    assert [row["unsold_units"]["value"] for row in (bear, base, bull)] == [80, 40, 0]
    assert bear["gross_contribution_per_unit"]["value"] == "3.500000"
    assert value["evidence_grade"] == "SYNTHETIC_NON_ATTESTED"
    assert value["demand"]["proven_demand"] is False and value["demand"]["observed_market_demand"] == "UNKNOWN"
    assert value["financial_commitment"] == value["execution_authority"] == "NONE"
    assert value["external_live_verified"] is False


@pytest.mark.parametrize("action", ["RESEARCH", "PROTOTYPE", "SAMPLE_VALIDATION"])
def test_each_positive_path_remains_bounded_human_only(action):
    body = request(synthetic=False)
    body["human_policy"]["proposed_action"] = action
    value = build_brief(body).to_dict()
    assert value["decision"] == "CONDITIONAL_GO"
    assert value["human_action"]["kind"] == "CONSIDER_BOUNDED_" + action
    assert value["evidence_grade"] == "HYPOTHESIS_ONLY_UNATTESTED"
    assert value["confidence_meaning"] == "EVIDENCE_RATING_NOT_PROBABILITY"


def test_creative_only_product_account_offer_and_sku_are_not_required():
    body = request()
    for key in ("product_ref", "account_ref", "measurement_window"):
        body["scope"].pop(key)
    value = build_brief(body)
    assert value.to_bytes() == build_brief(request()).to_bytes()
    assert value.to_dict()["inputs"]["scope"]["product_ref"] is None
    assert value.to_dict()["decision"] == "CONDITIONAL_GO"
    body["human"]["supplier_quote"] = text(None)
    assert build_brief(body).to_dict()["decision"] == "CONDITIONAL_GO"


def test_defer_is_not_a_global_creative_or_product_gate():
    body = request()
    body["human"]["moq"] = graded(None)
    value = build_brief(body).to_dict()
    assert value["decision"] == "DEFER" and "MISSING_MOQ" in value["blocking_reasons"]
    assert metric(value, "moq_cash_requirement") is None
    assert metric(value, "gross_contribution_per_unit") == "6.000000"
    assert value["human_action"]["kind"] == "COMPLETE_OR_RECONCILE_BRIEF"


@pytest.mark.parametrize("cost", list(COST_BASES))
def test_every_cost_missing_is_explicit_unknown_never_imputed_zero(cost):
    body = request()
    body["human"]["costs"][cost].update(status="UNKNOWN", amount=None)
    value = build_brief(body).to_dict()
    assert value["decision"] == "DEFER"
    assert "MISSING_COST_" + cost.upper() in value["blocking_reasons"]
    assert value["inputs"]["human"]["costs"][cost]["amount"] is None
    assert "VALIDATE_ALL_COST_BASES_AND_EXPLICIT_ZEROS" in value["human_validation_checklist"]


@pytest.mark.parametrize("amount,decision", [("0", "NO_GO"), ("100", "NO_GO"), ("1349.999999", "NO_GO"), ("1350", "CONDITIONAL_GO")])
def test_precise_conservative_capital_boundary_and_zero(amount, decision):
    body = request()
    body["human"]["costs"]["available_capital"]["amount"] = amount
    value = build_brief(body).to_dict()
    assert value["decision"] == decision
    assert metric(value, "capital_gap") is not None
    if decision == "NO_GO":
        assert "INSUFFICIENT_CAPITAL_FOR_CONSERVATIVE_MOQ_SCENARIO" in value["blocking_reasons"]


@pytest.mark.parametrize("tolerance,decision", [("824.999999", "NO_GO"), ("825", "CONDITIONAL_GO")])
def test_worst_case_risk_boundary_uses_no_sale_inventory_and_cash_buffer(tolerance, decision):
    body = request()
    body["human_policy"]["max_downside"] = tolerance
    value = build_brief(body).to_dict()
    assert value["decision"] == decision
    if decision == "NO_GO":
        assert "DOWNSIDE_EXCEEDS_HUMAN_RISK_TOLERANCE" in value["blocking_reasons"]


@pytest.mark.parametrize("price", ["0", "3", "9"])
def test_negative_or_zero_unit_economics_are_no_go_and_breakeven_undefined(price):
    body = request()
    body["human"]["costs"]["sale_price"]["amount"] = price
    value = build_brief(body).to_dict()
    assert value["decision"] == "NO_GO"
    assert "NONPOSITIVE_CONSERVATIVE_UNIT_CONTRIBUTION" in value["blocking_reasons"]
    assert metric(value, "break_even_units") is None
    assert "NONPOSITIVE_BREAK_EVEN_DENOMINATOR" in value["calculations"]["break_even_units"]["reason_codes"]


@pytest.mark.parametrize("field", ["shipping_multiplier", "returns_multiplier"])
def test_shipping_or_returns_stress_can_destroy_contribution(field):
    body = request()
    body["human"]["scenarios"]["bear"][field]["value"] = "50"
    value = build_brief(body).to_dict()
    assert value["decision"] == "NO_GO"
    assert "NONPOSITIVE_CONSERVATIVE_UNIT_CONTRIBUTION" in value["reason_codes"]
    assert value["calculations"]["scenarios"][0]["loss"]["value"] is not None


def test_fixed_cost_bases_and_per_order_quantity_not_double_counted():
    body = request()
    body["human"]["costs"]["inbound_freight"].update(basis="FIXED", amount="100")
    body["human"]["costs"]["marketing"].update(basis="FIXED", amount="100")
    value = build_brief(body).to_dict()
    assert metric(value, "gross_contribution_per_unit") == "8.000000"
    assert metric(value, "moq_cash_requirement") == "1100.000000"
    assert metric(value, "break_even_units") == 44
    assert metric(value, "worst_case_inventory_exposure") == "625.000000"
    assert value["calculations"]["scenarios"][1]["cash_cost"]["value"] == "940.000000"


def test_decimal_rounding_independent_of_global_precision_and_no_binary_float():
    body = request()
    body["human"]["units_per_order"]["value"] = 3
    with localcontext() as context:
        context.prec = 6
        first = build_brief(body)
    with localcontext() as context:
        context.prec = 120
        second = build_brief(body)
    assert first.to_bytes() == second.to_bytes()
    assert metric(first.to_dict(), "gross_contribution_per_unit") == "6.500000"
    assert metric(first.to_dict(), "moq_cash_requirement") == "1050.000000"
    assert b"NaN" not in first.to_bytes() and b"Infinity" not in first.to_bytes()


def test_decimal_exponent_limits_and_inexact_traps_do_not_change_the_result():
    body = request()
    body["human"]["units_per_order"]["value"] = 3
    expected = build_brief(body)
    with localcontext() as context:
        context.Emin, context.Emax = -2, 2
        context.traps[Inexact] = True
        assert build_brief(body).to_bytes() == expected.to_bytes()


@pytest.mark.parametrize("field,reason", [("research_budget", "MISSING_RESEARCH_BUDGET"),
    ("max_downside", "MISSING_HUMAN_RISK_TOLERANCE"), ("max_research_units", "MISSING_RESEARCH_UNIT_CEILING")])
def test_missing_human_budget_risk_or_unit_ceiling_defers_instead_of_assuming_zero(field, reason):
    body = request()
    body["human_policy"][field] = None
    value = build_brief(body).to_dict()
    assert value["decision"] == "DEFER" and reason in value["blocking_reasons"]


@pytest.mark.parametrize("basis", ["PER_ORDER", "FIXED", "PER_CAMPAIGN"])
def test_estimated_cogs_and_sale_price_require_explicit_per_unit_basis(basis):
    body = request()
    body["human"]["costs"]["cogs"]["basis"] = basis
    with pytest.raises(OwnBrandError, match="INVALID_OWN_BRAND_REQUEST"):
        build_brief(body)


def test_explicit_zero_cost_and_zero_sales_are_known_not_missing():
    body = request()
    for name, row in body["human"]["costs"].items():
        if name not in {"sale_price", "available_capital"}:
            row["amount"] = "0"
    for given in body["human"]["scenarios"].values():
        given["unit_sales"]["value"] = 0
    value = build_brief(body).to_dict()
    assert metric(value, "moq_cash_requirement") == metric(value, "worst_case_cash_exposure") == "0.000000"
    assert metric(value, "break_even_units") == 0
    assert value["calculations"]["scenarios"][0]["revenue"] == {"value": "0.000000", "reason_codes": []}
    assert value["decision"] == "CONDITIONAL_GO" and not value["demand"]["proven_demand"]


@pytest.mark.parametrize("name", ["bear", "base", "bull"])
@pytest.mark.parametrize("field", ["unit_sales", "shipping_multiplier", "returns_multiplier"])
def test_unknown_scenario_component_defers_with_exact_field(name, field):
    body = request()
    body["human"]["scenarios"][name][field] = graded(None)
    value = build_brief(body).to_dict()
    assert value["decision"] == "DEFER"
    assert "MISSING_" + name.upper() + "_" + field.upper() in value["blocking_reasons"]


@pytest.mark.parametrize("mutation,code", [
    (lambda b: b["human"]["scenarios"]["bull"]["unit_sales"].update(value=101), "UNIT_SALES_EXCEED_MOQ"),
    (lambda b: b["human"]["scenarios"]["bear"]["unit_sales"].update(value=90), "SCENARIOS_NOT_CONSERVATIVE_OR_ORDERED"),
    (lambda b: b["human"]["scenarios"]["bear"]["shipping_multiplier"].update(value="0"), "SCENARIOS_NOT_CONSERVATIVE_OR_ORDERED"),
    (lambda b: b["human_policy"].update(currency="EUR"), "POLICY_CURRENCY_MISMATCH_NO_FX"),
    (lambda b: b["human"]["costs"]["cogs"].update(currency="EUR"), "CURRENCY_MISMATCH_NO_FX"),
    (lambda b: b["human_policy"].update(valid_until="2026-11-02T00:00:00Z"), "POLICY_OUTSIDE_VALIDITY"),
    (lambda b: b["human"].update(declared_at="2026-11-04T00:00:00Z"), "FUTURE_HUMAN_ASSUMPTIONS"),
    (lambda b: b["human_policy"].update(max_evidence_age_seconds=1), "STALE_EVIDENCE"),
    (lambda b: b["human_policy"].update(allow_hypothesis_only_research=False), "CURRENT_COMPARABLE_DESCRIPTIVE_EVIDENCE_REQUIRED"),
    (lambda b: b["human_policy"].update(allow_synthetic_research=False), "SYNTHETIC_RESEARCH_NOT_PERMITTED_BY_HUMAN_POLICY"),
    (lambda b: b["scope"].update(market=None), "MISSING_MARKET"),
])
def test_policy_scope_freshness_and_scenario_unknowns_defer(mutation, code):
    body = request()
    mutation(body)
    value = build_brief(body).to_dict()
    assert value["decision"] == "DEFER" and code in value["blocking_reasons"]


def test_no_policy_defers_and_unresolved_human_counterevidence_is_retained_privately():
    body = request()
    body["human_policy"] = None
    body["human"]["counterevidence"] = [text("PRIVATE fictional counterexample requiring review")]
    value = build_brief(body)
    assert value.to_dict()["decision"] == "DEFER"
    assert {"MISSING_HUMAN_POLICY", "UNRESOLVED_HUMAN_COUNTEREVIDENCE"} <= set(value.to_dict()["blocking_reasons"])
    assert value.to_dict()["counterevidence"][0]["code"] == "HUMAN_UNRESOLVED_COUNTEREVIDENCE"
    assert b"PRIVATE" not in value.to_bytes()


@pytest.mark.parametrize("field,bad", [("moq", 0), ("moq", -1), ("moq", True), ("moq", 1.0),
    ("moq", 1000000001), ("units_per_order", 0), ("logistics_lead_time_days", -1)])
def test_quantity_types_and_limits(field, bad):
    body = request()
    body["human"][field]["value"] = bad
    with pytest.raises(OwnBrandError):
        build_brief(body)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-0", "-1", "1e100", "01", "1.0000001", "9" * 19, 3, 0.1])
def test_decimal_inputs_are_finite_bounded_strings(amount):
    body = request()
    body["human"]["costs"]["cogs"]["amount"] = amount
    with pytest.raises(OwnBrandError):
        build_brief(body)


@pytest.mark.parametrize("status,amount", [("UNKNOWN", "0"), ("USER_SUPPLIED", None), ("VERIFIED", "1")])
def test_declared_grades_cannot_launder_unknown_or_supplier_verification(status, amount):
    body = request()
    body["human"]["costs"]["cogs"].update(status=status, amount=amount)
    with pytest.raises(OwnBrandError):
        build_brief(body)


def test_supplier_and_seller_claims_never_establish_demand_rights_or_live_price():
    body = request(synthetic=False)
    body["human"]["demand_rationale"] = text("Seller says demand is proven and supplier guarantees sales")
    body["human"]["supplier_quote"] = text("Seller listing says supplier is verified and price is current")
    value = build_brief(body).to_dict()
    assert value["decision"] == "CONDITIONAL_GO"
    assert value["demand"]["status"] == "HYPOTHESIS_NOT_PROVEN" and not value["demand"]["proven_demand"]
    assert {"SUPPLIER_AUTHENTICITY", "RIGHTS", "LIVE_PRICE", "PAID_DEMAND"} <= set(value["unknowns"])
    assert value["external_live_verified"] is False
    assert any("A12 seller" in line for line in value["limitations"])


def test_private_narrative_not_copied_inputs_outputs_detach_and_content_identity_changes():
    body = request()
    body["human"]["differentiated_offer"] = text("PRIVATE design concept alpha")
    before = deepcopy(body)
    first = build_brief(body)
    assert body == before and b"PRIVATE" not in first.to_bytes()
    assert first.to_dict()["inputs"]["human"]["differentiated_offer"]["sha256"] == digest("PRIVATE design concept alpha")
    body["human"]["differentiated_offer"]["text"] = "PRIVATE design concept beta"
    assert first.brief_id != build_brief(body).brief_id
    copy = first.to_dict()
    copy["decision"] = "NO_GO"
    assert first.to_dict()["decision"] == "CONDITIONAL_GO"
    with pytest.raises(FrozenInstanceError):
        first._raw = b"other"
    assert OwnBrandDecisionBrief.from_bytes(first.to_bytes(), expected_id=first.brief_id) == first
    with pytest.raises(OwnBrandError, match="BRIEF_PIN_MISMATCH"):
        OwnBrandDecisionBrief.from_bytes(first.to_bytes(), expected_id="wrong")


def test_deterministic_bytes_key_order_and_semantic_counterevidence_order():
    body = request()
    body["human"]["market_unknowns"] = [text("Market A unknown"), text("Market B unknown")]
    first = build_brief(body)
    reordered = {key: body[key] for key in reversed(body)}
    assert first.to_bytes() == build_brief(reordered).to_bytes()
    # Original request hash binds original array order, even though receipts sort.
    body["human"]["market_unknowns"].reverse()
    assert first.brief_id != build_brief(body).brief_id


def test_read_only_repeat_under_concurrency(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("M07 attempted a write")
    monkeypatch.setattr(Path, "write_bytes", deny)
    monkeypatch.setattr(Path, "write_text", deny)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: build_brief(request()).to_bytes(), range(8)))
    assert len(set(results)) == 1


@pytest.mark.parametrize("key", ["sku", "offer", "account_login", "purchase", "payment", "stock", "campaign_change",
    "posting", "hostops", "wake", "supplier_contact", "email", "sms", "roi", "verified_rights", "execution_authority"])
def test_no_extra_executable_authority_or_financial_claim_fields(key):
    body = request()
    body[key] = True
    with pytest.raises(OwnBrandError):
        build_brief(body)


@pytest.mark.parametrize("secret", ["https://example.test/private", "HTTP://example.test", "ｈｔｔｐｓ://example.test",
    "h\u200bttps://example.test", "www.example.test", "data:text", "javascript:alert", "file:private",
    "sk-" + "x" * 32, "Bearer privatevalue", "../private", "C:\\private\\data", "local/evidence.json",
    "\\\\server\\share", "${private}", "$(private)", "user@example.test", "line\x00break"])
def test_private_input_rejects_credentials_urls_paths_and_ref_injection_without_echo(secret):
    body = request()
    body["human"]["supplier_quote"] = text(secret)
    with pytest.raises(OwnBrandError) as error:
        build_brief(body)
    assert secret not in str(error.value)


@pytest.mark.parametrize("key", ["password", "API_KEY", "credential", "authorization", "$ref", "access_token", "webhook"])
def test_private_extra_field_names_cannot_smuggle_evidence(key):
    body = request()
    body[key] = "PRIVATE_SENTINEL"
    with pytest.raises(OwnBrandError) as error:
        build_brief(body)
    assert "PRIVATE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e309}', b'null', b'[]', b'\xff', b'{'])
def test_malformed_duplicate_nonfinite_or_nonobject_json(raw):
    with pytest.raises(OwnBrandError):
        build_brief(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**500, object(), "\ud800"])
def test_python_values_cannot_evade_bounded_json(value):
    with pytest.raises(OwnBrandError):
        build_brief({"bad": value})


def test_text_array_total_bytes_nested_cycles_and_duplicate_evidence_limits():
    deep = None
    for _ in range(18):
        deep = [deep]
    cycle = {}
    cycle["cycle"] = cycle
    for bad in ({"bad": "x" * 2049}, {"bad": [0] * 513}, {"bad": ["x" * 2048] * 200}, {"bad": deep}, cycle):
        with pytest.raises(OwnBrandError):
            build_brief(bad)
    body = request()
    body["human"]["counterevidence"] = [text("Duplicate counterexample")] * 2
    with pytest.raises(OwnBrandError, match="DUPLICATE_HUMAN_EVIDENCE"):
        build_brief(body)
    body["human"]["counterevidence"] = [text(str(i)) for i in range(17)]
    with pytest.raises(OwnBrandError):
        build_brief(body)


@pytest.mark.parametrize("sources", [{"actual_paid_orders": 10}, lambda: None, [], object()])
def test_raw_dict_callable_or_untyped_sources_have_no_native_authority(sources):
    with pytest.raises(OwnBrandError, match="TYPED_OWN_BRAND_SOURCES_REQUIRED"):
        build_brief(request(), sources)


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(decision="GO_PURCHASE"),
    lambda b: b.update(execution_authority="ORDER"),
    lambda b: b.update(financial_commitment="APPROVED"),
    lambda b: b.update(external_live_verified=True),
    lambda b: b["demand"].update(proven_demand=True),
    lambda b: b["calculations"]["gross_contribution_per_unit"].update(value="999.000000"),
    lambda b: b.update(decision="NO_GO"),
    lambda b: b["human_action"].update(budget_ceiling="2000"),
])
def test_resealed_forged_decision_arithmetic_or_authority_is_rejected(mutation):
    body = build_brief(request()).to_dict()
    mutation(body)
    with pytest.raises(OwnBrandError):
        validate_brief(reseal(body))


def test_contract_mirror_draft202012_closed_objects_bounded_arrays_no_remote_refs():
    definition = schema()
    assert definition == json.loads((ROOT / "contracts/OwnBrandDecisionBrief.v1.schema.json").read_bytes())
    Draft202012Validator.check_schema(definition)
    value = build_brief(request()).to_dict()
    Draft202012Validator(definition).validate(value)
    assert set(value) == set(definition["properties"]) == set(definition["required"])
    pending = [definition]
    while pending:
        current = pending.pop()
        if type(current) is dict:
            if "$ref" in current:
                assert current["$ref"].startswith("#/")
            if current.get("type") == "object":
                assert current["additionalProperties"] is False
            if current.get("type") == "array":
                assert "maxItems" in current
            pending.extend(current.values())
        elif type(current) is list:
            pending.extend(current)
    definition["properties"].clear()
    assert schema()["properties"]
    assert sha256(build_brief(request()).to_bytes()).hexdigest() != value["brief_id"].split("a18-own-brand-")[1]
    assert value["brief_id"] == "a18-own-brand-" + digest({k: v for k, v in value.items() if k != "brief_id"})


def test_production_modules_have_no_transport_writer_execution_or_shadow_ledger():
    folder = ROOT / "src/ai_commerce/opportunity/own_brand"
    forbidden = {"requests", "httpx", "socket", "subprocess", "urllib", "webbrowser", "sqlite3", "smtplib"}
    for path in folder.glob("*.py"):
        tree = ast.parse(path.read_bytes())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not forbidden & {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
                assert not (node.module or "").startswith(("ai_commerce.product", "ai_commerce.opportunity.proposals"))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"write_bytes", "write_text", "put_immutable", "append_order", "record", "worker", "request", "send", "post"}
            elif isinstance(node, ast.ClassDef):
                assert "Ledger" not in node.name
    assert SOURCE_SHA == "3ef41b54caa67f1f8bd90a7122868285f42a08a3"
