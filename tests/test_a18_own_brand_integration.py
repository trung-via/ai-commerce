"""Real M06/A16 native replay and registered closure; no live/provider attestation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.cost_ledger import CostLedger
from ai_commerce.opportunity.own_brand import (
    CommercialSource, OwnBrandDecisionBrief, OwnBrandError, OwnBrandSources, PortfolioSource, build_brief,
)
from ai_commerce.opportunity.own_brand.schemas import SOURCE_SHA
from ai_commerce.opportunity.portfolio import (
    CostSource, EvidenceBundle, PortfolioRecommendation, ProposalSource, own_brand_reference, recommend_portfolio,
)
from ai_commerce.settlement import CommercialReview, SettlementLedger, commercial_review

# Existing factories use actual native constructors and validators unchanged.
from test_a18_own_brand import offline, request, metric, reseal
from test_a18_portfolio import request as portfolio_request
from test_a18_portfolio_integration import (
    analytics, evaluation, manual_provisional_case, files_under, deny_effects, propose_opportunities,
)
from test_a16_commercial_review import native, document_for, cost as native_cost
from test_a16_settlement import build, receipt, revision

ROOT = Path(__file__).resolve().parents[1]


def portfolio_selector(record, evidence=None):
    return PortfolioSource(record, record.recommendation_id, sha256(record.to_bytes()).hexdigest(),
                           canonical_bytes(own_brand_reference(record)), evidence)


def for_portfolio(record):
    body = request(synthetic=False)
    native_request = record.to_dict()["request"]
    scope = native_request["scope"]
    body["scope"].update({key: deepcopy(scope[key]) for key in ("campaign_ref", "product_ref", "account_ref")})
    body["scope"]["concept_ref"] = native_request["candidate_id"]
    body["scope"]["market"] = scope["market"] or "VN"
    body["scope"]["measurement_window"] = deepcopy(native_request["measurement_window"])
    body["currency"] = native_request["currency"]
    body["as_of"] = native_request["as_of"]
    return body


def review_selector(review, *, current=None):
    source = review.to_dict()["sources"]
    refs = [("commerce_run", source["commerce_run"]["raw_sha256"]),
            ("settlement_history", source["settlement_history"]["raw_sha256"])]
    refs.extend(("cost:" + row["entry_id"], sha256(canonical_bytes(row)).hexdigest()) for row in source["cost_records"])
    return CommercialSource(review, review.review_id, sha256(review.to_bytes()).hexdigest(), tuple(sorted(refs)),
                            *(current or (None, None, None)))


def for_review(review):
    body = request()
    scope = review.to_dict()["scope"]
    body["scope"].update(product_ref=scope["product_id"], account_ref=deepcopy(scope["account_ref"]),
                          market=scope["market"], measurement_window=deepcopy(scope["measurement_window"]))
    return body


def test_exact_native_m06_bytes_reference_and_archive_versus_current(tmp_path):
    snapshot, _, _, source = analytics(tmp_path, values=(12,))
    bundle = EvidenceBundle(reports=(source,))
    record = recommend_portfolio(evaluation(snapshot), bundle)
    before, reference = record.to_bytes(), canonical_bytes(own_brand_reference(record))
    body = for_portfolio(record)
    archived = build_brief(body, OwnBrandSources(portfolio_selector(record))).to_dict()
    current = build_brief(body, OwnBrandSources(portfolio_selector(record, bundle))).to_dict()
    assert record.to_bytes() == before and canonical_bytes(own_brand_reference(record)) == reference
    assert archived["sources"]["portfolio"]["custody"] == "ARCHIVED_ONLY"
    assert archived["demand"]["descriptive_observation_count"] == 0
    assert "PORTFOLIO_ARCHIVED_ONLY_NO_RENEWED_AUTHORITY" in archived["reason_codes"]
    assert current["sources"]["portfolio"]["custody"] == "CURRENT_REPLAY"
    assert current["demand"]["descriptive_observation_count"] == 1
    assert current["sources"]["portfolio"]["reference"] == own_brand_reference(record)
    assert current["sources"]["portfolio"]["reference_sha256"] == sha256(reference).hexdigest()
    assert current["sources"]["portfolio"]["reference"]["source_sha"] == record.to_dict()["source_sha"]
    assert current["sources"]["portfolio"]["reference"]["source_sha"] != SOURCE_SHA
    assert current["brief_id"] != archived["brief_id"]
    assert current["evidence_grade"] == "SYNTHETIC_NON_ATTESTED"
    assert current["demand"]["proven_demand"] is False


def test_actual_reread_detects_deleted_native_source_archive_does_not_renew(tmp_path):
    snapshot, origin, _, source = analytics(tmp_path, values=(12,))
    bundle = EvidenceBundle(reports=(source,))
    record = recommend_portfolio(evaluation(snapshot), bundle)
    sources = OwnBrandSources(portfolio_selector(record, bundle))
    original = build_brief(for_portfolio(record), sources)
    origin.unlink()
    with pytest.raises(OwnBrandError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_brief(for_portfolio(record), sources)
    assert OwnBrandDecisionBrief.from_bytes(original.to_bytes(), expected_id=original.brief_id) == original
    archived = build_brief(for_portfolio(record), OwnBrandSources(portfolio_selector(record))).to_dict()
    assert archived["sources"]["portfolio"]["custody"] == "ARCHIVED_ONLY"
    assert archived["sources"]["portfolio"]["renewed_execution_authority"] == "NONE"


@pytest.mark.parametrize("mutation", [
    lambda s: replace(s, sha256="0" * 64),
    lambda s: replace(s, recommendation_id="a18-portfolio-" + "0" * 64),
    lambda s: replace(s, recommendation_id=None),
    lambda s: replace(s, reference_bytes=s.reference_bytes + b" "),
    lambda s: replace(s, reference_bytes=b'{"private":"https://example.test"}'),
    lambda s: replace(s, recommendation=s.recommendation.to_dict()),
    lambda s: replace(s, evidence={}),
])
def test_changed_m06_pin_id_reference_or_typed_selector_cannot_pass(mutation):
    record = recommend_portfolio(portfolio_request())
    with pytest.raises(OwnBrandError):
        build_brief(for_portfolio(record), OwnBrandSources(mutation(portfolio_selector(record))))


def test_changed_original_bundle_and_duplicate_evidence_do_not_reuse_old_m06(tmp_path):
    snapshot, _, _, source = analytics(tmp_path, values=(10,))
    record = recommend_portfolio(evaluation(snapshot), EvidenceBundle(reports=(source,)))
    with pytest.raises(OwnBrandError, match="PORTFOLIO_CURRENT_REPLAY_MISMATCH"):
        build_brief(for_portfolio(record), OwnBrandSources(portfolio_selector(record, EvidenceBundle())))
    duplicate = EvidenceBundle(reports=(source, source))
    with pytest.raises(OwnBrandError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_brief(for_portfolio(record), OwnBrandSources(portfolio_selector(record, duplicate)))


def test_native_m06_internal_corruption_is_revalidated_even_with_new_outer_hash():
    record = recommend_portfolio(portfolio_request())
    forged_body = record.to_dict()
    forged_body["decision"] = "KEEP"
    forged = object.__new__(PortfolioRecommendation)
    raw = canonical_bytes(forged_body)
    object.__setattr__(forged, "_raw", raw)
    selector = PortfolioSource(forged, record.recommendation_id, sha256(raw).hexdigest(), b"{}")
    with pytest.raises(OwnBrandError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_brief(for_portfolio(record), OwnBrandSources(selector))


def test_actual_a10_manual_proposal_is_only_a_hypothesis_and_private_text_stays_out():
    proposal, = propose_opportunities(manual_idea="PRIVATE-A10 hypothetical creative direction",
                                      as_of="2026-11-03T00:00:00Z")
    raw = canonical_bytes(proposal)
    bundle = EvidenceBundle(proposals=(ProposalSource(raw, sha256(raw).hexdigest()),))
    record = recommend_portfolio(portfolio_request(), bundle)
    value = build_brief(for_portfolio(record), OwnBrandSources(portfolio_selector(record, bundle)))
    body = value.to_dict()
    assert body["sources"]["portfolio"]["native_sources"][0]["epistemic_status"] == "HYPOTHESIS"
    assert body["demand"]["descriptive_observation_count"] == 0
    assert body["demand"]["observed_market_demand"] == "UNKNOWN" and not body["demand"]["proven_demand"]
    assert b"PRIVATE-A10" not in value.to_bytes()
    later = for_portfolio(record)
    later["as_of"] = "2026-11-03T01:00:00Z"
    assert "PORTFOLIO_INTERIOR_FRESHNESS_NOT_RENEWED" in build_brief(later, OwnBrandSources(portfolio_selector(record, bundle))).to_dict()["blocking_reasons"]


def test_archived_brief_native_reference_fields_cannot_smuggle_url_or_path_injection():
    record = recommend_portfolio(portfolio_request())
    value = build_brief(for_portfolio(record), OwnBrandSources(portfolio_selector(record))).to_dict()
    value["sources"]["portfolio"]["scope"]["account_ref"] = {"namespace": "internal_alias", "id": "file:private"}
    with pytest.raises(OwnBrandError, match="URL_OR_CREDENTIAL_DENIED"):
        OwnBrandDecisionBrief.from_bytes(canonical_bytes(reseal(value)))


def test_stricter_m07_age_policy_cannot_refresh_old_a10_evidence_via_original_m06_clock():
    proposal, = propose_opportunities(manual_idea="Fictional old creative hypothesis",
                                      as_of="2026-08-01T00:00:00Z")
    raw = canonical_bytes(proposal)
    bundle = EvidenceBundle(proposals=(ProposalSource(raw, sha256(raw).hexdigest()),))
    original_request = portfolio_request(policy=True)
    original_request["human_policy"]["max_evidence_age_seconds"] = 31536000
    record = recommend_portfolio(original_request, bundle)
    assert "STALE_EVIDENCE" not in record.to_dict()["reason_codes"]
    result = build_brief(for_portfolio(record), OwnBrandSources(portfolio_selector(record, bundle))).to_dict()
    assert result["sources"]["portfolio"]["native_max_evidence_age_seconds"] == 31536000
    assert result["decision"] == "DEFER"
    assert "PORTFOLIO_INTERIOR_FRESHNESS_NOT_RENEWED" in result["blocking_reasons"]
    assert result["demand"]["proven_demand"] is False


@pytest.mark.parametrize("observed,band", [(120, "KEEP"), (55, "CHANGE"), (0, "STOP")])
def test_provisional_m06_band_is_descriptive_not_provider_or_purchase_truth(tmp_path, observed, band):
    snapshot, source = manual_provisional_case(tmp_path, observed)
    evaluation_request = evaluation(snapshot)
    evaluation_request["human_policy"]["requirement"] = "PROVISIONAL_LOCAL_DESCRIPTIVE"
    bundle = EvidenceBundle(reports=(source,))
    record = recommend_portfolio(evaluation_request, bundle)
    assert record.to_dict()["decision"] == band
    body = for_portfolio(record)
    body["human_policy"]["allow_hypothesis_only_research"] = False
    result = build_brief(body, OwnBrandSources(portfolio_selector(record, bundle))).to_dict()
    assert result["decision"] == "CONDITIONAL_GO"
    assert result["sources"]["portfolio"]["reference"]["decision"] == band
    assert result["sources"]["portfolio"]["interpretation"] == "HUMAN_SUPPLIED_DESCRIPTIVE_ONLY"
    assert result["evidence_grade"] == "USER_SUPPLIED_DESCRIPTIVE_UNATTESTED"
    assert result["demand"]["observed_market_demand"] == "UNKNOWN"
    assert result["external_live_verified"] is False
    archived = build_brief(body, OwnBrandSources(portfolio_selector(record))).to_dict()
    assert archived["decision"] == "DEFER"
    assert "CURRENT_COMPARABLE_DESCRIPTIVE_EVIDENCE_REQUIRED" in archived["blocking_reasons"]


@pytest.mark.parametrize("field,value,reason", [
    ("campaign_ref", "another-campaign", "SOURCE_CAMPAIGN_REF_MISMATCH"),
    ("concept_ref", "another-concept", "SOURCE_CONCEPT_REF_MISMATCH"),
    ("product_ref", "another-product", "SOURCE_PRODUCT_REF_MISMATCH"),
    ("market", "US", "SOURCE_MARKET_MISMATCH"),
    ("account_ref", {"namespace": "internal_alias", "id": "another"}, "SOURCE_ACCOUNT_REF_MISMATCH"),
    ("measurement_window", ["2026-10-31T00:00:00Z", "2026-11-01T00:00:00Z"], "SOURCE_MEASUREMENT_WINDOW_MISMATCH"),
])
def test_native_scope_identity_and_market_mismatches_hold(field, value, reason):
    native_request = portfolio_request()
    native_request["scope"].update(product_ref="fixture-product", market="VN",
                                  account_ref={"namespace": "internal_alias", "id": "fixture-account"})
    record = recommend_portfolio(native_request)
    body = for_portfolio(record)
    body["scope"][field] = value
    result = build_brief(body, OwnBrandSources(portfolio_selector(record, EvidenceBundle()))).to_dict()
    assert result["decision"] == "DEFER" and reason in result["blocking_reasons"]


def test_native_currency_staleness_and_counterevidence_cannot_be_ignored(tmp_path):
    snapshot, _, _, source = analytics(tmp_path, values=(-10,))
    bundle = EvidenceBundle(reports=(source,))
    record = recommend_portfolio(evaluation(snapshot), bundle)
    body = for_portfolio(record)
    body["currency"] = "EUR"
    body["as_of"] = "2027-01-03T00:00:00Z"
    result = build_brief(body, OwnBrandSources(portfolio_selector(record, bundle))).to_dict()
    assert result["decision"] == "DEFER"
    assert {"SOURCE_CURRENCY_MISMATCH_NO_FX", "STALE_EVIDENCE", "UNRESOLVED_NATIVE_COUNTEREVIDENCE"} <= set(result["blocking_reasons"])
    assert result["counterevidence"] and result["evidence_grade"] == "CONFLICTED_UNATTESTED"


@pytest.mark.parametrize("status", ["PENDING", "SETTLED"])
def test_real_typed_l05_review_native_refs_without_trusting_paid_orders(tmp_path, native, status):
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    settlements.append(build(document_for(native[1], status, attr=native[2]), attribution=native[2]))
    selected = native_cost(costs)
    review = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs,
                               cost_selections=[selected])
    source = review_selector(review, current=(native[1], settlements, costs))
    before = review.to_bytes()
    body = for_review(review)
    plain = build_brief(body).to_dict()
    result = build_brief(body, OwnBrandSources(commercial_review=source)).to_dict()
    actual = result["sources"]["commercial_review"]
    assert review.to_bytes() == before and actual["custody"] == "CURRENT_REPLAY"
    assert actual["native_refs"] == [{"ref": key, "sha256": sha} for key, sha in source.native_refs]
    assert actual["actual_paid_orders"] == actual["actual_settlement"] == actual["realized_profit"] == "UNKNOWN"
    assert actual["costs_added_to_scenario"] is False
    assert actual["attestation"] == "NOT_PROVIDER_ATTESTED"
    assert result["calculations"] == plain["calculations"]
    assert "PENDING_FINANCIAL_EVIDENCE_NOT_ZERO" in result["reason_codes"]
    assert result["external_live_verified"] is False
    archived = build_brief(body, OwnBrandSources(commercial_review=review_selector(review))).to_dict()
    assert archived["sources"]["commercial_review"]["custody"] == "ARCHIVED_ONLY"


@pytest.mark.parametrize("field", ["actual_orders_verified", "actual_settlement_status", "realized_profit", "external_provider_charge_verified"])
def test_real_commercial_from_bytes_rejects_forged_paid_settlement_or_profit_flags(tmp_path, native, field):
    registry = ArtifactRegistry(tmp_path / "store")
    review = commercial_review(commerce_run=native[0], settlement_ledger=SettlementLedger(registry), cost_ledger=CostLedger(registry))
    body = review.to_dict()
    body[field] = {"status": "REPORTED", "amount": "999", "verified": True} if field == "realized_profit" else "SETTLED" if field == "actual_settlement_status" else True
    forged = object.__new__(CommercialReview)
    raw = canonical_bytes(body)
    object.__setattr__(forged, "_raw", raw)
    selector = replace(review_selector(review), review=forged, sha256=sha256(raw).hexdigest())
    with pytest.raises(OwnBrandError, match="NATIVE_SOURCE_REVALIDATION_FAILED"):
        build_brief(for_review(review), OwnBrandSources(commercial_review=selector))


def test_real_native_review_reread_detects_cost_change_and_settlement_refund(tmp_path, native):
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    selected = native_cost(costs)
    paid = build(document_for(native[1], "SETTLED", attr=native[2]), attribution=native[2])
    settlements.append(paid)
    review = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selected])
    selector = review_selector(review, current=(native[1], settlements, costs))
    original_entry = costs.get(selected["entry_id"])
    replacement = replace(original_entry, amount="3.50")
    path = registry.safe_path(CostLedger.ref(selected["entry_id"]))
    path.write_bytes(canonical_bytes(replacement.to_record()))
    with pytest.raises(OwnBrandError, match="COMMERCIAL_CURRENT_REPLAY_MISMATCH"):
        build_brief(for_review(review), OwnBrandSources(commercial_review=selector))
    path.write_bytes(canonical_bytes(original_entry.to_record()))
    doc = revision(paid, "REFUNDED")
    receipt(doc, "REFUND", "100.00")
    settlements.append(build(doc, attribution=native[2], correction_of=paid))
    with pytest.raises(OwnBrandError, match="COMMERCIAL_CURRENT_REPLAY_MISMATCH"):
        build_brief(for_review(review), OwnBrandSources(commercial_review=selector))
    newer = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selected])
    updated = build_brief(for_review(newer), OwnBrandSources(commercial_review=review_selector(newer, current=(native[1], settlements, costs)))).to_dict()
    assert updated["decision"] == "DEFER"
    assert {"A16_UPSTREAM_HOLD", "UNRESOLVED_REFUND_OR_REVOCATION"} <= set(updated["blocking_reasons"])
    assert {row["code"] for row in updated["counterevidence"]} == {"A16_UPSTREAM_HOLD", "SETTLEMENT_REFUND_OR_REVOCATION"}


@pytest.mark.parametrize("defect", ["sha", "id", "missing_id", "refs", "duplicate", "dict", "partial"])
def test_review_pin_and_exact_native_source_references_required(tmp_path, native, defect):
    registry = ArtifactRegistry(tmp_path / "store")
    review = commercial_review(commerce_run=native[0], settlement_ledger=SettlementLedger(registry), cost_ledger=CostLedger(registry))
    source = review_selector(review)
    source = {"sha": lambda: replace(source, sha256="0" * 64),
        "id": lambda: replace(source, review_id="wrong"),
        "missing_id": lambda: replace(source, review_id=None),
        "refs": lambda: replace(source, native_refs=(("commerce_run", "0" * 64), source.native_refs[1])),
        "duplicate": lambda: replace(source, native_refs=source.native_refs + source.native_refs[:1]),
        "dict": lambda: replace(source, review=review.to_dict()),
        "partial": lambda: replace(source, commerce_run=native[0])}[defect]()
    with pytest.raises(OwnBrandError):
        build_brief(for_review(review), OwnBrandSources(commercial_review=source))


def test_shared_a15_cost_is_reference_only_and_conflicting_native_versions_hold(tmp_path, native):
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    selected = native_cost(costs)
    entry = costs.get(selected["entry_id"])
    bundle = EvidenceBundle(costs=(CostSource(entry, sha256(canonical_bytes(entry.to_record())).hexdigest(), costs),))
    record = recommend_portfolio(portfolio_request(), bundle)
    review = commercial_review(commerce_run=native[0], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selected])
    body = for_review(review)
    body["scope"].update(campaign_ref=record.to_dict()["request"]["scope"]["campaign_ref"],
                          concept_ref=record.to_dict()["request"]["candidate_id"])
    result = build_brief(body, OwnBrandSources(portfolio_selector(record, bundle),
        review_selector(review, current=(native[0], settlements, costs)))).to_dict()
    assert result["calculations"] == build_brief(body).to_dict()["calculations"]
    assert result["sources"]["commercial_review"]["costs_added_to_scenario"] is False
    changed = replace(entry, amount="9.99")
    registry.safe_path(CostLedger.ref(entry.entry_id)).write_bytes(canonical_bytes(changed.to_record()))
    updated_review = commercial_review(commerce_run=native[0], settlement_ledger=settlements, cost_ledger=costs, cost_selections=[selected])
    with pytest.raises(OwnBrandError, match="CROSS_SOURCE_COST_VERSION_MISMATCH"):
        build_brief(body, OwnBrandSources(portfolio_selector(record), review_selector(updated_review)))


def test_commercial_market_product_account_window_and_currency_mismatches_defer(tmp_path, native):
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    settlements.append(build(document_for(native[1], "SETTLED", attr=native[2]), attribution=native[2]))
    review = commercial_review(commerce_run=native[1], settlement_ledger=settlements, cost_ledger=costs)
    for field, value in (("market", "US"), ("product_ref", "other-product"),
        ("account_ref", {"namespace": "internal_alias", "id": "other"}),
        ("measurement_window", ["2026-10-30T00:00:00Z", "2026-10-31T00:00:00Z"])):
        body = for_review(review)
        body["scope"][field] = value
        result = build_brief(body, OwnBrandSources(commercial_review=review_selector(review))).to_dict()
        assert result["decision"] == "DEFER" and "SOURCE_" + field.upper() + "_MISMATCH" in result["blocking_reasons"]
    body = for_review(review)
    body["currency"] = "EUR"
    assert "SOURCE_CURRENCY_MISMATCH_NO_FX" in build_brief(body, OwnBrandSources(commercial_review=review_selector(review))).to_dict()["blocking_reasons"]


def test_native_replay_under_no_network_no_writes_no_process_guards(tmp_path, native, monkeypatch):
    snapshot, _, _, source = analytics(tmp_path, values=(12,))
    bundle = EvidenceBundle(reports=(source,))
    record = recommend_portfolio(evaluation(snapshot), bundle)
    registry = ArtifactRegistry(tmp_path / "store")
    settlements, costs = SettlementLedger(registry), CostLedger(registry)
    review = commercial_review(commerce_run=native[0], settlement_ledger=settlements, cost_ledger=costs)
    before = files_under(tmp_path)
    with monkeypatch.context() as guarded:
        deny_effects(guarded)
        one = build_brief(for_portfolio(record), OwnBrandSources(portfolio_selector(record, bundle)))
        two = build_brief(for_review(review), OwnBrandSources(commercial_review=review_selector(review, current=(native[0], settlements, costs))))
    assert one.to_dict()["execution_authority"] == two.to_dict()["execution_authority"] == "NONE"
    assert before == files_under(tmp_path)


def test_source_owner_manifest_exactness_and_actual_registered_onboarding_closure():
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    from ai_commerce.engineering.minimum_test import approved_groups, file_hashes
    manifest = json.loads((ROOT / "work_items/mt01-components/a18-2.json").read_bytes())
    work = json.loads((ROOT / "work_items/A18-2-OWN-BRAND-001.json").read_bytes())
    assert manifest == {"schema_version": "MT01ComponentOwner.v1", "component": "a18-2",
        "sources": ["src/ai_commerce/opportunity/own_brand/" + n + ".py" for n in ("__init__", "schemas", "sources", "brief")],
        "tests": ["tests/test_a18_own_brand.py", "tests/test_a18_own_brand_integration.py"],
        "acs": ["A18.M07.own-brand-offline-evidence-bound-go-no-go-brief"],
        "consumers": ["src/ai_commerce/opportunity/portfolio/recommend.py", "src/ai_commerce/opportunity/portfolio/schemas.py",
            "src/ai_commerce/settlement/review.py", "src/ai_commerce/settlement/ledger.py", "src/ai_commerce/operations/cost_ledger.py"],
        "contracts": ["contracts/OwnBrandDecisionBrief.v1.schema.json"]}
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_bytes())
    files, allowed = file_hashes(ROOT), work["allowed_paths"]
    plan = registered_action_plan(repo=ROOT, base_sha=SOURCE_SHA,
        base_files={p: v for p, v in files.items() if p not in allowed}, candidate_files=files,
        changed_paths=allowed, required_acs=work["acceptance"], allowed_paths=allowed,
        fallback_argv=profiles["mt01-component-onboarding"], groups=approved_groups(), registered_profiles=profiles)
    assert plan["selected_groups"] == ["a15", "a16", "a18-1", "a18-2"]
    assert plan["unknown_acs"] == [] and not plan["fallback"]
    assert plan["impact_v21"]["missing_mapping"] == ()
    assert all(g["execute"] and g["state"] == "EXECUTE" for g in plan["groups"])
    group = next(g for g in plan["groups"] if g["group"] == "a18-2")
    assert group["selected_profile"] == "mt01-component-onboarding"
    assert group["binding"]["onboarding_data_only"] is True
    assert group["binding"]["argv"] == ["python", "-m", "pytest", *manifest["tests"], "-q", "-n", "12"]
    for g in plan["groups"]:
        assert g["binding"]["argv"][-2:] == ["-n", "12"]
        if g["group"] != "a18-2" and g["group"] != "a18-1":
            assert g["binding"]["argv"] == profiles[g["selected_profile"]]


def test_unknown_owner_or_consumer_produces_precise_hold_not_fake_full_suite():
    from ai_commerce.engineering.impact_v21 import (
        load_baseline_component_owner_records, registered_action_plan, validate_component_onboarding_manifest,
    )
    from ai_commerce.engineering.minimum_test import approved_groups, file_hashes
    work = json.loads((ROOT / "work_items/A18-2-OWN-BRAND-001.json").read_bytes())
    data = json.loads((ROOT / work["owner_manifest"]).read_bytes())
    files = file_hashes(ROOT)
    original = approved_groups()
    groups = (*original, *(r["group"] for r in load_baseline_component_owner_records(root=ROOT, base_sha=SOURCE_SHA, existing_groups=original)))
    data["consumers"].append("src/ai_commerce/unknown/unmapped.py")
    with pytest.raises(ValueError, match="ONBOARD_CONSUMER_UNPROVEN"):
        validate_component_onboarding_manifest(data, manifest_path=work["owner_manifest"], changed_paths=work["allowed_paths"],
            base_paths=[p for p in files if p not in work["allowed_paths"]], existing_groups=groups,
            allowed_paths=work["allowed_paths"], root=ROOT)
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_bytes())
    plan = registered_action_plan(repo=ROOT, base_sha=SOURCE_SHA,
        base_files={p: v for p, v in files.items() if p not in work["allowed_paths"]}, candidate_files=files,
        changed_paths=[*work["allowed_paths"], "src/ai_commerce/unknown/unmapped.py"], required_acs=work["acceptance"],
        allowed_paths=work["allowed_paths"], fallback_argv=profiles["mt01-component-onboarding"],
        groups=original, registered_profiles=profiles)
    assert all(not g["execute"] for g in plan["groups"])
    assert any("ONBOARD_CHANGED_OUT_OF_SCOPE" in code for g in plan["groups"] for code in g["explanation"])
    assert plan["fallback"] is False


def test_self_host_receipt_contract_requires_actual_completed_worker_collections():
    from ai_commerce.engineering.verification import observed_xdist_workers, pytest_observation_env
    assert "ai_commerce.engineering.pytest_profile" in pytest_observation_env()["PYTEST_PLUGINS"].split(",")
    # Startup/requested counts cannot substitute for the actual hook receipt.
    assert observed_xdist_workers(b"created: 12/12 workers\n12 workers [42 items]\n") is None
    assert observed_xdist_workers(b"MT01_XDIST_WORKERS_COLLECTED=12\n") == 12
    assert observed_xdist_workers(b"MT01_XDIST_WORKERS_COLLECTED=12\nMT01_XDIST_WORKERS_COLLECTED=12\n") is None
    assert observed_xdist_workers(b"MT01_XDIST_WORKERS_COLLECTED=4\n") != 12
