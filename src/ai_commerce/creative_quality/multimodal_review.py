"""Strict Q2/T1/T2 composition and an unrun T3 proposal; never a quality score."""

from __future__ import annotations

from dataclasses import dataclass

from ai_commerce.creative_quality import critic, inspection_evidence as t1, quality_review as qr
from ai_commerce.creative_quality import t2_sampling as t2, t3_assessment as t3


def _payload(review, sampled, assessment):
    review._validate(review.to_dict())
    t2.validate_evidence(sampled)
    assessment = t3.T3AssessmentLedgerlessStatus.from_json(assessment.to_json())
    original, signals, proposal = review.to_dict(), sampled.to_dict(), assessment.to_dict()
    request = proposal["request"]
    if (original["technical_evidence_digest"] != signals["technical_evidence_digest"]
            or original["technical_evidence"] != signals["technical_evidence"]
            or request["t2_digest"] != sampled.digest or request["preflight_digest"] != original["preflight_digest"]
            or request["plan_digest"] != original["plan_digest"]
            or request["technical_evidence_digest"] != original["technical_evidence_digest"]):
        raise critic.Error("multimodal source/plan/asset digest chain mismatch")
    expected = t3.propose_assessment(critic.BQ02aPreflightCritique.from_dict(original["preflight"]), sampled._source,
        sampled, preflight_digest=original["preflight_digest"], technical_evidence_digest=sampled._source.digest,
        t2_digest=sampled.digest, detectors=tuple(r["detector"] for r in request["regions"]))
    if expected.to_dict() != request:
        raise critic.Error("T3 proposal differs from original hypotheses/coverage")
    status, reasons = original["status"], list(original["reason_codes"])
    if proposal["status"] == "HOLD_UNVERIFIED":
        status = "HOLD"
        reasons.append("T3_SUBMISSION_UNVERIFIED")
    if signals["status"] != "OBSERVED":
        reasons.append("T2_COVERAGE_MISSING")
    if status not in ("HOLD", "REVISE"):
        status = "UNKNOWN"
    reasons.append("PERCEPTUAL_SEMANTIC_HUMAN_COVERAGE_NOT_ESTABLISHED")
    return {"schema_version": "BQ02bMultimodalReview.v1", "original_review": original, "original_review_digest": review.digest,
            "plan_digest": original["plan_digest"], "preflight_digest": original["preflight_digest"],
            "technical_evidence_digest": original["technical_evidence_digest"],
            "a13_record_sha256": signals["a13_record_sha256"], "observed_asset_sha256": signals["observed_asset_sha256"],
            "sampled_evidence": signals, "t2_digest": sampled.digest,
            "t3_assessment": proposal, "t3_digest": assessment.digest, "status": status,
            "reason_codes": sorted(set(reasons + signals["reason_codes"])),
            "measured_findings": {"frames": signals["frames"], "audio": signals["audio"], "audio_status": signals["audio_status"]},
            "confidence": {"T1": "A13_TECHNICAL_ONLY", "T2": "MEASURED_SIGNAL_ONLY" if signals["status"] == "OBSERVED" else "UNKNOWN",
                           "T3": "UNKNOWN", "semantic": "UNKNOWN"},
            "coverage": signals["coverage"], "missing_coverage": ["UNSAMPLED_TIMES", "UNSAMPLED_STREAMS", "SPATIAL_ROI",
                "OCR", "ASR", "PERCEPTION", "HUMAN_BLIND_R6_CALIBRATION", "R1_R8_NOT_ALL_MEASURED"],
            "metric_provenance": {"detector_version": t2.DETECTOR, "sampling_profile": t2.PROFILE,
                "revalidation": "Explicit Path root; local T1 replay then locked snapshot A13 + fixed T2 tools; exact record equality"},
            "counterexamples": [t2.COUNTEREXAMPLE, "A fictional impossible action may be intentional; a source hypothesis is not asset truth.",
                "A technically decoded asset can contain false product claims or unlicensed material; P0 remains HOLD."],
            "p3_route": "BRAIN_ONLY", "semantic_quality": "UNKNOWN", "human_calibration": "NOT_RUN",
            "rights": "UNKNOWN", "release_authorization": "NONE", "spend_authorization": "NONE", "authority": critic.AUTHORITY}


@dataclass(frozen=True, slots=True, init=False)
class BQ02bMultimodalReview(critic._Record):
    _review: qr.BQ02aAdversarialReview
    _sampled: t2.BQ02bT2SelectiveEvidence
    SCHEMA = "BQ02bMultimodalReview.v1"

    def __init__(self, raw):
        raise critic.Error("use multimodal_review or explicit local replay")

    def _validate(self, value):
        if type(value) is not dict or "t3_assessment" not in value:
            raise critic.Error("multimodal review required")
        assessment = t3.T3AssessmentLedgerlessStatus.from_dict(value["t3_assessment"])
        if critic._canonical(value) != critic._canonical(_payload(self._review, self._sampled, assessment)):
            raise critic.Error("multimodal result differs from native evidence chain")

    @classmethod
    def from_dict(cls, value, *, root=None):
        data = critic._read(critic._canonical(value))
        if type(data) is not dict or not {"sampled_evidence", "original_review"} <= set(data):
            raise critic.Error("multimodal review required")
        sampled = t2.BQ02bT2SelectiveEvidence.from_dict(data["sampled_evidence"], root=root)
        original = data["original_review"]
        # Reuse the locally reauthenticated T1; avoid a second mutable-original
        # interpretation of the evidence used by the snapshot probes.
        pre = critic.BQ02aPreflightCritique.from_dict(original["preflight"])
        review = qr.adversarial_review(pre, preflight_digest=pre.digest, technical_evidence=sampled._source,
                                      technical_evidence_digest=sampled._source.digest)
        if review.to_dict() != original:
            raise critic.Error("original BQ-02a review replay mismatch")
        return _bound(data, review, sampled)

    @classmethod
    def from_json(cls, raw, *, root=None):
        return cls.from_dict(critic._read(raw), root=root)


def _bound(data, review, sampled):
    result = object.__new__(BQ02bMultimodalReview)
    object.__setattr__(result, "_review", review)
    object.__setattr__(result, "_sampled", sampled)
    critic._Record.__init__(result, critic._canonical(data))
    return result


def multimodal_review(original_review, sampled_evidence, *, original_review_digest, t2_digest, assessment=None):
    if type(original_review) is not qr.BQ02aAdversarialReview:
        raise critic.Error("native original BQ-02a review required")
    original_review._validate(original_review.to_dict())
    original_review.verify_digest(original_review_digest)
    sampled = t2.validate_evidence(sampled_evidence)
    sampled.verify_digest(t2_digest)
    if assessment is None:
        pre = critic.BQ02aPreflightCritique.from_dict(original_review.to_dict()["preflight"])
        request = t3.propose_assessment(pre, sampled._source, sampled, preflight_digest=pre.digest,
                                      technical_evidence_digest=sampled._source.digest, t2_digest=sampled.digest)
        assessment = t3.classify_assessment(request)
    if type(assessment) is not t3.T3AssessmentLedgerlessStatus:
        raise critic.Error("native T3 ledgerless status required")
    return _bound(_payload(original_review, sampled, assessment), original_review, sampled)
