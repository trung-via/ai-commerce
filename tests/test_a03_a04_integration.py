"""Offline A03/A04 fan-in: real persisted contracts and failure semantics."""

from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest

from ai_commerce.cli import main
from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import ConceptRecord, CreativeStore
from ai_commerce.media import MediaProviderError, MediaProviderTimeout, MockMediaProvider
from ai_commerce.operations import ArtifactRegistry, AssetJobManifest, LocalMediaRunner

ROOT = Path(__file__).resolve().parents[1]
REQUEST = "Một bài thơ thị giác về cơn mưa Sài Gòn, kể bằng ánh sáng và nhịp nước. 🌧️"


def json_call(capsys, argv, *, expected=0):
    assert main(list(map(str, argv))) == expected
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


@pytest.fixture
def selected(tmp_path, capsys):
    store = tmp_path / "creative"
    result = json_call(capsys, [
        "concept-create", "--text", REQUEST, "--store", store, "--mock",
    ])
    assert len(result["concepts"]) == 1
    return store, result["concepts"][0]


def png():
    """Independent one-pixel PNG fixture; no media/model service is called."""
    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x30\x60\x90"))
            + chunk(b"IEND", b""))


def asset_args(store, selection, out, *extra):
    return ["asset-run", "--store", store, "--selection-id",
            selection["selection"]["selection_id"], "--out-dir", out, *extra]


def inspect_args(store, out, handoff):
    return ["asset-inspect", "--store", store, "--out-dir", out, "--handoff", handoff]


@pytest.mark.parametrize("mode", ["import", "mock"])
def test_brief_concept_request_artifact_and_reopened_a05_handoff(selected, tmp_path, capsys, mode):
    store, chosen = selected
    brief = validate_record("ContentBrief", chosen["brief"])
    assert brief["objective"] == brief["prompt"] == REQUEST
    assert brief["product_ref"] is None and "cta" not in brief
    concept = ConceptRecord.from_dict(chosen["concept"])
    assert concept.observation.mode == "TEST_ONLY_MOCK"
    assert concept.observation.model == "UNKNOWN"
    assert concept.factual_status == "UNVERIFIED" and concept.rights_status == "UNKNOWN"

    out, source = tmp_path / "assets", tmp_path / "rain.png"
    options = ["--mode", mode]
    if mode == "import":
        source.write_bytes(png())
        options += ["--source", source, "--mime-type", "image/png"]
    output = json_call(capsys, asset_args(store, chosen, out, *options))
    manifest = AssetJobManifest.from_dict(output["asset_manifest"])
    result = validate_record("JobResult", manifest.result)
    artifact = validate_record("MediaArtifact", result["artifact"])
    registry = ArtifactRegistry(out)
    content = registry.verify(artifact)
    assert manifest.state == result["status"] == "SUCCEEDED"
    assert artifact["sha256"] == sha256_bytes(content)
    assert artifact["mime_type"] == "image/png" and artifact["kind"] == "image"
    assert artifact["source_snapshot_ids"] == [result["source_snapshot"]["snapshot_id"]]
    assert artifact["rights_status"] == result["source_snapshot"]["rights_status"] == "UNKNOWN"
    assert artifact["generation_performed"] is result["generation_performed"] is False
    assert result["publication_status"] == "NOT_ATTEMPTED"
    assert result["cost_status"] == "NOT_APPLICABLE" and result["cost_amount"] is None
    if mode == "import":
        assert content == source.read_bytes()
        assert manifest.request["input"]["source_ref"] == str(source.resolve())
        assert artifact["source_ref"] == str(source.resolve())
        assert artifact["source_generation"] == "UNKNOWN"
        assert result["source_snapshot"]["source_kind"] == "LOCAL_MEDIA_FILE"
        source.unlink()
    else:
        assert artifact["origin"] == "MOCK"
        assert artifact["source_generation"] == "SYNTHETIC_FIXTURE"
        assert result["source_snapshot"]["source_kind"] == "MEDIA_REQUEST"
        assert b"MOCK fixture; no model generation" in content

    # A05-style consumer reconstructs both parents solely from persisted roots.
    handoff = json.loads(registry.read(output["handoff_ref"]))
    creative = CreativeStore(store).load_selected(handoff["selection_id"])
    reopened = LocalMediaRunner(ArtifactRegistry(out)).load(handoff["job_id"], handoff["attempt"])
    assert creative == chosen == output["creative"]
    assert creative["selection"]["concept_digest"] == concept.digest
    assert handoff["manifest_record_sha256"] == reopened.to_dict()["record_sha256"]
    assert reopened.to_dict() == manifest.to_dict()
    assert json_call(capsys, inspect_args(store, out, output["handoff_ref"])) == output


