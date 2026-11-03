"""E07 deterministic Human research decisions; fixtures are never live evidence."""
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

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.opportunity.scale_option import (
    ScaleBrandDecisionMemo, ScaleOptionError, ScaleOptionSources, build_memo, schema, validate_memo,
)
from ai_commerce.opportunity.scale_option.schemas import (
    MAX_BYTES, PER_ASSET, SCENARIOS, SOURCE_SHA, digest, parse, validator,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("E07 attempted external access")
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", deny)


def request(*, product=None, economic_basis="COST_ONLY_RESEARCH"):
    return {"schema_version": "ScaleBrandEvaluation.v1", "currency": "USD", "as_of": "2026-11-03T00:00:00Z",
        "scope": {"campaign_ref": "fixture-campaign", "concept_ref": "fixture-concept", "product_ref": product,
            "market": "VN", "account_ref": None, "provider_ref": None, "platform": None, "surface": None,
            "creative_ref": None, "creative_sha256": None,
            "measurement_window": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]},
        "human": {"authored_by": "HUMAN_DECLARED_NOT_AUTHENTICATED", "declared_at": "2026-11-02T00:00:00Z",
            "data_kind": "SYNTHETIC_FIXTURE", "rationale": "Private fictional asset research rationale",
            "counterevidence": [], "uncertainties": ["Provider capacity is an unverified Human estimate"]},
        "scale": {"assumption_id": "fixture-assumptions", "window": ["2026-11-03T00:00:00Z", "2026-11-05T00:00:00Z"],
            "unit": "ASSET", "cost_basis": "PER_ASSET_PLUS_FIXED_ONE_EXPERIMENT",
            "cost_lineage": "HUMAN_ESTIMATES_NO_IMPORTED_TOTALS", "economic_basis": economic_basis,
            "uncertainty": "Private capacity and channel cost stress assumptions",
            "scenarios": {name: {"assets": assets, "capacity_assets": capacity,
                "costs": {"provider_cost": provider, "channel_cost": "1", "operations_cost": "2", "fee_cost": "0.5",
                          "fixed_cost": "10", "reserve": "5"},
                "hypothetical_value_per_asset": "15" if economic_basis == "HYPOTHETICAL_VALUE_PER_ASSET" else None}
                for name, assets, capacity, provider in (("conservative", 2, 4, "5"), ("base", 4, 8, "3"), ("expansion", 8, 12, "2"))}},
        "permissions": {"rights_status": "UNKNOWN", "provider_entitlement": "UNKNOWN", "prior_rights_sha256": None,
                        "current_rights_sha256": None, "valid_until": None},
        "human_policy": {"schema_version": "ScaleBrandHumanPolicy.v1", "policy_id": "fixture-policy",
            "authored_by": "HUMAN_DECLARED_NOT_AUTHENTICATED", "valid_from": "2026-11-01T00:00:00Z",
            "valid_until": "2026-12-01T00:00:00Z", "currency": "USD", "max_evidence_age_seconds": 2592000,
            "evidence_requirement": "HYPOTHESIS_RESEARCH", "allow_synthetic_research": True,
            "spend_ceiling": "100", "available_budget": "3000", "max_downside": "1000", "max_unit_cost": "10",
            "max_experiment_assets": 10, "proposed_action": "RESEARCH", "capital_or_stock_commitment": False,
            "campaign_or_account_changes": False}}


def lane(value, name="PORTFOLIO_SCALE"):
    return next(row for row in value["options"] if row["lane"] == name)


def reseal(value):
    body = {key: deepcopy(item) for key, item in value.items() if key not in {"memo_id", "content_sha256"}}
    content = digest(body)
    return {**body, "content_sha256": content, "memo_id": "a18-scale-brand-" + content}


