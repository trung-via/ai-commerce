"""Local immutable brief snapshots, concept revisions and pinned selections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_commerce.core.provenance import canonical_bytes

from ._validation import CreativeValidationError, VersionConflict, positive_int, read_json
from .briefs import brief_digest, intake_brief
from .models import (
    EDITABLE_FIELDS, ConceptChange, ConceptRecord, ConceptSelection, require_concept_id, require_digest,
)


def _put(path: Path, content: bytes) -> None:
    """Exclusive create closes A02 put_immutable's check/write race for A03.

    Matching bytes are idempotent; conflicting or partial files fail closed.
    Existing A02 core helpers remain unchanged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as target:
            target.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise VersionConflict(f"VERSION_CONFLICT: refusing to overwrite {path}") from None


def _check_parent(record: ConceptRecord, parent: ConceptRecord) -> None:
    if record.parent is None or record.parent.digest != parent.digest:
        raise CreativeValidationError("parent digest mismatch")
    retained = (
        "brief_id", "source_brief_digest", "concept_id", "source_refs", "assumptions",
        "observation", "factual_status", "rights_status", "schema_version",
    )
    if any(getattr(record, name) != getattr(parent, name) for name in retained):
        raise CreativeValidationError("revision cannot alter source or generation provenance")
    expected = tuple(
        ConceptChange(name, getattr(parent, name), getattr(record, name))
        for name in sorted(EDITABLE_FIELDS) if getattr(parent, name) != getattr(record, name)
    )
    if record.changes != expected:
        raise CreativeValidationError("revision changes do not describe its actual parent")


class CreativeStore:
    """One local directory; identity validation prevents caller-controlled paths.

    Brief versions are content addressed; concepts have sequential revisions.
    No mutable 'latest' or global approval/quality state is maintained.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save_brief(self, brief: Any) -> str:
        valid = intake_brief(brief)
        digest = brief_digest(valid)
        _put(self.root / "briefs" / f"{digest}.json", canonical_bytes(valid))
        return digest

    def load_brief(self, digest: str) -> dict[str, Any]:
        require_digest(digest)
        content = (self.root / "briefs" / f"{digest}.json").read_bytes()
        brief = intake_brief(read_json(content.decode("utf-8")))
        if brief_digest(brief) != digest:
            raise CreativeValidationError("stored brief digest mismatch")
        return brief

    def _concept_path(self, concept_id: str, revision: int) -> Path:
        require_concept_id(concept_id)
        positive_int(revision, "revision")
        return self.root / "concepts" / concept_id[8:] / f"r{revision}.json"

    def _validate_links(self, record: ConceptRecord) -> None:
        brief = self.load_brief(record.source_brief_digest)
        if record.brief_id != brief["brief_id"] or not set(record.source_refs) <= set(brief.get("source_refs", [])):
            raise CreativeValidationError("concept does not match its source brief")
        if record.parent:
            parent = self.load_concept(record.parent.concept_id, record.parent.revision)
            _check_parent(record, parent)

    def save_concept(self, concept: ConceptRecord) -> ConceptRecord:
        if not isinstance(concept, ConceptRecord):
            raise CreativeValidationError("expected ConceptRecord")
        validated = ConceptRecord.from_dict(concept.to_dict())
        self._validate_links(validated)
        _put(self._concept_path(validated.concept_id, validated.revision), canonical_bytes(validated.to_dict()))
        return validated

    def load_concept(self, concept_id: str, revision: int) -> ConceptRecord:
        data = self._concept_path(concept_id, revision).read_text(encoding="utf-8")
        record = ConceptRecord.from_json(data)
        if record.concept_id != concept_id or record.revision != revision:
            raise CreativeValidationError("stored concept identity/revision mismatch")
        self._validate_links(record)
        return record

    def select(self, concept_id: str, revision: int) -> ConceptSelection:
        record = self.load_concept(concept_id, revision)
        selection = ConceptSelection(
            record.concept_id, record.revision, record.digest,
            record.brief_id, record.source_brief_digest,
        )
        _put(self._selection_path(selection.selection_id), canonical_bytes(selection.to_dict()))
        return selection

    def _selection_path(self, selection_id: str) -> Path:
        if not isinstance(selection_id, str) or not selection_id.startswith("selection:"):
            raise CreativeValidationError("invalid selection_id")
        digest = require_digest(selection_id[10:])
        return self.root / "selections" / f"{digest}.json"

    def load_selection(self, selection_id: str) -> ConceptSelection:
        selection = ConceptSelection.from_dict(read_json(
            self._selection_path(selection_id).read_text(encoding="utf-8")
        ))
        if selection.selection_id != selection_id:
            raise CreativeValidationError("stored selection digest mismatch")
        concept = self.load_concept(selection.concept_id, selection.revision)
        if (concept.digest != selection.concept_digest or concept.brief_id != selection.brief_id
                or concept.source_brief_digest != selection.source_brief_digest):
            raise CreativeValidationError("selection does not match saved concept")
        return selection

    def load_selected(self, selection_id: str) -> dict[str, Any]:
        """Return a fresh JSON-ready A05 input, reconstructible without chat.

        Mutating this detached consumer payload cannot change stored records.
        No A05 rendering or publication is performed.
        """
        selection = self.load_selection(selection_id)
        concept = self.load_concept(selection.concept_id, selection.revision)
        return {
            "brief": self.load_brief(selection.source_brief_digest),
            "concept": concept.to_dict(),
            "selection": {**selection.to_dict(), "selection_id": selection.selection_id},
        }
