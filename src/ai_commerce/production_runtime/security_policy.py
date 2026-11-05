"""B0-S pure policy model, deliberately not connected to execution.

There is NO private verifier or positive external-release API. Native scope
bytes and local comparison markers are untrusted model inputs, not custody,
authentication, replay-store or anti-rollback proof. Existing B0.3/B0.4 locks,
leases, QA, path checks, rights reopening and recovery remain mandatory.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re

from .contracts import ContractError, parse_contract
from .security_claims import (
    ClaimError, EXTERNAL_ACTIONS, LOCAL_ACTIONS, canonical_bytes, closed,
    decode_json, is_hash, is_ref, target_digest, validate_intent,
)

# These are the frozen B0.3 local scope, not this change's Git baseline.
# No import of its filesystem/subprocess adapters into this pure model.
NATIVE_SOURCE_SHA = "32f42b84567e1217a93854389b1c96435bbf454d"
NATIVE_TOOL = "tool:a06-e01-offline-local-stills"
REASONS = (
    "LOCAL_MODEL_ONLY_RECHECK_NATIVE_GATES", "REJECT_STRUCTURE", "REJECT_UNKNOWN_DOMAIN",
    "REJECT_UNKNOWN_ACTION", "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED", "HOLD_NATIVE_SCOPE",
    "HOLD_SCOPE_MISMATCH", "HOLD_LOCAL_RIGHTS", "HOLD_RECOVERY", "HOLD_REPLAY",
    "HOLD_NATIVE_ACTION_NOT_ALLOWED",
)


@dataclass(frozen=True, slots=True)
class Decision:
    """Finite, data-free response; ALLOW_LOCAL is an inert policy-model result."""
    outcome: str
    reason: str

    def __post_init__(self):
        if (self.outcome not in ("ALLOW_LOCAL", "HOLD", "REJECT") or self.reason not in REASONS
                or (self.outcome == "ALLOW_LOCAL") != (self.reason == REASONS[0])
                or (self.outcome == "HOLD") != self.reason.startswith("HOLD_")
                or (self.outcome == "REJECT") != self.reason.startswith("REJECT_")):
            raise ValueError("INVALID_DECISION")


@dataclass(frozen=True, slots=True)
class LocalScope:
    """Injected native bytes and synthetic comparison state, never authority.

    No verified/trusted signer parameter exists. Fresh byte validation occurs
    on every decision; constructing this object does not admit native execution.
    """
    directive_raw: bytes
    native_intent_raw: bytes
    state: str
    marker_status: str = "UNKNOWN"
    used_nonces: tuple[str, ...] = ()
    used_requests: tuple[str, ...] = ()
    used_targets: tuple[str, ...] = ()


def _hold(reason: str) -> Decision:
    return Decision("HOLD", reason)


def _scope_data(scope: LocalScope) -> tuple[dict, dict]:
    if (type(scope) is not LocalScope or type(scope.directive_raw) is not bytes
            or type(scope.native_intent_raw) is not bytes or len(scope.directive_raw) > 16384):
        raise ClaimError("INVALID_PIN")
    native = decode_json(scope.native_intent_raw)
    closed(native, ("schema_version", "source_sha", "journey_id", "directive_sha256", "directive_version",
                    "scope", "directive_file_sha256", "directive_record_sha256", "runtime_sha256",
                    "assets", "tool", "evidence_type", "parameters", "render_ref", "journal_id"))
    record = parse_contract("CreativeDirective", scope.directive_raw)
    directive = record.to_dict()
    expected_scope = {"action_class": "EXPORT_DRAFT", "provider_ref": None,
                      "account_ref": None, "surface": None, "asset_sha256": None}
    if (native["schema_version"] != "AttendedOfflineIntent.v1" or native["source_sha"] != NATIVE_SOURCE_SHA
            or native["scope"] != expected_scope or directive["scope"] != expected_scope
            or directive["action_classes"] != ["EXPORT_DRAFT"]
            or directive["allowed_tools"] != [NATIVE_TOOL] or native["tool"] != NATIVE_TOOL
            or canonical_bytes(native["parameters"]) != canonical_bytes(
                {"width": 64, "height": 96, "fps": 12, "frames": 3, "silence": False})
            or not is_hash(native["runtime_sha256"])):
        raise ClaimError("INVALID_LOCAL_SCOPE")
    for key in ("source_sha", "journey_id", "directive_sha256", "directive_version"):
        if canonical_bytes(native[key]) != canonical_bytes(directive[key]):
            raise ClaimError("INVALID_LOCAL_SCOPE")
    identity = sha256(record.raw).hexdigest()
    if (native["directive_record_sha256"] != identity
            or native["directive_file_sha256"] != sha256(scope.directive_raw).hexdigest()
            or native["render_ref"] != "local:e01-" + identity[:24]
            or native["journal_id"] != "journal:" + identity[:32]):
        raise ClaimError("INVALID_LOCAL_SCOPE")
    assets = directive["assets"]
    if not 1 <= len(assets) <= 4 or len({a["sha256"] for a in assets}) != len(assets):
        raise ClaimError("INVALID_LOCAL_SCOPE")
    expected_assets = [{"sha256": a["sha256"], "evidence_type": a["evidence_type"],
                        "rights_sha256": a["rights"]["sha256"]} for a in assets]
    origin = directive["provenance"]
    if (not is_ref(directive["journey_id"], "journey")
            or (directive["creative"]["product_ref"] is not None
                and not is_ref(directive["creative"]["product_ref"], "product"))
            or any(type(origin[k]) is not str or re.fullmatch(
                r"(?:human|fixture|local|directive):[0-9a-f]{32,64}", origin[k]) is None
                for k in ("actor_ref", "source_ref"))):
        raise ClaimError("INVALID_LOCAL_SCOPE")
    if ((origin["actor"], origin["evidence_type"]) not in
            (("HUMAN", "HUMAN_SUPPLIED"), ("FIXTURE", "SYNTHETIC_FIXTURE"))
            or canonical_bytes(native["assets"]) != canonical_bytes(expected_assets)
            or native["evidence_type"] != ("SYNTHETIC_FIXTURE" if origin["evidence_type"] == "SYNTHETIC_FIXTURE"
                or any(a["evidence_type"] == "SYNTHETIC_FIXTURE" for a in assets) else "HUMAN_SUPPLIED")):
        raise ClaimError("INVALID_LOCAL_SCOPE")
    for asset in assets:
        right = asset["rights"]
        if (not is_ref(asset["ref"], "asset") or asset["evidence_type"] not in ("SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED")
                or (asset["evidence_type"] == "SYNTHETIC_FIXTURE" and (origin["evidence_type"] != "SYNTHETIC_FIXTURE"
                    or right != {"status": "NOT_APPLICABLE", "ref": None, "sha256": None}))
                or (asset["evidence_type"] == "HUMAN_SUPPLIED" and (right["status"] != "GRANTED"
                    or not is_ref(right["ref"], "rights") or not is_hash(right["sha256"])))):
            raise ClaimError("INVALID_LOCAL_SCOPE")
    for items, prefix in ((scope.used_nonces, "nonce"), (scope.used_requests, "request"), (scope.used_targets, None)):
        if (type(items) is not tuple or len(items) > 64 or len(set(items)) != len(items)
                or not all(is_hash(v) if prefix is None else is_ref(v, prefix) for v in items)):
            raise ClaimError("INVALID_PIN")
    return directive, native


def decide(request: object, *, at: int, scope: LocalScope | None = None) -> Decision:
    """No callback, external context, grant booleans, signer injection or effects."""
    try:
        data = decode_json(request)
        if type(data) is not dict:
            return Decision("REJECT", "REJECT_STRUCTURE")
        if data.get("domain") not in ("LOCAL_UNPAID", "EXTERNAL_EFFECT"):
            return Decision("REJECT", "REJECT_UNKNOWN_DOMAIN")
        action = data.get("action")
        if type(action) is not str or action not in (*LOCAL_ACTIONS, *EXTERNAL_ACTIONS):
            return Decision("REJECT", "REJECT_UNKNOWN_ACTION")
        # Still attempt strict validation of recognized external requests, but
        # malformed/forged grants can NEVER change the fixed external HOLD.
        try:
            draft = validate_intent(data, at=at)
        except ClaimError:
            if action in EXTERNAL_ACTIONS or data["domain"] == "EXTERNAL_EFFECT":
                return _hold("HOLD_PRIVATE_VERIFIER_NOT_ENROLLED")
            return Decision("REJECT", "REJECT_STRUCTURE")
        if action in EXTERNAL_ACTIONS or data["domain"] == "EXTERNAL_EFFECT":
            return _hold("HOLD_PRIVATE_VERIFIER_NOT_ENROLLED")
        data = draft.to_dict()
        if type(scope) is not LocalScope:
            return _hold("HOLD_NATIVE_SCOPE")
        directive, native = _scope_data(scope)
        if (type(scope.marker_status) is not str or scope.marker_status != "COHERENT_LOCAL_ONLY"
                or type(scope.state) is not str or scope.state not in
                    ("INPUT_VALIDATED", "RUNNING", "TECHNICAL_QA", "WAIT_BRAIN")):
            return _hold("HOLD_RECOVERY")
        if any(data[k] != native[k] for k in ("source_sha", "journey_id", "directive_sha256", "directive_version")):
            return _hold("HOLD_SCOPE_MISMATCH")
        asset = next((a for a in directive["assets"] if a["sha256"] == data["approved_asset_sha256"]), None)
        if asset is None:
            return _hold("HOLD_SCOPE_MISMATCH")
        right = data["rights"]
        fixture = asset["evidence_type"] == "SYNTHETIC_FIXTURE"
        expected_ref = "rights:" + asset["sha256"] if fixture else asset["rights"]["ref"]
        expected_hash = sha256(canonical_bytes(asset["rights"])).hexdigest() if fixture else asset["rights"]["sha256"]
        if (right["status"] != ("SYNTHETIC_FIXTURE" if fixture else "LOCAL_PERMISSION")
                or right["ref"] != expected_ref or right["sha256"] != expected_hash
                or at >= right["expires_at"] or at >= data["provider_entitlement"]["expires_at"]):
            return _hold("HOLD_LOCAL_RIGHTS")
        if (data["nonce"] in scope.used_nonces or data["idempotency_key"] in scope.used_requests
                or target_digest(data) in scope.used_targets):
            return _hold("HOLD_REPLAY")
        # B0.3 has no PLAN_OFFLINE action. Its local A06 render is a run from
        # INPUT_VALIDATED; exports/handoff must retain native WAIT_BRAIN QA.
        if (action == "PLAN_OFFLINE"
                or (action == "RENDER_LOCAL_OFFLINE" and scope.state != "INPUT_VALIDATED")
                or (action in ("EXPORT_DRAFT", "HANDOFF") and scope.state != "WAIT_BRAIN")):
            return _hold("HOLD_NATIVE_ACTION_NOT_ALLOWED")
        return Decision("ALLOW_LOCAL", "LOCAL_MODEL_ONLY_RECHECK_NATIVE_GATES")
    except (ClaimError, ContractError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return Decision("REJECT", "REJECT_STRUCTURE")
