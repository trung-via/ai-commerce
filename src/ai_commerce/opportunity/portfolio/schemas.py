"""M06 portable contracts and bounded, private diagnostics; no schema I/O."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
import re
import unicodedata

from jsonschema import Draft202012Validator
from referencing import Registry

from ai_commerce.analytics.schemas import ACCOUNT, DEFINITION, PLATFORMS
from ai_commerce.contracts.schema import _format_checker
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

SOURCE_SHA = "b08d33f3195c48559d4964cf36c8b03612e41dd9"
EVALUATOR_VERSION = "a18-m06/1.0"
MAX_BYTES = 2 * 1024 * 1024
MAX_SOURCES = 32


class PortfolioError(ValueError):
    """Code only: never echo private source content, credentials or paths."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise PortfolioError(code)


def obj(fields: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": fields, "required": list(fields)}


def array(item: dict, maximum: int = MAX_SOURCES) -> dict:
    return {"type": "array", "maxItems": maximum, "items": item}


def optional(item: dict) -> dict:
    return {"oneOf": [item, {"type": "null"}]}


TOKEN = {"type": "string", "minLength": 1, "maxLength": 256,
         "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"}
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$", "maxLength": 64}
DATE = {"type": "string", "format": "date-time", "maxLength": 64}
WINDOW = {"type": "array", "minItems": 2, "maxItems": 2, "items": DATE}
DECIMAL = {"type": "string", "maxLength": 224,
           "pattern": r"^-?(?:0|[1-9][0-9]{0,105})(?:\.[0-9]{1,105})?$"}
CURRENCY = {"type": "string", "pattern": "^[A-Z]{3}$", "maxLength": 3}
TIER = {"enum": ["A", "B", "C", "D", "UNKNOWN", "NOT_AVAILABLE"]}
SCOPE = obj({
    "campaign_ref": TOKEN, "creative_ref": optional(TOKEN),
    "creative_sha256": optional(SHA), "product_ref": optional(TOKEN),
    "provider_ref": optional(TOKEN), "market": optional(TOKEN),
    "platform": {"enum": PLATFORMS}, "surface": TOKEN,
    "account_ref": optional(ACCOUNT), "cohort": optional(TOKEN),
    "timezone": {"type": "string", "minLength": 1, "maxLength": 128}, "attribution_tier": TIER,
})
POLICY = obj({
    "schema_version": {"const": "PortfolioHumanPolicy.v1"}, "policy_id": TOKEN,
    "authored_by": {"const": "HUMAN_DECLARED_NOT_AUTHENTICATED"},
    "valid_from": DATE, "valid_until": DATE,
    "max_evidence_age_seconds": {"type": "integer", "minimum": 0, "maximum": 31536000},
    "metric_definition_sha256": SHA,
    "keep_at_or_above": DECIMAL, "stop_at_or_below": DECIMAL,
    "between": {"const": "CHANGE"}, "currency": CURRENCY,
    "requirement": {"const": "COMPARABLE_AUTHORITATIVE_OBSERVATIONS"},
})
REQUEST = obj({
    "schema_version": {"const": "PortfolioEvaluation.v1"}, "candidate_id": TOKEN,
    "scope": SCOPE, "as_of": DATE, "measurement_window": WINDOW,
    "baseline_window": optional(WINDOW), "currency": CURRENCY,
    "metric": DEFINITION, "human_policy": optional(POLICY),
})
REF = obj({"ref": TOKEN, "sha256": SHA})
RECEIPT = obj({
    "kind": {"enum": ["PROPOSAL", "ADVISORY", "REPORT", "COMPARISON", "COST", "REVIEW"]},
    "source_id": TOKEN, "sha256": SHA, "native_version": TOKEN,
    "custody": {"enum": ["NATIVE_REPLAY", "ARCHIVE_VALIDATED", "UNVERIFIED"]},
    "epistemic_status": {"enum": ["HYPOTHESIS", "DESCRIPTIVE_OBSERVATION",
        "DESCRIPTIVE_COMPARISON", "COST_ASSERTION", "COMMERCIAL_ASSERTION", "ADVISORY"]},
    "attestation": {"const": "NOT_PROVIDER_ATTESTED"},
    "data_kind": {"enum": ["SYNTHETIC_FIXTURE", "USER_SUPPLIED", "UNVERIFIED", "MIXED"]},
    "scope_link": {"enum": ["MATCHED", "PARTIAL", "UNLINKED", "MISMATCH"]},
    "native_refs": array(REF, 128),
})
MEASUREMENT = obj({
    "source_id": TOKEN, "role": {"enum": ["CURRENT", "BASELINE", "UNMATCHED"]},
    "status": {"enum": ["AVAILABLE", "NOT_AVAILABLE", "INSUFFICIENT_DATA", "UNVERIFIED", "NOT_COMPARABLE"]},
    "value": optional(DECIMAL), "numerator": optional(DECIMAL),
    "denominator_value": optional(DECIMAL), "unit": {"type": "string", "maxLength": 64, "minLength": 1},
    "denominator": optional({"type": "string", "maxLength": 512, "minLength": 1}),
    "metric_definition_sha256": SHA, "measurement_window": WINDOW,
    "available_count": {"type": "integer", "minimum": 0, "maximum": 10000},
    "not_available_count": {"type": "integer", "minimum": 0, "maximum": 10000},
    "claim": {"const": "LOCAL_DESCRIPTIVE_ONLY"},
})
MEASUREMENT["allOf"] = [{
    "if": {"properties": {"status": {"const": "AVAILABLE"}}},
    "then": {"properties": {"value": DECIMAL}},
    "else": {"properties": {"value": {"type": "null"}}},
}]
COST = obj({
    "source_id": TOKEN, "entry_id": TOKEN, "job_id": TOKEN,
    "attempt": {"type": "integer", "minimum": 1}, "intent_digest": SHA,
    "provider": {"type": "string", "minLength": 1, "maxLength": 128},
    "status": {"enum": ["ESTIMATED", "CHARGED", "PENDING", "UNKNOWN", "NOT_APPLICABLE"]},
    "amount": optional(DECIMAL), "currency": optional(CURRENCY),
    "evidence_origin": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE", "UNVERIFIED"]},
    "evidence_sha256": optional(SHA), "evidence_ref_sha256": optional(SHA),
    "observed_at": DATE,
    "allocation": {"const": "UNVERIFIED_NO_NATIVE_CAMPAIGN_BINDING"},
})
COST_TOTAL = obj({
    "currency": CURRENCY, "estimated": optional(DECIMAL),
    "supplied_charged": optional(DECIMAL), "synthetic_charged": optional(DECIMAL),
    "pending_count": {"type": "integer", "minimum": 0, "maximum": MAX_SOURCES},
    "unknown_count": {"type": "integer", "minimum": 0, "maximum": MAX_SOURCES},
    "complete": {"const": False}, "provider_charge_verified": {"const": False},
})
COMPARISON = obj({
    "source_id": TOKEN, "left_report_id": TOKEN, "right_report_id": TOKEN,
    "status": {"enum": ["AVAILABLE", "NOT_COMPARABLE", "NOT_AVAILABLE", "INSUFFICIENT_DATA", "UNVERIFIED"]},
    "absolute_change": optional(DECIMAL), "relative_change": optional(DECIMAL),
    "unit": {"type": "string", "maxLength": 64, "minLength": 1},
    "claim": {"const": "DESCRIPTIVE_NO_CAUSAL_UPLIFT"},
})
LIMITATIONS = [
    "OFFLINE_ONLY: supplied and synthetic facts cannot prove live outcomes or market feasibility.",
    "Confidence is an evidence rating, never a probability of success.",
    "Local custody and hashes do not authenticate providers, receipts, rights or Human identity.",
    "No causal uplift, ranked ROI, realized profit or verified settlement is inferred.",
    "Cost job identity does not establish campaign allocation; currencies are never converted.",
    "KEEP, CHANGE and STOP require comparable authoritative observations; current native grades cannot supply them.",
    "Human policy is editable data; all actions require a separate Human decision.",
]


def schema() -> dict:
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:PortfolioRecommendation:v1",
        "title": "PortfolioRecommendation.v1",
        "description": "M06 offline Human decision support; no execution authority.",
        **obj({
            "schema_version": {"const": "PortfolioRecommendation.v1"},
            "recommendation_id": {"type": "string", "pattern": "^a18-portfolio-[a-f0-9]{64}$", "maxLength": 78},
            "evaluator_version": {"const": EVALUATOR_VERSION}, "source_sha": {"const": SOURCE_SHA},
            "request": REQUEST, "request_sha256": SHA, "policy_sha256": optional(SHA),
            "decision": {"enum": ["KEEP", "CHANGE", "STOP", "INCONCLUSIVE"]},
            "evidence_quality": {"enum": ["MISSING", "UNVERIFIED", "LOCAL_REPLAY_ONLY", "CONFLICTED"]},
            "confidence": {"const": "INSUFFICIENT_FOR_DECISIVE_RECOMMENDATION"},
            "confidence_meaning": {"const": "EVIDENCE_RATING_NOT_PROBABILITY"},
            "sources": array(RECEIPT), "measurements": array(MEASUREMENT),
            "comparisons": array(COMPARISON), "costs": array(COST),
            "cost_totals": array(COST_TOTAL),
            "attribution": obj({"declared_tier": TIER,
                "reviews": array(obj({"source_id": TOKEN,
                    "scope_link": {"enum": ["MATCHED", "PARTIAL", "UNLINKED", "MISMATCH"]},
                    "tiers": array(TIER, 5)})),
                "settlement_status": {"const": "UNVERIFIED"}, "paid_proof_verified": {"const": False}}),
            "reason_codes": array(TOKEN, 96), "missingness": array(TOKEN, 64),
            "counterevidence": array(obj({"source_id": TOKEN, "code": TOKEN}), 128),
            "assumptions": {"const": list(LIMITATIONS)},
            "human_action": obj({"kind": {"enum": ["GATHER_COMPARABLE_EVIDENCE", "REVIEW_CONFLICTS"]},
                "proposed_only": {"const": True}, "execution_authority": {"const": "NONE"}}),
            "downstream_use": {"const": "HUMAN_OR_A18_2_VERSIONED_REFERENCE_ONLY"},
            "financial_commitment": {"const": "NONE"}, "live_outcome_verified": {"const": False},
        }),
        # This version has no provider-attested native input. Prevent a resealed
        # record from asserting a decisive classification even in JSON Schema.
        "allOf": [{"properties": {"decision": {"const": "INCONCLUSIVE"}}}],
    })


