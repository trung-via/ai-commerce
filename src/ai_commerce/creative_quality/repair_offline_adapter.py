"""BQ-04b admission-only fallback. No editor or filesystem capability exists.

Native BQ02b does not classify localized defects, measure source frame/PTS or
ROI, or authenticate repair rights. Inventing those facts to reach BQ04a's
local-edit branch would violate its policy. Every admitted attempt is HOLD.
This record is evidence of a refused attempt, never evidence of edited bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_commerce.creative_quality import critic, multimodal_review as mm
from ai_commerce.creative_quality import repair_policy as policy
from ai_commerce.creative_quality import repair_preservation as preservation

SCHEMA = "BQ04bOfflineAttemptReceipt.v1"
SUPPORTED_OPERATIONS = ()  # Deliberately no executable operation in this cut.
OBSTACLE = "NATIVE_Q4_LOCALIZED_DEFECT_RIGHTS_AND_PRESERVATION_ORACLE_UNAVAILABLE"


def validate_review(review, expected_digest):
    """Validate native graph and closed native probe shapes without any I/O.

    This is in-process consistency, not a signature, fresh file rehash, or
    authentication against hostile Python code in the same interpreter.
    """
    if type(review) is not mm.BQ02bMultimodalReview:
        raise critic.Error("native BQ02bMultimodalReview required")
    review.verify_digest(expected_digest)
    review._validate(review.to_dict())
    sampled = review._sampled.to_dict()
    # Reject the native-shaped injected 'defect' records used by BQ04a unit
    # tests. They are branch fixtures, not an authentic detector extension.
    frame_keys = {"state", "stream_index", "region", "time", "width", "height",
                  "pixel_format", "sample_sha256", "minimum", "maximum", "mean_luma",
                  "mean_absolute_difference_previous", "confidence", "counterexample"}
    audio_keys = {"state", "stream_index", "region", "time", "requested_duration_seconds",
                  "observed_sample_count", "decoded_duration_seconds", "sample_rate_hz",
                  "channels", "sample_format", "sample_sha256", "absolute_peak", "mean_square",
                  "rms_integer_floor", "confidence", "counterexample"}
    time_keys = {"requested_seconds", "seek_argument_seconds", "binding", "sample_output_index",
                 "observed_source_frame_index", "observed_source_pts_seconds", "uncertainty"}
    for rows, keys in ((sampled["frames"], frame_keys), (sampled["audio"], audio_keys)):
        for row in rows:
            if (type(row) is not dict or set(row) != keys
                    or type(row["time"]) is not dict or set(row["time"]) != time_keys):
                raise critic.Error("closed native Q4 probe shape required; injected finding rejected")
    return review.to_dict()


@dataclass(frozen=True, slots=True, init=False)
class BQ04bOfflineAttemptReceipt(critic._Record):
    _expected_json: str
    SCHEMA = SCHEMA

    def __init__(self, raw):
        raise critic.Error("use assess_offline_attempt or explicit native replay")

    def _validate(self, value):
        if critic._canonical(value) != self._expected_json:
            raise critic.Error("offline attempt replay mismatch")

    @classmethod
    def from_dict(cls, value, *, decision, plan, decision_digest, plan_digest,
                  decision_context=None, preservation_context=None, operation="NONE"):
        expected = assess_offline_attempt(decision, plan, decision_digest=decision_digest,
            plan_digest=plan_digest, decision_context=decision_context,
            preservation_context=preservation_context, operation=operation)
        if critic._canonical(value) != expected.to_json():
            raise critic.Error("offline attempt replay mismatch (closed schema)")
        return expected

    @classmethod
    def from_json(cls, raw, **sources):
        return cls.from_dict(critic._read(raw), **sources)


def _bind(data):
    result = object.__new__(BQ04bOfflineAttemptReceipt)
    object.__setattr__(result, "_expected_json", critic._canonical(data))
    critic._Record.__init__(result, critic._canonical(data))
    return result


def assess_offline_attempt(decision, plan, *, decision_digest, plan_digest,
                           decision_context=None, preservation_context=None, operation="NONE"):
    """Replay BQ04a and return HOLD. Accept no path, runner, bytes or grant.

    Digests pin the native source graph. The original SHA is a native A13/Q4
    pin, explicitly not a current-file hash performed by this pure adapter.
    No scratch directory is created because no edit is admitted.
    """
    if type(decision) is not policy.BQ04aRepairDecision:
        raise critic.Error("native BQ04aRepairDecision required")
    if type(plan) is not preservation.BQ04aPreservationPlan:
        raise critic.Error("native BQ04aPreservationPlan required")
    decision.verify_digest(decision_digest)
    plan.verify_digest(plan_digest)
    review = decision._review
    source = validate_review(review, decision.to_dict()["multimodal_review_digest"])
    policy.BQ04aRepairDecision.from_dict(decision.to_dict(), review=review,
        study_readiness=decision._readiness, context=decision_context)
    preservation.BQ04aPreservationPlan.from_dict(plan.to_dict(), decision=decision,
        context=preservation_context)
    d, p = decision.to_dict(), plan.to_dict()
    if (p["decision_digest"] != decision.digest or plan._decision.digest != decision.digest
            or p["original_asset_sha256"] != d["original_asset_sha256"]):
        raise critic.Error("decision/plan parent or original SHA mismatch")
    if type(operation) is not str or operation not in preservation.SUPPORTED_EDITORS:
        raise critic.Error("unsupported operation; no caller editor or script accepted")
    reasons = {OBSTACLE, "NO_EXECUTABLE_OPERATION_IN_SOURCE_CUT", "IMPROVEMENT_UNPROVEN",
               "PRESERVATION_UNPROVEN", "CURRENT_FIXTURE_BYTES_NOT_REHASHED"}
    if d["choice"] != "PROPOSE_LOCAL_DETERMINISTIC_EDIT":
        reasons.add("BQ04A_DECISION_NOT_LOCAL_EDIT")
    if p["status"] != "PRESERVATION_PLAN_ESTABLISHED":
        reasons.add("BQ04A_PRESERVATION_NOT_ACTIONABLE")
    if not d["grounded_fault"]:
        reasons.add("NO_NATIVE_LOCALIZED_DEFECT")
    scope = p["target_scope"]
    if any(scope[k] is None for k in ("shot_id", "time_range", "spatial_region")):
        reasons.add("EXACT_TARGET_LOCALIZATION_UNPROVEN")
    if p["test_oracle"] is None:
        reasons.add("NO_PRESERVATION_TEST_ORACLE")
    if any(p["non_target_invariants"].get(k) is not True for k in preservation.REQUIRED_INVARIANTS):
        raise critic.Error("all preservation invariants must remain enabled")
    if operation != p["editor"]:
        reasons.add("OPERATION_PLAN_EDITOR_MISMATCH")
    return _bind({
        "schema_version": SCHEMA, "status": "HOLD", "evidence_domain": "SYNTHETIC",
        "decision_digest": decision.digest, "preservation_plan_digest": plan.digest,
        "before_review_digest": review.digest, "original_asset_sha256": d["original_asset_sha256"],
        "input_identity": "NATIVE_Q4_PIN_ONLY" if source["observed_asset_sha256"] else "UNAVAILABLE",
        "input_bytes_verified_by_adapter": False, "output_asset_sha256": None,
        "actual_bounded_location": None, "output_created": False,
        "operation": operation, "operation_version": "UNIMPLEMENTED",
        "supported_operations": list(SUPPORTED_OPERATIONS), "reason_codes": sorted(reasons),
        "uncertainties": ["CURRENT_FILE_IDENTITY_UNKNOWN", "REPAIR_EFFECT_UNKNOWN",
                          "ALL_PRESERVATION_UNPROVEN", "RIGHTS_UNKNOWN"],
        "quality_pass": False, "release_authorization": "NONE", "spend_authorization": "NONE",
        "external_effects": "NONE", "authority": dict(policy.AUTHORITY),
    })
