"""Local FFmpeg implementation of the provider-neutral renderer protocol.

A04 owns source validation and storage safety. Derived output uses its chunked
capture and no-replace commit, NOT register_file(import), which would mislabel
the output. _commit is the one pinned A04-v1.1 internal custody seam used here.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Protocol

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes, timestamp_utc
from ai_commerce.media.base import MediaDependencyError, MediaValidationError
from ai_commerce.media.files import hash_file, open_regular, validate_media_file
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.errors import JobCollisionError, MediaOperationError
from ai_commerce.operations.manifest import AssetJobManifest, strict_json

from .manifest import RenderManifest, checksum, manifest_ref, validate_binding
from .models import EncoderConfig, IMAGE_CODECS, RenderError, RenderPlan
from .process import ToolRunner
from .verify import verify_mp4

MAX_METADATA = 1024 * 1024


class RendererAdapter(Protocol):
    def detect(self) -> dict: ...
    def render(self, plan: RenderPlan, *, job_id: str | None = None) -> RenderManifest: ...
    def load(self, job_id: str) -> RenderManifest: ...


def _read(registry: ArtifactRegistry, ref: str) -> bytes:
    with open_regular(registry.safe_path(ref)) as stream:
        raw = stream.read(MAX_METADATA + 1)
    if len(raw) > MAX_METADATA:
        raise RenderError("MANIFEST_INVALID", "Metadata exceeds 1 MiB", "REOPEN")
    return raw


class FFmpegRenderer:
    def __init__(self, registry: ArtifactRegistry, *, encoder: EncoderConfig | None = None,
                 timeout_seconds: float = 60, ffmpeg: str | None = None,
                 ffprobe: str | None = None):
        self.registry, self.encoder = registry, encoder or EncoderConfig()
        if not isinstance(self.encoder, EncoderConfig):
            raise ValueError("Expected EncoderConfig")
        # Validate even on a host with no tools.
        ToolRunner(registry.root, timeout_seconds, [])
        self.timeout = timeout_seconds
        self.ffmpeg, self.ffprobe = ffmpeg or "ffmpeg", ffprobe or "ffprobe"

    @staticmethod
    def manifest_ref(job_id: str) -> str:
        return manifest_ref(job_id)

    def _tools(self) -> dict:
        return {"ffmpeg": shutil.which(self.ffmpeg), "ffprobe": shutil.which(self.ffprobe),
                "ffmpeg_version": None, "ffprobe_version": None, "libx264": False, "aac": False, "decoders": []}

    def _detect(self, tools: dict, runner: ToolRunner, *, silence: bool = False,
                source_mimes: tuple[str, ...] = ("image/png",)) -> dict:
        missing = []
        for name in ("ffmpeg", "ffprobe"):
            if tools[name] is None:
                missing.append(name)
                continue
            version = runner.run([tools[name], "-version"], "CAPABILITIES", strict_errors=False)
            first = version.decode("utf-8", "replace").splitlines()
            if not first or not first[0].startswith(name + " version "):
                raise RenderError("ENVIRONMENT_TOOL_UNAVAILABLE",
                                  f"{tools[name]} did not identify itself as {name}", "CAPABILITIES")
            tools[name + "_version"] = first[0]
        if missing:
            raise RenderError("ENVIRONMENT_TOOL_UNAVAILABLE",
                              "Missing local executable(s): " + ", ".join(missing) +
                              ". Provide trusted installed tools on PATH or explicit executable paths; no binaries were installed.",
                              "CAPABILITIES")
        listing = runner.run([tools["ffmpeg"], "-hide_banner", "-encoders"],
                             "CAPABILITIES", strict_errors=False).decode("utf-8", "replace")
        encoders = set()
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) >= 2 and re.fullmatch(r"[VAS][A-Z.]{5}", parts[0]):
                encoders.add(parts[1])
        tools["libx264"], tools["aac"] = "libx264" in encoders, "aac" in encoders
        required = {"libx264", "png"} | ({"aac"} if silence else set())
        if required - encoders:
            raise RenderError("ENVIRONMENT_ENCODER_UNAVAILABLE",
                              "FFmpeg lacks required encoder(s): " + ", ".join(sorted(required - encoders)) +
                              "; H264 requires libx264 in this adapter.", "CAPABILITIES")
        listing = runner.run([tools["ffmpeg"], "-hide_banner", "-decoders"],
                             "CAPABILITIES", strict_errors=False).decode("utf-8", "replace")
        decoders = set()
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) >= 2 and re.fullmatch(r"[VAS][A-Z.]{5}", parts[0]):
                decoders.add(parts[1])
        tools["decoders"] = sorted(decoders)
        required_decoders = {"h264"} | {IMAGE_CODECS[m] for m in source_mimes} | ({"aac"} if silence else set())
        if required_decoders - decoders:
            raise RenderError("ENVIRONMENT_DECODER_UNAVAILABLE",
                              "FFmpeg lacks required decoder(s): " + ", ".join(sorted(required_decoders - decoders)),
                              "CAPABILITIES")
        return tools

    def detect(self) -> dict:
        self.registry.safe_path(".render-probe")
        diagnostics = []
        with tempfile.TemporaryDirectory(prefix=".render-probe-", dir=self.registry.root) as temp:
            try:
                return self._detect(self._tools(), ToolRunner(Path(temp), self.timeout, diagnostics))
            except RenderError as exc:
                exc.diagnostics = tuple(diagnostics)
                raise

    def _sources(self, plan: RenderPlan) -> list[Path]:
        paths = []
        try:
            for item in plan.items:
                source, artifact = item.source, item.source.artifact
                if source.manifest_ref is not None:
                    saved = AssetJobManifest.from_dict(strict_json(_read(self.registry, source.manifest_ref)))
                    if (saved.state != "SUCCEEDED" or saved.result["artifact"] != artifact
                            or saved.to_dict()["record_sha256"] != source.manifest_digest):
                        raise RenderError("SOURCE_CORRUPT", "Source manifest/artifact binding differs", "SOURCE")
                paths.append(self.registry.verify_file(artifact))
        except MediaDependencyError as exc:
            raise RenderError("ENVIRONMENT_SOURCE_DECODER_UNAVAILABLE", str(exc), "SOURCE") from exc
        except (MediaValidationError, MediaOperationError, OSError, ValueError) as exc:
            raise RenderError("SOURCE_INVALID", str(exc), "SOURCE") from exc
        return paths

    def _argv(self, plan: RenderPlan, sources: list[Path], tools: dict, output: Path) -> list[str]:
        c = plan.canvas
        argv = [tools["ffmpeg"], "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-n",
                "-filter_complex_threads", "1"]
        for item, path in zip(plan.items, sources):
            argv += ["-err_detect", "explode", "-protocol_whitelist", "file", "-f", "image2",
                     "-pattern_type", "none", "-loop", "1", "-framerate", str(c.fps),
                     "-c:v", IMAGE_CODECS[item.source.artifact["mime_type"]], "-i", str(path)]
        if plan.silence:
            argv += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        filters = []
        for index, item in enumerate(plan.items):
            filters.append(
                f"[{index}:v]scale={c.width}:{c.height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={c.width}:{c.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,setparams=range=tv,format=yuv420p,"
                f"fps={c.fps},trim=end_frame={item.duration_frames},setpts=PTS-STARTPTS[s{index}]"
            )
        labels = "".join(f"[s{i}]" for i in range(len(plan.items)))
        filters.append(labels + f"concat=n={len(plan.items)}:v=1:a=0[video]")
        argv += ["-filter_complex", ";".join(filters), "-map", "[video]",
                 "-c:v", self.encoder.encoder, "-crf", str(self.encoder.crf),
                 "-preset", self.encoder.preset, "-pix_fmt", "yuv420p", "-r", str(c.fps),
                 "-threads", "1"]
        # Both outputs are finite; frame count remains the authoritative video bound.
        if plan.silence:
            duration = format(plan.duration_frames / c.fps, ".12f")
            argv += ["-map", f"{len(plan.items)}:a:0", "-c:a", "aac", "-b:a", "128k",
                     "-af", f"atrim=duration={duration},asetpts=PTS-STARTPTS", "-t", duration]
        else:
            argv += ["-an"]
        if not plan.silence:
            argv += ["-frames:v", str(plan.duration_frames)]
        argv += ["-movflags", "+faststart", "-f", "mp4", str(output)]
        return argv

    def _store_file(self, source: Path, expected: dict) -> None:
        # A04 capture/commit never reads MP4 into a bytes object.
        with self.registry.capture_file(source) as captured:
            if (captured.sha256, captured.size_bytes) != (expected["sha256"], expected["size_bytes"]):
                raise RenderError("OUTPUT_CORRUPT", "Validated output changed before custody", "REGISTER")
            temporary = captured.path.relative_to(self.registry.root).as_posix()
            self.registry._commit(temporary, expected["ref"])

    def _finish(self, binding, state, tools, diagnostics, *, output=None, thumbnail=None, error=None):
        raw = canonical_bytes({"commands": diagnostics})
        if len(raw) > MAX_METADATA:
            raise RenderError("TOOL_OUTPUT_LIMIT", "Combined diagnostics exceed 1 MiB", "DIAGNOSTICS")
        ref = f"render-jobs/{binding['job_id']}/diagnostics.json"
        self.registry.put_immutable(ref, raw)
        receipt = RenderManifest.build(
            binding, state=state, tools=tools,
            diagnostics={"ref": ref, "sha256": sha256_bytes(raw), "size_bytes": len(raw)},
            output=output, thumbnail=thumbnail, error=error,
        )
        self.registry.put_immutable(manifest_ref(binding["job_id"]), canonical_bytes(receipt.to_dict()))
        return receipt

    def render(self, plan: RenderPlan, *, job_id: str | None = None) -> RenderManifest:
        if not isinstance(plan, RenderPlan):
            raise ValueError("Renderer requires a RenderPlan data contract")
        job_id = job_id or "render-" + uuid.uuid4().hex
        manifest_ref(job_id)
        request_ref = f"render-jobs/{job_id}/request.json"
        with self.registry.worker():
            if self.registry.exists(request_ref):
                binding = strict_json(_read(self.registry, request_ref))
                validate_binding(binding)
                if binding["job_id"] != job_id or binding["plan"] != plan.to_dict() or binding["encoder"] != self.encoder.to_dict():
                    raise JobCollisionError("Immutable render job is bound to different inputs/configuration")
                # No implicit retry of failed/interrupted jobs; choose a new job ID.
                return self.load(job_id)
            binding = checksum({"request_version": 1, "job_id": job_id, "created_at": timestamp_utc(),
                                "plan": plan.to_dict(), "encoder": self.encoder.to_dict()})
            raw = canonical_bytes(binding)
            if len(raw) > MAX_METADATA:
                raise ValueError("Render request exceeds the 1 MiB metadata bound")
            self.registry.put_immutable(request_ref, raw)
            tools, diagnostics = self._tools(), []
            try:
                self.registry.safe_path(".render-work")
                with tempfile.TemporaryDirectory(prefix=".render-work-", dir=self.registry.root) as temp:
                    directory = Path(temp)
                    runner = ToolRunner(directory, self.timeout, diagnostics)
                    sources = self._sources(plan)
                    self._detect(tools, runner, silence=plan.silence,
                                 source_mimes=tuple(x.source.artifact["mime_type"] for x in plan.items))
                    movie, thumb = directory / "output.mp4", directory / "thumbnail.png"
                    runner.run(self._argv(plan, sources, tools, movie), "ENCODE")
                    output = verify_mp4(movie, plan, tools, runner)
                    runner.run([
                        tools["ffmpeg"], "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-n",
                        "-err_detect", "explode", "-protocol_whitelist", "file", "-enable_drefs", "0",
                        "-i", str(movie), "-map", "0:v:0", "-frames:v", "1", "-threads", "1",
                        "-c:v", "png", "-f", "image2", str(thumb),
                    ], "THUMBNAIL")
                    validate_media_file(thumb, "image/png")
                    sha, size = hash_file(thumb)
                    thumbnail = {"ref": "objects/" + sha, "sha256": sha, "size_bytes": size, "mime_type": "image/png"}
                    # Validate source custody again after all decoder reads.
                    self._sources(plan)
                    if hash_file(movie) != (output["sha256"], output["size_bytes"]):
                        raise RenderError("OUTPUT_CORRUPT", "Output changed after decode", "REGISTER")
                    self._store_file(movie, output)
                    self._store_file(thumb, thumbnail)
            except RenderError as exc:
                return self._finish(binding, "FAILED", tools, diagnostics, error=exc)
            except MediaDependencyError as exc:
                return self._finish(binding, "FAILED", tools, diagnostics,
                                    error=RenderError("ENVIRONMENT_MEDIA_UNAVAILABLE", str(exc), "VERIFY"))
            except (MediaValidationError, MediaOperationError, OSError, ValueError) as exc:
                return self._finish(binding, "FAILED", tools, diagnostics,
                                    error=RenderError("RENDER_INVALID", str(exc), "RENDER"))
            except BaseException:
                self._finish(binding, "UNKNOWN", tools, diagnostics,
                             error=RenderError("RENDER_INTERRUPTED", "Local render was interrupted; no verified completion", "RENDER"))
                raise
            return self._finish(binding, "SUCCEEDED", tools, diagnostics, output=output, thumbnail=thumbnail)

    def load(self, job_id: str) -> RenderManifest:
        ref = manifest_ref(job_id)
        try:
            binding = strict_json(_read(self.registry, f"render-jobs/{job_id}/request.json"))
            plan = validate_binding(binding)
            if binding["job_id"] != job_id:
                raise ValueError("Request job identity mismatch")
            if not self.registry.exists(ref):
                return RenderManifest.build(
                    binding, state="UNKNOWN", tools=self._tools(), diagnostics=None,
                    error=RenderError("RENDER_INTERRUPTED", "Durable request has no completed manifest; inspect before a new job", "REOPEN"),
                )
            receipt = RenderManifest.from_dict(strict_json(_read(self.registry, ref)))
            record = receipt.to_dict()
            if (record["job_id"] != job_id or record["request_digest"] != binding["record_sha256"]
                    or record["plan"] != binding["plan"] or record["encoder"] != binding["encoder"]
                    or record["created_at"] != binding["created_at"]):
                raise ValueError("Manifest is not bound to this immutable request")
            diag = record["diagnostics"]
            if diag is not None:
                raw = _read(self.registry, diag["ref"])
                if (sha256_bytes(raw), len(raw)) != (diag["sha256"], diag["size_bytes"]):
                    raise ValueError("Diagnostics checksum mismatch")
                strict_json(raw)
            if receipt.state == "SUCCEEDED":
                self._sources(plan)
                output, thumb = receipt.output, receipt.thumbnail
                for artifact in (output, thumb):
                    if hash_file(self.registry.safe_path(artifact["ref"])) != (artifact["sha256"], artifact["size_bytes"]):
                        raise RenderError("OUTPUT_CORRUPT", "Stored output SHA256/size mismatch", "REOPEN")
                tools = self._tools()
                if not tools["ffmpeg"] or not tools["ffprobe"]:
                    raise RenderError("ENVIRONMENT_TOOL_UNAVAILABLE", "Reopen verification requires local FFmpeg and ffprobe", "REOPEN")
                with tempfile.TemporaryDirectory(prefix=".render-reopen-", dir=self.registry.root) as temp:
                    observed = verify_mp4(self.registry.safe_path(output["ref"]), plan, tools,
                                          ToolRunner(Path(temp), self.timeout, []))
                if observed != output:
                    raise RenderError("OUTPUT_CORRUPT", "Reopened stream/duration evidence differs", "REOPEN")
                validate_media_file(self.registry.safe_path(thumb["ref"]), "image/png")
                if hash_file(self.registry.safe_path(thumb["ref"])) != (thumb["sha256"], thumb["size_bytes"]):
                    raise RenderError("OUTPUT_CORRUPT", "Thumbnail changed during verification", "REOPEN")
            return receipt
        except RenderError:
            raise
        except MediaDependencyError as exc:
            raise RenderError("ENVIRONMENT_MEDIA_UNAVAILABLE", str(exc), "REOPEN") from exc
        except (MediaOperationError, MediaValidationError, OSError, KeyError, ValueError, TypeError) as exc:
            raise RenderError("MANIFEST_INVALID", str(exc), "REOPEN") from exc
