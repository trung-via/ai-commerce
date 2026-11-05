"""Denied-by-default B0-S.3a source model; never a broker or live verifier.

No IO, callbacks, signer, key material, state writes or connection to a worker.
Even a coherent snapshot claiming every gate passed remains untrusted. There
is no positive external outcome, enrollment transition or accepted grant API.
"""
from __future__ import annotations

from dataclasses import dataclass

from .security_claims import ClaimError, closed, decode_json, is_hash, is_ref, logical_time
from .custody_claims import (
    AUDIENCES, ENROLLMENT_STATES, KINDS, SCOPE_FIELDS, validate_draft,
)

OUTCOMES = ("PREVIEW_INERT", "HOLD", "REJECT")
REASONS = (
    "PREVIEW_REDACTED_NO_AUTHORITY", "REJECT_STRUCTURE", "REJECT_DRAFT_KIND", "REJECT_TIME",
    "NO_INDEPENDENT_CUSTODY", "ISSUER_NOT_ENROLLED", "CHALLENGE_UNPROVEN", "EXPIRED",
    "REVOKED", "AUDIENCE_MISMATCH", "SCOPE_MISMATCH", "REPLAY_STORE_UNPROVEN",
    "UNKNOWN_EFFECT", "SECRET_BOUNDARY_UNPROVEN", "REVOCATION_EPOCH_ROLLBACK",
)
PREVIEW_FIELDS = ("source_sha", "journey_id", "directive_sha256", "final_media_sha256",
                  "provider_ref", "account_ref", "surface", "action", "currency", "spend_ceiling_minor")
PREVIEW_HASH = "0" * 64  # Fixed redaction sentinel; deliberately not the transaction digest.
EVIDENCE_FIELDS = (
    "schema_version", "enrollment_state", "custody_boundary", "secret_boundary", "replay_store",
    "minimum_epoch", "used_nonces", "used_requests", "used_targets", "effect_status",
    "original_idempotency_key", "reservation_ref", "expected_transaction", "challenge",
)


@dataclass(frozen=True, slots=True)
class CustodyResult:
    """Finite data-free denial, or fixed field names/sentinel for Human reading."""
    outcome: str
    reason: str
    preview_fields: tuple[str, ...] = ()
    preview_sha256: str | None = None

    def __post_init__(self):
        if (type(self.outcome) is not str or type(self.reason) is not str
                or type(self.preview_fields) is not tuple
                or not all(type(k) is str for k in self.preview_fields)):
            raise ValueError("INVALID_CUSTODY_RESULT")
        valid = self.outcome in OUTCOMES and self.reason in REASONS
        if self.outcome == "PREVIEW_INERT":
            valid = (valid and self.reason == REASONS[0] and self.preview_fields == PREVIEW_FIELDS
                     and type(self.preview_sha256) is str and self.preview_sha256 == PREVIEW_HASH)
        else:
            valid = (valid and self.preview_fields == () and self.preview_sha256 is None
                     and ((self.outcome == "REJECT" and self.reason.startswith("REJECT_"))
                          or (self.outcome == "HOLD" and self.reason in REASONS[4:])))
        if not valid:
            raise ValueError("INVALID_CUSTODY_RESULT")


def _hold(reason: str) -> CustodyResult:
    return CustodyResult("HOLD", reason)


def _require(ok: bool) -> None:
    if not ok:
        raise ClaimError("CLOSED_FIELDS")


def _evidence(value: object) -> dict | None:
    if value is None:
        return None
    data = decode_json(value)
    closed(data, EVIDENCE_FIELDS)
    _require(data["schema_version"] == "CustodyEvidenceSnapshot.v1")
    for key, choices in (
        ("enrollment_state", ENROLLMENT_STATES),
        ("custody_boundary", ("UNKNOWN", "OFF_HOST_CLAIM", "SAME_SID", "DPAPI_SAME_SID",
                              "CREDENTIAL_MANAGER_SAME_SID", "LOCAL_ACL", "TPM_ONLY", "PASSKEY_ONLY", "LOCAL_SERVICE")),
        ("secret_boundary", ("UNKNOWN", "CLAIMED")),
        ("replay_store", ("UNKNOWN", "CLAIMED", "ROLLBACK")),
        ("effect_status", ("NONE", "UNKNOWN_EFFECT", "CLAIMED_CONFIRMED", "CLAIMED_NO_EFFECT")),
    ):
        _require(type(data[key]) is str and data[key] in choices)
    _require(type(data["minimum_epoch"]) is int and 1 <= data["minimum_epoch"] <= 2**31 - 1)
    for key, prefix in (("used_nonces", "nonce"), ("used_requests", "request"), ("used_targets", None)):
        items = data[key]
        _require(type(items) is list and len(items) <= 64)
        _require(all(is_hash(v) if prefix is None else is_ref(v, prefix) for v in items))
        _require(len(set(items)) == len(items))
    _require(data["original_idempotency_key"] is None or is_ref(data["original_idempotency_key"], "request"))
    _require(data["reservation_ref"] is None or is_ref(data["reservation_ref"], "reservation"))
    if data["effect_status"] != "NONE":
        _require(data["original_idempotency_key"] is not None and data["reservation_ref"] is not None)
    for key, kind in (("expected_transaction", KINDS[3]), ("challenge", KINDS[1])):
        if data[key] is not None:
            data[key] = validate_draft(data[key]).to_dict()
            _require(data[key]["schema_version"] == kind)
    return data


