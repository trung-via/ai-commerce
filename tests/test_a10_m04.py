"""M04 acceptance on real M01/M02/M03 APIs; all fixtures are synthetic/offline."""
from __future__ import annotations

import json
import socket
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.opportunity.audience import AudienceLedger, signal_evidence_ref
from ai_commerce.opportunity.core.models import digest
from ai_commerce.opportunity.core.schemas import SOURCE_STATUS
from ai_commerce.opportunity.proposals import (
    ProposalError, propose_opportunities, schema, validate_proposal,
)
from ai_commerce.opportunity.proposals.schemas import MAX_INSIGHTS, MAX_SERIES
from ai_commerce.opportunity.signals import SignalLedger
from ai_commerce.opportunity.trends import TrendRule, build_series, evaluate_trend, validate_series

AS_OF = "2026-10-29T12:00:00Z"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Network access attempted")
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


@pytest.fixture
def signals(tmp_path):
    return SignalLedger(tmp_path / "signals.sqlite")


def add_series(signals, values=(10, 10, 20), *, topic="SYNTHETIC quiet break", locale="vi-VN", geo="VN",
               source_id="synthetic-m04", context_changes=None, snapshot_changes=None, row_changes=None,
               correction_of=None):
    rows = []
    for index, value in enumerate(values):
        when = datetime(2026, 10, 27, tzinfo=timezone.utc) + timedelta(days=index)
        rows.append({
            "topic": topic, "signal_type": "TOPIC", "source_type": "MANUAL_NOTES",
            "locale": locale, "geo": geo, "query_context": "synthetic:manual-observations",
            "observed_at": when.isoformat(), "collected_at": AS_OF, "timezone": "UTC",
            "unit": "count", "denominator": None, "observation_window": None,
            "status": "NOT_AVAILABLE" if value is None else "AVAILABLE", "value": value,
            "rights_status": "UNKNOWN", "evidence_refs": ["synthetic://local-source"],
            **(row_changes or {}),
        })
    raw = b"\xef\xbb\xbf" + canonical_bytes({"schema_version": "ManualSignalRows.v1", "rows": rows})
    snapshot = {
        "schema_version": "SignalSourceSnapshot.v1", "source_id": source_id,
        "source_kind": "SYNTHETIC_FIXTURE", "source_ref": "synthetic://signals.json",
        "captured_at": AS_OF, "collected_at": AS_OF, "sha256": sha256_bytes(raw),
        "rights_status": "UNKNOWN", "verification_status": SOURCE_STATUS, "geo": geo, "locale": locale,
        "notes": "Synthetic fixtures only; no provider facts.", **(snapshot_changes or {}),
    }
    imported = signals.import_bytes(raw, source_snapshot=snapshot, file_format="json", correction_of=correction_of)
    import_id = imported["import"]["import_id"]
    context = {
        "schema_version": "TrendComparisonContext.v1", "metric": "manual_mentions", "metric_kind": "COUNT",
        "baseline_id": "synthetic-protocol.v1", "normalization_id": "raw-count.v1", "query_batch": None,
        "relative_scale": None, "window_kind": "POINT", "evidence_refs": ["synthetic://comparison-note"],
        "source_status": SOURCE_STATUS, **(context_changes or {}),
    }
    series = build_series(signals, import_ids=[import_id], topic=topic,
                          signal_type=rows[0]["signal_type"], comparison_contexts={import_id: context})
    return series, imported, raw


def notes_document():
    return {
        "schema_version": "AudienceNotes.v1", "data_kind": "SYNTHETIC_FIXTURE",
        "source_note": "  Synthetic workshop; no real participants.\r\n  ",
        "noted_at": "2026-10-28T00:00:00Z", "audience_segment": None,
        "statements": [
            {"kind": "JOB_TO_BE_DONE", "status": "OBSERVED_USER_SUPPLIED", "text": "A supplied note asks for a quiet break."},
            {"kind": "CONTENT_CONTEXT", "status": "INFERRED", "text": "A silent imagined story might fit.", "uncertainty": "Untested interpretation."},
            {"kind": "COUNTEREXAMPLE", "status": "OBSERVED_USER_SUPPLIED", "text": "Another supplied note prefers lively sound."},
            {"kind": "ALTERNATIVE", "status": "UNKNOWN", "text": "A poem is an alternative."},
        ],
    }