def test_json_brief_and_multiple_concepts_keep_custom_fields_without_commerce(tmp_path, capsys):
    brief = {"brief_id": "rain", "objective": REQUEST, "product_ref": None,
             "source_refs": ["note:unverified-observation"], "palette": ["xanh", "tím"]}
    source = tmp_path / "brief.json"
    source.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8-sig")
    output = json_call(capsys, [
        "concept-create", "--brief", source, "--store", tmp_path / "creative", "--mock", "--count", "2",
    ])
    assert len(output["concepts"]) == 2
    assert len({item["concept"]["family"] for item in output["concepts"]}) == 2
    for item in output["concepts"]:
        assert item["brief"] == brief
        assert item["concept"]["source_refs"] == brief["source_refs"]
        assert item["concept"]["factual_status"] == "UNVERIFIED"


def test_reframe_keeps_original_selection_and_binds_shared_asset_to_exact_revision(selected, tmp_path, capsys):
    store, original = selected
    out = tmp_path / "assets"
    first_asset = json_call(capsys, asset_args(store, original, out, "--mode", "mock"))
    changes = tmp_path / "changes.json"
    changes.write_text(json.dumps({"premise": "Hư cấu: chiếc ô mong trời đừng tạnh."}), encoding="utf-8-sig")
    revised = json_call(capsys, [
        "concept-reframe", "--store", store, "--selection-id", original["selection"]["selection_id"],
        "--lens", "Điểm nhìn của chiếc ô", "--changes", changes,
    ])
    assert revised["concept"]["revision"] == 2
    assert revised["concept"]["concept_id"] == original["concept"]["concept_id"]
    assert revised["concept"]["parent"]["digest"] == original["selection"]["concept_digest"]
    assert revised["concept"]["operation"] == "REFRAME"
    assert revised["concept"]["observation"] == original["concept"]["observation"]
    assert json_call(capsys, [
        "concept-inspect", "--store", store, "--selection-id", original["selection"]["selection_id"],
    ]) == original
    second_asset = json_call(capsys, asset_args(store, revised, out, "--mode", "mock"))
    assert first_asset["asset_manifest"] == second_asset["asset_manifest"]
    assert first_asset["handoff_ref"] != second_asset["handoff_ref"]
    assert json_call(capsys, inspect_args(store, out, first_asset["handoff_ref"]))["creative"] == original
    assert json_call(capsys, inspect_args(store, out, second_asset["handoff_ref"]))["creative"] == revised


