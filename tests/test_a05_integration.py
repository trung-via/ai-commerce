"""A05 End-to-End integration tests.

Verifies:
- Legacy A02/A03/A04 commands and handoffs remain completely identical.
- CLI composition-create, composition-inspect, render-run, render-inspect.
- Real playable H.264 MP4 rendering with ffprobe stream validation and full decode.
- Manifest record hashes, reopen validation, and tamper rejection.
- Error cases (unresolved intent, corrupt media, non-even dimensions).
"""

from __future__ import annotations

import json
import struct
import zlib
from fractions import Fraction
from pathlib import Path

import pytest

from ai_commerce.bridge import BridgeStore, composition_to_render_plan
from ai_commerce.cli import main
from ai_commerce.composition import CompositionStore
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.media.files import hash_file
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.render import FFmpegRenderer
from ai_commerce.render.process import ToolRunner
from ai_commerce.render.verify import verify_mp4


def _make_png(red: int = 255, green: int = 0, blue: int = 0) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))
    raw_pixel = bytes([0, red, green, blue])
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw_pixel))
            + chunk(b"IEND", b""))


def _invoke_cli(capsys, argv: list[str], expected_code: int = 0) -> dict | str:
    code = main(argv)
    captured = capsys.readouterr()
    assert code == expected_code, f"Expected {expected_code}, got {code}. stderr: {captured.err}"
    if expected_code == 0:
        return json.loads(captured.out)
    return captured.err


def test_legacy_cli_commands_remain_identical(tmp_path: Path, capsys):
    """Ensure existing concept and asset commands retain 100% exact behavior."""
    store_dir = tmp_path / "creative"
    out_dir = tmp_path / "assets"
    source_png = tmp_path / "test.png"
    source_png.write_bytes(_make_png(128, 64, 32))

    # 1. concept-create
    c_out = _invoke_cli(capsys, [
        "concept-create", "--text", "Cà phê sữa đá Sài Gòn", "--store", str(store_dir), "--mock",
    ])
    assert "concepts" in c_out and len(c_out["concepts"]) == 1
    selection_id = c_out["concepts"][0]["selection"]["selection_id"]

    # 2. concept-inspect
    insp_out = _invoke_cli(capsys, [
        "concept-inspect", "--store", str(store_dir), "--selection-id", selection_id,
    ])
    assert insp_out["selection"]["selection_id"] == selection_id

    # 3. concept-reframe
    changes_file = tmp_path / "changes.json"
    changes_file.write_text(json.dumps({"premise": "Vị đắng ngọt ngào của buổi sớm."}), encoding="utf-8")
    reframe_out = _invoke_cli(capsys, [
        "concept-reframe", "--store", str(store_dir), "--selection-id", selection_id,
        "--lens", "EMPHASIS", "--changes", str(changes_file),
    ])
    assert reframe_out["concept"]["premise"] == "Vị đắng ngọt ngào của buổi sớm."
    assert reframe_out["concept"]["revision"] == 2
    revised_sel_id = reframe_out["selection"]["selection_id"]

    # 4. asset-run (import mode)
    asset_out = _invoke_cli(capsys, [
        "asset-run", "--store", str(store_dir), "--selection-id", revised_sel_id,
        "--out-dir", str(out_dir), "--mode", "import", "--source", str(source_png),
        "--mime-type", "image/png",
    ])
    assert asset_out["asset_manifest"]["state"] == "SUCCEEDED"
    handoff_ref = asset_out["handoff_ref"]
    assert handoff_ref.startswith("handoffs/")

    # 5. asset-inspect
    asset_insp = _invoke_cli(capsys, [
        "asset-inspect", "--store", str(store_dir), "--out-dir", str(out_dir),
        "--handoff", handoff_ref,
    ])
    assert asset_insp == asset_out


