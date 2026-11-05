"""Q2 preflight plus bounded Q4 T1 counterevidence, with no release authority."""

from __future__ import annotations

from dataclasses import dataclass

from ai_commerce.creative_quality import behavioral, critic, inspection_evidence


def _payload(preflight, evidence):
    q2 = critic.BQ02aPreflightCritique.from_json(preflight.to_json())
    q4 = inspection_evidence.validate_evidence(evidence)
    plan, asset = q2.to_dict(), q4.to_dict()
    reasons = []
    challenges = []
    for item in plan["items"]:
        challenges.append({"scope": "PLAN", "item_id": item["id"], "dimension": item["dimension"],
                           "severity": item["severity"], "assertion": item["assertion"],
                           "counterevidence": item["counterexample"], "grounding": item["grounding"],
                           "source_refs": item["source_refs"], "shot_id": item["shot_id"],
                           "intent_time_ms": item["intent_time_ms"], "asset_time": None, "region": None})
    for index, finding in enumerate(asset["findings"]):
        challenges.append({"scope": "ASSET", "finding_index": index,
                           "asset_ref": finding["asset_ref"], "asset_sha256": finding["asset_sha256"],
                           "evidence_state": finding["evidence_state"], "kind": finding["kind"],
                           "asset_time": finding["time"], "region": finding["region"],
                           "counterevidence": "A13 technical evidence cannot establish motion meaning, pronunciation, emotion, brands, truth or rights.",
                           "creative_severity": "UNASSESSED", "shot_id": None, "intent_time_ms": None})
    status = plan["status"]
    if status == "HOLD":
        reasons.append("P0_SOURCE_HOLD")
    if asset["status"] == "FAIL":
        status = "HOLD"
        reasons.append("A13_TECHNICAL_FAIL")
    elif asset["status"] == "UNKNOWN" and status not in ("HOLD", "REVISE"):
        status = "UNKNOWN"
    if asset["status"] == "UNKNOWN":
        reasons.append("A13_TECHNICAL_UNKNOWN")
    if plan["status"] == "REVISE":
        reasons.append("P1_PLAN_REVISION")
    if plan["status"] == "UNKNOWN":
        reasons.append("BLOCKING_PLAN_UNCERTAINTY")
    reasons.extend(asset["reason_codes"])
    if asset["status"] == "NOT_INSPECTED":
        reasons.append("PRE_RENDER_ONLY")
    return {"schema_version": "BQ02aAdversarialReview.v1", "preflight": plan, "preflight_digest": q2.digest,
            "technical_evidence": asset, "technical_evidence_digest": q4.digest,
            "plan_digest": plan["plan_digest"], "status": status,
            "review_scope": "PRE_RENDER_ONLY" if asset["status"] == "NOT_INSPECTED" else "PLAN_AND_T1_TECHNICAL",
            "reason_codes": sorted(set(reasons)), "challenges": challenges,
            "semantic_quality": "UNKNOWN", "human_calibration": "NOT_RUN",
            "coverage": asset["coverage"], "authority": critic.AUTHORITY}


@dataclass(frozen=True, slots=True, init=False)
class BQ02aAdversarialReview(critic._Record):
    _evidence: inspection_evidence.BQ02aT1TechnicalEvidence
    SCHEMA = "BQ02aAdversarialReview.v1"

    def __init__(self, raw):
        raise critic.Error("use adversarial_review or validated replay")

    def _validate(self, value):
        data = behavioral._fields(value, "schema_version preflight preflight_digest technical_evidence technical_evidence_digest plan_digest status review_scope reason_codes challenges semantic_quality human_calibration coverage authority")
        q2 = critic.BQ02aPreflightCritique.from_dict(data["preflight"])
        q2.verify_digest(data["preflight_digest"])
        q4 = inspection_evidence.validate_evidence(self._evidence)
        q4.verify_digest(data["technical_evidence_digest"])
        if critic._canonical(data) != critic._canonical(_payload(q2, q4)):
            raise critic.Error("review differs from independently validated source/asset records")

    @classmethod
    def from_dict(cls, value, *, root=None):
        data = critic._read(critic._canonical(value))
        if type(data) is not dict or "technical_evidence" not in data:
            raise critic.Error("review record required")
        q4 = inspection_evidence.BQ02aT1TechnicalEvidence.from_dict(data["technical_evidence"], root=root)
        return _bound(data, q4)

    @classmethod
    def from_json(cls, raw, *, root=None):
        return cls.from_dict(critic._read(raw), root=root)


def _bound(value, evidence):
    result = object.__new__(BQ02aAdversarialReview)
    object.__setattr__(result, "_evidence", evidence)
    critic._Record.__init__(result, critic._canonical(value))
    return result


def adversarial_review(preflight, *, preflight_digest, technical_evidence=None, technical_evidence_digest=None):
    """Fault precedence: P0/technical FAIL > P1 > blocking UNKNOWN > advisory.

    Expected pre-render uncertainty and P3 preferences do not imply defects.
    T1 PASS never removes source HOLDs or proves creative/semantic quality.
    """
    if type(preflight) is not critic.BQ02aPreflightCritique:
        raise critic.Error("native preflight critique required")
    q2 = critic.BQ02aPreflightCritique.from_json(preflight.to_json())
    q2.verify_digest(preflight_digest)
    if technical_evidence is None:
        if technical_evidence_digest is not None:
            raise critic.Error("absent technical evidence has no caller digest")
        q4 = inspection_evidence.not_inspected_t1()
    else:
        q4 = inspection_evidence.validate_evidence(technical_evidence)
        q4.verify_digest(technical_evidence_digest)
    return _bound(_payload(q2, q4), q4)
