"""Independent evidence REQUEST only; this cut never admits Human data.

Untrusted submissions are discarded without serialization, hashing, echoing,
logging, persistence or callback execution. Closed reference metadata describes
requests, never authenticated receipts. Future observation requires a separately
versioned authorized verifier with independent custody and trusted time.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from ai_commerce.creative_quality import blind_trials as bt, study_protocol as sp

KINDS = ("CONSENT", "EXPOSURE_RIGHTS", "SESSION_EXPOSURE", "RESPONSE_WITHDRAWAL", "VERIFIER_CUSTODY")
STATES = ("UNVERIFIED", "MISSING", "REVOKED", "EXPIRED", "REPLAY", "UNSIGNED",
          "INVALID_SCOPE", "SYNTHETIC", "CLAIMED_WITHDRAWAL", "UNKNOWN_EFFECT")
REQUIREMENTS = (
    "PARTICIPANT_SPECIFIC_INFORMED_CONSENT_SEPARATE_AUTHORIZED_SERVICE_BEFORE_EXPOSURE_PRIVACY_VERSION_REVOCATION",
    "EXACT_ASSET_RIGHTS_LIKENESS_VOICE_AND_MEDIA_EXPOSURE_AUTHORIZATION",
    "INDEPENDENT_PSEUDONYM_SESSION_ASSIGNMENT_ACTUAL_EXPOSURE_TIME_TASK_PRIVATE_MAPPING",
    "VALIDATED_RESPONSE_WITHDRAWAL_PROTOCOL_ASSET_SHA_BLINDED_TASK_DIGEST_NONCE_EXPIRY",
    "INDEPENDENT_VERIFIER_CUSTODY_PUBLIC_KEY_CHAIN_REVOCATION_TRUSTED_VERIFICATION_TIME")


def _metadata(references, draft):
    """Check types/closed tokens BEFORE canonicalizing; never retain bad values."""
    if type(references) is not tuple or len(references) > len(KINDS):
        return [], ["INVALID_REFERENCE_METADATA"], "UNKNOWN"
    safe, reasons, kinds, ids = [], [], set(), set()
    assets = draft.to_dict()["expected_asset_sha256s"]
    tasks = {t["blinded_task_digest"]: t["expected_asset_sha256s"]
             for t in draft.to_dict()["blinded_task_bindings"]}
    for row in references:
        if (type(row) is not dict or len(row) != 6 or any(type(k) is not str for k in row)
                or set(row) != {
                "kind", "ref_id", "protocol_digest", "asset_sha256", "blinded_task_digest", "state"}):
            return [], ["INVALID_REFERENCE_METADATA"], "UNKNOWN"
        if any(type(v) is not str for v in row.values()):
            return [], ["INVALID_REFERENCE_METADATA"], "UNKNOWN"
        if (row["kind"] not in KINDS or row["state"] not in STATES
                or re.fullmatch(r"request_[0-9a-f]{32}", row["ref_id"]) is None
                or any(re.fullmatch(r"[0-9a-f]{64}", row[k]) is None for k in
                       ("protocol_digest", "asset_sha256", "blinded_task_digest"))):
            return [], ["INVALID_REFERENCE_METADATA"], "UNKNOWN"
        if row["kind"] in kinds or row["ref_id"] in ids:
            reasons.append("DUPLICATE_REFERENCE")
        kinds.add(row["kind"])
        ids.add(row["ref_id"])
        if row["protocol_digest"] != draft.digest:
            reasons.append("WRONG_PROTOCOL_REFERENCE")
        if row["asset_sha256"] not in assets:
            reasons.append("ASSET_SHA_MISMATCH")
        if row["blinded_task_digest"] not in tasks:
            reasons.append("BLINDED_TASK_DIGEST_MISMATCH")
        elif row["asset_sha256"] not in tasks[row["blinded_task_digest"]]:
            reasons.append("TASK_ASSET_MISMATCH")
        reasons.append(row["state"] + "_REFERENCE")
        safe.append(row.copy())
    for kind in KINDS:
        if kind not in kinds:
            reasons.append("MISSING_" + kind + "_REFERENCE")
    unknown = any(row["state"] == "UNKNOWN_EFFECT" for row in safe)
    if unknown:
        reasons.append("UNKNOWN_EFFECT_NO_RETRY")
    return sorted(safe, key=lambda x: (x["kind"], x["ref_id"])), reasons, "UNKNOWN" if unknown else "NOT_ATTEMPTED"


def _payload(draft, references, rejected, invalid_metadata=False):
    safe, reasons, effect = _metadata(references, draft)
    if "INVALID_REFERENCE_METADATA" in reasons:
        invalid_metadata = True
    if invalid_metadata:
        safe, reasons, effect = _metadata((), draft)
        reasons.append("INVALID_REFERENCE_METADATA")
        rejected = True
    if rejected:
        reasons.append("RAW_SUBMISSION_OR_AUTHORITY_OVERRIDE_NOT_ADMITTED")
        effect = "UNKNOWN"
    if effect == "UNKNOWN":
        reasons.append("UNKNOWN_EFFECT_NO_RETRY")
    if draft.to_dict()["source_status"] == "NO_BOUND_SOURCE":
        reasons.append("NO_BOUND_SOURCE")
    return {"schema_version": BQ03bHumanAttestationRequest.SCHEMA,
        "status": "HOLD_UNVERIFIED", "admission": "NOT_ADMITTED",
        "evidence_state": "UNVERIFIED", "protocol_digest": draft.digest,
        "source_plan_digest": draft.to_dict()["source_plan_digest"],
        "reference_metadata": safe, "submission_handling": "REJECTED_WITHOUT_RETENTION" if rejected else "NOT_SUPPLIED",
        "reference_handling": "INVALID_METADATA_DISCARDED" if invalid_metadata else "CLOSED_REQUEST_METADATA_ONLY",
        "reason_codes": sorted(set(reasons + ["INDEPENDENT_AUTHENTICATED_VERIFIER_NOT_CONNECTED",
            "PARTICIPANT_CONSENT_NOT_AUTHENTICATED", "EXPOSURE_RIGHTS_NOT_AUTHENTICATED",
            "BLINDED_TASK_ASSIGNMENT_NOT_AUTHENTICATED"])),
        "required_external_receipts": list(REQUIREMENTS),
        "human_observation": "AUTHENTIC_HUMAN_DATA_NOT_AVAILABLE", "observed_human_count": 0,
        "consent": "NOT_ESTABLISHED", "rights": "UNKNOWN", "withdrawal": "NOT_VALIDATED",
        "trusted_verification_time": "NOT_OBSERVED", "external_effect": effect,
        "retry": "NO_AUTOMATIC_RETRY", "live_intake": "NOT_IMPLEMENTED",
        "future_admission": "SEPARATELY_VERSIONED_AUTHENTICATED_VERIFIER_ONLY",
        "authority": "REQUEST_ONLY_NO_SIGNER_NO_HUMAN_VERDICT"}


@dataclass(frozen=True, slots=True, init=False)
class BQ03bHumanAttestationRequest(bt._Record):
    SCHEMA = "BQ03bHumanAttestationRequest.v1"

    @classmethod
    def from_dict(cls, value, *, draft, draft_digest, plan, plan_digest, **source):
        draft = sp.validate_protocol(draft, plan=plan, plan_digest=plan_digest,
                                     draft_digest=bt.sha(draft_digest), **source)
        bt.fields(value, " ".join(_payload(draft, (), False)))
        rows = value["reference_metadata"]
        if type(rows) is not list:
            raise bt.Error("closed request metadata required")
        handling = bt.option(value["submission_handling"], ("REJECTED_WITHOUT_RETENTION", "NOT_SUPPLIED"))
        invalid = bt.option(value["reference_handling"],
                            ("INVALID_METADATA_DISCARDED", "CLOSED_REQUEST_METADATA_ONLY"))
        expected = _payload(draft, tuple(rows), handling == "REJECTED_WITHOUT_RETENTION",
                            invalid == "INVALID_METADATA_DISCARDED")
        if bt.canonical(value) != bt.canonical(expected):
            raise bt.Error("attestation request differs from closed fail-closed replay")
        return bt.bind(cls, expected)

    @classmethod
    def from_json(cls, raw, **kwargs):
        return cls.from_dict(bt.read(raw), **kwargs)


def request_attestation(draft, *, draft_digest, plan, plan_digest, references=(), submission=None, **unsupported):
    # Source kwargs must be explicit so an allow_true or fake verifier never
    # becomes a trusted dependency. No callbacks are accepted or invoked.
    source = {k: unsupported.pop(k) for k in ("linked_review", "linked_review_digest") if k in unsupported}
    draft = sp.validate_protocol(draft, plan=plan, plan_digest=plan_digest,
                                 draft_digest=bt.sha(draft_digest), **source)
    rejected = submission is not None or bool(unsupported)
    payload = _payload(draft, references, rejected)
    return bt.bind(BQ03bHumanAttestationRequest, payload)


def validate_request(request, *, attestation_digest, **bindings):
    if type(request) is not BQ03bHumanAttestationRequest:
        raise bt.Error("exact native NOT_ADMITTED request required")
    request.verify_digest(attestation_digest)
    return BQ03bHumanAttestationRequest.from_json(request.to_json(), **bindings)
