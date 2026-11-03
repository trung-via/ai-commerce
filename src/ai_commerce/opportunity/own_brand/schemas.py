"""M07 portable contracts and bounded private JSON; no file or remote schema I/O."""
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

from ai_commerce.contracts.schema import _format_checker
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

SOURCE_SHA = "3ef41b54caa67f1f8bd90a7122868285f42a08a3"
EVALUATOR_VERSION = "a18-m07/1.0"
MAX_BYTES = 262144
MAX_NATIVE_BYTES = 2 * 1024 * 1024
GRADES = ["UNKNOWN", "ESTIMATED_USER_SUPPLIED", "USER_SUPPLIED"]
COST_BASES = {
    "cogs": ["PER_UNIT"], "sale_price": ["PER_UNIT"],
    "inbound_freight": ["PER_UNIT", "FIXED"],
    "outbound_shipping": ["PER_UNIT", "PER_ORDER"],
    "fulfillment": ["PER_UNIT", "PER_ORDER"],
    "storage": ["PER_UNIT", "FIXED"],
    "returns_warranty": ["PER_UNIT", "PER_ORDER"],
    "setup": ["FIXED"], "certification": ["FIXED"],
    "marketing": ["PER_UNIT", "PER_ORDER", "FIXED"],
    "fees": ["PER_UNIT", "PER_ORDER", "FIXED"],
    "buffer": ["FIXED"], "available_capital": ["FIXED"],
}
INVENTORY_COSTS = ("cogs", "inbound_freight", "storage")
SCENARIOS = ("bear", "base", "bull")


class OwnBrandError(ValueError):
    """Code-only diagnostics never echo private input or native exceptions."""


def require(ok, code):
    if not ok:
        raise OwnBrandError(code)


def obj(fields, *, required=None):
    return {"type": "object", "additionalProperties": False,
            "properties": fields, "required": list(fields) if required is None else required}


def array(item, maximum=32):
    return {"type": "array", "maxItems": maximum, "items": item}


def optional(item):
    return {"oneOf": [item, {"type": "null"}]}


TOKEN = {"type": "string", "minLength": 1, "maxLength": 96,
         "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]*$"}
NATIVE_TOKEN = {"type": "string", "minLength": 1, "maxLength": 256,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]*$"}
CODE = {"type": "string", "maxLength": 128, "pattern": "^[A-Z][A-Z0-9_]*$"}
SHA = {"type": "string", "maxLength": 64, "pattern": "^[a-f0-9]{64}$"}
DATE = {"type": "string", "format": "date-time", "maxLength": 64}
CURRENCY = {"type": "string", "maxLength": 3, "pattern": "^[A-Z]{3}$"}
MONEY = {"type": "string", "maxLength": 25,
         "pattern": r"^(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,6})?$"}
NUMBER = {"type": "string", "maxLength": 55,
          "pattern": r"^-?(?:0|[1-9][0-9]{0,47})\.[0-9]{6}$"}
MULTIPLIER = {"type": "string", "maxLength": 11,
              "pattern": r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,6})?$"}
COUNT = {"type": "integer", "minimum": 0, "maximum": 1000000000}
WINDOW = {**array(DATE, 2), "minItems": 2}
ACCOUNT = obj({"namespace": TOKEN, "id": TOKEN})
TEXT = {"type": "string", "minLength": 1, "maxLength": 1024}


def graded(value, name="value"):
    result = obj({"status": {"enum": GRADES}, name: optional(value)})
    # Branches refine properties without declaring a second open object type.
    result["allOf"] = [{
        "if": {"properties": {"status": {"const": "UNKNOWN"}}},
        "then": {"properties": {name: {"type": "null"}}},
        "else": {"properties": {name: value}},
    }]
    return result


DECLARATION = graded(TEXT, "text")
TEXT_RECEIPT = graded(SHA, "sha256")
MOQ_INPUT = graded({"type": "integer", "minimum": 1, "maximum": 1000000000})
UNITS_PER_ORDER = graded({"type": "integer", "minimum": 1, "maximum": 1000000})
LEAD_TIME = graded({"type": "integer", "minimum": 0, "maximum": 3650})
COSTS = obj({name: {
    **graded(MONEY, "amount"),
    "properties": {**graded(MONEY, "amount")["properties"],
                   "currency": CURRENCY, "basis": {"enum": bases}},
    "required": ["status", "amount", "currency", "basis"],
} for name, bases in COST_BASES.items()})
SCENARIO_INPUT = obj({"unit_sales": graded(COUNT),
    "shipping_multiplier": graded(MULTIPLIER), "returns_multiplier": graded(MULTIPLIER)})
