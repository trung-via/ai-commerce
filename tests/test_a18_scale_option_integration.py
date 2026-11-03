"""Real M06/M07/A16/A15 validators and Windows MT01 closure, without attestation."""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.cost_ledger import CostLedger
from ai_commerce.opportunity.own_brand import OwnBrandDecisionBrief, OwnBrandSources, build_brief
from ai_commerce.opportunity.portfolio import CostSource, EvidenceBundle, PortfolioRecommendation, ProposalSource, recommend_portfolio
from ai_commerce.opportunity.scale_option import (
    CommercialSource, CostReference, OwnBrandSource, PortfolioSource, ScaleBrandDecisionMemo,
    ScaleOptionError, ScaleOptionSources, build_memo,
)
from ai_commerce.opportunity.scale_option.schemas import SOURCE_SHA, digest
from ai_commerce.settlement import CommercialReview, SettlementLedger, commercial_review

# Factories construct real native records and stores. No owning validator is patched.
from test_a18_scale_option import offline, request, lane, reseal
from test_a18_own_brand import request as brand_request
from test_a18_own_brand_integration import portfolio_selector, review_selector, for_portfolio as brand_for_portfolio, for_review as brand_for_review
from test_a18_portfolio import request as portfolio_request
from test_a18_portfolio_integration import analytics, evaluation, manual_provisional_case, cost_source, files_under, deny_effects, propose_opportunities
from test_a16_commercial_review import native, document_for, cost as native_cost
from test_a16_settlement import build, receipt, revision

ROOT = Path(__file__).resolve().parents[1]


def for_portfolio(record):
    body = request()
    native_request = record.to_dict()["request"]
    scope = native_request["scope"]
    for key in body["scope"]:
        if key in scope:
            body["scope"][key] = deepcopy(scope[key])
    body["scope"]["concept_ref"] = native_request["candidate_id"]
    body["scope"]["measurement_window"] = deepcopy(native_request["measurement_window"])
    body["scope"]["market"] = scope["market"] or "VN"
    body["currency"], body["as_of"] = native_request["currency"], native_request["as_of"]
    return body


def own_selector(brief, original_request=None, original_sources=None):
    return OwnBrandSource(brief, brief.brief_id, sha256(brief.to_bytes()).hexdigest(),
        canonical_bytes(original_request) if original_request is not None else None, original_sources)


def bridge(tmp_path, *, product="fixture-product", values=(12,)):
    snapshot, origin, _, report_source = analytics(tmp_path, values=values)
    bundle = EvidenceBundle(reports=(report_source,))
    evaluation_request = evaluation(snapshot)
    evaluation_request["scope"]["product_ref"] = product
    record = recommend_portfolio(evaluation_request, bundle)
    selected = portfolio_selector(record, bundle)
    original = brand_for_portfolio(record)
    native_sources = OwnBrandSources(selected)
    brief = build_brief(original, native_sources)
    sources = ScaleOptionSources(selected, own_selector(brief, original, native_sources))
    return for_portfolio(record), sources, record, brief, origin, original, native_sources


def for_review(review):
    body = request(product=review.to_dict()["scope"]["product_id"])
    scope = review.to_dict()["scope"]
    for key in body["scope"]:
        if key in scope:
            body["scope"][key] = deepcopy(scope[key])
    return body


def cost_ref(ledger, entry_id):
    return CostReference(entry_id, sha256(canonical_bytes(ledger.get(entry_id).to_record())).hexdigest(), ledger)


def test_positive_synthetic_native_bridge_reuses_exact_m07_feasibility(tmp_path):
    body, sources, portfolio, brief, _, original, _ = bridge(tmp_path)
    original_bytes = portfolio.to_bytes(), brief.to_bytes()
    value = build_memo(body, sources).to_dict()
    assert value["decision"] == "CONDITIONAL_VALIDATE"
    assert [r["state"] for r in value["options"]] == ["CONDITIONAL_VALIDATE", "CONDITIONAL_VALIDATE"]
    assert original_bytes == (portfolio.to_bytes(), brief.to_bytes())
    assert value["sources"]["own_brand"]["feasibility"] == brief.to_dict()["calculations"]
    assert value["sources"]["own_brand"]["feasibility_sha256"] == digest(brief.to_dict()["calculations"])
    assert value["sources"]["own_brand"]["moq"] == original["human"]["moq"]["value"]
    assert value["sources"]["portfolio"]["custody"] == value["sources"]["own_brand"]["custody"] == "CURRENT_REPLAY"
    assert value["sources"]["own_brand"]["native_custody"]["portfolio"] == "CURRENT_REPLAY"
    assert lane(value, "OWN_BRAND")["human_action"]["budget_ceiling"] == "50"
    assert lane(value, "OWN_BRAND")["human_action"]["unit_ceiling"] == 2
    assert value["sources"]["own_brand"]["proven_demand"] is False
    assert value["external_live_verified"] is False
    raw = canonical_bytes(value)
    assert b"Fictional easy-clean" not in raw and b"Fictional unverified quote" not in raw
    assert b"raw_utf8" not in raw and b"store" not in raw


