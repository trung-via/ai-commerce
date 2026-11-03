"""E06 next-brief suggestions and immutable Human adoption, never execution.

Accepting a suggestion records a decision only. Generation, spending, posting,
provider observation and retry are absent from this module's interfaces.
"""
from __future__ import annotations

import re

from ai_commerce.creative import intake_brief

from .learning_bridge import _advisory, consume_learning_advisory, reject_claims
from .schemas import (
    FEEDBACK_PROPOSAL_SCHEMA, detached, digest, private_text, put_record,
    read_record, require, seal, validate, window,
)
from .variants import brief_snapshot, checked_batch, load_variant_batch, source_pin


def proposal_ref(proposal_id: str, revision: int = 1) -> str:
    require(type(proposal_id) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", proposal_id) is not None,
            "PROPOSAL_ID_INVALID")
    require(type(revision) is int and revision in (1, 2), "PROPOSAL_REVISION_INVALID")
    return f"creative-scaling/proposals/{proposal_id}/r{revision}.json"


def _build(batch, *, registry, source_id, advisory, expected_export_id, learning_key,
           expected_revision, scope, proposal_id, suggestion, hypothesis, comparison, opt_in):
    proposal_ref(proposal_id)
    require(type(suggestion) is str and 0 < len(suggestion.strip()) <= 4096
            and type(hypothesis) is str and 0 < len(hypothesis.strip()) <= 2048, "PROPOSAL_TEXT_INVALID")
    reject_claims(suggestion)
    reject_claims(hypothesis)
    comparison = detached(comparison)
    require(type(comparison) is dict and set(comparison) == {"after_window", "metric", "cohort", "timezone"},
            "COMPARISON_FIELDS_INVALID")
    after_start, after_end = window(comparison["after_window"])
    batch = checked_batch(batch, registry)
    learning = consume_learning_advisory(
        advisory, registry=registry, batch=batch, source_id=source_id,
        expected_export_id=expected_export_id, learning_key=learning_key,
        expected_revision=expected_revision, scope=scope, opt_in=opt_in)
    context = learning["context"]
    scope, metric, pin = context["scope"], context["metric"], context["source"]
    before_start, before_end = window(scope["measurement_window"])
    require(after_start >= before_end and after_end - after_start == before_end - before_start,
            "NONCOMPARABLE_WINDOWS")
    require(comparison["metric"] == metric and comparison["cohort"] == scope["cohort"]
            and comparison["timezone"] == scope["timezone"], "INCOMPATIBLE_COMPARISON_CONTEXT")
    _, source = source_pin(batch, source_id)
    source_brief = read_record(registry, source["brief"]["ref"])
    # Use the A03 brief API; preserve optional CTA/product/custom constraints.
    identity = digest({"source": pin, "advisory": expected_export_id, "suggestion": suggestion,
                       "hypothesis": hypothesis, "comparison": comparison})
    next_brief = intake_brief({**source_brief, "brief_id": "a17-next:" + identity,
                               "objective": suggestion, "prompt": suggestion,
                               "source_refs": list(dict.fromkeys(source_brief.get("source_refs", [])
                                                                  + [source_id, expected_export_id]))})
    next_pin, next_brief = brief_snapshot(next_brief)
    observations = context["observations"]
    latest = [o for o in observations if o["revision"] == expected_revision]
    missing = ["AFTER_WINDOW_NOT_OBSERVED", "PROVIDER_VERIFICATION_UNAVAILABLE", "A11_MARKET_NOT_RECORDED",
               "CREATIVE_ATTRIBUTION_NOT_PROVIDER_VERIFIED"]
    if not latest or any(o["status"] == "UNKNOWN" for o in latest):
        missing.append("BEFORE_EVIDENCE_UNKNOWN")
    if metric["denominator"] is not None and any(o["denominator"] is None for o in latest):
        missing.append("DENOMINATOR_UNKNOWN")
    if scope["cohort"] is None:
        missing.append("COHORT_UNKNOWN")
    advisory_pin = {key: context[key] for key in (
        "export_id", "archive_ref", "archive_sha256", "event_count", "head_event_hash",
        "learning_key", "revision", "revision_id")}
    advisory_pin.update(context_ref=learning["ref"], context_sha256=learning["sha256"])
    body = {
        "schema_version": FEEDBACK_PROPOSAL_SCHEMA, "proposal_id": proposal_id, "revision": 1, "parent": None,
        "batch_id": batch["batch_id"], "batch_sha256": batch["record_sha256"],
        "master_id": batch["master"]["master_id"], "source": pin,
        "source_brief": source["brief"], "next_brief": next_pin, "scope": scope, "advisory": advisory_pin,
        "evidence_ids": sorted({o["evidence"]["evidence_id"] for o in observations}),
        "negative_evidence_ids": sorted({o["evidence"]["evidence_id"] for o in observations
                                         if o["evidence"]["role"] == "COUNTEREVIDENCE"}),
        "suggestion": suggestion, "hypothesis": hypothesis,
        "uncertainty": context["history"][-1]["record"]["uncertainty"],
        "comparison": {
            "before_window": scope["measurement_window"], "after_window": comparison["after_window"],
            "timezone": scope["timezone"], "cohort": scope["cohort"], "metric": metric,
            "before": [{"evidence_id": o["evidence"]["evidence_id"], "status": o["status"],
                        "value": o["value"], "denominator": o["denominator"], "provenance": o["provenance"]}
                       for o in latest],
            "after": {"status": "UNKNOWN", "value": None, "denominator": None},
            "causal_lift": None, "trend": "NOT_ESTABLISHED"},
        "missing_data": missing, "provenance": "DETERMINISTIC_LOCAL_PROPOSAL",
        "adoption": {"decision": "PENDING", "actor": None, "note": None},
        "inference": "HYPOTHESIS_ONLY", "generation_authorization": "REQUIRED_SEPARATELY",
        "generation_performed": False, "publication_status": "NOT_ATTEMPTED",
    }
    proposal = validate(FEEDBACK_PROPOSAL_SCHEMA, seal(body))
    blobs = [(context["archive_ref"], _advisory(advisory, expected_export_id).to_dict()),
             (learning["ref"], context), (next_pin["ref"], next_brief)]
    return proposal, blobs