POLICY = obj({
    "schema_version": {"const": "OwnBrandHumanPolicy.v1"}, "policy_id": TOKEN,
    "authored_by": {"const": "HUMAN_DECLARED_NOT_AUTHENTICATED"},
    "valid_from": DATE, "valid_until": DATE, "currency": CURRENCY,
    "max_evidence_age_seconds": {"type": "integer", "minimum": 0, "maximum": 31536000},
    "allow_hypothesis_only_research": {"type": "boolean"},
    "allow_synthetic_research": {"type": "boolean"},
    "max_downside": optional(MONEY), "research_budget": optional(MONEY),
    "max_research_units": optional({"type": "integer", "minimum": 1, "maximum": 1000000}),
    "proposed_action": {"enum": ["RESEARCH", "PROTOTYPE", "SAMPLE_VALIDATION"]},
})
SCOPE = obj({"campaign_ref": TOKEN, "concept_ref": TOKEN,
    "product_ref": optional(TOKEN), "market": optional(TOKEN),
    "account_ref": optional(ACCOUNT), "measurement_window": optional(WINDOW)})


def human_fields(text):
    return {
        "authored_by": {"const": "HUMAN_DECLARED_NOT_AUTHENTICATED"},
        "declared_at": DATE, "data_kind": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE"]},
        "differentiated_offer": text, "demand_rationale": text, "supplier_quote": text,
        "counterevidence": array(text, 16), "market_unknowns": array(text, 16),
        "supplier_unknowns": array(text, 16), "moq": MOQ_INPUT,
        "units_per_order": UNITS_PER_ORDER, "logistics_lead_time_days": LEAD_TIME,
        "costs": COSTS, "scenarios": obj({name: SCENARIO_INPUT for name in SCENARIOS}),
    }


REQUEST = obj({"schema_version": {"const": "OwnBrandEvaluation.v1"},
    "scope": {**SCOPE, "required": ["campaign_ref", "concept_ref", "market"]},
    "currency": CURRENCY, "as_of": DATE, "human": obj(human_fields(DECLARATION)),
    "human_policy": optional(POLICY)})
INPUTS = obj({"scope": SCOPE, "currency": CURRENCY, "as_of": DATE,
    "human": obj(human_fields(TEXT_RECEIPT)), "human_policy": optional(POLICY)})
REF = obj({"ref": NATIVE_TOKEN, "sha256": SHA})
PORTFOLIO_REF = obj({
    "schema_version": {"const": "PortfolioRecommendationRef.v1"},
    "recommendation_id": NATIVE_TOKEN, "sha256": SHA,
    "source_sha": {"type": "string", "pattern": "^[a-f0-9]{40}$", "maxLength": 40},
    "candidate_id": NATIVE_TOKEN, "scope_sha256": SHA, "as_of": DATE,
    "decision": {"enum": ["KEEP", "CHANGE", "STOP", "INCONCLUSIVE"]},
    "use": {"const": "A18_2_M07_HUMAN_REVIEW_DATA_ONLY"},
    "financial_commitment": {"const": "NONE"}, "execution_authority": {"const": "NONE"},
})
NATIVE_SCOPE = obj({"campaign_ref": optional(NATIVE_TOKEN), "concept_ref": optional(NATIVE_TOKEN),
    "product_ref": optional(NATIVE_TOKEN), "market": optional(NATIVE_TOKEN),
    "account_ref": optional(obj({"namespace": NATIVE_TOKEN, "id": NATIVE_TOKEN})),
    "measurement_window": WINDOW})
