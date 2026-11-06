"""BQ-04a Q5/R7: reinspection request and repair closure evaluation.

Immutable canonical versioned BQ04aReinspectionRequest.v1 and BQ04aRepairClosure.v1.
No media modification, file writing, network calls, or false quality passes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_commerce.creative_quality import behavioral, critic
from ai_commerce.creative_quality import multimodal_review as mm
from ai_commerce.creative_quality import repair_policy as rp
from ai_commerce.creative_quality import repair_preservation as r_pres

SCHEMA_REQUEST = "BQ04aReinspectionRequest.v1"
SCHEMA_CLOSURE = "BQ04aRepairClosure.v1"

DEFAULT_PROBES = (
    "T1_CONTAINER_DECODE_INTEGRITY",
    "T2_TARGET_RANGE_SIGNAL_PROBES",
    "INVARIANTS_NON_TARGET_REGIONS",
)


def _payload_request(
    plan: r_pres.BQ04aPreservationPlan,
    *,
    expected_probes: list[str] | None = None,
    expected_after_sha256: str | None = None,
) -> dict[str, Any]:
    if type(plan) is not r_pres.BQ04aPreservationPlan:
        raise critic.Error("native BQ04aPreservationPlan required")

    plan._validate(plan.to_dict())
    p_dict = plan.to_dict()
    orig_sha = p_dict["original_asset_sha256"]

    after_sha = None
    if expected_after_sha256 is not None:
        after_sha = behavioral._sha(expected_after_sha256, "expected_after_sha256")

    probes = list(DEFAULT_PROBES)
    if expected_probes is not None:
        if not isinstance(expected_probes, (list, tuple)):
            raise critic.Error("expected_probes must be a sequence of strings")
        probes = [behavioral._text(p, "probe_name", limit=64) for p in expected_probes]

    return {
        "schema_version": SCHEMA_REQUEST,
        "status": "PENDING_REINSPECTION",
        "preservation_plan_digest": plan.digest,
        "original_asset_sha256": orig_sha,
        "expected_derived_sha256": after_sha,
        "target_scope": p_dict["target_scope"],
        "non_target_invariants": p_dict["non_target_invariants"],
        "expected_probes": sorted(set(probes)),
        "reason_codes": ["REINSPECTION_PENDING_OBSERVED_AFTER_MEDIA"],
        "authority": dict(rp.AUTHORITY),
    }


@dataclass(frozen=True, slots=True, init=False)
class BQ04aReinspectionRequest(critic._Record):
    _plan: r_pres.BQ04aPreservationPlan
    SCHEMA = SCHEMA_REQUEST

    def __init__(self, raw: Any):
        raise critic.Error("use request_reinspection or explicit replay from_dict/from_json")

    def _validate(self, value: Any) -> None:
        if type(value) is not dict or value.get("schema_version") != SCHEMA_REQUEST:
            raise critic.Error("valid BQ04aReinspectionRequest required")
        if value.get("original_asset_sha256") is not None:
            behavioral._sha(value.get("original_asset_sha256"), "original_asset_sha256")
        behavioral._sha(value.get("preservation_plan_digest"), "preservation_plan_digest")
        if value.get("expected_derived_sha256") is not None:
            behavioral._sha(value.get("expected_derived_sha256"), "expected_derived_sha256")

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        plan: r_pres.BQ04aPreservationPlan | None = None,
        root: Any = None,
    ) -> BQ04aReinspectionRequest:
        data = critic._read(critic._canonical(value))
        if type(data) is not dict or data.get("schema_version") != SCHEMA_REQUEST:
            raise critic.Error("valid BQ04aReinspectionRequest.v1 dict required")

        if plan is None:
            if "preservation_plan" in data:
                plan = r_pres.BQ04aPreservationPlan.from_dict(data["preservation_plan"], root=root)
            else:
                raise critic.Error("preservation plan required for verified replay")

        expected = request_reinspection(
            plan,
            plan_digest=plan.digest,
            expected_probes=data.get("expected_probes"),
            expected_after_sha256=data.get("expected_derived_sha256"),
        )

        expected_dict = expected.to_dict()
        for key in (
            "schema_version",
            "preservation_plan_digest",
            "original_asset_sha256",
            "expected_derived_sha256",
            "target_scope",
            "authority",
        ):
            if data.get(key) != expected_dict.get(key):
                raise critic.Error(f"replay mismatch on reinspection request key: {key}")

        return _bind_request(data, plan)

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        plan: r_pres.BQ04aPreservationPlan | None = None,
        root: Any = None,
    ) -> BQ04aReinspectionRequest:
        return cls.from_dict(critic._read(raw), plan=plan, root=root)


def _bind_request(data: dict[str, Any], plan: r_pres.BQ04aPreservationPlan) -> BQ04aReinspectionRequest:
    result = object.__new__(BQ04aReinspectionRequest)
    object.__setattr__(result, "_plan", plan)
    critic._Record.__init__(result, critic._canonical(data))
    return result


def request_reinspection(
    plan: r_pres.BQ04aPreservationPlan,
    *,
    plan_digest: str,
    expected_probes: list[str] | None = None,
    expected_after_sha256: str | None = None,
) -> BQ04aReinspectionRequest:
    if type(plan) is not r_pres.BQ04aPreservationPlan:
        raise critic.Error("native BQ04aPreservationPlan required")

    plan._validate(plan.to_dict())
    plan.verify_digest(plan_digest)

    data = _payload_request(
        plan,
        expected_probes=expected_probes,
        expected_after_sha256=expected_after_sha256,
    )
    return _bind_request(data, plan)


def _payload_closure(
    request: BQ04aReinspectionRequest,
    *,
    after_review: mm.BQ02bMultimodalReview | None = None,
    after_asset_sha256: str | None = None,
    test_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if type(request) is not BQ04aReinspectionRequest:
        raise critic.Error("native BQ04aReinspectionRequest required")

    request._validate(request.to_dict())
    req_dict = request.to_dict()
    orig_sha = req_dict["original_asset_sha256"]

    # Default fail-closed state when no after measurement is supplied
    if after_review is None or after_asset_sha256 is None:
        return {
            "schema_version": SCHEMA_CLOSURE,
            "status": "PENDING_REINSPECTION",
            "outcome": "IMPROVEMENT_UNPROVEN",
            "reinspection_request_digest": request.digest,
            "original_asset_sha256": orig_sha,
            "after_asset_sha256": None,
            "after_review_digest": None,
            "target_corrected": False,
            "invariants_unregressed": False,
            "regressions": [],
            "counterexamples": [
                "Absence of independent AFTER measurement cannot establish repair success.",
                "T1/T2 measurements cannot prove semantic or human perceptual improvement.",
            ],
            "semantic_quality": "UNKNOWN",
            "human_calibration": "NOT_RUN",
            "quality_pass": False,
            "release_authorization": "NONE",
            "spend_authorization": "NONE",
            "reason_codes": [
                "IMPROVEMENT_UNPROVEN",
                "NO_AFTER_MEDIA_OR_REVIEW_OBSERVED",
                "PRESERVATION_UNPROVEN",
            ],
            "authority": dict(rp.AUTHORITY),
        }

    # If after_review is provided, validate strictly
    if type(after_review) is not mm.BQ02bMultimodalReview:
        raise critic.Error("after_review must be a native BQ02bMultimodalReview")

    after_review._validate(after_review.to_dict())
    after_dict = after_review.to_dict()

    after_sha = behavioral._sha(after_asset_sha256, "after_asset_sha256")
    if after_dict["observed_asset_sha256"] != after_sha:
        raise critic.Error("after_review asset SHA mismatch with provided after_asset_sha256")

    reasons: list[str] = []
    regressions: list[str] = []
    target_corrected = False
    invariants_unregressed = True

    # Check for unchanged bytes
    if after_sha == orig_sha:
        return {
            "schema_version": SCHEMA_CLOSURE,
            "status": "IMPROVEMENT_UNPROVEN",
            "outcome": "UNCHANGED_ORIGINAL_BYTES",
            "reinspection_request_digest": request.digest,
            "original_asset_sha256": orig_sha,
            "after_asset_sha256": after_sha,
            "after_review_digest": after_review.digest,
            "target_corrected": False,
            "invariants_unregressed": True,
            "regressions": [],
            "counterexamples": [
                "Unchanged asset bytes indicate no repair execution occurred.",
            ],
            "semantic_quality": "UNKNOWN",
            "human_calibration": "NOT_RUN",
            "quality_pass": False,
            "release_authorization": "NONE",
            "spend_authorization": "NONE",
            "reason_codes": [
                "IMPROVEMENT_UNPROVEN",
                "PRESERVATION_UNPROVEN",
                "UNCHANGED_ORIGINAL_BYTES_NO_REPAIR",
            ],
            "authority": dict(rp.AUTHORITY),
        }

    # Exclude synthetic fixtures / unobserved proofs
    if (
        test_receipt
        and test_receipt.get("is_synthetic") is True
        or after_dict.get("confidence", {}).get("T2") != "MEASURED_SIGNAL_ONLY"
    ):
        reasons.append("SYNTHETIC_OR_UNOBSERVED_PROOF_EXCLUDED")

    # Evaluate target correction
    # Check if technical or signal defects are resolved
    after_orig = after_dict.get("original_review", {})
    after_t1_status = after_orig.get("technical_evidence", {}).get("status")
    after_t2_status = after_dict.get("sampled_evidence", {}).get("status")
    after_reasons = set(after_dict.get("reason_codes", []))

    if after_t1_status == "PASS" and after_t2_status == "OBSERVED":
        # Check if defect reason codes from before were cleared
        target_corrected = True
        reasons.append("TARGET_FAULT_MEASURED_CORRECTED")
    else:
        target_corrected = False
        reasons.append("TARGET_DEFECT_PERSISTS_IN_AFTER_REVIEW")

    # Evaluate invariant regressions
    if "A13_TECHNICAL_FAIL" in after_reasons or after_t1_status == "FAIL":
        invariants_unregressed = False
        regressions.append("A13_TECHNICAL_DECODING_REGRESSION")

    if any(r.startswith("P0_") for r in after_reasons) or "P0_SOURCE_HOLD" in after_reasons:
        invariants_unregressed = False
        regressions.append("P0_LEGAL_OR_RIGHTS_REGRESSION")

    # Determine final closure status
    if not target_corrected:
        status = "REINSPECTION_FAILED_DEFECT_PERSISTS"
        outcome = "DEFECT_PERSISTS"
        reasons.append("REINSPECTION_DEFECT_NOT_CORRECTED")
    elif not invariants_unregressed:
        status = "REINSPECTION_FAILED_REGRESSION"
        outcome = "REGRESSION_DETECTED"
        reasons.append("REINSPECTION_INVARIANTS_VIOLATED")
    else:
        status = "TECHNICAL_TARGET_CORRECTED_INVARIANTS_UNREGRESSED"
        outcome = "FORWARD_TO_BRAIN_FOR_ATTENDED_REVIEW"
        reasons.extend([
            "INVARIANTS_PRESERVED_WITHOUT_REGRESSION",
            "SEMANTIC_AND_HUMAN_QUALITY_REMAIN_UNPROVEN",
        ])

    return {
        "schema_version": SCHEMA_CLOSURE,
        "status": status,
        "outcome": outcome,
        "reinspection_request_digest": request.digest,
        "original_asset_sha256": orig_sha,
        "after_asset_sha256": after_sha,
        "after_review_digest": after_review.digest,
        "target_corrected": target_corrected,
        "invariants_unregressed": invariants_unregressed,
        "regressions": sorted(set(regressions)),
        "counterexamples": [
            "Independent AFTER measurement confirms local technical signal change only.",
            "T1/T2 measurements cannot prove semantic or human perceptual improvement.",
            "Q4 green technical signal is never a license to publish or spend.",
        ],
        "semantic_quality": "UNKNOWN",
        "human_calibration": "NOT_RUN",
        "quality_pass": False,
        "release_authorization": "NONE",
        "spend_authorization": "NONE",
        "reason_codes": sorted(set(reasons)),
        "authority": dict(rp.AUTHORITY),
    }


@dataclass(frozen=True, slots=True, init=False)
class BQ04aRepairClosure(critic._Record):
    _request: BQ04aReinspectionRequest
    _after_review: mm.BQ02bMultimodalReview | None
    SCHEMA = SCHEMA_CLOSURE

    def __init__(self, raw: Any):
        raise critic.Error("use evaluate_repair_closure or explicit replay from_dict/from_json")

    def _validate(self, value: Any) -> None:
        if type(value) is not dict or value.get("schema_version") != SCHEMA_CLOSURE:
            raise critic.Error("valid BQ04aRepairClosure required")
        if value.get("original_asset_sha256") is not None:
            behavioral._sha(value.get("original_asset_sha256"), "original_asset_sha256")
        behavioral._sha(value.get("reinspection_request_digest"), "reinspection_request_digest")
        if value.get("after_asset_sha256") is not None:
            behavioral._sha(value.get("after_asset_sha256"), "after_asset_sha256")
        if value.get("after_review_digest") is not None:
            behavioral._sha(value.get("after_review_digest"), "after_review_digest")

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        request: BQ04aReinspectionRequest | None = None,
        after_review: mm.BQ02bMultimodalReview | None = None,
        root: Any = None,
    ) -> BQ04aRepairClosure:
        data = critic._read(critic._canonical(value))
        if type(data) is not dict or data.get("schema_version") != SCHEMA_CLOSURE:
            raise critic.Error("valid BQ04aRepairClosure.v1 dict required")

        if request is None:
            if "request" in data:
                request = BQ04aReinspectionRequest.from_dict(data["request"], root=root)
            else:
                raise critic.Error("reinspection request required for verified replay")

        if after_review is None and "after_review" in data:
            after_review = mm.BQ02bMultimodalReview.from_dict(data["after_review"], root=root)

        expected = evaluate_repair_closure(
            request,
            request_digest=request.digest,
            after_review=after_review,
            after_asset_sha256=data.get("after_asset_sha256"),
        )

        expected_dict = expected.to_dict()
        for key in (
            "schema_version",
            "status",
            "outcome",
            "reinspection_request_digest",
            "original_asset_sha256",
            "after_asset_sha256",
            "target_corrected",
            "invariants_unregressed",
            "quality_pass",
            "release_authorization",
            "spend_authorization",
            "authority",
        ):
            if data.get(key) != expected_dict.get(key):
                raise critic.Error(f"replay mismatch on closure key: {key}")

        return _bind_closure(data, request, after_review)

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        request: BQ04aReinspectionRequest | None = None,
        after_review: mm.BQ02bMultimodalReview | None = None,
        root: Any = None,
    ) -> BQ04aRepairClosure:
        return cls.from_dict(critic._read(raw), request=request, after_review=after_review, root=root)


def _bind_closure(
    data: dict[str, Any],
    request: BQ04aReinspectionRequest,
    after_review: mm.BQ02bMultimodalReview | None = None,
) -> BQ04aRepairClosure:
    result = object.__new__(BQ04aRepairClosure)
    object.__setattr__(result, "_request", request)
    object.__setattr__(result, "_after_review", after_review)
    critic._Record.__init__(result, critic._canonical(data))
    return result


def evaluate_repair_closure(
    request: BQ04aReinspectionRequest,
    *,
    request_digest: str,
    after_review: mm.BQ02bMultimodalReview | None = None,
    after_review_digest: str | None = None,
    after_asset_sha256: str | None = None,
    test_receipt: dict[str, Any] | None = None,
) -> BQ04aRepairClosure:
    if type(request) is not BQ04aReinspectionRequest:
        raise critic.Error("native BQ04aReinspectionRequest required")

    request._validate(request.to_dict())
    request.verify_digest(request_digest)

    if after_review is not None and after_review_digest is not None:
        after_review.verify_digest(after_review_digest)

    data = _payload_closure(
        request,
        after_review=after_review,
        after_asset_sha256=after_asset_sha256,
        test_receipt=test_receipt,
    )
    return _bind_closure(data, request, after_review)
