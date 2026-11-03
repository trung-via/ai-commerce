"""Closed E07 contracts and private bounded JSON, without schema I/O."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import unicodedata

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

SOURCE_SHA = "62a29f0e9938de0f23540458d9c04522c43fc8e9"
EVALUATOR_VERSION = "a18-e07/1.0"
MAX_BYTES = 262144
MAX_NATIVE_BYTES = 2 * 1024 * 1024
SCENARIOS = ("conservative", "base", "expansion")
PER_ASSET = ("provider_cost", "channel_cost", "operations_cost", "fee_cost")
FIXED = ("fixed_cost", "reserve")


class ScaleOptionError(ValueError):
    """Fixed diagnostic codes only; no native exception or private input text."""


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_bytes(raw):
    return sha256(raw).hexdigest()


def require(ok, code):
    if not ok:
        raise ScaleOptionError(code)


def obj(fields):
    return {"type": "object", "additionalProperties": False,
            "properties": fields, "required": list(fields)}


def array(item, maximum=32):
    return {"type": "array", "maxItems": maximum, "items": item}


def optional(item):
    return {"oneOf": [item, {"type": "null"}]}


TOKEN = {"type": "string", "minLength": 1, "maxLength": 96,
         "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]*$"}
NATIVE_TOKEN = {"type": "string", "minLength": 1, "maxLength": 256,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"}
CODE = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[A-Z][A-Z0-9_]*$"}
SHA = {"type": "string", "maxLength": 64, "pattern": r"^[a-f0-9]{64}$"}
COMMIT = {"type": "string", "maxLength": 40, "pattern": r"^[a-f0-9]{40}$"}
DATE = {"type": "string", "maxLength": 64, "format": "date-time"}
WINDOW = {**array(DATE, 2), "minItems": 2}
CURRENCY = {"type": "string", "maxLength": 3, "pattern": r"^[A-Z]{3}$"}
MONEY = {"type": "string", "maxLength": 25,
         "pattern": r"^(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,6})?$"}
NUMBER = {"type": "string", "maxLength": 55,
          "pattern": r"^-?(?:0|[1-9][0-9]{0,47})\.[0-9]{6}$"}
COUNT = {"type": "integer", "minimum": 0, "maximum": 1000000000}
TEXT = {"type": "string", "minLength": 1, "maxLength": 1024}
ACCOUNT = obj({"namespace": NATIVE_TOKEN, "id": NATIVE_TOKEN})
SCOPE = obj({"campaign_ref": NATIVE_TOKEN, "concept_ref": NATIVE_TOKEN,
    "product_ref": optional(NATIVE_TOKEN), "market": optional(NATIVE_TOKEN),
    "account_ref": optional(ACCOUNT), "provider_ref": optional(NATIVE_TOKEN),
    "platform": optional(TOKEN), "surface": optional(NATIVE_TOKEN),
    "creative_ref": optional(NATIVE_TOKEN), "creative_sha256": optional(SHA),
    "measurement_window": optional(WINDOW)})
NATIVE_SCOPE = obj({**SCOPE["properties"], "campaign_ref": optional(NATIVE_TOKEN),
    "concept_ref": optional(NATIVE_TOKEN)})
POLICY = obj({"schema_version": {"const": "ScaleBrandHumanPolicy.v1"}, "policy_id": TOKEN,
    "authored_by": {"const": "HUMAN_DECLARED_NOT_AUTHENTICATED"},
    "valid_from": DATE, "valid_until": DATE, "currency": CURRENCY,
    "max_evidence_age_seconds": {"type": "integer", "minimum": 0, "maximum": 31536000},
    "evidence_requirement": {"enum": ["HYPOTHESIS_RESEARCH", "CURRENT_DESCRIPTIVE", "CURRENT_COMMERCIAL"]},
    "allow_synthetic_research": {"type": "boolean"},
    "spend_ceiling": optional(MONEY), "available_budget": optional(MONEY),
    "max_downside": optional(MONEY), "max_unit_cost": optional(MONEY),
    "max_experiment_assets": optional({"type": "integer", "minimum": 1, "maximum": 1000}),
    "proposed_action": {"enum": ["RESEARCH", "PROTOTYPE", "SCALE_EXPERIMENT", "LIVE_SCALE"]},
    "capital_or_stock_commitment": {"type": "boolean"},
    "campaign_or_account_changes": {"type": "boolean"}})
PERMISSIONS = obj({"rights_status": {"enum": ["UNKNOWN", "HUMAN_DECLARED", "REVOKED"]},
    "provider_entitlement": {"enum": ["UNKNOWN", "HUMAN_DECLARED", "REVOKED"]},
    "prior_rights_sha256": optional(SHA), "current_rights_sha256": optional(SHA),
    "valid_until": optional(DATE)})
SCENARIO = obj({"assets": optional(COUNT), "capacity_assets": optional(COUNT),
    "costs": obj({name: optional(MONEY) for name in (*PER_ASSET, *FIXED)}),
    "hypothetical_value_per_asset": optional(MONEY)})


def human(text):
    return obj({"authored_by": {"const": "HUMAN_DECLARED_NOT_AUTHENTICATED"},
        "declared_at": DATE, "data_kind": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE"]},
        "rationale": text, "counterevidence": array(text, 16), "uncertainties": array(text, 16)})


def scale(text):
    return obj({"assumption_id": TOKEN, "window": WINDOW, "unit": {"const": "ASSET"},
        "cost_basis": {"const": "PER_ASSET_PLUS_FIXED_ONE_EXPERIMENT"},
        "cost_lineage": {"const": "HUMAN_ESTIMATES_NO_IMPORTED_TOTALS"},
        "economic_basis": {"enum": ["COST_ONLY_RESEARCH", "HYPOTHETICAL_VALUE_PER_ASSET"]},
        "uncertainty": text, "scenarios": obj({name: SCENARIO for name in SCENARIOS})})


REQUEST = obj({"schema_version": {"const": "ScaleBrandEvaluation.v1"},
    "scope": SCOPE, "currency": CURRENCY, "as_of": DATE,
    "human": human(TEXT), "scale": optional(scale(TEXT)),
    "permissions": PERMISSIONS, "human_policy": optional(POLICY)})
INPUTS = obj({"scope": SCOPE, "currency": CURRENCY, "as_of": DATE,
    "human": human(SHA), "scale": optional(scale(SHA)),
    "permissions": PERMISSIONS, "human_policy": optional(POLICY)})
REF = obj({"kind": {"enum": ["PORTFOLIO", "OWN_BRAND", "REVIEW", "COST", "NATIVE"]},
    "source_id": NATIVE_TOKEN, "sha256": SHA})
CUSTODY = {"enum": ["ARCHIVED_ONLY", "CURRENT_REPLAY"]}
MEASURE = obj({"value": optional(NUMBER), "reason_codes": array(CODE, 32)})
UNITS = obj({"value": optional({"type": "integer", "minimum": 0, "maximum": 10**48}),
    "reason_codes": array(CODE, 32)})
BRAND_SCENARIO = obj({"name": {"enum": ["bear", "base", "bull"]}, "unit_sales": optional(COUNT),
    **{k: MEASURE for k in ("gross_contribution_per_unit", "revenue", "cash_cost", "profit_or_loss", "loss", "cash_requirement")},
    "unsold_units": UNITS})
FEASIBILITY = obj({"rounding": {"const": "DECIMAL_80_DIGITS_HALF_UP_6_PLACES_AT_UNIT_COST_AND_RESULT"},
    "inventory_policy": {"const": "ONE_MOQ_BATCH_NO_SALVAGE_UNSOLD_WRITTEN_OFF_NO_REORDER"},
    "buffer_policy": {"const": "RESERVE_IN_CASH_REQUIREMENT_EXCLUDED_FROM_PROFIT_OR_LOSS"},
    "a15_a16_allocation": {"const": "REFERENCE_ONLY_NO_COSTS_OR_PROFIT_IMPORTED"},
    **{k: MEASURE for k in ("gross_contribution_per_unit", "moq_cash_requirement", "worst_case_inventory_exposure",
                           "worst_case_loss", "worst_case_cash_exposure", "capital_gap")},
    "break_even_units": UNITS, "batch_break_even_units": UNITS,
    "scenarios": {**array(BRAND_SCENARIO, 3), "minItems": 3}})
COMMON_RECEIPT = {"source_id": NATIVE_TOKEN, "sha256": SHA, "native_version": NATIVE_TOKEN,
    "source_sha": optional(COMMIT), "evaluator_version": NATIVE_TOKEN,
    "source_as_of": DATE, "scope": NATIVE_SCOPE, "currencies": array(CURRENCY, 128),
    "custody": CUSTODY, "original_request_replayed": {"type": "boolean"},
    "observed_timestamps": array(DATE, 512), "source_codes": array(CODE, 160),
    "lineage": array(REF, 256), "attestation": {"const": "NOT_PROVIDER_ATTESTED"}}
# Evaluator versions are exact native constants, including their slash separator.
PORTFOLIO = obj({**COMMON_RECEIPT, "native_version": {"const": "PortfolioRecommendation.v1"},
    "evaluator_version": {"const": "a18-m06/1.1"},
    "source_sha": {"const": "b08d33f3195c48559d4964cf36c8b03612e41dd9"},
    "source_id": {"type": "string", "maxLength": 78, "pattern": r"^a18-portfolio-[a-f0-9]{64}$"},
    "decision": {"enum": ["KEEP", "CHANGE", "STOP", "INCONCLUSIVE"]},
    "observation_count": {"type": "integer", "minimum": 0, "maximum": 32},
    "data_kind": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE", "MIXED", "UNVERIFIED"]},
    "counterevidence_codes": array(CODE, 128), "scope_discrepancy": {"type": "boolean"},
    "interior_timestamps_complete": {"type": "boolean"},
    "native_max_age_seconds": {"type": "integer", "minimum": 0, "maximum": 31536000},
    "evidence_quality": {"enum": ["MISSING", "UNVERIFIED", "LOCAL_REPLAY_ONLY", "CONFLICTED"]}})
REVIEW = obj({**COMMON_RECEIPT, "native_version": {"const": "CommercialReview.v1"},
    "evaluator_version": {"const": "a16-e05/1.0"},
    "source_sha": {"type": "null"},
    "source_id": {"type": "string", "maxLength": 75, "pattern": r"^a16-review-[a-f0-9]{64}$"},
    "status": {"enum": ["PARTIAL", "HOLD"]}, "pending": {"type": "boolean"},
    "refund_or_revocation": {"type": "boolean"},
    "reported_statuses": array(CODE, 8),
    "data_kind": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE", "MIXED", "UNVERIFIED"]},
    "actual_paid_orders": {"const": "UNKNOWN"}, "actual_settlement": {"const": "UNKNOWN"},
    "realized_profit": {"const": "UNKNOWN"}})
BRAND = obj({**COMMON_RECEIPT, "native_version": {"const": "OwnBrandDecisionBrief.v1"},
    "evaluator_version": {"const": "a18-m07/1.0"},
    "source_sha": {"const": "3ef41b54caa67f1f8bd90a7122868285f42a08a3"},
    "source_id": {"type": "string", "maxLength": 78, "pattern": r"^a18-own-brand-[a-f0-9]{64}$"},
    "decision": {"enum": ["DEFER", "NO_GO", "CONDITIONAL_GO"]},
    "blocking_reasons": array(CODE, 128), "evidence_grade": CODE,
    "native_custody": obj({"portfolio": optional(CUSTODY), "commercial_review": optional(CUSTODY)}),
    "native_bundle_replayed": {"type": "boolean"},
    "native_max_age_seconds": {"type": "integer", "minimum": 0, "maximum": 31536000},
    "interior_timestamps_complete": {"type": "boolean"},
    "feasibility": FEASIBILITY, "feasibility_sha256": SHA,
    "moq": optional(COUNT), "research_budget": optional(MONEY),
    "max_research_units": optional(COUNT), "pending": {"type": "boolean"},
    "refund_or_revocation": {"type": "boolean"}, "counterevidence_codes": array(CODE, 160),
    "proven_demand": {"const": False}})
COST = obj({"entry_id": TOKEN, "sha256": SHA, "observed_at": DATE,
    "status": {"enum": ["ESTIMATED", "CHARGED", "UNKNOWN", "PENDING", "NOT_APPLICABLE"]},
    "amount": optional(MONEY), "currency": optional(CURRENCY),
    "data_kind": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE", "UNVERIFIED"]},
    "allocation": {"const": "REFERENCE_ONLY_NO_NATIVE_SCOPE_BINDING"},
    "provider_charge_verified": {"const": False}})
SOURCES = obj({"portfolio": optional(PORTFOLIO), "own_brand": optional(BRAND),
    "commercial_review": optional(REVIEW), "costs": array(COST, 32)})
SCALE_RESULT = obj({"name": {"enum": list(SCENARIOS)}, "assets": optional(COUNT),
    "capacity_assets": optional(COUNT),
    **{k: MEASURE for k in ("unit_cost", "experiment_cost", "cash_exposure", "budget_gap", "ceiling_gap",
                           "hypothetical_unit_contribution", "hypothetical_total_value", "hypothetical_surplus")},
    "capacity_gap": UNITS, "throughput_assets_per_day": MEASURE,
    "claim": {"const": "HUMAN_COST_SCENARIO_NOT_OBSERVED_PERFORMANCE"}})
CALCULATIONS = obj({"currency": CURRENCY,
    "rounding": {"const": "DECIMAL_80_DIGITS_HALF_UP_6_PLACES_RESULTS"},
    "cost_allocation": {"const": "A15_A16_REFERENCE_ONLY_NO_AUTOMATIC_ADDITION"},
    "downside_basis": {"const": "ZERO_HYPOTHETICAL_VALUE_FULL_COST_AND_RESERVE_AT_RISK"},
    "worst_case_exposure": MEASURE, "scenarios": {**array(SCALE_RESULT, 3), "minItems": 3}})
STATES = ["DEFER", "DO_NOT_SCALE", "CONDITIONAL_VALIDATE", "NOT_APPLICABLE"]
LANE = obj({"lane": {"enum": ["PORTFOLIO_SCALE", "OWN_BRAND"]}, "state": {"enum": STATES},
    "blocking_reasons": array(CODE, 192), "rationale_codes": array(CODE, 192),
    "alternative_paths": array(CODE, 8),
    "human_action": obj({"kind": {"enum": ["NONE", "COMPLETE_OR_RECONCILE_INPUTS", "REVIEW_LIMITS",
        "CONSIDER_BOUNDED_RESEARCH", "CONSIDER_BOUNDED_PROTOTYPE", "CONSIDER_BOUNDED_SCALE_EXPERIMENT"]},
        "budget_ceiling": optional(MONEY), "unit_ceiling": optional(COUNT), "currency": CURRENCY,
        "human_decision_required": {"const": True}, "proposed_only": {"const": True},
        "execution_authority": {"const": "NONE"}})})
LIMITATIONS = [
    "Offline Human decision support only; all policy caps and authorizations remain external to code.",
    "Human assumptions are hypotheses; local observations and supplied receipts are not provider attestation.",
    "M06 KEEP CHANGE STOP are provisional advice with no execution authority.",
    "M07 feasibility is reused unchanged; no brand idea establishes verified demand.",
    "Archive validation preserves historical content and never renews custody freshness rights or entitlements.",
    "Synthetic success proves calculations and conditional offline research only.",
    "No provider availability revenue profit demand launch right or live scaling success is verified.",
    "A15 and A16 costs remain reference only; Human scenario estimates are never summed with their totals.",
    "No FX inferred returns fees uplift conversion probabilities or automatic option ranking.",
    "Human alone decides expenditure campaigns accounts inventory capital supplier contact and launch.",
]


def schema():
    return deepcopy({"$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:ScaleBrandDecisionMemo:v1", "title": "ScaleBrandDecisionMemo.v1",
        **obj({"schema_version": {"const": "ScaleBrandDecisionMemo.v1"},
            "memo_id": {"type": "string", "maxLength": 80, "pattern": r"^a18-scale-brand-[a-f0-9]{64}$"},
            "content_sha256": SHA, "evaluator_version": {"const": EVALUATOR_VERSION},
            "source_sha": {"const": SOURCE_SHA}, "request_sha256": SHA,
            "inputs": INPUTS, "sources": SOURCES,
            "scale_calculations": optional(CALCULATIONS),
            "options": {**array(LANE, 2), "minItems": 2},
            "decision": {"enum": STATES[:3]}, "reason_codes": array(CODE, 256),
            "comparison_basis": {"const": "INDEPENDENT_LANES_NO_SCORE_NO_RANKING_NO_AUTHORITY_TRANSFER"},
            "epistemic_status": obj({"human_assumptions": {"const": "HYPOTHESIS"},
                "native_observations": {"const": "LOCAL_DESCRIPTIVE_NOT_PROVIDER_ATTESTED"},
                "validated_provider_receipts": {"const": "NONE"}, "verified_demand": {"const": False},
                "actual_revenue": {"const": "UNKNOWN"}, "actual_profit": {"const": "UNKNOWN"},
                "actual_returns": {"const": "UNKNOWN"}, "actual_fees": {"const": "UNKNOWN"},
                "actual_settlement": {"const": "UNKNOWN"}}),
            "archive_validation": {"const": "CONTENT_ONLY_NOT_CURRENT_NATIVE_CUSTODY"},
            "limitations": {"const": LIMITATIONS},
            "execution_authority": {"const": "NONE"}, "financial_commitment": {"const": "NONE"},
            "external_live_verified": {"const": False}})})


def bounded(value, *, private=False):
    pending, nodes, size = [(value, 0)], 0, 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        require(depth <= 20 and nodes <= 20000, "JSON_COMPLEXITY_LIMIT")
        if type(current) is dict:
            require(len(current) <= 128 and all(type(k) is str for k in current), "INVALID_JSON_OBJECT")
            for key, item in current.items():
                scanned = unicodedata.normalize("NFKC", key).casefold()
                require(not re.search(r"password|secret|credential|api[_-]?(key|token)|authorization|webhook|\$ref|^token$|^auth$|access_token|refresh_token", scanned),
                        "CREDENTIAL_OR_REFERENCE_FIELD_DENIED")
                pending.extend(((key, depth + 1), (item, depth + 1)))
        elif type(current) is list:
            require(len(current) <= 512, "JSON_ARRAY_LIMIT")
            pending.extend((item, depth + 1) for item in current)
        elif type(current) is str:
            require(len(current) <= 2048, "JSON_TEXT_LIMIT")
            if private:
                scanned = unicodedata.normalize("NFKC", current)
                scanned = "".join(c for c in scanned if unicodedata.category(c) != "Cf")
                require(not re.search(r"(?i)(?:[a-z][a-z0-9+.-]*://|https?:|ftp:|file:|data:|javascript:|mailto:|tel:|www\.|sk-[A-Za-z0-9]{16,}|bearer\s|\b(?:api[_-]?key|password|access_token|private_key)\s*[:=])", scanned),
                        "URL_OR_CREDENTIAL_DENIED")
                require(scanned in {EVALUATOR_VERSION, "a18-m06/1.1", "a18-m07/1.0", "a16-e05/1.0"}
                        or not re.search(r"[\\/]|(?:^|\s)~|\.\.|\$\{|\$\(", scanned), "PATH_OR_REFERENCE_DENIED")
                require(not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+|\+[0-9][0-9 ()-]{7,}[0-9]", scanned), "CONTACT_DENIED")
                require(not any(unicodedata.category(c) in {"Cc", "Cf"} for c in current), "CONTROL_CHARACTER_DENIED")
            try:
                size += len(current.encode("utf-8"))
            except UnicodeError:
                raise ScaleOptionError("INVALID_JSON_VALUE") from None
        elif type(current) is int:
            require(current.bit_length() <= 192, "JSON_NUMBER_LIMIT")
            size += 64
        elif current is None or type(current) is bool:
            size += 5
        else:
            raise ScaleOptionError("DECIMAL_STRINGS_AND_INTEGER_COUNTS_REQUIRED")
        require(size <= MAX_BYTES, "JSON_BYTES_LIMIT")
    try:
        raw = canonical_bytes(value)
        require(len(raw) <= MAX_BYTES, "JSON_BYTES_LIMIT")
        return raw
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ScaleOptionError("INVALID_JSON_VALUE") from None


def parse(raw, *, private=False):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "JSON_BYTES_LIMIT")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise ScaleOptionError("JSON_NONFINITE")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
        bounded(value, private=private)
        return value
    except ScaleOptionError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ScaleOptionError("INVALID_JSON") from None


def digest(value):
    return sha256_bytes(bounded(value))


def instant(value):
    try:
        require(type(value) is str and len(value) <= 64, "INVALID_CLOCK")
        result = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        require(result.utcoffset() is not None, "INVALID_CLOCK")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ScaleOptionError("INVALID_CLOCK") from None


def validator(contract=None):
    # Fresh validators avoid shared mutable schema/cache poisoning and remote resolution.
    return Draft202012Validator(schema() if contract is None else deepcopy(contract),
                                registry=Registry(), format_checker=FormatChecker())
