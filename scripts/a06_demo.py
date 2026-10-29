"""A06 E01 reproducible offline demo: locally authored synthetic PNGs, not AI assets."""
import argparse
import json
import struct
import tempfile
import zlib
from pathlib import Path
from ai_commerce.e2e import run, verify


def png(red: int, blue: int) -> bytes:
    width = height = 32
    rows = b"".join(b"\x00" + b"".join(
        bytes((red, (x + y) % 255, blue)) for x in range(width))
        for y in range(height))
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(
            ">I", zlib.crc32(kind + body) & 0xffffffff)
    hdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", hdr)
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="e01-offline-fixtures-") as folder:
        root = Path(folder)
        first, second = root / "scene-1.png", root / "scene-2.png"
        first.write_bytes(png(70, 180))
        second.write_bytes(png(210, 40))
        result = run("Original two-scene abstract color study; local synthetic input, "
                     "no AI generation, no publication", [first, second],
                     args.output, width=64, height=96, fps=24, frames=12)
    checked = verify(args.output)
    if checked != result:
        raise RuntimeError("E01 proof did not reopen identically")
    print(json.dumps(checked, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
