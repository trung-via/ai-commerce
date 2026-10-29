"""Deterministic, side-effect-free capability decisions: no API or OAuth."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .model import CapabilityError, provider_profiles, timestamp, validate, validate_request
from .registry import CapabilityRegistry

_API_ACTIONS = {"API_UPLOAD", "API_DIRECT_POST", "PUBLIC_VISIBILITY", "PRODUCT_ANCHOR", "METRICS_READ", "SCHEDULE_POST"}
_ACCOUNT_KINDS = {"ACCOUNT_AUTHORIZATION", "SCOPE_OBSERVATION", "ACCOUNT_STATE_OBSERVATION"}
_MANUAL_KINDS = {"MANUAL_ATTESTATION", "ACCOUNT_AUTHORIZATION", "ACCOUNT_STATE_OBSERVATION"}


def evaluate(
    request: dict[str, Any], registry: CapabilityRegistry, *,
    at: datetime | None = None
) -> dict[str, Any]:
    req = validate_request(request)
    now = at if at is not None else datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise CapabilityError("EVALUATION_TIME_MUST_BE_AWARE")
    now = now.astimezone(timezone.utc)
    simulation = req.get("simulation", False)

    def result(decision: str, reason: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        return validate("ChannelCapabilityDecision", {
            "schema_version": "ChannelCapabilityDecision.v1",
            "request": req,
            "decision": decision,
            "reason_codes": [reason],
            "snapshot_ids": [snapshot["snapshot_id"]] if snapshot else [],
            "evidence_ids": [e["evidence_id"] for e in snapshot["evidence_refs"]] if snapshot else [],
            "simulation": simulation,
            "verification": "OFFLINE_RECORDED_NOT_LIVE_VERIFIED",
            "evaluated_at": now.isoformat(),
            "evaluator_version": "a07/1.0",
        })

    snapshot = registry.latest(req)
    if snapshot is None:
        return result("UNKNOWN", "NO_MATCHING_SNAPSHOT")
    if timestamp(snapshot["observed_at"]) > now or timestamp(snapshot["recorded_at"]) > now:
        return result("NEEDS_RECHECK", "SNAPSHOT_FROM_FUTURE", snapshot)
    if snapshot.get("revoked_at") is not None and timestamp(snapshot["revoked_at"]) <= now:
        return result("DENY", "SNAPSHOT_REVOKED", snapshot)
    if snapshot["state"] in ("REVOKED", "DENIED"):
        return result("DENY", "SNAPSHOT_" + snapshot["state"], snapshot)
    if snapshot["state"] == "UNKNOWN":
        return result("UNKNOWN", "SNAPSHOT_UNKNOWN", snapshot)
    if snapshot["state"] == "EXPIRED":
        return result("NEEDS_RECHECK", "SNAPSHOT_EXPIRED", snapshot)
    if snapshot["expires_at"] is None or now >= timestamp(snapshot["expires_at"]):
        return result("NEEDS_RECHECK", "EVIDENCE_EXPIRED_OR_UNBOUNDED", snapshot)
    if snapshot["source_mode"] == "TEST_FIXTURE" and not simulation:
        return result("NEEDS_RECHECK", "FIXTURE_NOT_ACCOUNT_PROOF", snapshot)
    if snapshot["source_mode"] == "OBSERVED" and simulation:
        return result("NEEDS_RECHECK", "SIMULATION_NOT_OBSERVED_PROOF", snapshot)

    profile = provider_profiles()
    if snapshot["profile_version"] != profile["version"]:
        return result("NEEDS_RECHECK", "PROVIDER_PROFILE_VERSION_CHANGED", snapshot)
    platform = profile["platforms"].get(req["platform"], {})
    action = platform.get("actions", {}).get(req["action"])
    if action is None:
        return result("UNKNOWN", "ACTION_NOT_PROFILED", snapshot)
    if req["surface"] not in platform.get("surfaces", []):
        return result("UNKNOWN", "SURFACE_NOT_PROFILED", snapshot)
    if req["action"] in _API_ACTIONS and (req["app_ref"] is None or req["app_ref"]["namespace"] == "internal_alias"):
        return result("NEEDS_RECHECK", "APP_IDENTITY_MISSING_OR_ALIAS", snapshot)
    if req["action"] in _API_ACTIONS and req["market"] is None:
        return result("NEEDS_RECHECK", "MARKET_UNPROVEN", snapshot)
    if req["action"] in _API_ACTIONS and req["account_ref"]["namespace"] == "internal_alias" and not simulation:
        return result("NEEDS_RECHECK", "ACCOUNT_IDENTITY_UNVERIFIED", snapshot)

    if snapshot["source_mode"] == "TEST_FIXTURE":
        eligible = [e for e in snapshot["evidence_refs"] if e["kind"] == "TEST_FIXTURE" and e["scope"] == "TARGET"]
    else:
        kinds = _MANUAL_KINDS if req["action"] == "MANUAL_POST" else _ACCOUNT_KINDS
        eligible = [e for e in snapshot["evidence_refs"] if e["kind"] in kinds and e["scope"] == "TARGET"]
    if not eligible:
        return result("NEEDS_RECHECK", "ACCOUNT_SPECIFIC_EVIDENCE_MISSING", snapshot)

    if req["platform"] == "TIKTOK" and req["action"] == "API_DIRECT_POST" and not snapshot["constraints"].get("visibility"):
        return result("NEEDS_RECHECK", "VISIBILITY_CONSTRAINT_UNPROVEN", snapshot)
    required = action.get("required_scopes", [])
    if "login_modes" in action:
        mode = snapshot["constraints"].get("login_mode")
        if not isinstance(mode, str) or mode not in action["login_modes"]:
            return result("NEEDS_RECHECK", "LOGIN_MODE_UNPROVEN", snapshot)
        required = action["login_modes"][mode]
        if snapshot["constraints"].get("account_class") != "PROFESSIONAL":
            return result("NEEDS_RECHECK", "PROFESSIONAL_ACCOUNT_NOT_PROVEN", snapshot)
        if mode == "FACEBOOK_LOGIN" and snapshot["constraints"].get("linked_page_verified") != "TRUE":
            return result("NEEDS_RECHECK", "LINKED_PAGE_NOT_PROVEN", snapshot)
    if snapshot["source_mode"] == "OBSERVED" and req["action"] in _API_ACTIONS:
        kinds = {e["kind"] for e in snapshot["evidence_refs"] if e["scope"] == "TARGET"}
        if "ACCOUNT_AUTHORIZATION" not in kinds:
            return result("NEEDS_RECHECK", "ACCOUNT_AUTHORIZATION_NOT_OBSERVED", snapshot)
        if required and "SCOPE_OBSERVATION" not in kinds:
            return result("NEEDS_RECHECK", "SCOPE_EVIDENCE_MISSING", snapshot)
        if req["platform"] == "TIKTOK" and req["action"] == "API_DIRECT_POST" and "APP_CONFIGURATION" not in kinds:
            return result("NEEDS_RECHECK", "APP_AUDIT_EVIDENCE_MISSING", snapshot)

    if snapshot["state"] == "LIMITED":
        if not snapshot["constraints"]:
            return result("NEEDS_RECHECK", "LIMITED_WITHOUT_CONSTRAINTS", snapshot)
        for constraint in snapshot["constraints"]:
            if constraint not in req.get("required_constraints", {}):
                return result("NEEDS_RECHECK", "LIMITED_CONSTRAINT_NOT_REQUESTED:" + constraint, snapshot)

    scope_claims = {s["name"]: s["status"] for s in snapshot["scope_claims"]}
    for name in required:
        if scope_claims.get(name) in ("REVOKED", "NOT_GRANTED"):
            return result("DENY", "REQUIRED_SCOPE_REVOKED_OR_DENIED", snapshot)
        if scope_claims.get(name) != "GRANTED":
            return result("NEEDS_RECHECK", "REQUIRED_SCOPE_NOT_PROVEN", snapshot)

    for key, needed in req.get("required_constraints", {}).items():
        allowed = snapshot["constraints"].get(key)
        if allowed is None:
            return result("NEEDS_RECHECK", "CONSTRAINT_UNPROVEN:" + key, snapshot)
        if needed not in (allowed if isinstance(allowed, list) else [allowed]):
            return result("DENY", "CONSTRAINT_INCOMPATIBLE:" + key, snapshot)
    return result("ALLOW", "RECORDED_CAPABILITY_MATCH", snapshot)
