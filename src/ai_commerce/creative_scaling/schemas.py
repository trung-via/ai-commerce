"""A17.1 bounded contracts and local custody helpers (no remote references).

Digests cover canonical, finite JSON, including nulls, versions and provenance.
They establish local integrity, never provider attestation or causal evidence.
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

from ai_commerce.analytics.model import validate_timezone
from ai_commerce.analytics.schemas import CONTEXT, DEFINITION, TARGET_RULES
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.manifest import strict_json

VARIANT_BATCH_SCHEMA = "CreativeVariantBatch.v1"
FEEDBACK_PROPOSAL_SCHEMA = "CreativeFeedbackProposal.v1"
MAX_BYTES = 8 * 1024 * 1024
MAX_VARIANTS = 16
MAX_ASSETS = 32
MAX_EVIDENCE = 4096


class CreativeScalingError(ValueError):
    """Bounded error codes deliberately exclude source text and private paths."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CreativeScalingError(code)


def bounded(value, *, limit: int = MAX_BYTES) -> bytes:
    pending, nodes, size = [(value, 0)], 0, 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            require(depth <= 32 and nodes <= 250000, "JSON_COMPLEXITY_LIMIT")
            if type(item) is dict:
                require(len(item) <= 256 and all(type(k) is str for k in item), "JSON_OBJECT_INVALID")
                pending.extend((v, depth + 1) for pair in item.items() for v in pair)
            elif type(item) is list:
                require(len(item) <= 10000, "JSON_ARRAY_LIMIT")
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
    except CreativeScalingError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise CreativeScalingError("JSON_VALUE_INVALID") from exc


def detached(value):
    return json.loads(bounded(value))


def digest(value) -> str:
    return sha256_bytes(bounded(value))


def seal(value: dict) -> dict:
    require(type(value) is dict and "record_sha256" not in value, "ALREADY_SEALED")
    return {**value, "record_sha256": digest(value)}


def check_seal(value: dict) -> None:
    require(value.get("record_sha256") == digest({k: v for k, v in value.items() if k != "record_sha256"}),
            "RECORD_DIGEST_MISMATCH")


_PRIVATE = re.compile(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
                      r"\bBearer\s+\S+|-----BEGIN .*PRIVATE KEY|\bsk-[A-Za-z0-9_-]{12,}|"
                      r"(?<!\w)\+?\d[\d ()-]{8,16}\d(?!\w)", re.I)
_SECRET_KEYS = {"password", "api_key", "access_token", "refresh_token", "secret", "credentials"}


def private_text(value) -> None:
    """Reject obvious contacts/credentials; require non-personal prose.

    This is deliberately not a claim of general purpose PII detection.
    """
    if type(value) is dict:
        require(not {k.lower() for k in value} & _SECRET_KEYS, "PRIVATE_DATA_NOT_ALLOWED")
        for item in value.values():
            private_text(item)
    elif type(value) is list:
        for item in value:
            private_text(item)
    elif type(value) is str:
        require(not _PRIVATE.search(value), "PRIVATE_DATA_NOT_ALLOWED")