def test_archived_m06_and_m07_do_not_inherit_build_time_current_custody(tmp_path):
    body, sources, portfolio, brief, _, _, _ = bridge(tmp_path)
    archived = ScaleOptionSources(portfolio_selector(portfolio), own_selector(brief))
    value = build_memo(body, archived).to_dict()
    assert value["sources"]["portfolio"]["custody"] == value["sources"]["own_brand"]["custody"] == "ARCHIVED_ONLY"
    assert value["sources"]["portfolio"]["observation_count"] == 0
    # Preserve M07's historical native grade without claiming it was reread now.
    assert value["sources"]["own_brand"]["native_custody"]["portfolio"] == "CURRENT_REPLAY"
    assert value["sources"]["own_brand"]["original_request_replayed"] is False
    assert lane(value)["state"] == "CONDITIONAL_VALIDATE"
    assert lane(value, "OWN_BRAND")["state"] == value["decision"] == "DEFER"
    assert "M07_ORIGINAL_NATIVE_CUSTODY_REQUIRED" in lane(value, "OWN_BRAND")["blocking_reasons"]
    assert build_memo(body, sources).memo_id != build_memo(body, archived).memo_id


def test_replaying_m07_with_archived_m06_cannot_renew_native_evidence(tmp_path):
    body, _, record, _, _, original, _ = bridge(tmp_path)
    archived_native = OwnBrandSources(portfolio_selector(record))
    brief = build_brief(original, archived_native)
    value = build_memo(body, ScaleOptionSources(own_brand=own_selector(brief, original, archived_native))).to_dict()
    row = value["sources"]["own_brand"]
    assert row["original_request_replayed"] is True
    assert row["native_custody"]["portfolio"] == row["custody"] == "ARCHIVED_ONLY"
    assert lane(value, "OWN_BRAND")["state"] == "DEFER"


def test_m07_hypothesis_with_no_native_bundle_is_not_current_demand():
    original = brand_request()
    original["scope"]["product_ref"] = "fixture-product"
    native_sources = OwnBrandSources()
    brief = build_brief(original, native_sources)
    value = build_memo(request(product="fixture-product"), ScaleOptionSources(
        own_brand=own_selector(brief, original, native_sources))).to_dict()
    assert value["sources"]["own_brand"]["custody"] == "ARCHIVED_ONLY"
    assert value["sources"]["own_brand"]["native_custody"] == {"portfolio": None, "commercial_review": None}
    assert lane(value, "OWN_BRAND")["state"] == "DEFER"


def test_m07_current_grade_with_archived_inner_m06_bundle_is_not_current_at_e07(tmp_path):
    snapshot, _, _, source = analytics(tmp_path, values=(12,))
    bundle = EvidenceBundle(reports=(replace(source, snapshots=()),))
    original_portfolio = evaluation(snapshot)
    original_portfolio["scope"]["product_ref"] = "fixture-product"
    record = recommend_portfolio(original_portfolio, bundle)
    assert record.to_dict()["sources"][0]["custody"] != "NATIVE_REPLAY"
    original = brand_for_portfolio(record)
    native_sources = OwnBrandSources(portfolio_selector(record, bundle))
    brief = build_brief(original, native_sources)
    assert brief.to_dict()["sources"]["portfolio"]["custody"] == "CURRENT_REPLAY"
    value = build_memo(for_portfolio(record), ScaleOptionSources(own_brand=own_selector(brief, original, native_sources))).to_dict()
    row = value["sources"]["own_brand"]
    assert row["custody"] == "ARCHIVED_ONLY" and row["native_bundle_replayed"] is False
    assert lane(value, "OWN_BRAND")["state"] == "DEFER"


def test_non_native_objects_cannot_supply_to_bytes_callbacks():
    class Callback:
        def to_bytes(self):
            pytest.fail("Untrusted callback was invoked")
    record = recommend_portfolio(portfolio_request())
    selected = replace(portfolio_selector(record), recommendation=Callback())
    with pytest.raises(ScaleOptionError, match="NATIVE_RECOMMENDATION_REQUIRED"):
        build_memo(request(), ScaleOptionSources(portfolio=selected))


