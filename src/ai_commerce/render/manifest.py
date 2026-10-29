"""An immutable render receipt, separate from A04 import/generation manifests."""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.media.provenance import MediaProvenance
from ai_commerce.operations.manifest import strict_json

from .models import EncoderConfig, JOB_ID, RenderError, RenderPlan, digest, fields

RENDERER = "local-ffmpeg-v1"


def manifest_ref(job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid render job ID")
    return f"render-jobs/{job_id}/manifest.json"


def checksum(record: dict) -> dict:
    return {**record, "record_sha256": sha256_bytes(canonical_bytes(record))}


def validate_binding(record: dict) -> RenderPlan:
    fields(record, {"request_version", "job_id", "created_at", "plan", "encoder", "record_sha256"})
    manifest_ref(record["job_id"])
    if type(record["request_version"]) is not int or record["request_version"] != 1:
        raise ValueError("Invalid render request version")
    payload = {k: v for k, v in record.items() if k != "record_sha256"}
    if record["record_sha256"] != sha256_bytes(canonical_bytes(payload)):
        raise ValueError("Render request checksum mismatch")
    if not isinstance(record["created_at"], str) or not record["created_at"]:
        raise ValueError("Missing creation time")
    EncoderConfig.from_dict(record["encoder"])
    return RenderPlan.from_dict(record["plan"])


def file_ref(record: dict, mime: str) -> None:
    fields(record, {"ref", "sha256", "size_bytes", "mime_type"} |
           ({"verification"} if mime == "video/mp4" else set()))
    digest(record["sha256"])
    if (record["mime_type"] != mime or record["ref"] != "objects/" + record["sha256"]
            or type(record["size_bytes"]) is not int or record["size_bytes"] <= 0):
        raise ValueError("Invalid content-addressed output ref")


@dataclass(frozen=True)
class RenderManifest:
    _json: bytes = field(repr=False)

    def __post_init__(self) -> None:
        try:
            if type(self._json) is not bytes:
                raise ValueError("Expected immutable manifest bytes")
            self._validate(strict_json(self._json))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise RenderError("MANIFEST_INVALID", str(exc), "REOPEN") from exc

    def to_dict(self) -> dict:
        return strict_json(self._json)

    @property
    def job_id(self) -> str:
        return self.to_dict()["job_id"]

    @property
    def state(self) -> str:
        return self.to_dict()["state"]

    @property
    def error(self) -> dict | None:
        return self.to_dict()["error"]

    @property
    def output(self) -> dict | None:
        return self.to_dict()["output"]

    @property
    def thumbnail(self) -> dict | None:
        return self.to_dict()["thumbnail"]

    @property
    def plan(self) -> RenderPlan:
        return RenderPlan.from_dict(self.to_dict()["plan"])

    @classmethod
    def from_dict(cls, record: dict) -> RenderManifest:
        return cls(canonical_bytes(record))

    @classmethod
    def build(cls, binding: dict, *, state: str, tools: dict, diagnostics: dict | None,
              output: dict | None = None, thumbnail: dict | None = None,
              error: RenderError | None = None) -> RenderManifest:
        plan = validate_binding(binding)
        provenance = MediaProvenance()
        if state == "SUCCEEDED":
            provenance = MediaProvenance.derived_render(
                observed_sha256=output["sha256"], receipt_output_sha256=output["sha256"],
                input_sha256s=tuple(x.source.artifact["sha256"] for x in plan.items),
                renderer_ref=RENDERER + ":" + tools["ffmpeg_version"],
                receipt_ref=manifest_ref(binding["job_id"]),
            )
        return cls.from_dict(checksum({
            "manifest_version": 1, "renderer": RENDERER, "job_id": binding["job_id"],
            "created_at": binding["created_at"], "request_digest": binding["record_sha256"],
            "input_digest": plan.digest, "plan": plan.to_dict(), "encoder": binding["encoder"],
            "source_sha256s": [x.source.artifact["sha256"] for x in plan.items],
            "state": state, "tools": tools, "diagnostics": diagnostics,
            "output": output, "thumbnail": thumbnail, "error": error.to_dict() if error else None,
            "provenance": provenance.to_dict(), "generation_performed": False,
            "publication_status": "NOT_ATTEMPTED", "semantic_creative_realization": "UNKNOWN",
            "cost": {"status": "NOT_APPLICABLE", "amount": None},
        }))

    @staticmethod
    def _validate(record: dict) -> None:
        fields(record, {"manifest_version", "renderer", "job_id", "created_at", "request_digest",
                        "input_digest", "plan", "encoder", "source_sha256s", "state", "tools",
                        "diagnostics", "output", "thumbnail", "error", "provenance",
                        "generation_performed", "publication_status", "semantic_creative_realization",
                        "cost", "record_sha256"})
        if type(record["manifest_version"]) is not int or record["manifest_version"] != 1 or record["renderer"] != RENDERER:
            raise ValueError("Invalid render manifest version/renderer")
        ref = manifest_ref(record["job_id"])
        payload = {k: v for k, v in record.items() if k != "record_sha256"}
        if record["record_sha256"] != sha256_bytes(canonical_bytes(payload)):
            raise ValueError("Render manifest checksum mismatch")
        digest(record["request_digest"])
        plan = RenderPlan.from_dict(record["plan"])
        EncoderConfig.from_dict(record["encoder"])
        if (record["input_digest"] != plan.digest
                or record["source_sha256s"] != [x.source.artifact["sha256"] for x in plan.items]):
            raise ValueError("Input/source digest mismatch")
        tools = record["tools"]
        fields(tools, {"ffmpeg", "ffprobe", "ffmpeg_version", "ffprobe_version", "libx264", "aac", "decoders"})
        for name in ("ffmpeg", "ffprobe", "ffmpeg_version", "ffprobe_version"):
            if tools[name] is not None and (not isinstance(tools[name], str) or not tools[name]):
                raise ValueError("Invalid tool identity")
        if not isinstance(tools["decoders"], list) or any(not isinstance(x, str) for x in tools["decoders"]):
            raise ValueError("Invalid decoder capabilities")
        if type(tools["libx264"]) is not bool or type(tools["aac"]) is not bool:
            raise ValueError("Invalid codec capability")
        diag = record["diagnostics"]
        if diag is not None:
            fields(diag, {"ref", "sha256", "size_bytes"})
            digest(diag["sha256"])
            if diag["ref"] != f"render-jobs/{record['job_id']}/diagnostics.json" or type(diag["size_bytes"]) is not int or diag["size_bytes"] <= 0:
                raise ValueError("Invalid diagnostics ref")
        if (record["generation_performed"] is not False
                or record["publication_status"] != "NOT_ATTEMPTED"
                or record["semantic_creative_realization"] != "UNKNOWN"
                or record["cost"] != {"status": "NOT_APPLICABLE", "amount": None}):
            raise ValueError("Unsupported generation/publication/cost claim")
        if record["state"] == "SUCCEEDED":
            if record["error"] is not None or diag is None or not all(tools[x] for x in ("ffmpeg", "ffprobe", "ffmpeg_version", "ffprobe_version", "libx264")):
                raise ValueError("Success requires tool/codec/diagnostic evidence")
            if plan.silence and not tools["aac"]:
                raise ValueError("Silent output requires AAC capability evidence")
            if "h264" not in tools["decoders"]:
                raise ValueError("Success requires H264 decoder evidence")
            file_ref(record["output"], "video/mp4")
            file_ref(record["thumbnail"], "image/png")
            v = record["output"]["verification"]
            fields(v, {"codec", "pixel_format", "width", "height", "fps", "frame_count",
                       "duration", "container_duration", "audio"})
            if (v["codec"] != "h264" or v["pixel_format"] != "yuv420p"
                    or (v["width"], v["height"], v["fps"], v["frame_count"]) !=
                    (plan.canvas.width, plan.canvas.height, plan.canvas.fps, plan.duration_frames)
                    or (v["audio"] is not None) != plan.silence):
                raise ValueError("Output evidence does not match plan")
            expected = MediaProvenance.derived_render(
                observed_sha256=record["output"]["sha256"],
                receipt_output_sha256=record["output"]["sha256"],
                input_sha256s=tuple(record["source_sha256s"]),
                renderer_ref=RENDERER + ":" + tools["ffmpeg_version"], receipt_ref=ref,
            ).to_dict()
            if record["provenance"] != expected:
                raise ValueError("Render provenance mismatch")
        elif record["state"] in {"FAILED", "UNKNOWN"}:
            fields(record["error"], {"code", "detail", "stage"})
            if not all(isinstance(x, str) and x for x in record["error"].values()):
                raise ValueError("Missing failure reason")
            if record["output"] is not None or record["thumbnail"] is not None or record["provenance"] != MediaProvenance().to_dict():
                raise ValueError("Incomplete render cannot claim an output")
            if diag is None and (record["state"] != "UNKNOWN" or record["error"]["code"] != "RENDER_INTERRUPTED"):
                raise ValueError("Missing diagnostics")
        else:
            raise ValueError("Invalid render state")