NATIVE_RECEIPT = obj({
    "kind": {"enum": ["PROPOSAL", "ADVISORY", "REPORT", "COMPARISON", "COST", "REVIEW"]},
    "source_id": NATIVE_TOKEN, "sha256": SHA, "native_version": NATIVE_TOKEN,
    "custody": {"enum": ["NATIVE_REPLAY", "ARCHIVE_VALIDATED", "UNVERIFIED"]},
    "epistemic_status": {"enum": ["HYPOTHESIS", "DESCRIPTIVE_OBSERVATION", "DESCRIPTIVE_COMPARISON",
                                   "COST_ASSERTION", "COMMERCIAL_ASSERTION", "ADVISORY"]},
    "attestation": {"const": "NOT_PROVIDER_ATTESTED"},
    "data_kind": {"enum": ["SYNTHETIC_FIXTURE", "USER_SUPPLIED", "UNVERIFIED", "MIXED"]},
    "scope_link": {"enum": ["MATCHED", "PARTIAL", "UNLINKED", "MISMATCH"]},
    "native_refs": array(REF, 128),
})
COUNTER = obj({"source_id": NATIVE_TOKEN, "code": CODE})
PORTFOLIO_RECEIPT = obj({
    "reference": PORTFOLIO_REF, "reference_sha256": SHA,
    "custody": {"enum": ["CURRENT_REPLAY", "ARCHIVED_ONLY"]},
    "scope": NATIVE_SCOPE, "currency": CURRENCY,
    "native_max_evidence_age_seconds": {"type": "integer", "minimum": 0, "maximum": 31536000},
    "evidence_quality": {"enum": ["MISSING", "UNVERIFIED", "LOCAL_REPLAY_ONLY", "CONFLICTED"]},
    "source_codes": array(CODE, 96), "counterevidence": array(COUNTER, 128),
    "native_sources": array(NATIVE_RECEIPT, 32), "observed_timestamps": array(DATE, 256),
    "current_observation_count": {"type": "integer", "minimum": 0, "maximum": 32},
    "interpretation": {"const": "HUMAN_SUPPLIED_DESCRIPTIVE_ONLY"},
    "renewed_execution_authority": {"const": "NONE"},
})
COMMERCIAL_RECEIPT = obj({
    "review_id": NATIVE_TOKEN, "sha256": SHA, "native_version": {"const": "CommercialReview.v1"},
    "custody": {"enum": ["CURRENT_REPLAY", "ARCHIVED_ONLY"]}, "native_refs": array(REF, 130),
    "scope": NATIVE_SCOPE, "currencies": array(CURRENCY, 128),
    "source_as_of": DATE, "observed_timestamps": array(DATE, 512),
    "status": {"enum": ["PARTIAL", "HOLD"]}, "source_codes": array(CODE, 32),
    "reported_statuses": array(CODE, 8),
    "data_kind": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE", "MIXED", "UNVERIFIED"]},
    "pending_financial_evidence": {"type": "boolean"}, "refund_return_present": {"type": "boolean"},
    "actual_paid_orders": {"const": "UNKNOWN"}, "actual_settlement": {"const": "UNKNOWN"},
    "realized_profit": {"const": "UNKNOWN"}, "costs_added_to_scenario": {"const": False},
    "attestation": {"const": "NOT_PROVIDER_ATTESTED"},
})
SOURCES = obj({"portfolio": optional(PORTFOLIO_RECEIPT), "commercial_review": optional(COMMERCIAL_RECEIPT)})
MEASURE = obj({"value": optional(NUMBER), "reason_codes": array(CODE, 32)})
UNITS = obj({"value": optional({"type": "integer", "minimum": 0, "maximum": 10**48}),
             "reason_codes": array(CODE, 32)})
SCENARIO_RESULT = obj({"name": {"enum": list(SCENARIOS)}, "unit_sales": optional(COUNT),
    "gross_contribution_per_unit": MEASURE, "revenue": MEASURE, "cash_cost": MEASURE,
    "profit_or_loss": MEASURE, "loss": MEASURE, "unsold_units": UNITS,
    "cash_requirement": MEASURE})