def test_deleted_native_origin_fails_real_reread_but_archive_can_be_validated(tmp_path):
    body, sources, record, brief, origin, _, _ = bridge(tmp_path)
    memo = build_memo(body, sources)
    origin.unlink()
    with pytest.raises(ScaleOptionError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_memo(body, sources)
    # Archived validation is content replay only, not an assertion about NOW.
    assert ScaleBrandDecisionMemo.from_bytes(memo.to_bytes(), expected_id=memo.memo_id) == memo
    historical = build_memo(body, ScaleOptionSources(portfolio_selector(record), own_selector(brief))).to_dict()
    assert historical["decision"] == "DEFER" and historical["archive_validation"] == "CONTENT_ONLY_NOT_CURRENT_NATIVE_CUSTODY"


@pytest.mark.parametrize("mutation", [
    lambda s: replace(s, sha256="0" * 64),
    lambda s: replace(s, recommendation_id=None),
    lambda s: replace(s, recommendation_id="a18-portfolio-" + "0" * 64),
    lambda s: replace(s, reference_bytes=s.reference_bytes + b" "),
    lambda s: replace(s, reference_bytes=b'{"url":"https://private.test"}'),
    lambda s: replace(s, recommendation=s.recommendation.to_dict()),
    lambda s: replace(s, evidence={}),
])
def test_m06_corrupt_hash_id_bytes_or_freestyle_dict_fails_real_validator(mutation):
    record = recommend_portfolio(portfolio_request())
    with pytest.raises(ScaleOptionError):
        build_memo(for_portfolio(record), ScaleOptionSources(portfolio=mutation(portfolio_selector(record))))


@pytest.mark.parametrize("defect", ["hash", "id", "missing_id", "dict", "request", "sources", "incomplete"])
def test_m07_hash_original_request_and_native_custody_are_all_required(tmp_path, defect):
    body, sources, _, _, _, original, _ = bridge(tmp_path)
    selected = sources.own_brand
    changed = deepcopy(original)
    changed["as_of"] = "2026-11-03T01:00:00Z"
    faulty = {"hash": lambda: replace(selected, sha256="0" * 64),
        "id": lambda: replace(selected, brief_id="a18-own-brand-" + "0" * 64),
        "missing_id": lambda: replace(selected, brief_id=None),
        "dict": lambda: replace(selected, brief=selected.brief.to_dict()),
        "request": lambda: replace(selected, original_request_bytes=canonical_bytes(changed)),
        "sources": lambda: replace(selected, original_sources=OwnBrandSources()),
        "incomplete": lambda: replace(selected, original_sources=None)}[defect]()
    with pytest.raises(ScaleOptionError):
        build_memo(body, replace(sources, own_brand=faulty))


@pytest.mark.parametrize("native_kind", ["PORTFOLIO", "OWN_BRAND"])
def test_native_internal_tamper_with_recomputed_outer_pin_is_rejected(native_kind):
    if native_kind == "PORTFOLIO":
        record = recommend_portfolio(portfolio_request())
        value = record.to_dict()
        value["decision"] = "KEEP"
        forged = object.__new__(PortfolioRecommendation)
        raw = canonical_bytes(value)
        object.__setattr__(forged, "_raw", raw)
        sources = ScaleOptionSources(portfolio=replace(portfolio_selector(record), recommendation=forged, sha256=sha256(raw).hexdigest()))
    else:
        brief = build_brief(brand_request())
        value = brief.to_dict()
        value["calculations"]["capital_gap"]["value"] = "999.000000"
        forged = object.__new__(OwnBrandDecisionBrief)
        raw = canonical_bytes(value)
        object.__setattr__(forged, "_raw", raw)
        sources = ScaleOptionSources(own_brand=OwnBrandSource(forged, brief.brief_id, sha256(raw).hexdigest()))
    with pytest.raises(ScaleOptionError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_memo(request(), sources)


@pytest.mark.parametrize("observed,band", [(120, "KEEP"), (55, "CHANGE"), (0, "STOP")])
def test_m06_provisional_band_never_grants_action_authority(tmp_path, observed, band):
    snapshot, source = manual_provisional_case(tmp_path, observed)
    native_request = evaluation(snapshot)
    native_request["human_policy"]["requirement"] = "PROVISIONAL_LOCAL_DESCRIPTIVE"
    bundle = EvidenceBundle(reports=(source,))
    record = recommend_portfolio(native_request, bundle)
    assert record.to_dict()["decision"] == band
    body = for_portfolio(record)
    value = build_memo(body, ScaleOptionSources(portfolio_selector(record, bundle))).to_dict()
    assert value["sources"]["portfolio"]["decision"] == band
    assert value["sources"]["portfolio"]["observation_count"] == 1
    assert lane(value)["state"] == "CONDITIONAL_VALIDATE"
    body["human_policy"]["available_budget"] = "0"
    denied = build_memo(body, ScaleOptionSources(portfolio_selector(record, bundle))).to_dict()
    assert denied["decision"] == "DO_NOT_SCALE" and denied["execution_authority"] == "NONE"


def test_current_descriptive_requirement_cannot_use_an_archived_receipt(tmp_path):
    body, sources, record, _, _, _, _ = bridge(tmp_path, product=None)
    body["human_policy"]["evidence_requirement"] = "CURRENT_DESCRIPTIVE"
    # The original M06 market is unbound. Preserve the native scope exactly.
    body["scope"]["market"] = None
    current = build_memo(body, ScaleOptionSources(sources.portfolio)).to_dict()
    assert current["decision"] == "CONDITIONAL_VALIDATE"
    archived = build_memo(body, ScaleOptionSources(portfolio_selector(record))).to_dict()
    assert archived["decision"] == "DEFER"
    assert "CURRENT_DESCRIPTIVE_PORTFOLIO_REQUIRED" in lane(archived)["blocking_reasons"]


@pytest.mark.parametrize("field,value", [
    ("campaign_ref", "other"), ("concept_ref", "other"), ("product_ref", "other"),
    ("account_ref", {"namespace": "internal_alias", "id": "other"}), ("market", "US"),
    ("provider_ref", "other-provider"), ("platform", "YOUTUBE"), ("surface", "other"),
    ("creative_ref", "other-creative"),
    ("measurement_window", ["2026-10-30T00:00:00Z", "2026-10-31T00:00:00Z"]),
])
def test_exact_native_scope_mismatches_defer_instead_of_silent_join(field, value):
    native_request = portfolio_request()
    native_request["scope"].update(product_ref="fixture-product", provider_ref="fixture-provider", market="VN",
        account_ref={"namespace": "internal_alias", "id": "fixture-account"}, creative_ref="fixture-creative", creative_sha256="a" * 64)
    record = recommend_portfolio(native_request)
    body = for_portfolio(record)
    body["scope"][field] = value
    result = build_memo(body, ScaleOptionSources(portfolio_selector(record))).to_dict()
    assert lane(result)["state"] == "DEFER"
    assert "SOURCE_" + field.upper() + "_MISMATCH" in lane(result)["blocking_reasons"]


def test_stale_m06_and_m07_sources_cannot_be_renewed_by_e07_clock(tmp_path):
    body, sources, _, _, _, _, _ = bridge(tmp_path)
    body["as_of"] = "2027-01-03T00:00:00Z"
    body["scale"]["window"] = [body["as_of"], "2027-01-04T00:00:00Z"]
    result = build_memo(body, sources).to_dict()
    assert all("STALE_EVIDENCE" in row["blocking_reasons"] for row in result["options"])
    assert result["decision"] == "DEFER"


def test_m06_interior_timestamps_cannot_be_refreshed_with_new_clock_or_stricter_age():
    proposal, = propose_opportunities(manual_idea="Private old hypothetical brand idea", as_of="2026-08-01T00:00:00Z")
    raw = canonical_bytes(proposal)
    bundle = EvidenceBundle(proposals=(ProposalSource(raw, sha256(raw).hexdigest()),))
    original = portfolio_request(policy=True)
    original["human_policy"]["max_evidence_age_seconds"] = 31536000
    record = recommend_portfolio(original, bundle)
    body = for_portfolio(record)
    value = build_memo(body, ScaleOptionSources(portfolio_selector(record, bundle))).to_dict()
    assert "NATIVE_INTERIOR_FRESHNESS_NOT_RENEWED" in lane(value)["blocking_reasons"]
    assert value["sources"]["portfolio"]["observation_count"] == 0
    assert b"Private old" not in canonical_bytes(value)


def test_native_contradictory_measurements_and_currency_discrepancy_defer(tmp_path):
    snapshot, _, _, first = analytics(tmp_path, name="first", values=(-10,))
    _, _, _, second = analytics(tmp_path, name="second", values=(20,))
    bundle = EvidenceBundle(reports=(first, second))
    record = recommend_portfolio(evaluation(snapshot), bundle)
    body = for_portfolio(record)
    body["currency"] = "EUR"
    value = build_memo(body, ScaleOptionSources(portfolio_selector(record, bundle))).to_dict()
    assert {"SOURCE_CURRENCY_MISMATCH_NO_FX", "UNRESOLVED_NATIVE_COUNTEREVIDENCE", "PROVISIONAL_SOURCE_DISCREPANCY"} <= set(lane(value)["blocking_reasons"])
    assert value["decision"] == "DEFER"


def test_m07_stale_or_missing_lane_cannot_promote_or_block_other_lane_for_content(tmp_path):
    body, sources, _, brief, _, _, _ = bridge(tmp_path)
    body["scope"]["product_ref"] = None
    value = build_memo(body, ScaleOptionSources(own_brand=own_selector(brief))).to_dict()
    assert lane(value, "OWN_BRAND")["state"] == "NOT_APPLICABLE"
    assert value["decision"] == lane(value)["state"] == "CONDITIONAL_VALIDATE"
    body["scope"]["product_ref"] = "fixture-product"
    body["scale"] = None
    body["human_policy"]["max_unit_cost"] = None
    value = build_memo(body, sources).to_dict()
    assert lane(value)["state"] == "NOT_APPLICABLE"
    assert value["decision"] == lane(value, "OWN_BRAND")["state"] == "CONDITIONAL_VALIDATE"


def test_m07_conservative_no_go_reused_and_e07_caps_remain_external(tmp_path):
    body, sources, _, _, _, original, native_sources = bridge(tmp_path)
    original["human"]["costs"]["sale_price"]["amount"] = "0"
    brief = build_brief(original, native_sources)
    assert brief.to_dict()["decision"] == "NO_GO"
    value = build_memo(body, replace(sources, own_brand=own_selector(brief, original, native_sources))).to_dict()
    assert lane(value, "OWN_BRAND")["state"] == "DO_NOT_SCALE"
    assert value["decision"] == "DO_NOT_SCALE" and lane(value)["state"] == "CONDITIONAL_VALIDATE"
    assert value["sources"]["own_brand"]["feasibility"] == brief.to_dict()["calculations"]


@pytest.mark.parametrize("field,value,reason", [
    ("max_downside", "824.999999", "M07_DOWNSIDE_EXCEEDS_E07_CAP"),
    ("available_budget", "1099.999999", "M07_CAPITAL_REQUIREMENT_EXCEEDS_E07_BUDGET"),
    ("spend_ceiling", "49.999999", "M07_RESEARCH_BUDGET_EXCEEDS_E07_CAP"),
    ("max_experiment_assets", 1, "M07_RESEARCH_UNITS_EXCEED_E07_CAP"),
])
def test_e07_cannot_raise_m07_risk_capital_or_research_caps(tmp_path, field, value, reason):
    body, sources, _, _, _, _, _ = bridge(tmp_path)
    body["human_policy"][field] = value
    result = build_memo(body, sources).to_dict()
    assert lane(result, "OWN_BRAND")["state"] == "DO_NOT_SCALE"
    assert reason in lane(result, "OWN_BRAND")["blocking_reasons"]


@pytest.mark.parametrize("status", ["PENDING", "SETTLED", "REFUNDED", "REVOKED"])
def test_real_typed_a16_receipts_are_not_paid_orders_settlement_or_profit(tmp_path, native, status):
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    settlements.append(build(document_for(native[1], status, attr=native[2]), attribution=native[2]))
    selected = native_cost(costs)
    review = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selected])
    selector = review_selector(review, current=(native[1], settlements, costs))
    original = brand_for_review(review)
    native_sources = OwnBrandSources(commercial_review=selector)
    brief = build_brief(original, native_sources)
    body = for_review(review)
    value = build_memo(body, ScaleOptionSources(own_brand=own_selector(brief, original, native_sources), commercial_review=selector)).to_dict()
    row = value["sources"]["commercial_review"]
    assert row["custody"] == "CURRENT_REPLAY"
    assert row["actual_paid_orders"] == row["actual_settlement"] == row["realized_profit"] == "UNKNOWN"
    assert value["scale_calculations"] == build_memo(body).to_dict()["scale_calculations"]
    if status in {"REFUNDED", "REVOKED"}:
        assert lane(value, "OWN_BRAND")["state"] == "DEFER"
        assert "UNRESOLVED_REFUND_OR_REVOCATION" in lane(value, "OWN_BRAND")["blocking_reasons"]
    else:
        # Even a SETTLED report has unknown native fees/refunds in this fixture.
        assert row["pending"] and lane(value, "OWN_BRAND")["state"] == "DEFER"
    archive = build_memo(body, ScaleOptionSources(commercial_review=review_selector(review))).to_dict()
    assert archive["sources"]["commercial_review"]["custody"] == "ARCHIVED_ONLY"


