"""C10 opt-in, read-only A11 consumer with complete immutable archive custody.

A11 has no market field or per-observation creative attribution. The market is
an explicit A17 Human declaration; hypothesis-to-creative correlation is checked
but is never presented as provider-verified attribution. These limits survive
into E06. No A11 registry or A09 source is opened or changed here.
"""
from __future__ import annotations

import re

from ai_commerce.analytics.schemas import CONTEXT, VERIFICATION
from ai_commerce.learning import LearningAdvisory

from .schemas import (
    MAX_BYTES, MAX_EVIDENCE, bounded, detached, digest, private_text,
    require, scope_value, seal,
)
from .variants import checked_batch, source_pin


def reject_claims(value: str) -> None:
    """Reject explicit unsupported trust/causality assertions in proposal prose.

    Freeform language is not an evidence validator; fixed inference/status fields
    and source checks are authoritative regardless of the wording supplied.
    """
    private_text(value)
    require(not re.search(r"\b(?:proven|verified|guaranteed|established)\s+(?:causal\s+)?(?:lift|uplift|trend)\b|"
                          r"\bcausal (?:lift|uplift)\s+(?:of|is|was|=)|\bcauses?\s+(?:a\s+)?(?:lift|uplift)|"
                          r"\bprovider[- ](?:verified|attested)\s+(?:metrics?|lift|results?)", value, re.I),
            "UNSUPPORTED_CAUSAL_OR_PROVIDER_CLAIM")


def _advisory(value, expected_export_id):
    require(type(expected_export_id) is str
            and re.fullmatch(r"a11-advisory-[a-f0-9]{64}", expected_export_id) is not None,
            "ADVISORY_PIN_REQUIRED")
    # Even a typed object is reconstructed. Duck-typed/fake advisory objects,
    # altered dataclass internals and valid but truncated prefix exports fail.
    if type(value) is LearningAdvisory:
        raw = value.to_bytes()
    elif type(value) is bytes:
        raw = value
    else:
        require(type(value) is dict, "A11_ADVISORY_REQUIRED")
        raw = bounded(value)
    require(0 < len(raw) <= MAX_BYTES, "ADVISORY_SIZE_LIMIT")
    return LearningAdvisory.from_bytes(raw, expected_id=expected_export_id)