def _transaction(request: object, at: object) -> tuple[dict | None, CustodyResult | None]:
    if not logical_time(at):
        return None, CustodyResult("REJECT", "REJECT_TIME")
    try:
        data = validate_draft(request).to_dict()
    except ClaimError:
        return None, CustodyResult("REJECT", "REJECT_STRUCTURE")
    if data["schema_version"] != KINDS[3]:
        return None, CustodyResult("REJECT", "REJECT_DRAFT_KIND")
    return data, None


def preview_transaction(request: object, *, at: int) -> CustodyResult:
    """Structural preview only. No values, real hashes, permissions or evidence."""
    data, error = _transaction(request, at)
    if error is not None:
        return error
    if at >= data["expires_at"]:
        return _hold("EXPIRED")
    if at < data["issued_at"]:
        return _hold("CHALLENGE_UNPROVEN")
    if data["policy_audience"] != AUDIENCES[0]:
        return _hold("AUDIENCE_MISMATCH")
    return CustodyResult("PREVIEW_INERT", REASONS[0], PREVIEW_FIELDS, PREVIEW_HASH)


def decide(request: object, *, at: int, evidence: object = None) -> CustodyResult:
    """Diagnostic denials only; snapshots/labels never establish live authority.

    No state is mutated. In particular UNKNOWN exposure retains its original
    idempotency key and reservation in the caller's snapshot; it cannot retry,
    rekey, release budget, confirm a provider result or rotate an enrolled key.
    """
    data, error = _transaction(request, at)
    if error is not None:
        return error
    try:
        context = _evidence(evidence)
    except ClaimError:
        return CustodyResult("REJECT", "REJECT_STRUCTURE")
    # A supplied confirmed/no-effect label is not an independent provider receipt.
    if context is not None and context["effect_status"] != "NONE":
        return _hold("UNKNOWN_EFFECT")
    if context is not None and context["enrollment_state"] == "REVOKED":
        return _hold("REVOKED")
    if at >= data["expires_at"] or (context is not None and context["enrollment_state"] == "EXPIRED"):
        return _hold("EXPIRED")
    if at < data["issued_at"]:
        return _hold("CHALLENGE_UNPROVEN")
    if data["policy_audience"] != AUDIENCES[0]:
        return _hold("AUDIENCE_MISMATCH")
    if context is None:
        return _hold("NO_INDEPENDENT_CUSTODY")
    if data["revocation_epoch"] < context["minimum_epoch"]:
        return _hold("REVOCATION_EPOCH_ROLLBACK")
    expected = context["expected_transaction"]
    if expected is not None and any(data[k] != expected[k] for k in SCOPE_FIELDS):
        return _hold("SCOPE_MISMATCH")
    if (data["nonce"] in context["used_nonces"]
            or data["idempotency_key"] in context["used_requests"]
            or data["semantic_target_sha256"] in context["used_targets"]):
        return _hold("REPLAY_STORE_UNPROVEN")
    if context["custody_boundary"] != "OFF_HOST_CLAIM":
        return _hold("NO_INDEPENDENT_CUSTODY")
    if context["secret_boundary"] != "CLAIMED":
        return _hold("SECRET_BOUNDARY_UNPROVEN")
    if context["enrollment_state"] != "ENROLLED":
        return _hold("ISSUER_NOT_ENROLLED")
    challenge = context["challenge"]
    if challenge is None or expected is None:
        return _hold("CHALLENGE_UNPROVEN")
    if challenge["policy_audience"] != data["policy_audience"]:
        return _hold("AUDIENCE_MISMATCH")
    if (not challenge["issued_at"] <= at < challenge["expires_at"]
            or challenge["issued_at"] < data["issued_at"]
            or challenge["expires_at"] > data["expires_at"]
            or any(challenge[k] != data[k] for k in
                   ("issuer_ref", "public_key_id", "source_sha", "nonce", "revocation_epoch",
                    "transaction_sha256", "idempotency_key"))
            or challenge["display_sha256"] != data["transaction_sha256"]):
        return _hold("CHALLENGE_UNPROVEN")
    if context["replay_store"] != "CLAIMED":
        return _hold("REPLAY_STORE_UNPROVEN")
    # Deliberately unconditional. All coherent positive labels are still claims.
    return _hold("ISSUER_NOT_ENROLLED")
