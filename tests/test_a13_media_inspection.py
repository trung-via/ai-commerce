"""C08: real local media evidence; simulated faults are explicitly identified."""

from __future__ import annotations

import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import zlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.affiliate_release import (
    DiagnosticOptions, InspectionLimits, MediaInspectionError,
    MediaReleaseInspection, ReleaseAsset, asset_from_e01, inspect_media_release,
    media_inspection_schema,
)
from ai_commerce.affiliate_release import inspection as inspection_module
from ai_commerce.affiliate_release.process import BoundedToolRunner, ToolFailure
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.media.files import hash_file
from ai_commerce.media import files as media_files


@pytest.fixture(scope="module")
def real_media(tmp_path_factory):
    """Tools are real; unavailable dependencies are never substituted with mocks."""
    root = tmp_path_factory.mktemp("c08-real")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("Real integration requires installed ffmpeg and ffprobe")
    for name, audio in (("silent-art.mp4", False), ("silent-track.mp4", True)):
        argv = [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                "color=c=black:s=48x32:r=10:d=0.5"]
        if audio:
            argv += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
        argv += ["-t", "0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-threads", "1"]
        if audio:
            argv += ["-c:a", "aac"]
        argv += [str(root / name)]
        subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=30)
    return root


def pinned(root, name="silent-art.mp4"):
    return ReleaseAsset(name, hash_file(root / name)[0])


def test_real_media_facts_deterministic_schema_and_no_external_claims(real_media):
    asset = pinned(real_media)
    first = inspect_media_release(real_media, [asset])
    second = inspect_media_release(real_media, [asset])
    assert first.to_bytes() == second.to_bytes()
    report = first.to_dict()
    assert report["status"] == "PASS"
    result = report["assets"][0]
    assert result["status"] == "PASS" and result["full_decode"] is True
    assert result["observed_sha256"] == asset.expected_sha256
    assert result["size_bytes"] == (real_media / asset.asset_ref).stat().st_size
    facts = result["facts"]
    assert facts["container"] == "mp4" and facts["duration_seconds"] == "1/2"
    assert facts["streams"][0]["codec"] == "h264"
    assert (facts["streams"][0]["width"], facts["streams"][0]["height"]) == (48, 32)
    assert facts["streams"][0]["decoded_frames"] == 5
    assert result["diagnostics"]["black"]["status"] == "NOT_REQUESTED"
    assert report["scope"] == "COMMERCIAL_PLACEMENT_TECHNICAL_INSPECTION_ONLY"
    assert report["rights_status"] == "UNKNOWN"
    assert report["account_eligibility"] == report["link_activation"] == "UNKNOWN"
    assert report["publication_status"] == "NOT_ATTEMPTED"
    Draft202012Validator(media_inspection_schema()).validate(report)
    Draft202012Validator.check_schema(media_inspection_schema())
    detached_schema = media_inspection_schema()
    detached_schema["properties"]["status"]["enum"].clear()
    assert media_inspection_schema()["properties"]["status"]["enum"] == ["PASS", "FAIL", "UNKNOWN"]
    assert MediaReleaseInspection.from_bytes(first.to_bytes()).to_dict() == report
    detached = first.to_dict()
    detached["status"] = "FAIL"
    assert first.to_dict()["status"] == "PASS"


def test_optional_black_and_no_audio_are_observations_not_creative_gates(real_media):
    result = inspect_media_release(real_media, [pinned(real_media)],
                                  diagnostics=DiagnosticOptions(black=True, silence=True)).to_dict()
    row = result["assets"][0]
    assert result["status"] == "PASS"
    assert row["diagnostics"]["black"]["status"] == "OBSERVED"
    assert row["diagnostics"]["black"]["segments"]
    assert row["diagnostics"]["silence"]["status"] == "NOT_APPLICABLE"


def test_optional_real_silent_audio_passes(real_media):
    report = inspect_media_release(real_media, [pinned(real_media, "silent-track.mp4")],
                                   diagnostics=DiagnosticOptions(silence=True)).to_dict()
    row = report["assets"][0]
    assert report["status"] == "PASS"
    assert row["diagnostics"]["silence"]["status"] == "OBSERVED"
    assert row["diagnostics"]["silence"]["segments"]
    assert any(s["codec"] == "aac" and s["sample_rate"] == 48000
               for s in row["facts"]["streams"])


