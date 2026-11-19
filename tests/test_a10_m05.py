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


@pytest.mark.parametrize("contact", ["person@example.invalid", "+84 912 345 678", "mailto:person", "tel:+84912345678"],
                         ids=["email", "phone", "mailto", "tel"])
def test_contact_input_is_not_retained_or_echoed(contact, tmp_path, capsys):
    with pytest.raises(CreativeBridgeError) as failure:
        freeform_brief(contact)
    assert contact not in str(failure.value)
    result = cli(capsys, "bridge", "--text", contact, "--store", tmp_path / "creative", expected=2)
    assert result["error"] == "BRIDGE_CONTACT_OR_IDENTIFIER_NOT_ALLOWED"
    assert not (tmp_path / "creative").exists()


@pytest.mark.parametrize("text", [
    "Khám phá https://shop.example.invalid/item/1731381331718341815",
    "Theo dõi @synthetic_handle để xem thêm",
    "Kể chuyện về một đường dẫn https://example.invalid/truyen/2026",
])
def test_human_freeform_urls_and_handles_remain_optional_and_unverified(text):
    brief = freeform_brief(text)
    assert brief["objective"] == brief["prompt"] == text
    assert brief["opportunity_bridge"]["mode"] == "FREEFORM"
    assert brief["opportunity_bridge"]["factual_status"] == "UNVERIFIED"
    assert brief["product_ref"] is None and brief["cta"] is None


def test_explicit_human_affiliate_style_url_cta_is_preserved(proposal):
    url = "https://shop.example.invalid/item/1731381331718341815"
    edits = {"cta": "Xem sản phẩm: " + url, "product_ref": url, "media_hint": "@creator"}
    brief = opportunity_to_brief(proposal, edits=edits)
    assert all(brief[k] == v for k, v in edits.items())
    assert brief["opportunity_bridge"]["human_edits"] == edits
    assert brief["opportunity_bridge"]["rights_status"] == "UNKNOWN"
    assert brief["opportunity_bridge"]["publication_status"] == "NOT_ATTEMPTED"


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


@pytest.fixture
def automation_job(tmp_path):
    from ai_commerce.opportunity import prepare_pinned_source
    raw = b"\xef\xbb\xbf" + canonical_bytes({"schema_version": "ManualSignalRows.v1", "rows": [
        demo._row(demo.JSON_TOPIC, day, value) for day, value in ((27, 0), (28, 0), (29, 4))]}) + b"\r\n"
    snapshot = demo._snapshot(raw, "automation")
    prepared = prepare_pinned_source(raw, expected_sha256=sha256_bytes(raw), source_snapshot=snapshot, file_format="json")
    reference = {"import_id": prepared.record["import_id"], "signal_id": prepared.signals[0]["signal_id"],
                 "raw_sha256": sha256_bytes(raw), "evidence_ref": "sha256:" + sha256_bytes(raw)}
    notes = {"schema_version": "AudienceNotes.v1", "data_kind": "SYNTHETIC_FIXTURE",
             "source_note": "SYNTHETIC Human-authored fixture; no real participants.", "noted_at": AS_OF,
             "statements": [{"kind": "JOB_TO_BE_DONE", "status": "OBSERVED_USER_SUPPLIED",
                             "text": "SYNTHETIC quiet creative break.", "evidence_refs": [reference]}]}
    raw_notes = b"\xef\xbb\xbf" + canonical_bytes(notes) + b"\r\n"
    (tmp_path / "signals.json").write_bytes(raw)
    (tmp_path / "notes.json").write_bytes(raw_notes)
    manifest = {"schema_version": "A10AutomationJob.v1", "job_id": "synthetic-job-1", "source_sha": "b" * 40,
                "topic": demo.JSON_TOPIC, "as_of": AS_OF, "provider": None, "ai_assessment": False,
                "sources": [{"file": "signals.json", "format": "json", "sha256": sha256_bytes(raw),
                             "snapshot": snapshot, "comparison_context": demo._comparison()}],
                "audience": {"file": "notes.json", "sha256": sha256_bytes(raw_notes), "origin": "HUMAN"},
                "allowed_effects": ["IMPORT_SIGNALS", "APPEND_HUMAN_AUDIENCE", "CREATE_BRIEF", "AI_RESEARCH"]}
    return manifest, {"base_dir": tmp_path, "source_sha": manifest["source_sha"], "state_dir": tmp_path / "state",
                      "mode": "offline", "permissions": manifest["allowed_effects"]}


