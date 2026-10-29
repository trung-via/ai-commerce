"""A10 v1 fan-in, real A03 contracts and offline CLI; synthetic/deidentified only."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.cli import main
from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import (
    CreativeStore, MockCreativeReasoner, brief_digest, generate_concepts, intake_brief,
    reframe_concept, revise_brief,
)
from ai_commerce.opportunity.bridge import (
    CreativeBridgeError, freeform_brief, opportunity_to_brief, schema, validate_bridge,
)
from ai_commerce.opportunity.core.models import OpportunityError, digest
from ai_commerce.opportunity.proposals import propose_opportunities

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-10-29T12:00:00Z"
TEXT = "  Một đám mây học cách thì thầm. 🌧️\r\n  "
spec = importlib.util.spec_from_file_location("a10_synthetic_demo", ROOT / "scripts/a10_demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Network access attempted")
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


@pytest.fixture
def proposal():
    return propose_opportunities(as_of=AS_OF, manual_idea=TEXT)[0]


@pytest.fixture
def integrated(tmp_path):
    output = tmp_path / "demo"
    manifest = demo.run_demo(output)
    return output, manifest


def read(path):
    return json.loads(path.read_bytes())


def write(path, record):
    path.write_bytes(canonical_bytes(record) + b"\n")
    return path


def cli(capsys, *args, expected=0):
    code = main(["opportunity", *map(str, args)])
    captured = capsys.readouterr()
    assert code == expected, captured
    if expected:
        assert not captured.out
        return json.loads(captured.err)
    assert not captured.err
    return json.loads(captured.out)


def test_receipt_schema_is_published_without_changing_a03():
    published = read(ROOT / "contracts/OpportunityCreativeBridge.v1.schema.json")
    assert published == schema()
    Draft202012Validator.check_schema(published)
    a03 = read(ROOT / "src/ai_commerce/contracts/schemas/ContentBrief.v0.1.schema.json")
    assert a03["required"] == ["brief_id", "objective"]
    assert a03["additionalProperties"] is True


def test_real_a03_intake_and_consumers_preserve_exact_manual_source(proposal, tmp_path):
    original = deepcopy(proposal)
    brief = opportunity_to_brief(proposal)
    assert validate_record("ContentBrief", brief) is brief
    assert intake_brief(brief) == brief
    assert brief["objective"] == brief["prompt"] == TEXT
    assert all(brief[k] is None for k in ("product_ref", "cta", "audience", "media_hint"))
    assert not {"target_audience", "main_topic", "objectives", "sections", "storyboard", "format"} & brief.keys()
    receipt = brief["opportunity_bridge"]
    assert validate_bridge(receipt) is receipt
    assert receipt["proposal"] == proposal == original
    assert receipt["proposal_sha256"] == digest(proposal)
    assert receipt["factual_status"] == "UNVERIFIED"
    assert receipt["epistemic_status"] == "INFERRED" and receipt["rights_status"] == "UNKNOWN"
    assert set(brief["source_refs"]) == {proposal["proposal_id"], *proposal["source_refs"]}
    store = CreativeStore(tmp_path / "creative")
    saved = store.save_brief(brief)
    assert store.save_brief(brief) == saved and store.load_brief(saved) == brief
    concept = generate_concepts(brief, MockCreativeReasoner())[0]
    store.save_concept(concept)
    reframed = reframe_concept(concept, lens="SYNTHETIC playful", changes={"premise": "SYNTHETIC new premise"})
    store.save_concept(reframed)
    assert store.load_selected(store.select(reframed.concept_id, reframed.revision).selection_id)["brief"] == brief


def test_human_edit_and_later_a03_revision_do_not_rewrite_evidence(proposal, tmp_path):
    edits = {"objective": "  Kể theo góc nhìn chiếc lá.\n", "audience": "Người tìm ý tưởng nhẹ nhàng",
             "media_hint": None, "cta": None}
    brief = opportunity_to_brief(proposal, edits=edits, brief_id="human-edited")
    assert brief["objective"] == edits["objective"] and brief["prompt"] == TEXT
    assert brief["opportunity_bridge"]["proposal"] == proposal
    assert brief["opportunity_bridge"]["human_edits"] == edits
    revised = revise_brief(brief, {"objective": "Một bản diễn giải tự do khác"})
    assert revised["brief_id"] == brief["brief_id"]
    assert revised["opportunity_bridge"] == brief["opportunity_bridge"]
    store = CreativeStore(tmp_path / "creative")
    assert store.save_brief(revised) != store.save_brief(brief)
    assert brief["objective"] == edits["objective"]


def test_explicit_product_cta_media_are_optional_human_inputs(proposal):
    edits = {"product_ref": "local:synthetic-product", "cta": "Một câu hỏi gợi mở", "media_hint": "Âm thanh"}
    brief = opportunity_to_brief(proposal, edits=edits)
    assert all(brief[k] == v for k, v in edits.items())
    upstream = propose_opportunities(as_of=AS_OF, manual_idea=TEXT, creative_overrides=edits)[0]
    assert opportunity_to_brief(upstream)["product_ref"] == edits["product_ref"]
    assert brief["opportunity_bridge"]["rights_status"] == "UNKNOWN"


@pytest.mark.parametrize("text", [TEXT, "夜の街を音で描く", "حكاية عن المطر", "e\u0301 🌿", "{an intentionally freeform idea}"])
def test_freeform_bypasses_a10_sources_and_preserves_unicode(text, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("A10 evidence must not gate freeform intake")
    monkeypatch.setattr("ai_commerce.opportunity.bridge.briefs.validate_proposal", unavailable)
    monkeypatch.setattr("ai_commerce.opportunity.signals.SignalLedger.get", unavailable)
    brief = freeform_brief(text)
    assert brief["objective"] == brief["prompt"] == text
    assert brief["product_ref"] is None and brief["cta"] is None
    receipt = brief["opportunity_bridge"]
    assert receipt["mode"] == "FREEFORM" and receipt["proposal"] is None
    assert receipt["epistemic_status"] == "HUMAN_CREATIVE_INPUT"
    assert receipt["factual_status"] == "UNVERIFIED"
    assert receipt["human_input"]["text_sha256"] == sha256_bytes(text.encode("utf-8"))
    assert intake_brief(text)["prompt"] == text if not text.startswith("{") else intake_brief(brief) == brief


def test_file_receipt_separates_raw_bom_newlines_and_unicode_text_hash():
    raw = b"\xef\xbb\xbf" + TEXT.encode("utf-8")
    brief = freeform_brief(TEXT, raw_bytes=raw, synthetic=True)
    source = brief["opportunity_bridge"]["human_input"]
    assert source["text"] == TEXT and source["raw_size_bytes"] == len(raw)
    assert source["raw_sha256"] == sha256_bytes(raw) != source["text_sha256"]
    assert set(brief["source_refs"]) == {"sha256:" + source["raw_sha256"], "sha256:" + source["text_sha256"]}
    assert brief["opportunity_bridge"]["data_kind"] == "SYNTHETIC_FIXTURE"
    with pytest.raises(CreativeBridgeError, match="RAW_TEXT_MISMATCH"):
        freeform_brief(TEXT, raw_bytes=b"different")


@pytest.mark.parametrize("text", ["", "  ", None, 7, "\ud800", "a\x00b"], ids=["empty", "blank", "null", "number", "surrogate", "nul"])
def test_bad_human_text_is_rejected(text):
    with pytest.raises(CreativeBridgeError):
        freeform_brief(text)


@pytest.mark.parametrize("edits", [
    {"source_refs": []}, {"opportunity_bridge": {}}, {"factual_status": "VERIFIED"},
    {"objective": None}, {"objective": " "}, {"product_ref": ""},
    {"sections": []}, {"audience": []}, {"media_hint": False}, ["objective"],
], ids=["refs", "receipt", "status", "null-objective", "blank-objective", "empty-product", "invented", "audience", "media", "array"])
def test_edits_cannot_change_evidence_or_invent_a_contract(proposal, edits):
    with pytest.raises(CreativeBridgeError):
        opportunity_to_brief(proposal, edits=edits)


@pytest.mark.parametrize("contact", ["person@example.invalid", "@synthetic_handle", "+84 912 345 678", "mailto:person", "https://example.invalid/contact"],
                         ids=["email", "handle", "phone", "mailto", "link"])
def test_contact_input_is_not_retained_or_echoed(contact, tmp_path, capsys):
    with pytest.raises(CreativeBridgeError) as failure:
        freeform_brief(contact)
    assert contact not in str(failure.value)
    result = cli(capsys, "bridge", "--text", contact, "--store", tmp_path / "creative", expected=2)
    assert result["error"] == "BRIDGE_CONTACT_OR_IDENTIFIER_NOT_ALLOWED"
    assert not (tmp_path / "creative").exists()


@pytest.mark.parametrize("change", ["missing-objective", "id", "rights", "source-status", "duplicate-refs", "manual-hash"])
def test_invalid_serialized_proposals_fail_without_source_promotion(proposal, change):
    bad = deepcopy(proposal)
    if change == "missing-objective":
        del bad["creative"]["objective"]
    elif change == "id":
        bad["proposal_id"] = "opp-" + "0" * 64
    elif change == "rights":
        bad["rights_status"] = "VERIFIED"
    elif change == "source-status":
        bad["source_status"] = "PROVIDER_VERIFIED"
    elif change == "duplicate-refs":
        bad["source_refs"].append(bad["source_refs"][0])
    else:
        bad["manual_input"]["text_sha256"] = "0" * 64
    with pytest.raises(OpportunityError):
        opportunity_to_brief(bad)


def test_receipt_hash_and_reference_tampering_is_detected(proposal):
    receipt = opportunity_to_brief(proposal)["opportunity_bridge"]
    receipt["factual_status"] = "VERIFIED"
    with pytest.raises(CreativeBridgeError):
        validate_bridge(receipt)
    receipt = opportunity_to_brief(proposal)["opportunity_bridge"]
    receipt["source_refs"] = []
    receipt["bridge_id"] = "bridge-" + digest({k: v for k, v in receipt.items() if k != "bridge_id"})
    with pytest.raises(CreativeBridgeError, match="SOURCE_REFERENCES"):
        validate_bridge(receipt)


def test_integrated_csv_json_notes_proposals_to_brief_keep_provenance(integrated):
    output, manifest = integrated
    assert manifest["data_kind"] == "SYNTHETIC_FIXTURE"
    assert manifest["external_provider_calls"] == 0
    assert manifest["trend_statuses"] == ["GROWING", "INSUFFICIENT_HISTORY"]
    assert manifest["duplicate_imports_checked"] and manifest["raw_bytes_replayed"]
    brief = read(output / "brief.json")
    receipt = brief["opportunity_bridge"]
    proposal = receipt["proposal"]
    assert receipt["data_kind"] == "SYNTHETIC_FIXTURE"
    assert proposal["epistemic_status"] == "INFERRED"
    trace = proposal["audience_insights"][0]
    assert [s["status"] for s in trace["statements"]] == ["OBSERVED_USER_SUPPLIED", "INFERRED", "OBSERVED_USER_SUPPLIED"]
    assert any(r["polarity"] == "COUNTEREVIDENCE" for r in proposal["reasons"])
    assert manifest["raw_sha256"]["csv"] in {s["raw_sha256"] for s in proposal["signal_evidence"]}
    assert trace["source_sha256"] == manifest["raw_sha256"]["audience"]
    assert 0 in [s["value"] for s in proposal["signal_evidence"]]
    assert set(proposal["source_refs"]) <= set(brief["source_refs"])
    assert receipt["validation_scope"] == "SERIALIZED_PROPOSAL_INTEGRITY_ONLY"
    assert all(brief[k] is None for k in ("product_ref", "cta", "media_hint", "audience"))


def test_sparse_missing_data_remains_explicit_in_bridge(integrated):
    output, _ = integrated
    proposal = next(p for p in read(output / "proposals.json")["proposals"] if p["topic"] == demo.JSON_TOPIC)
    brief = opportunity_to_brief(proposal, synthetic=True)
    assert brief["opportunity_bridge"]["proposal"] == proposal
    assert "INSUFFICIENT_HISTORY" in proposal["trend"]["conditions"] and proposal["trend"]["direction"] is None
    assert {(s["signal_status"], s["value"]) for s in proposal["signal_evidence"]} == {("AVAILABLE", 0), ("NOT_AVAILABLE", None)}
    assert not [r for r in proposal["reasons"] if r["polarity"] == "SUPPORT"]


def test_cli_all_audience_read_and_validation_operations(integrated, capsys):
    output, manifest = integrated
    common = ["--signals", output / "signals.sqlite"]
    validated = cli(capsys, "audience", "validate", "--file", output / "inputs/audience.json", *common)
    duplicate = cli(capsys, "audience", "append", "--file", output / "inputs/audience.json",
                    "--ledger", output / "audience.sqlite", *common)
    assert validated["insight_id"] == manifest["insight_id"] and duplicate["duplicate"]
    inspected = cli(capsys, "audience", "inspect", "--ledger", output / "audience.sqlite", "--insight-id", manifest["insight_id"], *common)
    history = cli(capsys, "audience", "history", "--ledger", output / "audience.sqlite", "--insight-id", manifest["insight_id"], *common)
    assert history == [inspected] == [validated]


def test_cli_missing_sources_fail_without_creating_ledger(tmp_path, capsys):
    missing = tmp_path / "missing.sqlite"
    assert cli(capsys, "inspect", "--ledger", missing, "--import-id", "a10-" + "0" * 64, expected=2)["error"]
    assert not missing.exists()
    assert cli(capsys, "propose", "--as-of", AS_OF) == {"proposals": []}
    assert cli(capsys, "propose", "--as-of", "not-a-clock", expected=2)["error"]


def test_cli_preserves_m02_unbound_context_rejection(integrated, capsys):
    output, manifest = integrated
    result = cli(capsys, "trend", "--ledger", output / "signals.sqlite", "--import-id", manifest["import_ids"][0],
                 "--topic", demo.CSV_TOPIC, "--contexts", output / "inputs/comparisons.json", "--as-of", AS_OF, expected=2)
    assert result["error"] == "UNBOUND_COMPARISON_CONTEXT"


def test_cli_store_rejects_network_share_before_io(capsys):
    result = cli(capsys, "bridge", "--text", TEXT, "--store", "//offline.invalid/share/creative", expected=2)
    assert result["error"] == "UNSAFE_PATH"


def test_cli_proposals_require_unambiguous_selection(integrated, capsys):
    output, manifest = integrated
    result = cli(capsys, "bridge", "--proposal", output / "proposals.json", expected=2)
    assert result["error"] == "BRIDGE_SELECT_EXACTLY_ONE_PROPOSAL"
    brief = cli(capsys, "bridge", "--proposal", output / "proposals.json", "--proposal-id", manifest["proposal_ids"][0])
    assert intake_brief(brief) == brief
    assert cli(capsys, "bridge", "--proposal", output / "proposals.json", "--proposal-id", "missing", expected=2)["error"]


@pytest.mark.parametrize("raw", [b'{"proposals":[],"proposals":[]}', b'{"proposals":NaN}', b'[]', b'\xff', b'{'],
                         ids=["duplicate-keys", "nonfinite", "array", "encoding", "malformed"])
def test_cli_strict_json_rejects_bad_bridge_documents(tmp_path, capsys, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    assert cli(capsys, "bridge", "--proposal", path, expected=2)["error"]


def test_cli_m04_replays_saved_series_and_rejects_rehashed_omission(integrated, capsys):
    output, _ = integrated
    series = read(output / "csv-trend.json")["series"]
    series["points"].pop()
    series["series_id"] = "series-" + digest({k: v for k, v in series.items() if k != "series_id"})
    path = write(output / "bad-series.json", series)
    before = (output / "signals.sqlite").read_bytes()
    assert cli(capsys, "propose", "--signals", output / "signals.sqlite", "--series", path, "--as-of", AS_OF, expected=2)["error"]
    assert (output / "signals.sqlite").read_bytes() == before


def test_cli_invalid_audience_notes_are_atomic_and_no_pii_echoed(tmp_path, capsys):
    notes = {"schema_version": "AudienceNotes.v1", "data_kind": "SYNTHETIC_FIXTURE",
             "source_note": "person@example.invalid", "statements": []}
    path = write(tmp_path / "notes.json", notes)
    ledger = tmp_path / "audience.sqlite"
    result = cli(capsys, "audience", "append", "--file", path, "--ledger", ledger, expected=2)
    assert "person" not in json.dumps(result) and not ledger.exists()


def test_demo_is_reproducible_and_preserves_raw_files(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    initial = demo.run_demo(first)
    before = {str(p): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    assert initial == demo.run_demo(second, replay_from=first)
    assert before == {str(p): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    files1 = {str(p.relative_to(first)): p.read_bytes() for p in first.rglob("*") if p.is_file() and p.name != "execution.json"}
    files2 = {str(p.relative_to(second)): p.read_bytes() for p in second.rglob("*") if p.is_file() and p.name != "execution.json"}
    assert files1 == files2
    assert files1[str(Path("inputs/signals.csv"))].startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in files1[str(Path("inputs/signals.csv"))]
    assert read(first / "execution.json")["mode"] == "FRESH_IMPORT"
    assert read(second / "execution.json")["mode"] == "REPLAY"
    with pytest.raises(ValueError, match="NEW_OUTPUT_DIRECTORY"):
        demo.run_demo(first)
    assert before == {str(p): p.read_bytes() for p in first.rglob("*") if p.is_file()}


def test_real_module_cli_prints_portable_json_and_bom_text_provenance(tmp_path):
    raw = b"\xef\xbb\xbf" + TEXT.encode("utf-8")
    path = tmp_path / "human.txt"
    path.write_bytes(raw)
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))}
    env.update(PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="ascii")
    result = subprocess.run([sys.executable, "-m", "ai_commerce", "opportunity", "bridge", "--text-file", str(path)],
                            cwd=ROOT, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    brief = json.loads(result.stdout.decode("ascii"))
    assert brief["prompt"] == TEXT and path.read_bytes() == raw
    assert brief["opportunity_bridge"]["human_input"]["raw_sha256"] == sha256_bytes(raw)
