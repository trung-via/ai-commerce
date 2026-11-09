"""Bounded typed filesystem adapter. Never invokes a shell or elevates a token.

Directory handles pin ancestors on Windows; POSIX uses directory-relative syscalls.
No traversal through links/reparse points; existing hardlink aliases are denied.
Hash preconditions are checked twice. The conditional mutation implementations
below are prototypes; Worker blocks their execution until atomicity is proven.
External writers are not transactional. No universal rollback is promised.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import time
import unicodedata

from .contract import MAX_CHUNK, blob, deny, digest

DENIED_PARTS = frozenset((".git .github .ssh .aws .azure .gnupg .codex .agents "
                          "windows system32 syswow64 programdata credentials cookies").split()) | {
    "program files", "program files (x86)", "login data", ".env", "secrets",
    "browser", "user data", "system volume information", "$recycle.bin"}


def contained(path: Path, root: Path):
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def canonical_path(raw, roots, allow_unc, forbidden=()):
    if type(raw) is not str or unicodedata.normalize("NFC", raw) != raw:
        deny("PATH_NON_CANONICAL")
    if raw.startswith(("\\\\?", "\\\\.", "//?", "//.")) or "\x00" in raw:
        deny("DEVICE_PATH_DENIED")
    p = Path(raw)
    if not p.is_absolute() or ".." in p.parts or "." in raw.replace("\\", "/").split("/"):
        deny("PATH_NON_CANONICAL")
    if raw.startswith(("\\\\", "//")) and not allow_unc:
        deny("UNC_DENIED")
    for part in p.parts[1:]:
        lower = part.casefold()
        if (lower in DENIED_PARTS or lower.startswith(".env.") or lower.endswith((".pem", ".pfx", ".key"))):
            deny("PROTECTED_PATH")
        if (":" in part or part.endswith((".", " ")) or any(c in part for c in '<>"|?*') or
                any(ord(c) < 32 for c in part) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part)):
            deny("DEVICE_PATH_DENIED")
    p = Path(os.path.abspath(p))
    if not any(contained(p, Path(os.path.abspath(r))) for r in roots):
        deny("PATH_OUTSIDE_LEASE")
    if any(contained(p, Path(os.path.abspath(r))) for r in forbidden):
        deny("PROTECTED_PATH")
    return p


def no_link(p):
    info = p.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        deny("REPARSE_DENIED")
    return info


def _win_handle(path, *, directory=False, write=False, read_control=False):
    # OPEN_REPARSE_POINT prevents following a replaced final link.
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    access = (0x00020080 if read_control else 0x80) if directory else 0x80000000 | (0x40000000 if write else 0)
    share = 3 if directory else 1  # directories cannot be renamed; files deny writers/deleters
    flags = 0x00200000 | (0x02000000 if directory else 0)
    handle = create(str(path), access, share, None, 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    # Attributes from the opened object, not a second path lookup.
    data = (wintypes.DWORD * 13)()
    kernel.GetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(data)) or data[0] & 0x400:
        kernel.CloseHandle(handle)
        deny("REPARSE_DENIED")
    if not directory and data[10] != 1:  # BY_HANDLE_FILE_INFORMATION.nNumberOfLinks
        kernel.CloseHandle(handle)
        deny("HARDLINK_DENIED")
    return handle


class PinnedPath:
    def __init__(self, path):
        self.path, self.handles, self.parent_fd = path, [], None

    def __enter__(self):
        try:
            if os.name == "nt":
                for p in reversed(self.path.parents):
                    self.handles.append(_win_handle(p, directory=True))
            else:
                fd = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY)
                self.handles.append(fd)
                for part in self.path.parts[1:-1]:
                    fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    self.handles.append(fd)
                self.parent_fd = fd
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        for h in reversed(self.handles):
            if os.name == "nt":
                close = ctypes.WinDLL("kernel32").CloseHandle
                close.argtypes = (ctypes.c_void_p,)
                close(h)
            else:
                os.close(h)
        self.handles.clear()

    def info(self):
        if os.name == "nt":
            return no_link(self.path)
        s = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(s.st_mode):
            deny("REPARSE_DENIED")
        return s

    def open(self, write=False, create=False):
        if os.name == "nt" and not create:
            import msvcrt
            handle = _win_handle(self.path, write=write)
            fd = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if write else os.O_RDONLY))
        else:
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL) if create else (os.O_RDWR if write else os.O_RDONLY)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(self.path if os.name == "nt" else self.path.name, flags, 0o600,
                         **({} if os.name == "nt" else {"dir_fd": self.parent_fd}))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            deny("SPECIAL_OR_HARDLINK_DENIED")
        return os.fdopen(fd, "r+b" if write or create else "rb")

    def sync_parent(self):
        if self.parent_fd is not None:
            os.fsync(self.parent_fd)


class Filesystem:
    def __init__(self, lease, state_dir, forbidden, checkpoint, *, fault=lambda _: None):
        self.lease, self.state = lease, Path(state_dir)
        self.forbidden, self.checkpoint, self.fault = forbidden, checkpoint, fault
        self.started = False

    def path(self, raw):
        return canonical_path(raw, self.lease["roots"], self.lease["allow_unc"], self.forbidden)

    def effect(self):
        self.checkpoint()
        self.fault("before_effect")
        self.started = True

    def hash_stream(self, f):
        f.seek(0)
        h, size = hashlib.sha256(), 0
        while True:
            self.checkpoint()
            chunk = f.read(MAX_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > self.lease["max_bytes"]:
                deny("FILE_LIMIT")
            h.update(chunk)
        return h.hexdigest(), size

    def hash(self, raw):
        p = self.path(raw)
        with PinnedPath(p) as pinned, pinned.open() as f:
            before = os.fstat(f.fileno())
            value, size = self.hash_stream(f)
            after = os.fstat(f.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                deny("FILE_CHANGED")
            return value, size

    def assert_hash(self, raw, expected):
        actual, size = self.hash(raw)
        if actual != expected:
            deny("PRECONDITION_FAILED")
        return size

    @staticmethod
    def cross_volume(source, destination):
        return source.info().st_dev != os.stat(destination.parent).st_dev

    def metadata(self, op, p, selection):
        if op == "volumes":
            # Only eligible roots: no probing an unleased UNC host or offline drive.
            volumes = []
            for root in self.lease["roots"]:
                self.checkpoint()
                path = self.path(root)
                with PinnedPath(path) as pin:
                    s = pin.info()
                    # Windows device IDs can exceed canonical JSON's safe integer range.
                    volumes.append({"root": str(path), "device": digest(str(s.st_dev).encode("ascii"))})
            selection(volumes)
            return {"volume_count": len({v["device"] for v in volumes}), "local_only": True}
        root = self.path(p["path"])
        with PinnedPath(root) as pinned:
            s = pinned.info()
            if op == "stat":
                return {"exists": True, "is_directory": stat.S_ISDIR(s.st_mode), "bytes": s.st_size}
            if not stat.S_ISDIR(s.st_mode):
                deny("NOT_DIRECTORY")
            # Pin the enumerated directory itself, not only its ancestors.
            child = root / "hostops-unused-leaf"
            with PinnedPath(child) as directory:
                entries, scanned, truncated = [], 0, False
                with os.scandir(root if os.name == "nt" else directory.parent_fd) as it:
                    for item in it:
                        self.checkpoint()
                        scanned += 1
                        if scanned > self.lease["max_entries"] or len(entries) >= p["limit"]:
                            truncated = True
                            break
                        # Do not follow links or return forbidden filenames, even locally.
                        if item.is_symlink():
                            continue
                        try:
                            self.path(str(root / item.name))
                        except Exception as exc:
                            from .contract import Denied
                            if isinstance(exc, Denied):
                                continue
                            raise
                        if op == "list" or p["needle"].casefold() in item.name.casefold():
                            entries.append({"name": item.name, "directory": item.is_dir(follow_symlinks=False)})
                selection(entries)
                return {"count": len(entries), "truncated": truncated, "local_only": True}

    def read(self, p, selection):
        root = self.path(p["path"])
        with PinnedPath(root) as pin, pin.open() as f:
            expected, size = self.hash_stream(f)
            if expected != p["expected_sha256"]:
                deny("PRECONDITION_FAILED")
            if p["offset"] > size:
                deny("CHUNK_RANGE")
            f.seek(p["offset"])
            content = f.read(p["length"])
            # Detect a concurrent writer, including writes during chunk extraction.
            if self.hash_stream(f) != (expected, size):
                deny("FILE_CHANGED")
        selected = selection(content)
        return {"selection_id": selected, "chunk_sha256": digest(content), "sha256": expected,
                "bytes": len(content), "offset": p["offset"], "total_bytes": size, "local_only": True}

    def _create(self, path, data):
        if len(data) > self.lease["max_bytes"]:
            deny("FILE_LIMIT")
        with PinnedPath(path) as pin:
            self.effect()
            with pin.open(create=True) as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            pin.sync_parent()
        self.fault("after_destination")
        if self.hash(str(path)) != (digest(data), len(data)):
            deny("POSTCONDITION_FAILED")
        return {"sha256": digest(data), "bytes": len(data)}

    def mutate(self, op, p):
        path = self.path(p["path"])
        if op == "create":
            # Existing objects are rejected before any side effect.
            if os.path.lexists(path):
                deny("ALREADY_EXISTS")
            return self._create(path, blob(p["data_b64"]))
        size = self.assert_hash(str(path), p["expected_sha256"])
        if op in ("append", "replace", "patch"):
            data = blob(p["data_b64"])
            with PinnedPath(path) as pin:
                if op == "append":
                    with pin.open(write=True) as f:
                        if self.hash_stream(f)[0] != p["expected_sha256"]:
                            deny("PRECONDITION_FAILED")
                        if size + len(data) > self.lease["max_bytes"]:
                            deny("FILE_LIMIT")
                        self.effect()
                        f.seek(0, os.SEEK_END)
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                        value, count = self.hash_stream(f)
                    return {"sha256": value, "bytes": count}
                if op == "patch":
                    with pin.open() as f:
                        old = f.read(self.lease["max_bytes"] + 1)
                    if digest(old) != p["expected_sha256"] or p["offset"] + p["remove_bytes"] > len(old):
                        deny("PRECONDITION_FAILED")
                    data = old[:p["offset"]] + data + old[p["offset"] + p["remove_bytes"]:]
                if len(data) > self.lease["max_bytes"]:
                    deny("FILE_LIMIT")
                # Staging is on the same filesystem as the destination.
                stage = path.with_name(".hostops-stage-" + os.urandom(16).hex())
                self.effect()
                try:
                    with PinnedPath(stage) as staged, staged.open(create=True) as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    self.fault("before_replace_check")
                    self.assert_hash(str(path), p["expected_sha256"])
                    self.checkpoint()
                    if os.name == "nt":
                        # ReplaceFile preserves the replaced file's security descriptor.
                        from ctypes import wintypes
                        replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
                        replace.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                            wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)
                        if not replace(str(path), str(stage), None, 0, None, None):
                            raise ctypes.WinError(ctypes.get_last_error())
                    else:
                        os.replace(stage.name, path.name, src_dir_fd=pin.parent_fd, dst_dir_fd=pin.parent_fd)
                    pin.sync_parent()
                finally:
                    if os.path.lexists(stage):
                        if os.name == "nt":
                            stage.unlink()
                        else:
                            os.unlink(stage.name, dir_fd=pin.parent_fd)
                self.fault("after_destination")
                if self.hash(str(path)) != (digest(data), len(data)):
                    deny("POSTCONDITION_FAILED")
                return {"sha256": digest(data), "bytes": len(data)}
        destination = self.path(p["destination"]) if "destination" in p else path.with_name(
            ".hostops-quarantine-" + os.urandom(16).hex())
        if os.path.lexists(destination):
            deny("ALREADY_EXISTS")
        with PinnedPath(path) as source, PinnedPath(destination) as dest:
            if op in ("copy", "move", "rename"):
                if op == "rename" and path.parent != destination.parent:
                    deny("RENAME_PARENT_MISMATCH")
                with source.open() as f:
                    data = f.read(self.lease["max_bytes"] + 1)
                if len(data) > self.lease["max_bytes"] or digest(data) != p["expected_sha256"]:
                    deny("PRECONDITION_FAILED")
                cross = self.cross_volume(source, destination)
                if op == "copy" or cross:
                    self._create(destination, data)
                    if self.hash(str(destination)) != (p["expected_sha256"], size):
                        deny("POSTCONDITION_FAILED")
                    if op != "copy":
                        self.assert_hash(str(path), p["expected_sha256"])
                        self.checkpoint()
                        self.fault("before_source_remove")
                        if os.name == "nt":
                            path.unlink()
                        else:
                            os.unlink(path.name, dir_fd=source.parent_fd)
                        source.sync_parent()
                    return {"sha256": p["expected_sha256"], "bytes": size, "cross_volume": cross}
            # Windows rename is no-clobber. POSIX link+unlink gives no-clobber
            # semantics without pretending the pair is an atomic move.
            self.fault("before_move_check")
            self.assert_hash(str(path), p["expected_sha256"])
            self.effect()
            if os.name == "nt":
                os.rename(path, destination)
            else:
                os.link(path.name, destination.name, src_dir_fd=source.parent_fd,
                        dst_dir_fd=dest.parent_fd, follow_symlinks=False)
                self.fault("before_source_remove")
                os.unlink(path.name, dir_fd=source.parent_fd)
            source.sync_parent()
            dest.sync_parent()
        self.fault("after_destination")
        if self.hash(str(destination)) != (p["expected_sha256"], size) or path.exists():
            deny("POSTCONDITION_FAILED")
        # Quarantined names and original paths stay local; no raw names in receipt.
        return {"sha256": p["expected_sha256"], "bytes": size, "cross_volume": False}


# W1.3: bounded Windows native create-new fixture. No CLI, Issue poller,
# self-hosted job, key enrollment or credential store invokes these functions.
# The external signer/root identity trust boundary is NOT provided by this code.
def _w1_valid_leaf(name):
    if type(name) is not str or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,55}\.(?:txt|md|json)", name):
        deny("W1_NATIVE_FILENAME_DENIED")
    stem = name.rsplit(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} | {
            "COM" + str(i) for i in range(1, 10)} | {
            "LPT" + str(i) for i in range(1, 10)}:
        deny("W1_NATIVE_FILENAME_DENIED")


def _w1_open_root(root, *, read_control=False):
    """Open an absolute local directory, never a junction or UNC path."""
    if os.name != "nt":
        deny("W1_NATIVE_WINDOWS_ONLY")
    p = Path(root)
    raw = str(root)
    if (not p.is_absolute() or raw.startswith(("\\\\", "//", "\\\\.\\", "\\\\?\\")) or
            ".." in p.parts or "." in p.parts):
        deny("W1_NATIVE_ROOT_DENIED")
    # Existing path policy forbids credential/browser/system directories.
    canonical_path(str(p / "w1-native-check.txt"), [p], False)
    try:
        for ancestor in (p, *p.parents):
            item = ancestor.lstat()
            if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
                deny("W1_NATIVE_ROOT_REPARSE")
        if not p.is_dir():
            deny("W1_NATIVE_ROOT_DENIED")
        handle = _win_handle(p, directory=True, read_control=read_control)
    except OSError:
        deny("W1_NATIVE_ROOT_DENIED")
    from ctypes import wintypes
    data = (wintypes.DWORD * 13)()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(data)):
        kernel.CloseHandle(handle)
        deny("W1_NATIVE_ROOT_DENIED")
    if data[0] & 0x400 or not data[0] & 0x10:
        kernel.CloseHandle(handle)
        deny("W1_NATIVE_ROOT_REPARSE")
    identity = {
        "volume_serial_hex": f"{data[7]:08x}",
        "file_index_hex": f"{((data[11] << 32) | data[12]):016x}",
    }
    return handle, identity


def w1_windows_root_identity(root):
    """Read-only candidate root identity; not independently trusted enrollment.

    A Human or isolated authority must pin this identity out-of-band. Simply
    calling this function from the untrusted public runner is not attestation.
    """
    handle, identity = _w1_open_root(root)
    kernel = ctypes.WinDLL("kernel32")
    from ctypes import wintypes
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle(handle)
    return identity


def w1_windows_native_create_fixture(root, expected_root_identity, filename, content):
    """Native root-HANDLE-relative create-new, fixture/operator-only primitive.

    The caller MUST separately authenticate an exact Human-signed operation,
    bind the private Issue/source, reserve an independent anti-replay intent
    and verify the approved root identity before use. There is deliberately
    no CLI/worker route or claim of remote signed-write capability.
    """
    _w1_valid_leaf(filename)
    if type(content) is not str or "\x00" in content:
        deny("W1_NATIVE_CONTENT_DENIED")
    try:
        payload = content.encode("utf-8", "strict")
    except UnicodeError:
        deny("W1_NATIVE_CONTENT_DENIED")
    if not 1 <= len(payload) <= 2048:
        deny("W1_NATIVE_CONTENT_DENIED")
    if (type(expected_root_identity) is not dict or
            set(expected_root_identity) != {"volume_serial_hex", "file_index_hex"} or
            any(type(v) is not str for v in expected_root_identity.values()) or
            not re.fullmatch(r"[0-9a-f]{8}", expected_root_identity["volume_serial_hex"]) or
            not re.fullmatch(r"[0-9a-f]{16}", expected_root_identity["file_index_hex"])):
        deny("W1_NATIVE_ROOT_UNPINNED")

    root_handle, actual_identity = _w1_open_root(root)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    from ctypes import wintypes
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    try:
        if actual_identity != expected_root_identity:
            deny("W1_NATIVE_ROOT_MISMATCH")

        class UnicodeString(ctypes.Structure):
            _fields_ = [("Length", wintypes.WORD),
                        ("MaximumLength", wintypes.WORD),
                        ("Buffer", wintypes.LPWSTR)]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [("Length", wintypes.DWORD),
                        ("RootDirectory", wintypes.HANDLE),
                        ("ObjectName", ctypes.POINTER(UnicodeString)),
                        ("Attributes", wintypes.DWORD),
                        ("SecurityDescriptor", ctypes.c_void_p),
                        ("SecurityQualityOfService", ctypes.c_void_p)]

        class IOStatusBlock(ctypes.Structure):
            _fields_ = [("StatusOrPointer", ctypes.c_void_p),
                        ("Information", ctypes.c_size_t)]

        text_buffer = ctypes.create_unicode_buffer(filename)
        name_length = len(filename.encode("utf-16-le"))
        nt_name = UnicodeString(name_length, name_length, ctypes.cast(text_buffer, wintypes.LPWSTR))
        attrs = ObjectAttributes(ctypes.sizeof(ObjectAttributes), root_handle,
                                 ctypes.pointer(nt_name), 0x40, None, None)
        status = IOStatusBlock()
        file_handle = wintypes.HANDLE()
        nt_create = ctypes.WinDLL("ntdll").NtCreateFile
        nt_create.restype = ctypes.c_long
        nt_create.argtypes = (
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
            ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IOStatusBlock),
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        )
        # FILE_CREATE, FILE_OPEN_REPARSE_POINT, synchronous non-directory.
        # RootDirectory references the pinned root handle, not an absolute path.
        rc = nt_create(ctypes.byref(file_handle), 0xC0100003, ctypes.byref(attrs),
                       ctypes.byref(status), None, 0x80, 0, 2,
                       0x00200060, None, 0)
        if rc != 0:
            if rc & 0xFFFFFFFF == 0xC0000035:
                deny("W1_NATIVE_ALREADY_EXISTS")
            deny("W1_NATIVE_CREATE_DENIED")
        import msvcrt
        try:
            fd = msvcrt.open_osfhandle(file_handle.value, os.O_BINARY | os.O_RDWR)
        except OSError:
            kernel.CloseHandle(file_handle)
            deny("W1_NATIVE_CREATE_DENIED")
        try:
            with os.fdopen(fd, "w+b") as created:
                created.write(payload)
                created.flush()
                os.fsync(created.fileno())
                created.seek(0)
                observed = created.read(2049)
        except OSError:
            deny("W1_NATIVE_EFFECT_UNKNOWN")
        if observed != payload:
            deny("W1_NATIVE_EFFECT_UNKNOWN")
        return {
            "schema": "hostops.personal.w1_native_fixture.v1",
            "status": "NATIVE_FIXTURE_CREATE_VERIFIED",
            "content_sha256": digest(payload),
            "content_bytes": len(payload),
            "real_host_attested": False,
            "remote_write_enabled": False,
            "credential_custody_proven": False,
        }
    finally:
        kernel.CloseHandle(root_handle)


# ---------------------------------------------------------------------------
# Windows Owned Fixture Root Pre-flight (non-production fixture only)
# ---------------------------------------------------------------------------

DANGEROUS_WRITE_RIGHTS = 0x500D0156  # generic write/all, data/append/delete/ACL/owner
VOLUME_ROOT_DANGEROUS_RIGHTS = 0x500D0040  # volume root cannot be renamed; Authenticated Users have FILE_ADD_SUBDIRECTORY
HARMLESS_READ_TRAVERSE = 0x001200A9  # read/execute/traverse/read-control/synchronize

_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True) if os.name == "nt" else None
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None


def _windows_inspect_handle_security(handle, trusted_owners, *, is_ancestor=False, is_volume_root=False):
    """Query owner and DACL directly from an opened Win32 directory HANDLE.

    Returns a sanitized dictionary of findings. Never returns raw SIDs, paths,
    or raw ACL pointers.
    """
    from ctypes import wintypes
    adv = _advapi32 if _advapi32 is not None else ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = _kernel32 if _kernel32 is not None else ctypes.WinDLL("kernel32", use_last_error=True)

    adv.GetSecurityInfo.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    adv.GetSecurityInfo.restype = wintypes.DWORD

    adv.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL

    kernel.LocalFree.argtypes = (ctypes.c_void_p,)

    p_owner = ctypes.c_void_p()
    p_dacl = ctypes.c_void_p()
    p_desc = ctypes.c_void_p()

    # SE_FILE_OBJECT = 1, OWNER_SECURITY_INFORMATION (1) | DACL_SECURITY_INFORMATION (4) = 5
    err = adv.GetSecurityInfo(
        handle, 1, 5,
        ctypes.byref(p_owner), None, ctypes.byref(p_dacl), None,
        ctypes.byref(p_desc),
    )
    if err != 0:
        deny("ACL_UNAVAILABLE")

    def sid_str(sid_ptr):
        out = wintypes.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid_ptr, ctypes.byref(out)):
            deny("ACL_UNAVAILABLE")
        try:
            return out.value
        finally:
            kernel.LocalFree(out)

    try:
        if not p_owner.value or not p_dacl.value:
            deny("HOLD_NATIVE_CUSTODY_ACL_FOREIGN")

        owner_sid = sid_str(p_owner)
        is_trusted_owner = (
            owner_sid in trusted_owners or
            (is_ancestor and owner_sid.startswith("S-1-5-80-"))
        )
        if not is_trusted_owner:
            deny("HOLD_NATIVE_CUSTODY_ACL_FOREIGN")

        count = ctypes.c_ushort.from_address(p_dacl.value + 4).value
        adv.GetAce.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
        adv.GetAce.restype = wintypes.BOOL

        inherited_foreign_read_count = 0
        has_inherited_foreign_write = False
        has_explicit_foreign_write = False
        owner_has_effective_rights = False

        for i in range(count):
            ace = ctypes.c_void_p()
            if not adv.GetAce(p_dacl, i, ctypes.byref(ace)):
                deny("ACL_UNAVAILABLE")
            kind, flags = (ctypes.c_ubyte * 2).from_address(ace.value)
            is_inherited = bool(flags & 0x10)
            is_inherit_only = bool(flags & 0x08)

            if kind == 1:  # ACCESS_DENIED_ACE
                continue
            if kind != 0:  # ACCESS_ALLOWED_ACE_TYPE = 0
                deny("UNPROVEN_UNSUPPORTED_ACE_TYPE")

            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            ace_sid = sid_str(ctypes.c_void_p(ace.value + 8))

            is_trusted_ace = (
                ace_sid in trusted_owners or
                (is_ancestor and ace_sid.startswith("S-1-5-80-"))
            )

            if is_trusted_ace:
                if not is_inherit_only and (mask & 0x10000000 or (mask & 0x3) == 0x3 or mask & 0x001F01FF):
                    owner_has_effective_rights = True
            else:
                if is_ancestor and is_inherit_only:
                    continue
                dangerous_mask = VOLUME_ROOT_DANGEROUS_RIGHTS if is_volume_root else DANGEROUS_WRITE_RIGHTS
                if mask & dangerous_mask:
                    if is_inherited:
                        has_inherited_foreign_write = True
                    else:
                        has_explicit_foreign_write = True
                    deny("HOLD_NATIVE_CUSTODY_ACL_FOREIGN")
                else:
                    inherited_foreign_read_count += 1

        return {
            "owner_trusted": True,
            "owner_has_effective_rights": owner_has_effective_rights,
            "inherited_foreign_read_count": inherited_foreign_read_count,
            "has_inherited_foreign_write": has_inherited_foreign_write,
            "has_explicit_foreign_write": has_explicit_foreign_write,
            "foreign_write_detected": has_inherited_foreign_write or has_explicit_foreign_write,
        }
    finally:
        kernel.LocalFree(p_desc)


def windows_owned_fixture_root_preflight(path, *, trusted_owners=None, check_ancestors=True, raise_denied=True):
    """Pre-flight check for Windows owned fixture root custody.

    Verifies pinned directory handle identities, owner DACL effective rights,
    and absence of foreign WRITE/DELETE/DELETE_CHILD/WRITE_DAC/WRITE_OWNER.
    Returns typed sanitized status and hash evidence; never exposes raw SIDs or paths.
    """
    if os.name != "nt":
        if raise_denied:
            deny("W1_NATIVE_WINDOWS_ONLY")
        return {"status": "W1_NATIVE_WINDOWS_ONLY", "verified": False}

    try:
        from .security import windows_identity
        current_sid, _ = windows_identity()
    except Exception:
        if raise_denied:
            deny("TOKEN_UNAVAILABLE")
        return {"status": "TOKEN_UNAVAILABLE", "verified": False}

    if trusted_owners is None:
        trusted_sids = frozenset({current_sid, "S-1-5-18", "S-1-5-32-544"})
    else:
        trusted_sids = frozenset(trusted_owners) | {current_sid, "S-1-5-18", "S-1-5-32-544"}

    p = Path(path)
    raw = str(path)
    if (not p.is_absolute() or raw.startswith(("\\\\", "//", "\\\\.\\", "\\\\?\\")) or
            ".." in p.parts or "." in p.parts):
        if raise_denied:
            deny("W1_NATIVE_ROOT_DENIED")
        return {"status": "W1_NATIVE_ROOT_DENIED", "verified": False}

    try:
        canonical_path(str(p / "w1-fixture-check.txt"), [p], False)
    except Exception as exc:
        if raise_denied:
            raise
        return {"status": "PROTECTED_PATH", "verified": False}

    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL

    ancestor_handles = []
    ancestor_identities = []
    total_inherited_foreign_reads = 0
    base_volume_serial = None

    try:
        ancestors_to_check = list(reversed(p.parents)) if check_ancestors else []
        for ancestor in ancestors_to_check:
            try:
                item = ancestor.lstat()
                if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
                    deny("W1_NATIVE_ROOT_REPARSE")
            except OSError:
                deny("W1_NATIVE_ROOT_DENIED")

            h = _win_handle(ancestor, directory=True, read_control=True)
            ancestor_handles.append(h)

            data = (wintypes.DWORD * 13)()
            if not kernel.GetFileInformationByHandle(h, ctypes.byref(data)):
                deny("W1_NATIVE_ROOT_DENIED")
            if data[0] & 0x400 or not (data[0] & 0x10):
                deny("W1_NATIVE_ROOT_REPARSE")

            vol_serial = f"{data[7]:08x}"
            if base_volume_serial is None:
                base_volume_serial = vol_serial
            elif vol_serial != base_volume_serial:
                deny("W1_NATIVE_WRONG_VOLUME")

            anc_id = {
                "volume_serial_hex": vol_serial,
                "file_index_hex": f"{((data[11] << 32) | data[12]):016x}",
            }
            ancestor_identities.append(anc_id)

            sec_info = _windows_inspect_handle_security(
                h, trusted_sids, is_ancestor=True, is_volume_root=(ancestor == ancestor.parent)
            )
            total_inherited_foreign_reads += sec_info["inherited_foreign_read_count"]

        path_identity = None
        effective_rights_verified = False
        if p.exists():
            item = p.lstat()
            if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
                deny("W1_NATIVE_ROOT_REPARSE")
            if not p.is_dir():
                deny("W1_NATIVE_ROOT_DENIED")

            target_h = _win_handle(p, directory=True, read_control=True)
            ancestor_handles.append(target_h)

            data = (wintypes.DWORD * 13)()
            if not kernel.GetFileInformationByHandle(target_h, ctypes.byref(data)):
                deny("W1_NATIVE_ROOT_DENIED")
            if data[0] & 0x400 or not (data[0] & 0x10):
                deny("W1_NATIVE_ROOT_REPARSE")

            vol_serial = f"{data[7]:08x}"
            if base_volume_serial is not None and vol_serial != base_volume_serial:
                deny("W1_NATIVE_WRONG_VOLUME")

            path_identity = {
                "volume_serial_hex": vol_serial,
                "file_index_hex": f"{((data[11] << 32) | data[12]):016x}",
            }

            sec_info = _windows_inspect_handle_security(target_h, trusted_sids, is_ancestor=False)
            effective_rights_verified = sec_info["owner_has_effective_rights"]
            total_inherited_foreign_reads += sec_info["inherited_foreign_read_count"]

        return {
            "schema": "hostops.fixture.owned_root.v1",
            "status": "OWNED_FIXTURE_ROOT_VERIFIED",
            "root_digest": digest(str(p).encode("utf-8")),
            "root_identity": path_identity,
            "ancestor_identities": ancestor_identities,
            "ancestors_verified_count": len(ancestor_identities),
            "effective_rights_verified": effective_rights_verified,
            "inherited_foreign_read_only_count": total_inherited_foreign_reads,
            "foreign_write_detected": False,
            "verified": True,
        }
    except Exception as exc:
        if raise_denied:
            raise
        from .contract import Denied
        err_msg = str(exc) if isinstance(exc, Denied) else "HOLD_NATIVE_CUSTODY_ACL_FOREIGN"
        return {
            "schema": "hostops.fixture.owned_root.v1",
            "status": err_msg,
            "root_digest": digest(str(p).encode("utf-8")),
            "ancestor_identities": ancestor_identities,
            "ancestors_verified_count": len(ancestor_identities),
            "effective_rights_verified": False,
            "inherited_foreign_read_only_count": total_inherited_foreign_reads,
            "foreign_write_detected": True if "FOREIGN" in err_msg else False,
            "verified": False,
        }
    finally:
        for h in ancestor_handles:
            kernel.CloseHandle(h)


@contextmanager
def windows_owned_fixture_root(candidate_ancestors=None, prefix=".hostops-fixture-", *, trusted_owners=None):
    """Context manager to find a verified local ancestor, create a short-lived
    test-owned private fixture root, prove pinned root + rename-relevant ancestor
    identities, verify owner DACL effective rights, and perform bounded cleanup.
    """
    if os.name != "nt":
        deny("W1_NATIVE_WINDOWS_ONLY")

    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL

    if candidate_ancestors is None:
        raw_candidates = []
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            raw_candidates.append(Path(local_app))
        user_home = Path.home()
        raw_candidates.append(user_home / "AppData" / "Local")
        app_data = os.environ.get("APPDATA")
        if app_data:
            raw_candidates.append(Path(app_data))
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            raw_candidates.append(Path(user_profile))
    else:
        raw_candidates = [Path(c) for c in candidate_ancestors]

    verified_ancestor = None
    ancestor_pin_handles = []
    ancestor_receipt = None

    for cand in raw_candidates:
        if not cand.exists() or not cand.is_dir():
            continue
        try:
            receipt = windows_owned_fixture_root_preflight(
                cand, trusted_owners=trusted_owners, check_ancestors=True, raise_denied=True
            )
            h = _win_handle(cand, directory=True, read_control=True)
            ancestor_pin_handles.append(h)
            verified_ancestor = cand
            ancestor_receipt = receipt
            break
        except Exception:
            continue

    if verified_ancestor is None:
        deny("HOLD_NATIVE_FIXTURE_ROOT_UNAVAILABLE")

    leaf = f"{prefix}{os.urandom(16).hex()}"
    fixture_path = verified_ancestor / leaf
    fixture_path.mkdir(exist_ok=False)

    try:
        preflight = windows_owned_fixture_root_preflight(
            fixture_path, trusted_owners=trusted_owners, check_ancestors=False, raise_denied=True
        )

        data = (wintypes.DWORD * 13)()
        parent_h = ancestor_pin_handles[0]
        if not kernel.GetFileInformationByHandle(parent_h, ctypes.byref(data)):
            deny("W1_NATIVE_ROOT_DENIED")
        parent_current_id = {
            "volume_serial_hex": f"{data[7]:08x}",
            "file_index_hex": f"{((data[11] << 32) | data[12]):016x}",
        }
        if parent_current_id != ancestor_receipt["root_identity"]:
            deny("W1_NATIVE_ROOT_MISMATCH")

        preflight["ancestor_identities"] = ancestor_receipt["ancestor_identities"] + [ancestor_receipt["root_identity"]]
        preflight["ancestors_verified_count"] = len(preflight["ancestor_identities"])
        preflight["inherited_foreign_read_only_count"] += ancestor_receipt["inherited_foreign_read_only_count"]

        yield fixture_path, preflight
    finally:
        for h in ancestor_pin_handles:
            kernel.CloseHandle(h)
        ancestor_pin_handles.clear()

        if fixture_path.exists():
            for root, dirs, files in os.walk(fixture_path, topdown=False):
                for f in files:
                    try:
                        (Path(root) / f).unlink()
                    except OSError:
                        pass
                for d in dirs:
                    try:
                        (Path(root) / d).rmdir()
                    except OSError:
                        pass
            try:
                fixture_path.rmdir()
            except OSError:
                pass