def automation_selection(result, *, use_ai=False):
    proposal = result["proposals"][0]
    return {**result["selection_template"], "proposal_id": proposal["proposal_id"], "proposal_sha256": digest(proposal),
            "use_ai_assessment": use_ai,
            "assessment_receipt_id": result["assessment"]["receipt_id"] if use_ai else None}


def human_identity(selection):
    # Trusted test user session, separate from manifest and model output.
    return {"actor": "HUMAN", "subject": "synthetic-user", "selection_sha256": digest(selection)}


def automation_capability(profile="codex-cli"):
    from ai_commerce.opportunity import ai_provider_profile
    provider = ai_provider_profile(profile)
    command = "codex" if profile == "codex-cli" else "antigravity"
    return {"argv": [command, "exec", "--model", provider["model"], "--effort", provider["effort"]],
            "model": provider["model"], "effort": provider["effort"], "structured_json_stdio": True,
            "read_only": True, "no_tools": True}


def automation_output(task):
    # A distinct mocked model score, not the deterministic policy's ranking.
    scores = [{"proposal_id": p["proposal_id"], "proposal_sha256": digest(p), "eligible": True,
               "score": 91, "rationale": "SYNTHETIC model creative assessment, not observed demand.",
               "citations": [{"ref": p["proposal_id"], "sha256": digest(p)},
                             {"ref": p["signal_evidence"][0]["raw_ref"], "sha256": p["signal_evidence"][0]["raw_sha256"]}],
               "counterevidence": []} for p in task["context"]["proposals"]]
    selected = sorted(scores, key=lambda s: s["proposal_id"])[0] if scores else None
    return canonical_bytes({"schema_version": "A10AIResearchOutput.v1", "task_id": task["task_id"],
        "task_sha256": digest(task), "provider": task["provider"], "status": "OK", "effects": [],
        "reported_model": "gpt-6.1-sol" if task["provider"]["profile"] == "chatgpt-attended" else task["provider"]["model"],
        "reported_effort": "xhigh", "claims": [
            {"kind": "MODEL_INFERENCE", "origin": "MODEL", "text": "SYNTHETIC optional machine assessment.",
             "citations": [task["sources"][0]]}],
        "decision": {"schema_version": "A10OpportunityChoice.v1", "outcome": "SELECT" if selected else "NO_SUITABLE_OPPORTUNITY",
                     "selected_proposal_id": selected["proposal_id"] if selected else None,
                     "selected_proposal_sha256": selected["proposal_sha256"] if selected else None, "scores": scores}})


def test_automation_plan_and_offline_full_pipeline_replay_without_provider(automation_job):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    def forbidden(*args, **kw):
        raise AssertionError("Offline automation attempted a provider")
    plan = run_automation(manifest, **{**kwargs, "mode": "plan"}, runner=forbidden)
    assert plan["status"] == "PLAN" and not kwargs["state_dir"].exists()
    assert plan == run_automation(manifest, **{**kwargs, "mode": "plan"}, runner=forbidden)
    completed = run_automation(manifest, **kwargs, runner=forbidden)
    assert completed["assessment"] is None and completed["hypotheses"][0]["status"] == "GROWING"
    assert completed["status"] == "COMPLETE" and completed["publication_status"] == "NOT_ATTEMPTED"
    brief = completed["brief"]
    assert intake_brief(brief) == brief and validate_record("ContentBrief", brief) == brief
    assert all(brief[k] is None for k in ("product_ref", "cta", "media_hint", "audience"))
    assert brief["opportunity_bridge"]["proposal"] == completed["proposals"][0]
    assert brief["opportunity_automation"]["decision"] == completed["decision"]
    assert completed["decision"]["actor"] == "SYSTEM" and completed["selection_template"] is None
    assert completed["decision"]["assessment_receipt_id"] is None
    assert completed["next_state"] == completed["next_step"] == "READY_FOR_A03_DISPATCH"
    assert completed["brief_sha256"] == digest(brief) and completed["decision_sha256"] == digest(completed["decision"])
    assert set(completed["metrics"].values()) == {"UNKNOWN"}
    job_dir = kwargs["state_dir"] / manifest["job_id"]
    before = {p.name: p.read_bytes() for p in job_dir.iterdir() if p.is_file()}
    assert completed == run_automation(manifest, **kwargs, runner=forbidden)
    assert before == {p.name: p.read_bytes() for p in job_dir.iterdir() if p.is_file()}
    from ai_commerce.opportunity.audience import AudienceLedger
    from ai_commerce.opportunity import SignalLedger
    assert AudienceLedger(job_dir / "audience.sqlite").raw_bytes(completed["insight_ids"][0],
        signal_ledger=SignalLedger(job_dir / "signals.sqlite")) == (kwargs["base_dir"] / "notes.json").read_bytes()


