"""C07 deterministic proposals, backed by A04 custody and real A05 consumers.

No provider is accepted by this API. Creating a batch never renders. An explicit
Human-authorized local render produces a separate immutable execution receipt;
the original proposal and master remain byte-for-byte intact.
"""
from __future__ import annotations

from decimal import Decimal
import re

from ai_commerce.bridge import composition_to_render_plan
from ai_commerce.composition import (
    CompositionStore, CreativeComposition, RegistryMediaResolver, compose_images,
    compile_composition, create_composition,
)
from ai_commerce.creative import brief_digest, intake_brief
from ai_commerce.operations import ArtifactRegistry, LocalMediaRunner
from ai_commerce.operations.cost_ledger import CostLedger
from ai_commerce.operations.manifest import strict_json
from ai_commerce.render import FFmpegRenderer

from .schemas import (
    MAX_ASSETS, MAX_VARIANTS, VARIANT_BATCH_SCHEMA, bounded, detached, digest,
    private_text, put_record, read_record, require, scope_value, seal, validate,
)


def batch_ref(batch_id: str) -> str:
    require(type(batch_id) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", batch_id) is not None,
            "BATCH_ID_INVALID")
    return f"creative-scaling/batches/{batch_id}.json"


def brief_snapshot(brief: dict) -> tuple[dict, dict]:
    valid = intake_brief(detached(brief))
    private_text(valid)
    sha = brief_digest(valid)
    return {"id": valid["brief_id"], "revision": 1, "sha256": sha,
            "ref": f"creative-scaling/briefs/{sha}.json"}, valid


def _composition_pin(comp: CreativeComposition) -> dict:
    return {"id": comp.composition_id, "revision": comp.revision, "sha256": comp.digest,
            "ref": CompositionStore.revision_ref(comp.composition_id, comp.revision)}


def _assets(comp: CreativeComposition, registry: ArtifactRegistry) -> list[dict]:
    library = {m.artifact_id: m for m in comp.media}
    result, resolver = [], RegistryMediaResolver(registry)
    for track in comp.tracks:
        for item in track.items:
            if item.asset_id is None:
                continue
            ref = library[item.asset_id]
            resolver.resolve(ref)  # A04 manifest/bytes/rights checks, never generation.
            artifact = ref.artifact
            origin = artifact.get("origin")
            require(origin in {"LOCAL_IMPORT", "MOCK"} and artifact.get("generation_performed") is False,
                    "UNVERIFIED_ASSET_PROVIDER_CLAIM")
            result.append({"role": track.track_id + "/" + item.item_id,
                           "artifact_id": ref.artifact_id, "sha256": artifact["sha256"],
                           "manifest_ref": ref.manifest_ref, "manifest_sha256": ref.manifest_record_sha256,
                           "provenance": "SYNTHETIC_FIXTURE" if origin == "MOCK" else "USER_SUPPLIED",
                           "rights_status": artifact["rights_status"]})
    require(len(result) <= MAX_ASSETS, "ASSET_LIMIT")
    return result


def _describe(comp, brief_pin, registry) -> tuple[dict, tuple[str, dict]]:
    private_text(comp.intent)
    script = {"schema_version": "CreativeScript.v1", "intent": comp.intent}
    sha = digest(script)
    script_pin = {"id": "script:" + sha, "revision": 1, "sha256": sha,
                  "ref": f"creative-scaling/scripts/{sha}.json"}
    return {"brief": brief_pin, "script": script_pin, "composition": _composition_pin(comp),
            "assets": _assets(comp, registry)}, (script_pin["ref"], script)


