"""Private preregistration draft from BQ-03a; no participants or exposure.

Only public_preview() is suitable for public transport. A source merge cannot
grant participant consent, media rights, statistical validity or live study use.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from ai_commerce.creative_quality import blind_trials as bt

Error = bt.Error
PRIVACY_MODES = ("EXTERNAL_CUSTODY_PSEUDONYMS_ONLY", "AGGREGATES_ONLY_PENDING_REVIEW")
POPULATIONS = ("GENERAL_ADULT_VOLUNTEERS", "VIETNAMESE_LANGUAGE_ADULT_VOLUNTEERS")
CONSENT_CHECKLIST = ("PURPOSE", "INTENDED_USE", "VOLUNTARY_OPT_IN", "WITHDRAWAL",
                    "ACCESS", "RETENTION_DELETION", "COMPENSATION_TERMS")
STOP_CRITERIA = ("CONSENT_MISSING_OR_REVOKED", "RIGHTS_UNVERIFIED", "SAFETY_CONCERN",
                 "BLINDING_BREACH", "ASSET_OR_PROTOCOL_DRIFT", "VERIFIER_UNAVAILABLE",
                 "REPLAY_OR_STALE_EVIDENCE", "UNKNOWN_EFFECT_NO_RETRY")


def bound_plan(plan, plan_digest, **source):
    if plan is None:
        if plan_digest is not None or any(v is not None for v in source.values()):
            raise Error("NO_BOUND_SOURCE cannot carry detached pins")
        return None
    native = bt.validate_plan(plan, **source)
    native.verify_digest(plan_digest)
    return native


def _payload(plan, privacy_mode, population):
    bt.option(privacy_mode, PRIVACY_MODES)
    bt.option(population, POPULATIONS)
    config = None if plan is None else plan.to_dict()["config"]
    tasks = [] if plan is None else plan.to_dict()["assignments"]
    return {
        "schema_version": BQ03bStudyProtocolDraft.SCHEMA,
        "visibility": "PRIVATE_STUDY_DESIGN_REFERENCE_ONLY",
        "status": "HOLD_PENDING_AUTHENTIC_GRANTS",
        "source_status": "NO_BOUND_SOURCE" if plan is None else "NATIVE_BQ03A_BOUND",
        "source_plan_digest": None if plan is None else plan.digest,
        "expected_asset_sha256s": [] if config is None else config["asset_sha256s"],
        "blinded_task_bindings": [{
            "blinded_task_digest": hashlib.sha256(bt.canonical({"task_id": task["task_id"],
                "LEFT": "LEFT", "RIGHT": "RIGHT", "rubric": config["rubric"]}).encode("utf-8")).hexdigest(),
            "expected_asset_sha256s": sorted([task["LEFT"], task["RIGHT"]])} for task in tasks],
        "source_kind": "SYNTHETIC_DESIGN_NOT_OBSERVED",
        "study_question": "Which blinded alternative is preferred on the applicable preregistered rubric?",
        "primary_outcome": "CANONICAL_PAIR_PREFERENCE_WITH_TIE_CATEGORY",
        "secondary_outcomes": ["PAIR_DISAGREEMENT", "DESCRIPTIVE_POSITION_ASSOCIATION"],
        "rubric_version": "BQ03a.r6.v1",
        "rubric": [] if config is None else config["rubric"],
        "applicability": None if config is None else {
            k: config[k] for k in ("visual_mode", "audio_mode", "logo_mode", "product_ref")},
        "population_category": population,
        "inclusion_categories": ["ADULT_ELIGIBILITY_EXTERNAL_REVIEW", "VOLUNTARY_INFORMED_OPT_IN",
                                 "TASK_LANGUAGE_FAMILIARITY", "ACCESSIBILITY_REVIEW"],
        "exclusion_categories": ["DECLINED_OR_REVOKED_CONSENT", "INELIGIBLE_EXPOSURE",
                                 "DUPLICATE_SESSION", "UNVERIFIABLE_PROVENANCE"],
        "allocation": {
            "design": "BETWEEN_PERSON_COUNTERBALANCE_AB_BA",
            "intended_side_assignment": "SOURCE_HMAC_RANDOMIZED_PLAN_ONLY",
            "actual_assignment": "NOT_RUN_REQUIRES_INDEPENDENT_RANDOMIZATION",
            "masks": ["ASSET_LABELS", "MODEL", "PROVIDER"],
            "repeated_pairs": "CLUSTER_BY_RATER_AND_ASSET_NO_INDEPENDENCE_ASSUMPTION",
            "blinded_ui": "NOT_DEPLOYED"},
        "response_policy": {"missing": "REMAIN_MISSING_REPORT_DENOMINATORS",
            "tie": "SEPARATE_AGREEMENT_CATEGORY_EXCLUDE_BINARY_INTERVALS",
            "abstain": "NO_SCORE_EXCLUDE_BINARY_INTERVALS_REPORT_COUNT",
            "withdrawal": "EXCLUDE_REVOKED_DATA_RECOMPUTE_WITH_AUDIT_NO_IDENTITY_IN_ANALYSIS"},
        "sampling_power": {"status": "PENDING_INDEPENDENT_CALCULATION_AND_APPROVAL",
            "target_participant_count": None, "approved_calculation_digest": None,
            "analysis": "PREREGISTER_CLUSTERING_EFFECT_SIZE_PRECISION_ATTRITION_AND_STOP_RULE",
            "validity": "SOURCE_SLOT_COUNT_IS_NOT_SAMPLE_SIZE_OR_POWER"},
        "stop_criteria": list(STOP_CRITERIA),
        "exposure_order": ["RIGHTS_AND_SAFETY_REVIEW", "PARTICIPANT_INFORMED_OPT_IN",
                           "INDEPENDENT_ASSIGNMENT", "ACTUAL_EXPOSURE", "RESPONSE_OR_WITHDRAWAL"],
        "rights_review": ["ASSET_PROVENANCE", "LIKENESS", "VOICE", "PRODUCT_LOGO", "MEDIA_EXPOSURE"],
        "rights_status": "REVIEW_REQUIRED_NO_GRANT",
        "consent_checklist": list(CONSENT_CHECKLIST),
        "consent_text_status": "DRAFT_ONLY_NOT_PARTICIPANT_CONSENT",
        "privacy": {"mode": privacy_mode, "data_minimization": "NO_PERSONAL_DATA_IN_SOURCE",
            "identity_linkage": "SEPARATE_AUTHORIZED_SERVICE_NOT_CONNECTED",
            "retention_deletion_access": "PENDING_APPROVED_CUSTODIAN_AND_SCHEDULE"},
        "local_time_status": "SYNTHETIC_NOT_OBSERVED_NO_SESSION_TIMESTAMP",
        "observed_human_count": 0, "human_calibration": "NOT_RUN",
        "interpretation": "PREFERENCE_IS_NOT_ROI_RIGHTS_VIRAL_OR_FACTUAL_TRUTH",
        "authority": "BRAIN_DRAFT_RECOMMENDATION_ONLY_NO_AUTOMATIC_GATE"}


@dataclass(frozen=True, slots=True, init=False)
class BQ03bStudyProtocolDraft(bt._Record):
    SCHEMA = "BQ03bStudyProtocolDraft.v1"

    @classmethod
    def from_dict(cls, value, *, plan, plan_digest, **source):
        bt.fields(value, " ".join(_payload(None, PRIVACY_MODES[0], POPULATIONS[0])))
        native = bound_plan(plan, plan_digest, **source)
        expected = _payload(native, value["privacy"]["mode"] if type(value["privacy"]) is dict
                            and "mode" in value["privacy"] else None, value["population_category"])
        if bt.canonical(value) != bt.canonical(expected):
            raise Error("protocol differs from exact native design replay")
        return bt.bind(cls, expected)

    @classmethod
    def from_json(cls, raw, **kwargs):
        return cls.from_dict(bt.read(raw), **kwargs)

    def public_preview(self, *, plan, plan_digest, **source):
        validate_protocol(self, plan=plan, plan_digest=plan_digest, **source)
        # Fixed allowlist: no seed, plan digest, task/slot/pair mapping, asset,
        # product reference, file reference, time or participant identifier.
        return {"schema_version": "BQ03bPublicProtocolPreview.v1",
                "status": "DRAFT_ONLY_NOT_READY_FOR_LIVE_STUDY",
                "question": "Blinded pair preference on applicable preregistered rubrics",
                "consent": "INDIVIDUAL_OPT_IN_BEFORE_EXPOSURE_WITH_WITHDRAWAL",
                "privacy": "MINIMIZE_DATA_SEPARATE_AUTHORIZED_CUSTODY",
                "sampling": "PENDING_INDEPENDENT_CALCULATION_AND_APPROVAL",
                "observed_human_count": 0, "human_calibration": "NOT_RUN"}


def build_protocol(plan=None, *, plan_digest=None, privacy_mode=PRIVACY_MODES[0],
                   population=POPULATIONS[0], **source):
    native = bound_plan(plan, plan_digest, **source)
    return bt.bind(BQ03bStudyProtocolDraft, _payload(native, privacy_mode, population))


def validate_protocol(draft, *, plan, plan_digest, draft_digest=None, **source):
    if type(draft) is not BQ03bStudyProtocolDraft:
        raise Error("exact native preregistration draft required")
    if draft_digest is not None:
        draft.verify_digest(draft_digest)
    return BQ03bStudyProtocolDraft.from_json(draft.to_json(), plan=plan,
                                           plan_digest=plan_digest, **source)