@pytest.mark.parametrize("change,reason,status", [
    ("code-pin", "JOB_SOURCE_SHA_MISMATCH", "FAIL"),
    ("raw-pin", "JOB_SOURCE_HASH_MISMATCH", "FAIL"),
    ("snapshot-pin", "JOB_SOURCE_HASH_MISMATCH", "FAIL"),
    ("no-data", "NO_SIGNAL_DATA", "WAIT"),
    ("no-provider", "PROVIDER_UNAVAILABLE", "WAIT"),
    ("permissions", "PERMISSION_DENIED", "WAIT"),
    ("model-audience", "AUDIENCE_HUMAN_ORIGIN_REQUIRED", "FAIL"),
    ("unknown-field", "JOB_MANIFEST_INVALID", "FAIL"),
    ("outside-input", "JOB_INPUT_OUTSIDE_ROOT", "FAIL"),
    ("duplicate-source", "JOB_DUPLICATE_SOURCE", "FAIL"),
])
def test_automation_preflight_fails_closed_without_journal_writes(automation_job, change, reason, status):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest, kwargs = deepcopy(manifest), dict(kwargs)
    if change == "code-pin":
        kwargs["source_sha"] = "c" * 40
    elif change == "raw-pin":
        (kwargs["base_dir"] / "signals.json").write_bytes(b"changed")
    elif change == "snapshot-pin":
        manifest["sources"][0]["snapshot"]["sha256"] = "c" * 64
    elif change == "no-data":
        manifest["sources"] = []
    elif change == "no-provider":
        manifest["ai_assessment"] = True
        kwargs["mode"] = "execute"
    elif change == "permissions":
        kwargs["permissions"] = []
    elif change == "model-audience":
        manifest["audience"]["origin"] = "MODEL"
    elif change == "unknown-field":
        manifest["manual_idea"] = "AI text may not become a manual idea"
    elif change == "outside-input":
        manifest["sources"][0]["file"] = "/outside.json"
    elif change == "duplicate-source":
        manifest["sources"].append(deepcopy(manifest["sources"][0]))
    result = run_automation(manifest, **kwargs)
    assert (result["status"], result["reason"]) == (status, reason)
    assert result["brief"] is None and not kwargs["state_dir"].exists()


@pytest.mark.parametrize("change", ["proposal-hash", "manifest-hash", "actor", "machine-without-receipt", "ai-human-edits"])
def test_automation_selection_binds_operator_proposal_and_sidecar(automation_job, change):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest["decision_mode"] = "manual"
    wait = run_automation(manifest, **kwargs)
    assert wait["reason"] == "SELECTION_REQUIRED"
    selection = automation_selection(wait)
    if change == "proposal-hash":
        selection["proposal_sha256"] = "a" * 64
    elif change == "manifest-hash":
        selection["manifest_sha256"] = "a" * 64
    elif change == "actor":
        selection["actor"] = "MODEL"
    elif change == "machine-without-receipt":
        selection["use_ai_assessment"] = True
        selection["assessment_receipt_id"] = "fake"
    elif change == "ai-human-edits":
        selection["human_edits"] = {"objective": "AI generated text"}
    job_dir = kwargs["state_dir"] / manifest["job_id"]
    before = {p.name: p.read_bytes() for p in job_dir.glob("*.sqlite")}
    result = run_automation(manifest, **kwargs, selection=selection, human_identity=human_identity(selection))
    assert result["status"] == "FAIL" and result["brief"] is None
    assert not (job_dir / "brief.json").exists()
    assert before == {p.name: p.read_bytes() for p in job_dir.glob("*.sqlite")}