def test_changed_refund_history_or_a15_cost_invalidates_original_a16_bytes(tmp_path, native):
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    paid = build(document_for(native[1], "SETTLED", attr=native[2]), attribution=native[2])
    settlements.append(paid)
    selection = native_cost(costs)
    review = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selection])
    source = review_selector(review, current=(native[1], settlements, costs))
    entry = costs.get(selection["entry_id"])
    path = registry.safe_path(CostLedger.ref(entry.entry_id))
    path.write_bytes(canonical_bytes(replace(entry, amount="9.99").to_record()))
    with pytest.raises(ScaleOptionError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_memo(for_review(review), ScaleOptionSources(commercial_review=source))
    path.write_bytes(canonical_bytes(entry.to_record()))
    doc = revision(paid, "REFUNDED")
    receipt(doc, "REFUND", "100.00")
    settlements.append(build(doc, attribution=native[2], correction_of=paid))
    with pytest.raises(ScaleOptionError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_memo(for_review(review), ScaleOptionSources(commercial_review=source))


@pytest.mark.parametrize("defect", ["sha", "id", "missing_id", "refs", "duplicate", "dict", "partial", "forged"])
def test_real_a16_validator_and_explicit_native_pins_are_mandatory(tmp_path, native, defect):
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    review = commercial_review(commerce_run=native[0], settlement_ledger=settlements, cost_ledger=costs)
    selected = review_selector(review)
    forged = object.__new__(CommercialReview)
    value = review.to_dict()
    value["actual_orders_verified"] = True
    raw = canonical_bytes(value)
    object.__setattr__(forged, "_raw", raw)
    source = {"sha": lambda: replace(selected, sha256="0" * 64),
        "id": lambda: replace(selected, review_id="a16-review-" + "0" * 64),
        "missing_id": lambda: replace(selected, review_id=None),
        "refs": lambda: replace(selected, native_refs=(("commerce_run", "0" * 64),)),
        "duplicate": lambda: replace(selected, native_refs=selected.native_refs + selected.native_refs[:1]),
        "dict": lambda: replace(selected, review=review.to_dict()),
        "partial": lambda: replace(selected, commerce_run=native[0]),
        "forged": lambda: replace(selected, review=forged, sha256=sha256(raw).hexdigest())}[defect]()
    with pytest.raises(ScaleOptionError):
        build_memo(for_review(review), ScaleOptionSources(commercial_review=source))


def test_a15_reference_shared_with_m06_and_a16_is_never_added_twice(tmp_path, native):
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    selection = native_cost(costs)
    entry = costs.get(selection["entry_id"])
    bundle = EvidenceBundle(costs=(CostSource(entry, sha256(canonical_bytes(entry.to_record())).hexdigest(), costs),))
    record = recommend_portfolio(portfolio_request(), bundle)
    review = commercial_review(commerce_run=native[0], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selection])
    body = for_review(review)
    value = build_memo(body, ScaleOptionSources(portfolio_selector(record, bundle), commercial_review=review_selector(review),
        costs=(cost_ref(costs, entry.entry_id),))).to_dict()
    assert value["scale_calculations"] == build_memo(body).to_dict()["scale_calculations"]
    assert value["sources"]["costs"][0]["allocation"] == "REFERENCE_ONLY_NO_NATIVE_SCOPE_BINDING"
    changed = replace(entry, amount="9.99")
    registry.safe_path(CostLedger.ref(entry.entry_id)).write_bytes(canonical_bytes(changed.to_record()))
    updated = commercial_review(commerce_run=native[0], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selection])
    with pytest.raises(ScaleOptionError, match="NATIVE_SOURCE_REVALIDATION_FAILED|CROSS_SOURCE"):
        build_memo(body, ScaleOptionSources(portfolio_selector(record), commercial_review=review_selector(updated)))


