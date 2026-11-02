"""Strict, local-only A16 contracts. Hashes identify bytes, not providers."""
from copy import deepcopy

from ai_commerce.affiliate_attribution.schemas import DATE, SHA, TOKEN, array, nullable, obj
from ai_commerce.affiliate_commerce.schemas import SCOPE as COMMERCE_SCOPE

SETTLEMENT_SCHEMA = "SettlementEvidence.v1"
REVIEW_SCHEMA = "CommercialReview.v1"
STATES = ["UNKNOWN", "CREATED", "ATTRIBUTED", "APPROVED", "PENDING", "REFUNDED",
          "REVOKED", "PARTIALLY_SETTLED", "SETTLED"]
ORIGINS = ["USER_SUPPLIED", "SYNTHETIC_FIXTURE"]
KINDS = {"ORDER": "order_gross", "COMMISSION": "credited_commission",
         "SETTLEMENT": "settled_commission", "REFUND": "refund_gross",
         "REVERSAL": "reversed_commission", "FEE": "fees"}
CURRENCY = {"type": "string", "pattern": "^[A-Z]{3}$", "maxLength": 3}
DECIMAL = {"type": "string", "pattern": r"^(0|[1-9][0-9]{0,23})(\.[0-9]{1,6})?$",
           "maxLength": 31, "not": {"pattern": r"\s"}}
EVIDENCE_ID = {"type": "string", "pattern": "^a16-settlement-[a-f0-9]{64}$", "maxLength": 79}
SCOPE = obj({**deepcopy(COMMERCE_SCOPE["properties"]), "order_ref": TOKEN, "currency": CURRENCY})
MONEY = obj({"status": {"enum": ["REPORTED", "UNKNOWN"]},
             "amount": nullable(DECIMAL), "currency": CURRENCY})
MONEY["allOf"] = [{"if": {"properties": {"status": {"const": "REPORTED"}}},
                   "then": {"properties": {"amount": DECIMAL}},
                   "else": {"properties": {"amount": {"type": "null"}}}}]
AMOUNTS = obj({name: MONEY for name in KINDS.values()})
ATTRIBUTION = obj({"evidence_id": {"type": "string", "pattern": "^a14-attribution-[a-f0-9]{64}$"},
                   "raw_sha256": SHA, "event_ref": TOKEN, "tier": {"enum": ["A", "B", "C", "D"]}})
RECEIPT = obj({"receipt_ref": TOKEN, "raw_sha256": SHA,
               "correction_of": nullable(SHA), "source_kind": {"enum": ORIGINS},
               "scope": SCOPE, "kind": {"enum": list(KINDS)},
               "occurred_at": DATE, "amount": DECIMAL})
SOURCE = obj({"schema_version": {"const": "SettlementSource.v1"},
              "idempotency_key": TOKEN, "source_ref": TOKEN,
              "source_kind": {"enum": ORIGINS}, "recorded_at": DATE,
              "scope": SCOPE, "reported_status": {"enum": STATES},
              "commission_basis": {"enum": ["UNKNOWN", "MERCHANT_GROSS", "MERCHANT_NET", "CREATOR_SHARE"]},
              "attribution": nullable(ATTRIBUTION), "amounts": AMOUNTS,
              "receipts": array(RECEIPT, 64)})


def blob_schema(limit):
    return obj({"raw_utf8": {"type": "string", "minLength": 1, "maxLength": limit}, "raw_sha256": SHA})


def _contract(title, fields):
    return deepcopy({"$schema": "https://json-schema.org/draft/2020-12/schema",
                     "$id": "urn:ai-commerce:" + title.replace(".v1", ":v1"), "title": title,
                     "description": "Offline supplied assertions only. Native replay is required; checksums do not authenticate providers.",
                     **obj(fields)})


def settlement_source_schema():
    return deepcopy(SOURCE)


def settlement_evidence_schema():
    return _contract(SETTLEMENT_SCHEMA, {
        "schema_version": {"const": SETTLEMENT_SCHEMA}, "evaluator_version": {"const": "a16-l05/1.0"},
        "evidence_id": EVIDENCE_ID, "record_sha256": SHA, "correction_of": nullable(EVIDENCE_ID),
        "source": obj({**blob_schema(262144)["properties"], "canonical_sha256": SHA, "document": SOURCE}),
        "attribution_snapshot": nullable(blob_schema(2 * 1024 * 1024)),
        "attribution_tier": {"enum": ["A", "B", "C", "D", "UNKNOWN"]},
        "source_status": {"const": "SUPPLIED_NOT_PROVIDER_ATTESTED"},
        "actual_orders_verified": {"const": False}, "settlement_verified": {"const": False},
        "realized_profit_verified": {"const": False}, "network_calls_performed": {"const": False},
    })