def propose_next_creative(batch: dict, *, registry, source_id: str, advisory,
                          expected_export_id: str, learning_key: str, expected_revision: int,
                          scope: dict, proposal_id: str, suggestion: str, hypothesis: str,
                          comparison: dict, opt_in: bool = False) -> dict:
    """Create a reproducible, pending next-brief suggestion from pinned A11 history.

    comparison supplies after_window, the exact metric definition, cohort and
    timezone. Equal-length, nonoverlapping windows are a proposed measurement
    design; no after observation, trend, causal lift or outcome is inferred.
    """
    value, blobs = _build(batch, registry=registry, source_id=source_id, advisory=advisory,
                          expected_export_id=expected_export_id, learning_key=learning_key,
                          expected_revision=expected_revision, scope=scope, proposal_id=proposal_id,
                          suggestion=suggestion, hypothesis=hypothesis, comparison=comparison, opt_in=opt_in)
    ref = proposal_ref(proposal_id)
    if registry.exists(ref):
        original = load_proposal(registry, proposal_id)
        require(original == value, "PROPOSAL_DIGEST_CONFLICT")
        return original
    for relative, payload in blobs:
        put_record(registry, relative, payload)
    put_record(registry, ref, value)
    return load_proposal(registry, proposal_id)


def _adopted(original, decision, note):
    require(type(decision) is str and decision in {"ACCEPTED", "DECLINED"}, "ADOPTION_DECISION_INVALID")
    require(note is None or type(note) is str and 0 < len(note.strip()) <= 2048, "ADOPTION_NOTE_INVALID")
    private_text(note)
    return validate(FEEDBACK_PROPOSAL_SCHEMA, seal({
        **{k: v for k, v in original.items() if k != "record_sha256"}, "revision": 2,
        "parent": {"id": original["proposal_id"], "revision": 1, "sha256": original["record_sha256"],
                   "ref": proposal_ref(original["proposal_id"])},
        "adoption": {"decision": decision, "actor": "HUMAN", "note": note}}))


def load_proposal(registry, proposal_id: str, revision: int = 1) -> dict:
    value = validate(FEEDBACK_PROPOSAL_SCHEMA, read_record(registry, proposal_ref(proposal_id, revision)))
    require(value["proposal_id"] == proposal_id and value["revision"] == revision, "PROPOSAL_IDENTITY_MISMATCH")
    if revision == 2:
        original = load_proposal(registry, proposal_id, 1)
        expected = _adopted(original, value["adoption"]["decision"], value["adoption"]["note"])
        require(value == expected, "ADOPTION_LINEAGE_MISMATCH")
        return value
    batch = load_variant_batch(registry, value["batch_id"])
    pin, compare = value["advisory"], value["comparison"]
    advisory = read_record(registry, pin["archive_ref"])
    expected, blobs = _build(
        batch, registry=registry, source_id=value["source"]["id"], advisory=advisory,
        expected_export_id=pin["export_id"], learning_key=pin["learning_key"], expected_revision=pin["revision"],
        scope=value["scope"], proposal_id=proposal_id, suggestion=value["suggestion"], hypothesis=value["hypothesis"],
        comparison={key: compare[key] for key in ("after_window", "metric", "cohort", "timezone")}, opt_in=True)
    require(expected == value, "PROPOSAL_SOURCE_BINDING_MISMATCH")
    for ref, body in blobs:
        require(read_record(registry, ref) == body, "PROPOSAL_SNAPSHOT_MISMATCH")
    return value


def record_adoption(proposal: dict, decision: str, *, registry, human_authorized: bool = False,
                    note: str | None = None) -> dict:
    """Append revision 2 for a Human decision. This never authorizes generation."""
    require(human_authorized is True, "HUMAN_ADOPTION_AUTHORIZATION_REQUIRED")
    original = validate(FEEDBACK_PROPOSAL_SCHEMA, proposal)
    require(original["revision"] == 1 and original["adoption"]["decision"] == "PENDING", "ADOPTION_TERMINAL")
    actual = load_proposal(registry, original["proposal_id"])
    require(actual == original, "PROPOSAL_NOT_PERSISTED")
    adopted = _adopted(original, decision, note)
    ref = proposal_ref(original["proposal_id"], 2)
    if registry.exists(ref):
        prior = load_proposal(registry, original["proposal_id"], 2)
        require(prior == adopted, "ADOPTION_DIGEST_CONFLICT")
        return prior
    put_record(registry, ref, adopted)
    return load_proposal(registry, original["proposal_id"], 2)
