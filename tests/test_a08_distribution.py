"""A08 D02-D04: offline E01 packages, capability truth and evidence-bound receipts."""
import hashlib
import json
import sqlite3
import struct
import zlib

import pytest

from ai_commerce.cli import main
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.distribution.assisted import DistributionError, export_package, inspect_package
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.e2e import run as e01_run


def _src(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    blob = b"mock MP4 bytes; no actual media execution in this unit fixture"
    (source / "video.mp4").write_bytes(blob)
    (source / "e01-manifest.json").write_bytes(canonical_bytes({
        "run_id": "e01-fixture", "publication_status": "NOT_ATTEMPTED",
        "output_sha256": hashlib.sha256(blob).hexdigest(),
    }))
    monkeypatch.setattr("ai_commerce.distribution.assisted.verify_e01",
                        lambda _: {"state": "DELIVERED"})
    return source


def _pkg(tmp_path, monkeypatch, *, caption="Xin chào"):
    source = _src(tmp_path, monkeypatch)
    out = tmp_path / "package"
    result = export_package(source, out, platform="TIKTOK", surface="VIDEO",
                            account="internal_alias:demo", caption=caption,
                            hashtags=["AiCommerce", "Demo"])
    return out, result


def _write(tmp_path, name, blob):
    path = tmp_path / name
    path.write_bytes(blob)
    return path


def test_export_source_digest_and_no_false_post(tmp_path, monkeypatch):
    out, result = _pkg(tmp_path, monkeypatch)
    assert result["publication_status"] == "NOT_ATTEMPTED"
    assert result["capability"]["decision"] == "UNKNOWN"
    assert result["capability"]["verification"] == "OFFLINE_RECORDED_NOT_LIVE_VERIFIED"
    assert inspect_package(out) == result
    assert (out / "video.mp4").read_bytes().startswith(b"mock MP4")
    assert "NOT SUBMITTED" in (out / "publish-instructions.md").read_text(encoding="utf-8")
    with pytest.raises(DistributionError, match="OUTPUT_EXISTS"):
        export_package(tmp_path / "source", out, platform="TIKTOK", surface="VIDEO",
                       account="internal_alias:demo")


def test_modified_package_and_wrong_target_denied(tmp_path, monkeypatch):
    out, _ = _pkg(tmp_path, monkeypatch)
    (out / "caption.txt").write_text("Changed", encoding="utf-8")
    with pytest.raises(DistributionError, match="PACKAGE_FILE_DIGEST_MISMATCH"):
        inspect_package(out)
    source = tmp_path / "source"
    with pytest.raises((DistributionError, ValueError), match="TARGET_NOT_PROFILED"):
        export_package(source, tmp_path / "other", platform="TIKTOK", surface="SHORT",
                       account="internal_alias:demo")
    with pytest.raises(ValueError):
        export_package(source, tmp_path / "bad-account", platform="TIKTOK", surface="VIDEO",
                       account="facebook_page_id:123")


def test_source_digest_and_missing_source_refused(tmp_path, monkeypatch):
    source = _src(tmp_path, monkeypatch)
    with pytest.raises(DistributionError, match="SOURCE_VIDEO_SHA_MISMATCH"):
        (source / "video.mp4").write_bytes(b"tamper")
        export_package(source, tmp_path / "out", platform="YOUTUBE", surface="SHORT",
                       account="internal_alias:test")
    with pytest.raises(DistributionError, match="OUTPUT_INSIDE_SOURCE"):
        export_package(source, source / "export", platform="YOUTUBE", surface="SHORT",
                       account="internal_alias:test")


def test_create_handoff_idempotency_and_collision(tmp_path, monkeypatch):
    out, _ = _pkg(tmp_path, monkeypatch)
    db = tmp_path / "journal.sqlite"
    ledger = PublicationJournal(db)
    a = ledger.create(out, idempotency_key="once")
    assert a["state"] == "MANUAL_ACTION_REQUIRED"
    assert a["visibility"] == "UNKNOWN"
    assert a["events"] == 0
    assert ledger.create(out, idempotency_key="once") == a
    # Same key cannot be used for another content digest.
    other = tmp_path / "other"
    from ai_commerce.distribution.assisted import export_package
    export_package(tmp_path / "source", other, platform="YOUTUBE", surface="SHORT",
                   account="internal_alias:other")
    with pytest.raises(DistributionError, match="PUBLICATION_IDEMPOTENCY_COLLISION"):
        ledger.create(other, idempotency_key="once")


def test_submission_requires_separate_publish_and_visibility_evidence(tmp_path, monkeypatch):
    out, _ = _pkg(tmp_path, monkeypatch)
    ledger = PublicationJournal(tmp_path / "journal.sqlite")
    item = ledger.create(out, idempotency_key="post-01")
    ident = item["publication_id"]
    with pytest.raises(DistributionError, match="PUBLICATION_NOT_CONFIRMED"):
        ledger.record(ident, "VISIBILITY_EVIDENCE",
                      evidence_file=_write(tmp_path, "visibility1", b"public proof"),
                      public_url="https://example.org/post/1")
    a = ledger.record(ident, "SUBMISSION_REPORTED")
    assert a["state"] == "SUBMITTED" and a["visibility"] == "UNKNOWN"
    with pytest.raises(DistributionError, match="PUBLICATION_RECEIPT_REQUIRED"):
        ledger.record(ident, "PUBLICATION_RECEIPT", provider_post_id="123")
    receipt = _write(tmp_path, "receipt", b"human provided provider receipt")
    a = ledger.record(ident, "PUBLICATION_RECEIPT", provider_post_id="123",
                      evidence_file=receipt)
    assert a["state"] == "PUBLISHED_CONFIRMED" and a["visibility"] == "UNKNOWN"
    assert a["verification"] == "USER_SUPPLIED_NOT_LIVE_VERIFIED"
    with pytest.raises(DistributionError, match="VISIBILITY_EVIDENCE_MUST_BE_INDEPENDENT"):
        ledger.record(ident, "VISIBILITY_EVIDENCE", evidence_file=receipt,
                      public_url="https://example.org/post/1")
    with pytest.raises(DistributionError, match="PUBLIC_URL_INVALID"):
        ledger.record(ident, "VISIBILITY_EVIDENCE",
                      evidence_file=_write(tmp_path, "other", b"other screenshot"),
                      public_url="http://example.org/post/1")
    a = ledger.record(ident, "VISIBILITY_EVIDENCE",
                      evidence_file=_write(tmp_path, "visibility", b"separate visibility screenshot"),
                      public_url="https://example.org/post/1")
    assert a["state"] == "PUBLISHED_CONFIRMED" and a["visibility"] == "PUBLIC"
    assert a["events"] == 3 and len(a["receipt_refs"]) == 2


def test_unknown_does_not_retry_or_fabricate_publish(tmp_path, monkeypatch):
    out, _ = _pkg(tmp_path, monkeypatch)
    ledger = PublicationJournal(tmp_path / "journal.sqlite")
    ident = ledger.create(out, idempotency_key="try")["publication_id"]
    assert ledger.record(ident, "OUTCOME_UNKNOWN")["state"] == "UNKNOWN"
    assert ledger.get(ident)["provider_post_id"] is None
    with pytest.raises(DistributionError, match="PUBLICATION_RECEIPT_REQUIRED"):
        ledger.record(ident, "PUBLICATION_RECEIPT")
    assert ledger.get(ident)["state"] == "UNKNOWN"


def test_tampered_sqlite_evidence_is_detected(tmp_path, monkeypatch):
    out, _ = _pkg(tmp_path, monkeypatch)
    path = tmp_path / "ledger.sqlite"
    ledger = PublicationJournal(path)
    ident = ledger.create(out, idempotency_key="proof")["publication_id"]
    ledger.record(ident, "SUBMISSION_REPORTED")
    ledger.record(ident, "PUBLICATION_RECEIPT", provider_post_id="ABC",
                  evidence_file=_write(tmp_path, "receipt", b"binary proof"))
    with sqlite3.connect(path) as db:
        db.execute("UPDATE publication_events SET evidence_blob=? WHERE evidence_sha256 IS NOT NULL", (b"modified",))
    with pytest.raises(DistributionError, match="CORRUPT_PUBLICATION_EVIDENCE"):
        ledger.get(ident)


def test_cli_export_handoff_status_and_submission(tmp_path, monkeypatch, capsys):
    source = _src(tmp_path, monkeypatch)
    package = tmp_path / "tiktok-export"
    assert main(["distribution", "export", "--source", str(source), "--output", str(package),
                 "--platform", "TIKTOK", "--surface", "VIDEO",
                 "--account", "internal_alias:creator", "--caption", "Hello"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["publication_status"] == "NOT_ATTEMPTED"
    db = tmp_path / "journal.sqlite"
    assert main(["distribution", "handoff", "--package", str(package), "--db", str(db),
                 "--idempotency-key", "c1"]) == 0
    item = json.loads(capsys.readouterr().out)
    assert main(["distribution", "event", "--db", str(db),
                 "--publication-id", item["publication_id"], "--type", "SUBMISSION_REPORTED"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "SUBMITTED"
    assert main(["distribution", "status", "--db", str(db),
                 "--publication-id", item["publication_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["visibility"] == "UNKNOWN"


def _png() -> bytes:
    width = height = 16
    row = b"\x00" + bytes((65, 30, 170)) * width
    pixels = zlib.compress(row * height)
    def chunk(name, value):
        return struct.pack(">I", len(value)) + name + value + struct.pack(">I", zlib.crc32(name + value) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def test_real_e01_round_trip_provenance_to_manual_export(tmp_path):
    image = _write(tmp_path, "scene.png", _png())
    source = tmp_path / "real-e01"
    delivery = e01_run("An original animated character", [image], source,
                       width=64, height=96, fps=24, frames=4)
    assert delivery["state"] == "DELIVERED"
    result = export_package(source, tmp_path / "real-export",
                            platform="YOUTUBE", surface="SHORT", account="internal_alias:mychannel")
    assert result["video_sha256"] == json.loads(
        (source / "e01-manifest.json").read_text(encoding="utf-8"))["output_sha256"]
    assert inspect_package(tmp_path / "real-export") == result