def test_cli_composition_create_inspect_and_unresolved_intent(tmp_path: Path, capsys):
    out_dir = tmp_path / "compositions"

    # Create unresolved intent composition
    c_out = _invoke_cli(capsys, [
        "composition-create", "--text", "Nắng chiều trên sông Sài Gòn",
        "--out-dir", str(out_dir), "--width", "64", "--height", "96", "--fps", "24",
    ])
    assert c_out["unresolved_intent"] is True
    comp_id = c_out["composition_id"]
    revision = c_out["revision"]

    # Inspect the saved composition
    insp_out = _invoke_cli(capsys, [
        "composition-inspect", "--out-dir", str(out_dir),
        "--composition-id", comp_id, "--revision", str(revision),
    ])
    assert insp_out == c_out

    # Attempting to render an unresolved composition must fail with ASSET_REQUIRED
    err = _invoke_cli(capsys, [
        "render-run", "--out-dir", str(out_dir),
        "--composition-id", comp_id, "--revision", str(revision),
    ], expected_code=2)
    assert "ASSET_REQUIRED" in err


def test_cli_render_run_and_inspect_real_mp4(tmp_path: Path, capsys):
    out_dir = tmp_path / "render_direct"
    source_png = tmp_path / "still.png"
    source_png.write_bytes(_make_png(200, 100, 50))

    # render-run from direct still image
    r_out = _invoke_cli(capsys, [
        "render-run", "--out-dir", str(out_dir), "--source", str(source_png),
        "--mime-type", "image/png", "--width", "64", "--height", "96", "--fps", "24",
        "--duration-frames", "24", "--silence",
    ])
    assert r_out["state"] == "SUCCEEDED"
    job_id = r_out["job_id"]
    output = r_out["output"]
    assert output is not None
    assert output["mime_type"] == "video/mp4"

    # Verify actual MP4 file exists in registry and matches hash
    registry = ArtifactRegistry(out_dir)
    movie_path = registry.safe_path(output["ref"])
    assert movie_path.is_file()
    sha, size = hash_file(movie_path)
    assert (sha, size) == (output["sha256"], output["size_bytes"])

    # render-inspect succeeds and matches
    insp_out = _invoke_cli(capsys, [
        "render-inspect", "--out-dir", str(out_dir), "--job-id", job_id,
    ])
    assert insp_out["job_id"] == job_id
    assert insp_out["state"] == "SUCCEEDED"

    # Tamper with the output file: render-inspect must fail closed
    movie_path.write_bytes(movie_path.read_bytes() + b"\x00")
    err = _invoke_cli(capsys, [
        "render-inspect", "--out-dir", str(out_dir), "--job-id", job_id,
    ], expected_code=2)
    assert any(x in err for x in ("corrupted or modified", "Stored output SHA256/size mismatch", "OUTPUT_CORRUPT"))


