"""Self-contained offline L04 contracts. No remote schema resolution."""
from copy import deepcopy


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


def array(value, maximum=256):
    return {"type": "array", "items": value, "maxItems": maximum}


# Business aliases only: no URLs, email addresses, network addresses or free text.
TOKEN = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
         "minLength": 1, "maxLength": 128}
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$", "minLength": 64, "maxLength": 64}
DATE = {"type": "string", "format": "date-time", "maxLength": 32,
        "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$"}
ACCOUNT = obj({"namespace": {"enum": ["internal_alias", "tiktok_open_id",
    "youtube_channel_id", "facebook_page_id", "instagram_user_id"]}, "id": TOKEN})
SCOPE = obj({
    "provider_ref": TOKEN,
    "platform": {"enum": ["TIKTOK", "YOUTUBE", "FACEBOOK", "INSTAGRAM"]},
    "account_ref": ACCOUNT, "app_ref": nullable(TOKEN),
    "market": {"type": "string", "pattern": "^[A-Z]{2}$", "maxLength": 2},
    "surface": TOKEN,
    "measurement_window": {"type": "array", "prefixItems": [DATE, DATE],
                           "items": False, "minItems": 2, "maxItems": 2},
    "timezone": {"const": "UTC"}, "cohort_ref": nullable(TOKEN),
})
ALGORITHMS = ["UNSPECIFIED", "FIRST_TOUCH", "LAST_TOUCH", "LINEAR", "PROVIDER_REPORTED"]
ORDER_STATES = ["UNKNOWN", "NOT_AVAILABLE", "PENDING", "REFUNDED", "REVOKED", "SETTLED"]
LINK_STATES = ["UNKNOWN", "NOT_AVAILABLE", "REPORTED_ACTIVE", "REVOKED"]
COUNT = obj({"status": {"enum": ["AVAILABLE", "UNKNOWN", "NOT_AVAILABLE"]},
             "value": nullable({"type": "integer", "minimum": 0, "maximum": 10**12})})
COUNT["allOf"] = [{"if": {"properties": {"status": {"const": "AVAILABLE"}}},
    "then": {"properties": {"value": {"type": "integer"}}},
    "else": {"properties": {"value": {"type": "null"}}}}]
UPSTREAM = obj({"component": {"enum": ["A06", "A07", "A08", "A09", "A12", "A13"]},
                "evidence_ref": TOKEN, "raw_sha256": SHA})


def _event(kind, fields):
    return obj({"kind": {"const": kind}, "event_ref": TOKEN, "observed_at": DATE, **fields})


