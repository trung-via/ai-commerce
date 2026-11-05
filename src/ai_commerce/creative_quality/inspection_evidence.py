"""Q4 T1 bridge to the existing A13 C08 local-byte inspector.

Only inspect_local_t1 (or explicit local reinspection of serialized evidence)
can create an asset-bearing native record. There is no hand-entered finding
ingress. The in-memory binding is not a signature or a durable custody ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative_quality import behavioral, critic


PROFILE = "bq02a-t1/a13-c08-1.0/v1"
REGION = {"availability": "UNAVAILABLE", "reason": "A13_HAS_NO_SPATIAL_REGION"}
NO_TIME = {"availability": "UNAVAILABLE", "reason": "NO_A13_MEASURED_SEGMENT"}
LIMITS = {"creative_quality": "UNKNOWN", "perceptual_quality": "UNKNOWN", "semantic_quality": "UNKNOWN",
          "product_truth": "UNKNOWN", "copyright_rights": "UNKNOWN", "accounts": "UNKNOWN", "publication": "NONE"}


def _validated_report(value):
    """A13 remains authoritative for schema, hash, facts and diagnostic checks."""
    critic._canonical(value)
    try:
        report = a13.MediaReleaseInspection(value)
    except (a13.MediaInspectionError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        raise critic.Error("invalid authoritative A13 receipt: " + str(exc)[:100]) from exc
    if report.to_dict()["inspector_version"] != "a13-c08/1.0":
        raise critic.Error("unsupported A13 inspector version")
    return report


def _projection(report):
    if report is None:
        return {"schema_version": "BQ02aT1TechnicalEvidence.v1", "scope": "ASSET_TECHNICAL_ONLY",
                "status": "NOT_INSPECTED", "verified": False, "evidence_source": "A13_C08",
                "verifier_profile": PROFILE, "a13_record_sha256": None, "a13_report": None,
                "findings": [], "reason_codes": ["NO_LOCAL_ASSET_EXAMINED"],
                "coverage": {"T1": "NOT_INSPECTED", "T2": "T2_NOT_RUN", "T3": "T3_NOT_RUN"},
                "limits_of_evidence": LIMITS, "authority": critic.AUTHORITY}
    data = _validated_report(report.to_dict()).to_dict()
    findings, reasons = [], []
    for asset in data["assets"]:
        verified = (asset["status"] == "PASS" and asset["full_decode"] is True
                    and asset["observed_sha256"] == asset["expected_sha256"]
                    and type(asset["size_bytes"]) is int and asset["size_bytes"] > 0)
        state = "OBSERVED" if verified else asset["status"]
        reasons.extend(asset["reason_codes"])

        def finding(kind, status, *, segment=None, diagnostics=None, reason_codes=None):
            return {"asset_ref": asset["asset_ref"], "expected_sha256": asset["expected_sha256"],
                    "observed_sha256": asset["observed_sha256"],
                    "asset_sha256": asset["observed_sha256"] if verified else None,
                    "size_bytes": asset["size_bytes"], "kind": kind, "evidence_state": status,
                    "asset_verified": verified, "evidence_source": "A13_C08",
                    "inspector_version": data["inspector_version"], "verifier_profile": PROFILE,
                    "full_decode": asset["full_decode"],
                    "facts": asset["facts"] if verified else None,
                    "diagnostic": diagnostics, "time": NO_TIME if segment is None else {
                        "availability": "A13_MEASURED_SEGMENT", **segment},
                    "region": REGION, "reason_codes": asset["reason_codes"] if reason_codes is None else reason_codes,
                    "meaning": "TECHNICAL_OBSERVATION_ONLY"}

        findings.append(finding("TECHNICAL_SUMMARY", state))
        for kind, diagnostic in asset["diagnostics"].items():
            diag_state = diagnostic["status"] if verified else "UNAVAILABLE"
            diag_reasons = diagnostic["reason_codes"] if verified else ["ASSET_NOT_VERIFIED"]
            if verified and kind == "silence" and not any(s["kind"] == "audio" for s in asset["facts"]["streams"]):
                diag_state, diag_reasons = "NOT_APPLICABLE", ["KNOWN_ABSENT_AUDIO"]
            reasons.extend(diagnostic["reason_codes"])
            segments = diagnostic["segments"] if verified and diag_state == "OBSERVED" else []
            metadata = {k: diagnostic[k] for k in ("status", "parameters", "reason_codes")}
            if not segments:
                findings.append(finding(kind.upper(), diag_state, diagnostics=metadata, reason_codes=diag_reasons))
            for segment in segments:
                # A13 supplies exact rational times and stream indices. No
                # shot/ROI, object, emotion, pronunciation or bbox is inferred.
                if Fraction(segment["start_seconds"]) < 0:
                    raise critic.Error("negative A13 segment")
                findings.append(finding(kind.upper(), "OBSERVED", segment=segment,
                                        diagnostics=metadata, reason_codes=[]))
    status = data["status"]
    if status == "PASS" and any(d["status"] == "UNKNOWN" for a in data["assets"] for d in a["diagnostics"].values()):
        status = "UNKNOWN"
    return {"schema_version": "BQ02aT1TechnicalEvidence.v1", "scope": "ASSET_TECHNICAL_ONLY",
            "status": status, "verified": status == "PASS", "evidence_source": "A13_C08",
            "verifier_profile": PROFILE, "a13_record_sha256": data["record_sha256"], "a13_report": data,
            "findings": findings, "reason_codes": sorted(set(reasons)),
            "coverage": {"T1": "T1_DETERMINISTIC", "T2": "T2_NOT_RUN", "T3": "T3_NOT_RUN"},
            "limits_of_evidence": LIMITS, "authority": critic.AUTHORITY}


@dataclass(frozen=True, slots=True, init=False)
class BQ02aT1TechnicalEvidence(critic._Record):
    _receipt: a13.MediaReleaseInspection | None
    SCHEMA = "BQ02aT1TechnicalEvidence.v1"

    def __init__(self, raw):
        raise critic.Error("use inspect_local_t1, not_inspected_t1 or explicit local replay")

    def _validate(self, value):
        expected = _projection(self._receipt)
        if critic._canonical(value) != critic._canonical(expected):
            raise critic.Error("T1 findings differ from authoritative A13 recomputation")

    @classmethod
    def from_dict(cls, value, *, root=None):
        data = critic._read(critic._canonical(value))
        if type(data) is not dict or "a13_report" not in data:
            raise critic.Error("T1 record required")
        report = data["a13_report"]
        if report is None:
            return _bound(data, None)
        if not isinstance(root, Path):
            raise critic.Error("LOCAL_REINSPECTION_REQUIRED: explicit Path root")
        original = _validated_report(report).to_dict()
        assets = []
        for asset in original["assets"]:
            source = asset["source"]
            assets.append(a13.ReleaseAsset(asset["asset_ref"], asset["expected_sha256"], asset["mime_type"],
                source.get("manifest_ref"), source.get("manifest_sha256")))
        # Actual local tool invocation, not acceptance of caller-authored facts.
        observed = a13.inspect_media_release(root, assets,
            diagnostics=a13.DiagnosticOptions(**original["diagnostics_requested"]),
            limits=a13.InspectionLimits(**original["limits"]))
        if critic._canonical(observed.to_dict()) != critic._canonical(original):
            raise critic.Error("local A13 replay differs from supplied receipt")
        return _bound(data, observed)

    @classmethod
    def from_json(cls, raw, *, root=None):
        return cls.from_dict(critic._read(raw), root=root)


def _bound(value, receipt):
    if receipt is not None and type(receipt) is not a13.MediaReleaseInspection:
        raise critic.Error("native A13 receipt required")
    result = object.__new__(BQ02aT1TechnicalEvidence)
    object.__setattr__(result, "_receipt", receipt)
    critic._Record.__init__(result, critic._canonical(value))
    return result


def validate_evidence(value):
    """Independently replay native projection and A13 validation, without I/O."""
    if type(value) is not BQ02aT1TechnicalEvidence:
        raise critic.Error("native BQ02aT1TechnicalEvidence required")
    value._validate(value.to_dict())
    return value


def not_inspected_t1():
    return _bound(_projection(None), None)


def inspect_local_t1(root, assets, *, diagnostics=None, limits=None):
    """Explicit opt-in local I/O. Delegate custody, tools and hashes to A13."""
    if not isinstance(root, Path):
        raise critic.Error("explicit Path root required")
    if type(assets) not in (list, tuple) or not assets or any(type(a) is not a13.ReleaseAsset for a in assets):
        raise critic.Error("explicit native ReleaseAsset list required")
    if diagnostics is not None and type(diagnostics) is not a13.DiagnosticOptions:
        raise critic.Error("native DiagnosticOptions required")
    if limits is not None and type(limits) is not a13.InspectionLimits:
        raise critic.Error("native InspectionLimits required")
    report = a13.inspect_media_release(root, assets, diagnostics=diagnostics, limits=limits)
    return _bound(_projection(report), report)
