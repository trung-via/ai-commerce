"""The renderer boundary contains data, never C03 classes or FFmpeg commands."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.operations.manifest import AssetJobManifest, strict_json

DIGEST = re.compile(r"[0-9a-f]{64}")
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
IMAGE_CODECS = {"image/png": "png", "image/jpeg": "mjpeg", "image/webp": "webp"}


class RenderError(RuntimeError):
    def __init__(self, code: str, detail: str, stage: str):
        super().__init__(detail)
        self.code, self.detail, self.stage = code, detail, stage
        self.diagnostics: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail, "stage": self.stage}


def digest(value: str) -> None:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError("Expected a lowercase SHA256 digest")


def fields(record: dict, expected: set[str]) -> None:
    if not isinstance(record, dict) or set(record) != expected:
        raise ValueError("Unexpected record fields")


@dataclass(frozen=True)
class Canvas:
    width: int
    height: int
    fps: int

    def __post_init__(self) -> None:
        if any(type(x) is not int for x in (self.width, self.height, self.fps)):
            raise ValueError("Canvas dimensions and fps must be integers")
        if not (2 <= self.width <= 8192 and 2 <= self.height <= 8192):
            raise ValueError("Canvas dimensions must be between 2 and 8192")
        if self.width % 2 or self.height % 2:
            raise ValueError("H264 yuv420p requires even canvas dimensions")
        if not 1 <= self.fps <= 240:
            raise ValueError("fps must be between 1 and 240")

    def to_dict(self) -> dict:
        return {"width": self.width, "height": self.height, "fps": self.fps}

    @classmethod
    def from_dict(cls, record: dict) -> Canvas:
        fields(record, {"width", "height", "fps"})
        return cls(**record)


@dataclass(frozen=True)
class SourceMediaRef:
    """A frozen A04 artifact snapshot, optionally bound to its saved V1 manifest.

    Construction validates the contract. Every render/reopen also verifies the
    stored file through A04 v1.1; labels cannot establish actual media validity.
    """
    _artifact_json: bytes = field(repr=False)
    manifest_ref: str | None = None
    manifest_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self._artifact_json) is not bytes:
            raise ValueError("Source snapshot must be immutable bytes")
        artifact = strict_json(self._artifact_json)
        validate_record("MediaArtifact", artifact)
        if artifact["kind"] != "image" or artifact["mime_type"] not in IMAGE_CODECS:
            raise ValueError("Only PNG/JPEG/WebP still sources are supported")
        if ((self.manifest_ref is None) != (self.manifest_digest is None)):
            raise ValueError("Source manifest ref and digest must be supplied together")
        if self.manifest_ref is not None:
            if not isinstance(self.manifest_ref, str) or not self.manifest_ref:
                raise ValueError("Invalid source manifest ref")
            digest(self.manifest_digest)

    @property
    def artifact(self) -> dict[str, Any]:
        return strict_json(self._artifact_json)

    @classmethod
    def from_artifact(cls, artifact: dict) -> SourceMediaRef:
        return cls(canonical_bytes(artifact))

    @classmethod
    def from_manifest(cls, manifest: AssetJobManifest, *, manifest_ref: str) -> SourceMediaRef:
        checked = AssetJobManifest.from_dict(manifest.to_dict())
        if checked.state != "SUCCEEDED":
            raise ValueError("Source manifest must have a successful observed artifact")
        return cls(canonical_bytes(checked.result["artifact"]), manifest_ref,
                   checked.to_dict()["record_sha256"])

    def to_dict(self) -> dict:
        return {"artifact": self.artifact, "manifest_ref": self.manifest_ref,
                "manifest_digest": self.manifest_digest}

    @classmethod
    def from_dict(cls, record: dict) -> SourceMediaRef:
        fields(record, {"artifact", "manifest_ref", "manifest_digest"})
        return cls(canonical_bytes(record["artifact"]), record["manifest_ref"],
                   record["manifest_digest"])


@dataclass(frozen=True)
class RenderItem:
    source: SourceMediaRef
    start_frame: int
    duration_frames: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceMediaRef):
            raise ValueError("Expected a validated SourceMediaRef")
        if (type(self.start_frame) is not int or self.start_frame < 0
                or type(self.duration_frames) is not int or self.duration_frames <= 0):
            raise ValueError("Item start/duration must be nonnegative/positive integer frames")

    def to_dict(self) -> dict:
        return {"source": self.source.to_dict(), "start_frame": self.start_frame,
                "duration_frames": self.duration_frames}

    @classmethod
    def from_dict(cls, record: dict) -> RenderItem:
        fields(record, {"source", "start_frame", "duration_frames"})
        return cls(SourceMediaRef.from_dict(record["source"]), record["start_frame"],
                   record["duration_frames"])


@dataclass(frozen=True)
class RenderPlan:
    """C06 v1 supports a contiguous ordered still track, plus optional silence.

    Gaps, overlaps, video, text and transitions fail explicitly. A future fan-in
    maps C03 into this contract; no C03 serialization is assumed here.
    """
    canvas: Canvas
    items: tuple[RenderItem, ...]
    silence: bool = False
    composition_ref: str | None = None
    composition_sha256: str | None = None
    composition_revision: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.canvas, Canvas) or type(self.items) is not tuple:
            raise ValueError("Expected Canvas and immutable ordered items")
        if not self.items:
            raise ValueError("ASSET_REQUIRED: supply at least one validated still")
        if type(self.silence) is not bool:
            raise ValueError("silence must be boolean")
        end = 0
        for item in self.items:
            if not isinstance(item, RenderItem) or item.start_frame != end:
                raise ValueError("Still items must be ordered and contiguous from frame zero")
            end += item.duration_frames
        lineage = (self.composition_ref, self.composition_sha256, self.composition_revision)
        if any(x is not None for x in lineage):
            if (not isinstance(self.composition_ref, str) or not self.composition_ref.strip()
                    or type(self.composition_revision) is not int or self.composition_revision < 1):
                raise ValueError("Composition lineage needs ref, SHA256 and positive revision")
            digest(self.composition_sha256)

    @property
    def duration_frames(self) -> int:
        return sum(x.duration_frames for x in self.items)

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    def to_dict(self) -> dict:
        return {"contract_version": 1, "canvas": self.canvas.to_dict(),
                "items": [x.to_dict() for x in self.items], "silence": self.silence,
                "composition_ref": self.composition_ref,
                "composition_sha256": self.composition_sha256,
                "composition_revision": self.composition_revision}

    @classmethod
    def from_dict(cls, record: dict) -> RenderPlan:
        fields(record, {"contract_version", "canvas", "items", "silence", "composition_ref",
                        "composition_sha256", "composition_revision"})
        if type(record["contract_version"]) is not int or record["contract_version"] != 1:
            raise ValueError("Unsupported renderer plan version")
        if not isinstance(record["items"], list):
            raise ValueError("Expected ordered items")
        return cls(Canvas.from_dict(record["canvas"]),
                   tuple(RenderItem.from_dict(x) for x in record["items"]), record["silence"],
                   record["composition_ref"], record["composition_sha256"],
                   record["composition_revision"])


@dataclass(frozen=True)
class EncoderConfig:
    encoder: str = "libx264"
    crf: int = 23
    preset: str = "medium"

    def __post_init__(self) -> None:
        if self.encoder != "libx264":
            raise ValueError("This local adapter supports H264 through libx264 only")
        if type(self.crf) is not int or not 0 <= self.crf <= 51:
            raise ValueError("CRF must be an integer between 0 and 51")
        if self.preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast",
                               "medium", "slow", "slower", "veryslow"}:
            raise ValueError("Unsupported libx264 preset")

    def to_dict(self) -> dict:
        return {"encoder": self.encoder, "crf": self.crf, "preset": self.preset,
                "codec": "h264", "pixel_format": "yuv420p", "container": "mp4",
                "scale": "fit-pad-black", "threads": 1,
                "audio": {"encoder": "aac", "sample_rate": 48000, "channels": 2}}

    @classmethod
    def from_dict(cls, record: dict) -> EncoderConfig:
        obj = cls(record["encoder"], record["crf"], record["preset"])
        if obj.to_dict() != record:
            raise ValueError("Invalid encoder configuration")
        return obj