def cost_record_schema():
    # A15 remains the authoritative semantic validator (CostEntry.from_record).
    return obj({"version": {"const": 1}, "record_sha256": SHA, "entry_id": TOKEN,
                "job_id": TOKEN, "attempt": {"type": "integer", "minimum": 1},
                "intent_digest": SHA, "provider": TOKEN, "model": nullable(TOKEN),
                "status": {"enum": ["ESTIMATED", "CHARGED", "PENDING", "UNKNOWN", "NOT_APPLICABLE"]},
                "amount": nullable(DECIMAL), "currency": nullable(CURRENCY),
                "evidence_ref": nullable({"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_:/.-]{0,511}$"}),
                "evidence_sha256": nullable(SHA),
                "evidence_origin": {"enum": ORIGINS + ["UNVERIFIED"]}, "observed_at": DATE})


COST_SELECTION = obj({"entry_id": TOKEN, "job_id": TOKEN,
                      "attempt": {"type": "integer", "minimum": 1},
                      "intent_digest": SHA, "provider": TOKEN})
COUNT = {"type": "integer", "minimum": 0, "maximum": 128}
SUM_DECIMAL = {**DECIMAL, "pattern": r"^(0|[1-9][0-9]{0,26})(\.[0-9]{1,6})?$", "maxLength": 34}
TOTAL = obj({"status": {"enum": ["REPORTED", "PARTIAL", "UNKNOWN"]},
             "amount": nullable(SUM_DECIMAL), "reported_subtotal": nullable(SUM_DECIMAL),
             "known_count": COUNT, "unknown_count": COUNT})
TOTAL["allOf"] = [
    {"if": {"properties": {"status": {"const": "REPORTED"}}},
     "then": {"properties": {"amount": SUM_DECIMAL, "reported_subtotal": SUM_DECIMAL, "known_count": {"minimum": 1}, "unknown_count": {"const": 0}}}},
    {"if": {"properties": {"status": {"const": "PARTIAL"}}},
     "then": {"properties": {"amount": {"type": "null"}, "reported_subtotal": SUM_DECIMAL, "known_count": {"minimum": 1}, "unknown_count": {"minimum": 1}}}},
    {"if": {"properties": {"status": {"const": "UNKNOWN"}}},
     "then": {"properties": {"amount": {"type": "null"}, "reported_subtotal": {"type": "null"}, "known_count": {"const": 0}}}},
]
REASONS = ["OFFLINE_UNATTESTED", "SETTLEMENT_EVIDENCE_MISSING", "COST_EVIDENCE_MISSING",
           "COMMERCIAL_EVIDENCE_INCOMPLETE", "SYNTHETIC_EVIDENCE_PRESENT", "UPSTREAM_HOLD",
           "ATTRIBUTION_AMBIGUOUS", "REFUND_AFTER_SETTLEMENT", "REVOCATION_AFTER_SETTLEMENT",
           "UNKNOWN_COST_CURRENCY"]


def commercial_review_schema():
    return _contract(REVIEW_SCHEMA, {
        "schema_version": {"const": REVIEW_SCHEMA}, "evaluator_version": {"const": "a16-e05/1.0"},
        "review_id": {"type": "string", "pattern": "^a16-review-[a-f0-9]{64}$"}, "record_sha256": SHA,
        "scope": COMMERCE_SCOPE,
        "sources": obj({"commerce_run": blob_schema(20 * 1024 * 1024),
                        "settlement_history": blob_schema(40 * 1024 * 1024),
                        "cost_records": array(cost_record_schema(), 128),
                        "cost_selections": array(COST_SELECTION, 128)}),
        "cost_allocation_basis": {"const": "USER_SUPPLIED_CREATIVE_ALLOCATION_NOT_PROVIDER_VERIFIED"},
        "status": {"enum": ["PARTIAL", "HOLD"]},
        "reason_codes": {**array({"enum": REASONS}, len(REASONS)), "minItems": 1, "uniqueItems": True},
        "orders": array(obj({"order_ref": TOKEN, "evidence_id": EVIDENCE_ID,
                             "reported_status": {"enum": STATES}, "currency": CURRENCY,
                             "source_kind": {"enum": ORIGINS},
                             "attribution_tier": {"enum": ["A", "B", "C", "D", "UNKNOWN"]}}), 128),
        "supplied_order_count": obj({"status": {"enum": ["REPORTED", "UNKNOWN"]}, "value": nullable(COUNT)}),
        "by_currency": array(obj({"currency": CURRENCY,
                                   **{name: TOTAL for name in [*KINDS.values(), "estimated_cost", "supplied_charged_cost", "synthetic_charged_cost"]}}), 128),
        "actual_orders_verified": {"const": False}, "actual_settlement_status": {"const": "UNKNOWN"},
        "external_provider_charge_verified": {"const": False},
        "realized_profit": obj({"status": {"const": "UNKNOWN"}, "amount": {"type": "null"}, "verified": {"const": False}}),
        "network_calls_performed": {"const": False}, "advisory_only": {"const": True},
    })