def test_positive_cost_research_is_conditional_without_any_provider_claim():
    value = build_memo(request()).to_dict()
    assert value["decision"] == lane(value)["state"] == "CONDITIONAL_VALIDATE"
    assert lane(value, "OWN_BRAND")["state"] == "NOT_APPLICABLE"
    assert lane(value)["human_action"]["kind"] == "CONSIDER_BOUNDED_RESEARCH"
    assert lane(value)["human_action"]["budget_ceiling"] == "100"
    assert value["scale_calculations"]["worst_case_exposure"]["value"] == "59.000000"
    rows = value["scale_calculations"]["scenarios"]
    assert [r["unit_cost"]["value"] for r in rows] == ["8.500000", "6.500000", "5.500000"]
    assert [r["experiment_cost"]["value"] for r in rows] == ["27.000000", "36.000000", "54.000000"]
    assert [r["cash_exposure"]["value"] for r in rows] == ["32.000000", "41.000000", "59.000000"]
    assert [r["throughput_assets_per_day"]["value"] for r in rows] == ["1.000000", "2.000000", "4.000000"]
    assert all(r["hypothetical_unit_contribution"]["value"] is None for r in rows)
    assert value["epistemic_status"] == {"human_assumptions": "HYPOTHESIS",
        "native_observations": "LOCAL_DESCRIPTIVE_NOT_PROVIDER_ATTESTED", "validated_provider_receipts": "NONE",
        "verified_demand": False, "actual_revenue": "UNKNOWN", "actual_profit": "UNKNOWN", "actual_returns": "UNKNOWN",
        "actual_fees": "UNKNOWN", "actual_settlement": "UNKNOWN"}
    assert value["execution_authority"] == value["financial_commitment"] == "NONE"
    assert value["external_live_verified"] is False
    assert "SYNTHETIC_CALCULATION_AND_RESEARCH_ONLY" in value["reason_codes"]


@pytest.mark.parametrize("action", ["RESEARCH", "PROTOTYPE", "SCALE_EXPERIMENT"])
def test_small_explicit_actions_remain_human_only_and_score_free(action):
    body = request()
    body["human_policy"]["proposed_action"] = action
    value = build_memo(body).to_dict()
    assert value["decision"] == "CONDITIONAL_VALIDATE"
    assert lane(value)["human_action"]["kind"] == "CONSIDER_BOUNDED_" + action
    assert "NO_SCORE_NO_RANKING" in value["comparison_basis"]
    assert lane(value)["alternative_paths"] == ["MAINTAIN_CURRENT_WITHOUT_COMMITMENT", "GATHER_OFFLINE_EVIDENCE",
                                               "HUMAN_REVIEW_BOUNDED_VALIDATION"]


def test_hypothetical_unit_economics_are_never_measured_revenue_or_profit():
    value = build_memo(request(economic_basis="HYPOTHETICAL_VALUE_PER_ASSET")).to_dict()
    row = value["scale_calculations"]["scenarios"][0]
    assert row["hypothetical_unit_contribution"]["value"] == "6.500000"
    assert row["hypothetical_total_value"]["value"] == "30.000000"
    assert row["hypothetical_surplus"]["value"] == "3.000000"
    assert value["scale_calculations"]["worst_case_exposure"]["value"] == "59.000000"
    assert value["epistemic_status"]["actual_profit"] == "UNKNOWN"


@pytest.mark.parametrize("field,amount,reason", [
    ("spend_ceiling", "58.999999", "HUMAN_SPEND_CEILING_EXCEEDED"),
    ("available_budget", "0", "AVAILABLE_BUDGET_EXCEEDED"),
    ("max_downside", "58.999999", "HUMAN_DOWNSIDE_LIMIT_EXCEEDED"),
    ("max_unit_cost", "8.499999", "HUMAN_UNIT_COST_LIMIT_EXCEEDED"),
    ("max_experiment_assets", 7, "HUMAN_EXPERIMENT_SIZE_LIMIT_EXCEEDED"),
])
def test_conservative_policy_violations_do_not_scale(field, amount, reason):
    body = request()
    body["human_policy"][field] = amount
    value = build_memo(body).to_dict()
    assert value["decision"] == "DO_NOT_SCALE"
    assert reason in lane(value)["blocking_reasons"]
    assert lane(value)["human_action"]["kind"] == "REVIEW_LIMITS"


def test_exact_spend_risk_unit_and_capacity_boundaries_are_inclusive():
    body = request()
    body["human_policy"].update(spend_ceiling="59", available_budget="59", max_downside="59", max_unit_cost="8.5", max_experiment_assets=8)
    for row in body["scale"]["scenarios"].values():
        row["capacity_assets"] = row["assets"]
    assert build_memo(body).to_dict()["decision"] == "CONDITIONAL_VALIDATE"


@pytest.mark.parametrize("capacity", [0, 7])
def test_throughput_and_capacity_never_infer_provider_availability(capacity):
    body = request()
    body["scale"]["scenarios"]["expansion"]["capacity_assets"] = capacity
    value = build_memo(body).to_dict()
    assert value["decision"] == "DO_NOT_SCALE" and "CAPACITY_LIMIT_EXCEEDED" in lane(value)["blocking_reasons"]
    assert value["scale_calculations"]["scenarios"][2]["capacity_gap"]["value"] == 8 - capacity
    assert value["external_live_verified"] is False