@pytest.mark.parametrize("missing", ["ffprobe", "ffmpeg"])
def test_missing_tool_is_unknown_not_fabricated_pass(real_media, monkeypatch, missing):
    original = shutil.which
    monkeypatch.setattr(inspection_module.shutil, "which",
                        lambda name: None if name == missing else original(name))
    report = inspect_media_release(real_media, [pinned(real_media)]).to_dict()
    assert report["status"] == "UNKNOWN"
    assert report["assets"][0]["full_decode"] is False
    assert "MEDIA_TOOL_UNAVAILABLE" in report["assets"][0]["reason_codes"]


def test_corrupt_and_changed_media_fail(tmp_path, real_media):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not an MP4")
    bad = inspect_media_release(tmp_path, [pinned(tmp_path, "broken.mp4")]).to_dict()
    assert bad["status"] == "FAIL" and bad["assets"][0]["full_decode"] is False
    shutil.copyfile(real_media / "silent-art.mp4", tmp_path / "movie.mp4")
    asset = pinned(tmp_path, "movie.mp4")
    with (tmp_path / "movie.mp4").open("ab") as stream:
        stream.write(b"changed after the release request")
    changed = inspect_media_release(tmp_path, [asset]).to_dict()
    assert changed["status"] == "FAIL"
    assert changed["assets"][0]["reason_codes"] == ["MEDIA_SHA256_MISMATCH"]


def test_decode_corruption_is_detected_by_real_tools(tmp_path, real_media):
    data = bytearray((real_media / "silent-art.mp4").read_bytes())
    marker = data.index(b"mdat")
    data[marker + 4:marker + 4 + 256] = bytes(256)
    (tmp_path / "broken.mp4").write_bytes(data)
    report = inspect_media_release(tmp_path, [pinned(tmp_path, "broken.mp4")]).to_dict()
    assert report["status"] == "FAIL"
    assert report["assets"][0]["full_decode"] is False


@pytest.mark.parametrize("ref", ["../outside.mp4", "/absolute.mp4", "C:/outside.mp4",
                                    "https://example.test/a.mp4", "a\\b.mp4", "a.mp4:ads",
                                    "./video.mp4", "a//b.mp4", "NUL.mp4", "a./b.mp4"])
def test_invalid_paths_never_launch_tools(tmp_path, monkeypatch, ref):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid path must not reach tool discovery")
    monkeypatch.setattr(inspection_module.shutil, "which", forbidden)
    report = inspect_media_release(tmp_path, [ReleaseAsset(ref, "0" * 64)]).to_dict()
    assert report["status"] == "FAIL"
    assert report["assets"][0]["reason_codes"] == ["MEDIA_PATH_INVALID"]


def test_missing_file_directory_and_unsupported_container(tmp_path, monkeypatch):
    assert inspect_media_release(tmp_path, [ReleaseAsset("missing.mp4", "0" * 64)]).status == "FAIL"
    (tmp_path / "directory.mp4").mkdir()
    assert inspect_media_release(tmp_path, [ReleaseAsset("directory.mp4", "0" * 64)]).status == "FAIL"
    (tmp_path / "unknown.webm").write_bytes(b"unsupported container fixture")
    monkeypatch.setattr(inspection_module.shutil, "which", lambda name: pytest.fail("No tool needed"))
    unsupported = replace(pinned(tmp_path, "unknown.webm"), mime_type="video/webm")
    report = inspect_media_release(tmp_path, [unsupported]).to_dict()
    assert report["status"] == "UNKNOWN"
    assert report["assets"][0]["reason_codes"] == ["MEDIA_CONTAINER_UNSUPPORTED"]


@pytest.mark.parametrize("kind", ["hardlink", "symlink", "reparse", "ancestor_reparse"])
def test_link_and_reparse_detection_without_os_link_privileges(tmp_path, monkeypatch, kind):
    folder = tmp_path / "folder"
    folder.mkdir()
    source = folder / "movie.mp4"
    source.write_bytes(b"should never be opened")
    original = Path.lstat
    def unsafe_lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == (folder if kind == "ancestor_reparse" else source):
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else info.st_mode,
                                   st_file_attributes=0x400 if "reparse" in kind else 0,
                                   st_nlink=2 if kind == "hardlink" else 1)
        return info
    monkeypatch.setattr(Path, "lstat", unsafe_lstat)
    monkeypatch.setattr(inspection_module.shutil, "which", lambda name: pytest.fail("Unsafe path launched tools"))
    report = inspect_media_release(tmp_path, [ReleaseAsset("folder/movie.mp4", "0" * 64)])
    assert report.status == "FAIL"


