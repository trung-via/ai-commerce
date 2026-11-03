"""Read-only per-platform proposals and explicitly comparable synthetic metrics."""
from __future__ import annotations

from dataclasses import dataclass

from .capability import NativeBinding
from .direct_post import OfflineDirectPostAdapter, check_consent
from .schemas import (PROFILES, VERSION, SyntheticReceipt, canonical, checked_request, digest,
                      instant, parse, require, validate, validate_target)


@dataclass(frozen=True, slots=True, init=False)
class ChannelProjection:
    """Native selectors accompany bytes so aggregation can reopen the source."""
    _raw: bytes
    _request: object
    _source: NativeBinding
    _issuer: object

    def to_dict(self):
        return parse(self._raw)

    def to_bytes(self):
        return self._raw


def metric_definitions(platform):
    require(platform in PROFILES, "CHANNEL_UNKNOWN")
    return [{"name": name, "definition": definition, "denominator": denominator, "unit": "COUNT",
             "visibility_meaning": "UNKNOWN", "namespace": platform}
            for name, definition, denominator in PROFILES[platform]["metrics"]]


def adapter_catalog():
    entries = []
    for platform, profile in PROFILES.items():
        entries.append({"platform": platform, "adapter_namespace": "offline." + platform.lower(),
            "adapter_version": VERSION, "surface": profile["surface"], "account_namespace": profile["account_namespace"],
            "app_namespace": profile["app_namespace"], "a07_action": profile["a07_action"],
            "provenance": "SYNTHETIC_FIXTURE", "live_available": False,
            "receipt_semantics": "SYNTHETIC_CONFIRMATION_IS_NOT_PUBLICATION", "metrics": metric_definitions(platform)})
    return validate("Catalog", {"schema_version": "ChannelAdapterCatalog.v1", "version": VERSION,
                                 "entries": entries, "read_only": True})


def synthetic_metric(request, source: NativeBinding, *, name, value=None, attribution_tier="NONE", currency=None):
    """A supplied synthetic number, never an audience observation or provider fact."""
    req = checked_request(request)
    require(type(source) is NativeBinding, "NATIVE_BINDING_REQUIRED")
    native = source.reopen(request)
    definitions = {d["name"]: d for d in metric_definitions(req["target"]["platform"])}
    require(type(name) is str and name in definitions, "CHANNEL_METRIC_UNKNOWN")
    # Financial metric transfer is deliberately unavailable in this vocabulary.
    require(currency is None, "COUNT_METRIC_HAS_NO_CURRENCY")
    definition = definitions[name]
    return validate("Metric", {"schema_version": "ChannelMetricFixture.v1", "request_sha256": request.sha256,
        "definition": definition, "definition_sha256": digest(definition),
        "measurement_window": native["scope"]["measurement_window"], "timezone": native["scope"]["timezone"],
        "value": value, "status": "UNKNOWN" if value is None else "RECORDED_SYNTHETIC",
        "attribution_tier": attribution_tier, "currency": None, "provenance": "SYNTHETIC_FIXTURE",
        "provider_attested": False})


def _checked_metric(metric, request, scope):
    value = validate("Metric", metric)
    definitions = metric_definitions(request.to_dict()["target"]["platform"])
    require(value["request_sha256"] == request.sha256 and value["definition"] in definitions
            and digest(value["definition"]) == value["definition_sha256"], "METRIC_SOURCE_OR_DEFINITION_MISMATCH")
    require(value["measurement_window"] == scope["measurement_window"] and value["timezone"] == scope["timezone"],
            "METRIC_WINDOW_MISMATCH")
    require((value["value"] is None) == (value["status"] == "UNKNOWN"), "MISSING_METRIC_IS_NOT_ZERO")
    require(value["currency"] is None, "COUNT_METRIC_HAS_NO_CURRENCY")
    return value


def project_channel(request, source: NativeBinding, *, metrics=(), receipt=None, issuer=None, consent=None, at=None):
    """Build an offline proposal. This function never invokes simulate or submit."""
    req = checked_request(request)
    require(type(source) is NativeBinding, "NATIVE_BINDING_REQUIRED")
    require(type(metrics) in (tuple, list) and len(metrics) <= 2, "METRIC_LIMIT")
    native = source.reopen(request)
    scope = native["scope"]
    supplied = {}
    for item in metrics:
        item = _checked_metric(item, request, scope)
        name = item["definition"]["name"]
        require(name not in supplied, "DUPLICATE_CHANNEL_METRIC")
        supplied[name] = item
    rows = []
    for definition in metric_definitions(req["target"]["platform"]):
        rows.append(supplied.get(definition["name"]) or synthetic_metric(request, source, name=definition["name"]))
    fixture = None
    if receipt is not None:
        require(type(issuer) is OfflineDirectPostAdapter and type(receipt) is SyntheticReceipt, "ISSUER_REQUIRED")
        fixture = issuer.require_issued(receipt, request)
    else:
        require(issuer is None, "UNUSED_RECEIPT_ISSUER")
    if consent is not None:
        check_consent(consent, request, at)
    view = {"schema_version": "ChannelAdapterView.v1", "request_sha256": request.sha256, "target": req["target"],
        "source_sha256": req["source_sha256"], "dashboard_sha256": native["dashboard"].to_dict()["record_sha256"],
        "reconciliation_sha256": native["reconciliation_sha256"], "native_decision": native["decision"],
        "native_reported_state": native["dashboard"].to_dict()["publication"]["reported_state"],
        "measurement_window": scope["measurement_window"], "timezone": scope["timezone"], "product_ref": scope["product_ref"],
        "fixture_receipt": fixture, "metrics": rows, "opt_in": "PROPOSAL_ONLY" if consent is not None else "NO_PROPOSAL",
        "execution_dispatched": False, "public_visibility": "UNKNOWN", "publication_status": "UNKNOWN", "read_only": True}
    result = object.__new__(ChannelProjection)
    object.__setattr__(result, "_raw", canonical(validate("View", view)))
    object.__setattr__(result, "_request", request)
    object.__setattr__(result, "_source", source)
    object.__setattr__(result, "_issuer", issuer)
    return result