@pytest.mark.parametrize("amount", ["0", "8", "8.5"])
def test_zero_or_negative_human_unit_contribution_blocks(amount):
    body = request(economic_basis="HYPOTHETICAL_VALUE_PER_ASSET")
    body["scale"]["scenarios"]["conservative"]["hypothetical_value_per_asset"] = amount
    value = build_memo(body).to_dict()
    assert value["decision"] == "DO_NOT_SCALE"
    assert "NONPOSITIVE_HUMAN_UNIT_CONTRIBUTION" in lane(value)["blocking_reasons"]


@pytest.mark.parametrize("name", SCENARIOS)
@pytest.mark.parametrize("key", [*PER_ASSET, "fixed_cost", "reserve"])
def test_every_unknown_experiment_cost_defers_and_stays_null(name, key):
    body = request()
    body["scale"]["scenarios"][name]["costs"][key] = None
    value = build_memo(body).to_dict()
    assert value["decision"] == "DEFER" and "MISSING_" + key.upper() in lane(value)["blocking_reasons"]
    assert next(r for r in value["scale_calculations"]["scenarios"] if r["name"] == name)["cash_exposure"]["value"] is None
    assert value["scale_calculations"]["worst_case_exposure"]["value"] is None


@pytest.mark.parametrize("key", ["spend_ceiling", "available_budget", "max_downside", "max_unit_cost", "max_experiment_assets"])
def test_missing_human_caps_never_default_to_zero_or_authority(key):
    body = request()
    body["human_policy"][key] = None
    value = build_memo(body).to_dict()
    assert value["decision"] == "DEFER" and "MISSING_" + key.upper() in lane(value)["blocking_reasons"]


@pytest.mark.parametrize("key", ["assets", "capacity_assets"])
def test_unknown_capacity_or_asset_count_defers(key):
    body = request()
    body["scale"]["scenarios"]["base"][key] = None
    value = build_memo(body).to_dict()
    assert value["decision"] == "DEFER"
    assert value["scale_calculations"]["scenarios"][1]["capacity_gap"]["value"] is None


def test_genuine_zero_cost_is_different_from_unknown_cost_and_zero_units():
    body = request()
    body["human_policy"].update(spend_ceiling="0", available_budget="0", max_downside="0", max_unit_cost="0")
    for row in body["scale"]["scenarios"].values():
        row["costs"] = dict.fromkeys(row["costs"], "0")
    value = build_memo(body).to_dict()
    assert value["decision"] == "CONDITIONAL_VALIDATE"
    assert value["scale_calculations"]["worst_case_exposure"] == {"value": "0.000000", "reason_codes": []}
    body["scale"]["scenarios"]["base"]["costs"]["fee_cost"] = None
    assert build_memo(body).to_dict()["decision"] == "DEFER"
    body["scale"]["scenarios"]["base"]["costs"]["fee_cost"] = "0"
    body["scale"]["scenarios"]["base"]["assets"] = 0
    result = build_memo(body).to_dict()
    assert "EMPTY_EXPERIMENT" in lane(result)["blocking_reasons"]
    assert result["scale_calculations"]["scenarios"][1]["experiment_cost"]["value"] == "0.000000"


