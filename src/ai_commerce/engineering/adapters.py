"""Selective CLI invocation adapters, not AIOS Runtime, Kernel, or Publisher.

Command layouts were adapted from pinned AIOS codex_adapter/antigravity_adapter.
AGY flag support must be confirmed against the user's installed CLI version.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from pathlib import Path

from .profile import ExecutorSelection
from .source_safety import is_link


AGY_CONTRACT_NAMES = {"instruction.txt", "repair_feedback.json"}
AGY_CONTRACT_MAX_BYTES = 4 * 1024 * 1024
# Deliberately not a general metadata/config extension allowlist. Contents of
# these disposable, non-executable sidecars are never opened or interpreted.
AGY_AUXILIARY_NAME = re.compile(r"(?:pytest|ffmpeg|scratch|cache)-[A-Za-z0-9_-]{1,64}\.(?:log|tmp)\Z")


def agy_file_identity(info) -> tuple:
    # On native Windows, fstat and lstat can expose different st_ctime values
    # (change time versus legacy creation time). Compare creation time where
    # available, plus exact bytes, file ID, mode, links, size and write time.
    stamp = getattr(info, "st_birthtime_ns", 0) if os.name == "nt" else info.st_ctime_ns
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, stamp)


def agy_no_named_streams(path: Path) -> None:
    """NTFS streams are invisible to iterdir, including on the mirror itself."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    first = kernel.FindFirstStreamW
    first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(StreamData), wintypes.DWORD]
    first.restype = wintypes.HANDLE
    next_stream = kernel.FindNextStreamW
    next_stream.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
    next_stream.restype = wintypes.BOOL
    close = kernel.FindClose
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    data = StreamData()
    handle = first(str(path), 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        if ctypes.get_last_error() == 38:  # ERROR_HANDLE_EOF: no streams
            return
        raise ValueError("AGY_INPUT_STREAMS_UNPROVEN")
    try:
        while True:
            if data.name != "::$DATA":
                raise ValueError("AGY_INPUT_NAMED_STREAM")
            if not next_stream(handle, ctypes.byref(data)):
                if ctypes.get_last_error() != 38:
                    raise ValueError("AGY_INPUT_STREAMS_UNPROVEN")
                break
    finally:
        close(handle)


def agy_regular_state(path: Path):
    info = path.lstat()
    if is_link(path) or not stat.S_ISREG(info.st_mode):
        raise ValueError("AGY_INPUT_NON_REGULAR")
    if info.st_nlink != 1:
        raise ValueError("AGY_INPUT_HARDLINK")
    if info.st_mode & 0o111:
        raise ValueError("AGY_INPUT_EXECUTABLE")
    agy_no_named_streams(path)
    if agy_file_identity(path.lstat()) != agy_file_identity(info) or is_link(path):
        raise ValueError("AGY_INPUT_RACE")
    return info


def agy_read_regular(path: Path, limit: int) -> tuple[bytes, tuple]:
    """Bounded handle read, rejecting replacement, hardlinks and reparse races."""
    parent = agy_directory(path.parent)
    parent_id = (parent.stat().st_dev, parent.stat().st_ino)
    before = agy_regular_state(path)
    if before.st_size > limit:
        raise ValueError("AGY_INPUT_SIZE_LIMIT")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if agy_file_identity(os.fstat(stream.fileno())) != agy_file_identity(before):
            raise ValueError("AGY_INPUT_RACE")
        # Validate the *opened handle* before reading any bytes. A parent
        # reparse swap must not turn an expected contract into an external read.
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            final_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
            final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            final_path.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            length = final_path(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
            if not 0 < length < len(buffer):
                raise ValueError("AGY_INPUT_HANDLE_PATH_UNPROVEN")
            actual = buffer.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            if os.path.normcase(actual) != os.path.normcase(str(parent / path.name)):
                raise ValueError("AGY_INPUT_HANDLE_PATH_CHANGED")
        current_parent = agy_directory(path.parent).stat()
        if (current_parent.st_dev, current_parent.st_ino) != parent_id:
            raise ValueError("AGY_INPUT_RACE")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(data) != before.st_size or len(data) > limit or
            agy_file_identity(after) != agy_file_identity(before) or
            agy_file_identity(agy_regular_state(path)) != agy_file_identity(before)):
        raise ValueError("AGY_INPUT_RACE")
    return data, agy_file_identity(before)


def agy_directory(path: Path) -> Path:
    """Bind an existing physical directory without accepting reparse aliases."""
    if ".." in Path(path).parts:
        raise ValueError("AGY_PATH_INDIRECTION")
    absolute = Path(os.path.abspath(path))
    if any(is_link(p) for p in (absolute, *absolute.parents)):
        raise ValueError("AGY_PATH_INDIRECTION")
    resolved = absolute.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("AGY_DIRECTORY_REQUIRED")
    return resolved


def agy_data_directory(path: Path) -> Path:
    """Input data must not acquire a checkout's implicit source authority."""
    directory = agy_directory(path)
    for ancestor in (directory, *directory.parents):
        marker = ancestor / ".git"
        if marker.exists() or is_link(marker) or (
                (ancestor / "HEAD").is_file() and (ancestor / "objects").is_dir()
                and (ancestor / "refs").is_dir()):
            raise ValueError("AGY_INPUT_INSIDE_GIT")
    return directory


class _AgyCleanupHandle:
    """Bind an NTFS entry without following reparse points or allowing rename.

    Deletion and readonly changes use this same handle, never a reopened path.
    Directory handles remain open while their children are inspected/deleted.
    This is confined scratch ownership, not an ACL/privilege repair facility.
    """
    def __init__(self, path: Path, expected, *, parent_only=False):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel = self.kernel
        signatures = {
            "CreateFileW": ([w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            "GetCurrentProcess": ([], w.HANDLE),
            "DuplicateHandle": ([w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD], w.BOOL),
            "GetFinalPathNameByHandleW": ([w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
            "SetFileInformationByHandle": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes, function.restype = args, result
        # FILE_READ_ATTRIBUTES; DELETE + FILE_WRITE_ATTRIBUTES for owned entries.
        access = 0x80 if parent_only else 0x10180
        self.handle = kernel.CreateFileW(str(path), access, 3, None, 3, 0x02200000, None)
        if self.handle == ctypes.c_void_p(-1).value:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # Python 3.11 uses legacy Windows file/volume IDs; 3.12+ can
            # expose 128/64 bits. fstat on a duplicate of this SAME handle
            # uses the interpreter's lstat representation without truncation.
            import msvcrt
            current, duplicate = kernel.GetCurrentProcess(), w.HANDLE()
            if not kernel.DuplicateHandle(current, self.handle, current, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle(duplicate)
                raise
            try:
                opened = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            actual_id = (opened.st_dev, opened.st_ino)
            if actual_id != (expected.st_dev, expected.st_ino):
                raise ValueError("AGY_INPUT_CLEANUP_UNSAFE")
            buffer = ctypes.create_unicode_buffer(32768)
            length = kernel.GetFinalPathNameByHandleW(self.handle, buffer, len(buffer), 0)
            if not 0 < length < len(buffer):
                raise ValueError("AGY_SCRATCH_HANDLE_UNPROVEN")
            actual = buffer.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            if os.path.normcase(actual) != os.path.normcase(str(path)):
                raise ValueError("AGY_INPUT_CLEANUP_UNSAFE")
        except BaseException:
            self.close()
            raise

    def delete(self, info, report):
        ctypes, w = self.ctypes, self.w
        class Disposition(ctypes.Structure):
            _fields_ = [("delete", ctypes.c_ubyte)]
        disposition = Disposition(1)
        if self.kernel.SetFileInformationByHandle(self.handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            return
        error = ctypes.get_last_error()
        if (error != 5 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not getattr(info, "st_file_attributes", 0) & 1
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ctypes.WinError(error)
        class BasicInfo(ctypes.Structure):
            _fields_ = [("creation", ctypes.c_longlong), ("access", ctypes.c_longlong),
                        ("write", ctypes.c_longlong), ("change", ctypes.c_longlong), ("attributes", w.DWORD)]
        # Zero timestamps preserve their current values. Clear ONLY the proven
        # readonly bit on this single-link, non-reparse, exact owned file handle.
        basic = BasicInfo(0, 0, 0, 0, (info.st_file_attributes & ~1) or 0x80)
        if not self.kernel.SetFileInformationByHandle(self.handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ctypes.WinError(ctypes.get_last_error())
        report["readonly_files_cleared"] += 1
        if not self.kernel.SetFileInformationByHandle(self.handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def agy_remove_owned_tree(directory: Path, parent: Path, identity: tuple,
                          parent_identity: tuple, *, timeout_seconds=5.0) -> dict:
    """Bounded scratch teardown with sanitized diagnostics and no ACL changes.

    Native Windows uses handle-bound deletion. POSIX is a separate fixture path
    with lstat/containment checks; it does not claim native Windows proof.
    """
    report = {"status": "HOLD", "reason": "AGY_SCRATCH_CLEANUP_UNPROVEN",
              "platform": "WINDOWS_HANDLES" if os.name == "nt" else "POSIX_FIXTURE",
              "root_token_sha256": hashlib.sha256(str(directory).encode()).hexdigest(),
              "attempts": 0, "readonly_files_cleared": 0, "reparse_links_removed": 0,
              "diagnostics": []}
    deadline = time.monotonic() + timeout_seconds
    target = directory
    def inspect(path):
        if path != directory and directory not in path.parents:
            raise ValueError("AGY_INPUT_CLEANUP_UNSAFE")
        relative = path.relative_to(parent)
        if any(p in ("", ".", "..") or ":" in p or p.rstrip(". ") != p for p in relative.parts):
            raise ValueError("AGY_INPUT_CLEANUP_UNSAFE")
        return path.lstat()
    try:
        if (directory.parent != parent or directory == parent or agy_directory(parent) != parent
                or (parent.stat().st_dev, parent.stat().st_ino) != parent_identity):
            raise ValueError("AGY_INPUT_CLEANUP_UNSAFE")
        parent_handle = _AgyCleanupHandle(parent, parent.lstat(), parent_only=True) if os.name == "nt" else None
        try:
            for attempt in range(5):
                report["attempts"] = attempt + 1
                count = 0
                def remove(path, depth=0):
                    nonlocal count, target
                    target = path
                    count += 1
                    if count > 100000 or depth > 128 or time.monotonic() >= deadline:
                        raise ValueError("AGY_SCRATCH_CLEANUP_BOUND_EXCEEDED")
                    info = inspect(path)
                    reparse = is_link(path)
                    if path == directory and (reparse or (info.st_dev, info.st_ino) != identity):
                        raise ValueError("AGY_INPUT_CLEANUP_UNSAFE")
                    if not reparse and not stat.S_ISDIR(info.st_mode) and (
                            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                        raise ValueError("AGY_SCRATCH_ENTRY_UNSAFE")
                    handle = _AgyCleanupHandle(path, info) if os.name == "nt" else None
                    try:
                        if stat.S_ISDIR(info.st_mode) and not reparse:
                            with os.scandir(path) as entries:
                                # Bound enumeration as well as deletion; never
                                # materialize an attacker-sized list of paths.
                                for entry in entries:
                                    remove(Path(entry.path), depth + 1)
                        current = inspect(path)
                        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino) or is_link(path) != reparse:
                            raise ValueError("AGY_INPUT_CLEANUP_UNSAFE")
                        if handle is not None:
                            handle.delete(current, report)
                        elif stat.S_ISDIR(info.st_mode) and not path.is_symlink():
                            path.rmdir()
                        else:
                            path.unlink()
                        if reparse:
                            report["reparse_links_removed"] += 1
                    finally:
                        if handle is not None:
                            handle.close()
                try:
                    remove(directory)
                    if os.path.lexists(directory):
                        raise ValueError("AGY_SCRATCH_RESIDUAL_PRESENT")
                    report.update(status="CLEAN", reason=None)
                    break
                except OSError as exc:
                    native_code = getattr(exc, "winerror", None)
                    report["diagnostics"].append({"winerror": native_code,
                        "target_sha256": hashlib.sha256(str(target.relative_to(parent)).encode()).hexdigest(),
                        "classification": "SHARING_VIOLATION" if native_code in (32, 33) else
                        "ACCESS_DENIED_UNATTRIBUTED" if native_code == 5 else "OS_ERROR"})
                    # ERROR_ACCESS_DENIED may be transient (including scanners),
                    # but is never labeled Defender/ACL without independent proof.
                    if os.name != "nt" or native_code not in (5, 32, 33) or attempt == 4:
                        raise
                    delay = 0.05 * 2 ** attempt
                    if time.monotonic() + delay >= deadline:
                        raise
                    time.sleep(delay)
        finally:
            if parent_handle is not None:
                parent_handle.close()
    except (OSError, ValueError) as exc:
        code = str(exc)
        if re.fullmatch(r"AGY_[A-Z_]+", code):
            report["reason"] = code
    return report


def agy_input_files(repo: Path, path: Path) -> dict[str, bytes]:
    source, directory = agy_directory(repo), agy_data_directory(path)
    if source == directory or source in directory.parents or directory in source.parents:
        raise ValueError("AGY_INPUT_SOURCE_OVERLAP")
    files = list(directory.iterdir())
    if (not (directory / "instruction.txt").is_file() or
            any(p.name not in ("instruction.txt", "repair_feedback.json") or
                is_link(p) or not p.is_file() for p in files)):
        raise ValueError("AGY_EVIDENCE_DIR_NOT_PREPARED")
    return {p.name: agy_read_regular(p, AGY_CONTRACT_MAX_BYTES)[0] for p in files}


def agy_input_manifest(repo: Path, path: Path, expected: dict[str, bytes],
                       baseline: dict | None = None) -> dict:
    """Separate immutable host contracts from a finite inert sidecar namespace.

    Auxiliary bytes are never read, hashed, used as instructions or published.
    A null hash explicitly records that limitation rather than inventing proof.
    """
    result = {"status": "INTACT", "reason": None, "files": [],
              "directory_identity": None, "directory_state": None}
    try:
        source, directory = agy_directory(repo), agy_data_directory(path)
        if source == directory or source in directory.parents or directory in source.parents:
            raise ValueError("AGY_INPUT_SOURCE_OVERLAP")
        root = directory.lstat()
        result["directory_identity"] = [root.st_dev, root.st_ino]
        result["directory_state"] = list(agy_file_identity(root))
        if baseline and result["directory_identity"] != baseline["directory_identity"]:
            raise ValueError("AGY_INPUT_DIRECTORY_REPLACED")
        agy_no_named_streams(directory)
        if "instruction.txt" not in expected or not set(expected) <= AGY_CONTRACT_NAMES:
            raise ValueError("AGY_INPUT_CONTRACT_UNPREPARED")
        def entries():
            found = {}
            with os.scandir(directory) as scan:
                for entry in scan:
                    found[entry.name] = Path(entry.path)
                    if len(found) > len(expected) + 16:
                        raise ValueError("AGY_INPUT_COUNT_LIMIT")
            return found
        by_name = entries()
        prior = {f["name"]: f for f in baseline["files"]} if baseline else {}
        states = {}
        auxiliary_size = 0
        for name in sorted(set(by_name) | set(expected) | set(prior)):
            item = {"name": name if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) else None,
                    "name_sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
                    "role": "CONTRACT" if name in expected else "AUXILIARY",
                    "change": "ADDED" if name not in expected else "UNCHANGED",
                    "type": "UNKNOWN", "size": None, "sha256": None}
            result["files"].append(item)
            if name not in by_name:
                item.update(change="DELETED", type="MISSING")
                if name in expected:
                    raise ValueError("AGY_INPUT_CONTRACT_DELETED")
                continue
            candidate = by_name[name]
            info = candidate.lstat()
            item.update(type="REPARSE" if is_link(candidate) else
                        "REGULAR" if stat.S_ISREG(info.st_mode) else "NON_REGULAR",
                        size=info.st_size, mode=stat.S_IMODE(info.st_mode))
            states[name] = agy_file_identity(agy_regular_state(candidate))
            if name in expected:
                data, identity = agy_read_regular(candidate, AGY_CONTRACT_MAX_BYTES)
                item.update(sha256=hashlib.sha256(data).hexdigest(), identity=list(identity))
                if data != expected[name]:
                    item["change"] = "MODIFIED"
                    raise ValueError("AGY_INPUT_CONTRACT_MODIFIED")
                if baseline and item["identity"] != prior[name]["identity"]:
                    item["change"] = "REPLACED"
                    raise ValueError("AGY_INPUT_CONTRACT_REPLACED")
            else:
                item["identity"] = list(states[name])
                if name in prior:
                    item["change"] = "UNCHANGED" if item["identity"] == prior[name].get("identity") else "MODIFIED"
                item["hash_status"] = "AUXILIARY_CONTENT_NOT_READ"
                if not AGY_AUXILIARY_NAME.fullmatch(name):
                    raise ValueError("AGY_INPUT_UNEXPECTED_AUXILIARY")
                auxiliary_size += info.st_size
                if info.st_size > 1024 * 1024 or auxiliary_size > 4 * 1024 * 1024:
                    raise ValueError("AGY_INPUT_SIZE_LIMIT")
        # Detect changes while the manifest was being assembled, including a
        # swap with equal bytes. The verifier repeats this check after tests.
        if set(entries()) != set(by_name):
            raise ValueError("AGY_INPUT_RACE")
        for name, identity in states.items():
            if agy_file_identity(agy_regular_state(directory / name)) != identity:
                raise ValueError("AGY_INPUT_RACE")
        if agy_directory(path) != directory or agy_file_identity(directory.lstat()) != agy_file_identity(root):
            raise ValueError("AGY_INPUT_RACE")
        agy_no_named_streams(directory)
    except (OSError, ValueError) as exc:
        code = str(exc)
        result.update(status="HOLD", reason=code if re.fullmatch(r"AGY_[A-Z_]+", code)
                      else "AGY_INPUT_INSPECTION_UNPROVEN")
    return result


def command_for(selection: ExecutorSelection, repo: Path, instruction: str,
                *, codex_bin: str = "codex", antigravity_bin: str = "agy",
                codex_windows_sandbox: str | None = None,
                antigravity_evidence_dir: Path | None = None,
                antigravity_scratch_dir: Path | None = None) -> tuple[list[str], str | None]:
    """Return argv and optional stdin; no shell and no silent alternative provider.

    Only the EF02 Windows Service caller opts into MXC. Manual EF01 remains
    unchanged, and Antigravity never receives Codex-specific sandbox flags.
    """
    if codex_windows_sandbox not in (None, "mxc"):
        raise ValueError("UNSUPPORTED_CODEX_WINDOWS_SANDBOX")
    if codex_windows_sandbox is not None and selection.executor != "codex":
        raise ValueError("CODEX_SANDBOX_WITH_NON_CODEX_EXECUTOR")
    if antigravity_evidence_dir is not None and selection.executor != "antigravity":
        raise ValueError("AGY_EVIDENCE_WITH_NON_AGY_EXECUTOR")
    if antigravity_scratch_dir is not None and selection.executor != "antigravity":
        raise ValueError("AGY_SCRATCH_WITH_NON_AGY_EXECUTOR")
    if selection.executor == "codex":
        mxc_override = ["-c", "windows.sandbox=mxc"] if codex_windows_sandbox == "mxc" else []
        return [
            codex_bin, *mxc_override, "exec", "--cd", str(repo), "-m", selection.model,
            "-c", f'model_reasoning_effort="{selection.effort}"',
            "--sandbox", "workspace-write", "--color", "never", "--json", "-",
        ], instruction
    # Register only the bound source and disposable input, never runner evidence
    # or the primary checkout. The second root carries data, not source authority.
    source = agy_directory(repo)
    evidence_args: list[str] = []
    if antigravity_evidence_dir is not None:
        evidence = agy_data_directory(antigravity_evidence_dir)
        agy_input_files(source, evidence)
        evidence_args = ["--add-dir", str(evidence)]
    if antigravity_scratch_dir is not None:
        scratch = agy_data_directory(antigravity_scratch_dir)
        roots = [source] + ([evidence] if antigravity_evidence_dir is not None else [])
        if any(scratch == root or scratch in root.parents or root in scratch.parents for root in roots):
            raise ValueError("AGY_SCRATCH_ROOT_OVERLAP")
        evidence_args += ["--add-dir", str(scratch)]
    return [
        antigravity_bin, "--print", instruction, "--add-dir", str(source),
        *evidence_args, "--model", selection.model, "--effort", selection.effort,
        "--mode", "accept-edits", "--disable-slash-commands", "--output-format", "json",
    ], None