@pytest.mark.parametrize("status,amount", [("ESTIMATED", "0"), ("PENDING", None), ("UNKNOWN", None)])
def test_a15_unknown_vs_zero_reference_never_fills_experiment_assumptions(tmp_path, status, amount):
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    source = cost_source(ledger, status=status, amount=amount)
    body = request()
    sources = ScaleOptionSources(costs=(cost_ref(ledger, source.entry.entry_id),))
    value = build_memo(body, sources).to_dict()
    assert value["sources"]["costs"][0]["amount"] == amount
    assert value["scale_calculations"] == build_memo(body).to_dict()["scale_calculations"]
    body["scale"]["scenarios"]["base"]["costs"]["provider_cost"] = None
    assert build_memo(body, sources).to_dict()["decision"] == "DEFER"


def test_a15_duplicate_corrupt_changed_permission_and_currency_guards(tmp_path, monkeypatch):
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    source = cost_source(ledger)
    selected = cost_ref(ledger, source.entry.entry_id)
    with pytest.raises(ScaleOptionError, match="DUPLICATE_COST_REFERENCE"):
        build_memo(request(), ScaleOptionSources(costs=(selected, selected)))
    with pytest.raises(ScaleOptionError, match="SOURCE_HASH_PIN_MISMATCH"):
        build_memo(request(), ScaleOptionSources(costs=(replace(selected, sha256="0" * 64),)))
    with monkeypatch.context() as guarded:
        def denied(*args, **kwargs):
            raise PermissionError("PRIVATE source path cannot be read")
        guarded.setattr(ledger.registry, "read", denied)
        with pytest.raises(ScaleOptionError) as caught:
            build_memo(request(), ScaleOptionSources(costs=(selected,)))
        assert str(caught.value) == "NATIVE_SOURCE_REVALIDATION_FAILED"
    body = request()
    body["currency"] = "EUR"
    value = build_memo(body, ScaleOptionSources(costs=(selected,))).to_dict()
    assert "COST_REFERENCE_CURRENCY_MISMATCH_NO_FX" in lane(value)["blocking_reasons"]
    record = source.entry.to_record()
    record["amount"] = "888"
    ledger.registry.safe_path(CostLedger.ref(source.entry.entry_id)).write_bytes(canonical_bytes(record))
    with pytest.raises(ScaleOptionError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_memo(request(), ScaleOptionSources(costs=(selected,)))


def test_duplicate_a15_charged_receipt_under_new_id_is_not_independent_cost(tmp_path):
    ledger = CostLedger(ArtifactRegistry(tmp_path / "store"))
    first = cost_source(ledger, entry_id="first", status="CHARGED", origin="USER_SUPPLIED")
    second = cost_source(ledger, entry_id="second", status="CHARGED", origin="USER_SUPPLIED",
        job_id="other-job", evidence_ref="other-alias", evidence_sha256=first.entry.evidence_sha256)
    with pytest.raises(ScaleOptionError, match="DUPLICATE_COST_RECEIPT"):
        build_memo(request(), ScaleOptionSources(costs=(cost_ref(ledger, first.entry.entry_id), cost_ref(ledger, second.entry.entry_id))))


def test_m07_selected_m06_discrepancy_cannot_silently_join_different_recommendations(tmp_path):
    body, sources, record, _, _, _, _ = bridge(tmp_path)
    original = record.to_dict()["request"]
    original["candidate_id"] = "other-concept"
    different = recommend_portfolio(original, sources.portfolio.evidence)
    with pytest.raises(ScaleOptionError, match="M07_SELECTED_SOURCE_DISCREPANCY"):
        build_memo(body, replace(sources, portfolio=portfolio_selector(different, sources.portfolio.evidence)))


def test_private_projection_injection_and_archived_grade_tampering_rejected(tmp_path):
    body, sources, _, _, _, _, _ = bridge(tmp_path)
    memo = build_memo(body, sources).to_dict()
    for location in ("own_brand", "portfolio"):
        value = deepcopy(memo)
        value["sources"][location]["scope"]["account_ref"] = {"namespace": "internal_alias", "id": "file:private"}
        with pytest.raises(ScaleOptionError, match="URL_OR_CREDENTIAL_DENIED"):
            ScaleBrandDecisionMemo.from_bytes(canonical_bytes(reseal(value)))
    value = deepcopy(memo)
    value["sources"]["own_brand"]["feasibility"]["capital_gap"]["value"] = "999.000000"
    with pytest.raises(ScaleOptionError, match="M07_FEASIBILITY_PIN_MISMATCH"):
        ScaleBrandDecisionMemo.from_bytes(canonical_bytes(reseal(value)))
    value = deepcopy(memo)
    value["sources"]["own_brand"]["native_custody"]["portfolio"] = "ARCHIVED_ONLY"
    with pytest.raises(ScaleOptionError, match="M07_ARCHIVED_CUSTODY_PROMOTION"):
        ScaleBrandDecisionMemo.from_bytes(canonical_bytes(reseal(value)))


def test_build_and_archive_replay_have_no_network_writes_process_or_clock_effects(tmp_path, native, monkeypatch):
    native_directory = tmp_path / "native"
    native_directory.mkdir()
    body, sources, _, _, _, _, _ = bridge(native_directory)
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    selection = native_cost(costs)
    review = commercial_review(commerce_run=native[0], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selection])
    selected = review_selector(review, current=(native[0], settlements, costs))
    before = files_under(tmp_path)
    with monkeypatch.context() as guarded:
        deny_effects(guarded)
        one = build_memo(body, sources)
        two = build_memo(body, sources)
        three = build_memo(for_review(review), ScaleOptionSources(commercial_review=selected, costs=(cost_ref(costs, selection["entry_id"]),)))
        assert ScaleBrandDecisionMemo.from_bytes(one.to_bytes()) == one
    assert one.to_bytes() == two.to_bytes()
    assert three.to_dict()["execution_authority"] == "NONE"
    assert before == files_under(tmp_path)