def _costs(edit: dict, master: CreativeComposition, registry: ArtifactRegistry) -> list[dict]:
    """Consume A15 entries only for jobs actually used by this variant.

    C07 has not incurred a render charge. Its cost attachments are the existing
    input-asset jobs, with estimated and supplied charged evidence kept distinct.
    """
    ids = edit["cost_entry_ids"]
    require(len(ids) == len(set(ids)), "DUPLICATE_COST_ENTRY")
    refs = {m.artifact_id: m for m in master.media}
    used = set(edit["asset_order"] or [])
    jobs = {}
    for asset_id in used:
        ref = refs[asset_id]
        if ref.manifest_ref is not None:
            parts = ref.manifest_ref.split("/")
            manifest = LocalMediaRunner(registry).load(parts[1], int(parts[2][8:-14]))
            jobs[(manifest.job_id, manifest.attempt)] = manifest
    ledger, records = CostLedger(registry), []
    for entry_id in ids:
        cost_ref = ledger.ref(entry_id)
        require(registry.safe_path(cost_ref).stat().st_size <= 65536, "COST_RECORD_SIZE_LIMIT")
        raw_cost = registry.read(cost_ref)
        original = validate("CostRecord", strict_json(raw_cost))
        require(raw_cost == bounded(original) + b"\n", "COST_RECORD_NONCANONICAL")
        entry = ledger.get(entry_id)
        manifest = jobs.get((entry.job_id, entry.attempt))
        require(manifest is not None and entry.intent_digest == manifest.request_digest
                and entry.provider == manifest.request["provider"]["name"], "COST_SOURCE_MISMATCH")
        record = entry.to_record()
        require(record == original, "COST_RECORD_NORMALIZATION_FORBIDDEN")
        private_text({"provider": record["provider"], "model": record["model"],
                      "evidence_ref": record["evidence_ref"]})
        if entry.evidence_ref is not None:
            require(registry.safe_path(entry.evidence_ref).stat().st_size <= 1024 * 1024,
                    "COST_EVIDENCE_SIZE_LIMIT")
            raw = registry.read(entry.evidence_ref)
            from ai_commerce.core.provenance import sha256_bytes
            require(len(raw) <= 1024 * 1024 and sha256_bytes(raw) == entry.evidence_sha256,
                    "COST_EVIDENCE_DIGEST_MISMATCH")
        records.append(record)
    ledger.summarize(ids)  # Existing duplicate-charge and decimal semantics.
    return records


def _variant_composition(master, edit):
    metadata = {"source_brief": master.source_brief, "selection": master.selection,
                "settings": master.settings, "canvas": master.canvas}
    if edit["mode"] == "FREEFORM":
        require(edit["intent"] != master.intent, "UNCHANGED_VARIANT")
        return create_composition(edit["intent"], **metadata)
    require(len(edit["asset_order"]) == len(edit["frames"])
            and sum(edit["frames"]) <= 3600, "VARIANT_TIMELINE_INVALID")
    library = {m.artifact_id: m for m in master.media}
    master_used = {i.asset_id for t in master.tracks for i in t.items if i.asset_id is not None}
    require(set(edit["asset_order"]) <= master_used, "CROSS_MASTER_ASSET")
    require(all(library[a].artifact["kind"] == "image" for a in edit["asset_order"]),
            "CONTROLLED_STILL_REQUIRED")
    comp = compose_images(edit["intent"], [(library[a], n) for a, n in zip(edit["asset_order"], edit["frames"])],
                          **metadata)
    # A new ID alone is not a creative edit: intent, order or duration must differ.
    old_items = [(i.asset_id, i.duration_frames) for t in master.tracks for i in t.items]
    require(comp.intent != master.intent or old_items != list(zip(edit["asset_order"], edit["frames"])),
            "UNCHANGED_VARIANT")
    return comp


