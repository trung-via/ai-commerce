"""Q5 current-byte reinspection of one witnessed synthetic edit.

Historical A13/Q4 pins prove historical observations only. A technical result
requires locked current files, independent Q4 replay and every fixture frame.
No result grants rights, production quality, provider, spend or publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative_quality import critic, repair_reinspection as reinspection
from ai_commerce.creative_quality import repair_offline_adapter as adapter


@dataclass(frozen=True, slots=True, init=False)
class BQ04bTechnicalVerification(critic._Record):
    _sealed: str
    SCHEMA = "BQ04bTechnicalVerification.v1"

    def __init__(self, raw):
        raise critic.Error("use verify_technical_evidence")

    def _validate(self, value):
        if critic._canonical(value) != self._sealed:
            raise critic.Error("technical verification replay mismatch")

    @classmethod
    def from_dict(cls, value, **sources):
        observed = verify_technical_evidence(**sources)
        if critic._canonical(value) != observed.to_json():
            raise critic.Error("technical verification replay mismatch")
        return observed

    @classmethod
    def from_json(cls, raw, **sources):
        return cls.from_dict(critic._read(raw), **sources)


def _bind(data):
    record = object.__new__(BQ04bTechnicalVerification)
    object.__setattr__(record, "_sealed", critic._canonical(data))
    critic._Record.__init__(record, record._sealed)
    return record


def _timeline(root, path, tools):
    argv = [tools["ffprobe"], "-v", "error", "-protocol_whitelist", "file", "-enable_drefs", "0",
        "-select_streams", "v:0", "-show_frames", "-show_entries", "frame=best_effort_timestamp_time",
        "-of", "json", "-i", str(path)]
    try:
        frames = json.loads(adapter._runner(root).run(argv).stdout)["frames"]
        times = [str(Fraction(frame["best_effort_timestamp_time"])) for frame in frames]
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise critic.Error("frame timeline unproven") from exc
    if times != list(adapter.TIMES):
        raise critic.Error("fixed fixture timeline mismatch")
    return times


def _geometry(facts):
    return [(s["kind"], s.get("width"), s.get("height"), s.get("fps"),
             s.get("duration_seconds"), s.get("decoded_frames")) for s in facts["streams"]]


def _measure_locked(root, source, output, before, after, receipt):
    """Caller holds all path and byte locks through this complete observation."""
    original, derived = receipt["original_asset_sha256"], receipt["output_asset_sha256"]
    if adapter._hash(source) != original or adapter._hash(output) != derived:
        raise critic.Error("current source/output SHA mismatch")
    if (before["sampled_evidence"]["asset_ref"] != "fixture.mp4"
            or after["sampled_evidence"]["asset_ref"] != "repaired.mp4"
            or after["observed_asset_sha256"] != derived):
        raise critic.Error("AFTER file/asset binding mismatch")
    adapter.mm.BQ02bMultimodalReview.from_dict(before, root=root)
    adapter.mm.BQ02bMultimodalReview.from_dict(after, root=root)
    if before["preflight_digest"] != after["preflight_digest"]:
        raise critic.Error("BEFORE/AFTER preflight mismatch")
    tools = adapter._tools()
    bf, af = adapter._frames(root, source, tools), adapter._frames(root, output, tools)
    bt, at = _timeline(root, source, tools), _timeline(root, output, tools)
    for review in (before, after):
        facts = review["sampled_evidence"]["snapshot_reinspection"]["assets"][0]["facts"]
        if _geometry(facts) != [("video", 64, 64, "4", "2", 8)] or facts["duration_seconds"] != "2":
            raise critic.Error("stream geometry/duration invariant failed")
    if any(max(bf[index]) != 0 or min(af[index]) < 254 for index in (2, 3)):
        raise critic.Error("localized technical correction unproven")
    if any(bf[index] != af[index] for index in (0, 1, 4, 5, 6, 7)):
        raise critic.Error("non-target frame invariant failed")
    if adapter._hash(source) != original or adapter._hash(output) != derived:
        raise critic.Error("current bytes changed during reinspection")
    return dict(pixel_format="rgb24", width=64, height=64, frame_times_before=bt, frame_times_after=at,
        frame_sha256_before=[hashlib.sha256(frame).hexdigest() for frame in bf],
        frame_sha256_after=[hashlib.sha256(frame).hexdigest() for frame in af],
        target_indices=[2, 3], non_target_indices=[0, 1, 4, 5, 6, 7],
        target_before_maximum=[max(bf[index]) for index in (2, 3)],
        target_after_minimum=[min(af[index]) for index in (2, 3)])


def verify_technical_evidence(*, attempt, attempt_digest, decision, plan, decision_digest, plan_digest,
        request, request_digest, before, before_digest, after=None, after_digest=None,
        operation="NONE", decision_context=None, preservation_context=None, fixture=None, target_oracle=None):
    adapter._parents(decision, plan, decision_digest, plan_digest, decision_context, preservation_context)
    if type(attempt) is not adapter.BQ04bOfflineAttemptReceipt:
        raise critic.Error("native offline attempt required")
    attempt.verify_digest(attempt_digest)
    attempt._validate(attempt.to_dict())
    receipt = attempt.to_dict()
    if (receipt["decision_digest"] != decision.digest or receipt["preservation_plan_digest"] != plan.digest
            or receipt["operation"] != operation):
        raise adapter.ParentBindingError("attempt parent/operation mismatch")
    if type(request) is not reinspection.BQ04aReinspectionRequest:
        raise critic.Error("native BQ04a reinspection request required")
    request.verify_digest(request_digest)
    reinspection.BQ04aReinspectionRequest.from_dict(request.to_dict(), plan=plan)
    b = adapter.validate_review(before, before_digest)
    if b["observed_asset_sha256"] != receipt["original_asset_sha256"] or before.digest != receipt["before_review_digest"]:
        raise adapter.ParentBindingError("BEFORE review/asset mismatch")
    if (after is None) != (after_digest is None):
        raise critic.Error("AFTER review and digest required together")
    a = None if after is None else adapter.validate_review(after, after_digest)
    result = dict(schema_version=BQ04bTechnicalVerification.SCHEMA, status="IMPROVEMENT_UNPROVEN",
        evidence_domain="SYNTHETIC_TECHNICAL_ONLY", attempt_digest=attempt.digest,
        decision_digest=decision.digest, plan_digest=plan.digest, request_digest=request.digest,
        before_digest=before.digest, after_digest=None if after is None else after.digest,
        original_asset_sha256=receipt["original_asset_sha256"], candidate_output_sha256=receipt["output_asset_sha256"],
        measurement_identity="HISTORICAL_MEASUREMENT_SHA_BOUND", current_file_identity="UNKNOWN",
        target_corrected=False, technical_invariants_unregressed=False, preservation_status="PRESERVATION_UNPROVEN",
        reason_codes=[], measurements=None, path_rejection_code=None,
        product_identity_and_claims="UNKNOWN", story_and_semantics="UNKNOWN", rights="UNKNOWN",
        human_calibration="NOT_RUN", quality_pass=False, release_authorization="NONE", spend_authorization="NONE",
        authority=dict(adapter.policy.AUTHORITY))
    reasons = result["reason_codes"]
    if receipt["status"] != "EXECUTED":
        reasons.append("ATTEMPT_WAS_HOLD_NO_EXECUTION")
    elif fixture is None:
        reasons.append("CURRENT_FILE_REINSPECTION_NOT_AUTHORIZED")
    elif a is None:
        reasons.append("AFTER_MEASUREMENT_MISSING")
    elif target_oracle != adapter.ORACLE:
        reasons.append("LOCALIZED_ORACLE_MISSING_OR_UNSUPPORTED")
    else:
        adapter.BQ04bOfflineAttemptReceipt.from_dict(receipt, witness=attempt, decision=decision, plan=plan,
            decision_digest=decision_digest, plan_digest=plan_digest, operation=operation,
            decision_context=decision_context, preservation_context=preservation_context)
        state = adapter._session(fixture)
        if attempt._fixture is not fixture:
            raise critic.Error("attempt belongs to a different fixture scope")
        root = state["root"]
        try:
            with adapter._paths_locked(root, root / "fixture.mp4", root / "repaired.mp4"):
                measured = _measure_locked(root, root / "fixture.mp4", root / "repaired.mp4", b, a, receipt)
            # Publish facts only after every final handle/path check succeeds.
            result.update(status="TECHNICAL_SUCCESS", target_corrected=True, technical_invariants_unregressed=True,
                current_file_identity="CURRENT_FILE_BYTES_VERIFIED_DURING_REINSPECTION", measurements=measured)
            reasons.append("FIXED_SYNTHETIC_TARGET_AND_ALL_NON_TARGET_FRAMES_MEASURED")
        except (critic.Error, a13.MediaInspectionError, a13.MediaValidationError, a13.ToolFailure, OSError) as exc:
            if isinstance(exc, adapter.UnsafePathError):
                result["path_rejection_code"] = exc.code
            reasons.append("CURRENT_REINSPECTION_UNPROVEN:" + str(exc)[:160])
    reasons.append("PRODUCTION_SEMANTICS_RIGHTS_AND_PERCEPTUAL_QUALITY_UNPROVEN")
    return _bind(result)
