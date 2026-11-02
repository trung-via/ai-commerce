"""Pure L04 evidence replay: no provider adapter, file access, clock or network."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from jsonschema import Draft202012Validator, FormatChecker

from .schemas import (ALGORITHMS, affiliate_attribution_schema,
                      attribution_source_schema)

MAX_SOURCE_BYTES = 262144
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
_FORMATS = FormatChecker()


@_FORMATS.checks("date-time", raises=(ValueError, TypeError))
def _valid_instant(value):
    # jsonschema's optional RFC3339 dependency is not installed on every runner.
    return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None


_SOURCE = Draft202012Validator(attribution_source_schema(), format_checker=_FORMATS)
_EVIDENCE = Draft202012Validator(affiliate_attribution_schema(), format_checker=_FORMATS)


class AttributionError(ValueError):
    """Bounded error codes; never echo supplied identifiers or raw data."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise AttributionError(code)


def canonical_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise AttributionError("INVALID_JSON_VALUE") from exc


def digest(value):
    return sha256(canonical_bytes(value)).hexdigest()


def parse_json(raw, limit=MAX_EVIDENCE_BYTES):
    require(type(raw) is bytes and 0 < len(raw) <= limit, "INPUT_SIZE_OR_TYPE_INVALID")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise AttributionError("NONFINITE_JSON_NUMBER")

    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
                           parse_constant=nonfinite)
        require(type(value) is dict, "JSON_OBJECT_REQUIRED")
        canonical_bytes(value)
        return value
    except AttributionError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise AttributionError("INVALID_JSON") from exc


def validate(validator, value, code):
    try:
        require(validator.is_valid(value), code)
    except (RecursionError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, AttributionError):
            raise
        raise AttributionError(code) from exc


def _time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise AttributionError("INVALID_TIMESTAMP") from exc


def scope_identity(scope):
    """Provider/account/link ID namespace; windows do not namespace real IDs."""
    return {key: value for key, value in scope.items()
            if key not in {"measurement_window", "timezone", "cohort_ref"}}


def _source(raw):
    document = parse_json(raw, MAX_SOURCE_BYTES)
    validate(_SOURCE, document, "INVALID_SOURCE_CONTRACT")
    scope = document["scope"]
    namespace = {"TIKTOK": "tiktok_open_id", "YOUTUBE": "youtube_channel_id",
                 "FACEBOOK": "facebook_page_id", "INSTAGRAM": "instagram_user_id"}
    surfaces = {"TIKTOK": {"VIDEO", "PROFILE_VIDEO", "PRODUCT_ANCHOR"},
                "YOUTUBE": {"VIDEO", "SHORT"}, "FACEBOOK": {"PAGE_REEL"},
                "INSTAGRAM": {"REEL"}}
    require(scope["account_ref"]["namespace"] in {"internal_alias", namespace[scope["platform"]]},
            "ACCOUNT_NAMESPACE_PLATFORM_MISMATCH")
    require(scope["surface"] in surfaces[scope["platform"]], "SURFACE_PLATFORM_MISMATCH")
    start, end = map(_time, scope["measurement_window"])
    require(start < end, "INVALID_MEASUREMENT_WINDOW")
    upstream = {}
    for ref in document["upstream_refs"]:
        key = (ref["component"], ref["evidence_ref"])
        require(key not in upstream or upstream[key] == ref["raw_sha256"],
                "UPSTREAM_REFERENCE_COLLISION")
        upstream[key] = ref["raw_sha256"]
    events = {}
    for event in document["events"]:
        require(start <= _time(event["observed_at"]) < end, "EVENT_OUTSIDE_WINDOW")
        # JSON Schema considers 1.0 an integer; the source contract uses exact JSON integers.
        if "reported_count" in event:
            count = event["reported_count"]
            require(count["value"] is None or type(count["value"]) is int,
                    "COUNT_INTEGER_REQUIRED")
        if event["kind"] == "DIRECTIONAL":
            require(event["reported_count"]["status"] != "AVAILABLE", "DIRECTIONAL_COUNT_UNSUPPORTED")
            require((event["creative_ref"] is None) == (event["creative_sha256"] is None),
                    "INCOMPLETE_CREATIVE_IDENTITY")
        if event["kind"] == "COHORT_REPORTED":
            require(scope["cohort_ref"] == event["cohort_ref"], "COHORT_SCOPE_MISMATCH")
        if event["kind"] == "ORDER_CLICK":
            require(event["reported_count"]["value"] in (None, 0, 1), "SINGLE_ORDER_COUNT_INVALID")
        key = event["event_ref"]
        require(key not in events or events[key] == event, "EVENT_ID_COLLISION")
        events[key] = event
    return document, sorted(events.values(), key=lambda e: e["event_ref"])


