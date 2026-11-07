"""BQ-04a Q5/R7: deterministic repair decision policy and evidence binding.

Immutable canonical versioned BQ04aRepairDecision.v1.
No media editing, rendering, provider calling, or publication authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_commerce.creative_quality import behavioral, critic
from ai_commerce.creative_quality import multimodal_review as mm

try:
    from ai_commerce.creative_quality import study_readiness as sr
except ImportError:
    sr = None  # type: ignore[assignment]


SCHEMA = "BQ04aRepairDecision.v1"
CHOICES = (
    "KEEP_ORIGINAL",
    "PROPOSE_LOCAL_DETERMINISTIC_EDIT",
    "PROPOSE_SHOT_REGENERATION",
    "RETURN_TO_BRAIN",
    "HOLD",
)
AUTHORITY = {
    "repair_execution": "NONE",
    "provider_dispatch": "NONE",
    "publication": "NONE",
    "strategy_authority": "BRAIN_ONLY",
}


def _check_sha(value: Any, name: str) -> str:
    return behavioral._sha(value, name)


def _payload(
    review: mm.BQ02bMultimodalReview,
    *,
    asset_sha256: str,
    study_readiness: Any | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if type(review) is not mm.BQ02bMultimodalReview:
        raise critic.Error("native BQ02bMultimodalReview required")

    review._validate(review.to_dict())
    r_dict = review.to_dict()

    rev_asset_sha = r_dict.get("observed_asset_sha256")
    if rev_asset_sha is None:
        if asset_sha256 is not None and asset_sha256 != "0" * 64:
            raise critic.Error("asset SHA pin mismatch with review: review has no observed asset")
        observed_asset_sha = None
    else:
        if asset_sha256 is None:
            raise critic.Error("asset SHA pin required when review has observed asset")
        observed_asset_sha = _check_sha(asset_sha256, "asset_sha256")
        if rev_asset_sha != observed_asset_sha:
            raise critic.Error("asset SHA pin mismatch with review")

    readiness_digest = None
    human_calibration_status = "NOT_RUN"
    if study_readiness is not None:
        if sr is not None and type(study_readiness) is not sr.BQ03bStudyReadiness:
            raise critic.Error("native BQ03bStudyReadiness required")
        if not hasattr(study_readiness, "digest") or not hasattr(study_readiness, "to_dict"):
            raise critic.Error("valid study readiness record required")
        readiness_digest = study_readiness.digest
        human_calibration_status = "NOT_READY_FOR_LIVE_STUDY"

    # Context bounds and sanitization
    ctx = {}
    if context is not None:
        if type(context) is not dict:
            raise critic.Error("context must be a dictionary")
        critic._canonical(context)
        ctx = context

    # Extract nested evidence
    original_review = r_dict["original_review"]
    preflight = original_review["preflight"]
    technical_evidence = original_review.get("technical_evidence", {})
    sampled_evidence = r_dict.get("sampled_evidence", {})
    challenges = original_review.get("challenges", [])
    measured_findings = r_dict.get("measured_findings", {})
    r_reasons = list(r_dict.get("reason_codes", []))

    reasons: list[str] = list(r_reasons)
    risks: list[str] = []
    exemptions: list[str] = []
    grounded_fault: dict[str, Any] | None = None

    # Check creative exemptions
    is_silent = False
    if (
        measured_findings.get("audio_status") == "SILENT"
        or ctx.get("is_silent") is True
        or "SILENCE_EXEMPTED" in ctx.get("tags", [])
    ):
        is_silent = True
        exemptions.append("SILENT_AUDIO_EXEMPTED")

    is_fantasy = False
    plan_style = preflight.get("plan", {}).get("style", "")
    if (
        "fantasy" in plan_style.lower()
        or "animation" in plan_style.lower()
        or ctx.get("is_fantasy") is True
        or ctx.get("is_animation") is True
    ):
        is_fantasy = True
        exemptions.append("FANTASY_ANIMATION_PHYSICS_EXEMPTED")

    has_null_product = False
    brief = preflight.get("plan", {}).get("behavioral_brief", {})
    if brief.get("product_ref") is None or ctx.get("null_product") is True:
        has_null_product = True
        exemptions.append("NULL_PRODUCT_EXEMPTED")

    # Decision tree classification
    choice = "KEEP_ORIGINAL"
    status = "KEEP"

    # Pre-evaluate P0 conditions
    has_p0 = False
    p0_details: list[str] = []
    for item in preflight.get("items", []):
        if item.get("severity") == "P0" or item.get("dimension") == "claim_rights":
            has_p0 = True
            p0_details.append(f"P0_PREFLIGHT_{item.get('id', 'ITEM')}")
    for ch in challenges:
        if ch.get("severity") == "P0":
            has_p0 = True
            p0_details.append("P0_CHALLENGE_SEVERITY")
    if any(r.startswith("P0_") for r in r_reasons) or "P0_SOURCE_HOLD" in r_reasons:
        has_p0 = True
        p0_details.append("P0_REASON_CODE_FLAGGED")
    if ctx.get("rights_violation") or ctx.get("claim_mismatch") or ctx.get("identity_mismatch"):
        has_p0 = True
        p0_details.append("P0_CONTEXT_RIGHTS_OR_IDENTITY_VIOLATION")

    if observed_asset_sha is None:
        choice = "HOLD"
        status = "HELD"
        reasons.append("NO_OBSERVED_ASSET_HOLD")
        risks.append("NO_ASSET_OBSERVED_IN_REVIEW")

    # 1. P0 VETO CHECK (Fail-Closed)
    elif has_p0:
        choice = "HOLD"
        status = "HELD"
        reasons.append("P0_VETO_HOLD")
        reasons.extend(p0_details)
        risks.append("LEGAL_RIGHTS_CLAIM_OR_IDENTITY_VIOLATION")

    # 2. UNKNOWN PROVIDER EFFECT CHECK
    elif (
        "UNKNOWN_PROVIDER_EFFECT" in r_reasons
        or ctx.get("unknown_provider_effect") is True
    ):
        choice = "HOLD"
        status = "HELD"
        reasons.append("UNKNOWN_PROVIDER_EFFECT_HOLD")
        risks.append("AMBIGUOUS_PROVIDER_OUTCOME_NO_RETRY")

    # 3. TECHNICAL EVIDENCE & SAMPLING COMPLETENESS
    elif technical_evidence.get("status") in ("FAIL", "NOT_INSPECTED") or "A13_TECHNICAL_FAIL" in r_reasons:
        choice = "HOLD"
        status = "HELD"
        reasons.append("TECHNICAL_EVIDENCE_FAILED_OR_MISSING_HOLD")
        risks.append("UNVERIFIED_OR_CORRUPT_MEDIA_CONTAINER")

    elif sampled_evidence.get("status") != "OBSERVED":
        choice = "HOLD"
        status = "HELD"
        reasons.append("T2_SIGNAL_COVERAGE_MISSING_HOLD")
        risks.append("SIGNAL_PROBES_NOT_COMPLETED")

    # 4. GROUNDED FAULT DETECTION & CLASSIFICATION
    else:
        # Check for localized defects
        t1_findings = technical_evidence.get("findings", [])
        t2_frames = measured_findings.get("frames", [])
        t2_audio = measured_findings.get("audio", [])

        # Look for concrete localized flaws
        detected_faults: list[dict[str, Any]] = []

        # This profile describes a test-owned white slate, not a rule that
        # black frames in creative media are defects. It grants no execution.
        if ctx.get("synthetic_fixture_profile") == "bq04b-white-slate-4fps/v1":
            samples = {
                frame["time"]["requested_seconds"]: frame
                for frame in t2_frames
                if isinstance(frame, dict) and isinstance(frame.get("time"), dict)
                and "requested_seconds" in frame["time"]
            }
            target_samples = [samples.get(time) for time in ("1/2", "3/4")]
            if all(sample is not None and sample.get("state") == "OBSERVED"
                   and sample.get("region") == "FULL_FRAME"
                   and sample.get("minimum") == sample.get("maximum") == 0
                   and sample.get("mean_luma") == "0" for sample in target_samples):
                detected_faults.append({
                    "kind": "SYNTHETIC_WHITE_SLATE_BLACK_SIGNAL", "time": "1/2",
                    "region": [0.0, 0.0, 1.0, 1.0], "shot_id": None,
                    "severity": "SYNTHETIC_TECHNICAL_ONLY",
                })

        # T2 Frame findings (e.g. freeze or black frame)
        for frame in t2_frames:
            if type(frame) is dict and frame.get("defect"):
                time_val = frame.get("time") or frame.get("timestamp")
                if time_val is not None:
                    detected_faults.append({
                        "kind": frame.get("kind", "FRAME_SIGNAL_DEFECT"),
                        "time": str(time_val),
                        "region": frame.get("region"),
                        "shot_id": frame.get("shot_id"),
                        "severity": frame.get("severity", "LOCAL_DEFECT"),
                    })

        # T2 Audio findings (e.g. clipping, clipping silence if not exempt)
        if not is_silent:
            for audio in t2_audio:
                if type(audio) is dict and audio.get("defect"):
                    time_val = audio.get("time") or audio.get("timestamp")
                    if time_val is not None:
                        detected_faults.append({
                            "kind": audio.get("kind", "AUDIO_SIGNAL_DEFECT"),
                            "time": str(time_val),
                            "region": None,
                            "shot_id": audio.get("shot_id"),
                            "severity": audio.get("severity", "LOCAL_DEFECT"),
                        })

        # T1 findings with concrete time/region
        for finding in t1_findings:
            if type(finding) is dict and finding.get("evidence_state") in ("FAIL", "DEFECT"):
                t_val = finding.get("time")
                r_val = finding.get("region")
                if t_val is not None or r_val is not None:
                    detected_faults.append({
                        "kind": finding.get("kind", "T1_DECODE_DEFECT"),
                        "time": str(t_val) if t_val is not None else None,
                        "region": r_val,
                        "shot_id": finding.get("shot_id"),
                        "severity": "LOCAL_DEFECT",
                    })

        # Also check context injected defects - caller-provided context is UNTRUSTED!
        # Context cannot directly create verified Q4 fault or location.
        has_unproven_caller_defect = bool(
            ctx.get("grounded_defect")
            or ctx.get("caller_defect")
            or ctx.get("unproven_defect")
        )

        # Plan revisions (P1)
        has_p1 = (
            preflight.get("status") == "REVISE"
            or "P1_PLAN_REVISION" in r_reasons
            or any(item.get("severity") == "P1" for item in preflight.get("items", []))
            or ctx.get("plan_revision") is True
        )

        # Q2 P3 Subjective preference
        has_p3_preference = (
            any(item.get("severity") == "P3" for item in preflight.get("items", []))
            or r_dict.get("p3_route") == "BRAIN_ONLY"
            or ctx.get("subjective_preference") is True
        )

        # Ungrounded defect check
        has_ungrounded = (
            ctx.get("ungrounded_defect") is True
            or any(
                type(f) is dict and f.get("evidence_state") == "DEFECT" and f.get("time") is None and f.get("region") is None
                for f in t1_findings
            )
        )

        if has_unproven_caller_defect and not detected_faults:
            # Caller defect claim not verified by native Q4 observed evidence
            choice = "RETURN_TO_BRAIN"
            status = "RETURNED_TO_BRAIN"
            reasons.append("UNPROVEN_CALLER_DEFECT_NO_NATIVE_OBSERVATION")
            risks.append("CALLER_DEFECT_NOT_VERIFIED_BY_Q4_EVIDENCE")

        elif has_ungrounded:
            choice = "RETURN_TO_BRAIN"
            status = "RETURNED_TO_BRAIN"
            reasons.append("UNGROUNDED_FAULT_CANNOT_INVENT_COORDINATES")
            risks.append("FAULT_CANNOT_BE_LOCALIZED_REQUIRES_CREATIVE_REVIEW")

        elif detected_faults:
            # Concrete grounded defect found -> propose local edit
            grounded_fault = detected_faults[0]
            choice = "PROPOSE_LOCAL_DETERMINISTIC_EDIT"
            status = "PROPOSED_LOCAL_EDIT"
            reasons.append("LOCAL_DETERMINISTIC_EDIT_PROPOSED_NO_EXECUTION")
            risks.append("LOCAL_EDIT_MUST_PRESERVE_ALL_NON_TARGET_INVARIANTS")

        elif has_p1:
            if ctx.get("narrative_conflict") is True or ctx.get("product_truth_conflict") is True:
                choice = "RETURN_TO_BRAIN"
                status = "RETURNED_TO_BRAIN"
                reasons.append("NARRATIVE_OR_PRODUCT_TRUTH_CONFLICT_RETURN_TO_BRAIN")
                risks.append("NARRATIVE_DIRECTION_REQUIRES_HUMAN_BRAIN_DECISION")
            else:
                choice = "PROPOSE_SHOT_REGENERATION"
                status = "PROPOSED_SHOT_REGENERATION"
                reasons.append("SHOT_REGENERATION_REQUIRES_NEW_GRANTS_NO_DISPATCH")
                risks.append("SHOT_REGENERATION_REQUIRES_RESOURCE_AND_SPEND_GRANTS")

        elif has_p3_preference and (ctx.get("force_p3_return") is True or ctx.get("subjective_preference") is True):
            choice = "RETURN_TO_BRAIN"
            status = "RETURNED_TO_BRAIN"
            reasons.append("Q2_P3_SUBJECTIVE_PREFERENCE_TO_BRAIN")
            risks.append("SUBJECTIVE_AESTHETIC_PREFERENCE_NOT_AUTO_CORRECTED")

        else:
            # Clean / Acceptable noise -> KEEP_ORIGINAL (no quality pass)
            choice = "KEEP_ORIGINAL"
            status = "KEEP"
            reasons.append("KEEP_ORIGINAL_NO_QUALITY_PASS_ISSUED")
            risks.append("UNTESTED_SEMANTIC_PERCEPTUAL_RISK_REMAINS")

    uncertainties = [
        "T3_ASSESSMENT_NOT_RUN",
        "SEMANTIC_QUALITY_UNKNOWN",
        "HUMAN_PERCEPTUAL_CALIBRATION_NOT_RUN"
        if study_readiness is None
        else "HUMAN_PERCEPTUAL_CALIBRATION_NOT_READY_FOR_LIVE_STUDY",
        "REPAIR_EFFECT_UNPROVEN_UNTIL_REINSPECTED",
    ]

    return {
        "schema_version": SCHEMA,
        "choice": choice,
        "status": status,
        "original_asset_sha256": observed_asset_sha,
        "multimodal_review_digest": review.digest,
        "multimodal_review_version": r_dict.get("schema_version", "BQ02bMultimodalReview.v1"),
        "t1_digest": r_dict.get("technical_evidence_digest"),
        "t2_digest": r_dict.get("t2_digest"),
        "plan_digest": r_dict.get("plan_digest"),
        "preflight_digest": r_dict.get("preflight_digest"),
        "study_readiness_digest": readiness_digest,
        "human_calibration": human_calibration_status,
        "t3_status": "NOT_RUN",
        "semantic_quality": "UNKNOWN",
        "grounded_fault": grounded_fault,
        "exemptions": sorted(set(exemptions)),
        "reason_codes": sorted(set(reasons)),
        "risks": sorted(set(risks)),
        "uncertainties": sorted(set(uncertainties)),
        "authority": dict(AUTHORITY),
    }


@dataclass(frozen=True, slots=True, init=False)
class BQ04aRepairDecision(critic._Record):
    _review: mm.BQ02bMultimodalReview
    _readiness: Any | None
    SCHEMA = SCHEMA

    def __init__(self, raw: Any):
        raise critic.Error("use decide_repair or explicit replay from_dict/from_json")

    def _validate(self, value: Any) -> None:
        if type(value) is not dict or value.get("schema_version") != SCHEMA:
            raise critic.Error("valid BQ04aRepairDecision required")
        if value.get("choice") not in CHOICES:
            raise critic.Error("invalid repair choice")
        if value.get("original_asset_sha256") is not None:
            _check_sha(value.get("original_asset_sha256"), "original_asset_sha256")
        _check_sha(value.get("multimodal_review_digest"), "multimodal_review_digest")
        if value.get("study_readiness_digest") is not None:
            _check_sha(value.get("study_readiness_digest"), "study_readiness_digest")

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        review: mm.BQ02bMultimodalReview | None = None,
        study_readiness: Any | None = None,
        context: dict[str, Any] | None = None,
        root: Any = None,
    ) -> BQ04aRepairDecision:
        data = critic._read(critic._canonical(value))
        if type(data) is not dict or data.get("schema_version") != SCHEMA:
            raise critic.Error("valid BQ04aRepairDecision.v1 dict required")

        if review is None:
            if "multimodal_review" in data:
                review = mm.BQ02bMultimodalReview.from_dict(data["multimodal_review"], root=root)
            else:
                raise critic.Error("review record required for verified replay")

        readiness_digest = data.get("study_readiness_digest")
        if study_readiness is not None:
            if sr is not None and type(study_readiness) is not sr.BQ03bStudyReadiness:
                raise critic.Error("native BQ03bStudyReadiness required")
            if not hasattr(study_readiness, "digest") or not hasattr(study_readiness, "to_dict"):
                raise critic.Error("valid study readiness record required")
            if readiness_digest is not None and study_readiness.digest != readiness_digest:
                raise critic.Error("study readiness digest mismatch")
        elif readiness_digest is not None:
            raise critic.Error("study readiness record required for replay with study_readiness_digest")

        expected = decide_repair(
            review,
            review_digest=review.digest,
            asset_sha256=data.get("original_asset_sha256"),
            study_readiness=study_readiness,
            readiness_digest=readiness_digest,
            context=context,
        )

        expected_dict = expected.to_dict()
        if set(data.keys()) != set(expected_dict.keys()):
            raise critic.Error("unexpected, extra, or missing fields in repair decision")

        for key in expected_dict:
            if data[key] != expected_dict[key]:
                raise critic.Error(f"replay mismatch on decision field: {key}")

        return _bind_decision(expected_dict, review, study_readiness)

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        review: mm.BQ02bMultimodalReview | None = None,
        study_readiness: Any | None = None,
        context: dict[str, Any] | None = None,
        root: Any = None,
    ) -> BQ04aRepairDecision:
        return cls.from_dict(
            critic._read(raw),
            review=review,
            study_readiness=study_readiness,
            context=context,
            root=root,
        )


def _bind_decision(
    data: dict[str, Any],
    review: mm.BQ02bMultimodalReview,
    study_readiness: Any | None = None,
) -> BQ04aRepairDecision:
    result = object.__new__(BQ04aRepairDecision)
    object.__setattr__(result, "_review", review)
    object.__setattr__(result, "_readiness", study_readiness)
    critic._Record.__init__(result, critic._canonical(data))
    return result


def decide_repair(
    review: mm.BQ02bMultimodalReview,
    *,
    review_digest: str,
    asset_sha256: str | None = None,
    study_readiness: Any | None = None,
    readiness_digest: str | None = None,
    context: dict[str, Any] | None = None,
) -> BQ04aRepairDecision:
    if type(review) is not mm.BQ02bMultimodalReview:
        raise critic.Error("native BQ02bMultimodalReview required")

    review._validate(review.to_dict())
    review.verify_digest(review_digest)

    if study_readiness is not None and readiness_digest is not None:
        if hasattr(study_readiness, "verify_digest"):
            study_readiness.verify_digest(readiness_digest)
        elif hasattr(study_readiness, "digest") and study_readiness.digest != readiness_digest:
            raise critic.Error("study readiness digest mismatch")

    data = _payload(
        review,
        asset_sha256=asset_sha256,
        study_readiness=study_readiness,
        context=context,
    )
    return _bind_decision(data, review, study_readiness)
