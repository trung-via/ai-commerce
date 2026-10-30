"""Bounded private JSON transactions; locks survive process crashes as OS locks.

The containing directory is provisioned by the trusted local operator. Never
unlink the lock file, reclaim a lock by age, or follow links/reparse points.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import stat
import time

from .conversation_binding import BindingError, _decode_unique


class PrivateStateError(ValueError):
    pass


def _linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _regular(info) -> bool:
    return stat.S_ISREG(info.st_mode) and not _linked(info) and info.st_nlink == 1


def _windows_handle(path: Path, flags: int, *, directory: bool = False):
    """Open the reparse point itself; deny rename/delete while the handle lives."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    access = 0x80 if directory else 0x80000000
    if not directory and flags & (os.O_WRONLY | os.O_RDWR):
        access |= 0x40000000
    creation = 1 if flags & os.O_EXCL else 4 if flags & os.O_CREAT else 3
    handle = create(str(path), access, 3, None, creation,
                    0x00200000 | (0x02000000 if directory else 0), None)
    if handle == wintypes.HANDLE(-1).value:
        code = ctypes.get_last_error()
        if code in (2, 3):
            raise FileNotFoundError
        raise OSError(code, "PRIVATE_STATE_OPEN_FAILED")

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    info = AttributeTag()
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    query.restype = wintypes.BOOL
    if not query(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or info.attributes & 0x400:
        close(handle)
        raise PrivateStateError("PATH_UNSAFE")
    return handle, close


class _Transaction:
    def __init__(self, state, directory_fd=None):
        self.state = state
        self.directory_fd = directory_fd

    def open(self, name: str, flags: int) -> int:
        if os.name == "nt":
            import msvcrt
            handle, close = _windows_handle(self.state.path.parent / name, flags)
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if flags &
                                           (os.O_WRONLY | os.O_RDWR) else os.O_RDONLY))
            except BaseException:
                close(handle)
                raise
        else:
            fd = os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=self.directory_fd)
        if not _regular(os.fstat(fd)):
            os.close(fd)
            raise PrivateStateError("PATH_UNSAFE")
        return fd

    def read(self, default: dict) -> dict:
        try:
            fd = self.open(self.state.path.name, os.O_RDONLY)
        except FileNotFoundError:
            return default
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(self.state.max_bytes + 1)
        if len(raw) > self.state.max_bytes:
            raise PrivateStateError("TOO_LARGE")
        try:
            return _decode_unique(raw.decode("utf-8"))
        except BindingError:
            raise PrivateStateError("DUPLICATE_KEY") from None
        except (UnicodeError, ValueError, RecursionError):
            raise PrivateStateError("INVALID") from None

    def write(self, data: dict) -> None:
        raw = json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(raw) > self.state.max_bytes:
            raise PrivateStateError("TOO_LARGE")
        # Validate an existing destination without following it. Replacing it
        # later cannot redirect a write through a final-component symlink.
        try:
            fd = self.open(self.state.path.name, os.O_RDONLY)
        except FileNotFoundError:
            pass
        else:
            os.close(fd)
        temporary = self.state.path.name + "." + secrets.token_hex(16) + ".part"
        fd = self.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name == "nt":
                os.replace(self.state.path.parent / temporary, self.state.path)
            else:
                os.replace(temporary, self.state.path.name, src_dir_fd=self.directory_fd,
                           dst_dir_fd=self.directory_fd)
                os.fsync(self.directory_fd)
        finally:
            try:
                if os.name == "nt":
                    os.unlink(self.state.path.parent / temporary)
                else:
                    os.unlink(temporary, dir_fd=self.directory_fd)
            except FileNotFoundError:
                pass


class PrivateJsonState:
    def __init__(self, path: Path, repository_root: Path, *, label: str, max_bytes: int = 131072):
        self.path, self.repository_root = Path(path), Path(repository_root)
        self.label, self.max_bytes = label, max_bytes
        self._check_location()

    def _check_location(self):
        try:
            if (not self.path.is_absolute() or not self.repository_root.is_absolute() or
                    ".." in self.path.parts or self.path.drive.startswith("\\\\") or
                    ":" in self.path.name or self.path.name.endswith((".", " "))):
                raise PrivateStateError("LOCATION_INVALID")
            for part in [*reversed(self.path.parents), self.path]:
                try:
                    info = part.lstat()
                except FileNotFoundError:
                    continue
                if _linked(info):
                    raise PrivateStateError("LOCATION_INVALID")
            resolved, root = self.path.resolve(), self.repository_root.resolve()
            if resolved == root or root in resolved.parents:
                raise PrivateStateError("LOCATION_INVALID")
            if self.path.exists() and not _regular(self.path.lstat()):
                raise PrivateStateError("LOCATION_INVALID")
        except (OSError, ValueError, RuntimeError):
            raise PrivateStateError(self.label + "_LOCATION_INVALID") from None

    @contextmanager
    def locked(self):
        """One OS lock covers the entire read/validate/modify/replace operation."""
        directories, lock_fd, unlock = [], None, None
        try:
            self._check_location()
            if os.name == "nt":
                for part in reversed(self.path.parents):
                    directories.append(_windows_handle(part, 0, directory=True))
                transaction = _Transaction(self)
            elif os.name == "posix" and hasattr(os, "O_NOFOLLOW"):
                directory_fd = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                directories.append(directory_fd)
                for part in self.path.parent.parts[1:]:
                    directory_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                           dir_fd=directory_fd)
                    directories.append(directory_fd)
                transaction = _Transaction(self, directory_fd)
            else:
                raise PrivateStateError("LOCKING_UNSUPPORTED")
            lock_fd = transaction.open(self.path.name + ".lock", os.O_RDWR | os.O_CREAT)
            deadline = time.monotonic() + 5
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(lock_fd, 0, os.SEEK_SET)
                        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                        unlock = lambda: msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        unlock = lambda: fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise PrivateStateError("BUSY") from None
                    time.sleep(0.01)
            yield transaction
        except PrivateStateError as exc:
            code = str(exc)
            raise PrivateStateError(code if code.startswith(self.label + "_") else
                                    self.label + "_" + code) from None
        except (OSError, RecursionError):
            raise PrivateStateError(self.label + "_UNAVAILABLE") from None
        finally:
            if unlock is not None:
                try:
                    unlock()
                except OSError:
                    pass  # Closing the descriptor still releases the OS lock.
            if lock_fd is not None:
                os.close(lock_fd)
            for handle in reversed(directories):
                if os.name == "nt":
                    handle[1](handle[0])
                else:
                    os.close(handle)