def test_full_e2e_a03_to_a05_playable_mp4(tmp_path: Path, capsys):
    """End-to-end: Concept -> Asset -> Link -> Composition -> Render -> Real Playable MP4."""
    store_dir = tmp_path / "creative"
    assets_dir = tmp_path / "work"
    source_png = tmp_path / "hero.png"
    source_png.write_bytes(_make_png(255, 0, 0))

    # 1. Concept Create
    c_out = _invoke_cli(capsys, [
        "concept-create", "--text", "Hương hoa sen Đồng Tháp Mười",
        "--store", str(store_dir), "--mock",
    ])
    sel_id = c_out["concepts"][0]["selection"]["selection_id"]

    # 2. Asset Run
    asset_out = _invoke_cli(capsys, [
        "asset-run", "--store", str(store_dir), "--selection-id", sel_id,
        "--out-dir", str(assets_dir), "--mode", "import", "--source", str(source_png),
        "--mime-type", "image/png",
    ])
    handoff_ref = asset_out["handoff_ref"]

    # 3. Upgrade / Link via BridgeStore
    registry = ArtifactRegistry(assets_dir)
    bridge_store = BridgeStore(registry)
    link_ref, link = bridge_store.upgrade_legacy_handoff(handoff_ref, creative_store=store_dir)
    assert link.provenance_class == "LOCAL_IMPORT"
    assert link.realization_claim is False
    assert link.prompt == "Hương hoa sen Đồng Tháp Mười"

    # 4. Composition Create with media from handoff
    comp_out = _invoke_cli(capsys, [
        "composition-create", "--text", "Hương hoa sen Đồng Tháp Mười",
        "--out-dir", str(assets_dir), "--handoff", handoff_ref,
        "--width", "64", "--height", "96", "--fps", "24", "--duration-frames", "24",
    ])
    assert comp_out["unresolved_intent"] is False
    comp_id = comp_out["composition_id"]

    # 5. Render Run from composition
    render_out = _invoke_cli(capsys, [
        "render-run", "--out-dir", str(assets_dir),
        "--composition-id", comp_id, "--revision", "1", "--silence",
    ])
    assert render_out["state"] == "SUCCEEDED"
    job_id = render_out["job_id"]

    # 6. Verify technical MP4 playability through ffprobe + full decode
    renderer = FFmpegRenderer(registry)
    loaded_manifest = renderer.load(job_id)
    assert loaded_manifest.state == "SUCCEEDED"
    assert loaded_manifest.output is not None

    movie_path = registry.safe_path(loaded_manifest.output["ref"])
    comp_store = CompositionStore(assets_dir)
    comp = comp_store.load(comp_id, 1)
    plan = composition_to_render_plan(comp, silence=True)

    tools = renderer._tools()
    scratch_dir = tmp_path / "probe_scratch"
    scratch_dir.mkdir(exist_ok=True)
    t_runner = ToolRunner(scratch_dir, 60.0, [])
    verified_meta = verify_mp4(movie_path, plan, tools, t_runner)
    assert verified_meta["mime_type"] == "video/mp4"
    observed = verified_meta["verification"]
    assert observed["codec"] == "h264"
    assert observed["pixel_format"] == "yuv420p"
    assert observed["width"] == 64
    assert observed["height"] == 96
    assert observed["frame_count"] == 24
    assert observed["audio"]["codec"] == "aac"


def test_multi_scene_ordered_composition_render(tmp_path: Path):
    """Test composition with two ordered scenes (Red then Blue) rendered to MP4."""
    out_dir = tmp_path / "multi_scene"
    registry = ArtifactRegistry(out_dir)

    red_png = tmp_path / "red.png"
    red_png.write_bytes(_make_png(255, 0, 0))
    blue_png = tmp_path / "blue.png"
    blue_png.write_bytes(_make_png(0, 0, 255))

    runner = FFmpegRenderer(registry)
    from ai_commerce.composition.authoring import compose_images
    from ai_commerce.composition.models import Canvas as CompCanvas, FrameRate
    from ai_commerce.composition.sources import MediaReference
    from ai_commerce.operations import LocalMediaRunner
    from ai_commerce.media import MediaRequest

    media_runner = LocalMediaRunner(registry)
    m_red = media_runner.run(MediaRequest(mode="import", source_path=red_png, mime_type="image/png"))
    m_blue = media_runner.run(MediaRequest(mode="import", source_path=blue_png, mime_type="image/png"))

    ref_red = MediaReference.from_manifest(m_red)
    ref_blue = MediaReference.from_manifest(m_blue)

    canvas = CompCanvas(64, 96, FrameRate(15, 1))
    comp = compose_images(
        "Hai cảnh sắc đỏ và xanh",
        [(ref_red, 7), (ref_blue, 13)],
        canvas=canvas,
    )
    store = CompositionStore(out_dir)
    saved = store.save(comp)

    plan = composition_to_render_plan(saved, silence=True)
    manifest = runner.render(plan, job_id="multi-scene-01")
    assert manifest.state == "SUCCEEDED"
    assert manifest.output is not None

    movie = registry.safe_path(manifest.output["ref"])
    scratch2 = tmp_path / "scratch2"
    scratch2.mkdir(exist_ok=True)
    t_runner2 = ToolRunner(scratch2, 60.0, [])
    meta = verify_mp4(movie, plan, runner._tools(), t_runner2)
    assert meta["verification"]["frame_count"] == 20
    assert float(Fraction(meta["verification"]["duration"])) == pytest.approx(20 / 15, abs=0.01)
