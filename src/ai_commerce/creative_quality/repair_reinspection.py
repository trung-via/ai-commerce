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
    plan_status = p_dict.get("status", "")
    choice = p_dict.get("decision_choice", "")

    # Non-actionable HOLD or RETURN_TO_BRAIN preservation plans must never become actionable PENDING_REINSPECTION
    if plan_status.startswith("HOLD_") or choice == "HOLD":
        status = plan_status if plan_status.startswith("HOLD_") else "HOLD_REINSPECTION_BLOCKED"
        reasons = ["HOLD_PRESERVATION_CANNOT_REQUEST_REINSPECTION", plan_status]
    elif plan_status == "RETURNED_TO_BRAIN_NO_LOCAL_PLAN" or choice == "RETURN_TO_BRAIN":
        status = "RETURNED_TO_BRAIN_NON_ACTIONABLE"
        reasons = ["RETURNED_TO_BRAIN_NO_REINSPECTION", "NON_ACTIONABLE_PLAN"]
    else:
        status = "PENDING_REINSPECTION"
        reasons = ["REINSPECTION_PENDING_OBSERVED_AFTER_MEDIA"]

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
        "status": status,
        "preservation_plan_digest": plan.digest,
        "original_asset_sha256": orig_sha,
        "expected_derived_sha256": after_sha,
        "target_scope": p_dict["target_scope"],
        "non_target_invariants": p_dict["non_target_invariants"],
        "expected_probes": sorted(set(probes)),
        "reason_codes": sorted(set(reasons)),
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
        expected_probes: list[str] | None = None,
        expected_after_sha256: str | None = None,
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

        plan_expected_sha = plan.to_dict().get("expected_derived_sha256")
        target_sha = expected_after_sha256 if expected_after_sha256 is not None else plan_expected_sha

        expected = request_reinspection(
            plan,
            plan_digest=plan.digest,
            expected_probes=expected_probes,
            expected_after_sha256=target_sha,
        )

        expected_dict = expected.to_dict()
        if set(data.keys()) != set(expected_dict.keys()):
            raise critic.Error("unexpected, extra, or missing fields in reinspection request")

        for key in expected_dict:
            if data[key] != expected_dict[key]:
                raise critic.Error(f"replay mismatch on reinspection request key: {key}")

        return _bind_request(expected_dict, plan)

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        plan: r_pres.BQ04aPreservationPlan | None = None,
        expected_probes: list[str] | None = None,
        expected_after_sha256: str | None = None,
        root: Any = None,
    ) -> BQ04aReinspectionRequest:
        return cls.from_dict(
            critic._read(raw),
            plan=plan,
            expected_probes=expected_probes,
            expected_after_sha256=expected_after_sha256,
            root=root,
        )


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

    # Reinspection request must be actionable PENDING_REINSPECTION
    if req_dict.get("status") != "PENDING_REINSPECTION":
        return {
            "schema_version": SCHEMA_CLOSURE,
            "status": "HOLD_REINSPECTION_BLOCKED",
            "outcome": "NON_ACTIONABLE_REQUEST",
            "reinspection_request_digest": request.digest,
            "original_asset_sha256": orig_sha,
            "after_asset_sha256": None,
            "after_review_digest": None,
            "target_corrected": False,
            "invariants_unregressed": False,
            "regressions": [],
            "counterexamples": [
                "Reinspection request is in a non-actionable or HOLD status.",
                "Non-actionable requests cannot evaluate closure.",
            ],
            "semantic_quality": "UNKNOWN",
            "human_calibration": "NOT_RUN",
            "quality_pass": False,
            "release_authorization": "NONE",
            "spend_authorization": "NONE",
            "reason_codes": [
                "HOLD_REINSPECTION_NOT_ACTIONABLE",
                "REINSPECTION_BLOCKED_BY_REQUEST_STATUS",
            ],
            "authority": dict(rp.AUTHORITY),
        }

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

    # Check expected derived SHA
    if (
        req_dict.get("expected_derived_sha256") is not None
        and after_sha != req_dict["expected_derived_sha256"]
    ):
        return {
            "schema_version": SCHEMA_CLOSURE,
            "status": "IMPROVEMENT_UNPROVEN",
            "outcome": "AFTER_SHA_MISMATCH",
            "reinspection_request_digest": request.digest,
            "original_asset_sha256": orig_sha,
            "after_asset_sha256": after_sha,
            "after_review_digest": after_review.digest,
            "target_corrected": False,
            "invariants_unregressed": False,
            "regressions": [],
            "counterexamples": [
                "Observed AFTER asset SHA does not match expected derived SHA from preservation plan.",
            ],
            "semantic_quality": "UNKNOWN",
            "human_calibration": "NOT_RUN",
            "quality_pass": False,
            "release_authorization": "NONE",
            "spend_authorization": "NONE",
            "reason_codes": [
                "EXPECTED_AFTER_SHA_MISMATCH",
                "IMPROVEMENT_UNPROVEN",
                "PRESERVATION_UNPROVEN",
            ],
            "authority": dict(rp.AUTHORITY),
        }

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
            "invariants_unregressed": False,
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

    # Evaluate invariant regressions
    after_orig = after_dict.get("original_review", {})
    after_t1_status = after_orig.get("technical_evidence", {}).get("status")
    after_reasons = set(after_dict.get("reason_codes", []))
    regressions: list[str] = []

    if "A13_TECHNICAL_FAIL" in after_reasons or after_t1_status == "FAIL":
        regressions.append("A13_TECHNICAL_DECODING_REGRESSION")

    if any(r.startswith("P0_") for r in after_reasons) or "P0_SOURCE_HOLD" in after_reasons:
        regressions.append("P0_LEGAL_OR_RIGHTS_REGRESSION")

    if regressions:
        return {
            "schema_version": SCHEMA_CLOSURE,
            "status": "REINSPECTION_FAILED_REGRESSION",
            "outcome": "REGRESSION_DETECTED",
            "reinspection_request_digest": request.digest,
            "original_asset_sha256": orig_sha,
            "after_asset_sha256": after_sha,
            "after_review_digest": after_review.digest,
            "target_corrected": False,
            "invariants_unregressed": False,
            "regressions": sorted(set(regressions)),
            "counterexamples": [
                "Reinspection detected technical, legal or rights regressions in AFTER media.",
            ],
            "semantic_quality": "UNKNOWN",
            "human_calibration": "NOT_RUN",
            "quality_pass": False,
            "release_authorization": "NONE",
            "spend_authorization": "NONE",
            "reason_codes": sorted(set(["REINSPECTION_INVARIANTS_VIOLATED", "REINSPECTION_FAILED_REGRESSION"] + regressions)),
            "authority": dict(rp.AUTHORITY),
        }

    reasons: list[str] = []

    # Check synthetic receipt or unobserved proof
    is_synthetic = bool(
        (test_receipt and (
            test_receipt.get("is_synthetic") is True
            or test_receipt.get("type") == "SYNTHETIC"
            or test_receipt.get("synthetic") is True
        ))
        or after_dict.get("confidence", {}).get("T2") != "MEASURED_SIGNAL_ONLY"
    )
    if is_synthetic:
        reasons.append("SYNTHETIC_OR_UNOBSERVED_PROOF_EXCLUDED")

    target_scope = req_dict.get("target_scope", {})
    has_target = bool(
        target_scope.get("time_range")
        or target_scope.get("spatial_region")
        or target_scope.get("shot_id")
    )
    if not has_target:
        reasons.append("NO_ORIGINAL_LOCALIZED_FAULT_TO_CORRECT")

    # In BQ-04a source-only, there is no authenticated live measurement (which requires BQ-04b).
    # Caller JSON 'test_receipt', boolean flags, or self-attestation cannot prove a real Q4 probe happened.
    # Therefore, improvement and preservation remain UNPROVEN.
    reasons.extend([
        "AUTHENTICATED_MEASUREMENT_PENDING_BQ04B",
        "IMPROVEMENT_UNPROVEN",
        "INDEPENDENT_TARGET_AND_INVARIANT_PROOF_REQUIRED",
        "PRESERVATION_UNPROVEN",
    ])

    return {
        "schema_version": SCHEMA_CLOSURE,
        "status": "IMPROVEMENT_UNPROVEN",
        "outcome": "IMPROVEMENT_UNPROVEN",
        "reinspection_request_digest": request.digest,
        "original_asset_sha256": orig_sha,
        "after_asset_sha256": after_sha,
        "after_review_digest": after_review.digest,
        "target_corrected": False,
        "invariants_unregressed": False,
        "regressions": [],
        "counterexamples": [
            "AFTER asset SHA difference alone is not proof of repair.",
            "Independent Q4 before/after localized target evidence and non-target receipts required.",
            "Synthetic or caller-attested receipts cannot prove real Q4 probe execution.",
            "BQ-04a source-only cannot authenticate live measurement; requires BQ-04b.",
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
        test_receipt: dict[str, Any] | None = None,
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

        receipt = test_receipt if test_receipt is not None else data.get("test_receipt")
        expected = evaluate_repair_closure(
            request,
            request_digest=request.digest,
            after_review=after_review,
            after_review_digest=after_review.digest if after_review else None,
            after_asset_sha256=data.get("after_asset_sha256"),
            test_receipt=receipt,
        )

        expected_dict = expected.to_dict()
        if set(data.keys()) != set(expected_dict.keys()):
            raise critic.Error("unexpected, extra, or missing fields in repair closure")

        for key in expected_dict:
            if data[key] != expected_dict[key]:
                raise critic.Error(f"replay mismatch on closure key: {key}")

        return _bind_closure(expected_dict, request, after_review)

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        request: BQ04aReinspectionRequest | None = None,
        after_review: mm.BQ02bMultimodalReview | None = None,
        test_receipt: dict[str, Any] | None = None,
        root: Any = None,
    ) -> BQ04aRepairClosure:
        return cls.from_dict(
            critic._read(raw),
            request=request,
            after_review=after_review,
            test_receipt=test_receipt,
            root=root,
        )


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