def test_new_module_commands_work_outside_checkout_with_unicode_brief(tmp_path):
    source = tmp_path / "brief.txt"
    source.write_text(REQUEST, encoding="utf-8")
    store, out = tmp_path / "creative", tmp_path / "assets"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")

    def invoke(*argv):
        result = subprocess.run([sys.executable, "-m", "ai_commerce", *map(str, argv)],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    selected = invoke("concept-create", "--brief", source, "--store", store, "--mock")["concepts"][0]
    assert selected["brief"]["objective"] == REQUEST
    output = invoke(*asset_args(store, selected, out, "--mode", "mock"))
    assert invoke(*inspect_args(store, out, output["handoff_ref"])) == output


@pytest.mark.parametrize("text", [
    "", "   ", '{"brief_id":', '["not a brief"]',
    '{"brief_id":"b","objective":2}',
    '{"brief_id":"b","objective":"one","objective":"two"}',
])
def test_malformed_brief_fails_without_creating_concepts(tmp_path, capsys, text):
    store = tmp_path / "creative"
    source = tmp_path / "bad-brief.txt"
    source.write_text(text, encoding="utf-8")
    assert main(["concept-create", "--brief", str(source), "--store", str(store), "--mock"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "ERROR" in captured.err
    assert not store.exists()


@pytest.mark.parametrize("count", ["0", "-1", "5"])
def test_invalid_mock_count_is_an_error_without_partial_saved_state(tmp_path, capsys, count):
    store = tmp_path / "creative"
    assert main(["concept-create", "--text", REQUEST, "--store", str(store), "--mock", "--count", count]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "ERROR" in captured.err
    assert not store.exists()


def test_concept_creation_requires_explicit_mock_selection(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["concept-create", "--text", REQUEST, "--store", str(tmp_path / "creative")])
    assert exc.value.code == 2
    assert "--mock" in capsys.readouterr().err


@pytest.mark.parametrize("options", [
    ["--mode", "import"],
    ["--mode", "import", "--mime-type", "image/png"],
    ["--mode", "import", "--source", "SOURCE"],
    ["--mode", "mock", "--source", "SOURCE"],
])
def test_invalid_asset_request_has_no_success_manifest(selected, tmp_path, capsys, options):
    store, chosen = selected
    source, out = tmp_path / "rain.png", tmp_path / "assets"
    source.write_bytes(png())
    options = [str(source) if value == "SOURCE" else value for value in options]
    assert main(list(map(str, asset_args(store, chosen, out, *options)))) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "MediaValidationError" in captured.err
    assert not list(out.rglob("*.manifest.json"))


def test_corrupted_import_fails_before_job_creation(selected, tmp_path, capsys):
    store, chosen = selected
    source, out = tmp_path / "broken.png", tmp_path / "assets"
    source.write_bytes(b"corrupted PNG")
    assert main(list(map(str, asset_args(store, chosen, out, "--mode", "import",
                                        "--source", source, "--mime-type", "image/png")))) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "MediaValidationError" in captured.err
    assert not list(out.rglob("*.manifest.json"))


@pytest.mark.parametrize("damage,error", [("corrupt", "ArtifactCorruptError"), ("missing", "ArtifactMissingError")])
def test_corrupt_or_missing_asset_never_replays_as_success(selected, tmp_path, capsys, damage, error):
    store, chosen = selected
    out = tmp_path / "assets"
    output = json_call(capsys, asset_args(store, chosen, out, "--mode", "mock"))
    manifest_path = out / output["asset_manifest_ref"]
    preserved_manifest = manifest_path.read_bytes()
    artifact = out / output["asset_manifest"]["result"]["artifact"]["path"]
    if damage == "corrupt":
        artifact.write_bytes(b"tampered")
    else:
        artifact.unlink()
    for argv in (inspect_args(store, out, output["handoff_ref"]),
                 asset_args(store, chosen, out, "--mode", "mock")):
        assert main(list(map(str, argv))) == 2
        captured = capsys.readouterr()
        assert captured.out == "" and error in captured.err
    assert manifest_path.read_bytes() == preserved_manifest
    if damage == "corrupt":
        assert artifact.read_bytes() == b"tampered"
    else:
        assert not artifact.exists()


@pytest.mark.parametrize("failure,state,retry", [
    (MediaProviderTimeout("Outcome cannot be determined"), "UNKNOWN", "RECONCILE_REQUIRED"),
    (MediaProviderError("LOCAL_FAILURE", "Explicit local failure", side_effects="NONE"), "FAILED", "SAFE_LOCAL"),
])
def test_unknown_and_failed_outcomes_remain_non_success_without_retry(
    selected, tmp_path, capsys, monkeypatch, failure, state, retry,
):
    class Broken(MockMediaProvider):
        calls = 0

        def produce(self, request):
            self.calls += 1
            raise failure

    provider = Broken()
    monkeypatch.setattr("ai_commerce.operations.runner.MockMediaProvider", lambda: provider)
    store, chosen = selected
    out = tmp_path / "assets"
    argv = asset_args(store, chosen, out, "--mode", "mock")
    output = json_call(capsys, argv, expected=1)
    manifest = output["asset_manifest"]
    assert manifest["state"] == manifest["result"]["status"] == state
    assert manifest["retry_safety"] == retry
    assert manifest["result"]["artifact"] is None
    assert manifest["cost"] == {"status": "UNKNOWN", "amount": None}
    assert output["creative"]["concept"]["factual_status"] == "UNVERIFIED"
    assert json_call(capsys, argv, expected=1) == output
    assert json_call(capsys, inspect_args(store, out, output["handoff_ref"]), expected=1) == output
    assert provider.calls == 1 and not (out / "objects").exists()


@pytest.mark.parametrize("field,value", [("factual_status", "VERIFIED"), ("rights_status", "GRANTED")])
def test_unknown_creative_correctness_and_rights_cannot_be_promoted(selected, tmp_path, capsys, field, value):
    store, chosen = selected
    record = dict(chosen["concept"])
    record[field] = value
    path = store / "concepts" / record["concept_id"][8:] / "r1.json"
    path.write_bytes(canonical_bytes(record))
    out = tmp_path / "assets"
    assert main(list(map(str, asset_args(store, chosen, out, "--mode", "mock")))) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "CreativeValidationError" in captured.err
    assert not out.exists()


@pytest.mark.parametrize("damage", ["address", "manifest", "version"])
def test_handoff_checks_identity_version_and_actual_manifest_binding(selected, tmp_path, capsys, damage):
    store, chosen = selected
    out = tmp_path / "assets"
    output = json_call(capsys, asset_args(store, chosen, out, "--mode", "mock"))
    record = output["handoff"]
    ref = output["handoff_ref"]
    if damage == "manifest":
        record["manifest_record_sha256"] = "0" * 64
    else:
        record["handoff_version"] = 2
    content = canonical_bytes(record)
    if damage == "address":
        (out / ref).write_bytes(content)
    else:
        ref = "handoffs/" + sha256_bytes(content) + ".json"
        ArtifactRegistry(out).put_immutable(ref, content)
    assert main(list(map(str, inspect_args(store, out, ref)))) == 2
    captured = capsys.readouterr()
    expected = {"address": "HANDOFF_IDENTITY_MISMATCH", "manifest": "HANDOFF_MANIFEST_MISMATCH",
                "version": "INVALID_HANDOFF_FIELDS_OR_VERSION"}[damage]
    assert captured.out == "" and expected in captured.err


def test_unknown_selection_cannot_start_an_asset_job(tmp_path, capsys):
    out = tmp_path / "assets"
    assert main(["asset-run", "--store", str(tmp_path / "creative"),
                 "--selection-id", "selection:" + "a" * 64, "--out-dir", str(out), "--mode", "mock"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "FileNotFoundError" in captured.err
    assert not out.exists()
