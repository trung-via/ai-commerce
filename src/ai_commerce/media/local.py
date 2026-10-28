"""Offline imports and an explicitly synthetic mock; format checks, no rendering."""

from __future__ import annotations

import struct
import zlib

from .base import (
    Cost, MediaCapabilities, MediaInput, MediaOutput, MediaValidationError,
)

MIME_KINDS = {"image/png": "image", "video/x-msvideo": "video"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def validate_media(content: bytes, mime_type: str) -> str:
    """Check supported container structure, not full decoding or codec support."""
    if mime_type not in MIME_KINDS:
        raise MediaValidationError(f"Unsupported MIME: {mime_type!r}; expected PNG or AVI")
    if not isinstance(content, bytes) or not content:
        raise MediaValidationError("Expected nonempty media bytes")
    try:
        if mime_type == "image/png":
            _check_png(content)
        else:
            _check_avi(content)
    except (ValueError, struct.error, zlib.error) as exc:
        raise MediaValidationError(f"Bytes do not match {mime_type}: {exc}") from exc
    return MIME_KINDS[mime_type]


def _check_png(data: bytes) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("PNG signature missing")
    pos, chunks, compressed, palettes = 8, [], [], []
    while pos < len(data):
        size = struct.unpack_from(">I", data, pos)[0]
        end = pos + 12 + size
        if end > len(data):
            raise ValueError("Truncated PNG chunk")
        name, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + size]
        crc = struct.unpack_from(">I", data, pos + 8 + size)[0]
        if zlib.crc32(name + body) & 0xffffffff != crc:
            raise ValueError("PNG CRC mismatch")
        chunks.append(name)
        if name == b"IHDR":
            if len(chunks) != 1 or size != 13:
                raise ValueError("Invalid PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if (not width or not height or depth not in depths.get(color, set())
                    or compression or filtering or interlace != 0):
                raise ValueError("Invalid PNG dimensions/encoding")
        if name == b"PLTE":
            palettes.append(body)
        if name == b"IDAT":
            compressed.append(body)
        if name == b"IEND" and (size != 0 or end != len(data)):
            raise ValueError("Invalid PNG end")
        pos = end
    if not chunks or chunks[0] != b"IHDR" or chunks[-1] != b"IEND" or b"IDAT" not in chunks:
        raise ValueError("Incomplete PNG")
    if (len(palettes) > 1 or (color == 3 and not palettes)
            or (palettes and (not 0 < len(palettes[0]) <= 768 or len(palettes[0]) % 3
                              or color in {0, 4} or chunks.index(b"PLTE") > chunks.index(b"IDAT")
                              or (color == 3 and len(palettes[0]) // 3 > 1 << depth)))):
        raise ValueError("Invalid or missing PNG palette")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    row_size = (width * channels * depth + 7) // 8 + 1
    expected = row_size * height
    if expected > 128 * 1024 * 1024:
        raise ValueError("PNG exceeds the 128 MiB decoded-input limit")
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(b"".join(compressed), expected + 1)
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or len(pixels) != expected or any(pixels[i] > 4 for i in range(0, expected, row_size))):
        raise ValueError("Invalid PNG compressed pixels/scanlines")


def _check_avi(data: bytes) -> None:
    if (len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"AVI "
            or struct.unpack_from("<I", data, 4)[0] + 8 != len(data)):
        raise ValueError("Invalid AVI RIFF header")
    lists, headers, video_stream, frame = set(), [], False, False

    def walk(start: int, end: int, depth: int) -> None:
        nonlocal video_stream, frame
        if depth > 4:
            raise ValueError("Excessive AVI nesting")
        pos = start
        while pos < end:
            if pos + 8 > end:
                raise ValueError("Truncated AVI chunk")
            name, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
            body, stop = pos + 8, pos + 8 + size
            if stop + (size & 1) > end:
                raise ValueError("Truncated AVI body")
            if name == b"LIST":
                if size < 4:
                    raise ValueError("Invalid AVI list")
                lists.add(data[body:body + 4])
                walk(body + 4, stop, depth + 1)
            elif name == b"avih":
                headers.append(data[body:stop])
            elif name == b"strh" and size >= 56:
                video_stream |= data[body:body + 4] == b"vids"
            elif name[:2].isdigit() and name[2:] in {b"db", b"dc"} and size:
                frame = True
            pos = stop + (size & 1)

    walk(12, len(data), 0)
    if not {b"hdrl", b"movi"} <= lists or len(headers) != 1 or not video_stream or not frame:
        raise ValueError("AVI video headers/frames missing")
    header = headers[0]
    if len(header) != 56 or not all(struct.unpack_from("<I", header, offset)[0] for offset in (16, 24, 32, 36)):
        raise ValueError("Invalid AVI frame/stream/dimension metadata")


class LocalImportAdapter:
    @property
    def capabilities(self) -> MediaCapabilities:
        return MediaCapabilities("local-import", "LOCAL_IMPORT", ("import",), tuple(MIME_KINDS))

    def produce(self, request: MediaInput) -> MediaOutput:
        if request.mode != "import" or request.content is None or request.source_ref is None:
            raise MediaValidationError("Import requires captured bytes and their true source")
        validate_media(request.content, request.mime_type)
        return MediaOutput(request.content, request.mime_type, Cost("NOT_APPLICABLE"))


def _chunk(name: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)


class MockMediaProvider:
    """A04-only synthetic pixel fixture. A02's text mock remains text-only."""

    @property
    def capabilities(self) -> MediaCapabilities:
        return MediaCapabilities("local-media-mock", "MOCK", ("mock",), ("image/png",))

    def produce(self, request: MediaInput) -> MediaOutput:
        if request.mode != "mock":
            raise MediaValidationError("Mock requires explicit mock mode")
        content = (PNG_SIGNATURE
                   + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                   + _chunk(b"tEXt", b"Comment\x00AI Commerce MOCK fixture; no model generation")
                   + _chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\xff\xff"))
                   + _chunk(b"IEND", b""))
        return MediaOutput(content, "image/png", Cost("NOT_APPLICABLE"))