def test_real_linked_files_are_rejected_when_os_permits(tmp_path, real_media):
    source = tmp_path / "original.mp4"
    shutil.copyfile(real_media / "silent-art.mp4", source)
    link = tmp_path / "link.mp4"
    supported = False
    try:
        os.link(source, link)
    except OSError:
        pass
    else:
        supported = True
        report = inspect_media_release(tmp_path, [ReleaseAsset("link.mp4", sha256_bytes(source.read_bytes()))])
        assert report.status == "FAIL"
        link.unlink()
    try:
        link.symlink_to(source)
    except OSError:
        if not supported:
            pytest.skip("Sandbox denies hard-link and symbolic-link creation; detection tested independently")
    else:
        assert inspect_media_release(tmp_path, [ReleaseAsset("link.mp4", hash_file(source)[0])]).status == "FAIL"


def test_source_changes_during_decode_even_with_preserved_stat_fail(real_media, tmp_path, monkeypatch):
    movie = tmp_path / "movie.mp4"
    shutil.copyfile(real_media / "silent-art.mp4", movie)
    asset = pinned(tmp_path, "movie.mp4")
    original = BoundedToolRunner.run
    altered = False
    def tamper(self, argv, *, strict_errors=True):
        nonlocal altered
        result = original(self, argv, strict_errors=strict_errors)
        if "-progress" in argv and not altered:
            altered = True
            info = movie.stat()
            with movie.open("r+b") as stream:
                stream.seek(20)
                byte = stream.read(1)
                stream.seek(20)
                stream.write(bytes((byte[0] ^ 1,)))
            os.utime(movie, ns=(info.st_atime_ns, info.st_mtime_ns))
        return result
    monkeypatch.setattr(BoundedToolRunner, "run", tamper)
    result = inspect_media_release(tmp_path, [asset]).to_dict()
    assert result["status"] == "FAIL" and altered
    assert result["assets"][0]["full_decode"] is False


def test_only_requested_asset_is_opened_and_byte_limits_apply(real_media, tmp_path):
    shutil.copyfile(real_media / "silent-art.mp4", tmp_path / "requested.mp4")
    (tmp_path / "unrequested.mp4").write_bytes(b"corrupt unrelated creative draft")
    asset = pinned(tmp_path, "requested.mp4")
    assert inspect_media_release(tmp_path, [asset]).status == "PASS"
    limited = inspect_media_release(tmp_path, [asset], limits=InspectionLimits(max_asset_bytes=16)).to_dict()
    assert limited["status"] == "UNKNOWN"
    assert limited["assets"][0]["reason_codes"] == ["MEDIA_BYTE_LIMIT"]
    shutil.copyfile(tmp_path / "requested.mp4", tmp_path / "second.mp4")
    total = inspect_media_release(tmp_path, [asset, pinned(tmp_path, "second.mp4")],
                                  limits=InspectionLimits(max_total_bytes=(tmp_path / "requested.mp4").stat().st_size))
    assert total.status == "UNKNOWN"
    assert total.to_dict()["assets"][1]["reason_codes"] == ["MEDIA_BYTE_LIMIT"]
    with pytest.raises(MediaInspectionError, match="RELEASE_ASSETS_REQUIRED"):
        inspect_media_release(tmp_path, [])
    with pytest.raises(MediaInspectionError, match="DUPLICATE_RELEASE_ASSET"):
        inspect_media_release(tmp_path, [asset, asset])


@pytest.mark.parametrize("pipe", [1, 2])
def test_tool_timeout_and_output_bytes_are_actually_bounded(tmp_path, pipe):
    runner = BoundedToolRunner(tmp_path, timeout_seconds=0.1, max_output_bytes=4096)
    with pytest.raises(ToolFailure, match="MEDIA_TOOL_TIMEOUT"):
        runner.run([sys.executable, "-c", "import time; time.sleep(3)"])
    runner = BoundedToolRunner(tmp_path, timeout_seconds=5, max_output_bytes=1024)
    with pytest.raises(ToolFailure, match="MEDIA_TOOL_OUTPUT_LIMIT"):
        runner.run([sys.executable, "-c", f"import os; os.write({pipe}, b'x' * 100000)"])
    with pytest.raises(ToolFailure, match="MEDIA_TOOL_UNAVAILABLE"):
        runner.run([str(tmp_path / "nonexistent-executable")])