def obj(properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def text(maximum=256):
    return {"type": "string", "minLength": 1, "maxLength": maximum, "pattern": r"\S"}


def optional(value):
    return {"oneOf": [value, {"type": "null"}]}


def array(item, maximum=32, minimum=0):
    return {"type": "array", "minItems": minimum, "maxItems": maximum, "items": item}


def ident(prefix):
    return {"type": "string", "pattern": "^" + re.escape(prefix) + "[a-f0-9]{64}$"}


SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
KEY = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"}
REV = {"type": "integer", "minimum": 1, "maximum": 1000000}
REF = {**text(512), "pattern": "^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$"}
PROVENANCE = {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE", "PROVIDER_ATTESTED"]}
# No offline upstream producer can attest a provider. Retain the vocabulary
# without accepting a label as proof or silently downcasting an attested source.
OFFLINE_PROVENANCE = {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE"]}
SCOPE = obj({**deepcopy(CONTEXT), "market": {**text(16), "pattern": "^[A-Z][A-Z0-9_-]{1,15}$"}})
SCOPE["properties"]["account_ref"] = obj({"namespace": {"const": "internal_alias"},
                                         "id": ident("a11-account-")})
SCOPE["allOf"] = deepcopy(TARGET_RULES)
PIN = obj({"id": text(), "revision": REV, "sha256": SHA, "ref": REF})
SOURCE = obj({"kind": {"enum": ["MASTER", "VARIANT"]}, "id": text(), "revision": REV, "sha256": SHA})
ASSET = obj({"role": text(128), "artifact_id": ident("artifact:"), "sha256": SHA,
             "manifest_ref": optional(REF), "manifest_sha256": optional(SHA),
             "provenance": OFFLINE_PROVENANCE,
             "rights_status": {"enum": ["UNKNOWN", "GRANTED", "RESTRICTED", "NOT_APPLICABLE"]}})
ASSET["allOf"] = [{"if": {"properties": {"manifest_ref": {"type": "null"}}},
                   "then": {"properties": {"manifest_sha256": {"type": "null"}}},
                   "else": {"properties": {"manifest_sha256": SHA}}}]
EDIT = obj({"mode": {"enum": ["FREEFORM", "CONTROLLED"]}, "intent": text(4096),
            "asset_order": optional(array(ident("artifact:"), MAX_ASSETS, 1)),
            "frames": optional(array({"type": "integer", "minimum": 1, "maximum": 1440}, MAX_ASSETS, 1)),
            "cost_entry_ids": array(KEY, 32)})
EDIT["properties"]["cost_entry_ids"]["uniqueItems"] = True
EDIT["allOf"] = [{"if": {"properties": {"mode": {"const": "FREEFORM"}}},
                  "then": {"properties": {"asset_order": {"type": "null"}, "frames": {"type": "null"}}},
                  "else": {"properties": {"asset_order": {"type": "array"}, "frames": {"type": "array"}}}}]
COST = obj({"version": {"const": 1}, "entry_id": text(96), "job_id": text(96), "attempt": REV,
            "intent_digest": SHA, "provider": text(128), "model": optional(text(128)),
            "status": {"enum": ["ESTIMATED", "CHARGED", "PENDING", "UNKNOWN", "NOT_APPLICABLE"]},
            "amount": optional({"type": "string", "pattern": r"^(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,6})?$"}),
            "currency": optional({"type": "string", "pattern": "^[A-Z]{3}$"}),
            "evidence_ref": optional(text(512)), "evidence_sha256": optional(SHA),
            "evidence_origin": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE", "UNVERIFIED"]},
            "observed_at": {"type": "string", "format": "date-time", "maxLength": 64}, "record_sha256": SHA})
COST["allOf"] = [
    {"if": {"properties": {"status": {"enum": ["ESTIMATED", "CHARGED"]}}},
     "then": {"properties": {"amount": {"type": "string"}, "currency": {"type": "string"}}},
     "else": {"properties": {"amount": {"type": "null"}}}},
    {"if": {"properties": {"evidence_ref": {"type": "null"}}},
     "then": {"properties": {"evidence_sha256": {"type": "null"}}},
     "else": {"properties": {"evidence_sha256": SHA}}},
    {"if": {"properties": {"status": {"const": "CHARGED"}}},
     "then": {"properties": {"evidence_ref": {"type": "string"},
                             "evidence_origin": OFFLINE_PROVENANCE}}},
]
MASTER = obj({"master_id": ident("a17-master-"), "revision": {"const": 1},
              "brief": PIN, "script": PIN, "composition": PIN, "assets": array(ASSET, MAX_ASSETS)})
VARIANT = obj({"variant_id": ident("a17-variant-"), "revision": {"const": 1},
               "master_id": ident("a17-master-"),
               "parent": obj({"kind": {"const": "MASTER"}, "id": ident("a17-master-"),
                              "revision": {"const": 1}, "sha256": SHA}),
               "edit": EDIT, "brief": PIN, "script": PIN, "composition": PIN,
               "assets": array(ASSET, MAX_ASSETS), "manifest_ref": REF,
               "render": obj({"state": {"enum": ["NOT_ATTEMPTED", "ASSET_REQUIRED"]},
                              "job_id": optional(text(96)), "manifest_ref": optional(REF)}),
               "costs": array(COST, 32),
               "cost_basis": {"const": "INPUT_ASSET_JOBS_ONLY_RENDER_COST_UNKNOWN"}})
VARIANT["allOf"] = [
    {"if": {"properties": {"edit": {"properties": {"mode": {"const": "FREEFORM"}}}}},
     "then": {"properties": {"assets": {"maxItems": 0}, "render": {"properties": {
         "state": {"const": "ASSET_REQUIRED"}, "job_id": {"type": "null"}, "manifest_ref": {"type": "null"}}}}},
     "else": {"properties": {"render": {"properties": {
         "state": {"const": "NOT_ATTEMPTED"}, "job_id": {"type": "string"}, "manifest_ref": {"type": "string"}}}}}},
]
BATCH = obj({"schema_version": {"const": VARIANT_BATCH_SCHEMA}, "batch_id": KEY,
             "revision": {"const": 1}, "request_sha256": SHA, "scope": optional(SCOPE),
             "provenance": OFFLINE_PROVENANCE, "product_ref": optional(text()), "master": MASTER,
             "variants": array(VARIANT, MAX_VARIANTS, 2), "generation_performed": {"const": False},
             "publication_status": {"const": "NOT_ATTEMPTED"}, "record_sha256": SHA})
ADVISORY_PIN = obj({"export_id": ident("a11-advisory-"), "archive_ref": REF, "archive_sha256": SHA,
                    "event_count": {"type": "integer", "minimum": 1, "maximum": 4096},
                    "head_event_hash": SHA, "learning_key": text(128), "revision": REV,
                    "revision_id": ident("a11-revision-"), "context_ref": REF, "context_sha256": SHA})
VALUE = {"type": ["number", "null"], "minimum": -1e100, "maximum": 1e100}
BEFORE_VALUE = obj({"evidence_id": ident("a11-evidence-"),
                    "status": {"enum": ["AVAILABLE", "UNKNOWN"]}, "value": VALUE,
                    "denominator": VALUE, "provenance": OFFLINE_PROVENANCE})
BEFORE_VALUE["allOf"] = [{"if": {"properties": {"status": {"const": "AVAILABLE"}}},
                          "then": {"properties": {"value": {"type": "number"}}},
                          "else": {"properties": {"value": {"type": "null"}}}}]
COMPARISON = obj({"before_window": deepcopy(CONTEXT["measurement_window"]),
                  "after_window": deepcopy(CONTEXT["measurement_window"]),
                  "timezone": text(128), "cohort": optional(text()), "metric": deepcopy(DEFINITION),
                  "before": array(BEFORE_VALUE, 200),
                  "after": obj({"status": {"const": "UNKNOWN"}, "value": {"type": "null"},
                                "denominator": {"type": "null"}}),
                  "causal_lift": {"type": "null"}, "trend": {"const": "NOT_ESTABLISHED"}})
ADOPTION = obj({"decision": {"enum": ["PENDING", "ACCEPTED", "DECLINED"]},
                "actor": {"enum": [None, "HUMAN"]}, "note": optional(text(2048))})
PROPOSAL = obj({"schema_version": {"const": FEEDBACK_PROPOSAL_SCHEMA}, "proposal_id": KEY,
                "revision": {"type": "integer", "minimum": 1, "maximum": 2}, "parent": optional(PIN),
                "batch_id": KEY, "batch_sha256": SHA, "master_id": ident("a17-master-"), "source": SOURCE,
                "source_brief": PIN, "next_brief": PIN, "scope": SCOPE, "advisory": ADVISORY_PIN,
                "evidence_ids": array(ident("a11-evidence-"), MAX_EVIDENCE),
                "negative_evidence_ids": array(ident("a11-evidence-"), MAX_EVIDENCE),
                "suggestion": text(4096), "hypothesis": text(2048), "uncertainty": text(2048), "comparison": COMPARISON,
                "missing_data": array(text(96), 16, 1), "provenance": {"const": "DETERMINISTIC_LOCAL_PROPOSAL"},
                "adoption": ADOPTION, "inference": {"const": "HYPOTHESIS_ONLY"},
                "generation_authorization": {"const": "REQUIRED_SEPARATELY"},
                "generation_performed": {"const": False}, "publication_status": {"const": "NOT_ATTEMPTED"},
                "record_sha256": SHA})
PROPOSAL["allOf"] = [
    {"if": {"properties": {"revision": {"const": 1}}},
     "then": {"properties": {"parent": {"type": "null"}, "adoption": {"properties": {
         "decision": {"const": "PENDING"}, "actor": {"type": "null"}, "note": {"type": "null"}}}}},
     "else": {"properties": {"parent": PIN, "adoption": {"properties": {
         "decision": {"enum": ["ACCEPTED", "DECLINED"]}, "actor": {"const": "HUMAN"}}}}}},
]
for _name in ("evidence_ids", "negative_evidence_ids", "missing_data"):
    PROPOSAL["properties"][_name]["uniqueItems"] = True
_SCHEMAS = {VARIANT_BATCH_SCHEMA: BATCH, FEEDBACK_PROPOSAL_SCHEMA: PROPOSAL,
            "Scope": SCOPE, "Edit": EDIT, "CostRecord": COST}


def schema(name: str) -> dict:
    require(name in _SCHEMAS, "UNKNOWN_SCHEMA")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:ai-commerce:" + name.replace(".v1", ":v1"),
            "title": name, **deepcopy(_SCHEMAS[name])}


@lru_cache(maxsize=5)
def _validator(name):
    definition = schema(name)
    Draft202012Validator.check_schema(definition)
    return Draft202012Validator(definition, registry=Registry(), format_checker=FormatChecker())


def validate(name: str, value) -> dict:
    value = detached(value)
    require(_validator(name).is_valid(value), "SCHEMA_INVALID:" + name)
    # JSON Schema considers 1.0 an integer. Identities require JSON integers.
    def integers(item):
        if type(item) is dict:
            for key, child in item.items():
                if key in {"revision", "attempt", "event_count", "version"}:
                    require(type(child) is int, "INTEGER_REQUIRED")
                if key == "frames" and child is not None:
                    require(all(type(v) is int for v in child), "INTEGER_REQUIRED")
                integers(child)
        elif type(item) is list:
            for child in item:
                integers(child)
    integers(value)
    if name in (VARIANT_BATCH_SCHEMA, FEEDBACK_PROPOSAL_SCHEMA):
        check_seal(value)
    return value


def window(value) -> tuple[datetime, datetime]:
    require(type(value) is list and len(value) == 2, "WINDOW_INVALID")
    try:
        require(all(type(s) is str and len(s) <= 64 for s in value), "WINDOW_INVALID")
        start, end = (datetime.fromisoformat(s.replace("Z", "+00:00")) for s in value)
        require(start.tzinfo is not None and end.tzinfo is not None and start < end, "WINDOW_INVALID")
        return start, end
    except (TypeError, ValueError, OverflowError) as exc:
        raise CreativeScalingError("WINDOW_INVALID") from exc


def scope_value(value) -> dict:
    value = validate("Scope", value)
    window(value["measurement_window"])
    try:
        validate_timezone(value["timezone"])
    except ValueError as exc:
        raise CreativeScalingError("TIMEZONE_INVALID") from exc
    private_text(value["cohort"])
    return value


def read_record(registry: ArtifactRegistry, ref: str, *, limit: int = MAX_BYTES):
    require(type(registry) is ArtifactRegistry, "ARTIFACT_REGISTRY_REQUIRED")
    path = registry.safe_path(ref)
    require(path.is_file() and path.stat().st_size <= limit, "RECORD_MISSING_OR_TOO_LARGE")
    raw = registry.read(ref)
    require(0 < len(raw) <= limit, "RECORD_MISSING_OR_TOO_LARGE")
    try:
        value = strict_json(raw)
        require(bounded(value, limit=limit) == raw, "NONCANONICAL_RECORD")
        return value
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CreativeScalingError):
            raise
        raise CreativeScalingError("RECORD_JSON_INVALID") from exc


def put_record(registry: ArtifactRegistry, ref: str, value: dict) -> None:
    require(type(registry) is ArtifactRegistry, "ARTIFACT_REGISTRY_REQUIRED")
    registry.put_immutable(ref, bounded(value))
