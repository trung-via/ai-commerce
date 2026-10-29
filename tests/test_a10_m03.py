"""M03 synthetic offline fixtures: provenance, honest uncertainty and immutable notes."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.creative.briefs import intake_brief
from ai_commerce.opportunity.audience import (
    AudienceError, AudienceLedger, prepare_bytes, schema, signal_evidence_ref,
    validate_insight,
)
from ai_commerce.opportunity.audience.__main__ import main
from ai_commerce.opportunity.audience.models import MAX_NOTES_BYTES
from ai_commerce.opportunity.core.schemas import SOURCE_STATUS
from ai_commerce.opportunity.signals import SignalLedger


@pytest.fixture
def notes():
    return {
        "schema_version": "AudienceNotes.v1",
        "data_kind": "SYNTHETIC_FIXTURE",
        "source_note": "  Synthetic qualitative workshop notes.\r\nNo real participants.  ",
        "statements": [
            {"kind": "JOB_TO_BE_DONE", "status": "OBSERVED_USER_SUPPLIED", "text": "Tìm một khoảng nghỉ yên tĩnh."},
            {"kind": "FRICTION", "status": "OBSERVED_USER_SUPPLIED", "text": "The supplied note mentions distracting noise."},
            {"kind": "CONTENT_CONTEXT", "status": "INFERRED", "text": "A silent visual story may suit this context."},
            {"kind": "ALTERNATIVE", "status": "UNKNOWN", "text": "A text-only idea is an alternative."},
            {"kind": "COUNTEREXAMPLE", "status": "OBSERVED_USER_SUPPLIED", "text": "Another note prefers lively sound."},
        ],
    }


def encoded(value, *, bom=False):
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return (b"\xef\xbb\xbf" if bom else b"") + raw


@pytest.fixture
def source(tmp_path):
    rows = [{"topic": "SYNTHETIC quiet break", "observed_at": "2026-10-28T00:00:00Z",
             "status": "AVAILABLE", "value": 0},
            {"topic": "SYNTHETIC missing note", "observed_at": "2026-10-28T00:00:00Z",
             "status": "NOT_AVAILABLE", "value": None}]
    raw = encoded({"schema_version": "ManualSignalRows.v1", "rows": rows}, bom=True)
    snapshot = {
        "schema_version": "SignalSourceSnapshot.v1", "source_id": "synthetic-m03",
        "source_kind": "SYNTHETIC_FIXTURE", "source_ref": "synthetic://m03/signals.json",
        "captured_at": "2026-10-29T00:00:00Z", "sha256": sha256(raw).hexdigest(),
        "rights_status": "UNKNOWN", "verification_status": SOURCE_STATUS,
        "locale": "vi-VN", "geo": "VN", "notes": "SYNTHETIC fixture; no provider facts.",
    }
    ledger = SignalLedger(tmp_path / "signals.sqlite")
    imported = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="json")
    return ledger, imported, raw, snapshot


def reference(source, index=0):
    ledger, imported, _, _ = source
    return signal_evidence_ref(ledger, imported["import"]["import_id"], imported["signals"][index]["signal_id"])


def test_exact_bytes_text_and_deterministic_identity(tmp_path, notes):
    raw = encoded(notes, bom=True)
    first = prepare_bytes(raw).record
    assert first == prepare_bytes(raw).record
    assert first["source_sha256"] == sha256(raw).hexdigest()
    assert first["source_note"] == notes["source_note"]
    assert first["data_kind"] == "SYNTHETIC_FIXTURE"
    assert first["audience_segment"] is None and first["noted_at"] is None
    assert first["rights_status"] == "UNKNOWN" and first["source_status"] == SOURCE_STATUS
    for actual, original in zip(first["statements"], notes["statements"]):
        assert actual["text"] == original["text"] and actual["status"] == original["status"]
        assert actual["uncertainty"] == "UNKNOWN"
        assert actual["evidence_status"] == "NOTE_ONLY"
        assert actual["note_ref"] == "sha256:" + sha256(raw).hexdigest()
    store = AudienceLedger(tmp_path / "audience.sqlite")
    assert store.append_bytes(raw)["insight"] == first
    assert store.raw_bytes(first["insight_id"]) == raw
    assert store.get(first["insight_id"]) == first
    assert prepare_bytes(raw + b"\n").record["insight_id"] != first["insight_id"]


def test_schema_mirror_and_no_external_references(notes):
    published = Path(__file__).resolve().parents[1] / "contracts/AudienceInsight.v1.schema.json"
    assert json.loads(published.read_bytes()) == schema()
    Draft202012Validator.check_schema(schema())
    assert Draft202012Validator(schema()).is_valid(prepare_bytes(encoded(notes)).record)
    assert '"$ref"' not in json.dumps(schema())


def test_m01_exact_refs_and_zero_missing_separation(tmp_path, notes, source):
    signal_ledger, imported, _, _ = source
    before = signal_ledger.path.read_bytes()
    notes["statements"][0]["evidence_refs"] = [reference(source, 0), reference(source, 1)]
    notes["statements"][2]["evidence_refs"] = [reference(source, 0)]
    result = AudienceLedger(tmp_path / "audience.sqlite").append_bytes(encoded(notes), signal_ledger=signal_ledger)["insight"]
    assert signal_ledger.path.read_bytes() == before
    assert len(result["signal_evidence"]) == 2
    by_status = {p["signal_status"]: p for p in result["signal_evidence"]}
    assert by_status["AVAILABLE"]["value"] == 0
    assert by_status["NOT_AVAILABLE"]["value"] is None
    proof = by_status["AVAILABLE"]
    assert proof["context_sha256"] == imported["import"]["context_sha256"]
    assert proof["scope_sha256"] == imported["import"]["scope_sha256"]
    assert proof["lineage"] == imported["import"]["lineage"]
    assert proof["source_rights_status"] == proof["signal_rights_status"] == "UNKNOWN"
    assert result["statements"][2]["status"] == "INFERRED"
    assert result["statements"][2]["evidence_status"] == "LOCAL_INTEGRITY_CHECKED"
    assert "VERIFIED" not in {s["status"] for s in result["statements"]}


def test_m01_original_source_ref_convention(notes, source):
    ref = reference(source)
    ref["evidence_ref"] = source[1]["signals"][0]["evidence_refs"][0]
    notes["statements"][0]["evidence_refs"] = [ref]
    record = prepare_bytes(encoded(notes), signal_ledger=source[0]).record
    assert record["signal_evidence"][0]["reference"] == ref


@pytest.mark.parametrize(("field", "value", "code"), [
    ("import_id", "a10-" + "f" * 64, "M01_EVIDENCE_UNAVAILABLE_OR_INVALID"),
    ("signal_id", "sig-" + "f" * 64, "M01_SIGNAL_NOT_FOUND"),
    ("raw_sha256", "f" * 64, "M01_RAW_HASH_MISMATCH"),
    ("evidence_ref", "synthetic://unrelated", "M01_REF_NOT_BOUND"),
])
def test_forged_refs_do_not_persist(tmp_path, notes, source, field, value, code):
    ref = reference(source)
    ref[field] = value
    notes["statements"][0]["evidence_refs"] = [ref]
    path = tmp_path / "rejected.sqlite"
    with pytest.raises(AudienceError, match=code):
        AudienceLedger(path).append_bytes(encoded(notes), signal_ledger=source[0])
    assert not path.exists()


def test_missing_proof_is_never_accepted(notes, source):
    notes["statements"][0]["evidence_refs"] = [reference(source)]
    with pytest.raises(AudienceError, match="M01_EVIDENCE_REQUIRED"):
        prepare_bytes(encoded(notes))


def test_source_hash_declaration_mismatch_is_rejected(tmp_path, notes, source):
    _, _, raw, snapshot = source
    snapshot = {**snapshot, "sha256": "f" * 64}
    ledger = SignalLedger(tmp_path / "wrong-declaration.sqlite")
    imported = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="json")
    with pytest.raises(AudienceError, match="M01_SOURCE_HASH_MISMATCH"):
        signal_evidence_ref(ledger, imported["import"]["import_id"], imported["signals"][0]["signal_id"])


def test_m01_correction_lineage_preserved(notes, source):
    ledger, imported, raw, snapshot = source
    corrected = json.loads(raw.decode("utf-8-sig"))
    corrected["rows"][0]["value"] = 1
    new_raw = encoded(corrected)
    snapshot = {**snapshot, "sha256": sha256(new_raw).hexdigest()}
    revised = ledger.import_bytes(new_raw, source_snapshot=snapshot, file_format="json",
                                  correction_of=imported["import"]["import_id"])
    notes["statements"][0]["evidence_refs"] = [signal_evidence_ref(ledger, revised["import"]["import_id"], revised["signals"][0]["signal_id"])]
    proof = prepare_bytes(encoded(notes), signal_ledger=ledger).record["signal_evidence"][0]
    assert proof["lineage"] == {"relationship": "CORRECTION", "parent_import_id": imported["import"]["import_id"]}
    assert ledger.get(imported["import"]["import_id"])["signals"][0]["value"] == 0


@pytest.mark.parametrize("text", [
    "Write to person@example.test", "Call +84 912 345 678", "Call ０９１２３４５６７８",
    "Reply to @someone", "See https://example.test/person", "mailto:person", "Call 555-123-4567",
    "Contact p\u200berson@example.test",
])
@pytest.mark.parametrize("field", ["source_note", "audience_segment", "statement", "uncertainty"])
def test_contact_data_rejected_before_persistence(tmp_path, notes, field, text):
    if field in ("statement", "uncertainty"):
        notes["statements"][0]["text" if field == "statement" else "uncertainty"] = text
    else:
        notes[field] = text
    path = tmp_path / "private.sqlite"
    with pytest.raises(AudienceError) as error:
        AudienceLedger(path).append_bytes(encoded(notes))
    assert str(error.value) == "QUALITATIVE_CONTACT_OR_IDENTIFIER_NOT_ALLOWED"
    assert text not in str(error.value)
    assert not path.exists()


@pytest.mark.parametrize(("field", "value"), [
    ("email", "person@example.test"), ("age", 25), ("gender", "invented"),
    ("person_id", "person-1"), ("provider_verified", True),
])
def test_no_identifier_or_demographic_fields(notes, field, value):
    notes[field] = value
    with pytest.raises(AudienceError, match="INVALID_AUDIENCE_NOTES"):
        prepare_bytes(encoded(notes))


@pytest.mark.parametrize("status", ["VERIFIED", "PROVIDER_VERIFIED", "GROWING", None])
def test_statement_status_cannot_be_promoted(notes, status):
    notes["statements"][0]["status"] = status
    with pytest.raises(AudienceError, match="INVALID_AUDIENCE_NOTES"):
        prepare_bytes(encoded(notes))


@pytest.mark.parametrize("raw", [b"{}", b"[]", b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff", b"", b"x" * (MAX_NOTES_BYTES + 1)],
                         ids=["empty-object", "non-object", "duplicate-key", "nonfinite", "invalid-utf8", "empty-bytes", "oversize"])
def test_malformed_notes_are_bounded(raw):
    with pytest.raises(AudienceError):
        prepare_bytes(raw)


def test_optional_segment_uncertainty_and_empty_statements(notes):
    notes["audience_segment"] = "People seeking a quiet break"
    notes["noted_at"] = "2026-10-29T10:00:00+07:00"
    notes["statements"][2]["uncertainty"] = "Only a hypothesis from a synthetic workshop."
    result = prepare_bytes(encoded(notes)).record
    assert result["audience_segment"] == notes["audience_segment"]
    assert result["noted_at"] == notes["noted_at"]
    assert result["statements"][2]["uncertainty"] == notes["statements"][2]["uncertainty"]
    notes["statements"] = []
    empty = prepare_bytes(encoded(notes)).record
    assert empty["statements"] == empty["signal_evidence"] == []
    assert intake_brief("A freeform imaginary story")["objective"] == "A freeform imaginary story"


def test_append_only_idempotency_revision_and_branches(tmp_path, notes):
    store = AudienceLedger(tmp_path / "audience.sqlite")
    original_raw = encoded(notes, bom=True)
    original = store.append_bytes(original_raw)["insight"]
    assert store.append_bytes(original_raw) == {"insight": original, "duplicate": True}
    notes["source_note"] += " Revised by caller."
    revised_raw = encoded(notes)
    revised = store.append_bytes(revised_raw, revision_of=original["insight_id"])["insight"]
    assert revised["lineage"] == {"relationship": "REVISION", "parent_insight_id": original["insight_id"], "number": 2}
    assert store.append_bytes(revised_raw, revision_of=original["insight_id"])["duplicate"] is True
    assert store.history(revised["insight_id"]) == [original, revised]
    notes["source_note"] += " Alternative revision."
    branch = store.append_bytes(encoded(notes), revision_of=original["insight_id"])["insight"]
    assert store.history(branch["insight_id"]) == [original, branch]
    assert store.raw_bytes(original["insight_id"]) == original_raw
    with pytest.raises(AudienceError, match="REVISION_SOURCE_UNCHANGED"):
        store.append_bytes(original_raw, revision_of=original["insight_id"])
    with pytest.raises(AudienceError, match="AUDIENCE_INSIGHT_NOT_FOUND"):
        store.append_bytes(revised_raw, revision_of="aud-" + "f" * 64)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM audience_insights").fetchone()[0] == 3
        for table in ("audience_raw", "audience_insights"):
            for operation in ("DELETE FROM " + table, "UPDATE " + table + " SET payload=x'00'"):
                with pytest.raises(sqlite3.IntegrityError, match="A10_AUDIENCE_APPEND_ONLY"):
                    db.execute(operation)


@pytest.mark.parametrize("target", ["audience_raw", "audience_insights"])
def test_tamper_is_detected(tmp_path, notes, target):
    store = AudienceLedger(tmp_path / "audience.sqlite")
    record = store.append_bytes(encoded(notes))["insight"]
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER " + target + "_no_update")
        payload = db.execute("SELECT payload FROM " + target).fetchone()[0]
        db.execute("UPDATE " + target + " SET payload=?", (b"x" * len(payload),))
    with pytest.raises(AudienceError, match="AUDIENCE_RAW_TAMPERED|AUDIENCE_RECORD_TAMPERED"):
        store.get(record["insight_id"])


def test_stored_proof_needs_live_local_recheck(tmp_path, notes, source):
    notes["statements"][0]["evidence_refs"] = [reference(source)]
    store = AudienceLedger(tmp_path / "audience.sqlite")
    record = store.append_bytes(encoded(notes), signal_ledger=source[0])["insight"]
    with pytest.raises(AudienceError, match="M01_EVIDENCE_REQUIRED"):
        store.get(record["insight_id"])
    assert store.get(record["insight_id"], signal_ledger=source[0]) == record
    with sqlite3.connect(source[0].path) as db:
        db.execute("DROP TRIGGER raw_evidence_no_update")
        raw = db.execute("SELECT payload FROM raw_evidence").fetchone()[0]
        db.execute("UPDATE raw_evidence SET payload=?", (b"x" * len(raw),))
    with pytest.raises(AudienceError, match="M01_EVIDENCE_UNAVAILABLE_OR_INVALID"):
        store.get(record["insight_id"], signal_ledger=source[0])


def test_record_identity_and_index_tamper(tmp_path, notes):
    record = prepare_bytes(encoded(notes)).record
    tampered = deepcopy(record)
    tampered["source_note"] += " changed"
    with pytest.raises(AudienceError, match="INSIGHT_IDENTITY_MISMATCH"):
        validate_insight(tampered)
    store = AudienceLedger(tmp_path / "audience.sqlite")
    store.append_bytes(encoded(notes))
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER audience_insights_no_update")
        db.execute("UPDATE audience_insights SET revision=2")
    with pytest.raises(AudienceError, match="AUDIENCE_INDEX_MISMATCH"):
        store.get(record["insight_id"])


def test_cannot_write_to_m01_database(notes, source):
    before = source[0].path.read_bytes()
    with pytest.raises(AudienceError, match="AUDIENCE_LEDGER_FORMAT_MISMATCH"):
        AudienceLedger(source[0].path).append_bytes(encoded(notes))
    assert source[0].path.read_bytes() == before


def test_offline_api_and_cli(monkeypatch, tmp_path, notes, source, capsys):
    def no_socket(*args, **kwargs):
        raise AssertionError("Network access attempted")
    monkeypatch.setattr(socket, "socket", no_socket)
    notes["statements"][0]["evidence_refs"] = [reference(source)]
    note_file = tmp_path / "notes.json"
    note_file.write_bytes(encoded(notes, bom=True))
    audience_path = tmp_path / "audience.sqlite"
    common = ["--signals", str(source[0].path)]
    assert main(["validate", "--file", str(note_file), *common]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert main(["append", "--file", str(note_file), "--ledger", str(audience_path), *common]) == 0
    assert json.loads(capsys.readouterr().out)["insight"] == preview
    assert main(["inspect", "--ledger", str(audience_path), "--insight-id", preview["insight_id"], *common]) == 0
    assert json.loads(capsys.readouterr().out) == preview
    assert main(["history", "--ledger", str(audience_path), "--insight-id", preview["insight_id"], *common]) == 0
    assert json.loads(capsys.readouterr().out) == [preview]
    notes["source_note"] = "person@example.test"
    note_file.write_bytes(encoded(notes))
    assert main(["validate", "--file", str(note_file)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "QUALITATIVE_CONTACT_OR_IDENTIFIER_NOT_ALLOWED"}


def test_module_entrypoint(tmp_path, notes):
    path = tmp_path / "notes.json"
    path.write_bytes(encoded(notes))
    command = [sys.executable, "-m", "ai_commerce.opportunity.audience", "validate", "--file", str(path)]
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run(command, env=env, capture_output=True, check=False, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["source_note"] == notes["source_note"]