CALCULATIONS = obj({
    "rounding": {"const": "DECIMAL_80_DIGITS_HALF_UP_6_PLACES_AT_UNIT_COST_AND_RESULT"},
    "inventory_policy": {"const": "ONE_MOQ_BATCH_NO_SALVAGE_UNSOLD_WRITTEN_OFF_NO_REORDER"},
    "buffer_policy": {"const": "RESERVE_IN_CASH_REQUIREMENT_EXCLUDED_FROM_PROFIT_OR_LOSS"},
    "a15_a16_allocation": {"const": "REFERENCE_ONLY_NO_COSTS_OR_PROFIT_IMPORTED"},
    "gross_contribution_per_unit": MEASURE, "moq_cash_requirement": MEASURE,
    "break_even_units": UNITS, "batch_break_even_units": UNITS,
    "worst_case_inventory_exposure": MEASURE, "worst_case_loss": MEASURE,
    "worst_case_cash_exposure": MEASURE, "capital_gap": MEASURE,
    "scenarios": {**array(SCENARIO_RESULT, 3), "minItems": 3},
})
LIMITATIONS = [
    "All assumptions, quotes, costs, policy and scenarios are Human supplied and unauthenticated.",
    "A10 proposals and the demand rationale are hypotheses, never observed or proven market demand.",
    "A11 local observations are descriptive; no causal uplift or provider performance is attested.",
    "A12 seller listings, offers and claims do not verify a supplier, rights, current price or demand.",
    "A15 costs have no automatic allocation; A16 paid orders, settlement and realized profit remain UNKNOWN.",
    "Synthetic fixtures test arithmetic and research decisions only; they are not live business evidence.",
    "Hashes identify local content; archived validation does not renew custody, freshness, rights or authority.",
    "No FX, supplier or market guesses, cross-source financial joins or automatic double counting occur.",
    "Confidence is an evidence rating, never a probability of success.",
    "Human alone controls capital, inventory, vendor outreach and any separately authorized next action.",
]


def schema():
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:OwnBrandDecisionBrief:v1", "title": "OwnBrandDecisionBrief.v1",
        "description": "M07 offline research decision support owned by Commerce Opportunity.",
        **obj({
            "schema_version": {"const": "OwnBrandDecisionBrief.v1"},
            "brief_id": {"type": "string", "pattern": "^a18-own-brand-[a-f0-9]{64}$", "maxLength": 78},
            "evaluator_version": {"const": EVALUATOR_VERSION}, "source_sha": {"const": SOURCE_SHA},
            "request_sha256": SHA, "inputs": INPUTS, "sources": SOURCES,
            "calculations": CALCULATIONS,
            "decision": {"enum": ["DEFER", "NO_GO", "CONDITIONAL_GO"]},
            "decision_meaning": {"const": "HUMAN_REVIEW_OF_BOUNDED_VALIDATION_ONLY"},
            "reason_codes": array(CODE, 128), "blocking_reasons": array(CODE, 128),
            "evidence_grade": {"enum": ["HYPOTHESIS_ONLY_UNATTESTED", "SYNTHETIC_NON_ATTESTED",
                "USER_SUPPLIED_DESCRIPTIVE_UNATTESTED", "ARCHIVED_UNATTESTED", "CONFLICTED_UNATTESTED"]},
            "confidence_meaning": {"const": "EVIDENCE_RATING_NOT_PROBABILITY"},
            "demand": obj({"rationale": TEXT_RECEIPT, "status": {"const": "HYPOTHESIS_NOT_PROVEN"},
                "descriptive_observation_count": {"type": "integer", "minimum": 0, "maximum": 32},
                "observed_market_demand": {"const": "UNKNOWN"}, "proven_demand": {"const": False}}),
            "unknowns": {"const": ["MARKET_SIZE", "SUPPLIER_AUTHENTICITY", "QUOTE_VALIDITY", "RIGHTS",
                "CERTIFICATION", "LOGISTICS_RELIABILITY", "RETURN_RATE", "LIVE_PRICE", "PAID_DEMAND"]},
            "counterevidence": array(COUNTER, 160), "human_validation_checklist": array(CODE, 128),
            "human_action": obj({"kind": {"enum": ["COMPLETE_OR_RECONCILE_BRIEF", "REVIEW_NO_GO",
                "CONSIDER_BOUNDED_RESEARCH", "CONSIDER_BOUNDED_PROTOTYPE", "CONSIDER_BOUNDED_SAMPLE_VALIDATION"]},
                "proposed_only": {"const": True}, "budget_ceiling": optional(MONEY), "currency": CURRENCY,
                "unit_ceiling": optional({"type": "integer", "minimum": 1, "maximum": 1000000}),
                "human_decision_required": {"const": True}, "execution_authority": {"const": "NONE"}}),
            "limitations": {"const": LIMITATIONS}, "execution_authority": {"const": "NONE"},
            "financial_commitment": {"const": "NONE"}, "external_live_verified": {"const": False},
        }),
        "allOf": [{"if": {"properties": {"decision": {"const": "CONDITIONAL_GO"}}},
                   "then": {"properties": {"blocking_reasons": {"maxItems": 0},
                       "human_action": {"properties": {"kind": {"enum": ["CONSIDER_BOUNDED_RESEARCH",
                           "CONSIDER_BOUNDED_PROTOTYPE", "CONSIDER_BOUNDED_SAMPLE_VALIDATION"]}}}}}}],
    })