def _assemble(master, edits, *, registry, batch_id, scope, provenance):
    require(type(registry) is ArtifactRegistry and type(master) is CreativeComposition,
            "TYPED_MASTER_AND_REGISTRY_REQUIRED")
    batch_ref(batch_id)
    require(provenance in {"USER_SUPPLIED", "SYNTHETIC_FIXTURE"}, "PROVIDER_ATTESTATION_UNAVAILABLE")
    scope = None if scope is None else scope_value(scope)
    require(type(edits) is list and 2 <= len(edits) <= MAX_VARIANTS, "VARIANT_COUNT_INVALID")
    edits = [validate("Edit", edit) for edit in edits]
    for edit in edits:
        private_text(edit["intent"])
    # Bound before the upstream store/model processes any caller payload.
    bounded(master.to_dict())
    loaded = CompositionStore(registry.root).load(master.composition_id, master.revision)
    require(loaded.to_dict() == master.to_dict(), "MASTER_NOT_PERSISTED_OR_STALE")
    brief = master.source_brief.to_dict() if master.source_brief else intake_brief(master.original_intent)
    brief_pin, brief = brief_snapshot(brief)
    master_data, script = _describe(master, brief_pin, registry)
    master_data = {"master_id": "a17-master-" + digest(master_data), "revision": 1, **master_data}
    blobs, compositions, variants, contents = [(brief_pin["ref"], brief), script], [], [], set()
    for edit in edits:
        comp = _variant_composition(master, edit)
        require(comp.digest not in contents, "DUPLICATE_VARIANT_CONTENT")
        contents.add(comp.digest)
        descriptor, script = _describe(comp, brief_pin, registry)
        variant_id = "a17-variant-" + digest({"master": master_data, "edit": edit,
                                             "scope": scope, "provenance": provenance})
        job_id = "a17-" + variant_id.removeprefix("a17-variant-")
        renderable = not comp.unresolved_intent
        variant = {"variant_id": variant_id, "revision": 1, "master_id": master_data["master_id"],
                   "parent": {"kind": "MASTER", "id": master_data["master_id"], "revision": 1,
                              "sha256": digest(master_data)}, "edit": edit, **descriptor,
                   "manifest_ref": f"creative-scaling/variants/{variant_id[12:]}.json",
                   "render": {"state": "NOT_ATTEMPTED" if renderable else "ASSET_REQUIRED",
                              "job_id": job_id if renderable else None,
                              "manifest_ref": FFmpegRenderer.manifest_ref(job_id) if renderable else None},
                   "costs": _costs(edit, master, registry),
                   "cost_basis": "INPUT_ASSET_JOBS_ONLY_RENDER_COST_UNKNOWN"}
        variants.append(variant)
        compositions.append(comp)
        blobs.extend([script, (variant["manifest_ref"], variant)])
    request = {"master": master_data, "edits": edits, "scope": scope, "provenance": provenance}
    batch = seal({"schema_version": VARIANT_BATCH_SCHEMA, "batch_id": batch_id, "revision": 1,
                  "request_sha256": digest(request), "scope": scope, "provenance": provenance,
                  "product_ref": brief.get("product_ref"), "master": master_data, "variants": variants,
                  "generation_performed": False, "publication_status": "NOT_ATTEMPTED"})
    return validate(VARIANT_BATCH_SCHEMA, batch), blobs, compositions


def create_variant_batch(master: CreativeComposition, edits: list[dict], *, registry: ArtifactRegistry,
                         batch_id: str, scope: dict | None = None, provenance: str = "USER_SUPPLIED") -> dict:
    """Propose 2..16 ordered forks of an exact saved master; no render/generation.

    FREEFORM needs intent and null asset_order/frames. CONTROLLED needs explicit
    still IDs and frame lengths. Both require cost_entry_ids (possibly empty).
    A null scope permits content-only local work with no account or SKU.
    """
    batch, blobs, comps = _assemble(master, edits, registry=registry, batch_id=batch_id,
                                   scope=scope, provenance=provenance)
    ref = batch_ref(batch_id)
    if registry.exists(ref):
        original = load_variant_batch(registry, batch_id)
        require(original == batch, "BATCH_DIGEST_CONFLICT")
        return original
    # Exclusive registry writes are idempotent. Partial attempts never overwrite;
    # corrupted or conflicting existing dependencies stop publication of a batch.
    for relative, value in blobs:
        put_record(registry, relative, value)
    store = CompositionStore(registry.root)
    for comp in comps:
        store.save(comp)
    put_record(registry, ref, batch)
    return load_variant_batch(registry, batch_id)


def load_variant_batch(registry: ArtifactRegistry, batch_id: str) -> dict:
    batch = validate(VARIANT_BATCH_SCHEMA, read_record(registry, batch_ref(batch_id)))
    require(batch["batch_id"] == batch_id, "BATCH_IDENTITY_MISMATCH")
    pin = batch["master"]["composition"]
    master = CompositionStore(registry.root).load(pin["id"], pin["revision"])
    expected, blobs, comps = _assemble(master, [v["edit"] for v in batch["variants"]], registry=registry,
                                       batch_id=batch_id, scope=batch["scope"], provenance=batch["provenance"])
    require(batch == expected, "BATCH_SOURCE_BINDING_MISMATCH")
    for relative, value in blobs:
        require(read_record(registry, relative) == value, "VARIANT_SNAPSHOT_MISMATCH")
    for comp in comps:
        actual = CompositionStore(registry.root).load(comp.composition_id, comp.revision)
        require(actual.to_dict() == comp.to_dict(), "VARIANT_COMPOSITION_MISMATCH")
    return batch


def checked_batch(batch, registry) -> dict:
    value = validate(VARIANT_BATCH_SCHEMA, batch)
    actual = load_variant_batch(registry, value["batch_id"])
    require(actual == value, "BATCH_NOT_PERSISTED")
    return actual