def test_report_tampering_and_resealed_false_pass_are_rejected(real_media):
    report = inspect_media_release(real_media, [pinned(real_media)]).to_dict()
    report["assets"][0]["size_bytes"] += 1
    with pytest.raises(MediaInspectionError, match="INSPECTION_DIGEST_MISMATCH"):
        MediaReleaseInspection(report)
    report = inspect_media_release(real_media, [pinned(real_media)]).to_dict()
    report["assets"][0]["full_decode"] = False
    report["record_sha256"] = sha256_bytes(canonical_bytes(
        {k: v for k, v in report.items() if k != "record_sha256"}))
    with pytest.raises(MediaInspectionError, match="INSPECTION_PASS_WITHOUT_EVIDENCE"):
        MediaReleaseInspection(report)


def test_resealed_receipt_with_non_digest_hash_is_rejected(real_media):
    report = inspect_media_release(real_media, [pinned(real_media)]).to_dict()
    # JSON Schema's '$' anchor alone also accepts a final newline. A bound
    # digest must contain exactly 64 hex characters, even in a resealed record.
    report["assets"][0]["expected_sha256"] += "\n"
    report["assets"][0]["observed_sha256"] += "\n"
    report["record_sha256"] = sha256_bytes(canonical_bytes(
        {k: v for k, v in report.items() if k != "record_sha256"}))
    with pytest.raises(MediaInspectionError, match="INSPECTION_SCHEMA_INVALID"):
        MediaReleaseInspection(report)


