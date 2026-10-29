"""E01 product completion. Source truth stays with A04, bridge, composition and render.

One local run imports explicitly supplied stills, binds their provenance to a
freeform prompt, renders with the existing C06 engine, and delivers an MP4 with
a digest-bound evidence index. It does not invoke or claim a generative model.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Sequence

from ai_commerce.bridge import BridgeStore, CreativeAssetLink, composition_to_render_plan
from ai_commerce.composition import Canvas, CompositionStore, FrameRate, MediaReference, compose_images
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.media import MediaRequest
from ai_commerce.media.files import hash_file
from ai_commerce.operations import ArtifactRegistry, LocalMediaRunner
from ai_commerce.operations.manifest import strict_json
from ai_commerce.render import FFmpegRenderer

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
VERSION = "E01Manifest.v1"


class E01Error(ValueError):
    """User-actionable E01 failure; never implies a successful render."""


def _require(cond: bool, detail: str) -> None:
    if not cond:
        raise E01Error(detail)


def _check_payload(data: dict) -> dict:
    _require(isinstance(data, dict) and set(data) == {
        "schema_version", "run_id", "request_ref", "request_sha256",
        "assets", "composition", "render", "output_sha256",
        "publication_status", "generation_performed", "state", "record_sha256",
    }, "E01_MANIFEST_FIELDS_INVALID")
    digest = data["record_sha256"]
    raw = {k: v for k, v in data.items() if k != "record_sha256"}
    _require(data["schema_version"] == VERSION and data["state"] == "DELIVERED",
             "E01_MANIFEST_STATE_INVALID")
    _require(data["publication_status"] == "NOT_ATTEMPTED" and
             data["generation_performed"] is False, "E01_FALSE_EXTERNAL_CLAIM")
    _require(isinstance(digest, str) and digest == sha256_bytes(canonical_bytes(raw)),
             "E01_MANIFEST_DIGEST_MISMATCH")
    _require(data["request_ref"] == "e01/request.json" and isinstance(data["assets"], list)
             and len(data["assets"]) >= 1, "E01_REQUEST_OR_ASSET_INVALID")
    _require(isinstance(data["output_sha256"], str) and len(data["output_sha256"]) == 64,
             "E01_OUTPUT_DIGEST_INVALID")
    return data


def _write_new(path: Path, data: bytes) -> None:
    """Create a new view atomically; never quietly replace an existing delivery."""
    _require(not path.exists(), "E01_DELIVERY_CONFLICT: " + path.name)
    with tempfile.NamedTemporaryFile(prefix=".e01-write-", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        _require(not path.exists(), "E01_DELIVERY_CONFLICT: " + path.name)
        temporary.rename(path)
    finally:
        temporary.unlink(missing_ok=True)


def run(
    prompt: str,
    assets: Sequence[str | Path],
    output: str | Path,
    *,
    width: int = 64,
    height: int = 96,
    fps: int = 24,
    frames: int = 24,
    silence: bool = False,
) -> dict:
    """Deliver an offline MP4 from explicit local still images, with E01 evidence."""
    _require(isinstance(prompt, str) and bool(prompt.strip()), "E01_PROMPT_REQUIRED")
    _require(isinstance(assets, (tuple, list)) and bool(assets), "E01_LOCAL_ASSET_REQUIRED")
    _require(all(type(x) is int and x > 0 for x in (width, height, fps, frames))
             and width % 2 == 0 and height % 2 == 0, "E01_CANVAS_INVALID")
    _require(type(silence) is bool, "E01_SILENCE_INVALID")

    inputs: list[tuple[Path, str]] = []
    for asset in assets:
        path = Path(asset).absolute()
        mime = MIME.get(path.suffix.lower())
        _require(mime is not None and path.is_file(), "E01_INVALID_LOCAL_IMAGE: " + str(path))
        inputs.append((path, mime))
    dest = Path(output).absolute()
    _require(not dest.is_file(), "E01_OUTPUT_NOT_DIRECTORY")
    if dest.exists():
        _require(not any(dest.iterdir()), "E01_OUTPUT_NOT_EMPTY: use a fresh directory")
    dest.mkdir(parents=True, exist_ok=True)
    registry = ArtifactRegistry(dest / "custody")
    media_runner = LocalMediaRunner(registry)
    links = BridgeStore(registry)
    references = []
    asset_index = []
    for index, (path, mime) in enumerate(inputs):
        # The A04 importer performs real decoding/custody; extension alone is not proof.
        imported = media_runner.import_file(
            MediaRequest(mode="import", source_path=path, mime_type=mime))
        _require(imported.state == "SUCCEEDED", "E01_MEDIA_IMPORT_FAILED")
        artifact = imported.result["artifact"]
        link = CreativeAssetLink.create(
            prompt=prompt, asset_role=f"scene-{index + 1}", artifact=artifact,
            job_id=imported.job_id, attempt=imported.attempt,
            manifest_record_sha256=imported.to_dict()["record_sha256"],
            manifest_ref=media_runner.manifest_ref(imported.job_id, imported.attempt),
            provenance_class="LOCAL_IMPORT", realization_claim=False)
        link_ref = links.save(link)
        references.append(MediaReference.from_manifest(imported))
        asset_index.append({
            "manifest_ref": media_runner.manifest_ref(imported.job_id, imported.attempt),
            "manifest_sha256": imported.to_dict()["record_sha256"],
            "link_ref": link_ref,
            "link_id": link.link_id,
            "artifact_sha256": artifact["sha256"],
            "origin": "LOCAL_IMPORT",
            "realization_claim": False,
        })
    request = {
        "prompt": prompt,
        "asset_sha256s": [item["artifact_sha256"] for item in asset_index],
        "canvas": {"width": width, "height": height, "fps": fps},
        "frames_per_asset": frames,
        "silence": silence,
        "source_mode": "LOCAL_IMPORT",
    }
    req_data = canonical_bytes(request)
    registry.put_immutable("e01/request.json", req_data)
    comp = compose_images(
        prompt, [(ref, frames) for ref in references],
        canvas=Canvas(width, height, FrameRate(fps, 1)))
    comp_store = CompositionStore(registry.root)
    saved = comp_store.save(comp)
    comp_ref = comp_store.revision_ref(saved.composition_id, saved.revision)
    plan = composition_to_render_plan(
        saved, silence=silence, composition_ref=comp_ref)
    rendered = FFmpegRenderer(registry).render(plan)
    _require(rendered.state == "SUCCEEDED" and rendered.output is not None,
             "E01_RENDER_NOT_SUCCEEDED: " + str(rendered.error))
    renderer = FFmpegRenderer(registry)
    rendered = renderer.load(rendered.job_id)  # full ffprobe and decode, not metadata-only
    output_artifact = rendered.output
    _require(output_artifact is not None, "E01_NO_RENDER_OUTPUT")
    source_video = registry.safe_path(output_artifact["ref"])
    output_sha, size = hash_file(source_video)
    _require((output_sha, size) == (output_artifact["sha256"], output_artifact["size_bytes"]),
             "E01_RENDER_BYTES_CHANGED")

    payload = {
        "schema_version": VERSION,
        "run_id": "e01-" + sha256_bytes(req_data)[:32],
        "request_ref": "e01/request.json",
        "request_sha256": sha256_bytes(req_data),
        "assets": asset_index,
        "composition": {
            "id": saved.composition_id,
            "revision": saved.revision,
            "digest": saved.digest,
            "ref": comp_ref,
        },
        "render": {
            "job_id": rendered.job_id,
            "manifest_ref": renderer.manifest_ref(rendered.job_id),
            "manifest_sha256": rendered.to_dict()["record_sha256"],
        },
        "output_sha256": output_sha,
        "publication_status": "NOT_ATTEMPTED",
        "generation_performed": False,
        "state": "DELIVERED",
    }
    manifest = {**payload, "record_sha256": sha256_bytes(canonical_bytes(payload))}
    _check_payload(manifest)
    raw = canonical_bytes(manifest)
    registry.put_immutable("e01/manifest.json", raw)
    # E01 consumer view. No duplicate mutable domain authority.
    with tempfile.NamedTemporaryFile(prefix=".e01-video-", dir=dest, delete=False) as temp:
        temp_video = Path(temp.name)
        with source_video.open("rb") as source:
            shutil.copyfileobj(source, temp, length=1024 * 1024)
        temp.flush()
        os.fsync(temp.fileno())
    try:
        _require(hash_file(temp_video) == (output_sha, size), "E01_DELIVERY_BYTES_CHANGED")
        _require(not (dest / "video.mp4").exists(), "E01_DELIVERY_CONFLICT")
        temp_video.rename(dest / "video.mp4")
    finally:
        temp_video.unlink(missing_ok=True)
    _write_new(dest / "e01-manifest.json", raw)
    delivery = {
        "schema_version": "E01Delivery.v1",
        "video_ref": "video.mp4",
        "manifest_ref": "e01-manifest.json",
        "video_sha256": output_sha,
        "manifest_sha256": sha256_bytes(raw),
        "state": "DELIVERED",
        "publication_status": "NOT_ATTEMPTED",
    }
    _write_new(dest / "delivery.json", canonical_bytes(delivery))
    return verify(dest)


def inspect(output: str | Path) -> dict:
    """Read the delivery index without running FFmpeg or modifying evidence."""
    root = Path(output).absolute()
    manifest = root / "e01-manifest.json"
    if not manifest.exists():
        return {"state": "INCOMPLETE", "reason": "E01_MANIFEST_MISSING",
                "run": str(root)}
    return _check_payload(strict_json(manifest.read_bytes()))


def verify(output: str | Path) -> dict:
    """Reopen all evidence from disk; A05 load performs ffprobe and full decode."""
    root = Path(output).absolute()
    record = inspect(root)
    _require(record.get("state") == "DELIVERED", "E01_PACKAGE_INCOMPLETE")
    registry = ArtifactRegistry(root / "custody")
    _require(registry.read("e01/manifest.json") == (root / "e01-manifest.json").read_bytes(),
             "E01_MANIFEST_VIEW_MISMATCH")
    req_raw = registry.read(record["request_ref"])
    _require(sha256_bytes(req_raw) == record["request_sha256"], "E01_REQUEST_TAMPERED")
    request = strict_json(req_raw)
    _require(request.get("source_mode") == "LOCAL_IMPORT", "E01_SOURCE_MODE_INVALID")
    runner = LocalMediaRunner(registry)
    bridge = BridgeStore(registry)
    verified = []
    for index, asset in enumerate(record["assets"]):
        _require(set(asset) == {"manifest_ref", "manifest_sha256", "link_ref", "link_id",
                               "artifact_sha256", "origin", "realization_claim"},
                 "E01_ASSET_INDEX_INVALID")
        _require(asset["origin"] == "LOCAL_IMPORT" and asset["realization_claim"] is False,
                 "E01_FALSE_GENERATION_CLAIM")
        parts = asset["manifest_ref"].split("/")
        _require(len(parts) == 3 and parts[0] == "jobs" and parts[2].startswith("attempt-"),
                 "E01_INVALID_ASSET_MANIFEST_REF")
        job_id = parts[1]
        attempt = int(parts[2].removeprefix("attempt-").removesuffix(".manifest.json"))
        saved = runner.load(job_id, attempt)
        _require(saved.to_dict()["record_sha256"] == asset["manifest_sha256"],
                 "E01_ASSET_MANIFEST_MISMATCH")
        artifact = saved.result["artifact"]
        link = bridge.load(asset["link_ref"])
        _require(link.link_id == asset["link_id"] and link.job_id == job_id and
                 link.attempt == attempt and link.artifact_sha256 == artifact["sha256"] and
                 link.prompt == request["prompt"] and link.provenance_class == "LOCAL_IMPORT" and
                 not link.realization_claim and asset["artifact_sha256"] == artifact["sha256"],
                 "E01_ASSET_PROVENANCE_MISMATCH")
        verified.append(artifact["sha256"])
    _require(verified == request["asset_sha256s"], "E01_ASSET_SEQUENCE_MISMATCH")
    composition = record["composition"]
    store = CompositionStore(registry.root)
    saved_comp = store.load(composition["id"], composition["revision"])
    _require(saved_comp.digest == composition["digest"] and
             store.revision_ref(saved_comp.composition_id, saved_comp.revision) == composition["ref"] and
             saved_comp.original_intent == request["prompt"],
             "E01_COMPOSITION_IDENTITY_MISMATCH")
    renderer = FFmpegRenderer(registry)
    render = renderer.load(record["render"]["job_id"])
    _require(render.state == "SUCCEEDED" and render.output is not None and
             render.to_dict()["record_sha256"] == record["render"]["manifest_sha256"] and
             renderer.manifest_ref(render.job_id) == record["render"]["manifest_ref"] and
             render.output["sha256"] == record["output_sha256"],
             "E01_RENDER_EVIDENCE_MISMATCH")
    movie = root / "video.mp4"
    _require(movie.is_file() and hash_file(movie) == (
        render.output["sha256"], render.output["size_bytes"]), "E01_VIDEO_BYTES_MISMATCH")
    delivery = strict_json((root / "delivery.json").read_bytes())
    _require(delivery == {
        "schema_version": "E01Delivery.v1",
        "video_ref": "video.mp4", "manifest_ref": "e01-manifest.json",
        "video_sha256": record["output_sha256"],
        "manifest_sha256": sha256_bytes((root / "e01-manifest.json").read_bytes()),
        "state": "DELIVERED", "publication_status": "NOT_ATTEMPTED",
    }, "E01_DELIVERY_INDEX_MISMATCH")
    return {
        "state": "DELIVERED",
        "run_id": record["run_id"],
        "video": str(movie),
        "sha256": record["output_sha256"],
        "assets": len(verified),
        "publication_status": "NOT_ATTEMPTED",
        "generation_performed": False,
        "ffprobe_full_decode": True,
    }