@pytest.mark.parametrize("profile", ["codex-cli", "antigravity-cli"])
def test_automation_fake_provider_sidecar_preserves_m04_and_runs_once(automation_job, profile):
    from types import SimpleNamespace
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.update(provider=profile, ai_assessment=True)
    kwargs = {**kwargs, "mode": "execute", "capability": automation_capability(profile)}
    calls = []
    def fake(argv, **kw):
        task = json.loads(kw["input"])["task"]
        calls.append(task)
        assert task["stage"] == "OPPORTUNITY_ASSESSMENT" and task["context"]["proposals"]
        assert (kwargs["state_dir"] / manifest["job_id"] / "invocation.json").exists()
        return SimpleNamespace(returncode=0, stdout=automation_output(task))
    completed = run_automation(manifest, **kwargs, runner=fake)
    assert completed["status"] == "COMPLETE" and len(calls) == 1
    assert completed["proposals"] == calls[0]["context"]["proposals"]
    assert all(p["manual_input"] is None and p["creative_overrides"] == {} for p in completed["proposals"])
    assert completed["decision"]["actor"] == "BRAIN"
    assert completed["decision"]["assessment_receipt_id"] == completed["assessment"]["receipt_id"]
    assert completed["assessment"]["execution"]["kind"] == "MOCK_AUTHENTICATED_RUNNER"
    assert completed["assessment"]["reported_model"] == calls[0]["provider"]["model"]
    assert completed["decision"]["scoring"][0]["score"] == 91
    assert completed["decision"]["selected_proposal_id"] == completed["assessment"]["decision"]["selected_proposal_id"]
    assert completed["next_state"] == "READY_FOR_A03_DISPATCH"
    assert completed["brief"]["opportunity_bridge"]["human_edits"] == {}
    assert completed == run_automation(manifest, **kwargs, runner=fake)
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "invalid-output", "lost-receipt"])
def test_automation_ambiguous_invocation_is_never_replayed(automation_job, failure):
    from types import SimpleNamespace
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.update(provider="codex-cli", ai_assessment=True)
    kwargs = {**kwargs, "mode": "execute", "capability": automation_capability()}
    calls = []
    def fake(argv, **kw):
        calls.append(argv)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 60)
        raw = b'{"effects":["publish"]}' if failure == "invalid-output" else automation_output(json.loads(kw["input"])["task"])
        return SimpleNamespace(returncode=0, stdout=raw)
    first = run_automation(manifest, **kwargs, runner=fake)
    if failure == "lost-receipt":
        (kwargs["state_dir"] / manifest["job_id"] / "assessment.json").unlink()
    else:
        assert first["assessment"]["status"] == "UNKNOWN_EFFECT"
    resumed = run_automation(manifest, **kwargs, runner=fake)
    assert resumed["status"] == "WAIT" and resumed["reason"] == "UNKNOWN_EFFECT"
    assert resumed["next_step"] == "RECONCILE_WITH_OPERATOR_NO_RETRY" and resumed["brief"] is None
    assert len(calls) == 1


def test_automation_attended_handoff_resumes_only_with_explicit_output(automation_job):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.update(provider="chatgpt-attended", ai_assessment=True)
    manifest["decision_mode"] = "manual"
    kwargs = {**kwargs, "mode": "execute"}
    def forbidden(*args, **kw):
        raise AssertionError("Attended handoff attempted a process or Send")
    handoff = run_automation(manifest, **kwargs, runner=forbidden)
    assert handoff["status"] == "WAIT" and handoff["assessment"]["status"] == "NEEDS_HUMAN"
    assert handoff["handoff"]["send_performed"] is False
    response = automation_output(handoff["ai_task"])
    assessed = run_automation(manifest, **kwargs, runner=forbidden, attended_output=response)
    assert assessed["reason"] == "SELECTION_REQUIRED" and assessed["assessment"]["status"] == "OK"
    selection = automation_selection(assessed, use_ai=True)
    completed = run_automation(manifest, **kwargs, runner=forbidden, selection=selection, human_identity=human_identity(selection))
    assert completed["status"] == "COMPLETE"
    assert completed["decision"]["actor"] == "HUMAN" and completed["assessment"]["execution"] is None
    assert completed == run_automation(manifest, **kwargs, runner=forbidden, selection=selection, human_identity=human_identity(selection))


