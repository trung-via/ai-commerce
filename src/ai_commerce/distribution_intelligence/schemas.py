"""A17.2 bounded, exact-key offline contracts. Hashes are never attestations.

Runtime schemas are self-contained so installed wheels need no repository paths
or remote schema resolver. The JSON files in contracts/ are identical mirrors.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from functools import lru_cache
import json
import math
import re

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from ai_commerce.analytics.files import local_path, read_local
from ai_commerce.analytics.model import validate_timezone
from ai_commerce.analytics.schemas import DEFINITION
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.operations.registry import ArtifactRegistry

RECONCILIATION = "DistributionReconciliation.v1"
DASHBOARD = "DecisionDashboardSnapshot.v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 256
MAX_RECORDS = 128


class IntelligenceError(ValueError):
    """Bounded codes only: no raw paths, receipts, credentials or identifiers."""


def require(ok, code):
    if not ok:
        raise IntelligenceError(code)


def bounded(value, *, limit=MAX_BYTES):
    pending, nodes, size = [(value, 0)], 0, 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            require(depth <= 32 and nodes <= 100000, "JSON_COMPLEXITY_LIMIT")
            if type(item) is dict:
                require(len(item) <= 256 and all(type(k) is str for k in item), "JSON_OBJECT_INVALID")
                pending.extend((v, depth + 1) for pair in item.items() for v in pair)
            elif type(item) is list:
                require(len(item) <= 4096, "JSON_ARRAY_LIMIT")
                pending.extend((v, depth + 1) for v in item)
            elif type(item) is str:
                require(len(item) <= 16384, "JSON_TEXT_LIMIT")
                size += len(item.encode("utf-8"))
            elif type(item) is float:
                require(math.isfinite(item), "JSON_NONFINITE")
                size += 32
            elif type(item) is int:
                require(item.bit_length() <= 512, "JSON_INTEGER_LIMIT")
                size += 160
            else:
                require(item is None or type(item) is bool, "JSON_VALUE_INVALID")
                size += 5
            require(size <= limit, "JSON_SIZE_LIMIT")
        raw = canonical_bytes(value)
        require(len(raw) <= limit, "JSON_SIZE_LIMIT")
        return raw
    except IntelligenceError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise IntelligenceError("JSON_VALUE_INVALID") from exc


def parse(raw, *, limit=MAX_BYTES):
    require(type(raw) is bytes and 0 < len(raw) <= limit, "JSON_SIZE_OR_TYPE_INVALID")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise IntelligenceError("JSON_NONFINITE")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
        require(type(value) is dict, "JSON_OBJECT_REQUIRED")
        bounded(value, limit=limit)
        return value
    except IntelligenceError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise IntelligenceError("JSON_INVALID") from exc


def detached(value):
    return json.loads(bounded(value))


def digest(value):
    return sha256_bytes(bounded(value))


_PRIVATE = re.compile(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
                      r"\bBearer\s+\S+|-----BEGIN .*PRIVATE KEY|\bsk-[A-Za-z0-9_-]{12,}", re.I)
_SECRET_KEYS = {"password", "api_key", "access_token", "refresh_token", "secret", "credentials"}


def public_aliases(value):
    """Only non-personal business aliases/prose are supported, never raw exports.

    This rejects obvious contacts and credentials, not all possible PII. Raw A08
    evidence bodies and public URLs are deliberately never copied into outputs.
    """
    if type(value) is dict:
        require(not set(k.lower() for k in value) & _SECRET_KEYS, "PRIVATE_DATA_FORBIDDEN")
        for child in value.values():
            public_aliases(child)
    elif type(value) is list:
        for child in value:
            public_aliases(child)
    elif type(value) is str:
        require(not _PRIVATE.search(value) and not any(ord(c) < 32 for c in value), "PRIVATE_DATA_FORBIDDEN")


def instant(value):
    try:
        require(type(value) is str and len(value) <= 64, "TIMESTAMP_INVALID")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "TIMESTAMP_INVALID")
        return parsed
    except (ValueError, TypeError, OverflowError) as exc:
        raise IntelligenceError("TIMESTAMP_INVALID") from exc


def window(value):
    require(type(value) is list and len(value) == 2, "WINDOW_INVALID")
    start, end = map(instant, value)
    require(start < end, "WINDOW_INVALID")
    return start, end


def within(value, period):
    start, end = window(period)
    require(start <= instant(value) < end, "SOURCE_OUTSIDE_WINDOW")


def safe_file(path, *, limit):
    try:
        target = local_path(path)
        info = target.stat()
        require(info.st_nlink == 1, "LINKED_SOURCE_FORBIDDEN")
        return read_local(target, limit=limit)
    except IntelligenceError:
        raise
    except (OSError, ValueError) as exc:
        raise IntelligenceError("SOURCE_FILE_UNSAFE_OR_UNREADABLE") from exc


def registry_bytes(registry, ref, *, limit=MAX_BYTES):
    require(type(registry) is ArtifactRegistry, "ARTIFACT_REGISTRY_REQUIRED")
    try:
        path = registry.safe_path(ref)
        require(path.is_file() and 0 < path.stat().st_size <= limit, "REGISTRY_SOURCE_SIZE_INVALID")
        raw = registry.read(ref)
        require(0 < len(raw) <= limit, "REGISTRY_SOURCE_SIZE_INVALID")
        return raw
    except IntelligenceError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise IntelligenceError("REGISTRY_SOURCE_UNPROVEN") from exc


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def opt(value):
    return {"oneOf": [value, {"type": "null"}]}


def arr(value, maximum=128, minimum=0):
    return {"type": "array", "items": value, "minItems": minimum, "maxItems": maximum}


def text(maximum=256):
    return {"type": "string", "minLength": 1, "maxLength": maximum, "pattern": r"\S"}


SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$", "maxLength": 64}
TOKEN = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$", "maxLength": 256}
KEY = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$", "maxLength": 96}
REF = {**text(512), "pattern": "^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$"}
DATE = {"type": "string", "format": "date-time", "maxLength": 64,
        "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$"}
WINDOW = {**arr(DATE, 2, 2)}
REV = {"type": "integer", "minimum": 1, "maximum": MAX_RECORDS}
ACCOUNT = obj({"namespace": TOKEN, "id": TOKEN})
ORIGIN = {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE", "UNVERIFIED"]}
SCOPE = obj({
    "platform": opt({"enum": ["TIKTOK", "YOUTUBE", "FACEBOOK", "INSTAGRAM"]}),
    "surface": opt(TOKEN), "account_ref": opt(ACCOUNT), "app_ref": opt(ACCOUNT),
    "provider_ref": opt(TOKEN), "market": opt({"type": "string", "pattern": "^[A-Z]{2}$"}),
    "channel": opt(TOKEN), "product_ref": opt(TOKEN), "listing_ref": opt(TOKEN),
    "variant_ref": opt(TOKEN), "link_ref": opt(TOKEN), "link_url_sha256": opt(SHA),
    "creative_ref": opt(TOKEN), "creative_revision": opt(REV), "creative_sha256": opt(SHA),
    "content_sha256": opt(SHA), "measurement_window": WINDOW, "timezone": text(128), "cohort": opt(TOKEN),
})

OBSERVATION = obj({
    "schema_version": {"const": "DistributionScopedObservation.v1"}, "observation_id": KEY,
    "publication_id": TOKEN, "intent_id": KEY, "attempt": REV, "request_digest": SHA,
    "original_record_sha256": SHA, "scope": SCOPE, "observed_at": DATE,
    "side_effects": {"enum": ["NONE", "CONFIRMED"]}, "provider_post_id": opt(TOKEN),
    "evidence_ref": REF, "evidence_sha256": SHA,
    "evidence_origin": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE"]},
})
OBS_PIN = obj({"ref": REF, "sha256": SHA})
EVENT = obj({"sequence": {"type": "integer", "minimum": 1, "maximum": 2048},
             "event_sha256": SHA, "event_type": {"enum": ["SUBMISSION_REPORTED", "OUTCOME_UNKNOWN",
                 "REJECTION_REPORTED", "PUBLICATION_RECEIPT", "VISIBILITY_EVIDENCE", "REMOVAL_REPORTED"]},
             "provider_post_id": opt(TOKEN), "public_url_sha256": opt(SHA), "evidence_sha256": opt(SHA),
             "recorded_at": DATE, "provenance": {"const": "USER_SUPPLIED"}})
PUBLICATION = obj({"publication_id": TOKEN, "idempotency_key_sha256": SHA, "package_sha256": SHA,
    "platform": TOKEN, "surface": TOKEN, "account_ref": ACCOUNT, "created_at": DATE,
    "state": {"enum": ["MANUAL_ACTION_REQUIRED", "SUBMITTED", "UNKNOWN", "REJECTED", "PUBLISHED_CONFIRMED", "REMOVED"]},
    "reported_visibility": {"enum": ["UNKNOWN", "PUBLIC"]}, "provider_post_id": opt(TOKEN),
    "events": arr(EVENT, MAX_EVENTS), "journal_sha256": SHA, "inventory_sha256": SHA})
ATTEMPT = obj({"version": {"const": 1}, "intent_id": KEY, "attempt": REV,
    "outcome": {"enum": ["PENDING", "UNKNOWN_EFFECT", "FAILED_NO_EFFECT", "SUCCEEDED", "RECONCILED_NO_EFFECT", "RECONCILED_CONFIRMED"]},
    "side_effects": {"enum": ["NONE", "UNKNOWN", "CONFIRMED"]}, "evidence_ref": opt(REF), "evidence_sha256": opt(SHA)})
NATIVE_RECON = obj({"version": {"const": 1}, "intent_id": KEY, "attempt": REV,
    "request_digest": SHA, "original_record_sha256": SHA, "side_effects": {"enum": ["NONE", "CONFIRMED"]},
    "evidence_ref": REF, "evidence_sha256": SHA, "evidence_origin": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE"]}})
SOURCES = obj({"schema_version": {"const": "DistributionSourceSnapshot.v1"}, "scope": SCOPE,
    "package": obj({"package_id": TOKEN, "package_sha256": SHA, "source_manifest_sha256": SHA, "video_sha256": SHA}),
    "publication": PUBLICATION,
    "retry": obj({"intent": obj({"version": {"const": 1}, "intent_id": KEY, "request_digest": SHA}),
        "attempts": arr(ATTEMPT, MAX_RECORDS, 1), "reconciliation": opt(NATIVE_RECON),
        "history_reconciliations": arr(NATIVE_RECON, MAX_RECORDS),
        "native_decision": {"enum": ["RECONCILE_REQUIRED", "NO_RETRY", "REQUIRES_HUMAN_AUTHORIZATION"]}, "sha256": SHA}),
    "observations": arr(obj({"pin": OBS_PIN, "document": OBSERVATION}), 32),
    "source_sha256": SHA,
    "verification_scope": {"const": "LOCAL_SOURCE_REPLAY_NOT_PROVIDER_ATTESTED"}})
OLD_STATE = obj({"publication": PUBLICATION["properties"]["state"],
    "reported_visibility": PUBLICATION["properties"]["reported_visibility"], "attempt_outcome": ATTEMPT["properties"]["outcome"]})
NEW_STATE = obj({"status": {"enum": ["RECONCILE_REQUIRED", "CONFLICTING_EVIDENCE", "OBSERVED_CONFIRMED_UNATTESTED", "OBSERVED_NO_EFFECT_UNATTESTED"]},
    "decision": {"enum": ["RECONCILE_REQUIRED", "NO_RETRY", "REQUIRES_SEPARATE_AUTHORIZATION"]},
    "public_visibility": {"const": "UNKNOWN"}, "replay_safe": {"const": False}, "provider_attested": {"const": False}})
REASON = {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,95}$", "maxLength": 96}
REASONS = {**arr(REASON, 64), "uniqueItems": True}
RECORD = obj({"schema_version": {"const": RECONCILIATION}, "record_id": TOKEN, "record_sha256": SHA,
    "sequence": REV, "revision": REV, "key": KEY, "parent_id": opt(TOKEN), "previous_sha256": opt(SHA),
    "sources": SOURCES, "old_state": OLD_STATE, "new_state": NEW_STATE, "reason_codes": REASONS,
    "provider_called": {"const": False}, "retry_dispatched": {"const": False}})

SOURCE_PIN = obj({"component": {"enum": ["A08_A15", "A09", "A11", "A14", "A15", "A16", "A17_1", "E06"]},
    "id": TOKEN, "sha256": SHA, "provenance": ORIGIN,
    "verification": {"const": "LOCAL_BYTES_ONLY_NOT_PROVIDER_VERIFIED"}})
METRIC = deepcopy(DEFINITION)
VALUE = {"type": ["number", "null"], "minimum": -1e100, "maximum": 1e100}
AUDIENCE_ROW = obj({"observation_id": TOKEN, "source_sha256": SHA, "native_context": obj({
    "platform": TOKEN, "surface": TOKEN, "account_ref": ACCOUNT, "measurement_window": WINDOW,
    "timezone": text(128), "cohort": opt(TOKEN)}),
    "provider_post_id": opt(TOKEN), "stage": {"enum": ["REACH", "ENGAGEMENT"]},
    "role_basis": {"const": "EXPLICIT_SELECTION_BY_DEFINITION_HASH"}, "metric": METRIC, "metric_sha256": SHA,
    "status": {"enum": ["AVAILABLE", "NOT_AVAILABLE"]}, "value": VALUE,
    "denominator_value": {"type": "null"}, "attribution_tier": {"enum": ["A", "B", "C", "D", "NOT_AVAILABLE"]},
    "provenance": ORIGIN, "limitations": REASONS})
AUDIENCE_ROW["allOf"] = [{"if": {"properties": {"status": {"const": "NOT_AVAILABLE"}}},
    "then": {"properties": {"value": {"type": "null"}}}, "else": {"properties": {"value": {"type": "number"}}}}]
COUNT = obj({"status": {"enum": ["AVAILABLE", "UNKNOWN", "NOT_AVAILABLE"]},
             "value": opt({"type": "integer", "minimum": 0, "maximum": 10**12})})
COUNT["allOf"] = [{"if": {"properties": {"status": {"const": "AVAILABLE"}}},
    "then": {"properties": {"value": {"type": "integer"}}}, "else": {"properties": {"value": {"type": "null"}}}}]
CLICK = obj({"event_ref": TOKEN, "click_ref": TOKEN, "link_ref": TOKEN, "observed_at": DATE,
    "creative_ref": opt(TOKEN), "creative_sha256": opt(SHA), "provenance": ORIGIN})
ORDER = obj({"event_ref": TOKEN, "order_ref": opt(TOKEN), "creative_ref": opt(TOKEN), "creative_sha256": opt(SHA),
    "reported_count": COUNT, "reported_order_status": TOKEN, "attribution_tier": {"enum": ["A", "B", "C", "D"]},
    "attribution_scope": {"enum": ["CREATIVE", "COHORT", "UNKNOWN"]}, "reason_code": REASON,
    "support_event_refs": arr(TOKEN, 3, 1), "provenance": ORIGIN})
DECIMAL = {"type": "string", "pattern": r"^(0|[1-9][0-9]{0,27})(\.[0-9]{1,6})?$", "maxLength": 35}
CURRENCY = {"type": "string", "pattern": "^[A-Z]{3}$", "maxLength": 3}
MONEY = obj({"status": {"enum": ["REPORTED", "UNKNOWN"]}, "amount": opt(DECIMAL), "currency": CURRENCY})
MONEY["allOf"] = [{"if": {"properties": {"status": {"const": "REPORTED"}}},
    "then": {"properties": {"amount": DECIMAL}}, "else": {"properties": {"amount": {"type": "null"}}}}]
SETTLEMENT_ROW = obj({"evidence_id": TOKEN, "order_ref": TOKEN, "reported_status": TOKEN,
    "currency": CURRENCY, "attribution_tier": {"enum": ["A", "B", "C", "D", "UNKNOWN"]},
    "settled_commission": MONEY, "credited_commission": MONEY, "refund_gross": MONEY,
    "reversed_commission": MONEY, "commission_basis": TOKEN, "provenance": ORIGIN})
COST_ROW = obj({"entry_id": KEY, "job_id": KEY, "attempt": REV, "intent_digest": SHA,
    "provider": text(128), "model": opt(text(128)), "status": {"enum": ["ESTIMATED", "CHARGED", "UNKNOWN", "PENDING", "NOT_APPLICABLE"]},
    "amount": opt(DECIMAL), "currency": opt(CURRENCY), "evidence_sha256": opt(SHA),
    "provenance": ORIGIN, "observed_at": DATE, "allocation_basis": {"const": "USER_SUPPLIED_JOB_SELECTION"}})
COST_ROW["allOf"] = [{"if": {"properties": {"status": {"enum": ["ESTIMATED", "CHARGED"]}}},
    "then": {"properties": {"amount": DECIMAL, "currency": CURRENCY}},
    "else": {"properties": {"amount": {"type": "null"}}}}]
TOTAL = obj({"currency": CURRENCY, "estimated": opt(DECIMAL), "supplied_charged": opt(DECIMAL),
    "synthetic_charged": opt(DECIMAL), "completeness": {"const": "SELECTED_ENTRIES_ONLY"}})
LEARNING_FACT = obj({"evidence_id": TOKEN, "revision": {"type": "integer", "minimum": 1, "maximum": 1000000},
    "role": {"enum": ["SUPPORTING", "COUNTEREVIDENCE", "INCONCLUSIVE"]}, "status": {"enum": ["AVAILABLE", "UNKNOWN"]},
    "value": VALUE, "denominator": VALUE, "provenance": ORIGIN, "observation_sha256": SHA})
LEARNING_FACT["allOf"] = [{"if": {"properties": {"status": {"const": "UNKNOWN"}}},
    "then": {"properties": {"value": {"type": "null"}}}}]
LEARNING_REV = obj({"revision": {"type": "integer", "minimum": 1, "maximum": 1000000}, "revision_id": TOKEN,
    "entry_sha256": SHA, "uncertainty": text(2048), "status": TOKEN,
    "confounders": arr(obj({"code": TOKEN, "description": text(2048), "status": TOKEN,
        "source_sha256": SHA}), 100), "counterevidence_ids": arr(TOKEN, 1000)})
FEEDBACK = obj({"proposal_id": TOKEN, "revision": {"type": "integer", "minimum": 1, "maximum": 2},
    "record_sha256": SHA, "source_brief_sha256": SHA, "next_brief_sha256": SHA,
    "adoption_decision": {"enum": ["PENDING", "ACCEPTED", "DECLINED"]},
    "after_status": {"const": "UNKNOWN"}, "causal_lift": {"type": "null"}})
SNAPSHOT = obj({"schema_version": {"const": DASHBOARD}, "snapshot_id": TOKEN, "record_sha256": SHA,
    "scope": SCOPE, "source_pins": arr(SOURCE_PIN, 256),
    "creative": obj({"created": {"enum": ["RECORDED", "UNKNOWN"]}, "batch_sha256": opt(SHA),
        "creative_sha256": opt(SHA), "mp4_generated": {"enum": ["RECORDED_LOCAL", "UNKNOWN", "NOT_AVAILABLE"]},
        "content_sha256": opt(SHA), "generation_performed": {"const": False}, "provenance": ORIGIN,
        "feedback": opt(FEEDBACK)}),
    "publication": obj({"reported_state": opt(PUBLICATION["properties"]["state"]),
        "reported_visibility": opt(PUBLICATION["properties"]["reported_visibility"]),
        "public_visibility": {"const": "UNKNOWN"}, "provider_post_id": opt(TOKEN), "provenance": ORIGIN,
        "reconciliation_status": opt(NEW_STATE["properties"]["status"]),
        "decision": {"enum": ["RECONCILE_REQUIRED", "NO_RETRY", "REQUIRES_SEPARATE_AUTHORIZATION"]}}),
    "audience": obj({"status": {"enum": ["RECORDED", "NOT_AVAILABLE"]}, "rows": arr(AUDIENCE_ROW, 1024)}),
    "learning": obj({"status": {"enum": ["ADVISORY_ONLY", "NOT_AVAILABLE"]}, "export_id": opt(TOKEN),
        "metric": opt(METRIC), "history": arr(LEARNING_REV, 128), "observations": arr(LEARNING_FACT, 4096),
        "causal_lift": {"type": "null"}}),
    "affiliate": obj({"status": {"enum": ["RECORDED", "NOT_AVAILABLE"]},
        "clicks": arr(CLICK, 256), "orders": arr(ORDER, 256), "actual_orders_verified": {"const": False}}),
    "settlement": obj({"status": {"enum": ["RECORDED", "NOT_AVAILABLE"]}, "rows": arr(SETTLEMENT_ROW, 128),
        "actual_settlement": {"const": "UNKNOWN"}, "settlement_verified": {"const": False}}),
    "costs": obj({"status": {"enum": ["RECORDED", "NOT_AVAILABLE"]}, "rows": arr(COST_ROW, 128),
        "by_currency": arr(TOTAL, 128), "provider_charge_verified": {"const": False}}),
    "limitations": REASONS, "realized_profit": {"type": "null"}, "causal_uplift": {"type": "null"},
    "provider_live": {"const": "UNKNOWN"}, "read_only": {"const": True}, "network_calls_performed": {"const": False}})

_SCHEMAS = {RECONCILIATION: RECORD, DASHBOARD: SNAPSHOT, "Scope": SCOPE, "Sources": SOURCES,
            "Observation": OBSERVATION, "ObservationPin": OBS_PIN}
_FORMATS = FormatChecker()


@_FORMATS.checks("date-time", raises=(ValueError, TypeError))
def _date(value):
    return isinstance(value, str) and instant(value).tzinfo is not None


def schema(name):
    require(name in _SCHEMAS, "SCHEMA_UNKNOWN")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:ai-commerce:" + name.replace(".v1", ":v1"), "title": name, **deepcopy(_SCHEMAS[name])}


@lru_cache(maxsize=6)
def _validator(name):
    value = schema(name)
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value, registry=Registry(), format_checker=_FORMATS)


def validate(name, value):
    value = detached(value)
    try:
        require(_validator(name).is_valid(value), "CONTRACT_INVALID:" + name)
    except (ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, IntelligenceError):
            raise
        raise IntelligenceError("CONTRACT_VALUE_INVALID") from exc

    def integers(item):
        if type(item) is dict:
            for key, child in item.items():
                if key in {"version", "sequence", "revision", "attempt", "creative_revision"} and child is not None:
                    require(type(child) is int, "EXACT_INTEGER_REQUIRED")
                integers(child)
        elif type(item) is list:
            for child in item:
                integers(child)
    integers(value)
    public_aliases(value)
    return value


def scope_value(value, *, publication=False):
    value = validate("Scope", value)
    window(value["measurement_window"])
    try:
        validate_timezone(value["timezone"])
    except ValueError as exc:
        raise IntelligenceError("TIMEZONE_INVALID") from exc
    targets = {"TIKTOK": ("tiktok_open_id", {"VIDEO", "PROFILE_VIDEO", "PRODUCT_ANCHOR"}),
               "YOUTUBE": ("youtube_channel_id", {"VIDEO", "SHORT"}),
               "FACEBOOK": ("facebook_page_id", {"PAGE_REEL"}), "INSTAGRAM": ("instagram_user_id", {"REEL"})}
    if value["platform"] is not None:
        namespace, surfaces = targets[value["platform"]]
        require(value["surface"] in surfaces, "SCOPE_PLATFORM_SURFACE_MISMATCH")
        if value["account_ref"] is not None:
            require(value["account_ref"]["namespace"] in {namespace, "internal_alias"}, "SCOPE_ACCOUNT_PLATFORM_MISMATCH")
    else:
        require(value["surface"] is None and value["account_ref"] is None, "SCOPE_TARGET_INCOMPLETE")
    require((value["creative_ref"] is None) == (value["creative_sha256"] is None)
            == (value["creative_revision"] is None), "CREATIVE_IDENTITY_INCOMPLETE")
    if publication:
        require(all(value[key] is not None for key in ("platform", "surface", "account_ref", "content_sha256")),
                "PUBLICATION_SCOPE_REQUIRED")
    return value