@pytest.mark.parametrize("mutation,reason", [
    (lambda b: b.update(human_policy=None), "MISSING_HUMAN_POLICY"),
    (lambda b: b["human_policy"].update(valid_until=b["as_of"]), "POLICY_OUTSIDE_VALIDITY"),
    (lambda b: b["human_policy"].update(currency="EUR"), "POLICY_CURRENCY_MISMATCH_NO_FX"),
    (lambda b: b["human_policy"].update(allow_synthetic_research=False), "SYNTHETIC_RESEARCH_NOT_PERMITTED"),
    (lambda b: b["human"].update(declared_at="2026-11-04T00:00:00Z"), "FUTURE_EVIDENCE"),
    (lambda b: b["human"].update(declared_at="2026-08-01T00:00:00Z"), "STALE_EVIDENCE"),
    (lambda b: b["human"]["counterevidence"].append("Capacity estimate is disputed"), "UNRESOLVED_HUMAN_COUNTEREVIDENCE"),
    (lambda b: b["human_policy"].update(capital_or_stock_commitment=True), "IRREVERSIBLE_OR_CAMPAIGN_EFFECTS_OUT_OF_SCOPE"),
    (lambda b: b["human_policy"].update(campaign_or_account_changes=True), "IRREVERSIBLE_OR_CAMPAIGN_EFFECTS_OUT_OF_SCOPE"),
    (lambda b: b["scale"].update(window=["2026-11-02T00:00:00Z", "2026-11-04T00:00:00Z"]), "EXPERIMENT_WINDOW_ALREADY_STARTED"),
    (lambda b: b["permissions"].update(rights_status="REVOKED"), "RIGHTS_OR_ENTITLEMENT_REVOKED"),
    (lambda b: b["permissions"].update(provider_entitlement="REVOKED"), "RIGHTS_OR_ENTITLEMENT_REVOKED"),
    (lambda b: b["permissions"].update(prior_rights_sha256="a" * 64, current_rights_sha256="b" * 64), "RIGHTS_CHANGED_OR_MISSING_PIN"),
    (lambda b: b["permissions"].update(valid_until=b["as_of"]), "RIGHTS_OR_ENTITLEMENT_EXPIRED"),
    (lambda b: b["permissions"].update(rights_status="HUMAN_DECLARED"), "INCOMPLETE_HUMAN_PERMISSION_PIN"),
])
def test_incomplete_stale_contradictory_or_unauthorized_request_defers(mutation, reason):
    body = request()
    mutation(body)
    value = build_memo(body).to_dict()
    assert value["decision"] == "DEFER" and reason in lane(value)["blocking_reasons"]


@pytest.mark.parametrize("permission", ["UNKNOWN", "HUMAN_DECLARED"])
def test_even_human_declared_rights_cannot_authorize_live_scale(permission):
    body = request()
    body["human_policy"]["proposed_action"] = "LIVE_SCALE"
    body["permissions"].update(rights_status=permission, provider_entitlement=permission,
        current_rights_sha256="a" * 64, valid_until="2026-12-01T00:00:00Z")
    value = build_memo(body).to_dict()
    assert value["decision"] == "DEFER" and "LIVE_ACTION_OUT_OF_SCOPE" in lane(value)["blocking_reasons"]
    assert value["execution_authority"] == "NONE" and not value["external_live_verified"]


def test_lane_independence_and_content_only_does_not_block_creative():
    body = request(product="fixture-product")
    value = build_memo(body).to_dict()
    assert lane(value)["state"] == "CONDITIONAL_VALIDATE"
    assert lane(value, "OWN_BRAND")["state"] == value["decision"] == "DEFER"
    assert "MISSING_NATIVE_M07_BRIEF" in lane(value, "OWN_BRAND")["blocking_reasons"]
    body["scope"]["product_ref"] = None
    assert build_memo(body).to_dict()["decision"] == "CONDITIONAL_VALIDATE"
    body["scale"] = None
    value = build_memo(body).to_dict()
    assert value["decision"] == "DEFER" and value["scale_calculations"] is None
    assert all(r["state"] == "NOT_APPLICABLE" and r["alternative_paths"] == ["CONTINUE_OPTIONAL_CREATIVE_WORK"] for r in value["options"])


@pytest.mark.parametrize("requirement", ["CURRENT_DESCRIPTIVE", "CURRENT_COMMERCIAL"])
def test_required_native_observations_cannot_be_filled_by_human_idea(requirement):
    body = request()
    body["human_policy"]["evidence_requirement"] = requirement
    value = build_memo(body).to_dict()
    assert value["decision"] == "DEFER"
    assert "CURRENT_DESCRIPTIVE_PORTFOLIO_REQUIRED" in lane(value)["blocking_reasons"]


def test_cost_only_vs_value_unknown_cannot_silently_change_economic_basis():
    body = request()
    body["scale"]["scenarios"]["base"]["hypothetical_value_per_asset"] = "0"
    assert "CONTRADICTORY_COST_ONLY_VALUE_ASSUMPTION" in lane(build_memo(body).to_dict())["blocking_reasons"]
    body = request(economic_basis="HYPOTHETICAL_VALUE_PER_ASSET")
    body["scale"]["scenarios"]["base"]["hypothetical_value_per_asset"] = None
    assert "MISSING_HYPOTHETICAL_VALUE" in lane(build_memo(body).to_dict())["blocking_reasons"]