def source_pin(batch: dict, source_id: str) -> tuple[dict, dict]:
    master = batch["master"]
    if source_id == master["master_id"]:
        source, kind = master, "MASTER"
    else:
        matches = [v for v in batch["variants"] if v["variant_id"] == source_id]
        require(len(matches) == 1, "SOURCE_NOT_IN_BATCH")
        source, kind = matches[0], "VARIANT"
    return {"kind": kind, "id": source_id, "revision": source["revision"],
            "sha256": source["composition"]["sha256"]}, source


def summarize_variant_costs(batch: dict, *, registry: ArtifactRegistry, currency: str) -> dict:
    """Explicit single-currency view; unknown/absent costs are never a zero bill."""
    batch = checked_batch(batch, registry)
    require(type(currency) is str and re.fullmatch(r"[A-Z]{3}", currency) is not None, "CURRENCY_INVALID")
    entries = {c["entry_id"]: c for v in batch["variants"] for c in v["costs"]}
    require(all(c["currency"] in (None, currency) for c in entries.values()), "CROSS_CURRENCY_SUM_FORBIDDEN")
    CostLedger(registry).summarize(tuple(entries))  # Detect repeated receipt lines across variants too.
    totals = {"estimated": None, "supplied_charged": None, "synthetic_charged": None}
    unknown = any(not v["costs"] for v in batch["variants"]) or any(
        c["status"] in {"UNKNOWN", "PENDING"} for c in entries.values())
    for c in entries.values():
        if c["status"] == "ESTIMATED":
            totals["estimated"] = (totals["estimated"] or Decimal(0)) + Decimal(c["amount"])
        elif c["status"] == "CHARGED":
            bucket = "synthetic_charged" if c["evidence_origin"] == "SYNTHETIC_FIXTURE" else "supplied_charged"
            totals[bucket] = (totals[bucket] or Decimal(0)) + Decimal(c["amount"])
    return {"currency": currency, "totals": {k: None if v is None else format(v, "f") for k, v in totals.items()},
            "cost_completeness": "UNKNOWN" if unknown else "SUPPLIED_ENTRIES_ONLY",
            "provider_charge_verified": False}


def render_variant(batch: dict, variant_id: str, *, registry: ArtifactRegistry,
                   human_authorized: bool = False) -> dict:
    """Explicit local A05 FFmpeg consumer. No provider APIs or automatic retry.

    Only a persisted/reopened successful receipt proves usable MP4 bytes. A05's
    technical still render cannot establish realization of freeform semantics.
    """
    require(human_authorized is True, "HUMAN_LOCAL_RENDER_AUTHORIZATION_REQUIRED")
    batch = checked_batch(batch, registry)
    pin, variant = source_pin(batch, variant_id)
    require(pin["kind"] == "VARIANT" and variant["render"]["state"] == "NOT_ATTEMPTED", "ASSET_REQUIRED")
    cp = variant["composition"]
    comp = CompositionStore(registry.root).load(cp["id"], cp["revision"])
    require(comp.canvas.fps.denominator == 1 and comp.settings.to_dict() == {"fit": "contain", "background": "#000000"},
            "LOCAL_RENDER_UNSUPPORTED_SETTINGS")
    result = compile_composition(comp, RegistryMediaResolver(registry))
    require(result.state == "READY", "LOCAL_COMPILE_NOT_READY")
    plan = composition_to_render_plan(comp, composition_ref=cp["ref"])
    renderer, job_id = FFmpegRenderer(registry), variant["render"]["job_id"]
    if registry.exists(f"render-jobs/{job_id}/request.json"):
        receipt = renderer.load(job_id)  # An interrupted/UNKNOWN job is never restarted.
    else:
        receipt = renderer.render(plan, job_id=job_id)
    require(receipt.plan.to_dict() == plan.to_dict(), "CROSS_VARIANT_RENDER")
    if receipt.state == "SUCCEEDED":
        receipt = renderer.load(job_id)  # ffprobe + full decode, not metadata alone.
    value = seal({"schema_version": "CreativeVariantRender.v1", "source": pin,
                  "master_id": batch["master"]["master_id"], "composition": cp,
                  "job_id": job_id, "state": receipt.state,
                  "manifest_ref": renderer.manifest_ref(job_id),
                  "manifest_sha256": receipt.to_dict()["record_sha256"]
                                     if registry.exists(renderer.manifest_ref(job_id)) else None,
                  "output": receipt.output, "full_decode_verified": receipt.state == "SUCCEEDED",
                  "semantic_realization": "UNKNOWN", "generation_performed": False,
                  "publication_status": "NOT_ATTEMPTED"})
    put_record(registry, f"creative-scaling/renders/{variant_id[12:]}.json", value)
    return value
