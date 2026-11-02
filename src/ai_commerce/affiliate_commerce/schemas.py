"""E04's self-contained contract. Native upstream contracts remain authoritative."""
from copy import deepcopy

from ai_commerce.affiliate_attribution.schemas import ACCOUNT, DATE, SHA, TOKEN, array, nullable, obj


IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 256,
              "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$"}
REFERENCE = {"type": "string", "minLength": 1, "maxLength": 512}
APP = obj({"namespace": {"enum": ["internal_alias", "tiktok_client_key", "meta_app_id",
                                  "google_cloud_project_id"]}, "id": TOKEN})
SCOPE = obj({
    "provider_ref": TOKEN, "platform": {"enum": ["TIKTOK", "YOUTUBE", "FACEBOOK", "INSTAGRAM"]},
    "account_ref": ACCOUNT, "app_ref": nullable(APP),
    "market": {"type": "string", "pattern": "^[A-Z]{2}$"}, "surface": TOKEN,
    "channel": TOKEN, "product_id": IDENTIFIER, "listing_id": nullable(IDENTIFIER),
    "variant_id": IDENTIFIER, "link_ref": TOKEN, "link_url_sha256": SHA,
    "creative_ref": TOKEN, "creative_sha256": SHA,
    "measurement_window": {"type": "array", "prefixItems": [DATE, DATE],
                           "items": False, "minItems": 2, "maxItems": 2},
    "timezone": {"const": "UTC"},
})
REQUEST = obj({"schema_version": {"const": "AffiliateCommerceRequest.v1"},
               "idempotency_key": TOKEN, "as_of": DATE, "scope": SCOPE})
SLOTS = ("e01_manifest", "e01_delivery", "render_manifest", "product_pack", "release_pack",
         "capability_snapshot", "distribution_package", "publication_record", "attribution")
BLOB = obj({"raw_utf8": {"type": "string", "minLength": 1, "maxLength": 8 * 1024 * 1024},
            "raw_sha256": SHA})
STAGE = obj({"status": {"enum": ["VERIFIED_LOCAL", "READY_FOR_MANUAL_REVIEW",
    "EXPORTED_FOR_MANUAL_HANDOFF", "MANUAL_ACTION_REQUIRED", "SUBMITTED", "PUBLISHED_CONFIRMED",
    "REMOVED", "REJECTED", "PARTIAL", "UNKNOWN", "HOLD", "NOT_PROVIDED"]},
    "evidence_slots": {**array({"enum": list(SLOTS)}, 9), "uniqueItems": True},
    "reason_codes": {**array(TOKEN, 32), "uniqueItems": True}})
STAGES = ("creative", "product", "release", "capability", "distribution", "publication",
          "attribution", "observations", "commercial")


def affiliate_commerce_request_schema():
    return deepcopy({"$schema": "https://json-schema.org/draft/2020-12/schema", **REQUEST})


def affiliate_commerce_run_schema():
    return deepcopy({"$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:AffiliateCommerceRun:v1", "title": "AffiliateCommerceRun.v1",
        **obj({
            "schema_version": {"const": "AffiliateCommerceRun.v1"},
            "evaluator_version": {"const": "a14-e04/1.0"}, "request": REQUEST,
            "evidence": obj({slot: nullable(BLOB) for slot in SLOTS}),
            "stages": obj({stage: STAGE for stage in STAGES}),
            "creative_output": obj({"video_ref": {"const": "video.mp4"}, "sha256": SHA,
                "source_run_id": TOKEN, "composition_ref": REFERENCE,
                "render_manifest_ref": REFERENCE, "custody_output_ref": REFERENCE}),
            "status": {"enum": ["PARTIAL", "HOLD"]},
            "release_decision": {"enum": ["READY_FOR_MANUAL_REVIEW", "HOLD", "UNKNOWN"]},
            "publication_status": {"enum": ["NOT_ATTEMPTED", "MANUAL_ACTION_REQUIRED",
                "SUBMITTED", "PUBLISHED_CONFIRMED", "REMOVED", "REJECTED", "UNKNOWN"]},
            "visibility_status": {"enum": ["PUBLIC", "UNKNOWN"]},
            "link_status": {"const": "UNKNOWN"}, "actual_orders_verified": {"const": False},
            "actual_affiliate_entitlement": {"const": "UNKNOWN"},
            "actual_revenue": {"const": "UNKNOWN"}, "actual_payout": {"const": "UNKNOWN"},
            "settlement_status": {"const": "UNKNOWN"},
            "verification_scope": {"const": "OFFLINE_NATIVE_CONTRACTS_AND_PINNED_LOCAL_SNAPSHOTS"},
            "network_calls_performed": {"const": False}, "advisory_only": {"const": True},
            "record_sha256": SHA,
            "run_id": {"type": "string", "pattern": "^a14-commerce-[a-f0-9]{64}$"},
        })})
