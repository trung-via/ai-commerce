"""Bounded file capture and explicitly detected real media verification.

PNG/AVI retain V1's container checks. JPEG/WebP require Pillow decoding; MP4
requires ffprobe inspection and FFmpeg error-strict decoding. No downloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import tempfile
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from .base import MediaDependencyError, MediaValidationError
from .local import MIME_KINDS, validate_media

EXTENDED_MIME_KINDS = {"image/jpeg": "image", "image/webp": "image", "video/mp4": "video"}
SUPPORTED_MIME_KINDS = {**MIME_KINDS, **EXTENDED_MIME_KINDS}
CHUNK_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 128 * CHUNK_BYTES
MAX_DECODED_BYTES = 128 * CHUNK_BYTES
MAX_TOOL_OUTPUT = CHUNK_BYTES


def fingerprint(info: os.stat_result) -> tuple[int, ...]:
    # Windows Python may expose different pathname/handle ctime after rename.
    # Compare ctime only between fstat calls on the same open handle below.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def inspect_regular(path: Path) -> os.stat_result:
    """Reject source links before resolving/opening potentially special files."""
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise MediaValidationError(f"Linked/reparse media path: {part}")
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise MediaValidationError("Non-directory media ancestor")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MediaValidationError("Media must be an unlinked regular file")
    return info


@contextmanager
def open_regular(path: Path) -> Iterator[BinaryIO]:
    path = Path(os.path.abspath(path))
    if any(":" in part for part in path.parts[1:]):
        raise MediaValidationError("Alternate data stream media paths are forbidden")
    try:
        before = inspect_regular(path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if fingerprint(before) != fingerprint(opened) or opened.st_nlink != 1:
                raise MediaValidationError("Media changed before capture")
            yield stream
            after = os.fstat(stream.fileno())
            if (fingerprint(opened) != fingerprint(after) or opened.st_ctime_ns != after.st_ctime_ns
                    or after.st_nlink != 1
                    or fingerprint(after) != fingerprint(inspect_regular(path))):
                raise MediaValidationError("Media changed during capture/verification")
    except OSError as exc:
        raise MediaValidationError(f"Cannot access local media: {exc}") from exc


def copy_and_hash(source: BinaryIO, target: BinaryIO | None = None) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    while chunk := source.read(CHUNK_BYTES):
        digest.update(chunk)
        size += len(chunk)
        if target is not None and target.write(chunk) != len(chunk):
            raise OSError("Short write during media capture")
    return digest.hexdigest(), size


def hash_file(path: Path) -> tuple[str, int]:
    with open_regular(path) as stream:
        return copy_and_hash(stream)


@dataclass(frozen=True)
class FileCapture:
    path: Path
    sha256: str
    size_bytes: int
    source_ref: str
    requested_ref: str


@dataclass(frozen=True)
class MediaVerification:
    mime_type: str
    kind: str
    method: str


def _pillow():
    try:
        from PIL import Image, ImageFile, features
        return Image, ImageFile, features
    except (ImportError, OSError) as exc:
        raise MediaDependencyError(f"JPEG/WebP require a working Pillow decoder: {exc}") from exc


def _decode_image(stream: BinaryIO, mime_type: str, size: int) -> None:
    if size > MAX_IMAGE_BYTES:
        raise MediaValidationError("Image exceeds the 128 MiB encoded-input limit")
    expected = {"image/jpeg": "JPEG", "image/webp": "WEBP"}[mime_type]
    if mime_type == "image/jpeg":
        stream.seek(-2, os.SEEK_END)
        if stream.read(2) != b"\xff\xd9":
            raise MediaValidationError("Truncated JPEG: EOI missing")
    else:
        header = stream.read(12)
        if (len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP"
                or struct.unpack_from("<I", header, 4)[0] + 8 != size):
            raise MediaValidationError("Invalid/truncated WebP RIFF container")
    stream.seek(0)
    Image, ImageFile, features = _pillow()
    if mime_type == "image/webp" and not features.check("webp"):
        raise MediaDependencyError("WebP requires Pillow built with libwebp")
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise MediaDependencyError("Strict image verification requires Pillow LOAD_TRUNCATED_IMAGES=False")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(stream)
            if image.format != expected:
                raise MediaValidationError(f"Bytes do not match {mime_type}")
            image.verify()
            stream.seek(0)
            image = Image.open(stream)
            frames, decoded = getattr(image, "n_frames", 1), 0
            if not 0 < frames <= 1024:
                raise MediaValidationError("Image frame limit exceeded")
            for index in range(frames):
                image.seek(index)
                decoded += image.width * image.height * 4
                if decoded > MAX_DECODED_BYTES:
                    raise MediaValidationError("Image exceeds the 128 MiB decoded-input limit")
                image.load()  # verify() alone does not decode JPEG pixels.
    except MediaValidationError:
        raise
    except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise MediaValidationError(f"Cannot decode {mime_type}: {exc}") from exc


def _mp4_boxes(stream: BinaryIO, size: int) -> None:
    """Bounded structural check supplements, never replaces, the real decoder."""
    found, offset, count = set(), 0, 0
    while offset < size:
        stream.seek(offset)
        header = stream.read(8)
        if len(header) != 8:
            raise MediaValidationError("Truncated MP4 box header")
        length, name = struct.unpack(">I4s", header)
        minimum = 8
        if length == 1:
            raw = stream.read(8)
            if len(raw) != 8:
                raise MediaValidationError("Truncated MP4 extended box header")
            length, minimum = struct.unpack(">Q", raw)[0], 16
        elif length == 0:
            length = size - offset
        if length < minimum or offset + length > size:
            raise MediaValidationError("Truncated/invalid MP4 box length")
        if name == b"ftyp":
            body_size = length - minimum
            if name in found or not 8 <= body_size <= 4096 or body_size % 4:
                raise MediaValidationError("Invalid MP4 file-type box")
            body = stream.read(body_size)
            brands = {body[:4], *(body[i:i + 4] for i in range(8, len(body), 4))}
            mp4_brands = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6",
                          b"mp41", b"mp42", b"avc1", b"M4V ", b"dash", b"cmfc",
                          b"cmfs", b"msdh", b"msix", b"MSNV", b"f4v "}
            if not brands & mp4_brands:
                raise MediaValidationError("File-type brands do not identify MP4 video")
        found.add(name)
        offset += length
        count += 1
        if count > 100000:
            raise MediaValidationError("MP4 top-level box limit exceeded")
    if not {b"ftyp", b"moov", b"mdat"} <= found:
        raise MediaValidationError("MP4 ftyp/moov/mdat boxes are required")


def _tool(command: list[str], directory: Path) -> bytes:
    # Disk spooling prevents an unbounded in-memory subprocess pipe.
    try:
        with tempfile.TemporaryFile(dir=directory) as output, tempfile.TemporaryFile(dir=directory) as errors:
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=errors, timeout=60, check=False)
            if output.tell() > MAX_TOOL_OUTPUT or errors.tell() > MAX_TOOL_OUTPUT:
                raise MediaValidationError("Media verifier output limit exceeded")
            output.seek(0)
            errors.seek(0)
            detail = errors.read(MAX_TOOL_OUTPUT)
            if completed.returncode & 0xFFFFFFFF >= 0xC0000000:
                raise MediaDependencyError(f"Media verifier failed to initialize: {completed.returncode}")
            if completed.returncode or detail.strip():
                raise MediaValidationError(f"Media verifier rejected input: {detail[:2048].decode('utf-8', 'replace')}")
            return output.read(MAX_TOOL_OUTPUT)
    except (FileNotFoundError, PermissionError) as exc:
        raise MediaDependencyError(f"Cannot execute media verifier {command[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaValidationError("Local media verification exceeded 60 seconds") from exc
    except OSError as exc:
        raise MediaDependencyError(f"Media verifier could not start: {exc}") from exc


def _decode_mp4(path: Path) -> None:
    ffprobe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if ffprobe is None or ffmpeg is None:
        raise MediaDependencyError("MP4 verification requires local ffprobe and FFmpeg on PATH")
    raw = _tool([ffprobe, "-v", "error", "-protocol_whitelist", "file", "-count_frames",
                 "-select_streams", "v:0", "-show_entries",
                 "format=format_name:stream=codec_type,width,height,nb_read_frames",
                 "-of", "json", "-enable_drefs", "0", "-i", str(path)], path.parent)
    try:
        data = json.loads(raw)
        streams = data["streams"]
        names = data["format"]["format_name"].split(",")
        if ("mp4" not in names or len(streams) != 1 or streams[0]["codec_type"] != "video"
                or int(streams[0]["width"]) <= 0 or int(streams[0]["height"]) <= 0
                or int(streams[0]["nb_read_frames"]) <= 0):
            raise ValueError("Expected an MP4 video with decoded frames")
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaValidationError(f"Invalid ffprobe video evidence: {exc}") from exc
    _tool([ffmpeg, "-v", "error", "-xerror", "-err_detect", "explode",
           "-protocol_whitelist", "file", "-enable_drefs", "0", "-i", str(path), "-map", "0:v:0",
           "-f", "null", "-"], path.parent)


def validate_media_file(path: Path, mime_type: str) -> MediaVerification:
    if mime_type not in SUPPORTED_MIME_KINDS:
        raise MediaValidationError(f"Unsupported MIME: {mime_type!r}")
    path = Path(os.path.abspath(path))
    with open_regular(path) as stream:
        size = os.fstat(stream.fileno()).st_size
        if size <= 0:
            raise MediaValidationError("Expected nonempty media file")
        if mime_type in MIME_KINDS:
            if size > MAX_IMAGE_BYTES:
                raise MediaValidationError("V1 file checks are limited to 128 MiB; use MP4 for large video")
            validate_media(stream.read(MAX_IMAGE_BYTES + 1), mime_type)
            method = "PNG_CONTAINER" if mime_type == "image/png" else "AVI_CONTAINER"
        elif mime_type.startswith("image/"):
            if size < 12:
                raise MediaValidationError("Truncated image file")
            _decode_image(stream, mime_type, size)
            method = "PILLOW_DECODE"
        else:
            _mp4_boxes(stream, size)
            _decode_mp4(path)
            method = "FFPROBE_AND_FFMPEG_DECODE"
    return MediaVerification(mime_type, SUPPORTED_MIME_KINDS[mime_type], method)