def registered_plan():
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    from ai_commerce.engineering.minimum_test import approved_groups, file_hashes
    work = json.loads((ROOT / "work_items/A18-3-SCALE-BRAND-001.json").read_bytes())
    files = file_hashes(ROOT)
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_bytes())
    return registered_action_plan(repo=ROOT, base_sha=SOURCE_SHA,
        base_files={p: h for p, h in files.items() if p not in work["allowed_paths"]}, candidate_files=files,
        changed_paths=work["allowed_paths"], required_acs=work["acceptance"], allowed_paths=work["allowed_paths"],
        fallback_argv=profiles["mt01-component-onboarding"], groups=approved_groups(), registered_profiles=profiles)


def test_actual_registered_owner_manifest_and_unchanged_protected_group_closure():
    manifest = json.loads((ROOT / "work_items/mt01-components/a18-3.json").read_bytes())
    assert manifest == {"schema_version": "MT01ComponentOwner.v1", "component": "a18-3",
        "sources": ["src/ai_commerce/opportunity/scale_option/" + n + ".py" for n in ("__init__", "schemas", "sources", "memo")],
        "tests": ["tests/test_a18_scale_option.py", "tests/test_a18_scale_option_integration.py"],
        "acs": ["A18.E07.offline-scale-brand-decision-memo"],
        "consumers": ["src/ai_commerce/opportunity/portfolio/recommend.py", "src/ai_commerce/opportunity/own_brand/brief.py",
            "src/ai_commerce/opportunity/own_brand/sources.py", "src/ai_commerce/settlement/review.py", "src/ai_commerce/operations/cost_ledger.py"],
        "contracts": ["contracts/ScaleBrandDecisionMemo.v1.schema.json"]}
    # Every direct domain dependency is one of the five proven consumer interfaces.
    for source in manifest["sources"]:
        for node in ast.walk(ast.parse((ROOT / source).read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("ai_commerce."):
                assert "src/" + node.module.replace(".", "/") + ".py" in manifest["consumers"]
    plan = registered_plan()
    assert plan["selected_groups"] == ["a15", "a16", "a18-1", "a18-2", "a18-3"]
    assert not plan["fallback"] and plan["unknown_acs"] == [] and plan["impact_v21"]["missing_mapping"] == ()
    assert all(g["execute"] and g["state"] == "EXECUTE" and g["binding"]["argv"][-2:] == ["-n", "12"] for g in plan["groups"])
    own = next(g for g in plan["groups"] if g["group"] == "a18-3")
    assert own["selected_profile"] == "mt01-component-onboarding" and own["binding"]["onboarding_data_only"]
    assert own["binding"]["argv"] == ["python", "-m", "pytest", *manifest["tests"], "-q", "-n", "12"]


def test_owner_mapping_refuses_unknown_consumer_without_broad_suite_fallback():
    from ai_commerce.engineering.impact_v21 import validate_component_onboarding_manifest, load_baseline_component_owner_records
    from ai_commerce.engineering.minimum_test import approved_groups, file_hashes
    work = json.loads((ROOT / "work_items/A18-3-SCALE-BRAND-001.json").read_bytes())
    data = json.loads((ROOT / work["owner_manifest"]).read_bytes())
    files = file_hashes(ROOT)
    original = approved_groups()
    groups = (*original, *(r["group"] for r in load_baseline_component_owner_records(root=ROOT, base_sha=SOURCE_SHA, existing_groups=original)))
    data["consumers"].append("src/ai_commerce/unknown/unmapped.py")
    with pytest.raises(ValueError, match="ONBOARD_CONSUMER_UNPROVEN"):
        validate_component_onboarding_manifest(data, manifest_path=work["owner_manifest"], changed_paths=work["allowed_paths"],
            base_paths=[p for p in files if p not in work["allowed_paths"]], existing_groups=groups,
            allowed_paths=work["allowed_paths"], root=ROOT)


def test_worker_proof_requires_actual_complete_collection_and_windows_profile():
    from ai_commerce.engineering.verification import observed_xdist_workers, pytest_observation_env
    assert "ai_commerce.engineering.pytest_profile" in pytest_observation_env()["PYTEST_PLUGINS"].split(",")
    assert observed_xdist_workers(b"created: 12/12 workers\n12 workers [100 items]\n") is None
    assert observed_xdist_workers(b"MT01_XDIST_WORKERS_COLLECTED=12\n") == 12
    assert observed_xdist_workers(b"MT01_XDIST_WORKERS_COLLECTED=4\n") != 12
    assert observed_xdist_workers(b"MT01_XDIST_WORKERS_COLLECTED=12\n" * 2) is None
