"""Tests for CreativeAssetLink.v1 provenance sidecar and legacy upgrade bridge."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest

from ai_commerce.bridge import (
    BridgeStore, BridgeValidationError, CreativeAssetLink,
)
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import (
    CreativeStore, MockCreativeReasoner, generate_concepts, intake_brief,
)
from ai_commerce.media import MediaRequest
from ai_commerce.operations import ArtifactRegistry, LocalMediaRunner


def _make_png() -> bytes:
    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x30\x60\x90"))
            + chunk(b"IEND", b""))


def _sample_artifact(sha: str = "a" * 64) -> dict:
    return {
        "artifact_id": f"artifact:{sha}",
        "kind": "image",
        "mime_type": "image/png",
        "sha256": sha,
        "size_bytes": 1024,
        "path": f"objects/{sha}",
        "source_snapshot_ids": [f"snapshot:{sha}"],
        "rights_status": "UNKNOWN",
        "provider": "caller-declared",
        "generation_performed": False,
    }


def test_direct_prompt_link_without_selection(tmp_path: Path):
    art = _sample_artifact()
    link = CreativeAssetLink.create(
        prompt="A vibrant autumn morning in Hanoi",
        asset_role="hero_image",
        artifact=art,
        job_id="job-001",
        attempt=1,
        manifest_record_sha256="b" * 64,
        manifest_ref="jobs/job-001/attempt-1.manifest.json",
        provenance_class="LOCAL_IMPORT",
    )
    assert link.schema_version == "CreativeAssetLink.v1"
    assert link.selection_id is None
    assert link.concept_id is None
    assert link.concept_revision is None
    assert link.realization_claim is False
    assert link.link_id == f"link:{link.digest}"
    assert link.prompt_digest == sha256_bytes(canonical_bytes("A vibrant autumn morning in Hanoi"))

    store = BridgeStore(tmp_path)
    ref = store.save(link)
    assert ref == f"links/{link.digest}.json"

    loaded = store.load(ref)
    assert loaded == link
    assert loaded.to_dict() == link.to_dict()


def test_selection_with_concept_revision_and_provenance(tmp_path: Path):
    art = _sample_artifact()
    selection_sha = "c" * 64
    intent_sha = "d" * 64
    decision_sha = "e" * 64
    link = CreativeAssetLink.create(
        prompt="Chợ Bến Thành rực rỡ nắng sớm",
        asset_role="scene_still",
        artifact=art,
        job_id="job-002",
        attempt=1,
        manifest_record_sha256="f" * 64,
        manifest_ref="jobs/job-002/attempt-1.manifest.json",
        selection_id=f"selection:{selection_sha}",
        concept_id="concept-benthanh",
        concept_revision=3,
        intent_digest=intent_sha,
        decision_ref=f"decision:{decision_sha}",
        provider_request_ref="req-ext-12345",
        provenance_class="SYNTHETIC_FIXTURE",
        realization_claim=False,
    )
    assert link.selection_id == f"selection:{selection_sha}"
    assert link.concept_id == "concept-benthanh"
    assert link.concept_revision == 3
    assert link.intent_digest == intent_sha
    assert link.decision_ref == f"decision:{decision_sha}"
    assert link.provider_request_ref == "req-ext-12345"
    assert link.realization_claim is False

    store = BridgeStore(tmp_path)
    ref = store.save(link)
    loaded = store.load(link.link_id)
    assert loaded == link


def test_truthful_realization_claim_invariants():
    art = _sample_artifact()
    # Default realization_claim must be False
    link = CreativeAssetLink.create(
        prompt="test prompt",
        artifact=art,
        job_id="job-003",
        attempt=1,
        manifest_record_sha256="1" * 64,
        manifest_ref="jobs/job-003/attempt-1.manifest.json",
    )
    assert link.realization_claim is False

    # Invalid boolean type rejected
    with pytest.raises(BridgeValidationError, match="realization_claim must be a boolean"):
        CreativeAssetLink.create(
            prompt="test prompt",
            artifact=art,
            job_id="job-003",
            attempt=1,
            manifest_record_sha256="1" * 64,
            manifest_ref="jobs/job-003/attempt-1.manifest.json",
            realization_claim="not-a-bool",  # type: ignore
        )


def test_tamper_and_hash_corruption_rejected(tmp_path: Path):
    art = _sample_artifact()
    link = CreativeAssetLink.create(
        prompt="authentic prompt",
        artifact=art,
        job_id="job-004",
        attempt=1,
        manifest_record_sha256="2" * 64,
        manifest_ref="jobs/job-004/attempt-1.manifest.json",
    )
    store = BridgeStore(tmp_path)
    ref = store.save(link)

    # Reopening normally succeeds
    assert store.load(ref) == link

    # Tamper with file on disk: change prompt without updating prompt_digest or digest
    file_path = store.registry.safe_path(ref)
    data = json.loads(file_path.read_text(encoding="utf-8"))
    data["prompt"] = "tampered prompt"
    file_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(BridgeValidationError):
        store.load(ref)


def test_upgrade_legacy_handoff_to_creative_asset_link(tmp_path: Path):
    # Set up CreativeStore with a brief and concept
    cstore_dir = tmp_path / "creative"
    cstore = CreativeStore(cstore_dir)
    brief_dict = intake_brief("Tra sua pho di bo")
    cstore.save_brief(brief_dict)
    concept = generate_concepts(brief_dict, MockCreativeReasoner())[0]
    cstore.save_concept(concept)
    selection = cstore.select(concept.concept_id, concept.revision)

    # Set up A04 media runner with an imported PNG
    out_dir = tmp_path / "assets"
    registry = ArtifactRegistry(out_dir)
    runner = LocalMediaRunner(registry)
    source_png = tmp_path / "ts.png"
    source_png.write_bytes(_make_png())
    manifest = runner.run(MediaRequest(mode="import", source_path=source_png, mime_type="image/png"))

    # Create legacy handoff
    handoff_record = {
        "handoff_version": 1,
        "selection_id": selection.selection_id,
        "job_id": manifest.job_id,
        "attempt": manifest.attempt,
        "manifest_record_sha256": manifest.to_dict()["record_sha256"],
    }
    content = canonical_bytes(handoff_record)
    handoff_ref = "handoffs/" + sha256_bytes(content) + ".json"
    registry.put_immutable(handoff_ref, content)

    # Now upgrade using BridgeStore
    bridge_store = BridgeStore(registry)
    link_ref, link = bridge_store.upgrade_legacy_handoff(handoff_ref, creative_store=cstore)

    assert link_ref.startswith("links/")
    assert link.legacy_handoff_ref == handoff_ref
    assert link.selection_id == selection.selection_id
    assert link.concept_id == concept.concept_id
    assert link.concept_revision == concept.revision
    assert link.prompt == "Tra sua pho di bo"
    assert link.job_id == manifest.job_id
    assert link.attempt == manifest.attempt
    assert link.provenance_class == "LOCAL_IMPORT"
    assert link.realization_claim is False

    # Original legacy handoff is still readable and unchanged
    reopened_legacy = json.loads(registry.read(handoff_ref))
    assert reopened_legacy == handoff_record

    # Upgraded link loads cleanly
    loaded_link = bridge_store.load(link_ref)
    assert loaded_link == link
