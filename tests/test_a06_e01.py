"""A06: consumer-level evidence and playable MP4 from explicitly imported stills."""
import json
import struct
import zlib
from pathlib import Path

import pytest

from ai_commerce.cli import main
from ai_commerce.e2e import E01Error, inspect, run, verify


def png(red: int) -> bytes:
    width = height = 16
    row = b"\x00" + bytes((red, 30, 170)) * width
    pixels = zlib.compress(row * height)
    def chunk(kind, content):
        return (struct.pack(">I", len(content)) + kind + content
                + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", pixels) + chunk(b"IEND", b""))


def assets(root):
    result = []
    for n, red in enumerate((50, 220)):
        path = root / f"scene-{n}.png"
        path.write_bytes(png(red))
        result.append(path)
    return result


def test_local_two_scene_mp4_and_reopen_with_truthful_provenance(tmp_path):
    images = assets(tmp_path)
    out = tmp_path / "e01"
    result = run("A fictional robot cat story; no product, no CTA", images, out,
                 width=64, height=96, fps=24, frames=12, silence=False)
    assert result["state"] == "DELIVERED"
    assert result["ffprobe_full_decode"] is True
    assert result["assets"] == 2
    assert result["publication_status"] == "NOT_ATTEMPTED"
    assert result["generation_performed"] is False
    assert (out / "video.mp4").stat().st_size > 0
    manifest = inspect(out)
    assert len(manifest["assets"]) == 2
    assert all(row["origin"] == "LOCAL_IMPORT" and row["realization_claim"] is False
               for row in manifest["assets"])
    assert manifest["render"]["job_id"]
    assert verify(out) == result


def test_video_tamper_refused_even_if_custody_is_untouched(tmp_path):
    image = assets(tmp_path)[0]
    out = tmp_path / "e01"
    run("A still", [image], out, frames=2)
    with (out / "video.mp4").open("ab") as stream:
        stream.write(b"tampering")
    with pytest.raises(E01Error, match="E01_VIDEO_BYTES_MISMATCH"):
        verify(out)


def test_manifest_tamper_not_mistaken_for_success(tmp_path):
    image = assets(tmp_path)[0]
    out = tmp_path / "e01"
    run("A still", [image], out, frames=2)
    record = json.loads((out / "e01-manifest.json").read_bytes())
    record["publication_status"] = "PUBLISHED_CONFIRMED"
    (out / "e01-manifest.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(E01Error):
        verify(out)


def test_missing_corrupt_input_and_partial_delivery_do_not_pass(tmp_path):
    with pytest.raises(E01Error, match="E01_LOCAL_ASSET_REQUIRED"):
        run("A", [], tmp_path / "missing")
    with pytest.raises(E01Error, match="E01_INVALID_LOCAL_IMAGE"):
        run("A", [tmp_path / "nonexistent.png"], tmp_path / "noimage")
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a PNG")
    with pytest.raises(Exception):
        run("A", [broken], tmp_path / "corrupt")
    partial = tmp_path / "interrupted"
    partial.mkdir()
    (partial / "request.json").write_text("{}", encoding="utf-8")
    assert inspect(partial)["state"] == "INCOMPLETE"
    with pytest.raises(E01Error, match="E01_PACKAGE_INCOMPLETE"):
        verify(partial)


def test_existing_output_never_silently_overwritten(tmp_path):
    image = assets(tmp_path)[0]
    out = tmp_path / "e01"
    run("An original story", [image], out, frames=2)
    with pytest.raises(E01Error, match="E01_OUTPUT_NOT_EMPTY"):
        run("A competing story", [image], out, frames=2)
    assert verify(out)["state"] == "DELIVERED"


def test_cli_additive_commands_create_inspect_verify(tmp_path, capsys):
    image = assets(tmp_path)[0]
    out = tmp_path / "demo"
    assert main(["creative-e2e", "run", "--prompt", "Cat in space",
                 "--asset", str(image), "--output", str(out), "--frames", "2"]) == 0
    capsys.readouterr()
    assert main(["creative-e2e", "inspect", "--run", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "DELIVERED"
    assert main(["creative-e2e", "verify", "--run", str(out),
                 "--full-decode"]) == 0
    assert json.loads(capsys.readouterr().out)["ffprobe_full_decode"] is True