def _validate_view(view):
    value = validate("View", view)
    validate_target(value["target"])
    definitions = metric_definitions(value["target"]["platform"])
    require([m["definition"] for m in value["metrics"]] == definitions, "CHANNEL_METRIC_VOCABULARY_MISMATCH")
    require(instant(value["measurement_window"][0]) < instant(value["measurement_window"][1]), "METRIC_WINDOW_INVALID")
    for row in value["metrics"]:
        require(row["request_sha256"] == value["request_sha256"] and row["measurement_window"] == value["measurement_window"]
                and row["timezone"] == value["timezone"] and row["definition_sha256"] == digest(row["definition"]),
                "METRIC_VIEW_BINDING_MISMATCH")
        require((row["value"] is None) == (row["status"] == "UNKNOWN"), "MISSING_METRIC_IS_NOT_ZERO")
        require(row["currency"] is None, "COUNT_METRIC_HAS_NO_CURRENCY")
    fixture = value["fixture_receipt"]
    if fixture is not None:
        parsed = SyntheticReceipt.from_bytes(canonical(fixture)).to_dict()
        require(parsed["target"] == value["target"] and parsed["request_sha256"] == value["request_sha256"]
                and parsed["source_sha256"] == value["source_sha256"] and parsed["native_decision"] == value["native_decision"],
                "RECEIPT_VIEW_BINDING_MISMATCH")
    return value


def _reopen_projection(projection):
    require(type(projection) is ChannelProjection, "TYPED_NATIVE_PROJECTION_REQUIRED")
    value = _validate_view(projection.to_dict())
    request, source = projection._request, projection._source
    req = checked_request(request)
    require(type(source) is NativeBinding, "NATIVE_BINDING_REQUIRED")
    native = source.reopen(request)
    dashboard = native["dashboard"].to_dict()
    require(value["target"] == req["target"] and value["request_sha256"] == request.sha256
            and value["source_sha256"] == req["source_sha256"]
            and value["dashboard_sha256"] == dashboard["record_sha256"]
            and value["reconciliation_sha256"] == native["reconciliation_sha256"]
            and value["native_decision"] == native["decision"]
            and value["native_reported_state"] == dashboard["publication"]["reported_state"]
            and all(value[k] == native["scope"][k] for k in ("measurement_window", "timezone", "product_ref")),
            "NATIVE_PROJECTION_REPLAY_MISMATCH")
    if value["fixture_receipt"] is not None:
        require(type(projection._issuer) is OfflineDirectPostAdapter, "ISSUER_REQUIRED")
        projection._issuer.require_issued(SyntheticReceipt.from_bytes(canonical(value["fixture_receipt"])), request)
    return value


def _context(view, metric):
    target = view["target"]
    return {"target": {k: target[k] for k in ("platform", "surface", "account_ref", "app_ref", "market")},
            "product_ref": view["product_ref"], **{k: metric[k] for k in ("definition", "measurement_window", "timezone",
                                                                       "attribution_tier", "currency", "provenance")}}


def aggregate_channels(views):
    """Compare supplied offline projections; no sum, rights transfer or execution."""
    require(type(views) in (tuple, list) and len(views) <= 16, "CHANNEL_VIEW_LIMIT")
    rows = [_reopen_projection(v) for v in views]
    rows.sort(key=lambda v: v["request_sha256"])
    require(len({v["request_sha256"] for v in rows}) == len(rows), "DUPLICATE_CHANNEL_VIEW")
    require(len({digest(v["target"]) for v in rows}) == len(rows), "REKEYED_CHANNEL_VIEW")
    comparisons = []
    for number, left in enumerate(rows):
        for right in rows[number + 1:]:
            for index, lmetric in enumerate(left["metrics"]):
                rmetric = right["metrics"][index]
                compatible = _context(left, lmetric) == _context(right, rmetric)
                missing = lmetric["value"] is None or rmetric["value"] is None
                comparable = compatible and not missing
                comparisons.append({"left": left["request_sha256"], "right": right["request_sha256"],
                    "metric": lmetric["definition"]["name"], "comparable": comparable,
                    "reason": "INCOMPATIBLE_CONTEXT" if not compatible else "MISSING_VALUE" if missing
                              else "COMPATIBLE_SYNTHETIC_CONTEXT",
                    "delta": rmetric["value"] - lmetric["value"] if comparable else None})
    return validate("Aggregate", {"schema_version": "MultichannelOfflineSnapshot.v1", "channels": rows,
        "comparisons": comparisons, "read_only": True, "execution_dispatched": False, "revenue": None, "settlement": None})
