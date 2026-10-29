"""Deterministic suggestions from exact local evidence, without external effects.

Saved M02 series are rebuilt against M01. M03 insights are read by immutable ID
from AudienceLedger, which replays the original notes and signal references.
Hashes identify content; they are neither provider attestations nor permissions.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from itertools import islice

from jsonschema import Draft202012Validator
from referencing import Registry

from ai_commerce.contracts.schema import _format_checker, _valid_datetime
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ..audience import AudienceLedger, validate_insight
from ..core.models import OpportunityError, digest
from ..core.schemas import SOURCE_STATUS
from ..signals import SignalLedger
from ..trends import TrendRule, build_series, evaluate_trend, validate_series
from .schemas import (
    MAX_INSIGHTS, MAX_RECORD_BYTES, MAX_SERIES, MAX_SIGNALS, OVERRIDES, schema,
)

LIMITATIONS = [
    "All sources are user supplied; local replay checks integrity, not provider authenticity or rights.",
    "These are inferred creative hypotheses, not validated demand, virality, revenue or causal predictions.",
    "Audience statements retain the caller's observation/inference status; representativeness is unknown.",
    "Freshness uses the explicit as_of clock and supplied timestamps; collection time does not refresh an observation.",
    "Manual ideas and creative overrides supply creative direction, not evidence or action authority.",
    "Source hashes refer to retained local inputs; omitted source metadata is not rewritten or independently verified.",
    "Suggestions require no product, CTA, audience profile, trend or media format.",
]


class ProposalError(OpportunityError):
    """Bounded diagnostics never echo raw notes, contacts or local paths."""


def _check(ok: bool, code: str) -> None:
    if not ok:
        raise ProposalError(code)


def _bounded_record(value: dict) -> None:
    try:
        _check(len(canonical_bytes(value)) <= MAX_RECORD_BYTES, "PROPOSAL_INPUT_OR_OUTPUT_TOO_LARGE")
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        if isinstance(exc, ProposalError):
            raise
        raise ProposalError("INVALID_PROPOSAL_VALUE") from None


def _text(value: str, *, creative: bool = False) -> None:
    _check(isinstance(value, str) and bool(value.strip()), "PROPOSAL_TEXT_EMPTY")
    scanned = unicodedata.normalize("NFKC", value)
    scanned = "".join(c for c in scanned if unicodedata.category(c) != "Cf")
    # Human-authored creative can contain a public URL or @handle. A URL may
    # include numeric product IDs; neither is provider evidence or permission.
    # Keep direct contact details out of both creative and evidence, and apply
    # the stricter URL/handle rule to user-supplied audience/source context.
    pii_scan = re.sub(r"https?://[^\s]+", "", scanned) if creative else scanned
    contact = (
        re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", pii_scan)
        or re.search(r"(?<!\w)(?:\+?\d[\s().-]*){7,}(?!\w)", pii_scan)
        or re.search(r"(?i)\b(?:mailto|tel):", pii_scan)
        or (not creative and re.search(r"(?<!\w)@[\w.]{2,}|https?://", scanned))
    )
    _check(not contact, "PROPOSAL_CONTACT_OR_IDENTIFIER_NOT_ALLOWED")
    _check(not any(ord(c) < 32 and c not in "\n\r\t" for c in value), "PROPOSAL_CONTROL_CHARACTER")


def _instant(value: str) -> datetime:
    _check(isinstance(value, str) and len(value) <= 64 and _valid_datetime(value), "INVALID_PROPOSAL_CLOCK")
    return datetime.fromisoformat(value.replace("t", "T").replace("z", "Z").replace("Z", "+00:00")).astimezone(timezone.utc)


def _freshness(value: str | None, clock: datetime, max_age: int, *, collected_at: str | None = None) -> str:
    if value is None:
        return "UNKNOWN"
    observed = _instant(value)
    if observed > clock or (collected_at is not None and _instant(collected_at) > clock):
        return "FUTURE"
    return "STALE" if (clock - observed).total_seconds() > max_age else "FRESH"


def _selection(values: Iterable, maximum: int) -> list:
    _check(isinstance(values, Iterable) and not isinstance(values, (str, bytes, Mapping)), "PROPOSAL_SELECTION_ARRAY_REQUIRED")
    result = list(islice(iter(values), maximum + 1))
    _check(len(result) <= maximum, "TOO_MANY_PROPOSAL_INPUTS")
    return result


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    definition = schema()
    Draft202012Validator.check_schema(definition)
    return Draft202012Validator(definition, registry=Registry(), format_checker=_format_checker())


def validate_proposal(record: dict) -> dict:
    """Check serialized structure, content identity and internal references only.

    This is not renewed ledger replay or proof of authorship. Use
    propose_opportunities with the retained source series and ledgers to rebuild.
    """
    _bounded_record(record)
    _check(_validator().is_valid(record), "INVALID_OPPORTUNITY_PROPOSAL")
    _check(record["proposal_id"] == "opp-" + digest({k: v for k, v in record.items() if k != "proposal_id"}),
           "PROPOSAL_IDENTITY_MISMATCH")
    allowed = set()
    signals = {s["signal_id"] for s in record["signal_evidence"]}
    _check(len(signals) == len(record["signal_evidence"]), "DUPLICATE_PROPOSAL_SIGNAL")
    for receipt in record["signal_evidence"]:
        _check(receipt["raw_ref"] == "sha256:" + receipt["raw_sha256"], "PROPOSAL_RAW_REFERENCE_MISMATCH")
        allowed.update((receipt["import_id"], receipt["signal_id"], receipt["raw_ref"]))
        for field in ("unit", "denominator", "locale", "geo"):
            if receipt[field] is not None:
                _text(receipt[field])
    for insight in record["audience_insights"]:
        _check(insight["source_ref"] == "sha256:" + insight["source_sha256"], "PROPOSAL_NOTE_REFERENCE_MISMATCH")
        allowed.update((insight["insight_id"], insight["source_ref"]))
        if insight["audience_segment"] is not None:
            _text(insight["audience_segment"])
        for statement in insight["statements"]:
            allowed.add(insight["insight_id"] + "/statements/" + str(statement["index"]))
            _check(statement["note_ref"] == insight["source_ref"], "PROPOSAL_NOTE_REFERENCE_MISMATCH")
            _check(set(statement["matched_signal_ids"]) <= set(statement["signal_ids"]) <= signals,
                   "PROPOSAL_STATEMENT_REFERENCE_MISMATCH")
            _text(statement["text"])
            _text(statement["uncertainty"])
    if record["trend"] is not None:
        trend = record["trend"]
        allowed.update((trend["series_id"], trend["hypothesis_id"]))
        for field in ("supporting_signal_ids", "counterevidence_signal_ids", "unavailable_signal_ids", "unused_signal_ids"):
            _check(set(trend[field]) <= signals, "PROPOSAL_TREND_REFERENCE_MISMATCH")
    if record["manual_input"] is not None:
        manual = record["manual_input"]
        _text(manual["text"], creative=True)
        _check(manual["text_sha256"] == sha256_bytes(manual["text"].encode("utf-8")), "PROPOSAL_MANUAL_HASH_MISMATCH")
        allowed.add("sha256:" + manual["text_sha256"])
    _check(record["source_refs"] == sorted(allowed), "PROPOSAL_SOURCE_REFERENCES_MISMATCH")
    for reason in record["reasons"]:
        _check(set(reason["source_refs"]) <= allowed, "PROPOSAL_REASON_REFERENCE_MISMATCH")
    for field, value in record["creative"].items():
        if value is not None:
            _text(value, creative=True)
        if field in record["creative_overrides"]:
            _check(value == record["creative_overrides"][field], "PROPOSAL_OVERRIDE_MISMATCH")
    if record["topic"] is not None:
        _text(record["topic"])
    return record


class _ReadSignals:
    """One read-only replay per selected import, shared across both consumers."""

    def __init__(self, ledger: SignalLedger | None):
        self.ledger = ledger
        self.cache = {}
        self.count = 0

    def get(self, import_id: str) -> dict:
        _check(self.ledger is not None, "PROPOSAL_M01_EVIDENCE_REQUIRED")
        if import_id not in self.cache:
            try:
                result = self.ledger.get(import_id)
            except (OpportunityError, OSError, ValueError, TypeError, KeyError):
                raise ProposalError("PROPOSAL_M01_EVIDENCE_INVALID") from None
            _check(result["import"]["source_snapshot"]["sha256"] == result["import"]["raw_sha256"],
                   "PROPOSAL_SOURCE_HASH_MISMATCH")
            self.count += len(result["signals"])
            _check(self.count <= MAX_SIGNALS and len(self.cache) < 100, "TOO_MUCH_PROPOSAL_EVIDENCE")
            self.cache[import_id] = result
        return self.cache[import_id]


def _replay_series(value: dict, signals: _ReadSignals) -> dict:
    _bounded_record(value)
    try:
        validate_series(value)
    except (OpportunityError, ValueError, TypeError, KeyError, RecursionError):
        raise ProposalError("PROPOSAL_TREND_SERIES_INVALID") from None
    contexts, seen = {}, {}
    for point in value["points"]:
        import_id, context = point["signal"]["import_id"], point["comparison_context"]
        _check(import_id not in seen or seen[import_id] == context, "PROPOSAL_COMPARISON_CONTEXT_CONFLICT")
        seen[import_id] = context
        if context is not None:
            contexts[import_id] = context
    rebuilt = build_series(signals, import_ids=[r["import_id"] for r in value["imports"]],
                           topic=value["topic"], signal_type=value["signal_type"], comparison_contexts=contexts)
    _check(rebuilt == value, "PROPOSAL_TREND_REPLAY_MISMATCH")
    return rebuilt


def _receipt(signal_id: str, import_id: str, signals: _ReadSignals, clock: datetime, max_age: int) -> dict:
    imported = signals.get(import_id)
    record = imported["import"]
    signal = next((s for s in imported["signals"] if s["signal_id"] == signal_id), None)
    _check(signal is not None, "PROPOSAL_SIGNAL_NOT_FOUND")
    window = signal.get("observation_window")
    return {
        "import_id": import_id, "signal_id": signal_id,
        "raw_sha256": record["raw_sha256"], "raw_ref": record["raw_ref"],
        "import_sha256": digest(record), "signal_sha256": digest(signal),
        "context_sha256": record["context_sha256"], "scope_sha256": record["scope_sha256"],
        "lineage": deepcopy(record["lineage"]), "source_status": record["source_status"],
        "source_rights_status": record["source_snapshot"]["rights_status"],
        "signal_rights_status": signal["rights_status"], "signal_status": signal["status"],
        "value": signal["value"], "unit": signal["unit"], "denominator": signal.get("denominator"),
        "locale": signal["locale"], "geo": signal["geo"],
        "observed_at": signal["observed_at"], "collected_at": signal["collected_at"],
        "observation_window": deepcopy(window),
        "freshness": "FUTURE" if _instant(signal["observed_at"]) > clock else _freshness(
            window[1] if window else signal["observed_at"], clock, max_age, collected_at=signal["collected_at"]),
    }


def _reason(code: str, polarity: str, text: str, refs: list[str], status: str = "INFERRED") -> dict:
    return {"code": code, "polarity": polarity, "text": text, "epistemic_status": status,
            "source_refs": sorted(set(refs))}


def _build(*, series: dict | None, insights: list[dict], manual: str | None, overrides: dict,
           signals: _ReadSignals, clock: datetime, rule: TrendRule) -> dict:
    max_age = rule.record()["max_age_seconds"]
    trend_ids = {p["signal"]["signal_id"] for p in series["points"]} if series else set()
    receipts, reasons, audience, source_refs = {}, [], [], set()
    limitations = list(LIMITATIONS)
    alternatives = ["Explore a different creative interpretation of the same idea.",
                    "Keep a Human-authored freeform idea without an evidence requirement."]
    trend = None
    if series is not None:
        hypothesis = evaluate_trend(series, as_of=clock.isoformat(), rule=rule)
        fields = ("series_id", "hypothesis_id", "status", "direction", "conditions", "rule",
                  "observation_count", "independent_timestamp_count", "supporting_signal_ids",
                  "counterevidence_signal_ids", "unavailable_signal_ids", "unused_signal_ids", "reason_codes", "comparison")
        trend = {**{k: deepcopy(hypothesis[k]) for k in fields},
                 "series_sha256": digest(series), "hypothesis_sha256": digest(hypothesis),
                 "category": series["category"], "comparison_status": series["comparison_status"]}
        source_refs.update((series["series_id"], hypothesis["hypothesis_id"]))
        status = hypothesis["status"]
        polarity = "SUPPORT" if status == "GROWING" else "COUNTEREVIDENCE" if status == "DROPPING" else "CONTEXT"
        if hypothesis["conditions"]:
            polarity = "LIMITATION"
        reasons.append(_reason("TREND_" + status, polarity,
            "M02 describes " + status + " in supplied observations; creative relevance remains a hypothesis.",
            [hypothesis["hypothesis_id"], *hypothesis["supporting_signal_ids"]]))
        for condition in hypothesis["conditions"]:
            reasons.append(_reason("TREND_CONDITION_" + condition, "LIMITATION",
                                   "The supplied series has the M02 condition " + condition + ".",
                                   [hypothesis["hypothesis_id"]]))
        if hypothesis["counterevidence_signal_ids"]:
            reasons.append(_reason("TREND_COUNTEREVIDENCE", "COUNTEREVIDENCE",
                                   "M02 retains these counterexamples or data limitations; none are discarded.",
                                   [hypothesis["hypothesis_id"], *hypothesis["counterevidence_signal_ids"]]))
        for point in series["points"]:
            signal = point["signal"]
            receipts[signal["signal_id"]] = _receipt(signal["signal_id"], signal["import_id"], signals, clock, max_age)
    for insight in insights:
        freshness = _freshness(insight["noted_at"], clock, max_age)
        trace = {k: deepcopy(insight[k]) for k in (
            "insight_id", "source_sha256", "source_ref", "source_status", "rights_status", "data_kind",
            "lineage", "noted_at", "audience_segment")}
        trace.update(record_sha256=digest(insight), freshness=freshness, statements=[])
        source_refs.update((insight["insight_id"], insight["source_ref"]))
        if freshness != "FRESH":
            reasons.append(_reason("AUDIENCE_FRESHNESS_" + freshness, "LIMITATION",
                                   "The caller's note timestamp has freshness " + freshness + ".",
                                   [insight["insight_id"], insight["source_ref"]]))
        for index, statement in enumerate(insight["statements"]):
            ids = sorted({ref["signal_id"] for ref in statement["evidence_refs"]})
            matched = sorted(set(ids) & trend_ids)
            relation = "NO_TREND" if series is None else "MATCHED_SIGNAL" if matched else "UNLINKED_CONTEXT"
            trace["statements"].append({
                **{k: deepcopy(statement[k]) for k in ("kind", "status", "text", "uncertainty", "note_ref", "evidence_status")},
                "index": index, "signal_ids": ids, "matched_signal_ids": matched, "relation_to_trend": relation,
            })
            ref = insight["insight_id"] + "/statements/" + str(index)
            source_refs.add(ref)
            kind = statement["kind"]
            polarity = "COUNTEREVIDENCE" if kind == "COUNTEREXAMPLE" else "ALTERNATIVE" if kind == "ALTERNATIVE" else "SUPPORT"
            if polarity == "SUPPORT" and (relation == "UNLINKED_CONTEXT" or statement["status"] == "UNKNOWN" or freshness in ("STALE", "FUTURE")):
                polarity = "CONTEXT"
            reasons.append(_reason("AUDIENCE_" + kind, polarity, statement["text"], [ref, insight["source_ref"], *ids], statement["status"]))
            if kind == "ALTERNATIVE":
                alternatives.append(statement["text"])
            for evidence in statement["evidence_refs"]:
                receipts[evidence["signal_id"]] = _receipt(evidence["signal_id"], evidence["import_id"], signals, clock, max_age)
        audience.append(trace)
    if manual is not None:
        manual_input = {"text": manual, "text_sha256": sha256_bytes(manual.encode("utf-8"))}
        ref = "sha256:" + manual_input["text_sha256"]
        source_refs.add(ref)
        reasons.append(_reason("HUMAN_CREATIVE_IDEA", "CONTEXT", "Human creative input supplies direction, with no claim of observed demand.", [ref]))
        objective, kind = manual, "MANUAL_CONTENT"
    else:
        manual_input = None
        if series is None:
            objective = "Explore an original creative response to the supplied qualitative notes."
        elif series["category"] == "CREATIVE_FORMAT":
            objective = "Explore the caller-described creative format " + series["topic"] + " as one optional direction."
        else:
            objective = "Explore an original content idea about " + series["topic"] + "."
        # Use a caller-authored job/friction/context as a creative prompt, never
        # as a demographic, demand claim or inferred fact about a population.
        eligible = [statement for item in audience if item["freshness"] not in ("STALE", "FUTURE")
                    for statement in item["statements"]
                    if statement["kind"] in ("JOB_TO_BE_DONE", "FRICTION", "CONTENT_CONTEXT")
                    and statement["status"] != "UNKNOWN"
                    and statement["relation_to_trend"] != "UNLINKED_CONTEXT"]
        if eligible:
            objective += " Consider this caller-supplied note as creative context: " + eligible[0]["text"]
        kind = "TREND_CONTENT" if series else "AUDIENCE_CONTENT"
    if not audience:
        limitations.append("No audience insight is linked to this proposal; no audience profile is inferred.")
    if series is None:
        limitations.append("No trend evidence is attached; trend direction and source freshness may be unknown.")
    if any(s["relation_to_trend"] == "UNLINKED_CONTEXT" for a in audience for s in a["statements"]):
        limitations.append("Unlinked audience statements are context only; a shared note does not establish a signal relationship.")
    for receipt in receipts.values():
        source_refs.update((receipt["signal_id"], receipt["import_id"], receipt["raw_ref"]))
    body = {
        "schema_version": "OpportunityProposal.v1", "kind": kind, "status": "HYPOTHESIS",
        "epistemic_status": "INFERRED", "source_status": SOURCE_STATUS, "rights_status": "UNKNOWN",
        "suggestion_only": True, "publication_status": "NOT_ATTEMPTED",
        "as_of": clock.isoformat().replace("+00:00", "Z"), "freshness_max_age_seconds": max_age,
        "topic": series["topic"] if series else None,
        "creative": {"objective": objective, "audience": None, "product_ref": None, "cta": None, "media_hint": None, **deepcopy(overrides)},
        "creative_overrides": deepcopy(overrides), "manual_input": manual_input, "trend": trend,
        "audience_insights": audience, "signal_evidence": [receipts[k] for k in sorted(receipts)],
        "reasons": reasons, "alternatives": sorted(set(alternatives)), "source_refs": sorted(source_refs),
        "limitations": limitations,
    }
    return validate_proposal({"proposal_id": "opp-" + digest(body), **body})


def propose_opportunities(*, trend_series: Iterable[dict] = (), audience_insight_ids: Iterable[str] = (),
                          signal_ledger: SignalLedger | None = None, audience_ledger: AudienceLedger | None = None,
                          as_of: str, manual_idea: str | None = None, creative_overrides: Mapping | None = None,
                          trend_rule: TrendRule | None = None) -> list[dict]:
    """Replay selected local evidence and return content hypotheses in ID order.

    Join M02/M03 only through exact M01 signal identity. Unmatched notes produce
    separate audience proposals. A manual idea always has its own independent
    proposal; no evidence becomes a prerequisite for Human creativity. Overrides
    affect creative fields only, and preserve the original manual text/hash.
    """
    clock = _instant(as_of)
    rule = TrendRule() if trend_rule is None else trend_rule
    _check(isinstance(rule, TrendRule), "INVALID_PROPOSAL_TREND_RULE")
    rule.record()
    overrides = {} if creative_overrides is None else creative_overrides
    _check(isinstance(overrides, Mapping), "INVALID_PROPOSAL_OVERRIDES")
    overrides = deepcopy(dict(overrides))
    _bounded_record(overrides)
    _check(Draft202012Validator(OVERRIDES).is_valid(overrides), "INVALID_PROPOSAL_OVERRIDES")
    for value in overrides.values():
        if value is not None:
            _text(value, creative=True)
    if manual_idea is not None:
        _check(isinstance(manual_idea, str) and len(manual_idea) <= 8192, "INVALID_MANUAL_CREATIVE_IDEA")
        _text(manual_idea, creative=True)
        _bounded_record({"text": manual_idea})
    signals = _ReadSignals(signal_ledger)
    selected = _selection(trend_series, MAX_SERIES)
    _bounded_record({"series": selected})
    series_by_id = {}
    for supplied in selected:
        rebuilt = _replay_series(supplied, signals)
        if rebuilt["points"]:
            series_by_id[rebuilt["series_id"]] = rebuilt
    ids = _selection(audience_insight_ids, MAX_INSIGHTS)
    _check(all(isinstance(i, str) and re.fullmatch(r"aud-[a-f0-9]{64}", i) for i in ids), "INVALID_PROPOSAL_INSIGHT_ID")
    _check(not ids or audience_ledger is not None, "PROPOSAL_AUDIENCE_LEDGER_REQUIRED")
    insights = []
    for insight_id in sorted(set(ids)):
        try:
            insight = audience_ledger.get(insight_id, signal_ledger=signals)
            validate_insight(insight)
        except (OpportunityError, OSError, ValueError, TypeError, KeyError):
            raise ProposalError("PROPOSAL_AUDIENCE_EVIDENCE_INVALID") from None
        if insight["statements"]:
            insights.append(insight)
    _bounded_record({"audience": insights})
    result, linked = [], set()
    for series_id in sorted(series_by_id):
        series = series_by_id[series_id]
        point_ids = {p["signal"]["signal_id"] for p in series["points"]}
        matched = [i for i in insights if any(ref["signal_id"] in point_ids
                   for statement in i["statements"] for ref in statement["evidence_refs"])]
        linked.update(i["insight_id"] for i in matched)
        result.append(_build(series=series, insights=matched, manual=None, overrides=overrides, signals=signals, clock=clock, rule=rule))
    for insight in insights:
        if insight["insight_id"] not in linked:
            result.append(_build(series=None, insights=[insight], manual=None, overrides=overrides, signals=signals, clock=clock, rule=rule))
    if manual_idea is not None:
        result.append(_build(series=None, insights=[], manual=manual_idea, overrides=overrides, signals=signals, clock=clock, rule=rule))
    return sorted(result, key=lambda p: p["proposal_id"])
