"""M06 contract, boundary and deterministic decision tests. No live outcomes."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import http.client
import json
from pathlib import Path
import socket
import urllib.request

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.opportunity.portfolio import (
    EvidenceBundle, PortfolioError, PortfolioRecommendation, own_brand_reference,
    recommend_many, recommend_portfolio, schema, validate_recommendation,
)
from ai_commerce.opportunity.portfolio.schemas import SOURCE_SHA, bounded, digest, parse

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("M06 attempted an outbound effect")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", deny)


def request(*, candidate="creative-only", policy=False):
    metric = {"schema_version": "MetricDefinition.v1", "metric_key": "plays",
        "provider_name": "Synthetic plays", "definition_version": "fixture-1",
        "platform": "TIKTOK", "unit": "count", "definition": "Offline fixture count",
        "denominator": None, "cohort": "fixture-audience",
        "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE",
        "source_status": "USER_SUPPLIED_NOT_LIVE_VERIFIED"}
    body = {"schema_version": "PortfolioEvaluation.v1", "candidate_id": candidate,
        "scope": {"campaign_ref": "fixture-campaign", "creative_ref": None,
            "creative_sha256": None, "product_ref": None, "provider_ref": None,
            "market": None, "platform": "TIKTOK", "surface": "VIDEO", "account_ref": None,
            "cohort": "fixture-audience", "timezone": "UTC", "attribution_tier": "NOT_AVAILABLE"},
        "as_of": "2026-11-03T00:00:00Z",
        "measurement_window": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"],
        "baseline_window": None, "currency": "USD", "metric": metric, "human_policy": None}
    if policy:
        body["human_policy"] = {"schema_version": "PortfolioHumanPolicy.v1", "policy_id": "human-v1",
            "authored_by": "HUMAN_DECLARED_NOT_AUTHENTICATED",
            "valid_from": "2026-11-01T00:00:00Z", "valid_until": "2026-12-01T00:00:00Z",
            "max_evidence_age_seconds": 2592000, "metric_definition_sha256": digest(metric),
            "keep_at_or_above": "100", "stop_at_or_below": "10", "between": "CHANGE",
            "currency": "USD", "requirement": "COMPARABLE_AUTHORITATIVE_OBSERVATIONS"}
    return body


def reseal(value):
    value = deepcopy(value)
    value["recommendation_id"] = "a18-portfolio-" + digest({k: v for k, v in value.items() if k != "recommendation_id"})
    return value


def test_creative_only_without_product_account_or_business_facts():
    value = recommend_portfolio(request()).to_dict()
    assert value["decision"] == "INCONCLUSIVE"
    assert value["evidence_quality"] == "MISSING"
    assert value["sources"] == value["measurements"] == value["cost_totals"] == []
    assert value["request"]["scope"]["product_ref"] is None
    assert value["attribution"]["paid_proof_verified"] is False
    assert value["human_action"] == {"kind": "GATHER_COMPARABLE_EVIDENCE", "proposed_only": True, "execution_authority": "NONE"}
    assert "HUMAN_POLICY" in value["missingness"]
    assert "PAID_SETTLEMENT_PROOF" not in value["missingness"]
    assert value["confidence_meaning"] == "EVIDENCE_RATING_NOT_PROBABILITY"
    assert value["financial_commitment"] == "NONE" and not value["live_outcome_verified"]


def test_replay_immutable_detached_bytes_and_expected_id():
    original = request(policy=True)
    result = recommend_portfolio(original)
    original["scope"]["campaign_ref"] = "changed"
    assert result.to_bytes() == recommend_portfolio(request(policy=True)).to_bytes()
    copy = result.to_dict()
    copy["decision"] = "KEEP"
    assert result.to_dict()["decision"] == "INCONCLUSIVE"
    assert PortfolioRecommendation.from_bytes(result.to_bytes(), expected_id=result.recommendation_id) == result
    with pytest.raises(FrozenInstanceError):
        result._raw = b"other"
    with pytest.raises(PortfolioError, match="PIN_MISMATCH"):
        PortfolioRecommendation.from_bytes(result.to_bytes(), expected_id="a18-portfolio-" + "0" * 64)


def test_changed_policy_clock_candidate_or_scope_changes_content_identity():
    base = request(policy=True)
    seen = {recommend_portfolio(base).recommendation_id}
    for field, val in (("as_of", "2026-11-03T01:00:00Z"), ("candidate_id", "another"), ("currency", "VND")):
        changed = deepcopy(base)
        changed[field] = val
        seen.add(recommend_portfolio(changed).recommendation_id)
    changed = deepcopy(base)
    changed["human_policy"]["keep_at_or_above"] = "101"
    seen.add(recommend_portfolio(changed).recommendation_id)
    assert len(seen) == 5


def test_concurrent_repeat_is_pure_and_candidate_order_stable(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("M06 attempted a write")
    monkeypatch.setattr(Path, "write_bytes", deny)
    monkeypatch.setattr(Path, "write_text", deny)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: recommend_portfolio(request()).to_bytes(), range(16)))
    assert len(set(results)) == 1
    left = (request(candidate="b"), EvidenceBundle())
    right = (request(candidate="a"), EvidenceBundle())
    assert recommend_many((left, right)) == recommend_many((right, left))
    assert [r.to_dict()["request"]["candidate_id"] for r in recommend_many((left, right))] == ["a", "b"]
    with pytest.raises(PortfolioError, match="DUPLICATE_CANDIDATE"):
        recommend_many((left, left))


@pytest.mark.parametrize("key", ["budget", "publish", "buy_inventory", "account_login", "webhook", "email", "sms", "wake", "hostops", "provider_credits", "roi", "attestation"])
def test_no_executable_or_financial_extra_input_keys(key):
    value = request()
    value[key] = True
    with pytest.raises(PortfolioError):
        recommend_portfolio(value)


@pytest.mark.parametrize("key", ["extra", "permissions", "sku", "settlement", "revenue", "ad_spend"])
def test_exact_nested_scope_keys(key):
    value = request()
    value["scope"][key] = "forged"
    with pytest.raises(PortfolioError, match="INVALID_PORTFOLIO_REQUEST"):
        recommend_portfolio(value)


@pytest.mark.parametrize("text", ["https://example.test/private", "HTTP://example.test", "h\u200bttps://example.test", "ｈｔｔｐｓ://example.test", "file:///private", "www.example.test", "javascript:alert", "sk-" + "x" * 32, "../private", "C:\\private\\store"])
def test_urls_credentials_and_traversal_never_become_decision_inputs(text):
    value = request()
    value["metric"]["definition"] = text
    with pytest.raises(PortfolioError, match="DENIED") as exc:
        recommend_portfolio(value)
    assert text not in str(exc.value)


@pytest.mark.parametrize("key", ["password", "API_KEY", "access_token", "refresh-token", "credentials", "authorization", "token"])
def test_credential_fields_are_rejected_before_schema_or_echo(key):
    value = request()
    value[key] = "private-content-must-not-echo"
    with pytest.raises(PortfolioError, match="CREDENTIAL_FIELD_DENIED") as exc:
        recommend_portfolio(value)
    assert "private-content" not in str(exc.value)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e309}', b'[]', b'null', b'\xff', b'{'])
def test_malformed_duplicate_and_nonfinite_json_rejected(raw):
    with pytest.raises(PortfolioError):
        recommend_portfolio(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 10**500, object(), "\ud800"])
def test_python_values_cannot_bypass_json_limits(value):
    with pytest.raises(PortfolioError):
        recommend_portfolio({"bad": value})


def test_text_arrays_depth_cycles_and_total_bytes_are_bounded():
    values = [{"bad": "x" * 4097}, {"bad": [0] * 2049}, {"bad": ["x" * 4000] * 600}]
    deep = None
    for _ in range(26):
        deep = [deep]
    values.append({"bad": deep})
    cycle = {}
    cycle["cycle"] = cycle
    values.append(cycle)
    for value in values:
        with pytest.raises(PortfolioError):
            recommend_portfolio(value)


@pytest.mark.parametrize("field,value", [("keep_at_or_above", "NaN"), ("stop_at_or_below", "100"),
    ("max_evidence_age_seconds", -1), ("requirement", "TRUST_CALLER"), ("authored_by", "PROVIDER_ATTESTED")])
def test_policy_cannot_fabricate_evidence_authority(field, value):
    body = request(policy=True)
    body["human_policy"][field] = value
    with pytest.raises(PortfolioError):
        recommend_portfolio(body)


@pytest.mark.parametrize("field,value,reason", [
    ("valid_until", "2026-11-02T00:00:00Z", "POLICY_OUTSIDE_VALIDITY"),
    ("valid_from", "2026-11-04T00:00:00Z", "POLICY_OUTSIDE_VALIDITY"),
    ("currency", "EUR", "POLICY_CURRENCY_MISMATCH"),
    ("metric_definition_sha256", "0" * 64, "POLICY_METRIC_MISMATCH")])
def test_stale_or_mismatched_policy_is_safe_review_data(field, value, reason):
    body = request(policy=True)
    body["human_policy"][field] = value
    record = recommend_portfolio(body).to_dict()
    assert reason in record["reason_codes"] and record["decision"] == "INCONCLUSIVE"
    assert record["human_action"]["kind"] == "REVIEW_CONFLICTS"


def test_currency_no_fx_and_future_or_incompatible_windows():
    body = request(policy=True)
    body["baseline_window"] = ["2026-10-30T00:00:00Z", "2026-11-01T12:00:00Z"]
    body["as_of"] = "2026-11-01T12:00:00Z"
    record = recommend_portfolio(body).to_dict()
    assert {"COMPARISON_WINDOW_MISMATCH", "EVALUATION_WINDOW_FUTURE"} <= set(record["reason_codes"])
    assert record["comparisons"] == []
    body["measurement_window"].reverse()
    with pytest.raises(PortfolioError, match="INVALID_MEASUREMENT_WINDOW"):
        recommend_portfolio(body)


def test_plain_dict_or_arbitrary_callable_has_no_native_authority():
    for evidence in ({"roi": 5}, lambda: None):
        with pytest.raises(PortfolioError, match="TYPED_EVIDENCE_BUNDLE_REQUIRED"):
            recommend_portfolio(request(), evidence)
    with pytest.raises(PortfolioError, match="TYPED_NATIVE_SOURCE_REQUIRED"):
        recommend_portfolio(request(), EvidenceBundle(reports=({"measurement": {"value": 100}},)))
    with pytest.raises(PortfolioError, match="TYPED_SOURCE_TUPLE_REQUIRED"):
        recommend_portfolio(request(), EvidenceBundle(reports=[]))


def test_opaque_native_field_name_cannot_smuggle_urls_into_request():
    body = request()
    body["raw_utf8"] = '{"source":"https://example.test/private"}'
    with pytest.raises(PortfolioError, match="URL_OR_CREDENTIAL_DENIED"):
        recommend_portfolio(body)


def test_runtime_schema_mirror_exact_keysets_and_no_remote_refs():
    contract = ROOT / "contracts/PortfolioRecommendation.v1.schema.json"
    definition = json.loads(contract.read_bytes())
    assert definition == schema()
    Draft202012Validator.check_schema(definition)
    record = recommend_portfolio(request()).to_dict()
    Draft202012Validator(definition).validate(record)
    assert set(record) == set(definition["properties"]) == set(definition["required"])
    pending = [definition]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if "$ref" in current:
                assert current["$ref"].startswith("#/")
            if current.get("type") == "array":
                assert "maxItems" in current
            if current.get("type") == "object":
                assert current["additionalProperties"] is False
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    definition["properties"]["decision"] = {}
    assert schema() != definition


@pytest.mark.parametrize("field,value", [("decision", "KEEP"), ("decision", "CHANGE"), ("decision", "STOP"),
    ("live_outcome_verified", True), ("financial_commitment", "BUY"), ("confidence", "0.99")])
def test_contract_and_runtime_reject_forged_decisive_or_live_outcome(field, value):
    record = recommend_portfolio(request()).to_dict()
    record[field] = value
    forged = reseal(record)
    assert not Draft202012Validator(schema()).is_valid(forged)
    with pytest.raises(PortfolioError):
        validate_recommendation(forged)


def test_output_hash_extra_fields_totals_and_source_refs_are_validated():
    record = recommend_portfolio(request()).to_dict()
    record["request"]["candidate_id"] = "tampered"
    with pytest.raises(PortfolioError, match="REQUEST_HASH_MISMATCH"):
        validate_recommendation(record)
    record = recommend_portfolio(request()).to_dict()
    record["counterevidence"] = [{"source_id": "unknown", "code": "FORGED"}]
    with pytest.raises(PortfolioError, match="COUNTEREVIDENCE_REF_MISMATCH"):
        validate_recommendation(reseal(record))
    record["extra"] = "value"
    with pytest.raises(PortfolioError, match="INVALID_PORTFOLIO_RECOMMENDATION"):
        validate_recommendation(reseal(record))


def test_versioned_m07_reference_has_exact_hashes_and_no_commitment():
    record = recommend_portfolio(request())
    bridge = own_brand_reference(record)
    assert bridge == {"schema_version": "PortfolioRecommendationRef.v1", "recommendation_id": record.recommendation_id,
        "sha256": sha256(record.to_bytes()).hexdigest(), "source_sha": SOURCE_SHA,
        "candidate_id": "creative-only", "scope_sha256": digest(request()["scope"]),
        "as_of": request()["as_of"], "decision": "INCONCLUSIVE",
        "use": "A18_2_M07_HUMAN_REVIEW_DATA_ONLY", "financial_commitment": "NONE", "execution_authority": "NONE"}
    with pytest.raises(PortfolioError, match="NATIVE_RECOMMENDATION_REQUIRED"):
        own_brand_reference(record.to_dict())


def test_manifest_exact_sources_consumers_contract_tests_and_registered_owner_maps():
    from ai_commerce.engineering.impact_v21 import validate_component_onboarding_manifest
    from ai_commerce.engineering.minimum_test import approved_groups
    manifest_path = "work_items/mt01-components/a18-1.json"
    manifest = json.loads((ROOT / manifest_path).read_bytes())
    work = json.loads((ROOT / "work_items/A18-1-PORTFOLIO-001.json").read_bytes())
    sources = ["src/ai_commerce/opportunity/portfolio/" + name for name in ("__init__.py", "schemas.py", "sources.py", "recommend.py")]
    tests = ["tests/test_a18_portfolio.py", "tests/test_a18_portfolio_integration.py"]
    consumers = ["src/ai_commerce/opportunity/proposals/planning.py", "src/ai_commerce/learning/export.py",
        "src/ai_commerce/learning/reporting.py", "src/ai_commerce/operations/cost_ledger.py", "src/ai_commerce/settlement/review.py"]
    assert manifest == {"schema_version": "MT01ComponentOwner.v1", "component": "a18-1", "sources": sources,
        "tests": tests, "acs": ["A18.M06.offline-evidence-bound-portfolio-recommendation"], "consumers": consumers,
        "contracts": ["contracts/PortfolioRecommendation.v1.schema.json"]}
    groups = approved_groups()
    owner_names = {g.name for g in groups if set(consumers) & set(g.paths)}
    assert owner_names == {"a10", "a11", "a15", "a16"}
    assert all(any(p in g.paths for g in groups) for p in consumers)
    allowed = work["allowed_paths"]
    assert len(allowed) == len(set(allowed)) == 10
    checked = validate_component_onboarding_manifest(manifest, manifest_path=manifest_path,
        changed_paths=allowed, base_paths=consumers, existing_groups=groups, allowed_paths=allowed, root=ROOT)
    assert checked["status"] == "VALIDATED_DATA_ONLY_NOT_VERIFICATION_PASS"
    assert work["verification_profile"] == "mt01-component-onboarding" and work["source_sha"] == SOURCE_SHA
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_bytes())
    for owner in owner_names:
        group = next(g for g in groups if g.name == owner)
        assert ["python", "-m", "pytest", *group.tests, "-q", "-n", "12"] in profiles.values()


def test_new_modules_expose_no_direct_side_effect_or_dynamic_import_api():
    blocked = {"socket", "requests", "urllib", "http", "subprocess", "os", "shutil", "importlib"}
    for path in (ROOT / "src/ai_commerce/opportunity/portfolio").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in blocked for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in blocked
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "eval", "exec", "__import__"}


def test_existing_native_and_protected_files_remain_frozen():
    frozen = {
        "src/ai_commerce/opportunity/proposals/planning.py": "1e3234bde87bdb614b9a2335c722116d6de61246ef8091d080b483d84b9b12e7",
        "src/ai_commerce/opportunity/proposals/schemas.py": "04a5c82766b669c1fd443e5aa84123953d72bf384f0fc6d9b7cac3fefb1b1f43",
        "src/ai_commerce/learning/export.py": "41f7cf76279f3e05e1c072fc68e42b54bb6209f2070adeff624af7103ec811b7",
        "src/ai_commerce/learning/reporting.py": "55d347f799e548f97836468d3459da79111db211aa5b91fbaa414200af63e8ab",
        "src/ai_commerce/operations/cost_ledger.py": "2afb7d0c05b6e90d75020c8b04df5c218c3f7bc609b84f16bb3c721dc66dc969",
        "src/ai_commerce/operations/status_report.py": "8e8365dc440f14b0fa6d8b9bea1acd987cb698d637436e5cf6ba0a17a35eef97",
        "src/ai_commerce/settlement/review.py": "44851e8a755b07ff359a2047fe0175dfe261d89b49c75fbd8b5c6a76fa420c86",
        ".github/ef02/verify-profiles.json": "7135eca52af7bf296a2f2753319ef8e878086d5ff6727ab138673d15fded20d0",
        "src/ai_commerce/engineering/minimum_test.py": "20ecbcefa133d40e753d2d9f699801b5c5145ec85a2ad93d40babb84dd407a74",
    }
    assert all(sha256((ROOT / path).read_bytes()).hexdigest() == expected for path, expected in frozen.items())