def test_all_scenarios_and_fixed_reserve_count_once_in_sensitivity():
    body = request()
    baseline = build_memo(body).to_dict()
    body["scale"]["scenarios"]["base"]["costs"]["provider_cost"] = "30"
    stressed = build_memo(body).to_dict()
    assert stressed["decision"] == "DO_NOT_SCALE"
    assert stressed["scale_calculations"]["scenarios"][0] == baseline["scale_calculations"]["scenarios"][0]
    assert stressed["scale_calculations"]["scenarios"][1]["cash_exposure"]["value"] == "149.000000"
    assert stressed["scale_calculations"]["worst_case_exposure"]["value"] == "149.000000"


def test_canonical_bytes_identity_private_immutability_and_external_pins():
    body = request()
    snapshot = deepcopy(body)
    memo = build_memo(body)
    assert body == snapshot
    value = memo.to_dict()
    assert memo.to_bytes() == canonical_bytes(value)
    assert memo.sha256 == sha256(memo.to_bytes()).hexdigest()
    assert memo.content_sha256 == digest({k: v for k, v in value.items() if k not in {"memo_id", "content_sha256"}})
    assert memo.memo_id == "a18-scale-brand-" + memo.content_sha256
    assert memo.sha256 != memo.content_sha256
    assert ScaleBrandDecisionMemo.from_bytes(memo.to_bytes(), expected_id=memo.memo_id, expected_sha256=memo.sha256) == memo
    assert validate_memo(value) == value
    with pytest.raises(FrozenInstanceError):
        memo._raw = b"changed"
    value["inputs"]["currency"] = "EUR"
    assert memo.to_dict()["inputs"]["currency"] == "USD"
    for keyword in ({"expected_id": "a18-scale-brand-" + "0" * 64}, {"expected_sha256": "0" * 64}):
        with pytest.raises(ScaleOptionError):
            ScaleBrandDecisionMemo.from_bytes(memo.to_bytes(), **keyword)
    assert b"Private fictional" not in memo.to_bytes() and b"Private capacity" not in memo.to_bytes()


def test_determinism_across_input_order_threads_and_ambient_decimal_context():
    body = request()
    reordered = dict(reversed(list(body.items())))
    with localcontext() as context:
        context.prec = 4
        context.traps[Inexact] = True
        one = build_memo(body)
    with localcontext() as context:
        context.prec = 100
        two = build_memo(canonical_bytes(reordered))
    assert one.to_bytes() == two.to_bytes()
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert set(pool.map(lambda _: build_memo(body).to_bytes(), range(6))) == {one.to_bytes()}
    body["as_of"] = "2026-11-03T01:00:00Z"
    assert build_memo(body).memo_id != one.memo_id


def test_portable_contract_exact_runtime_mirror_is_closed_and_detached():
    portable = json.loads((ROOT / "contracts/ScaleBrandDecisionMemo.v1.schema.json").read_bytes())
    assert portable == schema()
    Draft202012Validator.check_schema(portable)
    Draft202012Validator(portable).validate(build_memo(request()).to_dict())
    pending = [portable]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item.get("type") == "object":
                assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])
            assert "$ref" not in item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    broken = schema()
    broken["properties"]["external_live_verified"] = {"const": True}
    assert not validator(broken).is_valid(build_memo(request()).to_dict())
    detached = validator()
    detached.schema["properties"].clear()
    assert validator().is_valid(build_memo(request()).to_dict())
    assert schema()["properties"] and schema()["properties"]["source_sha"]["const"] == SOURCE_SHA


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(decision="CONDITIONAL_VALIDATE", reason_codes=[]),
    lambda v: v["scale_calculations"]["scenarios"][0]["cash_exposure"].update(value="0.000000"),
    lambda v: v["options"][0].update(state="DO_NOT_SCALE"),
    lambda v: v["options"][0]["human_action"].update(budget_ceiling="999"),
    lambda v: v.update(source_sha="0" * 40),
    lambda v: v.update(external_live_verified=True),
    lambda v: v.update(execution_authority="EXECUTE"),
    lambda v: v.update(financial_commitment="APPROVED"),
    lambda v: v["epistemic_status"].update(verified_demand=True),
    lambda v: v["epistemic_status"].update(actual_profit="100"),
    lambda v: v["options"].reverse(),
    lambda v: v.update(extra="private"),
])
def test_tampered_memo_even_resealed_cannot_change_derived_output(mutation):
    value = build_memo(request()).to_dict()
    mutation(value)
    with pytest.raises(ScaleOptionError):
        ScaleBrandDecisionMemo.from_bytes(canonical_bytes(reseal(value)))