def identity_bindings(events):
    """Immutable identifiers must have a unique meaning, including across imports."""
    bindings = {}

    def bind(kind, key, value):
        address = (kind, key)
        require(address not in bindings or bindings[address] == value, kind + "_ID_COLLISION")
        bindings[address] = value

    for event in events:
        bind("EVENT", event["event_ref"], event)
        kind = event["kind"]
        if "creative_ref" in event and event["creative_ref"] is not None:
            bind("CREATIVE", event["creative_ref"], event["creative_sha256"])
        if kind in {"LINK_CREATIVE", "PROVIDER_REPORTED"}:
            bind("LINK", event["link_ref"], [event["creative_ref"], event["creative_sha256"],
                                           event["reported_link_status"]])
        if kind == "CLICK_LINK":
            bind("CLICK", event["click_ref"], [event["link_ref"], event["observed_at"]])
        if kind in {"ORDER_CLICK", "PROVIDER_REPORTED"} and event["order_ref"] is not None:
            # Cross-kind order claims are ambiguous without explicit correction lineage.
            bind("ORDER", event["order_ref"], {k: v for k, v in event.items() if k != "event_ref"})
        if kind == "PROVIDER_REPORTED":
            bind("PROVIDER_REPORT", event["provider_report_ref"],
                 {k: v for k, v in event.items() if k != "event_ref"})
    return bindings


def _derive(events):
    identity_bindings(events)  # Never pick a winner from ambiguous IDs.
    links, clicks, orders, reports = {}, {}, set(), set()
    for event in events:
        if event["kind"] == "LINK_CREATIVE":
            require(event["link_ref"] not in links, "LINK_ID_COLLISION")
            links[event["link_ref"]] = event
        elif event["kind"] == "CLICK_LINK":
            require(event["click_ref"] not in clicks, "CLICK_ID_COLLISION")
            clicks[event["click_ref"]] = event
        if event["kind"] in {"ORDER_CLICK", "PROVIDER_REPORTED"} and event["order_ref"] is not None:
            require(event["order_ref"] not in orders, "ORDER_ID_COLLISION")
            orders.add(event["order_ref"])
        if event["kind"] == "PROVIDER_REPORTED":
            require(event["provider_report_ref"] not in reports, "PROVIDER_REPORT_ID_COLLISION")
            reports.add(event["provider_report_ref"])
    results = []
    for event in events:
        kind = event["kind"]
        if kind in {"LINK_CREATIVE", "CLICK_LINK"}:
            continue
        result = {"event_ref": event["event_ref"], "tier": "D", "attribution_scope": "UNKNOWN",
            "reason_code": "DIRECTIONAL_ONLY", "link_ref": event.get("link_ref"),
            "creative_ref": event.get("creative_ref"), "creative_sha256": event.get("creative_sha256"),
            "click_ref": event.get("click_ref"), "order_ref": event.get("order_ref"),
            "cohort_ref": event.get("cohort_ref"), "reported_count": event["reported_count"],
            "reported_order_status": event["reported_order_status"],
            "reported_link_status": event.get("reported_link_status", "UNKNOWN"),
            "support_event_refs": [event["event_ref"]], "per_video_profit_verified": False}
        if kind == "ORDER_CLICK":
            click = clicks.get(event["click_ref"])
            link = links.get(click["link_ref"]) if click else None
            if click is None:
                result["reason_code"] = "MISSING_CLICK"
            else:
                result["link_ref"] = click["link_ref"]
                result["support_event_refs"].append(click["event_ref"])
                if link is None:
                    result["reason_code"] = "MISSING_LINK_CREATIVE"
                else:
                    result.update({k: link[k] for k in ("creative_ref", "creative_sha256",
                                                       "reported_link_status")})
                    result["support_event_refs"].append(link["event_ref"])
                    if not (_time(link["observed_at"]) <= _time(click["observed_at"]) <=
                            _time(event["observed_at"])):
                        result["reason_code"] = "NON_CAUSAL_EVENT_ORDER"
                    else:
                        result.update(tier="A", attribution_scope="CREATIVE", reason_code="EXACT_ID_CHAIN")
        elif kind == "PROVIDER_REPORTED":
            result.update(tier="B", attribution_scope="CREATIVE", reason_code="PROVIDER_REPORTED_ONLY")
        elif kind == "COHORT_REPORTED":
            result.update(tier="C", attribution_scope="COHORT", reason_code="COHORT_ONLY")
        result["support_event_refs"].sort()
        results.append(result)
    return results