CREATIVE = {"creative_ref": TOKEN, "creative_sha256": SHA}
REPORTED = {"reported_order_status": {"enum": ORDER_STATES}, "reported_count": COUNT}
EVENT = {"oneOf": [
    _event("LINK_CREATIVE", {"link_ref": TOKEN, **CREATIVE,
                            "reported_link_status": {"enum": LINK_STATES}}),
    _event("CLICK_LINK", {"click_ref": TOKEN, "link_ref": TOKEN}),
    _event("ORDER_CLICK", {"order_ref": TOKEN, "click_ref": TOKEN, **REPORTED}),
    _event("PROVIDER_REPORTED", {"provider_report_ref": TOKEN, "link_ref": TOKEN,
        **CREATIVE, "order_ref": nullable(TOKEN), **REPORTED,
        "reported_link_status": {"enum": LINK_STATES}}),
    _event("COHORT_REPORTED", {"cohort_ref": TOKEN, **REPORTED}),
    _event("DIRECTIONAL", {"link_ref": nullable(TOKEN),
        "creative_ref": nullable(TOKEN), "creative_sha256": nullable(SHA),
        "reported_count": COUNT,
        "reported_order_status": {"enum": ["UNKNOWN", "NOT_AVAILABLE"]}}),
]}
SOURCE = obj({
    "schema_version": {"const": "AffiliateAttributionSource.v1"},
    "source_kind": {"enum": ["SYNTHETIC_FIXTURE", "USER_AUTHORED_MANUAL",
                              "USER_PROVIDED_EXPORT_PROJECTION"]},
    "idempotency_key": TOKEN, "scope": SCOPE,
    "upstream_refs": array(UPSTREAM, 32), "events": array(EVENT),
})
RESULT = obj({
    "event_ref": TOKEN, "tier": {"enum": ["A", "B", "C", "D"]},
    "attribution_scope": {"enum": ["CREATIVE", "COHORT", "UNKNOWN"]},
    "reason_code": {"enum": ["EXACT_ID_CHAIN", "PROVIDER_REPORTED_ONLY",
        "COHORT_ONLY", "DIRECTIONAL_ONLY", "MISSING_CLICK", "MISSING_LINK_CREATIVE",
        "NON_CAUSAL_EVENT_ORDER"]},
    "link_ref": nullable(TOKEN), "creative_ref": nullable(TOKEN),
    "creative_sha256": nullable(SHA), "click_ref": nullable(TOKEN),
    "order_ref": nullable(TOKEN), "cohort_ref": nullable(TOKEN),
    "reported_order_status": {"enum": ORDER_STATES},
    "reported_link_status": {"enum": LINK_STATES}, "reported_count": COUNT,
    "support_event_refs": {**array(TOKEN, 3), "minItems": 1, "uniqueItems": True},
    "per_video_profit_verified": {"const": False},
})
SOURCE_RECORD = obj({"raw_utf8": {"type": "string", "minLength": 1, "maxLength": 262144},
    "raw_sha256": SHA, "canonical_sha256": SHA, "document": SOURCE})
EVENT_HASH = obj({"event_ref": TOKEN, "event_sha256": SHA})


def attribution_source_schema():
    return deepcopy({"$schema": "https://json-schema.org/draft/2020-12/schema",
                     "title": "AffiliateAttributionSource.v1", **SOURCE})


def affiliate_attribution_schema():
    result = obj({
        "schema_version": {"const": "AffiliateAttributionEvidence.v1"},
        "evaluator_version": {"const": "a14-l04/1.0"},
        "evidence_id": {"type": "string", "pattern": "^a14-attribution-[a-f0-9]{64}$",
                        "maxLength": 80},
        "record_sha256": SHA, "scope": SCOPE,
        "algorithm": {"enum": ALGORITHMS},
        "algorithm_execution": {"const": "DECLARATION_ONLY_NO_TOUCH_MODEL_EXECUTED"},
        "correction_of": nullable({"type": "string",
            "pattern": "^a14-attribution-[a-f0-9]{64}$", "maxLength": 80}),
        "source": SOURCE_RECORD, "event_hashes": array(EVENT_HASH),
        "results": array(RESULT), "synthetic": {"type": "boolean"},
        "source_status": {"const": "USER_SUPPLIED_NOT_LIVE_VERIFIED"},
        "verification_scope": {"const": "LOCAL_BYTES_HASH_AND_DETERMINISTIC_REPLAY_ONLY"},
        "upstream_reference_verification": {"const": "HASH_PINS_ONLY_NOT_OPENED"},
        "actual_orders_verified": {"const": False},
        "actual_affiliate_entitlement": {"const": "UNKNOWN"},
        "actual_revenue": {"const": "UNKNOWN"}, "actual_payout": {"const": "UNKNOWN"},
        "settlement_verification": {"const": "NOT_PERFORMED"},
        "publication_status": {"const": "NOT_ASSESSED"},
        "visibility_status": {"const": "NOT_ASSESSED"},
        "link_activation": {"const": "UNKNOWN"}, "advisory_only": {"const": True},
        "network_calls_performed": {"const": False},
    })
    return deepcopy({"$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:AffiliateAttributionEvidence:v1",
        "title": "AffiliateAttributionEvidence.v1",
        "description": "Offline supplied evidence strength, separate from algorithm, actual orders, entitlement and settlement. Hashes are not signatures. Replay is required in addition to schema validation.",
        **result})
