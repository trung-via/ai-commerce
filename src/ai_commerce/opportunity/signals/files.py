"""Bounded local files; reject traversal, links, reparse points and device paths."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PureWindowsPath

from ..core.models import OpportunityError, require

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_ROW_BYTES = 64 * 1024
MAX_FIELD_CHARS = 4096
MAX_ROWS = 10000


def local_path(value: str | Path) -> Path:
    raw = str(value)
    windows = PureWindowsPath(raw)
    require(bool(raw) and "\x00" not in raw and not raw.startswith(("\\\\", "//")), "UNSAFE_PATH")
    require(".." not in Path(raw).parts and ".." not in windows.parts, "PATH_TRAVERSAL")
    require(not windows.drive or re.fullmatch(r"[A-Za-z]:", windows.drive) is not None, "UNSAFE_PATH")
    require(not windows.drive or bool(windows.root), "UNSAFE_PATH")
    for part in windows.parts:
        if part == windows.anchor:
            continue
        require(
            not part.endswith((" ", "."))
            and re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part) is None,
            "UNSAFE_PATH",
        )
    path = Path(value).absolute()
    require(all(":" not in part for part in path.parts[1:]), "UNSAFE_PATH")
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OpportunityError("FILE_IO_ERROR") from exc
        require(
            not stat.S_ISLNK(info.st_mode)
            and not (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            ),
            "PATH_LINK_NOT_ALLOWED",
        )
        if part != path:
            require(stat.S_ISDIR(info.st_mode), "PATH_PARENT_NOT_DIRECTORY")
    return path


def read_local(value: str | Path, *, limit: int = MAX_FILE_BYTES) -> bytes:
    path = local_path(value)
    fd = None
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), "SOURCE_NOT_REGULAR_FILE")
        require(0 < before.st_size <= limit, "SOURCE_EMPTY_OR_TOO_LARGE")
        actual = local_path(path).lstat()
        require((actual.st_dev, actual.st_ino) == (before.st_dev, before.st_ino), "SOURCE_CHANGED_DURING_READ")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        require(0 < len(raw) <= limit, "SOURCE_EMPTY_OR_TOO_LARGE")
        require(
            (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
            and len(raw) == before.st_size,
            "SOURCE_CHANGED_DURING_READ",
        )
        return raw
    except OpportunityError:
        raise
    except OSError as exc:
        raise OpportunityError("FILE_IO_ERROR") from exc
    finally:
        if fd is not None:
            os.close(fd)


def ledger_path(value: str | Path, *, create: bool) -> Path:
    path = local_path(value)
    try:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        local_path(path)
        if path.exists():
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "LEDGER_NOT_REGULAR_FILE")
        else:
            require(create, "LEDGER_NOT_FOUND")
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = local_path(str(path) + suffix)
            if sidecar.exists():
                info = sidecar.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "LEDGER_SIDECAR_UNSAFE")
        return path
    except OpportunityError:
        raise
    except OSError as exc:
        raise OpportunityError("FILE_IO_ERROR") from exc
