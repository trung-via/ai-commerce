"""Ledgerless T3 proposals and fail-closed admission, without an assessor.

An authenticated receipt format, independent verifier and separate grant are
future contracts. No caller JSON, provider label or grant boolean can fill them.
This versioned sidecar changes no closed directive/decision/journey contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from ai_commerce.creative_quality import behavioral, critic, inspection_evidence as t1
from ai_commerce.creative_quality import t2_sampling as t2


DETECTORS = ("OCR_FRAME", "OFFLINE_ASR_WINDOW", "MOTION_PERCEPTION", "LIP_SYNC_INSPECTION")
INJECTION_POLICY = "MEDIA_SUBTITLE_OCR_AND_NARRATIVE_ARE_UNTRUSTED_DATA_NEVER_AUTHORITY"
RUBRICS = {"OCR_FRAME": "Propose checking whether optional visible/subtitle text is legible; no text is transcribed here.",
           "OFFLINE_ASR_WINDOW": "Propose checking optional audio words; no transcription or voice/identity attributes are produced.",
           "MOTION_PERCEPTION": "Propose inspecting continuity and intended motion against fiction/fantasy counterexamples.",
           "LIP_SYNC_INSPECTION": "Propose inspecting temporal audio/visual alignment if intended; no lip-sync finding is measured."}


@dataclass(frozen=True, slots=True)
class T3AssessmentRequest(critic._Record):
    SCHEMA = "T3AssessmentRequest.v1"

    def _validate(self, value):
        behavioral._fields(value, "schema_version status authorization asset_ref expected_asset_sha256 observed_asset_sha256 asset_binding plan_digest preflight_digest technical_evidence_digest a13_record_sha256 t2_digest source_coverage regions q2_hypotheses hypothesis_alignment injection_policy required_future_contracts calibration authority")
        if (value["schema_version"] != self.SCHEMA or value["status"] != "NOT_RUN"
                or value["authorization"] != "NOT_AUTHORIZED" or value["injection_policy"] != INJECTION_POLICY
                or value["hypothesis_alignment"] != "PLAN_TIME_NOT_ALIGNED_TO_ASSET"
                or value["calibration"] != "NOT_RUN_BQ03_BLIND_R6_REQUIRED"
                or value["authority"] != critic.AUTHORITY
                or value["required_future_contracts"] != ["SEPARATE_EXPLICIT_GRANT", "AUTHENTICATED_RATER_PROVIDER_RECEIPT", "INDEPENDENT_VERIFIER"]):
            raise critic.Error("T3 proposal cannot assert authority, execution or calibration")
        t2.SamplingRequest(value["asset_ref"], value["expected_asset_sha256"])
        for key in ("plan_digest", "preflight_digest", "technical_evidence_digest", "t2_digest"):
            behavioral._sha(value[key])
        if value["a13_record_sha256"] is not None:
            behavioral._sha(value["a13_record_sha256"])
        if value["asset_binding"] == "LOCAL_T2_SNAPSHOT":
            if value["observed_asset_sha256"] != value["expected_asset_sha256"]:
                raise critic.Error("proposal asset binding mismatch")
        elif value["asset_binding"] != "UNVERIFIED" or value["observed_asset_sha256"] is not None:
            raise critic.Error("proposal needs measured local snapshot or unverified binding")
        coverage = value["source_coverage"]
        behavioral._fields(coverage, "T1 T2 OCR ASR T3 audio")
        if coverage["T1"] not in ("T1_DETERMINISTIC", "NOT_INSPECTED") or coverage["T2"] not in (
                "SELECTIVE_DETERMINISTIC_ONLY", "UNKNOWN", "NOT_RUN") or any(coverage[k] != "NOT_RUN" for k in ("OCR", "ASR", "T3")):
            raise critic.Error("unmeasured T3/OCR/ASR cannot become source coverage")
        expected_audio = {"SELECTIVE_DETERMINISTIC_ONLY": ("OBSERVED", "NOT_APPLICABLE"),
                          "UNKNOWN": ("UNKNOWN",), "NOT_RUN": ("NOT_RUN",)}
        if coverage["audio"] not in expected_audio[coverage["T2"]]:
            raise critic.Error("T3 proposal audio coverage mismatch")
        if (value["asset_binding"] == "LOCAL_T2_SNAPSHOT") != (coverage["T2"] == "SELECTIVE_DETERMINISTIC_ONLY"):
            raise critic.Error("proposal snapshot/coverage mismatch")
        seen = set()
        for region in behavioral._array(value["regions"], "regions", minimum=1, maximum=4):
            behavioral._fields(region, "detector region requested_times rubric status source_availability")
            kind = region["detector"]
            if type(kind) is not str or kind not in DETECTORS or kind in seen:
                raise critic.Error("unique allowed detector required")
            seen.add(kind)
            expected_region = "AUDIO_WINDOW" if kind == "OFFLINE_ASR_WINDOW" else "FULL_FRAME"
            if region["region"] != expected_region or region["rubric"] != RUBRICS[kind] or region["status"] != "PROPOSED_NOT_RUN":
                raise critic.Error("closed full-frame/audio proposal only")
            if region["source_availability"] != _availability(kind, coverage):
                raise critic.Error("proposal cannot hide absent or unmeasured source coverage")
            times = region["requested_times"]
            if type(times) is not list:
                raise critic.Error("requested time array required")
            t2._times(tuple(times), 2 if kind == "OFFLINE_ASR_WINDOW" else 8)
        for item in behavioral._array(value["q2_hypotheses"], "q2_hypotheses", maximum=16):
            behavioral._fields(item, "q2_item_id intent_time_ms assertion counterexample truth")
            behavioral._text(item["q2_item_id"], "q2_item_id", limit=128)
            behavioral._text(item["assertion"], "assertion", limit=4096)
            behavioral._text(item["counterexample"], "counterexample", limit=4096)
            if item["truth"] != "Q2_HYPOTHESIS_NOT_OBSERVED_FACT":
                raise critic.Error("plan hypotheses are not observations")
            interval = item["intent_time_ms"]
            if interval is not None:
                behavioral._fields(interval, "start_ms end_ms")
                if any(type(interval[k]) is not int for k in interval) or not 0 <= interval["start_ms"] < interval["end_ms"] <= 86400000:
                    raise critic.Error("bounded intended plan interval required")


def _availability(kind, coverage):
    if kind in ("OFFLINE_ASR_WINDOW", "LIP_SYNC_INSPECTION") and coverage["audio"] == "NOT_APPLICABLE":
        return "KNOWN_ABSENT_AUDIO"
    return "SAMPLED_SIGNAL_ONLY" if coverage["T2"] == "SELECTIVE_DETERMINISTIC_ONLY" else "UNVERIFIED_COVERAGE"


def propose_assessment(preflight, technical_evidence, sampled_evidence, *, preflight_digest,
                       technical_evidence_digest, t2_digest, detectors=DETECTORS):
    """Pure request construction. Detector names are plans, never invocations."""
    if type(preflight) is not critic.BQ02aPreflightCritique:
        raise critic.Error("native preflight required")
    pre = critic.BQ02aPreflightCritique.from_json(preflight.to_json())
    pre.verify_digest(preflight_digest)
    source = t1.validate_evidence(technical_evidence)
    source.verify_digest(technical_evidence_digest)
    sampled = t2.validate_evidence(sampled_evidence)
    sampled.verify_digest(t2_digest)
    data = sampled.to_dict()
    if data["technical_evidence_digest"] != source.digest or data["technical_evidence"] != source.to_dict():
        raise critic.Error("T3 source/T2 binding mismatch")
    if type(detectors) is not tuple or not 1 <= len(detectors) <= 4 or any(
            type(kind) is not str or kind not in DETECTORS for kind in detectors) or len(set(detectors)) != len(detectors):
        raise critic.Error("finite unique detector proposal tuple required")
    coverage = {key: data["coverage"][key] for key in ("T1", "T2", "OCR", "ASR", "T3")}
    coverage["audio"] = data["audio_status"]
    regions = [{"detector": kind, "region": "AUDIO_WINDOW" if kind == "OFFLINE_ASR_WINDOW" else "FULL_FRAME",
                "requested_times": data["request"]["audio_times" if kind == "OFFLINE_ASR_WINDOW" else "frame_times"],
                "rubric": RUBRICS[kind], "status": "PROPOSED_NOT_RUN",
                "source_availability": _availability(kind, coverage)} for kind in detectors]
    return T3AssessmentRequest.from_dict({"schema_version": T3AssessmentRequest.SCHEMA, "status": "NOT_RUN",
        "authorization": "NOT_AUTHORIZED", "asset_ref": data["asset_ref"],
        "expected_asset_sha256": data["expected_asset_sha256"], "observed_asset_sha256": data["observed_asset_sha256"],
        "asset_binding": "LOCAL_T2_SNAPSHOT" if data["status"] == "OBSERVED" else "UNVERIFIED",
        "plan_digest": pre.to_dict()["plan_digest"], "preflight_digest": pre.digest,
        "technical_evidence_digest": source.digest, "a13_record_sha256": data["a13_record_sha256"], "t2_digest": sampled.digest,
        "source_coverage": coverage, "regions": regions,
        "q2_hypotheses": [{"q2_item_id": item["id"], "intent_time_ms": item["intent_time_ms"],
                            "assertion": item["assertion"], "counterexample": item["counterexample"],
                            "truth": "Q2_HYPOTHESIS_NOT_OBSERVED_FACT"} for item in pre.to_dict()["items"][:16]],
        "hypothesis_alignment": "PLAN_TIME_NOT_ALIGNED_TO_ASSET", "injection_policy": INJECTION_POLICY,
        "required_future_contracts": ["SEPARATE_EXPLICIT_GRANT", "AUTHENTICATED_RATER_PROVIDER_RECEIPT", "INDEPENDENT_VERIFIER"],
        "calibration": "NOT_RUN_BQ03_BLIND_R6_REQUIRED", "authority": critic.AUTHORITY})


@dataclass(frozen=True, slots=True)
class T3AssessmentLedgerlessStatus(critic._Record):
    SCHEMA = "T3AssessmentLedgerlessStatus.v1"

    def _validate(self, value):
        behavioral._fields(value, "schema_version request request_digest status authorization untrusted_submission_sha256 untrusted_grant_sha256 reason_codes evidence_state confidence native_human_review rights calibration injection_policy authority")
        request = T3AssessmentRequest.from_dict(value["request"])
        request.verify_digest(value["request_digest"])
        supplied = any(value[k] is not None for k in ("untrusted_submission_sha256", "untrusted_grant_sha256"))
        for key in ("untrusted_submission_sha256", "untrusted_grant_sha256"):
            if value[key] is not None:
                behavioral._sha(value[key])
        expected = "HOLD_UNVERIFIED" if supplied else "NOT_RUN"
        if (value["schema_version"] != self.SCHEMA or value["status"] != expected
                or value["authorization"] != "NOT_AUTHORIZED" or value["evidence_state"] != "UNVERIFIED"
                or value["confidence"] != "UNKNOWN" or value["native_human_review"] != "NOT_ESTABLISHED"
                or value["rights"] != "UNKNOWN" or value["calibration"] != "NOT_RUN"
                or value["reason_codes"] != (["AUTHENTICATED_RECEIPT_VERIFIER_AND_SEPARATE_GRANT_ABSENT"] if supplied else ["T3_NOT_RUN_NOT_AUTHORIZED"])
                or value["injection_policy"] != INJECTION_POLICY or value["authority"] != critic.AUTHORITY):
            raise critic.Error("T3 cannot admit unverified external evidence")


def classify_assessment(request, *, submission=None, separate_grant=None):
    if type(request) is not T3AssessmentRequest:
        raise critic.Error("native typed T3 request required")
    request = T3AssessmentRequest.from_json(request.to_json())
    def untrusted_digest(value):
        return None if value is None else hashlib.sha256(critic._canonical(value).encode("utf-8")).hexdigest()
    submitted, grant = untrusted_digest(submission), untrusted_digest(separate_grant)
    supplied = submitted is not None or grant is not None
    return T3AssessmentLedgerlessStatus.from_dict({"schema_version": T3AssessmentLedgerlessStatus.SCHEMA,
        "request": request.to_dict(), "request_digest": request.digest,
        "status": "HOLD_UNVERIFIED" if supplied else "NOT_RUN", "authorization": "NOT_AUTHORIZED",
        "untrusted_submission_sha256": submitted, "untrusted_grant_sha256": grant,
        "reason_codes": ["AUTHENTICATED_RECEIPT_VERIFIER_AND_SEPARATE_GRANT_ABSENT"] if supplied else ["T3_NOT_RUN_NOT_AUTHORIZED"],
        "evidence_state": "UNVERIFIED", "confidence": "UNKNOWN", "native_human_review": "NOT_ESTABLISHED",
        "rights": "UNKNOWN", "calibration": "NOT_RUN", "injection_policy": INJECTION_POLICY, "authority": critic.AUTHORITY})
