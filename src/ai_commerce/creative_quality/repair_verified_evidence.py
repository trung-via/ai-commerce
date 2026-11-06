"""Read-only BQ-04b evidence comparison; no BQ04a closure policy replacement.

Native A13/T2 records bind historical measured bytes. This pure bridge does
not reopen files, run tools, authenticate an editor, or prove current bytes.
Only an independently bound candidate and exact target/invariant oracles
could corroborate correction; this source cut has neither and fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative_quality import critic, repair_reinspection as reinspection
from ai_commerce.creative_quality import repair_offline_adapter as adapter

SCHEMA = "BQ04bTechnicalVerification.v1"


@dataclass(frozen=True, slots=True, init=False)
class BQ04bTechnicalVerification(critic._Record):
    _expected_json: str
    SCHEMA = SCHEMA

    def __init__(self, raw):
        raise critic.Error("use verify_technical_evidence or explicit native replay")

    def _validate(self, value):
        if critic._canonical(value) != self._expected_json:
            raise critic.Error("technical verification replay mismatch")

    @classmethod
    def from_dict(cls, value, **sources):
        expected = verify_technical_evidence(**sources)
        if critic._canonical(value) != expected.to_json():
            raise critic.Error("technical verification replay mismatch (closed schema)")
        return expected

    @classmethod
    def from_json(cls, raw, **sources):
        return cls.from_dict(critic._read(raw), **sources)


def _native_measurement(review, digest):
    data = adapter.validate_review(review, digest)
    sampled = data["sampled_evidence"]
    report = sampled["snapshot_reinspection"]
    if sampled["status"] != "OBSERVED" or report is None:
        raise critic.Error("native tool-backed BEFORE/AFTER measurement required")
    # A13 independently owns validation of its closed report, hashes and facts.
    validated = a13.MediaReleaseInspection(report).to_dict()
    matching = [row for row in validated["assets"] if row["asset_ref"] == sampled["asset_ref"]]
    if (len(matching) != 1 or validated["status"] != "PASS"
            or matching[0]["status"] != "PASS" or matching[0]["full_decode"] is not True
            or not matching[0]["size_bytes"]
            or matching[0]["expected_sha256"] != matching[0]["observed_sha256"]
            or matching[0]["observed_sha256"] != sampled["observed_asset_sha256"]
            or data["observed_asset_sha256"] != sampled["observed_asset_sha256"]):
        raise critic.Error("native A13/T2 asset SHA binding mismatch")
    return data


def verify_technical_evidence(*, attempt, attempt_digest, decision, plan, decision_digest,
                              plan_digest, request, request_digest, before, before_digest,
                              after=None, after_digest=None, decision_context=None,
                              preservation_context=None, operation="NONE"):
    """Compare native sources, never promote receipt claims to measurements.

    No path, oracle callback, context script, quality flag or production-domain
    switch is accepted. AFTER's SHA is recorded as a native measurement pin,
    never as a newly produced/independently file-hashed candidate identity.
    """
    if type(attempt) is not adapter.BQ04bOfflineAttemptReceipt:
        raise critic.Error("native BQ04bOfflineAttemptReceipt required")
    attempt.verify_digest(attempt_digest)
    adapter.BQ04bOfflineAttemptReceipt.from_dict(attempt.to_dict(), decision=decision,
        plan=plan, decision_digest=decision_digest, plan_digest=plan_digest,
        decision_context=decision_context, preservation_context=preservation_context,
        operation=operation)
    if type(request) is not reinspection.BQ04aReinspectionRequest:
        raise critic.Error("native BQ04aReinspectionRequest required")
    request.verify_digest(request_digest)
    # A held adapter has no derived bytes, so cannot supply an AFTER pin.
    reinspection.BQ04aReinspectionRequest.from_dict(request.to_dict(), plan=plan)
    r, held = request.to_dict(), attempt.to_dict()
    b = _native_measurement(before, before_digest)
    if (before.digest != held["before_review_digest"]
            or b["observed_asset_sha256"] != held["original_asset_sha256"]
            or r["original_asset_sha256"] != held["original_asset_sha256"]
            or request._plan.digest != plan.digest):
        raise critic.Error("original BEFORE/request/attempt parent binding mismatch")
    if (after is None) != (after_digest is None):
        raise critic.Error("AFTER record and digest must be supplied together")
    a = None if after is None else _native_measurement(after, after_digest)
    reasons = {"IMPROVEMENT_UNPROVEN", "PRESERVATION_UNPROVEN", "NO_TRUSTED_CANDIDATE_OUTPUT",
               "CURRENT_FILES_NOT_REHASHED", "NO_EXACT_TARGET_BEFORE_AFTER_ORACLE",
               "NON_TARGET_TECHNICAL_INVARIANTS_NOT_INDEPENDENTLY_CHECKED"}
    if r["status"] != "PENDING_REINSPECTION":
        reasons.add("BQ04A_REINSPECTION_NON_ACTIONABLE")
    if a is None:
        reasons.add("NO_AFTER_MEASUREMENT")
    elif a["observed_asset_sha256"] == b["observed_asset_sha256"]:
        reasons.add("UNCHANGED_ORIGINAL_BYTES_NO_REPAIR")
    else:
        reasons.add("ALTERNATE_ASSET_HAS_NO_TARGET_OR_CANDIDATE_LINK")
    value = {
        "schema_version": SCHEMA, "status": "IMPROVEMENT_UNPROVEN",
        "preservation_status": "PRESERVATION_UNPROVEN", "evidence_domain": "SYNTHETIC",
        "attempt_digest": attempt.digest, "request_digest": request.digest,
        "before_review_digest": before.digest, "after_review_digest": None if after is None else after.digest,
        "original_asset_sha256": held["original_asset_sha256"],
        "after_native_measurement_pin": None if a is None else a["observed_asset_sha256"],
        "candidate_output_sha256": None, "current_file_identity": "UNKNOWN",
        "target_scope": r["target_scope"], "target_corrected": False,
        "technical_invariants_unregressed": False, "all_invariants_unregressed": False,
        "product_identity_and_claims": "UNKNOWN", "actor_voice_likeness_rights": "UNKNOWN",
        "story_and_semantics": "UNKNOWN", "human_calibration": "UNKNOWN",
        "quality_pass": False, "brain_media_pass": False,
        "release_authorization": "NONE", "spend_authorization": "NONE", "external_effects": "NONE",
        "reason_codes": sorted(reasons),
    }
    result = object.__new__(BQ04bTechnicalVerification)
    object.__setattr__(result, "_expected_json", critic._canonical(value))
    critic._Record.__init__(result, critic._canonical(value))
    return result