def bounded(value, *, private=False, native=False):
    """Bound before recursion/serialization. Native envelopes stay opaque, never exported."""
    pending, nodes, size = [(value, 0)], 0, 0
    limit = MAX_NATIVE_BYTES if native else MAX_BYTES
    while pending:
        current, depth = pending.pop()
        nodes += 1
        require(depth <= (32 if native else 16) and nodes <= (50000 if native else 16000), "JSON_COMPLEXITY_LIMIT")
        if type(current) is dict:
            require(len(current) <= 128 and all(type(k) is str for k in current), "INVALID_JSON_OBJECT")
            for key, item in current.items():
                if private:
                    scanned = unicodedata.normalize("NFKC", key).casefold()
                    require(not re.search(r"password|secret|credential|api[_-]?(key|token)|authorization|webhook|\$ref|^token$|^auth$|access_token|refresh_token", scanned),
                            "CREDENTIAL_OR_REFERENCE_FIELD_DENIED")
                pending.extend(((key, depth + 1), (item, depth + 1)))
        elif type(current) is list:
            require(len(current) <= (2048 if native else 512), "JSON_ARRAY_LIMIT")
            pending.extend((item, depth + 1) for item in current)
        elif type(current) is str:
            require(len(current) <= (262144 if native else 2048), "JSON_TEXT_LIMIT")
            if private:
                scanned = unicodedata.normalize("NFKC", current)
                scanned = "".join(c for c in scanned if unicodedata.category(c) != "Cf")
                require(not re.search(r"(?i)(?:[a-z][a-z0-9+.-]*://|https?:|ftp:|file:|data:|javascript:|mailto:|tel:|www\.|sk-[A-Za-z0-9]{16,}|bearer\s|\b(?:api[_-]?key|password|access_token)\s*[:=])", scanned),
                        "URL_OR_CREDENTIAL_DENIED")
                require(not re.search(r"[\\/]|(?:^|\s)~|\.\.|\$\{|\$\(", scanned), "PATH_OR_REFERENCE_DENIED")
                require(not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", scanned), "CONTACT_DENIED")
                require(not any(unicodedata.category(c) in {"Cc", "Cf"} for c in current), "CONTROL_CHARACTER_DENIED")
            try:
                size += len(current.encode("utf-8"))
            except UnicodeError:
                raise OwnBrandError("INVALID_JSON_VALUE") from None
        elif type(current) is int:
            require(current.bit_length() <= 192, "JSON_NUMBER_LIMIT")
            size += 64
        elif type(current) is float:
            require(math.isfinite(current), "JSON_NONFINITE")
            require(not private, "DECIMAL_STRINGS_AND_INTEGER_COUNTS_REQUIRED")
            size += 32
        elif current is None or type(current) is bool:
            size += 5
        else:
            raise OwnBrandError("INVALID_JSON_VALUE")
        require(size <= limit, "JSON_BYTES_LIMIT")
    try:
        raw = canonical_bytes(value)
        require(len(raw) <= limit, "JSON_BYTES_LIMIT")
        return raw
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise OwnBrandError("INVALID_JSON_VALUE") from None


def parse(raw, *, private=False, native=False):
    require(type(raw) is bytes and 0 < len(raw) <= (MAX_NATIVE_BYTES if native else MAX_BYTES), "JSON_BYTES_LIMIT")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise OwnBrandError("JSON_NONFINITE")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
        bounded(value, private=private, native=native)
        return value
    except OwnBrandError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise OwnBrandError("INVALID_JSON") from None


def digest(value):
    return sha256_bytes(bounded(value))


def instant(value):
    try:
        require(type(value) is str and len(value) <= 64, "INVALID_CLOCK")
        result = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        require(result.utcoffset() is not None, "INVALID_CLOCK")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise OwnBrandError("INVALID_CLOCK") from None


@lru_cache(maxsize=1)
def validator():
    return Draft202012Validator(schema(), registry=Registry(), format_checker=_format_checker())