def consume_learning_advisory(advisory, *, registry, batch: dict, source_id: str,
                              expected_export_id: str, learning_key: str, expected_revision: int,
                              scope: dict, opt_in: bool = False) -> dict:
    """Validate every archived event, then pin one key's complete revision history.

    Scope must equal the batch's explicit scope. Every selected historical fact
    must match the account/channel/window/timezone/cohort and metric definition.
    Unsupported/mismatched facts are rejected as a whole, never filtered into a
    winner. Original roles, negative evidence, unknowns and confounders remain in
    the copied archive and context. Numeric sign does not determine evidence role.
    """
    require(opt_in is True, "LEARNING_OPT_IN_REQUIRED")
    require(type(expected_revision) is int and 1 <= expected_revision <= 1000000,
            "LEARNING_REVISION_INVALID")
    require(type(learning_key) is str and 1 <= len(learning_key) <= 128, "LEARNING_KEY_INVALID")
    batch = checked_batch(batch, registry)
    scope = scope_value(scope)
    require(batch["scope"] is not None and scope == batch["scope"], "SOURCE_SCOPE_MISMATCH")
    pin, _ = source_pin(batch, source_id)
    checked = _advisory(advisory, expected_export_id)
    archive = checked.to_dict()
    require(type(archive["event_count"]) is int and all(
        type(e["sequence"]) is int and type(e["entry"]["revision"]["revision"]) is int
        for e in archive["entries"]), "LEARNING_HISTORY_INTEGER_REQUIRED")
    selected = [event for event in archive["entries"] if event["entry"]["revision"]["learning_key"] == learning_key]
    require(bool(selected), "LEARNING_KEY_NOT_FOUND")
    latest = selected[-1]["entry"]
    require(latest["revision"]["revision"] == expected_revision, "STALE_LEARNING_REVISION")
    require(latest["record"]["status"] != "RETIRED", "RETIRED_LEARNING")
    expected_context = {key: scope[key] for key in CONTEXT}
    creative_ref = {"creative_id": pin["id"], "version": str(pin["revision"]), "content_sha256": pin["sha256"]}
    history, observations, metric = [], [], None
    for event in selected:
        entry = event["entry"]
        hyp, record, rev = entry["hypothesis"], entry["record"], entry["revision"]
        require(hyp["creative_ref"] == creative_ref, "ADVISORY_CREATIVE_SOURCE_MISMATCH")
        require({key: hyp[key] for key in CONTEXT} == expected_context, "ADVISORY_CONTEXT_MISMATCH")
        reject_claims(hyp["statement"])
        reject_claims(hyp["expected_outcome"])
        facts = {digest(f["observation_ref"]): f for f in entry["observation_sources"]}
        reports = {p["report_ref"]["report_id"]: p["body"] for p in entry["report_sources"]}
        for fact in facts.values():
            require(fact["context"] == expected_context, "EVIDENCE_CONTEXT_MISMATCH")
            if metric is None:
                metric = fact["metric"]
            require(fact["metric"] == metric and metric["cohort"] == scope["cohort"]
                    and metric["platform"] == scope["platform"], "INCOMPATIBLE_METRIC_DEFINITION")
            require(fact["source"]["source_status"] == VERIFICATION, "UNVERIFIED_PROVIDER_METRICS")
        for report in reports.values():
            require(report["metric"] == metric and {key: report[key] for key in CONTEXT} == expected_context,
                    "INCOMPATIBLE_REPORT_CONTEXT")
        for evidence in entry["evidence"]:
            reject_claims(evidence["observed_outcome"])
            fact = facts[digest(evidence["observation_ref"])]
            report_ref = evidence.get("report_ref")
            measurement = None if report_ref is None else reports[report_ref["report_id"]]["measurement"]
            available = evidence["evidence_state"] == "AVAILABLE"
            observations.append({
                "revision": rev["revision"], "revision_id": rev["revision_id"],
                "evidence": evidence, "source_fact": fact,
                "report_measurement": measurement,
                "status": "AVAILABLE" if available else "UNKNOWN",
                # Keep the raw source fact as well as report-level missingness.
                "value": (fact["value"] if measurement is None else measurement["value"]) if available else None,
                "denominator": None if measurement is None else measurement["denominator"],
                "provenance": "SYNTHETIC_FIXTURE" if fact["source"]["source_kind"] == "SYNTHETIC_FIXTURE"
                              else "USER_SUPPLIED",
            })
            require(len(observations) <= MAX_EVIDENCE, "LEARNING_CONTEXT_CAPACITY")
        history.append({"sequence": event["sequence"], "event_hash": event["event_hash"],
                        "entry_sha256": digest(entry), "hypothesis": hyp, "record": record, "revision": rev})
    require(metric is not None, "METRIC_CONTEXT_MISSING")
    archive_sha = digest(archive)
    archive_ref = f"creative-scaling/advisories/{archive_sha}.json"
    body = {"schema_version": "CreativeLearningContext.v1", "source": pin, "scope": scope,
            "export_id": checked.export_id, "archive_ref": archive_ref, "archive_sha256": archive_sha,
            "event_count": archive["event_count"], "head_event_hash": archive["head_event_hash"],
            "learning_key": learning_key, "revision": expected_revision,
            "revision_id": latest["revision"]["revision_id"], "metric": metric,
            "history": history, "observations": observations, "source_status": VERIFICATION,
            "market_basis": "USER_SUPPLIED_NOT_RECORDED_BY_A11",
            "creative_correlation": "HYPOTHESIS_PIN_ONLY_NOT_PROVIDER_ATTRIBUTION",
            "inference": "ADVISORY_ONLY", "causal_lift": None, "trend": "NOT_ESTABLISHED",
            "provider_metrics_verified": False, "original_limitations": archive["limitations"]}
    context = seal(body)
    context_ref = f"creative-scaling/learning/{digest(context)}.json"
    # Read-only: references describe the deterministic archive/context custody
    # used by E06. This consumer itself creates no files and changes no source.
    return detached({"context": context, "ref": context_ref, "sha256": digest(context)})
