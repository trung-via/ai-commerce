"""Detached A03/A04 snapshots; associations never imply approval or generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import ConceptRecord, ConceptSelection, intake_brief
from ai_commerce.operations.manifest import AssetJobManifest

from ._validation import CompositionValidationError, digest, identity, integer, read_json, record, text


@dataclass(frozen=True, slots=True)
class BriefSnapshot:
    """Entire validated source brief, without mutable nested mappings."""

    _json: bytes

    def __post_init__(self) -> None:
        if not isinstance(self._json, bytes):
            raise CompositionValidationError("expected brief JSON bytes")
        object.__setattr__(self, "_json", canonical_bytes(intake_brief(read_json(self._json))))

    def to_dict(self) -> dict[str, Any]:
        return read_json(self._json)

    @property
    def digest(self) -> str:
        return sha256_bytes(self._json)

    @classmethod
    def from_dict(cls, value: object) -> BriefSnapshot:
        return cls(canonical_bytes(intake_brief(value)))


def pinned_selection(value: object) -> tuple[ConceptSelection, BriefSnapshot | None]:
    """Read V1 selection JSON, load_selected payload, or a typed pin.

    A pin is not HUMAN_SELECTED. Complete payloads are checked against exact
    concept/brief snapshots; a bare pin is only a structural reference.
    """
    if isinstance(value, ConceptSelection):
        return ConceptSelection.from_dict(value.to_dict()), None
    if isinstance(value, dict) and "selection" in value:
        payload = record(value, {"selection", "concept", "brief"})
        selection, _ = pinned_selection(payload["selection"])
        concept = ConceptRecord.from_dict(payload["concept"])
        brief = BriefSnapshot.from_dict(payload["brief"])
        if (selection.concept_id != concept.concept_id or selection.revision != concept.revision
                or selection.concept_digest != concept.digest
                or selection.brief_id != concept.brief_id
                or selection.source_brief_digest != concept.source_brief_digest
                or selection.brief_id != brief.to_dict()["brief_id"]
                or selection.source_brief_digest != brief.digest
                or not set(concept.source_refs) <= set(brief.to_dict().get("source_refs", []))):
            raise CompositionValidationError("pinned selection does not match V1 source snapshots")
        return selection, brief
    data = dict(value) if isinstance(value, dict) else value
    has_id = isinstance(data, dict) and "selection_id" in data
    supplied_id = data.pop("selection_id") if has_id else None
    selection = ConceptSelection.from_dict(data)
    if has_id and supplied_id != selection.selection_id:
        raise CompositionValidationError("selection_id does not match pinned selection")
    return selection, None


@dataclass(frozen=True, slots=True)
class MediaReference:
    """Exact artifact metadata and optional immutable A04 association.

    This is a declaration until a resolver verifies bytes at compile. Original
    UNKNOWN rights and mock/import labels survive without reinterpretation.
    """

    _artifact_json: bytes
    manifest_ref: str | None = None
    manifest_record_sha256: str | None = None
    handoff_ref: str | None = None
    selection_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self._artifact_json, bytes):
            raise CompositionValidationError("expected artifact JSON bytes")
        artifact = read_json(self._artifact_json)
        required = {"artifact_id", "kind", "mime_type", "sha256", "size_bytes", "path",
                    "source_snapshot_ids", "rights_status", "provider"}
        if not isinstance(artifact, dict):
            raise CompositionValidationError("expected artifact object")
        record(artifact, required, set(artifact) - required)
        sha = digest(artifact["sha256"])
        if artifact["artifact_id"] != "artifact:" + sha or artifact["path"] != "objects/" + sha:
            raise CompositionValidationError("artifact identity/path does not match SHA-256")
        kinds = {"image/png": "image", "image/jpeg": "image", "image/webp": "image",
                 "video/mp4": "video", "video/x-msvideo": "video"}
        if not isinstance(artifact["mime_type"], str) or kinds.get(artifact["mime_type"]) != artifact["kind"]:
            raise CompositionValidationError("unsupported or inconsistent media kind/MIME")
        integer(artifact["size_bytes"], "size_bytes", 1)
        refs = artifact["source_snapshot_ids"]
        if not isinstance(refs, list) or any(not isinstance(v, str) or not v.strip() for v in refs):
            raise CompositionValidationError("invalid source_snapshot_ids")
        if artifact["rights_status"] not in ("UNKNOWN", "GRANTED", "RESTRICTED", "NOT_APPLICABLE"):
            raise CompositionValidationError("invalid rights status")
        text(artifact["provider"], "provider")
        if (self.manifest_ref is None) != (self.manifest_record_sha256 is None):
            raise CompositionValidationError("manifest reference and checksum must be paired")
        if self.manifest_ref is not None:
            if not isinstance(self.manifest_ref, str) or not re.fullmatch(
                r"jobs/[A-Za-z0-9][A-Za-z0-9_-]{0,95}/attempt-[1-9][0-9]*\.manifest\.json", self.manifest_ref
            ):
                raise CompositionValidationError("invalid A04 manifest reference")
            digest(self.manifest_record_sha256)
        if self.handoff_ref is not None:
            if self.manifest_ref is None or self.selection_id is None:
                raise CompositionValidationError("handoff requires manifest and selection references")
            identity(self.selection_id, "selection")
            expected = "handoffs/" + sha256_bytes(canonical_bytes(self._handoff())) + ".json"
            if self.handoff_ref != expected:
                raise CompositionValidationError("A04 handoff identity mismatch")
        elif self.selection_id is not None:
            raise CompositionValidationError("handoff selection requires its handoff reference")
        object.__setattr__(self, "_artifact_json", canonical_bytes(artifact))

    def _handoff(self) -> dict[str, Any]:
        parts = self.manifest_ref.split("/")
        return {"handoff_version": 1, "selection_id": self.selection_id, "job_id": parts[1],
                "attempt": int(parts[2][8:-14]), "manifest_record_sha256": self.manifest_record_sha256}

    @property
    def artifact(self) -> dict[str, Any]:
        return read_json(self._artifact_json)

    @property
    def artifact_id(self) -> str:
        return self.artifact["artifact_id"]

    def to_dict(self) -> dict[str, Any]:
        return {"artifact": self.artifact, "manifest_ref": self.manifest_ref,
                "manifest_record_sha256": self.manifest_record_sha256,
                "handoff_ref": self.handoff_ref, "selection_id": self.selection_id}

    @classmethod
    def from_dict(cls, value: object) -> MediaReference:
        data = record(value, {"artifact", "manifest_ref", "manifest_record_sha256", "handoff_ref", "selection_id"})
        artifact = data.pop("artifact")
        return cls(canonical_bytes(artifact), **data)

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> MediaReference:
        return cls(canonical_bytes(artifact))

    @classmethod
    def from_manifest(cls, manifest: AssetJobManifest | dict[str, Any], *,
                      handoff: dict[str, Any] | None = None, handoff_ref: str | None = None) -> MediaReference:
        valid = AssetJobManifest.from_dict(manifest.to_dict() if isinstance(manifest, AssetJobManifest) else manifest)
        if valid.state != "SUCCEEDED":
            raise CompositionValidationError("ASSET_REQUIRED: A04 manifest has no successful artifact")
        ref = f"jobs/{valid.job_id}/attempt-{valid.attempt}.manifest.json"
        checksum = valid.to_dict()["record_sha256"]
        selection_id = None
        if handoff is not None:
            record(handoff, {"handoff_version", "selection_id", "job_id", "attempt", "manifest_record_sha256"})
            if (type(handoff["handoff_version"]) is not int or handoff["handoff_version"] != 1
                    or type(handoff["attempt"]) is not int or handoff["attempt"] != valid.attempt
                    or handoff["job_id"] != valid.job_id or handoff["manifest_record_sha256"] != checksum):
                raise CompositionValidationError("A04 handoff does not match manifest")
            selection_id = identity(handoff["selection_id"], "selection")
            expected = "handoffs/" + sha256_bytes(canonical_bytes(handoff)) + ".json"
            if handoff_ref != expected:
                raise CompositionValidationError("A04 handoff identity mismatch")
        elif handoff_ref is not None:
            raise CompositionValidationError("handoff_ref requires the actual handoff")
        return cls(canonical_bytes(valid.result["artifact"]), ref, checksum, handoff_ref, selection_id)