def test_automation_cli_transport_writes_bounded_event_and_preserves_old_commands(automation_job, capsys):
    manifest, kwargs = automation_job
    path = write(kwargs["base_dir"] / "job.json", manifest)
    event_path = kwargs["base_dir"] / "event.json"
    args = ["opportunity", "automate", "--manifest", str(path), "--source-sha", kwargs["source_sha"],
            "--state-dir", str(kwargs["state_dir"]), "--result", str(event_path), "--mode", "plan"]
    assert main(args) == 0
    captured = capsys.readouterr()
    event = json.loads(captured.out)
    assert not captured.err and read(event_path) == event and event["status"] == "PLAN"
    assert not kwargs["state_dir"].exists()
    wait_path = kwargs["base_dir"] / "wait.json"
    wait_args = list(args)
    wait_args[wait_args.index(str(event_path))] = str(wait_path)
    wait_args[-1] = "offline"
    assert main(wait_args) == 3
    waiting = capsys.readouterr()
    assert not waiting.err and json.loads(waiting.out)["reason"] == "PERMISSION_DENIED"
    assert main([*args[:-1], "offline"]) == 2
    failed = json.loads(capsys.readouterr().err)
    # A changed event cannot overwrite an immutable transport result filename.
    assert failed["error"] == "JOB_STATE_CONFLICT" and read(event_path) == event