def _build(raw, algorithm, parent):
    require(type(algorithm) is str and algorithm in ALGORITHMS, "INVALID_ALGORITHM")
    document, events = _source(raw)
    payload = {
        "schema_version": "AffiliateAttributionEvidence.v1", "evaluator_version": "a14-l04/1.0",
        "scope": document["scope"], "algorithm": algorithm,
        "algorithm_execution": "DECLARATION_ONLY_NO_TOUCH_MODEL_EXECUTED", "correction_of": parent,
        "source": {"raw_utf8": raw.decode("utf-8"), "raw_sha256": sha256(raw).hexdigest(),
                   "canonical_sha256": digest(document), "document": document},
        "event_hashes": [{"event_ref": event["event_ref"],
            "event_sha256": digest({"scope": document["scope"], "event": event})} for event in events],
        "results": _derive(events), "synthetic": document["source_kind"] == "SYNTHETIC_FIXTURE",
        "source_status": "USER_SUPPLIED_NOT_LIVE_VERIFIED",
        "verification_scope": "LOCAL_BYTES_HASH_AND_DETERMINISTIC_REPLAY_ONLY",
        "upstream_reference_verification": "HASH_PINS_ONLY_NOT_OPENED",
        "actual_orders_verified": False, "actual_affiliate_entitlement": "UNKNOWN",
        "actual_revenue": "UNKNOWN", "actual_payout": "UNKNOWN",
        "settlement_verification": "NOT_PERFORMED", "publication_status": "NOT_ASSESSED",
        "visibility_status": "NOT_ASSESSED", "link_activation": "UNKNOWN",
        "advisory_only": True, "network_calls_performed": False,
    }
    record_hash = digest(payload)
    result = {**payload, "record_sha256": record_hash, "evidence_id": "a14-attribution-" + record_hash}
    validate(_EVIDENCE, result, "INVALID_EVIDENCE_CONTRACT")
    return result


@dataclass(frozen=True, init=False)
class AffiliateAttributionEvidence:
    """Immutable canonical bytes, always constructed through full replay."""
    _raw: bytes

    @classmethod
    def from_bytes(cls, raw, *, expected_id=None):
        record = parse_json(raw)
        validate(_EVIDENCE, record, "INVALID_EVIDENCE_CONTRACT")
        replay = _build(record["source"]["raw_utf8"].encode("utf-8"), record["algorithm"],
                        record["correction_of"])
        # Python equality aliases 1/1.0 and 0/False; evidence hashes bind JSON bytes.
        require(canonical_bytes(record) == canonical_bytes(replay), "EVIDENCE_REPLAY_MISMATCH")
        require(expected_id is None or expected_id == record["evidence_id"], "EVIDENCE_PIN_MISMATCH")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_raw", canonical_bytes(record))
        return instance

    def to_bytes(self):
        return self._raw

    def to_dict(self):
        return parse_json(self._raw)

    @property
    def evidence_id(self):
        return self.to_dict()["evidence_id"]


def build_attribution_evidence(raw, *, algorithm="UNSPECIFIED", correction_of=None,
                               expected_source_sha256=None):
    """Replay explicit source bytes. Corrections require the complete parent object.

    A standalone correction records a parent pin; admission to AttributionLedger
    additionally verifies presence, latest lineage and competing corrections.
    """
    require(type(raw) is bytes and 0 < len(raw) <= MAX_SOURCE_BYTES, "INPUT_SIZE_OR_TYPE_INVALID")
    require(expected_source_sha256 is None or sha256(raw).hexdigest() == expected_source_sha256,
            "SOURCE_PIN_MISMATCH")
    parent_id = None
    if correction_of is not None:
        require(isinstance(correction_of, AffiliateAttributionEvidence), "CORRECTION_PARENT_REQUIRED")
        parent = AffiliateAttributionEvidence.from_bytes(correction_of.to_bytes()).to_dict()
        document, new_events = _source(raw)
        require(document["scope"] == parent["scope"], "CORRECTION_SCOPE_MISMATCH")
        require(algorithm == parent["algorithm"], "CORRECTION_ALGORITHM_MISMATCH")
        require(document["source_kind"] == parent["source"]["document"]["source_kind"],
                "CORRECTION_SOURCE_KIND_MISMATCH")
        require(document["idempotency_key"] != parent["source"]["document"]["idempotency_key"],
                "CORRECTION_REQUIRES_NEW_KEY")
        _, old_events = _source(parent["source"]["raw_utf8"].encode("utf-8"))
        require(new_events != old_events, "CORRECTION_REQUIRES_CHANGED_EVENTS")
        parent_id = parent["evidence_id"]
    return AffiliateAttributionEvidence.from_bytes(canonical_bytes(_build(raw, algorithm, parent_id)))
