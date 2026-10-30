"""Read bounded regular files within an explicit local root, never URLs."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PureWindowsPath

from .core import ProductError, require
from .schemas import MAX_RAW_BYTES


def _path(value) -> Path:
    require(isinstance(value, (str, Path)), "UNSAFE_PATH")
    raw = str(value)
    windows = PureWindowsPath(raw)
    require(bool(raw) and "\x00" not in raw and "://" not in raw
            and not raw.startswith(("\\\\", "//")), "UNSAFE_PATH")
    require(".." not in Path(raw).parts and ".." not in windows.parts, "PATH_TRAVERSAL")
    require(not windows.drive or (re.fullmatch(r"[A-Za-z]:", windows.drive) is not None
                                 and bool(windows.root)), "UNSAFE_PATH")
    for part in windows.parts:
        if part == windows.anchor:
            continue
        require(":" not in part and not part.endswith((" ", ".")) and re.fullmatch(
            r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part) is None,
            "UNSAFE_PATH")
    if os.name != "nt":
        require("\\" not in raw and not windows.drive, "UNSAFE_PATH")
    return Path(raw)


def _inspect(path: Path):
    """Check every existing ancestor, including root's own ancestors."""
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not (
            getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)),
            "PATH_LINK_NOT_ALLOWED")
        if part != path:
            require(stat.S_ISDIR(info.st_mode), "PATH_PARENT_NOT_DIRECTORY")
    return info


def _signature(info):
    # Windows lstat/fstat may give ctime different meanings (creation/change).
    # Compare these common fields across APIs; check ctime within each API below.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def read_local(path, *, root) -> bytes:
    """Reject traversal, symlinks/junctions, devices, growth and replacement.

    This is a local file boundary, not an OS sandbox against privileged actors.
    No file, directory, archive, sidecar or raw-evidence store is written.
    """
    root_path = _path(root).absolute()
    file_path = _path(path)
    file_path = file_path.absolute() if file_path.is_absolute() else root_path / file_path
    require(file_path.is_relative_to(root_path), "PATH_OUTSIDE_ROOT")
    fd = None
    try:
        require(stat.S_ISDIR(_inspect(root_path).st_mode), "SOURCE_ROOT_NOT_DIRECTORY")
        before = _inspect(file_path)
        require(stat.S_ISREG(before.st_mode), "SOURCE_NOT_REGULAR_FILE")
        require(0 < before.st_size <= MAX_RAW_BYTES, "SOURCE_EMPTY_OR_TOO_LARGE")
        fd = os.open(file_path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        opened = os.fstat(fd)
        require(stat.S_ISREG(opened.st_mode) and _signature(before) == _signature(opened),
                "SOURCE_CHANGED_DURING_READ")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(MAX_RAW_BYTES + 1)
            after = os.fstat(stream.fileno())
        require(0 < len(raw) <= MAX_RAW_BYTES, "SOURCE_EMPTY_OR_TOO_LARGE")
        final = _inspect(file_path)
        require(_signature(opened) == _signature(after) == _signature(final)
                and before.st_ctime_ns == final.st_ctime_ns
                and opened.st_ctime_ns == after.st_ctime_ns
                and len(raw) == opened.st_size, "SOURCE_CHANGED_DURING_READ")
        return raw
    except ProductError:
        raise
    except (OSError, ValueError) as exc:
        raise ProductError("SOURCE_READ_FAILED") from exc
    finally:
        if fd is not None:
            os.close(fd)
