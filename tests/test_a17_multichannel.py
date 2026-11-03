"""A17.D07.D08.offline-capability-gated-adapters: isolated, read-only projections."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from ai_commerce.channel_adapters import (AdapterError, DirectPostRequest, NativeBinding,
    OfflineDirectPostAdapter, adapter_catalog, aggregate_channels, project_channel, propose_human_opt_in, synthetic_metric)
from ai_commerce.channel_adapters.multichannel import ChannelProjection, metric_definitions
from ai_commerce.channel_adapters.schemas import canonical, digest, validate

from test_a17_direct_post_adapter import (AT, callback_for, guard_effects, make_native_case,
    offline, simulate, tree_bytes)


def test_catalog_has_distinct_namespaces_metric_denominators_and_no_live_rights():
    catalog = adapter_catalog()
    entries = catalog["entries"]
    assert {e["platform"] for e in entries} == {"TIKTOK", "INSTAGRAM", "YOUTUBE"}
    assert len({e["adapter_namespace"] for e in entries}) == 3
    assert len({e["account_namespace"] for e in entries}) == 3
    assert len({e["metrics"][0]["denominator"] for e in entries}) == 3
    assert all(e["live_available"] is False and e["provenance"] == "SYNTHETIC_FIXTURE" for e in entries)
    entries[0]["metrics"][0]["denominator"] = "forged"
    assert adapter_catalog()["entries"][0]["metrics"][0]["denominator"] == "play_events"


def test_native_multichannel_is_read_only_null_unknown_and_opt_in_is_only_proposal(tmp_path, monkeypatch):
    cases = [make_native_case(tmp_path, monkeypatch, platform=p) for p in ("TIKTOK", "INSTAGRAM", "YOUTUBE")]
    before = [tree_bytes(c["root"]) for c in cases]
    guard_effects(monkeypatch)
    monkeypatch.setattr(OfflineDirectPostAdapter, "simulate", lambda *a, **k: pytest.fail("projection invoked mock execution"))
    views = [project_channel(c["request"], c["source"], consent=c["consent"], at=AT) for c in cases]
    result = aggregate_channels(views)
    assert len(result["channels"]) == 3 and len(result["comparisons"]) == 6
    assert result["revenue"] is None and result["settlement"] is None and result["execution_dispatched"] is False
    for view in result["channels"]:
        assert view["opt_in"] == "PROPOSAL_ONLY" and view["fixture_receipt"] is None
        assert view["publication_status"] == view["public_visibility"] == "UNKNOWN"
        assert view["native_decision"] == "RECONCILE_REQUIRED"
        assert all(m["value"] is None and m["status"] == "UNKNOWN" for m in view["metrics"])
    assert all(not c["comparable"] and c["delta"] is None for c in result["comparisons"])
    assert before == [tree_bytes(c["root"]) for c in cases]


def test_each_platform_same_raw_key_has_independent_fixture_custody(tmp_path, monkeypatch):
    cases = [make_native_case(tmp_path, monkeypatch, platform=p) for p in ("TIKTOK", "INSTAGRAM", "YOUTUBE")]
    guard_effects(monkeypatch)
    receipts = [simulate(c, outcome="CONFIRMED") for c in cases]
    assert len({r.to_dict()["receipt_id"] for r in receipts}) == 3
    views = [project_channel(c["request"], c["source"], receipt=r, issuer=c["adapter"]) for c, r in zip(cases, receipts)]
    result = aggregate_channels(views)
    assert all(v["fixture_receipt"]["fixture_state"] == "CONFIRMED" and v["publication_status"] == "UNKNOWN"
               for v in result["channels"])
    with pytest.raises(AdapterError, match="ADAPTER_PLATFORM_MISMATCH"):
        cases[0]["adapter"].simulate(cases[1]["request"], source=cases[1]["source"], consent=cases[1]["consent"], at=AT)
    with pytest.raises(AdapterError, match="RECEIPT_REQUEST_MISMATCH"):
        cases[1]["adapter"].require_issued(receipts[0], cases[1]["request"])
    with pytest.raises(AdapterError, match="CONSENT_BINDING_MISMATCH"):
        cases[1]["adapter"].simulate(cases[1]["request"], source=cases[1]["source"], consent=cases[0]["consent"], at=AT)
    with pytest.raises(AdapterError, match="RECEIPT_REQUEST_MISMATCH"):
        cases[1]["adapter"].inspect_callback(cases[1]["request"], receipts[0], callback_for(receipts[0]))


@pytest.mark.parametrize("dimension", ["account", "app", "window", "tier", "platform", "missing"])
def test_metric_comparisons_require_explicit_compatible_context(dimension, tmp_path, monkeypatch):
    left = make_native_case(tmp_path, monkeypatch, label="left")
    changes = {"label": "right"}
    if dimension == "account":
        changes["account_id"] = "other-account"
    if dimension == "app":
        changes["app_id"] = "other-app"
    if dimension == "window":
        changes["measurement_window"] = ["2026-11-02T00:00:00Z", "2026-11-04T00:00:00Z"]
    if dimension == "platform":
        changes["platform"] = "INSTAGRAM"
    right = make_native_case(tmp_path, monkeypatch, **changes)
    guard_effects(monkeypatch)
    metrics = []
    for case, number in ((left, 5), (right, None if dimension == "missing" else 0)):
        name = metric_definitions(case["request"].to_dict()["target"]["platform"])[0]["name"]
        metrics.append(synthetic_metric(case["request"], case["source"], name=name, value=number,
            attribution_tier="UNVERIFIED" if dimension == "tier" and case is right else "NONE"))
    result = aggregate_channels([project_channel(left["request"], left["source"], metrics=[metrics[0]]),
                                 project_channel(right["request"], right["source"], metrics=[metrics[1]])])
    assert all(c["comparable"] is False and c["delta"] is None for c in result["comparisons"])
    values = [v["metrics"][0]["value"] for v in result["channels"]]
    assert 5 in values and (None if dimension == "missing" else 0) in values


def test_compatible_source_window_denominator_counts_compare_without_summing_reach(tmp_path, monkeypatch):
    cases = [make_native_case(tmp_path, monkeypatch, label=label) for label in ("left", "right")]
    guard_effects(monkeypatch)
    views = []
    for case, count in zip(cases, (4, 0)):
        metric = synthetic_metric(case["request"], case["source"], name="tiktok.fixture.video_views", value=count)
        views.append(project_channel(case["request"], case["source"], metrics=[metric]))
    result = aggregate_channels(views)
    comparable = [c for c in result["comparisons"] if c["comparable"]]
    assert len(comparable) == 1 and abs(comparable[0]["delta"]) == 4
    assert comparable[0]["reason"] == "COMPATIBLE_SYNTHETIC_CONTEXT"
    assert not any("total" in key for key in result)


@pytest.mark.parametrize("field,value", [("definition_sha256", "a" * 64), ("request_sha256", "b" * 64),
    ("currency", "USD"), ("provenance", "PROVIDER_VERIFIED"), ("provider_attested", True),
    ("status", "RECORDED_SYNTHETIC"), ("email", "person@example.test")])
def test_forged_metrics_cannot_transfer_provenance_or_missing_as_zero(field, value, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    metric = synthetic_metric(case["request"], case["source"], name="tiktok.fixture.video_views")
    metric[field] = value
    with pytest.raises(AdapterError):
        project_channel(case["request"], case["source"], metrics=[metric])


@pytest.mark.parametrize("kind", ["definition", "denominator", "namespace", "window", "timezone"])
def test_cross_platform_definition_and_window_replay_rejected(kind, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    metric = synthetic_metric(case["request"], case["source"], name="tiktok.fixture.video_views", value=0)
    if kind == "definition":
        metric["definition"] = metric_definitions("YOUTUBE")[0]
    elif kind == "denominator":
        metric["definition"]["denominator"] = "unique_fixture_accounts"
    elif kind == "namespace":
        metric["definition"]["namespace"] = "INSTAGRAM"
    elif kind == "window":
        metric["measurement_window"] = ["2026-11-02T00:00:00Z", "2026-11-04T00:00:00Z"]
    else:
        metric["timezone"] = "Asia/Saigon"
    metric["definition_sha256"] = digest(metric["definition"])
    with pytest.raises(AdapterError):
        project_channel(case["request"], case["source"], metrics=[metric])


@pytest.mark.parametrize("value", [-1, True, "0", float("inf"), 10**12 + 1])
def test_metric_numeric_bounds_and_unknown_null_are_strict(value, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    with pytest.raises(AdapterError):
        synthetic_metric(case["request"], case["source"], name="tiktok.fixture.video_views", value=value)
    with pytest.raises(AdapterError, match="CHANNEL_METRIC_UNKNOWN"):
        synthetic_metric(case["request"], case["source"], name="revenue", value=0)


def test_duplicate_views_metrics_and_arbitrary_dict_claims_rejected(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    metric = synthetic_metric(case["request"], case["source"], name="tiktok.fixture.video_views", value=1)
    with pytest.raises(AdapterError, match="DUPLICATE_CHANNEL_METRIC"):
        project_channel(case["request"], case["source"], metrics=[metric, metric])
    view = project_channel(case["request"], case["source"])
    with pytest.raises(AdapterError, match="DUPLICATE_CHANNEL_VIEW"):
        aggregate_channels([view, view])
    with pytest.raises(AdapterError, match="TYPED_NATIVE_PROJECTION_REQUIRED"):
        aggregate_channels([view.to_dict()])
    with pytest.raises(FrozenInstanceError):
        view._raw = b"changed"
    raw = view.to_dict()
    raw["metrics"][0]["value"] = 999
    assert view.to_dict()["metrics"][0]["value"] is None


def test_aggregate_reopens_d05_and_never_trusts_archived_projection(tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    view = project_channel(case["request"], case["source"])
    case["source"].selection.package_dir.joinpath("caption.txt").write_bytes(b"tampered package metadata")
    guard_effects(monkeypatch)
    with pytest.raises(AdapterError):
        aggregate_channels([view])


@pytest.mark.parametrize("field,value", [("native_decision", "NO_RETRY"), ("native_reported_state", "PUBLISHED_CONFIRMED"),
    ("source_sha256", "a" * 64), ("dashboard_sha256", "b" * 64), ("reconciliation_sha256", None)])
def test_resealed_projection_status_claims_need_actual_native_replay(field, value, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    original = project_channel(case["request"], case["source"])
    raw = original.to_dict()
    raw[field] = value
    forged = object.__new__(ChannelProjection)
    for name, field_value in (("_raw", canonical(raw)), ("_request", original._request),
                              ("_source", original._source), ("_issuer", None)):
        object.__setattr__(forged, name, field_value)
    with pytest.raises(AdapterError, match="NATIVE_PROJECTION_REPLAY_MISMATCH"):
        aggregate_channels([forged])


@pytest.mark.parametrize("contract", ["Catalog", "Metric", "View", "Aggregate"])
def test_catalog_projection_and_aggregate_contracts_reject_extra_keys(contract, tmp_path, monkeypatch):
    case = make_native_case(tmp_path, monkeypatch)
    guard_effects(monkeypatch)
    view = project_channel(case["request"], case["source"])
    values = {"Catalog": adapter_catalog(), "Metric": view.to_dict()["metrics"][0],
              "View": view.to_dict(), "Aggregate": aggregate_channels([view])}
    raw = values[contract]
    raw["permissions"] = ["live-publish"]
    with pytest.raises(AdapterError, match="CONTRACT_INVALID"):
        validate(contract, raw)


def test_empty_read_only_aggregate_does_not_fabricate_zero():
    result = aggregate_channels([])
    assert result["channels"] == [] and result["comparisons"] == []
    assert result["revenue"] is None and result["settlement"] is None