def test_missing_probe_facts_are_unknown_and_optional_diagnostic_failure_is_advisory(real_media, monkeypatch):
    from ai_commerce.affiliate_release.process import ToolOutput
    original = BoundedToolRunner.run
    def incomplete_probe(self, argv, *, strict_errors=True):
        result = original(self, argv, strict_errors=strict_errors)
        if "-show_entries" in argv:
            data = json.loads(result.stdout)
            del data["format"]["duration"]
            return ToolOutput(canonical_bytes(data), result.stderr)
        return result
    monkeypatch.setattr(BoundedToolRunner, "run", incomplete_probe)
    assert inspect_media_release(real_media, [pinned(real_media)]).status == "UNKNOWN"
    def failed_diagnostic(self, argv, *, strict_errors=True):
        if "-vf" in argv:
            raise ToolFailure("MEDIA_TOOL_TIMEOUT")
        return original(self, argv, strict_errors=strict_errors)
    monkeypatch.setattr(BoundedToolRunner, "run", failed_diagnostic)
    report = inspect_media_release(real_media, [pinned(real_media)],
                                   diagnostics=DiagnosticOptions(black=True)).to_dict()
    assert report["status"] == "PASS"
    assert report["assets"][0]["diagnostics"]["black"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("invalid_format_name", [None, 42, []])
def test_malformed_tool_evidence_cannot_crash_or_claim_pass(real_media, monkeypatch, invalid_format_name):
    from ai_commerce.affiliate_release.process import ToolOutput
    original = BoundedToolRunner.run
    def malformed_probe(self, argv, *, strict_errors=True):
        result = original(self, argv, strict_errors=strict_errors)
        if "-show_entries" in argv:
            data = json.loads(result.stdout)
            data["format"]["format_name"] = invalid_format_name
            return ToolOutput(canonical_bytes(data), result.stderr)
        return result
    monkeypatch.setattr(BoundedToolRunner, "run", malformed_probe)
    report = inspect_media_release(real_media, [pinned(real_media)]).to_dict()
    assert report["status"] == "UNKNOWN"
    assert report["assets"][0]["reason_codes"] == ["MEDIA_FACTS_INCOMPLETE"]
    assert report["assets"][0]["full_decode"] is False


@pytest.mark.parametrize("target", ["capture", "source"])
def test_media_growing_beyond_bound_during_decode_is_tamper(real_media, tmp_path, monkeypatch, target):
    shutil.copyfile(real_media / "silent-art.mp4", tmp_path / "movie.mp4")
    asset = pinned(tmp_path, "movie.mp4")
    original = BoundedToolRunner.run
    def change_snapshot(self, argv, *, strict_errors=True):
        result = original(self, argv, strict_errors=strict_errors)
        if "-progress" in argv:
            path = Path(argv[argv.index("-i") + 1]) if target == "capture" else tmp_path / "movie.mp4"
            with path.open("ab") as stream:
                stream.write(b"altered captured bytes")
        return result
    monkeypatch.setattr(BoundedToolRunner, "run", change_snapshot)
    report = inspect_media_release(tmp_path, [asset],
                                   limits=InspectionLimits(max_asset_bytes=(tmp_path / "movie.mp4").stat().st_size)).to_dict()
    assert report["status"] == "FAIL"
    assert report["assets"][0]["reason_codes"] == ["MEDIA_CHANGED_DURING_INSPECTION"]


def test_empty_media_invalid_options_and_json_are_rejected(tmp_path):
    (tmp_path / "empty.mp4").write_bytes(b"")
    assert inspect_media_release(tmp_path, [pinned(tmp_path, "empty.mp4")]).status == "FAIL"
    for kwargs in ({"max_assets": True}, {"tool_timeout_seconds": float("nan")},
                   {"tool_timeout_seconds": 61}, {"max_tool_output_bytes": 2 ** 30}):
        with pytest.raises(MediaInspectionError, match="INVALID_INSPECTION_LIMITS"):
            InspectionLimits(**kwargs)
    with pytest.raises(MediaInspectionError, match="INSPECTION_JSON_INVALID"):
        MediaReleaseInspection.from_bytes(b'{"status":"PASS", "status":"FAIL"}')
    with pytest.raises(MediaInspectionError, match="INVALID_RELEASE_SHA256"):
        ReleaseAsset("video.mp4", "not a hash")
    with pytest.raises(MediaInspectionError, match="INVALID_RELEASE_ASSET_REF"):
        ReleaseAsset("bad\ud800.mp4", "0" * 64)
    # Python/pytest workers can have different recursion limits: both paths
    # reject this document, through JSON depth or through its invalid schema.
    with pytest.raises(MediaInspectionError, match="INSPECTION_(JSON|SCHEMA)_INVALID"):
        MediaReleaseInspection.from_bytes(b'{"nested":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}')


@pytest.mark.parametrize("message,code", [
    ("Cannot allocate memory", "MEDIA_TOOL_RESOURCE_LIMIT"),
    ("Decoding requested, but no decoder found", "MEDIA_TOOL_DECODER_UNAVAILABLE"),
])
def test_tool_resource_and_decoder_limits_are_unknown(tmp_path, message, code):
    runner = BoundedToolRunner(tmp_path, timeout_seconds=5, max_output_bytes=4096)
    with pytest.raises(ToolFailure, match=code) as caught:
        runner.run([sys.executable, "-c", f"import sys; sys.stderr.write({message!r}); sys.exit(1)"])
    assert caught.value.status == "UNKNOWN"


def png():
    def chunk(name, body):
        return struct.pack(">I", len(body)) + name + body + struct.pack(">I", zlib.crc32(name + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\x33\x55\x77" * 16) * 16))
            + chunk(b"IEND", b""))


def test_real_a06_manifest_binding_and_manifest_tamper(tmp_path):
    from ai_commerce.e2e import run
    image = tmp_path / "source.png"
    image.write_bytes(png())
    delivered = tmp_path / "delivery"
    run("Silent abstract art", [image], delivered, width=48, height=32, fps=10, frames=5)
    manifest_sha = hash_file(delivered / "e01-manifest.json")[0]
    asset = asset_from_e01(delivered, expected_manifest_sha256=manifest_sha)
    report = inspect_media_release(delivered, [asset]).to_dict()
    assert report["status"] == "PASS"
    source = report["assets"][0]["source"]
    assert source["kind"] == "A06_E01_MANIFEST_HASH_BOUND"
    assert source["manifest_sha256"] == manifest_sha
    assert source["custody_verification"] == "NOT_PERFORMED"
    with (delivered / "e01-manifest.json").open("ab") as stream:
        stream.write(b" ")
    assert inspect_media_release(delivered, [asset]).status == "FAIL"
    with pytest.raises(MediaInspectionError, match="E01_MANIFEST_SHA256_MISMATCH"):
        asset_from_e01(delivered, expected_manifest_sha256=manifest_sha)