def bounded(value, *, native: bool = False) -> bytes:
    pending, nodes, size = [(value, 0, False)], 0, 0
    while pending:
        current, depth, opaque = pending.pop()
        nodes += 1
        require(depth <= 24 and nodes <= 50000, "JSON_COMPLEXITY_LIMIT")
        if type(current) is dict:
            require(len(current) <= 128 and all(type(k) is str for k in current), "INVALID_JSON_OBJECT")
            for key, item in current.items():
                normalized = unicodedata.normalize("NFKC", key).casefold()
                require(not re.search(r"(?:password|secret|credential|api[_-]?(?:key|token)|(?:access|refresh)[_-]?token|authorization|webhook)|^(?:token|auth|bearer|private_key)$", normalized),
                        "CREDENTIAL_FIELD_DENIED")
                # Native envelopes contain retained renderer/tool metadata and
                # provenance URLs. Their owning replay validator checks these
                # opaque bytes. They are never decision fields, dereferenced or
                # exported. This exception cannot bypass an outer exact schema.
                pending.extend(((key, depth + 1, False),
                                (item, depth + 1, native and key == "raw_utf8")))
        elif type(current) is list:
            require(len(current) <= 2048, "JSON_ARRAY_LIMIT")
            pending.extend((item, depth + 1, False) for item in current)
        elif type(current) is str:
            require(len(current) <= (262144 if native else 4096), "JSON_TEXT_LIMIT")
            scanned = unicodedata.normalize("NFKC", current)
            scanned = "".join(c for c in scanned if unicodedata.category(c) != "Cf")
            require(opaque or not re.search(r"(?i)(?:https?|ftp|file|javascript|data):|www\.|sk-[A-Za-z0-9]{16,}", scanned),
                    "URL_OR_CREDENTIAL_DENIED")
            require(not re.search(r"(?:^|[\\/])\.\.(?:[\\/]|$)|^[A-Za-z]:[\\/]|^\\\\", scanned),
                    "FILESYSTEM_REFERENCE_DENIED")
            require(not any(ord(c) < 32 and c not in "\n\t\r" for c in current), "CONTROL_CHARACTER_DENIED")
            try:
                size += len(current.encode("utf-8"))
            except UnicodeError:
                raise PortfolioError("INVALID_JSON_VALUE") from None
        elif type(current) is float:
            require(math.isfinite(current), "JSON_NONFINITE")
            size += 32
        elif type(current) is int:
            require(current.bit_length() <= 384, "JSON_NUMBER_LIMIT")
            size += 128
        elif current is None or type(current) is bool:
            size += 5
        else:
            raise PortfolioError("INVALID_JSON_VALUE")
        require(size <= MAX_BYTES, "JSON_BYTES_LIMIT")
    try:
        raw = canonical_bytes(value)
        require(len(raw) <= MAX_BYTES, "JSON_BYTES_LIMIT")
        return raw
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise PortfolioError("INVALID_JSON_VALUE") from None


def parse(raw: bytes, *, native: bool = False):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "JSON_BYTES_LIMIT")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise PortfolioError("JSON_NONFINITE")

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
        bounded(result, native=native)
        return result
    except PortfolioError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise PortfolioError("INVALID_JSON") from None


def digest(value) -> str:
    return sha256_bytes(bounded(value, native=True))


def instant(value: str) -> datetime:
    try:
        require(type(value) is str and len(value) <= 64, "INVALID_CLOCK")
        result = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        require(result.utcoffset() is not None, "INVALID_CLOCK")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise PortfolioError("INVALID_CLOCK") from None


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    return Draft202012Validator(schema(), registry=Registry(), format_checker=_format_checker())
