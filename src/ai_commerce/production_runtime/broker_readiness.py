"""B0-S.3b deterministic fail-closed broker readiness and isolation gate model.

No IO, network, signer, cryptographic nonce generator, cloud IAM/KMS client,
environment read, filesystem write, local service or runtime worker wiring.
All simulated isolation/key/Human/provider claims are forced to UNPROVEN.
External effects remain unconditionally HOLD_PRIVATE_VERIFIER_NOT_ENROLLED.
"""
from __future__ import annotations

from dataclasses import dataclass

from .custody_gate import (
    ENROLLMENT_STATES,
    REASONS as S3A_REASONS,
    decide as decide_custody,
)
from .security_policy import decide as decide_policy
from .broker_intake import (
    ACTIONS,
    CLAIMED_STATUS_KEYS,
    EFFECT_OBSERVATIONS,
    KINDS,
    RECEIPT_SCOPE_FIELDS,
    IntakeError,
    closed,
    decode_json,
    is_hash,
    is_ref,
    logical_time,
    validate_broker_draft,
)

STATUSES = (
    "DESIGN_READY_FOR_HUMAN_SELECTION",
    "HOLD",
    "REJECT",
)
FORBIDDEN_POSITIVE_STATUSES = (
    "AUTHORIZED",
    "GRANT_ACCEPTED",
    "ENROLLED",
    "ALLOW_EXTERNAL",
    "VALIDATED_CUSTODY",
    "LIVE_VERIFIED",
    "ISSUER_ENROLLED",
)
EVIDENCE_TIERS = (
    "DESIGNED",
    "SIMULATED_ONLY",
    "UNPROVEN",
)
FIXED_CLAIM_ASSESSMENTS = tuple((k, "UNPROVEN") for k in CLAIMED_STATUS_KEYS)
PREVIEW_FIELDS = (
    "source_sha",
    "selection_ref",
    "architecture_pattern",
    "vendor_neutral_slot",
    "credential_custody_mode",
    "human_approval_mode",
    "replay_storage_mode",
    "budget_reservation_mode",
    "deploy_admin_separation",
    "revoke_kill_sla_seconds",
)
PREVIEW_HASH = "0" * 64
MAX_ACCEPTABLE_KILL_SLA_SECONDS = 300
ALLOWED_OFF_HOST_PATTERNS = (
    "OFF_HOST_ISOLATED_BROKER",
    "CLOUD_WORKER_DURABLE_STATE",
    "CLOUD_MANAGED_COMPUTE_VAULT",
    "SEPARATE_DEVICE_BROKER",
)
REASONS = (
    "INERT_DESIGN_READY_HUMAN_SELECTION_REQUIRED",
    "REJECT_STRUCTURE",
    "REJECT_TIME",
    "REJECT_CROSS_AUDIENCE_OR_ROLE",
    "REJECT_SECRET_OR_PATH_LEAK",
    "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED",
    "HOLD_VENDOR_PRE_SELECTED",
    "HOLD_UNSAFE_ARCHITECTURE_PATTERN",
    "HOLD_SAME_SID_ACCESS",
    "HOLD_CLOUD_DEPLOY_ADMIN_OVERLAP",
    "HOLD_LONG_LIVED_BEARER_OR_KEY_ONLY",
    "HOLD_UNTRUSTED_HUMAN_DISPLAY",
    "HOLD_SELF_CERTIFIED_OR_UNPROVEN_AUDITOR",
    "HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN",
    "HOLD_BUDGET_RESERVATION_UNPROVEN",
    "HOLD_REVOKE_LATENCY_OR_EPOCH",
    "HOLD_ROLLBACK_OR_SCOPE_TAMPER",
    "HOLD_UNKNOWN_EFFECT_RETAINED",
)
STATE_FIELDS = (
    "schema_version",
    "request_mode",
    "enrollment_state",
    "incident_kill",
    "rollback_detected",
    "replay_store_state",
    "revoke_latency_proven",
    "minimum_epoch",
    "used_nonces",
    "used_requests",
    "used_targets",
    "effect_status",
    "original_idempotency_key",
    "reservation_ref",
    "expected_receipt",
)


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Finite redacted readiness report; never grants permission or custody."""

    status: str
    reason: str
    external_effect_decision: str = "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED"
    custody_gate_reason: str = "ISSUER_NOT_ENROLLED"
    evidence_tier: str = "UNPROVEN"
    claim_assessments: tuple[tuple[str, str], ...] = FIXED_CLAIM_ASSESSMENTS
    preview_fields: tuple[str, ...] = ()
    preview_sha256: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.status) is not str
            or type(self.reason) is not str
            or type(self.external_effect_decision) is not str
            or type(self.custody_gate_reason) is not str
            or type(self.evidence_tier) is not str
            or type(self.claim_assessments) is not tuple
            or type(self.preview_fields) is not tuple
        ):
            raise ValueError("INVALID_READINESS_REPORT")
        if (
            self.status in FORBIDDEN_POSITIVE_STATUSES
            or self.status not in STATUSES
            or self.reason not in REASONS
            or self.external_effect_decision != "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED"
            or self.custody_gate_reason not in S3A_REASONS
            or self.evidence_tier not in EVIDENCE_TIERS
            or self.claim_assessments != FIXED_CLAIM_ASSESSMENTS
        ):
            raise ValueError("INVALID_READINESS_REPORT")
        if self.status == "DESIGN_READY_FOR_HUMAN_SELECTION":
            valid = (
                self.reason == REASONS[0]
                and self.evidence_tier in ("DESIGNED", "SIMULATED_ONLY")
                and self.preview_fields == PREVIEW_FIELDS
                and type(self.preview_sha256) is str
                and self.preview_sha256 == PREVIEW_HASH
            )
        else:
            valid = (
                self.preview_fields == ()
                and self.preview_sha256 is None
                and (
                    (self.status == "REJECT" and self.reason.startswith("REJECT_"))
                    or (self.status == "HOLD" and self.reason.startswith("HOLD_"))
                )
            )
        if not valid:
            raise ValueError("INVALID_READINESS_REPORT")


def _hold(reason: str, *, custody_reason: str = "ISSUER_NOT_ENROLLED") -> ReadinessReport:
    return ReadinessReport("HOLD", reason, custody_gate_reason=custody_reason)


def _reject_from_intake(error: IntakeError) -> ReadinessReport:
    code = str(error)
    if code in ("INVALID_BROKER_AUDIENCE", "INVALID_BROKER_ROLE"):
        return ReadinessReport("REJECT", "REJECT_CROSS_AUDIENCE_OR_ROLE")
    if code == "SECRET_OR_PATH_FORBIDDEN":
        return ReadinessReport("REJECT", "REJECT_SECRET_OR_PATH_LEAK")
    return ReadinessReport("REJECT", "REJECT_STRUCTURE")


def _require_state(ok: bool) -> None:
    if not ok:
        raise IntakeError("CLOSED_FIELDS")


def _parse_state(value: object) -> dict | None:
    if value is None:
        return None
    data = decode_json(value)
    closed(data, STATE_FIELDS)
    _require_state(data["schema_version"] == "BrokerReadinessState.v1")
    _require_state(
        type(data["request_mode"]) is str
        and data["request_mode"] in ("DESIGN_READINESS_ONLY", "LIVE_EFFECT_OR_ENROLLMENT")
    )
    _require_state(
        type(data["enrollment_state"]) is str
        and data["enrollment_state"] in ENROLLMENT_STATES
    )
    _require_state(
        type(data["incident_kill"]) is bool
        and type(data["rollback_detected"]) is bool
        and type(data["revoke_latency_proven"]) is bool
    )
    _require_state(
        type(data["replay_store_state"]) is str
        and data["replay_store_state"] in ("DURABLE_CLAIMED", "ABSENT", "ROLLBACK", "UNKNOWN")
    )
    _require_state(
        type(data["minimum_epoch"]) is int and 1 <= data["minimum_epoch"] <= 2**31 - 1
    )
    for key, prefix in (
        ("used_nonces", "nonce"),
        ("used_requests", "request"),
        ("used_targets", None),
    ):
        items = data[key]
        _require_state(type(items) is list and len(items) <= 64)
        _require_state(
            all(is_hash(v) if prefix is None else is_ref(v, prefix) for v in items)
        )
        _require_state(len(set(items)) == len(items))
    _require_state(
        type(data["effect_status"]) is str and data["effect_status"] in EFFECT_OBSERVATIONS
    )
    _require_state(
        data["original_idempotency_key"] is None
        or is_ref(data["original_idempotency_key"], "request")
    )
    _require_state(
        data["reservation_ref"] is None
        or is_ref(data["reservation_ref"], "reservation")
    )
    if data["effect_status"] != "NONE":
        _require_state(
            data["original_idempotency_key"] is not None
            and data["reservation_ref"] is not None
        )
    if data["expected_receipt"] is not None:
        parsed_expected = validate_broker_draft(data["expected_receipt"]).to_dict()
        _require_state(parsed_expected["schema_version"] == KINDS[2])
        data["expected_receipt"] = parsed_expected
    return data


def _check_selection_gates(sel: dict, *, at: int) -> ReadinessReport | None:
    if not (sel["issued_at"] <= at < sel["expires_at"]):
        return _hold("HOLD_ROLLBACK_OR_SCOPE_TAMPER")
    if sel["vendor_neutral_slot"] != "UNSELECTED_HUMAN_DECISION_REQUIRED":
        return _hold("HOLD_VENDOR_PRE_SELECTED")
    if sel["architecture_pattern"] not in ALLOWED_OFF_HOST_PATTERNS:
        return _hold("HOLD_UNSAFE_ARCHITECTURE_PATTERN")
    if (
        sel["bearer_returned_to_caller"] is True
        or sel["credential_custody_mode"] != "BROKER_HOLDS_PROVIDER_CREDENTIALS"
    ):
        return _hold("HOLD_LONG_LIVED_BEARER_OR_KEY_ONLY")
    if sel["deploy_admin_separation"] != "SEPARATE_ADMIN_AND_AUDITOR":
        return _hold("HOLD_CLOUD_DEPLOY_ADMIN_OVERLAP")
    if sel["human_approval_mode"] != "INDEPENDENT_WYSIWYS_DEVICE":
        return _hold("HOLD_UNTRUSTED_HUMAN_DISPLAY")
    if sel["replay_storage_mode"] != "DURABLE_ATOMIC_OUTSIDE_SID":
        return _hold("HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN")
    if sel["budget_reservation_mode"] != "ATOMIC_PRE_CALL_RESERVATION":
        return _hold("HOLD_BUDGET_RESERVATION_UNPROVEN")
    if not (1 <= sel["revoke_kill_sla_seconds"] <= MAX_ACCEPTABLE_KILL_SLA_SECONDS):
        return _hold("HOLD_REVOKE_LATENCY_OR_EPOCH")
    return None


def preview_selection(selection: object, *, at: int) -> ReadinessReport:
    """Inert design preview only; emits fixed field names and zero sentinel."""
    if not logical_time(at):
        return ReadinessReport("REJECT", "REJECT_TIME")
    try:
        sel = validate_broker_draft(selection).to_dict()
    except IntakeError as error:
        return _reject_from_intake(error)
    if sel["schema_version"] != KINDS[0]:
        return ReadinessReport("REJECT", "REJECT_STRUCTURE")
    denied = _check_selection_gates(sel, at=at)
    if denied is not None:
        return denied
    return ReadinessReport(
        "DESIGN_READY_FOR_HUMAN_SELECTION",
        REASONS[0],
        evidence_tier="DESIGNED",
        preview_fields=PREVIEW_FIELDS,
        preview_sha256=PREVIEW_HASH,
    )


def evaluate_readiness(
    selection: object,
    *,
    at: int,
    isolation: object = None,
    receipt: object = None,
    state: object = None,
) -> ReadinessReport:
    """Evaluate untrusted broker selection, isolation and receipt claims fail-closed."""
    if not logical_time(at):
        return ReadinessReport("REJECT", "REJECT_TIME")
    try:
        sel = validate_broker_draft(selection).to_dict()
        iso = None if isolation is None else validate_broker_draft(isolation).to_dict()
        rec = None if receipt is None else validate_broker_draft(receipt).to_dict()
        ctx = _parse_state(state)
    except IntakeError as error:
        return _reject_from_intake(error)
    if (
        sel["schema_version"] != KINDS[0]
        or (iso is not None and iso["schema_version"] != KINDS[1])
        or (rec is not None and rec["schema_version"] != KINDS[2])
    ):
        return ReadinessReport("REJECT", "REJECT_STRUCTURE")

    action_probe = rec["action"] if rec is not None else ACTIONS[0]
    s2_decision = decide_policy(
        {"domain": "EXTERNAL_EFFECT", "action": action_probe},
        at=at,
    )
    if s2_decision.reason != "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED":
        return _hold("HOLD_PRIVATE_VERIFIER_NOT_ENROLLED")

    if (ctx is not None and ctx["effect_status"] != "NONE") or (
        rec is not None and rec["effect_observation"] != "NONE"
    ):
        return _hold("HOLD_UNKNOWN_EFFECT_RETAINED", custody_reason="UNKNOWN_EFFECT")
    if ctx is not None and (
        ctx["incident_kill"] is True or ctx["enrollment_state"] == "REVOKED"
    ):
        return _hold("HOLD_REVOKE_LATENCY_OR_EPOCH", custody_reason="REVOKED")
    if ctx is not None and (
        ctx["rollback_detected"] is True or ctx["replay_store_state"] == "ROLLBACK"
    ):
        return _hold("HOLD_ROLLBACK_OR_SCOPE_TAMPER", custody_reason="REPLAY_STORE_UNPROVEN")
    if ctx is not None and ctx["enrollment_state"] == "EXPIRED":
        return _hold("HOLD_ROLLBACK_OR_SCOPE_TAMPER", custody_reason="EXPIRED")

    denied = _check_selection_gates(sel, at=at)
    if denied is not None:
        return denied

    if iso is None:
        return _hold("HOLD_SELF_CERTIFIED_OR_UNPROVEN_AUDITOR")
    if not (iso["issued_at"] <= at < iso["expires_at"]):
        return _hold("HOLD_ROLLBACK_OR_SCOPE_TAMPER", custody_reason="EXPIRED")
    if any(
        iso[k] != sel[k]
        for k in (
            "selection_ref",
            "issuer_ref",
            "public_key_id",
            "source_sha",
            "revocation_epoch",
        )
    ):
        return _hold("HOLD_ROLLBACK_OR_SCOPE_TAMPER", custody_reason="SCOPE_MISMATCH")
    if iso["secret_binding_mode"] != "FULL_POLICY_MEDIATED_BY_BROKER":
        return _hold("HOLD_LONG_LIVED_BEARER_OR_KEY_ONLY")
    if iso["same_sid_access"] != "DENIED_CLAIMED":
        return _hold("HOLD_SAME_SID_ACCESS", custody_reason="NO_INDEPENDENT_CUSTODY")
    if iso["cloud_admin_overlap"] != "NONE_CLAIMED":
        return _hold("HOLD_CLOUD_DEPLOY_ADMIN_OVERLAP", custody_reason="NO_INDEPENDENT_CUSTODY")
    if iso["wysiwys_display_mode"] != "INDEPENDENT_TRUSTED_DEVICE_DISPLAY":
        return _hold("HOLD_UNTRUSTED_HUMAN_DISPLAY", custody_reason="CHALLENGE_UNPROVEN")
    if (
        iso["root_enrollment_mode"] != "OUT_OF_BAND_HUMAN_CEREMONY"
        or iso["auditor_independence"] != "OUT_OF_BAND_INDEPENDENT_CLAIMED"
    ):
        return _hold("HOLD_SELF_CERTIFIED_OR_UNPROVEN_AUDITOR")

    if rec is not None:
        if not (rec["issued_at"] <= at < rec["expires_at"]):
            return _hold("HOLD_ROLLBACK_OR_SCOPE_TAMPER", custody_reason="EXPIRED")
        if any(
            rec[k] != sel[k]
            for k in ("issuer_ref", "public_key_id", "source_sha", "revocation_epoch")
        ):
            return _hold("HOLD_ROLLBACK_OR_SCOPE_TAMPER", custody_reason="SCOPE_MISMATCH")
        if rec["display_sha256"] != rec["transaction_sha256"]:
            return _hold("HOLD_UNTRUSTED_HUMAN_DISPLAY", custody_reason="CHALLENGE_UNPROVEN")

    if ctx is not None:
        for draft in (sel, iso, *(() if rec is None else (rec,))):
            if draft["revocation_epoch"] < ctx["minimum_epoch"]:
                return _hold(
                    "HOLD_REVOKE_LATENCY_OR_EPOCH",
                    custody_reason="REVOCATION_EPOCH_ROLLBACK",
                )
            if draft["nonce"] in ctx["used_nonces"]:
                return _hold(
                    "HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN",
                    custody_reason="REPLAY_STORE_UNPROVEN",
                )
        if ctx["replay_store_state"] != "DURABLE_CLAIMED":
            return _hold(
                "HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN",
                custody_reason="REPLAY_STORE_UNPROVEN",
            )
        if ctx["revoke_latency_proven"] is not True:
            return _hold("HOLD_REVOKE_LATENCY_OR_EPOCH")
        if rec is not None:
            if (
                rec["idempotency_key"] in ctx["used_requests"]
                or rec["semantic_target_sha256"] in ctx["used_targets"]
            ):
                return _hold(
                    "HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN",
                    custody_reason="REPLAY_STORE_UNPROVEN",
                )
            expected = ctx["expected_receipt"]
            if expected is not None and any(
                rec[k] != expected[k] for k in RECEIPT_SCOPE_FIELDS
            ):
                return _hold(
                    "HOLD_ROLLBACK_OR_SCOPE_TAMPER",
                    custody_reason="SCOPE_MISMATCH",
                )
        if ctx["request_mode"] == "LIVE_EFFECT_OR_ENROLLMENT":
            return _hold("HOLD_PRIVATE_VERIFIER_NOT_ENROLLED")

    return ReadinessReport(
        "DESIGN_READY_FOR_HUMAN_SELECTION",
        REASONS[0],
        external_effect_decision="HOLD_PRIVATE_VERIFIER_NOT_ENROLLED",
        custody_gate_reason="ISSUER_NOT_ENROLLED",
        evidence_tier="SIMULATED_ONLY",
        claim_assessments=FIXED_CLAIM_ASSESSMENTS,
        preview_fields=PREVIEW_FIELDS,
        preview_sha256=PREVIEW_HASH,
    )


def decide_external_effect(
    selection: object,
    *,
    at: int,
    isolation: object = None,
    receipt: object = None,
    state: object = None,
    s3a_transaction: object = None,
    s3a_evidence: object = None,
) -> ReadinessReport:
    """Compose B0-S2, B0-S3a, and B0-S3b gates; external effect is NEVER permitted."""
    report = evaluate_readiness(
        selection,
        at=at,
        isolation=isolation,
        receipt=receipt,
        state=state,
    )
    if report.status in ("HOLD", "REJECT"):
        return report
    custody_reason = "ISSUER_NOT_ENROLLED"
    if s3a_transaction is not None:
        s3a_result = decide_custody(s3a_transaction, at=at, evidence=s3a_evidence)
        if s3a_result.outcome == "REJECT":
            return ReadinessReport("REJECT", "REJECT_STRUCTURE")
        custody_reason = s3a_result.reason
    return _hold("HOLD_PRIVATE_VERIFIER_NOT_ENROLLED", custody_reason=custody_reason)
