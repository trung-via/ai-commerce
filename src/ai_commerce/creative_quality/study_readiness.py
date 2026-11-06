"""Offline readiness checklist; no Human data, automatic gate or live effects."""

from __future__ import annotations

from dataclasses import dataclass

from ai_commerce.creative_quality import blind_trials as bt, study_protocol as sp, human_attestation as ha

CHECKLIST = ("PARTICIPANT_CONSENT_BEFORE_EXPOSURE", "ASSET_EXPOSURE_RIGHTS",
    "INDEPENDENT_VERIFIER_SIGNER_CUSTODY", "WITHDRAWAL_PATH", "RETENTION_DELETION_ACCESS",
    "EXPOSURE_PLATFORM_BLINDING", "PREREGISTERED_SAMPLE_POWER", "SAFETY_REVIEW",
    "REVOCATION_AND_EXPIRY", "PRIVACY_MINIMIZED_AUDIT_LOGS")


def _payload(plan, draft, request):
    source = None if plan is None else plan.to_dict()
    reasons = ["AUTHENTIC_HUMAN_DATA_NOT_AVAILABLE", "PREREGISTERED_SAMPLE_POWER_PENDING",
               "CONSENT_RIGHTS_AND_INDEPENDENT_VERIFIER_ABSENT"]
    if plan is None:
        reasons.append("NO_BOUND_SOURCE")
    if request is None:
        reasons.append("ATTESTATION_REQUEST_MISSING")
    else:
        reasons.extend(request.to_dict()["reason_codes"])
    return {"schema_version": BQ03bStudyReadiness.SCHEMA,
        "status": "NOT_READY_FOR_LIVE_STUDY", "decision": "HOLD_PENDING_AUTHENTIC_GRANTS",
        "human_data": "AUTHENTIC_HUMAN_DATA_NOT_AVAILABLE",
        "source_plan_digest": None if plan is None else plan.digest,
        "protocol_digest": draft.digest, "attestation_request_digest": None if request is None else request.digest,
        "expected_asset_sha256s": draft.to_dict()["expected_asset_sha256s"],
        "checklist_status": "CHECKLIST_ONLY_PENDING_INDEPENDENT_PROOF",
        "checklist": [{"requirement": k, "status": "PENDING_AUTHENTIC_EXTERNAL_PROOF"} for k in CHECKLIST],
        "reason_codes": sorted(set(reasons)), "observed_human_count": 0,
        "participant_consent_count": 0, "rights_attestation_count": 0,
        "human_calibration": "NOT_RUN", "media_inspection": "NOT_RUN",
        "source_link": None if source is None else source["linked_bq02b"],
        "source_judgments": "SYNTHETIC_ONLY_NEVER_UPGRADED",
        "q2_p0": "UNCHANGED_SOURCE_AUTHORITY", "q4_semantic": "UNKNOWN_PRESERVED",
        "t3_status": "NOT_RUN", "quality_gate": "NO_AUTOMATIC_APPROVAL_OR_VETO",
        "brain_recommendation": "REVIEW_OFFLINE_DESIGN_AND_SEPARATE_FUTURE_AUTHORIZATION",
        "uncertainty": ["NO_ACTUAL_PARTICIPANTS_OR_EXPOSURE", "RATER_ASSET_CLUSTERING",
            "SELECTION_POSITION_ORDER_AND_RATER_BIAS_UNMEASURED", "SAMPLE_POWER_UNAPPROVED",
            "PREFERENCE_IS_NOT_ROI_RIGHTS_OR_VIRAL"],
        "external_effects": "NONE_INVOKED", "authority": "BOUNDED_BRAIN_READINESS_SUGGESTION_ONLY"}


@dataclass(frozen=True, slots=True, init=False)
class BQ03bStudyReadiness(bt._Record):
    SCHEMA = "BQ03bStudyReadiness.v1"

    @classmethod
    def from_dict(cls, value, **bindings):
        expected = build_readiness(**bindings)
        if bt.canonical(value) != expected.to_json():
            raise bt.Error("readiness differs from exact native checklist replay")
        return expected

    @classmethod
    def from_json(cls, raw, **bindings):
        return cls.from_dict(bt.read(raw), **bindings)


def build_readiness(plan, draft, request=None, *, plan_digest, draft_digest,
                    attestation_digest=None, **source):
    native = sp.bound_plan(plan, plan_digest, **source)
    draft = sp.validate_protocol(draft, plan=native, plan_digest=plan_digest,
                                 draft_digest=bt.sha(draft_digest), **source)
    if request is None:
        if attestation_digest is not None:
            raise bt.Error("missing attestation cannot carry a detached pin")
    else:
        request = ha.validate_request(request, attestation_digest=attestation_digest,
            draft=draft, draft_digest=draft.digest, plan=native, plan_digest=plan_digest, **source)
    return bt.bind(BQ03bStudyReadiness, _payload(native, draft, request))