@pytest.mark.parametrize("value", ["https://example.test", "hTtPs:private", "file:private", "ftp://private", "data:text",
    "javascript:run", "www.example.test", "mailto:a@example.test", "person@example.test", "+84 912 345 678",
    "C:\\private\\receipt", "../private", "folder/file", "~/private", "${PRIVATE}", "$(private)",
    "bearer private", "api_key=private", "password:private", "sk-abcdefghijklmnopqrstuv", "https\u200b://example.test",
    "ｈｔｔｐｓ：private", "private\x00value", "private\u200bvalue"])
def test_private_contact_url_path_credentials_or_unicode_injection_denied(value):
    body = request()
    body["human"]["rationale"] = value
    with pytest.raises(ScaleOptionError) as caught:
        build_memo(body)
    assert value not in str(caught.value)


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(extra=1), lambda b: b["human"].update(password="private"),
    lambda b: b["scale"]["scenarios"]["base"]["costs"].update(extra="0"),
    lambda b: b["scale"].update(unit="ORDER"), lambda b: b["human_policy"].update(proposed_action="GO_SPEND"),
    lambda b: b["human"].update(rationale=" " * 10), lambda b: b["human"].update(rationale="a" * 2049),
    lambda b: b["human"].update(uncertainties=["same"] * 17),
    lambda b: b["human"].update(counterevidence=["duplicate", "duplicate"]),
    lambda b: b["scale"]["scenarios"]["base"].update(assets=10**80),
    lambda b: b["scale"]["scenarios"]["base"].update(assets=True),
    lambda b: b["scope"].update(creative_ref="creative-without-hash"),
    lambda b: b["scope"].update(measurement_window=[b["as_of"], b["as_of"]]),
    lambda b: b["scale"].update(window=[b["as_of"], "2026-11-02T00:00:00Z"]),
    lambda b: b["human_policy"].update(valid_from=b["human_policy"]["valid_until"]),
    lambda b: b.update(as_of="2026-11-03T00:00:00"),
])
def test_closed_typed_bounded_request_contract(mutation):
    body = request()
    mutation(body)
    with pytest.raises(ScaleOptionError):
        build_memo(body)


@pytest.mark.parametrize("amount", [0.1, float("nan"), float("inf"), -1, True, "NaN", "Infinity", "1e20", "-1", "01", "1.0000001", "1" * 30])
def test_money_accepts_only_bounded_decimal_strings(amount):
    body = request()
    body["scale"]["scenarios"]["base"]["costs"]["provider_cost"] = amount
    with pytest.raises(ScaleOptionError):
        build_memo(body)


@pytest.mark.parametrize("raw", [b'{}', b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e1000}',
    b'\xff', b'[]', b'null', b'{', b' ' * (MAX_BYTES + 1), b'[' * 5000 + b'0' + b']' * 5000],
    ids=["empty-object", "duplicate-keys", "nan", "infinity", "overflow", "invalid-utf8", "array", "null", "syntax", "oversized", "recursive"])
def test_raw_malformed_duplicate_nonfinite_oversized_and_recursive_json(raw):
    with pytest.raises(ScaleOptionError):
        build_memo(raw)


def test_cyclic_values_and_large_arrays_reject_before_serialization():
    body = request()
    body["cycle"] = body
    with pytest.raises(ScaleOptionError, match="JSON_COMPLEXITY_LIMIT"):
        build_memo(body)
    with pytest.raises(ScaleOptionError, match="JSON_ARRAY_LIMIT"):
        parse(canonical_bytes({"rows": [None] * 513}))


def test_production_has_no_io_execution_or_clock_discovery_interfaces():
    forbidden_modules = {"socket", "urllib", "requests", "http", "subprocess", "os", "pathlib", "sqlite3", "time", "random"}
    forbidden_calls = {"open", "write", "write_bytes", "write_text", "mkdir", "unlink", "now", "utcnow", "timestamp_utc",
                       "record", "append", "put_immutable", "urlopen", "Popen", "run", "system"}
    for path in (ROOT / "src/ai_commerce/opportunity/scale_option").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(n.name.split(".")[0] in forbidden_modules for n in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden_modules
            if isinstance(node, ast.Call):
                # list.append is in-memory construction, never a ledger writer.
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                assert name not in forbidden_calls - {"append"}


def test_forged_source_dicts_are_not_current_performance_claims():
    for sources in ({}, {"portfolio": {"orders": 100, "revenue": "1000"}}, ScaleOptionSources(portfolio={})):
        with pytest.raises(ScaleOptionError):
            build_memo(request(), sources)
