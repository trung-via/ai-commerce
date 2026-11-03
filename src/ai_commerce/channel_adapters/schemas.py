"""Bounded offline contracts. Digests identify bytes; they never attest rights.

Runtime schemas are self-contained. The two repository JSON schemas are mirrors,
so installation needs neither repository-relative paths nor remote resolution.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import re

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

VERSION = "a17-3/1.0"
MAX_BYTES = 256 * 1024
MAX_RECORDS = 128


class AdapterError(ValueError):
    """Only bounded codes are exposed, never rejected input or source paths."""


def require(condition, code):
    if not condition:
        raise AdapterError(code)


def canonical(value):
    pending, nodes, size = [(value, 0)], 0, 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            require(depth <= 20 and nodes <= 12000, "JSON_COMPLEXITY_LIMIT")
            if type(item) is dict:
                require(len(item) <= 64 and all(type(k) is str for k in item), "JSON_OBJECT_INVALID")
                pending.extend((v, depth + 1) for pair in item.items() for v in pair)
            elif type(item) is list:
                require(len(item) <= 256, "JSON_ARRAY_LIMIT")
                pending.extend((v, depth + 1) for v in item)
            elif type(item) is str:
                require(len(item) <= 4096, "JSON_TEXT_LIMIT")
                size += len(item.encode("utf-8"))
            elif type(item) is int:
                require(item.bit_length() <= 64, "JSON_INTEGER_LIMIT")
                size += 24
            elif type(item) is float:
                require(math.isfinite(item), "JSON_NONFINITE")
                size += 24
            else:
                require(item is None or type(item) is bool, "JSON_VALUE_INVALID")
                size += 5
            require(size <= MAX_BYTES, "JSON_SIZE_LIMIT")
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
        require(len(raw) <= MAX_BYTES, "JSON_SIZE_LIMIT")
        return raw
    except AdapterError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise AdapterError("JSON_INVALID") from exc


def parse(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "JSON_SIZE_OR_TYPE_INVALID")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise AdapterError("JSON_NONFINITE")

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
        require(type(result) is dict, "JSON_OBJECT_REQUIRED")
        canonical(result)
        return result
    except AdapterError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise AdapterError("JSON_INVALID") from exc


def detached(value):
    return json.loads(canonical(value))


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


_PRIVATE = re.compile(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\bBearer\s+\S+|"
                      r"-----BEGIN .*PRIVATE KEY|\bsk-[A-Za-z0-9_-]{12,}|\+\d[\d ()-]{8,}", re.I)
_PRIVATE_KEYS = {"password", "secret", "credentials", "api_key", "access_token", "refresh_token",
                 "authorization", "cookie", "email", "phone", "address", "full_name"}


def public_data(value):
    if type(value) is dict:
        require(not {k.lower() for k in value} & _PRIVATE_KEYS, "PRIVATE_DATA_FORBIDDEN")
        for child in value.values():
            public_data(child)
    elif type(value) is list:
        for child in value:
            public_data(child)
    elif type(value) is str:
        require(not _PRIVATE.search(value) and not any(ord(c) < 32 for c in value), "PRIVATE_DATA_FORBIDDEN")


def instant(value):
    try:
        require(type(value) is str and len(value) <= 40, "TIME_INVALID")
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.tzinfo is not None and result.utcoffset() is not None, "TIME_INVALID")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise AdapterError("TIME_INVALID") from exc


def aware(at):
    require(type(at) is datetime and at.tzinfo is not None and at.utcoffset() is not None, "AWARE_TIME_REQUIRED")
    return at.astimezone(timezone.utc)


def obj(properties):
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


def arr(items, maximum=128, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def opt(item):
    return {"oneOf": [item, {"type": "null"}]}


def const(value):
    return {"const": value}


TOKEN = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_.:-]{0,95}$", "minLength": 1, "maxLength": 96}
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$", "minLength": 64, "maxLength": 64}
DATE = {"type": "string", "format": "date-time", "maxLength": 40,
        "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"}
PLATFORM = {"enum": ["TIKTOK", "INSTAGRAM", "YOUTUBE"]}
ACCOUNT = obj({"namespace": TOKEN, "id": TOKEN})
WINDOW = arr(DATE, 2, 2)
TIMEZONE = {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z][A-Za-z0-9_+./-]*$"}
DECISION = {"enum": ["RECONCILE_REQUIRED", "NO_RETRY", "REQUIRES_SEPARATE_AUTHORIZATION"]}
EFFECT_FLAGS = {"provider_attested": const(False), "public_visibility": const("UNKNOWN"),
                "real_post_created": const(False), "retry_dispatched": const(False)}
TARGET = obj({"platform": PLATFORM, "surface": TOKEN, "account_ref": ACCOUNT, "app_ref": ACCOUNT,
              "market": {"type": "string", "pattern": "^[A-Z]{2}$", "maxLength": 2},
              "package_id": TOKEN, "package_sha256": SHA, "content_sha256": SHA,
              "media_type": const("video/mp4")})
REQUEST = obj({"schema_version": const("DirectPostRequest.v1"), "adapter_version": const(VERSION),
               "mode": const("OFFLINE"), "target": TARGET, "source_sha256": SHA,
               "idempotency_key": TOKEN, "requested_at": DATE, "expires_at": DATE})
CONSENT = obj({"schema_version": const("HumanDecisionProposal.v1"), "request_sha256": SHA,
               "target_sha256": SHA, "actor_alias": TOKEN, "decision": {"enum": ["OPT_IN", "DECLINE"]},
               "decided_at": DATE, "expires_at": DATE, "provenance": const("UNVERIFIED_HUMAN_PROPOSAL"),
               "authorization_verified": const(False), "live_authorized": const(False)})
CAPABILITY = obj({"schema_version": const("AdapterCapability.v1"), "adapter_version": const(VERSION),
                  "platform": PLATFORM, "target_sha256": SHA, "a07_decision_sha256": SHA,
                  "a07_decision": {"enum": ["ALLOW", "DENY", "UNKNOWN", "NEEDS_RECHECK"]},
                  "verification": const("OFFLINE_RECORDED_NOT_LIVE_VERIFIED"),
                  "live_eligible": const(False), "entitlement": const("ENTITLEMENT_UNPROVEN")})
INTENT = obj({"schema_version": const("DirectPostIntent.v1"), "request_sha256": SHA,
              "consent_sha256": SHA, "capability_sha256": SHA, "source_sha256": SHA,
              "reconciliation_sha256": opt(SHA), "dashboard_sha256": SHA,
              "native_decision": DECISION, "mode": const("OFFLINE"), "effect_authorized": const(False)})
RECEIPT_BODY = {"schema_version": const("DirectPostReceipt.v1"), "adapter_version": const(VERSION),
                "adapter_namespace": TOKEN, "target": TARGET, "request_sha256": SHA, "intent_sha256": SHA,
                "source_sha256": SHA, "native_decision": DECISION,
                "fixture_state": {"enum": ["SUBMITTED", "UNKNOWN", "REJECTED", "CONFIRMED"]},
                "provenance": const("SYNTHETIC_FIXTURE"), "publication_status": const("UNKNOWN"),
                "mode": const("OFFLINE"), **EFFECT_FLAGS}
RECEIPT = obj({**RECEIPT_BODY, "receipt_id": {"type": "string", "pattern": "^synthetic-(tiktok|instagram|youtube)-[a-f0-9]{64}$", "maxLength": 84},
               "record_sha256": SHA})
CALLBACK = obj({"schema_version": const("SyntheticAdapterCallback.v1"), "event_id": TOKEN,
                "receipt_sha256": SHA, "target": TARGET, "source_sha256": SHA,
                "outcome": {"enum": ["UNKNOWN", "CONFIRMED", "NO_EFFECT", "AMBIGUOUS"]},
                "provenance": const("SYNTHETIC_FIXTURE"), "provider_attested": const(False)})
DENIAL = obj({"schema_version": const("DirectPostDenial.v1"), "request_sha256": SHA,
              "reason": {"enum": ["ENTITLEMENT_UNPROVEN", "LIVE_NOT_AUTHORIZED"]},
              "provider_called": const(False), "live_authorized": const(False), **EFFECT_FLAGS})

# These definitions describe synthetic examples, never current provider rules.
PROFILES = {
    "TIKTOK": {"surface": "VIDEO", "account_namespace": "tiktok_open_id", "app_namespace": "tiktok_client_key",
               "a07_action": "API_DIRECT_POST", "metrics": [
                   ("tiktok.fixture.video_views", "synthetic_play_events_v1", "play_events"),
                   ("tiktok.fixture.likes", "synthetic_like_events_v1", "like_events")]},
    "INSTAGRAM": {"surface": "REEL", "account_namespace": "instagram_user_id", "app_namespace": "meta_app_id",
                  "a07_action": "API_DIRECT_POST", "metrics": [
                      ("instagram.fixture.accounts_reached", "synthetic_unique_accounts_v1", "unique_fixture_accounts"),
                      ("instagram.fixture.likes", "synthetic_like_accounts_v1", "fixture_like_accounts")]},
    "YOUTUBE": {"surface": "VIDEO", "account_namespace": "youtube_channel_id", "app_namespace": "google_cloud_project_id",
                "a07_action": "API_UPLOAD", "metrics": [
                    ("youtube.fixture.view_events", "synthetic_qualified_views_v1", "fixture_qualified_views"),
                    ("youtube.fixture.likes", "synthetic_video_likes_v1", "fixture_video_likes")]},
}
METRIC_DEFINITION = obj({"name": TOKEN, "definition": TOKEN, "denominator": TOKEN,
                        "unit": const("COUNT"), "visibility_meaning": const("UNKNOWN"),
                        "namespace": PLATFORM})
CATALOG_ENTRY = obj({"platform": PLATFORM, "adapter_namespace": TOKEN, "adapter_version": const(VERSION),
                     "surface": TOKEN, "account_namespace": TOKEN, "app_namespace": TOKEN,
                     "a07_action": {"enum": ["API_DIRECT_POST", "API_UPLOAD"]},
                     "provenance": const("SYNTHETIC_FIXTURE"), "live_available": const(False),
                     "receipt_semantics": const("SYNTHETIC_CONFIRMATION_IS_NOT_PUBLICATION"),
                     "metrics": arr(METRIC_DEFINITION, 2, 2)})
CATALOG = obj({"schema_version": const("ChannelAdapterCatalog.v1"), "version": const(VERSION),
               "entries": arr(CATALOG_ENTRY, 3, 3), "read_only": const(True)})
METRIC = obj({"schema_version": const("ChannelMetricFixture.v1"), "request_sha256": SHA,
              "definition": METRIC_DEFINITION, "definition_sha256": SHA,
              "measurement_window": WINDOW, "timezone": TIMEZONE,
              "value": opt({"type": "integer", "minimum": 0, "maximum": 10**12}),
              "status": {"enum": ["UNKNOWN", "RECORDED_SYNTHETIC"]},
              "attribution_tier": {"enum": ["NONE", "UNVERIFIED"]},
              "currency": opt({"type": "string", "pattern": "^[A-Z]{3}$", "maxLength": 3}),
              "provenance": const("SYNTHETIC_FIXTURE"), "provider_attested": const(False)})
VIEW = obj({"schema_version": const("ChannelAdapterView.v1"), "request_sha256": SHA, "target": TARGET,
            "source_sha256": SHA, "dashboard_sha256": SHA, "reconciliation_sha256": opt(SHA),
            "native_decision": DECISION, "native_reported_state": opt(TOKEN),
            "measurement_window": WINDOW, "timezone": TIMEZONE, "product_ref": opt(TOKEN),
            "fixture_receipt": opt(RECEIPT), "metrics": arr(METRIC, 2, 2),
            "opt_in": {"enum": ["NO_PROPOSAL", "PROPOSAL_ONLY"]},
            "execution_dispatched": const(False), "public_visibility": const("UNKNOWN"),
            "publication_status": const("UNKNOWN"), "read_only": const(True)})
COMPARISON = obj({"left": SHA, "right": SHA, "metric": TOKEN, "comparable": {"type": "boolean"},
                  "reason": {"enum": ["COMPATIBLE_SYNTHETIC_CONTEXT", "INCOMPATIBLE_CONTEXT", "MISSING_VALUE"]},
                  "delta": opt({"type": "integer", "minimum": -(10**12), "maximum": 10**12})})
AGGREGATE = obj({"schema_version": const("MultichannelOfflineSnapshot.v1"), "channels": arr(VIEW, 16),
                 "comparisons": arr(COMPARISON, 240), "read_only": const(True),
                 "execution_dispatched": const(False), "revenue": const(None), "settlement": const(None)})

_SCHEMAS = {"Request": REQUEST, "Consent": CONSENT, "Capability": CAPABILITY, "Intent": INTENT,
            "Receipt": RECEIPT, "Callback": CALLBACK, "Denial": DENIAL,
            "Catalog": CATALOG, "Metric": METRIC, "View": VIEW, "Aggregate": AGGREGATE}


def schema(name):
    require(name in _SCHEMAS, "CONTRACT_UNKNOWN")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **deepcopy(_SCHEMAS[name])}


def direct_post_contract():
    names = ("Request", "Consent", "Capability", "Intent", "Receipt", "Callback", "Denial")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:ai-commerce:DirectPostAdapter.v1",
            "title": "DirectPostAdapter.v1", "$defs": {name: deepcopy(_SCHEMAS[name]) for name in names},
            "oneOf": [{"$ref": "#/$defs/" + name} for name in names]}


def catalog_contract():
    names = ("Catalog", "Metric", "View", "Aggregate")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:ai-commerce:ChannelAdapterCatalog.v1",
            "title": "ChannelAdapterCatalog.v1", "$defs": {name: deepcopy(_SCHEMAS[name]) for name in names},
            "oneOf": [{"$ref": "#/$defs/" + name} for name in names]}


@lru_cache(maxsize=16)
def _validator(name):
    contract = schema(name)
    Draft202012Validator.check_schema(contract)
    return Draft202012Validator(contract, format_checker=FormatChecker(), registry=Registry())


def validate(name, value):
    canonical(value)
    public_data(value)
    require(_validator(name).is_valid(value), "CONTRACT_INVALID_" + name.upper())
    return detached(value)


def validate_target(value):
    platform = value["platform"]
    profile = PROFILES[platform]
    require(value["surface"] == profile["surface"], "CHANNEL_SURFACE_MISMATCH")
    for field, expected in (("account_ref", profile["account_namespace"]), ("app_ref", profile["app_namespace"])):
        require(value[field]["namespace"] == expected, "CHANNEL_NAMESPACE_MISMATCH")


@dataclass(frozen=True, slots=True, init=False)
class DirectPostRequest:
    _raw: bytes

    @classmethod
    def from_dict(cls, value):
        value = validate("Request", value)
        validate_target(value["target"])
        require(instant(value["requested_at"]) < instant(value["expires_at"]), "REQUEST_WINDOW_INVALID")
        require((instant(value["expires_at"]) - instant(value["requested_at"])).total_seconds() <= 86400,
                "REQUEST_LIFETIME_LIMIT")
        result = object.__new__(cls)
        object.__setattr__(result, "_raw", canonical(value))
        return result

    @classmethod
    def from_bytes(cls, raw):
        return cls.from_dict(parse(raw))

    def to_dict(self):
        return parse(self._raw)

    def to_bytes(self):
        return self._raw

    @property
    def sha256(self):
        return digest(self.to_dict())


@dataclass(frozen=True, slots=True, init=False)
class HumanConsent:
    """An explicitly supplied proposal; no authenticated Human authority exists here."""
    _raw: bytes

    @classmethod
    def from_dict(cls, value):
        value = validate("Consent", value)
        require(instant(value["decided_at"]) < instant(value["expires_at"]), "CONSENT_WINDOW_INVALID")
        result = object.__new__(cls)
        object.__setattr__(result, "_raw", canonical(value))
        return result

    def to_dict(self):
        return parse(self._raw)


@dataclass(frozen=True, slots=True, init=False)
class SyntheticReceipt:
    _raw: bytes

    @classmethod
    def from_bytes(cls, raw):
        value = validate("Receipt", parse(raw))
        validate_target(value["target"])
        body = {k: v for k, v in value.items() if k not in {"receipt_id", "record_sha256"}}
        sha = digest(body)
        platform = value["target"]["platform"].lower()
        require(value["record_sha256"] == sha and value["receipt_id"] == "synthetic-" + platform + "-" + sha,
                "RECEIPT_DIGEST_MISMATCH")
        require(value["adapter_namespace"] == "offline." + platform, "RECEIPT_NAMESPACE_MISMATCH")
        result = object.__new__(cls)
        object.__setattr__(result, "_raw", canonical(value))
        return result

    def to_dict(self):
        return parse(self._raw)

    def to_bytes(self):
        return self._raw


def checked_request(request):
    require(type(request) is DirectPostRequest, "TYPED_REQUEST_REQUIRED")
    return DirectPostRequest.from_bytes(request.to_bytes()).to_dict()