def test_automation_cli_offline_creates_brief_without_selection(automation_job, capsys):
    manifest, kwargs = automation_job
    job_path = write(kwargs["base_dir"] / "job.json", manifest)
    common = ["opportunity", "automate", "--manifest", str(job_path), "--mode", "offline",
              "--source-sha", kwargs["source_sha"], "--state-dir", str(kwargs["state_dir"])]
    for effect in manifest["allowed_effects"]:
        common += ["--allow-effect", effect]
    assert main([*common, "--result", str(kwargs["base_dir"] / "complete.json")]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    completed = json.loads(captured.out)
    assert completed["status"] == "COMPLETE" and intake_brief(completed["brief"]) == completed["brief"]
    assert completed["decision"]["actor"] == "SYSTEM" and completed["next_state"] == "READY_FOR_A03_DISPATCH"


def test_automation_topic_only_needs_no_human_notes_or_model(automation_job):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.pop("audience")
    # Offline explicitly uses SYSTEM even if an advisory provider was configured.
    manifest.update(provider="codex-cli", ai_assessment=True)
    def forbidden(*args, **kw):
        raise AssertionError("Offline must not invoke an AI provider")
    result = run_automation(manifest, **kwargs, runner=forbidden)
    assert result["status"] == "COMPLETE" and result["brief"]["product_ref"] is None
    assert result["insight_ids"] == [] and result["assessment"] is None and result["handoff"] is None
    assert result["decision"]["actor"] == "SYSTEM" and result["decision"]["policy"] == "A10EligibleFallback.v1"
    assert "AI_RESEARCH" not in result["required_effects"]
    assert result["decision"]["scoring"][0]["score"] == 80
    assert result["dispatch_status"] == "NOT_ATTEMPTED"


@pytest.mark.parametrize("scenario", ["no-proposals", "ineligible"])
def test_automation_terminal_no_suitable_is_immutable(automation_job, scenario):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.pop("audience")
    if scenario == "no-proposals":
        manifest["topic"] = "SYNTHETIC absent topic"
    else:
        manifest["as_of"] = "2026-11-17T12:00:00Z"
    first = run_automation(manifest, **kwargs)
    assert first["status"] == "COMPLETE" and first["reason"] == "NO_SUITABLE_OPPORTUNITY"
    assert first["decision"]["outcome"] == "NO_SUITABLE_OPPORTUNITY" and first["brief"] is None
    assert first["decision"]["selected_proposal_id"] is None and first["decision"]["selected_proposal_sha256"] is None
    assert bool(first["proposals"]) == (scenario == "ineligible")
    job_dir = kwargs["state_dir"] / manifest["job_id"]
    assert not (job_dir / "brief.json").exists()
    before = {p.name: p.read_bytes() for p in job_dir.iterdir() if p.is_file()}
    assert first == run_automation(manifest, **kwargs)
    assert before == {p.name: p.read_bytes() for p in job_dir.iterdir() if p.is_file()}


@pytest.mark.parametrize("change", ["missing-decision", "score", "boolean-score", "missing-score", "citations",
                                  "proposal-sha", "model", "effort", "source-sha", "human-actor", "brain-actor"])
def test_automation_invalid_model_choice_cannot_create_brief_or_retry(automation_job, change):
    from types import SimpleNamespace
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.update(provider="codex-cli", ai_assessment=True)
    kwargs = {**kwargs, "mode": "execute", "capability": automation_capability()}
    calls = []
    def fake(argv, **kw):
        task = json.loads(kw["input"])["task"]
        calls.append(task)
        value = json.loads(automation_output(task))
        score = value["decision"]["scores"][0]
        if change == "missing-decision":
            value.pop("decision")
        elif change == "score":
            score["score"] = 101
        elif change == "boolean-score":
            score["score"] = True
        elif change == "missing-score":
            score.pop("score")
        elif change == "citations":
            score["citations"] = [{"ref": "sha256:" + "f" * 64, "sha256": "f" * 64}]
        elif change == "proposal-sha":
            score["proposal_sha256"] = "f" * 64
        elif change == "model":
            value["reported_model"] = "unconfigured-model"
        elif change == "effort":
            value["reported_effort"] = "medium"
        elif change == "source-sha":
            value["source_sha"] = "f" * 40
        else:
            value["decision"]["actor"] = "HUMAN" if change == "human-actor" else "BRAIN"
        return SimpleNamespace(returncode=0, stdout=canonical_bytes(value))
    result = run_automation(manifest, **kwargs, runner=fake)
    assert result["status"] == "WAIT" and result["reason"] == "UNKNOWN_EFFECT" and result["brief"] is None
    assert result["decision"] is None and result["assessment"]["decision"] is None
    assert result == run_automation(manifest, **kwargs, runner=fake) and len(calls) == 1
    job_dir = kwargs["state_dir"] / manifest["job_id"]
    assert not (job_dir / "decision.json").exists() and not (job_dir / "brief.json").exists()


@pytest.mark.parametrize("profile", ["codex-cli", "antigravity-cli"])
def test_automation_execute_requires_provider_capability_without_fallback(automation_job, profile):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.update(provider=profile, ai_assessment=True)
    def forbidden(*args, **kw):
        raise AssertionError("Missing capability cannot invoke a process")
    result = run_automation(manifest, **{**kwargs, "mode": "execute"}, runner=forbidden)
    assert result["status"] == "WAIT" and result["reason"] == "PROVIDER_UNAVAILABLE"
    assert result["assessment"]["reason"] == "CAPABILITY_REQUIRED" and result["brief"] is None
    assert result["handoff"] is None and result["decision"] is None


def test_automation_advisory_receipt_is_not_an_autonomous_chooser(automation_job):
    from types import SimpleNamespace
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.update(provider="codex-cli", ai_assessment=True)
    calls = []
    def advisory(argv, **kw):
        calls.append(argv)
        value = json.loads(automation_output(json.loads(kw["input"])["task"]))
        value["decision"] = None
        return SimpleNamespace(returncode=0, stdout=canonical_bytes(value))
    kwargs = {**kwargs, "mode": "execute", "capability": automation_capability(), "runner": advisory}
    result = run_automation(manifest, **kwargs)
    assert result["status"] == "WAIT" and result["reason"] == "AI_DECISION_REQUIRED"
    assert result["assessment"]["status"] == "OK" and result["brief"] is None
    assert result == run_automation(manifest, **kwargs) and len(calls) == 1


@pytest.mark.parametrize("change", ["brain-selection", "human-without-identity", "identity-hash"])
def test_automation_optional_manual_selection_requires_real_user_boundary(automation_job, change):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest["decision_mode"] = "manual"
    waiting = run_automation(manifest, **kwargs)
    assert waiting["reason"] == "SELECTION_REQUIRED"
    selected = automation_selection(waiting)
    identity = human_identity(selected)
    if change == "brain-selection":
        selected["actor"] = "BRAIN"
        identity = human_identity(selected)
    elif change == "human-without-identity":
        identity = None
    else:
        identity["selection_sha256"] = "f" * 64
    result = run_automation(manifest, **kwargs, selection=selected, human_identity=identity)
    assert result["status"] == "FAIL" and result["brief"] is None and result["decision"] is None
    assert result["reason"] in ("JOB_SELECTION_AUTHORITY_REQUIRED", "JOB_HUMAN_IDENTITY_REQUIRED")


def test_automation_decision_tampering_and_changed_job_cannot_overwrite(automation_job):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    first = run_automation(manifest, **kwargs)
    assert first["status"] == "COMPLETE"
    job_dir = kwargs["state_dir"] / manifest["job_id"]
    original_brief = (job_dir / "brief.json").read_bytes()
    changed = {**manifest, "as_of": "2026-10-29T13:00:00Z"}
    rejected = run_automation(changed, **kwargs)
    assert rejected["status"] == "FAIL" and rejected["reason"] == "JOB_STATE_CONFLICT"
    decision = read(job_dir / "decision.json")
    decision["scoring"][0]["score"] = 100
    decision["decision_id"] = "a10decision-" + digest({k: v for k, v in decision.items() if k != "decision_id"})
    write(job_dir / "decision.json", decision)
    rejected = run_automation(manifest, **kwargs)
    assert rejected["status"] == "FAIL" and rejected["reason"] == "DECISION_BINDING_OR_IDENTITY_MISMATCH"
    assert rejected["brief"] is None and (job_dir / "brief.json").read_bytes() == original_brief


def test_automation_model_can_choose_terminal_no_suitable(automation_job):
    from types import SimpleNamespace
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.update(provider="codex-cli", ai_assessment=True)
    calls = []
    def decline(argv, **kw):
        calls.append(argv)
        value = json.loads(automation_output(json.loads(kw["input"])["task"]))
        for score in value["decision"]["scores"]:
            score.update(eligible=False, score=20, rationale="SYNTHETIC assessment finds no suitable creative opportunity.")
        value["decision"].update(outcome="NO_SUITABLE_OPPORTUNITY", selected_proposal_id=None, selected_proposal_sha256=None)
        return SimpleNamespace(returncode=0, stdout=canonical_bytes(value))
    kwargs = {**kwargs, "mode": "execute", "capability": automation_capability(), "runner": decline}
    result = run_automation(manifest, **kwargs)
    assert result["status"] == "COMPLETE" and result["reason"] == "NO_SUITABLE_OPPORTUNITY"
    assert result["decision"]["actor"] == "BRAIN" and result["brief"] is None
    assert result["decision"]["assessment_sha256"] == digest(result["assessment"])
    assert result == run_automation(manifest, **kwargs) and len(calls) == 1


def test_automation_self_report_cannot_forge_brain_authority(automation_job):
    from ai_commerce.opportunity import run_automation, make_opportunity_decision, parse_ai_research_output
    manifest, kwargs = automation_job
    manifest.update(provider="chatgpt-attended", ai_assessment=True)
    kwargs = {**kwargs, "mode": "execute"}
    handoff = run_automation(manifest, **kwargs)
    assert handoff["assessment"]["reason"] == "ATTENDED_HANDOFF_REQUIRED"
    raw = automation_output(handoff["ai_task"])
    result = run_automation(manifest, **kwargs, attended_output=raw)
    assert result["status"] == "WAIT" and result["reason"] == "PROVIDER_UNAVAILABLE"
    assert result["assessment"]["status"] == "OK" and result["assessment"]["execution"] is None
    forged = parse_ai_research_output(result["ai_task"], raw, effect="PROCESS_INVOKED")
    with pytest.raises(OpportunityError, match="DECISION_AUTHENTICATED_CHOOSER_REQUIRED"):
        make_opportunity_decision(proposals=result["proposals"], job_id=manifest["job_id"],
            manifest_sha256=digest(manifest), source_sha=manifest["source_sha"], actor="BRAIN",
            assessment=forged, assessment_task=result["ai_task"])
    assert result["brief"] is None and result["decision"] is None


def local_production_owner(manifest, kwargs):
    from ai_commerce.production_orchestration import LocalAuthorization, ProductionPipelineOrchestrator
    return ProductionPipelineOrchestrator(state_dir=kwargs["state_dir"].parent / "production",
        source_sha=kwargs["source_sha"], local_authorization=LocalAuthorization(
            journey_id="synthetic:a10-a03", cycle_id="cycle:1", job_id=manifest["job_id"],
            source_sha=kwargs["source_sha"], authorization_ref="fixture:local-only"))


@pytest.mark.parametrize("mode", ["offline", "mock-execute"])
def test_automation_committed_m05_dispatches_real_local_a03_without_clicks(automation_job, mode):
    from types import SimpleNamespace
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    manifest.pop("audience")
    calls = []
    def mock_provider(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=automation_output(json.loads(kw["input"])["task"]))
    if mode == "mock-execute":
        manifest.update(provider="codex-cli", ai_assessment=True)
        kwargs = {**kwargs, "mode": "execute", "capability": automation_capability(), "runner": mock_provider}
    owner = local_production_owner(manifest, kwargs)
    result = run_automation(manifest, **kwargs, production=owner)
    downstream = owner.last_result
    assert result["status"] == "COMPLETE" and downstream.status == "SUCCESS"
    assert result["selection_template"] is None
    assert result["brief_ref"] == downstream.completed.spec.local_handoff.brief_ref
    assert sha256_bytes(Path(result["brief_ref"]).read_bytes()) == result["brief_bytes_sha256"]
    assert downstream.completed.spec.local_handoff.producer_event_id == result["event_id"]
    assert downstream.event.receipt.consumer_id == "ai_commerce.creative.briefs:intake_brief"
    assert downstream.event.receipt.outcome == "SUCCESS"
    assert downstream.event.receipt.output_sha256 == result["brief_sha256"]
    store = CreativeStore(owner._state_dir / "creative")
    assert store.load_brief(result["brief_sha256"]) == result["brief"]
    if mode == "mock-execute":
        assert result["assessment"]["execution"]["kind"] == "MOCK_AUTHENTICATED_RUNNER"
    assert len(calls) == (mode == "mock-execute")
    replay_owner = local_production_owner(manifest, kwargs)
    assert run_automation(manifest, **kwargs, production=replay_owner) == result
    assert replay_owner.last_result == downstream and len(calls) == (mode == "mock-execute")


@pytest.mark.parametrize("scenario", ["no-suitable", "permission", "unavailable", "unknown", "fail"])
def test_automation_non_success_never_offers_production_event(automation_job, scenario):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    if scenario == "no-suitable":
        manifest["topic"] = "SYNTHETIC absent topic"
    elif scenario == "permission":
        kwargs = {**kwargs, "permissions": ()}
    elif scenario == "fail":
        manifest["schema_version"] = "A10AutomationJob.v2"
    else:
        manifest.update(provider="codex-cli", ai_assessment=True)
        kwargs = {**kwargs, "mode": "execute"}
        if scenario == "unknown":
            def unknown(*args, **kw):
                raise TimeoutError("SYNTHETIC unknown provider completion")
            kwargs.update(capability=automation_capability(), runner=unknown)
    owner = local_production_owner(manifest, kwargs)
    result = run_automation(manifest, **kwargs, production=owner)
    assert result["brief"] is None and result["brief_ref"] is None and result["brief_bytes_sha256"] is None
    assert owner.last_result is None
    assert not owner._state_dir.exists()
    assert result["status"] in ("COMPLETE", "WAIT", "FAIL")
    if scenario == "unknown":
        assert result["reason"] == "UNKNOWN_EFFECT"


def test_a10_success_is_not_rewritten_by_a03_ambiguous_outcome(automation_job, monkeypatch):
    from ai_commerce.opportunity import run_automation
    manifest, kwargs = automation_job
    owner = local_production_owner(manifest, kwargs)
    def reject(*args):
        raise ValueError("SYNTHETIC downstream rejection")
    monkeypatch.setattr(owner, "_invoke_a03", reject)
    result = run_automation(manifest, **kwargs, production=owner)
    assert result["status"] == "COMPLETE" and owner.last_result.status == "UNKNOWN"
    assert owner.last_result.reason == "RECEIVER_UNRESOLVED"
    assert owner.last_result.event is None and owner.last_result.accepted_consumer is None
    assert json.loads(Path(owner.last_result.completed.completion_ref).read_bytes()) == result