def append_notes(tmp_path, signals=None, imported=None, *, document=None, store=None, revision_of=None):
    document = deepcopy(notes_document() if document is None else document)
    if imported is not None:
        ref = signal_evidence_ref(signals, imported["import"]["import_id"], imported["signals"][0]["signal_id"])
        document["statements"][0]["evidence_refs"] = [ref]
    raw = b"\xef\xbb\xbf" + (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    store = store or AudienceLedger(tmp_path / "audience.sqlite")
    insight = store.append_bytes(raw, signal_ledger=signals, revision_of=revision_of)["insight"]
    return store, insight, raw


def propose(signals, series, **kwargs):
    return propose_opportunities(trend_series=[series], signal_ledger=signals, as_of=AS_OF, **kwargs)


def reidentify_series(series):
    series["series_id"] = "series-" + digest({k: v for k, v in series.items() if k != "series_id"})
    return series


def reidentify_proposal(record):
    record["proposal_id"] = "opp-" + digest({k: v for k, v in record.items() if k != "proposal_id"})
    return record


def test_genuine_fan_in_replays_sources_and_preserves_negative_evidence(tmp_path, signals):
    series, imported, raw = add_series(signals)
    audience, insight, note_raw = append_notes(tmp_path, signals, imported)
    signals_before, notes_before = Path(signals.path).read_bytes(), Path(audience.path).read_bytes()
    original_series, original_insight = deepcopy(series), deepcopy(insight)
    proposal, = propose(signals, series, audience_ledger=audience, audience_insight_ids=[insight["insight_id"]])
    assert proposal["kind"] == "TREND_CONTENT"
    assert proposal["status"] == "HYPOTHESIS" and proposal["epistemic_status"] == "INFERRED"
    assert proposal["trend"]["series_id"] == series["series_id"]
    assert proposal["trend"]["series_sha256"] == digest(series)
    assert proposal["trend"]["hypothesis_sha256"] == digest(evaluate_trend(series, as_of=AS_OF))
    assert proposal["trend"]["status"] == "GROWING"
    assert insight["statements"][0]["text"] in proposal["creative"]["objective"]
    trace, = proposal["audience_insights"]
    assert trace["record_sha256"] == digest(insight)
    assert trace["source_sha256"] == sha256_bytes(note_raw)
    assert trace["statements"][0]["relation_to_trend"] == "MATCHED_SIGNAL"
    assert trace["statements"][1]["status"] == "INFERRED"
    assert trace["statements"][1]["relation_to_trend"] == "UNLINKED_CONTEXT"
    assert trace["statements"][1]["uncertainty"] == "Untested interpretation."
    negative, = [r for r in proposal["reasons"] if r["code"] == "AUDIENCE_COUNTEREXAMPLE"]
    assert negative["polarity"] == "COUNTEREVIDENCE"
    assert negative["epistemic_status"] == "OBSERVED_USER_SUPPLIED"
    assert insight["statements"][2]["text"] == negative["text"]
    assert insight["statements"][3]["text"] in proposal["alternatives"]
    assert {r["raw_sha256"] for r in proposal["signal_evidence"]} == {sha256_bytes(raw)}
    assert all(r["source_rights_status"] == r["signal_rights_status"] == "UNKNOWN" for r in proposal["signal_evidence"])
    assert proposal["rights_status"] == "UNKNOWN" and proposal["suggestion_only"] is True
    assert proposal["publication_status"] == "NOT_ATTEMPTED"
    assert all(proposal["creative"][key] is None for key in ("audience", "product_ref", "cta", "media_hint"))
    assert series == original_series and insight == original_insight
    assert Path(signals.path).read_bytes() == signals_before
    assert Path(audience.path).read_bytes() == notes_before
    assert validate_proposal(proposal) == proposal


def test_empty_inputs_and_empty_components_do_not_create_evidence(tmp_path, signals):
    assert propose_opportunities(as_of=AS_OF) == []
    empty = build_series(signals, import_ids=[], topic="SYNTHETIC empty")
    document = notes_document()
    document["statements"] = []
    audience, insight, _ = append_notes(tmp_path, document=document)
    assert propose_opportunities(trend_series=[empty], audience_ledger=audience,
                                 audience_insight_ids=[insight["insight_id"]], as_of=AS_OF) == []
    assert not Path(signals.path).exists()


@pytest.mark.parametrize("values", [(10,), (10, 20), (None,), (0, None, 0)], ids=["single", "two", "missing", "zero-missing"])
def test_sparse_or_missing_history_is_never_a_positive_trend(signals, values):
    series, _, _ = add_series(signals, values)
    proposal, = propose(signals, series)
    assert "INSUFFICIENT_HISTORY" in proposal["trend"]["conditions"]
    assert proposal["trend"]["direction"] is None
    assert not [r for r in proposal["reasons"] if r["polarity"] == "SUPPORT"]
    assert len(proposal["signal_evidence"]) == len(values)
    actual = sorted(proposal["signal_evidence"], key=lambda r: r["observed_at"])
    assert [r["value"] for r in actual] == list(values)
    assert all((r["signal_status"] == "NOT_AVAILABLE") == (r["value"] is None) for r in actual)


def test_zero_baseline_does_not_invent_percentage_or_demand(signals):
    series, _, _ = add_series(signals, (0, 0, 10))
    proposal, = propose(signals, series)
    assert proposal["trend"]["comparison"]["baseline_median"] == 0
    assert proposal["trend"]["comparison"]["relative_change"] is None
    assert "ZERO_BASELINE_RELATIVE_CHANGE_UNDEFINED" in proposal["trend"]["reason_codes"]
    assert "priority" not in proposal and "score" not in proposal and "probability" not in proposal


@pytest.mark.parametrize(("values", "expected"), [((10, 30, 5), "CONTRADICTORY"), ((30, 20, 10), "DROPPING")])
def test_contradictory_and_negative_history_retains_counterevidence(signals, values, expected):
    series, _, _ = add_series(signals, values)
    proposal, = propose(signals, series)
    assert proposal["trend"]["status"] == expected
    assert any(r["polarity"] == "COUNTEREVIDENCE" for r in proposal["reasons"])
    assert not [r for r in proposal["reasons"] if r["polarity"] == "SUPPORT"]
    if expected == "CONTRADICTORY":
        assert proposal["trend"]["direction"] is None
        assert proposal["trend"]["counterevidence_signal_ids"]


def test_stale_sources_are_explicit_and_note_age_is_not_refreshed(tmp_path, signals):
    series, imported, _ = add_series(signals)
    audience, insight, _ = append_notes(tmp_path, signals, imported)
    proposal, = propose_opportunities(trend_series=[series], signal_ledger=signals,
        audience_ledger=audience, audience_insight_ids=[insight["insight_id"]], as_of="2026-11-10T12:00:00Z")
    assert "STALE" in proposal["trend"]["conditions"]
    assert all(r["freshness"] == "STALE" for r in proposal["signal_evidence"])
    assert proposal["audience_insights"][0]["freshness"] == "STALE"
    assert insight["statements"][0]["text"] not in proposal["creative"]["objective"]
    assert not [r for r in proposal["reasons"] if r["polarity"] == "SUPPORT"]


def test_freshness_rule_is_explicit_and_adjustable(signals):
    series, _, _ = add_series(signals)
    default, = propose(signals, series)
    strict, = propose(signals, series, trend_rule=TrendRule(max_age_seconds=0))
    assert default["proposal_id"] != strict["proposal_id"]
    assert strict["freshness_max_age_seconds"] == 0 and strict["trend"]["rule"]["max_age_seconds"] == 0
    assert strict["trend"]["status"] == "STALE"


def test_future_evidence_cannot_supply_positive_direction(tmp_path, signals):
    series, imported, _ = add_series(signals)
    document = notes_document()
    document["noted_at"] = "2026-10-30T00:00:00Z"
    audience, insight, _ = append_notes(tmp_path, signals, imported, document=document)
    proposal, = propose_opportunities(trend_series=[series], signal_ledger=signals, audience_ledger=audience,
        audience_insight_ids=[insight["insight_id"]], as_of="2026-10-28T12:00:00Z")
    assert "UNKNOWN" in proposal["trend"]["conditions"]
    assert proposal["audience_insights"][0]["freshness"] == "FUTURE"
    assert insight["statements"][0]["text"] not in proposal["creative"]["objective"]
    assert all(r["freshness"] == "FUTURE" for r in proposal["signal_evidence"])
    assert not [r for r in proposal["reasons"] if r["polarity"] == "SUPPORT"]


def test_unknown_comparison_metadata_stays_unknown(signals):
    series, _, _ = add_series(signals, context_changes={"baseline_id": None})
    proposal, = propose(signals, series)
    assert proposal["trend"]["status"] == "UNKNOWN"
    assert proposal["trend"]["comparison_status"] == "UNKNOWN"
    assert "MISSING_BASELINE_ID" in proposal["trend"]["reason_codes"]


def test_saved_series_requires_actual_m01_replay(signals):
    series, _, _ = add_series(signals)
    with pytest.raises(ProposalError, match="PROPOSAL_M01_EVIDENCE_REQUIRED"):
        propose_opportunities(trend_series=[series], as_of=AS_OF)


def test_valid_content_hash_cannot_launder_omitted_signal(signals):
    series, _, _ = add_series(signals)
    forged = deepcopy(series)
    removed = forged["points"].pop()["signal"]["signal_id"]
    forged["cohorts"][0]["signal_ids"].remove(removed)
    reidentify_series(forged)
    validate_series(forged)  # Content consistency alone is deliberately insufficient.
    with pytest.raises(ProposalError, match="PROPOSAL_TREND_REPLAY_MISMATCH"):
        propose(signals, forged)


def test_valid_content_hash_cannot_launder_forged_raw_provenance(signals):
    series, _, _ = add_series(signals)
    forged = deepcopy(series)
    forged["imports"][0]["raw_sha256"] = "f" * 64
    forged["imports"][0]["raw_ref"] = "sha256:" + "f" * 64
    forged["imports"][0]["source_snapshot"]["sha256"] = "f" * 64
    reidentify_series(forged)
    validate_series(forged)
    with pytest.raises(ProposalError, match="PROPOSAL_TREND_REPLAY_MISMATCH"):
        propose(signals, forged)


def test_series_identity_mismatch_is_rejected(signals):
    series, _, _ = add_series(signals)
    series["series_id"] = "series-" + "f" * 64
    with pytest.raises(ProposalError, match="PROPOSAL_TREND_SERIES_INVALID"):
        propose(signals, series)


def test_source_hash_declaration_mismatch_is_not_promoted(signals):
    series, _, _ = add_series(signals, snapshot_changes={"sha256": "f" * 64})
    with pytest.raises(ProposalError, match="PROPOSAL_SOURCE_HASH_MISMATCH"):
        propose(signals, series)


def test_corrupted_m01_evidence_is_rejected(signals):
    series, _, _ = add_series(signals)
    with sqlite3.connect(signals.path) as db:
        db.execute("DROP TRIGGER raw_evidence_no_update")
        raw = db.execute("SELECT payload FROM raw_evidence").fetchone()[0]
        db.execute("UPDATE raw_evidence SET payload=?", (b"x" * len(raw),))
    with pytest.raises(ProposalError, match="PROPOSAL_M01_EVIDENCE_INVALID"):
        propose(signals, series)


def test_missing_and_corrupted_audience_evidence_is_rejected(tmp_path, signals):
    series, imported, _ = add_series(signals)
    audience, insight, _ = append_notes(tmp_path, signals, imported)
    with pytest.raises(ProposalError, match="PROPOSAL_AUDIENCE_LEDGER_REQUIRED"):
        propose(signals, series, audience_insight_ids=[insight["insight_id"]])
    with pytest.raises(ProposalError, match="PROPOSAL_AUDIENCE_EVIDENCE_INVALID"):
        propose(signals, series, audience_ledger=audience, audience_insight_ids=["aud-" + "f" * 64])
    with sqlite3.connect(audience.path) as db:
        db.execute("DROP TRIGGER audience_raw_no_update")
        raw = db.execute("SELECT payload FROM audience_raw").fetchone()[0]
        db.execute("UPDATE audience_raw SET payload=?", (b"x" * len(raw),))
    with pytest.raises(ProposalError, match="PROPOSAL_AUDIENCE_EVIDENCE_INVALID"):
        propose(signals, series, audience_ledger=audience, audience_insight_ids=[insight["insight_id"]])


def test_unrelated_identities_never_join_by_topic_or_source_label(tmp_path, signals):
    vn, _, _ = add_series(signals)
    us, imported_us, _ = add_series(signals, locale="en-US", geo="US")
    audience, insight, _ = append_notes(tmp_path, signals, imported_us)
    result = propose(signals, vn, audience_ledger=audience, audience_insight_ids=[insight["insight_id"]])
    assert len(result) == 2
    trend = next(p for p in result if p["trend"] is not None)
    assert trend["audience_insights"] == []
    assert insight["statements"][0]["text"] not in trend["creative"]["objective"]
    standalone = next(p for p in result if p["trend"] is None)
    assert standalone["kind"] == "AUDIENCE_CONTENT" and standalone["audience_insights"][0]["insight_id"] == insight["insight_id"]
    assert {r["locale"] for r in standalone["signal_evidence"]} == {"en-US"}
    both = propose_opportunities(trend_series=[us, vn], signal_ledger=signals, audience_ledger=audience,
        audience_insight_ids=[insight["insight_id"]], as_of=AS_OF)
    assert len(both) == 2
    assert next(p for p in both if p["trend"]["series_id"] == us["series_id"])["audience_insights"]
    assert next(p for p in both if p["trend"]["series_id"] == vn["series_id"])["audience_insights"] == []


def test_note_only_audience_is_optional_and_freshness_unknown_is_explicit(tmp_path):
    document = notes_document()
    document.pop("noted_at")
    audience, insight, _ = append_notes(tmp_path, document=document)
    proposal, = propose_opportunities(audience_ledger=audience, audience_insight_ids=[insight["insight_id"]], as_of=AS_OF)
    assert proposal["kind"] == "AUDIENCE_CONTENT" and proposal["trend"] is None
    assert proposal["signal_evidence"] == []
    assert proposal["audience_insights"][0]["freshness"] == "UNKNOWN"
    assert all(s["relation_to_trend"] == "NO_TREND" for s in proposal["audience_insights"][0]["statements"])
    assert any(r["code"] == "AUDIENCE_FRESHNESS_UNKNOWN" for r in proposal["reasons"])
    assert insight["statements"][0]["text"] in proposal["creative"]["objective"]


def test_linked_note_changes_the_creative_hypothesis_without_claiming_demand(tmp_path, signals):
    series, imported, _ = add_series(signals)
    audience, first, _ = append_notes(tmp_path, signals, imported)
    first_proposal, = propose(signals, series, audience_ledger=audience, audience_insight_ids=[first["insight_id"]])
    document = notes_document()
    document["statements"][0]["text"] = "A supplied note asks for an imaginative surprise."
    _, revised, _ = append_notes(tmp_path, signals, imported, document=document, store=audience, revision_of=first["insight_id"])
    revised_proposal, = propose(signals, series, audience_ledger=audience, audience_insight_ids=[revised["insight_id"]])
    assert first_proposal["creative"]["objective"] != revised_proposal["creative"]["objective"]
    assert document["statements"][0]["text"] in revised_proposal["creative"]["objective"]
    assert revised_proposal["trend"] == first_proposal["trend"]
    assert revised_proposal["status"] == "HYPOTHESIS" and revised_proposal["epistemic_status"] == "INFERRED"


def test_creative_format_is_an_optional_exploration_without_mandatory_media(signals):
    series, _, _ = add_series(signals, topic="SYNTHETIC silent vignette", row_changes={"signal_type": "CREATIVE_FORMAT"})
    proposal, = propose(signals, series)
    assert proposal["trend"]["category"] == "CREATIVE_FORMAT"
    assert "one optional direction" in proposal["creative"]["objective"]
    assert proposal["creative"]["media_hint"] is None


def test_correction_and_note_revision_lineage_are_preserved(tmp_path, signals):
    _, original, _ = add_series(signals, (0, 0, 10))
    corrected_series, corrected, _ = add_series(signals, (0, 1, 10), correction_of=original["import"]["import_id"])
    audience, parent, parent_raw = append_notes(tmp_path, signals, corrected)
    doc = notes_document()
    doc["source_note"] += " Caller revision."
    _, revised, _ = append_notes(tmp_path, signals, corrected, document=doc, store=audience, revision_of=parent["insight_id"])
    proposal, = propose(signals, corrected_series, audience_ledger=audience,
        audience_insight_ids=[revised["insight_id"], parent["insight_id"]])
    assert {r["lineage"]["parent_import_id"] for r in proposal["signal_evidence"]} == {original["import"]["import_id"]}
    assert {r["lineage"]["relationship"] for r in proposal["signal_evidence"]} == {"CORRECTION"}
    traces = {i["insight_id"]: i for i in proposal["audience_insights"]}
    assert traces[revised["insight_id"]]["lineage"]["parent_insight_id"] == parent["insight_id"]
    assert traces[parent["insight_id"]]["source_sha256"] == sha256_bytes(parent_raw)
    assert signals.get(original["import"]["import_id"])["signals"][1]["value"] == 0


def test_stable_order_deduplication_and_caller_inputs_do_not_mutate(tmp_path, signals):
    first, imported, _ = add_series(signals)
    second, _, _ = add_series(signals, topic="SYNTHETIC paper clouds")
    audience, insight, _ = append_notes(tmp_path, signals, imported)
    originals = deepcopy([first, second])
    options = {"product_ref": None, "cta": None, "media_hint": None, "objective": "Human creative direction"}
    common = dict(signal_ledger=signals, audience_ledger=audience, as_of=AS_OF, manual_idea="  A cloud learns to whisper.\r\n ")
    result = propose_opportunities(trend_series=[second, first, second], audience_insight_ids=[insight["insight_id"]] * 2,
                                   creative_overrides=options, **common)
    reordered = propose_opportunities(trend_series=[first, second], audience_insight_ids=[insight["insight_id"]],
                                      creative_overrides=dict(reversed(list(options.items()))), **common)
    assert canonical_bytes({"proposals": result}) == canonical_bytes({"proposals": reordered})
    assert [p["proposal_id"] for p in result] == sorted(p["proposal_id"] for p in result)
    assert len(result) == 3 and [first, second] == originals
    assert options["objective"] == "Human creative direction"
    manual = next(p for p in result if p["kind"] == "MANUAL_CONTENT")
    assert manual["signal_evidence"] == manual["audience_insights"] == [] and manual["trend"] is None


def test_manual_idea_is_exact_optional_and_content_only():
    text = "  Một đám mây kể chuyện không lời.\r\n  "
    proposal, = propose_opportunities(as_of=AS_OF, manual_idea=text)
    assert proposal["creative"]["objective"] == text
    assert proposal["manual_input"] == {"text": text, "text_sha256": sha256_bytes(text.encode("utf-8"))}
    assert all(proposal["creative"][key] is None for key in ("product_ref", "audience", "cta", "media_hint"))
    assert proposal["source_refs"] == ["sha256:" + sha256_bytes(text.encode("utf-8"))]
    assert propose_opportunities(as_of=AS_OF, manual_idea=text)[0] == proposal
    assert propose_opportunities(as_of=AS_OF, manual_idea=text + "\n")[0]["proposal_id"] != proposal["proposal_id"]
    edited, = propose_opportunities(as_of=AS_OF, manual_idea=text, creative_overrides={"objective": "An editable Human override", "product_ref": "product:synthetic"})
    assert edited["manual_input"] == proposal["manual_input"]
    assert edited["creative"]["objective"] == "An editable Human override"
    assert edited["creative_overrides"]["product_ref"] == "product:synthetic"
    assert edited["status"] == "HYPOTHESIS" and edited["rights_status"] == "UNKNOWN"


@pytest.mark.parametrize("field", ["status", "rights_status", "trend", "score", "spend_authorized", "publish"])
def test_creative_override_cannot_change_evidence_or_authority(field):
    with pytest.raises(ProposalError, match="INVALID_PROPOSAL_OVERRIDES"):
        propose_opportunities(as_of=AS_OF, manual_idea="A fictional story", creative_overrides={field: "verified"})


@pytest.mark.parametrize("contact", ["person@example.test", "Call +84 912 345 678", "Call ０９１２３４５６７８",
    "p\u200berson@example.test", "mailto:someone", "tel:+84912345678"],
    ids=["email", "phone", "fullwidth", "obfuscated", "mailto", "tel"])
@pytest.mark.parametrize("field", ["manual", "objective", "audience", "product_ref", "cta", "media_hint"])
def test_personal_contacts_are_rejected_with_bounded_errors(field, contact):
    kwargs = {"manual_idea": contact} if field == "manual" else {"manual_idea": "A fictional story", "creative_overrides": {field: contact}}
    with pytest.raises(ProposalError) as error:
        propose_opportunities(as_of=AS_OF, **kwargs)
    assert str(error.value) == "PROPOSAL_CONTACT_OR_IDENTIFIER_NOT_ALLOWED"
    assert contact not in str(error.value)


@pytest.mark.parametrize("field", ["manual", "objective", "audience", "product_ref", "cta", "media_hint"])
def test_human_creative_url_handle_and_numeric_product_url_are_permitted(field):
    public = "See https://shop.example.invalid/item/1731381331718341815 and @creator"
    kwargs = {"manual_idea": public} if field == "manual" else {
        "manual_idea": "A fictional story", "creative_overrides": {field: public},
    }
    proposal, = propose_opportunities(as_of=AS_OF, **kwargs)
    assert proposal["status"] == "HYPOTHESIS"
    assert proposal["rights_status"] == "UNKNOWN" and proposal["publication_status"] == "NOT_ATTEMPTED"
    assert (proposal["manual_input"]["text"] if field == "manual"
            else proposal["creative"][field]) == public
    assert validate_proposal(proposal) is proposal


def test_private_source_metadata_is_not_copied_or_laundered(signals):
    series, _, raw = add_series(signals, snapshot_changes={"source_ref": "https://example.test/person@example.test", "notes": "person@example.test"})
    proposal, = propose(signals, series)
    assert "person@example.test" not in json.dumps(proposal)
    assert {r["raw_sha256"] for r in proposal["signal_evidence"]} == {sha256_bytes(raw)}
    assert proposal["trend"]["series_sha256"] == digest(series)


def test_contact_in_topic_is_rejected_without_echo(signals):
    series, _, _ = add_series(signals, topic="Contact person@example.test")
    with pytest.raises(ProposalError, match="^PROPOSAL_CONTACT_OR_IDENTIFIER_NOT_ALLOWED$"):
        propose(signals, series)


def test_saved_proposal_tamper_and_forged_internal_references_are_detected():
    proposal, = propose_opportunities(as_of=AS_OF, manual_idea="An original imagined story")
    changed = deepcopy(proposal)
    changed["creative"]["objective"] += " changed"
    with pytest.raises(ProposalError, match="PROPOSAL_IDENTITY_MISMATCH"):
        validate_proposal(changed)
    changed = deepcopy(proposal)
    changed["manual_input"]["text"] += " changed"
    with pytest.raises(ProposalError, match="PROPOSAL_MANUAL_HASH_MISMATCH"):
        validate_proposal(reidentify_proposal(changed))
    changed = deepcopy(proposal)
    changed["reasons"][0]["source_refs"] = ["sig-" + "f" * 64]
    with pytest.raises(ProposalError, match="PROPOSAL_REASON_REFERENCE_MISMATCH"):
        validate_proposal(reidentify_proposal(changed))
    changed = deepcopy(proposal)
    changed["source_refs"].append("sha256:" + "f" * 64)
    with pytest.raises(ProposalError, match="PROPOSAL_SOURCE_REFERENCES_MISMATCH"):
        validate_proposal(reidentify_proposal(changed))


@pytest.mark.parametrize(("key", "value"), [("status", "VALIDATED_DEMAND"), ("rights_status", "VERIFIED"),
    ("publication_status", "PUBLISHED"), ("suggestion_only", False), ("budget", 1), ("email", "private")])
def test_proposal_schema_prevents_fact_and_authority_promotion(key, value):
    proposal, = propose_opportunities(as_of=AS_OF, manual_idea="An original imagined story")
    proposal[key] = value
    with pytest.raises(ProposalError, match="INVALID_OPPORTUNITY_PROPOSAL"):
        validate_proposal(reidentify_proposal(proposal))


@pytest.mark.parametrize("clock", ["2026-10-29", "2026-10-29T12:00:00", "garbage", None, "x" * 65], ids=["date", "naive", "invalid", "null", "long"])
def test_clock_is_explicit_and_timezone_aware(clock):
    with pytest.raises(ProposalError, match="INVALID_PROPOSAL_CLOCK"):
        propose_opportunities(as_of=clock)


@pytest.mark.parametrize("idea", ["", "  ", "x" * 8193, 4], ids=["empty", "blank", "oversize", "number"])
def test_manual_idea_validation_is_bounded(idea):
    with pytest.raises(ProposalError):
        propose_opportunities(as_of=AS_OF, manual_idea=idea)


def test_selection_limits_and_no_implicit_ledger_queries():
    with pytest.raises(ProposalError, match="TOO_MANY_PROPOSAL_INPUTS"):
        propose_opportunities(as_of=AS_OF, trend_series=(None for _ in range(MAX_SERIES + 1)))
    with pytest.raises(ProposalError, match="TOO_MANY_PROPOSAL_INPUTS"):
        propose_opportunities(as_of=AS_OF, audience_insight_ids=["aud-" + "a" * 64] * (MAX_INSIGHTS + 1))
    with pytest.raises(ProposalError, match="PROPOSAL_SELECTION_ARRAY_REQUIRED"):
        propose_opportunities(as_of=AS_OF, trend_series={})
    with pytest.raises(ProposalError, match="INVALID_PROPOSAL_INSIGHT_ID"):
        propose_opportunities(as_of=AS_OF, audience_insight_ids=[{}])


def test_schema_mirror_is_lf_self_contained_and_valid():
    path = Path(__file__).resolve().parents[1] / "contracts/OpportunityProposal.v1.schema.json"
    assert json.loads(path.read_bytes()) == schema()
    assert b"\r\n" not in path.read_bytes()
    assert '"$ref"' not in json.dumps(schema())
    Draft202012Validator.check_schema(schema())
    proposal, = propose_opportunities(as_of=AS_OF, manual_idea="A story with no required format")
    assert Draft202012Validator(schema()).is_valid(proposal)
