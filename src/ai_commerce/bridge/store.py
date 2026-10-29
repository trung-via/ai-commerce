"""Storage and legacy upgrade bridge for CreativeAssetLink sidecars."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import CreativeStore
from ai_commerce.operations import ArtifactRegistry, AssetJobManifest, LocalMediaRunner
from ai_commerce.operations.manifest import strict_json

from .link import BridgeValidationError, CreativeAssetLink


class BridgeStore:
    """Immutable sidecar storage for A03<->A04 CreativeAssetLinks."""

    def __init__(self, root: str | Path | ArtifactRegistry) -> None:
        if isinstance(root, ArtifactRegistry):
            self.registry = root
        else:
            self.registry = ArtifactRegistry(Path(root))
        self.root = self.registry.root

    @staticmethod
    def link_ref(digest: str) -> str:
        return f"links/{digest}.json"

    def save(self, link: CreativeAssetLink) -> str:
        if not isinstance(link, CreativeAssetLink):
            raise BridgeValidationError("Expected CreativeAssetLink")
        ref = self.link_ref(link.digest)
        content = canonical_bytes(link.to_dict())
        self.registry.put_immutable(ref, content)
        return ref

    def load(self, ref_or_digest: str) -> CreativeAssetLink:
        if ref_or_digest.startswith("link:"):
            digest = ref_or_digest[5:]
            ref = self.link_ref(digest)
        elif "/" in ref_or_digest:
            ref = ref_or_digest
        else:
            ref = self.link_ref(ref_or_digest)

        raw = self.registry.read(ref)
        data = strict_json(raw)
        link = CreativeAssetLink.from_dict(data)
        expected_ref = self.link_ref(link.digest)
        if ref != expected_ref:
            raise BridgeValidationError(f"Link reference {ref} does not match computed digest {link.digest}")
        return link

    def upgrade_legacy_handoff(
        self,
        handoff_ref: str,
        creative_store: CreativeStore | str | Path | None = None,
        *,
        asset_role: str = "primary",
    ) -> tuple[str, CreativeAssetLink]:
        """Read legacy V1 handoff without mutation; emit new CreativeAssetLink sidecar."""
        raw = self.registry.read(handoff_ref)
        handoff = strict_json(raw)
        if handoff_ref != "handoffs/" + sha256_bytes(canonical_bytes(handoff)) + ".json":
            raise BridgeValidationError("Legacy handoff ref mismatch")
        fields = {"handoff_version", "selection_id", "job_id", "attempt", "manifest_record_sha256"}
        if set(handoff) != fields or handoff["handoff_version"] != 1:
            raise BridgeValidationError("Invalid legacy handoff structure or version")

        runner = LocalMediaRunner(self.registry)
        manifest = runner.load(handoff["job_id"], handoff["attempt"])
        if manifest.to_dict()["record_sha256"] != handoff["manifest_record_sha256"]:
            raise BridgeValidationError("Manifest checksum mismatch against legacy handoff")

        selection_id = handoff["selection_id"]
        concept_id = None
        concept_revision = None
        prompt = f"Legacy selection: {selection_id}"
        intent_digest = None

        if creative_store is not None:
            cstore = creative_store if isinstance(creative_store, CreativeStore) else CreativeStore(creative_store)
            creative = cstore.load_selected(selection_id)
            concept = creative.get("concept", {})
            brief = creative.get("brief", {})
            concept_id = concept.get("concept_id")
            concept_revision = concept.get("revision")
            prompt = brief.get("prompt") or brief.get("raw_text") or brief.get("text") or prompt

        artifact = manifest.result["artifact"]
        mode = manifest.request.get("mode", "")
        if mode == "import":
            provenance_class = "LOCAL_IMPORT"
        elif mode == "mock":
            provenance_class = "SYNTHETIC_FIXTURE"
        else:
            provenance_class = "UNKNOWN"

        link = CreativeAssetLink.create(
            prompt=prompt,
            asset_role=asset_role,
            artifact=artifact,
            job_id=manifest.job_id,
            attempt=manifest.attempt,
            manifest_record_sha256=handoff["manifest_record_sha256"],
            manifest_ref=runner.manifest_ref(manifest.job_id, manifest.attempt),
            intent_digest=intent_digest,
            selection_id=selection_id,
            concept_id=concept_id,
            concept_revision=concept_revision,
            provenance_class=provenance_class,
            realization_claim=False,
            legacy_handoff_ref=handoff_ref,
        )
        saved_ref = self.save(link)
        return saved_ref, link
