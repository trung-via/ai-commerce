"""Content-addressed local custody inside a dedicated, non-linked storage root."""

from __future__ import annotations

import io
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ai_commerce.contracts.schema import ContractValidationError, validate_record
from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.media.base import MediaDependencyError, MediaValidationError
from ai_commerce.media.files import (
    EXTENDED_MIME_KINDS, FileCapture, copy_and_hash, hash_file, open_regular,
    validate_media_file,
)
from ai_commerce.media.local import validate_media
from .errors import (
    ArtifactCollisionError, ArtifactCorruptError, ArtifactMissingError,
    RegistryIOError, RunnerBusyError, UnsafePathError,
)

_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+")
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
}


def _inspect(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise UnsafePathError(f"Linked/reparse storage path: {path}")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise UnsafePathError(f"Storage path is not a regular file/directory: {path}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise UnsafePathError(f"Hard-linked storage file: {path}")
    return info


class ArtifactRegistry:
    """Single-user local store; callers must keep its directory access protected.

    Rejects links/junctions, traversal, ADS and replaced roots on every access.
    This is not an OS sandbox against a hostile process concurrently renaming
    ancestor directories. Output bytes are never overwritten or silently healed.
    """

    def __init__(self, root: Path):
        self._root = Path(os.path.abspath(root))
        if self._root == Path(self._root.anchor):
            raise UnsafePathError("A dedicated registry directory is required")
        self._check_ancestors()
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._check_ancestors()
        info = _inspect(self._root)
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise UnsafePathError("Registry root must be a directory")
        self._identity = (info.st_dev, info.st_ino)

    @property
    def root(self) -> Path:
        return self._root

    def _check_ancestors(self) -> None:
        for path in (*reversed(self._root.parents), self._root):
            info = _inspect(path)
            if info is not None and not stat.S_ISDIR(info.st_mode):
                raise UnsafePathError(f"Non-directory ancestor: {path}")

    def _guard_root(self) -> None:
        self._check_ancestors()
        info = _inspect(self._root)
        if info is None or (info.st_dev, info.st_ino) != self._identity:
            raise UnsafePathError("Registry root was removed or replaced")

    def safe_path(self, relative: str) -> Path:
        self._guard_root()
        if not isinstance(relative, str) or not relative:
            raise UnsafePathError("Expected a nonempty relative storage reference")
        parts = relative.split("/")
        if any(not _COMPONENT.fullmatch(part) or part in {".", ".."}
               or part.endswith(".") or part.split(".")[0].upper() in _RESERVED
               for part in parts):
            raise UnsafePathError(f"Invalid storage reference: {relative!r}")
        path = self._root
        for i, part in enumerate(parts):
            path = path / part
            info = _inspect(path)
            if info is not None and i < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise UnsafePathError("Non-directory storage ancestor")
        if not path.resolve().is_relative_to(self._root):
            raise UnsafePathError("Storage reference escaped its root")
        return path

    def exists(self, relative: str) -> bool:
        return _inspect(self.safe_path(relative)) is not None

    def read(self, relative: str) -> bytes:
        path = self.safe_path(relative)
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise UnsafePathError("Expected an unlinked regular storage file")
                content = stream.read()
            after = _inspect(self.safe_path(relative))
            if after is None or (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino):
                raise UnsafePathError("Storage file changed during read")
            return content
        except FileNotFoundError as exc:
            raise ArtifactMissingError(f"Missing storage output: {relative}") from exc
        except OSError as exc:
            raise RegistryIOError(f"Cannot read {relative}: {exc}") from exc

    def _parents(self, relative: str) -> Path:
        path = self.safe_path(relative)
        for parent in reversed(path.parents):
            if parent.is_relative_to(self._root) and parent != self._root:
                self.safe_path(parent.relative_to(self._root).as_posix())
                parent.mkdir(exist_ok=True, mode=0o700)
                self.safe_path(parent.relative_to(self._root).as_posix())
        return self.safe_path(relative)

    @contextmanager
    def _temporary(self):
        relative = ".staging/" + uuid.uuid4().hex + ".tmp"
        path = self._parents(relative)
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        try:
            with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
                yield relative, path, stream
        finally:
            # Check links and root identity even during cleanup.
            if self.exists(relative):
                self.safe_path(relative).unlink()

    def _same_files(self, left: str, right: str) -> bool:
        from ai_commerce.media.files import CHUNK_BYTES
        with open_regular(self.safe_path(left)) as a, open_regular(self.safe_path(right)) as b:
            while True:
                x, y = a.read(CHUNK_BYTES), b.read(CHUNK_BYTES)
                if x != y:
                    return False
                if not x:
                    return True

    def _commit(self, temporary: str, relative: str) -> bool:
        """Atomic no-replace publication on Windows and POSIX.

        POSIX uses link+unlink. A crash in that short interval leaves two links
        and fails closed on subsequent access; it needs local reconciliation.
        """
        source, target = self.safe_path(temporary), self._parents(relative)
        try:
            if os.name == "nt":
                os.rename(source, target)  # Windows rename cannot replace a target.
            else:
                os.link(source, target, follow_symlinks=False)
                source.unlink()  # Reduce our own temporary second link to one.
        except FileExistsError:
            if not self._same_files(temporary, relative):
                raise ArtifactCollisionError(f"Refusing to overwrite immutable output: {relative}")
            return False
        self.safe_path(relative)
        return True

    def put_immutable(self, relative: str, content: bytes) -> bool:
        if not isinstance(content, bytes):
            raise TypeError("Registry accepts bytes")
        self.safe_path(relative)
        try:
            with self._temporary() as (temporary, _, stream):
                copy_and_hash(io.BytesIO(content), stream)
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
                return self._commit(temporary, relative)
        except OSError as exc:
            raise RegistryIOError(f"Cannot write {relative}: {exc}") from exc

    @contextmanager
    def capture_file(self, source: Path) -> Iterator[FileCapture]:
        """Capture once to a protected temp, hashing fixed-size chunks."""
        source = Path(os.path.abspath(source))
        try:
            with self._temporary() as (_, path, target):
                with open_regular(source) as stream:
                    digest, size = copy_and_hash(stream, target)
                if size <= 0:
                    raise MediaValidationError("Expected nonempty media file")
                target.flush()
                os.fsync(target.fileno())
                target.close()
                yield FileCapture(path, digest, size, str(source), str(source))
        except OSError as exc:
            raise RegistryIOError(f"Cannot capture media file: {exc}") from exc

    def register_file(self, source: Path, mime_type: str, *, provider: str,
                      snapshot: dict[str, Any], mode: str, origin: str,
                      rights_ref: str | None = None) -> dict[str, Any]:
        """Opt-in file custody; no large video bytes are returned or retained."""
        if {"import": "LOCAL_IMPORT", "mock": "MOCK"}.get(mode) != origin or origin is None:
            raise MediaValidationError("Only explicit local-import/mock custody is allowed")
        validate_record("SourceSnapshot", snapshot)
        if snapshot["rights_status"] == "GRANTED" and (not isinstance(rights_ref, str) or not rights_ref.strip()):
            raise MediaValidationError("GRANTED requires explicit rights evidence")
        with self.capture_file(source) as captured:
            verification = validate_media_file(captured.path, mime_type)
            if origin == "LOCAL_IMPORT" and captured.sha256 != snapshot["sha256"]:
                raise MediaValidationError("Local import must preserve the source digest")
            artifact = {
                "artifact_id": "artifact:" + captured.sha256, "kind": verification.kind,
                "mime_type": mime_type, "sha256": captured.sha256,
                "size_bytes": captured.size_bytes, "path": "objects/" + captured.sha256,
                "source_snapshot_ids": [snapshot["snapshot_id"]],
                "source_ref": snapshot["source_ref"], "rights_status": snapshot["rights_status"],
                "rights_ref": rights_ref, "provider": provider, "mode": mode, "origin": origin,
                "generation_performed": False,
                "source_generation": "UNKNOWN" if origin == "LOCAL_IMPORT" else "SYNTHETIC_FIXTURE",
            }
            validate_record("MediaArtifact", artifact)
            if hash_file(captured.path) != (captured.sha256, captured.size_bytes):
                raise MediaValidationError("Captured media changed during validation")
            temporary = captured.path.relative_to(self._root).as_posix()
            try:
                self._commit(temporary, artifact["path"])
            except OSError as exc:
                raise RegistryIOError(f"Cannot commit media object: {exc}") from exc
            return artifact

    def verify_file(self, artifact: dict[str, Any]) -> Path:
        """Reopen, hash and validate stored media without materializing MP4."""
        try:
            validate_record("MediaArtifact", artifact)
        except ContractValidationError as exc:
            raise ArtifactCorruptError(f"Invalid MediaArtifact: {exc}") from exc
        digest, relative = artifact["sha256"], artifact["path"]
        path = self.safe_path(relative)
        if relative != "objects/" + digest or artifact["artifact_id"] != "artifact:" + digest:
            raise ArtifactCorruptError("Artifact identity/path does not match its SHA256")
        if not self.exists(relative):
            raise ArtifactMissingError(f"Missing storage output: {relative}")
        try:
            if hash_file(path) != (digest, artifact["size_bytes"]):
                raise ArtifactCorruptError("Output bytes differ from the recorded SHA256/size")
            check = validate_media_file(self.safe_path(relative), artifact["mime_type"])
            if check.kind != artifact["kind"]:
                raise ArtifactCorruptError("Artifact kind does not match media")
            if hash_file(self.safe_path(relative)) != (digest, artifact["size_bytes"]):
                raise ArtifactCorruptError("Output changed during verification")
        except MediaDependencyError:
            raise  # Missing decoder does not establish corrupt bytes.
        except MediaValidationError as exc:
            raise ArtifactCorruptError(f"Invalid stored media: {exc}") from exc
        return self.safe_path(relative)

    @contextmanager
    def worker(self) -> Iterator[None]:
        """One worker per root, including other runner instances/processes.

        A process crash leaves the lock for explicit local reconciliation.
        """
        token = uuid.uuid4().hex.encode("ascii")
        path = self.safe_path("worker.lock")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError as exc:
            raise RunnerBusyError("Local worker already active or unreconciled lock") from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(token)
            yield
        finally:
            if self.read("worker.lock") != token:
                raise RegistryIOError("Worker lock changed; refusing to remove it")
            self.safe_path("worker.lock").unlink()

    def register(self, content: bytes, mime_type: str, *, provider: str,
                 snapshot: dict[str, Any], mode: str, origin: str,
                 rights_ref: str | None = None) -> dict[str, Any]:
        if {"import": "LOCAL_IMPORT", "mock": "MOCK"}.get(mode) != origin or origin is None:
            raise MediaValidationError("Only explicit local-import/mock custody is allowed")
        kind = validate_media(content, mime_type)
        validate_record("SourceSnapshot", snapshot)
        digest = sha256_bytes(content)
        if origin == "LOCAL_IMPORT" and digest != snapshot["sha256"]:
            raise MediaValidationError("Local import must preserve the source digest")
        artifact = {
            "artifact_id": "artifact:" + digest, "kind": kind, "mime_type": mime_type,
            "sha256": digest, "size_bytes": len(content), "path": "objects/" + digest,
            "source_snapshot_ids": [snapshot["snapshot_id"]],
            "source_ref": snapshot["source_ref"], "rights_status": snapshot["rights_status"],
            "rights_ref": rights_ref, "provider": provider, "mode": mode, "origin": origin,
            "generation_performed": False,
            "source_generation": "UNKNOWN" if origin == "LOCAL_IMPORT" else "SYNTHETIC_FIXTURE",
        }
        validate_record("MediaArtifact", artifact)
        self.put_immutable(artifact["path"], content)
        return artifact

    def verify(self, artifact: dict[str, Any]) -> bytes:
        if artifact.get("mime_type") in EXTENDED_MIME_KINDS:
            path = self.verify_file(artifact)
            if artifact["mime_type"] == "video/mp4":
                raise MediaValidationError("MP4 is file-only; use verify_file()")
            return self.read(path.relative_to(self._root).as_posix())
        try:
            validate_record("MediaArtifact", artifact)
        except ContractValidationError as exc:
            raise ArtifactCorruptError(f"Invalid MediaArtifact: {exc}") from exc
        self.safe_path(artifact["path"])
        digest = artifact["sha256"]
        if artifact["path"] != "objects/" + digest or artifact["artifact_id"] != "artifact:" + digest:
            raise ArtifactCorruptError("Artifact identity/path does not match its SHA256")
        content = self.read(artifact["path"])
        if sha256_bytes(content) != digest or len(content) != artifact["size_bytes"]:
            raise ArtifactCorruptError("Output bytes differ from the recorded SHA256/size")
        try:
            kind = validate_media(content, artifact["mime_type"])
        except MediaValidationError as exc:
            raise ArtifactCorruptError("Invalid stored media") from exc
        if artifact["kind"] != kind:
            raise ArtifactCorruptError("Artifact kind does not match media")
        return content
