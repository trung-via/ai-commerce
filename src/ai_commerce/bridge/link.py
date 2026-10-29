"""Immutable CreativeAssetLink.v1 provenance sidecar.

Bridges A03 creative understanding and A04 media operations without false
claims of human approval or model realization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes, timestamp_utc

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SELECTION_RE = re.compile(r"^selection:[0-9a-f]{64}$")
ARTIFACT_RE = re.compile(r"^artifact:[0-9a-f]{64}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")

PROVENANCE_CLASSES = frozenset({
    "LOCAL_IMPORT", "SYNTHETIC_FIXTURE", "MODEL_GENERATED", "DERIVED_RENDER", "UNKNOWN",
})


class BridgeValidationError(ValueError):
    """Validation failure for bridge contracts or links."""


def _check_digest(val: str | None, name: str) -> None:
    if val is not None and (not isinstance(val, str) or not DIGEST_RE.fullmatch(val)):
        raise BridgeValidationError(f"Invalid {name}: must be 64-char lowercase hex")


@dataclass(frozen=True, slots=True)
class CreativeAssetLink:
    """Immutable provenance record connecting creative intent/concept to media asset.

    A link records that an asset was associated with a prompt or pinned concept;
    it does NOT claim that the asset was generated to realize the concept unless
    explicitly evidenced by a verified generator receipt.
    """

    prompt: str
    prompt_digest: str
    asset_role: str
    artifact_id: str
    artifact_sha256: str
    manifest_record_sha256: str
    job_id: str
    attempt: int
    manifest_ref: str
    schema_version: str = "CreativeAssetLink.v1"
    link_id: str | None = None
    intent_digest: str | None = None
    selection_id: str | None = None
    concept_id: str | None = None
    concept_revision: int | None = None
    decision_ref: str | None = None
    provider_request_ref: str | None = None
    provenance_class: str = "UNKNOWN"
    realization_claim: bool = False
    legacy_handoff_ref: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != "CreativeAssetLink.v1":
            raise BridgeValidationError(f"Unsupported schema_version: {self.schema_version}")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise BridgeValidationError("prompt must be a non-empty string")
        if not isinstance(self.asset_role, str) or not self.asset_role.strip():
            raise BridgeValidationError("asset_role must be a non-empty string")
        if not isinstance(self.job_id, str) or not JOB_ID_RE.fullmatch(self.job_id):
            raise BridgeValidationError(f"Invalid job_id: {self.job_id}")
        if type(self.attempt) is not int or self.attempt < 1:
            raise BridgeValidationError(f"Invalid attempt: {self.attempt}; must be positive int")
        if not isinstance(self.manifest_ref, str) or not self.manifest_ref.strip():
            raise BridgeValidationError("manifest_ref must be a non-empty string")

        _check_digest(self.prompt_digest, "prompt_digest")
        _check_digest(self.artifact_sha256, "artifact_sha256")
        _check_digest(self.manifest_record_sha256, "manifest_record_sha256")
        _check_digest(self.intent_digest, "intent_digest")

        expected_art_id = f"artifact:{self.artifact_sha256}"
        if self.artifact_id != expected_art_id:
            raise BridgeValidationError(f"artifact_id {self.artifact_id} mismatch for sha256 {self.artifact_sha256}")

        if self.selection_id is not None:
            if not isinstance(self.selection_id, str) or not SELECTION_RE.fullmatch(self.selection_id):
                raise BridgeValidationError(f"Invalid selection_id: {self.selection_id}")
            if self.concept_revision is not None and (type(self.concept_revision) is not int or self.concept_revision < 1):
                raise BridgeValidationError(f"Invalid concept_revision: {self.concept_revision}")

        if self.provenance_class not in PROVENANCE_CLASSES:
            raise BridgeValidationError(f"Unknown provenance_class: {self.provenance_class}")

        if type(self.realization_claim) is not bool:
            raise BridgeValidationError("realization_claim must be a boolean")

        computed_prompt_digest = sha256_bytes(canonical_bytes(self.prompt))
        if self.prompt_digest != computed_prompt_digest:
            raise BridgeValidationError("prompt_digest does not match canonical bytes of prompt")

        ts = self.created_at or timestamp_utc()
        object.__setattr__(self, "created_at", ts)

        computed_digest = self._compute_digest()
        computed_id = f"link:{computed_digest}"
        if self.link_id is not None and self.link_id != computed_id:
            raise BridgeValidationError(f"link_id mismatch: expected {computed_id}, got {self.link_id}")
        object.__setattr__(self, "link_id", computed_id)

    def _payload_for_digest(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "prompt": self.prompt,
            "prompt_digest": self.prompt_digest,
            "asset_role": self.asset_role,
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "manifest_record_sha256": self.manifest_record_sha256,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "manifest_ref": self.manifest_ref,
            "intent_digest": self.intent_digest,
            "selection_id": self.selection_id,
            "concept_id": self.concept_id,
            "concept_revision": self.concept_revision,
            "decision_ref": self.decision_ref,
            "provider_request_ref": self.provider_request_ref,
            "provenance_class": self.provenance_class,
            "realization_claim": self.realization_claim,
            "legacy_handoff_ref": self.legacy_handoff_ref,
            "created_at": self.created_at,
        }

    def _compute_digest(self) -> str:
        return sha256_bytes(canonical_bytes(self._payload_for_digest()))

    @property
    def digest(self) -> str:
        return self._compute_digest()

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload_for_digest()
        payload["link_id"] = self.link_id
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreativeAssetLink:
        if not isinstance(data, dict):
            raise BridgeValidationError("Link data must be a dict")
        fields = {
            "schema_version", "link_id", "prompt", "prompt_digest", "asset_role",
            "artifact_id", "artifact_sha256", "manifest_record_sha256", "job_id",
            "attempt", "manifest_ref", "intent_digest", "selection_id",
            "concept_id", "concept_revision", "decision_ref", "provider_request_ref",
            "provenance_class", "realization_claim", "legacy_handoff_ref", "created_at",
        }
        unknown = set(data) - fields
        if unknown:
            raise BridgeValidationError(f"Unknown fields in CreativeAssetLink: {unknown}")
        return cls(
            prompt=data["prompt"],
            prompt_digest=data["prompt_digest"],
            asset_role=data["asset_role"],
            artifact_id=data["artifact_id"],
            artifact_sha256=data["artifact_sha256"],
            manifest_record_sha256=data["manifest_record_sha256"],
            job_id=data["job_id"],
            attempt=data["attempt"],
            manifest_ref=data["manifest_ref"],
            schema_version=data.get("schema_version", "CreativeAssetLink.v1"),
            link_id=data.get("link_id"),
            intent_digest=data.get("intent_digest"),
            selection_id=data.get("selection_id"),
            concept_id=data.get("concept_id"),
            concept_revision=data.get("concept_revision"),
            decision_ref=data.get("decision_ref"),
            provider_request_ref=data.get("provider_request_ref"),
            provenance_class=data.get("provenance_class", "UNKNOWN"),
            realization_claim=data.get("realization_claim", False),
            legacy_handoff_ref=data.get("legacy_handoff_ref"),
            created_at=data.get("created_at"),
        )

    @classmethod
    def create(
        cls,
        *,
        prompt: str,
        asset_role: str = "primary",
        artifact: dict[str, Any],
        job_id: str,
        attempt: int,
        manifest_record_sha256: str,
        manifest_ref: str,
        intent_digest: str | None = None,
        selection_id: str | None = None,
        concept_id: str | None = None,
        concept_revision: int | None = None,
        decision_ref: str | None = None,
        provider_request_ref: str | None = None,
        provenance_class: str = "UNKNOWN",
        realization_claim: bool = False,
        legacy_handoff_ref: str | None = None,
    ) -> CreativeAssetLink:
        prompt_digest = sha256_bytes(canonical_bytes(prompt))
        artifact_sha256 = artifact["sha256"]
        artifact_id = artifact["artifact_id"]
        return cls(
            prompt=prompt,
            prompt_digest=prompt_digest,
            asset_role=asset_role,
            artifact_id=artifact_id,
            artifact_sha256=artifact_sha256,
            manifest_record_sha256=manifest_record_sha256,
            job_id=job_id,
            attempt=attempt,
            manifest_ref=manifest_ref,
            intent_digest=intent_digest,
            selection_id=selection_id,
            concept_id=concept_id,
            concept_revision=concept_revision,
            decision_ref=decision_ref,
            provider_request_ref=provider_request_ref,
            provenance_class=provenance_class,
            realization_claim=realization_claim,
            legacy_handoff_ref=legacy_handoff_ref,
        )
