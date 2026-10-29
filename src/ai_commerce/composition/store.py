"""Append-only composition revisions, using existing protected local custody."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.errors import ArtifactCollisionError

from ._validation import (
    CompositionValidationError, CompositionVersionConflict, digest, identity,
    integer, read_json, record,
)
from .authoring import SOURCE_FIELDS, edit_composition
from .models import CreativeComposition


class CompositionStore:
    """No mutable latest pointer; callers always load an exact revision.

    Registry exclusive creation makes identical writes idempotent and refuses
    conflicts. Interrupted partial files fail closed; this is not a transaction
    or a sandbox against hostile processes renaming ancestor directories.
    """

    def __init__(self, root: str | Path) -> None:
        self.registry = ArtifactRegistry(Path(root))
        self.root = self.registry.root
        # Keep concurrent revisions on this store instance from inspecting the
        # transient POSIX link+unlink publication window as an unsafe hardlink.
        # Filesystem-level safety checks remain unchanged.
        self._save_lock = RLock()

    @staticmethod
    def revision_ref(composition_id: str, revision: int) -> str:
        identity(composition_id, "composition")
        integer(revision, "revision", 1)
        return f"compositions/{composition_id[12:]}/r{revision}.json"

    def _read(self, composition_id: str, revision: int) -> CreativeComposition:
        data = record(read_json(self.registry.read(self.revision_ref(composition_id, revision))),
                      {"composition", "digest"})
        digest(data["digest"])
        composition = CreativeComposition.from_dict(data["composition"])
        if composition.composition_id != composition_id or composition.revision != revision:
            raise CompositionValidationError("stored composition identity/revision mismatch")
        if data["digest"] != composition.digest:
            raise CompositionValidationError("stored composition digest mismatch")
        return composition

    def _check_lineage(self, composition: CreativeComposition) -> None:
        child = composition
        while child.parent is not None:
            parent = self._read(child.parent.composition_id, child.parent.revision)
            if parent.digest != child.parent.digest:
                raise CompositionValidationError("parent digest mismatch")
            if any(getattr(child, name) != getattr(parent, name) for name in SOURCE_FIELDS):
                raise CompositionValidationError("revision cannot change original source evidence")
            content = ("intent", "canvas", "duration_frames", "tracks", "media", "settings")
            if all(getattr(child, name) == getattr(parent, name) for name in content):
                raise CompositionValidationError("revision must change content")
            child = parent

    def save(self, composition: CreativeComposition) -> CreativeComposition:
        if not isinstance(composition, CreativeComposition):
            raise CompositionValidationError("expected CreativeComposition")
        valid = CreativeComposition.from_dict(composition.to_dict())
        # A single writer at a time per instance prevents another writer from
        # observing our transient 2-link POSIX commit window. Do not relax the
        # registry's hardlink protection or collision rules.
        with self._save_lock:
            self._check_lineage(valid)
            payload = canonical_bytes({"composition": valid.to_dict(), "digest": valid.digest})
            try:
                self.registry.put_immutable(self.revision_ref(valid.composition_id, valid.revision), payload)
            except ArtifactCollisionError as exc:
                raise CompositionVersionConflict("VERSION_CONFLICT: composition revision cannot be overwritten") from exc
        return valid

    def load(self, composition_id: str, revision: int) -> CreativeComposition:
        composition = self._read(composition_id, revision)
        self._check_lineage(composition)
        return composition

    def edit(self, composition_id: str, revision: int, **changes: Any) -> CreativeComposition:
        return self.save(edit_composition(self.load(composition_id, revision), **changes))
