"""BQ-04a Q5/R7: bounded preservation plan and invariant locking.

Immutable canonical versioned BQ04aPreservationPlan.v1.
No media modification, file writing, network calls, or subprocesses.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from ai_commerce.creative_quality import behavioral, critic
from ai_commerce.creative_quality import repair_policy as rp

SCHEMA = "BQ04aPreservationPlan.v1"
SUPPORTED_EDITORS = (
    "NONE",
    "OFFLINE_DETERMINISTIC_TRIM",
    "OFFLINE_DETERMINISTIC_AUDIO_NORMALIZE",
    "OFFLINE_DETERMINISTIC_CROP",
    "OFFLINE_MOCK_EDITOR",
    "OFFLINE_DETERMINISTIC_FIXTURE_LUMA",
)
REQUIRED_INVARIANTS = (
    "product_identity_and_claims",
    "actor_voice_likeness_rights",
    "audio_continuity",
    "temporal_causal_continuity",
    "scene_intent",
    "surrounding_regions",
    "disclosure_and_brand_constraints",
    "source_provenance",
)


def _validate_time_range(value: Any) -> list[float] | None:
    if value is None or value == "UNKNOWN":
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise critic.Error("time_range must be a 2-element sequence [start_s, end_s]")
    try:
        start = float(value[0])
        end = float(value[1])
    except (ValueError, TypeError) as exc:
        raise critic.Error("time_range elements must be numbers") from exc
    if not (math.isfinite(start) and math.isfinite(end)):
        raise critic.Error("time_range elements must be finite")
    if start < 0 or end < start or end > 86400:
        raise critic.Error("time_range out of valid bounds [0, 86400]")
    return [round(start, 4), round(end, 4)]


def _validate_spatial_region(value: Any) -> list[float] | None:
    if value is None or value == "UNKNOWN":
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise critic.Error("spatial_region must be a 4-element sequence [x_min, y_min, x_max, y_max]")
    try:
        coords = [float(c) for c in value]
    except (ValueError, TypeError) as exc:
        raise critic.Error("spatial_region coordinates must be numbers") from exc
    for c in coords:
        if not math.isfinite(c) or c < 0.0 or c > 1.0:
            raise critic.Error("spatial_region coordinates must be normalized within [0.0, 1.0]")
    x_min, y_min, x_max, y_max = coords
    if x_min > x_max or y_min > y_max:
        raise critic.Error("spatial_region min coordinates must be <= max coordinates")
    return [round(c, 4) for c in coords]


def _payload(
    decision: rp.BQ04aRepairDecision,
    *,
    target_scope: dict[str, Any] | None = None,
    non_target_invariants: dict[str, Any] | None = None,
    test_oracle: dict[str, Any] | None = None,
    editor: str = "NONE",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if type(decision) is not rp.BQ04aRepairDecision:
        raise critic.Error("native BQ04aRepairDecision required")

    decision._validate(decision.to_dict())
    dec_dict = decision.to_dict()
    choice = dec_dict["choice"]
    original_asset_sha = dec_dict["original_asset_sha256"]

    # Invariants verification
    invariants: dict[str, bool] = {k: True for k in REQUIRED_INVARIANTS}
    if non_target_invariants is not None:
        if type(non_target_invariants) is not dict:
            raise critic.Error("non_target_invariants must be a dictionary")
        for key, val in non_target_invariants.items():
            if key not in REQUIRED_INVARIANTS:
                raise critic.Error(f"unrecognized invariant key: {key}")
            if val is not True:
                raise critic.Error(f"invariant {key} cannot be disabled or weakened without external grants")

    # Scope validation
    scope: dict[str, Any] = {
        "shot_id": None,
        "time_range": None,
        "spatial_region": None,
        "target_type": "UNKNOWN",
    }
    if target_scope is not None:
        if type(target_scope) is not dict:
            raise critic.Error("target_scope must be a dictionary")
        critic._canonical(target_scope)
        if "shot_id" in target_scope and target_scope["shot_id"] is not None:
            scope["shot_id"] = behavioral._identifier(target_scope["shot_id"], "target_scope.shot_id")
        if "time_range" in target_scope:
            scope["time_range"] = _validate_time_range(target_scope["time_range"])
        if "spatial_region" in target_scope:
            scope["spatial_region"] = _validate_spatial_region(target_scope["spatial_region"])
        if "target_type" in target_scope:
            scope["target_type"] = behavioral._text(target_scope["target_type"], "target_type", limit=64)

    # Editor validation
    ed = editor.upper().strip()
    if ed not in SUPPORTED_EDITORS:
        raise critic.Error(f"unsupported editor: {editor}")
    if choice != "PROPOSE_LOCAL_DETERMINISTIC_EDIT" and ed != "NONE":
        raise critic.Error("editor cannot be specified when choice is not local edit")

    reasons: list[str] = list(dec_dict.get("reason_codes", []))
    status = "PRESERVATION_PLAN_ESTABLISHED"

    if choice == "HOLD":
        status = "HOLD_NO_REPAIR_PERMITTED"
        reasons.append("DECISION_IS_HOLD_CANNOT_PRESERVE")

    elif choice == "KEEP_ORIGINAL":
        status = "LOCKED_KEEP_ORIGINAL"
        reasons.append("KEEP_ORIGINAL_ALL_INVARIANTS_LOCKED")

    elif choice == "RETURN_TO_BRAIN":
        status = "RETURNED_TO_BRAIN_NO_LOCAL_PLAN"
        reasons.append("RETURNED_TO_BRAIN_NO_LOCAL_PRESERVATION")

    elif choice == "PROPOSE_SHOT_REGENERATION":
        status = "SHOT_REGENERATION_UNAUTHORIZED_NO_DISPATCH"
        reasons.append("SHOT_REGENERATION_REQUIRES_SEPARATE_GRANTS")

    elif choice == "PROPOSE_LOCAL_DETERMINISTIC_EDIT":
        # Must have concrete localized scope
        if scope["time_range"] is None and scope["spatial_region"] is None and scope["shot_id"] is None:
            status = "HOLD_MISSING_CONCRETE_SCOPE"
            reasons.append("MISSING_CONCRETE_TARGET_SCOPE")
        elif test_oracle is None or type(test_oracle) is not dict or not test_oracle.get("oracle_type"):
            status = "HOLD_MISSING_TEST_ORACLE"
            reasons.append("NO_TEST_ORACLE_FOR_PRESERVATION")
        elif context and context.get("missing_rights") is True:
            status = "HOLD_MISSING_RIGHTS"
            reasons.append("RIGHTS_NOT_CONFIRMED_FOR_TARGET_REGION")
        elif ed == "NONE":
            status = "HOLD_NO_EDITOR_SPECIFIED"
            reasons.append("NO_SUPPORTED_EDITOR_SPECIFIED")
        else:
            status = "PRESERVATION_PLAN_ESTABLISHED"
            reasons.append("PRESERVATION_BOUNDARIES_LOCKED")

    # Oracle representation
    oracle_summary = None
    if test_oracle is not None:
        critic._canonical(test_oracle)
        oracle_summary = {
            "oracle_type": str(test_oracle.get("oracle_type")),
            "expected_verification": str(test_oracle.get("expected_verification", "Q4_AFTER_REINSPECTION")),
        }

    return {
        "schema_version": SCHEMA,
        "status": status,
        "decision_choice": choice,
        "decision_digest": decision.digest,
        "original_asset_sha256": original_asset_sha,
        "expected_derived_sha256": None,  # Strictly None; future evidence only, no made-up SHA
        "target_scope": scope,
        "non_target_invariants": invariants,
        "test_oracle": oracle_summary,
        "editor": ed,
        "reason_codes": sorted(set(reasons)),
        "authority": dict(rp.AUTHORITY),
    }


@dataclass(frozen=True, slots=True, init=False)
class BQ04aPreservationPlan(critic._Record):
    _decision: rp.BQ04aRepairDecision
    SCHEMA = SCHEMA

    def __init__(self, raw: Any):
        raise critic.Error("use create_preservation_plan or explicit replay from_dict/from_json")

    def _validate(self, value: Any) -> None:
        if type(value) is not dict or value.get("schema_version") != SCHEMA:
            raise critic.Error("valid BQ04aPreservationPlan required")
        if value.get("original_asset_sha256") is not None:
            behavioral._sha(value.get("original_asset_sha256"), "original_asset_sha256")
        behavioral._sha(value.get("decision_digest"), "decision_digest")
        if value.get("expected_derived_sha256") is not None:
            behavioral._sha(value.get("expected_derived_sha256"), "expected_derived_sha256")

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        decision: rp.BQ04aRepairDecision | None = None,
        context: dict[str, Any] | None = None,
        root: Any = None,
    ) -> BQ04aPreservationPlan:
        data = critic._read(critic._canonical(value))
        if type(data) is not dict or data.get("schema_version") != SCHEMA:
            raise critic.Error("valid BQ04aPreservationPlan.v1 dict required")

        if decision is None:
            if "decision" in data:
                decision = rp.BQ04aRepairDecision.from_dict(data["decision"], root=root)
            else:
                raise critic.Error("decision record required for verified replay")

        expected = create_preservation_plan(
            decision,
            decision_digest=decision.digest,
            target_scope=data.get("target_scope"),
            non_target_invariants=data.get("non_target_invariants"),
            test_oracle=data.get("test_oracle"),
            editor=data.get("editor", "NONE"),
            context=context,
        )

        expected_dict = expected.to_dict()
        if set(data.keys()) != set(expected_dict.keys()):
            raise critic.Error("unexpected, extra, or missing fields in preservation plan")

        for key in expected_dict:
            if data[key] != expected_dict[key]:
                raise critic.Error(f"replay mismatch on preservation key: {key}")

        return _bind_plan(expected_dict, decision)

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        decision: rp.BQ04aRepairDecision | None = None,
        context: dict[str, Any] | None = None,
        root: Any = None,
    ) -> BQ04aPreservationPlan:
        return cls.from_dict(critic._read(raw), decision=decision, context=context, root=root)


def _bind_plan(data: dict[str, Any], decision: rp.BQ04aRepairDecision) -> BQ04aPreservationPlan:
    result = object.__new__(BQ04aPreservationPlan)
    object.__setattr__(result, "_decision", decision)
    critic._Record.__init__(result, critic._canonical(data))
    return result


def create_preservation_plan(
    decision: rp.BQ04aRepairDecision,
    *,
    decision_digest: str,
    target_scope: dict[str, Any] | None = None,
    non_target_invariants: dict[str, Any] | None = None,
    test_oracle: dict[str, Any] | None = None,
    editor: str = "NONE",
    context: dict[str, Any] | None = None,
) -> BQ04aPreservationPlan:
    if type(decision) is not rp.BQ04aRepairDecision:
        raise critic.Error("native BQ04aRepairDecision required")

    decision._validate(decision.to_dict())
    decision.verify_digest(decision_digest)

    data = _payload(
        decision,
        target_scope=target_scope,
        non_target_invariants=non_target_invariants,
        test_oracle=test_oracle,
        editor=editor,
        context=context,
    )
    return _bind_plan(data, decision)
